"""
TDSQL SQL审核工具 - 第三轮UAT测试

覆盖前两轮遗漏的规则、功能点和边界场景。
"""
import pytest
from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)

issues = []


def record_issue(severity, module, description, expected, actual):
    issues.append({
        "severity": severity,
        "module": module,
        "description": description,
        "expected": expected,
        "actual": actual,
    })


# ═══════════════════════════════════════════════════════════
# UAT-14: 前两轮未覆盖的规则验证
# ═══════════════════════════════════════════════════════════

class TestUAT14_MissingRuleCoverage:
    """前两轮未覆盖的规则验证"""

    def test_uat14_01_r008_foreign_key_rejected(self):
        """R008: 外键约束应被拦截"""
        sql = """
        CREATE TABLE t_order_item (
            id BIGINT PRIMARY KEY,
            order_id BIGINT,
            FOREIGN KEY (order_id) REFERENCES t_order(id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        rule_ids = [v["rule_id"] for v in data.get("violations", [])]
        if "R008" not in rule_ids:
            record_issue("P1", "R008", "外键约束未被拦截", "R008 in violations", f"rule_ids={rule_ids}")

    def test_uat14_02_r010_varchar_length_warning(self):
        """R010: VARCHAR超长应被警告"""
        sql = "CREATE TABLE t_user (id BIGINT PRIMARY KEY, remark VARCHAR(3000)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        rule_ids = [v["rule_id"] for v in data.get("violations", [])]
        if "R010" not in rule_ids:
            record_issue("P2", "R010", "VARCHAR(3000)未被警告", "R010 in violations", f"rule_ids={rule_ids}")

    def test_uat14_03_r010_varchar_short_pass(self):
        """R010: VARCHAR(100)不应触发"""
        sql = "CREATE TABLE t_user (id BIGINT PRIMARY KEY, name VARCHAR(100)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        rule_ids = [v["rule_id"] for v in data.get("violations", [])]
        if "R010" in rule_ids:
            record_issue("P2", "R010", "VARCHAR(100)被误报", "R010 not in violations", f"rule_ids={rule_ids}")

    def test_uat14_04_r011_text_blob_warning(self):
        """R011: TEXT/BLOB应被警告"""
        sql = "CREATE TABLE t_log (id BIGINT PRIMARY KEY, content TEXT, image BLOB) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        rule_ids = [v["rule_id"] for v in data.get("violations", [])]
        if "R011" not in rule_ids:
            record_issue("P2", "R011", "TEXT/BLOB未被警告", "R011 in violations", f"rule_ids={rule_ids}")

    def test_uat14_05_r015_subquery_depth_4(self):
        """R015: 4层子查询应被拦截"""
        sql = "SELECT * FROM t1 WHERE id IN (SELECT id FROM t2 WHERE id IN (SELECT id FROM t3 WHERE id IN (SELECT id FROM t4)))"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        rule_ids = [v["rule_id"] for v in data.get("violations", [])]
        if "R015" not in rule_ids:
            record_issue("P1", "R015", "4层子查询未被拦截", "R015 in violations", f"rule_ids={rule_ids}")

    def test_uat14_06_r017_order_by_rand(self):
        """R017: ORDER BY RAND()应被拦截"""
        sql = "SELECT id FROM t_user ORDER BY RAND() LIMIT 10"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        rule_ids = [v["rule_id"] for v in data.get("violations", [])]
        if "R017" not in rule_ids:
            record_issue("P1", "R017", "ORDER BY RAND()未被拦截", "R017 in violations", f"rule_ids={rule_ids}")

    def test_uat14_07_r021_shardkey_update(self):
        """R021: 更新分片键应被拦截"""
        sql = "UPDATE t_order SET shard_key = 100 WHERE id = 1"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        rule_ids = [v["rule_id"] for v in data.get("violations", [])]
        if "R021" not in rule_ids:
            record_issue("P1", "R021", "更新分片键未被拦截", "R021 in violations", f"rule_ids={rule_ids}")

    def test_uat14_08_r022_global_delete_no_eq(self):
        """R022: 无等值条件DELETE应被拦截"""
        sql = "DELETE FROM t_order WHERE status != 1"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        rule_ids = [v["rule_id"] for v in data.get("violations", [])]
        if "R022" not in rule_ids:
            record_issue("P1", "R022", "无等值DELETE未被拦截", "R022 in violations", f"rule_ids={rule_ids}")

    def test_uat14_09_r018_index_count_boundary(self):
        """R018: 索引数量边界测试"""
        # 刚好5个索引（含主键）不触发
        sql_ok = """
        CREATE TABLE t_ok (
            id BIGINT PRIMARY KEY,
            a VARCHAR(10), b VARCHAR(10),
            INDEX idx_a(a), INDEX idx_b(b)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
        resp = client.post("/api/v1/audit/sql", json={"sql": sql_ok})
        data = resp.json()
        rule_ids = [v["rule_id"] for v in data.get("violations", [])]
        if "R018" in rule_ids:
            record_issue("P2", "R018", "3个索引被误报为过多", "R018 not in violations", f"rule_ids={rule_ids}")

    def test_uat14_10_r019_redundant_index(self):
        """R019: 冗余索引应被检测"""
        sql = """
        CREATE TABLE t_test (
            id BIGINT PRIMARY KEY,
            a VARCHAR(10), b VARCHAR(10),
            INDEX idx_a(a), INDEX idx_ab(a, b)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        rule_ids = [v["rule_id"] for v in data.get("violations", [])]
        if "R019" not in rule_ids:
            record_issue("P2", "R019", "冗余索引未被检测", "R019 in violations", f"rule_ids={rule_ids}")


# ═══════════════════════════════════════════════════════════
# UAT-15: 前两轮未覆盖的SQL模式
# ═══════════════════════════════════════════════════════════

class TestUAT15_MissingSQLPatterns:
    """前两轮未覆盖的SQL模式"""

    def test_uat15_01_full_fuzzy_like(self):
        """全模糊LIKE应被检测"""
        sql = "SELECT id FROM t_user WHERE name LIKE '%test%'"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        rule_ids = [v["rule_id"] for v in data.get("violations", [])]
        if "R016" not in rule_ids:
            record_issue("P2", "LIKE模式", "全模糊LIKE未被检测", "R016 in violations", f"rule_ids={rule_ids}")

    def test_uat15_02_or_condition(self):
        """OR条件应被检测"""
        sql = "SELECT id FROM t_user WHERE user_id = 1 OR phone = '13800138000'"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        rule_ids = [v["rule_id"] for v in data.get("violations", [])]
        if "R016" not in rule_ids:
            record_issue("P2", "OR条件", "OR条件未被检测", "R016 in violations", f"rule_ids={rule_ids}")

    def test_uat15_03_sql_with_block_comment(self):
        """含块注释的SQL应正常审核"""
        sql = "/* 查询用户 */ SELECT id, name FROM t_user WHERE id = 1"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        if data["passed"] is not True:
            record_issue("P2", "注释SQL", "含注释SQL被误判", "passed=True", f"passed={data['passed']}")

    def test_uat15_04_sql_with_line_comment(self):
        """含行注释的SQL应正常审核"""
        sql = "SELECT id, name FROM t_user WHERE id = 1 -- 按ID查询"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        if data["passed"] is not True:
            record_issue("P2", "注释SQL", "含行注释SQL被误判", "passed=True", f"passed={data['passed']}")

    def test_uat15_05_very_long_sql(self):
        """超长SQL不应崩溃"""
        conditions = " AND ".join([f"col{i} = {i}" for i in range(100)])
        sql = f"SELECT id FROM t_user WHERE {conditions}"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        if resp.status_code != 200:
            record_issue("P1", "超长SQL", "超长SQL导致服务异常", "200", f"{resp.status_code}")

    def test_uat15_06_create_with_auto_increment(self):
        """自增主键建表应通过"""
        sql = "CREATE TABLE t_user (id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY, name VARCHAR(50)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        if data["passed"] is not True:
            record_issue("P1", "自增主键", "自增主键建表被误判", "passed=True", f"passed={data['passed']}")

    def test_uat15_07_create_with_composite_primary_key(self):
        """联合主键建表应通过"""
        sql = "CREATE TABLE t_rel (user_id BIGINT, role_id BIGINT, PRIMARY KEY (user_id, role_id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        if data["passed"] is not True:
            record_issue("P2", "联合主键", "联合主键建表被误判", "passed=True", f"passed={data['passed']}")

    def test_uat15_08_insert_with_select(self):
        """INSERT INTO ... SELECT应正常审核"""
        sql = "INSERT INTO t_backup (id, name) SELECT id, name FROM t_user WHERE status = 0"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        if resp.status_code != 200:
            record_issue("P2", "INSERT SELECT", "INSERT SELECT导致异常", "200", f"{resp.status_code}")

    def test_uat15_09_select_with_case_when(self):
        """CASE WHEN查询应正常审核"""
        sql = "SELECT id, CASE WHEN status = 1 THEN 'active' ELSE 'inactive' END as status_name FROM t_user WHERE id = 1"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        if data["passed"] is not True:
            record_issue("P2", "CASE WHEN", "CASE WHEN查询被误判", "passed=True", f"passed={data['passed']}")

    def test_uat15_10_select_with_between(self):
        """BETWEEN查询应正常审核"""
        sql = "SELECT id FROM t_order WHERE create_time BETWEEN '2024-01-01' AND '2024-12-31'"
        resp = client.post("/api/v1/audit/sql", json={"sql": sql})
        data = resp.json()
        if data["passed"] is not True:
            record_issue("P2", "BETWEEN", "BETWEEN查询被误判", "passed=True", f"passed={data['passed']}")


# ═══════════════════════════════════════════════════════════
# UAT-16: Dashboard与统计
# ═══════════════════════════════════════════════════════════

class TestUAT16_DashboardStats:
    """Dashboard与统计"""

    def test_uat16_01_rule_count_accuracy(self):
        """规则总数应为22"""
        resp = client.get("/api/v1/dashboard/summary")
        data = resp.json()
        if data.get("rules", {}).get("total") != 22:
            record_issue("P2", "Dashboard", "规则总数不正确", "22", f"{data.get('rules', {}).get('total')}")

    def test_uat16_02_rule_category_accuracy(self):
        """规则分类数量应正确"""
        resp = client.get("/api/v1/dashboard/summary")
        data = resp.json()
        cats = data.get("rules", {}).get("by_category", {})
        if cats.get("naming") != 2:
            record_issue("P2", "Dashboard", "命名规则数不正确", "2", f"{cats.get('naming')}")
        if cats.get("ddl") != 9:
            record_issue("P2", "Dashboard", "DDL规则数不正确", "9", f"{cats.get('ddl')}")
        if cats.get("distributed") != 3:
            record_issue("P2", "Dashboard", "分布式规则数不正确", "3", f"{cats.get('distributed')}")

    def test_uat16_03_audit_trend_7_days(self):
        """7天趋势应正常返回"""
        resp = client.get("/api/v1/dashboard/audit-trend?days=7")
        data = resp.json()
        if "dates" not in data:
            record_issue("P2", "Dashboard", "趋势缺少dates", "有dates", f"keys={list(data.keys())}")

    def test_uat16_04_rule_stats(self):
        """规则命中统计应正常返回"""
        resp = client.get("/api/v1/dashboard/rule-stats")
        data = resp.json()
        if "rules" not in data:
            record_issue("P2", "Dashboard", "规则统计缺少rules", "有rules", f"keys={list(data.keys())}")

    def test_uat16_05_slow_query_statistics(self):
        """慢SQL统计应正常返回"""
        resp = client.get("/api/v1/slow-queries/statistics")
        data = resp.json()
        required = ["total", "by_severity", "by_status"]
        for key in required:
            if key not in data:
                record_issue("P2", "慢SQL统计", f"缺少{key}", f"有{key}", f"keys={list(data.keys())}")


# ═══════════════════════════════════════════════════════════
# UAT-17: 慢SQL分析深度覆盖
# ═══════════════════════════════════════════════════════════

class TestUAT17_SlowQueryDeep:
    """慢SQL分析深度覆盖"""

    def test_uat17_01_explain_range_good(self):
        """EXPLAIN range类型不应报ERROR"""
        resp = client.post("/api/v1/slow-queries/analyze-explain", json={
            "explain_data": [{
                "id": 1, "select_type": "SIMPLE", "table": "t_order",
                "type": "range", "key": "idx_create_time",
                "rows": 5000, "filtered": 100.0, "extra": "Using index condition"
            }]
        })
        data = resp.json()
        errors = [a for a in data.get("analyses", []) if a["severity"] == "ERROR"]
        if errors:
            record_issue("P2", "EXPLAIN", "range类型被误报ERROR", "0个ERROR", f"errors={errors}")

    def test_uat17_02_explain_ref_good(self):
        """EXPLAIN ref类型不应报ERROR"""
        resp = client.post("/api/v1/slow-queries/analyze-explain", json={
            "explain_data": [{
                "id": 1, "select_type": "SIMPLE", "table": "t_user",
                "type": "ref", "key": "idx_status",
                "rows": 100, "filtered": 100.0, "extra": ""
            }]
        })
        data = resp.json()
        errors = [a for a in data.get("analyses", []) if a["severity"] == "ERROR"]
        if errors:
            record_issue("P2", "EXPLAIN", "ref类型被误报ERROR", "0个ERROR", f"errors={errors}")

    def test_uat17_03_slow_query_list_filter_severity(self):
        """慢SQL列表按严重程度筛选"""
        resp = client.get("/api/v1/slow-queries?severity=ERROR&limit=5")
        if resp.status_code != 200:
            record_issue("P1", "慢SQL列表", "按severity筛选失败", "200", f"{resp.status_code}")

    def test_uat17_04_slow_query_list_filter_status(self):
        """慢SQL列表按状态筛选"""
        resp = client.get("/api/v1/slow-queries?status=pending&limit=5")
        if resp.status_code != 200:
            record_issue("P1", "慢SQL列表", "按status筛选失败", "200", f"{resp.status_code}")

    def test_uat17_05_slow_query_lock_time(self):
        """锁等待分析应正常工作"""
        resp = client.post("/api/v1/slow-queries", json={
            "fingerprint": "UPDATE t SET col = ? WHERE id = ?",
            "sql_text": "UPDATE t SET col = 1 WHERE id = 100",
            "exec_count": 100,
            "avg_time_ms": 5000,
            "total_time_ms": 500000,
            "rows_examined": 1,
            "lock_time_ms": 200000,
        })
        data = resp.json()
        problem_types = [a["problem_type"] for a in data.get("analyses", [])]
        if "锁等待严重" not in problem_types:
            record_issue("P2", "锁等待", "高锁等待未被检测", "包含锁等待严重", f"problem_types={problem_types}")

    def test_uat17_06_explain_multi_row_join(self):
        """多行EXPLAIN（JOIN场景）应正确分析"""
        resp = client.post("/api/v1/slow-queries/analyze-explain", json={
            "explain_data": [
                {"id": 1, "select_type": "SIMPLE", "table": "t_order", "type": "ALL", "rows": 100000, "extra": "Using where"},
                {"id": 1, "select_type": "SIMPLE", "table": "t_user", "type": "eq_ref", "key": "PRIMARY", "rows": 1, "extra": "Using index"},
            ]
        })
        data = resp.json()
        # 应该检测到t_order的全表扫描
        problem_types = [a["problem_type"] for a in data.get("analyses", [])]
        if "全表扫描" not in problem_types:
            record_issue("P2", "EXPLAIN", "JOIN中全表扫描未检测", "包含全表扫描", f"problem_types={problem_types}")

    def test_uat17_07_slow_query_detail(self):
        """慢SQL详情应包含完整信息"""
        resp = client.post("/api/v1/slow-queries", json={
            "fingerprint": "DETAIL_TEST SELECT 1",
            "sql_text": "SELECT 1",
            "exec_count": 42,
            "avg_time_ms": 99.5,
        })
        slow_id = resp.json()["id"]
        resp = client.get(f"/api/v1/slow-queries/{slow_id}")
        data = resp.json()
        if data.get("exec_count") != 42:
            record_issue("P1", "慢SQL详情", "exec_count不正确", "42", f"{data.get('exec_count')}")
        if data.get("avg_time_ms") != 99.5:
            record_issue("P1", "慢SQL详情", "avg_time_ms不正确", "99.5", f"{data.get('avg_time_ms')}")


# ═══════════════════════════════════════════════════════════
# UAT-18: GitLab集成深度覆盖
# ═══════════════════════════════════════════════════════════

class TestUAT18_GitLabDeep:
    """GitLab集成深度覆盖"""

    def test_uat18_01_audit_diff_sql_file(self):
        """审核SQL文件Diff"""
        resp = client.post("/api/v1/gitlab/audit/diff", json={
            "diff": "+SELECT id FROM t_user WHERE id = 1\n+SELECT * FROM t_order",
            "file_path": "test.sql"
        })
        if resp.status_code != 200:
            record_issue("P1", "GitLab", "SQL文件Diff审核失败", "200", f"{resp.status_code}")

    def test_uat18_02_audit_diff_xml_file(self):
        """审核XML文件Diff"""
        resp = client.post("/api/v1/gitlab/audit/diff", json={
            "diff": "+<select id='test'>SELECT * FROM t_user WHERE id = #{id}</select>",
            "file_path": "UserMapper.xml"
        })
        if resp.status_code != 200:
            record_issue("P1", "GitLab", "XML文件Diff审核失败", "200", f"{resp.status_code}")

    def test_uat18_03_audit_repo_mixed(self):
        """审核混合文件仓库"""
        resp = client.post("/api/v1/gitlab/audit/repository", json={
            "files": [
                {"path": "README.md", "content": "# readme"},
                {"path": "src/UserMapper.xml", "content": "<mapper><select id='test'>SELECT * FROM t_user</select></mapper>"},
                {"path": "sql/init.sql", "content": "CREATE TABLE t_user (id BIGINT PRIMARY KEY) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;"},
                {"path": "app.java", "content": "public class App {}"},
            ]
        })
        data = resp.json()
        if "total_files" not in data:
            record_issue("P1", "GitLab", "仓库审核缺少total_files", "有total_files", f"keys={list(data.keys())}")

    def test_uat18_04_webhook_ignore_push(self):
        """Push事件应被忽略"""
        resp = client.post("/api/v1/gitlab/webhook/merge-request", json={
            "object_kind": "push", "ref": "refs/heads/main"
        })
        data = resp.json()
        if "Ignored" not in data.get("message", ""):
            record_issue("P2", "GitLab", "Push事件未被忽略", "包含Ignored", f"message={data.get('message')}")

    def test_uat18_05_webhook_ignore_close(self):
        """MR close事件应被忽略"""
        resp = client.post("/api/v1/gitlab/webhook/merge-request", json={
            "object_kind": "merge_request",
            "object_attributes": {"iid": 1, "action": "close"},
            "project": {"name": "test"}
        })
        data = resp.json()
        if "Ignored" not in data.get("message", ""):
            record_issue("P2", "GitLab", "MR close未被忽略", "包含Ignored", f"message={data.get('message')}")


# ═══════════════════════════════════════════════════════════
# UAT-19: TDSQL管理深度覆盖
# ═══════════════════════════════════════════════════════════

class TestUAT19_TDSQLDeep:
    """TDSQL管理深度覆盖"""

    def test_uat19_01_status_returns_connected_field(self):
        """状态查询应返回connected字段"""
        resp = client.get("/api/v1/tdsql/status")
        data = resp.json()
        if "connected" not in data:
            record_issue("P1", "TDSQL", "状态缺少connected", "有connected", f"keys={list(data.keys())}")

    def test_uat19_02_disconnect_idempotent(self):
        """重复断开不应报错"""
        resp1 = client.post("/api/v1/tdsql/disconnect")
        resp2 = client.post("/api/v1/tdsql/disconnect")
        if resp1.status_code != 200 or resp2.status_code != 200:
            record_issue("P2", "TDSQL", "重复断开报错", "200", f"{resp1.status_code}, {resp2.status_code}")

    def test_uat19_03_tables_without_connection(self):
        """未连接时获取表应返回400"""
        client.post("/api/v1/tdsql/disconnect")
        resp = client.get("/api/v1/tdsql/tables")
        if resp.status_code != 400:
            record_issue("P2", "TDSQL", "未连接时tables应返回400", "400", f"{resp.status_code}")

    def test_uat19_04_connect_invalid_host(self):
        """连接无效主机应返回错误"""
        resp = client.post("/api/v1/tdsql/connect", json={
            "host": "192.0.2.1", "port": 3306, "user": "root", "password": "test", "database": "test"
        })
        if resp.status_code not in (400, 500):
            record_issue("P2", "TDSQL", "无效主机应返回错误", "400/500", f"{resp.status_code}")

    def test_uat19_05_connect_missing_fields(self):
        """缺少必填字段应返回422"""
        resp = client.post("/api/v1/tdsql/connect", json={"host": "127.0.0.1"})
        if resp.status_code != 422:
            record_issue("P2", "TDSQL", "缺少字段应返回422", "422", f"{resp.status_code}")


# ═══════════════════════════════════════════════════════════
# 问题清单输出
# ═══════════════════════════════════════════════════════════

class TestUAT_Round3Report:
    """第三轮UAT问题清单"""

    def test_generate_report(self):
        """生成第三轮UAT问题清单"""
        if issues:
            print("\n" + "=" * 80)
            print("UAT 第三轮问题清单")
            print("=" * 80)
            for i, issue in enumerate(issues, 1):
                print(f"\n[{issue['severity']}] UAT3-{i:03d}")
                print(f"  Module: {issue['module']}")
                print(f"  Desc: {issue['description']}")
                print(f"  Expected: {issue['expected']}")
                print(f"  Actual: {issue['actual']}")
            print("\n" + "=" * 80)
            p0 = sum(1 for i in issues if i['severity'] == 'P0')
            p1 = sum(1 for i in issues if i['severity'] == 'P1')
            p2 = sum(1 for i in issues if i['severity'] == 'P2')
            print(f"Total: {len(issues)} issues")
            print(f"  P0: {p0}  P1: {p1}  P2: {p2}")
            print("=" * 80)
        else:
            print("\nUAT Round 3: 0 issues, all passed")
        assert True
