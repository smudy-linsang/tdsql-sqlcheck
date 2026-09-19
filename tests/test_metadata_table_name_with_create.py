# -*- coding: utf-8 -*-
"""回归锁：防止表名/视图名自身包含 "create" 词根时元数据提取被截断为裸表名。

复现样本来自内网 sealdb 集中式库实测：
- 27号表: seal_custcreateoperdetailx
- 33号表: seal_his_custcreateoperdetailx
在 v1.6.3.5~1.6.3.7 中因 values() 循环内模糊匹配 `"CREATE" in s.upper()`，
误把第一列的表名当成了建表 DDL，导致后续审核判定为 UNKNOWN 并报 E999。
"""

import pytest
from backend.services.metadata_audit_pipeline import _show_create, extract_metadata, MetadataExtractError


class _MockCursor:
    def __init__(self, result_row):
        self._result_row = result_row
        self.last_sql = ""

    def execute(self, sql, params=None):
        self.last_sql = sql

    def fetchone(self):
        return self._result_row


class _MockConn:
    def __init__(self, result_row):
        self._row = result_row

    def cursor(self):
        return _MockCursor(self._row)


def test_show_create_table_with_create_in_name_dict():
    """字典游标：表名含 'create' 时必须正确提取 'Create Table'，不能误取表名。"""
    real_ddl = (
        "CREATE TABLE `seal_custcreateoperdetailx` (\n"
        "  `dbserno` bigint NOT NULL AUTO_INCREMENT COMMENT '序号',\n"
        "  PRIMARY KEY (`dbserno`)\n"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='印鉴建库明细'"
    )
    # PyMySQL DictCursor 返回的顺序为 Table (vals[0]) 和 Create Table (vals[1])
    row = {
        "Table": "seal_custcreateoperdetailx",
        "Create Table": real_ddl,
    }
    conn = _MockConn(row)
    ddl = _show_create(conn, "sealdb", {"name": "seal_custcreateoperdetailx", "type": "BASE TABLE"})
    assert ddl == real_ddl
    assert ddl.startswith("CREATE TABLE")
    assert "dbserno" in ddl


def test_show_create_his_table_with_create_in_name_dict():
    """字典游标：历史表 seal_his_custcreateoperdetailx 提取验证。"""
    real_ddl = (
        "CREATE TABLE `seal_his_custcreateoperdetailx` (\n"
        "  `dbserno` bigint NOT NULL AUTO_INCREMENT COMMENT '序号',\n"
        "  PRIMARY KEY (`dbserno`)\n"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='历史印鉴建库明细'"
    )
    row = {
        "Table": "seal_his_custcreateoperdetailx",
        "Create Table": real_ddl,
    }
    conn = _MockConn(row)
    ddl = _show_create(conn, "sealdb", {"name": "seal_his_custcreateoperdetailx", "type": "BASE TABLE"})
    assert ddl == real_ddl
    assert ddl.startswith("CREATE TABLE")


def test_show_create_table_with_create_in_name_tuple():
    """元组/列表游标：表名含 'create' 时协议第二列是 DDL，必须正确提取。"""
    real_ddl = "CREATE TABLE `t_create_order` (`id` int) ENGINE=InnoDB"
    row = ("t_create_order", real_ddl)
    conn = _MockConn(row)
    ddl = _show_create(conn, "testdb", {"name": "t_create_order", "type": "BASE TABLE"})
    assert ddl == real_ddl


def test_show_create_view_with_create_in_name_dict():
    """视图名含 'create' 时必须正确提取 'Create View'。"""
    real_ddl = "CREATE ALGORITHM=UNDEFINED VIEW `v_create_summary` AS select 1 AS `one`"
    row = {
        "View": "v_create_summary",
        "Create View": real_ddl,
        "character_set_client": "utf8mb4",
        "collation_connection": "utf8mb4_general_ci",
    }
    conn = _MockConn(row)
    ddl = _show_create(conn, "testdb", {"name": "v_create_summary", "type": "VIEW"})
    assert ddl == real_ddl
    assert ddl.startswith("CREATE")


def test_show_create_empty_or_failed_raises():
    """空结果抛出 MetadataExtractError。"""
    conn = _MockConn(None)
    with pytest.raises(MetadataExtractError) as exc_info:
        _show_create(conn, "testdb", {"name": "t_missing", "type": "BASE TABLE"})
    assert exc_info.value.code == "EXTRACT_OBJECT_FAILED"
