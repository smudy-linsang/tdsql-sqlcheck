# v1.6.3.2 审核规则调整与扫描历史跨页对比
## 第六轮生产门禁整改复测报告（智能体 O）

| 项目 | 内容 |
|---|---|
| 测试版本 | v1.6.3.2 |
| 被测提交 | `76752ef47113fcd9af63d4949dff024bad382978` |
| 整改来源 | 第五轮报告 `UAT-O-1632-R5-01` / `UAT-O-1632-R5-02` 与 Q 开发报告 Rev.R6 |
| 测试日期 | 2026-09-05 |
| 测试人 | 智能体 O（独立 UAT） |
| 最终结论 | **不通过；GATE-3 已由林桑签署通过，GATE-2 仍被 1 项 P1 阻断，禁止生产发布** |

---

## 1. 执行摘要

O 在当前 `main` 提交上完成了代码差异审计、官方语法复核、真实浏览器点击、审核入口矩阵、专项/全量/三方回归。结论如下：

1. **`UAT-O-1632-R5-02`（P2）关闭。** Q 已把 MAXVALUE 归一化改为 token/span 方式；bare、括号、大小写、换行以及关键字间块注释/行注释均不再触发 E999 与建表规则级联，R121 仍精准保留。O 技术复测通过后，林桑已于 2026-09-05 明确签署 GATE-3 通过；该门禁正式关闭，不再列入后续待办。
2. **`UAT-O-1632-R5-01`（P1）仅部分修复，继续未关闭。** 非 TABLE 对象分流已经正确，集中式 VIEW 页面审核通过；但官方合法的存储过程参数模式 `IN/OUT/INOUT` 仍被误报 E999，例程内 `IF/CASE/WHILE/LOOP/REPEAT` 以及 CASE 表达式仍会被错误拆句。
3. **入口行为不一致。** 即时审核使用新拆分器，但它只统计 `BEGIN/END`；文件审核仍使用独立的字符串判断器；`/batch-stream` 仍使用数据库通用拆分器。相同例程可分别得到 3 段、3 条结果或 4 段，Q 报告“文件审核行为一致”的说法不成立。
4. **既有回归全绿不改变 UAT 裁决。** 当前新增测试只覆盖简单 `BEGIN SET; SET; END` 及无参数、无复杂控制流的最小对象，没有覆盖官方例程参数模式、控制流闭合和三个真实审核入口。

本轮缺陷总量：`P0=0、P1=1、P2=0、P3=0`。GATE-1、GATE-3 均已由林桑签署通过；GATE-2 不通过；生产准出仍为禁止。

---

## 2. 证据边界与官方语法依据

### 2.1 证据边界

- O 使用当前提交在 `127.0.0.1:8002` 新启隔离服务，以真实浏览器登录、选择集中式/分布式、输入 SQL 并点击“开始审核”。
- 用户此前提供的内网 TDSQL 建表成功截图继续作为 GATE-3 的真实目标数据库证据；O 本轮未直连该内网集群，不把截图事实冒充为 O 现场执行。
- 本轮没有执行目标麒麟 V10 SP3 部署后 12/0/0，也没有执行生产容量或性能测试。
- 工作树中的用户文件 `docs/PRODUCTION-DEPLOY-ISSUES-v1.6.3.0.md` 未读取、未修改、未暂存。

### 2.2 官方语法依据

- MySQL 官方 `CREATE PROCEDURE` 语法明确规定过程参数为 `[IN | OUT | INOUT] param_name type`，例程体既可以是简单语句，也可以是 `BEGIN...END` 复合语句：[CREATE PROCEDURE and CREATE FUNCTION](https://dev.mysql.com/doc/mysql/8.0/en/create-procedure.html)。
- 官方复合语句说明指出，存储过程、函数、触发器、事件的块中可包含嵌套块、声明、游标、条件处理和控制流：[Compound Statement Syntax](https://dev.mysql.com/doc/refman/8.0/en/sql-compound-statements.html)。
- 官方 `IF` 语法为 `IF...THEN...END IF`，块内每条语句和 `END IF` 都以分号结束，且可以嵌套：[IF Statement](https://dev.mysql.com/doc/refman/8.0/en/if.html)。
- 官方 LOOP 示例直接展示 `LOOP` 中嵌套 `IF...END IF`，之后还有 `LEAVE`、`END LOOP` 和后续 SET：[LOOP Statement](https://dev.mysql.com/doc/refman/8.0/en/loop.html)。
- 腾讯云 TDSQL MySQL 分布式版限制文档明确列出分布式不支持存储过程、触发器、游标、复合语句及自定义函数，支持 R030/R031 仅分布式的治理边界：[TDSQL MySQL 使用限制](https://cloud.tencent.com/document/product/557/47511)。

因此，“集中式放行合法例程”的门禁语义不能只用无参数、单语句样例代替；至少必须接受官方参数模式和官方列出的复合控制结构。同时，分布式应由 R030/R031 精确拦截，而不是附加工具自身的 E999 或把一个例程拆成多条伪 SQL。

---

## 3. 测试基线与 Q 改动核对

| 项目 | 实际值 |
|---|---|
| 仓库 | `C:\TDSQL_SQLCHECK\TDSQL-SQLCheck` |
| 分支 | `main` |
| HEAD / origin/main（测试开始时） | `76752ef47113fcd9af63d4949dff024bad382978` / 一致 |
| 隔离服务 | `127.0.0.1:8002`，页面版本 v1.6.3.2 |
| Q 变更范围 | parser、即时审核拆分入口、规则专项测试、开发报告 |
| 未动模块策略 | 以全量与三方回归作简单放行；不重复展开已经过多轮确认的跨页对比等功能 |

Q 的三项主要改动事实成立：

1. `parser_legacy.py:2734` 按 `exp.Create.kind` 将 TABLE 与非 TABLE 对象分流，`_parse_create()` 在 `:3096` 增加 TABLE 防御守卫；
2. `parser_legacy.py:118` 的 MAXVALUE 归一化改用 token/span；
3. `audit_service.py:191` 的即时审核切换到 `split_sql_statements_for_audit()`。

但 `split_sql_statements_for_audit()` 在 `parser_legacy.py:242` 只维护一个 `BEGIN/END` 深度；所有 `END IF/CASE/WHILE/LOOP/REPEAT` 里的 `END` 都会错误减少外层 BEGIN 深度。`checker.py:430` 文件审核继续保留独立字符串逻辑，`sql_audit.py:219` 流式入口继续调用旧 `database.split_sql_statements()`，没有完成共享契约。

---

## 4. 浏览器真实点击结果

### 4.1 已通过项

| 页面操作 | 页面结果 | 判定 |
|---|---|---|
| 集中式：`CREATE VIEW v_order AS SELECT 1 AS id` | 审核通过，SQL 类型 `CREATE VIEW`；无建表规则误报 | **对象分流修复通过** |
| 分布式：二级 RANGE 的 `VALUES LESS THAN /*合法注释*/ MAXVALUE` 合规建表样例 | 仅出现 R121；无 E999/R003/R004/R005/R028/R118 | **R5-02 关闭** |

### 4.2 仍失败项

在“SQL 审核 → 即时审核”选择“集中式”，输入：

```sql
CREATE PROCEDURE p_in(IN x INT) SELECT x;
```

点击“开始审核”后，页面显示：

```text
发现 1 项违规
SQL类型: CREATE PROCEDURE
[E999_SYNTAX_ERROR] Expecting ). Line 1, Col: 26 ... IN x INT ...
```

该 SQL 与官方 `[IN | OUT | INOUT] param_name type` 语法一致，属于稳定假阳性。带 `IN` 参数并包含 IF 的合法过程在页面还会显示为 `BATCH`，证明参数解析与拆句是两个独立残留根因。

---

## 5. 例程拆句与入口一致性矩阵

### 5.1 新审核拆分器

输入均为一个合法例程，外层 `BEGIN...END` 中在控制结构后继续执行一条 SET：

| 例程体 | 期望段数 | 实际段数 | 实际错误边界 |
|---|---:|---:|---|
| `SET; SET;` | 1 | 1 | 无 |
| 嵌套 `BEGIN...END` | 1 | 1 | 无 |
| `IF...END IF; SET;` | 1 | **3** | `END IF` 后、SET 后 |
| `CASE...END CASE; SET;` | 1 | **3** | `END CASE` 后、SET 后 |
| `WHILE...END WHILE; SET;` | 1 | **3** | `END WHILE` 后、SET 后 |
| `LOOP...END LOOP; SET;` | 1 | **3** | `END LOOP` 后、SET 后 |
| `REPEAT...END REPEAT; SET;` | 1 | **3** | `END REPEAT` 后、SET 后 |
| `SET x=CASE...END; SET;` | 1 | **3** | CASE 表达式 END 后、SET 后 |

直接走 `AuditService.audit_single_sql()`：

```text
procedure IF（无参数） -> sql_type=BATCH, passed=True, violations=[]
function IF             -> sql_type=BATCH, passed=False, E999_SYNTAX_ERROR
```

“BATCH 且通过”同样是失败：用户提交的是一个 `CREATE PROCEDURE`，系统丢失对象类型和完整性，不能因恰好没有规则命中就判为正确。

### 5.2 文件审核与流式入口

同一多行 `CREATE PROCEDURE ... IF ... END IF; SET; END;`：

| 入口 | 实际结果 |
|---|---|
| `RuleChecker._split_sql_file()` | 拆为 3 条，起始行分别落在过程头、后续 SET、孤立 END |
| `AuditService.audit_file_content()` | 3 个结果：`CREATE PROCEDURE`、`UNKNOWN`、`UNKNOWN` |
| `/api/v1/audit/batch-stream` 使用的 `database.split_sql_statements()` | 拆为 4 段：过程头至首个 SET、`END IF`、后续 SET、孤立 END |

这证明问题不是单个 UI 文案，而是审核结果数量、类型、行号、历史记录和门禁统计都会受到影响。

---

## 6. 官方参数模式兼容矩阵

下列集中式 SQL 均为官方合法最小过程，期望无 E999：

| SQL 形态 | 当前类型 | 当前结果 |
|---|---|---|
| `p(IN x INT) SELECT x` | CREATE PROCEDURE | **E999：Expecting )** |
| `p(OUT x INT) SELECT 1 INTO x` | CREATE PROCEDURE | **E999：Expecting )** |
| `p(INOUT x INT) SET x=x+1` | CREATE PROCEDURE | **E999：Expecting )** |
| 无参数过程 + IF 控制流 | BATCH | **被拆 3 段** |
| 函数 + IF 控制流 | BATCH | **被拆且 E999** |

whole-SQL parser 探针进一步确认：`IN/OUT/INOUT` 的 E999 来自 sqlglot 当前 MySQL 方言的例程参数能力缺口，不是用户 SQL 语法错误；复杂函数即使不经拆分，也可能在合法 IF 体处触发 `Invalid expression / Unexpected token`。因此只修拆分器仍不足以关闭 P1。

---

## 7. 缺陷处置

### 7.1 `UAT-O-1632-R5-01`（P1）——部分修复，继续未关闭

**已通过子项：**

- 非 TABLE CREATE 不再进入建表规则；
- 集中式最小 VIEW/PROCEDURE/FUNCTION/TRIGGER 的无参数、简单体样例可通过；
- 分布式对象能由 R030/R031 负责治理。

**未通过子项：**

- 官方合法 `IN/OUT/INOUT` 参数过程误报 E999；
- IF/CASE/WHILE/LOOP/REPEAT/CASE 表达式破坏例程边界；
- 即时、文件、流式三个入口没有共用同一拆句实现；
- 缺少真实 Service/API/UI 的完整结果锁。

该缺陷直接违反 GATE-2“集中式合法对象不再误拦截”的最终业务目标，维持 P1 和发布阻断。

### 7.2 `UAT-O-1632-R5-02`（P2）——关闭

token/span 修复满足以下关闭标准：

- 注释对关键字序列透明，原文空白与注释保留；
- bare / `(MAXVALUE)` / 大小写 / 换行均不报 E999；
- 非法 `MAXVALUES` 仍失败关闭；
- 字符串或 COMMENT 中的同形短语不被改写；
- 页面仅保留应有 R121，无建表规则级联。

---

## 8. 给 Q 的照图施工解决方案

### 8.1 建立唯一的审核拆分内核

新增一个审核专用、可返回 `sql/start_line/end_line` 的共享拆分器，替换以下三处的分叉实现：

1. `AuditService.audit_single_sql()`；
2. `RuleChecker._split_sql_file()`（外层可继续处理 `DELIMITER` 与 SQL Object 注释，但例程边界必须调用共享内核）；
3. `/api/v1/audit/batch-stream`（不得继续调用数据库执行用途的通用分号拆分器）。

文件上传中的自定义 `DELIMITER //` / `$$` 先由薄适配层识别并剥离指令，再把每个候选对象交给同一内核；不得复制三套 BEGIN/END 逻辑。

### 8.2 拆分器必须使用“构造栈”，不能使用单一 BEGIN 深度

在确认顶层是 `CREATE [OR REPLACE] [DEFINER=...] PROCEDURE|FUNCTION|TRIGGER|EVENT` 后维护构造栈：

- `BEGIN` → 压入 `BEGIN_BLOCK`；
- 语句位置的 `IF ... THEN` → 压入 `IF_STMT`；
- `CASE` → 区分 `CASE_STMT` 与 `CASE_EXPR`，两者都必须保护内部分号；
- `LOOP` / `WHILE ... DO` / `REPEAT` → 压入对应构造；
- `END IF/CASE/LOOP/WHILE/REPEAT` 只弹出匹配构造，**不得**减少外层 BEGIN；
- bare `END` 只关闭 `BEGIN_BLOCK` 或 `CASE_EXPR`；处理标签但不把标签识别成新语句；
- 栈未闭合、结束类型不匹配或例程头/参数括号不闭合时返回结构错误，最终生成 E999；
- 只有例程根体完整闭合后的分隔符才是顶层语句边界；简单例程体则以其第一个顶层分隔符结束。

区分 `IF` 语句与 `IF()` 函数时不能只看下一个 token 是否为左括号，因为合法存储程序允许 `IF (condition) THEN`。应按“当前是否为语句起点 + 同层是否出现 THEN”判断；CASE 表达式也必须有独立栈类型。

### 8.3 增加受限的例程语法兼容层，禁止 blanket 放行

sqlglot 不能完整解析 MySQL 存储程序语法。建议在 `SQLParser.parse()` 中对**已经由词法/结构校验确认完整**的 CREATE ROUTINE 增加兼容路径：

1. 识别可选 DEFINER、对象类型和对象名；
2. 对 PROCEDURE 参数逐项解析 `[IN|OUT|INOUT] name type`，FUNCTION 参数解析 `name type`，校验逗号、括号和类型片段；
3. 识别 FUNCTION 的 `RETURNS type` 及常见 routine characteristics；
4. 复用 §8.2 构造栈验证 simple/compound body 完整性；
5. 若 sqlglot 成功，继续使用 AST；若 sqlglot 仅因已知例程语法能力缺口失败、而兼容层完整通过，则保留原始 SQL，设置准确 `sql_type/created_object_kind/created_object_name`，清除该已知假 E999；
6. 分布式仍由 raw SQL 上的 R030/R031 命中；集中式不产生表规则或假 E999；
7. 参数括号不闭合、缺失对象名/RETURNS/过程体、`END` 类型不匹配等负例必须继续 E999。

不得对所有 `CREATE PROCEDURE/FUNCTION` 无条件吞掉 parse_error，也不得用一个跨全文正则冒充语法校验。只删除参数模式 token 的“解析副本”可以作为简单过程的兼容手段，但复杂控制流仍需上述结构校验；`raw_sql` 必须保持不变。

### 8.4 必增自动化锁

**拆句单测：**

- simple body、嵌套 BEGIN；
- IF/ELSEIF/ELSE、嵌套 IF；
- CASE statement 与 CASE expression；
- LOOP、WHILE、REPEAT，以及官方 LOOP 中嵌套 IF 的示例；
- DECLARE CURSOR/HANDLER、标签、触发器、事件、DEFINER；
- 例程后紧跟 CREATE TABLE；普通多语句与事务 BEGIN；
- DELIMITER `//` / `$$`；多行和单行两种排版。

**语法兼容正例：**

- PROCEDURE 的省略 IN、显式 IN、OUT、INOUT 和混合参数；
- FUNCTION 参数、RETURNS、characteristics、simple RETURN 和 compound IF；
- 每项分别断言 centralized `sql_type` 准确、`violations == []`，distributed 只出现 R030(/R031)，不出现 E999/建表规则。

**失败关闭负例：**

- 参数列表不闭合、非法参数顺序、缺失 RETURNS/体；
- 未闭合 BEGIN/IF/CASE/LOOP/WHILE/REPEAT；
- `END` 类型错配；
- 例程后存在半条 SQL。

**入口合同测试：**

- 对 `/api/v1/audit/sql`、`/api/v1/audit/file`（及上传入口）和 `/api/v1/audit/batch-stream` 发送同一多行例程；
- 三个入口都必须得到恰好 1 个对象、相同 `sql_type` 和违规集合；
- 历史记录 `total_sql` 必须为 1，行号为例程首行；
- 至少保留一条真实浏览器集中式 `IN/OUT/INOUT + IF` 点击用例。

---

## 9. 自动化与回归结果

| 命令 / 测试集 | 结果 |
|---|---|
| `pytest tests/test_rules_v1632.py tests/test_instance_scope_rules.py -q` | **99 passed，3 warnings，0 failed，4.04s** |
| `python tests/rule_audit_materials/verify_rules.py --verbose` | **PASS；total=121，covered=109，metadata=7，exempt=5，failures=0** |
| `pytest tests -q` | **1865 passed，11 warnings，0 failed，402.13s** |
| `T3P_BASE_URL=http://127.0.0.1:8002 pytest tests_3p -q` | **125 passed，1 skipped，2 warnings，0 failed，22.43s** |

本轮没有无效的三方运行。上述测试证明既有定义无回归，但它们没有覆盖 §5/§6 的官方参数和复杂控制流，因此不能抵消浏览器及独立入口的稳定失败证据。

---

## 10. 门禁裁决

| 门禁 | 第六轮状态 | O 意见 |
|---|---|---|
| GATE-1 | 已由林桑签署通过 | 保持，不重开 |
| GATE-2 | **不通过** | 对象分流子项通过；R5-01 的例程参数、控制流与入口一致性仍失败 |
| GATE-3 | **林桑已签署通过** | R5-02 已关闭；O 技术复测通过后，林桑于 2026-09-05 确认签署，门禁正式关闭 |
| 生产准出 | **禁止** | 等 Q 再修 R5-01、O 第七轮关闭、林桑完成 GATE-2 签署，并完成目标麒麟 12/0/0 |

---

## 11. 最终裁决

**第六轮复测不通过。**

Q 对 MAXVALUE 注释边界和非 TABLE 对象分流的修复有效，分别关闭 `R5-02` 和 `R5-01` 的一个子根因；但“集中式合法例程可用”尚未实现。`IN/OUT/INOUT` 过程仍被 E999 拦截，复杂控制流仍在即时、文件和流式入口被不同方式拆散。请 Q 按 §8 完成共享拆分内核、例程受限兼容层和三入口合同测试后，再转 O 第七轮定点复测。

证据索引：`docs/evidence/v1.6.3.2-uat-o-r6/README.md`。
