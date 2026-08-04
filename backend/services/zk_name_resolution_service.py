"""V1.6.0.3 实例名称多源自适应解析服务（设计 DESIGN-v1.6.0.3 §4）。

v1.6.0.1 的精确等值查询（f_mid='/tdsqlzk/<id>' + f_type=1 + 固定两键）在内网真实
monitordb 全部落空。本服务改为五级解析链，命中即停，全程返回 name_source 溯源：

  L1 monitor_exact  精确 mid + 标准键 + f_type=1（保留原口径，标准环境可中）
  L2 monitor_like   模糊 mid（f_mid/f_pmid LIKE）+ 扩宽键集，不限 f_type
  L3 monitor_value  模糊 mid + 不限键，取"值像名称"的行
  L4 meta_table     monitordb 中 %instance%/%meta% 表的名称类列探针
  L5 zk_node        ZK setrun 节点名称类字段（扫描期已直取，零额外依赖）

另提供 diagnose()：返回某实例在 monitordb 的 mid 样本/键样本/各级命中/元数据表样本，
供内网首测固化 name_query_hint。所有 SQL 参数化绑定；响应不含口令。
"""
from __future__ import annotations

import logging
import re
from typing import Iterable, Optional

logger = logging.getLogger("tdsql.zk_name")

NAME_KEYS = ("instance_name", "clientName", "name", "set_name", "instance_alias")
_NAMELIKE_COL = re.compile(r"(name|alias|client)", re.IGNORECASE)
_IDLIKE_COL = re.compile(r"(^f_mid$|^f_pmid$|mid$|_id$|^id$)", re.IGNORECASE)


def _looks_like_name(value: str) -> bool:
    v = (value or "").strip()
    if not v or len(v) > 64:
        return False
    if v.isdigit():
        return False
    if any(ch in v for ch in "/{}%"):
        return False
    return True


class ZKNameResolutionService:
    """实例 ID → 实例名称的五级解析链。monitor_conn 为已建立的 pymysql 连接（DictCursor）。"""

    def resolve(self, monitor_conn, instance_id: str, set_ids: Iterable[str],
                instance_kind: str, hint: str = "",
                zk_name_fields: Optional[dict] = None) -> tuple[str, str, dict]:
        """返回 (resolved_name, name_source, detail)。未解析返回 ("", "", detail)。"""
        candidates = [str(instance_id or "").strip()]
        extra = [str(s).strip() for s in (set_ids or []) if str(s).strip()]
        if instance_kind == "groupshard":
            candidates.extend(extra)
        elif extra and extra[0] not in candidates:
            candidates.append(extra[0])
        candidates = [c for c in candidates if c]
        detail: dict = {"candidates": candidates}
        levels = ["L1", "L2", "L3", "L4", "L5"]
        start = levels.index(hint) if hint in levels else 0

        if monitor_conn is not None:
            for level in levels[start:4 if start < 4 else 4]:
                for cid in candidates:
                    hit = getattr(self, f"_level_{level.lower()}")(monitor_conn, cid, detail)
                    if hit:
                        return hit[0], hit[1], detail
        # L5：ZK setrun 名称类字段
        if start <= 4:
            fields = zk_name_fields or {}
            for key in ("instance_name", "clientName", "name", "set_name", "comment", "alias"):
                value = str(fields.get(key) or "").strip()
                if _looks_like_name(value):
                    detail["l5_key"] = key
                    return value, "zk_node", detail
        return "", "", detail

    # ── L1 ────────────────────────────────────────────────
    @staticmethod
    def _level_l1(conn, cid: str, detail: dict):
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT f_key, f_val FROM m_data_cur "
                    "WHERE f_type = 1 AND f_mid = %s AND f_key IN ('instance_name','clientName') "
                    "ORDER BY CASE f_key WHEN 'instance_name' THEN 0 ELSE 1 END",
                    (f"/tdsqlzk/{cid}",))
                for row in cur.fetchall() or []:
                    name = str((row or {}).get("f_val") or "").strip()
                    if _looks_like_name(name):
                        detail["l1"] = {"f_mid": f"/tdsqlzk/{cid}", "f_key": row.get("f_key")}
                        return name, "monitor_exact", detail
        except Exception as exc:
            logger.debug("name L1 failed cid=%s: %s", cid, type(exc).__name__)
        return None

    # ── L2 ────────────────────────────────────────────────
    @staticmethod
    def _level_l2(conn, cid: str, detail: dict):
        try:
            with conn.cursor() as cur:
                placeholders = ",".join(["%s"] * len(NAME_KEYS))
                cur.execute(
                    "SELECT f_mid, f_key, f_val FROM m_data_cur "
                    f"WHERE (f_mid LIKE %s OR f_pmid LIKE %s) AND f_key IN ({placeholders}) "
                    "ORDER BY f_key LIMIT 5",
                    (f"%{cid}%", f"%{cid}%", *NAME_KEYS))
                for row in cur.fetchall() or []:
                    name = str((row or {}).get("f_val") or "").strip()
                    if _looks_like_name(name):
                        detail["l2"] = {"f_mid": row.get("f_mid"), "f_key": row.get("f_key")}
                        return name, "monitor_like", detail
        except Exception as exc:
            logger.debug("name L2 failed cid=%s: %s", cid, type(exc).__name__)
        return None

    # ── L3 ────────────────────────────────────────────────
    @staticmethod
    def _level_l3(conn, cid: str, detail: dict):
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT f_mid, f_key, f_val FROM m_data_cur "
                    "WHERE (f_mid LIKE %s OR f_pmid LIKE %s) LIMIT 200",
                    (f"%{cid}%", f"%{cid}%"))
                for row in cur.fetchall() or []:
                    name = str((row or {}).get("f_val") or "").strip()
                    if _looks_like_name(name):
                        detail["l3"] = {"f_mid": row.get("f_mid"), "f_key": row.get("f_key")}
                        return name, "monitor_value", detail
        except Exception as exc:
            logger.debug("name L3 failed cid=%s: %s", cid, type(exc).__name__)
        return None

    # ── L4 ────────────────────────────────────────────────
    @staticmethod
    def _level_l4(conn, cid: str, detail: dict):
        try:
            with conn.cursor() as cur:
                tables: list[str] = []
                for pattern in ("%instance%", "%meta%"):
                    cur.execute("SHOW TABLES LIKE %s", (pattern,))
                    tables.extend(next(iter(row.values())) for row in cur.fetchall() or [])
                for table in list(dict.fromkeys(tables))[:5]:
                    cur.execute(
                        "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s", (table,))
                    cols = [str(r.get("COLUMN_NAME") or r.get("column_name") or "") for r in cur.fetchall() or []]
                    name_cols = [c for c in cols if _NAMELIKE_COL.search(c)]
                    id_cols = [c for c in cols if _IDLIKE_COL.search(c)]
                    if not name_cols or not id_cols:
                        continue
                    cur.execute(
                        f"SELECT `{name_cols[0]}` AS nm FROM `{table}` "
                        f"WHERE `{id_cols[0]}` LIKE %s LIMIT 1", (f"%{cid}%",))
                    row = (cur.fetchall() or [None])[0]
                    name = str((row or {}).get("nm") or "").strip()
                    if _looks_like_name(name):
                        detail["l4"] = {"table": table, "name_col": name_cols[0], "id_col": id_cols[0]}
                        return name, "meta_table", detail
        except Exception as exc:
            logger.debug("name L4 failed cid=%s: %s", cid, type(exc).__name__)
        return None

    # ── 诊断 ──────────────────────────────────────────────
    def diagnose(self, monitor_conn, instance_id: str, set_ids: Iterable[str],
                 instance_kind: str, zk_name_fields: Optional[dict] = None) -> dict:
        """返回该实例在 monitordb 的形态样本与各级命中，供固化 name_query_hint。"""
        candidates = [str(instance_id or "").strip()] + [
            str(s).strip() for s in (set_ids or []) if str(s).strip()]
        out = {
            "instance_id": instance_id,
            "matched_mids": [], "available_keys": [], "name_hits": [],
            "meta_tables": [], "zk_name_fields": zk_name_fields or {},
        }
        if monitor_conn is None:
            return out
        try:
            with monitor_conn.cursor() as cur:
                for cid in candidates:
                    cur.execute(
                        "SELECT DISTINCT f_mid FROM m_data_cur "
                        "WHERE f_mid LIKE %s OR f_pmid LIKE %s LIMIT 10",
                        (f"%{cid}%", f"%{cid}%"))
                    mids = [str(r.get("f_mid") or "") for r in cur.fetchall() or []]
                    out["matched_mids"].extend(m for m in mids if m)
                    if mids:
                        ph = ",".join(["%s"] * len(mids))
                        cur.execute(
                            f"SELECT DISTINCT f_key FROM m_data_cur WHERE f_mid IN ({ph}) LIMIT 50",
                            tuple(mids))
                        out["available_keys"].extend(
                            str(r.get("f_key") or "") for r in cur.fetchall() or [])
                out["matched_mids"] = list(dict.fromkeys(out["matched_mids"]))[:10]
                out["available_keys"] = list(dict.fromkeys(out["available_keys"]))[:50]
                for level in ("L1", "L2", "L3"):
                    for cid in candidates:
                        hit = getattr(self, f"_level_{level.lower()}")(monitor_conn, cid, {})
                        if hit:
                            out["name_hits"].append(
                                {"level": level, "name": hit[0], "source": hit[1],
                                 **{k: v for k, v in hit[2].get(level.lower(), {}).items()}})
                            break
                cur.execute("SHOW TABLES LIKE '%instance%'")
                inst_tables = [next(iter(r.values())) for r in cur.fetchall() or []]
                cur.execute("SHOW TABLES LIKE '%meta%'")
                inst_tables += [next(iter(r.values())) for r in cur.fetchall() or []]
                for table in list(dict.fromkeys(inst_tables))[:5]:
                    cur.execute(
                        "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s", (table,))
                    cols = [str(r.get("COLUMN_NAME") or r.get("column_name") or "") for r in cur.fetchall() or []]
                    out["meta_tables"].append({
                        "table": table,
                        "name_columns": [c for c in cols if _NAMELIKE_COL.search(c)],
                    })
        except Exception as exc:
            logger.warning("name diagnose failed instance=%s error_type=%s",
                           instance_id, type(exc).__name__)
            out["error"] = type(exc).__name__
        return out


zk_name_resolution_service = ZKNameResolutionService()
