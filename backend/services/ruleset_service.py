"""
TDSQL SQL审核工具 - 规则集服务 (V2.0)

多租户规则管理：不同项目/团队/环境可绑定不同规则集，
按规则集覆盖规则的启停状态与严重级别。

- 内置 default 规则集（空覆盖 = 全部规则按默认配置执行）
- 项目通过 projects.rule_set_id 绑定规则集
- 审核时通过 get_overrides() 获取生效覆盖，传给 RuleChecker
"""
import logging
from typing import Optional

from backend.services.database import _get_connection, ensure_db, log_operation

logger = logging.getLogger("tdsql.ruleset")

VALID_SEVERITIES = ("ERROR", "WARNING", "INFO")


class RulesetService:
    """规则集管理服务"""

    def list_rulesets(self) -> list[dict]:
        """规则集列表（含条目数统计）"""
        ensure_db()
        conn = _get_connection()
        try:
            rows = conn.execute("""
                SELECT rs.*, COUNT(rsi.rule_id) AS item_count
                FROM rule_sets rs
                LEFT JOIN rule_set_items rsi ON rsi.rule_set_id = rs.id
                GROUP BY rs.id ORDER BY rs.created_at
            """).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_ruleset(self, rule_set_id: str) -> Optional[dict]:
        """获取规则集详情（含条目）"""
        ensure_db()
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM rule_sets WHERE id = ?", (rule_set_id,)).fetchone()
            if not row:
                return None
            result = dict(row)
            items = conn.execute(
                "SELECT rule_id, enabled, severity_override FROM rule_set_items "
                "WHERE rule_set_id = ? ORDER BY rule_id", (rule_set_id,)).fetchall()
            result["items"] = [dict(i) for i in items]
            return result
        finally:
            conn.close()

    def create_ruleset(self, rule_set_id: str, name: str, description: str = "",
                       items: Optional[list[dict]] = None,
                       operator: str = "") -> tuple[Optional[dict], Optional[str]]:
        """创建规则集。items: [{rule_id, enabled, severity_override}]"""
        if not rule_set_id or not rule_set_id.replace("_", "").replace("-", "").isalnum():
            return None, "规则集ID只能包含字母、数字、下划线和连字符"
        err = self._validate_items(items or [])
        if err:
            return None, err
        ensure_db()
        conn = _get_connection()
        try:
            exists = conn.execute(
                "SELECT 1 FROM rule_sets WHERE id = ?", (rule_set_id,)).fetchone()
            if exists:
                return None, f"规则集已存在: {rule_set_id}"
            conn.execute(
                "INSERT INTO rule_sets(id, name, description, is_builtin, created_by) "
                "VALUES (?, ?, ?, 0, ?)", (rule_set_id, name, description, operator))
            for item in (items or []):
                conn.execute(
                    "INSERT INTO rule_set_items(rule_set_id, rule_id, enabled, severity_override) "
                    "VALUES (?, ?, ?, ?)",
                    (rule_set_id, item["rule_id"],
                     1 if item.get("enabled", True) else 0,
                     item.get("severity_override")))
            conn.commit()
            log_operation(operator, "create_ruleset", "rule_set", rule_set_id)
            return self.get_ruleset(rule_set_id), None
        finally:
            conn.close()

    def update_ruleset(self, rule_set_id: str, name: Optional[str] = None,
                       description: Optional[str] = None,
                       items: Optional[list[dict]] = None,
                       operator: str = "") -> Optional[str]:
        """更新规则集（items 传入时全量替换条目）"""
        ensure_db()
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM rule_sets WHERE id = ?", (rule_set_id,)).fetchone()
            if not row:
                return "规则集不存在"
            if row["is_builtin"] and items is not None:
                return "内置规则集不允许修改条目，请创建自定义规则集"
            if items is not None:
                err = self._validate_items(items)
                if err:
                    return err
            if name is not None or description is not None:
                conn.execute(
                    "UPDATE rule_sets SET name = COALESCE(?, name), "
                    "description = COALESCE(?, description), "
                    "updated_at = NOW() WHERE id = ?",
                    (name, description, rule_set_id))
            if items is not None:
                conn.execute(
                    "DELETE FROM rule_set_items WHERE rule_set_id = ?", (rule_set_id,))
                for item in items:
                    conn.execute(
                        "INSERT INTO rule_set_items(rule_set_id, rule_id, enabled, severity_override) "
                        "VALUES (?, ?, ?, ?)",
                        (rule_set_id, item["rule_id"],
                         1 if item.get("enabled", True) else 0,
                         item.get("severity_override")))
            conn.commit()
            log_operation(operator, "update_ruleset", "rule_set", rule_set_id)
            return None
        finally:
            conn.close()

    def delete_ruleset(self, rule_set_id: str, operator: str = "") -> Optional[str]:
        """删除规则集（内置不可删；被项目引用时禁止删除）"""
        ensure_db()
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM rule_sets WHERE id = ?", (rule_set_id,)).fetchone()
            if not row:
                return "规则集不存在"
            if row["is_builtin"]:
                return "内置规则集不允许删除"
            ref = conn.execute(
                "SELECT project_id FROM projects WHERE rule_set_id = ? LIMIT 1",
                (rule_set_id,)).fetchone()
            if ref:
                return f"规则集正被项目 {ref['project_id']} 引用，请先解绑"
            conn.execute("DELETE FROM rule_set_items WHERE rule_set_id = ?", (rule_set_id,))
            conn.execute("DELETE FROM rule_sets WHERE id = ?", (rule_set_id,))
            conn.commit()
            log_operation(operator, "delete_ruleset", "rule_set", rule_set_id)
            return None
        finally:
            conn.close()

    def get_overrides(self, rule_set_id: Optional[str]) -> Optional[dict]:
        """
        获取规则集的生效覆盖。

        Returns:
            {rule_id: {"enabled": bool, "severity_override": str|None}}，
            规则集不存在或为 default（无条目）时返回 None（= 全默认）
        """
        if not rule_set_id or rule_set_id == "default":
            return None
        ensure_db()
        conn = _get_connection()
        try:
            rows = conn.execute(
                "SELECT rule_id, enabled, severity_override FROM rule_set_items "
                "WHERE rule_set_id = ?", (rule_set_id,)).fetchall()
            if not rows:
                return None
            return {
                r["rule_id"]: {
                    "enabled": bool(r["enabled"]),
                    "severity_override": r["severity_override"],
                }
                for r in rows
            }
        finally:
            conn.close()

    def get_overrides_for_project(self, project_id: Optional[str]) -> Optional[dict]:
        """按项目解析规则集覆盖（project → rule_set_id → overrides）"""
        if not project_id:
            return None
        ensure_db()
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT rule_set_id FROM projects WHERE project_id = ?",
                (project_id,)).fetchone()
            if not row:
                return None
            return self.get_overrides(row["rule_set_id"])
        finally:
            conn.close()

    @staticmethod
    def _validate_items(items: list[dict]) -> Optional[str]:
        from backend.engine.rules import ALL_RULE_CLASSES
        valid_ids = {cls.rule_id for cls in ALL_RULE_CLASSES}
        for item in items:
            rid = item.get("rule_id", "")
            if rid not in valid_ids:
                return f"未知规则ID: {rid}"
            sev = item.get("severity_override")
            if sev and sev not in VALID_SEVERITIES:
                return f"非法严重级别: {sev}（可选: {', '.join(VALID_SEVERITIES)}）"
        return None


# 全局单例
ruleset_service = RulesetService()
