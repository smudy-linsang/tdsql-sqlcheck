"""
TDSQL 分布式架构深度检测器 (TDSQLAuditor)
"""
from dataclasses import dataclass
import sqlglot.expressions as exp


@dataclass
class TDSQLFinding:
    rule_id: str
    severity: str
    message: str
    suggestion: str


class TDSQLAuditor:
    """TDSQL 特性检测：分片键缺失推演、GSI 同步延迟及广播表锁表"""

    def check_shard_key_presence(self, expression: exp.Expression, shard_keys: list[str]) -> list[TDSQLFinding]:
        findings = []
        if not shard_keys or not expression:
            return findings

        # 仅针对 DML 校验
        if not isinstance(expression, (exp.Select, exp.Update, exp.Delete)):
            return findings

        where = expression.find(exp.Where)
        if not where:
            findings.append(TDSQLFinding(
                rule_id="DIST_001",
                severity="ERROR",
                message="DML 语句缺少 WHERE 条件，将触发全节点全表广播扫描！",
                suggestion=f"必须提供分片键 ({', '.join(shard_keys)}) 的过滤条件"
            ))
            return findings

        used_cols = {col.name.lower() for col in where.find_all(exp.Column)}
        if not any(sk.lower() in used_cols for sk in shard_keys):
            findings.append(TDSQLFinding(
                rule_id="DIST_002",
                severity="WARNING",
                message=f"WHERE 条件未覆盖分片键 [{', '.join(shard_keys)}]，查询将被路由广播至所有 SET/Shard 节点！",
                suggestion="建议在条件中引入分片键或改为点对点精准路由。"
            ))
        return findings

    def check_broadcast_table_write(self, expression: exp.Expression, is_broadcast: bool) -> list[TDSQLFinding]:
        findings = []
        if is_broadcast and isinstance(expression, (exp.Update, exp.Delete, exp.Insert)):
            findings.append(TDSQLFinding(
                rule_id="DIST_003",
                severity="WARNING",
                message="对广播表执行变更操作会触发全局分布式锁并同步所有 SET 节点，高频写操作将导致性能骤降！",
                suggestion="建议将广播表变更安排在业务低峰期批量执行。"
            ))
        return findings
