# -*- coding: utf-8 -*-
"""UAT-O-16 回归守护：巡检比对结果绑定实例/日期/状态，失败或切换时不残留旧结果

结构守卫（与 O 第四轮报告整改建议对齐）：
1. 结果绑定 {connection_id,date1,date2,generated_at,status}；
2. 模板只有在范围完全一致且状态成功时才显示（dailyResultVisible）；
3. 新任务开始/比对失败/异常统一执行 resetDailyResult()。
"""
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _js() -> str:
    return (_ROOT / "frontend" / "static" / "js" / "app.js").read_text(encoding="utf-8")


def _html() -> str:
    return (_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")


def test_scope_binding_exists():
    js = _js()
    assert "const dailyCompareScope=ref(null);" in js, "缺少结果范围绑定状态"
    assert "connection_id: deepConnId.value, date1: d1, date2: d2" in js, \
        "比对成功时必须把结果绑定到当前实例与日期范围"
    assert 'status: "success"' in js, "结果范围必须携带成功状态"
    assert "generated_at: new Date().toISOString()" in js, "结果范围必须携带生成时刻"


def test_visibility_gated_by_scope_match():
    js = _js()
    assert "const dailyResultVisible=computed(" in js, "缺少范围匹配可见性计算属性"
    assert "sc.connection_id===deepConnId.value" in js, "可见性必须校验实例一致"
    assert "sc.date1===ds[0]&&sc.date2===ds[1]" in js, "可见性必须校验日期范围一致"
    assert "sc.status!=='success'" in js, "非成功状态的结果不得展示"


def test_reset_on_task_boundaries():
    js = _js()
    assert "const resetDailyResult=()" in js, "缺少统一清理函数"
    # 新采集任务开始与重新比对都必须先清空旧结果
    assert js.count("resetDailyResult();") >= 2, "采集开始与比对开始均需执行清理"
    # 清理必须覆盖结果、趋势图、节点筛选与分页
    for token in ("dailyCompareResult.value=null", "dailyCompareScope.value=null",
                  "dailyInspectChartData.value=null",
                  "dailyInstNodeSelect.value=''", "dailySrvIpSelect.value=''",
                  "dailyInstPage.value=1", "dailySrvPage.value=1"):
        assert token in js, f"resetDailyResult 缺少清理项: {token}"


def test_template_uses_scope_gating():
    html = _html()
    assert 'v-if="dailyResultVisible"' in html, "比对差异表格必须由范围匹配结果门控"
    assert 'v-show="dailyInspectChartData && dailyResultVisible"' in html, \
        "趋势图必须由范围匹配结果门控"
    assert 'v-if="dailyCompareResult"' not in html, "不得再以裸结果存在性作为展示条件"


def test_setup_exposes_guards():
    js = _js()
    assert "dailyCompareResult,dailyResultVisible,resetDailyResult," in js, \
        "setup 必须暴露范围门控与清理函数"
