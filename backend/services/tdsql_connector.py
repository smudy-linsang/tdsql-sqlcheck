"""
TDSQL SQL审核工具 - TDSQL数据库连接器

提供TDSQL MySQL实例的连接、元数据查询和慢查询抓取能力。

功能：
1. 连接TDSQL实例（通过MySQL协议）
2. 查询表元数据（分片键、索引、字段类型等）
3. 抓取慢查询日志
4. 执行EXPLAIN分析
"""
import logging
import re
import threading
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("tdsql.connector")

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
    # 分布式实例的 SET 列表（逗号分隔，如 set_1772437478_1,set_1772437504_3）。
    # 慢SQL digest 扫描逐 SET 查询后按 DIGEST 合并；为空则退回直查 Proxy（单 SET/集中式）。
    set_list: str = ""


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


# 大表检查：系统库排除清单（与 schema_inspector 口径一致）
_BIGTABLE_SYS_DBS = (
    "__tencentdb__", "information_schema", "mysql", "performance_schema",
    "query_rewrite", "sys", "sysdb", "test", "xa",
)


def build_large_tables_query(threshold_gb: float = 1.0, database: str = None) -> tuple:
    """构造大表检查 SQL 与参数（双源取大，兼容 TDSQL 分区表壳值）。

    数据源=information_schema.PARTITIONS（覆盖全部表：分区表逐分区一行，非分区表
    以 PARTITION_NAME=NULL 单行出现），按 (库,表) 聚合得合并大小(SUM part_bytes)与
    最大单分区(MAX max_part_bytes)；再与 information_schema.TABLES 的整表大小取
    GREATEST——本次缺陷根因是"某源对分区表返回 512KB 壳值"，双源取大可使任一源返回
    壳值时仍以另一源真实值为准，覆盖三种情况：①单表>阈值 ②分区表单分区>阈值
    ③分区表合并后>阈值。默认扫全部业务库；database 非空时仅扫该库（参数化传入，防注入）。

    Returns:
        (sql, params) 供 pool._execute(sql, params) 使用
    """
    threshold_bytes = int(threshold_gb * 1024 * 1024 * 1024)
    placeholders = ",".join(["%s"] * len(_BIGTABLE_SYS_DBS))
    db_filter = " AND TABLE_SCHEMA = %s" if database else ""

    sql = f"""
        SELECT
            agg.schema_name, agg.table_name, agg.is_partitioned, agg.partition_count,
            agg.rows_count,
            ROUND(GREATEST(agg.part_bytes, COALESCE(t.tab_bytes,0))/1024/1024/1024, 2) AS size_gb,
            ROUND(agg.max_part_bytes/1024/1024/1024, 2) AS max_partition_gb,
            ROUND(agg.part_data/1024/1024, 2)  AS data_mb,
            ROUND(agg.part_index/1024/1024, 2) AS index_mb,
            CASE
              WHEN GREATEST(agg.part_bytes, COALESCE(t.tab_bytes,0)) >= 50*1024*1024*1024
                   OR agg.rows_count >= 200000000 THEN 'L3 特大表'
              WHEN GREATEST(agg.part_bytes, COALESCE(t.tab_bytes,0)) >= 10*1024*1024*1024
                   OR agg.rows_count >= 30000000  THEN 'L2 重点大表'
              ELSE 'L1 一般大表'
            END AS level,
            CASE
              WHEN agg.is_partitioned = 0 THEN '单表超标'
              WHEN agg.max_part_bytes >= %s THEN '单分区超标'
              ELSE '合并超标'
            END AS trigger_type
        FROM (
            SELECT
                TABLE_SCHEMA AS schema_name, TABLE_NAME AS table_name,
                CASE WHEN SUM(PARTITION_NAME IS NOT NULL) > 0 THEN 1 ELSE 0 END AS is_partitioned,
                SUM(PARTITION_NAME IS NOT NULL)                       AS partition_count,
                SUM(COALESCE(TABLE_ROWS,0))                           AS rows_count,
                SUM(COALESCE(DATA_LENGTH,0)+COALESCE(INDEX_LENGTH,0)) AS part_bytes,
                MAX(COALESCE(DATA_LENGTH,0)+COALESCE(INDEX_LENGTH,0)) AS max_part_bytes,
                SUM(COALESCE(DATA_LENGTH,0))                          AS part_data,
                SUM(COALESCE(INDEX_LENGTH,0))                         AS part_index
            FROM information_schema.PARTITIONS
            WHERE TABLE_SCHEMA NOT IN ({placeholders})
              AND TABLE_NAME NOT LIKE '%%_tdsql_subp_auto_%%'{db_filter}
            GROUP BY TABLE_SCHEMA, TABLE_NAME
        ) agg
        LEFT JOIN (
            SELECT TABLE_SCHEMA, TABLE_NAME,
                   COALESCE(DATA_LENGTH,0)+COALESCE(INDEX_LENGTH,0) AS tab_bytes
            FROM information_schema.TABLES WHERE TABLE_TYPE='BASE TABLE'
        ) t ON t.TABLE_SCHEMA=agg.schema_name AND t.TABLE_NAME=agg.table_name
        WHERE GREATEST(agg.part_bytes, COALESCE(t.tab_bytes,0)) >= %s
        ORDER BY size_gb DESC
    """
    # 参数顺序（SQL 中 %s 自上而下）：trigger_type 阈值 → 系统库(9) → 可选 database → 外层 WHERE 阈值
    params = [threshold_bytes, *_BIGTABLE_SYS_DBS]
    if database:
        params.append(database)
    params.append(threshold_bytes)
    return sql, tuple(params)


def parse_shard_key_from_ddl(create_sql: str) -> str:
    """从 SHOW CREATE TABLE 的 DDL 中解析 TDSQL 分片键，无则返回空字符串。

    TDSQL 分布式表的建表语句尾部形如 `... COLLATE=utf8mb4_bin shardkey=id`
    或多列 `shardkey=(a,b)`。noshard/broadcast 表无此项，返回 ''（正确语义）。
    与 _detect_shard_info 同口径正则。
    """
    if not create_sql or "SHARDKEY" not in create_sql.upper():
        return ""
    m = re.search(r"SHARDKEY\s*=?\s*\(?([^)]+)\)?", create_sql, re.IGNORECASE)
    if m:
        return m.group(1).strip().strip('`"\'')
    return ""


# 分区数量水位阈值（与 bigtable_engine.PartitionMonitor 口径一致）
_PARTITION_MAX = 100
_PARTITION_WARN_PCT = 70
_PARTITION_CRIT_PCT = 85


def _analyze_partitions(partitions: list) -> dict:
    """基于逐分区明细计算派生分析（供下钻展示：数据倾斜/兜底分区过大/分区水位/空分区）。"""
    n = len(partitions)
    flags = []
    if n == 0:
        return {"max_partition": None, "avg_gb": 0.0, "skew_ratio": 0.0,
                "partition_count": 0, "flags": flags}

    sizes = [p["size_gb"] for p in partitions]
    max_p = max(partitions, key=lambda p: p["size_gb"])
    avg_gb = round(sum(sizes) / n, 3)
    skew = round(max_p["size_gb"] / avg_gb, 2) if avg_gb > 0 else 0.0

    # 1) 兜底 MAXVALUE 分区过大：未及时补建未来分区
    mv = next((p for p in partitions if p["is_maxvalue"]), None)
    if mv and mv["pct"] > 30:
        flags.append({
            "code": "maxvalue_oversized", "level": "warning",
            "msg": f"兜底分区 {mv['name']}(MAXVALUE) 占比 {mv['pct']}%，"
                   f"可能未及时补建未来分区，建议补建分区",
        })

    # 2) 数据倾斜：最大分区/平均 ≥ 3 倍
    if avg_gb > 0 and skew >= 3:
        flags.append({
            "code": "data_skew", "level": "warning",
            "msg": f"最大分区 {max_p['name']} 是平均的 {skew} 倍，存在数据倾斜",
        })

    # 3) 分区数量水位（复用 PartitionMonitor 阈值 100/70%/85%）
    pct_cnt = round(n / _PARTITION_MAX * 100, 1)
    if pct_cnt >= _PARTITION_CRIT_PCT:
        flags.append({"code": "too_many_partitions", "level": "danger",
                      "msg": f"分区数 {n} 已达上限 {_PARTITION_MAX} 的 {pct_cnt}%"})
    elif pct_cnt >= _PARTITION_WARN_PCT:
        flags.append({"code": "too_many_partitions", "level": "warning",
                      "msg": f"分区数 {n} 达上限 {_PARTITION_MAX} 的 {pct_cnt}%"})

    # 4) 空分区偏多
    empty = [p["name"] for p in partitions if p["rows"] == 0 and p["size_gb"] < 0.001]
    if len(empty) >= 3:
        flags.append({"code": "empty_partitions", "level": "info",
                      "msg": f"存在 {len(empty)} 个空分区"})

    return {
        "max_partition": {"name": max_p["name"], "size_gb": max_p["size_gb"]},
        "avg_gb": avg_gb, "skew_ratio": skew, "partition_count": n, "flags": flags,
    }


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
        self._connected = False  # 全局连接状态标志

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
        self._connected = True
        return conn

    def _execute(self, sql: str, params: tuple = None) -> list[dict]:
        """执行SQL查询（使用连接池）"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, params)
                return cursor.fetchall()

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
            # 连接异常时，关闭旧连接并重建，然后重新抛出原始异常
            try:
                self._local.conn.close()
            except Exception:
                pass
            self._local.conn = self._create_connection()
            raise

    def is_connected(self) -> bool:
        """检查连接状态（全局标志 + 线程本地ping）"""
        if not self._connected:
            return False
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.ping(reconnect=False)
                return True
            except Exception:
                pass
        # 线程本地无连接，尝试创建测试连接
        try:
            test_conn = self._create_connection()
            test_conn.close()
            return True
        except Exception:
            self._connected = False
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
        self._connected = False

    # ── 元数据查询（代理方法，使用连接池） ─────────────────────

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

        meta.columns = self._execute("""
            SELECT COLUMN_NAME, DATA_TYPE, COLUMN_TYPE, IS_NULLABLE,
                   COLUMN_DEFAULT, COLUMN_KEY, COLUMN_COMMENT,
                   CHARACTER_SET_NAME, COLLATION_NAME
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
            ORDER BY ORDINAL_POSITION
        """, (db, table_name))

        meta.indexes = self._execute("""
            SELECT INDEX_NAME, COLUMN_NAME, SEQ_IN_INDEX,
                   NON_UNIQUE, INDEX_TYPE, CARDINALITY
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
            ORDER BY INDEX_NAME, SEQ_IN_INDEX
        """, (db, table_name))

        try:
            create_result = self._execute(f"SHOW CREATE TABLE `{db}`.`{table_name}`")
            if create_result:
                meta.create_sql = create_result[0].get("Create Table", "")
        except Exception:
            pass

        self._detect_shard_info(meta, db, table_name)
        return meta

    def _detect_shard_info(self, meta: TableMetadata, db: str, table_name: str):
        """检测TDSQL分片信息"""
        try:
            if "_tdsql_subp" in table_name:
                meta.is_shard_table = True

            create_sql_upper = meta.create_sql.upper() if meta.create_sql else ""
            if "SHARDKEY" in create_sql_upper or "SHARD_KEY" in create_sql_upper:
                meta.is_shard_table = True
                # 在原始SQL上做正则匹配，保留原始大小写
                shard_match = re.search(r"SHARDKEY\s*=?\s*\(?([^)]+)\)?", meta.create_sql or "", re.IGNORECASE)
                if shard_match:
                    raw_key = shard_match.group(1).strip()
                    meta.shard_key = raw_key.strip('`"\'')

            if "BROADCAST" in create_sql_upper:
                meta.is_broadcast_table = True

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

    # ── SET 发现 ─────────────────────────────────────────

    def discover_sets(self) -> list[dict]:
        """发现 TDSQL 分布式实例的所有 SET（分片）

        通过 /*proxy*/show status 命令获取 SET 列表。
        对于非分布式实例（集中式），返回空列表。

        Returns:
            SET 列表，每项含 set_id 和 set_name
        """
        sets = []
        # 方式1: /*proxy*/show status（TDSQL Proxy 命令）
        try:
            rows = self._execute("/*proxy*/show status")
            for row in rows:
                # 兼容不同版本的返回格式
                # 可能的字段名: Variable_name/Value, Config_name/Value, name/value 等
                name = ""
                value = ""
                if isinstance(row, dict):
                    name = str(row.get("Variable_name", row.get("Config_name", row.get("status_name", row.get("name", row.get("Key", ""))))))
                    value = str(row.get("Value", row.get("value", "")))
                # 严格匹配 SET 相关行：name 精确为 "set" 或包含 "set_" / "set_id" 前缀
                name_lower = name.lower()
                if name_lower == "set":
                    # name="set" 时 value 是逗号分隔的 SET ID 列表
                    # 如 "set_1782132369_1,set_1782132389_3"
                    for s in value.split(","):
                        s = s.strip()
                        if s and s not in [x["set_id"] for x in sets]:
                            sets.append({"set_id": s, "set_name": s})
                elif "set_" in name_lower or "set_id" in name_lower:
                    # 从 value 中提取 set 名称（支持 set_N、set_N_M、setN 格式）
                    set_matches = re.findall(r"set_\d+_\d+|set_\d+|set\d+", value, re.IGNORECASE)
                    if set_matches:
                        for s in set_matches:
                            if s not in [x["set_id"] for x in sets]:
                                sets.append({"set_id": s, "set_name": s})
        except Exception as e:
            logger.debug(f"SET discovery via /*proxy*/show status failed: {e}")

        # 方式2（回退）: 如果方式1未发现 SET，尝试从 TDSQL_SHARDING_RULES 获取分片信息
        if not sets:
            try:
                shard_rows = self._execute("""
                    SELECT DISTINCT SHARD_TABLE_NAME
                    FROM information_schema.TDSQL_SHARDING_RULES
                    WHERE SHARD_TABLE_NAME IS NOT NULL
                    LIMIT 1
                """)
                # 如果有分片规则，说明是分布式实例，但无法获取 SET 名称
                if shard_rows:
                    logger.debug("Found sharding rules but could not discover SET names via /*proxy*/show status")
            except Exception as e:
                logger.debug(f"SET discovery via TDSQL_SHARDING_RULES failed: {e}")

        return sets

    @staticmethod
    def _build_set_hint(set_id: str = None) -> str:
        """构建 SET 路由 hint 前缀

        对 set_id 进行白名单校验，仅允许字母、数字、下划线和逗号，
        防止 SQL 注入攻击（如 set_id 中包含 */ 或 ; 等特殊字符）。
        """
        if set_id:
            if re.match(r'^[a-zA-Z0-9_,]+$', set_id):
                return f"/*sets:{set_id}*/"
            else:
                logger.warning(f"Invalid set_id format, ignoring SET hint: {set_id}")
        return ""

    # ── 慢查询抓取 ─────────────────────────────────────────

    def get_slow_queries_from_processlist(self, min_time: float = 0.1, set_id: str = None) -> list[dict]:
        """从 processlist 获取当前正在执行的慢SQL快照（单次）

        注意: 此方法仅捕获扫描瞬间正在执行且耗时超过阈值的SQL。
        对于TDSQL分布式实例，直接通过Proxy查询（不做SET路由）。
        推荐使用 poll_processlist() 进行多次轮询以提高捕获率。

        Args:
            min_time: 最小执行时间阈值（秒），默认0.1s
            set_id: 已废弃，保留参数兼容性但不使用
        """
        return self._execute("""
            SELECT id, user, host, db, command, time, state, info
            FROM information_schema.processlist
            WHERE command <> 'Sleep' AND time > %s AND info IS NOT NULL
            ORDER BY time DESC
        """, (min_time,))

    def poll_processlist(self, duration_seconds: float = 10.0, interval: float = 1.0, min_time: float = 0.1) -> list[dict]:
        """多次轮询 processlist，合并去重结果

        通过在指定时间窗口内重复采样 processlist，提高捕获短时慢SQL的概率。
        结果按 (db, info) 去重，保留最大执行时间和最后一次采样时间。

        Args:
            duration_seconds: 轮询持续时间（秒），默认10秒
            interval: 采样间隔（秒），默认1秒
            min_time: 最小执行时间阈值（秒），默认0.1s

        Returns:
            去重合并后的慢SQL列表，每项包含:
            id, user, host, db, command, time, state, info, sample_count
        """
        import time as _time

        captured = {}  # key: (db, info_normalized) -> merged record
        start = _time.time()
        sample_count = 0

        while (_time.time() - start) < duration_seconds:
            sample_count += 1
            try:
                rows = self._execute("""
                    SELECT id, user, host, db, command, time, state, info
                    FROM information_schema.processlist
                    WHERE command <> 'Sleep' AND time >= %s AND info IS NOT NULL
                      AND info NOT LIKE '%%processlist%%'
                    ORDER BY time DESC
                """, (min_time,))

                for row in rows:
                    info = row.get("info", "")
                    if isinstance(info, bytes):
                        info = info.decode("utf-8", errors="replace")
                    db = row.get("db", "") or ""
                    if isinstance(db, bytes):
                        db = db.decode("utf-8", errors="replace")

                    # 归一化key：去除首尾空格
                    key = (db.strip(), info.strip()[:500])

                    if key in captured:
                        # 合并：保留最大执行时间
                        existing = captured[key]
                        existing["time"] = max(existing["time"], row.get("time", 0) or 0)
                        existing["sample_count"] = existing.get("sample_count", 1) + 1
                    else:
                        captured[key] = {
                            "id": row.get("id"),
                            "user": row.get("user", ""),
                            "host": row.get("host", ""),
                            "db": db,
                            "command": row.get("command", ""),
                            "time": row.get("time", 0) or 0,
                            "state": row.get("state", ""),
                            "info": info,
                            "sample_count": 1,
                        }
            except Exception as e:
                logger.debug(f"processlist poll sample failed: {e}")

            # 等待间隔（最后一次不等待）
            remaining = duration_seconds - (_time.time() - start)
            if remaining > interval:
                _time.sleep(interval)
            elif remaining > 0.1:
                _time.sleep(min(remaining, 0.5))
            else:
                break

        logger.info(f"processlist poll: {sample_count} samples in {_time.time()-start:.1f}s, captured {len(captured)} unique queries")

        # 按执行时间降序排列
        result = sorted(captured.values(), key=lambda x: x["time"], reverse=True)
        return result

    def get_slow_queries_from_digest(self, limit: int = 50, min_time: float = 0.1, time_start: str = None, time_end: str = None, set_id: str = None, database: str = None) -> list[dict]:
        """从 performance_schema 获取TopN慢SQL摘要（分布式实例逐 SET 合并）。

        重要：TDSQL Proxy 对 performance_schema 的查询会**随机路由到某一个 SET**，
        并不会自动聚合所有 SET 的数据。因此对分布式实例，需按 config.set_list 配置的
        SET 列表逐个用 /*sets:xxx*/ hint 查询，再在应用层按 DIGEST 合并（正确口径：
        次数/耗时求和，平均=总耗时÷总次数重算，最大取MAX，首末次取MIN/MAX）。
        set_list 为空则退回直查 Proxy（集中式实例，或未配置 SET 列表）。

        时间单位: performance_schema 中 TIMER_WAIT 单位为皮秒(10^-12秒)，/1e12 = 秒。

        Args:
            limit: 返回条数
            min_time: 最小平均耗时阈值（秒），合并后按重算平均值过滤
            time_start/time_end: 时间窗口 (YYYY-MM-DD HH:MM:SS)
            set_id: 已废弃，保留参数兼容性但不使用
            database: 可选，仅返回指定数据库的SQL
        """
        sets = [s.strip() for s in (getattr(self.config, "set_list", "") or "").split(",") if s.strip()]
        if not sets:
            # 集中式实例 / 未配置 SET 列表：退回直查 Proxy（原行为）
            return self._query_digest_direct(limit, min_time, time_start, time_end, database)
        # 分布式实例：逐 SET 查询原始列 → 应用层按 DIGEST 合并
        per_set = []
        for s in sets:
            hint = self._build_set_hint(s)
            per_set.append((s, self._query_digest_raw(hint, time_start, time_end, database)))
        return self._merge_digest_across_sets(per_set, min_time, limit)

    def _query_digest_direct(self, limit, min_time, time_start, time_end, database) -> list[dict]:
        """直查 Proxy（不做 SET 路由）—— 集中式实例或未配置 SET 列表时使用。"""
        sql = """
            SELECT SCHEMA_NAME, DIGEST, DIGEST_TEXT,
                   COUNT_STAR AS exec_count,
                   ROUND(SUM_TIMER_WAIT/1e12, 4) AS total_seconds,
                   ROUND(AVG_TIMER_WAIT/1e12, 4) AS avg_seconds,
                   ROUND(MAX_TIMER_WAIT/1e12, 4) AS max_seconds,
                   SUM_ROWS_EXAMINED AS rows_examined,
                   SUM_ROWS_SENT AS rows_sent,
                   SUM_NO_INDEX_USED AS no_index_count,
                   SUM_LOCK_TIME/1e12 AS lock_time_seconds,
                   FIRST_SEEN, LAST_SEEN
            FROM performance_schema.events_statements_summary_by_digest
            WHERE SCHEMA_NAME NOT IN ('mysql', 'information_schema', 'performance_schema', 'sys')
              AND SCHEMA_NAME IS NOT NULL
              AND DIGEST_TEXT IS NOT NULL
        """
        params = []
        if database:
            sql += " AND SCHEMA_NAME = %s"
            params.append(database)
        if min_time and min_time > 0:
            sql += " AND AVG_TIMER_WAIT/1e12 >= %s"
            params.append(min_time)
        if time_start:
            sql += " AND LAST_SEEN >= %s"
            params.append(time_start)
        if time_end:
            sql += " AND FIRST_SEEN <= %s"
            params.append(time_end)
        sql += " ORDER BY SUM_TIMER_WAIT DESC LIMIT %s"
        params.append(limit)
        return self._execute(sql, tuple(params))

    def _query_digest_raw(self, hint: str, time_start, time_end, database) -> list[dict]:
        """逐 SET 查询原始计数列（不做 min_time 过滤/聚合），供应用层合并。"""
        sql = (hint or "") + """
            SELECT SCHEMA_NAME, DIGEST, DIGEST_TEXT,
                   COUNT_STAR, SUM_TIMER_WAIT, MAX_TIMER_WAIT,
                   SUM_ROWS_EXAMINED, SUM_ROWS_SENT, SUM_NO_INDEX_USED,
                   SUM_LOCK_TIME, FIRST_SEEN, LAST_SEEN
            FROM performance_schema.events_statements_summary_by_digest
            WHERE SCHEMA_NAME NOT IN ('mysql', 'information_schema', 'performance_schema', 'sys')
              AND SCHEMA_NAME IS NOT NULL
              AND DIGEST_TEXT IS NOT NULL
        """
        params = []
        if database:
            sql += " AND SCHEMA_NAME = %s"
            params.append(database)
        if time_start:
            sql += " AND LAST_SEEN >= %s"
            params.append(time_start)
        if time_end:
            sql += " AND FIRST_SEEN <= %s"
            params.append(time_end)
        return self._execute(sql, tuple(params))

    @staticmethod
    def _merge_digest_across_sets(per_set: list, min_time: float, limit: int) -> list[dict]:
        """按 DIGEST 跨 SET 合并原始计数（纯函数，便于单测）。

        per_set: [(set_id, [raw_row_dict]), ...]，raw_row 含 performance_schema 原始列。
        合并口径：次数/耗时/行数求和，平均=Σ总耗时÷Σ次数（重算，不能平均各SET的平均），
        最大取 MAX(MAX_TIMER_WAIT)，首/末次取 MIN(FIRST_SEEN)/MAX(LAST_SEEN)。
        合并后按重算平均值过滤 min_time，按总耗时降序取 TopN。
        """
        def _i(v):
            try:
                return int(v or 0)
            except (TypeError, ValueError):
                return 0

        merged = {}
        for sid, rows in per_set:
            for r in (rows or []):
                key = (r.get("SCHEMA_NAME"), r.get("DIGEST"))
                m = merged.get(key)
                if m is None:
                    m = {
                        "SCHEMA_NAME": r.get("SCHEMA_NAME"), "DIGEST": r.get("DIGEST"),
                        "DIGEST_TEXT": r.get("DIGEST_TEXT"),
                        "_count": 0, "_sum_timer": 0, "_max_timer": 0,
                        "_rows_examined": 0, "_rows_sent": 0, "_no_index": 0, "_lock": 0,
                        "FIRST_SEEN": r.get("FIRST_SEEN"), "LAST_SEEN": r.get("LAST_SEEN"),
                        "_sets": {},
                    }
                    merged[key] = m
                cnt = _i(r.get("COUNT_STAR"))
                m["_count"] += cnt
                m["_sum_timer"] += _i(r.get("SUM_TIMER_WAIT"))
                m["_max_timer"] = max(m["_max_timer"], _i(r.get("MAX_TIMER_WAIT")))
                m["_rows_examined"] += _i(r.get("SUM_ROWS_EXAMINED"))
                m["_rows_sent"] += _i(r.get("SUM_ROWS_SENT"))
                m["_no_index"] += _i(r.get("SUM_NO_INDEX_USED"))
                m["_lock"] += _i(r.get("SUM_LOCK_TIME"))
                if r.get("FIRST_SEEN") and (not m["FIRST_SEEN"] or str(r["FIRST_SEEN"]) < str(m["FIRST_SEEN"])):
                    m["FIRST_SEEN"] = r["FIRST_SEEN"]
                if r.get("LAST_SEEN") and (not m["LAST_SEEN"] or str(r["LAST_SEEN"]) > str(m["LAST_SEEN"])):
                    m["LAST_SEEN"] = r["LAST_SEEN"]
                if cnt:
                    m["_sets"][sid] = m["_sets"].get(sid, 0) + cnt

        out = []
        for m in merged.values():
            cnt = m["_count"]
            avg_seconds = (m["_sum_timer"] / cnt / 1e12) if cnt else 0.0
            if min_time and min_time > 0 and avg_seconds < min_time:
                continue
            out.append({
                "SCHEMA_NAME": m["SCHEMA_NAME"], "DIGEST": m["DIGEST"],
                "DIGEST_TEXT": m["DIGEST_TEXT"],
                "exec_count": cnt,
                "total_seconds": round(m["_sum_timer"] / 1e12, 4),
                "avg_seconds": round(avg_seconds, 4),
                "max_seconds": round(m["_max_timer"] / 1e12, 4),
                "rows_examined": m["_rows_examined"],
                "rows_sent": m["_rows_sent"],
                "no_index_count": m["_no_index"],
                "lock_time_seconds": round(m["_lock"] / 1e12, 6),
                "FIRST_SEEN": m["FIRST_SEEN"], "LAST_SEEN": m["LAST_SEEN"],
                "set_ids": ",".join(f"{k}({v})" for k, v in sorted(m["_sets"].items())),
            })
        out.sort(key=lambda x: x["total_seconds"], reverse=True)
        return out[:limit]

    # NOTE: get_slow_queries_from_slow_log() 已移除。
    # TDSQL分布式实例中，SET实例的mysql.slow_log表不记录数据，
    # 慢日志由Proxy层统一管理（写入本地文件由赤兔平台收集）。
    # 请使用 get_slow_queries_from_digest() 获取慢SQL统计。

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

    def get_proxy_config(self) -> dict:
        """获取Proxy层慢日志相关配置

        执行 /*proxy*/show config 获取Proxy配置，
        提取慢日志相关参数（slow_log_level, slow_log_ms等）。

        Returns:
            包含Proxy慢日志配置的字典，如:
            {
                "slow_log_level": "1",
                "slow_log_ms": "100",
                "all_config": {...}  # 完整配置
            }
        """
        try:
            rows = self._execute("/*proxy*/show config")
            all_config = {}
            slow_config = {}
            for row in rows:
                # show config 返回格式可能是 (config_name, config_value) 或 dict
                if isinstance(row, dict):
                    name = row.get("config_name") or row.get("name") or row.get("Variable_name", "")
                    value = row.get("config_value") or row.get("value") or row.get("Value", "")
                else:
                    continue
                all_config[name] = value
                # 提取慢日志相关配置
                if "slow" in name.lower() or "log" in name.lower():
                    slow_config[name] = value

            return {
                "slow_log_level": all_config.get("slow_log_level", "unknown"),
                "slow_log_ms": all_config.get("slow_log_ms", "unknown"),
                "slow_config": slow_config,
                "all_config": all_config,
            }
        except Exception as e:
            logger.warning(f"获取Proxy配置失败: {e}")
            return {
                "slow_log_level": "unknown",
                "slow_log_ms": "unknown",
                "slow_config": {},
                "all_config": {},
                "error": str(e),
            }

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
        """检查库内字符集一致性"""
        db = database or self.config.database

        db_charset = self._execute("""
            SELECT DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME
            FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = %s
        """, (db,))

        tableCharsets = self._execute("""
            SELECT TABLE_COLLATION, COUNT(*) AS cnt
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE'
            GROUP BY TABLE_COLLATION
        """, (db,))

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

    def get_shard_key(self, db: str, table: str) -> str:
        """获取表的 TDSQL 分片键（SHOW CREATE TABLE 解析），无则返回空字符串。"""
        try:
            rows = self._execute(f"SHOW CREATE TABLE `{db}`.`{table}`")
            if rows:
                return parse_shard_key_from_ddl(rows[0].get("Create Table", "") or "")
        except Exception:
            pass
        return ""

    def check_large_tables(self, database: str = None, threshold_gb: float = 1.0) -> list[dict]:
        """检查大表（双源取大 GREATEST(PARTITIONS聚合, TABLES值)，兼容 TDSQL 分区表壳值）

        覆盖三种情况：①单表>阈值 ②分区表单分区>阈值 ③分区表合并后>阈值。
        默认扫描全部业务库；database 非空时仅扫该库。详见 build_large_tables_query。
        对每张大表补一次分片键（大表数量少，SHOW CREATE 开销可忽略）。
        """
        sql, params = build_large_tables_query(threshold_gb, database)
        rows = self._execute(sql, params)
        for r in rows:
            r["shard_key"] = self.get_shard_key(r.get("schema_name", ""), r.get("table_name", ""))
        return rows

    def get_table_partitions(self, db: str, table: str) -> dict:
        """获取分区表逐分区明细 + 派生分析（供大表治理下钻）。

        数据源 information_schema.PARTITIONS（TDSQL proxy 已验证返回真实分区大小）。
        非分区表返回 is_partitioned=False、partitions=[]。
        """
        rows = self._execute("""
            SELECT PARTITION_NAME, PARTITION_ORDINAL_POSITION AS ordinal,
                   PARTITION_METHOD, PARTITION_EXPRESSION, PARTITION_DESCRIPTION,
                   COALESCE(TABLE_ROWS,0) AS rows_count,
                   COALESCE(DATA_LENGTH,0) AS data_bytes,
                   COALESCE(INDEX_LENGTH,0) AS index_bytes,
                   COALESCE(DATA_LENGTH,0)+COALESCE(INDEX_LENGTH,0) AS bytes,
                   CREATE_TIME, UPDATE_TIME
            FROM information_schema.PARTITIONS
            WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s
              AND PARTITION_NAME IS NOT NULL
            ORDER BY PARTITION_ORDINAL_POSITION
        """, (db, table))

        if not rows:
            return {"schema": db, "table": table, "is_partitioned": False,
                    "partition_method": "", "partition_expression": "",
                    "partition_count": 0, "total_gb": 0.0,
                    "partitions": [], "analysis": {"flags": []}}

        total_bytes = sum(int(r["bytes"] or 0) for r in rows) or 1
        partitions = []
        for r in rows:
            b = int(r["bytes"] or 0)
            desc = (str(r.get("PARTITION_DESCRIPTION") or "")).strip()
            partitions.append({
                "name": r["PARTITION_NAME"],
                "ordinal": r["ordinal"],
                "boundary": desc or "-",
                "is_maxvalue": desc.upper() == "MAXVALUE",
                "rows": int(r["rows_count"] or 0),
                "size_gb": round(b / 1024 / 1024 / 1024, 3),
                "data_mb": round(int(r["data_bytes"] or 0) / 1024 / 1024, 2),
                "index_mb": round(int(r["index_bytes"] or 0) / 1024 / 1024, 2),
                "pct": round(b / total_bytes * 100, 1),
                "create_time": str(r.get("CREATE_TIME") or ""),
                "update_time": str(r.get("UPDATE_TIME") or ""),
            })

        return {
            "schema": db, "table": table, "is_partitioned": True,
            "partition_method": str(rows[0].get("PARTITION_METHOD") or ""),
            "partition_expression": str(rows[0].get("PARTITION_EXPRESSION") or ""),
            "partition_count": len(partitions),
            "total_gb": round(total_bytes / 1024 / 1024 / 1024, 2),
            "partitions": partitions,
            "analysis": _analyze_partitions(partitions),
        }


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
            if "BROADCAST" in create_sql_upper:
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

    def get_slow_queries_from_digest(self, limit: int = 50, time_start: str = None, time_end: str = None) -> list[dict]:
        """从 performance_schema 获取TopN慢SQL摘要，支持时间范围过滤"""
        sql = """
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
        """
        params = []
        if time_start:
            sql += " AND LAST_SEEN >= %s"
            params.append(time_start)
        if time_end:
            sql += " AND FIRST_SEEN <= %s"
            params.append(time_end)
        sql += " ORDER BY SUM_TIMER_WAIT DESC LIMIT %s"
        params.append(limit)
        return self._execute(sql, tuple(params))

    # NOTE: get_slow_queries_from_slow_log() 已移除。
    # TDSQL分布式实例中，SET实例的mysql.slow_log表不记录数据。
    # 请使用 get_slow_queries_from_digest() 获取慢SQL统计。

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

    def get_shard_key(self, db: str, table: str) -> str:
        """获取表的 TDSQL 分片键（SHOW CREATE TABLE 解析），无则返回空字符串。"""
        try:
            rows = self._execute(f"SHOW CREATE TABLE `{db}`.`{table}`")
            if rows:
                return parse_shard_key_from_ddl(rows[0].get("Create Table", "") or "")
        except Exception:
            pass
        return ""

    def check_large_tables(self, database: str = None, threshold_gb: float = 1.0) -> list[dict]:
        """检查大表（双源取大 GREATEST(PARTITIONS聚合, TABLES值)，兼容 TDSQL 分区表壳值）

        与 TDSQLConnectionPool.check_large_tables 同口径，详见 build_large_tables_query。
        对每张大表补一次分片键。
        """
        sql, params = build_large_tables_query(threshold_gb, database)
        rows = self._execute(sql, params)
        for r in rows:
            r["shard_key"] = self.get_shard_key(r.get("schema_name", ""), r.get("table_name", ""))
        return rows
