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


@pytest.mark.parametrize("script", ["install.sh", "upgrade_incremental.sh", "apply_patch.sh"])
def test_runner_starts_before_web(script):
    """先 runner 后 Web（SIT-R3-01 强化）：比对**真正的启动动作**位置，而非 unit 安装段。

    runner 启动动作 = `systemctl restart tdsql-metadata-runner` 或非 systemd 分支的
    `backend.workers.metadata_runner`（nohup 拉起）；Web 启动 = `systemctl restart tdsql-sqlcheck`。
    这样即使 runner 的 unit 安装段在前、但启动被删掉/挪到 Web 之后，也能被本断言抓住。
    """
    content = (DEPLOY / script).read_text(encoding="utf-8")
    runner_start = content.find("systemctl restart tdsql-metadata-runner")
    if runner_start == -1:
        runner_start = content.find("backend.workers.metadata_runner")  # 非 systemd nohup 分支
    web_restart = content.find("systemctl restart tdsql-sqlcheck")
    assert runner_start != -1, f"{script} 未找到 metadata-runner 的启动动作"
    assert web_restart != -1, f"{script} 未找到 Web 的启动动作"
    assert runner_start < web_restart, \
        f"{script} 中 runner 必须先于 Web 启动（设计要求 runner 先就绪）"


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
