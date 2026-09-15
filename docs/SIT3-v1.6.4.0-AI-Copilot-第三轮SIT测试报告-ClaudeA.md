# v1.6.4.0 AI Copilot 专家助手 · 第三轮独立 SIT 测试报告

| 项 | 内容 |
|---|---|
| 受测版本 | v1.6.4.0 / CP-1（受测提交 `9e1b9f4`） |
| 对照基线 | `7ff356d`（第二轮受测代码，与 `74c3e5c` 代码同源） |
| 上一轮 | [第二轮 SIT 报告](SIT2-v1.6.4.0-AI-Copilot-第二轮SIT测试报告-ClaudeA.md)（`7ff356d`） |
| 测试人 | 智能体A（独立评审/测试） |
| 报告日期 | 2026-09-15 |
| 送呈 | Mr.Linsang |

---

## 0. 结论

**B-01、N-01 两项整改全部到位，第一轮与第二轮的全部通过项重跑无回退，回归零新增失败。**

**可以进 UAT。**

本轮另发现 2 条不阻塞的遗留项（N-03 源文档漂移无锁、N-04 LF 锁在 POSIX 上是空锁），建议在 UAT 期间或下一个小版本补齐。

我还要**订正自己第二轮报告中的一段结论**，见 §1.3。

| 编号 | 级别 | 上一轮问题 | 本轮结论 |
|---|---|---|---|
| B-01 | BLOCK | 随包知识包 manifest 与实际字节不符，CP-F02 开箱即坏 | ✅ **已修复** |
| N-01 | MAJOR | X1/X3 两条锁假绿：只测辅助函数不测接线 | ✅ **已修复**，变异全部被杀 |
| N-02 | MINOR | M-03 的锁仅处女库通过 | ✅ **已修复**，同库连跑三次稳定 |

---

## 1. B-01 已修复，并且根因被我复算证实

### 1.1 干净检出上的字节核对

在 `9e1b9f4` 的独立 worktree 上逐字节核对：

| 文件 | 实际字节 | manifest 记录 | sha256 |
|---|---:|---:|---|
| chunks.jsonl | 15517 | 15517 | `951cb726…a26152` 一致 |
| index.json | 140 | 140 | `bdd6cc3c…0eb3713` 一致 |

运行期实测（不是读 manifest，是真正走加载路径）：

```
KnowledgeStore().load() => READY
status_info() => {'knowledge_status': 'READY',
                  'bundle_id': 'kb-1.6.4.0-5b8426f429c6a89a', 'reason_code': ''}
```

### 1.2 根因：不是"没重建"，是换行符

我把旧 manifest 的四项数值拿来复算，结论是**精确命中**，不是近似吻合：

| 文件 | LF 字节 | 行数 | CRLF 展开后 | 旧 manifest | 旧 manifest sha256 |
|---|---:|---:|---:|---:|---|
| chunks.jsonl | 15517 | 23 | **15540** | 15540 ✓ | 与 CRLF 字节的 sha256 **逐位相同** ✓ |
| index.json | 140 | 4 | **144** | 144 ✓ | 与 CRLF 字节的 sha256 **逐位相同** ✓ |

四项数值全部等于「把 LF 字节逐行展开成 CRLF 之后」的 size 与 sha256。机制是：Python 的 `write_text()` 在 Windows 上默认按 `os.linesep` 翻译换行，builder 落盘时写的是 CRLF、算的也是 CRLF；`git add` 按 `.gitattributes` 归一化回 LF 存仓；于是**存仓字节是 LF、manifest 记的是 CRLF 口径**。Q 在开发记录 §7.1 给出的就是这个判断，与我的复算一致。

### 1.3 我要撤回第二轮报告中的一段话

第二轮我写过两句：

> 「Q 自述'重建知识包……状态 READY'——不属实」
> 「说明提交前根本没跑过这套用例」

**这两句我撤回。**

按上面已证实的机制：在 CRLF 工作副本上真实重建，产出的 manifest 同样是 CRLF 口径；此时工作区文件也是 CRLF，两边自洽，**开发者本地跑 `test_shipped_bundle_ready` 会是绿的**；只有提交归一化成 LF 之后、在 Linux 检出上才会红。所以"第二轮 manifest 数值一字未改"完全可以由"重建了但平台口径不变"解释，我据此推断"没重建、没跑用例"缺乏依据。

B-01 未修、不能进 UAT 的**结论仍然成立**，但那段关于开发纪律的指控不成立。

### 1.4 修复的实际着力点

Q 本轮动了两处，我逐一核过有效性：

- **`builder.py` 三处产物落盘显式加 `newline="\n"`** —— 这是真正起作用的修复。
- **`.gitattributes` 新增知识包 JSON/JSONL 的 `eol=lf`** —— 我在整改前的检出上跑 `git check-attr`，这两类文件**当时就已经判定为 `eol: lf`**（被第 3 行 `* text=auto eol=lf` 的通配规则覆盖），`git ls-files --eol` 也显示索引与工作区均为 `i/lf w/lf`。所以新增的两行在功能上是冗余的，属于显式化/防止日后通配规则被收窄，不是修复本身。我认为保留没有坏处，但不应被当成这次修复的依据。

### 1.5 我额外做了一次确定性核验

从 `sources/` 重建到临时目录，与随包产物比对：

```
重建 bundle_id : kb-1.6.4.0-5b8426f429c6a89a
随包 bundle_id : kb-1.6.4.0-5b8426f429c6a89a       一致
chunks.jsonl   重建 == 随包 : True
index.json     重建 == 随包 : True
manifest 差异字段: ['approved_by', 'reviewed_at']   （二者本就是构建入参）
```

也就是说随包产物**不只是自洽，而且确实是当前 sources 的忠实构建结果**。B-01 是真闭环。

---

## 2. N-01 已修复：两条变异全部被杀，且杀伤面精确

第二轮存活的 X1、X3 本轮重做：

| 变异 | 注入内容 | 结果 | 打红条目 |
|---|---|---|---|
| **X1** | 摘掉 `build()` 构建后自校验的 `raise` | ✅ **被杀** | 2 failed（`test_build_calls_self_verify_end_to_end` 两个参数化用例） |
| **X3A** | 整条端点闸失效 | ✅ **被杀** | 4 failed |
| **X3B** | 只校验主路、放过备用腿 | ✅ **被杀** | 1 failed（恰为 fallback-deny） |
| **X3C** | 部署闸失效 | ✅ **被杀** | 1 failed（恰为 deploy-deny） |
| **X3D** | 逐实例授权闸失效 | ✅ **被杀** | 1 failed（恰为 grant-deny） |

X3B/X3C/X3D 各只打红一条、且正好是对应那条参数化用例 —— 说明新的 M-01 锁不是"整体报红"式的粗锁，三道闸各自有独立可分辨的覆盖。这条锁现在真调 `build_preview()`、真读库里的 grant/provider/route/session，不再是测试体里自己重算一遍。

第二轮已被杀的三条本轮重做，全部仍被杀：

| 变异 | 结果 |
|---|---|
| X2　B 组结构错误分类恒 False | ✅ 被杀（2 failed） |
| X4　`Limits.validate()` 恒返回空 | ✅ 被杀（3 failed / 13 passed） |
| X5　settings 摘掉 `_require_ready` | ✅ 被杀（1 failed） |

---

## 3. N-02 已修复：同库连跑稳定，且零残留

同一个数据库（`t3_sit`，第 1 次为新建库，第 2、3 次不清空）连跑三次：

```
run 1: 111 passed, 18.55s
run 2: 111 passed, 15.19s
run 3: 111 passed, 15.10s
```

三次后查库，M-01 / M-03 用例的残留全部为 0：

```
users(cp_m03_*)          0        copilot_sessions         0
users(cp_m01_*)          0        copilot_providers(m01_)  0
tdsql_connections(M01*)  0        copilot_scene_routes     空
users 总数               8（与运行前一致）
```

`copilot_scene_routes` / `copilot_providers` 两张表在跑完后是空的 —— 说明 M-01 用例对路由与 provider 的写入确实整体回滚了，**没有覆盖持久场景配置**，这一点是我专门查的，因为那条用例现在要写真实路由。

---

## 4. 第一轮 + 第二轮全部通过项重跑（防回退）

### 4.1 方案乙逐表隔离 —— 9/9

B 组 9 张表逐一 DROP，每次都先恢复结构并强制 READY：

| 表 | 结构验收检出 | 读端点 | 错误码 | 状态迁移 | 核心功能 |
|---|---|---|---|---|---|
| copilot_providers | ✅ | 503 | COPILOT_SCHEMA_UNAVAILABLE | READY/2→UNAVAILABLE/3 | 正常 |
| copilot_scene_routes | ✅ | 503 | COPILOT_SCHEMA_UNAVAILABLE | READY/3→UNAVAILABLE/4 | 正常 |
| copilot_instance_grants | ✅ | 503 | COPILOT_SCHEMA_UNAVAILABLE | READY/4→UNAVAILABLE/5 | 正常 |
| copilot_sessions | ✅ | 503 | COPILOT_SCHEMA_UNAVAILABLE | READY/5→UNAVAILABLE/6 | 正常 |
| copilot_turns | ✅ | 503 | COPILOT_SCHEMA_UNAVAILABLE | READY/6→UNAVAILABLE/7 | 正常 |
| copilot_audit_events | ✅ | 503 | COPILOT_SCHEMA_UNAVAILABLE | READY/7→UNAVAILABLE/8 | 正常 |
| copilot_previews | ✅ | 无读端点 | —— | —— | 正常 |
| copilot_daily_budgets | ✅ | 无读端点 | —— | —— | 正常 |
| copilot_provider_attempts | ✅ | 无读端点 | —— | —— | 正常 |

后三张表没有直接读取它们的 GET 端点（只在 runner 侧写），因此以 `verify_business_schema()` 的结构验收为准，**9 张全部被检出**。修复后 `/copilot/sessions` 回到 200、结构验收问题数归零，自动收敛。

> 说明：我第一遍脚本对这 9 张表都打同一个端点，结果 7 张显示"未降级"。那是我的探针问题——端点没碰到被删的表，当然不报错，这恰恰是正确行为。改成逐表对应端点 + 结构验收后才是上表结果。

### 4.2 B-02 / M-02 / M-03

- **B-02**：见 §4.1，503 + `COPILOT_SCHEMA_UNAVAILABLE` + READY→UNAVAILABLE + epoch 自增 + 修复后收敛，全部成立。
- **M-02**：4 项越界参数全部返回拒绝清单而非夹值；三处执行点（`services/copilot/__init__.py:50`、`api/copilot.py:739`、`workers/copilot_runner.py:87`）仍在。
- **M-03**：12 个端点判定 12/12 正确 —— `capabilities` / `help` / `copilot-admin/health` 保持 200（只读例外），其余 9 个全部 503。

### 4.3 N-10 双向锁（子进程隔离，规避 `ensure_db` 的进程内幂等）

| 方向 | 操作 | 结果 |
|---|---|---|
| 方向一 | DROP `metadata_audit_jobs`（既有 CREATE TABLE 迁移） | ✅ 自愈重建，QC-DEFECT-07 行为保留 |
| 方向二 | DROP `copilot_subjects`（A 组注册表） | ✅ `MigrationError`：「该迁移属已登记即禁止自动重建的关键组（N-10）」，**表未被静默重建**（核对为 0） |
| 方向二(b) | DROP `copilot_runtime` | ✅ 同上，失败关闭 |

### 4.4 RBAC

| 路径 | admin | developer | auditor |
|---|---:|---:|---:|
| `/api/v1/copilot/sessions` | 200 | 200 | **403** |
| `/api/v1/copilot-admin/providers` | 200 | **403** | **403** |
| `/api/v1/copilot-audit/events` | 200 | **403** | 200 |

与设计 §9.1 一致。三个前缀在 `auth_service.py:399-443` 均已登记且判定顺序正确（`copilot-admin` / `copilot-audit` 先于通用 `copilot` 前缀），不会落到第 509 行 `return True  # 无映射的路径默认放行` 的兜底分支。

### 4.5 INV-03 无执行面

`backend/services/copilot`、`api/copilot*.py`、`workers/copilot*.py`、`copilot_knowledge` 全量扫 `requests` / `urllib` / `socket` / `subprocess` / `eval(` / `exec(` / `os.system` / `pickle.loads` —— **零命中**。出网只有 `providers.py` 一处 `httpx.AsyncClient(trust_env=False, follow_redirects=False)`。

### 4.6 B 组全灭期间的账号生命周期与核心功能

一次性 DROP 全部 9 张 B 表、模块置 UNAVAILABLE 之后：

- 创建用户 ✅　登录 ✅　重置口令 ✅　删除用户 ✅（删除后残留核对为空）
- 12 条核心端点全部 200：`/rules`、`/auth/me`、`/auth/users`、`/auth/roles`、`/auth/visible-menus`、`/admin/info`、`/admin/config`、`/admin/operation-logs`、`/tdsql/connections`、`/audit/metadata-jobs`、`/dashboard/summary`、`/health`
- 恢复结构后 Copilot 回到 200

**Copilot 整体失效对主产品零影响**，这是方案乙最关键的一条，仍然成立。

---

## 5. 回归对照

同一沙箱、同一 MariaDB 实例、两侧各自独立库，全量 `tests/`：

| | 失败 | 通过 | 跳过 | 错误 |
|---|---:|---:|---:|---:|
| base `7ff356d` | 407 | 1704 | 86 | 82 |
| head `9e1b9f4` | **403** | **1716** | 86 | 82 |

逐条求集合差（489 条 vs 485 条失败/错误条目）：

```
head 新增（base 通过 → head 失败）：（空）零新增 ✅

head 修复（base 失败 → head 通过）：4 条
  test_knowledge_output.py::TestKnowledge::test_shipped_bundle_ready
  test_knowledge_output.py::TestKnowledge::test_rule_id_exact_boost
  test_knowledge_output.py::TestKnowledge::test_stale_bundle_expired
  test_knowledge_output.py::TestKnowledge::test_store_ready_and_search
```

**零新增失败，修复 4 条**。B-01 此前连带打红的是 4 条而不是 1 条 —— 后三条都依赖随包知识包处于 READY。

> 两侧共有的约 400 项失败与 82 项 error 是沙箱既有噪声（MariaDB 10.11 对 MySQL 8 的类型宽度差异、冷库缺前置数据等），两侧完全相同，不构成本轮增量。

### 5.1 产品代码影响面

整个整改改动 7 个文件，其中**运行期代码只有 1 个**：

```
.gitattributes                              （构建/存仓约定）
backend/copilot_knowledge/builder.py        ← 唯一的运行期代码，且是构建期工具
backend/copilot_knowledge/…/manifest.json   （数据产物）
docs/DEV-…-开发记录-Q.md                     （文档）
tests/copilot/conftest.py                   （测试）
tests/copilot/test_knowledge_output.py      （测试）
tests/copilot/test_m01_m03.py               （测试）
```

`backend/services`、`backend/api`、`backend/workers`、`backend/schema`、`backend/models`、`backend/main.py` **零改动**。次生灾害面在结构上就被限死了 —— 这是本轮整改最值得肯定的一点：改对了地方，而且只改了那个地方。

---

## 6. 本轮新发现（均不阻塞 UAT）

### 6.1 【N-03｜MINOR】改源文档而不重建知识包，没有任何锁会红

变异 X6：在 `backend/copilot_knowledge/sources/01_user_guide_core.md` 末尾追加一节内容、不重建知识包。

```
[X6] 变异存活 ✗   111 passed
```

随包产物与 `sources/` 可以静默漂移：manifest 自洽（哈希对得上自己的产物）、`test_shipped_bundle_ready` 照样绿，但 Copilot 检索到的还是旧内容。`manifest.source_entries` 只记了 source_id/title/authority，**没有记源文件哈希**，所以没有任何一层能发现。

这是 B-01 的同族问题（"随包产物 ≠ 仓库真相"）往上游挪了一格。用户指南、故障条目、TDSQL 语法摘要这几份源文档在后续版本里一定会改，改完忘记重建是很自然的事。

**建议补一条确定性锁**，我已实测可行（§1.5）：从 `sources/` 重建到临时目录，断言 `bundle_id` 与 `chunks.jsonl` / `index.json` 的 sha256 与随包一致（`approved_by` / `reviewed_at` 是构建入参，排除即可）。构建是确定性的，这条锁不会抖。

### 6.2 【N-04｜提示级】`test_build_outputs_lf_bytes` 在 POSIX 上是空锁

变异 X1-nl：撤掉 builder 三处的 `newline="\n"`（也就是把本轮真正的修复回退掉）。

```
[X1-nl] 变异存活 ✗   111 passed
```

因为在 Linux 上 `newline="\n"` 与默认行为等价，这条断言永远不会红。Q 自己做的 `B01-LF` 变异（把输出主动改写成 CRLF）在 Linux 上确实能红，但那不是真实的回退形态 —— 真实的回退是"有人觉得这个 kwarg 冗余、顺手删掉"。

**残余风险可接受**：兜底的 `test_shipped_bundle_ready` 是按结果判定的，与成因无关，我用 X1-LF 变异（把随包产物改成 CRLF）实测它**会红**。所以即便 `newline="\n"` 被删掉，缺陷也要等到有人在 Windows 上重建并提交时才会出现，而那一刻 Linux CI 上 `test_shipped_bundle_ready` 就会拦住，**不会流到用户手里**。故列为提示级，不列整改。

### 6.3 观察项：conftest 去掉 `pytest.skip` 兜底

`tests/copilot/conftest.py` 原来是 `except Exception: pytest.skip("元数据库不可用")`，本轮改成断言直接报错。方向是对的 —— 上一轮正是这类宽泛兜底把结构回归伪装成 skip。代价是元数据库真的不可用时整套用例会 error 而不是 skip。对门禁用途来说报错优于跳过，我认可这个取舍，记录在此备查。

---

## 7. 关于开发记录 §7 的核对

我把 Q 开发记录 §7.4 报的每一个数字，与我在**完全不同的环境**（Linux + MariaDB 10.11 + 兼容垫片；他是 Windows + MySQL 8.0.45）独立测得的数字逐项比对：

| 项 | Q 自述 | 我实测 | |
|---|---|---|---|
| Copilot 连跑第 1/2/3 轮 | 111 / 111 / 111 | 111 / 111 / 111 | ✅ |
| X1 摘除 `_self_verify` | 2 failed | 2 failed | ✅ |
| X3 摘除端点闸 | 4 failed / 4 passed | 4 failed / 4 passed | ✅ |
| B01-LF 输出改 CRLF | 1 failed | 1 failed | ✅ |
| X2 结构错误分类恒 False | 2 failed | 2 failed | ✅ |
| X4 `Limits.validate` 恒空 | 3 failed / 13 passed | 3 failed / 13 passed | ✅ |
| X5 摘除 settings 门禁 | GET/PUT 分别摘除，各 1 failed | GET/PUT 一并摘除，1 failed（M-03 用例本就同时覆盖两者） | ✅ |
| 回归对照 | 新增失败 0，修复 4 项 | 新增失败 0，修复 4 项 | ✅ |
| 三轮后 M01/M03 残留 | 全部 0 | 全部 0 | ✅ |

**九项全中，没有一项对不上。**

§7.1 还主动把 §4/§6 里"B-01 已闭环""X1 变异有效""专项 103/103、全量零失败"标注为不可作为交付验收结论，并保留原文作历史记录；§7.4 如实写出"原始新库全量 417 failed，不是有效的全绿证据"，§7.5 明确写出未覆盖边界、不自行宣布 UAT 放行。

前两轮"自述数字在干净检出上重现不了"的问题，到这一轮实质性闭环了。我第二轮建议的那条硬规矩（提交前在干净检出跑一遍、把实际结果贴进开发记录），执行到位了。

---

## 8. 放行意见

**可以进 UAT。**

理由：

1. 两项整改（B-01、N-01）经独立复验确实修好，根因被复算证实，不是表面修复。
2. 第一轮、第二轮的全部通过项重跑无一回退；方案乙隔离 9/9、N-10 双向、RBAC、INV-03、B 组全灭期间的账号生命周期与 12 条核心端点全部仍然成立。
3. 回归零新增失败，修复 4 条。
4. 整改的运行期代码影响面只有 1 个构建期工具文件，services/api/workers/schema/models 零改动 —— 次生灾害在结构上就不成立。
5. 6 条变异（X1、X3A–D）新杀 + 3 条（X2、X4、X5）复杀，锁的有效性有实证，不是"跑绿了就算"。

### UAT 前请一并带上的遗留项

| 编号 | 级别 | 内容 | 建议处理时机 |
|---|---|---|---|
| N-03 | MINOR | 源文档改动不重建知识包无锁可红 | UAT 期间补锁即可，不必卡放行 |
| N-04 | 提示 | LF 锁在 POSIX 上不会红（兜底锁有效） | 可并入 N-03 一起处理 |
| —— | —— | health 第三条只读例外待 O 在设计 §10.4 订正 | O 的设计侧动作 |

### UAT 必须覆盖、本轮测不到的

Q 在开发记录 §7.5 自己划了边界，我认可并补充强调：本轮全部是单元/集成层面的验证，**没有接真实模型、没有做数据出域审批、没有内网实机容量压测、没有黄金集模型效果评测**。这四项必须由 UAT 覆盖，尤其是：

- 真实模型接入后的 `projection_mode` 与实际出站载荷的一致性抽检（M-01 的最终价值在这里体现）
- 元数据库连接池与磁盘在 Copilot 并发下的占用（这是 Copilot 与主产品真正共享的资源，我在设计评审阶段已经因为搞错过一次共享资源被 O 纠正）
- 知识包内容的实际检索质量（本轮只验证了产物完整性，没有验证答得对不对）

---

**-ClaudeA**
