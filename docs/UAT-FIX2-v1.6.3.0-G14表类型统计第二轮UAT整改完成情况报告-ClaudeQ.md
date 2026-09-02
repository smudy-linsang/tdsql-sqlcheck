# UAT-FIX2-v1.6.3.0 深度诊断·表类型统计（G14）第二轮 UAT 整改完成情况报告

| 项 | 内容 |
|---|---|
| 整改对象 | `UAT2-v1.6.3.0-G14表类型统计第二轮用户验收测试报告-智能体O.md`（结论：不通过，1 MAJOR + 1 MINOR） |
| 整改基线 | `main` / `4c7a737`（第一轮 UAT 整改提交） |
| 整改人 | 智能体 Q |
| 整改日期 | 2026-09-02 |
| 设计定版 | `DESIGN-v1.6.3.0-…详细设计说明书.md` **Rev.P → Rev.Q**（第二轮 UAT 整改定版） |
| **整改结论** | **两项缺陷全部关闭（含行为级浏览器复测），并用 TDSQL 高仿靶场完成分布式成功路径端到端验证；回归 1761 全绿，可再次提交 UAT** |

---

## 1. 整改总览

| 级别 | 编号 | 问题 | 整改方式 | 状态 |
|---|---|---|---|---|
| **MAJOR** | UAT2-O-G14-01 | 旧请求的迟到错误提示串到新实例上下文 | 按报告 6 步：独立 `tabletypeLoading` + `isTableTypeRequestCurrent` 纯判定 + `runTableTypeStats` 自带请求 | ✅ 关闭 |
| MINOR | UAT2-O-G14-02 | 实时结果范围的采集时间为空 | 服务端 `run_stats` 生成同源 `captured_at` 显式落库并随响应返回；前端缺失明示 | ✅ 关闭 |
| — | 用户专项要求 | 用 G 的 TDSQL 高仿靶场做分布式实例测试 | 靶场 Proxy 扩展三条命令 + 平台端到端验证 | ✅ 完成（证据见 §4.2） |

---

## 2. 逐项整改明细

### 2.1 UAT2-O-G14-01（MAJOR）—— 迟到错误提示串台

**根因（报告 §6.1 已定位，复核确认）**：`runTableTypeStats()` 只在 `_deepPost()` 返回后检查序号与 scope，而错误提示在 `_deepPost()` **内部、序号检查之前**就已弹出；共享的 `deepLoading` 字符串无法区分"旧请求 finally"与"新请求 loading"的所有权。

**按报告"必须按以下结构修改"逐条落实**（全部在 G14 前端块内，其他深度诊断页签的 `_deepPost` 未动，回归面最小）：

| # | 报告要求 | 落实 |
|---|---|---|
| 1 | 独立 `tabletypeLoading = ref(false)`，按钮改绑 | ✅ `app.js` 新增；`index.html` 按钮改 `:loading="tabletypeLoading"` |
| 2 | 纯判定函数 `isTableTypeRequestCurrent(mySeq, scope)` | ✅ 比较序号 + 登录用户名 + `deepConnId` + 规范化 `deepDb` 四维度 |
| 3 | `runTableTypeStats` 不再委托 `_deepPost`，自带 apiFetch/解析/提示，固定顺序 | ✅ `++seq → 快照 scope → resetTableTypeState() → loading=true → 请求 → 先判 isCurrent，过期静默 return（无任何提示/数据/loading 副作用）→ 仍当前才显示业务错误或写结果 → finally 仅 `mySeq===tabletypeSeq.value` 时关 loading` |
| 4 | watch 与 `clearRoleScopedState` 同步释放 loading | ✅ 两处均已加 `tabletypeLoading.value=false` |
| 5 | 旧请求 finally 不得误关新请求 loading | ✅ finally 内序号守卫 |
| 6 | `tabletypeLoading` 暴露模板；auditor/后端 403 不变 | ✅ 返回清单已登记 |

**新增自动化（满足"删掉关键检查即失败"，非纯字符串存在性）**——
`tests/test_g14_frontend_state_binding.py` 扩展 3 项：
- `test_uat2_stale_error_toast_is_suppressed`：断言不再委托 `_deepPost`、自带 `apiFetch`、**每个提示分支（`!resp.ok` 与 `catch`）之前都存在 `isTableTypeRequestCurrent` 判定**（顺序断言）；
- `test_uat2_loading_has_independent_ownership`：独立 loading、finally 序号守卫、watch/登录态释放、模板绑定与返回清单登记；
- `test_uat2_scope_text_never_blank_tail`（O-G14-02 前端侧）：缺失时间必须显示"采集时间不可用"，禁止空白尾段。

**真实浏览器复测（Chromium）**：

| 报告关闭标准路径 | 实测 |
|---|---|
| 离线发起统计→约 60ms 内切换集中式实例 | 当前实例正确切换；**无任何旧"实例连接失败"提示**；结果空态；按钮未锁；离线请求实际 422 被静默作废 ✅ |
| 重放一次（离线→54ms 切靶场） | 结论一致 ✅ |
| 反向护栏：不切 scope 的普通 400 | "数据库不存在…"提示**正常显示**，未被误吞 ✅ |
| 控制台 | 无 JS 运行时错误 ✅ |

### 2.2 UAT2-O-G14-02（MINOR）—— 采集时间同源

**服务端**（`table_type_stats_service.py::run_stats`）：采集完成后生成一次
`captured_at = datetime.now().replace(microsecond=0)`；INSERT 显式写入同一
`captured_at`（列清单追加 `created_at`）；`res["created_at"] = captured_at.isoformat(sep=" ")`。
**响应与历史严格同源**，前端不取本机时间。历史接口与 DDL 默认值不变，旧记录不迁移不回填。

**前端**：`tabletypeScopeText` 有值时 `formatTime(r.created_at)`；缺失时显示
"采集时间不可用"，不再留下空白尾段。

**新增 3 条元数据库集成用例**（`tests/test_table_type_stats.py`，挂 `g14_schema` 护栏）：
- `test_uat2_created_at_in_run_response`：响应含非空可解析 `created_at`；
- `test_uat2_created_at_matches_history_row`：响应时间与 `stat_id` 历史行**解析后精确到秒一致**；
- `test_uat2_created_at_on_empty_and_failed_runs`：空库与部分失败（1064）分支同样携带时间。

---

## 3. 用户专项：TDSQL 高仿靶场分布式验证

按用户要求用 G 的靶场（`deploy/tdsql-dev-cluster`）实测。**发现靶场 Proxy 缺 G14 必需的
三条命令支持**（`/*proxy*/show table with shardkey / with noshardkey_allset / without shardkey`
此前透传后端必然 1064），且 PyMySQL `select_db()` 走 **COM_INIT_DB** 而非 COM_QUERY，
靶场只跟踪 `USE` 会切库失效。

**靶场增强（`deploy/tdsql-dev-cluster/tdsql_proxy.py`，+65 行）**：
- 新增库维度分片元数据 `_SHARD_BY_DB`（与持久化表同源加载/回写）；
- 拦截三条命令，按**会话默认库**返回库限定名 `db.table`——`with*` 双列带 `info`
  （`shardkey:user_id` / `shardkey:noshardkey_allset`）、`without` 单列，
  形态与设计附录 B 的真实实测逐字一致；单表 = 当前库 BASE TABLE 减去已登记分片/广播；
- 补 COM_INIT_DB 跟踪（`select_db` 场景）。

**平台端到端验证（经 127.0.0.1:15002，真实 HTTP + 真实 TCP 协议栈）**：

| 核对项 | 实测 | 期望 |
|---|---|---|
| 实例类型识别 | distributed（PR001 探针经 Proxy 拿到真实拓扑签名） | distributed ✅ |
| 分片/广播/单表 | 2 / 2 / 4 | 2 / 2 / 4 ✅ |
| 总表 / 逻辑基线 / 子分区 | 8 / 8 / 0 | 8 / 8 / 0 ✅ |
| `RECON_MISMATCH` | 无 | 无 ✅ |
| 恒等式 total=shard+broadcast+single | 成立 | ✅ |
| 响应 `created_at` 与历史行 | 精确到秒一致 | ✅ |
| 浏览器页签（场景 D） | "分布式 · 库 1 · 总表 8 · 单表 4 · 广播 2 · 分片 2 · 基线 8"，范围行含采集时间 | ✅ |

**边界如实登记（按报告 §7 与 §8.5 要求）**：靶场是高仿 Mock，本次验证证明的是
"解析/归属/去重/交叉校验/落库/前端"全链路在真实协议栈上工作；**真实 TDSQL 分布式
六数字对账（lzbj_ecif 215/0/117/98/215/78）与 T20 性能证据仍属受控生产上线后的
内网最终验收项，本轮不宣称已通过**。

---

## 4. 变更清单与验证证据

### 4.1 变更清单

| 文件 | 变更 | 对应 |
|---|---|---|
| `frontend/static/js/app.js` | 请求所有权六步 + `tabletypeLoading` + `isTableTypeRequestCurrent` | UAT2-O-G14-01 |
| `frontend/index.html` | 按钮改绑独立 loading（1 行） | UAT2-O-G14-01 |
| `backend/services/table_type_stats_service.py` | `captured_at` 同源（import + INSERT 列 + 响应字段） | UAT2-O-G14-02 |
| `tests/test_table_type_stats.py` | +3 条用例（118 项） | UAT2-O-G14-02 |
| `tests/test_g14_frontend_state_binding.py` | +3 项静态门禁（12 项） | UAT2-O-G14-01/02 |
| `deploy/tdsql-dev-cluster/tdsql_proxy.py` | +65 行：三条命令 + COM_INIT_DB 跟踪 + 库维度元数据 | 靶场专项 |
| `docs/DESIGN-…-Rev.Q` | 头部/§4.4/§11/§12.8/修订记录；附录 A.1/A.2/A.4 与仓库逐字同步 | 全部 |

**冻结面核查**：`backend/api/table_type_stats.py`、`backend/schema/v13/130_table_type_stats.sql`、
`backend/engine/**`、119 条规则、`auth_service.py`、其余 9 个深度诊断子模块**零改动**。

### 4.2 回归证据（本地 MySQL 8 元数据库）

| 门禁 | 结果 |
|---|---|
| `tests/test_table_type_stats.py` | **118 passed**（115 + 3） |
| 门禁六件组（模块+rbac+路由+附录+版本+前端） | **145 passed** |
| 规则组 | **105 passed** |
| 全量 `tests/` | **1761 passed, 0 failed, 0 skipped**（1755 → 1761） |

### 4.3 附录↔仓库一致性

`test_design_appendix_matches_repo.py` 4 项全绿：A.1（1191 行）/ A.2（118 行）/
A.3（46 行）/ A.4（2620 行）与仓库逐字一致。

---

## 5. 遗留事项（移交第三轮 UAT / 内网最终验收）

| 项 | 说明 |
|---|---|
| 内网真实 TDSQL 六数字对账 + T20 性能证据 | 按报告 §7 受控生产上线后由内网智能体执行；**本报告不宣称已通过** |
| 迟到提示的跨用户形态 | 本轮浏览器复测覆盖跨实例/跨输入；跨用户形态由第三轮 UAT 复核 |
| 靶场 Proxy 扩展的负责人归属 | `tdsql_proxy.py` 属 G 的靶场设施，本轮为完成用户指定的靶场验证而扩展；建议 G 复核后纳入靶场正式能力 |
| 环境校正说明 | 浏览器复测时将 `smoke-g14-local`（13306 实为普通 MySQL）按探测建议管理员锁定为集中式、将靶场连接端口由 13306 校正为 15002——均为平台正规管理操作，未改被测代码 |

---

## 6. 结论

第二轮 UAT 的 1 MAJOR + 1 MINOR **全部按报告方案关闭**：迟到请求的提示/数据/loading
副作用全部作废且当前请求反馈不受影响；采集时间服务端同源、与历史一致。用户指定的
靶场分布式验证完成，六数字精确对账。回归 1761 全绿、冻结面零改动、附录与仓库逐字一致。

**请求进入第三轮 UAT。**
