"""
TDSQL SQL审核工具 - 数据库管理层 (V2.0)

负责MySQL数据库的初始化、迁移和连接管理。
V2.0: 27张表（新增用户/规则集/扫描计划/保留策略/调度租约）+ 历史版本增量迁移。
V2.1: 从SQLite迁移到MySQL，支撑生产级并发场景。
"""
import json
import logging
import queue
import re
import threading
import os
import pymysql
import pymysql.cursors
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("tdsql.database")

# 线程安全初始化锁
_init_lock = threading.Lock()
_db_initialized = False

# MySQL连接配置 — 优先读环境变量，默认指向Docker MySQL
MYSQL_CONFIG = {
    "host": os.getenv("SQLCHECK_DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("SQLCHECK_DB_PORT", "13306")),
    "user": os.getenv("SQLCHECK_DB_USER", "root"),
    "password": os.getenv("SQLCHECK_DB_PASSWORD", "tdsql_test_2024"),
    "database": os.getenv("SQLCHECK_DB_NAME", "tdsql_sqlcheck"),
    "charset": "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor,
    "autocommit": False,
}

# ── 元数据库连接池（V2.1性能优化：避免每次请求新建TCP连接引发连接风暴） ──
# 池中保留最多 SQLCHECK_DB_POOL_SIZE 个空闲连接复用；
# 超出池容量的并发请求按需新建、用完直接关闭（不阻塞排队）。
POOL_SIZE = int(os.getenv("SQLCHECK_DB_POOL_SIZE", "10"))
_conn_pool: "queue.Queue" = queue.Queue(maxsize=POOL_SIZE)


class _MySQLCompatCursor:
    """兼容SQLite风格的游标包装器
    
    自动将SQL中的 ? 占位符转换为 MySQL 的 %s 占位符，
    使原有SQLite代码无需修改即可在MySQL上运行。
    """
    def __init__(self, cursor):
        self._cursor = cursor
    
    def execute(self, sql, params=None):
        # 自动转换 ? → %s（但排除 LIKE '%?%' 中的文本?）
        # 只转换SQL参数占位符：独立出现的 ?
        converted = re.sub(r"(?<!['\"\w])\?(?!['\"\w])", "%s", sql)
        if params is not None:
            return self._cursor.execute(converted, params)
        return self._cursor.execute(converted)
    
    def executemany(self, sql, params):
        converted = re.sub(r"(?<!['\"\w])\?(?!['\"\w])", "%s", sql)
        return self._cursor.executemany(converted, params)
    
    def fetchone(self):
        row = self._cursor.fetchone()
        return self._convert_row(row)
    
    def fetchall(self):
        rows = self._cursor.fetchall()
        return [self._convert_row(r) for r in rows]
    
    def fetchmany(self, size=None):
        if size:
            rows = self._cursor.fetchmany(size)
        else:
            rows = self._cursor.fetchmany()
        return [self._convert_row(r) for r in rows]
    
    @staticmethod
    def _convert_row(row):
        """将MySQL返回的datetime/timedelta等转为字符串，匹配SQLite行为"""
        if row is None:
            return None
        if isinstance(row, dict):
            for k, v in row.items():
                if isinstance(v, datetime):
                    row[k] = v.isoformat()
                elif hasattr(v, 'total_seconds'):
                    row[k] = str(v)
            return row
        return row

    def __iter__(self):
        """支持 for row in cursor 迭代（兼容sqlite3游标行为）"""
        while True:
            row = self.fetchone()
            if row is None:
                return
            yield row

    @property
    def lastrowid(self):
        return self._cursor.lastrowid
    
    @property
    def rowcount(self):
        return self._cursor.rowcount
    
    @property
    def description(self):
        return self._cursor.description
    
    def close(self):
        return self._cursor.close()


def split_sql_statements(sql_script: str) -> list[str]:
    """将 SQL 脚本按分号拆分为多条 SQL 语句，能够正确处理字符串字面量、行注释和块注释中的分号"""
    statements = []
    current_statement = []
    
    in_single_quote = False
    in_double_quote = False
    in_backtick = False
    in_line_comment = False
    in_block_comment = False
    
    chars = list(sql_script)
    i = 0
    n = len(chars)
    
    while i < n:
        c = chars[i]
        
        # 处理转义字符
        if c == '\\' and (in_single_quote or in_double_quote):
            current_statement.append(c)
            if i + 1 < n:
                current_statement.append(chars[i+1])
                i += 2
            else:
                i += 1
            continue
            
        # 处理单行注释内容
        if in_line_comment:
            if c == '\n':
                in_line_comment = False
                current_statement.append(c)
            i += 1
            continue
            
        # 处理块注释内容
        if in_block_comment:
            if c == '*' and i + 1 < n and chars[i+1] == '/':
                in_block_comment = False
                i += 2
            else:
                i += 1
            continue
            
        # 检测注释起始
        if not in_single_quote and not in_double_quote and not in_backtick:
            if c == '-' and i + 1 < n and chars[i+1] == '-':
                in_line_comment = True
                i += 2
                continue
            if c == '#':
                in_line_comment = True
                i += 1
                continue
            if c == '/' and i + 1 < n and chars[i+1] == '*':
                in_block_comment = True
                i += 2
                continue
                
        # 处理引号闭合
        if c == "'" and not in_double_quote and not in_backtick:
            in_single_quote = not in_single_quote
        elif c == '"' and not in_single_quote and not in_backtick:
            in_double_quote = not in_double_quote
        elif c == '`' and not in_single_quote and not in_double_quote:
            in_backtick = not in_backtick
            
        # 遇到未闭合分号，视为语句结束
        if c == ';' and not in_single_quote and not in_double_quote and not in_backtick:
            stmt = "".join(current_statement).strip()
            if stmt:
                statements.append(stmt)
            current_statement = []
        else:
            current_statement.append(c)
            
        i += 1
        
    stmt = "".join(current_statement).strip()
    if stmt:
        statements.append(stmt)
        
    return statements


class _MySQLCompatConnection:
    """兼容SQLite风格的MySQL连接包装器

    提供 conn.execute() 和 conn.executescript() 方法，
    兼容原有基于sqlite3的代码，无需修改业务层。

    V2.1: close() 将底层连接归还连接池复用（回滚未提交事务后入池），
    池满或连接异常时才真正关闭。
    """
    def __init__(self, raw_conn):
        self._conn = raw_conn
        self._total_changes = 0
        self._closed = False
    
    def cursor(self):
        return _MySQLCompatCursor(self._conn.cursor())
    
    def execute(self, sql, params=None):
        """直接在连接上执行SQL（兼容sqlite3风格）"""
        cursor = self.cursor()
        cursor.execute(sql, params)
        self._total_changes += cursor.rowcount
        return cursor
    
    def executescript(self, sql_script):
        """执行多条SQL（兼容sqlite3风格，按分号分割）"""
        statements = split_sql_statements(sql_script)
        for stmt in statements:
            try:
                self._conn.cursor().execute(stmt)
            except Exception as e:
                logger.debug(f"executescript跳过: {str(e)[:80]}")

    
    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        """归还连接到池（幂等；异常连接直接关闭）"""
        if self._closed:
            return
        self._closed = True
        _checkin_connection(self._conn)
    
    @property
    def row_factory(self):
        return None
    
    @row_factory.setter
    def row_factory(self, value):
        # 忽略sqlite3.Row设置（DictCursor已提供字典访问）
        pass
    
    @property
    def total_changes(self):
        """兼容sqlite3的total_changes属性"""
        return self._total_changes


def _create_raw_connection():
    """新建底层pymysql连接（元数据库不存在时自动创建，errno 1049）"""
    try:
        return pymysql.connect(**MYSQL_CONFIG)
    except pymysql.err.OperationalError as e:
        if e.args and e.args[0] == 1049:  # Unknown database
            bootstrap_cfg = {k: v for k, v in MYSQL_CONFIG.items() if k != "database"}
            boot_conn = pymysql.connect(**bootstrap_cfg)
            try:
                with boot_conn.cursor() as cur:
                    cur.execute(
                        f"CREATE DATABASE IF NOT EXISTS `{MYSQL_CONFIG['database']}` "
                        f"DEFAULT CHARACTER SET utf8mb4")
                boot_conn.commit()
                logger.info("元数据库 %s 不存在，已自动创建", MYSQL_CONFIG['database'])
            finally:
                boot_conn.close()
            return pymysql.connect(**MYSQL_CONFIG)
        raise


def _checkout_connection():
    """从连接池取连接；空闲连接ping校验，失效则重建；池空则新建"""
    while True:
        try:
            raw = _conn_pool.get_nowait()
        except queue.Empty:
            return _create_raw_connection()
        try:
            raw.ping(reconnect=False)  # 仅校验存活；失效连接直接淘汰重建
            return raw
        except Exception:
            try:
                raw.close()
            except Exception:
                pass
            # 继续尝试池中下一个，直至池空新建


def _checkin_connection(raw):
    """归还连接到池：回滚未提交事务后入池；池满或连接异常则关闭"""
    try:
        raw.rollback()  # 清理事务状态，避免复用连接携带旧快照/未提交变更
        _conn_pool.put_nowait(raw)
    except queue.Full:
        try:
            raw.close()
        except Exception:
            pass
    except Exception:
        try:
            raw.close()
        except Exception:
            pass


def _get_connection() -> _MySQLCompatConnection:
    """获取MySQL数据库连接（兼容SQLite代码风格，V2.1连接池复用）

    返回一个包装后的连接对象，支持:
    - conn.execute(sql, params)  直接执行（自动 ? → %s）
    - conn.cursor().execute(sql, params)  通过游标执行
    - conn.executescript(sql)  批量执行
    - conn.commit() / conn.close()  close()归还连接池

    连接池容量由 SQLCHECK_DB_POOL_SIZE 控制（默认10）；
    元数据库不存在时自动创建（全新部署引导）。
    """
    return _MySQLCompatConnection(_checkout_connection())


def ensure_db():
    """确保数据库已初始化（线程安全懒加载）"""
    global _db_initialized
    if not _db_initialized:
        with _init_lock:
            if not _db_initialized:
                init_db()
                _db_initialized = True


def init_db():
    """初始化数据库 — 创建所有27张表 + 迁移 + 初始化默认数据"""
    conn = _get_connection()
    try:
        # 版本管理表
        _execute_sql(conn, """
            CREATE TABLE IF NOT EXISTS schema_version (
                `key`     VARCHAR(128) PRIMARY KEY,
                value     TEXT NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        # 检查是否需要迁移旧版表
        _migrate_old_tables(conn)

        # 创建所有表（IF NOT EXISTS确保幂等）
        _create_all_tables(conn)

        # 初始化默认数据
        _init_default_data(conn)

        conn.commit()
        logger.info("数据库初始化完成 (V2.0, MySQL)")
    except Exception as e:
        logger.error(f"数据库初始化失败: {e}")
        raise
    finally:
        conn.close()


def _execute_sql(conn, sql: str, params=None):
    """执行单条SQL"""
    cursor = conn.cursor()
    cursor.execute(sql, params)
    return cursor


def _executescript(conn, sql_script: str):
    """执行多条SQL（按分号分割，MySQL不原生支持executescript）

    单条失败时回滚并记录警告后继续后续语句（幂等DDL场景），
    不做重复执行（避免同一错误二次抛出）。
    """
    # 移除注释行
    lines = []
    for line in sql_script.split('\n'):
        stripped = line.strip()
        if stripped.startswith('--'):
            continue
        lines.append(line)
    sql_clean = '\n'.join(lines)

    # 按分号分割并执行
    statements = []
    current = []
    for line in sql_clean.split('\n'):
        current.append(line)
        if line.strip().endswith(';'):
            stmt = '\n'.join(current).strip().rstrip(';')
            if stmt.strip():
                statements.append(stmt)
            current = []

    for stmt in statements:
        try:
            conn.cursor().execute(stmt)
        except Exception as e:
            logger.warning(f"SQL执行警告(已跳过): {str(e)[:100]}")
            conn.rollback()


def _table_exists(conn, table_name: str) -> bool:
    """检查表是否存在"""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) AS cnt FROM information_schema.tables WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s",
        (MYSQL_CONFIG['database'], table_name)
    )
    return cursor.fetchone()['cnt'] > 0


def _get_table_names(conn) -> list[str]:
    """获取所有表名"""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT TABLE_NAME AS tn FROM information_schema.tables WHERE TABLE_SCHEMA = %s",
        (MYSQL_CONFIG['database'],)
    )
    return [row['tn'] for row in cursor.fetchall()]


def _migrate_old_tables(conn):
    """旧版表增量迁移 — 新增字段"""
    table_names = _get_table_names(conn)

    if "slow_queries" in table_names:
        _add_column_if_not_exists(conn, "slow_queries", "connection_id", "VARCHAR(64) DEFAULT ''")
        _add_column_if_not_exists(conn, "slow_queries", "project_id", "VARCHAR(64) DEFAULT ''")
        _add_column_if_not_exists(conn, "slow_queries", "normalized_sql", "TEXT")
        _add_column_if_not_exists(conn, "slow_queries", "distributed_analysis", "TEXT")
        _add_column_if_not_exists(conn, "slow_queries", "index_suggestions", "TEXT")
        _add_column_if_not_exists(conn, "slow_queries", "rewrite_suggestions", "TEXT")
        _add_column_if_not_exists(conn, "slow_queries", "assigned_to", "VARCHAR(64) DEFAULT ''")
        _add_column_if_not_exists(conn, "slow_queries", "updated_at", "DATETIME DEFAULT CURRENT_TIMESTAMP")
        _add_column_if_not_exists(conn, "slow_queries", "scan_task_id", "INT DEFAULT NULL")
        _add_column_if_not_exists(conn, "slow_queries", "set_id", "VARCHAR(512) DEFAULT ''")
        _add_column_if_not_exists(conn, "slow_queries", "client_user", "VARCHAR(128) DEFAULT ''")
        _add_column_if_not_exists(conn, "slow_queries", "client_host", "VARCHAR(128) DEFAULT ''")
        # 存量库：set_id 原为 VARCHAR(32)，多SET合并的分布(如 set_a(40),set_b(11))装不下，
        # 加宽到 VARCHAR(512)。仅当当前长度不足时执行 ALTER（避免每次启动重建表）。
        try:
            row = conn.execute("""
                SELECT CHARACTER_MAXIMUM_LENGTH AS len FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'slow_queries'
                  AND COLUMN_NAME = 'set_id'
            """).fetchone()
            cur_len = row["len"] if row and row["len"] is not None else None
            if cur_len is not None and int(cur_len) < 512:
                conn.execute("ALTER TABLE slow_queries MODIFY COLUMN set_id VARCHAR(512) DEFAULT ''")
        except Exception as e:
            logger.warning(f"widen slow_queries.set_id failed: {e}")

    if "tdsql_connections" in table_names:
        # 分布式实例 SET 列表（慢SQL digest 逐SET合并用）
        _add_column_if_not_exists(conn, "tdsql_connections", "set_list", "VARCHAR(512) DEFAULT ''")
        # monitordb（集群级慢SQL/监控数据源，端口 15001）连接信息
        _add_column_if_not_exists(conn, "tdsql_connections", "monitor_host", "VARCHAR(128) DEFAULT ''")
        _add_column_if_not_exists(conn, "tdsql_connections", "monitor_port", "INT DEFAULT 15001")
        _add_column_if_not_exists(conn, "tdsql_connections", "monitor_user", "VARCHAR(128) DEFAULT ''")
        _add_column_if_not_exists(conn, "tdsql_connections", "monitor_password_encrypted", "TEXT")
        _add_column_if_not_exists(conn, "tdsql_connections", "monitor_db", "VARCHAR(128) DEFAULT 'tdsqlpcloud_monitor'")

    if "slow_queries" in table_names:
        # monitordb 独有：DML 影响行数（digest 源为0）
        _add_column_if_not_exists(conn, "slow_queries", "rows_affected", "BIGINT DEFAULT 0")

    if "audit_history" in table_names:
        _add_column_if_not_exists(conn, "audit_history", "project_id", "VARCHAR(64) DEFAULT ''")
        _add_column_if_not_exists(conn, "audit_history", "connection_id", "VARCHAR(64) DEFAULT ''")
        _add_column_if_not_exists(conn, "audit_history", "gate_passed", "INT DEFAULT NULL")
        _add_column_if_not_exists(conn, "audit_history", "gate_detail", "TEXT")
        _add_column_if_not_exists(conn, "audit_history", "top_violations", "TEXT")
        _add_column_if_not_exists(conn, "audit_history", "results_summary", "TEXT")
        _add_column_if_not_exists(conn, "audit_history", "created_by", "VARCHAR(64) DEFAULT ''")

    conn.commit()


def _add_column_if_not_exists(conn, table: str, column: str, definition: str):
    """如果列不存在则添加（MySQL版）"""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) AS cnt FROM information_schema.columns WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (MYSQL_CONFIG['database'], table, column)
    )
    if cursor.fetchone()['cnt'] == 0:
        try:
            conn.cursor().execute(f"ALTER TABLE `{table}` ADD COLUMN `{column}` {definition}")
            logger.info(f"迁移: {table} 添加列 {column}")
        except pymysql.err.OperationalError:
            pass


def _create_all_tables(conn):
    """创建所有27张表"""

    # 迁移 scan_tasks 表新增字段（兼容已有数据库）
    if _table_exists(conn, "scan_tasks"):
        _add_column_if_not_exists(conn, "scan_tasks", "time_window_start", "VARCHAR(32) DEFAULT ''")
        _add_column_if_not_exists(conn, "scan_tasks", "time_window_end", "VARCHAR(32) DEFAULT ''")
        _add_column_if_not_exists(conn, "scan_tasks", "created_by", "VARCHAR(64) DEFAULT ''")

    table_ddls = [
        # T01. slow_queries
        """CREATE TABLE IF NOT EXISTS slow_queries (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            fingerprint         TEXT NOT NULL,
            sql_text            TEXT NOT NULL,
            normalized_sql      TEXT,
            db_name             VARCHAR(128) DEFAULT '',
            set_id              VARCHAR(512) DEFAULT '',
            connection_id       VARCHAR(64) DEFAULT '',
            project_id          VARCHAR(64) DEFAULT '',
            client_user         VARCHAR(128) DEFAULT '',
            client_host         VARCHAR(128) DEFAULT '',
            exec_count          INT DEFAULT 0,
            total_time_ms       DOUBLE DEFAULT 0,
            avg_time_ms         DOUBLE DEFAULT 0,
            max_time_ms         DOUBLE DEFAULT 0,
            rows_examined       INT DEFAULT 0,
            rows_sent           INT DEFAULT 0,
            rows_affected       BIGINT DEFAULT 0,
            lock_time_ms        DOUBLE DEFAULT 0,
            first_seen          VARCHAR(32),
            last_seen           VARCHAR(32),
            problem_type        VARCHAR(256) DEFAULT '',
            severity            VARCHAR(32) DEFAULT 'INFO',
            root_cause          TEXT,
            suggestion          TEXT,
            optimized_sql       TEXT,
            distributed_analysis TEXT,
            index_suggestions   TEXT,
            rewrite_suggestions TEXT,
            status              VARCHAR(32) DEFAULT 'pending',
            assigned_to         VARCHAR(64) DEFAULT '',
            scan_task_id        INT DEFAULT NULL,
            analysis_json       TEXT,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_slow_fingerprint (fingerprint(255)),
            INDEX idx_slow_db (db_name),
            INDEX idx_slow_set_id (set_id),
            INDEX idx_slow_status (status),
            INDEX idx_slow_connection (connection_id),
            INDEX idx_slow_project (project_id),
            INDEX idx_slow_last_seen (last_seen),
            INDEX idx_slow_severity (severity)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        # T02. audit_history
        """CREATE TABLE IF NOT EXISTS audit_history (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            audit_type          VARCHAR(64) NOT NULL,
            source              TEXT,
            project_id          VARCHAR(64) DEFAULT '',
            connection_id       VARCHAR(64) DEFAULT '',
            total_sql           INT DEFAULT 0,
            passed              INT DEFAULT 0,
            failed              INT DEFAULT 0,
            error_count         INT DEFAULT 0,
            warning_count       INT DEFAULT 0,
            pass_rate           DOUBLE DEFAULT 0,
            results_json        LONGTEXT,
            gate_passed         INT DEFAULT NULL,
            gate_detail         TEXT,
            top_violations      TEXT,
            results_summary     TEXT,
            created_by          VARCHAR(64) DEFAULT '',
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_audit_type (audit_type),
            INDEX idx_audit_project (project_id),
            INDEX idx_audit_created (created_at),
            INDEX idx_audit_gate (gate_passed)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        # T03. audit_results
        """CREATE TABLE IF NOT EXISTS audit_results (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            audit_history_id    INT NOT NULL,
            sql_text            TEXT NOT NULL,
            sql_type            VARCHAR(32) DEFAULT '',
            line_number         INT,
            file_path           TEXT,
            passed              INT DEFAULT 1,
            violations_json     TEXT,
            error_count         INT DEFAULT 0,
            warning_count       INT DEFAULT 0,
            triggered_rules     TEXT,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (audit_history_id) REFERENCES audit_history(id) ON DELETE CASCADE,
            INDEX idx_results_history (audit_history_id),
            INDEX idx_results_passed (passed)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        # T04. rule_configs
        """CREATE TABLE IF NOT EXISTS rule_configs (
            rule_id             VARCHAR(64) PRIMARY KEY,
            category            VARCHAR(64) NOT NULL,
            severity            VARCHAR(32) NOT NULL,
            description         TEXT NOT NULL,
            spec_source         TEXT,
            fix_suggestion      TEXT,
            enabled             INT DEFAULT 1,
            is_builtin          INT DEFAULT 1,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_rule_category (category),
            INDEX idx_rule_enabled (enabled)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        # T05. rule_whitelist
        """CREATE TABLE IF NOT EXISTS rule_whitelist (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            rule_id             VARCHAR(64) NOT NULL,
            table_pattern       TEXT,
            sql_pattern         TEXT,
            reason              TEXT,
            created_by          VARCHAR(64) DEFAULT '',
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_whitelist_rule (rule_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        # T06. gate_rules
        """CREATE TABLE IF NOT EXISTS gate_rules (
            project_id          VARCHAR(64) PRIMARY KEY,
            max_error_count     INT DEFAULT 0,
            max_warning_count   INT DEFAULT -1,
            required_rules      TEXT,
            blocked_rules       TEXT,
            description         TEXT,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        # T07. gate_audit_logs
        """CREATE TABLE IF NOT EXISTS gate_audit_logs (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            project_id          VARCHAR(64) NOT NULL,
            audit_history_id    INT,
            source              TEXT,
            passed              INT NOT NULL,
            error_count         INT DEFAULT 0,
            warning_count       INT DEFAULT 0,
            blocked_by          TEXT,
            detail              TEXT,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (audit_history_id) REFERENCES audit_history(id) ON DELETE SET NULL,
            INDEX idx_gate_project (project_id),
            INDEX idx_gate_passed (passed),
            INDEX idx_gate_created (created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        # T08. tdsql_connections
        """CREATE TABLE IF NOT EXISTS tdsql_connections (
            id                  VARCHAR(64) PRIMARY KEY,
            name                VARCHAR(128) NOT NULL,
            host                VARCHAR(256) NOT NULL,
            port                INT NOT NULL,
            username            VARCHAR(64) NOT NULL,
            password_encrypted  TEXT NOT NULL,
            `database`          VARCHAR(128) DEFAULT '',
            charset             VARCHAR(32) DEFAULT 'utf8mb4',
            is_default          INT DEFAULT 0,
            is_distributed      INT DEFAULT 1,
            description         TEXT,
            set_list            VARCHAR(512) DEFAULT '',
            monitor_host        VARCHAR(128) DEFAULT '',
            monitor_port        INT DEFAULT 15001,
            monitor_user        VARCHAR(128) DEFAULT '',
            monitor_password_encrypted TEXT,
            monitor_db          VARCHAR(128) DEFAULT 'tdsqlpcloud_monitor',
            status              VARCHAR(32) DEFAULT 'disconnected',
            last_connected_at   VARCHAR(32),
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_conn_default (is_default)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        # T09. bigtable_inventory
        """CREATE TABLE IF NOT EXISTS bigtable_inventory (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            connection_id       VARCHAR(64) NOT NULL,
            schema_name         VARCHAR(128) NOT NULL,
            table_name          VARCHAR(256) NOT NULL,
            size_gb             DOUBLE DEFAULT 0,
            size_mb             DOUBLE DEFAULT 0,
            rows_count          BIGINT DEFAULT 0,
            index_size_mb       DOUBLE DEFAULT 0,
            daily_inc_mb        DOUBLE DEFAULT 0,
            level               VARCHAR(16) NOT NULL,
            is_partitioned      INT DEFAULT 0,
            partition_count     INT DEFAULT 0,
            has_global_index    INT DEFAULT 0,
            shard_key           VARCHAR(128) DEFAULT '',
            inspection_date     VARCHAR(32) NOT NULL,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uk_bt (connection_id, schema_name(128), table_name(128), inspection_date),
            INDEX idx_bt_level (level),
            INDEX idx_bt_connection (connection_id),
            INDEX idx_bt_date (inspection_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        # T10. bigtable_classification
        """CREATE TABLE IF NOT EXISTS bigtable_classification (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            connection_id       VARCHAR(64) NOT NULL,
            schema_name         VARCHAR(128) NOT NULL,
            table_name          VARCHAR(256) NOT NULL,
            table_type          VARCHAR(32) NOT NULL,
            table_type_label    VARCHAR(64) DEFAULT '',
            retention_days      INT DEFAULT 0,
            archive_target      VARCHAR(128) DEFAULT '',
            archive_period      VARCHAR(64) DEFAULT '',
            partition_key       VARCHAR(128) DEFAULT '',
            partition_granularity VARCHAR(32) DEFAULT '',
            classified_by       VARCHAR(64) DEFAULT '',
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_bc (connection_id, schema_name(128), table_name(128))
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        # T11. partition_watermarks
        """CREATE TABLE IF NOT EXISTS partition_watermarks (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            connection_id       VARCHAR(64) NOT NULL,
            schema_name         VARCHAR(128) NOT NULL,
            table_name          VARCHAR(256) NOT NULL,
            partition_count     INT NOT NULL,
            watermark_percent   DOUBLE DEFAULT 0,
            status              VARCHAR(32) NOT NULL,
            check_date          VARCHAR(32) NOT NULL,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uk_pw (connection_id, schema_name(128), table_name(128), check_date),
            INDEX idx_pw_status (status),
            INDEX idx_pw_date (check_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        # T12. change_controls
        """CREATE TABLE IF NOT EXISTS change_controls (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            connection_id       VARCHAR(64) NOT NULL,
            schema_name         VARCHAR(128) NOT NULL,
            table_name          VARCHAR(256) NOT NULL,
            table_level         VARCHAR(16) NOT NULL,
            change_type         VARCHAR(32) NOT NULL,
            change_sql          TEXT NOT NULL,
            reason              TEXT,
            stage               VARCHAR(32) DEFAULT 'submitted',
            backup_completed    INT DEFAULT 0,
            ticket_approved     INT DEFAULT 0,
            window_applied      INT DEFAULT 0,
            executed_at         VARCHAR(32),
            executed_by         VARCHAR(64) DEFAULT '',
            result              TEXT,
            post_check_status   VARCHAR(32) DEFAULT '',
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_cc_stage (stage),
            INDEX idx_cc_level (table_level)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        # T13. inspection_tasks
        """CREATE TABLE IF NOT EXISTS inspection_tasks (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            connection_id       VARCHAR(64) NOT NULL,
            inspection_type     VARCHAR(32) NOT NULL,
            status              VARCHAR(32) DEFAULT 'pending',
            started_at          VARCHAR(32),
            completed_at        VARCHAR(32),
            error_message       TEXT,
            report_path         TEXT,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_it_status (status),
            INDEX idx_it_type (inspection_type),
            INDEX idx_it_date (created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        # T14. inspection_results
        """CREATE TABLE IF NOT EXISTS inspection_results (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            task_id             INT NOT NULL,
            category            VARCHAR(64) NOT NULL,
            severity            VARCHAR(32) NOT NULL,
            schema_name         VARCHAR(128) DEFAULT '',
            table_name          VARCHAR(256) DEFAULT '',
            metric_name         VARCHAR(128) DEFAULT '',
            metric_value        TEXT,
            threshold           TEXT,
            message             TEXT NOT NULL,
            suggestion          TEXT,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (task_id) REFERENCES inspection_tasks(id) ON DELETE CASCADE,
            INDEX idx_ir_task (task_id),
            INDEX idx_ir_severity (severity),
            INDEX idx_ir_category (category)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        # T15. alerts
        """CREATE TABLE IF NOT EXISTS alerts (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            connection_id       VARCHAR(64) NOT NULL,
            metric_name         VARCHAR(128) NOT NULL,
            metric_value        DOUBLE NOT NULL,
            level               VARCHAR(16) NOT NULL,
            threshold           DOUBLE NOT NULL,
            message             TEXT NOT NULL,
            status              VARCHAR(32) DEFAULT 'active',
            acknowledged_by     VARCHAR(64) DEFAULT '',
            acknowledged_at     VARCHAR(32),
            resolved_at         VARCHAR(32),
            notify_channels     TEXT,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_alert_status (status),
            INDEX idx_alert_level (level),
            INDEX idx_alert_connection (connection_id),
            INDEX idx_alert_created (created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        # T16. alert_rules
        """CREATE TABLE IF NOT EXISTS alert_rules (
            metric_name         VARCHAR(128) PRIMARY KEY,
            warning_threshold   DOUBLE NOT NULL,
            urgent_threshold    DOUBLE NOT NULL,
            check_interval_sec  INT DEFAULT 60,
            notify_webhook      TEXT,
            notify_email        TEXT,
            enabled             INT DEFAULT 1,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        # T17. projects
        """CREATE TABLE IF NOT EXISTS projects (
            project_id          VARCHAR(64) PRIMARY KEY,
            project_name        VARCHAR(128) NOT NULL,
            tdsql_connection_id VARCHAR(64) DEFAULT '',
            rule_set_id         VARCHAR(64) DEFAULT 'default',
            gate_rule_id        VARCHAR(64) DEFAULT 'default',
            gitlab_project_id   INT,
            gitlab_url          TEXT,
            description         TEXT,
            status              VARCHAR(32) DEFAULT 'active',
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        # T18. operation_logs
        """CREATE TABLE IF NOT EXISTS operation_logs (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            operator            VARCHAR(64) DEFAULT '',
            operation_type      VARCHAR(64) NOT NULL,
            target_type         VARCHAR(64) DEFAULT '',
            target_id           VARCHAR(128) DEFAULT '',
            detail              TEXT,
            ip_address          VARCHAR(64) DEFAULT '',
            user_agent          TEXT,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_log_operator (operator),
            INDEX idx_log_type (operation_type),
            INDEX idx_log_created (created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        # T19. fingerprint_stats
        """CREATE TABLE IF NOT EXISTS fingerprint_stats (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            connection_id       VARCHAR(64) NOT NULL,
            fingerprint         TEXT NOT NULL,
            sample_sql          TEXT NOT NULL,
            stat_date           VARCHAR(32) NOT NULL,
            exec_count          INT DEFAULT 0,
            total_time_ms       DOUBLE DEFAULT 0,
            avg_time_ms         DOUBLE DEFAULT 0,
            max_time_ms         DOUBLE DEFAULT 0,
            rows_examined       INT DEFAULT 0,
            rows_sent           INT DEFAULT 0,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uk_fp (connection_id, fingerprint(255), stat_date),
            INDEX idx_fp_connection (connection_id),
            INDEX idx_fp_date (stat_date),
            INDEX idx_fp_total_time (total_time_ms),
            INDEX idx_fp_exec_count (exec_count)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        # T20. optimization_records
        """CREATE TABLE IF NOT EXISTS optimization_records (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            slow_query_id       INT,
            connection_id       VARCHAR(64) NOT NULL,
            original_sql        TEXT NOT NULL,
            optimized_sql       TEXT NOT NULL,
            before_type         VARCHAR(64) DEFAULT '',
            before_key          TEXT,
            before_rows         INT DEFAULT 0,
            before_extra        TEXT,
            before_time_ms      DOUBLE DEFAULT 0,
            after_type          VARCHAR(64) DEFAULT '',
            after_key           TEXT,
            after_rows          INT DEFAULT 0,
            after_extra         TEXT,
            after_time_ms       DOUBLE DEFAULT 0,
            improvement         VARCHAR(128) DEFAULT '',
            improvement_detail  TEXT,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (slow_query_id) REFERENCES slow_queries(id) ON DELETE SET NULL,
            INDEX idx_opt_slow_query (slow_query_id),
            INDEX idx_opt_improvement (improvement)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        # T21. scan_tasks
        """CREATE TABLE IF NOT EXISTS scan_tasks (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            task_name           VARCHAR(256) NOT NULL,
            source              VARCHAR(32) DEFAULT 'manual',
            db_name             VARCHAR(128) DEFAULT '',
            connection_id       VARCHAR(64) DEFAULT '',
            connection_name     VARCHAR(256) DEFAULT '',
            time_window_start   VARCHAR(32) DEFAULT '',
            time_window_end     VARCHAR(32) DEFAULT '',
            created_by          VARCHAR(64) DEFAULT '',
            total_fetched       INT DEFAULT 0,
            total_analyzed      INT DEFAULT 0,
            status              VARCHAR(32) DEFAULT 'completed',
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_scan_task_db (db_name),
            INDEX idx_scan_task_source (source)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        # T22. users (V2.0认证授权)
        """CREATE TABLE IF NOT EXISTS users (
            id                    INT PRIMARY KEY AUTO_INCREMENT,
            username              VARCHAR(64) NOT NULL UNIQUE,
            display_name          VARCHAR(128) DEFAULT '',
            role                  VARCHAR(32) NOT NULL DEFAULT 'developer',
            password_hash         TEXT NOT NULL,
            salt                  TEXT NOT NULL,
            status                VARCHAR(16) DEFAULT 'active',
            must_change_password  INT DEFAULT 0,
            failed_attempts       INT DEFAULT 0,
            locked_until          VARCHAR(32) DEFAULT NULL,
            last_login_at         VARCHAR(32) DEFAULT NULL,
            created_by            VARCHAR(64) DEFAULT '',
            created_at            DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at            DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_users_role (role),
            INDEX idx_users_status (status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        # T23. rule_sets (V2.0多租户规则)
        """CREATE TABLE IF NOT EXISTS rule_sets (
            id                  VARCHAR(64) PRIMARY KEY,
            name                VARCHAR(128) NOT NULL,
            description         TEXT,
            is_builtin          INT DEFAULT 0,
            created_by          VARCHAR(64) DEFAULT '',
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        # T24. rule_set_items
        """CREATE TABLE IF NOT EXISTS rule_set_items (
            rule_set_id         VARCHAR(64) NOT NULL,
            rule_id             VARCHAR(64) NOT NULL,
            enabled             INT DEFAULT 1,
            severity_override   VARCHAR(32) DEFAULT NULL,
            PRIMARY KEY (rule_set_id, rule_id),
            INDEX idx_rsi_set (rule_set_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        # T25. scan_schedules (V2.0多实例调度)
        """CREATE TABLE IF NOT EXISTS scan_schedules (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            connection_id       VARCHAR(64) NOT NULL,
            source              VARCHAR(32) DEFAULT 'digest',
            cron_hour           INT DEFAULT 2,
            cron_minute         INT DEFAULT 0,
            limit_rows          INT DEFAULT 100,
            min_time            DOUBLE DEFAULT 1.0,
            enabled             INT DEFAULT 1,
            last_run_at         VARCHAR(32) DEFAULT NULL,
            last_run_status     VARCHAR(32) DEFAULT '',
            created_by          VARCHAR(64) DEFAULT '',
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_sched_conn (connection_id),
            INDEX idx_sched_enabled (enabled)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        # T26. retention_policies (V2.0数据生命周期)
        """CREATE TABLE IF NOT EXISTS retention_policies (
            table_name          VARCHAR(64) PRIMARY KEY,
            retention_days      INT NOT NULL,
            enabled             INT DEFAULT 1,
            updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        # T27. scheduler_lease (调度器leader租约)
        """CREATE TABLE IF NOT EXISTS scheduler_lease (
            id                  INT PRIMARY KEY,
            holder              VARCHAR(128) NOT NULL,
            expires_at          VARCHAR(32) NOT NULL,
            CONSTRAINT chk_lease_id CHECK (id = 1)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        # T28. system_config (系统配置键值对)
        """CREATE TABLE IF NOT EXISTS system_config (
            config_key          VARCHAR(64) PRIMARY KEY,
            config_value        VARCHAR(256) DEFAULT '',
            updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        # T29. roles (角色管理)
        """CREATE TABLE IF NOT EXISTS roles (
            role_id             VARCHAR(32) PRIMARY KEY,
            role_name           VARCHAR(64) NOT NULL,
            is_builtin          INT DEFAULT 0,
            description         TEXT,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        # T30. role_permissions (角色-菜单权限矩阵)
        """CREATE TABLE IF NOT EXISTS role_permissions (
            role_id             VARCHAR(32) NOT NULL,
            menu_key            VARCHAR(64) NOT NULL,
            visible             INT DEFAULT 1,
            PRIMARY KEY (role_id, menu_key),
            INDEX idx_rp_role (role_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    ]

    for ddl in table_ddls:
        try:
            conn.cursor().execute(ddl)
        except Exception as e:
            logger.warning(f"建表警告(可忽略): {str(e)[:120]}")


def _init_default_data(conn):
    """初始化默认数据：规则配置、门禁规则、告警规则"""

    # 默认门禁规则
    conn.cursor().execute("""
        INSERT IGNORE INTO gate_rules(project_id, max_error_count, max_warning_count, description)
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
        conn.cursor().execute("""
            INSERT IGNORE INTO alert_rules(metric_name, warning_threshold, urgent_threshold, check_interval_sec, enabled)
            VALUES (%s, %s, %s, %s, %s)
        """, (metric, warning, urgent, interval, enabled))

    # V2.0: 默认规则集
    conn.cursor().execute("""
        INSERT IGNORE INTO rule_sets(id, name, description, is_builtin, created_by)
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
        conn.cursor().execute("""
            INSERT IGNORE INTO retention_policies(table_name, retention_days, enabled)
            VALUES (%s, %s, 1)
        """, (table, days))

    # V3.0: 初始化内置角色
    builtin_roles = [
        ('admin', '系统管理员', 1, '拥有全部权限'),
        ('dba', '数据库管理员', 1, '管理实例/扫描/规则集/门禁'),
        ('developer', '开发人员', 1, 'SQL审核/EXPLAIN分析'),
        ('auditor', '审计员', 1, '只读审计/操作日志/报告导出'),
    ]
    for rid, rname, builtin, desc in builtin_roles:
        conn.cursor().execute("""
            INSERT IGNORE INTO roles(role_id, role_name, is_builtin, description)
            VALUES (%s, %s, %s, %s)
        """, (rid, rname, builtin, desc))

    # V3.0: 初始化角色权限矩阵（全菜单默认可见=1）
    all_menus = [
        'dashboard', 'audit-sql', 'file-audit', 'rules',
        'slow-tasks', 'slow-records', 'slow-schedule', 'explain',
        'instances', 'health-check', 'schema-check', 'bigtable',
        'projects', 'rulesets', 'gate', 'monitor', 'inspection',
        'sys-users', 'sys-retention', 'sys-auditlog', 'sys-info',
        'sys-roles', 'sys-perms',
    ]
    for rid, _, _, _ in builtin_roles:
        for mk in all_menus:
            visible = 1
            # developer 默认不可见监控/巡检/扫描计划/用户管理/数据保留/系统信息
            if rid == 'developer' and mk in ('monitor', 'inspection', 'slow-schedule', 'sys-users', 'sys-retention', 'sys-info', 'sys-roles', 'sys-perms'):
                visible = 0
            # auditor 默认不可见扫描计划/用户管理/数据保留/角色管理/上线检查(只读角色不可执行POST)
            if rid == 'auditor' and mk in ('slow-schedule', 'sys-users', 'sys-retention', 'sys-roles', 'sys-perms', 'schema-check'):
                visible = 0
            conn.cursor().execute("""
                INSERT IGNORE INTO role_permissions(role_id, menu_key, visible)
                VALUES (%s, %s, %s)
            """, (rid, mk, visible))

    # V3.1: schema-check 授予 admin/dba/developer 可见，auditor(只读)不可见
    for rid in ('admin', 'dba', 'developer'):
        conn.cursor().execute("""
            INSERT IGNORE INTO role_permissions(role_id, menu_key, visible)
            VALUES (%s, 'schema-check', 1)
        """, (rid,))
    # 存量库订正：auditor 若被旧迁移写入 visible=1，改回 0
    conn.cursor().execute("""
        UPDATE role_permissions SET visible=0
        WHERE menu_key='schema-check' AND role_id='auditor'
    """)

    # 更新版本号
    conn.cursor().execute("""
        REPLACE INTO schema_version(`key`, value) VALUES('version', '2.0')
    """)

    conn.commit()


def init_rule_configs(conn=None):
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
            conn.cursor().execute("""
                INSERT IGNORE INTO rule_configs
                (rule_id, category, severity, description, spec_source, fix_suggestion, enabled, is_builtin)
                VALUES (%s, %s, %s, %s, %s, %s, 1, 1)
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
        conn.cursor().execute("""
            INSERT INTO operation_logs(operator, operation_type, target_type, target_id, detail, ip_address, user_agent)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (operator, operation_type, target_type, target_id, detail, ip_address, user_agent))
        conn.commit()
    finally:
        conn.close()
