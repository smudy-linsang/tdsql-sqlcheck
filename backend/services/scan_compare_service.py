"""
扫描结果比对引擎

设计依据：docs/DETAIL-v1.3-扫描结果对比.md §6

核心语义：
  FIXED  基准有 ∧ 目标无 —— "改了多少"（慢SQL 文案为"已消失(未复现)"）
  NEW    基准无 ∧ 目标有 —— "有没有新增"
  REMAIN 两边都有         —— "还留有多少"
  CHANGED REMAIN 的子集，关键属性发生变化（汇总不重复计入）

比对方向恒按 scan_finished_at 自动定基准，与用户勾选顺序无关。
"""
import logging

from backend.services import scan_snapshot_service as snapshot_service

logger = logging.getLogger(__name__)

# CHANGED 判定阈值（可按需调整）
COMPARE_SLOW_DELTA_PCT = 30.0    # 慢SQL 平均耗时变化幅度
COMPARE_SIZE_DELTA_PCT = 30.0    # 大表体量增长幅度

_SEV_ORDER = {"ERROR": 0, "WARNING": 1, "INFO": 2}
_SEV_RANK = {"INFO": 0, "WARNING": 1, "ERROR": 2}

DEFAULT_DETAIL_LIMIT = 500
MAX_DETAIL_LIMIT = 2000

# 大表等级严重度排序（L3 严控级最重，L1 关注级最轻）
_LEVEL_RANK = {"": 0, "L1": 1, "L2": 2, "L3": 3}


class CompareError(Exception):
    """比对可比性校验失败。携带 HTTP 状态码与业务错误码。"""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _labels_for(module: str) -> dict:
    """分类文案按模块差异化。

    慢SQL 是时间窗口采样，"消失"不等于"已修复"，文案必须区分，
    否则报告会系统性高估整改成效。
    """
    if module == "slow_scan":
        return {"fixed": "已消失（未复现）", "new": "新出现慢SQL", "remain": "仍然存在"}
    return {"fixed": "已修复", "new": "新增问题", "remain": "遗留未整改"}


def _safe_issues(snap: dict) -> tuple[list, bool]:
    """取问题项数组；缺失/损坏/非列表时返回 ([], True) 触发降级（D1'）"""
    try:
        arr = snap.get("issues")
        if not isinstance(arr, list):
            return [], True
        return [i for i in arr if isinstance(i, dict) and i.get("key")], False
    except Exception:
        return [], True


def _parse_window_hours(start: str, end: str):
    """解析时间窗口时长（小时）；无法解析返回 None"""
    if not start or not end:
        return None
    from datetime import datetime
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            s = datetime.strptime(start[:19], fmt)
            e = datetime.strptime(end[:19], fmt)
            delta = (e - s).total_seconds() / 3600.0
            return delta if delta > 0 else None
        except ValueError:
            continue
    return None


def validate_pair(snapshot_ids, module: str = "") -> tuple[dict, dict, list]:
    """可比性校验（DETAIL §6.1）。

    返回 (base, target, warnings)；base/target 按 scan_finished_at 自动定序。
    校验不通过抛 CompareError。
    """
    ids = list(snapshot_ids or [])
    # 1. 必须恰好两个
    if len(ids) != 2:
        raise CompareError("E4001", "只能选择两次扫描结果进行对比")
    # 2. 不能与自身对比
    if str(ids[0]) == str(ids[1]):
        raise CompareError("E4002", "不能与自身对比，请选择两次不同的扫描结果")

    # 3. 两份快照均存在
    s1 = snapshot_service.get_snapshot(int(ids[0]), with_issues=True)
    s2 = snapshot_service.get_snapshot(int(ids[1]), with_issues=True)
    if not s1 or not s2:
        raise CompareError("E4004", "快照不存在或已被数据保留策略清理", status=404)

    # 4. 同模块
    if s1.get("module") != s2.get("module"):
        raise CompareError("E4003", "不同模块的扫描结果不可对比")
    if module and s1.get("module") != module:
        raise CompareError("E4003", "快照所属模块与请求模块不一致")

    # 5. 同实例（两侧均非空时才比较；存量回填数据 connection_id 可能为空）
    c1, c2 = s1.get("connection_id") or "", s2.get("connection_id") or ""
    if c1 and c2 and c1 != c2:
        raise CompareError("E4003", "不同实例的扫描结果不可对比")

    # 6. 同指纹算法版本
    if s1.get("fingerprint_algo") != s2.get("fingerprint_algo"):
        raise CompareError("E4005", "两次扫描的指纹算法版本不一致，无法可靠对比", status=409)

    # 7. 同评估尺度（V1.4，修复既有缺陷）
    # 尺度不同则"问题数变化"不可解释：规则集变了，事实没变，只是判断变了。
    # 此前 validate_pair 校验了模块/实例/指纹算法版本，唯独没有规则集，
    # 导致能拿两个不同尺度的快照算出看似权威的"整改率"。
    r1, r2 = s1.get("rule_set_id"), s2.get("rule_set_id")
    if r1 and r2 and r1 != r2:
        raise CompareError(
            "E4007",
            f"两次扫描的评估尺度不同（{r1} vs {r2}），问题数变化不可比，已拒绝对比",
            status=409)

    # 基准判定：时间早的为 base，与勾选顺序无关
    base, target = sorted([s1, s2], key=lambda s: (s.get("scan_finished_at") or "", s.get("id")))

    warnings = []
    # 7b. 存量快照宽容处理：任一尺度为 NULL（V1.4 前产生）不拒绝，仅警告。
    # 若一律拒绝，全部存量快照立即不可对比，等于废掉 V1.3 刚交付的能力。
    if not r1 or not r2:
        warnings.append(
            "其中一次扫描产生于 V1.4 之前，评估尺度未知，整改率仅供参考")
    # 7. 慢SQL 时间窗口一致性（不拦截，仅提示）
    if base.get("module") == "slow_scan":
        h1 = _parse_window_hours(base.get("time_window_start"), base.get("time_window_end"))
        h2 = _parse_window_hours(target.get("time_window_start"), target.get("time_window_end"))
        if h1 and h2:
            ratio = h1 / h2 if h2 else 0
            if ratio > 2 or ratio < 0.5:
                warnings.append(
                    f"两次扫描的时间窗口长度差异较大（{h1:.1f}h vs {h2:.1f}h），整改率仅供参考")

    # 8. 截断提示（不拦截）
    if base.get("truncated"):
        warnings.append(
            f"基准快照问题项过多已被截断（截掉 {base.get('truncated_count', 0)} 条），对比结果可能不完整")
    if target.get("truncated"):
        warnings.append(
            f"目标快照问题项过多已被截断（截掉 {target.get('truncated_count', 0)} 条），对比结果可能不完整")

    # 回填数据缺实例信息提示
    if (base.get("source_kind") == "rebuild" or target.get("source_kind") == "rebuild") \
            and not (c1 and c2):
        warnings.append("历史回填数据缺少实例信息，请确认对比对象一致")

    return base, target, warnings


def _pct_change(old: float, new: float):
    """变化百分比；old 为 0 时无法计算返回 None"""
    if not old:
        return None
    return (new - old) / abs(old) * 100.0


def detect_change(module: str, ob: dict, ot: dict):
    """CHANGED 判定（DETAIL §6.3）。返回 change 字典或 None。

    同时命中多条时取优先级最高一条（SEVERITY > PERF/GROWTH），其余入 others。
    """
    changes = []

    # 全模块：严重级别变化
    sb = (ob.get("severity") or "").upper()
    st = (ot.get("severity") or "").upper()
    if sb and st and sb != st:
        direction = "UP" if _SEV_RANK.get(st, 0) > _SEV_RANK.get(sb, 0) else "DOWN"
        changes.append({"type": "SEVERITY", "field": "severity",
                        "old": sb, "new": st, "direction": direction})

    ab = ob.get("attrs") or {}
    at = ot.get("attrs") or {}

    if module == "slow_scan":
        old_ms = float(ab.get("avg_time_ms") or 0)
        new_ms = float(at.get("avg_time_ms") or 0)
        pct = _pct_change(old_ms, new_ms)
        if pct is not None and abs(pct) >= COMPARE_SLOW_DELTA_PCT:
            changes.append({"type": "PERF", "field": "avg_time_ms",
                            "old": round(old_ms, 1), "new": round(new_ms, 1),
                            "pct": round(pct, 1),
                            "direction": "UP" if pct > 0 else "DOWN"})

    elif module == "bigtable":
        lb = (ab.get("level") or "").upper()
        lt = (at.get("level") or "").upper()
        if lb != lt:
            changes.append({"type": "GROWTH", "field": "level",
                            "old": lb, "new": lt,
                            "direction": "UP" if _LEVEL_RANK.get(lt, 0) > _LEVEL_RANK.get(lb, 0) else "DOWN"})
        old_gb = float(ab.get("size_gb") or 0)
        new_gb = float(at.get("size_gb") or 0)
        pct = _pct_change(old_gb, new_gb)
        if pct is not None and abs(pct) >= COMPARE_SIZE_DELTA_PCT:
            changes.append({"type": "GROWTH", "field": "size_gb",
                            "old": round(old_gb, 2), "new": round(new_gb, 2),
                            "pct": round(pct, 1),
                            "direction": "UP" if pct > 0 else "DOWN"})

    if not changes:
        return None
    # 优先级：SEVERITY 最高
    changes.sort(key=lambda c: 0 if c["type"] == "SEVERITY" else 1)
    primary = dict(changes[0])
    if len(changes) > 1:
        primary["others"] = changes[1:]
    return primary


def compare(s_base: dict, s_target: dict) -> dict:
    """核心比对（DETAIL §6.2）。O(n+m) 哈希 join。"""
    # 【D1' 降级保护】任一快照明细缺失/损坏时不抛异常，退化为全量差集并打标
    b_arr, b_bad = _safe_issues(s_base)
    t_arr, t_bad = _safe_issues(s_target)
    degraded = b_bad or t_bad

    b_issues = {i["key"]: i for i in b_arr}
    t_issues = {i["key"]: i for i in t_arr}
    b_keys, t_keys = set(b_issues), set(t_issues)

    fixed_keys = b_keys - t_keys
    new_keys = t_keys - b_keys
    remain_keys = b_keys & t_keys

    module = s_base.get("module") or ""
    fixed = [b_issues[k] for k in fixed_keys]
    new = [t_issues[k] for k in new_keys]
    remain, changed = [], []
    for k in remain_keys:
        ob, ot = b_issues[k], t_issues[k]
        ch = detect_change(module, ob, ot)
        item = dict(ot)
        if ch:
            item["change"] = ch
            changed.append(item)
        remain.append(item)

    base_total, target_total = len(b_keys), len(t_keys)
    fix_rate = round(len(fixed) / base_total * 100, 1) if base_total else 0.0

    for arr in (fixed, new, remain, changed):
        arr.sort(key=lambda i: (_SEV_ORDER.get((i.get("severity") or "").upper(), 9),
                                i.get("object_name") or ""))

    return {
        "summary": {
            "base_total": base_total,
            "target_total": target_total,
            "fixed_count": len(fixed),
            "new_count": len(new),
            "remain_count": len(remain),
            "changed_count": len(changed),
            "fix_rate": fix_rate,
            "delta": target_total - base_total,
            "degraded": degraded,
            "by_severity": {
                "base": (s_base.get("stats") or {}).get("by_severity", {}),
                "target": (s_target.get("stats") or {}).get("by_severity", {}),
            },
        },
        "fixed": fixed, "new": new, "remain": remain, "changed": changed,
        "labels": _labels_for(module),
    }


def _snap_brief(snap: dict) -> dict:
    return {
        "id": snap.get("id"),
        "scan_finished_at": snap.get("scan_finished_at"),
        "scan_label": snap.get("scan_label"),
        "issue_total": snap.get("issue_total"),
        "truncated": snap.get("truncated"),
        "truncated_count": snap.get("truncated_count", 0),
    }


def run_compare(snapshot_ids, module: str = "", include_details: bool = True,
                detail_limit: int = DEFAULT_DETAIL_LIMIT) -> dict:
    """完整比对流程：校验 → 比对 → 组装响应（含明细截断）"""
    base, target, warnings = validate_pair(snapshot_ids, module)
    result = compare(base, target)

    if result["summary"]["degraded"]:
        warnings = list(warnings) + ["部分快照明细缺失或损坏，已退化为全量差集，结果仅供参考"]

    resp = {
        "module": base.get("module"),
        "base": _snap_brief(base),
        "target": _snap_brief(target),
        "connection_id": base.get("connection_id") or target.get("connection_id") or "",
        "connection_name": base.get("connection_name") or target.get("connection_name") or "",
        "db_name": base.get("db_name") or target.get("db_name") or "",
        # V1.4：评估尺度（两快照同尺度时取 base；NULL 表示 V1.4 前尺度未知）
        "rule_set_id": base.get("rule_set_id") or target.get("rule_set_id") or "",
        "rule_set_name": base.get("rule_set_name") or target.get("rule_set_name") or "",
        "labels": result["labels"],
        "warnings": warnings,
        "summary": result["summary"],
    }

    if not include_details:
        return resp

    limit = max(1, min(int(detail_limit or DEFAULT_DETAIL_LIMIT), MAX_DETAIL_LIMIT))
    truncated_flags = {}
    for kind in ("fixed", "new", "remain", "changed"):
        arr = result[kind]
        truncated_flags[kind] = len(arr) > limit
        resp[kind] = arr[:limit]
    resp["detail_truncated"] = truncated_flags
    return resp
