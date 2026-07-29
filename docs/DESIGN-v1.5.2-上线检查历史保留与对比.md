# TDSQL-SQLCheck v1.5.2 设计说明书
## 「上线检查」结果历史保留与历史结果对比

| 项 | 内容 |
|---|---|
| 版本 | v1.5.2.0 |
| 基线 | v1.5.1.2（commit `f006d36`） |
| 文档类型 | 设计说明书（含概要 / 数据库 / 接口 / 详细四部分） |
| 编制 | 智能体 A（质量/架构） |
| 施工规约 | **`GUIDE-团队施工规约.md`（唯一出处，施工前通读）** —— 本次尤其相关：R-02~R-05（迁移）、R-15（失效方向）、R-16（未命中≠反向成立） |
| 前置文档 | `ARCHITECTURE-v1.3-扫描结果对比.md` · `DETAIL-v1.3-扫描结果对比.md` |

> **本文档为什么不拆四份**：v1.3 建立扫描对比框架时是新建能力，四份文档各自成体量。本次是**在既有框架上接入第 4 个模块**——新增 1 个抽取器、4 张表的配置项、1 个页面改 3 页签。拆开会让读者在文档间反复跳转去拼一条本来很短的链路。内部仍按概要（§1–§4）/ 数据库（§5）/ 接口（§6）/ 详细（§7）分节，详细部分保持照图施工粒度。

---

## 1. 问题描述

v1.3 建立「扫描结果纵向对比」框架时，接入了 3 个模块：

```python
# backend/services/scan_snapshot_service.py:20
MODULES = ("schema_audit", "slow_scan", "bigtable")
```

**「上线检查」（`schema-check`）被遗漏。** 它是唯一一个产出结构化问题清单、却没有历史保留与历史对比能力的扫描模块。

### 1.1 遗漏造成的三个缺口

| # | 缺口 | 现状 |
|---|---|---|
| **缺口 1** | **无法看历史** | 检查结果只存在于当前页面状态。刷新页面即消失，昨天查过什么无从追溯 |
| **缺口 2** | **无法做对比** | 无从回答"上周整改的 27 个无主键表，这周真的改完了吗" |
| **缺口 3** | **明细无限增长** | `inspection_tasks` / `inspection_results` **不在 `CLEANABLE_TABLES` 中**，永不清理 |

> **缺口 3 是一个当下就在发生的问题**，与对比功能无关。每执行一次上线检查，最多写入 `12 × 100 = 1200` 行明细，且永不删除。

### 1.2 现状勘察（代码事实）

| 层 | 事实 | 证据 |
|---|---|---|
| 前端 | `schema-check` 单页视图，无页签，结果仅存于 `schemaCheckResults` 组件状态 | `index.html:1019-1069` |
| 后端 | `run_schema_check` 建 `inspection_tasks` 任务、写 `inspection_results` 明细，**但不创建快照** | `api/inspection.py:265-315` |
| 明细截断 | 每项检查最多存 100 行（`check_result["rows"][:100]`） | `api/inspection.py:290` |
| 明细形态 | 行被压平成字符串 `"k: v \| k: v"` 存入 `message` | `api/inspection.py:293-295` |
| 快照框架 | 仅注册 3 个模块 | `scan_snapshot_service.py:20` |
| 保留策略 | `CLEANABLE_TABLES` 无 inspection 相关表 | `retention_service.py:20-30` |
| 级联 | `inspection_results.task_id` → `inspection_tasks(id) ON DELETE CASCADE` | `database.py:979` |

### 1.3 可复用的既有资产

本次**不新建任何框架**，全部复用 v1.3 已有能力：

| 资产 | 位置 | 复用方式 |
|---|---|---|
| 快照落库 | `create_snapshot` / `safe_create_snapshot` | 直接调用，只需新增模块名 |
| 问题项结构与指纹 | `snapshot_extractors/base.py`（`IssueItem` / `fp`） | 直接复用 |
| 对比引擎 | `scan_compare_service.compare` | 需新增 `detect_change` 的模块分支 |
| 对比 API | `/api/v1/scan-compare/*` 全套 | **零改动**（模块名参数化） |
| 留档报告 | `scan_compare_reports` | **零改动** |
| 前端对比 UI | `cmpState` + 两个页签模板 | 复制页签结构，切换 module 值 |
| 保留策略 | `retention_policies` + `RetentionService` | 新增表配置项 |

---

## 2. 设计目标

| 编号 | 目标 |
|---|---|
| **G1** | 每次上线检查自动落快照，可在「扫描历史对比」页签查看历史清单 |
| **G2** | 任选两次检查结果做对比，产出 新增 / 已解决 / 仍存在 / 已变化 四类结论 |
| **G3** | 对比结果可留档、可导出 HTML，与既有三模块体验一致 |
| **G4** | `inspection_tasks` / `inspection_results` 纳入保留策略，堵住无限增长 |
| **G5** | **对比口径必须可比**：检查范围（数据库过滤）不同的两次结果**拒绝对比** |
| **G6** | 快照创建为旁路，失败**绝不影响**上线检查主流程 |

### 明确的非目标

- ❌ 不改动 12 项检查的规则内容与判定逻辑
- ❌ 不改动 v1.3 对比引擎的通用部分
- ❌ **不支持存量历史回填**（理由见 §4.4，这是一条经过论证的拒绝，不是偷懒）

---

## 3. 关键设计决策

### D1 模块标识用 `launch_check`，不用 `schema_check`

**问题**：既有模块已有 `schema_audit`（在线元数据审核）。若本模块取名 `schema_check`，两者仅一词之差，且会**在同一个下拉框、同一张快照列表里相邻出现**。

**后果**：字符串字面量散布在数据库行、API 参数、前端判断、测试断言中，混淆代价高且排查困难。

**决策**：模块标识取 **`launch_check`**（上线检查 = go-live check），与 `schema_audit` 在视觉与语义上都清晰可分。

**存量语义不动**：`inspection_tasks.inspection_type` 保持 `'schema_check'`，前端路由保持 `schema-check`。三者的对应关系写入代码注释：

```
前端路由 schema-check  ←→  inspection_type='schema_check'  ←→  快照 module='launch_check'
```

> 引入第三个名字确有认知成本，但它们分处三个不同命名空间且有文档映射；而 `schema_audit` / `schema_check` 是**同一命名空间内**的近似名，混淆风险高一个量级。

### D2 问题项粒度 = 单行明细，度量值一律进 `attrs`

**这是本次设计的正确性核心**（§7.2 给出全部 12 项的逐项定义）。

**原则**：

1. **指纹只含"这个问题是关于谁的"**——检查项 + 数据库 + 表 + 列；
2. **度量值一律进 `attrs`**——索引数、字段数、字符数、表数量等**会变化的数字，绝不能进指纹**。

**为什么**：若把索引数写进指纹，一张表的索引从 5 个变成 8 个，会被判成"旧问题已解决 + 新问题出现"——**制造出一次虚假整改**。放进 `attrs` 才能正确判为 `CHANGED`。

> 这条与 `base.py` 开头的红线同源：
> ```
> 【红线】指纹严禁包含 line_number、报告序号 #idx、自增 id、扫描时间。
> ```
> 本次把它扩展为：**指纹严禁包含任何会随时间变化的度量值。**

### D3 C01 是唯一的聚合行，必须特殊处理

11 项检查返回**对象级**明细（有表名），**唯独 C01「字符编码非utf8mb4的表」返回聚合行**：

```sql
SELECT table_schema AS `数据库`, table_collation AS `排序规则`, COUNT(*) AS `表数量`
```

**没有表名**，一行代表"某库某排序规则下有 N 张表"。

若套用通用规则（表名取空串），同库同排序规则的行仍能稳定指纹——**但前提是必须把 `表数量` 排除在指纹之外**，否则表数量从 12 变 15 就会被判成"解决了一个 + 新增了一个"。

**决策**：C01 指纹 = `fp("launch_check", "C01", 数据库, 排序规则)`，`表数量` 进 `attrs`。

> **漏掉这一项不会报错，只会让 C01 的对比结果长期悄悄错着。** 单列为一条决策以确保施工时不被当成"通用情况"一笔带过。

### D4 检查范围不同的两次结果，拒绝对比

上线检查支持 `database_filter`（可指定单库，也可留空查全部）。

| 基线范围 | 对比范围 | 若允许对比会发生什么 |
|---|---|---|
| 全部库 | `dbA` | 除 `dbA` 外所有库的问题项**全部显示为"已解决"** → **一次凭空的大规模整改** |

**决策**：`launch_check` 两侧 `db_name` 必须**完全相同**，否则拒绝（新增错误码 `E4008`）。

> 这是 R-15 的直接应用：**"看起来整改了一大批"是不可见的错误方向**——它不报错、不异常，只会让人对着一份漂亮的假报告做决策。

> **顺带记录一个既有缺口（不在本次范围）**：`schema_audit` 模块目前**也没有** `db_name` 一致性校验，同实例不同库的两次快照可以互相对比。其风险低于本模块（schema_audit 的 `db_name` 必填且单值，跨库对比会表现为"全新增+全解决"，异常明显），但仍建议后续统一补上。已记入 §9 遗留清单。

### D5 保留策略只清 `inspection_tasks`，不单独清 `inspection_results`

`inspection_results` 有 `ON DELETE CASCADE` 外键指向 `inspection_tasks`（`database.py:979`）。

因此：

- ✅ 把 `inspection_tasks` 纳入 `CLEANABLE_TABLES`，明细随级联自动清理；
- ❌ **不要**把 `inspection_results` 也加进去。若按它自己的 `created_at` 单独清理，会留下**任务还在、明细被删了一半**的残缺记录——比不清理更糟。

### D6 快照保留 365 天 > 明细保留 180 天，这是有意的

| 数据 | 保留期 | 说明 |
|---|---|---|
| `inspection_tasks` / `inspection_results`（原始明细） | **180 天** | 与 `scan_tasks` 一致 |
| `scan_snapshots`（快照） | **365 天**（既有） | 快照是**自包含**的，不依赖原始明细 |

**结果**：180 天后原始明细清了，但快照仍在，**历史对比照常可用**。这是快照设计的固有优势，不是巧合——快照把问题项完整序列化进 `snapshot_json`，与源表解耦。

---

## 4. 总体设计

### 4.1 数据流

```
用户点击「执行上线检查」
   ↓
POST /api/v1/inspection/schema-check
   ↓
SchemaInspector.inspect()  →  results（12 项，内存中【完整】）
   ↓
   ├─→ 主流程：写 inspection_tasks / inspection_results（每项截断 100 行）── 既有，不动
   │
   └─→ 旁路（新增）：launch_check.extract(results) → IssueItem[]
                          ↓
                  safe_create_snapshot("launch_check", meta, issues, object_total)
                          ↓
                  scan_snapshots 表（失败仅告警，不影响主流程）
   ↓
返回 results + summary + snapshot_id（新增字段）
```

> **快照用的是内存中【完整】的 `results`，不是写库时截断到 100 行的副本。** 混淆二者会让快照丢失大量问题项，且丢失方式不可见——下一次对比时那些项会显示为"已解决"。

### 4.2 对比链路（零改动复用）

```
「扫描历史对比」页签
   → GET  /api/v1/scan-compare/snapshots?module=launch_check&connection_id=...
   → 勾选两条
   → POST /api/v1/scan-compare/compare   {module: "launch_check", base_id, target_id}
                ↓
        validate_pair()  ← 新增 db_name 一致性校验（§7.5）
                ↓
        detect_change()  ← 新增 launch_check 分支（§7.4）
                ↓
        新增 / 已解决 / 仍存在 / 已变化
   → 可留档 POST /reports、可导出 GET /compare/html
```

**除 `validate_pair` 与 `detect_change` 两处外，对比引擎与全部 API 零改动。**

### 4.3 UI 结构

`schema-check` 页由单视图改为 **3 页签**，与「大表治理」完全一致：

| 页签 | 内容 |
|---|---|
| **上线检查** | 现有视图原样迁入 |
| **扫描历史对比** | 快照列表（勾两条对比）+ 对比结果 —— 复制 `index.html:1153+` 结构，module 换成 `launch_check` |
| **已留档对比报告** | 留档列表 —— 复制既有结构 |

### 4.4 为什么不支持存量回填

`rebuild_snapshots` 对 `schema_audit` / `slow_scan` / `bigtable` 支持从业务表回填历史快照。**`launch_check` 明确不支持**，理由是**数据保真度不足以支撑可比性**：

| # | 障碍 | 后果 |
|---|---|---|
| 1 | `inspection_results` 每项检查**只存了前 100 行** | 回填快照缺失第 100 行之后的全部问题项 |
| 2 | 行被压平成 `message` 字符串（`"k: v \| k: v"`） | 列名/类型等属性无法可靠还原，`attrs` 残缺 |

**若强行回填**：一个（截断的）回填快照 vs 一个（完整的）实时快照做对比，**第 100 行之后的问题项会全部显示为"新增"，反向对比则全部显示为"已解决"**。

> **这是在制造虚假数据，而且错误方向不可见。** 按 R-15，禁止。
>
> `rebuild_snapshots("launch_check")` 必须**显式拒绝并说明原因**，而不是静默返回空结果——静默返回会让人以为"回填了但没有历史数据"。

---

## 5. 数据库设计

### 5.1 变更总览

**无表结构变更，无新增列。** 仅新增两条保留策略配置行。

| 对象 | 变更 |
|---|---|
| `scan_snapshots` | ⚪ 无变更（`module` 是 VARCHAR，新增取值无需 DDL） |
| `scan_compare_reports` | ⚪ 无变更 |
| `inspection_tasks` / `inspection_results` | ⚪ 无结构变更 |
| `retention_policies` | ➕ 新增 1 行配置 |
| 迁移文件 | `backend/schema/v6/060_launch_check_retention.sql`（新建） |

> 本次是纯配置与代码层改动，**数据库结构零变更**——这正是 v1.3 把 `module` 设计成字符串而非枚举的收益。

### 5.2 迁移脚本

```sql
-- ============================================================================
-- V1.5.2 上线检查结果历史保留与对比
-- 无表结构变更；仅补入保留策略配置
-- 设计依据：docs/DESIGN-v1.5.2-上线检查历史保留与对比.md §5
-- ============================================================================

-- ── F-1 上线检查明细纳入保留策略 ──
-- 此前 inspection_tasks / inspection_results 不在 CLEANABLE_TABLES 中，永不清理。
-- 每执行一次上线检查最多写入 12×100=1200 行明细，属当下正在发生的增长问题。
--
-- 只登记 inspection_tasks：inspection_results 有 ON DELETE CASCADE 外键
-- （database.py:979），随任务级联清理。若把 results 也单独登记、按其自身
-- created_at 清理，会留下"任务还在、明细被删一半"的残缺记录，比不清理更糟。
--
-- 180 天与 scan_tasks 对齐。快照（scan_snapshots）保留 365 天且自包含，
-- 故明细清理后历史对比照常可用。
INSERT IGNORE INTO retention_policies(table_name, retention_days, enabled)
VALUES ('inspection_tasks', 180, 1);

-- ── 存量数据：不做任何处理 ──
-- 存量 inspection_tasks 将在首次清理时按 180 天规则自然淘汰，符合预期。
-- 不补建历史快照（理由见设计文档 §4.4：明细已截断至 100 行，回填出的
-- 快照与实时快照不可比，会在对比中制造虚假的"已解决"）。
```

### 5.3 代码侧同步

`retention_service.py` 的 `CLEANABLE_TABLES` 新增：

```python
    # V1.5.2 上线检查（inspection_results 经外键级联清理，不单独登记）
    "inspection_tasks": "created_at",
```

`database.py` 的 `retention_defaults` 新增 `("inspection_tasks", 180)`（双保险，R-04）。

---

## 6. 接口设计

### 6.1 变更总览

| 接口 | 变更 |
|---|---|
| `POST /api/v1/inspection/schema-check` | 📤 响应新增 `snapshot_id` |
| `GET /api/v1/scan-compare/snapshots` | ⚪ 无变更（`module=launch_check` 直接可用） |
| `POST /api/v1/scan-compare/compare` | 🔀 新增 `E4008` 检查范围不一致 |
| `GET /api/v1/scan-compare/compare/html` | ⚪ 无变更 |
| `POST/GET/DELETE /api/v1/scan-compare/reports` | ⚪ 无变更 |
| `POST /api/v1/scan-compare/snapshots/rebuild` | 🔀 `module=launch_check` 显式拒绝 |
| 保留策略接口 | ⚪ 无变更（新表项自动出现在列表中） |

**除标注外全部零改动**——这是复用既有框架的直接收益。

### 6.2 上线检查（响应扩展）

```
POST /api/v1/inspection/schema-check
```

**请求不变。响应新增：**

```json
{
  "task_id": 128,
  "summary": { "total": 86, "error": 12, "warning": 60, "info": 14, "...": "..." },
  "results": ["..."],

  "snapshot_id": 4021,
  "snapshot_error": ""
}
```

| 字段 | 说明 |
|---|---|
| `snapshot_id` | 本次快照 ID；**创建失败时为 `null`** |
| `snapshot_error` | 失败原因（成功时为空串）。**仅用于前端提示，不抛异常**（G6） |

> 快照创建失败时**主流程照常返回检查结果**。前端在页面上给一条轻提示："本次结果未能存入历史（原因），检查结果不受影响。"

### 6.3 对比（新增可比性校验）

```
POST /api/v1/scan-compare/compare
```

**新增校验**：`module == "launch_check"` 时，两侧 `db_name` 必须完全相同。

**响应 409**

```json
{
  "code": "E4008",
  "detail": "两次上线检查的范围不同（基线=全部数据库，对比=dbA），问题数变化不可比，已拒绝对比。请选择检查范围相同的两次结果。"
}
```

**错误文案要求**：必须把两侧的实际范围**都写出来**。只说"范围不同"，使用者无从判断该选哪两条。

### 6.4 回填（显式拒绝）

```
POST /api/v1/scan-compare/snapshots/rebuild   {"module": "launch_check"}
```

**响应 400**

```json
{
  "code": "E4009",
  "detail": "上线检查不支持存量回填：历史明细每项仅保留前 100 行且已压平为文本，回填出的快照与实时快照不可比，会在对比中把未回填的问题项误显示为「已解决」。请以本次上线之后的检查结果为对比基线。"
}
```

> **必须显式拒绝，不能静默返回空结果。** 静默返回会让使用者以为"回填过了，只是没有历史数据"，从而误信后续对比结论。

---

## 7. 详细设计（照图施工）

### 7.0 改造清单

| # | 文件 | 动作 |
|---|---|---|
| 1 | `backend/services/snapshot_extractors/launch_check.py` | **新建** 抽取器 |
| 2 | `backend/services/scan_snapshot_service.py` | `MODULES` 加 `launch_check`；`rebuild_snapshots` 显式拒绝 |
| 3 | `backend/services/scan_compare_service.py` | `detect_change` 加分支；`validate_pair` 加范围校验 |
| 4 | `backend/api/inspection.py` | `run_schema_check` 旁路建快照，响应加字段 |
| 5 | `backend/schema/v6/060_launch_check_retention.sql` | **新建** 迁移 |
| 6 | `backend/services/retention_service.py` | `CLEANABLE_TABLES` 加 `inspection_tasks` |
| 7 | `backend/services/database.py` | `retention_defaults` 加项（双保险） |
| 8 | `frontend/index.html` · `static/js/app.js` | `schema-check` 页改 3 页签 |
| 9 | `tests/test_launch_check_snapshot.py` | **新建** 测试 |
| 10 | `backend/config.py` · `VERSION` · `index.html` | 版本号 → `1.5.2.0` |

---

### 7.1 抽取器 — `snapshot_extractors/launch_check.py`（新建）

```python
"""上线检查（launch_check）问题项抽取器

设计依据：docs/DESIGN-v1.5.2-上线检查历史保留与对比.md §7.2

指纹：fp("launch_check", check_id, 数据库, 表名, 列名)
  C01 为唯一的聚合行（无表名），指纹改用 排序规则 作为区分位。

【红线】度量值（表数量/索引数/字段数/字符数）一律进 attrs，严禁进指纹。
       否则索引数 5→8 会被判成"旧问题已解决 + 新问题出现"，制造虚假整改。

命名说明（三个命名空间的对应关系，勿混淆）：
  前端路由 schema-check  ←→  inspection_type='schema_check'  ←→  快照 module='launch_check'
  取 launch_check 而非 schema_check，是为了与既有模块 schema_audit
  （在线元数据审核）在同一下拉框中清晰可分。
"""
import logging

from .base import IssueItem, fp

logger = logging.getLogger(__name__)

# 明细行中的固定列名（SchemaInspector 的 SQL 用中文别名，见 schema_inspector.py）
_K_DB, _K_TABLE, _K_COL = "数据库", "表名", "列名"

# 各检查项的【区分位】与【度量位】定义，逐项依据见设计文档 §7.2
#   extra_key : 参与指纹的附加列（除 库/表/列 外）
#   metrics   : 进 attrs 的度量列（【严禁】进指纹）
#   attrs     : 进 attrs 的属性列（用于 CHANGED 判定，不进指纹）
_CHECK_SPEC = {
    "C01": {"extra_key": ["排序规则"], "metrics": ["表数量"], "attrs": []},
    "C02": {"extra_key": [], "metrics": [], "attrs": ["类型", "排序规则"]},
    "C03": {"extra_key": [], "metrics": [], "attrs": ["排序规则"]},
    "C04": {"extra_key": [], "metrics": [], "attrs": ["类型", "排序规则"]},
    "C05": {"extra_key": [], "metrics": ["字符数"], "attrs": []},
    "C06": {"extra_key": [], "metrics": ["索引数"], "attrs": []},
    "C07": {"extra_key": [], "metrics": [], "attrs": []},
    "C08": {"extra_key": [], "metrics": [], "attrs": ["类型"]},
    "C09": {"extra_key": [], "metrics": [], "attrs": ["当前注释"]},
    "C10": {"extra_key": [], "metrics": [], "attrs": ["当前注释"]},
    "C11": {"extra_key": [], "metrics": ["字段数"], "attrs": []},
    "C12": {"extra_key": [], "metrics": [], "attrs": ["类型"]},
}

# 未登记的检查项（将来新增 C13+ 时）的保守兜底：
#   全部非 库/表/列 的列都当作 attrs，不进指纹、不当度量。
# 这样新增检查项即使漏改本文件也只会退化成"属性变化不敏感"，
# 而不会因把度量写进指纹而制造虚假整改。
_DEFAULT_SPEC = {"extra_key": [], "metrics": [], "attrs": None}   # None = 全部剩余列


def _num(v):
    """度量值转数字；转不了就原样返回（用于 CHANGED 的数值比较）"""
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return v


def extract(results: list, database_filter: str = "") -> tuple[list, int]:
    """从 SchemaInspector.inspect() 的【完整】结果抽取问题项。

    Args:
        results: inspect() 的返回值（内存中完整数据，【不是】写库时截断到
                 100 行的副本 —— 用截断副本会让快照静默丢失问题项，
                 下次对比时它们会显示为"已解决"）
        database_filter: 本次检查范围，空串表示全部数据库

    Returns:
        (IssueItem 列表, 被检查对象总数)
    """
    items = []
    object_keys = set()

    for check in results or []:
        cid = str(check.get("id") or "")
        if check.get("error"):
            continue                     # 执行失败的检查项不产出问题项
        spec = _CHECK_SPEC.get(cid, _DEFAULT_SPEC)
        sev = (check.get("severity") or "WARNING").upper()
        cname = check.get("name") or cid
        sug = check.get("suggestion") or ""

        for row in (check.get("rows") or []):
            if not isinstance(row, dict):
                continue
            db = str(row.get(_K_DB, "") or "")
            table = str(row.get(_K_TABLE, "") or "")
            col = str(row.get(_K_COL, "") or "")

            # ── 指纹区分位 ──
            extra = [str(row.get(k, "") or "") for k in spec["extra_key"]]
            key = fp("launch_check", cid, db, table, col, *extra)

            # ── attrs：度量 + 属性 ──
            attrs = {}
            for k in spec["metrics"]:
                if k in row:
                    attrs[k] = _num(row[k])
            attr_keys = spec["attrs"]
            if attr_keys is None:        # 兜底：除 库/表/列 外全部当属性
                attr_keys = [k for k in row
                             if k not in (_K_DB, _K_TABLE, _K_COL)]
            for k in attr_keys:
                if k in row:
                    attrs[k] = row[k]

            # ── 展示文案 ──
            obj = ".".join(p for p in (db, table) if p) or db
            obj_full = f"{obj}.{col}" if col else obj
            detail = " | ".join(f"{k}: {v}" for k, v in row.items())

            items.append(IssueItem(
                key=key,
                object_name=obj_full,
                object_type="COLUMN" if col else ("TABLE" if table else "SCHEMA"),
                issue_type=cid,
                severity=sev,
                title=f"[{cid}] {cname}：{obj_full}",
                detail=detail,
                suggestion=sug,
                attrs=attrs,
            ))
            if obj:
                object_keys.add(obj)

    return items, len(object_keys)
```

> **`object_total` 取的是"涉及的对象数"（去重后的 库.表），不是问题项数。** 与既有模块口径一致（`bigtable` 用大表张数、`schema_audit` 用对象数）。

---

### 7.2 12 项检查的逐项定义（正确性基准）

**这张表是本次改造的正确性基准**，§8.2 的一致性测试会锁定它。

| 检查项 | 名称 | 指纹区分位 | `attrs` 度量 | `attrs` 属性 |
|---|---|---|---|---|
| **C01** | 字符编码非utf8mb4的表 | 数据库 + **排序规则** ⚠️ | **表数量** | — |
| C02 | 字符编码非utf8mb4的列 | 数据库 + 表名 + 列名 | — | 类型, 排序规则 |
| C03 | 大小写敏感未设置的表 | 数据库 + 表名 | — | 排序规则 |
| C04 | 大小写敏感未设置的列 | 数据库 + 表名 + 列名 | — | 类型, 排序规则 |
| C05 | 表名超过32个字符 | 数据库 + 表名 | 字符数 | — |
| **C06** | 索引数量>=5的表 | 数据库 + 表名 | **索引数** ⚠️ | — |
| C07 | 无主键的表 | 数据库 + 表名 | — | — |
| C08 | varchar字段长度>500 | 数据库 + 表名 + 列名 | — | 类型 |
| C09 | 无注释的列 | 数据库 + 表名 + 列名 | — | 当前注释 |
| C10 | 无注释的表 | 数据库 + 表名 | — | 当前注释 |
| **C11** | 字段数>50的表 | 数据库 + 表名 | **字段数** ⚠️ | — |
| C12 | timestamp类型字段 | 数据库 + 表名 + 列名 | — | 类型 |

**⚠️ 标记的三项是最易出错处**：

- **C01 无表名**（聚合行），必须用 `排序规则` 作区分位，且 `表数量` **绝不能**进指纹；
- **C06 / C11 的度量值天然会变**（索引数、字段数），进了指纹就会把"变化"误判成"解决+新增"。

---

### 7.3 快照创建 — `api/inspection.py::run_schema_check`

在 `_service.update_task_status(task_id, "completed")` **之后**、`return` **之前**插入：

```python
        # ── V1.5.2：旁路创建对比快照（失败仅告警，绝不影响检查主流程）──
        snapshot_id, snapshot_error = None, ""
        try:
            from backend.services.snapshot_extractors.launch_check import extract as _lc_extract
            from backend.services import scan_snapshot_service as _snap
            from backend.services.connection_registry import registry as _reg

            # 用【内存中完整的 results】，不是上面写库时截断到 100 行的副本
            _items, _obj_total = _lc_extract(results, request.database_filter or "")
            _conn_info = _reg.get_saved(request.connection_id) or {}
            _scope_label = request.database_filter or "全部数据库"

            snapshot_id = _snap.safe_create_snapshot("launch_check", {
                "biz_ref_id": str(task_id),
                "connection_id": request.connection_id,
                "connection_name": _conn_info.get("name", ""),
                # db_name 即检查范围，参与 §7.5 的可比性校验；空串=全部数据库
                "db_name": request.database_filter or "",
                "scan_label": f"上线检查 · {_scope_label}",
                "scan_started_at": _started_at,
                "scan_finished_at": datetime.now().isoformat(),
            }, _items, object_total=_obj_total)
        except Exception as e:
            snapshot_error = str(e)[:200]
            logger.warning("上线检查快照创建失败（不影响检查结果）task_id=%s: %s",
                           task_id, e)

        return ApiResponse(data={
            "task_id": task_id,
            "summary": summary,
            "results": results,
            "snapshot_id": snapshot_id,
            "snapshot_error": snapshot_error,
        })
```

**配套改动**：

1. 函数开头（创建任务前）记录 `_started_at = datetime.now().isoformat()`；
2. 文件顶部确认已有 `logger`（**若无必须补** `logger = logging.getLogger(__name__)`——v1.3 曾因 `logger` 未定义导致快照失败时抛 `NameError`、进而把已提交的审核结果整个丢弃返回 500，见 `sql_audit.py` 的同类修复）；
3. 确认 `datetime` 已导入。

> `safe_create_snapshot` 本身已吞异常，外层 `try` 是对**抽取器**异常的兜底。两层都要有——抽取器在 `safe_create_snapshot` 之外执行。

---

### 7.4 CHANGED 判定 — `scan_compare_service.detect_change`

在 `elif module == "bigtable":` 分支之后新增：

```python
    elif module == "launch_check":
        # 度量类：索引数/字段数/表数量/字符数 变化超阈值 → GROWTH
        for field in ("索引数", "字段数", "表数量", "字符数"):
            ob_v, ot_v = ab.get(field), at.get(field)
            if ob_v is None or ot_v is None:
                continue
            try:
                old_n, new_n = float(ob_v), float(ot_v)
            except (TypeError, ValueError):
                continue
            pct = _pct_change(old_n, new_n)
            if pct is not None and abs(pct) >= COMPARE_LAUNCH_DELTA_PCT:
                changes.append({"type": "GROWTH", "field": field,
                                "old": old_n, "new": new_n,
                                "pct": round(pct, 1),
                                "direction": "UP" if pct > 0 else "DOWN"})

        # 属性类：类型/排序规则/当前注释 变化 → ATTR
        for field in ("类型", "排序规则", "当前注释"):
            ob_v, ot_v = ab.get(field), at.get(field)
            if ob_v is None and ot_v is None:
                continue
            if str(ob_v or "") != str(ot_v or ""):
                changes.append({"type": "ATTR", "field": field,
                                "old": ob_v, "new": ot_v, "direction": ""})
```

模块常量区新增：

```python
COMPARE_LAUNCH_DELTA_PCT = 20.0   # 上线检查度量变化幅度阈值
```

> 阈值取 20%（低于慢SQL/大表的 30%）。索引数、字段数是**离散小整数**——5→6 就是 20%，属于值得关注的结构变化；用 30% 会让 5→6 被忽略。

---

### 7.5 可比性校验 — `scan_compare_service.validate_pair`

在"7. 同评估尺度"校验之后新增：

```python
    # 8. 上线检查：检查范围必须一致（V1.5.2）
    # 全部数据库 vs 单库的两次结果若允许对比，未覆盖库的问题项会全部
    # 显示为"已解决"—— 一次凭空的大规模整改。这个错误方向不报错、
    # 不异常，只会让人对着假报告做决策，故必须拦截（规约 R-15）。
    if s1.get("module") == "launch_check":
        d1 = (s1.get("db_name") or "").strip()
        d2 = (s2.get("db_name") or "").strip()
        if d1 != d2:
            _lbl = lambda d: d or "全部数据库"
            raise CompareError(
                "E4008",
                f"两次上线检查的范围不同（{_lbl(d1)} vs {_lbl(d2)}），"
                f"问题数变化不可比，已拒绝对比。请选择检查范围相同的两次结果。",
                status=409)
```

---

### 7.6 回填拒绝 — `scan_snapshot_service.rebuild_snapshots`

函数入口处新增：

```python
    if module == "launch_check":
        raise ValueError(
            "上线检查不支持存量回填：历史明细每项仅保留前 100 行且已压平为文本，"
            "回填出的快照与实时快照不可比，会在对比中把未回填的问题项误显示为"
            "「已解决」。请以本次上线之后的检查结果为对比基线。")
```

API 层（`scan_compare.py` 的 rebuild 端点）把该异常转为 **400 + `E4009`**。

---

### 7.7 前端

**`index.html`：`schema-check` 页改 3 页签**

```html
<div v-if="currentPage==='schema-check'">
  <el-tabs v-model="schemaCheckTab" @tab-change="onSchemaCheckTabChange">
    <el-tab-pane label="上线检查" name="inventory">
      <!-- 现有 1021~1069 行内容原样迁入 -->
    </el-tab-pane>
    <el-tab-pane label="扫描历史对比" name="compare">
      <!-- 复制大表治理的 compare 页签，loadSnapshots('launch_check') -->
    </el-tab-pane>
    <el-tab-pane label="已留档对比报告" name="reports">
      <!-- 复制大表治理的 reports 页签 -->
    </el-tab-pane>
  </el-tabs>
</div>
```

**`app.js`**

```javascript
const schemaCheckTab = ref('inventory');
const onSchemaCheckTabChange = (name) => {
  // 与 onBigtableTabChange 同款：切页签时重置对比结果，避免展示上一个模块的残留
  cmpResetResult();
  if (name === 'compare')  loadSnapshots('launch_check');
  if (name === 'reports')  loadCompareReports('launch_check');
};
```

> **`cmpState` 是三个模块共用的单例。** 切页签、切页面时必须调 `cmpResetResult()`，否则会把上一个模块的对比结果显示在当前模块下——既有 `onBigtableTabChange` / `onSlowTasksTabChange` 已是这个做法，照抄即可。

**快照失败提示**：`runSchemaCheck` 拿到响应后，若 `snapshot_error` 非空，给一条 `ElMessage.warning`：

> 本次结果未能存入历史（原因），检查结果不受影响。

---

## 8. 测试设计

### 8.1 新建测试文件

`tests/test_launch_check_snapshot.py`

### 8.2 判定表一致性（锁定 §7.2）

```python
def test_check_spec_covers_all_12_checks():
    """_CHECK_SPEC 必须覆盖 SchemaInspector 的全部检查项。

    新增检查项（C13+）时本用例会失败 —— 这是有意的：
    必须显式决定新项的指纹区分位与度量位，不能靠兜底蒙混过关。
    """
    from backend.engine.schema_inspector import SchemaInspector
    from backend.services.snapshot_extractors.launch_check import _CHECK_SPEC
    actual = {c["id"] for c in SchemaInspector.CHECKS}
    assert actual == set(_CHECK_SPEC), (
        f"未登记: {actual - set(_CHECK_SPEC)}，多余: {set(_CHECK_SPEC) - actual}")


def test_metrics_never_enter_fingerprint():
    """【红线】度量值变化不得改变指纹 —— 否则制造虚假整改。"""
    from backend.services.snapshot_extractors.launch_check import extract
    def _mk(idx_count):
        return [{"id": "C06", "name": "索引数量>=5的表", "severity": "WARNING",
                 "suggestion": "", "count": 1,
                 "rows": [{"数据库": "db1", "表名": "t1", "索引数": idx_count}]}]
    k5 = extract(_mk(5))[0][0].key
    k8 = extract(_mk(8))[0][0].key
    assert k5 == k8, "索引数进了指纹：5→8 会被误判为「已解决+新增」"
    assert extract(_mk(8))[0][0].attrs["索引数"] == 8.0


def test_c01_aggregate_row_uses_collation_as_discriminator():
    """C01 是唯一的聚合行（无表名），须用排序规则区分，表数量不得进指纹"""
    from backend.services.snapshot_extractors.launch_check import extract
    def _mk(collation, cnt):
        return [{"id": "C01", "name": "字符编码非utf8mb4的表", "severity": "WARNING",
                 "suggestion": "", "count": cnt,
                 "rows": [{"数据库": "db1", "排序规则": collation, "表数量": cnt}]}]
    # 同排序规则、表数量不同 → 同一问题项
    assert extract(_mk("latin1_swedish_ci", 12))[0][0].key == \
           extract(_mk("latin1_swedish_ci", 15))[0][0].key
    # 不同排序规则 → 不同问题项
    assert extract(_mk("latin1_swedish_ci", 12))[0][0].key != \
           extract(_mk("gbk_chinese_ci", 12))[0][0].key
```

### 8.3 抽取与对比

```python
def test_extract_fingerprint_stability():
    """同一份结果抽两次，指纹完全一致"""


def test_column_level_checks_distinguish_columns():
    """C02/C04/C08/C09/C12 同表不同列须产出不同指纹"""


def test_failed_check_produces_no_items():
    """执行失败的检查项（error 非空）不产出问题项，避免把「查不了」记成「没问题」"""


def test_changed_on_index_growth():
    """C06 索引数 5→8（+60% ≥ 20%）判为 CHANGED/GROWTH，而非解决+新增"""


def test_changed_on_attr_diff():
    """C08 类型 varchar(600)→varchar(800) 判为 CHANGED/ATTR"""
```

### 8.4 可比性与拒绝路径

```python
def test_reject_different_scope():
    """全部数据库 vs 单库 → E4008 拒绝，且错误文案含两侧实际范围"""
    with pytest.raises(CompareError) as e:
        validate_pair(snap_all_dbs, snap_single_db)
    assert e.value.code == "E4008"
    assert "全部数据库" in e.value.message and "dbA" in e.value.message


def test_same_scope_compares_normally():
    """范围相同 → 正常对比"""


def test_rebuild_explicitly_rejected():
    """回填必须显式拒绝并说明原因，不得静默返回空结果 ——
    静默返回会让人以为「回填过了只是没数据」，从而误信后续对比结论。"""
    with pytest.raises(ValueError, match="不支持存量回填"):
        rebuild_snapshots("launch_check")
```

### 8.5 主流程隔离（G6）

```python
def test_snapshot_failure_does_not_break_check(monkeypatch):
    """快照创建失败时，上线检查照常返回结果，snapshot_id=None"""
    monkeypatch.setattr(_snap, "safe_create_snapshot",
                        lambda *a, **k: (_ for _ in ()).throw(Exception("boom")))
    resp = run_schema_check(req, http_req)
    assert resp.data["results"]                    # 检查结果完整返回
    assert resp.data["snapshot_id"] is None
    assert resp.data["snapshot_error"]


def test_extractor_exception_does_not_break_check(monkeypatch):
    """抽取器抛异常同样不得影响主流程（safe_create_snapshot 之外的一层）"""
```

### 8.6 保留策略

```python
def test_inspection_tasks_in_cleanable_tables():
    from backend.services.retention_service import CLEANABLE_TABLES
    assert "inspection_tasks" in CLEANABLE_TABLES


def test_inspection_results_not_registered_separately():
    """results 靠外键级联清理。单独登记会按自身 created_at 清，
    留下「任务还在、明细被删一半」的残缺记录，比不清理更糟。"""
    from backend.services.retention_service import CLEANABLE_TABLES
    assert "inspection_results" not in CLEANABLE_TABLES


def test_cascade_deletes_results():
    """删 inspection_tasks 后对应 inspection_results 应一并消失"""
```

### 8.7 真实环境验收（SIT）

| # | 用例 | 预期 |
|---|---|---|
| **K1** | 执行一次上线检查 | 响应含 `snapshot_id`；`scan_snapshots` 新增一行 `module='launch_check'` |
| **K2** | 「扫描历史对比」页签 | 列出该实例的历史检查记录 |
| **K3** | 同范围两次对比 | 正常产出 新增/已解决/仍存在/已变化 |
| **K4** | 治理一张无主键表后重新检查再对比 | 该表的 C07 项显示为**已解决**，其余不受影响 |
| **K5** | 给某表加索引 5→8 后对比 | C06 显示为**已变化（GROWTH）**，**不是**已解决+新增 |
| **K6** | 全部库 vs 单库对比 | **409 + E4008**，文案含两侧范围 |
| **K7** | 留档 + 导出 HTML | 与既有三模块体验一致 |
| **K8** | 回填 | **400 + E4009**，说明原因 |
| **K9** | 数据保留页 | 出现 `inspection_tasks` 策略项，可编辑 |
| **K10** | 全量回归 | `pytest` 全绿 |

---

## 9. 施工检查清单

- [ ] 模块名为 **`launch_check`**（不是 `schema_check`）；三命名空间对应关系写入注释
- [ ] `MODULES` 已加 `launch_check`
- [ ] `_CHECK_SPEC` 覆盖全部 12 项，与 §7.2 逐项一致
- [ ] **C01 用「排序规则」作区分位，`表数量` 未进指纹**
- [ ] **C06「索引数」/ C11「字段数」/ C05「字符数」均未进指纹**
- [ ] 未登记检查项的兜底为"全部剩余列当 attrs"（保守方向）
- [ ] 快照用**内存中完整的 `results`**，不是截断到 100 行的库副本
- [ ] `run_schema_check` 中 `logger` **已定义**（未定义会在快照失败时抛 `NameError` 吞掉整个检查结果）
- [ ] 抽取器异常与快照异常**两层都有兜底**，主流程绝不受影响
- [ ] `detect_change` 已加 `launch_check` 分支；阈值 `COMPARE_LAUNCH_DELTA_PCT = 20.0`
- [ ] `validate_pair` 已加范围校验（E4008），错误文案含**两侧实际范围**
- [ ] `rebuild_snapshots` 对 `launch_check` **显式抛错**，不是静默返回空
- [ ] 迁移 `v6/060_launch_check_retention.sql` 注释均为整行 `--`（R-03）
- [ ] `CLEANABLE_TABLES` 只加 `inspection_tasks`，**未**加 `inspection_results`
- [ ] `database.py::retention_defaults` 已补双保险（R-04）
- [ ] 前端切页签调 `cmpResetResult()`（`cmpState` 是三模块共用单例）
- [ ] `snapshot_error` 非空时前端有轻提示
- [ ] 全部 SQL 用 `?` 占位符（R-01）
- [ ] 版本号 → `1.5.2.0`（`config.py` / `VERSION` / 前端标题与页脚）
- [ ] SIT **K4 / K5 / K6 通过**（核心验收项）

---

## 10. 风险与遗留

### 10.1 风险

| # | 风险 | 等级 | 对策 |
|---|---|---|---|
| R1 | 度量值误入指纹 → 虚假整改 | **高** | §7.2 判定表 + `test_metrics_never_enter_fingerprint` 钉死；施工清单单列 |
| R2 | 用截断到 100 行的库副本建快照 → 快照静默丢项 | **高** | §4.1 与 §7.3 反复标注；丢失方式不可见，故列为高风险 |
| R3 | 跨范围对比 → 虚假大规模整改 | **高** | E4008 拦截（§7.5） |
| R4 | 新增检查项（C13+）漏改 `_CHECK_SPEC` | 中 | 兜底为保守方向（全当 attrs）；一致性测试会失败提醒 |
| R5 | `cmpState` 单例导致跨模块串台 | 低 | 切页签重置，照抄既有做法 |
| R6 | 快照创建失败影响主流程 | 低 | 两层兜底 + G6 测试 |

### 10.2 遗留清单（不在本次范围）

| # | 项 | 说明 |
|---|---|---|
| L-03 | `schema_audit` 模块缺 `db_name` 一致性校验 | 同实例不同库的快照可互相对比。风险低于本模块（表现为"全新增+全解决"，异常明显），建议后续与 E4008 统一处理 |

---

## 11. 交付

| 项 | 内容 |
|---|---|
| 数据库变更 | **无表结构变更**，仅 1 条保留策略配置 |
| 新建文件 | 抽取器 1、迁移 1、测试 1 |
| 改动文件 | 后端 4、前端 2、版本 3 |
| 对比引擎改动 | 仅 `detect_change` 与 `validate_pair` 各一处 |
| API 改动 | 1 处响应扩展 + 2 处新增错误码，**其余全部零改动** |

**核心验收项**：K4（已解决判定正确）、K5（变化不被误判成解决+新增）、K6（跨范围拒绝）。
