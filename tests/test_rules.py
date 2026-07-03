"""
TDSQL SQL审核工具 - 规则引擎单元测试
"""
import pytest

from backend.engine.checker import RuleChecker
from backend.models import Severity


@pytest.fixture
def checker():
    return RuleChecker(dialect="mysql")


class TestNamingRules:
    """命名规范规则测试 (R001-R002)"""

    def test_r001_valid_name(self, checker):
        """R001: 合法表名通过"""
        result = checker.audit_sql("SELECT id FROM t_user WHERE id = 1")
        r001_violations = [v for v in result.violations if v.rule_id == "R001"]
        assert len(r001_violations) == 0

    def test_r001_invalid_name_uppercase(self, checker):
        """R001: 大写表名不通过"""
        result = checker.audit_sql("SELECT id FROM T_USER WHERE id = 1")
        r001_violations = [v for v in result.violations if v.rule_id == "R001"]
        assert len(r001_violations) > 0

    def test_r001_invalid_name_starts_with_number(self, checker):
        """R001: 数字开头表名不通过"""
        result = checker.audit_sql("SELECT id FROM 1_user WHERE id = 1")
        r001_violations = [v for v in result.violations if v.rule_id == "R001"]
        assert len(r001_violations) > 0

    def test_r002_reserved_keyword(self, checker):
        """R002: 关键字表名不通过"""
        result = checker.audit_sql("SELECT id FROM `order` WHERE id = 1")
        r002_violations = [v for v in result.violations if v.rule_id == "R002"]
        assert len(r002_violations) > 0

    def test_r002_valid_name(self, checker):
        """R002: 非关键字表名通过"""
        result = checker.audit_sql("SELECT id FROM t_order WHERE id = 1")
        r002_violations = [v for v in result.violations if v.rule_id == "R002"]
        assert len(r002_violations) == 0


class TestDDLRules:
    """DDL规范规则测试 (R003-R011)"""

    def test_r003_primary_key_exists(self, checker):
        """R003: 有主键通过"""
        sql = "CREATE TABLE t_test (id INT PRIMARY KEY, name VARCHAR(32)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        result = checker.audit_sql(sql)
        r003 = [v for v in result.violations if v.rule_id == "R003"]
        assert len(r003) == 0

    def test_r003_no_primary_key(self, checker):
        """R003: 无主键不通过"""
        sql = "CREATE TABLE t_test (id INT, name VARCHAR(32)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        result = checker.audit_sql(sql)
        r003 = [v for v in result.violations if v.rule_id == "R003"]
        assert len(r003) > 0

    def test_r004_engine_innodb(self, checker):
        """R004: InnoDB 引擎通过"""
        sql = "CREATE TABLE t_test (id INT PRIMARY KEY) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        result = checker.audit_sql(sql)
        r004 = [v for v in result.violations if v.rule_id == "R004"]
        assert len(r004) == 0

    def test_r004_no_engine(self, checker):
        """R004: 未指定引擎不通过"""
        sql = "CREATE TABLE t_test (id INT PRIMARY KEY) DEFAULT CHARSET=utf8mb4"
        result = checker.audit_sql(sql)
        r004 = [v for v in result.violations if v.rule_id == "R004"]
        assert len(r004) > 0

    def test_r005_charset_utf8mb4(self, checker):
        """R005: utf8mb4 字符集通过"""
        sql = "CREATE TABLE t_test (id INT PRIMARY KEY) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        result = checker.audit_sql(sql)
        r005 = [v for v in result.violations if v.rule_id == "R005"]
        assert len(r005) == 0

    def test_r005_charset_utf8(self, checker):
        """R005: utf8 字符集不通过"""
        sql = "CREATE TABLE t_test (id INT PRIMARY KEY) ENGINE=InnoDB DEFAULT CHARSET=utf8"
        result = checker.audit_sql(sql)
        r005 = [v for v in result.violations if v.rule_id == "R005"]
        assert len(r005) > 0

    def test_r006_enum_type(self, checker):
        """R006: ENUM 类型不通过"""
        sql = "CREATE TABLE t_test (id INT PRIMARY KEY, status ENUM('0','1')) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        result = checker.audit_sql(sql)
        r006 = [v for v in result.violations if v.rule_id == "R006"]
        assert len(r006) > 0

    def test_r007_timestamp_type(self, checker):
        """R007: TIMESTAMP 类型不通过"""
        sql = "CREATE TABLE t_test (id INT PRIMARY KEY, create_time TIMESTAMP) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        result = checker.audit_sql(sql)
        r007 = [v for v in result.violations if v.rule_id == "R007"]
        assert len(r007) > 0

    def test_r008_no_foreign_key(self, checker):
        """R008: 无外键通过"""
        sql = "CREATE TABLE t_test (id INT PRIMARY KEY) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        result = checker.audit_sql(sql)
        r008 = [v for v in result.violations if v.rule_id == "R008"]
        assert len(r008) == 0

    def test_r009_finance_float(self, checker):
        """R009: 财务字段使用 FLOAT 不通过"""
        sql = "CREATE TABLE t_test (id INT PRIMARY KEY, amount FLOAT) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        result = checker.audit_sql(sql)
        r009 = [v for v in result.violations if v.rule_id == "R009"]
        assert len(r009) > 0

    def test_r010_varchar_too_long(self, checker):
        """R010: VARCHAR 超长不通过"""
        sql = "CREATE TABLE t_test (id INT PRIMARY KEY, name VARCHAR(5000)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        result = checker.audit_sql(sql)
        r010 = [v for v in result.violations if v.rule_id == "R010"]
        assert len(r010) > 0

    def test_r011_text_type(self, checker):
        """R011: TEXT 类型不通过"""
        sql = "CREATE TABLE t_test (id INT PRIMARY KEY, content TEXT) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        result = checker.audit_sql(sql)
        r011 = [v for v in result.violations if v.rule_id == "R011"]
        assert len(r011) > 0


class TestDMLRules:
    """DML规范规则测试 (R012-R019)"""

    def test_r012_select_star(self, checker):
        """R012: SELECT * 不通过"""
        result = checker.audit_sql("SELECT * FROM t_user WHERE id = 1")
        r012 = [v for v in result.violations if v.rule_id == "R012"]
        assert len(r012) > 0

    def test_r012_select_fields(self, checker):
        """R012: 指定字段通过"""
        result = checker.audit_sql("SELECT id, name FROM t_user WHERE id = 1")
        r012 = [v for v in result.violations if v.rule_id == "R012"]
        assert len(r012) == 0

    def test_r013_update_without_where(self, checker):
        """R013: 不带 WHERE 的 UPDATE 不通过"""
        result = checker.audit_sql("UPDATE t_user SET name = 'test'")
        r013 = [v for v in result.violations if v.rule_id == "R013"]
        assert len(r013) > 0

    def test_r013_update_with_where(self, checker):
        """R013: 带 WHERE 的 UPDATE 通过"""
        result = checker.audit_sql("UPDATE t_user SET name = 'test' WHERE id = 1")
        r013 = [v for v in result.violations if v.rule_id == "R013"]
        assert len(r013) == 0

    def test_r013_delete_without_where(self, checker):
        """R013: 不带 WHERE 的 DELETE 不通过"""
        result = checker.audit_sql("DELETE FROM t_user")
        r013 = [v for v in result.violations if v.rule_id == "R013"]
        assert len(r013) > 0

    def test_r015_subquery_depth(self, checker):
        """R015: 嵌套子查询超过3层不通过（4层嵌套）"""
        sql = """
        SELECT * FROM t1 WHERE id IN (
            SELECT id FROM t2 WHERE id IN (
                SELECT id FROM t3 WHERE id IN (
                    SELECT id FROM t4 WHERE id IN (
                        SELECT id FROM t5 WHERE id = 1
                    )
                )
            )
        )
        """
        result = checker.audit_sql(sql)
        r015 = [v for v in result.violations if v.rule_id == "R015"]
        assert len(r015) > 0

    def test_r016_function_in_where(self, checker):
        """R016: WHERE 中使用函数不通过"""
        result = checker.audit_sql("SELECT * FROM t_order WHERE DATE(create_time) = '2024-01-01'")
        r016 = [v for v in result.violations if v.rule_id == "R016"]
        assert len(r016) > 0

    def test_r017_order_by_rand(self, checker):
        """R017: ORDER BY RAND() 不通过"""
        result = checker.audit_sql("SELECT * FROM t_user ORDER BY RAND() LIMIT 10")
        r017 = [v for v in result.violations if v.rule_id == "R017"]
        assert len(r017) > 0


class TestCheckerIntegration:
    """规则检查器集成测试"""

    def test_clean_ddl_passes(self, checker):
        """合规的 DDL 应该通过所有检查

        注: R077 要求 TDSQL 分布式建表必须声明分片键(SHARDKEY)或广播表(BROADCAST)，
        且分片键必须是主键/唯一索引字段，因此合规 DDL 包含 SHARDKEY=id（id 为主键）。
        """
        sql = """
        CREATE TABLE t_order (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
            order_no VARCHAR(64) NOT NULL COMMENT '订单号',
            user_id BIGINT UNSIGNED NOT NULL COMMENT '用户ID',
            amount DECIMAL(18,2) NOT NULL DEFAULT 0 COMMENT '金额',
            status TINYINT NOT NULL DEFAULT 0 COMMENT '状态',
            create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
            update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间'
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='订单表' SHARDKEY=id
        """
        result = checker.audit_sql(sql)
        assert result.passed is True, f"violations: {[(v.rule_id, v.message) for v in result.violations]}"
        assert result.sql_type == "CREATE TABLE"

    def test_bad_select_fails(self, checker):
        """不合规的 SELECT 应该失败"""
        result = checker.audit_sql("SELECT * FROM t_user ORDER BY RAND() LIMIT 10")
        assert result.passed is False
        # 应该触发 R012 (SELECT *) 和 R017 (ORDER BY RAND)
        rule_ids = {v.rule_id for v in result.violations}
        assert "R012" in rule_ids
        assert "R017" in rule_ids

    def test_dangerous_update_fails(self, checker):
        """危险的 UPDATE 应该失败"""
        result = checker.audit_sql("UPDATE t_user SET status = 0")
        assert result.passed is False

    def test_summary_computation(self, checker):
        """测试汇总计算"""
        results = [
            checker.audit_sql("SELECT id FROM t_user WHERE id = 1"),
            checker.audit_sql("SELECT * FROM t_user"),
            checker.audit_sql("DELETE FROM t_user"),
        ]
        summary = checker.compute_summary(results)
        assert summary.total_sql == 3
        assert summary.passed == 1  # 第一条通过
        assert summary.failed == 2
