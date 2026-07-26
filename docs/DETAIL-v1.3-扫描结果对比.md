# TDSQL-SQLCheck 扫描结果纵向对比能力 — 详细设计说明书

| 项 | 内容 |
|---|---|
| 目标版本 | V1.3.0.0 |
| 基线版本 | **V1.2.0.9**（main @ `a700de5`，2026-07-26 逐项复核，文中行号均以此为准） |
| 配套文档 | 《需求分析与概要设计》《接口说明书》 |
| 定位 | **照图施工级**：明确到文件、函数、字段、算法、SQL 与前端代码结构 |
| 编制 | 智能体 A |

---

## 1. 编码约定（施工前必读）

| 约定 | 说明 |
|---|---|
| SQL 占位符 | 统一用 `?`。`backend/services/database.py::_MySQLCompatCursor.execute()`（:54）会自动把独立的 `?` 转成 MySQL 的 `%s`，**不要手写 `%s`** |
| 取连接 | `from backend.services.database import _get_connection, ensure_db`；`conn = _get_connection()`，务必 `try/finally: conn.close()`（close 是归还连接池） |
| 查询返回 | `fetchone()/fetchall()` 返回 dict-like，可直接 `dict(row)` |
| 时间格式 | 存 `DATETIME`，Python 侧统一 `datetime.now().isoformat()`，前端用既有 `formatTime()` |
| 日志 | `logger = logging.getLogger(__name__)`，与各服务保持一致 |
| 异常 | 快照生成链路**全部吞异常**（仅告警），不得影响扫描主流程；比对链路正常抛 `HTTPException` |

---

## 2. 交付物清单（新增 / 改造）

### 2.1 新增文件

| 文件 | 职责 | 预估行数 |
|---|---|---|
| `backend/schema/v2/020_scan_compare_tables.sql` | 两张新表 DDL | ~60 |
| `backend/services/scan_snapshot_service.py` | 快照生成/查询/重建 | ~320 |
| `backend/services/snapshot_extractors/__init__.py` | 抽取器注册与分发 | ~40 |
| `backend/services/snapshot_extractors/base.py` | `IssueItem` 数据类 + 指纹工具 | ~90 |
| `backend/services/snapshot_extractors/schema_audit.py` | 元数据审核抽取器 | ~130 |
| `backend/services/snapshot_extractors/slow_scan.py` | 慢SQL抽取器 | ~100 |
| `backend/services/snapshot_extractors/bigtable.py` | 大表抽取器 | ~110 |
| `backend/services/scan_compare_service.py` | 比对引擎 + 汇总 | ~280 |
| `backend/services/scan_compare_report.py` | 对比报告 HTML 渲染 | ~260 |
| `backend/api/scan_compare.py` | 统一 API | ~230 |
| `tests/test_scan_compare.py` | 单测 | ~200 |

### 2.2 改造文件（行号为 V1.2.0.9 现状）

| 文件 | 改造点 | 章节 |
|---|---|---|
| `backend/services/database.py` | `audit_history` 补 `db_name` 列 + 组合索引；新增 `_add_index_if_not_exists` 辅助函数 | §11.1 |
| `backend/services/audit_service.py` | `_save_audit_history()`（:28）增加 `connection_id`/`db_name` 参数 | §11.1 |
| `backend/api/sql_audit.py` | `extract_and_audit`（:141）传实例信息 + 挂快照；`get_extracted_reports`（:267）加筛选 | §11.1 / §5.3.1 |
| `backend/services/slow_query_service.py` | `get_scan_tasks()`（:431）增加筛选参数 | §11.3 |
| `backend/api/slow_query.py` | `list_scan_tasks`（:127）透传筛选参数 | §11.3 |
| `backend/services/scan_service.py` | `run_scan()` 末尾挂快照生成 | §5.3.2 |
| `backend/services/bigtable_service.py` | `get_inventory()`（:46）补日期过滤；`save_inventory()`（:22）挂快照 | §11.2 / §5.3.3 |
| `backend/api/bigtable.py` | `get_inventory`（:14）加 `inspection_date` 参数 | §11.2 |
| `backend/services/retention_service.py` | `CLEANABLE_TABLES`（:19）增加两表 | §4.4 |
| `backend/services/auth_service.py` | `ALL_MENU_KEYS`（:331）/`MENU_LABELS`（:344）/`_PATH_TO_MENU`（:230）增加 `scan-compare` | §8.2 |
| `backend/main.py` | 注册 `scan_compare` 路由 | §8.1 |
| `frontend/index.html` | 三模块扫描历史区 + 对比结果视图（三模块块位于 :297 / :412 / :712） | §9 |
| `frontend/static/js/app.js` | 对比相关状态与方法 | §9 |

---

## 3. 核心数据结构

### 3.1 IssueItem（问题项）

比对的最小单元。定义于 `snapshot_extractors/base.py`：

```python
@dataclass
class IssueItem:
    key: str            # 稳定指纹，比对主键（见 §3.3）
    object_name: str    # 归属对象：schema.table / SQL指纹短标识
    object_type: str    # TABLE | VIEW | INDEX | SQL | ''
    issue_type: str     # 问题类型：rule_id / slow问题类型 / 大表问题类型
    severity: str       # ERROR | WARNING | INFO
    title: str          # 一行简述（报告主文案）
    detail: str         # 详细描述
    suggestion: str     # 修复建议
    attrs: dict         # 可变属性，用于 CHANGED 判定（见 §6.3）
```

### 3.2 快照 JSON Schema

存入 `scan_snapshots.snapshot_json` 的完整结构：

```jsonc
{
  "schema_version": 1,                 // 快照结构版本，升级时用于兼容处理
  "module": "schema_audit",            // schema_audit | slow_scan | bigtable
  "fingerprint_algo": "v1",            // 指纹算法版本
  "truncated": false,                  // 问题项是否被截断（超限保护）
  "meta": {
    "biz_ref_id": "1024",              // 源记录ID
    "connection_id": "conn-8f2a",
    "connection_name": "核心交易库-SIT",
    "db_name": "trade_core",
    "scan_label": "extracted_trade_core_20260701_020000.sql",
    "scan_started_at": "2026-07-01T02:00:00",
    "scan_finished_at": "2026-07-01T02:03:11",
    "time_window_start": "",           // 慢SQL专用
    "time_window_end": "",
    "created_by": "admin"
  },
  "stats": {
    "object_total": 268,               // 扫描对象总数（表/视图/SQL/大表）
    "issue_total": 412,
    "by_severity": { "ERROR": 190, "WARNING": 222, "INFO": 0 },
    "by_issue_type": { "R003": 42, "R012": 88 }
  },
  "issues": [
    {
      "key": "a1b2c3d4e5f60718",
      "object_name": "trade_core.t_order",
      "object_type": "TABLE",
      "issue_type": "R003",
      "severity": "ERROR",
      "title": "CREATE TABLE 未指定主键",
      "detail": "TDSQL 要求每个表必须有主键",
      "suggestion": "建议添加自增主键: id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY",
      "attrs": { }
    }
  ]
}
```

**字段裁剪原则**：快照**只存比对与报告展示必需的字段**。明确**不存**：完整 DDL 文本、EXPLAIN 执行计划、表结构 JSON、索引明细等大字段（这些仍在源表，需要时按 `biz_ref_id` 回查）。目标单份 10～100KB。

**超限保护**：`issues` 超过 `SNAPSHOT_MAX_ISSUES`（默认 20000）时，按 severity 降序截断，置 `truncated=true`，报告顶部提示。

### 3.3 指纹算法 v1（**本设计的核心**）

#### 3.3.1 通用工具

```python
import hashlib, re

def _fp(*parts: str) -> str:
    """指纹：各部分归一化后用 \x1f 连接取 sha1 前 16 位"""
    norm = [(p or "").strip().lower() for p in parts]
    raw = "\x1f".join(norm)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

_VOLATILE = re.compile(r"\d+|'[^']*'|\"[^\"]*\"|`[^`]*`")

def _stable_text(msg: str) -> str:
    """归一化消息文本：去掉数字与引号内容等易变部分，用于同对象同规则多次命中的区分位"""
    return _VOLATILE.sub("#", msg or "").strip().lower()
```

> **红线：指纹严禁包含 `line_number`、报告序号 `#idx`、自增 `id`、扫描时间。** 违反将导致比对全部误判为"新增"，需求直接失败。

#### 3.3.2 各模块指纹规则

| 模块 | 指纹输入 | 说明 |
|---|---|---|
| `schema_audit` | `_fp(module, object_name, object_type, rule_id, disc)` | `disc` 为同对象同规则多次命中的区分位，取 `_fp(_stable_text(message))[:8]`；仍冲突时追加该对象内出现序号 |
| `slow_scan` | `_fp(module, db_name, sha1(fingerprint))` | `fingerprint` 为平台既有 SQL 归一化指纹，天然跨扫描稳定 |
| `bigtable` | `_fp(module, schema_name, table_name, issue_type)` | 一张表可能命中多个问题类型，各自独立成项 |

#### 3.3.3 对象名解析（schema_audit 专用）

元数据审核的审核结果不带对象名，需从 DDL 文本解析：

```python
_OBJ_RE = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:ALGORITHM\s*=\s*\w+\s+)?"
    r"(?:DEFINER\s*=\s*\S+\s+)?(?:SQL\s+SECURITY\s+\w+\s+)?"
    r"(TABLE|VIEW|INDEX)\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"[`\"]?([\w$]+)[`\"]?(?:\s*\.\s*[`\"]?([\w$]+)[`\"]?)?",
    re.IGNORECASE)

_ALTER_RE = re.compile(
    r"ALTER\s+TABLE\s+[`\"]?([\w$]+)[`\"]?(?:\s*\.\s*[`\"]?([\w$]+)[`\"]?)?",
    re.IGNORECASE)

def parse_object(sql: str, default_db: str) -> tuple[str, str]:
    """返回 (object_name='db.obj', object_type)；解析失败返回 ('<unparsed:hash>', '')"""
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
    return f"<unparsed:{_fp(_stable_text(sql))[:8]}>", ""
```

**关键实现要求**：快照生成发生在审核完成后的**同一进程内**，应消费**内存中的完整 `results` 对象**解析对象名，
**不要**读 `audit_history.results_json`（该字段的 `sql` 被截断为 500 字符）。
仅在"存量回填/重建"场景才回落到 DB JSON —— 因 `CREATE TABLE xxx` 位于语句开头，截断不影响对象名解析。

---

## 4. 数据库设计

### 4.1 迁移文件

新建 `backend/schema/v2/020_scan_compare_tables.sql`。
命名须符合 `backend/schema/loader.py` 规则：`v{N}/{NNN}_{name}.sql`；
`migrator.py` 以 `version_key = f"v{version}_{sequence:03d}_{name}"` 记录到 `schema_migrations`，幂等执行。

### 4.2 完整 DDL

```sql
-- v1.3 扫描结果纵向对比：快照表与对比报告表

CREATE TABLE IF NOT EXISTS scan_snapshots (
    id                  BIGINT PRIMARY KEY AUTO_INCREMENT,
    module              VARCHAR(32)  NOT NULL COMMENT 'schema_audit|slow_scan|bigtable',
    biz_ref_id          VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '源记录ID',
    connection_id       VARCHAR(64)  NOT NULL DEFAULT '',
    connection_name     VARCHAR(256) NOT NULL DEFAULT '',
    db_name             VARCHAR(128) NOT NULL DEFAULT '',
    scan_label          VARCHAR(512) NOT NULL DEFAULT '' COMMENT '展示名',
    scan_started_at     DATETIME     NULL,
    scan_finished_at    DATETIME     NOT NULL COMMENT '比对方向判定依据',
    time_window_start   VARCHAR(32)  NOT NULL DEFAULT '' COMMENT '慢SQL可比性',
    time_window_end     VARCHAR(32)  NOT NULL DEFAULT '',
    object_total        INT          NOT NULL DEFAULT 0,
    issue_total         INT          NOT NULL DEFAULT 0,
    error_count         INT          NOT NULL DEFAULT 0,
    warning_count       INT          NOT NULL DEFAULT 0,
    fingerprint_algo    VARCHAR(16)  NOT NULL DEFAULT 'v1',
    schema_version      INT          NOT NULL DEFAULT 1,
    truncated           TINYINT      NOT NULL DEFAULT 0,
    snapshot_json       LONGTEXT     NULL COMMENT '快照主体',
    snapshot_size       INT          NOT NULL DEFAULT 0,
    source_kind         VARCHAR(16)  NOT NULL DEFAULT 'live' COMMENT 'live=扫描实时生成, rebuild=回填',
    created_by          VARCHAR(64)  NOT NULL DEFAULT '',
    created_at          DATETIME     DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_snap_module_biz (module, biz_ref_id),
    INDEX idx_snap_query (module, connection_id, scan_finished_at),
    INDEX idx_snap_db (module, db_name),
    INDEX idx_snap_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS scan_compare_reports (
    id                  BIGINT PRIMARY KEY AUTO_INCREMENT,
    module              VARCHAR(32)  NOT NULL,
    connection_id       VARCHAR(64)  NOT NULL DEFAULT '',
    connection_name     VARCHAR(256) NOT NULL DEFAULT '',
    db_name             VARCHAR(128) NOT NULL DEFAULT '',
    base_snapshot_id    BIGINT       NOT NULL,
    target_snapshot_id  BIGINT       NOT NULL,
    base_scan_at        DATETIME     NULL,
    target_scan_at      DATETIME     NULL,
    title               VARCHAR(512) NOT NULL DEFAULT '',
    base_total          INT          NOT NULL DEFAULT 0,
    target_total        INT          NOT NULL DEFAULT 0,
    fixed_count         INT          NOT NULL DEFAULT 0,
    new_count           INT          NOT NULL DEFAULT 0,
    remain_count        INT          NOT NULL DEFAULT 0,
    changed_count       INT          NOT NULL DEFAULT 0,
    fix_rate            DOUBLE       NOT NULL DEFAULT 0,
    summary_json        LONGTEXT     NULL COMMENT '汇总，不含明细',
    created_by          VARCHAR(64)  NOT NULL DEFAULT '',
    created_at          DATETIME     DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_cmp_query (module, connection_id, created_at),
    INDEX idx_cmp_snap (base_snapshot_id, target_snapshot_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 4.3 索引设计说明

- `uk_snap_module_biz`：**幂等保护**。同一源记录重复触发快照生成时用 `INSERT ... ON DUPLICATE KEY UPDATE` 覆盖，避免重复快照污染列表。
- `idx_snap_query (module, connection_id, scan_finished_at)`：完全覆盖列表页最高频查询"某模块 + 某实例 + 按时间倒序分页"。

### 4.4 数据保留策略

`backend/services/retention_service.py` 的 `CLEANABLE_TABLES`（:19）增加：

```python
CLEANABLE_TABLES = {
    ...  # 既有 7 张表保持不变
    "scan_snapshots": "created_at",
    "scan_compare_reports": "created_at",
}
```

并在 `database.py` 默认策略初始化处（`INSERT IGNORE INTO retention_policies` 附近，约 :1358）插入默认值：
`scan_snapshots = 365 天`、`scan_compare_reports = 365 天`
（对比是长周期行为，需长于慢SQL默认策略；下限 7 天由既有 `set_policy` 校验保证）。

---

## 5. 快照层详细设计

### 5.1 `scan_snapshot_service.py` 对外接口

```python
MODULES = ("schema_audit", "slow_scan", "bigtable")
SNAPSHOT_MAX_ISSUES = 20000
FINGERPRINT_ALGO = "v1"
SNAPSHOT_SCHEMA_VERSION = 1

def create_snapshot(module: str, meta: dict, issues: list[IssueItem],
                    object_total: int = 0, source_kind: str = "live") -> int | None:
    """构建并落库快照，返回 snapshot_id；任何异常仅告警返回 None（不阻断主流程）"""

def safe_create_snapshot(module, meta, issues, object_total=0, source_kind="live") -> int | None:
    """create_snapshot 的吞异常包装。三模块挂载点统一调用此函数"""

def list_snapshots(module: str = "", connection_id: str = "", db_name: str = "",
                   date_from: str = "", date_to: str = "",
                   limit: int = 20, offset: int = 0) -> dict:
    """列表查询，返回 {total, items[]}；items 不含 snapshot_json"""

def get_snapshot(snapshot_id: int, with_issues: bool = True) -> dict | None:
    """取单个快照；with_issues=False 时不解析 snapshot_json（列表/校验场景）"""

def rebuild_snapshots(module: str, limit: int = 200,
                      overwrite: bool = False) -> dict:
    """从源表回填历史快照，返回 {scanned, created, skipped, failed}"""
```

### 5.2 `create_snapshot` 实现要点

```python
def create_snapshot(module, meta, issues, object_total=0, source_kind="live"):
    if module not in MODULES:
        raise ValueError(f"不支持的模块: {module}")

    truncated = False
    if len(issues) > SNAPSHOT_MAX_ISSUES:
        order = {"ERROR": 0, "WARNING": 1, "INFO": 2}
        issues = sorted(issues, key=lambda i: order.get(i.severity, 9))[:SNAPSHOT_MAX_ISSUES]
        truncated = True

    # 1) 指纹去重（同 key 只保留一条，防止抽取器产生重复）
    seen, uniq = set(), []
    for it in issues:
        if it.key in seen:
            continue
        seen.add(it.key); uniq.append(it)

    # 2) 统计
    by_sev, by_type = {}, {}
    for it in uniq:
        by_sev[it.severity] = by_sev.get(it.severity, 0) + 1
        by_type[it.issue_type] = by_type.get(it.issue_type, 0) + 1

    payload = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "module": module,
        "fingerprint_algo": FINGERPRINT_ALGO,
        "truncated": truncated,
        "meta": meta,
        "stats": {"object_total": object_total, "issue_total": len(uniq),
                  "by_severity": by_sev, "by_issue_type": by_type},
        "issues": [asdict(i) for i in uniq],
    }
    blob = json.dumps(payload, ensure_ascii=False)

    # 3) 幂等落库
    conn = _get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO scan_snapshots
              (module, biz_ref_id, connection_id, connection_name, db_name, scan_label,
               scan_started_at, scan_finished_at, time_window_start, time_window_end,
               object_total, issue_total, error_count, warning_count,
               fingerprint_algo, schema_version, truncated, snapshot_json, snapshot_size,
               source_kind, created_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON DUPLICATE KEY UPDATE
               issue_total=VALUES(issue_total), error_count=VALUES(error_count),
               warning_count=VALUES(warning_count), object_total=VALUES(object_total),
               snapshot_json=VALUES(snapshot_json), snapshot_size=VALUES(snapshot_size),
               truncated=VALUES(truncated), scan_finished_at=VALUES(scan_finished_at)
        """, (module, str(meta.get("biz_ref_id","")), meta.get("connection_id",""),
              meta.get("connection_name",""), meta.get("db_name",""), meta.get("scan_label","")[:500],
              meta.get("scan_started_at"), meta.get("scan_finished_at") or datetime.now().isoformat(),
              meta.get("time_window_start",""), meta.get("time_window_end",""),
              object_total, len(uniq), by_sev.get("ERROR",0), by_sev.get("WARNING",0),
              FINGERPRINT_ALGO, SNAPSHOT_SCHEMA_VERSION, 1 if truncated else 0,
              blob, len(blob), source_kind, meta.get("created_by","")))
        conn.commit()
        return getattr(cur, "lastrowid", None)
    finally:
        conn.close()
```

### 5.3 三模块抽取器与挂载点

#### 5.3.1 元数据审核 `snapshot_extractors/schema_audit.py`

**输入**：`results: list[AuditResult]`（内存对象）、`db_name`
**输出**：`list[IssueItem]`、`object_total`

```python
def extract(results, db_name: str) -> tuple[list[IssueItem], int]:
    items, objects, per_obj_rule = [], set(), {}
    for r in results:
        obj_name, obj_type = parse_object(r.sql, db_name)
        objects.add(obj_name)
        for v in (r.violations or []):
            sev = v.severity.value if hasattr(v.severity, "value") else str(v.severity)
            disc = _fp(_stable_text(v.message))[:8]
            ck = (obj_name, v.rule_id, disc)
            per_obj_rule[ck] = per_obj_rule.get(ck, 0) + 1
            seq = per_obj_rule[ck]
            key = _fp("schema_audit", obj_name, obj_type, v.rule_id, disc,
                      "" if seq == 1 else str(seq))
            items.append(IssueItem(
                key=key, object_name=obj_name, object_type=obj_type,
                issue_type=v.rule_id, severity=sev,
                title=f"[{v.rule_id}] {v.message}"[:500],
                detail=v.message or "", suggestion=v.suggestion or "",
                attrs={"severity": sev}))
    return items, len(objects)
```

**挂载点**：`backend/api/sql_audit.py::extract_and_audit`（现签名
`async def extract_and_audit(http_request: Request, payload: dict)`，:141），
在 `_save_audit_history(...)` 取得 `report_id` 之后（现 :246 附近）追加。
端点内**现成可用**的上下文变量：`connection_id = payload.get("connection_id")`（:145）、
`conn_info = registry.get_saved(connection_id) or {}`（:151，含 `name` 字段）、`target_db`（:160）、`filename`（:234）：

```python
# —— 旁路：生成对比快照（失败不影响审核主流程）——
snapshot_id = None
try:
    from backend.services.snapshot_extractors.schema_audit import extract as _extract
    from backend.services import scan_snapshot_service as _snap
    _items, _obj_total = _extract(results, target_db)
    snapshot_id = _snap.safe_create_snapshot("schema_audit", {
        "biz_ref_id": str(report_id),
        "connection_id": connection_id,
        "connection_name": conn_info.get("name", ""),
        "db_name": target_db,
        "scan_label": filename,
        "scan_started_at": _started_at,          # 端点入口处记录 datetime.now().isoformat()
        "scan_finished_at": datetime.now().isoformat(),
        "created_by": _operator(http_request),
    }, _items, _obj_total)
except Exception as e:
    logger.warning(f"生成元数据审核快照失败: {e}")
# 响应体中增加 "snapshot_id": snapshot_id
```

#### 5.3.2 慢SQL扫描 `snapshot_extractors/slow_scan.py`

**输入**：`task_id`（从 `slow_queries WHERE scan_task_id=?` 读取）
**问题项口径**：`severity IN ('ERROR','WARNING')` 的慢SQL记为问题项（INFO 级仅计入 object_total）。

```python
def extract(task_id: int, db_name_default: str = "") -> tuple[list[IssueItem], int]:
    conn = _get_connection()
    try:
        rows = conn.execute("""
            SELECT fingerprint, db_name, avg_time_ms, max_time_ms, exec_count,
                   severity, problem_type, suggestion, last_seen, involved_tables
            FROM slow_queries WHERE scan_task_id = ?
        """, (task_id,)).fetchall()
    finally:
        conn.close()

    items, total = [], 0
    for r in rows:
        d = dict(r); total += 1
        if (d.get("severity") or "").upper() not in ("ERROR", "WARNING"):
            continue
        db = d.get("db_name") or db_name_default
        fp_hash = hashlib.sha1((d.get("fingerprint") or "").encode()).hexdigest()[:16]
        items.append(IssueItem(
            key=_fp("slow_scan", db, fp_hash),
            object_name=(d.get("involved_tables") or db or "")[:200],
            object_type="SQL",
            issue_type=(d.get("problem_type") or "SLOW")[:64],
            severity=(d.get("severity") or "WARNING").upper(),
            title=(d.get("fingerprint") or "")[:300],
            detail=f"平均耗时 {d.get('avg_time_ms',0):.1f}ms / 执行 {d.get('exec_count',0)} 次",
            suggestion=(d.get("suggestion") or "")[:1000],
            attrs={"avg_time_ms": float(d.get("avg_time_ms") or 0),
                   "max_time_ms": float(d.get("max_time_ms") or 0),
                   "exec_count": int(d.get("exec_count") or 0),
                   "severity": (d.get("severity") or "").upper(),
                   "last_seen": d.get("last_seen") or ""}))
    return items, total
```

**挂载点**：`backend/services/scan_service.py::run_scan()` 末尾（任务落库、慢SQL入库完成后）调用，
`meta` 中必须带上 `time_window_start/end`（取自 `scan_tasks`），用于 §6.1 可比性校验。

#### 5.3.3 大表治理 `snapshot_extractors/bigtable.py`

**问题类型枚举**（沿用 `engine` 既有分级口径，不新增阈值）：

| issue_type | 判定 | severity |
|---|---|---|
| `OVERSIZE` | `level` 属于大表分级（L1/L2/L3 等） | L1→ERROR，其余→WARNING |
| `NO_PARTITION` | `is_partitioned = 0` 且已判定为大表 | WARNING |
| `NO_SHARD_KEY` | `shard_key` 为空 | WARNING |
| `PARTITION_WATERMARK` | 关联 `partition_watermarks.status` 异常 | ERROR |

```python
def extract(connection_id: str, inspection_date: str) -> tuple[list[IssueItem], int]:
    conn = _get_connection()
    try:
        rows = conn.execute("""
            SELECT schema_name, table_name, size_gb, rows_count, level,
                   is_partitioned, partition_count, shard_key
            FROM bigtable_inventory
            WHERE connection_id = ? AND inspection_date = ?
        """, (connection_id, inspection_date)).fetchall()
    finally:
        conn.close()

    items = []
    for r in rows:
        d = dict(r)
        obj = f"{d['schema_name']}.{d['table_name']}"
        base_attrs = {"size_gb": float(d.get("size_gb") or 0),
                      "rows_count": int(d.get("rows_count") or 0),
                      "level": d.get("level") or ""}
        def _add(itype, sev, title, sug):
            items.append(IssueItem(
                key=_fp("bigtable", d["schema_name"], d["table_name"], itype),
                object_name=obj, object_type="TABLE", issue_type=itype,
                severity=sev, title=title,
                detail=f"体量 {base_attrs['size_gb']:.2f}GB / {base_attrs['rows_count']} 行 / 等级 {base_attrs['level']}",
                suggestion=sug, attrs=dict(base_attrs)))

        _add("OVERSIZE", "ERROR" if str(d.get("level")).upper().startswith("L1") else "WARNING",
             f"大表 {obj} 体量 {base_attrs['size_gb']:.2f}GB（等级 {base_attrs['level']}）",
             "建议纳入分区/归档治理")
        if not d.get("is_partitioned"):
            _add("NO_PARTITION", "WARNING", f"{obj} 未做分区", "建议按时间维度分区")
        if not (d.get("shard_key") or "").strip():
            _add("NO_SHARD_KEY", "WARNING", f"{obj} 未识别分片键", "确认分布式分片键设置")
    return items, len(rows)
```

**挂载点**：`bigtable_service.save_inventory()`（:22）在 `conn.commit()`（:41）之后，以当次 `inspection_date` 调用抽取并生成快照；
`biz_ref_id` 取 `f"{connection_id}:{inspection_date}:{时:分:秒}"`，使同日多次盘点各成一份快照（解决概要设计 D3）。

---

## 6. 比对层详细设计

### 6.1 可比性校验（`scan_compare_service.validate_pair`）

按序校验，任一不过即返回明确错误（错误码见 §12）：

| 序 | 校验 | 不通过 |
|---|---|---|
| 1 | `snapshot_ids` 长度必须 == 2 | `E4001` 只能选择两次扫描结果进行对比 |
| 2 | 两个 ID 不相同 | `E4002` 不能与自身对比 |
| 3 | 两份快照均存在 | `E4004` 快照不存在或已被清理 |
| 4 | `module` 相同 | `E4003` 不同模块的扫描结果不可对比 |
| 5 | `connection_id` 相同（非空时） | `E4003` 不同实例的扫描结果不可对比 |
| 6 | `fingerprint_algo` 相同 | `E4005` 指纹算法版本不一致，无法可靠对比 |
| 7 | 慢SQL：两次时间窗口时长比 > 2 或 < 0.5 | ⚠️ **不拦截**，置 `warnings[]` 供报告提示 |
| 8 | 任一快照 `truncated=true` | ⚠️ **不拦截**，置 `warnings[]` |

**基准判定**：`base, target = sorted([s1, s2], key=lambda s: s["scan_finished_at"])`，与用户勾选顺序无关。

### 6.2 比对算法

```python
def compare(s_base: dict, s_target: dict) -> dict:
    b_issues = {i["key"]: i for i in s_base["issues"]}
    t_issues = {i["key"]: i for i in s_target["issues"]}
    b_keys, t_keys = set(b_issues), set(t_issues)

    fixed_keys  = b_keys - t_keys          # 基准有、目标无
    new_keys    = t_keys - b_keys          # 基准无、目标有
    remain_keys = b_keys & t_keys          # 两边都有

    module = s_base["module"]
    fixed  = [b_issues[k] for k in fixed_keys]
    new    = [t_issues[k] for k in new_keys]
    remain, changed = [], []
    for k in remain_keys:
        ob, ot = b_issues[k], t_issues[k]
        ch = detect_change(module, ob, ot)     # §6.3
        item = dict(ot)
        if ch:
            item["change"] = ch                # {type, field, old, new, direction}
            changed.append(item)
        remain.append(item)

    base_total, target_total = len(b_keys), len(t_keys)
    fix_rate = round(len(fixed) / base_total * 100, 1) if base_total else 0.0
    # 排序：ERROR 优先，其次按对象名
    sev_order = {"ERROR": 0, "WARNING": 1, "INFO": 2}
    for arr in (fixed, new, remain, changed):
        arr.sort(key=lambda i: (sev_order.get(i.get("severity"), 9), i.get("object_name", "")))
    return {
        "summary": {
            "base_total": base_total, "target_total": target_total,
            "fixed_count": len(fixed), "new_count": len(new),
            "remain_count": len(remain), "changed_count": len(changed),
            "fix_rate": fix_rate,
            "delta": target_total - base_total,
            "by_severity": {
                "base": s_base["stats"]["by_severity"],
                "target": s_target["stats"]["by_severity"],
            },
        },
        "fixed": fixed, "new": new, "remain": remain, "changed": changed,
    }
```

**复杂度**：`O(n + m)`，n/m 为两份快照问题项数。千级规模实测目标 < 200ms。

### 6.3 CHANGED 判定规则（`detect_change`）

| 模块 | 判定条件 | 输出 |
|---|---|---|
| 全部 | `severity` 变化 | `{type:"SEVERITY", old, new, direction: UP/DOWN}` |
| `slow_scan` | `avg_time_ms` 变化幅度 ≥ **30%**（可配 `COMPARE_SLOW_DELTA_PCT`） | `{type:"PERF", field:"avg_time_ms", old, new, direction}` |
| `bigtable` | `level` 跃迁，或 `size_gb` 增长 ≥ **30%**（可配 `COMPARE_SIZE_DELTA_PCT`） | `{type:"GROWTH", field, old, new, direction}` |
| `schema_audit` | 仅 severity（规则命中属离散事件，无连续量） | — |

同时命中多条时取**优先级最高**一条（SEVERITY > PERF/GROWTH）并在 `change.others[]` 记录其余。

### 6.4 分类文案（**按模块差异化，见概要设计 §3.3**）

| 分类 | schema_audit / bigtable | slow_scan |
|---|---|---|
| FIXED | **已修复** | **已消失（未复现）** |
| NEW | 新增问题 | 新出现慢SQL |
| REMAIN | 遗留未整改 | 仍然存在 |

`compare()` 返回中附带 `labels` 字段供前端与报告直接取用，避免前端硬编码模块判断。

---

## 7. 对比报告 HTML 设计（`scan_compare_report.py`）

### 7.1 结构

```
┌ 报告头：TDSQL 扫描结果对比报告 / 模块名 / 实例名 · 库名
│         基准：2026-07-01 02:03  →  目标：2026-07-15 02:05
├ ⚠️ 提示区（可比性告警：时间窗口不一致 / 快照被截断）
├ KPI 六宫格：之前问题数 | 现在问题数 | 已修复 | 新增 | 遗留 | 整改率
├ 变化概览：ERROR/WARNING 两次分布对比（纯 CSS 横向条形，不引入图表库）
├ 明细一：已修复（绿）      —— 对象 | 问题类型 | 级别 | 描述
├ 明细二：新增（红）        —— 同上
├ 明细三：遗留未整改（橙）  —— 同上 + 首次发现时间
├ 明细四：级别/指标变化（蓝）—— 对象 | 变化项 | 变化前 → 变化后
└ 页脚：生成时间 / 生成人 / 平台版本
```

### 7.2 实现要求

- **纯静态自包含**：内联 CSS，**不引用任何外部资源**（与平台内网部署一致，且保证离线可打开、可邮件转发）
- **打印友好**：`@media print` 去背景色、表格 `page-break-inside: avoid`
- **明细行数保护**：单类明细超过 500 行时只渲染前 500 行并注明"其余 N 条请在页面查看/导出"，避免生成超大 HTML（1.2.0.8 巡检对比补分页的教训，首版即内置）
- **XSS 防护**：所有来自 DDL/SQL/消息的文本必须 `html.escape()` 后再拼接（**当前 `sql_audit.py` 的 HTML 报告存在未转义拼接，建议本次一并修正**）
- 复用 `daily_inspect_service.generate_comparison_html_report()`（:811）的视觉风格，保持平台内报告观感统一

---

## 8. 接口层与权限

### 8.1 路由注册

`backend/main.py` 中与其他 router 同风格注册：

```python
from backend.api import scan_compare
app.include_router(scan_compare.router)
```

`scan_compare.py` 定义：`router = APIRouter(prefix="/api/v1/scan-compare", tags=["扫描结果对比"])`

### 8.2 权限接入

`backend/services/auth_service.py` 三处修改（行号为 1.2.0.9）：

```python
ALL_MENU_KEYS = [..., 'scan-compare']            # :331
MENU_LABELS   = {..., 'scan-compare': '扫描结果对比'}   # :344
_PATH_TO_MENU = {..., "/api/v1/scan-compare": "scan-compare"}  # :230
```

**二次越权校验（重要）**：仅有 `scan-compare` 权限不足以查看任意模块数据。
`scan_compare.py` 中所有接口在处理前调用：

```python
_MODULE_MENU = {"schema_audit": "schema-extractor-audit",
                "slow_scan": "slow-tasks",
                "bigtable": "bigtable"}

def _check_module_perm(request, module: str):
    """校验调用者是否具备该 module 对应模块自身的菜单权限，否则 403"""
```

默认授权：`admin`、`dba` 全量；`auditor` 只读（列表 + 比对 + 报告，无回填）。

---

## 9. 前端详细设计

### 9.1 状态（`app.js` setup 内新增）

```javascript
// —— 扫描结果对比（三模块共用）——
const cmpState = reactive({
  module: '',                 // schema_audit | slow_scan | bigtable
  filters: { connection_id:'', db_name:'', date_from:'', date_to:'' },
  list: [], total: 0, page: 1, pageSize: 10, loading: false,
  selected: [],               // 选中的快照对象，最多 2
  result: null,               // 比对结果
  comparing: false,
  visible: false              // 对比结果区是否展开
});
const cmpTableRef = ref(null); // el-table 引用，用于取消超选
```

### 9.2 关键方法

```javascript
const loadSnapshots = async (module) => {
  cmpState.module = module; cmpState.loading = true;
  try {
    const f = cmpState.filters;
    const qs = new URLSearchParams({
      module, connection_id: f.connection_id||'', db_name: f.db_name||'',
      date_from: f.date_from||'', date_to: f.date_to||'',
      limit: cmpState.pageSize, offset: (cmpState.page-1)*cmpState.pageSize
    });
    const resp = await apiFetch(`${API_BASE}/api/v1/scan-compare/snapshots?${qs}`);
    const d = await resp.json();
    cmpState.list = d.items || []; cmpState.total = d.total || 0;
  } finally { cmpState.loading = false; }
};

// 【G6】限选两个：超选提示 + 自动取消
const onSnapshotSelect = (rows) => {
  if (rows.length > 2) {
    ElementPlus.ElMessage.warning('最多只能选择两次扫描结果进行对比');
    const extra = rows[rows.length - 1];
    nextTick(() => cmpTableRef.value?.toggleRowSelection(extra, false));
    return;                    // 不写入 selected，保持前两条
  }
  cmpState.selected = rows;
};

const runCompare = async () => {
  if (cmpState.selected.length !== 2) {
    ElementPlus.ElMessage.warning('请选择两次扫描结果进行对比'); return;
  }
  cmpState.comparing = true;
  try {
    const resp = await apiFetch(`${API_BASE}/api/v1/scan-compare/compare`, {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ module: cmpState.module,
                             snapshot_ids: cmpState.selected.map(s => s.id) })
    });
    if (!resp.ok) { ElementPlus.ElMessage.error((await resp.json()).detail || '对比失败'); return; }
    cmpState.result = await resp.json();
    cmpState.visible = true;
  } finally { cmpState.comparing = false; }
};

const exportCompareHtml = () => {
  const [a, b] = cmpState.selected.map(s => s.id);
  window.open(`${API_BASE}/api/v1/scan-compare/compare/html?module=${cmpState.module}`
            + `&snapshot_ids=${a}&snapshot_ids=${b}&token=${encodeURIComponent(authState.token)}`, '_blank');
};
```

> **导出的鉴权说明**：`window.open` 无法带 Authorization 头。沿用平台既有报告下载做法——
> 服务端对该 GET 接口额外接受 `token` 查询参数（仅此类导出接口开放，且记录操作日志）。
> 若平台已有统一的下载鉴权封装，优先复用，避免新开旁路。

### 9.3 模板（三模块复用同一段结构，仅 `module` 与列定义不同）

```html
<!-- 扫描历史与对比：<MODULE> -->
<div class="page-card">
  <div class="card-head">
    <div class="page-card-title mb0">扫描历史（可选两次进行对比）</div>
    <div class="flex-gap8">
      <el-button type="primary" size="small"
                 :disabled="cmpState.selected.length!==2" :loading="cmpState.comparing"
                 @click="runCompare">开始对比</el-button>
      <el-button size="small" @click="loadSnapshots('<MODULE>')">刷新</el-button>
    </div>
  </div>

  <div class="filter-bar">
    <el-select v-model="cmpState.filters.connection_id" placeholder="数据库实例" clearable filterable size="small" class="w-220">
      <el-option v-for="c in savedConnections" :key="c.id" :value="c.id" :label="c.name"></el-option>
    </el-select>
    <el-input v-model="cmpState.filters.db_name" placeholder="库名" clearable size="small" class="w-140"></el-input>
    <el-date-picker v-model="cmpState.filters.date_from" type="date" value-format="YYYY-MM-DD" placeholder="开始日期" size="small"></el-date-picker>
    <el-date-picker v-model="cmpState.filters.date_to" type="date" value-format="YYYY-MM-DD" placeholder="结束日期" size="small"></el-date-picker>
    <el-button type="primary" size="small" @click="cmpState.page=1;loadSnapshots('<MODULE>')">查询</el-button>
  </div>

  <el-table ref="cmpTableRef" :data="cmpState.list" v-loading="cmpState.loading"
            stripe size="small" class="w-full" @selection-change="onSnapshotSelect">
    <el-table-column type="selection" width="40"></el-table-column>
    <el-table-column label="扫描时间" width="160">
      <template #default="{row}">{{ formatTime(row.scan_finished_at) }}</template>
    </el-table-column>
    <el-table-column prop="connection_name" label="实例" show-overflow-tooltip></el-table-column>
    <el-table-column prop="db_name" label="库名" width="130"></el-table-column>
    <el-table-column prop="object_total" label="对象数" width="80"></el-table-column>
    <el-table-column label="问题数" width="140">
      <template #default="{row}">
        <span class="t-danger">{{ row.error_count }}</span> /
        <span class="t-warning">{{ row.warning_count }}</span>
        <span class="t-secondary">（共 {{ row.issue_total }}）</span>
      </template>
    </el-table-column>
    <el-table-column prop="created_by" label="执行人" width="90"></el-table-column>
  </el-table>
  <el-pagination v-if="cmpState.total>cmpState.pageSize" small layout="total,prev,pager,next"
                 :total="cmpState.total" :page-size="cmpState.pageSize"
                 v-model:current-page="cmpState.page" @current-change="loadSnapshots('<MODULE>')"></el-pagination>
</div>

<!-- 对比结果 -->
<div class="page-card" v-if="cmpState.visible && cmpState.result">
  <div class="card-head">
    <div class="page-card-title mb0">
      对比结果：{{ formatTime(cmpState.result.base.scan_finished_at) }}
      → {{ formatTime(cmpState.result.target.scan_finished_at) }}
    </div>
    <el-button type="success" size="small" @click="exportCompareHtml">导出对比报告</el-button>
  </div>

  <el-alert v-for="w in (cmpState.result.warnings||[])" :key="w" :title="w"
            type="warning" show-icon :closable="false" class="mb-8"></el-alert>

  <el-row :gutter="12" class="mb-16">
    <el-col :span="4"><div class="kpi-card"><div class="kpi-num">{{ cmpState.result.summary.base_total }}</div><div class="kpi-label">之前问题数</div></div></el-col>
    <el-col :span="4"><div class="kpi-card"><div class="kpi-num">{{ cmpState.result.summary.target_total }}</div><div class="kpi-label">现在问题数</div></div></el-col>
    <el-col :span="4"><div class="kpi-card"><div class="kpi-num t-success">{{ cmpState.result.summary.fixed_count }}</div><div class="kpi-label">{{ cmpState.result.labels.fixed }}</div></div></el-col>
    <el-col :span="4"><div class="kpi-card"><div class="kpi-num t-danger">{{ cmpState.result.summary.new_count }}</div><div class="kpi-label">{{ cmpState.result.labels.new }}</div></div></el-col>
    <el-col :span="4"><div class="kpi-card"><div class="kpi-num t-warning">{{ cmpState.result.summary.remain_count }}</div><div class="kpi-label">{{ cmpState.result.labels.remain }}</div></div></el-col>
    <el-col :span="4"><div class="kpi-card"><div class="kpi-num">{{ cmpState.result.summary.fix_rate }}%</div><div class="kpi-label">整改率</div></div></el-col>
  </el-row>

  <el-tabs>
    <el-tab-pane :label="cmpState.result.labels.fixed + '(' + cmpState.result.summary.fixed_count + ')'">
      <!-- 明细表：对象 | 类型 | 级别 | 描述 -->
    </el-tab-pane>
    <el-tab-pane :label="cmpState.result.labels.new + '(' + cmpState.result.summary.new_count + ')'"></el-tab-pane>
    <el-tab-pane :label="cmpState.result.labels.remain + '(' + cmpState.result.summary.remain_count + ')'"></el-tab-pane>
    <el-tab-pane :label="'变化(' + cmpState.result.summary.changed_count + ')'"></el-tab-pane>
  </el-tabs>
</div>
```

### 9.4 挂载位置（行号为 1.2.0.9 现状）

| 模块 | 位置 |
|---|---|
| 在线元数据审核 | `currentPage==='schema-extractor-audit'`（index.html:297）的 `el-tabs` 内**新增第三个 tab「扫描对比」** |
| 慢SQL扫描任务 | `currentPage==='slow-tasks'`（index.html:412）现有任务列表卡片**下方追加** |
| 大表治理 | `currentPage==='bigtable'`（index.html:712）页面**下方追加** |

进入对应页/tab 时在 `watch(currentPage)` 中调用 `loadSnapshots(<module>)`（与既有 `loadRules`/`loadScanTasks` 同风格）。

---

## 10. 存量数据回填（`rebuild_snapshots`）

上线后若不回填，需等待两轮新扫描才能比对，领导无法立即看到效果。回填逻辑：

| 模块 | 数据来源 | biz_ref_id | 实例信息 |
|---|---|---|---|
| `schema_audit` | `audit_history WHERE audit_type='extracted_schema'` | `id` | 历史记录无实例信息 → `connection_id=''`，前端归入"未知实例" |
| `slow_scan` | `scan_tasks` + `slow_queries.scan_task_id` | `scan_tasks.id` | ✅ 表中已有 |
| `bigtable` | `bigtable_inventory` 按 `(connection_id, inspection_date)` 分组 | `conn:date` | ✅ 表中已有 |

要点：
- 默认 `overwrite=False`，已存在（命中 `uk_snap_module_biz`）则跳过
- 单次 `limit` 默认 200，避免长事务；返回 `{scanned, created, skipped, failed}` 支持分批调用
- `source_kind='rebuild'` 标记来源，便于区分与排查
- `schema_audit` 回填从 `results_json` 解析（`sql` 截断 500 字符不影响对象名解析，见 §3.3.3）

---

## 11. 既有缺陷修复施工方案（D1 / D2 / D4）

> 本章为三个既有缺陷的**照图施工**修复设计：给出现状代码、修改后代码、迁移与兼容性说明。
> 三处修复均可独立提交、独立验证，建议在快照功能（S2）之前先行落地（S3 排期见概要设计 §10）。

### 11.1 D1：元数据审核未落实例信息（P0，阻断"按实例筛选"）

**现状（V1.2.0.9）**
- `audit_history` 表：有 `connection_id` 列（`database.py:549` 迁移补列）但**从未被写入**；**无 `db_name` 列**。
- `audit_service.py:28 _save_audit_history()` 签名无实例参数：

```python
# 现状 backend/services/audit_service.py:28
def _save_audit_history(audit_type: str, source: str, results: list[AuditResult],
                        summary: AuditSummary, created_by: str = "",
                        project_id: str = "",
                        gate_result: Optional[GateResult] = None):
```

- `api/sql_audit.py:246` 调用处未传实例信息，而端点内**实例信息现成可得**：
  `connection_id = payload.get("connection_id")`（:145）、`target_db`（:160）。

**修复步骤**

**① `database.py` — 补列与索引**

在既有 `if "audit_history" in table_names:` 迁移块（:547-556）内追加一行：

```python
        _add_column_if_not_exists(conn, "audit_history", "db_name", "VARCHAR(128) DEFAULT ''")
```

新增索引辅助函数（放在 `_add_column_if_not_exists`（:559）旁，同风格）：

```python
def _add_index_if_not_exists(conn, table: str, index: str, definition: str):
    """幂等加索引：先查 information_schema.STATISTICS，存在即跳过"""
    row = conn.execute("""
        SELECT COUNT(*) AS cnt FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = ? AND INDEX_NAME = ?
    """, (table, index)).fetchone()
    if not (row and row["cnt"]):
        conn.execute(f"ALTER TABLE {table} ADD INDEX {index} {definition}")
```

并在同一迁移块调用：

```python
        _add_index_if_not_exists(conn, "audit_history", "idx_audit_conn", "(connection_id, created_at)")
```

**② `audit_service.py` — 扩展 `_save_audit_history`**（新增参数带默认值，其余 4 处既有调用不受影响）：

```python
# 修改后
def _save_audit_history(audit_type: str, source: str, results: list[AuditResult],
                        summary: AuditSummary, created_by: str = "",
                        project_id: str = "",
                        gate_result: Optional[GateResult] = None,
                        connection_id: str = "", db_name: str = ""):   # ← 新增
    ...
    cursor.execute("""
        INSERT INTO audit_history (audit_type, source, total_sql, passed, failed,
            error_count, warning_count, pass_rate, results_json,
            created_by, project_id, gate_passed, gate_detail, created_at,
            connection_id, db_name)                                    -- ← 新增两列
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, ( ...原有值..., connection_id, db_name))
```

**③ `api/sql_audit.py:246` — 调用处传值**：

```python
        report_id = _save_audit_history(
            audit_type="extracted_schema",
            source=filename,
            results=results,
            summary=summary,
            created_by=_operator(http_request),
            connection_id=connection_id,      # ← 新增（:145 已有该变量）
            db_name=target_db,                # ← 新增（:160 已有该变量）
        )
```

**④ `api/sql_audit.py:267 get_extracted_reports` — 列表查询加筛选与实例名回显**：

```python
@router.get("/extracted-reports", summary="在线元数据审核历史记录列表")
async def get_extracted_reports(limit: int = 20, offset: int = 0,
                                connection_id: str = "", db_name: str = "",
                                date_from: str = "", date_to: str = ""):
    ...
    where = ["h.audit_type = ?"]; args = ["extracted_schema"]
    if connection_id == "__unknown__":
        where.append("(h.connection_id IS NULL OR h.connection_id = '')")
    elif connection_id:
        where.append("h.connection_id = ?"); args.append(connection_id)
    if db_name:
        where.append("h.db_name = ?"); args.append(db_name)
    if date_from:
        where.append("DATE(h.created_at) >= ?"); args.append(date_from)
    if date_to:
        where.append("DATE(h.created_at) <= ?"); args.append(date_to)

    rows = conn.execute(f"""
        SELECT h.id, h.audit_type, h.source, h.total_sql, h.passed, h.failed,
               h.error_count, h.warning_count, h.pass_rate, h.created_by, h.created_at,
               h.connection_id, h.db_name, COALESCE(c.name, '') AS connection_name
        FROM audit_history h
        LEFT JOIN tdsql_connections c ON c.id = h.connection_id
        WHERE {' AND '.join(where)}
        ORDER BY h.created_at DESC LIMIT ? OFFSET ?
    """, (*args, limit, offset)).fetchall()
    # COUNT 同 WHERE 条件；注意本查询不再返回 results_json（大字段，列表页不需要）
```

> `connection_name` 通过 `LEFT JOIN tdsql_connections` 实时解析（连接被删除时回落为空串），
> `audit_history` 不冗余存名称，避免改名后陈旧。

**兼容性**：新参数均有默认值；老前端不传参行为不变。响应新增 `connection_id`/`db_name`/`connection_name` 字段，不删除既有字段（`results_json` 从列表响应移除属优化，前端历史 tab 未使用该字段——施工时复核 `loadExtractedReports` 确认）。

**验证**：跑一次在线元数据审核 → `SELECT connection_id, db_name FROM audit_history ORDER BY id DESC LIMIT 1` 应为实际值；按实例筛选列表应只返回该实例记录。

### 11.2 D2：大表清单跨日期混合返回（P0）

**现状（V1.2.0.9）**：`bigtable_service.py:46`

```python
# 现状：无日期过滤 → 多次盘点后同一张表出现多行
def get_inventory(self, connection_id: str, level: str = "") -> list[dict]:
    ...
    "SELECT * FROM bigtable_inventory WHERE connection_id = ? [AND level = ?] ORDER BY size_gb DESC"
```

**修复后**：

```python
def get_inventory(self, connection_id: str, level: str = "",
                  inspection_date: str = "") -> list[dict]:
    """获取大表清单；inspection_date 为空时自动取该实例最近一次盘点日期"""
    ensure_db()
    conn = _get_connection()
    try:
        if not inspection_date:
            row = conn.execute(
                "SELECT MAX(inspection_date) AS d FROM bigtable_inventory WHERE connection_id = ?",
                (connection_id,)).fetchone()
            inspection_date = (row and row["d"]) or ""
            if not inspection_date:
                return []                      # 从未盘点
        sql = "SELECT * FROM bigtable_inventory WHERE connection_id = ? AND inspection_date = ?"
        args = [connection_id, inspection_date]
        if level:
            sql += " AND level = ?"; args.append(level)
        sql += " ORDER BY size_gb DESC"
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()
```

**API 层**（`api/bigtable.py:14`）：

```python
@router.get("/inventory/{connection_id}", response_model=ApiResponse)
def get_inventory(connection_id: str, level: str = "", inspection_date: str = ""):
    items = _service.get_inventory(connection_id, level, inspection_date)
    return ApiResponse(data=items)
```

**连带核查**：`get_governance_report()`（:70）内部调用 `get_inventory()`，修复后自动继承"最近一次盘点"语义——治理报告口径同步修正，属预期收益。

**⚠️ 行为变更声明**：不传 `inspection_date` 时由"返回所有历史日期"变为"返回最近一次盘点"。这是**修正错误行为**——升级后大表清单数量可能较此前"减少"，属正确表现，需写入升级说明与用户培训材料。

**验证**：对同一实例执行两次盘点（不同日期）→ 清单只显示最近一次；传 `inspection_date=首次日期` 可查看历史批次。

### 11.3 D4：扫描任务列表无筛选、不回显实例名（P1）

**现状（V1.2.0.9）**：`slow_query_service.py:431`

```python
# 现状：无任何筛选
def get_scan_tasks(self, limit: int = 50, offset: int = 0) -> dict:
    total = conn.execute("SELECT COUNT(*) as cnt FROM scan_tasks").fetchone()["cnt"]
    rows = conn.execute(
        """SELECT * FROM scan_tasks ORDER BY created_at DESC LIMIT ? OFFSET ?""",
        (limit, offset)).fetchall()
```

**修复后**（动态 WHERE，**值全部参数化**，禁止字符串拼接值）：

```python
def get_scan_tasks(self, limit: int = 50, offset: int = 0,
                   connection_id: str = "", db_name: str = "",
                   date_from: str = "", date_to: str = "") -> dict:
    """获取扫描任务列表，支持按实例/库/时间筛选"""
    conn = _get_connection()
    try:
        where, args = [], []
        if connection_id:
            where.append("connection_id = ?"); args.append(connection_id)
        if db_name:
            where.append("db_name = ?"); args.append(db_name)
        if date_from:
            where.append("DATE(created_at) >= ?"); args.append(date_from)
        if date_to:
            where.append("DATE(created_at) <= ?"); args.append(date_to)
        cond = (" WHERE " + " AND ".join(where)) if where else ""
        total = conn.execute(f"SELECT COUNT(*) AS cnt FROM scan_tasks{cond}",
                             args).fetchone()["cnt"]
        rows = conn.execute(
            f"SELECT * FROM scan_tasks{cond} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (*args, limit, offset)).fetchall()
        return {"total": total, "tasks": [dict(r) for r in rows]}
    finally:
        conn.close()
```

**API 层**（`api/slow_query.py:127`）：

```python
@router.get("/scan-tasks", summary="获取扫描任务列表")
def list_scan_tasks(limit: int = 50, offset: int = 0,
                    connection_id: str = "", db_name: str = "",
                    date_from: str = "", date_to: str = ""):
    return service.get_scan_tasks(limit=limit, offset=offset,
                                  connection_id=connection_id, db_name=db_name,
                                  date_from=date_from, date_to=date_to)
```

**实例名回显**：`scan_tasks` 表**已有 `connection_name` 列**（建表即含，`database.py:1006`），
`SELECT *` 已随行返回，此前只是前端未展示——前端任务表格新增"实例"列即可
（`<el-table-column prop="connection_name" label="实例" show-overflow-tooltip>`）。

**兼容性**：新参数均有默认值，返回结构不变（`tasks` 内多出的字段本就存在）。

**验证**：多实例各建一个扫描任务 → 按实例筛选各自只见自己的任务；表格"实例"列正确显示。

---

## 12. 错误码

| 错误码 | HTTP | 文案 |
|---|---|---|
| `E4001` | 400 | 只能选择两次扫描结果进行对比 |
| `E4002` | 400 | 不能与自身对比，请选择两次不同的扫描结果 |
| `E4003` | 400 | 不同实例/模块的扫描结果不可对比 |
| `E4004` | 404 | 快照不存在或已被数据保留策略清理 |
| `E4005` | 409 | 两次扫描的指纹算法版本不一致，无法可靠对比 |
| `E4006` | 400 | 不支持的模块类型 |
| `E4031` | 403 | 无该模块数据的访问权限 |

---

## 13. 测试用例清单（`tests/test_scan_compare.py`）

| 编号 | 用例 | 预期 |
|---|---|---|
| T01 | 指纹稳定性：同一份 results 生成两次快照 | 两份 `issues[].key` 集合完全一致 |
| T02 | **行号漂移不影响指纹**：在 DDL 前插入若干新表使行号整体后移 | 原有问题项 key 不变，比对结果 0 修复 0 新增（**核心用例**） |
| T03 | 对象重命名 | 旧对象计 FIXED，新对象计 NEW（符合预期语义） |
| T04 | 修复一个问题 | `fixed_count=1`，`fix_rate` 正确 |
| T05 | 新增一个问题 | `new_count=1` |
| T06 | severity 由 WARNING 升 ERROR | 计入 `changed`，`direction=UP` |
| T07 | 慢SQL avg_time_ms +50% | `changed` 含 PERF 变化 |
| T08 | `snapshot_ids` 传 1 个 / 3 个 | 返回 400 `E4001` |
| T09 | 两个相同 ID | 400 `E4002` |
| T10 | 跨实例、跨模块比对 | 400 `E4003` |
| T11 | 比对方向：勾选顺序颠倒 | 结果一致（早的恒为 base） |
| T12 | 时间窗口差异 > 2 倍 | `warnings` 含提示，但不拦截 |
| T13 | 快照生成抛异常 | 扫描主流程正常返回，仅日志告警 |
| T14 | 幂等：同一 `biz_ref_id` 生成两次 | `scan_snapshots` 仅一条记录 |
| T15 | 超过 `SNAPSHOT_MAX_ISSUES` | `truncated=true`，保留 ERROR 优先 |
| T16 | 回填后立即比对 | 正常产出结果 |
| T17 | 仅有 `scan-compare` 权限、无模块权限 | 403 `E4031` |
| T18 | HTML 报告含恶意字符的对象名 | 正确转义，无 XSS |
| T19 | **D1 修复**：extract-and-audit 后 `audit_history` 最新行带 `connection_id`/`db_name` | 值正确 |
| T20 | **D2 修复**：两次不同日期盘点后 `get_inventory` 默认只返回最近一次 | 无跨日期重复表 |
| T21 | **D4 修复**：按实例筛选扫描任务 | 只返回该实例任务且带 `connection_name` |

---

## 14. 部署与回滚

**部署顺序**
1. 备份元数据库
2. 部署代码（迁移由 `ensure_db()` 启动时自动执行：新表走 `schema/v2` 迁移文件，`audit_history` 补列走 `_add_column_if_not_exists`，均幂等）
3. 验证 `scan_snapshots`、`scan_compare_reports` 已创建，`audit_history.db_name` 列与 `idx_audit_conn` 索引已存在
4. 调用 `POST /snapshots/rebuild` 分批回填三模块存量数据
5. 前端强刷（静态文件由后端从磁盘实时读取，无需重启）
6. 冒烟：各模块跑一次扫描 → 确认生成快照 → 选两次执行对比 → 导出报告；D1/D2/D4 按 §11 各自验证项复验

**回滚**
- 代码回滚至 V1.2.0.9 即可；新表与新列**保留不删**（不影响旧版本运行）
- 快照生成为旁路，回滚后三模块功能与现状完全一致
- D2 行为变更如需回退，单独还原 `get_inventory()` 即可

---

## 15. 工作量分解

| 模块 | 内容 | 人日 |
|---|---|---|
| 数据层 | DDL + 迁移 + 保留策略 + 补列/索引辅助函数 | 0.5 |
| 快照层 | service + base + 三抽取器 | 2.0 |
| 缺陷修复 | D1/D2/D4（§11）+ 三处挂载 | 1.5 |
| 比对层 | 校验 + 引擎 + CHANGED + 汇总 | 1.5 |
| 报告层 | HTML 渲染 + 打印样式 + 转义 | 1.0 |
| 接口层 | 6 个接口 + 权限二次校验 | 1.0 |
| 前端 | 三模块筛选/限选/对比视图/导出 | 2.5 |
| 回填与联调 | rebuild + 端到端自测 | 1.5 |
| 文档 | 升级说明/使用手册 | 0.5 |
| **合计** | | **12.0** |
