# v1.6.2.2 索引解析修复设计 Rev.M 第十二轮开发准入独立复审报告

## 1. 评审结论

**结论：Rev.M 暂不通过开发准入（No-Go）。**

本轮不是对 A 的整体方案作否定。Rev.M 已经实质关闭了主 token 流上的表尾回环、广播哨兵混型、`FULLTEXT/SPATIAL` 入口不一致以及 `COLUMN_FORMAT/ENGINE_ATTRIBUTE` 端到端不可达等问题，方案结构也比 Rev.L 清晰得多。

但在把文档中的最终代码块直接执行、再用未出现在 manifest 中的组合反例与 AST 变异测试后，仍发现：

- **5 项 BLOCK**：可执行注释没有位置语义、终止分号门槛在集成点被绕过、类型/字面量白名单仍未闭合、候选 AST 门禁仍未守恒顶层与表尾语义、所谓“唯一真源”在仓库中不可执行；
- **2 项 MAJOR**：官方 `CONSTRAINT … PRIMARY KEY` 被候选门禁系统性误杀；当前施工/验收指令仍有互相冲突的最终态描述；
- **1 项 MINOR**：历史标识与统计表述仍有模板残留和口径混杂。

其中 BLOCK-12-01、BLOCK-12-02、BLOCK-12-04 都能让本应失败关闭的 SQL 被恢复成 `Create`，属于真实的“吞错/次生灾害”风险；BLOCK-12-03 和 MAJOR-12-01 会继续把官方合法 TDSQL/MySQL 兼容语法留在 E999 路径。进入开发前必须闭环。

## 2. 评审对象与边界

| 项 | 内容 |
|---|---|
| 仓库 | `C:\Codex\tdsql-sqlcheck` |
| 分支 | `main` |
| 评审基线 | `84e9266`（Rev.L 第十一轮复审报告） |
| A 的修订提交 | `76df50fa096eca5d1d8ca1308d295d1e5fd66c4f` |
| 设计版本 | Rev.M |
| 产品代码状态 | A 本次提交只修改设计文档；`backend/engine/parser/parser_legacy.py` 仍为 v1.6.2.1 现状 |
| 本轮动作 | 只评审、测试和编写报告，未修改产品代码 |

本轮继续遵守用户已冻结的决策，不重新争论：

- 目标实例的 `TDSQL_DISTRIBUTED BY HASH(cust_no)` 为合法分片表语法；
- `shardkey=noshardkey_allset` 为合法广播表语法；
- 使用 sqlglot 词法器；
- `SPATIAL` 暂维持映射为 `NORMAL`；
- KFN-1（`MAXVALUE`）、ADJ-6、NG-10/ADJ-11 的 `CONSTRAINT … UNIQUE` 本期不扩项；
- `NEW_SECONDARY` 暂按用户已认可的 capability profile 决策登记而不放行。

本报告对 `CONSTRAINT … PRIMARY KEY` 的意见不涉及已经冻结的 `CONSTRAINT … UNIQUE`。

## 3. 独立验证方法与总览

### 3.1 实际执行

1. 从 Rev.M §3 的最终 Python 代码块直接抽取并在内存中执行；
2. 从附录 C 抽取 410 条 manifest 数据和 5 套、28 个逻辑变异断言；
3. 在 sqlglot **29.0.0 / 30.14.0 / 30.17.0** 三版分别执行规划器、候选解析与门禁矩阵；
4. 对表尾 atom 长度 0～4 的全部组合进行穷举；
5. 对可执行注释位置、重复分区、广播冲突、终止分号、官方类型别名/边界、顶层 CREATE 结构、表级属性和分区结构进行独立反例与变异测试；
6. 复核腾讯 TDSQL 官方建表、兼容性、二级分区和 DTS 分区语法页面；MySQL 手册只用于腾讯文档明确声明继承 MySQL 的类型边界补充；
7. 运行仓库现有 71 项专项与全量基线回归。

### 3.2 结果摘要

| 检查 | 结果 |
|---|---|
| Rev.M 自带 410 条 manifest，在三版上的规划器/候选/门禁覆盖域 | **0 失败** |
| typed atom 序列穷举 | **1,555 条序列，0 个非预期 profile 匹配** |
| 可执行注释新增位置/组合反例 | 4 类均被错误接受，三版一致 |
| 多终止分号集成模拟 | `;;`、`;;;`、`; ;` 均最终恢复成 `Create` |
| 类型新增反例 | 合法形态误拒、非法 `FLOAT(54)` 误收，三版一致 |
| 顶层/表尾 AST 定向变异 | schema、TEMPORARY、IF NOT EXISTS、ENGINE、CHARSET、COMMENT、分区方法/键/边界等变异均被门禁放行 |
| 现有专项 | **71 passed，3 warnings** |
| 当前 main 全量 | **1,384 passed，10 warnings**；见 §16.3。只证明文档提交未破坏 v1.6.2.1 基线，不证明 Rev.M 设计代码正确 |
| `verify_rules.py` | 119 条规则、107 条文件审核规则、0 未覆盖、3 条既有断言失败；与文档登记的同名同因基线一致 |

“A 的现有 manifest 全绿”与“本轮仍发现缺陷”并不矛盾：前者证明实现满足了当前 410 条已列用例；后者证明 manifest 尚未覆盖关键的组合域与集成点。

## 4. 第十一轮问题闭环复核

| 第十一轮项 | Rev.M 状态 | 本轮结论 |
|---|---|---|
| BLOCK-11-01 可执行注释不可见 | 增加 payload 校验 | **部分关闭**；未保留位置，未并入表尾计数/profile，转化为 BLOCK-12-01 |
| BLOCK-11-02 表尾回环 | typed atoms + 完整 profile | **主 token 流已关闭**；1,555 序列穷举通过，但可执行注释可绕过 |
| BLOCK-11-03 广播哨兵混型 | 独立终态 atom | **主 token 流已关闭**；可执行注释分区仍可挂到广播哨兵之后 |
| BLOCK-11-04 类型双向失真 | `_TYPE_RULES` | **部分关闭**；典型边界修好，但官方完整语法域仍不闭合，见 BLOCK-12-03 |
| BLOCK-11-05 SourceFingerprint 不守恒 | 比较列与索引 | **部分关闭**；定义项明显改善，但顶层 CREATE 与整个 tail 指纹没有进入比较，见 BLOCK-12-04 |
| BLOCK-11-06 列属性端到端不可达 | 辅助掩码 | **关闭**；在设计锁定版矩阵中 `COLUMN_FORMAT` 路径成立，官方枚举已纠正 |
| BLOCK-11-07 测试真源矛盾 | 文档附录提供 manifest | **未完全关闭**；仓库内没有可执行文件，且遗漏本轮关键域，见 BLOCK-12-05 |
| MAJOR-11-01 FULLTEXT/SPATIAL 入口 | 共用 `_index_lead()` | **关闭** |
| MAJOR-11-02 capability profile | 三个具名 profile | **关闭**；不重新打开用户认可的 `NEW_SECONDARY` 决策 |
| MINOR-11-01/02 陈旧锚点与规模 | 加历史提示、附 codestat | **部分关闭**；当前准出表和附录仍有陈旧名称/矛盾句，见 MAJOR-12-02、MINOR-12-01 |

## 5. BLOCK-12-01：可执行注释只校验“内容”，没有校验“插入位置”和全句组合

### 5.1 发生原因

Rev.M 的 `_collect_executable_comments()` 最终只返回 payload 字符串；它丢失了：

- 注释挂在哪一个主 token 上；
- 注释在原 SQL 中位于建表头、定义列表、表选项之间还是表尾；
- 该 payload 与主 token 流中的 `PARTITION`、`BROADCAST_SENTINEL`、`DIST` 的先后关系。

`_validate_executable_comments()` 又只返回 `(ok, exec_part)`。`exec_part` 被存进 plan，但既没有加入 `_scan_table_tail()` 的 atom 序列，也没有加入“二级分区至多一个”的计数，更没有参与 capability profile 匹配。

因此代码证明的只是“payload 单独看像一个合法分区”，没有证明“把 payload 放回原位置后，整条 CREATE TABLE 合法”。

### 5.2 可复现反例

以下四类在 29.0.0、30.14.0、30.17.0 上均为 `plan=True`，按 Rev.M 集成链最终均为 `Create`：

```sql
-- 1. 可执行分区被插进列定义内部
CREATE TABLE t (
  id INT /*!50100 PARTITION BY RANGE(id)
    (PARTITION p0 VALUES LESS THAN (10)) */,
  PRIMARY KEY(id) COMMENT 'p'
) ENGINE=InnoDB;

-- 2. 可执行分区位于 CREATE TABLE 之前
/*!50100 PARTITION BY RANGE(id)
  (PARTITION p0 VALUES LESS THAN (10)) */
CREATE TABLE t (id INT, PRIMARY KEY(id) COMMENT 'p') ENGINE=InnoDB;

-- 3. 主 token 流已经有分区，又追加可执行分区
CREATE TABLE t (id INT, PRIMARY KEY(id) COMMENT 'p') ENGINE=InnoDB
PARTITION BY RANGE(id) (PARTITION p0 VALUES LESS THAN (10))
/*!50100 PARTITION BY RANGE(id) (PARTITION p1 VALUES LESS THAN (20)) */;

-- 4. 广播表后追加可执行分区
CREATE TABLE t (id INT, PRIMARY KEY(id) COMMENT 'p') ENGINE=InnoDB
shardkey=noshardkey_allset
/*!50100 PARTITION BY RANGE(id) (PARTITION p0 VALUES LESS THAN (10)) */;
```

第 3 条绕过了 BLOCK-11-02 的二级分区计数；第 4 条绕过了 BLOCK-11-03 的广播终态约束。

### 5.3 必须如何修改

不要再返回裸 payload 列表。建议改为结构化条目：

```text
ExecutableAtom(owner_token_index, source_order, kind, fingerprint, version_guard)
```

然后：

1. 注释所属位置必须落在顶层表尾域；建表头和定义列表内的可执行分区直接失败关闭；
2. 把合法 payload 解析成 `PARTITION` atom，按原 SQL 顺序合并进 `_scan_table_tail()` 的 atom 流；
3. 与主 token 流共用同一个“二级分区至多一个”计数和 capability profile；
4. `BROADCAST_SENTINEL/BROADCAST_KEYWORD + executable PARTITION` 必须被同一 profile 拒绝；
5. fingerprint 记录 payload 的方法、键、分区名、边界和选项；不能只留一个布尔值；
6. 新增“位置 × 主尾子句”的全组合测试，必须走 `SQLParser.parse()` 端到端，不得只调用 `_validate_executable_comments()`。

生产 fixture 的合法 `/*!50100 PARTITION BY … */` 可以继续支持，但它必须通过“原位置合并后的整句 profile”，不能作为旁路例外。

## 6. BLOCK-12-02：`rstrip(";")` 在规划器之前吞掉了多终止分号

### 6.1 发生原因

Rev.M 新增 `_strip_terminal_semicolon()`，声明只允许 0 或 1 个且只能位于 EOF 前；这个函数本身逻辑正确。

但 `SQLParser.parse()` 入口仍保留：

```python
sql_clean = sql.strip().rstrip(";")
```

两个恢复调用点传给 `_plan_recovery()` 的都是已经被 `rstrip(";")` 处理过的 `sql_clean`。因此规划器永远看不到原始终止分号的数量。

### 6.2 可复现结果

对含 `PRIMARY KEY … COMMENT` 的目标语句执行 Rev.M 完整集成流程：

| 原始结尾 | 直接调用 `_plan_recovery(raw)` | `parse()` 预清理后 | 最终 |
|---|---:|---:|---|
| 无分号 | ACCEPT | ACCEPT | `Create` |
| `;` | ACCEPT | ACCEPT | `Create` |
| `;;` | REJECT | **ACCEPT** | **`Create`** |
| `;;;` | REJECT | **ACCEPT** | **`Create`** |
| `; ;` | REJECT | **ACCEPT** | **`Create`** |

所以 K-7 的门槛在真实调用链上不可达。当前 manifest 也没有 0/1/2/3 分号的端到端用例。

### 6.3 必须如何修改

- 规划器必须接收 `sql.strip()` 后、尚未删除分号的同一原串；
- 终止符验证与移除只能有一个责任点；验证失败后不得再进入恢复；
- 所有 span 必须相对同一字符串计算，禁止先改长度再套用旧偏移；
- 新增 `SQLParser.parse()` 端到端矩阵：0、1、2、3 个分号，分号间空白，分号后普通注释，多语句拼接，字符串内分号；
- manifest 的负例不得只断言“直接 plan=False”，还要断言最终 AST/E999。

## 7. BLOCK-12-03：结构化 `_TYPE_RULES` 仍不是 TDSQL/MySQL 兼容语法的闭合集

### 7.1 官方判据

腾讯 TDSQL [兼容性文档](https://intl.cloud.tencent.com/zh/document/product/1042/38180)明确写明支持 MySQL 的所有数据类型，并列出整数、FLOAT/REAL/DOUBLE PRECISION、DECIMAL/NUMERIC、字符串、空间和 JSON；同页还把 `.2`、科学计数法、正负号、hex、bit、布尔、NULL 列为支持的字面量。

在腾讯已声明继承 MySQL 类型的前提下，MySQL 5.7 [数值类型语法](https://dev.mysql.com/doc/refman/5.7/en/numeric-type-syntax.html)进一步明确：

- `DEC` 是 `DECIMAL` 同义词；
- 单参数 `FLOAT(p)` 的 `p` 范围是 0～53，0～24 为 FLOAT，25～53 为 DOUBLE；
- 数值类型允许 `SIGNED`；
- `SERIAL` 是带隐含约束的别名。

MySQL 5.7 [字符串类型语法](https://dev.mysql.com/doc/refman/5.7/en/string-type-syntax.html)还明确了 `NCHAR/NVARCHAR/CHARACTER VARYING` 等别名、字符族 `BINARY/ASCII/UNICODE` 属性，以及 SET 最多 64 个成员。

### 7.2 Rev.M 的代表性误判

以下结果在三版 sqlglot 上一致：

| 形态 | 官方性质 | Rev.M 结果 | 问题 |
|---|---|---|---|
| `DEC(10,2)` | 合法 DECIMAL 同义词 | `plan=False` | 假阴性，未登记 KFN |
| `FLOAT(0)` | 合法 `FLOAT(p)` 下界 | `plan=False` | 假阴性 |
| `FLOAT(54)` | 超出 `FLOAT(p)` 上界 53 | `plan=True → Create → gate=True` | **吞掉非法类型** |
| `NCHAR(10)` / `NVARCHAR(10)` | 合法 MySQL 兼容别名 | `plan=False` | 假阴性，未登记 KFN |
| `INT SIGNED` | 合法且 SIGNED 无额外语义 | `plan=True`，候选 ParseError | 代码声称支持但端到端不支持，未登记 KFN |
| `VARCHAR(20) BINARY` | 合法字符属性 | 规划器接受，候选失败 | KFN-3 只登记了 `CHAR BINARY`，范围不完整 |
| `DEFAULT .2` | TDSQL 官方列出的数值字面量 | `plan=False` | 假阴性 |
| 65 个不同成员的 `SET(...)` | 超出上限 64 | `plan=True → Create → gate=True` | 边界未实现 |

`SERIAL`、`NATIONAL CHAR/VARCHAR`、列级 `CHARSET` 等形态也必须在“支持”与“具名 KFN”之间作明确裁定，不能既不实现又不登记，然后继续宣称“官方合法形态零回归”。

### 7.3 根因

当前 `_TYPE_RULES` 仍是一张“类型名 → 单一 arity”的手写表，表达不了同一关键字的多种合法产生式。例如 `FLOAT(M,D)` 与 `FLOAT(p)` 的参数意义和范围不同，却被统一塞进 `M_D`，于是同时造成合法下界误拒和非法上界误收。

此外，TY 组是从实现已想到的条目手工选样，不是从官方类型产生式反向生成，所以“108 例全绿”不能证明类型域闭合。

### 7.4 必须如何修改

1. 建立机器可读的官方类型清单：源拼写、别名、可选产生式、每个参数范围、canonical、允许属性、sqlglot 三版行为、最终处置；
2. 一个类型必须允许多个产生式；`FLOAT(p)` 与 `FLOAT(M,D)` 分开；
3. 补齐 DEC、NCHAR/NVARCHAR/CHARACTER 等可由锁定版正常解析的别名；
4. 对 `SIGNED`、`SERIAL`、NATIONAL 形态、字符族 `BINARY/CHARSET/ASCII/UNICODE` 逐项决定“实现”或“KFN-A”，并写出端到端结果；
5. `DEFAULT .2` 需要规范成与候选 AST 一致的 `0.2`；其余腾讯官方字面量逐一做上下界测试；
6. 实现 SET 成员数上限；ENUM/SET 的边界和转义在锁定版上做语义归一；
7. 测试由官方清单生成：每个产生式至少覆盖下界、正常值、上界、下界外、上界外、别名、属性组合；
8. 所有当前无法恢复的官方形态必须进入 KFN 表，不能藏在普通 `plan=False` 中。

## 8. BLOCK-12-04：`SourceFingerprint.tail` 被生成但从未比较，顶层 CREATE 语义也未入指纹

### 8.1 发生原因

Rev.M 的 `_validate_recovery_candidate()` 已经能比较列与索引，这是明显进步；但它没有读取 `plan["fingerprint"]["tail"]`，对分区只检查“候选有且仅有一个 `PartitionBy*` 节点”。

同时，源指纹没有记录：

- schema/catalog；只保留最后一级表名；
- `CREATE TEMPORARY`；
- `IF NOT EXISTS`；
- 表级 ENGINE、CHARSET、COLLATE、COMMENT、ROW_FORMAT、STATS；
- 分区方法、分区键、分区名、VALUES 边界和顺序。

这些不是装饰信息。现有规则直接读取 `parsed.engine`、`parsed.charset`、`has_table_comment` 和临时表标志；若候选 AST 静默丢失或改写它们，会直接改变审核结论。

### 8.2 白盒变异结果

以合法源 plan 为基准，把候选 AST 对应 SQL 作以下单点变异，Rev.M 门禁全部返回 `True`：

| 变异 | 门禁 |
|---|---:|
| `CREATE TEMPORARY` → `CREATE` | **True** |
| 删除 `IF NOT EXISTS` | **True** |
| `db1.t` → `db2.t` 或删除 schema | **True** |
| `ENGINE=InnoDB` → `ENGINE=MyISAM` | **True** |
| `CHARSET=utf8mb4` → `latin1` | **True** |
| 表 COMMENT 文本改变或全部表选项删除 | **True** |
| `PARTITION BY RANGE(id)` → `LIST(x)` | **True** |
| 分区键、分区名、LESS THAN 边界改变 | **True** |

这说明 BLOCK-11-05 只关闭了“定义列表”一半，尚未形成完整的 CREATE TABLE 结构守恒。

### 8.3 必须如何修改

建议把指纹正式拆成三个结构，而不是继续向字符串元组追加字段：

```text
CreateShape
  ├─ QualifiedTableName(catalog, schema, table)
  ├─ CreateModifiers(temporary, if_not_exists)
  ├─ DefinitionShapes(columns, indexes, named_constraints)
  └─ TailShape(local_options, distribution_atom, partition_shape)
```

候选侧建立镜像提取器并逐字段比较：

- qname 必须完整相等；
- TEMPORARY、IF NOT EXISTS 必须相等；
- ENGINE、CHARSET、COLLATE、COMMENT、ROW_FORMAT、STATS 等未被掩码的本地表选项必须规范化后相等；
- 未被掩码的 PARTITION 必须比较方法、表达式、定义个数与顺序、分区名、VALUES 和保留下来的选项；
- `TDSQL_DISTRIBUTED/BROADCAST/shardkey` 等故意从候选 AST 移除的方言 atom，应标成“source-only approved transform”，由 raw SQL 规则和 profile 负责，不能与普通 table tail 混为一谈；
- 对每一个字段增加正候选与单点变异候选。当前 M 组只覆盖定义项，必须扩成 `M-CREATE/M-DEF/M-TAIL/M-PARTITION` 四组。

## 9. BLOCK-12-05：测试“唯一真源”尚未成为仓库中的可执行证据

### 9.1 客观状态

A 的提交 `76df50f` 只修改一份设计文档。当前 main 中以下文件全部不存在：

```text
tests/parser_recovery_manifest.py
tests/test_parser_recovery_manifest.py
tests/manifest_doc.py
tests/codestat.py
tools/rebuild_from_design.py
```

因此文档中的这些命令在当前提交上不能执行：

```text
pytest tests/test_parser_recovery_manifest.py -q
python tests/manifest_doc.py
python tests/codestat.py ...
python tools/rebuild_from_design.py ...
```

“从文档可复制出文件”不等于“仓库中已经交付了唯一真源”。当前仍同时存在附录数据、生成后的表格、人工编写的门槛和历史统计，无法用 CI 证明它们同源。

文档还声称“从说明书重建的 parser 与提交文件逐字节相同”，但本次提交明确是 docs-only，仓库中的 `parser_legacy.py` 仍是 v1.6.2.1；该陈述在当前提交上不可复现。

### 9.2 覆盖缺口

当前 410 条清单没有覆盖本报告的关键反例族：

- 可执行注释的原 SQL 位置；
- executable partition 与主 token partition/broadcast 的合并计数；
- 0/1/2/3 个终止分号的 `SQLParser.parse()` 端到端路径；
- FLOAT 单参数产生式、DEC/NCHAR/NVARCHAR/SIGNED、`.2`、SET 成员上限；
- schema、TEMPORARY、IF NOT EXISTS、表级属性和分区细节的 AST 变异；
- `CONSTRAINT symbol PRIMARY KEY`。

另外，G-5 写“collect 数 = `len(CASES)` + 变异断言数”，按文档口径应为 410+28+1；C-11 又写实际收集 416=410+5 套 mutation+1 fuzz。逻辑断言数和 pytest 参数项数被混用。

### 9.3 必须如何修改

- 在不提前修改产品代码的前提下，也应把证据资产提交到仓库；若不希望放入正式 `tests/`，可先放 `docs/evidence/v1.6.2.2/`，但命令必须真实可执行；
- 提供一个只在临时目录/临时 worktree 中重建设计补丁的脚本，禁止依赖人工复制；
- 生成文档时写入源文件 hash，CI 校验生成表与 manifest 一致；
- 把本报告全部反例加入 manifest，并让集成问题走真实 `SQLParser.parse()`；
- 明确区分“5 个 pytest mutation suite”“23 个变异候选”“28 个逻辑断言（含 5 个正确候选）”“416 个 collect item”；
- 在真实资产提交并跑通前，删除“已全绿”“逐字节相同”等过去时结论，改成开发准出条件。

## 10. MAJOR-12-01：官方 `CONSTRAINT symbol PRIMARY KEY` 被候选门禁系统性误杀

### 10.1 发生原因

腾讯 TDSQL [建表语法](https://cloud.tencent.com/document/product/557/8767)明确包含：

```text
[CONSTRAINT [symbol]] PRIMARY KEY [index_type] (key_part,...) [index_option] ...
```

Rev.M 源侧 `_scan_definition_list()` 会把它记录成：

```text
("constraint", "idx", "PRIMARY", ...)
```

但候选 sqlglot AST 的定义项是 `exp.Constraint`。`_validate_recovery_candidate()` 把该节点直接传给只识别 `PrimaryKey/UniqueColumnConstraint/IndexColumnConstraint` 的 `_ast_index_shape()`，所以必然返回 `None`。

### 10.2 实测

```sql
CREATE TABLE t (
  id INT,
  CONSTRAINT pk PRIMARY KEY(id)
) ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(id);
```

在锁定版 30.14.0 上：

```text
plan=True
candidate=Create
candidate definitions=[ColumnDef, Constraint]
gate=False
```

这会使一条官方合法、又包含用户冻结合法 HASH 方言的语句继续留在降级/E999 路径。

### 10.3 修改要求

- 候选提取器需要解包 `exp.Constraint`，比较 constraint symbol 和内部 PRIMARY 结构；
- 增加带名 PRIMARY constraint 与方言尾子句、PRIMARY COMMENT/UNIQUE COMMENT 共存的正例；
- `CONSTRAINT` 后省略 symbol 的官方形态在 sqlglot 30.14.0 上仍解析失败，必须实现或单独登记 KFN-A；
- 本项不要求打开用户已关闭的 `CONSTRAINT … UNIQUE` 规则能力。

## 11. MAJOR-12-02：最终施工与验收指令仍有相互冲突的口径

Rev.M 已给历史章节加提示，但当前准出表和附录 B 仍会直接误导开发：

1. §7.3 K-1 仍要求 `_TYPE_SPEC`，最终代码已经改为 `_TYPE_RULES`；
2. §7.3 K-6 仍要求 `_TAIL_EDGES`，最终代码已经改为 `_TAIL_PROFILES`；
3. 附录 B 第 12 条写“`PARTITION BY` 必须消费到语句结束”，第 18 条又写“不要求消费到语句结束”，后者才符合官方存在的 `PARTITION BY … TDSQL_DISTRIBUTED BY …` 顺序；
4. 附录 B 第 9/13 条仍把主干行为/rank 当反例期望依据，与 §7.1.0“期望由 TDSQL 规范推导、baseline 不参与 pass/fail”冲突；
5. §7.4 的复现命令引用不存在的脚本，并使用 `git stash && cp -r .`；这会扰动开发者脏工作区，不应作为标准复现流程。应改用独立临时 worktree 或归档快照。

修改要求：只保留一套“当前施工指令”和一套“当前准出门槛”；历史复盘不得继续出现在给 Q 的强制施工清单中。所有最终名称、数量和命令必须由已提交资产生成。

## 12. MINOR-12-01：历史模板与统计表述仍需清理

- 多个历史章节的提示被写成字面量 `Rev.%s`，没有替换成真实版本；
- 文档头部用 1355 passed 描述“本环境全量”，A-163 又写 1771 passed（含新增 416），虽然可以解释为“旧套件”和“加新套件”，但当前表述没有把两者分栏；
- “410 用例 + 28 变异 + 6000 fuzz”与“pytest collect 416”需要在第一次出现时说明 28 是逻辑断言而不是 28 个 pytest item。

这些不直接改变代码，但会降低评审证据的可复核性，建议与 BLOCK-12-05 一次清理。

## 13. 已确认正确、无需反复修改的部分

为避免 A 继续改动已经正确的机制，本轮明确确认：

1. 主 token 流的 typed atom + 完整 profile 方向正确；对长度 0～4 的 1,555 个 atom 序列穷举，匹配集合与声明 profile 一致；
2. 主 token 流里的重复一级分布、重复二级分区、广播哨兵括号/混列/再分区已经被拒绝；
3. ADJ-6 只保留精确的 `HASH_SHARDKEY + BROADCAST_KEYWORD` characterization，没有扩大；
4. `_index_lead()` 统一 `FULLTEXT/SPATIAL` 入口有效；裸形态与反引号同名列的分流成立；
5. `COLUMN_FORMAT` 的枚举已删除错误的 `COMPRESSED`；把 `COLUMN_FORMAT/ENGINE_ATTRIBUTE` 作为有主目标时的辅助掩码，方向可接受；
6. 列名、规范类型、列约束集合、索引 kind/name/key parts/USING 的新门禁比 Rev.L 明显增强；本轮只要求补齐顶层和 tail，不要求推翻现有定义比较；
7. `NEW_SECONDARY`、KFN-1、SPATIAL=NORMAL、sqlglot tokenizer、目标 HASH、广播哨兵等用户决策保持不变。

## 14. 建议 A 一次性提交的 Rev.N 闭环包

为了下一轮直接给开发 Go/No-Go，建议不要再按单个 SQL 加 if，而是一次提交以下五个闭环面：

1. **输入位置面**：可执行注释带 owner/source order，合并进表尾 atom/profile；
2. **语句边界面**：规划器接收未吞分号的原串；所有终止符测试走真实 `SQLParser.parse()`；
3. **官方语法面**：从 TDSQL/MySQL 官方产生式生成 TypeRule/alias/attribute/KFN 矩阵，修复 FLOAT 单参数和 `.2`；
4. **结构守恒面**：CreateShape 覆盖 qname、TEMPORARY、IF NOT EXISTS、local options、完整 partition，并支持 named PRIMARY constraint；
5. **证据面**：真实提交 manifest/test/generator/rebuild 资产，补入本报告反例并由 CI 生成文档表。

最小新增用例数不重要，重要的是按维度生成，而不是继续手写几个代表例。至少应有：

- executable comment：位置 × 主尾 atom 的笛卡尔积；
- semicolon：终止符数量 × 空白/注释/多语句；
- type：每个产生式的下界/上界/越界/别名/属性；
- gate：CreateShape 每个字段的一个正确候选 + 单点变异；
- constraint：带名 PRIMARY、无名 PRIMARY KFN、与三类主目标组合。

## 15. 下一轮开发准入门槛

- [ ] BLOCK-12-01：可执行注释位置合法，并与主 token 流共享分区计数/profile；本报告 4 类反例全部失败关闭；
- [ ] BLOCK-12-02：0/1 分号通过，2 个及以上和多语句端到端不恢复；
- [ ] BLOCK-12-03：官方类型产生式清单可执行生成；`FLOAT(0)` 合法、`FLOAT(54)` 拒绝；DEC/字符别名/属性/.2/KFN 明确闭合；
- [ ] BLOCK-12-04：qname、CREATE modifiers、local options、partition 细节的全部定向变异被门禁拒绝；
- [ ] BLOCK-12-05：证据文件在仓库中真实存在，文档生成与测试命令可直接执行；
- [ ] MAJOR-12-01：`CONSTRAINT symbol PRIMARY KEY` 与方言目标可恢复；无名形态实现或登记 KFN；
- [ ] MAJOR-12-02：最终施工/验收指令只有一套，无 `_TYPE_SPEC/_TAIL_EDGES` 等陈旧锚点；
- [ ] 用户冻结决策保持不变；
- [ ] sqlglot 29.0.0 / 30.14.0 / 30.17.0 新矩阵一致；发布依赖精确锁定 30.14.0；
- [ ] 两份生产 fixture 规则集合精确相等；197 条语料与生产 14 表按既定口径无非目标漂移；
- [ ] 现有 71 项专项、全量 tests、verify_rules 全部达到原门槛。

## 16. 本轮测试记录

### 16.1 现有专项

```text
python -m pytest -q \
  tests/test_parser_tdsql_dialect_fallback.py \
  tests/test_r077_r054_tdsql_syntax.py \
  tests/test_r061_index_name_quoting.py

71 passed, 3 warnings
```

### 16.2 Rev.M 文档代码独立矩阵

```text
sqlglot 29.0.0 : manifest planner/candidate/gate 410 cases, 0 failure
sqlglot 30.14.0: manifest planner/candidate/gate 410 cases, 0 failure
sqlglot 30.17.0: manifest planner/candidate/gate 410 cases, 0 failure

tail atoms length 0..4: 1555 sequences, 37 accepted, 0 mismatch
```

上述结果只代表附录当前覆盖域通过，不替代 BLOCK-12-05 所要求的真实 pytest 资产和端到端测试。

### 16.3 当前 main 全量回归

```text
python -m pytest -q

1384 passed, 10 warnings in 263.27s (0:04:23)
```

10 条均为既有的 Pydantic、Starlette/httpx 与 pytest 弃用告警；本轮仅新增评审文档，未修改产品代码。

### 16.4 规则覆盖基线

```text
python tests/rule_audit_materials/verify_rules.py

规则总数 119 / 文件审核已覆盖 107 / 未覆盖 0 / 断言失败 3
失败项：R023_01、R098_01、R116_01，均仍为既有 R036/R037 漏触发
```

脚本因这 3 条既有失败返回退出码 1；结果与 Rev.M 登记基线同名同因，本轮未出现新增差异。

### 16.5 官方依据

- [腾讯云 TDSQL MySQL 版：建表](https://cloud.tencent.com/document/product/557/8767)：分片/广播、`CONSTRAINT`、key part、列属性、表选项、Range/List 分片定义；
- [腾讯云 TDSQL MySQL 版：兼容性](https://intl.cloud.tencent.com/zh/document/product/1042/38180)：MySQL 数据类型与字面量兼容范围；
- [腾讯云 TDSQL MySQL 版：二级分区](https://intl.cloud.tencent.com/zh/document/product/1042/33361)：旧代际 Hash/Range + Range/List 二级分区顺序；
- [腾讯云 DTS：TDSQL MySQL 同步使用说明](https://cloud.tencent.com/document/product/571/105000)：旧/新分区代际能力画像；
- [MySQL 5.7 数值类型语法](https://dev.mysql.com/doc/refman/5.7/en/numeric-type-syntax.html)：仅在腾讯声明继承 MySQL 后补充参数边界与别名；
- [MySQL 5.7 字符串类型语法](https://dev.mysql.com/doc/refman/5.7/en/string-type-syntax.html)：仅用于补充字符别名、属性和 ENUM/SET 边界。

## 17. 最终意见

Rev.M 的大方向已经接近可施工：表尾从 FSM 改为 typed profile、定义项从字符串变为结构形状、测试从散落数字转向 manifest，这些都应保留。

本轮 No-Go 的原因不是“还想再找几个边角”，而是三个承重证明仍不成立：

1. 输入中的可执行语法没有按原位置参与整句判定；
2. 候选守恒只覆盖定义列表，没有覆盖 CREATE 顶层与表尾；
3. 官方语法与验收真源都还不是机器闭合、仓库可复现的集合。

把这三项连同分号集成点一次性收敛后，下一轮可以直接按新增 manifest 和 CreateShape mutation suite 作最终准入，不需要再重复前十一轮的逐例争论。
