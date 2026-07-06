"""
TDSQL SQL审核工具 - TDSQL管理API (V2.0)

提供TDSQL实例连接、连接测试、元数据查询、慢SQL抓取、字符集检查等功能。

V2.0 变更:
- 全局单连接模型 → 连接注册表（connection_id → 连接池），支持数百实例并存
- 所有查询类端点支持 connection_id 参数路由到指定实例
- 连接配置持久化从明文JSON文件迁移到 SQLite（密码Fernet加密）
- 慢SQL扫描抽取到 scan_service（限流 + 脱敏），支持后台异步执行
"""
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.config import TDSQL_CONFIG, is_tdsql_configured, load_tdsql_config_from_file
from backend.services.connection_registry import (
    ADHOC_ID, ConnectionNotFoundError, ScanBusyError, registry,
)

router = APIRouter(prefix="/api/v1/tdsql", tags=["TDSQL管理"])


class TDSQLConnectRequest(BaseModel):
    """TDSQL连接请求"""
    host: str = Field(..., description="TDSQL实例地址")
    port: int = Field(3306, description="端口")
    user: str = Field(..., description="用户名")
    password: str = Field(..., description="密码")
    database: str = Field("", description="默认数据库")
    name: str = Field("", description="连接名称（可选，用于多连接管理）")
    is_default: bool = Field(False, description="是否设为默认连接")
    description: str = Field("", description="连接描述")


class SlowQueryFetchRequest(BaseModel):
    """慢SQL抓取请求"""
    source: str = Field("digest", description="数据源: digest(性能摘要,推荐)/processlist(实时进程轮询)")
    connection_id: str = Field("", description="目标连接ID（空则使用当前/默认连接）")
    limit: int = Field(50, description="抓取条数上限")
    min_time: float = Field(0.1, description="最小耗时阈值(秒)，digest模式按平均耗时过滤，processlist按当前执行时间过滤")
    task_name: str = Field("", description="自定义扫描任务名称")
    time_window_start: str = Field("", description="时间窗口开始 (YYYY-MM-DD HH:MM:SS)")
    time_window_end: str = Field("", description="时间窗口结束 (YYYY-MM-DD HH:MM:SS)")
    poll_duration: float = Field(10.0, description="processlist轮询持续时间(秒)，仅processlist模式有效，默认10秒")
    poll_interval: float = Field(1.0, description="processlist轮询间隔(秒)，仅processlist模式有效，默认1秒")


def _operator(request: Request) -> str:
    return getattr(request.state, "username", "anonymous")


# V1.0 兼容测试席位: 存量测试通过 tdsql_manage._pool = <mock> 注入连接池。
# 生产路径不使用该变量（保持 None），统一走连接注册表。
_pool = None


def _get_pool(connection_id: Optional[str] = None):
    """获取连接池（注册表路由），未连接时返回400"""
    if _pool is not None and not connection_id:
        return _pool
    try:
        return registry.get(connection_id)
    except ConnectionNotFoundError:
        raise HTTPException(
            status_code=400,
            detail="未连接TDSQL实例，请先调用 /api/v1/tdsql/connect 或指定有效的 connection_id")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"连接失败: {str(e)}")


@router.post("/connect", summary="连接TDSQL实例")
async def connect_tdsql(request: TDSQLConnectRequest, http_request: Request):
    """
    以即席方式连接到TDSQL MySQL实例（注册为 adhoc 连接）。

    连接成功后，不带 connection_id 的API调用将默认使用此连接。
    如需长期管理多个实例，请使用 POST /connections 保存配置后按ID连接。
    """
    try:
        from backend.services.tdsql_connector import TDSQLConnectionConfig
        config = TDSQLConnectionConfig(
            host=request.host,
            port=request.port,
            user=request.user,
            password=request.password,
            database=request.database,
        )
        registry.register(ADHOC_ID, config)
        return {
            "message": "连接成功",
            "connection_id": ADHOC_ID,
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
    使用环境变量或配置文件中的参数连接TDSQL（注册为 adhoc 连接）。

    优先级: 环境变量 > 配置文件 > 默认值
    配置文件路径: 项目根目录/config/tdsql.json
    """
    try:
        from backend.services.tdsql_connector import TDSQLConnectionConfig
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
        registry.register(ADHOC_ID, conn_config)
        return {
            "message": "连接成功（配置文件模式）",
            "connection_id": ADHOC_ID,
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
    测试TDSQL连接可用性（不注册连接）。

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
        pool.close_all()

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
async def disconnect_tdsql(connection_id: Optional[str] = None):
    """断开指定连接；不指定 connection_id 时断开全部活跃连接。"""
    count = registry.disconnect(connection_id)
    return {"message": "已断开连接", "disconnected": count}


@router.get("/status", summary="检查连接状态")
async def connection_status():
    """检查所有活跃连接状态（V2.0返回多连接列表）"""
    active = registry.list_active()
    if not active:
        return {"connected": False, "active_connections": []}
    # 兼容V1.0字段：以 adhoc/首个连接作为主连接信息
    primary = next((c for c in active if c["connection_id"] == ADHOC_ID), active[0])
    return {
        "connected": True,
        "host": primary["host"],
        "port": primary["port"],
        "database": primary["database"],
        "user": primary["user"],
        "active_connections": active,
    }


@router.get("/tables", summary="获取表列表")
async def get_tables(database: Optional[str] = None,
                     connection_id: Optional[str] = None):
    """获取数据库中的所有表"""
    conn = _get_pool(connection_id)
    try:
        tables = conn.get_tables(database)
        return {"tables": [dict(t) for t in tables]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tables/{table_name}/metadata", summary="获取表元数据")
async def get_table_metadata(table_name: str, database: Optional[str] = None,
                             connection_id: Optional[str] = None):
    """
    获取表的完整元数据，包括分片键、索引、字段等信息。
    """
    conn = _get_pool(connection_id)
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
async def discover_sets(connection_id: Optional[str] = None):
    """
    通过 /*proxy*/show status 发现 TDSQL 分布式实例的所有 SET（分片）。

    对于非分布式实例（集中式），返回空列表。
    """
    conn = _get_pool(connection_id)
    try:
        sets = conn.discover_sets()
        return {"sets": sets, "total": len(sets)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/slow-queries/fetch", summary="从TDSQL抓取慢SQL")
def fetch_slow_queries(request: SlowQueryFetchRequest, http_request: Request):
    """
    从TDSQL实例抓取慢SQL并自动分析。

    数据源（基于TDSQL分布式架构设计）:
    - digest (推荐): 从 Proxy 层 performance_schema.events_statements_summary_by_digest 获取
      SQL执行统计摘要。这是TDSQL分布式实例唯一可靠的慢SQL数据源，Proxy自动聚合
      所有SET的执行数据。
    - processlist: 从 information_schema.processlist 抓取当前正在执行的SQL快照。
      仅能捕获扫描瞬间正在执行且耗时超过阈值的SQL，适合发现长时间运行的查询。

    V2.0: 支持 connection_id 指定目标实例；扫描受并发限流保护
    （按连接和全局双重限制）；SQL文本入库前自动脱敏。

    注意: TDSQL分布式实例的mysql.slow_log表不记录数据（慢日志由Proxy层统一管理），
    因此不支持slow_log数据源。所有查询直接通过Proxy执行，无需SET路由。
    """
    from backend.services.scan_service import run_scan
    try:
        return run_scan(
            connection_id=request.connection_id or None,
            source=request.source,
            limit=request.limit,
            min_time=request.min_time,
            task_name=request.task_name,
            time_window_start=request.time_window_start,
            time_window_end=request.time_window_end,
            poll_duration=request.poll_duration,
            poll_interval=request.poll_interval,
            operator=_operator(http_request),
            # V1.0兼容测试席位（生产为None）
            pool=_pool if (_pool is not None and not request.connection_id) else None,
        )
    except ValueError as e:
        # digest时间窗口缺失 → 422 (兼容V1.0行为)，其他参数错误 → 400
        status = 422 if "时间窗口" in str(e) else 400
        raise HTTPException(status_code=status, detail=str(e))
    except ScanBusyError as e:
        raise HTTPException(status_code=429, detail=str(e))
    except ConnectionNotFoundError:
        raise HTTPException(
            status_code=400,
            detail="未连接TDSQL实例，请先调用 /api/v1/tdsql/connect 或指定有效的 connection_id")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/check/charset", summary="字符集一致性检查")
async def check_charset(database: Optional[str] = None,
                        connection_id: Optional[str] = None):
    """
    检查库内字符集和排序规则一致性。
    """
    if not connection_id:
        raise HTTPException(status_code=400, detail="请先选择实例（connection_id必填）")
    conn = _get_pool(connection_id)
    try:
        result = conn.check_charset_consistency(database)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/check/large-tables", summary="大表检查")
async def check_large_tables(
    database: Optional[str] = None,
    threshold_gb: float = 1.0,
    connection_id: Optional[str] = None,
):
    """
    检查大表（参考大表治理规范）。
    """
    if not connection_id:
        raise HTTPException(status_code=400, detail="请先选择实例（connection_id必填）")
    conn = _get_pool(connection_id)
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
async def get_slow_query_config(connection_id: Optional[str] = None):
    """获取TDSQL实例的慢查询相关配置"""
    conn = _get_pool(connection_id)
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
    请求体可选 connection_id 指定目标实例。
    """
    sql = request.get("sql", "")
    if not sql:
        raise HTTPException(status_code=400, detail="sql不能为空")

    conn = _get_pool(request.get("connection_id") or None)

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


# ── 扫描计划管理（V2.0：按连接的定时扫描） ─────────────────


class ScanScheduleRequest(BaseModel):
    """扫描计划请求"""
    connection_id: str = Field(..., description="目标连接ID（已保存的连接配置）")
    source: str = Field("digest", description="数据源: digest/processlist")
    cron_hour: int = Field(2, ge=0, le=23, description="执行小时(0-23)")
    cron_minute: int = Field(0, ge=0, le=59, description="执行分钟(0-59)")
    limit_rows: int = Field(100, description="单次抓取条数上限")
    min_time: float = Field(1.0, description="最小耗时阈值(秒)")
    enabled: bool = Field(True, description="是否启用")


@router.get("/scan-schedules", summary="获取扫描计划列表")
async def list_scan_schedules():
    """获取所有按连接配置的定时扫描计划"""
    from backend.services.database import _get_connection, ensure_db
    ensure_db()
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM scan_schedules ORDER BY connection_id, cron_hour").fetchall()
        return {"schedules": [dict(r) for r in rows]}
    finally:
        conn.close()


@router.post("/scan-schedules", summary="创建扫描计划")
async def create_scan_schedule(body: ScanScheduleRequest, request: Request):
    """为指定连接创建每日定时扫描计划（由调度器leader执行）"""
    if body.source not in ("digest", "processlist"):
        raise HTTPException(status_code=400, detail="source 仅支持 digest/processlist")
    if not registry.get_saved(body.connection_id):
        raise HTTPException(status_code=404, detail=f"连接配置不存在: {body.connection_id}")
    from backend.services.database import _get_connection, ensure_db
    ensure_db()
    conn = _get_connection()
    try:
        cursor = conn.execute("""
            INSERT INTO scan_schedules
                (connection_id, source, cron_hour, cron_minute, limit_rows,
                 min_time, enabled, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (body.connection_id, body.source, body.cron_hour, body.cron_minute,
              body.limit_rows, body.min_time, 1 if body.enabled else 0,
              _operator(request)))
        conn.commit()
        return {"message": "扫描计划已创建", "id": cursor.lastrowid}
    finally:
        conn.close()


@router.put("/scan-schedules/{schedule_id}", summary="更新扫描计划")
async def update_scan_schedule(schedule_id: int, body: ScanScheduleRequest):
    from backend.services.database import _get_connection, ensure_db
    ensure_db()
    conn = _get_connection()
    try:
        cursor = conn.execute("""
            UPDATE scan_schedules
            SET connection_id=?, source=?, cron_hour=?, cron_minute=?,
                limit_rows=?, min_time=?, enabled=?
            WHERE id=?
        """, (body.connection_id, body.source, body.cron_hour, body.cron_minute,
              body.limit_rows, body.min_time, 1 if body.enabled else 0, schedule_id))
        conn.commit()
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="扫描计划不存在")
        return {"message": "扫描计划已更新"}
    finally:
        conn.close()


@router.delete("/scan-schedules/{schedule_id}", summary="删除扫描计划")
async def delete_scan_schedule(schedule_id: int):
    from backend.services.database import _get_connection, ensure_db
    ensure_db()
    conn = _get_connection()
    try:
        cursor = conn.execute("DELETE FROM scan_schedules WHERE id=?", (schedule_id,))
        conn.commit()
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="扫描计划不存在")
        return {"message": "扫描计划已删除"}
    finally:
        conn.close()


# ── 多连接配置管理（V2.0：SQLite加密存储） ─────────────────


@router.get("/connections", summary="获取所有连接配置")
async def get_connections():
    """
    获取所有已保存的连接配置列表（密码脱敏，标记活跃状态）。
    """
    connections = registry.list_saved()
    default_id = None
    for c in connections:
        # 兼容V1.0响应字段
        c["user"] = c.get("username", "")
        if c.get("is_default"):
            default_id = c["id"]
    return {
        "connections": connections,
        "default": default_id,
    }


@router.post("/connections", summary="保存连接配置")
async def save_connection(request: TDSQLConnectRequest, http_request: Request):
    """
    保存一个新的连接配置或更新已存在的连接（密码加密存储到数据库）。
    如果未指定name，将自动生成一个唯一名称。
    """
    conn_id = registry.save_connection(
        name=request.name,
        host=request.host,
        port=request.port,
        username=request.user,
        password=request.password,
        database=request.database,
        is_default=request.is_default,
        description=request.description,
        operator=_operator(http_request),
    )
    return {
        "message": "连接配置已保存",
        "id": conn_id,
        "name": request.name or f"{request.host}:{request.port}",
    }


@router.delete("/connections/{conn_id}", summary="删除连接配置")
async def delete_connection(conn_id: str, request: Request):
    """删除指定ID的连接配置（同时断开其活跃连接）"""
    if not registry.delete_saved(conn_id, operator=_operator(request)):
        raise HTTPException(status_code=404, detail=f"连接配置不存在: {conn_id}")
    return {"message": "连接配置已删除"}


@router.post("/connections/{conn_id}/set-default", summary="设置默认连接")
async def set_default_connection(conn_id: str):
    """设置指定ID的连接为默认连接"""
    if not registry.set_default_saved(conn_id):
        raise HTTPException(status_code=404, detail=f"连接配置不存在: {conn_id}")
    return {"message": "默认连接已设置"}


@router.post("/connections/{conn_id}/connect", summary="使用已保存的连接配置连接")
async def connect_by_saved_config(conn_id: str):
    """
    使用已保存的连接配置建立连接（注册到连接注册表，ID即配置ID）。
    """
    saved = registry.get_saved(conn_id)
    if not saved:
        raise HTTPException(status_code=404, detail=f"连接配置不存在: {conn_id}")
    try:
        pool = registry.get(conn_id, auto_connect=True)
        return {
            "message": "连接成功",
            "connection_id": conn_id,
            "name": saved.get("name"),
            "host": pool.config.host,
            "port": pool.config.port,
            "database": pool.config.database,
            "user": pool.config.user,
        }
    except ImportError:
        raise HTTPException(status_code=500, detail="pymysql未安装，请执行: pip install pymysql")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"连接失败: {str(e)}")


@router.get("/proxy-config", summary="获取Proxy慢日志配置")
async def get_proxy_config(connection_id: Optional[str] = None):
    """获取TDSQL Proxy层慢日志相关配置

    执行 /*proxy*/show config 命令获取Proxy配置信息，
    返回慢日志阈值（slow_log_ms）、日志级别（slow_log_level）等参数，
    方便用户确认Proxy慢日志配置是否符合预期。
    """
    pool = _get_pool(connection_id)
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
