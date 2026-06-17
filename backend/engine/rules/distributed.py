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
