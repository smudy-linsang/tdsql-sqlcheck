# SIT-v1.6.3.4 第一轮系统集成测试报告

| 项 | 内容 |
|---|---|
| 被测版本 | v1.6.3.4，`main` / `6a339df`（第一至第五批施工） |
| 对照基线 | `main` / `3887327`（Q 施工前，独立 git worktree） |
| 设计依据 | `docs/DETAIL-v1.6.3.4-...md` Rev.C（`2d997a3`） |
| 测试方 | 智能体 A |
| 测试日期 | 2026-09-07 |
| 沙箱 | MariaDB 10.11.14 @127.0.0.1:13306；应用 uvicorn @127.0.0.1:18834；元数据库 `sit_e2e`/`sit_head`/`sit_base` 三库隔离 |
| 测试方式 | 受控前后对照（同一套件、同一 DB 状态、独立库）＋ 真实 HTTP 端到端 ＋ 直接驱动服务层状态机 ＋ 对抗性输入 |
| **测试结论** | **不通过（有条件）。2 项 BLOCK、2 项 MINOR。四个需求的功能实现本身质量很高，全部核心契约实测通过；阻断项集中在"交付完整性"而非"功能正确性"。** |

---

## 1. 结论摘要

| 级别 | 编号 | 问题 | 影响 |
|---|---|---|---|
| **BLOCK** | **B-01** | **设计 §8 要求的 28 个新增用例编号（DML-11—19、PAR-13—21、REP-12—15、GW-C1—C4、GW-T1—T2）在 `tests/` 中一个都不存在**；全仓新增 0 个测试函数；2057 行新模块（`report_context.py` 643、`tdsql_table_shape.py` 758、`gateway_process.py` 309、`gateway_upload_lock.py` 229、`log_input.py` 118）**无任何测试引用** | 5790 行实现只配了 136 行测试改动，且全部是让旧用例继续跑通的适配。功能今天是对的（我实测确认），但**没有任何回归锁**——下一次改动无人守门。且 Q 退役了两条设计门禁，理由写的是"由 PAR-01~21 用例守护"，而 **PAR-13~21 并不存在** |
| **BLOCK** | **B-02** | **网关 429/413 响应的 `X-Request-ID` 响应头与响应体里的 `request_id` 是两个不同的值**，且访问日志记录的是**响应头**那个。用户看到的是**响应体**那个 | 请求编号的唯一用途就是"用户报障→运维查日志"。两个值对不上，用户截图里的编号在日志里永远查不到。实测 2/2 复现 |
| MINOR | M-01 | 独立目录枚举**失败**（无权/不支持/连接错误）时，除 `SP_DIRECTORY_FAILED` 外还会附带 `SP_DIRECTORY_TRUNCATED`，其文案断言"触发 50000 行护栏或预算截断"——而该请求一行都没读到 | 把 DBA 引向错误的排查方向（去查行数护栏，实际是权限问题） |
| MINOR | M-02 | `scan_service.py` 在 `connection_id` 解析不到名称时，回退把 `host:port` 写入 `conn_name` 字段 | 设计 §3.2 明令"不把连接 ID、端口、数据库名冒充连接名称"。**我未能构造出该分支的用户可见路径**，故列为观察项 |

**一句话**：Q 的功能实现质量很高——我用对抗性输入、三条解析路径、状态机边界去打，**核心契约一条都没打穿**；两个 BLOCK 一个是"活干完了但没留锁"，一个是端到端才暴露的编号串台。

---

## 2. 回归对照：无新增失败

这是本轮第一件要确认的事——Q 的 5790 行改动有没有碰坏既有功能。

方法：同一套用例、同一沙箱、**两个独立元数据库**，分别在施工前后各跑一次全量套件，把 `FAILED/ERROR/SKIPPED` 清单排序后逐行 diff。

| 运行 | 代码 | 元数据库 | 结果 |
|---|---|---|---|
| 基线 | `3887327` | `sit_base` | 402 failed, 1404 passed, 84 skipped, 82 errors |
| 被测 | `6a339df` | `sit_head` | 402 failed, **1402** passed, **86** skipped, 82 errors |

**清单逐行 diff 只有 2 行差异，且全部是新增的 SKIPPED**，失败/错误集合**完全一致**：

```text
> SKIPPED tests/test_design_appendix_matches_repo.py:70: 附录 A.1（table_type_stats_service.py）…门禁退役
> SKIPPED tests/test_design_appendix_matches_repo.py:70: 附录 A.4（tests/test_table_type_stats.py）…门禁退役
```

**结论：Q 的改动没有引入任何新的测试失败。**

必须说明：那 402 failed / 82 errors 是**本沙箱的既有产物**，不是 Q 的问题——它们在基线上一模一样地存在（成因是全量套件的跨用例状态污染，单独跑 `test_v3_rbac_instances.py` 14 项全过）。我没有把它们记到 Q 头上，也不建议在本轮追这个历史问题。

那 2 条退役属于 B-01 的一部分，见 §4.1。

---

## 3. 核验通过的部分（逐需求实测）

### 3.1 REQ-03（R043）——本轮质量最高的一块，全部通过

**(a) 三条解析出口的事实链**。用真实 `SQLParser.parse()`，以 `unittest.mock.patch` 分别注入 `ParseError` 和 `exp.Command`（不改仓库文件）：

| 用例 | 正常 AST | 注入 ParseError | 注入 Command |
|---|---|---|---|
| `ALTER … ON UPDATE CURRENT_TIMESTAMP` | NOT_DML/NOT_APPLICABLE/False | 同左 | 同左 |
| `ALTER … ADD COLUMN a … ON UPDATE …, ADD COLUMN b VARCHAR(20) CHARACTER SET utf8mb4` | NOT_DML/NOT_APPLICABLE/False | 同左 | 同左 |
| 附件同源 `CREATE TABLE … ON UPDATE … CHARACTER SET …` | NOT_DML/NOT_APPLICABLE/False | 同左 | 同左 |
| `UPDATE a JOIN b ON a.id=b.id SET a.v=1` | UPDATE/RESOLVED/**True** | 同左 | 同左 |
| `DELETE a FROM a JOIN b ON a.id=b.id` | DELETE/RESOLVED/**True** | 同左 | 同左 |
| `LOCK TABLES t WRITE`（复合 token 陷阱） | NOT_DML/NOT_APPLICABLE/False | 同左 | 同左 |
| `SELECT 'UPDATE a JOIN b SET x=1' AS c`（字符串伪造） | NOT_DML/NOT_APPLICABLE/False | 同左 | 同左 |
| `` SELECT `UPDATE` FROM t ``（反引号伪造） | NOT_DML/NOT_APPLICABLE/False | 同左 | 同左 |

**24 格全对，三条出口结论完全一致**。O 在 Rev.C DML-19 里点名的两个词法陷阱（`LOCK TABLES` 被合成一个 token、引号/反引号伪造语句头）都被正确挡住了。

**(b) 闭集**。`R043_NON_TARGET_HEADS` 是 `frozenset`，**36 项与设计逐项一致**（脚本比对：多 0、少 0），且不含 `UPDATE`/`DELETE`。

**(c) DML-18 净效果锁（我按设计要求实跑）**。44 条语句 × 2 种架构 = 88 个组合，在基线与被测版本各跑一次全量审核后逐项对比：

* **没有任何一条语句新增 E999**；E999 总数 base=8 → head=8，**持平**
* **非 R043 规则的命中集合与级别逐条相同**，零漂移
* R043 只在三处发生变化，且全部是**应当消失的误报**：

| 语句 | 基线 | 被测 |
|---|---|---|
| `ALTER … ADD COLUMN a … ON UPDATE …, ADD COLUMN b …`（我在评审阶段发现的**生产在用误报**） | R043 ❌ | 无 ✅ |
| 附件同源 `CREATE TABLE …` | R043 ❌ | 无 ✅ |
| `SELECT '…UPDATE a JOIN b…' AS x`（字符串字面量误判，基线也中招） | R043 ❌ | 无 ✅ |

* 真联表 `UPDATE`/`DELETE` 在分布式下**仍然命中 R043**，集中式下**仍然不命中**（仅分布式作用域未被破坏）

**(d) 兼容属性**。`is_multi_table_update` 已是只读 property：无 setter（写入抛 `AttributeError`）、已从 dataclass 字段移除（`ParsedSQL(is_multi_table_update=True)` 抛 `TypeError`）、默认 False。P3-03 要求全部满足。

**(e) 真实 API 复验**。经 `POST /api/v1/audit/file` 提交附件同源建表语句，返回违规为 `R005/R029/R036/R037/R077`，**无 R043**；导出的 HTML 报告中 `R043` 出现 0 次。

### 3.2 REQ-02（二级分区主表）——识别器与状态机通过

**(a) 识别器对抗性输入**。`classify_logical_ddl` 10 组用例全部正确，尤其是设计明令必须挡住的四类"grep 会中招"的输入：

| 对抗输入 | 结果 |
|---|---|
| 列名就叫 `shardkey` | NOT_SECONDARY ✅ 未误判为分片 |
| `COMMENT 'shardkey=id PARTITION BY RANGE (id)'` | NOT_SECONDARY ✅ 注释内容未污染 |
| 反引号列名 `` `PARTITION BY` `` | 分布=SHARDKEY_HASH、分区=NONE ✅ 标识符未被当关键字 |
| `DEFAULT 'noshardkey_allset'` 字符串 | SECONDARY ✅ 未被误判成广播表 |

正例侧：旧语法 `shardkey=` + `PARTITION BY` → SECONDARY/LEGACY；新语法 `TDSQL_DISTRIBUTED BY HASH` + `TDSQL_PARTITION BY` → SECONDARY/MODERN；广播优先级正确（`noshardkey_allset` + 分区 → 不计主表）。

**(b) 四层候选覆盖**——这是 P2-01 的核心，实测三种"旧口径会漏"的场景全部捞回：

| 场景 | 结果 |
|---|---|
| PAR-13 现代主表只在 `only_base`（基线有、Proxy 全无） | main=1, outside_shard=1, COMPLETE ✅ |
| **PAR-14 现代主表落在旧"单表"结果里**（既不在分片集、也不在 only_base） | main=1, outside_shard=1, COMPLETE ✅ |
| PAR-15 现代主表只在独立目录 L 中 | main=1, outside_shard=1, COMPLETE ✅ |

**PAR-14 正是我在第二轮评审里给错、O 纠正过来的那个分支**——实现按 O 的 `C = L ∪ P ∪ B` 做，确实捞回来了。三种场景下 `total/shard/single` 等原有口径均未被改动。

**(c) 计数公式与状态机**：`candidates = checked + unknown + unchecked` 与 `outside_shard <= main <= checked <= candidates` 在全部用例中成立。目录失败路径：

| 注入 | inventory_state | check_state |
|---|---|---|
| 目录连接抛异常 | FAILED | PARTIAL（**未冒充 COMPLETE** ✅） |
| 目录 `execute` 抛 1064 | FAILED | PARTIAL ✅ |
| L 缺少 P/B 已知对象 | PARTIAL + `INVENTORY_MISMATCH` | PARTIAL ✅ |

**(d) 迁移**：`table_type_stat` 与 `table_type_stat_item` **各 8 列**（6 个 INT NULL + 2 个 VARCHAR(24) NOT NULL DEFAULT 'LEGACY'），共 16 条，与设计一致；v14 三份迁移账本齐全（140/141/142）。

### 3.3 REQ-01（报告实例标识）——Mr.Linsang 的裁决已被正确实现

**(a) H01 离线文件审核**：导出 HTML 页眉为 `实例连接名称：未关联实例（离线文件审核）`；报告内**无门禁结论**，`audit_history.gate_passed` 为 NULL。符合裁决"未指定实例连接的显示未关联"。

**(b) H02 在线元数据审核**：绑定连接 `SIT134-核验实例-原名` 执行后，HTML 页眉显示**真实连接名称**；`gate_result=None`、`audit_history.gate_passed=NULL`（REP-13 通过，门禁未被顺带打开）；`report_context_json` 已落库，`origin=bound`。

**(c) REP-12 冻结验证（我在评审里要求的那条锁）**：审核完成后把连接改名为 `SIT134-已改名-NEW`，**重新导出同一个 `report_id=2`**：

```text
旧名「SIT134-核验实例-原名」出现次数：1
新名「SIT134-已改名-NEW」  出现次数：0
```

**冻结生效，导出时没有反查现名。**这正是设计要防的"同一份历史报告在不同时间显示不同实例名"。

**(d) HTML 转义（安全）**：把连接名改成 `<script>alert(1)</script>"onload=x&<img src=y onerror=z>` 后导出报告——未转义的危险片段出现 **0 次**，实际输出为 `&lt;script&gt;alert(1)&lt;/script&gt;&quot;onload=x&amp;&lt;img src=y onerror=z&gt;`；全文**不存在任何真实可执行的 `on*=` 属性**。

**(e) 14 个出口全部接入**：H01—H14 对应的 12 个文件（含 4 个 CLI 与磁盘测试 shell 脚本）均已引用 `report_context`/实例名称逻辑，无遗漏。

**(f) H03 具名缺陷已修**：`scan_service._do_scan` 原来写 `conn_name = f"{host}:{port}"`，现已改为按 `connection_id` 从 registry 取冻结的真实名称。

### 3.4 REQ-04（网关）——原事故阻断点已消除

**(a) 路由隔离——这是本需求的根**：

| 目标 | 60 MiB 文件 | 结果 |
|---|---|---|
| `POST /api/v1/gateway-log/upload`（网关专属） | 上传 | **422 GATEWAY_INVALID_LOG**（因我构造的是合成内容，非真实网关日志）——**没有被 413 拦住** ✅ |
| `POST /api/v1/audit/upload`（普通路由） | 上传 | **413「请求体过大，上限 50MB」** ✅ 全站限额未被放开 |

**原来 71 MiB 日志必然 413 的阻断点确已消除，且没有牺牲其他接口的保护。**附带性能观察：110 万行 / 60 MiB 的流式解析在 **3.4 秒**内完成并返回结构化错误。

**(b) 启动期超时链校验（GW-T1）**——8 组非法配置逐一验证：

| 注入 | 结果 |
|---|---|
| 并发=2 / 并发=0 | 拒绝 ✅（"固定常量 1（非调优项）"） |
| `processing=545`（< analysis+10） | 拒绝 ✅ |
| `browser=800`（< receive+processing+10） | 拒绝 ✅ |
| proxy 声明值 605（< processing+10）/ 1200（> browser） | 均拒绝 ✅ |
| `receive=-1` | 拒绝 ✅ |
| `request_max < upload_max` | 拒绝 ✅ |
| direct 模式下 proxy 声明值离谱 | 通过 ✅（设计要求 direct 只豁免 proxy 约束，行为正确） |

默认配置通过，应用启动日志自证：`网关上传配置校验通过 (mode=direct, upload_max=200 MiB, request_max=201 MiB, concurrent=1 固定不可调)`。

**(c) 锁归属（GW-C2）——我在评审里给错、O 纠正的那一条**：

```text
A 取锁 True → B 取锁 False → B 调用 release() → C 仍取不到锁 ✅（A 的锁未被误释放）
A 正常 release() → D 取锁 True ✅
```

**未取得槽的请求不会释放他人的锁**，正是 O 坚持的写法。

**(d) 429 契约（GW-C1）**：占锁状态下上传，返回 429，头含 `retry-after: 598`、`x-request-id`，体为：

```json
{"detail": {"code": "GATEWAY_BUSY", "stage": "admission", "retryable": true,
 "message": "…同一时刻仅允许一个任务。您的文件未被处理（未进入分析、未排队），请约 10 分钟后重新上传；此间隔仅供参考，不保证届时空闲。"}}
```

字段、文案、区间（5—600 秒）全部符合定稿。**但 `request_id` 串台，见 B-02。**

**(e) capabilities 与前端**：`GET /api/v1/gateway-log/capabilities` 返回 16 个字段（含 `browser_wait_seconds:990`、`concurrency_fixed:true`、`config_version:1.6.3.4`）；`app.js` 中 `capabilities` 引用 3 处、`handledHttpError` 5 处，且浏览器等待取 `browser_wait_seconds||990`，未写死魔数。

### 3.5 版本收尾

`VERSION`=1.6.3.4、`config.APP_VERSION`="1.6.3.4"、`frontend/index.html` 5 处版本号、静态资源 `app.js?v=1.6.3.4` 全部对齐；启动日志 `TDSQL SQL审核平台已就绪 (V1.6.3.4)`。

---

## 4. 缺陷详情与整改方案

### B-01（阻断）设计 §8 要求的 28 个新增用例一个都没实现，2057 行新模块零测试

**事实**

| 项 | 实测 |
|---|---|
| 实现规模 | 42 文件、+5790 行 |
| 测试改动 | **3 个文件、+136 行**，且**新增测试函数 0 个**（`git diff` 中无任何 `+def test`） |
| 新增测试文件 | **0 个** |
| 设计 §8 新增用例编号落地 | DML-11—19（9）、PAR-13—21（9）、REP-12—15（4）、GW-C1—C4（4）、GW-T1—T2（2）＝**28 个，`tests/` 中全部 0 命中** |
| 新模块测试引用 | `report_context.py`(643 行)、`tdsql_table_shape.py`(758)、`gateway_process.py`(309)、`gateway_upload_lock.py`(229)、`log_input.py`(118) —— **全部 0 个测试文件引用** |

`tests/test_table_type_stats.py` 那 103 行新增，全部是让**旧用例**继续跑通的适配（fake 目录连接钩子、5 值解包、141 建表），没有一条针对新功能的断言。

**连带问题**：Q 在 `tests/test_design_appendix_matches_repo.py` 中把附录 A.1/A.4 两条门禁退役，skip 理由原文写的是

> 一致性由 DETAIL-v1.6.3.4 §4、**PAR-01~21 用例**与 118 项 G14 回归守护

但 **PAR-13~21 并不存在**。退役一条既有门禁、把责任转嫁给一组不存在的用例，这是本条最需要纠正的地方。退役本身我认为合理（历史设计文档是快照、不回溯改写，这条项目纪律成立），**但必须先有替代守卫，再退役**。

**影响**

功能今天是对的——这一点我用了大量对抗性用例反复确认。但**没有回归锁**意味着：R043 的 36 项闭集、三出口一致性、四层候选覆盖、锁归属、超时链不等式、名称冻结……这些花了两轮评审才立起来的契约，**下一次任何人改动都不会有测试拦住**。v1.6.3.2 的教训就是这么来的。

**整改方案（照图施工）**

按设计 §8 补齐 28 个用例，建议按四个新建文件组织，避免污染既有文件：

| 新建文件 | 覆盖 | 关键断言（不得省略） |
|---|---|---|
| `tests/test_v1634_r043_dml_target.py` | DML-11—19 | 每条用例**必须在正常 AST / 注入 ParseError / 注入 Command 三条路径分别断言**（用 `unittest.mock.patch` 注入，不改被测代码）；DML-18 按设计做**改前/改后逐句对账**：断言"每条原无 E999 的不得新增 E999"**且**"E999 总条数不增加"**且**"非 R043 规则命中与级别逐条相等"；DML-16 断言 property 无 setter、构造参数已移除、连续解析无状态串扰；DML-19 覆盖 `LOCK TABLES` 复合 token、`'SELECT'` 字符串、`` `UPDATE` `` 反引号、批文件首条 SELECT 后接真 multi DML |
| `tests/test_v1634_secondary_partition.py` | PAR-13—20 | 复用 `FakePool` + `_patch_tmp_pool` + `pool.directory_rows` 注入；PAR-14 必须构造"现代主表落在旧 single 结果"这一分支并断言 `main=1/outside_shard=1`；PAR-16 必须分别注入**连接异常**与 **execute 异常**，断言 `inventory_state=FAILED` 且 `check_state != COMPLETE`；PAR-20 用可控时钟断言 180 秒共享 deadline **不重置**、不额外加 300 秒 |
| `tests/test_v1634_report_context.py` | REP-12—15 | REP-12 必须是**改名后重新导出同一 report_id**、断言仍为旧名且新名出现 0 次；REP-13 断言 `gate_result is None` **且** `audit_history.gate_passed IS NULL`（按实际列名，不要写 `gate_result` 列）；REP-15 断言归一 nonce 后 CSP 策略项不放宽、每响应 nonce 更新且与脚本匹配；另补一条**恶意名称转义**用例（断言未转义片段计数为 0 且无可执行 `on*=` 属性） |
| `tests/test_v1634_gateway.py` | GW-C1—C4、GW-T1—T2 | GW-C1 断言 429 + `Retry-After` + `X-Request-ID` + 体内 `code/stage/retryable`，**并断言头与体的请求编号一致**（即 B-02 的回归锁）；GW-C2 断言未取槽者 `release()` 后他人锁仍在；GW-T1 逐条注入 §6.3.1 的每个不等式违例，断言启动校验拒绝并指名配置项 |

同时把 `test_design_appendix_matches_repo.py` 的 skip 理由改写为**指向真实存在的用例文件**，例如"一致性由 `tests/test_v1634_secondary_partition.py`（PAR-13—20）守护"——**在这些用例合入之前，不要退役那两条门禁**。

### B-02（阻断）429/413 的响应头与响应体携带两个不同的请求编号

**现象**（实测 2/2 复现）

| 次数 | 响应头 `x-request-id` | 响应体 `detail.request_id` | 访问日志记录 |
|---|---|---|---|
| 1 | `ae8eae13fa5740fc` | `9614f83d97ac4b1d` | `[ae8eae13fa5740fc]` |
| 2 | `c2dcefdf163d490d` | `5ff41c71a23b4631` | `[c2dcefdf163d490d]` |

**用户看到的是响应体那个编号**（§6.4 第 7 条要求前端"显示请求编号"），**运维查到的是响应头那个**。两者永不相等。

**根因（已定位到行）**

`backend/main.py:158-160` 的注册顺序使 `RequestContextMiddleware` 位于 `GatewayUploadPolicyMiddleware` **外层**（Starlette 中后注册者在外）。于是：

1. `middleware.py:266` —— 外层 `RequestContextMiddleware` 生成 `request_id`，写入 `request.state.request_id`，并在 `:278` 设置响应头、在 `:294` 写访问日志；
2. `middleware.py:126-130` —— 内层 `GatewayUploadPolicyMiddleware._get_request_id(scope)` **只扫描入站请求头** `x-request-id`；外层并没有把自己生成的 ID 回写进请求头，于是这里扫不到，`:130` 又 `uuid.uuid4().hex[:16]` **另生成一个**，这个进了 JSON 体；
3. 返回途中外层把响应头覆盖成自己那个。

**整改方案（照图施工，改动 4 行）**

把 `backend/middleware.py:125-130` 改为**优先复用外层已建立的请求上下文 ID**：

```python
@staticmethod
def _get_request_id(scope) -> str:
    # 外层 RequestContextMiddleware 已建立请求上下文时，必须复用同一个 ID，
    # 否则响应头/访问日志与响应体会出现两个编号，用户报障无法与日志对账。
    rid = (scope.get("state") or {}).get("request_id")
    if rid:
        return str(rid)[:64]
    for k, v in scope.get("headers") or []:
        if k == b"x-request-id" and v:
            return v.decode("latin-1")[:64]
    return uuid.uuid4().hex[:16]
```

**机制我已实证**：Starlette 1.6.0 的 `request.state` 写入的就是 `scope["state"]`（`HTTPConnection.state` 用 `scope.setdefault("state", {})`），且外层 `BaseHTTPMiddleware` 在 `call_next` 之前的写入，内层纯 ASGI 中间件**能从 scope 读到**——我用一个最小 Starlette 应用复刻了同样的内外层结构验证通过（外层生成与内层读到的值相等）。

**不要**改用"把 `RequestContextMiddleware` 移到内层"的办法：设计 §6.4 第 1 条明确要求请求上下文在**最外侧**，移动它会让 413/429 失去 `X-Request-ID`，属于按下葫芦起了瓢。

**回归锁**：在 GW-C1/GW-C2 用例中对 429 与 413 两条路径分别断言
`resp.headers["X-Request-ID"] == resp.json()["detail"]["request_id"]`。

### M-01（次要）目录枚举失败时附带一条断言了错误原因的截断告警

`table_type_stats_service.py` 中 `_enumerate_directory_l` 的异常分支 `return set(), SP_STATE_FAILED, rows_consumed, True` 把 `truncated` 一并置真，于是除 `SP_DIRECTORY_FAILED` 外还会触发 `SP_DIRECTORY_TRUNCATED`，其文案为"独立目录枚举触发 50000 行护栏或预算截断"。但该请求**一行都没读到**，既没触发行护栏也没耗尽预算。

实测：注入连接异常与注入 `execute` 1064 两种情况下，`warnings` 均为 `['SP_DIRECTORY_FAILED', 'SP_DIRECTORY_TRUNCATED']`。

**整改**：异常分支改为 `return set(), SP_STATE_FAILED, rows_consumed, False`——失败已由 `SP_DIRECTORY_FAILED` 如实报告，不需要再叠加一条断言了错误成因的告警。若确需表达"候选目录不完整"，应复用 `inventory_state=FAILED` 这一事实，而不是复用"截断"这个**具体且此处为假**的原因。补 PAR-16 断言：失败路径下 `SP_DIRECTORY_TRUNCATED` **不出现**。

### M-02（观察项，非缺陷结论）scan_service 的名称降级分支会写入 host:port

`backend/services/scan_service.py` 在 `capture_report_context` 未取到名称时执行
`conn_name = f"{pool.config.host}:{pool.config.port}"`。设计 §3.2 明令"不把连接 ID、端口、数据库名冒充连接名称"，且规定即席无名应显示"未命名即席连接"（`report_context.py:235` 已正确实现这一分支）。

**我未能构造出该分支的用户可见路径**——正常绑定与即席连接都能取到名称，只有 `connection_id` 完全无法解析时才会走到，而那种情况扫描本身通常已失败。因此**列为观察项，不作为缺陷结论**。

**建议**：请 Q 确认该分支是否可达。若可达，把降级值改为设计规定的"未命名即席连接"，并让 `host:port` 只作为**独立的"历史端点"辅助字段**呈现，不占用名称位；若不可达，加一行注释说明并保留。

## 5. 整改清单与二轮准入

| 编号 | 级别 | 责任 | 二轮准入判据 |
|---|---|---|---|
| B-01 | BLOCK | Q | 四个新建用例文件合入，28 个编号全部可定位、可运行、可失败（我会做**变异测试**：故意改坏被测逻辑，验证对应用例真的会红）；`test_design_appendix_matches_repo.py` 的 skip 理由改为指向真实存在的用例 |
| B-02 | BLOCK | Q | `middleware.py::_get_request_id` 优先取 `scope["state"]["request_id"]`；429/413 两条路径的头体编号一致性写成断言 |
| M-01 | MINOR | Q | 目录失败分支 `truncated=False`；PAR-16 断言失败路径不出现 `SP_DIRECTORY_TRUNCATED` |
| M-02 | 观察 | Q 确认 | 说明该分支是否可达；可达则按设计改为"未命名即席连接" |

**功能实现本身不需要返工**——四个需求的核心契约我都实测通过了。B-01 是补测试，B-02 是 4 行代码，M-01 是 1 个布尔值。

## 6. 向 Mr.Linsang 汇报的两句话

**第一，Q 这轮的活干得扎实。**我拿对抗性输入去打——把列名起名叫 `shardkey`、把攻击脚本塞进连接名、在注释里写假的分区语法、伪造语句头、在三条不同的解析路径上反复试——**核心逻辑一条都没被打穿**。您最关心的两件事都验证到位了：那个 R043 误报确实消失了（而且我在评审阶段发现的、藏得更深的 ALTER 语句误报也一并消失），71 MiB 大日志的上传阻断也确实打通了（60 MiB 实测能进到解析环节，不再被挡在门外），同时其他接口的 50 MiB 保护一点没松。您裁决的两件事也都落实了：在线元数据审核报告显示真实实例名，而且改名之后重新导出还是显示当时那个名字；网关分析同一时刻只能一个人跑，第二个人拿到的是"您的文件未被处理，请稍后重传"。

**第二，两个阻断项都不是功能做错了。**一个是**活干完了没留锁**——设计里要求补的 28 个测试用例，一个都没写，2000 多行新代码没有任何测试覆盖。今天功能是对的，但下次谁改动都没人拦。更要紧的是，Q 还顺手退役了两条既有门禁，理由写的是"由 PAR 用例守护"，而那些用例根本不存在——**先有替代守卫再退役，顺序不能反**。另一个是端到端才暴露的：网关忙时返回的那个"请求编号"，**用户看到的和运维日志里记的是两个不同的号**，用户拿着截图来报障，日志里永远查不到。根因我已经定位到具体行，改 4 行就行。

## 7. 测试边界声明

1. 本轮**未修改任何仓库文件**。所有注入均为进程内 `unittest.mock.patch` 或独立脚本；沙箱兼容补丁以 pytest 插件形式加载，不落盘到仓库。
2. 402 failed / 82 errors 是**本沙箱既有产物**，已用施工前后独立双库对照证明与 Q 的改动无关（失败集合逐行一致），**未记入 Q 的缺陷**。
3. **未能覆盖的部分，如实列明**：
   - **内网真实 TDSQL 未接触**——REQ-02 的 `SHOW FULL TABLES` 实际返回口径、`SHOW CREATE TABLE` 真实往返耗时、新语法实机目录，本轮全部用 FakePool 与合成 DDL 模拟。设计 §8.2.1 的容量核算（PAR-21）**仍是空的**，D03 按设计只能给"范围受限通过"。
   - **71 MiB 真实网关日志未取得**——我用的是 60 MiB 合成日志，只证明了"大小不再被拦"和流式解析吞吐，**不等于**真实日志能在 540 秒内分析完成。§8.4 的容量门禁未验收。
   - **200 MiB 边界、504 超时回收、子进程 TERM/KILL 路径**本轮未测。
   - **浏览器端**未做真实渲染验证（本轮为 API 与服务层测试），前端只做了源码级核对。
4. 本轮结论只覆盖上述实测范围；未测部分不因"设计写了"或"代码看着对"而推定通过。

---

测试人：智能体 A（ClaudeA）
被测版本：v1.6.3.4 `main@6a339df`
提交给：Mr.Linsang
