# -*- coding: utf-8 -*-
"""v1.6.1.0 用例（设计 DESIGN-v1.6.0.8）：枚举失败语义细化 + sysdb 屏蔽。

- 全端点 1045 → NO_BUSINESS_USER（富集与预检两路径）；
- 混合 1045+2003 → NO_AVAILABLE_PROXY（分类不越界，反向鉴别）；
- sysdb 在富集与预检两路径均被排除；手工库填 sysdb 保留（反向鉴别）；
- 前端"未创建监控用户"短标签与 zkFailureLabel 映射结构守卫。
"""
import pymysql

from backend.services.zk_connection_import_service import (
    ImportCredentials, MonitorCredentials, zk_connection_import_service)
from backend.services.zk_scan_enrich_service import _list_business_databases

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


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


def test_all_1045_maps_to_no_business_user_enrich(monkeypatch):
    """全端点鉴权失败（1045）→ 富集来源 NO_BUSINESS_USER，页面可提示未创建监控用户。"""
    def fake_connect(host, port, **kw):
        raise pymysql.err.OperationalError(1045, "Access denied")
    monkeypatch.setattr(pymysql, "connect", fake_connect)
    dbs, source = _list_business_databases([("10.0.0.1", 15001), ("10.0.0.2", 15001)],
                                           "checksql", "p", "m")
    assert dbs == []
    assert source == "NO_BUSINESS_USER"


def test_mixed_1045_and_2003_stays_no_available_proxy(monkeypatch):
    """混合失败（鉴权+网络）→ 仍 NO_AVAILABLE_PROXY，分类不越界（反向鉴别）。"""
    def fake_connect(host, port, **kw):
        if host == "10.0.0.1":
            raise pymysql.err.OperationalError(1045, "Access denied")
        raise pymysql.err.OperationalError(2003, "Can't connect")
    monkeypatch.setattr(pymysql, "connect", fake_connect)
    dbs, source = _list_business_databases([("10.0.0.1", 15001), ("10.0.0.2", 15001)],
                                           "checksql", "p", "m")
    assert dbs == []
    assert source == "NO_AVAILABLE_PROXY"


def _instance():
    return {
        "instance_id": "set_v1610", "instance_kind": "noshard",
        "instance_type": "centralized", "host": "10.0.0.1", "port": 15001,
        "proxy_list": "10.0.0.1:15001;10.0.0.2:15001", "set_ids": ["set_v1610"],
        "is_mock": False,
    }


def test_preview_all_1045_failure_code_no_business_user(monkeypatch):
    """预检路径：全 1045 → 逐行 NO_BUSINESS_USER（errno 沿 _connect 包装层 __cause__ 追溯）。"""
    def fake_connect(host, port, **kw):
        raise pymysql.err.OperationalError(1045, "Access denied")
    monkeypatch.setattr(pymysql, "connect", fake_connect)
    rows = zk_connection_import_service.build_preview(
        [_instance()], ImportCredentials("checksql", "p"),
        MonitorCredentials("mon", 15001, "mu", "mp", "mdb"),
        name_overrides={"set_v1610": "回归实例"})
    assert len(rows) == 1 and rows[0]["status"] == "error"
    assert rows[0]["failure_code"] == "NO_BUSINESS_USER"
    assert "p" not in rows[0]["failure_detail"] or "password" not in rows[0]["failure_detail"]


def test_preview_excludes_sysdb_but_manual_keeps_it(monkeypatch):
    """sysdb 预检排除；手工库显式填 sysdb 则保留（反向鉴别）。"""
    monkeypatch.setattr(pymysql, "connect", lambda *a, **k: _FakeConn(["biz_a", "sysdb"]))
    rows = zk_connection_import_service.build_preview(
        [_instance()], ImportCredentials("checksql", "p"),
        MonitorCredentials("mon", 15001, "mu", "mp", "mdb"),
        name_overrides={"set_v1610": "回归实例"})
    assert [r["database"] for r in rows if r["status"] == "ready"] == ["biz_a"]
    # 手工库路径不过滤（显式意图）
    rows2 = zk_connection_import_service.build_preview(
        [_instance()], ImportCredentials("checksql", "p"),
        MonitorCredentials("mon", 15001, "mu", "mp", "mdb"),
        name_overrides={"set_v1610": "回归实例"},
        manual_databases={"set_v1610": ["sysdb"]})
    assert [r["database"] for r in rows2 if r["status"] == "ready"] == ["sysdb"]


def test_enrich_excludes_sysdb(monkeypatch):
    """富集路径同样排除 sysdb。"""
    monkeypatch.setattr(pymysql, "connect", lambda *a, **k: _FakeConn(["biz_a", "sysdb", "mysql"]))
    dbs, source = _list_business_databases([("10.0.0.1", 15001)], "checksql", "p", "m")
    assert dbs == ["biz_a"]
    assert source == "proxy_show"


def test_frontend_labels_for_no_business_user():
    """前端结构守卫：扫描列表短标签 + 预览状态列映射 + tooltip 文案齐备。"""
    html = (_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    js = (_ROOT / "frontend" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert "未创建监控用户" in html, "扫描列表业务库列缺短标签"
    assert "dbs_failed:NO_BUSINESS_USER" in html
    assert "zkFailureLabel" in html and "zkFailureLabel" in js
    assert "NO_BUSINESS_USER:'未创建监控用户'" in js
    assert "通常未创建监控用户" in html, "tooltip 处置建议缺失"
