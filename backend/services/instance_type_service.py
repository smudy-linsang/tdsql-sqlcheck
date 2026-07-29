"""实例类型解析服务（V1.5）

职责：把"这次扫描针对什么类型的实例"这个问题，收敛为一个确定的答案。

设计要点：对外永远返回确定的 InstanceType，不存在 unknown 态。
不确定性由 TypeSource 表达（"这个结论是探来的还是猜的"），
而不是由类型本身表达——否则引擎就要处理三态，而三态无论怎么处理都是错的：
跑全部=沿用误报，只跑通用=静默漏报。
"""
import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

from backend.models import InstanceType, TypeSource

logger = logging.getLogger("tdsql.instance_type")

_PROBE_CACHE_TTL = 300.0          # 秒。多 worker 下语义为"最长 5 分钟生效"
_DEFAULT_CACHE_TTL = 300.0
_cache: dict = {}                  # {connection_id: (at, connection_id, InstanceContext)}
_cache_lock = threading.Lock()
_default_cache = {"at": 0.0, "value": None}
_default_lock = threading.Lock()


@dataclass
class InstanceContext:
    """一次扫描的实例类型上下文，随调用链向下传递"""
    instance_type: InstanceType
    source: TypeSource
    conflict: bool = False                 # 探测与人工声明冲突
    declared: Optional[InstanceType] = None
    detected: Optional[InstanceType] = None


class InstanceTypeService:

    # ── 全局默认 ──────────────────────────────────────────

    def get_default_instance_type(self) -> InstanceType:
        """读 system_config.default_instance_type，带 300s 进程内缓存。

        任何异常一律回落 DISTRIBUTED —— 兜底方向必须偏向"跑全部规则"，
        宁可多报不可漏报（见 ARCHITECTURE §5.4）。
        """
        now = time.time()
        with _default_lock:
            if now - _default_cache["at"] < _DEFAULT_CACHE_TTL \
                    and _default_cache["value"] is not None:
                return _default_cache["value"]
        value = InstanceType.DISTRIBUTED
        try:
            from backend.services.database import _get_connection, ensure_db
            ensure_db()
            conn = _get_connection()
            try:
                row = conn.execute(
                    "SELECT config_value FROM system_config WHERE config_key = ?",
                    ("default_instance_type",)).fetchone()
                if row:
                    raw = (row["config_value"] if isinstance(row, dict) else row[0]) or ""
                    if raw == InstanceType.CENTRALIZED.value:
                        value = InstanceType.CENTRALIZED
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"读取全局默认实例类型失败(按分布式兜底): {e}")
        with _default_lock:
            _default_cache["at"] = now
            _default_cache["value"] = value
        return value

    def set_default_instance_type(self, value: str) -> None:
        from backend.services.database import _get_connection, ensure_db
        if value not in (InstanceType.DISTRIBUTED.value, InstanceType.CENTRALIZED.value):
            raise ValueError("default_instance_type 仅支持 distributed 或 centralized")
        ensure_db()
        conn = _get_connection()
        try:
            conn.execute(
                "REPLACE INTO system_config(config_key, config_value) VALUES(?, ?)",
                ("default_instance_type", value))
            conn.commit()
        finally:
            conn.close()
        with _default_lock:
            _default_cache["at"] = 0.0      # 本进程立即失效；其他 worker 最长 5 分钟

    # ── 核心解析 ──────────────────────────────────────────

    def resolve(self, connection_id: str = "",
                requested: Optional[str] = None) -> InstanceContext:
        """解析一次扫描的实例类型上下文。

        优先级：
          A类（有 connection_id）：探测 > 人工声明        —— 客观事实，忽略 requested
          B类（无 connection_id）：requested > 全局默认    —— 由调用方声明

        INV-2：A 类下 requested 被有意忽略。若允许调用方指定，
        只要传 instance_type=distributed，集中式实例就又会跑出 R077，
        可靠性保证即被绕过。
        """
        if connection_id:
            try:
                return self._resolve_by_connection(connection_id)
            except Exception as e:
                logger.warning(f"实例类型解析失败(回落全局默认): {connection_id}: {e}")
                return InstanceContext(self.get_default_instance_type(), TypeSource.DEFAULT)

        if requested in (InstanceType.DISTRIBUTED.value, InstanceType.CENTRALIZED.value):
            return InstanceContext(InstanceType(requested), TypeSource.REQUEST)
        return InstanceContext(self.get_default_instance_type(), TypeSource.DEFAULT)

    def _resolve_by_connection(self, connection_id: str) -> InstanceContext:
        now = time.time()
        with _cache_lock:
            hit = _cache.get(connection_id)
            if hit and now - hit[0] < _PROBE_CACHE_TTL:
                return hit[2]

        from backend.services.connection_registry import registry
        saved = registry.get_saved(connection_id) or {}

        declared = (InstanceType.DISTRIBUTED
                    if int(saved.get("is_distributed", 1) or 0) == 1
                    else InstanceType.CENTRALIZED)

        detected = None
        raw = saved.get("detected_instance_type")
        if raw in (InstanceType.DISTRIBUTED.value, InstanceType.CENTRALIZED.value):
            detected = InstanceType(raw)
        else:
            detected = self._probe_and_persist(connection_id)

        if detected is not None:
            ctx = InstanceContext(detected, TypeSource.PROBED,
                                  conflict=(detected != declared),
                                  declared=declared, detected=detected)
            if ctx.conflict:
                logger.warning(
                    f"实例 {connection_id} 类型冲突：声明={declared.value}，"
                    f"探测={detected.value}。审核按探测结果执行。")
        else:
            ctx = InstanceContext(declared, TypeSource.DECLARED, declared=declared)

        with _cache_lock:
            _cache[connection_id] = (now, connection_id, ctx)
        return ctx

    def _probe_and_persist(self, connection_id: str) -> Optional[InstanceType]:
        """执行探测并落库。探测失败返回 None，绝不抛异常（INV-5）。"""
        from datetime import datetime
        from backend.services.connection_registry import registry
        from backend.services.database import _get_connection

        result, detail, err = None, {}, ""
        try:
            pool = registry.get(connection_id)
            result, detail = pool.probe_instance_type()
            if result is None:
                err = str(detail)[:500]
        except Exception as e:
            err = str(e)[:500]
            logger.warning(f"实例 {connection_id} 类型探测失败: {e}")

        try:
            conn = _get_connection()
            try:
                if result:
                    conn.execute(
                        "UPDATE tdsql_connections SET detected_instance_type = ?, "
                        "instance_type_detected_at = ?, instance_type_probe_error = '' "
                        "WHERE id = ?",
                        (result, datetime.now().isoformat(), connection_id))
                else:
                    conn.execute(
                        "UPDATE tdsql_connections SET instance_type_probe_error = ? "
                        "WHERE id = ?", (err, connection_id))
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"探测结果落库失败: {e}")

        return InstanceType(result) if result else None

    def probe_now(self, connection_id: str) -> dict:
        """手动探测并返回完整结论（供 POST /connections/{id}/probe-instance-type）。

        强制重新探测（绕过缓存读取），刷新落库，并返回 API §3.1 的响应结构。
        """
        from backend.services.connection_registry import registry
        saved = registry.get_saved(connection_id) or {}
        declared = (InstanceType.DISTRIBUTED
                    if int(saved.get("is_distributed", 1) or 0) == 1
                    else InstanceType.CENTRALIZED)

        detected = self._probe_and_persist(connection_id)
        # 探测后失效缓存，下次解析读取最新落库值
        self.invalidate(connection_id)

        conflict = (detected is not None and detected != declared)
        effective = detected if detected is not None else declared
        source = TypeSource.PROBED if detected is not None else TypeSource.DECLARED

        if detected is not None and conflict:
            msg = (f"探测结论为「{_cn(detected)}」，与实例配置中声明的「{_cn(declared)}」不一致。"
                   f"审核将按探测结果（{_cn(detected)}）执行；如确认声明有误，请在实例编辑中修正实例类型。")
        elif detected is not None:
            msg = f"探测结论为「{_cn(detected)}」，与声明一致。"
        else:
            msg = f"本次探测无结论（可能网络不可达或权限不足），审核将退回声明值「{_cn(declared)}」。"

        return {
            "connection_id": connection_id,
            "detected_instance_type": detected.value if detected else None,
            "declared_instance_type": declared.value,
            "conflict": conflict,
            "effective_instance_type": effective.value,
            "instance_type_source": source.value,
            "detected_at": saved.get("instance_type_detected_at") or "",
            "message": msg,
        }

    def invalidate(self, connection_id: str = "") -> None:
        """实例配置变更后清缓存。本进程立即生效，其他 worker 最长 5 分钟。"""
        with _cache_lock:
            if connection_id:
                _cache.pop(connection_id, None)
            else:
                _cache.clear()


def _cn(t: InstanceType) -> str:
    return "分布式" if t == InstanceType.DISTRIBUTED else "集中式"


instance_type_service = InstanceTypeService()
