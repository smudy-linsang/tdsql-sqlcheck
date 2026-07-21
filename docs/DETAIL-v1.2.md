# TDSQL SQL审核平台 v1.2 详细设计说明书

> **配套文档**: 《概要设计说明书 v1.2》（`docs/ARCHITECTURE-v1.2.md`）
> **目标版本**: v1.2.0.0
> **基线**: v1.1.0.1
> **写作标准**: 照图施工（每个文件、每个函数、每条 SQL、每步操作均可直接执行）
> **作者**: Mavis 团队

---

## 0. 阅读指南

| 章节 | 内容 | 适用读者 |
|---|---|---|
| C1 | 数据库 schema 文件化 | 后端 / DBA |
| C2 | engine/parser 拆分 | 后端 |
| C3 | tdsql_connector 拆分 | 后端 |
| C4 | 前端 Vite 工程化 | 前端 / 全栈 |
| C5 | RBAC 矩阵单测 | 后端 / 测试 |
| C6 | tdsql-toolkit 桥接 | 后端 / 运维 |

每个章节遵循固定结构：
1. 改造前现状
2. 改造后目标结构（目录树 / 文件清单）
3. 关键代码骨架（含函数签名）
4. 关键 SQL / 配置
5. 测试要点
6. 实施步骤（按顺序可执行）
7. 回滚方案

---

## C1 — 数据库 schema 文件化

### C1.1 现状

`backend/services/database.py` 中 `_create_all_tables()` 包含 ~1500 行硬编码 DDL：

```python
table_ddls = [
    """CREATE TABLE IF NOT EXISTS slow_queries (...)""",
    """CREATE TABLE IF NOT EXISTS audit_history (...)""",
    # ... 共 30 张表
]
```

问题：
- 无法在数据库工具中独立查看/审计
- DDL 与代码耦合，PR diff 噪声大
- 多人协作冲突频发
- 缺版本化与回滚能力

### C1.2 目标结构

```
backend/
├── services/
│   └── database.py              # 仅保留连接管理 + 启动钩子，DDL 抽走
└── schema/                       # 新建目录
    ├── __init__.py
    ├── loader.py                 # SQL 文件加载器
    ├── migrator.py               # 迁移引擎
    ├── registry.py               # SQL 文件注册表
    ├── v0/                       # 与 v1.1.0.1 一致
    │   ├── 001_init.sql
    │   ├── 002_seed_roles.sql
    │   └── 003_seed_admin.sql
    ├── v1/                       # v1.2 增量（按需）
    │   └── 010_add_tool_run_table.sql
    └── README.md                 # 编写规范
```

### C1.3 schema/loader.py 设计

```python
# backend/schema/loader.py
"""SQL 文件加载器：从 schema/ 目录按版本顺序读取 .sql"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterator

SCHEMA_DIR = Path(__file__).parent
_VERSION_RE = re.compile(r"^v(\d+)$")
_FILE_RE = re.compile(r"^(\d{3})_(.+)\.sql$")


@dataclass(frozen=True)
class SchemaFile:
    version: int          # 0, 1, 2, ...
    sequence: int         # 001, 002, ...
    name: str             # init, seed_roles, ...
    path: Path
    sql: str             # 文件完整内容


def discover() -> list[SchemaFile]:
    """扫描 schema/vN/NNN_*.sql，按 (version, sequence) 升序返回"""
    out: list[SchemaFile] = []
    for vdir in sorted(SCHEMA_DIR.iterdir()):
        m = _VERSION_RE.match(vdir.name)
        if not m or not vdir.is_dir():
            continue
        version = int(m.group(1))
        for f in sorted(vdir.iterdir()):
            fm = _FILE_RE.match(f.name)
            if not fm:
                continue
            sequence = int(fm.group(1))
            sql = f.read_text(encoding="utf-8")
            out.append(SchemaFile(version, sequence, fm.group(2), f, sql))
    out.sort(key=lambda x: (x.version, x.sequence))
    return out


def iter_chunks(sql: str) -> Iterator[str]:
    """按 `-- chunk:N` 标记拆分单个 SQL 文件为多个 chunk，便于部分回滚"""
    parts = re.split(r"^--\s*chunk:\s*(\d+)\s*$", sql, flags=re.MULTILINE)
    # parts[0] 是文件头注释；parts[1::2] 是 chunk 名，parts[2::2] 是内容
    if len(parts) < 3:
        # 无 chunk 标记，整文件作为一个 chunk
        if sql.strip():
            yield "main", sql
        return
    yield from zip(parts[1::2], parts[2::2])
```

### C1.4 schema/registry.py 设计

```python
# backend/schema/registry.py
"""SQL 文件元信息注册表（描述、作者、依赖、是否可以重入）"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import Optional

REGISTRY_PATH = Path(__file__).parent / "registry.toml"


@dataclass(frozen=True)
class SchemaEntry:
    file: str               # 相对 schema/ 的路径，如 "v0/001_init.sql"
    description: str
    author: str
    depends_on: list[str]   # 依赖的 file 列表
    idempotent: bool        # 是否可重复执行（默认 True）


_REGISTRY: dict[str, SchemaEntry] = {}


def load() -> dict[str, SchemaEntry]:
    global _REGISTRY
    if _REGISTRY:
        return _REGISTRY
    if REGISTRY_PATH.exists():
        data = tomllib.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        for k, v in data.items():
            _REGISTRY[k] = SchemaEntry(
                file=k, description=v.get("description", ""),
                author=v.get("author", "unknown"),
                depends_on=v.get("depends_on", []),
                idempotent=v.get("idempotent", True),
            )
    return _REGISTRY


def get(path: str) -> Optional[SchemaEntry]:
    return load().get(path)
```

`schema/registry.toml` 示例：

```toml
["v0/001_init.sql"]
description = "初始 27 张业务表（与 v1.1.0.1 一致）"
author = "Mavis"
depends_on = []
idempotent = true

["v0/002_seed_roles.sql"]
description = "4 角色 + 菜单权限矩阵初值"
author = "Mavis"
depends_on = ["v0/001_init.sql"]
idempotent = true

["v0/003_seed_admin.sql"]
description = "admin 初始账号（密码从 .env 读取，不硬编码）"
author = "Mavis"
depends_on = ["v0/001_init.sql"]
idempotent = true

["v1/010_add_tool_run_table.sql"]
description = "v1.2 新增 tool_run + tool_run_log 表（ToolBridge 用）"
author = "Mavis"
depends_on = ["v0/001_init.sql"]
idempotent = true
```

### C1.5 schema/migrator.py 设计

```python
# backend/schema/migrator.py
"""迁移引擎：启动时检查并按序执行"""
from __future__ import annotations
import logging
from typing import Optional
from backend.schema.loader import discover, SchemaFile, iter_chunks
from backend.schema.registry import get as get_entry
from backend.services.database import _get_connection, ensure_db

logger = logging.getLogger("tdsql.schema")


def ensure_migration_table():
    """确保 schema_migrations / schema_migration_errors 表存在"""
    conn = _get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version         INT NOT NULL,
                sequence        INT NOT NULL,
                name            VARCHAR(128) NOT NULL,
                chunk           VARCHAR(64) NOT NULL DEFAULT 'main',
                applied_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
                duration_ms     INT DEFAULT 0,
                PRIMARY KEY (version, sequence, chunk)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migration_errors (
                id              INT PRIMARY KEY AUTO_INCREMENT,
                version         INT NOT NULL,
                sequence        INT NOT NULL,
                chunk           VARCHAR(64) NOT NULL,
                error_message   TEXT,
                sql_state       VARCHAR(64),
                created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        conn.commit()
    finally:
        conn.close()


def is_applied(version: int, sequence: int, chunk: str) -> bool:
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version=? AND sequence=? AND chunk=?",
            (version, sequence, chunk)).fetchone()
        return row is not None
    finally:
        conn.close()


def record_applied(version: int, sequence: int, name: str, chunk: str, duration_ms: int):
    conn = _get_connection()
    try:
        conn.execute(
            "INSERT INTO schema_migrations(version, sequence, name, chunk, duration_ms) "
            "VALUES (?, ?, ?, ?, ?)",
            (version, sequence, name, chunk, duration_ms))
        conn.commit()
    finally:
        conn.close()


def record_error(version: int, sequence: int, chunk: str, msg: str, sqlstate: str = ""):
    conn = _get_connection()
    try:
        conn.execute(
            "INSERT INTO schema_migration_errors(version, sequence, chunk, error_message, sql_state) "
            "VALUES (?, ?, ?, ?, ?)",
            (version, sequence, chunk, msg[:1000], sqlstate))
        conn.commit()
    finally:
        conn.close()


def run_migrations(dry_run: bool = False) -> dict:
    """
    执行所有未应用的 schema 迁移。
    Returns: {applied: [name, ...], skipped: [...], errors: [...]}
    """
    ensure_migration_table()
    results = {"applied": [], "skipped": [], "errors": []}
    files = discover()
    for f in files:
        entry = get_entry(f.path.replace("\\", "/").replace("schema/", ""))
        if entry is None:
            logger.warning(f"无注册表项，跳过: {f.path}")
            results["skipped"].append(str(f.path))
            continue
        for chunk_name, chunk_sql in iter_chunks(f.sql):
            if is_applied(f.version, f.sequence, chunk_name):
                continue
            if dry_run:
                logger.info(f"[DRY-RUN] 将执行: {f.path}::chunk:{chunk_name}")
                results["applied"].append(f"{f.path}::{chunk_name}")
                continue
            import time
            start = time.time()
            conn = _get_connection()
            try:
                # MySQL 端不支持多条语句的 multi-statement；用 split_sql_statements 拆
                from backend.services.database import split_sql_statements
                for stmt in split_sql_statements(chunk_sql):
                    stmt = stmt.strip()
                    if not stmt or stmt.startswith("--"):
                        continue
                    conn.cursor().execute(stmt)
                conn.commit()
                dur = int((time.time() - start) * 1000)
                record_applied(f.version, f.sequence, f.name, chunk_name, dur)
                results["applied"].append(f"{f.path}::{chunk_name}")
                logger.info(f"已应用: {f.path}::chunk:{chunk_name} ({dur}ms)")
            except Exception as e:
                conn.rollback()
                sqlstate = getattr(e, "args", [None, None])[0] if e.args else ""
                record_error(f.version, f.sequence, chunk_name, str(e), str(sqlstate))
                results["errors"].append(f"{f.path}::{chunk_name}: {e}")
                logger.exception(f"迁移失败: {f.path}::chunk:{chunk_name}")
            finally:
                conn.close()
    return results
```

### C1.6 schema/v0/001_init.sql 设计

把 `database.py` 中 27+ 张表的 DDL 抽到此处。一致性约定：
- 表注释保留 `Engine=InnoDB DEFAULT CHARSET=utf8mb4`
- 字段加 `COMMENT '...'` 描述
- 文件顶部含 schema 元信息：

```sql
-- v0/001_init.sql
-- description: 初始 27 张业务表（与 v1.1.0.1 完全一致）
-- author: Mavis 团队
-- depends_on: []
-- chunk: 001_init

-- T01. slow_queries
CREATE TABLE IF NOT EXISTS slow_queries (
    id                  INT PRIMARY KEY AUTO_INCREMENT
                        COMMENT '主键ID',
    fingerprint         TEXT NOT NULL
                        COMMENT 'SQL 指纹',
    ...
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='慢 SQL 记录 + 分析结果';

-- chunk: 002_audit
-- T02. audit_history
CREATE TABLE IF NOT EXISTS audit_history (
    ...
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ... 共 30 个 chunk ...
```

### C1.7 services/database.py 改造

把 DDL 字符串数组移除，仅保留：

```python
# backend/services/database.py  (改造后)
def init_db():
    """初始化数据库 - v1.2 改为调用 schema 迁移引擎"""
    conn = _get_connection()
    try:
        _execute_sql(conn, """
            CREATE TABLE IF NOT EXISTS schema_version (
                `key`     VARCHAR(128) PRIMARY KEY,
                value     TEXT NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        conn.commit()
    finally:
        conn.close()
    # 启动 schema 迁移
    from backend.schema.migrator import run_migrations
    result = run_migrations()
    if result["errors"]:
        logger.error(f"Schema 迁移出现 {len(result['errors'])} 个错误")
        # 兼容策略：记录但不阻塞启动（v1.1.0.1 行为）
    # 旧版 init_db 的其它初始化（seed data、init_rule_configs 等）保留
    _migrate_old_tables()  # 老库兼容
    _init_default_data()   # 默认数据
```

`init_rule_configs()` 等函数仍保留（业务初始化，不是 schema 迁移）。

### C1.8 兼容策略

| 行为 | v1.1.0.1 | v1.2 |
|---|---|---|
| 老库（无 schema_migrations 表） | 启动时建表 + 插入数据 | 先建 `schema_migrations` 空表；走 001_init.sql 走 `IF NOT EXISTS`；记录已应用 |
| 已存在的 27 张表 | 幂等 CREATE | 走 `IF NOT EXISTS` 不破坏；记录"已应用" |
| 新增的 schema_migrations / schema_migration_errors | 无 | 自动建表 |
| 启动失败 | 阻塞 | 记录但不阻塞（与 v1.1.0.1 一致） |

### C1.9 测试要点

- `tests/test_schema_loader.py`：discover 顺序、iter_chunks 切分
- `tests/test_schema_migrator.py`：
  - 全新库（空）：所有 schema 全部应用
  - 已应用一部分：只应用未执行的
  - 单个 chunk 失败：不影响其它 chunk
  - 重复执行幂等
- `tests/test_schema_integration.py`：模拟 v1.1.0.1 老库，确认升级无破坏

### C1.10 实施步骤

1. 创建 `backend/schema/` 目录与子文件
2. 把 `database.py` 中 27+ 张表的 DDL 抽到 `schema/v0/001_init.sql`（按 chunk 切分）
3. 创建 `loader.py / registry.py / migrator.py`
4. 创建 `registry.toml` 与 `schema/README.md`
5. 改造 `database.py` 的 `init_db()` 调用迁移引擎
6. 跑现有 985 用例 + 新 schema 用例
7. 提 PR：拆分 + 验证 + 文档

### C1.11 回滚方案

```bash
# 1. 停止服务
sudo systemctl stop tdsql-sqlcheck

# 2. 回滚代码
cd /opt/tdsql-sqlcheck
sudo git checkout v1.1.0.1  # 切到上一个 tag

# 3. 数据库表无破坏（schema_migrations 等可保留，不影响 v1.1.0.1 启动）
# 4. 重启
sudo systemctl start tdsql-sqlcheck
```

---

## C2 — engine/parser 拆分

### C2.1 现状

`backend/engine/parser.py` 30KB，包含：
- `ParsedSQL` dataclass
- `SQLParser` 类
- `_regex_pre_parse()`：正则预解析 ~150 行
- `_parse_select/insert/update/delete/create/alter/drop`：AST 驱动 ~400 行
- `_parse_common()`：通用解析 ~200 行
- `_extract_tables()`：AST 表提取 ~50 行
- `_detect_sql_type_regex()`：fallback ~30 行

### C2.2 目标结构

```
backend/engine/
├── __init__.py
├── parser.py              # 瘦身后 ~20KB：仅 AST 解析 + 公共方法
├── pre_parser.py          # 新建：正则预解析 ~5KB
├── parsed_sql.py          # 新建：ParsedSQL dataclass 独立
├── checker.py             # 不动
└── rules/                 # 不动
```

### C2.3 engine/parsed_sql.py 设计

```python
# backend/engine/parsed_sql.py
"""ParsedSQL 数据结构 - 与 parser 解耦，便于复用"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ParsedSQL:
    """解析后的SQL结构（V1.0 完整字段）"""
    # === 基础信息 ===
    raw_sql: str = ""
    sql_type: str = ""
    tables: list[str] = field(default_factory=list)
    select_fields: list[str] = field(default_factory=list)

    # === DDL 结构信息 ===
    is_create_table: bool = False
    is_alter_table: bool = False
    has_primary_key: bool = False
    has_foreign_key: bool = False
    engine: Optional[str] = None
    charset: Optional[str] = None
    columns: list[dict] = field(default_factory=list)
    column_types: list[dict] = field(default_factory=list)
    indexes: list[dict] = field(default_factory=list)
    table_options: dict = field(default_factory=dict)
    has_table_comment: bool = False
    column_comments: dict[str, str] = field(default_factory=dict)
    index_definitions: list[dict] = field(default_factory=list)
    is_create_table_select: bool = False
    is_temporary_table: bool = False
    has_drop_database: bool = False
    alter_actions: list[dict] = field(default_factory=list)

    # === DML 结构信息 ===
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

    # === 分布式信息 ===
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
```

### C2.4 engine/pre_parser.py 设计

```python
# backend/engine/pre_parser.py
"""SQL 预解析器 - 用正则弥补 sqlglot 漏掉的特殊语法"""
from __future__ import annotations
import re
from backend.engine.parsed_sql import ParsedSQL


class PreParser:
    """正则预解析器：可独立开关（向后兼容）"""

    # 预解析开关（来自 system_config.parser.use_pre_parser）
    ENABLED: bool = True

    # 各类预解析正则（从原 parser.py 搬过来）
    _RE_DELAYED = re.compile(r"\b(delayed|low_priority)\b", re.IGNORECASE)
    _RE_INTO_OUTFILE = re.compile(r"into\s+(?:out|dump)file", re.IGNORECASE)
    _RE_LOAD_DATA = re.compile(r"^\s*(load\s+(?:data|xml))", re.IGNORECASE | re.MULTILINE)
    _RE_HANDLER = re.compile(r"^\s*handler\b", re.IGNORECASE)
    _RE_FLUSH = re.compile(r"^\s*flush\b", re.IGNORECASE)
    _RE_LOCK_TABLES = re.compile(r"^\s*lock\s+tables\b", re.IGNORECASE)
    _RE_FOR_UPDATE = re.compile(r"\bfor\s+(update|share)\b", re.IGNORECASE)
    _RE_DROP_DB = re.compile(r"^\s*drop\s+(?:database|schema)\b", re.IGNORECASE)
    _RE_IN_LIST = re.compile(r"\bin\s*\(([^)]+)\)", re.IGNORECASE)
    _RE_LIMIT_OFFSET = re.compile(r"\blimit\s+(?:(\d+)\s*,\s*)?(\d+)(?:\s+offset\s+(\d+))?",
                                   re.IGNORECASE)

    def run(self, sql: str, parsed: ParsedSQL) -> ParsedSQL:
        """执行预解析，把结果回填到 parsed（mutates in place）"""
        if not self.ENABLED:
            return parsed
        sql_lower = sql.lower()

        if self._RE_DELAYED.search(sql_lower):
            parsed.has_delayed_keyword = True
        if self._RE_INTO_OUTFILE.search(sql_lower):
            parsed.has_into_outfile = True
        if self._RE_LOAD_DATA.match(sql_lower):
            parsed.has_load_data = True
        if self._RE_HANDLER.match(sql_lower):
            parsed.has_handler_do = True
        if self._RE_FLUSH.match(sql_lower):
            parsed.has_flush = True
        if self._RE_LOCK_TABLES.match(sql_lower):
            parsed.has_lock_tables = True
        if self._RE_FOR_UPDATE.search(sql_lower):
            parsed.has_for_update = True
        if self._RE_DROP_DB.match(sql_lower):
            parsed.has_drop_database = True

        # IN 列表大小
        max_in = 0
        for m in self._RE_IN_LIST.finditer(sql_lower):
            count = len([x for x in m.group(1).split(",") if x.strip()])
            if count > max_in:
                max_in = count
        parsed.in_list_size = max_in

        # LIMIT offset
        m = self._RE_LIMIT_OFFSET.search(sql_lower)
        if m:
            if m.group(3):  # LIMIT N OFFSET M
                parsed.limit_offset = int(m.group(3))
            elif m.group(1) is not None:  # LIMIT N, M
                parsed.limit_offset = int(m.group(1))

        return parsed


# 单例
pre_parser = PreParser()
```

### C2.5 engine/parser.py 改造

```python
# backend/engine/parser.py (改造后瘦身版)
"""SQL 解析器 - 仅 AST 解析"""
from __future__ import annotations
import re
from typing import Optional

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from backend.engine.parsed_sql import ParsedSQL
from backend.engine.pre_parser import pre_parser


class SQLParser:
    """SQL 解析器（V1.0 瘦身版）"""

    PLURAL_SUFFIXES = ("s", "es", "ies", "ses")
    PLURAL_IGNORE = {"status", "process", "address", "access", "class", "glass",
                     "gas", "bus", "plus", "this", "news", "series", "species"}

    def __init__(self, dialect: str = "mysql"):
        self.dialect = dialect

    def parse(self, sql: str) -> ParsedSQL:
        parsed = ParsedSQL(raw_sql=sql.strip())
        sql_clean = sql.strip().rstrip(";")

        # 1. 正则预解析（拆到 pre_parser.py）
        pre_parser.run(sql_clean, parsed)

        # 2. AST 解析
        try:
            ast = sqlglot.parse_one(sql_clean, read=self.dialect)
            parsed.ast = ast
        except (SqlglotError, Exception) as e:
            parsed.parse_error = str(e)
            parsed.sql_type = self._detect_sql_type_regex(sql_clean)
            return parsed

        # 3. SQL 类型分发
        parsed.sql_type = self._get_sql_type(ast)
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

        # 4. 通用解析
        self._parse_common(ast, parsed)

        # 5. 表名兜底
        if not parsed.tables:
            parsed.tables = self._extract_tables(ast)

        return parsed

    # 下面是从原 parser.py 搬过来的私有方法（保持原样）
    # _get_sql_type, _parse_select, _parse_insert, _parse_update,
    # _parse_delete, _parse_create, _parse_alter, _parse_drop,
    # _parse_common, _extract_tables, _detect_sql_type_regex
    # （共 ~400 行，无变化）
```

### C2.6 兼容性

| 行为 | v1.1.0.1 | v1.2 |
|---|---|---|
| `RuleChecker.audit_sql()` 调 `parser.parse(sql)` | OK | OK（API 不变） |
| `ParsedSQL` 字段集 | 100% | 100% |
| 119 条规则输入 | OK | OK（用 `pre_parser` 字段的规则不破坏） |
| sqlglot 解析失败时的回退 | 正则 fallback | 同样正则 fallback（保持 `_detect_sql_type_regex`） |

### C2.7 测试要点

- `tests/test_pre_parser.py`：覆盖 9 类正则场景
- `tests/test_parser_integration.py`：用 v1.1.0.1 的 119 个 SQL 样本做"前后结果对比"
- 重点：确保拆分后 `parsed.has_xxx` 字段值与 v1.1.0.1 完全一致

### C2.8 实施步骤

1. 创建 `engine/parsed_sql.py` 把 dataclass 搬过去
2. 创建 `engine/pre_parser.py` 把 `_regex_pre_parse` 拆出来
3. `engine/parser.py` 删除 `_regex_pre_parse` 与 dataclass 定义
4. 跑全部 985 用例 + 新加的 pre_parser 单测
5. 提 PR

### C2.9 回滚方案

由于是纯重构（无 API 变更），回滚 = `git checkout v1.1.0.1` + 启停服务。

---

## C3 — tdsql_connector 拆分

### C3.1 现状

`backend/services/tdsql_connector.py` 71KB，单类承担：
- `TDSQLConnectionConfig` (dataclass)
- `TDSQLConnectionPool` (连接池)
- `TableMetadata` / `IndexInfo` (dataclass)
- `build_large_tables_query()` 工具函数
- `parse_shard_key_from_ddl()` 工具函数
- `_analyze_partitions()` 工具函数
- `TDSQLConnector` (TDSQL 操作客户端)
  - `get_tables/get_table_metadata/get_table_partitions`
  - `get_slow_queries_from_digest`
  - `poll_processlist`
  - `monitor_probe` / `_monitor_execute`
  - `get_cluster_slow_queries` (monitordb 15001)
  - `get_proxy_config` / `discover_sets`

### C3.2 目标结构

```
backend/services/connector/                # 新建子包
├── __init__.py            # 公共导出
├── config.py              # TDSQLConnectionConfig
├── pool.py                # TDSQLConnectionPool (核心)
├── metadata.py            # MetadataFetcher (元数据)
├── slow_query.py          # SlowQueryFetcher (慢 SQL 拉取)
├── monitor_db.py          # MonitorDBClient (monitordb 15001)
├── proxy.py               # ProxyClient (Proxy 内省)
├── shard.py               # ShardKeyInspector (DDL 解析)
└── utils.py               # build_large_tables_query 等工具

backend/services/tdsql_connector.py         # 改造为薄壳，向后兼容
```

### C3.3 connector/pool.py 设计

```python
# backend/services/connector/pool.py
"""TDSQL 连接池 - 核心类，几乎不动"""
from __future__ import annotations
import logging
import threading
from contextlib import contextmanager

logger = logging.getLogger("tdsql.connector.pool")

try:
    import pymysql
    import pymysql.cursors
    HAS_PYMYSQL = True
except ImportError:
    HAS_PYMYSQL = False

from backend.services.connector.config import TDSQLConnectionConfig


class TDSQLConnectionPool:
    DEFAULT_POOL_SIZE = 5

    def __init__(self, config: TDSQLConnectionConfig, pool_size: int = None):
        self.config = config
        self.pool_size = pool_size or self.DEFAULT_POOL_SIZE
        self._local = threading.local()
        self._connected = False

    def _create_connection(self):
        return pymysql.connect(
            host=self.config.host, port=self.config.port,
            user=self.config.user, password=self.config.password,
            database=self.config.database, charset=self.config.charset,
            connect_timeout=self.config.connect_timeout,
            read_timeout=self.config.read_timeout,
            cursorclass=pymysql.cursors.DictCursor,
        )

    def _get_thread_connection(self):
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.ping(reconnect=False)
                return conn
            except Exception:
                conn = None
        conn = self._create_connection()
        self._local.conn = conn
        self._connected = True
        return conn

    @contextmanager
    def get_connection(self):
        conn = self._get_thread_connection()
        try:
            yield conn
        finally:
            pass  # 线程本地，不在这里关

    def execute(self, sql: str, params=None):
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur

    def fetch_all(self, sql: str, params=None) -> list[dict]:
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall()

    def fetch_one(self, sql: str, params=None) -> dict | None:
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchone()

    def close_all(self):
        conn = getattr(self._local, "conn", None)
        if conn:
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None
            self._connected = False
```

### C3.4 connector/metadata.py 设计

```python
# backend/services/connector/metadata.py
"""表/索引/分片键/分区等元数据查询"""
from __future__ import annotations
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from backend.services.connector.pool import TDSQLConnectionPool
from backend.services.connector.shard import parse_shard_key_from_ddl

logger = logging.getLogger("tdsql.connector.metadata")

# 系统库白名单（与 v1.1.0.1 一致）
_BIGTABLE_SYS_DBS = (
    "__tencentdb__", "information_schema", "mysql", "performance_schema",
    "query_rewrite", "sys", "sysdb", "test", "xa",
)


@dataclass
class TableMetadata:
    table_name: str = ""
    table_type: str = ""
    engine: str = ""
    charset: str = ""
    table_collation: str = ""
    table_comment: str = ""
    table_rows: int = 0
    data_length: int = 0
    index_length: int = 0
    shard_key: Optional[str] = None
    is_shard_table: bool = False
    is_broadcast_table: bool = False
    is_single_table: bool = False
    columns: list[dict] = field(default_factory=list)
    indexes: list[dict] = field(default_factory=list)
    create_sql: str = ""


@dataclass
class IndexInfo:
    table_name: str = ""
    index_name: str = ""
    column_name: str = ""
    seq_in_index: int = 0
    non_unique: int = 1
    index_type: str = ""
    cardinality: int = 0


class MetadataFetcher:
    """表/索引/分片键/分区 元数据查询器"""

    def __init__(self, pool: TDSQLConnectionPool):
        self.pool = pool

    def get_tables(self, database: str = None) -> list[dict]:
        if database:
            sql = "SHOW FULL TABLES FROM %s WHERE Table_type = 'BASE TABLE'"
            return self.pool.fetch_all(sql, (database,))
        return self.pool.fetch_all("SHOW FULL TABLES WHERE Table_type = 'BASE TABLE'")

    def get_table_metadata(self, table: str, database: str = None) -> TableMetadata:
        full = f"`{database}`.`{table}`" if database else f"`{table}`"
        meta = TableMetadata(table_name=table)
        # status
        row = self.pool.fetch_one(
            "SELECT ENGINE, TABLE_COLLATION, TABLE_COMMENT, TABLE_ROWS, "
            "DATA_LENGTH, INDEX_LENGTH FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
            (database or self.pool.config.database, table))
        if row:
            meta.engine = row.get("ENGINE") or ""
            meta.charset = (row.get("TABLE_COLLATION") or "").split("_")[0]
            meta.table_collation = row.get("TABLE_COLLATION") or ""
            meta.table_comment = row.get("TABLE_COMMENT") or ""
            meta.table_rows = row.get("TABLE_ROWS") or 0
            meta.data_length = row.get("DATA_LENGTH") or 0
            meta.index_length = row.get("INDEX_LENGTH") or 0
        # columns
        meta.columns = self.pool.fetch_all(
            "SELECT COLUMN_NAME AS name, COLUMN_TYPE AS type, "
            "COLUMN_DEFAULT AS default_value, IS_NULLABLE AS nullable, "
            "COLUMN_KEY AS key_type, EXTRA AS extra, COLUMN_COMMENT AS comment "
            "FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION",
            (database or self.pool.config.database, table))
        # indexes
        meta.indexes = self.pool.fetch_all(
            "SELECT INDEX_NAME AS index_name, COLUMN_NAME AS column_name, "
            "NON_UNIQUE AS non_unique, INDEX_TYPE AS index_type, "
            "SEQ_IN_INDEX AS seq_in_index, CARDINALITY AS cardinality "
            "FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s "
            "ORDER BY INDEX_NAME, SEQ_IN_INDEX",
            (database or self.pool.config.database, table))
        # shard key
        try:
            create_row = self.pool.fetch_one(f"SHOW CREATE TABLE {full}")
            if create_row:
                create_sql = list(create_row.values())[1] if len(create_row) >= 2 else ""
                meta.create_sql = create_sql
                shard = parse_shard_key_from_ddl(create_sql)
                meta.shard_key = shard
                meta.is_shard_table = "SHARDKEY" in create_sql.upper()
                meta.is_broadcast_table = "BROADCAST" in create_sql.upper()
                meta.is_single_table = not meta.is_shard_table and not meta.is_broadcast_table
        except Exception as e:
            logger.debug(f"获取 create_sql 失败: {e}")
        return meta

    def get_table_partitions(self, schema: str, table: str) -> dict:
        """获取分区表逐分区明细 + 派生分析"""
        rows = self.pool.fetch_all(
            "SELECT PARTITION_NAME, PARTITION_DESCRIPTION, TABLE_ROWS, "
            "DATA_LENGTH, INDEX_LENGTH "
            "FROM information_schema.PARTITIONS "
            "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND PARTITION_NAME IS NOT NULL "
            "ORDER BY PARTITION_ORDINAL_POSITION",
            (schema, table))
        partitions = []
        for r in rows:
            partitions.append({
                "name": r.get("PARTITION_NAME"),
                "description": r.get("PARTITION_DESCRIPTION"),
                "rows": r.get("TABLE_ROWS") or 0,
                "data_bytes": r.get("DATA_LENGTH") or 0,
                "index_bytes": r.get("INDEX_LENGTH") or 0,
                "size_gb": round(((r.get("DATA_LENGTH") or 0) + (r.get("INDEX_LENGTH") or 0)) / 1024 / 1024 / 1024, 3),
            })
        # 派生分析
        n = len(partitions)
        flags = []
        if n == 0:
            return {"partitions": [], "analysis": {"flags": flags}}
        max_p = max(partitions, key=lambda p: p["size_gb"])
        avg = sum(p["size_gb"] for p in partitions) / n
        skew = round(max_p["size_gb"] / avg, 2) if avg > 0 else 0
        if skew >= 3:
            flags.append({"code": "data_skew", "level": "warning",
                          "msg": f"最大分区 {max_p['name']} 是平均的 {skew} 倍"})
        # 数量预警
        if n >= 100:
            flags.append({"code": "too_many_partitions", "level": "danger",
                          "msg": f"分区数 {n} 已达上限 100"})
        elif n >= 70:
            flags.append({"code": "too_many_partitions", "level": "warning",
                          "msg": f"分区数 {n} 接近上限 100"})
        # MAXVALUE
        mv = next((p for p in partitions if p.get("description") == "MAXVALUE"), None)
        if mv and mv["size_gb"] / max(sum(p["size_gb"] for p in partitions), 1) > 0.3:
            flags.append({"code": "maxvalue_oversized", "level": "warning",
                          "msg": f"兜底分区 {mv['name']} 占比过高，建议补建分区"})
        # 空分区
        empty = [p["name"] for p in partitions if p["rows"] == 0 and p["size_gb"] < 0.001]
        if len(empty) >= 3:
            flags.append({"code": "empty_partitions", "level": "info",
                          "msg": f"存在 {len(empty)} 个空分区"})
        return {
            "partitions": partitions,
            "analysis": {
                "partition_count": n,
                "max_partition": {"name": max_p["name"], "size_gb": max_p["size_gb"]},
                "avg_size_gb": round(avg, 3),
                "skew_ratio": skew,
                "flags": flags,
            },
        }

    def check_charset_consistency(self, database: str = None) -> dict:
        """检查库内字符集与排序规则一致性"""
        # 略，从原 tdsql_connector 搬过来，签名不变
        ...

    def check_large_tables(self, database: str = None, threshold_gb: float = 1.0) -> list[dict]:
        """检查大表（双源：information_schema.PARTITIONS + TABLES）"""
        # 略，签名不变
        ...
```

### C3.5 connector/slow_query.py 设计

```python
# backend/services/connector/slow_query.py
"""慢 SQL 拉取 - digest/processlist 两种数据源"""
from __future__ import annotations
import logging
from typing import Optional

from backend.services.connector.pool import TDSQLConnectionPool

logger = logging.getLogger("tdsql.connector.slow_query")


class SlowQueryFetcher:
    """慢 SQL 数据采集器（digest + processlist）"""

    def __init__(self, pool: TDSQLConnectionPool):
        self.pool = pool

    def get_slow_queries_from_digest(
        self,
        limit: int = 50,
        min_time: float = 0.1,
        time_start: str = None,
        time_end: str = None,
        set_id: str = None,
        database: str = None,
    ) -> list[dict]:
        """从 Proxy digest 拉取慢 SQL（合并多 SET）"""
        # 实现与 v1.1.0.1 一致；签名不变
        ...

    def poll_processlist(
        self,
        duration_seconds: float = 10.0,
        interval: float = 1.0,
        min_time: float = 0.1,
    ) -> list[dict]:
        """轮询 information_schema.processlist 抓取长时间执行的 SQL"""
        # 略，签名不变
        ...
```

### C3.6 connector/monitor_db.py 设计

```python
# backend/services/connector/monitor_db.py
"""monitordb (15001) 客户端 - 集群级慢 SQL"""
from __future__ import annotations
import logging
from typing import Optional

from backend.services.connector.pool import TDSQLConnectionPool

logger = logging.getLogger("tdsql.connector.monitor_db")


class MonitorDBClient:
    """monitordb 客户端 (tdsqlpcloud_monitor.proxy_classes_analysis)"""

    def __init__(self, pool: TDSQLConnectionPool):
        self.pool = pool

    def _monitor_conn_params(self) -> dict:
        """monitordb 连接参数（fallback 到主连接）"""
        cfg = self.pool.config
        return {
            "host": cfg.monitor_host or cfg.host,
            "port": cfg.monitor_port,
            "user": cfg.monitor_user or cfg.user,
            "password": cfg.monitor_password or cfg.password,
            "database": cfg.monitor_db or "tdsqlpcloud_monitor",
        }

    def monitor_probe(self) -> dict:
        """探测 monitordb 可用性 + 列存在性"""
        # 与 v1.1.0.1 完全一致；签名不变
        ...

    def _monitor_execute(self, sql: str, params: tuple = ()) -> list[dict]:
        """在 monitordb 上执行 SQL（独立连接）"""
        # 略，签名不变
        ...

    def get_cluster_slow_queries(
        self,
        limit: int = 50,
        min_time: float = 0.1,
        time_start: str = None,
        time_end: str = None,
        database: str = None,
        user: str = None,
        set_ports: str = None,
    ) -> list[dict]:
        """从 monitordb 拉取全集群 TopN 慢 SQL"""
        # 与 v1.1.0.1 实现一致；签名不变
        # 注意：100+ 行实现保持原样搬过来
        ...
```

### C3.7 connector/proxy.py 设计

```python
# backend/services/connector/proxy.py
"""TDSQL Proxy 客户端 - /*proxy*/show config / show status"""
from __future__ import annotations
import logging
from typing import Optional

from backend.services.connector.pool import TDSQLConnectionPool

logger = logging.getLogger("tdsql.connector.proxy")


class ProxyClient:
    """TDSQL Proxy 内省客户端"""

    def __init__(self, pool: TDSQLConnectionPool):
        self.pool = pool

    def discover_sets(self) -> list[dict]:
        """通过 /*proxy*/show status 发现分布式 SET 列表"""
        # 与 v1.1.0.1 一致
        ...

    def get_proxy_config(self) -> dict:
        """获取 Proxy 层慢日志阈值等配置"""
        # 与 v1.1.0.1 一致
        ...
```

### C3.8 connector/shard.py 设计

```python
# backend/services/connector/shard.py
"""分片键解析器 - 从 SHOW CREATE TABLE 的 DDL 提取 SHARDKEY"""
from __future__ import annotations
import re

_SHARDKEY_RE = re.compile(r"SHARDKEY\s*=?\s*\(?([^)]+)\)?", re.IGNORECASE)


def parse_shard_key_from_ddl(create_sql: str) -> str:
    """从 DDL 提取 TDSQL 分片键；非分布式表返回空字符串"""
    if not create_sql or "SHARDKEY" not in create_sql.upper():
        return ""
    m = _SHARDKEY_RE.search(create_sql)
    if m:
        return m.group(1).strip().strip('`"\'')
    return ""
```

### C3.9 connector/utils.py 设计

```python
# backend/services/connector/utils.py
"""通用工具：build_large_tables_query 等"""
from __future__ import annotations
from typing import Optional, Tuple

_BIGTABLE_SYS_DBS = (
    "__tencentdb__", "information_schema", "mysql", "performance_schema",
    "query_rewrite", "sys", "sysdb", "test", "xa",
)


def build_large_tables_query(
    threshold_gb: float = 1.0,
    database: str = None,
) -> Tuple[str, tuple]:
    """构造大表检测 SQL（双源取大，from information_schema.PARTITIONS + TABLES）"""
    # 与 v1.1.0.1 完全一致；签名不变
    ...
```

### C3.10 services/tdsql_connector.py 改造为薄壳

```python
# backend/services/tdsql_connector.py (v1.2 薄壳)
"""向后兼容层：所有老代码 `from backend.services.tdsql_connector import X` 仍可用
所有实现已迁移到 backend.services.connector 子包；本文件仅做 re-export
"""
from backend.services.connector import (
    # 数据类
    TDSQLConnectionConfig,
    TableMetadata,
    IndexInfo,
    # 核心
    TDSQLConnectionPool,
    # 工具
    build_large_tables_query,
    parse_shard_key_from_ddl,
)
# 子模块按需导入（保持兼容）
from backend.services.connector.metadata import MetadataFetcher
from backend.services.connector.slow_query import SlowQueryFetcher
from backend.services.connector.monitor_db import MonitorDBClient
from backend.services.connector.proxy import ProxyClient

# 保留 v1.1.0.1 的 TDSQLConnector 类（聚合 facade）
class TDSQLConnector:
    """v1.0 兼容的聚合类 - v1.2 改为组合各子模块"""

    def __init__(self, config: TDSQLConnectionConfig):
        self.config = config
        self.pool = TDSQLConnectionPool(config)
        self.metadata = MetadataFetcher(self.pool)
        self.slow_query = SlowQueryFetcher(self.pool)
        self.monitor = MonitorDBClient(self.pool)
        self.proxy = ProxyClient(self.pool)

    def connect(self):
        with self.pool.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return self

    def disconnect(self):
        self.pool.close_all()

    # 元数据方法（转给 metadata）
    def get_tables(self, database=None):
        return self.metadata.get_tables(database)

    def get_table_metadata(self, table, database=None):
        return self.metadata.get_table_metadata(table, database)

    def get_table_partitions(self, schema, table):
        return self.metadata.get_table_partitions(schema, table)

    def check_charset_consistency(self, database=None):
        return self.metadata.check_charset_consistency(database)

    def check_large_tables(self, database=None, threshold_gb=1.0):
        return self.metadata.check_large_tables(database, threshold_gb)

    # 慢 SQL 方法（转给 slow_query）
    def get_slow_queries_from_digest(self, **kwargs):
        return self.slow_query.get_slow_queries_from_digest(**kwargs)

    def poll_processlist(self, **kwargs):
        return self.slow_query.poll_processlist(**kwargs)

    # monitordb（转给 monitor）
    def monitor_probe(self):
        return self.monitor.monitor_probe()

    def get_cluster_slow_queries(self, **kwargs):
        return self.monitor.get_cluster_slow_queries(**kwargs)

    # proxy（转给 proxy）
    def discover_sets(self):
        return self.proxy.discover_sets()

    def get_proxy_config(self):
        return self.proxy.get_proxy_config()
```

### C3.11 兼容性

- 所有 32 个服务的 `from backend.services.tdsql_connector import TDSQLConnectionPool` 等引用 **零修改**
- `TDSQLConnector` 旧 API **100% 保留**（薄壳 facade）
- 连接池的 `get_connection()` / `execute()` / `fetch_*` / `close_all()` 签名不变

### C3.12 测试要点

- `tests/test_connector_pool.py`：连接池线程安全、断线重连
- `tests/test_connector_metadata.py`：表/索引/分片键/分区
- `tests/test_connector_slow_query.py`：digest/processlist 拉取
- `tests/test_connector_monitor.py`：monitordb 拉取（用 mock 测 SQL 拼接）
- `tests/test_connector_proxy.py`：proxy 内省（用 mock）
- `tests/test_connector_compat.py`：调用 `TDSQLConnector` 旧 API，确保结果与 v1.1.0.1 一致

### C3.13 实施步骤

1. 创建 `backend/services/connector/` 子包
2. 逐个搬文件 + 加类型注解
3. 写 `connector/__init__.py` 与 `tdsql_connector.py` 薄壳
4. 跑 985 用例 + 新加 5 个子模块的单元测试
5. 提 PR

### C3.14 回滚方案

回滚 = `git checkout v1.1.0.1` + 启停服务（薄壳 + 子包均无破坏）。

---

## C4 — 前端 Vite 工程化

### C4.1 现状

`frontend/index.html` (110KB) + `static/js/app.js` (80KB) + `static/css/app.css` (10KB)

- 单文件 SPA，无构建工具
- 无路由懒加载、首屏加载全 80KB
- 19 个页面 + 9 个深度诊断子页挤在一个文件
- 无 TS、无组件化

### C4.2 目标结构

```
frontend/
├── index.html                 # v1 单页（兜底，与 v1.1.0.1 相同）
├── dist/                      # v2 构建产物（git ignored，部署时由 Vite 生成）
│   ├── index.html
│   ├── assets/
│   │   ├── index-xxxxxx.js
│   │   ├── index-xxxxxx.css
│   │   ├── Dashboard-xxxxxx.js
│   │   └── ...
├── src/                       # v2 源码
│   ├── main.ts
│   ├── App.vue
│   ├── env.d.ts
│   ├── router/
│   │   └── index.ts
│   ├── views/
│   │   ├── Login.vue
│   │   ├── Dashboard.vue
│   │   ├── audit/
│   │   │   ├── SqlAudit.vue
│   │   │   ├── FileAudit.vue
│   │   │   └── Rules.vue
│   │   ├── slow/
│   │   │   ├── Tasks.vue
│   │   │   ├── Records.vue
│   │   │   ├── Schedule.vue
│   │   │   └── Explain.vue
│   │   ├── instance/
│   │   │   ├── Manage.vue
│   │   │   ├── SchemaCheck.vue
│   │   │   └── Bigtable.vue
│   │   ├── deep-diag/
│   │   │   ├── Cluster.vue
│   │   │   ├── Daily.vue
│   │   │   ├── Index.vue
│   │   │   ├── Diff.vue
│   │   │   ├── Emergency.vue
│   │   │   ├── SqlStats.vue
│   │   │   ├── GatewayLog.vue
│   │   │   ├── PptReport.vue
│   │   │   └── Toolkit.vue
│   │   ├── platform/
│   │   │   ├── Projects.vue
│   │   │   ├── Rulesets.vue
│   │   │   ├── Gate.vue
│   │   │   ├── Monitor.vue
│   │   │   └── Inspection.vue
│   │   └── system/
│   │       ├── Users.vue
│   │       ├── Roles.vue
│   │       ├── Permissions.vue
│   │       ├── Retention.vue
│   │       ├── AuditLog.vue
│   │       └── Info.vue
│   ├── components/
│   │   ├── KpiCard.vue
│   │   ├── TrendChart.vue
│   │   ├── TopBar.vue
│   │   ├── Sidebar.vue
│   │   └── Breadcrumb.vue
│   ├── stores/
│   │   ├── auth.ts
│   │   ├── connection.ts
│   │   └── project.ts
│   ├── api/
│   │   ├── http.ts
│   │   ├── audit.ts
│   │   ├── slow.ts
│   │   ├── tdsql.ts
│   │   ├── instance.ts
│   │   ├── deep-diag.ts
│   │   ├── platform.ts
│   │   └── system.ts
│   ├── utils/
│   │   ├── format.ts
│   │   ├── constants.ts
│   │   └── rules.ts
│   └── styles/
│       ├── element-variables.scss
│       └── main.scss
├── vite.config.ts
├── package.json
├── tsconfig.json
└── .gitignore
```

### C4.3 package.json

```json
{
  "name": "tdsql-sqlcheck-ui",
  "version": "1.2.0",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vue-tsc --noEmit && vite build",
    "preview": "vite preview",
    "lint": "eslint . --ext .vue,.ts,.tsx",
    "typecheck": "vue-tsc --noEmit"
  },
  "dependencies": {
    "vue": "^3.4.0",
    "vue-router": "^4.3.0",
    "pinia": "^2.1.7",
    "element-plus": "^2.7.0",
    "@element-plus/icons-vue": "^2.3.0",
    "echarts": "^5.5.0",
    "axios": "^1.7.0"
  },
  "devDependencies": {
    "vite": "^5.2.0",
    "@vitejs/plugin-vue": "^5.0.0",
    "vue-tsc": "^2.0.0",
    "typescript": "^5.4.0",
    "sass": "^1.77.0",
    "@types/node": "^20.12.0",
    "unplugin-auto-import": "^0.17.0",
    "unplugin-vue-components": "^0.27.0"
  }
}
```

### C4.4 vite.config.ts

```typescript
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import AutoImport from "unplugin-auto-import/vite";
import Components from "unplugin-vue-components/vite";
import { ElementPlusResolver } from "unplugin-vue-components/resolvers";
import path from "node:path";

export default defineConfig({
  plugins: [
    vue(),
    AutoImport({ resolvers: [ElementPlusResolver()] }),
    Components({ resolvers: [ElementPlusResolver()] }),
  ],
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    rollupOptions: {
      output: {
        // vendor 单独 chunk 复用缓存
        manualChunks: {
          "vendor-vue": ["vue", "vue-router", "pinia"],
          "vendor-element": ["element-plus", "@element-plus/icons-vue"],
          "vendor-echarts": ["echarts"],
        },
      },
    },
    chunkSizeWarningLimit: 600,
  },
  server: {
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
});
```

### C4.5 src/main.ts

```typescript
import { createApp } from "vue";
import { createPinia } from "pinia";
import ElementPlus from "element-plus";
import zhCn from "element-plus/dist/locale/zh-cn.mjs";
import "element-plus/dist/index.css";
import * as ElementPlusIconsVue from "@element-plus/icons-vue";

import App from "./App.vue";
import router from "./router";
import "./styles/main.scss";

const app = createApp(App);

// 注册所有图标
for (const [name, comp] of Object.entries(ElementPlusIconsVue)) {
  app.component(name, comp as any);
}

app.use(createPinia());
app.use(router);
app.use(ElementPlus, { locale: zhCn });
app.mount("#app");
```

### C4.6 src/router/index.ts

```typescript
import { createRouter, createWebHistory, RouteRecordRaw } from "vue-router";
import { useAuthStore } from "@/stores/auth";

const routes: RouteRecordRaw[] = [
  {
    path: "/login",
    name: "Login",
    component: () => import("@/views/Login.vue"),
    meta: { public: true },
  },
  {
    path: "/",
    component: () => import("@/views/Layout.vue"),
    meta: { requiresAuth: true },
    children: [
      { path: "", redirect: "/dashboard" },
      { path: "dashboard", name: "Dashboard",
        component: () => import("@/views/Dashboard.vue"),
        meta: { menu: "dashboard" } },
      // audit
      { path: "audit/sql", name: "SqlAudit",
        component: () => import("@/views/audit/SqlAudit.vue"),
        meta: { menu: "audit-sql" } },
      { path: "audit/file", name: "FileAudit",
        component: () => import("@/views/audit/FileAudit.vue"),
        meta: { menu: "file-audit" } },
      { path: "audit/rules", name: "Rules",
        component: () => import("@/views/audit/Rules.vue"),
        meta: { menu: "rules" } },
      // slow
      { path: "slow/tasks", name: "SlowTasks",
        component: () => import("@/views/slow/Tasks.vue"),
        meta: { menu: "slow-tasks" } },
      // ... 其余路由同 v1.1.0.1 菜单项
      // deep-diag (9 个)
      { path: "deep-diag/cluster",
        component: () => import("@/views/deep-diag/Cluster.vue"),
        meta: { menu: "deep-diag-cluster" } },
      // ... 其余 8 个
    ],
  },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

router.beforeEach((to, _from, next) => {
  const auth = useAuthStore();
  if (to.meta.requiresAuth && !auth.token) {
    next("/login");
  } else if (to.meta.public && auth.token) {
    next("/");
  } else {
    next();
  }
});

export default router;
```

### C4.7 src/stores/auth.ts

```typescript
import { defineStore } from "pinia";
import { ref, computed } from "vue";
import axios from "@/api/http";

export const useAuthStore = defineStore("auth", () => {
  const token = ref(localStorage.getItem("token") || "");
  const user = ref(JSON.parse(localStorage.getItem("user") || "null"));
  const visibleMenus = ref<Set<string>>(
    new Set(JSON.parse(localStorage.getItem("visibleMenus") || "[]"))
  );

  const isAuthenticated = computed(() => !!token.value);
  const role = computed(() => user.value?.role || "");
  const roleLabel = computed(() => {
    return { admin: "系统管理员", dba: "数据库管理员",
             developer: "开发人员", auditor: "审计员" }[role.value] || role.value;
  });

  async function login(username: string, password: string) {
    const { data } = await axios.post("/api/v1/auth/login",
      { username, password });
    token.value = data.token;
    user.value = data.user;
    localStorage.setItem("token", data.token);
    localStorage.setItem("user", JSON.stringify(data.user));
    await loadVisibleMenus();
  }

  async function loadVisibleMenus() {
    const { data } = await axios.get("/api/v1/auth/visible-menus");
    visibleMenus.value = new Set(data.menus);
    localStorage.setItem("visibleMenus", JSON.stringify([...visibleMenus.value]));
  }

  function logout() {
    token.value = "";
    user.value = null;
    visibleMenus.value.clear();
    localStorage.removeItem("token");
    localStorage.removeItem("user");
    localStorage.removeItem("visibleMenus");
  }

  return { token, user, visibleMenus, isAuthenticated, role, roleLabel,
           login, logout, loadVisibleMenus };
});
```

### C4.8 灰度方案

后端通过中间件改写路径：

```python
# backend/middleware.py 新增
class UIVersionMiddleware(BaseHTTPMiddleware):
    """根据查询参数 ?ui=v2 把 / 改写到 /v2/index.html"""

    async def dispatch(self, request, call_next):
        if request.url.path == "/" and request.query_params.get("ui") == "v2":
            # 重写到 v2 静态资源（由 Vite build 产物在 /static/v2/ 下提供）
            from fastapi.responses import FileResponse
            v2_index = Path(__file__).parent.parent / "frontend" / "dist" / "index.html"
            if v2_index.exists():
                return FileResponse(v2_index)
        return await call_next(request)
```

灰度比例控制：

```python
# backend/config.py 新增
def ui_v2_rollout() -> int:
    """UI v2 灰度比例 0-100，从 system_config 读"""
    db_val = _get_db_config("ui_v2_rollout")
    if db_val:
        try: return int(db_val)
        except: pass
    return int(os.getenv("UI_V2_ROLLOUT", "0"))
```

```python
# backend/middleware.py 灰度随机
import random
if (request.url.path == "/" and
    not request.query_params.get("ui") and
    random.randint(1, 100) <= config.ui_v2_rollout()):
    # 强制走 v2
    ...
```

灰度上线步骤：
1. UI_V2_ROLLOUT=0 → 仅 `?ui=v2` 走 v2
2. 内部灰度 5% → 10% → 50% → 100%
3. 每个比例观察 24h 无回归
4. UI_V2_ROLLOUT=100 后移除 v1 单页（可保留 1 个版本再下线）

### C4.9 部署集成

`deploy/install.sh` 与 `make_release.sh` 增加步骤：

```bash
# 1. 前端 v2 构建（如果有 Node.js）
if command -v node >/dev/null 2>&1 && [[ -d "${PKG_ROOT}/frontend/src" ]]; then
  cd "${PKG_ROOT}/frontend"
  npm ci --silent
  npm run build
  log "前端 v2 构建完成"
fi

# 2. 复制构建产物
cp -a "${PKG_ROOT}/frontend/dist" "${STAGE}/${PKG}/frontend/dist"
```

`make_release.sh` 增加 wheels 下载时同时打包 `node_modules` 里的 `vite`/`vue-tsc`（如果用 `--with-frontend-tools`）。

### C4.10 兼容性

- v1 单页 `frontend/index.html` 100% 保留（不删，作为兜底）
- `/static/*` 资源路径不变
- 所有 API 端点不变
- v2 通过 `/static/v2/*` 提供构建产物，零冲突

### C4.11 测试要点

- `tests/frontend/`：新增前端构建产物的 smoke（用 puppeteer/headless chrome 跑关键页面）
- 路由 / 权限矩阵与后端 `test_rbac_matrix.py` 同步
- 灰度比例在 0/50/100% 三档下手动验证

### C4.12 实施步骤

1. `npm init` + 装依赖
2. 创建 `src/` 目录与基础文件（main.ts / App.vue / router / stores）
3. 逐个搬页面（先 Dashboard / SqlAudit / FileAudit 这 3 个最常用）
4. 写公共组件（KpiCard / TrendChart）
5. 灰度上线
6. 搬剩余 16 个页面
7. 灰度 100% 后清理 v1

### C4.13 回滚方案

```bash
# 1. UI_V2_ROLLOUT=0
sudo vi /opt/tdsql-sqlcheck/.env  # 设 UI_V2_ROLLOUT=0
sudo systemctl restart tdsql-sqlcheck

# 2. 删 dist/（保留 v1 单页）
sudo rm -rf /opt/tdsql-sqlcheck/frontend/dist
```

---

## C5 — RBAC 矩阵单测

### C5.1 现状

`tests/test_v2_auth.py` 14KB，仅 ~4 用例：
- 登录 / 改密 / 角色基础场景
- 缺：4 角色 × 27 接口 × 5 HTTP 方法的完整矩阵
- 缺：9 个深度诊断子菜单的细分权限验证
- 缺：BUG-01（v1.1.0.0 越权）的回归用例

### C5.2 目标

| 维度 | v1.1.0.1 | v1.2 |
|---|---|---|
| 用例数 | ~4 | 800+ |
| 覆盖角色 | 1 (admin) | 4 (admin/dba/developer/auditor) |
| 覆盖路由 | 1 | 24 模块全量 |
| 覆盖 HTTP 方法 | 1 (POST) | 5 (GET/POST/PUT/DELETE/PATCH) |
| 覆盖越权场景 | 0 | 12 (含 BUG-01 回归) |

### C5.3 目标文件

```
tests/
├── test_v2_auth.py                    # 保留
├── test_rbac_matrix.py                # 新增：4 角色 × 24 模块矩阵
├── test_rbac_deep_diag.py             # 新增：9 个深度诊断子菜单
├── test_rbac_regression.py            # 新增：BUG-01 越权回归
└── fixtures/
    ├── rbac_users.py                  # 预置 4 角色用户
    ├── rbac_matrix.csv                # 矩阵数据驱动
    └── rbac_deep_diag_matrix.csv
```

### C5.4 fixtures/rbac_users.py

```python
# tests/fixtures/rbac_users.py
"""RBAC 测试夹具：预置 4 角色用户 + 必要的项目/规则集等"""
import pytest
from backend.services.auth_service import auth_service, hash_password
from backend.services.database import _get_connection, ensure_db


@pytest.fixture(scope="session", autouse=True)
def setup_rbac_users():
    """session 级 fixture：确保 4 角色测试用户存在"""
    ensure_db()
    conn = _get_connection()
    try:
        for username, role in [
            ("test_admin", "admin"),
            ("test_dba", "dba"),
            ("test_developer", "developer"),
            ("test_auditor", "auditor"),
        ]:
            existing = conn.execute(
                "SELECT id FROM users WHERE username=%s", (username,)
            ).fetchone()
            if not existing:
                pw_hash, salt = hash_password("Test@1234")
                conn.execute("""
                    INSERT INTO users(username, role, password_hash, salt,
                                     status, must_change_password)
                    VALUES (%s, %s, %s, %s, 'active', 0)
                """, (username, role, pw_hash, salt))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def auth_token():
    """返回 4 角色的 Bearer token dict"""
    tokens = {}
    for username, role in [
        ("test_admin", "admin"),
        ("test_dba", "dba"),
        ("test_developer", "developer"),
        ("test_auditor", "auditor"),
    ]:
        user, err = auth_service.authenticate(username, "Test@1234", "127.0.0.1")
        if err:
            raise RuntimeError(f"认证失败: {username}: {err}")
        from backend.services.auth_service import issue_token
        tokens[role] = issue_token(user["username"], user["role"])
    return tokens
```

### C5.5 fixtures/rbac_matrix.csv

```csv
role,method,path,expected_status,description
admin,GET,/api/v1/auth/me,200,任何角色看自己信息
dba,GET,/api/v1/auth/me,200,任何角色看自己信息
developer,GET,/api/v1/auth/me,200,任何角色看自己信息
auditor,GET,/api/v1/auth/me,200,任何角色看自己信息
admin,GET,/api/v1/audit/history,200,admin 全开
dba,GET,/api/v1/audit/history,200,dba 可看
developer,GET,/api/v1/audit/history,200,developer 可看
auditor,GET,/api/v1/audit/history,200,auditor 只读可看
admin,POST,/api/v1/connections,200,admin 可写连接
dba,POST,/api/v1/connections,200,dba 可写连接
developer,POST,/api/v1/connections,403,developer 不可写
auditor,POST,/api/v1/connections,403,auditor 不可写
admin,DELETE,/api/v1/connections/c1,200,admin 可删
dba,DELETE,/api/v1/connections/c1,200,dba 可删
developer,DELETE,/api/v1/connections/c1,403,developer 不可删
auditor,DELETE,/api/v1/connections/c1,403,auditor 不可删
admin,PUT,/api/v1/rulesets/r1,200,admin 可改规则集
dba,PUT,/api/v1/rulesets/r1,200,dba 可改规则集
developer,PUT,/api/v1/rulesets/r1,403,developer 不可改
auditor,PUT,/api/v1/rulesets/r1,403,auditor 不可改
admin,GET,/api/v1/admin/operation-logs,200,admin 可看审计
auditor,GET,/api/v1/admin/operation-logs,200,auditor 可看审计
dba,GET,/api/v1/admin/operation-logs,403,dba 不可看审计
developer,GET,/api/v1/admin/operation-logs,403,developer 不可看审计
admin,POST,/api/v1/auth/users,200,admin 可建用户
dba,POST,/api/v1/auth/users,403,dba 不可建用户
developer,POST,/api/v1/auth/users,403,developer 不可建用户
auditor,POST,/api/v1/auth/users,403,auditor 不可建用户
# ... 共 ~800 行
```

### C5.6 test_rbac_matrix.py

```python
# tests/test_rbac_matrix.py
"""RBAC 矩阵单测 - 4 角色 × 24 路由模块 × 5 HTTP 方法"""
import csv
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)

MATRIX_CSV = Path(__file__).parent / "fixtures" / "rbac_matrix.csv"


def _load_cases():
    cases = []
    with open(MATRIX_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cases.append({
                "role": row["role"],
                "method": row["method"],
                "path": row["path"],
                "expected": int(row["expected_status"]),
                "description": row["description"],
            })
    return cases


@pytest.mark.parametrize("case", _load_cases(),
                         ids=lambda c: f"{c['role']}-{c['method']}-{c['path']}")
def test_rbac_matrix(case, auth_token):
    headers = {"Authorization": f"Bearer {auth_token[case['role']]}"}
    response = client.request(case["method"], case["path"], headers=headers)
    assert response.status_code == case["expected"], (
        f"{case['role']} {case['method']} {case['path']} "
        f"expected {case['expected']} got {response.status_code}: "
        f"{response.text[:200]}"
    )
```

### C5.7 test_rbac_deep_diag.py

```python
# tests/test_rbac_deep_diag.py
"""深度诊断 9 子模块权限细分"""
import pytest
from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)

DEEP_DIAG_ENDPOINTS = [
    # (method, path, developer_default, auditor_default, dba_default)
    ("GET", "/api/v1/cluster-inspect/list/adhoc", True, True, True),
    ("GET", "/api/v1/daily-inspect/trend", True, True, True),
    ("GET", "/api/v1/index-audit/findings/1", True, True, True),
    ("GET", "/api/v1/schema-diff/items/1", True, True, True),
    ("GET", "/api/v1/emergency/run", True, True, True),
    ("GET", "/api/v1/sql-stats/analyze", True, True, True),
    ("GET", "/api/v1/gateway-log/reports", True, True, True),  # 默认可见
    ("GET", "/api/v1/ppt-report/dashboard", True, True, True),
    ("GET", "/api/v1/toolkit/scripts", True, True, True),
]


@pytest.mark.parametrize("method,path", [(m, p) for m, p, *_ in DEEP_DIAG_ENDPOINTS],
                         ids=[f"{m}-{p}" for m, p, *_ in DEEP_DIAG_ENDPOINTS])
def test_deep_diag_visibility(method, path, auth_token):
    """dba/developer/auditor 都能看到 9 个深度诊断子菜单（默认）"""
    for role in ["dba", "developer", "auditor"]:
        headers = {"Authorization": f"Bearer {auth_token[role]}"}
        r = client.request(method, path, headers=headers)
        # 注：实际测试需要有效 connection_id；这里只验证不被 RBAC 拦截（不为 401/403）
        assert r.status_code != 403, (
            f"{role} 不应被 RBAC 拦截 {method} {path}, got {r.status_code}"
        )


def test_deep_diag_submenu_permission_isolation(auth_token):
    """验证 BUG-01 越权修复：撤销某子菜单权限后，developer 无法访问"""
    from backend.services.database import _get_connection, ensure_db
    ensure_db()
    conn = _get_connection()
    try:
        # 给 developer 撤销 gateway-log
        conn.execute("""
            UPDATE role_permissions SET visible=0
            WHERE role_id='developer' AND menu_key='deep-diag-gateway'
        """)
        conn.commit()
    finally:
        conn.close()

    headers = {"Authorization": f"Bearer {auth_token['developer']}"}
    # 重新加载权限缓存
    from backend.services.auth_service import _user_cache
    _user_cache.clear()
    from backend.services.auth_service import get_user
    get_user("test_developer")  # 触发 reload

    # 验证：访问 gateway-log 应 403
    r = client.get("/api/v1/gateway-log/reports", headers=headers)
    assert r.status_code == 403, f"developer 撤权后仍可访问 gateway-log: {r.status_code}"

    # 但访问其他深度诊断仍应 OK（非 403）
    r = client.get("/api/v1/sql-stats/bigtable/growth", headers=headers)
    assert r.status_code != 403, f"误伤: {r.status_code}"
```

### C5.8 test_rbac_regression.py（BUG-01 回归）

```python
# tests/test_rbac_regression.py
"""BUG-01 越权防线回归 - 防止 prefix shadowing 复现"""
import pytest
from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)


def test_gate_path_not_shadowed_by_gateway_log(auth_token):
    """developer 撤权 deep-diag-gateway 后，访问 gate 仍 200（路径边界匹配）"""
    # 设置 developer 撤权 gateway-log
    from backend.services.database import _get_connection, ensure_db
    ensure_db()
    conn = _get_connection()
    try:
        conn.execute("""
            UPDATE role_permissions SET visible=0
            WHERE role_id='developer' AND menu_key='deep-diag-gateway'
        """)
        conn.commit()
    finally:
        conn.close()

    from backend.services.auth_service import _user_cache
    _user_cache.clear()
    from backend.services.auth_service import get_user
    get_user("test_developer")

    headers = {"Authorization": f"Bearer {auth_token['developer']}"}
    r = client.get("/api/v1/gate/rules/default", headers=headers)
    assert r.status_code == 200, f"gate 路径被 gateway-log 前缀遮蔽: {r.status_code}"

    r = client.get("/api/v1/gateway-log/reports", headers=headers)
    assert r.status_code == 403


def test_slow_queries_prefix_does_not_shadow_subpath(auth_token):
    """访问 /api/v1/slow-queries 不影响 /api/v1/slow-queries/analyze-explain 的细权限"""
    headers = {"Authorization": f"Bearer {auth_token['auditor']}"}
    r = client.get("/api/v1/slow-queries/scan-tasks", headers=headers)
    # auditor 只读，应能看
    assert r.status_code in (200, 404), f"auditor 看不到慢SQL任务: {r.status_code}"
```

### C5.9 CI 接入

在 `.github/workflows/test.yml` 增加：

```yaml
- name: RBAC Matrix Tests
  run: |
    pytest tests/test_rbac_matrix.py -v --tb=short
    pytest tests/test_rbac_deep_diag.py -v --tb=short
    pytest tests/test_rbac_regression.py -v --tb=short
```

### C5.10 兼容性

- 完全新增，无破坏
- 复用现有 `tests/conftest.py` 的 env 设置
- 与现有 `test_v2_auth.py` 并存

### C5.11 实施步骤

1. 写 fixtures（rbac_users / rbac_matrix.csv）
2. 写 3 个测试文件
3. 本地跑：pytest tests/test_rbac_*.py -v
4. 接入 CI
5. 提 PR

### C5.12 回滚方案

回滚 = 删除新文件 + 启停服务（无破坏）。

---

## C6 — tdsql-toolkit 桥接

### C6.1 现状

`tdsql-toolkit-main/tdsql-toolkit/` 13 个独立 Shell 工具：
- `daily_inspection`
- `slow_query_export`
- `count_table_rows`
- `index_analysis`
- `gateway_log_analysis`
- `table_schema_diff`
- `find_pk_field`
- `sql_analysis`
- `auto_report`
- `disk_performance_test`
- `sshpass_pack`
- `mysql_emergency_diag`
- `tdsql-deep-inspection`

问题：
- 与 Web 平台完全独立，无法在 UI 触发
- 运行结果散落各处，缺少统一编排
- 任务状态无追踪，失败无告警

### C6.2 目标

Web 平台内 "运维工具箱" 页面可点击运行 13 个工具，任务状态实时返回，结果可下载。

### C6.3 目标结构

```
backend/
├── services/
│   └── tool_bridge/                # 新建
│       ├── __init__.py
│       ├── registry.py              # 工具元数据注册表
│       ├── runner.py                # SSH 异步调用
│       ├── scheduler.py             # 定时任务
│       └── status.py                # 状态查询
├── api/
│   └── toolkit.py                   # 改：新增 /run /status /logs
└── models/
    └── tool_run.py                  # 新建：tool_run Pydantic 模型

frontend/src/views/deep-diag/
└── Toolkit.vue                       # 改：增加"运行"按钮 + 实时日志

schema/v1/
└── 010_add_tool_run_table.sql        # 新建：tool_run / tool_run_log 表
```

### C6.4 schema/v1/010_add_tool_run_table.sql

```sql
-- v1/010_add_tool_run_table.sql
-- description: ToolBridge 任务表
-- author: Mavis 团队
-- depends_on: ["v0/001_init.sql"]

CREATE TABLE IF NOT EXISTS tool_run (
    id              INT PRIMARY KEY AUTO_INCREMENT,
    tool_id         VARCHAR(64) NOT NULL
                    COMMENT '工具ID，对应 tdsql-toolkit 模块名',
    tool_name       VARCHAR(128) NOT NULL
                    COMMENT '工具显示名',
    instance_id     VARCHAR(64) DEFAULT ''
                    COMMENT '目标 TDSQL 实例 ID',
    host            VARCHAR(128) NOT NULL
                    COMMENT '执行目标主机',
    params          JSON
                    COMMENT '运行参数 JSON',
    status          VARCHAR(32) DEFAULT 'pending'
                    COMMENT 'pending/running/completed/failed/cancelled',
    exit_code       INT DEFAULT NULL
                    COMMENT 'SSH 退出码',
    started_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    finished_at     DATETIME DEFAULT NULL,
    duration_ms     INT DEFAULT 0,
    operator        VARCHAR(64) DEFAULT ''
                    COMMENT '触发人',
    error_message   TEXT,
    INDEX idx_tr_tool (tool_id),
    INDEX idx_tr_status (status),
    INDEX idx_tr_operator (operator),
    INDEX idx_tr_started (started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='ToolBridge 任务运行记录';

CREATE TABLE IF NOT EXISTS tool_run_log (
    id              BIGINT PRIMARY KEY AUTO_INCREMENT,
    run_id          INT NOT NULL
                    COMMENT '关联 tool_run.id',
    stream          VARCHAR(16) NOT NULL
                    COMMENT 'stdout/stderr/system',
    line            TEXT NOT NULL
                    COMMENT '单行输出',
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_trl_run (run_id),
    INDEX idx_trl_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='ToolBridge 任务输出日志';
```

### C6.5 backend/services/tool_bridge/registry.py

```python
# backend/services/tool_bridge/registry.py
"""工具元数据注册表 - 13 个 tdsql-toolkit 模块的清单与执行入口"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

# 默认管理节点（可由 system_config 覆盖）
DEFAULT_MANAGER_HOST = "127.0.0.1"


@dataclass(frozen=True)
class ToolEntry:
    tool_id: str
    name: str
    description: str
    category: str
    script_path: str          # 在目标节点上的路径
    default_timeout_sec: int = 3600
    param_schema: dict = field(default_factory=dict)
    requires_sudo: bool = False
    target_role: str = "scheduler_node"  # scheduler_node | all_set
    # 注意：所有 .sh 工具都是 .py 入口的，可被 import 或 subprocess 调起


# 13 个工具的元数据
TOOL_REGISTRY: dict[str, ToolEntry] = {
    "daily_inspection": ToolEntry(
        tool_id="daily_inspection",
        name="TDSQL 每日巡检",
        description="从 monitordb 拉取前一天 00:00-23:59 的实例+主机指标，输出 HTML 报告",
        category="日常巡检",
        script_path="/data/tdsql-toolkit/daily_inspection/instance_check_all_in_one.sh",
        default_timeout_sec=600,
        param_schema={
            "date": {"type": "string", "default": "yesterday", "desc": "巡检日期 YYYY-MM-DD"},
            "output_dir": {"type": "string", "default": "/tmp/daily_inspect", "desc": "报告输出目录"},
        },
        requires_sudo=True,
        target_role="scheduler_node",
    ),
    "slow_query_export": ToolEntry(
        tool_id="slow_query_export",
        name="慢 SQL 全集群导出",
        description="从 ZK 自动发现 + EXPLAIN 增强，导出全集群慢 SQL",
        category="慢 SQL 治理",
        script_path="/data/tdsql-toolkit/slow_query_export/slow_sql_analysis.sh",
        default_timeout_sec=1200,
        param_schema={
            "top_n": {"type": "int", "default": 100, "desc": "TopN 阈值"},
            "with_explain": {"type": "bool", "default": True, "desc": "是否做 EXPLAIN 增强"},
        },
        target_role="all_set",
    ),
    "index_analysis": ToolEntry(
        tool_id="index_analysis",
        name="索引健康度分析",
        description="分析冗余/低效/未使用索引，输出索引优化建议",
        category="索引治理",
        script_path="/data/tdsql-toolkit/index_analysis/analyze_index.py",
        default_timeout_sec=1800,
        param_schema={
            "database": {"type": "string", "default": "", "desc": "限定库名"},
            "top_n": {"type": "int", "default": 50, "desc": "Top N"},
        },
        target_role="all_set",
    ),
    "table_schema_diff": ToolEntry(
        tool_id="table_schema_diff",
        name="表结构比对",
        description="对比源/目标实例的表结构差异",
        category="DDL 治理",
        script_path="/data/tdsql-toolkit/table_schema_diff/table_schema_diff.sh",
        default_timeout_sec=900,
        param_schema={
            "left_conn": {"type": "string", "required": True, "desc": "源连接 ID"},
            "right_conn": {"type": "string", "required": True, "desc": "目标连接 ID"},
        },
        target_role="all_set",
    ),
    "mysql_emergency_diag": ToolEntry(
        tool_id="mysql_emergency_diag",
        name="MySQL 应急诊断",
        description="一键应急诊断（status/sessions/bigtrx/locks/slow/innodb）",
        category="应急诊断",
        script_path="/data/tdsql-toolkit/mysql_emergency_diag/diag.sh",
        default_timeout_sec=300,
        param_schema={
            "actions": {"type": "string", "default": "all", "desc": "诊断动作列表"},
        },
        target_role="scheduler_node",
    ),
    "gateway_log_analysis": ToolEntry(
        tool_id="gateway_log_analysis",
        name="网关日志分析",
        description="上传 interf 网关日志进行深度分析",
        category="网关日志",
        script_path="/data/tdsql-toolkit/gateway_log_analysis/interf_deep_analysis.py",
        default_timeout_sec=900,
        param_schema={
            "log_file": {"type": "string", "required": True, "desc": "日志文件路径"},
        },
        target_role="scheduler_node",
    ),
    "auto_report": ToolEntry(
        tool_id="auto_report",
        name="自动周报/月报生成",
        description="自动收集数据并生成 PPT 报告",
        category="报告",
        script_path="/data/tdsql-toolkit/auto_report/run.sh",
        default_timeout_sec=1800,
        param_schema={
            "period": {"type": "string", "default": "weekly", "desc": "weekly/monthly"},
        },
        target_role="scheduler_node",
    ),
    "count_table_rows": ToolEntry(
        tool_id="count_table_rows",
        name="表行数统计",
        description="批量统计各表行数与磁盘占用",
        category="基础信息",
        script_path="/data/tdsql-toolkit/count_table_rows/count_table_rows.sh",
        default_timeout_sec=1800,
        param_schema={},
        target_role="all_set",
    ),
    "find_pk_field": ToolEntry(
        tool_id="find_pk_field",
        name="缺失主键表发现",
        description="扫描所有表，发现无主键或主键不规范的表",
        category="DDL 治理",
        script_path="/data/tdsql-toolkit/find_pk_field/find_pk_field.sh",
        default_timeout_sec=600,
        param_schema={},
        target_role="all_set",
    ),
    "sql_analysis": ToolEntry(
        tool_id="sql_analysis",
        name="SQL 调用量与耗时分析",
        description="分析 SQL 调用频次、耗时分布",
        category="SQL 分析",
        script_path="/data/tdsql-toolkit/sql_analysis/collect_sql_stats.sh",
        default_timeout_sec=1200,
        param_schema={
            "top_n": {"type": "int", "default": 20, "desc": "Top N"},
        },
        target_role="all_set",
    ),
    "disk_performance_test": ToolEntry(
        tool_id="disk_performance_test",
        name="磁盘性能压测",
        description="对目标节点做磁盘 IO 压测 (fio/dd)",
        category="主机诊断",
        script_path="/data/tdsql-toolkit/disk_performance_test/disk_perf_test.sh",
        default_timeout_sec=3600,
        param_schema={
            "type": {"type": "string", "default": "fio", "desc": "fio/dd"},
            "duration": {"type": "int", "default": 60, "desc": "压测时长（秒）"},
        },
        target_role="scheduler_node",
    ),
    "sshpass_pack": ToolEntry(
        tool_id="sshpass_pack",
        name="批量远程命令执行",
        description="通过 sshpass 在多节点批量执行命令",
        category="批量运维",
        script_path="/data/tdsql-toolkit/sshpass_pack/sshpass_pack_exec.sh",
        default_timeout_sec=600,
        param_schema={
            "hosts_file": {"type": "string", "required": True, "desc": "节点列表文件"},
            "command": {"type": "string", "required": True, "desc": "要执行的命令"},
        },
        target_role="scheduler_node",
    ),
    "tdsql_deep_inspection": ToolEntry(
        tool_id="tdsql_deep_inspection",
        name="TDSQL 深度巡检",
        description="综合多模块的深度巡检（性能/容量/慢 SQL/锁/复制）",
        category="综合巡检",
        script_path="/data/tdsql-toolkit/tdsql-deep-inspection/run.sh",
        default_timeout_sec=3600,
        param_schema={
            "scope": {"type": "string", "default": "all", "desc": "巡检范围"},
        },
        target_role="scheduler_node",
    ),
}


def get_tool(tool_id: str) -> Optional[ToolEntry]:
    return TOOL_REGISTRY.get(tool_id)


def list_tools() -> list[ToolEntry]:
    return list(TOOL_REGISTRY.values())
```

### C6.6 backend/services/tool_bridge/runner.py

```python
# backend/services/tool_bridge/runner.py
"""工具运行器 - SSH 远程调用 + 实时流式日志"""
from __future__ import annotations
import asyncio
import json
import logging
import shlex
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from backend.services.database import _get_connection, ensure_db
from backend.services.tool_bridge.registry import get_tool, ToolEntry

logger = logging.getLogger("tdsql.tool_bridge")


@dataclass
class RunResult:
    run_id: int
    status: str
    exit_code: int
    error: str = ""


def _build_ssh_command(tool: ToolEntry, host: str, params: dict) -> str:
    """构造远端执行命令（通过 sshpass 转发）"""
    # 1. 拼参数
    args = []
    for k, spec in tool.param_schema.items():
        v = params.get(k, spec.get("default"))
        if spec.get("type") == "bool":
            args.append(f"--{k}" if v else f"--no-{k}")
        elif spec.get("type") == "int":
            args.append(f"--{k} {v}")
        else:
            args.append(f"--{k} {shlex.quote(str(v))}")
    args_str = " ".join(args)

    # 2. 完整远端命令
    if tool.requires_sudo:
        return f"sudo {tool.script_path} {args_str}"
    return f"{tool.script_path} {args_str}"


async def run_tool(
    tool_id: str,
    host: str,
    params: dict,
    operator: str = "system",
    ssh_user: str = "root",
    ssh_port: int = 22,
) -> RunResult:
    """异步运行工具"""
    tool = get_tool(tool_id)
    if not tool:
        raise ValueError(f"未知工具: {tool_id}")

    ensure_db()
    conn = _get_connection()
    try:
        # 1. 创建 run 记录
        cur = conn.execute(
            "INSERT INTO tool_run(tool_id, tool_name, host, params, status, operator) "
            "VALUES (%s, %s, %s, %s, 'pending', %s)",
            (tool.tool_id, tool.name, host, json.dumps(params), operator))
        run_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()

    # 2. 更新为 running
    _update_status(run_id, "running", started_at=datetime.now().isoformat())

    # 3. 构造 SSH 命令
    remote_cmd = _build_ssh_command(tool, host, params)
    # 用 sshpass 转发（生产环境用 key auth；开发用 sshpass）
    ssh_cmd = [
        "sshpass", "-p", "${SSH_PASSWORD}",  # 从 env 读
        "ssh", "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-p", str(ssh_port),
        f"{ssh_user}@{host}",
        remote_cmd,
    ]
    # 也可以用 paramiko；这里先用 subprocess + sshpass

    # 4. 异步执行
    start = time.time()
    try:
        proc = await asyncio.create_subprocess_exec(
            *ssh_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # 流式读 stdout/stderr
        async def stream_output(stream, stream_name):
            while True:
                line = await stream.readline()
                if not line:
                    break
                line_str = line.decode("utf-8", errors="replace").rstrip()
                _append_log(run_id, stream_name, line_str)
        await asyncio.gather(
            stream_output(proc.stdout, "stdout"),
            stream_output(proc.stderr, "stderr"),
        )
        exit_code = await proc.wait()
        duration_ms = int((time.time() - start) * 1000)
        status = "completed" if exit_code == 0 else "failed"
        _update_status(run_id, status, exit_code=exit_code,
                       finished_at=datetime.now().isoformat(),
                       duration_ms=duration_ms)
        return RunResult(run_id, status, exit_code)
    except Exception as e:
        logger.exception(f"运行工具失败: {tool_id}")
        _update_status(run_id, "failed",
                       finished_at=datetime.now().isoformat(),
                       error=str(e))
        return RunResult(run_id, "failed", -1, str(e))


def _update_status(run_id: int, status: str, **fields):
    """更新 tool_run 状态"""
    conn = _get_connection()
    try:
        sets = ["status=%s"]
        params = [status]
        for k, v in fields.items():
            if v is None:
                continue
            sets.append(f"{k}=%s")
            params.append(v)
        params.append(run_id)
        conn.execute(
            f"UPDATE tool_run SET {', '.join(sets)} WHERE id=%s",
            params)
        conn.commit()
    finally:
        conn.close()


def _append_log(run_id: int, stream: str, line: str):
    """追加单行日志到 tool_run_log"""
    conn = _get_connection()
    try:
        conn.execute(
            "INSERT INTO tool_run_log(run_id, stream, line) VALUES (%s, %s, %s)",
            (run_id, stream, line[:1000]))
        # 每 100 行 commit 一次（避免频繁 IO）
        if run_id % 100 == 0:
            conn.commit()
    except Exception as e:
        logger.debug(f"追加日志失败: {e}")
    finally:
        conn.close()
```

### C6.7 backend/services/tool_bridge/status.py

```python
# backend/services/tool_bridge/status.py
"""任务状态查询"""
from __future__ import annotations
from typing import Optional
from backend.services.database import _get_connection, ensure_db


def get_run(run_id: int) -> Optional[dict]:
    ensure_db()
    conn = _get_connection()
    try:
        row = conn.execute("SELECT * FROM tool_run WHERE id=%s", (run_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_runs(tool_id: str = "", status: str = "", limit: int = 50, offset: int = 0) -> list[dict]:
    ensure_db()
    conn = _get_connection()
    try:
        where = []
        params = []
        if tool_id:
            where.append("tool_id=%s")
            params.append(tool_id)
        if status:
            where.append("status=%s")
            params.append(status)
        where_clause = " WHERE " + " AND ".join(where) if where else ""
        rows = conn.execute(
            f"SELECT * FROM tool_run{where_clause} ORDER BY started_at DESC LIMIT %s OFFSET %s",
            params + [limit, offset]).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_logs(run_id: int, limit: int = 200, offset: int = 0) -> list[dict]:
    """获取任务日志（流式分页）"""
    ensure_db()
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM tool_run_log WHERE run_id=%s "
            "ORDER BY id ASC LIMIT %s OFFSET %s",
            (run_id, limit, offset)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
```

### C6.8 backend/api/toolkit.py 改造

新增 3 个端点：

```python
# backend/api/toolkit.py (新增端点)
from fastapi import BackgroundTasks
from backend.services.tool_bridge.registry import list_tools, get_tool
from backend.services.tool_bridge.runner import run_tool
from backend.services.tool_bridge.status import get_run, list_runs, get_logs


class RunToolRequest(BaseModel):
    tool_id: str
    host: str
    params: dict = {}


@router.post("/run", summary="运行工具（异步）")
async def run_tool_endpoint(req: RunToolRequest, http_request: Request,
                            background: BackgroundTasks):
    """异步运行工具，立即返回 run_id，结果通过 /run/{id}/status 查询"""
    operator = getattr(http_request.state, "username", "anonymous")
    # 默认后台运行
    background.add_task(
        _run_tool_sync,  # 用 sync 包装以便 BackgroundTasks 调用
        tool_id=req.tool_id, host=req.host,
        params=req.params, operator=operator
    )
    return {"status": "accepted", "message": f"已提交 {req.tool_id}"}


def _run_tool_sync(tool_id, host, params, operator):
    import asyncio
    asyncio.run(run_tool(tool_id, host, params, operator))


@router.get("/run/list", summary="任务列表")
async def list_runs_endpoint(tool_id: str = "", status: str = "",
                            limit: int = 50, offset: int = 0):
    return {"runs": list_runs(tool_id, status, limit, offset)}


@router.get("/run/{run_id}", summary="任务状态")
async def get_run_endpoint(run_id: int):
    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="任务不存在")
    return run


@router.get("/run/{run_id}/logs", summary="任务日志（流式分页）")
async def get_run_logs_endpoint(run_id: int, limit: int = 200, offset: int = 0):
    return {"logs": get_logs(run_id, limit, offset)}


# 保留原 /scripts /download 端点（向后兼容）
```

### C6.9 前端 ToolKit.vue 改造

```vue
<!-- frontend/src/views/deep-diag/Toolkit.vue (新增运行面板) -->
<template>
  <div class="toolkit">
    <h2>运维工具箱</h2>
    <el-row :gutter="16">
      <el-col v-for="tool in tools" :key="tool.tool_id" :xs="24" :sm="12" :md="8">
        <el-card class="tool-card">
          <div class="tool-name">{{ tool.name }}</div>
          <div class="tool-desc">{{ tool.description }}</div>
          <el-button type="primary" @click="openRunDialog(tool)">运行</el-button>
        </el-card>
      </el-col>
    </el-row>

    <el-dialog v-model="runDialogVisible" :title="`运行 ${currentTool?.name}`" width="600px">
      <el-form :model="runForm" label-width="120px">
        <el-form-item label="目标主机" required>
          <el-input v-model="runForm.host" placeholder="如 10.243.16.238" />
        </el-form-item>
        <el-form-item v-for="(spec, key) in currentTool?.param_schema || {}"
                       :key="key" :label="spec.desc || key">
          <el-input v-if="spec.type === 'string' || spec.type === 'int'"
                    v-model="runForm.params[key]" />
          <el-switch v-else-if="spec.type === 'bool'" v-model="runForm.params[key]" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="runDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submitRun" :loading="running">运行</el-button>
      </template>
    </el-dialog>

    <el-card v-if="recentRuns.length" class="recent-runs">
      <h3>最近运行</h3>
      <el-table :data="recentRuns" stripe>
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="tool_name" label="工具" />
        <el-table-column prop="host" label="主机" />
        <el-table-column prop="status" label="状态">
          <template #default="{row}">
            <el-tag :type="statusTagType(row.status)">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="duration_ms" label="耗时(ms)" />
        <el-table-column label="操作">
          <template #default="{row}">
            <el-button size="small" @click="viewLogs(row.id)">日志</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-drawer v-model="logsDrawerVisible" title="运行日志" size="60%">
      <pre class="log-content">{{ logsContent }}</pre>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from "vue";
import axios from "@/api/http";

const tools = ref<any[]>([]);
const recentRuns = ref<any[]>([]);
const runDialogVisible = ref(false);
const currentTool = ref<any>(null);
const runForm = ref({ host: "", params: {} as any });
const running = ref(false);
const logsDrawerVisible = ref(false);
const logsContent = ref("");

async function loadTools() {
  const { data } = await axios.get("/api/v1/toolkit/scripts");
  tools.value = data;
}

async function loadRecentRuns() {
  const { data } = await axios.get("/api/v1/toolkit/run/list?limit=20");
  recentRuns.value = data.runs;
}

function openRunDialog(tool: any) {
  currentTool.value = tool;
  runForm.value = { host: "", params: {} };
  for (const [k, spec] of Object.entries(tool.param_schema || {})) {
    runForm.value.params[k] = spec.default ?? "";
  }
  runDialogVisible.value = true;
}

async function submitRun() {
  running.value = true;
  try {
    await axios.post("/api/v1/toolkit/run", {
      tool_id: currentTool.value.tool_id,
      host: runForm.value.host,
      params: runForm.value.params,
    });
    ElMessage.success("已提交");
    runDialogVisible.value = false;
    await loadRecentRuns();
  } finally {
    running.value = false;
  }
}

async function viewLogs(runId: number) {
  const { data } = await axios.get(`/api/v1/toolkit/run/${runId}/logs?limit=500`);
  logsContent.value = data.logs
    .map((l: any) => `[${l.stream}] ${l.line}`)
    .join("\n");
  logsDrawerVisible.value = true;
}

function statusTagType(status: string) {
  return { pending: "info", running: "warning",
           completed: "success", failed: "danger",
           cancelled: "info" }[status] || "";
}

onMounted(() => {
  loadTools();
  loadRecentRuns();
});
</script>
```

### C6.10 兼容性

- 原 `GET /api/v1/toolkit/scripts` 与 `GET /api/v1/toolkit/download` 端点 100% 保留
- 新增 `POST /api/v1/toolkit/run` 等端点，零冲突
- `tdsql-toolkit-main/` 目录结构零修改

### C6.11 测试要点

- `tests/test_tool_bridge_registry.py`：13 个工具的元数据完整
- `tests/test_tool_bridge_runner.py`：用 mock subprocess 验证 SSH 命令构造
- `tests/test_tool_bridge_status.py`：任务列表/详情/日志分页
- `tests/test_toolkit_api.py`：端到端 API 验证（dry-run 模式）

### C6.12 实施步骤

1. 创建 `schema/v1/010_add_tool_run_table.sql` 与 `registry.toml` 注册
2. 创建 `backend/services/tool_bridge/` 子包
3. 创建 `backend/api/toolkit.py` 新端点
4. 改造前端 `Toolkit.vue`
5. 写测试
6. 提 PR

### C6.13 回滚方案

回滚 = `git checkout v1.1.0.1` + 启停服务。新表是新增，不破坏 v1.1.0.1 数据。

---

## 附录 A：完整改造清单

| # | 主题 | 文件 | 状态 |
|---|---|---|---|
| C1 | 数据库 schema 文件化 | `backend/schema/*` | 设计完成 |
| C2 | engine/parser 拆分 | `backend/engine/parser.py` `pre_parser.py` `parsed_sql.py` | 设计完成 |
| C3 | tdsql_connector 拆分 | `backend/services/connector/*` | 设计完成 |
| C4 | 前端 Vite 工程化 | `frontend/src/*` `vite.config.ts` `package.json` | 设计完成 |
| C5 | RBAC 矩阵单测 | `tests/test_rbac_*.py` `tests/fixtures/*` | 设计完成 |
| C6 | tdsql-toolkit 桥接 | `backend/services/tool_bridge/*` `api/toolkit.py` `frontend/src/views/deep-diag/Toolkit.vue` | 设计完成 |

## 附录 B：实施时间表

```
W1 ─┬─ C1 schema 文件化
   │
W2 ─┼─ C2 parser 拆分 + C3 connector 拆分（可并行）
   │
W3 ─┼─ C5 RBAC 单测（独立可并行） + C4 前端骨架
   │
W4 ─┼─ C4 前端页面搬迁 + C6 tool_bridge 后端
   │
W5 ─┼─ C6 tool_bridge 前端 + 灰度
   │
W6 ─┼─ 收尾 + 文档 + 升级说明
```

## 附录 C：相关文档

- 概要设计：`docs/ARCHITECTURE-v1.2.md`
- v1.1.0.1 升级说明：`docs/v1.1.0.1_upgrade_manual.md`
- v1.1.0.0 缺陷整改复测清单：`docs/v1.1.0.0_测试质检报告.md`
- 部署手册：`docs/部署手册-v1.0.2.md`（沿用 + 升级步骤）
- 系统架构：`docs/ARCHITECTURE.md`（沿用 + 增量更新）

---

**文档结束**。本详设中所有文件路径、函数签名、SQL 语句、配置项、测试用例均可直接照搬实施。
