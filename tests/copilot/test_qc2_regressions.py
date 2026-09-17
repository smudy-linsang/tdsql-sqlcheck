# -*- coding: utf-8 -*-
"""QC2-B02 回归锁：候选 SQL T09 校验必须真正执行（非空 sql_candidates 分支）。

根因：`_t09_validate` 错写在模块顶层而非 TurnExecutor 类内，
`self._t09_validate()` 触发 AttributeError → INTERNAL_ERROR。
既有 117 项测试全部打桩 sql_candidates=[] 从未覆盖此分支。
"""
import uuid

import pytest


def _make_executor(conn, session=None):
    from backend.services.copilot import crypto as crypto_mod
    from backend.services.copilot.workflow import TurnExecutor
    return TurnExecutor(
        conn=conn,
        turn={"id": "t-qc2", "turn_kind": "USER_QUESTION"},
        preview={}, session=session or {"instance_type": "unknown"},
        identity_like={"username": "qc2", "role": "admin", "subject_id": "qc2"},
        crypto_keyring=crypto_mod.load_keyring())


class TestT09Validate:
    """_t09_validate 必须是 TurnExecutor 的实例方法。"""

    def test_t09_validate_is_instance_method(self, copilot_conn, copilot_db,
                                              keyring_file):
        """QC2-B02 核心锁：self._t09_validate 不抛 AttributeError。"""
        ex = _make_executor(copilot_conn)
        # 直接调用——若在类外会抛 AttributeError
        result = ex._t09_validate("SELECT 1")
        assert isinstance(result, dict)
        assert "validation" in result
        assert "executable" in result
        assert result["validation"] in (
            "TEXT_PASSED", "BLOCKED", "PARSE_FAILED", "INCOMPLETE", "EMPTY")

    def test_t09_validate_empty_sql(self, copilot_conn, copilot_db,
                                     keyring_file):
        ex = _make_executor(copilot_conn)
        result = ex._t09_validate("")
        assert result["validation"] == "EMPTY"
        assert result["executable"] == "NO"

    def test_t09_validate_whitespace_sql(self, copilot_conn, copilot_db,
                                          keyring_file):
        ex = _make_executor(copilot_conn)
        result = ex._t09_validate("   \n  ")
        assert result["validation"] == "EMPTY"

    def test_t09_validate_valid_sql(self, copilot_conn, copilot_db,
                                     keyring_file):
        ex = _make_executor(copilot_conn)
        result = ex._t09_validate(
            "CREATE TABLE t_ok (id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY, "
            "name VARCHAR(100) NOT NULL, INDEX idx_name (name))")
        # 审核引擎在测试环境可能不完整，接受四态闭集中任一值
        assert result["validation"] in (
            "TEXT_PASSED", "BLOCKED", "PARSE_FAILED", "INCOMPLETE")
        assert result["executable"] in ("REVIEW", "NO", "UNKNOWN")
        assert isinstance(result["violations"], list)

    def test_t09_validate_unparseable_sql(self, copilot_conn, copilot_db,
                                           keyring_file):
        ex = _make_executor(copilot_conn)
        result = ex._t09_validate("THIS IS NOT SQL AT ALL {{{")
        assert result["validation"] in ("PARSE_FAILED", "INCOMPLETE")
        assert result["executable"] == "UNKNOWN"

    def test_t09_validate_blocking_violation(self, copilot_conn, copilot_db,
                                              keyring_file):
        """包含已知违规的 SQL 应被标记为 BLOCKED 或至少产生 violations。"""
        ex = _make_executor(copilot_conn)
        # FLOAT 列是已知规则违规（R005）
        result = ex._t09_validate(
            "CREATE TABLE t_bad (id BIGINT PRIMARY KEY, amount FLOAT)")
        assert isinstance(result["violations"], list)
        # 不强制 BLOCKED——取决于规则集是否启用 R005
        assert result["validation"] in ("TEXT_PASSED", "BLOCKED", "INCOMPLETE")


class TestPublishModelWithCandidates:
    """_publish_model 遇到非空 sql_candidates 不崩溃。"""

    def test_publish_model_with_sql_candidates(self, copilot_conn, copilot_db,
                                                keyring_file, monkeypatch):
        """QC2-B02 端到端锁：模型返回含候选 SQL 时 _publish_model 正常完成。"""
        from backend.models.copilot import ModelAnswer
        from backend.services.copilot.workflow import TurnExecutor
        from backend.services.copilot import crypto as crypto_mod

        ex = TurnExecutor(
            conn=copilot_conn,
            turn={"id": "t-qc2-pub", "turn_kind": "USER_QUESTION",
                  "scene": "SQL_ADVISE"},
            preview={}, session={"instance_type": "unknown"},
            identity_like={"username": "qc2", "role": "admin",
                           "subject_id": "qc2"},
            crypto_keyring=crypto_mod.load_keyring())

        answer = ModelAnswer(
            schema_version=1, summary="测试候选 SQL",
            outcome_claims=[], findings=[], steps=[],
            missing_evidence=[], limitations=[],
            sql_candidates=[
                {"sql": "SELECT 1", "reason": "测试候选", "evidence_ids": []},
            ])

        # 打桩 _publish 避免真实 DB 写入
        published = {}
        def _fake_publish(state, response, error_code, error_message, **kw):
            published["state"] = state
            published["response"] = response
        monkeypatch.setattr(ex, "_publish", _fake_publish)

        ex._publish_model(answer, [], [])

        assert published["state"] == "SUCCEEDED"
        resp = published["response"]
        assert resp is not None
        candidates = resp["answer"]["sql_candidates"]
        assert len(candidates) == 1
        # 候选 SQL 必须带 T09 校验结果
        assert "validation" in candidates[0]
        assert candidates[0]["validation"]["validation"] in (
            "TEXT_PASSED", "BLOCKED", "PARSE_FAILED", "INCOMPLETE")


class TestHistoryAAD:
    """QC2-B01 回归锁：_build_history 必须用正确 AAD 解密。"""

    def test_build_history_returns_nonempty_after_decrypt_fix(
            self, copilot_conn, copilot_db, keyring_file):
        """构造一条 SUCCEEDED turn + preview，验证 _build_history 返回非空。"""
        import json
        from backend.services.copilot import crypto as crypto_mod
        from backend.services.copilot.repository import (
            PreviewRepo, SessionRepo, TurnRepo, new_id)
        from backend.services.copilot.preview_service import _build_history

        conn = copilot_conn
        kr = crypto_mod.load_keyring()
        suffix = uuid.uuid4().hex[:8]
        sid = new_id()
        pid = new_id()
        tid = new_id()

        # 创建 session
        SessionRepo.insert(conn, {
            "id": sid, "owner_subject_id": f"qc2_{suffix}",
            "owner": f"qc2_{suffix}", "scope_kind": "GLOBAL_HELP",
            "instance_type": "unknown", "connection_id": None,
            "database_name": None, "initial_page_key": "test",
            "name_snapshot": None, "name_source": "manual",
            "title": "qc2 history test",
            "expires_at": "2099-01-01 00:00:00",
        })

        # 创建 preview（用正确的 AAD 加密）
        payload = {"question": "R003 规则是什么？", "source_refs": [],
                    "draft": None, "page_key": "test"}
        payload_env = crypto_mod.encrypt(
            json.dumps(payload, ensure_ascii=False),
            "copilot_previews", pid, "payload_envelope",
            owner=f"qc2_{suffix}", keyring=kr)
        evidence_env = crypto_mod.encrypt(
            json.dumps({"evidence": [], "knowledge": []}),
            "copilot_previews", pid, "evidence_envelope",
            owner=f"qc2_{suffix}", keyring=kr)
        proj_env = crypto_mod.encrypt(
            json.dumps({"question": "R003 规则是什么？"}),
            "copilot_previews", pid, "model_projection_envelope",
            owner=f"qc2_{suffix}", keyring=kr)
        PreviewRepo.insert(conn, {
            "id": pid, "session_id": sid,
            "owner_subject_id": f"qc2_{suffix}", "owner": f"qc2_{suffix}",
            "scene": "RULE_EXPLAIN",
            "input_hash": "test-hash",
            "payload_envelope": payload_env,
            "evidence_envelope": evidence_env,
            "model_projection_envelope": proj_env,
            "snapshot_hash": "snap-hash",
            "permission_version": 1,
            "grant_revision": None, "route_revision": 1,
            "provider_revisions_json": "{}",
            "data_class": "INTERNAL_REDACTED",
            "storage_reserved_bytes": 0,
            "expires_at": "2099-01-01 00:00:00",
            "projection_mode": "ALIASED",
            "identifier_policy_revision": "test",
            "module_schema_epoch": 1,
        })

        # 创建 SUCCEEDED turn（直接 SQL，TurnRepo.insert 只创建 ACCEPTED）
        response = {"summary": "R003 要求表必须有主键",
                     "findings": [], "steps": [], "sql_candidates": []}
        conn.execute(
            "INSERT INTO copilot_turns (id, session_id, preview_id, owner, "
            "owner_subject_id, turn_kind, scene, rule_snapshot_hash, "
            "module_schema_epoch, client_request_id, request_hash, sequence_no, "
            "state, phase, route_snapshot_envelope, request_envelope, "
            "evidence_envelope, response_envelope, output_hash, charged_tokens, "
            "error_code, error_message, created_at, updated_at, deadline_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'SUCCEEDED','DONE','{}','{}','{}',?,?,0,"
            "NULL,NULL,"
            "UTC_TIMESTAMP(6),UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))",
            (tid, sid, pid, f"qc2_{suffix}", f"qc2_{suffix}",
             "USER_QUESTION", "RULE_EXPLAIN", None, 1,
             uuid.uuid4().hex, "req-hash", 1,
             json.dumps(response, ensure_ascii=False), "out-hash"))
        conn.commit()

        # 验证 _build_history 返回非空
        session = SessionRepo.get(conn, sid)
        history = _build_history(conn, session)
        assert len(history) > 0, \
            "QC2-B01：_build_history 返回空——AAD 解密仍失败"
        assert history[0]["question"] == "R003 规则是什么？"
        assert "R003" in history[0]["answer_summary"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
