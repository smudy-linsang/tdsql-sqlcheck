# TDSQL-SQLCheck 实例管理权限收敛与全模块实例读取解耦详细设计说明书

| 文档版本 | 编写日期 | 状态 | 适用版本 | 评审依据 |
|---|---|---|---|---|
| **V3.0** | 2026-08-20 | **整改升级版 (Codex 评审整改通过)** | V1.6.1.7+ | 全面吸收智能体 A 与智能体 O（Codex）评审意见（`REVIEW-RBAC-*.md`） |

---

## 1. 背景与问题定义

在 TDSQL-SQLCheck 平台的权限管控体系中，经智能体 A 与智能体 O（Codex）两轮实测复核与代码静态分析，确认存在以下核心体验缺陷、元数据阻断及深层安全隐患：

### 1.1 问题一：实例管理写操作入口未做角色收敛（体验缺陷 + 纵深防御缺口）
- **现象复核**：当系统管理员将“平台治理 ➔ 实例管理（`instances`）”菜单分配给普通开发人员（`developer`）、审计员（`auditor`）或自定义角色后，非管理员用户进入“实例管理”页面时，**能够看到“+ 新建连接”、“从 ZK 自动发现”、“ZK发现配置”、“编辑连接”、“删除连接”、“设为默认”以及“连接”等全部管理按钮**。
- **虽然**由于后端内置角色方法权限的拦截，普通角色点击写操作会收到 403 错误（未发生实际数据篡改）；
- **但是**前端暴露写操作入口严重违背最小特权原则，且部分前端函数（如 `deleteConn`、`setDefaultConn`）此前缺失 `resp.ok` 判断，导致产生“操作成功”的虚假提示错觉。
- **治理要求**：
  1. 前端界面对所有写操作按钮严格根据角色收敛，仅对 `admin` 与 `dba` 渲染；普通角色进入“实例管理”时呈现纯只读表格，写按钮数量严格为 0；
  2. 修复前端请求处理逻辑，必须严格基于 `resp.ok` 响应判定；
  3. 后端对所有实例管理写操作和有副作用的探测接口建立端点级与中间件双保险防护。

### 1.2 问题二：实例基础元数据读权限与管理菜单过度耦合（功能可用性阻断）
- **现象复核**：如果不给普通角色分配“实例管理（`instances`）”菜单，该用户在访问其具备权限的业务模块（“即时审核”、“在线元数据审核”、“慢SQL扫描任务”、“大表治理”、“EXPLAIN分析”、“上线检查”、“深度诊断”等）时，页面顶部或表单中用于选择目标数据库实例的**下拉列表全部为空**，导致所有业务功能无法选定实例执行。
- **治理要求**：**实例下拉选择所需的精简基础元数据（8 字段非敏感信息）属于平台各业务模块正常运转所必需的“全局只读上下文”；必须通过专用精简端点与全字段管理端点彻底解耦，无论是否分配“实例管理”菜单，合法登录用户均可获取实例下拉列表。**

### 1.3 伴生深层安全隐患（P0/P1 修订项）
1. **MonitorDB 密文泄露风险（P0）**：底层 `connection_registry.list_saved()` 原先仅移除了 `password_encrypted`，未移除 `monitor_password_encrypted`。必须在底层彻底修复双密文剔除，杜绝信息越级披露；
2. **有副作用 GET 接口越权探测（P0）**：`GET /connections/{id}/probe` 会实际发起对内网 MonitorDB 的网络连接；`GET /test-connection` 通过 URL 查询参数传递敏感口令。若分配 `instances` 菜单给普通角色，由于其为 `GET` 方法，原先会被 RBAC 误放行。必须收敛为仅限 `admin/dba` 并停用 GET 版连接测试；
3. **父级前缀误拦截（P1）**：`"/api/v1/tdsql/connections"` 映射到 `"instances"` 菜单，新增的 `/connections/options` 若无显式机制，会被父前缀匹配拦截。必须增加已认证菜单无关端点（`_MENU_INDEPENDENT_READ_ENDPOINTS`）机制。

---

## 2. 总体架构与设计方案

本设计遵循 **“精简只读全局放行、管理全量受控隔离、前后状态严格正交、端点权限分级收敛”** 原则。

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
  │ 载荷：严格 8 字段 Allowlist：                 │                   │  - 20 个实例管理与治理端点                    │
  │      id, name, host, port, database,         │                   │ 权限分级：                                    │
  │      effective_instance_type,                │                   │  - 全字段读：拥有 instances 菜单的角色        │
  │      is_default, active                      │                   │  - 常规管理(增删改/ZK导入)：admin / dba      │
  │ 前端状态：connectionOptions (专用状态)        │                   │  - 高危治理(类型锁定/ZK配置)：仅限 admin      │
  │ 用途：支撑全平台 18 处业务下拉选择框          │                   │ 前端状态：managedConnections (专用状态)       │
  └──────────────────────────────────────────────┘                   └──────────────────────────────────────────────┘
```

---

## 3. 详细分层权限矩阵 (Tiered RBAC Matrix)

| 分层 | 端点 / 能力 | HTTP 方法与路径 | 允许角色 | 菜单依赖 | 异常响应 |
|---|---|---|---|---|---|
| **L1: 全局基础只读** | 实例选择精简选项 (8字段) | `GET /api/v1/tdsql/connections/options` | 任意已认证登录角色 | 无需菜单 | 未登录 401 |
| **L2: 全字段管理读** | 实例管理完整信息列表 | `GET /api/v1/tdsql/connections` | `admin` / `dba` / 获授权角色 | 需 `instances` 菜单 | 403 Forbidden |
| **L3: 常规实例管理** | 新建实例连接 | `POST /api/v1/tdsql/connections` | `admin` / `dba` | 需 `instances` 菜单 | 403 Forbidden |
| | 更新实例连接 | `PUT /api/v1/tdsql/connections/{id}` | `admin` / `dba` | 需 `instances` 菜单 | 403 Forbidden |
| | 删除实例连接 | `DELETE /api/v1/tdsql/connections/{id}` | `admin` / `dba` | 需 `instances` 菜单 | 403 Forbidden |
| | 设为默认连接 / 别名 | `POST /connections/{id}/set-default` 或 `/default` | `admin` / `dba` | 需 `instances` 菜单 | 403 Forbidden |
| | 显式激活 / 断开连接 | `POST /connections/{id}/connect` 或 `/disconnect` | `admin` / `dba` | 需 `instances` 菜单 | 403 Forbidden |
| | 实例类型多源探测 / 诊断 | `POST /connections/{id}/probe-instance-type` 或 `probe-diagnostics` | `admin` / `dba` | 需 `instances` 菜单 | 403 Forbidden |
| | MonitorDB 连通性探测 | `POST /connections/{id}/monitor-probe` | `admin` / `dba` | 需 `instances` 菜单 | 403 Forbidden |
| | MonitorDB 状态探测(有副作用GET) | `GET /connections/{id}/probe` | `admin` / `dba` | 需 `instances` 菜单 | 403 Forbidden |
| | POST 测试连接 | `POST /api/v1/tdsql/test-connection` | `admin` / `dba` | 需 `instances` 菜单 | 403 Forbidden |
| | 直接连接 / 配置文件连接 | `POST /api/v1/tdsql/connect` 或 `connect-from-config` | `admin` / `dba` | 需 `instances` 菜单 | 403 Forbidden |
| | ZK 扫描 / 诊断 / 导入 | `POST /api/v1/tdsql/discover`、`name-diagnose`、`import-preview`、`import-commit` | `admin` / `dba` | 需 `instances` 菜单 | 403 Forbidden |
| **L4: 高危系统治理** | 实例类型锁定 / 解锁 | `PUT /api/v1/tdsql/connections/{id}/instance-type-lock` | **仅限 `admin`** | 需 `instances` 菜单 | 403 Forbidden |
| | ZK 自动发现全局配置维护 | `GET /discover/config`、`PUT /discover/config` | **仅限 `admin`** | 需 `instances` 菜单 | 403 Forbidden |
| **L5: 废弃端点** | 历史 ZK 注册路由 | `POST /api/v1/tdsql/discover/register` | `admin` / `dba` | 需 `instances` 菜单 | **固定 410 Gone** |

*(注：业务模块调用实例执行审核、大表采集或慢日志抓取，由系统底层 `registry.get(conn_id, auto_connect=True)` 自动完成建连，不依赖用户具备显式连接管理权限)*

---

## 4. 接口与代码级改造规范

### 4.1 后端服务层改造

#### 1. 修复 `list_saved()` 双密文脱敏（[`backend/services/connection_registry.py`](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/backend/services/connection_registry.py)）
```python
d = dict(r)
# 彻底移除业务口令密文与监控口令密文，杜绝信息泄露
d.pop("password_encrypted", None)
d.pop("monitor_password_encrypted", None)
d["password"] = "***"
d["monitor_password"] = "***" if d.get("monitor_user") else ""
```

#### 2. 新增精简 SQL 投影端点（[`backend/api/tdsql_manage.py`](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/backend/api/tdsql_manage.py)）
```python
ALLOWED_OPTION_FIELDS = {
    "id", "name", "host", "port", "database",
    "effective_instance_type", "is_default", "active"
}

@router.get("/connections/options", summary="获取实例下拉选择精简列表（全角色可用）")
def get_connection_options():
    """
    供全平台各业务模块下拉框使用的精简实例列表。
    仅返回 8 个必要字段，严格执行白名单过滤。
    """
    connections = registry.list_saved()
    options = []
    default_id = None
    for c in connections:
        if c.get("is_default"):
            default_id = c.get("id")
        # 严格过滤字段，杜绝任何敏感账号、描述与口令
        item = {k: c.get(k) for k in ALLOWED_OPTION_FIELDS if k in c}
        item["is_default"] = bool(c.get("is_default"))
        item["active"] = bool(c.get("active"))
        options.append(item)
    return {
        "connections": options,
        "default": default_id,
    }
```

#### 3. 停用 GET 版 `test-connection` 并加固端点校验（[`backend/api/tdsql_manage.py`](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/backend/api/tdsql_manage.py)）
- 移除 `@router.get("/test-connection")`，仅保留 `@router.post("/test-connection")`；
- 在 `POST /test-connection` 及 `GET /connections/{id}/probe` 中加入端点级 `_require_instance_manager(request)`。

#### 4. RBAC 中间件精确绕过父级前缀（[`backend/services/auth_service.py`](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/backend/services/auth_service.py)）
```python
# 与菜单解耦的已认证只读端点集合（要求合法 Token，非 PUBLIC_PATHS）
_MENU_INDEPENDENT_READ_ENDPOINTS = {
    ("GET", "/api/v1/tdsql/connections/options"),
}

def check_permission(role: str, method: str, path: str) -> bool:
    method = method.upper()
    ...
    # 若命中已认证独立只读端点，只要已成功登录直接放行
    if (method, path) in _MENU_INDEPENDENT_READ_ENDPOINTS:
        return True
    ...
```

---

### 4.2 前端表现层与状态管理改造

#### 1. 前端状态解耦（[`frontend/static/js/app.js`](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/frontend/static/js/app.js)）
彻底拆分选择器状态与管理状态，消除并发覆盖：
```javascript
const connectionOptions = ref([]);   // 专供全平台 18 处业务下拉框（审核、扫描、大表等）
const managedConnections = ref([]);  // 专供“实例管理”页面表格、筛选、分页与抽屉

const loadConnectionOptions = async () => {
  try {
    const resp = await apiFetch(`${API_BASE}/api/v1/tdsql/connections/options`);
    if (resp.ok) {
      const d = await resp.json();
      connectionOptions.value = d.connections || [];
      if (d.default && !currentConnectionId.value) currentConnectionId.value = d.default;
    }
  } catch (e) {}
};

const loadManagedConnections = async () => {
  connLoading.value = true;
  try {
    const resp = await apiFetch(`${API_BASE}/api/v1/tdsql/connections`);
    if (resp.ok) {
      const d = await resp.json();
      managedConnections.value = d.connections || [];
    }
  } catch (e) {} finally {
    connLoading.value = false;
  }
};
```

#### 2. 生命周期与响应判定优化（[`frontend/static/js/app.js`](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/frontend/static/js/app.js)）
- 在 `loadAll()` 中**无条件调用 `loadConnectionOptions()`**；
- 仅在进入 `instances` 页面时调用 `loadManagedConnections()`；
- `deleteConn()` 与 `setDefaultConn()` 增加 `if (resp.ok)` 检查，只在 2xx 响应时提示成功。

#### 3. 按钮级权限门禁收敛（[`frontend/index.html`](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/frontend/index.html)）
- 顶部按钮：`从 ZK 自动发现`、`+ 新建连接` 添加 `v-if="canManageInstances"`；
- 表格操作列：`连接`、`设为默认`、`编辑`、`删除` 添加 `v-if="canManageInstances"`；
- 下拉选择器统一绑定 `connectionOptions`，实例管理表格绑定 `pagedManagedConnections`。

---

## 5. 角色与权限矩阵对照

| 角色类型 | 是否分配“实例管理”菜单 | 进入“实例管理”页面表现 | 业务模块（审核/扫描/大表）实例下拉框 | 能否新建/编辑/删除/导入实例 | 能否锁定实例类型 / 修改ZK配置 |
|---|---|---|---|---|---|
| **系统管理员 (admin)** | 必须可见 | **完整管理态**：可见全部按钮 | 正常展示全部实例 | ✅ **允许** | ✅ **允许** |
| **数据库管理员 (dba)** | 必须可见 | **常规管理态**：可见新建/编辑/删除等 | 正常展示全部实例 | ✅ **允许** | ❌ **禁止 (403)** |
| **开发人员 (developer)** | **已分配** | **纯只读态**：展示列表，**写按钮数为0** | 正常展示全部实例 | ❌ **禁止 (403)** | ❌ **禁止 (403)** |
| **开发人员 (developer)** | **未分配** | 菜单不可见，无法进入 | **正常展示全部实例** (通过 /options) | ❌ **禁止 (403)** | ❌ **禁止 (403)** |
| **审计员 (auditor)** | **已分配** | **纯只读态**：展示列表，**写按钮数为0** | 正常展示全部实例 | ❌ **禁止 (403)** | ❌ **禁止 (403)** |
| **审计员 (auditor)** | **未分配** | 菜单不可见，无法进入 | **正常展示全部实例** (通过 /options) | ❌ **禁止 (403)** | ❌ **禁止 (403)** |
| **自定义角色 (custom)** | **已分配** | **纯只读态**：展示列表，**写按钮数为0** | 正常展示全部实例 | ❌ **禁止 (403)** | ❌ **禁止 (403)** |
| **自定义角色 (custom)** | **未分配** | 菜单不可见，无法进入 | **正常展示全部实例** (通过 /options) | ❌ **禁止 (403)** | ❌ **禁止 (403)** |

---

## 6. 全量测试与验收方案

### 6.1 后端权限与安全测试

| 编号 | 测试场景 | 预期断言 |
|---|---|---|
| **RBAC-01** | 无 Token 或过期 Token 请求 `/connections/options` | 401 Unauthorized |
| **RBAC-02** | 5 类角色在未分配 `instances` 菜单时请求 `/connections/options` | 全部 200 OK |
| **RBAC-03** | 普通角色在分配 `instances` 菜单时请求 `/connections` 全字段列表 | 200 OK，只读 |
| **RBAC-04** | 普通角色无论有无 `instances` 菜单，调用实例创建、修改、删除 | 全部 403 Forbidden，数据库零变化 |
| **RBAC-05** | 普通角色无论有无 `instances` 菜单，调用 `POST /test-connection` | 403 Forbidden，`GET /test-connection` 返回 405/404 |
| **RBAC-06** | 普通角色无论有无 `instances` 菜单，调用 `GET /connections/{id}/probe` | 403 Forbidden，服务端未发起外部连接 |
| **RBAC-07** | DBA 角色调用常规实例 CRUD、连接、ZK 导入 | 全部正常放行 200 OK |
| **RBAC-08** | DBA 角色调用 `instance-type-lock` 或 `PUT /discover/config` | 403 Forbidden (保持 admin 独占) |
| **RBAC-09** | DBA 角色调用废弃 `POST /discover/register` | 返回 410 Gone (非 200/403) |
| **DATA-01** | 检查 `/connections/options` 响应字段 | Key 集合严格等于 8 字段 Allowlist |
| **DATA-02** | 检查 `/connections/options` 完整 JSON 载荷 | 不含任何口令、密文、账号或描述字段 |
| **DATA-03** | 检查 `/connections` 完整 JSON 载荷 | 不含 `password_encrypted` 或 `monitor_password_encrypted` |

### 6.2 前端交互与状态回归测试

| 编号 | 测试场景 | 预期断言 |
|---|---|---|
| **UI-01** | 普通角色获授 `instances` 菜单进入实例管理 | 页面写操作按钮总数为 0，表格完整可读 |
| **UI-02** | 普通角色未获授 `instances` 菜单进入各业务模块 | 全平台 18 处实例下拉选择器均完整回显 |
| **UI-03** | `connectionOptions` 与 `managedConnections` 并发/乱序返回 | 状态互不覆盖，实例管理各字段完整 |
| **UI-04** | 删除或设默认接口返回 403 时 | 前端不提示“已删除/已设为默认”，展示错误提示 |
| **UI-05** | 未激活实例在各业务模块被选中使用 | 业务调用通过底层自动建连正常执行 |
