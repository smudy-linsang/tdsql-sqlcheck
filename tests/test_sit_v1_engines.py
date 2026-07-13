"""
TDSQL SQL审核工具 V1.0 - SIT测试 第二部分：核心引擎深度测试

覆盖8个V1.0核心引擎的功能边界、异常输入、业务场景。
"""
import pytest

from backend.engine.fingerprint import FingerprintEngine
from backend.engine.index_advisor import IndexAdvisor
from backend.engine.sql_rewriter import SQLRewriter
from backend.engine.charset_diagnoser import CharsetDiagnoser
from backend.engine.distributed_explain import DistributedExplainAnalyzer
from backend.engine.deadlock_analyzer import DeadlockAnalyzer
from backend.engine.long_transaction import LongTransactionAnalyzer
from backend.engine.bigtable_engine import BigTableClassifier, BigTableEngine, PartitionMonitor, PartitionAdvisor


# ═══════════════════════════════════════════════════════════
# 一、SQL指纹引擎
# ═══════════════════════════════════════════════════════════

class TestFingerprintEngine:
    """SQL指纹引擎深度测试"""

    def setup_method(self):
        self.engine = FingerprintEngine()

    def test_string_normalization(self):
        """字符串字面量归一化"""
        fp1 = self.engine.fingerprint("SELECT * FROM t WHERE name = 'abc'")
        fp2 = self.engine.fingerprint("SELECT * FROM t WHERE name = 'xyz'")
        assert fp1 == fp2

    def test_number_normalization(self):
        """数字字面量归一化"""
        fp1 = self.engine.fingerprint("SELECT * FROM t WHERE id = 1")
        fp2 = self.engine.fingerprint("SELECT * FROM t WHERE id = 999")
        assert fp1 == fp2

    def test_in_list_normalization(self):
        """IN列表归一化"""
        fp1 = self.engine.fingerprint("SELECT * FROM t WHERE id IN (1, 2, 3)")
        fp2 = self.engine.fingerprint("SELECT * FROM t WHERE id IN (10, 20, 30, 40)")
        assert fp1 == fp2

    def test_values_normalization(self):
        """VALUES列表归一化"""
        fp1 = self.engine.fingerprint("INSERT INTO t VALUES (1, 'a')")
        fp2 = self.engine.fingerprint("INSERT INTO t VALUES (2, 'b')")
        assert fp1 == fp2

    def test_different_sql_different_fingerprint(self):
        """不同SQL结构产生不同指纹"""
        fp1 = self.engine.fingerprint("SELECT * FROM t_user WHERE id = 1")
        fp2 = self.engine.fingerprint("SELECT * FROM t_order WHERE id = 1")
        assert fp1 != fp2

    def test_empty_sql(self):
        """空SQL处理"""
        assert self.engine.fingerprint("") == ""
        assert self.engine.fingerprint(None) == ""

    def test_fingerprint_hash_consistency(self):
        """相同SQL指纹哈希一致"""
        sql = "SELECT * FROM t WHERE id = 1"
        h1 = self.engine.fingerprint_hash(sql)
        h2 = self.engine.fingerprint_hash(sql)
        assert h1 == h2
        assert len(h1) == 16

    def test_extract_tables(self):
        """表名提取"""
        tables = self.engine.extract_tables_from_sql(
            "SELECT * FROM t_order o JOIN t_user u ON o.uid = u.id"
        )
        assert "t_order" in tables
        assert "t_user" in tables

    def test_normalize_for_display(self):
        """展示用归一化保留可读性"""
        normalized = self.engine.normalize_for_display(
            "SELECT * FROM t_user WHERE id = 1 AND name = 'abc'"
        )
        assert "?" in normalized
        assert "t_user" in normalized


# ═══════════════════════════════════════════════════════════
# 二、索引顾问引擎
# ═══════════════════════════════════════════════════════════

class TestIndexAdvisor:
    """索引顾问引擎深度测试"""

    def setup_method(self):
        self.advisor = IndexAdvisor()

    def test_single_column_index_recommendation(self):
        """单列索引推荐"""
        sql = "SELECT * FROM t_order WHERE cust_id = 100"
        recs = self.advisor.advise_from_sql(sql)
        assert len(recs) >= 1
        assert recs[0].type == "single"
        assert "cust_id" in recs[0].columns

    def test_composite_index_recommendation(self):
        """复合索引推荐（等值在前）"""
        sql = "SELECT * FROM t_order WHERE status = 1 AND create_time > '2024-01-01'"
        recs = self.advisor.advise_from_sql(sql)
        assert len(recs) >= 1
        rec = recs[0]
        assert rec.type == "composite"
        # 等值条件status应在前面
        assert "status" in rec.columns
        assert rec.columns.index("status") < rec.columns.index("create_time")

    def test_no_recommendation_for_non_select(self):
        """非SELECT语句不推荐索引"""
        sql = "UPDATE t_order SET status = 1 WHERE id = 1"
        recs = self.advisor.advise_from_sql(sql)
        assert len(recs) == 0

    def test_no_recommendation_without_where(self):
        """无WHERE条件不推荐索引"""
        sql = "SELECT * FROM t_order"
        recs = self.advisor.advise_from_sql(sql)
        assert len(recs) == 0

    def test_explain_full_scan_detection(self):
        """EXPLAIN全表扫描检测"""
        explain_rows = [
            {"table": "t_big", "type": "ALL", "rows": 100000, "key": "", "possible_keys": ""},
        ]
        recs = self.advisor.advise_from_explain(explain_rows)
        assert len(recs) >= 1
        assert recs[0].type == "missing"

    def test_explain_unused_index_detection(self):
        """EXPLAIN有可用索引但未使用"""
        explain_rows = [
            {"table": "t_test", "type": "ALL", "rows": 5000, "key": "", "possible_keys": "idx_name"},
        ]
        recs = self.advisor.advise_from_explain(explain_rows)
        assert any(r.type == "unused" for r in recs)

    def test_redundant_index_detection(self):
        """冗余索引检测"""
        indexes = [
            {"name": "idx_a", "columns": ["col_a"]},
            {"name": "idx_ab", "columns": ["col_a", "col_b"]},
            {"name": "idx_abc", "columns": ["col_a", "col_b", "col_c"]},
        ]
        recs = self.advisor.detect_redundant_indexes(indexes)
        assert len(recs) >= 1
        assert all(r.type == "redundant" for r in recs)

    def test_no_redundant_for_independent_indexes(self):
        """独立索引不应被检测为冗余"""
        indexes = [
            {"name": "idx_a", "columns": ["col_a"]},
            {"name": "idx_b", "columns": ["col_b"]},
        ]
        recs = self.advisor.detect_redundant_indexes(indexes)
        assert len(recs) == 0

    def test_index_with_existing_metadata(self):
        """有已有索引时不重复推荐"""
        sql = "SELECT * FROM t_order WHERE cust_id = 100"
        metadata = {"t_order": {"indexes": [{"columns": ["cust_id"]}]}}
        recs = self.advisor.advise_from_sql(sql, table_metadata=metadata)
        assert len(recs) == 0


# ═══════════════════════════════════════════════════════════
# 三、SQL改写引擎
# ═══════════════════════════════════════════════════════════

class TestSQLRewriter:
    """SQL改写引擎深度测试"""

    def setup_method(self):
        self.rewriter = SQLRewriter()

    def test_select_star_with_metadata(self):
        """SELECT * 有元数据时生成具体字段改写"""
        sql = "SELECT * FROM t_user WHERE id = 1"
        metadata = {"t_user": {"columns": ["id", "name", "email", "phone"]}}
        recs = self.rewriter.rewrite(sql, table_metadata=metadata)
        assert any(r.type == "select_star" for r in recs)
        star_rec = next(r for r in recs if r.type == "select_star")
        assert "id" in star_rec.rewritten_sql

    def test_select_star_without_metadata(self):
        """SELECT * 无元数据时生成提示性改写"""
        sql = "SELECT * FROM t_user WHERE id = 1"
        recs = self.rewriter.rewrite(sql)
        assert any(r.type == "select_star" for r in recs)

    def test_deep_pagination_with_order_by(self):
        """深分页改写（有ORDER BY）"""
        sql = "SELECT * FROM t_order ORDER BY id LIMIT 50000, 20"
        recs = self.rewriter.rewrite(sql)
        assert any(r.type == "deep_pagination" for r in recs)

    def test_shallow_pagination_no_rewrite(self):
        """浅分页不触发改写"""
        sql = "SELECT * FROM t_order ORDER BY id LIMIT 10, 20"
        recs = self.rewriter.rewrite(sql)
        assert not any(r.type == "deep_pagination" for r in recs)

    def test_or_to_union(self):
        """OR → UNION ALL 改写"""
        sql = "SELECT * FROM t_user WHERE age = 20 OR city = 'beijing'"
        recs = self.rewriter.rewrite(sql)
        assert any(r.type == "or_to_union" for r in recs)

    def test_subquery_to_join(self):
        """子查询 → JOIN 改写"""
        sql = "SELECT * FROM t_order WHERE cust_id IN (SELECT id FROM t_customer WHERE status = 1)"
        recs = self.rewriter.rewrite(sql)
        assert any(r.type == "subquery_to_join" for r in recs)

    def test_not_in_to_left_join(self):
        """NOT IN → LEFT JOIN 改写"""
        sql = "SELECT * FROM t_order WHERE cust_id NOT IN (SELECT id FROM t_blacklist)"
        recs = self.rewriter.rewrite(sql)
        assert any(r.type == "not_in_to_left_join" for r in recs)

    def test_no_suggestions_for_clean_sql(self):
        """规范SQL不产生改写建议"""
        sql = "SELECT id, name FROM t_user WHERE id = 1 ORDER BY id LIMIT 10"
        recs = self.rewriter.rewrite(sql)
        assert len(recs) == 0


# ═══════════════════════════════════════════════════════════
# 四、字符集诊断引擎
# ═══════════════════════════════════════════════════════════

class TestCharsetDiagnoser:
    """字符集诊断引擎深度测试"""

    def setup_method(self):
        self.diag = CharsetDiagnoser()

    def test_six_diagnostic_sqls(self):
        """6套诊断SQL完整"""
        sqls = self.diag.get_diagnostic_sqls()
        assert len(sqls) == 6
        expected_keys = {"instance_default", "database_charset", "table_charset",
                         "column_charset", "mismatch_columns", "join_collation_mismatch"}
        assert set(sqls.keys()) == expected_keys

    def test_instance_charset_mismatch(self):
        """实例级字符集不一致检测"""
        results = {
            "instance_default": [
                {"Variable_name": "character_set_server", "Variable_value": "latin1"},
                {"Variable_name": "collation_server", "Variable_value": "latin1_swedish_ci"},
            ],
        }
        report = self.diag.diagnose_from_query_results(results)
        assert len(report.issues) == 2
        assert any(i["level"] == "instance" for i in report.issues)

    def test_database_charset_mismatch(self):
        """库级字符集不一致检测"""
        results = {
            "database_charset": [
                {"SCHEMA_NAME": "test_db", "DEFAULT_CHARACTER_SET_NAME": "utf8"},
                {"SCHEMA_NAME": "mysql", "DEFAULT_CHARACTER_SET_NAME": "utf8mb4"},
            ],
        }
        report = self.diag.diagnose_from_query_results(results)
        # mysql系统库应被跳过，只检测test_db
        assert any(i["level"] == "database" and i["target"] == "test_db" for i in report.issues)

    def test_column_collation_mismatch(self):
        """列级排序规则不一致检测"""
        results = {
            "mismatch_columns": [
                {"TABLE_SCHEMA": "test", "TABLE_NAME": "t_user", "COLUMN_NAME": "name",
                 "col_collation": "utf8_bin", "tbl_collation": "utf8mb4_general_ci"},
            ],
        }
        report = self.diag.diagnose_from_query_results(results)
        assert any(i["level"] == "column" for i in report.issues)

    def test_clean_database_no_issues(self):
        """合规数据库无问题"""
        results = {
            "instance_default": [
                {"Variable_name": "character_set_server", "Variable_value": "utf8mb4"},
                {"Variable_name": "collation_server", "Variable_value": "utf8mb4_general_ci"},
            ],
            "database_charset": [],
            "table_charset": [],
            "column_charset": [],
            "mismatch_columns": [],
            "join_collation_mismatch": [],
        }
        report = self.diag.diagnose_from_query_results(results)
        assert len(report.issues) == 0

    def test_summary_by_level(self):
        """按级别汇总"""
        results = {
            "instance_default": [
                {"Variable_name": "character_set_server", "Variable_value": "latin1"},
            ],
            "database_charset": [
                {"SCHEMA_NAME": "db1", "DEFAULT_CHARACTER_SET_NAME": "utf8"},
            ],
        }
        report = self.diag.diagnose_from_query_results(results)
        assert report.summary["total_issues"] == 2
        assert report.summary["by_level"]["instance"] == 1
        assert report.summary["by_level"]["database"] == 1


# ═══════════════════════════════════════════════════════════
# 五、分布式EXPLAIN分析引擎
# ═══════════════════════════════════════════════════════════

class TestDistributedExplain:
    """分布式EXPLAIN分析引擎深度测试"""

    def setup_method(self):
        self.analyzer = DistributedExplainAnalyzer()

    def test_single_set_hit(self):
        """单SET命中"""
        report = self.analyzer.analyze([
            {"hit_set": "set1", "scan_type": "INDEX_SCAN"},
        ])
        assert report.shard_key_in_where is True
        assert len(report.warnings) == 0

    def test_multi_set_hit(self):
        """多SET命中产生警告"""
        report = self.analyzer.analyze([
            {"hit_set": "set1", "scan_type": "INDEX_SCAN"},
            {"hit_set": "set2", "scan_type": "INDEX_SCAN"},
        ])
        assert report.shard_key_in_where is False
        assert any("多SET" in w["message"] for w in report.warnings)

    def test_full_scan_warning(self):
        """全表扫描警告"""
        report = self.analyzer.analyze([
            {"hit_set": "set1", "scan_type": "FULL_SCAN"},
        ])
        assert any("全表扫描" in w["message"] for w in report.warnings)

    def test_broadcast_warning(self):
        """广播操作警告"""
        report = self.analyzer.analyze([
            {"hit_set": "set1", "is_broadcast": True},
        ])
        assert any("广播" in w["message"] for w in report.warnings)

    def test_static_analysis_no_where(self):
        """静态分析：分片表无WHERE"""
        report = self.analyzer.analyze(
            [], sql="SELECT * FROM t_order",
            table_metadata={"t_order": {"shard_key": "cust_id", "is_shard_table": True}}
        )
        assert any("无WHERE" in w["message"] for w in report.warnings)

    def test_static_analysis_shardkey_not_in_where(self):
        """静态分析：WHERE不含分片键"""
        report = self.analyzer.analyze(
            [], sql="SELECT * FROM t_order WHERE status = 1",
            table_metadata={"t_order": {"shard_key": "cust_id", "is_shard_table": True}}
        )
        assert any("分片键" in w["message"] for w in report.warnings)

    def test_static_analysis_shardkey_in_where(self):
        """静态分析：WHERE含分片键，无问题"""
        report = self.analyzer.analyze(
            [], sql="SELECT * FROM t_order WHERE cust_id = 100",
            table_metadata={"t_order": {"shard_key": "cust_id", "is_shard_table": True}}
        )
        assert report.shard_key_in_where is True
        assert len(report.warnings) == 0


# ═══════════════════════════════════════════════════════════
# 六、死锁分析引擎
# ═══════════════════════════════════════════════════════════

class TestDeadlockAnalyzer:
    """死锁分析引擎深度测试"""

    def setup_method(self):
        self.analyzer = DeadlockAnalyzer()

    DEADLOCK_LOG = """
    DEADLOCK detected at 2024-01-01 10:00:00
    *** (1) TRANSACTION:
    TRANSACTION 12345, ACTIVE 5 sec starting index read
    *** (1) WAITING FOR THIS LOCK TO BE GRANTED:
    RECORD LOCKS space id 100 page no 5 n bits 72 index PRIMARY of table `test`.`t_order`
    *** (2) TRANSACTION:
    TRANSACTION 12346, ACTIVE 3 sec starting index read
    *** (2) HOLDS THE LOCK(S):
    RECORD LOCKS space id 100 page no 5 n bits 72 index PRIMARY of table `test`.`t_order`
    *** (2) WAITING FOR THIS LOCK TO BE GRANTED:
    RECORD LOCKS space id 100 page no 5 n bits 72 index PRIMARY of table `test`.`t_order`
    *** WE ROLL BACK TRANSACTION (1)
    """

    def test_deadlock_detected(self):
        """死锁检出"""
        report = self.analyzer.analyze_from_log(self.DEADLOCK_LOG)
        assert report.has_deadlock is True

    def test_deadlock_time_extracted(self):
        """死锁时间提取"""
        report = self.analyzer.analyze_from_log(self.DEADLOCK_LOG)
        assert report.deadlock_time == "2024-01-01 10:00:00"

    def test_transaction_ids_extracted(self):
        """事务ID提取"""
        report = self.analyzer.analyze_from_log(self.DEADLOCK_LOG)
        assert report.transaction_1["id"] == "12345"
        assert report.transaction_2["id"] == "12346"

    def test_locked_resource_extracted(self):
        """锁竞争资源提取"""
        report = self.analyzer.analyze_from_log(self.DEADLOCK_LOG)
        assert "t_order" in report.locked_resource

    def test_suggestions_generated(self):
        """建议生成"""
        report = self.analyzer.analyze_from_log(self.DEADLOCK_LOG)
        assert len(report.suggestions) >= 2

    def test_no_deadlock_in_log(self):
        """日志无DEADLOCK关键字"""
        report = self.analyzer.analyze_from_log("some normal log without deadlock keyword")
        assert report.has_deadlock is False

    def test_empty_log(self):
        """空日志处理"""
        report = self.analyzer.analyze_from_log("")
        assert report.has_deadlock is False


# ═══════════════════════════════════════════════════════════
# 七、长事务分析引擎
# ═══════════════════════════════════════════════════════════

class TestLongTransactionAnalyzer:
    """长事务分析引擎深度测试"""

    def setup_method(self):
        self.analyzer = LongTransactionAnalyzer()

    def test_warning_threshold(self):
        """WARNING级别（5-29秒）"""
        rows = [{"trx_id": "T001", "trx_started": "2024-01-01 10:00:00",
                 "trx_state": "RUNNING", "trx_rows_locked": 100,
                 "trx_rows_modified": 50, "trx_query": "SELECT 1", "run_seconds": 10}]
        results = self.analyzer.analyze_from_query_results(rows)
        assert len(results) == 1
        assert results[0].severity == "WARNING"

    def test_critical_threshold(self):
        """CRITICAL级别（>=30秒）"""
        rows = [{"trx_id": "T002", "trx_started": "2024-01-01 10:00:00",
                 "trx_state": "RUNNING", "trx_rows_locked": 5000,
                 "trx_rows_modified": 20000, "trx_query": "UPDATE t SET x=1",
                 "run_seconds": 60}]
        results = self.analyzer.analyze_from_query_results(rows)
        assert len(results) == 1
        assert results[0].severity == "CRITICAL"

    def test_below_threshold_skipped(self):
        """低于阈值跳过"""
        rows = [{"trx_id": "T003", "run_seconds": 2, "trx_state": "RUNNING",
                 "trx_started": "", "trx_rows_locked": 0, "trx_rows_modified": 0, "trx_query": ""}]
        results = self.analyzer.analyze_from_query_results(rows)
        assert len(results) == 0

    def test_suggestions_for_critical(self):
        """CRITICAL事务建议"""
        info = type(results[0])(trx_id="T", started_at="", run_seconds=60, state="RUNNING",
                                 rows_locked=2000, rows_modified=15000, query="UPDATE", severity="CRITICAL") if (results := self.analyzer.analyze_from_query_results([{"trx_id": "T", "run_seconds": 60, "trx_state": "RUNNING", "trx_started": "", "trx_rows_locked": 2000, "trx_rows_modified": 15000, "trx_query": "UPDATE"}])) else None
        suggestions = self.analyzer.get_suggestions(info)
        assert any("60" in s for s in suggestions)
        assert any("15000" in s for s in suggestions)

    def test_empty_input(self):
        """空输入处理"""
        results = self.analyzer.analyze_from_query_results([])
        assert len(results) == 0


# ═══════════════════════════════════════════════════════════
# 八、大表治理引擎
# ═══════════════════════════════════════════════════════════

class TestBigTableEngine:
    """大表治理引擎深度测试"""

    def test_l1_classification(self):
        """L1级分类（50GB/5000万行）"""
        classifier = BigTableClassifier()
        level, label = classifier.classify(size_gb=60, rows=60000000)
        assert level == "L1"
        assert label == "关注级"

    def test_l2_classification(self):
        """L2级分类（200GB/2亿行）"""
        classifier = BigTableClassifier()
        level, label = classifier.classify(size_gb=250, rows=300000000)
        assert level == "L2"
        assert label == "管控级"

    def test_l3_classification(self):
        """L3级分类（500GB/5亿行）"""
        classifier = BigTableClassifier()
        level, label = classifier.classify(size_gb=600, rows=800000000)
        assert level == "L3"
        assert label == "严控级"

    def test_not_big_table(self):
        """非大表"""
        classifier = BigTableClassifier()
        level, label = classifier.classify(size_gb=1, rows=10000)
        assert level == ""
        assert label == ""

    def test_size_only_classification(self):
        """仅大小达标"""
        classifier = BigTableClassifier()
        level, _ = classifier.classify(size_gb=55, rows=100)
        assert level == "L1"

    def test_rows_only_classification(self):
        """仅行数达标"""
        classifier = BigTableClassifier()
        level, _ = classifier.classify(size_gb=0.1, rows=80000000)
        assert level == "L1"

    def test_scan_big_tables(self):
        """扫描识别大表"""
        engine = BigTableEngine()
        tables = [
            {"schema": "db1", "table": "t_big1", "size_gb": 100, "rows": 80000000, "is_partitioned": False},
            {"schema": "db1", "table": "t_small", "size_gb": 0.5, "rows": 1000, "is_partitioned": False},
            {"schema": "db1", "table": "t_big2", "size_gb": 300, "rows": 300000000, "is_partitioned": True, "partition_count": 50},
        ]
        big_tables = engine.scan_big_tables(tables)
        assert len(big_tables) == 2
        levels = [bt.level for bt in big_tables]
        assert "L1" in levels
        assert "L2" in levels

    def test_scan_big_tables_prefers_provided_level(self):
        """采集端已按1GB口径算好级别时优先采用，覆盖1~50GB大表(TDSQL需求>1GB)。

        无 level 时仍回退银行分类器(>=50GB)——保证既有语义不变。
        """
        engine = BigTableEngine()
        tables = [
            # 10GB分区表：银行分类器会因<50GB丢弃，但采集端给了level → 必须保留
            {"schema": "db1", "table": "t_part", "size_gb": 10.37, "rows": 100,
             "level": "L2 重点大表", "is_partitioned": True, "partition_count": 8},
            # 2GB非分区表：同理保留
            {"schema": "db1", "table": "t_plain", "size_gb": 2.0, "rows": 100,
             "level": "L1 一般大表", "is_partitioned": False},
        ]
        big_tables = engine.scan_big_tables(tables)
        assert len(big_tables) == 2, "带level的1~50GB大表不应被银行分类器丢弃"
        by_table = {bt.table: bt for bt in big_tables}
        assert by_table["t_part"].is_partitioned is True
        assert by_table["t_part"].partition_count == 8
        assert by_table["t_part"].level == "L2 重点大表"
        assert by_table["t_plain"].is_partitioned is False

    def test_build_large_tables_query_structure(self):
        """采集SQL(双源取大)结构与参数个数校验，防止 %s 与参数错位。"""
        from backend.services.tdsql_connector import build_large_tables_query
        sql, params = build_large_tables_query(1.0)
        assert "information_schema.PARTITIONS" in sql
        assert "GREATEST" in sql
        # %s 个数 == 参数个数：阈值(1) + 系统库(9) + 阈值(1) = 11
        assert sql.count("%s") == len(params) == 11
        # 带库过滤时多一个参数
        sql2, params2 = build_large_tables_query(1.0, "tdsql_check")
        assert sql2.count("%s") == len(params2) == 12
        assert params2[-2] == "tdsql_check"

    def test_parse_shard_key_from_ddl(self):
        """TDSQL分片键从 SHOW CREATE TABLE DDL 解析（含真实格式）。"""
        from backend.services.tdsql_connector import parse_shard_key_from_ddl
        real_ddl = (
            "CREATE TABLE `big_audit_trail` (\n"
            "  `id` bigint NOT NULL AUTO_INCREMENT,\n"
            "  PRIMARY KEY (`id`),\n"
            "  KEY `idx_event` (`event_time`)\n"
            ") ENGINE=InnoDB AUTO_INCREMENT=25600001 DEFAULT CHARSET=utf8mb4 "
            "COLLATE=utf8mb4_bin shardkey=id"
        )
        assert parse_shard_key_from_ddl(real_ddl) == "id"
        assert parse_shard_key_from_ddl(") ENGINE=InnoDB shardkey=(a,b)") == "a,b"
        # noshard/broadcast 表无 shardkey → 空（正确语义）
        assert parse_shard_key_from_ddl(") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4") == ""
        assert parse_shard_key_from_ddl("") == ""
        assert parse_shard_key_from_ddl(None) == ""

    def test_analyze_partitions_flags(self):
        """分区下钻派生分析：兜底分区过大 / 数据倾斜 / 空分区 / 空表。"""
        from backend.services.tdsql_connector import _analyze_partitions
        # 兜底 MAXVALUE 分区过大（占比 80% > 30%）
        mv = _analyze_partitions([
            {"name": "p1", "size_gb": 1.0, "pct": 10.0, "is_maxvalue": False, "rows": 100},
            {"name": "p2", "size_gb": 1.0, "pct": 10.0, "is_maxvalue": False, "rows": 100},
            {"name": "pmax", "size_gb": 8.0, "pct": 80.0, "is_maxvalue": True, "rows": 900},
        ])
        assert "maxvalue_oversized" in [f["code"] for f in mv["flags"]]
        assert mv["max_partition"]["name"] == "pmax"
        # 数据倾斜（4 小 + 1 大：max 8 / 平均 1.76 = 4.5x ≥ 3）
        skew = _analyze_partitions(
            [{"name": f"p{i}", "size_gb": 0.2, "pct": 2.3, "is_maxvalue": False, "rows": 10} for i in range(4)]
            + [{"name": "hot", "size_gb": 8.0, "pct": 90.9, "is_maxvalue": False, "rows": 999}])
        assert "data_skew" in [f["code"] for f in skew["flags"]]
        assert skew["skew_ratio"] >= 3
        # 空表边界
        empty = _analyze_partitions([])
        assert empty["flags"] == [] and empty["partition_count"] == 0
        # 空分区标记（≥3 个空分区）
        many_empty = [{"name": f"e{i}", "size_gb": 0.0, "pct": 0.0, "is_maxvalue": False, "rows": 0} for i in range(3)]
        many_empty.append({"name": "big", "size_gb": 2.0, "pct": 100.0, "is_maxvalue": False, "rows": 999})
        assert "empty_partitions" in [f["code"] for f in _analyze_partitions(many_empty)["flags"]]

    def test_governance_report(self):
        """治理报告生成"""
        engine = BigTableEngine()
        tables = [
            {"schema": "db1", "table": "t_big", "size_gb": 100, "rows": 80000000, "is_partitioned": False},
        ]
        big_tables = engine.scan_big_tables(tables)
        report = engine.get_governance_report(big_tables)
        assert report["total_big_tables"] == 1
        assert report["by_level"]["L1"] == 1
        assert len(report["unpartitioned"]) == 1
        assert len(report["partition_advice"]) >= 1

    def test_partition_watermark_normal(self):
        """分区水位正常"""
        monitor = PartitionMonitor()
        info = monitor.check_watermark(30, 100)
        assert info.status == "NORMAL"

    def test_partition_watermark_warning(self):
        """分区水位告警"""
        monitor = PartitionMonitor()
        info = monitor.check_watermark(75, 100)
        assert info.status == "WARNING"

    def test_partition_watermark_critical(self):
        """分区水位严重"""
        monitor = PartitionMonitor()
        info = monitor.check_watermark(90, 100)
        assert info.status == "CRITICAL"

    def test_partition_advisor_classify_transaction_log(self):
        """分区顾问分类交易流水表"""
        advisor = PartitionAdvisor()
        classification = advisor.classify_table("t_transaction_log")
        assert classification.table_type == "transaction_log"
        assert classification.retention_days == 365
        assert classification.partition_key == "create_time"

    def test_partition_advisor_classify_temp_table(self):
        """分区顾问分类临时表"""
        advisor = PartitionAdvisor()
        classification = advisor.classify_table("t_temp_cache")
        assert classification.table_type == "temp_data"
        assert classification.retention_days == 30

    def test_partition_advisor_suggest(self):
        """分区改造建议"""
        advisor = PartitionAdvisor()
        advice = advisor.suggest_partition("t_transaction_log", 100, 80000000)
        assert advice is not None
        assert "ALTER TABLE" in advice.ddl_example
        assert "PARTITION" in advice.ddl_example

    def test_partition_advisor_no_suggest_for_small_table(self):
        """小表不需分区改造"""
        advisor = PartitionAdvisor()
        advice = advisor.suggest_partition("t_small", 0.5, 1000)
        assert advice is None
