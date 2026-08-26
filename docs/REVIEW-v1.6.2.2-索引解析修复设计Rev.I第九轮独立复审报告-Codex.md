# v1.6.2.2 索引解析修复设计 Rev.I 第九轮独立复审报告

| 项目 | 内容 |
|---|---|
| 复审对象 | `DESIGN-v1.6.2.2-索引类型误判与唯一索引注释解析崩溃修复详细设计说明书.md` Rev.I |
| 复审基线 | `c721bec`（`main` / `origin/main`） |
| 复审日期 | 2026-08-26 |
| 复审人 | Codex（独立复审） |
| 复审范围 | 方案与伪代码，不修改产品代码 |
| 总体结论 | **不通过；Rev.I 暂不具备进入开发条件，须形成 Rev.J 后再审** |

## 1. 结论摘要

Rev.I 的方向较 Rev.H 明显收敛：统一恢复规划器、表尾全量扫描、定义项计数、候选 AST 门禁、`USING BTREE` 白名单及 `sqlglot==30.14.0` 精确锁定均是有效改进。上一轮明确提出的 `ENGINE=123`、空普通索引、重复逗号、孤立 `DEFAULT`、`RANGE(,)`、`USING HASH` 等反例，本轮用 Rev.I 文档中的原样代码独立执行后均已失败关闭。

但 Rev.I 的核心安全主张是“整条 `CREATE TABLE` 都按 TDSQL 官方语法验证通过”，实际实现仍只是对部分语法岛做白名单消费：

1. 列定义只检查“列名后还有一个 token”，没有消费数据类型及列约束；
2. 分区值、分区方法、分区子句次数及候选 AST 的分区结构没有精确守恒；
3. 表选项白名单与腾讯云 TDSQL 官方建表语法不一致。

上述空洞均位于 `E999 -> Create` 恢复链上，能够把本应保留的语法错误变为可信 AST，并继续触发 119 条规则。因此它们不是文档瑕疵，而是可能制造 SQL 审核漏报、误报的次生灾害。

本轮结论为：

- **3 个 BLOCK**：列定义未完整验证、分区语法与结构门禁不完备、表选项白名单偏离 TDSQL 官方语法；
- **2 个 MAJOR**：测试计数与验收证据不可核验、施工说明仍残留多套过期实现；
- KFN-1（`VALUES LESS THAN MAXVALUE` 本版本保留已知假阴性）是用户已批准决策，本轮**接受且不重新争论**；
- 用户已批准关闭或冻结的其他历史决策，本轮亦未作为问题重提。

## 2. 独立复审方法与证据

### 2.1 官方判据

本轮按照项目约定的判据优先级，首先使用 TDSQL 官方语法，而不是把 MySQL 或 sqlglot 当成最终合规判据：

- [腾讯云 TDSQL MySQL 版建表语法](https://cloud.tencent.com/document/product/557/8767)：给出 `column_definition`、`key_part`、`USING {BTREE}`、`Local_table_option`、`partition_options` 和 `partition_definition`；
- [腾讯云 TDSQL MySQL 版二级分区](https://intl.cloud.tencent.com/zh/document/product/1042/33361)：明确二级分区支持 Range、List，且建表语法与 MySQL 分区语法类似；
- [MySQL 8.0 分区限制](https://dev.mysql.com/doc/refman/8.0/en/partitioning-limitations.html)：仅用于 TDSQL 官方文档声明“与 MySQL 分区语法类似”后的补充交叉验证；官方示例确认负数分区边界在相应 SQL mode/类型语义下可合法建表。MySQL 资料没有覆盖或推翻任何 TDSQL 专有语法。

### 2.2 代码级验证

从 Rev.I 文档代码块中原样提取 `_plan_recovery()`、全部消费器、`_blank_spans()` 和 `_validate_recovery_candidate()`，在隔离的 `sqlglot 30.14.0` 环境中执行以下完整路径：

```text
原始 SQL
  -> _plan_recovery()
  -> 等长掩码 uq_spans + dialect_spans + mask_spans
  -> sqlglot.parse_one(..., read="mysql")
  -> _validate_recovery_candidate()
```

同时用当前 `RuleChecker` 对原始 SQL 取基线，确认本文列出的穿透样例在现行主干均包含 `E999_SYNTAX_ERROR`。因此，Rev.I 对这些样例返回 `ACCEPT:Create` 时，确实发生了 `E999 -> Create` 的结论跃迁，而不是单纯 AST 展示差异。

### 2.3 回归验证

执行：

```powershell
py -m pytest -q
```

结果：

```text
1384 passed, 10 warnings in 272.45s
```

测试环境为 Python 3.14.4、当前环境 sqlglot 30.12.0。Rev.I 伪代码的定点对抗验证另在其计划精确锁定的 sqlglot 30.14.0 下完成。

该结果证明当前仓库回归基线稳定，但 Rev.I 尚未落代码，不能用这 1384 个通过项证明 Rev.I 的恢复边界安全。

## 3. 已确认关闭的上一轮问题

| 验证项 | Rev.I 独立结果 | 判定 |
|---|---|---|
| 合法 `UNIQUE KEY ... USING BTREE COMMENT` | `ACCEPT:Create` | 通过 |
| `UNIQUE KEY ... USING HASH COMMENT` | `REJECT_PLAN` | 通过 |
| `ENGINE=123` | `REJECT_PLAN` | 通过 |
| 空普通索引 `KEY k ()` | `REJECT_PLAN` | 通过 |
| 定义列表重复逗号 | `REJECT_PLAN` | 通过 |
| 表尾孤立 `DEFAULT` | `REJECT_PLAN` | 通过 |
| `PARTITION BY RANGE(,)` | `REJECT_PLAN` | 通过 |
| 精确依赖版本 | 方案拟锁定 `sqlglot==30.14.0` | 原则通过；须在实现提交中核验所有依赖清单 |

Rev.I 没有简单绕开上一轮意见，而是确实关闭了已知反例。以下阻断项属于本轮在扩大后的“整条 DDL 验证”主张下发现的新空洞。

## 4. 问题总表

| 编号 | 级别 | 问题 | 风险结论 |
|---|---|---|---|
| BLOCK-I1 | BLOCK | 列定义未按 TDSQL 语法消费，AST 门禁会接纳被 sqlglot 静默纠正或容忍的非法列 | 可把 `E999` 变成 `Create`，造成审核结论污染 |
| BLOCK-I2 | BLOCK | 分区消费器和 AST 门禁均未保证方法、次数、值域及结构守恒 | 同时存在非法分区被恢复、合法负值分区无法恢复 |
| BLOCK-I3 | BLOCK | 表选项白名单与 TDSQL 官方 `Local_table_option` 不一致 | 官方合法语法漏恢复，未举证选项被放入可信恢复域 |
| MAJOR-I1 | MAJOR | H 组及总用例数算术矛盾，A-108/A-109 无法从文档复核 | 方案的“245/245、85/85”验收证据目前不可审计 |
| MAJOR-I2 | MAJOR | 文档仍保留 Rev.H 的函数名、两阶段流程和旧计数 | 开发者可能按过期路径施工，产生两套安全模型 |

## 5. 详细发现与可实施修改意见

### 5.1 BLOCK-I1：列定义检查不是完整语法验证

#### 5.1.1 发生原因

`_scan_definition_list()` 在列分支中只做三件事：

1. 首 token 必须像标识符；
2. 列名后必须存在一个非逗号 token；
3. 随后仅按括号深度跳到顶层逗号。

它没有识别 `data_type`，也没有消费 `NULL/NOT NULL/DEFAULT/AUTO_INCREMENT/UNIQUE/PRIMARY KEY/COMMENT/COLLATE/...` 的顺序、参数和重复性。

候选 AST 门禁仅检查 `ColumnDef.kind is not None`。sqlglot 的恢复性解析会把部分非法类型静默改写，或者保留重复约束后仍返回 `Create`，所以该门禁不能补足 token 层的空洞。

#### 5.1.2 可复现证据

以下原始 SQL 在当前 `RuleChecker` 中均含 `E999_SYNTAX_ERROR`；执行 Rev.I 完整恢复路径后：

| 样例中的列定义 | Rev.I 结果 | sqlglot 30.14.0 候选变化 |
|---|---|---|
| `id VARCHAR()` | `ACCEPT:Create` | 被静默正规化成 `id TEXT` |
| `id DECIMAL(,2)` | `ACCEPT:Create` | 被静默正规化成 `id DECIMAL(2)` |
| `id INT DEFAULT 1 DEFAULT 2` | `ACCEPT:Create` | 两个 `DEFAULT` 均被保留并接纳 |

完整模板为：

```sql
CREATE TABLE t (
  id <上述列定义的类型或约束部分>,
  UNIQUE KEY uk(id) COMMENT 'x'
) ENGINE=InnoDB;
```

这直接推翻 §3.0c 中“整条 `CREATE TABLE` 都按 TDSQL 官方语法验证通过”的充分性论证。定义项数量相同、`kind` 非空，不等于定义项语法合法，也不等于 AST 语义与原文守恒。

#### 5.1.3 次生灾害机制

```text
非法列定义 + 目标 UNIQUE COMMENT
  -> 主干因 UNIQUE COMMENT/非法列之一抛 ParseError，结果为 E999
  -> Rev.I 仅确认“列名后有 token”
  -> 掩码 UNIQUE COMMENT
  -> sqlglot 静默修正类型或容忍重复约束
  -> AST 门禁看到 ColumnDef.kind 非空
  -> E999 消失，119 条规则在错误 AST 上继续执行
```

此时系统无法区分“只修复了 sqlglot 的 UNIQUE COMMENT 缺口”和“顺便吞掉了另一处真实语法错误”。

#### 5.1.4 必须修改

Rev.J 必须在以下方案中选定并完成一种，不能继续以 `kind is not None` 作为列定义合法性的证明：

1. 新增保守的 `_consume_column_definition()` 与 `_consume_data_type()`，依据目标 TDSQL 版本的官方语法逐 token 消费数据类型、类型参数和列约束；任何未认领、次序非法、参数残缺或不可重复约束重复出现时失败关闭；或
2. 如果不准备在本次修复中实现完整列语法，则明确缩小恢复输入域，只对白名单化且已逐 token 证明的列定义子集开放恢复。不能再声称覆盖完整 TDSQL `CREATE TABLE`。

鉴于恢复失败只会保留现有 E999，建议本版本优先采用“覆盖生产必要子集、其余失败关闭”的保守实现，并把未覆盖的官方合法列形态登记为已知假阴性，而不是让宽松 AST 替代语法判据。

至少新增以下负向矩阵：

- `VARCHAR()`、`CHAR()`、`DECIMAL(,2)`、`DECIMAL(10,)`；
- 重复 `DEFAULT`、重复 `COMMENT`、矛盾 `NULL NOT NULL`；
- 残缺 `COLLATE`、`COMMENT`、`AUTO_INCREMENT`；
- 列定义中插入未知 token；
- 每例必须断言：若主干为 E999，恢复链不得得到 `Create`。

### 5.2 BLOCK-I2：分区语法与候选结构未守恒

#### 5.2.1 发生原因

分区链存在四层相互叠加的问题：

1. `_scan_table_tail()` 只维护 `seen_decl`，没有 `seen_partition`，可连续消费多个 `PARTITION BY`；
2. `_consume_partition_clause()` 的 `_PARTITION_METHODS` 包含 `RANGE/LIST/HASH/KEY`，并额外接受 `LINEAR`、`COLUMNS`、`PARTITIONS n`，与文档声明的 TDSQL 二级分区仅 Range/List 不一致；
3. `_consume_partition_values()` 把 `VAR`、`IDENTIFIER` 当成“字面量”，却不接受 `- NUMBER` 这样的有符号常量；
4. `_validate_recovery_candidate()` 只检查候选 properties 中“存在任意一个类名以 `PartitionBy` 开头的节点”，不校验分区节点数量、方法、表达式、定义数量和值。

#### 5.2.2 可复现证据

| 样例 | 当前主干 | Rev.I 结果 | 说明 |
|---|---|---|---|
| 连续两个 `PARTITION BY` | E999 | `ACCEPT:Create` | 候选 AST 同时含 `PartitionByRangeProperty`、`PartitionByListProperty`，布尔门禁照样通过 |
| `TDSQL_DISTRIBUTED BY HASH(id)` 后直接跟 Range 风格定义表 | E999 | `ACCEPT:Create` | 整段被掩码，候选 AST 的 properties 为空，门禁因原文无 `PARTITION BY` 而不检查 |
| `VALUES IN (\`foo\`)` | E999 | `ACCEPT:Create` | 标识符被错误当成分区值字面量 |
| `VALUES LESS THAN (-1)` | E999 | `REJECT_PLAN` | 合法负常量无法进入恢复链 |
| `VALUES IN (-1, 2)` | E999 | `REJECT_PLAN` | 同上 |

双分区穿透样例：

```sql
CREATE TABLE t (
  id INT,
  UNIQUE KEY uk(id) COMMENT 'x'
)
PARTITION BY RANGE(id) (
  PARTITION p0 VALUES LESS THAN (10)
)
PARTITION BY LIST(id) (
  PARTITION p1 VALUES IN (1)
);
```

负值不是把 MySQL 语法强加给 TDSQL：TDSQL 官方建表文档把 Range 边界定义为 `expr | value_list`，二级分区文档又明确其语法与 MySQL 类似；MySQL 官方文档给出了 `VALUES LESS THAN (-5)` 成功建表的实例。Rev.I 若要拒绝负数，必须提供目标 TDSQL 版本的相反官方证据，而当前没有。

#### 5.2.3 次生灾害机制

非法路径与合法路径同时受影响：

```text
非法多分区/伪值
  -> token 消费器逐段“都能消费”
  -> sqlglot 形成一个或多个 PartitionBy 节点
  -> 布尔门禁只问“有没有分区”
  -> E999 被吞掉

合法负值边界
  -> DASH token 不在 _LIT
  -> 规划阶段失败关闭
  -> 本次本来要修复的 UNIQUE COMMENT 崩溃仍保留为 E999
```

#### 5.2.4 必须修改

Rev.J 至少完成：

1. `_scan_table_tail()` 增加 `seen_partition`，一条 `CREATE TABLE` 最多允许一个二级 `PARTITION BY`；
2. 把方法传入后续消费者，按方法限定结构。二级分区只接受已有 TDSQL 官方证据支持的 Range/List；一级分片定义表只在其相应 Range/List 形态后开放，不能无条件挂在 Hash 后；
3. 分区值使用明确的值/表达式消费器：支持目标版本官方允许的有符号常量；不得把反引号标识符自动视为值字面量；
4. 候选门禁由布尔存在性升级为结构守恒：原文与候选的分区子句数、方法、表达式、分区定义数必须一致；被有意掩码的 `ENGINE/COMMENT` 只能作为已登记差异排除；
5. `_PARTITION_METHODS`、`LINEAR`、`COLUMNS`、`PARTITIONS n` 必须与 H4b 的预期一致：若判为 TDSQL 非法，就应在 token 层直接拒绝，不能依赖 sqlglot 30.14.0 恰好返回 `Command/ParseError`；
6. 对 KFN-1 保持用户已批准结论，不将本项整改扩展成重新开启 MAXVALUE 决策。

新增测试至少覆盖：重复分区、方法与定义表错配、标识符伪值、负整数、负小数（若目标语法允许/禁止需分别固化）、字符串值、日期值、多值列表、分区节点数量守恒。

### 5.3 BLOCK-I3：表选项白名单没有对齐 TDSQL 官方语法

#### 5.3.1 发生原因

Rev.I 宣布把判据切换为 TDSQL 官方语法，但 `_consume_table_option()` 仍沿用一组 MySQL/历史语料选项：

- `_TBL_OPT_VALUE_NUM` 包含 `CHECKSUM`、`AVG_ROW_LENGTH`、`KEY_BLOCK_SIZE`、`MAX_ROWS`、`MIN_ROWS`；
- `_TBL_OPT_TRISTATE` 包含 `PACK_KEYS`、`DELAY_KEY_WRITE`；
- 却遗漏 TDSQL 官方 `Local_table_option` 中明确列出的 `STATS_AUTO_RECALC` 和 `STATS_SAMPLE_PAGES`。

腾讯云 TDSQL 官方页面当前列出的本地表选项是：`AUTO_INCREMENT`、字符集、排序规则、`COMMENT`、`ENGINE`、`ROW_FORMAT`、`STATS_AUTO_RECALC`、`STATS_PERSISTENT`、`STATS_SAMPLE_PAGES`。Rev.I 未给出目标实例 `SHOW CREATE TABLE` 或目标版本官方资料来证明额外 MySQL 选项属于本次安全恢复域。

#### 5.3.2 可复现证据

| 表选项 | 官方状态 | Rev.I 结果 |
|---|---|---|
| `STATS_AUTO_RECALC=1` | TDSQL 官方明确列出 | `REJECT_PLAN` |
| `STATS_SAMPLE_PAGES=10` | TDSQL 官方明确列出 | `REJECT_PLAN` |
| `MAX_ROWS=10` | Rev.I 所引 TDSQL 官方清单未列出，方案无目标版本举证 | `ACCEPT:Create` |

三条原始 SQL 在当前主干均含 E999，模板同样只需在表内带一个目标 `UNIQUE KEY ... COMMENT`。

#### 5.3.3 影响机制

- 遗漏官方选项：合法 DDL 仍然 E999，本次修复不完整；
- 擅自扩展选项：一旦原始 SQL 的错误不只来自 UNIQUE COMMENT，恢复链可能借助 sqlglot 宽松解析吞掉真实错误；
- 最严重的问题是规范口径漂移：注释称“官方白名单”，实际却是“仓内语料 + MySQL 经验白名单”，后续维护者无法判断增删依据。

#### 5.3.4 必须修改

1. 以目标 TDSQL 版本官方建表语法为默认白名单，补齐 `STATS_AUTO_RECALC`、`STATS_SAMPLE_PAGES` 及其精确值域；
2. 对官方页面未列出的选项默认失败关闭；若生产 `SHOW CREATE TABLE` 已证明目标版本支持，则在设计中逐项附证据、适用版本和测试，不得笼统沿用 MySQL 清单；
3. 增加“选项名—官方出处—值谓词—正例—反例”追踪表；
4. 为每个选项覆盖有/无等号、合法值、数字/字符串/标识符错型、残缺值和重复出现策略。

### 5.4 MAJOR-I1：测试数量与验收证据内部矛盾

Rev.I §7.1 列出的 H 子组数量为：

```text
H0 14 + H1 11 + H2 5 + H2b 3 + H3 16 + H4 6
+ H4b 8 + H4c 2 + H5 22 + H6 12 + H7 10 = 109
```

但文档同时宣称“H 组 85 例”。A~W 其他组按文档数字合计 160，因此：

```text
160 + 109 = 269
```

并非文档宣称的 245。差额恰好是 H0 的 14 例和 H7 的 10 例，可能意味着它们只是旧 case 的别名或重分类；但文档又明确把 H0/H7 写成新增子组并纳入加法，因此当前无法审计。

此外，验收表仍有：

- G-26 写 H 组 81 例；
- 风险表仍写 H 组 81 例；
- A-108/A-109 写 H 组 85 例；
- 总量写 245 例。

#### 必须修改

1. 以实际参数化用例 ID 作为唯一真源，每个 case 只能有一个主 ID；重分类或别名不得重复计数；
2. 在测试模块中加入收集数量断言，或提供可复现的 `pytest --collect-only -q` 输出；
3. 同步修正 §7.1、G-1/G-5/G-26、风险表、A-108/A-109 和 checklist；
4. 实现后报告“本文件实际收集数”和“全仓实际通过数”，不要再用设计阶段的历史基线做算术推演。

在计数闭环前，“245/245、85/85、三版本逐条一致”均不能作为放行证据。

### 5.5 MAJOR-I2：施工说明仍混有 Rev.H 旧实现

Rev.I 的正式实现已经改为统一 `_plan_recovery()`，但文档其他位置仍描述：

- `USING (BTREE|HASH)`，与 Rev.I 的 BTREE-only 决策冲突；
- 已删除的 `_strip_tdsql_dialect_tail()`；
- `_strip_unique_index_comments()` 与方言剥离器的“两阶段重试”；
- checklist 中“两个剥离器”及导入旧函数的要求；
- H 组 81 例和“Rev.H 已关闭风险”的旧结论。

这不是单纯措辞问题。方案定位为“可照图施工”的详细设计，存在两套函数名和两套恢复流程会导致开发者复活已被 Rev.I 否定的分散安全模型，或者让测试导入不存在的函数。

#### 必须修改

Rev.J 应全文机械检索并统一以下关键词：

```text
_strip_tdsql_dialect_tail
_strip_unique_index_comments
两阶段
两个剥离器
BTREE|HASH
H 组 81
H组 81
Rev.H 关闭
```

历史说明可保留，但必须明确放入“已废弃历史方案”区并加不可施工标识；正文、伪代码、流程图、测试、checklist 和验收表只能指向 `_plan_recovery()` 这一条实现路径。

## 6. 对 Rev.I 核心安全性质的复判

| 安全性质 | Rev.I 声明 | 本轮结论 |
|---|---|---|
| 只修改目标 span | `_spans_only_diff` / 等长空白 | **局部成立**；它能证明字符修改未越界，不能证明未修改区域本来合法 |
| 整条 DDL 已按 TDSQL 官方语法验证 | 统一规划器 + 五类消费者 | **不成立**；列定义、分区结构、表选项仍有未覆盖或错误覆盖 |
| 候选 AST 结构保真 | 同表名、定义项数、非空 kind、存在分区 | **不足**；没有比较列语义和分区数量/方法/定义 |
| 只在原解析失败时恢复，因此对已有可解析语句零影响 | 恢复入口受异常触发 | **只能证明可解析语句不变，不能证明失败语句安全**；一个 E999 语句可同时包含目标解析器缺口和真实语法错误 |
| sqlglot 只是工具，不是 TDSQL 判据 | 文档原则 | **原则正确，代码尚未完全落实**；部分白名单仍由 sqlglot/历史 MySQL 经验兜底 |
| KFN-1 失败关闭 | 用户批准 | **接受** |

## 7. Rev.J 必须满足的放行门槛

### 7.1 设计门槛

- [ ] BLOCK-I1：列定义消费者或明确收窄的列定义白名单完成，本文 3 个穿透样例全部保住 E999；
- [ ] BLOCK-I2：分区次数、方法、值域和候选结构守恒完成，重复分区/伪值失败关闭，负值按 TDSQL 证据正确处理；
- [ ] BLOCK-I3：表选项清单与 TDSQL 官方语法逐项可追溯，两个官方遗漏项纳入正向用例；
- [ ] 所有失败关闭策略区分“官方非法”与“官方合法但本版未覆盖”，后者进入 KFN/已知限制登记；
- [ ] 全文只保留一条可施工恢复链；
- [ ] 测试 case ID、子组数量、总数和验收表完全一致。

### 7.2 测试门槛

- [ ] 当前 1384 项回归继续全绿；
- [ ] 生产 gg77/gg78 fixture 的两个原问题准确恢复，R054/R003/R004/R005/R028 不产生连带误报；
- [ ] 本报告所有穿透样例纳入自动化，断言恢复结果的等级不得比主干更宽松；
- [ ] 正向 TDSQL 官方语法用例和负向近邻反例成对覆盖；
- [ ] 在精确锁定的 sqlglot 30.14.0 环境执行零 skip；
- [ ] 依赖安装后显式断言运行时 `sqlglot.__version__ == "30.14.0"`；
- [ ] `pytest --collect-only` 的实际收集数与文档一致。

### 7.3 实现后专项回归建议

除单元测试外，至少进行：

1. 两份生产 HTML 报告对应 SQL 的端到端审核；
2. 同一张表同时包含普通索引 COMMENT、唯一索引 COMMENT、列 COMMENT、表 COMMENT、TDSQL 分片/广播和二级分区的组合测试；
3. E999 单错误、目标缺口单错误、目标缺口 + 另一真实语法错误三类因果隔离测试；
4. R003/R004/R005/R028/R054/R077 的精确规则集合对比，不能只断言“无 E999”；
5. 解析 AST 中表名、列数、列名/类型、索引数/类型/键列、分区数/方法/定义数的结构快照对比。

## 8. 最终评审意见

Rev.I 已经证明 A 对第八轮意见进行了认真整改，上一轮具体反例也确实关闭；本轮不否定统一规划器的方向。但恢复链是 SQL 审核系统的信任边界，不能把“扫描到了每个顶层片段”表述为“按官方语法证明了整条 DDL”。本轮三个 BLOCK 都有 `E999 -> Create` 或官方合法语法继续 E999 的可复现证据，风险直接作用于项目核心审核结论。

因此最终意见是：**Rev.I 不通过，暂停按本版开发；完成 BLOCK-I1/I2/I3、修正测试证据和全文施工口径后，以 Rev.J 提交第十轮复审。**

KFN-1 继续按用户在 2026-08-26 的批准决策执行，本报告不要求重新开放该决策。
