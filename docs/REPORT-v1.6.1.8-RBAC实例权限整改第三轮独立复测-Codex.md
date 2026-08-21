# V1.6.1.8 RBAC 实例权限整改第三轮独立复测报告

> 整改提交：`4ad076d254ab31af5d8386c5d2cad0df687a5163`
> 提交说明：`fix(test): snapshot pre-test database state, isolate must_change_password update, and cleanup test users/roles in rbac fixture teardown`
> 复测分支：`test/rbac-third-retest`
> 复测日期：2026-08-21
> 复测执行：Codex 独立测试
> 前序报告：`docs/REPORT-v1.6.1.8-RBAC实例权限整改第二轮独立复测-Codex.md`

## 1. 准出结论

**结论：业务功能验收通过，整体仍为有条件通过。**

G 本轮关闭了上一轮 P2 的大部分问题：

1. 不再无条件清除全部用户的 `must_change_password`；
2. developer、auditor 的 `instances` 权限可以恢复为测试前的 0，而不是硬编码为 1；
3. `test_dba`、`test_dev`、`test_aud`、`test_custom` 测试用户会在 teardown 中删除；
4. 测试连接和临时创建的自定义角色会清理；
5. 冒烟后紧接 SIT 不再产生权限污染假失败。

产品功能、接口权限、真实浏览器交互和慢网络会话隔离全部通过，没有发现新的业务发布阻断问题。

但上一轮 P2 尚未完全关闭：fixture 仍复用并重置共享 `admin`，只恢复口令哈希、盐、改密标志和状态，没有恢复 `token_version`、`last_login_at`、`updated_at` 等状态。本轮实测 `token_version` 增加 1，意味着测试仍会吊销执行前已签发的管理员会话。反向顺序执行后，admin 口令还会跟随其他测试套件漂移，导致首轮活服务用例 28 条因登录失败跳过。

此外，G 没有新增真实浏览器 E2E；仓库中仍只有源码字符串契约断言。上一轮 `RETEST2-GAP-01` 未关闭。

建议口径：

- 业务功能准出：**PASS**；
- 产品发布阻断：**无**；
- 测试工程准出：**CONDITIONAL PASS**；
- `RETEST3-DEFECT-01` 与 `RETEST3-GAP-01` 应继续整改，不应标记为全部关闭。

## 2. 整改代码核对

### 2.1 已正确实现

`tests/test_v3_rbac_instances.py` 已增加：

- 执行前读取全部 `role_permissions`；
- 单个用例修改权限前读取原值并在 `finally` 中恢复；
- 仅对四个测试用户清零 `must_change_password`；
- module teardown 删除测试连接、测试用户和临时角色；
- 保存并恢复 admin 的口令哈希、盐、`must_change_password` 和 `status`；
- DBA 临时实例增加二次清理。

这些改动能够关闭上一轮已经实测的普通用户与菜单权限污染。

### 2.2 尚未完整实现

1. admin 快照只包含 `password_hash`、`salt`、`must_change_password`、`status`；
2. `reset_password()` 会递增 `token_version`、更新 `updated_at` 并清理锁定状态，登录又会更新 `last_login_at`；
3. teardown 没有恢复上述字段；
4. 多处清理仍使用 `except Exception: pass`，清理失败不会令测试失败；
5. 固定用户名和固定角色 ID 仍可能与共享测试库已有数据冲突；
6. 本次提交只修改 `tests/test_v3_rbac_instances.py`，没有增加真实浏览器测试文件或浏览器测试依赖。

## 3. 测试环境

| 项目 | 实际值 |
|---|---|
| 应用版本 | `1.6.1.8` |
| Git 基线 | `4ad076d254ab31af5d8386c5d2cad0df687a5163` |
| Python | `3.14.6` |
| Node.js | `v24.18.0` |
| 元数据数据库 | MySQL `8.0.45`，`tdsql_sqlcheck_test` |
| Web 服务 | Uvicorn，`AUTH_ENABLED=true`，`SCHEDULER_ENABLED=false` |
| 服务地址 | `http://127.0.0.1:18080` |
| 浏览器 | Codex In-app Browser / Chromium 151 |
| 测试角色 | admin、dba、实例管理只读、无实例管理菜单跨模块角色 |

页面测试使用标记实例 `QA-RBAC-ROUND3-MARKER`，由 DBA 编辑为 `QA-RBAC-ROUND3-MARKER-DBA`，敏感用户名为 `qa_round3_sensitive`。全部临时数据已在结束后清理。

## 4. 自动化回归结果

| 阶段 | 首轮结果 | 活服务补跑 | 最终唯一用例 |
|---|---:|---:|---:|
| 静态检查 | `git diff --check`、`node --check` 通过 | 无 | PASS |
| 冒烟 | `47 passed` | 无 | **47 passed** |
| SIT | `369 passed / 11 skipped` | 11 条通过 | **380 passed** |
| UAT | `308 passed / 17 skipped` | 17 条通过 | **325 passed** |
| 全量回归 | `1278 passed / 28 skipped` | 28 条通过 | **1306 passed** |
| 活服务规则页 | 首轮 `8 passed / 28 skipped` | 规范化 admin 测试口令后 `36 passed` | PASS |

首轮活服务的 28 条跳过不是业务失败，而是反向顺序测试后共享 admin 口令漂移，登录夹具按设计跳过。将 admin 恢复为项目约定的 `Test@2026Admin`、重启服务后，36 条全部通过。该现象作为测试隔离证据记录在 `RETEST3-DEFECT-01`。

## 5. 测试隔离专项

### 5.1 普通用户和角色权限哨兵

执行前构造：

- `qa_round3_password_canary.must_change_password=1`；
- `developer.instances=0`；
- `auditor.instances=0`。

执行 `tests/test_v3_rbac_instances.py` 后：

| 状态 | 执行前 | 执行后 | 结论 |
|---|---:|---:|---|
| 哨兵用户 `must_change_password` | 1 | 1 | PASS |
| developer.instances | 0 | 0 | PASS |
| auditor.instances | 0 | 0 | PASS |
| 测试用户残留数 | 3 个历史残留 | 0 | PASS，历史残留也被清理 |

上一轮 `RETEST2-DEFECT-01` 中这三类污染已关闭。

### 5.2 共享 admin 状态哨兵

同一次测试前后 admin 结果：

| 字段 | 执行前 | 执行后 | 结论 |
|---|---|---|---|
| password_hash | 基线值 | 与基线相同 | PASS |
| salt | 基线值 | 与基线相同 | PASS |
| must_change_password | 0 | 0 | PASS |
| status | active | active | PASS |
| token_version | 1206 | 1207 | **FAIL** |
| last_login_at | 16:38:10 | 16:42:02 | **发生变更** |
| updated_at | 16:38:10 | 16:42:09 | **发生变更** |

`token_version` 是令牌吊销版本。增加 1 会使执行前签发的 admin Token 失效，因此不是单纯时间戳噪声。

### 5.3 双顺序回归

顺序一：冒烟（包含 V3 RBAC）→ SIT：

- 冒烟 47 passed；
- 冒烟后测试用户 0 个；
- developer、auditor 权限保持基线；
- SIT 369 passed / 11 skipped。

顺序二：`test_v2_sit.py` → `test_v3_rbac_instances.py`：

- `test_v2_sit.py`：11 passed；
- V3 RBAC：14 passed；
- 执行后测试用户 0 个，权限保持基线；
- 但 V3 RBAC 会把前一套件留下的 admin 口令状态当成原值恢复，使后续约定使用另一测试口令的活服务用例无法登录。

菜单权限顺序依赖已关闭，共享 admin 凭据顺序依赖仍存在。

## 6. 真实浏览器验收

### 6.1 在线元数据审核回归

从登录页真实输入 admin 用户名、口令并点击登录，不刷新页面，直接进入“SQL审核 → 在线元数据审核”：

- 页面表单完整；
- 实例下拉正常；
- 范围复选框和执行按钮正常；
- 控制台 error 数为 0；
- `extractedResult.filename` 空值异常未复现。

### 6.2 实例管理角色矩阵

| 角色 | 全字段查看 | 新建 | 编辑 | 删除 | 连接 | ZK 配置 | 类型锁定 |
|---|---:|---:|---:|---:|---:|---:|---:|
| admin | 是 | 是 | 是 | 是 | 是 | 是 | 是 |
| dba | 是 | 是 | 是 | 是 | 是 | 否 | 否 |
| 实例只读角色 | 是 | 否 | 否 | 否 | 否 | 否 | 否 |
| 无实例管理菜单角色 | 否 | 否 | 否 | 否 | 否 | 否 | 否 |

真实点击完成：admin 新建并保存实例；DBA 编辑名称；DBA 点击删除出现二次确认并取消；只读角色的管理入口数量均为 0。

### 6.3 无实例菜单的四模块读取

| 模块 | 标记实例存在 | 真实选中 | 按钮状态 | 控制台错误 |
|---|---:|---:|---|---:|
| 在线元数据审核 | 是 | 成功 | 执行按钮可用 | 0 |
| 扫描任务 | 是 | 成功 | 查询按钮可用 | 0 |
| 上线检查 | 是 | 成功 | 执行按钮可用 | 0 |
| 大表治理 | 是 | 成功 | 刷新按钮可用 | 0 |

实例下拉未显示 `qa_round3_sensitive`。

### 6.4 800ms 慢网络降权切换

同一标签页由 DBA 切换为无实例管理菜单角色：

| 采样点 | 实例管理菜单 | 标记实例 | 敏感用户名 | 用户名列 |
|---:|---:|---:|---:|---:|
| 100ms | 0 | 0 | 0 | 0 |
| 950ms | 0 | 0 | 0 | 0 |
| 1850ms | 0 | 0 | 0 | 0 |

网络请求包含 `/auth/login`、`/auth/visible-menus`、`/connections/options`，不包含全字段 `/connections`。跨角色敏感数据泄漏继续保持关闭。

## 7. 活服务接口矩阵

| 场景 | admin | dba | 实例只读 | 无实例菜单 |
|---|---:|---:|---:|---:|
| `/connections/options` | 200 | 200 | 200 | 200 |
| `/connections` | 200 | 200 | 200 | 403 |

- 未认证 options 返回 401；
- 四角色各读取 15 条、每条严格 8 个允许字段；
- options 不含用户名、口令、监控配置、描述或密文；
- 两个普通角色各执行 18 个管理/探测/ZK 动作，共 36 次全部返回 403；
- 越权请求前后实例快照一致；
- DBA 新建、更新、删除临时实例均为 200；
- DBA 调用 admin-only 类型锁定和 ZK 配置均为 403。

## 8. 剩余问题与修改意见

### RETEST3-DEFECT-01：共享 admin 状态仍被 RBAC fixture 改写

- **级别：P2 / 测试隔离**
- **范围：** `tests/test_v3_rbac_instances.py`
- **业务运行影响：** 无，仅执行测试时触发
- **稳定复现：** 是

根因是 fixture 调用：

```python
auth_service.reset_password("admin", STRONG_PW, operator="test")
```

随后虽然恢复口令哈希和盐，但没有恢复 `token_version`、`last_login_at`、`updated_at` 等完整用户状态。更根本的问题是测试不应复用共享 admin。

推荐直接改为专用临时管理员：

1. 生成唯一用户名，例如 `test_rbac_admin_<uuid>`；
2. 通过 `create_user(..., role="admin")` 创建；
3. 只对该临时用户清零 `must_change_password`；
4. 全部 admin 场景使用该用户 Token；
5. teardown 删除临时管理员；
6. 不再调用 `reset_password("admin", ...)`；
7. 所有用户名和自定义角色 ID 使用唯一后缀，避免碰撞；
8. 清理异常不得静默吞掉，应收集并令测试失败。

完成后增加数据库前后断言：真实 admin 的整行数据和 token 可用性必须保持不变。

### RETEST3-GAP-01：真实浏览器 E2E 仍未落库

- **级别：P2 / 测试债务**
- 本次提交只修改一个 Python 后端测试文件；
- `test_frontend_security_contract()` 仍仅搜索源码字符串；
- 仓库没有自动执行登录、菜单点击、实例选择、按钮矩阵、控制台 error 或慢网络采样。

建议新增独立浏览器 E2E 套件，至少固化本报告第 6 节的全部场景，并在 CI 中启动鉴权服务后执行。

## 9. 环境清理

已完成：

- 删除第三轮 QA 用户、角色、哨兵用户；
- 删除页面标记实例和 DBA API 临时实例；
- developer、auditor 权限恢复当前测试库基线；
- 测试用户残留数为 0；
- admin 恢复项目约定测试口令且 `must_change_password=0`；
- 关闭浏览器标签和 Uvicorn 服务；
- 确认 18080 端口释放。

## 10. 最终意见

用户提出的两个核心需求已经完整实现并通过第三轮真实验收：普通角色不能管理实例，但即使没有实例管理菜单，仍可在其他业务模块选择实例。当前无产品功能或权限安全阻断，可按业务口径准出。

G 本轮确实修复了普通用户、菜单权限和测试身份残留，但共享 admin 状态仍有可测副作用，浏览器 E2E 也没有进入仓库。建议继续按本报告两项意见整改，整改范围仅限测试工程，不需要再改业务权限实现。
