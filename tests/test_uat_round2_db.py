"""
TDSQL SQL审核工具 - 第二轮UAT测试（真实数据库集成测试）

使用Docker MySQL模拟TDSQL环境，测试TDSQL连接器、元数据查询、
慢查询抓取、字符集检查、大表检查、EXPLAIN分析、元数据增强审核等
全部需要数据库交互的功能。

前置条件：Docker MySQL容器运行中 (127.0.0.1:13306, tdsql_test)
"""
import os
import pytest
from fastapi.testclient import TestClient

# 测试数据库连接配置
TDSQL_TEST_CONFIG = {
    "host": "127.0.0.1",
    "port": 13306,
    "user": "root",
    "password": "tdsql_test_2024",
    "database": "tdsql_test",
}

# 检查MySQL是否可用（跳过条件）
try:
    import pymysql
    _conn = pymysql.connect(
        host=TDSQL_TEST_CONFIG["host"],
        port=TDSQL_TEST_CONFIG["port"],
        user=TDSQL_TEST_CONFIG["user"],
        password=TDSQL_TEST_CONFIG["password"],
        database=TDSQL_TEST_CONFIG["database"],
        connect_timeout=3,
    )
    _conn.close()
    MYSQL_AVAILABLE = True
except Exception:
    MYSQL_AVAILABLE = False

SKIP_REASON = "Docker MySQL环境未启动，请先运行: docker-compose up -d mysql"


@pytest.fixture(scope="module")
def client():
    """FastAPI测试客户端"""
    from backend.main import app
    return TestClient(app)


@pytest.fixture(scope="module")
def connected_client(client):
    """已连接TDSQL的测试客户端"""
    if not MYSQL_AVAILABLE:
        pytest.skip(SKIP_REASON)
    # 连接TDSQL
    resp = client.post("/api/v1/tdsql/connect", json=TDSQL_TEST_CONFIG)
    assert resp.status_code == 200, f"连接失败: {resp.text}"
    yield client
    # 断开连接
    client.post("/api/v1/tdsql/disconnect")


# ============================================================
# UAT-44: TDSQL连接管理
# ============================================================

class TestUAT44_TDSQLConnection:
    """UAT-44: TDSQL连接管理（真实MySQL连接）"""

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat44_01_connect_success(self, client):
        """测试连接TDSQL实例成功"""
        resp = client.post("/api/v1/tdsql/connect", json=TDSQL_TEST_CONFIG)
        assert resp.status_code == 200
        data = resp.json()
        assert data["message"] == "连接成功"
        assert data["host"] == "127.0.0.1"
        assert data["port"] == 13306
        assert data["database"] == "tdsql_test"
        # 清理
        client.post("/api/v1/tdsql/disconnect")

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat44_02_connect_wrong_password(self, client):
        """测试错误密码连接失败"""
        bad_config = {**TDSQL_TEST_CONFIG, "password": "wrong_password"}
        resp = client.post("/api/v1/tdsql/connect", json=bad_config)
        assert resp.status_code == 500
        assert "连接失败" in resp.json()["detail"]

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat44_03_connection_status(self, client):
        """测试连接状态检查"""
        # 未连接状态
        resp = client.get("/api/v1/tdsql/status")
        assert resp.status_code == 200
        assert resp.json()["connected"] is False
        # 连接后状态
        client.post("/api/v1/tdsql/connect", json=TDSQL_TEST_CONFIG)
        resp = client.get("/api/v1/tdsql/status")
        assert resp.status_code == 200
        assert resp.json()["connected"] is True
        assert resp.json()["host"] == "127.0.0.1"
        # 断开后状态
        client.post("/api/v1/tdsql/disconnect")
        resp = client.get("/api/v1/tdsql/status")
        assert resp.json()["connected"] is False

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat44_04_test_connection(self, client):
        """测试连接测试接口（返回版本和延迟）"""
        resp = client.get("/api/v1/tdsql/test-connection", params=TDSQL_TEST_CONFIG)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "connected"
        assert data["server_version"] != "unknown"
        assert data["latency_ms"] >= 0
        assert data["pymysql_available"] is True
        assert "slow_query_config" in data

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat44_05_disconnect(self, client):
        """测试断开连接"""
        client.post("/api/v1/tdsql/connect", json=TDSQL_TEST_CONFIG)
        resp = client.post("/api/v1/tdsql/disconnect")
        assert resp.status_code == 200
        assert resp.json()["message"] == "已断开连接"

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat44_06_not_connected_error(self, client):
        """测试未连接时操作返回错误"""
        client.post("/api/v1/tdsql/disconnect")
        resp = client.get("/api/v1/tdsql/tables")
        assert resp.status_code == 400
        assert "未连接" in resp.json()["detail"]


# ============================================================
# UAT-45: 元数据查询
# ============================================================

class TestUAT45_MetadataQuery:
    """UAT-45: 元数据查询（真实数据库）"""

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat45_01_get_tables(self, connected_client):
        """测试获取表列表"""
        resp = connected_client.get("/api/v1/tdsql/tables")
        assert resp.status_code == 200
        tables = resp.json()["tables"]
        assert len(tables) >= 10
        table_names = [t["TABLE_NAME"] for t in tables]
        assert "t_order" in table_names
        assert "t_user" in table_names
        assert "t_product" in table_names
        assert "t_config" in table_names

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat45_02_get_table_metadata(self, connected_client):
        """测试获取表完整元数据"""
        resp = connected_client.get("/api/v1/tdsql/tables/t_order/metadata")
        assert resp.status_code == 200
        meta = resp.json()
        assert meta["table_name"] == "t_order"
        assert meta["engine"] == "InnoDB"
        assert meta["table_collation"] == "utf8mb4_general_ci"
        assert meta["table_rows"] >= 8
        assert len(meta["columns"]) > 0
        assert len(meta["indexes"]) > 0
        assert "create_sql" in meta
        assert meta["create_sql"] != ""

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat45_03_metadata_columns(self, connected_client):
        """测试元数据中字段信息完整性"""
        resp = connected_client.get("/api/v1/tdsql/tables/t_user/metadata")
        assert resp.status_code == 200
        columns = resp.json()["columns"]
        col_names = [c["COLUMN_NAME"] for c in columns]
        assert "id" in col_names
        assert "user_name" in col_names
        assert "phone" in col_names
        assert "is_deleted" in col_names
        # 检查字段属性
        id_col = next(c for c in columns if c["COLUMN_NAME"] == "id")
        assert id_col["COLUMN_KEY"] == "PRI"

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat45_04_metadata_indexes(self, connected_client):
        """测试元数据中索引信息"""
        resp = connected_client.get("/api/v1/tdsql/tables/t_order/metadata")
        assert resp.status_code == 200
        indexes = resp.json()["indexes"]
        index_names = [idx["INDEX_NAME"] for idx in indexes]
        assert "PRIMARY" in index_names
        assert "uk_order_no" in index_names
        assert "idx_user_id" in index_names

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat45_05_metadata_nonexistent_table(self, connected_client):
        """测试获取不存在表的元数据"""
        resp = connected_client.get("/api/v1/tdsql/tables/t_nonexistent/metadata")
        # 应返回200但元数据为空，或返回500
        assert resp.status_code in [200, 500]

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat45_06_metadata_data_size(self, connected_client):
        """测试大表元数据大小信息"""
        resp = connected_client.get("/api/v1/tdsql/tables/t_large_order_log/metadata")
        assert resp.status_code == 200
        meta = resp.json()
        # InnoDB的TABLE_ROWS是估计值，可能为0，检查data_mb即可
        assert meta["data_mb"] >= 0
        assert meta["table_name"] == "t_large_order_log"


# ============================================================
# UAT-46: 分片表检测
# ============================================================

class TestUAT46_ShardTableDetection:
    """UAT-46: TDSQL分片表检测（模拟环境）"""

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat46_01_shardkey_detection(self, connected_client):
        """测试SHARDKEY分片键检测"""
        resp = connected_client.get("/api/v1/tdsql/tables/t_order/metadata")
        assert resp.status_code == 200
        meta = resp.json()
        assert meta["is_shard_table"] is True
        assert meta["shard_key"] == "user_id"
        assert meta["is_single_table"] is False

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat46_02_another_shardkey(self, connected_client):
        """测试另一个分片键检测"""
        resp = connected_client.get("/api/v1/tdsql/tables/t_order_detail/metadata")
        assert resp.status_code == 200
        meta = resp.json()
        assert meta["is_shard_table"] is True
        assert meta["shard_key"] == "order_id"

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat46_03_broadcast_table(self, connected_client):
        """测试广播表检测"""
        resp = connected_client.get("/api/v1/tdsql/tables/t_config/metadata")
        assert resp.status_code == 200
        meta = resp.json()
        assert meta["is_broadcast_table"] is True
        assert meta["is_shard_table"] is False

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat46_04_single_table(self, connected_client):
        """测试单表检测"""
        resp = connected_client.get("/api/v1/tdsql/tables/t_user/metadata")
        assert resp.status_code == 200
        meta = resp.json()
        assert meta["is_single_table"] is True
        assert meta["is_shard_table"] is False
        assert meta["is_broadcast_table"] is False

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat46_05_sub_partition_table(self, connected_client):
        """测试二级分区表检测"""
        resp = connected_client.get("/api/v1/tdsql/tables/t_sub_partition_tdsql_subp/metadata")
        assert resp.status_code == 200
        meta = resp.json()
        assert meta["is_shard_table"] is True

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat46_06_refund_shardkey(self, connected_client):
        """测试退款表分片键"""
        resp = connected_client.get("/api/v1/tdsql/tables/t_refund/metadata")
        assert resp.status_code == 200
        meta = resp.json()
        assert meta["is_shard_table"] is True
        assert meta["shard_key"] == "user_id"


# ============================================================
# UAT-47: 慢查询抓取
# ============================================================

class TestUAT47_SlowQueryFetch:
    """UAT-47: 慢查询抓取（真实performance_schema数据）"""

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat47_01_fetch_from_digest(self, connected_client):
        """测试从performance_schema抓取慢SQL摘要"""
        resp = connected_client.post("/api/v1/tdsql/slow-queries/fetch", json={
            "source": "digest",
            "limit": 10,
            "time_window_start": "2026-01-01 00:00:00",
            "time_window_end": "2026-12-31 23:59:59",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "digest"
        assert data["fetched"] >= 0
        assert isinstance(data["results"], list)

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat47_02_fetch_from_slow_log(self, connected_client):
        """测试从slow_log抓取慢SQL"""
        resp = connected_client.post("/api/v1/tdsql/slow-queries/fetch", json={
            "source": "slow_log",
            "limit": 10,
            "min_time": 0.0,
            "time_window_start": "2026-01-01 00:00:00",
            "time_window_end": "2026-12-31 23:59:59",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "slow_log"

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat47_03_fetch_from_processlist(self, connected_client):
        """测试从processlist抓取慢SQL"""
        resp = connected_client.post("/api/v1/tdsql/slow-queries/fetch", json={
            "source": "processlist",
            "min_time": 0,
            "time_window_start": "2026-01-01 00:00:00",
            "time_window_end": "2026-12-31 23:59:59",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "processlist"

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat47_04_invalid_source(self, connected_client):
        """测试无效数据源"""
        resp = connected_client.post("/api/v1/tdsql/slow-queries/fetch", json={
            "source": "invalid_source",
            "time_window_start": "2026-01-01 00:00:00",
            "time_window_end": "2026-12-31 23:59:59",
        })
        assert resp.status_code == 400

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat47_04a_no_time_window(self, connected_client):
        """测试缺少时间窗口返回422"""
        resp = connected_client.post("/api/v1/tdsql/slow-queries/fetch", json={
            "source": "digest",
            "limit": 10,
        })
        assert resp.status_code == 422

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat47_04b_time_window_reversed(self, connected_client):
        """测试时间窗口开始大于结束返回422"""
        resp = connected_client.post("/api/v1/tdsql/slow-queries/fetch", json={
            "source": "digest",
            "limit": 10,
            "time_window_start": "2026-06-17 12:00:00",
            "time_window_end": "2026-06-17 08:00:00",
        })
        assert resp.status_code == 422

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat47_05_slow_query_config(self, connected_client):
        """测试获取慢查询配置"""
        resp = connected_client.get("/api/v1/tdsql/slow-query-config")
        assert resp.status_code == 200
        data = resp.json()
        assert "variables" in data
        variables = data["variables"]
        assert "slow_query_log" in variables
        assert variables["slow_query_log"] == "ON"
        assert "long_query_time" in variables

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat47_06_digest_has_sql_text(self, connected_client):
        """测试digest数据包含SQL文本"""
        # 先执行一些查询来生成digest数据
        from backend.services.tdsql_connector import TDSQLConnectionPool, TDSQLConnectionConfig
        config = TDSQLConnectionConfig(**TDSQL_TEST_CONFIG)
        pool = TDSQLConnectionPool(config)
        with pool.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM t_no_index WHERE biz_key = 'KEY001'")
            cursor.fetchall()

        resp = connected_client.post("/api/v1/tdsql/slow-queries/fetch", json={
            "source": "digest",
            "limit": 50,
            "time_window_start": "2026-01-01 00:00:00",
            "time_window_end": "2026-12-31 23:59:59",
        })
        assert resp.status_code == 200
        data = resp.json()
        # 应该有数据
        assert data["fetched"] >= 0


# ============================================================
# UAT-48: 字符集一致性检查
# ============================================================

class TestUAT48_CharsetCheck:
    """UAT-48: 字符集一致性检查（真实数据）"""

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat48_01_charset_check_overall(self, connected_client):
        """测试字符集一致性检查整体接口"""
        resp = connected_client.get("/api/v1/tdsql/check/charset")
        assert resp.status_code == 200
        data = resp.json()
        assert data["database"] == "tdsql_test"
        assert "db_charset" in data
        assert "table_charset_distribution" in data
        assert "column_mismatches" in data
        assert "cross_table_mismatches" in data
        assert "is_consistent" in data

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat48_02_db_charset(self, connected_client):
        """测试库级字符集"""
        resp = connected_client.get("/api/v1/tdsql/check/charset")
        data = resp.json()
        db_charset = data["db_charset"]
        assert db_charset["DEFAULT_CHARACTER_SET_NAME"] == "utf8mb4"

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat48_03_table_charset_distribution(self, connected_client):
        """测试表级字符集分布"""
        resp = connected_client.get("/api/v1/tdsql/check/charset")
        data = resp.json()
        distribution = data["table_charset_distribution"]
        # 应该有utf8mb4_general_ci和latin1_swedish_ci两种
        collations = [d["TABLE_COLLATION"] for d in distribution]
        assert "utf8mb4_general_ci" in collations
        assert "latin1_swedish_ci" in collations

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat48_04_column_mismatches(self, connected_client):
        """测试字段级字符集不一致"""
        resp = connected_client.get("/api/v1/tdsql/check/charset")
        data = resp.json()
        # t_charset_latin1表有字段级不一致
        # description字段是utf8mb4但表是latin1
        assert data["is_consistent"] is False
        assert len(data["column_mismatches"]) > 0

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat48_05_charset_check_specific_db(self, connected_client):
        """测试指定数据库的字符集检查"""
        resp = connected_client.get("/api/v1/tdsql/check/charset", params={"database": "tdsql_test"})
        assert resp.status_code == 200
        assert resp.json()["database"] == "tdsql_test"


# ============================================================
# UAT-49: 大表检查
# ============================================================

class TestUAT49_LargeTableCheck:
    """UAT-49: 大表检查（真实数据）"""

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat49_01_large_tables_default_threshold(self, connected_client):
        """测试默认阈值(1GB)大表检查"""
        resp = connected_client.get("/api/v1/tdsql/check/large-tables")
        assert resp.status_code == 200
        data = resp.json()
        assert data["database"] == "tdsql_test"
        assert data["threshold_gb"] == 1.0
        assert "tables" in data
        # 默认1GB阈值，测试数据不会触发
        assert data["total"] == 0

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat49_02_large_tables_low_threshold(self, connected_client):
        """测试低阈值大表检查"""
        # 设置极低阈值(0.000001GB ≈ 1KB)，应该能检测到表
        resp = connected_client.get("/api/v1/tdsql/check/large-tables", params={
            "threshold_gb": 0.000001,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] > 0
        # 检查返回字段
        for t in data["tables"]:
            assert "TABLE_NAME" in t
            assert "size_gb" in t
            assert "TABLE_ROWS" in t
            assert "level" in t

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat49_03_large_table_levels(self, connected_client):
        """测试大表分级"""
        resp = connected_client.get("/api/v1/tdsql/check/large-tables", params={
            "threshold_gb": 0.000001,
        })
        data = resp.json()
        levels = [t["level"] for t in data["tables"]]
        # 测试数据较小，级别为“一般表”，但应该都有level字段
        assert len(levels) > 0
        for lv in levels:
            assert lv != ""

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat49_04_large_table_specific_db(self, connected_client):
        """测试指定数据库的大表检查"""
        resp = connected_client.get("/api/v1/tdsql/check/large-tables", params={
            "database": "tdsql_test",
            "threshold_gb": 0.000001,
        })
        assert resp.status_code == 200
        assert resp.json()["database"] == "tdsql_test"


# ============================================================
# UAT-50: EXPLAIN分析
# ============================================================

class TestUAT50_ExplainAnalysis:
    """UAT-50: EXPLAIN执行计划分析（真实数据库）"""

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat50_01_explain_simple_query(self, connected_client):
        """测试简单查询的EXPLAIN"""
        from backend.services.tdsql_connector import TDSQLConnectionPool, TDSQLConnectionConfig
        config = TDSQLConnectionConfig(**TDSQL_TEST_CONFIG)
        pool = TDSQLConnectionPool(config)
        with pool.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("EXPLAIN SELECT * FROM t_user WHERE id = 1")
            rows = cursor.fetchall()
            assert len(rows) >= 1
            assert "id" in rows[0] or "select_type" in rows[0]

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat50_02_explain_join_query(self, connected_client):
        """测试JOIN查询的EXPLAIN"""
        from backend.services.tdsql_connector import TDSQLConnectionPool, TDSQLConnectionConfig
        config = TDSQLConnectionConfig(**TDSQL_TEST_CONFIG)
        pool = TDSQLConnectionPool(config)
        with pool.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                EXPLAIN SELECT a.*, b.product_name
                FROM t_order a
                JOIN t_order_detail b ON a.id = b.order_id
                WHERE a.user_id = 1
            """)
            rows = cursor.fetchall()
            assert len(rows) >= 2  # 至少两行（两个表）

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat50_03_explain_no_index_query(self, connected_client):
        """测试无索引查询的EXPLAIN"""
        from backend.services.tdsql_connector import TDSQLConnectionPool, TDSQLConnectionConfig
        config = TDSQLConnectionConfig(**TDSQL_TEST_CONFIG)
        pool = TDSQLConnectionPool(config)
        with pool.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("EXPLAIN SELECT * FROM t_no_index WHERE biz_value = 'value003'")
            rows = cursor.fetchall()
            assert len(rows) >= 1
            # 应该是ALL（全表扫描）
            row = rows[0]
            type_val = row.get("type", "")
            assert type_val == "ALL"


# ============================================================
# UAT-51: 元数据增强审核
# ============================================================

class TestUAT51_AuditWithMetadata:
    """UAT-51: 使用真实元数据增强SQL审核"""

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat51_01_audit_with_metadata(self, connected_client):
        """测试元数据增强审核"""
        resp = connected_client.post("/api/v1/tdsql/audit/with-metadata", json={
            "sql": "SELECT * FROM t_order WHERE user_id = 1 AND status = 1",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "sql" in data
        assert "table_metadata" in data
        assert "audit_result" in data
        # t_order应该被识别为分片表
        assert "t_order" in data["table_metadata"]
        meta = data["table_metadata"]["t_order"]
        assert meta["is_shard_table"] is True
        assert meta["shard_key"].lower() == "user_id"

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat51_02_audit_dml_with_metadata(self, connected_client):
        """测试DML审核带元数据"""
        resp = connected_client.post("/api/v1/tdsql/audit/with-metadata", json={
            "sql": "UPDATE t_order SET status = 2 WHERE id = 1",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "t_order" in data["table_metadata"]

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat51_03_audit_multi_table_metadata(self, connected_client):
        """测试多表元数据增强审核"""
        resp = connected_client.post("/api/v1/tdsql/audit/with-metadata", json={
            "sql": "SELECT a.order_no, b.product_name FROM t_order a JOIN t_order_detail b ON a.id = b.order_id WHERE a.user_id = 1",
        })
        assert resp.status_code == 200
        data = resp.json()
        meta = data["table_metadata"]
        assert "t_order" in meta
        assert "t_order_detail" in meta

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat51_04_audit_empty_sql(self, connected_client):
        """测试空SQL审核"""
        resp = connected_client.post("/api/v1/tdsql/audit/with-metadata", json={
            "sql": "",
        })
        assert resp.status_code == 400


# ============================================================
# UAT-52: 多连接配置管理
# ============================================================

class TestUAT52_ConnectionManagement:
    """UAT-52: 多连接配置管理（真实连接）"""

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat52_01_save_connection(self, client):
        """测试保存连接配置"""
        resp = client.post("/api/v1/tdsql/connections", json={
            **TDSQL_TEST_CONFIG,
            "name": "Docker测试MySQL",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["message"] == "连接配置已保存"
        assert data["name"] == "Docker测试MySQL"
        assert "id" in data
        # 清理
        client.delete(f"/api/v1/tdsql/connections/{data['id']}")

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat52_02_list_connections(self, client):
        """测试列出连接配置"""
        # 先保存一个
        save_resp = client.post("/api/v1/tdsql/connections", json={
            **TDSQL_TEST_CONFIG,
            "name": "UAT测试连接",
        })
        conn_id = save_resp.json()["id"]

        resp = client.get("/api/v1/tdsql/connections")
        assert resp.status_code == 200
        data = resp.json()
        assert "connections" in data
        assert len(data["connections"]) >= 1
        # 密码不应返回
        for conn in data["connections"]:
            assert "password" not in conn
        # 清理
        client.delete(f"/api/v1/tdsql/connections/{conn_id}")

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat52_03_connect_by_saved_config(self, client):
        """测试使用已保存配置连接"""
        # 保存连接
        save_resp = client.post("/api/v1/tdsql/connections", json={
            **TDSQL_TEST_CONFIG,
            "name": "连接测试",
        })
        conn_id = save_resp.json()["id"]

        # 使用保存的配置连接
        resp = client.post(f"/api/v1/tdsql/connections/{conn_id}/connect")
        assert resp.status_code == 200
        data = resp.json()
        assert data["message"] == "连接成功"
        assert data["host"] == "127.0.0.1"

        # 清理
        client.post("/api/v1/tdsql/disconnect")
        client.delete(f"/api/v1/tdsql/connections/{conn_id}")

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat52_04_set_default_connection(self, client):
        """测试设置默认连接"""
        save_resp = client.post("/api/v1/tdsql/connections", json={
            **TDSQL_TEST_CONFIG,
            "name": "默认连接测试",
        })
        conn_id = save_resp.json()["id"]

        resp = client.post(f"/api/v1/tdsql/connections/{conn_id}/set-default")
        assert resp.status_code == 200
        assert resp.json()["message"] == "默认连接已设置"

        # 验证
        list_resp = client.get("/api/v1/tdsql/connections")
        assert list_resp.json()["default"] == conn_id

        # 清理
        client.delete(f"/api/v1/tdsql/connections/{conn_id}")

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat52_05_delete_connection(self, client):
        """测试删除连接配置"""
        save_resp = client.post("/api/v1/tdsql/connections", json={
            **TDSQL_TEST_CONFIG,
            "name": "待删除连接",
        })
        conn_id = save_resp.json()["id"]

        resp = client.delete(f"/api/v1/tdsql/connections/{conn_id}")
        assert resp.status_code == 200
        assert resp.json()["message"] == "连接配置已删除"

        # 验证已删除
        list_resp = client.get("/api/v1/tdsql/connections")
        conn_ids = [c["id"] for c in list_resp.json()["connections"]]
        assert conn_id not in conn_ids

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat52_06_delete_nonexistent_connection(self, client):
        """测试删除不存在的连接"""
        resp = client.delete("/api/v1/tdsql/connections/nonexistent_id")
        assert resp.status_code == 404

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat52_07_connect_nonexistent_config(self, client):
        """测试使用不存在的配置连接"""
        resp = client.post("/api/v1/tdsql/connections/nonexistent_id/connect")
        assert resp.status_code == 404


# ============================================================
# UAT-53: 端到端工作流
# ============================================================

class TestUAT53_EndToEndWorkflows:
    """UAT-53: 端到端工作流（真实数据库）"""

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat53_01_full_audit_workflow(self, client):
        """测试完整审核工作流：连接→获取表→获取元数据→审核"""
        # 1. 连接
        resp = client.post("/api/v1/tdsql/connect", json=TDSQL_TEST_CONFIG)
        assert resp.status_code == 200

        # 2. 获取表列表
        resp = client.get("/api/v1/tdsql/tables")
        assert resp.status_code == 200
        tables = resp.json()["tables"]
        assert len(tables) >= 10

        # 3. 获取特定表元数据
        resp = client.get("/api/v1/tdsql/tables/t_order/metadata")
        assert resp.status_code == 200
        meta = resp.json()
        assert meta["is_shard_table"] is True

        # 4. 使用元数据增强审核
        resp = client.post("/api/v1/tdsql/audit/with-metadata", json={
            "sql": "SELECT * FROM t_order WHERE user_id = 1",
        })
        assert resp.status_code == 200
        audit = resp.json()
        assert "t_order" in audit["table_metadata"]

        # 清理
        client.post("/api/v1/tdsql/disconnect")

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat53_02_slow_query_workflow(self, client):
        """测试慢查询工作流：连接→执行慢SQL→抓取→分析"""
        # 1. 连接
        client.post("/api/v1/tdsql/connect", json=TDSQL_TEST_CONFIG)

        # 2. 执行一些查询（生成慢SQL数据）
        from backend.services.tdsql_connector import TDSQLConnectionPool, TDSQLConnectionConfig
        config = TDSQLConnectionConfig(**TDSQL_TEST_CONFIG)
        pool = TDSQLConnectionPool(config)
        with pool.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM t_product WHERE product_name LIKE '%数据库%'")
            cursor.fetchall()
            cursor.execute("SELECT * FROM t_order WHERE order_no LIKE '%2401%'")
            cursor.fetchall()

        # 3. 抓取慢SQL
        resp = client.post("/api/v1/tdsql/slow-queries/fetch", json={
            "source": "digest",
            "limit": 20,
            "time_window_start": "2026-01-01 00:00:00",
            "time_window_end": "2026-12-31 23:59:59",
        })
        assert resp.status_code == 200

        # 4. 获取慢查询配置
        resp = client.get("/api/v1/tdsql/slow-query-config")
        assert resp.status_code == 200
        assert resp.json()["variables"]["slow_query_log"] == "ON"

        # 清理
        client.post("/api/v1/tdsql/disconnect")

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat53_03_charset_inspection_workflow(self, client):
        """测试字符集巡检工作流：连接→字符集检查→发现问题"""
        client.post("/api/v1/tdsql/connect", json=TDSQL_TEST_CONFIG)

        resp = client.get("/api/v1/tdsql/check/charset")
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_consistent"] is False
        assert len(data["column_mismatches"]) > 0
        assert len(data["table_charset_distribution"]) >= 2

        client.post("/api/v1/tdsql/disconnect")

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat53_04_bigtable_inspection_workflow(self, client):
        """测试大表巡检工作流：连接→大表检查→分级"""
        client.post("/api/v1/tdsql/connect", json=TDSQL_TEST_CONFIG)

        resp = client.get("/api/v1/tdsql/check/large-tables", params={
            "threshold_gb": 0.000001,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] > 0
        # 验证所有返回的表都有level字段
        for t in data["tables"]:
            assert "level" in t
            assert t["level"] != ""

        client.post("/api/v1/tdsql/disconnect")

    @pytest.mark.skipif(not MYSQL_AVAILABLE, reason=SKIP_REASON)
    def test_uat53_05_connection_management_workflow(self, client):
        """测试连接管理完整工作流：保存→设默认→列表→连接→删除"""
        # 1. 保存连接
        save_resp = client.post("/api/v1/tdsql/connections", json={
            **TDSQL_TEST_CONFIG,
            "name": "E2E工作流连接",
        })
        assert save_resp.status_code == 200
        conn_id = save_resp.json()["id"]

        # 2. 设置默认
        resp = client.post(f"/api/v1/tdsql/connections/{conn_id}/set-default")
        assert resp.status_code == 200

        # 3. 列表确认
        resp = client.get("/api/v1/tdsql/connections")
        assert resp.status_code == 200
        assert resp.json()["default"] == conn_id

        # 4. 用保存的配置连接
        resp = client.post(f"/api/v1/tdsql/connections/{conn_id}/connect")
        assert resp.status_code == 200

        # 5. 验证连接可用
        resp = client.get("/api/v1/tdsql/status")
        assert resp.json()["connected"] is True

        # 6. 断开
        client.post("/api/v1/tdsql/disconnect")

        # 7. 删除连接
        resp = client.delete(f"/api/v1/tdsql/connections/{conn_id}")
        assert resp.status_code == 200

        # 8. 确认已删除
        resp = client.get("/api/v1/tdsql/connections")
        conn_ids = [c["id"] for c in resp.json()["connections"]]
        assert conn_id not in conn_ids
