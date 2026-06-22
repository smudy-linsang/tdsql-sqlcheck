"""
TDSQL SQL审核工具 - 索引规范规则 (R061-R068)

V1.0新增: 8条索引设计规范规则。
"""
import re
from typing import Optional

from backend.engine.parser import ParsedSQL
from backend.engine.rules.base import BaseRule
from backend.models import RuleCategory, Severity, Violation


class R061IndexNaming(BaseRule):
    """R061: 索引命名规范"""
    rule_id = "R061"
    category = RuleCategory.INDEX
    severity = Severity.WARNING
    description = "索引命名应遵循规范: 普通索引idx_、唯一索引uk_、主键pk_"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 索引设计规范"
    fix_suggestion = "普通索引以idx_开头，唯一索引以uk_开头，主键以pk_开头"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not parsed.is_create_table:
            return None
        for idx in parsed.indexes:
            idx_name = idx.get("name", "").lower()
            idx_type = idx.get("type", "NORMAL")
            if not idx_name:
                continue
            if idx_type == "PRIMARY":
                if not idx_name.startswith("pk_"):
                    return self._make_violation(f"主键索引 '{idx_name}' 应以 pk_ 开头")
            elif idx_type == "UNIQUE":
                if not idx_name.startswith("uk_"):
                    return self._make_violation(f"唯一索引 '{idx_name}' 应以 uk_ 开头")
            else:
                if not idx_name.startswith("idx_"):
                    return self._make_violation(f"普通索引 '{idx_name}' 应以 idx_ 开头")
        return None


class R062CompositeIndexOrder(BaseRule):
    """R062: 复合索引字段顺序"""
    rule_id = "R062"
    category = RuleCategory.INDEX
    severity = Severity.WARNING
    description = "复合索引字段顺序应遵循最左前缀原则，区分度高的字段放前面"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 索引设计规范"
    fix_suggestion = "将区分度高的字段放在复合索引前面"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not parsed.is_create_table:
            return None
        for idx in parsed.indexes:
            cols = idx.get("columns", [])
            if len(cols) <= 1:
                continue
            # 简单启发式：id类字段通常区分度高，应放前面
            # 如果最后一个字段是id类而前面是status/type类，给出建议
            last_col = cols[-1].lower()
            first_col = cols[0].lower()
            low_cardinality = {"status", "type", "flag", "level", "category", "state", "is_"}
            if any(lc in first_col for lc in low_cardinality) and "id" in last_col:
                return self._make_violation(
                    f"复合索引({','.join(cols)})字段顺序不合理，区分度低的字段'{first_col}'在前",
                    suggestion=f"建议调整为: ({','.join(reversed(cols))})",
                )
        return None


class R063NoIndexOnLowCardinality(BaseRule):
    """R063: 低区分度字段不建议建索引"""
    rule_id = "R063"
    category = RuleCategory.INDEX
    severity = Severity.WARNING
    description = "区分度低的字段(如status/type/gender)不建议单独建索引"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 索引设计规范"
    fix_suggestion = "低区分度字段建议放入复合索引而非单独建索引"

    LOW_CARDINALITY_KEYWORDS = {"status", "type", "gender", "flag", "level", "category", "state", "is_deleted", "is_active"}

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not parsed.is_create_table:
            return None
        for idx in parsed.indexes:
            cols = idx.get("columns", [])
            if len(cols) == 1:
                col_name = cols[0].lower().strip('`"')
                if any(kw in col_name for kw in self.LOW_CARDINALITY_KEYWORDS):
                    return self._make_violation(
                        f"字段 '{cols[0]}' 区分度低，不建议单独建索引",
                        suggestion="建议将该字段放入复合索引",
                    )
        return None


class R064CoveringIndexSuggestion(BaseRule):
    """R064: 建议覆盖索引"""
    rule_id = "R064"
    category = RuleCategory.INDEX
    severity = Severity.INFO
    description = "高频查询建议使用覆盖索引避免回表"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 索引设计规范"
    fix_suggestion = "将SELECT的字段加入索引，实现覆盖索引避免回表"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if parsed.sql_type != "SELECT" or not parsed.select_fields:
            return None
        if parsed.has_wildcard_select:
            return None
        # 仅在有元数据时才能精确判断
        if not table_metadata:
            return None
        # 简化：如果有WHERE条件且查询字段较少，提示考虑覆盖索引
        if parsed.has_where and len(parsed.select_fields) <= 5:
            return self._make_violation(
                "查询字段较少，建议评估是否可通过覆盖索引避免回表",
            )
        return None


class R065IndexColumnCountLimit(BaseRule):
    """R065: 复合索引字段数不超过5"""
    rule_id = "R065"
    category = RuleCategory.INDEX
    severity = Severity.WARNING
    description = "复合索引字段数量建议不超过5个"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 索引设计规范"
    fix_suggestion = "复合索引字段过多会增加索引体积和维护成本，建议精简"

    MAX_COLUMNS = 5

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not parsed.is_create_table:
            return None
        for idx in parsed.indexes:
            cols = idx.get("columns", [])
            if len(cols) > self.MAX_COLUMNS:
                return self._make_violation(
                    f"索引 '{idx.get('name', '')}' 包含 {len(cols)} 个字段，超过建议的 {self.MAX_COLUMNS} 个",
                )
        return None


class R066NoIndexOnBlobText(BaseRule):
    """R066: 大字段禁止建索引"""
    rule_id = "R066"
    category = RuleCategory.INDEX
    severity = Severity.ERROR
    description = "TEXT/BLOB/JSON类型字段禁止建索引"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 索引设计规范"
    fix_suggestion = "大字段不支持直接索引，如需全文检索请使用FULLTEXT索引"

    LARGE_TYPES = {"TEXT", "TINYTEXT", "MEDIUMTEXT", "LONGTEXT", "BLOB", "TINYBLOB", "MEDIUMBLOB", "LONGBLOB", "JSON"}

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not parsed.is_create_table:
            return None
        # 构建列名→类型映射
        col_type_map = {}
        for col in parsed.column_types:
            col_type_map[col.get("name", "").lower()] = col.get("type", "").upper()
        # 检查每个索引的字段类型
        for idx in parsed.indexes:
            for col_name in idx.get("columns", []):
                col_type = col_type_map.get(col_name.lower().strip('`"'), "")
                if col_type in self.LARGE_TYPES:
                    return self._make_violation(
                        f"字段 '{col_name}' 类型为 {col_type}，禁止建索引",
                    )
        return None


class R067PrefixIndexSuggestion(BaseRule):
    """R067: 长字符串建议前缀索引"""
    rule_id = "R067"
    category = RuleCategory.INDEX
    severity = Severity.INFO
    description = "VARCHAR长度超过100的字段建议使用前缀索引"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 索引设计规范"
    fix_suggestion = "如: INDEX idx_name (col_name(20)) 使用前20字符建索引"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not parsed.is_create_table:
            return None
        col_length_map = {}
        for col in parsed.columns:
            col_type = col.get("type", "").upper()
            if col_type == "VARCHAR":
                length = col.get("length")
                if length and length > 100:
                    col_length_map[col.get("name", "").lower()] = length
        if not col_length_map:
            return None
        for idx in parsed.indexes:
            for col_name in idx.get("columns", []):
                if col_name.lower().strip('`"') in col_length_map:
                    return self._make_violation(
                        f"字段 '{col_name}' VARCHAR长度>{100}，建议使用前缀索引",
                    )
        return None


class R068SuggestIndexForForeignKey(BaseRule):
    """R068: 关联字段建议建索引"""
    rule_id = "R068"
    category = RuleCategory.INDEX
    severity = Severity.WARNING
    description = "JOIN关联字段建议建索引"
    enabled = True
    spec_source = "TDSQL数据库开发规范 - 索引设计规范"
    fix_suggestion = "为JOIN ON条件中的字段创建索引"

    def check(self, parsed: ParsedSQL, table_metadata: Optional[dict] = None) -> Optional[Violation]:
        if not parsed.has_explicit_join or len(parsed.tables) < 2:
            return None
        # 从SQL中提取JOIN ON条件的字段
        raw_lower = parsed.raw_sql.lower()
        on_match = re.findall(r"\bon\s+.*?(\w+)\s*=\s*(\w+)\.(\w+)", raw_lower)
        if not on_match:
            return None
        # 无元数据时仅提示
        if not table_metadata:
            return self._make_violation(
                "多表JOIN查询，请确保JOIN关联字段已建索引",
            )
        # 有元数据时检查
        for full_match in on_match:
            join_col = full_match[2] if len(full_match) > 2 else ""
            for table in parsed.tables:
                meta = table_metadata.get(table, {})
                indexes = meta.get("indexes", [])
                indexed_cols = set()
                for idx in indexes:
                    indexed_cols.update(c.lower() for c in idx.get("columns", []))
                if join_col and join_col.lower() not in indexed_cols:
                    return self._make_violation(
                        f"JOIN关联字段 '{join_col}' 在表 '{table}' 上可能无索引",
                    )
        return None
