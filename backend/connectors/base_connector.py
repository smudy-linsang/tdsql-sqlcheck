"""
连接器基类
"""
import logging

logger = logging.getLogger("tdsql.connector")

class ConnectorError(Exception):
    """连接器基类异常"""
    pass

class BaseConnector:
    def __init__(self, host: str, port: int, user: str, password: str, database: str = ""):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
