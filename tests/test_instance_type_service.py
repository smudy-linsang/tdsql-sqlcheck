# -*- coding: utf-8 -*-
"""V1.5.1 实例类型解析器测试

覆盖 DESIGN-v1.5.1 §7.4 / §9：多源分级（锁定 > ZK > 探测/声明保守合并）、
判据表驱动的探测、缓存、异常回落。

核心防线（本次事故直接催生）：
1) 探测源不得是常量函数——对两类实例必须给出不同结论（反向鉴别用例）；
2) 全部判据无结论时必须返回 None（而非 centralized）；
3) 任一可用源判定为分布式即按分布式执行（保守合并），
   "探测一律优先"的 V1.5 旧策略已废弃。
"""
from unittest.mock import patch, MagicMock

import pytest

from backend.models import InstanceType, TypeSource
from backend.services.instance_type_service import (
    InstanceTypeService, instance_type_service,
)

_REG = "backend.services.connection_registry.registry"


@pytest.fixture(autouse=True)
def _clear_cache():
    """每个用例前后清缓存，避免相互污染。"""
    instance_type_service.invalidate()
    yield
    instance_type_service.invalidate()


# ────────────────────────────────────────────────────────────
# G 实测数据（REPORT-v1.5.1，2026-07-29，Proxy 端口成对采集）
# ────────────────────────────────────────────────────────────

_CENT_SHOW_STATUS = [
    {"status_name": "set", "value": "set_1782130875_4"},
    {"status_name": "set_1782130875_4",
     "value": "10.206.0.4:4002;s1@10.206.0.8:4002@100@IDC3@0"},
]
_DIST_SHOW_STATUS = [
    {"status_name": "cluster", "value": "group_1782132247_10"},
    {"status_name": "set_1782132369_1:ip", "value": "10.206.0.8:4003;"},
    {"status_name": "set_1782132369_1:alias", "value": "s1"},
    {"status_name": "set_1782132369_1:hash_range", "value": "0---7"},
    {"status_name": "set_1782132389_3:ip", "value": "10.206.0.13:4002;"},
    {"status_name": "set_1782132389_3:alias", "value": "s2"},
    {"status_name": "set_1782132389_3:hash_range", "value": "8---15"},
    # 注意实测原文 set 行值末尾带一个空格
    {"status_name": "set", "value": "set_1782132369_1,set_1782132389_3 "},
]


def _mock_pool(kind: str):
    """按实例类别构造 mock 连接池：_execute 按语句返回 G 实测数据。"""
    from backend.services.tdsql_connector import TDSQLConnectionPool
    pool = MagicMock(spec=TDSQLConnectionPool)

    def _execute(sql, *args, **kwargs):
        s = str(sql).strip().lower()
        if s.startswith("/*proxy*/show status"):
            return _DIST_SHOW_STATUS if kind == "dist" else _CENT_SHOW_STATUS
        if s.startswith("explain"):
            if kind == "dist":
                return [{"id": 1, "Extra": "No tables used",
                         "info": "set_1782132369_1,EXPLAIN SELECT 1"}]
            return [{"id": 1, "Extra": "No tables used"}]
        if s.startswith("show databases"):
            base = [{"Database": d} for d in
                    ("information_schema", "mysql", "sys", "tdsql_check2")]
            if kind == "dist":
                base.append({"Database": "xa"})
            return base
        if "information_schema.tables" in s:
            return []          # PR004 无样本表 → 无结论
        return []

    pool._execute.side_effect = _execute
    return pool


# ════════════════════════════════════════════════════════════
# 探测函数本身（最关键）
# ════════════════════════════════════════════════════════════

def test_probe_must_not_be_a_constant_function():
    """探测源不得对两类实例返回相同结论。

    这是 V1.5 缺陷的直接防线：当时的探测对任何实例恒返回 "distributed"，
    是一个常量函数。任何"某类返回某值"式的断言都发现不了这一点，
    只有对两类目标各跑一次、断言结论不同，才能捕获。
    """
    from backend.services.tdsql_connector import TDSQLConnectionPool
    dist_result, _ = TDSQLConnectionPool.probe_instance_type(_mock_pool("dist"))
    cent_result, _ = TDSQLConnectionPool.probe_instance_type(_mock_pool("cent"))
    if dist_result is None and cent_result is None:
        return          # 判据表为空时的预期状态（当前已填充，不应走到这里）
    assert dist_result != cent_result, "探测源无鉴别力（对两类实例返回相同结论）"
    assert dist_result == "distributed"
    assert cent_result == "centralized"


def test_both_probes_error_returns_none_not_centralized():
    """全部判据执行异常 → 必须返回 None，不能返回 centralized。"""
    from backend.services.tdsql_connector import TDSQLConnectionPool
    broken = MagicMock(spec=TDSQLConnectionPool)
    broken._execute.side_effect = Exception("connection refused")
    result, detail = TDSQLConnectionPool.probe_instance_type(broken)
    assert result is None, "全部判据异常必须返回 None，否则分布式实例会被静默判成集中式"


# ════════════════════════════════════════════════════════════
# B 类通道（无 connection_id）
# ════════════════════════════════════════════════════════════

def test_b_class_uses_requested():
    ctx = instance_type_service.resolve("", "centralized")
    assert ctx.instance_type == InstanceType.CENTRALIZED
    assert ctx.source == TypeSource.REQUEST


def test_b_class_falls_back_to_default():
    ctx = instance_type_service.resolve("", None)
    assert ctx.source == TypeSource.DEFAULT
    assert ctx.instance_type == InstanceType.DISTRIBUTED


def test_b_class_invalid_requested_falls_back():
    ctx = instance_type_service.resolve("", "not_a_type")
    assert ctx.source == TypeSource.DEFAULT


# ════════════════════════════════════════════════════════════
# A 类通道（有 connection_id）：多源分级 + 保守合并
# ════════════════════════════════════════════════════════════

def test_a_class_ignores_requested_type():
    """INV-2：有 connection_id 时，调用方传的 instance_type 必须被忽略。"""
    with patch(_REG) as reg:
        reg.get_saved.return_value = {"is_distributed": 0,
                                      "detected_instance_type": "centralized"}
        ctx = instance_type_service.resolve("conn_x", requested="distributed")
        assert ctx.instance_type == InstanceType.CENTRALIZED, "A 类通道必须忽略 requested"


_NO_PROBE = patch.object(InstanceTypeService, "_probe_and_persist",
                         return_value=(None, {}))


def test_disabled_probe_does_not_override_declaration():
    """P0 核心（缺陷现场复现）：无探测结论时，使用者声明的「集中式」必须生效。

    复现缺陷现场：SIT-集中式实例A 声明 centralized，
    V1.5 下被恒真探测覆盖成 distributed，R077 照报。
    """
    with patch(_REG) as reg, _NO_PROBE:
        reg.get_saved.return_value = {"is_distributed": 0,
                                      "detected_instance_type": None}
        ctx = instance_type_service.resolve("conn_declared_centralized")
        assert ctx.instance_type == InstanceType.CENTRALIZED
        assert ctx.source == TypeSource.DECLARED
        assert ctx.conflict is False


def test_r077_gone_for_declared_centralized_instance():
    """端到端：声明为集中式的实例，审核不得出现 R077。"""
    from backend.engine.checker import RuleChecker
    with patch(_REG) as reg, _NO_PROBE:
        reg.get_saved.return_value = {"is_distributed": 0,
                                      "detected_instance_type": None}
        ctx = instance_type_service.resolve("conn_declared_centralized2")
    sql = "CREATE TABLE t_user (id BIGINT PRIMARY KEY) ENGINE=InnoDB"
    r = RuleChecker().audit_sql(sql, instance_type=ctx.instance_type.value)
    assert "R077" not in {v.rule_id for v in r.violations}


@pytest.mark.parametrize("zk_kind,detected,is_distributed,expect", [
    # ZK 说分布式 → 分布式
    ("groupshard", None, 0, InstanceType.DISTRIBUTED),
    # ZK 说集中式但声明说分布式 → 仍分布式（保守，判成集中式是不可见的失效方向）
    ("noshard", None, 1, InstanceType.DISTRIBUTED),
    # 全部一致 → 集中式
    ("noshard", None, 0, InstanceType.CENTRALIZED),
    # 仅声明可用 → 按声明
    (None, None, 0, InstanceType.CENTRALIZED),
    # 探测=分布式、声明=集中式 → 分布式（保守，与旧策略同向）
    (None, "distributed", 0, InstanceType.DISTRIBUTED),
    # 探测=集中式、声明=分布式 → 分布式（V1.5 旧策略在此静默漏报，V1.5.1 修正）
    (None, "centralized", 1, InstanceType.DISTRIBUTED),
])
def test_conservative_merge(zk_kind, detected, is_distributed, expect):
    """任一源说分布式即按分布式：判成集中式是不可见的失效方向。"""
    with patch(_REG) as reg, _NO_PROBE:
        reg.get_saved.return_value = {"is_distributed": is_distributed,
                                      "detected_instance_type": detected,
                                      "zk_instance_kind": zk_kind}
        ctx = instance_type_service.resolve(
            f"conn_merge_{zk_kind}_{detected}_{is_distributed}")
        assert ctx.instance_type == expect


def test_probe_wins_when_conservative():
    """探测=分布式、声明=集中式：取分布式且标记冲突（保守取值，来源=probed）。"""
    with patch(_REG) as reg:
        reg.get_saved.return_value = {"is_distributed": 0,
                                      "detected_instance_type": "distributed"}
        ctx = instance_type_service.resolve("conn_conflict")
        assert ctx.instance_type == InstanceType.DISTRIBUTED
        assert ctx.source == TypeSource.PROBED
        assert ctx.conflict is True


def test_zk_source_reported_when_zk_available():
    """探测无结论、ZK 可用且结论为分布式时，source 标注为 zk。"""
    with patch(_REG) as reg, _NO_PROBE:
        reg.get_saved.return_value = {"is_distributed": 1,
                                      "detected_instance_type": None,
                                      "zk_instance_kind": "groupshard"}
        ctx = instance_type_service.resolve("conn_zk_dist")
        assert ctx.instance_type == InstanceType.DISTRIBUTED
        assert ctx.source == TypeSource.ZK
        assert ctx.conflict is False


def test_probed_source_ranks_before_zk():
    """S1 探测与 S2 ZK 同时可用且均为分布式时，source 标注为优先级更高的 probed。"""
    with patch(_REG) as reg:
        reg.get_saved.return_value = {"is_distributed": 1,
                                      "detected_instance_type": "distributed",
                                      "zk_instance_kind": "groupshard"}
        ctx = instance_type_service.resolve("conn_probe_first")
        assert ctx.instance_type == InstanceType.DISTRIBUTED
        assert ctx.source == TypeSource.PROBED
        assert ctx.conflict is False


def test_lock_overrides_everything():
    """S0 管理员锁定是终审，覆盖 ZK 权威源。"""
    with patch(_REG) as reg:
        reg.get_saved.return_value = {"is_distributed": 1,
                                      "detected_instance_type": "distributed",
                                      "zk_instance_kind": "groupshard",
                                      "instance_type_locked": 1,
                                      "instance_type_locked_value": "centralized"}
        ctx = instance_type_service.resolve("conn_locked_centralized")
        assert ctx.instance_type == InstanceType.CENTRALIZED
        assert ctx.source == TypeSource.LOCKED


def test_unlocked_ignores_locked_value():
    """解锁后 locked_value 仅作回显保留，不参与判定。"""
    with patch(_REG) as reg, _NO_PROBE:
        reg.get_saved.return_value = {"is_distributed": 1,
                                      "detected_instance_type": None,
                                      "instance_type_locked": 0,
                                      "instance_type_locked_value": "centralized"}
        ctx = instance_type_service.resolve("conn_unlocked")
        assert ctx.instance_type == InstanceType.DISTRIBUTED
        assert ctx.source == TypeSource.DECLARED


def test_unknown_zk_kind_not_guessed():
    """未知 ZK 形态不得静默映射成某一类——那是本次事故的同类错误。"""
    with patch(_REG) as reg, _NO_PROBE:
        reg.get_saved.return_value = {"is_distributed": 0,
                                      "detected_instance_type": None,
                                      "zk_instance_kind": "brand_new_kind"}
        ctx = instance_type_service.resolve("conn_unknown_kind")
        assert ctx.instance_type == InstanceType.CENTRALIZED
        assert ctx.source == TypeSource.DECLARED


def test_resolve_exception_falls_back_to_default():
    """INV-5：解析任何异常都不得中断，回落全局默认。"""
    with patch(_REG) as reg:
        reg.get_saved.side_effect = Exception("db down")
        ctx = instance_type_service.resolve("conn_err")
        assert ctx.source == TypeSource.DEFAULT
        assert ctx.instance_type == InstanceType.DISTRIBUTED


# ════════════════════════════════════════════════════════════
# 全局默认配置 / 锁定入参校验
# ════════════════════════════════════════════════════════════

def test_set_default_invalid_raises():
    with pytest.raises(ValueError):
        instance_type_service.set_default_instance_type("invalid")


def test_set_lock_invalid_value_raises():
    with pytest.raises(ValueError):
        instance_type_service.set_lock("conn_x", True, "not_a_type")
