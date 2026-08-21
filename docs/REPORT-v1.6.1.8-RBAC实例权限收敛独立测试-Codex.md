# V1.6.1.8 RBAC 实例管理权限收敛独立测试报告

> 测试对象：`main@51ebaf26e1762d947ea950824218ce5368bebff7`
> 测试分支：`test/rbac-independent-validation`（与 `origin/main` 同提交，无业务代码改动）
> 测试日期：2026-08-21
> 测试执行：Codex 独立测试
> 依据：`docs/DESIGN-RBAC-实例管理权限收敛与全模块实例读取解耦详细设计说明书.md` V3.1

## 1. 验收结论

**结论：有条件不通过，暂不建议发布。**

两项核心业务要求在后端稳态和正常页面操作下均已实现：

1. 只有 `admin`、`dba` 可以新增、编辑、删除、连接、断开、测试、探测和导入实例；普通角色即使拥有“实例管理”菜单，也只能查看。
2. 未分配“实例管理”菜单的已登录角色，仍可通过精简 `/connections/options` 端点，在在线元数据审核、扫描任务、上线检查、大表治理等业务模块正常检索和选择实例。

自动化冒烟、SIT、UAT 和全量回归均为 0 失败，后端越权写入也未造成数据变化。但是，真实浏览器角色切换测试发现 **1 项 P1 前端会话隔离缺陷**：同一浏览器标签页从 DBA 切换到无“实例管理”菜单的低权限账号时，在权限菜单请求完成前，会短暂显示上一账号缓存的实例管理页面和全字段实例列表。后端随后返回 403 并使页面收敛，但敏感管理字段已经在低权限会话中短暂渲染，因此不能按“最终隐藏”判定通过。

## 2. 测试环境与基线

| 项目 | 实际值 |
|---|---|
| 应用版本 | `1.6.1.8` |
| Git 基线 | `51ebaf26e1762d947ea950824218ce5368bebff7` |
| Python | `3.14.6` |
| Node.js | `v24.18.0` |
| 元数据数据库 | MySQL `8.0.45`，独立测试库 `tdsql_sqlcheck_test` |
| Web 服务 | Uvicorn，`AUTH_ENABLED=true`，`SCHEDULER_ENABLED=false` |
| 浏览器 | Codex In-app Browser，访问 `http://127.0.0.1:18080` |
| 测试角色 | admin、dba、普通只读角色（有 instances 菜单）、普通角色（无 instances 菜单） |

测试连接、用户和自定义角色均使用 `QA-*` 临时数据。测试结束后已删除 3 条临时实例、3 个临时用户和 2 个临时角色，并把测试库 admin 口令恢复为本仓库自动化测试约定值；本地 Uvicorn 和浏览器测试页均已关闭。

## 3. 执行结果汇总

以下批次存在用例重叠，不应直接相加作为唯一用例总数；全量回归 1304 条为最终覆盖统计。

| 阶段 | 命令/范围 | 结果 | 耗时 |
|---|---|---:|---:|
| 静态冒烟 | `git diff --check`、`node --check frontend/static/js/app.js` | 通过 | < 1s |
| 冒烟测试 | `test_smoke_multi_set.py`、`test_security_headers.py`、`test_v3_rbac_instances.py` | **45 passed / 0 failed** | 8.672s |
| SIT | 9 个 `test_sit*.py` / `test_v2_sit.py` 测试文件 | **380 passed / 0 failed** | 90.862s |
| UAT | 10 个 `test_uat*.py` / `test_v2_uat.py` 测试文件 | **325 passed / 0 failed** | 98.321s |
| 全量回归 | `python -m pytest tests -q` | **1304 passed / 0 failed / 0 skipped** | 229.876s |
| 活服务接口矩阵 | 4 角色、18 个管理/探测/导入动作、字段白名单与数据快照 | 权限断言全部通过 | 约 2s |
| 真实浏览器 UAT | 4 角色登录、菜单展开、表单、下拉、增改、删除确认、错误反馈、角色切换 | 核心路径通过；发现 1 项 P1 | 手工交互 |

JUnit 结果保存在本地忽略目录 `scratch/test-results-{smoke,sit,uat,full}.xml`，未纳入提交。

## 4. 冒烟测试结果

### 4.1 服务与页面

- `/`、静态 Vue/Element Plus/ECharts/CSS/JS 资源均返回 200，页面标题与版本显示为 V1.6.1.8。
- admin、dba、普通只读、无 instances 菜单角色均可正常登录、退出。
- 管理员真实点击“新建连接”，填写表单并点击“测试连接”；服务访问日志确认请求为 `POST /api/v1/tdsql/test-connection`，连接失败时页面显示明确的业务库与 MonitorDB 失败原因，未再通过 URL Query 传递口令。
- 管理员真实保存 `QA-ADMIN-CLICK`，DBA 真实保存并编辑 `QA-DBA-CLICK -> QA-DBA-EDITED`，管理列表与精简下拉均同步刷新。
- DBA 真实点击删除后正确弹出二次确认框；本轮在浏览器中选择取消。随后使用同一活服务接口分别以 admin、dba 成功删除各自临时实例，均返回 200，并验证 `/connections/options` 不再包含已删项。
- 浏览器控制台在正常操作阶段无 JavaScript error/warn。

### 4.2 角色页面表现

| 角色/场景 | 实例管理页面 | 操作入口 | 业务模块实例下拉 | 结果 |
|---|---|---|---|---|
| admin | 可见完整列表 | 新建、编辑、删除、连接、探测、ZK 配置均可见 | 正常 | PASS |
| dba | 可见完整列表 | 新建、编辑、删除、连接、探测、ZK 导入可见；ZK 全局配置和类型锁定不可见 | 正常 | PASS |
| 普通角色 + instances | 可见完整列表 | 新建、编辑、删除、连接、设默认、探测、ZK 按钮及“操作”列全部不存在 | 正常 | PASS |
| 普通角色 - instances | 菜单不可见，稳态无法进入 | 不存在 | 正常 | **稳态 PASS；跨角色切换瞬态 FAIL，见 DEFECT-01** |

普通角色无 instances 菜单时，已逐页真实展开、筛选并选中管理员刚创建的实例：

- 在线元数据审核：选中后“拉取元数据并执行文件审核”由禁用变为可用；
- 扫描任务：实例筛选项可检索、选择，查询按钮可用；
- 上线检查：选中后“执行上线检查”由禁用变为可用；
- 大表治理：实例可检索、选择，“刷新清单”可用。

模板中的实例选择器共计 18 处，均统一绑定 `savedConnections`；当前实现中该变量是 `connectionOptions` 的兼容别名。自动化 UAT、静态绑定检查和上述四个代表性真实页面共同确认没有继续依赖管理端点的选择器。

## 5. SIT 测试结果

### 5.1 权限矩阵

对拥有 instances 菜单但角色不是 admin/dba 的账号，以下 **18 个活服务请求全部返回 403**：

1. 直接连接、配置文件连接、测试连接、断开连接；
2. 新建、更新、删除、设默认及默认别名、激活已保存连接；
3. 实例类型探测、探测诊断、MonitorDB GET/POST 探测；
4. ZK 扫描、导入预览、名称诊断、导入提交。

请求前后对 `tdsql_connections` 的总数、默认实例集合和目标实例关键字段做快照，结果完全一致，且不存在越权创建的 `qa_illegal_create` 记录。说明不是“前端隐藏按钮但接口仍可写”，后端越权防线有效。

### 5.2 读取分层与数据安全

- 无 Token 请求 `/connections/options`：401；四类已认证角色：全部 200。
- `/connections/options` 每项 Key 集合严格等于 8 字段：`id`、`name`、`host`、`port`、`database`、`effective_instance_type`、`is_default`、`active`。
- 精简响应不含 username、password、monitor_user、description、密文或测试口令。
- 普通角色拥有 instances 菜单时 `/connections` 返回 200；不拥有该菜单时返回 403。
- 全字段管理列表不含 `password_encrypted`、`monitor_password_encrypted`，口令仅以 `***` 返回。
- 历史 `GET /test-connection` 返回 405；新 `POST` 路径权限正确。
- DBA 调用实例类型锁定和 ZK 全局配置返回 403；admin 读取 ZK 全局配置返回 200。

### 5.3 现有功能回归

SIT 380 条和全量 1304 条全部通过，未发现本次改造对审核、慢 SQL、扫描计划、大表、深度诊断、平台治理等既有后端功能造成回归。

## 6. UAT 测试结果

### 6.1 用户目标验收

| 编号 | 验收目标 | 结果 |
|---|---|---|
| UAT-01 | 普通角色即使获得实例管理菜单，也不能新建、编辑、删除实例 | PASS |
| UAT-02 | 普通角色不能通过手工 HTTP 绕过前端执行实例写操作 | PASS |
| UAT-03 | admin、dba 仍可正常新建、编辑和删除实例 | PASS |
| UAT-04 | DBA 不能使用仅 admin 可用的类型锁定和 ZK 全局配置 | PASS |
| UAT-05 | 未分配实例管理菜单时，其他业务模块实例列表仍正常 | PASS |
| UAT-06 | 精简下拉响应不泄漏账号、口令、监控库和描述字段 | PASS |
| UAT-07 | 新增/编辑后管理列表和全局下拉状态同步 | PASS |
| UAT-08 | 同标签页高权限账号退出后，低权限账号不能看到前一账号管理数据 | **FAIL（P1）** |

### 6.2 用户体验

- 正常网络条件下，页面最终状态符合角色权限；普通只读页面没有“空操作列”。
- admin/dba 保存后无需手工刷新即可看到列表变化；其他业务模块重新登录后能检索到新实例。
- 测试连接失败反馈清晰，不会把服务端 4xx/5xx 误显示为成功。
- 角色切换时存在 P1 瞬态数据泄漏，必须修复后再做 UAT 准出。

## 7. 缺陷与可实施修改意见

### DEFECT-01：跨角色切换时短暂显示上一账号的实例管理全字段数据

- **级别：P1 / 发布阻断**
- **范围：** `frontend/static/js/app.js`
- **定位：** `visibleMenus` 初始化约第 376 行，`doLogout` 约第 407 行，`loadManagedConnections` 约第 485 行，`loadAll` 约第 1543-1548 行。

#### 复现步骤

1. 同一浏览器标签页以 DBA 登录并进入“实例管理”，页面缓存 17 条 `managedConnections`；
2. 退出登录，不刷新页面；
3. 登录一个没有 instances 菜单的角色；
4. 将 `/api/v1/auth/visible-menus` 网络延迟放大到 800ms；
5. 在权限请求返回前，页面仍显示“平台治理 / 实例管理”、17 条完整实例数据和“用户名”列；
6. 权限请求返回后页面才切到工作台，实例管理菜单消失；服务端同时记录该低权限账号 `GET /api/v1/tdsql/connections 403`。

稳定复现证据：瞬态 DOM 中 `实例管理=true`、上一账号临时实例行存在、`用户名`列存在；收敛后 `实例管理`节点数量为 0。

#### 根因

1. `visibleMenus` 初始值是包含全平台菜单的集合，退出时没有清空；
2. `doLogout()` 只清 Token/User，没有清空 `managedConnections`、`visibleMenus` 和 `currentPage`；
3. 登录成功后先 `applyUser(data.user)`，主界面立即渲染，再异步获取新角色菜单；
4. `loadAll()` 使用 `visibleMenus.has('instances') || currentPage==='instances'`，旧页面仍为 instances 时会让低权限账号请求管理端点；
5. `loadManagedConnections()` 在 403 时保留旧数组，没有执行失败关闭清理。

#### 建议修改

1. **菜单默认关闭：** 将 `visibleMenus` 初始化为空集合，未完成会话/权限加载前不渲染业务菜单。
2. **统一清理角色态：** 新增 `clearRoleScopedState()`，至少清空：
   - `visibleMenus.value = new Set()`；
   - `managedConnections.value = []`；
   - `currentPage.value = 'dashboard'`；
   - 其他仅高权限页面的缓存数据（用户、审计日志、系统信息等，建议一并审计）。
   在 `doLogout()`、401 回调和登录新账号前同步调用。
3. **调整登录顺序：** Token 写入后先获取 `visible-menus` 并规范化 `currentPage`，完成后再设置 `authState.user` 或解除主界面 loading；不能让新账号先使用旧权限状态渲染。
4. **调整 `loadAll()`：** 在任何数据加载前先完成当前页面授权校验；管理列表仅在 `visibleMenus.has('instances')` 时加载，删除 `|| currentPage==='instances'`。
5. **失败关闭：** `/connections` 非 2xx 或异常时立即 `managedConnections.value=[]`，不能沿用上次成功结果。
6. **推荐最小结构：**

```js
const clearRoleScopedState = () => {
  visibleMenus.value = new Set();
  managedConnections.value = [];
  currentPage.value = 'dashboard';
};

const loadAll = async () => {
  await loadVisibleMenus();
  normalizeCurrentPageByPermissions();
  loadConnectionOptions();
  if (visibleMenus.value.has('instances')) await loadManagedConnections();
  else managedConnections.value = [];
  // 再加载其他已授权模块
};
```

7. **新增自动化：** 增加真实浏览器 E2E：DBA 进入实例管理 → logout → 无 instances 角色登录；对 `visible-menus` 注入 500~800ms 延迟，断言全过程没有实例管理菜单、用户名列、上一账号实例行，同时断言新账号没有发出 `/connections` 请求。

### TEST-GAP-01：V3.1 新增测试不能覆盖真实浏览器角色切换

- **级别：P2 / 测试债务**
- G 新增的 `tests/test_v3_rbac_instances.py` 12 条全部通过，但主要验证稳定状态下的后端接口；文件头所称 UI-06 实际只是后端/状态断言，没有执行浏览器、角色切换或渲染时序。
- 建议建立浏览器 E2E 用例，至少覆盖：四角色按钮可见性、四个代表业务模块实例选择、POST 测试连接、增改后双列表刷新、删除当前选中实例、同标签页升降权切换、慢网络条件下无旧角色数据闪现。

## 8. 风险评估与准出条件

### 已排除风险

- 普通角色通过手工构造请求越权增删改实例；
- 无实例菜单导致业务模块实例下拉为空；
- options 被父级 `/connections` 菜单规则误拦截；
- 精简接口泄漏账号、口令、监控库配置或描述；
- GET Query 继续暴露测试连接口令；
- DBA 获得 admin-only 类型锁定或 ZK 全局配置权限；
- 改造导致现有 1304 条自动化回归失败。

### 发布前必须完成

1. 修复 DEFECT-01 的角色态清理、渲染顺序和管理列表失败关闭；
2. 增加慢网络跨角色切换 E2E，证明低权限会话全过程不渲染上一账号管理数据；
3. 复测四角色真实页面、18 个写端点、四个代表业务模块下拉；
4. 再跑 `test_v3_rbac_instances.py`、SIT、UAT 和全量 `pytest tests -q`，保持 0 failed；
5. 复测通过后再把本报告结论更新为“通过”。

## 9. 最终意见

G 的后端权限收敛和实例读取解耦方向正确，核心接口设计已能满足用户提出的两项业务目标，且自动化回归面稳定。当前唯一阻断点不是后端越权，而是前端 SPA 在同标签页角色切换时复用上一账号权限与管理数据的瞬态泄漏。该问题有稳定复现路径和明确修复点，修复工作量不大，但安全性质明确，建议修复并独立复测后再发布。
