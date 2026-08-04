"""ZK 标准化导入"提交路径"回归测试（v1.6.0.1 修复 P2：零覆盖补全）。

覆盖设计 §13.1 的 ZI-10/ZI-11/ZI-12：
- 正常提交：一库一连接、命名契约、口令仅加密落库、批次审计脱敏；
- 原子性：提交期冲突（地址端口库 / 连接名）→ 409 且零连接写入；
- 失败留痕：冲突/非 ready 提交在独立短事务登记 status='failed' 批次（修复 P4）；
- 会话安全：预览一次性（重放 410）、过期 410、他人属主 403；
- 批次查询：非敏感、不泄漏口令。

用例以"预置既有连接→提交→断言零写入"的反向鉴别结构编写（规约 R-12）。
"""
import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from backend.api import zk_discovery as zk_api
from backend.main import app
from backend.services.database import _get_connection
from backend.services.zk_connection_import_service import (
    ImportCredentials,
    MonitorCredentials,
)

client = TestClient(app)

UAT_TAG = "zk_commit_regression"


def _row(database="biz_db_a", name=None, status="ready"):
    return {
        "source_instance_id": f"set_{UAT_TAG}", "instance_kind": "noshard",
        "instance_type": "centralized", "primary_proxy": "127.0.0.1:25002",
        "primary_proxy_host": "127.0.0.1", "primary_proxy_port": 25002,
        "set_ids": [f"set_{UAT_TAG}"], "resolved_instance_name": f"回归实例_{UAT_TAG}",
        "name_source": "instance", "database": database,
        "generated_connection_name": name or f"回归实例_{UAT_TAG}-25002-{database}",
        "status": status, "failure_code": "", "failure_detail": "",
        "monitor_host": "mon.example", "monitor_port": 15001,
        "monitor_user": "mon_user", "monitor_db": "tdsqlpcloud_monitor",
    }


def _seed_preview(rows):
    """绕过真实 ZK/预检，直接向服务端塞一个属主为 anonymous 的预览会话。"""
    discovery_id = uuid.uuid4().hex
    preview_id, visible = zk_api._store_preview(
        discovery_id, "anonymous", rows,
        ImportCredentials("biz_user", "biz-secret"),
        MonitorCredentials("mon.example", 15001, "mon_user", "mon-secret", "tdsqlpcloud_monitor"))
    return discovery_id, preview_id, visible


def _commit(discovery_id, preview_id, tokens):
    return client.post("/api/v1/tdsql/discover/import-commit", json={
        "discovery_id": discovery_id, "preview_id": preview_id, "row_tokens": tokens})


def _batch_ids_by_tag():
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT DISTINCT batch_id FROM zk_discovery_import_items WHERE source_instance_id=?",
            (f"set_{UAT_TAG}",)).fetchall()
        failed = conn.execute(
            "SELECT id FROM zk_discovery_import_batches WHERE failure_summary LIKE ?",
            (f"%set_{UAT_TAG}%",)).fetchall()
        return [r["batch_id"] for r in rows] + [r["id"] for r in failed]
    finally:
        conn.close()


def _cleanup():
    conn = _get_connection()
    try:
        batch_ids = _batch_ids_by_tag()
        if batch_ids:
            placeholders = ",".join(["?"] * len(batch_ids))
            conn.execute(f"DELETE FROM zk_discovery_import_items WHERE batch_id IN ({placeholders})", tuple(batch_ids))
            conn.execute(f"DELETE FROM zk_discovery_import_batches WHERE id IN ({placeholders})", tuple(batch_ids))
            conn.execute(f"DELETE FROM tdsql_connections WHERE zk_import_batch_id IN ({placeholders})", tuple(batch_ids))
        conn.execute("DELETE FROM tdsql_connections WHERE name LIKE ?", (f"回归实例_{UAT_TAG}%",))
        conn.execute("DELETE FROM tdsql_connections WHERE name LIKE ?", (f"preexisting_{UAT_TAG}%",))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _clean():
    _cleanup()
    with zk_api._sessions_lock:
        zk_api._previews.clear()
    yield
    _cleanup()


def test_commit_happy_path_encrypts_and_audits():
    """ZI-06/ZI-08：一库一连接、命名契约、口令加密、审计表零敏感数据。"""
    discovery_id, preview_id, visible = _seed_preview([_row("biz_db_a"), _row("biz_db_b")])
    resp = _commit(discovery_id, preview_id, [r["row_token"] for r in visible])
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["created_count"] == 2
    batch_id = data["batch_id"]

    conn = _get_connection()
    try:
        conns = conn.execute(
            "SELECT name, `database`, username, password_encrypted, is_distributed, set_list, "
            "zk_instance_kind, zk_instance_id, zk_import_batch_id, monitor_host, monitor_port, "
            "monitor_user, monitor_db, monitor_password_encrypted "
            "FROM tdsql_connections WHERE zk_import_batch_id=?", (batch_id,)).fetchall()
        assert len(conns) == 2, "一库一连接未达成"
        names = sorted(c["name"] for c in conns)
        assert names == [f"回归实例_{UAT_TAG}-25002-biz_db_a", f"回归实例_{UAT_TAG}-25002-biz_db_b"]
        for c in conns:
            assert c["username"] == "biz_user"
            assert c["password_encrypted"] and "biz-secret" not in c["password_encrypted"], "业务口令未加密"
            assert c["monitor_password_encrypted"] and "mon-secret" not in c["monitor_password_encrypted"]
            assert c["set_list"] == f"set_{UAT_TAG}"
            assert c["zk_instance_kind"] == "noshard" and c["is_distributed"] == 0
        batch = conn.execute("SELECT * FROM zk_discovery_import_batches WHERE id=?", (batch_id,)).fetchone()
        assert batch["status"] == "completed"
        assert batch["created_count"] == 2
        # P4 修复后 candidate_count = 预览候选总数（此处恰为选中数）
        assert batch["candidate_count"] == 2
        items = conn.execute("SELECT * FROM zk_discovery_import_items WHERE batch_id=?", (batch_id,)).fetchall()
        assert len(items) == 2
        blob = json.dumps({k: str(v) for i in items for k, v in i.items()}
                          | {k: str(v) for k, v in batch.items()})
        for secret in ("biz-secret", "mon-secret",
                       conns[0]["password_encrypted"], conns[0]["monitor_password_encrypted"]):
            assert secret not in blob, "审计表泄漏口令或密文"
    finally:
        conn.close()


def test_commit_candidate_count_is_preview_total():
    """P4：预览 2 行只提交 1 行时，candidate_count 仍为预览全量。"""
    discovery_id, preview_id, visible = _seed_preview([_row("biz_db_a"), _row("biz_db_b")])
    resp = _commit(discovery_id, preview_id, [visible[0]["row_token"]])
    assert resp.status_code == 200, resp.text
    batch_id = resp.json()["batch_id"]
    conn = _get_connection()
    try:
        batch = conn.execute("SELECT * FROM zk_discovery_import_batches WHERE id=?", (batch_id,)).fetchone()
        assert batch["candidate_count"] == 2, "candidate_count 必须是预览候选总数而非选中数"
        assert batch["created_count"] == 1
    finally:
        conn.close()


def test_preview_is_single_use():
    """ZI-12：提交成功后预览即销毁，重放必须 410。"""
    discovery_id, preview_id, visible = _seed_preview([_row()])
    tokens = [r["row_token"] for r in visible]
    assert _commit(discovery_id, preview_id, tokens).status_code == 200
    replay = _commit(discovery_id, preview_id, tokens)
    assert replay.status_code == 410, f"预览重放未被拦截: {replay.status_code} {replay.text}"


def test_commit_conflict_by_endpoint_rolls_back_and_records_failed_batch():
    """ZI-10/ZI-11：host:port:database 冲突 → 409 零写入；P4：失败批次留痕。"""
    conn = _get_connection()
    try:
        conn.execute(
            "INSERT INTO tdsql_connections (id, name, host, port, username, password_encrypted, "
            "`database`, charset, is_default, is_distributed, created_at) "
            "VALUES (?, ?, '127.0.0.1', 25002, 'old_user', 'x', 'biz_db_a', 'utf8mb4', 0, 0, NOW())",
            (uuid.uuid4().hex, f"preexisting_{UAT_TAG}"))
        conn.commit()
    finally:
        conn.close()

    discovery_id, preview_id, visible = _seed_preview([_row("biz_db_a"), _row("biz_db_b")])
    resp = _commit(discovery_id, preview_id, [r["row_token"] for r in visible])
    assert resp.status_code == 409, f"冲突未返回409: {resp.status_code} {resp.text}"

    conn = _get_connection()
    try:
        left = conn.execute(
            "SELECT COUNT(*) AS c FROM tdsql_connections WHERE zk_import_batch_id IS NOT NULL AND name LIKE ?",
            (f"回归实例_{UAT_TAG}%",)).fetchone()["c"]
        assert left == 0, f"冲突回滚失败，残留连接 {left} 条（违反 ZI-11 原子性）"
        failed = conn.execute(
            "SELECT status, failure_summary, created_count FROM zk_discovery_import_batches "
            "WHERE failure_summary LIKE ?", (f"%set_{UAT_TAG}%",)).fetchall()
        assert len(failed) == 1, "P4：冲突后必须登记 status='failed' 批次"
        assert failed[0]["status"] == "failed"
        assert "IMPORT_CONFLICT" in failed[0]["failure_summary"]
        assert failed[0]["created_count"] == 0
        assert "biz-secret" not in failed[0]["failure_summary"]
    finally:
        conn.close()


def test_commit_conflict_by_name_rolls_back():
    """ZI-10：同名连接冲突同样整体回滚。"""
    conn = _get_connection()
    try:
        conn.execute(
            "INSERT INTO tdsql_connections (id, name, host, port, username, password_encrypted, "
            "`database`, charset, is_default, is_distributed, created_at) "
            "VALUES (?, ?, '10.9.9.9', 3306, 'other', 'x', 'other_db', 'utf8mb4', 0, 0, NOW())",
            (uuid.uuid4().hex, f"回归实例_{UAT_TAG}-25002-biz_db_a"))
        conn.commit()
    finally:
        conn.close()
    discovery_id, preview_id, visible = _seed_preview([_row("biz_db_a")])
    resp = _commit(discovery_id, preview_id, [r["row_token"] for r in visible])
    assert resp.status_code == 409, resp.text
    conn = _get_connection()
    try:
        left = conn.execute(
            "SELECT COUNT(*) AS c FROM tdsql_connections WHERE name LIKE ? AND zk_import_batch_id IS NOT NULL",
            (f"回归实例_{UAT_TAG}%",)).fetchone()["c"]
        assert left == 0
    finally:
        conn.close()


def test_commit_rejects_non_ready_rows():
    """选中行含非 ready 状态必须整体拒绝并留失败审计。"""
    rows = [_row("biz_db_a"), _row("biz_db_b", status="conflict")]
    discovery_id, preview_id, visible = _seed_preview(rows)
    resp = _commit(discovery_id, preview_id, [r["row_token"] for r in visible])
    assert resp.status_code == 409, f"含非ready行未拒绝: {resp.status_code} {resp.text}"
    conn = _get_connection()
    try:
        failed = conn.execute(
            "SELECT failure_summary FROM zk_discovery_import_batches WHERE failure_summary LIKE ?",
            (f"%set_{UAT_TAG}%",)).fetchall()
        assert failed and "ROW_NOT_READY" in failed[0]["failure_summary"]
    finally:
        conn.close()


def test_expired_preview_is_rejected():
    """ZI-12：过期预览必须 410。"""
    discovery_id, preview_id, visible = _seed_preview([_row()])
    with zk_api._sessions_lock:
        zk_api._previews[preview_id]["expires_at"] = time.monotonic() - 1
    resp = _commit(discovery_id, preview_id, [r["row_token"] for r in visible])
    assert resp.status_code == 410, resp.text


def test_foreign_owner_preview_is_rejected():
    """ZI-12：他人属主的预览必须 403。"""
    discovery_id, preview_id, visible = _seed_preview([_row()])
    with zk_api._sessions_lock:
        zk_api._previews[preview_id]["owner"] = "someone_else"
    resp = _commit(discovery_id, preview_id, [r["row_token"] for r in visible])
    assert resp.status_code == 403, resp.text


def test_import_batches_query_returns_audit_without_secrets():
    """ZI-12：批次查询接口返回审计明细且不含任何口令。"""
    discovery_id, preview_id, visible = _seed_preview([_row()])
    resp = _commit(discovery_id, preview_id, [r["row_token"] for r in visible])
    assert resp.status_code == 200, resp.text
    batch_id = resp.json()["batch_id"]
    q = client.get(f"/api/v1/tdsql/discover/import-batches/{batch_id}")
    assert q.status_code == 200, q.text
    assert "biz-secret" not in q.text and "mon-secret" not in q.text, "批次查询泄漏口令"
    assert q.json()["batch"]["created_count"] == 1


def test_concurrent_commit_same_candidate_exactly_one_wins():
    """P2-01（A 质检）：两位操作者并发提交同一候选 → 恰好一个成功、一个 409，库中恰一条。

    事务内预检 SELECT 在 REPEATABLE READ 下是非锁定读，挡不住并发；
    本用例验证唯一约束 uq_conn_name/uq_conn_endpoint 兜底 + IntegrityError
    归一为 IMPORT_CONFLICT 的完整链路（规约 R-12 反向鉴别）。
    """
    import threading

    rows = [_row("biz_db_conc")]
    d1, p1, v1 = _seed_preview(rows)
    d2, p2, v2 = _seed_preview(rows)
    results = {}
    barrier = threading.Barrier(2)

    def do(key, discovery_id, preview_id, tokens):
        local_client = TestClient(app)
        barrier.wait()
        results[key] = local_client.post("/api/v1/tdsql/discover/import-commit", json={
            "discovery_id": discovery_id, "preview_id": preview_id, "row_tokens": tokens})

    t1 = threading.Thread(target=do, args=("a", d1, p1, [r["row_token"] for r in v1]))
    t2 = threading.Thread(target=do, args=("b", d2, p2, [r["row_token"] for r in v2]))
    t1.start(); t2.start(); t1.join(); t2.join()

    codes = sorted(r.status_code for r in results.values())
    assert codes == [200, 409], f"并发提交应恰好一成功一冲突，实际: {codes}"
    loser = next(r for r in results.values() if r.status_code == 409)
    assert "既有连接" in str(loser.json().get("detail", "")), "冲突响应必须归一到 IMPORT_CONFLICT 语义"

    conn = _get_connection()
    try:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM tdsql_connections WHERE name=? AND zk_import_batch_id IS NOT NULL",
            (f"回归实例_{UAT_TAG}-25002-biz_db_conc",)).fetchone()["c"]
        assert n == 1, f"库中应恰好一条连接，实际 {n} 条（唯一约束失效）"
    finally:
        conn.close()


def test_manual_duplicate_connection_rejected():
    """P2-01：手工路径同样受唯一约束保护——直接插同名/同端点记录必须被数据库拒绝。"""
    conn = _get_connection()
    try:
        base = (uuid.uuid4().hex, f"手工重复_{UAT_TAG}", "10.8.8.8", 3306, "u", "x", "dbx")
        conn.execute(
            "INSERT INTO tdsql_connections (id, name, host, port, username, password_encrypted, "
            "`database`, charset, is_default, is_distributed, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'utf8mb4', 0, 0, NOW())", base)
        conn.commit()
        import pymysql as _pymysql
        dup_name_ok = False
        try:
            conn.execute(
                "INSERT INTO tdsql_connections (id, name, host, port, username, password_encrypted, "
                "`database`, charset, is_default, is_distributed, created_at) "
                "VALUES (?, ?, '10.9.9.9', 3307, 'u', 'x', 'other_db', 'utf8mb4', 0, 0, NOW())",
                (uuid.uuid4().hex, f"手工重复_{UAT_TAG}"))
            conn.commit()
            dup_name_ok = True
        except _pymysql.err.IntegrityError:
            conn.rollback()
        assert not dup_name_ok, "同名连接未被唯一约束拦截"
        dup_endpoint_ok = False
        try:
            conn.execute(
                "INSERT INTO tdsql_connections (id, name, host, port, username, password_encrypted, "
                "`database`, charset, is_default, is_distributed, created_at) "
                "VALUES (?, ?, '10.8.8.8', 3306, 'u', 'x', 'dbx', 'utf8mb4', 0, 0, NOW())",
                (uuid.uuid4().hex, f"手工重复2_{UAT_TAG}"))
            conn.commit()
            dup_endpoint_ok = True
        except _pymysql.err.IntegrityError:
            conn.rollback()
        assert not dup_endpoint_ok, "同端点连接未被唯一约束拦截"
    finally:
        conn.execute("DELETE FROM tdsql_connections WHERE name LIKE ?", (f"%{UAT_TAG}%",))
        conn.commit()
        conn.close()
