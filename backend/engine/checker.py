"""
TDSQL SQL审核工具 - 规则检查器

核心审核引擎：解析SQL → 加载规则 → 执行检查 → 汇总结果。
"""
import re
from typing import Optional

from backend.engine.parser import ParsedSQL, SQLParser
from backend.engine.parser.parser_legacy import _strip_comments_and_literals
from backend.engine.rules import ALL_RULE_CLASSES
from backend.engine.rules.base import BaseRule
from backend.models import (
    AuditResult, AuditSummary, Violation, RuleCategory,
)


class RuleChecker:
    """规则检查器 - 核心审核引擎"""

    def __init__(self, dialect: str = "mysql"):
        self.parser = SQLParser(dialect=dialect)
        self.rules: list[BaseRule] = self._load_default_rules()

    def _load_default_rules(self) -> list[BaseRule]:
        """加载全部119条规则"""
        return [cls() for cls in ALL_RULE_CLASSES]

    def get_enabled_rules(self, rule_overrides: Optional[dict] = None,
                          instance_type: Optional[str] = None) -> list[BaseRule]:
        """获取本次实际生效的规则（V1.5：适用域过滤的唯一收口点，INV-1）。

        两层过滤，串联，方向不对称：
          1) 适用域过滤（V1.5，客观）：规则在该类型实例上物理上是否有意义
          2) 规则集过滤（V1.4，主观）：管理员是否愿意查这条

        INV-2：适用域只做减法。规则集可以关掉一条适用的规则，
        但绝不能打开一条不适用的规则。

        Args:
            rule_overrides: {rule_id: {"enabled": bool, "severity_override": str|None}}
            instance_type: "distributed" | "centralized"；None 表示不做适用域过滤
                           （仅用于 get_rules_info 等纯展示场景）
        """
        result = []
        for r in self.rules:
            # 1) 适用域（客观事实）
            if instance_type is not None and not self._scope_match(r, instance_type):
                continue
            # 2) 规则集（主观尺度）
            override = rule_overrides.get(r.rule_id) if rule_overrides else None
            enabled = override["enabled"] if override else r.enabled
            if enabled:
                result.append(r)
        return result

    @staticmethod
    def _scope_match(rule: BaseRule, instance_type: str) -> bool:
        """唯一判定式：适用域为 ALL，或与实例类型相等。"""
        scope = getattr(rule, "instance_scope", None)
        scope = getattr(scope, "value", scope) or "all"
        return scope == "all" or scope == instance_type

    def count_skipped_by_scope(self, instance_type: Optional[str]) -> int:
        """统计因适用域不匹配而跳过的规则数，供报告横幅使用。"""
        if instance_type is None:
            return 0
        return sum(1 for r in self.rules if not self._scope_match(r, instance_type))

    def get_rules_info(self) -> list[dict]:
        """
        获取所有规则的详细信息列表。
        
        Returns:
            规则信息列表，每个规则包含：
            - rule_id: 规则ID
            - category: 规则类别
            - severity: 严重级别
            - description: 规则描述
            - enabled: 是否启用
        """
        return [
            {
                "rule_id": r.rule_id,
                "category": r.category.value if hasattr(r.category, 'value') else str(r.category),
                "severity": r.severity.value if hasattr(r.severity, 'value') else str(r.severity),
                "description": r.description,
                "enabled": r.enabled,
                "spec_source": getattr(r, 'spec_source', ''),
                "fix_suggestion": getattr(r, 'fix_suggestion', ''),
                # V1.5：规则固有属性，无条件返回（供规则管理页展示适用域列）
                "instance_scope": getattr(getattr(r, 'instance_scope', None), 'value', 'all'),
            }
            for r in self.rules
        ]

    def get_rules_by_category(self) -> dict:
        """
        按类别分组获取规则统计。
        
        Returns:
            分类统计字典，key为类别，value为该类别的规则列表
        """
        categories = {}
        for r in self.rules:
            cat = r.category.value if hasattr(r.category, 'value') else str(r.category)
            if cat not in categories:
                categories[cat] = []
            categories[cat].append({
                "rule_id": r.rule_id,
                "severity": r.severity.value if hasattr(r.severity, 'value') else str(r.severity),
                "description": r.description,
                # V1.5：适用域（供规则管理页展示"通用/仅分布式"列）
                "instance_scope": getattr(getattr(r, 'instance_scope', None), 'value', 'all'),
            })
        return categories

    def audit_sql(self, sql: str, file_path: str = "", line_number: Optional[int] = None,
                  table_metadata: Optional[dict] = None,
                  rule_overrides: Optional[dict] = None,
                  instance_type: Optional[str] = None) -> AuditResult:
        """
        审核单条 SQL。

        Args:
            sql: 待审核的 SQL 语句
            file_path: 来源文件路径（可选）
            line_number: 行号（可选）
            table_metadata: 表元数据字典（可选），用于分布式规则增强。
                           格式: {"table_name": {"shard_key": "...", "is_shard_table": True, ...}}
            rule_overrides: 规则集覆盖（V2.0多租户，可选），按规则集调整启停/级别

        Returns:
            AuditResult 审核结果
        """
        parsed = self.parser.parse(sql)
        violations: list[Violation] = []

        # 语法解析报错或结构不全时直接报 ERROR（排除存储过程/触发器/视图及 LOAD DATA 特殊语法）
        #
        # v1.6.2.2-UAT-O-01（R1）：豁免判定改为两段制，KFN 不可被豁免。
        # v1.6.2.2-UAT-O-01-R2：KFN 判定改为读结构化信号——
        #   known_fidelity_failures 由 parser preflight 在词法化阶段写入，
        #   覆盖包括 ParseError 提前 return 在内的全部返回路径，是唯一真值源；
        #   消息字符串 marker（KNOWN_FIDELITY_GAP/UNIQUE_SEMANTICS_INCOMPLETE）
        #   仅作向后兼容的展示通道。只凭消息判定时，异常路径会缺 marker 而漏判。
        # 普通解析错误的特殊语句豁免不再信任 sql_type/has_load_data——
        #   UNKNOWN/Command 路径下它们来自全文正则回退，会被
        #   COMMENT='LOAD DATA' 或 'CREATE VIEW' 字符串诱饵污染；
        #   改用「剥离注释与字符串字面量后的顶层语句头」判定。
        _KFN_MARKERS = ("KNOWN_FIDELITY_GAP", "UNIQUE_SEMANTICS_INCOMPLETE")
        is_kfn = bool(getattr(parsed, "known_fidelity_failures", None)) or bool(
            parsed.parse_error and any(m in parsed.parse_error for m in _KFN_MARKERS))
        if parsed.parse_error and not is_kfn:
            _head = _strip_comments_and_literals(sql)
            is_proc_or_trigger = bool(re.match(
                r"\s*create\s+(?:or\s+replace\s+)?(?:definer\s*=\s*\S+\s+)?"
                r"(?:view|procedure|function|trigger)\b", _head, re.IGNORECASE))
            is_load_stmt = bool(re.match(r"\s*load\s+(?:data|xml)\b", _head, re.IGNORECASE))
        else:
            is_proc_or_trigger = False
            is_load_stmt = False
        if parsed.parse_error and not is_proc_or_trigger and not is_load_stmt:
            violations.append(Violation(
                rule_id="E999_SYNTAX_ERROR",
                category=RuleCategory.DDL if ("CREATE" in sql.upper() or "ALTER" in sql.upper()) else RuleCategory.DML,
                severity="ERROR",
                message=f"SQL 语句无法解析或结构不完整（可能是拉取截断/语法错误）: {parsed.parse_error}",
                line_number=line_number,
            ))

        is_ddl_sql = (parsed.is_create_table or parsed.is_alter_table or parsed.sql_type in ("CREATE", "ALTER", "DROP", "TRUNCATE", "RENAME") or any(k in sql.upper() for k in ("CREATE", "ALTER", "DROP", "TRUNCATE", "RENAME")))
        for rule in self.get_enabled_rules(rule_overrides, instance_type):
            # DDL 规则只在 DDL 语句时检查
            if rule.category.value == "ddl" and not is_ddl_sql:
                continue
            try:
                violation = rule.check(parsed, table_metadata=table_metadata)
                if violation is not None:
                    # 确保行号信息传递
                    if violation.line_number is None and line_number is not None:
                        violation.line_number = line_number
                    # V2.0: 规则集级别覆盖
                    if rule_overrides:
                        override = rule_overrides.get(rule.rule_id)
                        if override and override.get("severity_override"):
                            violation.severity = override["severity_override"]
                    violations.append(violation)
            except Exception as e:
                # 规则执行异常时记录为 WARNING
                violations.append(Violation(
                    rule_id=rule.rule_id,
                    category=rule.category,
                    severity="WARNING",
                    message=f"规则 {rule.rule_id} 执行异常: {str(e)}",
                ))

        # 去重（R013/R014 可能产生重复）
        violations = self._deduplicate_violations(violations)

        return AuditResult(
            sql=sql.strip(),
            sql_type=parsed.sql_type,
            passed=len(violations) == 0,
            violations=violations,
            file_path=file_path,
            line_number=line_number,
        )

    def audit_file(self, content: str, file_path: str = "",
                   rule_overrides: Optional[dict] = None,
                   instance_type: Optional[str] = None) -> list[AuditResult]:
        """
        审核文件内容（支持 MyBatis XML、纯 SQL 文件）。

        Args:
            content: 文件内容
            file_path: 文件路径
            rule_overrides: 规则集覆盖（V2.0多租户，可选）

        Returns:
            审核结果列表
        """
        results: list[AuditResult] = []

        if file_path.lower().endswith(".xml"):
            # MyBatis XML 文件
            sqls = self._extract_sql_from_mybatis(content)
            for sql_text, line_no in sqls:
                result = self.audit_sql(sql_text, file_path=file_path, line_number=line_no,
                                        rule_overrides=rule_overrides,
                                        instance_type=instance_type)
                results.append(result)
        else:
            # 纯 SQL 文件：按分号分割
            sqls = self._split_sql_file(content)
            for sql_text, line_no in sqls:
                result = self.audit_sql(sql_text, file_path=file_path, line_number=line_no,
                                        rule_overrides=rule_overrides,
                                        instance_type=instance_type)
                results.append(result)

        return results

    def compute_summary(self, results: list[AuditResult]) -> AuditSummary:
        """计算审核汇总"""
        total = len(results)
        passed = sum(1 for r in results if r.passed)
        failed = total - passed
        error_count = sum(1 for r in results for v in r.violations if v.severity == "ERROR")
        warning_count = sum(1 for r in results for v in r.violations if v.severity == "WARNING")
        pass_rate = (passed / total * 100) if total > 0 else 0.0

        return AuditSummary(
            total_sql=total,
            passed=passed,
            failed=failed,
            error_count=error_count,
            warning_count=warning_count,
            pass_rate=round(pass_rate, 2),
        )

    # ── 私有辅助方法 ─────────────────────────────────────

    def _deduplicate_violations(self, violations: list[Violation]) -> list[Violation]:
        """去重：相同 rule_id + 相同 message 只保留一条"""
        seen = set()
        deduped = []
        for v in violations:
            key = (v.rule_id, v.message)
            if key not in seen:
                seen.add(key)
                deduped.append(v)
        return deduped

    def _extract_sql_from_mybatis(self, content: str) -> list[tuple[str, int]]:
        """
        从 MyBatis XML 中提取 SQL 语句。

        匹配 <select>, <insert>, <update>, <delete> 标签中的内容。
        返回 [(sql, line_number), ...]
        """
        results = []
        # 匹配 <select|insert|update|delete ...>...</select|insert|update|delete>
        pattern = re.compile(
            r"<(select|insert|update|delete)\b[^>]*>(.*?)</\1>",
            re.DOTALL | re.IGNORECASE,
        )
        for match in pattern.finditer(content):
            sql_text = match.group(2).strip()
            if not sql_text:
                continue
            # 计算行号
            line_no = content[: match.start()].count("\n") + 1
            # 清理 MyBatis 动态标签 (#{} 替换为 ?)
            sql_clean = self._clean_mybatis_sql(sql_text)
            if sql_clean.strip():
                results.append((sql_clean, line_no))
        return results

    def _clean_mybatis_sql(self, sql: str) -> str:
        """清理 MyBatis 动态 SQL 标签及 XML 转义字符"""
        import html
        # 0. 剥离 CDATA 标签，保留其内部原始文本
        sql = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", sql, flags=re.DOTALL)

        # 1. 替换包裹标签为相应的 SQL 关键字或空格，保留其内部内容
        sql = re.sub(r"<where\b[^>]*>", " WHERE ", sql, flags=re.DOTALL | re.IGNORECASE)
        sql = re.sub(r"</where>", " ", sql, flags=re.IGNORECASE)
        
        sql = re.sub(r"<set\b[^>]*>", " SET ", sql, flags=re.DOTALL | re.IGNORECASE)
        sql = re.sub(r"</set>", " ", sql, flags=re.IGNORECASE)
        
        # 2. 剥离其他常用动态标签，仅保留其内部 SQL 内容
        strip_tags = ["if", "foreach", "choose", "when", "otherwise", "trim", "bind"]
        for tag in strip_tags:
            sql = re.sub(rf"<{tag}\b[^>]*>", " ", sql, flags=re.DOTALL | re.IGNORECASE)
            sql = re.sub(rf"</{tag}>", " ", sql, flags=re.IGNORECASE)
            
        # 3. 变量占位符替换
        # #{...} → ?
        sql = re.sub(r"#\{[^}]*\}", "?", sql)
        # ${...} → ? (也替换，但有SQL注入风险，审核时可额外警告)
        sql = re.sub(r"\$\{[^}]*\}", "?", sql)
        
        # 4. XML / HTML 转义实体解码（&gt; -> >, &lt; -> <, &amp; -> & 等）
        sql = html.unescape(sql)

        # 5. 语法修复（如去除多余的 AND/OR, 逗号等）
        # 针对 <where> 剥离后可能产生的 WHERE AND 或 WHERE OR
        sql = re.sub(r"\bWHERE\s+(?:AND|OR)\b", "WHERE ", sql, flags=re.IGNORECASE)
        # 针对 <set> 剥离后可能产生的 SET , 
        sql = re.sub(r"\bSET\s*,", "SET ", sql, flags=re.IGNORECASE)
        # 去除连续的空白字符
        sql = re.sub(r"\s+", " ", sql)
        
        return sql.strip()


    def _split_sql_file(self, content: str) -> list[tuple[str, int]]:
        """
        智能拆分 SQL 脚本文件为多条独立的 SQL 语句。
        1. 动态支持 DELIMITER // 或 DELIMITER $$ 等自定义分隔符。
        2. 智能识别存储过程/函数/触发器中的 BEGIN...END 块与内部分号，防止语句被错误截断。
        3. 自动剥离全局横幅头注释（如 -- ===），保留单条 SQL 的紧邻注释及完整代码。
        4. 精确记录每条 SQL 语句在源文件中的起始行号。
        返回 [(sql, line_number), ...]
        """
        statements = []
        current_delimiter = ';'
        lines = content.splitlines(keepends=True)
        current_stmt = []
        line_no = 1
        stmt_start_line = 1
        
        in_begin_block = False
        
        for l in lines:
            stripped_line = l.strip()
            
            # 1. 检查 DELIMITER 切换指令
            delim_match = re.match(r'^DELIMITER\s+(\S+)', stripped_line, re.IGNORECASE)
            if delim_match:
                current_delimiter = delim_match.group(1)
                line_no += 1
                continue

            # 1.5 本系统抽取器为每条语句写入 `-- SQL Object:` 标记行
            #（见 backend/api/sql_audit.py）。上一条语句若因上游截断缺失
            # 分隔符，仅凭分隔符无法断开，会把下一条语句整体吞并、致其
            # 漏审（P2-04）——故将标记行视为隐式语句边界。
            if re.match(r'^--\s*SQL\s+Object\s*:', stripped_line, re.IGNORECASE) \
                    and not in_begin_block:
                pending = "".join(current_stmt).strip()
                cleaned_pending = re.sub(r'--[^\n]*', '', pending)
                cleaned_pending = re.sub(r'/\*.*?\*/', '', cleaned_pending,
                                         flags=re.DOTALL).strip()
                if cleaned_pending:   # 仅当积累了真实代码才断开；纯注释归属下一条
                    statements.append((pending, stmt_start_line))
                    current_stmt = []
                    stmt_start_line = line_no

            current_stmt.append(l)
            stmt_text = "".join(current_stmt)
            check_text = stmt_text.rstrip()
            
            # 2. 检查 BEGIN ... END 块状态（防止无 DELIMITER 声明或复杂过程体内的分号被错误切断）
            upper_text = re.sub(r'--[^\n]*', '', check_text)
            upper_text = re.sub(r'/\*.*?\*/', '', upper_text, flags=re.DOTALL).upper()
            
            if any(kw in upper_text for kw in ('CREATE PROCEDURE', 'CREATE TRIGGER', 'CREATE FUNCTION', 'CREATE EVENT')):
                if 'BEGIN' in upper_text and not 'END' in upper_text.split('BEGIN')[-1]:
                    in_begin_block = True
                elif 'END' in upper_text:
                    in_begin_block = False
            else:
                in_begin_block = False
                
            # 3. 检查语句是否达到当前分隔符（非 BEGIN 块内）
            if check_text.endswith(current_delimiter) and not in_begin_block:
                raw_sql = check_text[:-len(current_delimiter)].strip()
                
                cleaned = re.sub(r'--[^\n]*', '', raw_sql)
                cleaned = re.sub(r'/\*.*?\*/', '', cleaned, flags=re.DOTALL).strip()
                
                if cleaned:
                    # 过滤头部横幅注释（-- ===），保留每条语句真正的紧邻注释
                    lines_in_sql = raw_sql.splitlines()
                    first_code_idx = 0
                    for idx, sl in enumerate(lines_in_sql):
                        s_tr = sl.strip()
                        if s_tr and not s_tr.startswith('--') and not s_tr.startswith('/*'):
                            if idx > 0:
                                prev_comment = lines_in_sql[idx-1].strip()
                                if prev_comment.startswith('--') and '====' not in prev_comment:
                                    first_code_idx = idx - 1
                                else:
                                    first_code_idx = idx
                            else:
                                first_code_idx = idx
                            break
                    trimmed_sql = '\n'.join(lines_in_sql[first_code_idx:]).strip()
                    statements.append((trimmed_sql, stmt_start_line))
                    
                current_stmt = []
                stmt_start_line = line_no + 1
                
            line_no += 1
            
        if current_stmt:
            raw_sql = "".join(current_stmt).strip()
            cleaned = re.sub(r'--[^\n]*', '', raw_sql)
            cleaned = re.sub(r'/\*.*?\*/', '', cleaned, flags=re.DOTALL).strip()
            if cleaned:
                statements.append((raw_sql, stmt_start_line))
                
        return statements
