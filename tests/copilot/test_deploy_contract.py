# -*- coding: utf-8 -*-
"""v1.6.4.0 / CP-1：Copilot runner 部署接线契约（静态断言，CP-W10）。

锁定 DETAIL §16.2/§16.3：所有部署/升级/补丁/回滚/验证脚本都必须处理
tdsql-copilot-runner；防止“只改全量安装脚本导致增量升级到不了生产”的
历史缺陷复发。copilot 失败不阻断核心 Web（warn 而非 fail）。
"""
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"

COPILOT_SCRIPTS = [
    "install.sh",
    "upgrade_incremental.sh",
    "apply_patch.sh",
    "rollback.sh",
    "verify_deploy.sh",
]


def test_copilot_runner_unit_file_exists():
    assert (DEPLOY / "tdsql-copilot-runner.service").exists(), \
        "copilot-runner unit 模板缺失"


def test_copilot_emergency_disable_exists():
    assert (DEPLOY / "copilot_emergency_disable.sh").exists(), \
        "紧急停用脚本缺失"


def test_copilot_examples_exist():
    assert (DEPLOY / "copilot-endpoints.example.json").exists()
    assert (DEPLOY / "copilot-keyring.example.json").exists()


@pytest.mark.parametrize("script", COPILOT_SCRIPTS)
def test_script_references_copilot_runner(script):
    content = (DEPLOY / script).read_text(encoding="utf-8")
    assert "copilot" in content.lower() or "tdsql-copilot-runner" in content, \
        f"{script} 未处理 tdsql-copilot-runner"


def test_emergency_disable_requires_incident_id():
    """紧急停用脚本必须校验受限事件编号，不允许任意命令注入。"""
    content = (DEPLOY / "copilot_emergency_disable.sh").read_text(encoding="utf-8")
    assert "--incident-id" in content
    assert "A-Za-z0-9-" in content  # 事件编号白名单正则
    # 不允许把外部输入直接拼进 systemctl/pkill 的目标（固定 unit 名）
    assert 'UNIT="tdsql-copilot-runner"' in content


def test_runner_unit_hard_limits():
    """unit 文件必须含 KillMode/TimeoutStopSec/MemoryMax 等硬限制（§16.2）。"""
    content = (DEPLOY / "tdsql-copilot-runner.service").read_text(encoding="utf-8")
    for key in ("KillMode=control-group", "TimeoutStopSec=20",
                "Restart=on-failure", "NoNewPrivileges=true",
                "MemoryMax=1G", "TasksMax=64"):
        assert key in content, f"copilot unit 缺少 {key}"


@pytest.mark.parametrize("script", ["install.sh", "upgrade_incremental.sh",
                                    "apply_patch.sh"])
def test_copilot_runner_before_web(script):
    """copilot-runner 的 systemd 启动动作必须在 Web 重启之前（与 metadata-runner 同序）。"""
    content = (DEPLOY / script).read_text(encoding="utf-8")
    copilot_pos = content.find("tdsql-copilot-runner")
    web_pos = content.find("systemctl restart tdsql-sqlcheck")
    if web_pos == -1:
        web_pos = content.find("systemctl start tdsql-sqlcheck")
    assert copilot_pos != -1, f"{script} 未引用 copilot-runner"
    assert web_pos != -1, f"{script} 未找到 Web 启动动作"
    assert copilot_pos < web_pos, \
        f"{script} 中 copilot-runner 必须先于 Web（安装/部署顺序）"
