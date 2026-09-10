# v1.6.3.5 在线元数据审核 第三轮 UAT 复测报告（智能体D）

| 项 | 内容 |
|---|---|
| 产品版本 | v1.6.3.5 |
| 被测代码 | `main@7cfda09`（Q 的 UAT 第二轮整改提交），工作区无产品代码改动 |
| 测试方 | **智能体D**（接替 O 完成其未跑完的第三轮） |
| 委托方 | Mr.Linsang |
| 测试日期 | 2026-09-10 |
| 复测范围 | O 第二轮报告的 6 项整改（R2-01~R2-06）+ 设计 §11.3 UI 矩阵补充用例 + 部署契约独立变异复核 |
| 证据目录 | `docs/evidence/v1.6.3.5-uat3-d/`（28 张真实浏览器截图 + 20 份 JSON/日志） |
| 提交给 | Mr.Linsang |

---

## 0. 接替说明与本次立场

O 的第三轮 UAT 进行到中途因 token 耗尽停止。本报告**接替完成该轮**，并遵守以下边界：

1. **不修改任何产品代码、正式测试代码与部署脚本**，只新增本人取证脚本与证据目录；所有结论来自
   真实浏览器操作 + 数据库/接口反证。
2. O 已产出的证据（`docs/evidence/v1.6.3.5-uat3-o/`：过期心跳拒绝截图与探针、长任务心跳采样、
   全量回归 2126 用例）**作为已确认事实引用，不重复执行**；本报告不冒用 O 的结论，也不改写其署名。
   > 该目录当前仍是工作区未跟踪文件（按仓库 Git 纪律第 3 条，他人在途证据由产出方提交，本人未代为打包）；
   > 如需随本轮一并入库，请 Mr.Linsang 指示。
3. 本人使用**独立夹具**（`uat_d_1635_r3_meta` / `uat_d_1635_r3_target` / 专属测试账号），
   不触碰 O 的 `uat_o_1635_r2_*` 库与账号、不触碰生产配置。
4. 「真实浏览器点击」的实现方式为 **Playwright 1.60 驱动本机 Chrome（headless）**：真实浏览器引擎、
   真实 DOM 输入与鼠标事件、真实下载与刷新；**不是**手工鼠标点击，也不是接口替身。这一取证方式与 O
   本轮使用的内置 Chromium 同类，差异在报告 §8 明确标注。

---

## 1. 结论

**R2-01~R2-06 六项整改全部关闭（复测通过）**；本轮新发现 **2 项缺陷（D-01、D-02）** 与
**1 项要求未落地（D-03）**，均给出照图施工级整改方案，建议随 v1.6.3.6 或本轮补丁一并整改后复测。

| 项 | 结论 | 关键证据 |
|---|---|---|
| R2-01 runner 心跳与受理准入 | **通过** | runner 停机 >10s 后点击审核：503 + 中文提示，零新任务、零占槽；25s 长任务期间心跳年龄始终 ≤1s |
| R2-02 终态计数与耗时 | **通过** | 页面 `枚举 63/提取 63/审核 63`、`已用时长 3s`；64 表场景 `64/64`；与 results/history/manifest 一致 |
| R2-03 目标库错误语义化 | **通过** | 2003/1049 两条真实失败路径在页面与接口均给出中文建议 + 原始错误码 |
| R2-04 部署契约断言有效性 | **通过（独立变异复核）** | 3 脚本 × 4 类变异 + upgrade nohup 分支 2 类变异**全部变红**，干净脚本通过，0 漏检 |
| R2-05 重扫新鲜度 | **通过** | 目标库 63→64 后同条件再点：**新 job**、`total=64`、结果含新增表；响应丢失场景仅 1 个任务 |
| R2-06 刷新后活动任务恢复 | **通过** | 刷新→重进页面：任务卡带同一 job、状态/阶段/进度/冻结实例名恢复，轮询续跑至 SUCCEEDED |
| D-01 取消任务无完成时间 | **新发现（P2）** | 取消任务 `finished_at` 为空，`已用时长` 随查询时间持续膨胀（75 秒增长 75） |
| D-02 过期未认领任务不回收 | **新发现（P2）** | 受理后 runner 未认领即失效：超 30s 启动期限仍停在 ACCEPTED/WAITING，唯一槽持续被占 |
| D-03 submission 生命周期未落地 | **要求未闭环（P3）** | `meta_submission` 只写不读；无独立「恢复查看」与「重新扫描」动作；分页回写无 generation 守卫 |

---

## 2. 被测对象与环境

| 项 | 值 |
|---|---|
| 代码版本 | `main@7cfda09`（`APP_VERSION` = 1.6.3.5，前端 5 处版本标记一致） |
| Web | `127.0.0.1:8015`，`AUTH_ENABLED=true`、`SCHEDULER_ENABLED=false`、单 worker |
| runner | `python -m backend.workers.metadata_runner`（独立进程，可注入 child 启动延迟 0–25s） |
| 故障注入代理 | `127.0.0.1:8016 → 8015`（可丢弃一次/全部已收到的受理 POST 响应） |
| 元数据库 | 本机 MySQL `8.0.45`，专用库 `uat_d_1635_r3_meta` |
| 目标库 | `uat_d_1635_r3_target`（合成两列表 63 张 → 复测中新增第 64 张） |
| 旁证实例 | `D-UAT3-不可连接-本机`（13307 未监听）、`D-UAT3-不存在库-本机` |
| 测试账号 | `uat_d_1635_r3`(admin) / 复测中新建的 developer、dba 账号（口令仅存 `data/reports/`，不入库） |
| 解释器 | `C:\Python314\python.exe` + 用户站点包（含 kazoo/playwright/pytest，与 O 一致）；Python 3.14 |

**夹具自证**：`docs/evidence/v1.6.3.5-uat3-d/env-ready.json`（受理就绪、槽空闲、目标 63 表）。

---

## 3. R2-01~R2-06 逐项复测

### 3.1 R2-01 runner 心跳解析与受理准入 —— 通过

**用例 A（停机后受理必须失败关闭）**：真实停止 runner，等待心跳越过 10 秒窗口，在浏览器点击
「🚀 拉取元数据并执行文件审核」。

- 页面提示：`元数据执行服务心跳已过期或不可读（runner 可能已停止），请稍后重试。`
- 接口：`_check_runner_ready()` → `EXECUTOR_UNAVAILABLE / 503`
- **无任务卡、无新任务（8 → 8）、未占槽**
- 证据：`d-s7-01-submit-while-runner-down.png`、`s7_runner_offline.json`、`d-s7-before-click.json`

**用例 B（长任务期间心跳必须持续刷新）**：注入 child 启动延迟 25s，任务运行期间每 4 秒采样一次槽位心跳。

| 采样时刻 | 心跳年龄 | 槽是否被占 |
|---|---|---|
| +0s | 0.04s | 否 |
| +4s | 0.94s | 是 |
| +8s | 0.88s | 是 |
| +12s | 0.33s | 是 |
| +16s | 0.28s | 是 |
| +20s | 0.22s | 是 |
| +24s | 0.17s | 是 |

长任务全程心跳年龄 ≤1s，任务 27–28s 正常 SUCCEEDED。证据：`d-s6-heartbeat-samples.json`、
`s6_refresh.json`、`running-heartbeat.json`（O 侧同向证据）。

> 对应 O 报告 R2-01 第 1、2、3、4 条要求：**已闭环**。第 5 条（过期未认领任务的回收判定）见
> §6.2 D-02，**未闭环**。

### 3.2 R2-02 终态计数与耗时 —— 通过

| 场景 | 页面「进度」 | 页面「已用时长」 | 接口 progress | 一致性 |
|---|---|---|---|---|
| 63 表基线 | 枚举 63 / 提取 63 / **审核 63** | **3s** | `total_statements=63, audited_statements=63` | manifest counts 同值 |
| 64 表重扫 | 枚举 64 / 提取 64 / **审核 64** | 2s | 64/64 | results `total=64` |
| 25s 延迟任务 | 64/64 | **28s** | 64/64 | runner 日志 elapsed_s=28 |
| 失败任务 | 枚举 - / 提取 - / 审核 - | 3s | 全 null | 无 report_id、cleanup_ok=1 |

失败任务在页面 **8 秒内收敛为 FAILED** 并显示可读原因（`d-s13-failed-card.png`）。R2-02 要求的
"终态计数真实、耗时非空、不伪造完成数"**已闭环**；CANCELLED 任务的耗时不在此列，见 §6.1 D-01。

### 3.3 R2-03 目标库异常中文诊断 —— 通过

| 路径 | 页面/接口消息（截断） | 状态 |
|---|---|---|
| 端口未监听（2003） | `无法连接目标数据库（请检查实例地址、端口与网络连通性）。原始错误: 审核执行失败: (2003, "Can't connect to MySQL server on '127.0.0.1' …")` | FAILED / exit 1 / cleanup_ok 1 / 无报告 |
| 库不存在（1049） | `目标数据库不存在（请检查 database/库名配置）。原始错误: … (1049, "Unknown database 'uat_d_1635_r3_missing'")` | 同上 |

证据：`d-s10-offline.png`、`d-s10-missing_db.png`、`s10_errors.json`、`s13_failed_card.json`。

> 边界：`MetadataExtractError`（SHOW CREATE 失败）分支的 2013/1146 语义化，本轮以代码路径核对 +
> O 报告的注入证据为准，本人未重复做单元级注入。

### 3.4 R2-04 部署契约断言有效性 —— 通过（独立变异复核）

本人未采信既有断言，直接对 `tests/test_v1635_deploy_contract.py` 做**内存变异**后执行其真实断言：

| 脚本 | 删除启动行 | 注释启动行 | 挪到 Web 之后 | 只留安装/日志/pkill | 干净脚本 |
|---|---|---|---|---|---|
| install.sh | 抓住 | 抓住 | 抓住 | 抓住 | 通过 |
| upgrade_incremental.sh | 抓住 | 抓住 | 抓住 | 抓住 | 通过 |
| apply_patch.sh | 抓住 | 抓住 | 抓住 | 抓住 | 通过 |
| upgrade nohup 分支 | 抓住 | 抓住 | — | — | 通过 |

**坏变异 0 漏检**。证据：`probe-deploy-mutation.json`、`probe_contracts_d.py`。

### 3.5 R2-05 重扫新鲜度与受理幂等 —— 通过

**用例 A（目标库变化后的重扫）**：基线任务 63 对象 SUCCEEDED 后，向目标库新增
`t_d_added_after_scan`（实数 64）→ 表单条件不变再次点击。

| 项 | 第一次 | 第二次 |
|---|---|---|
| job_id | `bc27af99…` | **`b332bc3c…`（不同）** |
| progress | 枚举/选中/提取 63 | **64 / 64 / 64** |
| 页面结果条数 | 63 | **64** |
| 结果是否含新增表 | — | **是** |

证据：`d-s5-before-click.json`、`s5_rescan.json`、`d-s5-01/02.png`。

**用例 B（受理响应丢失）**：经代理丢弃一次已受理（202）的 POST 响应。浏览器随后以**同一幂等键**
重发，服务端幂等重放返回**同一 job**（`986a1c15…`），任务总数 8 → 9（**未重复创建**）。

证据：`s8_drop_post.json`、`data/reports/uat_d_1635_r3/proxy-events.jsonl`（含 `injection` 记录）。

### 3.6 R2-06 刷新后活动任务恢复 —— 通过

25s 延迟任务运行中刷新页面：

| 步骤 | 观察 |
|---|---|
| 刷新前 | 任务卡 `#93f7ae92`，ACCEPTED / WAITING |
| 刷新后（仍在概览页） | 无任务卡（预期：不同页面） |
| 点回「SQL审核 → 在线元数据审核」 | **任务卡恢复**：`#93f7ae92`、RUNNING / ENUMERATING、已用时长 9s、实例/库齐全 |
| 收尾 | 轮询续跑至 SUCCEEDED（64/64，28s） |

证据：`d-s6-01~04.png`、`s6_refresh.json`。恢复动作是 **GET**（不新建 POST），符合"不得为恢复而
新建任务"的要求。

---

## 4. 补充用例（设计 §11.3 UI 矩阵 / §11.2 任务矩阵）

| 用例 | 场景 | 结果 |
|---|---|---|
| UI-01 受理与阶段 | 真实登录→选实例→勾范围→点击 | 受理卡即时出现，阶段推进 WAITING→ENUMERATING→…→DONE |
| UI-06 分页 | 63 条结果翻到第 2 页 | 第 2 页 13 条（表格 14 行含表头），非全量渲染（`d-s1-06-page2.png`） |
| UI-06 下载 | 页面按钮下载 .sql / HTML | 18552 B / 58995 B，**sha256 与服务端 manifest 完全一致**，含冻结实例名 |
| UI-10 旧路径退役 | 浏览器上下文内 POST 旧同步接口 | **410 ENDPOINT_RETIRED** + 明确 detail，任务数不变（零副作用） |
| UI-02 三连击 | 同一事件循环内点击提交 3 次 | **仅产生 1 个任务**（`s15_double_click.json`） |
| JOB-01 跨用户并发 | admin 运行中，dba 用户提交 | dba 收到 `当前已有一个元数据审核任务正在执行，请稍后重试。`，**未建任务** |
| UI-08 取消 | 运行中点击「取消任务」→ 确认 | CANCELLED + CLEANUP、cleanup_ok=1、无 report；**耗时问题见 D-01** |
| UI-04 鉴权 | 未认证 / 跨用户访问 | 未认证 401；developer、dba 访问他人任务的状态/结果/SQL/HTML/取消**一律 404「任务不存在」**（不泄漏存在性） |
| UI-04 权限矩阵 | 三角色 `visible-menus` | 均含 `schema-extractor-audit`，与 `role_permissions` 一致；首登未改密时业务接口 403（既定安全行为） |
| JOB-09/14 参数与产物 | 无审核范围、产物缺失 | 无审核范围 422；产物缺失 410（由既有自动化用例覆盖） |

---

## 5. 自动化回归独立复核

- **部署契约用例** `tests/test_v1635_deploy_contract.py`：独立变异复核全部有效（§3.4）。
- **全量回归**：`data/reports/uat_d_1635_r3/full-regression.xml` / `full-regression.log`
  （专用回归库 `uat_d_1635_r3_regression`，前置见 `prepare_regression_d.py`，命令见证据目录 README）。
  三次运行全部留档，**只有环境备齐的最终一次被采信**：

| 运行 | 环境 | 结果 | 采信 |
|---|---|---|---|
| 第一次 | 新建库 admin 口令与用例期望不一致 + 解释器缺 `kazoo` | 420 failed / 1561 passed / 114 errors / 31 skipped | 否（环境未备齐，不作为产品缺陷） |
| 第二次 | 解释器已修正，但全量跑在 `_tests` 库 | 2071 passed / **0 failed** / 25 errors / 30 skipped | 否（25 errors 全是 G14 破坏性用例的库名守卫按其设计拒绝，非缺陷） |
| **最终** | 与 O 同口径：`C:\Python314` + 用户站点包，全量跑在回归库 | **2096 passed / 0 failed / 0 errors / 30 skipped（488.68s）** | **是** |

### 5.1 最终数字

**2096 passed、0 failed、0 errors、30 skipped、488.68 秒**，与 Q 开发记录中宣称的
"2096 passed + 30 skipped + 0 failed"**完全一致**，独立复核成立。

30 个 skip 为既定环境性跳过（`test_sit_rules.py` / `test_uat_rules.py` 等集成模块要求可登录的后端服务 +
内网凭据），与既往各轮口径相同；按施工规约 R-18，本报告不把 skip 计入通过，其补测责任在内网环境。
本轮无 401/凭据类假失败残留、无 `kazoo` 缺失类错误残留。

---

## 6. 新发现问题与照图施工级整改方案

### 6.1 D-01（P2）取消任务没有完成时间，耗时随时间无限膨胀

**现象（实测）**

| 任务 | 状态 | `finished_at` | 第一次查询 elapsed | 75 秒后再查 | 增长 |
|---|---|---|---|---|---|
| `5a667fe6…` | CANCELLED | **空** | 104 | **179** | **+75** |
| `bc27af99…` | SUCCEEDED | 有值 | 3 | 3 | 0 |
| `93f7ae92…` | SUCCEEDED | 有值 | 28 | 28 | 0 |

证据：`probe-cancel-elapsed.json`、`s9_cancel.json`、`d-s9-03-after-cancel.png`。

**影响**：① 页面「已用时长」对已取消任务会持续增长（次日打开将显示数千秒），属可见的错误数据；
② 取消任务在历史/审计中没有完成时间，无法按时间归档；③ 与 R2-02 已确立的"终态按
started→finished 算实际秒数"口径自相矛盾（CANCELLED 也是终态）。

**根因（行号）**
- `backend/workers/metadata_runner.py:126` 取消分支直接 `cas_state(..., R.STATE_CANCELLED, phase=CLEANUP, error_code="CANCELLED", …)`，
  **未传 `finished_at`**；而 `repository.fail()`（`metadata_audit_repository.py:433`）与 `complete()`（:424）
  都通过 `extra={"finished_at": _now()}` 落库。
- `backend/api/metadata_audit.py:172-178` 的耗时计算：终态时 `end_dt = parse_utc(job.get("finished_at")) or _now_utc()`，
  `finished_at` 为空即回退到"当前时间"，于是每次查询都变大。

**整改方案（照图施工）**

1. `backend/services/metadata_audit_repository.py` 新增与 `fail()` 对称的取消终态方法：

   ```python
   def cancel(self, job_id: str, attempt_token: str, *, cleanup_ok: bool = True) -> bool:
       """→ CANCELLED（终态写 finished_at，与 fail/complete 同口径）。"""
       return self.cas_state(
           job_id, attempt_token,
           (STATE_ACCEPTED, STATE_RUNNING, STATE_PUBLISHING, STATE_STOPPING),
           STATE_CANCELLED, phase=PHASE_CLEANUP, error_code="CANCELLED",
           error_message="任务已被用户取消。",
           extra={"finished_at": _now(), "cleanup_ok": 1 if cleanup_ok else 0})
   ```

2. `backend/workers/metadata_runner.py` 取消分支改为调用 `self.repo.cancel(job_id, token, cleanup_ok=res.cleanup_ok)`，
   删除就地 `cas_state`。

3. `backend/api/metadata_audit.py::_job_summary` 终态**不得回退到 now**：

   ```python
   if st_dt is not None:
       if job["state"] in TERMINAL_STATES:
           end_dt = _jp.parse_utc(job.get("finished_at"))
           elapsed = None if end_dt is None else max(0, int((end_dt - st_dt).total_seconds()))
       else:
           elapsed = max(0, int((_now_utc() - st_dt).total_seconds()))
   ```

   前端已用可选链显示 `-`，无需改动。

4. **存量数据处理遵循 R-06/R-13**：**不回填**历史 `finished_at`（完成时间不可考，回填等于伪造）。
   仅由第 3 步使老取消任务的耗时报 `null`（页面显示 `-`），停止膨胀。

5. 回归锁 `tests/test_v1635_uat3.py`：
   - `test_cancelled_job_writes_finished_at`：走取消路径后 `finished_at` 非空；
   - `test_cancelled_elapsed_stable`：对同一取消任务连续两次 `_job_summary`，`elapsed_seconds` 相等；
   - `test_terminal_without_finished_at_is_none`：终态且 `finished_at` 为空 → `elapsed_seconds is None`；
   - `test_all_terminal_states_have_finished_at`：SUCCEEDED/FAILED/CANCELLED 三态均有 `finished_at`。

### 6.2 D-02（P2）过期未认领任务不回收，唯一受理槽被永久占用

**现象（受控复现）**：runner 停止状态下受理一个任务（等价于"受理后执行器在认领前失效"），
此后每 5 秒采样一次：

| 距受理 | 状态 | 阶段 | 槽占用 |
|---|---|---|---|
| +5s … +50s（全 10 次采样） | **ACCEPTED** | WAITING | **仍是该 job** |

`still_waiting_after_50s = true`、`slot_still_occupied = true`；`START_TIMEOUT=30` 已过 20 秒仍无回收。
证据：`probe-stale_waiting.json`、`probe_stale_waiting_d.py`。

**影响**：唯一受理槽被一个永远不会执行的"幽灵任务"长期占用 —— 期间所有新提交都会得到
`409 METADATA_BUSY`，用户只能等运维重启 runner 才会被顺带认领。属"功能不可用且用户无法自救"。

**代码事实**
- `MetadataLimits.START_TIMEOUT = 30`（`metadata_job_process.py:131`）**无任何消费方**（全仓仅被一条
  单测断言常量）；
- 受理时写入的 `deadline_at`（`metadata_audit_repository.py:126`）**无任何读取方**；
- runner 的 `_tick()` 只做 `claim_next_accepted()`，没有任何"超期未认领 → 作废并释放槽"的分支。

> 证据边界：本项为**状态级受控复现**，不是"受理瞬间杀 runner"的竞态复现；它检验的是"过期 WAITING
> 是否存在回收方"这一代码事实。窗口本身较窄（心跳仍新鲜但 runner 已死的 ≤10 秒内受理，或 runner
> 被 SIGKILL），但后果是槽永久占用。

**整改方案（照图施工）**

1. `backend/services/metadata_audit_repository.py` 新增回收方法（**事务内锁序仍为 slot → job**）：

   ```python
   def reclaim_stale_accepted(self, start_timeout_seconds: int) -> Optional[str]:
       """把"受理后超期未被认领"的任务判失败并释放槽。返回被回收的 job_id 或 None。

       守卫（缺一不可）：
         · job.state='ACCEPTED'；
         · slot.active_job_id = job.id（不碰别人的槽、不抢占其他 runner 的任务）；
         · UTC_TIMESTAMP(6) - job.created_at > start_timeout_seconds。
       动作：CAS ACCEPTED→FAILED（error_code='START_TIMEOUT'，写 finished_at/cleanup_ok），
             然后释放槽。不得发信号、不得杀 PID。
       """
   ```

   SQL 骨架（沿用既有 `_now()`/`cas_state` 风格，禁止手写 `%s`，遵循 R-01）：

   ```sql
   SELECT * FROM metadata_audit_slot WHERE id=? FOR UPDATE;
   SELECT * FROM metadata_audit_jobs WHERE id=? FOR UPDATE;   -- id = slot.active_job_id
   -- 满足超期且 state='ACCEPTED' 时：
   UPDATE metadata_audit_jobs SET state='FAILED', phase='CLEANUP', error_code='START_TIMEOUT',
          error_message='受理后执行器未在 30 秒内认领，任务已作废并释放受理槽。',
          finished_at=?, cleanup_ok=1, updated_at=? WHERE id=? AND state='ACCEPTED';
   UPDATE metadata_audit_slot SET active_job_id=NULL, updated_at=? WHERE id=1 AND active_job_id=?;
   ```

2. **两个调用点**（缺一不可，否则 runner 已死时无人自愈）：
   - `backend/workers/metadata_runner.py::_tick()` 开头：`self.repo.reclaim_stale_accepted(jp.MetadataLimits.START_TIMEOUT)`；
   - `backend/api/metadata_audit.py::create_metadata_job`：在**受理事务内、判定 busy 之前**调用同一方法，
     使新请求能自愈陈旧槽（这是"runner 已死时用户仍能自救"的关键）。

3. 可观测：回收时 `logger.warning("回收过期未认领任务 job_id=%s age_s=%s", …)`；`error_code='START_TIMEOUT'`
   已可被前端 `metadataJob.error.message` 展示，无需新列。

4. 回归锁 `tests/test_v1635_uat3.py`：
   - `test_stale_accepted_reclaimed_allows_new_job`：构造 31 秒前的 ACCEPTED 占用，**新受理成功**（非 409），
     旧任务 FAILED/START_TIMEOUT；
   - `test_fresh_accepted_not_reclaimed`：5 秒内不被抢；
   - `test_running_job_never_reclaimed`：RUNNING 状态不被回收；
   - `test_reclaim_skips_when_slot_owned_by_other_job`：`slot.active_job_id` 非该 job 时不动作（防误杀）。

### 6.3 D-03（P3）R2-05 第 1、3 条要求的 submission 生命周期与"恢复/重扫"分离动作未落地

**事实**：R2-05 的核心缺陷（永久复用幂等键导致"改完目标库再扫还是旧报告"）**已修复**（§3.5 用例 A）。
但该条要求的另外两点未落地：

1. `frontend/static/js/app.js` 中 `meta_submission` **只写不读**（全文仅 `:461` 登出删除、`:1758` 写入），
   不存在 `{user, intent_id, normalized_input, key, job_id, submission_state}` 生命周期记录；
2. UI 只有**一个**按钮，没有把「恢复查看上次任务」（GET，不新建）与「重新扫描」（新 key）拆成两个明确动作；
3. `loadMetadataResults()`（`:1816`）结果回写**没有 generation 守卫**，慢的旧响应仍可能覆盖新视图
   （轮询 `_pollMetadataJob` 有 `_metaPollGen`，分页回写没有）。

**影响**：功能可用，但 ① 用户无法区分"看旧结果"和"重新扫"，误点后需靠肉眼比对；② 快速切换实例/参数时
存在旧响应覆盖新视图的时序风险；③ 交付与 O 报告要求存在差距，下一轮极易被判"未闭环"。

**整改方案（照图施工）**

1. `app.js` 建立并**真正消费**提交记录：

   ```js
   const META_SUB_KEY = 'meta_submission';
   const _saveSubmission = (o) => sessionStorage.setItem(META_SUB_KEY,
       JSON.stringify({user: currentUser.value?.username || '', ...o}));
   const _loadSubmission = () => { try { return JSON.parse(sessionStorage.getItem(META_SUB_KEY) || 'null'); }
                                   catch (e) { return null; } };
   ```

   字段与状态机：`{user, intent_id, normalized_input, key, job_id, submission_state}`，
   `submission_state ∈ pending | accepted | closed`。

2. 提交语义：
   - `重新扫描` → `intent_id = _newMetaKey()`，`key = intent_id`，`submission_state='pending'` 后 POST；
   - 响应丢失/网络失败且**未能确认结果** → 保留 `pending`，重试时**复用同一 key**（幂等重放）；
   - 收到 202/200 → 写 `job_id`、`submission_state='accepted'`；
   - 轮询到 SUCCEEDED/FAILED/CANCELLED → `submission_state='closed'`，**保留 `job_id` 供查看**，
     下次点击必须是新 key（该点已由 `_newMetaKey()` 保证，需补状态记录）。

3. UI 拆成两个明确动作（在线元数据审核页头部）：
   - `🔁 重新扫描`：走新 key 的受理；
   - `📄 恢复查看上次任务`：读取 `_loadSubmission().job_id`（或服务端该用户最新未决任务），**只 GET**，
     恢复任务卡与分页结果；无记录时按钮禁用并提示"无历史任务"。

4. `loadMetadataResults(job_id, page, gen)` 增加 `const gen = _metaPollGen;` 并在 `await` 之后
   `if (gen !== _metaPollGen) return;`，与轮询同源，保证"参数/页面 generation 变化后旧响应不覆盖新视图"。

5. 回归锁（前端静态契约，沿用 `tests/test_g14_frontend_state_binding.py` 的写法）：
   - `test_meta_submission_is_read_not_only_written`；
   - `test_two_distinct_actions_recover_and_rescan`（断言两个入口文案与各自调用的方法）；
   - `test_load_metadata_results_has_generation_guard`。

---

## 7. 证据索引

目录：`docs/evidence/v1.6.3.5-uat3-d/`

| 类别 | 文件 |
|---|---|
| 夹具/驱动 | `local_harness_d.py`（Web/runner/代理/检查/注入）、`browser_uat_d.py`（13 个真实浏览器场景）、`prepare_regression_d.py` |
| 场景 JSON | `env-ready`、`s1_baseline`、`s3_download`、`s5_rescan`、`s6_refresh`、`s7_runner_offline`、`s8_drop_post`、`s9_cancel`、`s10_errors`、`s11_legacy410`、`s12_authz`、`s13_failed_card`、`s14_concurrency`、`s15_double_click` |
| 探针 JSON | `probe-cancel-elapsed`、`probe-stale-waiting`、`probe-deploy-mutation`、`probe-ownership2`、`probe-rbac-matrix`、`probe-check-permission`、`probe-nonadmin-access` |
| 浏览器截图 | 28 张：`d-s1-01…06`、`d-s3-01`、`d-s5-01/02`、`d-s6-01…04`、`d-s7-01`、`d-s8-01/02`、`d-s9-01/02/02b/03`、`d-s10-offline`、`d-s10-missing_db`、`d-s11-legacy-410`、`d-s12-viewer-login`、`d-s13-failed-card`、`d-s14-a/b`、`d-s15-double-click` |
| 运行产物 | `data/reports/uat_d_1635_r3/`：`full-regression.xml/.log`、`proxy-events.jsonl`、`downloads/dl.sql`、`downloads/dl.html`、`reports/metadata-audit/<job>/manifest.json`（含 sha256） |

**可复现步骤**（本机、仅 loopback）：

```powershell
$PY='C:\Users\linsa\AppData\Local\Python\pythoncore-3.14-64\python.exe'
$env:PYTHONIOENCODING='utf-8'
& $PY docs/evidence/v1.6.3.5-uat3-d/local_harness_d.py setup
& $PY docs/evidence/v1.6.3.5-uat3-d/local_harness_d.py web      # 8015
& $PY docs/evidence/v1.6.3.5-uat3-d/local_harness_d.py runner
& $PY docs/evidence/v1.6.3.5-uat3-d/local_harness_d.py proxy    # 8016
& $PY docs/evidence/v1.6.3.5-uat3-d/browser_uat_d.py s1_baseline
```

账号口令运行期生成于 `data/reports/uat_d_1635_r3/admin.password`（`data/` 已被 `.gitignore` 排除，
不入库）；浏览器 JWT 与完整认证日志不进证据目录。

---

## 8. 未覆盖与边界（不得据此宣告通过的部分）

1. **真实内网 6000+ 表容量**：本轮目标库为合成本机目标（63→64 表），**不构成容量验收**。设计 §11.4
   的三层硬门禁（连续 3 次完整 SUCCEEDED、RSS 峰值 < 1024 MiB、1800s 预算、p95 响应）仍需内网实测。
2. **Linux/systemd 部署与回滚**：本轮全部在 Windows 完成，未执行 `install.sh`/`upgrade_incremental.sh`/
   `apply_patch.sh`/`rollback.sh` 的真实 systemd 运行；R2-04 只证明**静态断言有效**，不证明脚本在
   Linux 上真能起服务。
3. **CORE_SAFE 回退制品、断电重入、真实 RSS 越界、inotify/PID 复用**未由本轮覆盖。
4. **浏览器为 headless Chrome（Playwright 驱动）**：是真实浏览器引擎与真实点击/输入/下载/刷新，
   但**不是人工鼠标操作**；键盘无障碍、缩放/多分辨率、真实打印等未覆盖。
5. **单 worker**：本轮 Web 为单进程；`--workers 2` 下的跨进程受理竞争未在本轮复现（§11.2 JOB-01 的
   双 worker 场景）。
6. 本人**未验证内网 279 秒故障复现**，不把 OOM/TCP RST 写入本轮结论。

---

## 9. 建议的下一步

1. 按 §6.1、§6.2、§6.3 完成 D-01/D-02/D-03 整改并补回归锁（预计改动集中在
   `metadata_audit_repository.py`、`metadata_runner.py`、`metadata_audit.py`、`app.js` 与一个新测试文件）。
2. 整改后由 A（SIT）与 O（UAT）**按本报告的反例复测**：取消任务两次查询耗时一致性；过期未认领任务
   在 runner 不在时能否自愈；"恢复查看/重新扫描"两个入口。
3. 内网具备条件时由 G 组织 §11.4 容量与部署/回退门禁。

---

测试责任方：**智能体D**
提交给：Mr.Linsang
