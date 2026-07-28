"""
实例级质量门禁（V1.4）

门禁绑定对象由「项目」改为「实例」：同一把评估尺度（全局规则集）下，
不同实例可以有不同的放行标准——核心账务库与内部报表库本就不该一视同仁。

设计依据：docs/DETAIL-v1.4-全局规则集与实例门禁.md §5
"""
import logging
from typing import Optional

from backend.services.database import _get_connection, ensure_db, log_operation

logger = logging.getLogger(__name__)

# 未配置实例的兜底默认值（决策 2026-07-28：ERROR 上限 0、WARNING 上限 -1 不限）
# 该取值与 V1.3 的 gate_rules 默认值完全一致，因此门禁判定结论不发生任何变化，
# 存量实例无需迁移。切勿改成 0/0——那会让几乎所有实例立即门禁不通过。
DEFAULT_MAX_ERROR = 0
DEFAULT_MAX_WARNING = -1
DEFAULT_MODE = "enforce"
VALID_MODES = ("enforce", "observe")


class InstanceGateService:

    def get_rule(self, connection_id: str) -> dict:
        """取实例门禁配置；未配置返回系统默认。

        不预先为每个实例插行——预插会在新增实例时产生同步负担，
        漏插即行为不一致；兜底逻辑只需这一处。
        """
        fallback = {
            "connection_id": connection_id,
            "max_error_count": DEFAULT_MAX_ERROR,
            "max_warning_count": DEFAULT_MAX_WARNING,
            "mode": DEFAULT_MODE,
            "is_default": True,
        }
        if not connection_id:
            return fallback
        try:
            ensure_db()
            conn = _get_connection()
            try:
                row = conn.execute(
                    "SELECT connection_id, max_error_count, max_warning_count, mode, "
                    "description, updated_by, updated_at "
                    "FROM instance_gate_rules WHERE connection_id = ?",
                    (connection_id,)).fetchone()
                if not row:
                    return fallback
                d = dict(row)
                d["is_default"] = False
                return d
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"读取实例门禁配置失败，按系统默认判定: {e}")
            return fallback

    def save_rule(self, connection_id: str, max_error_count: int,
                  max_warning_count: int, mode: str = DEFAULT_MODE,
                  description: str = "", operator: str = "") -> Optional[dict]:
        """保存实例门禁配置。返回 None=成功；否则 {"message","code","status"}。"""
        if not connection_id:
            return {"message": "必须指定实例", "code": "E5011", "status": 400}
        if mode not in VALID_MODES:
            return {"message": f"非法判定模式: {mode}（仅 enforce / observe）",
                    "code": "E5014", "status": 400}
        for name, val in (("ERROR", max_error_count), ("WARNING", max_warning_count)):
            if val < -1:
                return {"message": f"{name} 上限非法：{val}（-1 表示不限，其余须 >= 0）",
                        "code": "E5013", "status": 400}

        ensure_db()
        conn = _get_connection()
        try:
            exists = conn.execute(
                "SELECT id FROM tdsql_connections WHERE id = ?",
                (connection_id,)).fetchone()
            if not exists:
                return {"message": f"实例不存在: {connection_id}", "code": "E5012", "status": 404}
            # 取旧值用于审计留痕
            old = conn.execute(
                "SELECT max_error_count, max_warning_count, mode FROM instance_gate_rules "
                "WHERE connection_id = ?", (connection_id,)).fetchone()
            conn.execute("""
                INSERT INTO instance_gate_rules
                    (connection_id, max_error_count, max_warning_count, mode,
                     description, updated_by)
                VALUES (?,?,?,?,?,?)
                ON DUPLICATE KEY UPDATE
                    max_error_count = VALUES(max_error_count),
                    max_warning_count = VALUES(max_warning_count),
                    mode = VALUES(mode),
                    description = VALUES(description),
                    updated_by = VALUES(updated_by)
            """, (connection_id, max_error_count, max_warning_count, mode,
                  description, operator))
            conn.commit()
        finally:
            conn.close()
        old_txt = (f"error<={old['max_error_count']};warning<={old['max_warning_count']};"
                   f"mode={old['mode']}" if old else "系统默认(0/-1/enforce)")
        log_operation(operator or "system", "set_instance_gate_rule",
                      "instance_gate_rule", connection_id,
                      f"{old_txt} -> error<={max_error_count};warning<={max_warning_count};mode={mode}")
        return None

    def delete_rule(self, connection_id: str, operator: str = "") -> Optional[dict]:
        """删除实例门禁配置（删除后回落系统默认）。返回 None=成功。"""
        if not connection_id:
            return {"message": "必须指定实例", "code": "E5011", "status": 400}
        ensure_db()
        conn = _get_connection()
        try:
            exists = conn.execute(
                "SELECT id FROM tdsql_connections WHERE id = ?",
                (connection_id,)).fetchone()
            if not exists:
                return {"message": f"实例不存在: {connection_id}", "code": "E5012", "status": 404}
            conn.execute("DELETE FROM instance_gate_rules WHERE connection_id = ?",
                         (connection_id,))
            conn.commit()
        finally:
            conn.close()
        log_operation(operator or "system", "delete_instance_gate_rule",
                      "instance_gate_rule", connection_id, "门禁配置已删除，回落系统默认")
        return None

    def list_rules(self) -> dict:
        """返回全部实例及其门禁配置（未配置实例返回系统默认并标记 is_default）。

        为什么返回全部实例而非仅已配置的：管理员需要看到"哪些实例还没配过"，
        只返回已配置的会让未配置实例隐形（《API-v1.4》§3.1）。
        """
        ensure_db()
        conn = _get_connection()
        try:
            rows = conn.execute("""
                SELECT c.id AS connection_id, c.name AS connection_name,
                       g.max_error_count, g.max_warning_count, g.mode,
                       g.description, g.updated_by, g.updated_at
                FROM tdsql_connections c
                LEFT JOIN instance_gate_rules g ON g.connection_id = c.id
                ORDER BY c.created_at
            """).fetchall()
            items = []
            for r in rows:
                d = dict(r)
                configured = d.get("max_error_count") is not None
                if not configured:
                    d["max_error_count"] = DEFAULT_MAX_ERROR
                    d["max_warning_count"] = DEFAULT_MAX_WARNING
                    d["mode"] = DEFAULT_MODE
                d["is_default"] = not configured
                items.append(d)
            return {
                "total": len(items),
                "default_rule": {
                    "max_error_count": DEFAULT_MAX_ERROR,
                    "max_warning_count": DEFAULT_MAX_WARNING,
                    "mode": DEFAULT_MODE,
                },
                "items": items,
            }
        finally:
            conn.close()


instance_gate_service = InstanceGateService()
