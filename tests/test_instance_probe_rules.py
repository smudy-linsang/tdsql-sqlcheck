# -*- coding: utf-8 -*-
"""V1.5.1 SQL 层判据表测试（DESIGN-v1.5.1 §7.0.1/§8.5 + A 施工提醒逐条钉死）

判据数据全部来自 G 的 Proxy 层成对实测（docs/REPORT-v1.5.1，2026-07-29）。
本文件的职责：
1) 钉死判据表出厂状态（PR001/PR002/PR004 启用，PR003 登记不启用，evidence 必填）；
2) 每条判据配套反向鉴别断言（两类数据各跑一次、结论不同）；
3) 钉死失效方向防线：全部未命中 ≠ 集中式；PR002/PR003/PR004 只准返回
   distributed 或 None（G 初版代码 else: return "centralized" 的教训——
   一次网络抖动就能关掉 27 条规则）。
"""
from unittest.mock import MagicMock

import pytest

from backend.services import instance_probe_rules as m
from backend.services.instance_probe_rules import (
    ACTIVE_PROBE_RULES, ProbeRule,
    _decide_proxy_status, _decide_explain_info,
    _decide_table_ddl, _decide_xa_database,
)
from backend.services.tdsql_connector import TDSQLConnectionPool

# ── G 实测数据（原样，含 DIST set 行末尾空格）──
CENT_STATUS = [
    {"status_name": "set", "value": "set_1782130875_4"},
    {"status_name": "set_1782130875_4",
     "value": "10.206.0.4:4002;s1@10.206.0.8:4002@100@IDC3@0"},
]
DIST_STATUS = [
    {"status_name": "cluster", "value": "group_1782132247_10"},
    {"status_name": "set_1782132369_1:ip", "value": "10.206.0.8:4003;"},
    {"status_name": "set_1782132369_1:alias", "value": "s1"},
    {"status_name": "set_1782132369_1:hash_range", "value": "0---7"},
    {"status_name": "set_1782132389_3:ip", "value": "10.206.0.13:4002;"},
    {"status_name": "set_1782132389_3:alias", "value": "s2"},
    {"status_name": "set_1782132389_3:hash_range", "value": "8---15"},
    {"status_name": "set", "value": "set_1782132369_1,set_1782132389_3 "},
]


# ════════════════════════════════════════════════════════════
# 判据表出厂状态
# ════════════════════════════════════════════════════════════

def test_probe_rules_factory_state():
    """出厂判据表：PR001/PR002/PR004 启用，PR003 登记但不启用。

    任何未经 §8.4 评审往表里增删判据的改动，本用例都会失败；
    新增判据须同步更新本用例与配套的反向鉴别用例。
    """
    state = {r.rule_id: r.enabled for r in ACTIVE_PROBE_RULES}
    assert state == {"PR001": True, "PR002": True,
                     "PR004": True, "PR003": False}, (
        "判据表变更必须先通过 DESIGN-v1.5.1 §8.4 三项标准评审")


def test_every_rule_has_evidence():
    """每条判据的 evidence 必填（实测日期 + 数据出处，§8.4 标准 1）。"""
    for r in ACTIVE_PROBE_RULES:
        assert r.evidence and "2026-07-29" in r.evidence, (
            f"{r.rule_id} 缺少实测依据")


# ════════════════════════════════════════════════════════════
# PR001：/*proxy*/show status 拓扑签名（反向鉴别 + A 提醒 2/3）
# ════════════════════════════════════════════════════════════

def test_pr001_discriminates_both_kinds():
    """反向鉴别：两类实测数据必须得出相反结论。"""
    assert _decide_proxy_status(DIST_STATUS) == "distributed"
    assert _decide_proxy_status(CENT_STATUS) == "centralized"


def test_pr001_cluster_row_positive():
    """签名1：存在 cluster 行即分布式。"""
    assert _decide_proxy_status(
        [{"status_name": "cluster", "value": "group_x"}]) == "distributed"


def test_pr001_colon_in_key_name_not_value():
    """签名2 判的是【键名】含 ':'，不是值含 ':'（A 提醒 3）。

    CENT 的值 10.206.0.4:4002 也有冒号，但键名没有——不得误判分布式。
    """
    # 值含冒号、键名不含 → 不触发签名2（走签名4 判集中式）
    rows = [{"status_name": "set", "value": "set_a"},
            {"status_name": "set_a", "value": "10.206.0.4:4002;..."}]
    assert _decide_proxy_status(rows) == "centralized"
    # 键名含冒号（:ip / :alias 单独出现也命中）→ 分布式
    assert _decide_proxy_status(
        [{"status_name": "set_a:ip", "value": "10.206.0.8:4003;"}]) == "distributed"
    assert _decide_proxy_status(
        [{"status_name": "set_a:hash_range", "value": "0---7"}]) == "distributed"


def test_pr001_set_value_strip_and_filter_empty():
    """签名3/4：set 行值必须 strip + 过滤空串（A 提醒 2）。

    实测原文 DIST 的 set 行末尾带一个空格，不处理会切出空元素；
    单 SET 值带尾随逗号/空格也不得误计成 2 个 SET。
    """
    # 两个 SET + 尾随空格 → 分布式
    assert _decide_proxy_status([{"status_name": "set",
                                  "value": "set_a,set_b "}]) == "distributed"
    # 单 SET + 尾随逗号与空格 → 仍是 1 个 SET → 集中式
    assert _decide_proxy_status([{"status_name": "set",
                                  "value": "set_a ,"}]) == "centralized"
    # set 行值为空 → 过滤后 0 个 SET → 无结论（不得判集中式）
    assert _decide_proxy_status([{"status_name": "set", "value": "  "}]) is None


def test_pr001_no_set_row_returns_none():
    """无 set 行（形态不符）→ 无结论，不猜。"""
    assert _decide_proxy_status([{"status_name": "uptime", "value": "100"}]) is None
    assert _decide_proxy_status([]) is None


# ════════════════════════════════════════════════════════════
# PR002 / PR003 / PR004：只准 return distributed 或 None（A 提醒 1）
# ════════════════════════════════════════════════════════════

def test_pr002_info_column_positive_else_none():
    assert _decide_explain_info([{"id": 1, "Extra": "No tables used",
                                  "info": "set_1,EXPLAIN SELECT 1"}]) == "distributed"
    # 无 info 列 ≠ 集中式，只准 None
    assert _decide_explain_info([{"id": 1, "Extra": "No tables used"}]) is None
    assert _decide_explain_info([]) is None


def test_pr004_xa_database_positive_else_none():
    assert _decide_xa_database([{"Database": "mysql"},
                                {"Database": "xa"}]) == "distributed"
    # 无 xa 库 ≠ 集中式（账号权限窄时同样看不到），只准 None
    assert _decide_xa_database([{"Database": "mysql"},
                                {"Database": "biz"}]) is None
    assert _decide_xa_database([]) is None


def test_pr003_shardkey_positive_else_none():
    ddl_dist = [{"Table": "t_order",
                 "Create Table": "CREATE TABLE `t_order` (...) ENGINE=InnoDB "
                                 "DEFAULT CHARSET=utf8mb4 shardkey=id"}]
    ddl_cent = [{"Table": "t_order",
                 "Create Table": "CREATE TABLE `t_order` (...) ENGINE=InnoDB "
                                 "DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin"}]
    assert _decide_table_ddl(ddl_dist) == "distributed"
    # 无 shardkey ≠ 集中式，只准 None
    assert _decide_table_ddl(ddl_cent) is None
    assert _decide_table_ddl([]) is None
    # 广播表标记同为阳性
    assert _decide_table_ddl([{"Create Table": "... broadcast ..."}]) == "distributed"


def test_no_decide_function_returns_centralized_except_pr001():
    """结构性断言：除 PR001 外，任何判据对任意"未命中"输入都不得返回 centralized。"""
    miss_inputs = [[], [{"whatever": "x"}], [{"Database": "mysql"}]]
    for r in ACTIVE_PROBE_RULES:
        if r.rule_id == "PR001":
            continue
        for rows in miss_inputs:
            assert r.decide(rows) != "centralized", (
                f"{r.rule_id} 产出了未经授权的集中式结论（静默漏报方向）")


# ════════════════════════════════════════════════════════════
# 连接器合并策略
# ════════════════════════════════════════════════════════════

def _pool_with(side_effect):
    pool = MagicMock(spec=TDSQLConnectionPool)
    pool._execute.side_effect = side_effect
    # spec mock 的类属性不是真列表，诊断采集器需要真清单
    pool._DIAGNOSTIC_STATEMENTS = TDSQLConnectionPool._DIAGNOSTIC_STATEMENTS
    return pool


def test_all_rules_miss_never_means_centralized(monkeypatch):
    """§8.4 标准 3 的代码化：全部判据未命中 ≠ 集中式。

    这是本次事故的失效方向 —— 必须由用例钉死。
    """
    monkeypatch.setattr(m, "ACTIVE_PROBE_RULES", [
        ProbeRule("PRTEST", "select 1", lambda rows: None, "unit-test"),
    ])
    result, _ = TDSQLConnectionPool.probe_instance_type(
        _pool_with(lambda sql: [{"1": 1}]))
    assert result is None          # 不是 "centralized"


def test_positive_beats_negative(monkeypatch):
    """阳性优先于阴性：一条判分布式、一条判集中式，取分布式（A 提醒 5）。"""
    monkeypatch.setattr(m, "ACTIVE_PROBE_RULES", [
        ProbeRule("PRNEG", "select 1", lambda rows: "centralized", "unit-test"),
        ProbeRule("PRPOS", "select 2", lambda rows: "distributed", "unit-test"),
    ])
    result, detail = TDSQLConnectionPool.probe_instance_type(
        _pool_with(lambda sql: [{"1": 1}]))
    assert result == "distributed"
    assert detail["matched"] == "PRPOS"


def test_disabled_or_empty_sql_rules_not_executed(monkeypatch):
    """enabled=False 或 sql 为空的判据（PR003）不参与自动探测。"""
    executed = []
    def _execute(sql, *args, **kwargs):
        executed.append(sql)
        return []
    monkeypatch.setattr(m, "ACTIVE_PROBE_RULES", [
        ProbeRule("PROFF", "select 1", lambda rows: "distributed",
                  "unit-test", enabled=False),
        ProbeRule("PRNOSQL", "", lambda rows: "distributed", "unit-test"),
    ])
    result, detail = TDSQLConnectionPool.probe_instance_type(_pool_with(_execute))
    assert result is None
    assert executed == []
    assert detail["rules"] == {}


def test_empty_rules_yield_no_conclusion(monkeypatch):
    """判据表为空时必须返回无结论，绝不能回退成某个默认类型。"""
    monkeypatch.setattr(m, "ACTIVE_PROBE_RULES", [])
    result, detail = TDSQLConnectionPool.probe_instance_type(
        _pool_with(lambda sql: [{"1": 1}]))
    assert result is None
    assert detail["matched"] is None


def test_probe_rule_failure_does_not_raise():
    """判据执行异常仅记录，不得抛出（INV-5）。"""
    result, detail = TDSQLConnectionPool.probe_instance_type(
        _pool_with(Exception("boom")))
    assert result is None


def test_probe_end_to_end_with_field_data():
    """端到端反向鉴别：用 G 原始数据跑完整判定链，两侧结论相反。"""
    def _make(kind):
        def _execute(sql, *args, **kwargs):
            s = str(sql).strip().lower()
            if s.startswith("/*proxy*/show status"):
                return DIST_STATUS if kind == "dist" else CENT_STATUS
            if s.startswith("explain"):
                return ([{"id": 1, "info": "set_1,EXPLAIN SELECT 1"}]
                        if kind == "dist" else [{"id": 1}])
            if s.startswith("show databases"):
                return ([{"Database": "mysql"}, {"Database": "xa"}]
                        if kind == "dist" else [{"Database": "mysql"}])
            return []
        return _pool_with(_execute)
    dist_result, dist_detail = TDSQLConnectionPool.probe_instance_type(_make("dist"))
    cent_result, cent_detail = TDSQLConnectionPool.probe_instance_type(_make("cent"))
    assert dist_result == "distributed" and dist_detail["matched"] == "PR001"
    assert cent_result == "centralized" and cent_detail["matched"] == "PR001"


# ════════════════════════════════════════════════════════════
# 诊断采集器（C 组）
# ════════════════════════════════════════════════════════════

def test_diagnostics_collects_all_statements():
    """诊断采集：单条语句失败不影响其余条目。"""
    def _execute(sql, *args, **kwargs):
        s = str(sql).lower()
        if "connectionpool" in s or "show shard" in s or "show sets" in s:
            raise Exception("Command is not supported")
        return [{"k": "v"}]
    pool = _pool_with(_execute)
    out = TDSQLConnectionPool.collect_probe_diagnostics(pool)
    keys = set(out["statements"])
    assert {"proxy_show_status", "show_databases"} <= keys
    assert out["statements"]["proxy_show_status"]["ok"] is True
    assert out["statements"]["proxy_connectionpool"]["ok"] is False
    assert any(v["ok"] for v in out["statements"].values())


def test_diagnostics_sample_table_failure_recorded():
    """样本表取 DDL 失败仅记录 reason，不中断。"""
    def _execute(sql, *args, **kwargs):
        if "show create table" in str(sql).lower():
            raise Exception("table not found")
        return [{"k": "v"}]
    pool = _pool_with(_execute)
    out = TDSQLConnectionPool.collect_probe_diagnostics(pool, "db.not_exist")
    assert out["sample_table_ddl"]["ok"] is False
    assert "table not found" in out["sample_table_ddl"]["reason"]


@pytest.mark.parametrize("bad", ["a;drop table t", "a b", "`x`", "a.b.c", "-- x"])
def test_diagnostics_api_rejects_bad_sample_table(bad):
    """sample_table 进入 SHOW CREATE TABLE，必须无任何拼接注入面（API 层白名单）。"""
    from backend.api.tdsql_manage import _SAMPLE_TABLE_RE
    assert not _SAMPLE_TABLE_RE.match(bad)


@pytest.mark.parametrize("good", ["t_order", "tdsql_check.t_order", "db1.T_2"])
def test_diagnostics_api_accepts_valid_sample_table(good):
    from backend.api.tdsql_manage import _SAMPLE_TABLE_RE
    assert _SAMPLE_TABLE_RE.match(good)
