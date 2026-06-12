"""
TDSQL SQL审核工具 - 规则引擎
"""
from backend.engine.rules.base import BaseRule
from backend.engine.rules.naming import R001NamingLength, R002ReservedKeywords
from backend.engine.rules.ddl import (
    R003PrimaryKey, R004Engine, R005Charset, R006EnumSetType,
    R007TimestampType, R008ForeignKey, R009FinanceFloatType,
    R010VarcharLength, R011TextBlobType,
)
from backend.engine.rules.dml import (
    R012SelectStar, R013DmlWithoutWhere, R014UpdateDeleteWithoutWhere,
    R015NestedSubquery, R016FunctionInWhere, R017OrderByRand,
    R018IndexCount, R019RedundantIndex,
)
from backend.engine.rules.distributed import (
    R020ShardKeyInWhere, R021ShardKeyUpdate, R022GlobalDeleteWithoutShardKey,
)

__all__ = [
    "BaseRule",
    # 命名规范
    "R001NamingLength",
    "R002ReservedKeywords",
    # DDL 规范
    "R003PrimaryKey",
    "R004Engine",
    "R005Charset",
    "R006EnumSetType",
    "R007TimestampType",
    "R008ForeignKey",
    "R009FinanceFloatType",
    "R010VarcharLength",
    "R011TextBlobType",
    # DML 规范
    "R012SelectStar",
    "R013DmlWithoutWhere",
    "R014UpdateDeleteWithoutWhere",
    "R015NestedSubquery",
    "R016FunctionInWhere",
    "R017OrderByRand",
    "R018IndexCount",
    "R019RedundantIndex",
    # 分布式规范
    "R020ShardKeyInWhere",
    "R021ShardKeyUpdate",
    "R022GlobalDeleteWithoutShardKey",
]
