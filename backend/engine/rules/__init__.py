"""
TDSQL SQL审核工具 - 规则引擎 (V1.0)

共76条规则，按7个类别组织:
- 命名规范 (NAMING): R001-R002, R033-R034, R049
- DDL规范 (DDL): R003-R011, R023-R032, R035-R038
- DML规范 (DML): R012-R015, R017, R040-R041, R043, R047
- 性能规范 (PERFORMANCE): R016, R044, R047, R050-R052
- 分布式规范 (DISTRIBUTED): R020-R022, R025, R048, R053-R060
- 索引规范 (INDEX): R018-R019, R061-R068
- 事务规范 (TRANSACTION): R069-R072
- 安全规范 (SECURITY): R039, R042, R045-R046, R073-R076
"""
from backend.engine.rules.base import BaseRule
from backend.engine.rules.naming import R001NamingLength, R002ReservedKeywords
from backend.engine.rules.ddl import (
    R003PrimaryKey, R004Engine, R005Charset, R006EnumSetType,
    R007TimestampType, R008ForeignKey, R009FinanceFloatType,
    R010VarcharLength, R011TextBlobType,
    R023NoCreateTableSelect, R024NoTemporaryTable, R025NoAlterShardKey,
    R026NoColumnShrink, R027NoDropDatabase, R028TableMustHaveComment,
    R029ColumnMustHaveComment, R030NoViewProcTrigger, R031NoCustomFunction,
    R032NoTemporaryTableRule, R033NoPluralTableName, R034BackupTableNaming,
    R035CrossTableFieldType, R036SuggestTimestampColumns,
    R037SuggestLogicalDelete, R038NoAutoIncrementForLargeTable,
)
from backend.engine.rules.dml import (
    R012SelectStar, R013DmlWithoutWhere, R014UpdateDeleteWithoutWhere,
    R015NestedSubquery, R016FunctionInWhere, R017OrderByRand,
    R018IndexCount, R019RedundantIndex,
    R039NoIntoOutfile, R040NoDelayedLowPriority, R041NoUnnamedInsert,
    R042NoLoadData, R043NoMultiTableUpdate, R044NoIndexHint,
    R045NoHandlerDo, R046NoFlushLockTable, R047DeleteAllUseTruncate,
    R048InsertMustIncludeShardKey, R049DifferentAliasForTables,
    R050InListSize, R051NoSelectWithoutWhere, R052NoImplicitTypeCast,
)
from backend.engine.rules.distributed import (
    R020ShardKeyInWhere, R021ShardKeyUpdate, R022GlobalDeleteWithoutShardKey,
    R053NoCrossShardJoin, R054ShardKeyMustBePrimaryKey,
    R055NoGlobalIndexOnly, R056SuggestShardKeyInOrderBy,
    R057NoBulkInsertWithoutShardKey, R058BatchUpdateLimit,
    R059NoDistributedTransaction, R060ExplainShardKeyCheck,
)
from backend.engine.rules.index import (
    R061IndexNaming, R062CompositeIndexOrder, R063NoIndexOnLowCardinality,
    R064CoveringIndexSuggestion, R065IndexColumnCountLimit,
    R066NoIndexOnBlobText, R067PrefixIndexSuggestion,
    R068SuggestIndexForForeignKey,
)
from backend.engine.rules.transaction import (
    R069NoLongTransaction, R070NoLargeTransaction,
    R071TransactionMustCommit, R072NoLockInTransaction,
)
from backend.engine.rules.security import (
    R073NoDdlWithoutBackup, R074NoGrantRevoke,
    R075NoTruncateWithoutCheck, R076NoSqlInjectionRisk,
)

# 所有规则类列表（用于初始化数据库配置）
ALL_RULE_CLASSES = [
    # 命名规范
    R001NamingLength, R002ReservedKeywords,
    # DDL规范
    R003PrimaryKey, R004Engine, R005Charset, R006EnumSetType,
    R007TimestampType, R008ForeignKey, R009FinanceFloatType,
    R010VarcharLength, R011TextBlobType,
    # DML规范
    R012SelectStar, R013DmlWithoutWhere, R014UpdateDeleteWithoutWhere,
    R015NestedSubquery, R016FunctionInWhere, R017OrderByRand,
    R018IndexCount, R019RedundantIndex,
    # 分布式规范
    R020ShardKeyInWhere, R021ShardKeyUpdate, R022GlobalDeleteWithoutShardKey,
    # DDL新增
    R023NoCreateTableSelect, R024NoTemporaryTable, R025NoAlterShardKey,
    R026NoColumnShrink, R027NoDropDatabase, R028TableMustHaveComment,
    R029ColumnMustHaveComment, R030NoViewProcTrigger, R031NoCustomFunction,
    R032NoTemporaryTableRule, R033NoPluralTableName, R034BackupTableNaming,
    R035CrossTableFieldType, R036SuggestTimestampColumns,
    R037SuggestLogicalDelete, R038NoAutoIncrementForLargeTable,
    # DML/安全/性能新增
    R039NoIntoOutfile, R040NoDelayedLowPriority, R041NoUnnamedInsert,
    R042NoLoadData, R043NoMultiTableUpdate, R044NoIndexHint,
    R045NoHandlerDo, R046NoFlushLockTable, R047DeleteAllUseTruncate,
    R048InsertMustIncludeShardKey, R049DifferentAliasForTables,
    R050InListSize, R051NoSelectWithoutWhere, R052NoImplicitTypeCast,
    # 分布式新增
    R053NoCrossShardJoin, R054ShardKeyMustBePrimaryKey,
    R055NoGlobalIndexOnly, R056SuggestShardKeyInOrderBy,
    R057NoBulkInsertWithoutShardKey, R058BatchUpdateLimit,
    R059NoDistributedTransaction, R060ExplainShardKeyCheck,
    # 索引规范
    R061IndexNaming, R062CompositeIndexOrder, R063NoIndexOnLowCardinality,
    R064CoveringIndexSuggestion, R065IndexColumnCountLimit,
    R066NoIndexOnBlobText, R067PrefixIndexSuggestion,
    R068SuggestIndexForForeignKey,
    # 事务规范
    R069NoLongTransaction, R070NoLargeTransaction,
    R071TransactionMustCommit, R072NoLockInTransaction,
    # 安全规范
    R073NoDdlWithoutBackup, R074NoGrantRevoke,
    R075NoTruncateWithoutCheck, R076NoSqlInjectionRisk,
]

__all__ = [
    "BaseRule",
    "ALL_RULE_CLASSES",
    # 命名规范
    "R001NamingLength", "R002ReservedKeywords",
    # DDL规范
    "R003PrimaryKey", "R004Engine", "R005Charset", "R006EnumSetType",
    "R007TimestampType", "R008ForeignKey", "R009FinanceFloatType",
    "R010VarcharLength", "R011TextBlobType",
    # DML规范
    "R012SelectStar", "R013DmlWithoutWhere", "R014UpdateDeleteWithoutWhere",
    "R015NestedSubquery", "R016FunctionInWhere", "R017OrderByRand",
    "R018IndexCount", "R019RedundantIndex",
    # 分布式规范
    "R020ShardKeyInWhere", "R021ShardKeyUpdate", "R022GlobalDeleteWithoutShardKey",
    # DDL新增
    "R023NoCreateTableSelect", "R024NoTemporaryTable", "R025NoAlterShardKey",
    "R026NoColumnShrink", "R027NoDropDatabase", "R028TableMustHaveComment",
    "R029ColumnMustHaveComment", "R030NoViewProcTrigger", "R031NoCustomFunction",
    "R032NoTemporaryTableRule", "R033NoPluralTableName", "R034BackupTableNaming",
    "R035CrossTableFieldType", "R036SuggestTimestampColumns",
    "R037SuggestLogicalDelete", "R038NoAutoIncrementForLargeTable",
    # DML/安全/性能新增
    "R039NoIntoOutfile", "R040NoDelayedLowPriority", "R041NoUnnamedInsert",
    "R042NoLoadData", "R043NoMultiTableUpdate", "R044NoIndexHint",
    "R045NoHandlerDo", "R046NoFlushLockTable", "R047DeleteAllUseTruncate",
    "R048InsertMustIncludeShardKey", "R049DifferentAliasForTables",
    "R050InListSize", "R051NoSelectWithoutWhere", "R052NoImplicitTypeCast",
    # 分布式新增
    "R053NoCrossShardJoin", "R054ShardKeyMustBePrimaryKey",
    "R055NoGlobalIndexOnly", "R056SuggestShardKeyInOrderBy",
    "R057NoBulkInsertWithoutShardKey", "R058BatchUpdateLimit",
    "R059NoDistributedTransaction", "R060ExplainShardKeyCheck",
    # 索引规范
    "R061IndexNaming", "R062CompositeIndexOrder", "R063NoIndexOnLowCardinality",
    "R064CoveringIndexSuggestion", "R065IndexColumnCountLimit",
    "R066NoIndexOnBlobText", "R067PrefixIndexSuggestion",
    "R068SuggestIndexForForeignKey",
    # 事务规范
    "R069NoLongTransaction", "R070NoLargeTransaction",
    "R071TransactionMustCommit", "R072NoLockInTransaction",
    # 安全规范
    "R073NoDdlWithoutBackup", "R074NoGrantRevoke",
    "R075NoTruncateWithoutCheck", "R076NoSqlInjectionRisk",
]
