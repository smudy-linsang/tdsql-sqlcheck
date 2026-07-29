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
    conflict: bool = False                 # 多个可用源结论不一致
    declared: Optional[InstanceType] = None
    detected: Optional[InstanceType] = None
    zk: Optional[InstanceType] = None            # V1.5.1 ZK 管控面判定
    locked: Optional[InstanceType] = None        # V1.5.1 管理员锁定值


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

        优先级（V1.5.1 多源分级）：
          A类（有 connection_id）：锁定 > ZK > 探测/声明保守合并 —— 忽略 requested
          B类（无 connection_id）：requested > 全局默认    —— 由调用方声明

        INV-2：A 类下 requested 被有意忽略。若允许调用方指定，
        只要传 instance_type=distributed，集中式实例就又会跑出 R077，
        可靠性保证即被绕过。管理员锁定不是绕过——它是实例级、持久化、
        可审计的配置，不是每次调用传参。
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

    _KIND_TO_TYPE = {"noshard": InstanceType.CENTRALIZED,
                     "groupshard": InstanceType.DISTRIBUTED}

    def _resolve_by_connection(self, connection_id: str) -> InstanceContext:
        """多源分级解析（V1.5.1）。

        优先级：S0 管理员锁定 > S1 ZK 管控面 > S2 SQL探测 > S3 人工声明

        除 S0 外，各源之间采用【取更保守者】合并：任一可用源判定为分布式，
        即按分布式执行。理由是两个方向的误判后果严重不对称——
        判成分布式只会多报（可见、可纠正），判成集中式会静默跳过 27 条规则
        （不可见、放行风险）。V1.5 的"探测一律优先"正是在此翻车。
        """
        now = time.time()
        with _cache_lock:
            hit = _cache.get(connection_id)
            if hit and now - hit[0] < _PROBE_CACHE_TTL:
                return hit[2]

        from backend.services.connection_registry import registry
        saved = registry.get_saved(connection_id) or {}

        # ── S3 人工声明（永远有值：is_distributed INT DEFAULT 1）──
        declared = (InstanceType.DISTRIBUTED
                    if int(saved.get("is_distributed", 1) or 0) == 1
                    else InstanceType.CENTRALIZED)

        # ── S0 管理员锁定：终审，直接返回，不参与保守合并 ──
        locked = None
        if int(saved.get("instance_type_locked", 0) or 0) == 1:
            raw = (saved.get("instance_type_locked_value") or "").strip()
            if raw in (InstanceType.DISTRIBUTED.value, InstanceType.CENTRALIZED.value):
                locked = InstanceType(raw)
        if locked is not None:
            ctx = InstanceContext(locked, TypeSource.LOCKED,
                                  declared=declared, locked=locked)
            with _cache_lock:
                _cache[connection_id] = (now, connection_id, ctx)
            return ctx

        # ── S1 ZK 管控面（落库缓存，扫描链路不实时访问 ZK）──
        zk = self._KIND_TO_TYPE.get((saved.get("zk_instance_kind") or "").strip())

        # ── S2 SQL 探测（判据表驱动，仅读落库值，不在解析链路上发起探测）──
        detected = None
        raw = saved.get("detected_instance_type")
        if raw in (InstanceType.DISTRIBUTED.value, InstanceType.CENTRALIZED.value):
            detected = InstanceType(raw)

        # ── 保守合并（candidates 顺序即优先级，不得重排）──
        candidates = [(TypeSource.ZK, zk), (TypeSource.PROBED, detected),
                      (TypeSource.DECLARED, declared)]
        available = [(s, v) for s, v in candidates if v is not None]

        # 任一源说分布式 → 分布式（保守）
        dist = [(s, v) for s, v in available if v == InstanceType.DISTRIBUTED]
        if dist:
            src, val = dist[0]              # 取优先级最高的那个作为 source 标注
        else:
            src, val = available[0]         # 全部为集中式，取最高优先级源

        conflict = len({v for _, v in available}) > 1
        ctx = InstanceContext(val, src, conflict=conflict,
                              declared=declared, detected=detected, zk=zk)

        if conflict:
            logger.warning(
                f"实例 {connection_id} 类型判定存在分歧："
                f"ZK={zk.value if zk else '无'}，"
                f"探测={detected.value if detected else '无'}，"
                f"声明={declared.value}。按保守原则采用 {val.value}"
                f"（来源 {src.value}）。")

        with _cache_lock:
            _cache[connection_id] = (now, connection_id, ctx)
        return ctx

    def _probe_and_persist(self, connection_id: str) -> tuple:
        """执行探测并落库。返回 (InstanceType 或 None, 探针明细)，绝不抛异常（INV-5）。"""
        from datetime import datetime
        from backend.services.connection_registry import registry
        from backend.services.database import _get_connection

        result, detail, err = None, {}, ""
        try:
            pool = registry.get(connection_id)
            result, detail = pool.probe_instance_type()
            if result is None:
                # V1.5.1：无结论不是失败。取判据层给出的中性描述，
                # 避免在实例详情里显示成"探测失败"。
                if isinstance(detail, dict) and detail.get("reason"):
                    err = str(detail["reason"])[:500]
                else:
                    err = "本次探测全部判据无结论，判定下沉至 ZK 管控面/人工声明"
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

        return (InstanceType(result) if result else None), detail

    def probe_now(self, connection_id: str) -> dict:
        """手动探测并返回多源判定明细（V1.5.1，供 POST /connections/{id}/probe-instance-type）。

        不再只跑 SQL 探针，而是依次尝试全部判定源并逐源返回
        available / value / reason —— 本次事故直接催生的设计：
        V1.5 只回一个 probe_detail，无法看出"哪个源投了票、
        哪个源根本没参与"，"探针恒真"因此潜伏了整整一个版本。
        """
        from backend.services.connection_registry import registry

        # 强制重新探测（S2）并落库，再按多源分级解析最终结论
        _, probe_detail = self._probe_and_persist(connection_id)
        self.invalidate(connection_id)

        saved = registry.get_saved(connection_id) or {}
        ctx = self._resolve_by_connection(connection_id)

        declared = ctx.declared or (
            InstanceType.DISTRIBUTED
            if int(saved.get("is_distributed", 1) or 0) == 1
            else InstanceType.CENTRALIZED)
        zk_kind = (saved.get("zk_instance_kind") or "").strip()
        detected = saved.get("detected_instance_type") or None
        if detected not in (InstanceType.DISTRIBUTED.value,
                            InstanceType.CENTRALIZED.value):
            detected = None

        probe_reason = ""
        if detected is None:
            probe_reason = (saved.get("instance_type_probe_error")
                            or "本次探测全部判据无结论")

        sources = {
            "locked": {
                "available": ctx.source == TypeSource.LOCKED,
                "value": ctx.locked.value if ctx.locked else None,
            },
            "zk": {
                "available": ctx.zk is not None or bool(zk_kind),
                "value": ctx.zk.value if ctx.zk else None,
                "kind": zk_kind or None,
                "reason": ("" if zk_kind else
                           "尚未执行 ZK 自动发现，或该实例未在 ZK 清单中匹配到"),
            },
            "probe": {
                "available": detected is not None,
                "value": detected,
                "reason": probe_reason,
                "detail": probe_detail,
            },
            "declared": {"available": True, "value": declared.value},
        }

        effective = ctx.instance_type
        msg = self._build_probe_message(ctx, declared)

        return {
            "connection_id": connection_id,
            "effective_instance_type": effective.value,
            "instance_type_source": ctx.source.value,
            "conflict": ctx.conflict,
            "sources": sources,
            # 兼容 V1.5 字段（前端旧逻辑/外部消费方）
            "detected_instance_type": detected,
            "declared_instance_type": declared.value,
            "detected_at": saved.get("instance_type_detected_at") or "",
            "message": msg,
        }

    @staticmethod
    def _build_probe_message(ctx: InstanceContext, declared: InstanceType) -> str:
        """面向使用者的结论 + 下一步建议（按最终 source 分支）"""
        eff_cn = _cn(ctx.instance_type)
        if ctx.source == TypeSource.LOCKED:
            return (f"该实例已由管理员锁定为「{eff_cn}」，锁定优先于一切自动判定源。"
                    f"如需恢复自动判定，请解除锁定。")
        if ctx.source == TypeSource.ZK:
            msg = f"ZK 管控面判定为「{eff_cn}」（权威源）。"
            if ctx.conflict:
                msg += (f"与其他来源存在分歧（声明为「{_cn(declared)}」），"
                        f"已按保守原则取「{eff_cn}」，请核实实例配置。")
            return msg
        if ctx.source == TypeSource.PROBED:
            msg = f"SQL 层探测判定为「{eff_cn}」（Proxy 层实测判据）。"
            if ctx.conflict:
                msg += (f"与声明「{_cn(declared)}」不一致，已按保守原则取「{eff_cn}」。"
                        f"如确认探测有误，请执行「ZK 自动发现」同步管控面数据，"
                        f"或由管理员锁定实例类型。")
            return msg
        # declared / default
        msg = f"本次采用实例配置中声明的「{_cn(declared)}」。"
        msg += ("SQL 层探测本次无结论；如需权威判定，请执行「ZK 自动发现」"
            "同步管控面数据，或由管理员锁定实例类型。")
        return msg

    def set_lock(self, connection_id: str, locked: bool,
                 instance_type: Optional[str] = None) -> None:
        """管理员锁定/解锁实例类型（V1.5.1）。

        解锁时保留 instance_type_locked_value，便于前端回显上次选择。
        """
        from backend.services.database import _get_connection, ensure_db
        if locked:
            if instance_type not in (InstanceType.DISTRIBUTED.value,
                                     InstanceType.CENTRALIZED.value):
                raise ValueError("instance_type 仅支持 distributed 或 centralized")
        ensure_db()
        conn = _get_connection()
        try:
            if locked:
                conn.execute(
                    "UPDATE tdsql_connections SET instance_type_locked = 1, "
                    "instance_type_locked_value = ? WHERE id = ?",
                    (instance_type, connection_id))
            else:
                conn.execute(
                    "UPDATE tdsql_connections SET instance_type_locked = 0 "
                    "WHERE id = ?", (connection_id,))
            conn.commit()
        finally:
            conn.close()
        self.invalidate(connection_id)

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
