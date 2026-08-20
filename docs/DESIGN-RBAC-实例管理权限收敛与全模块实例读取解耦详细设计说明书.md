# TDSQL-SQLCheck 实例管理权限收敛与全模块实例读取解耦详细设计说明书

| 文档版本 | 编写日期 | 状态 | 适用版本 | 评审依据 |
|---|---|---|---|---|
| **V2.0** | 2026-08-20 | **评审修订版 (Approved by Review)** | V1.6.1.7+ | 吸收智能体 A 评审意见（`REVIEW-RBAC-实例管理权限收敛设计评审_A.md`） |

---

## 1. 背景与问题定义

在 TDSQL-SQLCheck 平台的权限管控体系中，经智能体 A 实测核对与多角色交叉复核，确认存在以下体验缺陷、元数据阻断及数据分级安全隐患：

### 1.1 问题一：实例管理写操作入口未做角色收敛（体验缺陷 + 纵深防御缺口）
- **现象描述**：当系统管理员将“平台治理 ➔ 实例管理（`instances`）”菜单分配给普通开发人员（`developer`）、审计员（`auditor`）或自定义角色后，非管理员用户进入“实例管理”页面时，**能够看到“+ 新建连接”、“从 ZK 自动发现”、“ZK发现配置”、“编辑连接”、“删除连接”、“设为默认”以及“连接”等全部管理按钮**。虽然由于后端 RBAC 的拦截，普通用户点击这些写操作后会弹出 403 失败提示（未造成实际越权），但前端入口暴露严重破坏了用户体验与界面规范。
- **治理要求**：**前端界面必须遵循最小特权原则，仅对“系统管理员（`admin`）”和“数据库管理员（`dba`）”渲染写操作按钮；普通角色被分配该菜单时，页面必须呈现纯只读浏览态，彻底隐藏所有新增、修改、删除及导入入口。同时后端对全部 20 个写端点保持中间件与端点级双保险硬性拦截。**

### 1.2 问题二：实例基础元数据读权限与管理菜单过度耦合（功能可用性阻断）
- **现象描述**：如果不给普通角色分配“实例管理（`instances`）”菜单，该用户在访问其具备权限的业务模块（例如“即时审核”、“在线元数据审核”、“慢SQL扫描任务”、“大表治理”、“EXPLAIN分析”、“上线检查”、“深度诊断”等）时，页面顶部或表单中用于选择目标数据库实例的**下拉列表全部为空**，导致所有需要依赖实例上下文的业务功能完全不可用。
- **治理要求**：**实例下拉选择所需的精简基础元数据（连接名称、IP、端口、数据库名、实例形态等）属于平台各业务模块正常运转所必需的“全局只读上下文”；无论用户角色是否被授予了“实例管理”管理菜单，只要是合法登录用户，在访问具体业务模块时都必须能够正常加载实例下拉列表。**

### 1.3 伴生安全隐患：列表响应中 MonitorDB 密文泄露风险（P0 修订项）
- **根因核实**：现有 `connection_registry.list_saved()` 在返回全量连接列表时，仅剔除了 `password_encrypted`，未剔除 `monitor_password_encrypted`。若直接将现有的 `GET /api/v1/tdsql/connections` 全局放开，会导致监控口令密文被广播给所有普通角色。
- **治理要求**：**必须通过“新增专用精简只读端点（`/options`） + 底层密文脱敏修复”双重措施，仅向全角色披露下拉框所需的 6 个最小非敏感字段，全字段管理端点保持与 `instances` 菜单绑定，杜绝任何敏感口令泄露。**

---

## 2. 总体架构与详细设计（方案 B：精简只读端点解耦）

本设计遵循 **“精简只读全局放行、管理端点严格受控、边界消歧防御纵深”** 的银行级安全架构。

```
                                  ┌─────────────────────────────────────────┐
                                  │             已认证登录用户               │
                                  └────────────────────┬────────────────────┘
                                                       │
                     ┌─────────────────────────────────┴─────────────────────────────────┐
                     ▼                                                                   ▼
       【精简下拉元数据 (GET /options)】                                     【实例管理全字段与写操作】
  ┌──────────────────────────────────────────────┐                   ┌──────────────────────────────────────────────┐
  │ 接口：GET /api/v1/tdsql/connections/options  │                   │ 接口：                                        │
  │ 权限：合法 Token 已登录全角色即可访问          │                   │  - GET /api/v1/tdsql/connections (全字段管理)│
  │ 载荷：仅包含 6 个核心下拉展示字段：            │                   │  - 20 个写操作端点 (POST/PUT/DELETE)          │
  │      id, name, host, port, database,         │                   │ 权限：                                        │
  │      effective_instance_type,                │                   │  - 全字段读：需拥有 instances 菜单           │
  │      is_default, active                      │                   │  - 写端点：仅限 admin / dba 角色            │
  │ 用途：支撑审核、扫描、大表、诊断等下拉框数据   │                   │ 表现：非 admin/dba 隐藏全部写按钮，API 返回 403│
  └──────────────────────────────────────────────┘                   └──────────────────────────────────────────────┘
```

---

## 3. 接口与代码级改造清单

### 3.1 后端服务层改造

#### 1. 底层密文投影彻底修复（[`backend/services/connection_registry.py`](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/backend/services/connection_registry.py)）
在 `list_saved()` 中，确保剔除所有加密口令字段：
```python
d = dict(r)
d.pop("password_encrypted", None)
d.pop("monitor_password_encrypted", None)  # P0 修复：彻底杜绝监控口令密文泄露
d["password"] = "***"
d["monitor_password"] = "***" if d.get("monitor_user") else ""
```

#### 2. 新增精简下拉选项端点（[`backend/api/tdsql_manage.py`](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/backend/api/tdsql_manage.py)）
新增 `GET /api/v1/tdsql/connections/options` 路由：
```python
@router.get("/connections/options", summary="获取实例下拉选择精简列表（全角色可用）")
def get_connection_options():
    """
    供全平台业务模块下拉框使用的精简实例列表。
    仅返回非敏感展示字段，任何合法登录用户均可访问。
    """
    connections = registry.list_saved()
    options = []
    default_id = None
    for c in connections:
        if c.get("is_default"):
            default_id = c.get("id")
        options.append({
            "id": c.get("id"),
            "name": c.get("name"),
            "host": c.get("host"),
            "port": c.get("port"),
            "database": c.get("database"),
            "effective_instance_type": c.get("effective_instance_type", "distributed"),
            "is_default": bool(c.get("is_default")),
            "active": bool(c.get("active")),
        })
    return {
        "connections": options,
        "default": default_id,
    }
```

#### 3. RBAC 鉴权与前缀消歧配置（[`backend/services/auth_service.py`](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/backend/services/auth_service.py)）
- **只读放行**：将 `"/api/v1/tdsql/connections/options"` 声明为已登录用户基础只读端点（不绑定单一菜单，`method in _READ_METHODS` 且 Token 有效直接放行）；
- **全字段列表**：`"/api/v1/tdsql/connections"` 保持映射到 `"instances"` 菜单；
- **边界消歧**：严格沿用现有的 `path == prefix or path.startswith(prefix + "/")` 边界匹配语义，绝不使用含糊的朴素通配，防止 `/connect` 误伤 `/connections`。

#### 4. 全量 20 个实例写端点白名单严格收敛清单
在 `auth_service.py` 及相关 API 层，明确以下 20 个写操作端点**仅限 `admin` 与 `dba` 角色**访问：

| 序号 | HTTP 方法 | 完整路由路径 | 功能说明 |
|---|---|---|---|
| 1 | `POST` | `/api/v1/tdsql/connections` | 创建连接 |
| 2 | `PUT` | `/api/v1/tdsql/connections/{id}` | 更新连接 |
| 3 | `DELETE` | `/api/v1/tdsql/connections/{id}` | 删除连接 |
| 4 | `POST` | `/api/v1/tdsql/connections/{id}/set-default` | 设为默认连接 |
| 5 | `POST` | `/api/v1/tdsql/connections/{id}/default` | 设为默认连接（别名路由） |
| 6 | `POST` | `/api/v1/tdsql/connections/{id}/connect` | 激活连接 |
| 7 | `POST` | `/api/v1/tdsql/connections/{id}/probe-instance-type` | 探测实例类型 |
| 8 | `PUT` | `/api/v1/tdsql/connections/{id}/instance-type-lock` | 锁定实例类型 |
| 9 | `POST` | `/api/v1/tdsql/connections/{id}/probe-diagnostics` | 实例多源判定诊断 |
| 10 | `POST` | `/api/v1/tdsql/connections/{id}/monitor-probe` | 测试 MonitorDB 连通性 |
| 11 | `POST` | `/api/v1/tdsql/connect` | 直接连接实例 |
| 12 | `POST` | `/api/v1/tdsql/connect-from-config` | 使用配置文件连接 |
| 13 | `POST` | `/api/v1/tdsql/test-connection` | 测试连接 |
| 14 | `POST` | `/api/v1/tdsql/disconnect` | 断开连接 |
| 15 | `PUT` | `/api/v1/tdsql/discover/config` | 保存 ZK 自动发现配置 |
| 16 | `POST` | `/api/v1/tdsql/discover` | 执行 ZK 自动发现扫描 |
| 17 | `POST` | `/api/v1/tdsql/discover/name-diagnose` | ZK 实例名解析诊断 |
| 18 | `POST` | `/api/v1/tdsql/discover/import-preview` | ZK 导入生成预览 |
| 19 | `POST` | `/api/v1/tdsql/discover/import-commit` | ZK 批量导入登记 |
| 20 | `POST` | `/api/v1/tdsql/discover/register` | ZK 导入登记（历史路由，410） |

*(注：`GET /api/v1/tdsql/connections/{id}/probe` 保持与 `instances` 菜单绑定，不向普通角色开放，杜绝内网非授权探测)*

---

### 3.2 前端表现层改造

#### 1. 按钮级权限门禁收敛（[`frontend/index.html`](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/frontend/index.html)）
- 顶部操作区：
  - `<el-button v-if="canManageInstances" type="success" ... @click="openZkDiscovery">从 ZK 自动发现</el-button>`
  - `<el-button v-if="canManageInstances" type="primary" ... @click="openNewConn">+ 新建连接</el-button>`
  - *(ZK发现配置已有 `v-if="isAdmin"` 保持不变)*
- 表格操作列：
  - `<el-button v-if="canManageInstances" link size="small" @click="connectInstance(row)">连接</el-button>`
  - `<el-button v-if="canManageInstances" link size="small" @click="setDefaultConn(row)">设为默认</el-button>`
  - `<el-button v-if="canManageInstances" link type="primary" size="small" @click="openEditConn(row)">编辑</el-button>`
  - `<el-button v-if="canManageInstances" link type="danger" size="small" @click="deleteConn(row)">删除</el-button>`
  - *(前端隐藏为体验层优化，鉴权以后端 RBAC 强制拦截为准)*

#### 2. 全局基础元数据加载优化（[`frontend/static/js/app.js`](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/frontend/static/js/app.js)）
- 新增 `loadConnectionOptions()`：调用 `GET /api/v1/tdsql/connections/options` 并填充 `savedConnections`，供全平台 18 处业务下拉框（审核、扫描、大表等）无条件消费；
- 在 `loadAll()` 中无条件执行 `loadConnectionOptions()`，无论当前用户拥有何种菜单权限，首屏均具备实例下拉数据；
- 在进入 `instances` 页面时（`watch(currentPage)` 或分配了该菜单的角色），再按需调用完整版 `loadSavedConnections()` 刷新管理表格，避免重复拉取。

---

## 4. 角色与权限矩阵设计对照表

| 角色类型 | 是否分配“实例管理”菜单 | 进入“实例管理”页面表现 | 业务模块（审核/扫描/大表）实例下拉框 | 能否新建/编辑/删除/导入实例 |
|---|---|---|---|---|
| **系统管理员 (admin)** | 必须可见 | **完整管理态**：可见全部操作按钮 | 正常展示全部实例 | ✅ **允许** |
| **数据库管理员 (dba)** | 必须可见 | **完整管理态**：可见全部操作按钮 | 正常展示全部实例 | ✅ **允许** |
| **开发人员 (developer)** | **已分配** | **纯只读态**：展示列表，**完全隐藏**所有新建/编辑/删除/ZK按钮 | 正常展示全部实例 | ❌ **严格禁止** (按钮隐藏 + API 403) |
| **开发人员 (developer)** | **未分配** | 菜单不可见，无法进入该页面 | **正常展示全部实例** (通过 /options 端点加载) | ❌ **严格禁止** |
| **审计员 (auditor)** | **已分配** | **纯只读态**：展示列表，**完全隐藏**所有新建/编辑/删除/ZK按钮 | 正常展示全部实例 | ❌ **严格禁止** (按钮隐藏 + API 403) |
| **审计员 (auditor)** | **未分配** | 菜单不可见，无法进入该页面 | **正常展示全部实例** (通过 /options 端点加载) | ❌ **严格禁止** |
| **自定义角色 (custom)** | **已分配** | **纯只读态**：展示列表，**完全隐藏**所有新建/编辑/删除/ZK按钮 | 正常展示全部实例 | ❌ **严格禁止** (按钮隐藏 + API 403) |
| **自定义角色 (custom)** | **未分配** | 菜单不可见，无法进入该页面 | **正常展示全部实例** (通过 /options 端点加载) | ❌ **严格禁止** |

---

## 5. 信息披露范围与安全合规评估

1. **信息披露范围的精确收敛**：
   - 采用方案 B 后，未被授予“实例管理”菜单的普通角色通过 `GET /connections/options` **仅能看到 6 个最小必要字段**：`id`, `name`, `host`, `port`, `database`, `effective_instance_type`, `is_default`, `active`；
   - **完全不返回**：`username`, `password`, `monitor_host`, `monitor_port`, `monitor_user`, `monitor_password`, `description` 等敏感连接属性与管控账号；
   - 彻底消除了信息过度披露风险，满足银行内部安全审计要求。
2. **零副作用保证**：
   - 不修改底层数据库表结构与 TDSQL 执行引擎，全量测试套件保持 100% 兼容通过。

---

## 6. 完整测试与验收方案（含正向与反向鉴别）

| 编号 | 测试场景 | 操作角色 | 预期结果 |
|---|---|---|---|
| **TC-01** | 管理员/DBA 访问实例管理 | `admin` / `dba` | 正常可见“+新建连接”、“ZK自动发现”、“编辑”、“删除”，全部管理功能正常 |
| **TC-02** | 普通角色已分配“实例管理” | `developer` / `auditor` / 自定义角色 | 能够进入“实例管理”查看列表，**写操作按钮数为 0**，界面纯只读 |
| **TC-03** | 普通角色未分配“实例管理”，访问在线审核/扫描任务 | `developer` / `auditor` | 进入“在线元数据审核”与“扫描任务”页面，**实例下拉列表完整展示**，业务功能完全畅通 |
| **TC-04** | 未登录 / Token 过期请求 `/connections/options` | 未认证用户 | **强制返回 401 Unauthorized**（杜绝无鉴权公开暴露） |
| **TC-05** | 检查 `/connections/options` 响应体字段 | 任何角色 | **严禁包含任何 `*password*`、`*secret*`、`monitor_*` 字段** |
| **TC-06** | 普通角色请求 `GET /connections/{id}/probe` | `developer` / `auditor` | **返回 403 Forbidden**（杜绝有副作用的 GET 被非授权调用） |
| **TC-07** | 普通角色调用 **全量 20 个写操作端点** | `developer` / `auditor` / 自定义角色 | **逐一返回 403 Forbidden**，无任何遗漏 |
| **TC-08** | DBA 角色调用 **全量 20 个写操作端点** | `dba` | **全部正常放行**（防止权限白名单误伤 DBA） |
| **TC-09** | 运行 RBAC 全量自动化测试套件 | 自动化执行 | `test_rbac_path_coverage.py` 与 `test_v2_rbac_matrix.py` 扩展后 **100% PASS** |
