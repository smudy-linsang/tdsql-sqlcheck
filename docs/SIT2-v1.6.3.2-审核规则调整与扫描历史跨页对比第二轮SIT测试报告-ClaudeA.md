# SIT2-v1.6.3.2 审核规则调整与扫描历史跨页对比 第二轮 SIT 测试报告

| 项 | 内容 |
|---|---|
| 被测对象 | v1.6.3.2 **第一轮 SIT 整改版**（提交 `b7f5cf0`，6 文件 / +269 −23） |
| 上一轮 | `SIT-v1.6.3.2-…第一轮SIT测试报告-ClaudeA.md`（`cf78cad`，不通过：2 BLOCK + 2 MINOR + 1 NIT） |
| 设计基线 | `DESIGN-v1.6.3.2-…详细设计说明书.md` **Rev.D**（随本次整改同步） |
| 测试人 | 智能体 A |
| 测试轮次 | 第二轮 SIT（缺陷关闭核验 + 整改副作用探测 + 回归对照 + 第一轮遗留项补测） |
| 测试日期 | 2026-09-04 |
| 测试环境 | 沙箱 MariaDB 10.11.14 @13306；后端 uvicorn @18800；Chromium 真实浏览器；sqlglot 30.14.0 |
| **测试结论** | **通过。5 项缺陷全部关闭，零新增缺陷；第一轮遗留的 FE-11/FE-12 两项本轮补测通过。可提交 UAT。** |

---

## 1. 结论摘要

| 编号 | 级别 | 上一轮问题 | 关闭状态 | 关键证据 |
|---|---|---|---|---|
| DEF-SIT-01 | BLOCK | R121 对真实分区表达式失明 | ✅ **关闭** | 18 种表达式 × 2 种边界 = **36/36 全部命中 R121**；真实 `SHOW CREATE TABLE` 产物端到端命中；**11 种误报攻击面 0 命中** |
| DEF-SIT-02 | BLOCK | 合成 KFN 守卫把合法 DDL 判成语法错误 | ✅ **关闭** | §4.7.5 矩阵 **9 行 × 2 实例类型全部归位**；`ALTER REORGANIZE` 在两种实例类型下均不再产生 E999 |
| DEF-SIT-03 | MINOR | LIMIT 回退多做一次全量词法化 | ✅ **关闭** | 8 类语句词法化次数与 v1.6.3.2 前基线**逐条一致**（全部 3 次） |
| DEF-SIT-04 | MINOR | `TEST_SPEC` 未更新 | ✅ **关闭** | 5 处当前能力声明改 121；覆盖统计 `109` 经我实跑 `verify_rules.py` 核对**属实** |
| DEF-SIT-05 | NIT | Oracle 用例名残留 119 | ✅ **关闭** | 已改名 `test_total_rules_121` |

**本轮零新增缺陷。** 第一轮遗留的两项未覆盖项（FE-11 慢响应竞态、FE-12 退出登录清空）本轮已补测，均通过。

---

## 2. 缺陷关闭核验

### 2.1 DEF-SIT-01（BLOCK）：分区表达式覆盖面

**整改方式与我给的方案 A 一致**：新增 `_skip_balanced_parens()` + `_consume_partition_expr_lenient()`，
策略扫描改用宽松消费器（只跳过不校验），恢复门禁的 `_consume_partition_expr()` **一个字不动**。

**调用点隔离核验**（这是方案 A 的安全前提，必须逐点确认）：

```text
_consume_partition_expr_lenient  → 仅 1262 行（_scan_secondary_partition_policy_tokens）✅
_skip_balanced_parens            → 仅 1018 行（宽松消费器内部）✅
_consume_partition_expr（严格）  → 仅 1198 行（_consume_secondary_partition → _plan_recovery）✅
```

**覆盖面实测：18 种分区表达式 × bare/括号两种边界 = 36 组，全部命中 R121，`method` 正确回填 `RANGE`：**

| 表达式类别 | 形态 | 结果 |
|---|---|---|
| 列引用 | `(dt)`、`` (`dt`) `` | ✅ ✅ |
| 原白名单函数 | `YEAR` / `MONTH` / `DAY` | ✅ ✅ ✅ |
| **原先失明的日期函数** | `TO_DAYS` / `` to_days(`dt`) `` / `TO_SECONDS` / `UNIX_TIMESTAMP` / `YEARWEEK` / `DATEDIFF` | **全部 ✅** |
| **原先失明的表达式** | `EXTRACT(YEAR FROM dt)` / `ABS(id)` / `MOD(id,7)` / `FLOOR(id/100)` | **全部 ✅** |
| **原先失明的 COLUMNS/多列** | `COLUMNS(dt)` / `` COLUMNS(`dt`,id) `` / `(id,dt)` | **全部 ✅** |

**真实产物端到端**（本机 MariaDB `SHOW CREATE TABLE` 原样，含反引号、`ENGINE = InnoDB`、多行）：

```text
 PARTITION BY RANGE (to_days(`dt`))
(PARTITION `p0` VALUES LESS THAN (738000) ENGINE = InnoDB,
 PARTITION `pmax` VALUES LESS THAN MAXVALUE ENGINE = InnoDB)
→ maxvalue_partitions=('pmax',)  method=RANGE  distributed=[E999, R121]   ✅
括号版 → 同样命中 R121                                                     ✅
正常上界版 → maxvalue_partitions=()，不含 R121                             ✅
```

**误报攻击面（宽松化后的头号风险，我按 11 个方向逐一试）——0 命中：**

| 攻击向量 | R121 |
|---|---|
| 一级 `TDSQL_DISTRIBUTED BY RANGE(id) (… MAXVALUE)` | 不命中 ✅ |
| 一级含 MAXVALUE + 二级不含 | 不命中 ✅ |
| 表注释里整段伪造分区子句 | 不命中 ✅ |
| 列注释里伪造 | 不命中 ✅ |
| `--` 行注释里伪造 | 不命中 ✅ |
| `/* */` 块注释里伪造 | 不命中 ✅ |
| `CREATE TDSQL_SEQUENCE … TDSQL_MAXVALUE 100` | 不命中 ✅ |
| 二级 `LIST VALUES IN ('MAXVALUE')` | 不命中 ✅ |
| 二级 RANGE 正常上界（对照） | 不命中 ✅ |
| 列名叫 `maxvalue` | 不命中 ✅ |
| 字符串默认值 `'VALUES LESS THAN MAXVALUE'` | 不命中 ✅ |

### 2.2 DEF-SIT-02（BLOCK）：合成守卫的来源门闸

整改为一行 `source_context == "CREATE"`，并把注释里的错误事实前提改对。**§4.7.5 矩阵 9 行 × 2 实例类型逐格复测，全部归位：**

| 用例 | distributed 实测/期望 | centralized 实测/期望 |
|---|---|---|
| CREATE bare | `E999+R121` / `E999+R121` ✅ | `E999` / `E999` ✅ |
| CREATE 括号 | `R121` / `R121` ✅ | `-` / `-` ✅ |
| CREATE 正常上界 | `-` / `-` ✅ | `-` / `-` ✅ |
| **REORG bare** | **`R121`（无 E999）** ✅ | **`-`** ✅ |
| **REORG 括号** | **`R121`（无 E999）** ✅ | **`-`** ✅ |
| REORG 正常上界 | `-` / `-` ✅ | `-` / `-` ✅ |
| ALTER ADD bare / 括号 | `E999+R121` ✅ | `E999` ✅ |
| ALTER ADD 正常上界 | `E999` ✅ | `E999` ✅ |

合法 DDL 在集中式实例上的凭空 ERROR 级 E999 已消除。

### 2.3 DEF-SIT-03（MINOR）：词法化次数

整改为"AST 完好时早退"。前后基线逐条对照（tokenizer 打桩计数）：

| 语句 | v1.6.3.2 前基线 | 首版 | **整改后** |
|---|---:|---:|---:|
| SELECT ×2 / INSERT / CREATE / ALTER / `UPDATE … LIMIT 2000` | 3 | 3 | **3** |
| **`UPDATE … WHERE id=1`（无 LIMIT）** | 3 | **4** | **3** ✅ |
| **`DELETE … WHERE id=1`（无 LIMIT）** | 3 | **4** | **3** ✅ |

设计 §5.4 的性能不变量成立。

### 2.4 DEF-SIT-04 / DEF-SIT-05

`tests/TEST_SPEC-规则覆盖与压力测试.md` 5 处当前能力声明已改 121，IP `119.45.220.89` 保持原样 ✅。
覆盖统计写的是 `规则总数: 121  文件审核已覆盖: 109  未覆盖: 0`——我**实跑** `verify_rules.py` 核对：

```text
规则总数: 121  文件审核已覆盖: 109  未覆盖: 0
  其中 需元数据验证: 7 -> R048,R055,R056,R057,R058,R060,R064
  其中 已知不可触发(豁免): 5 -> R025,R035,R038,R049,R059
结论: [PASS] 断言全过且规则全覆盖（除已知不可触发）
```

数字属实，不是照抄 ✅。`test_total_rules_121` 已改名，同文件 `test_r078_to_r119_continuous` 中的 `R119`（Oracle 子集编号上界）按分类规则保持不变 ✅。

---

## 3. 回归锁的有效性（变异验证）

Q 按我在第一轮报告里给的清单补了 6 条回归锁。一条不会红的断言等于没有断言，所以逐条注入原缺陷验证：

| 变异 | 操作 | 结果 |
|---|---|---|
| M0 | 现版本 | **64 passed** ✅ |
| M1 | 撤回 DEF-SIT-02 门闸（删掉 `source_context == "CREATE"`） | **4 failed** ✅ —— `test_reorganize_maxvalue_must_not_fabricate_e999` 的 4 个参数组合全红 |
| M2 | 撤回 DEF-SIT-01（策略扫描改回严格消费器） | **12 failed** ✅ —— 表达式形态参数化 + 真实 `SHOW CREATE TABLE` 端到端全红 |
| M3 | 撤回 DEF-SIT-03（删掉 AST 完好早退） | **1 failed** ✅ —— `test_dml_limit_does_not_add_tokenization_when_ast_is_sound` |
| M4 | 把宽松消费器塞进恢复门禁 `_consume_secondary_partition` | **1 failed** ✅ —— 源码级反向锁 `test_lenient_expr_does_not_widen_recovery_gate` 红 |
| M0' | 全部还原 | **64 passed** ✅ |

四条变异全部红灯、还原后全绿。**反向锁尤其关键**：它是防止后人"顺手统一"两个消费器、把恢复门禁一起放宽的唯一自动防线，M4 证明它有牙齿。

> 顺带一笔：我在第一轮报告里把恢复门禁的函数名写成了 `_scan_create_tail`，
> 仓库里的真实名字是 `_scan_table_tail`。Q 在实现反向锁时按真实名字纠正了，
> 三个函数 `_plan_recovery` / `_scan_table_tail` / `_consume_secondary_partition` 均真实存在——
> 我逐个核对过，锁不是空转。

---

## 4. 第一轮遗留项补测

第一轮我如实记了两项没跑通/没构造的用例，本轮补齐：

### 4.1 FE-12 退出登录清空（第一轮我的下拉菜单定位失败）

真实浏览器：进入「SQL审核→在线元数据审核→扫描对比」勾满两条 → 退出登录 → 重新登录 → 回到同一页：

```text
selected_before_logout   = 2      按钮可用 = true
logged_out               = true
checked_after_relogin    = 0      按钮可用 = false      ✅
```

### 4.2 FE-11 慢响应竞态（第一轮未构造可控延迟）

用 Playwright 路由拦截把**第 2 页请求人为延迟 4 秒**，第 3 页请求正常放行，然后快速连点两次「下一页」：

```text
p1_first        = 2026-09-01 10:24     （第 1 页首行）
final_page_no   = 3                     ✅ 页码停在 3
final_first     = 2026-09-01 10:04     ✅ 正是第 3 页（offset 20）的首行
```

对照数据库真实排序：offset 0 / 10 / 20 的首行分别是 `10:24` / `10:14` / `10:04`。
**迟到的第 2 页响应（`10:14`）被正确丢弃，没有覆盖第 3 页内容**——`cmpReqSeq` 请求序号保护生效 ✅。

---

## 5. 整改副作用与回归

### 5.1 本轮改动面

`git diff cf78cad b7f5cf0 --name-only`：

```text
backend/engine/parser/parser_legacy.py     +66 −7    （三处整改）
tests/test_rules_v1632.py                  +141      （6 条回归锁）
tests/test_oracle_compat_rules.py          ±1        （用例改名）
tests/TEST_SPEC-规则覆盖与压力测试.md         ±22       （121 + 覆盖统计）
docs/DESIGN-…说明书.md                      +20 −2    （Rev.C → Rev.D）
docs/DEV-…开发报告.md                        +41       （整改记录）
```

**`frontend/`、`backend/engine/rules/`、`backend/engine/checker.py` 本轮零变更** ✅ ——
整改只落在解析器与测试，规则实现和前端未被顺手改动。

### 5.2 第一轮已通过项复核（不得倒退）

| 项 | 本轮实测 |
|---|---|
| 数量口径 | 总数 121 / DDL 23 / DISTRIBUTED 15 / 仅分布式 30 / 集中式 91 / Oracle 42 ✅ |
| R011/R120 CREATE 12 例 | 全对 ✅ |
| R011/R120 ALTER 6 例 | 全对 ✅ |
| R058 9 例（含注释、字符串、两参数 LIMIT、超大整数） | 全对 ✅ |
| R058 结果无"执行异常" | ✅ |
| R035 6 例（含 `INT(11)/INT`、`INT UNSIGNED/INT`、`TEXT/MEDIUMTEXT`） | 全对 ✅ |
| 前端跨页选择抽检（bigtable，前端零变更） | 跨页选两条、按钮可用、回上页恢复、请求体 `[482,472]`、超选回滚、查询清空 —— 全通过 ✅ |

### 5.3 全量回归

| 轮次 | 结果 |
|---|---|
| 第一轮（`c0e5e25`） | 4 failed / **1691** passed / 83 skipped / 29 errors |
| **本轮（`b7f5cf0`）** | 4 failed / **1716** passed / 83 skipped / 29 errors |

**失败集合逐行 `diff` 完全一致**——仍是那 4 项沙箱环境项（`o23` 默认值归一 ×2、`monitordb` 夹具、`file_report_delete` 夹具），与本次改动无关。
通过数 `1691 → 1716`，净增 25 正是 Q 新增的回归锁（6 条用例，其中含 18 个表达式参数化与 4 个实例/边界组合）。
29 项 error 全部是 G14 的库名闸与浏览器夹具，与 v1.6.3.2 无关。

专项门禁：`test_rules_v1632.py` **64 passed**、`test_oracle_compat_rules.py` **103 passed**、`test_instance_scope_rules.py` **13 passed**。

### 5.4 设计文档同步（Rev.C → Rev.D）

逐条核对，我在第一轮要求的文档同步全部落地，且有超出：

| 位置 | 内容 |
|---|---|
| §4.7.3 第 11 条 | 新增"分区表达式形态不参与 R121 命中判定"的分流原则，写明"白名单认不出=不恢复对恢复门禁是安全失败关闭，但对策略扫描是规则漏报，安全方向相反" |
| §4.7.5 | 订正合成守卫的立论、限定 `source_context == "CREATE"`，**新增 ALTER REORGANIZE 正常上界一行** |
| §5.4 第 11/12 条 | 守卫适用范围 + 宽松消费器分流 + 源码级反向锁要求 |
| §5.4 性能不变量 | 扩展到 DML LIMIT 通道，写明首版 15→17 的实测量级 |
| §10.1 R121 第 13-16 条 | 全表达式参数化 / 真实产物端到端 / 反向锁 / REORGANIZE 四组合 |
| §12 RISK-19 | 新增，登记"表达式白名单曾使 R121 主战场失明" |
| §15 | Rev.D 修订记录 |

---

## 6. 遗留与结论

### 6.1 遗留（均属 UAT/发布阶段，非 SIT 阻断）

| 项 | 说明 |
|---|---|
| 内网 UAT-01～UAT-11 | 需真实 TDSQL 分布式/集中式实例，沙箱无法覆盖 |
| 目标环境（TDSQL/MySQL 8）全量回归 | 沙箱是 MariaDB，本报告门禁数据不能替代内网重跑 |
| §12 三项生产发布书面门禁 | LIMIT 版本前提 / DBA 接受集中式零覆盖 / 规则集与流水线负责人接受门禁双向变化。Q 已建 `GATE-v1.6.3.2-…发起.md`，属发布环节 |

### 6.2 一项观察（不计缺陷）

**真实 `SHOW CREATE TABLE` 的分区表输出会同时产生一条 `E999_SYNTAX_ERROR`**——
sqlglot 不认逐分区的 `ENGINE = InnoDB` 选项。我在整改前后各测一次，`parse_error` **逐字相同**
（`Expecting ). Line 7, Col: 48`），确认是 **v1.6.3.2 之前就存在**的解析器限制，不是本次引入。

影响：在线元数据审核对分区表会同时给出 E999 与 R121。R121 的治理信息完整可用，
但用户会看到一条"语法错误"提示而语句并无语法错误。建议在 UAT 记录中确认可读性，
若需消除应另立解析器课题——**不属于 v1.6.3.2 范围，也不构成本轮阻断**。

### 6.3 结论

**通过。**

五项缺陷全部关闭，每一项我都做了行为级复现而不是看提交信息：
DEF-SIT-01 用 36 组表达式 × 边界组合 + 真实 `SHOW CREATE TABLE` 产物 + 11 个误报攻击面三面夹逼；
DEF-SIT-02 把 §4.7.5 矩阵 18 格逐格复测；DEF-SIT-03 与改前基线逐条比对词法化次数；
DEF-SIT-04 的覆盖统计我实跑 `verify_rules.py` 核对过数字。

整改质量值得说一句：**方案 A 的关键是"分流而不是放宽"**，Q 完整实现了这一点——
宽松消费器只出现在策略扫描一处，恢复门禁的严格白名单一个字没动，还配了源码级反向锁；
我用 M4 变异（把宽松消费器塞进恢复门禁）验证该锁确实会红。这是本次整改里最容易做错、
也最容易做成"看起来对"的地方，做住了。

第一轮我如实记录的两项未覆盖项本轮也补齐了，四条变异全部红灯、还原全绿，
全量回归失败集合与上一轮逐行一致、通过数净增可解释。

**建议提交 UAT。**
