"""
TDSQL SQL审核工具 - 分布式规范规则 (R020-R022, R053-R060, R077)

R020: 分布式表查询必须包含分片键
R021: 禁止更新分片键字段
R022: 禁止不带分片键的全局DELETE/UPDATE
R077: 建表语句必须声明分片键(shard key)或广播表标记

支持通过 table_metadata 参数获取真实的分片键信息，实现精确检测。
table_metadata 格式: {
    "table_name": {
        "shard_key": "user_id",
        "is_shard_table": True,
        ...
    }
}
"""
import re
from typing import Optional

from backend.engine.parser import ParsedSQL
from backend.engine.rules.base import BaseRule
from backend.models import RuleCategory, Severity, Violation, InstanceScope


# ═══════════════════════════════════════════════════════════════
# TDSQL 内核建表语法补充识别（v1.6.1.9）
#
# TDSQL 的 SHOW CREATE TABLE 对分片表/广播表存在两种输出形态，均非
# 开发人员手写的 `shardkey=col`：
#   分片表:  TDSQL_DISTRIBUTED BY HASH(col)
#   广播表:  shardkey=noshardkey_allset
# 本模块的规则此前只认手写形态，导致内核输出被误报为"未声明分片键"。
# 以下助手提供共享的、经注释/字符串清洗且尾部锚定的语法识别，
# 由 R077 与 R054 共同消费。
# ═══════════════════════════════════════════════════════════════

_NOSHARDKEY_ALLSET = "noshardkey_allset"   # 广播表(全局表)哨兵，精确值

# 取值必须以合法终止符收尾，避免 `noshardkey_allset-x` 被截断成合法哨兵
_TOKEN_END = r"(?=\s|[,;)]|$)"

_TDSQL_HASH_RE = re.compile(
    r"\btdsql_distributed\s+by\s+hash\s*\(\s*"
    r"(?:`(?P<quoted>[^`]+)`|(?P<bare>[a-z_][a-z0-9_]*))\s*\)",
    re.IGNORECASE,
)
# legacy 形态。模式本身与原 R077._SHARDKEY_RE / _SHARD_KEY_RE 保持一致，
# 只追加 token 边界；真正的变化是"喂给它的文本"从整条 raw 换成可信尾部。
_SHARDKEY_TAIL_RE = re.compile(
    r"\bshardkey\b\s*=?\s*\(?[`\"']?([a-z_][a-z0-9_]*)[`\"']?\)?" + _TOKEN_END,
    re.IGNORECASE,
)
_SHARD_KEY_TAIL_RE = re.compile(
    r"\bshard_key\b\s*=?\s*\(?[`\"']?([a-z_][a-z0-9_]*)[`\"']?\)?" + _TOKEN_END,
    re.IGNORECASE,
)


def _strip_sql_noise(sql: str) -> str:
    """剔除行/块注释与引号字符串字面量；保留反引号标识符。

    `--` 按 MySQL 5.7/8.0 词法处理：第二个 `-` 之后必须紧跟空白或控制字符
    才构成注释，否则 `CHECK(a--b > 0)` 会被误当注释截断，反把合法 HASH 表
    打成 R077 误报。参考 MySQL Reference Manual — Comments。

    ⚠️ 不得改用 backend/engine/rules/oracle_compat.py 的 clean_sql()：
       它的 _LINE_COMMENT = r"--[^\n]*" 正是上面这个词法缺陷（见 ADJ-8），
       且它会剔除反引号内容之外的大小写信息。换用它等于把已修复的
       BLOCK-3 重新装回来。二者看似重复，实为不同契约，请勿"DRY 化"。

    仅用于语法形态判定，不改变 parsed.raw_sql。
    """
    out, i, n = [], 0, len(sql)
    while i < n:
        c = sql[i]
        if c == '`':                                   # 反引号标识符：整体保留
            j = sql.find('`', i + 1)
            if j < 0:
                out.append(sql[i:]); break
            out.append(sql[i:j + 1]); i = j + 1
        elif c in ("'", '"'):                          # 字符串字面量：整体丢弃
            q, j = c, i + 1
            while j < n:
                if sql[j] == '\\':
                    j += 2; continue
                if sql[j] == q:
                    if j + 1 < n and sql[j + 1] == q:  # '' / "" 转义
                        j += 2; continue
                    break
                j += 1
            out.append(' '); i = j + 1
        elif (sql.startswith('--', i)
              and (i + 2 >= n or sql[i + 2].isspace())) or c == '#':
            j = sql.find('\n', i); out.append(' ')     # 行注释
            i = n if j < 0 else j
        elif sql.startswith('/*', i):                  # 块注释
            j = sql.find('*/', i + 2); out.append(' ')
            i = n if j < 0 else j + 2
        else:
            out.append(c); i += 1
    return ''.join(out)


def _ddl_options_tail(cleaned: str) -> str:
    """返回列定义清单右括号之后的表选项尾部；定位不到时返回空串（保守：不识别）。

    必须在 _strip_sql_noise 之后调用——字符串里的括号已被剔除，配对才可靠。
    """
    start = cleaned.find('(')
    if start < 0:
        return ""
    depth = 0
    for i in range(start, len(cleaned)):
        if cleaned[i] == '(':
            depth += 1
        elif cleaned[i] == ')':
            depth -= 1
            if depth == 0:
                return cleaned[i + 1:]
    return ""


def _trusted_options_tail(raw_sql: str) -> str:
    """一切 raw SQL 语法判定的唯一可信输入：清洗 + 表选项尾部锚定。"""
    return _ddl_options_tail(_strip_sql_noise(raw_sql))


def _extract_legacy_shard_key(raw_sql: str) -> str:
    """SHARDKEY= / SHARD_KEY= 的 raw 回退提取（限定在可信尾部内）。"""
    tail = _trusted_options_tail(raw_sql)
    if not tail:
        return ""
    for pat in (_SHARDKEY_TAIL_RE, _SHARD_KEY_TAIL_RE):
        m = pat.search(tail)
        if m:
            return m.group(1).strip('`"\' ').lower()
    return ""


def _extract_tdsql_hash_key(raw_sql: str) -> str:
    """TDSQL_DISTRIBUTED BY HASH(col) 的分片键提取（限定在可信尾部内）。"""
    tail = _trusted_options_tail(raw_sql)
    if not tail:
        return ""
    m = _TDSQL_HASH_RE.search(tail)
    if not m:
        return ""
    return (m.group('quoted') or m.group('bare')).strip('` ').lower()


def _is_broadcast_sentinel(value: str) -> bool:
    """精确判定广播表(全局表)哨兵值（大小写不敏感）。

    仅接受 noshardkey_allset。新增哨兵只能通过有出处的显式白名单扩展，
    不得改回前缀匹配——`noshardkey_*` 不是 TDSQL 保留命名空间。
    调用方必须保证 value 来自可信来源（table_metadata / table_options /
    上面两个 _extract_* 助手），否则等于把注释文本当成放行凭据。
    """
    return value.strip('`"\' ').casefold() == _NOSHARDKEY_ALLSET


_UNIQUE_IDX_RE = re.compile(
    r"\bunique\s+(?:key|index)\s*(?:`(?P<qname>[^`]+)`|(?P<bname>\w+))?\s*\(([^)]+)\)",
    re.IGNORECASE,
)


def _iter_unique_indexes(parsed: ParsedSQL, raw_sql: str):
    """R054 专属：逐个产出完整唯一约束；不得被 R077 复用。"""
    seen = set()
    structured_column_sets = set()
    # 每个结构化条目自身都已经通过严格 helper；全局 incomplete 只表示可能还有
    # 条目未提取，不表示已提取条目不可信。先产出它们，覆盖列级/裸 UNIQUE。
    for idx in parsed.unique_constraints:
        name = idx.get("name") or "UNIQUE索引"
        columns = {c.lower() for c in idx.get("columns", [])}
        identity = (str(name).lower(), frozenset(columns))
        if identity not in seen:
            seen.add(identity)
            structured_column_sets.add(frozenset(columns))
            yield name, columns
    if getattr(parsed, "unique_constraints_complete", False):
        return
    # 不完整路径再用既有 raw 回退补充 AST 未表达的 UNIQUE KEY/INDEX；按
    # (规范名, 列集合) 去重，避免同一条约束被重复消费。
    def _raw_base_column(fragment: str) -> str:
        value = fragment.strip()
        value = re.sub(r"\s+(?:asc|desc)\s*$", "", value, flags=re.IGNORECASE)
        # `_UNIQUE_IDX_RE` 在前缀长度的内层 `)` 处停止，故右括号可有可无。
        value = re.sub(r"\(\s*\d+\s*\)?$", "", value).strip()
        return value.strip('`"\' ').lower()

    for m in _UNIQUE_IDX_RE.finditer(_strip_sql_noise(raw_sql)):
        name = m.group('qname') or m.group('bname') or "UNIQUE索引"
        # 既有正则在 `col(n)` 的内层右括号处停止；去掉末尾正整数前缀长度，
        # 与结构化 helper 的“只保留基列”语义对齐，避免同一索引被误判成第二条。
        columns = {
            _raw_base_column(c)
            for c in m.group(3).split(",")
        }
        identity = (str(name).lower(), frozenset(columns))
        if identity not in seen and frozenset(columns) not in structured_column_sets:
            seen.add(identity)
            yield name, columns


class R020ShardKeyInWhere(BaseRule):
    """R020: 分布式表查询必须包含分片键字段"""

    rule_id = "R020"
    instance_scope = InstanceScope.DISTRIBUTED
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
    instance_scope = InstanceScope.DISTRIBUTED
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
    instance_scope = InstanceScope.DISTRIBUTED
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
    instance_scope = InstanceScope.DISTRIBUTED
    category = RuleCategory.DISTRIBUTED
    severity = Severity.ERROR
    description = "分布式表JOIN时必须在分片键上关联，避免跨SET广播JOIN"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 分布式规范"
    fix_suggestion = "请确保JOIN条件包含分片键等值关联，如: JOIN t2 ON t1.shard_key = t2.shard_key。关联条件均为分片键且有固定值→完全下推；无固定值→join下推并尽量过滤；小配置表设为广播表使join下推；均不可下推时按日期拆分请求/用单分片中间表落数据后再join；子查询表尽量把过滤条件写入子查询内"

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
    instance_scope = InstanceScope.DISTRIBUTED
    category = RuleCategory.DISTRIBUTED
    severity = Severity.WARNING
    description = "分片键必须包含在主键及所有唯一索引中（唯一索引不含分片键将无法创建）"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 分布式规范"
    fix_suggestion = "请将分片键字段加入主键，如: PRIMARY KEY (shard_key, id)；同时确保所有UNIQUE索引也包含分片键"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        # DDL上下文判断: AST解析 + raw_sql兜底(TDSQL shardkey=语法sqlglot不认)
        is_ddl = parsed.is_create_table
        if not is_ddl:
            raw = parsed.raw_sql
            if re.match(r"\s*create\s+(global\s+)?(temporary\s+)?table\b", raw, re.IGNORECASE):
                is_ddl = True
        if not is_ddl:
            return None

        # 获取分片键: 优先 table_metadata, 回退 raw_sql 正则
        shard_key = ""
        if table_metadata:
            for table in parsed.tables:
                meta = table_metadata.get(table, {})
                sk = meta.get("shard_key", "")
                if sk:
                    shard_key = sk
                    break
        if not shard_key:
            # v1.6.1.9: legacy 回退与 R077 共用同一可信来源
            #（清洗 + 表选项尾部锚定），不再 search 整条 raw_sql
            shard_key = _extract_legacy_shard_key(parsed.raw_sql)
        if not shard_key:
            # v1.6.1.9 新增来源: TDSQL_DISTRIBUTED BY HASH(col)
            shard_key = _extract_tdsql_hash_key(parsed.raw_sql)
        if not shard_key:
            return None

        # v1.6.1.9: 广播表(全局表) —— noshardkey_allset 是哨兵值而非列名
        if _is_broadcast_sentinel(shard_key):
            return None

        # 检查主键是否包含分片键
        pk_cols = set()
        for col in parsed.columns:
            if col.get("is_primary_key"):
                pk_cols.add(col.get("name", "").lower())
        for idx in parsed.indexes:
            if idx.get("type") == "PRIMARY":
                pk_cols.update(c.lower() for c in idx.get("columns", []))
        if not pk_cols:
            pk_match = re.search(
                r"primary\s+key\s*(?:using\s+\w+\s*)?\(([^)]+)\)",
                parsed.raw_sql, re.IGNORECASE,
            )
            if pk_match:
                pk_cols = {
                    c.strip('`"\' ').lower()
                    for c in pk_match.group(1).split(",")
                }
        # v1.6.1.9: 空主键集合同样是 J-2 失败
        if shard_key.lower() not in pk_cols:
            if not pk_cols:
                return self._make_violation(
                    f"建表语句未声明主键，分片键 '{shard_key}' 必须是主键的一部分",
                )
            return self._make_violation(
                f"分片键 '{shard_key}' 不在主键中，TDSQL要求分片键必须是主键的一部分",
            )

        # E2: J-3 —— 每一个唯一索引都必须包含分片键（逐个判断，不展平）
        for idx_name, idx_cols in _iter_unique_indexes(parsed, parsed.raw_sql):
            if shard_key.lower() not in idx_cols:
                return self._make_violation(
                    f"{idx_name}未包含分片键 '{shard_key}'，TDSQL要求唯一索引必须包含分片键",
                )
        return None


class R055NoGlobalIndexOnly(BaseRule):
    """R055: 禁纯全局索引"""
    rule_id = "R055"
    instance_scope = InstanceScope.DISTRIBUTED
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
    instance_scope = InstanceScope.DISTRIBUTED
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
    instance_scope = InstanceScope.DISTRIBUTED
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
    instance_scope = InstanceScope.DISTRIBUTED
    category = RuleCategory.DISTRIBUTED
    severity = Severity.WARNING
    description = "分布式表批量UPDATE/DELETE建议加LIMIT限制单次影响行数(≤1000)"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 分布式规范"
    fix_suggestion = "请添加 LIMIT 1000 限制单次操作行数。注意：update/delete…limit依赖proxy内嵌myisam临时表，主键varchar长度须<250(utf8mb4)/<333(utf8)，详见R115"

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
    instance_scope = InstanceScope.DISTRIBUTED
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
    instance_scope = InstanceScope.DISTRIBUTED
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


# ═══════════════════════════════════════════════════════════════
# R077: 建表语句必须声明分片键
# ═══════════════════════════════════════════════════════════════

class R077CreateTableMustHaveShardKey(BaseRule):
    """R077: 建表语句必须声明分片键(shard key)或广播表标记

    TDSQL分布式实例上只允许创建分片表和广播表，不允许创建单表。
    分片表必须声明 SHARDKEY，且分片键必须是主键或唯一索引的一个字段。
    广播表必须声明 BROADCAST。

    注意: R054 也在有 table_metadata 时检查分片键是否在主键中，
    两者存在职责重叠。实际文件审核场景下 table_metadata 为 None，
    只有 R077 会触发；有元数据时两者均可能触发但消息不同不算冲突。
    """
    rule_id = "R077"
    instance_scope = InstanceScope.DISTRIBUTED
    category = RuleCategory.DISTRIBUTED
    severity = Severity.ERROR
    description = (
        "TDSQL分布式实例建表必须声明分片键或广播表标记，不允许创建单表；"
        "分片键必须是主键或唯一索引的字段。支持的分片键声明形态："
        "SHARDKEY=列名、TDSQL_DISTRIBUTED BY HASH(列名)；"
        "广播表(全局表)形态：BROADCAST、shardkey=noshardkey_allset"
    )
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 分布式建表规范"
    fix_suggestion = (
        "请按目标实例支持的形态声明分片键或广播表。示例:\n"
        "  CREATE TABLE t1 (...) ENGINE=InnoDB SHARDKEY=user_id\n"
        "  CREATE TABLE t1 (...) ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(`user_id`)\n"
        "  CREATE TABLE t1 (...) ENGINE=InnoDB BROADCAST\n"
        "  CREATE TABLE t1 (...) ENGINE=InnoDB shardkey=noshardkey_allset\n"
        "分片表的分片键必须是主键(或主键的一部分)，且必须包含在每一个唯一索引中。"
    )

    # 分片键声明的正则模式（已迁至模块级 _SHARDKEY_TAIL_RE / _SHARD_KEY_TAIL_RE，
    # 由 _extract_shard_key() 经 _trusted_options_tail() 清洗后调用）
    _BROADCAST_RE = re.compile(r"\bbroadcast\b", re.IGNORECASE)
    # 表级 PRIMARY KEY 列提取正则（回退方案，兼容 USING BTREE 语法）
    _PK_RE = re.compile(
        r"primary\s+key\s*(?:using\s+\w+\s*)?\(([^)]+)\)",
        re.IGNORECASE,
    )
    # ⚠️ 不得单独放宽本正则：R077 仍保留 legacy 的"主键 或 唯一索引"判定
    #    （ADJ-4，已决策不收紧）。本正则一旦认出更多唯一索引，就会激活那个
    #    宽松分支并产生漏报。修改本正则、或让 parsed.indexes 开始产出 UNIQUE
    #    条目时，必须在同一次提交内把 R077 判定对齐 J-2/J-3，并通过
    #    tests/test_r077_r054_tdsql_syntax.py 中裸索引名/反引号索引名两组
    #    同语义用例。不得拆分提交。
    #    背景：docs/DESIGN-v1.6.1.9-TDSQL分片表与广播表建表语法识别缺陷修复详细设计说明书.md
    # 表级 UNIQUE KEY/INDEX 列提取正则（回退方案）
    _UNIQUE_RE = re.compile(
        r"unique\s+(?:key|index)\s+\w*\s*\(([^)]+)\)",
        re.IGNORECASE,
    )

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not parsed.is_create_table:
            return None

        # 跳过 CREATE TABLE ... SELECT（CTAS 语句）
        if parsed.is_create_table_select:
            return None

        # 跳过临时表
        if parsed.is_temporary_table:
            return None

        raw_sql = parsed.raw_sql

        # 检查是否声明了 BROADCAST（广播表不需要分片键）
        if self._BROADCAST_RE.search(raw_sql):
            return None

        # 提取分片键列名（优先使用解析器结构化数据，回退到正则）
        shard_key_col = self._extract_shard_key(parsed, raw_sql)

        # v1.6.1.9: 广播表(全局表)哨兵，精确等值且来源可信
        if shard_key_col and _is_broadcast_sentinel(shard_key_col):
            return None

        if not shard_key_col:
            # 未声明分片键，也未声明广播表 → 违规
            table_name = parsed.tables[0] if parsed.tables else ""
            return self._make_violation(
                f"建表语句未声明分片键(SHARDKEY)或广播表标记(BROADCAST)，"
                f"TDSQL分布式实例上不允许创建单表{f'（表 {table_name}）' if table_name else ''}。"
                f"分片表必须通过 SHARDKEY=列名 声明分片键，广播表必须通过 BROADCAST 声明",
                suggestion=self.fix_suggestion,
            )

        # 已声明分片键，检查是否为主键或唯一索引的字段
        pk_cols = self._collect_pk_cols(parsed, raw_sql)
        unique_index_cols = self._collect_unique_index_cols(parsed, raw_sql)

        if shard_key_col not in pk_cols and shard_key_col not in unique_index_cols:
            return self._make_violation(
                f"分片键 '{shard_key_col}' 不在主键或唯一索引中，"
                f"TDSQL要求分片键必须是主键或唯一索引的一个字段",
                suggestion=(
                    f"请将分片键 '{shard_key_col}' 加入主键，如: PRIMARY KEY ({shard_key_col}, id)，"
                    f"或为该列创建唯一索引"
                ),
            )

        return None

    def _extract_shard_key(self, parsed: ParsedSQL, raw_sql: str) -> str:
        """提取分片键列名，优先结构化数据，回退到清洗且尾部锚定的正则"""
        # 优先来源: parsed.table_options（sqlglot 已解析的表选项，可信）
        for key in ("SHARDKEY", "SHARD_KEY"):
            val = parsed.table_options.get(key, "")
            if val:
                return val.strip('`"\' ').lower()
        # 回退来源1: legacy SHARDKEY= / SHARD_KEY=（可信尾部）
        legacy = _extract_legacy_shard_key(raw_sql)
        if legacy:
            return legacy
        # 回退来源2: TDSQL_DISTRIBUTED BY HASH(col)（可信尾部）
        return _extract_tdsql_hash_key(raw_sql)

    def _collect_pk_cols(self, parsed: ParsedSQL, raw_sql: str) -> set[str]:
        """收集主键列名（三个来源合并，确保不遗漏）"""
        pk_cols = set()
        # 来源1: 列级 PRIMARY KEY 标记
        for col in parsed.columns:
            if col.get("is_primary_key"):
                pk_cols.add(col.get("name", "").lower())
        # 来源2: 表级 PRIMARY KEY (col1, col2) 声明（parsed.indexes）
        for idx in parsed.indexes:
            if idx.get("type") == "PRIMARY":
                pk_cols.update(c.lower() for c in idx.get("columns", []))
        # 来源3: 正则回退——从原始SQL提取表级 PRIMARY KEY 声明
        pk_match = self._PK_RE.search(raw_sql)
        if pk_match:
            pk_cols.update(
                c.strip('`"\' ').lower()
                for c in pk_match.group(1).split(",")
            )
        return pk_cols

    def _collect_unique_index_cols(self, parsed: ParsedSQL, raw_sql: str) -> set[str]:
        """收集唯一索引列名（两个来源合并）"""
        unique_index_cols = set()
        # 来源1: parsed.indexes
        for idx in parsed.indexes:
            if idx.get("type") == "UNIQUE":
                unique_index_cols.update(c.lower() for c in idx.get("columns", []))
        # 来源2: 正则回退——从原始SQL提取表级 UNIQUE KEY/INDEX 声明
        for m in self._UNIQUE_RE.finditer(raw_sql):
            cols = {c.strip('`"\' ').lower() for c in m.group(1).split(",")}
            unique_index_cols.update(cols)
        return unique_index_cols
