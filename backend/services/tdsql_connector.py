"""
TDSQL SQL审核工具 - TDSQL数据库连接器

提供TDSQL MySQL实例的连接、元数据查询和慢查询抓取能力。

功能：
1. 连接TDSQL实例（通过MySQL协议）
2. 查询表元数据（分片键、索引、字段类型等）
3. 抓取慢查询日志
4. 执行EXPLAIN分析
"""
import re
import threading
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Optional

# 尝试导入 pymysql，如果不可用则使用模拟模式
try:
    import pymysql
    import pymysql.cursors
    HAS_PYMYSQL = True
except ImportError:
    HAS_PYMYSQL = False


@dataclass
class TDSQLConnectionConfig:
    """TDSQL连接配置"""
    host: str = ""
    port: int = 3306
    user: str = ""
    password: str = ""
    database: str = ""
    charset: str = "utf8mb4"
    connect_timeout: int = 5
    read_timeout: int = 10


@dataclass
class TableMetadata:
    """表元数据"""
    table_name: str = ""
    table_type: str = ""
    engine: str = ""
    charset: str = ""
    table_collation: str = ""
    table_comment: str = ""
    table_rows: int = 0
    data_length: int = 0
    index_length: int = 0
    shard_key: Optional[str] = None       # 分片键
    is_shard_table: bool = False           # 是否分片表
    is_broadcast_table: bool = False       # 是否广播表
    is_single_table: bool = False          # 是否单表
    columns: list[dict] = field(default_factory=list)
    indexes: list[dict] = field(default_factory=list)
    create_sql: str = ""


@dataclass
class IndexInfo:
    """索引信息"""
    table_name: str = ""
    index_name: str = ""
    column_name: str = ""
    seq_in_index: int = 0
    non_unique: int = 1
    index_type: str = ""
    cardinality: int = 0


class TDSQLConnectionPool:
    """
    TDSQL 连接池。

    使用固定数量的连接复用，避免每次请求都创建新连接。
    线程安全，使用 thread-local 存储让每个线程独立使用一个连接，
    避免线程竞争同一连接的瓶颈。
    """

    DEFAULT_POOL_SIZE = 5

    def __init__(self, config: TDSQLConnectionConfig, pool_size: int = None):
        self.config = config
        self.pool_size = pool_size or self.DEFAULT_POOL_SIZE
        self._local = threading.local()

    def _create_connection(self):
        """创建新连接"""
        conn = pymysql.connect(
            host=self.config.host,
            port=self.config.port,
            user=self.config.user,
            password=self.config.password,
            database=self.config.database,
            charset=self.config.charset,
            connect_timeout=self.config.connect_timeout,
            read_timeout=self.config.read_timeout,
            cursorclass=pymysql.cursors.DictCursor,
        )
        return conn

    def _get_thread_connection(self):
        """获取当前线程的连接（线程本地存储）"""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.ping(reconnect=False)
                return conn
            except Exception:
                conn = None
        # 当前线程没有连接，创建新连接
        conn = self._create_connection()
        self._local.conn = conn
        return conn

    @contextmanager
    def get_connection(self):
        """
        从连接池获取一个连接（上下文管理器）。

        使用方式：
            with pool.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(...)
        """
        conn = self._get_thread_connection()
        try:
            yield conn
        except Exception:
            # 连接异常时，重新创建连接
            try:
                self._local.conn.close()
            except Exception:
                pass
            self._local.conn = self._create_connection()
            yield self._local.conn

    def is_connected(self) -> bool:
        """检查连接状态（检查当前线程连接）"""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            return False
        try:
            conn.ping(reconnect=False)
            return True
        except Exception:
            return False

    def close_all(self):
        """关闭所有线程的连接"""
        conn = getattr(self._local, "conn", None)
        if conn:
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None


class TDSQLConnector:
    """TDSQL数据库连接器（单连接模式，用于向后兼容）"""

    def __init__(self, config: TDSQLConnectionConfig):
        self.config = config
        self._connection = None

    def connect(self) -> bool:
        """建立数据库连接"""
        if not HAS_PYMYSQL:
            raise ImportError("pymysql 未安装，请执行: pip install pymysql")

        try:
            self._connection = pymysql.connect(
                host=self.config.host,
                port=self.config.port,
                user=self.config.user,
                password=self.config.password,
                database=self.config.database,
                charset=self.config.charset,
                connect_timeout=self.config.connect_timeout,
                read_timeout=self.config.read_timeout,
                cursorclass=pymysql.cursors.DictCursor,
            )
            return True
        except Exception as e:
            raise ConnectionError(f"连接TDSQL失败: {e}")

    def disconnect(self):
        """关闭连接"""
        if self._connection:
            try:
                self._connection.close()
            except Exception:
                pass
            self._connection = None

    def is_connected(self) -> bool:
        """检查连接状态"""
        if not self._connection:
            return False
        try:
            self._connection.ping(reconnect=False)
            return True
        except Exception:
            return False

    def _execute(self, sql: str, params: tuple = None) -> list[dict]:
        """执行SQL查询"""
        if not self.is_connected():
            self.connect()
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(sql, params)
                return cursor.fetchall()
        except Exception as e:
            # 尝试重连
            try:
                self.connect()
                with self._connection.cursor() as cursor:
                    cursor.execute(sql, params)
                    return cursor.fetchall()
            except Exception:
                raise RuntimeError(f"SQL执行失败: {e}")

    # ── 元数据查询 ─────────────────────────────────────────

    def get_tables(self, database: str = None) -> list[dict]:
        """获取数据库中的所有表"""
        db = database or self.config.database
        return self._execute("""
            SELECT TABLE_NAME, TABLE_TYPE, ENGINE, TABLE_COLLATION,
                   TABLE_COMMENT, TABLE_ROWS,
                   DATA_LENGTH, INDEX_LENGTH
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE'
            ORDER BY TABLE_NAME
        """, (db,))

    def get_table_metadata(self, table_name: str, database: str = None) -> TableMetadata:
        """获取表的完整元数据"""
        db = database or self.config.database

        # 表基本信息
        table_info = self._execute("""
            SELECT TABLE_NAME, TABLE_TYPE, ENGINE, TABLE_COLLATION,
                   TABLE_COMMENT, TABLE_ROWS, DATA_LENGTH, INDEX_LENGTH
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
        """, (db, table_name))

        meta = TableMetadata()
        if table_info:
            info = table_info[0]
            meta.table_name = info["TABLE_NAME"]
            meta.engine = info.get("ENGINE", "")
            meta.charset = info.get("TABLE_COLLATION", "").split("_")[0] if info.get("TABLE_COLLATION") else ""
            meta.table_collation = info.get("TABLE_COLLATION", "")
            meta.table_comment = info.get("TABLE_COMMENT", "")
            meta.table_rows = info.get("TABLE_ROWS", 0) or 0
            meta.data_length = info.get("DATA_LENGTH", 0) or 0
            meta.index_length = info.get("INDEX_LENGTH", 0) or 0

        # 字段信息
        meta.columns = self._execute("""
            SELECT COLUMN_NAME, DATA_TYPE, COLUMN_TYPE, IS_NULLABLE,
                   COLUMN_DEFAULT, COLUMN_KEY, COLUMN_COMMENT,
                   CHARACTER_SET_NAME, COLLATION_NAME
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
            ORDER BY ORDINAL_POSITION
        """, (db, table_name))

        # 索引信息
        meta.indexes = self._execute("""
            SELECT INDEX_NAME, COLUMN_NAME, SEQ_IN_INDEX,
                   NON_UNIQUE, INDEX_TYPE, CARDINALITY
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
            ORDER BY INDEX_NAME, SEQ_IN_INDEX
        """, (db, table_name))

        # 获取建表语句
        try:
            create_result = self._execute(f"SHOW CREATE TABLE `{db}`.`{table_name}`")
            if create_result:
                meta.create_sql = create_result[0].get("Create Table", "")
        except Exception:
            pass

        # 检测分片表信息（TDSQL特有）
        self._detect_shard_info(meta, db, table_name)

        return meta

    def _detect_shard_info(self, meta: TableMetadata, db: str, table_name: str):
        """检测TDSQL分片信息"""
        try:
            # 尝试查询TDSQL分片信息（通过TDSQL特有的系统表或SHOW语法）
            # TDSQL 分片表会在表名后有特定标记，或者通过 SHOW SHARDING RULES 获取

            # 方法1: 检查是否有 _tdsql_subp 后缀（二级分区表）
            if "_tdsql_subp" in table_name:
                meta.is_shard_table = True

            # 方法2: 通过建表语句检测分片键
            create_sql = meta.create_sql.upper() if meta.create_sql else ""
            if "SHARDKEY" in create_sql or "SHARD_KEY" in create_sql:
                meta.is_shard_table = True
                # 提取分片键
                shard_match = re.search(r"SHARDKEY\s*=?\s*\(?([^)]+)\)?", create_sql, re.IGNORECASE)
                if shard_match:
                    raw_key = shard_match.group(1).strip()
                    meta.shard_key = raw_key.strip('`"\'')

            # 方法3: 检查是否是广播表（TDSQL BROADCAST 表）
            if "BROADCAST" in create_sql:
                meta.is_broadcast_table = True

            # 方法4: 尝试查询 TDSQL 特有的分片元数据
            try:
                shard_info = self._execute("""
                    SELECT * FROM information_schema.TDSQL_SHARDING_RULES
                    WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
                """, (db, table_name))
                if shard_info:
                    meta.is_shard_table = True
                    meta.shard_key = shard_info[0].get("SHARD_KEY", "")
            except Exception:
                pass

            # 如果没有检测到分片信息，标记为单表
            if not meta.is_shard_table and not meta.is_broadcast_table:
                meta.is_single_table = True

        except Exception:
            pass

    def get_columns(self, table_name: str, database: str = None) -> list[dict]:
        """获取表的字段信息"""
        db = database or self.config.database
        return self._execute("""
            SELECT COLUMN_NAME, DATA_TYPE, COLUMN_TYPE, IS_NULLABLE,
                   COLUMN_DEFAULT, COLUMN_KEY, COLUMN_COMMENT,
                   CHARACTER_SET_NAME, COLLATION_NAME, ORDINAL_POSITION
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
            ORDER BY ORDINAL_POSITION
        """, (db, table_name))

    def get_indexes(self, table_name: str, database: str = None) -> list[dict]:
        """获取表的索引信息"""
        db = database or self.config.database
        return self._execute("""
            SELECT INDEX_NAME, COLUMN_NAME, SEQ_IN_INDEX,
                   NON_UNIQUE, INDEX_TYPE, CARDINALITY
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
            ORDER BY INDEX_NAME, SEQ_IN_INDEX
        """, (db, table_name))

    # ── 慢查询抓取 ─────────────────────────────────────────

    def get_slow_queries_from_processlist(self, min_time: int = 5) -> list[dict]:
        """从 processlist 获取当前正在执行的慢SQL"""
        return self._execute("""
            SELECT id, user, host, db, command, time, state, info
            FROM information_schema.processlist
            WHERE command <> 'Sleep' AND time > %s
            ORDER BY time DESC
        """, (min_time,))

    def get_slow_queries_from_digest(self, limit: int = 50) -> list[dict]:
        """从 performance_schema 获取TopN慢SQL摘要"""
        return self._execute("""
            SELECT SCHEMA_NAME, DIGEST_TEXT,
                   COUNT_STAR AS exec_count,
                   ROUND(SUM_TIMER_WAIT/1e9/1000, 2) AS total_seconds,
                   ROUND(AVG_TIMER_WAIT/1e9/1000, 2) AS avg_seconds,
                   ROUND(MAX_TIMER_WAIT/1e9/1000, 2) AS max_seconds,
                   SUM_ROWS_EXAMINED AS rows_examined,
                   SUM_ROWS_SENT AS rows_sent,
                   FIRST_SEEN, LAST_SEEN
            FROM performance_schema.events_statements_summary_by_digest
            WHERE SCHEMA_NAME NOT IN ('mysql', 'information_schema', 'performance_schema', 'sys')
            ORDER BY SUM_TIMER_WAIT DESC
            LIMIT %s
        """, (limit,))

    def get_slow_queries_from_slow_log(self, limit: int = 100, min_time: float = 1.0) -> list[dict]:
        """从 mysql.slow_log 表获取慢查询（如果开启）"""
        try:
            return self._execute("""
                SELECT start_time, user_host, query_time, lock_time,
                       rows_sent, rows_examined, db, sql_text
                FROM mysql.slow_log
                WHERE query_time > %s
                ORDER BY start_time DESC
                LIMIT %s
            """, (min_time, limit))
        except Exception:
            # slow_log 表可能不存在或未开启
            return []

    def get_slow_query_variables(self) -> dict:
        """获取慢查询相关配置"""
        result = self._execute("""
            SHOW VARIABLES WHERE Variable_name IN (
                'slow_query_log', 'long_query_time', 'log_queries_not_using_indexes',
                'log_slow_extra', 'min_examined_row_limit',
                'tdsql_compute_query_time_for_slow_logging'
            )
        """)
        return {row["Variable_name"]: row["Value"] for row in result}

    # ── EXPLAIN 分析 ────────────────────────────────────────

    def explain_query(self, sql: str) -> list[dict]:
        """执行EXPLAIN分析"""
        return self._execute(f"EXPLAIN {sql}")

    def explain_query_extended(self, sql: str) -> tuple[list[dict], str]:
        """执行EXPLAIN EXTENDED并获取SHOW WARNINGS"""
        explain_result = self._execute(f"EXPLAIN {sql}")
        warnings = ""
        try:
            warn_result = self._execute("SHOW WARNINGS")
            if warn_result:
                warnings = "\n".join(r.get("Message", "") for r in warn_result)
        except Exception:
            pass
        return explain_result, warnings

    # ── 字符集一致性检查 ────────────────────────────────────

    def check_charset_consistency(self, database: str = None) -> dict:
        """检查库内字符集一致性（参考慢SQL优化方案4.6.6）"""
        db = database or self.config.database

        # 检查库级别字符集
        db_charset = self._execute("""
            SELECT DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME
            FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = %s
        """, (db,))

        # 检查表级别字符集分布
        tableCharsets = self._execute("""
            SELECT TABLE_COLLATION, COUNT(*) AS cnt
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE'
            GROUP BY TABLE_COLLATION
        """, (db,))

        # 检查字段级别字符集不一致
        columnMismatches = self._execute("""
            SELECT C.TABLE_NAME, C.COLUMN_NAME, C.COLLATION_NAME AS col_collation,
                   T.TABLE_COLLATION AS tbl_collation
            FROM information_schema.COLUMNS C
            JOIN information_schema.TABLES T
              ON C.TABLE_SCHEMA = T.TABLE_SCHEMA AND C.TABLE_NAME = T.TABLE_NAME
            WHERE C.TABLE_SCHEMA = %s
              AND C.COLLATION_NAME IS NOT NULL
              AND C.COLLATION_NAME <> T.TABLE_COLLATION
            ORDER BY C.TABLE_NAME, C.ORDINAL_POSITION
        """, (db,))

        # 检查跨表同名字段字符集不一致
        crossTableMismatches = self._execute("""
            SELECT C1.COLUMN_NAME,
                   C1.TABLE_NAME AS tbl_a, C1.COLLATION_NAME AS col_a,
                   C2.TABLE_NAME AS tbl_b, C2.COLLATION_NAME AS col_b
            FROM information_schema.COLUMNS C1
            JOIN information_schema.COLUMNS C2
              ON C1.TABLE_SCHEMA = C2.TABLE_SCHEMA
              AND C1.COLUMN_NAME = C2.COLUMN_NAME
              AND C1.TABLE_NAME < C2.TABLE_NAME
              AND C1.COLLATION_NAME <> C2.COLLATION_NAME
            WHERE C1.TABLE_SCHEMA = %s
              AND C1.COLLATION_NAME IS NOT NULL
            ORDER BY C1.COLUMN_NAME, C1.TABLE_NAME
        """, (db,))

        return {
            "database": db,
            "db_charset": db_charset[0] if db_charset else {},
            "table_charset_distribution": [dict(r) for r in tableCharsets],
            "column_mismatches": [dict(r) for r in columnMismatches],
            "cross_table_mismatches": [dict(r) for r in crossTableMismatches],
            "is_consistent": len(columnMismatches) == 0 and len(crossTableMismatches) == 0,
        }

    # ── 大表检查 ────────────────────────────────────────────

    def check_large_tables(self, database: str = None, threshold_gb: float = 1.0) -> list[dict]:
        """检查大表（参考大表治理规范）"""
        db = database or self.config.database
        threshold_bytes = int(threshold_gb * 1024 * 1024 * 1024)

        return self._execute("""
            SELECT TABLE_NAME,
                   ROUND((DATA_LENGTH + INDEX_LENGTH)/1024/1024/1024, 2) AS size_gb,
                   TABLE_ROWS,
                   ROUND(DATA_LENGTH/1024/1024, 2) AS data_mb,
                   ROUND(INDEX_LENGTH/1024/1024, 2) AS index_mb,
                   CASE
                     WHEN (DATA_LENGTH + INDEX_LENGTH) >= 50*1024*1024*1024
                          OR TABLE_ROWS >= 200000000 THEN 'L3 特大表'
                     WHEN (DATA_LENGTH + INDEX_LENGTH) >= 10*1024*1024*1024
                          OR TABLE_ROWS >= 30000000 THEN 'L2 重点大表'
                     WHEN (DATA_LENGTH + INDEX_LENGTH) >= 1*1024*1024*1024
                          OR TABLE_ROWS >= 3000000 THEN 'L1 一般大表'
                     ELSE '一般表'
                   END AS level
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = %s
              AND TABLE_TYPE = 'BASE TABLE'
              AND (DATA_LENGTH + INDEX_LENGTH) >= %s
            ORDER BY (DATA_LENGTH + INDEX_LENGTH) DESC
        """, (db, threshold_bytes))
