# -*- coding: utf-8 -*-
"""v1.6.3.4 / D03：TDSQL 二级分区主表结构识别器（REQ-02）

设计出处：docs/DETAIL-v1.6.3.4-报告实例标识与分区统计及审核网关修复.md §4.2

本识别器消费**目标实例成功返回的 SHOW CREATE TABLE**，做结构识别，
不代替核心审核器证明 SQL 可执行。词法模块使用锁定版本 sqlglot tokenizer，
但不得调用/更改 _plan_recovery 的方言准入规则。

接口：
    classify_logical_ddl(ddl, expected_database, expected_table) -> ShapeEvidence

ShapeEvidence 字段：
    state          : SECONDARY | NOT_SECONDARY | UNKNOWN
    distribution   : SHARDKEY_HASH | TDSQL_RANGE | TDSQL_LIST | TDSQL_HASH
                     | BROADCAST | NONE | UNKNOWN
    partition      : RANGE | LIST | NONE | UNKNOWN
    syntax_family  : LEGACY | MODERN | NONE | UNKNOWN
    reason_code    : 固定枚举；不得携带完整 DDL

关键约束（§4.2）：
  · 表定义内的列名、COMMENT 字符串、普通注释均不得贡献 distribution/partition
    关键词。
  · MySQL 可执行版本注释 `/*!版本号 … */` 不能作为普通注释丢弃；仅对完整片段
    解析内部 token，并保留位置/边界。
  · 禁止使用 re.search('partition.*by', ddl)、'_tdsql_sub' in name、字段
    SUBPARTITION_NAME 或子表名称前缀数量推断主表数。
  · 多处分布声明冲突、重复分区头、未知 TDSQL 结构、未闭合列表返回 UNKNOWN。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

import sqlglot
from sqlglot.tokens import TokenType

logger = logging.getLogger("tdsql.table_shape")

# ══════════════════════════════════════════════════════════════════
# 枚举常量
# ══════════════════════════════════════════════════════════════════

# state
STATE_SECONDARY = "SECONDARY"
STATE_NOT_SECONDARY = "NOT_SECONDARY"
STATE_UNKNOWN = "UNKNOWN"

# distribution
DIST_SHARDKEY_HASH = "SHARDKEY_HASH"
DIST_TDSQL_RANGE = "TDSQL_RANGE"
DIST_TDSQL_LIST = "TDSQL_LIST"
DIST_TDSQL_HASH = "TDSQL_HASH"
DIST_BROADCAST = "BROADCAST"
DIST_NONE = "NONE"
DIST_UNKNOWN = "UNKNOWN"

# partition
PART_RANGE = "RANGE"
PART_LIST = "LIST"
PART_NONE = "NONE"
PART_UNKNOWN = "UNKNOWN"

# syntax_family
FAMILY_LEGACY = "LEGACY"      # 旧语法：shardkey=... + PARTITION BY
FAMILY_MODERN = "MODERN"      # 新语法：TDSQL_DISTRIBUTED BY + TDSQL_PARTITION BY
FAMILY_NONE = "NONE"
FAMILY_UNKNOWN = "UNKNOWN"

# 广播标记（§4.2 第 5 条：优先识别精确 shardkey=noshardkey_allset 为广播）
_BROADCAST_MARKER = "NOSHARDKEY_ALLSET"

# 可执行版本注释正则：/*!版本号 ... */
# MySQL 官方：/*!40101 SET ... */ 形式，版本号可选。
_VERSIONED_COMMENT_RE = re.compile(r"/\*!(\d*)(.*?)\*/", re.DOTALL)


# ══════════════════════════════════════════════════════════════════
# 数据结构
# ══════════════════════════════════════════════════════════════════

@dataclass
class ShapeEvidence:
    """二级分区主表结构识别证据（§4.2）。"""
    state: str = STATE_UNKNOWN
    distribution: str = DIST_UNKNOWN
    partition: str = PART_UNKNOWN
    syntax_family: str = FAMILY_UNKNOWN
    reason_code: str = ""

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "distribution": self.distribution,
            "partition": self.partition,
            "syntax_family": self.syntax_family,
            "reason_code": self.reason_code,
        }


# ══════════════════════════════════════════════════════════════════
# 词法辅助
# ══════════════════════════════════════════════════════════════════

def _tokenize(ddl: str, dialect: str = "mysql"):
    """用 sqlglot 词法器 token 化 DDL。失败返回 None。"""
    try:
        return sqlglot.Dialect.get_or_raise(dialect).tokenizer_class().tokenize(ddl)
    except Exception as e:
        logger.debug("table_shape tokenize 失败: %s", e)
        return None


def _tok_text_upper(tok) -> str:
    """token 文本的大写归一（剥反引号/引号）。"""
    t = (tok.text or "")
    return t.strip("`'\"").upper()


def _is_bare_kw(tok, word: Optional[str] = None) -> bool:
    """是否为裸关键字 token（排除字符串字面量与反引号标识符）。

    与 parser_legacy._is_bare_kw 同制，避免两套模型漂移。
    """
    _NON_KW = (TokenType.STRING, TokenType.IDENTIFIER, TokenType.BACKSLASH,
               TokenType.BIT_STRING, TokenType.HEX_STRING)
    if tok.token_type in _NON_KW:
        return False
    return True if word is None else _tok_text_upper(tok) == word


def _find_create_table_head(toks) -> Optional[int]:
    """定位 CREATE [TEMPORARY] TABLE 头，返回 TABLE token 之后的位置。

    验证 CREATE TABLE 头（§4.2 步骤 1）。失败返回 None。
    """
    n = len(toks)
    i = 0
    # 跳过前导注释/空白
    while i < n and toks[i].token_type == TokenType.COMMENT:
        i += 1
    if i >= n or not _is_bare_kw(toks[i], "CREATE"):
        return None
    i += 1
    # 可选 TEMPORARY
    if i < n and _is_bare_kw(toks[i], "TEMPORARY"):
        i += 1
    # 必须 TABLE
    if i >= n or not _is_bare_kw(toks[i], "TABLE"):
        return None
    i += 1
    # 可选 IF NOT EXISTS
    if (i + 2 < n and _is_bare_kw(toks[i], "IF")
            and _is_bare_kw(toks[i + 1], "NOT")
            and _is_bare_kw(toks[i + 2], "EXISTS")):
        i += 3
    return i


def _match_qualified_name(toks, start: int,
                          expected_db: str, expected_table: str):
    """验证限定表名与请求一致（§4.2 步骤 1）。

    返回 (新位置, 是否匹配)。反引号中的转义反引号正确解码。
    """
    n = len(toks)
    i = start
    # 跳过注释
    while i < n and toks[i].token_type == TokenType.COMMENT:
        i += 1
    if i >= n:
        return i, False

    # 取第一个标识符（可能是 db 或 table）
    def _read_ident(pos):
        # 跳过注释
        while pos < n and toks[pos].token_type == TokenType.COMMENT:
            pos += 1
        if pos >= n:
            return pos, ""
        tok = toks[pos]
        # 标识符可以是 VAR / IDENTIFIER（反引号）/ 关键字（表名与关键字重名时）
        text = (tok.text or "")
        # 反引号标识符：剥外层反引号，内部 `` 转义为 `
        if tok.token_type == TokenType.IDENTIFIER and text.startswith("`"):
            inner = text[1:-1] if text.endswith("`") and len(text) >= 2 else text.strip("`")
            inner = inner.replace("``", "`")
            return pos + 1, inner
        # 字符串字面量不能作表名
        if tok.token_type == TokenType.STRING:
            return pos + 1, ""
        return pos + 1, text.strip("`'\"")

    i, name1 = _read_ident(i)
    if not name1:
        return i, False

    # 检查是否有 . 分隔符（db.table）
    j = i
    while j < n and toks[j].token_type == TokenType.COMMENT:
        j += 1
    if j < n and toks[j].token_type == TokenType.DOT:
        j += 1
        j, name2 = _read_ident(j)
        if not name2:
            return j, False
        # name1 是 db，name2 是 table
        db_ok = (name1 == expected_db) if expected_db else True
        tbl_ok = (name2 == expected_table) if expected_table else True
        return j, (db_ok and tbl_ok)
    else:
        # 只有 table 名（无 db 限定）
        # 若期望有 db，则不匹配（SHOW CREATE TABLE 通常返回 `db`.`table` 或 `table`）
        tbl_ok = (name1 == expected_table) if expected_table else True
        # db 未限定：若 expected_db 非空，保守认为匹配（Proxy 可能只返回表名）
        return i, tbl_ok


def _find_def_list_close_paren(toks, start: int) -> int:
    """找到表定义列表的配对右括号位置（§4.2 步骤 1）。

    从 start（左括号后）开始，配对括号跳过表达式与定义列表。
    返回右括号的下标；未闭合返回 -1。
    """
    n = len(toks)
    depth = 1  # 已经在左括号内
    i = start
    while i < n:
        tt = toks[i].token_type
        if tt == TokenType.L_PAREN:
            depth += 1
        elif tt == TokenType.R_PAREN:
            depth -= 1
            if depth == 0:
                return i
            if depth < 0:
                return -1
        i += 1
    return -1  # 未闭合


# ══════════════════════════════════════════════════════════════════
# 可执行版本注释处理（§4.2 步骤 3）
# ══════════════════════════════════════════════════════════════════

def _extract_versioned_comment_segments(ddl: str):
    """提取完整的可执行版本注释片段 /*!版本号 ... */。

    返回 [(start, end, version_str, inner_sql), ...]。
    不跨字符串匹配，不把普通提示注释当 SQL。
    无法确定版本条件或不完整片段则不返回（调用方按 UNKNOWN 处理）。

    注意：sqlglot tokenizer 会把 /*!...*/ 当作普通注释跳过，因此本函数
    在原始 DDL 文本上用正则提取，再把内部 SQL 单独 token 化。
    """
    segments = []
    for m in _VERSIONED_COMMENT_RE.finditer(ddl):
        version_str = m.group(1) or ""
        inner = m.group(2) or ""
        segments.append((m.start(), m.end(), version_str, inner))
    return segments


# ══════════════════════════════════════════════════════════════════
# 表尾解析（§4.2 步骤 4—5）
# ══════════════════════════════════════════════════════════════════

@dataclass
class _TailFacts:
    """表尾解析产出的事实集合。"""
    shardkey_value: Optional[str] = None       # SHARDKEY = <identifier> 的值
    shardkey_is_broadcast: bool = False        # shardkey=noshardkey_allset
    tdsql_distributed: Optional[str] = None    # TDSQL_DISTRIBUTED BY <HASH/RANGE/LIST>
    tdsql_partition: Optional[str] = None      # TDSQL_PARTITION BY <RANGE/LIST>
    partition_by: Optional[str] = None         # PARTITION BY <RANGE/LIST>
    partition_columns: bool = False            # PARTITION BY ... COLUMNS
    tdsql_partition_columns: bool = False      # TDSQL_PARTITION BY ... COLUMNS
    distribution_count: int = 0                # 分布声明计数（冲突检测）
    partition_count: int = 0                   # 分区头计数（重复检测）
    unknown_structure: bool = False            # 未知 TDSQL 结构
    unclosed_list: bool = False                # 未闭合列表


def _parse_table_tail(toks, start: int) -> _TailFacts:
    """解析表尾（表定义列表右括号之后）的分布/分区子句。

    表尾逐个识别完整 token（§4.2 步骤 4）：
      · SHARDKEY = <identifier>
      · TDSQL_DISTRIBUTED BY <HASH/RANGE/LIST> (...)
      · PARTITION BY <RANGE/LIST> [COLUMNS] (...) (...)
      · TDSQL_PARTITION BY <RANGE/LIST> [COLUMNS] (...) (...)

    配对括号跳过表达式与定义列表，允许合法表选项穿插，不要求分布子句固定先后位置。
    COLUMNS 只在目标实际返回的对应头中识别。

    Args:
        toks: token 列表
        start: 表定义列表右括号之后的位置

    Returns:
        _TailFacts 事实集合
    """
    facts = _TailFacts()
    n = len(toks)
    i = start

    while i < n:
        tok = toks[i]
        tt = tok.token_type

        # 跳过注释
        if tt == TokenType.COMMENT:
            i += 1
            continue

        # 跳过字符串字面量（表选项值，如 COMMENT='...'）
        if tt == TokenType.STRING:
            i += 1
            continue

        # 只处理裸关键字
        if not _is_bare_kw(tok):
            i += 1
            continue

        text = _tok_text_upper(tok)

        # ── SHARDKEY = <identifier> ──────────────────────────────
        if text == "SHARDKEY":
            # 向后找 = 和值
            j = i + 1
            while j < n and toks[j].token_type == TokenType.COMMENT:
                j += 1
            if j < n and toks[j].token_type == TokenType.EQ:
                j += 1
                while j < n and toks[j].token_type == TokenType.COMMENT:
                    j += 1
                if j < n:
                    val_tok = toks[j]
                    # 值可以是标识符（VAR/IDENTIFIER）或关键字
                    if val_tok.token_type in (TokenType.VAR, TokenType.IDENTIFIER) \
                            or _is_bare_kw(val_tok):
                        val = _tok_text_upper(val_tok)
                        # 反引号标识符：剥外层反引号
                        raw = (val_tok.text or "").strip("`'\"")
                        if val_tok.token_type == TokenType.IDENTIFIER:
                            raw = raw.replace("``", "`")
                        facts.shardkey_value = raw
                        facts.distribution_count += 1
                        # 优先识别精确 shardkey=noshardkey_allset 为广播（§4.2 步骤 5）
                        if val == _BROADCAST_MARKER:
                            facts.shardkey_is_broadcast = True
                        i = j + 1
                        continue
            i += 1
            continue

        # ── TDSQL_DISTRIBUTED BY <HASH/RANGE/LIST> (...) ─────────
        if text == "TDSQL_DISTRIBUTED":
            j = i + 1
            while j < n and toks[j].token_type == TokenType.COMMENT:
                j += 1
            if j < n and _is_bare_kw(toks[j], "BY"):
                j += 1
                while j < n and toks[j].token_type == TokenType.COMMENT:
                    j += 1
                if j < n and _is_bare_kw(toks[j]):
                    method = _tok_text_upper(toks[j])
                    if method in ("HASH", "RANGE", "LIST"):
                        facts.tdsql_distributed = method
                        facts.distribution_count += 1
                        # 跳过方法名后的括号列表（配对括号）
                        j += 1
                        while j < n and toks[j].token_type == TokenType.COMMENT:
                            j += 1
                        if j < n and toks[j].token_type == TokenType.L_PAREN:
                            close = _find_def_list_close_paren(toks, j + 1)
                            if close < 0:
                                facts.unclosed_list = True
                            else:
                                j = close + 1
                        i = j
                        continue
                    else:
                        # 未知分布方法
                        facts.unknown_structure = True
            i += 1
            continue

        # ── TDSQL_PARTITION BY <RANGE/LIST> [COLUMNS] (...) ──────
        if text == "TDSQL_PARTITION":
            j = i + 1
            while j < n and toks[j].token_type == TokenType.COMMENT:
                j += 1
            if j < n and _is_bare_kw(toks[j], "BY"):
                j += 1
                while j < n and toks[j].token_type == TokenType.COMMENT:
                    j += 1
                if j < n and _is_bare_kw(toks[j]):
                    method = _tok_text_upper(toks[j])
                    if method in ("RANGE", "LIST"):
                        facts.tdsql_partition = method
                        facts.partition_count += 1
                        j += 1
                        # 可选 COLUMNS
                        while j < n and toks[j].token_type == TokenType.COMMENT:
                            j += 1
                        if j < n and _is_bare_kw(toks[j], "COLUMNS"):
                            facts.tdsql_partition_columns = True
                            j += 1
                        # 跳过括号列表（可能有多个分区定义括号）
                        while j < n:
                            while j < n and toks[j].token_type == TokenType.COMMENT:
                                j += 1
                            if j < n and toks[j].token_type == TokenType.L_PAREN:
                                close = _find_def_list_close_paren(toks, j + 1)
                                if close < 0:
                                    facts.unclosed_list = True
                                    break
                                j = close + 1
                            else:
                                break
                        i = j
                        continue
                    else:
                        facts.unknown_structure = True
            i += 1
            continue

        # ── PARTITION BY <RANGE/LIST> [COLUMNS] (...) (...) ──────
        # sqlglot 30.14.0 把 `PARTITION BY` 合成为一个 PARTITION_BY token
        # （text='PARTITION BY'），也可能在某些路径下分为 PARTITION + BY 两个 token。
        # 两种形态都要处理。
        is_partition_by_compound = (tt == TokenType.PARTITION_BY)
        if text == "PARTITION" or is_partition_by_compound:
            if is_partition_by_compound:
                # 复合 token：BY 已包含在内，直接从下一个 token 找方法
                j = i + 1
            else:
                # 分离 token：向后找 BY
                j = i + 1
                while j < n and toks[j].token_type == TokenType.COMMENT:
                    j += 1
                if j >= n or not _is_bare_kw(toks[j], "BY"):
                    i += 1
                    continue
                j += 1
            while j < n and toks[j].token_type == TokenType.COMMENT:
                j += 1
            if j < n and _is_bare_kw(toks[j]):
                method = _tok_text_upper(toks[j])
                if method in ("RANGE", "LIST"):
                    facts.partition_by = method
                    facts.partition_count += 1
                    j += 1
                    # 可选 COLUMNS
                    while j < n and toks[j].token_type == TokenType.COMMENT:
                        j += 1
                    if j < n and _is_bare_kw(toks[j], "COLUMNS"):
                        facts.partition_columns = True
                        j += 1
                    # 跳过括号列表
                    while j < n:
                        while j < n and toks[j].token_type == TokenType.COMMENT:
                            j += 1
                        if j < n and toks[j].token_type == TokenType.L_PAREN:
                            close = _find_def_list_close_paren(toks, j + 1)
                            if close < 0:
                                facts.unclosed_list = True
                                break
                            j = close + 1
                        else:
                            break
                    i = j
                    continue
                elif method in ("HASH", "KEY"):
                    # 原生 MySQL PARTITION BY HASH/KEY —— 不是 TDSQL 二级分区
                    # 但也不贡献 distribution/partition 关键词（§4.2 步骤 2）
                    # 记为原生分区，NOT_SECONDARY
                    facts.partition_by = "NATIVE_" + method
                    facts.partition_count += 1
                    j += 1
                    while j < n:
                        while j < n and toks[j].token_type == TokenType.COMMENT:
                            j += 1
                        if j < n and toks[j].token_type == TokenType.L_PAREN:
                            close = _find_def_list_close_paren(toks, j + 1)
                            if close < 0:
                                facts.unclosed_list = True
                                break
                            j = close + 1
                        else:
                            break
                    i = j
                    continue
                else:
                    facts.unknown_structure = True
            i += 1
            continue

        # 其他 token：合法表选项（ENGINE=/CHARSET=/COMMENT= 等）穿插，跳过
        i += 1

    return facts


# ══════════════════════════════════════════════════════════════════
# 组合判定（§4.2 步骤 6）
# ══════════════════════════════════════════════════════════════════

def _combine_facts(facts: _TailFacts) -> ShapeEvidence:
    """按 §4.2 步骤 6 的组合表判定 ShapeEvidence。

    | 一级事实 | 二级事实 | 结论 |
    |---|---|---|
    | SHARDKEY（非广播） | PARTITION BY RANGE/LIST | SECONDARY，旧 HASH 二级 |
    | TDSQL_DISTRIBUTED BY RANGE/LIST | PARTITION BY RANGE/LIST | SECONDARY，旧 RANGE/LIST 二级 |
    | TDSQL_DISTRIBUTED BY HASH | TDSQL_PARTITION BY RANGE/LIST | SECONDARY，新二级 |
    | 广播 | 无或分区结构 | 不计主表；若与 Proxy 分片候选冲突记元数据冲突 |
    | 仅一级分布 | 无分区 | NOT_SECONDARY |
    | 无分布 | 原生 PARTITION 或 SUBPARTITION | NOT_SECONDARY |
    | 其他交叉代际组合/不完整结构 | 任意 | UNKNOWN |
    """
    # 冲突/不完整检测（§4.2 步骤 5）
    if facts.unclosed_list:
        return ShapeEvidence(STATE_UNKNOWN, DIST_UNKNOWN, PART_UNKNOWN,
                             FAMILY_UNKNOWN, "UNCLOSED_LIST")
    if facts.distribution_count > 1:
        return ShapeEvidence(STATE_UNKNOWN, DIST_UNKNOWN, PART_UNKNOWN,
                             FAMILY_UNKNOWN, "MULTIPLE_DISTRIBUTION")
    if facts.partition_count > 1:
        return ShapeEvidence(STATE_UNKNOWN, DIST_UNKNOWN, PART_UNKNOWN,
                             FAMILY_UNKNOWN, "MULTIPLE_PARTITION_HEAD")
    if facts.unknown_structure:
        return ShapeEvidence(STATE_UNKNOWN, DIST_UNKNOWN, PART_UNKNOWN,
                             FAMILY_UNKNOWN, "UNKNOWN_TDSQL_STRUCTURE")

    # 广播优先（§4.2 步骤 5）
    if facts.shardkey_is_broadcast:
        # 广播：不计主表。distribution=BROADCAST，state=NOT_SECONDARY
        # （若与 Proxy 分片候选冲突，由调用方记元数据冲突，不在本识别器内判定）
        part = PART_NONE
        if facts.partition_by in ("RANGE", "LIST"):
            part = PART_RANGE if facts.partition_by == "RANGE" else PART_LIST
        elif facts.tdsql_partition in ("RANGE", "LIST"):
            part = PART_RANGE if facts.tdsql_partition == "RANGE" else PART_LIST
        return ShapeEvidence(STATE_NOT_SECONDARY, DIST_BROADCAST, part,
                             FAMILY_NONE, "BROADCAST")

    # 确定一级分布事实
    dist = DIST_NONE
    family = FAMILY_NONE
    if facts.shardkey_value:
        # SHARDKEY = <identifier>（非广播）→ 旧 HASH 分布
        dist = DIST_SHARDKEY_HASH
        family = FAMILY_LEGACY
    elif facts.tdsql_distributed:
        method = facts.tdsql_distributed
        if method == "HASH":
            dist = DIST_TDSQL_HASH
        elif method == "RANGE":
            dist = DIST_TDSQL_RANGE
        elif method == "LIST":
            dist = DIST_TDSQL_LIST
        family = FAMILY_MODERN

    # 确定二级分区事实
    part = PART_NONE
    part_family = FAMILY_NONE
    if facts.partition_by in ("RANGE", "LIST"):
        part = PART_RANGE if facts.partition_by == "RANGE" else PART_LIST
        part_family = FAMILY_LEGACY
    elif facts.tdsql_partition in ("RANGE", "LIST"):
        part = PART_RANGE if facts.tdsql_partition == "RANGE" else PART_LIST
        part_family = FAMILY_MODERN
    elif facts.partition_by and facts.partition_by.startswith("NATIVE_"):
        # 原生 MySQL PARTITION BY HASH/KEY —— 无分布时 NOT_SECONDARY
        part = PART_NONE
        part_family = FAMILY_NONE

    # 组合判定
    # 1. SHARDKEY（非广播）+ PARTITION BY RANGE/LIST → SECONDARY，旧 HASH 二级
    if dist == DIST_SHARDKEY_HASH and part in (PART_RANGE, PART_LIST):
        return ShapeEvidence(STATE_SECONDARY, dist, part, FAMILY_LEGACY,
                             "LEGACY_SHARDKEY_PARTITION")

    # 2. TDSQL_DISTRIBUTED BY RANGE/LIST + PARTITION BY RANGE/LIST → SECONDARY，旧 RANGE/LIST 二级
    if dist in (DIST_TDSQL_RANGE, DIST_TDSQL_LIST) and part in (PART_RANGE, PART_LIST):
        # 一级 TDSQL_DISTRIBUTED + 二级 PARTITION BY（旧式二级）
        fam = FAMILY_MODERN if part_family == FAMILY_MODERN else FAMILY_LEGACY
        return ShapeEvidence(STATE_SECONDARY, dist, part, fam,
                             "MODERN_DIST_LEGACY_PART")

    # 3. TDSQL_DISTRIBUTED BY HASH + TDSQL_PARTITION BY RANGE/LIST → SECONDARY，新二级
    if dist == DIST_TDSQL_HASH and facts.tdsql_partition in ("RANGE", "LIST"):
        return ShapeEvidence(STATE_SECONDARY, dist, part, FAMILY_MODERN,
                             "MODERN_DIST_MODERN_PART")

    # 4. 仅一级分布，无分区 → NOT_SECONDARY
    if dist != DIST_NONE and part == PART_NONE:
        return ShapeEvidence(STATE_NOT_SECONDARY, dist, PART_NONE, family,
                             "DISTRIBUTION_ONLY")

    # 5. 无分布，原生 PARTITION 或 SUBPARTITION → NOT_SECONDARY
    if dist == DIST_NONE and (facts.partition_by or facts.tdsql_partition):
        return ShapeEvidence(STATE_NOT_SECONDARY, DIST_NONE, part, FAMILY_NONE,
                             "NATIVE_PARTITION_ONLY")

    # 6. 无分布无分区 → NOT_SECONDARY（普通单表/广播已处理）
    if dist == DIST_NONE and part == PART_NONE:
        return ShapeEvidence(STATE_NOT_SECONDARY, DIST_NONE, PART_NONE,
                             FAMILY_NONE, "NO_DISTRIBUTION_NO_PARTITION")

    # 7. 其他交叉代际组合/不完整结构 → UNKNOWN
    return ShapeEvidence(STATE_UNKNOWN, dist, part,
                         family if family != FAMILY_NONE else FAMILY_UNKNOWN,
                         "CROSS_GENERATION_COMBO")


# ══════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════

def classify_logical_ddl(
    ddl: str,
    expected_database: str = "",
    expected_table: str = "",
    dialect: str = "mysql",
) -> ShapeEvidence:
    """识别 SHOW CREATE TABLE 返回的 DDL 是否为二级分区主表（§4.2）。

    该识别器消费**目标实例成功返回的 SHOW CREATE TABLE**，做结构识别，
    不代替核心审核器证明 SQL 可执行。

    Args:
        ddl: SHOW CREATE TABLE 返回的完整 DDL 文本
        expected_database: 期望的数据库名（用于验证限定表名一致）
        expected_table: 期望的表名
        dialect: sqlglot 方言（默认 mysql）

    Returns:
        ShapeEvidence。词法失败、返回截断、目标不一致返回 UNKNOWN。

    步骤（§4.2）：
      1. token 化并验证 CREATE TABLE 头、限定表名与请求一致；反引号中的
         转义反引号正确解码。找到表定义列表配对右括号，之后才进入表尾解析。
      2. 表定义内的列名、COMMENT 字符串、普通注释均不得贡献 distribution/
         partition 关键词。词法失败、返回截断、目标不一致返回 UNKNOWN。
      3. MySQL 可执行版本注释不能作为普通注释丢弃。仅对完整 /*!版本号 … */
         片段解析内部 token，并保留位置/边界。
      4. 表尾逐个识别完整 token。
      5. 优先识别精确 shardkey=noshardkey_allset 为广播。
      6. 按组合表判定。
    """
    if not ddl or not str(ddl).strip():
        return ShapeEvidence(STATE_UNKNOWN, DIST_UNKNOWN, PART_UNKNOWN,
                             FAMILY_UNKNOWN, "EMPTY_DDL")

    ddl = str(ddl)

    # 步骤 1：token 化
    toks = _tokenize(ddl, dialect)
    if not toks:
        return ShapeEvidence(STATE_UNKNOWN, DIST_UNKNOWN, PART_UNKNOWN,
                             FAMILY_UNKNOWN, "LEX_FAILED")

    # 步骤 1：验证 CREATE TABLE 头
    after_table = _find_create_table_head(toks)
    if after_table is None:
        return ShapeEvidence(STATE_UNKNOWN, DIST_UNKNOWN, PART_UNKNOWN,
                             FAMILY_UNKNOWN, "NOT_CREATE_TABLE")

    # 步骤 1：验证限定表名与请求一致
    after_name, name_ok = _match_qualified_name(
        toks, after_table, expected_database, expected_table)
    if not name_ok:
        return ShapeEvidence(STATE_UNKNOWN, DIST_UNKNOWN, PART_UNKNOWN,
                             FAMILY_UNKNOWN, "TARGET_MISMATCH")

    # 步骤 1：找到表定义列表的左括号
    n = len(toks)
    i = after_name
    while i < n and toks[i].token_type == TokenType.COMMENT:
        i += 1
    if i >= n or toks[i].token_type != TokenType.L_PAREN:
        # 没有表定义列表（可能是 CREATE TABLE ... SELECT 或截断）
        return ShapeEvidence(STATE_UNKNOWN, DIST_UNKNOWN, PART_UNKNOWN,
                             FAMILY_UNKNOWN, "NO_DEF_LIST")

    # 步骤 1：找到表定义列表的配对右括号
    close_paren = _find_def_list_close_paren(toks, i + 1)
    if close_paren < 0:
        return ShapeEvidence(STATE_UNKNOWN, DIST_UNKNOWN, PART_UNKNOWN,
                             FAMILY_UNKNOWN, "UNCLOSED_DEF_LIST")

    # 步骤 3：处理可执行版本注释。
    # sqlglot tokenizer 把 /*!...*/ 当作普通注释跳过，因此表尾解析看不到
    # 版本注释内部的 token。需要在原始 DDL 上提取版本注释片段，若片段内
    # 含分布/分区关键词，则单独 token 化并合并到表尾事实。
    # 无法确定版本条件或不完整片段则 UNKNOWN，不能认定没有分区。
    versioned_segments = _extract_versioned_comment_segments(ddl)
    # 只处理表定义列表右括号**之后**的版本注释（表尾部分）
    # 通过 token 位置映射：找到 close_paren token 的原文 end 偏移
    tail_start_offset = 0
    try:
        tail_start_offset = toks[close_paren].end + 1
    except Exception:
        tail_start_offset = 0

    # 步骤 4—5：解析表尾（右括号之后）
    facts = _parse_table_tail(toks, close_paren + 1)

    # 步骤 3：合并表尾版本注释内部的事实
    for (seg_start, seg_end, ver_str, inner_sql) in versioned_segments:
        if seg_start < tail_start_offset:
            continue  # 表定义列表内部的版本注释不贡献表尾事实
        if not inner_sql.strip():
            continue
        # 对内部 SQL 单独 token 化
        inner_toks = _tokenize(inner_sql, dialect)
        if not inner_toks:
            # 不完整片段 → UNKNOWN
            facts.unknown_structure = True
            continue
        inner_facts = _parse_table_tail(inner_toks, 0)
        # 合并事实（冲突检测）
        if inner_facts.shardkey_value and facts.shardkey_value:
            facts.distribution_count += 1  # 多处分布声明冲突
        elif inner_facts.shardkey_value:
            facts.shardkey_value = inner_facts.shardkey_value
            facts.shardkey_is_broadcast = inner_facts.shardkey_is_broadcast
            facts.distribution_count += 1
        if inner_facts.tdsql_distributed and facts.tdsql_distributed:
            facts.distribution_count += 1
        elif inner_facts.tdsql_distributed:
            facts.tdsql_distributed = inner_facts.tdsql_distributed
            facts.distribution_count += 1
        if inner_facts.partition_by and facts.partition_by:
            facts.partition_count += 1
        elif inner_facts.partition_by:
            facts.partition_by = inner_facts.partition_by
            facts.partition_columns = inner_facts.partition_columns
            facts.partition_count += 1
        if inner_facts.tdsql_partition and facts.tdsql_partition:
            facts.partition_count += 1
        elif inner_facts.tdsql_partition:
            facts.tdsql_partition = inner_facts.tdsql_partition
            facts.tdsql_partition_columns = inner_facts.tdsql_partition_columns
            facts.partition_count += 1
        if inner_facts.unknown_structure:
            facts.unknown_structure = True
        if inner_facts.unclosed_list:
            facts.unclosed_list = True

    # 步骤 6：组合判定
    return _combine_facts(facts)
