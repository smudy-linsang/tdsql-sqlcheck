"""
TDSQL SQL审核工具 - TDSQL管理API

提供TDSQL实例连接、连接测试、元数据查询、慢SQL抓取、字符集检查等功能。
"""
import time
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import threading

from backend.config import TDSQL_CONFIG, is_tdsql_configured, load_tdsql_config_from_file

router = APIRouter(prefix="/api/v1/tdsql", tags=["TDSQL管理"])

# 全局连接器实例（使用锁保护，确保线程安全）
_connector = None
_connector_lock = threading.Lock()


class TDSQLConnectRequest(BaseModel):
    """TDSQL连接请求"""
    host: str = Field(..., description="TDSQL实例地址")
    port: int = Field(3306, description="端口")
    user: str = Field(..., description="用户名")
    password: str = Field(..., description="密码")
    database: str = Field("", description="默认数据库")


class SlowQueryFetchRequest(BaseModel):
    """慢SQL抓取请求"""
    source: str = Field("digest", description="数据源: digest/slow_log/processlist")
    limit: int = Field(50, description="抓取条数")
    min_time: float = Field(1.0, description="最小耗时阈值(秒)")


def _get_connector():
    """获取连接器实例（线程安全）"""
    global _connector
    with _connector_lock:
        if _connector is None:
            raise HTTPException(status_code=400, detail="未连接TDSQL实例，请先调用 /api/v1/tdsql/connect")
        return _connector


@router.post("/connect", summary="连接TDSQL实例")
async def connect_tdsql(request: TDSQLConnectRequest):
    """
    连接到TDSQL MySQL实例。

    连接成功后，后续API调用将使用此连接。
    """
    global _connector
    try:
        from backend.services.tdsql_connector import TDSQLConnector, TDSQLConnectionConfig
        config = TDSQLConnectionConfig(
            host=request.host,
            port=request.port,
            user=request.user,
            password=request.password,
            database=request.database,
        )
        new_connector = TDSQLConnector(config)
        new_connector.connect()
        with _connector_lock:
            # 断开旧连接
            if _connector:
                try:
                    _connector.disconnect()
                except Exception:
                    pass
            _connector = new_connector
        return {
            "message": "连接成功",
            "host": request.host,
            "port": request.port,
            "database": request.database,
        }
    except ImportError:
        raise HTTPException(status_code=500, detail="pymysql未安装，请执行: pip install pymysql")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"连接失败: {str(e)}")


@router.post("/connect-from-config", summary="使用配置文件连接TDSQL")
async def connect_from_config(config_path: Optional[str] = None):
    """
    使用环境变量或配置文件中的参数连接TDSQL。

    优先级: 环境变量 > 配置文件 > 默认值
    配置文件路径: 项目根目录/config/tdsql.json
    """
    global _connector
    try:
        from backend.services.tdsql_connector import TDSQLConnector, TDSQLConnectionConfig
        config_data = load_tdsql_config_from_file(config_path)

        if not config_data.get("host") or not config_data.get("user"):
            raise HTTPException(
                status_code=400,
                detail="TDSQL连接参数未配置，请设置环境变量(TDSQL_HOST/TDSQL_USER/TDSQL_PASSWORD)或创建config/tdsql.json",
            )

        conn_config = TDSQLConnectionConfig(
            host=config_data["host"],
            port=config_data.get("port", 3306),
            user=config_data["user"],
            password=config_data.get("password", ""),
            database=config_data.get("database", ""),
            charset=config_data.get("charset", "utf8mb4"),
            connect_timeout=config_data.get("connect_timeout", 5),
            read_timeout=config_data.get("read_timeout", 10),
        )
        new_connector = TDSQLConnector(conn_config)
        new_connector.connect()
        with _connector_lock:
            if _connector:
                try:
                    _connector.disconnect()
                except Exception:
                    pass
            _connector = new_connector
        return {
            "message": "连接成功（配置文件模式）",
            "host": conn_config.host,
            "port": conn_config.port,
            "database": conn_config.database,
            "configured": is_tdsql_configured(),
        }
    except ImportError:
        raise HTTPException(status_code=500, detail="pymysql未安装，请执行: pip install pymysql")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"连接失败: {str(e)}")


@router.get("/test-connection", summary="测试TDSQL连接")
async def test_connection(host: Optional[str] = None, port: int = 3306,
                          user: Optional[str] = None, password: Optional[str] = None,
                          database: Optional[str] = None):
    """
    测试TDSQL连接可用性。

    可通过参数指定连接信息，也可使用环境变量/配置文件中的默认配置。
    返回连接延迟和服务器版本信息。
    """
    try:
        from backend.services.tdsql_connector import TDSQLConnector, TDSQLConnectionConfig

        # 优先使用传入参数，其次使用配置
        if host and user:
            config = TDSQLConnectionConfig(
                host=host, port=port, user=user,
                password=password or "", database=database or "",
            )
        else:
            config_data = TDSQL_CONFIG if TDSQL_CONFIG.get("host") else load_tdsql_config_from_file()
            if not config_data.get("host") or not config_data.get("user"):
                raise HTTPException(
                    status_code=400,
                    detail="请提供连接参数或配置环境变量/配置文件",
                )
            config = TDSQLConnectionConfig(
                host=config_data["host"],
                port=config_data.get("port", 3306),
                user=config_data["user"],
                password=config_data.get("password", ""),
                database=config_data.get("database", ""),
            )

        connector = TDSQLConnector(config)
        start_time = time.time()
        connector.connect()
        latency_ms = round((time.time() - start_time) * 1000, 2)

        # 获取服务器版本
        version_info = connector._execute("SELECT VERSION() as version")
        server_version = version_info[0].get("version", "unknown") if version_info else "unknown"

        # 获取慢查询配置
        try:
            slow_config = connector.get_slow_query_variables()
        except Exception:
            slow_config = {}

        connector.disconnect()

        return {
            "status": "connected",
            "host": config.host,
            "port": config.port,
            "database": config.database,
            "server_version": server_version,
            "latency_ms": latency_ms,
            "slow_query_config": slow_config,
            "pymysql_available": True,
        }
    except ImportError:
        return {
            "status": "error",
            "message": "pymysql未安装，请执行: pip install pymysql",
            "pymysql_available": False,
        }
    except HTTPException:
        raise
    except Exception as e:
        return {
            "status": "error",
            "message": f"连接测试失败: {str(e)}",
            "pymysql_available": True,
        }


@router.post("/disconnect", summary="断开TDSQL连接")
async def disconnect_tdsql():
    """断开TDSQL连接"""
    global _connector
    with _connector_lock:
        if _connector:
            _connector.disconnect()
            _connector = None
    return {"message": "已断开连接"}


@router.get("/status", summary="检查连接状态")
async def connection_status():
    """检查TDSQL连接状态"""
    global _connector
    with _connector_lock:
        if _connector and _connector.is_connected():
            return {"connected": True, "host": _connector.config.host}
    return {"connected": False}


@router.get("/tables", summary="获取表列表")
async def get_tables(database: Optional[str] = None):
    """获取数据库中的所有表"""
    conn = _get_connector()
    try:
        tables = conn.get_tables(database)
        return {"tables": [dict(t) for t in tables]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tables/{table_name}/metadata", summary="获取表元数据")
async def get_table_metadata(table_name: str, database: Optional[str] = None):
    """
    获取表的完整元数据，包括分片键、索引、字段等信息。
    """
    conn = _get_connector()
    try:
        meta = conn.get_table_metadata(table_name, database)
        return {
            "table_name": meta.table_name,
            "engine": meta.engine,
            "charset": meta.charset,
            "table_collation": meta.table_collation,
            "table_comment": meta.table_comment,
            "table_rows": meta.table_rows,
            "data_mb": round(meta.data_length / 1024 / 1024, 2),
            "index_mb": round(meta.index_length / 1024 / 1024, 2),
            "shard_key": meta.shard_key,
            "is_shard_table": meta.is_shard_table,
            "is_broadcast_table": meta.is_broadcast_table,
            "is_single_table": meta.is_single_table,
            "columns": meta.columns,
            "indexes": meta.indexes,
            "create_sql": meta.create_sql,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/slow-queries/fetch", summary="从TDSQL抓取慢SQL")
async def fetch_slow_queries(request: SlowQueryFetchRequest):
    """
    从TDSQL实例抓取慢SQL并自动分析。

    数据源:
    - digest: 从 performance_schema.events_statements_summary_by_digest 获取TopN慢SQL
    - slow_log: 从 mysql.slow_log 表获取
    - processlist: 从 processlist 获取当前正在执行的慢SQL
    """
    conn = _get_connector()
    try:
        if request.source == "digest":
            raw_queries = conn.get_slow_queries_from_digest(limit=request.limit)
        elif request.source == "slow_log":
            raw_queries = conn.get_slow_queries_from_slow_log(
                limit=request.limit, min_time=request.min_time
            )
        elif request.source == "processlist":
            raw_queries = conn.get_slow_queries_from_processlist(
                min_time=int(request.min_time)
            )
        else:
            raise HTTPException(status_code=400, detail=f"不支持的数据源: {request.source}")

        # 转换并分析
        from backend.engine.slow_analyzer import SlowQueryRecord, SlowSQLAnalyzer
        from backend.services.slow_query_service import SlowQueryService

        analyzer = SlowSQLAnalyzer()
        service = SlowQueryService()
        results = []

        for raw in raw_queries:
            sql_text = raw.get("sql_text") or raw.get("info") or raw.get("DIGEST_TEXT", "")
            if not sql_text:
                continue

            record = SlowQueryRecord(
                fingerprint=raw.get("DIGEST_TEXT", sql_text),
                sql_text=sql_text,
                db_name=raw.get("SCHEMA_NAME") or raw.get("db", ""),
                exec_count=raw.get("exec_count") or raw.get("COUNT_STAR", 0) or 0,
                total_time_ms=float(raw.get("total_seconds", 0) or 0) * 1000,
                avg_time_ms=float(raw.get("avg_seconds", 0) or 0) * 1000,
                max_time_ms=float(raw.get("max_seconds", 0) or 0) * 1000,
                rows_examined=raw.get("rows_examined") or raw.get("SUM_ROWS_EXAMINED", 0) or 0,
                rows_sent=raw.get("rows_sent") or raw.get("SUM_ROWS_SENT", 0) or 0,
            )

            # 保存并分析
            result = service.add_slow_query(record)
            results.append(result)

        return {
            "source": request.source,
            "fetched": len(results),
            "results": results,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/check/charset", summary="字符集一致性检查")
async def check_charset(database: Optional[str] = None):
    """
    检查库内字符集和排序规则一致性。

    基于慢SQL优化方案4.6.6的诊断SQL，检查：
    1. 库级别默认字符集
    2. 表级别字符集分布
    3. 字段级别字符集与表不一致
    4. 跨表同名字段字符集不一致
    """
    conn = _get_connector()
    try:
        result = conn.check_charset_consistency(database)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/check/large-tables", summary="大表检查")
async def check_large_tables(
    database: Optional[str] = None,
    threshold_gb: float = 1.0,
):
    """
    检查大表（参考大表治理规范）。

    默认阈值1GB，返回L1/L2/L3分级。
    """
    conn = _get_connector()
    try:
        tables = conn.check_large_tables(database, threshold_gb)
        return {
            "database": database or conn.config.database,
            "threshold_gb": threshold_gb,
            "total": len(tables),
            "tables": [dict(t) for t in tables],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/slow-query-config", summary="获取慢查询配置")
async def get_slow_query_config():
    """获取TDSQL实例的慢查询相关配置"""
    conn = _get_connector()
    try:
        config = conn.get_slow_query_variables()
        return {"variables": config}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/audit/with-metadata", summary="使用元数据增强审核")
async def audit_with_metadata(request: dict):
    """
    使用TDSQL表元数据增强SQL审核精度。

    自动获取表的分片键、索引等信息，用于增强R020-R022规则。
    """
    sql = request.get("sql", "")
    if not sql:
        raise HTTPException(status_code=400, detail="sql不能为空")

    conn = _get_connector()

    try:
        from backend.engine.checker import RuleChecker
        from backend.engine.parser import SQLParser

        # 解析SQL获取涉及的表
        parser = SQLParser()
        parsed = parser.parse(sql)

        # 获取每个表的元数据
        table_metadata = {}
        for table in parsed.tables:
            try:
                meta = conn.get_table_metadata(table)
                table_metadata[table] = {
                    "shard_key": meta.shard_key,
                    "is_shard_table": meta.is_shard_table,
                    "is_broadcast_table": meta.is_broadcast_table,
                    "indexes": meta.indexes,
                }
            except Exception:
                pass

        # 执行审核（传入元数据增强规则检查）
        checker = RuleChecker()

        # 将元数据注入到SQL中作为注释，供分布式规则识别
        enhanced_sql = sql
        for table, meta_info in table_metadata.items():
            if meta_info.get("shard_key"):
                shard_key = meta_info["shard_key"]
                enhanced_sql += f" /* shardkey:{shard_key} */"
                break

        result = checker.audit_sql(enhanced_sql)

        return {
            "sql": sql,
            "table_metadata": table_metadata,
            "audit_result": {
                "passed": result.passed,
                "sql_type": result.sql_type,
                "violations": [
                    {
                        "rule_id": v.rule_id,
                        "severity": v.severity,
                        "message": v.message,
                        "suggestion": v.suggestion,
                    }
                    for v in result.violations
                ],
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── 定时任务管理 ─────────────────────────────────────────


@router.get("/scheduler/status", summary="查看定时任务状态")
async def get_scheduler_status():
    """查看定时慢日志拉取任务的运行状态和调度配置"""
    from backend.services.scheduler import get_scheduler_status
    return get_scheduler_status()


@router.post("/scheduler/trigger", summary="手动触发慢日志拉取")
async def trigger_slow_query_fetch():
    """手动触发一次慢日志拉取任务，立即从TDSQL拉取并分析"""
    from backend.services.scheduler import manual_fetch_slow_queries
    return manual_fetch_slow_queries()
