"""
TDSQL SQL审核工具 - 慢SQL服务层

提供慢SQL的管理、分析和优化建议服务。
"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

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
        _ensure_db()
        self.analyzer = SlowSQLAnalyzer()

    def add_slow_query(self, record: SlowQueryRecord) -> dict:
        """
        添加慢SQL记录并自动分析。

        Args:
            record: 慢SQL记录

        Returns:
            包含分析结果的字典
        """
        # 执行分析
        report = self.analyzer.analyze_slow_query(record)

        conn = _get_connection()
        try:
            now = datetime.now().isoformat()
            cursor = conn.execute("""
                INSERT INTO slow_queries (
                    fingerprint, sql_text, db_name, exec_count,
                    total_time_ms, avg_time_ms, max_time_ms,
                    rows_examined, rows_sent, lock_time_ms,
                    first_seen, last_seen, problem_type, severity,
                    root_cause, suggestion, optimized_sql,
                    analysis_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record.fingerprint, record.sql_text, record.db_name,
                record.exec_count, record.total_time_ms, record.avg_time_ms,
                record.max_time_ms, record.rows_examined, record.rows_sent,
                record.lock_time_ms, now, now,
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
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """获取慢SQL列表"""
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
