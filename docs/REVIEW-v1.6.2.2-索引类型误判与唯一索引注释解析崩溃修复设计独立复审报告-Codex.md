# v1.6.2.2 索引类型误判与唯一索引注释解析崩溃修复设计独立复审报告

## 0. 文档信息

| 项目 | 内容 |
|---|---|
| 评审对象 | `DESIGN-v1.6.2.2-索引类型误判与唯一索引注释解析崩溃修复详细设计说明书.md`（Rev.A） |
| 问题版本 | v1.6.2.1 |
| 目标版本 | v1.6.2.2 |
| 评审日期 | 2026-08-25 |
| 独立评审人 | Codex（独立复现、独立构造边界样例、独立执行回归） |
| 评审基线 | `main@e6309d8`；`parser_legacy.py` 与设计基线 `29a0786` 比对无漂移 |
| 输入证据 | 用户提供的 gg77、gg78 原始 HTML 审核报告；仓库内两份同源生产 DDL fixture；当前解析器、规则实现与 Rev.A 方案 |
| 代码变更范围 | 本次只评审，不修改项目代码；候选补丁仅在临时 detached worktree 中验证 |

## 1. 评审结论

### 1.1 总结论：**Rev.A 暂不通过，必须修订后再实施**

两个问题的事实、根因和期望结果均成立：

1. **DEF-1（R054 假 UNIQUE）根因正确。** 普通索引的名称或列名中含有 `unique`，被 `str(col_def).upper()` 的裸子串判断误标为 `UNIQUE`。这不仅造成 R054 误报，还会让 R054 的结构化索引分支认为已经发现 UNIQUE，从而跳过原始 SQL 兜底扫描，存在掩盖真唯一索引的风险。
2. **DEF-2（唯一索引 COMMENT 导致解析崩溃）根因正确。** 当前 sqlglot 对本报告中的 `UNIQUE KEY ... COMMENT '...'` 不能完整解析，异常路径把列、主键、引擎、字符集和表注释等结构化信息全部留空，继而产生 E999、R003、R004、R005、R028 等连锁误报。
3. **DEF-1 的修复方向可以采纳；DEF-2 采用“受控预处理后重试完整 AST”的架构方向也可以采纳。**
4. **Rev.A 的 DEF-2 正则实现不能采纳。** 独立测试已证明它既漏掉多种合法 MySQL/TDSQL 索引写法，又会误命中普通字符串字面量中的伪 SQL，并在重试成功时静默改变结构化字段。该风险直接违背“不能产生次生灾害”的上线要求。

因此，本次评审不是推翻 A 对问题的分析，也不主张改走不完整的正则兜底方案 B；结论是：**保留 DEF-1 和 DEF-2-A 的总体方向，将 DEF-2 的“全局正则替换”改为有词法状态、作用域受限、失败关闭的预处理器，补齐门禁和回归用例后再复审。**

### 1.2 分项裁决

| 项目 | 裁决 | 说明 |
|---|---|---|
| DEF-1 根因 | 通过 | 独立复现与 AST 枚举均支持 |
| DEF-1 使用 AST `kind` 代替字符串包含 | 通过 | 能消除假 UNIQUE/FULLTEXT/PRIMARY |
| 删除 PRIMARY/UNIQUE 分支的“结构上不可达”论证 | 当前版本范围内通过，但需加兼容护栏 | 3 个 sqlglot 版本、32 类语法均无反例；依赖却未锁上限 |
| SPATIAL 暂维持 NORMAL | 通过 | 本次热修以输出域不变为优先，需明确是兼容取舍而非语义结论 |
| DEF-2 选择 A（预处理后重试） | 通过 | 保留完整 AST，优于不完整兜底字段 |
| DEF-2 当前剥离正则 | **阻断** | 合法语法漏匹配，并已证实会误改字符串内容 |
| DEF-2 当前重试接纳条件 | **阻断** | 只判断 `exp.Create`，作用域与同一性验证不足 |
| 不启用方案 B / ADJ-10 本次不修 | 通过 | 当前兜底函数不感知字符串，直接启用会引入新的漏报风险 |
| §2.3 “零影响”安全性论证 | 需重写 | 仅能证明成功解析路径不进入新逻辑，不能证明异常输入无语义污染 |

## 2. 独立复现结果

### 2.1 附件与仓库 fixture 一致性

对两份 HTML 中的原始 DDL 进行提取，并去除 fixture 文件用于标识来源的文件头注释后逐字符比较：

| 报告 | 表 | 仓库 fixture | 比较结果 | 审核规则集 |
|---|---|---|---|---|
| gg77 / #6309 | `kcfb_list_info` | `tests/fixtures/report_6309_kcfb_list_info.sql` | 完全一致 | 分布式 |
| gg78 / #6311 | `biz_tx_log` | `tests/fixtures/report_6311_biz_tx_log.sql` | 完全一致 | 集中式 |

这意味着本次可以用仓库 fixture 做稳定、可重复的生产问题回放，不需要手工构造替代表。

### 2.2 DEF-1：R054 假 UNIQUE

v1.6.2.1 当前行为：

- `kcfb_list_info_idx13` 是普通索引；
- 因索引列名包含 `unique`，`_parse_index_constraint()` 将其标记为 `UNIQUE`；
- R054 随后错误提示该“唯一索引”未包含分片键 `black_list_seq_num`。

应用 Rev.A 的 DEF-1 候选逻辑后：

- 该索引类型恢复为 `NORMAL`；
- gg77 原有规则结果中**仅 R054 消失**；
- 其余 R011、R018、R019、R036、R037、R061、R065、R067、R104 保持不变。

这符合“只消除假 UNIQUE 误报、不顺带改变其他审核结论”的目标。

### 2.3 DEF-2：唯一索引注释导致解析崩溃

v1.6.2.1 当前行为：

- `biz_tx_log` 解析失败；
- `columns=0`、`has_primary_key=False`、`engine=None`、`charset=None`；
- 最终产生 `E999_SYNTAX_ERROR`、R003、R004、R005、R028 共 5 项假违规。

应用 Rev.A 的基础样例剥离与重试后：

- 解析成功，识别 75 列、主键、`INNODB`、`UTF8MB4` 和原表注释；
- gg78 按报告原有的**集中式**规则集重放后，仅保留真实的 R036、R037；
- `parsed.raw_sql` 与输入 SQL 完全一致。

因此，A 选择“只为解析生成内部副本，审核证据仍保留原始 SQL”的方向是正确的。

## 3. 对 A 指定五个重点问题的逐项答复

### 3.1 §2.1：PRIMARY/UNIQUE 在 `IndexColumnConstraint` 中是否结构上不可达

#### 独立验证方法

在以下三个 sqlglot 版本上分别解析 32 类索引/约束写法：

- 依赖声明允许的最低版本：26.0.0；
- 当前工作环境实际版本：30.12.0；
- A 文档声明的验证版本：30.14.0。

覆盖：

- `KEY` / `INDEX`，有名/无名；
- `USING` 位于列清单前/后；
- 前缀索引、降序索引、多列索引；
- FULLTEXT、SPATIAL；
- UNIQUE KEY、UNIQUE INDEX、无名 UNIQUE；
- PRIMARY KEY；
- `CONSTRAINT ... UNIQUE/PRIMARY/FOREIGN`；
- 列内联 UNIQUE/PRIMARY；
- 索引名和列名分别包含 `unique`、`primary`、`fulltext`。

#### 结果

未发现任何合法样例让 UNIQUE 或 PRIMARY 落入外层 `IndexColumnConstraint`：

| SQL 结构 | 外层 AST 节点 |
|---|---|
| 普通、FULLTEXT、SPATIAL 表级索引 | `IndexColumnConstraint`；`kind` 分别为 `None`、`FULLTEXT`、`SPATIAL` |
| 表级 UNIQUE | `UniqueColumnConstraint` |
| 表级 PRIMARY KEY | `PrimaryKey` |
| 带 `CONSTRAINT` 的约束 | `Constraint` |
| 列内联 UNIQUE/PRIMARY | `ColumnDef` |

**裁决：A 的“当前受测版本中结构上不可达”论证成立，没有找到推翻 DEF-1 的反例。**

#### 仍需增加的兼容护栏

仓库当前依赖是 `sqlglot>=26.0.0` / `sqlglot>=26.0`，没有上限锁定。今天的 AST 可达性不是对未来所有 sqlglot 版本的永久保证。建议把 DEF-1 写成下列等价但更抗漂移的映射：

```python
kind = (col_def.args.get("kind") or "").upper()
idx_type = kind if kind in {"PRIMARY", "UNIQUE", "FULLTEXT"} else "NORMAL"
```

该写法在当前 AST 上与 Rev.A 输出完全相同，不恢复任何裸字符串判断；如果未来 sqlglot 把 PRIMARY/UNIQUE 放入该节点，也不会静默降级为 NORMAL。同时必须保留一组 AST 契约测试，或明确锁定并升级验证 sqlglot 版本。

### 3.2 §3.1：剥离正则边界

对 Rev.A 原样正则执行 14 类定向鉴别。A 特别提出的四类样例结果如下：

| 样例 | 结果 |
|---|---|
| 注释使用 SQL 标准双单引号：`COMMENT 'owner''s'` | 通过 |
| 列清单与 COMMENT 之间换行 | 通过 |
| 多个 UNIQUE 索引各带注释 | 通过（简单列清单） |
| 注释正文包含 `)` 或 `unique` | 通过 |

但扩展到 MySQL 合法边界后出现以下失败：

| 边界 | Rev.A 结果 | 风险 |
|---|---|---|
| 反斜杠转义单引号：`COMMENT 'owner\'s index'` | 错误截断注释，重写 SQL 仍解析失败 | 生产合法语法仍报 E999 及连锁误报 |
| 前缀索引：`UNIQUE KEY uk (a(20)) COMMENT 'x'` | `\([^()]*\)` 不匹配 | 合法常见索引仍无法修复 |
| 函数索引：`UNIQUE KEY uk ((lower(a))) COMMENT 'x'` | 不匹配 | MySQL 8.0 合法语法仍无法修复 |
| 转义反引号索引名：``UNIQUE KEY `uk``name` ...`` | 不匹配 | 合法标识符仍无法修复 |
| 其他索引选项，如 `VISIBLE`/`INVISIBLE` | 剥离后 sqlglot 仍可能失败 | “匹配到”不等于“重试可恢复” |

MySQL 官方语法明确允许索引列前缀、索引选项以及字符串中的反斜杠转义；MySQL 8.0 还支持函数键值部分和可见/不可见索引。参考：

- [MySQL 5.7 CREATE TABLE](https://dev.mysql.com/doc/refman/5.7/en/create-table.html)
- [MySQL 8.0 CREATE INDEX](https://dev.mysql.com/doc/refman/8.0/en/create-index.html)
- [MySQL 8.0 String Literals](https://dev.mysql.com/doc/refman/8.0/en/string-literals.html)
- [MySQL 8.0 Invisible Indexes](https://dev.mysql.com/doc/refman/8.0/en/invisible-indexes.html)

更严重的是，这不是简单“再补几个正则分支”能安全解决的问题。Rev.A 正则不知道自己是否位于字符串字面量内，会跨越 SQL 语义边界误替换。

以下样例同时含有一个真实的 UNIQUE 索引注释和一段列注释中的伪 SQL：

```sql
CREATE TABLE t (
  a VARCHAR(255) NOT NULL COMMENT 'a',
  b VARCHAR(255) COMMENT 'mentions UNIQUE KEY fake (a) COMMENT ''nested''',
  PRIMARY KEY (a),
  UNIQUE KEY uk (a) COMMENT 'real index comment'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='table';
```

Rev.A 正则命中两处而不是一处；重试能够成功，但 `b` 的结构化列注释被静默改成：

```text
mentions UNIQUE KEY fake (a)nested
```

`raw_sql` 虽然仍是原文，`column_comments` 已经被污染。这是一个已经复现的次生灾害：当前 R029 主要检查注释是否存在，可能暂时不暴露内容变化，但后续规则、报告展示或元数据消费完全可能使用被篡改的结构化值。

**裁决：当前正则必须废弃，不能通过扩大正则继续打补丁。**

### 3.3 §2.3：三条安全性质是否成立

| Rev.A 性质 | 复审结论 | 准确表述 / 缺口 |
|---|---|---|
| “只在已经抛错的语句上生效，因此对当前能解析的一切语句零影响” | **狭义成立，不足以证明整体安全** | 同一运行时、同一输入下，首次成功解析的路径确实不进入 except；但合法而恰好不被 sqlglot 支持的 SQL 正是异常路径的主体，不能由此推出异常路径无误改、无误接纳 |
| “重试失败则保持原失败行为” | **仅在没有接纳候选 AST 时成立** | 原始异常可能被保留，但原始 SQL 中的非目标字符串可能先被误改；一旦错误改写后的 SQL 恰好能解析，就会接纳被污染的 AST |
| “`raw_sql` 保持原文” | 成立 | 本轮所有样例均验证原文未被覆盖；但这不能替代对 AST 派生字段完整性的证明 |

所以，§2.3 第一条只能改写为：

> 新逻辑不会改变同一 sqlglot 运行时下首次解析成功语句的控制流与结果；对首次解析失败语句的安全性，必须由作用域受限的预处理、候选 AST 接纳门禁及负向测试单独保证。

不能再使用“对当前能解析的一切语句零影响”来承重整个爆炸半径论证。

### 3.4 SPATIAL 是否应单独成型

**同意本次维持 `SPATIAL -> NORMAL`，不把它列为阻断项。**

理由：

1. 本次修复目标是消除假 UNIQUE 与恢复完整解析，不是扩展索引类型模型；
2. 当前消费者对 PRIMARY/UNIQUE 有特殊审核语义，FULLTEXT 已有既有输出，而 SPATIAL 没有对应规则分支；
3. 新增 `SPATIAL` 会扩大内部输出枚举和下游兼容面，与热修“输出域不变”的约束冲突。

但文档应把 NG-6 表述为“兼容性保守映射”，而不是断言 SPATIAL 在语义上就是普通索引。后续如新增空间索引规则，再通过独立版本扩展模型与消费者。

### 3.5 DEF-2 只做 A、不做 B 是否合理

**取舍合理，同意 ADJ-10 本次不修。**

`_regex_fallback_create_table_props()` 只提取少量表属性，不能恢复列和索引的完整 AST；而且它直接在原始字符串中搜索 `PRIMARY KEY` 等文本，不感知字符串字面量。如果在所有异常路径中直接启用，列注释或表注释中的 `PRIMARY KEY` 字样可能让 R003 错误放行。

因此：

- 不应以 B 替代 A；
- 不应把 B 作为 A 重试失败后的无条件补救；
- A 应继续承担“成功时恢复完整 AST，失败时保持原错误”的职责；
- 但 A 的预处理必须先从全局正则升级为词法安全的受限变换。

## 4. 阻断问题与可实施修改要求

### BLOCK-1：全局正则不具备 SQL 词法边界，存在静默数据污染

**严重级别：阻断上线。**

#### 问题

Rev.A 会在字符串字面量、注释文本或默认值中识别出伪 `UNIQUE KEY ... COMMENT`，并可能生成一个可被 sqlglot 接纳但语义已改变的 AST。

#### 必须修改

新增一个专用、失败关闭的预处理函数，例如：

```python
def _strip_unique_index_comments_for_retry(sql: str) -> str | None:
    ...
```

函数至少应满足：

1. 单次扫描并维护 `NORMAL`、单引号、双引号、反引号、行注释、块注释状态；
2. 正确处理 `''`、`\'`、`\\`、```` 等转义；
3. 只进入顶层 `CREATE TABLE (...)` 的定义列表；
4. 只处理顶层定义项开头的真实 token：`UNIQUE [KEY|INDEX]`；
5. 用括号深度找到完整键值部分，支持 `a(20)`、多列和嵌套函数表达式；
6. 只移除该定义项顶层索引选项中的真实 `COMMENT '...'`；其他 `USING`、`KEY_BLOCK_SIZE`、`VISIBLE/INVISIBLE` 等选项原样保留；
7. 支持一个语句内多个 UNIQUE 索引；
8. 遇到未闭合引号、未闭合括号或无法证明边界时返回 `None`，不得猜测性改写；
9. 最好用等长空格替换目标片段并保留换行，使后续错误位置尽量对应原始 SQL。

如果 sqlglot tokenizer 能稳定提供所需 token、字符串类型和源码位置，可以复用；否则实现上述小型有限状态扫描器。不要继续用跨语义边界的单个 `re.sub()`。

### BLOCK-2：候选 AST 接纳门禁过宽

**严重级别：阻断上线。**

#### 问题

Rev.A 对所有首次解析异常的输入尝试正则，成功后只验证 `isinstance(_cand, exp.Create)`。这不足以证明：

- 原语句确实是 `CREATE TABLE`；
- 候选仍然是建表而不是其他 CREATE；
- 候选表与原始目标表相同；
- 改写只发生在批准的 UNIQUE 索引 COMMENT 片段。

#### 必须修改

重试前后增加以下门禁：

1. 原 SQL 的首个真实语句 token 必须是 `CREATE TABLE`；
2. 预处理器必须返回“发生过至少一次批准变换”的明确结果；
3. 候选必须是 `exp.Create` 且 `kind` 为 TABLE；
4. 候选表名必须与从原 SQL 安全提取的表名一致；
5. 预处理器应返回被替换的 span，验证差异只出现在这些 span；
6. 任一条件不满足，沿用原始异常与现有 E999 路径，不接纳候选 AST；
7. `parsed.raw_sql` 必须继续保持原始输入。

### MAJOR-1：DEF-1 需要依赖漂移护栏

**严重级别：重要，建议作为本次完成门禁。**

独立验证支持 A 的不可达结论，但项目没有锁定 sqlglot 上限。采用 `kind in {PRIMARY, UNIQUE, FULLTEXT}` 的精确映射不会改变当前结果，却能降低未来升级的静默回归概率。至少应二选一：

- 采用精确白名单映射；或
- 锁定 sqlglot 版本，并增加 AST 节点/`kind` 契约测试，在升级时显式失败。

### MAJOR-2：文档验证环境与实际环境不一致

**严重级别：重要。**

Rev.A 写明 sqlglot 30.14.0，而当前 main 工作环境实测为 30.12.0；依赖声明最低允许 26.0.0。虽然本轮在 26.0.0、30.12.0、30.14.0 上得到相同的关键 AST 结论，但实施文档和验收记录必须写清：

- 构建/发布实际安装版本；
- 最低支持版本；
- 回归实际执行版本。

否则“开发通过、生产安装了另一个满足 `>=26` 的版本”仍可能造成不可控漂移。

## 5. Rev.B 建议实现结构

建议把修复拆成以下职责，避免异常处理块继续膨胀：

```text
SQLParser.parse(raw_sql)
  ├─ 正常 sqlglot.parse_one(raw_sql)
  │    └─ 成功：完全沿用原路径
  └─ ParseError
       ├─ 非 CREATE TABLE：沿用原错误
       ├─ lexical transformer 未产生安全变换：沿用原错误
       └─ parse_one(transformed_sql)
            ├─ 失败：沿用原错误
            └─ CREATE TABLE + 同表名 + 差异 span 合法
                 ├─ ast = retry_ast
                 ├─ parsed.ast = retry_ast
                 └─ 继续执行原 `_parse_create()` 完整解析
```

注意保留 A 已识别的 `ast` 局部变量重绑要求；只更新 `parsed.ast` 而不更新后续使用的局部 `ast` 会造成控制流错误。

## 6. 必补测试矩阵

### 6.1 DEF-1 AST 与规则测试

1. 普通索引名含 `unique`、列名含 `unique`：均为 NORMAL，不触发 R054；
2. 名称/列名含 `primary`、`fulltext`：不得改变类型；
3. 真 UNIQUE 仍能被 R054 正确识别；
4. FULLTEXT 仍输出 FULLTEXT；
5. SPATIAL 仍按本次兼容约定输出 NORMAL；
6. `CONSTRAINT ... UNIQUE`、内联 UNIQUE 等非本函数路径的现有行为不得漂移；
7. gg77 原样 fixture：只有 R054 从结果中消失。

### 6.2 DEF-2 正向恢复测试

至少覆盖：

1. 单个 UNIQUE KEY/INDEX COMMENT；
2. 多个 UNIQUE 索引分别带 COMMENT；
3. COMMENT 与列清单间换行；
4. 注释包含 `)`、`unique`、`COMMENT`；
5. `''` 双引号式单引号转义；
6. `\'` 反斜杠单引号转义及 `\\`；
7. 前缀键值 `a(20)`；
8. 多列前缀键值；
9. 嵌套函数键值 `((lower(a)))`（若目标 TDSQL 版本明确不支持，应进入“仍报原错”测试并写清产品边界）；
10. 转义反引号索引名；
11. COMMENT 前后的 `USING`、`KEY_BLOCK_SIZE`、`VISIBLE/INVISIBLE`；
12. gg78 原样 fixture：不再出现 E999/R003/R004/R005/R028，只保留集中式规则下的真实 R036/R037；
13. 所有成功恢复样例的 `raw_sql` 必须逐字符等于输入。

### 6.3 DEF-2 负向与防次生灾害测试

以下位置即使包含完整的 `UNIQUE KEY fake (...) COMMENT '...'` 文本，也不得被修改：

1. 列 COMMENT；
2. 表 COMMENT；
3. 字符串 DEFAULT；
4. 单行注释、`#` 注释、块注释；
5. 双引号字符串（按方言配置验证）；
6. 反引号标识符。

每个负向用例应至少断言：

- 原本首次解析失败时，不会因伪目标被错误接纳；
- 若同一语句另有一个真实目标使重试成功，所有列注释、默认值、表注释仍与原文语义一致；
- 未闭合字符串、括号或真正语法错误仍返回原 E999，不得被“修复”成另一条可解析 SQL；
- 非 `CREATE TABLE` 输入完全不进入该重试逻辑。

### 6.4 回归门禁

1. 当前全量 pytest 必须 0 failed、0 error；
2. 生产 14 表回放零漂移；
3. 195 条历史语料零漂移；
4. 分布式/集中式规则集分别按报告原上下文重放，禁止用错 `instance_type`；
5. 对预处理器做属性测试或模糊测试：引号、括号、逗号、注释、转义随机组合不得崩溃，无法证明安全时必须失败关闭；
6. 记录实际 sqlglot 版本，至少在项目最低版本和发布锁定版本各跑一次解析器专项测试。

## 7. 已执行的独立验证

| 验证项 | 结果 |
|---|---|
| 两份 HTML 与仓库 fixture 同源性 | 2/2 完全一致 |
| v1.6.2.1 两个问题复现 | 2/2 成功 |
| Rev.A 候选对 gg77/gg78 的目标结果 | 基础生产样例均达到预期 |
| 32 类 AST 语法 × sqlglot 26.0.0/30.12.0/30.14.0 | 未发现 PRIMARY/UNIQUE 进入 `IndexColumnConstraint` 的反例 |
| Rev.A 正则边界鉴别 | 14 类；发现 4 类直接漏修/失败及 1 类可成功但污染 AST 的高危反例 |
| 解析器/R054/R061 相关现有测试 | 71 passed |
| 全量现有 pytest（临时候选补丁） | 1384 passed，0 failed；10 warnings |
| main 项目代码 | 未修改 |

全量现有测试通过说明候选补丁没有破坏已被测试覆盖的旧行为，但不能抵消新发现的负向反例。此次阻断恰恰表明现有语料尚未覆盖字符串边界、嵌套括号和 MySQL 转义规则。

## 8. 上线准入条件（Go / No-Go）

### 当前状态：**No-Go**

满足以下全部条件后，方可进入实施验收或再次设计复审：

- [ ] BLOCK-1：用词法安全、作用域受限的预处理器替换全局正则；
- [ ] BLOCK-2：补齐 CREATE TABLE、表名同一性、批准 span 等候选接纳门禁；
- [ ] 本报告 §6.2、§6.3 的正向与负向边界全部自动化，零 skip；
- [ ] gg77、gg78 使用原样 fixture 和原规则集通过；
- [ ] `raw_sql`、列注释、表注释、默认值等结构化字段无污染；
- [ ] 真实语法错误与非目标语句仍保持原失败行为；
- [ ] DEF-1 增加 sqlglot AST 漂移护栏；
- [ ] 明确并记录发布环境 sqlglot 版本；
- [ ] 现有全量测试、历史语料和生产 14 表回放全部通过且无非预期漂移。

## 9. 给施工方的最终意见

1. **可以直接保留**：DEF-1 根因与 AST `kind` 修复方向、DEF-2 预处理后重试的总体架构、重绑 `ast` 与 `parsed.ast`、保持 `raw_sql` 原文、SPATIAL 本次映射 NORMAL、ADJ-10 本次关闭。
2. **必须重做**：§3.1 的正则剥离实现，以及 §3.2 只凭 `exp.Create` 接纳重试 AST 的门禁。
3. **必须重写论证**：§2.3 第一条不能再从“正常解析路径不进入 except”推导整个改动零风险；要把安全证明落在词法作用域、允许变换 span、候选同一性和负向测试上。
4. **不要用更复杂的大正则修补当前大正则。** SQL 字符串、标识符、注释、转义和嵌套括号组合后，单个正则无法可靠证明自己只修改目标语法节点。
5. **不要启用方案 B 掩盖重试失败。** 失败关闭比带着错误的结构化事实继续审核更安全；未支持的合法语法应通过后续专项解析能力扩展解决，而不是用字符串兜底制造 R003 等漏报。

综合判断：**A 对两项缺陷的业务判断和主要根因分析是可靠的，DEF-1 基本成熟；DEF-2 的架构选择正确，但实现边界尚未达到生产核心审核能力所需的安全等级。Rev.B 完成上述阻断整改后再进入编码，是本次避免次生灾害的最低要求。**
