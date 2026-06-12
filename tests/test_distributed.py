"""
TDSQL SQL审核工具 - 分布式规则与慢SQL分析测试
"""
import pytest

from backend.engine.checker import RuleChecker
from backend.engine.rules.distributed import (
    R020ShardKeyInWhere,
    R021ShardKeyUpdate,
    R022GlobalDeleteWithoutShardKey,
)
from backend.engine.slow_analyzer import (
    SlowQueryRecord,
    SlowSQLAnalyzer,
)


@pytest.fixture
def checker():
    return RuleChecker()


@pytest.fixture
def analyzer():
    return SlowSQLAnalyzer()


# ============ 分布式规则测试 ============

class TestDistributedRules:
    """R020-R022 分布式规范规则测试"""

    def test_r021_shardkey_update(self):
        """R021: 禁止更新分片键字段"""
        rule = R021ShardKeyUpdate()
        from backend.engine.parser import SQLParser
        parser = SQLParser()

        # 检测到 shardkey 在 SET 子句中
        parsed = parser.parse("UPDATE t_order SET shard_key = 100 WHERE id = 1")
        v = rule.check(parsed)
        assert v is not None
        assert "R021" == v.rule_id

        # 正常的 UPDATE 不触发
        parsed = parser.parse("UPDATE t_order SET status = 1 WHERE id = 1")
        v = rule.check(parsed)
        assert v is None

    def test_r022_global_delete_without_shardkey(self):
        """R022: 禁止不带分片键的全局DELETE"""
        rule = R022GlobalDeleteWithoutShardKey()
        from backend.engine.parser import SQLParser
        parser = SQLParser()

        # 没有等值条件的 DELETE 触发
        parsed = parser.parse("DELETE FROM t_order WHERE status != 1")
        v = rule.check(parsed)
        assert v is not None
        assert "R022" == v.rule_id

        # 有等值条件的 DELETE 不触发
        parsed = parser.parse("DELETE FROM t_order WHERE id = 123")
        v = rule.check(parsed)
        assert v is None

        # 有 LIMIT 的 DELETE 不触发
        parsed = parser.parse("DELETE FROM t_order WHERE status != 1 LIMIT 100")
        v = rule.check(parsed)
        assert v is None

    def test_r020_multitable_join_warning(self):
        """R020: 多表JOIN时提醒分片键"""
        rule = R020ShardKeyInWhere()
        from backend.engine.parser import SQLParser
        parser = SQLParser()

        # 多表 JOIN 应该提醒
        parsed = parser.parse(
            "SELECT * FROM t_order o JOIN t_user u ON o.user_id = u.id WHERE u.name = 'test'"
        )
        v = rule.check(parsed)
        assert v is not None
        assert "R020" == v.rule_id

    def test_distributed_rules_in_checker(self, checker):
        """集成测试：分布式规则已加载"""
        # 检查规则列表中包含分布式规则
        rule_ids = {r.rule_id for r in checker.rules}
        assert "R020" in rule_ids
        assert "R021" in rule_ids
        assert "R022" in rule_ids

    def test_r021_in_checker(self, checker):
        """集成测试：R021 通过 checker 检测"""
        result = checker.audit_sql("UPDATE t_order SET shard_key = 100 WHERE id = 1")
        rule_ids = {v.rule_id for v in result.violations}
        assert "R021" in rule_ids


# ============ 慢SQL分析器测试 ============

class TestSlowSQLAnalyzer:
    """慢SQL分析引擎测试"""

    def test_analyze_explain_all_scan(self, analyzer):
        """测试EXPLAIN全表扫描分析"""
        explain_data = [
            {
                "id": 1,
                "select_type": "SIMPLE",
                "table": "t_order",
                "type": "ALL",
                "possible_keys": "NULL",
                "key": "NULL",
                "rows": 850000,
                "filtered": 10.0,
                "extra": "Using where",
            }
        ]
        report = analyzer.analyze_explain(explain_data)
        assert len(report.analyses) > 0
        # 应该检测到全表扫描
        problem_types = [a.problem_type for a in report.analyses]
        assert "全表扫描" in problem_types or "缺失索引" in problem_types

    def test_analyze_explain_good_plan(self, analyzer):
        """测试EXPLAIN良好执行计划"""
        explain_data = [
            {
                "id": 1,
                "select_type": "SIMPLE",
                "table": "t_order",
                "type": "ref",
                "possible_keys": "idx_user_id",
                "key": "idx_user_id",
                "rows": 5,
                "filtered": 100.0,
                "extra": "Using index",
            }
        ]
        report = analyzer.analyze_explain(explain_data)
        # 良好的执行计划应该没有严重问题
        errors = [a for a in report.analyses if a.severity == "ERROR"]
        assert len(errors) == 0

    def test_analyze_explain_filesort(self, analyzer):
        """测试EXPLAIN filesort检测"""
        explain_data = [
            {
                "id": 1,
                "select_type": "SIMPLE",
                "table": "t_order",
                "type": "ALL",
                "rows": 100000,
                "filtered": 100.0,
                "extra": "Using where; Using filesort",
            }
        ]
        report = analyzer.analyze_explain(explain_data)
        problem_types = [a.problem_type for a in report.analyses]
        assert "Using filesort" in problem_types

    def test_analyze_slow_query_high_freq(self, analyzer):
        """测试高频慢SQL分析"""
        record = SlowQueryRecord(
            fingerprint="SELECT * FROM t_order WHERE user_id = ?",
            sql_text="SELECT * FROM t_order WHERE user_id = 123",
            db_name="order_db",
            exec_count=5000,
            avg_time_ms=200,
            max_time_ms=1500,
            rows_examined=850000,
            rows_sent=100,
            lock_time_ms=50,
        )
        report = analyzer.analyze_slow_query(record)
        assert len(report.analyses) > 0
        # 应该检测到高频慢SQL和索引问题
        problem_types = [a.problem_type for a in report.analyses]
        assert "高频慢SQL" in problem_types

    def test_analyze_slow_query_select_star(self, analyzer):
        """测试SELECT *检测"""
        record = SlowQueryRecord(
            fingerprint="SELECT * FROM t_user WHERE id = ?",
            sql_text="SELECT * FROM t_user WHERE id = 1",
            exec_count=10,
            avg_time_ms=50,
            rows_examined=1,
            rows_sent=1,
        )
        report = analyzer.analyze_slow_query(record)
        problem_types = [a.problem_type for a in report.analyses]
        assert "SELECT *" in problem_types

    def test_analyze_slow_query_like_prefix(self, analyzer):
        """测试左模糊查询检测"""
        record = SlowQueryRecord(
            fingerprint="SELECT id FROM t_user WHERE name LIKE '%test'",
            sql_text="SELECT id FROM t_user WHERE name LIKE '%test'",
            exec_count=10,
            avg_time_ms=50,
        )
        report = analyzer.analyze_slow_query(record)
        problem_types = [a.problem_type for a in report.analyses]
        assert "左模糊查询" in problem_types

    def test_analyze_slow_query_deep_pagination(self, analyzer):
        """测试深度分页检测"""
        record = SlowQueryRecord(
            fingerprint="SELECT id FROM t_order ORDER BY id LIMIT ?, ?",
            sql_text="SELECT id FROM t_order ORDER BY id LIMIT 500000, 20",
            exec_count=100,
            avg_time_ms=300,
        )
        report = analyzer.analyze_slow_query(record)
        problem_types = [a.problem_type for a in report.analyses]
        assert "深度分页" in problem_types

    def test_analyze_slow_query_scan_ratio(self, analyzer):
        """测试扫描/返回行数比检测"""
        record = SlowQueryRecord(
            fingerprint="SELECT id FROM t_order WHERE status = ?",
            sql_text="SELECT id FROM t_order WHERE status = 1",
            exec_count=100,
            avg_time_ms=100,
            rows_examined=500000,
            rows_sent=10,
        )
        report = analyzer.analyze_slow_query(record)
        problem_types = [a.problem_type for a in report.analyses]
        assert "索引使用不充分" in problem_types

    def test_analyze_no_issues(self, analyzer):
        """测试无问题的SQL"""
        record = SlowQueryRecord(
            fingerprint="SELECT id FROM t_user WHERE id = ?",
            sql_text="SELECT id FROM t_user WHERE id = 1",
            exec_count=1,
            avg_time_ms=1,
            rows_examined=1,
            rows_sent=1,
        )
        report = analyzer.analyze_slow_query(record)
        # 应该没有严重问题
        errors = [a for a in report.analyses if a.severity == "ERROR"]
        assert len(errors) == 0
