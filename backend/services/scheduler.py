"""
TDSQL SQL审核工具 - 定时任务调度器

使用 APScheduler 实现定时慢日志拉取任务。
自动从 TDSQL performance_schema 拉取慢查询日志，存储到 SQLite 并触发分析。
"""
import logging
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.config import (
    SCHEDULER_CRON_HOUR,
    SCHEDULER_CRON_MINUTE,
    SCHEDULER_ENABLED,
    SCHEDULER_SLOW_QUERY_LIMIT,
    SCHEDULER_SLOW_QUERY_MIN_TIME,
    TDSQL_CONFIG,
    is_tdsql_configured,
    load_tdsql_config_from_file,
)

logger = logging.getLogger("tdsql.scheduler")

# 全局调度器实例
_scheduler: Optional[BackgroundScheduler] = None


def _fetch_and_analyze_slow_queries():
    """
    同步执行慢日志拉取和分析。

    从 TDSQL performance_schema.events_statements_summary_by_digest 拉取 TopN 慢SQL，
    保存到 SQLite 并触发分析。
    """
    logger.info("[定时任务] 开始拉取慢查询日志...")

    try:
        from backend.engine.slow_analyzer import SlowQueryRecord
        from backend.services.slow_query_service import SlowQueryService
        from backend.services.tdsql_connector import TDSQLConnectionConfig, TDSQLConnector

        # 获取连接配置
        config_data = TDSQL_CONFIG if TDSQL_CONFIG.get("host") else load_tdsql_config_from_file()
        if not config_data.get("host") or not config_data.get("user"):
            logger.warning("[定时任务] TDSQL连接参数未配置，跳过本次拉取")
            return

        # 建立连接
        conn_config = TDSQLConnectionConfig(
            host=config_data["host"],
            port=config_data.get("port", 3306),
            user=config_data["user"],
            password=config_data.get("password", ""),
            database=config_data.get("database", ""),
            charset=config_data.get("charset", "utf8mb4"),
            connect_timeout=config_data.get("connect_timeout", 5),
            read_timeout=config_data.get("read_timeout", 30),
        )
        connector = TDSQLConnector(conn_config)
        connector.connect()
        logger.info(f"[定时任务] 已连接TDSQL: {conn_config.host}:{conn_config.port}")

        # 从 performance_schema 拉取慢查询
        raw_queries = connector.get_slow_queries_from_digest(limit=SCHEDULER_SLOW_QUERY_LIMIT)
        logger.info(f"[定时任务] 拉取到 {len(raw_queries)} 条慢查询记录")

        # NOTE: mysql.slow_log 在TDSQL分布式实例中不可用（SET实例不记录数据，
        # 慢日志由Proxy层统一管理）。仅使用 performance_schema 作为数据源。

        connector.disconnect()

        if not raw_queries:
            logger.info("[定时任务] 无新的慢查询记录")
            return

        # 存储并分析
        service = SlowQueryService()
        saved_count = 0

        for raw in raw_queries:
            sql_text = (
                raw.get("sql_text")
                or raw.get("info")
                or raw.get("DIGEST_TEXT", "")
            )
            if not sql_text or len(sql_text.strip()) < 10:
                continue

            record = SlowQueryRecord(
                fingerprint=raw.get("DIGEST_TEXT", sql_text),
                sql_text=sql_text,
                db_name=raw.get("SCHEMA_NAME") or raw.get("db", "") or config_data.get("database", ""),
                exec_count=raw.get("exec_count") or raw.get("COUNT_STAR", 0) or 0,
                total_time_ms=float(raw.get("total_seconds", 0) or 0) * 1000,
                avg_time_ms=float(raw.get("avg_seconds", 0) or 0) * 1000,
                max_time_ms=float(raw.get("max_seconds", 0) or 0) * 1000,
                rows_examined=raw.get("rows_examined") or raw.get("SUM_ROWS_EXAMINED", 0) or 0,
                rows_sent=raw.get("rows_sent") or raw.get("SUM_ROWS_SENT", 0) or 0,
                lock_time_ms=float(raw.get("lock_time_ms", 0) or 0),
            )

            try:
                service.add_slow_query(record)
                saved_count += 1
            except Exception as e:
                logger.warning(f"[定时任务] 保存慢查询失败: {e}")

        logger.info(f"[定时任务] 完成: 保存并分析了 {saved_count} 条慢查询")

    except ImportError as e:
        logger.error(f"[定时任务] 缺少依赖: {e}")
    except Exception as e:
        logger.error(f"[定时任务] 执行异常: {e}", exc_info=True)


def start_scheduler() -> Optional[BackgroundScheduler]:
    """
    启动定时任务调度器。

    根据配置文件决定是否启用定时任务。
    默认每天凌晨2:00执行慢日志拉取。
    使用BackgroundScheduler在后台线程运行，避免阻塞FastAPI事件循环。

    Returns:
        调度器实例，未启用时返回 None
    """
    global _scheduler

    if not SCHEDULER_ENABLED:
        logger.info("定时任务调度器未启用（设置 SCHEDULER_ENABLED=true 以开启）")
        return None

    if not is_tdsql_configured():
        logger.warning("TDSQL连接参数未配置，定时任务调度器无法启动")
        return None

    _scheduler = BackgroundScheduler(
        timezone="Asia/Shanghai",
        job_defaults={
            "coalesce": True,       # 错过的任务合并为一次执行
            "max_instances": 1,     # 同一任务最多一个实例
            "misfire_grace_time": 3600,  # 错过1小时内仍执行
        },
    )

    # 添加定时慢日志拉取任务
    cron_trigger = CronTrigger(
        hour=SCHEDULER_CRON_HOUR,
        minute=SCHEDULER_CRON_MINUTE,
        timezone="Asia/Shanghai",
    )

    _scheduler.add_job(
        _fetch_and_analyze_slow_queries,
        trigger=cron_trigger,
        id="fetch_slow_queries",
        name="定时拉取慢查询日志",
        replace_existing=True,
    )

    # 添加每小时轻量级检查任务（检查连接状态）
    _scheduler.add_job(
        _health_check,
        trigger=CronTrigger(minute=0),
        id="tdsql_health_check",
        name="TDSQL连接健康检查",
        replace_existing=True,
    )

    _scheduler.start()
    logger.info(
        f"定时任务调度器已启动 - "
        f"慢日志拉取: 每天 {SCHEDULER_CRON_HOUR:02d}:{SCHEDULER_CRON_MINUTE:02d}, "
        f"健康检查: 每小时整点"
    )
    return _scheduler


def stop_scheduler():
    """停止定时任务调度器"""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("定时任务调度器已停止")


def get_scheduler_status() -> dict:
    """获取调度器状态信息"""
    jobs = []
    if _scheduler and _scheduler.running:
        for job in _scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run": str(job.next_run_time) if job.next_run_time else None,
                "trigger": str(job.trigger),
            })

    return {
        "enabled": SCHEDULER_ENABLED,
        "running": _scheduler is not None and _scheduler.running,
        "cron_hour": SCHEDULER_CRON_HOUR,
        "cron_minute": SCHEDULER_CRON_MINUTE,
        "slow_query_limit": SCHEDULER_SLOW_QUERY_LIMIT,
        "slow_query_min_time": SCHEDULER_SLOW_QUERY_MIN_TIME,
        "jobs": jobs,
    }


def _health_check():
    """TDSQL 连接健康检查"""
    try:
        from backend.services.tdsql_connector import TDSQLConnectionConfig, TDSQLConnector

        config_data = TDSQL_CONFIG if TDSQL_CONFIG.get("host") else load_tdsql_config_from_file()
        if not config_data.get("host"):
            return

        conn_config = TDSQLConnectionConfig(
            host=config_data["host"],
            port=config_data.get("port", 3306),
            user=config_data["user"],
            password=config_data.get("password", ""),
        )
        connector = TDSQLConnector(conn_config)
        connector.connect()
        connector.disconnect()
        logger.debug("[健康检查] TDSQL连接正常")
    except Exception as e:
        logger.warning(f"[健康检查] TDSQL连接异常: {e}")


def manual_fetch_slow_queries() -> dict:
    """
    手动触发慢日志拉取。

    可通过 API 接口调用，立即执行一次慢日志拉取。
    """
    _fetch_and_analyze_slow_queries()
    return {"message": "慢日志拉取任务已执行，请查看日志获取详情"}
