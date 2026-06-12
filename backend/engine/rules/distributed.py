"""
TDSQL SQL审核工具 - 分布式规范规则 (R020-R022)

R020: 分布式表查询必须包含分片键
R021: 禁止更新分片键字段
R022: 禁止不带分片键的全局DELETE/UPDATE
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

    def check(self, parsed: ParsedSQL) -> Optional[Violation]:
        if parsed.sql_type not in ("SELECT", "UPDATE", "DELETE"):
            return None

        # 如果没有 WHERE 条件，由 R013/R014 处理
        if not parsed.has_where:
            return None

        # 检查 WHERE 中是否包含分片键提示
        # 在实际场景中，分片键信息需要从元数据获取
        # 这里检查是否有 shardkey 注释或配置
        raw_lower = parsed.raw_sql.lower()

        # 如果SQL中明确标注了 shardkey 相关注释，跳过
        if "shardkey" in raw_lower or "shard_key" in raw_lower or "分片键" in raw_lower:
            return None

        # 对于没有分片信息的SQL，给出提示性建议
        # 只在涉及多表或看起来是分布式场景时提醒
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

    def check(self, parsed: ParsedSQL) -> Optional[Violation]:
        if parsed.sql_type != "UPDATE":
            return None

        raw_lower = parsed.raw_sql.lower()

        # 常见分片键字段名模式
        shard_key_patterns = [
            "shard_key", "shardkey", "分片键",
        ]

        # 检查 SET 子句中是否包含分片键相关字段
        # 从 UPDATE ... SET ... 中提取 SET 子句
        set_match = raw_lower.split(" set ")
        if len(set_match) > 1:
            set_clause = set_match[1].split(" where ")[0]
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

    def check(self, parsed: ParsedSQL) -> Optional[Violation]:
        if parsed.sql_type not in ("DELETE", "UPDATE"):
            return None

        # 没有 WHERE 的情况由 R013/R014 处理
        if not parsed.has_where:
            return None

        raw_lower = parsed.raw_sql.lower()

        # 检查 WHERE 子句中是否有分片键相关的等值条件
        # 通过检查 WHERE 中是否有 LIMIT 来辅助判断
        has_limit = "limit " in raw_lower

        # 对于大表的 DELETE/UPDATE，如果没有 LIMIT 也没有明显的分片键条件，给出警告
        # 这是一个启发式检查，实际分片键信息需要从元数据获取
        if parsed.sql_type == "DELETE" and not has_limit:
            where_clause = parsed.where_clause or raw_lower
            # 检查是否有明显的等值条件（至少一个 = 条件）
            has_eq_condition = "=" in where_clause and "!=" not in where_clause and "<>" not in where_clause
            if not has_eq_condition:
                return self._make_violation(
                    "DELETE语句的WHERE条件中未发现等值查询条件，在分布式场景下可能导致全SET扫描",
                    suggestion="建议添加分片键的等值条件，并限制单次操作行数（建议≤1000行），如: DELETE FROM ... WHERE shard_key = ? AND ... LIMIT 1000",
                )

        return None
