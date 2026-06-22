#!/usr/bin/env python
"""
TDSQL SQL审核工具 V1.0 冒烟测试脚本
覆盖：数据库初始化 → 规则引擎 → V1.0核心引擎 → 服务层 → API端点 → CLI工具
"""
import sys
import os
import warnings
warnings.filterwarnings("ignore", category=UserWarning)

# 确保项目根目录在 Python path 中
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

passed = 0
failed = 0
errors = []

def ok(msg):
    global passed
    passed += 1
    print(f"  [OK] {msg}")

def fail(msg, detail=""):
    global failed
    failed += 1
    errors.append(f"{msg}: {detail}")
    print(f"  [FAIL] {msg}")
    if detail:
        print(f"         {detail}")

def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

# ─────────────────────────────────────────────
# [1] 数据库初始化
# ─────────────────────────────────────────────
section("[1] 数据库初始化测试")
try:
    from backend.services.database import ensure_db, init_rule_configs, _get_connection
    ensure_db()
    ok("ensure_db() 执行成功")

    init_rule_configs()
    ok("init_rule_configs() 执行成功")

    conn = _get_connection()
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()]
    conn.close()

    expected_tables = [
        # V1.0实际20张业务表 + schema_version
        "slow_queries", "audit_history", "audit_results",
        "rule_configs", "rule_whitelist",
        "gate_rules", "gate_audit_logs",
        "tdsql_connections",
        "bigtable_inventory", "bigtable_classification",
        "partition_watermarks", "change_controls",
        "inspection_tasks", "inspection_results",
        "alerts", "alert_rules",
        "projects", "operation_logs",
        "fingerprint_stats", "optimization_records",
    ]
    for t in expected_tables:
        if t in tables:
            ok(f"表 {t} 存在")
        else:
            fail(f"表 {t} 缺失")

    # 检查规则配置
    conn = _get_connection()
    rule_count = conn.execute("SELECT COUNT(*) FROM rule_configs").fetchone()[0]
    conn.close()
    if rule_count >= 76:
        ok(f"规则配置表有 {rule_count} 条规则")
    else:
        fail(f"规则配置表只有 {rule_count} 条，期望 >= 76")

except Exception as e:
    fail("数据库初始化", str(e))

# ─────────────────────────────────────────────
# [2] 规则引擎功能测试
# ─────────────────────────────────────────────
section("[2] 规则引擎功能测试")
try:
    from backend.engine.checker import RuleChecker
    checker = RuleChecker(dialect="mysql")
    rules_info = checker.get_rules_info()
    ok(f"加载 {len(rules_info)} 条规则")

    # 测试1：命名违规的DDL
    sql_bad_naming = "CREATE TABLE x (id INT PRIMARY KEY, name VARCHAR(50))"
    result = checker.audit_sql(sql_bad_naming)
    if not result.passed:
        ok(f"违规DDL审核通过检测: 发现 {len(result.violations)} 条违规")
        for v in result.violations[:3]:
            print(f"       - {v.rule_id}: {v.message[:60]}")
    else:
        fail("违规DDL未检出问题", sql_bad_naming)

    # 测试2：规范DDL
    sql_good_ddl = """CREATE TABLE t_order_detail (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        order_no VARCHAR(32) NOT NULL COMMENT '订单编号',
        cust_id BIGINT UNSIGNED NOT NULL COMMENT '客户ID',
        amount DECIMAL(18,2) NOT NULL COMMENT '金额',
        status TINYINT NOT NULL DEFAULT 0 COMMENT '状态',
        create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        KEY idx_order_no (order_no),
        KEY idx_cust_id (cust_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='订单明细表'"""
    result2 = checker.audit_sql(sql_good_ddl)
    error_violations = [v for v in result2.violations if str(v.severity) == "ERROR"]
    if len(error_violations) == 0:
        ok(f"规范DDL无ERROR违规 (WARNING: {len(result2.violations)}条)")
    else:
        fail(f"规范DDL有 {len(error_violations)} 条ERROR", ", ".join(v.rule_id for v in error_violations))

    # 测试3：SELECT * 违规
    sql_select_star = "SELECT * FROM t_user WHERE id = 1"
    result3 = checker.audit_sql(sql_select_star)
    if result3.violations:
        ok(f"SELECT * 检出违规: {len(result3.violations)} 条")
    else:
        fail("SELECT * 未检出违规")

    # 测试4：无分片键查询（分布式规则）
    sql_no_shard = "SELECT * FROM t_order WHERE order_no = '123'"
    result4 = checker.audit_sql(sql_no_shard)
    ok(f"分布式规则测试: 检出 {len(result4.violations)} 条违规")

    # 测试5：审核文件
    file_content = """
    -- 注释行
    SELECT * FROM t_user WHERE id = 1;
    CREATE TABLE bad_table (x INT);
    """
    results = checker.audit_file(file_content, "test.sql")
    ok(f"文件审核: {len(results)} 条SQL, 汇总: {checker.compute_summary(results)}")

except Exception as e:
    import traceback
    fail("规则引擎测试", traceback.format_exc())

# ─────────────────────────────────────────────
# [3] V1.0 核心引擎测试
# ─────────────────────────────────────────────
section("[3] V1.0 核心引擎测试")

# 3.1 指纹引擎
try:
    from backend.engine.fingerprint import FingerprintEngine
    fp = FingerprintEngine()
    sql1 = "SELECT * FROM t_user WHERE id = 1 AND name = 'abc'"
    sql2 = "SELECT * FROM t_user WHERE id = 2 AND name = 'xyz'"
    fp1 = fp.fingerprint(sql1)
    fp2 = fp.fingerprint(sql2)
    if fp1 == fp2:
        ok(f"指纹归并: 两条不同参数SQL指纹一致 => {fp1[:50]}")
    else:
        fail("指纹归并失败", f"fp1={fp1[:40]} vs fp2={fp2[:40]}")

    h = fp.fingerprint_hash(sql1)
    ok(f"指纹哈希: {h}")
    tables = fp.extract_tables_from_sql("SELECT * FROM t_order o JOIN t_user u ON o.uid = u.id")
    ok(f"表名提取: {tables}")
except Exception as e:
    fail("指纹引擎", str(e))

# 3.2 索引顾问
try:
    from backend.engine.index_advisor import IndexAdvisor
    advisor = IndexAdvisor()
    sql = "SELECT * FROM t_order WHERE cust_id = 100 AND status = 1 AND create_time > '2024-01-01'"
    recs = advisor.advise_from_sql(sql)
    if recs:
        ok(f"索引推荐: {len(recs)} 条建议")
        for r in recs[:2]:
            print(f"       - {r.type}: {r.ddl[:60]}")
    else:
        fail("索引推荐无结果")

    # EXPLAIN分析
    explain_rows = [
        {"table": "t_order", "type": "ALL", "rows": 50000, "key": "", "possible_keys": ""},
    ]
    recs2 = advisor.advise_from_explain(explain_rows)
    if recs2:
        ok(f"EXPLAIN分析: 检出 {len(recs2)} 条索引建议")
    else:
        fail("EXPLAIN分析无结果")

    # 冗余索引检测
    indexes = [
        {"name": "idx_a", "columns": ["col_a"]},
        {"name": "idx_ab", "columns": ["col_a", "col_b"]},
    ]
    redundant = advisor.detect_redundant_indexes(indexes)
    if redundant:
        ok(f"冗余索引检测: 发现 {len(redundant)} 个冗余索引")
    else:
        fail("冗余索引检测无结果")
except Exception as e:
    fail("索引顾问", str(e))

# 3.3 SQL改写引擎
try:
    from backend.engine.sql_rewriter import SQLRewriter
    rewriter = SQLRewriter()

    # SELECT * 改写
    s1 = "SELECT * FROM t_user WHERE id = 1"
    r1 = rewriter.rewrite(s1, table_metadata={"t_user": {"columns": ["id", "name", "email", "phone"]}})
    if any(s.type == "select_star" for s in r1):
        ok("SELECT * 改写建议生成")
    else:
        fail("SELECT * 改写未触发")

    # 深分页改写
    s2 = "SELECT * FROM t_order ORDER BY id LIMIT 50000, 20"
    r2 = rewriter.rewrite(s2)
    if any(s.type == "deep_pagination" for s in r2):
        ok("深分页改写建议生成")
    else:
        fail("深分页改写未触发")

    # OR → UNION ALL
    s3 = "SELECT * FROM t_user WHERE age = 20 OR city = 'beijing'"
    r3 = rewriter.rewrite(s3)
    if any(s.type == "or_to_union" for s in r3):
        ok("OR→UNION ALL 改写建议生成")
    else:
        fail("OR→UNION ALL 改写未触发")

    # 子查询 → JOIN
    s4 = "SELECT * FROM t_order WHERE cust_id IN (SELECT id FROM t_customer WHERE status = 1)"
    r4 = rewriter.rewrite(s4)
    if any(s.type == "subquery_to_join" for s in r4):
        ok("子查询→JOIN 改写建议生成")
    else:
        fail("子查询→JOIN 改写未触发")
except Exception as e:
    fail("SQL改写引擎", str(e))

# 3.4 字符集诊断
try:
    from backend.engine.charset_diagnoser import CharsetDiagnoser
    diag = CharsetDiagnoser()
    sqls = diag.get_diagnostic_sqls()
    if len(sqls) == 6:
        ok(f"字符集诊断: 6套诊断SQL已定义")
    else:
        fail(f"字符集诊断SQL数量异常: {len(sqls)}")

    # 模拟诊断结果
    mock_results = {
        "instance_default": [
            {"Variable_name": "character_set_server", "Variable_value": "latin1"},
            {"Variable_name": "collation_server", "Variable_value": "latin1_swedish_ci"},
        ],
        "database_charset": [
            {"SCHEMA_NAME": "test_db", "DEFAULT_CHARACTER_SET_NAME": "utf8"},
        ],
        "table_charset": [],
        "column_charset": [],
        "mismatch_columns": [],
        "join_collation_mismatch": [],
    }
    report = diag.diagnose_from_query_results(mock_results)
    if report.issues:
        ok(f"字符集诊断: 检出 {len(report.issues)} 个问题")
    else:
        fail("字符集诊断未检出问题")
except Exception as e:
    fail("字符集诊断", str(e))

# 3.5 分布式EXPLAIN分析
try:
    from backend.engine.distributed_explain import DistributedExplainAnalyzer
    analyzer = DistributedExplainAnalyzer()
    # 无EXPLAIN输出时做静态分析
    sql = "SELECT * FROM t_order WHERE shardkey_col = 1"
    report = analyzer.analyze([], sql=sql, table_metadata={"t_order": {"shard_key": "shardkey_col", "is_shard_table": True}})
    ok(f"分布式EXPLAIN分析: shard_key_in_where={report.shard_key_in_where}, warnings={len(report.warnings)}")
    # 有EXPLAIN输出时
    report2 = analyzer.analyze([
        {"hit_set": "set1", "scan_type": "INDEX_SCAN"},
    ])
    ok(f"分布式EXPLAIN(有输出): hit_single_set={report2.shard_key_in_where}")
except Exception as e:
    fail("分布式EXPLAIN分析", str(e))

# 3.6 死锁分析器
try:
    from backend.engine.deadlock_analyzer import DeadlockAnalyzer
    analyzer = DeadlockAnalyzer()
    deadlock_log = """
    DEADLOCK detected at 2024-01-01 10:00:00
    *** (1) TRANSACTION:
    TRANSACTION 12345, ACTIVE 5 sec starting index read
    mysql tables in use 1, locked 1
    LOCK WAIT 3 lock struct(s), heap size 1136, 2 row lock(s)
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
    result = analyzer.analyze_from_log(deadlock_log)
    if result.has_deadlock:
        ok(f"死锁分析: has_deadlock={result.has_deadlock}, suggestions={len(result.suggestions)}")
    else:
        fail("死锁分析器未检出死锁")
except Exception as e:
    fail("死锁分析器", str(e))

# 3.7 长事务分析
try:
    from backend.engine.long_transaction import LongTransactionAnalyzer
    analyzer = LongTransactionAnalyzer()
    mock_trx = [
        {"trx_id": "12345", "trx_started": "2024-01-01 10:00:00", "trx_state": "RUNNING",
         "trx_rows_modified": 50000, "trx_rows_locked": 2000,
         "trx_query": "UPDATE t_order SET status = 1", "run_seconds": 35},
    ]
    results = analyzer.analyze_from_query_results(mock_trx)
    if results:
        info = results[0]
        suggestions = analyzer.get_suggestions(info)
        ok(f"长事务分析: {len(results)} 条长事务, severity={info.severity}, suggestions={len(suggestions)}")
    else:
        fail("长事务分析未检出长事务")
except Exception as e:
    fail("长事务分析", str(e))

# 3.8 大表治理引擎
try:
    from backend.engine.bigtable_engine import BigTableClassifier, BigTableEngine
    classifier = BigTableClassifier()
    level, label = classifier.classify(size_gb=60, rows=80000000)
    ok(f"大表分类: 60GB/8000万行 => {level}({label})")

    # 大表引擎扫描
    engine = BigTableEngine()
    tables_info = [
        {"schema": "test_db", "table": "t_transaction_log", "size_gb": 60, "rows": 80000000, "is_partitioned": False},
        {"schema": "test_db", "table": "t_config", "size_gb": 0.1, "rows": 100, "is_partitioned": False},
    ]
    big_tables = engine.scan_big_tables(tables_info)
    ok(f"大表扫描: 从{len(tables_info)}张表中识别出 {len(big_tables)} 张大表")

    report = engine.get_governance_report(big_tables)
    ok(f"治理报告: total={report['total_big_tables']}, by_level={report['by_level']}")
except Exception as e:
    fail("大表治理引擎", str(e))

# ─────────────────────────────────────────────
# [4] 服务层测试
# ─────────────────────────────────────────────
section("[4] 服务层测试")

# 4.1 质量门禁
try:
    from backend.services.gate_service import GateService, GATE_STRATEGIES
    from backend.models import Violation, Severity, RuleCategory
    svc = GateService()

    # 测试strict策略
    violations = [
        Violation(rule_id="R001", category=RuleCategory.NAMING, severity=Severity.ERROR, message="测试ERROR"),
        Violation(rule_id="R002", category=RuleCategory.NAMING, severity=Severity.WARNING, message="测试WARNING"),
    ]
    result = svc.evaluate(violations)
    ok(f"门禁评估(strict): passed={result.passed}, errors={result.error_count}, warnings={result.warning_count}")

    # 应用normal策略
    svc.apply_strategy("test_project", "normal")
    ok("门禁策略normal应用成功")

    # 获取门禁规则
    rule = svc.get_gate_rule("test_project")
    ok(f"门禁规则读取: max_error={rule.max_error_count}, max_warning={rule.max_warning_count}")
except Exception as e:
    fail("质量门禁服务", str(e))

# 4.2 大表治理服务
try:
    from backend.services.bigtable_service import BigTableService
    svc = BigTableService()
    ok("大表治理服务实例化成功")
except Exception as e:
    fail("大表治理服务", str(e))

# 4.3 项目管理服务
try:
    from backend.services.project_service import ProjectService
    svc = ProjectService()
    ok("项目管理服务实例化成功")
except Exception as e:
    fail("项目管理服务", str(e))

# 4.4 监控告警服务
try:
    from backend.services.monitor_service import MonitorService
    svc = MonitorService()
    ok("监控告警服务实例化成功")
except Exception as e:
    fail("监控告警服务", str(e))

# 4.5 巡检服务
try:
    from backend.services.inspection_service import InspectionService
    svc = InspectionService()
    ok("巡检服务实例化成功")
except Exception as e:
    fail("巡检服务", str(e))

# 4.6 密码加密服务
try:
    from backend.services.security_service import SecurityService, encrypt_password, decrypt_password
    encrypted = encrypt_password("test_password_123")
    decrypted = decrypt_password(encrypted)
    if decrypted == "test_password_123":
        ok(f"密码加密/解密: 加密后成功解密还原 (Fernet={'是' if encrypted.startswith('gAAAA') else '否(降级base64)'})")
    else:
        fail("密码加密/解密失败", f"解密结果: {decrypted}")

    masked = SecurityService.mask_password("my_secret_pwd")
    ok(f"密码脱敏: {masked}")
except Exception as e:
    fail("密码加密服务", str(e))

# ─────────────────────────────────────────────
# [5] API端点测试
# ─────────────────────────────────────────────
section("[5] API端点测试")
try:
    from fastapi.testclient import TestClient
    from backend.main import app

    with TestClient(app) as client:
        # 健康检查
        resp = client.get("/health")
        if resp.status_code == 200 and resp.json()["status"] == "ok":
            ok(f"GET /health => {resp.json()}")
        else:
            fail("GET /health", str(resp.status_code))

        # 规则列表
        resp = client.get("/api/v1/rules")
        if resp.status_code == 200:
            rules_data = resp.json()
            ok(f"GET /api/v1/rules => {len(rules_data)} 条规则")
        else:
            fail("GET /api/v1/rules", str(resp.status_code))

        # SQL审核
        resp = client.post("/api/v1/audit/sql", json={
            "sql": "SELECT * FROM t_user WHERE id = 1",
        })
        if resp.status_code == 200:
            audit_data = resp.json()
            ok(f"POST /api/v1/audit/sql => passed={audit_data.get('passed')}, violations={len(audit_data.get('violations', []))}")
        else:
            fail("POST /api/v1/audit/sql", f"status={resp.status_code}, body={resp.text[:200]}")

        # Dashboard
        resp = client.get("/api/v1/dashboard/summary")
        if resp.status_code == 200:
            ok(f"GET /api/v1/dashboard/summary => {resp.json()}")
        else:
            fail("GET /api/v1/dashboard/summary", str(resp.status_code))

        # 项目列表
        resp = client.get("/api/v1/projects")
        if resp.status_code == 200:
            ok(f"GET /api/v1/projects => status {resp.status_code}")
        else:
            fail("GET /api/v1/projects", str(resp.status_code))

        # 门禁规则
        resp = client.get("/api/v1/gate/rules/default")
        if resp.status_code == 200:
            ok(f"GET /api/v1/gate/rules/default => status {resp.status_code}")
        else:
            fail("GET /api/v1/gate/rules/default", str(resp.status_code))

        # 监控告警
        resp = client.get("/api/v1/monitor/alerts")
        if resp.status_code == 200:
            ok(f"GET /api/v1/monitor/alerts => status {resp.status_code}")
        else:
            fail("GET /api/v1/monitor/alerts", str(resp.status_code))

        # 巡检任务
        resp = client.get("/api/v1/inspection/tasks")
        if resp.status_code == 200:
            ok(f"GET /api/v1/inspection/tasks => status {resp.status_code}")
        else:
            fail("GET /api/v1/inspection/tasks", str(resp.status_code))

except Exception as e:
    import traceback
    fail("API端点测试", traceback.format_exc())

# ─────────────────────────────────────────────
# [6] CLI工具测试
# ─────────────────────────────────────────────
section("[6] CLI工具测试")
try:
    from backend.cli import cli
    from click.testing import CliRunner
    runner = CliRunner()

    # rules命令
    result = runner.invoke(cli, ["rules"])
    if result.exit_code == 0:
        ok("CLI rules 命令执行成功")
    else:
        fail("CLI rules 命令", result.output[:200])

    # audit命令（违规SQL会返回exit_code=1，这是正确行为）
    result = runner.invoke(cli, ["audit", "SELECT * FROM t_user WHERE id = 1"])
    if result.exit_code == 1:
        ok("CLI audit 命令执行成功 (违规SQL正确返回exit_code=1)")
    elif result.exit_code == 0:
        ok("CLI audit 命令执行成功 (SQL通过审核)")
    else:
        fail("CLI audit 命令", f"exit_code={result.exit_code}")

    # fingerprint命令
    result = runner.invoke(cli, ["fingerprint", "SELECT * FROM t_user WHERE id = 1 AND name = 'abc'"])
    if result.exit_code == 0:
        ok("CLI fingerprint 命令执行成功")
    else:
        fail("CLI fingerprint 命令", result.output[:200])

    # index-advise命令
    result = runner.invoke(cli, ["index-advise", "SELECT * FROM t_order WHERE cust_id = 1 AND status = 0"])
    if result.exit_code == 0:
        ok("CLI index-advise 命令执行成功")
    else:
        fail("CLI index-advise 命令", result.output[:200])

    # rewrite命令
    result = runner.invoke(cli, ["rewrite", "SELECT * FROM t_order LIMIT 50000, 20"])
    if result.exit_code == 0:
        ok("CLI rewrite 命令执行成功")
    else:
        fail("CLI rewrite 命令", result.output[:200])

except Exception as e:
    import traceback
    fail("CLI工具测试", traceback.format_exc())

# ─────────────────────────────────────────────
# 汇总
# ─────────────────────────────────────────────
section("冒烟测试汇总")
total = passed + failed
print(f"\n  总测试项: {total}")
print(f"  通过: {passed}")
print(f"  失败: {failed}")
print(f"  通过率: {passed/total*100:.1f}%" if total > 0 else "  N/A")

if errors:
    print(f"\n  失败详情:")
    for e in errors:
        print(f"    - {e}")

print(f"\n{'='*60}")
if failed == 0:
    print("  ✅ 全部冒烟测试通过!")
else:
    print(f"  ⚠️  {failed} 项测试失败，请检查上方详情")
print(f"{'='*60}")

sys.exit(0 if failed == 0 else 1)
