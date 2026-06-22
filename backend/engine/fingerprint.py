"""
TDSQL SQL审核工具 - SQL指纹引擎 (V1.0)

将SQL归一化为指纹形式，用于慢SQL聚合统计和TopN排序。
"""
import re
import hashlib
from typing import Optional


class FingerprintEngine:
    """SQL指纹归并引擎"""

    # 需要替换为?的模式
    STRING_PATTERN = re.compile(r"'(?:[^'\\]|\\.)*'")
    NUMBER_PATTERN = re.compile(r"\b\d+\b")
    HEX_PATTERN = re.compile(r"0x[0-9a-fA-F]+")
    IN_LIST_PATTERN = re.compile(r"\bIN\s*\([^)]+\)", re.IGNORECASE)
    VALUES_PATTERN = re.compile(r"\bVALUES\s*\(([^)]+)\)", re.IGNORECASE)

    def fingerprint(self, sql: str) -> str:
        """
        将SQL归一化为指纹。

        规则:
        1. 字符串字面量 → ?
        2. 数字字面量 → ?
        3. IN列表 → IN (?)
        4. VALUES列表 → VALUES (?)
        5. 转小写关键字
        6. 压缩多余空格
        7. 取MD5前16位作为指纹ID
        """
        if not sql:
            return ""

        normalized = sql.strip().rstrip(";")

        # 替换字符串
        normalized = self.STRING_PATTERN.sub("?", normalized)
        # 替换十六进制
        normalized = self.HEX_PATTERN.sub("?", normalized)
        # 替换IN列表（模式已编译时含IGNORECASE标志）
        normalized = self.IN_LIST_PATTERN.sub("IN (?)", normalized)
        # 替换VALUES列表
        normalized = self.VALUES_PATTERN.sub("VALUES (?)", normalized)
        # 替换数字（但保留LIMIT后的数字）
        normalized = re.sub(r"\bLIMIT\s+(\d+)", lambda m: f"LIMIT {m.group(1)}", normalized, flags=re.IGNORECASE)
        normalized = self.NUMBER_PATTERN.sub("?", normalized)
        # 恢复LIMIT数字
        normalized = re.sub(r"\bLIMIT\s+\?", lambda m: "LIMIT N", normalized, flags=re.IGNORECASE)

        # 转小写
        normalized = normalized.lower()
        # 压缩空格
        normalized = re.sub(r"\s+", " ", normalized).strip()

        return normalized

    def fingerprint_hash(self, sql: str) -> str:
        """获取SQL指纹的MD5哈希(前16位)"""
        fp = self.fingerprint(sql)
        return hashlib.md5(fp.encode()).hexdigest()[:16]

    def normalize_for_display(self, sql: str) -> str:
        """归一化用于展示（保留可读性）"""
        if not sql:
            return ""
        normalized = sql.strip().rstrip(";")
        # 仅替换字符串和数字
        normalized = self.STRING_PATTERN.sub("?", normalized)
        normalized = self.NUMBER_PATTERN.sub("?", normalized)
        # 压缩空格
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return normalized

    def extract_tables_from_sql(self, sql: str) -> list[str]:
        """从SQL文本中提取表名（正则方式）"""
        tables = []
        # FROM/JOIN/INTO/UPDATE 后的表名
        patterns = [
            r"\bfrom\s+([a-zA-Z_][a-zA-Z0-9_.]*)",
            r"\bjoin\s+([a-zA-Z_][a-zA-Z0-9_.]*)",
            r"\binto\s+([a-zA-Z_][a-zA-Z0-9_.]*)",
            r"\bupdate\s+([a-zA-Z_][a-zA-Z0-9_.]*)",
            r"\bdelete\s+from\s+([a-zA-Z_][a-zA-Z0-9_.]*)",
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, sql, re.IGNORECASE):
                table = match.group(1).strip("`\"")
                if table and table not in tables:
                    tables.append(table)
        return tables
