# -*- coding: utf-8 -*-
"""第五轮 UAT 准入条件 F-4：provider 自检链路回归锁。

> **参考实现（智能体D 编写并已验证红→绿）**。建议原样收录为
> `tests/copilot/test_provider_selftest_e2e.py`。
> 收录后请按工单要求，临时回退修复跑一次，把「红 → 绿」两次输出贴进开发记录。

为什么必须有这个文件：自检链路连续四轮、四个不同根因全部是"跑一次就会暴露"的类型——

| 轮次 | 根因 | 本文件哪条锁能抓住 |
|---|---|---|
| 第二轮 | `selftest` 手写会话缺 6 个必填字段 → 500 | `test_selftest_admits_and_writes_tested_revision` |
| 第三轮 | `module_schema_epoch` 写死 0 → CONTEXT_CHANGED | 同上（真跑执行器） |
| 第四轮 | 封套 AAD 行 ID 用字面量 `"selftest"` → InvalidTag | `test_selftest_envelopes_decrypt_with_real_preview_id` |
| 第四轮 | 自检轮被 `enabled=0` 挡住 → 永远发不出请求 | `test_selftest_turn_bypasses_enabled_only_for_selftest` |
"""
import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture(autouse=True)
def _ensure_schema_ready(copilot_db):
    """迁移器测试可能破坏 module_state，每条锁跑前确保 B 组就绪。"""
    from backend.services.copilot import schema as schema_mod
    from backend.services.database import _get_connection
    conn = _get_connection()
    try:
        rc = schema_mod.apply_business_schema()
        assert rc == 0, f"B组 schema 恢复失败 rc={rc}"
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════
# 夹具：一个未启用、未自检的 provider + 自检身份
# ══════════════════════════════════════════════════════════════════
def _make_provider(conn, name_suffix):
    from backend.services.copilot.repository import ProviderRepo, new_id
    pid = new_id()
    ProviderRepo.insert(conn, {
        "id": pid, "name": f"selftest_{name_suffix}",
        "endpoint_id": "ep-test", "protocol": "OPENAI_COMPAT_CHAT",
        "model_id": "selftest-model", "auth_mode": "NETWORK_IDENTITY",
        "capabilities_json": "{}", "secret_envelope": None,
    })
    return pid


def _identity(name_suffix):
    from backend.services.copilot.authz import CopilotIdentity
    return CopilotIdentity(f"selftest_{name_suffix}", "admin",
                           str(uuid.uuid4().hex), "")


class _ProvCallOK:
    """离线打桩：返回一份引用本轮真实 K 编号的合规答案。"""

    @staticmethod
    def make(payload_getter):
        async def _fake_call_provider(url, auth_mode, secret, body, *a, **kw):
            from backend.services.copilot.providers import ProviderCallResult
            user = ""
            for m in body.get("messages") or []:
                if m.get("role") == "user":
                    user = m.get("content") or ""
            try:
                payload = json.loads(user)
            except Exception:
                payload = {}
            kn = [k.get("knowledge_id") for k in (payload.get("knowledge") or [])
                  if k.get("knowledge_id")]
            ans = {"schema_version": 1, "summary": "自检合成答案",
                   "outcome_claims": [], "findings": [], "steps": [],
                   "missing_evidence": [], "sql_candidates": [], "limitations": []}
            if kn:
                ans["findings"].append({"kind": "POLICY", "text": "自检引用",
                                        "evidence_ids": [], "knowledge_ids": [kn[0]]})
            return ProviderCallResult(
                ok=True, http_status=200,
                body_text=json.dumps({
                    "choices": [{"message": {"content": json.dumps(ans)},
                                 "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}}))
        return _fake_call_provider


# ══════════════════════════════════════════════════════════════════
# 锁 1（最重要）：自检必须真正跑完并写回 tested_revision
# ══════════════════════════════════════════════════════════════════
def test_selftest_admits_and_writes_tested_revision(
        copilot_conn, copilot_db, keyring_file, endpoints_file, monkeypatch):
    """自检 → 执行器真跑一遍 → tested_revision 必须被写回。

    只断言"端点返回 202"没有意义：202 只代表受理。
    """
    from backend.services.copilot import crypto as crypto_mod
    from backend.services.copilot import providers as prov_mod
    from backend.services.copilot.repository import (
        PreviewRepo, ProviderRepo, SessionRepo, TurnRepo)
    from backend.services.copilot.selftest import admit_self_test
    from backend.services.copilot.workflow import TurnExecutor

    conn = copilot_conn
    suffix = uuid.uuid4().hex[:8]
    pid = _make_provider(conn, suffix)
    identity = _identity(suffix)
    provider = ProviderRepo.get(conn, pid)
    assert provider is not None and int(provider["enabled"] or 0) == 0, \
        "前置：新建 provider 必须是未启用状态（自检的目的就是启用前先验证）"

    # ── 受理自检（第二轮缺字段就是在这里 500 的）──
    turn_info = admit_self_test(conn, identity, provider, uuid.uuid4().hex)
    turn_id = turn_info["id"]

    # ── 模拟 runner 的「领取」：CAS ACCEPTED→RUNNING + fencing token ──
    # 不直接用 TurnRepo.claim_next()：它取全库最早的 ACCEPTED，
    # 在共享测试库里会被其它残留轮次抢走，导致用例不稳定。
    attempt_token = uuid.uuid4().hex
    cur = conn.execute(
        "UPDATE copilot_turns SET state='RUNNING', phase='EVIDENCE', "
        "attempt_token=?, runner_id=?, started_at=UTC_TIMESTAMP(6), "
        "lease_until=UTC_TIMESTAMP(6) + INTERVAL 60 SECOND, "
        "updated_at=UTC_TIMESTAMP(6) WHERE id=? AND state='ACCEPTED'",
        (attempt_token, "selftest-test-runner", turn_id))
    assert cur.rowcount == 1, "领取自检轮失败（CAS ACCEPTED→RUNNING）"

    turn = TurnRepo.get(conn, turn_id)
    preview = PreviewRepo.get(conn, turn["preview_id"])
    session = SessionRepo.get(conn, turn["session_id"])
    assert turn is not None and preview is not None and session is not None

    # ── 离线打桩模型调用，真跑执行器（第三轮 epoch 冻结校验在这条路径上）──
    monkeypatch.setattr(prov_mod, "call_provider", _ProvCallOK.make(None))
    executor = TurnExecutor(
        conn=conn, turn=turn, preview=preview, session=session,
        identity_like={"username": identity.username, "role": "admin",
                       "subject_id": identity.subject_id},
        crypto_keyring=crypto_mod.load_keyring())
    executor.run()

    final = TurnRepo.get(conn, turn_id)
    assert final["state"] == "SUCCEEDED", \
        f"自检未成功：state={final['state']} error={final.get('error_code')}"

    # ── 关键断言 ──
    after = ProviderRepo.get(conn, pid)
    assert after["tested_revision"] is not None, \
        "自检未写回 tested_revision：自检链路仍然是死的（回归 F-1）"
    assert int(after["tested_revision"]) == int(after["revision"]), \
        "tested_revision 必须等于当前 revision"

    # 自检通过后必须能启用
    from backend.services.copilot.repository import ProviderRepo as PR
    assert PR.set_enabled(conn, pid, True, int(after["revision"])), \
        "自检通过后仍无法启用 provider"


# ══════════════════════════════════════════════════════════════════
# 锁 2：封套 AAD 行 ID 必须等于真实 preview 主键
# ══════════════════════════════════════════════════════════════════
def test_selftest_envelopes_decrypt_with_real_preview_id(
        copilot_conn, copilot_db, keyring_file):
    """回归 R4-B01：AAD 行 ID 用字面量会让 AES-GCM 必然 InvalidTag。"""
    from backend.services.copilot import crypto as crypto_mod
    from backend.services.copilot.repository import PreviewRepo, ProviderRepo
    from backend.services.copilot.selftest import admit_self_test

    conn = copilot_conn
    suffix = uuid.uuid4().hex[:8]
    pid = _make_provider(conn, suffix)
    identity = _identity(suffix)
    provider = ProviderRepo.get(conn, pid)
    turn_id = admit_self_test(conn, identity, provider, uuid.uuid4().hex)["id"]

    from backend.services.copilot.repository import TurnRepo
    turn = TurnRepo.get(conn, turn_id)
    row = PreviewRepo.get(conn, turn["preview_id"])
    kr = crypto_mod.load_keyring()

    # 用 preview 的真实主键作为 AAD 解密——runner 就是这么解的
    for col in ("payload_envelope", "model_projection_envelope"):
        raw = crypto_mod.decrypt(row[col], "copilot_previews", row["id"], col,
                                 owner=row["owner_subject_id"], keyring=kr)
        assert json.loads(raw)["question"], f"{col} 解密失败或内容异常"


# ══════════════════════════════════════════════════════════════════
# 锁 3：自检轮放行 enabled=0，业务轮不放行
# ══════════════════════════════════════════════════════════════════
def test_selftest_turn_bypasses_enabled_only_for_selftest(
        copilot_conn, copilot_db, keyring_file, endpoints_file, monkeypatch):
    """回归 R4-B02：自检的目的是"启用前先验证"，此时 provider 必然 enabled=0。"""
    from backend.models.copilot import TurnKind
    from backend.services.copilot import crypto as crypto_mod
    from backend.services.copilot.repository import ProviderRepo
    from backend.services.copilot.workflow import TurnExecutor

    conn = copilot_conn
    suffix = uuid.uuid4().hex[:8]
    pid = _make_provider(conn, suffix)
    provider = ProviderRepo.get(conn, pid)
    assert int(provider["enabled"] or 0) == 0
    leg = {"provider_id": pid, "provider_revision": int(provider["revision"])}

    def _executor(turn_kind):
        return TurnExecutor(conn=conn,
                            turn={"id": "t", "turn_kind": turn_kind},
                            preview={}, session={},
                            identity_like={"username": "x", "role": "admin",
                                           "subject_id": "x"},
                            crypto_keyring=crypto_mod.load_keyring())

    assert _executor(TurnKind.PROVIDER_SELFTEST.value)._load_provider_frozen(leg) \
        is not None, "自检轮必须能对未启用的 provider 发起验证调用"

    assert _executor(TurnKind.USER_QUESTION.value)._load_provider_frozen(leg) \
        is None, "业务轮不得放行未启用的 provider"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
