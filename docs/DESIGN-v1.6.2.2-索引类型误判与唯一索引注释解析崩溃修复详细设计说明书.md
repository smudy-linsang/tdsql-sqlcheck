# DESIGN-v1.6.2.2 索引类型误判与唯一索引注释解析崩溃 修复详细设计说明书

| 项目 | 内容 |
|---|---|
| 文档版本 | **Rev.M**（针对 O 第十一轮"开发准入"独立复审：7 BLOCK + 2 MAJOR + 2 MINOR **全部认可并整改**。按四个结构面一次性收敛：输入面 / 表尾面 / 定义面 / 证据面。见 Rev.M 修订说明） |
| 目标版本 | **v1.6.2.2** |
| 缺陷来源 | 内网人工扫描报告 #6309（gg77）、#6311（gg78） |
| 缺陷编号 | **DEF-1 = DEF-R054-FAKEUNIQUE**；**DEF-2 = DEF-PARSE-UKCOMMENT** |
| 撰写 | 智能体 A |
| 施工 | 智能体 Q |
| 基线 commit | `03216b7`（main） |
| 评审依据 | `docs/REVIEW-v1.6.2.2-...独立复审报告-Codex.md` |
| 改动范围 | **`parser_legacy.py` 5 个改动点**（含**删除 v1.6.2.0 的 `_TDSQL_DIALECT_RE` 全局正则**）+ **`requirements.txt` / `pyproject.toml` 各 1 行依赖 pin**；fixture 已在 Rev.C 修正 |
| 实测结论 | 生产 14 表 + 全语料共 **201 条语句逐键零漂移**（两个目标 fixture 单列、按预期变化）；全量回归 **0 failed**（本环境 1355 passed / 29 skipped，**仅供参考、不作门槛**）；唯一 case manifest **410 例 + 28 条变异断言 + 6000 条模糊，在 sqlglot 29.0.0 / 30.14.0 / 30.17.0 三版上全绿**；生产回放**精确集合相等**；从本说明书代码块重建的 `parser_legacy.py` 与提交文件**逐字节相同** |

---

## 🚨 首要事项：本缺陷**已在当前生产版本 v1.6.2.1 上活跃**

O 第三轮指出的 BLOCK-C1，我复现后发现它**不是 Rev.C 引入的**——
它是 **v1.6.2.0 引入、目前正在内网运行的 `_TDSQL_DIALECT_RE` 全局正则**的缺陷。
在**当前已部署的 v1.6.2.1** 上直接实测（不打任何补丁）：

| 输入（分片表，尾子句 `TDSQL_DISTRIBUTED BY HASH(sk)`） | 当前生产版本的实际解析结果 |
|---|---|
| 有一列名为 `` `broadcast` `` | 列名变成 **`' '`（空白）——该列被吃掉** |
| 某列注释为 `'broadcast table info'` | 注释被改成 **`'  table info'`** |
| 某列注释为 `'TDSQL_DISTRIBUTED BY HASH(fake)'` | 注释被清空成 **`' '`** |

三种情况**解析都"成功"**，产出的是**结构已被破坏的 AST**，下游 119 条规则基于错误结构继续审核，
不会报 E999，也没有任何告警。这比显式报错更危险。

**因此本次修复的性质变了**：不再只是"修两个误报"，而是**同时修掉一个正在生产环境静默破坏
审核数据的缺陷**。

> 🔒 **用户决策（2026-08-25）**：**不单独出热修、不单独知会内网**，一并随 v1.6.2.2 解决。
> 依据：内网目前"用关键字作列名"的情形还不多，暴露面有限。
> 本条已决，后续评审与施工**不必再把它作为独立待办重新提出**——
> 只需确保 v1.6.2.2 把它修好（门槛 G-15、X 组 40 例）。

---

## Rev.M 修订说明（针对 O 第十一轮"开发准入"独立复审：7 BLOCK + 2 MAJOR + 2 MINOR）

> **本轮我方结论：11 条全部复现、全部认可，无异议条目。**
> 按 O §15 的要求，本版不再"逐反例补 if"，而是按**四个结构面**一次性收敛：
> **输入面**（可执行注释）、**表尾面**（typed atoms + 无环 capability profile）、
> **定义面**（结构化 TypeSpec + 结构化 SourceFingerprint）、**证据面**（可执行 case manifest 成为唯一真源）。

| 编号 | O 的结论 | 我方复现 | Rev.M 处置 |
|---|---|---|---|
| **BLOCK-11-01** | MySQL 可执行注释 `/*!50100 …*/` 完全绕过整句验证 | ✅ 复现：payload 落在 `token.comments`，规划器完全看不见 | 新增 `_collect_executable_comments()` / `_validate_executable_comments()`：**至多一个**可执行注释；payload 必须重新词法化，首 token 必须是 `PARTITION BY`，且必须被 `_consume_secondary_partition()` **完整消费到末尾**；任一条不满足 → 整句失败关闭。普通 `/* */`、`--`、`#` 注释仍保持不可见 |
| **BLOCK-11-02** | 表尾迁移图存在回环，一级分布互斥未实现 | ✅ 复现：`DIST → PARTITION → DIST` 被接受 | 表尾改为 **typed atoms + 无环 profile 白名单**：`_scan_table_tail()` 产出原子序列，`_match_tail_profile()` 要求整条序列**完整命中**一个具名 profile；一级分布、二级分区各自**独立计数、至多一个**，禁止跨代际拼接 |
| **BLOCK-11-03** | 广播哨兵与普通 shardkey 混型，R054/R077 边界可伪造 | ✅ 复现：`shardkey=(noshardkey_allset,id)` 被接受 | 广播哨兵单独成 `BROADCAST_SENTINEL` 原子并置为**终态**：只接受裸形态，括号形态、与普通分片键混列、其后再接二级分区，一律失败关闭 |
| **BLOCK-11-04** | 数据类型规范表双向失真 | ✅ 复现：`INTEGER`/`NUMERIC`/`REAL`/`DOUBLE PRECISION`/`ENUM`/`ZEROFILL`/`CHAR(0)` 被误拒；`DECIMAL(1,2)`/`BIT(65)`/`CHAR(256)`/`YEAR(999)`/裸 `ENUM` 被误收 | `_TYPE_SPEC` 模式字符串升级为**结构化规则表** `_TYPE_RULES`：每型显式声明 `canonical / arity / 参数区间 / 族`；别名在**源侧**即规范化，源侧与候选侧**共用同一个 `_consume_data_type()`**；类型属性按**族**开放；`DOUBLE PRECISION` 同时适配单 token 与双 token 词法表现。**TY 组 108 例双向闭合矩阵**：官方合法 78 例零回归、越界非法 30 例零误放行 |
| **BLOCK-11-05** | SourceFingerprint 只是"丰富字符串"，门禁没有守恒 | ✅ 复现：丢 `NOT NULL DEFAULT 7`、`UNIQUE→KEY`、`UNIQUE→PRIMARY`，Rev.L 门禁**全部返回 True** | 门禁改为**逐字段结构比较**：列名 / 规范类型 / 列约束集合、索引 kind / 索引名 / 键列与前缀长度 / `USING`、定义项**顺序与个数**、表名、二级分区节点数。被忽略的差异逐条具名列出。新增 **M 组 28 条变异断言**做反向鉴别 |
| **BLOCK-11-06** | `COLUMN_FORMAT` / `ENGINE_ATTRIBUTE` 的"已恢复"结论与代码相反 | ✅ 复现，**且是我方错误**：Rev.K 只在规划层验证就写了"恢复 ✅"，端到端仍 E999 | 采纳 O 的**推荐方案**：两者作**辅助掩码 span**，仅在已有主目标时掩码，`raw_sql` 不变，且已确认现有 119 条规则无消费者依赖这两个属性。同时按 §9.2 更正官方画像：`COMPRESSED` 从列级枚举**删除**；列级 `STORAGE` 改判 `unsupported_unproven` |
| **BLOCK-11-07** | 测试"唯一真源"未形成，正文硬断言互相冲突 | ✅ 复现，逐条核对 8 处矛盾 | **新增 `tests/parser_recovery_manifest.py` 作为唯一 case manifest**（410 例 + 28 条变异断言 + 6000 条模糊），§7.1/§7.1a/§7.1b/§7.1c/§7.1d 与全部计数由 `tests/manifest_doc.py` **从 manifest 生成**；8 处矛盾逐条裁定（见下表） |
| **MAJOR-11-01** | `FULLTEXT`/`SPATIAL` 裸形态入口死分支 | ✅ 复现：`FULLTEXT (a)` 被送进列定义消费器 | `_is_index_item()` 与 `_consume_index_definition()` 统一到同一个 `_index_lead()` 判据；新增 R11-M1 组 9 例（含 `` `fulltext` `` / `` `spatial` `` 反引号列名的反向鉴别） |
| **MAJOR-11-02** | 分区代际未形成显式 capability profile | ✅ 认可 | 建立三个具名 profile，每条允许序列有唯一 provenance：`TARGET_CURRENT`（7 条）与 `LEGACY_PARTITION`（3 条）**放行**，parser 调用点拿不到实例版本，故接受这两者的**无冲突并集**，但**每条 SQL 必须完整匹配其中一条序列**，禁止跨 profile 拼接；`NEW_SECONDARY`（腾讯新版 `TDSQL_PARTITION BY`）**具名登记于 `_TAIL_PROFILES_UNPROVEN` 但成员不放行**——无目标实例证据、语料 0 例，按本方案自己的 provenance 原则归 `unsupported_unproven`（manifest `R11-02-05/06`）。取证后只需把条目搬进 `_TAIL_PROFILES`，判定逻辑一行不改 |
| **MINOR-11-01** | 总览仍引用 Rev.K 旧证据（PRIMARY COMMENT 写作 KFN-2） | ✅ 复现 | 全文历史段落统一加注 **「Rev.K 历史，仅供变更说明；当前准出门槛见 §7.3」**；K-10 按 Rev.L 改为 PRIMARY 掩码 |
| **MINOR-11-02** | §3.4 函数名、插入行数、"+403 行"已失真 | ✅ 复现 | §3.4 规模表、函数清单、唯一性检查改由 `tests/codestat.py` **从最终补丁自动生成** |

### Rev.M 对 §10.1 八处矛盾的逐条裁定

| 位置 | 裁定 | 依据 |
|---|---|---|
| Z2 `BROADCAST COMMENT='x'` | **判为 `unsupported_unproven`（失败关闭）**，撤销 Rev.L 正文的 `pos` 表述 | `BROADCAST` 是终态原子；该形态在 197 条语料与生产 14 表中出现 **0 次**，无 TDSQL 官方证据。代码、manifest（`Z-15`）、正文三者现已同源 |
| §7.1a H 组来源 | manifest 随设计一并提交为 `tests/parser_recovery_manifest.py`，不再引用不存在的文件 | BLOCK-11-07 第 1 条 |
| §7.1 总计式 | **删除人工总计式**，全部计数由 `manifest_doc.py` 生成 | BLOCK-11-07 第 2 条 |
| G-24 / G-25（H4=2、H6=12） | **删除这两条硬编码**，H 各子组例数由 manifest 生成（现为 H4=6、H6=15） | 同上 |
| K-10 PRIMARY COMMENT | **按 Rev.L 改为掩码目标**，KFN-2 已撤销为 DEF-3 | 用户确认"内网实际有这种表" |
| 文档头 1355/29 | 改为「本环境实测 1355 passed / 29 skipped；**门槛是 0 failed，不同环境分布不同，不得硬编码**」 | Rev.K 已声明不硬编码 |
| §3.4 `_consume_partition_clause()` / +403 行 | **由 `codestat.py` 自动生成**，函数名与行数以最终补丁为准 | MINOR-11-02 |
| §7.1a 与 G-24/G-25 的分歧 | 唯一真源为 manifest；本节以下所有数字均为生成结果 | BLOCK-11-07 |

### Rev.M 新增的已知假阴性：KFN-3（sqlglot 固有类型边界）

以下 8 种 **TDSQL 官方合法**的列类型，在 **29.0.0 / 30.14.0 / 30.17.0 三版 sqlglot 上一致 ParseError**，
本次修复既不能改善也未曾恶化——**去掉索引 COMMENT 的普通建表在修复前后都报 E999，逐条实测行为完全一致**：

```text
CHAR(n) BINARY   POINT   LINESTRING   POLYGON
MULTIPOINT   MULTILINESTRING   MULTIPOLYGON   GEOMETRYCOLLECTION
```

- **登记类别**：KFN-A（官方合法、暂不支持），manifest 中为 `TY-K-01 … TY-K-08`，分类 `pos_known`；
- **产品代价**：这些类型的建表语句继续报 E999，与当前生产版本表现**完全相同**，不构成回归；
- **出现频度**：197 条语料与生产 14 表中出现 **0 次**；
- **消除条件**：sqlglot 上游支持后自动消除，无需改本方案代码——`_TYPE_RULES` 已按官方八种空间类型登记完整。

> 按 O 的要求单独登记并提请用户知悉：**这是对既有能力边界的如实登记，不是本次修改引入的新限制。**

---

## Rev.L 修订说明（用户确认：目标实例存在 `PRIMARY KEY … COMMENT` 形态）
> ⚠️ **本节为 Rev.%s 历史，仅供变更说明**；其中的分类、门槛、数字**均可能已被后续修订取代**。当前准出门槛只看 §7.3，当前用例与计数只看 §7.1 由 manifest 生成的表。

**这一版不是复审驱动，是用户提供了新的目标实例事实。**

Rev.K 把 `PRIMARY KEY (col) COMMENT '…'` 登记为 **KFN-2**（TDSQL 官方合法、
sqlglot 30.x 解析不了、语料出现 0 次，故失败关闭并留待确认）。
用户确认 **内网实际存在这种表**，因此该形态从"已知假阴性"转为**必须修复**，
KFN-2 登记随之撤销。

### DEF-3：PRIMARY 索引 COMMENT 导致解析崩溃

与 DEF-2 **同一缺陷类、同一修复机制**，只是索引 kind 不同：

| | 主干 v1.6.2.1 / Rev.K | Rev.L |
|---|---|---|
| `PRIMARY KEY (id) COMMENT '主键索引'` 的表 | `E999_SYNTAX_ERROR` + **R003 / R004 / R005 / R028 四条连带误报** | 正常解析，只剩正确结论 |
| 解析产物 | `ast=None`、`cols=0`、`has_primary_key=False` | `ast=Create`、`cols=4`、`has_primary_key=True` |

实测一张典型内网形态的表（4 列 + `PRIMARY KEY (id) COMMENT`）：

```text
主干 / Rev.K ：['E999_SYNTAX_ERROR', 'R003', 'R004', 'R005', 'R028']
Rev.L        ：['R037']
```

**这与 gg78 的误报形态完全一致**——`has_primary_key=False` 触发 R003/R004，
列信息全丢触发 R005/R028。

### 改动

`_consume_index_definition()` 的索引 COMMENT 分流由两支改为三支：

| 索引 kind + COMMENT | sqlglot 30.x 实测 | Rev.K | Rev.L |
|---|---|---|---|
| `UNIQUE KEY u (a) COMMENT` | ParseError | 主目标，记 span | 主目标，记 span |
| **`PRIMARY KEY (a) COMMENT`** | **ParseError** | **失败关闭（KFN-2）** | **主目标，记 span** ✅ |
| `KEY` / `INDEX` / `FULLTEXT` … `COMMENT` | 可解析 | 原样保留 | 原样保留 |

改动只有这一处判断，**不新增机制**：掩码、span 门禁、结构指纹守恒全部沿用 DEF-2 的既有链路。
实测掩码后 `PRIMARY KEY (a)`、`PRIMARY KEY (a,b)`、`PRIMARY KEY (a) USING BTREE`
以及 **PRIMARY 与 UNIQUE 双注释共存** 四种形态均可解析。

### 爆炸半径

| 检查项 | 结果 |
|---|---|
| 全语料 197 条 | **恰好 2 条**变化，与 Rev.K **逐键完全一致**（语料中无 PRIMARY COMMENT 表） |
| 生产 14 表 | **零漂移** |
| 两份生产 fixture | 规则集合**精确相等** |
| 第十轮全部反例 | `PRIMARY KEY pk(id)`（PRIMARY 后带名）等**仍全部失败关闭** |
| 全量回归 | 0 failed |

新增 **P 组 14 例**（8 正例 + 6 非法近邻），在 sqlglot **29.0.0 / 30.14.0 / 30.17.0** 三版全通过。

> ⚠️ 需要留意的一点：本改动**扩大了进入恢复链的语句范围**——带 `PRIMARY … COMMENT`
> 的表此前一律停在 E999，现在会走完整条恢复链。所有安全性质（S-1~S-4、S-2c）
> 与门禁对它一视同仁，P2 的 6 例非法近邻即为此设的边界证明。

---

## Rev.K 修订说明（针对 O 第十轮深度独立复审）
> ⚠️ **本节为 Rev.%s 历史，仅供变更说明**；其中的分类、门槛、数字**均可能已被后续修订取代**。当前准出门槛只看 §7.3，当前用例与计数只看 §7.1 由 manifest 生成的表。

O 对 Rev.J 判定 **No-Go**，开出 **5 项 BLOCK（J1~J5）+ 2 项 MAJOR（J1~J2）**。
**我逐条独立复现，全部成立，全部接受**，无异议。

他还做了一件对本项目实际帮助很大的事：**把我取不到的 TDSQL 官方文档做成了离线摘要**
（建表页 / 二级分区页 / 兼容性页 / DTS 同步页）。Rev.J §5.23.4 记录的
"`cloud.tencent.com` 被出口代理拦截、无法独立抓取官方 `Local_table_option` 清单"
这一取证缺口，本版据此**补齐并更正**。

### 五项 BLOCK

| 编号 | O 的意见 | 我的复核 | Rev.K 处置 |
|---|---|---|---|
| **J1** | 列定义与 `DEFAULT` 仍是无类型上下文的通用消费器 | ✅ 双向复现。**放行**：`id RANGE` / `id NULL` / `VARCHAR(1,2,3)` / `INT(1,2)` / `DATE(1)` / `DECIMAL(10,2,1)` / `JSON(1)` / `DEFAULT foo` / `DEFAULT ()` / `DEFAULT (,)` / `DEFAULT (SELECT 1)`；**误拒**：官方合法的 `DECIMAL(10,0)` / `DATETIME(0)` / `TIME(0)` / `DEFAULT -1` / `DEFAULT +1` / `COLUMN_FORMAT` / `ENGINE_ATTRIBUTE` | 建立**数据类型规范表** `_TYPE_SPEC`（参数模式 NONE/M/M_OPT/M_D/FSP/ENUM_SET），类型名走**显式白名单**；**scale 与 fsp 允许 0**，不再复用索引前缀的"正整数"谓词；`_consume_default_value()` 按官方字面量域建模（含带符号数值、hex、bit、布尔、NULL、时间函数）；实现官方 `COLUMN_FORMAT` / `ENGINE_ATTRIBUTE` / 列级 `STORAGE` |
| **J2** | `SourceFingerprint` 只是"生成了"，没有"守恒" | ✅ `id JSON(1)` 原文指纹是 `JSON(1)`、候选静默变 `JSON`，门禁只看"有类型 + 列名"仍返回 True | 门禁把**规范类型形态**纳入逐项比较（`_ast_definition_fingerprints()` 从候选 AST 取 `kind.sql()` 归一后比对） |
| **J3** | 表尾状态机没有按声明执行；分号策略把合法单语句也拒绝 | ✅ 全部复现：`shardkey=id ENGINE=InnoDB`（shardkey 走表选项分支、**根本不推进 phase**）、`BROADCAST PARTITION BY`、`PARTITION BY … BROADCAST` 均被接纳；**合法单条 DDL 的终止分号反被拒绝** | 改为**显式迁移表** `_TAIL_EDGES`，每条边带 provenance；没有证据的边默认不存在；`_strip_terminal_semicolon()` 允许 **0 或 1 个且仅位于 EOF 前**的终止分号 |
| **J4** | 官方白名单不完整，并混合了不同产品代际 | ✅ **这是我的取证错误**：官方建表页明示 `ROW_FORMAT` 与 `STATS_PERSISTENT` 属 local_table_option，Rev.J 却把它们判成 `unsupported_unproven` | 按官方清单补回并给出严格值域（`ROW_FORMAT` 六值枚举、`STATS_*` 为 `DEFAULT/0/1`）；`CHECKSUM` 等无证据项继续失败关闭；代际差异按 provenance 分别标注 |
| **J5** | 分区函数、值和 option 仍未按 TDSQL 上下文闭合 | ✅ 官方二级分区页只明示 year/month/day，Rev.J 另放行 4 个未举证函数；`VALUES IN (-'x')` 被恢复（符号可修饰字符串）；官方 `STORAGE ENGINE` 被拒、反序 `COMMENT … ENGINE` 反被接受 | 函数白名单收为 **YEAR/MONTH/DAY** 且参数必须**恰好一个列标识符**；符号只进入数值分支；`_consume_partition_options()` 按官方序列 `[STORAGE] ENGINE → COMMENT` 建小状态机，各至多一次且不得反序 |

### 两项 MAJOR

| 编号 | 我的复核 | Rev.K 处置 |
|---|---|---|
| **MAJOR-J1** | ✅ 属实。§7.1 H 组明细相加为 **109**、总计式写 **90**、H6 两处口径不一；文档还引用了仓库里并不存在的 `h_cases.py` | §7.1 的 H 组表改为**由实际参数化清单生成**（见 §7.1a），逐条 case 带稳定 ID、分类与规范依据；准出门槛不再硬编码我本地环境的 `1355 passed / 29 skipped`，改为"**原有全部用例全通过 + `pytest --collect-only` 实际收集数全通过**" |
| **MAJOR-J2** | ✅ `PRIMARY KEY pk(id)` 被接纳（PRIMARY 后不应有索引名）；前置与后置 `USING` 各自新建 seen 集合 | `_consume_index_definition()` 按 kind 分支；**PRIMARY 之后不消费索引名**；前后置 `USING` **共用同一个 seen**；`PRIMARY COMMENT` 登记为 **KFN-2** |

### 我在这一轮自己引入并当场发现的回归

改完索引分支后，我一度把**所有非 UNIQUE 索引的 COMMENT 都判成失败关闭**，
结果**生产 fixture gg78 直接回归**（`精确相等 ❌`）——因为它含真实的
`KEY idx_term_bizlog (…) COMMENT '终端查询索引：…'`。

实测 sqlglot 30.x 的真实能力后按 kind 分流才是对的：

| 索引类型 + COMMENT | sqlglot 30.x | Rev.K |
|---|---|---|
| `UNIQUE KEY u (a) COMMENT` | **ParseError** | **本次 DEF-2 主目标**，记 span 掩码 |
| `PRIMARY KEY (a) COMMENT` | **ParseError** | 失败关闭，登记 **KFN-2** |
| `KEY k (a) COMMENT` / `INDEX` / `FULLTEXT` | 可解析 | **原样保留、不掩码**（生产 gg78 即此形态） |

**教训：按 kind 分支时，每一支的处置都必须由该支的实测能力决定，不能沿用相邻分支的结论。**
生产 fixture 的精确集合断言是这次唯一抓住它的东西——这条断言必须一直留在回归里。

同样地，`CONSTRAINT symbol UNIQUE (col)` 我一度改成"整句失败关闭"。
但 NG-10/ADJ-11 冻结的是"**本版不修**"，不是"整句拒绝"；且它是官方合法形态、
sqlglot 也能解析。现改为**逐 token 消费以完成整句校验，但不收集它的 COMMENT 作目标**。

---

## Rev.J 修订说明（针对 O 第九轮独立复审 + 全域穷举审计）
> ⚠️ **本节为 Rev.%s 历史，仅供变更说明**；其中的分类、门槛、数字**均可能已被后续修订取代**。当前准出门槛只看 §7.3，当前用例与计数只看 §7.1 由 manifest 生成的表。

O 对 Rev.I 判定 **No-Go**，并追加了一份**全域穷举审计报告**，把恢复链拆成 13 个决策面
逐一静态审计 + 交叉组合（二级分区 80、一级分片 60、表尾顺序 56、token 变异 20,000）。
他开出 **7 项 BLOCK（X1~X7）+ 3 项 MAJOR（X1~X3）**。

**我逐条独立复现，全部成立，全部接受。** 这一轮我没有任何异议要提。

### 为什么这轮必须重构而不是继续打补丁

O 在审计报告里点出了三个体系性原因，我认为这是九轮以来最准确的一次归因：

```text
1. 用缺陷主干当非法语法 oracle；
2. 用无上下文的通用消费者同时解析不同 TDSQL 语法域；
3. 用很弱的 AST 布尔门禁替代结构守恒。
```

前八轮我一直在"上一轮指出几条就修几条"，所以每关掉一批样例，相邻语法面又冒出新的。
Rev.J 按他给的顺序做整体重构，不再逐例打补丁。

### 七项 BLOCK

| 编号 | O 的意见 | 我的复核 | Rev.J 处置 |
|---|---|---|---|
| **X1** | 非法用例以**当前缺陷主干**作 oracle，`rank` 判据允许"主干错、候选继续错"通过 | ✅ 成立。**补充一个我实测出的细节**：现有 H 组里实际滑过判据的是 **0 条**（Rev.I 恰好处处更严），他给的反例是我**测试集里根本没有的输入**。两个问题叠加，结论一样——判据本身证明不了"0 例非法被恢复" | 期望值改为**由 TDSQL 规范推导**；主干结果降为 `baseline_observation`，只做诊断。用例分为 5 类：`pos` / `neg` / `pos_known` / `unsupported_unproven` / `characterization_user_decision` |
| **X2** | 列定义仍是未解析黑箱 | ✅ 7 例全部复现：`VARCHAR()` 静默变 `TEXT`、`DECIMAL(,2)` 变 `DECIMAL(2)`、重复 `DEFAULT`、`NULL NOT NULL` 矛盾等，主干 E999 → Rev.I `Create` | 新增 `_consume_data_type()` / `_consume_column_constraints()` / `_consume_column_definition()`：类型参数必须是**正整数**，不可重复约束用 seen 集合，`NULL`/`NOT NULL` 归一为同一 identity 互斥 |
| **X3** | 没有"主修复目标"也能启动恢复 | ✅ 成立，且**这是我在 Rev.I 引入的范围扩张**：只要存在 ASC/DESC 或 partition option 掩码就会恢复，等于悄悄新增了"所有 ASC/DESC 与 partition option 的自动修复" | 规划器返回 `primary_spans` / `auxiliary_spans` 两组；**入口条件是 `primary_spans` 非空**，辅助掩码不得单独触发恢复 |
| **X4** | 一级分片定义无方法上下文 | ✅ 3 例复现：`HASH(id) (s1 VALUES LESS THAN(10))`、`RANGE` 用 `IN`、`LIST` 用 `LESS THAN`，主干 `Command` → Rev.I `Create`；HASH+定义表那例连 R054/R077 一起消失 | `_consume_partition_defs(..., method, require_partition_kw)` 携带上下文；**HASH 不得挂分片定义表**；RANGE 只接 `LESS THAN`、LIST 只接 `IN`；官方一级分片定义表**禁止** `PARTITION` 前缀，二级分区**必须**有 |
| **X5** | 二级分区无结构与方法守恒；官方函数存在**代码死分支** | ✅ 全部复现。死分支根因与他判断一致：只有 `YEAR` 有专属 TokenType，`MONTH`/`DAY` 等被词法成 `VAR`，而 Rev.I **先判"是标识符就当普通列"**，永远到不了函数分支 | 分支顺序改为**先判"白名单函数 + 左括号"再判普通列**；二级分区方法收为官方的 **Range/List**；`_scan_table_tail` 增加 `seen_part`；值列表只接受数字/字符串并**支持负号**（`-1` 官方合法，Rev.I 误拒） |
| **X6** | 表尾缺少有限状态机 | ✅ 4 例复现：重复 `shardkey`、`shardkey + TDSQL_DISTRIBUTED` 并存、终结声明后再接表选项、二级分区后再接表选项 | 建立阶段模型 `LOCAL_OPTIONS → SECONDARY_PARTITION → DISTRIBUTION`；**`shardkey` 计入一级分布声明**并参与互斥；同名表选项不可重复；阶段只前进不回退 |
| **X7** | 表选项白名单偏离 TDSQL 官方清单 | ✅ 成立 | 按 provenance 重建白名单（见下）；`AUTO_INCREMENT=1.5` 因 `TokenType.NUMBER` 过宽被放行的问题一并关闭 |

### 三项 MAJOR

| 编号 | 我的复核 | Rev.J 处置 |
|---|---|---|
| **MAJOR-X1** | ✅ AST 门禁只查数量/非空/存在某个 PartitionBy，发现不了 `VARCHAR()`→`TEXT`、两个 `PARTITION BY` 同时保留 | 规划阶段生成 **SourceFingerprint**（表名 / 逐定义项形态 / 表尾指纹）；`_validate_recovery_candidate()` 逐字段守恒，分区要求**恰好一个** |
| **MAJOR-X2** | ✅ `id(1.5)`、`id(0)` 被当合法前缀长度；`USING BTREE` 与索引 `COMMENT` 可无限重复 | 前缀长度必须是**正整数**；索引选项用 seen 集合，`USING`/`COMMENT` 各至多一次 |
| **MAJOR-X3** | ✅ 全部属实：`_TDSQL_SHARD_METHODS` 在同一代码块**定义两次**；`want_dialect=False` 注释写"只验证不产 span"、实现却始终产 span；13 处旧剥离器名残留；4 处 `USING (BTREE|HASH)`；H 组同时存在 81/85 两套数量 | 全文机械清理；`want_dialect` 参数**整体删除**（Rev.J 的 `_scan_table_tail` 只有一种行为）；数量由逐条 case 清单唯一确定 |

### 我自己在这轮又犯的两个错

**其一，`_consume_column_constraints()` 没在顶层逗号处收尾。** 写完第一版后
**所有**列定义都被判非法、连基准用例都恢复不了。这类"新写的消费器边界条件漏了"
只能靠先跑基准用例发现——本版起，每写一个消费器就立刻用最小正例验一次。

**其二，A-61 那条旧证据是我数错的。** 第五轮我写"语料里 `BROADCAST` 末尾 0 处、
中间 8 处（`BROADCAST COMMENT='x'` 等）"，并据此**放弃了收紧**。本轮重新取证发现：
全仓 `.sql` 里**根本没有一条真实的广播表声明**，那 8 处全在**注释文本**里
（`COMMENT='系统配置表 BROADCAST'` 之类）。错误的证据让我在第五轮做了错误的设计让步。
**教训：取证脚本必须区分"token 流里的关键字"和"字符串字面量里的同名文本"** ——
这恰恰是本方案从头到尾在强调的事，我却在自己的取证脚本里犯了同一个错。

---

## Rev.I 修订说明（针对 O 第八轮独立复审）
> ⚠️ **本节为 Rev.%s 历史，仅供变更说明**；其中的分类、门槛、数字**均可能已被后续修订取代**。当前准出门槛只看 §7.3，当前用例与计数只看 §7.1 由 manifest 生成的表。

O 对 Rev.H 判定 **No-Go**，开出 3 项 BLOCK、2 项 MAJOR、2 项 MINOR。
**我逐条独立复现，7 条全部成立，全部接受**，并在复核过程中**自查出 3 条 O 未发现的同类问题**。

本轮最重要的不是又补了几个消费器，而是**判据换了**。用户与 O 在同一轮给出同一条纠正：

> 本项目是 **TDSQL** 数据库 SQL 审核。TDSQL 底层虽是 MySQL，语法却不等同；
> 最终必须遵照 **TDSQL 官方语法**。

因此 Rev.I 确立证据优先级，并写进代码注释顶部：

```text
① 目标实例真实 SHOW CREATE TABLE / 已验证生产 DDL
② 腾讯云 TDSQL MySQL 版官方语法
③ 项目已冻结的产品规则与用户决策
④ MySQL 官方语法
⑤ sqlglot 当前解析能力
```

**sqlglot 只是词法器与候选 AST 生成器，不是 TDSQL 合规性判据**：
既不能把"sqlglot 能解析"当作 TDSQL 合法，也不能把"sqlglot 解析失败"当作 TDSQL 非法。
前七轮我恰恰两头都犯过——`USING HASH` 属前者，`ASC/DESC` 属后者。

### O 的七条意见

| 编号 | O 的意见 | 我的复核 | Rev.I 处置 |
|---|---|---|---|
| **BLOCK-H1** | 恢复门禁只验证目标 UNIQUE，没有验证整条建表语句 | ✅ H1-1~H1-5 **五条全部复现**（我另加 2 条同类，共 7 条），主干 E999 → Rev.H `Create`。UNIQUE 单独恢复路径**根本不调用表尾消费者** | 新增 `_plan_recovery()` 统一规划器：定义列表逐项普查 + 表尾**始终**完整验证；新增 `_validate_recovery_candidate()` 结构保真门禁（定义项数、非空列/索引、分区保留） |
| **BLOCK-H2** | 分区消费者仍是"非空配平即通过" | ✅ `RANGE(,)` / `RANGE(+)` / `RANGE(id,)` 三条复现，主干 E999 → Rev.H `Create` | 分区表达式与分区定义按 TDSQL 官方形态精确建模：`_consume_partition_expr()` / `_consume_partition_values()` / `_consume_partition_defs()` |
| **BLOCK-H3** | `USING HASH` 与 TDSQL 官方 `index_type: USING {BTREE}` 冲突 | ✅ 官方语法核实无误；实测 Rev.H 明确批准 `USING HASH`，主干 E999 → `Create`；且 119 条规则无一否决 HASH 索引类型 | 索引选项白名单收为 `_TDSQL_INDEX_TYPES = ("BTREE",)`，`USING HASH` 失败关闭 |
| **MAJOR-H1** | 官方合法的 TDSQL 被标成"neg/产品边界"；分区顺序覆盖不全 | ✅ 官方 `key_part` 确含 `[ASC\|DESC]`；官方二级分区确含 List 与 partition `ENGINE`；官方确有 `PARTITION BY ... TDSQL_DISTRIBUTED BY ...` 顺序 | 三者**全部改为必须恢复**并已实现；测试分类新增 `pos_known`（TDSQL 合法但 sqlglot 暂不支持），与非法 neg 彻底分开统计 |
| **MAJOR-H2** | `sqlglot>=29,<31` 不是可复现构建 | ✅ 属实 | 依赖改为**精确锁定** `sqlglot==30.14.0`；并实测 29.0.0 / 30.14.0 / **30.17.0** 三版全部矩阵逐条一致，作为将来移动 pin 的依据 |
| **MINOR-H1** | §5.17.5 仍写 `PARTITION BY` 是"不透明终结子句" | ✅ 属实 | 已删除并改写为 Rev.G 历史标注 |
| **MINOR-H2** | §3.1 第⑤项、C-14 门槛区间、C-1 文件数三处旧口径 | ✅ 属实 | 已逐条更正 |

### 我自查出的三条（O 未发现）

按"TDSQL 官方语法优先"重做取证时，发现 Rev.H **会拒绝三种官方合法形态**——
方向与 BLOCK-H3 相反，属同一个根因（拿 MySQL/sqlglot 当判据）：

| 编号 | 形态 | 依据 | Rev.H | Rev.I |
|---|---|---|---|---|
| **SELF-I1** | `TDSQL_DISTRIBUTED BY range\|list (col) (s1 VALUES LESS THAN(100), ...)` | 腾讯官方建表文档原例：`tdsql_distributed by range(a) (s1 values less than(100), s2 values less than(200))` | **E999，不恢复** | `Create` ✅ |
| **SELF-I2** | `PARTITION BY LIST(o) (...) TDSQL_DISTRIBUTED BY RANGE(id)`（分区在前、分片在后） | 官方二级分区原例 `tb_sub_r_l` | **E999，不恢复**（Rev.H 强制分区子句消费到语句结束） | `Create` ✅ |
| **SELF-I3** | 多列分片键 `shardkey=(a,b)` | **项目自身代码**：`tdsql_connector.parse_shard_key_from_ddl()` 注释明写"或多列 `shardkey=(a,b)`" | **E999，不恢复**（只认单标识符） | `Create` ✅ |

> SELF-I3 尤其值得记一笔：**依据就在本仓库里**，我前七轮一次都没去查。
> 写 TDSQL 审核工具却不读项目自己已经沉淀的 TDSQL 事实，是这轮最该改的习惯。

同时，Z 组在我改完后立刻抓出一个我新引入的 bug：为支持多列 `shardkey=(a,b)`
我把"多标识符"规则误用到了 `TDSQL_DISTRIBUTED BY HASH(...)` 上。
但官方语法那里是**单列 `column_name`**，且 v1.6.1.9 冻结的 `_extract_tdsql_hash_key()`
也只提取单个分片键——已改回单列。**两处形态不同，不能共用一个消费器。**

### 结构性变化：从"多个剥离器"到"一个规划器 + 一道结构门禁"

Rev.H 的统一规划器各自决定"要不要改写"，谁也不为整条语句负责，这正是 BLOCK-H1 的根因。
Rev.I 改为：

```text
_plan_recovery(sql)                     ← 唯一入口，一次性验证整条 CREATE TABLE
  ├─ _tdsql_table_def_bounds()          定位建表头与定义列表
  ├─ _scan_definition_list()            逐个定义项普查（列类型、索引键列、索引选项）
  │    └─ _consume_index_key_parts()    TDSQL key_part：col [(len)] [ASC|DESC]
  └─ _scan_table_tail()                 表尾**始终**完整验证，直到语句结束
       ├─ _consume_table_option()       每选项专属值谓词
       ├─ _consume_partition_clause()   TDSQL 二级分区（Rev.M 已更名为 _consume_secondary_partition()）
       │    ├─ _consume_partition_expr()
       │    └─ _consume_partition_defs()  └─ _consume_partition_values()
       └─ 分片声明（恰好一个）：TDSQL_DISTRIBUTED BY … / BROADCAST
                                        ↓
         返回三类 span：uq（目标 COMMENT）/ dialect（方言）/ mask（官方语法掩码）
                                        ↓
_blank_spans() 一次性置空 → sqlglot 解析 → _spans_only_diff() 逐字符 span 门禁
                                        ↓
_validate_recovery_candidate()          ← **新增**：候选 AST 结构保真门禁
   ① exp.Create + kind==TABLE + 表名一致
   ② 候选定义项数 == 原文顶层定义项数        （防静默丢定义项）
   ③ 列必须有类型、索引必须有非空键列        （防空结构）
   ④ 原文有 PARTITION BY → 候选必须保留分区   （防静默丢分区）
```

**第三类 span 是本版的新机制**：TDSQL 官方合法、但 sqlglot 30.x 解析不了的形态
（`key_part` 的 `ASC/DESC`、分区定义里的 `ENGINE=`/`COMMENT=`），
与 UNIQUE COMMENT 用**完全相同的等长置空 + span 门禁**处理。
这样既不牺牲 TDSQL 合规性，也不引入新机制——实测五种缺口全部一次闭合。
`raw_sql` 始终保持原文（S-4），且实测 119 条规则无一消费 `ASC/DESC`，故掩码不影响任何结论。

---

## Rev.H 修订说明（针对 O 第七轮独立复审）
> ⚠️ **本节为 Rev.%s 历史，仅供变更说明**；其中的分类、门槛、数字**均可能已被后续修订取代**。当前准出门槛只看 §7.3，当前用例与计数只看 §7.1 由 manifest 生成的表。

O 对 Rev.G 判定 **No-Go**，开出 3 项 BLOCK、2 项 MAJOR、2 项 MINOR。
**我逐条独立复现，7 条全部成立，全部接受**（其中 MINOR-G1 我另有一处更正，见下）。

O 本轮先确认了第六轮的两个缺口已真实关闭（W 组 28 例在 sqlglot 30/29 双版本各 28/28），
然后指出 Rev.G 宣称的 S-2c「目标所在完整语法单元被完整消费」**仍没有真正成立**——
三段语法域还在被"配平即通过""看见起始 token 即豁免""值长得像就算数"放行。这个判断是对的。

| 编号 | O 的意见 | 我的复核 | Rev.H 处置 |
|---|---|---|---|
| **BLOCK-G1** | UNIQUE **索引选项**已完整消费，但**键值列表**仍只做括号配平 | ✅ `uk()` / `uk(,)` / `uk('id')` / `uk(123)` / `uk(lower(id))` / `uk(,id)` / `uk(id,)` **7 例全部** E999 → `Create`（主干均 E999） | 新增 `_consume_index_key_parts()`：`key_part := (VAR\|IDENTIFIER) [ "(" NUMBER ")" ] [ASC\|DESC]`，逗号只能出现在两个完整 key-part 之间，**至少一个**；函数/表达式索引失败关闭 |
| **BLOCK-G2** | `PARTITION BY` 被当作"不透明终结子句"直接 `break`，其后 token 完全不校验 | ✅ `PARTITION BY` / `PARTITION BY DEFAULT` 带 UNIQUE COMMENT 时 E999 → `Create`（主干 E999） | 新增 `_consume_partition_clause()`：**完整消费到语句结束**。缺方法、空括号、未闭合、尾随垃圾、括号体内藏第二个方言声明或分号，一律失败关闭 |
| **BLOCK-G3** | 表选项**名称**白名单化了，但**值类型**白名单过宽 | ✅ `ENGINE=123` / `ROW_FORMAT=123` / `ROW_FORMAT='x'` / `shardkey=123` 带 UNIQUE COMMENT 时 E999 → `Create` | `_consume_table_option()` 由"两个大桶"改为**每选项专属值谓词**：ENGINE→引擎名（拒 NUMBER）、ROW_FORMAT→官方枚举、SHARDKEY→单标识符、三值开关→`0\|1\|DEFAULT`、数值选项→NUMBER |
| **MAJOR-G1** | §7.1 Z1 与 G-19 仍写"仍报 E999"，未按路径拆开 | ✅ 实测：Z1 的 7 种非法参数，**带 UNIQUE COMMENT → `NoneType`+E999；不带 → `Command`、根本没有 E999**。文档确实无法同时满足 | Z1 / G-19 改为按路径分别断言最终 AST 类型 |
| **MAJOR-G2** | S-1 仍写"新逻辑只在 `except` 内"，与改动点 2b 冲突 | ✅ 属实——2b 明确改造了首次解析得到 `Command` 的路径 | S-1 改写为三条入口的精确描述 |
| **MINOR-G1** | 12 例结果在同一文档内有三种口径 | ✅ **文档确有三种口径**；但 O 给出的统一口径**本身有误**（见下） | 按实测统一为唯一口径 |
| **MINOR-G2** | §3.1 旧要求"保留 `USING` 等"与新白名单冲突；风险表两项评级已被推翻 | ✅ 属实 | 已改写；风险表两项按本轮新证据重新评级 |

### 我对 MINOR-G1 的一处更正

O 写"实际 Rev.F 的 12 条都发生最终状态变化：6 条 `Command→Create`，6 条 `E999→Create`"。
**我实测主干后确认前半句不成立**：那 6 条"无 UNIQUE COMMENT"路径在**主干上本来就是 `Create`**
（旧全局正则把方言尾子句删掉，sqlglot 宽松接纳），不是 `Command`。正确口径是：

| 路径 | 主干 v1.6.2.1 | Rev.F | Rev.G / Rev.H |
|---|---|---|---|
| 带 UNIQUE COMMENT（6 条） | `E999` | `Create`（**吞错**，即 BLOCK-F1） | `NoneType`+E999（与主干一致） |
| 无 UNIQUE COMMENT（6 条） | **`Create`**（旧正则对非法 DDL 的**假成功**） | `Create`（未变化） | **`Command`**（失败关闭，**较主干收紧**） |

所以最终状态发生变化的是 **6 条**（附录 A-66 的说法正确），不是 12 条。

同时这张表暴露出一件必须写清楚的事：**Rev.G/H 在"无 UNIQUE COMMENT"路径上是主动收紧主干的**——
主干那个 `Create` 是 `_TDSQL_DIALECT_RE` 对非法 DDL 的假成功，正是本次要删除的东西。
这一点前几版只在 X 组里体现、没有在正文说明，本版补入 §5.19.4 并给出精确例数。

### 差分判据的修正（本轮我自己的方法论问题）

我第一次写 H 组时又用了"反例必须与主干**逐字相同**"的判据，跑出 16 个红。
逐条查证后确认：其中 **14 条是判据错**——主干在"无 UNIQUE COMMENT"路径上的 `Create`
本身就是旧正则的假成功，候选降为 `Command` 是**预期收紧**，不是回归；
另 **2 条是用例归类错**——`PARTITION BY LIST (...) (PARTITION ... VALUES IN ...)`
经实测 **sqlglot 自身即 ParseError**，属产品边界，不该放进"必须恢复"的正例组。

因此 H 组改用**单调不变松**判据：

```text
rank: NoneType/E999 = 0  <  Command = 1  <  Create = 2
反例：rank(候选) <= rank(主干)，且主干的 E999 不得消失
正例：候选必须是 Create（仅限"合法 且 sqlglot 支持"的形态）
```

这条判据同时表达了 S-3（不得把非法 DDL 修成合法）与"不得收紧过头"，
且**不会被主干自身的缺陷带偏**——这正是我前两轮反复写错期望值的根因。
**从本版起，所有反例组一律使用该判据，不再手写期望值。**

### 白名单第三次扩张：从"完整语法单元"到"该单元的内部结构"

| 版本 | 白名单覆盖到哪一层 |
|---|---|
| Rev.C/D | 目标**字符**与**位置** |
| Rev.E/F | 目标**token 序列**与**参数、表名形态** |
| Rev.G | 目标**所处的语法单元**——表选项区、索引选项区被完整消费 |
| **Rev.H** | **该语法单元的内部结构**——键值列表逐 key-part、分区子句消费到语句结束、选项值逐选项定型 |

**统一契约**：所有消费器一律 `f(toks, i) -> 下一个下标 | -1`，从起点顺序消费到边界终点；
最外层 helper 只负责组合消费器与记录目标 span，不再自己做局部语法猜测。

**红线（S-2c）扩展为三条**：
① 不得配平后跳过内容；② 不得无条件 `break`；③ 不得用大类 token 代替选项专属值谓词。

---

## Rev.G 修订说明（针对 O 第六轮独立复审）
> ⚠️ **本节为 Rev.%s 历史，仅供变更说明**；其中的分类、门槛、数字**均可能已被后续修订取代**。当前准出门槛只看 §7.3，当前用例与计数只看 §7.1 由 manifest 生成的表。

O 对 Rev.F 判定 **No-Go**，开出 2 项 BLOCK、2 项 MAJOR、2 项 MINOR。**我逐条独立复现，全部成立，全部接受。**

| 编号 | O 的意见 | 我的复核 | Rev.G 处置 |
|---|---|---|---|
| **BLOCK-F1** | 方言目标"内部"合法，但**所处表选项上下文**未验证 | ✅ 目标前紧邻残缺 `DEFAULT` / `CHECKSUM` / `INDEX DIRECTORY` 时，**12 种组合全部**得 span=1、`ast=Create`、**E999 消失**（主干对照：均报 E999） | 表选项区改为**完整 atom 消费**：目标之外每个 token 都必须被 `_consume_table_option()` 消费；**不再有"跳过不认识的 token"** |
| **BLOCK-F2** | UNIQUE COMMENT 的**索引选项上下文**未验证 | ✅ `USING COMMENT 'x'` / `COMMENT 'x' USING`（缺 BTREE/HASH）→ Rev.F 得 span=1、`Create`、**E999 消失**（主干：E999） | 索引选项区同样改为**完整消费**：只接受 `USING BTREE` 与 `COMMENT STRING`，其余一律失败关闭 |
| **MAJOR-F1** | Z 组实际 22 例、文中写 21，总数连锁不一致 | ✅ 属实（Z4 是 4 例）。**顺着这条线全量核对后又查出两处同类问题**：Y 组文中写 16、实际逐条为 20（Y16 一行覆盖了 4 种形态，另有诱饵列名一例未计）；W6 原写成「`CHECKSUM=1` + `INDEX DIRECTORY='/p'`」，其中 `CHECKSUM=1` 与 W2 重复计数、`INDEX DIRECTORY='/p'` 完整形态则**根本没实测过** | 全文改为**以逐条 case 为唯一计数源**：Z 21→**22**、Y 16→**20**、W6 改为 `INDEX DIRECTORY='/p'` × 带/不带 UNIQUE COMMENT **两条路径并补测**（实测两条路径均 span=0、`ast is None`、E999 保留，与主干逐条一致），合计 156→**160**；同步统一 §7.1 / G-1 / G-5 / G-17 / G-19 / G-21 / C-11 / 附录 |
| **MAJOR-F2** | Z1 断言混淆两条恢复路径 | ✅ 属实——**我自己写 W 组用例时又踩了同一个坑**：把"无 UNIQUE COMMENT"路径也断言成"应报 E999"，实际它原本就是 `Command`（无 E999） | 所有反例断言改为**按路径分别断言最终 AST 节点类型**：带 UNIQUE COMMENT → 仍 `NoneType`(E999 保留)；不带 → 仍 `Command`（不得升级为 `Create`） |
| **MINOR-F1** | §3.2 "逐字照抄"块含**两段重复不可达代码** | ✅ 属实且严重：实测该块 `return parsed` 出现 **3 次** | 已去重（现为 1 次）；并把"代码块无重复"纳入自验证 |
| **MINOR-F2** | 旧函数名 `_tdsql_table_def_end`、§5.1 标题拼接、旧 Rev 标签、冗余边界判断 | ✅ 属实 | 已清理；冗余的 `if not (i + 5 < n + 1)` 已删除 |

### 这一轮暴露的三个我自己的问题

**其一，MINOR-F1 是我的自验证漏掉的。** 我每轮都做"抽取代码块→干净工作树施工→跑全套"，
但只校验**行为**（编译过、测试全绿）。重复的失败路径在第一个 `return parsed` 之后**永不可达**，
所以编译和测试都发现不了。**自验证从本版起增加"代码块无重复片段"检查。**

**其二，MAJOR-F2 我在写本轮 W 组用例时原样重犯了一次。** 我把"无 UNIQUE COMMENT"
的残缺上下文用例也断言成"应保留 E999"，跑出来 6 个红——一查才发现那条路径原本就是
`Command`（根本没有 E999 可保留）。这印证了 O 的判断：**反例断言必须按恢复路径分开写**，
否则要么断言错、要么为了让断言通过去改产品语义。

**其三，顺着 MAJOR-F1 全量核对计数时，又查出我自己的两处同类疏漏。** O 只指出了 Z 组，
我按"逐条 case"重数全部十二组，发现 Y 组文中写 16、实际逐条为 20，
更严重的是 **W6 那一行我写了「`INDEX DIRECTORY='/p'` 完整形态」，但从未实测过它**——
`CHECKSUM=1` 又与 W2 重复计数，两个错误恰好凑出"28"这个看起来对得上的数。
**数字对得上不等于用例存在。** 本版已把该形态补测（带/不带 UNIQUE COMMENT 两条路径，
实测均 span=0、`ast is None`、E999 保留，与主干逐条一致），合计由 156 更正为 **160**。
教训：**计数表必须由逐条实测清单反推，不能由分组小计相加**。

### 白名单从"目标片段"升级为"完整上下文"

前六轮的演进其实是同一条线在往上爬：

| 版本 | 白名单覆盖到哪一层 |
|---|---|
| Rev.C/D | 目标**字符**与**位置** |
| Rev.E/F | 目标**token 序列**与**参数、表名形态** |
| **Rev.G** | **目标所处的整个语法单元**——表选项区与索引选项区必须被完整消费 |

**关键机制变化：删掉了"跳过不认识的 token"这条路。** 之前循环里那句 `i += 1`
正是所有"上下文未验证"问题的入口；现在任何无法被白名单 atom 消费的 token 都直接失败关闭。

白名单依据是**实测**而非臆测：对仓内全部 `*.sql` 与两份生产 fixture 的表选项区做 token 统计，
实际只出现 `ENGINE= / DEFAULT CHARSET= / COLLATE= / COMMENT= / shardkey= / AUTO_INCREMENT=`
等有限组合（§5.17.1）。**合法但不在白名单内的选项（如 `INDEX DIRECTORY`）保持原 Command/E999**
——按 O 认可的保守取舍：漏一次恢复，好过把非法 SQL 恢复成"可信 AST"。

---

## Rev.F 修订说明（针对 O 第五轮独立复审）
> ⚠️ **本节为 Rev.%s 历史，仅供变更说明**；其中的分类、门槛、数字**均可能已被后续修订取代**。当前准出门槛只看 §7.3，当前用例与计数只看 §7.1 由 manifest 生成的表。

O 对 Rev.E 判定 **No-Go**，开出 2 项 BLOCK + 1 项 DOC。**我逐条独立复现，全部成立，全部接受。**

| 编号 | O 的意见 | 我的复核 | Rev.F 处置 |
|---|---|---|---|
| **BLOCK-E1** | 方法括号内只检查"能配平"，未验证分片键语法 | ✅ `HASH()`、`HASH(,)`、`HASH('id')`、`HASH(id+1)`、`HASH(lower(id))`、`HASH(a,b)` **六种非法形态全部得 1 span**，剥离后 `ast=Create`、**E999 消失**（主干对照：这些输入主干上明确报 E999） | 键值括号收紧为**精确形态**：`( 恰好一个 VAR/IDENTIFIER )`，其余一律失败关闭 |
| **BLOCK-E2** | `_NAMEY` 含 `TokenType.STRING`，同表名门禁又主动剥单引号 | ✅ `CREATE TABLE 't' (...)` / `"t"` + UNIQUE COMMENT：主干 E999，Rev.E **变成 `Create`、E999 消失** | 表名 token 白名单**删除 STRING**（只留 VAR/IDENTIFIER）；`_same_table_name()` 与 except 内比较**不再剥单引号**，只去反引号 |
| **DOC-E1** | §5.1 标题重复、§3.1 锚点失效、§8 风险表 pin 措辞、C-14 门槛区间、Y 组覆盖面表述 | ✅ 属实 | 已逐条更正 |

### 为什么五轮都没有一次到位——我自己的复盘

把五轮问题排在一起看，它们其实是**同一个错误**的五个切面：

| 轮次 | 被指出的问题 | 本质 |
|---|---|---|
| 一 | 全局正则跨字符串边界 | approve 了没证明是目标的字符 |
| 二 | `depth==1` 不等于"定义项起点" | approve 了没证明是目标的位置 |
| 三 | 第二阶段仍用同一条全局正则 | 同上，只是换了个入口 |
| 四 | `BY`/方法可选、STRING 当关键字、CTAS 括号、跨分号 | approve 了没证明是目标的 **token** |
| 五 | 括号体任意、STRING 表名 | approve 了没证明是目标的 **参数与表名** |

**共同点：我一直在写"扫描 + 排除已知的坏形态"（黑名单）。** 黑名单的问题是——
每补一种排除，总还剩下没想到的另一种；评审每轮都能再举出一个我没排除的东西，
所以永远收敛不了。**这不是 O 太苛刻，是我的写法决定了这个结果。**

**Rev.F 做的结构性改变：把黑名单换成白名单。**

1. **只接受精确形态，其余一律拒绝**——不再"扫描并排除"，而是把每个必选 token
   的类型与位置逐个断言，任何一位不符立刻 `return None`；
2. **统一规划器共用同一个严格头部定位器** `_tdsql_table_def_bounds()`。
   第五轮 §5.2.4 指出：两套头部逻辑各自演化，正是安全模型反复漂移的机制。
   合并后，"什么算合法建表头部"**只有一处定义**；
3. **原则写进模块首注释**，让后来者一眼看到约束，而不是散落在各处判断里。

按这个原则重写后，本轮 O 提的两类问题**同时**被覆盖，而不是各打一个补丁。

### 一处我按实测确认后才收紧的地方

O 要求把方法参数收紧为"单个标识符"。我先查了**冻结的 v1.6.1.9 契约**：
`_extract_tdsql_hash_key()` 用 `_TDSQL_HASH_RE` 只提取**单个**分片键；
仓内全部语料的 `TDSQL_DISTRIBUTED BY <方法>(...)` 也**全是单字段**，无多字段/表达式形态。
两者一致，故"恰好一个标识符"的收紧与既有契约不冲突。
若将来确认官方支持多字段，必须**带出处地**显式建模，不得退回"任意平衡括号"。

**另一处我没有按直觉收紧**：曾考虑要求 `BROADCAST` 必须是语句最后一个 token。
实测仓内语料中 `BROADCAST` **从未**出现在末尾（`BROADCAST COMMENT='测试表'`、
`BROADCAST SHARDKEY=sk` 各若干处，末尾 0 处、中间 8 处），这条会直接打断合法用例。
**先量再改，没有拍脑袋。**

---

## Rev.E 修订说明（针对 O 第四轮独立复审）
> ⚠️ **本节为 Rev.%s 历史，仅供变更说明**；其中的分类、门槛、数字**均可能已被后续修订取代**。当前准出门槛只看 §7.3，当前用例与计数只看 §7.1 由 manifest 生成的表。

O 对 Rev.D 判定 **No-Go**，开出 2 项 BLOCK、2 项 MAJOR。**我逐条独立复现，全部成立，全部接受。**

| 编号 | O 的意见 | 我的复核 | Rev.E 处置 |
|---|---|---|---|
| **BLOCK-D1a** | `BY` / 分片方法被写成**可选**，非法 DDL 被修成合法 | ✅ `TDSQL_DISTRIBUTED (sk)`、`... BY (sk)`、`... HASH(sk)` 三条各得 1 个 span，剥离后 `cols=2` 成功解析 | 三个必选成分改为**顺序强校验**，任一缺失立即 `return (None, [], "")` |
| **BLOCK-D1b** | 只比 `token.text`，STRING / IDENTIFIER 被当关键字 | ✅ `'TDSQL_DISTRIBUTED'`、`` `TDSQL_DISTRIBUTED` ``、`` `broadcast` `` 三条各得 1 span | 新增 `_is_bare_kw()`，**排除 STRING / IDENTIFIER** |
| **BLOCK-D1c** | 表注释恰为 `'TDSQL_DISTRIBUTED'` 会**阻断**真实尾子句恢复 | ✅ 实测 `ast=Command`、`cols=0`（无 UNIQUE COMMENT 时）/ E999（有时） | 同上——STRING 不再进入关键字分支，真实尾子句正常恢复 |
| **BLOCK-D1d** | 未拒绝双声明 / 冲突声明 | ✅ `HASH+BROADCAST`、`HASH+RANGE` 各得 2 span 并被接纳 | 一条语句只允许**一个**分布声明，第二个即失败关闭 |
| **BLOCK-D2a** | 定义列表定位器取"`TABLE` 后任意第一个左括号"，CTAS 的 `CONCAT()` 括号被冒充 | ✅ 实测 CTAS 的 SELECT 列 `broadcast` 与真实尾子句**双双被删**，仍解析成 `Create` | `_tdsql_table_def_end()`（**Rev.G 已更名为 `_tdsql_table_def_bounds()`**）改为**严格形态**：表名后必须**紧接**定义列表左括号；CTAS / LIKE 返回 `(-1, -1)` |
| **BLOCK-D2b** | 不在分号处停止；首次重试只判"非 Command"，会接纳 `exp.Block` | ✅ 两条语句得 2 span、两条尾子句都被改；`parse_one` 返回 `Block` 被接纳 | 剥离器**发现任何分号即失败关闭**；首次重试门禁补齐 **Create + kind==TABLE + 同表名** |
| **MAJOR-D1** | 依赖 pin 仍是"待拍板"，未工程闭环 | ✅ 属实 | 本版把 pin 写成**确定的改动点**：`sqlglot>=29,<31`（下界 29.0.0 为实测，O 独立复测一致） |
| **MAJOR-D2** | 施工清单/附录仍保留"复用旧正则、一字不动、52 例"等**与正文冲突**的旧指令 | ✅ 属实，且 C-10 与附录 B 第 3 条会直接指导 Q **恢复已删除的不安全实现** | 已全局清理，见本节末尾对照表 |

### 一处我按实测修正了 O 的建议写法

O 的整改建议里写：「`TDSQL_DISTRIBUTED`、`BY`、`HASH/RANGE/LIST` 均必须验证为预期的裸关键字 token；
**当前 sqlglot 实测均为 `TokenType.VAR`**」。我照此实现后**RANGE / LIST 立刻回归失败**。
实测原因：

| 关键字 | sqlglot 30.14 的 token 类型 |
|---|---|
| `TDSQL_DISTRIBUTED` / `BY` / `HASH` / `BROADCAST` | `TokenType.VAR` |
| **`RANGE`** | **`TokenType.RANGE`**（专用类型） |
| **`LIST`** | **`TokenType.LIST`**（专用类型） |

因此 Rev.E 采用**排除法**而非"只认 VAR"：`_is_bare_kw()` 拒绝 `STRING` 与 `IDENTIFIER`，
不限定具体关键字类型。这既满足 O 的意图（字符串/标识符不得冒充关键字），
又不依赖 sqlglot 给某个关键字分配哪一个 token 类型，**跨版本更稳**。

### 这一轮我最该反省的

**我又一次把"看起来是关键字的文本"当成了"关键字"。** Rev.D 的注释里我自己写着
「必须是真实关键字 token，不是字符串/注释/标识符内容」，代码却只比了 `token.text`——
和 Rev.B「文档写了 `at_def_start`、代码没做」是同一类错误：**注释承诺了代码没兑现的性质**。
本版起，凡是安全性质，我都在 §5 给出**对应的可执行反例**，不再只靠注释声明。

### MAJOR-D2 清理对照

| 位置 | 旧指令（危险/冲突） | Rev.E |
|---|---|---|
| §3.2 门禁表 ③b | 「复用 v1.6.2.0 同一规则（`_TDSQL_DIALECT_RE`）」 | 改为「调用 `_plan_recovery()`」 |
| 施工清单 C-10 | 「`_TDSQL_DIALECT_RE` 及旧重试块**一字未动**」 | 改为「**确认该常量已删除**」 |
| 施工清单 C-11 | 「A~F+T+N 共 **52 例**」 | 改为「A~F+T+N+X 共 **90 例**」 |
| G-13 | 「T 组 **10 例**」 | 改为「T 组 **8 例**」（T7/T8 已撤销） |
| 附录 B 第 3 条 | 「复用**同一条** `_TDSQL_DIALECT_RE`」 | 改为「调用新的 token 剥离器，**不得**恢复旧正则」 |
| §9 C-1/C-2、§8 回滚 | 「只改 1 个产品文件、4 个改动点」 | 改为「`parser_legacy.py` 5 个改动点 + 2 处依赖声明」 |
| §5.1 标题重复、附录 B「六句话」实为 7 条 | — | 已更正 |

---

## Rev.D 修订说明（针对 O 第三轮独立复审）
> ⚠️ **本节为 Rev.%s 历史，仅供变更说明**；其中的分类、门槛、数字**均可能已被后续修订取代**。当前准出门槛只看 §7.3，当前用例与计数只看 §7.1 由 manifest 生成的表。

O 对 Rev.C 判定 **No-Go**，开出 1 项 BLOCK、1 项 MAJOR、1 项 DOC。**我逐条独立复现，全部成立，全部接受。**

| 编号 | O 的意见 | 我的复核 | Rev.D 处置 |
|---|---|---|---|
| **BLOCK-C1** | 第二阶段仍对整条 SQL 做不感知作用域的 `_TDSQL_DIALECT_RE.sub()`，会删真实列、改真实注释，且错误 AST 能通过四道门禁 | ✅ **三个反例全部复现**，并进一步查明**当前生产版本 v1.6.2.1 上已经如此** | **删除 `_TDSQL_DIALECT_RE`**，新增 token 级 `_plan_recovery()`；**新旧两条恢复入口统一使用它**；两阶段 span **联合门禁** |
| **MAJOR-C1** | 依赖声明 `>=26.0.0`，但 T5（HASH+二级分区）在 26.0.0 下不成立 | ✅ **复现**，并**二分出真实下界**：26/27/28 失败，**29.0.0 起通过** | §5.0 给出实测版本矩阵与 pin 方案（需用户拍板） |
| **DOC-C1** | 文字与证据标签仍停留在 Rev.B | ✅ 属实 | 已随本版更正 |

### 这一轮我最该反省的两点

**其一，我的 T7/T8 用例是"构造得让缺陷不可能出现"。** 两条用例的尾子句都写成了 `shardkey=sk`
——而 `shardkey=` **根本不触发**那条方言正则。于是"列名 broadcast 仍在""注释原样保留"这两个
结论看着通过，实际上从未走进出问题的代码路径。O 说得对：**这是同源错误对照，不能当安全 oracle。**

**其二，我把"NG-4 不动 v1.6.2.0 的代码"当成了不可逾越的边界。** 但当既有代码被证明正在损坏数据、
而我又正把更多语句引流进去时，正确的做法是**撤销这条 NG 并把它一起修好**，而不是绕着它走。
Rev.D 因此**撤销 NG-4**。

---

## Rev.C 修订说明（针对 O 第二轮独立复审）
> ⚠️ **本节为 Rev.%s 历史，仅供变更说明**；其中的分类、门槛、数字**均可能已被后续修订取代**。当前准出门槛只看 §7.3，当前用例与计数只看 §7.1 由 manifest 生成的表。

O 对 Rev.B 判定 **No-Go**，开出 2 项 BLOCK、2 项 MAJOR。**我逐条独立复现，全部成立，无一误判，全部接受。**

| 编号 | O 的意见 | 我的复核 | Rev.C 处置 |
|---|---|---|---|
| **BLOCK-B1** | 新重试没有与 v1.6.2.0 的 TDSQL 方言重试组合 | ✅ **复现**：`UNIQUE COMMENT` + `HASH/RANGE/LIST/BROADCAST` **四类全部**仍失败、`cols=0`；`shardkey=` 对照可恢复 | 剥离后若候选降级为 `Command` 且命中既有 `_TDSQL_DIALECT_RE`，**复用同一条正则与同样的前置条件**再恢复一次（§3.2） |
| **BLOCK-B2a** | "只处理顶层定义项开头"文档声称已实现、代码实际未实现 | ✅ **复现**：`CONSTRAINT uq UNIQUE (a) COMMENT` 被计入 span，返回 **2 处**而非 1 处，与 NG-10 自相矛盾 | 显式维护 `at_def_start` 状态机（§3.1） |
| **BLOCK-B2b** | 第一个定义列表闭合后未停止，第二条语句也被扫描 | ✅ **复现**：两条语句拼接 → **2 处 span**，却只接纳第一张表的 AST | 定位定义列表左括号后开始扫描，深度归零**立即 break**（§3.1） |
| **MAJOR-B1** | 漏掉 `CREATE TEMPORARY TABLE` | ✅ **复现**：TEMPORARY + UNIQUE COMMENT 不变换、仍报 E999；且 `is_temporary_table`、R024、R032、既有测试均证明它属既有产品域 | 入口改为 `CREATE [TEMPORARY] TABLE`（§3.1） |
| **MAJOR-B2a** | `UNIQUE KEY uk USING BTREE (a)`（index_type 前置）未列入产品边界 | ✅ **复现**：不产生 span；去掉 COMMENT 后 sqlglot 同样不支持 | 产品边界由 3 类补为 **4 类**（§5.4、§7.1 C 组） |
| **MAJOR-B2b** | fixture 文件头污染审核；子集断言证明不了"零新增" | ✅ **复现**：我加的中文文件头含**全角括号**，使 gg78 原样读取多出 **R104** | fixture **只保留报告真实 DDL**，来源说明移入 `tests/fixtures/README-report-fixtures.md`；F 组改为**精确集合相等**断言 |

### 这一轮我最该反省的一点

BLOCK-B2a 是**文档写了、代码没做**——我在 Rev.B §3.1 的对照表里写下"只处理顶层定义项开头的真实
`UNIQUE [KEY|INDEX]` token"，但实现里的条件只有 `depth == 1 and tt == TokenType.UNIQUE`，
**根本没有"定义项起点"这个状态**。O 说得对：`span` 门禁只能证明"改动落在自己声明的 span 内"，
不能证明"这个 span 语义上就是目标语法"。Rev.C 因此把 S-2 拆成**词法完整性**与**语法作用域完整性**两层。

BLOCK-B1 则是我把 NG-4「不改 v1.6.2.0 方言重试」误读成了「新路径不必复用它」。在 TDSQL 平台上，
`TDSQL_DISTRIBUTED BY HASH` 是分片表的主流写法（用户在 v1.6.2.0 时明确说过"内网里有的库几乎
所有的分片表都是用这种语法"），它与 UNIQUE-COMMENT 的交集恰恰是最该修好的场景，我却漏了。

---

## Rev.B 修订说明（针对 O 的独立复审）
> ⚠️ **本节为 Rev.%s 历史，仅供变更说明**；其中的分类、门槛、数字**均可能已被后续修订取代**。当前准出门槛只看 §7.3，当前用例与计数只看 §7.1 由 manifest 生成的表。

O 对 Rev.A 判定 **No-Go**，开出 2 项 BLOCK、2 项 MAJOR。**我逐条独立复现，全部成立，无一误判，全部接受。**

| 编号 | O 的意见 | 我的复核 | Rev.B 处置 |
|---|---|---|---|
| **BLOCK-1** | 全局正则无词法边界，会改坏字符串字面量内容 | ✅ **原样复现**：他的反例中我的正则命中 **2 处**而非 1 处，`column_comments['b']` 被静默改成 `mentions UNIQUE KEY fake (a)nested` | **正则整体废弃**，改为基于 **sqlglot 词法器**的受限剥离器（§3.1） |
| **BLOCK-2** | 只判 `isinstance(exp.Create)` 门禁过宽 | ✅ **复现**：实测 `exp.Create` 同时覆盖 `CREATE VIEW / INDEX / DATABASE` | 增加**四道门禁**：等长+差异仅在批准 span、`kind=='TABLE'`、表名同一性（§3.2） |
| **MAJOR-1** | DEF-1 需依赖漂移护栏 | ✅ 实测他建议的白名单映射与我的写法**今日输出逐项相同**且更抗漂移 | 采用白名单映射 + AST 契约测试（§3.3、§7.1 A 组） |
| **MAJOR-2** | 文档未记录 sqlglot 版本 | ✅ 实测 `requirements.txt` 为 `sqlglot>=26.0.0`、`pyproject.toml` 为 `>=26.0`，**无上限** | §5.0 记录版本矩阵 |
| §2.3 安全性论证需重写 | ✅ 我的第一条性质"只在抛错语句上生效"是真的，但我用它承载了整个爆炸半径论证——它对"变换本身是否安全"什么都没说 | 见 §2.3 重写 |
| SPATIAL 维持 NORMAL / 不做方案 B | O 同意 | 保留，NG-6 措辞改为"兼容取舍"（§4） |

### 一处带回给 O 的改进：用词法器而不是手写状态机

O 在 BLOCK-1 里开出的整改是"手写维护引号/注释/转义状态的有限状态扫描器"，同时留了一句
"如果 sqlglot tokenizer 能稳定提供所需 token、字符串类型和源码位置，可以复用"。
**我把这条支路实测了，它严格更好，Rev.B 采用它**（已征得用户同意）：

- **sqlglot 词法器能处理解析器拒绝的 SQL**（词法与语法是两个阶段），且整个字符串字面量是**一个 `STRING` token**
  ——列注释里的伪 SQL 在结构上**不可见**，BLOCK-1 从根上不可能发生，而不是"靠扫描器写对";
- 代码量远小于手写 FSM，且复用的是**有维护的**词法器，转义规则（`''`、`\'`、`\\`、``` `` ```）不需要我们自己实现；
- **实测比 Rev.A 多修好 3 类合法语法**：反斜杠转义注释、前缀索引 `a(20)`、转义反引号索引名——
  这 3 类在 O 的边界清单里，手写 FSM 也要额外正确处理括号深度与转义才能覆盖。

O 边界清单里剩下的 3 类（函数索引 `((lower(a)))`、`VISIBLE`、`KEY_BLOCK_SIZE`）**不是剥离器的问题**：
把 COMMENT 完全去掉、只留这些语法，**sqlglot 自身照样 ParseError**（实测）。
故失败关闭是正确行为，本版把它们写成**显式产品边界**（§5.4、§7.1 B 组），
这正是 O §6.2 第 9 条要求的处置方式。

---

## 0. 一句话结论

两个缺陷同源同一个文件，且是**同一种错误模式**——**解析器拿不到事实，规则把"拿不到"当成了"事实不存在"**：

- **DEF-1**：索引类型用 `str(col_def).upper()` 做**裸子串包含**判断，列名 `list_unique_num` 里的 `unique` 让**普通索引被标成 UNIQUE** → R054 误报；更严重的是它**顶替了真唯一索引的位置**，导致真唯一索引根本不被检查 → **漏报**。
- **DEF-2**：sqlglot 不支持 `UNIQUE KEY ... COMMENT '...'`，整条 CREATE TABLE **抛 ParseError** → `columns/engine/charset/主键/表注释` 全空 → **R003/R004/R005/R028 集体误报**（实测还连带误报 R118）。

两处都改在 `parser_legacy.py`，产品代码净改动 **3 个点**，规则层**一行不动**。

---

## 1. 缺陷事实

### 1.1 DEF-1：普通索引被误判为唯一索引（报告 #6309）

**现场**：表 `kcfb_list_info`，`shardkey=black_list_seq_num`，10 个索引。报告给出：

> `[R054]` `` `kcfb_list_info_idx13` ``未包含分片键 'black_list_seq_num'，TDSQL要求唯一索引必须包含分片键

但 `kcfb_list_info_idx13` 是 **普通索引**：

```sql
KEY `kcfb_list_info_idx13` (`list_unique_num`,`lgl_pern_code`),
UNIQUE KEY `kcfb_list_info_idx14` (`black_list_seq_num`,`list_main_body_tp`) USING BTREE
```

**根因**：`parser_legacy.py:581-588`

```python
        # 判断索引类型
        def_str = str(col_def).upper()          # ← 把整条索引定义连同列名字符串化
        if "PRIMARY" in def_str:
            idx_type = "PRIMARY"
        elif "UNIQUE" in def_str:               # ← 裸子串包含
            idx_type = "UNIQUE"
        elif "FULLTEXT" in def_str:
            idx_type = "FULLTEXT"
```

实测 `str(col_def)`：

```
INDEX "kcfb_list_info_idx13" ("list_unique_num", "lgl_pern_code")
                                     ^^^^^^ 这里的 unique 命中了子串判断
```

其余 8 个普通索引的列名/索引名都不含这些词，所以**只有 idx13 中招**——这不是随机误报，是**由列名精确决定**的。

**暴露面（实测）**：

| 列名或**索引名**含 | 被误判为 |
|---|---|
| `unique`（`list_unique_num`、`unique_code`、索引名 `unique_lookup`） | UNIQUE |
| `primary`（`biz_primary_no`、`primary_flag`） | PRIMARY |
| `fulltext`（`fulltext_body`） | FULLTEXT |

**双重后果——漏报比误报更危险**：

`distributed.py::_iter_unique_indexes()` 的逻辑是：只要在 `parsed.indexes` 里找到 `type=="UNIQUE"` 就 `seen=True` 并 **`return`，不再走兜底正则**。假 UNIQUE 顶掉真 UNIQUE 的位置后——

> **真正的唯一索引 `kcfb_list_info_idx14` 从头到尾没有被 R054 检查过。**

本表 idx14 恰好含分片键所以没露馅。构造对照实测（探针 T8）：

| 场景 | 基线 R054 |
|---|---|
| 普通索引列名含 `unique`（诱饵）+ 真唯一索引**不含**分片键 | **★ 不报（漏报）** |
| 把诱饵列名改掉，其余不变 | 正确报出 |

**即：一张真正违反 TDSQL 约束的表，只要某个普通索引的列名里带 `unique`，就会被判成合规放行。**

**第三个后果**：R061 会把普通索引说成"唯一索引 …… 应以 `uk_` 开头"，前缀要求也跟着用错（实测）。

### 1.2 DEF-2：唯一索引带 COMMENT 导致整条语句解析崩溃（报告 #6311）

**现场**：表 `biz_tx_log`，报告给出 5 条 ERROR：

```
[E999_SYNTAX_ERROR] SQL 语句无法解析或结构不完整: Expecting ). Line 78, Col: 86.
[R003] CREATE TABLE 未指定主键
[R004] 未指定存储引擎
[R005] 未指定字符集
[R028] 表 biz_tx_log 缺少表级别COMMENT
```

而这张 DDL **四样全都写了**：

```sql
  PRIMARY KEY (`tran_day`,`tran_date`,`tx_serial_no`),
  UNIQUE KEY `uk_biztxlog` (...) COMMENT '唯一索引：交易日期+终端编号+终端流水号',
  KEY `idx_term_bizlog` (...) COMMENT '终端查询索引：...'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='联机交易流水表'
```

**根因**：sqlglot 30.14.0 的 mysql 方言**不支持 UNIQUE 索引上的 COMMENT 子句**。消融实测：

| 改动 | 结果 |
|---|---|
| 原样 | ❌ ParseError |
| 去掉索引级 COMMENT | ✅ **解析成功** |
| 去掉 `/*!50100 PARTITION BY LIST*/` | ❌ 仍失败（**分区块不是原因**） |

最小复现矩阵：

| 写法 | 结果 |
|---|---|
| `KEY k (a) COMMENT '注释'` | ✅ 成功 |
| `KEY k (a) USING BTREE COMMENT '注释'` | ✅ 成功 |
| **`UNIQUE KEY u (a) COMMENT '注释'`** | ❌ **ParseError** |
| `UNIQUE INDEX u (a) COMMENT '注释'` | ❌ ParseError |
| `UNIQUE (a) COMMENT '注释'` | ❌ ParseError |
| `UNIQUE KEY u (a) USING BTREE COMMENT '注释'` | ❌ ParseError |
| `UNIQUE KEY u (a)`（无注释） | ✅ 成功 |

**普通索引带注释没事，唯一索引带注释就挂。**

**传导链**：`parse()` 的 `except` 分支（当前 144-155 行）只做表名正则提取，**并把 `is_create_table` 置为 True**，然后 `return parsed`。于是：

```
is_create_table=True（规则会执行）  但  has_primary_key=False, engine=None,
                                        charset=None, has_table_comment=False
```

而 R003/R004/R005/R028 的守卫只有 `if not parsed.is_create_table: return None`——
**被告知这是建表语句，却一个结构事实都拿不到，于是把"拿不到"当成了"没有"。**

**决定性对照**（只删索引级 COMMENT，其余一字未改）：

```
原样      : columns=0  pk=False engine=None    charset=None     → E999,R003,R004,R005,R028
删索引注释 : columns=75 pk=True  engine='INNODB' charset='UTF8MB4' → R036,R037
```

> **R005 澄清**：R005（`ddl.py:69-77`）只读表级 `parsed.charset`，**完全不检查字段级 charset/collation**
> （全仓规则层 `grep -i collat` 零命中）。这张表写了 `DEFAULT CHARSET=utf8mb4`，而 R005 白名单是
> `("UTF8MB4","UTF8MB4_GENERAL_CI")`，本就该通过。它报的"**未指定**字符集"对该 DDL 是事实错误。
> **R005 同样是误报。** 用户已决策：字段级字符集本次不纳入，R005 维持只判表级（见 NG-7）。

### 1.3 为什么合并成一次修

两个缺陷同文件、同函数域（`parse()` 与其下的 `_parse_index_constraint()`）、同错误模式。
分两次改会让 `parser_legacy.py` 连续两轮进入变更窗口，回归成本翻倍而收益为零。
且实测证明二者**互不干扰**（漂移集合为空集，见 §5）。

---

## 2. 方案选型

### 2.1 DEF-1 的候选

| 方案 | 做法 | 取舍 |
|---|---|---|
| **A（采纳）** | 改读 AST 的 `kind` 参数 | 判据从"字符串长相"变成"语法结构"，**根治**；且实测输出域不变 |
| B | 把子串判断改成词边界正则 `\bUNIQUE\b` | 仍会被恰好名为 `unique` 的列/索引骗到；治标 |
| C | 在 R054 侧过滤 | 不治本，R061 的错误文案、其他消费者仍错 |

**为什么 A 是安全的——实测枚举 18 种索引写法**：

| 写法 | AST 节点 | `kind` |
|---|---|---|
| `KEY/INDEX k (a)`、`KEY (a)`、`USING BTREE/HASH`、前缀索引、`DESC` | `IndexColumnConstraint` | `None` |
| `FULLTEXT KEY/INDEX/(a)` | `IndexColumnConstraint` | `'FULLTEXT'` |
| `SPATIAL KEY k (a)` | `IndexColumnConstraint` | `'SPATIAL'` |
| `UNIQUE KEY/INDEX/UNIQUE (a)` | **`UniqueColumnConstraint`** | — |
| `PRIMARY KEY (a)` | **`exp.PrimaryKey`** | — |
| `CONSTRAINT c UNIQUE/PRIMARY KEY (a)` | **`Constraint`** | — |

> **`IndexColumnConstraint` 只承载 `kind ∈ {None, 'FULLTEXT', 'SPATIAL'}`。
> UNIQUE 走 `UniqueColumnConstraint`（`_parse_unique_constraint` 里硬编码 `"type": "UNIQUE"`），
> PRIMARY 走 `exp.PrimaryKey`（`_parse_create` 第 524-525 行置 `has_primary_key`），
> 二者都不经过 `_parse_index_constraint`。**

**推论（重要）**：原代码里 `idx_type = "PRIMARY"` 与 `idx_type = "UNIQUE"` 两个分支
**对合法输入结构上不可达**——它们每一次触发都是误判。删掉它们不会丢失任何正确行为。

**SPATIAL 的处置**：修复前干净列名下 SPATIAL 落在 `else` → `NORMAL`。
本方案**维持判为 NORMAL**，保证输出域与修复前逐字一致（`NORMAL` / `FULLTEXT`），blast radius 为零。
（把 SPATIAL 单独成型不属于本次缺陷，留待专项。）

### 2.2 DEF-2 的候选

| 方案 | 做法 | 取舍 |
|---|---|---|
| ~~A-正则~~（Rev.A，**已废弃**） | 全局正则剥离 UNIQUE 索引 COMMENT 后重试 | ❌ 无词法边界，会改坏字符串字面量内容（O 的 BLOCK-1，已复现） |
| **A-词法（Rev.B 起采纳，Rev.C~G 持续收紧）** | **基于 sqlglot 词法器**的受限剥离 + 严格接纳门禁后重试 | 恢复**完整** AST；伪 SQL 结构上不可见；失败关闭 |
| B | 在 `except` 补调 `_regex_fallback_create_table_props()` | 只救回 4 个字段，columns/indexes 仍空；且该函数不感知字符串字面量，`COMMENT '……PRIMARY KEY……'` 会造成 R003 **漏报** |
| C | 升级/更换 sqlglot | 影响面不可控，不在本次范围 |

**B 不做，登记 ADJ-10**（O 复审同意此取舍）。

### 2.3 安全性论证（按 O 意见重写）

Rev.A 用"只在已经抛错的语句上生效，故对能解析的一切语句零影响"一条来承载整个爆炸半径论证。
**这条陈述本身为真，但它对"变换本身是否安全"什么都没说**——而 BLOCK-1 恰恰发生在变换里。
Rev.B 把安全性拆成若干条**各自独立可验证**的性质（Rev.G 已增至 5 条）：

| 编号 | 性质 | 由什么保证 | 实测证据 |
|---|---|---|---|
| **S-1** | 不改变"首次解析即成功"语句的控制流与结果 | 恢复链只有**三条入口**，各自都要先拿到批准 span：① 首次解析得到**非 `Command` 的成功 AST** → **直接返回，不进入任何恢复**；② 首次得到 `exp.Command` → 仅当 `_plan_recovery()` 返回批准 span 时才重试（改动点 2b）；③ 抛异常进入 `except` → 仅当 `_plan_recovery()` 返回批准 span 时才重试（改动点 2）。**Rev.G 之前此处写作"新逻辑只在 `except` 内"，与 2b 冲突，第七轮 MAJOR-G2 已更正** | 全语料 197 条中仅 2 条变化，且均为本次目标缺陷 |
| **S-2a 词法完整性** | **整条恢复链**（阶段一 UNIQUE COMMENT + 阶段二 TDSQL 尾子句）的差异只落在两阶段 span 并集内 | 两阶段均为 token 级剥离并各自返回 span；最终做 `sql_clean → _final_sql` 的**联合**逐字符校验 | BLOCK-1 反例越界改写 **0**；X 组 40 例字段级精确保持（生产版本 36 例失败） |
| **S-2b 语法作用域与形态完整性** | **UNIQUE 阶段**：span 必须来自第一条 CREATE TABLE 顶层、以 UNIQUE 开头的定义项；**TDSQL 阶段**：span 必须是定义列表**闭合之后**顶层的**完整合法**方言尾子句 | UNIQUE 阶段用 `at_def_start`；TDSQL 阶段用**严格形态定位** + 必选 token 强校验 + 单声明约束 + 分号即失败关闭 | N 组 5 例 span 全为 1；§5.15 的 D1a/D1b/D1d 非法形态 span **全为 0**；CTAS / LIKE / 多语句 span **全为 0** |
| **S-2c 上下文完整性（Rev.G 引入，Rev.H 扩展到内部结构）** | 目标 span 所在的**整个语法单元及其内部结构**必须被逐 token 完整消费：表选项区逐 atom 且**每个选项使用专属值谓词**；UNIQUE 索引选项区只接受 `USING BTREE` 与 `COMMENT STRING`；**键值列表逐 key-part**；**分区子句消费到语句结束**。**存在任何未被认领的 token 即整体失败关闭** | 五个消费器统一契约 `f(toks,i) -> 下一个下标 \| -1`；三条红线：不得配平后跳过内容、不得无条件 `break`、不得用大类 token 代替选项专属值谓词 | §5.17 W 组 28 例 + §5.19 **H 组用例（数量见 §7.1a）**，在 sqlglot 30.14.0 与 29.0.0 上**逐条一致**：非法输入 0 例被修成合法，合法形态 0 例被收紧过头 |
| **S-3** | 无法证明安全时**失败关闭**，**绝不把非法 DDL 修成合法** | 采用**白名单**：只接受精确形态，其余全部 `return None`。覆盖缺 BY / 缺方法 / 缺括号 / 未知方法 / **括号体非单标识符** / 双声明 / 冲突声明 / STRING / IDENTIFIER / **STRING 表名** / CTAS / LIKE / 多语句 / 未闭合引号或括号 / **未知表选项** / **未知索引选项** / **非法 key-part** / **残缺分区子句** / **非法选项取值** | §5.15 的 13 类 + §5.16 的 10 类 + §5.17 的 15 类 + §5.19 的 **62 类**实测全部失败关闭；断言判据为 `rank(候选) ≤ rank(主干)` 且**主干的 E999 不得消失**（Rev.E/F/G 正是在这一层被吞掉） |
| **S-4** | `parsed.raw_sql` 保持原文 | 变换只作用于送进 sqlglot 的副本 | 12 例正向恢复全部 `raw_sql == 输入` |

> **S-2a 是 Rev.A 完全缺失的一条；S-2b 是 Rev.B 只写进文档、未在代码中实现的一条；
> S-2c 是 Rev.F 之前一直缺失的一条，且 Rev.G 只做到了「语法单元」层、Rev.H 才做到「内部结构」层。**
> O 第二轮指出：span 门禁只能证明「改动落在自己声明的 span 内」，
> **不能**证明「这个 span 语义上就是目标语法」——两层必须同时成立，门禁才是有效的安全证明。
> O 第六轮进一步指出：即使 span 与目标 token 序列都对，只要**目标周围还有未被理解的 token**，
> 剥离仍可能改变整条语句的语义。因此白名单必须从「目标片段」扩展到「目标所在的完整语法单元」——
> 这就是 S-2c。判定准则：**扫描器不允许存在"看不懂就跳过"的分支**。

---

## 3. 详细设计（照图施工）

### 3.0 改动点 0：新增一处 import

在 `from sqlglot.errors import SqlglotError` 之后增加一行：

```python
from sqlglot.tokens import TokenType
```

### 3.0b 改动点 0b：**删除** `_TDSQL_DIALECT_RE`（NG-4 已撤销）

删除 `parser_legacy.py` 第 16-29 行的整块注释与 `_TDSQL_DIALECT_RE = re.compile(...)` 定义。

**删除理由（实测，非推演）**：它对整条 SQL 做 `re.sub()`，不感知 token 作用域。
只要语句含真实 TDSQL 尾子句（这正是它被激活的条件），SQL 任何位置的同名文本都会被一并抹掉：

| 输入片段 | 该正则处理后 |
|---|---|
| `` `broadcast` varchar(20) `` | 列名被抹成空白，**该列消失** |
| `COMMENT 'broadcast table info'` | 变成 `COMMENT '  table info'` |
| `COMMENT 'TDSQL_DISTRIBUTED BY HASH(fake)'` | 变成 `COMMENT ' '` |

且改坏后的 SQL **仍能解析成同表名的 `exp.Create`**，四道门禁发现不了 → **静默错误 AST**。

> 全仓 `grep` 确认该常量**只被 `parser_legacy.py` 自身引用**（第 135/138 行），删除无外部影响。

### 3.0c 改动点 0c：新增 span 校验器与 token 级 TDSQL 尾子句剥离器

**位置**：原 `_TDSQL_DIALECT_RE` 所在处（即 import 区之后、`_plan_recovery` 之前）。

```python
# ── v1.6.2.2：解析恢复链的 token 级安全剥离器 ─────────────────────────────────
#
# 本文件原有的 _TDSQL_DIALECT_RE（v1.6.2.0 引入的全局正则）已删除。
# 删除原因（实测，见设计说明书 §5.14）：它对整条 SQL 做 re.sub()，不感知
# token 作用域，会把定义体里的真实内容一并抹掉——
#   `broadcast` varchar(20)                 → 列被删除（列名变成空白）
#   COMMENT 'broadcast table info'          → 注释被改成 '  table info'
#   COMMENT 'TDSQL_DISTRIBUTED BY HASH(x)'  → 注释被清空
# 且改写后的 SQL 仍能解析成同表名的 exp.Create，门禁发现不了，
# 形成**静默错误 AST**。该缺陷自 v1.6.2.0 起已在生产版本中存在。
#
# ── 本模块的设计原则：白名单，不是黑名单 ──
# 前几版反复出问题的根源是"扫描 + 排除已知的坏形态"：每补一种排除，
# 就还剩下没想到的另一种。本版一律改成**只接受精确形态、其余全部拒绝**：
#   * 建表头部：CREATE [TEMPORARY] TABLE [IF NOT EXISTS] 名[.名] (  —— 且表名
#     只接受裸标识符 VAR 与反引号标识符 IDENTIFIER；STRING（单/双引号）一律拒绝；
#   * 方言尾子句：TDSQL_DISTRIBUTED BY HASH|RANGE|LIST ( 单个标识符 )
#     —— 括号内必须**恰好一个**标识符 token，空参数、字符串、逗号、多字段、
#     运算符、函数、嵌套括号一律拒绝；
#   * 广播标志：独立的裸 BROADCAST 关键字；
#   * 其余一切形态 → 返回 None，**保持原有失败路径**（宁可继续报 E999，
#     也绝不把非法 DDL 修成"解析成功"）。
# 两个剥离器共用同一个严格头部定位器 _tdsql_table_def_bounds()，
# 避免两套安全模型再次各自漂移。


def _spans_only_diff(orig: str, new: str, spans) -> bool:
    """校验 new 相对 orig 的全部差异都落在 spans 内，且长度恒等。"""
    if new is None or len(new) != len(orig):
        return False
    for i in range(len(orig)):
        if orig[i] != new[i] and not any(s <= i <= e for s, e in spans):
            return False
    return True


# 不得当作关键字的 token 类型：字符串字面量与（反）引号标识符。
# 用"排除法"而非"只认 VAR"是实测决定的：sqlglot 30.14 里
#   TDSQL_DISTRIBUTED / BY / HASH / BROADCAST -> TokenType.VAR
#   RANGE -> TokenType.RANGE ，LIST -> TokenType.LIST（各有专用 token 类型）
# 只认 VAR 会让合法的 BY RANGE(...) / BY LIST(...) 无法恢复（已实测）。
_NON_KEYWORD_TOKENS = (TokenType.STRING, TokenType.IDENTIFIER)

# 合法标识符 token：裸名(VAR) 与反引号名(IDENTIFIER)。
# **不含 STRING**——MySQL 下 't' / "t" 会被词法器标成 STRING，
# 若把它当合法表名/分片键，就会把非法 DDL 恢复成功（第五轮 BLOCK-E2）。
_IDENT_TOKENS = (TokenType.VAR, TokenType.IDENTIFIER)


def _is_bare_kw(tok, word=None) -> bool:
    """是否为裸关键字 token（排除字符串字面量与反引号标识符）。

    `word=None` 表示"只要求是裸词、不限定具体文本"——供枚举型选项值使用。
    """
    if tok.token_type in _NON_KEYWORD_TOKENS:
        return False
    return True if word is None else (tok.text or "").upper() == word


def _tdsql_table_def_bounds(toks):
    """严格定位第一条建表语句的列定义列表。

    返回 (左括号下标, 右括号下标, 表名)；任一环节不满足返回 (-1, -1, "")。

    只接受：CREATE [TEMPORARY] TABLE [IF NOT EXISTS] <名>[.<名>] ( ... )
      * 表名只接受 VAR / IDENTIFIER，**STRING 一律拒绝**；
      * 表名之后必须**紧接**列定义左括号 —— CTAS(`AS SELECT`)、`LIKE`
        因此被拒，不会拿后续任意括号（如 CONCAT(...)）冒充定义列表。
    """
    n = len(toks)
    if n < 4 or toks[0].token_type != TokenType.CREATE:
        return -1, -1, ""
    p = 1
    if toks[p].token_type == TokenType.TEMPORARY:
        p += 1
    if p >= n or toks[p].token_type != TokenType.TABLE:
        return -1, -1, ""
    p += 1
    if (p + 2 < n and _is_bare_kw(toks[p], "IF")
            and toks[p + 1].token_type == TokenType.NOT
            and toks[p + 2].token_type == TokenType.EXISTS):
        p += 3
    if p >= n or toks[p].token_type not in _IDENT_TOKENS:
        return -1, -1, ""
    table_name = toks[p].text
    p += 1
    if (p + 1 < n and toks[p].token_type == TokenType.DOT
            and toks[p + 1].token_type in _IDENT_TOKENS):
        table_name = toks[p + 1].text
        p += 2
    if p >= n or toks[p].token_type != TokenType.L_PAREN:
        return -1, -1, ""
    open_idx = p
    d = 0
    while p < n:
        if toks[p].token_type == TokenType.L_PAREN:
            d += 1
        elif toks[p].token_type == TokenType.R_PAREN:
            d -= 1
            if d == 0:
                return open_idx, p, table_name
        p += 1
    return -1, -1, ""




# ── TDSQL 官方语法消费器（Rev.M：结构化类型表 + typed atoms + 指纹守恒）──
#
# 判据优先级：① 目标实例事实 ② TDSQL 官方文档 ③ 用户冻结决策
#             ④ 官方声明继承 MySQL 处用 MySQL 手册补边界 ⑤ sqlglot 只做词法与候选
#
# 引擎名 / 字符集 / 排序规则：裸名、反引号名、引号名都合法，但**不能是数字**
_OPT_NAMEY = (TokenType.VAR, TokenType.IDENTIFIER, TokenType.STRING)


# ── 结构化数据类型规范表（第十一轮 BLOCK-11-04）─────────────────────────────
#
# Rev.L 的 `_TYPE_SPEC = 名 -> 模式字符串` 是**双向失真**的：
#   过窄——`INTEGER` / `NUMERIC(M,D)` / `REAL(M,D)` / `ENUM(...)` / `INT ZEROFILL`
#          因指纹按字面比较而被拒（sqlglot 会把它们规范化）；`CHAR(0)` / `VARCHAR(0)`
#          / `MULTIPOINT` / `DOUBLE PRECISION` 直接进不了规划器；
#   过宽——`DECIMAL(1,2)`（scale > precision）、`DECIMAL(66,0)`、`BIT(65)`、
#          `CHAR(256)`、`VARCHAR(65536)`、`YEAR(999)`、裸 `ENUM` 全被放行。
#
# Rev.M 改为结构化规则表，每个类型显式声明：
#   canonical  规范名（**与 sqlglot 的归一结果一致**，两侧共用同一 canonicalizer）
#   arity      NONE / M_OPT / M_REQ / M_D / FSP / ENUM_SET
#   rng        各参数的闭区间（None 表示不限）
#   family     类型族，决定可接的类型属性
#
# 参数边界依据：TDSQL 官方兼容性页声明继承 MySQL 类型语义，故按 MySQL 5.7 手册取值。
_F_INT, _F_DEC, _F_STR, _F_BIN, _F_TIME, _F_OTHER = "int", "dec", "str", "bin", "time", "other"
_TYPE_RULES = {
    # 源名          : (canonical,  arity,      参数区间,                     族)
    "TINYINT":       ("TINYINT",   "M_OPT",   ((1, 255),),                  _F_INT),
    "SMALLINT":      ("SMALLINT",  "M_OPT",   ((1, 255),),                  _F_INT),
    "MEDIUMINT":     ("MEDIUMINT", "M_OPT",   ((1, 255),),                  _F_INT),
    "INT":           ("INT",       "M_OPT",   ((1, 255),),                  _F_INT),
    "INTEGER":       ("INT",       "M_OPT",   ((1, 255),),                  _F_INT),
    "BIGINT":        ("BIGINT",    "M_OPT",   ((1, 255),),                  _F_INT),
    "DECIMAL":       ("DECIMAL",   "M_D",     ((1, 65), (0, 30)),           _F_DEC),
    "NUMERIC":       ("DECIMAL",   "M_D",     ((1, 65), (0, 30)),           _F_DEC),
    "FIXED":         ("DECIMAL",   "M_D",     ((1, 65), (0, 30)),           _F_DEC),
    "FLOAT":         ("FLOAT",     "M_D",     ((1, 255), (0, 30)),          _F_DEC),
    "REAL":          ("FLOAT",     "M_D",     ((1, 255), (0, 30)),          _F_DEC),
    "DOUBLE":        ("DOUBLE",    "M_D",     ((1, 255), (0, 30)),          _F_DEC),
    # ⚠️ 实测：sqlglot 把 `DOUBLE PRECISION` 词法成**单个 token**，text 即含空格。
    #    Rev.L 只登记了二元组，该分支永不可达（第十一轮 BLOCK-11-04）。两种表现都登记。
    "DOUBLE PRECISION": ("DOUBLE",  "M_D",     ((1, 255), (0, 30)),          _F_DEC),
    "CHAR":          ("CHAR",      "M_OPT",   ((0, 255),),                  _F_STR),
    "VARCHAR":       ("VARCHAR",   "M_REQ",   ((0, 65535),),                _F_STR),
    "BINARY":        ("BINARY",    "M_OPT",   ((0, 255),),                  _F_BIN),
    "VARBINARY":     ("VARBINARY", "M_REQ",   ((0, 65535),),                _F_BIN),
    "TINYTEXT":      ("TINYTEXT",  "NONE",    (),                           _F_STR),
    "TEXT":          ("TEXT",      "M_OPT",   ((0, 65535),),                _F_STR),
    "MEDIUMTEXT":    ("MEDIUMTEXT", "NONE",   (),                           _F_STR),
    "LONGTEXT":      ("LONGTEXT",  "NONE",    (),                           _F_STR),
    "TINYBLOB":      ("TINYBLOB",  "NONE",    (),                           _F_BIN),
    "BLOB":          ("BLOB",      "M_OPT",   ((0, 65535),),                _F_BIN),
    "MEDIUMBLOB":    ("MEDIUMBLOB", "NONE",   (),                           _F_BIN),
    "LONGBLOB":      ("LONGBLOB",  "NONE",    (),                           _F_BIN),
    "ENUM":          ("ENUM",      "ENUM_SET", (),                          _F_STR),
    "SET":           ("SET",       "ENUM_SET", (),                          _F_STR),
    "DATE":          ("DATE",      "NONE",    (),                           _F_TIME),
    "YEAR":          ("YEAR",      "M_OPT",   ((4, 4),),                    _F_TIME),
    "TIME":          ("TIME",      "FSP",     ((0, 6),),                    _F_TIME),
    "DATETIME":      ("DATETIME",  "FSP",     ((0, 6),),                    _F_TIME),
    "TIMESTAMP":     ("TIMESTAMP", "FSP",     ((0, 6),),                    _F_TIME),
    "BIT":           ("BIT",       "M_OPT",   ((1, 64),),                   _F_OTHER),
    "BOOL":          ("BOOLEAN",   "NONE",    (),                           _F_OTHER),
    "BOOLEAN":       ("BOOLEAN",   "NONE",    (),                           _F_OTHER),
    "JSON":          ("JSON",      "NONE",    (),                           _F_OTHER),
    "GEOMETRY":      ("GEOMETRY",  "NONE",    (),                           _F_OTHER),
    "POINT":         ("POINT",     "NONE",    (),                           _F_OTHER),
    "LINESTRING":    ("LINESTRING", "NONE",   (),                           _F_OTHER),
    "POLYGON":       ("POLYGON",   "NONE",    (),                           _F_OTHER),
    "MULTIPOINT":    ("MULTIPOINT", "NONE",   (),                           _F_OTHER),
    "MULTILINESTRING": ("MULTILINESTRING", "NONE", (),                      _F_OTHER),
    "MULTIPOLYGON":  ("MULTIPOLYGON", "NONE", (),                           _F_OTHER),
    "GEOMETRYCOLLECTION": ("GEOMETRYCOLLECTION", "NONE", (),                _F_OTHER),
}
# 多 token 类型名。⚠️ sqlglot 对 `DOUBLE PRECISION` 的词法表现随上下文而异，
# 故两种表现都要能进：这里既登记二元组，`_TYPE_RULES` 也含单词 `DOUBLE`。
_TYPE_MULTIWORD = {("DOUBLE", "PRECISION"): "DOUBLE"}
# 类型属性按**族**开放：数值族才能 UNSIGNED/ZEROFILL，字符族才能 BINARY。
_TYPE_ATTRS_BY_FAMILY = {
    _F_INT:   ("UNSIGNED", "SIGNED", "ZEROFILL"),
    _F_DEC:   ("UNSIGNED", "SIGNED", "ZEROFILL"),
    _F_STR:   ("BINARY",),
    _F_BIN:   (),
    _F_TIME:  (),
    _F_OTHER: (),
}
# sqlglot 回生成时**丢弃** ZEROFILL（实测），故它不参与候选比对；
# 它是显示属性，规则层无消费者。记入源指纹但比对时归一掉。
_TYPE_ATTRS_DROPPED_BY_AST = ("ZEROFILL", "SIGNED")


def _int_val(tok, allow_zero=False):
    """十进制整数字面量的值；不是则返回 None。"""
    if tok.token_type != TokenType.NUMBER:
        return None
    txt = (tok.text or "").strip()
    if not txt.isdigit():
        return None
    v = int(txt)
    return v if (allow_zero or v > 0) else None


def _in_range(v, rng):
    lo, hi = rng
    return (lo is None or v >= lo) and (hi is None or v <= hi)


def _consume_data_type(toks, i, stop):
    """按结构化规则表消费列数据类型。

    返回 `(下一个下标, (canonical, 参数元组, 属性元组))` 或 `(-1, None)`。
    源侧与候选侧**共用本函数**，从而消除 `INTEGER`/`NUMERIC`/`REAL` 等别名
    以及 `ZEROFILL` 被 AST 丢弃导致的假不一致（第十一轮 BLOCK-11-04）。
    """
    if i >= stop:
        return -1, None
    src = (toks[i].text or "").upper()
    j = i + 1
    rule = None
    if j < stop and (src, (toks[j].text or "").upper()) in _TYPE_MULTIWORD:
        rule = _TYPE_RULES[_TYPE_MULTIWORD[(src, (toks[j].text or "").upper())]]
        j += 1
    if rule is None:
        if toks[i].token_type in _NON_KEYWORD_TOKENS:
            return -1, None
        rule = _TYPE_RULES.get(src)
        if rule is None:
            return -1, None
    canonical, arity, rng, family = rule
    args = ()
    if j < stop and toks[j].token_type == TokenType.L_PAREN:
        if arity == "NONE":
            return -1, None                            # JSON(1) / DATE(1) → 失败关闭
        k = j + 1
        if arity == "ENUM_SET":
            vals = []
            while True:
                if k >= stop or toks[k].token_type != TokenType.STRING:
                    return -1, None
                vals.append((toks[k].text or ""))
                k += 1
                if k < stop and toks[k].token_type == TokenType.COMMA:
                    k += 1
                    continue
                break
            args = tuple(vals)                         # **保留逐值内容**，不再只记数量
        else:
            nums = []
            while True:
                v = _int_val(toks[k], allow_zero=True) if k < stop else None
                if v is None:
                    return -1, None
                nums.append(v)
                k += 1
                if k < stop and toks[k].token_type == TokenType.COMMA:
                    k += 1
                    continue
                break
            if arity in ("M_OPT", "M_REQ", "FSP"):
                if len(nums) != 1:
                    return -1, None
            elif arity == "M_D":
                if len(nums) not in (1, 2):
                    return -1, None
            for idx, v in enumerate(nums):
                if idx >= len(rng) or not _in_range(v, rng[idx]):
                    return -1, None                    # 越界（BIT(65) / CHAR(256) / YEAR(999)…）
            if arity == "M_D" and len(nums) == 2 and nums[1] > nums[0]:
                return -1, None                        # scale 不得大于 precision
            args = tuple(nums)
        if k >= stop or toks[k].token_type != TokenType.R_PAREN:
            return -1, None
        j = k + 1
    else:
        if arity == "M_REQ":
            return -1, None                            # VARCHAR 必须带长度
        if arity == "ENUM_SET":
            return -1, None                            # 裸 ENUM / SET → 失败关闭
    allowed = _TYPE_ATTRS_BY_FAMILY.get(family, ())
    attrs = []
    while j < stop and _is_bare_kw(toks[j]):
        a = (toks[j].text or "").upper()
        if a not in allowed:
            break
        if a in attrs:
            return -1, None
        attrs.append(a)
        j += 1
    # 属性与类型族错配（DATE UNSIGNED / JSON BINARY…）在**规划层**即拒绝
    if j < stop and _is_bare_kw(toks[j]) and (toks[j].text or "").upper() in (
            "UNSIGNED", "SIGNED", "ZEROFILL", "BINARY"):
        return -1, None
    keep = tuple(a for a in attrs if a not in _TYPE_ATTRS_DROPPED_BY_AST)
    return j, (canonical, args, keep)


def _canonical_type_from_sql(text, dialect="mysql"):
    """把候选 AST 回生成的类型文本送进**同一个** `_consume_data_type()`。

    这样别名归一、参数形态、属性丢弃三件事在两侧完全一致，
    不再出现"源写 `NUMERIC(10,2)`、AST 写 `DECIMAL(10, 2)`"这类假不一致。
    """
    try:
        toks = sqlglot.Dialect.get_or_raise(dialect).tokenizer_class().tokenize(text)
    except Exception:
        return None
    j, shape = _consume_data_type(toks, 0, len(toks))
    return shape if (j == len(toks) and shape is not None) else None


# ── 列约束与 DEFAULT（结构化指纹）──────────────────────────────────────────
_DEFAULT_LITERAL_TOKENS = (TokenType.STRING, TokenType.NUMBER, TokenType.NULL,
                           TokenType.TRUE, TokenType.FALSE,
                           TokenType.HEX_STRING, TokenType.BIT_STRING)
_DEFAULT_TIME_FUNCS = ("CURRENT_TIMESTAMP", "NOW", "LOCALTIME", "LOCALTIMESTAMP")
# 腾讯官方建表页列级 COLUMN_FORMAT 只有三值；Rev.L 误加了表级 ROW_FORMAT 的
# `COMPRESSED`（第十一轮 BLOCK-11-06 §9.2）。
_COLUMN_FORMAT_ENUM = ("FIXED", "DYNAMIC", "DEFAULT")
_COL_CONSTRAINT_ONCE = ("NULLABILITY", "DEFAULT", "AUTO_INCREMENT", "COMMENT",
                        "COLLATE", "CHARACTER_SET", "KEYNESS", "ON_UPDATE",
                        "COLUMN_FORMAT", "ENGINE_ATTRIBUTE")
# sqlglot 回生成列定义时**不保留**这些约束（实测），故它们记入源指纹但不参与候选比对
_COL_CONSTRAINT_NOT_IN_AST = ("COLUMN_FORMAT", "ENGINE_ATTRIBUTE")


def _consume_default_value(toks, i, stop):
    """消费 DEFAULT / ON UPDATE 的值；返回 (下一个下标, 值指纹) 或 (-1, None)。

    第十一轮 BLOCK-11-04：时间函数精度必须落在 0~6，
    `DEFAULT CURRENT_TIMESTAMP(7)` 不得放行。
    """
    if i >= stop:
        return -1, None
    tt = toks[i].token_type
    if tt in (TokenType.DASH, TokenType.PLUS):
        # 符号**只能**修饰数值字面量
        if i + 1 < stop and toks[i + 1].token_type == TokenType.NUMBER:
            # 正号归一：sqlglot 回生成时丢弃 `+`（实测 `DEFAULT +1` → `DEFAULT 1`），
            # 两侧必须得到同一规范形，否则合法正例会被门禁误拒。
            sign = "-" if tt == TokenType.DASH else ""
            return i + 2, ("num", sign + (toks[i + 1].text or ""))
        return -1, None
    if tt == TokenType.CURRENT_TIMESTAMP or (
            _is_bare_kw(toks[i]) and (toks[i].text or "").upper() in _DEFAULT_TIME_FUNCS):
        fname = (toks[i].text or "").upper()
        j, fsp = i + 1, None
        if j + 1 < stop and toks[j].token_type == TokenType.L_PAREN:
            if toks[j + 1].token_type == TokenType.R_PAREN:
                j += 2
            else:
                v = _int_val(toks[j + 1], allow_zero=True) if j + 1 < stop else None
                if v is None or not (0 <= v <= 6) or not (
                        j + 2 < stop and toks[j + 2].token_type == TokenType.R_PAREN):
                    return -1, None                    # fsp 越界 → 失败关闭
                fsp, j = v, j + 3
        return j, ("time", fname, fsp)
    if tt in _DEFAULT_LITERAL_TOKENS:
        return i + 1, (("null",) if tt == TokenType.NULL
                       else ("lit", tt.name, (toks[i].text or "")))
    return -1, None                                    # 裸标识符 / 任意表达式 → 失败关闭


def _consume_column_constraints(toks, i, stop):
    """消费列约束序列；返回 (下一个下标, 约束元组, 可掩码 span) 或 (-1, None, [])。

    第十一轮 BLOCK-11-06：官方列属性 `COLUMN_FORMAT` / `ENGINE_ATTRIBUTE`
    在 sqlglot 30.x 上**候选仍 ParseError**（Rev.L 只验了规划层就宣称"已恢复"，
    结论与代码相反）。本版按复审方推荐方案把它们作为**辅助掩码 span**：
    只在已有主目标时随之掩码，`raw_sql` 不变，且实测 119 条规则无消费者。
    """
    seen, fp, spans = [], [], []
    j = i
    while j < stop:
        tt = toks[j].token_type
        txt = (toks[j].text or "").upper()
        if tt == TokenType.COMMA:
            break
        if tt == TokenType.NOT and j + 1 < stop and toks[j + 1].token_type == TokenType.NULL:
            ident, val, j = "NULLABILITY", "NOTNULL", j + 2
        elif tt == TokenType.NULL:
            ident, val, j = "NULLABILITY", "NULL", j + 1
        elif tt == TokenType.DEFAULT:
            k, val = _consume_default_value(toks, j + 1, stop)
            if k < 0:
                return -1, None, []
            ident, j = "DEFAULT", k
        elif tt == TokenType.AUTO_INCREMENT:
            ident, val, j = "AUTO_INCREMENT", None, j + 1
        elif tt == TokenType.COMMENT:
            if not (j + 1 < stop and toks[j + 1].token_type == TokenType.STRING):
                return -1, None, []
            ident, val, j = "COMMENT", None, j + 2
        elif tt in (TokenType.COLLATE, TokenType.CHARACTER_SET):
            if not (j + 1 < stop and toks[j + 1].token_type in _OPT_NAMEY):
                return -1, None, []
            ident = "COLLATE" if tt == TokenType.COLLATE else "CHARACTER_SET"
            val, j = (toks[j + 1].text or "").lower(), j + 2
        elif tt == TokenType.PRIMARY_KEY:
            ident, val, j = "KEYNESS", "PRIMARY", j + 1
        elif tt == TokenType.UNIQUE:
            j += 1
            if j < stop and toks[j].token_type == TokenType.KEY:
                j += 1
            ident, val = "KEYNESS", "UNIQUE"
        elif tt == TokenType.KEY:
            ident, val, j = "KEYNESS", "KEY", j + 1
        elif tt == TokenType.ON and j + 1 < stop and toks[j + 1].token_type == TokenType.UPDATE:
            k, val = _consume_default_value(toks, j + 2, stop)
            if k < 0 or not (isinstance(val, tuple) and val[0] == "time"):
                return -1, None, []
            ident, j = "ON_UPDATE", k
        elif _is_bare_kw(toks[j]) and txt == "COLUMN_FORMAT":
            if not (j + 1 < stop and _is_bare_kw(toks[j + 1])
                    and (toks[j + 1].text or "").upper() in _COLUMN_FORMAT_ENUM):
                return -1, None, []
            ident, val = "COLUMN_FORMAT", (toks[j + 1].text or "").upper()
            spans.append((toks[j].start, toks[j + 1].end))      # 辅助掩码
            j += 2
        elif _is_bare_kw(toks[j]) and txt == "ENGINE_ATTRIBUTE":
            k = j + 1
            if k < stop and toks[k].token_type == TokenType.EQ:
                k += 1
            if k >= stop or toks[k].token_type != TokenType.STRING:
                return -1, None, []
            ident, val = "ENGINE_ATTRIBUTE", "<str>"
            spans.append((toks[j].start, toks[k].end))          # 辅助掩码
            j = k + 1
        else:
            return -1, None, []                        # 未知列约束（含列级 STORAGE）→ 失败关闭
        if ident in _COL_CONSTRAINT_ONCE and ident in [x[0] for x in fp]:
            return -1, None, []                        # 重复/矛盾约束
        fp.append((ident, val))
    return j, tuple(fp), spans


def _consume_column_definition(toks, i, stop):
    """消费一个完整列定义；返回 (下一个下标, 列指纹, 可掩码 span) 或 (-1, None, [])。

    列指纹为**结构化元组**（第十一轮 BLOCK-11-05：禁止 `|` 拼接后再 split——
    合法反引号列名 `` `a|b` `` 会把字符串指纹拆坏）。
    """
    if i >= stop or toks[i].token_type not in _IDENT_TOKENS:
        return -1, None, []
    col = (toks[i].text or "").strip("` ").lower()
    j, shape = _consume_data_type(toks, i + 1, stop)
    if j < 0:
        return -1, None, []
    j, cons, spans = _consume_column_constraints(toks, j, stop)
    if j < 0:
        return -1, None, []
    return j, ("col", col, shape, cons), spans


# ── 索引：按 kind 分支 + 结构化指纹（第十一轮 BLOCK-11-05 / MAJOR-11-01）─────
_TDSQL_INDEX_TYPES = ("BTREE",)
_INDEX_LEAD_WORDS = ("FULLTEXT", "SPATIAL")


def _index_lead(toks, i, stop):
    """识别索引定义项的引导形态；不是索引返回 None。

    第十一轮 MAJOR-11-01：Rev.L 的 `_is_index_item()` 要求 FULLTEXT/SPATIAL
    后必须紧跟 KEY/INDEX，而消费器却支持裸形态——**入口与消费器判据不一致**，
    合法的 `FULLTEXT (col)` 被错误送进列消费器。本函数是**唯一**引导判据，
    入口与消费器共用它。
    """
    if i >= stop:
        return None
    tt = toks[i].token_type
    if tt == TokenType.PRIMARY_KEY:
        return "PRIMARY"
    if tt == TokenType.UNIQUE:
        return "UNIQUE"
    if tt in (TokenType.KEY, TokenType.INDEX):
        return "NORMAL"
    if _is_bare_kw(toks[i]) and (toks[i].text or "").upper() in _INDEX_LEAD_WORDS:
        # 裸 FULLTEXT/SPATIAL 也算，但必须后接 KEY/INDEX、索引名或左括号，
        # 以免把名为 `fulltext` 的**列**误判成索引（反引号形态已由 _is_bare_kw 排除）
        if i + 1 < stop and (toks[i + 1].token_type in (TokenType.KEY, TokenType.INDEX,
                                                        TokenType.L_PAREN)
                             or toks[i + 1].token_type in _IDENT_TOKENS):
            return (toks[i].text or "").upper()
    return None


def _consume_index_definition(toks, i, stop):
    """消费一个索引定义项。

    返回 `(下一个下标, 主目标 COMMENT span, 辅助掩码 span, 索引指纹)`
    或 `(-1, [], [], None)`。指纹为结构化元组。
    """
    kind = _index_lead(toks, i, stop)
    if kind is None:
        return -1, [], [], None
    j = i + 1
    if kind in ("UNIQUE",) + _INDEX_LEAD_WORDS:
        if j < stop and toks[j].token_type in (TokenType.KEY, TokenType.INDEX):
            j += 1
    iname = ""
    if kind != "PRIMARY":                              # PRIMARY 之后不得有索引名
        if j < stop and toks[j].token_type in _IDENT_TOKENS:
            iname = (toks[j].text or "").strip("` ").lower()
            j += 1
    seen_opt = []                                      # 前置与后置 index_type 共用
    if j < stop and toks[j].token_type == TokenType.USING:
        if not (j + 1 < stop and _is_bare_kw(toks[j + 1])
                and (toks[j + 1].text or "").upper() in _TDSQL_INDEX_TYPES):
            return -1, [], [], None
        seen_opt.append("USING")
        j += 2
    j, asc_spans, kparts = _consume_index_key_parts(toks, j, stop)
    if j < 0:
        return -1, [], [], None
    uq_spans = []
    while j < stop and toks[j].token_type != TokenType.COMMA:
        tt = toks[j].token_type
        if tt == TokenType.USING:
            if "USING" in seen_opt:
                return -1, [], [], None
            if not (j + 1 < stop and _is_bare_kw(toks[j + 1])
                    and (toks[j + 1].text or "").upper() in _TDSQL_INDEX_TYPES):
                return -1, [], [], None
            seen_opt.append("USING")
            j += 2
            continue
        if tt == TokenType.COMMENT:
            if "COMMENT" in seen_opt:
                return -1, [], [], None
            if not (j + 1 < stop and toks[j + 1].token_type == TokenType.STRING):
                return -1, [], [], None
            seen_opt.append("COMMENT")
            # UNIQUE / PRIMARY 的 COMMENT 是 sqlglot ParseError → 主目标，记 span；
            # NORMAL / FULLTEXT / SPATIAL 可解析 → 原样保留（生产 gg78 即此形态）
            if kind in ("UNIQUE", "PRIMARY"):
                uq_spans.append((toks[j].start, toks[j + 1].end))
            j += 2
            continue
        return -1, [], [], None
    return j, uq_spans, asc_spans, ("idx", kind, iname, kparts, tuple(sorted(seen_opt)))


def _consume_index_key_parts(toks, i, stop):
    """消费索引键值列表。

    返回 `(下一个下标, ASC/DESC 掩码 span, key_part 元组)` 或 `(-1, [], ())`。
    key_part 元组形如 `((列名, 前缀长度|None, 'ASC'|'DESC'|None), ...)`。
    """
    if i >= stop or toks[i].token_type != TokenType.L_PAREN:
        return -1, [], ()
    spans, parts = [], []
    j = i + 1
    while True:
        if j >= stop or toks[j].token_type not in _IDENT_TOKENS:
            return -1, [], ()
        name = (toks[j].text or "").strip("` ").lower()
        j += 1
        plen = None
        if j < stop and toks[j].token_type == TokenType.L_PAREN:
            # 索引前缀长度必须是**正整数**（与类型的 scale/fsp 不同，后者允许 0）
            v = _int_val(toks[j + 1], allow_zero=False) if j + 1 < stop else None
            if v is None or not (j + 2 < stop and toks[j + 2].token_type == TokenType.R_PAREN):
                return -1, [], ()
            plen, j = v, j + 3
        order = None
        if j < stop and toks[j].token_type in (TokenType.ASC, TokenType.DESC):
            order = toks[j].token_type.name
            spans.append((toks[j].start, toks[j].end))
            j += 1
        parts.append((name, plen, order))
        if j < stop and toks[j].token_type == TokenType.COMMA:
            j += 1
            continue
        if j < stop and toks[j].token_type == TokenType.R_PAREN:
            return j + 1, spans, tuple(parts)
        return -1, [], ()


def _consume_ident(toks, i):
    """消费一个标识符（裸名或反引号名），返回下一个下标；否则 -1。"""
    n = len(toks)
    if i < n and toks[i].token_type in _IDENT_TOKENS:
        return i + 1
    return -1


def _consume_ident_list(toks, i):
    """消费 `( ident [, ident]* )`，返回下一个下标；否则 -1。至少一个，逗号不得前导/尾随/连续。"""
    n = len(toks)
    if i >= n or toks[i].token_type != TokenType.L_PAREN:
        return -1
    j = i + 1
    while True:
        j = _consume_ident(toks, j)
        if j < 0:
            return -1
        if j < n and toks[j].token_type == TokenType.COMMA:
            j += 1
            continue
        if j < n and toks[j].token_type == TokenType.R_PAREN:
            return j + 1
        return -1


# ── 分区值与分区定义（第十轮 BLOCK-J5）───────────────────────────────────────
# 官方二级分区页只明示 year / month / day 三个日期函数；
# Rev.J 另外放行的 DAYOFMONTH / TO_DAYS / TO_SECONDS / UNIX_TIMESTAMP
# 无目标实例证据，本版收回并登记为 unsupported_unproven（KFN 表 B 类）。
_PARTITION_FUNCS = ("YEAR", "MONTH", "DAY")
_SECONDARY_PARTITION_METHODS = ("RANGE", "LIST")
_TDSQL_SHARD_METHODS = ("HASH", "RANGE", "LIST")


def _consume_partition_expr(toks, i, stop):
    """消费分区表达式 `( col )` 或 `( FUNC(col) )`；返回 (下一个下标, 指纹) 或 (-1, "")。

    ⚠️ 分支顺序：**先判"白名单函数 + 左括号"，再判普通列**。
    只有 `YEAR` 有专属 TokenType，`MONTH`/`DAY` 被词法成 VAR；顺序反了它们
    会先被当成普通列名，永远走不到函数分支（第九轮 BLOCK-X5 死分支）。
    """
    if i >= stop or toks[i].token_type != TokenType.L_PAREN:
        return -1, ""
    j = i + 1
    if (j + 1 < stop and toks[j].token_type not in _NON_KEYWORD_TOKENS
            and (toks[j].text or "").upper() in _PARTITION_FUNCS
            and toks[j + 1].token_type == TokenType.L_PAREN):
        fname = (toks[j].text or "").upper()
        # 函数参数必须**恰好一个**列标识符
        if not (j + 3 < stop and toks[j + 2].token_type in _IDENT_TOKENS
                and toks[j + 3].token_type == TokenType.R_PAREN):
            return -1, ""
        shape, j = "%s(1)" % fname, j + 4
    elif j < stop and toks[j].token_type in _IDENT_TOKENS:
        shape, j = "col:%s" % (toks[j].text or "").strip("` ").lower(), j + 1
    else:
        return -1, ""
    return (j + 1, shape) if (j < stop and toks[j].token_type == TokenType.R_PAREN) else (-1, "")


def _consume_value_list(toks, i, stop):
    """消费 `( 字面量 [, 字面量]* )`；返回 (下一个下标, 值个数) 或 (-1, 0)。

    第十轮 BLOCK-J5：**符号只能修饰数值**。Rev.J 先可选吃掉 DASH 再统一接受
    NUMBER 或 STRING，于是 `VALUES IN (-'x')` 被恢复为 Create。
    """
    if i >= stop or toks[i].token_type != TokenType.L_PAREN:
        return -1, 0
    j, n = i + 1, 0
    while True:
        if j < stop and toks[j].token_type in (TokenType.DASH, TokenType.PLUS):
            if not (j + 1 < stop and toks[j + 1].token_type == TokenType.NUMBER):
                return -1, 0                           # 符号后必须是数字
            j += 2
        elif j < stop and toks[j].token_type in (TokenType.NUMBER, TokenType.STRING):
            j += 1
        else:
            return -1, 0
        n += 1
        if j < stop and toks[j].token_type == TokenType.COMMA:
            j += 1
            continue
        if j < stop and toks[j].token_type == TokenType.R_PAREN:
            return j + 1, n
        return -1, 0


def _consume_partition_values(toks, i, stop, method):
    """按**分区方法**消费 VALUES 子句；返回 (下一个下标, 指纹) 或 (-1, "")。

    RANGE → 只接受 `VALUES LESS THAN (...)`（`MAXVALUE` 属 KFN-1，仍失败关闭）
    LIST  → 只接受 `VALUES IN (...)`
    """
    if i >= stop or toks[i].token_type != TokenType.VALUES:
        return -1, ""
    j = i + 1
    if method == "RANGE":
        if not (j + 1 < stop and _is_bare_kw(toks[j], "LESS") and _is_bare_kw(toks[j + 1], "THAN")):
            return -1, ""
        j += 2
        if j < stop and _is_bare_kw(toks[j], "MAXVALUE"):
            return -1, ""                              # KFN-1：已登记的已知假阴性
        k, n = _consume_value_list(toks, j, stop)
        return (k, "LESS_THAN(%d)" % n) if k >= 0 else (-1, "")
    if method == "LIST":
        if not (j < stop and toks[j].token_type == TokenType.IN):
            return -1, ""
        k, n = _consume_value_list(toks, j + 1, stop)
        return (k, "IN(%d)" % n) if k >= 0 else (-1, "")
    return -1, ""                                      # HASH 不得挂 VALUES 定义表


def _consume_partition_options(toks, i, stop):
    """按官方顺序消费 partition_option：`[STORAGE] ENGINE [=] name` 然后 `COMMENT [=] str`。

    第十轮 BLOCK-J5：Rev.J 拒绝官方的 `STORAGE ENGINE=`，却接受反序的
    `COMMENT=… ENGINE=…`。本版按官方序列建小状态机，两者各至多一次且不得反序。
    返回 (下一个下标, 可掩码 span, 指纹)。
    """
    spans, fp = [], []
    j = i
    if j < stop and _is_bare_kw(toks[j], "STORAGE"):
        st = j
        j += 1
        if not (j < stop and _is_bare_kw(toks[j], "ENGINE")):
            return -1, [], ""
        k = j + 1
        if k < stop and toks[k].token_type == TokenType.EQ:
            k += 1
        if k >= stop or toks[k].token_type not in _OPT_NAMEY:
            return -1, [], ""
        spans.append((toks[st].start, toks[k].end))
        fp.append("STORAGE_ENGINE")
        j = k + 1
    elif j < stop and _is_bare_kw(toks[j], "ENGINE"):
        k = j + 1
        if k < stop and toks[k].token_type == TokenType.EQ:
            k += 1
        if k >= stop or toks[k].token_type not in _OPT_NAMEY:
            return -1, [], ""
        spans.append((toks[j].start, toks[k].end))
        fp.append("ENGINE")
        j = k + 1
    if j < stop and toks[j].token_type == TokenType.COMMENT:
        k = j + 1
        if k < stop and toks[k].token_type == TokenType.EQ:
            k += 1
        if k >= stop or toks[k].token_type != TokenType.STRING:
            return -1, [], ""
        spans.append((toks[j].start, toks[k].end))
        fp.append("COMMENT")
        j = k + 1
    return j, spans, "/".join(fp)


def _consume_partition_defs(toks, i, stop, method, require_partition_kw):
    """消费分区/分片定义表；返回 (下一个下标, 可掩码 span, 指纹) 或 (-1, [], "")。"""
    if i >= stop or toks[i].token_type != TokenType.L_PAREN:
        return -1, [], ""
    spans, defs = [], []
    j = i + 1
    while True:
        has_kw = j < stop and toks[j].token_type == TokenType.PARTITION
        if has_kw != require_partition_kw:
            return -1, [], ""
        if has_kw:
            j += 1
        if j >= stop or toks[j].token_type not in _IDENT_TOKENS:
            return -1, [], ""
        pname = (toks[j].text or "").strip("` ").lower()
        j += 1
        j, vshape = _consume_partition_values(toks, j, stop, method)
        if j < 0:
            return -1, [], ""
        j, osp, oshape = _consume_partition_options(toks, j, stop)
        if j < 0:
            return -1, [], ""
        spans.extend(osp)
        defs.append("%s:%s:%s" % (pname, vshape, oshape))
        if j < stop and toks[j].token_type == TokenType.COMMA:
            j += 1
            continue
        if j < stop and toks[j].token_type == TokenType.R_PAREN:
            return j + 1, spans, ";".join(defs)
        return -1, [], ""


def _consume_secondary_partition(toks, i, stop):
    """消费一整个二级分区子句；返回 (下一个下标, 可掩码 span, 指纹) 或 (-1, [], "")。"""
    if i >= stop or toks[i].token_type != TokenType.PARTITION_BY:
        return -1, [], ""
    j = i + 1
    if not (j < stop and _is_bare_kw(toks[j])
            and (toks[j].text or "").upper() in _SECONDARY_PARTITION_METHODS):
        return -1, [], ""
    method = (toks[j].text or "").upper()
    j, eshape = _consume_partition_expr(toks, j + 1, stop)
    if j < 0:
        return -1, [], ""
    j, spans, dshape = _consume_partition_defs(toks, j, stop, method, require_partition_kw=True)
    if j < 0:
        return -1, [], ""
    return j, spans, "part:%s:%s:[%s]" % (method, eshape, dshape)


# ── 本地表选项（第十轮 BLOCK-J4）─────────────────────────────────────────────
#
# 官方建表页明示的 local_table_option：AUTO_INCREMENT、CHARACTER SET、COLLATE、
# COMMENT、ENGINE、ROW_FORMAT、STATS_AUTO_RECALC、STATS_PERSISTENT、
# STATS_SAMPLE_PAGES。Rev.J 把 ROW_FORMAT 与 STATS_PERSISTENT 判成
# `unsupported_unproven` 是**取证错误**，本版按官方清单补回并给出严格值域。
# CHECKSUM / AVG_ROW_LENGTH / KEY_BLOCK_SIZE / MAX_ROWS / MIN_ROWS /
# PACK_KEYS / DELAY_KEY_WRITE 无 TDSQL 或目标实例证据，继续失败关闭。
_ROW_FORMAT_ENUM = ("DEFAULT", "DYNAMIC", "FIXED", "COMPRESSED", "REDUNDANT", "COMPACT")
_TBL_OPT_SPEC = {
    # name                : (值谓词,            provenance)
    "ENGINE":               ("NAMEY",           "OFFICIAL + CORPUS×78"),
    "COMMENT":              ("STR",             "OFFICIAL + CORPUS×多"),
    "AUTO_INCREMENT":       ("POSINT",          "OFFICIAL + CORPUS×8"),
    "ROW_FORMAT":           ("ROW_FORMAT_ENUM", "OFFICIAL"),
    "STATS_AUTO_RECALC":    ("ZERO_ONE_DEFAULT", "OFFICIAL"),
    "STATS_PERSISTENT":     ("ZERO_ONE_DEFAULT", "OFFICIAL"),
    "STATS_SAMPLE_PAGES":   ("POSINT",          "OFFICIAL"),
    "SHARDKEY":             ("IDENT_LIST",      "OFFICIAL(hash/broadcast) + CORPUS×20"),
}


def _consume_table_option(toks, i, stop):
    """消费**一个**完整本地表选项；返回 (下一个下标, identity, 指纹) 或 (-1, "", "")。"""
    if i >= stop:
        return -1, "", ""
    tt = toks[i].token_type
    txt = (toks[i].text or "").upper()

    def _eq(j):
        return j + 1 if (j < stop and toks[j].token_type == TokenType.EQ) else j

    def _take(j, pred):
        j = _eq(j)
        if j >= stop:
            return -1, ""
        t = toks[j]
        if pred == "NAMEY" and t.token_type in _OPT_NAMEY:
            return j + 1, (t.text or "").lower()
        if pred == "STR" and t.token_type == TokenType.STRING:
            return j + 1, "<str>"
        if pred == "POSINT" and _int_val(t, allow_zero=False) is not None:
            return j + 1, (t.text or "")
        if pred == "ROW_FORMAT_ENUM" and _is_bare_kw(t) and (t.text or "").upper() in _ROW_FORMAT_ENUM:
            return j + 1, (t.text or "").upper()
        if pred == "ZERO_ONE_DEFAULT":
            if t.token_type == TokenType.NUMBER and (t.text or "") in ("0", "1"):
                return j + 1, (t.text or "")
            if _is_bare_kw(t, "DEFAULT"):
                return j + 1, "DEFAULT"
        if pred == "IDENT_LIST":
            if t.token_type == TokenType.L_PAREN:
                k = _consume_ident_list(toks, j)
                return (k, "<multi>") if k >= 0 else (-1, "")
            if t.token_type in _IDENT_TOKENS:
                return j + 1, (t.text or "").lower()
        return -1, ""

    if tt == TokenType.DEFAULT:
        if i + 1 < stop and toks[i + 1].token_type in (TokenType.CHARACTER_SET, TokenType.COLLATE):
            ident = "CHARSET" if toks[i + 1].token_type == TokenType.CHARACTER_SET else "COLLATE"
            j, v = _take(i + 2, "NAMEY")
            return (j, ident, "%s=%s" % (ident, v)) if j >= 0 else (-1, "", "")
        return -1, "", ""
    if tt in (TokenType.CHARACTER_SET, TokenType.COLLATE):
        ident = "CHARSET" if tt == TokenType.CHARACTER_SET else "COLLATE"
        j, v = _take(i + 1, "NAMEY")
        return (j, ident, "%s=%s" % (ident, v)) if j >= 0 else (-1, "", "")
    if tt == TokenType.COMMENT:
        j, v = _take(i + 1, "STR")
        return (j, "COMMENT", "COMMENT=<str>") if j >= 0 else (-1, "", "")
    if tt == TokenType.AUTO_INCREMENT:
        j, v = _take(i + 1, "POSINT")
        return (j, "AUTO_INCREMENT", "AUTO_INCREMENT=%s" % v) if j >= 0 else (-1, "", "")
    if tt == TokenType.VAR and txt in _TBL_OPT_SPEC:
        pred, _prov = _TBL_OPT_SPEC[txt]
        j, v = _take(i + 1, pred)
        return (j, txt, "%s=%s" % (txt, v)) if j >= 0 else (-1, "", "")
    return -1, "", ""




# ── 表尾：先解析成带子类型的 atom，再按具名 profile 校验整个序列 ──────────────
#
# 第十一轮 BLOCK-11-02：Rev.L 的四状态 FSM 含 `S2→S3` 与 `S3→S2` 回环，
# 于是 `DIST → PARTITION → DIST`、`shardkey → PARTITION → DIST` 这类
# **双一级分布声明**被放行；状态只表达"当前阶段"，不保留历史计数。
# 第十一轮 BLOCK-11-03：`shardkey=noshardkey_allset` 与普通 shardkey 被归一成
# 同一个 atom，于是伪哨兵 `shardkey=(noshardkey_allset,id)`、广播再分区全部放行。
#
# Rev.M 改为两步：① 解析成 typed atoms；② 整个序列必须**完整匹配**一个具名 profile。
# atom 子类型：
#   LOCAL(<option名>)    本地表选项
#   HASH_SHARDKEY        shardkey=<单列> 或 shardkey=(<多列>)
#   BROADCAST_SENTINEL   shardkey=noshardkey_allset（**精确哨兵**，不接受括号/混合）
#   BROADCAST_KEYWORD    裸 BROADCAST 关键字
#   DIST(<方法>)         TDSQL_DISTRIBUTED BY hash|range|list(col) [分片定义表]
#   PARTITION            二级分区子句
_BROADCAST_SENTINEL = "NOSHARDKEY_ALLSET"

# 具名 capability profile（第十一轮 MAJOR-11-02）：每条允许序列有唯一 provenance，
# **每条 SQL 必须完整匹配其中一个**，禁止跨 profile 拼接。
# 序列用正则式记法：L* 表示任意多个 LOCAL；? 表示可选。
_TAIL_PROFILES = (
    # (profile, 序列模板, provenance)
    ("TARGET_CURRENT",  ("L*",),                              "无分布声明的普通表"),
    ("TARGET_CURRENT",  ("L*", "HASH_SHARDKEY"),              "OFFICIAL hash 分片；CORPUS 生产 fixture 实测"),
    ("TARGET_CURRENT",  ("L*", "BROADCAST_SENTINEL"),         "OFFICIAL 广播表哨兵"),
    ("TARGET_CURRENT",  ("L*", "BROADCAST_KEYWORD"),          "TARGET_INSTANCE 广播表关键字形态"),
    ("TARGET_CURRENT",  ("L*", "HASH_SHARDKEY", "BROADCAST_KEYWORD"),
                                                              "ADJ-6 characterization：用户冻结的现状，**不代表 TDSQL 合法**"),
    ("TARGET_CURRENT",  ("L*", "DIST"),                       "OFFICIAL 一级 range/list 声明；目标实例 HASH 形态"),
    ("TARGET_CURRENT",  ("L*", "DIST", "PARTITION"),          "PROJECT_ACCEPTED：D5/T5 既有用例，O 第八轮明确接受"),
    ("LEGACY_PARTITION", ("L*", "HASH_SHARDKEY", "PARTITION"), "OFFICIAL 二级分区原例 `shardkey=col PARTITION BY LIST(...)`"),
    ("LEGACY_PARTITION", ("L*", "PARTITION", "DIST"),          "OFFICIAL 二级分区原例 `tb_sub_r_l`"),
    ("LEGACY_PARTITION", ("L*", "PARTITION"),                  "OFFICIAL：仅二级分区、无一级声明"),
)

# 第三个代际 profile：**已具名声明，但成员集为空**（第十一轮 MAJOR-11-02）。
# 新语法 `TDSQL_DISTRIBUTED BY HASH(col) TDSQL_PARTITION BY RANGE|LIST(col) (...)`
# 未取得目标实例证据、也未出现在 197 条语料与生产 14 表中（0 次），
# 按本方案自己的 provenance 原则归 `unsupported_unproven`：
# **登记能力代际，但不放行**——`TDSQL_PARTITION` 不产生 atom，整条语句失败关闭。
# 取得目标实例证据后，只需把下表条目搬进 `_TAIL_PROFILES` 即可，无需改判定逻辑。
_TAIL_PROFILES_UNPROVEN = (
    ("NEW_SECONDARY", ("L*", "DIST", "TDSQL_PARTITION"),
     "腾讯新版二级分区语法；无目标实例证据、语料 0 例 → 暂不放行"),
    ("NEW_SECONDARY", ("L*", "HASH_SHARDKEY", "TDSQL_PARTITION"),
     "同上"),
)


def _match_tail_profile(kinds):
    """整个 atom 序列是否完整匹配某个 profile；匹配返回 (profile, provenance)，否则 None。

    只在 `_TAIL_PROFILES` 中查找。`_TAIL_PROFILES_UNPROVEN` 是**纯登记表**，
    刻意不参与匹配——未取证的能力代际不得放行（MAJOR-11-02）。
    """
    for prof, tmpl, prov in _TAIL_PROFILES:
        seq = list(kinds)
        ok, ti = True, 0
        for part in tmpl:
            if part == "L*":
                while seq and seq[0] == "LOCAL":
                    seq.pop(0)
            else:
                if not seq or seq[0] != part:
                    ok = False
                    break
                seq.pop(0)
            ti += 1
        if ok and not seq:
            return prof, prov
    return None


def _consume_shardkey_value(toks, i, stop):
    """消费 shardkey 的值并**分型**；返回 (下一个下标, 子类型, 指纹) 或 (-1, None, None)。

    官方广播哨兵是**裸的、单个、精确**的 `noshardkey_allset`；
    `shardkey=(noshardkey_allset)`、`shardkey=(noshardkey_allset, id)` 一律不是哨兵，
    且不得被当成普通分片键放行（第十一轮 BLOCK-11-03）。
    """
    j = i + 1 if (i < stop and toks[i].token_type == TokenType.EQ) else i
    if j >= stop:
        return -1, None, None
    if toks[j].token_type == TokenType.L_PAREN:
        k, cols = j + 1, []
        while True:
            if k >= stop or toks[k].token_type not in _IDENT_TOKENS:
                return -1, None, None
            nm = (toks[k].text or "").strip("` ").lower()
            if nm.upper() == _BROADCAST_SENTINEL:
                return -1, None, None                  # 哨兵不得出现在列表里
            cols.append(nm)
            k += 1
            if k < stop and toks[k].token_type == TokenType.COMMA:
                k += 1
                continue
            if k < stop and toks[k].token_type == TokenType.R_PAREN:
                return k + 1, "HASH_SHARDKEY", ("shardkey", tuple(cols))
            return -1, None, None
    if toks[j].token_type in _IDENT_TOKENS:
        nm = (toks[j].text or "").strip("` ").lower()
        if nm.upper() == _BROADCAST_SENTINEL:
            return j + 1, "BROADCAST_SENTINEL", ("broadcast_sentinel",)
        return j + 1, "HASH_SHARDKEY", ("shardkey", (nm,))
    return -1, None, None


def _scan_table_tail(toks, start, stop):
    """把表尾解析成 typed atoms，再整体匹配 profile。

    返回 (方言目标 span, 辅助掩码 span, 表尾指纹)；不合规返回 (None, None, None)。
    """
    tgt_spans, mask_spans, atoms, fp = [], [], [], []
    seen_local = []
    i = start
    while i < stop:
        tt = toks[i].token_type
        if tt == TokenType.PARTITION_BY:
            j, msp, pshape = _consume_secondary_partition(toks, i, stop)
            if j < 0:
                return None, None, None
            mask_spans.extend(msp)
            atoms.append("PARTITION")
            fp.append(pshape)
            i = j
            continue
        if _is_bare_kw(toks[i], "TDSQL_DISTRIBUTED"):
            if not (i + 1 < stop and _is_bare_kw(toks[i + 1], "BY")):
                return None, None, None
            if not (i + 2 < stop and _is_bare_kw(toks[i + 2])
                    and (toks[i + 2].text or "").upper() in _TDSQL_SHARD_METHODS):
                return None, None, None
            method = (toks[i + 2].text or "").upper()
            j = i + 3
            if not (j + 2 < stop and toks[j].token_type == TokenType.L_PAREN
                    and toks[j + 1].token_type in _IDENT_TOKENS
                    and toks[j + 2].token_type == TokenType.R_PAREN):
                return None, None, None
            key = (toks[j + 1].text or "").strip("` ").lower()
            j += 3
            end_tok, dshape = j - 1, ()
            if j < stop and toks[j].token_type == TokenType.L_PAREN:
                if method == "HASH":
                    return None, None, None            # 官方仅 range/list 带分片定义表
                j2, msp, dshape = _consume_partition_defs(
                    toks, j, stop, method, require_partition_kw=False)
                if j2 < 0:
                    return None, None, None
                mask_spans.extend(msp)
                end_tok, j = j2 - 1, j2
            tgt_spans.append((toks[i].start, toks[end_tok].end))
            atoms.append("DIST")
            fp.append(("dist", method, key, dshape))
            i = j
            continue
        if _is_bare_kw(toks[i], "BROADCAST"):
            tgt_spans.append((toks[i].start, toks[i].end))
            atoms.append("BROADCAST_KEYWORD")
            fp.append(("broadcast_keyword",))
            i += 1
            continue
        j, ident, oshape = _consume_table_option(toks, i, stop)
        if j < 0:
            return None, None, None
        if ident == "SHARDKEY":
            k, sub, sfp = _consume_shardkey_value(toks, i + 1, stop)
            if k < 0:
                return None, None, None
            atoms.append(sub)
            fp.append(sfp)
            i = k
            continue
        if ident in seen_local:
            return None, None, None                    # 同名本地选项不可重复
        seen_local.append(ident)
        atoms.append("LOCAL")
        fp.append(oshape)
        i = j
    # ── 计数硬断言（即使 profile 表将来扩充也必须成立）──
    if sum(1 for a in atoms if a in ("HASH_SHARDKEY", "BROADCAST_SENTINEL",
                                     "BROADCAST_KEYWORD", "DIST")) > 1:
        # 唯一例外是 ADJ-6 的 `HASH_SHARDKEY + BROADCAST_KEYWORD`，由 profile 表精确批准
        if [a for a in atoms if a != "LOCAL"] != ["HASH_SHARDKEY", "BROADCAST_KEYWORD"]:
            return None, None, None
    if sum(1 for a in atoms if a == "PARTITION") > 1:
        return None, None, None
    m = _match_tail_profile(atoms)
    if m is None:
        return None, None, None                        # 未列明的序列一律失败关闭
    return tgt_spans, mask_spans, ("tail", m[0], tuple(fp))


# ── MySQL 可执行注释（第十一轮 BLOCK-11-01）─────────────────────────────────
#
# sqlglot 的词法器不会把 `/*!50100 ... */` 的内容变成主 token，而是挂在**前一个
# token 的 `comments` 属性**上。Rev.L 只遍历主 token，于是**服务器真正会执行的语法
# 对规划器完全不可见**：`/*!50100 PARTITION BY RANGE() (...) */`、两条连续
# `PARTITION BY`、甚至 `/*!50100 EVIL OPTION */` 都能恢复成 Create 并过门禁。
#
# 本版在规划入口显式处理：普通注释继续忽略；`!<版本号>` 开头的可执行注释
# **必须整段通过验证**，且本版只接受**一个完整的**二级分区 payload。
_EXEC_COMMENT_PREFIX = "!"


def _collect_executable_comments(toks):
    """收集所有 MySQL 可执行注释 payload（去掉 `!<版本号>` 前缀后的正文）。"""
    out = []
    for t in toks:
        for c in (getattr(t, "comments", None) or []):
            body = (c or "").strip()
            if not body.startswith(_EXEC_COMMENT_PREFIX):
                continue                               # 普通注释 / 优化器 hint → 继续忽略
            rest = body[1:].lstrip()
            k = 0
            while k < len(rest) and rest[k].isdigit():
                k += 1
            out.append(rest[k:].strip())
    return out


def _validate_executable_comments(toks, dialect="mysql"):
    """验证可执行注释。返回 (是否通过, 是否含二级分区)。

    白名单：**至多一个** payload，且必须是**一个完整的**
    `PARTITION BY RANGE|LIST ... (分区定义表)`，消费到 payload 结束。
    残缺、重复、未知内容一律不通过。
    """
    payloads = _collect_executable_comments(toks)
    if not payloads:
        return True, False
    if len(payloads) > 1:
        return False, False
    try:
        ptoks = sqlglot.Dialect.get_or_raise(dialect).tokenizer_class().tokenize(payloads[0])
    except Exception:
        return False, False
    if not ptoks or ptoks[0].token_type != TokenType.PARTITION_BY:
        return False, False
    j, _msp, _fp = _consume_secondary_partition(ptoks, 0, len(ptoks))
    if j != len(ptoks):
        return False, False                            # 未消费到结尾 → 失败关闭
    return True, True


def _scan_definition_list(toks, open_idx, close_idx):
    """逐项消费顶层定义列表。

    返回 (定义指纹元组, 主目标 span, 辅助掩码 span)；不合规返回 (None, [], [])。
    """
    defs, uq_spans, mask_spans = [], [], []
    i = open_idx + 1
    while i < close_idx:
        if toks[i].token_type == TokenType.CONSTRAINT:
            # NG-10 / ADJ-11：不作恢复目标，但**逐 token 消费以完成整句校验**
            k = i + 1
            if k < close_idx and toks[k].token_type in _IDENT_TOKENS:
                k += 1
            j, _usp, asp, shape = _consume_index_definition(toks, k, close_idx)
            if j < 0 or shape is None:
                return None, [], []
            mask_spans.extend(asp)
            defs.append(("constraint",) + shape)
        elif _index_lead(toks, i, close_idx) is not None:
            j, usp, asp, shape = _consume_index_definition(toks, i, close_idx)
            if j < 0 or shape is None:
                return None, [], []
            uq_spans.extend(usp)
            mask_spans.extend(asp)
            defs.append(shape)
        else:
            j, shape, csp = _consume_column_definition(toks, i, close_idx)
            if j < 0:
                return None, [], []
            mask_spans.extend(csp)
            defs.append(shape)
        if j < close_idx and toks[j].token_type == TokenType.COMMA:
            j += 1
            if j >= close_idx:
                return None, [], []
        elif j < close_idx:
            return None, [], []
        i = j
    return (tuple(defs), uq_spans, mask_spans) if defs else (None, [], [])


def _strip_terminal_semicolon(toks):
    """允许 0 或 1 个、且仅位于 EOF 前的终止分号；否则返回 None。"""
    n = len(toks)
    sem = [k for k, t in enumerate(toks) if t.token_type == TokenType.SEMICOLON]
    if not sem:
        return toks
    if len(sem) > 1 or sem[0] != n - 1:
        return None
    return toks[:-1]


def _plan_recovery(sql: str, dialect: str = "mysql"):
    """统一恢复规划器：按 TDSQL 官方语法验证整条建表语句并生成结构化指纹。"""
    try:
        toks = sqlglot.Dialect.get_or_raise(dialect).tokenizer_class().tokenize(sql)
    except Exception:
        return None
    ok, exec_part = _validate_executable_comments(toks, dialect)
    if not ok:
        return None                                    # 可执行注释未通过验证 → 失败关闭
    toks = _strip_terminal_semicolon(toks)
    if toks is None:
        return None
    open_idx, close_idx, table_name = _tdsql_table_def_bounds(toks)
    if open_idx < 0:
        return None
    defs, uq_spans, mask_a = _scan_definition_list(toks, open_idx, close_idx)
    if defs is None:
        return None
    tgt_spans, mask_b, tail_fp = _scan_table_tail(toks, close_idx + 1, len(toks))
    if tgt_spans is None:
        return None
    primary = list(uq_spans) + list(tgt_spans)
    if not primary:
        return None                                    # 无主目标 → 不恢复
    tok_part = any(t.token_type == TokenType.PARTITION_BY for t in toks)
    return {
        "table": table_name,
        "primary_spans": primary,
        "auxiliary_spans": list(mask_a) + list(mask_b),
        "fingerprint": {
            "table": (table_name or "").strip("` ").lower(),
            "definitions": defs,
            "tail": tail_fp,
        },
        # 分区保真门禁只对**主 token 流里的**分区生效；
        # 可执行注释里的分区 sqlglot 不产生节点，其完整性已由
        # `_validate_executable_comments()` 独立证明（具名 provenance）。
        "had_partition": tok_part,
        "exec_comment_partition": exec_part,
    }


def _same_table_name(node, expected: str) -> bool:
    """候选 AST 的表名是否与从原文提取的表名一致。

    只去反引号 —— **不再剥单引号**：STRING 表名已在定位阶段被拒绝，
    此处若继续归一化单引号，等于把被拒的形态又放回来（第五轮 BLOCK-E2）。
    """
    if not expected:
        return False
    schema = node.this
    tbl = schema.this if isinstance(schema, exp.Schema) else schema
    name = (getattr(tbl, "name", "") or "") if tbl is not None else ""
    return bool(name) and name.strip("` ").lower() == expected.strip("` ").lower()


def _blank_spans(sql: str, spans):
    """把给定 span 等长置空（保留换行），返回新串；越界返回 None。"""
    if not spans:
        return sql
    buf = list(sql)
    for s, e in spans:
        if not (0 <= s <= e < len(buf)):
            return None
        for q in range(s, e + 1):
            if buf[q] != "\n":
                buf[q] = " "
    return "".join(buf)


# 分区保真门禁用：候选 AST 中代表二级分区的 properties 节点名前缀


# ── 候选 AST 结构守恒门禁（第十一轮 BLOCK-11-05）─────────────────────────────
#
# Rev.L 的门禁只比较列名与类型字符串，索引一律折叠成 `(IDX, None, None)`。
# 白盒反向鉴别证明：丢掉 `NOT NULL DEFAULT 7`、把 `UNIQUE u(id)` 换成 `KEY v(x)`、
# 换成 `PRIMARY KEY(x)`，门禁**全部返回 True**。本版逐字段比较。
#
# 被批准忽略的差异（各有具名理由，必须逐条列出）：
_GATE_IGNORED_COL_CONSTRAINTS = (
    "COMMENT",            # 列注释：sqlglot 保留但本门禁不比较文本内容
    "COLUMN_FORMAT",      # 官方列属性，已作辅助掩码剥离（sqlglot 不认）
    "ENGINE_ATTRIBUTE",   # 同上
)
_GATE_IGNORED_INDEX_OPTS = (
    "COMMENT",            # UNIQUE/PRIMARY 的注释正是本次掩码目标
)


def _canonical_default_from_sql(text, dialect="mysql"):
    """把候选 AST 回生成的 `DEFAULT <值>` / `ON UPDATE <值>` 送进**同一个**
    `_consume_default_value()`，保证两侧规范形一致（第十一轮 BLOCK-11-05）。"""
    body = (text or "").strip()
    for lead in ("DEFAULT", "ON UPDATE"):
        if body.upper().startswith(lead):
            body = body[len(lead):].strip()
            break
    try:
        toks = sqlglot.Dialect.get_or_raise(dialect).tokenizer_class().tokenize(body)
    except Exception:
        return None
    j, val = _consume_default_value(toks, 0, len(toks))
    return val if j == len(toks) else None


def _ast_column_shape(col):
    """从候选 AST 的列定义提取可比结构；无法提取返回 None。"""
    kind = col.args.get("kind")
    if kind is None:
        return None
    shape = _canonical_type_from_sql(kind.sql(dialect="mysql"))
    if shape is None:
        return None
    cons = []
    for c in (col.args.get("constraints") or []):
        k = c.args.get("kind")
        nm = type(k).__name__ if k is not None else ""
        if nm == "NotNullColumnConstraint":
            cons.append(("NULLABILITY", "NULL" if k.args.get("allow_null") else "NOTNULL"))
        elif nm == "DefaultColumnConstraint":
            cons.append(("DEFAULT", _canonical_default_from_sql(k.sql(dialect="mysql"))))
        elif nm == "AutoIncrementColumnConstraint":
            cons.append(("AUTO_INCREMENT", None))
        elif nm == "CollateColumnConstraint":
            cons.append(("COLLATE", (k.sql(dialect="mysql") or "").split()[-1].strip("`\"' ").lower()))
        elif nm == "CharacterSetColumnConstraint":
            cons.append(("CHARACTER_SET", (k.sql(dialect="mysql") or "").split()[-1].strip("`\"' ").lower()))
        elif nm in ("PrimaryKeyColumnConstraint", "UniqueColumnConstraint"):
            cons.append(("KEYNESS", "PRIMARY" if nm.startswith("Primary") else "UNIQUE"))
        elif nm == "OnUpdateColumnConstraint":
            cons.append(("ON_UPDATE", _canonical_default_from_sql(k.sql(dialect="mysql"))))
        elif nm == "CommentColumnConstraint":
            cons.append(("COMMENT", None))
    return (col.name or "").strip("` ").lower(), shape, tuple(cons)


def _ast_index_using(node):
    """判定候选 AST 的索引节点是否携带 `USING`。

    sqlglot 30.14.0 实测：同一个 `USING BTREE` 依索引种类与书写位置落在**三个
    不同的 arg** 上，只读 `index_type` 会把 `PRIMARY KEY (id) USING BTREE`
    误判为“无 USING”，从而把本应恢复的语句挡在门外（第十一轮 P 组实测）：

      · `index_type=str`                              —— UNIQUE 的任意位置；
                                                         KEY 的前置 USING
      · `options=[IndexConstraintOption(using=...)]`  —— KEY 的后置 USING
      · `include=IndexParameters(using=...)`          —— PRIMARY KEY 的后置 USING

    三处任一命中即认定存在 USING。options 逐项按 arg 名判定而非按节点类名判定，
    因为 `IndexConstraintOption` 同时承载 comment / key_block_size 等其他选项。
    """
    it = node.args.get("index_type")
    if isinstance(it, str) and it:
        return True
    for o in (node.args.get("options") or []):
        if getattr(o, "args", None) and o.args.get("using") is not None:
            return True
    inc = node.args.get("include")
    if inc is not None and getattr(inc, "args", None) and inc.args.get("using") is not None:
        return True
    return False


def _ast_index_shape(node):
    """从候选 AST 的索引定义提取 (kind, 名称, key_parts, 选项)；无法提取返回 None。"""
    nm = type(node).__name__
    if nm == "PrimaryKey":
        kind, iname = "PRIMARY", ""
        exprs = node.args.get("expressions") or []
    elif nm == "UniqueColumnConstraint":
        kind = "UNIQUE"
        sch = node.args.get("this")
        iname = ""
        exprs = []
        if sch is not None:
            t = sch.args.get("this") if hasattr(sch, "args") else None
            iname = (getattr(t, "name", "") or "") if t is not None else ""
            exprs = sch.args.get("expressions") or []
    elif nm == "IndexColumnConstraint":
        k = node.args.get("kind")
        kind = (str(k).upper() if k else "NORMAL")
        iname = (getattr(node.args.get("this"), "name", "") or "")
        exprs = node.args.get("expressions") or []
    else:
        return None
    parts = []
    for e in exprs:
        txt = (e.sql(dialect="mysql") or "").strip()
        base = txt.strip("`")
        plen = None
        if "(" in txt and txt.endswith(")"):
            head, num = txt[:txt.rindex("(")], txt[txt.rindex("(") + 1:-1].strip()
            if num.isdigit():
                base, plen = head.strip().strip("`"), int(num)
        parts.append((base.strip("` ").lower(), plen))
    opts = ("USING",) if _ast_index_using(node) else ()
    return kind, (iname or "").strip("` ").lower(), tuple(parts), opts


def _validate_recovery_candidate(node, plan):
    """候选 AST 结构守恒门禁：逐字段比较，不再是布尔检查。"""
    if not isinstance(node, exp.Create):
        return False
    if str(node.args.get("kind") or "").upper() != "TABLE":
        return False
    if not _same_table_name(node, plan["table"]):
        return False
    schema = node.this
    if not isinstance(schema, exp.Schema):
        return False
    items = list(schema.expressions or [])
    src_defs = plan["fingerprint"]["definitions"]
    if len(items) != len(src_defs):
        return False
    for it, src in zip(items, src_defs):
        tag = src[0]
        if tag == "col":
            if not isinstance(it, exp.ColumnDef):
                return False
            got = _ast_column_shape(it)
            if got is None:
                return False
            _, s_name, s_type, s_cons = src
            g_name, g_type, g_cons = got
            if g_name != s_name or g_type != s_type:
                return False
            def _norm(cs):
                return tuple(sorted((k, v) for k, v in cs
                                    if k not in _GATE_IGNORED_COL_CONSTRAINTS))
            if _norm(s_cons) != _norm(g_cons):
                return False                           # 列约束守恒
        else:
            if isinstance(it, exp.ColumnDef):
                return False
            got = _ast_index_shape(it)
            if got is None:
                return False
            off = 1 if tag == "constraint" else 0
            s_kind, s_name, s_parts, s_opts = src[1 + off], src[2 + off], src[3 + off], src[4 + off]
            g_kind, g_name, g_parts, g_opts = got
            if g_kind != s_kind:
                return False                           # 索引 kind 守恒
            if s_kind != "PRIMARY" and g_name != s_name:
                return False                           # 索引名守恒
            if tuple((p[0], p[1]) for p in s_parts) != g_parts:
                return False                           # 键列与前缀长度守恒
            if tuple(o for o in s_opts if o not in _GATE_IGNORED_INDEX_OPTS) != g_opts:
                return False                           # USING 守恒
    if plan["had_partition"]:
        props = node.args.get("properties")
        names = [type(p).__name__ for p in (props.expressions if props else [])]
        if sum(1 for nm in names if nm.startswith("PartitionBy")) != 1:
            return False
    return True
```

**与被删正则的本质区别**：定义体（列、索引、注释、DEFAULT）在**位置上**就不在扫描范围内
——`_tdsql_table_def_bounds()` 先定位定义列表收尾右括号，扫描**从它之后**才开始。
因此名为 `broadcast` 的列、注释里的伪方言片段**结构上不可达**，不可能被误改。

### 3.1 改动点 1：新增词法安全、作用域受限的剥离器（模块级）

**位置**：`backend/engine/parser/parser_legacy.py`，紧接 §3.0c 的方言剥离器之后、
`@dataclass class ParsedSQL` 之前（`_TDSQL_DIALECT_RE` 已按 §3.0b 删除，不再作为锚点）。

```python
# （Rev.I：本函数已并入 §3.0c 的统一规划器，见上方 _plan_recovery / _scan_definition_list）
```

**Rev.C 相对 Rev.B 的三处关键变化**（对应 O 第二轮 BLOCK-B2a/B2b、MAJOR-B1）：

| 变化 | 作用 |
|---|---|
| 入口改为 `CREATE [TEMPORARY] TABLE` | 纳入既有产品域（`is_temporary_table` / R024 / R032） |
| 新增 `at_def_start` 状态 | 只有"定义列表左括号之后"或"深度 1 逗号之后"的第一个真实 token 才算定义项起点——`CONSTRAINT x UNIQUE`、列内联 `UNIQUE`、定义项中部 `UNIQUE` 全部**不再**进入 |
| 从定义列表左括号开始扫描、深度归零 `break` | 第一个定义列表闭合后**立即停止**，表选项、分区定义、第二条语句一律不扫 |

**满足 O BLOCK-1 九项要求的对应关系**：

| O 的要求 | Rev.B 如何满足 |
|---|---|
| ① 维护引号/注释等词法状态 | **由 sqlglot 词法器提供**，字符串/标识符/注释各是一个 token |
| ② 正确处理 `''`、`\'`、`\\`、``` `` ``` 转义 | 同上，词法器负责；实测 4 类转义全部通过 |
| ③ 只进入顶层 `CREATE TABLE (...)` 定义列表 | 首两个 token 必须是 `CREATE`+`TABLE`；只在 `depth == 1` 识别 |
| ④ 只处理定义项开头的真实 `UNIQUE [KEY\|INDEX]` token | 在 `depth == 1` 上按 token 类型判定，非文本匹配 |
| ⑤ 按 **TDSQL 官方 `key_part`** 逐项消费键值列表：`col [(length)] [ASC|DESC]`，逗号只能在两个完整 key-part 之间 | `_consume_index_key_parts()`；**函数 / 表达式索引失败关闭**（旧口径“支持嵌套函数”已被第七轮 BLOCK-G1 推翻）；`ASC/DESC` 作可掩码 span |
| ⑥ **只在整个索引定义被完整消费之后**才移除 `COMMENT '...'` | 键值列表逐 key-part 消费（`_consume_index_key_parts()`）；选项区只接受 `USING BTREE` 与 `COMMENT STRING` 两种完整 atom，**其余一律失败关闭**（不是"保留"，是"整体放弃"）；只在 `COMMENT`+`STRING` token 对上记 span |
| ⑦ 支持一个语句内多个 UNIQUE 索引 | 循环 `continue`；实测双 UNIQUE 记 2 处 span |
| ⑧ 无法证明边界时返回 `None`，不猜测性改写 | 词法异常 / 括号未闭合 / 非建表 / 无 span / span 越界 均返回 `(None, [], "")` |
| ⑨ 等长空格替换并保留换行 | 逐字符置空格、跳过 `\n`；实测改写前后**长度恒等** |

### 3.2b 改动点 2b：**改造既有首次解析的 `Command` 重试**（BLOCK-C1 第 4 条）

**改动前**（当前第 135-142 行，v1.6.2.0 原样）：

```python
            if isinstance(ast, exp.Command) and _TDSQL_DIALECT_RE.search(sql_clean):
                try:
                    _retry_ast = sqlglot.parse_one(
                        _TDSQL_DIALECT_RE.sub(" ", sql_clean), read=self.dialect)
                    if not isinstance(_retry_ast, exp.Command):
                        ast = _retry_ast
                except Exception:
                    pass
```

**改动后**（逐字照抄）：

```python
            if isinstance(ast, exp.Command):
                # v1.6.2.2 / BLOCK-C1+D1+D2: 原实现对整条 SQL 做
                # _TDSQL_DIALECT_RE.sub()，不感知 token 作用域，会删掉名为
                # broadcast 的列、篡改注释里的片段，且改坏后仍能解析成同表名
                # Create，形成静默错误 AST。改用严格的 token 级尾子句剥离器，
                # 并要求候选必须是同表名的 CREATE TABLE（不接纳 Block 等节点）。
                # Rev.I：改用统一规划器——一次性按 TDSQL 官方语法验证**整条语句**
                # （定义列表 + 表尾），再决定是否改写。
                # Rev.J：规划器返回 None 即"无法证明整条语句合规"或"无主目标"，
                # 一律不恢复（第九轮 BLOCK-X3）。
                _plan2 = _plan_recovery(sql_clean, self.dialect)
                if _plan2 is not None:
                    _all2 = _plan2["primary_spans"] + _plan2["auxiliary_spans"]
                    _t_sql = _blank_spans(sql_clean, _all2)
                    if (_t_sql is not None
                            and _spans_only_diff(sql_clean, _t_sql, _all2)):
                        try:
                            _retry_ast = sqlglot.parse_one(_t_sql, read=self.dialect)
                        except Exception:
                            _retry_ast = None
                        if _validate_recovery_candidate(_retry_ast, _plan2):
                            ast = _retry_ast
```

> **必须同时改这里，不能只改 except 分支。** O 指出：只修新路径会留下
> "无 UNIQUE COMMENT 时仍静默损坏"的同源问题——而那正是**当前生产版本正在发生的事**。
> 实测：改造后，三个反例在**首次重试路径**上同样恢复正确（列名与注释逐字保持）。

### 3.2 改动点 2：`parse()` 的 `except` 分支——受限重试 + 四道门禁

**改动前**（当前第 144-155 行，逐字现状）：

```python
        except (SqlglotError, Exception) as e:
            parsed.parse_error = str(e)
            parsed.sql_type = self._detect_sql_type_regex(sql_clean)
            # 正则回退提取表名（防止含中划线等语法不合规表名在解析报错时漏检）
            tbl_match = re.search(r'\b(?:create\s+table|alter\s+table|drop\s+table|truncate\s+table|from|into|update)\s+(?:if\s+(?:not\s+)?exists\s+)?([`\'"]?[a-zA-Z0-9_\-]+[`\'"]?)', sql_clean, re.IGNORECASE)
            if tbl_match:
                tb_name = tbl_match.group(1).strip("`\"' ")
                if tb_name and tb_name.lower() not in ("table", "if", "exists"):
                    parsed.tables.append(tb_name)
                    if "create table" in sql_clean.lower():
                        parsed.is_create_table = True
            return parsed
```

**改动后**（逐字照抄）：

```python
        except (SqlglotError, Exception) as e:
            # v1.6.2.2 / DEF-2: UNIQUE 索引带 COMMENT 会让 sqlglot 抛 ParseError，
            # 整条语句结构信息全丢，R003/R004/R005/R028 集体误报。
            # 恢复链共两阶段，**两阶段都是 token 级剥离并各自返回 span**：
            #   阶段一：剥离 UNIQUE 索引 COMMENT
            #   阶段二：若仍降级为 Command，再剥离 TDSQL 方言尾子句
            # 最终以「原文 → 最终 SQL 的全部差异必须落在两阶段 span 并集内」
            # 作联合门禁（BLOCK-C1 要求）；任一环节不满足即沿用原异常，
            # 下方失败路径与改前逐字一致。
            # Rev.I：单一规划器取代 Rev.H 的两阶段串联。
            # 第八轮 BLOCK-H1：Rev.H 的 UNIQUE 单独恢复路径**根本不验证表尾**，
            # 于是 ENGINE=123 / 孤立 DEFAULT / PARTITION BY RANGE(,) 这些与目标
            # 无关的非法结构被 sqlglot 静默丢弃后仍返回 Create，原 E999 消失。
            # 现在无论走哪条路径，都必须先让 _plan_recovery() 按 TDSQL 官方语法
            # 验证整条语句，再由 _validate_recovery_candidate() 校验候选 AST
            # 未丢结构。三类 span（UNIQUE COMMENT / 方言声明 / 官方语法掩码）
            # 一次性置空，联合做逐字符 span 门禁。
            _retry_ast = None
            _plan = _plan_recovery(sql_clean, self.dialect)
            if _plan is not None:
                _all_spans = _plan["primary_spans"] + _plan["auxiliary_spans"]
                _final_sql = _blank_spans(sql_clean, _all_spans)
                if (_final_sql is not None
                        and _spans_only_diff(sql_clean, _final_sql, _all_spans)):
                    try:
                        _cand = sqlglot.parse_one(_final_sql, read=self.dialect)
                    except Exception:
                        _cand = None
                    if _validate_recovery_candidate(_cand, _plan):
                        _retry_ast = _cand
            if _retry_ast is not None:
                # 必须同时重绑局部变量 ast——下方通用流程（_get_sql_type/_parse_create/
                # _parse_common）直接引用 ast，只赋 parsed.ast 会 UnboundLocalError。
                ast = _retry_ast
                parsed.ast = ast
            else:
                parsed.parse_error = str(e)
                parsed.sql_type = self._detect_sql_type_regex(sql_clean)
                # 正则回退提取表名（防止含中划线等语法不合规表名在解析报错时漏检）
                tbl_match = re.search(r'\b(?:create\s+table|alter\s+table|drop\s+table|truncate\s+table|from|into|update)\s+(?:if\s+(?:not\s+)?exists\s+)?([`\'"]?[a-zA-Z0-9_\-]+[`\'"]?)', sql_clean, re.IGNORECASE)
                if tbl_match:
                    tb_name = tbl_match.group(1).strip("`\"' ")
                    if tb_name and tb_name.lower() not in ("table", "if", "exists"):
                        parsed.tables.append(tb_name)
                        if "create table" in sql_clean.lower():
                            parsed.is_create_table = True
                return parsed
```

**四道门禁与 O BLOCK-2 七项要求的对应**：

| O 的要求 | Rev.B 如何满足 |
|---|---|
| ① 首个真实语句 token 必须是 `CREATE TABLE` | 在剥离器内校验 `toks[0]/toks[1]`，否则返回 `None` |
| ② 预处理器必须明确返回"发生过至少一次批准变换" | 返回 `spans`；`if _new_sql is not None and _spans` |
| ③ 候选必须是 `exp.Create` 且 `kind` 为 TABLE | `isinstance(_cand, exp.Create) and kind == "TABLE"` |
| **③b（BLOCK-B1/C1/D1/D2）** | **候选若降级为 `exp.Command`，调用 `_plan_recovery()` 再恢复一次**，并把其 span 并入联合门禁。🚫 **不得**使用任何全局正则替换 |
| ④ 候选表名必须与从原 SQL 安全提取的表名一致 | 剥离器从 token 流取表名，与候选 AST 表名不区分大小写比对 |
| ⑤ 验证差异只出现在批准 span | **门禁①**：等长 + 逐字符校验 |
| ⑥ 任一条件不满足 → 沿用原异常与 E999 路径 | `_retry_ast` 保持 `None` → 走 `else` 分支（与改前逐字一致） |
| ⑦ `parsed.raw_sql` 保持原始输入 | 第 119 行 `ParsedSQL(raw_sql=sql.strip())` 未动；变换只作用于局部副本 |

> 🚨 **施工陷阱（Rev.A 原型阶段真踩到过，必须注意）**
> 重试成功后**必须同时重绑局部变量 `ast`**，不能只赋 `parsed.ast`。
> `except` 之后的通用流程（`self._get_sql_type(ast)`、`_parse_create(ast, parsed)`、
> `_parse_common(ast, parsed)`）**直接引用局部变量 `ast`**，而它在抛错时从未被赋值。
> 只写 `parsed.ast = _retry_ast` 会得到
> `UnboundLocalError: cannot access local variable 'ast'`，
> 且只有跑到含 UNIQUE-COMMENT 的语句才会炸，单测不覆盖就会漏。

### 3.3 改动点 3：索引类型判据（DEF-1）

**改动前**（当前第 581-588 行，逐字现状）：

```python
        # 判断索引类型
        def_str = str(col_def).upper()
        if "PRIMARY" in def_str:
            idx_type = "PRIMARY"
        elif "UNIQUE" in def_str:
            idx_type = "UNIQUE"
        elif "FULLTEXT" in def_str:
            idx_type = "FULLTEXT"
```

**改动后**（逐字照抄，已采纳 O 的 MAJOR-1 白名单映射）：

```python
        # 判断索引类型
        # v1.6.2.2 / DEF-1: 原实现 `def_str = str(col_def).upper()` + 裸子串包含判断，
        # 会把列名/索引名中含 unique/primary/fulltext 的普通索引误判（实测：列名
        # list_unique_num → 该普通索引被标成 UNIQUE），进而 R054 对普通索引误报，
        # 且真唯一索引被顶替而漏检。改读 sqlglot 的结构化 kind 参数。
        # 实测 sqlglot 26.0/30.12/30.14：IndexColumnConstraint 只承载
        # kind ∈ {None,'FULLTEXT','SPATIAL'}，UNIQUE 走 UniqueColumnConstraint、
        # PRIMARY 走 exp.PrimaryKey，都不经过本函数。此处仍用白名单精确映射而非
        # 二元判断：万一未来 sqlglot 把 PRIMARY/UNIQUE 放进本节点，也不会静默
        # 降级成 NORMAL（配套 AST 契约测试在升级时显式失败）。
        # SPATIAL 维持映射为 NORMAL：这是本次热修"输出域不变"的兼容性取舍，
        # 不是"空间索引在语义上等同普通索引"的结论。
        kind = (col_def.args.get("kind") or "").upper()
        idx_type = kind if kind in {"PRIMARY", "UNIQUE", "FULLTEXT"} else "NORMAL"
```

> ✅ **本文档的代码块已自验证（Rev.H）**：§3.2b / §3.2 / §3.3 的三个「改动前」块经程序比对与
> `parser_legacy.py` **逐字匹配**；「改动后」块被**原样抽取**并施工到一棵干净工作树上，实测：
> 语法通过、导入自检通过、**H 组用例（数量见 §7.1a）全通过**、**W 组 28 例全通过**、**Z 组 22 例全通过**、
> **Y 组 20 例全通过**、**X 组 40 例全通过**、T/N/C/F 与 6000 条模糊测试逐项相同、
> 专项见 §7.1 manifest 生成表、全量回归 **0 failed**（本环境 1355 passed / 29 skipped，不作门槛）、
> **上述矩阵在 sqlglot 29.0.0 与 30.14.0 上逐条一致**、
> `grep _tdsql_table_def_bounds` 确认统一规划器共用同一定位器。Q 可以直接复制粘贴。
>
> 🆕 **本版起自验证还增加「反例期望值必须来自主干实测」检查**（第七轮教训）：
> 反例断言一律走 rank 判据，禁止手写期望值——否则会被主干自身的缺陷带偏。
>
> 🆕 **自验证的「代码块无重复片段」检查**（MINOR-F1 教训）：
> 逐块比对相邻行窗口，并断言 `except` 分支内 `return parsed` 恰好出现 **1 次**、
> 每个新增函数在文件中**只定义一次**。仅验证"行为正确"是不够的——
> 重复的不可达代码同样能编译、同样能通过全部测试。
>
> ⚠️ 抽取时注意块的先后：§3.2b、§3.2、§3.3 均为**前者「改动前」、后者「改动后」**，
> 且 §3.3 两块开头都是 `# 判断索引类型`，容易搞反。

### 3.4 改动汇总

| 序号 | 位置 | 改动 |
|---|---|---|
| 0 | 文件头 import 区 | `from sqlglot.tokens import TokenType` |
| **0b** | 原 `_TDSQL_DIALECT_RE` 处 | **删除**该全局正则及其注释 |
| **0c** | 同上位置 | 新增全部模块级恢复链代码（结构化类型表 `_TYPE_RULES`、typed atoms + capability profile、可执行注释验证、结构化指纹与守恒门禁）。函数逐个清单见下方自动生成表 |
| **2b** | `parse()` 首次 `Command` 重试 | 改用 token 剥离器 + span 校验（v1.6.2.0 代码，NG-4 已撤销） |
| 2 | `parse()` 的 `except` 分支 | 两阶段受限重试 + **联合 span 门禁** |
| 3 | `_parse_index_constraint()` | 类型判据改读 `kind` 白名单映射 |

**产品代码：`parser_legacy.py` 一个文件。fixture 已在 Rev.C 修正。
不新增第三方依赖（`TokenType` 来自已在用的 sqlglot），规则层一行不动。**

> **规模数字与函数清单一律由 `tests/codestat.py` 从最终补丁生成，不得人工维护**
> （第十一轮 MINOR-11-02）。复现命令：
>
> ```bash
> python tests/codestat.py <基线 parser_legacy.py> backend/engine/parser/parser_legacy.py
> ```

<!-- 本节由 tests/codestat.py 生成，请勿手改 -->

**`backend/engine/parser/parser_legacy.py` 规模（自动生成）**

| 项 | 基线 | Rev.M | 变化 |
|---|---:|---:|---:|
| 文件行数 | 849 | 2318 | +1469 |
| 模块级函数/类 | 2 | 39 | +37 |
| 模块级常量 | 1 | 25 | +24 |
| diff 行 | —— | —— | +1509 / -40 |

**新增函数（37 个）**

| 函数 | 起始行 | 行数 |
|---|---:|---:|
| `_spans_only_diff` | 43 | 8 |
| `_is_bare_kw` | 66 | 8 |
| `_tdsql_table_def_bounds` | 76 | 44 |
| `_int_val` | 216 | 9 |
| `_in_range` | 227 | 3 |
| `_consume_data_type` | 232 | 87 |
| `_canonical_type_from_sql` | 321 | 12 |
| `_consume_default_value` | 350 | 35 |
| `_consume_column_constraints` | 387 | 71 |
| `_consume_column_definition` | 460 | 16 |
| `_index_lead` | 483 | 25 |
| `_consume_index_definition` | 510 | 54 |
| `_consume_index_key_parts` | 566 | 34 |
| `_consume_ident` | 602 | 6 |
| `_consume_ident_list` | 610 | 16 |
| `_consume_partition_expr` | 637 | 24 |
| `_consume_value_list` | 663 | 25 |
| `_consume_partition_values` | 690 | 23 |
| `_consume_partition_options` | 715 | 41 |
| `_consume_partition_defs` | 758 | 30 |
| `_consume_secondary_partition` | 790 | 16 |
| `_consume_table_option` | 830 | 57 |
| `_match_tail_profile` | 941 | 22 |
| `_consume_shardkey_value` | 965 | 32 |
| `_scan_table_tail` | 999 | 83 |
| `_collect_executable_comments` | 1096 | 14 |
| `_validate_executable_comments` | 1112 | 22 |
| `_scan_definition_list` | 1136 | 39 |
| `_strip_terminal_semicolon` | 1177 | 9 |
| `_plan_recovery` | 1188 | 40 |
| `_same_table_name` | 1230 | 12 |
| `_blank_spans` | 1244 | 12 |
| `_canonical_default_from_sql` | 1278 | 14 |
| `_ast_column_shape` | 1294 | 29 |
| `_ast_index_using` | 1325 | 25 |
| `_ast_index_shape` | 1352 | 34 |
| `_validate_recovery_candidate` | 1388 | 55 |

**删除函数（0 个）**：无

**行数发生变化的既有函数（1 个）**

| 函数 | 基线行数 | Rev.M 行数 |
|---|---:|---:|
| `class SQLParser` | 744 | 799 |

**唯一性检查**

| 检查 | 结果 |
|---|---|
| 模块级函数重复定义 | ✅ 无 |
| 模块级常量重复定义 | ✅ 无 |
| 语法可解析 | ✅ |

> 本版改动量明显大于 Rev.C——因为 NG-4 被撤销，v1.6.2.0 的方言处理被纳入修复范围，
> 且第八~十一轮把「按 TDSQL 官方语法逐 token 验证整条语句」纳入了恢复前置条件。
> 这是必要的：那段代码**正在生产环境静默破坏审核数据**（§5.14.1）。
> 爆炸半径的实测边界见 §7.3 门槛 G-7/G-8：**全语料 201 条语句零漂移，
> 仅两个目标 fixture 按预期变化。**

## 4. 明确的非目标（NG，施工红线）

| 编号 | 非目标 | 说明 |
|---|---|---|
| **NG-0** | **不再使用任何跨语义边界的正则做 SQL 改写** | Rev.A 的 `_UNIQUE_IDX_COMMENT_RE` 整体删除，不得以「再补几个分支」的方式保留 |
| **NG-1** | **不改任何规则文件** | `ddl.py` / `index.py` / `distributed.py` / `dml.py` / `oracle_compat.py` **零改动**。本次是解析器供数问题，不是规则判据问题 |
| **NG-2** | **不动 `distributed.py`** | v1.6.1.9 冻结代码；`_iter_unique_indexes` 的早退逻辑本次不碰——DEF-1 修好后它拿到的就是正确输入 |
| **NG-3** | **不动 `_parse_unique_constraint()`** | 它硬编码 `"type": "UNIQUE"`，本就正确 |
| ~~NG-4~~ | ~~不动 v1.6.2.0 的 TDSQL 方言重试~~ | 🚫 **本版撤销**。O 第三轮证明该正则会静默破坏 AST，且我正把更多语句引流进去；继续绕开它等于把已知损坏留在生产。Rev.D **删除该正则**并把两条恢复入口统一到 token 级剥离器 |
| **NG-5** | **不动 v1.6.2.1 的 R061 去引号** | `index.py` 一字不改 |
| **NG-6** | **不把 SPATIAL 单独成型** | 维持映射为 NORMAL。这是本次热修「输出域不变」的**兼容性取舍**，**不是**「空间索引语义上等同普通索引」的结论；后续如新增空间索引规则，另行立项扩展模型与消费者（O 复审同意） |
| **NG-7** | **不新增字段级字符集/排序规则检查** | 用户已决策：R005 维持只判表级，字段级字符集本次不纳入 |
| **NG-8** | **不在 `except` 补调 `_regex_fallback_create_table_props()`** | 见 §2.2 方案 B，登记 ADJ-10 |
| **NG-9** | **不修 E999 文案** | 现文案"可能是拉取截断/语法错误"对合法 MySQL 有误导，但属独立体验问题，登记 ADJ-12 |
| **NG-10** | **不支持 `CONSTRAINT x UNIQUE (col)` 形态** | 既有缺陷（ADJ-11）。Rev.C 已用 `at_def_start` **显式排除**该形态——其定义项起点是 `CONSTRAINT` 而非 `UNIQUE`，故不会被纳入 span；这是**设计上的明确排除**，不是偶然不命中。若将来决定支持，必须显式建模并同步删除本条 |

---

## 5. 影响面分析（全部实测）

### 5.0 sqlglot 版本矩阵与依赖 pin（O MAJOR-C1）

**实测版本矩阵**（在独立 venv 中逐版本安装后跑同一组探针）：

| sqlglot | T1~T4/T6（HASH/RANGE/LIST/BROADCAST/shardkey + UNIQUE COMMENT） | T5（HASH + 二级分区） | BLOCK-C1 三反例（列/注释保持） |
|---|---|---|---|
| 26.0.0 | ✅ | ❌ **失败** | ✅ |
| 27.0.0 | ✅ | ❌ **失败** | ✅ |
| 28.0.0 | ✅ | ❌ **失败** | ✅ |
| **29.0.0** | ✅ | ✅ **起可用** | ✅ |
| 30.0.0 | ✅ | ✅ | ✅ |
| 30.12.0（O 侧） | ✅ | ✅ | ✅ |
| 30.14.0（本文档回归版本） | ✅ | ✅ | ✅ |

**两条结论**：

1. **本次 BLOCK-C1 修复本身与版本无关**——26 / 27 / 28 / 29 / 30 上列名与注释均正确保持。
2. **T5 的真实下界是 29.0.0**（26/27/28 实测失败）。这是 **v1.6.2.0 既有的**版本兼容边界
   （仓内既有 `test_d5_hash_plus_partition` 在 26.0.0 下同样失败），不是 Rev.D 引入。

**当前依赖声明与实际安装的脱节**：

| 位置 | 现状 |
|---|---|
| `requirements.txt` | `sqlglot>=26.0.0`（无上限） |
| `pyproject.toml` | `sqlglot>=26.0`（无上限） |
| 内网部署 | `pip install --no-index --find-links=wheels/ -r requirements.txt`，**实际版本 = 打包时 `make_release.sh` 抓到的 wheel**，未固定 |

**Rev.E 决定（MAJOR-D1 闭环）**：把两处依赖声明改为 **`sqlglot==30.14.0`**（Rev.I 起；Rev.E~H 曾为 `sqlglot>=29,<31`）。

| 依据 | 内容 |
|---|---|
| 下界 29.0.0 | **实测得出**：26/27/28 的 T5 失败、29.0.0 起通过；O 独立复测结论一致 |
| 上界 `<31` | 不把未验证的大版本纳入 |
| 本次回归版本 | 30.14.0（O 侧另覆盖 30.12.0） |
| 发布要求 | 离线包只携带一个通过完整验收的确定 wheel，并在发布说明记录准确版本 |

> 这条改的是发布包而不只是代码（`requirements.txt` / `pyproject.toml` 各 1 行）。
> 我之所以不再挂"待拍板"：下界是实测的、两名评审结论一致、改动一行、且继续保留
> `>=26` 就等于**在文档里宣称 T5 已解决、实际却允许装上一个 T5 不成立的版本**。
> **如果你不同意这个 pin，告诉我，我改回去。**

### 5.1 引擎指纹与解析产物

| 指标 | 基线 | Rev.B |
|---|---|---|
| 规则总数 | 119 | **119** |
| 全语料解析失败语句数 | 14 | **13**（恢复的正是 gg78） |
| 全语料索引 `type` 分布 | `{'NORMAL': 59, 'UNIQUE': 1}` | **`{'NORMAL': 61}`** |

> `type` 分布变化**逐个可account**：`-1 UNIQUE` 是被消除的假 UNIQUE（gg77 的 idx13）；
> `+2 NORMAL` = 该假 UNIQUE 归位为 NORMAL（+1）+ gg78 恢复解析后新可见的 `idx_term_bizlog`（+1）。
> 59+1+1 = 61，**无任何无法解释的增减**。

### 5.2 生产 14 表回放（v1.6.2.1 已稳定，要求零漂移）

**漂移表数 = 0。** 14 张表命中规则集合逐表逐条相同。✅

### 5.3 全语料 × 全规则漂移

197 条语句 × 119 条规则（键集完全相同），**恰好 2 条变化，且都是本次的目标缺陷**：

| 语料 | 变化 |
|---|---|
| `tests/fixtures/report_6309_kcfb_list_info.sql` | **−R054**，无任何新增 |
| `tests/fixtures/report_6311_biz_tx_log.sql` | **−E999、−R003、−R004、−R005、−R028**；**+R036、+R037**（原被解析失败掩盖的真实建议）；解析错误 `True → False` |

**除这两条外，其余 195 条零变化。**

### 5.4 产品边界：sqlglot 自身不支持的四类语法（O 两轮共同确认）

以下**四类**（O 第二轮补入第 4 类）**去掉 COMMENT 后 sqlglot 依然 ParseError**，说明不是剥离器的问题，而是解析器能力边界。
Rev.B 对它们**失败关闭**，仍报原错误——这是正确行为，并在此显式声明为产品边界：

| 语法 | 去掉 COMMENT 后 sqlglot | Rev.B 行为 |
|---|---|---|
| 函数键值 `UNIQUE KEY uk ((lower(a)))` | ❌ 不支持 | 仍报原错误 ✅ |
| `VISIBLE` / `INVISIBLE` 索引选项 | ❌ 不支持 | 仍报原错误 ✅ |
| `KEY_BLOCK_SIZE=8` 索引选项 | ❌ 不支持 | 仍报原错误 ✅ |
| **`UNIQUE KEY uk USING BTREE (a)`（index_type 前置于键值列表，MySQL 官方合法）** | ❌ 不支持 | 仍报原错误 ✅ |

> 这四类若要支持，属于**解析器能力扩展**，需独立立项（升级 sqlglot 或补方言），
> **不得**用字符串兜底伪造结构化事实（同 ADJ-10 的理由）。

### 5.5 §6.2 正向恢复矩阵（12 例，全部实测通过）

| 编号 | 用例 | 恢复 | `raw_sql` 原文 |
|---|---|---|---|
| 1 / 1b | 单个 `UNIQUE KEY` / `UNIQUE INDEX` 带 COMMENT | ✅ | ✅ |
| 2 | 多个 UNIQUE 各带 COMMENT（记 2 处 span） | ✅ | ✅ |
| 3 | 列清单与 COMMENT 间换行 | ✅ | ✅ |
| 4 | 注释含 `)`、`unique`、`COMMENT` 字样 | ✅ | ✅ |
| 5 | `''` 双单引号转义 | ✅ | ✅ |
| **6 / 6b** | **`\'` 反斜杠转义、`\\` 结尾** | ✅ **Rev.A 此项失败** | ✅ |
| **7 / 8** | **前缀键值 `a(20)`、多列前缀键值** | ✅ **Rev.A 此项失败** | ✅ |
| **10** | **转义反引号索引名**（索引名内含成对反引号转义） | ✅ **Rev.A 此项失败** | ✅ |
| 11a | `USING BTREE` 位于 COMMENT 之前 | ✅ | ✅ |

### 5.6 §6.3 负向 / 防次生灾害矩阵（全部实测通过）

伪 SQL 文本 `UNIQUE KEY fake (zz) COMMENT ''inner''` 分别放在以下位置，
断言**越界改写 = 0**（即只有真实索引注释被抹除）：

| 位置 | 变换处数 | 越界改写 | 判定 |
|---|---|---|---|
| 列 COMMENT 内 | 1（仅真实那处） | **0** | ✅ |
| 表 COMMENT 内 | 1 | **0** | ✅ |
| `DEFAULT` 字符串内 | 1 | **0** | ✅ |
| `--` 行注释内 | 1 | **0** | ✅ |
| `/* */` 块注释内 | 1 | **0** | ✅ |
| 反引号标识符内 | 1 | **0** | ✅ |

**O 的 BLOCK-1 原样反例逐字符定位**：改写后长度**恒等**，25 个差异字符**全部落在批准 span `(171,198)`** 内，
该区间原文为 `COMMENT 'real index comment'`；列 `b` 的注释源码片段**逐字未动**。

> 对照实验澄清一处易误读：该反例中 `b` 的列注释解析值为 `"mentions UNIQUE KEY fake (a) COMMENT ''nested"`，
> 但这是 **sqlglot 对 `''` 的既有反转义行为**——用一条 sqlglot 原生可解析、`b` 列注释字面量完全相同的
> 对照 SQL（不含任何 UNIQUE-COMMENT、不经任何改写）实测，得到**完全相同**的值。
> 与本次变换无关。

### 5.7 失败关闭矩阵

| 输入 | 剥离器 | 最终 |
|---|---|---|
| 未闭合单引号 | 不变换 | 仍报原错误 ✅ |
| 未闭合括号 | 不变换 | 仍报原错误 ✅ |
| 非 `CREATE TABLE`（`SELECT ... WHERE x='UNIQUE KEY ...'`） | 不变换 | 不进入重试 ✅ |
| 缺右括号的建表语句 | 有变换但重试失败 | 仍报原错误 ✅ |

> **一处需如实说明的边界**：形如 `CREATE TABLE t (a , UNIQUE KEY u (a) COMMENT 'x')`
> （列缺类型）在 Rev.B 下会重试成功、不再报 E999。
> 这**不是本次新开的口子**——实测基线上 `CREATE TABLE t (a , KEY u (a))`、
> `CREATE TABLE t (a )` 等同类语句**本来就能被 sqlglot 解析**且不报 E999。
> 本次只是让"UNIQUE+COMMENT"变体与"同一条 SQL 去掉 COMMENT"行为一致，属**消除不一致**而非放宽。

> *（§5.8 在 Rev.C 整合进 §5.7，编号保留空缺以免打乱既有引用。）*

### 5.9 TDSQL 方言组合矩阵（Rev.C 新增，BLOCK-B1）

同一条 DDL 同时含 `UNIQUE ... COMMENT` 与 TDSQL 方言尾子句：

| 编号 | 尾子句 | Rev.B | Rev.C | 与「同表去掉 COMMENT」结论一致 |
|---|---|---|---|---|
| T1 | `TDSQL_DISTRIBUTED BY HASH(sk)` | ❌ E999，cols=0 | ✅ cols=5 | ✅ |
| T2 | `... BY RANGE(sk)` | ❌ E999 | ✅ cols=5 | ✅ |
| T3 | `... BY LIST(sk)` | ❌ E999 | ✅ cols=5 | ✅ |
| T4 | `BROADCAST` | ❌ E999 | ✅ cols=5 | ✅ |
| T5 | `HASH + 二级 PARTITION` | ❌ | ✅ | ✅ |
| T6 | `shardkey=sk`（对照） | ✅ | ✅ | ✅ |
| ~~T7~~ | ~~列名为 `broadcast`~~ | 🚫 **本版撤回**：Rev.C 的 T7 尾子句写成了 `shardkey=`，**根本不触发**方言正则，是同源错误对照，不能作安全 oracle。已由 §5.14 的 40 例交叉矩阵取代 |
| ~~T8~~ | ~~列注释含伪 `TDSQL_DISTRIBUTED`~~ | 🚫 **本版撤回**，同上 |
| T9/T10 | `TEMPORARY` 集中式 / 分布式 | ❌ E999 | ✅ 且 R032 / R024+R032 正常命中 | — |

> **最强的一条不变量**：T1~T6 均实测「带 COMMENT 的表」与「同一张表去掉 COMMENT」
> 的**规则命中集合完全相同**。也就是说本次恢复**没有引入任何自己的口径**，
> 只是让这类表回到它本来就该有的审核结果。

> **一处如实说明**：T2/T3（RANGE/LIST）会命中 R077。实测**基线上同一张表去掉 COMMENT
> 后同样命中 R077**——v1.6.1.9 只把 `HASH` 认作分片键声明，RANGE/LIST 未纳入。
> 这是**既有口径**，与本次改动无关；Rev.C 只是让这类表终于能被解析，从而把它暴露出来。
> 已登记 **ADJ-13**，本次不修。

### 5.10 作用域负向矩阵（Rev.C 新增，BLOCK-B2）

以下形态**同时**含一个真实目标（`UNIQUE KEY uk (...) COMMENT 'real'`）与一个不该被处理的 UNIQUE：

| 编号 | 场景 | Rev.B span | Rev.C span | 抹除内容 |
|---|---|---|---|---|
| N1 | `CONSTRAINT uq UNIQUE (a) COMMENT 'cc'` | **2**（含 cc，违反 NG-10） | **1** ✅ | 仅 `COMMENT 'real'` |
| N2 | 列内联 `a int UNIQUE COMMENT 'inline'` | — | **1** ✅ | 仅 `COMMENT 'real'` |
| N3 | 定义项中部 `KEY k (a) UNIQUE COMMENT 'mid'` | — | **1** ✅ | 仅 `COMMENT 'real'` |
| N4 | 两条 CREATE TABLE 拼接 | **2**（含第二条） | **1** ✅ | 仅第一条的 `COMMENT 'first'` |
| N5 | 定义列表闭合后表选项内含伪 UNIQUE | — | **1** ✅ | 仅 `COMMENT 'real'` |

### 5.11 模糊测试（Rev.C 复跑）

6000 条随机组合（引号、括号、逗号、转义、`--`/`#`/`/* */` 注释、`TEMPORARY`、`CONSTRAINT`、
`TDSQL_DISTRIBUTED` 片段）：**抛异常 0**，43 条发生变换，
**违反「长度恒等 + 差异全在 span 内」0**。

### 5.12 生产回放（精确集合断言，MAJOR-B2b）

fixture 已移除会污染审核的文件头（来源说明移入 `tests/fixtures/README-report-fixtures.md`）：

| fixture | instance_type | Rev.C 实测（**精确相等**） |
|---|---|---|
| `report_6309_kcfb_list_info.sql` | **distributed** | `{R011,R018,R019,R036,R037,R061,R065,R067,R104}` ✅ |
| `report_6311_biz_tx_log.sql` | **centralized** | `{R036,R037}` ✅ |

> 修正前实测：gg78 原样读取会因我加的中文文件头（含**全角括号**）多出一条 **R104**——
> 这正是 O 指出的、子集断言无法暴露的问题。

### 5.14 BLOCK-C1：方言尾子句处理的安全性（Rev.D 新增，本轮核心）

#### 5.14.1 缺陷在当前生产版本上的实际表现

在**未打任何补丁的 v1.6.2.1**（即内网正在运行的版本）上实测：

| 输入（尾子句 `TDSQL_DISTRIBUTED BY HASH(sk)`） | 生产版本实际结果 |
|---|---|
| 有一列名为 `` `broadcast` `` | 列名变成 `' '`，**该列消失** |
| 某列注释 `'broadcast table info'` | 变成 `'  table info'` |
| 某列注释 `'TDSQL_DISTRIBUTED BY HASH(fake)'` | 变成 `' '` |

三种情况**均"解析成功"**、无 E999，产出结构已损坏的 AST。

#### 5.14.2 交叉矩阵：40 例（4 尾子句 × 5 诱饵 × 带/不带 UNIQUE COMMENT）

诱饵：列名为 `broadcast`（反引号 / 裸名）、列注释含 `broadcast`、列注释含伪 `TDSQL_DISTRIBUTED`、
`DEFAULT` 值含 `broadcast`。断言**字段级精确保持**：列名序列、目标列注释、`raw_sql` 逐字等于输入。

| 版本 | 结果 |
|---|---|
| **当前生产 v1.6.2.1** | **40 例中 36 例失败** |
| **Rev.D** | **40 例全部通过** ✅ |

> 这条矩阵是 O 要求的"**真正独立的结构 oracle**"——它不比较两个都经过同一不安全预处理的
> 规则集合，而是直接对列名、列注释、DEFAULT、`raw_sql` 做字段级精确断言。

#### 5.14.3 两条恢复入口均已统一

| 入口 | 触发条件 | Rev.D 前 | Rev.D 后 |
|---|---|---|---|
| 首次解析降级为 `Command`（v1.6.2.0） | 语句含真实方言尾子句 | 全局正则，**损坏** | token 剥离器 + span 校验 ✅ |
| 首次解析抛 `ParseError`（本次新增） | UNIQUE COMMENT + 方言尾子句 | Rev.C 复用同一不安全正则 | 同上，且与阶段一做**联合 span 门禁** ✅ |

#### 5.14.4 既有方言回退专项未退化

`tests/test_parser_tdsql_dialect_fallback.py`（v1.6.2.0 的 14 例）在 Rev.D 下 **14 passed**，
`test_r077_r054_tdsql_syntax.py` **45 passed**，`test_r061_index_name_quoting.py` **12 passed**。

### 5.15 BLOCK-D1 / D2：严格语法识别与语句边界（Rev.E 新增）

#### 5.15.1 非法方言必须失败关闭（D1a）

| 尾子句 | Rev.D | Rev.E |
|---|---|---|
| `TDSQL_DISTRIBUTED (sk)`（缺 BY） | span=1，剥离后 `cols=2` **被修成合法** | **span=0，仍报原错** ✅ |
| `TDSQL_DISTRIBUTED BY (sk)`（缺方法） | span=1，被修成合法 | **span=0** ✅ |
| `TDSQL_DISTRIBUTED HASH(sk)`（缺 BY） | span=1，被修成合法 | **span=0** ✅ |
| `TDSQL_DISTRIBUTED BY FOO(sk)`（未知方法） | span=0（本就正确） | span=0 ✅ |
| `TDSQL_DISTRIBUTED BY HASH`（缺括号） | span=0 | span=0 ✅ |

#### 5.15.2 字符串 / 标识符不得冒充关键字（D1b、D1c）

| 输入 | Rev.D | Rev.E |
|---|---|---|
| `'TDSQL_DISTRIBUTED' BY HASH(sk)` | span=1，**误当关键字** | **span=0** ✅ |
| `` `TDSQL_DISTRIBUTED` BY HASH(sk) `` | span=1 | **span=0** ✅ |
| `` `broadcast` `` | span=1 | **span=0** ✅ |
| **`COMMENT='TDSQL_DISTRIBUTED'` + 真实 `HASH(sk)`** | **span=0（真实尾子句被阻断）**，`ast=Command`、`cols=0` | **span=1，`cols=2` 正常恢复** ✅ |
| **`COMMENT='BROADCAST'` + 真实 `BROADCAST`** | 同上被阻断 | **span=1，正常恢复** ✅ |

#### 5.15.3 双声明 / 冲突声明失败关闭（D1d）

| 输入 | Rev.D | Rev.E |
|---|---|---|
| `HASH(sk) BROADCAST` | span=2，全部删除后被接纳 | **span=0** ✅ |
| `HASH(sk) TDSQL_DISTRIBUTED BY RANGE(sk)` | span=2 | **span=0** ✅ |

#### 5.15.4 定义列表与语句边界（D2）

| 输入 | Rev.D | Rev.E |
|---|---|---|
| CTAS：`CREATE TABLE t AS SELECT CONCAT('a','b') AS c, broadcast FROM src TDSQL_DISTRIBUTED BY HASH(c)` | **span=2**，删掉 SELECT 列 `broadcast` **与**真实尾子句，仍解析成 `Create` —— **CTAS 语义被静默改写** | **span=0**（表名后非左括号即拒绝） ✅ |
| `CREATE TABLE t LIKE src` | span=0 | span=0 ✅ |
| 两条语句拼接 | **span=2**，两条尾子句都被改 | **span=0**（发现分号即失败关闭） ✅ |

> **关于多语句下 `ast=Block`**：实测 `sqlglot.parse_one()` 对多语句输入**原生返回 `Block`**
> （不含任何方言语法时同样如此），这是**基线既有行为**，非本次引入。
> Rev.E 关闭的是 O 指出的两点：① 剥离器不再跨分号改写；② 首次重试门禁补齐
> `exp.Create` + `kind=='TABLE'` + 同表名，**`Block` 在第一关即被拒绝**（实测）。
> 另：`RuleChecker.audit_file()` 通过 `_split_sql_file()` 先行拆分语句，
> 多语句进入 `parse()` 属边缘路径。

#### 5.15.5 合法形态不得回归

| 形态 | Rev.E |
|---|---|
| `BY HASH(sk)` / `BY RANGE(sk)` / `BY LIST(sk)` / `BROADCAST` | 均 span=1、解析成功、`cols=2` ✅ |
| 反引号列名 `` `broadcast` `` + 真实 `HASH` 尾子句 | span=1、列 `broadcast` **完整保留** ✅ |

> ⚠️ **RANGE / LIST 是本轮的一处真实回归风险**：按 O 建议的"只认 `TokenType.VAR`"实现后，
> 二者立刻失败（实测 `RANGE`→`TokenType.RANGE`、`LIST`→`TokenType.LIST`）。
> Rev.E 改用排除法后恢复正常。**Q 施工后务必确认这三种方法都能恢复。**

### 5.16 BLOCK-E1 / E2：方法参数与表名的精确形态（Rev.F 新增）

#### 5.16.1 方法参数（BLOCK-E1）

| 尾子句 | Rev.E | Rev.F |
|---|---|---|
| `HASH()` 空参 | span=1 → `Create`，**E999 被吞** | **span=0，仍报 E999** ✅ |
| `HASH(,)` | span=1 → `Create` | **span=0** ✅ |
| `HASH('id')` 字符串 | span=1 → `Create` | **span=0** ✅ |
| `HASH(`id` + 1)` 表达式 | span=1 → `Create` | **span=0** ✅ |
| `HASH(lower(`id`))` 函数 | span=1 → `Create` | **span=0** ✅ |
| `HASH(`a`,`b`)` 多字段 | span=1 → `Create` | **span=0** ✅ |
| `HASH("id")` 双引号 | span=1 → `Create` | **span=0** ✅ |

**合法形态不得回归**（6 组，全部实测 span=1 且解析成功）：
`HASH/RANGE/LIST` ×（反引号 `` `id` `` / 裸名 `id`），外加 `BROADCAST`、`BROADCAST COMMENT='x'`。

> **主干对照**：上述 7 种非法形态在**当前主干 v1.6.2.1** 上均报 `E999_SYNTAX_ERROR`。
> Rev.E 把它们变成了"解析成功"，Rev.F 恢复为**继续报 E999**——这是 S-3 的直接体现：
> **宁可继续报错，也绝不把非法 DDL 修成合法。**

#### 5.16.2 表名 token（BLOCK-E2）

| 输入 | Rev.E | Rev.F |
|---|---|---|
| `CREATE TABLE 't' (...)` + UNIQUE COMMENT | `Create`，**E999 消失** | **仍报 E999** ✅ |
| `CREATE TABLE "t" (...)` + UNIQUE COMMENT | `Create`，E999 消失 | **仍报 E999** ✅ |
| 单引号表名 + `HASH(`id`)` | `Create`，E999 消失 | **仍报 E999** ✅ |

**合法表名形态不得回归**（4 例，全部实测 `Create` 且 `cols>0`）：
裸表名 `t`、反引号 `` `t` ``、库限定 `` `db`.`t` ``、`IF NOT EXISTS`。

#### 5.16.3 统一规划器已合并到同一严格头部定位器

`_plan_recovery()` 与 `_plan_recovery()` 现在都调用
`_tdsql_table_def_bounds()`。这条是第五轮 §5.2.4 第 3/4 点的要求，也是防止
"两套安全模型各自漂移"的结构性措施——**"什么算合法建表头部"只有一处定义**。

### 5.17 BLOCK-F1 / F2：目标所处上下文的完整性（Rev.G 新增）

#### 5.17.1 表选项白名单的实测依据

对仓内全部 `*.sql` 语料与两份生产 fixture 的**表选项区**（定义列表右括号之后）做 token 统计，
实际出现的类型只有：`VAR`(195) / `EQ`(175) / `DEFAULT`(51) / `CHARACTER_SET`(51) /
`COMMENT`(49) / `STRING`(49) / `COLLATE`(12) / `PARTITION_BY`(1) / `NUMBER`(1)。
文本形态高度规则，例如两份生产 fixture 的完整选项区分别是：

```text
ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_bin COMMENT = '…' shardkey = black_list_seq_num
ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_general_ci COMMENT = '联机交易流水表'
```

白名单据此建立，**不是臆测的语法子集**。

#### 5.17.2 残缺上下文必须失败关闭（BLOCK-F1，12 例）

3 类残缺选项（`DEFAULT` / `CHECKSUM` / `INDEX DIRECTORY`）× 2 类目标（`BROADCAST` /
`TDSQL_DISTRIBUTED BY HASH(...)`）× 2 条恢复路径：

| 路径 | Rev.F | Rev.G |
|---|---|---|
| 带 UNIQUE COMMENT（原 `ParseError`） | span=1、`Create`、**E999 消失** | **span=0、仍 `NoneType`、E999 保留** ✅ |
| 不带 UNIQUE COMMENT（原 `Command`） | span=1、`Create` | **span=0、仍 `Command`（未被升级）** ✅ |

> ⚠️ **两条路径的断言不同**（O MAJOR-F2）：不带 UNIQUE COMMENT 的输入原本就是
> `Command`、**没有 E999 可保留**，正确判据是"不得被升级成 `Create`"。
> 我第一版 W 组用例正是把两条路径混写成同一个断言，跑出 6 个红才发现。

#### 5.17.3 完整表选项正例不得误伤（8 例）

`DEFAULT CHARSET=` / `AUTO_INCREMENT=` / `COLLATE=` / `COMMENT=` / `shardkey=` /
`ROW_FORMAT=` + `BROADCAST`，以及生产同款全套选项组合 —— 全部 **span=1、`Create`** ✅。

> **一处 sqlglot 能力边界**：`CHECKSUM=1` 会让 sqlglot **自身**降级为 `Command`
> （实测：无论有无 UNIQUE KEY、无论是否经过剥离，均为 `Command`），
> 因此该组合最终**失败关闭**。这是解析器能力边界，非本剥离器缺陷。

#### 5.17.4 索引选项上下文（BLOCK-F2）

| 用例 | Rev.F | Rev.G |
|---|---|---|
| `USING COMMENT 'x'`（缺 BTREE/HASH） | span=1、`Create`、**E999 消失** | **span=0、E999 保留** ✅ |
| `COMMENT 'x' USING`（缺类型） | span=1、`Create` | **span=0、E999 保留** ✅ |
| `COMMENT` 后非字符串 | — | **span=0、E999 保留** ✅ |
| 正例 `USING BTREE COMMENT 'x'` | ✅ | **span=1、`Create`** ✅ |
| 正例 纯 `COMMENT 'x'` | ✅ | **span=1、`Create`** ✅ |

#### 5.17.5 `PARTITION BY` 的处置

> ~~`PARTITION BY` 作为**不透明终结子句**：遇到即停止消费与目标识别，其后内容不校验也不改写。~~
> **⚠️ 上句为 Rev.G 历史口径，已被第七轮 BLOCK-G2 与第八轮 BLOCK-H2 先后推翻，**
> **不得作为施工依据。现行口径见 §5.21.2：分区子句按 TDSQL 官方文法完整消费，且不再要求消费到语句结束。**
因此目标必须出现在 `PARTITION BY` **之前**——与真实 TDSQL 输出一致。
既有 `test_d5_hash_plus_partition`（HASH + 二级分区）实测 **`cols=3`，未回归** ✅。

### 5.19 BLOCK-G1 / G2 / G3：语法单元内部结构的完整性（Rev.H 新增）

判据统一为**单调不变松**（见 Rev.H 修订说明）：
`rank(NoneType/E999)=0 < rank(Command)=1 < rank(Create)=2`；
反例要求 `rank(候选) ≤ rank(主干)` 且主干的 E999 不得消失，正例要求候选为 `Create`。
**所有期望值均由主干实测得出，不手写。**

#### 5.19.1 BLOCK-G1：UNIQUE 键值列表

Rev.G 只对索引**选项区**做了完整消费，键值列表仍只做括号配平：

| 键值列表 | 主干 | Rev.G | Rev.H |
|---|---|---|---|
| `uk()` 空清单 | E999 | `Create`（**吞错**） | **E999 保留** ✅ |
| `uk(,)` 只有逗号 | E999 | `Create` | **E999 保留** ✅ |
| `uk(,id)` 前导逗号 | E999 | `Create` | **E999 保留** ✅ |
| `uk(id,)` 尾随逗号 | E999 | `Create` | **E999 保留** ✅ |
| `uk(id,,sk)` 连续逗号 | E999 | `Create` | **E999 保留** ✅ |
| `uk('id')` 字符串键 | E999 | `Create` | **E999 保留** ✅ |
| `uk(123)` 数字键 | E999 | `Create` | **E999 保留** ✅ |
| `uk(lower(id))` 函数键 | E999 | `Create` | **E999 保留** ✅ |
| `uk(id+1)` 表达式键 | E999 | `Create` | **E999 保留** ✅ |
| `uk(id('x'))` 前缀长度非数字 | E999 | `Create` | **E999 保留** ✅ |
| `uk(id(10)` 前缀括号未闭合 | E999 | `Create` | **E999 保留** ✅ |

正例（合法且 sqlglot 支持）必须仍恢复：`(id)`、`` (`id`) ``、`` (`id`,`sk`) ``、
`` (`id`(10)) ``、`` (`id`(10),`sk`) `` —— **5 例全部 `Create`** ✅

> ⚠️ **产品边界（实测确认，非本次收紧）**：`` (`id` ASC) ``、`` (`id` DESC) ``、
> `` (`id`(10) DESC,`sk`) `` 这三种**合法 MySQL 形态**，去掉 COMMENT 后
> **sqlglot 自身即 ParseError**。本次维持失败关闭，与 §5.4 的四类边界同类。
> 我第一版把它们写进"必须恢复"的正例组，跑出 3 个红才发现是**我的归类错**。

**建模的 key-part 文法**（MySQL 官方 `key_part` 的子集）：

```text
key_part := (VAR | IDENTIFIER) [ "(" NUMBER ")" ] [ ASC | DESC ]
key_list := key_part ( "," key_part )*          # 至少一个；逗号不得前导/尾随/连续
```

实测依据：仓内全部语料 + 生产 fixture 的索引键值列表内**只出现** `VAR` / `IDENTIFIER` /
`COMMA` 三种 token（唯一那 1 个 `NUMBER` 经定位是列名为 `key` 的 `VARCHAR(128)` 列定义，
系我扫描器的误命中，不是 key-part）。前缀长度与 `ASC/DESC` 语料中未出现，
但属官方 `key_part` 的无歧义形态，一并纳入以免对常见 DDL 失败关闭。

#### 5.19.2 BLOCK-G2：分区子句

Rev.G 写 `if tt == TokenType.PARTITION_BY: break`，其后 token 完全不校验：

| 分区尾巴 | 主干（带UK / 无UK） | Rev.G（带UK） | Rev.H（带UK / 无UK） |
|---|---|---|---|
| 裸 `PARTITION BY` | E999 / `Create` | `Create`（**吞错**） | **E999** / `Command` ✅ |
| `PARTITION BY DEFAULT` | E999 / `Create` | `Create`（**吞错**） | **E999** / `Command` ✅ |
| 方法为字符串 `'HASH'(sk)` | E999 / `Command` | E999 | **E999** / `Command` ✅ |
| 空括号 `HASH()` | E999 / `Command` | E999 | **E999** / `Command` ✅ |
| 未闭合 `HASH(sk` | E999 / `Command` | E999 | **E999** / `Command` ✅ |
| 合法分区后**尾随垃圾** | E999 / `Command` | — | **E999** / `Command` ✅ |
| 分区体内**第二个方言声明** | E999 / `Create` | — | **E999** / `Command` ✅ |
| 分区体内**藏分号** | E999 / E999 | — | **E999** / E999 ✅ |

正例必须仍恢复（**D5 场景不得回归**）：
`PARTITION BY RANGE (YEAR(dt)) (PARTITION p1 VALUES LESS THAN (2026), ...)`
—— 带 UK / 无 UK **两条路径均 `Create`、`cols=3`** ✅

> ⚠️ **我没有采纳 O 的"保守方案"（遇 `PARTITION_BY` 一律失败关闭）。**
> 实测该方案会让 D5 这类合法形态从主干的 `Create` 降为 `Command`——
> 是**真实的覆盖面损失**，而 O 自己也写明"只有用户接受这一产品边界时才能采用"。
> 采用他的**推荐方案**（完整消费分区子句）后，D5 无 UK 路径保持 `Create`、`cols=3`
> 与主干一致，**零覆盖面损失**，同时上表 8 类反例全部失败关闭。

**建模的分区子句文法**（MySQL `partition_options` 的子集）：

```text
partition_clause := PARTITION BY [LINEAR] <方法> [COLUMNS] "(" <非空> ")"
                    [ PARTITIONS NUMBER ] [ "(" <非空分区定义表> ")" ] <语句结束>
方法 := HASH | KEY | RANGE | LIST        # 裸词；KEY/RANGE/LIST 有专属 TokenType
```

括号体内部不逐 token 建模（分区定义语法庞大），但它**不是被跳过**：
必须非空、必须闭合、**内部不得出现** `PARTITION BY` / `TDSQL_DISTRIBUTED` /
`BROADCAST` / 分号，且整个子句必须消费到**语句结束**——尾随任何未认领 token 即失败关闭。
本函数对该区间**不做任何改写**。

> **实测依据**：仓内全部语料 + 生产 14 表中，作为 **token** 出现的 `PARTITION BY`
> 仅 **1 处**（`01_naming_ddl.sql` 的 `PARTITION BY HASH(region_code) PARTITIONS 4`），
> 且该语句既无方言尾子句也无 UNIQUE COMMENT，不走本恢复链。
> 生产 mysqldump 的分区子句包在 `/*!50100 ... */` 里——**实测 sqlglot 词法器整体跳过**，
> 生产 fixture gg78 的尾部只剩 13 个 token（`ENGINE` / `DEFAULT CHARSET` / `COLLATE` / `COMMENT`），
> `PARTITION BY LIST` 根本不进入 token 流。因此本改动对生产 fixture **零影响**。

> ⚠️ **产品边界（sqlglot 能力，非本次收紧）**：`HASH(col) PARTITIONS 4`、`LINEAR HASH`、
> `KEY(col)` 三种形态**消费器接受**，但 sqlglot 自身把它们降级为 `Command`；
> `RANGE COLUMNS(...)` 与 `LIST (...) (PARTITION ... VALUES IN ...)` 则 sqlglot 直接 ParseError。
> 五者均与主干同结论，属既有边界。

#### 5.19.3 BLOCK-G3：表选项值谓词

Rev.G 把 `ENGINE` / `ROW_FORMAT` / `SHARDKEY` 统一放行 `VAR|IDENTIFIER|STRING|NUMBER`：

| 选项取值 | 主干（带UK） | Rev.G（带UK） | Rev.H（带UK / 无UK） |
|---|---|---|---|
| `ENGINE=123` | E999 | `Create`（**吞错**） | **E999** / `Command` ✅ |
| `ROW_FORMAT=123` | E999 | `Create` | **E999** / `Command` ✅ |
| `ROW_FORMAT='x'` | E999 | `Create` | **E999** / `Command` ✅ |
| `ROW_FORMAT=UNKNOWN` | E999 | `Create` | **E999** / `Command` ✅ |
| `shardkey=123` | E999 | `Create` | **E999** / `Command` ✅ |
| `shardkey='sk'` | E999 | `Create` | **E999** / `Command` ✅ |
| `AUTO_INCREMENT=abc` | E999 | — | **E999** / `Command` ✅ |
| `COMMENT=123` | E999 | — | **E999** / `Command` ✅ |
| `PACK_KEYS=7` | E999 | — | **E999** / `Command` ✅ |
| `STATS_PERSISTENT='1'` | E999 | — | **E999** / `Command` ✅ |
| `DEFAULT CHARSET=123` | E999 | — | **E999** / `Command` ✅ |

正例必须仍恢复：`ENGINE=InnoDB`、`ENGINE='InnoDB'`、`ROW_FORMAT=DYNAMIC|DEFAULT|FIXED|COMPRESSED`、
`shardkey=sk`、`shardkey=noshardkey_allset`、`PACK_KEYS=1|DEFAULT`、`AUTO_INCREMENT=100`、
生产同款全套组合 —— **12 例全部 `Create`** ✅

**每选项值谓词（全部由语料实测得出）**：

| 选项 | 合法取值 | 语料实测 |
|---|---|---|
| `ENGINE` | `VAR` / `IDENTIFIER` / `STRING`（**拒 NUMBER**） | `InnoDB` ×77、`MyISAM` ×1，全为 `VAR` |
| `[DEFAULT] CHARSET` / `CHARACTER SET` | 同上 | `utf8mb4` ×76、`latin1` ×2 |
| `[DEFAULT] COLLATE` | 同上 | 3 种取值 ×26 |
| `COMMENT` | `STRING` | 大量 |
| `AUTO_INCREMENT` | `NUMBER` | ×8 |
| `SHARDKEY` | `VAR` / `IDENTIFIER`（**拒 STRING / NUMBER**） | 10 种列名 + `noshardkey_allset` ×9 |
| `ROW_FORMAT` | 裸词且 ∈ `{DEFAULT, DYNAMIC, FIXED, COMPRESSED, REDUNDANT, COMPACT}` | 语料未出现；按官方枚举建模 |
| `STATS_PERSISTENT` / `PACK_KEYS` / `DELAY_KEY_WRITE` | `0` / `1` / 裸词 `DEFAULT` | 语料未出现；按官方取值建模 |
| `CHECKSUM` / `AVG_ROW_LENGTH` / `KEY_BLOCK_SIZE` / `MAX_ROWS` / `MIN_ROWS` | `NUMBER` | 语料未出现 |

> 🚨 **施工陷阱（实测，务必照做）**：`ROW_FORMAT=DEFAULT` 的值 token 是
> **`TokenType.DEFAULT`**、`ROW_FORMAT=FIXED` 的值 token 是 **`TokenType.DECIMAL`**，
> 其余才是 `VAR`。因此枚举必须**按文本匹配**（并用 `_is_bare_kw()` 排除引号形态），
> 不能按 token 类型匹配，否则 `DEFAULT` 与 `FIXED` 两个合法取值会被误拒。

#### 5.19.4 一并说清：本方案在"无 UNIQUE COMMENT"路径上是**主动收紧主干**的

这一点前几版只体现在 X 组，未在正文说明，本版补上。

主干 v1.6.2.1 的 `_TDSQL_DIALECT_RE` 会把方言尾子句从**任何**语句里删掉，
包括那些**表选项本身就非法**的语句；删完之后 sqlglot 宽松接纳，得到一个 `Create`。
**这个 `Create` 是对非法 DDL 的假成功**，119 条规则会照着这个不可信 AST 出结论。

本方案删除该正则后，这类语句失败关闭、停在 `Command`。H 组用例（数量见 §7.1a）中：

```text
较主干收紧（非法 DDL 由假 Create 降为 Command）= 14 例
  ├─ H3 分区非法（无UK）           3 例
  └─ H5 表选项值非法（无UK）       11 例
覆盖面损失（合法形态由 Create 降级）= 0 例
```

**这是本次修复的目的之一，不是副作用**；它与 §5.14.1 记录的生产缺陷是同一件事。
全语料 197 条、生产 14 表**零漂移**说明真实数据里不存在这类非法 DDL。

### 5.21 BLOCK-H1/H2/H3 与 TDSQL 官方语法对齐（Rev.I 新增）

判据：**TDSQL 官方语法优先**（见 Rev.I 修订说明的证据优先级）。
断言仍用**单调不变松**，但用例分为三类：

```text
neg        非法 DDL          → rank(候选) <= rank(主干)，主干 E999 不得消失
pos        TDSQL 官方合法    → 候选必须是 Create
pos_known  TDSQL 官方合法、
           但 sqlglot 暂不支持 → 必须失败关闭（与主干同结论），**单独计数登记**
```

#### 5.21.1 BLOCK-H1：恢复门禁只验证目标 UNIQUE

Rev.H 的 UNIQUE 单独恢复路径**不看表尾、不看其他定义项**：

| 编号 | 目标之外的非法结构 | 主干 | Rev.H | Rev.I |
|---|---|---|---|---|
| H1-1 | `ENGINE=123` | E999 | `Create`（**吞错**） | **E999 保留** ✅ |
| H1-2 | 空普通索引 `KEY k ()` | E999 | `Create` | **E999 保留** ✅ |
| H1-3 | 定义列表重复逗号 `id INT,,` | E999 | `Create` | **E999 保留** ✅ |
| H1-4 | 孤立表选项 `) DEFAULT` | E999 | `Create` | **E999 保留** ✅ |
| H1-5 | `PARTITION BY RANGE(,)` | E999 | `Create` | **E999 保留** ✅ |
| H1-6 | 列缺数据类型 `(id, ...)` | E999 | `Create` | **E999 保留** ✅ |
| H1-7 | 空主键 `PRIMARY KEY ()` | E999 | E999 | **E999 保留** ✅ |

> H1-1~H1-4 **不需要任何 TDSQL 方言目标就能发生**。因此第七轮 W/H 组
> 只围绕"方言目标 + 表选项/分区"做组合，证明不了 UNIQUE 单独恢复路径的安全性——
> 这是 O 本轮最关键的一句判断，成立。

**整改**：`_plan_recovery()` 成为唯一入口，无论走哪条路径都必须：
① 逐项普查定义列表（拒绝空定义项、空索引、缺类型列）；
② **始终**完整验证表尾（`_scan_table_tail()`，Rev.J 起只有一种行为，无开关参数）；
③ 候选 AST 过 `_validate_recovery_candidate()` 结构保真门禁。

> 🚨 **施工要点**：`_scan_table_tail()` **无论走哪条恢复路径都必须调用**。
> 少了它，UNIQUE 单独恢复路径就会回到"表尾不看"的老路——这正是 BLOCK-H1 的本体。
>
> ⚠️ Rev.I 曾给它加过一个 `want_dialect=False` 开关，注释写"只验证、不产 span"，
> **实现却始终产 span**，两者矛盾（第九轮 MAJOR-X3）。Rev.J **删除该参数**——
> 表尾扫描只有一种行为：完整验证并返回方言 span 与辅助掩码 span。

#### 5.21.2 BLOCK-H2：分区表达式与分区定义

| 反例 | 主干 | Rev.H | Rev.I |
|---|---|---|---|
| `PARTITION BY RANGE(,)` | E999 | `Create`（**吞错**） | **E999 保留** ✅ |
| `PARTITION BY RANGE(+)` | E999 | `Create` | **E999 保留** ✅ |
| `PARTITION BY RANGE(id,)` | E999 | `Create` | **E999 保留** ✅ |
| 分区定义表非 `PARTITION` 起始 | E999 | E999 | **E999 保留** ✅ |
| 残缺 `VALUES` | E999 | E999 | **E999 保留** ✅ |

**建模的 TDSQL 二级分区文法**：

```text
partition_clause := PARTITION BY [LINEAR] <方法> [COLUMNS] "(" partition_expr ")"
                    [PARTITIONS NUMBER] [ "(" partition_def ("," partition_def)* ")" ]
partition_expr   := col | FUNC "(" col ")"        FUNC ∈ {YEAR,TO_DAYS,TO_SECONDS,
                                                          UNIX_TIMESTAMP,MONTH,DAYOFMONTH}
partition_def    := [PARTITION] name VALUES (LESS THAN "(" 字面量列表 ")"
                                            | LESS THAN MAXVALUE
                                            | IN "(" 字面量列表 ")")
                    [ENGINE [=] name] [COMMENT [=] STRING]     ← 可掩码 span
方法             := RANGE | LIST | HASH | KEY
```

> ⚠️ **`PARTITION` 前缀是可选的**：TDSQL 官方 `TDSQL_DISTRIBUTED BY range(a)
> (s1 values less than(100), ...)` 的分片定义表**没有** `PARTITION` 前缀，
> 而二级分区 `PARTITION BY LIST(c) (PARTITION p1 VALUES IN (1))` 有。两种都要接受。

> ⚠️ **不再要求分区子句消费到语句结束**（Rev.H 如此要求）。
> 官方存在 `PARTITION BY ... TDSQL_DISTRIBUTED BY RANGE(id)` 的顺序，
> 强制到 EOF 会把该官方形态判成非法。尾部完整性改由 `_scan_table_tail()` 统一保证。

#### 5.21.3 BLOCK-H3：`USING HASH` 不是 TDSQL 合规 DDL

TDSQL 官方 `index_type` 只有 `USING {BTREE}`。`HASH` 是 MySQL 某些引擎的能力：

| 输入 | 主干 | Rev.H | Rev.I |
|---|---|---|---|
| `UNIQUE KEY uk (id) USING HASH COMMENT 'x'` | E999 | `Create`（**次生放行**） | **E999 保留** ✅ |
| `UNIQUE KEY uk (id) USING BTREE COMMENT 'x'` | E999 | `Create` | `Create` ✅ |

> 实测确认：**119 条规则中没有任何一条负责否决 HASH 索引类型**，
> 因此一旦放行就直接进入"可信 AST 审核"，下游无从补救。
> 若目标内网 TDSQL 的特定内核版本确实支持 HASH，需提供该版本官方手册或目标实例
> 真实 `SHOW CREATE TABLE` 证据，由用户决定后再纳入版本化能力矩阵——
> **不得只以 sqlglot / MySQL 能解析为证据**。

#### 5.21.4 TDSQL 官方合法形态：必须恢复（MAJOR-H1 + 我方自查）

| 形态 | 依据 | 主干 | Rev.H | Rev.I |
|---|---|---|---|---|
| `key_part` 的 `ASC` / `DESC` | 官方 `key_part: {col_name [(length)]} [ASC \| DESC]` | E999 | **E999（误判为非法）** | `Create` ✅ |
| 二级 LIST 分区 + partition `ENGINE=` | 官方二级分区 + 官方 partition_definition | E999 | **E999（误判为非法）** | `Create` ✅ |
| **`TDSQL_DISTRIBUTED BY range\|list (col) (分片定义表)`** | 官方建表原例 | E999 | **E999（误判为非法）** | `Create` ✅ |
| **`PARTITION BY ... (...) TDSQL_DISTRIBUTED BY RANGE(id)`** | 官方二级分区原例 `tb_sub_r_l` | E999 | **E999（误判为非法）** | `Create` ✅ |
| **多列 `shardkey=(a,b)`** | 项目自身 `tdsql_connector.parse_shard_key_from_ddl()` | E999 | **E999（误判为非法）** | `Create` ✅ |
| `shardkey=col` / `noshardkey_allset` | 官方 | E999 | `Create` | `Create` ✅ |
| `shardkey=col PARTITION BY LIST(...)` | 官方二级分区原例 | E999 | `Create` | `Create` ✅ |

> 后三行加粗的是 **O 未发现、我自查出的**：Rev.H 会把三种官方合法形态判成非法。
> 方向与 BLOCK-H3 相反，但根因相同——**拿 MySQL / sqlglot 当判据**。

**sqlglot 缺口用同一套 span 掩码机制闭合**（实测五种形态全部一次通过）：

| TDSQL 官方形态 | sqlglot 30.x | Rev.I 处置 |
|---|---|---|
| `uk (id ASC)` / `uk (id DESC)` | ParseError | 掩码 `ASC`/`DESC` token → `Create` |
| `uk (id(10) DESC, sk)` | ParseError | 同上 → `Create` |
| `(PARTITION p1 VALUES IN (1) ENGINE = InnoDB)` | ParseError | 掩码 `ENGINE = InnoDB` → `Create` |
| `TDSQL_DISTRIBUTED BY RANGE(a) (s1 VALUES LESS THAN(100), ...)` | `Command` | 整体作方言 span 剥离 → `Create` |
| `PARTITION BY LIST(o) (...) TDSQL_DISTRIBUTED BY RANGE(id)` | `Command` | 只剥方言 span、保留分区 → `Create` |

> **掩码为什么不影响审核结论**：`raw_sql` 始终保持原文（S-4）；
> 实测 **119 条规则无一引用 `ASC`/`DESC`**，解析器也从不向规则层暴露排序方向；
> 分区规则（`oracle_compat._RE_HASH_PART` 等）读的是 `raw_sql` 正则，不读 AST。

#### 5.21.5 已知假阴性登记表（`pos_known`，O 的 I-7）

**用户决策（2026-08-26）**：MAXVALUE 一项**按 O 的要求单独登记为已知假阴性**，
本版不补实现，失败关闭（保留 E999）。以下为完整登记。

##### A. 已知假阴性：TDSQL 官方合法，本版未支持

| 编号 | 形态 | 合法性依据 | 受阻于 | 本版处置 | 语料/生产出现 | 用户批准 |
|---|---|---|---|---|---|---|
| **KFN-1** | `PARTITION ... VALUES LESS THAN MAXVALUE` | TDSQL / MySQL 官方 partition_definition | **sqlglot 30.x ParseError**（去掉方言尾子句后亦然，非本方案所致） | 失败关闭，**保留原 E999** | **0 次** | ✅ 2026-08-26 |
| ~~**KFN-2**~~ | ~~`PRIMARY KEY (col) COMMENT '…'`~~ | —— | —— | **登记已撤销** | —— | ❌ **2026-08-26 用户确认目标实例存在该形态 → 转为 DEF-3 修复**，见 Rev.L 修订说明与 §5.27 |
| **KFN-3** | `CHAR(n) BINARY`、`POINT`、`LINESTRING`、`POLYGON`、`MULTIPOINT`、`MULTILINESTRING`、`MULTIPOLYGON`、`GEOMETRYCOLLECTION` | TDSQL 官方数据类型清单（八种空间类型 + 字符族 `BINARY` 属性） | **sqlglot 29.0.0 / 30.14.0 / 30.17.0 三版一致 ParseError**（去掉索引 COMMENT 的普通建表亦然，非本方案所致） | 失败关闭，**保留原 E999**；`_TYPE_RULES` 已按官方八种登记完整，sqlglot 上游支持后自动消除，无需改本方案代码 | **0 次** | 🔔 **随 Rev.M 提请用户知悉**（见下方"确切代价"） |

**KFN-3 的确切代价**：建表语句中出现上述 8 种类型之一时，继续报 `E999_SYNTAX_ERROR`。

- **与本次修复无关**：逐条实测证明**修复前后行为完全相同**——`CREATE TABLE t (c POINT, sk INT)`
  在当前生产版本 v1.6.2.1 与 Rev.M 上都是 `ast=None / E999=有`。这是对**既有能力边界的如实登记**，
  **不是本次修改引入的新限制**，也不构成回归；
- **复检触发条件**：升级 `sqlglot` pin 时必须重跑 §7.1d 的 TY 组矩阵——若上游已支持，
  这 8 条会自动从 `pos_known` 变为可迁回 `pos`（manifest 中改一个字段即可）；
- **manifest 登记位置**：`TY-K-01 … TY-K-08`，分类 `pos_known`，`prov=SQLGLOT_LIMIT`。

**KFN-1 的确切代价**：一张表**同时**满足下面两个条件时，会继续误报 `E999_SYNTAX_ERROR`
及其连带的 R003/R004/R005/R028 等：

1. 分区定义中含 `VALUES LESS THAN MAXVALUE` 兜底分区；**且**
2. 该表带 UNIQUE 索引 COMMENT（即本次 DEF-2 的修复目标）。

只满足其一都不受影响：无 UNIQUE COMMENT 的 MAXVALUE 分区表本就走首次解析路径；
有 UNIQUE COMMENT 但无 MAXVALUE 的表按 §5.21.4 正常恢复。

**适用版本**：sqlglot `30.14.0`（本版锁定版本）。实测 `29.0.0` / `30.17.0` 行为相同。
若将来 sqlglot 支持该语法，本条自动失效——**移动依赖 pin 时须复测本条并更新登记**。

**复检触发条件**（满足任一即须专项处理，不得沿用本登记）：

- 目标内网实例出现同时含 MAXVALUE 兜底分区与 UNIQUE COMMENT 的表；
- 语料或生产回放中该组合出现次数由 0 变为非 0；
- 依赖 pin 移动到支持该语法的 sqlglot 版本。

> 🚫 **不得**为了消除本条而放宽分区定义消费器（例如退回"非空配平即通过"）——
> 那正是第八轮 BLOCK-H2。宁可保留这条有账可查的假阴性。

##### B. 合法性待确认：官方文档未列，保守失败关闭

以下形态**不是**已知假阴性，也**不是**已确认的非法语法，而是 TDSQL 官方二级分区文档
（只列 Range 与 List）未覆盖的形态。本版按 S-3 保守失败关闭，并在此登记以免下轮
再被当成"已确认非法"或"已确认合法"：

| 编号 | 形态 | 现状 | 本版处置 |
|---|---|---|---|
| **UNK-1** | `PARTITION BY HASH(col) PARTITIONS n` | 官方二级分区文档未列；sqlglot 亦降级为 `Command` | 失败关闭 |
| **UNK-2** | `PARTITION BY LINEAR HASH(col)` | 同上 | 失败关闭 |
| **UNK-3** | `PARTITION BY KEY(col)` | 同上 | 失败关闭 |
| **UNK-4** | `PARTITION BY RANGE COLUMNS(col)` | 同上；sqlglot ParseError | 失败关闭 |
| **UNK-5** | 二级分区日期函数 `DAYOFMONTH` / `TO_DAYS` / `TO_SECONDS` / `UNIX_TIMESTAMP` | 官方二级分区页只明示 year/month/day；这四个**无目标实例证据** | 失败关闭（Rev.J 曾误放行，第十轮 BLOCK-J5 收回） |
| **UNK-6** | 本地表选项 `CHECKSUM` / `AVG_ROW_LENGTH` / `KEY_BLOCK_SIZE` / `MAX_ROWS` / `MIN_ROWS` / `PACK_KEYS` / `DELAY_KEY_WRITE` | 官方 local_table_option 清单**未列**，语料出现 0 次 | 失败关闭 |
| **UNK-7** | `TDSQL_PARTITION BY RANGE/LIST`（新代际二级分区关键字） | 2026 DTS 页显示存在新旧两代方言；**目标实例代际未确认** | 本版不实现；与旧代际 `PARTITION BY` **不得混成一个无版本白名单**（第十轮 BLOCK-J4） |

> 这四条与主干结论一致（不产生任何行为变化），因此**不构成本次修改引入的假阴性**。
> 若需支持，须先提供目标实例真实 `SHOW CREATE TABLE` 或官方手册证据，
> 由用户决定后纳入版本化能力矩阵——**不得只以 MySQL 合法或 sqlglot 能解析为依据**。

##### C. 既有产品边界（非本次引入，沿用 §5.4）

`UNIQUE KEY uk USING BTREE (a)`（index_type 前置于键值列表）、函数/表达式索引、
`VISIBLE`、`KEY_BLOCK_SIZE` 等四类，见 §5.4，本版未改变其行为。

#### 5.21.6 依赖锁定（MAJOR-H2）

O 指出 `sqlglot>=29,<31` 不是可复现构建，两个端点证明不了区间内所有版本。**成立。**

| 版本 | H 组用例（数量见 §7.1a） | W/Z/Y/X 矩阵 |
|---|---|---|
| 29.0.0（原下界） | 85/85 | 全通过 |
| **30.14.0（本次全量验证版本）** | 85/85 | 全通过 |
| 30.17.0（当前最新 30.x） | 85/85 | 全通过 |

三版**逐条一致，0 例差异**。据此：

- `requirements.txt` / `pyproject.toml` 均改为**精确锁定 `sqlglot==30.14.0`**；
- 上表作为将来移动 pin 的依据：**换版本必须重跑全部矩阵**，不得只凭区间放行。


### 5.23 第九轮全域审计整改实测（Rev.J 新增）

#### 5.23.1 测试判据的规范化（BLOCK-X1）

Rev.H~I 的 `rank` 判据以**当前缺陷主干**为 oracle：

```text
主干错误 Create（rank=2）；候选仍错误 Create（rank=2）；2 <= 2 → 通过
```

> 📌 **一个必须说清的实测细节**：在 Rev.I 的 H 组里，实际"滑过判据且候选仍是 `Create`"
> 的用例是 **0 条**——Rev.I 恰好处处比主干更严。O 给出的反例
> （`TDSQL_DISTRIBUTED BY RANGE(id) (s1 VALUES IN (1))`，主干 `Command` → Rev.I `Create`）
> 其实**会**被 rank 判据拒绝，只是**我的测试集里没有这条输入**。
> 所以真实情况是两个问题叠加：**判据证明力不足** + **输入域有缺口**。
> 无论哪一个，结论都一样：判据必须改。

Rev.J 起用例分为五类，期望值**由 TDSQL 规范推导**，主干结果只作 `baseline_observation`：

| 类别 | 含义 | 硬断言 |
|---|---|---|
| `pos` | TDSQL 官方/生产实证合法 | 候选必须 `Create`，且结构指纹一致 |
| `neg` | 规范判定非法 | 候选**不得** `Create` |
| `pos_known` | 官方合法、经用户批准本版失败关闭 | 必须失败关闭，**单独登记**（KFN-1） |
| `unsupported_unproven` | 无目标版本证据 | 必须失败关闭，**不冒充合法也不冒充非法** |
| `characterization_user_decision` | 锁定用户决策，不代表官方合法 | 锁定现状（ADJ-6 等） |

#### 5.23.2 逐项整改实测

**BLOCK-X2 列定义**（主干 E999 → Rev.I `Create` → Rev.J 保留 E999）：

| 列定义 | 主干 | Rev.I | Rev.J |
|---|---|---|---|
| `id VARCHAR()` | E999 | `Create`，静默变 `TEXT` | **E999 保留** ✅ |
| `id DECIMAL(,2)` | E999 | `Create`，变 `DECIMAL(2)` | **E999 保留** ✅ |
| `id DECIMAL(10,)` | E999 | `Create`，变 `DECIMAL(10)` | **E999 保留** ✅ |
| `id INT DEFAULT 1 DEFAULT 2` | E999 | `Create` | **E999 保留** ✅ |
| `id INT NULL NOT NULL` | E999 | `Create` | **E999 保留** ✅ |
| `id INT AUTO_INCREMENT AUTO_INCREMENT` | E999 | `Create` | **E999 保留** ✅ |
| `id INT COMMENT 'a' COMMENT 'b'` | E999 | `Create` | **E999 保留** ✅ |

**BLOCK-X3 主目标缺失**：`CREATE TABLE t (id VARCHAR()) PARTITION BY RANGE(id)
(PARTITION p0 VALUES LESS THAN (10) COMMENT='p')` —— 无 UNIQUE COMMENT、无方言声明，
Rev.I 仅凭 partition COMMENT 掩码即恢复并把 `VARCHAR()` 变 `TEXT`；**Rev.J 保留 E999** ✅

**BLOCK-X4 一级分片**（主干 `Command` → Rev.I `Create` → Rev.J `Command`）：
`HASH(id) (s1 VALUES LESS THAN (10))`、`RANGE(id) (s1 VALUES IN (1))`、
`LIST(id) (s1 VALUES LESS THAN (10))` —— **三例全部不再升级** ✅

**BLOCK-X5 二级分区**：

| 用例 | 主干 | Rev.I | Rev.J |
|---|---|---|---|
| 两个 `PARTITION BY` | E999 | `Create` | **E999 保留** ✅ |
| `VALUES IN (foo)` 标识符冒充字面量 | E999 | `Create` | **E999 保留** ✅ |
| 官方 `YEAR(dt)` | E999 | `Create` | `Create` ✅ |
| **官方 `MONTH(dt)`** | E999 | **E999（死分支误拒）** | **`Create`** ✅ |
| **官方 `DAY(dt)`** | E999 | **E999（死分支误拒）** | **`Create`** ✅ |
| **官方负值边界 `LESS THAN (-1)`** | E999 | **E999（误拒）** | **`Create`** ✅ |

**BLOCK-X6 表尾状态机**（四例主干 E999 → Rev.I `Create` → Rev.J 保留 E999）：
重复 `shardkey`、`shardkey + TDSQL_DISTRIBUTED` 并存、终结声明后再接表选项、
二级分区后再接表选项 —— **全部失败关闭** ✅

**MAJOR-X2 索引值域/次数**（四例主干 E999 → Rev.I `Create` → Rev.J 保留 E999）：
`uk(id(1.5))`、`uk(id(0))`、重复 `USING BTREE`、重复索引 `COMMENT` ✅

#### 5.23.3 表尾阶段模型与 provenance

```text
阶段 0 LOCAL_OPTIONS      本地表选项（shardkey 也在此阶段，但它**是**一级分布声明）
阶段 1 SECONDARY_PARTITION 二级分区子句（至多一个）
阶段 2 DISTRIBUTION        TDSQL_DISTRIBUTED BY … / BROADCAST（至多一个）
约束：一级分布声明（shardkey / TDSQL_DISTRIBUTED / BROADCAST）至多一个；
      本地表选项不得出现在分区/终结阶段之后；同名表选项不可重复。
```

| 允许的子句顺序 | provenance |
|---|---|
| `LOCAL_OPTIONS* shardkey=col` | **CORPUS**：生产 fixture 实测 `) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=… COMMENT='…' shardkey=black_list_seq_num` |
| `shardkey=col PARTITION BY LIST(...) (...)` | **OFFICIAL**：腾讯官方二级分区原例 |
| `PARTITION BY LIST(...) (...) TDSQL_DISTRIBUTED BY RANGE(id)` | **OFFICIAL**：官方原例 `tb_sub_r_l` |
| `TDSQL_DISTRIBUTED BY HASH(sk) PARTITION BY RANGE(...)` | **PROJECT_ACCEPTED**：无官方正例；项目既有 D5/T5 用例，O 第八轮明确接受"保住 D5 覆盖面" |
| `shardkey=col … BROADCAST` | **ADJ-6 characterization**：用户已冻结的现状行为，**不代表 TDSQL 合法** |

#### 5.23.4 表选项白名单与取证限制（BLOCK-X7）

> ⚠️ **取证限制（如实记录）**：本环境的出口代理**拦截 `cloud.tencent.com`**，
> 我**无法独立抓取完整官方 `Local_table_option` 清单**。因此本表按下列规则构建，
> 并把每项的 provenance 写进代码注释；**官方未列出且语料无实证的一律失败关闭**。

| option | 值谓词 | provenance |
|---|---|---|
| `ENGINE` | 引擎名（拒 NUMBER） | CORPUS ×78 |
| `[DEFAULT] CHARSET` / `CHARACTER SET` | 字符集名 | CORPUS ×78 |
| `[DEFAULT] COLLATE` | 排序规则名 | CORPUS ×26 |
| `COMMENT` | STRING | CORPUS ×多 |
| `AUTO_INCREMENT` | **正整数** | CORPUS ×8 |
| `SHARDKEY` | 单标识符 / `(a,b)` / `noshardkey_allset` | CORPUS ×20 + 官方 |
| `STATS_AUTO_RECALC` | `0` / `1` / `DEFAULT` | OFFICIAL（复审方引用） |
| `STATS_SAMPLE_PAGES` | 正整数 | OFFICIAL（复审方引用） |

**Rev.I 曾凭臆测放行、本版全部移出白名单**（语料出现 **0** 次且无 TDSQL 证据）：
`ROW_FORMAT`、`CHECKSUM`、`AVG_ROW_LENGTH`、`KEY_BLOCK_SIZE`、`MAX_ROWS`、
`MIN_ROWS`、`PACK_KEYS`、`DELAY_KEY_WRITE` —— 归入 `unsupported_unproven`（H6b 组 8 例）。
若目标实例 `SHOW CREATE TABLE` 证明某版本支持，须记录实例版本与输出后再纳入。

#### 5.23.5 结构指纹守恒（MAJOR-X1）

规划阶段生成 `SourceFingerprint`：

```text
table          归一化表名
definitions[]  逐定义项形态：
                 col:<列名>|<类型形态>|<约束 identity 序列>
                 idx:<种类>:<索引名>:(<key_part 序列>):<选项 identity 序列>
tail           表尾指纹：opt:<名>=<归一值> | part:<方法>:<表达式>:[定义表] | dist:<方法>:<键>:[定义表]
```

候选 AST 门禁逐项比对：① `Create` + `kind==TABLE` + 表名一致；
② 定义项**数量与逐项种类、列名**一致；③ 列必须有类型、索引必须有非空键列；
④ 原文有二级分区时，候选必须**恰好保留一个**分区 property。

> ⚠️ **已知例外（O 已指出，本版明确写入）**：生产 mysqldump 的
> `/*!50100 PARTITION BY ... */` 会被 sqlglot 词法器**整体跳过**，
> 原文 token 流中没有 `PARTITION BY`，故 ④ 不触发。该行为与当前 sqlglot 基线一致，
> 由两份生产 fixture 的**精确规则集合**断言兜底（F 组）。


### 5.25 第十轮整改实测（Rev.K 新增）

#### 5.25.1 官方语法取证缺口的补齐（BLOCK-J4）

Rev.J §5.23.4 曾如实记录："`cloud.tencent.com` 被出口代理拦截，无法独立抓取完整官方
`Local_table_option` 清单"。第十轮复审方提供了**官方文档离线摘要**，据此更正：

| 项 | Rev.J 判定 | 官方摘要 | Rev.K |
|---|---|---|---|
| `ROW_FORMAT` | `unsupported_unproven`（**取证错误**） | 官方 local_table_option，值域 DEFAULT/DYNAMIC/FIXED/COMPRESSED/REDUNDANT/COMPACT | **`pos`**，严格六值枚举 |
| `STATS_PERSISTENT` | 未列入 | 官方，值域 DEFAULT/0/1 | **`pos`** |
| `STATS_AUTO_RECALC` / `STATS_SAMPLE_PAGES` | 已列入 | 官方 | 保持 |
| `CHECKSUM` / `AVG_ROW_LENGTH` / `KEY_BLOCK_SIZE` / `MAX_ROWS` / `MIN_ROWS` / `PACK_KEYS` / `DELAY_KEY_WRITE` | `unsupported_unproven` | 官方清单**未列** | 保持 `unsupported_unproven` |
| 列级 `COLUMN_FORMAT` / `ENGINE_ATTRIBUTE` | 拒绝（当作非法） | 官方 column_definition **明示支持** | **实现并接受** |
| 二级分区日期函数 | YEAR/MONTH/DAY + 另 4 个 | 官方只明示 **year/month/day** | 收回另 4 个 → `unsupported_unproven` |
| 类型参数 | 一律"正整数" | 官方兼容性页继承 MySQL：`DECIMAL(M,0)`、`DATETIME(0)`、fsp 0~6 均合法 | **scale / fsp 允许 0** |
| `DEFAULT` 值域 | "后面还有一个 token" | 官方：字符串、数值（可带 +/-、小数、科学计数）、hex、bit、布尔、NULL | 按官方字面量域建模 |

#### 5.25.2 逐项整改实测

**BLOCK-J1 列定义与 DEFAULT**（主干 E999）：

| 输入 | Rev.J | Rev.K |
|---|---|---|
| `id RANGE` / `id NULL` | `Create`（关键字冒充类型） | **E999 保留** ✅ |
| `VARCHAR(1,2,3)` / `INT(1,2)` / `DATE(1)` / `DECIMAL(10,2,1)` | `Create` | **E999 保留** ✅ |
| `JSON(1)` | `Create`，候选静默变 `JSON` | **E999 保留** ✅ |
| `DEFAULT foo` / `DEFAULT ()` / `DEFAULT (,)` / `DEFAULT (SELECT 1)` | `Create` | **E999 保留** ✅ |
| **官方** `DECIMAL(10,0)` / `DATETIME(0)` / `TIME(0)` | **误拒** | **恢复** ✅ |
| **官方** `DEFAULT -1` / `DEFAULT +1` | **误拒** | **恢复** ✅ |
| **官方** `COLUMN_FORMAT DYNAMIC` / `ENGINE_ATTRIBUTE='x'` | **误拒** | **恢复** ✅ |

**BLOCK-J3 表尾与分号**：

| 输入 | Rev.J | Rev.K |
|---|---|---|
| `shardkey=id ENGINE=InnoDB` | `ACCEPT`（shardkey 不推进 phase） | **REJECT** ✅ |
| `BROADCAST … PARTITION BY …` | `ACCEPT` | **REJECT** ✅ |
| `PARTITION BY … BROADCAST` | `ACCEPT` | **REJECT** ✅ |
| **合法单条 DDL 末尾 `;`** | **REJECT（误拒）** | **ACCEPT** ✅ |

**BLOCK-J5 分区**：

| 输入 | Rev.J | Rev.K |
|---|---|---|
| `VALUES IN (-'x')` | `Create`（符号可修饰字符串） | **E999 保留** ✅ |
| 未举证函数 `UNIX_TIMESTAMP(dt)` | `Create` | **E999 保留** ✅（`unsupported_unproven`） |
| 分区选项反序 `COMMENT=… ENGINE=…` | `ACCEPT` | **REJECT** ✅ |
| **官方** `STORAGE ENGINE=InnoDB` | **误拒** | **恢复** ✅ |

**MAJOR-J2 索引**：`PRIMARY KEY pk(id)` 由 `Create` → **E999 保留**；
前后置 `USING` 共用 seen，`UNIQUE KEY uk USING BTREE (id) USING BTREE` 在 **token 层**即拒绝。

#### 5.25.3 表尾显式迁移表（BLOCK-J3）

每条边都必须有 provenance；**没有证据的边默认不存在**：

| 起点 | atom | 终点 | provenance |
|---|---|---|---|
| S0 LOCAL | LOCAL_OPTION | S0 | OFFICIAL：local_table_options 在最前 |
| S0 | SHARDKEY | S1 | OFFICIAL（shardkey 置于尾部）+ CORPUS 生产 fixture |
| S0 | PARTITION_BY | S2 | OFFICIAL：二级分区页示例 |
| S0 | TDSQL_DISTRIBUTED | S3 | OFFICIAL：一级 range/list 声明 |
| S0 | BROADCAST | S3 | TARGET_INSTANCE |
| S1 | PARTITION_BY | S2 | OFFICIAL：`shardkey=col PARTITION BY LIST(...)` |
| S1 | BROADCAST | S3 | **ADJ-6 characterization**（用户冻结，不代表 TDSQL 合法） |
| S2 | TDSQL_DISTRIBUTED | S3 | OFFICIAL：`tb_sub_r_l` 原例 |
| S3 | PARTITION_BY | S2 | **PROJECT_ACCEPTED**，且 `_TAIL_EDGE_GUARD` 限定**仅 TDSQL_DISTRIBUTED 方向**（BROADCAST 之后不得接分区） |

#### 5.25.4 索引 COMMENT 按 kind 分流（我方回归教训）

| 索引类型 + COMMENT | sqlglot 30.x 实测 | Rev.K 处置 |
|---|---|---|
| `UNIQUE KEY u (a) COMMENT` | **ParseError** | 本次 DEF-2 **主目标**，记 span 掩码 |
| `PRIMARY KEY (a) COMMENT` | **ParseError** | 失败关闭，登记 **KFN-2** |
| `KEY k (a) COMMENT` / `INDEX` / `FULLTEXT` | 可解析 | **原样保留、不掩码** |

> 🚨 我一度把三者统一判成失败关闭，**生产 fixture gg78 立刻回归**——它含真实的
> `KEY idx_term_bizlog (…) COMMENT '终端查询索引：…'`。
> **按 kind 分支时，每一支的处置必须由该支的实测能力决定。**
> 抓住它的是两份 fixture 的**精确规则集合断言**，这条断言必须永久保留在回归里。


### 5.27 DEF-3：PRIMARY 索引 COMMENT（Rev.L 新增）

#### 5.27.1 缺陷形态与影响

用户确认目标实例存在 `PRIMARY KEY (col) COMMENT '…'` 形态的表。
实测一张典型内网形态的表（4 列 + `PRIMARY KEY (id) COMMENT '主键索引'`）：

| | `ast` | E999 | `cols` | `has_primary_key` | 集中式规则集合 |
|---|---|---|---|---|---|
| 主干 v1.6.2.1 | `None` | 有 | 0 | `False` | `E999, R003, R004, R005, R028` |
| Rev.K（KFN-2 登记态） | `None` | 有 | 0 | `False` | 同上 |
| **Rev.L** | **`Create`** | **无** | **4** | **`True`** | **`R037`** |

**误报机理与 gg78 完全一致**：解析崩溃 → `has_primary_key=False` 触发 R003/R004、
列信息全丢触发 R005/R028。四条全是误报。

#### 5.27.2 修复机制（不新增机制）

`_consume_index_definition()` 的索引 COMMENT 分流由两支扩为三支，**只改一处判断**：

```text
UNIQUE  → ParseError → 主目标，记 span 掩码        （DEF-2，既有）
PRIMARY → ParseError → 主目标，记 span 掩码        （DEF-3，本版新增）
NORMAL / FULLTEXT / SPATIAL → 可解析 → 原样保留     （既有）
```

掩码、`_spans_only_diff()` span 门禁、`_validate_recovery_candidate()` 结构指纹守恒
**全部沿用 DEF-2 的既有链路**。实测掩码后可解析的形态：

| 形态 | 原文 | 掩码后 |
|---|---|---|
| `PRIMARY KEY (a) COMMENT 'pk'` | ParseError | `Create` ✅ |
| `PRIMARY KEY (a,b) COMMENT 'pk'` | ParseError | `Create` ✅ |
| `PRIMARY KEY (a) USING BTREE COMMENT 'pk'` | ParseError | `Create` ✅ |
| `PRIMARY KEY (a) COMMENT 'pk', UNIQUE KEY u (b) COMMENT 'uk'` | ParseError | `Create` ✅ |

#### 5.27.3 边界（P2 组）

本改动**扩大了进入恢复链的语句范围**，故必须证明边界未被放松：

| 非法近邻 | Rev.L |
|---|---|
| `PRIMARY KEY \`pk\` (id) COMMENT 'x'`（PRIMARY 后带索引名） | **E999 保留** ✅ |
| `PRIMARY KEY () COMMENT 'x'`（空键列） | **E999 保留** ✅ |
| ``PRIMARY KEY (id) COMMENT `x` ``（COMMENT 非字符串） | **E999 保留** ✅ |
| `PRIMARY KEY (id) COMMENT 'a' COMMENT 'b'`（重复） | **E999 保留** ✅ |
| `PRIMARY KEY (id) USING HASH COMMENT 'x'`（TDSQL 官方只有 BTREE） | **E999 保留** ✅ |
| `PRIMARY KEY USING BTREE (id) USING BTREE COMMENT 'x'`（前后置 USING） | **E999 保留** ✅ |

#### 5.27.4 爆炸半径

| 检查项 | 结果 |
|---|---|
| 全语料 197 条 | 恰好 2 条变化，**与 Rev.K 逐键完全一致**（语料中无 PRIMARY COMMENT 表） |
| 生产 14 表 | **零漂移** |
| 两份生产 fixture | 规则集合**精确相等** |
| 前十轮全部矩阵 | W / Z / Y / X / T / N / C / F、模糊 6000 条**全部保持通过** |
| 三版本 | sqlglot 29.0.0 / 30.14.0 / 30.17.0 上 P 组一致 |
| 全量回归 | 0 failed |


### 5.28 全量回归与审核物料校验器

```
基线   ：1355 passed, 29 skipped, 0 failed
Rev.B  ：1355 passed, 29 skipped, 0 failed        ← 逐项一致

verify_rules.py  基线 ：119 / 107 / 未覆盖 0 / 断言失败 3
verify_rules.py  Rev.B：119 / 107 / 未覆盖 0 / 断言失败 3   ← 逐项一致
```

3 条断言失败两侧同名同因（`01_naming_ddl.sql` 的 `R023_01`/`R098_01`/`R116_01` 期望多写了
`R036,R037`），是**先于本次改动存在的测试资产缺陷**。

> ✅ **零回归。**

### 5.29 第十一轮整改实测（Rev.M 新增）

> 全部实测在 **sqlglot 30.14.0**（发布锁定版）上取得，并在 **29.0.0 / 30.17.0** 上逐条复核一致。
> 复现命令：`python tests/test_parser_recovery_manifest.py`（或 `pytest tests/test_parser_recovery_manifest.py -q`）。

#### 5.29.1 BLOCK-11-01：MySQL 可执行注释

MySQL 的 `/*!50100 …*/` 是**可执行注释**：内容对 MySQL 是真语句，对 sqlglot 词法器却落在
`token.comments` 里，Rev.L 的规划器**完全看不见**。`mysqldump` 导出的二级分区正是这个形态，
因此这不是理论风险。Rev.M 新增两个函数：

- `_collect_executable_comments(toks)` —— 从全部 token 的 `comments` 中收集 `/*!…*/` payload；
- `_validate_executable_comments(toks, dialect)` —— **至多一个** payload；重新词法化后
  首 token 必须是 `PARTITION BY`；且必须被 `_consume_secondary_partition()` **完整消费到末尾**。

任一条不满足 → `_plan_recovery()` 返回 `None`，**整句失败关闭**。
普通 `/* */`、`--`、`#` 注释仍保持不可见，既不参与验证也不阻断恢复。

| 反例 / 正例 | Rev.L | Rev.M |
|---|---|---|
| `/*!50100 PARTITION BY RANGE() (…) */`（空方法参数） | plan=ACCEPT ❌ | **plan=REJECT** ✅ |
| `/*!50100 … PARTITION BY … PARTITION BY … */`（两条） | plan=ACCEPT ❌ | **plan=REJECT** ✅ |
| `/*!50100 EVIL OPTION */` | plan=ACCEPT ❌ | **plan=REJECT** ✅ |
| 两个可执行注释 | plan=ACCEPT ❌ | **plan=REJECT** ✅ |
| `/*!50100 PARTITION BY LIST (id) (PARTITION p0 VALUES IN (1) ENGINE = InnoDB) */` | plan=ACCEPT | **plan=ACCEPT → Create → gate=True** ✅ |
| 普通块注释内的伪分区 | —— | **不被当作可执行注释，正常恢复** ✅ |

> 注意 `_plan_recovery()` 返回的 `exec_comment_partition` 与 `had_partition` 是**两个独立标记**：
> 分区保真门禁只对**主 token 流里的**分区生效；可执行注释里的分区 sqlglot 不产生节点，
> 其完整性已由 `_validate_executable_comments()` 独立证明（具名 provenance）。

#### 5.29.2 BLOCK-11-02 / BLOCK-11-03 / MAJOR-11-02：表尾 typed atoms + capability profile

Rev.L 的四状态 FSM 含 `S2→S3` 与 `S3→S2` 回环，状态只表达"当前阶段"、不保留历史计数，
于是**双一级分布声明**被放行；`shardkey=noshardkey_allset` 又与普通 shardkey 归一成同一个 atom，
伪哨兵与广播再分区全部漏网。Rev.M 改为两步：**① 解析成 typed atoms；② 整个序列必须完整匹配一个具名 profile。**

atom 子类型：`LOCAL(<选项名>)` / `HASH_SHARDKEY` / `BROADCAST_SENTINEL` / `BROADCAST_KEYWORD` /
`DIST(<方法>)` / `PARTITION`。

| profile | 允许序列（`L*` = 任意多个 LOCAL） | provenance |
|---|---|---|
| `TARGET_CURRENT` | `L*` | 无分布声明的普通表 |
| `TARGET_CURRENT` | `L* HASH_SHARDKEY` | OFFICIAL hash 分片；CORPUS 生产 fixture 实测 |
| `TARGET_CURRENT` | `L* BROADCAST_SENTINEL` | OFFICIAL 广播表哨兵 |
| `TARGET_CURRENT` | `L* BROADCAST_KEYWORD` | TARGET_INSTANCE 广播表关键字形态 |
| `TARGET_CURRENT` | `L* HASH_SHARDKEY BROADCAST_KEYWORD` | **ADJ-6 characterization**：用户冻结的现状，**不代表 TDSQL 合法** |
| `TARGET_CURRENT` | `L* DIST` | OFFICIAL 一级 range/list 声明；目标实例 HASH 形态 |
| `TARGET_CURRENT` | `L* DIST PARTITION` | PROJECT_ACCEPTED：D5/T5 既有用例 |
| `LEGACY_PARTITION` | `L* HASH_SHARDKEY PARTITION` | OFFICIAL 二级分区原例 |
| `LEGACY_PARTITION` | `L* PARTITION DIST` | OFFICIAL 原例 `tb_sub_r_l` |
| `LEGACY_PARTITION` | `L* PARTITION` | OFFICIAL：仅二级分区、无一级声明 |
| ~~`NEW_SECONDARY`~~ | ~~`L* DIST TDSQL_PARTITION`~~ / ~~`L* HASH_SHARDKEY TDSQL_PARTITION`~~ | **登记于 `_TAIL_PROFILES_UNPROVEN`，刻意不参与匹配**：无目标实例证据、语料 0 例 → `unsupported_unproven` |

`_match_tail_profile()` 要求**整条序列完整消费完毕**才算命中，因此一级分布与二级分区天然各至多一个，
回环不可能存在；`BROADCAST_SENTINEL` 只出现在序列末尾，天然是终态。

| 反例 | Rev.L | Rev.M |
|---|---|---|
| `DIST → PARTITION → DIST` | plan=ACCEPT ❌ | **plan=REJECT** ✅ |
| `shardkey → PARTITION → DIST` | plan=ACCEPT ❌ | **plan=REJECT** ✅ |
| `PARTITION → DIST → PARTITION` | plan=ACCEPT ❌ | **plan=REJECT** ✅ |
| 哨兵 + `PARTITION BY` | plan=ACCEPT ❌ | **plan=REJECT** ✅ |
| `shardkey=(noshardkey_allset)` | plan=ACCEPT ❌ | **plan=REJECT** ✅ |
| `shardkey=(noshardkey_allset,id)` | plan=ACCEPT ❌ | **plan=REJECT** ✅ |
| 裸哨兵 `shardkey=noshardkey_allset` | ACCEPT | **ACCEPT → Create → gate=True** ✅ |
| `shardkey=sk PARTITION BY RANGE(...)` | ACCEPT | **ACCEPT**（LEGACY_PARTITION）✅ |
| `PARTITION BY LIST(...) TDSQL_DISTRIBUTED BY RANGE(sk)` | ACCEPT | **ACCEPT**（LEGACY_PARTITION）✅ |

#### 5.29.3 BLOCK-11-04：结构化数据类型规范表

`_TYPE_SPEC = 名 → 模式字符串` 是**双向失真**的：既误拒官方合法形态（因为 sqlglot 会规范化
`INTEGER→INT`、`NUMERIC→DECIMAL`、`REAL→FLOAT`、`DOUBLE PRECISION→DOUBLE`，并丢弃 `ZEROFILL`），
又误收明确越界形态（因为所有类型复用同一个"正整数"判据）。Rev.M 换成结构化规则表 `_TYPE_RULES`，
每型显式声明 `canonical / arity / 参数区间 / 族`：

- **别名在源侧就规范化**，且源侧与候选侧**共用同一个 `_consume_data_type()`** —— 从机制上消除两侧口径漂移；
- **各自的边界**：M、D、fsp、BIT、CHAR/VARCHAR、YEAR 分别使用自己的区间，不再复用 `_int_val`；
- **ENUM/SET** 强制括号 + 至少一个字符串字面量，指纹**保留逐值内容**而非只记数量；
- **类型属性按族开放**：数值族才能 `UNSIGNED/SIGNED/ZEROFILL`，字符族才能 `BINARY`，其余族一律拒绝；
- **`DOUBLE PRECISION`** 同时适配 tokenizer 的单 token 与双 token 两种表现（实测 30.14.0 是**单 token**，
  文本为 `"DOUBLE PRECISION"`，故 `_TYPE_RULES` 与 `_TYPE_MULTIWORD` 两处都登记）；
- **`ZEROFILL` / `SIGNED`** 实测被 sqlglot 回生成时丢弃，记入源指纹但比对时归一掉（`_TYPE_ATTRS_DROPPED_BY_AST`）。

**双向闭合矩阵（TY 组，例数见 §7.1d 生成表）实测：官方合法形态零回归，越界/非法形态零误放行。**

| 方向 | Rev.L | Rev.M |
|---|---|---|
| `INTEGER` / `NUMERIC(10,2)` / `REAL(10,2)` / `DOUBLE PRECISION(10,2)` | 误拒 ❌ | **ACCEPT** ✅ |
| `ENUM('a','b')` / `SET('a','b')` / `INT ZEROFILL` / `CHAR(0)` / `VARCHAR(0)` | 误拒 ❌ | **ACCEPT** ✅ |
| `DECIMAL(1,2)` / `DECIMAL(66,0)` / `DECIMAL(65,31)` / `BIT(65)` | 误收 ❌ | **REJECT_PLAN** ✅ |
| `CHAR(256)` / `VARCHAR(65536)` / `YEAR(999)` / 裸 `ENUM`、裸 `SET` | 误收 ❌ | **REJECT_PLAN** ✅ |
| `DATE UNSIGNED` / `VARCHAR(20) UNSIGNED` / `JSON BINARY` | 误收 ❌ | **REJECT_PLAN** ✅ |
| `DATETIME DEFAULT CURRENT_TIMESTAMP(7)` | 误收 ❌ | **REJECT_PLAN** ✅ |
| `POINT` / `MULTIPOINT` 等八种空间/`CHAR(n) BINARY` | 误拒 | **仍不能恢复 → 登记 KFN-3**（sqlglot 固有边界，见 Rev.M 修订说明） |

#### 5.29.4 BLOCK-11-05：候选 AST 结构守恒门禁

Rev.L 的门禁只比较列名与类型字符串，索引一律折叠成 `(IDX, None, None)`。**白盒反向鉴别证明**：
丢掉 `NOT NULL DEFAULT 7`、把 `UNIQUE u(id)` 换成 `KEY v(x)`、换成 `PRIMARY KEY(x)`，
门禁**全部返回 `True`** —— 也就是说它根本没有在守恒。Rev.M 改为**逐字段比较**：

| 维度 | 比较内容 |
|---|---|
| 表名 | 去引号、小写后相等 |
| 定义项 | **数量与顺序**逐项对齐；列定义不得与索引定义互换 |
| 列 | 列名、**规范类型形态**（`(canonical, 参数, 属性)`）、列约束集合 |
| 索引 | kind（PRIMARY/UNIQUE/NORMAL/FULLTEXT/SPATIAL）、索引名、**键列与前缀长度**、`USING` |
| 分区 | 原文有 `PARTITION BY` 时候选必须**恰好一个** `PartitionBy*` property |

被批准忽略的差异**逐条具名列出**（`_GATE_IGNORED_COL_CONSTRAINTS` / `_GATE_IGNORED_INDEX_OPTS`），
不允许"默默放宽"。

> **一处必须写明的 sqlglot 实现细节**：同一个 `USING BTREE` 依索引种类与书写位置，
> 会落在**三个不同的 arg** 上（30.14.0 实测）：
> `index_type=str`（UNIQUE 任意位置、KEY 的前置 USING）、
> `options=[IndexConstraintOption(using=…)]`（KEY 的后置 USING）、
> `include=IndexParameters(using=…)`（**PRIMARY KEY 的后置 USING**）。
> 只读 `index_type` 会把 `PRIMARY KEY (id) USING BTREE COMMENT 'pk'` 误判成"无 USING"从而误杀，
> 故新增 `_ast_index_using()` 统一扫描三处；options 逐项按 **arg 名**判定而非按节点类名判定，
> 因为 `IndexConstraintOption` 同时承载 `comment` / `key_block_size` 等其他选项。

**M 组变异断言（例数见 §7.1 全局计数表）全部通过**：正确候选零误杀；
丢约束 / 改类型 / 改类型长度 / 改列名 / 改索引 kind / 改索引名 / 改键列 / 丢前缀长度 /
丢 `USING` / **凭空多出 `USING`** / 增删定义项 / 换表名 / 定义项换序 / 抹掉分区 —— **全部被拒**。

#### 5.29.5 BLOCK-11-06：`COLUMN_FORMAT` / `ENGINE_ATTRIBUTE` 端到端恢复

**这一条是我方的错误。** Rev.K §5.25.2 与 A-141 写的"恢复 ✅"只在**规划层**验证过；
端到端仍报 E999，因为这两个属性没有被掩码，而 sqlglot 根本不认识它们。

Rev.M 采纳 O 的推荐方案：把它们作为**辅助掩码 span**（`_COL_CONSTRAINT_NOT_IN_AST`），
**只在已有 PRIMARY/UNIQUE COMMENT 或 TDSQL 方言主目标时才掩码**，`raw_sql` 不变，
完整结构记入 SourceFingerprint。已 `grep` 确认现有 **119 条规则中无任何消费者**依赖这两个属性。

同时按 O §9.2 更正官方画像：

| 项 | Rev.L | Rev.M |
|---|---|---|
| `_COLUMN_FORMAT_ENUM` | `FIXED/DYNAMIC/DEFAULT/COMPRESSED` ❌ | **`FIXED/DYNAMIC/DEFAULT`**（`COMPRESSED` 来自表级 `ROW_FORMAT`，已删除）✅ |
| 列级 `STORAGE` | 标为"建表页明示"的 official positive ❌ | **`unsupported_unproven`，失败关闭**（腾讯建表页列级清单未列出）✅ |
| `SECONDARY_ENGINE_ATTRIBUTE` | —— | **`unsupported_unproven`，失败关闭**（同上处置）✅ |

端到端实测：`COLUMN_FORMAT DYNAMIC` / `ENGINE_ATTRIBUTE='x'` 均为
`plan=ACCEPT → cand=Create → gate=True → 端到端 Create / 无 E999 / cols=1`。

#### 5.29.6 MAJOR-11-01：`FULLTEXT` / `SPATIAL` 裸形态入口

`_consume_index_definition()` 本来就能识别裸 `FULLTEXT` / `SPATIAL`，但 `_is_index_item()`
只有在下一 token 是 `KEY`/`INDEX` 时才把它分发给索引消费器 —— 于是官方合法的 `FULLTEXT (id)`
被送进**列定义消费器**并 plan=False，形成入口死分支。

Rev.M 让两者统一到同一个 `_index_lead()` 判据。实测：`FULLTEXT KEY f (a)` / `FULLTEXT INDEX f (a)` /
`FULLTEXT (a)` / `FULLTEXT f (a)` / `SPATIAL KEY s (g)` / `SPATIAL (g)` **全部恢复**；
`FULLTEXT` 缺括号**失败关闭**；反引号列名 `` `fulltext` `` 与 `` `spatial` `` **仍走列定义消费器**（反向鉴别）。

## 6. 与既有缺陷的交互 / ADJ 台账

### 6.1 与 ADJ-5 的交互（必须理解，本次不修）

`parsed.indexes` **不产出真正的 UNIQUE 条目**（`UniqueColumnConstraint` 路径在多数真实 DDL 下
返回空）——这是长期登记的 **ADJ-5**。DEF-1 的严重性正是两者叠加的结果：

> 真 UNIQUE 本来就不在 `parsed.indexes` 里（ADJ-5），假 UNIQUE 又填进去（DEF-1），
> 于是 `_iter_unique_indexes` 的早退判断被假货触发 → 真货连兜底正则都走不到。

**本次修好 DEF-1 后**：`parsed.indexes` 里不再有假 UNIQUE → `seen` 保持 False →
`_iter_unique_indexes` **正常回落到兜底正则** `_UNIQUE_IDX_RE` → 真唯一索引被正确检查
（探针 T6/T8 已证）。**即 DEF-1 修复本身就化解了这层叠加，无需触碰 ADJ-5。**

### 6.2 ADJ 台账更新

| 编号 | 内容 | 状态 |
|---|---|---|
| ADJ-1 解析降级漏审 | ✅ v1.6.2.0 已修 |
| ADJ-2 / ADJ-3 `tdsql_connector` | ⏸ Phase 2（ADJ-3 仍是真实缺陷） |
| ADJ-4 R077 宽松 OR | 🔒 用户决策：永久关闭 |
| ADJ-5 `parsed.indexes` 不产出 UNIQUE | ⏸ 未修；本次**不需要**修（见 §6.1） |
| ADJ-6 BROADCAST 冲突 | 🔒 用户决策：关闭 |
| ADJ-7 R116/R117/R118 对 HASH 不感知 | ⏸ 未修 |
| ADJ-8 `oracle_compat.clean_sql()` `--` 词法 | ⏸ 未修 |
| ADJ-9 解析器索引名未去引号 | ⏸ 未修（v1.6.2.1 登记） |
| **ADJ-10** | **`except` 路径未调用 `_regex_fallback_create_table_props()`**，导致"重试也救不回来"的语句仍会让 R003/R004/R005/R028 误报。该函数不感知字符串字面量，直接启用可能引入 R003 漏报，需专项评估 | 🆕 **本次登记，不修**（NG-8） |
| **ADJ-11** | **`CONSTRAINT c UNIQUE (col)` 形态的唯一索引完全不可见**——AST 落到 `Constraint` 节点，`_parse_create` 不处理；兜底正则 `_UNIQUE_IDX_RE` 要求 `unique\s+(key\|index)` 也不匹配。实测该形态下 R054 **完全不报（漏报）** | 🆕 **本次登记，不修**（NG-10） |
| **ADJ-13** | **R077 只把 `TDSQL_DISTRIBUTED BY HASH` 认作分片键声明，`RANGE` / `LIST` 未纳入**，导致这两类分片表被判「未声明分片键」。实测基线上同一张表（无 UNIQUE COMMENT）同样命中 R077，属 **v1.6.1.9 既有口径**，与本次改动无关 | 🆕 **本次登记，不修**（超出本次范围，且涉及 v1.6.1.9 冻结代码） |
| **ADJ-12** | E999 文案"可能是拉取截断/语法错误"对合法 MySQL 有误导 | 🆕 **本次登记，不修**（NG-9） |
| R036 只认两个字面名 | 🔒 用户决策：维持现状 |
| 字段级字符集检查 | 🔒 用户决策：本次不纳入（NG-7） |

---

## 7. 验收测试方案

### 7.1 唯一 case manifest（第十一轮 BLOCK-11-07）

**本轮起，全部用例、全部计数、全部分类只有一个来源：**

| 文件 | 职责 |
|---|---|
| `tests/parser_recovery_manifest.py` | **唯一 case manifest**。每条用例含稳定 `cid`、SQL、`klass`（判据）、`prov`（证据来源）、`note`（理由）与判据参数 |
| `tests/test_parser_recovery_manifest.py` | 参数化 pytest：逐条执行 manifest，判据完全来自 manifest，本文件不含任何用例数据 |
| `tests/manifest_doc.py` | 从 manifest **生成**下方全部表格与计数 |
| `tests/codestat.py` | 从最终补丁生成 §3.4 的规模表、函数清单与唯一性检查 |

> ⚠️ **禁止在任何章节人工维护第二份用例数量。** 本节以下所有数字都是
> `python tests/manifest_doc.py` 的输出，改用例只改 manifest，重跑本命令即可。
> 施工后以 `pytest --collect-only -q` 的实际收集数为最终证据，要求**零 skip**。

#### 7.1.0 分类语义（`klass`）

```text
pos                   必须恢复：规划器接受 → 候选 AST 为 Create → 结构守恒门禁通过 → 无 E999
neg                   必须失败关闭：token 规划器**先行拒绝**，且最终 AST 不得为 Create
                      （不能只依赖候选 parser 或 AST 门禁恰好拒绝）
pos_known             TDSQL 官方合法、但 sqlglot 当前解析不了 → 必须失败关闭，
                      单独计入已知假阴性 KFN-A，不得混进"非法反例"口径
unsupported_unproven  无 TDSQL/目标实例证据 → 必须失败关闭（KFN-B），
                      既不冒充合法，也不冒充非法
characterization      用户已冻结的表征行为（ADJ-6），锁定当前结论，**不代表 TDSQL 合法**
ruleset               断言规则命中集合**精确相等**（生产 fixture 回放）
spans                 断言剥离 span 数量 + **越界改写字符数 == 0** + 长度恒等
contract              断言 sqlglot AST 契约；上游升级破坏该假设时必须显式失败
```

> ⚠️ **期望值一律由 TDSQL 官方规范 / 目标实例契约推导**；当前主干的行为只记入
> `baseline_observation`，**不参与 pass/fail 判定**（第九轮 BLOCK-X1、第十轮 MAJOR-J1）。

#### 7.1.1 各组判据要点（不可退化的硬约束）

| 组 | 不可退化的判据 |
|---|---|
| **A** | A9 断言 `UNIQUE KEY`→`UniqueColumnConstraint`、`PRIMARY KEY`→`exp.PrimaryKey`、`FULLTEXT/SPATIAL KEY`→`IndexColumnConstraint` 且 `kind` 分别为 `'FULLTEXT'/'SPATIAL'`；断言消息必须打印实际 `sqlglot.__version__`。A1~A8 **不含索引 COMMENT**，本就无需恢复，故不断言 `plan` |
| **B** | 每例断言 `parse_error` 为空、`len(columns) > 0`、且 **`raw_sql` 逐字符等于输入** |
| **C** | 断言**仍报原错误**；并在注释写明这是 sqlglot 能力边界——去掉 COMMENT 后 sqlglot 同样 ParseError |
| **D / N** | ① span 数 == 该语句中**真实**索引注释个数；② **越界改写字符数 == 0**；③ 改写前后**长度恒等** |
| **F** | ① 分别使用报告原上下文的 `instance_type`（6309 **分布式** / 6311 **集中式**），不得混用；② **原样读取** fixture 全文送审，不得在测试里过滤注释行；③ 必须用**精确集合相等**断言，不得退化为子集断言 |
| **T** | 除解析成功外，**规则命中集合必须与「同一张表去掉索引 COMMENT」完全相等**——这条相等断言证明恢复**没有引入任何自己的口径** |
| **X** | **字段级精确断言**，不得退化为"与去掉 COMMENT 的结果相等"这类同源对照：① 列名序列精确相等；② 目标列注释逐字相等；③ `DEFAULT` 值保持；④ `raw_sql` 逐字符等于输入 |
| **Y / Z** | Z1/Z3 的断言必须包含"**仍报 E999**"，只断言 `span==0` 不够——Rev.E 正是在 span 层面看着正常、却在最终结论上吞掉了 E999 |
| **W** | W1 **必须按路径分别断言最终 AST 类型**：带 UNIQUE COMMENT → `ast is None`（E999 保留）；不带 → 仍 `exp.Command`（**不得升级为 `Create`**），不能统一写成"应报 E999" |
| **M** | 正确候选必须过门禁（不得误杀）；每个定向变异候选必须被拒（不得漏放） |

<!-- 本节由 tests/manifest_doc.py 从 tests/parser_recovery_manifest.py 生成，请勿手改 -->

**§7.1 主用例表**

| 子组 | 例数 | 说明 | 分类构成 |
|---|---:|---|---|
| **A** | 9 | DEF-1 索引类型判据 + AST 契约 | pos×8  contract×1 |
| **B** | 12 | DEF-2 正向恢复 | pos×12 |
| **C** | 4 | DEF-2 产品边界（sqlglot 能力边界） | pos_known×4 |
| **D** | 6 | 负向 / 防次生灾害 | spans×6 |
| **E** | 4 | 失败关闭 | neg×4 |
| **F** | 2 | 生产回放（精确规则集合） | ruleset×2 |
| **T** | 8 | TDSQL 方言组合 | pos×8 |
| **N** | 5 | 作用域负向 | spans×5 |
| **X** | 40 | 方言尾子句安全交叉矩阵 | pos×40 |
| **Y** | 20 | 方言语法严格性与语句边界 | pos×7  neg×10  spans×3 |
| **Z** | 22 | 方法参数与表名精确形态 | pos×11  neg×10  unsupported_unproven×1 |
| **W** | 28 | 目标上下文完整性 | pos×10  neg×15  unsupported_unproven×3 |
| **合计** | **160** | —— | pos×96  neg×39  pos_known×4  unsupported_unproven×4  ruleset×2  spans×14  contract×1 | 

**§7.1a H 组**

| 子组 | 例数 | 说明 | 分类构成 |
|---|---:|---|---|
| **H1** | 11 | key_part 非法 | neg×11 |
| **H2** | 5 | key_part 官方合法 | pos×5 |
| **H2b** | 3 | key_part 含 ASC/DESC | pos×3 |
| **H3** | 16 | 分区子句非法 | neg×16 |
| **H4** | 6 | 官方二级分区 Range/List | pos×6 |
| **H4c** | 2 | 官方合法但 sqlglot 不支持 | pos_known×2 |
| **H4b** | 8 | 官方未列的分区方法 | neg×8 |
| **H5** | 22 | 表选项值非法 | neg×22 |
| **H6** | 15 | 表选项官方合法 | pos×15 |
| **H6b** | 8 | 表选项无证据 | unsupported_unproven×8 |
| **合计** | **96** | —— | pos×29  neg×57  pos_known×2  unsupported_unproven×8 | 

**§7.1b P 组（DEF-3）**

| 子组 | 例数 | 说明 | 分类构成 |
|---|---:|---|---|
| **P1** | 8 | PRIMARY COMMENT 官方合法 | pos×8 |
| **P2** | 6 | PRIMARY COMMENT 非法近邻 | neg×6 |
| **合计** | **14** | —— | pos×8  neg×6 | 

**§7.1c R11 组（第十一轮复审反例）**

| 子组 | 例数 | 说明 | 分类构成 |
|---|---:|---|---|
| **R11-01** | 6 | 可执行注释（BLOCK-11-01） | pos×2  neg×4 |
| **R11-02** | 7 | 表尾迁移图（BLOCK-11-02） | pos×2  neg×3  unsupported_unproven×2 |
| **R11-03** | 5 | 广播哨兵分型（BLOCK-11-03） | pos×1  neg×3  characterization×1 |
| **R11-06** | 5 | 列属性（BLOCK-11-06） | pos×2  neg×1  unsupported_unproven×2 |
| **R11-M1** | 9 | FULLTEXT/SPATIAL 入口（MAJOR-11-01） | pos×8  neg×1 |
| **合计** | **32** | —— | pos×15  neg×12  unsupported_unproven×4  characterization×1 | 

**§7.1d TY 组（官方数据类型双向闭合矩阵）**

| 子组 | 例数 | 说明 | 分类构成 |
|---|---:|---|---|
| **TY-P** | 70 | 官方类型：必须恢复 | pos×70 |
| **TY-K** | 8 | 官方类型：sqlglot 不支持（KFN-3） | pos_known×8 |
| **TY-N** | 27 | 类型越界/非法：必须失败关闭 | neg×27 |
| **TY-D** | 3 | 官方类型：DEFAULT/ON UPDATE 精度 | pos×3 |
| **合计** | **108** | —— | pos×73  neg×27  pos_known×8 | 

**全局计数（唯一真源）**

| 项 | 值 |
|---|---:|
| manifest 用例总数 | **410** |
| 其中 `pos` | 221 |
| 其中 `neg` | 141 |
| 其中 `pos_known` | 14 |
| 其中 `unsupported_unproven` | 16 |
| 其中 `characterization` | 1 |
| 其中 `ruleset` | 2 |
| 其中 `spans` | 14 |
| 其中 `contract` | 1 |
| 变异门禁断言（5 套） | **28** |
| 模糊测试（seed=20260826） | **6000** |

**证据来源分布**

| provenance | 例数 |
|---|---:|
| `OFFICIAL` | 242 |
| `CORPUS` | 69 |
| `REVIEW_11` | 42 |
| `PROJECT_ACCEPTED` | 28 |
| `SQLGLOT_LIMIT` | 16 |
| `TARGET_INSTANCE` | 12 |
| `USER_DECISION` | 1 |

**已知假阴性 / 未证实能力登记（由 manifest 生成）**

| 类别 | cid | 形态 | 理由 |
|---|---|---|---|
| KFN-A（官方合法、暂不支持） | C-01 | `函数键值 ((lower(a)))` | 去掉 COMMENT 后 sqlglot 同样 ParseError → 非剥离器缺陷 |
| KFN-A（官方合法、暂不支持） | C-02 | `VISIBLE` | 去掉 COMMENT 后 sqlglot 同样 ParseError → 非剥离器缺陷 |
| KFN-A（官方合法、暂不支持） | C-03 | `KEY_BLOCK_SIZE` | 去掉 COMMENT 后 sqlglot 同样 ParseError → 非剥离器缺陷 |
| KFN-A（官方合法、暂不支持） | C-04 | `USING 前置于键值列表` | 去掉 COMMENT 后 sqlglot 同样 ParseError → 非剥离器缺陷 |
| KFN-B（未证实能力） | Z-15 | `Z2 BROADCAST COMMENT='x'（哨兵后接表选项）` | BROADCAST 是终态原子：其后不再接任何表选项。语料 197 条与生产 14 表出现 0 次，无 TDSQL 官方证据 → 失败关闭（Rev.M 统一口径，撤销 Rev.L 正文的 pos 表述） |
| KFN-B（未证实能力） | W-19 | `W2 CHECKSUM=1 + BROADCAST（无 TDSQL 证据）` | CHECKSUM 无 TDSQL 官方证据、语料 0 例 → 失败关闭 |
| KFN-B（未证实能力） | W-27 | `W6 INDEX DIRECTORY='/p' + BROADCAST（带 UK COMMENT）` | sqlglot 本就不支持 INDEX DIRECTORY，两条路径均与主干一致 |
| KFN-B（未证实能力） | W-28 | `W6 INDEX DIRECTORY='/p' + BROADCAST（无 UK COMMENT）` | sqlglot 本就不支持 INDEX DIRECTORY，两条路径均与主干一致 |
| KFN-A（官方合法、暂不支持） | H4c-01 | `RANGE+MAXVALUE 兜底分区 带UK` | KFN-1（用户 2026-08-26 批准）：sqlglot 30.x 对 MAXVALUE ParseError，语料/生产 0 例 |
| KFN-A（官方合法、暂不支持） | H4c-02 | `RANGE+MAXVALUE 兜底分区 无UK` | KFN-1（用户 2026-08-26 批准） |
| KFN-B（未证实能力） | H6b-01 | `PACK_KEYS=1 带UK` | 无 TDSQL / 目标实例证据 → 失败关闭，不冒充合法也不冒充非法 |
| KFN-B（未证实能力） | H6b-02 | `PACK_KEYS=DEFAULT 带UK` | 无 TDSQL / 目标实例证据 → 失败关闭，不冒充合法也不冒充非法 |
| KFN-B（未证实能力） | H6b-03 | `CHECKSUM=1 带UK` | 无 TDSQL / 目标实例证据 → 失败关闭，不冒充合法也不冒充非法 |
| KFN-B（未证实能力） | H6b-04 | `KEY_BLOCK_SIZE=8 带UK` | 无 TDSQL / 目标实例证据 → 失败关闭，不冒充合法也不冒充非法 |
| KFN-B（未证实能力） | H6b-05 | `AVG_ROW_LENGTH=100 带UK` | 无 TDSQL / 目标实例证据 → 失败关闭，不冒充合法也不冒充非法 |
| KFN-B（未证实能力） | H6b-06 | `MAX_ROWS=1000 带UK` | 无 TDSQL / 目标实例证据 → 失败关闭，不冒充合法也不冒充非法 |
| KFN-B（未证实能力） | H6b-07 | `MIN_ROWS=1 带UK` | 无 TDSQL / 目标实例证据 → 失败关闭，不冒充合法也不冒充非法 |
| KFN-B（未证实能力） | H6b-08 | `DELAY_KEY_WRITE=1 带UK` | 无 TDSQL / 目标实例证据 → 失败关闭，不冒充合法也不冒充非法 |
| KFN-B（未证实能力） | R11-02-05 | `NEW_SECONDARY：DIST + TDSQL_PARTITION BY RANGE` | 腾讯新版二级分区语法：无目标实例证据、语料 0 例 → 已具名登记为 NEW_SECONDARY profile 但不放行 |
| KFN-B（未证实能力） | R11-02-06 | `NEW_SECONDARY：shardkey + TDSQL_PARTITION BY LIST` | 同上 |
| KFN-B（未证实能力） | R11-06-03 | `SECONDARY_ENGINE_ATTRIBUTE='x'` | 腾讯官方建表页列级清单未列出（与列级 STORAGE 同处置）；语料 0 例 → 失败关闭 |
| KFN-B（未证实能力） | R11-06-04 | `列级 STORAGE DISK（NDB 专属，非 InnoDB 官方枚举）` | 无 TDSQL/目标实例证据，语料 0 例 → 失败关闭 |
| KFN-A（官方合法、暂不支持） | TY-K-01 | `CHAR(10) BINARY` | KFN-3：sqlglot 三版一致 ParseError，修复前后行为完全一致 |
| KFN-A（官方合法、暂不支持） | TY-K-02 | `POINT` | KFN-3：sqlglot 三版一致 ParseError，修复前后行为完全一致 |
| KFN-A（官方合法、暂不支持） | TY-K-03 | `LINESTRING` | KFN-3：sqlglot 三版一致 ParseError，修复前后行为完全一致 |
| KFN-A（官方合法、暂不支持） | TY-K-04 | `POLYGON` | KFN-3：sqlglot 三版一致 ParseError，修复前后行为完全一致 |
| KFN-A（官方合法、暂不支持） | TY-K-05 | `MULTIPOINT` | KFN-3：sqlglot 三版一致 ParseError，修复前后行为完全一致 |
| KFN-A（官方合法、暂不支持） | TY-K-06 | `MULTILINESTRING` | KFN-3：sqlglot 三版一致 ParseError，修复前后行为完全一致 |
| KFN-A（官方合法、暂不支持） | TY-K-07 | `MULTIPOLYGON` | KFN-3：sqlglot 三版一致 ParseError，修复前后行为完全一致 |
| KFN-A（官方合法、暂不支持） | TY-K-08 | `GEOMETRYCOLLECTION` | KFN-3：sqlglot 三版一致 ParseError，修复前后行为完全一致 |

### 7.2 需修订的既有测试

**预期为无。** 实测全语料除两条目标 fixture 外零漂移、全量回归零变化。
若施工中出现既有测试失败，**停工复核**，不得改测试迁就代码。

### 7.3 回归门槛（准出条件）

| 门槛 | 要求 |
|---|---|
| G-1 | `pytest tests/` 全量：**原有全部用例保持通过、0 failed**。⚠️ **门槛只有 `0 failed` 这一条**——不同环境的 passed/skipped 分布不同，**任何章节都不得硬编码**（我方本环境实测 `1355 passed / 29 skipped`，仅作参考、不作门槛）。另需新增 §7.1 manifest 的实际收集数，全部通过 |
| G-2 | `test_r077_r054_tdsql_syntax.py` **45 passed** |
| G-3 | `test_parser_tdsql_dialect_fallback.py` **14 passed** |
| G-4 | `test_r061_index_name_quoting.py` **12 passed** |
| G-5 | 新增 `tests/test_parser_recovery_manifest.py`：`pytest --collect-only -q` 的实际收集数**全通过，零 skip**；收集数必须等于 `tests/parser_recovery_manifest.py` 的 `len(CASES)` 加变异断言数（**第十一轮 BLOCK-11-07**：manifest 是唯一真源，任何差值都说明有用例未被执行） |
| G-6 | `verify_rules.py`：119 / 107 / 未覆盖 0 / 断言失败 **3**（与基线同名同因） |
| G-7 | 全语料（197 条语料语句 + 生产 14 表，去重后 **201 条**）× 119 规则：**逐键零漂移**；两个目标 fixture 单列，按预期各变化 1 处（6309 去掉 R054 误报；6311 去掉 E999 与 R003/R004/R005/R028 误报、补回 R036/R037） |
| G-8 | 生产 14 表回放**零漂移** |
| G-9 | 全语料索引 `type` 分布 = `{'NORMAL': 61}`；解析失败语句数 = **13** |
| G-10 | F1/F2 **精确集合相等**通过 |
| **G-13** | **T 组 8 例全通过**（T7/T8 已撤销），其中 T1~T6 的「与去掉 COMMENT 结论相等」断言必须成立 |
| **G-14** | **N 组 5 例 span 数全部为 1** |
| **G-15** | **X 组 40 例全通过**（字段级精确断言），且 `test_parser_tdsql_dialect_fallback.py` 仍 **14 passed** |
| **G-16** | 代码中**不得再出现** `_TDSQL_DIALECT_RE`（注释性说明除外），`grep` 确认无 `.sub(` 形式的 SQL 全局改写 |
| **G-17** | **Y 组 20 例全通过**；其中 Y16~Y19 四种合法方言形态必须全部恢复 |
| **G-18** | **依赖 pin 已落地**：`requirements.txt` 与 `pyproject.toml` 均为 **`sqlglot==30.14.0`**（精确锁定）；提交说明记录打包 wheel 实际版本；29.0.0 / 30.17.0 对照实测见 §5.21.6 |
| **G-19** | **Z 组 22 例全通过**；Z1/Z3（**带 UNIQUE COMMENT** 路径）必须断言 `ast is None` + E999，**不带 UNIQUE COMMENT 的同源输入必须断言仍是 `Command`**；Z2/Z4 必须断言合法形态仍恢复 |
| **G-20** | **统一规划器共用 `_tdsql_table_def_bounds()`**；`grep` 确认代码中不存在第二套建表头部定位逻辑 |
| **G-21** | **W 组 28 例全通过**；W1 必须按路径分别断言最终 AST 类型 |
| **G-23** | **H1 11 例 + H2 5 例**：非法 key-part 全部保住主干结论；合法 key-part 全部恢复为 `Create`（BLOCK-G1） |
| **G-24** | **H3 + H4 子组（例数见 §7.1a 生成表，禁止在此硬编码）**：残缺/尾随垃圾/内藏声明的分区子句全部失败关闭；**D5 的 `RANGE`+分区定义表两条路径仍 `Create`、`cols=3`**（BLOCK-G2） |
| **G-25** | **H5 + H6 子组（例数见 §7.1a 生成表，禁止在此硬编码）**：`ENGINE=123` / `ROW_FORMAT=123` 等非法取值全部失败关闭；官方/语料实证的合法取值全部恢复（BLOCK-G3） |
| **G-26** | **H 组用例（数量见 §7.1a）在 sqlglot 29.0.0 与 30.x 上结果逐条一致**（依赖矩阵，对应 O 的 H-5） |
| **G-27** | 五个消费器统一契约 `f(toks,i) -> 下一个下标 \| -1`；静态检查断言**扫描循环内不存在"看不懂就跳过"分支**、无重复函数定义、无不可达语句 |
| **I-1** | 第八轮 H1-1 ~ H1-5（外加我方补充的列缺类型、空主键）**全部保留原 E999**，不得变成 `Command`/`Create` |
| **I-2** | `USING HASH COMMENT` 按 TDSQL 官方口径失败关闭；`USING BTREE COMMENT` 正常恢复 |
| **I-3** | `PARTITION BY RANGE(,)` / `RANGE(+)` / `RANGE(id,)` 及分区定义结构反例全部失败关闭 |
| **I-4** | 进入恢复的语句，**原顶层定义项数 == 候选 AST 定义项数**；列类型与索引键列不得为空 |
| **I-5** | 原文存在 `PARTITION BY` 时，候选 AST 必须保留分区 property（`PartitionBy*`） |
| **I-6** | UNIQUE-COMMENT 单独路径、HASH 路径、BROADCAST 路径、Range/List **双子句顺序**路径均覆盖 |
| **I-7** | `ASC/DESC`、官方 LIST + partition `ENGINE`、官方 RANGE/LIST 分片定义表、多列 `shardkey=(a,b)` **按 pos 断言必须恢复**；`MAXVALUE` 按 `pos_known` 单独登记为 **KFN-1**，**不得归入非法 neg**。§5.21.5 已记录**剩余误报的确切条件、适用 sqlglot 版本、复检触发条件与用户批准（2026-08-26）** |
| **I-8** | TDSQL 官方二级分区示例进 fixture，并记录适用 TDSQL 内核版本 |
| **I-9** | 实际发布版本 `sqlglot==30.14.0` 通过全部新增专项、既有 71 例、全量 tests、生产 fixture 与语料漂移；29.0.0 / 30.17.0 作为对照实测记录 |
| **I-10** | 两个用户报告 fixture 仍达预期，规则集合继续用**精确相等**断言 |
| **J-1** | 非法用例的期望值**由 TDSQL 规范推导**，主干结果只作 `baseline_observation`；`neg` 一律断言"候选不得为 `Create`" |
| **J-2** | 列定义走 `_consume_column_definition()`：`VARCHAR()` / `DECIMAL(,2)` / `DECIMAL(10,)` / 重复 `DEFAULT` / `NULL NOT NULL` / 重复 `AUTO_INCREMENT` / 重复列 `COMMENT` **全部保留 E999** |
| **J-3** | **无 primary target 不得恢复**：仅含 ASC/DESC 或 partition option 掩码的语句必须保持原结论 |
| **J-4** | 一级分片定义带方法上下文：`HASH` 不得挂定义表；`RANGE` 只接 `LESS THAN`；`LIST` 只接 `IN`；官方一级分片定义表**禁止** `PARTITION` 前缀 |
| **J-5** | 二级分区只接受官方 Range/List；两个 `PARTITION BY` 失败关闭；**`MONTH`/`DAY` 等官方函数必须恢复**；负值边界 `LESS THAN (-1)` 必须恢复 |
| **J-6** | 表尾阶段模型：一级分布声明至多一个（`shardkey` 计入）；同名表选项不可重复；阶段只前进不回退；ADJ-6 作为唯一具名 characterization 例外 |
| **J-7** | 表选项按 provenance 白名单；`AUTO_INCREMENT=1.5` 失败关闭；无证据选项归 `unsupported_unproven`（H6b 8 例）**不冒充合法也不冒充非法** |
| **J-8** | 候选 AST 逐字段守恒：定义项数量与**逐项种类、列名**一致；分区**恰好一个**；`/*!50100 …*/` 例外须显式写明并由 F 组精确规则集合兜底 |
| **J-9** | 索引前缀长度必须是**正整数**；`USING` 与索引 `COMMENT` 各至多一次 |
| **J-10** | 静态检查：**无重复函数定义、无重复模块级常量**、无不可达语句、无 `want_dialect` 之类注释与实现不一致的开关 |
| **J-11** | H 组用例（数量见 §7.1a）在 **sqlglot 29.0.0 / 30.14.0 / 30.17.0** 三版结果逐条一致 |
| **J-12** | 生产 14 表零漂移；全语料 197 条恰好 2 条变化；两份 fixture 规则集合**精确相等** |
| **K-1** | 类型名走 `_TYPE_SPEC` 显式白名单；`RANGE`/`LIST`/`NULL` 等非类型 token 一律失败关闭 |
| **K-2** | 类型参数按类型模式校验：`VARCHAR(1,2,3)` / `INT(1,2)` / `DATE(1)` / `JSON(1)` / `DECIMAL(10,2,1)` 失败关闭；**官方 `DECIMAL(M,0)` / `DATETIME(0)` / `TIME(0)` 必须恢复** |
| **K-3** | `DEFAULT` 按官方字面量域：`foo` / `()` / `(,)` / `(SELECT 1)` 失败关闭；**`-1` / `+1` / 小数 / hex / bit / 布尔 / NULL / 时间函数必须恢复** |
| **K-4** | ~~官方列属性 `COLUMN_FORMAT` / `ENGINE_ATTRIBUTE` / 列级 `STORAGE` 必须恢复~~ → **Rev.M 更正（BLOCK-11-06）**：`COLUMN_FORMAT`（枚举仅 FIXED/DYNAMIC/DEFAULT，**删除 `COMPRESSED`**）与 `ENGINE_ATTRIBUTE` 作辅助掩码 span **端到端恢复**；列级 `STORAGE` 与 `SECONDARY_ENGINE_ATTRIBUTE` 腾讯官方建表页未列出，改判 `unsupported_unproven` **失败关闭** |
| **K-5** | 候选 AST 门禁比较**规范类型形态**：`JSON(1)`→`JSON` 这类漂移必须被发现 |
| **K-6** | 表尾走 `_TAIL_EDGES` 显式迁移表，每条边有 provenance；`shardkey=… ENGINE=…`、`BROADCAST…PARTITION`、`PARTITION…BROADCAST` 全部失败关闭 |
| **K-7** | 允许 **0 或 1 个且仅位于 EOF 前**的终止分号；分号后仍有真实 token 或出现第二个分号即失败关闭 |
| **K-8** | 表选项按官方清单：`ROW_FORMAT` 六值枚举与 `STATS_PERSISTENT` 必须恢复；无证据项继续失败关闭 |
| **K-9** | 二级分区函数收为 YEAR/MONTH/DAY 且参数恰好一个列；符号只修饰数值（`VALUES IN (-'x')` 失败关闭）；partition option 按 `[STORAGE] ENGINE → COMMENT` 顺序各至多一次 |
| **K-10** | 索引按 kind 分支：`PRIMARY KEY pk(id)`（PRIMARY 后带索引名）失败关闭；前后置 `USING` 共用 seen；索引 COMMENT 按 kind 分流。⚠️ **Rev.L/M 已更新**：`PRIMARY` 与 `UNIQUE` 同为掩码主目标（DEF-3，用户确认内网实际存在该形态），**KFN-2 已撤销**；普通 `KEY/INDEX` 与 `FULLTEXT` 的 COMMENT 原样保留（sqlglot 本就能解析） |
| **K-11** | **两份生产 fixture 的规则集合精确相等断言必须常驻回归**——它是本轮唯一抓住 `KEY … COMMENT` 回归的断言 |
| **K-12** | H 组数量由 §7.1a 参数化清单生成；准出以 `pytest --collect-only -q` 实际收集数为证，**不得硬编码任何单一环境的 passed/skipped 分布** |
| **L-1** | **DEF-3**：`PRIMARY KEY (col) COMMENT '…'` 必须恢复为 `Create`，且 `has_primary_key == True`、列信息完整；连带的 R003/R004/R005/R028 误报必须消失 |
| **L-2** | P1 的 8 种官方形态（含 PRIMARY 与 UNIQUE 双注释共存、与三种分布声明组合）全部恢复 |
| **L-3** | P2 的 6 例非法近邻全部失败关闭 —— 扩大恢复范围**不得**放松任何既有边界 |
| **L-4** | P 组在 sqlglot 29.0.0 / 30.14.0 / 30.17.0 三版结果一致 |
| **L-5** | 全语料与生产 14 表相对 Rev.K **逐键无变化**（语料中无 PRIMARY COMMENT 表，故本改动对既有数据零影响） |
| **G-22** | **代码中不存在"跳过未知 token"分支**：统一规划器的选项扫描循环里，未被白名单消费的 token 必须导致 `return None`；`grep` 确认无裸 `i += 1` 兜底 |
| **G-11** | **模糊测试（O §6.4-5）**：对 `_plan_recovery()` 随机组合引号、括号、逗号、注释、转义生成 ≥2000 条输入，断言**不抛异常**，且凡返回非 `None` 者必满足「长度恒等 + 差异全在 span 内」 |
| **G-12** | 提交说明记录实际 `sqlglot.__version__` |
| **M-1** | **可执行注释（BLOCK-11-01）**：`/*!…*/` 至多一个；payload 重新词法化后首 token 必须是 `PARTITION BY` 且被**完整消费到末尾**；`RANGE()` 空参、两条 `PARTITION BY`、`EVIL OPTION`、两个可执行注释全部失败关闭；合法 `/*!50100 PARTITION BY LIST … */` 必须恢复；普通 `/* */` 注释仍不可见、也不阻断恢复 |
| **M-2** | **表尾无回环（BLOCK-11-02）**：整条 atom 序列必须**完整匹配**一个具名 profile；`DIST→PARTITION→DIST`、`shardkey→PARTITION→DIST`、`PARTITION→DIST→PARTITION` 全部失败关闭；一级分布、二级分区各**独立计数、至多一个** |
| **M-3** | **广播哨兵精确分型（BLOCK-11-03）**：`BROADCAST_SENTINEL` 为终态原子；`shardkey=(noshardkey_allset)`、`shardkey=(noshardkey_allset,id)`、哨兵后接 `PARTITION BY` 全部失败关闭；裸哨兵必须恢复；ADJ-6 是**唯一**具名 characterization 例外 |
| **M-4** | **类型表双向闭合（BLOCK-11-04）**：TY 组矩阵（例数见 §7.1d 生成表）——官方合法形态**零回归**、越界/非法形态**零误放行**；源侧与候选侧共用同一个 `_consume_data_type()`；类型属性按族开放 |
| **M-5** | **门禁守恒（BLOCK-11-05）**：M 组变异断言全部通过——正确候选不得误杀，定向变异（丢约束 / 改类型 / 改 kind / 改索引名 / 改键列 / 丢前缀 / 丢或凭空多出 `USING` / 增删定义项 / 换表名 / 换序 / 抹掉分区）全部必须被拒 |
| **M-6** | **列属性端到端（BLOCK-11-06）**：`COLUMN_FORMAT` / `ENGINE_ATTRIBUTE` 断言到**最终 `Create` + 无 E999**，不得只在规划层验证就宣称"已恢复"；`grep` 确认 `_COLUMN_FORMAT_ENUM` 不含 `COMPRESSED` |
| **M-7** | **manifest 唯一真源（BLOCK-11-07）**：§7.1 全部表格与计数由 `python tests/manifest_doc.py` 生成，正文与 manifest 不一致即判失败；§10.1 八处矛盾逐条裁定已落地 |
| **M-8** | **FULLTEXT/SPATIAL 入口一致（MAJOR-11-01）**：`_is_index_item()` 与 `_consume_index_definition()` 共用 `_index_lead()`；裸 `FULLTEXT (a)` / `SPATIAL (g)` 必须恢复；`` `fulltext` `` / `` `spatial` `` 反引号列名必须仍走列定义消费器 |
| **M-9** | **capability profile（MAJOR-11-02）**：每条 SQL 完整匹配单一 profile，禁止跨 profile 拼接；`NEW_SECONDARY`（`TDSQL_PARTITION BY`）登记于 `_TAIL_PROFILES_UNPROVEN` 且**不参与匹配**，对应用例按 `unsupported_unproven` 断言失败关闭 |
| **M-10** | **规模数字自动生成（MINOR-11-02）**：§3.4 的行数、函数清单、唯一性检查由 `python tests/codestat.py` 生成；唯一性检查必须报告"模块级函数/常量无重复定义" |
| **M-11** | **照图施工可复现**：从本说明书的 10 个 `python` 代码块重建 `parser_legacy.py`，结果必须与提交文件**逐字节相同**（复现脚本见 §7.4） |
| **M-12** | **三版一致**：manifest 全量（用例 + 变异 + 模糊）在 **sqlglot 29.0.0 / 30.14.0 / 30.17.0** 上结果逐条一致 |

### 7.4 证据面交付物与复现命令（第十一轮 BLOCK-11-07 / MINOR-11-02）

本方案随设计一并交付四个可执行文件，**它们本身就是准出证据**：

| 文件 | 复现命令 | 产出 |
|---|---|---|
| `tests/parser_recovery_manifest.py` | —— | 唯一 case manifest（数据，无逻辑） |
| `tests/test_parser_recovery_manifest.py` | `pytest tests/test_parser_recovery_manifest.py -q` | 逐条执行；零 skip |
| `tests/manifest_doc.py` | `python tests/manifest_doc.py` | §7.1 全部表格与计数 |
| `tests/codestat.py` | `python tests/codestat.py <基线> <目标>` | §3.4 规模表、函数清单、唯一性检查 |

#### 7.4.1 「照图施工」可复现性自检（门槛 M-11）

本说明书的 10 个 `python` 代码块必须能**机械地**重建出提交的 `parser_legacy.py`。
施工方可用下列脚本自检（前 4 个块是「改动前」快照，用于定位；后 4 个是「改动后」替换）：

```bash
# ① 从干净主干拷一份
git stash && cp -r . /tmp/wt-verify && git stash pop
# ② 按说明书代码块重建
python tools/rebuild_from_design.py /tmp/wt-verify/backend/engine/parser/parser_legacy.py
# ③ 必须逐字节相同
diff -q backend/engine/parser/parser_legacy.py /tmp/wt-verify/backend/engine/parser/parser_legacy.py
```

> 我方已按此流程自检：**从本说明书重建的文件与提交文件逐字节相同**，
> 且重建后的树复跑 manifest 全量与 `pytest tests/` 结果与直接提交的树完全一致。

#### 7.4.2 三版 sqlglot 实测记录

| sqlglot | manifest 用例 | 变异断言 | 模糊 | 结论 |
|---|---|---|---|---|
| **30.14.0**（发布锁定） | 410 / 410 ✅ | 28 / 28 ✅ | 6000 条零违例 ✅ | 全绿 |
| 29.0.0（对照） | 410 / 410 ✅ | 28 / 28 ✅ | 6000 条零违例 ✅ | 全绿 |
| 30.17.0（对照） | 410 / 410 ✅ | 28 / 28 ✅ | 6000 条零违例 ✅ | 全绿 |

#### 7.4.3 漂移与回归原始结果

| 项 | 结果 |
|---|---|
| 全语料 + 生产 14 表（201 条语句 × 119 规则） | **逐键零漂移**，两侧解析异常均为 0 |
| `tests/fixtures/report_6309_kcfb_list_info.sql`（分布式） | 基线 `[R011,R018,R019,R036,R037,**R054**,R061,R065,R067,R104]` → Rev.M `[R011,R018,R019,R036,R037,R061,R065,R067,R104]`（**R054 误报消失**） |
| `tests/fixtures/report_6311_biz_tx_log.sql`（集中式） | 基线 `[**E999_SYNTAX_ERROR**,R003,R004,R005,R028]` → Rev.M `[R036,R037]`（**E999 与四条连带误报消失，补回正确结论**） |
| `pytest tests/` 全量 | **0 failed**（本环境 1355 passed / 29 skipped，不作门槛） |
| `test_r077_r054_tdsql_syntax.py` | 45 passed |
| `test_parser_tdsql_dialect_fallback.py` | 14 passed |
| `test_r061_index_name_quoting.py` | 12 passed |
| `test_parser.py` | 14 passed |
| `verify_rules.py` | 119 / 107 / 未覆盖 0 / 断言失败 **3**（与基线**同名同因**：`R023_01`/`R098_01`/`R116_01` 均为 R036+R037 漏触发） |

## 8. 风险与回滚

| 风险 | 等级 | 说明与缓解 |
|---|---|---|
| **改坏字符串字面量内容（Rev.A 的 BLOCK-1）** | **已消除** | 词法器令伪 SQL 结构上不可见；门禁①逐字符校验；6 例负向用例 + 4000 条模糊测试越界改写均为 0 |
| 接纳了不该接纳的候选 AST | **中→低（Rev.H 关闭）** | AST 门禁是**最后防线，不能替代 token 语法完整性**——第六、七轮连续证明目标片段合法、AST 门禁全过，语句整体仍可能非法。现由五个消费器在 token 层先行把关（表选项 / 索引选项 / 键值列表 / 分区子句 / 方言尾子句），门禁只做兜底。H 组用例（数量见 §7.1a）锁定 |
| 吃掉真语法错误 | **中→低（Rev.H 关闭）** | 第六轮（BLOCK-F1/F2）与第七轮（BLOCK-G1/G2/G3）各查出一批 `E999→Create`，说明此前的"低"评级证据不足。现由 W 组 28 例 + H 组用例（数量见 §7.1a）双版本锁定，判据为「rank(候选) ≤ rank(主干) 且 E999 不得消失」。边界见 §5.7 末尾与 §5.19 |
| 合法但 sqlglot 不支持的语法仍误报 | **已知边界** | §5.4 三类，显式声明为产品边界，失败关闭，不用字符串兜底伪造事实 |
| sqlglot 升级导致 AST 假设失效 | **中→低** | 白名单映射不会静默降级；A9 契约测试在升级时显式失败；§5.0 记录版本 |
| 丢失真索引类型 | **低** | A5 锁定真 FULLTEXT |
| 告警数量变化引发用户疑虑 | **需沟通** | gg78 由 5 条 ERROR 变为 2 条 INFO；gg77 少 1 条 WARNING。减少的**全部是误报**，另有 1 处漏报被补上 |
| **UNIQUE-COMMENT 与 TDSQL 方言组合仍失败** | **已消除** | 方言恢复串联；T1~T6 实测全部恢复 |
| **方言全局正则静默破坏 AST（BLOCK-C1）** | **已消除，且顺带修好一个生产在跑的缺陷** | 删除 `_TDSQL_DIALECT_RE`；两条入口统一 token 剥离器；X 组 40 例字段级精确断言全过（生产版本 36 例失败） |
| **sqlglot 版本漂移致 T5 失效** | **已决并纳入改动** | 实测下界 29.0.0；`requirements.txt` / `pyproject.toml` 均**精确锁定** `sqlglot==30.14.0`（§5.21.6、C-19、G-18、I-9） |
| **span 被错误批准（作用域越界）** | **已消除** | `at_def_start` + 定义列表闭合即停；5 类作用域负例 span 全为 1 |
| `UnboundLocalError` | **已知陷阱** | §3.2 红框；自验证断言 `except` 内存在 `ast = _retry_ast` 重绑 |
| **KFN-1：MAXVALUE 兜底分区 + UNIQUE COMMENT 仍误报 E999** | **已登记并经用户批准** | 受阻于 sqlglot 30.x 自身（非本方案所致）。语料 197 条 / 生产 14 表出现 **0 次**；确切代价、适用版本、复检触发条件见 §5.21.5。**移动依赖 pin 时须复测本条** |

**回滚**：5 个文件（解析器 1 + 依赖声明 2 + 版本号 2）、5 个解析改动点，`git revert` 单个 commit 即可完全回退。
无数据迁移、无配置变更、无接口变更、无前端联动。

---

## 9. 施工检查单（Q 逐项打勾）

- [ ] **C-1** 产品代码改 `backend/engine/parser/parser_legacy.py`（5 个改动点）+ `requirements.txt` / `pyproject.toml` 各 1 行依赖 pin + `VERSION` 与 `backend/config.py` 版本号（C-16），**共 5 个产品文件**；另按**附录 C** 逐字新建 4 个证据面文件（`tests/parser_recovery_manifest.py`、`tests/test_parser_recovery_manifest.py`、`tests/manifest_doc.py`、`tests/codestat.py`）
- [ ] **C-2** 五个改动点（含 import、删除旧正则）均按 §3 逐字落地，未做自由发挥
- [ ] **C-3** **Rev.A 的 `_UNIQUE_IDX_COMMENT_RE` 不得出现在代码中**（NG-0）——本次不是「改正则」，是「换实现」
- [ ] **C-4** ⚠️ 重试成功分支**同时**执行 `ast = _retry_ast` 与 `parsed.ast = ast`（§3.2 陷阱）
- [ ] **C-5** 失败路径（`else` 分支内）与改前**逐字一致**，仅整体缩进一层
- [ ] **C-6** 五道门禁一个不少（等长+差异仅在 span、`exp.Create`、`kind=='TABLE'`、表名同一性、**TDSQL 方言恢复串联**）
- [ ] **C-6b** 剥离器入口接受 `CREATE [TEMPORARY] TABLE`；`at_def_start` 状态存在且正确；定义列表闭合即 `break`
- [ ] **C-6c** **`_TDSQL_DIALECT_RE` 已删除**；两条恢复入口（首次 `Command` 重试、`except` 重试）**都**改用 `_plan_recovery()` + `_spans_only_diff()`
- [ ] **C-6d** except 路径做的是**两阶段 span 联合门禁**（`sql_clean → _final_sql` 的全部差异落在 `_all_spans` 内），不是只校验阶段一
- [ ] **C-7** 剥离器在词法异常 / 括号未闭合 / 非建表 / 无 span / span 越界时一律返回 `(None, [], "")`
- [ ] **C-8** 未新增第三方依赖；只新增 `from sqlglot.tokens import TokenType`
- [ ] **C-9** 规则层零改动：`ddl.py`/`index.py`/`distributed.py`/`dml.py`/`oracle_compat.py`
- [ ] **C-10** ✅ **确认 `_TDSQL_DIALECT_RE` 常量已从代码中删除**（仅允许出现在解释性注释里）；`_parse_unique_constraint()` 一字未动
- [ ] **C-11** 按**附录 C** 新建 `tests/parser_recovery_manifest.py` 与 `tests/test_parser_recovery_manifest.py`，`pytest --collect-only -q` 收集数**全通过、零 skip**（**计数由 manifest 自动生成，任何章节不得人工维护**；我方实测收集 **416** 项 = 410 条用例 + 5 套变异 + 1 条模糊）
- [ ] **C-12** F 组**原样读取**已提交的两个纯 DDL fixture（**不要过滤注释行**），6309 用**分布式**、6311 用**集中式**，且用**精确集合相等**断言
- [ ] **C-12b** **不得**给这两个 fixture 重新添加任何文件头注释
- [ ] **C-13** 未修改任何既有测试文件；若确需修改，**停工回报**
- [ ] **C-14** G-1 ~ G-27、I-1 ~ I-10、J-1 ~ J-12、K-1 ~ K-12、L-1 ~ L-5 与 **M-1 ~ M-12** 全部门槛逐条实测通过，提交说明中贴出实测数字
- [ ] **C-19** **依赖 pin**：`requirements.txt` 与 `pyproject.toml` 的 `sqlglot` 声明改为 **`sqlglot==30.14.0`**（精确锁定，第八轮 MAJOR-H2），并在提交说明记录打包 wheel 实际版本
- [ ] **C-20b** ⚠️ **统一规划器必须调用同一个 `_tdsql_table_def_bounds()`**，不得各写一套头部定位
- [ ] **C-20** ⚠️ **确认 `BY RANGE(...)` / `BY LIST(...)` 仍能恢复**——严格化时若写成"只认 `TokenType.VAR`"，这两种会静默回归（我实现时踩到过）
- [ ] **C-15** 导入自检：`python -c "from backend.engine.parser.parser_legacy import SQLParser, _plan_recovery"` 无异常
- [ ] **C-16** 版本号更新：`VERSION` 与 `backend/config.py` 的 `APP_VERSION`、`APP_DESCRIPTION` → `1.6.2.2`
- [ ] **C-17** 提交说明记录实际 `sqlglot.__version__`
- [ ] **C-21** **证据面自检**：`python tests/manifest_doc.py` 的输出与 §7.1 逐字一致；`python tests/codestat.py <基线> <目标>` 的输出与 §3.4 逐字一致。不一致时**以脚本输出为准**并更新正文，不得反向改脚本
- [ ] **C-22** **「照图施工」自检（门槛 M-11）**：按 §7.4.1 从本说明书的前 10 个 `python` 代码块重建 `parser_legacy.py`，结果与提交文件**逐字节相同**
- [ ] **C-23** **三版实测（门槛 M-12）**：manifest 全量在 sqlglot **29.0.0 / 30.14.0 / 30.17.0** 上结果逐条一致，发布锁定 30.14.0
- [ ] **C-24** **KFN 逐条落地**：KFN-1（`MAXVALUE`）、KFN-3（8 种 sqlglot 固有类型边界）在 manifest 中为 `pos_known`，**断言失败关闭**而非断言恢复；KFN-B 各项为 `unsupported_unproven`
- [ ] **C-25** **官方画像更正已落地**：`grep` 确认 `_COLUMN_FORMAT_ENUM` **不含 `COMPRESSED`**；列级 `STORAGE` 与 `SECONDARY_ENGINE_ATTRIBUTE` 失败关闭；`_TAIL_PROFILES_UNPROVEN` 存在且**不参与匹配**
- [ ] **C-18** 提交信息：`fix(v1.6.2.2): 索引类型误判与唯一索引注释解析崩溃修复`

---

## 附录 A：实测证据清单（Rev.M）

### A.1 Rev.A / Rev.B 阶段既有证据（沿用）

| 编号 | 证据 | 结论 |
|---|---|---|
| A-1~A-7 | gg77/gg78 复现、`str(col_def)` 打印、18 类 AST 枚举、暴露面探针、T8 漏报构造、gg78 消融、8 类 COMMENT 写法矩阵 | DEF-1/DEF-2 根因成立 |
| A-8 | 复现 O 第一轮 BLOCK-1（Rev.A 正则） | 命中 2 处，`column_comments['b']` 被污染 —— 指控成立 |
| A-9 | 复现 O 第一轮 BLOCK-2 | `exp.Create` 覆盖 `CREATE VIEW/INDEX/DATABASE` |
| A-10 | 复现 O 第一轮 6 类边界失败 | 全部复现 |
| A-11~A-12 | Rev.B 逐字符定位、`''` 反转义对照实验 | 越界改写 0；残留差异系 sqlglot 既有行为 |
| A-20 | sqlglot 对「列缺类型」的既有宽容度对照 | §5.7 边界非新开口子 |

### A.2 Rev.C 新增证据（本轮）

| 编号 | 证据 | 结论 |
|---|---|---|
| **A-22** | **复现 O 第二轮 BLOCK-B1**：Rev.B + `HASH/RANGE/LIST/BROADCAST` | **4/4 全部仍 E999、cols=0**；`shardkey=` 对照可恢复 —— 指控成立 |
| **A-23** | **复现 O 第二轮 BLOCK-B2a**：`CONSTRAINT uq UNIQUE (a) COMMENT` | Rev.B 返回 **2 处 span**，与 NG-10 自相矛盾 —— 指控成立 |
| **A-24** | **复现 O 第二轮 BLOCK-B2b**：两条语句拼接 | Rev.B 修改 **2 处 span**，却只接纳第一表 AST —— 指控成立 |
| **A-25** | **复现 O 第二轮 MAJOR-B1**：`CREATE TEMPORARY TABLE` | Rev.B 不变换、仍 E999；且 `is_temporary_table`/R024/R032/既有测试证明属既有产品域 —— 指控成立 |
| **A-26** | **复现 O 第二轮 MAJOR-B2a**：`UNIQUE KEY uk USING BTREE (a)` | 无 span；去 COMMENT 后 sqlglot 亦不支持 —— 应列入产品边界 |
| **A-27** | **复现 O 第二轮 MAJOR-B2b**：fixture 文件头 | 我加的中文文件头含全角括号，使 gg78 原样读取**多出 R104** —— 指控成立 |
| **A-28** | Rev.C T 组 10 例（TDSQL 方言组合） | 全部恢复；T1~T6 与「去掉 COMMENT」**规则结论完全相等** |
| **A-29** | Rev.C N 组 5 例（作用域负向） | span 数**全部为 1**，抹除的均为真实目标 |
| **A-30** | Rev.C C 组 4 例（产品边界） | 四类全部失败关闭；去 COMMENT 后 sqlglot 均不支持 |
| **A-31** | Rev.C 模糊测试 6000 条 | 抛异常 **0**；不变量违例 **0** |
| **A-32** | Rev.C F 组精确集合断言 | 6309 与 6311 均**精确相等** |
| **A-33** | RANGE/LIST 的 R077 基线对照 | 基线上同表去 COMMENT 后同样命中 R077 → 既有口径，登记 ADJ-13 |
| **A-34** | Rev.C 生产 14 表 + 全语料 197 条漂移 | 14 表**零漂移**；语料**恰好 2 条**变化，均为目标 fixture |
| **A-35** | Rev.C 全量回归 + `verify_rules.py` 双侧 | **1355 passed / 0 failed / 29 skipped**、119/107/0/3，**逐项一致** |
| **A-36** | **文档代码块自验证** | 各改动点代码块抽取施工到干净工作树，行为与实现完全一致 |

### A.3 Rev.D 新增证据（第三轮整改）

| 编号 | 证据 | 结论 |
|---|---|---|
| **A-37** | **复现 O 第三轮 BLOCK-C1 三反例** | 列被删、注释被改、伪片段被清空 —— 指控成立 |
| **A-38** | **在未打补丁的 v1.6.2.1 上复跑同三例** | **同样损坏** → 该缺陷**已在生产环境活跃**，非 Rev.C 引入 |
| **A-39** | **X 组 40 例交叉矩阵** | 生产版本 **36/40 失败**；Rev.D **40/40 通过** |
| **A-40** | Rev.D 对两条恢复入口的统一改造 | 首次 `Command` 重试路径上三反例同样恢复正确 |
| **A-41** | **sqlglot 版本二分**：26/27/28/29/30/30.12/30.14 | T5 真实下界 = **29.0.0**；BLOCK-C1 修复**与版本无关** |
| **A-42** | 既有方言回退 14 例 + R077/R054 45 例 + R061 12 例 | 全部 passed，未退化 |
| **A-43** | Rev.D 全语料 197 条 / 生产 14 表 / 全量回归 | 语料**恰好 2 条**变化；14 表**零漂移**；**1355 passed / 0 failed** |
| **A-44** | 自查 Rev.C 的 T7/T8 用例 | 尾子句写成 `shardkey=`，**从未触发**方言路径 —— O 的『同源错误对照』判断成立 |

### A.4 Rev.E 新增证据（第四轮整改）

| 编号 | 证据 | 结论 |
|---|---|---|
| **A-45** | 复现 BLOCK-D1a：缺 BY / 缺方法 / 缺 BY 有方法 三类非法 DDL | Rev.D 各得 1 span 并被**修成合法 `Create`** —— 指控成立 |
| **A-46** | 复现 BLOCK-D1b：`'TDSQL_DISTRIBUTED'` / `` `TDSQL_DISTRIBUTED` `` / `` `broadcast` `` | Rev.D 各得 1 span —— STRING/IDENTIFIER 确未被排除 |
| **A-47** | 复现 BLOCK-D1c：`COMMENT='TDSQL_DISTRIBUTED'` + 真实 HASH 尾子句 | Rev.D **阻断**真实恢复（`ast=Command`、`cols=0`）—— 指控成立 |
| **A-48** | 复现 BLOCK-D1d：`HASH+BROADCAST` / `HASH+RANGE` | Rev.D 各得 2 span 并被接纳 —— 指控成立 |
| **A-49** | 复现 BLOCK-D2a：CTAS + `CONCAT()` 括号 | Rev.D **同时删除** SELECT 列 `broadcast` 与真实尾子句，仍解析成 `Create` —— **CTAS 语义被静默改写**，指控成立 |
| **A-50** | 复现 BLOCK-D2b：两条语句拼接 | Rev.D 得 2 span、两条尾子句都被改，`parse_one` 返回 `Block` 被首次重试接纳 —— 指控成立 |
| **A-51** | **`Block` 的来源核查** | `sqlglot.parse_one()` 对多语句**原生返回 `Block`**（无方言语法时亦然）→ 属基线既有行为；Rev.E 的门禁在第一关 `isinstance(exp.Create)` 即拒绝 |
| **A-52** | **RANGE / LIST token 类型实测** | `HASH`→`VAR`、**`RANGE`→`TokenType.RANGE`**、**`LIST`→`TokenType.LIST`**；按"只认 VAR"实现会让二者回归失败（实际发生过） |
| **A-53** | Rev.E Y 组 20 例（严格性 + 边界 + 合法形态） | **全部通过**：13 类非法/越界 span 全为 0；4 种合法形态全部恢复 |
| **A-54** | Rev.E X 组 40 例 / T 组 / N 组 / C 组 / F 组 / 模糊 6000 条 | 全部保持通过，无回归 |
| **A-55** | Rev.E 专项 71 例 + 生产 14 表 + 全语料 197 条 + 全量回归 | 71 passed；14 表**零漂移**；语料**恰好 2 条**变化；**1355 passed / 0 failed / 29 skipped** |
| **A-56** | `RuleChecker.audit_file()` 拆分核查 | 经 `_split_sql_file()` 先行拆分 → 多语句进入 `parse()` 属边缘路径 |

### A.5 Rev.F 新增证据（第五轮整改）

| 编号 | 证据 | 结论 |
|---|---|---|
| **A-57** | 复现 BLOCK-E1：7 种非法方法参数 | Rev.E 全部得 1 span、`ast=Create`、**E999 被吞** —— 指控成立 |
| **A-58** | **主干对照**：同 7 种输入在 v1.6.2.1 上 | 均明确报 `E999_SYNTAX_ERROR` → 确系 Rev.E 吞掉，非"本就没有" |
| **A-59** | 复现 BLOCK-E2：单/双引号表名 × UNIQUE COMMENT × HASH | Rev.E 一律变成 `Create`、E999 消失 —— 指控成立 |
| **A-60** | **冻结契约核查**：`_extract_tdsql_hash_key()` / `_TDSQL_HASH_RE` | 只提取**单个**分片键；仓内语料无多字段/表达式形态 → "恰好一个标识符"的收紧与 v1.6.1.9 契约一致 |
| **A-61** | **BROADCAST 位置实测**（曾考虑要求"必须在末尾"） | 仓内语料末尾 **0** 处、中间 **8** 处（`BROADCAST COMMENT='x'` 等）→ 该收紧会打断合法用例，**未采纳** |
| **A-62** | Rev.F Z 组 22 例 | 全通过：Z1 7 例非法参数仍报 E999；Z2 8 例合法形态全恢复；Z3 3 例 STRING 表名仍报 E999；Z4 4 例合法表名全恢复 |
| **A-63** | Rev.F 对前四轮全部矩阵复跑 | Y 组 20 例、X 组 40 例、T/N/C/F、模糊 6000 条 **全部保持通过，无回归** |
| **A-64** | Rev.F 专项 71 例 + 生产 14 表 + 全语料 197 条 + 全量回归 | 71 passed；14 表**零漂移**；语料**恰好 2 条**变化；**1355 passed / 0 failed / 29 skipped** |
| **A-65** | 统一规划器头部定位器合并 | 均调用 `_tdsql_table_def_bounds()`，代码中不存在第二套头部逻辑 |

### A.6 Rev.G 新增证据（第六轮整改）

| 编号 | 证据 | 结论 |
|---|---|---|
| **A-66** | 复现 BLOCK-F1：12 类未知/不完整表选项 × UNIQUE COMMENT | Rev.F 全部得 span 并改写，**其中 6 例最终结论与主干不一致**（E999 被吞或 AST 类型改变）—— 指控成立 |
| **A-67** | **主干对照**：同 12 类输入在 v1.6.2.1 上逐条记录最终 `ast` 类型与错误码 | 建立**逐路径**期望值（`Command` / `Create` / E999 三种），不再用"一律 E999"的粗口径 |
| **A-68** | **我自己的复评误判**（自我批评） | W 组首跑 7 例"失败"，查证后确认**是我的期望写错**：无 UNIQUE COMMENT 的路径主干本就是 `Command`（无 E999 可保）；`CHECKSUM=1` 会让 sqlglot 自身降级。期望改为**按路径断言最终 AST 类型** |
| **A-69** | 复现 BLOCK-F2：`USING COMMENT 'x'` / `COMMENT 'x' USING`（缺 BTREE/HASH） | Rev.F 得 span=1、AST 变 `Create`、**E999 消失**；主干为 E999 —— 指控成立 |
| **A-70** | **表选项白名单取值实测** | 逐条量取仓内语料 + 构造样本的 token 类型，确定 `_TBL_OPT_VALUE_VAR` / `_TBL_OPT_VALUE_NUM` 两张表；`DEFAULT`/`CHARACTER_SET`/`COLLATE`/`COMMENT`/`AUTO_INCREMENT` 为 sqlglot 专有 token 类型，单独分支处理 |
| **A-71** | **`PARTITION_BY` 终止实测** | 分区子句形态开放（`RANGE`/`LIST`/`HASH` + 括号体 + `PARTITION p0 VALUES ...`），无法穷举白名单 → 扫描遇 `PARTITION_BY` **立即终止**，其后不再剥离；D5 用例 `cols=3` 未回归 |
| **A-72** | Rev.G W 组 28 例（逐条实测，含 W6 `INDEX DIRECTORY='/p'` 2 例） | **0 失败**：12 例未知表选项 + 3 例未知索引选项**失败关闭且最终结论与主干逐条一致**；8 例合法表选项 + 2 例合法索引选项全部恢复；PARTITION BY 用例不回归 |
| **A-73** | Rev.G 对前五轮全部矩阵复跑 | Z 组 22 例、Y 组 20 例、X 组 40 例、T/N/C/F、模糊 6000 条（0 崩溃、0 不变量违例）**全部保持通过，无回归** |
| **A-74** | Rev.G 专项 71 例 + 生产 14 表 + 全语料 197 条 + 全量回归 | 71 passed；14 表**零漂移**；语料**恰好 2 条**变化（均为本次目标 fixture）；**1355 passed / 0 failed / 29 skipped** |
| **A-75** | **MINOR-F1 死代码核查（新增自验证项）** | `except` 分支内 `return parsed` 出现次数由 **3 → 1**；本版起自验证增加「代码块无重复片段」检查 |

---

### A.7 Rev.H 新增证据（第七轮整改）

| 编号 | 证据 | 结论 |
|---|---|---|
| **A-76** | 复现 BLOCK-G1：11 类非法 key-part × UNIQUE COMMENT | Rev.G 上 7 类核心反例全部 `E999 → Create`（主干均 E999）—— 指控成立 |
| **A-77** | 复现 BLOCK-G2：`PARTITION BY` / `PARTITION BY DEFAULT` × 带 UNIQUE COMMENT | Rev.G 上 `E999 → Create`（主干 E999）—— 指控成立。`HASH()` / `HASH(,)` 因 sqlglot 自身报错而未泄漏 |
| **A-78** | 复现 BLOCK-G3：`ENGINE=123` / `ROW_FORMAT=123` / `ROW_FORMAT='x'` / `shardkey=123` | Rev.G 上全部 `E999 → Create` —— 指控成立 |
| **A-79** | **key-part token 全量普查** | 仓内语料 + 生产 fixture 的索引键值列表内**只出现** `VAR` / `IDENTIFIER` / `COMMA`；唯一 1 个 `NUMBER` 经定位是列名为 `key` 的列定义（扫描器误命中），**不是 key-part** |
| **A-80** | **`PARTITION BY` token 全量普查** | 作为 token 出现仅 **1 处**，且该语句无方言尾子句、无 UNIQUE COMMENT，不走恢复链 |
| **A-81** | **生产 mysqldump 分区子句的词法行为** | gg78 的 `/*!50100 PARTITION BY LIST ... */` 被 sqlglot 词法器**整体跳过**：定义列表收尾后只剩 13 个 token。故 BLOCK-G2 的整改对生产 fixture **零影响** |
| **A-82** | **表选项 名→值 全量普查** | 实际只出现 `ENGINE=VAR`(78) / `DEFAULT CHARSET=VAR`(78) / `COLLATE=VAR`(26) / `COMMENT=STRING` / `AUTO_INCREMENT=NUMBER`(8) / `SHARDKEY=VAR`(20)。Rev.G 白名单里的 `ROW_FORMAT` / `CHECKSUM` / `STATS_PERSISTENT` 等**语料中一次都没出现**——属我臆测项，本版改为按官方取值精确建模而非放宽 |
| **A-83** | **`ROW_FORMAT` 取值 token 类型实测** | `DEFAULT`→`TokenType.DEFAULT`、`FIXED`→**`TokenType.DECIMAL`**、其余→`VAR`。故枚举必须按**文本**匹配，按 token 类型匹配会误拒两个合法取值 |
| **A-84** | **key-part 的 `ASC`/`DESC` 实测** | `UNIQUE KEY uk (id ASC)` 去掉 COMMENT 后 **sqlglot 自身即 ParseError** → 属产品边界（§5.4 同类），非本次收紧 |
| **A-85** | **分区形态的 sqlglot 原生能力实测** | `RANGE (expr) (PARTITION ... VALUES LESS THAN ...)` 可解析；`HASH+PARTITIONS n` / `LINEAR HASH` / `KEY(col)` **降级为 `Command`**；`RANGE COLUMNS` 与 `LIST (...) (PARTITION ... VALUES IN ...)` **ParseError** |
| **A-86** | **O 的"保守方案"实测代价** | 遇 `PARTITION_BY` 一律失败关闭会让 D5 无 UK 路径由主干的 `Create`/`cols=3` 降为 `Command`/`cols=0` —— **真实覆盖面损失**，故未采纳；改用其推荐方案（完整消费）后 **D5 零损失** |
| **A-87** | **我自己的两处期望值错误**（自我批评） | H 组首跑 16 红：14 条系判据错（主干"无 UK"路径的 `Create` 本就是旧正则假成功），2 条系用例归类错（`LIST+分区定义表` sqlglot 自身 ParseError）。已改为**单调不变松**判据，期望值一律由主干实测得出 |
| **A-88** | Rev.H H 组用例（数量见 §7.1a）（sqlglot 30.14.0） | **失败 0**；其中较主干**收紧 14 例**（非法 DDL 由假 `Create` 降为 `Command`），**覆盖面损失 0 例** |
| **A-89** | Rev.H H 组用例（数量见 §7.1a）（sqlglot 29.0.0，依赖下界） | **失败 0，与 30.14.0 逐条一致**（收紧同样 14 例）—— 满足 O 的 H-5 门禁 |
| **A-90** | Rev.H 对前六轮全部矩阵复跑（双版本） | W 28 例、Z 22 例、Y 20 例、X 40 例、T/N/C/F、模糊 6000 条（0 崩溃、0 不变量违例）**全部保持通过，无回归** |
| **A-91** | Rev.H 生产 14 表 + 全语料 197 条 + 两份 fixture | 14 表**零漂移**；语料**恰好 2 条**变化（均为本次目标 fixture）；**与 Rev.G 逐键完全一致**——三项整改只作用于非法输入 |
| **A-92** | Rev.H 全量回归 | **1355 passed / 0 failed / 29 skipped**，与主干逐条相同 |
| **A-93** | Rev.H 静态检查 | 39 个函数无重复定义、无不可达语句、`except` 内 `return parsed` 恰 1 次、旧正则代码中已彻底删除、五个消费器统一契约 |

---

### A.8 Rev.I 新增证据（第八轮整改）

| 编号 | 证据 | 结论 |
|---|---|---|
| **A-94** | 复现 BLOCK-H1：O 的 H1-1~H1-5 + 我方补充 2 例 | **7 例全部复现**：主干 E999 → Rev.H `Create`。其中 H1-1~H1-4 **不需要任何 TDSQL 方言目标**即可发生 —— 证明第七轮 W/H 组的输入域确有空洞 |
| **A-95** | 复现 BLOCK-H2：分区表达式/定义结构反例 5 例 | `RANGE(,)` / `RANGE(+)` / `RANGE(id,)` 三例 E999 → `Create`；另 2 例 Rev.H 已失败关闭 |
| **A-96** | 复现 BLOCK-H3：`USING HASH COMMENT` | Rev.H 明确批准，E999 → `Create`；且**实测 119 条规则无一否决 HASH 索引类型**，下游无从补救 |
| **A-97** | **TDSQL 官方语法核实**（腾讯云官方文档） | `index_type: USING {BTREE}`（**无 HASH**）；`key_part: {col_name [(length)]} [ASC\|DESC]`；hash/broadcast 用 `shardkey=`，range/list 用 `TDSQL_DISTRIBUTED BY range\|list (column_name) [partition_options]` |
| **A-98** | **官方建表原例取证** | `tdsql_distributed by range(a) (s1 values less than(100), s2 values less than(200))` —— 分片声明**自带分片定义表**，且定义项**无 `PARTITION` 前缀** |
| **A-99** | **官方二级分区原例取证** | `... PARTITION BY list(order_id) (...) TDSQL_DISTRIBUTED BY RANGE(id)` —— 存在**分区在前、分片声明在后**的合法顺序；另一例为 `shardkey=first_name PARTITION BY LIST (city) (...)` |
| **A-100** | **SELF-I1/I2/I3：我方自查出 Rev.H 拒绝三种官方合法形态** | 官方 RANGE/LIST + 分片定义表、官方"分区在前"顺序、多列 `shardkey=(a,b)` —— Rev.H 一律 E999 不恢复。**根因与 BLOCK-H3 相同：拿 MySQL/sqlglot 当判据** |
| **A-101** | **SELF-I3 的依据就在本仓库** | `backend/services/tdsql_connector.py:165` 注释明写"或多列 `shardkey=(a,b)`" —— 前七轮一次都没查过项目自己沉淀的 TDSQL 事实 |
| **A-102** | **通用"候选 AST 回生成比对"方案可行性验证** | **不成立**：sqlglot 生成器把 `UNIQUE KEY` 归一为 `UNIQUE`、`DEFAULT CHARSET` 归一为 `CHARACTER SET`，正例同样报"丢 token"；且 `ENGINE=123` 反而检测不出。故改用 O 提的**定向结构门禁** |
| **A-103** | **定向结构门禁可行性实测** | `PARTITION BY RANGE(,)` → 候选 `properties=[]`（分区被静默丢弃）；官方分区 → `PartitionByListProperty` 保留；空索引 → 定义项数可辨。四条门禁均可从 AST 直接判定 |
| **A-104** | **sqlglot 缺口的掩码闭合实测** | `ASC` / `DESC` / 前缀+DESC+多列 / 分区定义 `ENGINE=` / 官方两种子句顺序 —— **五种形态用同一套等长置空 span 机制全部一次闭合** |
| **A-105** | **掩码不影响审核结论的证明** | 实测 119 条规则**无一引用 `ASC`/`DESC`**，解析器亦从不向规则层暴露排序方向；分区类规则读 `raw_sql` 正则；`raw_sql` 始终保持原文（S-4） |
| **A-106** | **我新引入 bug 被 Z 组当场抓出**（自我批评） | 为支持多列 `shardkey=(a,b)`，我把"多标识符"规则误用到 `TDSQL_DISTRIBUTED BY HASH(...)`。官方那里是**单列 `column_name`**，且 v1.6.1.9 冻结的 `_extract_tdsql_hash_key()` 只提取单个分片键 —— 已改回单列。**两处形态不同，不能共用消费器** |
| **A-107** | `MAXVALUE` 的处置依据与用户决策 | `VALUES LESS THAN MAXVALUE` 在 sqlglot 30.x 上 ParseError（去方言后亦然，非本方案所致）；语料 197 条与生产 14 表中出现 **0 次**。**用户 2026-08-26 决定按 O 的要求单独登记为已知假阴性、本版不补实现** → §5.21.5 KFN-1 |
| **A-108** | Rev.I H 组用例（数量见 §7.1a） | **失败 0**：14 例第八轮原始反例全部保留 E999；10 例 TDSQL 官方形态全部恢复；2 例 `pos_known` 单独登记；14 例较主干收紧（旧正则假成功） |
| **A-109** | **依赖三版矩阵**（MAJOR-H2） | 29.0.0 / 30.14.0 / 30.17.0 上 H 组用例（数量见 §7.1a）与 W/Z/Y/X 矩阵**逐条一致，0 例差异** → 依赖改为**精确锁定 `sqlglot==30.14.0`**，三版记录作为将来移动 pin 的依据 |
| **A-110** | Rev.I 对前七轮全部矩阵复跑（三版本） | W 28 例、Z 22 例、Y 20 例、X 40 例、T/N/C/F、模糊 6000 条（0 崩溃、0 不变量违例）**全部保持通过** |
| **A-111** | Rev.I 生产 14 表 + 全语料 197 条 + 两份 fixture | 14 表**零漂移**；语料**恰好 2 条**变化（均为目标 fixture）；**与 Rev.H 逐键完全一致** —— 本轮整改只作用于非法输入与此前被误拒的官方形态 |
| **A-112** | Rev.I 全量回归 | **1355 passed / 0 failed / 29 skipped**，与主干逐条相同 |

---

### A.9 Rev.J 新增证据（第九轮整改）

| 编号 | 证据 | 结论 |
|---|---|---|
| **A-113** | 复现 BLOCK-X2：7 类非法列定义 × UNIQUE COMMENT | 主干 E999 → Rev.I `Create`，且 `VARCHAR()` 静默变 `TEXT`、`DECIMAL(,2)` 变 `DECIMAL(2)` —— 错误列类型直接进入 119 条规则。**7 例全部复现** |
| **A-114** | 复现 BLOCK-X3：仅 partition COMMENT 掩码、无主目标 | 主干 E999 → Rev.I `Create` 并把 `VARCHAR()` 变 `TEXT` —— 证实 Rev.I **隐式扩大了修复范围** |
| **A-115** | 复现 BLOCK-X4：3 类方法/操作符错配 | 主干 `Command` → Rev.I `Create`；`HASH + 定义表` 那例 R054/R077 一并消失 |
| **A-116** | 复现 BLOCK-X5：两个 `PARTITION BY`、标识符冒充字面量 | 主干 E999 → Rev.I `Create` |
| **A-117** | **验证 X5 的死分支根因** | 实测：只有 `YEAR` 有专属 TokenType，`MONTH`/`DAY`/`TO_DAYS`/`UNIX_TIMESTAMP` 等**全部被词法成 `VAR`**；Rev.I 先判"是标识符就当普通列"，永远到不了函数分支 —— **与 O 的判断完全一致，加函数名到白名单没用，必须改分支顺序** |
| **A-118** | 复现 BLOCK-X6：4 类表尾顺序/次数错误 | 主干 E999 → Rev.I `Create`，含重复 `shardkey`、`shardkey + TDSQL_DISTRIBUTED` 并存 |
| **A-119** | 复现 MAJOR-X2：`id(1.5)` / `id(0)` / 重复 `USING` / 重复索引 `COMMENT` | 主干 E999 → Rev.I `Create`，4 例全部复现 |
| **A-120** | **精确验证 BLOCK-X1 的证明力边界**（我方补充） | 在 Rev.I 的 H 组里，**实际滑过 rank 判据且候选仍是 `Create` 的用例为 0 条**；O 给的反例其实**会**被判据拒绝，只是**我的测试集里没有这条输入**。故真实情况是"判据证明力不足 + 输入域有缺口"两个问题叠加，结论仍是必须换判据 |
| **A-121** | **A-61 旧证据更正**（自我批评） | 第五轮我写"语料 `BROADCAST` 中间 8 处"并据此放弃收紧。本轮重新取证：全仓 `.sql` **没有一条真实广播表声明**，那 8 处全在**注释文本**里（`COMMENT='系统配置表 BROADCAST'`）。**取证脚本必须区分 token 流关键字与字符串字面量同名文本** —— 我在自己的取证脚本里犯了本方案一直在防的那个错 |
| **A-122** | **生产表尾实测（表尾状态机的 provenance）** | `) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='…' shardkey=black_list_seq_num` —— **本地选项在前、shardkey 在后**，与阶段模型一致 |
| **A-123** | **我自己引入并被基准用例当场发现的 bug**（自我批评） | `_consume_column_constraints()` 首版未在顶层逗号处收尾，导致**所有**列定义被判非法、连基准正例都恢复不了。**每写一个消费器必须立刻用最小正例验一次** |
| **A-124** | **死代码区清理**（MAJOR-X3 第 1 条） | 重建后仍残留 105 行 Rev.H 死代码（含被遮蔽的旧 `_consume_table_option` 与 `_TDSQL_SHARD_METHODS`）。已删除；静态检查现断言**无重复函数定义 + 无重复模块级常量** |
| **A-125** | Rev.J H 组用例（数量见 §7.1a） | **失败 0**：X1~X7 与 M1~M2 的全部反例保留原结论；官方 `MONTH`/`DAY`/负值边界/`STATS_*` 恢复；8 例 `unsupported_unproven`、2 例 `pos_known` 单独登记 |
| **A-126** | Rev.J 三版本矩阵 | sqlglot **29.0.0 / 30.14.0 / 30.17.0** 上 H 组用例（数量见 §7.1a）逐条一致，0 例差异 |
| **A-127** | Rev.J 对前八轮全部矩阵复跑 | W 28、Z 22、Y 20、X 40、T/N/C/F、模糊 6000 条（0 崩溃、0 不变量违例）**全部通过** |
| **A-128** | Rev.J 生产 14 表 + 全语料 197 条 + 两份 fixture | 14 表**零漂移**；语料**恰好 2 条**变化；**与 Rev.I 逐键完全一致** —— 这次重构规模最大，却对合法数据零影响 |
| **A-129** | Rev.J 全量回归 | **1355 passed / 0 failed / 29 skipped**，与主干逐条相同 |

---

### A.10 Rev.K 新增证据（第十轮整改）

| 编号 | 证据 | 结论 |
|---|---|---|
| **A-130** | 复现 BLOCK-J1（放行侧）：11 类非法列定义/DEFAULT | 主干 E999 → Rev.J `Create`。含 `id RANGE`、`id NULL`、`VARCHAR(1,2,3)`、`JSON(1)`、`DEFAULT (SELECT 1)` 等，**全部复现** |
| **A-131** | 复现 BLOCK-J1（误拒侧）：7 类官方合法列定义 | `DECIMAL(10,0)` / `DATETIME(0)` / `TIME(0)` / `DEFAULT -1` / `DEFAULT +1` / `COLUMN_FORMAT` / `ENGINE_ATTRIBUTE` 在 Rev.J 上 **REJECT_PLAN** —— **我把索引前缀的"正整数"谓词复用到了 scale/fsp 上** |
| **A-132** | 复现 BLOCK-J2 | `id JSON(1)` 原文指纹 `JSON(1)`、候选静默变 `JSON`，Rev.J 门禁仍返回 True |
| **A-133** | 复现 BLOCK-J3 | `shardkey=id ENGINE=InnoDB`、`BROADCAST…PARTITION`、`PARTITION…BROADCAST` 三条均 `ACCEPT`；**合法单条 DDL 尾分号被误拒** |
| **A-134** | 复现 BLOCK-J5 | `VALUES IN (-'x')` `ACCEPT`；4 个未举证函数全部可达 `Create`；官方 `STORAGE ENGINE` 被拒、反序 `COMMENT…ENGINE` 反被接受 |
| **A-135** | 复现 MAJOR-J2 | `PRIMARY KEY pk(id)` `ACCEPT`；前后置 `USING` 各自新建 seen |
| **A-136** | **复审方提供的官方文档离线摘要** | 补齐 Rev.J §5.23.4 记录的取证缺口（`cloud.tencent.com` 被出口代理拦截）。据此更正：`ROW_FORMAT` / `STATS_PERSISTENT` 是**官方 local_table_option**，Rev.J 判成 `unsupported_unproven` 属**我的取证错误** |
| **A-137** | **sqlglot 对各 kind 索引 COMMENT 的能力实测** | `UNIQUE` ParseError / `PRIMARY` ParseError / 普通 `KEY`、`INDEX`、`FULLTEXT` **可解析** / `CONSTRAINT … UNIQUE` 可解析 |
| **A-138** | **我自己引入并当场被发现的回归**（自我批评） | 一度把所有非 UNIQUE 索引 COMMENT 判成失败关闭，**生产 fixture gg78 立即回归**（它含真实的 `KEY … COMMENT`）。抓住它的是 fixture 的**精确规则集合断言** |
| **A-139** | **CONSTRAINT 处置更正**（自我批评） | 一度把 `CONSTRAINT symbol UNIQUE` 改成"整句失败关闭"；但 NG-10/ADJ-11 冻结的是"本版不修"，不是"整句拒绝"，且它官方合法、sqlglot 可解析。改为**逐 token 消费以完成整句校验，但不作目标** |
| **A-140** | **测试清单真源化**（MAJOR-J1） | §7.1 旧 H 组表明细相加 109、总计写 90，且文档引用了仓库不存在的 `h_cases.py`。§7.1a 改为**由参数化清单生成**；准出改以 `pytest --collect-only -q` 实际收集数为证 |
| **A-141** | Rev.K H 组（清单见 §7.1a） | **失败 0**：J1~J5 与 MAJOR-J2 全部反例保留原结论；官方 `ROW_FORMAT` / `STATS_PERSISTENT` / `DECIMAL(M,0)` / `DEFAULT ±n` / `COLUMN_FORMAT` 等全部恢复 |
| **A-142** | Rev.K 三版本矩阵 | sqlglot **29.0.0 / 30.14.0 / 30.17.0** 逐条一致，0 例差异 |
| **A-143** | Rev.K 对前九轮全部矩阵复跑 | W 28、Z 22、Y 20、X 40、T/N/C/F、模糊 6000 条（0 崩溃、0 不变量违例）**全部通过** |
| **A-144** | Rev.K 生产 14 表 + 全语料 197 条 + 两份 fixture | 14 表**零漂移**；语料**恰好 2 条**变化；**与 Rev.J 逐键完全一致**；两份 fixture 规则集合**精确相等** |
| **A-145** | Rev.K 全量回归 | 与主干逐条相同，0 failed |

---

### A.11 Rev.L 新增证据（DEF-3）

| 编号 | 证据 | 结论 |
|---|---|---|
| **A-146** | **用户确认目标实例存在 `PRIMARY KEY … COMMENT` 形态** | KFN-2 由"已知假阴性"转为**必须修复**，登记撤销 |
| **A-147** | 典型内网形态实测（4 列 + PRIMARY COMMENT） | 主干/Rev.K：`E999, R003, R004, R005, R028`（四条连带误报）→ Rev.L：`R037`。**误报机理与 gg78 完全一致** |
| **A-148** | 掩码路径实测 | `PRIMARY KEY (a)` / `(a,b)` / `USING BTREE` / **与 UNIQUE 双注释共存** 四种形态掩码后**全部可解析** |
| **A-149** | P 组 14 例（8 正例 + 6 非法近邻） | **失败 0**；6 例非法近邻全部保持失败关闭，证明扩大恢复范围未放松边界 |
| **A-150** | P 组三版本 | sqlglot 29.0.0 / 30.14.0 / 30.17.0 **一致** |
| **A-151** | Rev.L 爆炸半径 | 全语料 197 条与生产 14 表**相对 Rev.K 逐键无变化**；两份 fixture 精确相等；前十轮全部矩阵通过；全量回归 0 failed |

### A.12 Rev.M 新增证据（第十一轮整改）

| 编号 | 证据 | 结论 |
|---|---|---|
| **A-152** | 复现 O 第十一轮 11 条发现 | **全部复现，无异议条目**；其中 BLOCK-11-06 是**我方错误**——Rev.K 只在规划层验证就写了"恢复 ✅" |
| **A-153** | `/*!50100 …*/` 可执行注释白盒实测 | payload 落在 `token.comments`，Rev.L 规划器完全看不见；Rev.M 的 `_validate_executable_comments()` 使 `RANGE()` 空参 / 两条 `PARTITION BY` / `EVIL OPTION` / 两个可执行注释**全部 plan=REJECT**，合法 `/*!50100 PARTITION BY LIST … */` **恢复为 Create** |
| **A-154** | 表尾 typed atoms + profile 实测 | `DIST→PARTITION→DIST`、`shardkey→PARTITION→DIST`、`PARTITION→DIST→PARTITION` **全部 plan=REJECT**；`shardkey+PARTITION`、`PARTITION+DIST` 两种官方原例**仍恢复** |
| **A-155** | 广播哨兵分型实测 | `shardkey=(noshardkey_allset)` / `shardkey=(noshardkey_allset,id)` / 哨兵后接 `PARTITION BY` **全部 plan=REJECT**；裸哨兵**仍恢复** |
| **A-156** | 类型双向闭合矩阵（TY 组 108 例，三版） | 官方合法 78 例**零回归**；越界/非法 30 例**零误放行**；三版结果逐条一致 |
| **A-157** | KFN-3 前后对照实测 | 8 种类型在 **repo main 基线与 Rev.M 上行为完全相同**（均 `ast=None / E999=有`）→ 属既有能力边界，非本次引入 |
| **A-158** | 门禁白盒反向鉴别（M 组 28 条） | Rev.L 门禁对丢约束 / 换索引 kind 等**全部返回 True**（形同虚设）；Rev.M **全部拒绝**，且正确候选零误杀 |
| **A-159** | `USING` 三处 arg 的 sqlglot 实测 | `index_type` / `options[].using` / `include.using` 三处并存；只读 `index_type` 会误杀 `PRIMARY KEY (id) USING BTREE COMMENT`（P 组实测），故新增 `_ast_index_using()` |
| **A-160** | `COLUMN_FORMAT` / `ENGINE_ATTRIBUTE` **端到端**实测 | `plan=ACCEPT → cand=Create → gate=True → 端到端 Create / 无 E999`；`grep` 确认 119 条规则**无消费者**依赖这两个属性 |
| **A-161** | `FULLTEXT`/`SPATIAL` 入口实测 | 裸 `FULLTEXT (a)` / `SPATIAL (g)` **恢复**；缺括号**失败关闭**；`` `fulltext` `` / `` `spatial` `` 反引号列名**仍走列定义消费器** |
| **A-162** | manifest 全量（410 例 + 28 变异 + 6000 模糊） | **29.0.0 / 30.14.0 / 30.17.0 三版全绿**；`pytest --collect-only -q` 收集 **416** 项、**零 skip** |
| **A-163** | Rev.M 爆炸半径 | 全语料 + 生产 14 表共 **201 条语句逐键零漂移**（两侧解析异常均为 0）；两份 fixture 精确相等；`verify_rules.py` 119/107/0/**3**（与基线同名同因）；全量回归 **1771 passed / 0 failed / 29 skipped**（含新增 416 项） |
| **A-164** | 「照图施工」自检 | 从本说明书前 10 个代码块重建的 `parser_legacy.py` 与提交文件**逐字节相同**；重建树复跑 manifest 与 `pytest tests/` 结果一致。附录 C 的 4 个文件同样可从文档逐字节还原 |

---

## 附录 C：证据面交付物源码（Rev.M 新增，第十一轮 BLOCK-11-07 / MINOR-11-02）

> 以下四个文件**随本设计一并交付**，请按给出的路径**逐字创建**。
> 它们不是产品代码，不参与运行时；但它们是**准出证据本身**——
> §7.1 的全部表格与计数、§3.4 的全部规模数字，都必须能由这四个文件复现。
> ⚠️ 施工时不要修改任何一行：正文与它们不一致时，**以文件为准**，然后重跑生成器更新正文。

### `tests/parser_recovery_manifest.py`

唯一 case manifest（纯数据，无判定逻辑）

```python
# -*- coding: utf-8 -*-
"""v1.6.2.2 解析恢复链 —— 唯一 case manifest（第十一轮 BLOCK-11-07）。

本文件是**全部用例的唯一真源**。设计说明书 §7.1/§7.1a/§7.1b 的每一张用例表、
每一个计数，都由 `manifest_doc.py` 从这里生成；任何章节都不得再人工维护第二份。

字段
----
cid          稳定 ID（组名 + 序号），一经分配不再变更；新增只追加不插队
group        组名（A/B/C/D/E/F/T/N/X/Y/Z/W/H*/P*/M*/TY*/R11*）
label        中文标签
sql          完整 SQL（None 表示该例由 fixture 文件提供，见 extra['fixture']）
klass        分类，决定判据：
             pos                   必须恢复：plan=True、AST=Create、无 E999
             neg                   必须失败关闭：plan=False 且 AST≠Create
             pos_known             TDSQL 官方合法但 sqlglot 解析不了 →
                                   必须失败关闭，单独计入已知假阴性（KFN-A）
             unsupported_unproven  无 TDSQL/目标实例证据 → 必须失败关闭（KFN-B），
                                   既不冒充合法也不冒充非法
             characterization      用户已冻结的表征行为，锁定当前结论，不代表 TDSQL 合法
             ruleset               断言规则命中集合精确相等（生产 fixture 回放）
             spans                 断言剥离 span 的数量与越界字符数
             contract              断言 sqlglot AST 契约（升级破坏时必须显式失败）
prov         证据来源：
             OFFICIAL          腾讯 TDSQL 官方文档
             TARGET_INSTANCE   目标实例实测
             CORPUS            197 条语料 / 生产 14 表实证
             PROJECT_ACCEPTED  项目既有已接受用例
             SQLGLOT_LIMIT     sqlglot 自身能力边界（修复前后行为一致）
             USER_DECISION     用户冻结决策
             REVIEW_11         第十一轮复审报告 §4~§9 的反例
note         一句话理由
extra        判据参数（期望 span 数、期望规则集合、fixture 名、instance_type 等）
"""
from collections import namedtuple

CASE = namedtuple("CASE", "cid group label sql klass prov note extra")
CASES = []


def add(group, label, sql, klass, prov, note="", **extra):
    cid = "%s-%02d" % (group, sum(1 for c in CASES if c.group == group) + 1)
    CASES.append(CASE(cid, group, label, sql, klass, prov, note, extra))


# ══════════════════════════════════════════════════════════════════════════
# A 组 —— DEF-1 索引类型判据 + AST 契约
# ══════════════════════════════════════════════════════════════════════════
_A = ("CREATE TABLE `t` (`id` int NOT NULL, `sk` int NOT NULL, `%s` int NOT NULL, "
      "PRIMARY KEY (`id`,`sk`), %s) ENGINE=InnoDB shardkey=sk")
for lbl, col, idx, want in [
    ("普通索引，列名 list_unique_num", "list_unique_num", "KEY `k` (`list_unique_num`)", "NORMAL"),
    ("索引名 unique_lookup",           "c",               "KEY `unique_lookup` (`c`)",   "NORMAL"),
    ("列名 biz_primary_no",            "biz_primary_no",  "KEY `k` (`biz_primary_no`)",  "NORMAL"),
    ("列名 fulltext_body",             "fulltext_body",   "KEY `k` (`fulltext_body`)",   "NORMAL"),
    ("真 FULLTEXT KEY（反向鉴别）",     "c",               "FULLTEXT KEY `ft` (`c`)",     "FULLTEXT"),
]:
    add("A", lbl, _A % (col, idx), "pos", "PROJECT_ACCEPTED",
        "索引类型只认关键字，不得从名字/列名猜", index_type=want, needs_recovery=False)
add("A", "真 UNIQUE 不含分片键 → R054 命中",
    _A % ("c", "UNIQUE KEY `uk` (`c`)"), "pos", "PROJECT_ACCEPTED",
    "反向鉴别：真 UNIQUE 必须触发 R054", rule_hit="R054", needs_recovery=False)
add("A", "真 UNIQUE 含分片键 → R054 不命中",
    _A % ("c", "UNIQUE KEY `uk` (`sk`,`c`)"), "pos", "PROJECT_ACCEPTED",
    "含分片键的 UNIQUE 合规", rule_miss="R054", needs_recovery=False)
add("A", "诱饵列名 + 真 UNIQUE 不含分片键 → R054 命中",
    _A % ("list_unique_num", "UNIQUE KEY `uk` (`list_unique_num`)"), "pos", "PROJECT_ACCEPTED",
    "本组最重要：锁定漏报修复", rule_hit="R054", needs_recovery=False)
add("A", "sqlglot AST 契约", None, "contract", "PROJECT_ACCEPTED",
    "UNIQUE→UniqueColumnConstraint、PRIMARY→PrimaryKey、FULLTEXT/SPATIAL→IndexColumnConstraint")

# ══════════════════════════════════════════════════════════════════════════
# B 组 —— DEF-2 正向恢复
# ══════════════════════════════════════════════════════════════════════════
_B = "CREATE TABLE `t` (`id` int NOT NULL COMMENT 'i', `sk` int NOT NULL COMMENT 's'%s) %s"
for lbl, defs, tail in [
    ("单个 UNIQUE COMMENT",        ", UNIQUE KEY `uk` (`sk`) COMMENT 'u'", "ENGINE=InnoDB"),
    ("UNIQUE COMMENT 双引号值",     ', UNIQUE KEY `uk` (`sk`) COMMENT "u"', "ENGINE=InnoDB"),
    ("UNIQUE INDEX 写法",          ", UNIQUE INDEX `uk` (`sk`) COMMENT 'u'", "ENGINE=InnoDB"),
    ("裸 UNIQUE（无 KEY/INDEX）",   ", UNIQUE `uk` (`sk`) COMMENT 'u'", "ENGINE=InnoDB"),
    ("USING BTREE + COMMENT",     ", UNIQUE KEY `uk` (`sk`) USING BTREE COMMENT 'u'", "ENGINE=InnoDB"),
    ("两个 UNIQUE 各带 COMMENT",    ", UNIQUE KEY `u1` (`id`) COMMENT 'a', UNIQUE KEY `u2` (`sk`) COMMENT 'b'", "ENGINE=InnoDB"),
    ("UNIQUE COMMENT + 普通 KEY COMMENT", ", UNIQUE KEY `uk` (`sk`) COMMENT 'u', KEY `k` (`id`) COMMENT 'n'", "ENGINE=InnoDB"),
    ("COMMENT 值含转义单引号",       ", UNIQUE KEY `uk` (`sk`) COMMENT 'it''s'", "ENGINE=InnoDB"),
    ("COMMENT 值含中文与括号",       ", UNIQUE KEY `uk` (`sk`) COMMENT '唯一(索引)'", "ENGINE=InnoDB"),
    ("多列 UNIQUE + COMMENT",      ", UNIQUE KEY `uk` (`id`,`sk`) COMMENT 'u'", "ENGINE=InnoDB"),
    ("PRIMARY + UNIQUE COMMENT",   ", PRIMARY KEY (`id`), UNIQUE KEY `uk` (`sk`) COMMENT 'u'", "ENGINE=InnoDB"),
    ("UNIQUE COMMENT + 表选项全套",  ", UNIQUE KEY `uk` (`sk`) COMMENT 'u'",
     "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='表'"),
]:
    add("B", lbl, _B % (defs, tail), "pos", "CORPUS", "正向恢复：raw_sql 必须逐字等于输入")

# ══════════════════════════════════════════════════════════════════════════
# C 组 —— DEF-2 产品边界（sqlglot 自身不支持，去掉 COMMENT 也 ParseError）
# ══════════════════════════════════════════════════════════════════════════
for lbl, frag in [
    ("函数键值 ((lower(a)))",  "UNIQUE KEY `uk` ((lower(`a`))) COMMENT 'x'"),
    ("VISIBLE",              "UNIQUE KEY `uk` (`a`) COMMENT 'x' VISIBLE"),
    ("KEY_BLOCK_SIZE",       "UNIQUE KEY `uk` (`a`) KEY_BLOCK_SIZE=8 COMMENT 'x'"),
    ("USING 前置于键值列表",     "UNIQUE KEY `uk` USING BTREE (`a`) COMMENT 'x'"),
]:
    add("C", lbl,
        "CREATE TABLE `t` (`a` int NOT NULL COMMENT 'x', PRIMARY KEY (`a`), %s) ENGINE=InnoDB" % frag,
        "pos_known", "SQLGLOT_LIMIT",
        "去掉 COMMENT 后 sqlglot 同样 ParseError → 非剥离器缺陷")

# ══════════════════════════════════════════════════════════════════════════
# D 组 —— 负向 / 防次生灾害（断言 span 数与越界改写字符数）
# ══════════════════════════════════════════════════════════════════════════
_D = "CREATE TABLE `t` (`a` int NOT NULL %s, UNIQUE KEY `uk` (`a`) COMMENT 'real') ENGINE=InnoDB %s"
for lbl, coldef, tail in [
    ("伪 SQL 藏在列 COMMENT",  "COMMENT 'UNIQUE KEY z (a) COMMENT ''fake'''", ""),
    ("伪 SQL 藏在表 COMMENT",  "COMMENT 'x'", "COMMENT='UNIQUE KEY z (a) COMMENT ''fake'''"),
    ("伪 SQL 藏在 DEFAULT 串", "DEFAULT 'UNIQUE KEY z (a) COMMENT ''fake''' COMMENT 'x'", ""),
]:
    add("D", lbl, _D % (coldef, tail), "spans", "PROJECT_ACCEPTED",
        "只允许抹掉真实索引 COMMENT，越界字符数必须为 0", spans=1)
add("D", "伪 SQL 藏在 -- 行注释",
    "CREATE TABLE `t` (`a` int NOT NULL COMMENT 'x', -- UNIQUE KEY z (a) COMMENT 'fake'\n"
    " UNIQUE KEY `uk` (`a`) COMMENT 'real') ENGINE=InnoDB", "spans", "PROJECT_ACCEPTED",
    "行注释内容不可见", spans=1)
add("D", "伪 SQL 藏在 /* */ 块注释",
    "CREATE TABLE `t` (`a` int NOT NULL COMMENT 'x', /* UNIQUE KEY z (a) COMMENT 'fake' */"
    " UNIQUE KEY `uk` (`a`) COMMENT 'real') ENGINE=InnoDB", "spans", "PROJECT_ACCEPTED",
    "块注释内容不可见", spans=1)
add("D", "伪 SQL 藏在反引号标识符内",
    "CREATE TABLE `t` (`UNIQUE KEY z (a) COMMENT ''fake''` int NOT NULL COMMENT 'x',"
    " UNIQUE KEY `uk` (`a`) COMMENT 'real') ENGINE=InnoDB", "spans", "PROJECT_ACCEPTED",
    "标识符内容不可见", spans=1)

# ══════════════════════════════════════════════════════════════════════════
# E 组 —— 失败关闭
# ══════════════════════════════════════════════════════════════════════════
for lbl, sql in [
    ("未闭合单引号",   "CREATE TABLE `t` (`a` int COMMENT 'x, UNIQUE KEY `uk` (`a`) COMMENT 'u') ENGINE=InnoDB"),
    ("未闭合括号",     "CREATE TABLE `t` (`a` int, UNIQUE KEY `uk` (`a` COMMENT 'u') ENGINE=InnoDB"),
    ("非 CREATE TABLE", "ALTER TABLE `t` ADD UNIQUE KEY `uk` (`a`) COMMENT 'u'"),
    ("缺右括号建表",   "CREATE TABLE `t` (`a` int, UNIQUE KEY `uk` (`a`) COMMENT 'u' ENGINE=InnoDB"),
]:
    add("E", lbl, sql, "neg", "PROJECT_ACCEPTED", "剥离器返回 None 或重试失败，仍报原错误")

# ══════════════════════════════════════════════════════════════════════════
# F 组 —— 生产回放（精确规则集合相等）
# ══════════════════════════════════════════════════════════════════════════
add("F", "report_6309_kcfb_list_info.sql（分布式）", None, "ruleset", "CORPUS",
    "精确相等，子集断言证明不了零新增",
    fixture="report_6309_kcfb_list_info.sql", instance_type="distributed",
    rules={"R011", "R018", "R019", "R036", "R037", "R061", "R065", "R067", "R104"})
add("F", "report_6311_biz_tx_log.sql（集中式）", None, "ruleset", "CORPUS",
    "精确相等；集中式上下文不得与分布式混用",
    fixture="report_6311_biz_tx_log.sql", instance_type="centralized",
    rules={"R036", "R037"})

# ══════════════════════════════════════════════════════════════════════════
# T 组 —— TDSQL 方言组合（每例额外断言：与"同表去掉索引 COMMENT"的规则集合相等）
# ══════════════════════════════════════════════════════════════════════════
_T = ("CREATE TABLE `t` (`a` int NOT NULL COMMENT 'x', `sk` int NOT NULL COMMENT 'y',\n"
      " `create_time` datetime NOT NULL COMMENT 'c', `update_time` datetime NOT NULL COMMENT 'u',\n"
      " `is_deleted` tinyint NOT NULL DEFAULT 0 COMMENT 'd', PRIMARY KEY (`a`,`sk`),\n"
      " UNIQUE KEY `uk` (`a`,`sk`) COMMENT '唯一索引说明'\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='表' %s")
for lbl, tail in [("T1 HASH", "TDSQL_DISTRIBUTED BY HASH(`sk`)"),
                  ("T2 RANGE", "TDSQL_DISTRIBUTED BY RANGE(`sk`)"),
                  ("T3 LIST", "TDSQL_DISTRIBUTED BY LIST(`sk`)"),
                  ("T4 BROADCAST", "BROADCAST"),
                  ("T6 shardkey=（对照）", "shardkey=sk")]:
    add("T", lbl, _T % tail, "pos", "OFFICIAL",
        "恢复不得引入自己的口径：规则集合须等于去掉 COMMENT 的同表",
        equal_ruleset_without_comment=True, instance_type="distributed")
add("T", "T5 HASH + 二级分区",
    "CREATE TABLE `t5` (`a` int NOT NULL COMMENT 'x', `sk` int NOT NULL COMMENT 'y',"
    " PRIMARY KEY (`a`,`sk`), UNIQUE KEY `uk` (`a`,`sk`) COMMENT 'z') ENGINE=InnoDB"
    " TDSQL_DISTRIBUTED BY HASH(`sk`) PARTITION BY RANGE(`a`) (PARTITION p0 VALUES LESS THAN (10))",
    "pos", "PROJECT_ACCEPTED", "D5/T5 既有用例不得回归",
    equal_ruleset_without_comment=True, instance_type="distributed")
_TT = ("CREATE TEMPORARY TABLE `tt` (`a` int NOT NULL COMMENT 'x', PRIMARY KEY (`a`),"
       " UNIQUE KEY `u` (`a`) COMMENT 'z') ENGINE=InnoDB")
add("T", "T9 TEMPORARY（集中式）", _TT, "pos", "PROJECT_ACCEPTED",
    "R032 仍命中；is_temporary_table 为真", instance_type="centralized", rule_hit="R032")
add("T", "T10 TEMPORARY（分布式）", _TT, "pos", "PROJECT_ACCEPTED",
    "R024+R032 仍命中", instance_type="distributed", rule_hit="R032")

# ══════════════════════════════════════════════════════════════════════════
# N 组 —— 作用域负向（断言 span 数恰为 1 或 0）
# ══════════════════════════════════════════════════════════════════════════
for lbl, sql, n in [
    ("N1 CONSTRAINT ... UNIQUE",
     "CREATE TABLE `t` (`a` int NOT NULL COMMENT 'x',\n CONSTRAINT `uq` UNIQUE (`a`) COMMENT 'cc',\n"
     " UNIQUE KEY `uk` (`a`) COMMENT 'real'\n) ENGINE=InnoDB", 1),
    ("N2 列内联 UNIQUE",
     "CREATE TABLE `t` (`a` int NOT NULL UNIQUE COMMENT 'inline',\n `b` int COMMENT 'y',\n"
     " UNIQUE KEY `uk` (`b`) COMMENT 'real'\n) ENGINE=InnoDB", 1),
    ("N3 定义项中部 UNIQUE（整句非法，须拒绝）",
     "CREATE TABLE `t` (`a` int NOT NULL COMMENT 'x',\n KEY `k` (`a`) UNIQUE COMMENT 'mid',\n"
     " UNIQUE KEY `uk` (`a`) COMMENT 'real'\n) ENGINE=InnoDB", 0),
    ("N4 两条语句拼接（须拒绝）",
     "CREATE TABLE `t1` (`a` int NOT NULL COMMENT 'x', UNIQUE KEY `u1` (`a`) COMMENT 'first') ENGINE=InnoDB;\n"
     "CREATE TABLE `t2` (`b` int NOT NULL COMMENT 'y', UNIQUE KEY `u2` (`b`) COMMENT 'second') ENGINE=InnoDB", 0),
    ("N5 定义列表闭合后的表选项",
     "CREATE TABLE `t` (`a` int NOT NULL COMMENT 'x', UNIQUE KEY `uk` (`a`) COMMENT 'real') ENGINE=InnoDB"
     " COMMENT='tail UNIQUE KEY z (a) COMMENT ''fake'''", 1),
]:
    add("N", lbl, sql, "spans", "PROJECT_ACCEPTED", "抹除的必须正是那个真实目标", spans=n)

# ══════════════════════════════════════════════════════════════════════════
# X 组 —— 方言尾子句安全交叉矩阵（4 尾子句 × 5 诱饵 × 带/不带 UNIQUE COMMENT）
#         每例做字段级精确断言：列名序列、目标列注释、DEFAULT、raw_sql 逐字
# ══════════════════════════════════════════════════════════════════════════
_X_TAILS = [("HASH", "TDSQL_DISTRIBUTED BY HASH(`sk`)"), ("RANGE", "TDSQL_DISTRIBUTED BY RANGE(`sk`)"),
            ("LIST", "TDSQL_DISTRIBUTED BY LIST(`sk`)"), ("BROADCAST", "BROADCAST")]
_X_DECOYS = [
    ("列名为 `broadcast`", "`broadcast` varchar(20) DEFAULT NULL COMMENT 'bc'",
     ["id", "sk", "broadcast"], None),
    ("裸列名 broadcast", "broadcast varchar(20) DEFAULT NULL COMMENT 'bc'",
     ["id", "sk", "broadcast"], None),
    ("列注释含 broadcast", "`note` varchar(80) DEFAULT NULL COMMENT 'broadcast table info'",
     ["id", "sk", "note"], ("note", "broadcast table info")),
    ("列注释含伪 TDSQL 子句", "`note` varchar(80) DEFAULT NULL COMMENT 'TDSQL_DISTRIBUTED BY HASH(fake)'",
     ["id", "sk", "note"], ("note", "TDSQL_DISTRIBUTED BY HASH(fake)")),
    ("DEFAULT 值含 broadcast", "`note` varchar(80) DEFAULT 'broadcast' COMMENT 'n'",
     ["id", "sk", "note"], ("note", "n")),
]
for _tl, _tail in _X_TAILS:
    for _dl, _col, _cols, _cmt in _X_DECOYS:
        for _withuk in (True, False):
            _uk = (" UNIQUE KEY `uk` (`sk`) COMMENT 'x'," if _withuk
                   else " UNIQUE KEY `uk` (`sk`),")
            _s = ("CREATE TABLE `t` (`id` bigint NOT NULL COMMENT 'i',\n `sk` bigint NOT NULL COMMENT 's',\n %s,\n"
                  " PRIMARY KEY (`id`,`sk`),\n%s\n KEY `idx_k2` (`id`)\n) ENGINE=InnoDB %s"
                  % (_col, _uk, _tail))
            add("X", "%s × %s × %s" % (_tl, _dl, "带 UK COMMENT" if _withuk else "无 UK COMMENT"),
                _s, "pos", "CORPUS",
                "旧全局正则 _TDSQL_DIALECT_RE 会改写定义体，本组锁定它已被删除",
                columns=_cols, column_comment=_cmt, raw_verbatim=True)

# ══════════════════════════════════════════════════════════════════════════
# Y 组 —— 方言语法严格性与语句边界
# ══════════════════════════════════════════════════════════════════════════
_Y = ("CREATE TABLE `t` (`id` bigint COMMENT 'i', `sk` bigint COMMENT 's', "
      "PRIMARY KEY (`id`,`sk`)) ENGINE=InnoDB ")
for lbl, tail in [("Y1 缺 BY", "TDSQL_DISTRIBUTED (`sk`)"),
                  ("Y2 缺方法", "TDSQL_DISTRIBUTED BY (`sk`)"),
                  ("Y3 缺 BY 有方法", "TDSQL_DISTRIBUTED HASH(`sk`)"),
                  ("Y4 未知方法 FOO", "TDSQL_DISTRIBUTED BY FOO(`sk`)"),
                  ("Y5 缺括号", "TDSQL_DISTRIBUTED BY HASH")]:
    add("Y", lbl, _Y + tail, "neg", "OFFICIAL", "非法方言声明不得被修成合法", spans=0)
for lbl, tail in [("Y6 字符串 'TDSQL_DISTRIBUTED'", "'TDSQL_DISTRIBUTED' BY HASH(`sk`)"),
                  ("Y7 反引号 `TDSQL_DISTRIBUTED`", "`TDSQL_DISTRIBUTED` BY HASH(`sk`)"),
                  ("Y8 反引号 `broadcast`", "`broadcast`")]:
    add("Y", lbl, _Y + tail, "neg", "OFFICIAL", "字符串/标识符不得冒充关键字", spans=0)
add("Y", "Y9 COMMENT='TDSQL_DISTRIBUTED' + 真 HASH",
    _Y + "COMMENT='TDSQL_DISTRIBUTED' TDSQL_DISTRIBUTED BY HASH(`sk`)", "pos", "OFFICIAL",
    "表注释恰为方言词不得阻断真实尾子句", spans=1)
add("Y", "Y10 COMMENT='BROADCAST' + 真 BROADCAST",
    _Y + "COMMENT='BROADCAST' BROADCAST", "pos", "OFFICIAL",
    "同上", spans=1)
add("Y", "Y11 HASH + BROADCAST 双声明",
    _Y + "TDSQL_DISTRIBUTED BY HASH(`sk`) BROADCAST", "neg", "OFFICIAL",
    "一级分布至多一个", spans=0)
add("Y", "Y12 HASH + RANGE 双声明",
    _Y + "TDSQL_DISTRIBUTED BY HASH(`sk`) TDSQL_DISTRIBUTED BY RANGE(`sk`)", "neg", "OFFICIAL",
    "同上", spans=0)
add("Y", "Y13 CTAS（含函数括号）",
    "CREATE TABLE `t` AS\nSELECT CONCAT('a','b') AS c, broadcast\nFROM src\nTDSQL_DISTRIBUTED BY HASH(c)",
    "spans", "OFFICIAL", "CTAS 无定义列表，SELECT 列不得被改", spans=0)
add("Y", "Y14 CREATE TABLE ... LIKE", "CREATE TABLE `t` LIKE `src`", "spans", "OFFICIAL",
    "LIKE 无定义列表；sqlglot 原生即可解析，判据是剥离器不得改写", spans=0)
add("Y", "Y15 两条语句拼接",
    "CREATE TABLE `t` (`sk` bigint COMMENT 's', PRIMARY KEY (`sk`)) ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(`sk`);\n"
    "CREATE TABLE `u` (`x` int COMMENT 'x') ENGINE=InnoDB BROADCAST",
    "spans", "OFFICIAL", "剥离器不得跨分号改写", spans=0)
for _m in ("HASH", "RANGE", "LIST"):
    add("Y", "Y1x 合法 %s" % _m, _Y + "TDSQL_DISTRIBUTED BY %s(`sk`)" % _m, "pos", "OFFICIAL",
        "防收紧过头：RANGE/LIST 在实现中回归过一次", spans=1)
add("Y", "Y19 合法 BROADCAST", _Y + "BROADCAST", "pos", "OFFICIAL", "防收紧过头", spans=1)
add("Y", "Y20 反引号列名 `broadcast` + 真 HASH",
    "CREATE TABLE `t` (`broadcast` int COMMENT 'b', `sk` bigint COMMENT 's', PRIMARY KEY (`sk`))"
    " ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(`sk`)", "pos", "OFFICIAL",
    "诱饵列名不得阻断真实尾子句", spans=1)

# ══════════════════════════════════════════════════════════════════════════
# Z 组 —— 方法参数与表名精确形态
# ══════════════════════════════════════════════════════════════════════════
_Z = "CREATE TABLE `t` (`id` int NOT NULL COMMENT 'i', UNIQUE KEY `uk` (`id`) COMMENT 'u') ENGINE=InnoDB "
for lbl, tail in [("Z1 HASH() 空参", "TDSQL_DISTRIBUTED BY HASH()"),
                  ("Z1 HASH(,) 逗号", "TDSQL_DISTRIBUTED BY HASH(,)"),
                  ("Z1 HASH('id') 字符串", "TDSQL_DISTRIBUTED BY HASH('id')"),
                  ("Z1 HASH(id+1) 表达式", "TDSQL_DISTRIBUTED BY HASH(`id` + 1)"),
                  ("Z1 HASH(lower(id)) 函数", "TDSQL_DISTRIBUTED BY HASH(lower(`id`))"),
                  ("Z1 HASH(a,b) 多字段", "TDSQL_DISTRIBUTED BY HASH(`a`,`b`)"),
                  ('Z1 HASH("id") 双引号', 'TDSQL_DISTRIBUTED BY HASH("id")')]:
    add("Z", lbl, _Z + tail, "neg", "OFFICIAL",
        "括号内必须恰好一个标识符；带 UK COMMENT 路径须仍报 E999", spans=0, e999=True)
for _m in ("HASH", "RANGE", "LIST"):
    add("Z", "Z2 合法 %s(`id`) 反引号" % _m, _Z + "TDSQL_DISTRIBUTED BY %s(`id`)" % _m,
        "pos", "OFFICIAL", "防收紧过头", spans=1, e999=False)
    add("Z", "Z2 合法 %s(id) 裸名" % _m, _Z + "TDSQL_DISTRIBUTED BY %s(id)" % _m,
        "pos", "OFFICIAL", "防收紧过头", spans=1, e999=False)
add("Z", "Z2 合法 BROADCAST", _Z + "BROADCAST", "pos", "OFFICIAL", "防收紧过头", spans=1, e999=False)
add("Z", "Z2 BROADCAST COMMENT='x'（哨兵后接表选项）", _Z + "BROADCAST COMMENT='x'",
    "unsupported_unproven", "CORPUS",
    "BROADCAST 是终态原子：其后不再接任何表选项。语料 197 条与生产 14 表出现 0 次，"
    "无 TDSQL 官方证据 → 失败关闭（Rev.M 统一口径，撤销 Rev.L 正文的 pos 表述）",
    spans=0, e999=True)
for lbl, sql in [
    ("Z3 单引号表名", "CREATE TABLE 't' (`id` int NOT NULL COMMENT 'i', UNIQUE KEY `uk` (`id`) COMMENT 'u')"),
    ("Z3 双引号表名", 'CREATE TABLE "t" (`id` int NOT NULL COMMENT \'i\', UNIQUE KEY `uk` (`id`) COMMENT \'u\')'),
    ("Z3 单引号表名 + HASH",
     "CREATE TABLE 't' (`id` int NOT NULL COMMENT 'i', UNIQUE KEY `uk` (`id`) COMMENT 'u')"
     " ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(`id`)")]:
    add("Z", lbl, sql, "neg", "OFFICIAL", "表名只接受裸标识符与反引号标识符", e999=True)
_ZT = "(id int NOT NULL COMMENT 'i', UNIQUE KEY uk (id) COMMENT 'u') ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(id)"
for lbl, head in [("Z4 裸表名", "CREATE TABLE t "), ("Z4 反引号表名", "CREATE TABLE `t` "),
                  ("Z4 库限定 `db`.`t`", "CREATE TABLE `db`.`t` "),
                  ("Z4 IF NOT EXISTS", "CREATE TABLE IF NOT EXISTS `t` ")]:
    add("Z", lbl, head + _ZT, "pos", "OFFICIAL", "合法表名形态必须仍可恢复")

# ══════════════════════════════════════════════════════════════════════════
# W 组 —— 目标上下文完整性
# ══════════════════════════════════════════════════════════════════════════
_WB = ("CREATE TABLE `t` (`id` int NOT NULL COMMENT 'i', `sk` int NOT NULL COMMENT 's', "
       "PRIMARY KEY (`id`,`sk`)%s)")
_WUK = ", UNIQUE KEY `uk` (`sk`) COMMENT 'u'"
for _ctx in ("DEFAULT", "CHECKSUM", "INDEX DIRECTORY"):
    for _tgt, _tn in (("BROADCAST", "BROADCAST"), ("TDSQL_DISTRIBUTED BY HASH(`sk`)", "HASH")):
        for _uk, _ul in ((_WUK, "带 UK COMMENT"), ("", "无 UK COMMENT")):
            add("W", "W1 残缺 %s + %s（%s）" % (_ctx, _tn, _ul),
                (_WB % _uk) + " ENGINE=InnoDB %s %s" % (_ctx, _tgt), "neg", "OFFICIAL",
                "残缺表选项上下文必须失败关闭；两条路径分别断言最终 AST",
                spans=0, ast=("NoneType" if _uk else "Command"))
for lbl, opt in [("完整 DEFAULT CHARSET", "DEFAULT CHARSET=utf8mb4"),
                 ("AUTO_INCREMENT=100", "AUTO_INCREMENT=100"),
                 ("COLLATE=utf8mb4_bin", "COLLATE=utf8mb4_bin"),
                 ("COMMENT='x'", "COMMENT='x'"),
                 ("shardkey=sk", "shardkey=sk")]:
    add("W", "W2 %s + BROADCAST" % lbl, (_WB % _WUK) + " ENGINE=InnoDB %s BROADCAST" % opt,
        "pos", "OFFICIAL", "完整表选项正例不得误伤", spans=1, ast="Create")
add("W", "W2 生产形态 全套选项 + BROADCAST",
    (_WB % _WUK) + " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='表' BROADCAST",
    "pos", "CORPUS", "生产同款组合", spans=1, ast="Create")
add("W", "W2 CHECKSUM=1 + BROADCAST（无 TDSQL 证据）",
    (_WB % _WUK) + " ENGINE=InnoDB CHECKSUM=1 BROADCAST", "unsupported_unproven", "CORPUS",
    "CHECKSUM 无 TDSQL 官方证据、语料 0 例 → 失败关闭", spans=0, ast="NoneType")
add("W", "W2 ROW_FORMAT=DYNAMIC + BROADCAST（官方 local_table_option）",
    (_WB % _WUK) + " ENGINE=InnoDB ROW_FORMAT=DYNAMIC BROADCAST", "pos", "OFFICIAL",
    "官方建表页明示 ROW_FORMAT 属 local_table_option（第十轮 BLOCK-J4 更正）",
    spans=1, ast="Create")
_W3 = "CREATE TABLE `t` (`id` int NOT NULL COMMENT 'i', UNIQUE KEY `uk` (`id`) %s) ENGINE=InnoDB"
for lbl, frag in [("W3 USING 缺类型 在 COMMENT 前", "USING COMMENT 'target'"),
                  ("W3 USING 缺类型 在 COMMENT 后", "COMMENT 'target' USING"),
                  ("W3 COMMENT 后非字符串", "COMMENT `x`")]:
    add("W", lbl, _W3 % frag, "neg", "OFFICIAL", "索引选项上下文残缺必须失败关闭", spans=0, e999=True)
for lbl, frag in [("W4 USING BTREE COMMENT", "USING BTREE COMMENT 'x'"),
                  ("W4 纯 COMMENT", "COMMENT 'x'")]:
    add("W", lbl, _W3 % frag, "pos", "OFFICIAL", "索引选项正例必须仍恢复", spans=1, ast="Create")
add("W", "W5 HASH + 二级 PARTITION BY（既有 D5 场景）",
    "CREATE TABLE t_hp (id BIGINT NOT NULL, sk BIGINT NOT NULL, dt DATETIME NOT NULL, "
    "PRIMARY KEY (id, sk)) ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(`sk`) PARTITION BY RANGE (YEAR(dt)) ("
    "PARTITION p2025 VALUES LESS THAN (2026), PARTITION p2026 VALUES LESS THAN (2027))",
    "pos", "PROJECT_ACCEPTED", "D5 场景不得回归", ast="Create")
for _uk, _ul in ((_WUK, "带 UK COMMENT"), ("", "无 UK COMMENT")):
    add("W", "W6 INDEX DIRECTORY='/p' + BROADCAST（%s）" % _ul,
        (_WB % _uk) + " ENGINE=InnoDB INDEX DIRECTORY='/p' BROADCAST",
        "unsupported_unproven", "SQLGLOT_LIMIT",
        "sqlglot 本就不支持 INDEX DIRECTORY，两条路径均与主干一致",
        spans=0, ast="NoneType")

# ══════════════════════════════════════════════════════════════════════════
# H 组 —— TDSQL 规范符合性（key-part / 分区 / 表选项）
# ══════════════════════════════════════════════════════════════════════════
_HUKC = ", UNIQUE KEY `uk` (`sk`) COMMENT 'u'"
_H1 = "CREATE TABLE `t` (`id` INT, `sk` INT, %s) ENGINE=InnoDB"
_H2 = ("CREATE TABLE `t` (`id` INT, `sk` INT, `dt` DATETIME, PRIMARY KEY(`id`,`sk`)%s) "
       "ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(`sk`) %s")
_H3 = ("CREATE TABLE `t` (`id` INT, `sk` INT, PRIMARY KEY(`id`,`sk`)%s) "
       "%s TDSQL_DISTRIBUTED BY HASH(`sk`)")
_H4 = "CREATE TABLE `t` (`id` INT, `sk` INT, PRIMARY KEY(`id`,`sk`)%s) %s"


def _hu(kp):
    return _H1 % ("UNIQUE KEY `uk` %s COMMENT 'x'" % kp)


for lbl, kp in [("空清单 ()", "()"), ("只有逗号 (,)", "(,)"), ("前导逗号 (,id)", "(,`id`)"),
                ("尾随逗号 (id,)", "(`id`,)"), ("连续逗号 (id,,sk)", "(`id`,,`sk`)"),
                ("字符串键 ('id')", "('id')"), ("数字键 (123)", "(123)"),
                ("函数键 (lower(id))", "(lower(`id`))"), ("表达式键 (id+1)", "(`id`+1)"),
                ("前缀长度非数字", "(`id`('x'))"), ("前缀括号未闭合", "(`id`(10)")]:
    add("H1", lbl, _hu(kp), "neg", "OFFICIAL", "key_part 必须是标识符[(正整数)][ASC|DESC]")
for lbl, kp in [("裸列名 (id)", "(id)"), ("反引号列 (`id`)", "(`id`)"),
                ("多列 (`id`,`sk`)", "(`id`,`sk`)"), ("前缀索引 (`id`(10))", "(`id`(10))"),
                ("前缀+多列", "(`id`(10),`sk`)")]:
    add("H2", lbl, _hu(kp), "pos", "OFFICIAL", "官方合法 key_part 必须恢复")
for lbl, kp in [("ASC (`id` ASC)", "(`id` ASC)"), ("DESC (`id` DESC)", "(`id` DESC)"),
                ("前缀+DESC+多列", "(`id`(10) DESC,`sk`)")]:
    add("H2b", lbl, _hu(kp), "pos", "OFFICIAL",
        "官方 key_part 含 [ASC|DESC]；sqlglot 30.x 对其 ParseError，由辅助掩码绕开")
for lbl, pt in [("裸 PARTITION BY", "PARTITION BY"),
                ("PARTITION BY DEFAULT", "PARTITION BY DEFAULT"),
                ("方法为字符串 'HASH'", "PARTITION BY 'HASH'(`sk`)"),
                ("空括号 HASH()", "PARTITION BY HASH()"),
                ("未闭合 HASH(`sk`", "PARTITION BY HASH(`sk`"),
                ("合法分区后尾随垃圾", "PARTITION BY HASH(`sk`) GARBAGE"),
                ("分区体内第二个方言声明",
                 "PARTITION BY LIST (`sk`) (PARTITION p1 VALUES IN (1) BROADCAST)"),
                ("分区体内藏分号",
                 "PARTITION BY LIST (`sk`) (PARTITION p1 VALUES IN (1); )")]:
    add("H3", lbl + " 带UK", _H2 % (_HUKC, pt), "neg", "OFFICIAL", "非法分区子句失败关闭")
    add("H3", lbl + " 无UK", _H2 % ("", pt), "neg", "OFFICIAL", "非法分区子句失败关闭")
for lbl, pt in [("RANGE+分区定义表",
                 "PARTITION BY RANGE (YEAR(`dt`)) (PARTITION p1 VALUES LESS THAN (2026), "
                 "PARTITION p2 VALUES LESS THAN (2027))"),
                ("LIST+分区定义表+partition ENGINE",
                 "PARTITION BY LIST (`sk`) (PARTITION p1 VALUES IN (1) ENGINE = InnoDB)"),
                ("LIST+VALUES IN 多值",
                 "PARTITION BY LIST (`sk`) (PARTITION p1 VALUES IN (1,2), PARTITION p2 VALUES IN (3,4))")]:
    add("H4", lbl + " 带UK", _H2 % (_HUKC, pt), "pos", "OFFICIAL", "官方二级分区 Range/List 必须恢复")
    add("H4", lbl + " 无UK", _H2 % ("", pt), "pos", "OFFICIAL", "官方二级分区 Range/List 必须恢复")
for lbl, pt in [("RANGE+MAXVALUE 兜底分区",
                 "PARTITION BY RANGE (`sk`) (PARTITION p1 VALUES LESS THAN (10), "
                 "PARTITION pm VALUES LESS THAN MAXVALUE)")]:
    add("H4c", lbl + " 带UK", _H2 % (_HUKC, pt), "pos_known", "SQLGLOT_LIMIT",
        "KFN-1（用户 2026-08-26 批准）：sqlglot 30.x 对 MAXVALUE ParseError，语料/生产 0 例")
    add("H4c", lbl + " 无UK", _H2 % ("", pt), "pos_known", "SQLGLOT_LIMIT",
        "KFN-1（用户 2026-08-26 批准）")
for lbl, pt in [("HASH+PARTITIONS n", "PARTITION BY HASH(`sk`) PARTITIONS 4"),
                ("LINEAR HASH", "PARTITION BY LINEAR HASH(`sk`)"),
                ("KEY(col)", "PARTITION BY KEY(`sk`)"),
                ("RANGE COLUMNS", "PARTITION BY RANGE COLUMNS(`sk`) (PARTITION p1 VALUES LESS THAN (10))")]:
    add("H4b", lbl + " 带UK", _H2 % (_HUKC, pt), "neg", "OFFICIAL",
        "官方二级分区只列 Range 与 List，其余保守失败关闭")
    add("H4b", lbl + " 无UK", _H2 % ("", pt), "neg", "OFFICIAL",
        "官方二级分区只列 Range 与 List，其余保守失败关闭")
for lbl, opt in [("ENGINE=123", "ENGINE=123"), ("ROW_FORMAT=123", "ENGINE=InnoDB ROW_FORMAT=123"),
                 ("ROW_FORMAT='x'", "ENGINE=InnoDB ROW_FORMAT='x'"),
                 ("ROW_FORMAT=UNKNOWN", "ENGINE=InnoDB ROW_FORMAT=UNKNOWN"),
                 ("SHARDKEY=123", "ENGINE=InnoDB shardkey=123"),
                 ("SHARDKEY='sk'", "ENGINE=InnoDB shardkey='sk'"),
                 ("AUTO_INCREMENT=abc", "ENGINE=InnoDB AUTO_INCREMENT=abc"),
                 ("COMMENT=123", "ENGINE=InnoDB COMMENT=123"),
                 ("PACK_KEYS=7", "ENGINE=InnoDB PACK_KEYS=7"),
                 ("STATS_PERSISTENT='1'", "ENGINE=InnoDB STATS_PERSISTENT='1'"),
                 ("CHARSET=123", "ENGINE=InnoDB DEFAULT CHARSET=123")]:
    add("H5", lbl + " 带UK", _H3 % (_HUKC, opt), "neg", "OFFICIAL", "表选项值谓词按类型校验")
    add("H5", lbl + " 无UK", _H3 % ("", opt), "neg", "OFFICIAL", "表选项值谓词按类型校验")
for lbl, opt in [("ENGINE=InnoDB", "ENGINE=InnoDB"), ("ENGINE='InnoDB'", "ENGINE='InnoDB'"),
                 ("AUTO_INCREMENT=100", "ENGINE=InnoDB AUTO_INCREMENT=100"),
                 ("STATS_AUTO_RECALC=1", "ENGINE=InnoDB STATS_AUTO_RECALC=1"),
                 ("STATS_SAMPLE_PAGES=8", "ENGINE=InnoDB STATS_SAMPLE_PAGES=8"),
                 ("生产同款全套", "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='表'")]:
    add("H6", lbl + " 带UK", _H3 % (_HUKC, opt), "pos", "OFFICIAL", "官方/语料实证的合法取值必须恢复")
for lbl, opt in [("shardkey=sk", "ENGINE=InnoDB shardkey=sk"),
                 ("shardkey=noshardkey_allset", "ENGINE=InnoDB shardkey=noshardkey_allset"),
                 ("多列 shardkey=(id,sk)", "ENGINE=InnoDB shardkey=(id,sk)")]:
    add("H6", lbl + " 带UK", _H4 % (_HUKC, opt), "pos", "TARGET_INSTANCE",
        "shardkey 本身即一级分布声明，不能再拼 TDSQL_DISTRIBUTED")
for lbl, opt in [("ROW_FORMAT=DYNAMIC", "ENGINE=InnoDB ROW_FORMAT=DYNAMIC"),
                 ("ROW_FORMAT=DEFAULT", "ENGINE=InnoDB ROW_FORMAT=DEFAULT"),
                 ("ROW_FORMAT=FIXED", "ENGINE=InnoDB ROW_FORMAT=FIXED"),
                 ("ROW_FORMAT=COMPRESSED", "ENGINE=InnoDB ROW_FORMAT=COMPRESSED"),
                 ("STATS_PERSISTENT=1", "ENGINE=InnoDB STATS_PERSISTENT=1"),
                 ("STATS_PERSISTENT=DEFAULT", "ENGINE=InnoDB STATS_PERSISTENT=DEFAULT")]:
    add("H6", lbl + " 带UK", _H3 % (_HUKC, opt), "pos", "OFFICIAL",
        "官方建表页明示属 local_table_option（第十轮 BLOCK-J4 更正）")
for lbl, opt in [("PACK_KEYS=1", "ENGINE=InnoDB PACK_KEYS=1"),
                 ("PACK_KEYS=DEFAULT", "ENGINE=InnoDB PACK_KEYS=DEFAULT"),
                 ("CHECKSUM=1", "ENGINE=InnoDB CHECKSUM=1"),
                 ("KEY_BLOCK_SIZE=8", "ENGINE=InnoDB KEY_BLOCK_SIZE=8"),
                 ("AVG_ROW_LENGTH=100", "ENGINE=InnoDB AVG_ROW_LENGTH=100"),
                 ("MAX_ROWS=1000", "ENGINE=InnoDB MAX_ROWS=1000"),
                 ("MIN_ROWS=1", "ENGINE=InnoDB MIN_ROWS=1"),
                 ("DELAY_KEY_WRITE=1", "ENGINE=InnoDB DELAY_KEY_WRITE=1")]:
    add("H6b", lbl + " 带UK", _H3 % (_HUKC, opt), "unsupported_unproven", "CORPUS",
        "无 TDSQL / 目标实例证据 → 失败关闭，不冒充合法也不冒充非法")

# ══════════════════════════════════════════════════════════════════════════
# P 组 —— DEF-3：PRIMARY 索引 COMMENT（用户确认内网实际存在该形态）
# ══════════════════════════════════════════════════════════════════════════
_PUKC = ", UNIQUE KEY `uk` (`sk`) COMMENT 'u'"
_P = "CREATE TABLE `t` (`id` INT, `sk` INT%s) %s"
for lbl, defn, tail in [
    ("单列 PRIMARY COMMENT", ", PRIMARY KEY (`id`) COMMENT 'pk'", "ENGINE=InnoDB"),
    ("多列 PRIMARY COMMENT", ", PRIMARY KEY (`id`,`sk`) COMMENT 'pk'", "ENGINE=InnoDB"),
    ("PRIMARY USING BTREE COMMENT", ", PRIMARY KEY (`id`) USING BTREE COMMENT 'pk'", "ENGINE=InnoDB"),
    ("PRIMARY COMMENT + shardkey", ", PRIMARY KEY (`id`) COMMENT 'pk'", "ENGINE=InnoDB shardkey=id"),
    ("PRIMARY COMMENT + BROADCAST", ", PRIMARY KEY (`id`) COMMENT 'pk'", "ENGINE=InnoDB BROADCAST"),
    ("PRIMARY COMMENT + 方言 HASH", ", PRIMARY KEY (`id`,`sk`) COMMENT 'pk'",
     "ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(`sk`)"),
    ("PRIMARY + UNIQUE 双 COMMENT", ", PRIMARY KEY (`id`) COMMENT 'pk'" + _PUKC, "ENGINE=InnoDB"),
    ("PRIMARY COMMENT + 普通索引 COMMENT",
     ", PRIMARY KEY (`id`) COMMENT 'pk', KEY `k` (`sk`) COMMENT 'idx'", "ENGINE=InnoDB"),
]:
    add("P1", lbl, _P % (defn, tail), "pos", "TARGET_INSTANCE",
        "DEF-3：用户确认内网实际存在 PRIMARY KEY … COMMENT 的表")
for lbl, defn in [
    ("PRIMARY 后带索引名", ", PRIMARY KEY `pk` (`id`) COMMENT 'x'"),
    ("PRIMARY 空键列", ", PRIMARY KEY () COMMENT 'x'"),
    ("PRIMARY COMMENT 非字符串", ", PRIMARY KEY (`id`) COMMENT `x`"),
    ("PRIMARY 重复 COMMENT", ", PRIMARY KEY (`id`) COMMENT 'a' COMMENT 'b'"),
    ("PRIMARY USING HASH", ", PRIMARY KEY (`id`) USING HASH COMMENT 'x'"),
    ("PRIMARY 前后置 USING", ", PRIMARY KEY USING BTREE (`id`) USING BTREE COMMENT 'x'"),
]:
    add("P2", lbl, _P % (defn, "ENGINE=InnoDB"), "neg", "OFFICIAL",
        "扩大恢复范围后的边界证明：非法近邻必须仍失败关闭")

# ══════════════════════════════════════════════════════════════════════════
# R11 组 —— 第十一轮复审报告 §4~§9、§11 的全部反例（BLOCK-11-07 第 5 条）
# ══════════════════════════════════════════════════════════════════════════
_RPK = ", PRIMARY KEY (`id`) COMMENT 'pk'"
_R1 = "CREATE TABLE `t` (`id` INT%s) ENGINE=InnoDB" % _RPK
_R2 = "CREATE TABLE `t` (`id` INT, `sk` INT%s) ENGINE=InnoDB" % _RPK

# —— BLOCK-11-01：MySQL 可执行注释 ——
add("R11-01", "/*!50100 PARTITION BY RANGE() 空方法参数 */",
    _R1 + "\n/*!50100 PARTITION BY RANGE() (PARTITION p0 VALUES LESS THAN (10)) */",
    "neg", "REVIEW_11", "可执行注释 payload 必须逐 token 验证，非法则整句失败关闭")
add("R11-01", "/*!50100 两条 PARTITION BY */",
    _R1 + "\n/*!50100 PARTITION BY LIST (`id`) (PARTITION p0 VALUES IN (1))"
          " PARTITION BY LIST (`id`) (PARTITION p1 VALUES IN (2)) */",
    "neg", "REVIEW_11", "payload 必须完整消费到末尾，多余 token 一律拒绝")
add("R11-01", "/*!50100 EVIL OPTION */", _R1 + "\n/*!50100 EVIL OPTION */",
    "neg", "REVIEW_11", "payload 首 token 必须是 PARTITION BY")
add("R11-01", "两个可执行注释", _R1 + "\n/*!50100 PARTITION BY LIST (`id`) (PARTITION p0 VALUES IN (1)) */"
    "\n/*!50100 PARTITION BY LIST (`id`) (PARTITION p1 VALUES IN (2)) */",
    "neg", "REVIEW_11", "至多一个可执行注释")
add("R11-01", "正例 /*!50100 PARTITION BY LIST 合法 */",
    _R1 + "\n/*!50100 PARTITION BY LIST (`id`) (PARTITION p0 VALUES IN (1) ENGINE = InnoDB) */",
    "pos", "OFFICIAL", "mysqldump 输出的官方二级分区形态必须恢复")
add("R11-01", "普通块注释内的伪分区（不得被当作可执行注释）",
    _R1 + "\n/* PARTITION BY RANGE() (PARTITION p0 VALUES LESS THAN (10)) */",
    "pos", "REVIEW_11", "普通注释仍保持不可见，不参与验证也不阻断恢复")

# —— BLOCK-11-02：表尾迁移图回环 ——
add("R11-02", "DIST → PARTITION → DIST",
    _R2 + " TDSQL_DISTRIBUTED BY HASH(`sk`) PARTITION BY RANGE(`id`) (PARTITION p0 VALUES LESS THAN (10))"
          " TDSQL_DISTRIBUTED BY HASH(`sk`)",
    "neg", "REVIEW_11", "一级分布至多一个；表尾图必须无环")
add("R11-02", "shardkey → PARTITION → DIST",
    _R2 + " shardkey=id PARTITION BY RANGE(`id`) (PARTITION p0 VALUES LESS THAN (10))"
          " TDSQL_DISTRIBUTED BY HASH(`sk`)",
    "neg", "REVIEW_11", "shardkey 与 TDSQL_DISTRIBUTED 同为一级分布，互斥")
add("R11-02", "PARTITION → DIST → PARTITION",
    _R2 + " PARTITION BY RANGE(`id`) (PARTITION p0 VALUES LESS THAN (10))"
          " TDSQL_DISTRIBUTED BY HASH(`sk`) PARTITION BY LIST(`id`) (PARTITION p1 VALUES IN (1))",
    "neg", "REVIEW_11", "二级分区至多一个")
add("R11-02", "正例 shardkey + PARTITION（官方二级分区原例）",
    _R2 + " shardkey=sk PARTITION BY RANGE(`id`) (PARTITION p0 VALUES LESS THAN (10))",
    "pos", "OFFICIAL", "LEGACY_PARTITION profile")
add("R11-02", "NEW_SECONDARY：DIST + TDSQL_PARTITION BY RANGE",
    _R2 + " TDSQL_DISTRIBUTED BY HASH(`sk`) TDSQL_PARTITION BY RANGE(`id`)"
          " (PARTITION p0 VALUES LESS THAN (10))",
    "unsupported_unproven", "CORPUS",
    "腾讯新版二级分区语法：无目标实例证据、语料 0 例 → 已具名登记为 NEW_SECONDARY profile 但不放行")
add("R11-02", "NEW_SECONDARY：shardkey + TDSQL_PARTITION BY LIST",
    _R2 + " shardkey=sk TDSQL_PARTITION BY LIST(`id`) (PARTITION p0 VALUES IN (1))",
    "unsupported_unproven", "CORPUS", "同上")
add("R11-02", "正例 PARTITION + DIST（官方原例 tb_sub_r_l）",
    _R2 + " PARTITION BY LIST(`id`) (PARTITION p0 VALUES IN (1)) TDSQL_DISTRIBUTED BY RANGE(`sk`)",
    "pos", "OFFICIAL", "LEGACY_PARTITION profile")

# —— BLOCK-11-03：广播哨兵混型 ——
add("R11-03", "哨兵 + PARTITION BY", _R1 + " shardkey=noshardkey_allset"
    " PARTITION BY LIST(`id`) (PARTITION p0 VALUES IN (1))",
    "neg", "REVIEW_11", "广播哨兵是终态原子，其后不得再有二级分区")
add("R11-03", "括号哨兵 shardkey=(noshardkey_allset)",
    _R1 + " shardkey=(noshardkey_allset)", "neg", "REVIEW_11",
    "哨兵只接受裸形态，括号形态无证据")
add("R11-03", "混合 shardkey=(noshardkey_allset,id)",
    _R1 + " shardkey=(noshardkey_allset,id)", "neg", "REVIEW_11",
    "哨兵不得与普通分片键混列，否则 R054/R077 边界可被伪造")
add("R11-03", "正例 裸哨兵 shardkey=noshardkey_allset",
    _R1 + " shardkey=noshardkey_allset", "pos", "TARGET_INSTANCE",
    "用户冻结：目标实例广播表哨兵形态")
add("R11-03", "BROADCAST 关键字 + shardkey（ADJ-6 表征）",
    _R1 + " shardkey=id BROADCAST", "characterization", "USER_DECISION",
    "ADJ-6：用户冻结的表征行为，不代表 TDSQL 合法", expect_pos=True)

# —— BLOCK-11-06：列属性 COLUMN_FORMAT / ENGINE_ATTRIBUTE / STORAGE ——
add("R11-06", "COLUMN_FORMAT DYNAMIC",
    "CREATE TABLE `t` (`id` INT COLUMN_FORMAT DYNAMIC%s) ENGINE=InnoDB" % _RPK,
    "pos", "OFFICIAL", "官方列属性；sqlglot 不认 → 作辅助掩码剥离后恢复")
add("R11-06", "ENGINE_ATTRIBUTE='x'",
    "CREATE TABLE `t` (`id` INT ENGINE_ATTRIBUTE='x'%s) ENGINE=InnoDB" % _RPK,
    "pos", "OFFICIAL", "同上")
add("R11-06", "SECONDARY_ENGINE_ATTRIBUTE='x'",
    "CREATE TABLE `t` (`id` INT SECONDARY_ENGINE_ATTRIBUTE='x'%s) ENGINE=InnoDB" % _RPK,
    "unsupported_unproven", "CORPUS",
    "腾讯官方建表页列级清单未列出（与列级 STORAGE 同处置）；语料 0 例 → 失败关闭")
add("R11-06", "列级 STORAGE DISK（NDB 专属，非 InnoDB 官方枚举）",
    "CREATE TABLE `t` (`id` INT STORAGE DISK%s) ENGINE=InnoDB" % _RPK,
    "unsupported_unproven", "CORPUS", "无 TDSQL/目标实例证据，语料 0 例 → 失败关闭")
add("R11-06", "COLUMN_FORMAT 非法取值 COLUMN_FORMAT=1",
    "CREATE TABLE `t` (`id` INT COLUMN_FORMAT=1%s) ENGINE=InnoDB" % _RPK,
    "neg", "REVIEW_11", "官方枚举只有 FIXED/DYNAMIC/DEFAULT，且不带等号")

# —— MAJOR-11-01：FULLTEXT / SPATIAL 裸形态 ——
add("R11-M1", "FULLTEXT KEY `f` (`a`)",
    "CREATE TABLE `t` (`id` INT, `a` VARCHAR(20), FULLTEXT KEY `f` (`a`)%s) ENGINE=InnoDB" % _RPK,
    "pos", "OFFICIAL", "带 KEY 的形态")
add("R11-M1", "FULLTEXT INDEX `f` (`a`)",
    "CREATE TABLE `t` (`id` INT, `a` VARCHAR(20), FULLTEXT INDEX `f` (`a`)%s) ENGINE=InnoDB" % _RPK,
    "pos", "OFFICIAL", "带 INDEX 的形态")
add("R11-M1", "FULLTEXT (`a`)（省略 KEY/INDEX）",
    "CREATE TABLE `t` (`id` INT, `a` VARCHAR(20), FULLTEXT (`a`)%s) ENGINE=InnoDB" % _RPK,
    "pos", "OFFICIAL", "官方语法 KEY/INDEX 可省略；入口判据必须与消费器同源")
add("R11-M1", "FULLTEXT `f` (`a`)（有名无 KEY/INDEX）",
    "CREATE TABLE `t` (`id` INT, `a` VARCHAR(20), FULLTEXT `f` (`a`)%s) ENGINE=InnoDB" % _RPK,
    "pos", "OFFICIAL", "同上")
add("R11-M1", "SPATIAL KEY `s` (`g`)",
    "CREATE TABLE `t` (`id` INT, `g` GEOMETRY NOT NULL, SPATIAL KEY `s` (`g`)%s) ENGINE=InnoDB" % _RPK,
    "pos", "OFFICIAL", "SPATIAL 索引按 NORMAL 处理（用户冻结决策 SPATIAL→NORMAL）")
add("R11-M1", "SPATIAL (`g`)（省略 KEY/INDEX）",
    "CREATE TABLE `t` (`id` INT, `g` GEOMETRY NOT NULL, SPATIAL (`g`)%s) ENGINE=InnoDB" % _RPK,
    "pos", "OFFICIAL", "同上")
add("R11-M1", "FULLTEXT 缺括号（非法）",
    "CREATE TABLE `t` (`id` INT, `a` VARCHAR(20), FULLTEXT `f`%s) ENGINE=InnoDB" % _RPK,
    "neg", "REVIEW_11", "缺键列列表必须失败关闭")
add("R11-M1", "列名恰为 `fulltext`（反向鉴别：不得误当索引）",
    "CREATE TABLE `t` (`id` INT, `fulltext` VARCHAR(20)%s) ENGINE=InnoDB" % _RPK,
    "pos", "REVIEW_11", "反引号标识符必须仍走列定义消费器")
add("R11-M1", "列名恰为 `spatial`（反向鉴别）",
    "CREATE TABLE `t` (`id` INT, `spatial` VARCHAR(20)%s) ENGINE=InnoDB" % _RPK,
    "pos", "REVIEW_11", "同上")

# ══════════════════════════════════════════════════════════════════════════
# TY 组 —— TDSQL 官方数据类型的双向闭合矩阵（BLOCK-11-04）
#          模板固定为"一列待测类型 + 一个必须恢复的 UNIQUE COMMENT"
# ══════════════════════════════════════════════════════════════════════════
_TY = "CREATE TABLE `t` (`c` %s, `sk` INT, UNIQUE KEY `uk` (`sk`) COMMENT 'u') ENGINE=InnoDB"
# KFN-3：sqlglot 30.14.0 / 29.0.0 / 30.17.0 三版一致 ParseError 的官方类型。
# 实测：去掉 UNIQUE COMMENT 的普通建表在**修复前后**都报 E999，行为完全一致，
# 本次修复既不改善也不恶化，仅登记能力边界。
_TY_KFN3 = ("CHAR(10) BINARY", "POINT", "LINESTRING", "POLYGON",
            "MULTIPOINT", "MULTILINESTRING", "MULTIPOLYGON", "GEOMETRYCOLLECTION")
_TY_LEGAL = [
    "TINYINT", "TINYINT(4)", "TINYINT UNSIGNED", "TINYINT(3) UNSIGNED ZEROFILL",
    "SMALLINT", "SMALLINT(6)", "MEDIUMINT", "MEDIUMINT(9)",
    "INT", "INT(11)", "INT UNSIGNED", "INT(10) UNSIGNED ZEROFILL", "INTEGER", "INTEGER(11)",
    "BIGINT", "BIGINT(20)", "BIGINT UNSIGNED",
    "DECIMAL", "DECIMAL(10)", "DECIMAL(10,2)", "DECIMAL(65,30)", "DECIMAL(10,2) UNSIGNED",
    "NUMERIC(10,2)", "FIXED(10,2)",
    "FLOAT", "FLOAT(10,2)", "REAL", "REAL(10,2)", "DOUBLE", "DOUBLE(10,2)",
    "DOUBLE PRECISION", "DOUBLE PRECISION(10,2)",
    "CHAR", "CHAR(0)", "CHAR(1)", "CHAR(255)", "CHAR(10) BINARY",
    "VARCHAR(0)", "VARCHAR(255)", "VARCHAR(65535)",
    "BINARY", "BINARY(16)", "VARBINARY(255)",
    "TINYTEXT", "TEXT", "TEXT(1000)", "MEDIUMTEXT", "LONGTEXT",
    "TINYBLOB", "BLOB", "BLOB(1000)", "MEDIUMBLOB", "LONGBLOB",
    "ENUM('a','b')", "SET('a','b')",
    "DATE", "YEAR", "YEAR(4)", "TIME", "TIME(6)", "DATETIME", "DATETIME(3)",
    "TIMESTAMP", "TIMESTAMP(6)",
    "BIT", "BIT(1)", "BIT(64)", "BOOL", "BOOLEAN", "JSON",
    "GEOMETRY", "POINT", "LINESTRING", "POLYGON",
    "MULTIPOINT", "MULTILINESTRING", "MULTIPOLYGON", "GEOMETRYCOLLECTION",
]
_TY_ILLEGAL = [
    ("DECIMAL(1,2)", "scale 不得大于 precision"),
    ("DECIMAL(66,0)", "precision 上限 65"),
    ("DECIMAL(65,31)", "scale 上限 30"),
    ("BIT(0)", "BIT 下限 1"),
    ("BIT(65)", "BIT 上限 64"),
    ("CHAR(256)", "CHAR 上限 255"),
    ("VARCHAR(65536)", "VARCHAR 声明长度上限 65535"),
    ("VARCHAR", "VARCHAR 长度必填"),
    ("YEAR(999)", "YEAR 只接受省略或 4"),
    ("YEAR(2)", "MySQL 5.7 起 YEAR(2) 已移除"),
    ("TIME(7)", "fsp 上限 6"),
    ("DATETIME(7)", "fsp 上限 6"),
    ("TIMESTAMP(7)", "fsp 上限 6"),
    ("ENUM", "ENUM 必须带括号值表"),
    ("SET", "SET 必须带括号值表"),
    ("ENUM()", "至少一个字符串值"),
    ("SET()", "至少一个字符串值"),
    ("ENUM(1,2)", "值必须是字符串字面量"),
    ("DATE UNSIGNED", "时间族不接受数值属性"),
    ("VARCHAR(10) UNSIGNED", "字符族不接受数值属性"),
    ("JSON BINARY", "JSON 不接受任何类型属性"),
    ("INT BINARY", "数值族不接受 BINARY"),
    ("TEXT ZEROFILL", "字符族不接受 ZEROFILL"),
    ("NOSUCHTYPE", "未登记类型名"),
    ("NOSUCHTYPE(3)", "未登记类型名"),
]
for _t in _TY_LEGAL:
    if _t in _TY_KFN3:
        add("TY-K", _t, _TY % _t, "pos_known", "SQLGLOT_LIMIT",
            "KFN-3：sqlglot 三版一致 ParseError，修复前后行为完全一致")
    else:
        add("TY-P", _t, _TY % _t, "pos", "OFFICIAL",
            "官方合法类型必须恢复；别名与展示属性在源侧规范化后与 AST 一致")
for _t, _why in _TY_ILLEGAL:
    add("TY-N", _t, _TY % _t, "neg", "REVIEW_11", _why)
# DEFAULT / ON UPDATE 时间函数精度
for _d, _k, _why in [
    ("DATETIME DEFAULT CURRENT_TIMESTAMP", "pos", "官方合法"),
    ("DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6)", "pos", "官方合法"),
    ("DATETIME DEFAULT CURRENT_TIMESTAMP(7)", "neg", "时间函数精度上限 6"),
    ("TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP", "pos", "官方合法"),
    ("TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP(7)", "neg", "同上"),
]:
    add("TY-D" if _k == "pos" else "TY-N", _d, _TY % _d, _k,
        "OFFICIAL" if _k == "pos" else "REVIEW_11", _why)

# ══════════════════════════════════════════════════════════════════════════
# M 组 —— 候选 AST 结构守恒门禁的反向鉴别（BLOCK-11-05 白盒变异测试）
#         每条 (源 SQL, 正确候选 SQL, [变异候选 SQL...])
#         正确候选必须过门禁；每个变异候选必须被门禁拒绝。
# ══════════════════════════════════════════════════════════════════════════
MUTATIONS = []


def mut(title, src, good, muts):
    cid = "M-%02d" % (len(MUTATIONS) + 1)
    MUTATIONS.append({"cid": cid, "title": title, "src": src, "good": good, "muts": muts})


mut("UNIQUE 注释恢复",
    "CREATE TABLE `t` (`id` INT NOT NULL DEFAULT 7, `sk` VARCHAR(32), "
    "UNIQUE KEY `uk` (`sk`(8)) USING BTREE COMMENT 'u') ENGINE=InnoDB",
    "CREATE TABLE `t` (`id` INT NOT NULL DEFAULT 7, `sk` VARCHAR(32), "
    "UNIQUE KEY `uk` (`sk`(8)) USING BTREE) ENGINE=InnoDB",
    [("丢 NOT NULL", "CREATE TABLE `t` (`id` INT DEFAULT 7, `sk` VARCHAR(32), UNIQUE KEY `uk` (`sk`(8)) USING BTREE) ENGINE=InnoDB"),
     ("丢 DEFAULT", "CREATE TABLE `t` (`id` INT NOT NULL, `sk` VARCHAR(32), UNIQUE KEY `uk` (`sk`(8)) USING BTREE) ENGINE=InnoDB"),
     ("改列类型", "CREATE TABLE `t` (`id` BIGINT NOT NULL DEFAULT 7, `sk` VARCHAR(32), UNIQUE KEY `uk` (`sk`(8)) USING BTREE) ENGINE=InnoDB"),
     ("改类型长度", "CREATE TABLE `t` (`id` INT NOT NULL DEFAULT 7, `sk` VARCHAR(64), UNIQUE KEY `uk` (`sk`(8)) USING BTREE) ENGINE=InnoDB"),
     ("改列名", "CREATE TABLE `t` (`id` INT NOT NULL DEFAULT 7, `zz` VARCHAR(32), UNIQUE KEY `uk` (`sk`(8)) USING BTREE) ENGINE=InnoDB"),
     ("UNIQUE→KEY", "CREATE TABLE `t` (`id` INT NOT NULL DEFAULT 7, `sk` VARCHAR(32), KEY `uk` (`sk`(8)) USING BTREE) ENGINE=InnoDB"),
     ("UNIQUE→PRIMARY", "CREATE TABLE `t` (`id` INT NOT NULL DEFAULT 7, `sk` VARCHAR(32), PRIMARY KEY (`sk`)) ENGINE=InnoDB"),
     ("改索引名", "CREATE TABLE `t` (`id` INT NOT NULL DEFAULT 7, `sk` VARCHAR(32), UNIQUE KEY `vv` (`sk`(8)) USING BTREE) ENGINE=InnoDB"),
     ("改键列", "CREATE TABLE `t` (`id` INT NOT NULL DEFAULT 7, `sk` VARCHAR(32), UNIQUE KEY `uk` (`id`) USING BTREE) ENGINE=InnoDB"),
     ("丢前缀长度", "CREATE TABLE `t` (`id` INT NOT NULL DEFAULT 7, `sk` VARCHAR(32), UNIQUE KEY `uk` (`sk`) USING BTREE) ENGINE=InnoDB"),
     ("丢 USING", "CREATE TABLE `t` (`id` INT NOT NULL DEFAULT 7, `sk` VARCHAR(32), UNIQUE KEY `uk` (`sk`(8))) ENGINE=InnoDB"),
     ("少一个定义项", "CREATE TABLE `t` (`id` INT NOT NULL DEFAULT 7, UNIQUE KEY `uk` (`sk`(8)) USING BTREE) ENGINE=InnoDB"),
     ("多一个定义项", "CREATE TABLE `t` (`id` INT NOT NULL DEFAULT 7, `sk` VARCHAR(32), `x` INT, UNIQUE KEY `uk` (`sk`(8)) USING BTREE) ENGINE=InnoDB"),
     ("换表名", "CREATE TABLE `other` (`id` INT NOT NULL DEFAULT 7, `sk` VARCHAR(32), UNIQUE KEY `uk` (`sk`(8)) USING BTREE) ENGINE=InnoDB"),
     ("定义项换序", "CREATE TABLE `t` (`sk` VARCHAR(32), `id` INT NOT NULL DEFAULT 7, UNIQUE KEY `uk` (`sk`(8)) USING BTREE) ENGINE=InnoDB")])
mut("PRIMARY 注释恢复（后置 USING）",
    "CREATE TABLE `t` (`id` INT, `sk` INT, PRIMARY KEY (`id`) USING BTREE COMMENT 'pk') ENGINE=InnoDB",
    "CREATE TABLE `t` (`id` INT, `sk` INT, PRIMARY KEY (`id`) USING BTREE) ENGINE=InnoDB",
    [("丢 USING（PRIMARY）", "CREATE TABLE `t` (`id` INT, `sk` INT, PRIMARY KEY (`id`)) ENGINE=InnoDB"),
     ("改主键列", "CREATE TABLE `t` (`id` INT, `sk` INT, PRIMARY KEY (`sk`) USING BTREE) ENGINE=InnoDB"),
     ("PRIMARY→UNIQUE", "CREATE TABLE `t` (`id` INT, `sk` INT, UNIQUE KEY `id` (`id`) USING BTREE) ENGINE=InnoDB"),
     ("主键多一列", "CREATE TABLE `t` (`id` INT, `sk` INT, PRIMARY KEY (`id`,`sk`) USING BTREE) ENGINE=InnoDB")])
mut("无 USING 的 PRIMARY：不得凭空多出 USING",
    "CREATE TABLE `t` (`id` INT, `sk` INT, PRIMARY KEY (`id`) COMMENT 'pk') ENGINE=InnoDB",
    "CREATE TABLE `t` (`id` INT, `sk` INT, PRIMARY KEY (`id`)) ENGINE=InnoDB",
    [("凭空 USING（PRIMARY）", "CREATE TABLE `t` (`id` INT, `sk` INT, PRIMARY KEY (`id`) USING BTREE) ENGINE=InnoDB")])
mut("无 USING 的 KEY：不得凭空多出 USING",
    "CREATE TABLE `t` (`id` INT, `sk` INT, UNIQUE KEY `uk` (`id`) COMMENT 'u', KEY `k` (`sk`)) ENGINE=InnoDB",
    "CREATE TABLE `t` (`id` INT, `sk` INT, UNIQUE KEY `uk` (`id`), KEY `k` (`sk`)) ENGINE=InnoDB",
    [("凭空 USING（KEY 后置）", "CREATE TABLE `t` (`id` INT, `sk` INT, UNIQUE KEY `uk` (`id`), KEY `k` (`sk`) USING BTREE) ENGINE=InnoDB"),
     ("凭空 USING（KEY 前置）", "CREATE TABLE `t` (`id` INT, `sk` INT, UNIQUE KEY `uk` (`id`), KEY `k` USING BTREE (`sk`)) ENGINE=InnoDB")])
mut("二级分区保真",
    "CREATE TABLE `t` (`id` INT, `sk` INT, UNIQUE KEY `uk` (`id`) COMMENT 'u') "
    "ENGINE=InnoDB shardkey=sk PARTITION BY RANGE(`sk`) (PARTITION p0 VALUES LESS THAN (10))",
    "CREATE TABLE `t` (`id` INT, `sk` INT, UNIQUE KEY `uk` (`id`)) "
    "ENGINE=InnoDB PARTITION BY RANGE(`sk`) (PARTITION p0 VALUES LESS THAN (10))",
    [("分区被抹掉", "CREATE TABLE `t` (`id` INT, `sk` INT, UNIQUE KEY `uk` (`id`)) ENGINE=InnoDB")])

# 模糊测试参数（不变量：长度恒等 + 差异全部落在 span 内 + 不抛异常）
FUZZ = {"seed": 20260826, "n": 6000}
```

### `tests/test_parser_recovery_manifest.py`

参数化 pytest：判据全部来自 manifest

```python
# -*- coding: utf-8 -*-
"""按 manifest 判据逐条执行（第十一轮 BLOCK-11-07）。

判据完全来自 `tests/parser_recovery_manifest.py`，本文件**不含任何用例数据**——
新增/修改用例只改 manifest，本文件与 §7.1 的表格都自动跟随。
"""
import io
import random

import pytest
import sqlglot

from backend.engine.checker import RuleChecker
from backend.engine.parser import parser_legacy as PL
from backend.engine.parser.parser_legacy import SQLParser
from tests.parser_recovery_manifest import CASES, FUZZ, MUTATIONS

_sp = SQLParser()
_ck = RuleChecker()
_rid_cache = {}


def _rid(sql, inst):
    key = (sql, inst)
    if key not in _rid_cache:
        _rid_cache[key] = frozenset(
            v.rule_id for v in _ck.audit_sql(sql, instance_type=inst).violations)
    return _rid_cache[key]


def _plan_and_spans(sql):
    plan = PL._plan_recovery(sql, "mysql")
    if plan is None:
        return None, []
    return plan, list(plan["primary_spans"]) + list(plan["auxiliary_spans"])


def _out_of_span_chars(sql, spans):
    """越界改写字符数；长度不恒等直接判 -1。"""
    out = PL._blank_spans(sql, spans)
    if out is None or len(out) != len(sql):
        return -1
    return sum(1 for i, ch in enumerate(sql)
               if ch != out[i] and not any(a <= i <= b for a, b in spans))


# ── 判据分派 ────────────────────────────────────────────────────────────────

def _check_contract():
    node = sqlglot.parse_one(
        "CREATE TABLE t (id INT, a VARCHAR(20), g GEOMETRY, PRIMARY KEY (id), "
        "UNIQUE KEY u (id), FULLTEXT KEY f (a), SPATIAL KEY s (g))", dialect="mysql")
    got = [type(i).__name__ for i in node.this.expressions[3:]]
    kinds = [str(i.args.get("kind") or "") for i in node.this.expressions[5:]]
    assert got == ["PrimaryKey", "UniqueColumnConstraint",
                   "IndexColumnConstraint", "IndexColumnConstraint"], (
        "sqlglot %s 的建表 AST 契约已变化：%s" % (sqlglot.__version__, got))
    assert kinds == ["FULLTEXT", "SPATIAL"], (
        "sqlglot %s 的索引 kind 契约已变化：%s" % (sqlglot.__version__, kinds))


def _check_ruleset(case):
    ex = case.extra
    raw = io.open("tests/fixtures/" + ex["fixture"], encoding="utf-8").read()
    got = set(_rid(raw, ex["instance_type"]))
    assert got == ex["rules"], "规则集合必须精确相等：多出=%s 少了=%s" % (
        sorted(got - ex["rules"]), sorted(ex["rules"] - got))


def _check_spans(case):
    plan, spans = _plan_and_spans(case.sql)
    n = len(spans) if plan else 0
    assert n == case.extra["spans"], "span 数应为 %d，实得 %d" % (case.extra["spans"], n)
    if plan:
        assert _out_of_span_chars(case.sql, spans) == 0, "存在越界改写字符"


def _check_sql_case(case):
    ex = case.extra
    plan, spans = _plan_and_spans(case.sql)
    has_plan = plan is not None
    parsed = _sp.parse(case.sql)
    ast = type(parsed.ast).__name__
    e999 = bool(parsed.parse_error)

    if case.klass in ("pos", "characterization"):
        assert ast == "Create" and not e999, "应恢复为 Create 且无 E999，实得 %s/E999=%s" % (ast, e999)
        if case.klass == "pos" and ex.get("needs_recovery", True):
            assert has_plan, "规划器必须先接受该语句"
        if "spans" in ex:
            if ex["spans"]:
                assert len(spans) >= 1, "应产生掩码 span"
            else:
                assert len(spans) == 0, "不应产生掩码 span"
        if ex.get("raw_verbatim"):
            assert parsed.raw_sql == case.sql.strip(), "raw_sql 必须逐字符等于输入"
        if ex.get("columns") is not None:
            assert [c.get("name") for c in (parsed.columns or [])] == ex["columns"]
        if ex.get("column_comment"):
            col, txt = ex["column_comment"]
            assert parsed.column_comments.get(col) == txt
        if ex.get("index_type"):
            assert ex["index_type"] in [i.get("type") for i in (parsed.indexes or [])]
        inst = ex.get("instance_type", "distributed")
        if ex.get("rule_hit"):
            assert ex["rule_hit"] in _rid(case.sql, inst)
        if ex.get("rule_miss"):
            assert ex["rule_miss"] not in _rid(case.sql, inst)
        if ex.get("equal_ruleset_without_comment"):
            bare = case.sql.replace(" COMMENT '唯一索引说明'", "").replace(" COMMENT 'z'", "")
            assert _rid(case.sql, inst) == _rid(bare, inst), (
                "恢复不得引入自己的口径：规则集合必须等于去掉索引 COMMENT 的同表")
        return

    if case.klass == "neg":
        assert not has_plan, "token 规划器必须先行拒绝，不能只依赖候选 parser 或 AST 门禁"
        assert ast != "Create", "非法形态不得被恢复成 Create"
        if ex.get("e999"):
            assert e999, "必须保留 E999"
        if ex.get("ast"):
            assert ast == ex["ast"]
        return

    if case.klass in ("pos_known", "unsupported_unproven"):
        assert ast != "Create", "必须失败关闭（既不冒充合法，也不冒充非法）"
        if ex.get("ast"):
            assert ast == ex["ast"]
        return

    raise AssertionError("未知 klass: %s" % case.klass)


@pytest.mark.parametrize("case", CASES, ids=[c.cid for c in CASES])
def test_manifest_case(case):
    if case.klass == "contract":
        _check_contract()
    elif case.klass == "ruleset":
        _check_ruleset(case)
    elif case.klass == "spans":
        _check_spans(case)
    else:
        _check_sql_case(case)


@pytest.mark.parametrize("suite", MUTATIONS, ids=[s["cid"] for s in MUTATIONS])
def test_gate_is_conservative(suite):
    """候选 AST 结构守恒门禁的反向鉴别：不得误杀，也不得漏放。"""
    plan = PL._plan_recovery(suite["src"], "mysql")
    assert plan is not None, "变异套件的源语句必须能生成恢复计划"
    good = sqlglot.parse_one(suite["good"], dialect="mysql")
    assert PL._validate_recovery_candidate(good, plan) is True, (
        "%s：正确候选被门禁误杀" % suite["title"])
    for label, msql in suite["muts"]:
        try:
            cand = sqlglot.parse_one(msql, dialect="mysql")
        except Exception:
            continue                                   # 解析不出来 → 不可能成为候选
        assert PL._validate_recovery_candidate(cand, plan) is False, (
            "%s / %s：变异候选被门禁放行" % (suite["title"], label))


def test_blank_spans_invariants_under_fuzz():
    """模糊测试：不抛异常；凡产生计划者必满足长度恒等 + 差异全落在 span 内。"""
    random.seed(FUZZ["seed"])
    atoms = ["'", "''", "\\'", "\\\\", "`", "``", '"', "(", ")", ",", ";", "--x\n",
             "/*y*/", "\n", " ", "#z\n", "UNIQUE", "KEY", "INDEX", "COMMENT", "CREATE",
             "TABLE", "TEMPORARY", "PRIMARY", "CONSTRAINT", "a", "uk", "20",
             "UNIQUE KEY `u` (`a`) COMMENT 'x'", "varchar(20)", "ENGINE=InnoDB",
             "NOT NULL", "TDSQL_DISTRIBUTED BY HASH(a)"]
    violations = []
    for _ in range(FUZZ["n"]):
        body = "".join(random.choice(atoms) for _ in range(random.randint(3, 45)))
        sql = random.choice(["CREATE TABLE `t` (" + body,
                             "CREATE TEMPORARY TABLE `t` (" + body + ")", body])
        plan, spans = _plan_and_spans(sql)           # 抛异常即测试失败
        if plan is not None and _out_of_span_chars(sql, spans) != 0:
            violations.append(sql)
    assert not violations, "模糊测试发现 %d 条越界改写" % len(violations)
```

### `tests/manifest_doc.py`

从 manifest 生成 §7.1 全部表格与计数

```python
# -*- coding: utf-8 -*-
"""从 manifest 生成设计说明书的用例表与计数（第十一轮 BLOCK-11-07 第 2 条）。

任何章节都不得人工维护第二份计数；本脚本的输出即正文。
"""
import sys, os, collections
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.parser_recovery_manifest import CASES, MUTATIONS, FUZZ

KLASS_ORDER = ["pos", "neg", "pos_known", "unsupported_unproven",
               "characterization", "ruleset", "spans", "contract"]
GROUP_TITLE = {
    "A": "DEF-1 索引类型判据 + AST 契约", "B": "DEF-2 正向恢复",
    "C": "DEF-2 产品边界（sqlglot 能力边界）", "D": "负向 / 防次生灾害",
    "E": "失败关闭", "F": "生产回放（精确规则集合）", "T": "TDSQL 方言组合",
    "N": "作用域负向", "X": "方言尾子句安全交叉矩阵", "Y": "方言语法严格性与语句边界",
    "Z": "方法参数与表名精确形态", "W": "目标上下文完整性",
    "H1": "key_part 非法", "H2": "key_part 官方合法", "H2b": "key_part 含 ASC/DESC",
    "H3": "分区子句非法", "H4": "官方二级分区 Range/List", "H4c": "官方合法但 sqlglot 不支持",
    "H4b": "官方未列的分区方法", "H5": "表选项值非法", "H6": "表选项官方合法",
    "H6b": "表选项无证据", "P1": "PRIMARY COMMENT 官方合法", "P2": "PRIMARY COMMENT 非法近邻",
    "R11-01": "可执行注释（BLOCK-11-01）", "R11-02": "表尾迁移图（BLOCK-11-02）",
    "R11-03": "广播哨兵分型（BLOCK-11-03）", "R11-06": "列属性（BLOCK-11-06）",
    "R11-M1": "FULLTEXT/SPATIAL 入口（MAJOR-11-01）",
    "TY-P": "官方类型：必须恢复", "TY-D": "官方类型：DEFAULT/ON UPDATE 精度",
    "TY-K": "官方类型：sqlglot 不支持（KFN-3）", "TY-N": "类型越界/非法：必须失败关闭",
}


def compose(gs):
    c = collections.Counter(x.klass for x in CASES if x.group in gs)
    return "×".join([]) or "  ".join("%s×%d" % (k, c[k]) for k in KLASS_ORDER if c[k])


def table(groups, title):
    out = ["| 子组 | 例数 | 说明 | 分类构成 |", "|---|---:|---|---|"]
    tot = 0
    for g in groups:
        n = sum(1 for x in CASES if x.group == g)
        tot += n
        out.append("| **%s** | %d | %s | %s |" % (g, n, GROUP_TITLE.get(g, ""), compose([g])))
    out.append("| **合计** | **%d** | —— | %s |" % (tot, compose(groups)))
    return "**%s**\n\n" % title + "\n".join(out)


def main():
    order = [x.group for x in CASES]
    seen, groups = set(), []
    for g in order:
        if g not in seen:
            seen.add(g); groups.append(g)
    main_g = [g for g in groups if not (g.startswith("H") or g.startswith("P")
                                        or g.startswith("R11") or g.startswith("TY"))]
    print("<!-- 本节由 tests/manifest_doc.py 从 tests/parser_recovery_manifest.py 生成，请勿手改 -->\n")
    print(table(main_g, "§7.1 主用例表"), "\n")
    print(table([g for g in groups if g.startswith("H")], "§7.1a H 组"), "\n")
    print(table([g for g in groups if g.startswith("P")], "§7.1b P 组（DEF-3）"), "\n")
    print(table([g for g in groups if g.startswith("R11")], "§7.1c R11 组（第十一轮复审反例）"), "\n")
    print(table([g for g in groups if g.startswith("TY")], "§7.1d TY 组（官方数据类型双向闭合矩阵）"), "\n")
    kc = collections.Counter(x.klass for x in CASES)
    pc = collections.Counter(x.prov for x in CASES)
    print("**全局计数（唯一真源）**\n")
    print("| 项 | 值 |")
    print("|---|---:|")
    print("| manifest 用例总数 | **%d** |" % len(CASES))
    for k in KLASS_ORDER:
        if kc[k]:
            print("| 其中 `%s` | %d |" % (k, kc[k]))
    print("| 变异门禁断言（%d 套） | **%d** |" % (
        len(MUTATIONS), sum(1 + len(s["muts"]) for s in MUTATIONS)))
    print("| 模糊测试（seed=%d） | **%d** |" % (FUZZ["seed"], FUZZ["n"]))
    print()
    print("**证据来源分布**\n")
    print("| provenance | 例数 |")
    print("|---|---:|")
    for p, n in sorted(pc.items(), key=lambda kv: -kv[1]):
        print("| `%s` | %d |" % (p, n))
    print()
    kfn = [x for x in CASES if x.klass in ("pos_known", "unsupported_unproven")]
    print("**已知假阴性 / 未证实能力登记（由 manifest 生成）**\n")
    print("| 类别 | cid | 形态 | 理由 |")
    print("|---|---|---|---|")
    for x in kfn:
        print("| %s | %s | `%s` | %s |" % (
            "KFN-A（官方合法、暂不支持）" if x.klass == "pos_known" else "KFN-B（未证实能力）",
            x.cid, x.label.replace("|", "\\|"), x.note))


if __name__ == "__main__":
    main()
```

### `tests/codestat.py`

从最终补丁生成 §3.4 规模表、函数清单与唯一性检查

```python
# -*- coding: utf-8 -*-
"""从最终补丁自动生成 diff stat、函数清单与唯一性检查（第十一轮 MINOR-11-02）。

用法：python codestat.py <基线文件> <目标文件>
正文的 §3.4 规模数字必须由本脚本输出，不得人工维护。
"""
import ast, io, sys, difflib, collections

REL = "backend/engine/parser/parser_legacy.py"


def top_defs(src):
    """模块级 def 名 -> (起始行, 行数)；同时收集模块级赋值名。"""
    tree = ast.parse(src)
    fns, consts = collections.OrderedDict(), collections.OrderedDict()
    dup_fn, dup_const = [], []
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if n.name in fns:
                dup_fn.append(n.name)
            fns[n.name] = (n.lineno, (n.end_lineno or n.lineno) - n.lineno + 1)
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    if t.id in consts:
                        dup_const.append(t.id)
                    consts[t.id] = n.lineno
        elif isinstance(n, ast.ClassDef):
            fns["class " + n.name] = (n.lineno, (n.end_lineno or n.lineno) - n.lineno + 1)
    return fns, consts, dup_fn, dup_const


def main():
    base_p, new_p = sys.argv[1], sys.argv[2]
    base = io.open(base_p, encoding="utf-8").read()
    new = io.open(new_p, encoding="utf-8").read()
    bl, nl = base.splitlines(), new.splitlines()
    add = dele = 0
    for line in difflib.unified_diff(bl, nl, n=0, lineterm=""):
        if line.startswith("+") and not line.startswith("+++"):
            add += 1
        elif line.startswith("-") and not line.startswith("---"):
            dele += 1
    bf, bc, _, _ = top_defs(base)
    nf, nc, dupf, dupc = top_defs(new)

    print("<!-- 本节由 tests/codestat.py 生成，请勿手改 -->\n")
    print("**`%s` 规模（自动生成）**\n" % REL)
    print("| 项 | 基线 | Rev.M | 变化 |")
    print("|---|---:|---:|---:|")
    print("| 文件行数 | %d | %d | %+d |" % (len(bl), len(nl), len(nl) - len(bl)))
    print("| 模块级函数/类 | %d | %d | %+d |" % (len(bf), len(nf), len(nf) - len(bf)))
    print("| 模块级常量 | %d | %d | %+d |" % (len(bc), len(nc), len(nc) - len(bc)))
    print("| diff 行 | —— | —— | +%d / -%d |" % (add, dele))
    print()
    added = [k for k in nf if k not in bf]
    removed = [k for k in bf if k not in nf]
    changed = [k for k in nf if k in bf and nf[k][1] != bf[k][1]]
    print("**新增函数（%d 个）**\n" % len(added))
    print("| 函数 | 起始行 | 行数 |")
    print("|---|---:|---:|")
    for k in added:
        print("| `%s` | %d | %d |" % (k, nf[k][0], nf[k][1]))
    print()
    print("**删除函数（%d 个）**：%s\n" % (len(removed), ", ".join("`%s`" % k for k in removed) or "无"))
    print("**行数发生变化的既有函数（%d 个）**\n" % len(changed))
    if changed:
        print("| 函数 | 基线行数 | Rev.M 行数 |")
        print("|---|---:|---:|")
        for k in changed:
            print("| `%s` | %d | %d |" % (k, bf[k][1], nf[k][1]))
    print()
    print("**唯一性检查**\n")
    print("| 检查 | 结果 |")
    print("|---|---|")
    print("| 模块级函数重复定义 | %s |" % ("❌ " + ", ".join(dupf) if dupf else "✅ 无"))
    print("| 模块级常量重复定义 | %s |" % ("❌ " + ", ".join(dupc) if dupc else "✅ 无"))
    print("| 语法可解析 | ✅ |")
    return 1 if (dupf or dupc) else 0


if __name__ == "__main__":
    sys.exit(main())
```

## 附录 B：给智能体 Q 的三十六句话

1. **本次不是"把正则改好"，是"把正则换掉"。** Rev.A 的 `_UNIQUE_IDX_COMMENT_RE` 必须**整体删除**，
   不要保留任何跨语义边界的正则改写（NG-0）。
2. **`at_def_start` 那个状态是这一版的核心，不能省。** 少了它，`CONSTRAINT x UNIQUE (...)`、
   列内联 `UNIQUE`、定义项中部的 `UNIQUE` 都会被错误地当成目标——Rev.B 就是这么被打回来的。
   **span 门禁只能自证「改动落在自己声明的范围内」，证明不了「这个范围是对的」。**
3. **方言恢复必须串联，但必须走新的 token 剥离器。**
   🚫 **绝对不要恢复 `_TDSQL_DIALECT_RE`，也不要另写任何全局正则**——那条正则正在生产环境
   静默删列、篡改注释（§5.14.1）。串联的是 `_plan_recovery()`，并把它的 span
   并入联合门禁。
4. **§3.2 那个 `ast` 重绑的坑我真踩过。** 只赋 `parsed.ast` 会 `UnboundLocalError`，
   且要跑到含 UNIQUE-COMMENT 的语句才炸。
5. **F 组要原样读 fixture、用精确相等断言。** 不要过滤注释行（fixture 已是纯 DDL），
   不要再加文件头（我上一版加的文件头就让 gg78 多出一条 R104），
   6309 走**分布式**、6311 走**集中式**。
6. **X 组 40 例是本轮的重中之重。** 其中 **36 例在当前生产版本上就是失败的**——
   它们直接验证列名、列注释、DEFAULT、`raw_sql` 有没有被静默改坏。
   **不要**用『与去掉 UNIQUE COMMENT 的结果相等』代替字段级断言：两边都会经过同一条
   不安全预处理，是同源错误对照，我上一版就栽在这里。
7. **T 组那条『与去掉 COMMENT 结论相等』仍可保留，但只能作辅助 oracle，不能当主断言。**

8. **两个扫描循环里都不许有"看不懂就 `i += 1` 跳过"。** 这是 Rev.G 的红线（S-2c）。
   表选项区的每个 token 必须被 `_consume_table_option()` 按**整个选项**认领并前进；
   索引选项区只接受 `USING BTREE` 与 `COMMENT STRING`。
   **凡有一个 token 认领不了，整个函数 `return None, [], ""` / 放弃剥离。**
   宁可不修（保持原结论），也绝不在没看懂上下文的情况下动刀——
   前五轮被打回，根子都在"目标 token 序列对了就动手"。
9. **W 组的期望值必须逐路径量取主干，不能写"一律 E999"。**
   同一批输入在 v1.6.2.1 上有三种结局：`Command`（无语法错，sqlglot 不认方言）、
   `Create`（sqlglot 自己就能解析）、E999。**先跑主干记录，再拿它当期望**——
   我上一版就是凭印象写"一律 E999"，自己把自己的复评带偏了 7 例。

10. **全部消费器是一套东西，契约必须一致：`f(toks, i, stop) -> (下一个下标, 结果) | (-1, None)`。**
    `_consume_data_type()` / `_consume_column_constraints()` / `_consume_index_definition()` /
    `_consume_index_key_parts()` / `_consume_table_option()` / `_consume_secondary_partition()`
    各管一段，`_plan_recovery()` 只负责
    **组合它们 + 记录目标 span**，不要在外层再写局部语法判断——那正是前七轮反复出问题的地方。
    ⚠️ 函数清单以 §3.4 由 `codestat.py` 生成的表为准，本条只讲契约。
11. **`ROW_FORMAT` 的枚举要按文本匹配，不能按 token 类型。**
    实测 `DEFAULT`→`TokenType.DEFAULT`、`FIXED`→**`TokenType.DECIMAL`**、其余→`VAR`。
    按类型写会把这两个**合法**取值误拒。用 `_is_bare_kw()` 排除引号形态即可。
12. **`PARTITION BY` 必须消费到语句结束，不能 `break`、也不要一律拒绝。**
    一律拒绝会让 D5（`RANGE (YEAR(dt)) (PARTITION ... VALUES LESS THAN ...)`）
    从主干的 `Create`/`cols=3` 降为 `Command` —— 那是真实的覆盖面损失，我实测过。
13. **反例期望值一律先在主干上跑一遍记下来，再用 rank 判据比对，不要手写。**
    `rank(NoneType/E999)=0 < Command=1 < Create=2`，反例只要求 `rank(候选) ≤ rank(主干)`
    且 E999 不消失。**主干在"无 UNIQUE COMMENT"路径上的 `Create` 有 14 例是旧正则的假成功**，
    按"必须与主干相同"去写，一定会把预期收紧误判成回归——第六、七两轮我都栽在这里。

14. **判据是 TDSQL 官方语法，不是 MySQL，更不是 sqlglot。** 这是第八轮的总纲。
    遇到"这个语法合不合法"的问题，按 ①目标实例真实 DDL ②TDSQL 官方文档 ③项目冻结规则
    ④MySQL ⑤sqlglot 的顺序找依据。**"sqlglot 能解析"≠TDSQL 合法（`USING HASH` 就是），
    "sqlglot 解析失败"≠TDSQL 非法（`ASC/DESC` 就是）。** 我两头都犯过。
15. **先读项目自己的代码再去查外网。** 多列 `shardkey=(a,b)` 的依据一直写在
    `backend/services/tdsql_connector.py` 的注释里，我前七轮一次都没查。
16. **`_scan_table_tail()` 无论走哪条恢复路径都要调用，且它没有开关参数。**
    Rev.I 那个 `want_dialect=False` 开关的注释与实现自相矛盾，Rev.J 已删除。
    少调用它就退回 BLOCK-H1 的老路：`ENGINE=123`、孤立 `DEFAULT` 又会被静默放行。
17. **`TDSQL_DISTRIBUTED BY HASH(col)` 是单列，`shardkey=(a,b)` 才是多列。**
    两处形态不同，**不要共用消费器**——我为了支持后者把前者也放宽了，Z 组当场抓出来。
18. **分区子句不要求消费到语句结束。** 官方有 `PARTITION BY ... TDSQL_DISTRIBUTED BY ...`
    这种分区在前的顺序；强制到 EOF 会把官方形态判成非法。尾部完整性由 `_scan_table_tail()` 统一负责。
19. **第三类 span（官方语法掩码）和前两类是同一套机制。** `ASC/DESC`、分区定义的
    `ENGINE=`/`COMMENT=` 都只是等长置空，走同一个 `_spans_only_diff()` 门禁。
    不要为它们另写机制，也不要改成"替换成别的内容"——那会变成伪造原文。
20. **`_validate_recovery_candidate()` 是最后一道，但不能当第一道。**
    它证明"候选 AST 没丢结构"，证明不了"这个语法 TDSQL 允许"（`USING HASH` 能过它）。
    token 级 TDSQL 白名单和 AST 结构门禁**两层都要有**，缺一不可。

21. **类型参数的"正整数"谓词不能到处复用。** 索引前缀长度必须 > 0，但
    `DECIMAL(M,0)` 的 scale、`DATETIME(0)` 的 fsp **都允许 0**。我把这两处
    共用了一个谓词，误拒了官方合法语法（第十轮 BLOCK-J1）。
22. **按 kind 分支时，每一支的处置必须由该支的实测能力决定。**
    索引 COMMENT：`UNIQUE`/`PRIMARY` 是 sqlglot ParseError，普通 `KEY`/`INDEX`/
    `FULLTEXT` 却能正常解析。我一度三者统一失败关闭，**生产 fixture gg78 立刻回归**。
23. **两份生产 fixture 的"规则集合精确相等"断言不许删、不许放宽。**
    它是第十轮唯一抓住上面那个回归的东西。子集断言证明不了"零新增"。
24. **表尾迁移表里没有 provenance 的边就是不存在的边。**
    不要因为"看起来合理"就加一条；OFFICIAL / TARGET_INSTANCE / CORPUS /
    PROJECT_ACCEPTED / ADJ-6 各是各的依据，混用等于没有依据。
25. **数量只有一个真源：参数化清单 + `pytest --collect-only -q`。**
    不要在正文、门槛、checklist 三处各写一遍——第十轮 MAJOR-J1 就是这么来的。

26. **DEF-3 和 DEF-2 是同一件事，只是索引 kind 不同。** `PRIMARY KEY … COMMENT`
    与 `UNIQUE KEY … COMMENT` 在 sqlglot 30.x 上都是 ParseError，掩码后都能解析。
    **不要为它另写一套机制**——只是在索引 COMMENT 分流处多认一个 kind。
27. **但普通 `KEY`/`INDEX`/`FULLTEXT` 的 COMMENT 绝不能一起掩码。** 它们 sqlglot
    本来就能解析，掩码等于无谓改写原文；生产 fixture gg78 就是这一支。
28. **扩大恢复范围时，必须同时补"非法近邻"用例。** P2 那 6 例（PRIMARY 后带名、
    空键列、重复 COMMENT、`USING HASH`、前后置 USING）就是 DEF-3 的边界证明；
    只加正例不加反例，等于把范围放开了却没有证明边界还在。

29. **注释不等于不可见。** MySQL 的 `/*!50100 …*/` 是**可执行注释**：对 MySQL 是真语句，
    对 sqlglot 却落在 `token.comments` 里。`mysqldump` 导出的二级分区正是这个形态。
    "扫 token 就够了"这个前提在这里是错的——必须显式收集并**重新词法化**验证。
30. **状态机不等于计数器。** 四状态 FSM 只表达"当前阶段"，不保留历史，
    于是 `DIST → PARTITION → DIST` 这种**双一级分布**会被放行。
    要么显式计数，要么像 Rev.M 这样改成"整条序列必须完整匹配一个 profile"。
31. **哨兵值不能和普通值共用 atom。** `shardkey=noshardkey_allset` 是广播表哨兵，
    `shardkey=id` 是普通分片键。归一成同一个 atom，`shardkey=(noshardkey_allset,id)`
    就会混过去，R054/R077 的边界随之可被伪造。
32. **别名规范化必须发生在源侧，而且两侧共用同一个函数。** sqlglot 会把
    `INTEGER→INT`、`NUMERIC→DECIMAL`、`REAL→FLOAT`、`DOUBLE PRECISION→DOUBLE` 规范化，
    还会丢掉 `ZEROFILL`。源侧按字面记、候选侧按 AST 记，两边永远不可能相等。
33. **"门禁通过"必须是端到端结论，不是规划层结论。** BLOCK-11-06 就是这么来的：
    我在规划层看到 plan=ACCEPT 就写了"已恢复"，实际上掩码没做、候选仍 ParseError。
    **任何"已恢复"的断言都必须断到最终 `Create` + 无 E999。**
34. **同一个语法在 AST 里可能有多个落点。** `USING BTREE` 依索引种类与位置分别落在
    `index_type` / `options[].using` / `include.using` 三处；只读一处会误杀正确候选。
    写门禁前先把该字段的**所有**表现枚举一遍。
35. **入口判据和消费器判据必须同源。** `_is_index_item()` 只认 `FULLTEXT KEY`、
    `_consume_index_definition()` 却也认裸 `FULLTEXT`——结果官方合法的 `FULLTEXT (a)`
    进了列定义消费器，形成死分支。两处判据抽成同一个函数就不会漂移。
36. **计数、表格、规模数字一律由脚本生成。** 附录 C 的四个文件就是为此存在的：
    manifest 是唯一真源，`manifest_doc.py` 生成 §7.1，`codestat.py` 生成 §3.4。
    **正文与脚本输出不一致时以脚本为准**，然后重跑生成器更新正文——不要反过来改脚本。
