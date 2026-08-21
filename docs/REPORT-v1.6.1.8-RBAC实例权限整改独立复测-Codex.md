# V1.6.1.8 RBAC 实例权限整改独立复测报告

> 整改提交：`79c0022907ecf8b1829ac39aa07920f70969ffcb`
> 提交说明：`fix(rbac): resolve DEFECT-01 cross-role session isolation and race condition on login/logout`
> 复测分支：`test/rbac-independent-retest`
> 复测日期：2026-08-21
> 复测执行：Codex 独立测试
> 前序报告：`docs/REPORT-v1.6.1.8-RBAC实例权限收敛独立测试-Codex.md`

## 1. 准出结论

**结论：不通过，暂不建议发布。**

G 对前序 `DEFECT-01` 的整改有效：同一浏览器标签页从 DBA 切换到没有“实例管理”菜单的角色时，即使施加 800ms 网络延迟，登录全过程也没有再显示上一账号的实例管理菜单、用户名列、敏感用户名或实例行，且低权限账号没有发出全字段 `/connections` 请求。

但整改新增的统一状态清理逻辑引入了 **1 项 P1 前端功能回归**：用户通过登录表单进入系统后，点击“在线元数据审核”，页面发生 Vue 渲染异常，只显示页签，不显示实例下拉和审核表单。刷新浏览器后页面恢复，证明后端精简实例读取正常，故障来自前端清理态与模板数据契约不一致。该模块正是本次“未分配实例管理菜单也必须能选择实例”的核心验收范围，因此仍不满足准出条件。

此外发现 **1 项 P2 测试隔离缺陷**：新增 RBAC 测试永久修改共享测试库的 `developer.instances` 权限而不恢复，使“冒烟测试 → SIT”顺序稳定产生 1 条假失败，自动化结果依赖执行顺序。

## 2. 整改代码核对

整改已覆盖前序报告提出的主要代码点：

1. `visibleMenus` 默认初始化为空集合；
2. 新增 `clearRoleScopedState()`，退出、重新登录和 401 时清空角色态；
3. 登录时先读取 `visible-menus`，再应用新用户角色；
4. `loadAll()` 删除 `currentPage==='instances'` 的越权加载旁路；
5. 无 `instances` 菜单时清空 `managedConnections`；
6. 管理列表请求失败时执行失败关闭；
7. 当前页监听器仅在拥有 `instances` 菜单时加载管理列表；
8. 增加后端会话隔离断言和前端静态契约断言。

方向正确，前序敏感数据瞬态泄漏已经关闭。新问题来自状态清理值没有保持原组件的数据类型契约，详见 `RETEST-DEFECT-01`。

## 3. 测试环境与数据

| 项目 | 实际值 |
|---|---|
| 应用版本 | `1.6.1.8` |
| Git 基线 | `79c0022907ecf8b1829ac39aa07920f70969ffcb` |
| Python | `3.14.6` |
| Node.js | `v24.18.0` |
| 元数据数据库 | MySQL `8.0.45`，独立测试库 `tdsql_sqlcheck_test` |
| Web 服务 | Uvicorn，`AUTH_ENABLED=true`，`SCHEDULER_ENABLED=false` |
| 服务地址 | `http://127.0.0.1:18080` |
| 浏览器 | Codex In-app Browser |
| 测试角色 | admin、dba、实例管理只读角色、无实例管理菜单的跨模块只读角色 |

真实页面创建了唯一标记实例 `QA-RBAC-SESSION-MARKER`，随后由 DBA 编辑为 `QA-RBAC-SESSION-MARKER-DBA`，用户名使用 `qa_sensitive_user`，用于识别上一角色数据是否在低权限会话闪现。

## 4. 自动化执行结果

SIT、UAT 中有部分用例必须访问已经启动并可登录的真实服务。首次批量执行时这些用例按设计跳过；随后在 `127.0.0.1:18080` 启动鉴权服务并补跑，所有跳过项均已实际执行并通过。以下“最终唯一用例”不重复累计补跑批次。

| 阶段 | 首轮结果 | 补充执行 | 最终唯一用例结论 |
|---|---:|---:|---:|
| 静态检查 | `git diff --check`、`node --check` 通过 | 无 | PASS |
| 冒烟 | `47 passed` | 无 | **47 passed** |
| SIT | `369 passed / 11 skipped` | 11 个真实服务用例通过 | **380 passed** |
| UAT | `308 passed / 17 skipped` | 17 个真实服务用例通过 | **325 passed** |
| 全量回归 | `1278 passed / 28 skipped` | 28 个跳过项全部通过 | **1306 passed** |
| 真实服务规则页批次 | 无 | `36 passed` | PASS |

自动化业务断言最终均通过，但不能据此判定可发布，原因是新增前端“安全契约测试”只做源码字符串匹配，没有运行浏览器，未覆盖本轮发现的真实渲染异常。

## 5. 原缺陷专项回归

### 5.1 同标签页 DBA 降权切换

真实点击执行以下路径：

1. DBA 登录并进入“实例管理”；
2. 确认页面已缓存 15 条全字段实例，且显示标记实例、用户名列、新建/编辑/删除入口；
3. 退出登录，不刷新标签页；
4. 对浏览器网络施加 800ms 延迟；
5. 登录没有 `instances` 菜单的 `qa_retest_nomenu`；
6. 分别在点击登录后 100ms、950ms、1850ms 采样 DOM；
7. 读取全过程网络请求。

三个采样点结果完全一致：

| 采样点 | 实例管理菜单 | 标记实例 | 用户名列 | 敏感用户名 |
|---:|---:|---:|---:|---:|
| 100ms | 0 | 0 | 0 | 0 |
| 950ms | 0 | 0 | 0 | 0 |
| 1850ms | 0 | 0 | 0 | 0 |

网络证据：

- 已请求 `/api/v1/auth/login`；
- 已请求 `/api/v1/auth/visible-menus`；
- 已请求 `/api/v1/tdsql/connections/options`；
- **没有请求** `/api/v1/tdsql/connections` 全字段管理端点。

结论：**前序 DEFECT-01 已修复。**

### 5.2 正常升降权切换

- admin 真实点击新建并保存标记实例，列表立即增加至 15 条；
- DBA 登录后能看到标记实例、新建、编辑、删除、连接、探测入口；
- DBA 真实编辑实例名称成功；
- DBA 点击删除后正常弹出二次确认框，本轮点击取消，数据未删除；
- DBA 不显示仅 admin 可用的“ZK发现配置”和实例类型锁定入口；
- 只读角色拥有“实例管理”菜单时能看到标记实例和用户名列，但新建、编辑、删除、连接、ZK 入口及“操作”列数量均为 0。

## 6. 活服务接口矩阵

### 6.1 读取分层

| 场景 | admin | dba | 实例只读角色 | 无实例菜单角色 |
|---|---:|---:|---:|---:|
| `/connections/options` | 200 | 200 | 200 | 200 |
| `/connections` | 200 | 200 | 200 | 403 |

- 未认证访问 `/connections/options` 返回 401；
- 四个角色的 options 均包含 15 条实例；
- 每个 options 项 Key 严格等于 8 个允许字段；
- options 响应不含 `qa_sensitive_user`、口令、监控用户名、描述或密文字段；
- 全字段管理列表中的口令仅返回 `***`，不返回密文。

### 6.2 非管理角色越权写入

分别以“拥有实例管理菜单的只读角色”和“没有实例管理菜单的角色”调用以下 18 个动作：

1. 直接连接；
2. 配置文件连接；
3. 测试连接；
4. 断开连接；
5. 新建实例；
6. 更新实例；
7. 删除实例；
8. 设为默认；
9. 默认别名接口；
10. 激活已保存实例；
11. 实例类型探测；
12. 探测诊断；
13. MonitorDB POST 探测；
14. MonitorDB GET 探针；
15. ZK 扫描；
16. ZK 导入预览；
17. ZK 名称诊断；
18. ZK 导入提交。

两个低权限角色共 36 次请求全部返回 403。请求前后实例总数、默认实例集合和标记实例的名称、地址、用户名、数据库及描述快照完全一致。

### 6.3 DBA 与 admin-only 边界

- DBA 新建、更新、删除临时实例均返回 200；
- DBA 调用实例类型锁定返回 403；
- DBA 读取 ZK 全局配置返回 403。

后端权限边界满足设计要求。

## 7. 跨模块实例读取真实点击结果

测试账号没有“实例管理”菜单，但拥有在线元数据审核、扫描任务、上线检查和大表治理菜单。

| 模块 | 实例菜单/选项 | 真实选择 | 操作状态 | 结论 |
|---|---|---|---|---|
| 在线元数据审核 | 后端 options 正常 | 登录后页面表单未渲染 | 控制台 TypeError | **FAIL** |
| 扫描任务 | 标记实例存在 | 可选中 | 查询按钮可用 | PASS |
| 上线检查 | 标记实例存在 | 可选中 | 执行上线检查按钮由禁用变为可用 | PASS |
| 大表治理 | 标记实例存在 | 可选中 | 刷新清单按钮可用 | PASS |

对在线元数据审核执行浏览器刷新后，再次进入页面可以看到实例下拉、选中标记实例并使“拉取元数据并执行文件审核”按钮可用。这一对照进一步证明 options 解耦没有问题，故障发生在登录时的前端状态重置。

## 8. 新发现问题与修改意见

### RETEST-DEFECT-01：登录后“在线元数据审核”页面渲染崩溃

- **级别：P1 / 发布阻断**
- **范围：** `frontend/static/js/app.js`、`frontend/index.html`
- **稳定复现：** admin、dba、自定义普通角色均可复现；刷新页面后暂时恢复。

#### 复现步骤

1. 打开登录页并通过表单登录任意拥有“在线元数据审核”菜单的账号；
2. 点击“SQL审核 → 在线元数据审核”；
3. 页面只显示四个页签，不显示实例下拉、目标库、范围选择和执行按钮；
4. 浏览器控制台出现：`TypeError: Cannot read properties of null (reading 'filename')`；
5. 按 F5 刷新，在保留会话的情况下重新进入该页面，表单恢复。

#### 根因

1. `frontend/static/js/app.js` 初始化使用 `const extractedResult=ref({});`；
2. 新增的 `clearRoleScopedState()` 却执行 `extractedResult.value=null;`；
3. 登录表单每次提交前都会调用 `clearRoleScopedState()`；
4. `frontend/index.html` 直接执行 `v-if="extractedResult.filename"`，没有空值保护；
5. 因此登录后第一次进入页面时，Vue 渲染直接异常，整个当前页内容树中断。

#### 可实施修改

最小修复必须同时保持状态类型一致并增加模板防御：

```js
// clearRoleScopedState()
extractedResult.value = {};
```

```html
<div v-if="extractedResult && extractedResult.filename" class="mt-16">
```

进一步建议：

1. 为角色态建立统一的初始值工厂，初始化和清理共同使用，避免数组、对象、`null` 三种状态漂移；
2. 对 `clearRoleScopedState()` 中每个字段核对模板访问方式，凡直接访问属性的对象均不能无条件清为 `null`；
3. 新增浏览器 E2E：从登录页登录后依次打开在线元数据审核、扫描任务、上线检查、大表治理，断言核心表单存在且控制台无 error；
4. 增加 DBA → 无实例菜单角色切换后打开上述四页的回归，不能只验证菜单和管理列表。

### RETEST-DEFECT-02：新增 RBAC 测试污染共享测试库权限

- **级别：P2 / 测试可靠性**
- **范围：** `tests/test_v3_rbac_instances.py`

#### 复现步骤

1. 执行冒烟批次，其中包含 `test_v3_rbac_instances.py`，结果 47 passed；
2. 紧接着执行 SIT；
3. `tests/test_v2_sit.py::TestSITConnectionFlow::test_dba_saves_connection_and_schedule` 失败；
4. 响应实际为 403，后续访问 `resp.json()["connections"]` 触发 `KeyError`；
5. 查询 `tdsql_sqlcheck_test.role_permissions`，确认 `developer / instances / visible=0`；
6. 恢复为 1 后，单测与完整 SIT 均通过。

#### 根因

`test_session_isolation_and_role_switching()` 调用：

```python
set_role_permissions("developer", {"instances": 0})
```

但模块 fixture 的 teardown 只删除测试实例，没有恢复 built-in `developer` 角色原始权限。测试直接修改共享 MySQL 测试库，结果会跨 pytest 进程保留。

#### 可实施修改

推荐不要修改内置角色，改用专用临时角色：

1. fixture 创建 `test_no_instances_role`；
2. 只为该角色设置所需菜单权限；
3. 创建专用用户执行测试；
4. teardown 删除用户和角色；
5. 所有创建、权限修改和清理放在 `try/finally` 中。

若必须修改 `developer`，至少在修改前读取原值，并在 fixture finalizer 中调用 `set_role_permissions()` 恢复原值；恢复动作必须在断言失败时也执行。

同时建议在 CI 中固定增加顺序回归：

```text
test_v3_rbac_instances.py -> test_v2_sit.py
test_v2_sit.py -> test_v3_rbac_instances.py
```

两个顺序都必须独立通过。

### RETEST-GAP-01：所谓“前端安全契约测试”仍不是浏览器 E2E

- **级别：P2 / 测试债务**
- 新增 `test_frontend_security_contract()` 仅查找源码字符串；
- 它能证明代码中出现了 `clearRoleScopedState`，不能证明 Vue 渲染正常、慢网络无闪现或跨模块下拉可用；
- 本轮 P1 正是由被静态测试判定为“存在”的清理代码引入，说明该测试不能作为前端验收替代品。

建议引入真实浏览器测试，至少覆盖：

1. admin、dba、实例只读、无实例菜单四角色的按钮和菜单矩阵；
2. admin 新建、DBA 编辑、删除二次确认；
3. 四个代表业务模块的实例选项和选中状态；
4. DBA → 无实例菜单角色，在 500ms、800ms 延迟下全过程无旧数据；
5. 低权限登录全过程不请求 `/connections`；
6. 每次导航后断言控制台 error 数为 0。

## 9. 风险评估

### 已确认关闭

- 跨角色切换瞬态显示上一账号实例管理数据；
- 低权限账号因旧 `currentPage` 请求管理端点；
- 普通角色通过手工请求增删改实例；
- 无实例菜单导致 options 接口被菜单权限拦截；
- options 泄漏用户名、口令、监控配置、描述或密文；
- DBA 获得 admin-only 类型锁定或 ZK 全局配置权限。

### 当前阻断风险

- 普通用户正常登录后无法使用“在线元数据审核”页面；
- 当前新增自动化会污染共享测试库，使流水线结果依赖测试顺序；
- 没有真实浏览器 E2E，后续同类状态契约回归仍可能漏检。

## 10. 再次准出条件

1. 将 `extractedResult` 清理值恢复为与初始化一致的对象，并在模板增加空值保护；
2. 真实登录后复测在线元数据审核，实例下拉、表单和按钮正常，控制台无 error；
3. 再跑四模块跨角色真实点击，全部可以检索并选择实例；
4. 修复 `test_v3_rbac_instances.py` 的权限恢复或改用临时角色；
5. 按“冒烟 → SIT → UAT → 全量”顺序连续执行，测试库无需人工恢复且 0 failed；
6. 增加真实浏览器 E2E 后再申请发布准出。

## 11. 最终意见

G 已正确修复前序敏感数据瞬态泄漏，后端权限收敛和 options 解耦继续保持有效。但本次整改的统一状态清理引入了一个用户可直接触发的页面崩溃，导致核心跨模块实例选择场景部分不可用；新增测试又缺乏状态恢复和真实浏览器覆盖。建议按本报告两项缺陷完成整改后，再进行一次针对性复测，不应以当前提交直接发布。
