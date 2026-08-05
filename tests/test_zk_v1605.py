# -*- coding: utf-8 -*-
"""v1.6.0.5 三问题修复回归用例。

Fix1 业务库枚举 ≥1 成功（形态无关）；
Fix2 会话/预览改存元数据库（worker 无关、凭据加密、删预览防重放）。
Fix3 为前端行为，见 test_zk_frontend_v1605 结构守卫。
"""
import json

import pymysql
import pytest

from backend.services import zk_discovery_session_store as store
from backend.services.zk_connection_import_service import (
    ImportCredentials, MonitorCredentials)
from backend.services.zk_scan_enrich_service import _list_business_databases


class _FakeCursor:
    def __init__(self, dbs):
        self._dbs = dbs

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        pass

    def fetchall(self):
        return [{"Database": d} for d in self._dbs]


class _FakeConn:
    def __init__(self, dbs):
        self._dbs = dbs

    def cursor(self):
        return _FakeCursor(self._dbs)

    def close(self):
        pass


def test_list_business_databases_one_proxy_down_still_succeeds(monkeypatch):
    """Fix1：双 Proxy 其一不可达，另一成功 → 仍返回业务库（不整实例失败）。

    v1.6.0.6（A-P2-01）：备 Proxy 挂掉属于降级，source 必须标
    proxy_show_partial——"用一个 Proxy 的目录代表整个实例"用户有权知道。
    """
    def fake_connect(host, port, **kw):
        if host == "10.0.0.2":
            raise pymysql.err.OperationalError(2003, "Can't connect")
        return _FakeConn(["cap_gz", "sysdb"])
    monkeypatch.setattr(pymysql, "connect", fake_connect)
    dbs, source = _list_business_databases(
        [("10.0.0.1", 15001), ("10.0.0.2", 15001)], "u", "p", "tdsqlpcloud_monitor")
    assert dbs == ["cap_gz", "sysdb"]
    assert source == "proxy_show_partial"


def test_list_business_databases_all_down_no_fake_data(monkeypatch):
    """Fix1：全部 Proxy 不可达 → NO_AVAILABLE_PROXY，不假数据。"""
    def fake_connect(host, port, **kw):
        raise pymysql.err.OperationalError(2003, "Can't connect")
    monkeypatch.setattr(pymysql, "connect", fake_connect)
    dbs, source = _list_business_databases([("10.0.0.1", 1), ("10.0.0.2", 1)], "u", "p", "m")
    assert dbs == []
    assert source == "NO_AVAILABLE_PROXY"


def test_list_business_databases_inconsistent_union_partial(monkeypatch):
    """Fix1：多 Proxy 成功但库不一致 → 取并集 + proxy_show_partial。"""
    def fake_connect(host, port, **kw):
        return _FakeConn(["a", "b"] if host == "10.0.0.1" else ["b", "c"])
    monkeypatch.setattr(pymysql, "connect", fake_connect)
    dbs, source = _list_business_databases([("10.0.0.1", 1), ("10.0.0.2", 1)], "u", "p", "m")
    assert dbs == ["a", "b", "c"]
    assert source == "proxy_show_partial"


def test_session_store_roundtrip_and_preview_encrypt():
    """Fix2：会话/预览入库可跨读；business/monitor 口令加密存储；删预览后 410。"""
    results = [{
        "instance_id": "set_x", "instance_kind": "noshard", "instance_type": "centralized",
        "host": "10.0.0.1", "port": 15001, "proxy_list": "10.0.0.1:15001",
        "set_ids": ["set_x"], "user": "zk_internal", "password": "zk_secret", "database": "ALL",
        "is_mock": False,
    }]
    discovery_id, visible = store.store_session(results, "ownerA")
    assert visible and "password" not in visible[0] and "user" not in visible[0]
    items = store.load_session_items(discovery_id, [visible[0]["item_token"]], "ownerA")
    assert items[0]["instance_id"] == "set_x"
    # 口令不入库明文
    conn = store._get_connection()
    try:
        row = conn.execute("SELECT items_json FROM zk_discovery_sessions WHERE discovery_id=%s",
                           (discovery_id,)).fetchone()
        assert "zk_secret" not in (row["items_json"] or "")
    finally:
        conn.close()

    business = ImportCredentials("biz", "biz_secret")
    monitor = MonitorCredentials("10.0.0.9", 15001, "mon", "mon_secret", "mdb")
    preview_id, vrows = store.store_preview(discovery_id, "ownerA", [
        {"source_instance_id": "set_x", "database": "cap_gz", "status": "ready"}], business, monitor)
    preview, selected = store.load_preview(preview_id, discovery_id,
                                           [vrows[0]["row_token"]], "ownerA")
    assert preview["business"].password == "biz_secret"
    assert preview["monitor"].password == "mon_secret"
    assert selected[0]["database"] == "cap_gz"
    # 密文入库、非明文
    conn = store._get_connection()
    try:
        row = conn.execute("SELECT business_enc, monitor_enc FROM zk_discovery_previews WHERE preview_id=%s",
                           (preview_id,)).fetchone()
        assert "biz_secret" not in (row["business_enc"] or "")
        assert "mon_secret" not in (row["monitor_enc"] or "")
    finally:
        conn.close()
    # 删预览后重放 → 410
    store.delete_preview(preview_id)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        store.load_preview(preview_id, discovery_id, [vrows[0]["row_token"]], "ownerA")
    assert ei.value.status_code == 410


def test_session_owner_isolation():
    """Fix2：其他操作者读取会话 → 403。"""
    results = [{"instance_id": "set_y", "instance_kind": "noshard", "host": "10.0.0.1",
                "port": 1, "proxy_list": "", "set_ids": [], "is_mock": False}]
    discovery_id, visible = store.store_session(results, "ownerA")
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        store.load_session_items(discovery_id, [visible[0]["item_token"]], "ownerB")
    assert ei.value.status_code == 403


def test_frontend_v1605_close_clears_selection_binding():
    """Fix3 结构守卫：发现表有 ref、导入弹窗 @close 绑定 closeZkImport、setup 暴露两者。"""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    html = (root / "frontend" / "index.html").read_text(encoding="utf-8")
    js = (root / "frontend" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert 'ref="zkDiscoveryTableRef"' in html
    assert '@close="closeZkImport"' in html
    assert "const closeZkImport=" in js
    assert "zkDiscoveryTableRef,closeZkImport," in js or "closeZkImport," in js
    assert "clearSelection" in js
