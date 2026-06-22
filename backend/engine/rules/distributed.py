"""
TDSQL SQL审核工具 - 分布式规范规则 (R020-R022)

R020: 分布式表查询必须包含分片键
R021: 禁止更新分片键字段
R022: 禁止不带分片键的全局DELETE/UPDATE

支持通过 table_metadata 参数获取真实的分片键信息，实现精确检测。
table_metadata 格式: {
    "table_name": {
        "shard_key": "user_id",
        "is_shard_table": True,
        ...
    }
}
"""
from typing import Optional

from backend.engine.parser import ParsedSQL
from backend.engine.rules.base import BaseRule
from backend.models import RuleCategory, Severity, Violation


class R020ShardKeyInWhere(BaseRule):
    """R020: 分布式表查询必须包含分片键字段"""

    rule_id = "R020"
    category = RuleCategory.DISTRIBUTED
    severity = Severity.WARNING
    description = "分布式表的SELECT/UPDATE/DELETE语句应在WHERE条件中包含分片键字段"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 分布式规范"
    fix_suggestion = "请在WHERE条件中添加分片键字段，如: WHERE shard_key = ? AND ..."

    # 常见分片键字段名模式（启发式备选）
    SHARD_KEY_PATTERNS = frozenset([
        "shard_key", "shardkey", "sharding_key", "shardingkey",
        "分片键", "partition_key",
    ])

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.sql_type not in ("SELECT", "UPDATE", "DELETE"):
            return None

        if not parsed.has_where:
            return None

        raw_lower = parsed.raw_sql.lower()

        # SQL 中明确标注了 shardkey 相关注释，跳过
        if "shardkey" in raw_lower or "shard_key" in raw_lower or "分片键" in raw_lower:
            return None

        # 优先使用真实元数据检测分片键
        if table_metadata:
            for table in parsed.tables:
                meta = table_metadata.get(table, {})
                shard_key = meta.get("shard_key")
                is_shard = meta.get("is_shard_table", False)
                if is_shard and shard_key:
                    # 检查 WHERE 条件中是否包含该分片键字段
                    where_lower = (parsed.where_clause or "").lower()
                    if shard_key.lower() not in where_lower:
                        return self._make_violation(
                            f"表 '{table}' 为分片表，其分片键 '{shard_key}' 未在WHERE条件中",
                            suggestion=f"请在WHERE条件中添加分片键字段，如: WHERE {shard_key} = ? AND ...",
                        )
                    return None

        # 启发式回退：多表 JOIN 时提醒
        if len(parsed.tables) >= 2:
            return self._make_violation(
                "多表关联查询请确认是否在WHERE/ON条件中包含分片键字段，避免广播到所有SET导致性能下降",
                suggestion="建议在WHERE条件中添加分片键字段，如: WHERE shard_key = ? AND ...",
            )

        return None


class R021ShardKeyUpdate(BaseRule):
    """R021: 禁止更新分片键字段"""

    rule_id = "R021"
    category = RuleCategory.DISTRIBUTED
    severity = Severity.ERROR
    description = "禁止对分片键(shardkey)字段进行UPDATE操作"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 分布式规范"
    fix_suggestion = "分片键决定数据路由，更新分片键会导致数据迁移，必须通过DBA审核"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.sql_type != "UPDATE":
            return None

        raw_lower = parsed.raw_sql.lower()

        # 从 UPDATE ... SET ... 中提取 SET 子句
        set_match = raw_lower.split(" set ")
        if len(set_match) <= 1:
            return None
        set_clause = set_match[1].split(" where ")[0] if " where " in set_match[1] else set_match[1]

        # 优先使用真实元数据检测
        if table_metadata:
            for table in parsed.tables:
                meta = table_metadata.get(table, {})
                shard_key = meta.get("shard_key")
                is_shard = meta.get("is_shard_table", False)
                if is_shard and shard_key:
                    # 检查 SET 子句中是否包含分片键字段
                    set_lower = set_clause.lower()
                    # 提取被更新的字段名（简单正则匹配 column = value）
                    import re
                    updated_fields = re.findall(r"([a-z_][a-z0-9_]*)\s*=", set_lower)
                    if shard_key.lower() in updated_fields:
                        return self._make_violation(
                            f"禁止更新分片键字段 '{shard_key}'（表 '{table}' 的分片键）",
                            suggestion="分片键决定数据路由，更新分片键会导致数据迁移，必须通过DBA审核",
                        )
                    return None

        # 启发式回退：检测常见分片键字段名
        shard_key_patterns = [
            "shard_key", "shardkey", "分片键",
        ]
        for pattern in shard_key_patterns:
            if pattern in set_clause:
                return self._make_violation(
                    f"禁止更新分片键字段（检测到 '{pattern}' 在SET子句中）",
                    suggestion="分片键决定数据路由，更新分片键会导致数据迁移，必须通过DBA审核",
                )

        return None


class R022GlobalDeleteWithoutShardKey(BaseRule):
    """R022: 禁止不带分片键的全局DELETE/UPDATE"""

    rule_id = "R022"
    category = RuleCategory.DISTRIBUTED
    severity = Severity.ERROR
    description = "分布式表禁止不带分片键的全局DELETE/UPDATE，防止跨所有SET执行"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 分布式规范"
    fix_suggestion = "请添加分片键的等值条件，并限制单次操作行数（建议≤1000行）"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.sql_type not in ("DELETE", "UPDATE"):
            return None

        if not parsed.has_where:
            return None

        raw_lower = parsed.raw_sql.lower()
        has_limit = "limit " in raw_lower

        # 优先使用真实元数据检测
        if table_metadata:
            for table in parsed.tables:
                meta = table_metadata.get(table, {})
                shard_key = meta.get("shard_key")
                is_shard = meta.get("is_shard_table", False)
                if is_shard and shard_key:
                    where_lower = (parsed.where_clause or "").lower()
                    if shard_key.lower() not in where_lower:
                        action = "DELETE" if parsed.sql_type == "DELETE" else "UPDATE"
                        return self._make_violation(
                            f"{action}语句缺少分片键 '{shard_key}' 条件，可能导致跨所有SET执行",
                            suggestion=f"请添加分片键的等值条件，并限制单次操作行数（建议≤1000行），如: {action} FROM {table} WHERE {shard_key} = ? LIMIT 1000",
                        )
                    return None

        # 启发式回退：无 LIMIT 的 DELETE/UPDATE 且没有明显等值条件
        if not has_limit:
            where_clause = parsed.where_clause or raw_lower
            has_eq_condition = "=" in where_clause and "!=" not in where_clause and "<>" not in where_clause
            if not has_eq_condition:
                return self._make_violation(
                    "DELETE/UPDATE语句的WHERE条件中未发现等值查询条件，在分布式场景下可能导致全SET扫描",
                    suggestion="建议添加分片键的等值条件，并限制单次操作行数（建议≤1000行），如: DELETE FROM ... WHERE shard_key = ? AND ... LIMIT 1000",
                )

        return None


# ═══════════════════════════════════════════════════════════════
# R053-R060: 新增分布式规范规则
# ═══════════════════════════════════════════════════════════════

class R053NoCrossShardJoin(BaseRule):
    """R053: 禁跨分片JOIN"""
    rule_id = "R053"
    category = RuleCategory.DISTRIBUTED
    severity = Severity.ERROR
    description = "分布式表JOIN时必须在分片键上关联，避免跨SET广播JOIN"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 分布式规范"
    fix_suggestion = "请确保JOIN条件包含分片键等值关联，如: JOIN t2 ON t1.shard_key = t2.shard_key"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not parsed.has_explicit_join or len(parsed.tables) < 2:
            return None
        if not table_metadata:
            # 无元数据时仅提示
            if len(parsed.tables) >= 2:
                return self._make_violation(
                    "多表JOIN请确保在分片键上关联，避免跨SET广播JOIN",
                )
            return None
        # 有元数据时检查分片键是否在JOIN条件中
        shard_keys = set()
        for table in parsed.tables:
            meta = table_metadata.get(table, {})
            sk = meta.get("shard_key")
            if sk:
                shard_keys.add(sk.lower())
        if shard_keys:
            raw_lower = parsed.raw_sql.lower()
            if not any(sk in raw_lower for sk in shard_keys):
                return self._make_violation(
                    f"多表JOIN未在分片键({','.join(shard_keys)})上关联，将导致跨SET广播JOIN",
                )
        return None


class R054ShardKeyMustBePrimaryKey(BaseRule):
    """R054: 分片键应为主键一部分"""
    rule_id = "R054"
    category = RuleCategory.DISTRIBUTED
    severity = Severity.WARNING
    description = "分片键字段必须是主键的一部分（或主键本身）"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 分布式规范"
    fix_suggestion = "请将分片键字段加入主键，如: PRIMARY KEY (shard_key, id)"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not parsed.is_create_table or not table_metadata:
            return None
        for table in parsed.tables:
            meta = table_metadata.get(table, {})
            shard_key = meta.get("shard_key", "")
            if shard_key:
                # 检查主键是否包含分片键
                pk_cols = set()
                for col in parsed.columns:
                    if col.get("is_primary_key"):
                        pk_cols.add(col.get("name", "").lower())
                if shard_key.lower() not in pk_cols:
                    return self._make_violation(
                        f"分片键 '{shard_key}' 不在主键中，TDSQL要求分片键必须是主键的一部分",
                    )
        return None


class R055NoGlobalIndexOnly(BaseRule):
    """R055: 禁纯全局索引"""
    rule_id = "R055"
    category = RuleCategory.DISTRIBUTED
    severity = Severity.WARNING
    description = "分布式表不建议仅依赖全局索引，应优先使用本地索引+分片键路由"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 分布式规范"
    fix_suggestion = "请优化查询确保走分片键路由，减少对全局索引的依赖"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.sql_type != "SELECT" or not table_metadata:
            return None
        for table in parsed.tables:
            meta = table_metadata.get(table, {})
            if meta.get("is_shard_table") and meta.get("shard_key"):
                if not parsed.has_where:
                    return self._make_violation(
                        f"分片表 '{table}' 的查询无WHERE条件，将触发全SET扫描+全局索引",
                    )
        return None


class R056SuggestShardKeyInOrderBy(BaseRule):
    """R056: ORDER BY建议包含分片键"""
    rule_id = "R056"
    category = RuleCategory.DISTRIBUTED
    severity = Severity.INFO
    description = "分布式表ORDER BY建议包含分片键，避免跨SET排序"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 分布式规范"
    fix_suggestion = "在ORDER BY中添加分片键字段，减少跨SET归并排序"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not parsed.has_order_by or not table_metadata:
            return None
        for table in parsed.tables:
            meta = table_metadata.get(table, {})
            shard_key = meta.get("shard_key", "")
            if shard_key:
                raw_lower = parsed.raw_sql.lower()
                if "order by" in raw_lower:
                    order_part = raw_lower.split("order by")[1].split("limit")[0]
                    if shard_key.lower() not in order_part:
                        return self._make_violation(
                            f"ORDER BY未包含分片键 '{shard_key}'，可能导致跨SET归并排序",
                        )
        return None


class R057NoBulkInsertWithoutShardKey(BaseRule):
    """R057: 批量INSERT必须含分片键"""
    rule_id = "R057"
    category = RuleCategory.DISTRIBUTED
    severity = Severity.ERROR
    description = "批量INSERT/REPLACE必须包含分片键字段，否则无法路由到正确SET"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 分布式规范"
    fix_suggestion = "请在INSERT字段列表中显式包含分片键"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.sql_type not in ("INSERT", "REPLACE") or not table_metadata:
            return None
        if not parsed.insert_columns:
            return None
        for table in parsed.tables:
            meta = table_metadata.get(table, {})
            shard_key = meta.get("shard_key")
            if shard_key and shard_key not in parsed.insert_columns:
                return self._make_violation(
                    f"批量INSERT未包含分片键 '{shard_key}'，数据无法路由到正确SET",
                )
        return None


class R058BatchUpdateLimit(BaseRule):
    """R058: 批量UPDATE/DELETE限制行数"""
    rule_id = "R058"
    category = RuleCategory.DISTRIBUTED
    severity = Severity.WARNING
    description = "分布式表批量UPDATE/DELETE建议加LIMIT限制单次影响行数(≤1000)"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 分布式规范"
    fix_suggestion = "请添加 LIMIT 1000 限制单次操作行数"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.sql_type not in ("UPDATE", "DELETE"):
            return None
        if not parsed.has_where:
            return None
        # 仅在分布式表上下文中检查（有元数据且表为分片表）
        if not table_metadata:
            return None
        is_shard = False
        for table in parsed.tables:
            meta = table_metadata.get(table, {})
            if meta.get("is_shard_table"):
                is_shard = True
                break
        if not is_shard:
            return None
        raw_lower = parsed.raw_sql.lower()
        if "limit" not in raw_lower:
            return self._make_violation(
                "分布式表批量UPDATE/DELETE未加LIMIT，可能导致长事务和锁等待",
            )
        return None


class R059NoDistributedTransaction(BaseRule):
    """R059: 禁分布式事务"""
    rule_id = "R059"
    category = RuleCategory.DISTRIBUTED
    severity = Severity.WARNING
    description = "避免跨SET分布式事务，单事务应只操作同一分片数据"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 分布式规范"
    fix_suggestion = "请拆分事务确保单事务只操作同一分片数据"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.is_begin and table_metadata:
            return self._make_violation(
                "BEGIN事务请确保后续操作只涉及同一分片数据，避免跨SET分布式事务",
            )
        return None


class R060ExplainShardKeyCheck(BaseRule):
    """R060: 分布式EXPLAIN检查"""
    rule_id = "R060"
    category = RuleCategory.DISTRIBUTED
    severity = Severity.INFO
    description = "建议对分布式表查询执行EXPLAIN查看是否命中单SET"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 分布式规范"
    fix_suggestion = "执行 EXPLAIN SELECT ... 查看shard_key是否命中单SET"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.sql_type != "SELECT" or not table_metadata:
            return None
        for table in parsed.tables:
            meta = table_metadata.get(table, {})
            if meta.get("is_shard_table") and meta.get("shard_key"):
                if not parsed.has_where:
                    return self._make_violation(
                        f"分片表 '{table}' 查询无WHERE条件，建议执行EXPLAIN确认是否全SET扫描",
                    )
        return None
