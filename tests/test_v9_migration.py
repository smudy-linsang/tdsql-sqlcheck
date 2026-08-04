# -*- coding: utf-8 -*-
"""A-P1-01 修复：v9 迁移去重判据反向鉴别用例（R-12）。

覆盖智能体 A 复测要求的三条：
1. 老库存在同名不同端点的两条连接 → 去重后**两条都必须还在**；
2. 老库存在同名同端点同库的两条连接 → 合并为一条；
3. 被删连接若有 instance_gate_rules → 必须迁移到保留方或显式处置，不得静默 CASCADE。
"""
import uuid

from backend.services.database import (
    _dedup_connection_endpoints,
    _drop_index_if_exists,
    _get_connection,
)

TAG = "v9mig"


def _insert(conn, name, host, port, db, cid=None):
    cid = cid or uuid.uuid4().hex
    conn.execute(
        "INSERT INTO tdsql_connections (id, name, host, port, username, password_encrypted, "
        "`database`, charset, is_default, is_distributed, created_at) "
        "VALUES (?, ?, ?, ?, 'u', 'x', ?, 'utf8mb4', 0, 0, NOW())",
        (cid, name, host, port, db))
    conn.commit()
    return cid


def _cleanup(conn):
    # 丢弃旧 v9 误建的 uq_conn_name（同名≠重复）；不丢 uq_conn_endpoint（留给后续用例/生产）。
    _drop_index_if_exists(conn, "tdsql_connections", "uq_conn_name")
    conn.execute("DELETE FROM tdsql_connections WHERE name LIKE ?", (f"%{TAG}%",))
    conn.execute("DELETE FROM instance_gate_rules WHERE connection_id NOT IN (SELECT id FROM tdsql_connections)")
    conn.commit()


def test_same_name_different_endpoint_both_survive():
    """同名不同端点 = 两个不同实例，去重后两条都必须在。"""
    conn = _get_connection()
    try:
        _cleanup(conn)
        _insert(conn, f"核心库_{TAG}", "10.1.1.1", 15001, "tdsql_a")
        _insert(conn, f"核心库_{TAG}", "10.2.2.2", 15002, "tdsql_b")
        _dedup_connection_endpoints(conn)
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM tdsql_connections WHERE name=?", (f"核心库_{TAG}",)).fetchone()["c"]
        assert n == 2, f"同名不同端点应两条都在，实际 {n}"
    finally:
        _cleanup(conn)
        conn.close()


def test_same_endpoint_dedup_to_one():
    """同 host:port:database 真重复 → 合并为一条。"""
    conn = _get_connection()
    try:
        _cleanup(conn)
        _drop_index_if_exists(conn, "tdsql_connections", "uq_conn_endpoint")  # 临时允许插入真重复
        _insert(conn, f"重复A_{TAG}", "10.3.3.3", 15003, "tdsql_c")
        _insert(conn, f"重复B_{TAG}", "10.3.3.3", 15003, "tdsql_c")
        _dedup_connection_endpoints(conn)
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM tdsql_connections WHERE host='10.3.3.3' AND port=15003 "
            "AND `database`='tdsql_c'").fetchone()["c"]
        assert n == 1, f"同端点同库应合并为一条，实际 {n}"
    finally:
        _cleanup(conn)
        conn.close()


def test_dedup_handles_gate_rules_not_silent_cascade():
    """真重复去重后：门禁规则总数不丢（迁移到保留方），不得静默 CASCADE 丢失。"""
    conn = _get_connection()
    try:
        _cleanup(conn)
        _drop_index_if_exists(conn, "tdsql_connections", "uq_conn_endpoint")  # 临时允许插入真重复
        a = _insert(conn, f"门禁A_{TAG}", "10.4.4.4", 15004, "tdsql_d")
        b = _insert(conn, f"门禁B_{TAG}", "10.4.4.4", 15004, "tdsql_d")
        # 给其中一条挂门禁规则
        conn.execute(
            "INSERT INTO instance_gate_rules (connection_id, max_error_count, max_warning_count, mode) "
            "VALUES (?, 0, -1, 'enforce')", (b,))
        conn.commit()
        _dedup_connection_endpoints(conn)
        # 真重复只保留一条
        remain = conn.execute(
            "SELECT id FROM tdsql_connections WHERE host='10.4.4.4' AND port=15004 "
            "AND `database`='tdsql_d'").fetchall()
        assert len(remain) == 1, f"同端点同库应保留一条，实际 {len(remain)}"
        survivor = remain[0]["id"]
        # 门禁规则必须还在且挂在保留方上（不得静默 CASCADE 丢失）
        gate = conn.execute(
            "SELECT COUNT(*) AS c FROM instance_gate_rules WHERE connection_id=?", (survivor,)).fetchone()["c"]
        assert gate >= 1, "被删连接的门禁规则应迁移到保留方，不得静默 CASCADE"
    finally:
        _cleanup(conn)
        conn.close()
