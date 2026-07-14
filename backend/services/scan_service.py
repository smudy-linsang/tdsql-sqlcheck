"""
TDSQL SQL审核工具 - 慢SQL扫描服务 (V2.0)

将扫描逻辑从API层抽取为独立服务，供手动扫描API和定时调度器共用。

V2.0 增强:
- 通过连接注册表按 connection_id 路由到目标库
- 扫描并发限流（按连接 + 全局，保护目标库）
- SQL文本入库脱敏（字面量→?，防止客户敏感数据落地）
"""
import logging
from typing import Optional

from backend import config
from backend.services.connection_registry import registry

logger = logging.getLogger("tdsql.scan")

VALID_SOURCES = {"digest", "processlist", "monitordb"}


def run_scan(connection_id: Optional[str] = None, source: str = "digest",
             limit: int = 50, min_time: float = 0.1,
             task_name: str = "", time_window_start: str = "",
             time_window_end: str = "", poll_duration: float = 10.0,
             poll_interval: float = 1.0, operator: str = "",
             pool=None) -> dict:
    """
    执行一次慢SQL扫描并入库分析。

    Args:
        connection_id: 目标连接ID（空则使用默认/即席连接）
        source: digest(性能摘要,推荐) / processlist(实时进程轮询)
        limit: 抓取条数上限
        min_time: 最小耗时阈值(秒)
        task_name: 自定义任务名
        time_window_start/end: 时间窗口（digest模式必填，作为任务元数据）
        poll_duration/poll_interval: processlist轮询参数
        operator: 操作用户（审计）
        pool: 显式指定连接池（可选，供测试注入/V1.0兼容路径使用）

    Returns:
        {"source", "fetched", "scan_task_id", "errors", "results"}

    Raises:
        ValueError: 参数非法
        ScanBusyError: 并发超限
        ConnectionNotFoundError: 连接不可用
    """
    from backend.engine.slow_analyzer import SlowQueryRecord
    from backend.services.slow_query_service import SlowQueryService

    # 校验顺序与V1.0保持一致: 连接(400) → 数据源(400) → 时间窗口(422)
    if pool is None:
        pool = registry.get(connection_id)

    if source not in VALID_SOURCES:
        raise ValueError(
            f"不支持的数据源: {source}。TDSQL分布式实例支持: "
            f"monitordb(集群级慢SQL,推荐)、digest(性能摘要分析)、processlist(实时进程快照)。"
            f"注意: mysql.slow_log在TDSQL分布式架构下不可用（数据由Proxy层管理）")

    if source == "digest" and (not time_window_start or not time_window_end):
        raise ValueError("时间窗口开始和结束时间为必填项，请指定扫描时间范围（记录为任务元数据）")

    if time_window_start and time_window_end and time_window_start > time_window_end:
        raise ValueError("时间窗口开始时间不能晚于结束时间")

    conn_key = connection_id or "default"

    with registry.scan_slot(conn_key):
        return _do_scan(pool, connection_id or "", source, limit, min_time,
                        task_name, time_window_start, time_window_end,
                        poll_duration, poll_interval, operator)


def _do_scan(pool, connection_id: str, source: str, limit: int, min_time: float,
             task_name: str, time_window_start: str, time_window_end: str,
             poll_duration: float, poll_interval: float, operator: str) -> dict:
    from backend.engine.slow_analyzer import SlowQueryRecord
    from backend.services import metrics_service
    from backend.services.slow_query_service import SlowQueryService

    service = SlowQueryService()
    db_name = pool.config.database or ""
    conn_name = f"{pool.config.host}:{pool.config.port}"

    # 创建扫描任务（任务名自动包含时间段）
    source_labels = {"digest": "性能摘要分析", "processlist": "实时进程快照",
                     "monitordb": "集群级慢SQL(monitordb)"}
    time_range_str = ""
    if time_window_start and time_window_end:
        start_short = time_window_start[5:16]
        end_short = time_window_end[5:16]
        time_range_str = f" [{start_short} ~ {end_short}]"
    final_task_name = (task_name or
                       f"{source_labels.get(source, source)} - {conn_name}") + time_range_str
    task_id = service.create_scan_task(
        task_name=final_task_name, source=source, db_name=db_name,
        connection_id=connection_id, connection_name=conn_name,
        time_window_start=time_window_start, time_window_end=time_window_end,
        created_by=operator,
    )

    results = []
    errors = []
    try:
        if source == "monitordb":
            # 集群级慢SQL：读 tdsqlpcloud_monitor.proxy_classes_analysis，
            # 时间窗过滤走 timestramp（命中索引），一次取全集群、免 set_list。
            raw_queries = pool.get_cluster_slow_queries(
                limit=limit, min_time=min_time,
                time_start=time_window_start or None,
                time_end=time_window_end or None)
        elif source == "digest":
            # 注意: TDSQL Proxy的performance_schema不支持FIRST_SEEN/LAST_SEEN时间过滤，
            # 时间窗口仅作为扫描任务元数据记录，不传入SQL查询。
            raw_queries = pool.get_slow_queries_from_digest(
                limit=limit, min_time=min_time)
        else:
            duration = max(1.0, min(poll_duration, 60.0))
            interval = max(0.5, min(poll_interval, 5.0))
            raw_queries = pool.poll_processlist(
                duration_seconds=duration, interval=interval, min_time=min_time)
    except Exception as e:
        errors.append({"source": source, "error": str(e)})
        raw_queries = []

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

        # 执行者信息（monitordb/processlist有具体用户/IP，digest模式为聚合无此维度）
        client_user = raw.get("client_user") or raw.get("user", "") or ""
        client_host = raw.get("client_host") or raw.get("host", "") or ""
        if isinstance(client_user, bytes):
            client_user = client_user.decode("utf-8", errors="replace")
        if isinstance(client_host, bytes):
            client_host = client_host.decode("utf-8", errors="replace")

        first_seen_val = str(raw["FIRST_SEEN"]) if raw.get("FIRST_SEEN") else ""
        last_seen_val = str(raw["LAST_SEEN"]) if raw.get("LAST_SEEN") else ""

        record = SlowQueryRecord(
            fingerprint=raw.get("DIGEST_TEXT", sql_text),
            sql_text=sql_text,
            db_name=db_val,
            set_id=(raw.get("set_ids", "") or "")[:250],  # 分布式逐SET合并时记录命中的SET及次数
            client_user=client_user,
            client_host=client_host,
            exec_count=raw.get("exec_count") or raw.get("COUNT_STAR", 0) or 0,
            total_time_ms=total_ms,
            avg_time_ms=avg_ms,
            max_time_ms=max_ms,
            lock_time_ms=lock_time_ms,
            rows_examined=raw.get("rows_examined") or raw.get("SUM_ROWS_EXAMINED", 0) or 0,
            rows_sent=raw.get("rows_sent") or raw.get("SUM_ROWS_SENT", 0) or 0,
            rows_affected=int(raw.get("rows_affected", 0) or 0),
            first_seen=first_seen_val,
            last_seen=last_seen_val,
        )

        result = service.add_slow_query(
            record, scan_task_id=task_id, connection_id=connection_id)
        results.append(result)

    service.complete_scan_task(
        task_id, total_fetched=len(results), total_analyzed=len(results))
    metrics_service.inc("tdsql_scan_tasks_total",
                        {"status": "failed" if errors else "completed"})
    if operator:
        logger.info("扫描完成: operator=%s conn=%s source=%s fetched=%d",
                    operator, connection_id or "default", source, len(results))

    return {
        "source": source,
        "fetched": len(results),
        "scan_task_id": task_id,
        "errors": errors,
        "results": results,
    }
