"""
TDSQL SQL审核工具 - 配置管理 (V2.0)

配置优先级: 环境变量 > 配置文件 > 默认值。

V2.0 变更:
- 新增认证/授权、数据脱敏、数据保留、扫描限流、可观测性配置
- 安全相关配置(认证开关等)采用动态读取，支持运行期/测试期覆盖
- 移除未使用的 aiosqlite DATABASE_URL，SQLite 路径统一由 services/database.py 管理
"""
import json
import os
from pathlib import Path
from typing import Any, Optional

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent

# 数据目录（SQLite 数据库文件由 services/database.py 统一管理: data/tdsql_check.db）
DATABASE_DIR = BASE_DIR / "data"
DATABASE_DIR.mkdir(exist_ok=True)

# FastAPI 配置
APP_TITLE = "TDSQL SQL审核平台"
APP_VERSION = "1.0.4.2"
APP_DESCRIPTION = "银行级SQL质量管控与慢SQL分析平台（V2.0 - 认证授权/多实例连接/规则集/数据治理）"

# SQL 解析配置
SQL_DIALECT = "mysql"  # TDSQL 基于 MySQL

# 审核规则默认开关
DEFAULT_RULE_ENABLED = True


def _env_bool(name: str, default: str = "false") -> bool:
    """动态读取布尔环境变量（每次调用实时读取，支持测试期覆盖）"""
    return os.getenv(name, default).strip().lower() in ("true", "1", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


# ══════════════════════════════════════════════════════════════════
# V3.0: 从system_config表读取配置（优先DB > 环境变量 > 默认值）
# ══════════════════════════════════════════════════════════════════

def _get_db_config(key: str, default: str = "") -> str:
    """从system_config表读取配置值，失败时返回default"""
    try:
        from backend.services.database import _get_connection, ensure_db
        ensure_db()
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT config_value FROM system_config WHERE config_key = ?", (key,)
            ).fetchone()
            if row:
                return row["config_value"]
        finally:
            conn.close()
    except Exception:
        pass
    return default


# ══════════════════════════════════════════════════════════════════
# 认证与授权配置（V2.0，动态读取）
# ══════════════════════════════════════════════════════════════════

def auth_enabled() -> bool:
    """认证开关。优先读DB system_config，回退环境变量。"""
    db_val = _get_db_config("auth_enabled")
    if db_val:
        return db_val.lower() == "true"
    return _env_bool("AUTH_ENABLED", "true")


def auth_token_ttl_hours() -> int:
    """访问令牌有效期（小时），默认8小时"""
    return _env_int("AUTH_TOKEN_TTL_HOURS", 8)


def auth_max_login_failures() -> int:
    """连续登录失败锁定阈值"""
    return _env_int("AUTH_MAX_LOGIN_FAILURES", 5)


def auth_lock_minutes() -> int:
    """账户锁定时长（分钟）"""
    return _env_int("AUTH_LOCK_MINUTES", 15)


def admin_initial_password() -> str:
    """首个管理员账户初始口令（未设置则随机生成并打印一次）"""
    return os.getenv("ADMIN_INITIAL_PASSWORD", "")


def docs_public() -> bool:
    """API文档(/docs, /openapi.json)是否免认证开放。生产建议 false。"""
    return _env_bool("DOCS_PUBLIC", "true")


def cors_allow_origins() -> list[str]:
    """CORS 允许来源，逗号分隔。默认为空（同源部署，不下发跨域头）。"""
    raw = os.getenv("CORS_ALLOW_ORIGINS", "").strip()
    if not raw:
        return []
    return [o.strip() for o in raw.split(",") if o.strip()]


# ══════════════════════════════════════════════════════════════════
# 数据安全配置（V2.0，动态读取）
# ══════════════════════════════════════════════════════════════════

def data_masking_enabled() -> bool:
    """慢SQL入库脱敏开关。优先读DB system_config。"""
    db_val = _get_db_config("data_masking_enabled")
    if db_val:
        return db_val.lower() == "true"
    return _env_bool("DATA_MASKING_ENABLED", "true")


def gitlab_webhook_allow_insecure() -> bool:
    """未配置 GITLAB_WEBHOOK_SECRET 时是否仍接受 webhook。生产必须为 false（默认）。"""
    return _env_bool("GITLAB_WEBHOOK_ALLOW_INSECURE", "false")


# ══════════════════════════════════════════════════════════════════
# 扫描限流配置（V2.0，动态读取）
# ══════════════════════════════════════════════════════════════════

def max_concurrent_scans_per_connection() -> int:
    """单个目标库的最大并发扫描数（保护目标库）"""
    return _env_int("MAX_CONCURRENT_SCANS_PER_CONNECTION", 2)


def max_concurrent_scans_global() -> int:
    """全局最大并发扫描数（保护本服务）"""
    return _env_int("MAX_CONCURRENT_SCANS_GLOBAL", 8)


def connection_pool_max_instances() -> int:
    """连接注册表最大并存实例数（LRU淘汰）"""
    return _env_int("CONNECTION_POOL_MAX_INSTANCES", 256)


def connection_pool_idle_seconds() -> int:
    """连接池空闲回收时间（秒）"""
    return _env_int("CONNECTION_POOL_IDLE_SECONDS", 1800)


# ══════════════════════════════════════════════════════════════════
# 可观测性配置（V2.0）
# ══════════════════════════════════════════════════════════════════

def metrics_enabled() -> bool:
    """Prometheus /metrics 端点开关。优先读DB system_config。"""
    db_val = _get_db_config("metrics_enabled")
    if db_val:
        return db_val.lower() == "true"
    return _env_bool("METRICS_ENABLED", "true")


# ══════════════════════════════════════════════════════════════════
# TDSQL 连接配置（V1.0 兼容：单实例环境变量方式）
# ══════════════════════════════════════════════════════════════════

TDSQL_CONFIG: dict[str, Any] = {
    "host": os.getenv("TDSQL_HOST", ""),
    "port": int(os.getenv("TDSQL_PORT", "3306")),
    "user": os.getenv("TDSQL_USER", ""),
    "password": os.getenv("TDSQL_PASSWORD", ""),
    "database": os.getenv("TDSQL_DATABASE", ""),
    "charset": os.getenv("TDSQL_CHARSET", "utf8mb4"),
    "connect_timeout": int(os.getenv("TDSQL_CONNECT_TIMEOUT", "5")),
    "read_timeout": int(os.getenv("TDSQL_READ_TIMEOUT", "10")),
}

# ── 定时任务配置 ──
SCHEDULER_ENABLED = os.getenv("SCHEDULER_ENABLED", "false").lower() == "true"
SCHEDULER_CRON_HOUR = int(os.getenv("SCHEDULER_CRON_HOUR", "2"))
SCHEDULER_CRON_MINUTE = int(os.getenv("SCHEDULER_CRON_MINUTE", "0"))
SCHEDULER_SLOW_QUERY_LIMIT = int(os.getenv("SCHEDULER_SLOW_QUERY_LIMIT", "100"))
SCHEDULER_SLOW_QUERY_MIN_TIME = float(os.getenv("SCHEDULER_SLOW_QUERY_MIN_TIME", "1.0"))


def retention_cron_hour() -> int:
    """数据保留清理任务执行小时（默认凌晨3点）"""
    return _env_int("RETENTION_CRON_HOUR", 3)


# ── 审核报告配置 ──
REPORT_OUTPUT_DIR = Path(os.getenv("REPORT_OUTPUT_DIR", str(DATABASE_DIR / "reports")))
REPORT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def load_tdsql_config_from_file(config_path: Optional[str] = None) -> dict[str, Any]:
    """
    从JSON配置文件加载TDSQL连接参数。

    优先级: 环境变量 > 配置文件 > 默认值

    Args:
        config_path: 配置文件路径，默认为 BASE_DIR / config / tdsql.json

    Returns:
        合并后的TDSQL连接配置字典
    """
    default_config = TDSQL_CONFIG.copy()
    file_path = Path(config_path) if config_path else BASE_DIR / "config" / "tdsql.json"

    if file_path.exists():
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                file_config = json.load(f)
            # 仅使用文件中存在且环境变量未设置的值
            for key, value in file_config.items():
                env_key = f"TDSQL_{key.upper()}"
                if not os.getenv(env_key) and key in default_config:
                    default_config[key] = value
        except (json.JSONDecodeError, IOError):
            pass

    return default_config


def is_tdsql_configured() -> bool:
    """检查TDSQL连接是否已配置（host 和 user 非空）"""
    config = TDSQL_CONFIG
    return bool(config.get("host") and config.get("user"))

# TDSQL 关键字列表 (R002)
TDSQL_RESERVED_KEYWORDS = {
    "add", "all", "alter", "analyze", "and", "as", "asc", "asensitive",
    "before", "between", "bigint", "binary", "blob", "both", "by",
    "call", "cascade", "case", "change", "char", "character", "check",
    "collate", "column", "condition", "constraint", "continue", "convert",
    "create", "cross", "current_date", "current_time", "current_timestamp",
    "current_user", "cursor",
    "database", "databases", "day_hour", "day_microsecond", "day_minute",
    "day_second", "dec", "decimal", "declare", "default", "delayed",
    "delete", "desc", "describe", "deterministic", "distinct", "div",
    "double", "drop", "dual",
    "each", "else", "elseif", "enclosed", "escaped", "exists", "exit",
    "explain",
    "false", "fetch", "float", "float4", "float8", "for", "force",
    "foreign", "from", "fulltext",
    "generated", "get", "grant", "group",
    "having", "high_priority", "hour_microsecond", "hour_minute",
    "hour_second",
    "if", "ignore", "in", "index", "infile", "inner", "inout",
    "insensitive", "insert", "int", "int1", "int2", "int3", "int4",
    "int8", "integer", "interval", "into", "io_after_gtids",
    "io_before_gtids", "is", "iterate",
    "join",
    "key", "keys", "kill",
    "leading", "leave", "left", "like", "limit", "linear", "lines",
    "load", "localtime", "localtimestamp", "lock", "long", "longblob",
    "longtext", "loop", "low_priority",
    "master_bind", "master_ssl_verify_server_cert", "match", "maxvalue",
    "mediumblob", "mediumint", "mediumtext", "middleint", "minute_microsecond",
    "minute_second", "mod", "modifies",
    "natural", "not", "no_write_to_binlog", "null", "numeric",
    "on", "optimize", "optimizer_costs", "option", "optionally", "or",
    "order", "out", "outer", "outfile",
    "partition", "precision", "primary", "procedure", "purge",
    "range", "read", "reads", "read_write", "real", "references",
    "regexp", "release", "rename", "repeat", "replace", "require",
    "resignal", "restrict", "return", "revoke", "right", "rlike",
    "schema", "schemas", "second_microsecond", "select", "sensitive",
    "separator", "set", "show", "signal", "smallint", "spatial",
    "specific", "sql", "sqlexception", "sqlstate", "sqlwarning",
    "sql_big_result", "sql_calc_found_rows", "sql_small_result", "ssl",
    "starting", "stored", "straight_join",
    "table", "terminated", "then", "tinyblob", "tinyint", "tinytext",
    "to", "trailing", "trigger", "true",
    "undo", "union", "unique", "unlock", "unsigned", "update", "usage",
    "use", "using", "utc_date", "utc_time", "utc_timestamp",
    "values", "varbinary", "varchar", "varcharacter", "varying",
    "virtual",
    "when", "where", "while", "with", "write",
    "xor",
    "year_month",
    "zerofill",
}
