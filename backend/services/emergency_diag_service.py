"""M3 · G7 应急诊断一键包（源自原厂 mysql_emergency_diag）

对业务实例一键采集应急快照，全部只读（SELECT/SHOW），绝不执行任何写/kill：
  S1 实例健康 / S2 会话 / S3 大事务 / S4 锁等待(版本自适应) /
  S5 异常慢SQL / S6 InnoDB引擎状态与死锁
锁等待表按内核版本自适应(评审整改 §0.4-2)：
  8.0 → performance_schema.data_lock_waits；5.7 → information_schema.innodb_lock_waits。
tdsql=True 时对分布式查询加 /*sets:allsets*/（不支持则自动退回不加）。
"""
import json
import logging
import re

from backend.services.database import _get_connection

logger = logging.getLogger("tdsql.emergency")

VALID_ACTIONS = {"status", "session", "bigtrx", "lock", "slow", "innodb"}


def _rows(pool, sql, hint=""):
    try:
        return pool._execute((hint or "") + sql), None
    except Exception as e:
        # 加了 hint 失败时退回不加 hint 再试一次
        if hint:
            try:
                return pool._execute(sql), None
            except Exception as e2:
                return None, str(e2)[:200]
        return None, str(e)[:200]


def _status(pool, hint):
    rows, err = _rows(pool, "SHOW GLOBAL STATUS WHERE Variable_name IN "
                            "('Threads_connected','Threads_running','Questions','Uptime',"
                            "'Aborted_connects','Slow_queries')")
    if err:
        return {"ok": False, "error": err}
    st = {r["Variable_name"]: r["Value"] for r in rows}
    vrows, _ = _rows(pool, "SHOW VARIABLES WHERE Variable_name='max_connections'")
    max_conn = int(vrows[0]["Value"]) if vrows else 0
    conn_now = int(st.get("Threads_connected", 0) or 0)
    usage = round(conn_now / max_conn * 100, 1) if max_conn else 0
    summary = (f"连接 {conn_now}/{max_conn}({usage}%) | 运行中线程 "
               f"{st.get('Threads_running')} | 慢查询累计 {st.get('Slow_queries')}")
    sev = "ERROR" if usage >= 85 else ("WARNING" if usage >= 70 else "INFO")
    return {"ok": True, "severity": sev, "summary": summary, "data": st,
            "max_connections": max_conn, "conn_usage_pct": usage}


def _session(pool, hint):
    rows, err = _rows(pool, "SELECT id,user,host,db,command,time,state,LEFT(info,200) info "
                            "FROM information_schema.processlist "
                            "WHERE command<>'Sleep' ORDER BY time DESC LIMIT 30", hint)
    if err:
        return {"ok": False, "error": err}
    agg = {}
    for r in rows:
        agg[r.get("state") or ""] = agg.get(r.get("state") or "", 0) + 1
    return {"ok": True, "summary": f"活跃会话 {len(rows)} 条", "top_sessions": rows,
            "state_agg": agg, "severity": "INFO"}


def _bigtrx(pool, hint):
    rows, err = _rows(pool,
        "SELECT trx_id, trx_state, trx_started, "
        "TIMESTAMPDIFF(SECOND, trx_started, NOW()) AS duration_s, "
        "trx_rows_modified, trx_rows_locked, trx_mysql_thread_id, LEFT(trx_query,200) trx_query "
        "FROM information_schema.innodb_trx ORDER BY trx_started ASC LIMIT 30", hint)
    if err:
        return {"ok": False, "error": err}
    long_ones = [r for r in rows if int(r.get("duration_s") or 0) >= 10]
    sev = "ERROR" if any(int(r.get("duration_s") or 0) >= 60 for r in rows) else (
        "WARNING" if long_ones else "INFO")
    return {"ok": True, "severity": sev,
            "summary": f"活跃事务 {len(rows)} 条，其中运行≥10s {len(long_ones)} 条",
            "transactions": rows}


def _lock(pool, hint):
    """版本自适应：先试 8.0，失败退 5.7。"""
    ver = ""
    vr, _ = _rows(pool, "SELECT VERSION() AS v")
    if vr:
        ver = str(vr[0].get("v", ""))
    # 8.0 / MySQL performance_schema.data_lock_waits
    rows, err = _rows(pool,
        "SELECT r.trx_id waiting_trx, r.trx_mysql_thread_id waiting_thread, "
        "LEFT(r.trx_query,150) waiting_query, b.trx_id blocking_trx, "
        "b.trx_mysql_thread_id blocking_thread "
        "FROM performance_schema.data_lock_waits w "
        "JOIN information_schema.innodb_trx r ON r.trx_id=w.REQUESTING_ENGINE_TRANSACTION_ID "
        "JOIN information_schema.innodb_trx b ON b.trx_id=w.BLOCKING_ENGINE_TRANSACTION_ID LIMIT 50", hint)
    src = "performance_schema.data_lock_waits(8.0)"
    if err:
        # 5.7 / information_schema.innodb_lock_waits
        rows, err2 = _rows(pool,
            "SELECT r.trx_id waiting_trx, r.trx_mysql_thread_id waiting_thread, "
            "LEFT(r.trx_query,150) waiting_query, b.trx_id blocking_trx, "
            "b.trx_mysql_thread_id blocking_thread "
            "FROM information_schema.innodb_lock_waits w "
            "JOIN information_schema.innodb_trx r ON r.trx_id=w.requesting_trx_id "
            "JOIN information_schema.innodb_trx b ON b.trx_id=w.blocking_trx_id LIMIT 50", hint)
        src = "information_schema.innodb_lock_waits(5.7)"
        if err2:
            return {"ok": False, "version": ver, "error": f"锁等待表不可用: {err} / {err2}"}
    sev = "ERROR" if rows else "INFO"
    return {"ok": True, "version": ver, "source": src, "severity": sev,
            "summary": f"锁等待阻塞链 {len(rows)} 条", "lock_waits": rows}


def _slow(pool, hint, threshold=2):
    rows, err = _rows(pool,
        f"SELECT id,user,host,db,time,state,LEFT(info,200) info "
        f"FROM information_schema.processlist WHERE command<>'Sleep' AND time>{int(threshold)} "
        f"AND info IS NOT NULL ORDER BY time DESC LIMIT 30", hint)
    if err:
        return {"ok": False, "error": err}
    sev = "WARNING" if rows else "INFO"
    return {"ok": True, "severity": sev,
            "summary": f"正在执行且耗时>{threshold}s 的SQL {len(rows)} 条", "slow_running": rows}


def _innodb(pool, hint):
    rows, err = _rows(pool, "SHOW ENGINE INNODB STATUS")
    if err or not rows:
        return {"ok": False, "error": err or "无输出"}
    status_text = ""
    for k, v in rows[0].items():
        if k.lower() == "status":
            status_text = v or ""
    deadlock = ""
    m = re.search(r"LATEST DETECTED DEADLOCK\s*-+\s*(.*?)(?:-{10,}|WE ROLL BACK)",
                  status_text, re.DOTALL)
    if m:
        deadlock = m.group(0)[:2000]
    has_dl = "LATEST DETECTED DEADLOCK" in status_text and bool(deadlock)
    return {"ok": True, "severity": "WARNING" if has_dl else "INFO",
            "summary": "检测到最近死锁记录" if has_dl else "无最近死锁",
            "latest_deadlock": deadlock or "(无)"}


_DISPATCH = {"status": _status, "session": _session, "bigtrx": _bigtrx,
             "lock": _lock, "slow": _slow, "innodb": _innodb}


def run(pool, connection_id="", actions=None, tdsql=False, operator="") -> dict:
    acts = actions or ["all"]
    if "all" in acts:
        acts = ["status", "session", "bigtrx", "lock", "slow", "innodb"]
    acts = [a for a in acts if a in VALID_ACTIONS]
    hint = "/*sets:allsets*/" if tdsql else ""
    sections = {}
    for a in acts:
        try:
            sections[a] = _DISPATCH[a](pool, hint)
        except Exception as e:
            sections[a] = {"ok": False, "error": str(e)[:200]}
    report = {"connection_id": connection_id, "actions": acts, "sections": sections}
    conn = _get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO emergency_report (connection_id, actions, report_json, created_by) "
            "VALUES (?,?,?,?)",
            (connection_id, ",".join(acts), json.dumps(report, ensure_ascii=False, default=str), operator))
        report["report_id"] = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    return report
