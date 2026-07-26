"""
V1.3 扫描结果纵向对比 — 单元测试（T01–T29）

设计依据：docs/DETAIL-v1.3-扫描结果对比.md §13
"""
import json

import pytest

from backend.services import scan_compare_service as cmp
from backend.services import scan_snapshot_service as snap
from backend.services.scan_compare_service import CompareError
from backend.services.scan_compare_report import render_compare_html
from backend.services.snapshot_extractors.base import IssueItem, fp, parse_object
from backend.services.snapshot_extractors.schema_audit import extract_from_json


# ── 测试数据构造 ──

def _results(items):
    """items: [(sql, [(rule_id, severity, message)])]"""
    return json.dumps([
        {"sql": sql, "violations": [
            {"rule_id": r, "severity": s, "message": m, "suggestion": "fix"}
            for r, s, m in vs]}
        for sql, vs in items], ensure_ascii=False)


SCAN1 = _results([
    ("CREATE TABLE `t_order` (id int)", [
        ("R003", "ERROR", "CREATE TABLE 未指定主键"),
        ("R004", "ERROR", "存储引擎 'MyISAM' 不符合规范")]),
    ("CREATE TABLE `t_pay` (id int)", [("R012", "WARNING", "禁止使用 SELECT *")]),
    ("CREATE TABLE `t_log` (id int)", [("R028", "WARNING", "表 t_log 缺少表级别COMMENT")]),
])

SCAN2 = _results([
    ("CREATE TABLE `t_order` (id int)", [("R004", "ERROR", "存储引擎 'MyISAM' 不符合规范")]),
    ("CREATE TABLE `t_pay` (id int)", [("R012", "ERROR", "禁止使用 SELECT *")]),
    ("CREATE TABLE `t_log` (id int)", []),
    ("CREATE TABLE `t_new` (id int)", [("R003", "ERROR", "CREATE TABLE 未指定主键")]),
])


def _mk_snapshot(biz_ref, results_json, finished, conn_id="c-test", module="schema_audit"):
    items, obj_total = extract_from_json(results_json, "trade_core")
    return snap.create_snapshot(module, {
        "biz_ref_id": biz_ref, "connection_id": conn_id, "connection_name": "测试实例",
        "db_name": "trade_core", "node": "", "scan_label": biz_ref,
        "scan_finished_at": finished, "created_by": "pytest",
    }, items, obj_total)


@pytest.fixture(scope="module")
def pair():
    a = _mk_snapshot("UT-A", SCAN1, "2026-07-01 02:00:00")
    b = _mk_snapshot("UT-B", SCAN2, "2026-07-15 02:00:00")
    return a, b


# ── 指纹稳定性（核心）──

def test_t01_fingerprint_stable():
    """T01 同一份 results 生成两次，key 集合完全一致"""
    a, _ = extract_from_json(SCAN1, "trade_core")
    b, _ = extract_from_json(SCAN1, "trade_core")
    assert {i.key for i in a} == {i.key for i in b}


def test_t02_line_shift_does_not_change_fingerprint():
    """T02【核心】前面插入新表使行号整体后移，原有问题项 key 不变"""
    base, _ = extract_from_json(SCAN1, "trade_core")
    shifted = json.dumps(
        [{"sql": "CREATE TABLE `zz_pad` (id int)", "violations": []}] + json.loads(SCAN1),
        ensure_ascii=False)
    after, _ = extract_from_json(shifted, "trade_core")
    assert {i.key for i in base} <= {i.key for i in after}


def test_t03_object_rename_counts_as_fixed_and_new():
    """T03 对象重命名：旧对象 FIXED、新对象 NEW"""
    old = _results([("CREATE TABLE `t_a` (id int)", [("R003", "ERROR", "未指定主键")])])
    new = _results([("CREATE TABLE `t_b` (id int)", [("R003", "ERROR", "未指定主键")])])
    a, _ = extract_from_json(old, "d")
    b, _ = extract_from_json(new, "d")
    assert {i.key for i in a}.isdisjoint({i.key for i in b})


def test_parse_object_variants():
    assert parse_object("CREATE TABLE `db1`.`t1` (a int)", "d") == ("db1.t1", "TABLE")
    assert parse_object("CREATE TABLE t2 (a int)", "d") == ("d.t2", "TABLE")
    assert parse_object("CREATE VIEW v1 AS SELECT 1", "d") == ("d.v1", "VIEW")
    assert parse_object("ALTER TABLE t3 ADD COLUMN c int", "d") == ("d.t3", "TABLE")
    name, otype = parse_object("SET foo=1", "d")
    assert name.startswith("<unparsed:") and otype == ""


# ── 比对语义 ──

def test_t04_t05_fixed_new_remain(pair):
    """T04/T05 修复与新增计数正确"""
    a, b = pair
    r = cmp.run_compare([a, b], module="schema_audit")
    s = r["summary"]
    assert s["base_total"] == 4 and s["target_total"] == 3
    assert s["fixed_count"] == 2      # t_order R003 + t_log R028
    assert s["new_count"] == 1        # t_new R003
    assert s["remain_count"] == 2
    assert s["fix_rate"] == 50.0
    assert s["degraded"] is False
    fixed = {(x["object_name"], x["issue_type"]) for x in r["fixed"]}
    assert fixed == {("trade_core.t_order", "R003"), ("trade_core.t_log", "R028")}


def test_t06_severity_change_detected(pair):
    """T06 WARNING 升 ERROR 计入 changed，direction=UP"""
    a, b = pair
    r = cmp.run_compare([a, b], module="schema_audit")
    assert r["summary"]["changed_count"] == 1
    ch = r["changed"][0]["change"]
    assert ch["type"] == "SEVERITY" and ch["old"] == "WARNING" and ch["new"] == "ERROR"
    assert ch["direction"] == "UP"


def test_t07_slow_perf_change():
    """T07 慢SQL avg_time_ms 变化 ≥30% 计入 PERF 变化"""
    key = fp("s", "q1")
    mk = lambda ms: [IssueItem(key=key, object_name="t_a", issue_type="SLOW",
                               severity="ERROR", title="SELECT ...",
                               attrs={"avg_time_ms": ms, "exec_count": 10})]
    a = snap.create_snapshot("slow_scan", {
        "biz_ref_id": "UT-SL1", "connection_id": "c-sl", "db_name": "d",
        "scan_finished_at": "2026-07-01 00:00:00"}, mk(800.0), 1)
    b = snap.create_snapshot("slow_scan", {
        "biz_ref_id": "UT-SL2", "connection_id": "c-sl", "db_name": "d",
        "scan_finished_at": "2026-07-15 00:00:00"}, mk(2000.0), 1)
    r = cmp.run_compare([a, b], module="slow_scan")
    ch = r["changed"][0]["change"]
    assert ch["type"] == "PERF" and ch["direction"] == "UP" and ch["pct"] == 150.0


def test_t11_order_independent(pair):
    """T11 勾选顺序颠倒结果一致，base 恒为时间早的"""
    a, b = pair
    r1 = cmp.run_compare([a, b], module="schema_audit")
    r2 = cmp.run_compare([b, a], module="schema_audit")
    assert r1["summary"] == r2["summary"]
    assert r1["base"]["id"] == a and r1["target"]["id"] == b


# ── 可比性校验 ──

@pytest.mark.parametrize("ids,code", [
    ([1], "E4001"),
    ([1, 2, 3], "E4001"),
    ([], "E4001"),
])
def test_t08_must_be_exactly_two(ids, code):
    """T08 snapshot_ids 数量 ≠ 2 一律拒绝"""
    with pytest.raises(CompareError) as e:
        cmp.run_compare(ids, module="schema_audit")
    assert e.value.code == code and e.value.status == 400


def test_t09_cannot_compare_self(pair):
    """T09 不能与自身对比"""
    a, _ = pair
    with pytest.raises(CompareError) as e:
        cmp.run_compare([a, a], module="schema_audit")
    assert e.value.code == "E4002"


def test_t10_cross_instance_rejected(pair):
    """T10 跨实例比对被拒"""
    a, _ = pair
    other = _mk_snapshot("UT-OTHER", SCAN1, "2026-07-20 00:00:00", conn_id="c-other")
    with pytest.raises(CompareError) as e:
        cmp.run_compare([a, other], module="schema_audit")
    assert e.value.code == "E4003"


def test_t10b_snapshot_not_found():
    with pytest.raises(CompareError) as e:
        cmp.run_compare([1, 999999999], module="schema_audit")
    assert e.value.code == "E4004" and e.value.status == 404


def test_t12_time_window_warning():
    """T12 慢SQL 两次时间窗口差异 >2 倍时给出提示但不拦截"""
    it = [IssueItem(key=fp("w"), object_name="t", issue_type="SLOW", severity="ERROR", title="s")]
    a = snap.create_snapshot("slow_scan", {
        "biz_ref_id": "UT-W1", "connection_id": "c-w", "db_name": "d",
        "scan_finished_at": "2026-07-01 00:00:00",
        "time_window_start": "2026-06-30 00:00:00",
        "time_window_end": "2026-07-01 00:00:00"}, it, 1)
    b = snap.create_snapshot("slow_scan", {
        "biz_ref_id": "UT-W2", "connection_id": "c-w", "db_name": "d",
        "scan_finished_at": "2026-07-15 00:00:00",
        "time_window_start": "2026-07-15 00:00:00",
        "time_window_end": "2026-07-15 01:00:00"}, it, 1)
    r = cmp.run_compare([a, b], module="slow_scan")
    assert any("时间窗口" in w for w in r["warnings"])


# ── 快照生成 ──

def test_t13_snapshot_failure_is_swallowed():
    """T13 快照生成失败仅告警，不抛异常（保护扫描主流程）"""
    assert snap.safe_create_snapshot("BAD_MODULE", {}, [], 0) is None


def test_t14_idempotent():
    """T14 同一 biz_ref_id 生成两次只有一条记录"""
    it = [IssueItem(key=fp("i"), object_name="t", issue_type="R1", severity="ERROR", title="t")]
    meta = {"biz_ref_id": "UT-IDEM", "connection_id": "c-i", "db_name": "d",
            "scan_finished_at": "2026-07-01 00:00:00"}
    id1 = snap.create_snapshot("schema_audit", meta, it, 1)
    id2 = snap.create_snapshot("schema_audit", meta, it, 1)
    data = snap.list_snapshots(module="schema_audit", connection_id="c-i")
    assert data["total"] == 1
    assert id1 == id2 or snap.get_snapshot(id1, with_issues=False) is not None


def test_t15_truncation_keeps_high_severity(monkeypatch):
    """T15/T25 超限截断：按 ERROR 优先保留，truncated_count 正确"""
    monkeypatch.setattr(snap, "SNAPSHOT_MAX_ISSUES", 10)
    items = [IssueItem(key=fp("tr", str(i)), object_name=f"d.t{i}", issue_type="R1",
                       severity=("ERROR" if i < 4 else "WARNING"), title=f"i{i}")
             for i in range(25)]
    sid = snap.create_snapshot("schema_audit", {
        "biz_ref_id": "UT-TRUNC", "connection_id": "c-t", "db_name": "d",
        "scan_finished_at": "2026-08-01 00:00:00"}, items, 25)
    s = snap.get_snapshot(sid)
    assert s["truncated"] is True and s["truncated_count"] == 15
    assert len(s["issues"]) == 10
    assert [i["severity"] for i in s["issues"]][:4] == ["ERROR"] * 4


def test_t29_dedup_before_truncate(monkeypatch):
    """T29 先去重后截断：重复项不应挤占截断名额"""
    monkeypatch.setattr(snap, "SNAPSHOT_MAX_ISSUES", 10)
    dup = [IssueItem(key=fp("same"), object_name="d.x", issue_type="R1",
                     severity="ERROR", title="dup")] * 20
    uniq = [IssueItem(key=fp("u", str(i)), object_name=f"d.u{i}", issue_type="R1",
                      severity="WARNING", title="u") for i in range(5)]
    sid = snap.create_snapshot("schema_audit", {
        "biz_ref_id": "UT-DEDUP", "connection_id": "c-d", "db_name": "d",
        "scan_finished_at": "2026-08-02 00:00:00"}, dup + uniq, 25)
    s = snap.get_snapshot(sid)
    assert s["issue_total"] == 6
    assert s["truncated"] is False and s["truncated_count"] == 0


# ── 降级与截断语义区分 ──

def test_t26_degraded_on_broken_snapshot():
    """T26 D1' 快照明细损坏时退化为全量差集并打 degraded 标记"""
    it = [IssueItem(key=fp("g"), object_name="t", issue_type="R1", severity="ERROR", title="t")]
    good = snap.create_snapshot("schema_audit", {
        "biz_ref_id": "UT-GOOD", "connection_id": "c-g", "db_name": "d",
        "scan_finished_at": "2026-09-01 00:00:00"}, it, 1)
    bad = snap.create_snapshot("schema_audit", {
        "biz_ref_id": "UT-BAD", "connection_id": "c-g", "db_name": "d",
        "scan_finished_at": "2026-09-02 00:00:00"}, it, 1)
    from backend.services.database import _get_connection
    conn = _get_connection()
    try:
        conn.execute("UPDATE scan_snapshots SET snapshot_json = ? WHERE id = ?",
                     ('{"broken":1}', bad))
        conn.commit()
    finally:
        conn.close()
    r = cmp.run_compare([good, bad], module="schema_audit")
    assert r["summary"]["degraded"] is True
    assert r["summary"]["fixed_count"] == 1 and r["summary"]["remain_count"] == 0
    assert any("退化" in w or "损坏" in w for w in r["warnings"])


def test_t28_truncated_is_not_degraded(monkeypatch):
    """T28 仅截断不应标记 degraded（两种语义必须区分）"""
    monkeypatch.setattr(snap, "SNAPSHOT_MAX_ISSUES", 5)
    items = [IssueItem(key=fp("x", str(i)), object_name=f"d.t{i}", issue_type="R1",
                       severity="ERROR", title="t") for i in range(10)]
    a = snap.create_snapshot("schema_audit", {
        "biz_ref_id": "UT-TR-A", "connection_id": "c-tr", "db_name": "d",
        "scan_finished_at": "2026-09-10 00:00:00"}, items, 10)
    b = snap.create_snapshot("schema_audit", {
        "biz_ref_id": "UT-TR-B", "connection_id": "c-tr", "db_name": "d",
        "scan_finished_at": "2026-09-11 00:00:00"}, items, 10)
    r = cmp.run_compare([a, b], module="schema_audit")
    assert r["summary"]["degraded"] is False
    assert any("截断" in w for w in r["warnings"])


# ── 文案与报告 ──

def test_slow_scan_labels_differ():
    """慢SQL 的"消失"不等于"已修复"，文案必须区分"""
    assert cmp._labels_for("slow_scan")["fixed"] == "已消失（未复现）"
    assert cmp._labels_for("schema_audit")["fixed"] == "已修复"
    assert cmp._labels_for("bigtable")["fixed"] == "已修复"


def test_t18_report_escapes_xss():
    """T18 报告对恶意对象名正确转义"""
    evil = "<script>alert(1)</script>"
    it = [IssueItem(key=fp("x"), object_name=evil, issue_type="R1",
                    severity="ERROR", title=f"标题{evil}")]
    a = snap.create_snapshot("schema_audit", {
        "biz_ref_id": "UT-XSS1", "connection_id": "c-x", "db_name": "d",
        "scan_finished_at": "2026-09-20 00:00:00"}, it, 1)
    b = snap.create_snapshot("schema_audit", {
        "biz_ref_id": "UT-XSS2", "connection_id": "c-x", "db_name": "d",
        "scan_finished_at": "2026-09-21 00:00:00"}, [], 0)
    html = render_compare_html(cmp.run_compare([a, b], module="schema_audit"))
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_report_is_self_contained(pair):
    """报告自包含：无外部资源引用，含打印样式"""
    a, b = pair
    html = render_compare_html(cmp.run_compare([a, b], module="schema_audit"))
    assert 'src="http' not in html and 'href="http' not in html
    assert "@import" not in html
    assert "@media print" in html
    assert "整改率" in html


# ── 回填 ──

def test_t16_rebuild_from_legacy_and_idempotent():
    """T16 存量回填：能从 audit_history 补建快照，且重复调用幂等"""
    from backend.services.database import _get_connection
    conn = _get_connection()
    try:
        conn.execute("""
            INSERT INTO audit_history(audit_type, source, total_sql, passed, failed,
                error_count, warning_count, pass_rate, results_json, created_by,
                created_at, connection_id, db_name)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, ("extracted_schema", "ut_legacy.sql", 3, 0, 3, 2, 1, 0.0, SCAN1,
              "pytest", "2026-06-01 03:00:00", "c-legacy", "legacy_db"))
        conn.commit()
    finally:
        conn.close()

    r1 = snap.rebuild_snapshots("schema_audit", limit=50)
    assert r1["failed"] == 0 and r1["created"] >= 1

    r2 = snap.rebuild_snapshots("schema_audit", limit=50)
    assert r2["failed"] == 0
    assert r2["created"] == 0 and r2["skipped"] >= 1   # 幂等：第二次全部跳过

    # 回填的快照可被按实例检索，且标记为 rebuild
    data = snap.list_snapshots(module="schema_audit", connection_id="c-legacy")
    assert data["total"] >= 1
    assert all(i["source_kind"] == "rebuild" for i in data["items"])


def test_rebuild_rejects_bad_module():
    with pytest.raises(ValueError):
        snap.rebuild_snapshots("nope")


# ── 列表筛选 ──

def test_list_filter_by_instance(pair):
    """按实例筛选只返回该实例快照（需求 G2）"""
    a, b = pair
    data = snap.list_snapshots(module="schema_audit", connection_id="c-test")
    ids = {i["id"] for i in data["items"]}
    assert {a, b} <= ids
    assert all(i["connection_id"] == "c-test" for i in data["items"])
