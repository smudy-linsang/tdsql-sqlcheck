"""智能体D / v1.6.3.6 UAT：BUG-01 / BUG-02 修复的行为级复测（含故障注入）。

注入边界（如实声明）：本机是 MySQL 8.0，无 TDSQL Proxy，故 Proxy 侧不可读的物理子表
用**等价的 SHOW CREATE 异常注入**模拟（错误码/文案取自内网实测：660 Proxy ERROR /
1146）。注入只发生在 `_show_create` 边界，其余全部走真实代码路径（真实连接、真实枚举、
真实分类、真实产物与落库）。

子命令：
  bug01_matrix   零误杀 / 良性跳过 / 异常跳过 / 集中式门禁 / 全失败
  bug01_e2e      真实 worker 全流程产出一个"含良性+异常跳过"的任务（供四处口径核对）
  bug02_band     预检区分带：×1.25 放行 vs ×2 拦截
  bug02_compact  压缩路径：omitted 自洽、list 格式、pass_rate 未被污染
  bug02_packet   包限调小后的表现（友好拦截 / 正确压缩，不得裸 PyMySQL 报错）
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
os.environ.update({"SQLCHECK_DB_HOST": "127.0.0.1", "SQLCHECK_DB_PORT": "13306",
                   "SQLCHECK_DB_NAME": "uat_d_1636_meta", "AUTH_ENABLED": "true",
                   "REPORT_OUTPUT_DIR": str(ROOT / "data/reports/uat_d_1636/reports")})
sys.path.insert(0, str(ROOT))

from backend.services import metadata_audit_pipeline as P  # noqa: E402
from backend.services import metadata_audit_repository as R  # noqa: E402
from backend.services.connection_registry import registry  # noqa: E402
from backend.services.metadata_audit_repository import repository as repo  # noqa: E402

HERE = Path(__file__).resolve().parent
DIST_DB = "uat_d_1636_dist"
CENT_DB = "uat_d_1636_cent"
SCOPES = ["TABLE", "INDEX", "VIEW", "SHARDKEY"]


class ProxyErr(Exception):
    """模拟 PyMySQL 的 (code, message) 双参异常。"""


def _inject(rules):
    """按对象名子串注入 SHOW CREATE 异常；返回恢复函数。"""
    orig = P._show_create

    def fake(conn, db, obj):
        for pat, exc in rules.items():
            if pat in obj["name"]:
                raise exc
        return orig(conn, db, obj)

    P._show_create = fake
    return lambda: setattr(P, "_show_create", orig)


def _extract(conn_id, db, instance_type, label):
    pool = registry.get(conn_id)
    lines, stats = P.extract_metadata(pool, db, SCOPES, label, instance_type)
    text = "\n".join(lines)
    return lines, stats, text


def _summ(stats, text, lines):
    return {
        "enumerated": stats["enumerated_objects"], "selected": stats["selected_objects"],
        "extracted": stats["extracted_objects"], "skipped": stats["skipped_objects"],
        "benign": stats["skipped_benign"], "abnormal": stats["skipped_abnormal"],
        "skipped_list_len": len(stats.get("skipped_list") or []),
        "categories": sorted({s.get("category") for s in (stats.get("skipped_list") or [])}),
        "has_summary_line": "[SKIPPED-SUMMARY]" in text,
        "per_object_blocks": text.count("-- [SKIPPED] SQL Object:"),
        "schema_bytes": len(text.encode("utf-8")),
        "parsed_objects": text.count("-- SQL Object: CREATE"),
    }


def bug01_matrix():
    out = {}
    err660 = ProxyErr(660, "Proxy ERROR: Table 'x.cus_bas_merge_log_tdsql_subp190001' does not exist")
    err1045 = ProxyErr(1045, "Access denied for user 'ro'@'%' to database 'x'")

    # ① 零误杀：不注入，分布式库中"命名像子表"的真实表必须被正常提取
    _, st, text = _extract("d36-dist", DIST_DB, "distributed", "D36-分布式库-含子表命名")
    out["zero_kill"] = {**_summ(st, text, None),
                        "verdict": "PASS 零误杀" if st["extracted_objects"] == 9 and st["skipped_objects"] == 0 else "FAIL"}

    # ② 良性跳过：分布式 + 660 + 命名匹配 + 父表存在
    restore = _inject({"cus_bas_merge_log_tdsql_subp": err660})
    try:
        _, st, text = _extract("d36-dist", DIST_DB, "distributed", "D36-分布式库-含子表命名")
        out["benign_skip"] = {**_summ(st, text, None),
                              "verdict": ("PASS 良性跳过" if st["skipped_benign"] == 2
                                          and st["skipped_abnormal"] == 0
                                          and "[SKIPPED-SUMMARY]" in text
                                          and text.count("-- [SKIPPED] SQL Object:") == 0
                                          else "FAIL")}
    finally:
        restore()

    # ③ 异常跳过：普通表 1045（非 660/1146）→ 必须判异常并写逐个溯源块
    restore = _inject({"biz_tab_01": err1045})
    try:
        _, st, text = _extract("d36-dist", DIST_DB, "distributed", "D36-分布式库-含子表命名")
        out["abnormal_skip"] = {**_summ(st, text, None),
                                "verdict": ("PASS 异常跳过" if st["skipped_abnormal"] == 1
                                            and st["skipped_benign"] == 0
                                            and text.count("-- [SKIPPED] SQL Object:") == 1
                                            else "FAIL")}
    finally:
        restore()

    # ④ 集中式门禁：集中式实例 + 命中命名的对象失败 → 不得判良性
    restore = _inject({"cent_tab_b_tdsql_subp202601": err660})
    try:
        _, st, text = _extract("d36-cent", CENT_DB, "centralized", "D36-集中式库-含子表命名")
        out["centralized_gate"] = {**_summ(st, text, None),
                                   "verdict": ("PASS 集中式门禁" if st["skipped_benign"] == 0
                                               and st["skipped_abnormal"] == 1 else "FAIL")}
    finally:
        restore()

    # ⑤ 全失败：所有对象都失败 → 必须抛 NO_AUDITABLE_OBJECTS（fail-closed 不能被放开）
    restore = _inject({"": err1045})   # 子串空 → 命中全部
    try:
        try:
            _extract("d36-cent", CENT_DB, "centralized", "D36-集中式库-含子表命名")
            out["all_fail"] = {"verdict": "FAIL（未抛错）"}
        except P.MetadataExtractError as e:
            out["all_fail"] = {"code": e.code, "message": e.message[:80],
                               "verdict": "PASS 全失败仍报错" if e.code == "NO_AUDITABLE_OBJECTS" else "FAIL"}
    finally:
        restore()

    (HERE / "probe-bug01-matrix.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))


# ── BUG-02 ────────────────────────────────────────────────────────────────
def _mk_job(conn_id, db, instance_type, conn_name):
    key = "d36" + datetime.now(timezone.utc).strftime("%H%M%S%f")[:12]
    ctx = {"engine_version": "1.6.3.6", "rule_set_id": "", "overrides": None,
           "instance_type": instance_type, "instance_type_source": "probe",
           "report_context": "", "connection_name": conn_name, "scope_flags": SCOPES}
    import hashlib
    ctx["context_hash"] = hashlib.sha256(
        json.dumps(ctx, sort_keys=True, default=str).encode()).hexdigest()
    job, _ = repo.create_job(
        created_by="uat_d_1636", request_id="D36", idempotency_key=key,
        request_hash=key, connection_id=conn_id, db_name=db,
        request_json=json.dumps({"connection_id": conn_id, "database": db, "scopes": SCOPES}),
        execution_context_json=json.dumps(ctx, ensure_ascii=False),
        connection_fingerprint="fp", report_deadline_seconds=1800)
    token = "tok" + key
    repo.claim_next_accepted("runner-d36", token, R._now())
    return job["id"], token


def _packet_limit():
    from backend.services.database import _get_connection
    cx = _get_connection()
    p = cx.execute("SELECT @@session.max_allowed_packet AS p").fetchone()["p"]
    cx.close()
    return int(p)


def _publish_with_payload(job_id, token, results_json, extra_skip=(3, 2, 1)):
    """构造 24 列（index 8 = results_json），走真实 publish。"""
    cols = ("extracted_schema", "extracted_d36_probe.sql", 10, 8, 2, 1, 1, 80.0, results_json,
            "uat_d_1636", "", None, "", "2026-09-11 00:00:00.000000", "d36-dist", DIST_DB, None,
            "distributed", "probe", 0, None, extra_skip[0], extra_skip[1], extra_skip[2])
    repo.cas_state(job_id, token, (R.STATE_RUNNING,), R.STATE_PUBLISHING, phase=R.PHASE_PERSISTING)
    return repo.publish(job_id, token, audit_columns_values=cols, results_json=results_json)


def _records_json(n_ok, n_bad, pad_bytes=0):
    """生成 list 格式结果集；pad_bytes 用于把 payload 撑到目标体量。"""
    pad = "x" * pad_bytes if pad_bytes else ""
    recs = []
    for i in range(n_ok):
        recs.append({"sql": f"CREATE TABLE t_{i} (id INT) {pad}", "sql_type": "CREATE",
                     "passed": True, "file_path": "f.sql", "line_number": i, "violations": []})
    for i in range(n_bad):
        recs.append({"sql": f"SELECT * FROM t_{i}", "sql_type": "SELECT", "passed": False,
                     "file_path": "f.sql", "line_number": i, "violations": [
                         {"rule_id": "R012", "severity": "ERROR", "message": "禁用 SELECT *"}]})
    return json.dumps(recs, ensure_ascii=False)


def bug02_band():
    """区分带：真实包限 P 下，构造 len 使 len×1.25 < P < len×2。"""
    from backend.services.database import _get_connection
    p = _packet_limit()
    margin = R._PACKET_MARGIN
    out = {"max_allowed_packet": p, "escape_factor": R._ESCAPE_FACTOR, "margin": margin}

    # A) 放行带：转义后(×1.25)恰好不超限，但旧 ×2 会误拦（单条记录承载 padding）
    target_ok = int((p - margin) / R._ESCAPE_FACTOR) - 4096
    j1, t1 = _mk_job("d36-dist", DIST_DB, "distributed", "D36-分布式库-含子表命名")
    payload_ok = _records_json(1, 0, pad_bytes=max(0, target_ok - 200))
    out["band_pass"] = {"payload_bytes": len(payload_ok.encode()),
                        "x1_25": int(len(payload_ok.encode()) * 1.25) + margin,
                        "x2": len(payload_ok.encode()) * 2 + margin}
    try:
        rid = _publish_with_payload(j1, t1, payload_ok)
        out["band_pass"].update(report_id=rid, verdict="PASS 真实落库（旧 ×2 会误拦）")
    except Exception as e:  # noqa: BLE001
        out["band_pass"].update(verdict=f"FAIL {type(e).__name__}: {getattr(e,'code','')} {e}")
    finally:
        repo.release_slot(j1)

    # B) 拦截带：转义后(×1.25)超限 → 必须友好拦截（PERSIST_PAYLOAD_TOO_LARGE）
    target_bad = int((p - margin) / R._ESCAPE_FACTOR) + 2 * 1024 * 1024
    j2, t2 = _mk_job("d36-dist", DIST_DB, "distributed", "D36-分布式库-含子表命名")
    payload_bad = _records_json(1, 0, pad_bytes=max(0, target_bad - 200))
    try:
        rid = _publish_with_payload(j2, t2, payload_bad)
        out["band_reject"] = {"payload_bytes": len(payload_bad.encode()), "report_id": rid,
                              "verdict": "FAIL（本应拦截却落库）"}
    except Exception as e:  # noqa: BLE001
        out["band_reject"] = {"payload_bytes": len(payload_bad.encode()),
                              "code": getattr(e, "code", type(e).__name__),
                              "message": str(getattr(e, "message", e))[:120],
                              "verdict": ("PASS 友好拦截" if getattr(e, "code", "") ==
                                          "PERSIST_PAYLOAD_TOO_LARGE" else "FAIL 非友好拦截")}
    finally:
        repo.release_slot(j2)

    (HERE / "probe-bug02-band.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))


def bug02_compact():
    """压缩路径：构造 > 自适应阈值的载荷，验证 omitted 自洽 / list 格式 / 列契约。"""
    from backend.services.database import _get_connection
    p = _packet_limit()
    threshold = int((p - R._PACKET_MARGIN) / R._ESCAPE_FACTOR)
    out = {"max_allowed_packet": p, "adaptive_threshold": threshold}
    # 2000 条通过 + 20 条违规，撑到**略超**自适应阈值（必须真触发压缩）
    n = 2000
    per = max(1024, int(threshold * 1.05 / n))
    payload = _records_json(n, 20, pad_bytes=per)
    raw_len = len(payload.encode())
    out["payload_bytes"] = raw_len
    out["payload_over_threshold"] = raw_len > threshold
    j, t = _mk_job("d36-dist", DIST_DB, "distributed", "D36-分布式库-含子表命名")
    try:
        rid = _publish_with_payload(j, t, payload, extra_skip=(9, 7, 2))
        out["report_id"] = rid
        cx = _get_connection()
        row = cx.execute("SELECT total_sql,passed,failed,pass_rate,results_json,"
                         "skipped_objects,skipped_benign,skipped_abnormal,omitted_results "
                         "FROM audit_history WHERE id=?", (rid,)).fetchone()
        cx.close()
        stored = row["results_json"]
        recs = json.loads(stored)
        omitted = int(row["omitted_results"] or 0)
        out["stored"] = {"is_list": isinstance(recs, list), "stored_records": len(recs),
                         "stored_bytes": len(stored.encode()),
                         "omitted_results": omitted,
                         "skipped_objects": row["skipped_objects"],
                         "skipped_benign": row["skipped_benign"],
                         "skipped_abnormal": row["skipped_abnormal"],
                         "pass_rate": float(row["pass_rate"]),
                         "total_sql": row["total_sql"]}
        viol_kept = sum(1 for r in recs if isinstance(r, dict) and not r.get("passed", True))
        out["violations_kept"] = viol_kept
        out["verdict"] = ("PASS 压缩自洽" if isinstance(recs, list) and omitted > 0
                          and len(recs) + omitted == n + 20 and viol_kept == 20
                          and abs(float(row["pass_rate"]) - 80.0) < 1e-6
                          else "FAIL")
    except Exception as e:  # noqa: BLE001
        out["verdict"] = f"FAIL {type(e).__name__}: {getattr(e,'code','')} {e}"
    finally:
        repo.release_slot(j)
    (HERE / "probe-bug02-compact.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))


def bug02_packet():
    """包限调小后的表现（须在**独立进程**中运行：连接池会复用旧会话的包限）。

    场景 A：可压缩到限内 → 应压缩落库（omitted>0）；
    场景 B：违规项过多、压缩后仍超限 → 应**友好拦截** PERSIST_PAYLOAD_TOO_LARGE，
            不得裸穿 PyMySQL 1153/2006。
    """
    from backend.services.database import _get_connection
    p = _packet_limit()
    threshold = int((p - R._PACKET_MARGIN) / R._ESCAPE_FACTOR)
    out = {"max_allowed_packet": p, "adaptive_threshold": threshold}

    # A) 可通过压缩落入限内
    payload = _records_json(3000, 30, pad_bytes=max(1024, int(threshold * 1.6 / 3000)))
    out["A_payload_bytes"] = len(payload.encode())
    j, t = _mk_job("d36-dist", DIST_DB, "distributed", "D36-分布式库-含子表命名")
    try:
        rid = _publish_with_payload(j, t, payload)
        cx = _get_connection()
        row = cx.execute("SELECT omitted_results, LENGTH(results_json) AS n "
                         "FROM audit_history WHERE id=?", (rid,)).fetchone()
        cx.close()
        out.update(A_report_id=rid, A_omitted=int(row["omitted_results"] or 0),
                   A_stored_len=int(row["n"]),
                   A_verdict=("PASS 正确压缩落库" if int(row["omitted_results"] or 0) > 0
                              else "FAIL 未压缩却落库（可疑）"))
    except Exception as e:  # noqa: BLE001
        code = getattr(e, "code", type(e).__name__)
        bare = "1153" in str(e) or "2006" in str(e)
        out.update(A_code=code, A_verdict=("FAIL 裸 PyMySQL 报错" if bare else f"FAIL 非友好: {code}"))
    finally:
        repo.release_slot(j)

    # B) 违规项极多 → 压缩后仍超限 → 友好拦截
    n_bad = max(200, int(threshold * 2 / 4096))
    recs = [{"sql": "SELECT * FROM t", "sql_type": "SELECT", "passed": False,
             "file_path": "f.sql", "line_number": i,
             "violations": [{"rule_id": "R012", "severity": "ERROR",
                             "message": "禁用 SELECT * " + "y" * 2048}]}
            for i in range(n_bad)]
    payload_bad = json.dumps(recs, ensure_ascii=False)
    out["B_payload_bytes"] = len(payload_bad.encode())
    j2, t2 = _mk_job("d36-dist", DIST_DB, "distributed", "D36-分布式库-含子表命名")
    try:
        rid = _publish_with_payload(j2, t2, payload_bad)
        out.update(B_report_id=rid, B_verdict="PASS 落库成功（未超限）")
    except Exception as e:  # noqa: BLE001
        code = getattr(e, "code", type(e).__name__)
        bare = "1153" in str(e) or "2006" in str(e)
        out.update(B_code=code, B_message=str(getattr(e, "message", e))[:140],
                   B_verdict=("FAIL 裸 PyMySQL 报错" if bare else
                              ("PASS 友好拦截" if code == "PERSIST_PAYLOAD_TOO_LARGE"
                               else f"FAIL 非友好: {code}")))
    finally:
        repo.release_slot(j2)
    (HERE / "probe-bug02-packet.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))


def bug01_e2e():
    """真实 worker 全流程：分布式库 + 2 张良性跳过 + 1 张异常跳过 → 产出真实任务/报告/产物。"""
    from backend.workers import metadata_audit_worker as W
    from backend.services import metadata_audit_repository as RR
    job_id, token = _mk_job("d36-dist", DIST_DB, "distributed", "D36-分布式库-含子表命名")
    err660 = ProxyErr(660, "Proxy ERROR: Table 'uat_d_1636_dist.cus_bas_merge_log_tdsql_subp190001' does not exist")
    err1045 = ProxyErr(1045, "Access denied for user 'ro'@'%' to database 'uat_d_1636_dist'")
    restore = _inject({"cus_bas_merge_log_tdsql_subp": err660, "biz_tab_01": err1045})
    out = {"job_id": job_id}
    try:
        rc = W.run(job_id, token)
        out["worker_rc"] = rc
        repo.complete(job_id, token, exit_code=0, cleanup_ok=True)
        repo.release_slot(job_id)   # 模拟 runner 回收后释放槽
    finally:
        restore()
    j = repo.get_job(job_id)
    out.update(state=j["state"], report_id=j.get("report_id"), snapshot_id=j.get("snapshot_id"),
               progress=j.get("progress_json"))
    rid = j.get("report_id")
    if rid:
        from backend.services.database import _get_connection
        cx = _get_connection()
        row = cx.execute("SELECT total_sql,passed,failed,pass_rate,skipped_objects,skipped_benign,"
                         "skipped_abnormal,omitted_results,connection_id,db_name,results_json "
                         "FROM audit_history WHERE id=?", (rid,)).fetchone()
        cx.close()
        recs = json.loads(row["results_json"])
        out["history"] = {"total_sql": row["total_sql"], "passed": row["passed"],
                          "failed": row["failed"], "pass_rate": float(row["pass_rate"]),
                          "skipped_objects": row["skipped_objects"],
                          "skipped_benign": row["skipped_benign"],
                          "skipped_abnormal": row["skipped_abnormal"],
                          "omitted_results": row["omitted_results"],
                          "records_in_history": len(recs), "is_list": isinstance(recs, list),
                          "connection_id": row["connection_id"], "db_name": row["db_name"]}
        d = ROOT / "data/reports/uat_d_1636/reports/metadata-audit" / job_id
        schema = (d / "schema.sql")
        text = schema.read_text(encoding="utf-8") if schema.exists() else ""
        out["artifacts"] = {
            "dir": str(d),
            "schema_exists": schema.exists(),
            "schema_has_summary": "[SKIPPED-SUMMARY]" in text,
            "schema_per_object_blocks": text.count("-- [SKIPPED] SQL Object:"),
            "schema_objects": text.count("-- SQL Object: CREATE"),
            "manifest_exists": (d / "manifest.json").exists(),
            "results_ndjson_lines": (sum(1 for _ in (d / "results.ndjson").open(encoding="utf-8"))
                                     if (d / "results.ndjson").exists() else 0),
        }
    out["verdict"] = ("PASS 端到端（含良性+异常跳过）"
                      if out.get("state") == "SUCCEEDED" and out.get("history", {}).get("skipped_benign") == 2
                      and out.get("history", {}).get("skipped_abnormal") == 1
                      and out.get("artifacts", {}).get("schema_has_summary")
                      and out.get("artifacts", {}).get("schema_per_object_blocks") == 1
                      else "FAIL")
    (HERE / "probe-bug01-e2e.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    {"bug01_matrix": bug01_matrix, "bug01_e2e": bug01_e2e, "bug02_band": bug02_band,
     "bug02_compact": bug02_compact, "bug02_packet": bug02_packet}[sys.argv[1]]()
