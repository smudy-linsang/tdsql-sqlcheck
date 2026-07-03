"""
TDSQL SQL审核工具 - 定时任务调度器 (V2.0)

使用 APScheduler 实现后台定时任务:
1. [V1.0兼容] 环境变量配置的单实例慢日志拉取（每日 SCHEDULER_CRON_HOUR:MINUTE）
2. [V2.0] 按连接的扫描计划（scan_schedules 表，每分钟检查到期计划）
3. [V2.0] 每日数据保留清理（retention_policies）
4. TDSQL连接健康检查（每小时）

V2.0 多副本安全: 通过 scheduler_lease 表实现 leader 租约，
多副本部署时仅 leader 执行调度任务，防止重复扫描/清理。
"""
import logging
import socket
import uuid
from datetime import datetime, timedelta
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from backend.config import (
    SCHEDULER_CRON_HOUR,
    SCHEDULER_CRON_MINUTE,
    SCHEDULER_ENABLED,
    SCHEDULER_SLOW_QUERY_LIMIT,
    SCHEDULER_SLOW_QUERY_MIN_TIME,
    TDSQL_CONFIG,
    is_tdsql_configured,
    load_tdsql_config_from_file,
    retention_cron_hour,
)

logger = logging.getLogger("tdsql.scheduler")

# 全局调度器实例
_scheduler: Optional[BackgroundScheduler] = None

# 本副本身份（leader租约持有者标识）
_HOLDER_ID = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"

# 租约时长（秒）
_LEASE_TTL_SECONDS = 120


# ══════════════════════════════════════════════════════════════════
# Leader 租约（多副本防重复执行）
# ══════════════════════════════════════════════════════════════════

def _try_acquire_lease() -> bool:
    """尝试获取/续期 leader 租约。返回是否为 leader。"""
    try:
        from backend.services.database import _get_connection, ensure_db
        ensure_db()
        conn = _get_connection()
        try:
            now = datetime.now()
            expires = (now + timedelta(seconds=_LEASE_TTL_SECONDS)).isoformat()
            row = conn.execute(
                "SELECT holder, expires_at FROM scheduler_lease WHERE id = 1").fetchone()
            if row is None:
                conn.execute(
                    "INSERT OR IGNORE INTO scheduler_lease(id, holder, expires_at) VALUES (1, ?, ?)",
                    (_HOLDER_ID, expires))
                conn.commit()
                # 重新读取确认（并发场景下可能被其他副本抢先）
                row = conn.execute(
                    "SELECT holder FROM scheduler_lease WHERE id = 1").fetchone()
                return row and row["holder"] == _HOLDER_ID
            # 自己持有 → 续期；租约过期 → 抢占
            if row["holder"] == _HOLDER_ID or row["expires_at"] < now.isoformat():
                cursor = conn.execute(
                    "UPDATE scheduler_lease SET holder = ?, expires_at = ? "
                    "WHERE id = 1 AND (holder = ? OR expires_at < ?)",
                    (_HOLDER_ID, expires, _HOLDER_ID, now.isoformat()))
                conn.commit()
                return cursor.rowcount > 0
            return False
        finally:
            conn.close()
    except Exception as e:
        logger.warning("leader租约获取失败: %s", e)
        return False


def is_leader() -> bool:
    """当前副本是否为调度 leader"""
    return _try_acquire_lease()


# ══════════════════════════════════════════════════════════════════
# V1.0 兼容: 环境变量单实例慢日志拉取
# ══════════════════════════════════════════════════════════════════

def _fetch_and_analyze_slow_queries():
    """
    同步执行慢日志拉取和分析（V1.0环境变量配置模式）。

    从 TDSQL performance_schema.events_statements_summary_by_digest 拉取 TopN 慢SQL，
    保存到 SQLite 并触发分析。
    """
    if not _try_acquire_lease():
        logger.debug("[定时任务] 非leader副本，跳过慢日志拉取")
        return

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


# ══════════════════════════════════════════════════════════════════
# V2.0: 按连接的扫描计划
# ══════════════════════════════════════════════════════════════════

def _run_due_scan_schedules():
    """
    检查并执行到期的扫描计划（每分钟调用，仅leader执行）。

    到期判定: enabled=1 且 (cron_hour, cron_minute) 等于当前时间(分钟精度)
    且今天尚未执行过。
    """
    if not _try_acquire_lease():
        return

    try:
        from backend.services.database import _get_connection, ensure_db
        ensure_db()
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")

        conn = _get_connection()
        try:
            rows = conn.execute("""
                SELECT * FROM scan_schedules
                WHERE enabled = 1 AND cron_hour = ? AND cron_minute = ?
                  AND (last_run_at IS NULL OR last_run_at < ?)
            """, (now.hour, now.minute, today)).fetchall()
            schedules = [dict(r) for r in rows]
        finally:
            conn.close()

        for sched in schedules:
            _execute_scan_schedule(sched)
    except Exception as e:
        logger.error("[扫描计划] 检查执行异常: %s", e, exc_info=True)


def _execute_scan_schedule(sched: dict):
    """执行单个扫描计划"""
    from backend.services.database import _get_connection
    from backend.services.scan_service import run_scan

    schedule_id = sched["id"]
    conn_id = sched["connection_id"]
    status = "success"
    try:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        window_start = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        result = run_scan(
            connection_id=conn_id,
            source=sched.get("source", "digest"),
            limit=sched.get("limit_rows", 100),
            min_time=sched.get("min_time", 1.0),
            task_name=f"定时扫描计划#{schedule_id}",
            time_window_start=window_start,
            time_window_end=now_str,
            operator="scheduler",
        )
        logger.info("[扫描计划#%d] 连接 %s 扫描完成: %d 条",
                    schedule_id, conn_id, result.get("fetched", 0))
    except Exception as e:
        status = f"failed: {str(e)[:200]}"
        logger.warning("[扫描计划#%d] 连接 %s 执行失败: %s", schedule_id, conn_id, e)

    try:
        conn = _get_connection()
        try:
            conn.execute(
                "UPDATE scan_schedules SET last_run_at = ?, last_run_status = ? WHERE id = ?",
                (datetime.now().isoformat(), status, schedule_id))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════
# V2.0: 数据保留清理
# ══════════════════════════════════════════════════════════════════

def _run_retention_cleanup():
    """每日数据保留清理（仅leader执行）"""
    if not _try_acquire_lease():
        return
    try:
        from backend.services.retention_service import retention_service
        deleted = retention_service.run_cleanup(operator="scheduler")
        if deleted:
            logger.info("[保留清理] 完成: %s", deleted)
    except Exception as e:
        logger.error("[保留清理] 执行异常: %s", e, exc_info=True)


# ══════════════════════════════════════════════════════════════════
# 调度器生命周期
# ══════════════════════════════════════════════════════════════════

def start_scheduler() -> Optional[BackgroundScheduler]:
    """
    启动定时任务调度器。

    根据配置文件决定是否启用定时任务。
    使用BackgroundScheduler在后台线程运行，避免阻塞FastAPI事件循环。

    Returns:
        调度器实例，未启用时返回 None
    """
    global _scheduler

    if not SCHEDULER_ENABLED:
        logger.info("定时任务调度器未启用（设置 SCHEDULER_ENABLED=true 以开启）")
        return None

    _scheduler = BackgroundScheduler(
        timezone="Asia/Shanghai",
        job_defaults={
            "coalesce": True,       # 错过的任务合并为一次执行
            "max_instances": 1,     # 同一任务最多一个实例
            "misfire_grace_time": 3600,  # 错过1小时内仍执行
        },
    )

    # [V1.0兼容] 环境变量单实例慢日志拉取（仅在配置了TDSQL_HOST时注册）
    if is_tdsql_configured():
        _scheduler.add_job(
            _fetch_and_analyze_slow_queries,
            trigger=CronTrigger(
                hour=SCHEDULER_CRON_HOUR,
                minute=SCHEDULER_CRON_MINUTE,
                timezone="Asia/Shanghai",
            ),
            id="fetch_slow_queries",
            name="定时拉取慢查询日志(环境变量实例)",
            replace_existing=True,
        )
        # TDSQL连接健康检查
        _scheduler.add_job(
            _health_check,
            trigger=CronTrigger(minute=0),
            id="tdsql_health_check",
            name="TDSQL连接健康检查",
            replace_existing=True,
        )

    # [V2.0] 按连接的扫描计划（每分钟检查到期计划）
    _scheduler.add_job(
        _run_due_scan_schedules,
        trigger=IntervalTrigger(minutes=1),
        id="scan_schedules",
        name="按连接扫描计划检查",
        replace_existing=True,
    )

    # [V2.0] 每日数据保留清理
    _scheduler.add_job(
        _run_retention_cleanup,
        trigger=CronTrigger(hour=retention_cron_hour(), minute=30,
                            timezone="Asia/Shanghai"),
        id="retention_cleanup",
        name="数据保留清理",
        replace_existing=True,
    )

    _scheduler.start()
    logger.info(
        "定时任务调度器已启动 (V2.0, holder=%s) - "
        "扫描计划: 每分钟检查; 保留清理: 每天 %02d:30; 单实例拉取: %s",
        _HOLDER_ID, retention_cron_hour(),
        f"每天 {SCHEDULER_CRON_HOUR:02d}:{SCHEDULER_CRON_MINUTE:02d}"
        if is_tdsql_configured() else "未配置",
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
        "holder_id": _HOLDER_ID,
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
