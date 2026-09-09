# SIT2-v1.6.3.5 第二轮系统集成测试报告

| 项 | 内容 |
|---|---|
| 被测版本 | v1.6.3.5，`main` / `1ab5130`（SIT 第一轮整改） |
| 对照基线 | `main` / `1f274cd`（施工前，第一轮已建立的失败清单） |
| 上轮结论 | 不通过：3 BLOCK ＋ 2 MAJOR ＋ 1 MINOR |
| 测试方 | 智能体 A |
| 测试日期 | 2026-09-09 |
| 本轮方式 | 逐项功能复验（不看 grep 看行为）＋ **变异测试** ＋ 全量回归对照 |
| **测试结论** | **不通过。上轮 6 项中 5 项已关闭且经变异验证是真锁；M-02 只修了一半——内网实际使用的升级脚本没有接线，本版修复到不了生产。另新增 1 项回归。** |

---

## 1. 结论摘要

| 上轮编号 | 状态 | 依据 |
|---|---|---|
| B-01 应用起不来 | **已关闭** | 交付状态可直接启动；新增交付门禁用例，**变异复现 B-01 后 5 条用例变红** |
| B-02 资源护栏缺失 | **已关闭** | 10 个参数落 9 个；RSS 采样、`statvfs` 磁盘检查、25% MemTotal 预检均实测生效 |
| B-03 业务错误裸 500 | **已关闭** | 实测返回 **503 ＋ `EXECUTOR_UNAVAILABLE`** 结构化响应 |
| M-01 子进程失败无线索 | **已关闭** | 硬崩分支记录脱敏 stderr 尾部（日志 800 字符 / `error_message` 400 字符，去 CRLF） |
| **M-02 部署未接线** | **未关闭（降级为 BLOCK）** | `install.sh`/`verify_deploy.sh` 已接，但 **`upgrade_incremental.sh`/`apply_patch.sh`/`rollback.sh` 仍为 0 处引用**——而内网**正是用增量脚本升级的** |
| N-01 失败原因不暴露 | **已关闭** | API 实测返回 `error_code`/`error_message`/`exit_code` |

| 级别 | 编号 | 新增问题 |
|---|---|---|
| **BLOCK** | **R2-01** | M-02 只修了全量安装脚本。**内网所有版本（v1.6.3.2、v1.6.3.4，测试与生产四份手册）都走 `upgrade_incremental.sh`**，该脚本 0 处引用 runner → 按内网实际流程升级，**runner 根本不会被装、不会启动，本次修复到不了生产**。另有两处偏离设计：install.sh **先起 Web 再起 runner**（设计要求相反）、runner 未起来只 **warning 不返回非零** |
| MAJOR | R2-02 | **新增回归**：`verify_deploy.sh` 的 runner 检查是**无条件**的，导致既有 `tests/test_verify_deploy_contract.py` **3 条用例变红**（无 systemd 环境必然失败） |
| NIT | R2-03 | `METADATA_TMP_DIR` 未实现（产物实走 `REPORT_OUTPUT_DIR/metadata-audit`）；不影响功能，但设计与实现应对齐 |

---

## 2. 变异测试：新补的用例是真锁

用例全绿不等于有效。我故意改坏 6 处被测逻辑，验证对应用例是否真会变红：

| 变异 | 结果 |
|---|---|
| M1 让 25% 预检恒通过 | ✅ 红：`test_precheck_rejects_when_rss_over_25pct` |
| M2 去掉 RSS 上下界校验 | ✅ 红：`test_limits_out_of_range_rejected` |
| M3 摘掉 `MetadataJobError` 异常处理器 | ✅ 红：`test_main_has_metadata_job_error_handler` |
| M4 让 `error_code` 不再暴露 | ✅ 红：`test_job_summary_exposes_error_fields` |
| M5 让磁盘受理前置检查恒通过 | ✅ 红：**3 条**（`test_disk_check_before_accept` / `_insufficient` / `_unavailable`） |
| **M6 重现 B-01（删掉 `Optional` 导入）** | ✅ 红：**5 条**，含 `test_main_app_importable` |

**6/6 全部捕获。**M6 尤其关键——它证明我上轮要求的那条"能不能 import 起来"的交付门禁**真的能挡住同类问题**，不是摆设。

---

## 3. 逐项复验详情

### 3.1 B-01 已关闭

交付状态下 `import backend.services.metadata_artifacts` 与 `from backend.main import app` 均成功（路由 38 条）。新增 `tests/test_v1635_delivery_gate.py`，含三类用例：模块可导入、**模块注解可求值**（正是 B-01 的失效点）、`main.app` 可导入。变异复现后 5 条变红。

### 3.2 B-02 已关闭（功能实测，非 grep）

参数落地 **9/10**（`METADATA_TMP_DIR` 见 R2-03）。新增 `backend/services/metadata_job_process.py` 提供 `validate_limits` / `precheck_runner_start` / `check_disk_before_accept`。我用子进程注入不同环境变量实测：

| 注入 | 结果 |
|---|---|
| 默认配置 | ✅ 通过 |
| `RSS=511`（下界外） | ✅ 拒绝："超出允许范围 [512,4096]" |
| `RSS=4097`（上界外） | ✅ 拒绝（范围 ＋ 25% 两条都报） |
| `RSS=4096`（范围内） | ✅ 在本沙箱被 25% 规则拒绝："4294967296 字节超过有效容量 16856092672 的 25%" |
| `JOB_TIMEOUT=100` | ✅ 拒绝："超出允许范围 [300,7200]" |
| 总量 512 < 单任务 1024 | ✅ 拒绝（范围 ＋ "总量必须 >= 单任务上限"两条） |

**25% 规则是环境敏感且计算正确的**：本沙箱有效容量 15.7 GiB，4096 MiB×4=16 GiB 超出故拒绝；换到内网 61.59 GiB 主机则 4096 合法（16 GiB < 61.59 GiB）。这正是设计要的行为。

`METADATA_START_TIMEOUT_SECONDS` 已实现（默认 30）。runner 日志现含 `pid_budget_mib`、`cancelled`、`rss_exceeded` 字段。

### 3.3 B-03 已关闭

`backend/main.py` 注册了 `MetadataJobError` 处理器。实测 runner 未启动时提交任务：

```text
HTTP/1.1 503 Service Unavailable
{"code":"EXECUTOR_UNAVAILABLE","detail":"元数据执行服务未就绪（runner 未启动），请稍后重试或联系管理员。",
 "message":"…","request_id":"15675b3f0fda40ec"}
```

上轮是裸 `500 Internal Server Error`，现在是带 code、带 request_id 的 503。

### 3.4 M-01 已关闭

`metadata_runner.py:121-128`：child 异常退出且未发布时，`_sanitize_log(res.stderr_tail)` 去 CRLF 限长后，**写入 runner 日志（尾部 800 字符）与 `error_message`（尾部 400 字符）**，错误码 `CHILD_EXITED`。与我上轮要求的"脱敏、限长、不落全量输出"一致。

### 3.5 N-01 已关闭

实测强制子进程失败（目标库被删）：

```text
API: FAILED | WORKER_ERROR | 审核执行失败: (1049, "Unknown database 's5t'") | exit_code=1
```

上轮 API 返回的是 `error_code=None, error_message=None`，现在原因完整可见。

### 3.6 主流程与既有契约

* happy path：`ACCEPTED → SUCCEEDED/DONE`，`report_id` 非空，`rc=0`
* 三个 v1635 用例文件 **57 项全部通过**（上轮 29 项）

---

## 4. 新增/未关闭问题

### R2-01（BLOCK）部署只接了全量安装脚本，而内网走的是增量升级——本次修复到不了生产

**事实**

| 脚本 | 引用 `metadata-runner` |
|---|---|
| `deploy/install.sh` | ✅ 8 处（安装 unit、enable、restart、is-active 检查） |
| `deploy/verify_deploy.sh` | ✅ 3 处 |
| **`deploy/upgrade_incremental.sh`** | ❌ **0 处** |
| **`deploy/apply_patch.sh`** | ❌ **0 处** |
| **`deploy/rollback.sh`** | ❌ **0 处** |

**而内网从来不用 `install.sh` 升级。**四份部署手册全部使用增量脚本：

```text
docs/DEPLOY-v1.6.3.2-内网测试环境增量更新部署手册.md:121  bash deploy/upgrade_incremental.sh /opt/tdsql-sqlcheck 8000
docs/DEPLOY-v1.6.3.2-内网生产环境增量更新部署手册.md:143  通过 SSH 远程调用包内专用的 upgrade_incremental.sh 脚本
docs/DEPLOY-v1.6.3.4-内网测试环境增量更新部署手册.md
docs/DEPLOY-v1.6.3.4-内网生产环境增量更新部署手册.md
```

**后果**：按内网既有流程升级到 v1.6.3.5——代码更新了，**runner 不会被安装、不会被启动**。Web 侧一收到任务请求就走"runner 未就绪"分支返回 503（B-03 修好之后至少提示清楚了，但功能仍然 100% 不可用）。**也就是说，这一版为解决 6000 表故障所做的全部工作，按当前部署脚本根本到不了生产环境。**

设计 Rev.B §12.2 第 7 条对此有原文要求：

> `apply_patch.sh` 与增量升级、make_patch 产物遵循相同双服务协议；**不能只有全量安装脚本支持新 runner**。

**另有两处偏离设计**（同属本条）：

1. **启动顺序反了**。`install.sh:145` 先 `systemctl restart tdsql-sqlcheck`（Web），`:149-154` 才装并启动 runner。设计 §12.2 第 5 步要求：**先起 runner 并通过校验 → accepting=1 → 再起 Web**。当前顺序下，Web 起来后到 runner 就绪前存在一个窗口，所有任务请求都会 503。
2. **runner 起不来只告警不失败**。`install.sh:155-157` 用 `is-active … || log "警告: …"`，脚本继续并最终打印成功。设计 §12.2 第 5 步要求：**失败时整体安装/升级返回非零，不打印"升级圆满完成"**。

**整改**

1. `upgrade_incremental.sh`、`apply_patch.sh`、`rollback.sh` 按 `install.sh` 同等协议接入 runner（渲染/安装 unit、daemon-reload、按序启停、健康校验）；`make_patch` 产物需包含 unit 模板与 workers 包。
2. 调整为**先 runner 后 Web**：装 unit → 起 runner → 校验（版本/存储/恢复）通过 → 再 restart Web。
3. runner 校验失败时**脚本返回非零并终止**，不得继续打印成功。
4. 补一条部署契约用例：**断言三个增量/回滚脚本都包含 runner 安装与启停段落**（纯静态断言即可，成本很低，防止再次只改一个脚本）。

### R2-02（MAJOR，新增回归）`verify_deploy.sh` 的 runner 检查无条件执行，打挂 3 条既有用例

**回归对照**（同一套件、施工前后独立元数据库）：

```text
施工前基线 = 544 行     本轮 = 547 行     diff = 3 行（全部为新增 FAILED）
> FAILED tests/test_verify_deploy_contract.py::test_healthy_service_all_pass
> FAILED tests/test_verify_deploy_contract.py::test_huge_front_page_no_sigpipe_false_failure
> FAILED tests/test_verify_deploy_contract.py::test_large_utf8_rules_payload_on_git_bash
```

失败原因一致：

```text
[PASS] 健康探针 HTTP 成功
[PASS] 版本号 1.6.3.5
[FAIL] metadata-runner 服务未运行（在线元数据审核不可用；查 journalctl -u tdsql-metadata-runner）
[PASS] 首页可访问
…
assert code == 0
```

这些用例用 mock 服务验证 `verify_deploy.sh` 的契约，**环境里没有 systemd**，于是 runner 检查必然失败 → 整个脚本返回非零 → 3 条用例全红。

**判断**：检查本身是对的（设计要求 verify_deploy 覆盖 runner 健康），**问题是它无条件执行**。除了打挂既有用例，任何非 systemd 环境（开发机、容器、CI）跑 `verify_deploy.sh` 都会必然失败。

**整改**

* 当 `systemctl` 不存在或系统未以 systemd 为 PID 1 时，该项应输出 **SKIP（并说明原因）而非 FAIL**，不影响脚本退出码；
* 真实 systemd 环境下保持 FAIL 语义不变；
* 同步更新 `tests/test_verify_deploy_contract.py`：**两个分支都要有用例**（有 systemd → 检查生效；无 systemd → SKIP 且整体通过）。

### R2-03（NIT）`METADATA_TMP_DIR` 未实现

设计 §5.2 列了该参数，实现未落；产物根目录实走 `REPORT_OUTPUT_DIR/metadata-audit`（`metadata_artifacts.py:26-31`）。复用既有环境变量本身是合理的，**不影响功能**。建议二选一：实现该参数，或在设计中删除并注明"复用 `REPORT_OUTPUT_DIR`"。**不要让设计与实现长期不一致。**

---

## 5. 整改清单与三轮准入

| 编号 | 级别 | 一句话 | 准入判据 |
|---|---|---|---|
| R2-01 | BLOCK | 增量/补丁/回滚三个脚本接入 runner；先 runner 后 Web；校验失败返回非零 | 三脚本均含 runner 段落且有静态断言用例；install 顺序调整；失败路径返回非零 |
| R2-02 | MAJOR | `verify_deploy.sh` 的 runner 检查在无 systemd 时降级为 SKIP | 3 条既有用例恢复绿；新增无 systemd 分支用例 |
| R2-03 | NIT | 设计与实现就 `METADATA_TMP_DIR` 对齐 | 二选一并注明 |

**DU-1（核心算法）继续保持通过**——上轮已实证等价性（1800 语料 0 差异）与收益（内存 20 倍、GC 停顿 45 倍、耗时反而更快），本轮回归未触及。**三个问题全部在 DU-2 的部署与验证层，不涉及算法与任务执行主流程。**

三轮我只做定点复验 ＋ 一次变异（改坏部署脚本断言，确认新用例会红），不重开完整 SIT。

## 6. 向 Mr.Linsang 汇报

**上轮那六个问题，Q 修好了五个，而且我验证过这些修复是"真锁"不是摆设。**

我故意把代码改坏六处——让内存预检形同虚设、把异常处理器摘掉、让失败原因不再显示、甚至把上轮那个"应用起不来"的 bug 原样重现一遍——**六次全部被新加的测试抓住变红**。特别是最后那个：我上轮要求加的"能不能启动起来"的门禁检查，重现 bug 后**五条用例同时变红**,证明这道闸真的能挡住同类问题。

具体说几个：应用现在能正常启动了；那一整层资源护栏（内存上限、磁盘检查、25% 内存比例预检）都真实实现并且我实测生效了；以前所有业务错误都返回裸的"500 内部错误"，现在返回的是带错误码的 503；任务失败的原因现在能完整显示出来（我实测强制失败，页面上能看到"Unknown database 's5t'"这样的具体原因）。

**但有一个问题只修了一半，而且这一半恰好是关键的一半。**

Q 把 runner 的安装接进了 `install.sh`（全量安装脚本）——**可是内网从来不用这个脚本升级**。我查了四份部署手册，v1.6.3.2 和 v1.6.3.4、测试和生产，**全都是用 `upgrade_incremental.sh` 增量升级的**。而增量升级脚本、补丁脚本、回滚脚本里，**runner 的引用是零**。

**后果很直接：按内网现在的升级流程装上去，代码是新的，但那个执行服务根本不会被安装、不会启动。这一版为了解决 6000 表故障做的全部工作，到不了生产环境。**

方案里其实白纸黑字写过这条——"不能只有全量安装脚本支持新 runner"——只是这次没做到。另外还有两处小偏离：装的时候是先起 Web 再起 runner（方案要求反过来），以及 runner 起不来时只打个警告就继续，最后还是报"安装成功"（方案要求这种情况必须返回失败）。

**另外发现一个新的回归**：Q 给部署验证脚本加的 runner 检查是无条件的，结果把三条既有的测试打挂了——那些测试跑在没有 systemd 的环境里，这个检查必然失败。检查本身是对的，只是得在没有 systemd 时跳过而不是判失败。

**核心算法那部分继续保持通过**，上轮已经实证过等价性和收益（内存省 20 倍、GC 停顿降 45 倍、耗时还更快了），这轮没有被触及。**三个问题全在部署和验证这一层，不涉及算法和任务执行主流程**，改动量都不大。

## 7. 测试边界声明

1. 本轮**未修改仓库任何文件**。变异测试全部"改—跑—`git checkout` 还原"，结束后 `git status` 干净，已核验。
2. 上轮我打的那个临时补丁**本轮不再需要**——B-01 已由 Q 正式修复，交付状态可直接启动。
3. 回归对照为同一套件、施工前后**独立元数据库**各跑一次，逐行 diff；3 行差异已定位到具体原因（R2-02），不是环境波动。
4. **未覆盖**：内网真实 6000+ 表容量验收（§11.4 三次）、CORE_SAFE 回退制品与 JOB-21 往返、取消/恢复/断电重入、产物损坏分支、浏览器端真实点击（UI-10）、多 worker 并发受理竞争、RSS 超限的**真实触发**（我只验证了参数校验与代码路径，未在沙箱制造真实内存越界）。这些不因本轮通过项而推定通过。
5. 25% 预检的判定用的是**本沙箱**的有效容量（15.7 GiB），不是内网的 61.59 GiB；两者结论不同属于设计预期的环境敏感行为，已在 §3.2 说明。

---

测试人：智能体 A（ClaudeA）
被测版本：v1.6.3.5 `main@1ab5130`
提交给：Mr.Linsang
