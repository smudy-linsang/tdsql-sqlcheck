# SIT-v1.6.3.5 第一轮系统集成测试报告

| 项 | 内容 |
|---|---|
| 被测版本 | v1.6.3.5，`main` / `480b873`（DU-1 `1fb7ce3` ＋ DU-2 `480b873`） |
| 对照基线 | `main` / `1f274cd`（施工前，独立 git worktree） |
| 设计依据 | `docs/DETAIL-v1.6.3.5-大库在线元数据审核稳定性修复.md` Rev.B |
| 测试方 | 智能体 A |
| 测试日期 | 2026-09-09 |
| **测试结论** | **不通过。3 项 BLOCK、2 项 MAJOR、1 项 MINOR。核心算法（DU-1）质量很高、性能收益显著且等价性我已实证；但交付状态下应用根本起不来，且设计中整层资源护栏与错误分类未实现。** |

---

## 1. 结论摘要

| 级别 | 编号 | 问题 | 影响 |
|---|---|---|---|
| **BLOCK** | **B-01** | `backend/services/metadata_artifacts.py:146` 用了 `Optional` 却**没有 import** | **整个应用无法启动**。`main.py → api/metadata_audit.py → metadata_artifacts.py` 链上 `NameError`。三个新用例文件之一**连收集都过不了**。说明交付状态**一次都没被运行过** |
| **BLOCK** | **B-02** | 设计 §5.2/§5.4 的**整层资源护栏未实现**：10 个 `METADATA_*` 参数只落了 2 个；**零 RSS 采样代码**、**零磁盘空间检查**；**25% MemTotal 预检不存在** | 这正是我第一轮 P1-01 的整改内容（当时唯一的阻断项）与磁盘补充建议。等于本版**没有任何资源保护**——子进程可无限增长，磁盘可写满 |
| **BLOCK** | **B-03** | `MetadataJobError` 带 `code/message/http_status`，但**全仓没有任何异常处理器**把它转成 HTTP 响应 | 所有业务错误一律变成裸的 **500 Internal Server Error**。实测 runner 未启动时返回 500 而非设计的 503。§8.3 的错误分类**整体失效**（7 个设计错误码全部不存在） |
| MAJOR | M-01 | runner **丢弃子进程 stdout/stderr**；失败只记通用 `WORKER_ERROR` | 子进程失败时**没有任何诊断线索**。我自己排查时就卡在这里，只能去数据库翻 |
| MAJOR | M-02 | runner 的 systemd unit 文件存在，但**没有任何部署脚本引用它**；Web unit 也**未固定** `--timeout-worker-healthcheck 5` | 按现状部署，**runner 永远不会启动**，新功能全部不可用；且设计 §12.1 承诺的 5 秒窗口固定未落地 |
| MINOR | N-01 | 失败任务的 `error_code`/`error_message` **不经 API 暴露**（DB 有 `WORKER_ERROR`，API 返回 `null`）；`exit_code` 也未落库 | 用户只看到"失败"，看不到任何原因 |

**一句话**：**DU-1（核心算法）做得很好，我实证了等价性和收益；DU-2（任务架构）骨架能跑通，但护栏层、错误分类层、部署接线三块基本是空的，且交付状态连启动都启动不了。**

---

## 2. B-01：交付状态下应用无法启动

```text
$ python3 -c "import backend.services.metadata_artifacts"
  File "backend/services/metadata_artifacts.py", line 146, in <module>
    def read_result_detail(job_id: str, statement_index: int) -> Optional[dict]:
NameError: name 'Optional' is not defined
```

该文件 import 段只有 `hashlib / json / logging / os / pathlib.Path`，**没有 `from typing import Optional`**。因为它出现在**模块级函数注解**上，import 时即求值即崩。

**传导链**：`backend/main.py` → `backend/api/metadata_audit.py:26` → `metadata_artifacts.py` → `NameError`。实测 `from backend.main import app` 直接失败。

**同类排查**：我把 5 个新模块逐个 import，并交叉核对了各文件"用到的 typing 名 vs 已导入的 typing 名"——**只有这一个文件有问题**，其余 4 个都正确导入了 `Optional`。所以是孤立的一行遗漏，不是系统性问题。

（说明：我一度想用 pyflakes 做静态扫描，但本机没装该模块，那次扫描**是空跑**，结论不采信，已改用逐模块 import ＋ 名称交叉核对。）

**整改**：`metadata_artifacts.py` import 段补 `from typing import Optional`。

**但真正要处理的不是这一行**：一个模块级 `NameError` 意味着**这份交付从未被启动过一次**，`tests/test_v1635_metadata_artifacts.py` 也从未被收集过。建议在交付门禁里加一条最低限度的冒烟：`python -c "from backend.main import app"` 必须成功——**成本一行，能挡住整类"根本没跑过"的交付**。

---

## 3. B-02：设计的整层资源护栏没有实现

我按设计 §5.2 的参数表逐项在代码里找：

| 参数 | 设计要求 | 实现 |
|---|---|---|
| `METADATA_MAX_CONCURRENT` | 固定 1 | ✅ `metadata_runner.py:53` |
| `METADATA_JOB_TIMEOUT_SECONDS` | 1800 | ✅ `metadata_runner.py:52` |
| `METADATA_START_TIMEOUT_SECONDS` | 30 | ❌ |
| **`METADATA_CHILD_RSS_LIMIT_MIB`** | **1024（P1-01 整改核心）** | ❌ |
| `METADATA_SQL_MAX_MIB` | 256 | ❌ |
| `METADATA_RESULTS_MAX_MIB` | 256 | ❌ |
| `METADATA_ARTIFACT_MAX_MIB` | 1024 | ❌ |
| `METADATA_ARTIFACT_TOTAL_MAX_MIB` | 10240 | ❌ |
| `METADATA_MIN_FREE_BYTES` | 2 GiB | ❌ |
| `METADATA_TMP_DIR` | 独立目录 | ❌ |

并且：

* **零 RSS 采样代码**——全仓检索 `VmRSS`/`statm`/`/proc/*/status`/`memory_info`，在 workers 与 metadata 服务中**一处都没有**。而 `metadata_runner.py:10` 的文档字符串明写"每 1 秒复核取消意图/超时/**child RSS**"——**注释描述了一个不存在的功能**。
* **零磁盘空间检查**——全仓 `statvfs`/`f_bavail`/`disk_usage` 在本模块无命中。
* **25% MemTotal 硬预检不存在**——`MemTotal` 在 backend 下 0 命中。

**这一整块正是我第一轮 P1-01 的整改内容（当时唯一的阻断项），以及我补充建议的磁盘水位。设计 Rev.B 写得非常细（有效容量取 `min(MemTotal, 有限 limit)`、`rss*4 > capacity` 才拒绝、安装与每次 runner 启动都校验、读数失败 fail-closed），但代码里一行都没有。**

**实测佐证**：`METADATA_START_TIMEOUT_SECONDS` 缺失的后果我直接观察到了——runner 未运行时提交任务，任务**永久停在 `ACCEPTED/WAITING`**，没有任何超时收口（设计要求 30 秒后记 `START_TIMEOUT` 并释放槽位）。

**整改**：按 §5.2/§5.4 补齐 8 个参数、RSS 采样与回收、磁盘受理前置检查、25% 预检（安装期 ＋ 每次 runner 启动），并补 JOB-17/18/20 对应用例。

---

## 4. B-03：业务错误全部退化成裸 500

`metadata_audit_repository.py:65-71` 定义得很规范：

```python
class MetadataJobError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 409):
        self.code = code; self.message = message; self.http_status = http_status
```

**但没有任何地方把它翻译成 HTTP 响应**——`backend/main.py` 中 `exception_handler` **0 命中**，受理路由也没有捕获它。

**实测**：runner 未启动时提交任务 →

```text
HTTP 500
Internal Server Error
```

而服务端日志里是设计预期的失败关闭：`MetadataJobError: 元数据执行服务未就绪（runner 未启动），请稍后重试或联系管理员。`——**逻辑对了，但用户拿到的是一个裸 500**，与"真的崩了"无法区分，`http_status` 字段完全没被使用。

设计 §8.3 的 7 个错误码在代码中**全部不存在**：`ONLINE_METADATA_DISABLED`、`RUNNER_UNAVAILABLE`、`STORAGE_CHECK_UNAVAILABLE`、`INSUFFICIENT_STORAGE`、`ARTIFACT_QUOTA_EXCEEDED`、`RESOURCE_LIMIT`、`START_TIMEOUT`。

**整改**：在 `backend/main.py` 注册

```python
@app.exception_handler(MetadataJobError)
async def _metadata_job_error(request: Request, exc: MetadataJobError):
    return JSONResponse(status_code=exc.http_status,
                        content={"code": exc.code, "detail": exc.message,
                                 "message": exc.message,
                                 "request_id": getattr(request.state, "request_id", "")})
```

并按 §8.3 补齐 7 个错误码的抛出点。回归锁：**断言 runner 未就绪时返回 503 且响应体含 `code`，而不是 500。**

---

## 5. MAJOR 与 MINOR

### M-01 runner 丢弃子进程输出，失败无从诊断

`metadata_runner.py:96-97` 调用 `run_analysis_process`（复用 v1.6.3.4 的网关子进程封装，该封装用 `subprocess.PIPE` **捕获**了 stdout/stderr），但 runner **拿到 `proc` 之后从不读取也不记录 `proc.stdout/stderr`**，只记 `rc=1`。

失败时数据库里写的是通用 `error_code=WORKER_ERROR`。**这两点叠加的结果是：子进程为什么死，现场没有任何线索。**我自己排查时就卡在这里——只能去数据库翻 `error_message` 才知道原因。

考虑到本版的整个立项背景就是"一个进程静默消失、查不到原因"，**这条尤其讽刺，也尤其该修**。

**整改**：子进程非零退出时，把 `proc.stderr` 的**末尾 N KiB**（建议 8 KiB）脱敏后写入 runner 日志与 `error_message`；日志字段沿用 §12.4 已定义的 `child_pid/exit_code/error_code`。**不要**记全量输出（可能含 SQL 片段）。

### M-02 部署完全没有接线

| 检查 | 结果 |
|---|---|
| `deploy/tdsql-metadata-runner.service` 文件存在 | ✅ |
| 任何部署脚本引用 `metadata-runner` | ❌ **0 处**（install/upgrade_incremental/apply_patch/rollback/verify_deploy 全无） |
| Web unit 固定 `--timeout-worker-healthcheck 5` | ❌ 未添加 |

按现状部署：**runner 不会被安装、不会被启动、不会被校验**。而 Web 侧一旦收到任务请求就会走到"runner 未就绪"分支——也就是**新功能 100% 不可用**（且因 B-03 表现为 500）。

设计 §12.2 第 4/5 步明确要求：停旧 → 迁移 → **先起 runner 并通过版本/存储/恢复校验 → accepting=1 → 再起 Web**；§12.3 要求 CORE_SAFE 预建制品。这些都还没开始。

**整改**：按 §12.1—12.3 补齐双服务安装/升级/回滚/verify_deploy，并在 Web unit ExecStart 追加 `--timeout-worker-healthcheck 5`（CLI 优先于 env，见设计 §12.1）。

### N-01 失败原因不经 API 暴露

```text
API : {'state': 'FAILED', 'error_code': None, 'error_message': None}
DB  : FAILED / WORKER_ERROR   （error_message 有完整原因）
```

任务详情接口把 `error_code`/`error_message` 丢了；`exit_code` 也没落库（DB 中为 NULL，尽管 runner 日志记了 `rc=1`）。用户只看到"失败"，看不到任何原因——这与 §8.3/§9 "浏览器显示真实阶段、计数、失败原因"直接冲突。

**整改**：任务详情响应补 `error_code`/`error_message`/`exit_code` 三个字段；补一条断言"FAILED 任务的 `error_code` 非空"。

---

## 6. 核验通过的部分（DU-1 质量很高）

### 6.1 R035 有界见证索引：等价性实证通过

我没有采信设计的等价性证明，也没有复用 O 或 Q 的模型，而是**用真实新实现跑差分**：

* oracle：复刻 v1.6.3.4 语义（逐句解析 ＋ **完整历史** ＋ 首个冲突）
* 被测：HEAD 的 `audit_file`（有界见证 ＋ 流式）
* 比对：**逐语句比对完整违规集合，含 rule_id、级别与消息前 200 字**（不只比 R035 有无）
* 语料：300 组随机 × {3,6,12} 表 × {分布式,集中式} ＝ **1800 个语料**

```text
R035 等价性差分：1800 个语料 × 逐语句比对，差异 0 处
```

### 6.2 性能收益：实测优于设计预期，且我第二轮的 GC 预测被证实

**配对测量**（同一脚本、同一仪表、子进程交替执行修复前/后，用 `ru_maxrss` 测真实 RSS 而非 tracemalloc）：

| 表数 | 修复前耗时 | 修复后耗时 | 耗时比 | 修复前 RSS | 修复后 RSS | 修复前最长 GC 停顿 | 修复后 |
|---|---|---|---|---|---|---|---|
| 800 | 5.49 s | 4.15 s | 0.76× | 182.3 MB | 43.9 MB | 0.198 s | 0.020 s |
| 1600 | 12.43 s | 8.32 s | 0.67× | 427.4 MB | 49.4 MB | 0.448 s | 0.022 s |
| 3200 | 36.34 s | 16.98 s | **0.47×** | **1218.2 MB** | **60.1 MB** | **1.297 s** | **0.029 s** |

* **内存：3200 表 1218 MB → 60 MB，约 20 倍**，且修复后近乎持平（43.9→49.4→60.1），不再随规模膨胀
* **耗时：不但没变慢，反而快了一倍以上**（0.47×）——O 在评审中担心的"完整历史扫描 CPU 平方级"确实存在于旧实现，有界见证把它一并消掉了
* **GC 停顿：1.297 s → 0.029 s，约 45 倍**，且修复后基本不随规模增长

**两处与现场证据的印证**：

1. 修复前 3200 表实测 RSS 1218 MB，外推 6000 表约 **2.7—3.0 GB**——与内网 service cgroup 历史高水位 **3.62 GiB** 量级吻合，说明我的合成负载对真实负载有代表性。
2. 我在第二轮评审中预测"修复后 GC 停顿远离 uvicorn 的 5 秒强杀线"，**实测修复后 3200 表仅 0.029 s，外推 6000 表约 0.04 s，余量约百倍**——预测成立。

### 6.3 旧路径退役：八项契约全部正确

| 检查 | 结果 |
|---|---|
| 状态码 | `410 Gone` ✅ |
| `detail` 为**字符串**（O 对我示例的纠正） | ✅ |
| 顶层 `code`/`message`/`docs`/`request_id` | ✅ |
| `Cache-Control: no-store` | ✅ |
| `X-Request-ID` 响应头 | ✅ |
| **零副作用**：任务表行数 before=after=0 | ✅ |
| 空 body 不被旧校验抢先变 422 | ✅ 仍 410 |
| 未认证返回 401 而非 410（不成为公开探针） | ✅ |

### 6.4 主流程可跑通、v1.6.3.4 契约未回退

runner 启动后提交任务：`ACCEPTED → SUCCEEDED/DONE`，`report_id` 非空，`rc=0`。四个产物接口（详情/结果分页/SQL 预览/HTML）**全部 200**。

**并且 v1.6.3.4 的报告实例标识契约没有回退**——新任务导出的 HTML 中正确出现 `实例连接名称：SIT135-实例`。

### 6.5 回归对照：零新增失败

同一套件、施工前后**独立元数据库**各跑一次，`FAILED/ERROR/SKIPPED` 清单排序后逐行 diff：

```text
baseline=544 行   head=544 行   diff 行数：0
```

**Q 的改动没有引入任何新增失败。**三个新用例文件 29 项全部通过（B-01 打补丁后）。

---

## 7. 整改清单与二轮准入

| 编号 | 级别 | 一句话 | 二轮准入判据 |
|---|---|---|---|
| B-01 | BLOCK | 补 `from typing import Optional` | `python -c "from backend.main import app"` 成功；该冒烟进交付门禁 |
| B-02 | BLOCK | 补齐 8 个 `METADATA_*` 参数、RSS 采样与回收、磁盘受理前置、25% MemTotal 预检 | JOB-17/18/20 可运行且能失败；`METADATA_START_TIMEOUT_SECONDS` 生效（runner 停机时任务 30 秒内收口而非永久 ACCEPTED） |
| B-03 | BLOCK | 注册 `MetadataJobError` 异常处理器，补 7 个错误码 | runner 未就绪返回 **503 + code**，非 500 |
| M-01 | MAJOR | 子进程失败时记录脱敏 stderr 尾部 | 故障注入后 runner 日志与 `error_message` 含可定位原因 |
| M-02 | MAJOR | 双服务部署接线 ＋ Web unit 固定 5 秒窗口 | 部署脚本安装/启动/校验 runner；先 runner 后 Web；`verify_deploy` 覆盖 |
| N-01 | MINOR | 任务详情补 `error_code`/`error_message`/`exit_code` | FAILED 任务 `error_code` 非空 |

**DU-1 可以判通过并保留**——等价性与收益我都实证了，且回归零新增失败。**B-01/B-02/B-03 全部集中在 DU-2**。这正好印证了我第一轮评审建议的 DU-1/DU-2 分单元交付：**DU-1 现在就具备独立验收条件，DU-2 需要返工。**

二轮我会做**变异测试**（故意改坏护栏与错误分类，验证新补的用例真会变红），并复验 B-01 的冒烟门禁是否真的能挡住同类问题。

## 8. 向 Mr.Linsang 汇报

**先说结论：这一版不能上，但坏消息比看上去的轻——真正的核心修复做得很好，出问题的是外围。**

**最要命的一条：这份代码交付过来是启动不了的。**有个文件用了一个类型名却忘了 import，**整个应用一 import 就崩**，三个新测试文件里有一个连收都收不上来。一行的事,但它说明的问题不小——**这份交付从头到尾没被运行过一次**。我建议在交付门禁里加一句话的冒烟检查（就是"能不能 import 起来"），这类事以后就不会再漏出来。

**第二条：方案里设计得很细的那一整层资源护栏，代码里一行都没有。**十个参数只落了两个,子进程内存监控**完全没写**（但注释里写着"每秒检查 child RSS"——描述了一个不存在的功能）,磁盘空间检查没有,我们上一轮争论了半天、最后定成 1024 MiB 的那个内存上限**根本不存在**。也就是说这版**没有任何资源保护**。我实测验证了一个后果：runner 没起来的时候提交任务，任务会**永远卡在"已受理"**，因为超时收口那个参数也没实现。

**第三条：所有的业务错误都变成了裸的 500。**代码里定义了很规范的错误类型（带错误码、中文提示、HTTP 状态码），但**没有任何地方把它翻译成 HTTP 响应**。我实测 runner 没起来时提交任务，返回的是"500 Internal Server Error"——**逻辑其实是对的（它确实是想告诉你 runner 没就绪），但用户看到的和真崩了一模一样**。设计里定的七个错误码，代码里一个都没有。

还有两条重要的：**runner 把子进程的错误输出全扔了**，子进程死了没有任何线索（我自己排查时就卡在这，只能去数据库翻）——考虑到这版的立项背景就是"进程静默消失查不出原因"，这条特别该修；以及**部署脚本压根没接 runner**，按现在这样装上去，那个执行服务永远不会启动，新功能 100% 用不了。

**现在说好消息，而且是实打实的好消息。**

**核心算法（我们讨论最久的那个"有界见证索引"）做得很好，我实证过了：**我拿真实的新代码跟"保留完整历史"的标准答案做差分，1800 组语料逐条比对（连错误消息文字都比），**零差异**。

**性能收益比方案预期还好：**

| 3200 张表 | 修复前 | 修复后 |
|---|---|---|
| 内存 | 1218 MB | **60 MB**（省 20 倍） |
| 耗时 | 36.3 秒 | **17.0 秒**（快一倍多） |
| GC 停顿 | 1.297 秒 | **0.029 秒**（降 45 倍） |

**不但省内存,还更快了**——O 当初担心我那个简单方案"CPU 会退化成平方级"，他是对的，而这个有界见证方案把内存和 CPU 一起解决了。

还有两处印证很有意思：修复前 3200 表实测内存 1218 MB，外推到 6000 表约 2.7—3.0 GB，**跟内网记录的 3.62 GiB 高水位对得上**，说明我的模拟负载是有代表性的；另外我上一轮预测"修复后 GC 停顿会远离那道 5 秒线"，**实测 0.029 秒,余量约一百倍**,预测成立。

另外旧接口退役那八条契约**全部正确**（包括 O 纠正我的那个字符串格式）,主流程跑得通,**v1.6.3.4 的实例名称契约也没有回退**,而且**全量回归零新增失败**。

**所以我的建议是：把 DU-1（核心修复）和 DU-2（任务架构）分开处理。**DU-1 现在就可以判通过——等价性和收益我都实证了。三个阻断项**全部集中在 DU-2**，需要返工。这正好印证了我第一轮评审时建议的分单元交付。

## 9. 测试边界声明

1. **我对仓库做过一次临时改动并已还原**：为让测试能继续，我在本地给 `metadata_artifacts.py` 补了那行 import，测完立即还原；`git status` 干净、`git diff HEAD` 为空，并已复验**还原后该缺陷确实仍然存在于交付状态**。该改动**从未提交**。
2. **§6.5 的回归对照是在打了该补丁的状态下跑的**——不打补丁，整个套件会因收集失败而无法比较。这一点必须明示。
3. 我一度用 pyflakes 做静态扫描，但**本机没有安装该模块，那次扫描是空跑**，结论已作废，改用逐模块 import ＋ typing 名交叉核对。
4. 子进程首次失败（`WORKER_ERROR`）是**我沙箱 MariaDB 的 `int(11)` 与 MySQL `int` 迁移口径差异**导致，属环境问题，**未记入 Q 的缺陷**；为继续测试，我通过 `sitecustomize.py` 让子进程也加载兼容补丁（同样只在沙箱，未入仓库）。
5. **未覆盖**：内网真实 6000+ 表容量验收（§11.4 三次）、CORE_SAFE 回退制品与 JOB-21 往返、取消/恢复/断电重入、产物损坏分支、浏览器端真实点击（UI-10）、多 worker 并发受理竞争。这些不因本轮通过项而推定通过。
6. 性能数字为本机合成负载（每表 20 列）实测，非内网实测；真实表结构不同，绝对值会有偏差。

---

测试人：智能体 A（ClaudeA）
被测版本：v1.6.3.5 `main@480b873`
提交给：Mr.Linsang
