"""ZooKeeper 自动发现配置的安全持久化服务（V1.6.0.1）。

认证口令只在服务端加密后入库，任何读取接口均不返回明文或密文。
"""
from __future__ import annotations

import json
from typing import Any

from backend.services.database import _get_connection
from backend.services.security_service import decrypt_password, encrypt_password


class ZKDiscoveryConfigError(ValueError):
    """管理员提交或已保存的 ZK 发现配置无效。"""


class ZKDiscoveryConfigService:
    """管理唯一一份 ZK 自动发现运行配置。"""

    CONFIG_ID = 1
    _DRIVERS = {"kazoo", "shell"}
    _PROXY_MODES = {"first", "random"}

    @staticmethod
    def _endpoint_map(value: Any) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ZKDiscoveryConfigError("内外网地址映射必须为对象")
        result: dict[str, str] = {}
        for source, target in value.items():
            source_text = str(source).strip()
            target_text = str(target).strip()
            if not source_text or not target_text:
                raise ZKDiscoveryConfigError("内外网地址映射的源地址和目标地址均不能为空")
            if any(ch in source_text + target_text for ch in "\r\n,;"):
                raise ZKDiscoveryConfigError("内外网地址映射格式无效")
            result[source_text] = target_text
        return result

    @staticmethod
    def _servers(value: Any) -> str:
        servers = ",".join(part.strip() for part in str(value or "").split(",") if part.strip())
        if not servers:
            raise ZKDiscoveryConfigError("请至少填写一个 ZooKeeper 服务地址")
        for endpoint in servers.split(","):
            host, sep, port_text = endpoint.rpartition(":")
            if not sep or not host.strip() or not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
                raise ZKDiscoveryConfigError("ZooKeeper 服务地址须为 主机:端口，多个地址以逗号分隔")
        return servers

    def _row(self) -> dict | None:
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM zk_discovery_config WHERE config_id = ?", (self.CONFIG_ID,)
            ).fetchone()
            return dict(row) if row else None
        except Exception as exc:
            raise ZKDiscoveryConfigError("ZK 发现配置表不可用，请确认系统已完成数据库迁移") from exc
        finally:
            conn.close()

    def load_runtime_config(self) -> dict | None:
        """返回运行时需要的配置；仅在服务端解密认证口令。"""
        row = self._row()
        if not row:
            return None
        try:
            endpoint_map = self._endpoint_map(json.loads(row["endpoint_map_json"] or "{}"))
            servers = self._servers(row["servers"])
            root_path = str(row["root_path"] or "").strip()
            driver = str(row["driver"] or "").strip().lower()
            proxy_mode = str(row["proxy_mode"] or "").strip().lower()
            default_database = str(row["default_database"] or "").strip() or "ALL"
            auth_username = str(row["auth_username"] or "").strip()
            auth_password = decrypt_password(str(row["auth_password_encrypted"] or ""))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ZKDiscoveryConfigError("已保存的 ZK 发现配置格式无效，请由管理员重新保存") from exc
        if not root_path.startswith("/"):
            raise ZKDiscoveryConfigError("已保存的 ZK 根路径无效，请由管理员重新保存")
        if driver not in self._DRIVERS:
            raise ZKDiscoveryConfigError("已保存的 ZK 发现驱动无效，请由管理员重新保存")
        if proxy_mode not in self._PROXY_MODES:
            raise ZKDiscoveryConfigError("已保存的 Proxy 选择方式无效，请由管理员重新保存")
        if not auth_username or not auth_password:
            raise ZKDiscoveryConfigError("已保存的 ZK 认证信息不可用，请由管理员重新保存")
        return {
            "force_mock": False,
            "servers": servers,
            "auth_user": auth_username,
            "auth_password": auth_password,
            "root": root_path,
            "driver": driver,
            "zkcli_path": str(row["zkcli_path"] or "").strip(),
            "default_database": default_database,
            "proxy_mode": proxy_mode,
            "endpoint_map": endpoint_map,
            "source": "database",
        }

    def public_config(self) -> dict | None:
        """返回给浏览器的脱敏配置。"""
        row = self._row()
        if not row:
            return None
        try:
            endpoint_map = self._endpoint_map(json.loads(row["endpoint_map_json"] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ZKDiscoveryConfigError("已保存的 ZK 发现配置格式无效，请重新保存") from exc
        updated_at = row.get("updated_at")
        if hasattr(updated_at, "isoformat"):
            updated_at = updated_at.isoformat(sep=" ")
        elif updated_at is not None:
            updated_at = str(updated_at)
        else:
            updated_at = ""
        return {
            "configured": True,
            "source": "database",
            "servers": str(row["servers"] or ""),
            "root_path": str(row["root_path"] or "/tdsqlzk"),
            "driver": str(row["driver"] or "kazoo"),
            "zkcli_path": str(row["zkcli_path"] or ""),
            "proxy_mode": str(row["proxy_mode"] or "first"),
            "default_database": str(row["default_database"] or "ALL"),
            "endpoint_map": endpoint_map,
            "auth_username": str(row["auth_username"] or ""),
            "password_configured": bool(row["auth_password_encrypted"]),
            "updated_by": str(row["updated_by"] or ""),
            "updated_at": updated_at,
        }

    def save(self, data: dict, operator: str) -> dict:
        """校验、加密并原子保存管理员提交的配置。空口令保留既有密文。"""
        current = self._row()
        servers = self._servers(data.get("servers"))
        root_path = str(data.get("root_path") or "").strip()
        if not root_path.startswith("/"):
            raise ZKDiscoveryConfigError("ZK 根路径必须以 / 开头")
        if len(root_path) > 512:
            raise ZKDiscoveryConfigError("ZK 根路径不能超过 512 个字符")
        driver = str(data.get("driver") or "kazoo").strip().lower()
        if driver not in self._DRIVERS:
            raise ZKDiscoveryConfigError("ZK 发现驱动仅支持 kazoo 或 shell")
        proxy_mode = str(data.get("proxy_mode") or "first").strip().lower()
        if proxy_mode not in self._PROXY_MODES:
            raise ZKDiscoveryConfigError("Proxy 选择方式仅支持 first 或 random")
        default_database = str(data.get("default_database") or "ALL").strip() or "ALL"
        if len(default_database) > 128:
            raise ZKDiscoveryConfigError("默认数据库不能超过 128 个字符")
        auth_username = str(data.get("auth_username") or "").strip()
        if not auth_username or len(auth_username) > 128:
            raise ZKDiscoveryConfigError("请填写有效的 ZK 认证用户名")
        endpoint_map = self._endpoint_map(data.get("endpoint_map", {}))
        supplied_password = str(data.get("auth_password") or "")
        if supplied_password:
            password_encrypted = encrypt_password(supplied_password)
        elif current and current.get("auth_password_encrypted"):
            password_encrypted = str(current["auth_password_encrypted"])
        else:
            raise ZKDiscoveryConfigError("首次保存请填写 ZK 认证口令")
        zkcli_path = str(data.get("zkcli_path") or "").strip()
        if driver == "shell" and not zkcli_path:
            raise ZKDiscoveryConfigError("选择 shell 驱动时请填写 zkCli.sh 路径")

        conn = _get_connection()
        try:
            conn.execute(
                """
                INSERT INTO zk_discovery_config (
                    config_id, servers, root_path, driver, zkcli_path, proxy_mode,
                    default_database, endpoint_map_json, auth_username,
                    auth_password_encrypted, updated_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON DUPLICATE KEY UPDATE
                    servers = VALUES(servers), root_path = VALUES(root_path),
                    driver = VALUES(driver), zkcli_path = VALUES(zkcli_path),
                    proxy_mode = VALUES(proxy_mode), default_database = VALUES(default_database),
                    endpoint_map_json = VALUES(endpoint_map_json), auth_username = VALUES(auth_username),
                    auth_password_encrypted = VALUES(auth_password_encrypted), updated_by = VALUES(updated_by)
                """,
                (
                    self.CONFIG_ID, servers, root_path, driver, zkcli_path, proxy_mode,
                    default_database, json.dumps(endpoint_map, ensure_ascii=False, sort_keys=True),
                    auth_username, password_encrypted, str(operator or "")[:64],
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return self.public_config() or {}


zk_discovery_config_service = ZKDiscoveryConfigService()
