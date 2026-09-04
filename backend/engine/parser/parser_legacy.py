"""
TDSQL SQL审核工具 - SQL解析器 (V1.0)

基于 sqlglot 实现 SQL 解析，提取语法树中的关键信息。
V1.0 扩展：新增30+字段，支持CREATE/ALTER/INSERT/LOAD/HANDLER等深度解析。
"""
import re
from dataclasses import dataclass, field
from typing import Optional

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.tokens import TokenType


# ── v1.6.2.2：解析恢复链的 token 级安全剥离器 ─────────────────────────────────
#
# 本文件原有的 _TDSQL_DIALECT_RE（v1.6.2.0 引入的全局正则）已删除。
# 删除原因（实测，见设计说明书 §5.14）：它对整条 SQL 做 re.sub()，不感知
# token 作用域，会把定义体里的真实内容一并抹掉——
#   `broadcast` varchar(20)                 → 列被删除（列名变成空白）
#   COMMENT 'broadcast table info'          → 注释被改成 '  table info'
#   COMMENT 'TDSQL_DISTRIBUTED BY HASH(x)'  → 注释被清空
# 且改写后的 SQL 仍能解析成同表名的 exp.Create，门禁发现不了，
# 形成**静默错误 AST**。该缺陷自 v1.6.2.0 起已在生产版本中存在。
#
# ── 本模块的设计原则：白名单，不是黑名单 ──
# 前几版反复出问题的根源是"扫描 + 排除已知的坏形态"：每补一种排除，
# 就还剩下没想到的另一种。本版一律改成**只接受精确形态、其余全部拒绝**：
#   * 建表头部：CREATE [TEMPORARY] TABLE [IF NOT EXISTS] 名[.名] (  —— 且表名
#     只接受裸标识符 VAR 与反引号标识符 IDENTIFIER；STRING（单/双引号）一律拒绝；
#   * 方言尾子句：TDSQL_DISTRIBUTED BY HASH|RANGE|LIST ( 单个标识符 )
#     —— 括号内必须**恰好一个**标识符 token，空参数、字符串、逗号、多字段、
#     运算符、函数、嵌套括号一律拒绝；
#   * 广播标志：独立的裸 BROADCAST 关键字；
#   * 其余一切形态 → 返回 None，**保持原有失败路径**（宁可继续报 E999，
#     也绝不把非法 DDL 修成"解析成功"）。
# 两个剥离器共用同一个严格头部定位器 _tdsql_table_def_bounds()，
# 避免两套安全模型再次各自漂移。



def normalize_newlines(text: str) -> str:
    """通用换行规范化：CRLF 与单独 CR 统一为 LF（v1.6.2.2-UAT-O-14）。

    拆句、解析、语句头词法判定三个组件必须消费同一份规范化文本：
    仅 CR 的换行会让 `--` 行注释的正则终止符（`\n`）失效，把注释后的真实
    语句整体吞掉（文件入口拆出 0 条）；而 sqlglot 词法器把 CR 当换行，
    两侧不一致正是"残缺 VIEW 绿色通过"的成因。
    """
    if not text:
        return text
    return text.replace("\r\n", "\n").replace("\r", "\n")


# v1.6.2.2-A-VERIFY-6.2：MySQL 8.0 索引列排序修饰（`PRIMARY KEY (col DESC)` /
# `KEY idx (a DESC, b)`）在 sqlglot 29/30 各版本均解析失败，整表退化到降级路径。
# 审核语义不依赖索引列排序方向，故在纯文本预处理层剥离该修饰，使结构类规则
# 恢复完整覆盖；仅对 DDL 生效、跳过 CTAS，且只在字符串/注释之外剥离（爆炸半径可控）。
_INDEX_ORDER_STRIP_RE = re.compile(r"\s+(ASC|DESC)(?=\s*[,)])", re.IGNORECASE)
_DDL_ORDER_GATE_RE = re.compile(
    r"^(create\s+(or\s+replace\s+)?(table|(unique\s+)?index)|alter\s+table)\b",
    re.IGNORECASE)
# v1.6.2.2-A-RETEST DEF-A-6.2-b：单/双引号分支必须认识反斜杠转义与双写转义，
# 反引号支持双写——否则含奇数个 \' 的语句在字符串边界判定上整体错位，
# 真正的索引 DESC 被误判为“在字符串内”而不剥离，修复完全失效。
_LITERAL_OR_COMMENT_RE = re.compile(
    r"('(?:\\.|''|[^'\\])*'"
    r"|\"(?:\\.|\"\"|[^\"\\])*\""
    r"|`(?:``|[^`])*`"
    r"|--[^\n]*|#[^\n]*|/\*.*?\*/)", re.DOTALL)


def _strip_leading_comments(s: str) -> str:
    """去除语句前导空白与注释，返回首个有效内容（用于 DDL 门控判定）。"""
    while True:
        s2 = s.lstrip()
        if s2.startswith("--") or s2.startswith("#"):
            nl = s2.find("\n")
            if nl < 0:
                return ""
            s = s2[nl + 1:]
        elif s2.startswith("/*"):
            end = s2.find("*/")
            if end < 0:
                return ""
            s = s2[end + 2:]
        else:
            return s2


def _strip_index_order_modifiers(sql: str) -> str:
    """剥离 DDL 索引定义中的 ASC/DESC 排序修饰（v1.6.2.2-A-VERIFY-6.2）。

    仅对 CREATE TABLE / CREATE [UNIQUE] INDEX / ALTER TABLE 生效；
    CTAS（CREATE TABLE ... AS SELECT）可能携带 ORDER BY，保持原文不动；
    剥离只发生在字符串字面量与注释之外的普通文本段。
    """
    body = _strip_leading_comments(sql)
    if not _DDL_ORDER_GATE_RE.match(body):
        return sql
    # v1.6.2.2-A-RETEST DEF-A-6.2-c：CTAS 判定只看普通代码段（字面量/注释/反引号
    # 标识符之外）——上一轮“全文找 select”会把注释/标识符含 select 的普通建表
    # 误判为 CTAS 而使剥离失效（8 例探测 5 例失效）。分段后两侧都正确：
    # CTAS 的 select 在代码段内仍被认出；注释/字面量/反引号里的 select 不影响门控。
    parts = _LITERAL_OR_COMMENT_RE.split(sql)
    if any(re.search(r"\bselect\b", seg, re.IGNORECASE) for seg in parts[0::2]):
        return sql
    for i in range(0, len(parts), 2):
        parts[i] = _INDEX_ORDER_STRIP_RE.sub("", parts[i])
    return "".join(parts)


def _strip_comments_for_fallback(sql: str) -> str:
    """降级路径表名提取前的注释剥离（v1.6.2.2-A-VERIFY-6.1）。

    文件审核保留 `-- SQL Object:` 等注释头，解析失败降级时若不剥离，
    表名正则会从注释内部提取出 `--` 残片当表名，导致 R001 误报。
    """
    text = re.sub(r"--[^\n]*", "", sql)
    text = re.sub(r"#[^\n]*", "", text)
    return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)


def _lex_head_words(sql: str, dialect: str = "mysql", limit: int = 8) -> Optional[list]:
    """用 sqlglot 词法器取语句头词序列（大写文本）。词法化失败返回 None。

    用途：为"顶层语句头"判定提供可信输入（v1.6.2.2-UAT-O-09）。
    sqlglot 的 MySQL 词法器完整处理 `#`/`--`/`/* */` 三种注释与引号/反引号
    字符串，注释与字面量内容不会进入词序列——从根本上避免手工状态机的
    覆盖盲区（上一轮自研剥离器没有 `#` 注释状态，`# operator's note` 中的
    单引号被误当字符串起点，吞掉后面的真实 LOAD 关键字导致 R042 漏报）。
    """
    try:
        toks = sqlglot.Dialect.get_or_raise(dialect).tokenizer_class().tokenize(sql)
    except Exception:
        return None
    return [t.text.upper() for t in toks][:limit]


def _is_load_statement_head(words) -> bool:
    """词序列是否为 LOAD DATA / LOAD XML 顶层语句头"""
    return bool(words) and len(words) >= 2 and words[0] == "LOAD" and words[1] in ("DATA", "XML")


def _is_create_routine_head(words) -> bool:
    """词序列是否为 CREATE [OR REPLACE] [DEFINER=...] VIEW/PROCEDURE/FUNCTION/TRIGGER 语句头"""
    if not words or words[0] != "CREATE":
        return False
    i = 1
    if i + 1 < len(words) and words[i] == "OR" and words[i + 1] == "REPLACE":
        i += 2
    if i < len(words) and words[i] == "DEFINER":
        # 跳过 DEFINER = user [@ host]（user 可为一个词或字符串字面量）
        i += 1
        if i < len(words) and words[i] == "=":
            i += 1
        if i < len(words):
            i += 1
            if i + 1 < len(words) and words[i] == "@":
                i += 2
    return i < len(words) and words[i] in ("VIEW", "PROCEDURE", "FUNCTION", "TRIGGER")


def _spans_only_diff(orig: str, new: str, spans) -> bool:
    """校验 new 相对 orig 的全部差异都落在 spans 内，且长度恒等。"""
    if new is None or len(new) != len(orig):
        return False
    for i in range(len(orig)):
        if orig[i] != new[i] and not any(s <= i <= e for s, e in spans):
            return False
    return True


# 不得当作关键字的 token 类型：字符串字面量与（反）引号标识符。
# 用"排除法"而非"只认 VAR"是实测决定的：sqlglot 30.14 里
#   TDSQL_DISTRIBUTED / BY / HASH / BROADCAST -> TokenType.VAR
#   RANGE -> TokenType.RANGE ，LIST -> TokenType.LIST（各有专用 token 类型）
# 只认 VAR 会让合法的 BY RANGE(...) / BY LIST(...) 无法恢复（已实测）。
_NON_KEYWORD_TOKENS = (TokenType.STRING, TokenType.IDENTIFIER)

# 合法标识符 token：裸名(VAR) 与反引号名(IDENTIFIER)。
# **不含 STRING**——MySQL 下 't' / "t" 会被词法器标成 STRING，
# 若把它当合法表名/分片键，就会把非法 DDL 恢复成功（第五轮 BLOCK-E2）。
_IDENT_TOKENS = (TokenType.VAR, TokenType.IDENTIFIER)


def _is_bare_kw(tok, word=None) -> bool:
    """是否为裸关键字 token（排除字符串字面量与反引号标识符）。

    `word=None` 表示"只要求是裸词、不限定具体文本"——供枚举型选项值使用。
    """
    if tok.token_type in _NON_KEYWORD_TOKENS:
        return False
    return True if word is None else (tok.text or "").upper() == word


def _ident_text(tok):
    """标识符 token 的归一文本：去反引号、去首尾空白、转小写。"""
    return (tok.text or "").strip("` ").strip().lower()


def _tdsql_table_def_bounds(toks):
    """严格定位第一条建表语句的列定义列表，并产出顶层 CreateShape。

    返回 (左括号下标, 右括号下标, 表名, head)；任一环节不满足返回 (-1, -1, "", None)。

    `head = (qname, temporary, if_not_exists)`，其中 `qname = (schema, table)`
    —— 第十二轮 BLOCK-12-04：Rev.M 只保留最后一级表名，于是候选把 `db1.t` 换成
    `db2.t`、把 `CREATE TEMPORARY` 降成 `CREATE`、把 `IF NOT EXISTS` 删掉，
    门禁一律返回 True。这三项都有规则消费者（临时表标志直接进 R032），
    必须进入指纹。

    只接受：CREATE [TEMPORARY] TABLE [IF NOT EXISTS] <名>[.<名>] ( ... )
      * 表名只接受 VAR / IDENTIFIER，**STRING 一律拒绝**；
      * 表名之后必须**紧接**列定义左括号 —— CTAS(`AS SELECT`)、`LIKE`
        因此被拒，不会拿后续任意括号（如 CONCAT(...)）冒充定义列表。
    """
    n = len(toks)
    if n < 4 or toks[0].token_type != TokenType.CREATE:
        return -1, -1, "", None
    p = 1
    temporary = False
    if toks[p].token_type == TokenType.TEMPORARY:
        temporary = True
        p += 1
    if p >= n or toks[p].token_type != TokenType.TABLE:
        return -1, -1, "", None
    p += 1
    if_not_exists = False
    if (p + 2 < n and _is_bare_kw(toks[p], "IF")
            and toks[p + 1].token_type == TokenType.NOT
            and toks[p + 2].token_type == TokenType.EXISTS):
        if_not_exists = True
        p += 3
    if p >= n or toks[p].token_type not in _IDENT_TOKENS:
        return -1, -1, "", None
    table_name = toks[p].text
    schema = ""
    p += 1
    if (p + 1 < n and toks[p].token_type == TokenType.DOT
            and toks[p + 1].token_type in _IDENT_TOKENS):
        schema = _ident_text(toks[p - 1])
        table_name = toks[p + 1].text
        p += 2
    if p >= n or toks[p].token_type != TokenType.L_PAREN:
        return -1, -1, "", None
    head = ((schema, (table_name or "").strip("` ").strip().lower()),
            temporary, if_not_exists)
    open_idx = p
    d = 0
    while p < n:
        if toks[p].token_type == TokenType.L_PAREN:
            d += 1
        elif toks[p].token_type == TokenType.R_PAREN:
            d -= 1
            if d == 0:
                return open_idx, p, table_name, head
        p += 1
    return -1, -1, "", None




# ── TDSQL 官方语法消费器（Rev.M：结构化类型表 + typed atoms + 指纹守恒）──
#
# 判据优先级：① 目标实例事实 ② TDSQL 官方文档 ③ 用户冻结决策
#             ④ 官方声明继承 MySQL 处用 MySQL 手册补边界 ⑤ sqlglot 只做词法与候选
#
# 引擎名 / 字符集 / 排序规则：裸名、反引号名、引号名都合法，但**不能是数字**
_OPT_NAMEY = (TokenType.VAR, TokenType.IDENTIFIER, TokenType.STRING)


# ── 结构化数据类型规范表（第十一轮 BLOCK-11-04）─────────────────────────────
#
# Rev.L 的 `_TYPE_SPEC = 名 -> 模式字符串` 是**双向失真**的：
#   过窄——`INTEGER` / `NUMERIC(M,D)` / `REAL(M,D)` / `ENUM(...)` / `INT ZEROFILL`
#          因指纹按字面比较而被拒（sqlglot 会把它们规范化）；`CHAR(0)` / `VARCHAR(0)`
#          / `MULTIPOINT` / `DOUBLE PRECISION` 直接进不了规划器；
#   过宽——`DECIMAL(1,2)`（scale > precision）、`DECIMAL(66,0)`、`BIT(65)`、
#          `CHAR(256)`、`VARCHAR(65536)`、`YEAR(999)`、裸 `ENUM` 全被放行。
#
# Rev.M 改为结构化规则表，每个类型显式声明：
#   canonical  规范名（**与 sqlglot 的归一结果一致**，两侧共用同一 canonicalizer）
#   arity      NONE / M_OPT / M_REQ / M_D / FSP / ENUM_SET
#   rng        各参数的闭区间（None 表示不限）
#   family     类型族，决定可接的类型属性
#
# 参数边界依据：TDSQL 官方兼容性页声明继承 MySQL 类型语义，故按 MySQL 5.7 手册取值。
_F_INT, _F_DEC, _F_STR, _F_BIN, _F_TIME, _F_OTHER = "int", "dec", "str", "bin", "time", "other"

# ── 产生式记法（第十二轮 BLOCK-12-03）────────────────────────────────────────
#
# Rev.M 的 `名 → 单一 arity` 表达不了"同一个关键字有多条合法产生式"。
# 最典型的是 FLOAT：官方同时存在 `FLOAT(p)`（p∈0..53，单参数、语义是精度位数）
# 与 `FLOAT(M,D)`（M∈1..255、D∈0..30）。Rev.M 把两者塞进同一个 `M_D`，
# 于是**同时**造成合法下界 `FLOAT(0)` 被误拒、非法上界 `FLOAT(54)` 被误收。
#
# 本版每个类型持有**一组**产生式，逐条尝试，命中任意一条即可：
#   _P_NONE            无括号
#   _P(*ranges)        恰好 len(ranges) 个整数参数，逐个落在对应闭区间
#   _P_VALUES(n)       括号内 1..n 个字符串字面量（ENUM/SET）
_P_NONE = ("NONE", ())


def _P(*ranges):
    return ("ARGS", ranges)


def _P_VALUES(max_members):
    return ("VALUES", max_members)


_INT_P = (_P_NONE, _P((1, 255)))
# MySQL/TDSQL 只有 BLOB[(M)] / TEXT[(M)] 的 M 是“选择最小可容纳类型”的长度提示，
# 不是 TEXT 本体 65535 的硬语法上限。允许到 LONGTEXT/LONGBLOB 的最大长度；
# 具体存储类型由数据库决定，审核器只保存源参数。TINY/MEDIUM/LONG 具名变体的
# 官方产生式没有 `(M)`，必须保持 `_P_NONE`，不能复用本组而误收 TINYTEXT(256)。
_LOB_P = (_P_NONE, _P((0, 4294967295)))
_FSP_P = (_P_NONE, _P((0, 6)))
_TYPE_RULES = {
    # 源名                : (canonical,   产生式组,                                    族)
    "TINYINT":             ("TINYINT",    _INT_P,                                     _F_INT),
    "SMALLINT":            ("SMALLINT",   _INT_P,                                     _F_INT),
    "MEDIUMINT":           ("MEDIUMINT",  _INT_P,                                     _F_INT),
    "INT":                 ("INT",        _INT_P,                                     _F_INT),
    "INTEGER":             ("INT",        _INT_P,                                     _F_INT),
    "BIGINT":              ("BIGINT",     _INT_P,                                     _F_INT),
    # SERIAL = BIGINT UNSIGNED NOT NULL AUTO_INCREMENT UNIQUE。Rev.N 只保留类型名，
    # 会让 R054/R038 等消费者看不到隐含约束。Rev.O 仍让规划器具名识别它，
    # 但通过 `_TYPE_KFN_CANONICAL` 标成 KFN-5，最终必须失败关闭。
    "SERIAL":              ("SERIAL",     (_P_NONE,),                                 _F_OTHER),
    "DECIMAL":             ("DECIMAL",    (_P_NONE, _P((1, 65)), _P((1, 65), (0, 30))), _F_DEC),
    "NUMERIC":             ("DECIMAL",    (_P_NONE, _P((1, 65)), _P((1, 65), (0, 30))), _F_DEC),
    "DEC":                 ("DECIMAL",    (_P_NONE, _P((1, 65)), _P((1, 65), (0, 30))), _F_DEC),
    "FIXED":               ("DECIMAL",    (_P_NONE, _P((1, 65)), _P((1, 65), (0, 30))), _F_DEC),
    # FLOAT 有两条产生式，先试 (p) 再试 (M,D)——见上方说明
    "FLOAT":               ("FLOAT",      (_P_NONE, _P((0, 53)), _P((1, 255), (0, 30))), _F_DEC),
    "REAL":                ("FLOAT",      (_P_NONE, _P((1, 255), (0, 30))),           _F_DEC),
    "DOUBLE":              ("DOUBLE",     (_P_NONE, _P((1, 255), (0, 30))),           _F_DEC),
    "DOUBLE PRECISION":    ("DOUBLE",     (_P_NONE, _P((1, 255), (0, 30))),           _F_DEC),
    "CHAR":                ("CHAR",       (_P_NONE, _P((0, 255))),                    _F_STR),
    "NCHAR":               ("CHAR",       (_P_NONE, _P((0, 255))),                    _F_STR),
    "CHARACTER":           ("CHAR",       (_P_NONE, _P((0, 255))),                    _F_STR),
    "VARCHAR":             ("VARCHAR",    (_P((0, 65535)),),                          _F_STR),
    "NVARCHAR":            ("VARCHAR",    (_P((0, 65535)),),                          _F_STR),
    "CHARACTER VARYING":   ("VARCHAR",    (_P((0, 65535)),),                          _F_STR),
    "BINARY":              ("BINARY",     (_P_NONE, _P((0, 255))),                    _F_BIN),
    "VARBINARY":           ("VARBINARY",  (_P((0, 65535)),),                          _F_BIN),
    "TINYTEXT":            ("TINYTEXT",   (_P_NONE,),                                 _F_STR),
    "TEXT":                ("TEXT",       _LOB_P,                                     _F_STR),
    "MEDIUMTEXT":          ("MEDIUMTEXT", (_P_NONE,),                                 _F_STR),
    "LONGTEXT":            ("LONGTEXT",   (_P_NONE,),                                 _F_STR),
    "TINYBLOB":            ("TINYBLOB",   (_P_NONE,),                                 _F_BIN),
    "BLOB":                ("BLOB",       _LOB_P,                                     _F_BIN),
    "MEDIUMBLOB":          ("MEDIUMBLOB", (_P_NONE,),                                 _F_BIN),
    "LONGBLOB":            ("LONGBLOB",   (_P_NONE,),                                 _F_BIN),
    # ENUM 上限 65535 个成员、SET 上限 64 个成员（MySQL 5.7 字符串类型语法）
    "ENUM":                ("ENUM",       (_P_VALUES(65535),),                        _F_STR),
    "SET":                 ("SET",        (_P_VALUES(64),),                           _F_STR),
    "DATE":                ("DATE",       (_P_NONE,),                                 _F_TIME),
    "YEAR":                ("YEAR",       (_P_NONE, _P((4, 4))),                      _F_TIME),
    "TIME":                ("TIME",       _FSP_P,                                     _F_TIME),
    "DATETIME":            ("DATETIME",   _FSP_P,                                     _F_TIME),
    "TIMESTAMP":           ("TIMESTAMP",  _FSP_P,                                     _F_TIME),
    "BIT":                 ("BIT",        (_P_NONE, _P((1, 64))),                     _F_OTHER),
    "BOOL":                ("BOOLEAN",    (_P_NONE,),                                 _F_OTHER),
    "BOOLEAN":             ("BOOLEAN",    (_P_NONE,),                                 _F_OTHER),
    "JSON":                ("JSON",       (_P_NONE,),                                 _F_OTHER),
    "GEOMETRY":            ("GEOMETRY",   (_P_NONE,),                                 _F_OTHER),
    "POINT":               ("POINT",      (_P_NONE,),                                 _F_OTHER),
    "LINESTRING":          ("LINESTRING", (_P_NONE,),                                 _F_OTHER),
    "POLYGON":             ("POLYGON",    (_P_NONE,),                                 _F_OTHER),
    "MULTIPOINT":          ("MULTIPOINT", (_P_NONE,),                                 _F_OTHER),
    "MULTILINESTRING":     ("MULTILINESTRING",   (_P_NONE,),                          _F_OTHER),
    "MULTIPOLYGON":        ("MULTIPOLYGON",      (_P_NONE,),                          _F_OTHER),
    "GEOMETRYCOLLECTION":  ("GEOMETRYCOLLECTION", (_P_NONE,),                         _F_OTHER),
}
# 多 token 类型名。⚠️ sqlglot 对 `DOUBLE PRECISION` 的词法表现随上下文而异，
# 故两种表现都要能进：这里既登记二元组，`_TYPE_RULES` 也含单词 `DOUBLE`。
_TYPE_MULTIWORD = {
    # 最长匹配优先。sqlglot 可能把 `CHARACTER VARYING` / `CHAR VARYING`
    # 合成一个 token，也可能拆成两个 token；两种词法形态必须映射到同一 canonical。
    ("NATIONAL", "CHARACTER", "VARYING"): "VARCHAR",
    ("NATIONAL", "CHAR", "VARYING"): "VARCHAR",
    ("NATIONAL", "CHARACTER VARYING"): "VARCHAR",
    ("NATIONAL", "CHAR VARYING"): "VARCHAR",
    ("NATIONAL CHARACTER VARYING",): "VARCHAR",
    ("NATIONAL CHAR VARYING",): "VARCHAR",
    ("NCHAR", "VARCHAR"): "VARCHAR",
    ("NCHAR VARCHAR",): "VARCHAR",
    ("CHAR", "BYTE"): "BINARY",
    ("CHAR BYTE",): "BINARY",
    ("DOUBLE", "PRECISION"): "DOUBLE PRECISION",
    # `NATIONAL CHAR` / `NATIONAL VARCHAR` 是官方别名，词法上是**两个** token；
    # sqlglot 30.14.0 三版均 ParseError → 已登记 KFN-A（见 §5.21.5 KFN-4）。
    # 这里登记是为了让它落在具名 KFN，而不是藏在普通 plan=False 里。
    ("NATIONAL", "CHAR"): "CHAR",
    ("NATIONAL", "CHARACTER"): "CHAR",
    ("NATIONAL", "VARCHAR"): "VARCHAR",
    ("NATIONAL CHAR",): "CHAR",
    ("NATIONAL CHARACTER",): "CHAR",
    ("NATIONAL VARCHAR",): "VARCHAR",
}
# 这些官方形态当前发布 pin 30.14.0 不能生成可保真的候选 AST；规划器必须具名
# 接受并落入 KFN-5，不能继续藏在普通 plan=False 中。
_TYPE_KFN_MULTIWORD = {
    ("NATIONAL", "CHARACTER", "VARYING"): "KFN-5-NATIONAL-VARYING",
    ("NATIONAL", "CHAR", "VARYING"): "KFN-5-NATIONAL-VARYING",
    ("NATIONAL", "CHARACTER VARYING"): "KFN-5-NATIONAL-VARYING",
    ("NATIONAL", "CHAR VARYING"): "KFN-5-NATIONAL-VARYING",
    ("NATIONAL CHARACTER VARYING",): "KFN-5-NATIONAL-VARYING",
    ("NATIONAL CHAR VARYING",): "KFN-5-NATIONAL-VARYING",
    ("NCHAR", "VARCHAR"): "KFN-5-NCHAR-VARCHAR",
    ("NCHAR VARCHAR",): "KFN-5-NCHAR-VARCHAR",
    ("CHAR", "BYTE"): "KFN-5-CHAR-BYTE",
    ("CHAR BYTE",): "KFN-5-CHAR-BYTE",
    ("NATIONAL", "CHAR"): "KFN-4-NATIONAL",
    ("NATIONAL", "CHARACTER"): "KFN-4-NATIONAL",
    ("NATIONAL", "VARCHAR"): "KFN-4-NATIONAL",
    ("NATIONAL CHAR",): "KFN-4-NATIONAL",
    ("NATIONAL CHARACTER",): "KFN-4-NATIONAL",
    ("NATIONAL VARCHAR",): "KFN-4-NATIONAL",
}
_TYPE_KFN_CANONICAL = {
    "SERIAL": "KFN-5-SERIAL",
    "POINT": "KFN-3-SPATIAL-TYPE",
    "LINESTRING": "KFN-3-SPATIAL-TYPE",
    "POLYGON": "KFN-3-SPATIAL-TYPE",
    "MULTIPOINT": "KFN-3-SPATIAL-TYPE",
    "MULTILINESTRING": "KFN-3-SPATIAL-TYPE",
    "MULTIPOLYGON": "KFN-3-SPATIAL-TYPE",
    "GEOMETRYCOLLECTION": "KFN-3-SPATIAL-TYPE",
}
# 类型属性按**族**开放：数值族才能 UNSIGNED/ZEROFILL，字符族才能
# BINARY/ASCII/UNICODE。ASCII/UNICODE 是官方别名，但当前 pin 不能解析，故具名 KFN。
_TYPE_ATTRS_BY_FAMILY = {
    _F_INT:   ("UNSIGNED", "SIGNED", "ZEROFILL"),
    _F_DEC:   ("UNSIGNED", "SIGNED", "ZEROFILL"),
    _F_STR:   ("BINARY", "ASCII", "UNICODE"),
    _F_BIN:   (),
    _F_TIME:  (),
    _F_OTHER: (),
}
_TYPE_KFN_ATTRS = {
    "SIGNED": "KFN-4-SIGNED",
    "BINARY": "KFN-4-CHAR-BINARY",
    "ASCII": "KFN-5-ASCII",
    "UNICODE": "KFN-5-UNICODE",
}
# sqlglot 回生成时**丢弃** ZEROFILL（实测），故它不参与候选比对；
# 它是显示属性，规则层无消费者。记入源指纹但比对时归一掉。
_TYPE_ATTRS_DROPPED_BY_AST = ("ZEROFILL", "SIGNED")


def _int_val(tok, allow_zero=False):
    """十进制整数字面量的值；不是则返回 None。"""
    if tok.token_type != TokenType.NUMBER:
        return None
    txt = (tok.text or "").strip()
    if not txt.isdigit():
        return None
    v = int(txt)
    return v if (allow_zero or v > 0) else None


def _in_range(v, rng):
    lo, hi = rng
    return (lo is None or v >= lo) and (hi is None or v <= hi)


def _try_type_production(toks, j, stop, prod):
    """按**单条产生式**消费类型参数；返回 (下一个下标, 参数元组) 或 (-1, None)。"""
    kind, spec = prod
    has_paren = j < stop and toks[j].token_type == TokenType.L_PAREN
    if kind == "NONE":
        return (j, ()) if not has_paren else (-1, None)
    if not has_paren:
        return -1, None                                # 该产生式要求括号
    k = j + 1
    if kind == "VALUES":
        vals = []
        while True:
            if k >= stop or toks[k].token_type != TokenType.STRING:
                return -1, None                        # 必须是字符串字面量
            vals.append(_unquote_str(toks[k]))
            k += 1
            if k < stop and toks[k].token_type == TokenType.COMMA:
                k += 1
                continue
            break
        if not vals or len(vals) > spec:
            return -1, None                            # 空值表 / 超出成员数上限
        args = tuple(vals)                             # **保留逐值内容**，不只记数量
    else:                                              # ARGS
        nums = []
        while True:
            v = _int_val(toks[k], allow_zero=True) if k < stop else None
            if v is None:
                return -1, None
            nums.append(v)
            k += 1
            if k < stop and toks[k].token_type == TokenType.COMMA:
                k += 1
                continue
            break
        if len(nums) != len(spec):
            return -1, None                            # 参数个数不匹配本产生式
        for idx, v in enumerate(nums):
            if not _in_range(v, spec[idx]):
                return -1, None                        # 越界（FLOAT(54)/BIT(65)/CHAR(256)…）
        if len(nums) == 2 and nums[1] > nums[0]:
            return -1, None                            # scale 不得大于 precision
        args = tuple(nums)
    if k >= stop or toks[k].token_type != TokenType.R_PAREN:
        return -1, None
    return k + 1, args


def _consume_data_type(toks, i, stop):
    """按结构化规则表消费列数据类型。

    返回 `(下一个下标, (canonical, 参数元组, 属性元组, family, KFN元组))`
    或 `(-1, None)`。family 必须继续传给列约束消费器，禁止非字符类型接收
    CHARACTER SET/COLLATE；KFN 使规划器具名接受、候选门禁强制失败关闭。
    源侧与候选侧**共用本函数**，从而消除 `INTEGER`/`NUMERIC`/`DEC`/`NCHAR` 等别名
    以及 `ZEROFILL` 被 AST 丢弃导致的假不一致（第十一/十二轮 BLOCK-11-04 / 12-03）。
    """
    if i >= stop:
        return -1, None
    src = (toks[i].text or "").upper()
    j = i + 1
    rule = None
    matched_words = None
    # sqlglot 不同版本可能把同一产生式切成 1/2/3 个 token；按“token 数优先、
    # token 内可含空格”的登记表最长匹配，不能让 NATIONAL CHAR 抢走后面的 VARYING。
    for width in (3, 2, 1):
        if i + width <= stop:
            words = tuple((toks[q].text or "").upper() for q in range(i, i + width))
            if words in _TYPE_MULTIWORD:
                rule = _TYPE_RULES[_TYPE_MULTIWORD[words]]
                matched_words = words
                j = i + width
                break
    if rule is None:
        if toks[i].token_type in _NON_KEYWORD_TOKENS:
            return -1, None
        rule = _TYPE_RULES.get(src)
        if rule is None:
            return -1, None
    canonical, productions, family = rule
    # 逐条尝试产生式，命中任意一条即可；全部不命中 → 失败关闭
    nxt, args = -1, None
    for prod in productions:
        nxt, args = _try_type_production(toks, j, stop, prod)
        if nxt >= 0:
            break
    if nxt < 0:
        return -1, None
    j = nxt
    allowed = _TYPE_ATTRS_BY_FAMILY.get(family, ())
    attrs = []
    while j < stop and _is_bare_kw(toks[j]):
        a = (toks[j].text or "").upper()
        if a not in allowed:
            break
        if a in attrs:
            return -1, None
        attrs.append(a)
        j += 1
    # 属性与类型族错配（DATE UNSIGNED / JSON BINARY…）在**规划层**即拒绝
    if j < stop and _is_bare_kw(toks[j]) and (toks[j].text or "").upper() in (
            "UNSIGNED", "SIGNED", "ZEROFILL", "BINARY"):
        return -1, None
    keep = tuple(a for a in attrs if a not in _TYPE_ATTRS_DROPPED_BY_AST)
    kfns = []
    if matched_words in _TYPE_KFN_MULTIWORD:
        kfns.append(_TYPE_KFN_MULTIWORD[matched_words])
    if canonical in _TYPE_KFN_CANONICAL:
        kfns.append(_TYPE_KFN_CANONICAL[canonical])
    kfns.extend(_TYPE_KFN_ATTRS[a] for a in attrs if a in _TYPE_KFN_ATTRS)
    return j, (canonical, args, keep, family, tuple(sorted(set(kfns))))


def _canonical_type_from_sql(text, dialect="mysql"):
    """把候选 AST 回生成的类型文本送进**同一个** `_consume_data_type()`。

    这样别名归一、参数形态、属性丢弃三件事在两侧完全一致，
    不再出现"源写 `NUMERIC(10,2)`、AST 写 `DECIMAL(10, 2)`"这类假不一致。
    """
    try:
        toks = sqlglot.Dialect.get_or_raise(dialect).tokenizer_class().tokenize(text)
    except Exception:
        return None
    j, shape = _consume_data_type(toks, 0, len(toks))
    return shape if (j == len(toks) and shape is not None) else None


# ── 列约束与 DEFAULT（结构化指纹）──────────────────────────────────────────
_DEFAULT_LITERAL_TOKENS = (TokenType.STRING, TokenType.NUMBER, TokenType.NULL,
                           TokenType.TRUE, TokenType.FALSE,
                           TokenType.HEX_STRING, TokenType.BIT_STRING)
_DEFAULT_TIME_FUNCS = ("CURRENT_TIMESTAMP", "NOW", "LOCALTIME", "LOCALTIMESTAMP")
# 腾讯官方建表页列级 COLUMN_FORMAT 只有三值；Rev.L 误加了表级 ROW_FORMAT 的
# `COMPRESSED`（第十一轮 BLOCK-11-06 §9.2）。
_COLUMN_FORMAT_ENUM = ("FIXED", "DYNAMIC", "DEFAULT")
_COL_CONSTRAINT_ONCE = ("NULLABILITY", "DEFAULT", "AUTO_INCREMENT", "COMMENT",
                        "COLLATE", "CHARACTER_SET", "KEYNESS", "ON_UPDATE",
                        "COLUMN_FORMAT", "ENGINE_ATTRIBUTE", "KFN")
# sqlglot 回生成列定义时**不保留**这些约束（实测），故它们记入源指纹但不参与候选比对
_COL_CONSTRAINT_NOT_IN_AST = ("COLUMN_FORMAT", "ENGINE_ATTRIBUTE")


def _canonical_number(text):
    """数值字面量的规范形。

    第十二轮 BLOCK-12-03：腾讯官方把 `.2` 列为支持的数值字面量，
    sqlglot 回生成时写作 `0.2`（实测）。源侧按字面记就永远等不上候选侧，
    于是合法的 `DEFAULT .2` 被门禁误拒。这里统一补零。
    十六进制 `0x1F`、位串 `b'101'`、科学计数法保持原样（两侧一致）。
    """
    t = (text or "").strip()
    if t.startswith("."):
        return "0" + t
    if t.startswith("-.") or t.startswith("+."):
        return t[0] + "0" + t[1:]
    return t


def _consume_default_value(toks, i, stop):
    """消费 DEFAULT / ON UPDATE 的值；返回 (下一个下标, 值指纹) 或 (-1, None)。

    第十一轮 BLOCK-11-04：时间函数精度必须落在 0~6，
    `DEFAULT CURRENT_TIMESTAMP(7)` 不得放行。
    """
    if i >= stop:
        return -1, None
    tt = toks[i].token_type
    # 腾讯官方把 `.2` 列为支持的数值字面量；词法器把它切成 `DOT` + `NUMBER`
    # 两个 token（实测），Rev.M 只认单个 NUMBER，于是合法字面量被误拒
    # （第十二轮 BLOCK-12-03）。这里显式识别"无整数部分的小数"。
    if tt == TokenType.DOT:
        if i + 1 < stop and toks[i + 1].token_type == TokenType.NUMBER:
            return i + 2, ("num", _canonical_number("." + (toks[i + 1].text or "")))
        return -1, None
    if tt in (TokenType.DASH, TokenType.PLUS):
        # 符号**只能**修饰数值字面量
        sign = "-" if tt == TokenType.DASH else ""
        if i + 2 < stop and toks[i + 1].token_type == TokenType.DOT \
                and toks[i + 2].token_type == TokenType.NUMBER:
            return i + 3, ("num", _canonical_number(
                sign + "." + (toks[i + 2].text or "")))
        if i + 1 < stop and toks[i + 1].token_type == TokenType.NUMBER:
            # 正号归一：sqlglot 回生成时丢弃 `+`（实测 `DEFAULT +1` → `DEFAULT 1`），
            # 两侧必须得到同一规范形，否则合法正例会被门禁误拒。
            return i + 2, ("num", sign + _canonical_number(toks[i + 1].text))
        return -1, None
    if tt == TokenType.CURRENT_TIMESTAMP or (
            _is_bare_kw(toks[i]) and (toks[i].text or "").upper() in _DEFAULT_TIME_FUNCS):
        fname = (toks[i].text or "").upper()
        j, fsp = i + 1, None
        if j + 1 < stop and toks[j].token_type == TokenType.L_PAREN:
            if toks[j + 1].token_type == TokenType.R_PAREN:
                j += 2
            else:
                v = _int_val(toks[j + 1], allow_zero=True) if j + 1 < stop else None
                if v is None or not (0 <= v <= 6) or not (
                        j + 2 < stop and toks[j + 2].token_type == TokenType.R_PAREN):
                    return -1, None                    # fsp 越界 → 失败关闭
                fsp, j = v, j + 3
        return j, ("time", fname, fsp)
    if tt in _DEFAULT_LITERAL_TOKENS:
        if tt == TokenType.NULL:
            return i + 1, ("null",)
        if tt == TokenType.NUMBER:
            return i + 1, ("num", _canonical_number(toks[i].text))
        if tt == TokenType.STRING:
            return i + 1, ("lit", tt.name, _unquote_str(toks[i]))
        return i + 1, ("lit", tt.name, (toks[i].text or ""))
    return -1, None                                    # 裸标识符 / 任意表达式 → 失败关闭


def _consume_column_constraints(toks, i, stop, family):
    """消费列约束序列；返回 (下一个下标, 约束元组, 可掩码 span) 或 (-1, None, [])。

    `family` 来自 `_consume_data_type()`，是列级 CHARACTER SET/COLLATE 的授权边界；
    非字符族不能因为 sqlglot 恰好能生成 AST 就被放行（第十三轮 BLOCK-13-03）。

    第十一轮 BLOCK-11-06：官方列属性 `COLUMN_FORMAT` / `ENGINE_ATTRIBUTE`
    在 sqlglot 30.x 上**候选仍 ParseError**（Rev.L 只验了规划层就宣称"已恢复"，
    结论与代码相反）。本版按复审方推荐方案把它们作为**辅助掩码 span**：
    只在已有主目标时随之掩码，`raw_sql` 不变，且实测无规则消费者（结论基于
    v1.6.2.x 历史基线 119 条规则；v1.6.3.2 为 121 条，新增 R120/R121 亦不消费
    该两个列属性，故本恢复安全性结论按历史基线保留，未经重测不擅改为 121）。
    """
    seen, fp, spans = [], [], []
    j = i
    while j < stop:
        tt = toks[j].token_type
        txt = (toks[j].text or "").upper()
        if tt == TokenType.COMMA:
            break
        if tt == TokenType.NOT and j + 1 < stop and toks[j + 1].token_type == TokenType.NULL:
            ident, val, j = "NULLABILITY", "NOTNULL", j + 2
        elif tt == TokenType.NULL:
            ident, val, j = "NULLABILITY", "NULL", j + 1
        elif tt == TokenType.DEFAULT:
            k, val = _consume_default_value(toks, j + 1, stop)
            if k < 0:
                return -1, None, []
            ident, j = "DEFAULT", k
        elif tt == TokenType.AUTO_INCREMENT:
            ident, val, j = "AUTO_INCREMENT", None, j + 1
        elif tt == TokenType.COMMENT:
            if not (j + 1 < stop and toks[j + 1].token_type == TokenType.STRING):
                return -1, None, []
            ident, val, j = "COMMENT", None, j + 2
        elif tt == TokenType.COLLATE:
            if family != _F_STR:
                return -1, None, []                   # INT/DATE/JSON COLLATE → 失败关闭
            if not (j + 1 < stop and toks[j + 1].token_type in _OPT_NAMEY):
                return -1, None, []
            ident, val, j = "COLLATE", (toks[j + 1].text or "").lower(), j + 2
        elif _charset_kw_end(toks, j, stop) >= 0:
            if family != _F_STR:
                return -1, None, []                   # INT/DATE/JSON CHARACTER SET → 失败关闭
            k = _charset_kw_end(toks, j, stop)
            if not (k < stop and toks[k].token_type in _OPT_NAMEY):
                return -1, None, []
            ident, val, j = "CHARACTER_SET", (toks[k].text or "").lower(), k + 1
        elif tt == TokenType.PRIMARY_KEY:
            ident, val, j = "KEYNESS", "PRIMARY", j + 1
        elif tt == TokenType.UNIQUE:
            j += 1
            if j < stop and toks[j].token_type == TokenType.KEY:
                j += 1
            ident, val = "KEYNESS", "UNIQUE"
        elif tt == TokenType.KEY:
            ident, val, j = "KEYNESS", "KEY", j + 1
        elif (_is_bare_kw(toks[j], "SERIAL") and j + 2 < stop
              and toks[j + 1].token_type == TokenType.DEFAULT
              and _is_bare_kw(toks[j + 2], "VALUE")):
            # `SERIAL DEFAULT VALUE` = NOT NULL AUTO_INCREMENT UNIQUE 的约束别名。
            # 本期不做不完整展开；规划器具名接受后由 KFN-5 强制失败关闭。
            ident, val, j = "KFN", "KFN-5-SERIAL-DEFAULT-VALUE", j + 3
        elif tt == TokenType.ON and j + 1 < stop and toks[j + 1].token_type == TokenType.UPDATE:
            k, val = _consume_default_value(toks, j + 2, stop)
            if k < 0 or not (isinstance(val, tuple) and val[0] == "time"):
                return -1, None, []
            ident, j = "ON_UPDATE", k
        elif _is_bare_kw(toks[j]) and txt == "COLUMN_FORMAT":
            if not (j + 1 < stop and _is_bare_kw(toks[j + 1])
                    and (toks[j + 1].text or "").upper() in _COLUMN_FORMAT_ENUM):
                return -1, None, []
            ident, val = "COLUMN_FORMAT", (toks[j + 1].text or "").upper()
            spans.append((toks[j].start, toks[j + 1].end))      # 辅助掩码
            j += 2
        elif _is_bare_kw(toks[j]) and txt == "ENGINE_ATTRIBUTE":
            k = j + 1
            if k < stop and toks[k].token_type == TokenType.EQ:
                k += 1
            if k >= stop or toks[k].token_type != TokenType.STRING:
                return -1, None, []
            ident, val = "ENGINE_ATTRIBUTE", "<str>"
            spans.append((toks[j].start, toks[k].end))          # 辅助掩码
            j = k + 1
        else:
            return -1, None, []                        # 未知列约束（含列级 STORAGE）→ 失败关闭
        if ident in _COL_CONSTRAINT_ONCE and ident in [x[0] for x in fp]:
            return -1, None, []                        # 重复/矛盾约束
        fp.append((ident, val))
    return j, tuple(fp), spans


def _consume_column_definition(toks, i, stop):
    """消费一个完整列定义；返回 (下一个下标, 列指纹, 可掩码 span) 或 (-1, None, [])。

    列指纹为**结构化元组**（第十一轮 BLOCK-11-05：禁止 `|` 拼接后再 split——
    合法反引号列名 `` `a|b` `` 会把字符串指纹拆坏）。
    """
    if i >= stop or toks[i].token_type not in _IDENT_TOKENS:
        return -1, None, []
    col = (toks[i].text or "").strip("` ").lower()
    j, shape = _consume_data_type(toks, i + 1, stop)
    if j < 0:
        return -1, None, []
    family = shape[3]
    j, cons, spans = _consume_column_constraints(toks, j, stop, family)
    if j < 0:
        return -1, None, []
    return j, ("col", col, shape, cons), spans


# ── 索引：按 kind 分支 + 结构化指纹（第十一轮 BLOCK-11-05 / MAJOR-11-01）─────
_TDSQL_INDEX_TYPES = ("BTREE",)
_INDEX_LEAD_WORDS = ("FULLTEXT", "SPATIAL")


def _index_lead(toks, i, stop):
    """识别索引定义项的引导形态；不是索引返回 None。

    第十一轮 MAJOR-11-01：Rev.L 的 `_is_index_item()` 要求 FULLTEXT/SPATIAL
    后必须紧跟 KEY/INDEX，而消费器却支持裸形态——**入口与消费器判据不一致**，
    合法的 `FULLTEXT (col)` 被错误送进列消费器。本函数是**唯一**引导判据，
    入口与消费器共用它。
    """
    if i >= stop:
        return None
    tt = toks[i].token_type
    if tt == TokenType.PRIMARY_KEY:
        return "PRIMARY"
    if tt == TokenType.UNIQUE:
        return "UNIQUE"
    if tt in (TokenType.KEY, TokenType.INDEX):
        return "NORMAL"
    if _is_bare_kw(toks[i]) and (toks[i].text or "").upper() in _INDEX_LEAD_WORDS:
        # 裸 FULLTEXT/SPATIAL 也算，但必须后接 KEY/INDEX、索引名或左括号，
        # 以免把名为 `fulltext` 的**列**误判成索引（反引号形态已由 _is_bare_kw 排除）
        if i + 1 < stop and (toks[i + 1].token_type in (TokenType.KEY, TokenType.INDEX,
                                                        TokenType.L_PAREN)
                             or toks[i + 1].token_type in _IDENT_TOKENS):
            return (toks[i].text or "").upper()
    return None


def _consume_index_definition(toks, i, stop):
    """消费一个索引定义项。

    返回 `(下一个下标, 主目标 COMMENT span, 辅助掩码 span, 索引指纹)`
    或 `(-1, [], [], None)`。指纹为结构化元组。
    """
    kind = _index_lead(toks, i, stop)
    if kind is None:
        return -1, [], [], None
    j = i + 1
    if kind in ("UNIQUE",) + _INDEX_LEAD_WORDS:
        if j < stop and toks[j].token_type in (TokenType.KEY, TokenType.INDEX):
            j += 1
    iname = ""
    if kind != "PRIMARY":                              # PRIMARY 之后不得有索引名
        if j < stop and toks[j].token_type in _IDENT_TOKENS:
            iname = (toks[j].text or "").strip("` ").lower()
            j += 1
    seen_opt = []                                      # 前置与后置 index_type 共用
    if j < stop and toks[j].token_type == TokenType.USING:
        if not (j + 1 < stop and _is_bare_kw(toks[j + 1])
                and (toks[j + 1].text or "").upper() in _TDSQL_INDEX_TYPES):
            return -1, [], [], None
        seen_opt.append("USING")
        j += 2
    j, asc_spans, kparts = _consume_index_key_parts(toks, j, stop)
    if j < 0:
        return -1, [], [], None
    uq_spans = []
    while j < stop and toks[j].token_type != TokenType.COMMA:
        tt = toks[j].token_type
        if tt == TokenType.USING:
            if "USING" in seen_opt:
                return -1, [], [], None
            if not (j + 1 < stop and _is_bare_kw(toks[j + 1])
                    and (toks[j + 1].text or "").upper() in _TDSQL_INDEX_TYPES):
                return -1, [], [], None
            seen_opt.append("USING")
            j += 2
            continue
        if tt == TokenType.COMMENT:
            if "COMMENT" in seen_opt:
                return -1, [], [], None
            if not (j + 1 < stop and toks[j + 1].token_type == TokenType.STRING):
                return -1, [], [], None
            seen_opt.append("COMMENT")
            # UNIQUE / PRIMARY 的 COMMENT 是 sqlglot ParseError → 主目标，记 span；
            # NORMAL / FULLTEXT / SPATIAL 可解析 → 原样保留（生产 gg78 即此形态）
            if kind in ("UNIQUE", "PRIMARY"):
                uq_spans.append((toks[j].start, toks[j + 1].end))
            j += 2
            continue
        return -1, [], [], None
    return j, uq_spans, asc_spans, ("idx", kind, iname, kparts, tuple(sorted(seen_opt)))


def _consume_index_key_parts(toks, i, stop):
    """消费索引键值列表。

    返回 `(下一个下标, ASC/DESC 掩码 span, key_part 元组)` 或 `(-1, [], ())`。
    key_part 元组形如 `((列名, 前缀长度|None, 'ASC'|'DESC'|None), ...)`。
    """
    if i >= stop or toks[i].token_type != TokenType.L_PAREN:
        return -1, [], ()
    spans, parts = [], []
    j = i + 1
    while True:
        if j >= stop or toks[j].token_type not in _IDENT_TOKENS:
            return -1, [], ()
        name = (toks[j].text or "").strip("` ").lower()
        j += 1
        plen = None
        if j < stop and toks[j].token_type == TokenType.L_PAREN:
            # 索引前缀长度必须是**正整数**（与类型的 scale/fsp 不同，后者允许 0）
            v = _int_val(toks[j + 1], allow_zero=False) if j + 1 < stop else None
            if v is None or not (j + 2 < stop and toks[j + 2].token_type == TokenType.R_PAREN):
                return -1, [], ()
            plen, j = v, j + 3
        order = None
        if j < stop and toks[j].token_type in (TokenType.ASC, TokenType.DESC):
            order = toks[j].token_type.name
            spans.append((toks[j].start, toks[j].end))
            j += 1
        parts.append((name, plen, order))
        if j < stop and toks[j].token_type == TokenType.COMMA:
            j += 1
            continue
        if j < stop and toks[j].token_type == TokenType.R_PAREN:
            return j + 1, spans, tuple(parts)
        return -1, [], ()


def _consume_ident(toks, i):
    """消费一个标识符（裸名或反引号名），返回下一个下标；否则 -1。"""
    n = len(toks)
    if i < n and toks[i].token_type in _IDENT_TOKENS:
        return i + 1
    return -1


def _consume_ident_list(toks, i):
    """消费 `( ident [, ident]* )`，返回下一个下标；否则 -1。至少一个，逗号不得前导/尾随/连续。"""
    n = len(toks)
    if i >= n or toks[i].token_type != TokenType.L_PAREN:
        return -1
    j = i + 1
    while True:
        j = _consume_ident(toks, j)
        if j < 0:
            return -1
        if j < n and toks[j].token_type == TokenType.COMMA:
            j += 1
            continue
        if j < n and toks[j].token_type == TokenType.R_PAREN:
            return j + 1
        return -1


# ── 分区值与分区定义（第十轮 BLOCK-J5）───────────────────────────────────────
# 官方二级分区页只明示 year / month / day 三个日期函数；
# Rev.J 另外放行的 DAYOFMONTH / TO_DAYS / TO_SECONDS / UNIX_TIMESTAMP
# 无目标实例证据，本版收回并登记为 unsupported_unproven（KFN 表 B 类）。
_PARTITION_FUNCS = ("YEAR", "MONTH", "DAY")
_SECONDARY_PARTITION_METHODS = ("RANGE", "LIST")
_TDSQL_SHARD_METHODS = ("HASH", "RANGE", "LIST")


def _consume_partition_expr(toks, i, stop):
    """消费分区表达式 `( col )` 或 `( FUNC(col) )`；返回 (下一个下标, 指纹) 或 (-1, "")。

    ⚠️ 分支顺序：**先判"白名单函数 + 左括号"，再判普通列**。
    只有 `YEAR` 有专属 TokenType，`MONTH`/`DAY` 被词法成 VAR；顺序反了它们
    会先被当成普通列名，永远走不到函数分支（第九轮 BLOCK-X5 死分支）。
    """
    if i >= stop or toks[i].token_type != TokenType.L_PAREN:
        return -1, ""
    j = i + 1
    if (j + 1 < stop and toks[j].token_type not in _NON_KEYWORD_TOKENS
            and (toks[j].text or "").upper() in _PARTITION_FUNCS
            and toks[j + 1].token_type == TokenType.L_PAREN):
        fname = (toks[j].text or "").upper()
        # 函数参数必须**恰好一个**列标识符
        if not (j + 3 < stop and toks[j + 2].token_type in _IDENT_TOKENS
                and toks[j + 3].token_type == TokenType.R_PAREN):
            return -1, ""
        shape, j = "%s(1)" % fname, j + 4
    elif j < stop and toks[j].token_type in _IDENT_TOKENS:
        shape, j = "col:%s" % (toks[j].text or "").strip("` ").lower(), j + 1
    else:
        return -1, ""
    return (j + 1, shape) if (j < stop and toks[j].token_type == TokenType.R_PAREN) else (-1, "")


def _skip_balanced_parens(toks, i, stop):
    """从 `(` 开始跳过一整段配平括号，返回下一个下标；不配平返回 -1。

    DEF-SIT-01：只供 R121 的**策略扫描**使用——策略扫描的目标是找到分区
    定义表并读出 VALUES LESS THAN 边界，不需要证明分区表达式合法。表达式
    的合法性校验仍由 `_consume_partition_expr()` 负责，它服务 AST 恢复门禁
    （v1.6.2.2 十三轮评审收敛的最敏感面），本函数绝不替代它，也不得被
    `_plan_recovery()` / `_scan_create_tail()` / `_consume_secondary_partition()`
    调用。
    """
    if i >= stop or toks[i].token_type != TokenType.L_PAREN:
        return -1
    depth, j = 0, i
    while j < stop:
        tt = toks[j].token_type
        if tt == TokenType.L_PAREN:
            depth += 1
        elif tt == TokenType.R_PAREN:
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return -1


def _consume_partition_expr_lenient(toks, i, stop):
    """策略扫描专用：接受 `(任意配平表达式)` 与 `COLUMNS(...)` 两种形态。

    DEF-SIT-01：覆盖 MySQL/TDSQL 允许的全部分区表达式（TO_DAYS/TO_SECONDS/
    UNIX_TIMESTAMP/EXTRACT/ABS/MOD/FLOOR、RANGE COLUMNS、多列 RANGE 等），
    不做函数白名单——真实 `SHOW CREATE TABLE` 产物（如
    ``RANGE (to_days(`dt`))``）是在线元数据审核的主战场形态，白名单曾使
    R121 对其整体失明、括号 MAXVALUE 形态甚至完全静默通过。
    返回 (下一个下标, "lenient") 或 (-1, "")。
    """
    j = i
    if j < stop and _is_bare_kw(toks[j], "COLUMNS"):
        j += 1
    k = _skip_balanced_parens(toks, j, stop)
    return (k, "lenient") if k >= 0 else (-1, "")


def _unquote_str(tok):
    """字符串字面量的归一内容：去外层引号并还原成对转义。

    源侧可能写 `COMMENT="x"`，候选回生成一律是 `COMMENT='x'`；
    不做归一会把同一个值判成不相等（第十二轮 BLOCK-12-04）。
    """
    txt = (tok.text or "")
    if len(txt) >= 2 and txt[0] == txt[-1] and txt[0] in ("'", '"'):
        q = txt[0]
        return txt[1:-1].replace(q + q, q).replace("\\" + q, q)
    return txt


def _consume_value_list(toks, i, stop, allow_maxvalue=False, accept_maxvalue=False):
    """消费 `( 字面量 [, 字面量]* )`；返回 (下一个下标, 值元组) 或 (-1, None)。

    第十轮 BLOCK-J5：**符号只能修饰数值**。Rev.J 先可选吃掉 DASH 再统一接受
    NUMBER 或 STRING，于是 `VALUES IN (-'x')` 被恢复为 Create。
    第十二轮 BLOCK-12-04：Rev.M 只返回**个数**，于是候选把
    `VALUES LESS THAN (10)` 改成 `(99)`、把 `VALUES IN (1,2)` 改成 `(8,9)`，
    指纹完全相同、门禁放行。本版返回逐个归一后的值。
    v1.6.3.2 / REQ-07：`accept_maxvalue=True` 时接受单元素 `MAXVALUE`
    （仅 RANGE 的 VALUES LESS THAN 路径传入）；LIST 的 VALUES IN 不允许。
    括号形态 `(MAXVALUE)` 在**恢复/掩码路径同样接受**（§4.7.5：括号形态命中
    R121、不报 E999——掩码后 sqlglot 可恢复为结构化 AST）；`allow_maxvalue`
    只额外控制 bare 形态的 LESS_THAN_MAXVALUE 归一（策略扫描专属）。
    """
    if i >= stop or toks[i].token_type != TokenType.L_PAREN:
        return -1, None
    j, vals = i + 1, []
    while True:
        if j < stop and toks[j].token_type in (TokenType.DASH, TokenType.PLUS):
            if not (j + 1 < stop and toks[j + 1].token_type == TokenType.NUMBER):
                return -1, None                        # 符号后必须是数字
            sign = "-" if toks[j].token_type == TokenType.DASH else ""
            vals.append(("num", sign + _canonical_number(toks[j + 1].text)))
            j += 2
        elif j < stop and toks[j].token_type == TokenType.NUMBER:
            vals.append(("num", _canonical_number(toks[j].text)))
            j += 1
        elif j < stop and toks[j].token_type == TokenType.STRING:
            vals.append(("str", _unquote_str(toks[j])))
            j += 1
        elif accept_maxvalue and j < stop and _is_bare_kw(toks[j], "MAXVALUE"):
            vals.append(("maxvalue", "MAXVALUE"))
            j += 1
        else:
            return -1, None
        if j < stop and toks[j].token_type == TokenType.COMMA:
            j += 1
            continue
        if j < stop and toks[j].token_type == TokenType.R_PAREN:
            return j + 1, tuple(vals)
        return -1, None


def _consume_partition_values(toks, i, stop, method, allow_maxvalue=False):
    """按**分区方法**消费 VALUES 子句；返回 (下一个下标, 指纹) 或 (-1, "")。

    RANGE → 只接受 `VALUES LESS THAN (...)`。bare `MAXVALUE` 归一为
            `LESS_THAN_MAXVALUE` 指纹**仅当 allow_maxvalue=True**（R121 策略
            扫描路径，v1.6.3.2 / REQ-07）。恢复/掩码路径（allow_maxvalue=False）
            仍拒绝 bare MAXVALUE，保留 sqlglot ParseError/Command → E999 的失败
            关闭（§4.7.5：bare 形态分布式结果至少含 E999 + R121）。
            括号形态 `(MAXVALUE)` **两条路径都接受**（§4.7.5：命中 R121、
            不报 E999——掩码后 sqlglot 可恢复为结构化 AST）。
    LIST  → 只接受 `VALUES IN (...)`，且不接受 MAXVALUE 当普通值
    """
    if i >= stop or toks[i].token_type != TokenType.VALUES:
        return -1, ""
    j = i + 1
    if method == "RANGE":
        if not (j + 1 < stop and _is_bare_kw(toks[j], "LESS") and _is_bare_kw(toks[j + 1], "THAN")):
            return -1, ""
        j += 2
        if allow_maxvalue and j < stop and _is_bare_kw(toks[j], "MAXVALUE"):
            return j + 1, ("LESS_THAN_MAXVALUE", ())   # bare 形态归一（仅策略扫描）
        k, vals = _consume_value_list(toks, j, stop, allow_maxvalue=allow_maxvalue,
                                      accept_maxvalue=True)
        if allow_maxvalue and k >= 0 and len(vals) == 1 and vals[0][0] == "maxvalue":
            return k, ("LESS_THAN_MAXVALUE", ())       # 单元素括号形态归一
        return (k, ("LESS_THAN", vals)) if k >= 0 else (-1, "")
    if method == "LIST":
        if not (j < stop and toks[j].token_type == TokenType.IN):
            return -1, ""
        k, vals = _consume_value_list(toks, j + 1, stop)
        return (k, ("IN", vals)) if k >= 0 else (-1, "")
    return -1, ""                                      # HASH 不得挂 VALUES 定义表


def _consume_partition_options(toks, i, stop):
    """按官方顺序消费 partition_option：`[STORAGE] ENGINE [=] name` 然后 `COMMENT [=] str`。

    第十轮 BLOCK-J5：Rev.J 拒绝官方的 `STORAGE ENGINE=`，却接受反序的
    `COMMENT=… ENGINE=…`。本版按官方序列建小状态机，两者各至多一次且不得反序。
    返回 (下一个下标, 可掩码 span, 指纹)。
    """
    spans, fp = [], []
    j = i
    if j < stop and _is_bare_kw(toks[j], "STORAGE"):
        st = j
        j += 1
        if not (j < stop and _is_bare_kw(toks[j], "ENGINE")):
            return -1, [], ""
        k = j + 1
        if k < stop and toks[k].token_type == TokenType.EQ:
            k += 1
        if k >= stop or toks[k].token_type not in _OPT_NAMEY:
            return -1, [], ""
        spans.append((toks[st].start, toks[k].end))
        fp.append("STORAGE_ENGINE")
        j = k + 1
    elif j < stop and _is_bare_kw(toks[j], "ENGINE"):
        k = j + 1
        if k < stop and toks[k].token_type == TokenType.EQ:
            k += 1
        if k >= stop or toks[k].token_type not in _OPT_NAMEY:
            return -1, [], ""
        spans.append((toks[j].start, toks[k].end))
        fp.append("ENGINE")
        j = k + 1
    if j < stop and toks[j].token_type == TokenType.COMMENT:
        k = j + 1
        if k < stop and toks[k].token_type == TokenType.EQ:
            k += 1
        if k >= stop or toks[k].token_type != TokenType.STRING:
            return -1, [], ""
        spans.append((toks[j].start, toks[k].end))
        fp.append("COMMENT")
        j = k + 1
    return j, spans, "/".join(fp)


def _consume_partition_defs(toks, i, stop, method, require_partition_kw,
                            allow_maxvalue=False):
    """消费分区/分片定义表；返回 (下一个下标, 可掩码 span, 指纹) 或 (-1, [], "")。"""
    if i >= stop or toks[i].token_type != TokenType.L_PAREN:
        return -1, [], ""
    spans, defs = [], []
    j = i + 1
    while True:
        has_kw = j < stop and toks[j].token_type == TokenType.PARTITION
        if has_kw != require_partition_kw:
            return -1, [], ""
        if has_kw:
            j += 1
        if j >= stop or toks[j].token_type not in _IDENT_TOKENS:
            return -1, [], ""
        pname = (toks[j].text or "").strip("` ").lower()
        j += 1
        j, vshape = _consume_partition_values(toks, j, stop, method,
                                              allow_maxvalue=allow_maxvalue)
        if j < 0:
            return -1, [], ""
        j, osp, oshape = _consume_partition_options(toks, j, stop)
        if j < 0:
            return -1, [], ""
        spans.extend(osp)
        defs.append((pname, vshape, oshape))
        if j < stop and toks[j].token_type == TokenType.COMMA:
            j += 1
            continue
        if j < stop and toks[j].token_type == TokenType.R_PAREN:
            return j + 1, spans, tuple(defs)
        return -1, [], ""


def _consume_secondary_partition(toks, i, stop):
    """消费一整个二级分区子句；返回 (下一个下标, 可掩码 span, 指纹) 或 (-1, [], "")。"""
    if i >= stop or toks[i].token_type != TokenType.PARTITION_BY:
        return -1, [], ""
    j = i + 1
    if not (j < stop and _is_bare_kw(toks[j])
            and (toks[j].text or "").upper() in _SECONDARY_PARTITION_METHODS):
        return -1, [], ""
    method = (toks[j].text or "").upper()
    j, eshape = _consume_partition_expr(toks, j + 1, stop)
    if j < 0:
        return -1, [], ""
    j, spans, dshape = _consume_partition_defs(toks, j, stop, method, require_partition_kw=True)
    if j < 0:
        return -1, [], ""
    return j, spans, ("part", method, eshape, dshape)


# ── v1.6.3.2 / REQ-07：二级分区策略事实（R121 的唯一真值源）────────────────
#
# R121 只读 token 层事实，不依赖 sqlglot AST 是否恢复成功：bare MAXVALUE 在
# sqlglot 上 ParseError、ALTER ADD 整体 ParseError、ALTER REORGANIZE 降级为
# Command，三条出口都必须保留策略事实。本扫描器**接收既有 tokens**，禁止再次
# tokenize（Rev.C / N-02 方案 i：并入 _preflight_create_definition_status 的
# 单次词法化）。
_EMPTY_SECONDARY_POLICY = {
    "has_definition": False,
    "method": "",
    "maxvalue_partitions": (),
    "source_context": "",
}


def _maxvalue_partition_names(defs):
    """从分区定义表中提取 VALUES LESS THAN MAXVALUE 的分区名（保序去重）。"""
    names = []
    for pname, vshape, _oshape in defs or ():
        if vshape and vshape[0] == "LESS_THAN_MAXVALUE" and pname not in names:
            names.append(pname)
    return tuple(names)


def _scan_secondary_partition_policy_tokens(toks):
    """扫描二级 RANGE 分区定义与 ALTER 分区定义，返回策略事实 dict。

    只认以下来源（§4.7.2）：
      · CREATE：`PARTITION BY RANGE (...) (...)`（二级子句；一级
        `TDSQL_DISTRIBUTED BY RANGE` 不属于本规则）；
      · ALTER ADD：`ADD PARTITION (...)`；
      · ALTER REORGANIZE：`REORGANIZE PARTITION ... INTO (...)`。
    非 CREATE/ALTER 首 token 返回空策略事实；不消费原 SQL、不再次 tokenize。
    """
    fact = {
        "has_definition": False,
        "method": "",
        "maxvalue_partitions": (),
        "source_context": "",
    }
    if not toks:
        return fact
    first = (toks[0].text or "").upper()
    n = len(toks)
    if first == "CREATE":
        k = 0
        while k + 1 < n:
            if (toks[k].token_type == TokenType.PARTITION_BY
                    and _is_bare_kw(toks[k + 1], "RANGE")):
                fact["has_definition"] = True
                fact["method"] = "RANGE"
                fact["source_context"] = "CREATE"
                # DEF-SIT-01：策略扫描改用宽松表达式消费器（只跳过不校验），
                # 覆盖 TO_DAYS/UNIX_TIMESTAMP/COLUMNS/多列等真实 SHOW CREATE
                # TABLE 形态；恢复门禁的 _consume_partition_expr 严格性不动。
                j, _eshape = _consume_partition_expr_lenient(toks, k + 2, n)
                if j < 0:
                    k += 2
                    continue
                j2, _spans, dshape = _consume_partition_defs(
                    toks, j, n, "RANGE", require_partition_kw=True,
                    allow_maxvalue=True)
                if j2 >= 0:
                    names = _maxvalue_partition_names(dshape)
                    merged = list(fact["maxvalue_partitions"])
                    merged.extend(p for p in names if p not in merged)
                    fact["maxvalue_partitions"] = tuple(merged)
                    k = j2
                    continue
                k = j
                continue
            k += 1
        return fact
    if first == "ALTER":
        k = 1
        while k + 1 < n:
            if _is_bare_kw(toks[k], "ADD") and toks[k + 1].token_type == TokenType.PARTITION:
                fact["has_definition"] = True
                fact["method"] = "RANGE"
                fact["source_context"] = "ALTER_ADD"
                j, _spans, dshape = _consume_partition_defs(
                    toks, k + 2, n, "RANGE", require_partition_kw=True,
                    allow_maxvalue=True)
                if j >= 0:
                    names = _maxvalue_partition_names(dshape)
                    merged = list(fact["maxvalue_partitions"])
                    merged.extend(p for p in names if p not in merged)
                    fact["maxvalue_partitions"] = tuple(merged)
                    k = j
                    continue
                k += 2
                continue
            if (_is_bare_kw(toks[k], "REORGANIZE")
                    and toks[k + 1].token_type == TokenType.PARTITION):
                fact["has_definition"] = True
                fact["method"] = "RANGE"
                fact["source_context"] = "ALTER_REORGANIZE"
                j = k + 2
                while j < n and not (toks[j].token_type == TokenType.IN
                                     or _is_bare_kw(toks[j], "INTO")):
                    j += 1
                if j < n:
                    j += 1
                j2, _spans, dshape = _consume_partition_defs(
                    toks, j, n, "RANGE", require_partition_kw=True,
                    allow_maxvalue=True)
                if j2 >= 0:
                    names = _maxvalue_partition_names(dshape)
                    merged = list(fact["maxvalue_partitions"])
                    merged.extend(p for p in names if p not in merged)
                    fact["maxvalue_partitions"] = tuple(merged)
                    k = j2
                    continue
                k = j
                continue
            k += 1
        return fact
    return fact


# ── 本地表选项（第十轮 BLOCK-J4）─────────────────────────────────────────────
#
# 官方建表页明示的 local_table_option：AUTO_INCREMENT、CHARACTER SET、COLLATE、
# COMMENT、ENGINE、ROW_FORMAT、STATS_AUTO_RECALC、STATS_PERSISTENT、
# STATS_SAMPLE_PAGES。Rev.J 把 ROW_FORMAT 与 STATS_PERSISTENT 判成
# `unsupported_unproven` 是**取证错误**，本版按官方清单补回并给出严格值域。
# CHECKSUM / AVG_ROW_LENGTH / KEY_BLOCK_SIZE / MAX_ROWS / MIN_ROWS /
# PACK_KEYS / DELAY_KEY_WRITE 无 TDSQL 或目标实例证据，继续失败关闭。
_ROW_FORMAT_ENUM = ("DEFAULT", "DYNAMIC", "FIXED", "COMPRESSED", "REDUNDANT", "COMPACT")
_TBL_OPT_SPEC = {
    # name                : (值谓词,            provenance)
    "ENGINE":               ("NAMEY",           "OFFICIAL + CORPUS×78"),
    "COMMENT":              ("STR",             "OFFICIAL + CORPUS×多"),
    "AUTO_INCREMENT":       ("POSINT",          "OFFICIAL + CORPUS×8"),
    "ROW_FORMAT":           ("ROW_FORMAT_ENUM", "OFFICIAL"),
    "STATS_AUTO_RECALC":    ("ZERO_ONE_DEFAULT", "OFFICIAL"),
    "STATS_PERSISTENT":     ("ZERO_ONE_DEFAULT", "OFFICIAL"),
    "STATS_SAMPLE_PAGES":   ("POSINT",          "OFFICIAL"),
    "SHARDKEY":             ("IDENT_LIST",      "OFFICIAL(hash/broadcast) + CORPUS×20"),
}


def _charset_kw_end(toks, i, stop):
    """识别 `CHARSET` / `CHARACTER SET` 关键字，返回其**之后**的下标；不是则返回 -1。

    ⚠️ 词法表现随 sqlglot 版本变化（三版实测）：
      · `CHARSET`          三版都是单个 `CHARACTER_SET` token；
      · `CHARACTER SET`    30.14.0 / 29.0.0 是单个 `CHARACTER_SET` token，
                           **30.17.0 拆成 `CHAR` + `SET` 两个 token**。
    只认 token 类型会让 `CHARACTER SET=utf8mb4` 在 30.17.0 上失败关闭
    （候选回生成用的正是这个拼写，于是合法正例被判成不守恒）。
    这里按**文本**兜住两种表现。
    """
    if i >= stop:
        return -1
    if toks[i].token_type == TokenType.CHARACTER_SET:
        return i + 1
    if (_is_bare_kw(toks[i], "CHARACTER") and i + 1 < stop
            and _is_bare_kw(toks[i + 1], "SET")):
        return i + 2
    return -1


def _consume_table_option(toks, i, stop):
    """消费**一个**完整本地表选项；返回 (下一个下标, identity, 指纹) 或 (-1, "", "")。"""
    if i >= stop:
        return -1, "", ""
    tt = toks[i].token_type
    txt = (toks[i].text or "").upper()

    def _eq(j):
        return j + 1 if (j < stop and toks[j].token_type == TokenType.EQ) else j

    def _take(j, pred):
        j = _eq(j)
        if j >= stop:
            return -1, ""
        t = toks[j]
        if pred == "NAMEY" and t.token_type in _OPT_NAMEY:
            return j + 1, (t.text or "").lower()
        if pred == "STR" and t.token_type == TokenType.STRING:
            return j + 1, _unquote_str(t)              # 记录实际文本，不是 <str>
        if pred == "POSINT" and _int_val(t, allow_zero=False) is not None:
            return j + 1, (t.text or "")
        if pred == "ROW_FORMAT_ENUM" and _is_bare_kw(t) and (t.text or "").upper() in _ROW_FORMAT_ENUM:
            return j + 1, (t.text or "").upper()
        if pred == "ZERO_ONE_DEFAULT":
            if t.token_type == TokenType.NUMBER and (t.text or "") in ("0", "1"):
                return j + 1, (t.text or "")
            if _is_bare_kw(t, "DEFAULT"):
                return j + 1, "DEFAULT"
        if pred == "IDENT_LIST":
            if t.token_type == TokenType.L_PAREN:
                k = _consume_ident_list(toks, j)
                return (k, "<multi>") if k >= 0 else (-1, "")
            if t.token_type in _IDENT_TOKENS:
                return j + 1, (t.text or "").lower()
        return -1, ""

    if tt == TokenType.DEFAULT:
        k = _charset_kw_end(toks, i + 1, stop)
        if k >= 0:
            j, v = _take(k, "NAMEY")
            return (j, "CHARSET", ("CHARSET", v)) if j >= 0 else (-1, "", "")
        if i + 1 < stop and toks[i + 1].token_type == TokenType.COLLATE:
            j, v = _take(i + 2, "NAMEY")
            return (j, "COLLATE", ("COLLATE", v)) if j >= 0 else (-1, "", "")
        return -1, "", ""
    k = _charset_kw_end(toks, i, stop)
    if k >= 0:
        j, v = _take(k, "NAMEY")
        return (j, "CHARSET", ("CHARSET", v)) if j >= 0 else (-1, "", "")
    if tt == TokenType.COLLATE:
        j, v = _take(i + 1, "NAMEY")
        return (j, "COLLATE", ("COLLATE", v)) if j >= 0 else (-1, "", "")
    if tt == TokenType.COMMENT:
        j, v = _take(i + 1, "STR")
        return (j, "COMMENT", ("COMMENT", v)) if j >= 0 else (-1, "", "")
    if tt == TokenType.AUTO_INCREMENT:
        j, v = _take(i + 1, "POSINT")
        return (j, "AUTO_INCREMENT", ("AUTO_INCREMENT", v)) if j >= 0 else (-1, "", "")
    if tt == TokenType.VAR and txt in _TBL_OPT_SPEC:
        pred, _prov = _TBL_OPT_SPEC[txt]
        j, v = _take(i + 1, pred)
        return (j, txt, (txt, v)) if j >= 0 else (-1, "", "")
    return -1, "", ""




# ── 表尾：先解析成带子类型的 atom，再按具名 profile 校验整个序列 ──────────────
#
# 第十一轮 BLOCK-11-02：Rev.L 的四状态 FSM 含 `S2→S3` 与 `S3→S2` 回环，
# 于是 `DIST → PARTITION → DIST`、`shardkey → PARTITION → DIST` 这类
# **双一级分布声明**被放行；状态只表达"当前阶段"，不保留历史计数。
# 第十一轮 BLOCK-11-03：`shardkey=noshardkey_allset` 与普通 shardkey 被归一成
# 同一个 atom，于是伪哨兵 `shardkey=(noshardkey_allset,id)`、广播再分区全部放行。
#
# Rev.M 改为两步：① 解析成 typed atoms；② 整个序列必须**完整匹配**一个具名 profile。
# atom 子类型：
#   LOCAL(<option名>)    本地表选项
#   HASH_SHARDKEY        shardkey=<单列> 或 shardkey=(<多列>)
#   BROADCAST_SENTINEL   shardkey=noshardkey_allset（**精确哨兵**，不接受括号/混合）
#   BROADCAST_KEYWORD    裸 BROADCAST 关键字
#   DIST(<方法>)         TDSQL_DISTRIBUTED BY hash|range|list(col) [分片定义表]
#   PARTITION            二级分区子句
_BROADCAST_SENTINEL = "NOSHARDKEY_ALLSET"

# 具名 capability profile（第十一轮 MAJOR-11-02）：每条允许序列有唯一 provenance，
# **每条 SQL 必须完整匹配其中一个**，禁止跨 profile 拼接。
# 序列用正则式记法：L* 表示任意多个 LOCAL；? 表示可选。
_TAIL_PROFILES = (
    # (profile, 序列模板, provenance)
    ("TARGET_CURRENT",  ("L*",),                              "无分布声明的普通表"),
    ("TARGET_CURRENT",  ("L*", "HASH_SHARDKEY"),              "OFFICIAL hash 分片；CORPUS 生产 fixture 实测"),
    ("TARGET_CURRENT",  ("L*", "BROADCAST_SENTINEL"),         "OFFICIAL 广播表哨兵"),
    ("TARGET_CURRENT",  ("L*", "BROADCAST_KEYWORD"),          "TARGET_INSTANCE 广播表关键字形态"),
    ("TARGET_CURRENT",  ("L*", "HASH_SHARDKEY", "BROADCAST_KEYWORD"),
                                                              "ADJ-6 characterization：用户冻结的现状，**不代表 TDSQL 合法**"),
    ("TARGET_CURRENT",  ("L*", "DIST"),                       "OFFICIAL 一级 range/list 声明；目标实例 HASH 形态"),
    ("TARGET_CURRENT",  ("L*", "DIST", "PARTITION"),          "PROJECT_ACCEPTED：D5/T5 既有用例，O 第八轮明确接受"),
    ("LEGACY_PARTITION", ("L*", "HASH_SHARDKEY", "PARTITION"), "OFFICIAL 二级分区原例 `shardkey=col PARTITION BY LIST(...)`"),
    ("LEGACY_PARTITION", ("L*", "PARTITION", "DIST"),          "OFFICIAL 二级分区原例 `tb_sub_r_l`"),
    ("LEGACY_PARTITION", ("L*", "PARTITION"),                  "OFFICIAL：仅二级分区、无一级声明"),
)

# 第三个代际 profile：**已具名声明，但成员集为空**（第十一轮 MAJOR-11-02）。
# 新语法 `TDSQL_DISTRIBUTED BY HASH(col) TDSQL_PARTITION BY RANGE|LIST(col) (...)`
# 未取得目标实例证据、也未出现在 197 条语料与生产 14 表中（0 次），
# 按本方案自己的 provenance 原则归 `unsupported_unproven`：
# **登记能力代际，但不放行**——`TDSQL_PARTITION` 不产生 atom，整条语句失败关闭。
# 取得目标实例证据后，只需把下表条目搬进 `_TAIL_PROFILES` 即可，无需改判定逻辑。
_TAIL_PROFILES_UNPROVEN = (
    ("NEW_SECONDARY", ("L*", "DIST", "TDSQL_PARTITION"),
     "腾讯新版二级分区语法；无目标实例证据、语料 0 例 → 暂不放行"),
    ("NEW_SECONDARY", ("L*", "HASH_SHARDKEY", "TDSQL_PARTITION"),
     "同上"),
)


def _match_tail_profile(kinds):
    """整个 atom 序列是否完整匹配某个 profile；匹配返回 (profile, provenance)，否则 None。

    只在 `_TAIL_PROFILES` 中查找。`_TAIL_PROFILES_UNPROVEN` 是**纯登记表**，
    刻意不参与匹配——未取证的能力代际不得放行（MAJOR-11-02）。
    """
    for prof, tmpl, prov in _TAIL_PROFILES:
        seq = list(kinds)
        ok, ti = True, 0
        for part in tmpl:
            if part == "L*":
                while seq and seq[0] == "LOCAL":
                    seq.pop(0)
            else:
                if not seq or seq[0] != part:
                    ok = False
                    break
                seq.pop(0)
            ti += 1
        if ok and not seq:
            return prof, prov
    return None


def _consume_shardkey_value(toks, i, stop):
    """消费 shardkey 的值并**分型**；返回 (下一个下标, 子类型, 指纹) 或 (-1, None, None)。

    官方广播哨兵是**裸的、单个、精确**的 `noshardkey_allset`；
    `shardkey=(noshardkey_allset)`、`shardkey=(noshardkey_allset, id)` 一律不是哨兵，
    且不得被当成普通分片键放行（第十一轮 BLOCK-11-03）。
    """
    j = i + 1 if (i < stop and toks[i].token_type == TokenType.EQ) else i
    if j >= stop:
        return -1, None, None
    if toks[j].token_type == TokenType.L_PAREN:
        k, cols = j + 1, []
        while True:
            if k >= stop or toks[k].token_type not in _IDENT_TOKENS:
                return -1, None, None
            nm = (toks[k].text or "").strip("` ").lower()
            if nm.upper() == _BROADCAST_SENTINEL:
                return -1, None, None                  # 哨兵不得出现在列表里
            cols.append(nm)
            k += 1
            if k < stop and toks[k].token_type == TokenType.COMMA:
                k += 1
                continue
            if k < stop and toks[k].token_type == TokenType.R_PAREN:
                return k + 1, "HASH_SHARDKEY", ("shardkey", tuple(cols))
            return -1, None, None
    if toks[j].token_type in _IDENT_TOKENS:
        nm = (toks[j].text or "").strip("` ").lower()
        if nm.upper() == _BROADCAST_SENTINEL:
            return j + 1, "BROADCAST_SENTINEL", ("broadcast_sentinel",)
        return j + 1, "HASH_SHARDKEY", ("shardkey", (nm,))
    return -1, None, None


def _scan_table_tail(toks, start, stop, exec_atoms=()):
    """把表尾解析成 typed atoms，再整体匹配 profile。

    `exec_atoms` 是 `_validate_executable_comments()` 产出的带原始字符 span、
    `left_idx/right_idx` 与 partition_shape 的条目。只有条目的左右 token 恰好等于
    两个**完整 atom**之间的边界，才允许合并进 atom 流（第十三轮 BLOCK-13-02）。
    合并进来的分区在指纹里标成 `source_only=True`：候选 AST 里不会有它们
    （sqlglot 根本看不见可执行注释），故不参与候选侧比较。

    返回 (方言目标 span, 辅助掩码 span, 表尾指纹)；不合规返回 (None, None, None)。
    """
    tgt_spans, mask_spans, atoms, fp = [], [], [], []
    seen_local = []
    pending = sorted(exec_atoms or (), key=lambda e: e["comment_start"])
    prev_atom_last = start - 1

    def _flush_exec_at_boundary(left_idx, right_idx):
        """只在完整 atom 边界插入；返回 False 表示注释落在 atom 内部。"""
        if not pending or pending[0]["right_idx"] > right_idx:
            return True
        e = pending[0]
        if e["left_idx"] != left_idx or e["right_idx"] != right_idx:
            return False
        pending.pop(0)
        atoms.append("PARTITION")
        fp.append(("exec_partition", e["partition_shape"]))
        return True

    i = start
    while i < stop:
        if not _flush_exec_at_boundary(prev_atom_last, i):
            return None, None, None                    # COMMENT 位于复合 atom 内部
        tt = toks[i].token_type
        if tt == TokenType.PARTITION_BY:
            j, msp, pshape = _consume_secondary_partition(toks, i, stop)
            if j < 0:
                return None, None, None
            mask_spans.extend(msp)
            atoms.append("PARTITION")
            fp.append(pshape)
            prev_atom_last = j - 1
            i = j
            continue
        if _is_bare_kw(toks[i], "TDSQL_DISTRIBUTED"):
            if not (i + 1 < stop and _is_bare_kw(toks[i + 1], "BY")):
                return None, None, None
            if not (i + 2 < stop and _is_bare_kw(toks[i + 2])
                    and (toks[i + 2].text or "").upper() in _TDSQL_SHARD_METHODS):
                return None, None, None
            method = (toks[i + 2].text or "").upper()
            j = i + 3
            if not (j + 2 < stop and toks[j].token_type == TokenType.L_PAREN
                    and toks[j + 1].token_type in _IDENT_TOKENS
                    and toks[j + 2].token_type == TokenType.R_PAREN):
                return None, None, None
            key = (toks[j + 1].text or "").strip("` ").lower()
            j += 3
            end_tok, dshape = j - 1, ()
            if j < stop and toks[j].token_type == TokenType.L_PAREN:
                if method == "HASH":
                    return None, None, None            # 官方仅 range/list 带分片定义表
                j2, msp, dshape = _consume_partition_defs(
                    toks, j, stop, method, require_partition_kw=False)
                if j2 < 0:
                    return None, None, None
                mask_spans.extend(msp)
                end_tok, j = j2 - 1, j2
            tgt_spans.append((toks[i].start, toks[end_tok].end))
            atoms.append("DIST")
            fp.append(("dist", method, key, dshape))
            prev_atom_last = j - 1
            i = j
            continue
        if _is_bare_kw(toks[i], "BROADCAST"):
            tgt_spans.append((toks[i].start, toks[i].end))
            atoms.append("BROADCAST_KEYWORD")
            fp.append(("broadcast_keyword",))
            prev_atom_last = i
            i += 1
            continue
        j, ident, oshape = _consume_table_option(toks, i, stop)
        if j < 0:
            return None, None, None
        if ident == "SHARDKEY":
            k, sub, sfp = _consume_shardkey_value(toks, i + 1, stop)
            if k < 0:
                return None, None, None
            atoms.append(sub)
            fp.append(sfp)
            prev_atom_last = k - 1
            i = k
            continue
        if ident in seen_local:
            return None, None, None                    # 同名本地选项不可重复
        seen_local.append(ident)
        atoms.append("LOCAL")
        fp.append(oshape)
        prev_atom_last = j - 1
        i = j
    if not _flush_exec_at_boundary(prev_atom_last, stop) or pending:
        return None, None, None                        # 尾部之外或 atom 内部仍有未归属注释
    # ── 计数硬断言（即使 profile 表将来扩充也必须成立）──
    if sum(1 for a in atoms if a in ("HASH_SHARDKEY", "BROADCAST_SENTINEL",
                                     "BROADCAST_KEYWORD", "DIST")) > 1:
        # 唯一例外是 ADJ-6 的 `HASH_SHARDKEY + BROADCAST_KEYWORD`，由 profile 表精确批准
        if [a for a in atoms if a != "LOCAL"] != ["HASH_SHARDKEY", "BROADCAST_KEYWORD"]:
            return None, None, None
    if sum(1 for a in atoms if a == "PARTITION") > 1:
        return None, None, None
    m = _match_tail_profile(atoms)
    if m is None:
        return None, None, None                        # 未列明的序列一律失败关闭
    return tgt_spans, mask_spans, ("tail", m[0], tuple(fp))


# ── MySQL 可执行注释（第十一轮 BLOCK-11-01）─────────────────────────────────
#
# sqlglot 的词法器不会把 `/*!50100 ... */` 的内容变成主 token；不同位置、不同版本下
# `token.comments` 的归属不能证明原文插入边界。Rev.O 因而只把 token 的原始字符 span
# 当作词法保护边界，在相邻 token 之间的原文 gap 中定位可执行注释，不读取 owner。
#
# 本版在规划入口显式处理：普通注释继续忽略；`!<版本号>` 开头的可执行注释
# **必须整段通过验证**，且本版只接受**一个完整的**二级分区 payload。
_EXEC_COMMENT_IN_GAP_RE = re.compile(
    r"/\*!\s*(?P<version>\d*)\s*(?P<payload>.*?)\*/", re.DOTALL)


def _collect_executable_comments(sql, toks):
    """在 sqlglot 已证明“无主 token”的 gap 内定位可执行注释原始 span。

    不相信 `token.comments` 的 owner 推断，也不在整条 SQL 上做替换。字符串、反引号
    标识符等都已被 sqlglot 划为 token，不会进入 gap；正则只负责从 token-free gap
    中取得 `/*!...*/` 的字符区间和 payload。
    """
    gaps = []
    if toks:
        gaps.append((-1, 0, 0, toks[0].start))
        for idx in range(len(toks) - 1):
            gaps.append((idx, idx + 1, toks[idx].end + 1, toks[idx + 1].start))
        gaps.append((len(toks) - 1, len(toks), toks[-1].end + 1, len(sql)))
    else:
        gaps.append((-1, 0, 0, len(sql)))
    out = []
    for left_idx, right_idx, gs, ge in gaps:
        if ge <= gs:
            continue
        for m in _EXEC_COMMENT_IN_GAP_RE.finditer(sql[gs:ge]):
            out.append({
                "comment_start": gs + m.start(),
                "comment_end": gs + m.end(),          # 半开区间
                "left_idx": left_idx,
                "right_idx": right_idx,
                "payload": (m.group("payload") or "").strip(),
            })
    return sorted(out, key=lambda e: e["comment_start"])


def _validate_executable_comments(sql, toks, close_idx, statement_end, dialect="mysql"):
    """验证 payload 与顶层域；完整 atom 边界由 `_scan_table_tail()` 最终裁决。"""
    entries = _collect_executable_comments(sql, toks)
    if not entries:
        return True, []
    if len(entries) > 1:
        return False, None                             # 多个可执行注释 → 失败关闭
    entry = entries[0]
    if (entry["comment_start"] <= toks[close_idx].end
            or entry["comment_end"] > statement_end):
        return False, None                             # 位置越界：建表头 / 定义列表内部
    try:
        ptoks = sqlglot.Dialect.get_or_raise(dialect).tokenizer_class().tokenize(
            entry["payload"])
    except Exception:
        return False, None
    if not ptoks or ptoks[0].token_type != TokenType.PARTITION_BY:
        return False, None
    j, _msp, pshape = _consume_secondary_partition(ptoks, 0, len(ptoks))
    if j != len(ptoks):
        return False, None                             # 未消费到结尾 → 失败关闭
    entry["partition_shape"] = pshape
    return True, [entry]


def _scan_definition_list(toks, open_idx, close_idx):
    """逐项消费顶层定义列表。

    返回 (定义指纹元组, 主目标 span, 辅助掩码 span)；不合规返回 (None, [], [])。
    """
    defs, uq_spans, mask_spans = [], [], []
    i = open_idx + 1
    while i < close_idx:
        if toks[i].token_type == TokenType.CONSTRAINT:
            # 用户冻结：本期只支持具名 PRIMARY；CONSTRAINT UNIQUE 不扩能力。
            # Rev.N“消费后顺带恢复”会让该唯一语义在 ParsedSQL 中消失并造成 R054 漏报，
            # Rev.O 改为具名失败关闭，绝不恢复一个下游看不懂的合法约束。
            k = i + 1
            if k < close_idx and toks[k].token_type in _IDENT_TOKENS:
                k += 1
            symbol = _ident_text(toks[i + 1]) if k > i + 1 else ""
            j, _usp, asp, shape = _consume_index_definition(toks, k, close_idx)
            if j < 0 or shape is None:
                return None, [], []
            if shape[1] not in ("PRIMARY", "UNIQUE"):
                return None, [], []                   # 其他 CONSTRAINT 形态仍不在支持域
            # PRIMARY COMMENT 是可恢复主目标；CONSTRAINT UNIQUE 只完整消费并登记
            # KFN-6，由全路径 source preflight 与候选门禁失败关闭，绝不恢复成无语义 AST。
            if shape[1] == "PRIMARY":
                uq_spans.extend(_usp)
            mask_spans.extend(asp)
            # 第十二轮 MAJOR-12-01：symbol 记入指纹（放末位，不改既有 off 偏移）
            defs.append(("constraint",) + shape + (symbol,))
        elif _index_lead(toks, i, close_idx) is not None:
            j, usp, asp, shape = _consume_index_definition(toks, i, close_idx)
            if j < 0 or shape is None:
                return None, [], []
            uq_spans.extend(usp)
            mask_spans.extend(asp)
            defs.append(shape)
        else:
            j, shape, csp = _consume_column_definition(toks, i, close_idx)
            if j < 0:
                return None, [], []
            mask_spans.extend(csp)
            defs.append(shape)
        if j < close_idx and toks[j].token_type == TokenType.COMMA:
            j += 1
            if j >= close_idx:
                return None, [], []
        elif j < close_idx:
            return None, [], []
        i = j
    return (tuple(defs), uq_spans, mask_spans) if defs else (None, [], [])


def _definition_kfns(defs):
    """从 SourceShape 收集具名已知假阴性；返回稳定去重后的编号元组。"""
    out = []
    for d in defs or ():
        if not d:
            continue
        if d[0] == "constraint":
            if len(d) >= 3 and d[2] == "UNIQUE":
                out.append("KFN-6-CONSTRAINT-UNIQUE")
                continue
            # `CONSTRAINT PRIMARY KEY`（省略 symbol）是既有 KFN-4：规划器能完整
            # 识别，但三版候选均 ParseError。constraint shape 的最后一项就是 symbol。
            if len(d) >= 7 and d[2] == "PRIMARY" and not d[6]:
                out.append("KFN-4-CONSTRAINT-PRIMARY-NO-SYMBOL")
            continue
        if d[0] != "col":
            continue
        type_shape, cons = d[2], d[3]
        if len(type_shape) >= 5:
            out.extend(type_shape[4] or ())
        out.extend(v for k, v in cons if k == "KFN")
    return tuple(sorted(set(out)))


def _top_level_definition_ranges(toks, open_idx: int, close_idx: int):
    """切分 CREATE TABLE 顶层定义项；不解释定义内容。"""
    ranges = []
    start = open_idx + 1
    depth = 0
    for i in range(start, close_idx):
        token_type = toks[i].token_type
        if token_type == TokenType.L_PAREN:
            depth += 1
        elif token_type == TokenType.R_PAREN:
            if depth == 0:
                return None
            depth -= 1
        elif token_type == TokenType.COMMA and depth == 0:
            if start >= i:
                return None
            ranges.append((start, i))
            start = i + 1
    if depth != 0 or start >= close_idx:
        return None
    ranges.append((start, close_idx))
    return tuple(ranges)


def _definition_item_kfns(toks, start: int, stop: int):
    """从一个顶层定义项提取可证明的 KFN，不要求完整消费邻接定义项。"""
    if start >= stop:
        return ()
    found = []

    # `[CONSTRAINT [symbol]] PRIMARY/UNIQUE ...`：这里只判定约束头；
    # CONSTRAINT UNIQUE 整族已由 ADJ-11 冻结为 KFN，不需要先完整理解其尾部。
    if toks[start].token_type == TokenType.CONSTRAINT:
        i = start + 1
        symbol = ""
        if i < stop and toks[i].token_type in _IDENT_TOKENS:
            symbol = _ident_text(toks[i])
            i += 1
        kind = _index_lead(toks, i, stop)
        if kind == "UNIQUE":
            found.append("KFN-6-CONSTRAINT-UNIQUE")
        elif kind == "PRIMARY" and not symbol:
            found.append("KFN-4-CONSTRAINT-PRIMARY-NO-SYMBOL")
        return tuple(sorted(set(found)))

    # 列定义：类型产生式本身即可证明 SERIAL / SIGNED / BINARY 等 KFN。
    # `SERIAL DEFAULT VALUE` 只在列属性顶层识别；字符串是单 token，括号表达式
    # 由 depth 隔离。全程 O(n)，不得用逐前缀重解析把 preflight 放大为 O(n²)。
    if toks[start].token_type in _IDENT_TOKENS:
        j, type_shape = _consume_data_type(toks, start + 1, stop)
        if j >= 0 and type_shape is not None:
            found.extend(type_shape[4] or ())
            depth = 0
            k = j
            while k < stop:
                token_type = toks[k].token_type
                if token_type == TokenType.L_PAREN:
                    depth += 1
                elif token_type == TokenType.R_PAREN:
                    if depth == 0:
                        break
                    depth -= 1
                elif (depth == 0 and _is_bare_kw(toks[k], "SERIAL")
                      and k + 2 < stop
                      and toks[k + 1].token_type == TokenType.DEFAULT
                      and _is_bare_kw(toks[k + 2], "VALUE")):
                    found.append("KFN-5-SERIAL-DEFAULT-VALUE")
                    k += 3
                    continue
                k += 1
    return tuple(sorted(set(found)))


def _preflight_create_definition_status(sql: str, dialect: str = "mysql"):
    """一次词法化同时返回 `(逐项 KFN, 定义列表完整性, 二级分区策略事实)`。

    v1.6.3.2 / Rev.C / N-02：返回值由二元组扩为三元组；二级分区策略扫描
    接收本次既有的 tokens（禁止再次 tokenize），且在 `open_idx < 0` 的
    CREATE 专用提前返回**之前**按首个有效 token 分流 CREATE/ALTER——
    ALTER ADD/REORGANIZE 的策略事实不依赖定义边界是否可得。
    所有出口（tokenize 异常、终止分号非法、非 CREATE/ALTER）都返回相同
    三元结构，空策略事实使用统一默认值。
    """
    try:
        toks = sqlglot.Dialect.get_or_raise(dialect).tokenizer_class().tokenize(sql)
    except Exception:
        return (), False, dict(_EMPTY_SECONDARY_POLICY)
    toks = _strip_terminal_semicolon(toks)
    if toks is None:
        return (), False, dict(_EMPTY_SECONDARY_POLICY)
    policy = _scan_secondary_partition_policy_tokens(toks)
    open_idx, close_idx, _table_name, _head = _tdsql_table_def_bounds(toks)
    if open_idx < 0:
        return (), False, policy
    ranges = _top_level_definition_ranges(toks, open_idx, close_idx)
    if ranges is None:
        return (), False, policy

    # KFN 与 strict scanner 的全表成功/失败解耦：任何一个未知伴生项都不得
    # 清空其他项已经证明的 KFN。
    known = []
    for start, stop in ranges:
        known.extend(_definition_item_kfns(toks, start, stop))
    defs, _primary, _auxiliary = _scan_definition_list(toks, open_idx, close_idx)
    return tuple(sorted(set(known))), defs is not None, policy


def _preflight_known_fidelity_failures(sql: str, dialect: str = "mysql"):
    """兼容测试/诊断入口；产品 parse() 使用同一 status 函数，避免重复词法化。"""
    return _preflight_create_definition_status(sql, dialect)[0]


def _strip_terminal_semicolon(toks):
    """允许 0 或 1 个、且仅位于 EOF 前的终止分号；否则返回 None。"""
    n = len(toks)
    sem = [k for k, t in enumerate(toks) if t.token_type == TokenType.SEMICOLON]
    if not sem:
        return toks
    if len(sem) > 1 or sem[0] != n - 1:
        return None
    return toks[:-1]


def _plan_recovery(sql: str, dialect: str = "mysql"):
    """统一恢复规划器：按 TDSQL 官方语法验证整条建表语句并生成结构化指纹。"""
    try:
        toks = sqlglot.Dialect.get_or_raise(dialect).tokenizer_class().tokenize(sql)
    except Exception:
        return None
    boundary_kfns = ()
    if (toks and toks[-1].token_type == TokenType.SEMICOLON
            and sql[toks[-1].end + 1:].strip()):
        # 普通注释不会成为主 token；若它位于终止分号之后，当前候选解析器会失败。
        # 规划器仍须把这个既有官方形态具名登记，而不是碰巧在 candidate 阶段失败。
        boundary_kfns = ("KFN-4-TRAILING-COMMENT-AFTER-SEMICOLON",)
    statement_end = (toks[-1].start if toks and toks[-1].token_type == TokenType.SEMICOLON
                     else len(sql))
    toks = _strip_terminal_semicolon(toks)
    if toks is None:
        return None
    open_idx, close_idx, table_name, head = _tdsql_table_def_bounds(toks)
    if open_idx < 0:
        return None
    # 可执行注释必须在拿到定义列表边界之后验证——位置合法性依赖 close_idx
    ok, exec_atoms = _validate_executable_comments(
        sql, toks, close_idx, statement_end, dialect)
    if not ok:
        return None                                    # 可执行注释未通过验证 → 失败关闭
    defs, uq_spans, mask_a = _scan_definition_list(toks, open_idx, close_idx)
    if defs is None:
        return None
    tgt_spans, mask_b, tail_fp = _scan_table_tail(
        toks, close_idx + 1, len(toks), exec_atoms)
    if tgt_spans is None:
        return None
    primary = list(uq_spans) + list(tgt_spans)
    if not primary:
        return None                                    # 无主目标 → 不恢复
    tok_part = any(t.token_type == TokenType.PARTITION_BY for t in toks)
    kfns = tuple(sorted(set(_definition_kfns(defs) + boundary_kfns)))
    return {
        "table": table_name,
        "primary_spans": primary,
        "auxiliary_spans": list(mask_a) + list(mask_b),
        # ── SourceFingerprint = CreateShape（第十二轮 BLOCK-12-04）──
        #   head        顶层语义：(schema, table) 全限定名 + TEMPORARY + IF NOT EXISTS
        #   definitions 定义列表形状（列 / 索引 / 具名约束）
        #   tail        表尾形状：本地表选项 + 分布 atom + 二级分区细节
        # 三者都必须进入候选比较；Rev.M 只比了 definitions，于是候选把
        # `db1.t` 换成 `db2.t`、把 ENGINE 换成 MyISAM、把分区边界改掉，
        # 门禁一律返回 True。
        "fingerprint": {
            "head": head,
            "table": (table_name or "").strip("` ").lower(),
            "definitions": defs,
            "tail": tail_fp,
        },
        # 分区保真门禁只对**主 token 流里的**分区生效；
        # 可执行注释里的分区 sqlglot 不产生节点，其完整性已由
        # `_validate_executable_comments()` 独立证明，并已按源序并入
        # 表尾 atom 流参与计数与 profile 匹配（第十二轮 BLOCK-12-01）。
        "had_partition": tok_part,
        "exec_comment_partition": bool(exec_atoms),
        # 非空时表示“官方合法但本期不能保真”。parse() 仍能证明规划器具名接受，
        # `_validate_recovery_candidate()` 则强制失败关闭，避免普通 plan=False 与 KFN 混淆。
        "known_false_negatives": kfns,
    }


def _same_table_name(node, expected: str) -> bool:
    """候选 AST 的表名是否与从原文提取的表名一致。

    只去反引号 —— **不再剥单引号**：STRING 表名已在定位阶段被拒绝，
    此处若继续归一化单引号，等于把被拒的形态又放回来（第五轮 BLOCK-E2）。
    """
    if not expected:
        return False
    schema = node.this
    tbl = schema.this if isinstance(schema, exp.Schema) else schema
    name = (getattr(tbl, "name", "") or "") if tbl is not None else ""
    return bool(name) and name.strip("` ").lower() == expected.strip("` ").lower()


def _blank_spans(sql: str, spans):
    """把给定 span 等长置空（保留换行），返回新串；越界返回 None。"""
    if not spans:
        return sql
    buf = list(sql)
    for s, e in spans:
        if not (0 <= s <= e < len(buf)):
            return None
        for q in range(s, e + 1):
            if buf[q] != "\n":
                buf[q] = " "
    return "".join(buf)


# 分区保真门禁用：候选 AST 中代表二级分区的 properties 节点名前缀


# ── 候选 AST 结构守恒门禁（第十一轮 BLOCK-11-05）─────────────────────────────
#
# Rev.L 的门禁只比较列名与类型字符串，索引一律折叠成 `(IDX, None, None)`。
# 白盒反向鉴别证明：丢掉 `NOT NULL DEFAULT 7`、把 `UNIQUE u(id)` 换成 `KEY v(x)`、
# 换成 `PRIMARY KEY(x)`，门禁**全部返回 True**。本版逐字段比较。
#
# 被批准忽略的差异（各有具名理由，必须逐条列出）：
_GATE_IGNORED_COL_CONSTRAINTS = (
    "COLUMN_FORMAT",      # 官方列属性，已作辅助掩码剥离（sqlglot 不认）
    "ENGINE_ATTRIBUTE",   # 同上
)
# 列 COMMENT **不在 ignored 集合**：指纹值仍为 None，表示只比较“有/无”，
# 不重复比较文本。文本保真由 raw_sql、_extract_column_comment() 与 R029 端到端断言负责。
_GATE_IGNORED_INDEX_OPTS = (
    "COMMENT",            # UNIQUE/PRIMARY 的注释正是本次掩码目标
)


def _canonical_default_from_sql(text, dialect="mysql"):
    """把候选 AST 回生成的 `DEFAULT <值>` / `ON UPDATE <值>` 送进**同一个**
    `_consume_default_value()`，保证两侧规范形一致（第十一轮 BLOCK-11-05）。"""
    body = (text or "").strip()
    for lead in ("DEFAULT", "ON UPDATE"):
        if body.upper().startswith(lead):
            body = body[len(lead):].strip()
            break
    try:
        toks = sqlglot.Dialect.get_or_raise(dialect).tokenizer_class().tokenize(body)
    except Exception:
        return None
    j, val = _consume_default_value(toks, 0, len(toks))
    return val if j == len(toks) else None


def _ast_column_shape(col):
    """从候选 AST 的列定义提取可比结构；无法提取返回 None。"""
    kind = col.args.get("kind")
    if kind is None:
        return None
    shape = _canonical_type_from_sql(kind.sql(dialect="mysql"))
    if shape is None:
        return None
    cons = []
    for c in (col.args.get("constraints") or []):
        k = c.args.get("kind")
        nm = type(k).__name__ if k is not None else ""
        if nm == "NotNullColumnConstraint":
            cons.append(("NULLABILITY", "NULL" if k.args.get("allow_null") else "NOTNULL"))
        elif nm == "DefaultColumnConstraint":
            cons.append(("DEFAULT", _canonical_default_from_sql(k.sql(dialect="mysql"))))
        elif nm == "AutoIncrementColumnConstraint":
            cons.append(("AUTO_INCREMENT", None))
        elif nm == "CollateColumnConstraint":
            cons.append(("COLLATE", (k.sql(dialect="mysql") or "").split()[-1].strip("`\"' ").lower()))
        elif nm == "CharacterSetColumnConstraint":
            cons.append(("CHARACTER_SET", (k.sql(dialect="mysql") or "").split()[-1].strip("`\"' ").lower()))
        elif nm in ("PrimaryKeyColumnConstraint", "UniqueColumnConstraint"):
            cons.append(("KEYNESS", "PRIMARY" if nm.startswith("Primary") else "UNIQUE"))
        elif nm == "OnUpdateColumnConstraint":
            cons.append(("ON_UPDATE", _canonical_default_from_sql(k.sql(dialect="mysql"))))
        elif nm == "CommentColumnConstraint":
            cons.append(("COMMENT", None))
    return (col.name or "").strip("` ").lower(), shape, tuple(cons)


def _ast_index_using(node):
    """判定候选 AST 的索引节点是否携带 `USING`。

    sqlglot 30.14.0 实测：同一个 `USING BTREE` 依索引种类与书写位置落在**三个
    不同的 arg** 上，只读 `index_type` 会把 `PRIMARY KEY (id) USING BTREE`
    误判为“无 USING”，从而把本应恢复的语句挡在门外（第十一轮 P 组实测）：

      · `index_type=str`                              —— UNIQUE 的任意位置；
                                                         KEY 的前置 USING
      · `options=[IndexConstraintOption(using=...)]`  —— KEY 的后置 USING
      · `include=IndexParameters(using=...)`          —— PRIMARY KEY 的后置 USING

    三处任一命中即认定存在 USING。options 逐项按 arg 名判定而非按节点类名判定，
    因为 `IndexConstraintOption` 同时承载 comment / key_block_size 等其他选项。
    """
    it = node.args.get("index_type")
    if isinstance(it, str) and it:
        return True
    for o in (node.args.get("options") or []):
        if getattr(o, "args", None) and o.args.get("using") is not None:
            return True
    inc = node.args.get("include")
    if inc is not None and getattr(inc, "args", None) and inc.args.get("using") is not None:
        return True
    return False


def _ast_index_shape(node):
    """从候选 AST 的索引定义提取 (kind, 名称, key_parts, 选项)；无法提取返回 None。"""
    nm = type(node).__name__
    if nm == "PrimaryKey":
        kind, iname = "PRIMARY", ""
        exprs = node.args.get("expressions") or []
    elif nm == "UniqueColumnConstraint":
        kind = "UNIQUE"
        sch = node.args.get("this")
        iname = ""
        exprs = []
        if sch is not None:
            t = sch.args.get("this") if hasattr(sch, "args") else None
            iname = (getattr(t, "name", "") or "") if t is not None else ""
            exprs = sch.args.get("expressions") or []
    elif nm == "IndexColumnConstraint":
        k = node.args.get("kind")
        kind = (str(k).upper() if k else "NORMAL")
        iname = (getattr(node.args.get("this"), "name", "") or "")
        exprs = node.args.get("expressions") or []
    else:
        return None
    parts = []
    for e in exprs:
        txt = (e.sql(dialect="mysql") or "").strip()
        base = txt.strip("`")
        plen = None
        if "(" in txt and txt.endswith(")"):
            head, num = txt[:txt.rindex("(")], txt[txt.rindex("(") + 1:-1].strip()
            if num.isdigit():
                base, plen = head.strip().strip("`"), int(num)
        parts.append((base.strip("` ").lower(), plen))
    opts = ("USING",) if _ast_index_using(node) else ()
    return kind, (iname or "").strip("` ").lower(), tuple(parts), opts


# ── 表尾里**故意**从候选 AST 移除的 atom（source-only approved transform）──
#
# 方言声明被掩码是本方案的既定动作，可执行注释里的分区 sqlglot 根本看不见；
# 它们由 raw SQL 规则与 capability profile 负责，不能与普通 table tail 混为一谈
# （第十二轮 BLOCK-12-04）。分区定义里的 `[STORAGE] ENGINE` / `COMMENT`
# 选项也是既定掩码目标，同样不参与候选比较。
_SOURCE_ONLY_TAIL_TAGS = ("dist", "broadcast_keyword", "broadcast_sentinel",
                          "shardkey", "exec_partition")


def _tail_comparable(tail_fp):
    """把表尾指纹投影成"候选侧也应当具备"的部分。

    返回 `(本地选项排序元组, 分区形状 | None)`；无法投影返回 None。
    本地选项按排序比较——表选项之间无顺序语义，排序后比较更稳，
    而 O 第十二轮列出的每一种变异（ENGINE/CHARSET/COLLATE/COMMENT/删除全部）
    都会改变多重集合，一样会被抓到。
    """
    if not tail_fp or len(tail_fp) != 3:
        return None
    locals_, part = [], None
    for e in tail_fp[2]:
        tag = e[0] if isinstance(e, tuple) and e else e
        if tag in _SOURCE_ONLY_TAIL_TAGS:
            continue
        if tag == "part":
            if part is not None:
                return None                            # 不可能：计数已保证至多一个
            _t, method, eshape, defs = e
            # 分区选项（ENGINE/COMMENT）是掩码目标 → 只比分区名与 VALUES 边界
            part = (method, eshape, tuple((d[0], d[1]) for d in defs))
            continue
        locals_.append(e)
    return tuple(sorted(locals_)), part


def _ast_head_shape(node):
    """候选 AST 的顶层语义：((schema, table), TEMPORARY, IF NOT EXISTS)。"""
    schema = node.this
    if not isinstance(schema, exp.Schema):
        return None
    t = schema.this
    if t is None:
        return None
    props = node.args.get("properties")
    names = [type(p).__name__ for p in (props.expressions if props else [])]
    return (((getattr(t, "db", "") or "").strip("` ").lower(),
             (getattr(t, "name", "") or "").strip("` ").lower()),
            "TemporaryProperty" in names,
            bool(node.args.get("exists")))


# 候选属性里**不属于表尾**的项：它们在 head 面已单独比较，不能混进 tail 扫描。
_AST_NON_TAIL_PROPERTIES = ("TemporaryProperty",)


def _ast_tail_shape(node, dialect="mysql"):
    """候选 AST 的表尾形状。

    做法与类型规范化同一套路（第十一轮 BLOCK-11-04 的教训）：把候选属性**逐个
    回生成**后拼成一段表尾，再送进**同一个** `_scan_table_tail()`，
    而不是另写一套 property 类名映射。好处是 `CHARSET` / `CHARACTER SET`、
    引号风格、`=` 有无这些差异被同一个消费器自动归一，两侧不可能各自漂移。

    ⚠️ 不能直接用 `node.sql()` 的整句文本：sqlglot 一旦遇到它不认识的表选项
    （`shardkey=`、`STATS_PERSISTENT=` 等），回生成时会把**整组**属性包进
    `WITH ( … )`（实测），tail 扫描随即失败、把合法正例判成不守恒。
    逐属性渲染就没有这个容器。
    """
    props = node.args.get("properties")
    parts = []
    for p in (props.expressions if props else []):
        if type(p).__name__ in _AST_NON_TAIL_PROPERTIES:
            continue
        try:
            txt = p.sql(dialect=dialect)
        except Exception:
            return None
        if txt:
            parts.append(txt)
    stub = "CREATE TABLE `__t__` (`__c__` INT) " + " ".join(parts)
    try:
        toks = sqlglot.Dialect.get_or_raise(dialect).tokenizer_class().tokenize(stub)
    except Exception:
        return None
    open_idx, close_idx, _nm, _head = _tdsql_table_def_bounds(toks)
    if open_idx < 0:
        return None
    _tgt, _msk, fp = _scan_table_tail(toks, close_idx + 1, len(toks))
    if fp is None:
        return None
    return _tail_comparable(fp)


def _validate_recovery_candidate(node, plan):
    """候选 AST 结构守恒门禁：逐字段比较，不再是布尔检查。

    第十二轮 BLOCK-12-04：Rev.M 只比较了定义列表，顶层 CREATE 语义与整个表尾
    都没有进入比较，于是 `CREATE TEMPORARY`→`CREATE`、删 `IF NOT EXISTS`、
    `db1.t`→`db2.t`、`ENGINE=InnoDB`→`MyISAM`、`CHARSET` 改变、表 COMMENT 改写、
    删光全部表选项、分区方法/键/名/边界改变——13 种单点变异**全部返回 True**。
    本版比较 CreateShape 的三个面：head / definitions / tail。
    """
    if plan.get("known_false_negatives"):
        return False                                  # 具名 KFN：计划可达，最终必须失败关闭
    if not isinstance(node, exp.Create):
        return False
    if str(node.args.get("kind") or "").upper() != "TABLE":
        return False
    if not _same_table_name(node, plan["table"]):
        return False
    fpr = plan["fingerprint"]
    # ① head：全限定名 + TEMPORARY + IF NOT EXISTS（都有规则消费者）
    if _ast_head_shape(node) != fpr.get("head"):
        return False
    # ② tail：本地表选项与二级分区细节
    if _ast_tail_shape(node) != _tail_comparable(fpr.get("tail")):
        return False
    schema = node.this
    if not isinstance(schema, exp.Schema):
        return False
    items = list(schema.expressions or [])
    src_defs = fpr["definitions"]
    if len(items) != len(src_defs):
        return False
    for it, src in zip(items, src_defs):
        tag = src[0]
        if tag == "col":
            if not isinstance(it, exp.ColumnDef):
                return False
            got = _ast_column_shape(it)
            if got is None:
                return False
            _, s_name, s_type, s_cons = src
            g_name, g_type, g_cons = got
            if g_name != s_name or g_type != s_type:
                return False
            def _norm(cs):
                return tuple(sorted((k, v) for k, v in cs
                                    if k not in _GATE_IGNORED_COL_CONSTRAINTS))
            if _norm(s_cons) != _norm(g_cons):
                return False                           # 列约束守恒
        else:
            if isinstance(it, exp.ColumnDef):
                return False
            off = 1 if tag == "constraint" else 0
            if tag == "constraint":
                # 第十二轮 MAJOR-12-01：官方 `[CONSTRAINT [symbol]] PRIMARY KEY (...)`
                # 在候选里是 `exp.Constraint(this=symbol, expressions=[PrimaryKey])`。
                # Rev.M 把它直接丢给只认 PrimaryKey/Unique/Index 的形状提取器，
                # 必然返回 None，于是这条**官方合法**语句被系统性误杀。
                if not isinstance(it, exp.Constraint):
                    return False
                inner = list(it.args.get("expressions") or [])
                primaries = [x for x in inner if isinstance(x, exp.PrimaryKey)]
                comments = [x for x in inner if type(x).__name__ == "CommentColumnConstraint"]
                if len(primaries) != 1 or len(inner) != len(primaries) + len(comments):
                    return False
                if (getattr(it.this, "name", "") or "").strip("` ").lower() != src[6]:
                    return False                       # constraint symbol 守恒
                # PRIMARY COMMENT 是批准掩码目标：候选通常没有 COMMENT；若 sqlglot
                # 某版本仍把 COMMENT 放在 Constraint wrapper，只允许源侧确实存在时出现。
                if comments and "COMMENT" not in src[5]:
                    return False
                it = primaries[0]
            elif isinstance(it, exp.Constraint):
                return False                           # 源侧不是具名约束，候选却是
            got = _ast_index_shape(it)
            if got is None:
                return False
            s_kind, s_name, s_parts, s_opts = src[1 + off], src[2 + off], src[3 + off], src[4 + off]
            g_kind, g_name, g_parts, g_opts = got
            if g_kind != s_kind:
                return False                           # 索引 kind 守恒
            if s_kind != "PRIMARY" and g_name != s_name:
                return False                           # 索引名守恒
            if tuple((p[0], p[1]) for p in s_parts) != g_parts:
                return False                           # 键列与前缀长度守恒
            if tuple(o for o in s_opts if o not in _GATE_IGNORED_INDEX_OPTS) != g_opts:
                return False                           # USING 守恒
    if plan["had_partition"]:
        props = node.args.get("properties")
        names = [type(p).__name__ for p in (props.expressions if props else [])]
        if sum(1 for nm in names if nm.startswith("PartitionBy")) != 1:
            return False
    return True


@dataclass
class ParsedSQL:
    """解析后的SQL结构（V1.0 完整字段）"""
    # === 基础信息 ===
    raw_sql: str = ""
    sql_type: str = ""  # SELECT/INSERT/UPDATE/DELETE/CREATE/ALTER/DROP/LOAD/HANDLER/FLUSH/LOCK
    tables: list[str] = field(default_factory=list)
    select_fields: list[str] = field(default_factory=list)

    # === DDL结构信息 ===
    is_create_table: bool = False
    is_alter_table: bool = False
    has_primary_key: bool = False
    has_foreign_key: bool = False
    engine: Optional[str] = None
    charset: Optional[str] = None
    columns: list[dict] = field(default_factory=list)
    column_types: list[dict] = field(default_factory=list)
    indexes: list[dict] = field(default_factory=list)
    # v1.6.2.2 / Rev.P：完整 UNIQUE 语义的隔离通道。不得无评审地改让
    # R077/R061 等 legacy 消费者读取它；本期唯一消费者是 R054 助手。
    unique_constraints: list[dict] = field(default_factory=list)
    unique_constraints_complete: bool = False
    # Rev.Q：source definition scanner 是否完整理解了每一个顶层定义项。
    # 该字段只控制 UNIQUE 结构化通道能否宣称 complete；False 不是语法错误。
    unique_source_definitions_complete: bool = False
    known_fidelity_failures: tuple[str, ...] = field(default_factory=tuple)
    table_options: dict = field(default_factory=dict)
    has_table_comment: bool = False
    column_comments: dict[str, str] = field(default_factory=dict)
    index_definitions: list[dict] = field(default_factory=list)
    is_create_table_select: bool = False
    is_temporary_table: bool = False
    has_drop_database: bool = False
    alter_actions: list[dict] = field(default_factory=list)
    # v1.6.3.2 / REQ-01A：ALTER ADD/MODIFY/CHANGE 的列类型只读通道。
    # 与 CREATE 的 column_types 同制（name/type/raw_type/length），另带 operation。
    # 解析失败时为空集合（保留 E999），不得用全文正则猜测类型。
    alter_column_types: list[dict] = field(default_factory=list)
    # v1.6.3.2 / REQ-06：UPDATE/DELETE 顶层 LIMIT 的结构化事实。
    # {present, row_count, offset, parameterized, verifiable}。
    # 只消费顶层 Limit 节点；SELECT 子查询内部的 LIMIT 不属于外层 DML 上限。
    dml_limit: dict = field(default_factory=dict)
    # v1.6.3.2 / REQ-07：二级分区策略事实，独立于 sqlglot AST 成败。
    # {has_definition, method, maxvalue_partitions, source_context}；
    # 由单次预检词法化产出，CREATE/ALTER_ADD/ALTER_REORGANIZE 三条出口均保留。
    secondary_partition: dict = field(default_factory=dict)

    # === DML结构信息 ===
    has_wildcard_select: bool = False
    where_clause: Optional[str] = None
    has_where: bool = False
    where_columns: list[str] = field(default_factory=list)
    where_has_function: bool = False
    has_order_by: bool = False
    order_by_random: bool = False
    subquery_depth: int = 0
    join_count: int = 0
    has_explicit_join: bool = False
    has_into_outfile: bool = False
    has_index_hint: bool = False
    has_for_update: bool = False
    has_lock_tables: bool = False
    or_in_where: bool = False
    in_list_size: int = 0
    limit_offset: int = -1
    has_delayed_keyword: bool = False
    is_multi_table_update: bool = False
    has_load_data: bool = False
    has_handler_do: bool = False
    has_flush: bool = False
    has_unnamed_insert: bool = False
    insert_columns: list[str] = field(default_factory=list)
    where_has_not_equal: bool = False
    has_hint: bool = False

    # === 命名信息 ===
    table_name_plural: bool = False

    # === 分布式信息（需元数据增强） ===
    shardkey_in_where: bool = False
    shardkey_in_insert: bool = False
    shardkey_in_orderby: bool = False

    # === 事务信息 ===
    is_begin: bool = False
    is_commit: bool = False
    is_rollback: bool = False
    transaction_sql_count: int = 0

    # === 解析元信息 ===
    parse_error: Optional[str] = None
    ast: Optional[object] = None


class SQLParser:
    """SQL解析器（V1.0）"""

    # 常见英语复数后缀
    PLURAL_SUFFIXES = ("s", "es", "ies", "ses")
    # 需要忽略复数检查的词（本身以s结尾但非复数）
    PLURAL_IGNORE = {"status", "process", "address", "access", "class", "glass", "gas", "bus", "plus", "this", "news", "series", "species"}

    def __init__(self, dialect: str = "mysql"):
        self.dialect = dialect

    def parse(self, sql: str) -> ParsedSQL:
        """解析SQL语句，返回结构化的 ParsedSQL 对象。"""
        parsed = ParsedSQL(raw_sql=sql.strip())
        sql_clean = sql.strip().rstrip(";")
        # 第十二轮 BLOCK-12-02：恢复链必须拿到**未被 rstrip(";") 处理过**的同一原串。
        # Rev.M 把 `sql_clean` 传给 `_plan_recovery()`，于是 `_strip_terminal_semicolon()`
        # 声明的"至多一个终止分号"在真实调用链上不可达——`;;`、`;;;`、`; ;` 都会
        # 先被 rstrip 抹平，再被规划器当成合法单语句接受并恢复成 Create。
        # 全部 span 都相对 `sql_recover` 计算，与 `_blank_spans()`/`_spans_only_diff()`
        # 共用同一个字符串，不存在"先改长度再套旧偏移"的问题。
        sql_recover = sql.strip()
        # v1.6.2.2-A-VERIFY-6.2：MySQL 8.0 索引列 ASC/DESC 修饰在 sqlglot 各版本均解析失败，
        # 纯文本预处理剥离（仅 DDL、跳过 CTAS、字符串/注释外）；sql_clean 与 sql_recover 同步
        # 处理，保证恢复链 span 与解析输入同源。raw_sql 保持原文（R077/R054 提取分片键不受影响）。
        sql_clean = _strip_index_order_modifiers(sql_clean)
        sql_recover = _strip_index_order_modifiers(sql_recover)

        # 先做正则级别的快速检测（补充sqlglot可能遗漏的信息）
        parsed = self._regex_pre_parse(sql_clean, parsed)

        # Rev.Q：KFN 与全表定义完整性同源但不互相吞没；只词法化一次。
        # v1.6.3.2 / Rev.C：status 函数返回三元组；二级分区策略事实在
        # sqlglot AST try/except **之前**写入 parsed，因此正常 AST、Command
        # 降级、ParseError 提前返回三条出口都保留该事实（严禁放在重试条件内）。
        (parsed.known_fidelity_failures,
         parsed.unique_source_definitions_complete,
         parsed.secondary_partition) = (
            _preflight_create_definition_status(sql_recover, self.dialect)
        )

        # 尝试解析SQL
        try:
            ast = sqlglot.parse_one(sql_clean, read=self.dialect)
            # v1.6.2.0: TDSQL 方言尾子句会让 sqlglot 把整条语句降级为 Command，
            # 导致 columns/indexes/table_options 全空、结构类规则静默漏审。
            # 仅在"确实已降级"且"语句含方言子句"时，剥离该子句重试一次；
            # 且只有重试确实产出非 Command 节点才采用其结果。
            # 正常解析的语句不会进入本分支，故对既有行为的影响可证明为零；
            # 重试失败时保留原 Command 结果，不劣于改前。
            # 注意: parsed.raw_sql 始终保持原文——R077/R054 依赖它提取分片键。
            if isinstance(ast, exp.Command):
                # v1.6.2.2 / BLOCK-C1+D1+D2: 原实现对整条 SQL 做
                # _TDSQL_DIALECT_RE.sub()，不感知 token 作用域，会删掉名为
                # broadcast 的列、篡改注释里的片段，且改坏后仍能解析成同表名
                # Create，形成静默错误 AST。改用严格的 token 级尾子句剥离器，
                # 并要求候选必须是同表名的 CREATE TABLE（不接纳 Block 等节点）。
                # Rev.I：改用统一规划器——一次性按 TDSQL 官方语法验证**整条语句**
                # （定义列表 + 表尾），再决定是否改写。
                # Rev.J：规划器返回 None 即"无法证明整条语句合规"或"无主目标"，
                # 一律不恢复（第九轮 BLOCK-X3）。
                _plan2 = _plan_recovery(sql_recover, self.dialect)
                if _plan2 is not None:
                    _all2 = _plan2["primary_spans"] + _plan2["auxiliary_spans"]
                    _t_sql = _blank_spans(sql_recover, _all2)
                    if (_t_sql is not None
                            and _spans_only_diff(sql_recover, _t_sql, _all2)):
                        try:
                            _retry_ast = sqlglot.parse_one(_t_sql, read=self.dialect)
                        except Exception:
                            _retry_ast = None
                        if _validate_recovery_candidate(_retry_ast, _plan2):
                            ast = _retry_ast
                # v1.6.3.2 / §4.7.5：仅针对 CREATE 来源的兜底。实测（sqlglot
                # 30.14.0，SIT DEF-SIT-02 复核）CREATE 的 bare MAXVALUE 是真实
                # ParseError（ast=None）、括号形态正常产出 Create，两者都不会
                # 落到本分支；本分支只在将来 sqlglot 改变 CREATE 降级行为时才
                # 生效。ALTER REORGANIZE 的 Command 是该语法的**正常降级形态、
                # 不是缺陷**，不得据此合成 parse_error——否则合法 DDL 在集中式
                # 实例上凭空多出一条 ERROR 级 E999，strict/normal 双门禁全卡。
                if (isinstance(ast, exp.Command)
                        and parsed.secondary_partition.get("source_context") == "CREATE"
                        and parsed.secondary_partition.get("maxvalue_partitions")):
                    parsed.parse_error = (
                        "KNOWN_FIDELITY_GAP[SECONDARY-PARTITION-MAXVALUE]: "
                        "二级分区 MAXVALUE 形态无法恢复为结构化 AST（sqlglot 降级为 Command）")
            parsed.ast = ast
        except (SqlglotError, Exception) as e:
            # v1.6.2.2 / DEF-2: UNIQUE 索引带 COMMENT 会让 sqlglot 抛 ParseError，
            # 整条语句结构信息全丢，R003/R004/R005/R028 集体误报。
            # 恢复链共两阶段，**两阶段都是 token 级剥离并各自返回 span**：
            #   阶段一：剥离 UNIQUE 索引 COMMENT
            #   阶段二：若仍降级为 Command，再剥离 TDSQL 方言尾子句
            # 最终以「原文 → 最终 SQL 的全部差异必须落在两阶段 span 并集内」
            # 作联合门禁（BLOCK-C1 要求）；任一环节不满足即沿用原异常，
            # 下方失败路径与改前逐字一致。
            # Rev.I：单一规划器取代 Rev.H 的两阶段串联。
            # 第八轮 BLOCK-H1：Rev.H 的 UNIQUE 单独恢复路径**根本不验证表尾**，
            # 于是 ENGINE=123 / 孤立 DEFAULT / PARTITION BY RANGE(,) 这些与目标
            # 无关的非法结构被 sqlglot 静默丢弃后仍返回 Create，原 E999 消失。
            # 现在无论走哪条路径，都必须先让 _plan_recovery() 按 TDSQL 官方语法
            # 验证整条语句，再由 _validate_recovery_candidate() 校验候选 AST
            # 未丢结构。三类 span（UNIQUE COMMENT / 方言声明 / 官方语法掩码）
            # 一次性置空，联合做逐字符 span 门禁。
            _retry_ast = None
            _plan = _plan_recovery(sql_recover, self.dialect)
            if _plan is not None:
                _all_spans = _plan["primary_spans"] + _plan["auxiliary_spans"]
                _final_sql = _blank_spans(sql_recover, _all_spans)
                if (_final_sql is not None
                        and _spans_only_diff(sql_recover, _final_sql, _all_spans)):
                    try:
                        _cand = sqlglot.parse_one(_final_sql, read=self.dialect)
                    except Exception:
                        _cand = None
                    if _validate_recovery_candidate(_cand, _plan):
                        _retry_ast = _cand
            if _retry_ast is not None:
                # 必须同时重绑局部变量 ast——下方通用流程（_get_sql_type/_parse_create/
                # _parse_common）直接引用 ast，只赋 parsed.ast 会 UnboundLocalError。
                ast = _retry_ast
                parsed.ast = ast
            else:
                # v1.6.2.2-UAT-O-01-R2：异常路径同样完成 KFN 消息归一化——
                # preflight 已把 known_fidelity_failures 写入 parsed（决策真值源），
                # 但旧实现在此提前 return，parse_error 只有普通异常文本，
                # 缺少 KNOWN_FIDELITY_GAP marker，导致下游仅凭消息判定时漏判。
                # 归一化时保留原始异常文本，不降低可诊断性（结构化类别负责决策，
                # 消息负责展示——与 2254 行正常出口的归一化同制）。
                if parsed.known_fidelity_failures:
                    parsed.parse_error = "KNOWN_FIDELITY_GAP[%s]: %s" % (
                        ",".join(parsed.known_fidelity_failures), e)
                else:
                    parsed.parse_error = str(e)
                parsed.sql_type = self._detect_sql_type_regex(sql_clean)
                # v1.6.3.2 / REQ-06：ParseError 出口无可靠 AST，DML LIMIT 走
                # token 回退（忽略注释/字符串/括号内 LIMIT）。
                if parsed.sql_type in ("UPDATE", "DELETE"):
                    parsed.dml_limit = self._extract_dml_limit(None, sql_recover)
                # 正则回退提取表名（防止含中划线等语法不合规表名在解析报错时漏检）；
                # v1.6.2.2-A-VERIFY-6.1：先剥离注释，避免从注释头提取出 `--` 残片当表名。
                tbl_match = re.search(r'\b(?:create\s+table|alter\s+table|drop\s+table|truncate\s+table|from|into|update)\s+(?:if\s+(?:not\s+)?exists\s+)?([`\'"]?[a-zA-Z0-9_\-]+[`\'"]?)', _strip_comments_for_fallback(sql_clean), re.IGNORECASE)
                if tbl_match:
                    tb_name = tbl_match.group(1).strip("`\"' ")
                    if tb_name and tb_name.lower() not in ("table", "if", "exists"):
                        parsed.tables.append(tb_name)
                        if "create table" in sql_clean.lower():
                            parsed.is_create_table = True
                return parsed

        # 确定 SQL 类型
        parsed.sql_type = self._get_sql_type(ast)
        # v1.6.3.2 / REQ-06：UPDATE/DELETE 顶层 LIMIT 结构化事实（AST 优先；
        # 只读顶层 Limit 节点，SELECT 子查询内部的 LIMIT 不属于外层 DML 上限）。
        if parsed.sql_type in ("UPDATE", "DELETE"):
            parsed.dml_limit = self._extract_dml_limit(ast, sql_recover)

        # 根据SQL类型分别解析
        if isinstance(ast, exp.Select):
            self._parse_select(ast, parsed)
        elif isinstance(ast, exp.Insert):
            self._parse_insert(ast, parsed)
        elif isinstance(ast, exp.Update):
            self._parse_update(ast, parsed)
        elif isinstance(ast, exp.Delete):
            self._parse_delete(ast, parsed)
        elif isinstance(ast, exp.Create):
            self._parse_create(ast, parsed)
        elif isinstance(ast, exp.Alter):
            self._parse_alter(ast, parsed)
        elif isinstance(ast, exp.Drop):
            self._parse_drop(ast, parsed)

        # 通用解析
        self._parse_common(ast, parsed)

        # 提取表名及 DDL 属性（如果各类型解析未提取到，或为 sqlglot Command 降级节点）
        if not parsed.tables or isinstance(ast, exp.Command) or parsed.sql_type == "UNKNOWN":
            if parsed.sql_type == "UNKNOWN":
                parsed.sql_type = self._detect_sql_type_regex(sql_clean)
            parsed.tables = self._extract_tables(ast)
            if not parsed.tables:
                # v1.6.2.2-A-VERIFY-6.1：同上——降级提取前先剥离注释，
                # 避免把 `-- SQL Object:` 注释头内的残片当表名。
                tbl_match = re.search(r'\b(?:create\s+table|alter\s+table|drop\s+table|truncate\s+table|from|into|update)\s+(?:if\s+(?:not\s+)?exists\s+)?([`\'"]?[a-zA-Z0-9_\-]+[`\'"]?)', _strip_comments_for_fallback(sql_clean), re.IGNORECASE)
                if tbl_match:
                    tb_name = tbl_match.group(1).strip("`\"' ")
                    if tb_name and tb_name.lower() not in ("table", "if", "exists"):
                        parsed.tables.append(tb_name)
            self._regex_fallback_create_table_props(sql_clean, parsed)

        if parsed.known_fidelity_failures:
            parsed.parse_error = "KNOWN_FIDELITY_GAP[%s]" % ",".join(
                parsed.known_fidelity_failures)
        return parsed

    # ── 正则预解析（补充sqlglot遗漏的信息） ──────────────────

    def _regex_pre_parse(self, sql: str, parsed: ParsedSQL) -> ParsedSQL:
        """用正则做快速预解析，补充sqlglot可能遗漏的信息"""
        sql_lower = sql.lower()

        # 检测 DELAYED / LOW_PRIORITY
        if re.search(r"\b(delayed|low_priority)\b", sql_lower):
            parsed.has_delayed_keyword = True

        # 检测 INTO OUTFILE
        if "into outfile" in sql_lower or "into dumpfile" in sql_lower:
            parsed.has_into_outfile = True

        # 检测 LOAD DATA / LOAD XML
        # v1.6.2.2-UAT-O-09：改用 sqlglot 词法器取语句头判定——词法器完整处理
        # `#`/`--`/`/* */` 注释与引号/反引号字符串，注释与字面量内容不会进入词序列。
        # 词法化失败（如未闭合字符串）时 has_load_data=False（失败关闭）：
        # 此时语句必有 parse_error，E999 兑底，不会被 LOAD 豁免漏放。
        _head = _lex_head_words(sql_lower, self.dialect)
        if _is_load_statement_head(_head):
            parsed.has_load_data = True
            if parsed.sql_type == "UNKNOWN":
                parsed.sql_type = "LOAD"

        # 检测 HANDLER ... OPEN/READ/CLOSE
        if re.match(r"\bhandler\b", sql_lower):
            parsed.has_handler_do = True

        # 检测 FLUSH
        if re.match(r"\bflush\b", sql_lower):
            parsed.has_flush = True

        # 检测 LOCK TABLES / UNLOCK TABLES
        if re.match(r"\block\s+tables\b", sql_lower):
            parsed.has_lock_tables = True

        # 检测 FOR UPDATE / FOR SHARE
        if "for update" in sql_lower or "for share" in sql_lower:
            parsed.has_for_update = True

        # 检测 DROP DATABASE
        if re.match(r"\bdrop\s+(database|schema)\b", sql_lower):
            parsed.has_drop_database = True

        # 检测 IN 列表大小
        in_match = re.findall(r"\bin\s*\(([^)]+)\)", sql_lower)
        for m in in_match:
            count = len([x for x in m.split(",") if x.strip()])
            if count > parsed.in_list_size:
                parsed.in_list_size = count

        # 检测 LIMIT offset
        limit_match = re.search(r"\blimit\s+(\d+)\s*,\s*(\d+)", sql_lower)
        if limit_match:
            parsed.limit_offset = int(limit_match.group(1))
        else:
            limit_offset_match = re.search(r"\blimit\s+(\d+)\s+offset\s+(\d+)", sql_lower)
            if limit_offset_match:
                parsed.limit_offset = int(limit_offset_match.group(2))

        # 检测 BEGIN / COMMIT / ROLLBACK
        if re.match(r"\b(begin|start\s+transaction)\b", sql_lower):
            parsed.is_begin = True
        if re.match(r"\bcommit\b", sql_lower):
            parsed.is_commit = True
        if re.match(r"\brollback\b", sql_lower):
            parsed.is_rollback = True

        # 检测 WHERE 中的 OR
        if " where " in sql_lower:
            where_part = sql_lower.split(" where ")[1].split(" group by ")[0].split(" order by ")[0].split(" limit ")[0]
            if re.search(r"\bor\b", where_part):
                parsed.or_in_where = True
            # 检测 != / <>
            if "!=" in where_part or "<>" in where_part or "is not null" in where_part or "is null" in where_part:
                parsed.where_has_not_equal = True

        # 检测 TEMPORARY（含Oracle GTT: CREATE GLOBAL TEMPORARY TABLE）
        if re.match(r"\bcreate\s+(global\s+)?temporary\s+table\b", sql_lower):
            parsed.is_temporary_table = True
        # Oracle GTT ON COMMIT DELETE|PRESERVE ROWS
        if re.search(r"\bon\s+commit\s+(delete|preserve)\s+rows\b", sql_lower):
            parsed.is_temporary_table = True

        # 检测 CREATE TABLE ... SELECT
        if re.match(r"\bcreate\s+(temporary\s+)?table\b.*\b(as\s+)?select\b", sql_lower):
            parsed.is_create_table_select = True

        # 检测联表更新 / 联表删除
        clean_sql_no_comm = re.sub(r'--[^\n]*', '', sql_lower)
        clean_sql_no_comm = re.sub(r'/\*.*?\*/', '', clean_sql_no_comm, flags=re.DOTALL).strip()
        # UPDATE：取 UPDATE 与 SET 之间的目标表段，段内含逗号或 JOIN 即联表。
        # 旧写法 [^set]+ 是否定"字符"组（排除字母 s/e/t），并非排除单词 SET——
        # 对 t_xxx 等含 s/e/t 的表名恒不匹配，导致逗号式联表 UPDATE 静默漏报（P2-03）。
        # 同时旧 JOIN 分支会把 SET 子句里子查询的 JOIN 误判进来，限定目标段后一并修正。
        m_upd = re.search(r"\bupdate\b(.*?)\bset\b", clean_sql_no_comm, re.DOTALL)
        upd_multi = bool(m_upd and ("," in m_upd.group(1)
                                    or re.search(r"\bjoin\b", m_upd.group(1))))
        # DELETE：同样只看目标段（DELETE 到第一个 WHERE 之间），理由与 UPDATE 一致。
        # 旧写法 `\bdelete\s+.*?\bjoin\b` 满句扫，会把 WHERE 子查询里的 JOIN
        # 误判成联表 DELETE —— `DELETE FROM t WHERE id IN (SELECT .. JOIN ..)`
        # 是合法单表删除，却命中 ERROR 级 R043，并被质量门禁（ERROR 阈值默认 0）
        # 挡住合法变更。该误报为 v1.2.0.9 之后引入，升级会带给内网。
        m_del = re.search(r"\bdelete\b(.*?)(?:\bwhere\b|$)", clean_sql_no_comm, re.DOTALL)
        del_seg = m_del.group(1) if m_del else ""
        # 先剥掉 DELETE 的合法修饰词，否则它们会被下面的"别名列表"正则当成别名：
        # `DELETE LOW_PRIORITY FROM t` 的目标段是 ` low_priority from t`，
        # 形态与 `DELETE a FROM t` 完全一致，会把单表删除误判成联表（O 复核发现）。
        # 只剥段首连续出现的修饰词，不碰后面的表名（表名可以叫 low_priority）。
        del_seg = re.sub(r"^\s*(?:(?:low_priority|quick|ignore)\s+)+", " ", del_seg)
        del_multi = bool(del_seg and (
            re.search(r"\bjoin\b", del_seg)                         # DELETE a FROM t1 JOIN t2
            or re.search(r"\busing\b", del_seg)                     # DELETE FROM t1,t2 USING ...
            # DELETE a, b FROM ...：FROM 之前必须先出现【标识符】。
            # 不能写成 [\w`\s,]+ —— 空白也在字符组里，` from t` 的前导空格
            # 自身即可满足 +，会把普通单表 DELETE FROM 全部误判成联表。
            or re.search(r"^\s*[a-zA-Z0-9_`][a-zA-Z0-9_`\s,]*\bfrom\b", del_seg)
        ))
        if upd_multi or del_multi:
            parsed.is_multi_table_update = True

        # 检测 INDEX HINT (USE INDEX / FORCE INDEX / IGNORE INDEX)
        if re.search(r"\b(use|force|ignore)\s+index\b", sql_lower):
            parsed.has_index_hint = True

        # 检测 SQL hint
        if re.search(r"\b(sql_no_cache|sql_calc_found_rows|sql_buffer_result)\b", sql_lower):
            parsed.has_hint = True

        return parsed

    def _detect_sql_type_regex(self, sql: str) -> str:
        """正则检测SQL类型（解析失败时的回退方案）"""
        clean_sql = re.sub(r'--[^\n]*', '', sql)
        clean_sql = re.sub(r'/\*.*?\*/', '', clean_sql, flags=re.DOTALL).strip()
        sql_upper = clean_sql.upper()
        if "CREATE TABLE" in sql_upper:
            return "CREATE TABLE"
        if "CREATE PROCEDURE" in sql_upper:
            return "CREATE PROCEDURE"
        if "CREATE TRIGGER" in sql_upper:
            return "CREATE TRIGGER"
        if "CREATE VIEW" in sql_upper:
            return "CREATE VIEW"
        if "CREATE FUNCTION" in sql_upper:
            return "CREATE FUNCTION"
        for keyword in ("SELECT", "INSERT", "REPLACE", "UPDATE", "DELETE",
                        "CREATE", "ALTER", "DROP", "LOAD", "HANDLER", "FLUSH",
                        "LOCK", "UNLOCK", "BEGIN", "START", "COMMIT", "ROLLBACK",
                        "GRANT", "REVOKE", "TRUNCATE"):
            if sql_upper.startswith(keyword) or f"\n{keyword}" in sql_upper or f" {keyword} " in sql_upper:
                return keyword
        return "UNKNOWN"

    def _regex_fallback_create_table_props(self, sql: str, parsed: ParsedSQL):
        """当 AST 解析失败或回退时，通过正则预防护航 DDL 关键属性（主键/引擎/字符集/注释）"""
        clean = re.sub(r'--[^\n]*', '', sql)
        clean = re.sub(r'/\*.*?\*/', '', clean, flags=re.DOTALL).strip()
        clean_lower = clean.lower()
        if "create table" in clean_lower:
            parsed.is_create_table = True
            if parsed.sql_type == "UNKNOWN":
                parsed.sql_type = "CREATE TABLE"
            # 提取主键
            if re.search(r'\bprimary\s+key\b', clean, re.IGNORECASE):
                parsed.has_primary_key = True
            # 提取引擎
            eng_m = re.search(r'\bengine\s*=\s*([a-zA-Z0-9_]+)\b', clean, re.IGNORECASE)
            if eng_m:
                parsed.engine = eng_m.group(1)
            # 提取字符集
            cs_m = re.search(r'\b(?:default\s+)?charset\s*=\s*([a-zA-Z0-9_]+)\b', clean, re.IGNORECASE)
            if cs_m:
                parsed.charset = cs_m.group(1)
            # 提取表级注释
            if re.search(r'\bcomment\s*=\s*[\'"]', clean, re.IGNORECASE):
                parsed.has_table_comment = True

    def _get_sql_type(self, ast) -> str:
        """从AST获取SQL类型"""
        if isinstance(ast, exp.Select):
            return "SELECT"
        elif isinstance(ast, exp.Insert):
            kind = ast.args.get("kind", "")
            return "REPLACE" if kind == "REPLACE" else "INSERT"
        elif isinstance(ast, exp.Update):
            return "UPDATE"
        elif isinstance(ast, exp.Delete):
            return "DELETE"
        elif isinstance(ast, exp.Create):
            kind = ast.args.get("kind", "")
            return f"CREATE {kind}".strip().upper() if kind else "CREATE"
        elif isinstance(ast, exp.Alter):
            return "ALTER"
        elif isinstance(ast, exp.Drop):
            return "DROP"
        return "UNKNOWN"

    # ── SELECT 解析 ──────────────────────────────────────

    def _parse_select(self, ast: exp.Select, parsed: ParsedSQL):
        """解析 SELECT 语句"""
        parsed.tables = self._extract_tables(ast)

        parsed.select_fields = []
        for e in ast.expressions:
            if isinstance(e, exp.Star):
                parsed.has_wildcard_select = True
                parsed.select_fields.append("*")
            else:
                parsed.select_fields.append(e.sql(dialect=self.dialect))

        where = ast.args.get("where")
        if where:
            parsed.has_where = True
            parsed.where_clause = where.sql(dialect=self.dialect)
            parsed.where_columns = self._extract_where_columns(where)
            parsed.where_has_function = self._check_where_has_function(where)

        order = ast.args.get("order")
        if order:
            parsed.has_order_by = True
            parsed.order_by_random = self._check_order_by_random(order)

        parsed.subquery_depth = self._calc_subquery_depth(ast)
        parsed.join_count = self._count_joins(ast)
        if parsed.join_count > 0:
            parsed.has_explicit_join = True

    # ── INSERT 解析 ──────────────────────────────────────

    def _parse_insert(self, ast: exp.Insert, parsed: ParsedSQL):
        """解析 INSERT 语句"""
        target = ast.args.get("this")
        if target:
            if isinstance(target, exp.Schema):
                table_obj = target.this
                if table_obj:
                    table_name = table_obj.sql(dialect=self.dialect)
                    parsed.tables.append(table_name)
                # 提取INSERT列名
                for col_expr in target.expressions:
                    if isinstance(col_expr, exp.ColumnDef):
                        parsed.insert_columns.append(col_expr.name)
                    elif isinstance(col_expr, exp.Identifier):
                        parsed.insert_columns.append(col_expr.name)
                    elif isinstance(col_expr, exp.Column):
                        parsed.insert_columns.append(col_expr.name)
                # 如果Schema有expressions但是没有提取到列名
                if not parsed.insert_columns and target.expressions:
                    for expr in target.expressions:
                        name = expr.name if hasattr(expr, 'name') else str(expr)
                        if name and name not in parsed.insert_columns:
                            parsed.insert_columns.append(name)
            else:
                parsed.tables.append(target.sql(dialect=self.dialect))

        # 如果没有提取到列名，标记为 unnamed insert
        if not parsed.insert_columns and target and not isinstance(target, exp.Schema):
            parsed.has_unnamed_insert = True
        elif isinstance(target, exp.Schema) and not target.expressions:
            parsed.has_unnamed_insert = True

        # INSERT ... SELECT
        select = ast.args.get("expression")
        if isinstance(select, exp.Select):
            self._parse_select(select, parsed)
            target_name = target.sql(dialect=self.dialect) if target else ""
            if target_name and target_name not in parsed.tables:
                parsed.tables.insert(0, target_name)

    # ── UPDATE 解析 ──────────────────────────────────────

    def _parse_update(self, ast: exp.Update, parsed: ParsedSQL):
        """解析 UPDATE 语句"""
        parsed.tables = self._extract_tables(ast)
        where = ast.args.get("where")
        if where:
            parsed.has_where = True
            parsed.where_clause = where.sql(dialect=self.dialect)
            parsed.where_columns = self._extract_where_columns(where)
            parsed.where_has_function = self._check_where_has_function(where)

    # ── DELETE 解析 ──────────────────────────────────────

    def _parse_delete(self, ast: exp.Delete, parsed: ParsedSQL):
        """解析 DELETE 语句"""
        parsed.tables = self._extract_tables(ast)
        where = ast.args.get("where")
        if where:
            parsed.has_where = True
            parsed.where_clause = where.sql(dialect=self.dialect)
            parsed.where_columns = self._extract_where_columns(where)
            parsed.where_has_function = self._check_where_has_function(where)

    # ── CREATE TABLE 解析 ────────────────────────────────

    def _parse_create(self, ast: exp.Create, parsed: ParsedSQL):
        """解析 CREATE TABLE 语句"""
        parsed.is_create_table = True

        schema = ast.args.get("this")

        # 提取表名
        table_name = ""
        if isinstance(schema, exp.Schema):
            table_obj = schema.this
            if table_obj:
                table_name = table_obj.sql(dialect=self.dialect)
                parsed.tables.append(table_name)
        elif schema:
            table_name = schema.sql(dialect=self.dialect)
            parsed.tables.append(table_name)

        # 复数检查
        if table_name:
            parsed.table_name_plural = self._check_plural(table_name)

        # 解析列定义和索引定义
        _unique_semantics_failed = False
        _unique_ast_incomplete = False
        if isinstance(schema, exp.Schema):
            for col_def in schema.expressions:
                if isinstance(col_def, exp.ColumnDef):
                    col_info = self._parse_column_def(col_def)
                    parsed.columns.append(col_info)
                    parsed.column_types.append({
                        "name": col_info["name"],
                        "type": col_info["type"],
                        "raw_type": col_info["raw_type"],
                    })
                    # 提取列注释
                    comment = self._extract_column_comment(col_def)
                    if comment:
                        parsed.column_comments[col_info["name"]] = comment
                    # Rev.P：列级 UNIQUE 进入隔离语义通道，绝不写 legacy indexes。
                    col_unique = self._parse_column_unique_constraint(col_def)
                    if col_unique is None:
                        pass                          # 本列无 UNIQUE
                    elif col_unique:
                        parsed.unique_constraints.append(col_unique)
                    else:
                        _unique_semantics_failed = True
                elif isinstance(col_def, exp.PrimaryKey):
                    parsed.has_primary_key = True
                elif isinstance(col_def, exp.IndexColumnConstraint):
                    idx_info = self._parse_index_constraint(col_def)
                    if idx_info:
                        parsed.indexes.append(idx_info)
                        parsed.index_definitions.append(idx_info)
                elif type(col_def).__name__ == "UniqueColumnConstraint":
                    # Rev.P：表级 UNIQUE 进入隔离语义通道；提取失败即保持 incomplete。
                    idx_info = self._parse_unique_constraint(col_def)
                    if idx_info:
                        parsed.unique_constraints.append(idx_info)
                    else:
                        _unique_semantics_failed = True
                # 检查表级 COMMENT
                elif type(col_def).__name__ in ("CommentColumnConstraint", "CommentColumnConstraint"):
                    parsed.has_table_comment = True
                else:
                    # Rev.Q：exp.Constraint、ForeignKey、Check 以及未来未知顶层节点
                    # 都证明 AST UNIQUE 遍历不是闭世界；只关闭 complete 并保留 raw
                    # R054 回退，不能把“扫描器尚未覆盖”偷换成 SQL 非法或 E999。
                    _unique_ast_incomplete = True

        if isinstance(schema, exp.Schema):
            if _unique_semantics_failed:
                parsed.parse_error = (
                    parsed.parse_error or "UNIQUE_SEMANTICS_INCOMPLETE"
                )
            parsed.unique_constraints_complete = (
                parsed.unique_source_definitions_complete
                and not _unique_ast_incomplete
                and not _unique_semantics_failed
                and not parsed.known_fidelity_failures
            )

        # 检查约束中的主键和外键
        if isinstance(schema, exp.Schema):
            for pk in schema.find_all(exp.PrimaryKey):
                parsed.has_primary_key = True
            for fk in schema.find_all(exp.ForeignKey):
                parsed.has_foreign_key = True

        # 检查列定义中的主键标记
        for col in parsed.columns:
            if col.get("is_primary_key"):
                parsed.has_primary_key = True

        # 解析表选项 (ENGINE, CHARSET, COMMENT 等)
        properties = ast.args.get("properties")
        if properties:
            self._parse_table_properties(properties, parsed)

        # 检查表级COMMENT（如果table_options或properties中存在）
        if not parsed.has_table_comment and properties and hasattr(properties, "expressions"):
            for prop in properties.expressions:
                try:
                    if isinstance(prop, exp.Property) and getattr(prop, "name", "").upper() == "COMMENT":
                        parsed.has_table_comment = True
                        break
                except Exception:
                    continue

    def _parse_index_constraint(self, col_def) -> dict:
        """解析 IndexColumnConstraint"""
        idx_name_node = col_def.args.get("this")
        idx_name = idx_name_node.sql(dialect=self.dialect) if idx_name_node else ""
        idx_cols = []
        idx_type = "NORMAL"
        for ordered_expr in col_def.expressions:
            col_node = ordered_expr.args.get("this") if hasattr(ordered_expr, 'args') else None
            if col_node:
                col_name = col_node.sql(dialect=self.dialect).strip('`"')
                if col_name:
                    idx_cols.append(col_name)
        # 判断索引类型
        # v1.6.2.2 / DEF-1: 原实现 `def_str = str(col_def).upper()` + 裸子串包含判断，
        # 会把列名/索引名中含 unique/primary/fulltext 的普通索引误判（实测：列名
        # list_unique_num → 该普通索引被标成 UNIQUE），进而 R054 对普通索引误报，
        # 且真唯一索引被顶替而漏检。改读 sqlglot 的结构化 kind 参数。
        # 实测 sqlglot 26.0/30.12/30.14：IndexColumnConstraint 只承载
        # kind ∈ {None,'FULLTEXT','SPATIAL'}，UNIQUE 走 UniqueColumnConstraint、
        # PRIMARY 走 exp.PrimaryKey，都不经过本函数。此处仍用白名单精确映射而非
        # 二元判断：万一未来 sqlglot 把 PRIMARY/UNIQUE 放进本节点，也不会静默
        # 降级成 NORMAL（配套 AST 契约测试在升级时显式失败）。
        # SPATIAL 维持映射为 NORMAL：这是本次热修"输出域不变"的兼容性取舍，
        # 不是"空间索引在语义上等同普通索引"的结论。
        kind = (col_def.args.get("kind") or "").upper()
        idx_type = kind if kind in {"PRIMARY", "UNIQUE", "FULLTEXT"} else "NORMAL"
        if idx_cols:
            return {"name": idx_name, "columns": idx_cols, "type": idx_type}
        return {}

    def _parse_unique_constraint(self, unique_def) -> dict:
        """结构化提取表级 UNIQUE KEY/INDEX；未知 AST 形状失败关闭。"""
        schema = unique_def.args.get("this")
        if not isinstance(schema, exp.Schema):
            return {}
        name_node = schema.args.get("this")
        idx_name = (getattr(name_node, "name", "") or "").strip('`" ')
        idx_cols = []
        for part in (schema.expressions or []):
            if isinstance(part, exp.Ordered):
                part = part.this
            if isinstance(part, exp.Identifier):
                col_name = part.name
            elif isinstance(part, exp.Anonymous):
                # TDSQL/MySQL 前缀索引 `col(n)` 在 sqlglot 中是 Anonymous；
                # 直接解析成功路径也会调用本函数，故这里不能只信规划器：必须再次
                # 证明恰好一个正整数字面量，避免把 `lower(col)` 函数索引当成列 lower。
                base = part.args.get("this")
                pargs = list(part.expressions or [])
                if (len(pargs) != 1 or not isinstance(pargs[0], exp.Literal)
                        or pargs[0].is_string or not str(pargs[0].this).isdigit()
                        or int(pargs[0].this) <= 0):
                    return {}
                col_name = base if isinstance(base, str) else getattr(base, "name", "")
            else:
                return {}                             # 函数/表达式/未知形状不得猜测
            col_name = (col_name or "").strip('`" ')
            if not col_name:
                return {}
            idx_cols.append(col_name)
        if not idx_cols:
            return {}
        return {
            "name": idx_name or "UNIQUE",
            "columns": idx_cols,
            "type": "UNIQUE",
            "origin": "TABLE_UNIQUE",
        }

    def _parse_column_unique_constraint(self, col_def: exp.ColumnDef):
        """把 `col TYPE UNIQUE [KEY]` 转成下游统一的 UNIQUE 索引语义。

        只遍历 ColumnDef 的**直接 constraints**，不使用 find_all()，避免把嵌套节点
        或未来 AST 结构误算成第二个唯一索引。MySQL/TDSQL 未显式命名的单列 UNIQUE
        以列名作为隐式索引名；R054 助手只读取 name/columns/type，origin 仅供诊断。
        """
        found = 0
        malformed = False
        for constraint in (col_def.args.get("constraints") or []):
            kind = constraint.args.get("kind")
            if isinstance(kind, exp.UniqueColumnConstraint):
                found += 1
                # sqlglot 29.0.0 会把第二个 UNIQUE 折叠到首个节点的 this，
                # 30.x 则可能形成第二个约束；两种 AST 都必须失败关闭。
                malformed = malformed or kind.args.get("this") is not None
        if found == 0:
            return None                               # 非唯一列，不影响完整性
        if found != 1 or malformed:
            return {}                                 # 看到了 UNIQUE 但不能形成唯一语义
        name = (col_def.name or "").strip('`" ')
        if not name:
            return {}
        return {
            "name": name,
            "columns": [name],
            "type": "UNIQUE",
            "origin": "COLUMN_UNIQUE",
        }

    def _extract_column_comment(self, col_def: exp.ColumnDef) -> str:
        """提取列注释"""
        for constraint in col_def.find_all(exp.ColumnConstraint):
            c_kind = constraint.args.get("kind")
            if type(c_kind).__name__ == "CommentColumnConstraint":
                if c_kind.this:
                    return c_kind.this.sql(dialect=self.dialect).strip("'\"")
        return ""

    def _check_plural(self, name: str) -> bool:
        """检查表名是否为复数"""
        name = name.strip('`"').lower()
        if not name or name in self.PLURAL_IGNORE:
            return False
        if name.endswith("ies"):
            return True
        if name.endswith("ses") or name.endswith("es"):
            base = name[:-2]
            return base not in self.PLURAL_IGNORE
        if name.endswith("s") and not name.endswith("ss"):
            return True
        return False

    def _parse_column_def(self, col_def: exp.ColumnDef) -> dict:
        """解析单个列定义"""
        col_name = col_def.name
        data_type = col_def.args.get("kind")
        raw_type = data_type.sql(dialect=self.dialect) if data_type else ""

        type_name = ""
        if data_type and data_type.this is not None:
            dtype = data_type.this
            if hasattr(dtype, 'name'):
                type_name = dtype.name.upper()
            elif hasattr(dtype, 'value'):
                type_name = str(dtype.value).upper()

        if not type_name and raw_type:
            type_name = raw_type.split("(")[0].split(" ")[0].upper()

        info = {
            "name": col_name,
            "type": type_name,
            "raw_type": raw_type,
            "is_primary_key": False,
            "is_not_null": False,
            "has_default": False,
            "default_value": None,
            "length": None,
            "has_comment": False,
            "comment": "",
        }

        for constraint in col_def.find_all(exp.ColumnConstraint):
            c_kind = constraint.args.get("kind")
            if isinstance(c_kind, exp.PrimaryKeyColumnConstraint):
                info["is_primary_key"] = True
            elif isinstance(c_kind, exp.NotNullColumnConstraint):
                info["is_not_null"] = True
            elif isinstance(c_kind, exp.DefaultColumnConstraint):
                info["has_default"] = True
                info["default_value"] = c_kind.this.sql(dialect=self.dialect) if c_kind.this else None

        if data_type:
            size = data_type.args.get("expressions")
            if size and len(size) > 0:
                try:
                    info["length"] = int(size[0].sql(dialect=self.dialect))
                except (ValueError, IndexError):
                    pass

        return info

    def _parse_table_properties(self, properties, parsed: ParsedSQL):
        """解析表选项 (ENGINE, CHARSET, COMMENT 等)"""
        for prop in getattr(properties, "expressions", []):
            try:
                if isinstance(prop, exp.EngineProperty):
                    engine_var = prop.this
                    if engine_var:
                        parsed.engine = engine_var.name.upper() if hasattr(engine_var, 'name') else str(engine_var).upper()
                        parsed.table_options["engine"] = parsed.engine
                elif isinstance(prop, exp.CharacterSetProperty):
                    charset_var = prop.this
                    if charset_var:
                        parsed.charset = charset_var.name.upper() if hasattr(charset_var, 'name') else str(charset_var).upper()
                        parsed.table_options["charset"] = parsed.charset
                elif isinstance(prop, exp.SchemaCommentProperty):
                    parsed.has_table_comment = True
                    c_val = prop.this
                    if c_val:
                        parsed.table_options["COMMENT"] = c_val.this if hasattr(c_val, 'this') else str(c_val)
                elif isinstance(prop, exp.Property):
                    key = prop.name.upper() if hasattr(prop, 'name') else ""
                    val = prop.args.get("value")
                    if key and val is not None:
                        try:
                            parsed.table_options[key] = val.sql(dialect=self.dialect)
                        except Exception:
                            parsed.table_options[key] = str(val)
                    if key == "COMMENT" or "COMMENT" in prop.__class__.__name__.upper():
                        parsed.has_table_comment = True
            except Exception:
                continue

    # ── ALTER TABLE 解析 ─────────────────────────────────

    def _parse_alter(self, ast: exp.Alter, parsed: ParsedSQL):
        """解析 ALTER TABLE 语句"""
        parsed.is_alter_table = True
        table = ast.args.get("this")
        if table:
            parsed.tables.append(table.sql(dialect=self.dialect))

        # 尝试提取ALTER操作
        # v1.6.3.2 / REQ-01A：sqlglot 30.14 的 Alter 动作在 args["actions"]
        # （`expressions` 属性对 Alter 恒为空），旧写法读 expressions 导致
        # alter_actions 恒为空、ALTER 列类型通道无从建立。
        actions = ast.args.get("actions") if isinstance(ast.args, dict) else None
        for action in actions or []:
            action_info = {"action": "modify", "column": "", "old_type": "", "new_type": ""}
            if isinstance(action, exp.AlterColumn):
                action_info["action"] = "modify"
                if hasattr(action, 'this') and action.this:
                    action_info["column"] = action.this.name
            elif isinstance(action, exp.RenameColumn):
                action_info["action"] = "rename"
                if hasattr(action, 'this') and action.this:
                    action_info["column"] = action.this.name
            parsed.alter_actions.append(action_info)
            # v1.6.3.2 / REQ-01A：ADD/MODIFY/CHANGE 的列类型只读通道。
            self._collect_alter_column_type(action, parsed)

    def _collect_alter_column_type(self, action, parsed: ParsedSQL):
        """从 ALTER action 提取列类型事实（REQ-01A）。

        sqlglot 30.14 形态（锁定版本实测）：ADD COLUMN → 直接 `ColumnDef`；
        MODIFY/CHANGE COLUMN → `ModifyColumn(this=ColumnDef)`，CHANGE 另带
        `rename_from`。DROP/RENAME/DEFAULT 等动作不进入该集合（OUT-09）。
        类型归一与 CREATE 共用 `_parse_column_def`，不另造归一器。
        """
        op, col_def = None, None
        if isinstance(action, exp.ColumnDef):
            op, col_def = "ADD", action
        elif isinstance(action, exp.ModifyColumn):
            op = "CHANGE" if action.args.get("rename_from") else "MODIFY"
            inner = action.this
            col_def = inner if isinstance(inner, exp.ColumnDef) else None
        if op is None or col_def is None:
            return
        info = self._parse_column_def(col_def)
        parsed.alter_column_types.append({
            "name": info["name"],
            "type": info["type"],
            "raw_type": info["raw_type"],
            "length": info.get("length"),
            "operation": op,
        })

    # ── v1.6.3.2 / REQ-06：DML LIMIT 结构化 ────────────────────────────────

    @staticmethod
    def _safe_limit_int(value) -> Optional[int]:
        """安全转换 LIMIT 字面量；非法/溢出/负值返回 None（视为不可证明）。"""
        try:
            v = int(str(value).strip())
        except (ValueError, TypeError):
            return None
        if v < 0 or v > 10 ** 12:
            return None
        return v

    def _extract_dml_limit(self, ast, sql: str) -> dict:
        """提取 UPDATE/DELETE 顶层 LIMIT 的结构化事实。

        优先从 SQLGlot AST 的 `Limit.expression` 读取；AST 不可靠（None 或
        Command 降级）时才使用词法 token 有限回退。回退忽略注释与字符串，
        且只接受括号深度 0 的 LIMIT（SELECT 子查询内部的不算外层上限）。
        N-01：`exp.Limit` 类上没有 `.offset` 访问器，一律 `args.get("offset")`；
        UPDATE/DELETE 两参数 LIMIT 的 offset 在 `Limit.args["offset"]`（Literal），
        与 SELECT 的 `Select.args["offset"]`（Offset 节点）不得互相套用。
        """
        fact = {"present": False, "row_count": None, "offset": None,
                "parameterized": False, "verifiable": False}
        lim = None
        if ast is not None and not isinstance(ast, exp.Command):
            args = getattr(ast, "args", None)
            if isinstance(args, dict):
                lim = args.get("limit")
        if isinstance(lim, exp.Limit):
            fact["present"] = True
            off = lim.args.get("offset")
            if off is not None:
                fact["offset"] = self._safe_limit_int(
                    off.this if isinstance(off, exp.Literal) else off)
                fact["verifiable"] = False
                return fact
            expr = lim.args.get("expression")
            if isinstance(expr, exp.Placeholder):
                fact["parameterized"] = True
                fact["verifiable"] = False
                return fact
            val = self._safe_limit_int(
                expr.this if isinstance(expr, exp.Literal) else expr)
            if val is None:
                fact["verifiable"] = False
                return fact
            fact["row_count"] = val
            fact["verifiable"] = True
            return fact
        # DEF-SIT-03：AST 完好（非 None、非 Command）时，"没有 limit 节点"
        # 本身就是权威结论——语句确实没有 LIMIT，无需再做一次全量词法化
        # （设计 §5.4 性能不变量：非 DDL 批不得新增 tokenization）。只有
        # AST 不可靠（ast is None 或降级为 Command）才允许 token 回退。
        if ast is not None and not isinstance(ast, exp.Command):
            return fact                      # present=False, verifiable=False
        # token 回退：仅当 AST 不可靠时使用
        try:
            toks = sqlglot.Dialect.get_or_raise(
                self.dialect).tokenizer_class().tokenize(sql)
        except Exception:
            return fact
        depth, k, n = 0, 0, len(toks)
        while k < n:
            tt = toks[k].token_type
            if tt == TokenType.L_PAREN:
                depth += 1
            elif tt == TokenType.R_PAREN:
                depth -= 1
            elif depth == 0 and tt == TokenType.LIMIT:
                fact["present"] = True
                j = k + 1
                if j < n and toks[j].token_type == TokenType.NUMBER:
                    first = self._safe_limit_int(toks[j].text)
                    j += 1
                    if j < n and toks[j].token_type == TokenType.COMMA:
                        fact["offset"] = first
                        fact["verifiable"] = False
                        return fact
                    fact["row_count"] = first
                    fact["verifiable"] = first is not None
                    return fact
                if j < n and (toks[j].text or "").strip()[:1] in ("?", ":"):
                    fact["parameterized"] = True
                    fact["verifiable"] = False
                    return fact
                fact["verifiable"] = False
                return fact
            k += 1
        return fact

    # ── DROP 解析 ────────────────────────────────────────

    def _parse_drop(self, ast: exp.Drop, parsed: ParsedSQL):
        """解析 DROP 语句"""
        table = ast.args.get("this")
        if table:
            parsed.tables.append(table.sql(dialect=self.dialect))

    # ── 通用解析 ─────────────────────────────────────────

    def _parse_common(self, ast, parsed: ParsedSQL):
        """通用解析：JOIN类型、子查询等"""
        # 检测显式JOIN
        for join in ast.find_all(exp.Join):
            parsed.has_explicit_join = True
            break

    # ── 通用辅助方法 ─────────────────────────────────────

    def _extract_tables(self, ast) -> list[str]:
        """从AST提取所有表名（不含别名）"""
        tables = []
        for table in ast.find_all(exp.Table):
            name = table.name
            if name and name not in tables:
                tables.append(name)
        return tables

    def _extract_where_columns(self, where_node) -> list[str]:
        """提取WHERE条件中涉及的列名"""
        columns = []
        for col in where_node.find_all(exp.Column):
            name = col.sql(dialect=self.dialect)
            if name and name not in columns:
                columns.append(name)
        return columns

    def _check_where_has_function(self, where_node) -> bool:
        """检查WHERE条件中是否包含函数调用或索引失效模式"""
        _op_names = {
            'And', 'Or', 'Not', 'EQ', 'NEQ', 'GT', 'GTE', 'LT', 'LTE',
            'Is', 'IsNot', 'In', 'Between', 'Like', 'ILike',
            'Paren', 'Condition',
        }
        for node in where_node.walk():
            node_type = type(node).__name__
            if isinstance(node, exp.Func) and node_type not in _op_names:
                return True
            if isinstance(node, exp.Like):
                pattern = node.args.get("expression")
                if pattern:
                    pattern_sql = pattern.sql().strip("'\"")
                    if pattern_sql.startswith("%"):
                        return True
            if isinstance(node, exp.Or):
                return True
        return False

    def _check_order_by_random(self, order_node) -> bool:
        """检查 ORDER BY 中是否包含 RAND()"""
        for expression in order_node.expressions:
            expr = expression.this
            if isinstance(expr, exp.Anonymous) and expr.name.upper() in ("RAND", "RANDOM"):
                return True
            if isinstance(expr, exp.Func) and expr.sql(dialect=self.dialect).upper().startswith("RAND"):
                return True
        return False

    def _calc_subquery_depth(self, ast) -> int:
        """计算子查询嵌套深度"""
        max_depth = 0
        stack = [(ast, 0)]
        while stack:
            node, depth = stack.pop()
            new_depth = depth
            if isinstance(node, exp.Subquery):
                new_depth = depth + 1
                max_depth = max(max_depth, new_depth)
            elif isinstance(node, exp.Select) and depth > 0:
                new_depth = depth + 1
                max_depth = max(max_depth, new_depth)
            for key, val in node.args.items():
                if isinstance(val, list):
                    for item in val:
                        if isinstance(item, exp.Expression):
                            stack.append((item, new_depth))
                elif isinstance(val, exp.Expression):
                    stack.append((val, new_depth))
        return max_depth

    def _count_joins(self, ast) -> int:
        """统计 JOIN 数量"""
        count = 0
        for join in ast.find_all(exp.Join):
            count += 1
        return count
