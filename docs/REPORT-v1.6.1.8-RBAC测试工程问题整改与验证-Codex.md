# V1.6.1.8 RBAC 测试工程问题整改与验证报告

> 前序报告：`docs/REPORT-v1.6.1.8-RBAC实例权限整改第三轮独立复测-Codex.md`
>
> 整改基线：`6cebcfa63469dea5203254d12b52cf17fc75f4d3`
>
> 整改分支：`test/rbac-third-retest`
>
> 整改与验证日期：2026-08-21
>
> 执行人：Codex

## 1. 结论

前序报告中的两项测试工程问题均已完成整改并通过验证：

| 编号 | 问题 | 结果 |
|---|---|---|
| RETEST3-DEFECT-01 | RBAC fixture 改写共享 admin 状态及会话 | **CLOSED** |
| RETEST3-GAP-01 | 真实浏览器 E2E 未落库、未进入 CI | **CLOSED** |

最终结论：**PASS，可关闭两项遗留问题。**

本次没有修改 RBAC 业务判定逻辑；变更集中于测试隔离、浏览器测试可观测性和 CI 自动执行能力，不改变现有用户操作流程与视觉体验。

## 2. RETEST3-DEFECT-01 整改

### 2.1 不再使用共享 admin

`tests/test_v3_rbac_instances.py` 已完成以下调整：

1. 删除 `ensure_bootstrap_admin()`、`reset_password("admin", ...)` 及真实 admin 的登录行为；
2. 每次运行使用 UUID 创建唯一临时 admin、DBA、developer、auditor、普通用户和自定义角色；
3. admin-only 场景使用临时 admin 令牌，不再依赖测试库中真实 admin 的口令；
4. 需要切换菜单权限的场景只操作唯一临时自定义角色，不再改写 developer、auditor 等内置角色权限；
5. 临时实例 ID、角色 ID、用户名均按运行唯一，不与并发测试或历史残留冲突；
6. `AUTH_ENABLED` 恢复为测试前原值，不再固定写回 `false`。

### 2.2 严格状态保护

fixture 在执行前对真实 admin 做整行只读快照，并按其当前 `token_version` 签发旧令牌探针。setup 和 teardown 分别断言：

- admin 整行数据完全相同；
- 测试前签发的 admin 令牌访问 `/api/v1/auth/me` 仍返回 200；
- developer、auditor 的 `instances` 权限没有变化；
- 临时用户、角色、实例和对应审计日志全部为 0 残留。

所有 teardown 异常均收集并使测试失败；不再使用 `except Exception: pass` 静默吞掉清理错误。允许忽略的情况仅限资源已经不存在。

### 2.3 实测证据

受控前后对比结果：

```text
pytest_exit=0
admin_full_row_equal=True
admin_old_token_status=200
builtin_instance_permissions_equal=True
```

定向测试：`15 passed`。

反向顺序测试：

```text
tests/test_v2_sit.py              11 passed
tests/test_v3_rbac_instances.py  15 passed
```

V3 RBAC 测试不再依赖前序套件留下的 admin 口令，也不会继续改变 admin 会话版本。

## 3. RETEST3-GAP-01 整改

### 3.1 真实浏览器 E2E 套件

新增 `tests/e2e/test_rbac_instances_browser.py`，使用 Playwright Chromium 启动真实 Uvicorn 服务并执行以下四组场景：

1. **管理员与 DBA 管理矩阵**
   - 管理员可见新增、编辑、删除、ZK 配置和类型锁定；
   - DBA 可见新增、编辑、删除，不可见 admin-only 控件；
   - DBA 通过页面真实完成一次新增、编辑、删除闭环；
   - 删除标记实例时管理员执行真实点击并取消确认，验证数据仍存在。
2. **普通只读角色**
   - 分配实例管理菜单后可以查看实例；
   - 新增、编辑、删除、操作列、ZK 配置全部不可见。
3. **无实例管理菜单角色**
   - 侧边栏不存在“实例管理”；
   - 在线元数据审核、扫描任务、上线检查、大表治理四个模块均能打开实例下拉列表、选择标记实例并启用对应操作按钮；
   - 页面不出现管理列表中的实例用户名等高权限缓存数据。
4. **800ms 慢网络跨角色切换**
   - 从 DBA 登出后登录无实例菜单角色；
   - 100ms、950ms、1850ms 三个时间点均不出现实例管理菜单、管理列表标记或敏感用户名；
   - 新角色请求 `/connections/options`，不请求 `/connections` 全量管理接口；
   - 无 JavaScript 运行时错误。

前端只增加稳定的 `data-testid` 测试钩子，不增加页面元素、不修改样式、不改变按钮权限或交互行为。

### 3.2 测试数据隔离

浏览器 E2E 同样使用唯一临时用户、角色和实例，不登录或修改共享 admin。服务关闭后严格验证：

- 临时用户数为 0；
- 临时角色数为 0；
- 标记实例和页面创建实例数为 0；
- 本轮临时身份对应审计日志数为 0；
- 共享 admin 整行数据不变且旧令牌仍有效。

### 3.3 CI 自动执行

新增 `.github/workflows/rbac-browser-e2e.yml`：

- push、pull request 和手工触发均可执行；
- 仅在后端、前端、RBAC 测试、E2E、依赖或工作流自身变更时触发；
- 使用 MariaDB 11.4 独立服务库；
- 安装项目依赖及 Playwright 固定版本 Chromium；
- 先执行隔离后的 V3 RBAC API 套件，再执行真实浏览器 E2E。

普通 `pytest tests` 默认明确跳过浏览器测试，避免开发机未安装浏览器时产生伪失败；CI 通过 `RUN_BROWSER_E2E=1` 强制执行，缺少 Playwright 或浏览器时会直接失败而不是跳过。

## 4. 回归结果

| 测试层级 | 结果 |
|---|---:|
| V3 RBAC 定向 | 15 passed |
| 冒烟 | 48 passed |
| SIT | 369 passed / 11 skipped |
| UAT | 308 passed / 17 skipped |
| 真实浏览器 E2E | 4 passed |
| 全量回归 | 1279 passed / 32 skipped |
| 硬编码凭据守卫 | 2 passed |
| Python 编译 / JS 语法 / Git whitespace | PASS |
| CI YAML 解析 | PASS |

全量回归的 32 条跳过包含 4 条浏览器 E2E，这是普通测试入口的预期行为；独立浏览器入口已经执行并 4/4 通过。其余跳过项和 10 条 warning 均为仓库原有条件用例或依赖弃用提示，本次未新增产品错误。

## 5. 变更文件

- `tests/test_v3_rbac_instances.py`
- `tests/e2e/test_rbac_instances_browser.py`
- `frontend/index.html`
- `pyproject.toml`
- `.github/workflows/rbac-browser-e2e.yml`
- `.gitignore`

## 6. 最终意见

两项测试工程问题已经形成可重复、可失败、可在 CI 持续执行的闭环：真实 admin 不再是测试夹具，真实浏览器覆盖也不再依赖人工复测。

建议将 `RETEST3-DEFECT-01`、`RETEST3-GAP-01` 均标记为关闭。本次整改不需要再修改业务权限实现。
