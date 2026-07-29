"""
扫描快照服务 — 生成 / 查询 / 回填

设计依据：docs/DETAIL-v1.3-扫描结果对比.md §5

核心约束：
  - 快照生成为旁路，任何异常仅告警，绝不阻断扫描主流程（safe_create_snapshot）
  - 幂等：(module, biz_ref_id) 唯一约束 + ON DUPLICATE KEY UPDATE
  - 先去重再截断，否则重复项挤占截断名额导致 truncated_count 失真
"""
import json
import logging
from datetime import datetime

from backend.services.database import _get_connection, ensure_db
from backend.services.snapshot_extractors.base import IssueItem, FINGERPRINT_ALGO

logger = logging.getLogger(__name__)

# V1.5.2 新增 launch_check（上线检查）。三命名空间对应关系：
# 前端路由 schema-check ←→ inspection_type='schema_check' ←→ 快照 module='launch_check'
MODULES = ("schema_audit", "slow_scan", "bigtable", "launch_check")
SNAPSHOT_MAX_ISSUES = 20000
SNAPSHOT_SCHEMA_VERSION = 1

_SEV_ORDER = {"ERROR": 0, "WARNING": 1, "INFO": 2}

# 列表查询返回的列（不含 snapshot_json 大字段）
_LIST_COLUMNS = """
    id, module, biz_ref_id, connection_id, connection_name, db_name, scan_label,
    scan_started_at, scan_finished_at, time_window_start, time_window_end,
    object_total, issue_total, error_count, warning_count,
    fingerprint_algo, schema_version, truncated, truncated_count,
    snapshot_size, source_kind, created_by, created_at, rule_set_id, instance_type
"""


def _rule_set_name_map(ids) -> dict:
    """批量查规则集名称（V1.4：快照列表/对比响应需展示尺度名称）。"""
    ids = {i for i in (ids or []) if i}
    if not ids:
        return {}
    conn = _get_connection()
    try:
        ph = ",".join(["?"] * len(ids))
        rows = conn.execute(
            f"SELECT id, name FROM rule_sets WHERE id IN ({ph})", list(ids)).fetchall()
        return {r["id"]: r["name"] for r in rows}
    except Exception as e:
        logger.warning(f"查询规则集名称失败: {e}")
        return {}
    finally:
        conn.close()


def _fmt_dt(value) -> str:
    """统一时间为字符串；None 返回空串"""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _row_to_item(row) -> dict:
    d = dict(row)
    for k in ("scan_started_at", "scan_finished_at", "created_at"):
        d[k] = _fmt_dt(d.get(k))
    d["truncated"] = bool(d.get("truncated"))
    return d


def create_snapshot(module: str, meta: dict, issues: list,
                    object_total: int = 0, source_kind: str = "live"):
    """构建并落库快照，返回 snapshot_id。异常向上抛（由 safe_create_snapshot 吞掉）。"""
    if module not in MODULES:
        raise ValueError(f"不支持的模块: {module}")
    meta = meta or {}

    # 1) 指纹去重（同 key 只保留一条，防止抽取器产生重复）
    #    顺序要求：先去重再截断，否则重复项会挤占截断名额，导致 truncated_count 失真
    seen, uniq = set(), []
    for it in (issues or []):
        if it.key in seen:
            continue
        seen.add(it.key)
        uniq.append(it)

    # 2) 超限截断（按 ERROR > WARNING > INFO 保留）
    truncated, truncated_count = False, 0
    if len(uniq) > SNAPSHOT_MAX_ISSUES:
        uniq.sort(key=lambda i: _SEV_ORDER.get(i.severity, 9))
        truncated_count = len(uniq) - SNAPSHOT_MAX_ISSUES
        uniq = uniq[:SNAPSHOT_MAX_ISSUES]
        truncated = True
        logger.warning("快照问题项超限：module=%s biz_ref_id=%s 截断 %d 条",
                       module, meta.get("biz_ref_id"), truncated_count)

    # 3) 统计
    by_sev, by_type = {}, {}
    for it in uniq:
        by_sev[it.severity] = by_sev.get(it.severity, 0) + 1
        by_type[it.issue_type] = by_type.get(it.issue_type, 0) + 1

    payload = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "module": module,
        "fingerprint_algo": FINGERPRINT_ALGO,
        "truncated": truncated,
        "truncated_count": truncated_count,
        "meta": meta,
        "stats": {
            "object_total": object_total,
            "issue_total": len(uniq),
            "by_severity": by_sev,
            "by_issue_type": by_type,
        },
        "issues": [i.to_dict() for i in uniq],
    }
    blob = json.dumps(payload, ensure_ascii=False)

    finished = meta.get("scan_finished_at") or datetime.now().isoformat()

    # 4) 幂等落库
    ensure_db()
    conn = _get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO scan_snapshots
              (module, biz_ref_id, connection_id, connection_name, db_name, scan_label,
               scan_started_at, scan_finished_at, time_window_start, time_window_end,
               object_total, issue_total, error_count, warning_count,
               fingerprint_algo, schema_version, truncated, truncated_count,
               snapshot_json, snapshot_size, source_kind, created_by, rule_set_id,
               instance_type)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON DUPLICATE KEY UPDATE
               connection_id=VALUES(connection_id),
               connection_name=VALUES(connection_name),
               db_name=VALUES(db_name),
               scan_label=VALUES(scan_label),
               scan_started_at=VALUES(scan_started_at),
               scan_finished_at=VALUES(scan_finished_at),
               time_window_start=VALUES(time_window_start),
               time_window_end=VALUES(time_window_end),
               object_total=VALUES(object_total),
               issue_total=VALUES(issue_total),
               error_count=VALUES(error_count),
               warning_count=VALUES(warning_count),
               truncated=VALUES(truncated),
               truncated_count=VALUES(truncated_count),
               snapshot_json=VALUES(snapshot_json),
               snapshot_size=VALUES(snapshot_size),
               rule_set_id=VALUES(rule_set_id),
               instance_type=VALUES(instance_type)
        """, (
            module, str(meta.get("biz_ref_id", ""))[:64],
            meta.get("connection_id", "") or "",
            (meta.get("connection_name", "") or "")[:256],
            (meta.get("db_name", "") or "")[:128],
            (meta.get("scan_label", "") or "")[:500],
            meta.get("scan_started_at") or None,
            finished,
            (meta.get("time_window_start", "") or "")[:32],
            (meta.get("time_window_end", "") or "")[:32],
            object_total, len(uniq),
            by_sev.get("ERROR", 0), by_sev.get("WARNING", 0),
            FINGERPRINT_ALGO, SNAPSHOT_SCHEMA_VERSION,
            1 if truncated else 0, truncated_count,
            blob, len(blob), source_kind,
            (meta.get("created_by", "") or "")[:64],
            # V1.4：生成本快照时生效的规则集（对比时校验同尺度）；NULL=V1.4 前尺度未知
            (meta.get("rule_set_id", "") or None),
            # V1.5：采集时的实例类型口径（只留痕，本版本不参与对比校验）；NULL=V1.5 前快照
            (meta.get("instance_type", "") or None),
        ))
        conn.commit()
        snap_id = getattr(cur, "lastrowid", None)
        if not snap_id:
            row = conn.execute(
                "SELECT id FROM scan_snapshots WHERE module = ? AND biz_ref_id = ?",
                (module, str(meta.get("biz_ref_id", "")))).fetchone()
            snap_id = dict(row)["id"] if row else None
        return snap_id
    finally:
        conn.close()


def safe_create_snapshot(module: str, meta: dict, issues: list,
                         object_total: int = 0, source_kind: str = "live"):
    """create_snapshot 的吞异常包装。三模块挂载点统一调用此函数。

    快照生成失败绝不能阻断扫描主流程。
    """
    try:
        return create_snapshot(module, meta, issues, object_total, source_kind)
    except Exception as e:
        logger.warning("生成扫描快照失败 module=%s biz_ref_id=%s: %s",
                       module, (meta or {}).get("biz_ref_id"), e)
        return None


def list_snapshots(module: str = "", connection_id: str = "", db_name: str = "",
                   date_from: str = "", date_to: str = "",
                   limit: int = 20, offset: int = 0) -> dict:
    """快照列表查询（不含 snapshot_json）"""
    ensure_db()
    limit = max(1, min(int(limit or 20), 100))
    offset = max(0, int(offset or 0))

    where, args = [], []
    if module:
        where.append("module = ?")
        args.append(module)
    if connection_id == "__unknown__":
        where.append("(connection_id IS NULL OR connection_id = '')")
    elif connection_id:
        where.append("connection_id = ?")
        args.append(connection_id)
    if db_name:
        where.append("db_name = ?")
        args.append(db_name)
    if date_from:
        where.append("DATE(scan_finished_at) >= ?")
        args.append(date_from)
    if date_to:
        where.append("DATE(scan_finished_at) <= ?")
        args.append(date_to)
    cond = (" WHERE " + " AND ".join(where)) if where else ""

    conn = _get_connection()
    try:
        total_row = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM scan_snapshots{cond}", args).fetchone()
        total = dict(total_row)["cnt"] if total_row else 0
        rows = conn.execute(
            f"SELECT {_LIST_COLUMNS} FROM scan_snapshots{cond} "
            f"ORDER BY scan_finished_at DESC, id DESC LIMIT ? OFFSET ?",
            (*args, limit, offset)).fetchall()
        items = [_row_to_item(r) for r in rows or []]
    finally:
        conn.close()
    # V1.4：补充尺度名称（rule_set_id 为 NULL 的存量快照名称为空）
    name_map = _rule_set_name_map([it.get("rule_set_id") for it in items])
    for it in items:
        it["rule_set_name"] = name_map.get(it.get("rule_set_id"), "")
    return {"total": total, "items": items}


def get_snapshot(snapshot_id: int, with_issues: bool = True):
    """取单个快照。with_issues=True 时解析 snapshot_json 并合并 payload。"""
    ensure_db()
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM scan_snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return None

    item = _row_to_item(row)
    raw = item.pop("snapshot_json", None)
    # V1.4：补充尺度名称
    item["rule_set_name"] = _rule_set_name_map([item.get("rule_set_id")]).get(
        item.get("rule_set_id"), "")
    if not with_issues:
        return item

    payload = {}
    try:
        payload = json.loads(raw or "{}")
    except (ValueError, TypeError):
        logger.warning("快照 %s 的 snapshot_json 解析失败，将触发降级比对", snapshot_id)
        payload = {}

    item["meta"] = payload.get("meta", {})
    item["stats"] = payload.get("stats", {})
    # issues 缺失/损坏时保持为 None，由比对引擎 _safe_issues 识别并降级
    item["issues"] = payload.get("issues")
    return item


# ── 存量数据回填（DETAIL §10）──

def _rebuild_schema_audit(conn, limit: int, overwrite: bool) -> dict:
    from backend.services.snapshot_extractors.schema_audit import extract_from_json

    rows = conn.execute("""
        SELECT h.id, h.source, h.results_json, h.created_by, h.created_at,
               h.connection_id, h.db_name, COALESCE(c.name, '') AS connection_name
        FROM audit_history h
        LEFT JOIN tdsql_connections c ON c.id = h.connection_id
        WHERE h.audit_type = ?
        ORDER BY h.id DESC LIMIT ?
    """, ("extracted_schema", limit)).fetchall()

    stat = {"scanned": 0, "created": 0, "skipped": 0, "failed": 0}
    for r in rows or []:
        d = dict(r)
        stat["scanned"] += 1
        if not overwrite and _snapshot_exists(conn, "schema_audit", str(d["id"])):
            stat["skipped"] += 1
            continue
        try:
            db_name = d.get("db_name") or ""
            items, obj_total = extract_from_json(d.get("results_json"), db_name)
            created_at = _fmt_dt(d.get("created_at"))
            create_snapshot("schema_audit", {
                "biz_ref_id": str(d["id"]),
                "connection_id": d.get("connection_id") or "",
                "connection_name": d.get("connection_name") or "",
                "db_name": db_name,
                "node": "",
                "scan_label": d.get("source") or "",
                "scan_started_at": None,
                "scan_finished_at": created_at,
                "created_by": d.get("created_by") or "",
            }, items, obj_total, source_kind="rebuild")
            stat["created"] += 1
        except Exception as e:
            logger.warning("回填 schema_audit 快照失败 id=%s: %s", d.get("id"), e)
            stat["failed"] += 1
    return stat


def _rebuild_slow_scan(conn, limit: int, overwrite: bool) -> dict:
    from backend.services.snapshot_extractors.slow_scan import extract as slow_extract

    rows = conn.execute("""
        SELECT id, task_name, connection_id, connection_name, db_name,
               time_window_start, time_window_end, created_by, created_at
        FROM scan_tasks ORDER BY id DESC LIMIT ?
    """, (limit,)).fetchall()

    stat = {"scanned": 0, "created": 0, "skipped": 0, "failed": 0}
    for r in rows or []:
        d = dict(r)
        stat["scanned"] += 1
        if not overwrite and _snapshot_exists(conn, "slow_scan", str(d["id"])):
            stat["skipped"] += 1
            continue
        try:
            items, obj_total = slow_extract(int(d["id"]), d.get("db_name") or "")
            create_snapshot("slow_scan", {
                "biz_ref_id": str(d["id"]),
                "connection_id": d.get("connection_id") or "",
                "connection_name": d.get("connection_name") or "",
                "db_name": d.get("db_name") or "",
                "scan_label": d.get("task_name") or "",
                "scan_started_at": None,
                "scan_finished_at": _fmt_dt(d.get("created_at")),
                "time_window_start": d.get("time_window_start") or "",
                "time_window_end": d.get("time_window_end") or "",
                "created_by": d.get("created_by") or "",
            }, items, obj_total, source_kind="rebuild")
            stat["created"] += 1
        except Exception as e:
            logger.warning("回填 slow_scan 快照失败 task_id=%s: %s", d.get("id"), e)
            stat["failed"] += 1
    return stat


def _rebuild_bigtable(conn, limit: int, overwrite: bool) -> dict:
    from backend.services.snapshot_extractors.bigtable import extract as bt_extract

    rows = conn.execute("""
        SELECT bi.connection_id, bi.inspection_date, COUNT(*) AS cnt,
               COALESCE(c.name, '') AS connection_name
        FROM bigtable_inventory bi
        LEFT JOIN tdsql_connections c ON c.id = bi.connection_id
        GROUP BY bi.connection_id, bi.inspection_date, c.name
        ORDER BY bi.inspection_date DESC LIMIT ?
    """, (limit,)).fetchall()

    stat = {"scanned": 0, "created": 0, "skipped": 0, "failed": 0}
    for r in rows or []:
        d = dict(r)
        conn_id = d.get("connection_id") or ""
        date = d.get("inspection_date") or ""
        biz_ref = f"{conn_id}:{date}"
        stat["scanned"] += 1
        if not overwrite and _snapshot_exists(conn, "bigtable", biz_ref):
            stat["skipped"] += 1
            continue
        try:
            items, obj_total = bt_extract(conn_id, date)
            create_snapshot("bigtable", {
                "biz_ref_id": biz_ref,
                "connection_id": conn_id,
                "connection_name": d.get("connection_name") or "",
                "db_name": "",
                "scan_label": f"大表盘点 {date}",
                "scan_started_at": None,
                "scan_finished_at": f"{date} 00:00:00" if date else "",
                "created_by": "",
            }, items, obj_total, source_kind="rebuild")
            stat["created"] += 1
        except Exception as e:
            logger.warning("回填 bigtable 快照失败 %s: %s", biz_ref, e)
            stat["failed"] += 1
    return stat


def _snapshot_exists(conn, module: str, biz_ref_id: str) -> bool:
    row = conn.execute(
        "SELECT id FROM scan_snapshots WHERE module = ? AND biz_ref_id = ?",
        (module, biz_ref_id)).fetchone()
    return bool(row)


_REBUILDERS = {
    "schema_audit": _rebuild_schema_audit,
    "slow_scan": _rebuild_slow_scan,
    "bigtable": _rebuild_bigtable,
}


def rebuild_snapshots(module: str, limit: int = 200, overwrite: bool = False) -> dict:
    """从源表回填历史快照，返回 {scanned, created, skipped, failed}"""
    if module == "launch_check":
        # V1.5.2：必须显式拒绝，不能静默返回空结果——静默返回会让人以为
        # "回填过了只是没历史数据"，从而误信后续对比结论（设计文档 §4.4）。
        raise ValueError(
            "上线检查不支持存量回填：历史明细每项仅保留前 100 行且已压平为文本，"
            "回填出的快照与实时快照不可比，会在对比中把未回填的问题项误显示为"
            "「已解决」。请以本次上线之后的检查结果为对比基线。")
    if module not in MODULES:
        raise ValueError(f"不支持的模块: {module}")
    limit = max(1, min(int(limit or 200), 1000))
    ensure_db()
    conn = _get_connection()
    try:
        stat = _REBUILDERS[module](conn, limit, bool(overwrite))
    finally:
        conn.close()
    stat["module"] = module
    stat["message"] = (f"回填完成：新建 {stat['created']}，跳过 {stat['skipped']}，"
                       f"失败 {stat['failed']}")
    return stat
