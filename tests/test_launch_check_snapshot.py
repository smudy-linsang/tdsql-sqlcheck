# -*- coding: utf-8 -*-
"""V1.5.2 上线检查历史保留与对比测试

覆盖 DESIGN-v1.5.2 §8：判定表一致性、指纹红线（度量不进指纹）、
C01 聚合行、CHANGED 判定、可比性校验（E4008）、回填拒绝（E4009）、
主流程隔离（G6）、保留策略（D5）。
"""
from unittest.mock import MagicMock, patch

import pytest

from backend.services import scan_compare_service as cmp_svc
from backend.services import scan_snapshot_service as snap_svc
from backend.services.scan_compare_service import CompareError, detect_change, validate_pair
from backend.services.snapshot_extractors.launch_check import extract, _CHECK_SPEC


# ════════════════════════════════════════════════════════════
# §8.2 判定表一致性（锁定设计 §7.2）
# ════════════════════════════════════════════════════════════

def test_check_spec_covers_all_12_checks():
    """_CHECK_SPEC 必须覆盖 SchemaInspector 的全部检查项。

    新增检查项（C13+）时本用例会失败 —— 这是有意的：
    必须显式决定新项的指纹区分位与度量位，不能靠兜底蒙混过关。
    """
    from backend.engine.schema_inspector import SchemaInspector
    actual = {c["id"] for c in SchemaInspector.CHECKS}
    assert actual == set(_CHECK_SPEC), (
        f"未登记: {actual - set(_CHECK_SPEC)}，多余: {set(_CHECK_SPEC) - actual}")


def test_metrics_never_enter_fingerprint():
    """【红线】度量值变化不得改变指纹 —— 否则制造虚假整改。"""
    def _mk(idx_count):
        return [{"id": "C06", "name": "索引数量>=5的表", "severity": "WARNING",
                 "suggestion": "", "count": 1,
                 "rows": [{"数据库": "db1", "表名": "t1", "索引数": idx_count}]}]
    k5 = extract(_mk(5))[0][0].key
    k8 = extract(_mk(8))[0][0].key
    assert k5 == k8, "索引数进了指纹：5→8 会被误判为「已解决+新增」"
    assert extract(_mk(8))[0][0].attrs["索引数"] == 8.0


@pytest.mark.parametrize("cid,metric", [
    ("C05", "字符数"), ("C11", "字段数"),
])
def test_other_metric_checks_never_enter_fingerprint(cid, metric):
    """C05 字符数 / C11 字段数 同样不得进指纹。"""
    def _mk(v):
        return [{"id": cid, "name": cid, "severity": "WARNING",
                 "suggestion": "", "count": 1,
                 "rows": [{"数据库": "db1", "表名": "t1", metric: v}]}]
    assert extract(_mk(40))[0][0].key == extract(_mk(80))[0][0].key
    assert extract(_mk(80))[0][0].attrs[metric] == 80.0


def test_c01_aggregate_row_uses_collation_as_discriminator():
    """C01 是唯一的聚合行（无表名），须用排序规则区分，表数量不得进指纹。"""
    def _mk(collation, cnt):
        return [{"id": "C01", "name": "字符编码非utf8mb4的表", "severity": "WARNING",
                 "suggestion": "", "count": cnt,
                 "rows": [{"数据库": "db1", "排序规则": collation, "表数量": cnt}]}]
    # 同排序规则、表数量不同 → 同一问题项
    assert extract(_mk("latin1_swedish_ci", 12))[0][0].key == \
           extract(_mk("latin1_swedish_ci", 15))[0][0].key
    # 不同排序规则 → 不同问题项
    assert extract(_mk("latin1_swedish_ci", 12))[0][0].key != \
           extract(_mk("gbk_chinese_ci", 12))[0][0].key
    # 表数量进 attrs（数值化）
    assert extract(_mk("latin1_swedish_ci", 12))[0][0].attrs["表数量"] == 12.0


# ════════════════════════════════════════════════════════════
# §8.3 抽取与对比
# ════════════════════════════════════════════════════════════

_FULL_SAMPLE = [
    {"id": "C07", "name": "无主键的表", "severity": "ERROR", "suggestion": "加主键",
     "count": 2, "rows": [{"数据库": "db1", "表名": "t1"},
                          {"数据库": "db1", "表名": "t2"}]},
    {"id": "C09", "name": "无注释的列", "severity": "INFO", "suggestion": "",
     "count": 2, "rows": [{"数据库": "db1", "表名": "t1", "列名": "c1", "当前注释": ""},
                          {"数据库": "db1", "表名": "t1", "列名": "c2", "当前注释": ""}]},
]


def test_extract_fingerprint_stability():
    """同一份结果抽两次，指纹完全一致。"""
    keys1 = [i.key for i in extract(_FULL_SAMPLE)[0]]
    keys2 = [i.key for i in extract(_FULL_SAMPLE)[0]]
    assert keys1 == keys2 and len(keys1) == 4


def test_column_level_checks_distinguish_columns():
    """C02/C04/C08/C09/C12 同表不同列须产出不同指纹。"""
    items, _ = extract(_FULL_SAMPLE)
    c09_keys = [i.key for i in items if i.issue_type == "C09"]
    assert len(c09_keys) == len(set(c09_keys)) == 2


def test_object_total_counts_distinct_tables():
    """object_total 取去重后的 库.表 数，不是问题项数。"""
    items, obj_total = extract(_FULL_SAMPLE)
    assert len(items) == 4
    assert obj_total == 2      # db1.t1 / db1.t2


def test_failed_check_produces_no_items():
    """执行失败的检查项（error 非空）不产出问题项，
    避免把「查不了」记成「没问题」。"""
    results = [{"id": "C07", "name": "无主键的表", "severity": "ERROR",
                "suggestion": "", "count": 0, "error": "timeout",
                "rows": [{"数据库": "db1", "表名": "t1"}]}]
    items, _ = extract(results)
    assert items == []


def test_unregistered_check_falls_back_conservatively():
    """未登记检查项（C13+）兜底：全部剩余列进 attrs，不进指纹。"""
    def _mk(extra_v):
        return [{"id": "C99", "name": "新检查项", "severity": "WARNING",
                 "suggestion": "", "count": 1,
                 "rows": [{"数据库": "db1", "表名": "t1", "某度量": extra_v}]}]
    assert extract(_mk(1))[0][0].key == extract(_mk(99))[0][0].key
    assert extract(_mk(99))[0][0].attrs["某度量"] == 99


def test_changed_on_index_growth():
    """C06 索引数 5→8（+60% ≥ 20%）判为 CHANGED/GROWTH，而非解决+新增。"""
    ob = {"severity": "WARNING", "attrs": {"索引数": 5.0}}
    ot = {"severity": "WARNING", "attrs": {"索引数": 8.0}}
    ch = detect_change("launch_check", ob, ot)
    assert ch and ch["type"] == "GROWTH" and ch["field"] == "索引数"
    assert ch["direction"] == "UP" and ch["pct"] == 60.0


def test_no_change_below_threshold():
    """度量变化低于 20% 阈值不产出 CHANGED（如 10→11 为 +10%）。"""
    ob = {"severity": "WARNING", "attrs": {"字段数": 10.0}}
    ot = {"severity": "WARNING", "attrs": {"字段数": 11.0}}
    assert detect_change("launch_check", ob, ot) is None


def test_changed_on_attr_diff():
    """C08 类型 varchar(600)→varchar(800) 判为 CHANGED/ATTR。"""
    ob = {"severity": "WARNING", "attrs": {"类型": "varchar(600)"}}
    ot = {"severity": "WARNING", "attrs": {"类型": "varchar(800)"}}
    ch = detect_change("launch_check", ob, ot)
    assert ch and ch["type"] == "ATTR" and ch["field"] == "类型"
    assert ch["old"] == "varchar(600)" and ch["new"] == "varchar(800)"


# ════════════════════════════════════════════════════════════
# §8.4 可比性与拒绝路径
# ════════════════════════════════════════════════════════════

def _snap(sid, db_name, module="launch_check"):
    return {"id": sid, "module": module, "connection_id": "conn1",
            "db_name": db_name, "fingerprint_algo": "v1",
            "rule_set_id": None, "scan_finished_at": f"2026-07-3{sid}",
            "truncated": False, "issues": [], "source_kind": "live"}


def test_reject_different_scope():
    """全部数据库 vs 单库 → E4008 拒绝，且错误文案含两侧实际范围。"""
    s_all, s_one = _snap(0, ""), _snap(1, "dbA")
    with patch.object(cmp_svc.snapshot_service, "get_snapshot",
                      side_effect=lambda sid, with_issues=True:
                      s_all if int(sid) == 100 else s_one):
        with pytest.raises(CompareError) as e:
            validate_pair([100, 101], "launch_check")
    assert e.value.code == "E4008"
    assert e.value.status == 409
    assert "全部数据库" in e.value.message and "dbA" in e.value.message


def test_same_scope_compares_normally():
    """范围相同 → 正常通过校验；且不误报"产生于 V1.4 之前"警告
    （launch_check 不走规则集，rule_set_id 恒为 NULL）。"""
    s1, s2 = _snap(0, "dbA"), _snap(1, "dbA")
    with patch.object(cmp_svc.snapshot_service, "get_snapshot",
                      side_effect=lambda sid, with_issues=True:
                      s1 if int(sid) == 100 else s2):
        base, target, warnings = validate_pair([100, 101], "launch_check")
    assert base["id"] == 0 and target["id"] == 1
    assert not any("V1.4" in w for w in warnings), (
        "launch_check 快照 rule_set_id 恒为 NULL，不应触发尺度未知警告")


def test_rebuild_explicitly_rejected():
    """回填必须显式拒绝并说明原因，不得静默返回空结果 ——
    静默返回会让人以为「回填过了只是没数据」，从而误信后续对比结论。"""
    with pytest.raises(ValueError, match="不支持存量回填"):
        snap_svc.rebuild_snapshots("launch_check")


def test_module_registered():
    """launch_check 已注册进快照框架模块清单。"""
    assert "launch_check" in snap_svc.MODULES


def test_compare_api_module_menu_registered():
    """对比 API 的模块白名单/越权映射已登记（设计 §6.1 落地补齐）。"""
    from backend.api.scan_compare import _MODULE_MENU
    assert _MODULE_MENU.get("launch_check") == "schema-check"


# ════════════════════════════════════════════════════════════
# §8.5 主流程隔离（G6）
# ════════════════════════════════════════════════════════════

def _run_schema_check_with(monkeypatch, snapshot_patch):
    """构造最小依赖执行 run_schema_check，返回响应 data。"""
    from backend.api import inspection as insp
    from backend.models import SchemaCheckRequest
    from backend.services.connection_registry import registry
    from backend.engine.schema_inspector import SchemaInspector
    from backend.services.instance_type_service import instance_type_service

    results = [{"id": "C07", "name": "无主键的表", "severity": "ERROR",
                "suggestion": "加主键", "count": 1,
                "rows": [{"数据库": "db1", "表名": "t1"}],
                "columns": ["数据库", "表名"]}]
    monkeypatch.setattr(registry, "get", lambda cid: MagicMock())
    monkeypatch.setattr(registry, "get_saved", lambda cid: {"name": "测试实例"})
    monkeypatch.setattr(SchemaInspector, "inspect",
                        lambda self, pool, f, it=None: results)
    monkeypatch.setattr(SchemaInspector, "get_summary",
                        lambda self, r: {"total": 1, "error": 1, "warning": 0,
                                         "info": 0, "checks_passed": 11,
                                         "checks_failed": 0})
    monkeypatch.setattr(instance_type_service, "resolve",
                        lambda cid, requested=None: MagicMock(
                            instance_type=MagicMock(value="distributed")))
    monkeypatch.setattr(insp._service, "create_task", lambda cid, t: 999)
    monkeypatch.setattr(insp._service, "update_task_status",
                        lambda *a, **k: None)
    monkeypatch.setattr(insp._service, "save_result", lambda *a, **k: None)
    snapshot_patch(monkeypatch)

    req = SchemaCheckRequest(connection_id="conn_x", database_filter="")
    resp = insp.run_schema_check(req, MagicMock())
    return resp.data


def test_snapshot_failure_does_not_break_check(monkeypatch):
    """快照创建失败时，上线检查照常返回结果，snapshot_id=None。"""
    def _patch(mp):
        mp.setattr(snap_svc, "safe_create_snapshot",
                   lambda *a, **k: (_ for _ in ()).throw(Exception("boom")))
    data = _run_schema_check_with(monkeypatch, _patch)
    assert data["results"]                    # 检查结果完整返回
    assert data["snapshot_id"] is None
    assert data["snapshot_error"]


def test_extractor_exception_does_not_break_check(monkeypatch):
    """抽取器抛异常同样不得影响主流程（safe_create_snapshot 之外的一层）。"""
    def _patch(mp):
        from backend.services.snapshot_extractors import launch_check as lc
        mp.setattr(lc, "extract",
                   lambda *a, **k: (_ for _ in ()).throw(Exception("extract boom")))
    data = _run_schema_check_with(monkeypatch, _patch)
    assert data["results"]
    assert data["snapshot_id"] is None
    assert "extract boom" in data["snapshot_error"]


def test_snapshot_created_with_full_results(monkeypatch):
    """快照旁路正常时返回 snapshot_id，且抽取的是完整 results。"""
    captured = {}
    def _patch(mp):
        def _fake_snap(module, meta, items, object_total=0, source_kind="live"):
            captured["module"] = module
            captured["meta"] = meta
            captured["items"] = items
            return 4021
        mp.setattr(snap_svc, "safe_create_snapshot", _fake_snap)
    data = _run_schema_check_with(monkeypatch, _patch)
    assert data["snapshot_id"] == 4021
    assert data["snapshot_error"] == ""
    assert captured["module"] == "launch_check"
    assert captured["meta"]["biz_ref_id"] == "999"
    assert captured["meta"]["db_name"] == ""          # 空串=全部数据库
    assert len(captured["items"]) == 1                # 完整 results 抽取


# ════════════════════════════════════════════════════════════
# §8.6 保留策略（D5）
# ════════════════════════════════════════════════════════════

def test_inspection_tasks_in_cleanable_tables():
    from backend.services.retention_service import CLEANABLE_TABLES
    assert "inspection_tasks" in CLEANABLE_TABLES


def test_inspection_results_not_registered_separately():
    """results 靠外键级联清理。单独登记会按自身 created_at 清，
    留下「任务还在、明细被删一半」的残缺记录，比不清理更糟。"""
    from backend.services.retention_service import CLEANABLE_TABLES
    assert "inspection_results" not in CLEANABLE_TABLES


def test_cascade_deletes_results():
    """删 inspection_tasks 后对应 inspection_results 应一并消失。"""
    from backend.services.database import _get_connection, ensure_db
    ensure_db()
    conn = _get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO inspection_tasks (connection_id, inspection_type, status) "
            "VALUES (?, 'schema_check', 'completed')", ("v152_cascade_test",))
        task_id = cur.lastrowid
        conn.execute(
            "INSERT INTO inspection_results (task_id, category, severity, message) "
            "VALUES (?, 'C07', 'ERROR', 'test')", (task_id,))
        conn.commit()

        conn.execute("DELETE FROM inspection_tasks WHERE id = ?", (task_id,))
        conn.commit()
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM inspection_results WHERE task_id = ?",
            (task_id,)).fetchone()
        assert dict(row)["c"] == 0, "级联删除未生效：明细成为孤儿记录"
    finally:
        try:
            conn.execute(
                "DELETE FROM inspection_tasks WHERE connection_id = 'v152_cascade_test'")
            conn.commit()
            conn.close()
        except Exception:
            pass
