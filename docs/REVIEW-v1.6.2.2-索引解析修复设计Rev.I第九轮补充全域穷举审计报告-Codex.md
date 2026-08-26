# v1.6.2.2 索引解析修复设计 Rev.I 第九轮补充全域穷举审计报告

| 项目 | 内容 |
|---|---|
| 审计对象 | Rev.I 详细设计中的完整恢复链与验收方案 |
| 仓库基线 | `7c39af5`（`main` / `origin/main`） |
| 审计日期 | 2026-08-26 |
| 审计人 | Codex |
| 与前报告关系 | **本报告是第九轮复审的全域补充和整改总表；A 后续应以本报告的根因清单为单一入口，不要逐个样例打补丁** |
| 最终结论 | **Rev.I 仍不通过；先重构语法模型与测试判据，再形成 Rev.J** |

## 1. 为什么追加本次全域审计

前九轮发现的问题都是真实问题，但复审方式仍偏向“针对上一轮新增代码构造若干反例”。这种方式能够证明某个补丁不安全，却不能一次性证明整个恢复输入域已经闭合，所以才会出现一轮关闭几个样例、下一轮又在相邻语法面发现新样例的现象。

本次不再按单例推进，而是把 Rev.I 的恢复链拆成有限的决策面，逐一做静态审计、交叉组合和端到端规则验证：

1. 建表头部；
2. 顶层定义列表；
3. 列定义；
4. 索引键值与索引选项；
5. 表选项；
6. 一级分片声明及分片定义；
7. 二级分区表达式、值和分区定义；
8. 表尾顺序与声明互斥；
9. span 触发条件与等长改写；
10. 候选 AST 结构守恒；
11. `Command` 和 `except` 两条真实调用路径；
12. 119 条规则的最终命中集合；
13. 测试判据、数量、依赖及施工文档一致性。

本报告把发现聚类为根因，不把同一根因下的几十个 SQL 逐条算成几十个问题。

## 2. 判据与范围约束

### 2.1 规范优先级

本轮继续遵守“以 TDSQL 为最终判据”的要求：

1. [腾讯云 TDSQL MySQL 版建表语法](https://cloud.tencent.com/document/product/557/8767)；
2. [腾讯云 TDSQL MySQL 版二级分区](https://intl.cloud.tencent.com/zh/document/product/1042/33361)；
3. 项目已冻结的产品决策和目标实例生产 DDL；
4. TDSQL 官方文档明确声明“与 MySQL 分区语法类似”时，才使用 [MySQL 官方分区限制](https://dev.mysql.com/doc/refman/8.0/en/partitioning-limitations.html) 和 [MySQL 官方索引语法](https://dev.mysql.com/doc/refman/8.0/en/create-index.html) 做补充交叉验证；
5. sqlglot 只作为词法器和候选 AST 生成器，不能充当 TDSQL 合法性判据。

### 2.2 不重新开启的用户决策

以下事项不构成本报告问题：

- KFN-1：`VALUES LESS THAN MAXVALUE` 本版本保留已知假阴性；
- ADJ-6：`BROADCAST + shardkey` 冲突维持现状；
- NG-10/ADJ-11：`CONSTRAINT x UNIQUE (col)` 本次不支持；
- sqlglot tokenizer 路线由用户决定，本报告不要求更换词法器；
- SPATIAL 维持映射为 NORMAL 的兼容取舍。

本报告对“重复 `shardkey`”和“`shardkey + TDSQL_DISTRIBUTED`”提出问题，不等于重新开启 ADJ-6；整改可以继续为 `BROADCAST + shardkey` 保留专门的特征化例外。

## 3. 全域审计方法和规模

### 3.1 原样代码执行

从 Rev.I 文档原样抽取：

```text
_spans_only_diff
_tdsql_table_def_bounds
_consume_table_option
_consume_ident / _consume_ident_list
_consume_index_key_parts
_consume_partition_expr
_consume_partition_values
_consume_partition_defs
_consume_partition_clause
_scan_table_tail
_scan_definition_list
_plan_recovery
_blank_spans
_validate_recovery_candidate
```

在方案拟精确锁定的 `sqlglot 30.14.0` 下执行完整链路，而不是只调用某个 helper：

```text
原始 SQL
  -> 当前主干解析结果
  -> Rev.I _plan_recovery
  -> 三类 span 等长掩码
  -> sqlglot.parse_one
  -> _validate_recovery_candidate
  -> 隔离施工后的 SQLParser
  -> RuleChecker 119 条规则集合
```

### 3.2 组合矩阵

| 矩阵 | 组合方式 | 数量 | 结果摘要 |
|---|---|---:|---|
| 二级分区 | 4 方法 × 2 VALUES 操作符 × 5 值形态 × 2 定义前缀 | 80 | Rev.I 最终接纳 8；另有多类官方正例在规划层被拒 |
| 一级分片定义 | 3 方法 × 2 VALUES 操作符 × 5 值形态 × 2 定义前缀 | 60 | **接纳 48**；因整段方言被掩码，候选 AST 无法反证其内部是否合法 |
| 表尾顺序 | 8 种 atom 的有序两两排列 | 56 | **接纳 50**；证明当前实现是“任意顺序循环”，不是 TDSQL 表尾状态机 |
| token 变异 | 对合法种子做插入、删除、替换 1～4 次 | 20,000 | helper 未崩溃；819 条进入恢复成功路径，随后按根因归类 |

“接纳数量”不等于这些样例全部非法；它用于发现状态机实际上允许的输入域。最终问题只采用有 TDSQL 官方语法、目标生产契约或内部自相矛盾可以证明的样例。

### 3.3 端到端验证

把 Rev.I 文档代码隔离施工到当前 `SQLParser` 后，通过 `RuleChecker` 执行规则，而不是只观察 AST。确认存在：

- `E999 -> Create`；
- `Command -> Create`；
- 非法 DDL 的 R054/R077 消失；
- 官方合法 DDL继续 E999并连带产生 R003/R004/R005/R028；
- 列类型被 sqlglot 静默正规化后进入规则层。

当前仓库专项基线：

```text
py -m pytest -q \
  tests/test_parser_tdsql_dialect_fallback.py \
  tests/test_r077_r054_tdsql_syntax.py \
  tests/test_v2_syntax_truncation.py

61 passed, 3 warnings
```

上一份第九轮报告已在同一仓库基线完成全量 `1384 passed`。本次不修改产品代码。

## 4. 消费器逐项审计结果

| 组件 | 审计结果 | 结论 |
|---|---|---|
| `_spans_only_diff()` / `_blank_spans()` | 20,000 条变异未崩溃；批准 span 外无字符变化 | 基本通过；它只能证明“改了哪里”，不能证明“没改的内容合法” |
| `_tdsql_table_def_bounds()` | 头部、限定名、括号和分号边界总体符合既定白名单 | 通过当前作用域 |
| `_consume_ident[_list]()` | 空值、连续逗号、尾逗号可失败关闭 | 通过当前作用域 |
| `_scan_definition_list()` | 索引键列表改善有效；列定义仍按括号深度整体跳过 | **阻断** |
| `_consume_index_key_parts()` | 列表结构改善有效；`NUMBER` 未限定整数/正数 | 需整改 |
| `_consume_table_option()` | 值谓词有所收敛；官方清单、声明身份和顺序仍不正确 | **阻断** |
| `_consume_partition_expr()` | 分支顺序使 YEAR 之外的 VAR 形态函数永远先被当成普通列名 | **阻断** |
| `_consume_partition_values()` | RANGE/LIST 不分方法；标识符被当字面量；有符号值被拒 | **阻断** |
| `_consume_partition_defs()` | 同时服务一级分片和二级分区，却没有上下文参数 | **阻断** |
| `_consume_partition_clause()` | 接受超出 TDSQL 二级分区范围的方法，且未输出结构指纹 | **阻断** |
| `_scan_table_tail()` | 只记录 `seen_decl`，但 `shardkey` 不计入；没有阶段和次数状态 | **阻断** |
| `_plan_recovery()` | 任意辅助 mask span 都能独立启动恢复，不要求本次两个目标存在 | **阻断** |
| `_validate_recovery_candidate()` | 只检查定义数量、非空 kind 和“存在某个 PartitionBy” | **阻断** |

## 5. 一次性整改根因总表

| 编号 | 级别 | 根因 | 必须处置 |
|---|---|---|---|
| BLOCK-X1 | BLOCK | 非法用例以当前缺陷主干作 oracle，`rank` 判据允许“主干错、候选继续错”通过 | 改成 TDSQL 规范期望；主干仅作诊断基线 |
| BLOCK-X2 | BLOCK | 列定义没有语法消费者，AST 门禁接受 sqlglot 的静默纠正 | 实现保守列定义语法或明确缩小恢复域 |
| BLOCK-X3 | BLOCK | 辅助掩码可独立触发恢复，修复范围从两个目标扩张到任意 ASC/DESC、partition option | 区分 primary target 与 auxiliary mask；无 primary target 不得恢复 |
| BLOCK-X4 | BLOCK | 一级分片定义无方法上下文，HASH/RANGE/LIST 共用同一 VALUES 消费器 | 按分片方法建立独立语法；方法、操作符、定义表绑定 |
| BLOCK-X5 | BLOCK | 二级分区无方法、次数和值域守恒，官方函数实现存在死分支 | 重写为上下文状态机并生成结构指纹 |
| BLOCK-X6 | BLOCK | 表尾不是有限状态机，`shardkey` 未纳入分布声明互斥，终结子句后还能接表选项 | 建立顺序、次数、互斥模型；保留 ADJ-6 特例 |
| BLOCK-X7 | BLOCK | 表选项白名单与 TDSQL 官方清单不一致 | 按官方清单逐项追踪；生产扩展须附版本证据 |
| MAJOR-X1 | MAJOR | 候选 AST 门禁没有比较定义类型、列语义、表选项和分区结构 | 使用恢复前 token 指纹与候选 AST 指纹做结构守恒 |
| MAJOR-X2 | MAJOR | 索引前缀长度及可重复选项没有精确值域/次数策略 | 正整数解析；为不可重复选项维护 seen 集合 |
| MAJOR-X3 | MAJOR | 代码、说明、测试数量和 checklist 仍并存 Rev.H/Rev.I 两套真相 | 全文归一并由测试清单自动生成数量 |

## 6. BLOCK-X1：测试 oracle 使用了正在修复的缺陷主干

### 6.1 发生原因

Rev.I 对 `neg` 用例采用：

```text
rank(NoneType/E999)=0 < rank(Command)=1 < rank(Create)=2
要求 rank(候选) <= rank(主干)，且主干 E999 不得消失
```

这个判据只适合“防止候选比一个正确基线更宽松”。当前主干的 `_TDSQL_DIALECT_RE` 正是本次要替换的不安全实现，它已经会把部分非法方言尾句剥掉并返回 `Create`。此时：

```text
主干错误 Create（rank=2）
候选仍错误 Create（rank=2）
2 <= 2，测试通过
```

因此，H 组即使全部绿色，也无法证明“非法 DDL 0 例被恢复成合法”。这不是少几个 case，而是 oracle 本身不能否决一整类错误。

### 6.2 实证

不带 UNIQUE COMMENT 的非法一级分片定义：

```sql
CREATE TABLE t (id INT PRIMARY KEY)
TDSQL_DISTRIBUTED BY RANGE(id) (s1 VALUES IN (1));
```

当前主干是 `Command`，Rev.I 是 `Create`。另一些旧正则已错误得到 `Create` 的变体，则可在现有 rank 判据下直接绿色通过。

### 6.3 必须修改

- `neg` 的预期必须来自 TDSQL 官方语法/用户冻结契约：**规范判定非法时，候选不得为 Create**；
- 当前主干结果只记录为 `baseline_observation`，不能当 expected oracle；
- `pos`、`neg`、`pos_known`、`characterization_user_decision` 四类必须分开；
- ADJ-6 这类用户冻结行为只能进入 characterization，不能反推为 TDSQL 合法；
- 每个 neg 同时跑 `Command` 路径和 `except` 路径，不能只看其中一条。

这是停止“每轮再冒几条”的第一优先级整改。只补消费者而不改 oracle，后续测试仍可能全部绿色但方案继续不安全。

## 7. BLOCK-X2：列定义仍是未解析黑箱

上一报告已经指出 `VARCHAR()`、`DECIMAL(,2)` 和重复 DEFAULT。本轮扩展后确认不是三个特例，而是整个列定义域未建模：

| 列定义 | 当前主干 | Rev.I | 候选结果 |
|---|---|---|---|
| `id VARCHAR()` | E999 | Create | 静默变成 `TEXT` |
| `id DECIMAL(,2)` | E999 | Create | 静默变成 `DECIMAL(2)` |
| `id DECIMAL(10,)` | E999 | Create | 静默变成 `DECIMAL(10)` |
| `id INT DEFAULT 1 DEFAULT 2` | E999 | Create | 重复约束被保留并接纳 |
| `id INT NULL NOT NULL` | E999 | Create | 矛盾约束被接纳 |
| `id INT AUTO_INCREMENT AUTO_INCREMENT` | E999 | Create | 重复约束被接纳 |
| `id INT COMMENT 'a' COMMENT 'b'` | E999 | Create | 重复注释被接纳 |

根因是 `_scan_definition_list()` 对列分支只检查“列名后还有一个非逗号 token”，之后按括号深度跳到下一个顶层逗号；`_validate_recovery_candidate()` 又只检查 `ColumnDef.kind is not None`。

### 必须修改

建议不要在下一版继续补“禁止 `VARCHAR()`”之类的黑名单。应增加：

```text
_consume_data_type(tokens, i) -> next | fail
_consume_column_constraints(tokens, i, seen) -> next | fail
_consume_column_definition(tokens, i) -> (next, fingerprint) | fail
```

本次可只覆盖生产 fixture 和目标 TDSQL 官方文档所需的保守子集；未覆盖的官方合法列定义登记为 `pos_known` 并失败关闭。不能再把 sqlglot 的宽松 AST 当成列语法证明。

## 8. BLOCK-X3：没有“主修复目标”也能启动恢复

Rev.I 把 span 分为：

- UNIQUE COMMENT；
- TDSQL 方言声明；
- ASC/DESC、partition ENGINE/COMMENT 等辅助 mask。

但调用端只判断 `_all_spans` 非空。只要存在辅助 mask，即使没有 UNIQUE COMMENT、没有 TDSQL 方言声明，也会启动恢复。

实证：

```sql
CREATE TABLE t (id VARCHAR())
PARTITION BY RANGE(id) (
  PARTITION p0 VALUES LESS THAN (10) COMMENT='p'
);
```

当前主干：E999。Rev.I：掩码 partition COMMENT 后返回 Create，并把非法 `VARCHAR()` 静默改成 `TEXT`。端到端规则集合从包含 E999 变成普通结构规则集合。

这说明 Rev.I 不只是修复“假 UNIQUE”和“唯一索引 COMMENT 崩溃”，还悄悄新增了“所有 partition option/ASC-DESC 的自动修复”。新增入口扩大了爆炸半径，但测试和风险评估仍按两个原问题描述。

### 必须修改

规划结果必须区分：

```python
RecoveryPlan(
    primary_target_spans,   # UNIQUE COMMENT 或已批准的 TDSQL 方言目标
    auxiliary_mask_spans,   # 只有存在 primary target 时才能附带使用
    source_fingerprint,
)
```

入口条件必须是 `primary_target_spans` 非空；不能用全部 span 的并集决定是否恢复。如果产品确实要单独修复 partition option 或 ASC/DESC，应另立缺陷、单独评估和验收，而不是作为本热修的隐式副作用。

## 9. BLOCK-X4：一级分片定义的 60 组合暴露上下文缺失

### 9.1 发生原因

`_scan_table_tail()` 在 `TDSQL_DISTRIBUTED BY HASH|RANGE|LIST(col)` 后，只要看见左括号就无条件调用同一个 `_consume_partition_defs()`。该消费者：

- 不知道当前分片方法；
- 同时接受 `VALUES LESS THAN` 与 `VALUES IN`；
- 同时接受带/不带 `PARTITION` 前缀；
- 把 NUMBER、STRING、VAR、IDENTIFIER、NULL 都当值；
- 对 HASH 也允许 Range/List 风格定义表。

更关键的是，整段 `TDSQL_DISTRIBUTED ...` 随后全部被掩码，候选 AST 中没有任何一级分片节点。`_validate_recovery_candidate()` 无法对这部分做第二次校验。

### 9.2 确认穿透

以下均被 Rev.I 从 Command/E999 升级为 Create：

```sql
-- HASH 后错误挂接 Range 定义表
CREATE TABLE t (id INT PRIMARY KEY)
TDSQL_DISTRIBUTED BY HASH(id) (s1 VALUES LESS THAN (10));

-- RANGE 使用 LIST 的操作符
CREATE TABLE t (id INT PRIMARY KEY)
TDSQL_DISTRIBUTED BY RANGE(id) (s1 VALUES IN (1));

-- LIST 使用 RANGE 的操作符
CREATE TABLE t (id INT PRIMARY KEY)
TDSQL_DISTRIBUTED BY LIST(id) (s1 VALUES LESS THAN (10));
```

端到端最严重的样例是 HASH + 定义表：当前主干规则集合包含 R054、R077；Rev.I 返回 Create 后两条均消失。即使 SQL 本身非法，审核结果看起来反而“更干净”。

### 9.3 必须修改

消费者至少要带上下文：

```text
_consume_distribution_defs(
    method=HASH|RANGE|LIST,
    require_partition_keyword=False,
) -> (next, distribution_fingerprint) | fail
```

并固化：

- HASH 是否允许定义表必须由目标 TDSQL 官方/生产版本证据决定；当前文档注释自己写的是“仅 range/list”，则代码必须拒绝 HASH 定义表；
- RANGE 只接受其官方边界结构；LIST 只接受 IN 列表；
- 值类型按目标分片方法和键类型的可证明语法建模；标识符不得冒充字面量；
- 分片方法、键、定义数量、每个定义的操作符和值数量写入 source fingerprint；
- 因整段方言会从候选 AST 消失，该 fingerprint 必须在规则层所需的 raw_sql 提取结果上独立复核，不能假装 AST 门禁覆盖了它。

## 10. BLOCK-X5：二级分区仍没有结构和方法守恒

### 10.1 独立缺口

1. `_scan_table_tail()` 没有 `seen_partition`，允许两个 `PARTITION BY`；
2. `_consume_partition_values()` 不知道外层是 RANGE 还是 LIST；
3. VAR/IDENTIFIER 被当成值字面量；
4. `-1` 的 DASH + NUMBER 被拒，官方合法负值无法恢复；
5. `_consume_partition_defs()` 同时服务一级分片和二级分区，二级分区所需的 `PARTITION` 前缀没有成为强约束；
6. `_validate_recovery_candidate()` 只问“是否存在某个类名以 `PartitionBy` 开头的 property”，不比较数量、方法、表达式和定义；
7. `_PARTITION_METHODS` 接受 HASH/KEY，消费者还接受 LINEAR/COLUMNS/PARTITIONS，实际失败依赖 sqlglot 30.14.0 恰好返回 Command，而不是 token 白名单拒绝。

### 10.2 多分区穿透

```sql
CREATE TABLE t (
  id INT PRIMARY KEY,
  UNIQUE KEY uk(id) COMMENT 'x'
)
PARTITION BY RANGE(id) (
  PARTITION p0 VALUES LESS THAN (10)
)
PARTITION BY LIST(id) (
  PARTITION p1 VALUES IN (1)
);
```

候选 AST 同时产生 `PartitionByRangeProperty` 和 `PartitionByListProperty`，现门禁仍返回 true，E999 消失。

### 10.3 官方函数存在代码死分支

TDSQL 二级分区官方文档明确支持 `year`、`month`、`day`。Rev.I 实测：

| 函数 | Rev.I |
|---|---|
| `YEAR(col)` | Create |
| `MONTH(col)` | REJECT_PLAN |
| `DAY(col)` | REJECT_PLAN |

原因不是函数白名单少两个词。sqlglot 30.14.0 把 `MONTH`、`DAY`、`DAYOFMONTH`、`TO_DAYS` 等词法成 VAR；`_consume_partition_expr()` 先执行“若是标识符就当普通列”，看到后续左括号便失败，永远到不了函数分支。只有 YEAR 因专用 TokenType 绕过第一个分支。

因此，仅向 `_PARTITION_FUNCS` 继续加字符串不会修好问题，必须调整分支顺序：先识别“白名单函数 + 左括号”，再识别普通单列。

### 10.4 必须修改

```text
_consume_secondary_partition(
    method=RANGE|LIST,
    require_partition_keyword=True,
) -> (next, SecondaryPartitionFingerprint) | fail
```

指纹至少包含：子句数量、方法、表达式规范形态、定义数量、定义名、每项 VALUES 操作符和值数量。候选 AST 必须逐项对比，而不是只查类名前缀。

KFN-1 保持原决策；负值和 MONTH/DAY 不属于 KFN-1，必须单独修复或登记新的、经用户批准的已知假阴性。

## 11. BLOCK-X6：表尾缺少有限状态机

### 11.1 发生原因

`_scan_table_tail()` 的实现本质是：

```text
while not EOF:
  能消费分区就消费
  能消费 TDSQL/BROADCAST 就消费
  否则当表选项消费
```

除了 TDSQL/BROADCAST 共用一个 `seen_decl` 外，没有阶段、顺序、次数和 option identity。`shardkey` 被 `_consume_table_option()` 吞掉，完全不会更新 `seen_decl`。

### 11.2 已确认接纳

```sql
-- 重复 shardkey
... shardkey=id shardkey=id

-- 两种一级分布声明并存（不是 ADJ-6 的 BROADCAST 特例）
... shardkey=id TDSQL_DISTRIBUTED BY HASH(id)

-- 终结声明后又出现本应在前的表选项
... TDSQL_DISTRIBUTED BY HASH(id) ENGINE=InnoDB

-- 二级分区之后再接本地表选项
... PARTITION BY RANGE(id) (...) ENGINE=InnoDB
```

带 UNIQUE COMMENT 时，上述多条从 E999 变为 Create。`shardkey + HASH` 的端到端样例中，E999、R054 均消失。

TDSQL 官方建表语法把 local table options 放在分片/分区声明之前，并明确 `TDSQL_DISTRIBUTED BY ...` 的放置位置。项目生产契约又存在 `shardkey + 二级分区`、`二级分区 + TDSQL_DISTRIBUTED` 等已验证顺序，所以这里需要显式状态机，不能简单规定唯一固定顺序，也不能“顺序不限”。

### 11.3 必须修改

建议规划器输出 atom identity，并建立经过官方/生产证据批准的状态迁移表：

```text
LOCAL_OPTIONS*
  -> SHARDKEY? -> SECONDARY_PARTITION?
  -> SECONDARY_PARTITION? -> TDSQL_DISTRIBUTED?
  -> BROADCAST?
  -> EOF
```

具体允许的分支以目标 TDSQL 版本证据为准。硬性要求：

- 同一表选项的重复策略明确；
- `shardkey` 必须被识别为一级分布声明；
- 重复 shardkey、shardkey + TDSQL_DISTRIBUTED 默认拒绝；
- ADJ-6 的 BROADCAST + shardkey 如需维持现状，必须作为命名清晰的唯一 characterization 例外；
- 进入终结阶段后不得再回到 local option 阶段；
- 测试由状态迁移表生成合法路径和每条边的非法近邻。

## 12. BLOCK-X7：表选项仍未按 TDSQL 官方清单建模

上一报告指出的结论在全域审计后不变：

- 官方明确支持但 Rev.I 拒绝：`STATS_AUTO_RECALC`、`STATS_SAMPLE_PAGES`；
- Rev.I 主动接纳但当前引用的 TDSQL 官方清单未列出：`CHECKSUM`、`AVG_ROW_LENGTH`、`KEY_BLOCK_SIZE`、`MAX_ROWS`、`MIN_ROWS`、`PACK_KEYS`、`DELAY_KEY_WRITE`；
- `AUTO_INCREMENT=1.5` 因 TokenType.NUMBER 过宽被接纳。

必须建立下表作为代码和测试的生成源：

| option | TDSQL 证据/目标版本 SHOW CREATE | 值谓词 | 可重复 | 所处阶段 | 正例 | 负例 |
|---|---|---|---|---|---|---|

官方未列出的选项默认失败关闭。若目标实例 `SHOW CREATE TABLE` 证明某个版本支持，应记录实例版本、输出和适用范围，再纳入白名单。

## 13. MAJOR-X1：AST 门禁需要结构指纹，不是布尔检查

当前门禁无法发现：

- `VARCHAR()` 变 `TEXT`；
- `DECIMAL(,2)` 变 `DECIMAL(2)`；
- 两个 PARTITION BY 被保留为两个 properties；
- 重复/错序表选项；
- 整个一级分片声明连同非法定义被删除；
- 候选的表选项数量、名称和值与原文不一致。

建议 `_plan_recovery()` 在 token 校验时生成 `SourceFingerprint`：

```text
qualified_table_name
definition_count
definitions[]:
  kind
  column_name + data_type_shape + constraint_shape
  index_kind + index_name + key_parts + option_kinds
table_options[]: name + normalized_value
distribution: method + keys + definitions
secondary_partition: method + expr + definitions
```

候选 AST 门禁逐项比较所有未被批准掩码改变的字段。对于 TDSQL 专有、候选 AST 必然不存在的一级分片结构，必须由独立 raw token fingerprint 保持并供后续规则读取，不能宣称 AST 已保真。

生产 fixture `report_6311_biz_tx_log.sql` 的 `/*!50100 PARTITION BY ... */` 会被 sqlglot tokenizer 整体跳过，`_had_partition()` 返回 false。该行为与当前 sqlglot 基线一致，但说明文档中的“原文含分区则 AST 必须保留”并不覆盖生产版本注释。Rev.J 必须把它写成明确例外，并继续用精确规则集合证明对当前 119 条规则无漂移。

## 14. MAJOR-X2：索引键和选项仍有值域/次数空洞

`_consume_index_key_parts()` 只检查 prefix length 是 TokenType.NUMBER，因此接纳：

```sql
UNIQUE KEY uk(id(1.5)) COMMENT 'x'
UNIQUE KEY uk(id(0)) COMMENT 'x'
```

TDSQL 官方把它定义为 key prefix `length`；MySQL 官方补充说明它是列前缀长度。Rev.J 应解析为符合目标版本要求的十进制正整数，并在规则允许的列类型上使用；至少不能把任意 NUMBER 当作已经证明的长度。

索引选项循环还允许多个 `USING BTREE` 和多个 COMMENT；后者会把所有 COMMENT 一次性掩码。是否允许重复必须由 TDSQL 目标版本证明，不能因为语法产生式写有 `[index_option] ...` 就默认同一个不可重复选项可无限重复。建议维护 `seen_using`、`seen_comment`。

## 15. MAJOR-X3：文档和验收材料仍有多套真相

除上一报告指出的 109/85/245 计数矛盾外，本轮完整检索还确认：

1. `_TDSQL_SHARD_METHODS` 在同一代码块定义两次；
2. `_scan_table_tail(..., want_dialect=False)` 的注释写“只验证、不产 span”，实现却始终追加并返回 dialect span；当前恢复流程实际上依赖实现行为；
3. S-1/S-2、§3.1、§3.2 门禁表、§3.4 改动汇总、风险表、施工 checklist、附录仍多次引用 `_strip_tdsql_dialect_tail()`、`_strip_unique_index_comments()`、“两个剥离器”“两阶段”；
4. 多处仍写 `USING (BTREE|HASH)`；
5. H 组同时存在 81、85、109 三套数量；
6. H4 表格把 LIST + partition ENGINE 列为 pos，紧邻说明却要求归入 H4b；
7. 风险表仍写“Rev.H 已关闭”，与 Rev.I 和本报告证据冲突。

`want_dialect=False` 的矛盾尤其危险：如果开发者照注释实现，组合语句中的方言 span 不会被掩码，恢复失败；如果照代码实现，参数名和 §5.21.1 的说明均是假的。

Rev.J 必须先确定单一行为，再全文机械清理。最终测试 case 数只能来自实际参数化清单或生成器，禁止人工在多个章节重复维护。

## 16. 端到端规则影响证据

| SQL 类别 | 当前主干 | Rev.I | 可观察影响 |
|---|---|---|---|
| 合法目标 UNIQUE COMMENT | E999 | Create | 目标修复生效 |
| 非法 `VARCHAR()` + UNIQUE COMMENT | E999 | Create，列变 TEXT | E999 被吞，规则在错误列类型上运行 |
| 重复 DEFAULT + UNIQUE COMMENT | E999 | Create | 真实语法错误被吞 |
| RANGE 分片使用 VALUES IN | Command | Create | 非法一级分片被升级为可信 AST |
| LIST 分片使用 LESS THAN | Command | Create | 同上 |
| HASH 后挂分片定义表 | Command，含 R054/R077 | Create，R054/R077 消失 | 审核结果被实质改变 |
| 两个 PARTITION BY + UNIQUE COMMENT | E999 | Create | 多分区结构门禁失效 |
| shardkey + HASH + UNIQUE COMMENT | E999，含 R054 | Create，R054 消失 | 分布声明冲突被掩盖 |
| 官方 MONTH/DAY 二级分区 + UNIQUE COMMENT | E999 | 仍 E999 | 合法 DDL未修复，R003/R004/R005/R028 连带误报仍在 |
| 官方 STATS_AUTO_RECALC/SAMPLE_PAGES + UNIQUE COMMENT | E999 | 仍 E999 | 同上 |
| 无主目标，仅 partition COMMENT + 非法类型 | E999 | Create，列变 TEXT | 隐式扩大修复范围并吞错 |

这张表证明问题不止发生在 helper 返回值，已经进入最终规则集合。

## 17. Rev.J 建议的重构顺序

为避免 A 再按样例逐个补丁，建议严格按以下顺序：

1. **先改测试 oracle**：规范负例不得 Create；baseline 只做观察；
2. **定义 TDSQL 建表尾部有限状态机**：列出批准顺序、次数和互斥；
3. **拆开一级分片与二级分区消费者**：所有消费者必须携带 method/context；
4. **建立保守列定义消费者**：覆盖生产必要子集，其他失败关闭；
5. **区分 primary target 和 auxiliary mask**：无本次主目标不恢复；
6. **建立 source/AST fingerprint**：逐字段守恒；
7. **按 TDSQL 官方清单重建表选项**；
8. **从状态机生成交叉测试**，而不是人工列几十个名字；
9. **最后统一全文、计数和 checklist**；
10. 在目标 TDSQL 5.7/8.0 双内核测试实例上执行合法/非法最小矩阵，保存错误码及 `SHOW CREATE TABLE`，解决官方文档存在版本差异的边界。

## 18. Rev.J 一次性验收矩阵

### 18.1 规范分类

- `pos_tdsql`：候选必须 Create，结构指纹一致；
- `neg_tdsql`：候选不得 Create，不以主干结果为准；
- `pos_known`：官方合法但经用户批准本版失败关闭；
- `characterization_user_decision`：锁定用户决策，不代表官方合法；
- `unsupported_unproven`：无目标版本证据，失败关闭且不冒充 neg。

### 18.2 必测生成维度

```text
入口路径: Command / except
主目标: UNIQUE COMMENT / TDSQL DISTRIBUTED / BROADCAST
辅助 mask: none / ASC / DESC / partition ENGINE / partition COMMENT
列定义: valid / missing arg / malformed arg / duplicate constraint / contradictory constraint
索引: kind / prefix / order / USING / COMMENT / duplicate option
表选项: official option / wrong value type / missing value / duplicate / wrong phase
一级分片: HASH/RANGE/LIST × operator × value × definition prefix
二级分区: RANGE/LIST × function × operator × value × definition prefix × count
声明组合: shardkey / TDSQL_DISTRIBUTED / BROADCAST / secondary partition
顺序: every allowed edge + every one-step illegal neighbor
```

### 18.3 硬断言

- 所有 neg：`candidate_ast` 不得是 Create；
- 所有 pos：无 E999，且 source/AST fingerprint 一致；
- 规则集合必须精确相等，不只断言某几条不存在；
- 恢复必须存在 primary target span；
- 所有辅助 span 必须隶属于一个已批准 primary recovery；
- 生产两份 fixture 原文回放；
- 20,000 条以上随机变异不崩溃，且任何 ACCEPT 都满足完整 fingerprint；
- `pytest --collect-only` 实际数量与文档自动同步；
- 精确依赖版本在运行时断言；
- KFN-1、ADJ-6、NG-10 使用各自类别和命名，不混入普通 pos/neg。

## 19. 已通过项与无需继续反复检查的区域

为防止下一轮又回头检查已经稳定的部分，本轮确认以下区域在当前设计边界内可以冻结：

- sqlglot tokenizer 路线；
- 等长 span 改写和越界差异校验；
- 建表头部对 CTAS、LIKE、多语句、字符串表名的保守拒绝；
- 空索引、连续逗号、尾逗号和缺数据类型的已知反例；
- `USING HASH` 对 TDSQL index_type 的失败关闭；
- DEF-1 从字符串包含改为结构化 kind 的方向；
- 精确锁定 sqlglot 30.14.0 的方向；
- 20,000 条变异下未发现 helper 未捕获异常；
- 用户已关闭/批准的 KFN-1、ADJ-6、NG-10 等决策。

下一轮应集中验证本报告 X1～X7/M1～M3 的整体重构，不需要再次争论这些冻结项。

## 20. 最终意见

本次全域审计解释了为什么前几轮会持续出现新问题：当前方案不是缺少几个 `if`，而是存在三个体系性原因：

1. 用缺陷主干当非法语法 oracle；
2. 用无上下文的通用消费者同时解析不同 TDSQL 语法域；
3. 用很弱的 AST 布尔门禁替代结构守恒。

如果继续在 Rev.I 上逐个追加反例分支，下一轮仍可能出现相邻问题。A 应先按 X1～X7 重构模型，再一次性跑生成式矩阵。完成前，Rev.I 不得进入开发。

本报告已把当前文档代码可达的恢复面、规范判据、调用路径、规则后果和测试机制统一成一张整改总表。后续 Rev.J 复审将以整张表闭环为准，不再采用“上一轮指出几条就只修几条”的方式。
