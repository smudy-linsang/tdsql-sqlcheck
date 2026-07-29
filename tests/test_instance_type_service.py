# -*- coding: utf-8 -*-
"""V1.5 实例类型解析器测试

覆盖 DETAIL-v1.5 §7.4：解析优先级、探测、缓存、异常回落。
核心：两个探针全异常时必须返回 None（而非 centralized），否则一次网络故障
就会让分布式实例被判成集中式 → 27 条规则静默失效（最危险的失效模式）。
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


# ════════════════════════════════════════════════════════════
# 探测函数本身（最关键）
# ════════════════════════════════════════════════════════════

def test_both_probes_error_returns_none_not_centralized():
    """两探针全异常 → 必须返回 None，不能返回 centralized。"""
    from backend.services.tdsql_connector import TDSQLConnectionPool
    broken = MagicMock(spec=TDSQLConnectionPool)
    broken._execute.side_effect = Exception("connection refused")
    result, detail = TDSQLConnectionPool.probe_instance_type(broken)
    assert result is None, "两探针全异常必须返回 None，否则分布式实例会被静默判成集中式"


def test_probe_distributed_when_proxy_ok():
    """探针1（/*proxy*/show status）成功 → 判分布式。"""
    from backend.services.tdsql_connector import TDSQLConnectionPool
    pool = MagicMock(spec=TDSQLConnectionPool)
    pool._execute.return_value = [{"rows": 1}]
    result, _ = TDSQLConnectionPool.probe_instance_type(pool)
    assert result == "distributed"


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
# A 类通道（有 connection_id）
# ════════════════════════════════════════════════════════════

def test_a_class_ignores_requested_type():
    """INV-2：有 connection_id 时，调用方传的 instance_type 必须被忽略。"""
    with patch(_REG) as reg:
        reg.get_saved.return_value = {"is_distributed": 0,
                                      "detected_instance_type": "centralized"}
        ctx = instance_type_service.resolve("conn_x", requested="distributed")
        assert ctx.instance_type == InstanceType.CENTRALIZED, "A 类通道必须忽略 requested"


def test_probe_wins_over_declaration():
    """G3：探测优先于人工声明；冲突时 conflict=True。"""
    with patch(_REG) as reg:
        reg.get_saved.return_value = {"is_distributed": 1,
                                      "detected_instance_type": "centralized"}
        ctx = instance_type_service.resolve("conn_conflict")
        assert ctx.instance_type == InstanceType.CENTRALIZED, "探测应优先于声明"
        assert ctx.source == TypeSource.PROBED
        assert ctx.conflict is True


def test_probe_failure_falls_back_to_declaration():
    """探测无结论（detected 为空）→ 退回声明值，source=declared。"""
    with patch(_REG) as reg:
        reg.get_saved.return_value = {"is_distributed": 0,
                                      "detected_instance_type": None}
        with patch.object(InstanceTypeService, "_probe_and_persist", return_value=None):
            ctx = instance_type_service.resolve("conn_noprobe")
            assert ctx.instance_type == InstanceType.CENTRALIZED
            assert ctx.source == TypeSource.DECLARED
            assert ctx.conflict is False


def test_resolve_exception_falls_back_to_default():
    """INV-5：解析任何异常都不得中断，回落全局默认。"""
    with patch(_REG) as reg:
        reg.get_saved.side_effect = Exception("db down")
        ctx = instance_type_service.resolve("conn_err")
        assert ctx.source == TypeSource.DEFAULT
        assert ctx.instance_type == InstanceType.DISTRIBUTED


# ════════════════════════════════════════════════════════════
# 全局默认配置
# ════════════════════════════════════════════════════════════

def test_set_default_invalid_raises():
    with pytest.raises(ValueError):
        instance_type_service.set_default_instance_type("invalid")
