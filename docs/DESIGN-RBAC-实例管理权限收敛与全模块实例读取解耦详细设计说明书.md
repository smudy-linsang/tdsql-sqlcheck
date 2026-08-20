# TDSQL-SQLCheck 实例管理权限收敛与全模块实例读取解耦详细设计说明书

| 文档版本 | 编写日期 | 状态 | 适用版本 | 评审对象 |
|---|---|---|---|---|
| **V1.0** | 2026-08-20 | **待评审 (Draft)** | V1.6.1.7+ | 架构师 / 运维智能体 / 安全审计组 |

---

## 1. 背景与问题定义

在 TDSQL-SQLCheck 平台的权限管控体系中，当前存在两项严重的权限与可用性缺陷：

### 1.1 问题一：实例管理写权限越权（安全风险）
- **现象描述**：当系统管理员在“角色与权限管理”中，将“平台治理 ➔ 实例管理（`instances`）”菜单分配给普通开发人员（`developer`）、审计员（`auditor`）或自定义角色后，这些非管理员用户进入“实例管理”页面时，**能够看到并执行“+ 新建连接”、“从 ZK 自动发现”、“编辑连接”、“删除连接”、“设为默认”以及“连接测试”等高危管理操作**。
- **安全要求**：**在任何情况下，只有“系统管理员（`admin`）”和“数据库管理员（`dba`）”拥有新建、编辑、删除、配置和导入实例连接的管理权限；其他被分配了该菜单的角色进入页面只能以只读形式浏览实例清单、形态和状态，严禁出现任何写操作入口。**

### 1.2 问题二：实例基础元数据读权限与管理菜单过度耦合（功能可用性阻断）
- **现象描述**：如果管理员不给普通角色分配“实例管理（`instances`）”菜单，该用户在访问其具备权限的业务模块（例如“即时审核”、“在线元数据审核”、“慢SQL扫描任务”、“大表治理”、“EXPLAIN分析”、“上线检查”、“深度诊断”等）时，页面顶部或表单中用于选择目标数据库实例的**下拉列表全部为空**，导致所有需要依赖实例上下文的业务功能完全不可用。
- **业务要求**：**实例列表的基础只读信息（连接名称、IP、端口、数据库名、实例形态等已脱敏数据）属于全平台各业务模块正常运转所必需的“全局基础元数据”；无论用户角色是否被授予了“实例管理”菜单，只要是合法登录用户，在访问具体业务模块时都必须能够正常加载实例下拉列表。**

---

## 2. 现有架构与根因分析 (Root Cause Analysis)

```
【现状问题调用链】

[未分配 instances 菜单的普通用户]
  │
  ├─► 登录系统
  │     └─► loadAll()
  │           └─► if (visibleMenus.has('instances')) ──[False]──► ❌ 放弃拉取实例列表 (app.js)
  │
  └─► 进入「在线元数据审核」/「扫描任务」页面
        ├─► 下拉框无数据 (savedConnections = [])
        └─► 若手动调用 GET /api/v1/tdsql/connections
              └─► 后端 RBAC 校验 _PATH_TO_MENU 映射为 "instances"
                    └─► visibleMenus.has('instances') == False ──► ❌ 返回 403 Forbidden


[分配了 instances 菜单的普通用户]
  │
  └─► 进入「实例管理」页面
        └─► index.html 中「新建/编辑/删除/ZK发现」按钮无权限限制 ──► ❌ 暴露全部写操作按钮
```

### 2.1 前端根因
1. **页面层缺少按钮级权限门禁**：
   在 [`frontend/index.html:1095-1136`](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/frontend/index.html#L1095-L1136) 中，“`+ 新建连接`”、“`从 ZK 自动发现`”、“`ZK发现配置`”、“`编辑`”、“`删除`”、“`设为默认`”、“`连接`”按钮均未声明 `v-if="canManageInstances"`，直接向所有能进入该页面的角色暴露了管理操作。
2. **初始化加载与菜单强行绑定**：
   在 [`frontend/static/js/app.js:1436`](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/frontend/static/js/app.js#L1436) 中，`loadSavedConnections()` 仅在 `visibleMenus.value.has('instances')` 时才被调用，导致未分配实例管理菜单的用户在全局初始化时缺失了实例列表。

### 2.2 后端根因
1. **RBAC 映射未做读写分离**：
   在 [`backend/services/auth_service.py:339`](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/backend/services/auth_service.py#L339) 中，`_PATH_TO_MENU` 将 `GET /api/v1/tdsql/connections` 单一强绑定到 `"instances"` 菜单；未将“实例基础只读列表”作为全局基础元数据放行。
2. **写端点鉴权边界需硬性收敛**：
   虽然非管理员在某些默认配置下无法写，但需要确保所有实例变更端点在接口层有明确的 `admin/dba` 硬性拦截（防御性编程）。

---

## 3. 架构与详细设计方案

本设计遵循 **“读写完全分离、最小特权原则、全局元数据正交解耦”** 的银行级安全架构原则。

### 3.1 总体架构设计

```
                                  ┌─────────────────────────────────────────┐
                                  │             已认证登录用户               │
                                  └────────────────────┬────────────────────┘
                                                       │
                     ┌─────────────────────────────────┴─────────────────────────────────┐
                     ▼                                                                   ▼
       【基础元数据读权限 (GET)】                                             【实例配置写权限 (POST/PUT/DELETE)】
  ┌──────────────────────────────────────────────┐                   ┌──────────────────────────────────────────────┐
  │ 接口：GET /api/v1/tdsql/connections          │                   │ 接口：                                        │
  │ 范围：已登录全角色 (admin/dba/dev/auditor/..) │                   │  - POST/PUT/DELETE /api/v1/tdsql/connections │
  │ 载荷：脱敏实例清单（密码字段已过滤）           │                   │  - POST /connections/{id}/set-default/connect│
  │ 用途：支撑审核、扫描、大表、诊断等下拉框数据   │                   │  - POST /api/v1/tdsql/discover* (ZK发现/导入) │
  │ 鉴权：合法 Token 即可访问（不与菜单绑定）       │                   │ 范围：仅限 admin 与 dba 角色                 │
  └──────────────────────────────────────────────┘                   │ 鉴权：后端 RBAC 拦截 403，前端按钮隐藏       │
                                                                     └──────────────────────────────────────────────┘
```

---

### 3.2 角色与权限矩阵设计对照

| 角色类型 | 是否分配“实例管理”菜单 | 进入“实例管理”页面表现 | 业务模块（审核/扫描/大表）实例下拉框 | 能否新建/编辑/删除/导入实例 |
|---|---|---|---|---|
| **系统管理员 (admin)** | 必须可见 | **完整管理态**：可见全部按钮与操作 | 正常展示全部实例 | ✅ **允许** |
| **数据库管理员 (dba)** | 必须可见 | **完整管理态**：可见全部按钮与操作 | 正常展示全部实例 | ✅ **允许** |
| **开发人员 (developer)** | **已分配** | **纯只读态**：展示列表，**隐藏**所有新建/编辑/删除/ZK按钮 | 正常展示全部实例 | ❌ **严格禁止** (按钮隐藏+API 403) |
| **开发人员 (developer)** | **未分配** | 菜单不可见，无法进入该页面 | **正常展示全部实例** (解耦后正常加载) | ❌ **严格禁止** |
| **审计员 (auditor)** | **已分配** | **纯只读态**：展示列表，**隐藏**所有新建/编辑/删除/ZK按钮 | 正常展示全部实例 | ❌ **严格禁止** (按钮隐藏+API 403) |
| **审计员 (auditor)** | **未分配** | 菜单不可见，无法进入该页面 | **正常展示全部实例** (解耦后正常加载) | ❌ **严格禁止** |
| **自定义角色 (custom)** | **已分配** | **纯只读态**：展示列表，**隐藏**所有新建/编辑/删除/ZK按钮 | 正常展示全部实例 | ❌ **严格禁止** (按钮隐藏+API 403) |
| **自定义角色 (custom)** | **未分配** | 菜单不可见，无法进入该页面 | **正常展示全部实例** (解耦后正常加载) | ❌ **严格禁止** |

---

## 4. 详细代码改造清单

### 4.1 前端表现层改造

#### (1) [`frontend/index.html`](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/frontend/index.html) - 按钮级权限门禁收敛
- **顶部操作栏**：
  - 将 `<el-button type="success" size="small" @click="openZkDiscovery">从 ZK 自动发现</el-button>` 增加 `v-if="canManageInstances"`；
  - 将 `<el-button type="primary" size="small" @click="openNewConn">+ 新建连接</el-button>` 增加 `v-if="canManageInstances"`；
- **表格操作列**：
  - 将 `连接` (`connectInstance`)、`设为默认` (`setDefaultConn`)、`编辑` (`openEditConn`)、`删除` (`deleteConn`) 增加 `v-if="canManageInstances"`；
  - 若当前用户非 `canManageInstances`，操作列中仅展示其可用的操作（若无操作则呈现 `—` 或纯只读展示），绝不展示变更入口。

#### (2) [`frontend/static/js/app.js`](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/frontend/static/js/app.js) - 全局基础元数据加载解耦
- 在 `loadAll()` 方法中：
  - **移除** `if (visibleMenus.value.has('instances')) loadSavedConnections();` 的前置条件判断；
  - **变更为**：无条件执行 `loadSavedConnections()`，确保所有登录用户在首帧即可持有完整的实例只读下拉列表。

---

### 4.2 后端鉴权层改造

#### (1) [`backend/services/auth_service.py`](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/backend/services/auth_service.py) - RBAC 规则读写分离
- **只读端点解耦**：
  - 从 `_PATH_TO_MENU` 字典中将 `"/api/v1/tdsql/connections"` 调整为全局基础只读服务（类似 `"/api/v1/config"` 与 `"/api/v1/admin/logo"`），允许所有已认证登录用户（`method in _READ_METHODS`）读取；
  - 或者在二级权限校验中，对 `GET /api/v1/tdsql/connections` 判定为只要登录有效即放行（密码已在 SQL 过滤层与 `registry.list_saved()` 中脱敏）。
- **写端点白名单严格收敛**：
  - 在 `check_permission` 中，明确将以下端点纳入仅允许 `admin` 与 `dba` 角色的写白名单：
    - `POST /api/v1/tdsql/connections`（创建连接）
    - `PUT /api/v1/tdsql/connections/*`（修改连接）
    - `DELETE /api/v1/tdsql/connections/*`（删除连接）
    - `POST /api/v1/tdsql/connections/*/set-default`（设为默认）
    - `POST /api/v1/tdsql/connections/*/connect`（激活连接）
    - `POST /api/v1/tdsql/connect*`（临时直连）
    - `POST /api/v1/tdsql/discover*`（ZK自动发现扫描）
    - `POST /api/v1/tdsql/import-commit`（ZK批量导入登记）
    - `POST /api/v1/tdsql/import-preview`（ZK导入预览生成）
  - 任何非 `admin`/`dba` 角色（包括已分配 `instances` 菜单的普通角色）向上述写端点发送请求时，一律被中间件硬性拦截并返回 `403 Forbidden`。

---

## 5. 影响面评估与安全性保证

1. **兼容性与回归风险**：
   - **对现有审核分析业务完全无破坏性**：未修改底层连接池逻辑、SQL 审核解析器或任务扫描器；
   - **全面提升系统鲁棒性**：消除了开发人员/审计员因菜单配置不当而导致误删生产数据库连接的重大安全隐患；消除了普通业务人员在审核页面因下拉框为空而无法选择实例的可用性阻断。
2. **安全性指标**：
   - 满足金融级系统“**权限最小化**”、“**职责分离（SoD）**”与“**数据只读脱敏**”合规要求。

---

## 6. 测试与验收方案

| 编号 | 测试场景 | 操作角色 | 预期结果 |
|---|---|---|---|
| **TC-01** | 管理员/DBA 访问实例管理 | `admin` / `dba` | 正常可见“+新建连接”、“ZK自动发现”、“编辑”、“删除”，功能完全可用 |
| **TC-02** | 普通角色已分配“实例管理” | `developer` / `auditor` / 自定义角色 | 能够进入“实例管理”查看列表与形态，**无任何新建、编辑、删除、导入按钮** |
| **TC-03** | 普通角色尝试调用实例写 API | `developer` (携带合法Token) | 直接 POST/DELETE `/api/v1/tdsql/connections` 接口，后端强制返回 `403 Forbidden` |
| **TC-04** | 普通角色未分配“实例管理”，访问即时审核/在线审核 | `developer` / `auditor` | 进入“在线元数据审核”页面，**实例下拉列表正常回显**，可正常选择实例并发起审核 |
| **TC-05** | 普通角色未分配“实例管理”，访问慢SQL任务/大表治理 | `developer` / `auditor` | 进入“扫描任务”或“大表治理”页面，**实例下拉列表正常回显**，业务功能完全畅通 |
