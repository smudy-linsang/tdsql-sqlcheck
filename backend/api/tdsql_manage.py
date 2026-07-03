"""
TDSQL SQL审核工具 - TDSQL管理API

提供TDSQL实例连接、连接测试、元数据查询、慢SQL抓取、字符集检查等功能。
"""
import json
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import threading

from backend.config import TDSQL_CONFIG, is_tdsql_configured, load_tdsql_config_from_file, BASE_DIR

router = APIRouter(prefix="/api/v1/tdsql", tags=["TDSQL管理"])

# 全局连接池实例（使用锁保护，确保线程安全）
_pool = None
_pool_lock = threading.Lock()

# 连接配置存储文件
CONNECTIONS_CONFIG_FILE = BASE_DIR / "config" / "tdsql_connections.json"


class TDSQLConnectRequest(BaseModel):
    """TDSQL连接请求"""
    host: str = Field(..., description="TDSQL实例地址")
    port: int = Field(3306, description="端口")
    user: str = Field(..., description="用户名")
    password: str = Field(..., description="密码")
    database: str = Field("", description="默认数据库")
    name: str = Field("", description="连接名称（可选，用于多连接管理）")


class SlowQueryFetchRequest(BaseModel):
    """慢SQL抓取请求"""
    source: str = Field("digest", description="数据源: digest(性能摘要,推荐)/processlist(实时进程轮询)")
    limit: int = Field(50, description="抓取条数上限")
    min_time: float = Field(0.1, description="最小耗时阈值(秒)，digest模式按平均耗时过滤，processlist按当前执行时间过滤")
    task_name: str = Field("", description="自定义扫描任务名称")
    time_window_start: str = Field("", description="时间窗口开始 (YYYY-MM-DD HH:MM:SS)")
    time_window_end: str = Field("", description="时间窗口结束 (YYYY-MM-DD HH:MM:SS)")
    poll_duration: float = Field(10.0, description="processlist轮询持续时间(秒)，仅processlist模式有效，默认10秒")
    poll_interval: float = Field(1.0, description="processlist轮询间隔(秒)，仅processlist模式有效，默认1秒")


def _get_pool():
    """获取连接器实例（线程安全）"""
    global _pool
    with _pool_lock:
        if _pool is None:
            raise HTTPException(status_code=400, detail="未连接TDSQL实例，请先调用 /api/v1/tdsql/connect")
        return _pool


@router.post("/connect", summary="连接TDSQL实例")
async def connect_tdsql(request: TDSQLConnectRequest):
    """
    连接到TDSQL MySQL实例。

    连接成功后，后续API调用将使用此连接。
    """
    global _pool
    try:
        from backend.services.tdsql_connector import TDSQLConnectionPool, TDSQLConnectionConfig
        config = TDSQLConnectionConfig(
            host=request.host,
            port=request.port,
            user=request.user,
            password=request.password,
            database=request.database,
        )
        new_pool = TDSQLConnectionPool(config)
        # 验证连接可用性（立即创建连接，失败则抛异常）
        try:
            with new_pool.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    cursor.fetchone()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"连接失败: {str(e)}")
        with _pool_lock:
            # 关闭旧连接池
            if _pool:
                try:
                    _pool.close_all()
                except Exception:
                    pass
            _pool = new_pool
        return {
            "message": "连接成功",
            "host": request.host,
            "port": request.port,
            "database": request.database,
            "user": request.user,
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
    global _pool
    try:
        from backend.services.tdsql_connector import TDSQLConnectionPool, TDSQLConnectionConfig
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
        new_pool = TDSQLConnectionPool(conn_config)
        # 验证连接可用性
        try:
            with new_pool.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    cursor.fetchone()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"连接失败: {str(e)}")
        with _pool_lock:
            if _pool:
                try:
                    _pool.close_all()
                except Exception:
                    pass
            _pool = new_pool
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
        from backend.services.tdsql_connector import TDSQLConnectionPool, TDSQLConnectionConfig

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

        pool = TDSQLConnectionPool(config)
        start_time = time.time()
        with pool.get_connection() as conn:
            latency_ms = round((time.time() - start_time) * 1000, 2)
            # 获取服务器版本
            with conn.cursor() as cursor:
                cursor.execute("SELECT VERSION() as version")
                version_info = cursor.fetchall()
            server_version = version_info[0].get("version", "unknown") if version_info else "unknown"
            # 获取慢查询配置
            try:
                with conn.cursor() as cursor:
                    cursor.execute("SHOW VARIABLES LIKE 'slow_query%'")
                    slow_rows = cursor.fetchall()
                    slow_config = {row.get("Variable_name", ""): row.get("Value", "") for row in slow_rows}
            except Exception:
                slow_config = {}

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
    global _pool
    with _pool_lock:
        if _pool:
            _pool.close_all()
            _pool = None
    return {"message": "已断开连接"}


@router.get("/status", summary="检查连接状态")
async def connection_status():
    """检查TDSQL连接状态"""
    global _pool
    with _pool_lock:
        if _pool and _pool.is_connected():
            return {
                "connected": True,
                "host": _pool.config.host,
                "port": _pool.config.port,
                "database": _pool.config.database,
                "user": _pool.config.user,
            }
    return {"connected": False}


@router.get("/tables", summary="获取表列表")
async def get_tables(database: Optional[str] = None):
    """获取数据库中的所有表"""
    conn = _get_pool()
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
    conn = _get_pool()
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


@router.get("/sets", summary="发现TDSQL分布式实例的所有SET")
async def discover_sets():
    """
    通过 /*proxy*/show status 发现 TDSQL 分布式实例的所有 SET（分片）。

    对于非分布式实例（集中式），返回空列表。
    """
    conn = _get_pool()
    try:
        sets = conn.discover_sets()
        return {"sets": sets, "total": len(sets)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/slow-queries/fetch", summary="从TDSQL抓取慢SQL")
def fetch_slow_queries(request: SlowQueryFetchRequest):
    """
    从TDSQL实例抓取慢SQL并自动分析。

    数据源（基于TDSQL分布式架构设计）:
    - digest (推荐): 从 Proxy 层 performance_schema.events_statements_summary_by_digest 获取
      SQL执行统计摘要。这是TDSQL分布式实例唯一可靠的慢SQL数据源，Proxy自动聚合
      所有SET的执行数据。
    - processlist: 从 information_schema.processlist 抓取当前正在执行的SQL快照。
      仅能捕获扫描瞬间正在执行且耗时超过阈值的SQL，适合发现长时间运行的查询。

    注意: TDSQL分布式实例的mysql.slow_log表不记录数据（慢日志由Proxy层统一管理），
    因此不支持slow_log数据源。所有查询直接通过Proxy执行，无需SET路由。
    """
    conn = _get_pool()

    # 校验数据源
    valid_sources = {"digest", "processlist"}
    if request.source not in valid_sources:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的数据源: {request.source}。"
                   f"TDSQL分布式实例仅支持: digest(性能摘要分析,推荐)、processlist(实时进程快照)。"
                   f"注意: mysql.slow_log在TDSQL分布式架构下不可用（数据由Proxy层管理，不写入SET实例表）。"
        )

    # digest模式校验时间窗口必填（作为扫描任务元数据记录，不作为SQL查询过滤条件）
    if request.source == "digest":
        if not request.time_window_start or not request.time_window_end:
            raise HTTPException(status_code=422, detail="时间窗口开始和结束时间为必填项，请指定扫描时间范围（记录为任务元数据）")

    try:
        # 获取连接信息用于创建扫描任务
        db_name = conn.config.database or ''
        conn_id = ''
        conn_name = f"{conn.config.host}:{conn.config.port}"

        from backend.engine.slow_analyzer import SlowQueryRecord, SlowSQLAnalyzer
        from backend.services.slow_query_service import SlowQueryService

        service = SlowQueryService()

        # 创建扫描任务（任务名自动包含时间段）
        source_labels = {"digest": "性能摘要分析", "processlist": "实时进程快照"}
        # 自动拼接时间段到任务名称
        time_range_str = ""
        if request.time_window_start and request.time_window_end:
            # 提取简短的时间表示: "07-01 00:00 ~ 23:59"
            start_short = request.time_window_start[5:16]  # MM-DD HH:MM
            end_short = request.time_window_end[5:16]
            time_range_str = f" [{start_short} ~ {end_short}]"
        task_name = request.task_name or f"{source_labels.get(request.source, request.source)} - {conn_name}"
        task_name = task_name + time_range_str
        task_id = service.create_scan_task(
            task_name=task_name,
            source=request.source,
            db_name=db_name,
            connection_id=conn_id,
            connection_name=conn_name,
            time_window_start=request.time_window_start,
            time_window_end=request.time_window_end,
        )

        # 执行扫描（直接通过Proxy查询，不做SET路由）
        results = []
        errors = []

        try:
            if request.source == "digest":
                # 注意: TDSQL Proxy的performance_schema不支持FIRST_SEEN/LAST_SEEN时间过滤，
                # 时间窗口仅作为扫描任务元数据记录，不传入SQL查询。
                raw_queries = conn.get_slow_queries_from_digest(
                    limit=request.limit,
                    min_time=request.min_time,
                )
            elif request.source == "processlist":
                # 使用多次轮询模式，提高捕获短时慢SQL的概率
                poll_duration = max(1.0, min(request.poll_duration, 60.0))  # 限制1-60秒
                poll_interval = max(0.5, min(request.poll_interval, 5.0))    # 限制0.5-5秒
                raw_queries = conn.poll_processlist(
                    duration_seconds=poll_duration,
                    interval=poll_interval,
                    min_time=request.min_time,
                )
        except Exception as e:
            errors.append({"source": request.source, "error": str(e)})
            raw_queries = []

        # 转换并分析
        for raw in raw_queries:
            sql_text = raw.get("DIGEST_TEXT") or raw.get("info") or raw.get("sql_text", "")
            if not sql_text:
                continue
            if isinstance(sql_text, bytes):
                sql_text = sql_text.decode("utf-8", errors="replace")
            db_val = raw.get("SCHEMA_NAME") or raw.get("db", "") or db_name
            if isinstance(db_val, bytes):
                db_val = db_val.decode("utf-8", errors="replace")

            # 处理时间字段
            query_time_val = raw.get("query_time") or raw.get("time")
            if query_time_val is not None:
                if hasattr(query_time_val, "total_seconds"):
                    qt_sec = query_time_val.total_seconds()
                else:
                    qt_sec = float(query_time_val)
                total_ms = avg_ms = max_ms = qt_sec * 1000
            else:
                total_ms = float(raw.get("total_seconds", 0) or 0) * 1000
                avg_ms = float(raw.get("avg_seconds", 0) or 0) * 1000
                max_ms = float(raw.get("max_seconds", 0) or 0) * 1000

            lock_time_ms = float(raw.get("lock_time_seconds", 0) or 0) * 1000

            # 无索引使用标记（从performance_schema获取）
            no_index_count = raw.get("no_index_count", 0) or 0

            # 执行者信息（processlist模式有具体用户/IP，digest模式为聚合数据无法区分）
            client_user = raw.get("user", "")
            client_host = raw.get("host", "")
            if isinstance(client_user, bytes):
                client_user = client_user.decode("utf-8", errors="replace")
            if isinstance(client_host, bytes):
                client_host = client_host.decode("utf-8", errors="replace")

            # 执行时间信息
            # digest模式: FIRST_SEEN/LAST_SEEN 是performance_schema记录的真实执行时间范围
            # processlist模式: 使用当前扫描时间
            first_seen_val = ""
            last_seen_val = ""
            if raw.get("FIRST_SEEN"):
                first_seen_val = str(raw["FIRST_SEEN"])
            if raw.get("LAST_SEEN"):
                last_seen_val = str(raw["LAST_SEEN"])

            record = SlowQueryRecord(
                fingerprint=raw.get("DIGEST_TEXT", sql_text),
                sql_text=sql_text,
                db_name=db_val,
                set_id="",  # Proxy层聚合，不区分SET
                client_user=client_user,
                client_host=client_host,
                exec_count=raw.get("exec_count") or raw.get("COUNT_STAR", 0) or 0,
                total_time_ms=total_ms,
                avg_time_ms=avg_ms,
                max_time_ms=max_ms,
                lock_time_ms=lock_time_ms,
                rows_examined=raw.get("rows_examined") or raw.get("SUM_ROWS_EXAMINED", 0) or 0,
                rows_sent=raw.get("rows_sent") or raw.get("SUM_ROWS_SENT", 0) or 0,
                first_seen=first_seen_val,
                last_seen=last_seen_val,
            )

            result = service.add_slow_query(record, scan_task_id=task_id)
            results.append(result)

        # 完成扫描任务
        service.complete_scan_task(task_id, total_fetched=len(results), total_analyzed=len(results))

        return {
            "source": request.source,
            "fetched": len(results),
            "scan_task_id": task_id,
            "errors": errors,
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
    conn = _get_pool()
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
    conn = _get_pool()
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
    conn = _get_pool()
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

    conn = _get_pool()

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

        # 传递真实元数据给审核引擎
        result = checker.audit_sql(sql, table_metadata=table_metadata)

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


# ── 多连接配置管理 ─────────────────────────────────────────


def _load_connections_config() -> dict:
    """加载连接配置列表"""
    if not CONNECTIONS_CONFIG_FILE.exists():
        return {"connections": [], "default": None}
    try:
        with open(CONNECTIONS_CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"connections": [], "default": None}


def _save_connections_config(config_data: dict):
    """保存连接配置列表"""
    CONNECTIONS_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONNECTIONS_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config_data, f, ensure_ascii=False, indent=2)


@router.get("/connections", summary="获取所有连接配置")
async def get_connections():
    """
    获取所有已保存的连接配置列表（密码会被脱敏处理）。
    """
    config_data = _load_connections_config()
    connections = []
    for conn in config_data.get("connections", []):
        # 脱敏处理，不返回明文密码
        safe_conn = {
            "id": conn.get("id"),
            "name": conn.get("name"),
            "host": conn.get("host"),
            "port": conn.get("port"),
            "user": conn.get("user"),
            "database": conn.get("database"),
            "charset": conn.get("charset", "utf8mb4"),
        }
        connections.append(safe_conn)
    return {
        "connections": connections,
        "default": config_data.get("default"),
    }


@router.post("/connections", summary="保存连接配置")
async def save_connection(request: TDSQLConnectRequest):
    """
    保存一个新的连接配置或更新已存在的连接。
    如果未指定name，将自动生成一个唯一名称。
    """
    config_data = _load_connections_config()
    connections = config_data.get("connections", [])
    
    # 生成连接ID
    import uuid
    conn_id = str(uuid.uuid4())[:8]
    
    # 如果未指定名称，使用 host:port 作为名称
    name = request.name or f"{request.host}:{request.port}"
    
    new_conn = {
        "id": conn_id,
        "name": name,
        "host": request.host,
        "port": request.port,
        "user": request.user,
        "password": request.password,  # 加密存储（实际生产环境应加密）
        "database": request.database,
        "charset": "utf8mb4",
    }
    
    # 检查是否已存在同名连接
    existing = False
    for i, conn in enumerate(connections):
        if conn.get("name") == name or (conn.get("host") == request.host and conn.get("port") == request.port):
            connections[i] = new_conn
            existing = True
            break
    
    if not existing:
        connections.append(new_conn)
    
    config_data["connections"] = connections
    _save_connections_config(config_data)
    
    return {
        "message": "连接配置已保存",
        "id": conn_id,
        "name": name,
    }


@router.delete("/connections/{conn_id}", summary="删除连接配置")
async def delete_connection(conn_id: str):
    """删除指定ID的连接配置"""
    config_data = _load_connections_config()
    connections = config_data.get("connections", [])
    
    original_count = len(connections)
    connections = [c for c in connections if c.get("id") != conn_id]
    
    if len(connections) == original_count:
        raise HTTPException(status_code=404, detail=f"连接配置不存在: {conn_id}")
    
    config_data["connections"] = connections
    if config_data.get("default") == conn_id:
        config_data["default"] = None
    _save_connections_config(config_data)
    
    return {"message": "连接配置已删除"}


@router.post("/connections/{conn_id}/set-default", summary="设置默认连接")
async def set_default_connection(conn_id: str):
    """设置指定ID的连接为默认连接"""
    config_data = _load_connections_config()
    connections = config_data.get("connections", [])
    
    # 验证连接是否存在
    exists = any(c.get("id") == conn_id for c in connections)
    if not exists:
        raise HTTPException(status_code=404, detail=f"连接配置不存在: {conn_id}")
    
    config_data["default"] = conn_id
    _save_connections_config(config_data)
    
    return {"message": "默认连接已设置"}


@router.post("/connections/{conn_id}/connect", summary="使用已保存的连接配置连接")
async def connect_by_saved_config(conn_id: str):
    """
    使用已保存的连接配置连接到TDSQL实例。
    """
    global _pool
    config_data = _load_connections_config()
    connections = config_data.get("connections", [])
    
    # 查找连接配置
    conn_config = None
    for c in connections:
        if c.get("id") == conn_id:
            conn_config = c
            break
    
    if not conn_config:
        raise HTTPException(status_code=404, detail=f"连接配置不存在: {conn_id}")
    
    try:
        from backend.services.tdsql_connector import TDSQLConnectionPool, TDSQLConnectionConfig
        config = TDSQLConnectionConfig(
            host=conn_config["host"],
            port=conn_config.get("port", 3306),
            user=conn_config["user"],
            password=conn_config.get("password", ""),
            database=conn_config.get("database", ""),
            charset=conn_config.get("charset", "utf8mb4"),
        )
        new_pool = TDSQLConnectionPool(config)
        # 验证连接可用性
        try:
            with new_pool.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    cursor.fetchone()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"连接失败: {str(e)}")
        
        with _pool_lock:
            if _pool:
                try:
                    _pool.close_all()
                except Exception:
                    pass
            _pool = new_pool
        
        return {
            "message": "连接成功",
            "name": conn_config.get("name"),
            "host": conn_config["host"],
            "port": conn_config.get("port", 3306),
            "database": conn_config.get("database", ""),
            "user": conn_config.get("user", ""),
        }
    except ImportError:
        raise HTTPException(status_code=500, detail="pymysql未安装，请执行: pip install pymysql")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"连接失败: {str(e)}")


@router.get("/proxy-config", summary="获取Proxy慢日志配置")
async def get_proxy_config():
    """获取TDSQL Proxy层慢日志相关配置

    执行 /*proxy*/show config 命令获取Proxy配置信息，
    返回慢日志阈值（slow_log_ms）、日志级别（slow_log_level）等参数，
    方便用户确认Proxy慢日志配置是否符合预期。
    """
    pool = _get_pool()
    try:
        config = pool.get_proxy_config()
        return {
            "status": "success",
            "proxy_config": config,
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"获取Proxy配置失败: {str(e)}",
            "proxy_config": None,
        }
