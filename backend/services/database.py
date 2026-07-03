"""
TDSQL SQL审核工具 - 数据库管理层 (V2.0)

负责SQLite数据库的初始化、迁移和连接管理。
V2.0: 27张表（新增用户/规则集/扫描计划/保留策略/调度租约）+ 历史版本增量迁移。

注: 生产环境如需集中式存储高可用，参见 docs/V2.0银行级改造设计说明书.md 中的
存储迁移方案（表结构与SQL已尽量保持 MySQL 兼容写法）。
"""
import json
import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("tdsql.database")

# 数据库路径
DB_DIR = Path(__file__).parent.parent.parent / "data"
DB_PATH = DB_DIR / "tdsql_check.db"

# 线程安全初始化锁
_init_lock = threading.Lock()
_db_initialized = False


def _get_connection() -> sqlite3.Connection:
    """获取数据库连接（WAL模式 + 超时设置）"""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def ensure_db():
    """确保数据库已初始化（线程安全懒加载）"""
    global _db_initialized
    if not _db_initialized:
        with _init_lock:
            if not _db_initialized:
                init_db()
                _db_initialized = True


def init_db():
    """初始化数据库 — 创建所有20张表 + 迁移 + 初始化默认数据"""
    conn = _get_connection()
    try:
        # 版本管理表
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS schema_version (
                key     TEXT PRIMARY KEY,
                value   TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now'))
            );
        """)

        # 检查是否需要迁移V0.4表
        _migrate_v04_tables(conn)

        # 创建所有V1.0新表（IF NOT EXISTS确保幂等）
        _create_all_tables(conn)

        # 初始化默认数据
        _init_default_data(conn)

        conn.commit()
        logger.info("数据库初始化完成 (V1.0)")
    except Exception as e:
        logger.error(f"数据库初始化失败: {e}")
        raise
    finally:
        conn.close()


def _migrate_v04_tables(conn: sqlite3.Connection):
    """V0.4表增量迁移 — 新增字段"""
    # 检查 slow_queries 表是否存在
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    table_names = [t["name"] for t in tables]

    if "slow_queries" in table_names:
        # 检查并添加V1.0新增字段
        _add_column_if_not_exists(conn, "slow_queries", "connection_id", "TEXT DEFAULT ''")
        _add_column_if_not_exists(conn, "slow_queries", "project_id", "TEXT DEFAULT ''")
        _add_column_if_not_exists(conn, "slow_queries", "normalized_sql", "TEXT DEFAULT ''")
        _add_column_if_not_exists(conn, "slow_queries", "distributed_analysis", "TEXT DEFAULT ''")
        _add_column_if_not_exists(conn, "slow_queries", "index_suggestions", "TEXT DEFAULT '[]'")
        _add_column_if_not_exists(conn, "slow_queries", "rewrite_suggestions", "TEXT DEFAULT '[]'")
        _add_column_if_not_exists(conn, "slow_queries", "assigned_to", "TEXT DEFAULT ''")
        _add_column_if_not_exists(conn, "slow_queries", "updated_at", "TEXT DEFAULT (datetime('now'))")
        _add_column_if_not_exists(conn, "slow_queries", "scan_task_id", "INTEGER DEFAULT NULL")
        _add_column_if_not_exists(conn, "slow_queries", "set_id", "TEXT DEFAULT ''")
        # V1.1: 新增执行者信息字段
        _add_column_if_not_exists(conn, "slow_queries", "client_user", "TEXT DEFAULT ''")
        _add_column_if_not_exists(conn, "slow_queries", "client_host", "TEXT DEFAULT ''")

    if "audit_history" in table_names:
        _add_column_if_not_exists(conn, "audit_history", "project_id", "TEXT DEFAULT ''")
        _add_column_if_not_exists(conn, "audit_history", "connection_id", "TEXT DEFAULT ''")
        _add_column_if_not_exists(conn, "audit_history", "gate_passed", "INTEGER DEFAULT NULL")
        _add_column_if_not_exists(conn, "audit_history", "gate_detail", "TEXT DEFAULT ''")
        _add_column_if_not_exists(conn, "audit_history", "top_violations", "TEXT DEFAULT '[]'")
        _add_column_if_not_exists(conn, "audit_history", "results_summary", "TEXT DEFAULT '{}'")
        # V2.0: 审计主体（操作用户）
        _add_column_if_not_exists(conn, "audit_history", "created_by", "TEXT DEFAULT ''")

    conn.commit()


def _add_column_if_not_exists(conn: sqlite3.Connection, table: str, column: str, definition: str):
    """如果列不存在则添加"""
    cursor = conn.execute(f"PRAGMA table_info({table})")
    existing_columns = [row["name"] for row in cursor.fetchall()]
    if column not in existing_columns:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            logger.info(f"迁移: {table} 添加列 {column}")
        except sqlite3.OperationalError:
            pass  # 列已存在或其他问题


def _create_all_tables(conn: sqlite3.Connection):
    """创建所有V1.0表"""

    # 迁移 scan_tasks 表新增字段（兼容已有数据库）
    _add_column_if_not_exists(conn, "scan_tasks", "time_window_start", "TEXT DEFAULT ''")
    _add_column_if_not_exists(conn, "scan_tasks", "time_window_end", "TEXT DEFAULT ''")

    conn.executescript("""
    -- T01. slow_queries (扩展版)
    CREATE TABLE IF NOT EXISTS slow_queries (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        fingerprint         TEXT NOT NULL,
        sql_text            TEXT NOT NULL,
        normalized_sql      TEXT DEFAULT '',
        db_name             TEXT DEFAULT '',
        set_id              TEXT DEFAULT '',           -- 来源 SET（如 set_1），非分布式为空
        connection_id       TEXT DEFAULT '',
        project_id          TEXT DEFAULT '',
        client_user         TEXT DEFAULT '',           -- 执行SQL的用户
        client_host         TEXT DEFAULT '',           -- 发起SQL的客户端IP
        exec_count          INTEGER DEFAULT 0,
        total_time_ms       REAL DEFAULT 0,
        avg_time_ms         REAL DEFAULT 0,
        max_time_ms         REAL DEFAULT 0,
        rows_examined       INTEGER DEFAULT 0,
        rows_sent           INTEGER DEFAULT 0,
        lock_time_ms        REAL DEFAULT 0,
        first_seen          TEXT,
        last_seen           TEXT,
        problem_type        TEXT DEFAULT '',
        severity            TEXT DEFAULT 'INFO',
        root_cause          TEXT DEFAULT '',
        suggestion          TEXT DEFAULT '',
        optimized_sql       TEXT DEFAULT '',
        distributed_analysis TEXT DEFAULT '',
        index_suggestions   TEXT DEFAULT '[]',
        rewrite_suggestions TEXT DEFAULT '[]',
        status              TEXT DEFAULT 'pending',
        assigned_to         TEXT DEFAULT '',
        scan_task_id        INTEGER DEFAULT NULL,
        analysis_json       TEXT DEFAULT '{}',
        created_at          TEXT DEFAULT (datetime('now')),
        updated_at          TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_slow_fingerprint ON slow_queries(fingerprint);
    CREATE INDEX IF NOT EXISTS idx_slow_db ON slow_queries(db_name);
    CREATE INDEX IF NOT EXISTS idx_slow_set_id ON slow_queries(set_id);
    CREATE INDEX IF NOT EXISTS idx_slow_status ON slow_queries(status);
    CREATE INDEX IF NOT EXISTS idx_slow_connection ON slow_queries(connection_id);
    CREATE INDEX IF NOT EXISTS idx_slow_project ON slow_queries(project_id);
    CREATE INDEX IF NOT EXISTS idx_slow_last_seen ON slow_queries(last_seen);
    CREATE INDEX IF NOT EXISTS idx_slow_severity ON slow_queries(severity);

    -- T02. audit_history (扩展版)
    CREATE TABLE IF NOT EXISTS audit_history (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        audit_type          TEXT NOT NULL,
        source              TEXT DEFAULT '',
        project_id          TEXT DEFAULT '',
        connection_id       TEXT DEFAULT '',
        total_sql           INTEGER DEFAULT 0,
        passed              INTEGER DEFAULT 0,
        failed              INTEGER DEFAULT 0,
        error_count         INTEGER DEFAULT 0,
        warning_count       INTEGER DEFAULT 0,
        pass_rate           REAL DEFAULT 0,
        results_json        TEXT DEFAULT '[]',
        gate_passed         INTEGER DEFAULT NULL,
        gate_detail         TEXT DEFAULT '',
        top_violations      TEXT DEFAULT '[]',
        results_summary     TEXT DEFAULT '{}',
        created_at          TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_audit_type ON audit_history(audit_type);
    CREATE INDEX IF NOT EXISTS idx_audit_project ON audit_history(project_id);
    CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_history(created_at);
    CREATE INDEX IF NOT EXISTS idx_audit_gate ON audit_history(gate_passed);

    -- T03. audit_results
    CREATE TABLE IF NOT EXISTS audit_results (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        audit_history_id    INTEGER NOT NULL,
        sql_text            TEXT NOT NULL,
        sql_type            TEXT DEFAULT '',
        line_number         INTEGER,
        file_path           TEXT DEFAULT '',
        passed              INTEGER DEFAULT 1,
        violations_json     TEXT DEFAULT '[]',
        error_count         INTEGER DEFAULT 0,
        warning_count       INTEGER DEFAULT 0,
        triggered_rules     TEXT DEFAULT '',
        created_at          TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (audit_history_id) REFERENCES audit_history(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_results_history ON audit_results(audit_history_id);
    CREATE INDEX IF NOT EXISTS idx_results_passed ON audit_results(passed);
    CREATE INDEX IF NOT EXISTS idx_results_rules ON audit_results(triggered_rules);

    -- T04. rule_configs
    CREATE TABLE IF NOT EXISTS rule_configs (
        rule_id             TEXT PRIMARY KEY,
        category            TEXT NOT NULL,
        severity            TEXT NOT NULL,
        description         TEXT NOT NULL,
        spec_source         TEXT DEFAULT '',
        fix_suggestion      TEXT DEFAULT '',
        enabled             INTEGER DEFAULT 1,
        is_builtin          INTEGER DEFAULT 1,
        created_at          TEXT DEFAULT (datetime('now')),
        updated_at          TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_rule_category ON rule_configs(category);
    CREATE INDEX IF NOT EXISTS idx_rule_enabled ON rule_configs(enabled);

    -- T05. rule_whitelist
    CREATE TABLE IF NOT EXISTS rule_whitelist (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        rule_id             TEXT NOT NULL,
        table_pattern       TEXT DEFAULT '',
        sql_pattern         TEXT DEFAULT '',
        reason              TEXT DEFAULT '',
        created_by          TEXT DEFAULT '',
        created_at          TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_whitelist_rule ON rule_whitelist(rule_id);

    -- T06. gate_rules
    CREATE TABLE IF NOT EXISTS gate_rules (
        project_id          TEXT PRIMARY KEY,
        max_error_count     INTEGER DEFAULT 0,
        max_warning_count   INTEGER DEFAULT -1,
        required_rules      TEXT DEFAULT '[]',
        blocked_rules       TEXT DEFAULT '[]',
        description         TEXT DEFAULT '',
        created_at          TEXT DEFAULT (datetime('now')),
        updated_at          TEXT DEFAULT (datetime('now'))
    );

    -- T07. gate_audit_logs
    CREATE TABLE IF NOT EXISTS gate_audit_logs (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id          TEXT NOT NULL,
        audit_history_id    INTEGER,
        source              TEXT DEFAULT '',
        passed              INTEGER NOT NULL,
        error_count         INTEGER DEFAULT 0,
        warning_count       INTEGER DEFAULT 0,
        blocked_by          TEXT DEFAULT '[]',
        detail              TEXT DEFAULT '',
        created_at          TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (audit_history_id) REFERENCES audit_history(id) ON DELETE SET NULL
    );
    CREATE INDEX IF NOT EXISTS idx_gate_project ON gate_audit_logs(project_id);
    CREATE INDEX IF NOT EXISTS idx_gate_passed ON gate_audit_logs(passed);
    CREATE INDEX IF NOT EXISTS idx_gate_created ON gate_audit_logs(created_at);

    -- T08. tdsql_connections
    CREATE TABLE IF NOT EXISTS tdsql_connections (
        id                  TEXT PRIMARY KEY,
        name                TEXT NOT NULL,
        host                TEXT NOT NULL,
        port                INTEGER NOT NULL,
        username            TEXT NOT NULL,
        password_encrypted  TEXT NOT NULL,
        database            TEXT DEFAULT '',
        charset             TEXT DEFAULT 'utf8mb4',
        is_default          INTEGER DEFAULT 0,
        is_distributed      INTEGER DEFAULT 1,
        description         TEXT DEFAULT '',
        status              TEXT DEFAULT 'disconnected',
        last_connected_at   TEXT,
        created_at          TEXT DEFAULT (datetime('now')),
        updated_at          TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_conn_default ON tdsql_connections(is_default);

    -- T09. bigtable_inventory
    CREATE TABLE IF NOT EXISTS bigtable_inventory (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        connection_id       TEXT NOT NULL,
        schema_name         TEXT NOT NULL,
        table_name          TEXT NOT NULL,
        size_gb             REAL DEFAULT 0,
        size_mb             REAL DEFAULT 0,
        rows_count          INTEGER DEFAULT 0,
        index_size_mb       REAL DEFAULT 0,
        daily_inc_mb        REAL DEFAULT 0,
        level               TEXT NOT NULL,
        is_partitioned      INTEGER DEFAULT 0,
        partition_count     INTEGER DEFAULT 0,
        has_global_index    INTEGER DEFAULT 0,
        shard_key           TEXT DEFAULT '',
        inspection_date     TEXT NOT NULL,
        created_at          TEXT DEFAULT (datetime('now')),
        UNIQUE(connection_id, schema_name, table_name, inspection_date)
    );
    CREATE INDEX IF NOT EXISTS idx_bt_level ON bigtable_inventory(level);
    CREATE INDEX IF NOT EXISTS idx_bt_connection ON bigtable_inventory(connection_id);
    CREATE INDEX IF NOT EXISTS idx_bt_date ON bigtable_inventory(inspection_date);
    CREATE INDEX IF NOT EXISTS idx_bt_table ON bigtable_inventory(schema_name, table_name);

    -- T10. bigtable_classification
    CREATE TABLE IF NOT EXISTS bigtable_classification (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        connection_id       TEXT NOT NULL,
        schema_name         TEXT NOT NULL,
        table_name          TEXT NOT NULL,
        table_type          TEXT NOT NULL,
        table_type_label    TEXT DEFAULT '',
        retention_days      INTEGER DEFAULT 0,
        archive_target      TEXT DEFAULT '',
        archive_period      TEXT DEFAULT '',
        partition_key       TEXT DEFAULT '',
        partition_granularity TEXT DEFAULT '',
        classified_by       TEXT DEFAULT '',
        created_at          TEXT DEFAULT (datetime('now')),
        updated_at          TEXT DEFAULT (datetime('now')),
        UNIQUE(connection_id, schema_name, table_name)
    );

    -- T11. partition_watermarks
    CREATE TABLE IF NOT EXISTS partition_watermarks (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        connection_id       TEXT NOT NULL,
        schema_name         TEXT NOT NULL,
        table_name          TEXT NOT NULL,
        partition_count     INTEGER NOT NULL,
        watermark_percent   REAL DEFAULT 0,
        status              TEXT NOT NULL,
        check_date          TEXT NOT NULL,
        created_at          TEXT DEFAULT (datetime('now')),
        UNIQUE(connection_id, schema_name, table_name, check_date)
    );
    CREATE INDEX IF NOT EXISTS idx_pw_status ON partition_watermarks(status);
    CREATE INDEX IF NOT EXISTS idx_pw_date ON partition_watermarks(check_date);
    CREATE INDEX IF NOT EXISTS idx_pw_table ON partition_watermarks(schema_name, table_name);

    -- T12. change_controls
    CREATE TABLE IF NOT EXISTS change_controls (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        connection_id       TEXT NOT NULL,
        schema_name         TEXT NOT NULL,
        table_name          TEXT NOT NULL,
        table_level         TEXT NOT NULL,
        change_type         TEXT NOT NULL,
        change_sql          TEXT NOT NULL,
        reason              TEXT DEFAULT '',
        stage               TEXT DEFAULT 'submitted',
        backup_completed    INTEGER DEFAULT 0,
        ticket_approved     INTEGER DEFAULT 0,
        window_applied      INTEGER DEFAULT 0,
        executed_at         TEXT,
        executed_by         TEXT DEFAULT '',
        result              TEXT DEFAULT '',
        post_check_status   TEXT DEFAULT '',
        created_at          TEXT DEFAULT (datetime('now')),
        updated_at          TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_cc_stage ON change_controls(stage);
    CREATE INDEX IF NOT EXISTS idx_cc_table ON change_controls(schema_name, table_name);
    CREATE INDEX IF NOT EXISTS idx_cc_level ON change_controls(table_level);

    -- T13. inspection_tasks
    CREATE TABLE IF NOT EXISTS inspection_tasks (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        connection_id       TEXT NOT NULL,
        inspection_type     TEXT NOT NULL,
        status              TEXT DEFAULT 'pending',
        started_at          TEXT,
        completed_at        TEXT,
        error_message       TEXT DEFAULT '',
        report_path         TEXT DEFAULT '',
        created_at          TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_it_status ON inspection_tasks(status);
    CREATE INDEX IF NOT EXISTS idx_it_type ON inspection_tasks(inspection_type);
    CREATE INDEX IF NOT EXISTS idx_it_date ON inspection_tasks(created_at);

    -- T14. inspection_results
    CREATE TABLE IF NOT EXISTS inspection_results (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id             INTEGER NOT NULL,
        category            TEXT NOT NULL,
        severity            TEXT NOT NULL,
        schema_name         TEXT DEFAULT '',
        table_name          TEXT DEFAULT '',
        metric_name         TEXT DEFAULT '',
        metric_value        TEXT DEFAULT '',
        threshold           TEXT DEFAULT '',
        message             TEXT NOT NULL,
        suggestion          TEXT DEFAULT '',
        created_at          TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (task_id) REFERENCES inspection_tasks(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_ir_task ON inspection_results(task_id);
    CREATE INDEX IF NOT EXISTS idx_ir_severity ON inspection_results(severity);
    CREATE INDEX IF NOT EXISTS idx_ir_category ON inspection_results(category);

    -- T15. alerts
    CREATE TABLE IF NOT EXISTS alerts (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        connection_id       TEXT NOT NULL,
        metric_name         TEXT NOT NULL,
        metric_value        REAL NOT NULL,
        level               TEXT NOT NULL,
        threshold           REAL NOT NULL,
        message             TEXT NOT NULL,
        status              TEXT DEFAULT 'active',
        acknowledged_by     TEXT DEFAULT '',
        acknowledged_at     TEXT,
        resolved_at         TEXT,
        notify_channels     TEXT DEFAULT '[]',
        created_at          TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_alert_status ON alerts(status);
    CREATE INDEX IF NOT EXISTS idx_alert_level ON alerts(level);
    CREATE INDEX IF NOT EXISTS idx_alert_connection ON alerts(connection_id);
    CREATE INDEX IF NOT EXISTS idx_alert_created ON alerts(created_at);

    -- T16. alert_rules
    CREATE TABLE IF NOT EXISTS alert_rules (
        metric_name         TEXT PRIMARY KEY,
        warning_threshold   REAL NOT NULL,
        urgent_threshold    REAL NOT NULL,
        check_interval_sec  INTEGER DEFAULT 60,
        notify_webhook      TEXT DEFAULT '',
        notify_email        TEXT DEFAULT '',
        enabled             INTEGER DEFAULT 1,
        created_at          TEXT DEFAULT (datetime('now')),
        updated_at          TEXT DEFAULT (datetime('now'))
    );

    -- T17. projects
    CREATE TABLE IF NOT EXISTS projects (
        project_id          TEXT PRIMARY KEY,
        project_name        TEXT NOT NULL,
        tdsql_connection_id TEXT DEFAULT '',
        rule_set_id         TEXT DEFAULT 'default',
        gate_rule_id        TEXT DEFAULT 'default',
        gitlab_project_id   INTEGER,
        gitlab_url          TEXT DEFAULT '',
        description         TEXT DEFAULT '',
        status              TEXT DEFAULT 'active',
        created_at          TEXT DEFAULT (datetime('now')),
        updated_at          TEXT DEFAULT (datetime('now'))
    );

    -- T18. operation_logs
    CREATE TABLE IF NOT EXISTS operation_logs (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        operator            TEXT DEFAULT '',
        operation_type      TEXT NOT NULL,
        target_type         TEXT DEFAULT '',
        target_id           TEXT DEFAULT '',
        detail              TEXT DEFAULT '',
        ip_address          TEXT DEFAULT '',
        user_agent          TEXT DEFAULT '',
        created_at          TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_log_operator ON operation_logs(operator);
    CREATE INDEX IF NOT EXISTS idx_log_type ON operation_logs(operation_type);
    CREATE INDEX IF NOT EXISTS idx_log_created ON operation_logs(created_at);

    -- T19. fingerprint_stats
    CREATE TABLE IF NOT EXISTS fingerprint_stats (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        connection_id       TEXT NOT NULL,
        fingerprint         TEXT NOT NULL,
        sample_sql          TEXT NOT NULL,
        stat_date           TEXT NOT NULL,
        exec_count          INTEGER DEFAULT 0,
        total_time_ms       REAL DEFAULT 0,
        avg_time_ms         REAL DEFAULT 0,
        max_time_ms         REAL DEFAULT 0,
        rows_examined       INTEGER DEFAULT 0,
        rows_sent           INTEGER DEFAULT 0,
        created_at          TEXT DEFAULT (datetime('now')),
        UNIQUE(connection_id, fingerprint, stat_date)
    );
    CREATE INDEX IF NOT EXISTS idx_fp_connection ON fingerprint_stats(connection_id);
    CREATE INDEX IF NOT EXISTS idx_fp_date ON fingerprint_stats(stat_date);
    CREATE INDEX IF NOT EXISTS idx_fp_total_time ON fingerprint_stats(total_time_ms);
    CREATE INDEX IF NOT EXISTS idx_fp_exec_count ON fingerprint_stats(exec_count);

    -- T20. optimization_records
    CREATE TABLE IF NOT EXISTS optimization_records (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        slow_query_id       INTEGER,
        connection_id       TEXT NOT NULL,
        original_sql        TEXT NOT NULL,
        optimized_sql       TEXT NOT NULL,
        before_type         TEXT DEFAULT '',
        before_key          TEXT DEFAULT '',
        before_rows         INTEGER DEFAULT 0,
        before_extra        TEXT DEFAULT '',
        before_time_ms      REAL DEFAULT 0,
        after_type          TEXT DEFAULT '',
        after_key           TEXT DEFAULT '',
        after_rows          INTEGER DEFAULT 0,
        after_extra         TEXT DEFAULT '',
        after_time_ms       REAL DEFAULT 0,
        improvement         TEXT DEFAULT '',
        improvement_detail  TEXT DEFAULT '',
        created_at          TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (slow_query_id) REFERENCES slow_queries(id) ON DELETE SET NULL
    );
    CREATE INDEX IF NOT EXISTS idx_opt_slow_query ON optimization_records(slow_query_id);
    CREATE INDEX IF NOT EXISTS idx_opt_improvement ON optimization_records(improvement);

    -- T21. scan_tasks (慢SQL扫描任务)
    CREATE TABLE IF NOT EXISTS scan_tasks (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        task_name           TEXT NOT NULL,
        source              TEXT DEFAULT 'manual',
        db_name             TEXT DEFAULT '',
        connection_id       TEXT DEFAULT '',
        connection_name     TEXT DEFAULT '',
        time_window_start   TEXT DEFAULT '',
        time_window_end     TEXT DEFAULT '',
        total_fetched       INTEGER DEFAULT 0,
        total_analyzed      INTEGER DEFAULT 0,
        status              TEXT DEFAULT 'completed',
        created_at          TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_scan_task_db ON scan_tasks(db_name);
    CREATE INDEX IF NOT EXISTS idx_scan_task_source ON scan_tasks(source);
    CREATE INDEX IF NOT EXISTS idx_slow_scan_task ON slow_queries(scan_task_id);

    -- ═══════════════ V2.0 新增表 ═══════════════

    -- T22. users (用户与角色，V2.0认证授权)
    CREATE TABLE IF NOT EXISTS users (
        id                    INTEGER PRIMARY KEY AUTOINCREMENT,
        username              TEXT NOT NULL UNIQUE,
        display_name          TEXT DEFAULT '',
        role                  TEXT NOT NULL DEFAULT 'developer',  -- admin/dba/developer/auditor
        password_hash         TEXT NOT NULL,
        salt                  TEXT NOT NULL,
        status                TEXT DEFAULT 'active',              -- active/disabled
        must_change_password  INTEGER DEFAULT 0,
        failed_attempts       INTEGER DEFAULT 0,
        locked_until          TEXT DEFAULT NULL,
        last_login_at         TEXT DEFAULT NULL,
        created_by            TEXT DEFAULT '',
        created_at            TEXT DEFAULT (datetime('now')),
        updated_at            TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
    CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);

    -- T23. rule_sets (规则集，V2.0多租户规则)
    CREATE TABLE IF NOT EXISTS rule_sets (
        id                  TEXT PRIMARY KEY,
        name                TEXT NOT NULL,
        description         TEXT DEFAULT '',
        is_builtin          INTEGER DEFAULT 0,
        created_by          TEXT DEFAULT '',
        created_at          TEXT DEFAULT (datetime('now')),
        updated_at          TEXT DEFAULT (datetime('now'))
    );

    -- T24. rule_set_items (规则集条目：按规则集覆盖规则启停/级别)
    CREATE TABLE IF NOT EXISTS rule_set_items (
        rule_set_id         TEXT NOT NULL,
        rule_id             TEXT NOT NULL,
        enabled             INTEGER DEFAULT 1,
        severity_override   TEXT DEFAULT NULL,   -- NULL=使用规则默认级别
        PRIMARY KEY (rule_set_id, rule_id)
    );
    CREATE INDEX IF NOT EXISTS idx_rsi_set ON rule_set_items(rule_set_id);

    -- T25. scan_schedules (按连接的慢SQL扫描计划，V2.0多实例调度)
    CREATE TABLE IF NOT EXISTS scan_schedules (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        connection_id       TEXT NOT NULL,
        source              TEXT DEFAULT 'digest',   -- digest/processlist
        cron_hour           INTEGER DEFAULT 2,
        cron_minute         INTEGER DEFAULT 0,
        limit_rows          INTEGER DEFAULT 100,
        min_time            REAL DEFAULT 1.0,
        enabled             INTEGER DEFAULT 1,
        last_run_at         TEXT DEFAULT NULL,
        last_run_status     TEXT DEFAULT '',
        created_by          TEXT DEFAULT '',
        created_at          TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_sched_conn ON scan_schedules(connection_id);
    CREATE INDEX IF NOT EXISTS idx_sched_enabled ON scan_schedules(enabled);

    -- T26. retention_policies (数据保留策略，V2.0数据生命周期)
    CREATE TABLE IF NOT EXISTS retention_policies (
        table_name          TEXT PRIMARY KEY,
        retention_days      INTEGER NOT NULL,
        enabled             INTEGER DEFAULT 1,
        updated_at          TEXT DEFAULT (datetime('now'))
    );

    -- T27. scheduler_lease (调度器leader租约，多副本部署防重复执行)
    CREATE TABLE IF NOT EXISTS scheduler_lease (
        id                  INTEGER PRIMARY KEY CHECK (id = 1),
        holder              TEXT NOT NULL,
        expires_at          TEXT NOT NULL
    );
    """)


def _init_default_data(conn: sqlite3.Connection):
    """初始化默认数据：规则配置、门禁规则、告警规则"""

    # 默认门禁规则
    conn.execute("""
        INSERT OR IGNORE INTO gate_rules(project_id, max_error_count, max_warning_count, description)
        VALUES ('default', 0, -1, '默认门禁规则：不允许任何ERROR级别违规')
    """)

    # 默认告警规则
    alert_rules = [
        ('threads_running', 100, 200, 60, 1),
        ('seconds_behind_master', 60, 300, 60, 1),
        ('lock_wait_count', 1, 10, 60, 1),
        ('long_transaction_count', 1, 5, 60, 1),
    ]
    for metric, warning, urgent, interval, enabled in alert_rules:
        conn.execute("""
            INSERT OR IGNORE INTO alert_rules(metric_name, warning_threshold, urgent_threshold, check_interval_sec, enabled)
            VALUES (?, ?, ?, ?, ?)
        """, (metric, warning, urgent, interval, enabled))

    # V2.0: 默认规则集（空条目 = 全部规则使用默认启停/级别）
    conn.execute("""
        INSERT OR IGNORE INTO rule_sets(id, name, description, is_builtin, created_by)
        VALUES ('default', '默认规则集', '全部规则按内置默认启停与级别执行', 1, 'system')
    """)

    # V2.0: 默认数据保留策略
    retention_defaults = [
        ("slow_queries", 180),
        ("audit_history", 365),
        ("scan_tasks", 180),
        ("alerts", 90),
        ("operation_logs", 365),
        ("gate_audit_logs", 365),
        ("fingerprint_stats", 180),
    ]
    for table, days in retention_defaults:
        conn.execute("""
            INSERT OR IGNORE INTO retention_policies(table_name, retention_days, enabled)
            VALUES (?, ?, 1)
        """, (table, days))

    # 更新版本号
    conn.execute("""
        INSERT OR REPLACE INTO schema_version(key, value) VALUES('version', '2.0')
    """)

    conn.commit()


def init_rule_configs(conn: sqlite3.Connection = None):
    """初始化规则配置表 — 将所有76条规则元数据写入rule_configs表"""
    from backend.engine.rules import ALL_RULE_CLASSES

    close_conn = False
    if conn is None:
        conn = _get_connection()
        close_conn = True

    try:
        for rule_cls in ALL_RULE_CLASSES:
            rule = rule_cls()
            category_val = rule.category.value if hasattr(rule.category, 'value') else str(rule.category)
            severity_val = rule.severity.value if hasattr(rule.severity, 'value') else str(rule.severity)
            conn.execute("""
                INSERT OR IGNORE INTO rule_configs
                (rule_id, category, severity, description, spec_source, fix_suggestion, enabled, is_builtin)
                VALUES (?, ?, ?, ?, ?, ?, 1, 1)
            """, (rule.rule_id, category_val, severity_val, rule.description,
                  rule.spec_source, rule.fix_suggestion))
        conn.commit()
        logger.info(f"规则配置初始化完成: {len(ALL_RULE_CLASSES)} 条规则")
    finally:
        if close_conn:
            conn.close()


def log_operation(operator: str, operation_type: str, target_type: str = "",
                  target_id: str = "", detail: str = "", ip_address: str = "",
                  user_agent: str = ""):
    """记录操作审计日志"""
    conn = _get_connection()
    try:
        conn.execute("""
            INSERT INTO operation_logs(operator, operation_type, target_type, target_id, detail, ip_address, user_agent)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (operator, operation_type, target_type, target_id, detail, ip_address, user_agent))
        conn.commit()
    finally:
        conn.close()
