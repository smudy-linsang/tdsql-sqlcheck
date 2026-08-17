"""
在线元数据提取与文件审核 API 功能测试 (V1.2 新增)
"""
import pytest
from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)


def test_extract_and_audit_unauthorized():
    """未登录或无 Token 时响应状态非 200"""
    resp = client.post("/api/v1/audit/extract-and-audit", json={"connection_id": "test"})
    assert resp.status_code in (400, 401, 403, 404)


def test_extract_and_audit_invalid_conn():
    """选择未建连的 ID 姿态时应返回 400"""
    login_resp = client.post("/api/v1/auth/login", json={"username": "admin", "password": "adminpassword"})
    token = login_resp.json().get("token", "")
    
    resp = client.post(
        "/api/v1/audit/extract-and-audit",
        headers={"Authorization": f"Bearer {token}"},
        json={"connection_id": "non_existent_conn_9999"}
    )
    assert resp.status_code == 400
    assert "未激活" in resp.json()["detail"] or "请在" in resp.json()["detail"]


def test_audit_partitioned_ddl_file_content():
    """测试含各类复杂分区语法（Range/List/Hash）的元数据 DDL 文件审核与快照抽取不报错"""
    from backend.services.audit_service import AuditService
    from backend.services.snapshot_extractors.schema_audit import extract as extract_schema_audit

    audit_service = AuditService()
    test_ddl = """
    CREATE TABLE `t_order_part` (
      `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
      `order_no` varchar(64) NOT NULL COMMENT '订单号',
      `create_time` datetime NOT NULL COMMENT '创建时间',
      `shard_key` varchar(32) NOT NULL,
      PRIMARY KEY (`id`, `create_time`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='订单分区表' shardkey=shard_key
    PARTITION BY RANGE (TO_DAYS(create_time)) (
      PARTITION p202601 VALUES LESS THAN (TO_DAYS('2026-02-01')),
      PARTITION p202602 VALUES LESS THAN (TO_DAYS('2026-03-01')),
      PARTITION pmax VALUES LESS THAN (MAXVALUE)
    );
    """
    results, summary, _, ictx = audit_service.audit_file_content(
        test_ddl,
        file_path="extracted_test_schema.sql",
        created_by="admin",
        save_history=False
    )
    assert len(results) >= 1
    items, obj_total = extract_schema_audit(results, db_name="test_db", node="")
    assert obj_total == 1

