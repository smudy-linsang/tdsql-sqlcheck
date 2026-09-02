# UAT-FIX3-v1.6.3.0 深度诊断·表类型统计（G14）第三轮 UAT 整改完成情况报告

| 项 | 内容 |
|---|---|
| 整改对象 | `UAT3-v1.6.3.0-G14表类型统计第三轮用户验收测试报告-智能体O.md`（提交 `57d3b66`，结论：有条件通过，第二轮两项均关闭，新增 1 项 P2） |
| 整改基线 | `main` / `37ea3ea`（第二轮 UAT 整改提交） |
| 整改人 | 智能体 Q |
| 整改日期 | 2026-09-02 |
| 设计定版 | `DESIGN-v1.6.3.0-…详细设计说明书.md` **Rev.Q → Rev.R** |
| **整改结论** | **P2 已关闭：异步请求所有权补齐行为级自动化（真实浏览器 + 可控 Promise + 行为断言 + 变异证据）；全量 1765 全绿** |

---

## 1. 缺陷与整改

### UAT3-O-G14-01（P2）：异步请求所有权仍无行为级自动化

**报告指出的问题（如实承认）**：Rev.Q 新增的 `test_g14_frontend_state_binding.py`
用例读取源码文本后检查字符串与相对位置——能防误删明显标记，但**没有运行 Vue 页面、
没有制造并控制两个 Promise 的返回顺序、没有观察 toast 和按钮状态**。我第二轮整改报告
称其为"非纯字符串存在性"是不准确的自我评价（静态顺序断言只是比纯字符串存在性稍强，
仍不是行为级）。

**整改内容（按报告 §7 六步逐项）**：

| # | 报告要求 | 落实 |
|---|---|---|
| 1 | 固定浏览器自动化版本；缺浏览器不得静默 skip | `requirements.txt` 固定 `playwright==1.62.0`（含 CI 镜像须内置浏览器的注释）；测试中 Chrome/Chromium 均不可用时 `pytest.fail` **失败关闭** |
| 2 | 独立测试文件，真实服务 + 真实页面 | 新增 `tests/test_g14_request_ownership_browser.py`：子进程启动真实后端（隔离库 `tdsql_sqlcheck_test`、免认证、独立端口 18977），Playwright 驱动**仓库内的真实** `frontend/index.html` + `app.js`，无复制简化版 |
| 3 | 请求拦截精确控制时序 | `page.route("**/api/v1/table-type-stats/run")` + `_RunGate`（按序号挂起/放行）；响应体采用现有接口契约，不依赖真实数据库 |
| 4 | 四个行为用例 | ①A 延迟 422 切 B：无 A 的 error toast/结果，按钮可用 ✅；②A 未返回发起 B：A 的 finally 不关 B 的 loading，B 完成才关闭 ✅；③A 延迟 200：无 A 的成功 toast/数据，B 只显示 B 的 scope 与数据 ✅；④不切 scope 的 400/422/500：服务端可读错误正常展示、按钮恢复 ✅ |
| 5 | 断言基于可见行为 | `.el-message--error/--success` 文本与数量、结果范围行、汇总数字（111/222 标记）、按钮 `is-loading` class——无源码字符串断言 |
| 6 | 纳入默认 pytest 全量 + mutation 证据 | 文件位于 `tests/` 下，默认全量收集执行；变异验证：**删除 `runTableTypeStats` 的 2 处 isCurrent 守卫 → 用例 1 红灯（迟到错误提示复活）；恢复 → 4 项全绿** |

**实施中发现并解决的一个测试基建问题**：Playwright sync 的 route handler 与主线程
共享驱动线程，首版 handler 里 `Event.wait()` 阻塞导致主线程 `page.evaluate` 排队死锁。
`_RunGate` 改为"挂起-放行"模式：handler 只登记挂起的 route 立即返回，主线程
`release()` 时 fulfill——这是本类测试可复用的正确模式。

**静态门禁的处置**：`test_g14_frontend_state_binding.py` 保留但**降级为补充**
（防误删标记），文档与测试中均不再称其为行为级证据。

## 2. 验证证据

| 项 | 结果 |
|---|---|
| 新增行为用例 | **4 passed**（真实服务 + 真实页面 + 系统 Chrome headless） |
| mutation 验证 | 删 2 处 isCurrent 守卫 → 1 failed（缺陷复活）；恢复 → 4 passed |
| 既有静态门禁（补充） | 12 passed |
| 全量 `tests/` | **1765 passed, 0 failed, 0 skipped**（1761 → 1765，净增即 4 条行为用例） |
| 附录↔仓库一致性 | 4 项全绿（本轮 G14 模块四个文件零改动） |

## 3. 冻结面核查

`backend/**`（含 G14 服务层/API）、`frontend/**`（含 app.js/index.html）、
`backend/schema/**`、119 条规则、`auth_service.py`——**本轮全部零改动**。
变更仅：新增 1 个测试文件 + `requirements.txt` 追加固定依赖 + 设计文档 Rev.R。

## 4. 遗留事项（如实登记）

| 项 | 说明 |
|---|---|
| 内网真实 TDSQL 六数字对账 + T20 | 受控生产变更窗口执行（报告 §8 施工单），本轮不宣称已通过 |
| CI 镜像内置浏览器 | 部署侧事项：CI 镜像需内置 Chromium/Chrome，否则该测试失败关闭（按报告要求） |
| 靶场文档改进建议 | O 报告 §5.3 对靶场 README 的措辞/能力矩阵建议已转达为 G 的待办，非 G14 缺陷 |

## 5. 结论

第三轮 UAT 唯一 P2 已关闭。G14 当前状态：**SIT 两轮通过 + UAT 三轮功能缺陷全部关闭
+ 行为级自动化补齐**。按报告口径，本模块可进入"内网受控灰度验证"流程；最终 UAT 签字
待真实 TDSQL 集合对账与 T20 完成后由内网智能体签署。
