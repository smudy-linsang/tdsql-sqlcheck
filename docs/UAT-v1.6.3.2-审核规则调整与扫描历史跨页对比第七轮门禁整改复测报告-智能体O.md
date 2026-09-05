# v1.6.3.2 审核规则调整与扫描历史跨页对比
## 第七轮生产门禁整改复测报告（智能体 O）

| 项目 | 内容 |
|---|---|
| 测试版本 | v1.6.3.2 |
| 被测提交 | `995a38bf3dad4a90be82351b19f992621bbb38e2` |
| 整改来源 | 第六轮报告 `UAT-O-1632-R5-01` 的残留项及 Q 开发报告 Rev.R7 |
| 测试日期 | 2026-09-05 |
| 测试人 | 智能体 O（独立 UAT） |
| 门禁签署人 | Mr.Linsang |
| 最终结论 | **不通过；新增 P1×2，GATE-2 继续阻断，GATE-1/GATE-3 已签署状态不变** |

---

## 1. 执行摘要

O 已在当前 `main` 提交上完成代码差异审计、MySQL/TDSQL 官方语法复核、真实浏览器点击、四个审核接口验证、失败关闭反向矩阵、专项/全量/三方回归。

Q 对第六轮核心正向复现的整改**部分有效**：

1. 不带 `DELIMITER` 的 `IN/OUT/INOUT + IF/CASE/WHILE/LOOP/REPEAT` 过程能够作为一个对象审核；
2. 集中式复杂过程不再显示 BATCH，页面显示 `CREATE PROCEDURE` 并审核通过；
3. 同一普通复杂过程经即时、文件和流式入口均只产出一个审核结果；
4. GATE-3 的 MAXVALUE 修复保持有效，规则总数与集中式跳过数仍为 121/31。

但本轮发现两个独立发布阻断缺陷：

| 缺陷编号 | 级别 | 摘要 | 状态 |
|---|---:|---|---|
| `UAT-O-1632-R7-01` | **P1** | 例程兼容层既误放明确非法 SQL，又拒绝官方合法 DEFINER/表达式组合 | 新建 / 未关闭 |
| `UAT-O-1632-R7-02` | **P1** | 标准 DELIMITER 例程文件仍报 E999，流式入口仍拆成多条 | 新建 / 未关闭 |

当前缺陷为 `P0=0、P1=2、P2=0、P3=0`。GATE-1、GATE-3 已由 Mr.Linsang 签署通过，不重开；GATE-2 继续不通过；生产发布继续阻断。

---

## 2. 证据边界与官方依据

### 2.1 证据边界

- O 使用 Q 当前提交在 `127.0.0.1:8002` 新启隔离服务，真实浏览器登录、选择集中式、输入 SQL 并点击“开始审核”。
- API 证据来自该隔离服务的真实 `/api/v1/audit/sql`、`/file`、`/upload`、`/batch-stream` 响应，不是函数模拟。
- 规则引擎反向矩阵用于定位边界，不能替代浏览器/API 证据；报告分别标注。
- 未直连 Mr.Linsang 的内网 TDSQL 集群；未执行目标麒麟 V10 SP3 部署后 12/0/0；未执行生产容量或性能测试。
- 用户自有未跟踪文件 `docs/PRODUCTION-DEPLOY-ISSUES-v1.6.3.0.md` 未读取、未修改、未暂存。

### 2.2 官方语法事实

MySQL 官方 `CREATE PROCEDURE / CREATE FUNCTION` 文档明确规定：

1. 参数列表括号必须始终存在；无参数也必须写 `()`；
2. `IN/OUT/INOUT` 仅可用于 PROCEDURE，FUNCTION 参数不能带这些模式；
3. 参数必须是逗号分隔的 `param_name type`；
4. `routine_body` 必须是有效 SQL 例程语句；
5. DEFINER 是 CREATE 例程的官方可选子句。

依据：[MySQL CREATE PROCEDURE and CREATE FUNCTION](https://dev.mysql.com/doc/mysql/8.0/en/create-procedure.html)。

MySQL 官方还明确：触发器头必须含触发时机、事件、目标表、`FOR EACH ROW` 和合法 trigger body；多语句触发器使用 `BEGIN...END`：[MySQL CREATE TRIGGER](https://dev.mysql.com/doc/refman/8.0/en/create-trigger.html)。复合例程允许嵌套块和控制流，客户端通常通过 `DELIMITER` 让体内分号随整个对象发送：[MySQL BEGIN...END](https://dev.mysql.com/doc/refman/8.0/en/begin-end.html)。

腾讯云 TDSQL MySQL 分布式版明确不支持存储过程、触发器、游标、复合语句及自定义函数，故这些对象在分布式由 R030/R031 拦截、在集中式按兼容语法审核的门禁边界仍成立：[TDSQL MySQL 使用限制](https://cloud.tencent.com/document/product/557/47511)。

---

## 3. 测试基线与代码核对

| 项目 | 实际值 |
|---|---|
| 仓库 | `C:\TDSQL_SQLCHECK\TDSQL-SQLCheck` |
| 分支 | `main` |
| HEAD / origin/main（测试开始时） | `995a38bf3dad4a90be82351b19f992621bbb38e2` / 一致 |
| 页面版本 | v1.6.3.2 |
| Q 新增测试 | `tests/test_routine_audit_r6.py`，32 项 |
| Q 声明 | 全控制流、参数兼容、失败关闭及三入口一致均已完成 |

静态核对确认 Q 确实实施了：

- `parser_legacy.py:267`：例程构造栈；
- `parser_legacy.py:321~492`：例程头、参数、体结构和兼容填充；
- `parser_legacy.py:497`：共享审核拆分器；
- `checker.py:430`：文件拆分入口调用共享未闭构造计数；
- `sql_audit.py:219`：流式入口改用共享拆分器。

问题不是“没有写代码”，而是兼容层的语法约束不足，且 DELIMITER 适配仍停留在文件拆分器外壳，未真正统一到共享入口。

---

## 4. 已通过的整改项

### 4.1 浏览器正向复测

在“SQL审核 → 即时审核”选择“集中式”，输入：

```sql
CREATE PROCEDURE p_x(IN pid INT)
BEGIN
  IF pid > 0 THEN
    SET @a = pid;
  END IF;
  UPDATE t SET n = pid WHERE id = pid;
END;
```

页面显示：

```text
审核通过
SQL类型: CREATE PROCEDURE
适用架构: 集中式
集中式审核：已排除 31 条分布式规则
```

原第六轮的 IN 参数、控制流和 BATCH 问题在这个核心场景已恢复。

### 4.2 三入口普通例程

对同一不含客户端 DELIMITER 指令的复杂过程：

| 入口 | 结果数 | SQL 类型 | 违规 |
|---|---:|---|---|
| `/api/v1/audit/sql` | 1 | CREATE PROCEDURE | `[]` |
| `/api/v1/audit/file` | 1 | CREATE PROCEDURE | `[]` |
| `/api/v1/audit/batch-stream` | 1 个数据帧 | 响应未提供 sql_type | `[]` |

拆句数量已一致。流式数据帧仍缺少 `sql_type`，尚不能满足第六轮提出的“结果类型一致”可观测性要求，见 §8.2。

### 4.3 GATE-3 保持性验证

带 `VALUES LESS THAN /*合法注释*/ MAXVALUE` 的分布式二级 RANGE 表仅命中既有 INFO 与 R121；无 E999/R003/R004/R005/R028/R118 级联。规则总数 121，集中式跳过 31。GATE-3 已签署状态保持，不因本轮 parser 改动重开。

---

## 5. `UAT-O-1632-R7-01`（P1）：例程兼容层错误放行与错误拦截并存

### 5.1 浏览器真实复现：非法过程被审核通过

操作步骤：

1. 登录 v1.6.3.2；
2. 进入“SQL审核 → 即时审核”；
3. 选择“集中式”；
4. 输入 `CREATE PROCEDURE p_bad SELECT 1;`；
5. 点击“开始审核”。

实际页面显示：

```text
审核通过
SQL类型: CREATE PROCEDURE
适用架构: 集中式
```

该 SQL 缺少官方强制要求的参数括号，必须产生 E999，不得通过。

### 5.2 浏览器真实复现：合法 DEFINER 过程被误拦

输入：

```sql
CREATE DEFINER = 'admin'@'localhost'
PROCEDURE p_def(IN x INT) SELECT x;
```

页面实际显示 SQL 类型为 `SELECT`，并产生：

```text
R051  SELECT语句无WHERE条件
E999_SYNTAX_ERROR  Expecting ) ... PROCEDURE p_def(IN x INT) ...
```

该语句符合官方 DEFINER 和 IN 参数语法；SQL 类型、规则对象和语法结论三项均错误。

### 5.3 规则引擎反向矩阵

以下非法 SQL 当前均 `violations == []`：

| 非法形态 | 违反的官方约束 | 当前结果 |
|---|---|---|
| `CREATE PROCEDURE p SELECT 1` | 参数括号必须存在 | 审核通过 |
| `p(,x INT)` | 前置空参数段 | 审核通过 |
| `p(x INT,)` | 尾随空参数段 | 审核通过 |
| `p(x INT,,y INT)` | 连续逗号/空参数段 | 审核通过 |
| `p(x INT y INT)` | 参数间缺逗号 | 审核通过 |
| `FUNCTION f(IN x INT)` | FUNCTION 禁止参数模式 | 审核通过 |
| `FUNCTION f(OUT x INT)` | FUNCTION 禁止参数模式 | 审核通过 |
| `PROCEDURE p() GARBAGE TOKEN` | body 不是有效 SQL 语句 | 审核通过 |
| `FUNCTION f(...) RETURNS INT GARBAGE TOKEN` | body 不是有效 SQL 语句 | 审核通过 |
| `TRIGGER tr GARBAGE TOKEN` | 缺失完整触发器头和合法体 | 审核通过 |
| `TRIGGER tr BEFORE INSERT ON t SET ...` | 缺 `FOR EACH ROW` | 审核通过 |
| `TRIGGER tr SOMETIME INSERT ...` | 非法触发时机 | 审核通过 |

以下官方合法 SQL 当前产生 E999：

| 合法形态 | 当前结果 |
|---|---|
| quoted DEFINER + PROCEDURE + IN | SQL 类型误成 SELECT，R051 + E999 |
| `DEFINER=CURRENT_USER` + PROCEDURE + IN | SQL 类型误成 SELECT，R051 + E999 |
| `IF()` 函数与 CASE 表达式位于同一 SET | 被误认存在未闭 IF，E999 |

schema-qualified 过程 `db1.p(IN x INT)` 虽审核通过，但 `created_object_name` 只记录为 `db1`，对象身份也不准确。

### 5.4 真实 API 复现

非法 `CREATE PROCEDURE p_bad SELECT 1;` 经当前隔离服务：

```text
/api/v1/audit/sql          -> 200, CREATE PROCEDURE, passed=true, violations=[]
/api/v1/audit/file         -> 200, 1 result, passed=true, violations=[]
/api/v1/audit/upload       -> 200, 1 result, passed=true, violations=[]
/api/v1/audit/batch-stream -> 200, 1 data frame, passed=true, violations=[]
```

这不是内部字段瑕疵，而是所有用户入口共同把非法 DDL 判为可用。

### 5.5 根因

1. `_routine_structure()` 把参数括号写成可选分支，没有强制 PROCEDURE/FUNCTION 名后紧接 `(`；
2. `_routine_params_ok()` 把空 segment 当合法，并仅用“首 token 是标识符且 token 数≥2”判断整个参数，无法发现缺逗号、非法模式、非法类型；
3. 参数校验没有接收 `kind`，因此 FUNCTION 的 IN/OUT/INOUT 被当合法；
4. `_routine_body_complete()` 只检查构造栈是否归零，不校验 body 是否为有效语句，任意 `GARBAGE TOKEN` 都能通过；
5. `_routine_compat_fill()` 在 sqlglot 失败后仅凭上述弱结构校验清除 E999，形成事实上的宽放行；
6. `_find_routine_head()` 不完整支持 quoted DEFINER、CURRENT_USER、限定名，导致合法例程不进入兼容路径；
7. `_if_is_statement()` 从 IF 向后扫描到分号，只要之后同层出现任意 THEN 就判为 IF 语句；`IF()` 后跟 CASE 的 THEN 会造成误判。

### 5.6 为什么 Q 的 32 项测试漏检

- `test_routine_negative_fails_closed` 只列 7 个指定负例，没有覆盖官方语法的对称边界；
- 参数正例覆盖模式，却没有 FUNCTION 模式禁用、括号必选、逗号/空段和类型消费断言；
- DEFINER、schema-qualified 名称、trigger 正/反向完整头没有测试；
- simple body 只测 SELECT/SET/RETURN，没有随机非法 token；
- 直接调用 checker 的测试无法代替四个真实 API 和浏览器最终结果。

---

## 6. `UAT-O-1632-R7-02`（P1）：DELIMITER 文件仍未统一

### 6.1 复现文件

```sql
DELIMITER $$
CREATE PROCEDURE p_d(IN x INT)
BEGIN
  SET @a = x;
  SET @b = x;
END$$
DELIMITER ;
```

这是客户端提交含内部分号例程的标准文件形态。

### 6.2 当前结果

| 入口 | 实际结果 |
|---|---|
| `RuleChecker._split_sql_file()` | 只得到 1 段，但段尾错误保留 `END$$` |
| `/api/v1/audit/file` | 1 个 CREATE PROCEDURE，E999 |
| `/api/v1/audit/upload` | 1 个 CREATE PROCEDURE，E999 |
| `/api/v1/audit/batch-stream` | 3 个数据结果；第 1 个 E999，后两段伪 SQL 通过 |

Q 新增 `test_split_delimiter_dollar_keeps_routine` 只断言 `len(...) == 1`，没有断言返回 SQL 已剥离 `$$`，也没有真正审核该结果，故测试绿而功能红。

### 6.3 根因

- 文件拆分器在判断是否仍处于 BEGIN 块时，把带尾部分隔符的 `END$$` 原样交给 `routine_construct_open_count()`；词法器无法把它稳定视为 bare END，`in_begin_block` 不归零，语句落入 EOF 兜底并保留 `$$`；
- `/batch-stream` 直接把含 `DELIMITER` 指令的全文交给 `split_sql_statements_for_audit()`；共享内核本身不识别客户端分隔符协议，所以体内分号仍被拆；
- 当前所谓“三入口统一”只统一了一个无 DELIMITER 的样例，没有统一真实 SQL 文件协议。

---

## 7. 给 Q 的照图施工方案

### 7.1 修复 R7-01：收紧例程兼容层

#### A. 例程头必须完整消费

实现 `_parse_routine_header()`，返回明确的 span/游标，不得用“找到 kind 后剩余任意 token 都算 body”：

1. 支持 `CREATE [OR REPLACE] [DEFINER = user]`；
2. DEFINER 支持 `'user'@'host'`、反引号、裸名、`CURRENT_USER`、`CURRENT_USER()`；
3. 支持 `[schema.]object_name`，`created_object_name` 必须保留完整限定名；
4. PROCEDURE/FUNCTION 名后必须紧接参数左括号，右括号必须配平；
5. FUNCTION 必须紧接合法 `RETURNS type`；
6. 只按白名单、按固定 token 数消费 routine characteristics：COMMENT、LANGUAGE SQL、[NOT] DETERMINISTIC、CONTAINS/NO/READS/MODIFIES SQL DATA、SQL SECURITY DEFINER/INVOKER；
7. characteristics 后必须存在 body，且不得遗留未知头部 token。

#### B. 参数解析必须按 kind 和段完整消费

把 `_routine_params_ok(toks, i, n)` 改为接收 `kind`：

- 空参数列表只允许一对相邻括号；一旦出现逗号，每个 segment 必须非空；
- PROCEDURE 每段：可选 IN/OUT/INOUT + 一个参数名 + 一个完整合法类型；
- FUNCTION 每段：参数名 + 类型，遇 IN/OUT/INOUT 立即非法；
- 类型不能只用 token 数判断。建议复用现有数据类型解析，或把类型 span 放入受控的 `CAST(NULL AS <type>)` 解析探针并要求整段完全消费；
- 一个 segment 中出现第二个“参数名+类型”但没有逗号必须失败；
- 增加 schema-qualified、复杂类型 `DECIMAL(10,2)`、字符集/排序规则、UNSIGNED 等正例。

#### C. body 不能只做括号平衡

- 结构栈只负责边界，不能证明 SQL 合法；
- simple body 必须由现有 SQL parser 成功解析，或由严格的 stored-statement grammar 完整消费；
- compound body 中的每个 statement 必须识别为受支持的存储程序语句，未知首 token、残缺 DML、缺 THEN/DO/UNTIL、非法 END 必须失败；
- 推荐优先对 PROCEDURE 参数模式制作**仅用于解析的规范化副本**：只在参数 span 删除 IN/OUT/INOUT，再交 sqlglot 重新解析；raw SQL 不变。只有重解析得到匹配 CREATE 例程 AST 时才能清除 E999；
- 对 sqlglot 仍不支持的函数/复杂体，再走严格 mini-parser，不得仅凭 `stack == []` 放行。

#### D. 修正 IF/CASE 上下文

- 维护“当前是否处于例程 statement 起点”状态；只有语句起点的 IF 才可能压入 IF_STMT；
- `IF (condition) THEN` 在语句起点仍合法；表达式中的 `IF(...)` 在右括号后即结束，不得继续扫描并借用后续 CASE 的 THEN；
- CASE_STMT 与 CASE_EXPR 保持不同栈类型，分别由 `END CASE` 和 bare `END` 关闭。

#### E. TRIGGER 不能继续走 Command 宽放行

对 CREATE TRIGGER 至少完整验证：

```text
[DEFINER] TRIGGER name
{BEFORE|AFTER} {INSERT|UPDATE|DELETE}
ON table FOR EACH ROW [FOLLOWS|PRECEDES other] body
```

缺 `FOR EACH ROW`、非法时机/事件、缺表或任意垃圾体必须 E999；合法复合触发器集中式通过，分布式只由 R030 命中。

### 7.2 修复 R7-02：统一 DELIMITER-aware 脚本拆分

不要让 `_split_sql_file()` 和 `/batch-stream` 各自包一层补丁。建立唯一 `split_audit_script()`，同时返回 `sql/start_line/end_line`：

1. 只在行首、且处于字符串/注释之外时识别 `DELIMITER <token>` 指令；
2. 切换后的分隔符用于寻找整个对象结束位置；体内普通 `;` 不切句；
3. **先剥离当前结束分隔符，再**把候选 SQL 交给构造栈和 parser；`END$$` 必须变成 `END`；
4. `DELIMITER` 指令自身不进入 SQL、不产生审核结果；
5. 恢复 `DELIMITER ;` 后，后续普通 SQL 正常拆分；
6. `/file`、`/upload`、`/batch-stream` 和即时多 SQL 适配层统一调用该函数；
7. 流式数据帧增加只读的 `sql_type` 字段，便于验证三个入口对象类型一致；这是向后兼容的新增字段。

### 7.3 必增测试

#### 语法正反向表驱动测试

每条正例应有对应负例：

- `()` 必选；无参空括号合法；缺括号非法；
- PROCEDURE 的省略模式/IN/OUT/INOUT/混合合法；FUNCTION 三种模式全部非法；
- 前置、尾随、连续逗号非法；缺逗号非法；参数缺名/缺类型非法；
- quoted DEFINER、CURRENT_USER、schema-qualified 名称合法且对象名准确；
- simple SELECT/SET/RETURN 与 compound body 合法；`GARBAGE TOKEN` 非法；
- TRIGGER 完整头正例，以及缺时机/事件/ON/FOR EACH ROW/body 的逐项负例；
- `IF()`、CASE 表达式、`IF()+CASE` 同一表达式、IF statement 各自准确。

所有负例统一断言：集中式含 `E999_SYNTAX_ERROR`，不得 `passed=true`；所有合法集中式正例断言准确 sql_type 且 `violations == []`。

#### DELIMITER 合同测试

对 `$$`、`//` 和自定义双字符分隔符，逐项走：

- `split_audit_script()`：结果文本不得含 DELIMITER 指令或尾部分隔符；
- `RuleChecker.audit_file()`；
- `/api/v1/audit/file`；
- `/api/v1/audit/upload`；
- `/api/v1/audit/batch-stream`。

统一断言：一个例程恰好一个结果、SQL 类型 CREATE PROCEDURE、集中式零违规；例程后跟 SELECT/CREATE TABLE 时数量和行号准确。测试不得只断言 `len == 1`。

#### 真实页面关闭标准

Q 修复后至少在当前提交页面点击：

1. quoted DEFINER + IN + compound IF：集中式通过，类型 CREATE PROCEDURE；
2. `IF()+CASE` 组合：集中式通过；
3. 缺括号过程、FUNCTION(IN...)、空参数段、垃圾 body、缺 FOR EACH ROW 触发器：逐条出现 E999；
4. 文件审核上传 DELIMITER 过程：一个结果、无 E999。

---

## 8. 自动化与回归结果

| 命令 / 测试集 | 结果 |
|---|---|
| `pytest tests/test_routine_audit_r6.py tests/test_rules_v1632.py tests/test_instance_scope_rules.py -q` | **131 passed，3 warnings，0 failed，4.36s** |
| `python tests/rule_audit_materials/verify_rules.py --verbose` | **PASS；total=121，covered=109，metadata=7，exempt=5，failures=0** |
| `pytest tests -q` | **1897 passed，11 warnings，0 failed，402.30s** |
| `T3P_BASE_URL=http://127.0.0.1:8002 pytest tests_3p -q` | **125 passed，1 skipped，2 warnings，0 failed，23.35s** |

三方测试会主动执行错误登录安全用例，结束后首次手工 API 登录命中 IP 级 60 秒窗口并返回 429；等待窗口结束后正常登录，随后全部 API/UI 证据稳定复现。该 429 是既有安全控制按设计生效，不作为产品缺陷，也不影响后续证据有效性。

自动化全绿说明现有断言没有回归；§5/§6 证明断言集合缺少官方语法的对称负例和真实 DELIMITER 结果合同，二者并不矛盾。

---

## 9. 门禁裁决

| 门禁 | 第七轮状态 | O 意见 |
|---|---|---|
| GATE-1 | **Mr.Linsang 已签署通过** | 保持，不重开 |
| GATE-2 | **不通过** | 普通正向例程恢复，但 R7-01/R7-02 两项 P1 独立阻断 |
| GATE-3 | **Mr.Linsang 已签署通过** | MAXVALUE 保持性验证通过，不重开 |
| 生产准出 | **禁止** | 等 Q 修复两项 P1、O 第八轮关闭、Mr.Linsang 完成 GATE-2 签署，并完成目标麒麟 12/0/0 |

---

## 10. 最终裁决

**第七轮复测不通过。**

Q 已修复第六轮最直接的 `IN + IF` 和无 DELIMITER 三入口拆分问题，但新增的例程兼容层不是可靠的失败关闭实现：它把多种官方明确非法 SQL 判为通过，同时仍误拦 quoted DEFINER 等合法语句。标准 DELIMITER 文件也没有真正统一到三个入口。

请 Q 按 §7 分别修复 `UAT-O-1632-R7-01` 和 `UAT-O-1632-R7-02`，补齐对称语法矩阵、真实接口合同和页面验收后，再转 O 第八轮定点复测。

证据索引：`docs/evidence/v1.6.3.2-uat-o-r7/README.md`。
