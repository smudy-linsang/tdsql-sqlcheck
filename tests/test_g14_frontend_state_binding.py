# -*- coding: utf-8 -*-
"""G14 前端结果绑定静态门禁（v1.6.3.0-UAT-O-G14-01 / O-G14-03）。

第一轮 UAT 的 BLOCK：统计结果保存在全局 deepResult.tabletype，未绑定
用户/实例/查询条件——查询失败、切换实例、退出换用户后旧结果仍显示，
且换用户后能看到前一用户的实例名与统计结果（会话隔离失效）。

本用例是静态门禁：删掉下列任一清理点/防护点即红灯。
本项目前端无单元测试框架，故以源码形态断言钉住关键控制点；
行为级证据由真实浏览器 UAT 路径补充（见 UAT 报告 §5.1 复测清单）。
"""
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_APP_JS = (_REPO / "frontend" / "static" / "js" / "app.js").read_text(encoding="utf-8")
_INDEX = (_REPO / "frontend" / "index.html").read_text(encoding="utf-8")


def _fn_body(name: str) -> str:
    """截取 `const name=...` 到下一个顶层 `const ...=（async）...` 声明之间的文本。"""
    m = re.search(rf"const {re.escape(name)}=(.*?)(?=\n    const \w+=|\n    // G10)",
                  _APP_JS, re.S)
    assert m, f"app.js 中未找到 {name}"
    return m.group(1)


def test_reset_function_clears_all_eight_state_points():
    """resetTableTypeState 必须一次性清理全部 8 个状态点（缺一即残留）。"""
    body = _fn_body("resetTableTypeState")
    for point in ("deepResult.tabletype=null", "tabletypeWarnAll.value=false",
                  "tabletypeHistoryVisible.value=false", "tabletypeHistory.value=[]",
                  "tabletypeDetailItems.value=[]", "tabletypeDetailAll.value=[]",
                  "tabletypeDetailExpand.value=false", "tabletypeDetailLoading.value=false"):
        assert point in body, f"resetTableTypeState 缺少清理点: {point}"


def test_run_invalidates_old_result_before_request():
    """查询开始即失效旧结果：reset 必须先于请求，且失败时不得恢复。"""
    body = _fn_body("runTableTypeStats")
    reset_pos = body.find("resetTableTypeState()")
    post_pos = body.find("apiFetch(")
    assert reset_pos != -1 and post_pos != -1 and reset_pos < post_pos, \
        "runTableTypeStats 必须先 resetTableTypeState() 再发起请求"


def test_uat2_stale_error_toast_is_suppressed():
    """UAT2-O-G14-01：迟到请求的【错误提示】也必须作废，不能只丢弃数据。

    报告的根因：错误提示在通用 _deepPost 内部、序号检查之前就已弹出。
    修复后 runTableTypeStats 自带请求——响应/异常回来先判 isCurrent，
    过期则静默 return，不得出现任何成功/失败提示。
    """
    body = _fn_body("runTableTypeStats")
    # 不再委托共用 _deepPost（其内部先行弹错，迟到错误无法撤回）
    assert "_deepPost(" not in body, "runTableTypeStats 不得再委托共用 _deepPost 发请求"
    assert "apiFetch(" in body, "应自带 apiFetch 请求"
    # 错误提示之前必须先有 isCurrent 判定（顺序断言）
    for branch_pos in (body.find("if(!resp.ok)"), body.find("}catch")):
        assert branch_pos != -1
        assert body.rfind("isTableTypeRequestCurrent(mySeq,scope)", 0, branch_pos) != -1, \
            "每个提示分支之前都必须先判 isTableTypeRequestCurrent"
    # 成功 toast 前同样要有数据守卫
    assert "ElementPlus.ElMessage" in body


def test_uat2_loading_has_independent_ownership():
    """UAT2-O-G14-01：loading 独立且按序号归属——旧请求 finally 不得误关新请求。"""
    assert "const tabletypeLoading=ref(false)" in _APP_JS
    body = _fn_body("runTableTypeStats")
    assert "tabletypeLoading.value=true" in body, "请求开始必须置 loading"
    fin = body.find("finally{")
    assert fin != -1
    assert "mySeq===tabletypeSeq.value" in body[fin:], \
        "finally 里必须以序号守卫关闭 loading"
    # 页面按钮改用独立 loading
    assert ':loading="tabletypeLoading"' in _INDEX
    # watch 与登录态清理都要释放 loading（切换后不被旧请求锁住按钮）
    w = re.search(r"watch\(\[deepConnId,deepDb\],(.*?)\}\);", _APP_JS, re.S).group(1)
    assert "tabletypeLoading.value=false" in w, "watch 未释放 loading"
    cr = _fn_body("clearRoleScopedState")
    assert "tabletypeLoading.value=false" in cr, "clearRoleScopedState 未释放 loading"
    # 返回清单登记（模板否则静默取 undefined）
    m = re.search(r"return\{(.+?)\};\s*\n\s*}\s*\n\}\)", _APP_JS, re.S)
    assert re.search(r"\btabletypeLoading\b", m.group(1))


def test_uat2_scope_text_never_blank_tail():
    """UAT2-O-G14-02：范围行缺失采集时间时必须明示，不得留下空白尾段。"""
    body = _fn_body("tabletypeScopeText")
    assert "采集时间不可用" in body
    assert "formatTime(r.created_at||'')" not in body, "缺失时间不得再格式化成空串"


def test_run_uses_sequence_guard_against_late_response():
    """防异步串台：每次统计递增序号，响应回来时经 isTableTypeRequestCurrent
    校验序号与范围快照（UAT2 起判定收敛为独立纯函数）。"""
    body = _fn_body("runTableTypeStats")
    assert "tabletypeSeq.value+=1" in body, "缺少请求序号递增"
    assert "isTableTypeRequestCurrent(mySeq,scope)" in body, "缺少迟到响应丢弃判断"
    assert "r._scope=scope" in body, "响应必须携带发起时的范围快照"
    # 判定函数本身：序号 + 用户 + 实例 + 库 四维度
    isf = _fn_body("isTableTypeRequestCurrent")
    assert "mySeq!==tabletypeSeq.value" in isf
    for cond in ("n.username===scope.username", "n.connectionId===scope.connectionId",
                 "n.database===scope.database"):
        assert cond in isf, f"范围快照比对缺少: {cond}"


def test_watch_clears_result_on_conn_or_db_change():
    """实例或库名变化即失效旧结果，实例变化时还要关闭并清空历史抽屉。"""
    m = re.search(r"watch\(\[deepConnId,deepDb\],(.*?)\}\);", _APP_JS, re.S)
    assert m, "缺少 watch([deepConnId,deepDb])"
    body = m.group(1)
    assert "tabletypeSeq.value+=1" in body
    assert "deepResult.tabletype=null" in body
    for point in ("tabletypeHistoryVisible.value=false", "tabletypeHistory.value=[]",
                  "tabletypeDetailItems.value=[]", "tabletypeDetailAll.value=[]"):
        assert point in body, f"实例变化时的历史清理缺少: {point}"


def test_clear_role_scoped_state_isolates_g14_context():
    """登录态彻底隔离：连接、深度诊断上下文、G14 状态与 localStorage 全部清理。"""
    body = _fn_body("clearRoleScopedState")
    for point in ("savedConnections.value=[]", "currentConnectionId.value=''",
                  "deepConnId.value=''", "deepRightConnId.value=''",
                  "deepDb.value=''", "deepLoading.value=''",
                  "deepResult.tabletype=null", "resetTableTypeState()",
                  "localStorage.removeItem('tdsql_conn')"):
        assert point in body, f"clearRoleScopedState 缺少隔离点: {point}"


def test_display_binds_scope_and_shows_range_text():
    """展示绑定范围：模板只允许渲染范围匹配的结果，且明确显示结果范围。"""
    assert "const tabletypeView=computed(()=>tabletypeScopeMatch.value" in _APP_JS
    assert "tabletypeScopeMatch" in _APP_JS
    for mark in ('v-if="tabletypeView"', ":data=\"tabletypeView.items\"",
                 "结果范围：{{ tabletypeScopeText }}"):
        assert mark in _INDEX, f"index.html 缺少范围绑定: {mark}"
    # 实时结果区不得再直接引用 deepResult.tabletype（历史抽屉不在此列）
    tab = _INDEX.split('<!-- 表类型统计 G14 -->', 1)[1].split("<!-- 结构比对 G6 -->", 1)[0]
    assert "deepResult.tabletype" not in tab, \
        "表类型统计页签内不得再直接引用 deepResult.tabletype（一律经 tabletypeView）"


def test_auditor_button_disabled_with_hint():
    """UAT-O-G14-03：auditor 按钮禁用并提示；后端 403 仍是最终防线。"""
    assert "canRunTableTypeStats=computed" in _APP_JS
    assert "authState.role!=='auditor'" in _APP_JS
    assert "审计员仅可查看历史" in _INDEX
    assert 'v-if="!canRunTableTypeStats"' in _INDEX


def test_api_error_message_reads_both_detail_and_message():
    """统一错误文案提取：字符串 detail → message → FastAPI 校验数组首条。"""
    body = _fn_body("apiErrorMessage")
    assert "typeof d==='string'" in body, "缺少字符串 detail 分支"
    assert "typeof data.message==='string'" in body, "缺少 message 分支"
    assert "Array.isArray(d)" in body, "缺少 FastAPI 校验数组分支"
    # 三处调用点统一使用该函数
    assert "apiErrorMessage(d,'执行失败')" in _APP_JS, "_deepPost 未接入"
    assert "apiErrorMessage(d,'加载历史失败')" in _APP_JS, "历史加载未接入"
    assert "apiErrorMessage(d,'加载明细失败')" in _APP_JS, "明细加载未接入"


def test_new_symbols_registered_in_setup_return():
    """模板引用的三个新标识符必须在 setup() 返回清单中，否则模板静默取空。"""
    m = re.search(r"return\{(.+?)\};\s*\n\s*}\s*\n\}\)", _APP_JS, re.S)
    assert m, "未找到 setup() 返回清单"
    ret = m.group(1)
    for name in ("tabletypeView", "tabletypeScopeText", "canRunTableTypeStats"):
        assert re.search(rf"\b{name}\b", ret), f"setup() 返回清单缺少 {name}"
