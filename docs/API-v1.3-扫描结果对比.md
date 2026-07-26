# API-v1.3-扫描结果对比 (接口说明书)

> **目标版本**：V1.3.0.0
> **基线版本**：V1.2.0.9（main @ `a700de5`，2026-07-26 逐项复核）
> **统一前缀**：`/api/v1/scan-compare`
> **配套文档**：《ARCHITECTURE-v1.3-扫描结果对比.md》 概要设计 / 《DETAIL-v1.3-扫描结果对比.md》 详细设计
> **编制**：智能体 A（原始设计） / 智能体 M（Mavis 合并与补充）

---

## 1. 通用约定

### 1.1 认证

除特别说明外，所有接口需携带登录令牌：

```
Authorization: Bearer <token>
```

令牌由 `POST /api/v1/auth/login` 获取。

> **例外**：`GET /compare/html` 为浏览器直接打开的下载型接口，`window.open` 无法携带请求头，
> 额外接受 `token` 查询参数（详见 §3.4）。该方式仅限此类导出接口，且服务端记录操作日志。

> **M 补充 5 [审计日志]**: 所有 `/api/v1/scan-compare/*` 接口调用必须调用既有 `log_operation()` 写入 `operation_logs`（M 补充 5），记录 operator / operation_type / target_id，便于 RBAC 审计追溯。

### 1.2 模块枚举 `module`

| 值 | 对应功能菜单 |
|---|---|
| `schema_audit` | SQL审核 → 在线元数据审核 |
| `slow_scan` | 慢SQL治理 → 扫描任务 |
| `bigtable` | 实例与体检 → 大表治理 |

### 1.3 响应约定

- 成功：`200`，直接返回业务 JSON（与平台既有 `sql_audit`/`daily_inspect` 风格一致）
- 失败：非 2xx，返回 `{"detail": "错误描述", "code": "E4001"}`
- 时间字段：ISO8601 字符串，如 `2026-07-15T02:05:11`

### 1.4 错误码

| 错误码 | HTTP | 含义 |
|---|---|---|
| `E4001` | 400 | 只能选择两次扫描结果进行对比 |
| `E4002` | 400 | 不能与自身对比 |
| `E4003` | 400 | 不同实例/模块的扫描结果不可对比 |
| `E4004` | 404 | 快照不存在或已被数据保留策略清理 |
| `E4005` | 409 | 指纹算法版本不一致，无法可靠对比 |
| `E4006` | 400 | 不支持的模块类型 |
| `E4031` | 403 | 无该模块数据的访问权限 |

### 1.5 权限矩阵

访问需同时满足：① 具备 `scan-compare` 菜单权限；② 具备 `module` 对应模块的菜单权限（二次校验，防越权）。

| 接口 | admin | dba | auditor | developer |
|---|:---:|:---:|:---:|:---:|
| `GET /snapshots` | ✅ | ✅ | ✅ | 按模块权限 |
| `GET /snapshots/{id}` | ✅ | ✅ | ✅ | 按模块权限 |
| `POST /compare` | ✅ | ✅ | ✅ | 按模块权限 |
| `GET /compare/html` | ✅ | ✅ | ✅ | 按模块权限 |
| `GET /reports` | ✅ | ✅ | ✅ | ❌ |
| `POST /reports` | ✅ | ✅ | ✅ | ❌ |
| **`DELETE /reports/{id}`** | ✅ | ❌ | ❌ | ❌ | ← M 补充 4：报告留档删除仅 admin |
| `POST /snapshots/rebuild` | ✅ | ✅ | ❌ | ❌ |

---

## 2. 接口总览

| # | 方法 | 路径 | 说明 |
|---|---|---|---|
| 1 | GET | `/api/v1/scan-compare/snapshots` | 扫描快照列表（筛选 + 分页） |
| 2 | GET | `/api/v1/scan-compare/snapshots/{id}` | 快照详情 |
| 3 | POST | `/api/v1/scan-compare/compare` | 两次扫描结果比对 |
| 4 | GET | `/api/v1/scan-compare/compare/html` | 对比报告 HTML |
| 5 | POST | `/api/v1/scan-compare/snapshots/rebuild` | 存量数据回填 |
| 6 | GET/POST | `/api/v1/scan-compare/reports` | 对比报告留档 列表/保存 |
| 7 | DELETE | `/api/v1/scan-compare/reports/{id}` | 删除报告留档（M 补充 4，仅 admin） |

---

## 3. 接口详细规格

### 3.1 快照列表

```
GET /api/v1/scan-compare/snapshots
```

支撑需求「按数据库实例筛选，快速找到想对比的两次结果」。

**请求参数（Query）**

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|:---:|---|---|
| `module` | string | 是 | — | 模块枚举，见 §1.2 |
| `connection_id` | string | 否 | `""` | 数据库实例ID；`__unknown__` 查询无实例信息的历史回填数据 |
| `db_name` | string | 否 | `""` | 库名，精确匹配 |
| `date_from` | string | 否 | `""` | 起始日期 `YYYY-MM-DD`（按 `scan_finished_at`） |
| `date_to` | string | 否 | `""` | 结束日期 `YYYY-MM-DD`，含当日 |
| `limit` | int | 否 | `20` | 每页条数，上限 100 |
| `offset` | int | 否 | `0` | 偏移量 |

**响应 200**

```json
{
  "total": 3,
  "items": [
    {
      "id": 135,
      "module": "schema_audit",
      "biz_ref_id": "1088",
      "connection_id": "conn-8f2a",
      "connection_name": "核心交易库-SIT",
      "db_name": "trade_core",
      "scan_label": "extracted_trade_core_20260730_020000.sql",
      "scan_started_at": "2026-07-30T02:00:00",
      "scan_finished_at": "2026-07-30T02:03:41",
      "time_window_start": "",
      "time_window_end": "",
      "object_total": 275,
      "issue_total": 96,
      "error_count": 31,
      "warning_count": 65,
      "truncated": false,
      "source_kind": "live",
      "created_by": "admin",
      "created_at": "2026-07-30T02:03:41"
    }
  ]
}
```

> 列表**不返回** `snapshot_json`（避免大字段传输）。

**示例**

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8000/api/v1/scan-compare/snapshots?module=schema_audit&connection_id=conn-8f2a&limit=20"
```

---

### 3.2 快照详情

```
GET /api/v1/scan-compare/snapshots/{id}
```

**请求参数**

| 参数 | 位置 | 类型 | 默认 | 说明 |
|---|---|---|---|---|
| `id` | path | int | — | 快照ID |
| `with_issues` | query | bool | `false` | 是否返回问题项明细数组 |

**响应 200**（`with_issues=false`）

```json
{
  "id": 135,
  "module": "schema_audit",
  "connection_name": "核心交易库-SIT",
  "db_name": "trade_core",
  "scan_finished_at": "2026-07-30T02:03:41",
  "stats": {
    "object_total": 275,
    "issue_total": 96,
    "by_severity": { "ERROR": 31, "WARNING": 65 },
    "by_issue_type": { "R003": 12, "R012": 28, "R005": 9 }
  },
  "fingerprint_algo": "v1",
  "truncated": false
}
```

`with_issues=true` 时追加 `issues` 数组，元素结构见《详细设计》§3.1。

**错误**：`404 E4004` 快照不存在。

---

### 3.3 两次扫描结果比对 ★核心接口

```
POST /api/v1/scan-compare/compare
```

**请求体**

```json
{
  "module": "schema_audit",
  "snapshot_ids": [101, 118],
  "include_details": true,
  "detail_limit": 500
}
```

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:---:|---|---|
| `module` | string | 是 | — | 模块枚举 |
| `snapshot_ids` | int[] | 是 | — | **必须恰好 2 个**，否则 `400 E4001`；顺序无关，服务端按扫描时间自动定基准 |
| `include_details` | bool | 否 | `true` | 为 `false` 时只返回 `summary`（用于列表快速预览） |
| `detail_limit` | int | 否 | `500` | 每类明细最多返回条数，上限 2000（明细量控制设计依据见概要设计 §2.3） |

**响应 200**

```json
{
  "module": "schema_audit",
  "base": {
    "id": 101,
    "scan_finished_at": "2026-07-01T02:03:11",
    "scan_label": "extracted_trade_core_20260701_020000.sql",
    "issue_total": 412
  },
  "target": {
    "id": 118,
    "scan_finished_at": "2026-07-15T02:05:22",
    "scan_label": "extracted_trade_core_20260715_020000.sql",
    "issue_total": 286
  },
  "connection_name": "核心交易库-SIT",
  "db_name": "trade_core",
  "labels": { "fixed": "已修复", "new": "新增问题", "remain": "遗留未整改" },
  "warnings": [],
  "summary": {
    "base_total": 412,
    "target_total": 286,
    "fixed_count": 168,
    "new_count": 42,
    "remain_count": 244,
    "changed_count": 7,
    "fix_rate": 40.8,
    "delta": -126,
    "by_severity": {
      "base":   { "ERROR": 190, "WARNING": 222 },
      "target": { "ERROR": 118, "WARNING": 168 }
    }
  },
  "fixed": [
    {
      "key": "a1b2c3d4e5f60718",
      "object_name": "trade_core.t_order",
      "object_type": "TABLE",
      "issue_type": "R003",
      "severity": "ERROR",
      "title": "[R003] CREATE TABLE 未指定主键",
      "detail": "TDSQL 要求每个表必须有主键",
      "suggestion": "建议添加自增主键"
    }
  ],
  "new": [],
  "remain": [],
  "changed": [
    {
      "key": "9f8e7d6c5b4a3210",
      "object_name": "trade_core.t_pay_log",
      "issue_type": "R012",
      "severity": "ERROR",
      "title": "[R012] 禁止使用 SELECT *",
      "change": { "type": "SEVERITY", "field": "severity",
                  "old": "WARNING", "new": "ERROR", "direction": "UP" }
    }
  ],
  "detail_truncated": { "fixed": false, "new": false, "remain": false, "changed": false }
}
```

**字段说明（对应领导五问）**

| 响应字段 | 业务含义 |
|---|---|
| `summary.base_total` | **之前有多少问题** |
| `summary.target_total` | **现在有多少问题** |
| `summary.fixed_count` | **改了多少** |
| `summary.remain_count` | **还留有多少** |
| `summary.new_count` | **有没有新增的问题** |
| `summary.fix_rate` | 整改率 = 已修复 ÷ 之前总数 × 100 |
| `summary.delta` | 净变化（负数为总量下降） |

**`labels` 按模块差异化**（前端与报告直接取用，勿硬编码）：

| module | fixed | new | remain |
|---|---|---|---|
| `schema_audit` / `bigtable` | 已修复 | 新增问题 | 遗留未整改 |
| `slow_scan` | **已消失（未复现）** | 新出现慢SQL | 仍然存在 |

> 慢SQL 为**时间窗口采样**，某条未出现可能是业务未触发而非已优化，故文案区分，避免高估整改成效。

**`warnings` 可能取值**

| 提示 | 触发条件 |
|---|---|
| `两次扫描的时间窗口长度差异较大（24.0h vs 1.0h），整改率仅供参考` | 慢SQL 两次窗口时长比 > 2 或 < 0.5 |
| `基准快照问题项过多已被截断，对比结果可能不完整` | 快照 `truncated=true` |
| `历史回填数据缺少实例信息，请确认对比对象一致` | 参与比对的快照 `source_kind=rebuild` 且 `connection_id` 为空 |
| **M 补充**: `两份快照位于不同 set 节点，对比结果按 set 隔离` | 跨 set 比对（M 补充 1） |

**错误响应**

| HTTP | code | 场景 |
|---|---|---|
| 400 | `E4001` | `snapshot_ids` 数量 ≠ 2 |
| 400 | `E4002` | 两个 ID 相同 |
| 400 | `E4003` | 两份快照 module 或 connection_id 不一致 |
| 404 | `E4004` | 任一快照不存在 |
| 409 | `E4005` | `fingerprint_algo` 不一致 |
| 403 | `E4031` | 无该模块权限 |

```json
{ "detail": "只能选择两次扫描结果进行对比", "code": "E4001" }
```

**示例**

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"module":"schema_audit","snapshot_ids":[101,118]}' \
  http://127.0.0.1:8000/api/v1/scan-compare/compare
```

---

### 3.4 对比报告 HTML

```
GET /api/v1/scan-compare/compare/html
```

**请求参数（Query）**

| 参数 | 类型 | 必填 | 说明 |
|---|---|:---:|---|
| `module` | string | 是 | 模块枚举 |
| `snapshot_ids` | int[] | 是 | 重复传参两次：`?snapshot_ids=101&snapshot_ids=118` |
| `token` | string | 否 | 浏览器直开时的令牌（无法带请求头时使用） |
| `inline` | bool | 否 | `true` 浏览器内打开预览；默认 `false` 触发下载 |

**响应 200**：`Content-Type: text/html; charset=utf-8`
默认 `Content-Disposition: attachment; filename=ScanCompare_{module}_{baseId}_{targetId}.html`

报告为**自包含静态 HTML**（内联 CSS、无外部资源引用），可离线打开、邮件转发、直接打印。
内容结构见《详细设计》§7.1：报告头 → 可比性提示 → KPI 六宫格 → 严重级别分布对比 → 四类明细 → 页脚。

**错误**：同 §3.3。

---

### 3.5 存量数据回填

```
POST /api/v1/scan-compare/snapshots/rebuild
```

从既有业务表补建历史快照，使功能上线后立即可用，无需等待两轮新扫描。

**请求体**

```json
{ "module": "slow_scan", "limit": 200, "overwrite": false }
```

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|:---:|---|---|
| `module` | string | 是 | — | 模块枚举；不支持一次回填全部，需分模块调用 |
| `limit` | int | 否 | `200` | 本次处理源记录数上限（上限 1000），避免长事务 |
| `overwrite` | bool | 否 | `false` | 已存在快照是否覆盖重建 |

**响应 200**

```json
{ "module": "slow_scan", "scanned": 200, "created": 173, "skipped": 25, "failed": 2,
  "message": "回填完成，失败 2 条详见服务端日志" }
```

**说明**
- 幂等：`(module, biz_ref_id)` 唯一约束保证重复调用不产生重复快照
- 分批：返回 `created + skipped < scanned` 或 `scanned == limit` 时，可继续调用直至 `scanned` 为 0
- `schema_audit` 历史记录无实例信息，回填后 `connection_id` 为空，前端归入"未知实例"分组

**权限**：仅 `admin`、`dba`。

---

### 3.6 对比报告留档

```
GET  /api/v1/scan-compare/reports        # 列表
POST /api/v1/scan-compare/reports        # 保存当前对比结果
DELETE /api/v1/scan-compare/reports/{id} # 删除留档 (M 补充 4, 仅 admin)
```

**POST 请求体**

```json
{ "module": "schema_audit", "snapshot_ids": [101, 118], "title": "核心交易库 7月上半月整改情况" }
```

服务端重新执行一次比对并将**汇总**落库（不存明细，明细可随时由快照重算），返回 `{"id": 12}`。

**GET 请求参数**：`module`、`connection_id`、`limit`、`offset`

**GET 响应 200**

```json
{
  "total": 1,
  "items": [
    { "id": 12, "module": "schema_audit", "title": "核心交易库 7月上半月整改情况",
      "connection_name": "核心交易库-SIT", "db_name": "trade_core",
      "base_snapshot_id": 101, "target_snapshot_id": 118,
      "base_scan_at": "2026-07-01T02:03:11", "target_scan_at": "2026-07-15T02:05:22",
      "base_total": 412, "target_total": 286,
      "fixed_count": 168, "new_count": 42, "remain_count": 244, "fix_rate": 40.8,
      "created_by": "admin", "created_at": "2026-07-15T09:12:00" }
  ]
}
```

**DELETE 请求**：`DELETE /api/v1/scan-compare/reports/{id}` — 仅 admin 可调用，其他角色 403。

---

## 4. 既有接口改造说明（含 D1/D2/D4 缺陷修复，施工细节见《详细设计》§11）

**兼容性总则**：全部改造**仅新增可选参数与返回字段**，不删除、不重命名既有字段，老前端不受影响。
唯一行为变更为 §4.4（属修正错误行为），已在升级说明中标注。

### 4.1 在线元数据提取与审核（修复 D1）

```
POST /api/v1/audit/extract-and-audit
```

**变更**：审核结果落库时补写实例信息（本需求"按实例筛选"的前置条件）。

| 项 | 变更 |
|---|---|
| 请求 | 无变化（`connection_id` 本已是入参，`payload.get("connection_id")`） |
| 落库 | `audit_history.connection_id` 写入实际值；`audit_history.db_name`（**新增列**，迁移自动补）写入目标库名 |
| 响应 | 新增 `snapshot_id`（快照ID，可为 `null`） |
| 副作用 | 旁路生成对比快照；失败仅告警，不影响审核结果返回 |

```json
{ "status": "SUCCESS", "report_id": 1088, "snapshot_id": 135,
  "filename": "extracted_trade_core_20260730_020000.sql",
  "extracted_sql": "...", "results": [], "summary": {} }
```

### 4.2 元数据审核历史列表（修复 D1 查询侧）

```
GET /api/v1/audit/extracted-reports
```

**新增可选 Query 参数**：

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `connection_id` | string | `""` | 按实例筛选；`__unknown__` 查询无实例信息的存量记录 |
| `db_name` | string | `""` | 按库名筛选 |
| `date_from` / `date_to` | string | `""` | 按审核时间筛选 `YYYY-MM-DD` |

**响应变更**：每条记录追加 `connection_id`、`db_name`、`connection_name`
（`connection_name` 由 `LEFT JOIN tdsql_connections` 实时解析，连接已删除时为空串）；
列表响应**不再包含** `results_json` 大字段（现前端历史列表未使用，属传输优化，施工时复核确认）。

### 4.3 慢SQL扫描任务列表（修复 D4）

```
GET /api/v1/slow-queries/scan-tasks
```

**新增可选 Query 参数**：`connection_id`、`db_name`、`date_from`、`date_to`
**响应说明**：`connection_name` 字段表中已有、随 `SELECT *` 本就返回，此前仅前端未展示；前端任务表格新增"实例"列。

### 4.4 大表清单（修复 D2）

```
GET /api/v1/bigtable/inventory/{connection_id}
```

**新增可选 Query 参数**：

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `inspection_date` | string | `""` | 指定盘点日期 `YYYY-MM-DD` |

**⚠️ 行为变更（修正既有缺陷 D2）**

| | 变更前 | 变更后 |
|---|---|---|
| 不传 `inspection_date` | 返回该实例**所有历史日期**的记录，同一张表出现多行，清单虚高 | 返回**最近一次盘点**的记录；从未盘点返回空数组 |

此为修正错误行为，升级后大表清单数量可能较此前"减少"，属正确表现，需在升级说明与用户培训中说明。
连带收益：`GET /bigtable/report/{connection_id}`（治理报告）内部复用同一查询，口径同步修正。

---

## 5. 调用时序示例

领导场景：核心交易库 1 号 / 15 号 / 30 号三次扫描，出具 1 号↔15 号对比报告。

```bash
# 0. 登录
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"******"}' | jq -r .token)

# 1. 按实例筛选出该库的扫描历史
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8000/api/v1/scan-compare/snapshots?module=schema_audit&connection_id=conn-8f2a" | jq
# → 得到 id: 101(7-01)、118(7-15)、135(7-30)

# 2. 比对 1 号 ↔ 15 号
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"module":"schema_audit","snapshot_ids":[101,118]}' \
  http://127.0.0.1:8000/api/v1/scan-compare/compare | jq .summary
# → {"base_total":412,"target_total":286,"fixed_count":168,"new_count":42,
#    "remain_count":244,"fix_rate":40.8,"delta":-126}

# 3. 导出对比报告
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8000/api/v1/scan-compare/compare/html?module=schema_audit&snapshot_ids=101&snapshot_ids=118" \
  -o 核心交易库_7月上半月整改对比.html

# 4. 同理可比 101↔135（1号↔30号）、118↔135（15号↔30号）

# 5. 删除留档报告 (M 补充 4, 仅 admin)
curl -X DELETE -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8000/api/v1/scan-compare/reports/12
```

---

## 6. 接口自测清单

| # | 校验点 | 预期 |
|---|---|---|
| 1 | `snapshot_ids` 传 1 个 | 400 `E4001` |
| 2 | `snapshot_ids` 传 3 个 | 400 `E4001` |
| 3 | 两个 ID 相同 | 400 `E4002` |
| 4 | 跨实例比对 | 400 `E4003` |
| 5 | 跨模块比对 | 400 `E4003` |
| 6 | 不存在的快照ID | 404 `E4004` |
| 7 | 勾选顺序颠倒（[118,101]） | 结果与 [101,118] 完全一致 |
| 8 | 慢SQL 两次窗口 24h vs 1h | `warnings` 含窗口提示，仍返回 200 |
| 9 | `include_details=false` | 仅返回 summary，无明细数组 |
| 10 | 无模块权限用户调用 | 403 `E4031` |
| 11 | HTML 报告离线打开 | 样式完整，无外部资源请求 |
| 12 | 既有接口不传新参数 | 行为与 V1.2.0.9 一致（§4.4 除外） |
| 13 | D1：审核后最新 `audit_history` 行带实例信息 | `connection_id`/`db_name` 值正确 |
| 14 | D2：`inspection_date` 传历史日期 | 返回该批次数据 |
| 15 | D4：`connection_id` 筛选扫描任务 | 只返回该实例任务 |
| 16 | **M 补充 1**：跨 set 指纹不冲突 | 同 db 不同 set 的同问题各自独立成项 |
| 17 | **M 补充 2**：跨算法版本比对 | 400 `E4005` |
| 18 | **M 补充 4**：dba 删除留档 | 403 |
| 19 | **M 补充 5**：operation_logs 记录 | 必有过审计记录 |
| 20 | **M 补充 6**：超限截断响应 | `truncated_count` 字段存在，summary `degraded=true` 标记 |
