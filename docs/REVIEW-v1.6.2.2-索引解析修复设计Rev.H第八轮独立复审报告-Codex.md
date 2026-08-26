# v1.6.2.2 索引解析修复设计 Rev.H 第八轮独立复审报告

| 项目 | 内容 |
|---|---|
| 评审对象 | `DESIGN-v1.6.2.2-索引类型误判与唯一索引注释解析崩溃修复详细设计说明书.md` Rev.H |
| 被评审提交 | `eb38b19`（`docs: v1.6.2.2 design Rev.H — close O's seventh-review BLOCK-G1/G2/G3`） |
| 评审日期 | 2026-08-26 |
| 评审人 | Codex（独立复审） |
| 评审范围 | 方案与代码块，不改项目产品代码 |
| 评审基准 | 目标 TDSQL 官方语法与目标生产事实优先；MySQL 语法和 sqlglot 行为只作实现参考 |
| 最终结论 | **不通过，暂不可施工：3 个 BLOCK、2 个 MAJOR、2 个 MINOR** |

---

## 1. 结论先行

Rev.H 对第七轮的三个问题做了实质整改：目标 UNIQUE 的键列清单不再只做括号配平，表选项改为逐项值谓词，`PARTITION BY` 也不再无条件 `break`。这些方向均正确，我独立构造的上一轮 G1/G2/G3 原始反例已经被明显收紧。

但是，Rev.H 仍没有建立“**原异常确实只由 UNIQUE 索引 COMMENT 引起**”这一最关键的恢复前提。当前方案只证明目标 UNIQUE 定义自身看起来完整，没有证明同一条 `CREATE TABLE` 的其他定义项、表选项及分区尾部完整。COMMENT 被置空后，sqlglot 会把若干与目标无关的非法结构静默丢弃并返回 `exp.Create`；现有四道门禁只检查节点类型、表名和改写 span，无法发现 AST 已经丢结构。

本轮已构造出多条可复现的 `E999_SYNTAX_ERROR → exp.Create` 路径，其中包含：

- `ENGINE=123`；
- 空普通索引 `KEY k()`；
- 定义列表重复逗号；
- `PARTITION BY RANGE(,)`；
- TDSQL 官方不允许的 `USING HASH` 索引类型。

因此，Rev.H 的 S-2c、S-3 以及风险表中的“已关闭”仍缺乏成立条件。本轮结论为：**不得按 Rev.H 当前文本施工；先关闭下述 3 个 BLOCK，再进入实现。**

---

## 2. 本轮边界与用户已决事项

本轮不再争论用户已经认可的 A 方保留意见，具体包括：

1. 接受 A 对第七轮 MINOR-G1 的实测更正，不再要求按我上一轮的错误数量口径改写；
2. 接受反例用“单调不变松”比较：`None/E999 < Command < Create`，不要求候选与主干逐字同态；
3. 不要求遇到 `PARTITION BY` 一律失败关闭；接受采用完整消费者、保住 D5 覆盖面的方向；
4. 用户既有 ADJ-4、ADJ-6、NG-7 等决策仍按设计文档执行。

本报告的问题均为上述决策之外的新证据，不是换一种说法重提已关闭争议。

---

## 3. 判据校正：TDSQL 是最终规范，不是 MySQL 子集猜测

本轮采用以下证据优先级：

1. 目标内网 TDSQL 的真实 `SHOW CREATE TABLE` / 已验证生产 DDL；
2. 腾讯云 TDSQL MySQL 版官方语法；
3. 项目已冻结的产品规则与用户决策；
4. MySQL 官方语法；
5. sqlglot 的当前解析能力。

腾讯官方资料明确说明，TDSQL proxy 会自行解析 SQL，存在“MySQL 支持但分布式实例不支持”的语法；因此不能从“MySQL 合法”直接推出“TDSQL 合法”。参考：

- [TDSQL MySQL 版建表语法](https://cloud.tencent.com/document/product/557/8767)
- [TDSQL MySQL 版二级分区](https://intl.cloud.tencent.com/zh/document/product/1042/33361)
- [TDSQL MySQL 版透传 SQL](https://cloud.tencent.com/document/product/557/47559)
- [TDSQL MySQL 版使用限制](https://cloud.tencent.com/document/product/557/47511)

sqlglot 在本方案中只能是词法器和候选 AST 生成器，不能反向充当 TDSQL 合规性判据。尤其不能把“sqlglot 能解析”写成“TDSQL 合法”，也不能把“sqlglot 解析失败”写成“TDSQL 非法”。

---

## 4. 已独立确认的正确整改

### 4.1 G1 键列清单的收紧方向正确

`_consume_index_key_parts()` 对空清单、前后逗号、连续逗号、字符串键、数字键、函数/表达式键的失败关闭，比 Rev.G 的“括号配平即通过”安全得多。

腾讯 TDSQL 官方建表语法给出的 `key_part` 是：

```text
{col_name [(length)]} [ASC | DESC]
```

Rev.H 当前的列名、可选前缀长度、可选 ASC/DESC 形态与该官方语法主体一致。需要整改的是后文把 ASC/DESC 归入“neg”的产品定性，而不是这一消费者的目标文法本身。

### 4.2 G3 表选项按专属值谓词建模的方向正确

`ENGINE`、`ROW_FORMAT`、`SHARDKEY`、数值选项和三值开关不再共享一个宽泛值桶，成功堵住了 Rev.G 的 `ENGINE=123`、`ROW_FORMAT='x'` 等局部误批准路径。

但这个消费者当前只在 TDSQL 方言尾剥离路径中生效；UNIQUE-COMMENT 单独恢复时并不会调用它。这正是 BLOCK-H1 的根因之一。

### 4.3 G2 不再无条件跳过分区尾部，方向正确

缺方法、空括号、未闭合、尾随 token、内藏第二声明的失败关闭是必要整改。问题在于 Rev.H 仍把“非空且括号配平”当成了分区表达式/定义的充分条件，详见 BLOCK-H2。

### 4.4 既有专项基线正常

在未施工的当前 main 上独立执行：

```text
python -m pytest -q \
  tests/test_r077_r054_tdsql_syntax.py \
  tests/test_parser_tdsql_dialect_fallback.py \
  tests/test_r061_index_name_quoting.py

71 passed, 3 warnings
```

这说明本轮反例不是由既有专项测试环境损坏造成的；它们是 Rev.H 新测试矩阵尚未覆盖的语义空洞。

---

## 5. BLOCK-H1：恢复门禁只验证目标 UNIQUE，没有验证整条建表语句

### 5.1 发生原因

`_strip_unique_index_comments()` 调用 `_tdsql_table_def_bounds()` 得到定义列表边界，但其扫描目的只是在顶层 UNIQUE 定义里批准 COMMENT span。到达定义列表右括号后立即停止：

```text
验证范围：目标 UNIQUE 的 key_part + 索引选项
未验证范围：其他列/索引/约束 + 定义项分隔 + 整个表尾
```

随后 `parse()` 只要求：

- 改写差异落在批准 span；
- 候选是 `exp.Create`；
- `kind == TABLE`；
- 表名相同。

这些条件能证明“改写位置没越界”，却不能证明“候选 AST 没有静默丢掉原 SQL 中的其他结构”。

### 5.2 处理机制为何会吞错

实际控制流为：

```text
原 SQL 因 UNIQUE ... COMMENT 抛 ParseError / E999
  → 只把 COMMENT token 等长置空
  → sqlglot 对其余非法结构宽松恢复
  → 非法 token 被丢弃或生成残缺 AST
  → 仍返回同表名 exp.Create
  → 四道门禁全部通过
  → 原 E999 消失，下游 119 条规则基于不可信 AST 审核
```

### 5.3 独立实测证据

以下原 SQL 在当前 `RuleChecker` 上均包含 `E999_SYNTAX_ERROR`；只移除目标 COMMENT 后，sqlglot 30.12.0 均可返回 `exp.Create` 或在方言剥离后返回 `exp.Create`：

| 编号 | 原 SQL 中与目标无关的问题 | 原 SQL | COMMENT 掩码后的候选 | Rev.H 现有门禁 |
|---|---|---|---|---|
| H1-1 | 非法引擎值 | `... UNIQUE KEY uk(id) COMMENT 'x') ENGINE=123` | `Create ... ENGINE=123` | 通过；UNIQUE 剥离路径不调用表尾消费者 |
| H1-2 | 空普通索引 | `id INT, KEY k(), UNIQUE KEY uk(id) COMMENT 'x'` | `Create`，普通索引变成无列索引 | 通过；只验证目标 `uk` |
| H1-3 | 定义列表重复逗号 | `id INT,, UNIQUE KEY uk(id) COMMENT 'x'` | `Create`，空定义项被丢弃 | 通过；没有定义项总数/空项门禁 |
| H1-4 | 孤立表选项 | `... UNIQUE KEY uk(id) COMMENT 'x') DEFAULT` | `Create`，`DEFAULT` 被丢弃 | 通过；没有全表尾验证 |
| H1-5 | 非法分区表达式 | `... UNIQUE KEY uk(id) COMMENT 'x') PARTITION BY RANGE(,)` | `Create`，整个分区子句被丢弃 | 通过；没有 AST 结构保留门禁 |

对 H1-1、H1-2、H1-3、H1-4，问题甚至不需要 `TDSQL_DISTRIBUTED` 或 `BROADCAST` 就能发生。因此 H 组只围绕“方言目标 + 表选项/分区”的组合测试，不能证明 UNIQUE-COMMENT 单独恢复路径安全。

### 5.4 为什么 Rev.H 的现有证明不成立

- S-2c 写“整个语法单元及其内部结构被完整消费”，实际只覆盖**被识别出的目标语法单元**，未覆盖整条建表语句；
- S-3 写“绝不把非法 DDL 修成合法”，H1-1 至 H1-5 已直接证伪；
- 风险表写“接纳了不该接纳的候选 AST 已关闭”，但 AST 是否丢结构从未进入门禁；
- H 组的单调判据是正确的，但输入域没有覆盖“合法目标 + 目标外非法结构”，所以全绿不能关闭该风险。

### 5.5 必须实施的修正

建议新增一个独立的 `_validate_recovery_candidate(...)`，把“span 合法”和“候选 AST 完整”拆成两道门：

1. **定义项普查**：从原 token 流统计定义列表顶层项，显式拒绝前导/尾随/连续逗号和空定义项；候选 `Schema.expressions` 数量必须与原文顶层非空定义项数量一致；
2. **必要结构非空**：每个列定义必须有数据类型；普通/唯一/主键索引必须有非空键列；不得存在 sqlglot 生成的空 Index；
3. **表尾总是验证**：抽出“只验证、不要求产生 span”的表尾消费者。即使没有 TDSQL 方言声明，只要进入 UNIQUE-COMMENT 恢复，也必须完整消费右括号后的所有 token；
4. **AST 保留门禁**：原 token 流含 `PARTITION BY` 时，候选 AST 必须仍含同方法的非空 Partition；原文含已支持的 ENGINE/CHARSET/COLLATE/COMMENT 时，候选 AST 对应属性必须存在且与原 token 值一致；
5. **失败关闭**：任一普查或映射不能证明完整时，保持原异常/E999，不得仅凭 `exp.Create + 同表名` 接纳；
6. **新增交叉测试**：至少覆盖“1 个合法目标 UNIQUE COMMENT × 其他定义项/分隔/表选项/分区尾部四大类反例”，并分别覆盖无 TDSQL、HASH、RANGE/LIST、BROADCAST 路径。

准出标准：H1-1 至 H1-5 在候选实现上必须全部保留 E999，且两个生产目标 fixture 仍恢复成功。

---

## 6. BLOCK-H2：分区消费者仍是“非空配平即通过”，可直接批准非法分区

### 6.1 发生原因

`_consume_partition_clause()` 的 `_balanced()` 只检查：

- 有左括号且最终闭合；
- 括号体不是完全空；
- 不含分号、第二个 `PARTITION BY` 或第二个方言声明。

它没有验证括号体确实是合法分区表达式，也没有验证可选定义表由合法 partition definition 组成。文档中“上述约束把它限定成确实是分区定义”的论证不成立：一个逗号、运算符或任意 VAR 都满足“非空且配平”。

### 6.2 可达的完整吞错路径

反例：

```sql
CREATE TABLE t (
  id INT,
  UNIQUE KEY uk(id) COMMENT 'x'
)
TDSQL_DISTRIBUTED BY HASH(id)
PARTITION BY RANGE(,)
```

当前 main 对原 SQL 明确报 `E999_SYNTAX_ERROR`。按 Rev.H 代码逐步执行：

1. UNIQUE 消费器批准并置空 `COMMENT 'x'`；
2. 第一次重试因 TDSQL 尾部降为 `Command`；
3. 方言剥离器批准 `TDSQL_DISTRIBUTED BY HASH(id)`；
4. `_consume_partition_clause()` 把 `(,)` 判为“非空、配平”，因此返回成功；
5. 方言 span 被置空后，sqlglot 30.12/30.14/30.17 均把 `PARTITION BY RANGE(,)` 静默丢掉并返回 `exp.Create`；
6. 表名、kind、span 门禁均通过，原 E999 消失。

这说明 BLOCK-H2 不依赖 BLOCK-H1 的“未调用表尾消费者”缺口；即使消费者被调用，它自身仍会误批准。

### 6.3 必须实施的修正

1. 按**目标 TDSQL 官方语法**给分区表达式和定义建立有限文法，不得继续用“任意非空括号体”；
2. 对 TDSQL 已证实的 Range/List 二级分区，表达式至少应精确建模为项目支持的列标识符或官方允许的日期函数形态；
3. 分区定义表必须逐个消费完整定义，不得仅配平外层括号；
4. 候选 AST 必须保留原 `PARTITION BY`，并校验方法、表达式及定义数量；若 sqlglot 静默丢分区则拒绝候选；
5. 增加 `(,)`、`(+ )`、`(id,)`、只有操作符、定义项前后/连续逗号、非 `PARTITION` 起始项、残缺 `VALUES` 等反例；
6. 新消费者要同时覆盖目标实例已验证的新 HASH 方言组合及腾讯官方 Range/List 组合，不能只照搬 MySQL 全集。

---

## 7. BLOCK-H3：`USING HASH` 与 TDSQL 官方建表语法冲突

### 7.1 发生原因

Rev.H 在 S-2c、`_strip_unique_index_comments()` 和测试说明中都把合法索引选项写成：

```text
USING (BTREE | HASH)
```

但腾讯官方 TDSQL MySQL 版建表语法对 `index_type` 的定义是：

```text
USING {BTREE}
```

这正是“TDSQL 不能直接等同 MySQL”的具体实例。即使某些 MySQL 引擎认识 HASH，也不能据此将其批准为目标 TDSQL 合规 DDL。

### 7.2 独立实测

```sql
CREATE TABLE t (
  id INT,
  UNIQUE KEY uk(id) USING HASH COMMENT 'x'
);
```

- 当前 main：`E999_SYNTAX_ERROR`；
- 去掉目标 COMMENT 后：sqlglot 30.12/30.14/30.17 均返回 `exp.Create`；
- Rev.H 代码：明确批准 `USING HASH`，因此会进入候选接纳；
- 当前 119 条规则中没有一条负责否决 HASH 索引类型，不能指望下游补救。

因此该输入会从“显式语法阻断”变成“可信 AST 审核”，是确定的次生放行。

### 7.3 必须实施的修正

1. 当前 TDSQL 契约下，索引选项白名单改为仅接受 `USING BTREE` 和 `COMMENT STRING`；
2. `USING HASH COMMENT` 必须加入负向用例，并断言主干 E999 在候选中不消失；
3. 若目标内网 TDSQL 的特定版本确实扩展支持 HASH，必须提供该版本官方手册或目标实例真实执行/`SHOW CREATE TABLE` 证据，由用户明确决定后再加入版本化能力矩阵；不得只以 sqlglot/MySQL 能解析为证据。

---

## 8. MAJOR-H1：官方有效 TDSQL 被标成“neg/产品边界”，且分区顺序覆盖不全

### 8.1 ASC/DESC 是 TDSQL 官方 key_part，不是 MySQL-only 负例

腾讯官方 TDSQL `key_part` 明确包含 `[ASC | DESC]`。Rev.H H2b 却把 ASC/DESC 三例归为 `neg（产品边界）`。失败关闭可以作为暂时实现行为，但测试分类必须改成：

```text
TDSQL 官方合法，但当前 sqlglot/产品尚未支持的已知假阴性
```

不能把它们和非法 SQL 共用 neg 口径，更不能据此声明“合法形态 0 例收紧”。应由用户决定 v1.6.2.2 必须补齐还是登记有期限的已知问题。

### 8.2 LIST 二级分区及 partition ENGINE 是 TDSQL 官方能力

官方二级分区文档明确支持 Range 和 List；官方建表文档的 partition definition 也包含 `ENGINE` 和 `COMMENT` 选项。Rev.H H4b 中的 LIST + `PARTITION ... VALUES IN ... ENGINE=InnoDB` 不能因为 sqlglot ParseError 就归入非法 neg。

本地 sqlglot 30.12/30.14/30.17 的对照也说明“sqlglot 能力”不是稳定的语法判据：简单 LIST definition 可解析为 `Create`，带 partition ENGINE 时才 ParseError。正确结论是解析器覆盖不完整，而不是 LIST 语法无效。

### 8.3 官方 Range/List 二级分区存在另一种子句顺序

腾讯官方示例包含：

```text
PARTITION BY LIST (...) (...)
TDSQL_DISTRIBUTED BY RANGE(id) (...)
```

Rev.H 的 `_consume_partition_clause()` 强制分区子句必须消费到语句结束；遇到其后的 `TDSQL_DISTRIBUTED` 会直接失败。当前 D5 只验证“方言声明在前、PARTITION BY 在后”，不能代表完整 TDSQL 排列。

### 8.4 修改意见

1. 把 §5.19.1、§5.19.2、H2b/H4b 的判据来源改为 TDSQL 官方语法；
2. 将“合法但未支持”与“非法反例”拆成不同测试组和不同准出结论；
3. 纳入官方 Range/List 二级分区示例，覆盖 `shardkey=... PARTITION BY ...`、`PARTITION BY ... TDSQL_DISTRIBUTED BY RANGE/LIST ...`，以及目标实例已验证的 `TDSQL_DISTRIBUTED BY HASH ... PARTITION BY ...`；
4. 对不同 TDSQL 内核版本不支持的方言建立 capability profile，不能用一个无版本白名单概括所有 TDSQL；
5. 若本版本不补 ASC/DESC、LIST+ENGINE 等有效语法，设计必须明确记录剩余误报、适用版本和用户批准，不得写成“覆盖面损失 0”。

---

## 9. MAJOR-H2：依赖范围允许安装未经门禁验证的 sqlglot 版本

### 9.1 问题

Rev.H 计划把依赖声明改为：

```text
sqlglot>=29,<31
```

但 G-26 只要求 29.0.0 与 30.14.0 两个点。2026-08-26 可解析到的最新 30.x 已是 30.17.0，本机现用版本是 30.12.0。该范围安装不是可复现构建，不能由两个端点证明区间内所有版本行为相同。

本轮在隔离目录安装 30.14.0 与 30.17.0，并复跑本报告的精选探针；两者在这些探针上结果相同，**本轮没有发现 30.17.0 的新增行为回归**。但这不等于 241 例和全量规则在 30.17.0 已过门禁，更不能约束未来发布的 30.18+。

### 9.2 修改意见

二选一：

1. **推荐**：生产依赖精确锁定为实际完成全量验证的版本，并锁定构建 wheel/hash；
2. 若必须保留范围：CI 必须对“下界 + 构建时 resolver 实际选中版本”运行 241 例、全量 tests、生产 fixture 与语料漂移；构建产物记录最终版本，未验证版本不得发布。

仅在提交说明里记录打包 wheel 版本，不能替代依赖文件的可复现约束。

---

## 10. MINOR 项

### MINOR-H1：正文仍残留 Rev.G 的分区处置说明

§5.17.5 仍写“`PARTITION BY` 作为不透明终结子句，遇到即停止消费”，与 Rev.H §5.19.2 和代码块的完整消费者冲突。应删除旧段或明确标注为 Rev.G 历史，不得让施工者面对两套相反指令。

### MINOR-H2：检查单与代码说明仍有旧口径

至少有三处需要统一：

- §3.1 安全清单第⑤项仍写键值列表“支持嵌套函数”，实际 `_consume_index_key_parts()` 明确拒绝函数/表达式；
- C-14 仍写“G-1 ~ G-20”，实际门槛已扩展到 G-27；
- C-1 写本次共 3 个文件，但 C-16 还要求修改 `VERSION` 和 `backend/config.py`，施工提交实际至少涉及 5 个产品/依赖/版本文件。

这些不改变算法结论，但会造成施工范围和验收清单歧义，应在下一版一次性清理。

---

## 11. 建议的 Rev.I 整改顺序

1. 先把判据切换到 TDSQL 官方语法，关闭 `USING HASH` 误批准；
2. 抽出全表尾“验证模式”，让 UNIQUE-COMMENT 单独恢复也必须完整验证尾部；
3. 重写分区消费者，不再使用“任意非空括号体”；
4. 增加候选 AST 结构保留门禁，覆盖定义项数量、空列/空索引和分区保留；
5. 区分“非法 neg”和“TDSQL 合法但暂不支持”，补齐官方 Range/List/ASC-DESC 矩阵；
6. 锁定或完整验证实际 sqlglot 构建版本；
7. 最后更新测试计数、风险表、施工清单和全量/语料基线。

不建议先继续扩大 H 组数量。当前关键不是数量，而是输入域缺了“目标合法、目标外结构非法”和“TDSQL 官方合法、sqlglot 不支持”两条轴。

---

## 12. Rev.I 最低准出门禁

在 Rev.H 原有门禁之外，至少新增：

| 编号 | 门禁 |
|---|---|
| I-1 | 本报告 H1-1 至 H1-5 全部保留原 E999，不得变成 Command/Create |
| I-2 | `USING HASH COMMENT` 按 TDSQL 官方口径失败关闭；`USING BTREE COMMENT` 正常恢复 |
| I-3 | `PARTITION BY RANGE(,)` 及分区表达式/定义的结构反例全部失败关闭 |
| I-4 | 进入恢复的语句，原顶层定义项数与候选 AST 定义项数一致；列类型和索引键列不得为空 |
| I-5 | 原文存在已支持的 `PARTITION BY` 时，候选 AST 必须保留同方法、非空表达式和定义数量 |
| I-6 | UNIQUE-COMMENT 单独路径、HASH 路径、BROADCAST 路径、Range/List 双顺序路径均覆盖 |
| I-7 | ASC/DESC、LIST、partition ENGINE 按“TDSQL 合法能力/已知未支持”单独统计，不得归入非法 neg |
| I-8 | 官方二级分区示例形成不可手写漂移的 fixture，并记录适用 TDSQL 内核版本 |
| I-9 | 实际发布 wheel 版本完整通过新增专项、既有 71 例、全量 tests、生产 fixture 与语料漂移 |
| I-10 | 两个用户报告 fixture 仍达到预期，且规则集合继续使用精确相等断言 |

---

## 13. 最终评审意见

Rev.H **部分解决了上一轮问题，但尚未达到可施工标准**。

肯定项是：G1/G3 的局部消费者收紧有效，A 对用户已认可事项的保留有实测依据，本轮不要求反转这些决定。

阻断项是：恢复链仍把“目标 UNIQUE 定义完整”误当成“整条建表语句可信”，分区消费者仍可批准 `(,)`，索引选项又把 MySQL 的 HASH 能力误带入 TDSQL。三者都能产生 `E999 → Create` 的次生放行，不能靠现有 span、表名或 AST 类型门禁兜底。

**结论：Rev.H 不通过。请 A 修订为 Rev.I，关闭 BLOCK-H1/H2/H3 后再复审；在此之前不要让 Q 施工。**
