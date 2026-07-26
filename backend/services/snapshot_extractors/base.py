"""
扫描快照 — 问题项数据结构与指纹算法 v1

设计依据：docs/DETAIL-v1.3-扫描结果对比.md §3.1 / §3.3

【红线】指纹严禁包含 line_number、报告序号 #idx、自增 id、扫描时间。
       违反将导致比对全部误判为"新增"。
"""
import hashlib
import re
from dataclasses import dataclass, field, asdict

FINGERPRINT_ALGO = "v1"

_VOLATILE = re.compile(r"\d+|'[^']*'|\"[^\"]*\"|`[^`]*`")


@dataclass
class IssueItem:
    """比对的最小单元"""
    key: str                     # 稳定指纹，比对主键
    object_name: str = ""        # 归属对象：schema.table / SQL 指纹短标识
    object_type: str = ""        # TABLE | VIEW | INDEX | SQL | ''
    issue_type: str = ""         # rule_id / 慢SQL问题类型 / 大表问题类型
    severity: str = "WARNING"    # ERROR | WARNING | INFO
    title: str = ""              # 一行简述（报告主文案）
    detail: str = ""             # 详细描述
    suggestion: str = ""         # 修复建议
    attrs: dict = field(default_factory=dict)   # 可变属性，用于 CHANGED 判定

    def to_dict(self) -> dict:
        return asdict(self)


def fp(*parts: str) -> str:
    """指纹：各部分归一化后用 \\x1f 连接取 sha1 前 16 位"""
    norm = [(p or "").strip().lower() for p in parts]
    raw = "\x1f".join(norm)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def stable_text(msg: str) -> str:
    """归一化消息文本：去掉数字与引号内容等易变部分。

    用于同对象同规则多次命中时的区分位，保证跨扫描稳定。
    """
    return _VOLATILE.sub("#", msg or "").strip().lower()


def sha1_16(text: str) -> str:
    """取任意文本的 sha1 前 16 位（用于慢SQL fingerprint 压缩）"""
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:16]


# ── 对象名解析（schema_audit 专用，详见 DETAIL §3.3.3）──

_OBJ_RE = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:ALGORITHM\s*=\s*\w+\s+)?"
    r"(?:DEFINER\s*=\s*\S+\s+)?(?:SQL\s+SECURITY\s+\w+\s+)?"
    r"(TABLE|VIEW|INDEX)\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"[`\"]?([\w$]+)[`\"]?(?:\s*\.\s*[`\"]?([\w$]+)[`\"]?)?",
    re.IGNORECASE)

_ALTER_RE = re.compile(
    r"ALTER\s+TABLE\s+[`\"]?([\w$]+)[`\"]?(?:\s*\.\s*[`\"]?([\w$]+)[`\"]?)?",
    re.IGNORECASE)


def parse_object(sql: str, default_db: str = "") -> tuple[str, str]:
    """从 DDL 文本解析对象名。

    返回 (object_name='db.obj', object_type)；解析失败返回 ('<unparsed:hash>', '')。
    对象名位于语句开头，故 results_json 中被截断为 500 字符也不影响解析。
    """
    m = _OBJ_RE.search(sql or "")
    if m:
        otype = m.group(1).upper()
        a, b = m.group(2), m.group(3)
        db, obj = (a, b) if b else (default_db, a)
        return f"{db}.{obj}", otype
    m = _ALTER_RE.search(sql or "")
    if m:
        a, b = m.group(1), m.group(2)
        db, obj = (a, b) if b else (default_db, a)
        return f"{db}.{obj}", "TABLE"
    return f"<unparsed:{fp(stable_text(sql))[:8]}>", ""
