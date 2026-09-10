# -*- coding: utf-8 -*-
"""v1.6.3.5 / SIT 第二轮 R2-01 — 部署脚本双服务契约（静态断言，防止再次只改一个脚本）。

A 第二轮 SIT：runner 只接进了 install.sh/verify_deploy.sh，而内网实际用
upgrade_incremental.sh 增量升级——修复到不了生产。本文件用静态断言锁定：
所有部署/升级/补丁/回滚/验证脚本都必须引用 tdsql-metadata-runner。
"""
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"

# 必须接线 runner 的脚本（增量/补丁/回滚/全量安装/部署验证）
RUNNER_SCRIPTS = [
    "install.sh",
    "upgrade_incremental.sh",
    "apply_patch.sh",
    "rollback.sh",
    "verify_deploy.sh",
]


def test_runner_unit_file_exists():
    assert (DEPLOY / "tdsql-metadata-runner.service").exists(), "runner unit 模板缺失"


@pytest.mark.parametrize("script", RUNNER_SCRIPTS)
def test_script_references_runner(script):
    content = (DEPLOY / script).read_text(encoding="utf-8")
    assert "tdsql-metadata-runner" in content, \
        f"{script} 未引用 tdsql-metadata-runner（runner 不会被安装/启动/校验）"


def _find_exec_line_pos(content: str, *patterns) -> int:
    """逐行找**可执行的** runner/Web 启动动作位置，排除注释行、pkill、echo/log。

    UAT-O-1635-R2-04：不能用全文 find——upgrade 删掉 systemd 启动行后会退而命中
    非 systemd 分支更早的 `pkill -f "backend.workers.metadata_runner"`（停止动作），
    把停止误判为启动。这里逐行剥离注释并排除 pkill，只认真正的启动命令。
    """
    for line in content.split("\n"):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "pkill" in line or "echo " in line or "log " in line or "log(" in line:
            continue
        for pat in patterns:
            if pat in line:
                return content.find(line)
    return -1


@pytest.mark.parametrize("script", ["install.sh", "upgrade_incremental.sh", "apply_patch.sh"])
def test_runner_starts_before_web(script):
    """先 runner 后 Web（SIT-R3-01 强化 + UAT-O-1635-R2-04）：比对**真正的 systemd 启动动作**。

    严格只认 `systemctl restart tdsql-metadata-runner`（排除注释/pkill/echo），删除、注释、
    挪到 Web 之后均变红；不回退到 nohup 分支（那是非 systemd 环境 fallback，单独校验）。
    """
    content = (DEPLOY / script).read_text(encoding="utf-8")
    runner_start = _find_exec_line_pos(content, "systemctl restart tdsql-metadata-runner")
    web_restart = _find_exec_line_pos(content, "systemctl restart tdsql-sqlcheck")
    assert runner_start != -1, f"{script} 未找到 metadata-runner 的可执行 systemd 启动动作"
    assert web_restart != -1, f"{script} 未找到 Web 的可执行启动动作"
    assert runner_start < web_restart, \
        f"{script} 中 runner 必须先于 Web 启动（设计要求 runner 先就绪）"


def test_upgrade_nohup_branch_starts_runner():
    """upgrade_incremental.sh 的非 systemd 分支必须真实用 nohup 拉起 runner（排除 pkill/注释）。

    UAT-O-1635-R2-04：非 systemd 分支单独校验真实 `nohup ... backend.workers.metadata_runner`。
    """
    content = (DEPLOY / "upgrade_incremental.sh").read_text(encoding="utf-8")
    nohup_pos = _find_exec_line_pos(content, "nohup")
    assert nohup_pos != -1 and "metadata_runner" in content, \
        "upgrade_incremental.sh 非 systemd 分支缺少 nohup 拉起 runner"
    # 找到含 metadata_runner 的 nohup 行（排除 pkill）
    found = False
    for line in content.split("\n"):
        s = line.strip()
        if s.startswith("#") or "pkill" in line:
            continue
        if "nohup" in line and "backend.workers.metadata_runner" in line:
            found = True
            break
    assert found, "upgrade_incremental.sh 非 systemd 分支未找到真实 nohup 拉起 runner 的行"


@pytest.mark.parametrize("script", ["install.sh", "upgrade_incremental.sh", "apply_patch.sh"])
def test_runner_failure_is_fatal(script):
    """runner 起不来必须使整个脚本返回非零，不得仅告警。"""
    content = (DEPLOY / script).read_text(encoding="utf-8")
    # runner is-active 检查后必须接 fail 或 exit 1（不能只 warn/log 继续）
    assert ("is-active" in content and ("fail" in content or "exit 1" in content)), \
        f"{script} 的 runner 启动失败未返回非零"


def test_verify_deploy_runner_check_gated_on_systemd_init():
    """verify_deploy 的 runner 检查必须以 systemd 为 PID1 init 为门（/run/systemd/system），
    非 systemd 环境不产生 [FAIL]（避免无 systemd 环境必然失败）。"""
    content = (DEPLOY / "verify_deploy.sh").read_text(encoding="utf-8")
    assert "/run/systemd/system" in content, \
        "runner 检查未以 /run/systemd/system 为门（A 环境有 systemctl 但非 init 会误判 FAIL）"
