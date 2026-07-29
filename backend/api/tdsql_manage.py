"""
TDSQL SQL审核工具 - TDSQL管理API (V2.0)

提供TDSQL实例连接、连接测试、元数据查询、慢SQL抓取、字符集检查等功能。

V2.0 变更:
- 全局单连接模型 → 连接注册表（connection_id → 连接池），支持数百实例并存
- 所有查询类端点支持 connection_id 参数路由到指定实例
- 连接配置持久化从明文JSON文件迁移到 SQLite（密码Fernet加密）
- 慢SQL扫描抽取到 scan_service（限流 + 脱敏），支持后台异步执行
"""
import re
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
    username: str = Field(..., description="用户名")
    password: str = Field(..., description="密码")
    database: str = Field("", description="默认数据库")
    name: str = Field("", description="连接名称（可选，用于多连接管理）")
    id: str = Field("", description="可选：客户端指定连接ID（幂等键）。留空则服务端生成")
    is_default: bool = Field(False, description="是否设为默认连接")
    is_distributed: bool = Field(True, description="是否分布式实例")
    description: str = Field("", description="连接描述")
    set_list: str = Field("", description="分布式实例SET列表(逗号分隔,从赤兔获取);慢SQL扫描逐SET合并用")
    # monitordb（集群级慢SQL/监控数据源，端口 15001）。留空则复用主连接同名字段
    monitor_host: str = Field("", description="monitordb地址(留空复用主连接host)")
    monitor_port: int = Field(15001, description="monitordb端口(默认15001)")
    monitor_user: str = Field("", description="monitordb用户(留空复用主连接)")
    monitor_password: str = Field("", description="monitordb密码(留空复用主连接)")
    monitor_db: str = Field("tdsqlpcloud_monitor", description="监控库名")


class SlowQueryFetchRequest(BaseModel):
    """慢SQL抓取请求"""
    source: str = Field("monitordb", description="数据源: monitordb(集群级慢SQL,推荐)/digest(性能摘要)/processlist(实时进程轮询)")
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
def connect_tdsql(request: TDSQLConnectRequest, http_request: Request):
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
            user=request.username,
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
            "user": request.username,
        }
    except ImportError:
        raise HTTPException(status_code=500, detail="pymysql未安装，请执行: pip install pymysql")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"连接失败: {str(e)}")


@router.post("/connect-from-config", summary="使用配置文件连接TDSQL")
def connect_from_config(config_path: Optional[str] = None):
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
@router.post("/test-connection", summary="测试TDSQL连接")
def test_connection(host: Optional[str] = None, port: int = 3306,
                          user: Optional[str] = None, password: Optional[str] = None,
                          database: Optional[str] = None,
                          monitor_host: Optional[str] = None, monitor_port: int = 15001,
                          monitor_user: Optional[str] = None, monitor_password: Optional[str] = None,
                          monitor_db: str = "tdsqlpcloud_monitor"):
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
                monitor_host=monitor_host or "", monitor_port=monitor_port,
                monitor_user=monitor_user or "", monitor_password=monitor_password or "",
                monitor_db=monitor_db or "tdsqlpcloud_monitor"
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
                monitor_host=config_data.get("monitor_host", ""),
                monitor_port=config_data.get("monitor_port", 15001),
                monitor_user=config_data.get("monitor_user", ""),
                monitor_password=config_data.get("monitor_password", ""),
                monitor_db=config_data.get("monitor_db", "tdsqlpcloud_monitor"),
            )

        pool = TDSQLConnectionPool(config)
        start_time = time.time()
        
        business_ok = False
        business_err = ""
        server_version = "unknown"
        slow_config = {}
        latency_ms = 0
        
        try:
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
                    pass
            business_ok = True
        except Exception as e:
            business_err = str(e)

        # 测试 monitor 连接
        monitor_ok = False
        monitor_err = ""
        monitor_column_count = 0
        try:
            probe_res = pool.monitor_probe()
            monitor_ok = probe_res["ok"]
            monitor_err = probe_res["error"]
            monitor_column_count = len(probe_res["columns"])
        except Exception as e:
            monitor_err = str(e)
            
        pool.close_all()

        if business_ok:
            return {
                "status": "connected",
                "host": config.host,
                "port": config.port,
                "database": config.database,
                "server_version": server_version,
                "latency_ms": latency_ms,
                "slow_query_config": slow_config,
                "pymysql_available": True,
                "monitor_status": "connected" if monitor_ok else "failed",
                "monitor_error": monitor_err,
                "monitor_column_count": monitor_column_count,
            }
        else:
            return {
                "status": "error",
                "message": f"连接测试失败: {business_err}",
                "pymysql_available": True,
                "monitor_status": "connected" if monitor_ok else "failed",
                "monitor_error": monitor_err,
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
def disconnect_tdsql(connection_id: Optional[str] = None):
    """断开指定连接；不指定 connection_id 时断开全部活跃连接。"""
    count = registry.disconnect(connection_id)
    return {"message": "已断开连接", "disconnected": count}


@router.get("/status", summary="检查连接状态")
def connection_status():
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
def get_tables(database: Optional[str] = None,
                     connection_id: Optional[str] = None):
    """获取数据库中的所有表"""
    conn = _get_pool(connection_id)
    try:
        tables = conn.get_tables(database)
        return {"tables": [dict(t) for t in tables]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tables/{table_name}/metadata", summary="获取表元数据")
def get_table_metadata(table_name: str, database: Optional[str] = None,
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
def discover_sets(connection_id: Optional[str] = None):
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
def check_charset(database: Optional[str] = None,
                        connection_id: Optional[str] = None):
    """
    检查库内字符集和排序规则一致性。
    """
    conn = _get_pool(connection_id)
    try:
        result = conn.check_charset_consistency(database)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/check/large-tables", summary="大表检查")
def check_large_tables(
    database: Optional[str] = None,
    threshold_gb: float = 1.0,
    connection_id: Optional[str] = None,
):
    """
    检查大表（参考大表治理规范）。
    """
    conn = _get_pool(connection_id)
    try:
        tables = conn.check_large_tables(database, threshold_gb)
        return {
            "database": database or conn.config.database or "(全部业务库)",
            "threshold_gb": threshold_gb,
            "total": len(tables),
            "tables": [
                {
                    **t,
                    "TABLE_NAME": t.get("table_name", ""),
                    "TABLE_ROWS": t.get("rows_count", 0),
                }
                for t in tables
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/table-partitions", summary="分区表逐分区明细（大表下钻）")
def get_table_partitions(
    connection_id: Optional[str] = None,
    schema: Optional[str] = None,
    table: Optional[str] = None,
):
    """获取某张分区表的逐分区明细 + 派生分析（数据倾斜/兜底分区过大/分区水位/空分区）。"""
    if not schema or not table:
        raise HTTPException(status_code=400, detail="schema 与 table 必填")
    conn = _get_pool(connection_id)
    try:
        return conn.get_table_partitions(schema, table)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/slow-query-config", summary="获取慢查询配置")
def get_slow_query_config(connection_id: Optional[str] = None):
    """获取TDSQL实例的慢查询相关配置"""
    conn = _get_pool(connection_id)
    try:
        config = conn.get_slow_query_variables()
        return {"variables": config}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/audit/with-metadata", summary="结合数据库元数据进行SQL审核")
def audit_with_metadata(request: dict):
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

        # V1.5.1：解析实例类型（A类通道，多源分级+保守合并），引擎按实例类型过滤适用域
        from backend.services.instance_type_service import instance_type_service
        ictx = instance_type_service.resolve(request.get("connection_id") or "")
        it = ictx.instance_type.value

        # 传递真实元数据给审核引擎
        result = checker.audit_sql(sql, table_metadata=table_metadata, instance_type=it)
        skipped = checker.count_skipped_by_scope(it)
        cn = "分布式" if it == "distributed" else "集中式"

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
                # V1.5：口径自证
                "instance_type": it,
                "instance_type_source": ictx.source.value,
                "instance_type_conflict": ictx.conflict,
                "skipped_rules_count": skipped,
                "scope_notice": (f"本次按【{cn}实例】口径评估，已跳过 {skipped} 条不适用规则。"
                                 if skipped else ""),
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── 定时任务管理 ─────────────────────────────────────────


@router.get("/scheduler/status", summary="查看定时任务状态")
def get_scheduler_status():
    """查看定时慢日志拉取任务的运行状态和调度配置"""
    from backend.services.scheduler import get_scheduler_status
    return get_scheduler_status()


@router.post("/scheduler/trigger", summary="手动触发慢日志拉取")
def trigger_slow_query_fetch():
    """手动触发一次慢日志拉取任务，立即从TDSQL拉取并分析"""
    from backend.services.scheduler import manual_fetch_slow_queries
    return manual_fetch_slow_queries()


# ── 扫描计划管理（V2.0：按连接的定时扫描） ─────────────────


class ScanScheduleRequest(BaseModel):
    """扫描计划请求"""
    connection_id: str = Field(..., description="目标连接ID（已保存的连接配置）")
    source: str = Field("monitordb", description="数据源: monitordb(集群级慢SQL,推荐)/digest/processlist")
    cron_hour: int = Field(2, ge=0, le=23, description="执行小时(0-23)")
    cron_minute: int = Field(0, ge=0, le=59, description="执行分钟(0-59)")
    limit_rows: int = Field(100, description="单次抓取条数上限")
    min_time: float = Field(1.0, description="最小耗时阈值(秒)")
    enabled: bool = Field(True, description="是否启用")


@router.get("/scan-schedules", summary="获取扫描计划列表")
def list_scan_schedules():
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
def create_scan_schedule(body: ScanScheduleRequest, request: Request):
    """为指定连接创建每日定时扫描计划（由调度器leader执行）"""
    if body.source not in ("digest", "processlist", "monitordb"):
        raise HTTPException(status_code=400, detail="source 仅支持 monitordb/digest/processlist")
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
def update_scan_schedule(schedule_id: int, body: ScanScheduleRequest):
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
def delete_scan_schedule(schedule_id: int):
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
def get_connections():
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
def save_connection(request: TDSQLConnectRequest, http_request: Request):
    """
    保存一个新的连接配置或更新已存在的连接（密码加密存储到数据库）。
    如果未指定name，将自动生成一个唯一名称。

    可选传入 id 作为幂等键：此前该字段被 pydantic 静默丢弃、服务端另生成随机 ID，
    自动化运维按原 ID 回查/删除会得到 404，幂等登记契约被破坏。
    若 id 已被另一实例（host:port 不同）占用，返回 409。
    """
    if request.id:
        existing = registry.get_saved(request.id) or {}
        if existing and (existing.get("host") != request.host
                         or int(existing.get("port") or 0) != int(request.port)):
            raise HTTPException(
                status_code=409,
                detail=f"连接ID {request.id} 已被 "
                       f"{existing.get('host')}:{existing.get('port')} 占用")

    conn_id = registry.save_connection(
        conn_id=request.id,
        name=request.name,
        host=request.host,
        port=request.port,
        username=request.username,
        password=request.password,
        database=request.database,
        is_default=request.is_default,
        is_distributed=request.is_distributed,
        description=request.description,
        set_list=request.set_list,
        monitor_host=request.monitor_host,
        monitor_port=request.monitor_port,
        monitor_user=request.monitor_user,
        monitor_password=request.monitor_password,
        monitor_db=request.monitor_db,
        operator=_operator(http_request),
    )
    # V1.5：实例配置变更后失效类型解析缓存，本进程立即生效
    from backend.services.instance_type_service import instance_type_service
    instance_type_service.invalidate(conn_id)
    return {
        "message": "连接配置已保存",
        "id": conn_id,
        "name": request.name or f"{request.host}:{request.port}",
    }


@router.put("/connections/{conn_id}", summary="更新连接配置")
def update_connection(conn_id: str, request: TDSQLConnectRequest, http_request: Request):
    """
    更新已存在的连接配置（所有字段均可修改，密码加密存储）。
    """
    saved = registry.get_saved(conn_id)
    if not saved:
        raise HTTPException(status_code=404, detail=f"连接配置不存在: {conn_id}")
    registry.save_connection(
        name=request.name,
        host=request.host,
        port=request.port,
        username=request.username,
        password=request.password,
        database=request.database,
        is_default=request.is_default,
        is_distributed=request.is_distributed,
        description=request.description,
        set_list=request.set_list,
        monitor_host=request.monitor_host,
        monitor_port=request.monitor_port,
        monitor_user=request.monitor_user,
        monitor_password=request.monitor_password,
        monitor_db=request.monitor_db,
        conn_id=conn_id,
        operator=_operator(http_request),
    )
    # V1.5：实例配置变更后失效类型解析缓存
    from backend.services.instance_type_service import instance_type_service
    instance_type_service.invalidate(conn_id)
    return {
        "message": "连接配置已更新",
        "id": conn_id,
        "name": request.name or f"{request.host}:{request.port}",
    }


@router.post("/connections/{conn_id}/probe-instance-type", summary="探测实例类型（V1.5.1 多源判定）")
def probe_instance_type(conn_id: str, http_request: Request):
    """对指定实例执行一次实例类型判定，返回多源明细（V1.5.1）。

    不再只跑 SQL 探针：依次尝试 锁定/ZK/探测/声明 全部判定源，
    逐源返回 available / value / reason，并给出最终生效结论与下一步建议。

    权限：admin / dba（写实例元数据，等同实例管理操作）。
    探测失败不返回 5xx——网络抖动/权限不足是正常业务分支，此时下沉至声明值。
    """
    if getattr(http_request.state, "role", "") not in ("admin", "dba"):
        raise HTTPException(status_code=403,
                            detail={"detail": "仅管理员/DBA 可探测实例类型", "code": "E403"})
    saved = registry.get_saved(conn_id)
    if not saved:
        raise HTTPException(status_code=404,
                            detail={"detail": f"实例不存在: {conn_id}", "code": "E5012"})
    from backend.services.instance_type_service import instance_type_service
    return instance_type_service.probe_now(conn_id)


@router.put("/connections/{conn_id}/instance-type-lock",
            summary="管理员锁定/解锁实例类型（V1.5.1）")
def set_instance_type_lock(conn_id: str, payload: dict, http_request: Request):
    """V1.5.1：管理员终审实例类型，优先级高于一切自动判定源。

    锁成 centralized 会关掉 27 条仅分布式适用的规则，是唯一可能造成
    静默漏报的操作，因此强制填写理由并落审计日志。

    权限：仅 admin。中间件已覆盖 instances 菜单，但 instances 可能同时授予
    dba，故处理函数内显式校验不可省略（双保险，与 v1.3 _require_admin 同款）。
    """
    if getattr(http_request.state, "role", "") != "admin":
        raise HTTPException(status_code=403,
                            detail={"detail": "仅系统管理员可锁定实例类型", "code": "E403"})
    saved = registry.get_saved(conn_id)
    if not saved:
        raise HTTPException(status_code=404,
                            detail={"detail": f"实例不存在: {conn_id}", "code": "E5012"})

    locked = bool(payload.get("locked"))
    itype = (payload.get("instance_type") or "").strip()
    reason = (payload.get("reason") or "").strip()

    if locked:
        if itype not in ("distributed", "centralized"):
            raise HTTPException(status_code=400,
                                detail="instance_type 仅支持 distributed 或 centralized")
        # 锁 distributed 是保守方向（多跑规则）；锁 centralized 会关规则，必须留下人为决策记录
        if itype == "centralized" and not reason:
            raise HTTPException(
                status_code=400,
                detail="锁定为「集中式」将跳过 27 条仅分布式适用的规则，请填写锁定理由")

    from backend.services.instance_type_service import instance_type_service
    instance_type_service.set_lock(conn_id, locked, itype if locked else None)

    # 审计：加锁/解锁均写 operation_logs
    try:
        from backend.services.database import log_operation
        log_operation(operator=_operator(http_request),
                      operation_type="instance_type_lock",
                      target_type="tdsql_connection", target_id=conn_id,
                      detail=f"locked={locked} value={itype} reason={reason}")
    except Exception as e:
        import logging
        logging.getLogger("tdsql.api").warning(f"锁定操作审计日志写入失败: {e}")

    _cn = {"distributed": "分布式", "centralized": "集中式"}
    return {
        "success": True,
        "connection_id": conn_id,
        "locked": locked,
        "instance_type": itype if locked else "",
        "message": ((f"已锁定实例类型为「{_cn.get(itype, itype)}」。"
                     + (f"该实例后续审核将跳过 27 条仅分布式适用的规则。"
                        if itype == "centralized" else "")
                     + "配置最长 5 分钟后在全部服务进程生效。") if locked
                    else "已解除实例类型锁定，恢复按自动判定源解析。配置最长 5 分钟后在全部服务进程生效。"),
    }


_SAMPLE_TABLE_RE = re.compile(r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)?$")


@router.post("/connections/{conn_id}/probe-diagnostics",
             summary="采集实例类型探测诊断数据（V1.5.1）")
def probe_diagnostics(conn_id: str, payload: dict, http_request: Request):
    """用系统自身的连接在目标实例上执行设计文档 §8.3 的采集清单，
    原样返回输出，供判据实测/复测（换 TDSQL 版本、换现场时一键重采）。

    采集环境 = 判定环境是硬约束的工程化保证：手工从后端 socket 采到的
    输出与系统经 Proxy 端口能看到的未必相同（本次事故正栽在此）。

    权限：admin / dba。
    """
    if getattr(http_request.state, "role", "") not in ("admin", "dba"):
        raise HTTPException(status_code=403,
                            detail={"detail": "仅管理员/DBA 可采集探测诊断", "code": "E403"})
    saved = registry.get_saved(conn_id)
    if not saved:
        raise HTTPException(status_code=404,
                            detail={"detail": f"实例不存在: {conn_id}", "code": "E5012"})

    sample_table = (payload.get("sample_table") or "").strip()
    # 白名单校验：该值会进入 SHOW CREATE TABLE 语句，不能有任何拼接注入面
    if sample_table and not _SAMPLE_TABLE_RE.match(sample_table):
        raise HTTPException(status_code=400,
                            detail="sample_table 仅允许字母数字下划线，可选 db.table 格式")

    pool = _get_pool(conn_id)
    diagnostics = pool.collect_probe_diagnostics(sample_table)

    from datetime import datetime
    declared = "distributed" if int(saved.get("is_distributed", 1) or 0) == 1 else "centralized"
    # 必须回带 endpoint 与类型上下文：缺了这两项，采回的数据无法配对分析
    return {
        "connection_id": conn_id,
        "instance_label": saved.get("name") or "",
        "endpoint": f"{saved.get('host')}:{saved.get('port')}",
        "declared_instance_type": declared,
        "zk_instance_kind": saved.get("zk_instance_kind") or None,
        "collected_at": datetime.now().isoformat(timespec="seconds"),
        "diagnostics": diagnostics,
    }


@router.post("/connections/{conn_id}/monitor-probe", summary="测试 monitordb 连通性")
@router.get("/connections/{conn_id}/probe", summary="探针检测monitordb状态")
def monitor_probe(conn_id: str):
    """探测该连接的 monitordb（15001/tdsqlpcloud_monitor）是否可用。
    仅返回连通性与列数，不回列名明细（避免信息泄露）。"""
    conn = _get_pool(conn_id)
    probe = conn.monitor_probe()
    return {
        "ok": probe["ok"],
        "column_count": len(probe["columns"]),
        "error": probe["error"],
        "monitor_port": conn.config.monitor_port,
        "monitor_db": conn.config.monitor_db,
    }


@router.delete("/connections/{conn_id}", summary="删除连接配置")
def delete_connection(conn_id: str, request: Request):
    """删除指定ID的连接配置（同时断开其活跃连接）"""
    if not registry.delete_saved(conn_id, operator=_operator(request)):
        raise HTTPException(status_code=404, detail=f"连接配置不存在: {conn_id}")
    return {"message": "连接配置已删除"}


@router.post("/connections/{conn_id}/set-default", summary="设置默认连接")
@router.post("/connections/{conn_id}/default", summary="设为默认连接")
def set_default_connection(conn_id: str):
    """设置指定ID的连接为默认连接"""
    if not registry.set_default_saved(conn_id):
        raise HTTPException(status_code=404, detail=f"连接配置不存在: {conn_id}")
    return {"message": "默认连接已设置"}


@router.post("/connections/{conn_id}/connect", summary="激活连接")
def connect_by_saved_config(conn_id: str):
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


@router.get("/proxy-config", summary="获取Proxy与分布式拓扑参数")
def get_proxy_config(connection_id: Optional[str] = None):
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
