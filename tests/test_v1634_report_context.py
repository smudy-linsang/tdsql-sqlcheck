# -*- coding: utf-8 -*-
"""v1.6.3.4 / D01+D02 报告实例标识 SIT 回归（DETAIL-v1.6.3.4 §8.1 REP-12—15）。

核心不变量：
  · REP-12 冻结：capture 在受理时冻结名称，改名/删除后重新导出同一记录仍显示旧名。
  · REP-13 门禁不被顺带打开：默认审核 evaluate_gate=False → gate_passed 写 NULL。
  · REP-14 离线/显式绑定分流：未绑定显示"未关联（离线文件审核）"；显式绑定冻结真名。
  · REP-15 注入安全：恶意连接名（脚本/on* 属性）被转义，无可执行属性。
"""
import re
import types

import pytest

from backend.services import report_context as rc
from backend.services import connection_registry
from backend.services import audit_service


def _patch_saved(monkeypatch, mapping):
    """让 registry.get_saved(connection_id) 返回 mapping.get(connection_id)。"""
    monkeypatch.setattr(connection_registry.registry, "get_saved",
                        lambda cid: mapping.get(cid))


# ════════════════════════════════════════════════════════════════════════════
# REP-12：改名后重新导出同一记录，仍显示旧名（冻结），新名出现 0 次
# ════════════════════════════════════════════════════════════════════════════
def test_rep12_frozen_name_survives_rename(monkeypatch):
    _patch_saved(monkeypatch, {"c1": {"name": "原名-NX", "database": "db1"}})
    ctx = rc.capture_report_context("c1", "db1", rc.ORIGIN_BOUND)
    stored = rc.context_to_json_column(ctx)
    assert stored, "capture 应产出 report_context_json"

    # 之后把连接改名为新名（注册表现名已变）
    _patch_saved(monkeypatch, {"c1": {"name": "新名-NY", "database": "db1"}})

    # 重新导出同一记录（记录含 connection_id，若冻结失效退化为现名查询会显示 NY）
    html = rc.render_for_record(
        {"report_context_json": stored, "connection_id": "c1"}, scene="在线元数据审核")
    assert "原名-NX" in html
    assert "新名-NY" not in html, "改名后重新导出不得反查现名（冻结失败）"


def test_rep12_frozen_name_survives_delete(monkeypatch):
    _patch_saved(monkeypatch, {"c1": {"name": "原名-NX", "database": "db1"}})
    stored = rc.context_to_json_column(
        rc.capture_report_context("c1", "db1", rc.ORIGIN_BOUND))
    _patch_saved(monkeypatch, {})
    html = rc.render_for_record({"report_context_json": stored}, scene="在线元数据审核")
    assert "原名-NX" in html, "删除连接后历史报告仍须显示冻结的原名"


def test_rep12_offline_no_context_does_not_fabricate(monkeypatch):
    _patch_saved(monkeypatch, {})
    html = rc.render_for_record({}, scene="离线文件审核")
    assert "未关联" in html


# ════════════════════════════════════════════════════════════════════════════
# REP-13：默认审核不评估门禁 → audit_history.gate_passed 写 NULL，report_context 落库
# ════════════════════════════════════════════════════════════════════════════
def test_rep13_gate_not_evaluated_writes_null_and_context(monkeypatch):
    captured = {}

    class _Cur:
        def execute(self, sql, params):
            captured["params"] = params

        @property
        def lastrowid(self):
            return 42

    class _Conn:
        def cursor(self):
            return _Cur()

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(audit_service, "ensure_db", lambda: None)
    monkeypatch.setattr(audit_service, "_get_connection", lambda: _Conn())

    summary = types.SimpleNamespace(total_sql=1, passed=1, failed=0,
                                    error_count=0, warning_count=0, pass_rate=100.0)
    ctx = rc.ReportContext(connections=[rc.ConnectionContext(
        connection_id="c1", connection_name="原名-NX",
        name_source=rc.NAME_SOURCE_SNAPSHOT, db_name="db1")])

    rid = audit_service._save_audit_history(
        "extracted_schema", "f.sql", [], summary, created_by="tester",
        gate_result=None, connection_id="c1", db_name="db1",
        report_context=ctx)

    assert rid == 42
    params = captured["params"]
    # INSERT 列序：..., gate_passed(索引11), gate_detail(12), ..., report_context_json(20)
    assert params[11] is None, "未评估门禁时 gate_passed 必须为 NULL（不能写 0/1）"
    assert params[12] == "", "未评估门禁时 gate_detail 为空串"
    assert params[20], "report_context_json 必须落库"
    assert "原名-NX" in params[20]


# ════════════════════════════════════════════════════════════════════════════
# REP-14：H01 前端未绑定（offline）显示未关联；API 显式绑定冻结真名
# ════════════════════════════════════════════════════════════════════════════
def test_rep14_offline_shows_unassociated():
    html = rc.render_for_record({}, scene="离线文件审核")
    assert "未关联" in html
    assert "离线文件审核" in html


def test_rep14_explicit_connection_id_freezes_name(monkeypatch):
    _patch_saved(monkeypatch, {"c9": {"name": "显式绑定库", "database": "dbx"}})
    ctx = rc.capture_report_context("c9", "dbx", rc.ORIGIN_BOUND)
    html = rc.render_for_record(
        {"report_context_json": rc.context_to_json_column(ctx)}, scene="文件审核")
    assert "显式绑定库" in html
    assert "未关联" not in html


# ════════════════════════════════════════════════════════════════════════════
# REP-15：恶意连接名转义（XSS），无可执行 on* 属性
# ════════════════════════════════════════════════════════════════════════════
def test_rep15_malicious_connection_name_escaped():
    malicious = "<script>alert(1)</script>\"onload=x&<img src=y onerror=z>"
    ctx = rc.ReportContext(connections=[rc.ConnectionContext(
        connection_id="c1", connection_name=malicious,
        name_source=rc.NAME_SOURCE_SNAPSHOT, db_name="db1")])
    html = rc.render_report_context(ctx)
    assert "<script>" not in html
    assert "<img" not in html                       # 标签起始 < 被转义，标签不成立
    assert "&lt;script&gt;" in html
    # 关键：任何未转义的真实标签（<tag ...>）都不得携带可执行 on* 属性。
    # （被转义的 "&lt;img src=y onerror=z" 里的 onerror= 是惰性文本，不构成标签属性。）
    assert not re.search(r"<[a-zA-Z][^>]*\son\w+\s*=", html), "存在可执行 on* 属性"


def test_rep15_inject_block_is_static_escaped():
    from backend.services.report_context import inject_context_into_html
    blk = rc.render_report_context(rc.ReportContext(connections=[rc.ConnectionContext(
        connection_id="c1", connection_name="a<b>", name_source=rc.NAME_SOURCE_SNAPSHOT)]))
    out = inject_context_into_html("<html><body><p>正文</p></body></html>", blk)
    assert out.count('class="report-context"') == 1
    assert "<p>正文</p>" in out


# ════════════════════════════════════════════════════════════════════════════
# UAT 第一轮 TKT-M01：H08 报告头部“双块”去重（服务时注入遇已有块应替换，不叠加）
# ════════════════════════════════════════════════════════════════════════════
def test_uat_m01_h08_header_no_duplicate():
    """TKT-M01：inject_context_into_html 遇已有 report-context 块应**替换**为冻结名，
    不再并存两块（“实例连接名称”唯一权威展示位）。UAT-M01 曾实测新报告头部双块。"""
    from backend.services.report_context import (
        inject_context_into_html, render_report_context, ReportContext,
        ConnectionContext)
    # 模拟分析器生成时已嵌入“未关联实例”占位的 report_html
    analyzer_html = (
        '<html><body>'
        '<div class="report-context" data-report-context-version="1" '
        'style="margin:8px;">实例连接名称：<strong>未关联实例（网关日志分析）</strong></div>'
        '<h1>报告</h1><p>正文</p></body></html>')
    frozen = render_report_context(ReportContext(connections=[ConnectionContext(
        connection_id="c1", connection_name="SIT-分布式实例A",
        name_source=rc.NAME_SOURCE_SNAPSHOT, db_name="tdsql_check")]))
    out = inject_context_into_html(analyzer_html, frozen)
    assert out.count('实例连接名称') == 1, "应只剩一个来源块"
    assert "SIT-分布式实例A" in out
    assert "未关联实例（网关日志分析）" not in out, "占位块应被替换掉"
    assert "<h1>报告</h1>" in out and "<p>正文</p>" in out   # 正文保留


def test_uat_m01_inject_when_no_existing_block():
    """TKT-M01 对照：报告无既有来源块时（旧 report_html），仍在 <body> 后注入一次。"""
    from backend.services.report_context import inject_context_into_html
    blk = rc.render_report_context(rc.ReportContext(connections=[rc.ConnectionContext(
        connection_id="c1", connection_name="实例A",
        name_source=rc.NAME_SOURCE_SNAPSHOT)]))
    out = inject_context_into_html("<html><body><h1>旧报告</h1></body></html>", blk)
    assert out.count('实例连接名称') == 1
    assert "实例A" in out


# ════════════════════════════════════════════════════════════════════════════
# UAT 第一轮 TKT-M02：离线/降级占位名带徽标，已绑定真名保持 strong
# ════════════════════════════════════════════════════════════════════════════
def test_uat_m02_offline_badge_render():
    from backend.services.report_context import (
        render_report_context, ReportContext, ConnectionContext)
    # 离线（未绑定）→ 占位名带徽标内联样式
    off = render_report_context(ReportContext(origin="offline", connections=[]),
                                scene="离线文件审核")
    assert "#fff3cd" in off, "离线占位应带浅黄徽标内联样式"
    assert "未关联实例（离线文件审核）" in off
    # 已绑定真名 → 保持 <strong>，不加徽标
    on = render_report_context(ReportContext(origin="bound", connections=[ConnectionContext(
        connection_id="c1", connection_name="SIT-分布式实例A",
        name_source=rc.NAME_SOURCE_SNAPSHOT)]))
    assert "#fff3cd" not in on, "已绑定真名不应加徽标"
    assert "<strong>SIT-分布式实例A</strong>" in on


def test_uat_m02_legacy_missing_name_badged():
    """TKT-M02：历史未记录名称（连接已删）的降级占位也应带徽标。"""
    from backend.services.report_context import render_report_context, ReportContext, ConnectionContext
    ctx = ReportContext(connections=[ConnectionContext(
        connection_id="c1", connection_name="", name_source=rc.NAME_SOURCE_MISSING)])
    out = render_report_context(ctx)
    assert "#fff3cd" in out
    assert "历史未记录名称" in out


# ════════════════════════════════════════════════════════════════════════════
# QC-DEFECT-05：connection_id 降级渲染只转义一次（不得 &amp;lt; 二次转义乱码）
# ════════════════════════════════════════════════════════════════════════════
def test_qc_defect05_connection_id_single_escape():
    """QC-DEFECT-05：connection_id 含特殊字符时只转义一次。
    修复前内层 _esc(connection_id) + 外层 _esc(name) 会产出 &amp;lt; 乱码实体。"""
    from backend.services.report_context import (
        render_report_context, ReportContext, ConnectionContext)
    ctx = ReportContext(connections=[ConnectionContext(
        connection_id="TDSQL<TEST>&DEV", connection_name="",
        name_source=rc.NAME_SOURCE_MISSING)])
    html = render_report_context(ctx)
    assert "&amp;lt;" not in html, "connection_id 被二次转义为 &amp;lt;"
    assert "&amp;amp;" not in html, "& 被二次转义"
    assert "&lt;" in html           # 单次转义仍须存在


def test_qc_defect05_multi_instance_single_escape():
    """QC-DEFECT-05：多实例分支的 connection_id 同样只转义一次。"""
    from backend.services.report_context import (
        render_report_context, ReportContext, ConnectionContext)
    ctx = ReportContext(connections=[
        ConnectionContext(connection_id="a<b", connection_name="", name_source=rc.NAME_SOURCE_MISSING),
        ConnectionContext(connection_id="c>d", connection_name="库B", name_source=rc.NAME_SOURCE_SNAPSHOT),
    ])
    html = render_report_context(ctx)
    assert "&amp;lt;" not in html, "多实例分支 connection_id 被二次转义"
