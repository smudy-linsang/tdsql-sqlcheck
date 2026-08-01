"""原始慢日志的 OpenSSH CLI 传输层。

不引入 paramiko 等 Python SSH 库，不执行远程 shell 命令。远端账户必须由
sshd ForceCommand 固定到 raw_slowlog_exporter，stdin/stdout 为一行 JSON 请求
与 NDJSON 响应协议。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SSH_ACCOUNT = "tdsql_log_reader"
_REF = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


class RawSlowLogSSHError(RuntimeError):
    """远端不可达、身份不匹配或 NDJSON 协议不合规。"""


@dataclass(frozen=True)
class SSHSecretPaths:
    private_key: Path
    known_hosts: Path


class SSHSecretResolver:
    """将数据库中的引用名映射为仅部署机本地可读的文件。

    数据库与 API 只保存 ref，路径不可由用户传入；因此不会将任意路径带入
    subprocess 或 SSH 选项。
    """

    def __init__(self, root: str | Path | None = None):
        configured = root or os.getenv("RAW_SLOWLOG_SECRET_DIR", "")
        self.root = Path(configured) if configured else Path("/run/secrets/tdsql-sqlcheck")

    @staticmethod
    def _validate_ref(value: str, label: str) -> str:
        if not isinstance(value, str) or not _REF.fullmatch(value):
            raise RawSlowLogSSHError(f"invalid {label} reference")
        return value

    def resolve(self, credential_ref: str, known_hosts_ref: str) -> SSHSecretPaths:
        credential_ref = self._validate_ref(credential_ref, "credential")
        known_hosts_ref = self._validate_ref(known_hosts_ref, "known_hosts")
        root = self.root.resolve()
        key = (root / f"{credential_ref}.key").resolve()
        hosts = (root / f"{known_hosts_ref}.known_hosts").resolve()
        if root not in key.parents or root not in hosts.parents:
            raise RawSlowLogSSHError("secret reference escapes configured secret root")
        for candidate, label in ((key, "private key"), (hosts, "known_hosts")):
            if not candidate.is_file():
                raise RawSlowLogSSHError(f"configured {label} is unavailable")
        return SSHSecretPaths(key, hosts)


class RawSlowLogSSHClient:
    """以固定 OpenSSH argv 调用受限远端导出器。"""

    def __init__(self, executable: str | None = None, secret_resolver: SSHSecretResolver | None = None):
        self.executable = executable or os.getenv("RAW_SLOWLOG_SSH_BIN", "ssh")
        self.secret_resolver = secret_resolver or SSHSecretResolver()

    def build_argv(self, node: dict[str, Any], source: dict[str, Any]) -> list[str]:
        try:
            port = int(node["ssh_port"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RawSlowLogSSHError("invalid SSH port") from exc
        if not 1 <= port <= 65535:
            raise RawSlowLogSSHError("invalid SSH port")
        host = str(node.get("ssh_host", "")).strip()
        alias = str(node.get("host_key_alias", "")).strip()
        if not host or not alias or any(c.isspace() for c in host + alias):
            raise RawSlowLogSSHError("invalid SSH host or host-key alias")
        secret = self.secret_resolver.resolve(source.get("credential_ref", ""), source.get("known_hosts_ref", ""))
        # Do not use ~/.ssh/config or global known_hosts: deployment config is
        # pinned and must be the only authority for this trust decision.
        return [
            self.executable,
            "-F", "none",
            "-T",
            "-p", str(port),
            "-i", str(secret.private_key),
            "-o", "BatchMode=yes",
            "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={secret.known_hosts}",
            "-o", f"GlobalKnownHostsFile={os.devnull}",
            "-o", f"HostKeyAlias={alias}",
            "-o", "RequestTTY=no",
            "-o", "ClearAllForwardings=yes",
            "-o", "PermitLocalCommand=no",
            "-o", "LogLevel=ERROR",
            f"{SSH_ACCOUNT}@{host}",
        ]

    def request(self, node: dict[str, Any], source: dict[str, Any], payload: dict[str, Any], timeout_seconds: int) -> list[dict[str, Any]]:
        operation = payload.get("op")
        if operation not in {"probe", "pull", "version"}:
            raise RawSlowLogSSHError("unsupported exporter operation")
        argv = self.build_argv(node, source)
        request_line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
                env={"PATH": os.environ.get("PATH", ""), "LC_ALL": "C", "LANG": "C"},
            )
            stdout, stderr = process.communicate(request_line, timeout=max(1, int(timeout_seconds)))
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.communicate()
            raise RawSlowLogSSHError("SSH exporter request timed out") from exc
        except OSError as exc:
            raise RawSlowLogSSHError("OpenSSH client could not be started") from exc
        if process.returncode != 0:
            # stderr can contain infrastructure details. Do not expose it from
            # the API; limit it to a local diagnostic log through the caller.
            code = "E5021" if "host key" in stderr.lower() or "verification failed" in stderr.lower() else "E5020"
            raise RawSlowLogSSHError(f"{code}: SSH exporter failed (exit={process.returncode}, detail={stderr.strip()[:160]})")
        messages: list[dict[str, Any]] = []
        for line in stdout.splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RawSlowLogSSHError("exporter returned non-NDJSON output") from exc
            if not isinstance(item, dict) or not isinstance(item.get("type"), str):
                raise RawSlowLogSSHError("exporter returned invalid NDJSON message")
            messages.append(item)
        if not messages:
            raise RawSlowLogSSHError("exporter returned no response")
        return messages
