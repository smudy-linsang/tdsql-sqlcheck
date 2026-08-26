# v1.6.2.2 索引解析修复设计 Rev.L 第十一轮开发准入独立复审报告（Codex）

| 项目 | 结论 |
|---|---|
| 复审对象 | `docs/DESIGN-v1.6.2.2-索引类型误判与唯一索引注释解析崩溃修复详细设计说明书.md` Rev.L |
| 复审基线 | `98db321`（第十轮报告） |
| A 的修订提交 | `28aeecb`（Rev.K）+ `2a2978a`（Rev.L） |
| 复审日期 | 2026-08-26 |
| 复审范围 | DEF-1 假 UNIQUE、DEF-2 UNIQUE COMMENT、DEF-3 PRIMARY COMMENT、TDSQL 方言恢复链、结构守恒、测试真源、官方语法画像 |
| 产品代码 | **未修改**；本报告只评审设计并执行独立探针 |
| 最终结论 | **No-Go：暂不能进入开发** |
| 问题统计 | **7 项 BLOCK、2 项 MAJOR、2 项 MINOR** |

## 1. 结论先行

Rev.L 对上一轮 5 个 BLOCK、2 个 MAJOR 的整改方向大体正确，尤其是：

1. `PRIMARY KEY ... COMMENT` 已与 `UNIQUE KEY ... COMMENT` 共用恢复机制，没有另造第二条链；
2. 单终止分号问题已修正；
3. `PRIMARY KEY pk(id)`、重复 `USING`、`VALUES IN (-'x')` 等上一轮反例已被显式处理；
4. `ROW_FORMAT`、`STATS_PERSISTENT`、`STORAGE ENGINE` 等官方形态已重新纳入设计视野；
5. 用户已经决定的事项均予以保留：目标实例的 `TDSQL_DISTRIBUTED BY HASH(...)`、`shardkey=noshardkey_allset`、sqlglot 词法器、SPATIAL 维持 NORMAL、KFN-1、ADJ-6、NG-10/ADJ-11，本轮不重新争论产品决策。

但 Rev.L 仍不能开发，原因不是文案精度，而是可执行代码块存在能直接造成次生误审的结构性缺口：

- MySQL/TDSQL 会执行的 `/*!50100 ... */` 内容完全绕过验证；
- 表尾迁移表存在回环，两个一级分布声明可同时被恢复成 `Create`；
- 广播哨兵与普通 shardkey 没有分型，伪广播、广播再分区均被放行；
- 数据类型表双向失真：官方合法类型被拒、明确越界类型被接受；
- `SourceFingerprint` 记录了约束和索引细节，但门禁实际上不比较它们；
- 文档声称 `COLUMN_FORMAT` / `ENGINE_ATTRIBUTE` 已恢复，三版实测却全部仍失败；
- 测试“唯一真源”尚不存在，且正文中仍有互相冲突的硬断言。

这些问题若直接施工，会让“修复合法 SQL 假阳性”的补丁同时打开“非法或未验证 SQL 被恢复为合法 AST”的新通道，正是本项目此前多轮复审一直在防止的次生灾害。

## 2. 本轮独立验证方法与总结果

### 2.1 方法

本轮没有把当前缺陷主干当成合法性 oracle，而是同时使用四层证据：

1. 从 Rev.L §3.0c **逐字抽取可执行代码块**，直接调用 `_plan_recovery()`、`_blank_spans()`、`sqlglot.parse_one()`、`_validate_recovery_candidate()`；
2. 在 sqlglot `29.0.0`、拟锁定的 `30.14.0`、对照版本 `30.17.0` 上运行同一矩阵；
3. 用腾讯云 TDSQL MySQL 版官方建表、二级分区、兼容性及 DTS 文档校验 TDSQL 边界；
4. 仅在 TDSQL 官方明确继承 MySQL 数据类型语义处，用 MySQL 5.7 官方手册补充参数上下界。

### 2.2 结果摘要

| 验证项 | 结果 |
|---|---:|
| 设计代码三版本正/负向矩阵 | 29 个 case/版本；每版均出现 **25 个规范不一致** |
| 三版本一致性 | 29.0.0 / 30.14.0 / 30.17.0 的错误结果逐项一致，说明不是偶发依赖漂移 |
| 非法可执行注释 | 3/3 均为 `plan=True → Create → candidate_guard=True` |
| 表尾冲突 | 双 `TDSQL_DISTRIBUTED`、`shardkey + TDSQL_DISTRIBUTED` 均最终通过 |
| 广播哨兵非法近邻 | 带分区、括号包裹、与普通列混合 3/3 均最终通过 |
| 指纹反向鉴别 | 丢列约束、UNIQUE→NORMAL、UNIQUE→PRIMARY 3/3 均通过门禁 |
| 既有专项回归 | **71 passed / 3 warnings** |
| 全量回归 | **1384 passed / 10 warnings**；只证明当前主干稳定，不能证明尚未施工的 Rev.L 正确 |

> 三版本矩阵的“25 个不一致”不是把同一问题重复计数：它包含 10 个官方正例失败和 15 个负例在规划层或最终候选层被放行；三版各自得到同一结论。

## 3. 官方 TDSQL 语法离线摘要（供 A 直接使用）

以下摘要在 2026-08-26 重新从官方页面核对。它解决 A 无法访问 TDSQL 官网的问题，后续不应再用“sqlglot 能否解析”替代 TDSQL 合法性。

### 3.1 建表、索引、表选项、广播表

来源：[腾讯云 TDSQL MySQL 版《建表》](https://cloud.tencent.com/document/product/557/8767)（页面更新时间 2024-11-29）。

| 语法面 | 官方边界摘要 |
|---|---|
| 传统一级 HASH | `... [local_table_options] shardkey=column_name`；有二级分区时可再接官方二级分区形态 |
| 一级 RANGE/LIST | `TDSQL_DISTRIBUTED BY RANGE|LIST(column_name) [partition_options]` |
| 广播表 | 官方公开示例是精确哨兵 `shardkey=noshardkey_allset` |
| PRIMARY | `[CONSTRAINT [symbol]] PRIMARY KEY [USING BTREE] (key_part,...) [index_option]` |
| UNIQUE | `[CONSTRAINT [symbol]] UNIQUE [INDEX|KEY] [index_name] [USING BTREE] (key_part,...) [index_option]` |
| key_part | `col_name [(length)] [ASC|DESC]` |
| index_option | `USING BTREE` 或 `COMMENT 'string'` |
| 列属性 | `NOT NULL/NULL`、`DEFAULT`、`AUTO_INCREMENT`、`UNIQUE/PRIMARY KEY`、`COMMENT`、`COLLATE`、`COLUMN_FORMAT`、`ENGINE_ATTRIBUTE` |
| COLUMN_FORMAT 值 | 官方页面列出 `FIXED`、`DYNAMIC`、`DEFAULT` 三值 |
| 本地表选项 | `AUTO_INCREMENT`、字符集、排序规则、表 COMMENT、ENGINE、ROW_FORMAT 六值、三个 STATS 选项 |
| 分区定义选项 | `[STORAGE] ENGINE` 后可接 `COMMENT` |

需要特别说明：用户已经用目标实例事实确认 `TDSQL_DISTRIBUTED BY HASH(cust_no)` 合法。本报告继续把它作为 **TARGET_INSTANCE 契约**保留，不用旧代际公开文档推翻用户已经确认的生产事实。

### 3.2 二级分区

来源：[腾讯云 TDSQL MySQL 版《二级分区》](https://intl.cloud.tencent.com/zh/document/product/1042/33361)（页面更新时间 2024-01-06）。

| 组合 | 官方示例/说明 |
|---|---|
| 一级 HASH + 二级 LIST | `shardkey=first_name PARTITION BY LIST(city) (...)` |
| 一级 RANGE + 二级 LIST | `PARTITION BY LIST(order_id) (...) TDSQL_DISTRIBUTED BY RANGE(id) (...)` |
| 二级方法 | RANGE、LIST |
| 日期函数 | YEAR、MONTH、DAY；函数为空时按文档说明处理 |
| 唯一性 | 主键/唯一索引需要包含分区键 |

### 3.3 数据类型

来源：[腾讯云 TDSQL MySQL 版《兼容性》](https://intl.cloud.tencent.com/zh/document/product/1042/38180)。官方明确说明支持 MySQL 的数字、字符、日期、空间、JSON 类型，并逐项列出：

- 整数别名包含 `INTEGER`；定点包含 `DECIMAL`、`NUMERIC`；浮点包含 `FLOAT`、`REAL(M,D)`、`DOUBLE PRECISION(M,D)`；
- 空间类型共八种：`GEOMETRY`、`POINT`、`LINESTRING`、`POLYGON`、`MULTIPOINT`、`MULTILINESTRING`、`MULTIPOLYGON`、`GEOMETRYCOLLECTION`；
- 字符类型包括 CHAR/VARCHAR、BINARY/VARBINARY、BLOB/TEXT、ENUM、SET。

在 TDSQL 已明确继承 MySQL 类型语义的前提下，MySQL 5.7 官方边界可用于补足类型参数：

- [DECIMAL/NUMERIC](https://dev.mysql.com/doc/refman/5.7/en/fixed-point-types.html)：最大精度 65，`NUMERIC` 与 `DECIMAL` 同义；
- [BIT](https://dev.mysql.com/doc/refman/5.7/en/bit-type.html)：`M` 为 1..64；
- [CHAR/VARCHAR](https://dev.mysql.com/doc/refman/5.7/en/char.html)：CHAR 长度 0..255，VARCHAR 声明长度 0..65535（仍受行大小和字符集约束）；
- [时间类型](https://dev.mysql.com/doc/refman/5.7/en/date-and-time-type-syntax.html)：TIME/DATETIME/TIMESTAMP 的 fsp 为 0..6，`YEAR` 为省略宽度或 `YEAR(4)`；
- [浮点类型](https://dev.mysql.com/doc/refman/5.7/en/floating-point-types.html)：`FLOAT(p)` 与 `FLOAT/REAL/DOUBLE PRECISION(M,D)` 是不同参数语义，不能共用一个无上下界的 `M_D` 模式。

### 3.4 新旧二级分区代际

来源：[腾讯云 DTS《使用说明》](https://cloud.tencent.com/document/product/571/105000)（2026 年仍在维护的官方页）。该页明确区分：

1. 传统一级 HASH：`shardkey`；传统二级：`shardkey + PARTITION BY RANGE|LIST`；
2. 一级 RANGE/LIST：`TDSQL_DISTRIBUTED BY RANGE|LIST`，可组合传统 `PARTITION BY`；
3. 新二级 HASH：`TDSQL_DISTRIBUTED BY HASH + TDSQL_PARTITION BY RANGE|LIST`。

因此实现可以保留用户确认的目标实例 HASH 形态，但不得把不同代际任意拼成一个可循环的“语法并集”。

## 4. BLOCK-11-01：MySQL 可执行注释完全绕过整句验证

### 4.1 发生原因

Rev.L 已知 sqlglot 会“跳过”`/*!50100 PARTITION BY ... */`，但把它当作一个可由 fixture 兜底的例外。实际词法行为不是信息消失：payload 保存在前一个 token 的 `comments` 属性中，例如：

```text
TokenType.R_PAREN.comments = ['!50100 PARTITION BY RANGE() (...) ']
```

`_plan_recovery()`、`_scan_definition_list()`、`_scan_table_tail()`、`had_partition` 全都只遍历主 token，不检查 `token.comments`。于是服务器会执行的语法对规划器完全不可见。

### 4.2 独立复现

以下三例均带 `PRIMARY KEY(id) COMMENT 'pk'`，以确保进入 Rev.L 新增恢复链；在 sqlglot 30.14.0 上结果完全相同：

| 可执行注释 payload | plan | 候选 | 门禁 |
|---|---:|---|---:|
| `PARTITION BY RANGE() (...)` | True | Create | True |
| 两条连续 `PARTITION BY` | True | Create | True |
| `EVIL OPTION` | True | Create | True |

这不是“已知假阴性”，而是非法/未知服务器语法被恢复成成功 AST 的假阴性通道。

### 4.3 为什么 fixture 不能兜底

`report_6311_biz_tx_log.sql` 只能证明一条已知的合法 `/*!50100 PARTITION BY LIST ... */` 不回归，不能证明任意版本注释 payload 合法。精确规则集合仍应保留，但它不能替代语法验证。

### 4.4 必须修改的机制

1. 在规划入口遍历每个 token 的 `comments`；普通注释继续忽略，`!<版本号>` 开头的 MySQL 可执行注释必须单独处理；
2. 本版如只需支持 mysqldump 分区，则白名单仅接受**一个完整的** `PARTITION BY RANGE|LIST ...` payload，并复用现有分区消费器；
3. payload 必须消费到 EOF；残缺、重复、未知内容一律 `return None`；
4. 将来源记录为 `EXECUTABLE_COMMENT`，设置 `had_partition=True`；
5. 候选 AST 仍看不到该分区时，只能对“payload 已被完整验证”的这一种 provenance 使用具名例外；不得对所有 `/*!...*/` 统一豁免；
6. 新增普通注释、优化器 hint、合法版本分区、空 payload、非法 RANGE、重复分区、未知 option、定义体内可执行属性等正反向用例。

## 5. BLOCK-11-02：表尾迁移图存在回环，一级分布互斥仍未实现

### 5.1 发生原因

`_TAIL_EDGES` 包含：

```text
S2 --TDSQL_DISTRIBUTED--> S3
S3 --PARTITION_BY-------> S2
```

这两条边组成显式回环。状态只表达“当前阶段”，没有保留“是否已经出现一级分布/二级分区”的历史；`last_dist` 还会被后续声明覆盖。因此文档声称的“一级分布至多一个、二级分区至多一个、阶段只前进”并未由代码实现。

### 5.2 独立复现

| 表尾 | plan | 候选 | 门禁 | 结论 |
|---|---:|---|---:|---|
| `TDSQL_DISTRIBUTED HASH → PARTITION → TDSQL_DISTRIBUTED HASH` | True | Create | True | **非法双一级分布最终通过** |
| `shardkey=id → PARTITION → TDSQL_DISTRIBUTED HASH` | True | Create | True | **两种一级分布最终通过** |
| `PARTITION → TDSQL_DISTRIBUTED → PARTITION` | True | Create | False | 候选门禁偶然挡住，但违反“neg 必须先由规划器拒绝” |

三版 sqlglot 结果一致。

### 5.3 必须修改的机制

推荐不要继续给四状态补边，而是分两步：

1. **先解析成带子类型的 atom 列表**，例如 `LOCAL(ENGINE)`、`HASH_SHARDKEY`、`BROADCAST_SENTINEL`、`DIST_HASH`、`DIST_RANGE`、`PARTITION`、`TDSQL_PARTITION`；
2. **再按具名 capability profile 校验整个序列**，只列出经官方、目标实例或用户决策批准的有限序列。

若仍保留 FSM，至少必须同时维护并硬断言：

```text
seen_primary_distribution <= 1
seen_secondary_partition  <= 1
seen_local_option[name]    <= 1
```

并删除所有可回到既往阶段的循环路径。测试不能只覆盖 2、3 个样例，应对 atom 序列长度 1..4 做笛卡尔生成，所有未列明序列失败关闭。

## 6. BLOCK-11-03：广播哨兵与普通 shardkey 混型，核心 R054/R077 边界仍可被伪造

### 6.1 发生原因

`_consume_table_option()` 把以下形态全部归一成同一个 `SHARDKEY` atom：

- `shardkey=id`；
- `shardkey=(id,x)`；
- `shardkey=noshardkey_allset`；
- `shardkey=(noshardkey_allset)`；
- `shardkey=(noshardkey_allset,id)`。

官方广播语法却是精确哨兵 `shardkey=noshardkey_allset`。广播与 HASH 在后续可否分区、是否可与其他分布声明共存上不是同一状态。

### 6.2 独立复现

| 非法近邻 | plan | 候选 | 门禁 |
|---|---:|---|---:|
| `shardkey=noshardkey_allset PARTITION BY LIST...` | True | Create | True |
| `shardkey=(noshardkey_allset)` | True | Create | True |
| `shardkey=(noshardkey_allset,id)` | True | Create | True |

这直接触及用户本次最关心的广播表 R054/R077 判据：若下游哨兵识别与规划器归一口径稍有差异，非法形态可能被当作广播表免检。

### 6.3 必须修改的机制

1. `_consume_shardkey()` 必须返回语义子型：`HASH_SHARDKEY` 或 `BROADCAST_SENTINEL`；
2. `BROADCAST_SENTINEL` 只接受裸的、单个、精确大小写不敏感值 `noshardkey_allset`；括号、列表、混合列全部拒绝；
3. 广播哨兵进入终态，不得再接分区或第二个分布声明；
4. ADJ-6 是用户批准的**精确 characterization**，不能用通用 `S1→BROADCAST` 边覆盖所有单列、多列、哨兵形态；必须给该批准形态加专属 guard 和专属测试 ID；
5. 增加“合法广播/普通 HASH/多列项目契约/伪哨兵/广播+分区/哨兵+显式 BROADCAST”正交矩阵。

本条不重新打开 ADJ-6 产品决策，只要求实现严格等于用户批准的边界，不得无意扩大。

## 7. BLOCK-11-04：数据类型规范表仍是双向失真的不完整语法器

### 7.1 官方合法形态被拒

在三版 sqlglot 上逐项一致：

| 官方合法形态 | 失败点 |
|---|---|
| `INTEGER` | AST 规范化成 INT，指纹按字面比较而拒绝 |
| `NUMERIC(M,D)` | AST 规范化成 DECIMAL，指纹拒绝 |
| `REAL(M,D)` | AST 规范化成 FLOAT，指纹拒绝 |
| `DOUBLE PRECISION(M,D)` | tokenizer 将多词类型作为单元时，`_TYPE_MULTIWORD` 分支不可达 |
| `ENUM('a','b')` / `SET('a','b')` | Source 指纹写成“值个数”，AST 指纹写实际值，必不相等 |
| `INT ZEROFILL` | AST 规范化丢弃/折叠展示属性，指纹拒绝 |
| `CHAR(0)` / `VARCHAR(0)` | 复用了“正整数”判据，但 MySQL 官方下界允许 0 |
| `MULTIPOINT` 等四种复合空间类型 | `_TYPE_SPEC` 漏项；官方八种只列四种 |
| POINT/LINESTRING/POLYGON | 规划器虽接受，但拟锁定 sqlglot 仍 ParseError，必须登记能力边界或补机制 |

### 7.2 明确非法/越界形态被恢复成功

以下均得到 `plan=True → Create → candidate_guard=True`：

| 非法形态 | 缺失约束 |
|---|---|
| `DECIMAL(1,2)` | scale 不得大于 precision |
| `DECIMAL(66,0)` | precision 超过 65 |
| `DECIMAL(65,31)` | scale 超过 30 |
| `BIT(65)` | BIT 上限 64 |
| `CHAR(256)` | CHAR 上限 255 |
| `VARCHAR(65536)` | 声明长度超过 65535 |
| `YEAR(999)` | 目标 MySQL 5.7 语义只接受省略或 4 |
| `ENUM` / `SET`（无值表） | `ENUM_SET` 模式未在无括号分支强制失败 |
| `DATETIME DEFAULT CURRENT_TIMESTAMP(7)` | DEFAULT/ON UPDATE 时间函数精度未检查 0..6 |

此外 `_TYPE_ATTRS` 对所有类型统一开放，规划层会接受 `DATE UNSIGNED`、`VARCHAR UNSIGNED`、`JSON BINARY` 等类型/属性错配；当前碰巧多由 sqlglot 后续拒绝，不等于规划器正确。

### 7.3 必须修改的机制

把 `_TYPE_SPEC = name -> 模式字符串` 升级为结构化规则表，至少包含：

```text
source_names
canonical_name
arity
arg_ranges
cross_arg_validator
allowed_type_attributes
requires_parenthesized_values
candidate_canonicalizer
provenance/profile
```

关键要求：

1. 同义词在 Source 侧就规范化：至少包括 TDSQL 官方列出的 `INTEGER→INT`、`NUMERIC→DECIMAL`、`REAL→FLOAT`、`DOUBLE PRECISION→DOUBLE`；其他 MySQL 别名仅在对应 TDSQL profile 有证据时加入；
2. `DOUBLE PRECISION` 同时适配 tokenizer 的单 token 与双 token 表现；
3. M、D、fsp、BIT、CHAR/VARCHAR、YEAR 分别使用自己的边界，不再复用一个 `_int_val`；
4. ENUM/SET 强制括号和至少一个字符串，并在指纹中保留逐值内容，不能只记数量；
5. 类型属性按类型族开放，冲突属性显式拒绝；
6. 对 sqlglot 30.14.0 仍不能生成 AST 的官方类型，必须在 KFN 中逐项登记、说明出现频度并取得用户批准，不能宣称“全部恢复”。

## 8. BLOCK-11-05：SourceFingerprint 仍只生成了“丰富字符串”，门禁没有守恒

### 8.1 发生原因

规划阶段确实记录了：

```text
col:<列名>|<类型>|<约束序列>
idx:<kind>:<索引名>:(<key_part>):<选项>
```

但 `_ast_definition_fingerprints()` 对列只返回 `(COL, name, type)`，对任何非列定义统一返回 `(IDX, None, None)`；`_validate_recovery_candidate()` 也只比较列名和类型，完全不比较列约束、索引 kind、索引名、键列、前缀长度、顺序和选项。

### 8.2 白盒反向鉴别

Source：

```sql
CREATE TABLE t (
  id INT NOT NULL DEFAULT 7,
  x INT,
  UNIQUE KEY u(id) COMMENT 'uk'
)
```

将候选替换为下列三种错误 AST，门禁均返回 True：

| 候选错误 | 门禁 |
|---|---:|
| 丢掉 `NOT NULL DEFAULT 7` | True |
| `UNIQUE u(id)` 变成普通 `KEY v(x)` | True |
| `UNIQUE u(id)` 变成 `PRIMARY KEY(x)` | True |

此外，以 `|` 拼接结构指纹会让合法反引号列名 `` `a|b` `` 在 `split('|')` 时被拆坏；实测原文与候选 AST 均正确，门禁仍返回 False。

### 8.3 风险

span 门禁只证明“输入文本只在批准区间置空”，不能证明第三方 parser 对剩余文本没有错读、丢字段或归一化错误。结构门禁正是为了独立约束候选 AST；如果它不比较已记录的语义，Rev.K 所称“指纹守恒”仍不成立。

### 8.4 必须修改的机制

1. 使用 dataclass/tuple/dict 表示指纹，禁止用 `|`、`:`、`+` 拼接后再 split；
2. 列指纹比较 canonical type、参数、类型属性、NULL 性、DEFAULT 类别及值、AUTO_INCREMENT、COMMENT/COLLATE 等所有未被批准掩码的语义；
3. 索引指纹比较 kind、名称、逐 key_part、前缀长度、顺序、USING、COMMENT presence；
4. 只对明确列入 `auxiliary_spans` 的属性允许具名差异，例如 ASC/DESC；
5. 增加“对正确 candidate 做 AST 定向变异”的 mutation tests；只跑正常 parser 输出无法证明门禁真的能抓到漂移。

## 9. BLOCK-11-06：COLUMN_FORMAT / ENGINE_ATTRIBUTE 的“已恢复”结论与代码相反

### 9.1 独立结果

以下正例在 Rev.L 规划层被接受，但掩码 PRIMARY/UNIQUE COMMENT 后，候选 sqlglot 仍 ParseError，最终不能恢复：

| 正例 | 29.0.0 | 30.14.0 | 30.17.0 |
|---|---|---|---|
| `COLUMN_FORMAT FIXED` | ParseError | ParseError | ParseError |
| `COLUMN_FORMAT DYNAMIC` | ParseError | ParseError | ParseError |
| `COLUMN_FORMAT DEFAULT` | ParseError | ParseError | ParseError |
| `ENGINE_ATTRIBUTE='{}'` | ParseError | ParseError | ParseError |

因此 §5.25.2、A-141 中“恢复”的证据不可由文档代码复现。

### 9.2 官方画像另有两处错误

1. 腾讯官方建表页的列级 `COLUMN_FORMAT` 是 FIXED/DYNAMIC/DEFAULT 三值；Rev.L `_COLUMN_FORMAT_ENUM` 额外加入 `COMPRESSED`，它来自表级 ROW_FORMAT，不应混入列级枚举；
2. Rev.L 注释称列级 `STORAGE` 为“建表页明示”，但当前腾讯建表页列级清单并未列出。按本文自己的 provenance 原则，未取得目标实例或对应版本官方证据前应归 `unsupported_unproven`，不能标作 official positive。

### 9.3 必须修改的机制

二选一并写清产品代价：

- **推荐**：把已精确验证但 sqlglot 不认识的 `COLUMN_FORMAT` / `ENGINE_ATTRIBUTE` 作为辅助 span；只在已有 PRIMARY/UNIQUE COMMENT 或 TDSQL 方言主目标时掩码，记录完整 SourceFingerprint，raw_sql 不变，且确认当前规则没有消费者依赖这些属性；
- **保守**：继续失败关闭，但逐项登记 KFN、统计生产/语料出现频度并取得用户批准，不再写“已恢复”。

无论选择哪条，`COMPRESSED` 必须从列级枚举删除；`STORAGE` 必须补目标实例证据或改为未证实能力。

## 10. BLOCK-11-07：测试“唯一真源”尚未形成，正文硬断言仍互相冲突

### 10.1 直接矛盾

| 位置 | Rev.L 表述 | 实际/另一处表述 |
|---|---|---|
| Z2 | `BROADCAST COMMENT='x'` 为 pos，必须 plan 成功 | 当前迁移表把 BROADCAST 置终态，实测 plan=False |
| §7.1a | H 组由真实参数化清单生成 | 仓库不存在 `tests/test_parser_index_type_and_uk_comment.py`，也不存在 `h_cases.py` |
| §7.1 总计式 | 列 A..H 组 | Rev.L 新增 P14，但公式未列 P 组 |
| G-24 | H4 写 2 例 | §7.1a H4 写 6 例 |
| G-25 | H6 写 12 例 | §7.1a H6 写 15 例 |
| K-10 | PRIMARY COMMENT 仍“失败关闭 KFN-2” | Rev.L 已撤销 KFN-2并要求恢复 |
| 文档头 | 继续硬编码 1355/29 | Rev.K 又声明不硬编码环境 passed/skipped |
| §3.4 | 引用 `_consume_partition_clause()` 和 +403 行 | 最终代码块函数名、规模均已变化 |

### 10.2 风险

开发者会面临互斥要求：按 Z2 改会破坏当前终态设计，不改则无法通过文档门槛；按 K-10 改会撤销 DEF-3。当前“1355 passed”等自验证不能在仓库复现，因为完整 case manifest 和实现 harness 都不在仓库。

### 10.3 必须修改的机制

1. 在设计阶段就提交唯一 case manifest（JSON/YAML/Python 参数表均可），每条包含稳定 ID、SQL、分类、profile/provenance、预期 plan/candidate/规则集合；
2. 文档表格和计数由该 manifest 生成，不再人工重复维护；
3. 明确 `BROADCAST COMMENT` 到底是撤销的旧假设还是项目契约，二选一后统一代码、Z2、历史修订说明；
4. K-10 按 Rev.L 改成 PRIMARY 掩码；H4/H6/P 组和总计全部从 manifest 生成；
5. 把本报告 §4~§9 的反例全部加入 manifest；
6. 开发准出必须提供实际 `pytest --collect-only -q` 输出和零 skip，而非仅写设计者本机结论。

## 11. MAJOR-11-01：FULLTEXT/SPATIAL 裸形态存在入口死分支

`_consume_index_definition()` 可以识别裸 `FULLTEXT` / `SPATIAL`，但 `_is_index_item()` 只有在下一 token 是 KEY/INDEX 时才把它分发给索引消费器。结果 `FULLTEXT (id)` 被错误送进列定义消费器并 plan=False。

这与本文 §2.1 自己的 18 种 AST 枚举（明确包含 `FULLTEXT (a)`）冲突，也与 MySQL/TDSQL 索引语法中 KEY/INDEX 可省略的形态不一致。

整改：让 `_is_index_item()` 与 `_consume_index_definition()` 使用同一 lead 判据；增加裸 FULLTEXT/SPATIAL、带 KEY、带 INDEX、带名、缺括号，以及反引号列名为 `` `fulltext` `` 的反向鉴别，避免把合法列定义误当索引。

## 12. MAJOR-11-02：不同 TDSQL 分区代际仍需形成显式 capability profile

Rev.L 已把 `TDSQL_PARTITION BY` 登记为未确认能力，这是诚实的；问题在于代码仍用一张 `_TAIL_EDGES` 同时承载 OLD_OFFICIAL、TARGET_INSTANCE、PROJECT_ACCEPTED、ADJ-6，且没有 profile 字段参与决策。

整改不要求推翻用户确认的 `TDSQL_DISTRIBUTED BY HASH`。建议建立至少三个具名 profile，并让每个允许序列有唯一 provenance：

| profile | 允许的核心形态 |
|---|---|
| `TARGET_CURRENT` | 用户确认的 `TDSQL_DISTRIBUTED BY HASH`、精确广播哨兵及目标实例实证组合 |
| `LEGACY_PARTITION` | `shardkey + PARTITION BY`、`PARTITION BY + TDSQL_DISTRIBUTED RANGE/LIST` 等官方旧形态 |
| `NEW_SECONDARY` | `TDSQL_DISTRIBUTED BY HASH + TDSQL_PARTITION BY RANGE/LIST` |

如果 parser 调用点暂时拿不到实例版本，可以安全接受这些 profile 的**无冲突并集**，但每条 SQL 必须完整匹配其中一个 profile，禁止从多个 profile 各取一段拼接。

## 13. MINOR

### MINOR-11-01：Rev.L 总览仍引用 Rev.K 旧证据

文档顶端实测总结、§5.25.4、K-10 等仍写 PRIMARY COMMENT 为 KFN-2；会误导开发与测试。应把历史段落明确标成“Rev.K 历史，仅供变更说明”，当前准出门槛只保留 Rev.L 最终态。

### MINOR-11-02：设计代码规模与汇总项失真

§3.4 的函数名、插入行数和“+403 行”已与最终代码块不一致。施工如果依赖这些锚点，容易漏贴或重复定义。应从最终补丁自动生成 diff stat、函数清单与唯一性检查。

## 14. 对 Rev.L 新增 DEF-3 的单独结论

把 PRIMARY COMMENT 与 UNIQUE COMMENT 合并到同一主目标机制，方向正确；最小基准：

```sql
CREATE TABLE t (id INT, PRIMARY KEY(id) COMMENT 'pk')
```

在三版 sqlglot 上均能得到 `plan=True → Create → guard=True`。P2 中 PRIMARY 后带名、空键列、非字符串 COMMENT、重复 COMMENT、USING HASH、重复 USING 的拒绝逻辑也没有被本轮反例推翻。

但是 DEF-3 扩大了进入恢复链的语句范围，所以它把 §4 的可执行注释盲区、§5/§6 的表尾冲突、§7 的类型误判全部变成了新增可达路径。必须先关闭这些 BLOCK，不能只凭 P 组 14 个邻近用例判定爆炸半径为零。

## 15. 建议 A 一次性提交的 Rev.M 闭环包

为了减少“每轮只发现两三条”的修改频度，下一版不要再逐反例补 if，按以下四个结构面一次性收敛：

1. **输入面**：解析并验证 MySQL 可执行注释；普通注释、字符串、标识符继续保持不可见；
2. **表尾面**：typed atoms + acyclic capability profiles；一级分布和二级分区有独立计数；广播哨兵单独成型；
3. **定义面**：结构化 TypeSpec + canonical aliases + 参数边界 + 结构化 SourceFingerprint；列约束和索引语义真正比较；
4. **证据面**：一个可执行 case manifest 生成所有计数和文档表，纳入本报告全部反例。

Rev.M 应同时提供：

- 最终可执行代码块；
- case manifest 与生成后的清单；
- 29.0.0 / 30.14.0 / 30.17.0 三版结果；
- 两份生产 fixture 精确规则集合；
- 生产 14 表与 197 条语料漂移明细；
- 专项、全量、collect-only 原始输出；
- 对仍不能支持的官方形态给出 KFN、频度、用户批准，不得隐藏在“失败关闭”里。

## 16. 开发准入门槛

以下条件全部满足后，方可从设计阶段进入开发：

- [ ] BLOCK-11-01：所有 MySQL 可执行注释被识别；只允许完整验证的具名 payload；
- [ ] BLOCK-11-02：表尾无回环；一级分布、二级分区各至多一个；
- [ ] BLOCK-11-03：广播哨兵精确分型，括号/混合/再分区失败关闭；ADJ-6 精确 guard；
- [ ] BLOCK-11-04：官方类型清单、别名、参数范围、ENUM/SET、类型属性矩阵闭合；
- [ ] BLOCK-11-05：列约束与索引结构进入候选 AST 守恒比较；mutation tests 能抓到定向漂移；
- [ ] BLOCK-11-06：COLUMN_FORMAT/ENGINE_ATTRIBUTE 真实可恢复，或经用户批准登记 KFN；官方枚举纠正；
- [ ] BLOCK-11-07：case manifest 成为唯一真源，所有冲突与陈旧门槛清理；
- [ ] MAJOR-11-01：FULLTEXT/SPATIAL 裸形态入口与消费器一致；
- [ ] MAJOR-11-02：每条表尾序列完整匹配一个 capability profile，不跨代际拼接；
- [ ] 用户冻结决策无回归：目标 HASH、广播哨兵、sqlglot tokenizer、SPATIAL=NORMAL、KFN-1、ADJ-6、NG-10；
- [ ] 设计代码三版本矩阵：所有 `pos` 恢复、所有 `neg` 规划层拒绝、所有已批准 KFN 稳定失败关闭；
- [ ] 两份生产 fixture 规则集合精确相等；全量回归 0 failed；新增专项 0 skip。

## 17. 本轮测试记录

### 17.1 已执行

```text
python -m pytest -q \
  tests/test_parser_tdsql_dialect_fallback.py \
  tests/test_r077_r054_tdsql_syntax.py \
  tests/test_r061_index_name_quoting.py

71 passed, 3 warnings
```

另执行：

- 从 Rev.L 抽取代码块，在 sqlglot 29.0.0 / 30.14.0 / 30.17.0 上运行 29 例开发准入矩阵；
- 执行 4 例可执行注释、7 例广播/ADJ-6 边界、4 例表尾循环、官方数据类型/别名/边界矩阵；
- 对 `_validate_recovery_candidate()` 执行 3 例 AST 定向变异反向鉴别；
- 重新读取腾讯 TDSQL 四份官方页面与 MySQL 5.7 类型边界页面。

### 17.2 关于当前全量回归

当前仓库尚未施工 Rev.L，故全量回归只能证明 `main` 的 v1.6.2.1 基线没有被本次文档提交破坏，不能拿它替代设计代码矩阵。本次实际结果：

```text
python -m pytest -q
1384 passed, 10 warnings in 264.35s
```

## 18. 最终评审意见

**Rev.L 暂不通过开发准入。**

这不是否定 A 的修订方向，也不要求重新推翻用户已经拍板的事项。需要做的是把当前“大而松的手写 CREATE TABLE 语法器”收敛成四个可证明的结构：可执行注释显式验证、表尾 profile 无环、数据类型与指纹结构化、测试清单单一真源。

上述 7 个 BLOCK 中，前三个直接关系 R054/R077 及 TDSQL 分布语义，后四个关系恢复链是否会吞掉真实错误或继续误报，均不能留到开发后再补。按 §15 一次性整改后，下一轮可直接按 manifest 做最终准入复核，不必再重走前十轮的逐例审查路径。
