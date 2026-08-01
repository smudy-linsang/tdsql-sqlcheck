from __future__ import annotations

from pathlib import Path

import pytest

from backend.services.raw_slowlog_ssh import (
    RawSlowLogSSHClient,
    RawSlowLogSSHError,
    SSHSecretResolver,
)


def _node():
    return {"ssh_host": "10.0.0.8", "ssh_port": 2222, "host_key_alias": "proxy-a"}


def _source():
    return {"credential_ref": "reader", "known_hosts_ref": "sit"}


def test_ssh_argv_is_pinned_noninteractive_and_has_no_remote_command(tmp_path: Path):
    (tmp_path / "reader.key").write_text("not-a-real-key", encoding="utf-8")
    (tmp_path / "sit.known_hosts").write_text("proxy-a ssh-ed25519 AAAA", encoding="utf-8")
    client = RawSlowLogSSHClient(executable="ssh", secret_resolver=SSHSecretResolver(tmp_path))

    argv = client.build_argv(_node(), _source())

    assert argv[0] == "ssh"
    assert "-F" in argv and argv[argv.index("-F") + 1] == "none"
    assert "-T" in argv
    assert "BatchMode=yes" in argv
    assert "StrictHostKeyChecking=yes" in argv
    assert "HostKeyAlias=proxy-a" in argv
    assert argv[-1] == "tdsql_log_reader@10.0.0.8"
    assert len(argv) == argv.index(argv[-1]) + 1  # no caller-controlled remote command


def test_ssh_client_rejects_non_ndjson_response(tmp_path: Path, monkeypatch):
    (tmp_path / "reader.key").write_text("key", encoding="utf-8")
    (tmp_path / "sit.known_hosts").write_text("hosts", encoding="utf-8")
    client = RawSlowLogSSHClient(secret_resolver=SSHSecretResolver(tmp_path))

    class FakeProcess:
        returncode = 0
        def communicate(self, data, timeout):
            assert '"op":"probe"' in data
            return "not-json\n", ""
        def kill(self):
            pass

    monkeypatch.setattr("backend.services.raw_slowlog_ssh.subprocess.Popen", lambda *args, **kwargs: FakeProcess())
    with pytest.raises(RawSlowLogSSHError, match="non-NDJSON"):
        client.request(_node(), _source(), {"op": "probe"}, 5)


def test_ssh_client_marks_host_key_verification_failure(tmp_path: Path, monkeypatch):
    (tmp_path / "reader.key").write_text("key", encoding="utf-8")
    (tmp_path / "sit.known_hosts").write_text("hosts", encoding="utf-8")
    client = RawSlowLogSSHClient(secret_resolver=SSHSecretResolver(tmp_path))

    class FakeProcess:
        returncode = 255

        def communicate(self, data, timeout):
            return "", "Host key verification failed."

        def kill(self):
            pass

    monkeypatch.setattr("backend.services.raw_slowlog_ssh.subprocess.Popen", lambda *args, **kwargs: FakeProcess())
    with pytest.raises(RawSlowLogSSHError, match="E5021"):
        client.request(_node(), _source(), {"op": "probe"}, 5)
