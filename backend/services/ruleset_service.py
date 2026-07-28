"""
TDSQL SQL审核工具 - 规则集服务 (V2.0)

多租户规则管理：不同项目/团队/环境可绑定不同规则集，
按规则集覆盖规则的启停状态与严重级别。

- 内置 default 规则集（空覆盖 = 全部规则按默认配置执行）
- 项目通过 projects.rule_set_id 绑定规则集
- 审核时通过 get_overrides() 获取生效覆盖，传给 RuleChecker
"""
import logging
import threading
import time
from typing import Optional

from backend.services.database import _get_connection, ensure_db, log_operation

logger = logging.getLogger("tdsql.ruleset")

VALID_SEVERITIES = ("ERROR", "WARNING", "INFO")

# ── V1.4 全局生效规则集的进程内缓存 ──
# 生产以 --workers 2 运行（deploy/tdsql-sqlcheck.service:13），进程内缓存在
# 多 worker 下不会互相失效，因此本缓存的实际语义是：
#   切换规则集后，最长 _ACTIVE_CACHE_TTL 秒全量生效。
# 对外表述一律按此口径，不得写成"即时生效"（v1.3.3 会话吊销已有同类教训）。
# 逐条审核都查库属浪费，而追求即时生效需跨进程失效通知，代价与收益不匹配。
_ACTIVE_CACHE_TTL = 30.0
_active_cache: dict = {"at": 0.0, "rule_set_id": None, "overrides": None}
_active_cache_lock = threading.Lock()

# 兜底规则集 ID（database.py 已 INSERT IGNORE 保证其存在）
DEFAULT_RULE_SET_ID = "default"
ACTIVE_CONFIG_KEY = "active_rule_set_id"


def invalidate_active_cache() -> None:
    """清空本进程的生效规则集缓存。

    仅对当前 worker 生效——其它 worker 仍最长 30 秒后自然过期。
    这是有意为之的取舍，见 _ACTIVE_CACHE_TTL 处的说明。
    """
    with _active_cache_lock:
        _active_cache.update({"at": 0.0, "rule_set_id": None, "overrides": None})


class RulesetService:
    """规则集管理服务"""

    def list_rulesets(self) -> list[dict]:
        """规则集列表（含条目数统计与 is_active 派生值）。

        V1.4：is_active 为读取时与全局 active_rule_set_id 比对得出的派生值，
        不落库（《DB-v1.4》§2.4）。
        """
        ensure_db()
        conn = _get_connection()
        try:
            from backend.engine.checker import RuleChecker
            total_rules_count = len(RuleChecker().get_rules_info())
            active_id = self.get_active_rule_set_id()

            rows = conn.execute("""
                SELECT rs.*,
                       COUNT(CASE WHEN rsi.enabled = 0 THEN 1 END) AS disabled_count,
                       COUNT(rsi.rule_id) AS total_items
                FROM rule_sets rs
                LEFT JOIN rule_set_items rsi ON rsi.rule_set_id = rs.id
                GROUP BY rs.id ORDER BY rs.created_at
            """).fetchall()

            result = []
            for r in rows:
                item = dict(r)
                if item.get("is_builtin") or item.get("id") == "default":
                    item["item_count"] = total_rules_count
                else:
                    dis = item.get("disabled_count", 0)
                    item["item_count"] = max(0, total_rules_count - dis)
                item["is_active"] = (item.get("id") == active_id)
                result.append(item)
            return result
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
            # V1.4：条目变更后失效缓存，否则最长 30 秒内仍按旧配置审核
            invalidate_active_cache()
            return None
        finally:
            conn.close()

    def delete_ruleset(self, rule_set_id: str, operator: str = "") -> Optional[dict]:
        """删除规则集。

        V1.4 拦截（《API-v1.4》§2.4）：
          - 内置规则集不可删 → E5004 / 409
          - 正在全局生效的规则集不可删 → E5003 / 409
        返回 None=成功；否则返回 {"message", "code", "status"} 供 API 层映射状态码。
        """
        ensure_db()
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM rule_sets WHERE id = ?", (rule_set_id,)).fetchone()
            if not row:
                return {"message": f"规则集不存在: {rule_set_id}", "code": "E5002", "status": 404}
            if row["is_builtin"]:
                return {"message": "内置规则集不可删除", "code": "E5004", "status": 409}
            if rule_set_id == self.get_active_rule_set_id():
                return {"message": "该规则集正在全局生效中，请先切换到其它规则集再删除",
                        "code": "E5003", "status": 409}
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
        """DEPRECATED(V1.4)：规则集已改为全局启用，项目不再决定尺度。

        保留仅为兼容存量调用；新代码一律用 get_active_overrides()。
        为避免"看似按项目生效、实则不然"的误解，本方法直接返回全局结果，
        而不是继续按项目解析——否则会出现双轨，正是本次要消灭的问题。
        """
        return self.get_active_overrides()[1]

    # ── V1.4 全局生效规则集解析（核心） ──

    def get_active_rule_set_id(self) -> str:
        """解析当前全局生效的规则集 ID（带兜底链）。

        兜底顺序（《ARCHITECTURE-v1.4》§3.2）：
            system_config.active_rule_set_id
              ↓ 键不存在 / 值为空 / 指向的规则集已被删除
            'default'
        任何异常均回落 'default'——尺度解析不允许抛异常打断审核主流程。
        """
        try:
            ensure_db()
            conn = _get_connection()
            try:
                row = conn.execute(
                    "SELECT config_value FROM system_config WHERE config_key = ?",
                    (ACTIVE_CONFIG_KEY,)).fetchone()
                rid = (dict(row).get("config_value") or "").strip() if row else ""
                if not rid:
                    return DEFAULT_RULE_SET_ID
                # 指向的规则集必须仍然存在，否则回落
                exists = conn.execute(
                    "SELECT 1 FROM rule_sets WHERE id = ?", (rid,)).fetchone()
                return rid if exists else DEFAULT_RULE_SET_ID
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"解析全局规则集失败，回落默认规则集: {e}")
            return DEFAULT_RULE_SET_ID

    def get_active_overrides(self) -> tuple[str, Optional[dict]]:
        """返回 (生效规则集ID, 规则覆盖字典)。带 30 秒进程内缓存。

        返回二元组而不只返回 overrides：调用方需要把规则集 ID 落库
        （audit_history / scan_snapshots / gate_audit_logs），
        若只返回 overrides，调用方要再查一次才知道用的是哪把尺。
        """
        now = time.time()
        with _active_cache_lock:
            if now - _active_cache["at"] < _ACTIVE_CACHE_TTL \
                    and _active_cache["rule_set_id"] is not None:
                return _active_cache["rule_set_id"], _active_cache["overrides"]

        rid = self.get_active_rule_set_id()
        overrides = self.get_overrides(rid)

        with _active_cache_lock:
            _active_cache.update({"at": now, "rule_set_id": rid, "overrides": overrides})
        return rid, overrides

    def get_active_detail(self) -> dict:
        """当前生效规则集的详情（供 GET /rulesets/active）。"""
        from backend.engine.checker import RuleChecker
        rid, overrides = self.get_active_overrides()
        info = self.get_ruleset(rid) or {}
        items = info.get("items", [])
        total_rules = len(RuleChecker().get_rules_info())
        disabled = sum(1 for i in items if not i.get("enabled", True))
        return {
            "rule_set_id": rid,
            "name": info.get("name", rid),
            "is_builtin": bool(info.get("is_builtin")) or rid == DEFAULT_RULE_SET_ID,
            "rule_count": total_rules,
            "overridden_count": len(items),
            "disabled_count": disabled,
            "effective_rule_count": max(0, total_rules - disabled),
            "activated_at": info.get("updated_at") or info.get("created_at") or "",
            "cache_ttl_seconds": int(_ACTIVE_CACHE_TTL),
        }

    def set_active_rule_set(self, rule_set_id: str, operator: str = "") -> Optional[dict]:
        """切换全局生效规则集。返回 None=成功；否则 {"message","code","status"}。"""
        if not rule_set_id:
            return {"message": "必须指定规则集ID", "code": "E5001", "status": 400}
        ensure_db()
        conn = _get_connection()
        try:
            exists = conn.execute(
                "SELECT id FROM rule_sets WHERE id = ?", (rule_set_id,)).fetchone()
            if not exists:
                return {"message": f"规则集不存在: {rule_set_id}", "code": "E5002", "status": 404}
            conn.execute("""
                INSERT INTO system_config(config_key, config_value) VALUES (?, ?)
                ON DUPLICATE KEY UPDATE config_value = VALUES(config_value)
            """, (ACTIVE_CONFIG_KEY, rule_set_id))
            conn.commit()
        finally:
            conn.close()
        invalidate_active_cache()
        log_operation(operator or "system", "set_active_rule_set",
                      "rule_set", rule_set_id, f"全局生效规则集切换为 {rule_set_id}")
        return None

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
