"""
TDSQL SQL审核工具 - SQL解析器单元测试
"""
import pytest

from backend.engine.parser import SQLParser


@pytest.fixture
def parser():
    return SQLParser(dialect="mysql")


class TestSQLParser:
    """SQL解析器测试"""

    def test_parse_select_star(self, parser):
        """测试解析 SELECT *"""
        result = parser.parse("SELECT * FROM t_user WHERE id = 1")
        assert result.sql_type == "SELECT"
        assert result.has_wildcard_select is True
        assert "t_user" in result.tables
        assert result.has_where is True

    def test_parse_select_fields(self, parser):
        """测试解析带字段列表的 SELECT"""
        result = parser.parse("SELECT id, name, email FROM t_user WHERE status = 1")
        assert result.sql_type == "SELECT"
        assert result.has_wildcard_select is False
        assert len(result.select_fields) == 3

    def test_parse_create_table(self, parser):
        """测试解析 CREATE TABLE"""
        sql = """
        CREATE TABLE t_order (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            order_no VARCHAR(64) NOT NULL,
            amount DECIMAL(18,2) NOT NULL DEFAULT 0,
            status TINYINT NOT NULL DEFAULT 0,
            create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
        result = parser.parse(sql)
        assert result.is_create_table is True
        assert result.has_primary_key is True
        assert result.engine == "INNODB"
        assert result.charset == "UTF8MB4"
        assert len(result.columns) == 5

    def test_parse_update_with_where(self, parser):
        """测试解析带 WHERE 的 UPDATE"""
        result = parser.parse("UPDATE t_user SET name = 'test' WHERE id = 1")
        assert result.sql_type == "UPDATE"
        assert result.has_where is True

    def test_parse_update_without_where(self, parser):
        """测试解析不带 WHERE 的 UPDATE"""
        result = parser.parse("UPDATE t_user SET name = 'test'")
        assert result.sql_type == "UPDATE"
        assert result.has_where is False

    def test_parse_delete_without_where(self, parser):
        """测试解析不带 WHERE 的 DELETE"""
        result = parser.parse("DELETE FROM t_user")
        assert result.sql_type == "DELETE"
        assert result.has_where is False

    def test_parse_order_by_rand(self, parser):
        """测试解析 ORDER BY RAND()"""
        result = parser.parse("SELECT * FROM t_user ORDER BY RAND() LIMIT 10")
        assert result.order_by_random is True

    def test_parse_subquery(self, parser):
        """测试解析子查询"""
        sql = "SELECT * FROM t_user WHERE id IN (SELECT user_id FROM t_order WHERE status = 1)"
        result = parser.parse(sql)
        assert result.subquery_depth >= 1

    def test_parse_join(self, parser):
        """测试解析 JOIN"""
        sql = "SELECT a.id, b.name FROM t_user a JOIN t_order b ON a.id = b.user_id"
        result = parser.parse(sql)
        assert result.join_count >= 1
        assert len(result.tables) >= 2

    def test_parse_create_enum_type(self, parser):
        """测试解析 ENUM 类型字段"""
        sql = "CREATE TABLE t_test (id INT, status ENUM('0','1') NOT NULL) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        result = parser.parse(sql)
        assert result.is_create_table is True
        enum_found = any("ENUM" in col.get("type", "").upper() or "ENUM" in col.get("raw_type", "").upper()
                         for col in result.column_types)
        assert enum_found

    def test_parse_create_timestamp_type(self, parser):
        """测试解析 TIMESTAMP 类型字段"""
        sql = "CREATE TABLE t_test (id INT, create_time TIMESTAMP) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        result = parser.parse(sql)
        ts_found = any("TIMESTAMP" in col.get("type", "").upper() for col in result.column_types)
        assert ts_found

    def test_parse_create_text_type(self, parser):
        """测试解析 TEXT 类型字段"""
        sql = "CREATE TABLE t_test (id INT, content TEXT) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        result = parser.parse(sql)
        text_found = any("TEXT" in col.get("type", "").upper() for col in result.column_types)
        assert text_found

    def test_parse_where_with_function(self, parser):
        """测试 WHERE 中包含函数"""
        sql = "SELECT * FROM t_order WHERE DATE(create_time) = '2024-01-01'"
        result = parser.parse(sql)
        assert result.where_has_function is True
