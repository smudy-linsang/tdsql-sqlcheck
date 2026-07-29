# TDSQL-SQLCheck v1.5 接口设计说明书
## 实例类型感知的规则适用域

| 项 | 内容 |
|---|---|
| 版本 | v1.5.0.0 |
| 基线 | v1.4.0.1（commit `6106a9a`） |
| 协议 | HTTP/1.1 · JSON · UTF-8 |
| 前缀 | `/api/v1` |
| 鉴权 | 沿用 v1.2 Bearer Token + RBAC 中间件，本文档只标注**新增/变更**的权限要求 |
| 关联文档 | `ARCHITECTURE-v1.5-*.md` · `DB-v1.5-*.md` · `DETAIL-v1.5-*.md` |

---

## 1. 接口设计原则

| # | 原则 | 说明 |
|---|---|---|
| I1 | **全部新增参数可选** | 不传即走解析器/全局默认。**所有 v1.4 存量调用方（含前端、CLI、GitLab Webhook、第三方脚本）无需任何改动即可继续工作** |
| I2 | **A 类通道不接受调用方指定实例类型** | 有 `connection_id` 时，实例类型是**客观事实**，由服务端解析。请求体即使带了 `instance_type` 也**忽略**（不报错，但响应中 `instance_type_source` 会如实返回 `probed`/`declared`，调用方可自行发现被忽略了） |
| I3 | **B 类通道接受显式声明** | 无 `connection_id` 的通道（上传/流式/GitLab/CLI），实例类型只能由调用方声明；未声明则取 `system_config.default_instance_type` |
| I4 | **响应必须自证口径** | 凡产出审核结论的接口，响应中一律回带 `instance_type` / `instance_type_source` / `skipped_rules_count`。**不能让调用方猜自己拿到的是哪种口径的结论** |
| I5 | **错误信息可执行** | 拒绝类错误（如跨口径对比）必须说明"为什么拒绝"和"该怎么办"，不能只回一句 `400 Bad Request` |

### 关于 I2 的必要性

如果允许调用方对 A 类通道传 `instance_type`，就等于把 §G1（集中式实例不得出现分布式规则）变成"可绕过的"。有人只要传 `instance_type=distributed`，集中式实例又会跑出 R077。**可靠性保证一旦可被参数绕过，就等于没有保证。**

同理，**规则集也不能反向打开一条不适用的规则**（见 `ARCHITECTURE-v1.5` §5.5）：适用域只做减法，不提供任何加法通道。

---

## 2. 接口变更总览

| 类别 | 数量 | 明细 |
|---|---|---|
| 新增接口 | 3 | 实例类型探测、全局默认口径读、全局默认口径写 |
| 响应体扩展（不改请求） | 6 | 元数据审核、即时审核、文件审核、带元数据审核、实例列表、实例详情 |
| 请求+响应均扩展 | 4 | 文件上传审核、批量流式审核、GitLab 两个审核接口 |
| 语义变更 | 2 | 规则列表（新增 `instance_scope` 字段 + 可选筛选）、扫描对比（新增口径校验） |
| **不变** | 其余全部 | 门禁、规则集、用户、慢SQL、大表治理等接口签名与语义均不变 |

> **门禁接口为什么不变**：`gate_service.evaluate_for_instance()` 接收的是**已经过滤后**的 violations 列表。适用域过滤发生在引擎内部（唯一收口点），门禁自动拿到正确的计数，无需任何接口改动。这是选择方案 D 的直接收益。

---

## 3. 新增接口

### 3.1 探测实例类型

```
POST /api/v1/tdsql/connections/{connection_id}/probe-instance-type
```

**用途**：对指定实例执行一次实例类型探测，刷新 `tdsql_connections.detected_instance_type`。

**权限**：`admin` / `dba`（写实例元数据，等同实例管理操作）

**触发时机**：
1. 前端「实例管理」页手动点击「重新探测」
2. 新建/编辑实例保存成功后自动异步触发一次
3. 扫描时若缓存过期，由解析器内部触发（不经此接口）

**请求体**：无

**响应 200**

```json
{
  "connection_id": "5ea70d74",
  "detected_instance_type": "centralized",
  "declared_instance_type": "distributed",
  "conflict": true,
  "effective_instance_type": "centralized",
  "instance_type_source": "probed",
  "detected_at": "2026-07-29T10:22:31",
  "probe_detail": {
    "proxy_show_status": {"ok": false, "reason": "ERROR 1064: syntax error near '/*proxy*/'"},
    "sharding_rules_table": {"ok": true, "exists": false}
  },
  "message": "探测结论为「集中式」，与实例配置中声明的「分布式」不一致。审核将按探测结果（集中式）执行；如确认声明有误，请在实例编辑中修正实例类型。"
}
```

**字段说明**

| 字段 | 类型 | 说明 |
|---|---|---|
| `detected_instance_type` | string\|null | 探测结论；null = 本次探测无结论 |
| `declared_instance_type` | string | 由 `is_distributed` 换算得到的人工声明值 |
| `conflict` | bool | 探测与声明是否冲突。**前端据此显示红色标记** |
| `effective_instance_type` | string | 最终生效值（探测优先） |
| `instance_type_source` | string | `probed` / `declared` |
| `probe_detail` | object | 两个探针各自的结果，供运维定位"为什么探不出来" |
| `message` | string | 面向使用者的自然语言结论。冲突时必须说清"用哪个"和"该怎么办" |

**响应 404**：`connection_id` 不存在
**响应 400**：实例未激活（未在实例管理中连接）
**响应 200 + `detected_instance_type: null`**：探测失败。**不返回 5xx** —— 探测失败是正常业务分支（网络抖动、权限不足），不是服务端错误；此时 `instance_type_probe_error` 已落库，`message` 说明将退回声明值。

---

### 3.2 读取全局默认实例类型

```
GET /api/v1/config/default-instance-type
```

**权限**：登录即可（前端 B 类通道的选择器需要拿它作为默认选中项）

**响应 200**

```json
{
  "default_instance_type": "distributed",
  "options": [
    {"value": "distributed", "label": "分布式实例"},
    {"value": "centralized", "label": "集中式实例"}
  ],
  "description": "用于无法确定目标实例的审核场景（文件上传、批量流式、GitLab MR、CLI）。出厂值为「分布式」，即按全部规则评估，宁可多报不可漏报。"
}
```

---

### 3.3 设置全局默认实例类型

```
PUT /api/v1/config/default-instance-type
```

**权限**：**仅 `admin`**（全局配置，影响所有无实例上下文的审核）

**请求体**

```json
{ "default_instance_type": "centralized" }
```

**响应 200**

```json
{
  "success": true,
  "default_instance_type": "centralized",
  "message": "已设置全局默认实例类型为「集中式」。该配置最长 5 分钟后在全部服务进程生效。"
}
```

> **`message` 的措辞是硬性要求**：生产以 `uvicorn --workers 2` 运行，配置读取带 300s 进程内缓存，**不存在跨进程即时一致性**。文案一律写"最长 5 分钟生效"，**严禁写"即时生效"**。此为 v1.3.3 已确立的团队约定。

**响应 400**：取值非 `distributed` / `centralized`

```json
{ "detail": "default_instance_type 仅支持 distributed 或 centralized" }
```

**响应 403**：非管理员

---

## 4. 响应体扩展（请求不变）

以下接口**请求签名完全不变**，仅在响应中增加口径字段。**存量调用方不受影响。**

### 4.1 在线元数据审核 —— 缺陷现场

```
POST /api/v1/audit/extract-and-audit
```

**请求**：不变（`connection_id` 本就必填，属 A 类通道，服务端自动解析）

**响应新增字段**

```json
{
  "report_id": 1234,
  "summary": { "total_sql": 86, "error_count": 3, "warning_count": 12, "...": "..." },
  "results": ["..."],

  "instance_type": "centralized",
  "instance_type_source": "probed",
  "skipped_rules_count": 25,
  "scope_notice": "本次按【集中式实例】口径评估，已跳过 25 条仅分布式实例适用的规则（如 R077 分片键检查）。",
  "instance_type_conflict": false
}
```

**行为变更（这就是缺陷修复本身）**：

| 实例类型 | v1.4.0.1 | v1.5 |
|---|---|---|
| 分布式 | 跑 119 条 | 跑 119 条（**逐条完全一致，零回归**） |
| 集中式 | 跑 119 条 → **误报 R077 等 25 条** | 跑 94 条 → **不再误报** |

### 4.2 即时 SQL 审核

```
POST /api/v1/audit/sql
```

**请求**：不变。`connection_id` 保持选填。

- 传了 `connection_id` → A 类，自动解析（`source` = `probed`/`declared`）
- 未传 → B 类，取全局默认（`source` = `default`）

**响应新增**：同 §4.1 的 5 个字段。

**附带修复（`ARCHITECTURE-v1.5` §5.6）**：本接口原先无条件调用"深度分布式检查"，产出 `DIST_001`/`DIST_002`，且分片键是**硬编码的 `order_id`/`user_id`**。v1.5 起：

1. 仅当口径为 `distributed` 时执行；
2. 分片键改为从 `table_metadata` 真实读取，取不到则整段跳过，**不再使用任何硬编码字段名**。

> 这两条必须同时做。只加实例类型闸门的话，分布式实例上"拿虚构分片键审核真实 SQL"的误报依然存在。

### 4.3 文件审核

```
POST /api/v1/audit/file
```

请求不变（`connection_id` 选填，语义同 §4.2），响应新增同 5 个字段。

### 4.4 带元数据审核

```
POST /api/v1/tdsql/audit/with-metadata
```

请求不变（`connection_id` 选填），响应的 `audit_result` 对象内新增同 5 个字段。

### 4.5 实例列表

```
GET /api/v1/tdsql/connections
```

**响应每个实例对象新增**

```json
{
  "id": "5ea70d74",
  "name": "SIT-分布式实例A",
  "is_distributed": 1,

  "declared_instance_type": "distributed",
  "detected_instance_type": "centralized",
  "effective_instance_type": "centralized",
  "instance_type_source": "probed",
  "instance_type_conflict": true,
  "instance_type_detected_at": "2026-07-29T10:22:31"
}
```

**前端呈现要求**：`instance_type_conflict = true` 时，「类型」列必须显示**红色警示标记**并附 tooltip：

> 声明为「分布式」，但系统探测结果为「集中式」。审核按探测结果执行。请核实实例配置。

**保留 `is_distributed` 原样返回**：既有前端逻辑（如慢SQL数据源选择、`connForm` 回填）依赖它，不能改。

### 4.6 实例详情

```
GET /api/v1/tdsql/connections/{connection_id}
```

同 §4.5，额外返回 `instance_type_probe_error`（供运维定位探测失败原因）。

---

## 5. 请求 + 响应均扩展（B 类通道）

这些通道**物理上没有目标实例**，实例类型只能由调用方声明。

### 5.1 文件上传审核

```
POST /api/v1/audit/upload      (multipart/form-data)
```

**新增表单字段**

| 字段 | 类型 | 必填 | 默认 | 取值 |
|---|---|---|---|---|
| `instance_type` | string | ❌ | `system_config.default_instance_type` | `distributed` / `centralized` |

**响应新增**：同 §4.1 的 5 个字段，其中 `instance_type_source` = `request`（显式传了）或 `default`（未传）。

**前端**：文件上传对话框增加一个实例类型下拉框，默认选中全局默认值，下方提示：

> 上传文件审核无法确定目标实例，请选择按哪种实例类型的规则评估。默认按「分布式」评估（规则最全）。

### 5.2 批量流式审核

```
POST /api/v1/audit/batch-stream     (multipart/form-data, 响应 NDJSON)
```

新增表单字段同 §5.1。

**NDJSON 首行新增一条元信息帧**（在逐条结果之前）：

```json
{"type":"meta","instance_type":"distributed","instance_type_source":"default","skipped_rules_count":0}
```

**兼容性**：既有前端逐行 `JSON.parse` 后按字段取值，新增的 `type=meta` 帧若不被识别会被当作一条结果渲染。**因此前端必须同步改造：识别并跳过 `type` 字段存在的帧**。此项列入 `DETAIL-v1.5` 的前端改造清单，属**破坏性变更，必须前后端同版本上线**。

### 5.3 GitLab MR 差异审核

```
POST /api/v1/gitlab/audit-diff
```

**请求体新增（可选）**

```json
{ "instance_type": "centralized" }
```

未传则取全局默认。

**说明**：GitLab Webhook 由 GitLab 自动发起，不可能携带此参数，因此实际生效的一定是全局默认值。若某仓库确定只面向集中式实例，可通过**每仓库配置**（后续版本）或调整全局默认解决。**本版本不做每仓库配置** —— 属于 §3.3 的非目标，避免范围蔓延。

**响应新增**：同 §4.1 的 5 个字段。

### 5.4 GitLab 仓库全量审核

```
POST /api/v1/gitlab/audit-repository
```

同 §5.3。

---

## 6. 语义变更接口

### 6.1 规则列表 —— 暴露适用域

```
GET /api/v1/rules
GET /api/v1/rules?instance_type=centralized
```

**新增查询参数**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `instance_type` | string | ❌ | 传入则**额外**返回 `effective_total` 与逐条 `effective` 标记；不传则行为完全同 v1.4 |

**响应（不传 `instance_type`，与 v1.4 兼容）**

```json
{
  "total": 119,
  "rules": [
    {
      "rule_id": "R077",
      "category": "distributed",
      "severity": "ERROR",
      "description": "TDSQL分布式实例建表必须声明分片键...",
      "enabled": true,
      "spec_source": "TDSQL数据库开发规范 - 分布式建表规范",
      "fix_suggestion": "...",

      "instance_scope": "distributed"
    }
  ]
}
```

> `instance_scope` **无条件返回**（即使不传查询参数）。这是规则的固有属性，规则管理页需要它来展示"适用域"列。新增字段不影响既有解析。

**响应（传 `instance_type=centralized`）**

```json
{
  "total": 119,
  "instance_type": "centralized",
  "effective_total": 94,
  "skipped_total": 25,
  "rules": [
    { "rule_id": "R077", "instance_scope": "distributed", "effective": false, "...": "..." },
    { "rule_id": "R012", "instance_scope": "all",         "effective": true,  "...": "..." }
  ]
}
```

### 6.2 规则分类统计

```
GET /api/v1/rules/categories?instance_type=centralized
```

传 `instance_type` 时，各分类下只统计该口径下实际生效的规则。用于规则管理页在切换"按实例类型查看"时同步刷新分类计数。

### 6.3 规则集详情 —— 补充实跑条数

```
GET /api/v1/rule-sets/{rule_set_id}
```

**响应新增**

```json
{
  "rule_set_id": "strict",
  "enabled_count": 119,

  "effective_counts": {
    "distributed": 119,
    "centralized": 94
  }
}
```

**为什么必须加**：规则集页面显示"启用 119 条"是**全局口径**。集中式实例上实跑只有 94 条。不明示这个差异，使用者会疑惑"我明明启用了 119 条，为什么报告里跳过了 25 条"。

**这不是规则集失效**——适用域是客观事实过滤，规则集是主观尺度，两者串联，**规则集不能反向打开一条不适用的规则**。

### 6.4 扫描对比 —— 口径校验

```
POST /api/v1/scan-compare/compare
```

**请求**：不变

**新增校验逻辑（三态）**

| 基线口径 | 对比口径 | 行为 |
|---|---|---|
| 同值 | 同值 | ✅ 正常对比 |
| `distributed` | `centralized`（或反之） | ❌ **400 拒绝** |
| NULL（v1.5 前快照） | 任意 | ⚠️ 允许，响应带 `scope_warning` |

**响应 400（跨口径）**

```json
{
  "detail": "两个快照的评估口径不同（基线=分布式实例，对比=集中式实例），问题数不具可比性，无法生成对比报告。请选择同一实例类型的两次扫描进行对比。"
}
```

**响应 200（含 NULL 侧）**

```json
{
  "diff": { "...": "..." },
  "scope_warning": "基线快照采集于 v1.5 之前，评估口径未知。本次问题数变化可能包含规则适用域调整因素，不代表真实整改成效，请结合明细逐条核对。"
}
```

**前端要求**：`scope_warning` 非空时，在对比报告顶部渲染**橙色警示条**，且 HTML 导出（`GET /compare/html`）也必须包含该警示——导出件常被作为汇报材料，不能丢掉这个前提。

**这条设计的意义**：不加这个校验，v1.5 上线当天所有集中式实例都会显示一次凭空的大幅"整改"。这与 v1.4 消灭的"换项目刷低问题数"是同一类伪命题。

### 6.5 审核历史列表 —— 回显口径

```
GET /api/v1/audit/extracted-reports
GET /api/v1/audit/history
```

**响应每条记录新增**

```json
{
  "id": 1234,
  "instance_type": "centralized",
  "instance_type_source": "probed",
  "skipped_rules_count": 25
}
```

`instance_type` 为 `null` 时（v1.5 前记录），前端「口径」列显示灰色 `未知`，tooltip：

> 该报告产生于 v1.5 之前，当时系统未区分实例类型，可能包含不适用于本实例的规则告警。

**与 v1.4 `rule_set_id` 的 NULL 呈现方式保持一致**，前端复用同一套"口径未知"渲染逻辑。

---

## 7. 权限矩阵（新增部分）

| 接口 | admin | dba | auditor | developer |
|---|---|---|---|---|
| `POST /tdsql/connections/{id}/probe-instance-type` | ✅ | ✅ | ❌ | ❌ |
| `GET /config/default-instance-type` | ✅ | ✅ | ✅ | ✅ |
| `PUT /config/default-instance-type` | ✅ | ❌ | ❌ | ❌ |
| `GET /rules?instance_type=` | ✅ | ✅ | ✅ | ✅ |

### RBAC 登记（施工必做，易漏）

`backend/services/auth_service.py` 的 `_PATH_TO_MENU` 必须登记新路径：

```python
"/api/v1/config/default-instance-type": "system_config",
```

> **这是一个必须显式处理的坑**：权限中间件对**未登记路径默认放行**。v1.3 的 `/api/v1/audit/extracted-reports` 就因未登记而不得不在处理函数内自行 `_require_admin`。
>
> 本次两条新接口的处理方式：
> - `PUT /config/default-instance-type`：**登记到 `_PATH_TO_MENU`**（走中间件），**并且**在处理函数内显式校验 `role == "admin"`（双保险）。仅靠中间件不够——`system_config` 菜单可能同时授予了 dba。
> - `POST /tdsql/connections/{id}/probe-instance-type`：路径前缀 `/api/v1/tdsql/connections` 已在 `_PATH_TO_MENU` 中登记为 `instances`，自动继承，无需新增。

---

## 8. 兼容性矩阵

| 调用方 | 影响 | 是否需要改动 |
|---|---|---|
| 前端（本仓库） | 需渲染口径横幅、冲突标记、B类选择器；**NDJSON meta 帧必须处理** | ✅ **必须同版本上线** |
| CLI (`backend/cli.py`) | 无实例上下文，走全局默认 `distributed` = 行为与 v1.4 一致 | ⚪ 可选（建议加 `--instance-type` 参数） |
| GitLab Webhook | GitLab 侧不可能传参，走全局默认 = 行为与 v1.4 一致 | ❌ 无需改动 |
| 第三方脚本（A 类接口） | 请求签名不变；**集中式实例的审核结果会改变**（这正是修复本身） | ❌ 无需改动，但**需在发布说明中明确告知** |
| 第三方脚本（B 类接口） | 不传新参数 = 走 `distributed` = 行为与 v1.4 逐条一致 | ❌ 无需改动 |
| 监控/Prometheus | 指标名与标签不变 | ❌ 无需改动 |

### 唯一的破坏性变更

**`POST /api/v1/audit/batch-stream` 的 NDJSON 首帧新增 `type=meta`。**

不识别该帧的旧客户端会把它当成一条审核结果渲染出来（显示为一条空白/异常记录），不会崩溃但会显示错乱。

**处置**：
- 前端同版本改造（识别并跳过带 `type` 字段的帧）；
- 发布说明中单列此项；
- 若确认有外部消费方，可为其保留 `?meta=0` 参数关闭元信息帧（**默认开启**）。

---

## 9. 接口清单速查

| 方法 | 路径 | 变更 |
|---|---|---|
| POST | `/api/v1/tdsql/connections/{id}/probe-instance-type` | 🆕 新增 |
| GET | `/api/v1/config/default-instance-type` | 🆕 新增 |
| PUT | `/api/v1/config/default-instance-type` | 🆕 新增 |
| POST | `/api/v1/audit/extract-and-audit` | 📤 响应扩展 |
| POST | `/api/v1/audit/sql` | 📤 响应扩展 + 行为修复 |
| POST | `/api/v1/audit/file` | 📤 响应扩展 |
| POST | `/api/v1/tdsql/audit/with-metadata` | 📤 响应扩展 |
| GET | `/api/v1/tdsql/connections` | 📤 响应扩展 |
| GET | `/api/v1/tdsql/connections/{id}` | 📤 响应扩展 |
| POST | `/api/v1/audit/upload` | 📥📤 请求+响应扩展 |
| POST | `/api/v1/audit/batch-stream` | 📥📤 请求+响应扩展（**破坏性**） |
| POST | `/api/v1/gitlab/audit-diff` | 📥📤 请求+响应扩展 |
| POST | `/api/v1/gitlab/audit-repository` | 📥📤 请求+响应扩展 |
| GET | `/api/v1/rules` | 🔀 新增字段 + 可选筛选 |
| GET | `/api/v1/rules/categories` | 🔀 可选筛选 |
| GET | `/api/v1/rule-sets/{id}` | 📤 响应扩展 |
| POST | `/api/v1/scan-compare/compare` | 🔀 新增口径校验（可能 400） |
| GET | `/api/v1/scan-compare/compare/html` | 📤 导出件含警示条 |
| GET | `/api/v1/audit/extracted-reports` | 📤 响应扩展 |
| GET | `/api/v1/audit/history` | 📤 响应扩展 |
| — | 门禁 / 规则集配置 / 用户 / 慢SQL / 大表治理 等 | ⚪ 无变更 |
