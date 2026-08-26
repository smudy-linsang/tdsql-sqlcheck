# v1.6.2.2 索引解析修复设计 Rev.J 第十轮深度独立复审报告

| 项目 | 内容 |
|---|---|
| 复审对象 | `DESIGN-v1.6.2.2-索引类型误判与唯一索引注释解析崩溃修复详细设计说明书.md` Rev.J |
| 仓库基线 | `180b090`（`main` / `origin/main`） |
| 复审日期 | 2026-08-26 |
| 复审人 | Codex |
| 复审范围 | DEF-1 假 UNIQUE；DEF-2 UNIQUE 索引注释恢复链；TDSQL 方言、列定义、索引、分区、表尾状态、候选 AST 门禁、测试判据与回归门槛 |
| 代码修改 | **无**。本轮只评审设计并运行独立探针、现有测试 |
| 最终结论 | **Rev.J 不通过，不得按当前方案施工/发布** |

## 1. 结论先行

Rev.J 的方向比 Rev.I 明显正确：统一规划器、主目标门禁、方法上下文、表尾 identity、候选结构门禁，都是应该保留的设计骨架。DEF-1 改读 AST `kind` 的方案也仍可通过。

但 Rev.J 还没有实现其宣称的“按 TDSQL 官方语法验证整条 CREATE TABLE”。独立抽取并原样运行 §3.0c 代码后，确认存在 **5 项 BLOCK、2 项 MAJOR**：

| 编号 | 级别 | 根因 | 直接后果 |
|---|---|---|---|
| BLOCK-J1 | BLOCK | `_consume_data_type()` 和 `DEFAULT` 仍是无类型上下文的通用 token 消费器 | 非法列定义被恢复为 `Create`；官方合法列定义被拒绝 |
| BLOCK-J2 | BLOCK | `SourceFingerprint` 生成了大量信息，但候选门禁只比较定义项种类和列名 | `JSON(1)` 被静默改成 `JSON` 等结构漂移仍可过门禁 |
| BLOCK-J3 | BLOCK | 表尾“有限状态机”没有按声明执行；分号策略把合法单语句也拒绝 | `BROADCAST + PARTITION` 被接纳，`BROADCAST COMMENT` 和普通尾分号反而失败 |
| BLOCK-J4 | BLOCK | 官方 TDSQL 语法清单不完整，并混合了不同内核/产品代际的方言 | 官方选项被误列为 unsupported；新旧二级分区关键字无法正确分流 |
| BLOCK-J5 | BLOCK | 分区函数、值和 option 仍未按 TDSQL 上下文闭合 | `VALUES IN (-'x')`、未举证函数、错序 option 可被恢复 |
| MAJOR-J1 | MAJOR | 验收章节仍保留主干 rank oracle，H 组数量和明细不一致 | “250 例、H90、三版本全过”不可由文档审计 |
| MAJOR-J2 | MAJOR | PRIMARY/UNIQUE/普通索引仍共用过宽解析路径 | 非法 `PRIMARY KEY pk(id)` 被接纳；前后置 `USING` 未共享 seen 状态 |

以下 9 个非法或未经批准的形态，在 sqlglot **29.0.0、30.14.0、30.17.0** 三版上均被 Rev.J 原样代码恢复为 `Create`：

```sql
id RANGE
id NULL
id VARCHAR(1,2,3)
id JSON(1)
id INT DEFAULT ()
id INT DEFAULT foo
PRIMARY KEY pk(id)
VALUES IN (-'x')
BROADCAST PARTITION BY RANGE(id) (...)
```

这不是依赖漂移，也不是只影响报错文案；它会把 TDSQL 不应接受的 DDL 送入 119 条审核规则，并可能让原本的 E999、R054 或其他规则结论消失，属于明确的次生灾害风险。

## 2. 本轮评审方法与证据

### 2.1 设计代码原样执行

从 Rev.J §3.0c 的 Python 代码块原样抽取 `_plan_recovery()`、各消费器和 `_validate_recovery_candidate()`，只注入其声明的 `sqlglot`、`exp`、`TokenType` 依赖；未改写方案逻辑。

独立矩阵覆盖：

- 数据类型名、参数个数、参数零值与多 token 类型；
- DEFAULT 单 token、带符号字面量、平衡括号垃圾和表达式；
- 列约束官方正例与重复/冲突近邻；
- PRIMARY/UNIQUE/普通索引的名称、key part、前后置 `USING`；
- 分区方法 × 函数 × 值类型 × option 顺序；
- 7 类表尾 atom 的 49 个有序二元组合；
- 单尾分号、多语句边界；
- 两份生产 fixture；
- sqlglot 29.0.0、30.14.0、30.17.0 三版本一致性。

### 2.2 当前仓库回归

| 测试 | 结果 |
|---|---:|
| `test_parser_tdsql_dialect_fallback.py` + `test_r077_r054_tdsql_syntax.py` + `test_r061_index_name_quoting.py` | **71 passed，3 warnings** |
| `python -m pytest -q` | **1384 passed，10 warnings，0 failed** |

上述结果只证明当前 `main` 基线正常。A 本次只提交了设计文档，Rev.J 的 250 个拟新增测试及产品实现尚不在仓库中，因此现有全绿不能作为 Rev.J 安全性的证据。

### 2.3 两个原始目标的复核

- DEF-1：`IndexColumnConstraint.kind` 白名单映射保持成立；普通索引名/列名含 `unique` 不再参与类型判定。SPATIAL 继续映射 NORMAL，按既定用户决策不重开。
- DEF-2：不带终止分号的 `report_6311_biz_tx_log.sql` 能生成计划并通过候选门禁；同一文本只追加一个正常的 `;` 后，`_plan_recovery()` 立即返回 `None`。因此“目标 fixture 通过”尚不能证明真实输入体验完整。

## 3. 上一轮全域问题闭环情况

| 上轮问题 | Rev.J 状态 | 本轮判断 |
|---|---|---|
| BLOCK-X1：主干作为非法 oracle | **未闭环** | §5.22 改成规范分类，但 §7.1、§3.3 自验证、附录与风险表仍多次要求从主干取期望并使用 rank |
| BLOCK-X2：列定义黑箱 | **未闭环** | 新增了消费者，但消费者不是按类型/约束的有限语法；产生新的吞错与误拒 |
| BLOCK-X3：辅助掩码独立触发 | **已闭环** | `primary` 为空即返回 `None`，本轮探针通过 |
| BLOCK-X4：一级分片缺方法上下文 | **基本闭环** | RANGE/LIST 操作符绑定、HASH 禁止定义表已落到代码；仍需纳入版本化方言表 |
| BLOCK-X5：二级分区结构 | **部分闭环** | 单个 `PARTITION BY`、Range/List 与负数已处理；函数、值、option 和候选结构仍有空洞 |
| BLOCK-X6：表尾有限状态机 | **未闭环** | phase 变量存在，但多条未经批准的跨阶段边仍可达 |
| BLOCK-X7：表选项官方清单 | **未闭环** | 官方 `ROW_FORMAT`、`STATS_PERSISTENT` 被错误移出白名单 |
| MAJOR-X1：结构指纹 | **未闭环** | 指纹多数未参与候选比较，`tail` 完全未使用 |
| MAJOR-X2：索引值域/次数 | **部分闭环** | 正整数前缀和后置 option seen 已完成；前后置 `USING`、PRIMARY 名称仍未闭合 |
| MAJOR-X3：文档/计数/死代码 | **未闭环** | 主代码块重复定义已消除，但验收真源和旧判据仍冲突 |

## 4. 给 A 的 TDSQL 官方语法离线摘要

### 4.1 证据优先级

本项目不能把 “MySQL 能解析” 或 “sqlglot 返回 Create” 当作 TDSQL 合法性证明。建议固定为：

1. 目标内网实例的版本号、`SHOW CREATE TABLE` 和实机建表结果；
2. 对应产品代际的腾讯云 TDSQL 官方文档；
3. 用户已确认的生产契约与冻结决策；
4. 仅当 TDSQL 官方明确声明“与 MySQL 相同/类似”时，再用对应内核版本的 MySQL 官方手册补足参数边界；
5. sqlglot 只负责词法和候选 AST，不负责裁定 TDSQL 合规性。

### 4.2 官方建表页明确支持的形态

来源：[腾讯云 TDSQL MySQL 版—建表](https://cloud.tencent.com/document/product/557/8767)，页面标注更新时间 2024-11-29。

离线要点：

- 公开页把一级 Hash 写为 `shardkey=column_name`，并要求放在语句尾部；
- 一级 Range/List 使用 `TDSQL_DISTRIBUTED BY range|list(column_name)`；页面同时提醒某些 5.7 内核不支持该形态；
- 广播表公开示例使用 `shardkey=noshardkey_allset`；
- key part 是列名、可选正整数前缀、可选 ASC/DESC；index type 只列 BTREE；index comment 属正式 index option；
- 列定义明示包含 NULL 性、DEFAULT、AUTO_INCREMENT、UNIQUE/PRIMARY、COMMENT、COLLATE、`COLUMN_FORMAT`、`ENGINE_ATTRIBUTE`；
- 本地表选项明示包含：AUTO_INCREMENT、CHARACTER SET、COLLATE、COMMENT、ENGINE、`ROW_FORMAT`、`STATS_AUTO_RECALC`、`STATS_PERSISTENT`、`STATS_SAMPLE_PAGES`；
- `ROW_FORMAT` 的官方值域是 DEFAULT、DYNAMIC、FIXED、COMPRESSED、REDUNDANT、COMPACT；两个 STATS 开关的值域是 DEFAULT/0/1；
- 分区定义允许 `[STORAGE] ENGINE`，其后可有 COMMENT。

因此 Rev.J 把 `ROW_FORMAT` 和 `STATS_PERSISTENT` 归入 `unsupported_unproven` 是事实错误；拒绝列级 `COLUMN_FORMAT`、`ENGINE_ATTRIBUTE` 也必须登记为官方合法语法的已知假阴性，或直接实现。

### 4.3 官方二级分区页明确支持的形态

来源：[腾讯云 TDSQL MySQL 版—二级分区](https://intl.cloud.tencent.com/zh/document/product/1042/33361)，页面标注更新时间 2024-01-06。

离线要点：

- 二级分区公开说明只列 Range 和 List；
- 官方示例存在 `shardkey=... PARTITION BY LIST(...)`；
- 也存在先 `PARTITION BY LIST(...)`、后 `TDSQL_DISTRIBUTED BY RANGE(...)` 的示例；
- 日期分区函数公开明示的是 year、month、day；没有在该页明示 DAYOFMONTH、TO_DAYS、TO_SECONDS、UNIX_TIMESTAMP；
- 主键和唯一索引需要包含分区键。

所以 Rev.J 可以支持 YEAR/MONTH/DAY，但其余四个函数在获得目标实例证据前应归入 `unsupported_unproven`，不能直接进入可信恢复域。

### 4.4 官方兼容性页对字面量和数据类型的约束

来源：[腾讯云 TDSQL MySQL 版—兼容性](https://intl.cloud.tencent.com/zh/document/product/1042/38180)，页面标注更新时间 2024-01-06。

离线要点：

- TDSQL 支持 MySQL 的字符串、数值、日期时间、十六进制、bit、布尔和 NULL 字面量；
- 数值字面量可有 `+` 或 `-`，也可用小数和科学计数；
- 页面声明支持 MySQL 的数值、字符串、日期时间、空间和 JSON 数据类型；
- 页面列出 FLOAT/REAL/DOUBLE PRECISION 的 `(M,D)` 与 DECIMAL/NUMERIC 的 `(M,D)` 形态。

在 TDSQL 已明确声明继承 MySQL 类型语义的前提下，可用对应 MySQL 5.7 手册补足数值边界：例如 [DECIMAL/NUMERIC](https://dev.mysql.com/doc/refman/5.7/en/fixed-point-types.html) 允许 scale 为 0，[TIME/DATETIME/TIMESTAMP](https://dev.mysql.com/doc/refman/5.7/en/date-and-time-type-syntax.html) 的 fsp 允许 0～6。故 Rev.J 的“所有类型参数必须为正整数”会错误拒绝 `DECIMAL(10,0)`、`DATETIME(0)`、`TIME(0)`、`TIMESTAMP(0)`。

### 4.5 当前官方资料显示存在产品代际差异

腾讯云 2026-02-05 更新的 [DTS 同步使用说明](https://cloud.tencent.com/document/product/571/105000) 把 TDSQL 分区关键语法分为：

- 一级 Hash：shardkey；一级 Range/List：TDSQL_DISTRIBUTED；
- 旧二级分区：一级声明加 `PARTITION BY RANGE/LIST`；
- 新二级分区：`TDSQL_DISTRIBUTED BY HASH` 加 `TDSQL_PARTITION BY RANGE/LIST`。

这与较早建表页不完全相同。用户已经用目标实例事实确认 `TDSQL_DISTRIBUTED BY HASH(cust_no)` 合法，本报告**不推翻该生产契约**；但 A 必须把它标为目标产品/版本证据，不能引用较早公开页声称它是所有 TDSQL 内核的通用语法。

同理：bare `BROADCAST`、多列 shardkey、`TDSQL_DISTRIBUTED BY HASH ... PARTITION BY` 若继续支持，应分别记录目标实例版本或用户冻结决策；不得和公开页的 `shardkey=noshardkey_allset`、新 `TDSQL_PARTITION BY` 混成一个无版本白名单。

### 4.6 本轮不重开的既定决策

- `TDSQL_DISTRIBUTED BY HASH(cust_no)`：按用户和目标实例事实继续判合法；
- `shardkey=noshardkey_allset`：按 TDSQL 官方广播表语法继续判合法；
- KFN-1 `VALUES LESS THAN MAXVALUE`：保持本版已批准的已知假阴性；
- ADJ-6：保持用户批准的具名 characterization，但只能是精确例外，不能扩成任意 BROADCAST 组合；
- NG-10 / ADJ-11：`CONSTRAINT x UNIQUE` 本版不修；官方虽列出该形态，本报告只要求在已知边界中如实记账；
- sqlglot 词法器路线和 SPATIAL→NORMAL：保持用户决策。

## 5. BLOCK-J1：列定义消费者仍会吞掉真实语法错误

### 5.1 发生原因

`_consume_data_type()` 的类型名判据实际是：只要 token 不是 STRING/IDENTIFIER 就可以当数据类型；参数判据则是：除 ENUM/SET 外，任意类型都可带任意多个正整数。

这不是数据类型语法，而是两个大桶：

```text
类型名 = 几乎任意关键字 token
类型参数 = 任意长度的正整数列表
```

因此它既不知道 `VARCHAR` 只能有一个长度，也不知道 `JSON` 不应带长度，更不知道 `RANGE`、`LIST`、`NULL` 根本不是 TDSQL 列类型。

`DEFAULT` 的问题更严重：

- 非括号形式只检查“后面还有一个 token”，没有值类别白名单；
- 括号形式只做括号配平，不检查括号内部表达式；
- `ON UPDATE` 固定跳过三个 token，却不验证第三个 token 的语义；
- 数据类型、默认值、列属性之间没有类型上下文。

### 5.2 三版本稳定复现

以下均为“再加一个合法 UNIQUE COMMENT 触发主目标”的完整 CREATE TABLE：

| 列定义 | Rev.J 计划/候选 | 问题 |
|---|---|---|
| `id RANGE` | `ACCEPT:Create` | 非数据类型关键字被当类型 |
| `id NULL` | `ACCEPT:Create` | NULL 被当数据类型而不是约束/字面量 |
| `id VARCHAR(1,2,3)` | `ACCEPT:Create` | 类型参数个数不受类型约束 |
| `id INT(1,2)` | `ACCEPT:Create` | 同上 |
| `id DATE(1)` | `ACCEPT:Create` | 无此 TDSQL/MySQL 类型形态 |
| `id JSON(1)` | `ACCEPT:Create`，候选变为 `JSON` | sqlglot 静默丢参数，门禁未发现 |
| `id DECIMAL(10,2,1)` | `ACCEPT:Create` | precision/scale 多出第三参数 |
| `id INT DEFAULT foo` | `ACCEPT:Create` | 任意裸标识符被当默认值 |
| `id INT DEFAULT ()` | `ACCEPT:Create` | 空表达式仅因括号闭合而通过 |
| `id INT DEFAULT (,)` | `ACCEPT:Create` | 同上 |
| `id INT DEFAULT (+)` | `ACCEPT:Create` | 同上 |
| `id INT DEFAULT (SELECT 1)` | `ACCEPT:Create` | 任意括号内容进入可信 AST |

反向误拒同样存在：

| 官方/兼容性支持的形态 | Rev.J |
|---|---|
| `DECIMAL(10,0)` | `REJECT_PLAN` |
| `DATETIME(0)` / `TIME(0)` / `TIMESTAMP(0)` | `REJECT_PLAN` |
| `DEFAULT -1` / `DEFAULT +1` | `REJECT_PLAN` |
| `COLUMN_FORMAT DYNAMIC` | `REJECT_PLAN` |
| `ENGINE_ATTRIBUTE='...'` | `REJECT_PLAN` |

### 5.3 必须修改的处理机制

不要继续给通用消费者追加 if。改成由目标 TDSQL 版本生成的数据类型规范表，每个类型至少声明：

```text
canonical_name
token/name 组合（含 DOUBLE PRECISION 等多 token 类型）
参数模式：NONE / M / M,D / FSP / ENUM_SET
每个参数是否允许 0、上下界、参数间关系
允许的 SIGNED/UNSIGNED/ZEROFILL/BINARY 等属性
允许的列约束和约束值消费器
```

最低硬要求：

1. 类型名使用显式白名单，拒绝 RANGE/LIST/NULL 等非类型 token；
2. VARCHAR/CHAR、DECIMAL/NUMERIC、FLOAT/DOUBLE、时间 fsp、ENUM/SET 分开建模；
3. `D` 和 `fsp` 允许合法 0，不能复用索引前缀的“正整数”谓词；
4. DEFAULT 使用目标内核允许的字面量/时间函数消费者；`()`、`(,)`、任意裸标识符和任意配平表达式不得通过；
5. ON UPDATE 只接受目标版本允许的时间表达式并逐 token 验证；
6. 实现 `COLUMN_FORMAT`、`ENGINE_ATTRIBUTE`，或登记为 `pos_known`；不能称其非法；
7. 对未覆盖但官方合法的数据类型/属性失败关闭，并形成有编号的已知假阴性清单。

## 6. BLOCK-J2：SourceFingerprint 目前只是“生成了”，没有完成“守恒”

### 6.1 发生原因

规划阶段确实记录了：

- 列类型形态与约束 identity；
- 索引种类、名称、key part、option；
- 表选项、二级分区、一级分布声明。

但 `_validate_recovery_candidate()` 实际只比较：

- Create/Table/表名；
- 定义项数量；
- 每项是 COL 还是 IDX；
- 列名；
- 原文出现 PARTITION BY 时，候选恰有一个 `PartitionBy*`。

`fingerprint.tail` 没有被读取；列类型/参数/约束没有比较；索引种类/名称/key part 没有比较；分区方法、表达式、定义和值没有比较。

### 6.2 可复现后果

`id JSON(1)` 在原文指纹里仍是 `JSON(1)`，sqlglot 候选却静默变成 `JSON`；因为门禁只看“有类型 + 列名 id”，最终返回 True。

同理，非法命名 PRIMARY、索引 kind/name/key part 变化、表选项被丢弃或重解释、分区方法/定义漂移，都没有由门禁证明守恒。

### 6.3 必须修改

候选指纹至少逐项比较：

| 结构 | 必比字段 |
|---|---|
| 列 | 名称、规范类型、参数、signedness、NULL 性、DEFAULT 类别、AUTO_INCREMENT、KEYNESS、COLLATE；被批准忽略的 COMMENT 单独列入 mask |
| 索引 | PRIMARY/UNIQUE/NORMAL/FULLTEXT/SPATIAL、名称、key 列、prefix、ASC/DESC、USING；被批准移除的 index COMMENT 单独列入 mask |
| 本地表选项 | identity、规范值、出现次数与顺序阶段 |
| 二级分区 | 数量、方法、表达式、定义数、定义名、VALUES 操作符和值结构；仅 ENGINE/COMMENT 的已批准 mask 可排除 |
| 一级方言 | raw token 指纹必须完整保留给规则侧；不能声称被置空后的 AST 自己保留了它 |

无法从 sqlglot AST 稳定提取的字段应导致该形态失败关闭，或改用经过验证的 raw token 指纹；不能降级成“AST 有一个节点就算相同”。

## 7. BLOCK-J3：表尾状态机与分号边界不成立

### 7.1 单尾分号被误当多语句

Rev.J 看到任意 `SEMICOLON` 就返回 `None`。这能阻止多语句跨界，却也拒绝最常见的单条 DDL 终止分号。

实测：

| 输入 | Rev.J |
|---|---|
| `CREATE TABLE ... UNIQUE ... COMMENT 'x'` | 计划成功 |
| 同一 SQL 末尾追加 `;` | `REJECT_PLAN` |

正确边界应是：允许 **0 或 1 个且仅位于 EOF 前的终止分号**；出现分号后仍有真实 token，或出现第二个分号时失败关闭。

### 7.2 phase 变量没有限制所有迁移

49 个二元组合矩阵确认以下未经批准的路径可达：

| 路径 | Rev.J | 问题 |
|---|---|---|
| `shardkey=id ENGINE=InnoDB` | ACCEPT | 代码仍处 phase 0；与文档 `LOCAL_OPTIONS* -> shardkey` 和官方“shardkey 放最后”冲突 |
| `PARTITION BY ... BROADCAST` | ACCEPT | 未有官方/项目证据 |
| `BROADCAST PARTITION BY ...` | ACCEPT | 广播表与二级分区被任意组合 |
| `PARTITION BY ... TDSQL_DISTRIBUTED BY HASH` | ACCEPT | 需按目标版本区分 old/new 关键字，不能无条件开放 |
| `TDSQL_DISTRIBUTED BY HASH/RANGE/LIST ... PARTITION BY` | ACCEPT | RANGE/LIST 有官方/DTS 依据；HASH 仅有 PROJECT_ACCEPTED，需精确边 |

与此同时，文档 Z2 和历史生产语料要求保留的 `BROADCAST COMMENT='x'` 被代码拒绝，因为 BROADCAST 把 phase 提到终结态后 COMMENT 被视为回退。

这证明当前实现不是有限状态机，而是“部分分支看 phase、部分分支无视 phase”。

### 7.3 必须修改

按带 provenance 的显式迁移表实现，不要在大 while 中分散写例外。atom 至少拆为：

```text
LOCAL_OPTION(name)
SHARDKEY
PARTITION_BY
TDSQL_PARTITION_BY
TDSQL_DISTRIBUTED(method)
BROADCAST
TERMINAL_SEMICOLON
EOF
```

硬性门禁：

1. shardkey 一旦出现，本地 option 阶段结束；仅开放“官方二级分区”或精确的 ADJ-6 后继；
2. BROADCAST 与 PARTITION 的两个方向默认都拒绝；
3. ADJ-6 只允许用户批准的确切 token 序列，不得用 `seen_dist in (...)` 泛化；
4. 若 `BROADCAST COMMENT` 确有生产契约，为它建立单独具名 transition，并记录 DDL/版本；否则把表注释放到 BROADCAST 前；
5. `TDSQL_DISTRIBUTED HASH + PARTITION BY` 与 `... + TDSQL_PARTITION BY` 分成两个版本 profile；
6. 由状态迁移表自动生成每条合法边和每条非法近邻测试。

## 8. BLOCK-J4：TDSQL 官方白名单和版本画像不准确

### 8.1 表选项误分类

Rev.J `_TBL_OPT_SPEC` 缺少官方 `ROW_FORMAT` 与 `STATS_PERSISTENT`，§5.23.4 还明确把 ROW_FORMAT 移到 `unsupported_unproven`。这不是保守失败关闭，而是官方取证错误。

最小改法：

- 加入 ROW_FORMAT，严格枚举六个官方值；
- 加入 STATS_PERSISTENT，严格枚举 DEFAULT/0/1；
- 保留 STATS_AUTO_RECALC、STATS_SAMPLE_PAGES；
- CHECKSUM、AVG_ROW_LENGTH 等无 TDSQL/目标实例证据的项目继续失败关闭；
- ENGINE/字符集/排序规则的 value token 是否允许单引号，须按目标实例核验，不要把 `_OPT_NAMEY` 当统一答案。

### 8.2 新旧方言混为一体

公开建表页、二级分区页、2026 DTS 页显示至少存在“传统 shardkey/Partition By”和“新 TDSQL_DISTRIBUTED/TDSQL_PARTITION”两代形态。当前 `_TDSQL_SHARD_METHODS` + 单一 `_consume_secondary_partition()` 不能表达该差异。

必须引入版本化 profile，例如：

```text
profile_id
kernel/product version evidence
hash declaration syntax
range/list declaration syntax
secondary partition keyword
allowed clause orders
broadcast syntax
multi-column shardkey capability
```

目标实例暂不能自动识别 profile 时，应由实例配置显式选择，或只取各 profile 的安全交集；不能自动并集放行。

## 9. BLOCK-J5：分区内部结构仍有过宽与过窄两面

### 9.1 未举证函数被直接放行

TDSQL 二级分区官方页明示 year/month/day；Rev.J 却直接增加 DAYOFMONTH、TO_DAYS、TO_SECONDS、UNIX_TIMESTAMP。三版本实测这七个函数全部能进入 `Create`。

此外函数参数调用 `_consume_ident_list()`，理论上允许多个参数；即使当前 sqlglot 对个别多参数函数恰好拒绝，也不满足 token 层失败关闭要求。

改法：函数白名单先收为 YEAR/MONTH/DAY，参数必须恰好一个列标识符。其他函数只有目标实例实测通过后才能进入相应 profile。

### 9.2 负号可修饰字符串

当前逻辑先可选消费 DASH，再统一接受 NUMBER 或 STRING，所以 `VALUES IN (-'x')` 被恢复为 `Create`。

改法：

- 符号只可进入 numeric literal 分支；
- STRING 分支不得带符号；
- 是否接受 `+NUMBER`、十六进制、bit、布尔、NULL，应按 TDSQL 分区值规则而不是通用 literal 页机械放行；
- 值数量、类型与 RANGE/LIST、普通 expr/COLUMNS profile 绑定。

### 9.3 partition option 顺序与 STORAGE

官方语法允许 `[STORAGE] ENGINE` 后接 COMMENT。Rev.J：

- 拒绝 `STORAGE ENGINE=InnoDB`；
- 接受 `COMMENT='x' ENGINE=InnoDB` 的反向顺序；
- 对 ENGINE/COMMENT 都做辅助掩码，候选 AST 无法替门禁发现错序。

改法是按官方序列建小状态机：可选 STORAGE → ENGINE 至多一次 → COMMENT 至多一次；若目标实例实测允许其他顺序，再进入对应 profile。

## 10. MAJOR-J1：测试真源和 oracle 仍未闭环

### 10.1 规范分类与主干 rank 同时存在

§5.22 正确地提出 `pos/neg/pos_known/unsupported_unproven/characterization_user_decision`，但以下位置仍要求从主干生成期望：

- §3.3 自验证说明；
- §5.19 与 §7.1 H 组说明；
- 风险表；
- 附录 B 第 13 条。

BLOCK-X1 因此没有真正关闭。正确规则应是：

```text
expected_by_tdsql_spec / target_contract = 唯一 pass/fail oracle
baseline_observation = 仅用于说明行为变化，不参与判定
```

### 10.2 H 组明细与总计冲突

§7.1 标题写 81 例；表中 H0/H1/H7/H2/H2b/H3/H4/H4b/H4c/H5/H6 相加为 **109**；总计式则写 H90，并把 H6 改成 9、另加表中没有的 H6b 8，同时未计 H0/H7。故“250 例”目前无法审计。

此外文档称 `h_cases.py` 是唯一真源，但仓库不存在该文件；拟新增 `test_parser_index_type_and_uk_comment.py` 也尚未提交。

### 10.3 必须修改

1. 在设计附录直接给出每个参数化 case 的稳定 ID、SQL/构造器、分类、规范依据、expected；
2. 同一 case 只出现一次，分组汇总由代码计算；
3. `pytest --collect-only -q` 的实际收集数才是计数证据；
4. `neg` 断言 token planner 必须拒绝，不能只依赖候选 parser/gate 恰好拒绝；
5. 主干结果只写入 `baseline_observation`；
6. 当前主干实测是 1384 passed，实施后的准出门槛应写“原 1384 全通过 + 新增实际收集数全通过”，不要硬编码旧环境的 1355 passed / 29 skipped 分布。

## 11. MAJOR-J2：索引定义仍需按 kind 分开建模

### 11.1 非法 PRIMARY 名称被接受

TDSQL 官方形态是可选 CONSTRAINT symbol 在 PRIMARY 前，而不是 `PRIMARY KEY` 后跟索引名。Rev.J 的通用逻辑却会在 PRIMARY 后无条件尝试消费 `iname`，因此：

```sql
PRIMARY KEY pk(id)
```

在三个 sqlglot 版本上均被恢复为 `Create`。

### 11.2 前后置 USING 没有共享次数状态

代码先在 key list 前单独消费一次 `USING BTREE`，随后 `_consume_index_options()` 又新建空 seen，因此 token planner 会批准前后各一个 USING。当前 sqlglot 恰好在候选阶段拒绝，不代表 token 语法已闭合。

### 11.3 官方 PRIMARY COMMENT 仍是未记账假阴性

官方 index option 适用于 PRIMARY/UNIQUE/普通索引；sqlglot 30.x 对 PRIMARY COMMENT 也会 ParseError。Rev.J 只把“定义项起点即 UNIQUE”的 COMMENT 记为主目标，因此 PRIMARY COMMENT 仍不能恢复。NG-10 只冻结 CONSTRAINT UNIQUE，不覆盖 PRIMARY COMMENT。

### 11.4 必须修改

- PRIMARY、UNIQUE、普通 INDEX、FULLTEXT/SPATIAL 使用各自 grammar 分支；
- PRIMARY 不得在 `PRIMARY KEY` 后消费 index name；若支持 CONSTRAINT symbol，只能在官方位置；本版不支持时显式失败关闭；
- 前置和后置 index_type 共用同一个 `seen_using`；
- 候选门禁比较 index kind/name/key parts/prefix/order/using；
- PRIMARY COMMENT 要么纳入同一安全恢复机制，要么登记 `pos_known` 并加入回归，不得遗漏。

## 12. 必须新增的完整测试域

本轮不建议再增加零散 H 编号。应按语法生成器形成以下独立笛卡尔积：

| 域 | 必测维度 |
|---|---|
| 数据类型 | 每个官方类型 × 合法参数形态 × 零/边界/越界 × 多参/缺参/多 token 近邻 |
| DEFAULT | 字符串、正负数、小数、科学计数、hex、bit、布尔、NULL、时间函数、空/逗号/运算符/SELECT/任意标识符 |
| 列约束 | 每个官方约束 × 合法值 × 重复 × 冲突 × 次序 × 类型适用性 |
| 索引 | kind × 有无名称 × key part 数量 × prefix × ASC/DESC × USING 前后位置 × COMMENT × 重复 option |
| 一级分布 | profile × HASH/RANGE/LIST/BROADCAST × key 形态 × 定义表方法/操作符 |
| 二级分区 | profile × 关键字 × RANGE/LIST × 函数 × 参数数 × VALUES 类型 × definition option |
| 表尾状态 | 所有 atom 的有序二元组合 + 每条合法路径的三元扩展 + 每条非法邻边 |
| 语句边界 | 无分号、一个尾分号、尾分号后注释、双分号、分号后第二语句、字符串内分号 |
| AST 守恒 | 对每个未 mask 字段做原文指纹与候选指纹精确相等断言 |
| 生产回放 | 两份 fixture 原文、追加尾分号版、目标 TDSQL_DISTRIBUTED HASH、广播表、每个 profile 官方示例 |

必须把本报告已复现的全部样例纳入永久回归，尤其是 `JSON(1)`、`DEFAULT ()`、`DEFAULT foo`、`PRIMARY KEY pk`、`-'x'`、BROADCAST+PARTITION、官方选项和尾分号。这些不是等价重复，而是分别锁定类型名、参数 arity、默认值语法、索引 kind、值符号、状态迁移、白名单和语句边界。

## 13. 修订后的准出门槛

Rev.K 至少同时满足：

1. 本报告 5 个 BLOCK、2 个 MAJOR 全部逐项关闭；
2. Rev.J 原样代码的 9 个 `ACCEPT:Create` 反例全部在 **planner 层**失败关闭；
3. 官方正例 `DECIMAL(10,0)`、时间 fsp 0、ROW_FORMAT、STATS_PERSISTENT、COLUMN_FORMAT、ENGINE_ATTRIBUTE、STORAGE ENGINE、单尾分号按设计目标恢复；不做的必须进入具名 `pos_known`；
4. TDSQL profile 表写明目标实例版本与证据，用户已确认的 HASH/广播语法不得回归；
5. 结构指纹逐字段比较，不再出现“指纹已记录但门禁不用”；
6. H/总测试数量由已提交参数表自动计算，全文只保留一个数字真源；
7. 三个 sqlglot 版本专项结果逐 case 相同；发布 pin 仍精确锁定；
8. 当前 1384 个既有测试全部通过，新增专项零 skip；
9. 两份生产 fixture 规则集合精确相等，197 条语料只允许两个目标变化，14 表零漂移；
10. 对带和不带尾分号的两份目标 DDL 分别做端到端规则集合断言。

## 14. 最终评审意见

### 14.1 可以保留

- DEF-1 的 AST kind 白名单映射；
- 单一 `_plan_recovery()` 架构；
- 主目标/辅助 span 分离；
- 等长置空和逐字符 span 门禁；
- 分片/分区方法上下文；
- option identity 与次数跟踪的方向；
- sqlglot 精确 pin 与多版本对照；
- 用户已批准的冻结项。

### 14.2 必须推倒重做的局部

- 数据类型与 DEFAULT 消费器；
- 候选 SourceFingerprint 比较器；
- 表尾迁移表；
- 分区 function/value/option 子状态机；
- TDSQL 版本 profile 和官方选项清单；
- H 组/总用例的规范 oracle 与唯一计数源。

### 14.3 放行结论

**不通过。**

Rev.J 不能进入开发施工，因为当前方案在拟锁定依赖上会稳定地把多类非法 TDSQL 恢复为可信 `Create`，同时拒绝多类官方合法语法和正常尾分号。这两类问题分别会造成审核漏报/误放行与生产误报，已经满足“可能产生次生灾害”的阻断条件。

A 下一版不应继续围绕本文样例追加 if；应按“版本化 TDSQL grammar profile → token parser → 完整 SourceFingerprint → 候选逐字段守恒 → 规范化 case generator”的链条整体收口。
