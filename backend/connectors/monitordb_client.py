"""
TDSQL 监控库 (monitordb) 客户端
"""
from backend.connectors.connection_pool import ConnectionPool

class MonitorDBClient(ConnectionPool):
    def __init__(self, host: str, port: int = 15001, user: str = "", password: str = "", database: str = "tdsqlpcloud_monitor"):
        super().__init__(host, port, user, password, database)

    def fetch_monitor_metrics(self, instance_name: str, limit: int = 50) -> list[dict]:
        """从 15001 端口读取 CPU/内存/并发/延迟物理监控指标"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM tdsql_instance_stat
                WHERE instance_name = %s
                ORDER BY collect_time DESC LIMIT %s
            """, (instance_name, limit))
            return cursor.fetchall()
