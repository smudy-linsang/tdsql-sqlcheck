"""
TDSQL SQL审核工具 - Oracle迁移兼容规则 (V2.1)

42条规则(R078-R119)，覆盖Oracle→TDSQL迁移中的函数替换、语法改写、分布式限制等。
规范来源：《TDSQL兼容业务系统适配改造方案》V1.5.1

检测策略：regex-first（Oracle语法在mysql方言下必然parse_error），AST仅作增强。
所有L1规则使用 clean_sql() 清洗后正则检测，防误报。
"""
import re
import functools
from typing import Optional

from sqlglot import exp

from backend.engine.parser import ParsedSQL
from backend.engine.rules.base import BaseRule
from backend.models import RuleCategory, Severity, Violation

# ═══════════════════════════════════════════════════════════════════
# 模块级预编译正则（性能硬要求）
# ═══════════════════════════════════════════════════════════════════

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"--[^\n]*")
_STRING_LIT = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")

# 各规则检测正则
_RE_ORACLE_TYPES = re.compile(r"\b(number|varchar2|nvarchar2|clob|nclob|bfile|rowid|urowid|binary_float|binary_double|long\s+raw)\s*[\(,\s]", re.IGNORECASE)
_RE_ROWINUM = re.compile(r"\brownum\b")
_RE_NVL = re.compile(r"\bnvl\s*\(")
_RE_DECODE = re.compile(r"\bdecode\s*\(")
_RE_TO_CHAR = re.compile(r"\bto_char\s*\(")
_RE_TO_NUMBER = re.compile(r"\bto_number\s*\(")
_RE_PIPE_CONCAT = re.compile(r"\|\|")
_RE_TO_DATE = re.compile(r"\bto_date\s*\(")
_RE_TRUNC = re.compile(r"\btrunc\s*\(")
_RE_LTRIM_RTRIM_2ARG = re.compile(r"\b[lr]trim\s*\([^()]*,")
_RE_ADD_MONTHS = re.compile(r"\badd_months\s*\(")
_RE_SUBSTR_ZERO = re.compile(r"\bsubstr(?:ing)?\s*\(\s*[^,()]+,\s*(?:0|'0')\s*[,)]")
_RE_SYSTIMESTAMP = re.compile(r"\bsystimestamp\b")
_RE_SYSDATE_BARE = re.compile(r"\bsysdate\b(?!\s*\()")
_RE_MERGE_INTO = re.compile(r"\bmerge\s+into\b")
_RE_WITH_AS = re.compile(r"^\s*with\s+(recursive\s+)?\w+\s+as\s*\(", re.IGNORECASE)
_RE_LENGTH_BARE = re.compile(r"(?<!char_)(?<!octet_)\blength\s*\(")
_RE_LISTAGG = re.compile(r"\blistagg\s*\(")
_RE_WITHIN_GROUP = re.compile(r"\bwithin\s+group\b")
_RE_MINUS = re.compile(r"\bminus\b")
_RE_FULL_JOIN = re.compile(r"\bfull\s+(outer\s+)?join\b")
_RE_DEFAULT_FUNC = re.compile(r"default\s+\w+\s*\(")
_RE_DEFAULT_CURRENT = re.compile(r"default\s+current_timestamp", re.IGNORECASE)
_RE_HASH_PART = re.compile(r"partition\s+by\s+hash\s*\(\s*([a-z_][\w]*)\s*\)")
_RE_DERIVED_NOALIAS = re.compile(r"from\s*\(\s*select[\s\S]*?\)\s*(where|group\s+by|order\s+by|limit|on|join|union|$)")
_RE_DELETE_ALIAS = re.compile(r"^\s*delete\s+from\s+[\w.\"`]+\s+(?:as\s+)?([a-z_]\w*)\b")
_RE_SEQ_KEYWORDS = re.compile(r"\bas\s+(condition|nextval|currval|minvalue|maxvalue|cycle|increment)\b")
_RE_CONDITION_BARE = re.compile(r"(?<![`\w])(condition)(?![`\w])")
_RE_ESCAPE_BS = re.compile(r"escape\s+'\\{1,2}'")
_RE_OP_SPACE = re.compile(r"[<>!]\s+=|<\s+>")
_RE_FUNC_PAREN_SPACE = re.compile(r"\b(sum|count|avg|max|min|ifnull|substr|substring|concat|group_concat|char_length|date_format|str_to_date|truncate|cast|convert|coalesce|upper|lower|round|abs)\s+\(")
_RE_FULLWIDTH_PAREN = re.compile(r"[（）]")
_RE_ORACLE_OUTER = re.compile(r"\(\s*\+\s*\)")
_RE_CONNECT_BY = re.compile(r"\bconnect\s+by\b")
_RE_INSERT_SELECT = re.compile(r"insert\s+into[\s\S]+?\bselect\b")
_RE_NEXTVAL_CURRVAL = re.compile(r"\b(nextval|currval)\b")
_RE_USERENV = re.compile(r"\buserenv\s*\(")
_RE_WINDOW_OVER = re.compile(r"\)\s*over\s*\(")
_RE_CURSOR_DECL = re.compile(r"declare\s+\w+\s+cursor\b")
_RE_CURSOR_FETCH = re.compile(r"\bfetch\s+\w+\s+into\b")
_RE_DROP_PARTITION = re.compile(r"\bdrop\s+partition\b")
_RE_SHARDKEY = re.compile(r"shardkey\s*=\s*([^\s,]+(?:\s*,\s*[^\s,]+)*)")
_RE_SHARDKEY_SINGLE = re.compile(r"shardkey\s*=\s*([\w]+)")
_RE_DATE_ARITH = re.compile(r"(sysdate\s*\(\s*\)|now\s*\(\s*\)|current_timestamp|curdate\s*\(\s*\))\s*[+-]\s*\d")

# 删除保留词安全关键词集合
_DELETE_RESERVED = {"where", "order", "limit", "using", "partition", "for", "join", "inner", "left", "right"}

# TDSQL sequence特殊词
TDSQL_SEQUENCE_KEYWORDS = {"nextval", "currval", "minvalue", "maxvalue", "cycle", "increment"}

# DDL自守卫辅助
_INT_TYPES = {"INT", "INTEGER", "BIGINT", "SMALLINT", "MEDIUMINT", "TINYINT"}
_SHARD_KEY_TYPES = {"INT", "INTEGER", "BIGINT", "SMALLINT", "CHAR", "VARCHAR"}


# ═══════════════════════════════════════════════════════════════════
# 共享清洗助手
# ═══════════════════════════════════════════════════════════════════

@functools.lru_cache(maxsize=256)
def clean_sql(sql: str) -> str:
    """审核前清洗：去块注释/行注释/字符串字面量（保留''占位），转小写。
    防止关键字出现在字符串或注释中造成误报。"""
    s = _BLOCK_COMMENT.sub(" ", sql)
    s = _LINE_COMMENT.sub(" ", s)
    s = _STRING_LIT.sub("''", s)
    return s.lower()


def _strip_comments_only(sql: str) -> str:
    """仅去注释，保留字符串字面量（用于R089/R102等需要检查字面量的规则）"""
    s = _BLOCK_COMMENT.sub(" ", sql)
    s = _LINE_COMMENT.sub(" ", s)
    return s.lower()


# ═══════════════════════════════════════════════════════════════════
# R078-R119: Oracle迁移兼容规则
# ═══════════════════════════════════════════════════════════════════

class R078OracleDataType(BaseRule):
    """R078: 禁止使用Oracle专有数据类型"""
    rule_id = "R078"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.ERROR
    description = "建表/改表禁止使用Oracle专有数据类型（NUMBER/VARCHAR2/CLOB等），需转换为TDSQL对等类型"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - 数据类型转换"
    fix_suggestion = "NUMBER→DECIMAL(p,s)或INT/BIGINT（注意：历史number放整型标志字段的，转换后勿带精度，避免Java取值类型错误）；VARCHAR2→VARCHAR；CLOB→TEXT/LONGTEXT；RAW→VARBINARY；DATE→DATETIME"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not (parsed.is_create_table or parsed.is_alter_table):
            return None
        # L3: 检查parsed.column_types
        for ct in parsed.column_types:
            t = ct.get("type", "").upper()
            if t in ("NUMBER", "VARCHAR2", "NVARCHAR2", "CLOB", "NCLOB", "RAW", "LONG", "BFILE", "BINARY_FLOAT", "BINARY_DOUBLE", "ROWID", "UROWID"):
                return self._make_violation(f"使用了Oracle专有数据类型 {t}，需转换为TDSQL对等类型")
        # L1回退
        text = clean_sql(parsed.raw_sql)
        if _RE_ORACLE_TYPES.search(text):
            return self._make_violation("检测到Oracle专有数据类型，需转换为TDSQL对等类型")
        return None


class R079RownumUsage(BaseRule):
    """R079: 禁止使用ROWNUM"""
    rule_id = "R079"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.ERROR
    description = "TDSQL不支持Oracle伪列ROWNUM，请改用LIMIT分页"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - ROWNUM改写"
    fix_suggestion = "SELECT * FROM t WHERE 条件 LIMIT n（LIMIT n 等价 LIMIT 0,n）；需排序时LIMIT置于ORDER BY之后：… ORDER BY 字段 LIMIT m,n"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        text = clean_sql(parsed.raw_sql)
        if _RE_ROWINUM.search(text):
            return self._make_violation("检测到ROWNUM，TDSQL不支持，请改用LIMIT分页")
        return None


class R080NvlFunction(BaseRule):
    """R080: 禁止使用NVL"""
    rule_id = "R080"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.ERROR
    description = "TDSQL不支持NVL函数，请改用IFNULL(expr1,expr2)或COALESCE"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - NVL改写"
    fix_suggestion = "NVL(a,b)→IFNULL(a,b)；多条件嵌套或多参用COALESCE(e1,e2,...)；示例：select IFNULL(max(tempa),0) from t"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        text = clean_sql(parsed.raw_sql)
        if _RE_NVL.search(text):
            return self._make_violation("检测到NVL函数，TDSQL不支持，请改用IFNULL或COALESCE")
        return None


class R081DecodeFunction(BaseRule):
    """R081: 禁止使用DECODE"""
    rule_id = "R081"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.ERROR
    description = "TDSQL不支持DECODE函数，请改用CASE WHEN或IF()"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - DECODE改写"
    fix_suggestion = "decode(x,'A','1','B','2',缺省)→CASE x WHEN 'A' THEN '1' WHEN 'B' THEN '2' ELSE 缺省 END；两分支简单场景可用IF(cond,v1,v2)"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        text = clean_sql(parsed.raw_sql)
        if _RE_DECODE.search(text):
            return self._make_violation("检测到DECODE函数，TDSQL不支持，请改用CASE WHEN或IF()")
        return None


class R082ToCharFunction(BaseRule):
    """R082: 禁止使用TO_CHAR"""
    rule_id = "R082"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.ERROR
    description = "TDSQL不支持TO_CHAR函数，日期格式化用DATE_FORMAT，数值转换用CONVERT/CAST/FORMAT"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - TO_CHAR改写"
    fix_suggestion = "日期→DATE_FORMAT(col,'%Y%m%d')；类型转换→CONVERT(value,type)（type: BINARY/CHAR()/DATE/TIME/DATETIME/DECIMAL/SIGNED/UNSIGNED）；含'FM9999.0999'类精度进位格式的，用LPAD/FORMAT改写，需逐格式核实进位方式（估值/额度/费用计算场景重点复核）"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        text = clean_sql(parsed.raw_sql)
        if _RE_TO_CHAR.search(text):
            return self._make_violation("检测到TO_CHAR函数，TDSQL不支持，请改用DATE_FORMAT/CONVERT/CAST")
        return None


class R083ToNumberFunction(BaseRule):
    """R083: 禁止使用TO_NUMBER"""
    rule_id = "R083"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.ERROR
    description = "TDSQL不支持TO_NUMBER函数，请改用CAST；注意CAST对非法数字会截断而不报错"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - TO_NUMBER改写"
    fix_suggestion = "cast(x as unsigned int)/cast(x as decimal(10,2))；注意：cast('11a' as unsigned int)=11（截断），不像to_number直接报错，涉及数据校验的需应用侧兜底"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        text = clean_sql(parsed.raw_sql)
        if _RE_TO_NUMBER.search(text):
            return self._make_violation("检测到TO_NUMBER函数，TDSQL不支持，请改用CAST")
        return None


class R084PipeConcat(BaseRule):
    """R084: 疑似Oracle || 字符串拼接"""
    rule_id = "R084"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.WARNING
    description = "检测到||运算符：MySQL/TDSQL默认语义为逻辑OR而非字符串拼接，Oracle迁移SQL请改用CONCAT()"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - 字符串拼接改写"
    fix_suggestion = "'%'||v||'%' → CONCAT('%',v,'%')"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        text = clean_sql(parsed.raw_sql)
        if _RE_PIPE_CONCAT.search(text):
            return self._make_violation("检测到||运算符，MySQL/TDSQL语义为逻辑OR，字符串拼接请改用CONCAT()")
        return None


class R085ToDateFunction(BaseRule):
    """R085: 禁止使用TO_DATE"""
    rule_id = "R085"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.ERROR
    description = "TDSQL不支持TO_DATE函数，请改用STR_TO_DATE(date,format)"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - TO_DATE改写"
    fix_suggestion = "to_date(v,'YYYYMMDD')→str_to_date(v,'%Y%m%d')；格式符对照：YYYY→%Y，MM→%m，DD→%d，HH24→%H，MI→%i，SS→%s"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        text = clean_sql(parsed.raw_sql)
        if _RE_TO_DATE.search(text):
            return self._make_violation("检测到TO_DATE函数，TDSQL不支持，请改用STR_TO_DATE")
        return None


class R086TruncFunction(BaseRule):
    """R086: 禁止使用TRUNC"""
    rule_id = "R086"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.ERROR
    description = "TDSQL不支持TRUNC函数：数值截断用TRUNCATE(X,D)，日期截断用DATE_FORMAT"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - TRUNC改写"
    fix_suggestion = "TRUNC(x,2)→TRUNCATE(x,2)；TRUNC(sysdate)→DATE_FORMAT(sysdate(),'%Y%m%d')；TRUNC(sysdate,'mm')→DATE_FORMAT(sysdate(),'%Y%m01')"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        text = clean_sql(parsed.raw_sql)
        if _RE_TRUNC.search(text):
            return self._make_violation("检测到TRUNC函数，TDSQL不支持，数值截断用TRUNCATE，日期截断用DATE_FORMAT")
        return None


class R087TrimTwoArgs(BaseRule):
    """R087: LTRIM/RTRIM双参数用法不支持"""
    rule_id = "R087"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.ERROR
    description = "TDSQL的LTRIM/RTRIM仅支持单参数去空格，去除指定字符请用TRIM({BOTH|LEADING|TRAILING} remstr FROM str)"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - TRIM差异"
    fix_suggestion = "ltrim(s,'0')→TRIM(LEADING '0' FROM s)；rtrim(s,'x')→TRIM(TRAILING 'x' FROM s)；注意TDSQL的remstr是整串匹配而非Oracle的字符集合匹配"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        text = clean_sql(parsed.raw_sql)
        if _RE_LTRIM_RTRIM_2ARG.search(text):
            return self._make_violation("检测到LTRIM/RTRIM双参数用法，TDSQL不支持，请改用TRIM(LEADING/TRAILING ... FROM ...)")
        return None


class R088AddMonths(BaseRule):
    """R088: 禁止使用ADD_MONTHS"""
    rule_id = "R088"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.ERROR
    description = "TDSQL不支持ADD_MONTHS函数，请改用ADDDATE(date, INTERVAL expr MONTH)"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - ADD_MONTHS改写"
    fix_suggestion = "add_months(d,-1)→adddate(d, INTERVAL -1 MONTH)；unit支持SECOND/MINUTE/HOUR/DAY/MONTH/YEAR"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        text = clean_sql(parsed.raw_sql)
        if _RE_ADD_MONTHS.search(text):
            return self._make_violation("检测到ADD_MONTHS函数，TDSQL不支持，请改用ADDDATE(... INTERVAL ... MONTH)")
        return None


class R089SubstrZeroStart(BaseRule):
    """R089: SUBSTR起始位置不能为0"""
    rule_id = "R089"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.ERROR
    description = "TDSQL的SUBSTR起始位置只能从1开始，start=0将返回空串（Oracle中0按1处理），结果错误"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - SUBSTR差异"
    fix_suggestion = "substr(c,0,9)→substr(c,1,9)（Oracle中0与1等效，TDSQL必须从1开始）"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        # 用raw_sql去注释但保留字面量
        text = _strip_comments_only(parsed.raw_sql)
        if _RE_SUBSTR_ZERO.search(text):
            return self._make_violation("SUBSTR起始位置为0，TDSQL中start=0返回空串，请改为从1开始")
        return None


class R090BareSysdate(BaseRule):
    """R090: SYSDATE/SYSTIMESTAMP裸用"""
    rule_id = "R090"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.ERROR
    description = "Oracle的SYSDATE/SYSTIMESTAMP关键字用法不支持，TDSQL需使用sysdate()/NOW()函数（带括号）"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - SYSDATE改写"
    fix_suggestion = "SYSDATE→sysdate()；SYSTIMESTAMP→NOW(3)/sysdate(3)；截取日期用DATE_FORMAT(sysdate(),'%Y%m%d')"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        text = clean_sql(parsed.raw_sql)
        if _RE_SYSTIMESTAMP.search(text):
            return self._make_violation("检测到SYSTIMESTAMP，TDSQL不支持，请改用NOW(3)或sysdate(3)")
        if _RE_SYSDATE_BARE.search(text):
            return self._make_violation("检测到SYSDATE裸用，TDSQL需使用sysdate()函数（带括号）")
        return None


class R091MergeInto(BaseRule):
    """R091: 禁止使用MERGE INTO"""
    rule_id = "R091"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.ERROR
    description = "TDSQL不支持MERGE INTO：集中式且按主键/唯一键关联可用INSERT…ON DUPLICATE KEY UPDATE，分布式需拆分为程序逻辑"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - MERGE INTO改写"
    fix_suggestion = "关联字段为主键/唯一键（集中式）→INSERT INTO … ON DUPLICATE KEY UPDATE；仅更新不插入→UPDATE a JOIN b ON a.id=b.id SET a.xx=b.xx；其余场景拆SQL由程序实现"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        text = clean_sql(parsed.raw_sql)
        if _RE_MERGE_INTO.search(text):
            return self._make_violation("检测到MERGE INTO，TDSQL不支持，请改用INSERT...ON DUPLICATE KEY UPDATE或拆分SQL")
        return None


class R092WithAsCte(BaseRule):
    """R092: WITH AS子查询不支持"""
    rule_id = "R092"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.ERROR
    description = "分布式实例不支持WITH AS(CTE)，请改写为子查询或JOIN；集中式8.0递归场景可评估WITH RECURSIVE"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - WITH AS改写"
    fix_suggestion = "WITH a AS(SELECT…) SELECT * FROM a JOIN b → SELECT * FROM (SELECT…) a JOIN b；复杂多关联+union all场景建议拆查询/引入中间表"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        # L2 AST优先
        if parsed.ast:
            try:
                w = parsed.ast.args.get("with")
                if w:
                    return self._make_violation("检测到WITH AS(CTE)，分布式实例不支持，请改写为子查询或JOIN")
            except Exception:
                pass
        # L1回退
        text = clean_sql(parsed.raw_sql)
        if _RE_WITH_AS.match(text):
            return self._make_violation("检测到WITH AS(CTE)，分布式实例不支持，请改写为子查询或JOIN")
        return None


class R093LengthSemantics(BaseRule):
    """R093: LENGTH字节/字符语义差异"""
    rule_id = "R093"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.WARNING
    description = "TDSQL的LENGTH()返回字节数（Oracle为字符数），中文场景结果不一致；需字符数请用CHAR_LENGTH()"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - LENGTH差异"
    fix_suggestion = "select length(c)→select char_length(c)（需要字符数时）；确认确需字节数的可保留并在工单注明"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        text = clean_sql(parsed.raw_sql)
        if _RE_LENGTH_BARE.search(text):
            return self._make_violation("LENGTH()在TDSQL中返回字节数，中文场景需改用CHAR_LENGTH()获取字符数")
        return None


class R094ListaggFunction(BaseRule):
    """R094: 禁止使用LISTAGG/WITHIN GROUP"""
    rule_id = "R094"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.ERROR
    description = "TDSQL不支持LISTAGG() WITHIN GROUP()，请改用GROUP_CONCAT"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - LISTAGG改写"
    fix_suggestion = "LISTAGG(a||':'||b,'|') WITHIN GROUP(ORDER BY a,b)→GROUP_CONCAT(a,':',b ORDER BY a,b SEPARATOR '|')"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        text = clean_sql(parsed.raw_sql)
        if _RE_LISTAGG.search(text) or _RE_WITHIN_GROUP.search(text):
            return self._make_violation("检测到LISTAGG/WITHIN GROUP，TDSQL不支持，请改用GROUP_CONCAT")
        return None


class R095MinusOperator(BaseRule):
    """R095: 禁止使用MINUS集合运算"""
    rule_id = "R095"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.ERROR
    description = "TDSQL不支持MINUS集合运算，请改用LEFT JOIN…IS NULL或NOT EXISTS实现差集"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - MINUS改写"
    fix_suggestion = "A minus B→select a.* from A a left join B b on a.id=b.id where b.id is null（注意minus含去重语义，必要时补distinct）"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        text = clean_sql(parsed.raw_sql)
        if _RE_MINUS.search(text) and text.count("select") >= 2:
            return self._make_violation("检测到MINUS集合运算，TDSQL不支持，请改用LEFT JOIN...IS NULL")
        return None


class R096FullJoin(BaseRule):
    """R096: 禁止使用FULL JOIN"""
    rule_id = "R096"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.ERROR
    description = "底层MySQL不支持FULL JOIN，请改写为LEFT JOIN UNION RIGHT JOIN"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - FULL JOIN改写"
    fix_suggestion = "select * from A full join B on…→ select…left join…UNION select…right join…"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        text = clean_sql(parsed.raw_sql)
        if _RE_FULL_JOIN.search(text):
            return self._make_violation("检测到FULL JOIN，MySQL/TDSQL不支持，请改写为LEFT JOIN UNION RIGHT JOIN")
        return None


class R097DefaultValueFunction(BaseRule):
    """R097: 建表默认值禁用函数/类型转换"""
    rule_id = "R097"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.ERROR
    description = "TDSQL建表字段DEFAULT值不支持类型转换/函数表达式（Proxy报ERROR 1064），仅CURRENT_TIMESTAMP例外"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - DEFAULT值限制"
    fix_suggestion = "如 data_dt char(8) default date_format(…)→改为 data_dt char(8) not null，默认值由应用层赋值"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not (parsed.is_create_table or parsed.is_alter_table):
            return None
        # L3: 检查parsed.columns
        for col in parsed.columns:
            if col.get("has_default"):
                dv = str(col.get("default_value", ""))
                if "(" in dv and not dv.lower().startswith(("current_timestamp", "now")):
                    return self._make_violation(f"字段 {col.get('name','')} 的DEFAULT值使用了函数表达式，TDSQL不支持")
        # L1回退
        text = clean_sql(parsed.raw_sql)
        if _RE_DEFAULT_FUNC.search(text) and not _RE_DEFAULT_CURRENT.search(text):
            return self._make_violation("建表字段DEFAULT值使用了函数/类型转换，TDSQL仅支持CURRENT_TIMESTAMP")
        return None


class R098HashPartitionNonInt(BaseRule):
    """R098: 非整型字段做HASH分区"""
    rule_id = "R098"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.ERROR
    description = "HASH分区要求分区字段为整型；char/varchar等非整型字段请改用KEY分区或murmurHashCodeAndMod改造"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - HASH分区限制"
    fix_suggestion = "方案1：partition by hash(murmurHashCodeAndMod(col,N))；方案2：改用KEY分区（支持非整型）"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not (parsed.is_create_table or parsed.is_alter_table):
            return None
        text = clean_sql(parsed.raw_sql)
        m = _RE_HASH_PART.search(text)
        if m:
            col_name = m.group(1)
            # 查该列类型
            for col in parsed.columns:
                if col.get("name", "").lower() == col_name.lower():
                    col_type = col.get("type", "").upper()
                    if col_type and col_type not in _INT_TYPES:
                        return self._make_violation(f"HASH分区字段 {col_name} 类型为{col_type}，非整型，请改用KEY分区")
        return None


class R099DerivedTableAlias(BaseRule):
    """R099: 派生表/子查询必须加别名"""
    rule_id = "R099"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.ERROR
    description = "FROM后的子查询（派生表）必须指定别名，否则TDSQL报错"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - 派生表别名"
    fix_suggestion = "select * from (select * from t)→select * from (select * from t) B"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        # L2 AST
        if parsed.ast:
            try:
                for sub in parsed.ast.find_all(exp.Subquery):
                    if sub.find_ancestor(exp.From, exp.Join) and not sub.alias:
                        return self._make_violation("FROM后的子查询（派生表）未指定别名，TDSQL会报错")
            except Exception:
                pass
        # L1回退
        text = clean_sql(parsed.raw_sql)
        if _RE_DERIVED_NOALIAS.search(text):
            return self._make_violation("FROM后的子查询（派生表）可能未指定别名，TDSQL要求必须有别名")
        return None


class R100DeleteTableAlias(BaseRule):
    """R100: DELETE语句禁用表别名"""
    rule_id = "R100"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.ERROR
    description = "分布式实例DELETE语句不支持对被删表设置别名，请使用真实表名"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - DELETE别名限制"
    fix_suggestion = "DELETE FROM T1 a WHERE EXISTS(...)→DELETE FROM T1 WHERE EXISTS(引用T1.id)"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.sql_type != "DELETE":
            return None
        text = clean_sql(parsed.raw_sql)
        m = _RE_DELETE_ALIAS.match(text)
        if m:
            alias = m.group(1)
            if alias.lower() not in _DELETE_RESERVED:
                return self._make_violation("DELETE语句中对被删表设置了别名，分布式实例不支持")
        return None


class R101ReservedWordAlias(BaseRule):
    """R101: 别名/列名使用保留字或sequence特殊词"""
    rule_id = "R101"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.ERROR
    description = "别名/标识符使用了TDSQL保留字（如CONDITION）或sequence特殊词（NEXTVAL/MINVALUE等），需加反引号或改用普通别名"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - 保留字与sequence特殊词"
    fix_suggestion = "select `CONDITION` from t（加反引号）；别名nextVal/minValue等sequence特殊词改为普通词"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        text = clean_sql(parsed.raw_sql)
        if _RE_SEQ_KEYWORDS.search(text):
            return self._make_violation("别名使用了TDSQL保留字或sequence特殊词（condition/nextval/currval等），需加反引号或改用普通别名")
        if _RE_CONDITION_BARE.search(text):
            # 检查是否已被反引号包裹
            raw_lower = parsed.raw_sql.lower()
            if re.search(r"(?<!`)condition(?!`)", raw_lower) and re.search(r"\bcondition\b", text):
                return self._make_violation("使用了CONDITION保留字作为标识符，需加反引号 `CONDITION`")
        return None


class R102EscapeBackslash(BaseRule):
    """R102: ESCAPE '\' 转义符"""
    rule_id = "R102"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.WARNING
    description = "LIKE…ESCAPE '\\'在TDSQL中行为与Oracle不一致，建议改用其他转义符（如'/'）"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - ESCAPE转义符"
    fix_suggestion = "LIKE '%\\_%' ESCAPE '\\' → LIKE '%/_%' ESCAPE '/'"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        text = _strip_comments_only(parsed.raw_sql)
        if _RE_ESCAPE_BS.search(text):
            return self._make_violation("LIKE...ESCAPE '\\'在TDSQL中行为不一致，建议改用'/'等转义符")
        return None


class R103OperatorSpace(BaseRule):
    """R103: 比较运算符中间含空格"""
    rule_id = "R103"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.ERROR
    description = "比较运算符中间不能有空格（如\"< =\"、\"> =\"、\"! =\"、\"< >\"），TDSQL会语法报错"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - 运算符空格"
    fix_suggestion = "\"< =\" → \"<=\"；\"> =\" → \">=\"；\"! =\" → \"!=\"；\"< >\" → \"<>\""
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        text = clean_sql(parsed.raw_sql)
        if _RE_OP_SPACE.search(text):
            return self._make_violation("比较运算符中间含空格（如< =、> =），TDSQL会语法报错")
        return None


class R104FunctionParenSpace(BaseRule):
    """R104: 函数与括号间空格/全角括号"""
    rule_id = "R104"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.ERROR
    description = "常用函数名与括号之间不能有空格（如SUM (、COUNT （），且禁止使用全角括号"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - 函数空格与全角括号"
    fix_suggestion = "SUM (→SUM(；COUNT （→COUNT(；全角（）改半角()"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        # 全角括号检测（raw_sql原文）
        if _RE_FULLWIDTH_PAREN.search(parsed.raw_sql):
            return self._make_violation("检测到全角括号（），请改为半角括号()")
        text = clean_sql(parsed.raw_sql)
        if _RE_FUNC_PAREN_SPACE.search(text):
            return self._make_violation("函数名与括号之间含空格（如SUM (），TDSQL会语法报错")
        return None


class R105OracleOuterJoin(BaseRule):
    """R105: Oracle (+) 外连接语法"""
    rule_id = "R105"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.ERROR
    description = "不支持Oracle的(+)外连接语法，请改写为LEFT/RIGHT OUTER JOIN"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - (+)外连接改写"
    fix_suggestion = "WHERE a.k=b.k(+)→FROM a LEFT JOIN b ON a.k=b.k（(+)在等号右侧为LEFT JOIN，在左侧为RIGHT JOIN）"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        text = clean_sql(parsed.raw_sql)
        if _RE_ORACLE_OUTER.search(text):
            return self._make_violation("检测到Oracle(+)外连接语法，TDSQL不支持，请改写为LEFT/RIGHT JOIN")
        return None


class R106ConnectBy(BaseRule):
    """R106: START WITH…CONNECT BY层级查询"""
    rule_id = "R106"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.ERROR
    description = "不支持Oracle层级查询START WITH…CONNECT BY：集中式可用WITH RECURSIVE改写，分布式需应用代码实现递归"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - CONNECT BY改写"
    fix_suggestion = "集中式8.0→WITH RECURSIVE（注意：返回为层次遍历序，Oracle为前序遍历）；分布式→应用先查必要信息再递归调用"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        text = clean_sql(parsed.raw_sql)
        if _RE_CONNECT_BY.search(text):
            return self._make_violation("检测到CONNECT BY层级查询，TDSQL不支持，请改用WITH RECURSIVE（集中式）或应用代码递归")
        return None


class R107InsertSelectRestriction(BaseRule):
    """R107: INSERT INTO…SELECT受限"""
    rule_id = "R107"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.WARNING
    description = "INSERT INTO…SELECT在目标表含自增列或分区时不支持，请执行前确认目标表结构"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - INSERT SELECT限制"
    fix_suggestion = "确认目标表无自增列且未分区；受限场景改为程序分批SELECT后INSERT VALUES"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.sql_type not in ("INSERT", "REPLACE"):
            return None
        text = clean_sql(parsed.raw_sql)
        if _RE_INSERT_SELECT.search(text):
            return self._make_violation("INSERT INTO…SELECT在目标表含自增列或分区时不支持，请确认目标表结构")
        return None


class R108SequenceBatchFetch(BaseRule):
    """R108: sequence批量获取不支持"""
    rule_id = "R108"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.ERROR
    description = "TDSQL的sequence在多行SELECT/INSERT…SELECT中不支持批量递增获取（多行返回相同值或直接报错）"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - sequence批量获取"
    fix_suggestion = "方案1：改自增序列；方案2：先insert into select落中间表，代码批量取序列二次赋值；方案3：预生成批量序列表"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        text = clean_sql(parsed.raw_sql)
        if not _RE_NEXTVAL_CURRVAL.search(text):
            return None
        # 单行取号（from dual）豁免
        if parsed.sql_type == "SELECT" and "from dual" in text:
            return None
        if (parsed.sql_type == "SELECT" and "from" in text) or \
           (parsed.sql_type in ("INSERT", "REPLACE") and "select" in text):
            return self._make_violation("TDSQL的sequence在多行查询中不支持批量递增获取，请改用其他方案")
        return None


class R109UpdateCaseWhenOrder(BaseRule):
    """R109: UPDATE多字段CASE WHEN求值顺序差异"""
    rule_id = "R109"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.WARNING
    description = "UPDATE中后续SET的CASE WHEN会读到前面SET字段的新值（与Oracle读旧值不同），判断条件字段的赋值应放到最后"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - UPDATE CASE WHEN顺序"
    fix_suggestion = "将修改判断条件字段（如stcd）的CASE WHEN赋值移到SET列表最后，或拆分为多条UPDATE"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not parsed.ast:
            return None
        try:
            if not isinstance(parsed.ast, exp.Update):
                return None
            exprs = parsed.ast.expressions
            assigned = set()
            for i, eq in enumerate(exprs):
                if i >= 1:
                    cols = eq.find_all(exp.Column)
                    for c in cols:
                        if c.name.lower() in assigned:
                            return self._make_violation("UPDATE中后续SET引用了前面已赋值字段的新值，TDSQL与Oracle行为不同")
                # 记录已赋值的目标列
                try:
                    assigned.add(eq.this.name.lower())
                except Exception:
                    pass
        except Exception:
            pass
        return None


class R110UserEnv(BaseRule):
    """R110: 禁止使用USERENV"""
    rule_id = "R110"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.ERROR
    description = "TDSQL不支持USERENV()系统上下文函数，系统级参数请从应用侧获取"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - USERENV不支持"
    fix_suggestion = "USERENV('INSTANCE')/USERENV('SID')等逻辑迁移到应用侧实现"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        text = clean_sql(parsed.raw_sql)
        if _RE_USERENV.search(text):
            return self._make_violation("检测到USERENV()，TDSQL不支持，请从应用侧获取系统级参数")
        return None


class R111WindowFunction(BaseRule):
    """R111: 分布式不支持窗口函数"""
    rule_id = "R111"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.ERROR
    description = "分布式实例不支持窗口函数（row_number()/rank()等 OVER()），需改写为分组+嵌套查询或应用侧处理"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - 窗口函数改写"
    fix_suggestion = "普通查询→分组/嵌套查询+排序改写；复杂嵌套→拆解落中间表；或JDK8 stream在应用侧sort/distinct"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        text = clean_sql(parsed.raw_sql)
        if _RE_WINDOW_OVER.search(text):
            return self._make_violation("检测到窗口函数(xxx() OVER())，分布式实例不支持，请改写为分组+嵌套查询")
        return None


class R112CursorUsage(BaseRule):
    """R112: 分布式不支持游标"""
    rule_id = "R112"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.ERROR
    description = "TDSQL分布式不支持游标（DECLARE…CURSOR/FETCH），请改用键集翻页或流式查询"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - 游标限制"
    fix_suggestion = "方案1：WHERE cond AND col>lastval ORDER BY col LIMIT N键集翻页；方案2：JDBC/ODBC流式查询；方案3：分片透传；方案4：TDSQL-PG版"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        text = clean_sql(parsed.raw_sql)
        if _RE_CURSOR_DECL.search(text) or _RE_CURSOR_FETCH.search(text):
            return self._make_violation("检测到游标用法，TDSQL分布式不支持，请改用键集翻页或流式查询")
        return None


class R113DropPartitionRisk(BaseRule):
    """R113: 删除分区高并发风险提示"""
    rule_id = "R113"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.INFO
    description = "高并发下DROP PARTITION与路由元数据更新存在毫秒级间隙，小概率报分区不存在；请逐表执行drop+analyze并配置重试"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - 删除分区注意事项"
    fix_suggestion = "将多表批量drop partition后统一analyze，改为逐表\"drop partition→analyze table\"循环"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        text = clean_sql(parsed.raw_sql)
        if _RE_DROP_PARTITION.search(text):
            return self._make_violation("DROP PARTITION在高并发下可能与路由元数据更新存在间隙，建议逐表执行并配置重试")
        return None


class R114DeepPagination(BaseRule):
    """R114: 深分页大偏移"""
    rule_id = "R114"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.WARNING
    description = "LIMIT大偏移分页在分布式实例代价高（proxy聚合各分片），请用索引有序性/键集翻页/条件初筛优化"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - 分页问题"
    fix_suggestion = "强排序分页→利用索引有序性；不排序分页→随机选分片返回单页；叠加日期/分类条件缩减初筛量级；最优为键集翻页where col>lastval limit N"
    enabled = True
    DEEP_PAGE_OFFSET = 10000

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.limit_offset > self.DEEP_PAGE_OFFSET:
            return self._make_violation(f"LIMIT偏移量{parsed.limit_offset}超过{self.DEEP_PAGE_OFFSET}，分布式实例深分页代价高")
        return None


class R115PrimaryKeyLength(BaseRule):
    """R115: 主键长度限制"""
    rule_id = "R115"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.WARNING
    description = "分布式实例update/delete…limit依赖proxy内嵌myisam临时表（索引限1000字节），utf8mb4下主键varchar长度须<250"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - 主键长度限制"
    fix_suggestion = "需要使用update/delete…limit语法的表，主键varchar长度限制在250以内(utf8mb4)/333以内(utf8)"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not parsed.is_create_table:
            return None
        # 找主键列
        pk_cols = []
        for col in parsed.columns:
            if col.get("is_primary_key"):
                pk_cols.append(col)
        # 从indexes中取表级主键
        if not pk_cols:
            for idx in parsed.indexes:
                if idx.get("type", "").upper() == "PRIMARY":
                    for cn in idx.get("columns", []):
                        for col in parsed.columns:
                            if col.get("name", "").lower() == cn.lower():
                                pk_cols.append(col)
        for col in pk_cols:
            col_type = col.get("type", "").upper()
            col_len = col.get("length", 0)
            if col_type in ("VARCHAR", "CHAR") and col_len and col_len > 250:
                return self._make_violation(f"主键字段 {col.get('name','')} 长度{col_len}超过250(utf8mb4)，update/delete..limit将受限")
        return None


class R116ShardKeySingleColumn(BaseRule):
    """R116: 分片键仅支持单字段"""
    rule_id = "R116"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.ERROR
    description = "分片键只支持一个字段，不支持多字段联合分片键"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - 分片键六条军规"
    fix_suggestion = "选择最常用于查询过滤/关联的单一字段作为shardkey"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not (parsed.is_create_table or parsed.is_alter_table):
            return None
        text = clean_sql(parsed.raw_sql)
        m = _RE_SHARDKEY.search(text)
        if m:
            parts = [p.strip() for p in m.group(1).split(",")]
            if len(parts) > 1:
                return self._make_violation("分片键只支持一个字段，不支持多字段联合分片键")
        return None


class R117ShardKeyType(BaseRule):
    """R117: 分片键字段类型限制"""
    rule_id = "R117"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.ERROR
    description = "shardkey字段类型必须是int/bigint/smallint/char/varchar"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - 分片键六条军规"
    fix_suggestion = "shardkey改用int/bigint/smallint/char/varchar类型；另注意：shardkey值不应含中文"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not (parsed.is_create_table or parsed.is_alter_table):
            return None
        text = clean_sql(parsed.raw_sql)
        m = _RE_SHARDKEY_SINGLE.search(text)
        if m:
            col_name = m.group(1)
            for col in parsed.columns:
                if col.get("name", "").lower() == col_name.lower():
                    col_type = col.get("type", "").upper()
                    if col_type and col_type not in _SHARD_KEY_TYPES:
                        return self._make_violation(f"shardkey字段 {col_name} 类型为{col_type}，不在许可类型(int/bigint/smallint/char/varchar)内")
        return None


class R118ShardKeyNotNull(BaseRule):
    """R118: 分片键必须NOT NULL"""
    rule_id = "R118"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.ERROR
    description = "shardkey字段的值不能为NULL，建表时必须显式NOT NULL约束"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - 分片键六条军规"
    fix_suggestion = "shardkey字段加NOT NULL；同时提醒：分片键值不能更新（已有R021约束DML侧）"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not (parsed.is_create_table or parsed.is_alter_table):
            return None
        text = clean_sql(parsed.raw_sql)
        m = _RE_SHARDKEY_SINGLE.search(text)
        if m:
            col_name = m.group(1)
            for col in parsed.columns:
                if col.get("name", "").lower() == col_name.lower():
                    if not col.get("is_not_null") and not col.get("is_primary_key"):
                        return self._make_violation(f"shardkey字段 {col_name} 未声明NOT NULL，分片键值不能为NULL")
        return None


class R119DateArithmetic(BaseRule):
    """R119: 日期函数直接加减数字"""
    rule_id = "R119"
    category = RuleCategory.ORACLE_COMPAT
    severity = Severity.WARNING
    description = "日期值直接±数字（如sysdate()-15）在TDSQL中语义与Oracle不同，请改用DATE_ADD/DATE_SUB(… INTERVAL n DAY)"
    spec_source = "ORACLE迁移TDSQL改造适配方案 V1.5.1 - 日期算术"
    fix_suggestion = "date_format(sysdate()-15,'%Y%m%d')→date_format(date_add(sysdate(), interval -15 day),'%Y%m%d')"
    enabled = True

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        text = clean_sql(parsed.raw_sql)
        if _RE_DATE_ARITH.search(text):
            return self._make_violation("日期函数直接±数字，TDSQL语义与Oracle不同，请改用DATE_ADD/DATE_SUB(... INTERVAL n DAY)")
        return None
