# DESIGN-v1.6.2.2 索引类型误判与唯一索引注释解析崩溃 修复详细设计说明书

| 项目 | 内容 |
|---|---|
| 文档版本 | **Rev.C**（Rev.A、Rev.B 经智能体 O 两轮独立复审均判 No-Go；本版按第二轮 BLOCK-B1/B2、MAJOR-B1/B2 整改） |
| 目标版本 | **v1.6.2.2** |
| 缺陷来源 | 内网人工扫描报告 #6309（gg77）、#6311（gg78） |
| 缺陷编号 | **DEF-1 = DEF-R054-FAKEUNIQUE**；**DEF-2 = DEF-PARSE-UKCOMMENT** |
| 撰写 | 智能体 A |
| 施工 | 智能体 Q |
| 基线 commit | `48d2396`（main） |
| 评审依据 | `docs/REVIEW-v1.6.2.2-...独立复审报告-Codex.md` |
| 改动范围 | **`parser_legacy.py` 4 个改动点**（+1 处 import）；**另修正 2 个 fixture**（删除会污染审核的文件头） |
| 实测结论 | 生产 14 表**零漂移**；全语料 197 条中**恰好 2 条**变化且都是目标缺陷；全量回归 **1355 passed / 0 failed / 29 skipped**；TDSQL 四种方言组合**全部恢复**；5 类作用域负例 span **全部为 1**；模糊 6000 条**零违例**；生产回放**精确集合相等** |

---

## Rev.C 修订说明（针对 O 第二轮独立复审）

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
| **A-词法（Rev.B 采纳）** | **基于 sqlglot 词法器**的受限剥离 + 严格接纳门禁后重试 | 恢复**完整** AST；伪 SQL 结构上不可见；失败关闭 |
| B | 在 `except` 补调 `_regex_fallback_create_table_props()` | 只救回 4 个字段，columns/indexes 仍空；且该函数不感知字符串字面量，`COMMENT '……PRIMARY KEY……'` 会造成 R003 **漏报** |
| C | 升级/更换 sqlglot | 影响面不可控，不在本次范围 |

**B 不做，登记 ADJ-10**（O 复审同意此取舍）。

### 2.3 安全性论证（按 O 意见重写）

Rev.A 用"只在已经抛错的语句上生效，故对能解析的一切语句零影响"一条来承载整个爆炸半径论证。
**这条陈述本身为真，但它对"变换本身是否安全"什么都没说**——而 BLOCK-1 恰恰发生在变换里。
Rev.B 把安全性拆成四条**各自独立可验证**的性质：

| 编号 | 性质 | 由什么保证 | 实测证据 |
|---|---|---|---|
| **S-1** | 不改变"首次解析即成功"语句的控制流与结果 | 新逻辑只在 `except` 内 | 全语料 197 条中仅 2 条变化，且均为本次目标缺陷 |
| **S-2a 词法完整性** | 差异只落在词法器给出的 token 区间内 | 字符串/标识符/注释各是完整 token；改写等长 + 逐字符校验（门禁①） | O 的 BLOCK-1 反例：变换 **1 处**（Rev.A 是 2 处），越界改写 **0** |
| **S-2b 语法作用域完整性**（Rev.C 新增） | **每个 span 必须来自第一条 CREATE TABLE 顶层、且以 UNIQUE 开头的定义项** | 显式 `at_def_start` 状态；定义列表闭合即停止扫描 | 5 类作用域负例（CONSTRAINT / 列内联 / 定义项中部 / 两条语句 / 表选项）span 数**全部为 1** |
| **S-3** | 无法证明安全时**失败关闭**，绝不猜测性改写 | 词法失败 / 括号未闭合 / 非 CREATE TABLE / 无 span → 返回 `None`，沿用原异常 | 未闭合引号、未闭合括号、非 CREATE TABLE 均实测不变换 |
| **S-4** | `parsed.raw_sql` 保持原文 | 变换只作用于送进 sqlglot 的副本 | 12 例正向恢复全部 `raw_sql == 输入` |

> **S-2a 是 Rev.A 完全缺失的一条；S-2b 是 Rev.B 只写进文档、未在代码中实现的一条。**
> O 第二轮指出：span 门禁只能证明「改动落在自己声明的 span 内」，
> **不能**证明「这个 span 语义上就是目标语法」——两层必须同时成立，门禁才是有效的安全证明。

---

## 3. 详细设计（照图施工）

### 3.0 改动点 0：新增一处 import

在 `from sqlglot.errors import SqlglotError` 之后增加一行：

```python
from sqlglot.tokens import TokenType
```

### 3.1 改动点 1：新增词法安全、作用域受限的剥离器（模块级）

**位置**：`backend/engine/parser/parser_legacy.py`，紧接 `_TDSQL_DIALECT_RE` 定义之后，
与下方 `@dataclass class ParsedSQL` 之间保留两个空行。

```python
# ── v1.6.2.2 / DEF-2：唯一索引 COMMENT 剥离（基于 sqlglot 词法器，非正则）──────
#
# sqlglot(30.x) 的 mysql 方言不支持 UNIQUE 索引上的 COMMENT 子句：
#   UNIQUE KEY `uk` (`a`) COMMENT '说明'   ← 抛 ParseError
#   KEY        `k`  (`a`) COMMENT '说明'   ← 正常解析（普通索引不受影响）
# 整条 CREATE TABLE 抛错后 columns/engine/charset/主键/表注释全空，
# R003/R004/R005/R028 集体误报。
#
# 为什么用词法器而不是正则：字符串字面量、反引号标识符、行/块注释在 token 流中
# 各自是完整单元，因此列注释里出现的伪 SQL（例如
#   b VARCHAR(255) COMMENT 'see UNIQUE KEY fake (a) COMMENT ''x'''
# ）在结构上不可见，不可能被误改。全局正则做不到这一点。


def _strip_unique_index_comments(sql: str, dialect: str = "mysql"):
    """剥离**第一条** CREATE TABLE 顶层定义项上 UNIQUE 索引的 COMMENT 子句。

    返回 (改写后SQL, 已抹除的span列表, 从原文提取的表名)。
    任一环节无法证明安全时返回 (None, [], "") —— 失败关闭，绝不猜测性改写。

    安全性质分两层（缺一不可）：
      * 词法完整性：差异只落在词法器给出的 token 区间内；
      * **语法作用域完整性**：每个 span 必须来自第一条 CREATE TABLE 定义列表的
        顶层、且**以 UNIQUE 开头**的定义项。为此显式维护 at_def_start 状态——
        只有"定义列表左括号之后"或"深度 1 的逗号之后"的第一个真实 token 才算
        定义项起点。`CONSTRAINT x UNIQUE (...)`（起点是 CONSTRAINT）、
        定义项中部的 UNIQUE、以及第一个定义列表闭合之后的一切内容，均不进入。
    """
    try:
        tokenizer = sqlglot.Dialect.get_or_raise(dialect).tokenizer_class()
        toks = tokenizer.tokenize(sql)
    except Exception:
        return None, [], ""
    n = len(toks)
    if n < 4:
        return None, [], ""

    # ── 入口：CREATE [TEMPORARY] TABLE ──（TEMPORARY 属既有产品域，见 R024/R032）
    if toks[0].token_type != TokenType.CREATE:
        return None, [], ""
    p = 1
    if toks[p].token_type == TokenType.TEMPORARY:
        p += 1
    if p >= n or toks[p].token_type != TokenType.TABLE:
        return None, [], ""
    p += 1

    # ── 表名 + 定义列表左括号定位 ──
    _NAMEY = (TokenType.VAR, TokenType.IDENTIFIER, TokenType.STRING)
    table_name = ""
    while p < n and toks[p].token_type != TokenType.L_PAREN:
        if toks[p].token_type in _NAMEY:
            table_name = toks[p].text
        p += 1
    if not table_name or p >= n:
        return None, [], ""

    spans = []
    depth = 1                      # 已越过定义列表左括号
    at_def_start = True            # 左括号之后即为第一个定义项的起点
    i = p + 1
    while i < n:
        tt = toks[i].token_type
        if tt == TokenType.L_PAREN:
            depth += 1
            at_def_start = False
            i += 1
            continue
        if tt == TokenType.R_PAREN:
            depth -= 1
            if depth == 0:
                break              # 第一个定义列表闭合 → 立即停止，不扫后续内容
            at_def_start = False
            i += 1
            continue
        if depth == 1 and tt == TokenType.COMMA:
            at_def_start = True    # 顶层逗号之后是下一个定义项的起点
            i += 1
            continue
        # 仅当"顶层定义项起点恰为 UNIQUE"时才进入
        if depth == 1 and at_def_start and tt == TokenType.UNIQUE:
            j = i + 1
            if j < n and toks[j].token_type in (TokenType.KEY, TokenType.INDEX):
                j += 1
            if j < n and toks[j].token_type in (TokenType.VAR, TokenType.IDENTIFIER):
                j += 1
            if j < n and toks[j].token_type == TokenType.L_PAREN:
                d2 = 0
                closed = False
                while j < n:
                    if toks[j].token_type == TokenType.L_PAREN:
                        d2 += 1
                    elif toks[j].token_type == TokenType.R_PAREN:
                        d2 -= 1
                        if d2 == 0:
                            j += 1
                            closed = True
                            break
                    j += 1
                if not closed:
                    return None, [], ""          # 括号未闭合 → 失败关闭
                # 索引选项区：扫到本定义项结束（顶层逗号 或 定义列表收尾右括号）
                while j < n:
                    tj = toks[j].token_type
                    if tj in (TokenType.COMMA, TokenType.R_PAREN):
                        break
                    if tj == TokenType.L_PAREN:
                        d3 = 0
                        while j < n:
                            if toks[j].token_type == TokenType.L_PAREN:
                                d3 += 1
                            elif toks[j].token_type == TokenType.R_PAREN:
                                d3 -= 1
                                if d3 == 0:
                                    j += 1
                                    break
                            j += 1
                        continue
                    if (tj == TokenType.COMMENT and j + 1 < n
                            and toks[j + 1].token_type == TokenType.STRING):
                        spans.append((toks[j].start, toks[j + 1].end))
                        j += 2
                        continue
                    j += 1
                i = j
                at_def_start = False
                continue
        at_def_start = False
        i += 1

    if not spans:
        return None, [], ""
    buf = list(sql)
    for s, e in spans:
        if not (0 <= s <= e < len(buf)):
            return None, [], ""
        for q in range(s, e + 1):
            if buf[q] != "\n":
                buf[q] = " "                     # 等长空格，保留换行
    return "".join(buf), spans, table_name
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
| ⑤ 用括号深度取完整键值部分，支持 `a(20)`、多列、嵌套函数 | `d2` 深度配对；实测前缀索引/多列前缀均恢复 |
| ⑥ 只移除该定义项顶层索引选项里的真实 `COMMENT '...'`，保留 `USING` 等 | 选项区扫描到顶层逗号或定义列表收尾右括号；只在 `COMMENT`+`STRING` token 对上记 span |
| ⑦ 支持一个语句内多个 UNIQUE 索引 | 循环 `continue`；实测双 UNIQUE 记 2 处 span |
| ⑧ 无法证明边界时返回 `None`，不猜测性改写 | 词法异常 / 括号未闭合 / 非建表 / 无 span / span 越界 均返回 `(None, [], "")` |
| ⑨ 等长空格替换并保留换行 | 逐字符置空格、跳过 `\n`；实测改写前后**长度恒等** |

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
            # 此处以"词法安全的受限剥离 + 严格接纳门禁"重试一次。
            # 六道门禁全部通过才采用候选 AST；任一不满足即沿用原异常与原有
            # E999 路径（失败关闭），故失败路径与改前逐字等价。
            _retry_ast = None
            _new_sql, _spans, _tbl = _strip_unique_index_comments(sql_clean, self.dialect)
            if _new_sql is not None and _spans:
                # 门禁①：改写必须等长，且差异只允许出现在批准的 span 内
                _ok = len(_new_sql) == len(sql_clean)
                if _ok:
                    for _p in range(len(sql_clean)):
                        if sql_clean[_p] != _new_sql[_p] and not any(
                                _s <= _p <= _e for _s, _e in _spans):
                            _ok = False
                            break
                if _ok:
                    try:
                        _cand = sqlglot.parse_one(_new_sql, read=self.dialect)
                    except Exception:
                        _cand = None
                    # v1.6.2.2 / BLOCK-B1: 与 v1.6.2.0 的 TDSQL 方言恢复串联。
                    # 同一条 DDL 同时含 UNIQUE-COMMENT 与 TDSQL_DISTRIBUTED/BROADCAST
                    # 时，剥离 COMMENT 后仍会降级为 Command；此处复用**同一条**
                    # _TDSQL_DIALECT_RE 与同样的"仅在已降级时"前置条件再恢复一次。
                    # 不修改该正则本身，也不放宽既有 Command 前置门禁。
                    if (isinstance(_cand, exp.Command)
                            and _TDSQL_DIALECT_RE.search(_new_sql)):
                        try:
                            _c2 = sqlglot.parse_one(
                                _TDSQL_DIALECT_RE.sub(" ", _new_sql), read=self.dialect)
                            if not isinstance(_c2, exp.Command):
                                _cand = _c2
                        except Exception:
                            pass
                    # 门禁②：候选必须是 CREATE           门禁③：kind 必须是 TABLE
                    if (isinstance(_cand, exp.Create)
                            and str(_cand.args.get("kind") or "").upper() == "TABLE"):
                        _cand_tbl = ""
                        _sch = _cand.this
                        _tobj = _sch.this if isinstance(_sch, exp.Schema) else _sch
                        if _tobj is not None:
                            _cand_tbl = getattr(_tobj, "name", "") or ""
                        # 门禁④：候选表名必须与原文提取的表名一致
                        if _cand_tbl and _cand_tbl.strip('`"\' ').lower() == _tbl.strip('`"\' ').lower():
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
| **③b（Rev.C 新增，BLOCK-B1）** | **候选若降级为 `exp.Command` 且命中既有 `_TDSQL_DIALECT_RE`，复用 v1.6.2.0 同一规则再恢复一次**；不修改该正则、不放宽其 Command 前置条件 |
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

> ✅ **本文档的代码块已自验证（Rev.C）**：§3.2 与 §3.3 的两个「改动前」块经程序比对与
> `parser_legacy.py` **逐字匹配**；四个「改动后」块被**原样抽取**并施工到一棵干净工作树上，
> 实测语法通过、导入自检通过、行为与我的实现**完全一致**——T 组 10 例、N 组 5 例、
> C 组 4 例、F 组精确集合断言、6000 条模糊测试逐项相同，全量回归
> **1355 passed / 0 failed / 29 skipped**。Q 可以直接复制粘贴，无需再做适配。
>
> ⚠️ 抽取时注意块的先后：§3.2 与 §3.3 都是**前者「改动前」、后者「改动后」**，
> 且 §3.3 两块开头都是 `# 判断索引类型`，容易搞反（我在 Rev.A 自验证时就反过一次）。

### 3.4 改动汇总

| 序号 | 位置 | 改动 |
|---|---|---|
| 0 | 文件头 import 区 | `from sqlglot.tokens import TokenType`（+1 行） |
| 1 | `_TDSQL_DIALECT_RE` 之后 | 新增 `_strip_unique_index_comments()`（约 +140 行，含约 35 行注释） |
| 2 | `parse()` 的 `except` 分支 | 受限重试 + 五道门禁（含 TDSQL 方言恢复串联）；原失败路径整体下移一层缩进 |
| 3 | `_parse_index_constraint()` | 类型判据改读 `kind` 白名单映射 |
| **4** | **`tests/fixtures/report_6309_*.sql`、`report_6311_*.sql`** | **删除会污染审核的文件头注释**（来源说明移入同目录 `README-report-fixtures.md`） |

**产品代码：1 个文件；连同两个 fixture 修正，`git diff --stat` 实测
`3 files changed, 218 insertions(+), 24 deletions(-)`。
不新增第三方依赖（`TokenType` 来自已在用的 sqlglot），规则层一行不动。**

## 4. 明确的非目标（NG，施工红线）

| 编号 | 非目标 | 说明 |
|---|---|---|
| **NG-0** | **不再使用任何跨语义边界的正则做 SQL 改写** | Rev.A 的 `_UNIQUE_IDX_COMMENT_RE` 整体删除，不得以「再补几个分支」的方式保留 |
| **NG-1** | **不改任何规则文件** | `ddl.py` / `index.py` / `distributed.py` / `dml.py` / `oracle_compat.py` **零改动**。本次是解析器供数问题，不是规则判据问题 |
| **NG-2** | **不动 `distributed.py`** | v1.6.1.9 冻结代码；`_iter_unique_indexes` 的早退逻辑本次不碰——DEF-1 修好后它拿到的就是正确输入 |
| **NG-3** | **不动 `_parse_unique_constraint()`** | 它硬编码 `"type": "UNIQUE"`，本就正确 |
| **NG-4** | **不动 v1.6.2.0 的 TDSQL 方言重试** | `_TDSQL_DIALECT_RE` 及其重试块一字不改 |
| **NG-5** | **不动 v1.6.2.1 的 R061 去引号** | `index.py` 一字不改 |
| **NG-6** | **不把 SPATIAL 单独成型** | 维持映射为 NORMAL。这是本次热修「输出域不变」的**兼容性取舍**，**不是**「空间索引语义上等同普通索引」的结论；后续如新增空间索引规则，另行立项扩展模型与消费者（O 复审同意） |
| **NG-7** | **不新增字段级字符集/排序规则检查** | 用户已决策：R005 维持只判表级，字段级字符集本次不纳入 |
| **NG-8** | **不在 `except` 补调 `_regex_fallback_create_table_props()`** | 见 §2.2 方案 B，登记 ADJ-10 |
| **NG-9** | **不修 E999 文案** | 现文案"可能是拉取截断/语法错误"对合法 MySQL 有误导，但属独立体验问题，登记 ADJ-12 |
| **NG-10** | **不支持 `CONSTRAINT x UNIQUE (col)` 形态** | 既有缺陷（ADJ-11）。Rev.C 已用 `at_def_start` **显式排除**该形态——其定义项起点是 `CONSTRAINT` 而非 `UNIQUE`，故不会被纳入 span；这是**设计上的明确排除**，不是偶然不命中。若将来决定支持，必须显式建模并同步删除本条 |

---

## 5. 影响面分析（全部实测）

### 5.0 环境与依赖版本（O MAJOR-2）

| 项目 | 值 |
|---|---|
| 依赖声明 | `requirements.txt: sqlglot>=26.0.0`；`pyproject.toml: sqlglot>=26.0`（**无上限**） |
| 本文档回归实际执行版本 | **sqlglot 30.14.0** |
| O 复审环境实测版本 | sqlglot 30.12.0 |
| DEF-1 AST 结论验证覆盖 | 26.0.0 / 30.12.0 / 30.14.0（O 侧）+ 30.14.0（本侧），**均无反例** |
| 数据库 | MariaDB `127.0.0.1:13306`，`slow_query_log=ON` |
| 后端 | uvicorn `127.0.0.1:8000` 在线 |

> **交付要求**：发布制品必须记录实际安装的 sqlglot 版本；建议后续单独立项锁定上限。
> 本次不改依赖声明（超出缺陷范围），但 §7.1 A 组的 AST 契约测试会在升级破坏假设时**显式失败**。

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
| T7 | 列名为 `broadcast` | — | ✅ 列仍在 | — |
| T8 | 列注释含伪 `TDSQL_DISTRIBUTED` | — | ✅ 注释原样保留 | — |
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

### 5.13 全量回归与审核物料校验器

```
基线   ：1355 passed, 29 skipped, 0 failed
Rev.B  ：1355 passed, 29 skipped, 0 failed        ← 逐项一致

verify_rules.py  基线 ：119 / 107 / 未覆盖 0 / 断言失败 3
verify_rules.py  Rev.B：119 / 107 / 未覆盖 0 / 断言失败 3   ← 逐项一致
```

3 条断言失败两侧同名同因（`01_naming_ddl.sql` 的 `R023_01`/`R098_01`/`R116_01` 期望多写了
`R036,R037`），是**先于本次改动存在的测试资产缺陷**。

> ✅ **零回归。**

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

### 7.1 新增测试（新建 `tests/test_parser_index_type_and_uk_comment.py`）

**A 组 — DEF-1 索引类型判据 + AST 契约（9 例）**

| 编号 | 用例 | 断言 |
|---|---|---|
| A1 | 普通索引，列名 `list_unique_num` | `type == "NORMAL"`；R054 **不命中** |
| A2 | 索引名 `unique_lookup` | `type == "NORMAL"` |
| A3 | 列名 `biz_primary_no` | `type == "NORMAL"` |
| A4 | 列名 `fulltext_body` | `type == "NORMAL"` |
| **A5** | **真 `FULLTEXT KEY`** | `type == "FULLTEXT"`（反向鉴别） |
| **A6** | **真 UNIQUE 不含分片键** | R054 **命中**（反向鉴别） |
| A7 | 真 UNIQUE 含分片键 | R054 不命中 |
| **A8** | **诱饵列名 + 真 UNIQUE 不含分片键** | R054 **命中**（锁定漏报修复，本组最重要） |
| **A9** | **AST 契约（O MAJOR-1）** | 断言 `UNIQUE KEY`→`UniqueColumnConstraint`、`PRIMARY KEY`→`exp.PrimaryKey`、`FULLTEXT/SPATIAL KEY`→`IndexColumnConstraint` 且 `kind` 分别为 `'FULLTEXT'/'SPATIAL'`。**sqlglot 升级破坏该假设时必须显式失败**，并在断言消息中打印实际 `sqlglot.__version__` |

**B 组 — DEF-2 正向恢复（12 例，对应 §5.5）**：1、1b、2、3、4、5、6、6b、7、8、10、11a。
每例均断言 `parse_error` 为空、`len(columns) > 0`、且 **`raw_sql` 逐字符等于输入**。

**C 组 — DEF-2 产品边界（4 例，对应 §5.4）**：函数键值、`VISIBLE`、`KEY_BLOCK_SIZE`、
**`USING BTREE` 前置于键值列表**。断言**仍报原错误**，并在注释中写明该处是 sqlglot 能力边界、
非剥离器缺陷（去掉 COMMENT 后 sqlglot 同样 ParseError）。

**T 组 — TDSQL 方言组合（10 例，对应 §5.9，BLOCK-B1）**：T1 HASH、T2 RANGE、T3 LIST、
T4 BROADCAST、T5 HASH+二级分区、T6 `shardkey=`（对照）、T7 列名为 `broadcast`、
T8 列注释含伪 `TDSQL_DISTRIBUTED`、T9 TEMPORARY（集中式）、T10 TEMPORARY（分布式）。

每例断言：解析成功、`columns > 0`、无 E999、`raw_sql` 逐字等于输入，
**且规则命中集合与「同一张表去掉 UNIQUE 索引 COMMENT」完全相等**
——这条相等断言是最强的护栏，它证明恢复**没有引入任何自己的口径**。
T9/T10 额外断言 R032（集中式）与 R024+R032（分布式）仍正常命中。

**N 组 — 作用域负向（5 例，对应 §5.10，BLOCK-B2）**：N1 `CONSTRAINT ... UNIQUE`、
N2 列内联 `UNIQUE`、N3 定义项中部 `UNIQUE`、N4 两条语句拼接、N5 定义列表闭合后的表选项。
每例都**同时**放一个真实目标，断言 **span 数恰为 1** 且抹除的正是那个真实目标。

**D 组 — 负向 / 防次生灾害（6 例，对应 §5.6）**：伪 SQL 分别置于列 COMMENT、表 COMMENT、
`DEFAULT` 字符串、`--` 行注释、`/* */` 块注释、反引号标识符内。每例断言：

1. 剥离 span 数 == 该语句中**真实**索引注释的个数；
2. **越界改写字符数 == 0**（逐字符校验差异全部落在 span 内）；
3. 改写前后**长度恒等**；
4. 解析成功后各列注释 / 表注释 / DEFAULT 与原文语义一致。

**E 组 — 失败关闭（4 例，对应 §5.7）**：未闭合单引号、未闭合括号、非 CREATE TABLE、
缺右括号建表语句。断言剥离器返回 `None` 或重试失败，且最终仍报原错误。

**F 组 — 生产回放（2 例）**

| 编号 | 用例 | 断言 |
|---|---|---|
| **F1** | `tests/fixtures/report_6309_kcfb_list_info.sql`（**分布式**规则集） | **精确相等**：`rule_ids == {'R011','R018','R019','R036','R037','R061','R065','R067','R104'}`（子集断言证明不了「零新增」，必须用相等） |
| **F2** | `tests/fixtures/report_6311_biz_tx_log.sql`（**集中式**规则集） | **精确相等**：`rule_ids == {'R036','R037'}` |

> ⚠️ **F 组三条硬约束**：
> 1. 必须分别使用报告原上下文的 `instance_type`（6309 **分布式** / 6311 **集中式**），不得混用；
> 2. 必须**原样读取** fixture 全文送审，**不要**在测试里过滤注释行——fixture 已清理为纯 DDL，
>    过滤逻辑只会掩盖「文件头污染审核」这类问题；
> 3. 必须用**精确集合相等**断言，不得退化为子集断言。
>
> 两个 fixture 已随设计提交（纯 DDL，来源说明见 `tests/fixtures/README-report-fixtures.md`），
> 与报告 HTML 中的 DDL 逐字一致，请直接读取，**不要手写替代表、不要再加文件头注释**。

**合计 52 例（A9 + B12 + C4 + D6 + E4 + F2 + T10 + N5），要求零 skip。**

### 7.2 需修订的既有测试

**预期为无。** 实测全语料除两条目标 fixture 外零漂移、全量回归零变化。
若施工中出现既有测试失败，**停工复核**，不得改测试迁就代码。

### 7.3 回归门槛（准出条件）

| 门槛 | 要求 |
|---|---|
| G-1 | `pytest tests/` 全量：**1355 passed / 0 failed / 29 skipped**（+新增 52 例 → 1407 passed），无既有用例由通过转失败 |
| G-2 | `test_r077_r054_tdsql_syntax.py` **45 passed** |
| G-3 | `test_parser_tdsql_dialect_fallback.py` **14 passed** |
| G-4 | `test_r061_index_name_quoting.py` **12 passed** |
| G-5 | 新增 `tests/test_parser_index_type_and_uk_comment.py` **52 例全通过，零 skip** |
| G-6 | `verify_rules.py`：119 / 107 / 未覆盖 0 / 断言失败 **3**（与基线同名同因） |
| G-7 | 全语料 197 条 × 119 规则：**恰好 2 条变化**，且均为两个目标 fixture；其余 195 条零漂移 |
| G-8 | 生产 14 表回放**零漂移** |
| G-9 | 全语料索引 `type` 分布 = `{'NORMAL': 61}`；解析失败语句数 = **13** |
| G-10 | F1/F2 **精确集合相等**通过 |
| **G-13** | **T 组 10 例全通过**，其中 T1~T6 的「与去掉 COMMENT 结论相等」断言必须成立 |
| **G-14** | **N 组 5 例 span 数全部为 1** |
| **G-11** | **模糊测试（O §6.4-5）**：对 `_strip_unique_index_comments()` 随机组合引号、括号、逗号、注释、转义生成 ≥2000 条输入，断言**不抛异常**，且凡返回非 `None` 者必满足「长度恒等 + 差异全在 span 内」 |
| **G-12** | 提交说明记录实际 `sqlglot.__version__` |

## 8. 风险与回滚

| 风险 | 等级 | 说明与缓解 |
|---|---|---|
| **改坏字符串字面量内容（Rev.A 的 BLOCK-1）** | **已消除** | 词法器令伪 SQL 结构上不可见；门禁①逐字符校验；6 例负向用例 + 4000 条模糊测试越界改写均为 0 |
| 接纳了不该接纳的候选 AST | **低** | 四道门禁：等长+差异仅在 span、`exp.Create`、`kind=='TABLE'`、表名同一性 |
| 吃掉真语法错误 | **低** | 失败关闭；E 组 4 例锁定。唯一边界见 §5.7 末尾（属消除既有不一致，非新开口子） |
| 合法但 sqlglot 不支持的语法仍误报 | **已知边界** | §5.4 三类，显式声明为产品边界，失败关闭，不用字符串兜底伪造事实 |
| sqlglot 升级导致 AST 假设失效 | **中→低** | 白名单映射不会静默降级；A9 契约测试在升级时显式失败；§5.0 记录版本 |
| 丢失真索引类型 | **低** | A5 锁定真 FULLTEXT |
| 告警数量变化引发用户疑虑 | **需沟通** | gg78 由 5 条 ERROR 变为 2 条 INFO；gg77 少 1 条 WARNING。减少的**全部是误报**，另有 1 处漏报被补上 |
| **UNIQUE-COMMENT 与 TDSQL 方言组合仍失败** | **已消除** | 方言恢复串联；T1~T6 实测全部恢复，且与「去掉 COMMENT」结论相等 |
| **span 被错误批准（作用域越界）** | **已消除** | `at_def_start` + 定义列表闭合即停；5 类作用域负例 span 全为 1 |
| `UnboundLocalError` | **已知陷阱** | §3.2 红框 |

**回滚**：单文件 4 个改动点，`git revert` 单个 commit 即可完全回退。
无数据迁移、无配置变更、无接口变更、无前端联动。

---

## 9. 施工检查单（Q 逐项打勾）

- [ ] **C-1** 产品代码只改 `backend/engine/parser/parser_legacy.py` 一个文件
- [ ] **C-2** 四个改动点（含 import）均按 §3 逐字落地，未做自由发挥
- [ ] **C-3** **Rev.A 的 `_UNIQUE_IDX_COMMENT_RE` 不得出现在代码中**（NG-0）——本次不是「改正则」，是「换实现」
- [ ] **C-4** ⚠️ 重试成功分支**同时**执行 `ast = _retry_ast` 与 `parsed.ast = ast`（§3.2 陷阱）
- [ ] **C-5** 失败路径（`else` 分支内）与改前**逐字一致**，仅整体缩进一层
- [ ] **C-6** 五道门禁一个不少（等长+差异仅在 span、`exp.Create`、`kind=='TABLE'`、表名同一性、**TDSQL 方言恢复串联**）
- [ ] **C-6b** 剥离器入口接受 `CREATE [TEMPORARY] TABLE`；`at_def_start` 状态存在且正确；定义列表闭合即 `break`
- [ ] **C-7** 剥离器在词法异常 / 括号未闭合 / 非建表 / 无 span / span 越界时一律返回 `(None, [], "")`
- [ ] **C-8** 未新增第三方依赖；只新增 `from sqlglot.tokens import TokenType`
- [ ] **C-9** 规则层零改动：`ddl.py`/`index.py`/`distributed.py`/`dml.py`/`oracle_compat.py`
- [ ] **C-10** `_TDSQL_DIALECT_RE` 及 v1.6.2.0 重试块、`_parse_unique_constraint()` 一字未动
- [ ] **C-11** 新建 `tests/test_parser_index_type_and_uk_comment.py`，覆盖 §7.1 A~F+T+N 共 **52 例**，零 skip
- [ ] **C-12** F 组**原样读取**已提交的两个纯 DDL fixture（**不要过滤注释行**），6309 用**分布式**、6311 用**集中式**，且用**精确集合相等**断言
- [ ] **C-12b** **不得**给这两个 fixture 重新添加任何文件头注释
- [ ] **C-13** 未修改任何既有测试文件；若确需修改，**停工回报**
- [ ] **C-14** G-1 ~ G-14 十四道门槛逐条实测通过，提交说明中贴出实测数字
- [ ] **C-15** 导入自检：`python -c "from backend.engine.parser.parser_legacy import SQLParser, _strip_unique_index_comments"` 无异常
- [ ] **C-16** 版本号更新：`VERSION` 与 `backend/config.py` 的 `APP_VERSION`、`APP_DESCRIPTION` → `1.6.2.2`
- [ ] **C-17** 提交说明记录实际 `sqlglot.__version__`
- [ ] **C-18** 提交信息：`fix(v1.6.2.2): 索引类型误判与唯一索引注释解析崩溃修复`

---

## 附录 A：实测证据清单（Rev.C）

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
| **A-36** | **文档代码块自验证** | §3.1/§3.2 四个块抽取施工到干净工作树，行为与实现完全一致 |

---

## 附录 B：给智能体 Q 的六句话

1. **本次不是"把正则改好"，是"把正则换掉"。** Rev.A 的 `_UNIQUE_IDX_COMMENT_RE` 必须**整体删除**，
   不要保留任何跨语义边界的正则改写（NG-0）。
2. **`at_def_start` 那个状态是这一版的核心，不能省。** 少了它，`CONSTRAINT x UNIQUE (...)`、
   列内联 `UNIQUE`、定义项中部的 `UNIQUE` 都会被错误地当成目标——Rev.B 就是这么被打回来的。
   **span 门禁只能自证「改动落在自己声明的范围内」，证明不了「这个范围是对的」。**
3. **方言恢复必须串联。** 这是 TDSQL 平台，`TDSQL_DISTRIBUTED BY HASH` 是分片表主流写法，
   它和 UNIQUE-COMMENT 的交集才是最该修好的场景。复用**同一条** `_TDSQL_DIALECT_RE`，
   不要另写正则，也不要放宽它的 Command 前置条件。
4. **§3.2 那个 `ast` 重绑的坑我真踩过。** 只赋 `parsed.ast` 会 `UnboundLocalError`，
   且要跑到含 UNIQUE-COMMENT 的语句才炸。
5. **F 组要原样读 fixture、用精确相等断言。** 不要过滤注释行（fixture 已是纯 DDL），
   不要再加文件头（我上一版加的文件头就让 gg78 多出一条 R104），
   6309 走**分布式**、6311 走**集中式**。
6. **T 组那条"与去掉 COMMENT 结论相等"是最强的护栏。** 它证明这次恢复没有引入任何自己的口径，
   只是让本该被审核的表回到它应有的结果。如果这条断言不成立，说明实现跑偏了。
