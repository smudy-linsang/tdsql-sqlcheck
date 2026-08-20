# 《实例管理权限收敛与全模块实例读取解耦》设计评审意见（Codex）

| 项目 | 内容 |
|---|---|
| 评审对象 | `docs/DESIGN-RBAC-实例管理权限收敛与全模块实例读取解耦详细设计说明书.md` V2.0 |
| 评审基线 | `origin/main@ae1114a9ad001f0dcc4c10fa6a8ac254b59b75ed` |
| 评审日期 | 2026-08-20 |
| 评审范围 | RBAC 后端强制鉴权、实例只读数据解耦、前端只读态、现有业务兼容性、验收方案 |
| 评审结论 | **有条件通过；当前 V2.0 不得直接进入实施或标记为最终批准版** |

---

## 1. 结论摘要

V2.0 采用“新增精简只读 `/connections/options` 端点、管理端点继续受控、前端隐藏非管理角色写按钮”的总体方向正确，能够解决以下两个核心诉求：

1. 只有系统管理员和数据库管理员能够新建、编辑、删除和导入实例，其他角色在“实例管理”页面只能查看；
2. 未分配“实例管理”菜单的合法用户，仍能在在线元数据审核、扫描任务、大表治理、EXPLAIN、上线检查和深度诊断等业务模块正常选择实例。

但当前设计仍存在 **1 项 P0、3 项 P1、2 项 P2**。其中最关键的问题是：设计认为普通角色调用有副作用的 GET 探测接口会被拒绝，但按现有 RBAC 实际会被放行；同时设计将两个现有 admin 独占能力错误扩大为 DBA 可用。若照文档直接实施，将出现新的安全边界错误和前端状态覆盖风险。

完成本文第 8 节全部准入项后，可进入开发。

---

## 2. 当前问题性质复核

### 2.1 新建、删除的后端数据权限当前已被拦截

在 `AUTH_ENABLED=true` 且使用当前 `auth_service.check_permission()` 的前提下，即使给 `developer` 分配 `instances` 菜单：

```text
POST   /api/v1/tdsql/connections       -> False
DELETE /api/v1/tdsql/connections/{id}  -> False
```

原因是内置角色的一级方法权限先于菜单权限生效：

- `dba`：允许读写，再检查菜单；
- `developer`：只允许指定开发自助写接口，实例写接口不在其中；
- `auditor`：只允许读；
- 自定义角色：默认只允许读。

因此，当前已核实的问题一主要是：

1. 普通角色能看见新建、删除、连接、设默认、编辑、ZK 导入等管理按钮；
2. 用户点击后得到 403 或产生错误反馈；
3. `deleteConn()`、`setDefaultConn()` 未检查 `resp.ok`，后端即使返回 403，前端也可能提示“已删除”或“已设为默认”，进一步造成已经越权成功的错觉。

这不降低整改优先级。前端只读态必须修复，后端也必须增加端点级纵深保护，防止未来角色方法矩阵调整后产生真实越权。

### 2.2 实例下拉为空的根因确认无误

当前 `loadAll()` 仅在 `visibleMenus` 包含 `instances` 时调用 `loadSavedConnections()`。全平台业务下拉框共同消费 `savedConnections`，所以移除实例管理菜单后，下拉数据不会加载。

新增已认证全角色可读的精简 options 端点，是正确的解耦方向。

### 2.3 MonitorDB 密文泄露风险确认无误

当前 `connection_registry.list_saved()` 使用 `SELECT c.*`，仅移除 `password_encrypted`，没有移除 `monitor_password_encrypted`。V2.0 提出的底层脱敏修复必须保留；未经修复不得扩大任何实例列表读取范围。

---

## 3. 阻断问题清单

### R01【P0】两个有副作用的 GET 接口仍会向获授 `instances` 的普通角色开放

#### 设计中的错误前提

V2.0 第 128 行写明：

> `GET /api/v1/tdsql/connections/{id}/probe` 保持与 `instances` 菜单绑定，不向普通角色开放。

但现有 RBAC 对 `GET/HEAD/OPTIONS` 先认定为只读；当普通角色拥有 `instances` 菜单时，`/api/v1/tdsql/connections` 前缀匹配成功，因此该接口会被放行。

实际权限判定结果：

```text
developer + instances=1:
GET /api/v1/tdsql/connections/{id}/probe -> True
```

该接口会由服务端实际连接目标 MonitorDB，属于有副作用的内网探测，不是普通浏览读取。

#### 设计还遗漏了 GET 版连接测试接口

当前同时存在：

```text
GET  /api/v1/tdsql/test-connection
POST /api/v1/tdsql/test-connection
```

前端 `testConn()` 实际调用 GET 版本，并把业务库和 MonitorDB 的 host、port、username、password 放入 URL 查询参数。普通角色获得 `instances` 菜单后，该 GET 接口同样会通过 RBAC：

```text
developer + instances=1:
GET /api/v1/tdsql/test-connection -> True
```

这不仅允许普通角色驱动服务器探测指定内网地址，而且 URL 中的口令可能进入浏览器历史、Nginx/网关 access log 和监控日志。

#### 必须整改

1. `GET /connections/{id}/probe` 仅允许 `admin`、`dba`；
2. 停用或删除 `GET /test-connection`，前端统一改为 `POST + JSON body`；
3. `POST /test-connection` 仅允许 `admin`、`dba`；
4. 两个处理函数均增加端点级 `_require_instance_manager(request)`；
5. 自动化测试覆盖“普通角色有/无 instances 菜单”两种情况，均必须返回 403；
6. 不得把这两个接口加入全角色只读白名单。

---

### R02【P1】“DBA 放行全部 20 个端点”破坏现有更窄权限

V2.0 第 103 行将全部 20 个端点统一描述为 `admin`、`dba` 可用，TC-08 又要求 DBA 调用全部 20 个端点都正常放行。该规则与当前系统冲突：

| 能力 | 当前端点级权限 | 正确处理 |
|---|---|---|
| 锁定/解锁实例类型 | 仅 `admin` | 保持仅 `admin`，不可扩大给 DBA |
| 读取/保存 ZK 自动发现全局配置 | 仅 `admin` | 保持仅 `admin`，不可扩大给 DBA |
| ZK 历史 `/register` 路由 | 固定返回 410 | DBA 通过 RBAC 后仍应返回 410，不应期望 2xx |

用户提出“只有管理员和数据库管理员可以新建、删除实例”是对普通角色写权限的上限约束，不代表所有高风险治理操作必须同时授予 DBA。

#### 必须改成分层权限矩阵

| 分层 | 端点/能力 | 权限 |
|---|---|---|
| 全局基础只读 | `GET /connections/options` | 任意合法登录用户 |
| 管理全字段读 | `GET /connections` | 拥有 `instances` 菜单的角色 |
| 常规实例管理 | 新建、编辑、删除、设默认、显式连接/断开、常规探测、ZK 扫描与导入 | `admin`、`dba`，并保留菜单校验 |
| 高风险治理 | 实例类型锁定/解锁、ZK 全局配置维护 | 仅 `admin` |
| 废弃端点 | `POST /discover/register` | 通过鉴权后固定 410 |

TC-08 必须按端点预期分别断言，不得笼统要求 DBA 全部返回成功。

---

### R03【P1】`/connections/options` 不能仅靠“不绑定菜单”实现放行

现有 `_PATH_TO_MENU` 包含：

```python
"/api/v1/tdsql/connections": "instances"
```

匹配规则是：

```python
path == prefix or path.startswith(prefix + "/")
```

所以新路径 `/api/v1/tdsql/connections/options` 会被父前缀捕获。若只是不为 `/options` 新增映射，未分配 `instances` 的角色仍会得到 403。

当前规则下的实际判定结果：

```text
developer + instances=0:
GET /api/v1/tdsql/connections/options -> False
```

#### 必须整改

增加“已认证但与菜单无关的精确只读端点”机制，例如：

```python
_MENU_INDEPENDENT_READ_ENDPOINTS = {
    ("GET", "/api/v1/tdsql/connections/options"),
}
```

要求：

1. Token 校验仍由认证中间件完成，绝不能加入 `PUBLIC_PATHS`；
2. 仅精确匹配 method + path，不允许对 `/connections` 整体放行；
3. HEAD/OPTIONS 如需支持，应明确列出或由框架统一处理，不得扩大到同前缀其他接口；
4. `/connections/{id}/probe`、`/connections` 全字段列表继续走各自权限规则。

---

### R04【P1】复用 `savedConnections` 会产生管理数据被精简数据覆盖的竞态

V2.0 计划让：

- `loadConnectionOptions()` 将精简数据写入 `savedConnections`；
- `loadSavedConnections()` 将全量管理数据也写入 `savedConnections`；
- `loadAll()` 无条件请求 options；
- 拥有 `instances` 菜单或进入实例管理页时再请求全量列表。

现有 `loadAll()` 中多数加载调用没有 `await`。如果两个请求并发，后返回的精简列表可能覆盖全量管理列表，导致：

- 实例管理表格的门禁、ZK、描述、类型来源等列为空；
- 三维筛选和分页数据异常；
- 编辑抽屉缺字段；
- admin/DBA 页面偶发退化，形成难复现的时序故障。

#### 必须整改

拆分状态，禁止共用：

```javascript
const connectionOptions = ref([])       // 全平台业务选择器
const managedConnections = ref([])      // 仅实例管理页面
```

并执行以下约束：

1. 全平台 18 处实例下拉框改用 `connectionOptions`；
2. 实例管理筛选、分页、表格、编辑操作改用 `managedConnections`；
3. `loadAll()` 只加载 options；
4. 仅在当前页面确为 `instances` 且用户具有菜单权限时加载管理列表；
5. 管理写操作完成后只刷新 `managedConnections`，必要时再显式刷新 options；
6. 刷新后校验当前已选 connection id 是否仍存在，防止实例删除后保留悬空选择。

---

## 4. 非阻断但必须纳入本次修订的问题

### R05【P2】精简端点字段契约和数据投影不够严格

文档多处称“6 个字段”，实际列出 8 个：

```text
id, name, host, port, database, effective_instance_type, is_default, active
```

其中 `is_default` 和 `active` 对默认选择及现有状态圆点体验有用，可以保留，但文档、模型和测试必须统一称为 8 个字段。

同时不建议 `/options` 直接调用使用 `SELECT c.*` 的 `list_saved()` 后再过滤。建议新增专用 `list_connection_options()`：

1. SQL 只读取最终输出字段和计算 `effective_instance_type` 必需的来源字段；
2. API 层使用固定响应模型或显式 allowlist 投影；
3. 测试断言每一项的 key 集合与 8 字段 allowlist 完全相等；
4. 继续修复 `list_saved()`，移除 `monitor_password_encrypted`，因为获授实例管理菜单的普通只读角色仍能访问管理列表；
5. 测试同时检查 `/options` 和 `/connections` 均不含任何明文或密文口令字段。

### R06【P2】前端失败反馈应一并修正

当前以下函数没有根据 `resp.ok` 判断结果：

- `deleteConn()`：403/404/500 时仍可能提示“已删除”；
- `setDefaultConn()`：403/404/500 时仍可能提示“已设为默认”。

虽然普通角色的按钮整改后会隐藏，但 admin/DBA 遇到真实服务异常时仍会得到错误成功反馈。G 应统一按 `resp.ok` 解析响应，并只在 2xx 时展示成功消息。

---

## 5. 对 V2.0 已正确部分的确认

以下设计方向应保留：

1. 新增独立 `/connections/options`，不直接放开现有全字段 `/connections`；
2. `/options` 必须要求合法 Token，不得成为公开接口；
3. `monitor_password_encrypted` 必须从 `list_saved()` 响应中移除；
4. 非 admin/DBA 进入实例管理页时隐藏所有新增、编辑、删除、导入、连接和探测操作；
5. 前端隐藏只负责体验，后端必须是最终权限边界；
6. 不修改数据库表结构，不改变 TDSQL 执行引擎；
7. 业务模块使用实例时，仍由 `_get_pool()` -> `registry.get(auto_connect=True)` 在授权业务调用内部自动建连，不要求普通用户拥有显式“连接实例”的管理权限。

第 7 点是保证现有体验的重要前提：普通角色的显式 `/connections/{id}/connect` 应返回 403，但其已获授权的在线审核、扫描或查询类业务接口仍应能够按 connection id 正常工作。

---

## 6. 修订后的目标权限矩阵

| 场景 | admin | dba | developer/auditor/custom + instances | developer/auditor/custom - instances |
|---|---:|---:|---:|---:|
| `GET /connections/options` | 允许 | 允许 | 允许 | 允许 |
| 查看实例管理菜单 | 允许 | 按菜单配置 | 按菜单配置 | 禁止 |
| `GET /connections` 管理列表 | 允许 | 按菜单配置 | 按菜单配置，只读 | 禁止 |
| 新建/编辑/删除/设默认实例 | 允许 | 允许（需菜单） | 禁止 | 禁止 |
| 显式连接、断开、连接测试、MonitorDB 探测 | 允许 | 允许（需菜单） | 禁止 | 禁止 |
| ZK 扫描、预览、导入 | 允许 | 允许（需菜单） | 禁止 | 禁止 |
| 实例类型锁定 | 允许 | 禁止 | 禁止 | 禁止 |
| ZK 全局配置维护 | 允许 | 禁止 | 禁止 | 禁止 |
| 已授权业务模块使用实例 | 按模块权限 | 按模块权限 | 按模块权限 | 按模块权限 |

说明：“业务模块使用实例”与“实例管理写权限”必须解耦。普通角色是否能够启动扫描、采集大表或执行在线审核，继续由对应模块权限决定；本次只保证其下拉选项不会因为缺少 `instances` 菜单而为空。

---

## 7. 必须补充的自动化测试

### 7.1 后端权限矩阵

| 编号 | 测试 | 预期 |
|---|---|---|
| RBAC-01 | 无 Token、过期 Token 请求 `/connections/options` | 401 |
| RBAC-02 | 四个内置角色和一个自定义角色，均不分配 `instances`，请求 `/connections/options` | 200 |
| RBAC-03 | 普通角色分配 `instances`，请求 `/connections` | 200，只读 |
| RBAC-04 | 普通角色有/无 `instances`，调用实例创建、编辑、删除 | 全部 403，数据库不变 |
| RBAC-05 | 普通角色有/无 `instances`，调用 GET/POST `test-connection` | GET 路由不存在或 405；POST 为 403 |
| RBAC-06 | 普通角色有/无 `instances`，调用 `/connections/{id}/probe` | 403，且探测方法未被调用 |
| RBAC-07 | admin/DBA 调用实例 CRUD | 按业务输入正常放行 |
| RBAC-08 | DBA 调用实例类型锁定和 ZK 配置维护 | 403 |
| RBAC-09 | admin 调用实例类型锁定和 ZK 配置维护 | 正常放行 |
| RBAC-10 | DBA 调用废弃 `/discover/register` | 410，而不是 2xx/403 |

### 7.2 响应数据安全

| 编号 | 测试 | 预期 |
|---|---|---|
| DATA-01 | 检查 `/connections/options` 每一项字段 | key 集合严格等于 8 字段 allowlist |
| DATA-02 | 检查 `/connections/options` 完整 JSON | 不含 `password`、`secret`、`monitor_*`、账号、描述等额外字段 |
| DATA-03 | 检查 `/connections` 完整 JSON | 不含 `password_encrypted`、`monitor_password_encrypted` 或任何真实口令 |
| DATA-04 | 多 Worker/活跃状态场景 | 不因单 Worker 的 active 状态导致功能不可用；状态差异仅作展示 |

### 7.3 前端与业务回归

| 编号 | 测试 | 预期 |
|---|---|---|
| UI-01 | 普通角色有 `instances` 菜单进入实例管理 | 写按钮数量为 0，管理列表可读 |
| UI-02 | 普通角色无 `instances` 菜单进入各授权业务模块 | 18 处实例选择器正常加载 |
| UI-03 | admin/DBA 进入实例管理 | 管理字段、筛选、分页、编辑功能完整 |
| UI-04 | options 与管理列表并发/乱序返回 | 两类状态互不覆盖 |
| UI-05 | 删除、设默认接口返回 403/404/500 | 不展示成功提示，展示后端错误 |
| UI-06 | 普通角色选择一个尚未显式激活的实例并执行已授权业务操作 | 业务调用可通过内部自动建连正常完成，不要求显式 connect 权限 |
| UI-07 | 管理员删除当前已选实例 | 所有选择器清理悬空 id，并选择有效默认值或提示重新选择 |

测试不能只断言状态码。涉及创建、删除、导入和探测的反向用例还必须断言：

- 数据库记录数量与内容未变化；
- `registry` 活跃连接未变化；
- Mock 的外部连接/探测函数没有被调用；
- 操作审计没有记录一条伪成功业务操作。

---

## 8. 给智能体 G 的必改清单与准入判据

G 修订详细设计时，必须逐项关闭以下条目：

- [ ] 将 `GET /connections/{id}/probe` 明确收敛为 admin/DBA，并给出端点级校验代码；
- [ ] 删除/停用 `GET /test-connection`，前端改用 `POST + JSON`，并收敛为 admin/DBA；
- [ ] 将 20 端点统一权限改成分层权限矩阵，保留实例类型锁定和 ZK 配置的 admin 独占；
- [ ] 修正 TC-08，不再要求 DBA 对 admin-only/410 端点返回 2xx；
- [ ] 给出 `/connections/options` 精确绕过父级菜单前缀的实现，不加入公开路径；
- [ ] 将前端 `connectionOptions` 与 `managedConnections` 分离，消除并发覆盖；
- [ ] 将“6 个字段”统一修正为实际 8 字段，并采用严格 allowlist；
- [ ] 为 options 增加专用最小 SQL/服务投影，保留 `list_saved()` 的双密文清理；
- [ ] 修复 `deleteConn()`、`setDefaultConn()` 的伪成功反馈；
- [ ] 扩充后端、数据安全、前端和未激活实例自动建连回归测试；
- [ ] 将文档状态从无条件 `Approved by Review` 改为“整改后批准”，直至上述测试全部通过。

全部勾选、实现完成并通过第 7 节测试后，本设计可判定为：

> **批准实施：能够满足实例管理写权限收敛与全模块实例只读上下文解耦要求，且不会破坏现有授权业务流程。**

在此之前，评审结论保持：

> **有条件通过，不得直接照 V2.0 当前文本施工。**
