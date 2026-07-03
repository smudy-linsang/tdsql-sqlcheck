"""
TDSQL SQL审核工具 - 慢SQL服务层

提供慢SQL的管理、分析和优化建议服务。
"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from backend import config
from backend.engine.slow_analyzer import (
    SlowAnalysisReport,
    SlowQueryRecord,
    SlowSQLAnalyzer,
)


# 数据库路径
DB_DIR = Path(__file__).parent.parent.parent / "data"
DB_PATH = DB_DIR / "tdsql_check.db"


def _get_connection() -> sqlite3.Connection:
    """获取数据库连接（WAL模式 + 超时设置，避免并发写入冲突）"""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    # 启用WAL模式，提升并发读写性能
    conn.execute("PRAGMA journal_mode=WAL")
    # 设置busy超时，避免 "database is locked" 错误
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db():
    """初始化数据库表"""
    conn = _get_connection()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS slow_queries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint TEXT NOT NULL,
                sql_text TEXT NOT NULL,
                db_name TEXT DEFAULT '',
                set_id TEXT DEFAULT '',           -- 来源 SET（如 set_1），非分布式为空
                exec_count INTEGER DEFAULT 0,
                total_time_ms REAL DEFAULT 0,
                avg_time_ms REAL DEFAULT 0,
                max_time_ms REAL DEFAULT 0,
                rows_examined INTEGER DEFAULT 0,
                rows_sent INTEGER DEFAULT 0,
                lock_time_ms REAL DEFAULT 0,
                first_seen TEXT,
                last_seen TEXT,
                problem_type TEXT DEFAULT '',
                severity TEXT DEFAULT 'INFO',
                root_cause TEXT DEFAULT '',
                suggestion TEXT DEFAULT '',
                optimized_sql TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                analysis_json TEXT DEFAULT '{}',
                scan_task_id INTEGER,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS audit_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                audit_type TEXT NOT NULL,
                source TEXT DEFAULT '',
                total_sql INTEGER DEFAULT 0,
                passed INTEGER DEFAULT 0,
                failed INTEGER DEFAULT 0,
                error_count INTEGER DEFAULT 0,
                warning_count INTEGER DEFAULT 0,
                pass_rate REAL DEFAULT 0,
                results_json TEXT DEFAULT '[]',
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_slow_fingerprint ON slow_queries(fingerprint);
            CREATE INDEX IF NOT EXISTS idx_slow_db ON slow_queries(db_name);
            CREATE INDEX IF NOT EXISTS idx_slow_set_id ON slow_queries(set_id);
            CREATE INDEX IF NOT EXISTS idx_slow_status ON slow_queries(status);
            CREATE INDEX IF NOT EXISTS idx_audit_type ON audit_history(audit_type);
        """)
        conn.commit()
    finally:
        conn.close()


_db_initialized = False


def _ensure_db():
    """确保数据库已初始化（懒加载）"""
    global _db_initialized
    if not _db_initialized:
        init_db()
        _db_initialized = True


class SlowQueryService:
    """慢SQL服务"""

    def __init__(self):
        from backend.services.database import ensure_db
        ensure_db()
        self.analyzer = SlowSQLAnalyzer()

    def add_slow_query(self, record: SlowQueryRecord, scan_task_id: int = None,
                       connection_id: str = "") -> dict:
        """
        添加慢SQL记录并自动分析。

        Args:
            record: 慢SQL记录
            scan_task_id: 可选，关联的扫描任务ID
            connection_id: 可选，来源连接ID（V2.0多实例）

        V2.0: DATA_MASKING_ENABLED 开启时（默认），SQL文本入库前将字面量
        替换为 ?，防止WHERE条件中的客户敏感数据（身份证/卡号等）落地。
        分析在脱敏前的原文上执行，不影响诊断质量。

        Returns:
            包含分析结果的字典
        """
        # 执行分析（在原文上分析，保证诊断质量）
        report = self.analyzer.analyze_slow_query(record)

        # V2.0: 入库脱敏
        stored_sql = record.sql_text
        stored_fingerprint = record.fingerprint
        if config.data_masking_enabled():
            from backend.engine.fingerprint import FingerprintEngine
            engine = FingerprintEngine()
            stored_sql = engine.normalize_for_display(record.sql_text)
            stored_fingerprint = engine.normalize_for_display(record.fingerprint) \
                if record.fingerprint else stored_sql

        conn = _get_connection()
        try:
            now = datetime.now().isoformat()
            # first_seen/last_seen: 优先使用record中的真实执行时间，否则用当前时间
            first_seen = record.first_seen or now
            last_seen = record.last_seen or now
            cursor = conn.execute("""
                INSERT INTO slow_queries (
                    fingerprint, sql_text, db_name, set_id, client_user, client_host,
                    connection_id,
                    exec_count, total_time_ms, avg_time_ms, max_time_ms,
                    rows_examined, rows_sent, lock_time_ms,
                    first_seen, last_seen, problem_type, severity,
                    root_cause, suggestion, optimized_sql,
                    analysis_json, scan_task_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                stored_fingerprint, stored_sql, record.db_name,
                record.set_id, record.client_user, record.client_host,
                connection_id,
                record.exec_count, record.total_time_ms, record.avg_time_ms,
                record.max_time_ms, record.rows_examined, record.rows_sent,
                record.lock_time_ms, first_seen, last_seen,
                report.problem_type, report.severity,
                report.analyses[0].root_cause if report.analyses else "",
                report.analyses[0].suggestion if report.analyses else "",
                report.analyses[0].optimized_sql if report.analyses else "",
                json.dumps([{
                    "problem_type": a.problem_type,
                    "severity": a.severity,
                    "description": a.description,
                    "evidence": a.evidence,
                    "root_cause": a.root_cause,
                    "suggestion": a.suggestion,
                    "optimized_sql": a.optimized_sql,
                } for a in report.analyses], ensure_ascii=False),
                scan_task_id,
                now, now,
            ))
            conn.commit()
            slow_id = cursor.lastrowid
        finally:
            conn.close()

        return {
            "id": slow_id,
            "fingerprint": record.fingerprint,
            "problem_type": report.problem_type,
            "severity": report.severity,
            "summary": report.summary,
            "analyses": [{
                "problem_type": a.problem_type,
                "severity": a.severity,
                "description": a.description,
                "evidence": a.evidence,
                "root_cause": a.root_cause,
                "suggestion": a.suggestion,
                "optimized_sql": a.optimized_sql,
            } for a in report.analyses],
        }

    def get_slow_queries(
        self,
        db_name: Optional[str] = None,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        scan_task_id: Optional[int] = None,
        set_id: Optional[str] = None,
        keyword: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict:
        """获取慢SQL列表，支持多维度筛选"""
        conn = _get_connection()
        try:
            conditions = []
            params = []
            if db_name:
                conditions.append("db_name = ?")
                params.append(db_name)
            if status:
                conditions.append("status = ?")
                params.append(status)
            if severity:
                conditions.append("severity = ?")
                params.append(severity)
            if scan_task_id is not None:
                conditions.append("scan_task_id = ?")
                params.append(scan_task_id)
            if set_id:
                conditions.append("set_id = ?")
                params.append(set_id)
            if keyword:
                conditions.append("(fingerprint LIKE ? OR sql_text LIKE ?)")
                params.extend([f"%{keyword}%", f"%{keyword}%"])

            where_clause = " AND ".join(conditions) if conditions else "1=1"

            # 总数
            count_sql = f"SELECT COUNT(*) as cnt FROM slow_queries WHERE {where_clause}"
            total = conn.execute(count_sql, params).fetchone()["cnt"]

            # 分页查询
            query_sql = f"""
                SELECT * FROM slow_queries
                WHERE {where_clause}
                ORDER BY avg_time_ms DESC, exec_count DESC
                LIMIT ? OFFSET ?
            """
            params.extend([limit, offset])
            rows = conn.execute(query_sql, params).fetchall()

            items = []
            for row in rows:
                item = dict(row)
                if item.get("analysis_json"):
                    try:
                        item["analyses"] = json.loads(item["analysis_json"])
                    except json.JSONDecodeError:
                        item["analyses"] = []
                del item["analysis_json"]
                items.append(item)
        finally:
            conn.close()

        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def get_slow_query_detail(self, slow_id: int) -> Optional[dict]:
        """获取慢SQL详情"""
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM slow_queries WHERE id = ?", (slow_id,)
            ).fetchone()
            if not row:
                return None
            item = dict(row)
            if item.get("analysis_json"):
                try:
                    item["analyses"] = json.loads(item["analysis_json"])
                except json.JSONDecodeError:
                    item["analyses"] = []
            del item["analysis_json"]
            return item
        finally:
            conn.close()

    def update_status(self, slow_id: int, status: str) -> bool:
        """更新慢SQL状态"""
        conn = _get_connection()
        try:
            conn.execute(
                "UPDATE slow_queries SET status = ?, updated_at = ? WHERE id = ?",
                (status, datetime.now().isoformat(), slow_id),
            )
            conn.commit()
            return conn.total_changes > 0
        finally:
            conn.close()

    def analyze_explain(self, explain_data: list[dict]) -> dict:
        """
        分析EXPLAIN执行计划。

        Args:
            explain_data: EXPLAIN输出数据

        Returns:
            分析报告
        """
        report = self.analyzer.analyze_explain(explain_data)
        return {
            "summary": report.summary,
            "analyses": [{
                "problem_type": a.problem_type,
                "severity": a.severity,
                "description": a.description,
                "evidence": a.evidence,
                "root_cause": a.root_cause,
                "suggestion": a.suggestion,
                "optimized_sql": a.optimized_sql,
            } for a in report.analyses],
        }

    def get_statistics(self) -> dict:
        """获取慢SQL统计信息"""
        conn = _get_connection()
        try:
            # 总数
            total = conn.execute("SELECT COUNT(*) as cnt FROM slow_queries").fetchone()["cnt"]

            # 按严重程度统计
            severity_stats = {}
            for row in conn.execute(
                "SELECT severity, COUNT(*) as cnt FROM slow_queries GROUP BY severity"
            ).fetchall():
                severity_stats[row["severity"]] = row["cnt"]

            # 按状态统计
            status_stats = {}
            for row in conn.execute(
                "SELECT status, COUNT(*) as cnt FROM slow_queries GROUP BY status"
            ).fetchall():
                status_stats[row["status"]] = row["cnt"]

            # Top10 高耗时
            top_by_time = []
            for row in conn.execute(
                "SELECT id, fingerprint, avg_time_ms, exec_count, severity FROM slow_queries ORDER BY avg_time_ms DESC LIMIT 10"
            ).fetchall():
                top_by_time.append(dict(row))

            # Top10 高频次
            top_by_freq = []
            for row in conn.execute(
                "SELECT id, fingerprint, avg_time_ms, exec_count, severity FROM slow_queries ORDER BY exec_count DESC LIMIT 10"
            ).fetchall():
                top_by_freq.append(dict(row))

            return {
                "total": total,
                "by_severity": severity_stats,
                "by_status": status_stats,
                "top_by_time": top_by_time,
                "top_by_frequency": top_by_freq,
            }
        finally:
            conn.close()

    # ============ 扫描任务管理 ============

    def create_scan_task(
        self,
        task_name: str,
        source: str,
        db_name: str = "",
        connection_id: str = "",
        connection_name: str = "",
        time_window_start: str = "",
        time_window_end: str = "",
    ) -> int:
        """创建扫描任务记录，返回任务ID"""
        conn = _get_connection()
        try:
            cursor = conn.execute(
                """INSERT INTO scan_tasks
                   (task_name, source, db_name, connection_id, connection_name,
                    time_window_start, time_window_end,
                    total_fetched, total_analyzed, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 'running', ?)""",
                (task_name, source, db_name, connection_id, connection_name,
                 time_window_start, time_window_end,
                 datetime.now().isoformat()),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def complete_scan_task(self, task_id: int, total_fetched: int, total_analyzed: int):
        """完成扫描任务"""
        conn = _get_connection()
        try:
            conn.execute(
                """UPDATE scan_tasks
                   SET total_fetched = ?, total_analyzed = ?, status = 'completed'
                   WHERE id = ?""",
                (total_fetched, total_analyzed, task_id),
            )
            conn.commit()
        finally:
            conn.close()

    def get_scan_tasks(self, limit: int = 50, offset: int = 0) -> dict:
        """获取扫描任务列表"""
        conn = _get_connection()
        try:
            total = conn.execute("SELECT COUNT(*) as cnt FROM scan_tasks").fetchone()["cnt"]
            rows = conn.execute(
                """SELECT * FROM scan_tasks ORDER BY created_at DESC LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
            return {"items": [dict(r) for r in rows], "total": total}
        finally:
            conn.close()

    def get_scan_task_detail(self, task_id: int) -> Optional[dict]:
        """获取扫描任务详情，含统计摘要"""
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM scan_tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if not row:
                return None
            task = dict(row)
            # 统计该任务下的慢SQL分布
            stats = {}
            for r in conn.execute(
                """SELECT severity, COUNT(*) as cnt
                   FROM slow_queries WHERE scan_task_id = ?
                   GROUP BY severity""",
                (task_id,),
            ).fetchall():
                stats[r["severity"]] = r["cnt"]
            task["severity_stats"] = stats
            return task
        finally:
            conn.close()

    def get_db_names(self) -> list[str]:
        """获取所有慢SQL记录中出现的数据库名列表（用于筛选下拉框）"""
        conn = _get_connection()
        try:
            rows = conn.execute(
                "SELECT DISTINCT db_name FROM slow_queries WHERE db_name != '' ORDER BY db_name"
            ).fetchall()
            return [r["db_name"] for r in rows]
        finally:
            conn.close()

    def get_set_ids(self) -> list[str]:
        """获取所有慢SQL记录中出现的 SET ID 列表（用于筛选下拉框）"""
        conn = _get_connection()
        try:
            rows = conn.execute(
                "SELECT DISTINCT set_id FROM slow_queries WHERE set_id != '' ORDER BY set_id"
            ).fetchall()
            return [r["set_id"] for r in rows]
        finally:
            conn.close()

    def get_cross_set_analysis(self, scan_task_id: int = None) -> dict:
        """跨 SET 对比分析

        分析维度:
        1. 各 SET 的慢 SQL 分布（总量/严重程度）
        2. 热点 SET 识别（慢 SQL 远超平均水平的 SET）
        3. 跨 SET 共现 SQL（在多个 SET 上都出现的慢 SQL）
        4. 顾问建议
        """
        conn = _get_connection()
        try:
            # 1. 各 SET 分布
            query = """
                SELECT set_id,
                       COUNT(*) as total,
                       SUM(CASE WHEN severity = 'ERROR' THEN 1 ELSE 0 END) as error_count,
                       SUM(CASE WHEN severity = 'WARNING' THEN 1 ELSE 0 END) as warning_count,
                       ROUND(AVG(avg_time_ms), 2) as avg_time_ms,
                       MAX(max_time_ms) as max_time_ms
                FROM slow_queries
                WHERE set_id != ''
            """
            params = []
            if scan_task_id:
                query += " AND scan_task_id = ?"
                params.append(scan_task_id)
            query += " GROUP BY set_id ORDER BY total DESC"

            set_rows = conn.execute(query, params).fetchall()
            set_distribution = {row["set_id"]: dict(row) for row in set_rows}

            if not set_distribution:
                return {
                    "set_distribution": {},
                    "hot_sets": [],
                    "cross_set_sqls": [],
                    "advice": "未发现带 SET 标识的慢SQL记录，可能扫描的是非分布式实例。",
                }

            # 2. 热点 SET 识别
            totals = [s["total"] for s in set_distribution.values()]
            avg_total = sum(totals) / len(totals) if totals else 0
            hot_sets = [
                {"set_id": sid, "total": s["total"], "ratio": round(s["total"] / avg_total, 2) if avg_total else 0}
                for sid, s in set_distribution.items()
                if s["total"] > avg_total * 1.5
            ]

            # 3. 跨 SET 共现 SQL（相同指纹在多个 SET 上出现）
            co_query = """
                SELECT fingerprint,
                       COUNT(DISTINCT set_id) as set_count,
                       GROUP_CONCAT(DISTINCT set_id) as sets,
                       SUM(exec_count) as total_exec,
                       MAX(max_time_ms) as max_time
                FROM slow_queries
                WHERE set_id != ''
            """
            if scan_task_id:
                co_query += " AND scan_task_id = ?"
            co_query += " GROUP BY fingerprint HAVING set_count > 1 ORDER BY total_exec DESC LIMIT 20"

            co_rows = conn.execute(co_query, params).fetchall()
            cross_set_sqls = [dict(r) for r in co_rows]

            # 4. 顾问建议
            advice_parts = []
            if hot_sets:
                hot_names = ", ".join(f"{h['set_id']}({h['total']}条)" for h in hot_sets)
                advice_parts.append(
                    f"热点 SET: {hot_names} 的慢SQL数量远超平均水平({avg_total:.0f}条)，"
                    f"建议检查该SET的数据分布是否倾斜、是否存在热点表"
                )
            if cross_set_sqls:
                advice_parts.append(
                    f"发现{len(cross_set_sqls)}个SQL在多个SET上同时出现慢查询，"
                    f"这些SQL可能是全局性问题（如缺少索引、全表扫描），建议优先优化"
                )
            if not advice_parts:
                advice_parts.append("各SET的慢SQL分布较为均匀，未发现明显的SET热点")

            return {
                "set_distribution": set_distribution,
                "hot_sets": hot_sets,
                "cross_set_sqls": cross_set_sqls,
                "advice": "；".join(advice_parts),
            }
        finally:
            conn.close()
