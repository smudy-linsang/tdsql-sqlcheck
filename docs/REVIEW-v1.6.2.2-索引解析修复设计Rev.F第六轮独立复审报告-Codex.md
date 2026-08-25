# v1.6.2.2 索引解析修复设计 Rev.F 第六轮独立复审报告

| 项目 | 内容 |
|---|---|
| 复审对象 | `DESIGN-v1.6.2.2-索引类型误判与唯一索引注释解析崩溃修复详细设计说明书.md` Rev.F |
| 被审提交 | `597779c18e2817515c006862f6228ae6c02a8a28` |
| 复审日期 | 2026-08-26 |
| 复审人 | Codex（独立复审） |
| 复审方式 | 文档逐段核验、按 Rev.F 代码块在 detached worktree 临时施工、现主干与候选双侧对比、非法语法上下文对抗、sqlglot 上下界专项验证、现场 fixture 精确集合断言、规则覆盖核验、全量回归 |
| 是否修改产品代码 | 否；候选施工仅用于复审，未进入主分支 |
| 最终结论 | **No-Go** |

## 1. 结论摘要

Rev.F 是一次认真且有实质进展的修订。第五轮的两项发布阻断已经关闭：

1. `TDSQL_DISTRIBUTED BY HASH/RANGE/LIST(...)` 的括号体已从“只要能配平”收紧为“恰好一个裸名或反引号标识符”；空参数、字符串、表达式、函数调用和多参数均不再被批准删除。
2. 两个剥离器已经共用 `_tdsql_table_def_bounds()`；表名只接受 `VAR/IDENTIFIER`，单引号和双引号 STRING 表名不再进入恢复链；同表名比较也不再主动去掉单引号。

同时，DEF-1 用 AST 类型字段替代裸字符串搜索的方向仍然成立；两份生产现场 fixture 在 Rev.F 候选下也得到预期的精确规则集合。Rev.F 没有倒退到正则剥离，也没有破坏前五轮已经建立的长度恒等、span 差异、`Create/TABLE`、同表名和失败关闭门禁。

但是第六轮发现两项新的发布阻断：

- **BLOCK-F1：TDSQL/BROADCAST 剥离器只验证“目标子句内部”合法，没有验证它在整段表选项中的语法位置。** 当目标前后紧邻缺值的 `DEFAULT`、`CHECKSUM`、`INDEX DIRECTORY` 等不完整表选项时，Rev.F 仍会删除目标；sqlglot 又会宽松接纳删除后的残片，于是原本的 `Command` 或 `E999` 被恢复成完整 `Create`。
- **BLOCK-F2：UNIQUE COMMENT 剥离器只识别 `COMMENT + STRING`，没有验证 COMMENT 所在的完整索引选项序列。** `USING COMMENT 'x'` 或 `COMMENT 'x' USING` 中的 `USING` 都缺少 `BTREE/HASH`，原 SQL 非法；Rev.F 删除 COMMENT 后，sqlglot 却会忽略/容忍孤立 `USING` 并产出 `Create`，导致 `E999` 消失。

两项问题的共同根因不是 token 边界或 AST 门禁失效，而是当前“白名单”仍是**目标片段白名单**，还不是**目标所在完整语法单元的白名单**。候选 AST 合法只能证明“删完以后能被 sqlglot 接纳”，不能反向证明“原 SQL 除批准的兼容语法外没有其他错误”；sqlglot 对部分残缺选项存在宽松解析，更使这个反推不成立。

因此本轮结论仍为 **No-Go**。不建议推翻 Rev.F 的总体架构；应保留 tokenizer、共享头部定位、等长 span、联合差异和 AST 接纳门禁，在两个剥离器内补上“完整上下文消费”这一层，再增加跨恢复链反例。

## 2. 复审范围与方法

### 2.1 范围

本轮重点核验：

1. Rev.F 对第五轮 BLOCK-E1、BLOCK-E2 是否真实关闭；
2. DEF-1 的 AST 类型判定是否受 Rev.F 影响；
3. 两个 tokenizer 剥离器是否满足文档 S-2b、S-3 所承诺的“只删目标、无法证明即失败关闭”；
4. 两阶段恢复链在组合输入下是否会把非法 DDL 修成合法 AST；
5. 依赖上下界、现场 fixture、规则覆盖和全量回归是否与文档相符；
6. 文档能否作为无歧义、可复制的施工依据。

### 2.2 方法

我在被审提交的 detached worktree 中，按 Rev.F 文档临时落地以下候选改动：

- `_spans_only_diff()`、`_is_bare_kw()`、共享 `_tdsql_table_def_bounds()`；
- token 级 `_strip_tdsql_dialect_tail()`；
- token 级 `_strip_unique_index_comments()`；
- 首次 `Command` 恢复和异常路径两阶段联合恢复；
- DEF-1 `kind` 白名单；
- `requirements.txt`、`pyproject.toml` 的 `sqlglot>=29,<31`。

验证对象使用仓库真实 `SQLParser`、`RuleChecker`、119 条规则和两份现场 fixture。候选代码只存在于临时工作树，本报告没有修改项目产品代码。

## 3. 原始两项生产问题与 Rev.F 处理机制

### 3.1 DEF-1：R054 假 UNIQUE

#### 3.1.1 发生原因

现有 `_parse_index_constraint()` 把整个索引 AST 转成字符串，再搜索 `PRIMARY`、`UNIQUE`、`FULLTEXT` 子串。索引名或列名只要含 `unique`，普通 KEY 就可能被错标为 UNIQUE：

```sql
KEY `kcfb_list_info_idx13` (`list_unique_num`,`lgl_pern_code`)
```

错误随后传入 R054：普通索引被当作唯一索引检查；而 `seen=True` 还可能抑制 raw SQL 兜底，使真正 UNIQUE 索引漏检。因此它不是单一误报，而是“假索引类型污染后续规则”的结构性问题。

#### 3.1.2 Rev.F 处理机制与结论

Rev.F 读取 `IndexColumnConstraint.args["kind"]`，只对白名单值作类型映射，其余回到 NORMAL。普通 KEY/INDEX 的 `kind=None`，FULLTEXT 保持 FULLTEXT；UNIQUE、PRIMARY 继续由专用 AST 节点路径处理，SPATIAL 按用户已经确认的产品决策保持 NORMAL。

独立施工后，生产 fixture `report_6309_kcfb_list_info.sql` 的规则集合从含假 `R054` 收敛为：

```text
R011,R018,R019,R036,R037,R061,R065,R067,R104
```

相对基线只删除错误的 R054，没有新增或误删其他规则。**DEF-1 本轮通过。**

### 3.2 DEF-2：唯一索引 COMMENT 导致解析崩溃

#### 3.2.1 发生原因

sqlglot 29/30 的 MySQL parser 无法接纳目标 UNIQUE 索引 COMMENT 形态。解析异常后，当前产品只做表名正则回退，列、主键、引擎、字符集和注释等结构事实全部为空；R003/R004/R005/R028 随后把“解析器没有拿到”误解为“SQL 没有声明”，造成 `E999` 与多条结构规则一起误报。

#### 3.2.2 Rev.F 处理机制与结论

Rev.F 使用 sqlglot tokenizer，只把目标 UNIQUE 定义项中真实的 `COMMENT + STRING` span 等长置空后重试；若候选又因 TDSQL 方言尾子句降级为 `Command`，再调用 TDSQL token 剥离器，并用两阶段 span 并集校验原文到最终候选的全部差异。最终候选还必须满足 `exp.Create`、`kind=TABLE` 和同表名。

生产 fixture `report_6311_biz_tx_log.sql` 在候选下不再解析失败，规则集合精确为：

```text
R036,R037
```

这证明 Rev.F 能修复目标现场语句，但本轮 BLOCK-F2 表明它尚未证明对目标语法周边的非法输入安全。**DEF-2 的主路径有效，安全边界未闭合。**

## 4. 第五轮问题关闭核验

| 第五轮问题 | 第六轮独立结果 | 结论 |
|---|---|---|
| BLOCK-E1：方法括号体只配平、不校验单标识符 | `HASH()`、`HASH(,)`、`HASH('sk')`、`HASH("sk")`、`HASH(sk+1)`、`HASH(lower(sk))`、`HASH(a,b)` 均不产生批准 span；与 UNIQUE COMMENT 组合时原错误状态保留 | **关闭** |
| BLOCK-E2：STRING 表名被表名定位和同名门禁接纳 | 单引号/双引号表名均无法通过共享头部定位；与 UNIQUE COMMENT、HASH 组合也保留原错误 | **关闭** |
| 两个剥离器头部语义可能漂移 | 均调用 `_tdsql_table_def_bounds()`，`_IDENT_TOKENS=(VAR,IDENTIFIER)` | **关闭** |
| 合法参数被收紧过头风险 | HASH/RANGE/LIST 的裸名、反引号名，以及 BROADCAST、限定表名、`IF NOT EXISTS`、`TEMPORARY` 均可恢复 | **未发现倒退** |
| 依赖范围不确定 | 两处声明均为 `sqlglot>=29,<31`；当前 30.12.0 可用，29.0.0 专项也通过 | **关闭** |

这里需要澄清 Rev.F 的 Z1 验收措辞：不带 UNIQUE COMMENT 的非法参数通常不会抛 `E999`，而是保留原 `Command`；只有组合到异常恢复路径时才应断言保留 `E999`。正确验收应分成“直接路径保留原 Command”和“组合路径保留 E999”两套断言，不能把两者合并成一句。

## 5. BLOCK-F1：方言目标的表选项上下文未验证

### 5.1 最小复现

以下每条 SQL 中，`BROADCAST` 或 `TDSQL_DISTRIBUTED BY HASH(sk)` 单独看都符合目标内部白名单，但它们被放在需要参数的不完整表选项之后：

```sql
CREATE TABLE t (
  id INT,
  sk INT,
  PRIMARY KEY(id, sk),
  UNIQUE KEY uk(sk) COMMENT 'u'
) ENGINE=InnoDB DEFAULT BROADCAST;

CREATE TABLE t (
  id INT,
  sk INT,
  PRIMARY KEY(id, sk),
  UNIQUE KEY uk(sk) COMMENT 'u'
) ENGINE=InnoDB CHECKSUM TDSQL_DISTRIBUTED BY HASH(sk);
```

`DEFAULT` 没有完成 `DEFAULT CHARACTER SET/CHARSET/COLLATE...` 等选项，`CHECKSUM` 没有值。`INDEX DIRECTORY` 没有路径值时也能触发同类问题。把目标放在这些残缺选项之前，同样可以复现。

### 5.2 发生机制

`_strip_tdsql_dialect_tail()` 从建表定义右括号之后进行顶层线性扫描：

1. 遇到 `TDSQL_DISTRIBUTED` 时只验证 `BY + 方法 + (单标识符)`；
2. 遇到 `BROADCAST` 时只排除前一 token 为 `=` 的情况；
3. 对目标之前和之后的其他 token 一律跳过；
4. 删除目标后，让 sqlglot 再判断剩余文本是否为 `Create`。

这个流程验证了目标内部形态，却没有证明目标处于一个完整表选项边界。删除目标后，sqlglot 会把某些孤立的 `DEFAULT`、`CHECKSUM` 或 `INDEX DIRECTORY` 容忍、截断或丢弃，最终仍返回 `Create`。随后长度恒等、span 差异、`Create/TABLE` 和同表名门禁都会通过，因为这些门禁验证的是“删了什么”和“删完得到什么”，并不验证“没删的上下文是否原本完整”。

两阶段组合路径如下：

```text
原 SQL：UNIQUE COMMENT + 不完整表选项 + 合法外观方言目标
  → 初次解析抛错
  → 阶段一删除 UNIQUE COMMENT
  → 候选降级为 Command
  → 阶段二把合法外观方言目标批准为 span 并删除
  → sqlglot 宽松接纳剩余的不完整表选项，返回 Create
  → 联合 span / Create / TABLE / 同表名门禁全部通过
  → E999 消失，columns/primary key 等结构被当成可信事实
```

没有 UNIQUE COMMENT 时，首次 `Command` 恢复路径也存在同源问题：原 `Command` 会被替换成 `Create`。

### 5.3 基线与 Rev.F 候选对比

六个代表场景的结果完全一致：

| 不完整上下文 | 方言目标 | 现主干基线 | Rev.F 候选 |
|---|---|---|---|
| `DEFAULT` | `BROADCAST` | `ast=None, parse_error=True, columns=0` | `ast=Create, parse_error=False, columns=2` |
| `CHECKSUM` | `BROADCAST` | 同上 | 同上 |
| `INDEX DIRECTORY` | `BROADCAST` | 同上 | 同上 |
| `DEFAULT` | `TDSQL_DISTRIBUTED BY HASH(sk)` | 同上 | 同上 |
| `CHECKSUM` | 同上 | 同上 | 同上 |
| `INDEX DIRECTORY` | 同上 | 同上 | 同上 |

基线规则集合为：

```text
E999_SYNTAX_ERROR,R003,R004,R005,R028
```

Rev.F 候选变成：

```text
R005,R028,R029,R036,R037
```

关键不是规则数增减，而是 `E999_SYNTAX_ERROR` 被错误消除、非法输入被升级为可信 AST。针对 30 种常见表选项名做前后邻接探测后，至少 `DEFAULT`、`CHECKSUM`、`INDEX DIRECTORY` 在目标前后均形成稳定反例，说明这不是一个孤立字符串，而是整类上下文缺口。

### 5.4 风险判断

这是发布阻断，理由如下：

1. 违反文档 S-3“绝不把非法 DDL 修成合法”；
2. 同时影响首次 `Command` 路径和 UNIQUE COMMENT 异常组合路径；
3. `E999` 消失后，下游无法知道 AST 是通过删除歧义上下文得到的；
4. 当前 AST 门禁无法补救，因为问题发生在 AST 形成之前；
5. 用现有正例、fixture 或全量回归无法发现，必须有专门的上下文反例。

### 5.5 可实施整改意见

不得只追加 `DEFAULT/CHECKSUM/DIRECTORY` 黑名单。那只能修掉当前举出的字符串，不能建立设计需要的安全性质。

建议把定义右括号到语句结尾/分区子句之间的**整个表选项区**解析为有限状态的完整 option atom 序列：

1. 先确定产品实际需要恢复的 TDSQL `SHOW CREATE TABLE` 语法域；
2. 对域内普通表选项建立“关键字、可选等号、必选值、值 token 类型”的精确白名单，例如 `ENGINE[=]标识符`、`DEFAULT? CHARSET[=]标识符`、`COLLATE[=]标识符`、`COMMENT[=]STRING` 等；
3. 把 `TDSQL_DISTRIBUTED BY METHOD(identifier)` 或 `BROADCAST` 当作一个独立完整 atom；
4. 从表选项区起点开始顺序消费，要求目标前缀和目标后缀都能被完整 atom 序列消费；
5. 任一未知 token、缺值、重复冲突声明或无法消费的残片，都使整个 helper 返回 `None, [], ""`；
6. 对设计已经允许的目标后续形态（如 PARTITION、BROADCAST 后的合法 COMMENT/SHARDKEY 形态）建立明确状态和正例，避免修复过度收紧。

如果团队不希望实现完整 MySQL 表选项语法，可以采取更保守的产品白名单，只覆盖两份现场 DDL和已确认的 TDSQL `SHOW CREATE TABLE` 组合；不在白名单中的合法 SQL继续保留原 `Command/E999`，也比把非法 SQL 恢复为可信 AST安全。

新增测试至少应包含：

- 目标前置/后置 `DEFAULT`、`CHECKSUM`、`INDEX DIRECTORY` 的残缺形态；
- BROADCAST 与 HASH/RANGE/LIST 各一组；
- 无 UNIQUE COMMENT 的首次 `Command` 路径，以及有 UNIQUE COMMENT 的两阶段路径；
- 每个反例同时断言 span 为 0、AST/parse_error 与改前一致、`E999` 不消失；
- 完整 `DEFAULT CHARSET`、`CHECKSUM=0/1`、`INDEX DIRECTORY='path'` 与目标组合的正例，防止误伤。

## 6. BLOCK-F2：UNIQUE COMMENT 的索引选项上下文未验证

### 6.1 最小复现

```sql
CREATE TABLE t (
  id INT,
  UNIQUE KEY uk(id) USING COMMENT 'target'
) ENGINE=InnoDB;

CREATE TABLE t (
  id INT,
  UNIQUE KEY uk(id) COMMENT 'target' USING
) ENGINE=InnoDB;
```

两条语句的 `USING` 都缺少必选的索引类型（如 BTREE/HASH），原 SQL 应失败。第一条把残缺项放在 COMMENT 前，第二条放在 COMMENT 后。

### 6.2 发生机制

当前 `_strip_unique_index_comments()` 在匹配 UNIQUE 头部并跳过完整列清单后，循环扫描到逗号或定义列表右括号：

- 看见 `COMMENT + STRING` 就记录并删除；
- 看见其他 token 就简单 `j += 1`；
- 看见括号就只做配平跳过；
- 不校验 COMMENT 前后是否为完整、允许的 index_option 序列。

因此不完整 `USING` 不会导致 helper 失败。COMMENT 被删除后，sqlglot 对孤立 USING 的宽松处理又恰好产出 `Create`，后续门禁全部通过。

### 6.3 基线与候选对比

两条反例在现主干基线均为：

```text
ast=None
parse_error=True
columns=0
rules=E999_SYNTAX_ERROR,R003,R004,R005,R028,R077
```

Rev.F 候选均变成：

```text
ast=Create
parse_error=False
columns=1
rules=R003,R005,R028,R029,R036,R037,R077
```

即使 R077 仍可能存在，语法错误 `E999` 已经被错误清除，整个结构被标记为可解析。该行为直接违反失败关闭要求。

### 6.4 可实施整改意见

在每个目标 UNIQUE 定义项中，跳过列清单后不能再“扫描寻找 COMMENT”，而应对到逗号/定义列表右括号之间的**完整 index_option slice** 做顺序消费：

1. 明确本次恢复域：至少支持纯 `COMMENT STRING`，以及已经有正例要求的完整 `USING (BTREE|HASH)` 与 `COMMENT STRING` 组合；
2. `USING` 后必须立即消费一个白名单索引类型；缺失、字符串、表达式或未知类型一律失败关闭；
3. `COMMENT` 后必须恰好是 STRING；目标 COMMENT 的次数必须符合语法域，重复 COMMENT 不得同时删除后侥幸成功；
4. 对本次未声明支持的 `VISIBLE/INVISIBLE`、`KEY_BLOCK_SIZE`、`WITH PARSER` 等选项，除非完整实现并有正反例，否则整个定义项不进入恢复；
5. 只有 option slice 从头到尾被白名单完全消费，才批准 COMMENT span；不能出现“跳过未知 token 继续找 COMMENT”。

新增测试至少应覆盖：

- `USING COMMENT 'x'`、`COMMENT 'x' USING`；
- `USING BTREE COMMENT 'x'` 和产品允许的相反顺序正例；
- 重复 COMMENT、COMMENT 缺 STRING、COMMENT 后非 STRING；
- 未支持选项在 COMMENT 前后两侧；
- 与合法/非法 TDSQL 尾子句组合后的两阶段路径；
- 反例断言原异常、`E999` 和空 AST 状态均不被消除。

## 7. 文档一致性与可施工性问题

### 7.1 MAJOR-F1：测试总数和门禁基数自相矛盾

§7.1 实际列出的 Z 组是：

```text
Z1 7 + Z2 8 + Z3 3 + Z4 4 = 22
```

但正文写成 Z4 3 例、Z 组合计 21 例，并据此把新增总数写成 127、全量预期写成 1482。附录 A-62 又正确写了 Z4 4 例。按当前明细，新增总数应为 128；若其余基线不变，则全量预期应相应增加 1。

这会造成测试遗漏或验收数字无法同时满足。必须以逐条参数化 case 为唯一计数源，统一 §7.1、G-1、G-5、G-19、C-11 和附录 A-62。

### 7.2 MAJOR-F2：Z1 的最终结果断言混淆两条恢复路径

文档写“Z1 span==0 且最终仍报 E999”。实际是：

- 不带 UNIQUE COMMENT：非法方法参数不产生 span，sqlglot 保留 `Command`，通常没有 `parse_error/E999`；
- 带 UNIQUE COMMENT：初次 ParseError，方法参数又不得在第二阶段被删除，最终才应保留 E999。

应拆成两个参数组，分别断言“原 Command 不被换成 Create”和“原 E999 不消失”。否则施工者可能为了满足错误断言改变产品语义，或者写出不真正进入目标路径的测试。

### 7.3 MINOR-F1：逐字施工代码块含两段不可达重复代码

§3.2 标为“改动后（逐字照抄）”的代码块，在第一次 `return parsed` 之后又重复了两遍相同的表名回退与 `return parsed`。它们编译可过但永久不可达，会污染正式补丁并使审阅者误判控制流。应只保留一份失败路径。

### 7.4 MINOR-F2：残留旧名称、旧版本标签和格式错误

- §2.2 与改动清单仍出现已废弃 `_tdsql_table_def_end()`，应统一为 `_tdsql_table_def_bounds()`；
- §5.1 标题重复拼接为 `### 5.1 引擎指纹与解析产物## 5.1...`；
- 多处结果表仍写“Rev.B”，§5.13 也沿用旧标签，无法区分当前证据属于哪个修订版；
- `_strip_tdsql_dialect_tail()` 中 `if not (i + 5 < n + 1)` 是冗余且误导性的边界判断，真正约束由后续逐项 `i+k<n` 完成，应删除；
- 改动量仍写旧 helper 名和旧行数，施工前应重新按最终代码核算。

这些不单独改变产品结论，但设计被标记为可复制施工文档时必须清理。

## 8. 独立验证结果

### 8.1 专项与依赖验证

| 验证项 | 结果 |
|---|---|
| 当前环境 sqlglot | 30.12.0 |
| Rev.F 目标专项：`test_parser_tdsql_dialect_fallback.py`、`test_r077_r054_tdsql_syntax.py`、`test_parser.py` | **73 passed** |
| sqlglot 29.0.0 下两组核心目标测试 | **59 passed** |
| 非法方法参数/STRING 表名对抗 | 第五轮两项阻断均已关闭 |
| 两份生产 fixture | 解析成功，规则集合与设计目标精确一致 |
| `verify_rules.py` | 119 条规则、107 个规则文件、未覆盖 0；仍为既有 3 个断言差异 `R023_01/R098_01/R116_01` 缺 `R036/R037`，未发现本候选新增差异 |

### 8.2 全量回归

按 Rev.F 临时施工后的全量结果：

```text
1384 passed, 10 warnings in 265.11s
```

没有既有测试由通过变失败。warning 均为既有模型字段遮蔽、依赖弃用或 pytest fixture 弃用提示，与本设计无直接关系。

### 8.3 为什么全量全绿仍不能放行

全量回归回答的是“现有测试描述的行为是否被破坏”，不能证明“未被枚举的非法上下文一定失败关闭”。BLOCK-F1/F2 都利用 sqlglot 对残缺 option 的宽松接纳；现有 X/Y/Z 主要枚举目标 token 自身、表名和多声明，没有对目标前后完整 option grammar 做笛卡尔交叉。因此 `1384 passed` 与两项阻断并不矛盾。

## 9. 第七版整改与放行门禁

建议 Rev.G 只做结构性收口，不再围绕反例添加局部条件：

1. 保留 Rev.F 已验证的共享头部定位、token 类型白名单、参数单标识符、等长 span、联合差异、`Create/TABLE` 和同表名门禁；
2. TDSQL helper 从“扫描寻找目标”改为“完整消费表选项区，在合法 atom 边界识别目标”；
3. UNIQUE helper 从“扫描寻找 COMMENT”改为“完整消费该 UNIQUE 定义的 index_option slice”；
4. 任一未消费 token、缺值 option、未知 option、重复冲突或 tokenizer 异常一律返回空 span；
5. 新增 W 组上下文测试，分别覆盖首次 `Command` 和异常两阶段恢复；
6. 把反例的验收从单一 `span==0` 提升为四重断言：原 AST 类型不被升级、原 parse_error/E999 不消失、原结构字段不被伪造、原规则集合不因恢复而丢失语法错误；
7. 修正文档计数、路径结果措辞、重复代码和旧标签；
8. 在依赖下界 29.x 与当前 30.x 各执行新增上下文组、两份 fixture 精确集合、规则覆盖和全量回归。

建议新增门禁：

| 门禁 | 放行条件 |
|---|---|
| G-21 表选项完整消费 | 方言目标前后所有 token 都属于并完整满足已声明 option grammar；未知/残缺上下文 span=0 |
| G-22 索引选项完整消费 | UNIQUE 列清单后的 option slice 100% 被白名单消费；孤立 USING、重复 COMMENT、未知 option 失败关闭 |
| G-23 双路径一致 | 每个非法上下文同时覆盖无 UNIQUE 的首次路径和带 UNIQUE 的两阶段路径 |
| G-24 结果保持 | 反例在修复前后的 AST/parse_error/E999/结构字段不被“升级” |
| G-25 正例不退化 | 现场两份 DDL、合法 USING BTREE+COMMENT、合法表选项+TDSQL/BROADCAST 均恢复成功 |
| G-26 依赖矩阵 | sqlglot 29.x 与 30.x 的 G-21~G-25 结果一致 |

## 10. 最终评审结论

**结论：No-Go，不建议按 Rev.F 直接开发或发布。**

Rev.F 已经认真解决第五轮的参数体和 STRING 表名问题，整体方案也比前版更集中、更易审计。当前尚未同频的核心并不是对 tokenizer 路线、AST 门禁或产品目标存在分歧，而是安全不变量的粒度仍差一层：A 证明了“要删除的目标本身合法”，而发布级失败关闭还必须证明“目标所在完整语法单元也合法”。

下一版无需推倒重来。只要两个剥离器都从局部搜索升级为完整 option slice 消费，并用本报告的前后邻接、双恢复路径反例锁住边界，方案就有机会从“能修现场问题”提升到“能证明不会把相邻非法语法洗成合法 AST”。在此之前，为避免语法错误静默消失和错误结构事实进入 119 条规则链，本轮不能放行。
