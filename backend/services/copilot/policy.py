# -*- coding: utf-8 -*-
"""v1.6.4.0 / CP-1：部署参数与出站端点策略（CP-W03，DETAIL §7.3/§9.2/§16.1）。

部署硬闸：COPILOT_ENABLED 是硬总闸（部署环境），DB 开关只能进一步关闭。
端点清单来自部署文件 COPILOT_ENDPOINTS_FILE（schema_version=1），
管理 UI 只选择 endpoint_id，不能输入任意 URL/代理/headers/CA 路径。
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

from backend.services.copilot.errors import CopilotError

logger = logging.getLogger("tdsql.copilot.policy")

_ENDPOINTS_MAX_BYTES = 256 * 1024
_ENDPOINTS_MAX_COUNT = 64
_ENDPOINT_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
_BASE_PATH_RE = re.compile(r"^/[A-Za-z0-9/_-]*$")


# ══════════════════════════════════════════════════════════════════
# §7.3 部署参数（环境变量，动态读取；超界拒绝启用，不静默夹值）
# ══════════════════════════════════════════════════════════════════

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def copilot_enabled() -> bool:
    """部署硬总闸（默认 false）。"""
    return os.getenv("COPILOT_ENABLED", "false").strip().lower() in ("true", "1", "yes", "on")


def deployed_disabled_reason() -> Optional[str]:
    """返回部署侧强制停用原因；None 表示未强制停用。

    统一供 capabilities / admit / preview / runner 使用，避免各处口径不一致。
    """
    from backend.services.copilot import local_ready
    _lr, reason = local_ready()
    return reason if reason == "COPILOT_DISABLED" else None


def allow_schema_identifiers_deploy() -> bool:
    """部署级结构标识符闸（默认 false；DB 只能进一步关闭）。"""
    return os.getenv("COPILOT_ALLOW_SCHEMA_IDENTIFIERS", "false").strip().lower() in \
        ("true", "1", "yes", "on")


class Limits:
    """§7.3 资源上限参数（含允许范围；读取时做范围校验）。"""

    RANGES = {
        "COPILOT_RUNNER_CONCURRENCY": (1, 4, 2),
        "COPILOT_MAX_ACTIVE_TURNS": (2, 8, 4),
        "COPILOT_TURN_DEADLINE_SECONDS": (30, 3600, 3600),
        "COPILOT_QUEUE_MAX_SECONDS": (5, 30, 15),
        "COPILOT_PROVIDER_TOTAL_SECONDS": (10, 3600, 3500),
        "COPILOT_HTTP_CONNECT_SECONDS": (1, 30, 10),
        "COPILOT_HTTP_READ_SECONDS": (5, 3600, 3600),
        "COPILOT_CONTEXT_MAX_BYTES": (8192, 32768, 24576),
        "COPILOT_INPUT_TOKEN_UPPER_BOUND": (4096, 32768, 16384),
        "COPILOT_OUTPUT_MAX_TOKENS": (512, 4096, 2048),
    }

    COPILOT_RESPONSE_MAX_BYTES = 1048576   # 固定 1MiB，边读边限
    COPILOT_ANSWER_MAX_BYTES = 32768       # 固定 32KiB
    COPILOT_MAX_ACTIVE_PER_USER = 1        # 首期固定 1
    COPILOT_STORAGE_MAX_MIB = 2048         # 正文逻辑配额（不含索引/审计/备份）

    @classmethod
    def get(cls, name: str) -> int:
        lo, hi, default = cls.RANGES[name]
        v = _env_int(name, default)
        return max(lo, min(hi, v))

    @classmethod
    def validate(cls) -> list[str]:
        """部署参数范围校验（启动/自检用；返回问题清单，空=通过）。"""
        problems = []
        for name, (lo, hi, default) in cls.RANGES.items():
            raw = os.getenv(name)
            if raw is None:
                continue
            try:
                v = int(raw)
            except ValueError:
                problems.append(f"{name} 非整数: {raw!r}")
                continue
            if not (lo <= v <= hi):
                problems.append(f"{name}={v} 超出允许范围 [{lo},{hi}]")
        if cls.get("COPILOT_QUEUE_MAX_SECONDS") > cls.get("COPILOT_TURN_DEADLINE_SECONDS") // 3:
            problems.append("COPILOT_QUEUE_MAX_SECONDS 不得超过 turn deadline 的 1/3")
        if cls.get("COPILOT_PROVIDER_TOTAL_SECONDS") > cls.get("COPILOT_TURN_DEADLINE_SECONDS") - 15:
            problems.append("COPILOT_PROVIDER_TOTAL_SECONDS 必须 ≤ turn deadline − 15")
        return problems

    @classmethod
    def snapshot(cls) -> dict:
        return {name: cls.get(name) for name in cls.RANGES} | {
            "COPILOT_RESPONSE_MAX_BYTES": cls.COPILOT_RESPONSE_MAX_BYTES,
            "COPILOT_ANSWER_MAX_BYTES": cls.COPILOT_ANSWER_MAX_BYTES,
            "COPILOT_MAX_ACTIVE_PER_USER": cls.COPILOT_MAX_ACTIVE_PER_USER,
            "COPILOT_STORAGE_MAX_MIB": cls.COPILOT_STORAGE_MAX_MIB,
        }


# ══════════════════════════════════════════════════════════════════
# §9.2 出站端点策略
# ══════════════════════════════════════════════════════════════════

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/0"),
    ipaddress.ip_network("127.0.0.0/8"),      # 回环
    ipaddress.ip_network("169.254.0.0/16"),   # 链路本地（含云元数据 169.254.169.254）
]
_BLOCKED_V6 = [
    ipaddress.ip_network("::/0"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fe80::/10"),
]


def _validate_cidr(cidr: str) -> Optional[str]:
    try:
        net = ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return f"CIDR 非法: {cidr}"
    for blocked in _BLOCKED_NETWORKS + _BLOCKED_V6:
        if net.version != blocked.version:
            continue
        # 仅拒绝“整条禁止网段被包含在批准 CIDR 中”或二者相等/批准范围更宽
        if blocked.prefixlen == 0:
            if net.prefixlen == 0:
                return f"CIDR 落入禁止范围 {blocked}: {cidr}"
            continue
        if net.subnet_of(blocked):
            return f"CIDR 落入禁止范围 {blocked}: {cidr}"
    return None


class EndpointPolicy:
    """部署批准端点清单（只读/管理写入并存；Web/runner 共享）。"""

    def __init__(self, path: str):
        self.path = path
        self._endpoints: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        p = Path(self.path)
        if not p.exists():
            raise CopilotError("POLICY_UNAVAILABLE")
        raw = p.read_bytes()
        if len(raw) > _ENDPOINTS_MAX_BYTES:
            raise CopilotError("POLICY_UNAVAILABLE")
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            raise CopilotError("POLICY_UNAVAILABLE")
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise CopilotError("POLICY_UNAVAILABLE")
        eps = data.get("endpoints")
        if not isinstance(eps, list) or len(eps) > _ENDPOINTS_MAX_COUNT:
            raise CopilotError("POLICY_UNAVAILABLE")
        for ep in eps:
            self._validate_entry(ep)
            eid = ep["endpoint_id"]
            if eid in self._endpoints:
                raise CopilotError("POLICY_UNAVAILABLE")  # 重复 endpoint_id
            self._endpoints[eid] = ep

    @staticmethod
    def _validate_entry(ep: dict) -> None:
        required = ("endpoint_id", "scheme", "canonical_host", "port", "base_path",
                    "data_zone", "privacy_profile", "allows_schema_identifiers",
                    "allowed_resolved_cidrs", "tls_ca_ref")
        if not isinstance(ep, dict):
            raise CopilotError("POLICY_UNAVAILABLE")
        for k in required:
            if k not in ep:
                raise CopilotError("POLICY_UNAVAILABLE")
        if not _ENDPOINT_ID_RE.match(str(ep["endpoint_id"])):
            raise CopilotError("POLICY_UNAVAILABLE")
        scheme = str(ep.get("scheme", "")).lower()
        if scheme not in ("https", "http"):
            raise CopilotError("POLICY_UNAVAILABLE")
        if scheme == "http":
            # HTTP 仅限 INTERNAL 内网分区，且需 ep["allow_http"]=True 或环境变量 COPILOT_ALLOW_HTTP=true
            is_internal = ep.get("data_zone") == "INTERNAL"
            allowed_http = ep.get("allow_http", False) or os.getenv("COPILOT_ALLOW_HTTP", "false").lower() in ("true", "1", "yes")
            if not is_internal or not allowed_http:
                raise CopilotError("POLICY_UNAVAILABLE")
        if not _HOST_RE.match(str(ep["canonical_host"])):
            raise CopilotError("POLICY_UNAVAILABLE")
        host = str(ep["canonical_host"])
        if "@" in host or "?" in host or "#" in host or "*" in host:
            raise CopilotError("POLICY_UNAVAILABLE")  # userinfo/query/泛域名
        port = int(ep["port"])
        if not (1 <= port <= 65535):
            raise CopilotError("POLICY_UNAVAILABLE")
        if not _BASE_PATH_RE.match(str(ep["base_path"])):
            raise CopilotError("POLICY_UNAVAILABLE")
        if ".." in str(ep["base_path"]):
            raise CopilotError("POLICY_UNAVAILABLE")
        if str(ep["data_zone"]) not in ("INTERNAL", "PUBLIC"):
            raise CopilotError("POLICY_UNAVAILABLE")
        if str(ep["privacy_profile"]) not in ("INTERNAL_REDACTED", "PUBLIC_HELP"):
            raise CopilotError("POLICY_UNAVAILABLE")
        # PUBLIC 端点不允许结构标识符；allows 默认 false
        if ep["allows_schema_identifiers"] not in (True, False):
            raise CopilotError("POLICY_UNAVAILABLE")
        if ep["data_zone"] == "PUBLIC" and ep["allows_schema_identifiers"]:
            raise CopilotError("POLICY_UNAVAILABLE")  # PUBLIC+true 直接拒绝
        cidrs = ep["allowed_resolved_cidrs"]
        if not isinstance(cidrs, list) or not cidrs:
            raise CopilotError("POLICY_UNAVAILABLE")
        for c in cidrs:
            if _validate_cidr(str(c)):
                raise CopilotError("POLICY_UNAVAILABLE")

    def get(self, endpoint_id: str) -> Optional[dict]:
        return self._endpoints.get(endpoint_id)

    def list_public_view(self) -> list[dict]:
        """管理端可见的非敏感视图（不含 CA 路径细节仅引用名）。"""
        return [{
            "endpoint_id": e["endpoint_id"],
            "scheme": e.get("scheme", "https"),
            "canonical_host": e["canonical_host"],
            "port": e["port"],
            "base_path": e["base_path"],
            "data_zone": e["data_zone"],
            "privacy_profile": e["privacy_profile"],
            "allows_schema_identifiers": e["allows_schema_identifiers"],
            "allowed_resolved_cidrs": e.get("allowed_resolved_cidrs", []),
            "tls_ca_ref": e.get("tls_ca_ref", "internal"),
            "description": e.get("description", ""),
            "allow_http": e.get("allow_http", False),
        } for e in self._endpoints.values()]

    def build_url(self, endpoint_id: str) -> str:
        """拼接 chat completions URL：去末尾 / 后只追加一次 /chat/completions。"""
        ep = self._endpoints.get(endpoint_id)
        if ep is None:
            raise CopilotError("PROVIDER_CONFIG_INVALID")
        scheme = ep.get("scheme", "https")
        base = f"{scheme}://{ep['canonical_host']}:{ep['port']}{ep['base_path']}"
        return base.rstrip("/") + "/chat/completions"

    def egress_check(self, endpoint_id: str, data_class: str,
                     identifiers: bool = False) -> None:
        """出域策略闸：数据分级/标识符能力与端点匹配，不通过即 EGRESS_DENIED。"""
        ep = self._endpoints.get(endpoint_id)
        if ep is None:
            raise CopilotError("EGRESS_DENIED")
        if data_class == "RESTRICTED":
            raise CopilotError("EGRESS_DENIED")
        if data_class == "INTERNAL_REDACTED" and ep["data_zone"] != "INTERNAL":
            raise CopilotError("EGRESS_DENIED")  # 内部资料决不因故障转移到公网
        if data_class == "PUBLIC_HELP" and ep["privacy_profile"] != "PUBLIC_HELP" \
                and ep["data_zone"] != "INTERNAL":
            raise CopilotError("EGRESS_DENIED")
        if identifiers and not ep["allows_schema_identifiers"]:
            raise CopilotError("EGRESS_DENIED")

    def upsert(self, ep: dict) -> None:
        """管理员在线添加或更新端点配置，并原子持久化到配置文件。"""
        self._validate_entry(ep)
        self._endpoints[ep["endpoint_id"]] = ep
        self._persist()

    def remove(self, endpoint_id: str) -> None:
        """管理员删除端点配置，并原子持久化到配置文件。"""
        if endpoint_id not in self._endpoints:
            raise CopilotError("NOT_FOUND", message=f"端点 {endpoint_id} 不存在")
        del self._endpoints[endpoint_id]
        self._persist()

    def _persist(self) -> None:
        """原子写入 JSON 配置文件并同步 mtime 缓存。"""
        p = Path(self.path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "schema_version": 1,
            "endpoints": list(self._endpoints.values())
        }
        tmp_path = p.with_suffix(f".tmp.{os.getpid()}")
        tmp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp_path.replace(p)
        global _policy_mtime_cache
        try:
            _policy_mtime_cache = os.path.getmtime(self.path)
        except OSError:
            pass


_policy_cache: Optional[EndpointPolicy] = None
_policy_path_cache: str = ""
_policy_mtime_cache: float = 0.0


def reset_policy_cache() -> None:
    global _policy_cache, _policy_path_cache, _policy_mtime_cache
    _policy_cache = None
    _policy_path_cache = ""
    _policy_mtime_cache = 0.0


def load_policy(path: Optional[str] = None) -> EndpointPolicy:
    """加载端点策略；支持基于文件 mtime 的自动热重载与缺省自动兜底。"""
    global _policy_cache, _policy_path_cache, _policy_mtime_cache
    p = path or os.getenv("COPILOT_ENDPOINTS_FILE", "")
    if not p:
        for candidate in [
            Path("data/reports/qc_o_1640/copilot-endpoints.json"),
            Path("data/copilot-endpoints.json"),
            Path(__file__).resolve().parent.parent.parent.parent / "data/reports/qc_o_1640/copilot-endpoints.json",
        ]:
            if candidate.exists():
                p = str(candidate)
                break
        if not p:
            p = str(Path("data/copilot-endpoints.json").resolve())
            if not Path(p).exists():
                Path(p).parent.mkdir(parents=True, exist_ok=True)
                Path(p).write_text(json.dumps({"schema_version": 1, "endpoints": []}), encoding="utf-8")

    mtime = 0.0
    try:
        mtime = os.path.getmtime(p)
    except OSError:
        pass

    if _policy_cache is not None and _policy_path_cache == p and _policy_mtime_cache == mtime:
        return _policy_cache

    pol = EndpointPolicy(p)
    _policy_cache = pol
    _policy_path_cache = p
    _policy_mtime_cache = mtime
    return pol


def policy_available() -> bool:
    try:
        load_policy()
        return True
    except Exception:
        return False
