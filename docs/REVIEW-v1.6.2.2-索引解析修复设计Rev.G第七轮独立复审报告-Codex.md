# v1.6.2.2 索引解析修复设计 Rev.G 第七轮独立复审报告

| 项目 | 内容 |
|---|---|
| 复审对象 | `DESIGN-v1.6.2.2-索引类型误判与唯一索引注释解析崩溃修复详细设计说明书.md` Rev.G |
| 被审提交 | `7b72601c7411d798725bc598de5012d359b92006` |
| 复审日期 | 2026-08-26 |
| 复审人 | Codex（独立复审） |
| 复审方式 | 文档逐段核验、按 Rev.G 代码块在 detached worktree 临时施工、现主干与候选双侧对比、第六轮 W 组重建、语法单元对抗探针、MySQL 官方语法对照、sqlglot 29/30 双版本验证、现场 fixture 精确集合断言、规则覆盖核验、全量回归 |
| 是否修改产品代码 | 否；候选施工仅用于复审，未进入主分支 |
| 最终结论 | **No-Go** |

## 1. 结论摘要

Rev.G 对第六轮意见的处理有明显进展，而且不是表面改字：

1. `_strip_tdsql_dialect_tail()` 已把 `DEFAULT/CHECKSUM/INDEX DIRECTORY` 等未知或残缺表选项从“跳过”改为整体失败关闭；
2. `_strip_unique_index_comments()` 已把索引选项区从“寻找 COMMENT”改为顺序消费，只允许完整 `USING (BTREE|HASH)` 与 `COMMENT STRING`；
3. 第六轮指出的测试计数、双恢复路径断言、重复不可达代码和旧名称等问题大部分得到清理；
4. 我按 Rev.G §5.17/W 组明细独立重建 28 个场景，在 sqlglot 30.12.0 与下界 29.0.0 上均全部通过；
5. 两份生产 fixture 的 AST、列数和规则精确集合在两个依赖版本上均符合目标；
6. 临时候选全量回归 `1384 passed`，未发现既有测试回归。

因此，第六轮 BLOCK-F1/F2 所列的**具体反例已经关闭**。

但是 Rev.G 宣称的 S-2c“目标所在完整语法单元被完整消费”仍没有真正成立。第七轮发现三项新的发布阻断：

- **BLOCK-G1：UNIQUE 定义的索引选项已完整消费，但索引列清单仍然只做括号配平。** 空清单、残缺逗号、字符串键、数字键和不合规函数键都能携带 COMMENT 进入恢复；删除 COMMENT 后，sqlglot 宽松产出 `Create`，原 `E999` 消失。
- **BLOCK-G2：`PARTITION BY` 被定义为“不透明终结子句”，代码遇到后直接 `break`，其后 token 完全不校验。** `PARTITION BY` 或 `PARTITION BY DEFAULT` 这类残缺分区语法与 UNIQUE COMMENT 组合后，会从主干的 `E999` 变成成功 `Create`。
- **BLOCK-G3：表选项名称虽然白名单化，但值类型白名单过宽。** `ENGINE`、`ROW_FORMAT`、`SHARDKEY` 等被统一允许 `VAR/IDENTIFIER/STRING/NUMBER`；`ENGINE=123`、`ROW_FORMAT=123` 与 UNIQUE COMMENT、TDSQL 尾子句组合时，同样会吞掉 `E999`。

这三项都不是要求支持全部 MySQL 语法，而是要求 Rev.G 已经选择支持的语法单元必须验证完整。官方 MySQL `CREATE TABLE` 语法明确要求索引列清单由至少一个 `key_part` 组成，分区子句必须带合法分区方法与参数，`ROW_FORMAT` 也只能取规定枚举；不能仅凭 token 能配平或值“长得像某种值”就批准恢复。[MySQL 8.0 CREATE TABLE 官方语法](https://dev.mysql.com/doc/refman/8.0/en/create-table.html)

**最终结论：No-Go。** Rev.G 的“顺序消费、不能识别即失败”方向正确，不应回退；下一版应把消费边界再扩大到完整 UNIQUE key-part 列表、完整分区子句，以及按选项分别定义的值谓词。

## 2. 复审范围与验证方法

### 2.1 本轮重点

本轮没有重新争论已经由用户确定的 sqlglot tokenizer 路线，也没有重新打开已关闭的 ADJ-6。重点是：

1. 第六轮 BLOCK-F1/F2 是否真实关闭；
2. 新增 `_consume_table_option()` 是否真的只接受完整合法 atom；
3. UNIQUE COMMENT helper 是否完整验证了它声称识别的 UNIQUE 定义；
4. `PARTITION BY` 不透明终止是否破坏 S-2c/S-3；
5. 首次 `Command` 路径与异常两阶段路径是否都失败关闭；
6. 依赖上下界、现场 fixture、规则覆盖和全量回归是否稳定；
7. Rev.G 文档能否作为无歧义施工依据。

### 2.2 临时施工

我在 `7b72601` 的 detached worktree 中按 Rev.G 文档临时落地：

- 删除 `_TDSQL_DIALECT_RE`；
- 新增共享建表头定位、span 校验和 token 辅助函数；
- 新增 `_consume_table_option()` 与 Rev.G 方言尾子句剥离器；
- 新增 Rev.G UNIQUE COMMENT 剥离器；
- 改造首次 `Command` 和异常两阶段恢复链；
- 落地 DEF-1 AST `kind` 判定；
- 将两处依赖声明改为 `sqlglot>=29,<31`。

临时候选只用于复审，没有提交任何产品代码。

## 3. 第六轮问题关闭核验

### 3.1 BLOCK-F1：目标周围残缺表选项

独立重建：

```text
DEFAULT / CHECKSUM / INDEX DIRECTORY
× BROADCAST / TDSQL_DISTRIBUTED BY HASH(sk)
× 无 UNIQUE COMMENT / 有 UNIQUE COMMENT
= 12 例
```

Rev.G 结果：

- `_strip_tdsql_dialect_tail()` 均返回 0 span；
- 无 UNIQUE COMMENT 路径保留原 `Command`；
- 有 UNIQUE COMMENT 路径保留 `ast=None` 与 `E999`；
- 没有任何一例被升级为 `Create`。

**BLOCK-F1 所列场景关闭。**

### 3.2 BLOCK-F2：COMMENT 周围残缺索引选项

以下反例均返回 0 span，并保留主干错误：

```sql
UNIQUE KEY uk(id) USING COMMENT 'x'
UNIQUE KEY uk(id) COMMENT 'x' USING
UNIQUE KEY uk(id) COMMENT id
```

以下正例正常恢复：

```sql
UNIQUE KEY uk(id) USING BTREE COMMENT 'x'
UNIQUE KEY uk(id) COMMENT 'x'
```

**BLOCK-F2 所列场景关闭。**

### 3.3 W 组和依赖下界

我根据文档逐条重建 W1～W6，共 28 例：

```text
sqlglot 30.12.0：28/28 passed
sqlglot 29.0.0 ：28/28 passed
```

这说明本报告以下问题不是第六轮反例没有修，而是 Rev.G 新的完整性声明仍缺少三段语法域。

## 4. BLOCK-G1：UNIQUE key-part 列表没有被完整消费

### 4.1 最小反例

```sql
CREATE TABLE t (
  id INT,
  UNIQUE KEY uk() COMMENT 'x'
) ENGINE=InnoDB;

CREATE TABLE t (
  id INT,
  UNIQUE KEY uk(,) COMMENT 'x'
) ENGINE=InnoDB;

CREATE TABLE t (
  id INT,
  UNIQUE KEY uk('id') COMMENT 'x'
) ENGINE=InnoDB;

CREATE TABLE t (
  id INT,
  UNIQUE KEY uk(lower(id)) COMMENT 'x'
) ENGINE=InnoDB;
```

MySQL 的 `key_part` 是列名（可带前缀长度）或括号包裹的表达式，并且索引列清单是一个或多个 `key_part` 的逗号列表。空列表、只有逗号、字符串字面量，以及没有按函数索引语法额外包裹的 `lower(id)` 都不符合该结构。[MySQL CREATE TABLE 的 `key_part` 定义](https://dev.mysql.com/doc/refman/8.0/en/create-table.html)

### 4.2 发生机制

Rev.G 在识别 UNIQUE 头部后执行：

```text
找到第一个左括号
  → 只维护 d2 深度，找到配对右括号
  → 不检查括号中是否存在 key_part
  → 不检查逗号两侧是否都有 key_part
  → 不检查 token 是否为列名/前缀长度/允许的排序方向
  → 进入已收紧的 index_option 消费
  → 删除 COMMENT
```

这意味着 Rev.G 只完整消费了**索引选项区**，没有完整消费它声称识别的**UNIQUE 定义本身**。删除 COMMENT 后，sqlglot 恰好对多种非法 key-part 形态采取宽松解析并返回 `Create`，最终 AST 门禁无法发现原文错误。

### 4.3 基线与候选差异

上述空列表、逗号列表、字符串键和单层函数键在现主干均为：

```text
ast=None
parse_error=True
columns=0
rules=E999_SYNTAX_ERROR,R003,R004,R005,R028,R077
```

Rev.G 候选均变成：

```text
ast=Create
parse_error=False
columns=1
rules=R003,R005,R028,R029,R036,R037,R077
```

同类反例还包括 `uk(,id)`、`uk(id,)`、`uk(123)`。这些结果在 sqlglot 29.0.0 和 30.12.0 上一致。

### 4.4 为什么 §5.7“列缺类型”边界不能覆盖本问题

§5.7 接受的是“其他列定义被 sqlglot 宽松解析”的既有产品边界。本问题发生在 helper **主动定位并改写的同一条 UNIQUE 定义内部**。S-2c 既然宣称“目标所在完整语法单元被完整消费”，就不能只配平 key list 后把其内容交给一个已知宽松的 parser 兜底。

### 4.5 可实施整改意见

新增 `_consume_unique_key_parts()`，对列清单内部从左括号后一 token 到配对右括号前一 token 做全量消费：

1. 至少消费一个 key-part；空列表直接失败关闭；
2. 逗号只能出现在两个完整 key-part 之间；不得前导、尾随或连续；
3. 本次已证实的产品域可先限定为：`IDENTIFIER/VAR`，可选前缀长度 `(NUMBER)`，可选 `ASC/DESC`；
4. 多列按逗号重复上述 atom；
5. 函数/表达式索引若本次不准备完整支持，就明确失败关闭；若要支持，必须按官方 `(expr)` key-part 形态单独建模，不能把任意平衡括号视为合法；
6. key-part 列表和 index_option slice 都被 100% 消费后，才批准 COMMENT span。

新增测试应至少覆盖：空列表、前导/尾随/连续逗号、STRING、NUMBER、单层函数、运算表达式，以及裸列、反引号列、前缀索引、多列、ASC/DESC 正例；每个反例都要断言 `E999` 不消失。

## 5. BLOCK-G2：`PARTITION BY` 不透明终止绕过上下文完整性

### 5.1 最小反例

```sql
CREATE TABLE t (
  id INT,
  sk INT,
  PRIMARY KEY(id, sk),
  UNIQUE KEY uk(sk) COMMENT 'u'
) ENGINE=InnoDB
TDSQL_DISTRIBUTED BY HASH(sk)
PARTITION BY;

CREATE TABLE t (
  id INT,
  sk INT,
  PRIMARY KEY(id, sk),
  UNIQUE KEY uk(sk) COMMENT 'u'
) ENGINE=InnoDB
TDSQL_DISTRIBUTED BY HASH(sk)
PARTITION BY DEFAULT;
```

官方语法要求 `PARTITION BY` 后必须跟 HASH/KEY/RANGE/LIST 等完整分区方法及其参数；上面两条显然残缺。[MySQL `partition_options` 语法](https://dev.mysql.com/doc/refman/8.0/en/create-table.html)

### 5.2 发生机制

Rev.G 在 `_strip_tdsql_dialect_tail()` 中写明：

```python
if tt == TokenType.PARTITION_BY:
    break
```

因此流程为：

```text
阶段一删除 UNIQUE COMMENT
  → 候选降级为 Command
  → 阶段二完整识别并删除 TDSQL_DISTRIBUTED
  → 遇 PARTITION_BY 直接停止，不检查后续任何 token
  → sqlglot 把孤立 PARTITION BY / PARTITION BY DEFAULT 静默丢弃
  → 返回同表名 Create
  → 所有现有门禁通过，E999 消失
```

这里的 `break` 与 S-2c“未认领 token 必须整体失败”直接矛盾。分区语法复杂，可以成为保守边界，但不能成为“看见起始 token 后无条件信任剩余内容”的豁免区。

### 5.3 基线与候选差异

带 UNIQUE COMMENT 的两条反例在现主干均为：

```text
ast=None, parse_error=True, columns=0
rules=E999_SYNTAX_ERROR,R003,R004,R005,R028
```

Rev.G 候选均变成：

```text
ast=Create, parse_error=False, columns=2
rules=R005,R028,R029,R036,R037
```

无 UNIQUE COMMENT 的 `... TDSQL_DISTRIBUTED ... PARTITION BY` 在当前生产实现中已经会被旧全局正则错误恢复为 `Create`；Rev.G 没有新增这一条直接路径的问题，但也没有完成本设计声称要消除的生产危险。组合路径则是 Rev.G 新引入的 `E999 → Create`。

### 5.4 可实施整改意见

不能继续无条件 `break`。可选两种安全实现：

1. **保守方案**：遇到 `PARTITION_BY` 就整体返回空 span。该方案最安全，但会让既有 D5 合法二级分区场景停止恢复，只有用户接受这一产品边界时才能采用。
2. **推荐方案**：新增 `_consume_partition_clause()`，先只实现仓内 D5 和生产已证实的完整分区形态，从 `PARTITION_BY` 一直消费到语句结尾；任一缺方法、缺括号、空表达式、残缺 partition definition、尾随未知 token 或第二个方言声明都失败关闭。

若借助候选 AST 校验，不能只检查“存在某个 partition 节点”，还要证明原分区 token 没有被 sqlglot 部分丢弃；最稳妥的仍是对已支持语法做 token 全消费。

新增 P 组至少覆盖：裸 `PARTITION BY`、`PARTITION BY DEFAULT`、`HASH()`、`HASH(,)`、合法分区后尾随垃圾、分区区内第二个 TDSQL 声明，以及 D5 完整 RANGE 正例；全部同时覆盖无 UNIQUE 的首次路径和带 UNIQUE 的两阶段路径。

## 6. BLOCK-G3：表选项值谓词过宽

### 6.1 代码与文档不一致

Rev.G 注释把 `ENGINE`、`ROW_FORMAT`、`SHARDKEY` 描述为 `[=] VAR`，实际实现却把同一组所有选项统一交给：

```python
(TokenType.VAR, TokenType.IDENTIFIER, TokenType.STRING, TokenType.NUMBER)
```

这不是按选项定义语法，而是“只要值属于四种宽类型之一就算完整”。官方语法中，`ENGINE` 需要引擎名（可按官方规则引用），`ROW_FORMAT` 只能是 `DEFAULT/DYNAMIC/FIXED/COMPRESSED/REDUNDANT/COMPACT` 等枚举；数字不是这两个选项的合法值。[MySQL table options 官方定义](https://dev.mysql.com/doc/refman/8.0/en/create-table.html)

### 6.2 最小反例

```sql
CREATE TABLE t (
  id INT,
  sk INT,
  PRIMARY KEY(id, sk),
  UNIQUE KEY uk(sk) COMMENT 'u'
) ENGINE=123
TDSQL_DISTRIBUTED BY HASH(sk);

CREATE TABLE t (
  id INT,
  sk INT,
  PRIMARY KEY(id, sk),
  UNIQUE KEY uk(sk) COMMENT 'u'
) ROW_FORMAT=123
TDSQL_DISTRIBUTED BY HASH(sk);
```

现主干：

```text
ast=None, parse_error=True, columns=0
rules=E999_SYNTAX_ERROR,R003,R004,R005,R028
```

Rev.G 候选：

```text
ast=Create, parse_error=False, columns=2
rules=R004,R005,R028,R029,R036,R037
```

两个依赖版本结果一致。问题与第六轮相同：helper 把一个并不完整合法的普通 option 批准为上下文，删除两个目标 span 后，sqlglot 宽松返回 `Create`。

### 6.3 可实施整改意见

不要再用 `_TBL_OPT_VALUE_VAR/_NUM` 把语义不同的选项分成两个大桶。改成“选项 → 专属值谓词”的映射或显式分支：

- `ENGINE`：引擎名 token，按官方规则决定是否接受 quoted name；拒绝 NUMBER；
- `ROW_FORMAT`：精确枚举；
- `SHARDKEY`：按 TDSQL 契约只接受单标识符/已确认哨兵，不接受 STRING/NUMBER；
- `STATS_PERSISTENT`、`PACK_KEYS`、`DELAY_KEY_WRITE`：分别接受其官方枚举或 0/1，不接受任意 VAR/NUMBER；
- `AUTO_INCREMENT`、`AVG_ROW_LENGTH`、`KEY_BLOCK_SIZE`、`MAX_ROWS`、`MIN_ROWS`：接受完整数值形态；必要时增加范围约束；
- 字符集、排序规则、COMMENT 各自保留专属谓词。

每个已支持选项都应至少有：合法裸值、合法等号形态、缺值、错误 token 类型、未知枚举、前后紧邻目标、带/不带 UNIQUE COMMENT两条路径。未知或暂未建模的合法 option 继续失败关闭即可，不要求本次扩成完整 MySQL table-option parser。

## 7. 文档一致性问题

### 7.1 MAJOR-G1：Z1 双路径断言仍未按第六轮意见拆开

Rev.G 修订说明声称“所有反例断言改为按路径分别断言最终 AST 节点类型”，但 §7.1 Z1 与 G-19 仍写：

```text
span == 0 且最终仍报 E999
```

不带 UNIQUE COMMENT 的非法方法参数原本是 `Command`，没有 E999；只有异常组合路径才应保留 E999。该问题在修订说明中宣布关闭，正文却仍保留旧验收，施工者无法同时满足真实行为和文档断言。

### 7.2 MAJOR-G2：S-1 的“只在 except 内生效”已不符合设计

S-1 仍写“新逻辑只在 `except` 内”，但 Rev.G 明确同时改造首次解析得到 `exp.Command` 的路径。应改为：

- 首次得到非 Command 的成功 AST 不进入恢复；
- 首次 Command 只在严格方言 helper 返回批准 span 后重试；
- except 路径只在严格 UNIQUE helper 返回批准 span 后重试。

否则安全性论证的入口范围与真实控制流不一致。

### 7.3 MINOR-G1：第六轮 12 例结果在同一文档内有三种口径

- Rev.G 开头表格写“12 种组合全部 E999 消失，主干均报 E999”；
- §5.17 正确区分 6 条 Command 路径与 6 条 E999 路径；
- 附录 A-66 又写“其中 6 例最终结论与主干不一致”。

实际 Rev.F 的 12 条都发生最终状态变化：6 条 `Command→Create`，6 条 `E999→Create`。应统一为这一口径。

### 7.4 MINOR-G2：旧验收文字与新白名单不一致

§3.1 的旧要求仍写“只移除 COMMENT、保留 USING 等”，容易让人理解为所有其他 index option 都可跳过；Rev.G 实际只允许完整 `USING BTREE/HASH`，其他选项整体失败关闭。应更新为“只在完整消费已支持 option slice 后删除 COMMENT”。

风险表把“接纳不该接纳 AST”和“吃掉真语法错误”都评为低风险，也已被本轮实测推翻；在 BLOCK-G1～G3 关闭前应标为高风险/未关闭。

## 8. 独立测试结果

### 8.1 专项结果

| 验证项 | 结果 |
|---|---|
| 当前 sqlglot | 30.12.0 |
| 目标专项：TDSQL fallback、R077/R054、parser | **73 passed** |
| sqlglot 29.0.0：TDSQL fallback + R077/R054 | **59 passed** |
| 独立重建 Rev.G W 组 | **30.12.0：28/28；29.0.0：28/28** |
| 两份生产 fixture（30.12.0/29.0.0） | 两个版本均 AST 成功、列数一致、规则精确集合一致 |
| fixture 6309 | `R011,R018,R019,R036,R037,R061,R065,R067,R104` |
| fixture 6311 | `R036,R037` |
| `verify_rules.py` | 119 条规则、107 个规则文件、未覆盖 0；仍为既有 3 个差异，未发现候选新增差异 |

### 8.2 全量回归

```text
1384 passed, 10 warnings in 261.10s
```

warning 为既有 Pydantic 字段遮蔽、依赖弃用和 pytest fixture 弃用提示，与本方案无直接关系。

全量全绿不能推翻本轮反例：现有仓库还没有文档计划中的 160 个新增测试，更没有本轮 key-part、partition-tail 和 option-value 维度；因此现有回归只能证明已覆盖行为未退化。

## 9. Rev.H 建议整改顺序与准出门禁

### 9.1 整改顺序

1. 先明确“完整语法单元”的边界：UNIQUE 头部 + key-part 列表 + index-option slice；普通 table-option atom；分区子句。
2. 为三个边界分别实现独立 consumer，全部采用“返回下一个下标或 -1”的统一契约。
3. 所有 consumer 必须从起点顺序消费到边界终点；不得平衡后跳过内容、不得无条件 `break`、不得用大类 token 代替选项专属值谓词。
4. 最外层 helper 只负责组合 consumer、记录目标 span，不再同时承担局部语法猜测。
5. 最后再运行 AST/同表名/span 门禁；AST 门禁是最后防线，不能替代 token 语法完整性。

### 9.2 新增准出门禁

| 门禁 | 放行条件 |
|---|---|
| H-1 UNIQUE 完整消费 | key-part 列表非空、逗号结构正确、每个 key-part 属于明确白名单；option slice 也完整消费 |
| H-2 分区完整消费 | 只恢复已明确建模且从 `PARTITION BY` 到 EOF 全部消费的分区形态；残缺/尾随垃圾不生成 span |
| H-3 表选项强类型 | 每个 option 使用专属值谓词；`ENGINE=123`、`ROW_FORMAT=123` 等返回空 span |
| H-4 双路径反例 | BLOCK-G1/G2/G3 均覆盖首次 Command 与异常两阶段路径；不得出现 `E999/Command→Create` |
| H-5 依赖矩阵 | H-1～H-4 在 sqlglot 29.x 与 30.x 结果一致 |
| H-6 生产正例 | 两份 fixture、W 组、D5 分区、HASH/RANGE/LIST/BROADCAST 全部不退化 |
| H-7 文档一致性 | Z1/G-19、S-1、12 例口径、风险表和旧“保留 USING”文字统一 |
| H-8 全量工程门禁 | 新增测试、专项、fixture 精确集合、规则覆盖、全量回归全部通过 |

## 10. 最终评审结论

**结论：No-Go，不建议按 Rev.G 直接施工或发布。**

本轮必须肯定 A 的实质进展：第六轮指出的两个具体缺口已经关闭，W 组双版本实测成立，Rev.G 也终于把“不能识别就失败”写进了核心控制流。当前没有同频的地方进一步缩小为三个非常具体的边界：

```text
UNIQUE options 完整了，但 key-part 没完整；
table-option 名称完整了，但 value predicate 不完整；
PARTITION BY 之后被整体豁免，没有完整。
```

这不是继续追加三个黑名单，而是要求把 Rev.G 已经采用的 consumer 模型贯彻到底。只要下一版真正做到三个语法域的从头到尾消费，本方案就会比前六轮更接近可证明收敛；在此之前，非法 DDL 仍能静默失去 E999 并进入 119 条规则链，不能为了赶进度放行。
