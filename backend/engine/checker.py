"""
TDSQL SQL审核工具 - 规则检查器

核心审核引擎：解析SQL → 加载规则 → 执行检查 → 汇总结果。
"""
import re
from typing import Optional

from backend.engine.parser import ParsedSQL, SQLParser
from backend.engine.rules import (
    R001NamingLength, R002ReservedKeywords,
    R003PrimaryKey, R004Engine, R005Charset, R006EnumSetType,
    R007TimestampType, R008ForeignKey, R009FinanceFloatType,
    R010VarcharLength, R011TextBlobType,
    R012SelectStar, R013DmlWithoutWhere, R014UpdateDeleteWithoutWhere,
    R015NestedSubquery, R016FunctionInWhere, R017OrderByRand,
    R018IndexCount, R019RedundantIndex,
    R020ShardKeyInWhere, R021ShardKeyUpdate, R022GlobalDeleteWithoutShardKey,
)
from backend.engine.rules.base import BaseRule
from backend.models import (
    AuditResult, AuditSummary, Violation,
)


class RuleChecker:
    """规则检查器 - 核心审核引擎"""

    def __init__(self, dialect: str = "mysql"):
        self.parser = SQLParser(dialect=dialect)
        self.rules: list[BaseRule] = self._load_default_rules()

    def _load_default_rules(self) -> list[BaseRule]:
        """加载默认规则集"""
        return [
            # 命名规范
            R001NamingLength(),
            R002ReservedKeywords(),
            # DDL 规范
            R003PrimaryKey(),
            R004Engine(),
            R005Charset(),
            R006EnumSetType(),
            R007TimestampType(),
            R008ForeignKey(),
            R009FinanceFloatType(),
            R010VarcharLength(),
            R011TextBlobType(),
            # DML 规范
            R012SelectStar(),
            R013DmlWithoutWhere(),
            R014UpdateDeleteWithoutWhere(),
            R015NestedSubquery(),
            R016FunctionInWhere(),
            R017OrderByRand(),
            R018IndexCount(),
            R019RedundantIndex(),
            # 分布式规范
            R020ShardKeyInWhere(),
            R021ShardKeyUpdate(),
            R022GlobalDeleteWithoutShardKey(),
        ]

    def get_enabled_rules(self) -> list[BaseRule]:
        """获取所有启用的规则"""
        return [r for r in self.rules if r.enabled]

    def audit_sql(self, sql: str, file_path: str = "", line_number: Optional[int] = None) -> AuditResult:
        """
        审核单条 SQL。

        Args:
            sql: 待审核的 SQL 语句
            file_path: 来源文件路径（可选）
            line_number: 行号（可选）

        Returns:
            AuditResult 审核结果
        """
        parsed = self.parser.parse(sql)
        violations: list[Violation] = []

        for rule in self.get_enabled_rules():
            # DDL 规则只在 CREATE/ALTER 时检查
            if rule.category.value == "ddl" and not (parsed.is_create_table or parsed.is_alter_table):
                continue
            try:
                violation = rule.check(parsed)
                if violation is not None:
                    # 确保行号信息传递
                    if violation.line_number is None and line_number is not None:
                        violation.line_number = line_number
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

    def audit_file(self, content: str, file_path: str = "") -> list[AuditResult]:
        """
        审核文件内容（支持 MyBatis XML、纯 SQL 文件）。

        Args:
            content: 文件内容
            file_path: 文件路径

        Returns:
            审核结果列表
        """
        results: list[AuditResult] = []

        if file_path.lower().endswith(".xml"):
            # MyBatis XML 文件
            sqls = self._extract_sql_from_mybatis(content)
            for sql_text, line_no in sqls:
                result = self.audit_sql(sql_text, file_path=file_path, line_number=line_no)
                results.append(result)
        else:
            # 纯 SQL 文件：按分号分割
            sqls = self._split_sql_file(content)
            for sql_text, line_no in sqls:
                result = self.audit_sql(sql_text, file_path=file_path, line_number=line_no)
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
        """清理 MyBatis 动态 SQL 标签"""
        # 移除 XML 动态标签
        sql = re.sub(r"<if\b[^>]*>.*?</if>", "", sql, flags=re.DOTALL)
        sql = re.sub(r"<where\b[^>]*>.*?</where>", " WHERE ", sql, flags=re.DOTALL)
        sql = re.sub(r"<set\b[^>]*>.*?</set>", " SET ", sql, flags=re.DOTALL)
        sql = re.sub(r"<foreach\b[^>]*>.*?</foreach>", "?", sql, flags=re.DOTALL)
        sql = re.sub(r"<choose>.*?</choose>", "?", sql, flags=re.DOTALL)
        sql = re.sub(r"<when\b[^>]*>.*?</when>", "", sql, flags=re.DOTALL)
        sql = re.sub(r"<otherwise>.*?</otherwise>", "", sql, flags=re.DOTALL)
        sql = re.sub(r"<trim\b[^>]*>.*?</trim>", "", sql, flags=re.DOTALL)
        # #{...} → ?
        sql = re.sub(r"#\{[^}]*\}", "?", sql)
        # ${...} → ? (也替换，但有SQL注入风险，审核时可额外警告)
        sql = re.sub(r"\$\{[^}]*\}", "?", sql)
        return sql.strip()

    def _split_sql_file(self, content: str) -> list[tuple[str, int]]:
        """
        将 SQL 文件按分号分割为多条 SQL。
        返回 [(sql, line_number), ...]
        """
        results = []
        current_line = 1
        statements = content.split(";")
        for stmt in statements:
            stmt_stripped = stmt.strip()
            if not stmt_stripped:
                current_line += stmt.count("\n") + 1
                continue
            # 跳过纯注释语句（去掉注释后为空）
            # 移除行注释和块注释后检查是否还有实际SQL
            cleaned = re.sub(r"--[^\n]*", "", stmt_stripped)
            cleaned = re.sub(r"/\*.*?\*/", "", cleaned, flags=re.DOTALL)
            cleaned = cleaned.strip()
            if not cleaned:
                # 纯注释，跳过
                current_line += stmt.count("\n") + 1
                continue
            results.append((stmt_stripped, current_line))
            current_line += stmt.count("\n") + 1
        return results
