# V1.6.1.8 RBAC 实例权限整改第二轮独立复测报告

> 整改提交：`6d7c70aaf2ca782d7211a1378f11eca20397a984`
> 提交说明：`fix(frontend,test): fix extractedResult object contract in clearRoleScopedState and ensure role permission teardown restore`
> 复测分支：`test/rbac-second-retest`
> 复测日期：2026-08-21
> 复测执行：Codex 独立测试
> 前序报告：`docs/REPORT-v1.6.1.8-RBAC实例权限整改独立复测-Codex.md`

## 1. 准出结论

**结论：业务功能验收通过，整体有条件通过。**

G 已修复前序 P1 前端回归：从登录页正常登录后，“在线元数据审核”页面能够完整渲染实例下拉、目标库、范围选项和执行按钮，控制台无异常；无“实例管理”菜单的普通角色可以在在线元数据审核、扫描任务、上线检查和大表治理四个模块中检索并选择实例。

原 RBAC 权限目标也继续满足：只有 admin、dba 可以新建、编辑、删除或连接实例；拥有“实例管理”菜单的其他角色只能查看；没有“实例管理”菜单的角色不能访问管理列表，但可以读取不含敏感字段的实例选项。

自动化按“冒烟 → SIT → UAT → 全量”连续执行全部通过，上一轮由 `developer.instances=0` 引发的 SIT 假失败不再出现。

但是，测试隔离整改仍有 **1 项 P2 未完全关闭**：`tests/test_v3_rbac_instances.py` 会将共享测试库中所有用户的 `must_change_password` 清为 0，并将 developer、auditor 的原始实例菜单权限硬改为 1，而不是恢复执行前快照。本轮通过哨兵试验稳定复现。该问题不影响运行时业务功能，但会污染持久化测试库，不能把“测试隔离缺陷”标记为彻底关闭。

因此建议口径如下：

- 业务功能准出：**PASS**；
- 产品发布阻断：**无**；
- 测试工程准出：**CONDITIONAL PASS**，P2 必须进入最近一次整改；
- 若项目规定发布前必须无 P2，则本轮仍应视为未完全准出。

## 2. 整改代码核对

### 2.1 前端状态契约修复

整改方向正确：

1. `frontend/static/js/app.js` 的 `clearRoleScopedState()` 将 `extractedResult.value` 从 `null` 改为 `{}`，与 `const extractedResult=ref({})` 的初始类型一致；
2. `frontend/index.html` 将结果区域条件改为 `extractedResult && extractedResult.filename`，增加模板空值保护；
3. `bigtableData` 的初始化和清理均使用 `null`，模板通过 `bigtableData || []` 兜底，类型契约一致。

真实浏览器验证表明，前序 `Cannot read properties of null (reading 'filename')` 已消失。

### 2.2 测试权限恢复修复

G 为修改 developer、auditor 权限的测试增加了 `try/finally`，模块 teardown 也恢复内置角色权限。对于当前标准测试库中 `instances=1` 的初始状态，这能避免上一轮“冒烟后执行 SIT”失败。

但恢复值被硬编码为 1，并且 fixture 仍存在无条件更新全部用户的 SQL，因此只解决了默认数据下的执行顺序问题，没有实现严格的状态快照与回滚。详见 `RETEST2-DEFECT-01`。

## 3. 测试环境

| 项目 | 实际值 |
|---|---|
| 应用版本 | `1.6.1.8` |
| Git 基线 | `6d7c70aaf2ca782d7211a1378f11eca20397a984` |
| Python | `3.14.6` |
| Node.js | `v24.18.0` |
| 元数据数据库 | MySQL `8.0.45`，独立测试库 `tdsql_sqlcheck_test` |
| Web 服务 | Uvicorn，`AUTH_ENABLED=true`，`SCHEDULER_ENABLED=false` |
| 服务地址 | `http://127.0.0.1:18080` |
| 浏览器 | Codex In-app Browser / Chromium 151 |
| 测试角色 | admin、dba、实例管理只读角色、无实例管理菜单的跨模块角色 |

真实页面创建唯一标记实例 `QA-RBAC-ROUND2-MARKER`，由 DBA 编辑为 `QA-RBAC-ROUND2-MARKER-DBA`，敏感用户名为 `qa_round2_sensitive`。测试结束后已全部删除。

## 4. 自动化测试结果

| 阶段 | 首轮结果 | 活服务补跑 | 最终唯一用例结论 |
|---|---:|---:|---:|
| 静态检查 | `git diff --check`、`node --check` 通过 | 无 | PASS |
| 冒烟 | `47 passed` | 无 | **47 passed** |
| SIT | `369 passed / 11 skipped` | 11 条活服务用例通过 | **380 passed** |
| UAT | `308 passed / 17 skipped` | 17 条活服务用例通过 | **325 passed** |
| 全量回归 | `1278 passed / 28 skipped` | 28 条跳过项全部通过 | **1306 passed** |
| 活服务规则页批次 | 无 | `36 passed` | PASS |

跳过项全部来自需要已经启动且可登录服务的规则页用例，已在 `127.0.0.1:18080` 上补跑，不存在未执行项。

### 4.1 测试顺序回归

执行顺序一：

1. 冒烟批次（包含 `test_v3_rbac_instances.py`）：47 passed；
2. 查询 `developer.instances`：1；
3. 紧接着执行完整 SIT：369 passed / 11 skipped；
4. 单独执行 `test_v2_sit.py`：11 passed。

执行顺序二：

1. `test_v2_sit.py`：11 passed；
2. `test_v3_rbac_instances.py`：14 passed；
3. 查询 `developer.instances`：1。

上一轮默认测试库下的顺序依赖故障已关闭。

## 5. 真实浏览器专项验收

### 5.1 登录后在线元数据审核

从无会话登录页输入 admin 用户名和口令，点击“登录”，不刷新浏览器，直接点击“SQL审核 → 在线元数据审核”：

- 四个页签正常显示；
- “选择目标实例”下拉正常显示；
- 目标数据库输入框正常显示；
- 表结构、索引/主键、视图定义、分片键范围正常显示；
- 执行按钮正常显示；
- 浏览器控制台 error 数为 0；
- 不再出现 `extractedResult.filename` 空值异常。

前序 `RETEST-DEFECT-01`：**已关闭。**

### 5.2 实例管理角色矩阵

| 角色 | 查看全字段列表 | 新建 | 编辑 | 删除 | 连接 | ZK 配置 | 类型锁定 |
|---|---:|---:|---:|---:|---:|---:|---:|
| admin | 是 | 是 | 是 | 是 | 是 | 是 | 是 |
| dba | 是 | 是 | 是 | 是 | 是 | 否 | 否 |
| 实例管理只读角色 | 是 | 否 | 否 | 否 | 否 | 否 | 否 |
| 无实例管理菜单角色 | 否 | 否 | 否 | 否 | 否 | 否 | 否 |

真实点击覆盖：

1. admin 点击“+ 新建连接”，填写连接名、地址、端口、用户名、口令、数据库和描述并保存成功；
2. DBA 登录后看到标记实例，点击“编辑”并更新名称成功；
3. DBA 点击“删除”，正常出现二次确认框，本轮点击“取消”，实例未被删除；
4. DBA 不显示“ZK发现配置”和“锁定类型”；
5. 实例只读角色能看到标记实例和用户名列，但新建、编辑、删除、连接、操作列和 ZK 入口均不存在。

### 5.3 无实例管理菜单的四模块实例选择

| 模块 | 标记实例选项 | 真实选择 | 操作状态 | 控制台错误 | 结论 |
|---|---:|---:|---|---:|---|
| 在线元数据审核 | 有 | 成功 | 执行按钮由禁用变为可用 | 0 | PASS |
| 扫描任务 | 有 | 成功 | 查询按钮可用 | 0 | PASS |
| 上线检查 | 有 | 成功 | 执行上线检查按钮由禁用变为可用 | 0 | PASS |
| 大表治理 | 有 | 成功 | 刷新清单按钮可用 | 0 | PASS |

四个下拉只显示实例名称、地址等精简信息，没有显示 `qa_round2_sensitive`。

### 5.4 800ms 慢网络跨角色切换

在同一浏览器标签页中先以 DBA 打开实例管理，确认页面存在标记实例、敏感用户名和管理按钮；随后施加 800ms 网络延迟，退出并登录无实例管理菜单角色。

| 采样点 | 实例管理菜单 | 标记实例 | 敏感用户名 | 用户名列 |
|---:|---:|---:|---:|---:|
| 100ms | 0 | 0 | 0 | 0 |
| 950ms | 0 | 0 | 0 | 0 |
| 1850ms | 0 | 0 | 0 | 0 |

网络请求包含：

- `/auth/login`；
- `/auth/visible-menus`；
- `/tdsql/connections/options`；

不包含全字段 `/tdsql/connections` 管理请求。前序跨角色敏感数据瞬态泄漏继续保持关闭。

## 6. 活服务接口权限矩阵

### 6.1 读取分层

| 场景 | admin | dba | 实例只读角色 | 无实例菜单角色 |
|---|---:|---:|---:|---:|
| `/connections/options` | 200 | 200 | 200 | 200 |
| `/connections` | 200 | 200 | 200 | 403 |

- 未认证访问 options 返回 401；
- 四种角色各读取到 15 条实例选项；
- 每项严格等于 8 个允许字段；
- options 不含用户名、口令、监控配置、描述或密文。

### 6.2 越权写入

实例只读角色和无实例菜单角色分别调用 18 个管理动作，包括连接、测试、断开、新建、修改、删除、设默认、激活、实例类型探测、诊断、MonitorDB 探针及四个 ZK 导入动作。

- 共 36 次请求全部返回 403；
- 请求前后实例总数、默认实例集合和标记实例快照完全一致；
- DBA 新建、更新、删除临时实例均返回 200；
- DBA 调用实例类型锁定和 ZK 全局配置均返回 403。

后端权限边界符合设计要求。

## 7. 剩余问题与修改意见

### RETEST2-DEFECT-01：RBAC fixture 仍会改写共享测试库非测试状态

- **级别：P2 / 测试可靠性与安全状态污染**
- **范围：** `tests/test_v3_rbac_instances.py`
- **业务运行影响：** 无；仅测试代码执行时触发
- **是否稳定复现：** 是

#### 证据一：无条件清除全部用户首次改密状态

fixture 中存在：

```python
conn.execute("UPDATE users SET must_change_password = 0")
```

本轮创建哨兵用户 `qa_round2_password_canary`，执行前 `must_change_password=1`。运行 `test_v3_rbac_instances.py` 后，用例自身 14 passed，但哨兵用户变为 `must_change_password=0`。

这意味着测试会修改不属于该 fixture 的用户，可能掩盖首次登录强制改密相关问题。

#### 证据二：权限恢复为硬编码默认值，而非原值

执行前将测试库中的：

- `developer.instances=0`；
- `auditor.instances=0`。

运行 `test_v3_rbac_instances.py` 后，两项均变为 1。原因是 teardown 使用：

```python
for r in ("admin", "dba", "developer", "auditor"):
    set_role_permissions(r, {"instances": 1})
```

该逻辑只能恢复“恰好等于代码默认值”的数据库，不能恢复测试执行前状态。

#### 证据三：测试身份没有完整回收

fixture 创建或复用 `test_dba`、`test_dev`、`test_aud`、`test_custom` 和 `custom_role`，teardown 仅删除测试连接，没有删除这些用户和自定义角色。持久化测试库会长期保留测试身份和权限行。

#### 可实施修改

建议将 fixture 改成严格的资源所有权模型：

1. 每次测试生成唯一角色和用户名，例如带 UUID 后缀，避免复用共享身份；
2. 不再执行无条件 `UPDATE users`，只更新本 fixture 创建的用户名：

```python
conn.execute(
    "UPDATE users SET must_change_password = 0 WHERE username IN (?, ?, ?, ?)",
    created_usernames,
)
```

3. 修改任何既有角色权限前，先读取并保存 `visible` 原值；finalizer 按原值恢复，而不是固定写 1；
4. 更推荐为无实例菜单场景创建专用临时自定义角色，避免修改 developer、auditor；
5. teardown 按“连接 → 用户 → 角色”顺序删除本 fixture 创建的全部资源；
6. 不要用裸 `except Exception: pass` 吞掉清理失败，至少汇总清理错误并使测试失败；
7. 增加数据库前后快照断言：除 operation log 等允许追加表外，用户、角色、角色权限和实例配置必须完全一致。

### RETEST2-GAP-01：真实浏览器回归仍未进入自动化套件

- **级别：P2 / 测试债务**
- G 新增的前端断言仍是源码字符串匹配，只能证明 `{}` 和空值保护文本存在；
- 本轮真实点击由独立测试人工自动化完成，仓库 CI 仍不会打开页面、登录、导航或检查控制台；
- 后续若其他状态字段出现相同类型漂移，当前静态测试仍可能漏检。

建议至少固化以下浏览器 E2E：

1. 登录后直接进入在线元数据审核，断言核心表单存在且控制台无 error；
2. 无实例管理菜单角色依次进入四个代表模块，选择同一实例并检查按钮状态；
3. admin、dba、实例只读角色的管理按钮矩阵；
4. DBA → 无实例菜单角色在 800ms 延迟下三个时间点无旧数据；
5. 断言低权限登录不请求全字段 `/connections`。

## 8. 环境清理

复测结束后已完成：

- 删除 `qa_round2_dba`、`qa_round2_view`、`qa_round2_nomenu` 和哨兵用户；
- 删除 `qa_round2_view`、`qa_round2_cross` 临时角色；
- 删除页面标记实例和 DBA API 临时实例；
- 恢复 developer、auditor 当前测试库基线权限；
- 关闭浏览器测试标签页；
- 关闭 Uvicorn 测试服务，确认 18080 端口已释放。

## 9. 最终意见

G 对前序 P1 的修复有效，用户提出的两项核心业务要求均已实现，且经过真实浏览器、活服务接口、慢网络切换和全量回归验证。当前没有发现新的产品功能或权限安全阻断问题，业务功能可以准出。

测试隔离整改尚未做到“执行前后状态一致”，应按 `RETEST2-DEFECT-01` 继续收敛；同时应将本轮真实浏览器场景固化到 CI，避免再次依赖人工复测发现前端状态契约回归。
