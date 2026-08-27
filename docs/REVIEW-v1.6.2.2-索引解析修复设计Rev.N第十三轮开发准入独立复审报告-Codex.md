# v1.6.2.2 索引解析修复设计 Rev.N 第十三轮开发准入独立复审报告

## 1. 评审结论

**结论：Rev.N 暂不通过开发准入（No-Go）。**

Rev.N 对第十二轮意见作了实质整改：证据资产已经真实提交，终止分号链路、主 token 表尾 profile、CreateShape 的顶层/普通表尾字段、FLOAT 两类产生式和具名 PRIMARY 的基本形态均有明显改善。现有 501 条 manifest 在 sqlglot 29.0.0、30.14.0、30.17.0 三版上也确实均为 511 项全绿。

但是，本轮把验证从“恢复成 `Create`”继续向下推进到 `ParsedSQL → RuleChecker`，并对现有 manifest 没有覆盖的复合原子内部位置、官方类型同义词和证据执行环境做交叉测试后，发现 **5 项 BLOCK、2 项 MAJOR**：

- **BLOCK-13-01**：列级 `UNIQUE`、`CONSTRAINT … UNIQUE`、`SERIAL` 的唯一语义没有进入 `ParsedSQL.indexes`，发布锁定版上应触发的 **R054 被漏掉**；
- **BLOCK-13-02**：可执行注释只有 owner token，没有原始字符位置与 atom 内部位置，仍可把非法 SQL 重排成合法 profile 后恢复；
- **BLOCK-13-03**：官方类型产生式仍不闭合，既有合法形态未登记，也会接纳非字符类型的字符集/排序规则属性；
- **BLOCK-13-04**：证据一键命令在默认 Windows/GBK 环境直接崩溃，使用环境中的 30.12.0 而非发布 pin，登记的也不是实际文件字节哈希，且无法在已施工文件上复跑；
- **BLOCK-13-05**：`CONSTRAINT pk PRIMARY KEY (…) COMMENT '…'` 与 TDSQL 方言尾共存时仍保留为 `Command`，列结构没有恢复；
- **MAJOR-13-01**：CreateShape 明确忽略列注释存在性，候选删除列注释仍通过门禁，会改变 R029；
- **MAJOR-13-02**：验收断言仍停留在 AST 类型，且当前准出表存在旧路径、错误 collect 公式和 KFN 断言缺口，无法防住上述语义问题。

前三项中，BLOCK-13-01 与用户本次要修复的 R054 核心能力直接相交；BLOCK-13-02 会吞掉非法 SQL；BLOCK-13-05 会继续留下“无 E999 但结构为空”的静默审核错误。因此不能带着这些问题进入开发。

## 2. 评审对象与边界

| 项 | 内容 |
|---|---|
| 仓库 | `C:\Codex\tdsql-sqlcheck` |
| 分支 | `main` |
| 第十二轮报告 | `79dbdfe34c4a7257c699754b4327743cfb93e75b` |
| A 的 Rev.N 提交 | `4d6968a059b017ce1e966ca969175c8d9920602f` |
| 设计文档 | `docs/DESIGN-v1.6.2.2-索引类型误判与唯一索引注释解析崩溃修复详细设计说明书.md` |
| 证据目录 | `docs/evidence/v1.6.2.2/` |
| 产品代码状态 | A 本次提交仍为设计与证据资产；现网产品代码未施工 |
| 本轮动作 | 独立评审、临时重建和测试；未修改产品代码 |

本轮继续遵守用户已冻结决策，不重新争论：

- 目标实例 `TDSQL_DISTRIBUTED BY HASH(cust_no)` 合法；
- `shardkey=noshardkey_allset` 是广播表合法哨兵；
- 使用 sqlglot 词法器；
- `SPATIAL` 暂映射为 `NORMAL`；
- KFN-1 `MAXVALUE`、ADJ-6 保持既定处置；
- `CONSTRAINT … UNIQUE` 本期不扩能力；
- `NEW_SECONDARY` 未取得目标实例证据前继续登记但不放行。

BLOCK-13-01 对 `CONSTRAINT … UNIQUE` 的要求是**落实“不支持就失败关闭”这一冻结决策**，不是要求本期开放该能力。

## 3. 独立验证方法与总览

### 3.1 实际执行

1. 在干净 `main` 上 fast-forward 到 `4d6968a`；
2. 直接运行仓库提交的 `run_all.py`，再以临时 UTF-8 环境运行以继续后续证据；
3. 保留临时重建树，直接执行设计重建后的 `parser_legacy.py`；
4. 在 sqlglot 29.0.0、30.14.0、30.17.0 三版分别运行 511 项既有 manifest；
5. 构造不在 manifest 中的 R054 语义形态、可执行注释 atom 内部位置、官方类型别名/边界、列属性族和候选 AST 变异；
6. 将验证推进到 `SQLParser.parse()`、`ParsedSQL.indexes/columns` 和 `RuleChecker` 的规则集合；
7. 核对腾讯 TDSQL 官方建表与兼容性页面；仅在腾讯明确声明继承 MySQL 的类型细节处，以 MySQL 5.7 官方手册补充产生式；
8. 运行现有 71 项专项、`verify_rules.py` 与全量仓库测试。

### 3.2 结果摘要

| 检查 | 结果 |
|---|---|
| Rev.N 既有 manifest | 三版均 **511 passed，0 failed，0 skipped** |
| 默认 `python docs/evidence/v1.6.2.2/run_all.py` | **失败**：Windows/GBK 打印 `✅` 时 `UnicodeEncodeError` |
| 临时设置 `PYTHONUTF8=1` 后的 `run_all.py` | 511 passed；两个生成器文本包含检查通过 |
| 发布锁定版 30.14.0 的 R054 独立语义测试 | 3 种唯一形态均未进入 `indexes`，应触发的 R054 均缺失 |
| 可执行注释 atom 内部反例 | `ENGINE` 内部与 `TDSQL_DISTRIBUTED BY … HASH` 内部均 `plan=True → Create`，三版一致 |
| 官方类型独立矩阵 | 多个合法形态普通 `plan=False`；非字符类型字符属性可 `plan=True → Create` |
| CreateShape 列注释变异 | 正确候选、改注释、删注释三者门禁均为 `True` |
| 现有专项 | **71 passed，3 warnings** |
| `verify_rules.py` | 119 / 107 / 0 / 3，3 条与既有基线同名同因 |
| 当前 main 全量 | 见 §16.5 |

现有 511 项全绿与本轮 No-Go 不矛盾：511 项主要断言“是否为 Create/是否失败关闭”，而本轮发现集中在“Create 内部语义是否完整、下游规则是否仍正确”以及 manifest 尚未覆盖的组合维度。

## 4. 第十二轮问题闭环复核

| 第十二轮项 | Rev.N 状态 | 本轮结论 |
|---|---|---|
| BLOCK-12-01 可执行注释位置 | 增加 owner token 并合并 atom | **部分关闭**；四类旧反例关闭，但复合 atom 内部仍可绕过，转 BLOCK-13-02 |
| BLOCK-12-02 终止分号 | 恢复链改用未删分号原串 | **关闭**；0/1/多分号集成路径符合门槛 |
| BLOCK-12-03 官方类型闭合 | 多产生式 + 新别名/KFN | **部分关闭**；FLOAT 主问题关闭，但官方字符串产生式、属性族和别名语义仍不闭合，转 BLOCK-13-03/01 |
| BLOCK-12-04 CreateShape | 比较 head/definitions/tail | **大部分关闭**；13 个旧顶层/表尾变异关闭，但列注释被显式忽略，转 MAJOR-13-01 |
| BLOCK-12-05 可执行证据 | 资产真实提交 | **形式关闭、执行未关闭**；默认命令、依赖 pin、哈希和施工后复跑仍不成立，转 BLOCK-13-04 |
| MAJOR-12-01 具名 PRIMARY | 解包 `exp.Constraint` | **部分关闭**；无自身 COMMENT 的组合关闭，带自身 COMMENT + 方言尾仍失败，转 BLOCK-13-05 |
| MAJOR-12-02 指令冲突 | 清理多数历史锚点 | **部分关闭**；当前 G-5/M-7/M-10 仍有旧路径和错误公式，转 MAJOR-13-02 |
| MINOR-12-01 计数口径 | 生成器区分三类数字 | **正文表已关闭**；当前准出表仍写错公式，归入 MAJOR-13-02 |

## 5. BLOCK-13-01：恢复成 Create，但唯一约束没有进入 R054 的语义输入

### 5.1 官方判据与项目判据

腾讯 TDSQL [建表文档](https://cloud.tencent.com/document/product/557/8767)同时给出：

- 分布式表的每个主键和唯一索引都必须包含 shardkey；
- 列定义允许 `[UNIQUE [KEY]]`；
- 表定义允许 `[CONSTRAINT [symbol]] UNIQUE …`。

MySQL 5.7 官方数值类型手册又明确 `SERIAL` 等价于 `BIGINT UNSIGNED NOT NULL AUTO_INCREMENT UNIQUE`。腾讯兼容性文档声明支持 MySQL 全部数据类型，因此 `SERIAL` 不能只当一个没有副作用的类型名。

R054 的实现正是逐个遍历 `parsed.indexes/index_definitions` 中的 UNIQUE；若解析器没有产出条目，正则只兜底 `UNIQUE KEY|INDEX (…)`，无法识别列级 UNIQUE、`CONSTRAINT uq UNIQUE (…)` 或 SERIAL 的隐式 UNIQUE。

### 5.2 发生原因

Rev.N 的源规划器和候选门禁能够识别这些形状，但 `_parse_create()` 的语义提取只处理：

- schema 顶层的 `UniqueColumnConstraint`；
- schema 顶层的 `IndexColumnConstraint`；
- `ColumnDef` 只提取 PRIMARY/NOT NULL/DEFAULT，不把列级 UNIQUE 生成索引；
- `exp.Constraint` 不生成 `parsed.indexes`；
- `SERIAL` 不展开隐含的 NOT NULL/AUTO_INCREMENT/UNIQUE。

因此“AST 形状守恒”与“审核语义守恒”在这里断开了。

### 5.3 发布锁定版 30.14.0 实测

以下三条都使用用户已确认合法的 HASH 方言；`sk` 已在主键内，但每个额外唯一约束都只含 `id`，按 TDSQL 必须触发 R054：

```sql
-- A. 列级 UNIQUE
CREATE TABLE t (
  id INT UNIQUE COMMENT 'id',
  sk INT COMMENT 'sk',
  PRIMARY KEY(id, sk)
) ENGINE=InnoDB COMMENT='t'
TDSQL_DISTRIBUTED BY HASH(sk);

-- B. 用户冻结为本期不支持的 CONSTRAINT UNIQUE
CREATE TABLE t (
  id INT COMMENT 'id',
  CONSTRAINT uq UNIQUE(id),
  sk INT COMMENT 'sk',
  PRIMARY KEY(id, sk)
) ENGINE=InnoDB COMMENT='t'
TDSQL_DISTRIBUTED BY HASH(sk);

-- C. SERIAL 隐含 UNIQUE
CREATE TABLE t (
  id SERIAL COMMENT 'id',
  sk INT COMMENT 'sk',
  PRIMARY KEY(id, sk)
) ENGINE=InnoDB COMMENT='t'
TDSQL_DISTRIBUTED BY HASH(sk);
```

实测：

| 形态 | 最终 AST | `parsed.indexes` | R054 |
|---|---|---|---|
| 列级 UNIQUE | `Create` | `[]` | **未触发** |
| `CONSTRAINT uq UNIQUE(id)` | `Create` | `[]` | **未触发** |
| SERIAL 隐含 UNIQUE | `Create` | `[]` | **未触发** |

30.17.0 结果相同；29.0.0 对列级 UNIQUE 组合失败关闭，但另外两类仍恢复且漏 R054。发布 pin 是 30.14.0，故不能用 29.0.0 的失败关闭规避问题。

这不是理论门禁变异，而是已执行到 `RuleChecker` 的核心规则漏审。

### 5.4 必须如何修改

1. **列级 UNIQUE**：这是腾讯文档明示语法，必须在 `_parse_column_def/_parse_create` 中形成一个 UNIQUE `index` 语义条目；至少列集合必须准确，匿名索引名可为空；
2. **`CONSTRAINT … UNIQUE`**：遵守用户冻结决策，本期规划器应对该定义失败关闭，不能一边写“不打开”，一边把它作为其他主目标的伴随语法恢复成 Create；
3. **SERIAL**：不得以普通无副作用类型放行。推荐本期先转为具名 KFN-A；若坚持支持，则必须展开 NOT NULL、AUTO_INCREMENT、UNIQUE，并让 R054、R038 等消费者得到等价语义；
4. 为三种形态各增加“唯一约束包含/不包含 shardkey”双向用例，断言：
   - `ParsedSQL.indexes` 精确结构；
   - R054 精确命中/不命中；
   - 不允许只断言 AST 是 Create；
5. 对所有 `CreateShape` 正例增加 `ParsedShape` 或规则集合守恒层，防止 AST 节点存在但语义抽取器不认识。

## 6. BLOCK-13-02：owner token 不能证明可执行注释位于完整 atom 边界

### 6.1 发生原因

Rev.N 把可执行注释表示为 `(owner_idx, payload)`。这能区分建表头、定义列表和表尾大区间，却不能区分：

- 注释在一个表尾 atom 完整结束之后；
- 注释插在一个多 token atom 的内部。

`_scan_table_tail()` 在消费完一个完整表选项或分布声明之后才调用 `_flush_exec()`。所以挂在 `ENGINE` 或 `BY` token 上的注释，会被从原位置取出并“搬到”整个 atom 之后，再参与 profile 匹配。

### 6.2 三版一致的反例

```sql
-- ENGINE 与 = InnoDB 之间插入可执行分区
CREATE TABLE t (id INT, PRIMARY KEY(id) COMMENT 'p')
ENGINE
/*!50100 PARTITION BY RANGE(id)
  (PARTITION p0 VALUES LESS THAN (10)) */
= InnoDB;

-- TDSQL_DISTRIBUTED BY 与 HASH(id) 之间插入可执行分区
CREATE TABLE t (id INT, PRIMARY KEY(id) COMMENT 'p')
TDSQL_DISTRIBUTED BY
/*!50100 PARTITION BY RANGE(id)
  (PARTITION p0 VALUES LESS THAN (10)) */
HASH(id);
```

在 29.0.0 / 30.14.0 / 30.17.0 上，两条均为：

```text
plan=True
final AST=Create
parse_error=False
```

数据库实际展开注释后得到的是把 `PARTITION BY` 插进 `ENGINE =` 或 `… BY HASH` 中间的非法语法；Rev.N 却分别重排成 `LOCAL + PARTITION`、`DIST + PARTITION` 后放行，属于真实吞错。

### 6.3 必须如何修改

- 可执行注释条目必须至少包含原始 `comment_start/comment_end`，不能只保留 owner token；
- 只有当注释 span 落在顶层 atom 的**边界集合**中才能合并；落在 `_consume_table_option()`、分布声明、shardkey 值、partition 定义等任一消费区间内部时必须失败关闭；
- 最稳妥的实现是 raw SQL 词法扫描得到注释 span，再由每个 consumer 返回自身原始字符区间；不要依赖“挂在前一个 token”推断完整位置；
- 新测试按“每个复合 atom × 前/中/后位置”生成，至少覆盖 ENGINE、CHARACTER SET、shardkey、TDSQL_DISTRIBUTED、PARTITION BY 和分区定义表；
- 测试必须断言真实 `SQLParser.parse()` 最终结果，不能只测 `_collect_executable_comments()`。

## 7. BLOCK-13-03：官方类型产生式仍是抽样表，不是闭合集

### 7.1 TDSQL 优先判据

腾讯 TDSQL [兼容性文档](https://intl.cloud.tencent.com/zh/document/product/1042/38180)明确写明支持 MySQL 的所有数据类型；腾讯 [建表文档](https://cloud.tencent.com/document/product/557/8767)又声明单表语法与 MySQL 完全一致。因而以下 MySQL 官方类型细节可作为腾讯兼容声明的补充，而不是把 MySQL 反过来凌驾于 TDSQL：

- `NCHAR VARCHAR`、`NATIONAL CHARACTER VARYING`、`NATIONAL CHAR VARYING` 是 `VARCHAR` 官方同义形态；
- `CHAR BYTE` 是 BINARY 别名；
- `ASCII/UNICODE` 是字符集属性别名；
- `TEXT(M)/BLOB(M)` 会选择足够容纳 M 的最小 TEXT/BLOB 类型，M 不能被硬截断在 65535；
- 字符集与 COLLATE 只属于字符字符串类型及其同义词；
- `SERIAL DEFAULT VALUE` 是整数列的官方约束别名。

来源：[MySQL 5.7 字符串类型语法](https://dev.mysql.com/doc/refman/5.7/en/string-type-syntax.html)、[National Character Set](https://dev.mysql.com/doc/refman/5.7/en/charset-national.html)、[数值类型语法](https://dev.mysql.com/doc/refman/5.7/en/numeric-type-syntax.html)。

### 7.2 合法形态未进入 KFN，也未恢复

以下在三版均为普通 `plan=False`，最终 E999；manifest 没有登记：

| 官方形态 | Rev.N |
|---|---|
| `NCHAR VARCHAR(10)` | `plan=False` |
| `NATIONAL CHARACTER VARYING(10)` | `plan=False` |
| `NATIONAL CHAR VARYING(10)` | `plan=False` |
| `CHAR BYTE` | `plan=False` |
| `TEXT(65536)` | `plan=False` |
| `BLOB(65536)` | `plan=False` |
| `VARCHAR(10) ASCII` | `plan=False` |
| `VARCHAR(10) UNICODE` | `plan=False` |
| `BIGINT SERIAL DEFAULT VALUE` | `plan=False` |

这直接推翻“官方类型与消费器已经登记完整、上游支持后无需改代码”的结论。

### 7.3 列级 CHARACTER SET 的跨版本修复只做了一半

Rev.N 新增 `_charset_kw_end()`，但只在**表选项**消费器复用。列约束仍只判断 `TokenType.CHARACTER_SET`。

因此：

```sql
CREATE TABLE t (
  id VARCHAR(10) CHARACTER SET utf8mb4,
  UNIQUE KEY u(id) COMMENT 'u'
) ENGINE=InnoDB;
```

- 29.0.0 / 30.14.0：恢复为 Create；
- 30.17.0：`plan=False + E999`。

文档宣称 `CHARACTER SET` 跨三版已闭合，但 R12-CS 只覆盖表级 option，未覆盖列级官方语法。

### 7.4 非字符类型的字符属性被错误接纳

`_consume_column_constraints()` 没有接收类型 family，因而会对任何类型接受 CHARACTER SET/COLLATE。发布锁定版 30.14.0 实测：

| 非法/无目标证据形态 | Rev.N |
|---|---|
| `INT CHARACTER SET utf8mb4` | `plan=True → Create` |
| `DATE COLLATE utf8mb4_bin` | `plan=True → Create` |
| `JSON CHARACTER SET utf8mb4` | `plan=True → Create` |

MySQL 官方把字符集/排序规则限定到 CHAR、VARCHAR、TEXT、ENUM、SET 及同义词。即使目标 TDSQL 版本存在额外扩展，在没有目标实例证据前也应按本方案自己的 provenance 原则失败关闭，不能默认放行。

### 7.5 必须如何修改

1. 类型清单必须覆盖“源拼写 + 多 token 同义词 + 参数产生式 + 属性产生式 + 隐含语义”，不只是 `类型名 → 参数范围`；
2. `_consume_column_constraints()` 必须取得 family；CHARACTER SET/COLLATE/BINARY/ASCII/UNICODE 只对官方字符族开放；
3. `_charset_kw_end()` 在列级和表级共用，三版各跑同一组列级用例；
4. `TEXT/BLOB(M)` 不得以 65535 作为语法上限；至少补 65535/65536/16777215/16777216 边界，无法恢复则登记 KFN-A；
5. 补齐 National/CHAR BYTE/ASCII/UNICODE/SERIAL DEFAULT VALUE，或逐条进入 KFN-A；
6. SERIAL 的隐含约束按 BLOCK-13-01 处理，不能只让 AST 变成 Create；
7. 从官方产生式生成“合法 + 越界 + family 错配”三类矩阵，不能继续只补发现过的单例。

## 8. BLOCK-13-04：证据资产已提交，但还不能作为开发准出闸门

### 8.1 默认 Windows 命令不可执行

在本项目当前 Windows/PowerShell 环境直接执行文档唯一命令：

```text
python docs/evidence/v1.6.2.2/run_all.py --keep
```

第一阶段重建成功后，在打印 `✅` 时抛出：

```text
UnicodeEncodeError: 'gbk' codec can't encode character '\u2705'
```

返回码为 1，manifest 与生成器均没有执行。只有临时设置 `PYTHONUTF8=1` 后才得到 511 passed。文档写“当前提交上一条命令即可执行”与实际不符。

### 8.2 一键命令使用环境版本，而不是发布 pin

当前仓库实际安装的是 sqlglot 30.12.0，`requirements.txt/pyproject.toml` 仍为 `>=26`。`run_all.py`：

- 不应用设计中的 pin 修改；
- 不断言 `sqlglot.__version__ == 30.14.0`；
- 不创建 29.0.0/30.14.0/30.17.0 三版隔离环境。

所以本轮临时 UTF-8 后“一键全绿”实际证明的是 30.12.0，不是发布锁定版。三版 511 项是本轮另行指定 PYTHONPATH 后才复现的，不是证据命令自身保证的。

### 8.3 “逐字节相同”哈希并不是文件字节哈希

文档登记：

```text
aaf11ddd96ad2e7bc96c3bc5615ef9b7111bc16f4c4b23801e1ae342cd0913d4
```

这是 `read_text().encode('utf-8')` 对换行归一后的文本计算值。Windows 重建文件实际含 CRLF，其真实字节 SHA256 是：

```text
bf708bf5200edacd16efaa52844cd01c84eb5f92b231c6ec17d78bd2b525db3f
```

因此 N-11/C.3 可以称“规范化文本哈希”，不能称“提交文件逐字节相同”。

### 8.4 施工后无法按当前流程复跑

`rebuild_from_design.py` 以“改动前块必须存在”为前提。把它对已经重建好的目标 parser 再执行，立即失败：

```text
AssertionError: §1.1 示意块与主干不匹配
exit=1
```

但当前施工检查单又要求施工后运行 `run_all.py` 并比对提交文件哈希。脚本始终从“当前工作树 parser”作为基线，产品代码一旦已经施工，就无法再重建。

### 8.5 必须如何修改

- 输出改为纯 ASCII，或显式配置可用编码；默认 Windows 命令必须直接成功；
- 分成两个明确模式：
  1. `design`：从固定基线 commit/blob 重建期望文件；
  2. `implementation`：不再二次打补丁，直接校验当前提交 parser 与期望规范化内容，并在当前实现上跑验收；
- 固定基线必须来自不可变 commit/blob，不能取“调用时工作树里的当前 parser”；
- 明确选择“规范化文本哈希”或“真实字节哈希”，脚本、文档和验收名称一致；
- 一键准出必须断言发布 sqlglot 30.14.0，并另有可复现三版本 CI/tox/nox 矩阵；
- 同时校验 `requirements.txt` 与 `pyproject.toml` 的精确 pin，不能只校验 parser；
- 施工完成后的最终命令必须测试**当前提交的产品实现**，而不是只测试临时重建副本。

## 9. BLOCK-13-05：具名 PRIMARY 的自身 COMMENT + 方言尾仍不能恢复

### 9.1 官方形态

腾讯建表语法给出：

```text
[CONSTRAINT [symbol]] PRIMARY KEY [index_type] (key_part,...) [index_option] ...
index_option: index_type | COMMENT 'string'
```

所以以下是具名 PRIMARY 与本次已确认合法 HASH 方言的正常组合：

```sql
CREATE TABLE t (
  id INT COMMENT 'id',
  CONSTRAINT pk PRIMARY KEY(id) COMMENT 'p'
) ENGINE=InnoDB
TDSQL_DISTRIBUTED BY HASH(id);
```

### 9.2 实测与原因

在三版上，源计划都能生成；掩掉 TDSQL 方言后，候选 `exp.Constraint` 的 expressions 是：

```text
[PrimaryKey, CommentColumnConstraint]
```

Rev.N 的约束门禁写死 `len(inner) == 1`，因此 gate=False。最终保留最初的 `Command`：

```text
final AST=Command
parse_error=False
columns=[]
```

这不会产生 E999，却使基于列信息的审核规则缺少输入，是静默失败。R12-CN 只测“具名 PK + 另一个 UNIQUE 的 COMMENT”，没有测“具名 PK 自己带 COMMENT”，所以 511 项仍全绿。

### 9.3 必须如何修改

- 解包 `exp.Constraint` 时允许且严格识别一个 PRIMARY 主节点及其合法 index options；不能只按 inner 数量判断；
- source/candidate 都要比较 constraint symbol、PRIMARY 键列、USING 与 COMMENT 是否按批准规则保留/掩码；
- 新增具名 PRIMARY 自身 COMMENT 与 HASH、广播、普通表选项、独立 UNIQUE COMMENT 共存矩阵；
- 端到端断言 `Create`、`columns`、`has_primary_key`、表/列注释以及规则集合，禁止只断言 AST 类型。

## 10. MAJOR-13-01：CreateShape 忽略列注释存在性，会改变 R029

### 10.1 发生原因

源侧列指纹把 COMMENT 记成 `("COMMENT", None)`，候选侧也能提取 COMMENT；但 `_GATE_IGNORED_COL_CONSTRAINTS` 又把 `COMMENT` 整项过滤掉。

对白盒候选变异：

| 候选 | gate |
|---|---:|
| 保留 `id COMMENT 'original'` | True |
| 改成 `id COMMENT 'changed'` | True |
| 完全删除列 COMMENT | **True** |

R029 明确通过 `parsed.column_comments` 判断每一列是否有注释，因此“删除注释”不是装饰差异，会改变审核结果。

### 10.2 修改要求

- 至少比较列 COMMENT 的**存在性**；这是 R029 所消费的语义；
- 若决定比较文本，再使用单一的 MySQL 字符串规范化器处理单双引号和转义；
- 增加每个规则消费字段的“保留/删除/改变”候选变异；
- 建立 `ParsedSQL` 字段到规则消费者的清单，只有没有任何消费者、且 raw 保留的字段才允许进入 ignored 表。

## 11. MAJOR-13-02：验收真源仍不足以证明语义，当前准出表还有错误指令

### 11.1 断言停在 AST，漏掉核心规则错误

`test_manifest_case()` 对大多数 `pos` 只断言：

```text
ast == Create and not E999
```

R12-TY 的 SERIAL 因而通过，却没有断言其隐含 UNIQUE；R12-CN 的 `CONSTRAINT UNIQUE` 也只断言 Create，没有断言 `ParsedSQL.indexes` 或 R054。

`pos_known` 又只断言最终不是 Create，没有断言文档 N-4 所说的“规划器具名接受”。未来某个 KFN 从 `plan=True` 退化为普通 `plan=False`，测试仍会通过。

### 11.2 当前准出表仍有旧路径和错误公式

Rev.N 当前 §7.3/§7.4 仍存在：

- G-5 指向不存在的 `tests/test_parser_recovery_manifest.py` 与 `tests/parser_recovery_manifest.py`，真实路径在 `docs/evidence/v1.6.2.2/`；
- G-5 写 collect 数 = `len(CASES) + 变异断言数`，实际正确公式是 `501 + 9 个变异 suite + 1 个 fuzz = 511`，不是 `501 + 53`；
- M-7/M-10 仍引用 `tests/manifest_doc.py`、`tests/codestat.py`；
- N-7 写“不打开 CONSTRAINT UNIQUE”，但 R12-CN 又把其作为 pos 恢复；结合 BLOCK-13-01，当前行为已经在伴随其他主目标时打开并丢失语义。

### 11.3 证据脚本还有两个静默退化口

- 变异测试对候选解析异常直接 `continue`；当前三版 44 个变异候选都能解析，但未来版本若变成不可解析，声明的“53 条断言”会减少却不失败；
- `run_all.py` 用 `generated_text in doc` 做正文检查，只证明正确文本是正文子串，不证明目标区段唯一、也不证明没有并存的陈旧表。

### 11.4 修改要求

1. 为每类 `pos` 定义最低语义 oracle：columns、indexes、primary key、table options、规则集合；
2. `pos_known` 必须断言 `plan=True`、最终失败关闭、KFN 编号一致；
3. 变异候选若不能解析必须测试失败，或显式计入 `unparseable` 分类并由生成器统计；
4. 生成器按带标记的唯一正文区段作精确替换/精确比对，要求出现次数恰好为 1；
5. 从真实资产生成 G-5/M-7/M-10 的路径与公式，不再手写；
6. 对 `CONSTRAINT UNIQUE` 按用户冻结决策统一为失败关闭，不保留互相冲突的 pos 说明。

## 12. 已确认关闭、不要重复推翻的部分

为减少下一轮修改频度，本轮明确确认以下机制可保留：

1. `sql_recover = sql.strip()` 进入恢复规划器，终止分号不再被 `rstrip(';')` 预吞；
2. 0/1/2/3 分号与多语句当前集成行为符合门槛；
3. 主 token 表尾 typed atom/profile 的旧重复分布、重复 partition、广播再分区反例已经关闭；
4. FLOAT(p) 与 FLOAT(M,D) 拆分方向正确，`FLOAT(0)`/`FLOAT(53)`/`FLOAT(54)`边界已修正；
5. qname、TEMPORARY、IF NOT EXISTS、本地表选项和普通 partition 细节已经进入 CreateShape；第十二轮 13 个变异不再放行；
6. 无自身 COMMENT 的 `CONSTRAINT symbol PRIMARY KEY` 基本形态能够通过门禁；
7. 表级 `CHARSET/CHARACTER SET` 的 30.17.0 词法差异已用文本识别关闭；
8. 证据资产从“只抄在文档”升级为仓库真实文件，这个方向正确；
9. 用户冻结的 HASH、广播哨兵、SPATIAL=NORMAL、MAXVALUE、ADJ-6、NEW_SECONDARY 决策保持不变。

## 13. 建议 A 一次性提交的 Rev.O 闭环包

为了下一轮直接做最终 Go/No-Go，建议一次提交以下五个成组闭环面：

1. **审核语义面**：列级 UNIQUE 进入 `ParsedSQL.indexes`；CONSTRAINT UNIQUE 按冻结决策失败关闭；SERIAL 进入 KFN 或完整展开语义；新增 R054 双向规则断言；
2. **原始位置面**：可执行注释保存字符 span，只允许位于完整 atom 边界；生成所有 compound atom 的前/中/后矩阵；
3. **官方类型面**：补 National/CHAR BYTE/ASCII/UNICODE/TEXT-BLOB M/SERIAL DEFAULT VALUE，列属性带 family，列级 CHARACTER SET 三版一致；
4. **结构守恒面**：具名 PRIMARY 自身 COMMENT 组合可恢复；列 COMMENT 存在性进入门禁；
5. **证据工程面**：Windows 默认可跑、发布 pin 可验证、三版矩阵可复现、规范化哈希命名准确、施工前后两模式、当前准出表由资产生成。

不建议再按本报告每一条 SQL 单独加 if；应分别在 ParsedSQL 语义抽取、atom boundary、TypeProduction、EvidenceRunner 四个抽象层处理。

## 14. 下一轮开发准入门槛

- [ ] 三种唯一形态在 30.14.0 下均产出准确语义；每个唯一索引包含/不包含 shardkey 时 R054 精确不报/报；
- [ ] `CONSTRAINT … UNIQUE` 按冻结决策始终失败关闭，不因存在其他主目标被顺带恢复；
- [ ] SERIAL 不再以无隐含约束的普通类型进入 Create；
- [ ] executable comment 只能位于完整 atom 边界；本报告两个内部反例和 compound atom 全矩阵全部关闭；
- [ ] 本报告列出的 9 个官方类型形态全部恢复或进入具名 KFN；不得普通 `plan=False`；
- [ ] 非字符类型的 CHARACTER SET/COLLATE 失败关闭；列级 CHARACTER SET 三版一致；
- [ ] 具名 PRIMARY 自身 COMMENT + HASH/BROADCAST/UNIQUE COMMENT 端到端恢复，columns/PK/规则集合正确；
- [ ] 删除列 COMMENT 的候选被门禁拒绝；
- [ ] `run_all.py` 在默认 Windows 命令行直接通过，并明确输出/断言实际 sqlglot 版本；
- [ ] 施工后模式直接验证当前产品代码；依赖 pin 和 parser 规范化内容同时校验；
- [ ] 当前准出表不存在旧路径与错误 collect 公式；
- [ ] 三版新增矩阵全绿；发布 pin 30.14.0 精确锁定；
- [ ] 现有 71 项专项、全量 tests、verify_rules 与两份生产 fixture 达到原门槛；
- [ ] 用户冻结决策保持不变。

## 15. 本轮官方依据

- [腾讯云 TDSQL MySQL 版：建表](https://cloud.tencent.com/document/product/557/8767)：HASH/shardkey、每个唯一索引必须含 shardkey、列级 UNIQUE、CONSTRAINT PRIMARY/UNIQUE、index option、广播表；
- [腾讯云 TDSQL MySQL 版：兼容性](https://intl.cloud.tencent.com/zh/document/product/1042/38180)：支持 MySQL 全部数据类型及类型清单；
- [MySQL 5.7：字符串类型语法](https://dev.mysql.com/doc/refman/5.7/en/string-type-syntax.html)：仅在腾讯兼容声明下补充字符类型同义词、属性族和 TEXT/BLOB(M)；
- [MySQL 5.7：National Character Set](https://dev.mysql.com/doc/refman/5.7/en/charset-national.html)：NCHAR VARCHAR 与 NATIONAL … VARYING 等价形态；
- [MySQL 5.7：数值类型语法](https://dev.mysql.com/doc/refman/5.7/en/numeric-type-syntax.html)：SERIAL 与 SERIAL DEFAULT VALUE 的隐含约束。

## 16. 本轮测试记录

### 16.1 证据命令默认环境

```text
python docs/evidence/v1.6.2.2/run_all.py --keep

UnicodeEncodeError: 'gbk' codec can't encode character '\u2705'
exit=1
```

### 16.2 临时 UTF-8 后的证据命令

```text
PYTHONUTF8=1 python docs/evidence/v1.6.2.2/run_all.py --keep

SHA256（规范化文本）= aaf11ddd96ad2e7bc96c3bc5615ef9b7111bc16f4c4b23801e1ae342cd0913d4
511 passed, 3 warnings
§7.1 用例表：一致
§3.4 规模表：一致
```

### 16.3 三版现有 manifest

```text
sqlglot 29.0.0 : 511 passed, 3 warnings
sqlglot 30.14.0: 511 passed, 3 warnings
sqlglot 30.17.0: 511 passed, 3 warnings
```

### 16.4 现有专项与规则覆盖

```text
python -m pytest -q \
  tests/test_parser_tdsql_dialect_fallback.py \
  tests/test_r077_r054_tdsql_syntax.py \
  tests/test_r061_index_name_quoting.py

71 passed, 3 warnings

python tests/rule_audit_materials/verify_rules.py

规则总数 119 / 文件审核已覆盖 107 / 未覆盖 0 / 断言失败 3
失败项：R023_01、R098_01、R116_01，均仍为既有 R036/R037 漏触发
```

### 16.5 当前 main 全量回归

```text
python -m pytest -q

1384 passed, 10 warnings in 254.82s (0:04:14)
```

10 条 warning 均为现有 Pydantic 字段遮蔽、Starlette/httpx、httpx 原始内容参数和 pytest 类级实例 fixture 弃用告警；本轮未新增产品代码或告警。该结果只证明 Rev.N 的 docs/evidence 提交未破坏现有 v1.6.2.1 基线，不证明设计补丁正确。

## 17. 最终意见

Rev.N 已经从“文档自述”推进到“仓库内可执行原型”，这是本轮最重要的进步；第十二轮的多数结构性方向应保留。

本轮仍为 No-Go 的核心原因只有一句话：**当前证据证明了很多 SQL 能恢复成 Create，但还没有证明恢复后的审核语义与 TDSQL 真实约束相同。** R054 对三种唯一语义的实际漏报已经证明这不是抽象担忧。

下一版如果按 §13 的五个闭环面一次性处理，并让测试从 AST 分类下沉到 ParsedSQL/RuleChecker，本项目就具备进入开发的基础；不需要再继续围绕单个字符串来回修补。
