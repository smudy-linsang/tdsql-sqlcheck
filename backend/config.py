"""
TDSQL SQL审核工具 - 配置管理

支持从环境变量和配置文件读取TDSQL连接参数。
"""
import json
import os
from pathlib import Path
from typing import Any, Optional

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent

# 数据库配置
DATABASE_DIR = BASE_DIR / "data"
DATABASE_DIR.mkdir(exist_ok=True)
DATABASE_URL = f"sqlite+aiosqlite:///{DATABASE_DIR}/tdsql_sqlcheck.db"

# FastAPI 配置
APP_TITLE = "TDSQL SQL审核工具"
APP_VERSION = "1.0.0"
APP_DESCRIPTION = "覆盖开发、测试、生产全生命周期的SQL质量管控与慢SQL分析工具"

# SQL 解析配置
SQL_DIALECT = "mysql"  # TDSQL 基于 MySQL

# 审核规则默认开关
DEFAULT_RULE_ENABLED = True

# ── TDSQL 连接配置（从环境变量读取） ──
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
