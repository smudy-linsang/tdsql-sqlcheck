# -*- coding: utf-8 -*-
"""V1.5.1 SQL 层判据表测试（DESIGN-v1.5.1 §9.2(5) + A 评审提醒逐条钉死）

判据数据全部来自 G 的 Proxy 层成对实测（docs/REPORT-v1.5.1，2026-07-29）。
本文件的职责：
1) 钉死判据表出厂状态（PR001-PR004，仅 PR001 允许阴性，evidence 必填）；
2) 每条判据配套反向鉴别断言（两类数据各跑一次、结论不同）；
3) 钉死失效方向防线：全部未命中 ≠ 集中式；非 allow_negative 判据的
   centralized 一律降级为无结论（G 初版代码 else: return "centralized"
   的教训——一次网络抖动就能关掉 27 条规则）。
"""
from unittest.mock import MagicMock

import pytest

from backend.services import instance_probe_rules as m
from backend.services.instance_probe_rules import (
    ACTIVE_PROBE_RULES, ProbeRule,
    _pr001_decide, _pr002_decide, _pr003_decide, _pr004_decide,
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
    """出厂判据表必须恰为 PR001-PR004（经 §8.4 评审入表的实测判据）。

    任何未经评审往表里增删判据的改动，本用例都会失败；
    新增判据须同步更新本用例与配套的反向鉴别用例。
    """
    ids = [r.rule_id for r in ACTIVE_PROBE_RULES]
    assert ids == ["PR001", "PR002", "PR003", "PR004"], (
        "判据表变更必须先通过 DESIGN-v1.5.1 §8.4 三项标准评审")


def test_only_pr001_allows_negative():
    """仅 PR001 允许产出 centralized（单 SET 拓扑的阳性识别）。

    PR002/PR003/PR004 只准 distributed 或 None——这是整个改造里唯一
    能造成静默漏报的地方，由 allow_negative 结构化钉死。
    """
    for r in ACTIVE_PROBE_RULES:
        if r.rule_id == "PR001":
            assert r.allow_negative is True
        else:
            assert r.allow_negative is False, f"{r.rule_id} 不得授权阴性判定"


def test_every_rule_has_evidence():
    """每条判据的 evidence 必填（实测日期 + 数据出处，§8.4 标准 1）。"""
    for r in ACTIVE_PROBE_RULES:
        assert r.evidence and "2026-07-29" in r.evidence, (
            f"{r.rule_id} 缺少实测依据")


# ════════════════════════════════════════════════════════════
# PR001：/*proxy*/show status（反向鉴别 + A 提醒 2/3）
# ════════════════════════════════════════════════════════════

def test_pr001_discriminates_both_kinds():
    """反向鉴别：两类实测数据必须得出相反结论。"""
    assert _pr001_decide(DIST_STATUS) == "distributed"
    assert _pr001_decide(CENT_STATUS) == "centralized"


def test_pr001_cluster_row_positive():
    """签名1：存在 cluster 行即分布式。"""
    assert _pr001_decide([{"status_name": "cluster", "value": "group_x"}]) == "distributed"


def test_pr001_colon_in_key_name_not_value():
    """签名2 判的是【键名】含 ':'，不是值含 ':'。

    CENT 的值 10.206.0.4:4002 也有冒号，但键名没有——不得误判分布式。
    """
    # 值含冒号、键名不含 → 不触发签名2（走签名3 判集中式）
    rows = [{"status_name": "set", "value": "set_a"},
            {"status_name": "set_a", "value": "10.206.0.4:4002;..."}]
    assert _pr001_decide(rows) == "centralized"
    # 键名含冒号 → 分布式
    rows = [{"status_name": "set_a:hash_range", "value": "0---7"}]
    assert _pr001_decide(rows) == "distributed"


def test_pr001_set_value_strip_and_filter_empty():
    """签名3：set 行值必须 strip + 过滤空串。

    实测原文 DIST 的 set 行末尾带一个空格，不处理会切出空元素；
    单 SET 值带尾随逗号/空格也不得误计成 2 个 SET。
    """
    # 两个 SET + 尾随空格 → 分布式
    assert _pr001_decide([{"status_name": "set",
                           "value": "set_a,set_b "}]) == "distributed"
    # 单 SET + 尾随逗号与空格 → 仍是 1 个 SET → 集中式
    assert _pr001_decide([{"status_name": "set",
                           "value": "set_a ,"}]) == "centralized"
    # set 行值为空 → 过滤后 0 个 SET → 无结论（不得判集中式）
    assert _pr001_decide([{"status_name": "set", "value": "  "}]) is None


def test_pr001_no_set_row_returns_none():
    """无 set 行（如权限受限只回其他状态行）→ 无结论。"""
    assert _pr001_decide([{"status_name": "uptime", "value": "100"}]) is None
    assert _pr001_decide([]) is None


# ════════════════════════════════════════════════════════════
# PR002 / PR003 / PR004：只准 distributed 或 None（A 提醒 1）
# ════════════════════════════════════════════════════════════

def test_pr002_info_column_positive_else_none():
    assert _pr002_decide([{"id": 1, "Extra": "No tables used",
                           "info": "set_1,EXPLAIN SELECT 1"}]) == "distributed"
    # 无 info 列 ≠ 集中式，只准 None
    assert _pr002_decide([{"id": 1, "Extra": "No tables used"}]) is None
    assert _pr002_decide([]) is None


def test_pr003_xa_database_positive_else_none():
    assert _pr003_decide([{"Database": "mysql"}, {"Database": "xa"}]) == "distributed"
    # 无 xa 库 ≠ 集中式（权限不足同样看不到），只准 None
    assert _pr003_decide([{"Database": "mysql"}, {"Database": "biz"}]) is None
    assert _pr003_decide([]) is None


def test_pr004_shardkey_positive_else_none():
    ddl_dist = [{"Table": "t_order",
                 "Create Table": "CREATE TABLE `t_order` (...) ENGINE=InnoDB "
                                 "DEFAULT CHARSET=utf8mb4 shardkey=id"}]
    ddl_cent = [{"Table": "t_order",
                 "Create Table": "CREATE TABLE `t_order` (...) ENGINE=InnoDB "
                                 "DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin"}]
    tables = [{"ts": "biz", "tn": "t_order"}]
    assert _pr004_decide(tables, lambda sql: ddl_dist) == "distributed"
    # 抽样表无 shardkey ≠ 集中式，只准 None
    assert _pr004_decide(tables, lambda sql: ddl_cent) is None
    # 空库无表可查 → None
    assert _pr004_decide([], lambda sql: []) is None
    # 无执行器 → None
    assert _pr004_decide(tables, None) is None


def test_pr004_rejects_bad_identifiers():
    """异常标识符不得进入 SHOW CREATE TABLE 拼接。"""
    called = []
    def execute(sql):
        called.append(sql)
        return []
    _pr004_decide([{"ts": "a;drop", "tn": "t"},
                   {"ts": "db", "tn": "`x`"}], execute)
    assert called == [], "非法标识符不得触发任何 SQL 执行"


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
        ProbeRule("PRTEST", "select 1", lambda rows, execute=None: None, "unit-test"),
    ])
    result, _ = TDSQLConnectionPool.probe_instance_type(
        _pool_with(lambda sql: [{"1": 1}]))
    assert result is None          # 不是 "centralized"


def test_unauthorized_negative_verdict_degraded(monkeypatch):
    """非 allow_negative 判据返回 centralized → 降级为无结论并告警。

    结构化防线：即使后来者写出 G 初版那种 else: return "centralized"
    的判据，也无法造成静默漏报。
    """
    monkeypatch.setattr(m, "ACTIVE_PROBE_RULES", [
        ProbeRule("PRBAD", "select 1",
                  lambda rows, execute=None: "centralized", "unit-test"),
    ])
    result, detail = TDSQLConnectionPool.probe_instance_type(
        _pool_with(lambda sql: [{"1": 1}]))
    assert result is None
    assert detail["rules"]["PRBAD"]["verdict"] is None


def test_positive_beats_negative(monkeypatch):
    """阳性优先于阴性：一条判分布式、一条判集中式，取分布式（A 提醒 5）。"""
    monkeypatch.setattr(m, "ACTIVE_PROBE_RULES", [
        ProbeRule("PRNEG", "select 1",
                  lambda rows, execute=None: "centralized", "unit-test",
                  allow_negative=True),
        ProbeRule("PRPOS", "select 2",
                  lambda rows, execute=None: "distributed", "unit-test"),
    ])
    result, detail = TDSQLConnectionPool.probe_instance_type(
        _pool_with(lambda sql: [{"1": 1}]))
    assert result == "distributed"
    assert detail["matched"] == "PRPOS"


def test_empty_rules_yield_no_conclusion(monkeypatch):
    """判据表为空时必须返回无结论，绝不能回退成某个默认类型。"""
    monkeypatch.setattr(m, "ACTIVE_PROBE_RULES", [])
    result, detail = TDSQLConnectionPool.probe_instance_type(
        _pool_with(lambda sql: [{"1": 1}]))
    assert result is None
    assert detail.get("disabled") is True


def test_probe_rule_failure_does_not_raise():
    """判据执行异常仅记录，不得抛出（INV-5）。"""
    result, detail = TDSQLConnectionPool.probe_instance_type(
        _pool_with(Exception("boom")))
    assert result is None


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
    assert {"proxy_show_status", "explain_select_1", "show_databases"} <= keys
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
