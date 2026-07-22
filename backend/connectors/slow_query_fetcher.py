"""
慢 SQL 日志与实时 Processlist 抓取器 (SlowQueryFetcher)
"""
class SlowQueryFetcher:
    def __init__(self, pool):
        self.pool = pool

    def fetch_active_processlist(self) -> list[dict]:
        """抓取当前活动的慢线程与挂起事务"""
        with self.pool.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SHOW FULL PROCESSLIST")
            rows = cursor.fetchall()
            return [r for r in rows if r.get("Command") != "Sleep" and r.get("Time", 0) > 0]
