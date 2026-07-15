"""
TDSQL SQL审核工具 - 连接注册表 (V2.0)

替代V1.0的全局单连接模型，支持数百个TDSQL实例并存：

- connection_id → 连接池 的注册表（线程安全）
- 连接配置持久化到 SQLite tdsql_connections 表（密码Fernet加密）
- LRU淘汰 + 空闲回收，防止长期占用目标库连接
- 按连接的扫描并发信号量 + 全局扫描信号量（保护目标库和本服务）
- 替换同ID连接时关闭旧池，杜绝连接泄漏

保留 "adhoc" 特殊连接ID用于兼容V1.0的 /connect 即席连接语义。
"""
import logging
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Optional

from backend import config
from backend.services.database import _get_connection, ensure_db
from backend.services.security_service import decrypt_password, encrypt_password
from backend.services.tdsql_connector import (
    TDSQLConnectionConfig, TDSQLConnectionPool,
)

logger = logging.getLogger("tdsql.registry")

ADHOC_ID = "adhoc"


class ScanBusyError(RuntimeError):
    """扫描并发超限"""


class ConnectionNotFoundError(LookupError):
    """连接不存在或未激活"""


class _PoolEntry:
    __slots__ = ("pool", "last_used", "created_at")

    def __init__(self, pool: TDSQLConnectionPool):
        self.pool = pool
        self.last_used = time.time()
        self.created_at = time.time()


class ConnectionRegistry:
    """多实例连接注册表"""

    def __init__(self):
        self._pools: dict[str, _PoolEntry] = {}
        self._lock = threading.RLock()
        self._scan_semaphores: dict[str, threading.Semaphore] = {}
        self._global_scan_semaphore: Optional[threading.Semaphore] = None
        self._global_scan_limit = 0

    # ══════════════════════════════════════════════════════
    # 连接池生命周期
    # ══════════════════════════════════════════════════════

    def register(self, conn_id: str, cfg: TDSQLConnectionConfig,
                 validate: bool = True) -> TDSQLConnectionPool:
        """
        注册/替换一个活跃连接。

        Args:
            conn_id: 连接ID（保存的连接配置ID，或 "adhoc"）
            cfg: 连接配置
            validate: 是否立即执行 SELECT 1 验证连接可用性

        Raises:
            ConnectionError: 验证失败
        """
        pool = TDSQLConnectionPool(cfg)
        if validate:
            with pool.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    cursor.fetchone()

        with self._lock:
            old = self._pools.pop(conn_id, None)
            if old:
                try:
                    old.pool.close_all()
                except Exception:
                    pass
            self._pools[conn_id] = _PoolEntry(pool)
            self._evict_locked()
        logger.info("连接已注册: %s → %s:%s/%s (活跃连接数=%d)",
                    conn_id, cfg.host, cfg.port, cfg.database, len(self._pools))
        return pool

    def get(self, conn_id: Optional[str] = None,
            auto_connect: bool = True) -> TDSQLConnectionPool:
        """
        获取连接池。

        conn_id 为空时: 优先返回 adhoc 即席连接（V1.0兼容），
        其次自动连接标记为默认的已保存连接。

        conn_id 非空时: 返回活跃池；不活跃且 auto_connect=True 时
        从已保存配置自动建连。

        Raises:
            ConnectionNotFoundError: 无可用连接
        """
        with self._lock:
            if conn_id:
                entry = self._pools.get(conn_id)
                if entry:
                    entry.last_used = time.time()
                    return entry.pool
            else:
                entry = self._pools.get(ADHOC_ID)
                if entry:
                    entry.last_used = time.time()
                    return entry.pool

        if not auto_connect:
            raise ConnectionNotFoundError(conn_id or "(default)")

        # 尝试从已保存配置自动建连
        saved = self.get_saved(conn_id) if conn_id else self.get_default_saved()
        if not saved:
            raise ConnectionNotFoundError(conn_id or "(default)")
        mon_pwd_enc = saved.get("monitor_password_encrypted", "") or ""
        cfg = TDSQLConnectionConfig(
            host=saved["host"], port=saved["port"], user=saved["username"],
            password=decrypt_password(saved["password_encrypted"]),
            database=saved["database"] or "", charset=saved["charset"] or "utf8mb4",
            set_list=saved.get("set_list", "") or "",
            monitor_host=saved.get("monitor_host", "") or "",
            monitor_port=int(saved.get("monitor_port") or 15001),
            monitor_user=saved.get("monitor_user", "") or "",
            monitor_password=decrypt_password(mon_pwd_enc) if mon_pwd_enc else "",
            monitor_db=saved.get("monitor_db", "") or "tdsqlpcloud_monitor",
        )
        pool = self.register(saved["id"], cfg)
        self._mark_connected(saved["id"])
        return pool

    def disconnect(self, conn_id: Optional[str] = None) -> int:
        """断开指定连接；conn_id为空时断开全部。返回断开数量。"""
        with self._lock:
            if conn_id:
                entry = self._pools.pop(conn_id, None)
                targets = [entry] if entry else []
            else:
                targets = list(self._pools.values())
                self._pools.clear()
        for entry in targets:
            try:
                entry.pool.close_all()
            except Exception:
                pass
        return len(targets)

    def list_active(self) -> list[dict]:
        """活跃连接列表"""
        with self._lock:
            return [
                {
                    "connection_id": cid,
                    "host": e.pool.config.host,
                    "port": e.pool.config.port,
                    "database": e.pool.config.database,
                    "user": e.pool.config.user,
                    "last_used": time.strftime(
                        "%Y-%m-%d %H:%M:%S", time.localtime(e.last_used)),
                    "idle_seconds": int(time.time() - e.last_used),
                }
                for cid, e in self._pools.items()
            ]

    def active_count(self) -> int:
        with self._lock:
            return len(self._pools)

    def is_active(self, conn_id: str) -> bool:
        with self._lock:
            return conn_id in self._pools

    def _evict_locked(self):
        """LRU淘汰 + 空闲回收（调用方需持锁）"""
        idle_limit = config.connection_pool_idle_seconds()
        now = time.time()
        # 空闲回收
        expired = [cid for cid, e in self._pools.items()
                   if now - e.last_used > idle_limit and cid != ADHOC_ID]
        for cid in expired:
            entry = self._pools.pop(cid)
            try:
                entry.pool.close_all()
            except Exception:
                pass
            logger.info("空闲连接已回收: %s", cid)
        # LRU淘汰
        max_size = config.connection_pool_max_instances()
        while len(self._pools) > max_size:
            lru_id = min(self._pools, key=lambda c: self._pools[c].last_used)
            entry = self._pools.pop(lru_id)
            try:
                entry.pool.close_all()
            except Exception:
                pass
            logger.warning("连接数超限(%d)，LRU淘汰: %s", max_size, lru_id)

    # ══════════════════════════════════════════════════════
    # 扫描并发保护
    # ══════════════════════════════════════════════════════

    @contextmanager
    def scan_slot(self, conn_id: str):
        """
        获取扫描槽位（按连接 + 全局双重限流）。

        Raises:
            ScanBusyError: 并发超限
        """
        per_conn_limit = config.max_concurrent_scans_per_connection()
        global_limit = config.max_concurrent_scans_global()
        with self._lock:
            if self._global_scan_semaphore is None or self._global_scan_limit != global_limit:
                self._global_scan_semaphore = threading.Semaphore(global_limit)
                self._global_scan_limit = global_limit
            if conn_id not in self._scan_semaphores:
                self._scan_semaphores[conn_id] = threading.Semaphore(per_conn_limit)
            sem = self._scan_semaphores[conn_id]
            gsem = self._global_scan_semaphore

        if not gsem.acquire(blocking=False):
            raise ScanBusyError(f"服务扫描并发已达上限({global_limit})，请稍后重试")
        if not sem.acquire(blocking=False):
            gsem.release()
            raise ScanBusyError(
                f"目标库 {conn_id} 扫描并发已达上限({per_conn_limit})，请稍后重试")
        try:
            yield
        finally:
            sem.release()
            gsem.release()

    # ══════════════════════════════════════════════════════
    # 连接配置持久化（SQLite T08，密码加密）
    # ══════════════════════════════════════════════════════

    def save_connection(self, name: str, host: str, port: int, username: str,
                        password: str, database: str = "", charset: str = "utf8mb4",
                        is_default: bool = False, is_distributed: bool = True,
                        description: str = "", conn_id: str = "",
                        operator: str = "", set_list: str = "",
                        monitor_host: str = "", monitor_port: int = 15001,
                        monitor_user: str = "", monitor_password: str = "",
                        monitor_db: str = "tdsqlpcloud_monitor") -> str:
        """保存连接配置（密码加密存储），返回连接ID"""
        ensure_db()
        conn = _get_connection()
        try:
            existing = None
            if conn_id:
                existing = conn.execute(
                    "SELECT password_encrypted, monitor_password_encrypted FROM tdsql_connections WHERE id = ?",
                    (conn_id,)).fetchone()
            else:
                # 同 host:port:database 视为同一连接，获取已有连接以更新
                row = conn.execute(
                    "SELECT id, password_encrypted, monitor_password_encrypted FROM tdsql_connections WHERE host=? AND port=? AND `database`=?",
                    (host, port, database)).fetchone()
                if row:
                    conn_id = row["id"]
                    existing = row

            if not conn_id:
                conn_id = uuid.uuid4().hex[:8]

            # 密码特殊处理：如果传入密码为空且存在旧密码，则复用旧密码；否则重新加密
            if not password and existing and existing.get("password_encrypted"):
                pwd_enc = existing["password_encrypted"]
            else:
                pwd_enc = encrypt_password(password or "")

            # 监控库密码特殊处理：如果传入监控库密码为空且存在旧监控库密码，则复用；否则重新加密/空串
            if not monitor_password and existing and existing.get("monitor_password_encrypted"):
                mon_pwd_enc = existing["monitor_password_encrypted"]
            else:
                mon_pwd_enc = encrypt_password(monitor_password or "") if monitor_password else ""

            if is_default:
                conn.execute("UPDATE tdsql_connections SET is_default = 0")

            conn.execute("""
                INSERT INTO tdsql_connections
                    (id, name, host, port, username, password_encrypted, `database`,
                     charset, is_default, is_distributed, description, set_list,
                     monitor_host, monitor_port, monitor_user, monitor_password_encrypted, monitor_db,
                     status, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'disconnected', NOW())
                ON DUPLICATE KEY UPDATE
                    name=VALUES(name), host=VALUES(host), port=VALUES(port),
                    username=VALUES(username),
                    password_encrypted=VALUES(password_encrypted),
                    `database`=VALUES(`database`), charset=VALUES(charset),
                    is_default=VALUES(is_default),
                    is_distributed=VALUES(is_distributed),
                    description=VALUES(description), set_list=VALUES(set_list),
                    monitor_host=VALUES(monitor_host), monitor_port=VALUES(monitor_port),
                    monitor_user=VALUES(monitor_user),
                    monitor_password_encrypted=VALUES(monitor_password_encrypted),
                    monitor_db=VALUES(monitor_db),
                    updated_at=NOW()
            """, (conn_id, name or f"{host}:{port}", host, port, username,
                  pwd_enc, database, charset,
                  1 if is_default else 0, 1 if is_distributed else 0, description,
                  set_list or "",
                  monitor_host or "", int(monitor_port or 15001), monitor_user or "",
                  mon_pwd_enc, monitor_db or "tdsqlpcloud_monitor"))
            conn.commit()
            from backend.services.database import log_operation
            log_operation(operator, "save_connection", "tdsql_connection", conn_id,
                          f"{host}:{port}/{database}")
            return conn_id
        finally:
            conn.close()

    def get_saved(self, conn_id: str) -> Optional[dict]:
        """获取已保存的连接配置（含加密密码，仅供内部建连）"""
        ensure_db()
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM tdsql_connections WHERE id = ?", (conn_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_default_saved(self) -> Optional[dict]:
        """获取默认连接配置"""
        ensure_db()
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM tdsql_connections WHERE is_default = 1 LIMIT 1"
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def list_saved(self) -> list[dict]:
        """已保存连接列表（密码脱敏）"""
        ensure_db()
        conn = _get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM tdsql_connections ORDER BY created_at").fetchall()
            result = []
            with self._lock:
                active_ids = set(self._pools.keys())
            for r in rows:
                d = dict(r)
                d.pop("password_encrypted", None)
                d["password"] = "***"
                d["active"] = d["id"] in active_ids
                result.append(d)
            return result
        finally:
            conn.close()

    def delete_saved(self, conn_id: str, operator: str = "") -> bool:
        """删除已保存连接（同时断开活跃池）"""
        self.disconnect(conn_id)
        ensure_db()
        conn = _get_connection()
        try:
            cursor = conn.execute(
                "DELETE FROM tdsql_connections WHERE id = ?", (conn_id,))
            conn.commit()
            if cursor.rowcount > 0:
                from backend.services.database import log_operation
                log_operation(operator, "delete_connection", "tdsql_connection", conn_id)
                return True
            return False
        finally:
            conn.close()

    def set_default_saved(self, conn_id: str) -> bool:
        """设置默认连接"""
        ensure_db()
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT 1 FROM tdsql_connections WHERE id = ?", (conn_id,)).fetchone()
            if not row:
                return False
            conn.execute("UPDATE tdsql_connections SET is_default = 0")
            conn.execute(
                "UPDATE tdsql_connections SET is_default = 1, updated_at = NOW() "
                "WHERE id = ?", (conn_id,))
            conn.commit()
            return True
        finally:
            conn.close()

    def _mark_connected(self, conn_id: str):
        ensure_db()
        conn = _get_connection()
        try:
            conn.execute(
                "UPDATE tdsql_connections SET status = 'connected', "
                "last_connected_at = NOW() WHERE id = ?", (conn_id,))
            conn.commit()
        finally:
            conn.close()


# 全局单例
registry = ConnectionRegistry()
