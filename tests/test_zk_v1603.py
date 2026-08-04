# -*- coding: utf-8 -*-
"""V1.6.0.3 ZK 发现内网适配与导入体验回归用例（设计 DESIGN-v1.6.0.3 §10.1 ZE-01~08）。"""
import json

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services import zk_discovery_service as zds
from backend.services.zk_connection_import_service import (
    ImportCredentials, MonitorCredentials, zk_connection_import_service)
from backend.services.zk_name_resolution_service import zk_name_resolution_service
from backend.services.zk_scan_enrich_service import enrich_discovered_items
from backend.services.database import _get_connection

client = TestClient(app)


# ── 伪造 MonitorDB 连接（按 SQL 特征返回预设行）──────────────
class _FakeCursor:
    def __init__(self, owner):
        self._owner = owner
        self._last = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self._last = (sql or "", params or ())

    def fetchall(self):
        sql, params = self._last
        return self._owner.route(sql, params)


class _FakeConn:
    def __init__(self, rows_l1=None, rows_l2=None, rows_l3=None, tables=None):
        self.rows_l1 = rows_l1 or []
        self.rows_l2 = rows_l2 or []
        self.rows_l3 = rows_l3 or []
        self.tables = tables or []

    def cursor(self):
        return _FakeCursor(self)

    def close(self):
        pass

    def route(self, sql, params):
        if "f_type = 1" in sql:
            return self.rows_l1
        if "f_key IN" in sql:
            return self.rows_l2
        if "LIMIT 200" in sql:
            return self.rows_l3
        if sql.startswith("SHOW TABLES"):
            return [{"t": t} for t in self.tables]
        if "information_schema.COLUMNS" in sql:
            return [{"COLUMN_NAME": "instance_name"}, {"COLUMN_NAME": "f_mid"}]
        if "SELECT `instance_name`" in sql:
            return [{"nm": "元数据表名称"}]
        return []


def test_ze01_octet_rules_transform_and_exact_map_override():
    """ZE-01：段替换作用于 host 与 proxy_list；精确映射可覆盖个别主机。"""
    results = [{
        "host": "10.243.21.13", "port": 15001,
        "proxy_list": "10.243.21.13:15001;10.243.21.14:15001",
    }]
    out = zds.ZKDiscoveryService.apply_endpoint_mapping(
        results, {"10.243.20.14": "192.168.9.9"},
        octet_rules=[{"segment": 3, "from": "21", "to": "20"}])
    item = out[0]
    assert item["host"] == "10.243.20.13"
    assert item["original_host"] == "10.243.21.13"
    assert item["proxy_list"] == "10.243.20.13:15001;192.168.9.9:15001"


def test_ze02_name_chain_l2_fuzzy_mid():
    """ZE-02：精确 mid 落空、模糊 mid 命中（f_type 不限）。"""
    conn = _FakeConn(
        rows_l1=[],  # L1 无
        rows_l2=[{"f_mid": "/tdsqlzk/sets/set@x", "f_key": "instance_name", "f_val": "核心系统"}])
    name, source, detail = zk_name_resolution_service.resolve(conn, "set_x", ["set_x"], "noshard")
    assert name == "核心系统" and source == "monitor_like"


def test_ze03_name_chain_l3_and_l4():
    """ZE-03：L3 值像名称 / L4 元数据表探针。"""
    conn3 = _FakeConn(rows_l3=[{"f_mid": "m", "f_key": "whatever", "f_val": "业务实例A"}])
    name, source, _ = zk_name_resolution_service.resolve(conn3, "set_y", ["set_y"], "noshard")
    assert name == "业务实例A" and source == "monitor_value"
    conn4 = _FakeConn(tables=["t_instance_info"])
    name, source, _ = zk_name_resolution_service.resolve(conn4, "set_z", ["set_z"], "noshard")
    assert name == "元数据表名称" and source == "meta_table"


def test_ze04_manual_fallback_import():
    """ZE-04：全链落空时手工命名+手工库可导入，来源留痕 manual。"""
    instances = [{
        "instance_id": "set_manual_1", "instance_kind": "noshard", "instance_type": "centralized",
        "host": "127.0.0.1", "port": 3999, "proxy_list": "127.0.0.1:3999", "set_ids": ["set_manual_1"]}]
    rows = zk_connection_import_service.build_preview(
        instances, ImportCredentials("u", "p"),
        MonitorCredentials("", 0, "", "", ""),  # 无 MonitorDB → 名称必须手工
        name_overrides={"set_manual_1": "手工实例"},
        manual_databases={"set_manual_1": ["db_a", "db_b"]})
    assert len(rows) == 2
    assert all(r["status"] == "ready" for r in rows)
    assert rows[0]["resolved_instance_name"] == "手工实例"
    assert rows[0]["name_source"] == "manual" and rows[0]["databases_source"] == "manual"
    assert rows[0]["generated_connection_name"] == "手工实例-3999-db_a"


def test_ze05_enrich_failure_not_blocking():
    """ZE-05：业务库枚举失败不阻断扫描，状态入 enrich_status。"""
    results = [{
        "instance_id": "set_e1", "instance_kind": "noshard", "instance_type": "centralized",
        "host": "192.0.2.1", "port": 3306, "proxy_list": "192.0.2.1:3306",
        "set_ids": ["set_e1"], "zk_name_fields": {}}]
    enrich_discovered_items(
        results, monitor=None,
        business={"username": "u", "password": "p"})
    item = results[0]
    assert item["enrich_status"].startswith("dbs_failed:")
    assert item["business_dbs"] == []


def test_ze06_frontend_pagination_and_filters_present():
    """ZE-06：扫描/预览列表具备分页与五维筛选绑定（结构守卫）。"""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    html = (root / "frontend" / "index.html").read_text(encoding="utf-8")
    js = (root / "frontend" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    for token in ("zkScanFilter.name", "zkScanFilter.db", "zkScanFilter.host",
                  "zkScanFilter.port", "zkScanFilter.kind", "zkPagedDiscovered",
                  "zkPagedPreviewRows", "el-pagination"):
        assert token in html or token in js, f"缺少分页/筛选绑定: {token}"
    assert "octet_rules" in js and "地址段替换" in html


def test_ze07_config_redacts_monitor_business_passwords():
    """ZE-07：GET config 不含 MonitorDB/业务口令明文或密文。"""
    resp = client.put("/api/v1/tdsql/discover/config", json={
        "servers": "127.0.0.1:2118", "auth_username": "u", "auth_password": "Auth#12345",
        "monitor_host": "127.0.0.1", "monitor_port": 15001, "monitor_user": "mu",
        "monitor_password": "Mon#12345", "monitor_db": "mdb",
        "business_username": "bu", "business_password": "Biz#12345"})
    assert resp.status_code == 200, resp.text
    got = client.get("/api/v1/tdsql/discover/config")
    assert got.status_code == 200
    body = got.text
    for secret in ("Mon#12345", "Biz#12345", "Auth#12345"):
        assert secret not in body
    data = got.json()
    assert data["monitor_password_configured"] and data["business_password_configured"]
    # 清理，避免影响其他用例
    conn = _get_connection()
    try:
        conn.execute("DELETE FROM zk_discovery_config WHERE config_id = 1")
        conn.commit()
    finally:
        conn.close()


def test_ze08_name_diagnose_endpoint_shape():
    """ZE-08：name-diagnose 返回诊断结构且不含口令（无 MonitorDB 时优雅降级）。"""
    headers = {}
    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "Abcd1234"})
    if login.status_code == 200 and login.json().get("token"):
        headers = {"Authorization": f"Bearer {login.json()['token']}"}
    resp = client.post("/api/v1/tdsql/discover/name-diagnose",
                       json={"instance_ids": ["set_x"]}, headers=headers)
    if resp.status_code == 401:
        pytest.skip("环境强制认证且无可用测试口令")
    # 未配置 MonitorDB → 503 或空诊断均可，但不得 5xx 崩溃且不得含口令
    assert resp.status_code in (200, 503)
    assert "password" not in resp.text.lower() or "password_configured" in resp.text
