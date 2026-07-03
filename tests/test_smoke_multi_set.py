"""
TDSQL慢SQL扫描 - 冒烟测试

验证基于TDSQL Proxy层架构的慢SQL扫描核心逻辑，不依赖真实TDSQL连接。
使用mock模拟TDSQL Proxy响应，验证：
1. SET发现机制（discover_sets仍用于实例拓扑展示）
2. Proxy层直查（无SET路由）
3. 数据源切换（digest/processlist）
4. slow_log拒绝
5. set_id筛选（基于已有数据）
6. 跨SET对比分析（基于已有数据）
"""
import json
import os
from unittest.mock import patch, MagicMock, PropertyMock

import pytest
from fastapi.testclient import TestClient


# ── 辅助：初始化测试数据（MySQL元数据库版） ──────────────────

@pytest.fixture(scope="module")
def test_db():
    """在MySQL测试库中准备种子数据（V2.1: 系统库已迁移到MySQL，不再使用临时SQLite文件）"""
    from backend.services.database import _get_connection, ensure_db
    ensure_db()

    conn = _get_connection()
    # 模块级全清（等价旧版临时SQLite文件的隔离语义，防止跨模块残留影响total断言）
    conn.execute("DELETE FROM slow_queries")
    conn.execute("DELETE FROM scan_tasks")
    conn.commit()

    now = "2026-06-17 10:00:00"
    # 插入3个SET的模拟慢SQL数据
    test_data = [
        # set_1: 3条
        ("SELECT * FROM t_order WHERE uid=?", "SELECT * FROM t_order WHERE uid=123", "tdsql_check", "set_1", 500, 200.5, 150.0, 300.0, 800000, 100, "全表扫描", "ERROR", "", "", "{}", 1, now, now, "pending"),
        ("UPDATE t_user SET name=? WHERE id=?", "UPDATE t_user SET name='test' WHERE id=1", "tdsql_check", "set_1", 200, 100.0, 80.0, 150.0, 50000, 1, "锁等待严重", "WARNING", "", "", "{}", 2, now, now, "pending"),
        ("SELECT * FROM t_order WHERE uid=?", "SELECT * FROM t_order WHERE uid=456", "tdsql_check", "set_1", 300, 180.0, 120.0, 250.0, 600000, 50, "全表扫描", "ERROR", "", "", "{}", 1, now, now, "pending"),
        # set_2: 2条
        ("SELECT * FROM t_order WHERE uid=?", "SELECT * FROM t_order WHERE uid=789", "tdsql_check", "set_2", 400, 220.0, 160.0, 280.0, 700000, 80, "全表扫描", "ERROR", "", "", "{}", 1, now, now, "pending"),
        ("DELETE FROM t_log WHERE created<?", "DELETE FROM t_log WHERE created<'2026-01-01'", "tdsql_check", "set_2", 50, 500.0, 400.0, 600.0, 2000000, 0, "扫描行数过多", "WARNING", "", "", "{}", 2, now, now, "pending"),
        # set_3: 1条
        ("SELECT * FROM t_order WHERE uid=?", "SELECT * FROM t_order WHERE uid=999", "tdsql_check", "set_3", 600, 300.0, 200.0, 400.0, 900000, 120, "全表扫描", "ERROR", "", "", "{}", 1, now, now, "pending"),
    ]
    for row in test_data:
        conn.execute("""
            INSERT INTO slow_queries (fingerprint, sql_text, db_name, set_id, exec_count,
                total_time_ms, avg_time_ms, max_time_ms, rows_examined, rows_sent,
                problem_type, severity, root_cause, suggestion, analysis_json,
                scan_task_id, first_seen, last_seen, status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, row)
    conn.commit()
    conn.close()

    yield "mysql"

    # 清理种子数据
    conn = _get_connection()
    conn.execute("DELETE FROM slow_queries WHERE db_name = 'tdsql_check'")
    conn.commit()
    conn.close()


@pytest.fixture(scope="module")
def client(test_db):
    """创建测试客户端"""
    from backend.main import app
    from backend.api import tdsql_manage
    # Mock连接池
    mock_pool = MagicMock()
    mock_pool.config = MagicMock()
    mock_pool.config.database = "tdsql_check"
    mock_pool.config.host = "192.168.1.100"
    mock_pool.config.port = 3306
    tdsql_manage._pool = mock_pool

    with TestClient(app) as c:
        yield c
    # 清理V1.0兼容测试席位，避免污染后续"未连接"用例
    tdsql_manage._pool = None


# ── 1. SET发现机制 ──────────────────────────────────────

class TestSetDiscovery:
    """测试SET发现逻辑（仍用于拓扑展示）"""

    def test_build_set_hint_normal(self):
        """测试_build_set_hint: 正常SET ID"""
        from backend.services.tdsql_connector import TDSQLConnectionPool
        hint = TDSQLConnectionPool._build_set_hint("set_1")
        assert hint == "/*sets:set_1*/"

    def test_build_set_hint_empty(self):
        """测试_build_set_hint: 空SET ID"""
        from backend.services.tdsql_connector import TDSQLConnectionPool
        hint = TDSQLConnectionPool._build_set_hint(None)
        assert hint == ""

    def test_build_set_hint_multi(self):
        """测试_build_set_hint: 多SET"""
        from backend.services.tdsql_connector import TDSQLConnectionPool
        hint = TDSQLConnectionPool._build_set_hint("set_1,set_2,set_3")
        assert hint == "/*sets:set_1,set_2,set_3*/"

    def test_discover_sets_with_proxy_status(self):
        """测试discover_sets: 通过/*proxy*/show status发现SET"""
        from backend.services.tdsql_connector import TDSQLConnectionPool, TDSQLConnectionConfig
        config = TDSQLConnectionConfig(host="mock", port=3306)
        pool = TDSQLConnectionPool(config)

        # Mock _execute 返回proxy status格式
        mock_rows = [
            {"Variable_name": "set_1_status", "Value": "set_1:192.168.1.1:3306"},
            {"Variable_name": "set_2_status", "Value": "set_2:192.168.1.2:3306"},
            {"Variable_name": "set_3_status", "Value": "set_3:192.168.1.3:3306"},
        ]
        pool._execute = MagicMock(return_value=mock_rows)

        sets = pool.discover_sets()
        assert len(sets) == 3
        set_ids = [s["set_id"] for s in sets]
        assert "set_1" in set_ids
        assert "set_2" in set_ids
        assert "set_3" in set_ids

    def test_discover_sets_empty_for_non_distributed(self):
        """测试discover_sets: 非分布式实例返回空列表"""
        from backend.services.tdsql_connector import TDSQLConnectionPool, TDSQLConnectionConfig
        config = TDSQLConnectionConfig(host="mock", port=3306)
        pool = TDSQLConnectionPool(config)

        # Mock _execute 返回空或非SET相关数据
        pool._execute = MagicMock(return_value=[
            {"Variable_name": "version", "Value": "5.7.17"},
        ])

        sets = pool.discover_sets()
        assert len(sets) == 0

    def test_discover_sets_handles_exception(self):
        """测试discover_sets: 异常时返回空列表"""
        from backend.services.tdsql_connector import TDSQLConnectionPool, TDSQLConnectionConfig
        config = TDSQLConnectionConfig(host="mock", port=3306)
        pool = TDSQLConnectionPool(config)

        pool._execute = MagicMock(side_effect=Exception("Connection refused"))

        sets = pool.discover_sets()
        assert len(sets) == 0


# ── 2. Proxy层直查（无SET路由） ──────────────────────────

class TestProxyDirectQuery:
    """测试digest和processlist直接通过Proxy查询（不使用SET hint）"""

    def test_digest_no_set_hint(self):
        """测试digest查询不包含SET路由hint"""
        from backend.services.tdsql_connector import TDSQLConnectionPool, TDSQLConnectionConfig
        config = TDSQLConnectionConfig(host="mock", port=3306)
        pool = TDSQLConnectionPool(config)

        captured_sql = []
        def mock_execute(sql, params=None):
            captured_sql.append(sql)
            return []

        pool._execute = MagicMock(side_effect=mock_execute)
        pool.get_slow_queries_from_digest(limit=10)

        assert "/*sets:" not in captured_sql[0]

    def test_digest_with_set_id_param_ignored(self):
        """测试digest传入set_id参数时仍不添加hint（参数已废弃）"""
        from backend.services.tdsql_connector import TDSQLConnectionPool, TDSQLConnectionConfig
        config = TDSQLConnectionConfig(host="mock", port=3306)
        pool = TDSQLConnectionPool(config)

        captured_sql = []
        def mock_execute(sql, params=None):
            captured_sql.append(sql)
            return []

        pool._execute = MagicMock(side_effect=mock_execute)
        pool.get_slow_queries_from_digest(limit=10, set_id="set_3")

        # set_id参数已废弃，不应添加hint
        assert "/*sets:" not in captured_sql[0]

    def test_processlist_no_set_hint(self):
        """测试processlist查询不包含SET路由hint"""
        from backend.services.tdsql_connector import TDSQLConnectionPool, TDSQLConnectionConfig
        config = TDSQLConnectionConfig(host="mock", port=3306)
        pool = TDSQLConnectionPool(config)

        captured_sql = []
        def mock_execute(sql, params=None):
            captured_sql.append(sql)
            return []

        pool._execute = MagicMock(side_effect=mock_execute)
        pool.get_slow_queries_from_processlist(min_time=5)

        assert "/*sets:" not in captured_sql[0]

    def test_digest_min_time_filter(self):
        """测试digest的min_time过滤功能"""
        from backend.services.tdsql_connector import TDSQLConnectionPool, TDSQLConnectionConfig
        config = TDSQLConnectionConfig(host="mock", port=3306)
        pool = TDSQLConnectionPool(config)

        captured_params = []
        def mock_execute(sql, params=None):
            captured_params.append(params)
            return []

        pool._execute = MagicMock(side_effect=mock_execute)
        pool.get_slow_queries_from_digest(limit=10, min_time=0.5)

        # params中应包含min_time值
        assert 0.5 in captured_params[0]


# ── 3. API端点测试 ──────────────────────────────────────

class TestSetAPIEndpoints:
    """测试API端点"""

    def test_get_set_ids(self, client):
        """测试 GET /api/v1/slow-queries/set-ids"""
        resp = client.get("/api/v1/slow-queries/set-ids")
        assert resp.status_code == 200
        data = resp.json()
        assert "set_ids" in data
        # 应该有 set_1, set_2, set_3（从预存数据）
        assert "set_1" in data["set_ids"]
        assert "set_2" in data["set_ids"]
        assert "set_3" in data["set_ids"]

    def test_cross_set_analysis(self, client):
        """测试 GET /api/v1/slow-queries/cross-set-analysis"""
        resp = client.get("/api/v1/slow-queries/cross-set-analysis?scan_task_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert "set_distribution" in data
        assert "hot_sets" in data
        assert "cross_set_sqls" in data
        assert "advice" in data
        # 应该有3个SET
        assert len(data["set_distribution"]) == 3

    def test_cross_set_analysis_without_task_id(self, client):
        """测试 cross-set-analysis 不传scan_task_id时返回400"""
        resp = client.get("/api/v1/slow-queries/cross-set-analysis")
        assert resp.status_code == 400

    def test_sets_discovery_endpoint(self, client):
        """测试 GET /api/v1/tdsql/sets"""
        from backend.api import tdsql_manage
        # Mock discover_sets
        tdsql_manage._pool.discover_sets = MagicMock(return_value=[
            {"set_id": "set_1", "set_name": "set_1"},
            {"set_id": "set_2", "set_name": "set_2"},
        ])
        resp = client.get("/api/v1/tdsql/sets")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["sets"]) == 2

    def test_sets_discovery_empty(self, client):
        """测试 GET /api/v1/tdsql/sets 非分布式实例返回空"""
        from backend.api import tdsql_manage
        tdsql_manage._pool.discover_sets = MagicMock(return_value=[])
        resp = client.get("/api/v1/tdsql/sets")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0


# ── 4. set_id筛选测试 ───────────────────────────────────

class TestSetIdFilter:
    """测试set_id筛选功能（基于已有数据）"""

    def test_filter_by_set_id(self, client):
        """测试按SET筛选慢SQL"""
        resp = client.get("/api/v1/slow-queries?set_id=set_1&limit=100")
        assert resp.status_code == 200
        data = resp.json()
        # set_1 应该有3条
        assert data["total"] == 3
        for item in data["items"]:
            assert item["set_id"] == "set_1"

    def test_filter_by_set_id_2(self, client):
        """测试按SET筛选慢SQL - set_2"""
        resp = client.get("/api/v1/slow-queries?set_id=set_2&limit=100")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        for item in data["items"]:
            assert item["set_id"] == "set_2"

    def test_filter_without_set_id(self, client):
        """测试不按SET筛选时返回所有"""
        resp = client.get("/api/v1/slow-queries?limit=100")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 6  # 所有SET的数据


# ── 5. 跨SET分析逻辑测试 ───────────────────────────────

class TestCrossSetAnalysis:
    """测试跨SET分析逻辑"""

    def test_hot_set_detection(self, client):
        """测试热点SET识别"""
        resp = client.get("/api/v1/slow-queries/cross-set-analysis?scan_task_id=1")
        data = resp.json()
        # set_1有3条, set_2有2条, set_3有1条
        dist = data["set_distribution"]
        if dist:
            totals = {sid: info["total"] for sid, info in dist.items()}
            avg = sum(totals.values()) / len(totals)
            for hs in data["hot_sets"]:
                assert totals[hs["set_id"]] > avg * 1.5

    def test_cross_set_sql_detection(self, client):
        """测试跨SET共现SQL识别"""
        resp = client.get("/api/v1/slow-queries/cross-set-analysis?scan_task_id=1")
        data = resp.json()
        # "SELECT * FROM t_order WHERE uid=?" 出现在 set_1, set_2, set_3
        cross_sqls = data["cross_set_sqls"]
        if cross_sqls:
            fingerprints = [cs["fingerprint"] for cs in cross_sqls]
            assert "SELECT * FROM t_order WHERE uid=?" in fingerprints
            # 找到该指纹的记录，应该出现在3个SET
            for cs in cross_sqls:
                if cs["fingerprint"] == "SELECT * FROM t_order WHERE uid=?":
                    assert cs["set_count"] == 3

    def test_advice_generation(self, client):
        """测试顾问建议生成"""
        resp = client.get("/api/v1/slow-queries/cross-set-analysis?scan_task_id=1")
        data = resp.json()
        assert isinstance(data["advice"], str)
        assert len(data["advice"]) > 0


# ── 6. 慢SQL扫描API测试（新架构） ─────────────────────────

class TestFetchSlowQueries:
    """测试fetch_slow_queries端点（Proxy直查架构）"""

    def test_fetch_digest_source(self, client):
        """测试digest数据源扫描"""
        from backend.api import tdsql_manage

        tdsql_manage._pool.get_slow_queries_from_digest = MagicMock(return_value=[
            {"DIGEST_TEXT": "SELECT * FROM t1 WHERE id=?", "SCHEMA_NAME": "db1",
             "COUNT_STAR": 100, "total_seconds": 5.0, "avg_seconds": 0.5,
             "max_seconds": 2.0, "SUM_ROWS_EXAMINED": 10000, "SUM_ROWS_SENT": 10,
             "no_index_count": 5,
             "FIRST_SEEN": "2026-06-17 08:00:00", "LAST_SEEN": "2026-06-17 09:00:00"},
            {"DIGEST_TEXT": "SELECT * FROM t2 WHERE name=?", "SCHEMA_NAME": "db1",
             "COUNT_STAR": 200, "total_seconds": 10.0, "avg_seconds": 1.0,
             "max_seconds": 3.0, "SUM_ROWS_EXAMINED": 20000, "SUM_ROWS_SENT": 20,
             "no_index_count": 0,
             "FIRST_SEEN": "2026-06-17 08:00:00", "LAST_SEEN": "2026-06-17 09:00:00"},
        ])

        resp = client.post("/api/v1/tdsql/slow-queries/fetch", json={
            "source": "digest",
            "limit": 50,
            "min_time": 0.1,
            "time_window_start": "2026-06-17 00:00:00",
            "time_window_end": "2026-06-17 23:59:59",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "digest"
        assert data["fetched"] == 2
        assert "scan_task_id" in data
        assert isinstance(data["errors"], list)
        assert isinstance(data["results"], list)

    def test_fetch_processlist_source(self, client):
        """测试processlist数据源扫描"""
        from backend.api import tdsql_manage

        # processlist扫描自slow_log重构后走poll_processlist（多次轮询采样）
        tdsql_manage._pool.poll_processlist = MagicMock(return_value=[
            {"id": 1, "user": "root", "host": "localhost", "db": "test",
             "command": "Query", "time": 15, "state": "Sending data",
             "info": "SELECT * FROM t1 WHERE id=1"},
        ])

        resp = client.post("/api/v1/tdsql/slow-queries/fetch", json={
            "source": "processlist",
            "limit": 50,
            "min_time": 5,
            "time_window_start": "2026-06-17 00:00:00",
            "time_window_end": "2026-06-17 23:59:59",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "processlist"
        assert data["fetched"] == 1

    def test_fetch_slow_log_rejected(self, client):
        """测试slow_log数据源被拒绝（返回400）"""
        from backend.api import tdsql_manage
        tdsql_manage._pool.config = MagicMock()
        tdsql_manage._pool.config.database = "tdsql_check"
        tdsql_manage._pool.config.host = "192.168.1.100"
        tdsql_manage._pool.config.port = 3306

        resp = client.post("/api/v1/tdsql/slow-queries/fetch", json={
            "source": "slow_log",
            "limit": 10,
            "min_time": 1.0,
            "time_window_start": "2026-06-17 00:00:00",
            "time_window_end": "2026-06-17 23:59:59",
        })
        assert resp.status_code == 400
        assert "slow_log" in resp.json()["detail"].lower() or "不支持" in resp.json()["detail"]

    def test_fetch_no_scan_all_sets_param(self, client):
        """测试新API不接受scan_all_sets参数（忽略而非报错）"""
        from backend.api import tdsql_manage

        tdsql_manage._pool.get_slow_queries_from_digest = MagicMock(return_value=[])

        resp = client.post("/api/v1/tdsql/slow-queries/fetch", json={
            "source": "digest",
            "limit": 10,
            "min_time": 0.1,
            "time_window_start": "2026-06-17 00:00:00",
            "time_window_end": "2026-06-17 23:59:59",
            "scan_all_sets": True,  # 旧参数，应被忽略
        })
        # 不应报错，只是忽略多余参数
        assert resp.status_code == 200

    def test_fetch_response_structure(self, client):
        """测试响应结构符合新架构"""
        from backend.api import tdsql_manage

        tdsql_manage._pool.get_slow_queries_from_digest = MagicMock(return_value=[
            {"DIGEST_TEXT": "SELECT 1", "SCHEMA_NAME": "db", "COUNT_STAR": 1,
             "total_seconds": 0.1, "avg_seconds": 0.1, "max_seconds": 0.1,
             "SUM_ROWS_EXAMINED": 1, "SUM_ROWS_SENT": 1,
             "FIRST_SEEN": "2026-06-17 08:00:00", "LAST_SEEN": "2026-06-17 09:00:00"}
        ])

        resp = client.post("/api/v1/tdsql/slow-queries/fetch", json={
            "source": "digest",
            "limit": 10,
            "min_time": 0.1,
            "time_window_start": "2026-06-17 00:00:00",
            "time_window_end": "2026-06-17 23:59:59",
        })
        data = resp.json()
        # 新架构响应字段
        assert "source" in data
        assert "fetched" in data
        assert "scan_task_id" in data
        assert "errors" in data
        assert "results" in data
        # 旧架构字段不应存在
        assert "set_count" not in data
        assert "sets_scanned" not in data
        assert "errors_per_set" not in data

    def test_fetch_error_handling(self, client):
        """测试扫描异常时的错误处理"""
        from backend.api import tdsql_manage

        tdsql_manage._pool.get_slow_queries_from_digest = MagicMock(
            side_effect=Exception("Connection timeout"))

        resp = client.post("/api/v1/tdsql/slow-queries/fetch", json={
            "source": "digest",
            "limit": 10,
            "min_time": 0.1,
            "time_window_start": "2026-06-17 00:00:00",
            "time_window_end": "2026-06-17 23:59:59",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["fetched"] == 0
        assert len(data["errors"]) == 1
        assert "Connection timeout" in data["errors"][0]["error"]

    def test_fetch_digest_requires_time_window(self, client):
        """测试digest模式要求时间窗口"""
        from backend.api import tdsql_manage

        resp = client.post("/api/v1/tdsql/slow-queries/fetch", json={
            "source": "digest",
            "limit": 10,
            "min_time": 0.1,
        })
        assert resp.status_code == 422


# ── 7. SlowQueryRecord set_id字段测试 ──────────────────

class TestSlowQueryRecordSetId:
    """测试SlowQueryRecord的set_id字段"""

    def test_record_with_set_id(self):
        """测试SlowQueryRecord包含set_id字段"""
        from backend.engine.slow_analyzer import SlowQueryRecord
        record = SlowQueryRecord(
            fingerprint="SELECT * FROM t1 WHERE id=?",
            sql_text="SELECT * FROM t1 WHERE id=1",
            db_name="test_db",
            set_id="set_5",
            exec_count=100,
            total_time_ms=5000,
            avg_time_ms=50,
            max_time_ms=200,
            rows_examined=10000,
            rows_sent=100,
        )
        assert record.set_id == "set_5"

    def test_record_default_set_id(self):
        """测试SlowQueryRecord默认set_id为空字符串"""
        from backend.engine.slow_analyzer import SlowQueryRecord
        record = SlowQueryRecord()
        assert record.set_id == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
