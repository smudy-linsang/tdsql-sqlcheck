# -*- coding: utf-8 -*-
"""UAT-O-18 回归测试：重复索引对方名称结构化，报告层不得只解析自然语言

覆盖 O 第四轮报告 O-15 同级缺陷 O-18（MAJOR）：
1. finding 记录携带 related_index_name / index_columns 结构化字段；
2. 消费端优先结构化字段；存量数据兼容两种文案格式；
3. 无结构化字段且两种格式都不匹配时才允许 N/A。
"""
import pytest

from backend.services.ppt_report_service import _duplicate_pair_fields


class TestDuplicatePairStructured:
    def test_structured_fields_preferred(self):
        row = {
            "related_index_name": "idx_code_copy",
            "index_columns": "code",
            # detail 即使被改成别的文案也不得影响结果
            "detail": "完全不同的文案",
        }
        assert _duplicate_pair_fields(row) == ("idx_code_copy", "code")

    def test_structured_beats_missing_metric(self):
        """旧实现 columns 取 metric（重复索引恒为空）→ 列清单必须来自结构化字段"""
        row = {"related_index_name": "k2", "index_columns": "a,b", "detail": ""}
        assert _duplicate_pair_fields(row) == ("k2", "a,b")


class TestDuplicatePairLegacyCompat:
    def test_current_production_text_format(self):
        """存量：生产端现行格式 '索引 A 与 B 列完全相同(columns)'"""
        row = {
            "related_index_name": "",
            "index_columns": "",
            "detail": "索引 idx_code 与 idx_code_copy 列完全相同(code)",
        }
        assert _duplicate_pair_fields(row) == ("idx_code_copy", "code")

    def test_current_format_multi_column(self):
        row = {
            "detail": "索引 idx_ab 与 idx_ab_dup 列完全相同(a,b)",
        }
        assert _duplicate_pair_fields(row) == ("idx_ab_dup", "a,b")

    def test_old_text_format(self):
        """存量：旧格式 '与 xxx 完全重复'"""
        row = {"detail": "与 idx_backup 完全重复"}
        assert _duplicate_pair_fields(row) == ("idx_backup", "")

    def test_old_format_backtick_name(self):
        row = {"detail": "与 `idx_backup` 完全重复"}
        assert _duplicate_pair_fields(row) == ("idx_backup", "")


class TestDuplicatePairFallback:
    def test_unknown_format_returns_na(self):
        row = {"detail": "无法识别的文案"}
        assert _duplicate_pair_fields(row) == ("N/A", "")

    def test_empty_detail_returns_na(self):
        assert _duplicate_pair_fields({}) == ("N/A", "")
