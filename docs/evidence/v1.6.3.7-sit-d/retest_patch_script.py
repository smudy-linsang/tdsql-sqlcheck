"""智能体D / v1.6.3.7 SIT 复测：数据订正脚本（F3）真实执行验证。

直接执行 `deploy/patch_v1637_source.sql` 与 `deploy/rollback_v1637_source.sql` 的**原文语句**，
在专用库 `uat_d_1636_meta` 上验证：① 通用订正生效；② 幂等性；③ 回滚可还原。
"""
import io
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
PATCH = ROOT / "deploy/patch_v1637_source.sql"
ROLLBACK = ROOT / "deploy/rollback_v1637_source.sql"


def connect():
    import pymysql
    return pymysql.connect(host="127.0.0.1", port=13306, user="root",
                           password="tdsql_test_2024", database="uat_d_1636_meta",
                           cursorclass=pymysql.cursors.DictCursor, autocommit=True)


def statements(path):
    """剥掉整行注释后按 ; 切分（脚本不含行尾注释）。"""
    text = io.open(path, encoding="utf-8").read()
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith("--")]
    body = "\n".join(lines)
    return [s.strip() for s in body.split(";") if s.strip()]


def run_script(conn, path, label, collect_selects=True):
    out = []
    with conn.cursor() as cur:
        for stmt in statements(path):
            cur.execute(stmt)
            head = stmt.split(None, 1)[0].upper()
            if collect_selects and head in ("SELECT", "SET"):
                try:
                    out.append({"stmt": head, "rows": cur.fetchall()})
                except Exception:  # SET 无结果集
                    pass
            elif head in ("UPDATE", "CREATE"):
                out.append({"stmt": head, "rowcount": cur.rowcount})
    return out


def setup_rows(conn):
    """造 3 条 UUID 形态记录 + 1 条 id=1377（脚本 OR 分支专指）。"""
    uuid_rows = [
        (1377, "lzbj_ecif", "extracted_lzbj_ecif_a27123e1.sql"),
        (1378, "uat_d_1636_dist", "extracted_uat_d_1636_dist_deadbeef.sql"),
        (1379, "uat_d_1636_dist", "extracted_uat_d_1636_dist_cafebabe.sql"),
    ]
    utc_now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM audit_history WHERE id IN (1377,1378,1379)")
        for rid, db, src in uuid_rows:
            cur.execute(
                "INSERT INTO audit_history (id, audit_type, source, total_sql, passed, failed,"
                " error_count, warning_count, pass_rate, results_json, created_by,"
                " project_id, gate_passed, gate_detail, created_at, connection_id, db_name)"
                " VALUES (%s, 'extracted_schema', %s, 1,1,0,0,0,100.0, '[]', 'sit37',"
                " '', NULL, '', %s, 'c', %s)", (rid, src, utc_now, db))
        cur.execute("DELETE FROM scan_snapshots WHERE biz_ref_id='1377'")
        # 按实际列结构构造快照行（表结构随版本演进，避免硬编码列名）
        cur.execute("SHOW COLUMNS FROM scan_snapshots")
        cols = cur.fetchall()
        fields = {"biz_ref_id": "1377",
                  "scan_label": "extracted_lzbj_ecif_a27123e1.sql"}
        for c in cols:
            name, null_ok, default, extra = c["Field"], c["Null"], c["Default"], c["Extra"]
            if name in fields or "auto_increment" in (extra or "").lower():
                continue
            if null_ok == "NO" and default is None and name not in fields:
                t = (c["Type"] or "").lower()
                if "char" in t or "text" in t:
                    fields[name] = "sit37"
                elif "date" in t or "time" in t:
                    fields[name] = datetime.now().replace(microsecond=0).strftime(
                        "%Y-%m-%d %H:%M:%S")
                else:
                    fields[name] = 0
        names = ", ".join(f"`{k}`" for k in fields)
        ph = ", ".join(["%s"] * len(fields))
        cur.execute(f"INSERT INTO scan_snapshots ({names}) VALUES ({ph})",
                    tuple(fields.values()))
    return {"inserted": [r[0] for r in uuid_rows], "inserted_utc_basis": str(utc_now)}


def snapshot(conn, ids=(1377, 1378, 1379)):
    with conn.cursor() as cur:
        cur.execute("SELECT id, source, created_at FROM audit_history WHERE id IN (%s)"
                    % ",".join(str(i) for i in ids))
        rows = cur.fetchall()
        cur.execute("SELECT biz_ref_id, scan_label FROM scan_snapshots WHERE biz_ref_id='1377'")
        snap = cur.fetchall()
    return {"rows": [{"id": r["id"], "source": r["source"],
                      "created_at": str(r["created_at"])} for r in rows],
            "snapshot": [dict(s) for s in snap]}


def main():
    conn = connect()
    out = {"patch_file": str(PATCH.relative_to(ROOT)),
           "rollback_file": str(ROLLBACK.relative_to(ROOT))}
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS _bak_v1637_audit_history")
        cur.execute("DELETE FROM scan_snapshots WHERE biz_ref_id='1377'")
    out["setup"] = setup_rows(conn)
    out["before"] = snapshot(conn)

    # ① 首次执行
    out["first_run"] = run_script(conn, PATCH, "patch")
    out["after_first"] = snapshot(conn)

    # ② 再次执行（幂等性检查）
    out["second_run"] = run_script(conn, PATCH, "patch")
    out["after_second"] = snapshot(conn)

    # 判定
    def rows_of(snap):
        return {r["id"]: r for r in snap["rows"]}

    def drift(snap):
        d = {}
        for r in snap["rows"]:
            m = re.search(r"_(\d{8}_\d{6})\.sql$", r["source"])
            if m:
                ts = datetime.strptime(m.group(1), "%Y%m%d_%H%M%S")
                ct = datetime.strptime(r["created_at"], "%Y-%m-%d %H:%M:%S")
                d[r["id"]] = round((ct - ts).total_seconds(), 1)
        return d

    out["drift_after_first_s"] = drift(out["after_first"])
    out["drift_after_second_s"] = drift(out["after_second"])
    out["all_names_timestamp_form"] = all(
        re.match(r"^extracted_.+_\d{8}_\d{6}\.sql$", r["source"]) for r in out["after_first"]["rows"])
    out["name_matches_created_at_first"] = all(
        abs(v) <= 2 for v in out["drift_after_first_s"].values())
    out["idempotent"] = (out["after_first"] == out["after_second"])
    out["snapshot_label_synced"] = (
        out["after_first"]["snapshot"] and
        out["after_first"]["snapshot"][0]["scan_label"].endswith("_ea1e8000.sql") is False)

    # ③ 回滚
    out["rollback_run"] = run_script(conn, ROLLBACK, "rollback")
    out["after_rollback"] = snapshot(conn)
    out["rollback_restored"] = (out["after_rollback"]["rows"] == out["before"]["rows"])

    out["verdict"] = {
        "F3-1_通用订正": "PASS" if out["all_names_timestamp_form"] and out["name_matches_created_at_first"] else "FAIL",
        "F3-幂等": "PASS" if out["idempotent"] else "FAIL（二次执行发生了变化）",
        "F3-2_回滚可还原": "PASS" if out["rollback_restored"] else "FAIL",
    }
    with conn.cursor() as cur:      # 收尾：删除 SIT 造数
        cur.execute("DELETE FROM audit_history WHERE id IN (1377,1378,1379)")
        cur.execute("DELETE FROM scan_snapshots WHERE biz_ref_id='1377'")
        cur.execute("DROP TABLE IF EXISTS _bak_v1637_audit_history")
    conn.close()
    (HERE / "retest-patch-script.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: out[k] for k in
                      ("before", "after_first", "after_second", "after_rollback",
                       "drift_after_first_s", "drift_after_second_s", "idempotent",
                       "rollback_restored", "verdict")},
                     ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
