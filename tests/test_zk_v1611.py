# -*- coding: utf-8 -*-
"""v1.6.1.1 用例（设计 DESIGN-v1.6.1.1）：默认库屏蔽扩展 + 实例管理前端结构守卫。

- query_rewrite / xa 在富集与预检两路径均被排除（同 sysdb 口径）；
- 实例管理页：分页组件、三维筛选（连接名模糊/地址/类型）、
  换行治理（地址 260 / 操作 480 / WARNING 120 + td-nowrap/th-nowrap）。
"""
from pathlib import Path

import pymysql

from backend.services.zk_connection_import_service import (
    ImportCredentials, MonitorCredentials, zk_connection_import_service)
from backend.services.zk_scan_enrich_service import _list_business_databases

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


def test_enrich_excludes_query_rewrite_and_xa(monkeypatch):
    """富集路径排除 query_rewrite / xa（实例默认库，同 sysdb 口径）。"""
    monkeypatch.setattr(pymysql, "connect",
                        lambda *a, **k: _FakeConn(["biz_a", "query_rewrite", "xa", "sysdb"]))
    dbs, source = _list_business_databases([("10.0.0.1", 15001)], "checksql", "p", "m")
    assert dbs == ["biz_a"]
    assert source == "proxy_show"


def _instance():
    return {
        "instance_id": "set_v1611", "instance_kind": "noshard",
        "instance_type": "centralized", "host": "10.0.0.1", "port": 15001,
        "proxy_list": "10.0.0.1:15001", "set_ids": ["set_v1611"], "is_mock": False,
    }


def test_preview_excludes_query_rewrite_and_xa(monkeypatch):
    """预检路径同样排除 query_rewrite / xa。"""
    monkeypatch.setattr(pymysql, "connect",
                        lambda *a, **k: _FakeConn(["biz_a", "query_rewrite", "xa"]))
    rows = zk_connection_import_service.build_preview(
        [_instance()], ImportCredentials("checksql", "p"),
        MonitorCredentials("mon", 15001, "mu", "mp", "mdb"),
        name_overrides={"set_v1611": "回归实例"})
    assert [r["database"] for r in rows if r["status"] == "ready"] == ["biz_a"]


def test_instance_management_pagination_filters_and_nowrap_guard():
    """前端结构守卫：分页 + 三维筛选 + 换行治理齐备。"""
    html = (_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    js = (_ROOT / "frontend" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    css = (_ROOT / "frontend" / "static" / "css" / "app.css").read_text(encoding="utf-8")
    # 分页：表格绑分页切片 + el-pagination 存在
    assert ':data="pagedConnections"' in html
    assert ":total=\"filteredConnections.length\"" in html
    assert "el-pagination" in html and "connPageSize" in html
    # 三维筛选：连接名模糊 / 地址 / 类型
    assert "connFilters.name" in html and "connFilters.address" in html and "connFilters.type" in html
    assert "连接名（模糊）" in html
    assert "filteredConnections" in js and "pagedConnections" in js and "onConnFilterChange" in js
    # 换行治理：列宽与 nowrap 类
    assert 'label="地址" width="260" class-name="td-nowrap"' in html
    assert 'label="操作" width="480" fixed="right" class-name="td-nowrap"' in html
    assert 'label="WARNING上限" width="120" label-class-name="th-nowrap"' in html
    assert ".td-nowrap .cell{white-space:nowrap;}" in css
    assert ".th-nowrap .cell{white-space:nowrap;}" in css
