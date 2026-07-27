"""
明文凭据防复发守护

背景：`checksql` 的生产口令曾被直接写入 docs 并随提交进入 git 历史
（dbb0918 / 376f2e1）；测试口令也被反复硬编码进用例（BUG-130-04 修了两次、
被回退过一次）。文档清理只能处理当下，挡不住下次再写进去，故用本用例守住。

约定：
- 一切真实凭据经环境变量注入，代码/文档/脚本中只允许出现变量名；
- 测试口令同样走环境变量，仅保留本地开发用的默认值，且默认值必须登记在
  下方 _ALLOWED_TEST_DEFAULTS 中，登记即视为"确认此值不是生产凭据"。
"""
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent

# 扫描范围：源码、文档、脚本、部署配置
_SCAN_DIRS = ("backend", "docs", "tests", "tests_3p", "deploy", "scratch", "scripts")
_SCAN_SUFFIXES = (".py", ".md", ".sh", ".js", ".yml", ".yaml", ".service", ".conf")

# 已知的本地/测试环境默认值。登记在此即表示已确认非生产凭据。
# 新增条目前请确认：该值只在本地或测试环境有效，泄露不造成实际影响。
_ALLOWED_TEST_DEFAULTS = {
    "tdsql_test_2024",       # 本地 MariaDB(13306) root 口令，仅测试库
    "Abcd1234",              # 本环境测试 admin
    "Admin@1234",            # G 环境测试 admin
    "T3p#Passw0rd2026",      # tests_3p 自建角色账号
    "SmokeConn@1", "Secret@123", "Pw@12345",   # 单元测试构造值
    "Sec01#Pass123", "Sec01#Pass456", "Sec01#Pass789",
    "Sec02#Pass123", "Sit05#Pass123", "Init#Pass123", "New#Passw0rd9",
    "my_secret_password_123!@#",
}

# 已确认泄露、必须永不再出现的生产凭据（值本身不写在此处，用特征匹配）
_FORBIDDEN_PATTERNS = [
    (re.compile(r"Abcd972"), "checksql 生产口令（已泄露，须由 DBA 轮换）"),
]


def _iter_files():
    for d in _SCAN_DIRS:
        base = _ROOT / d
        if not base.exists():
            continue
        for f in base.rglob("*"):
            if f.is_file() and f.suffix in _SCAN_SUFFIXES:
                # 本文件自身含示例值，跳过
                if f.name == Path(__file__).name:
                    continue
                yield f


def test_no_known_leaked_production_credentials():
    """已确认泄露的生产凭据不得再出现在仓库任何文件中"""
    hits = []
    for f in _iter_files():
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for pat, desc in _FORBIDDEN_PATTERNS:
            for i, line in enumerate(text.splitlines(), 1):
                if pat.search(line):
                    hits.append(f"{f.relative_to(_ROOT)}:{i} — {desc}")
    assert not hits, (
        "检出已泄露的生产凭据，必须改为环境变量注入：\n  " + "\n  ".join(hits))


def test_password_literals_are_registered_test_values():
    """形如 password="xxx" 的明文字面量必须是已登记的测试值"""
    # 只匹配确定是"赋值一个字符串常量"的写法，避免误报函数形参、字典取值等
    pat = re.compile(
        r"""(?:password|passwd|pwd)\s*[=:]\s*["']([^"'{}$\s][^"']{5,})["']""",
        re.IGNORECASE)
    offenders = []
    for f in _iter_files():
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            # 环境变量读取、占位符、字段名声明不算硬编码
            if "os.getenv" in line or "os.environ" in line or "getenv(" in line:
                continue
            if stripped.startswith(("#", ">", "//", "*")):
                continue
            m = pat.search(line)
            if not m:
                continue
            val = m.group(1)
            if val in _ALLOWED_TEST_DEFAULTS:
                continue
            # 明显的占位/描述性文本不算
            if val.lower() in ("password", "your_password", "xxx", "changeme",
                               "<password>", "口令", "密码"):
                continue
            offenders.append(f"{f.relative_to(_ROOT)}:{i}  {stripped[:90]}")

    assert not offenders, (
        "检出未登记的明文口令字面量。请改为环境变量注入；\n"
        "若确属测试专用值，登记到 _ALLOWED_TEST_DEFAULTS 并确认其非生产凭据：\n  "
        + "\n  ".join(offenders))
