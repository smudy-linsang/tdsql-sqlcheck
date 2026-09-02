# UAT-FIX-v1.6.3.0 深度诊断·表类型统计（G14）第一轮 UAT 整改完成情况报告

| 项 | 内容 |
|---|---|
| 整改对象 | `UAT-v1.6.3.0-G14表类型统计第一轮用户验收测试报告-智能体O.md`（提交 `e978cba`，结论：不通过，1 BLOCK + 2 MAJOR + 1 MINOR） |
| 整改基线 | `main` / `0f01346`（第二轮 SIT 整改提交） |
| 整改人 | 智能体 Q |
| 整改日期 | 2026-09-02 |
| 设计定版 | `DESIGN-v1.6.3.0-…详细设计说明书.md` **Rev.O → Rev.P**（UAT 整改定版） |
| **整改结论** | **四项缺陷全部关闭，回归 1755 全绿，浏览器复测 5 路径全过，可再次提交 UAT** |

---

## 1. 整改总览

| 级别 | 编号 | 问题 | 整改方式 | 状态 |
|---|---|---|---|---|
| **BLOCK** | O-G14-01 | 结果未绑定用户/实例/查询条件，旧结果跨场景残留（含跨用户会话隔离失效） | 按报告 6 步施工 + 9 项静态门禁 | ✅ 关闭 |
| MAJOR | O-G14-02 | 发布标识仍为 1.6.2.2 | 七处标识统一提升 1.6.3.0 + 一致性门禁 4 项 | ✅ 关闭 |
| MAJOR | O-G14-04 | 离线实例在连接解析阶段穿透为未处理 500 | `_pool()` 纳入完整异常边界 + `translate_db_error` 白名单映射 422 + 3 条测试 | ✅ 关闭 |
| MINOR | O-G14-03 | auditor 按钮可点且 403 文案被吞 | 统一 `apiErrorMessage()`（三处接入）+ auditor 按钮禁用并提示 | ✅ 关闭 |

---

## 2. 逐项整改明细

### 2.1 O-G14-01（BLOCK）—— 结果绑定用户/实例/查询条件

按报告 §5.1"必须按以下顺序修改"的 6 步逐条落实（全部在 `frontend/static/js/app.js`
与 `frontend/index.html` 的 G14 页签块内）：

| # | 报告要求 | 落实 |
|---|---|---|
| ① | 统一清理函数 | 新增 `resetTableTypeState()`，一次性清理 8 个状态点（结果/告警展开/抽屉/历史/明细×2/明细展开/明细 loading） |
| ② | 查询开始即失效旧结果 | `runTableTypeStats()` 先快照 `{username, connectionId, database, sequence}` 再 `resetTableTypeState()`，然后才发请求；接口错误时保持空态 |
| ③ | 防异步串台 | 每次统计递增 `tabletypeSeq`；响应回来时序号或范围快照（用户/实例/规范化库名）任一不同即**丢弃** |
| ④ | 实例/库名变化即清空 | 新增 `watch([deepConnId,deepDb])`：序号递增作废在途响应 + 清空实时结果；实例变化连带关闭历史抽屉并清空历史/明细 |
| ⑤ | 登录态彻底隔离 | `clearRoleScopedState()` 增补：`savedConnections`/`currentConnectionId`/`deepConnId`/`deepRightConnId`/`deepDb`/`deepLoading` 清空、`deepResult` 全键置 null、调用 `resetTableTypeState()`、`localStorage.removeItem('tdsql_conn')` |
| ⑥ | 展示范围绑定 | 新增 `tabletypeScopeMatch`/`tabletypeView`：结果与当前上下文不一致一律不渲染；摘要旁显示"结果范围：实例名（host:port）/ 库 / 采集时间" |

**自动化门禁（新增 `tests/test_g14_frontend_state_binding.py`，9 项静态断言）**：
统一清理点 8 项齐全 / reset 先于请求 / 序号守卫与范围快照 / watch 失效 /
登录态隔离 9 个清理点 / 模板不再直引 `deepResult.tabletype` / auditor 禁用 /
`apiErrorMessage` 三处调用点 / setup 返回清单登记——**删掉任一清理点即红灯**
（满足报告"只在错误回调里加一行不算关闭"的关闭标准）。

**浏览器复测（真实 Chromium，5 条路径全过）**：

| 路径 | 实测 |
|---|---|
| 成功后查不存在库 | 弹"数据库不存在…"，旧汇总/明细/范围**全部清空** ✅ |
| 清空输入不重现旧结果 → 查 bank_enrich 正常 | ✅ |
| 只改输入（bank_enrich→tdsql_test）不点击 | 旧结果**立即消失**（watch 生效）✅ |
| 查 tdsql_test 后切换实例 | 切换瞬间旧结果全部清空 ✅ |
| 结果范围行 | 显示"实例名（host:port）/ 库 / 采集时间" ✅ |

> 报告路径 3（跨用户登录态隔离）依赖多角色真实账号环境，本地沙箱无现成账号体系，
> 已由静态门禁钉住 `clearRoleScopedState` 全部隔离点；真实多账号复测移交第二轮 UAT。

### 2.2 O-G14-02（MAJOR）—— 发布标识提升 1.6.3.0

| 位置 | 变更 |
|---|---|
| `VERSION` | 1.6.2.2 → 1.6.3.0 |
| `backend/config.py` | `APP_VERSION` → 1.6.3.0；`APP_DESCRIPTION` 更新为 G14 版本说明 |
| `frontend/index.html` | title / 登录页版本行 / 2 处 CSS 缓存参数 / 1 处 app.js 缓存参数 |

历史注释中的 `v1.6.2.2-UAT-*` 缺陷追踪标识**未批量替换**（报告明确排除）。
`/health`、OpenAPI、系统信息、启动日志本就取自 `config.APP_VERSION`，随之一致。

**新增 `tests/test_version_consistency.py`（4 项）**：VERSION↔APP_VERSION /
/health↔OpenAPI / 前端五处标识 / 七处同源汇总断言，另带反向护栏
（静态资源缓存参数不得残留旧版本号）。

**实测证据**：新进程启动后 `/health` 返回 `{"status":"ok","version":"1.6.3.0"}`；
登录页 HTML 的 title/版本行/缓存参数全部 1.6.3.0；启动日志 `V1.6.3.0`。

### 2.3 O-G14-03（MINOR）—— auditor 按钮与错误文案

- 新增统一 `apiErrorMessage(data, fallback)`：字符串 `detail` → `message` →
  FastAPI 校验数组首条 `msg` → 兜底；禁止 `[object Object]`。
- `_deepPost`（错误分支）、`openTableTypeHistory`、`loadTableTypeHistoryDetail`
  **三处统一接入**（不只局部修一处）。
- 新增 `canRunTableTypeStats`：`auditor` 或无菜单权限者按钮**禁用**并
  tooltip 提示"审计员仅可查看历史"；后端 403 保持不变（前端只是体验层，
  不依赖前端做授权）。

### 2.4 O-G14-04（MAJOR）—— 离线实例异常边界

- `pool = _pool(body.connection_id)` 移入路由完整 `try` 边界（原在 try 之外，
  连接异常绕过全部 except 穿透成裸 500）。
- 复用既有 `backend/services/connection_errors.py::translate_db_error` **严格白名单**：
  连接类 errno（2003/2004/2005/2006/2013/1045/1049 等）→ 422 可读提示
  "实例连接失败：请检查地址、端口、网络和账号；本次未产生统计结果"；
  **未知程序异常（RuntimeError 等）原样抛出仍为 500**，绝不伪装成连接失败
  （口径与 `daily_inspect.py` 的 v1.6.2.2-UAT-O-19/O-24 完全一致）。
- 响应**不回带主机/口令/驱动原文**（冒烟实测曾发现首版文案带出了驱动消息中的
  host IP，已修正为纯业务文案并加测试断言）；完整堆栈留服务端日志 +
  X-Request-ID 关联。

**新增 3 条测试（`tests/test_table_type_stats.py`）**：

| 用例 | 断言 |
|---|---|
| `test_uat04_offline_instance_maps_to_readable_422` | `OperationalError(2003)` → 422 可读、**不发起采集**、响应不含主机/驱动原文 |
| `test_uat04b_timeout_and_auth_map_to_stable_422` | 2013 超时 → "实例连接失败"；1045 → "实例认证失败"；均 422 |
| `test_uat04c_runtime_error_still_fails_closed_500` | `RuntimeError` → 500 失败关闭；响应不回带异常串；日志含完整堆栈 |

**冒烟实测**：离线实例（127.0.0.1:1）`POST /run` → HTTP 422 可读提示，
不落历史；同连接随后切回可用实例统计正常。

---

## 3. 变更清单

| 文件 | 变更 | 对应缺陷 |
|---|---|---|
| `backend/api/table_type_stats.py` | +41 行（异常边界 + 白名单映射 + 日志化 500） | O-G14-04 |
| `frontend/static/js/app.js` | 结果绑定六步 + `apiErrorMessage` + `canRunTableTypeStats` | O-G14-01 / 03 |
| `frontend/index.html` | G14 页签块范围绑定 + auditor 禁用 + 5 处发布标识 | O-G14-01 / 02 / 03 |
| `VERSION` / `backend/config.py` | 1.6.3.0 | O-G14-02 |
| `tests/test_table_type_stats.py` | +81 行（3 条 uat04 用例 + docstring Rev.P） | O-G14-04 |
| `tests/test_g14_frontend_state_binding.py` | 全新 9 项静态门禁 | O-G14-01 / 03 |
| `tests/test_version_consistency.py` | 全新 4 项一致性门禁 | O-G14-02 |
| `docs/DESIGN-…-Rev.P` | 头部/§0/§4.4/§5 错误表/§8 E-38/§9.1/§9.3/§11/§12.8/修订记录；附录 A.2/A.4 与仓库逐字同步 | 全部 |

**冻结面核查**：`backend/services/table_type_stats_service.py`、
`backend/schema/v13/130_table_type_stats.sql`、`backend/engine/**`、119 条规则、
`auth_service.py`、其余 9 个深度诊断子模块**本轮零改动**；迁移文件一字未动。

---

## 4. 验证证据

### 4.1 回归测试（本地 MySQL 8 元数据库）

| 门禁 | 结果 |
|---|---|
| `tests/test_table_type_stats.py` | **115 passed**（112 + 3 条 uat04） |
| rbac/路由/附录一致性/版本/前端静态 五组合计 | **24 passed** |
| 规则组 | **105 passed** |
| 全量 `tests/` | **1755 passed, 0 failed, 0 skipped**（整改前 1739，净增 16 即本次新增） |

### 4.2 整改专项冒烟（真实 HTTP + 真实浏览器）

- **版本**：`/health` / OpenAPI / 登录页 title / 版本行 / 缓存参数全部 1.6.3.0 ✅
- **离线实例**：422 可读提示、不含主机/凭据、不落历史 ✅
- **结果绑定**：浏览器 5 路径全过（见 §2.1 表；控制台无 JS 异常）✅
- **端到端不回归**：集中式实例 57 库 / 2207 表、恒等式成立、无失败 ✅

### 4.3 附录↔仓库一致性

`test_design_appendix_matches_repo.py` 4 项全绿：附录 A.1（1178 行）/
A.2（118 行）/ A.3（46 行）/ A.4（2555 行）与仓库逐字一致——Rev.P 的
api 与测试改动已完整回填设计文档。

---

## 5. 遗留事项（移交第二轮 UAT）

| 项 | 说明 |
|---|---|
| O-G14-01 路径 3（跨用户真实账号隔离） | 本地无多角色账号环境，静态门禁已钉住全部隔离点；请第二轮 UAT 以 developer→auditor/最小权限角色复测 |
| 离线实例浏览器路径 | HTTP 层已验证 422；浏览器侧"loading 恢复/旧结果为空/可切回继续查询"请第二轮 UAT 复核 |
| 内网 `lzbj_ecif` 六数字对账 + T20 性能证据 | 需真实 TDSQL 环境，属发布前门禁（非本轮整改范围） |
| 免认证模式下前端登录页显隐 | 浏览器复测时发现：`AUTH_ENABLED=false` 时前端仍因 localStorage 无 token 停在登录页（平台既有行为，非 G14 引入，非本轮缺陷范围）——建议平台层另案评估 |

---

## 6. 结论

第一轮 UAT 的 1 BLOCK + 2 MAJOR + 1 MINOR **全部关闭**，且每一项都有可执行证据：
BLOCK 有真实浏览器 5 路径截图与静态门禁；版本有七处同源门禁与实测；
离线 422 有 3 条行为测试与 HTTP 冒烟；auditor 体验有静态门禁与文案统一改造。
全量回归 1755 通过、零失败、冻结面零改动、附录与仓库逐字一致。

**请求再次提交 UAT。**
