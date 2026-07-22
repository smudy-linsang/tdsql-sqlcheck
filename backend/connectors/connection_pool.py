"""
数据库连接池 (ConnectionPool)
"""
from contextlib import contextmanager
import threading
import time
import pymysql
from backend.connectors.base_connector import BaseConnector, ConnectorError


class ConnectionPool(BaseConnector):
    def __init__(self, host: str, port: int, user: str, password: str, database: str = "", max_connections: int = 10):
        super().__init__(host, port, user, password, database)
        self.max_connections = max_connections
        self._lock = threading.Lock()
        self._active_connections = 0
        self.last_used = time.time()

    def _create_raw_connection(self):
        try:
            return pymysql.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                database=self.database,
                charset="utf8mb4",
                cursorclass=pymysql.cursors.DictCursor,
                connect_timeout=5,
                read_timeout=15
            )
        except Exception as e:
            raise ConnectorError(f"连接数据库 {self.host}:{self.port} 失败: {e}")

    @contextmanager
    def get_connection(self):
        self.last_used = time.time()
        conn = self._create_raw_connection()
        try:
            yield conn
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def close_all(self):
        """关闭所有连接资源"""
        pass
