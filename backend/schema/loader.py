"""
SQL 文件加载器：从 schema/vN/NNN_*.sql 动态读取解析 SQL 迁移文件
"""
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterator

SCHEMA_DIR = Path(__file__).parent
_VERSION_RE = re.compile(r"^v(\d+)$")
_FILE_RE = re.compile(r"^(\d{3})_(.+)\.sql$")


@dataclass(frozen=True)
class SchemaFile:
    version: int
    sequence: int
    name: str
    path: Path
    sql: str


def discover_schema_files() -> list[SchemaFile]:
    """扫描 schema/ 目录下所有版本 SQL 文件并按 (version, sequence) 升序排序"""
    schema_files = []
    if not SCHEMA_DIR.exists():
        return schema_files

    for vdir in sorted(SCHEMA_DIR.iterdir()):
        v_match = _VERSION_RE.match(vdir.name)
        if not v_match or not vdir.is_dir():
            continue
        version = int(v_match.group(1))
        for sfile in sorted(vdir.iterdir()):
            f_match = _FILE_RE.match(sfile.name)
            if not f_match:
                continue
            sequence = int(f_match.group(1))
            sql_content = sfile.read_text(encoding="utf-8")
            schema_files.append(SchemaFile(version, sequence, f_match.group(2), sfile, sql_content))
    schema_files.sort(key=lambda sf: (sf.version, sf.sequence))
    return schema_files
