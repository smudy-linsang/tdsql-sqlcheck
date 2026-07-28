# TDSQL-SQLCheck V1.4 接口说明书
## 全局规则集 + 实例级质量门禁

| 项目 | 内容 |
|---|---|
| 文档类型 | 接口设计说明书 |
| 版本 | V1.4（基线 v1.3.3.1） |
| 关联文档 | 《ARCHITECTURE-v1.4》《DB-v1.4》《DETAIL-v1.4》 |

---

## 1. 通用约定

### 1.1 认证

所有接口需 `Authorization: Bearer <token>`。下载/导出类端点例外规则见 v1.3.3 的白名单机制，本次不涉及。

### 1.2 响应约定

成功：`200`，业务体直接返回。

失败：统一返回 `{"detail": "<中文说明>", "code": "<错误码>"}`。

> 沿用 v1.3.0.1 的裁定：**参数缺失与非法一律返回 400 + 明确错误码**，不使用 FastAPI 默认的 422——422 响应体不含 `code` 字段，会破坏本约定。

### 1.3 权限

| 操作 | 最低角色 |
|---|---|
| 查询规则集 / 门禁配置 | 具备对应菜单权限的任意角色 |
| **切换全局生效规则集** | **admin** |
| **配置实例门禁** | **admin** |
| 编辑规则集条目 | admin / dba（沿用既有） |

> 切换生效规则集会改变全系统的评估尺度，属高影响操作，**必须 admin 独占**，与 v1.3 对比报告留档删除的定级一致。

### 1.4 错误码

| 错误码 | HTTP | 含义 |
|---|---|---|
| `E5001` | 400 | 规则集 ID 缺失或非法 |
| `E5002` | 404 | 规则集不存在 |
| `E5003` | 409 | 该规则集正在生效中，不可删除 |
| `E5004` | 409 | 内置规则集不可删除 |
| `E5011` | 400 | 实例 ID 缺失 |
| `E5012` | 404 | 实例不存在 |
| `E5013` | 400 | 门禁上限非法（-1 表示不限，其余须 >= 0） |
| `E5014` | 400 | 门禁判定模式非法（仅 enforce / observe） |
| `E4007` | 409 | 两次扫描评估尺度不同，拒绝对比（V1.3 对比接口新增） |

---

## 2. 全局规则集

### 2.1 查询当前生效规则集

```
GET /api/v1/rulesets/active
```

**响应**

```json
{
  "rule_set_id": "rs_strict",
  "name": "核心账务严格规则集",
  "is_builtin": false,
  "rule_count": 119,
  "overridden_count": 23,
  "disabled_count": 8,
  "activated_at": "2026-07-28 10:12:33",
  "cache_ttl_seconds": 30
}
```

| 字段 | 说明 |
|---|---|
| `overridden_count` | 该规则集中显式配置过的规则条目数 |
| `disabled_count` | 其中被停用的规则数 |
| `cache_ttl_seconds` | 生效时延上限，供前端提示用（见 §2.2 说明） |

**兜底**：若 `system_config.active_rule_set_id` 缺失或指向已删除的规则集，本接口返回内置 `default` 的信息，不报错——与《ARCHITECTURE-v1.4》§3.2 的兜底链一致。

---

### 2.2 切换全局生效规则集（admin）

```
POST /api/v1/rulesets/{rule_set_id}/activate
```

**响应**

```json
{
  "status": "SUCCESS",
  "rule_set_id": "rs_strict",
  "name": "核心账务严格规则集",
  "effective_within_seconds": 30,
  "message": "已切换全局生效规则集，最长 30 秒内全量生效"
}
```

> ⚠ **`effective_within_seconds` 必须由前端展示给管理员**。生产以 `--workers 2` 运行，规则集解析结果有 30 秒进程内缓存，切换后另一个 worker 最长 30 秒才会重新加载。不告知会导致管理员切换后立即验证、发现未生效而误判为故障。

**错误**

| 场景 | 响应 |
|---|---|
| `rule_set_id` 为空 | 400 `E5001` |
| 规则集不存在 | 404 `E5002` |
| 非 admin | 403 |

**幂等**：对当前已生效的规则集重复调用返回 200，不报错。

**审计**：写 `operation_logs`，`operation_type='set_active_rule_set'`，`target_id=<rule_set_id>`。

> **不提供"停用"接口**。系统必须始终有一个生效规则集（决策第 3 条），因此只提供"切换到另一个"，不提供 deactivate（《ARCHITECTURE-v1.4》INV-2）。

---

### 2.3 规则集列表（既有接口，响应扩展）

```
GET /api/v1/rulesets
```

**响应新增字段**（不删除、不重命名任何既有字段）

```json
{
  "rulesets": [
    {
      "id": "default",
      "name": "默认规则集",
      "is_builtin": 1,
      "is_active": false
    },
    {
      "id": "rs_strict",
      "name": "核心账务严格规则集",
      "is_builtin": 0,
      "is_active": true
    }
  ],
  "active_rule_set_id": "rs_strict"
}
```

`is_active` 是**读取时与 `active_rule_set_id` 比对得出的派生值，不落库**（《DB-v1.4》§2.4）。

---

### 2.4 删除规则集（既有接口，新增前置校验）

```
DELETE /api/v1/rulesets/{rule_set_id}
```

**新增拦截**

| 场景 | 响应 |
|---|---|
| 该规则集正在生效 | 409 `E5003`「该规则集正在全局生效中，请先切换到其它规则集再删除」 |
| 内置规则集 | 409 `E5004`（既有校验，错误码规范化） |

---

## 3. 实例级质量门禁

### 3.1 门禁配置列表

```
GET /api/v1/gate/instances
```

返回**全部实例**及其门禁配置；未配置的实例返回系统默认值并标记 `is_default: true`。

**响应**

```json
{
  "total": 2,
  "default_rule": {"max_error_count": 0, "max_warning_count": 0, "mode": "enforce"},
  "items": [
    {
      "connection_id": "c-core-01",
      "connection_name": "核心账务库",
      "max_error_count": 0,
      "max_warning_count": 0,
      "mode": "enforce",
      "is_default": false,
      "description": "",
      "updated_by": "admin",
      "updated_at": "2026-07-28 11:00:00"
    },
    {
      "connection_id": "c-report-02",
      "connection_name": "内部报表库",
      "max_error_count": 0,
      "max_warning_count": 0,
      "mode": "enforce",
      "is_default": true
    }
  ]
}
```

**为什么返回全部实例而非仅已配置的**：管理员需要看到"哪些实例还没配"，只返回已配置的会让未配置实例隐形，而它们恰恰是走默认 0/0、最可能被拦截的那批。

---

### 3.2 查询单个实例门禁

```
GET /api/v1/gate/instances/{connection_id}
```

响应为 §3.1 中的单个 item 结构。实例不存在返回 404 `E5012`。

---

### 3.3 保存实例门禁（admin）

```
PUT /api/v1/gate/instances/{connection_id}
```

**请求体**

```json
{
  "max_error_count": 0,
  "max_warning_count": -1,
  "mode": "enforce",
  "description": "核心账务库，暂不收紧 WARNING"
}
```

| 字段 | 必填 | 约束 |
|---|---|---|
| `max_error_count` | 是 | `-1`（不限）或 `>= 0` |
| `max_warning_count` | 是 | 同上 |
| `mode` | 否 | `enforce`（默认）/ `observe` |
| `description` | 否 | 备注 |

> **`0` 与 `-1` 的语义极易被理解反**：`0` = 一个都不允许；`-1` = 不限量。前端弹窗必须显式说明。

**响应**

```json
{"status": "SUCCESS", "connection_id": "c-core-01"}
```

**错误**

| 场景 | 响应 |
|---|---|
| 实例不存在 | 404 `E5012` |
| 上限 < -1 | 400 `E5013` |
| 模式非法 | 400 `E5014` |
| 非 admin | 403 |

**审计**：`operation_type='set_instance_gate_rule'`，detail 记录新旧阈值。

---

### 3.4 删除实例门禁配置（admin）

```
DELETE /api/v1/gate/instances/{connection_id}
```

删除后该实例回落系统默认（0/0/enforce）。用于「配错了想恢复默认」。

---

### 3.5 既有门禁接口的处置

| 接口 | 处置 |
|---|---|
| `GET /api/v1/gate/rules/{project_id}` | **DEPRECATED**，兼容期内仍返回旧数据，响应加 `deprecated` 提示 |
| `POST /api/v1/gate/rules` | **DEPRECATED**，兼容期内仍可写旧表，但**不再影响实际判定** |
| `POST /api/v1/gate/strategy/{project_id}` | **DEPRECATED**，同上 |

> 关键：兼容期内这些接口**可以调用成功，但不改变任何放行结论**。必须在响应中明确提示，否则调用方会以为配置生效了（《ARCHITECTURE-v1.4》风险 R-3）。

---

## 4. 扫描结果对比（V1.3 接口变更）

### 4.1 新增尺度一致性校验

以下接口新增 `E4007` 错误：

```
GET  /api/v1/scan-compare/compare
GET  /api/v1/scan-compare/compare/html
POST /api/v1/scan-compare/reports
```

**触发条件**：两个快照的 `rule_set_id` 均非空且不相等。

**响应**

```json
{
  "detail": "两次扫描的评估尺度不同（rs_strict vs default），问题数变化不可比，已拒绝对比",
  "code": "E4007"
}
```

HTTP `409`。

**这条校验修复的是既有缺陷**：V1.3 的 `validate_pair` 校验了模块、实例、指纹算法版本，唯独没有规则集，导致当前就能拿两个不同尺度的快照算出一个看似权威的"整改率"。

### 4.2 存量快照的宽容处理

任一快照 `rule_set_id` 为 `NULL`（V1.4 之前产生）时，**不拒绝**，在 `warnings` 中追加：

```json
{
  "warnings": [
    "其中一次扫描产生于 V1.4 之前，评估尺度未知，整改率仅供参考"
  ]
}
```

取舍理由见《DETAIL-v1.4》§6.3。

### 4.3 快照与对比响应新增字段

| 接口 | 新增字段 |
|---|---|
| `GET /scan-compare/snapshots` | 每项增加 `rule_set_id`、`rule_set_name` |
| `GET /scan-compare/compare` | 顶层增加 `rule_set_id`、`rule_set_name` |

---

## 5. 既有审核接口的兼容期约定

### 5.1 受影响接口

| 接口 | `project_id` 参数 |
|---|---|
| `POST /api/v1/audit/sql` | 接受但**忽略**（不再决定尺度） |
| `POST /api/v1/audit/file` | 同上 |
| `POST /api/v1/audit/upload` | 同上 |
| `POST /api/v1/audit/extract-and-audit` | 同上 |

### 5.2 兼容策略

**不删除参数、不报错**，但在响应中显式提示：

```json
{
  "passed": false,
  "violations": [ ... ],
  "rule_set_id": "rs_strict",
  "rule_set_name": "核心账务严格规则集",
  "deprecated_params": {
    "project_id": "V1.4 起规则集已改为管理员全局启用，本参数不再影响评估尺度，将在后续版本移除"
  }
}
```

**为什么不直接报错**：存量调用方（GitLab 集成、CLI、可能的外部脚本）仍在传该参数，直接报错会打断既有流程。**为什么必须提示**：静默忽略会让调用方以为尺度仍按项目生效，这正是本次要消除的误解。

### 5.3 响应新增 `rule_set_id` / `rule_set_name`

所有审核类接口响应增加这两个字段，使调用方能明确知道"这次用的哪把尺"。这也是报告自解释的基础（《DETAIL-v1.4》§6.4）。

### 5.4 新增 `connection_id` 入参（门禁需要）

| 接口 | 变更 |
|---|---|
| `POST /api/v1/audit/sql` | 新增可选 `connection_id`，用于确定门禁绑定的实例 |
| `POST /api/v1/audit/file` | 同上 |

未传时门禁走系统默认（0/0）。`extract-and-audit` 本就有 `connection_id`，直接复用。

---

## 6. 接口清单汇总

| # | 方法 | 路径 | 权限 | 性质 |
|---|---|---|---|---|
| 1 | GET | `/api/v1/rulesets/active` | 菜单权限 | 新增 |
| 2 | POST | `/api/v1/rulesets/{id}/activate` | **admin** | 新增 |
| 3 | GET | `/api/v1/rulesets` | 菜单权限 | 响应扩展 |
| 4 | DELETE | `/api/v1/rulesets/{id}` | admin | 新增校验 |
| 5 | GET | `/api/v1/gate/instances` | 菜单权限 | 新增 |
| 6 | GET | `/api/v1/gate/instances/{cid}` | 菜单权限 | 新增 |
| 7 | PUT | `/api/v1/gate/instances/{cid}` | **admin** | 新增 |
| 8 | DELETE | `/api/v1/gate/instances/{cid}` | **admin** | 新增 |
| 9 | GET | `/api/v1/gate/rules/{pid}` | — | DEPRECATED |
| 10 | POST | `/api/v1/gate/rules` | — | DEPRECATED |
| 11 | POST | `/api/v1/gate/strategy/{pid}` | — | DEPRECATED |
| 12 | GET | `/api/v1/scan-compare/compare` | 模块权限 | 新增 E4007 |
| 13 | GET | `/api/v1/scan-compare/compare/html` | 模块权限 | 新增 E4007 |
| 14 | POST | `/api/v1/scan-compare/reports` | 模块权限 | 新增 E4007 |
| 15 | GET | `/api/v1/scan-compare/snapshots` | 模块权限 | 响应扩展 |
| 16 | POST | `/api/v1/audit/*` | 既有 | 参数废弃 + 响应扩展 |

---

## 7. RBAC 登记（必做）

新增的写端点必须登记 `auth_service._PATH_TO_MENU`，否则会走「未映射路径默认放行」的兜底分支，对所有登录角色敞开：

```python
    "/api/v1/rulesets": "rulesets",
    "/api/v1/gate": "gate",          # 既有，已覆盖 /gate/instances
```

`/api/v1/rulesets` 若已登记则无需重复。**新增后请运行 `tests/test_rbac_path_coverage.py` 确认无遗漏**——该用例会在写端点漏登记时直接失败（v1.3.2 为此专门建立）。

此外，切换生效规则集与配置实例门禁两个端点，**在处理函数内还需显式 admin 校验**，不能只依赖菜单权限——菜单权限只区分"能不能进这个页面"，区分不了"能不能改全局尺度"。
