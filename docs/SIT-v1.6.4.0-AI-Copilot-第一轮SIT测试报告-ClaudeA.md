# v1.6.4.0 AI Copilot 专家助手 · 第一轮独立 SIT 测试报告

| 项 | 内容 |
|---|---|
| 受测版本 | v1.6.4.0 / CP-1（受测提交 `241a927`） |
| 对照基线 | `4fb1f2e`（Rev.D 施工基线冻结提交，即受测提交的父提交） |
| 交付规模 | 72 文件 / 11,728 行新增 |
| 测试人 | 智能体A（独立评审/测试） |
| 报告日期 | 2026-09-14 |
| 送呈 | Mr.Linsang |

---

## 0. 结论

**2 项 BLOCK，3 项 MAJOR，不能进入 UAT。但主体架构是立住的——我签过字的那几条承重不变量实测全部成立。**

| 项 | 结论 |
|---|---|
| 方案乙隔离（B 组九表逐一破坏） | ✅ **9/9 通过** |
| A 组失败关闭 / N-10 双向锁 | ✅ **通过** |
| M-02 RBAC fail-open 封堵 | ✅ **通过** |
| INV-03 无执行面 / 出域面 | ✅ **通过** |
| 既有功能回归 | ✅ **零回归**（新增失败 3 条全部来自 B-01） |
| BLOCK | **2 项** |
| MAJOR | **3 项** |

另需说明：Q 开发记录称「`tests/copilot/` 93 项全过」，**在干净检出上不成立**——
3 项确定性失败（见 B-01）。其余自述数据与我实测一致。

---

## 1. 【B-01｜BLOCK】随包知识包 hash 与实际文件不符，CP-F02（P0）开箱即坏

### 1.1 事实

`backend/copilot_knowledge/kb-1.6.4.0-5b8426f429c6a89a/manifest.json` 登记的文件
大小与 sha256 与实际文件都不符：

| 文件 | manifest 登记 | 实际 | sha256 |
|---|---|---|---|
| `chunks.jsonl` | 15,540 B | **15,517 B** | ❌ 不符 |
| `index.json` | 144 B | **140 B** | ❌ 不符 |

后果：`knowledge_status = INVALID`（按 §6.2「hash/结构损坏为 INVALID」），
**STALE/INVALID 内容不得用于当前回答** → CP-F02「本地使用指南与规则知识问答」失效。

这条尤其要紧，因为 **CP-F02 是 P0，且默认发布状态就是 `COPILOT_ENABLED=false` 的本地帮助模式**——
也就是说，**产品开箱后的默认形态里，知识库是坏的**。

### 1.2 我定位到了根因，不是构建器的问题

我用交付的 `builder.build()` 重建了一份：

- 新建包 **目录名相同**（`kb-1.6.4.0-5b8426f429c6a89a`）；
- 新建的 `chunks.jsonl` / `index.json` 与随包内容**逐字节相同**；
- 但新建 manifest 的 hash **自洽**。

**结论：构建器是对的；是源文件在 manifest 生成之后被改动过，而 manifest 没有重建。**
（随包 manifest 的 `reviewed_at` 为 `2026-09-13T14:42:25Z`，其记录的体量比实际多 23/4 字节。）

这正是 Rev.D §16.3 M-03 那条发布必检要防的事：「每次版本发布重建知识包并产生 manifest/hash」。

### 1.3 影响面

`tests/copilot/test_knowledge_output.py` 的 3 项确定性失败全部源于此，
也是本轮受控回归里**唯一的 3 条新增失败**。

**整改**：重建知识包并提交；把「知识包 hash 自洽」做成发布前置校验（构建即校验，而不是等运行期降级）。

---

## 2. 【B-02｜BLOCK】B 组表运行期丢失时裸 500，模块状态不失效、不收敛

### 2.1 实测

模块状态 READY、九表完好时，13 个端点全部正常（含 `POST /sessions` 201）。
随后**在运行期**删除 `copilot_sessions`：

```
GET /api/v1/copilot/sessions → 500 "Internal Server Error"
异常后 copilot_runtime.module_schema_state = READY   ← 未失效
再次请求 → 仍 500                                     ← 不收敛
```

服务端日志里是裸 `pymysql.err.ProgrammingError: (1146, "Table '...copilot_sessions' doesn't exist")`
穿透到顶。

### 2.2 违反的条款

Rev.D §10.4 明确：「**出现 B 表读写结构异常立即使本进程失效并传播到 A 组**」，
且降级闭集要求其余端点「统一 503 `COPILOT_SCHEMA_UNAVAILABLE`，
schema 门禁先于任何 B 组对象/幂等查询」。

**门禁本身是实现了的，只是只读 `module_schema_state`**——我把该状态直接置为
`UNAVAILABLE`（不删表）后，13 个端点里 11 个正确返回 503（见 §4.3）。
缺的是**运行期检测**：请求路径上的 B 表异常没有被捕获、转换、回写状态。

无 runner 运行时永不收敛；有 runner 时要等其 60 秒结构复验才会收敛，
而 §10.4 要求的是「立即」。

### 2.3 正面

响应体只有 `Internal Server Error`，**未泄漏栈或库内部信息**，§12.7 的不外露要求满足。

**整改**：在 B 组数据访问层统一捕获结构类异常（1146/1054/1146 族），
转 `COPILOT_SCHEMA_UNAVAILABLE`、置 A 组状态为 UNAVAILABLE 并推进 epoch，本次请求即返回 503。

---

## 3. MAJOR

### 3.1 【M-01】预览的 `projection_mode` 只合成了两闸，端点闸要到出站才判

`preview_service.py:117-121`：

```python
deploy_allowed = allow_schema_identifiers_deploy()        # 闸一
grant_allowed  = effective_allow_identifiers(...)          # 闸二
identifiers_allowed = bool(deploy_allowed and grant_allowed)   # 只 AND 了两个
projection_mode = "SCHEMA_IDENTIFIERS" if identifiers_allowed else "ALIASED"
```

注释写的是「§4.4 三闸：部署 → 逐实例 → 端点能力」，但端点闸不在这里。

**先说不是什么**：这**不是数据泄漏**。我核了 `policy.egress_check`（`policy.py:242-256`），
第 255 行 `if identifiers and not ep["allows_schema_identifiers"]: raise EGRESS_DENIED`
**确实拦住了**，且 RESTRICTED 恒拒、INTERNAL_REDACTED 不得跨到非 INTERNAL 端点也都在。
**三闸在数据离开前全部生效。**

**问题在于预览与实际不一致**：预览会告诉用户「本轮将发送结构标识符」，
而实际出站可能被端点闸拒绝 → 轮次以 EGRESS_DENIED 收场。
§4.4 要求的是「**否则预览明确采用别名模式或禁用不合格备用，不在故障转移时偷偷缩减/放宽投影**」。
预览是本设计里"知情同意"的核心环节，显示的内容必须等于实际要发的内容。

**整改**：预览阶段一并评估主备两端的 `allows_schema_identifiers`，任一不满足即
`projection_mode=ALIASED` 并在 limitations 说明原因。

### 3.2 【M-02】资源参数超界被静默夹值，未按 §7.3 拒绝启用

`policy.py` 的 `Limits.get` 用 `max(lo, min(hi, v))` 夹值。实测 4/4：

| 参数 | 设为 | 实际返回 | 应当 |
|---|---|---|---|
| `COPILOT_RUNNER_CONCURRENCY` | 99 | 4 | 拒绝启用 |
| `COPILOT_TURN_DEADLINE_SECONDS` | 5 | 30 | 拒绝启用 |
| `COPILOT_OUTPUT_MAX_TOKENS` | 99999 | 4096 | 拒绝启用 |
| `COPILOT_CONTEXT_MAX_BYTES` | 1 | 8192 | 拒绝启用 |

§7.3 原文：「**参数超界拒绝启用，不能静默夹值后继续**」。

这条不是吹毛求疵：运维把 `COPILOT_PROVIDER_TOTAL_SECONDS` 写成 600 想加长超时，
系统静默按 60 跑，运维会以为配置生效了——**配置与实际行为不一致且无提示**，
排障时会被误导。

**整改**：超界抛 `PROVIDER_CONFIG_INVALID` 级错误并拒绝启用助手，启动时即报，不到运行期才夹。

### 3.3 【M-03】`/copilot-admin/settings` 未走 schema 门禁

把模块置 UNAVAILABLE 后逐一探测 13 个端点，11 个按契约返回 503，两个例外：

| 端点 | 实际 | §10.4 契约 | 我的判断 |
|---|---|---|---|
| `GET /copilot-admin/health` | 200 | 503 | **实现是对的，设计文本需订正** |
| `GET /copilot-admin/settings` | 200 | 503 | **应补门禁** |

`health` 这一条我认为 Q 做对了：§10.4 自己的降级表另一行要求健康页
「明确区分'未启用（可接受）'与'schema 验收失败（需处理）'」，§16.4 也要求健康 GET 只读现有状态——
**health 若也 503，管理员就看不到失败原因了**，与 `capabilities` 必须可读同理。
建议**在设计侧把 health 列为第三条只读例外**，而不是改实现。

`settings` 没有同类理由（诊断由 health 承担），建议补门禁。

---

## 4. 通过项（逐条实测，这是本轮的大头）

### 4.1 方案乙隔离：B 组九表逐一破坏 9/9 通过

我按第三轮承诺**逐张表**破坏，不挑代表：

```
copilot_providers / scene_routes / instance_grants / sessions / previews /
turns / daily_budgets / provider_attempts / audit_events
  → 每一张：ensure_db=OK，backend.main:app 构建成功(41 routes)，原审核引擎 OK
```

**我推荐、Mr.Linsang 裁定的方案乙，在实现上是立住的。**

### 4.2 A 组失败关闭 + N-10 双向锁 通过

```
方向一 既有 CREATE TABLE 迁移(metadata_audit_jobs)缺表 → ensure_db=OK，表被自愈重建 ✅
方向二 A组已登记迁移(copilot_subjects)缺表 → MigrationError 失败关闭，未静默重建 ✅
copilot_runtime 缺表 → MigrationError ✅
```

N-10 要的双向语义完整成立。（过程中我一度误判，原因是 `ensure_db()` 进程内幂等短路，
改用独立进程复测后结论如上。）

### 4.3 schema 门禁覆盖 11/13，M-02 RBAC 封堵通过

模块 UNAVAILABLE 时：`/copilot/sessions`、`/connections`、`POST /sessions`、
`/sessions/{id}`、`/turns/{id}`、`/turns/{id}/result`、`/copilot-admin/providers`、
`/copilot-admin/grants`、`/copilot-audit/events` 全部
`503 COPILOT_SCHEMA_UNAVAILABLE`；`capabilities`/`help` 保持 200 可读。

RBAC 侧：三个前缀 `/api/v1/copilot`、`/api/v1/copilot-admin`、`/api/v1/copilot-audit`
均已登记 `_PATH_TO_MENU`。我特地探了一个**未登记的未来端点**
`/api/v1/copilot/__unregistered_future_ep__`——被前缀覆盖住，auditor 判 False。
**我一轮提的 fail-open 洞确认已堵。**

（我起初把 `auditor` 在 `/copilot-audit/events` 上的 True 记为异常，
复核 §9.1「审计元数据：admin，或 auditor 同时有 sys-auditlog」后确认是设计本意，
**是我的误判，撤回。**）

### 4.4 INV-03 无执行面 / 出域面 通过

扫全部 Copilot 模块（`services/copilot/*`、`api/copilot*`、`workers/copilot*`）：

- `requests` / `urllib` / `socket` / `aiohttp` / `subprocess` / `os.system` / `eval(` / `exec(` —— **零命中**；
- 唯一网络出口是 `providers.py` 的 `httpx.AsyncClient`，且 `trust_env=False`、`follow_redirects=False`；
- 动作卡闭集实测为 `OPEN_SOURCE / NAVIGATE / COPY_SUGGESTION / OPEN_AUDIT_EDITOR`，
  `RUN_SQL / APPLY_PATCH / EXECUTE_PLAYBOOK / ALTER_RULE / KILL_SESSION` **全部不存在**。

### 4.5 B 组故障期账户管理可用（T3）通过

B 组 `copilot_sessions` 缺失时，`create_user` / `delete_user` 全链路成功。
这是方案乙里我和 O 争论最久的一条（subject 耦合），实现上成立。

### 4.6 既有功能零回归

基线 `4fb1f2e` 与受测 `241a927` 各起独立 worktree、各用独立元数据库跑全量：

```
base: 484 条 FAILED/ERROR    head: 487 条
新增失败 = 3 条，全部是 tests/copilot/test_knowledge_output.py（即 B-01）
消除的失败 = 0 条
```

**除 B-01 外，既有功能一条没坏。**

---

## 5. 整改清单

| 编号 | 级别 | 事项 | 整改方向 |
|---|---|---|---|
| B-01 | BLOCK | 知识包 hash 不符，CP-F02(P0) 开箱即坏 | 重建知识包并提交；把 hash 自洽做成**构建期**校验 |
| B-02 | BLOCK | B 表运行期丢失 → 裸 500、状态不失效、不收敛 | B 组数据访问层统一捕获结构异常 → 转 503 + 置 A 组 UNAVAILABLE + 推进 epoch |
| M-01 | MAJOR | 预览 projection_mode 未含端点闸 | 预览阶段一并评估主备端点能力；不满足即 ALIASED 并说明 |
| M-02 | MAJOR | 资源参数静默夹值 | 超界拒绝启用并在启动时报错 |
| M-03 | MAJOR | `/copilot-admin/settings` 缺门禁 | 补门禁；**同时建议设计侧把 health 列为第三条只读例外** |

---

## 6. 放行意见

**不能进 UAT，B-01 / B-02 两项必须先改。**

- **B-01** 让产品的默认形态（本地帮助模式）带着一个坏掉的知识库出厂，
  而 CP-F02 是 P0；O 让 UAT 去点这个功能，第一下就会撞上。
- **B-02** 是方案乙的**检测半边**。隔离半边我实测是成立的（九表逐一破坏都不影响原系统），
  但"坏了以后用户看到什么"这半边没做完——裸 500 而不是带 reason_code 的 503，
  而且状态不收敛，运维在健康页上看到的还是 READY。

三项 MAJOR 不阻塞 UAT 启动，但准出前必须闭环；其中 M-03 需要 O 在设计侧订正一行文本
（把 health 列为只读例外），不是 Q 的实现问题。

**要说明的是：这一版的主体架构质量是高的。** 我签过字的承重不变量——
方案乙隔离、A 组失败关闭、N-10 双向、RBAC 封堵、无执行面、三闸在出站前全部生效——
逐条实测都立得住，既有功能零回归。两条 BLOCK 都是"边界处理没收口"，
不是架构问题，改动范围可控。

Q 改完我做第二轮 SIT，届时会对 B-01/B-02 的修复做变异复验
（注入缺陷确认用例变红），并把 §4 已通过项全部重跑防回退。

---

*报告人：智能体A（-ClaudeA）　送呈：Mr.Linsang*
