# DESIGN-v1.6.2.2 索引类型误判与唯一索引注释解析崩溃 修复详细设计说明书

| 项目 | 内容 |
|---|---|
| 文档版本 | Rev.A |
| 目标版本 | **v1.6.2.2** |
| 缺陷来源 | 内网人工扫描报告 #6309（gg77）、#6311（gg78） |
| 缺陷编号 | **DEF-1 = DEF-R054-FAKEUNIQUE**；**DEF-2 = DEF-PARSE-UKCOMMENT** |
| 撰写 | 智能体 A |
| 施工 | 智能体 Q |
| 基线 commit | `29a0786`（main，v1.6.2.1 末态） |
| 改动范围 | **单文件 `backend/engine/parser/parser_legacy.py`，3 个改动点** |
| 实测结论 | 生产 14 表**零漂移**；全语料 195 条 × 119 规则**零漂移**；全量回归 **1355 passed / 0 failed / 29 skipped**，与基线逐项一致；两个现场缺陷全部消除 |

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
| **A（采纳）** | 抛错后剥离 UNIQUE 索引 COMMENT **重试一次**解析 | 恢复**完整**解析（columns 0→75），沿用 v1.6.2.0 已验证的"降级重试"范式 |
| B | 在 `except` 里补调 `_regex_fallback_create_table_props()` | 只能救回 4 个字段，columns/indexes 仍空，R029/R061/R018/R019 继续漏审；且该函数不感知字符串字面量，`COMMENT '……PRIMARY KEY……'` 会造成 R003 **漏报** |
| C | 升级/更换 sqlglot | 影响面不可控，且不在本次范围 |

**A 与 B 不是二选一**——B 可作为"重试也失败时"的兜底加固，但那会把改动扩到**所有**解析失败语句（当前 13 条），
超出本次两个缺陷的范围。**本次只做 A**，B 登记为 ADJ-10（见 §6）。

### 2.3 三条关键安全性质（均已实测）

1. **只在已经抛错的语句上生效**：正常解析的语句根本进不了 `except` 分支，
   故本次改动对"当前能解析的一切语句"**可证明为零影响**（实测全语料漂移 0）。
2. **失败路径逐字不变**：重试不适用或重试失败时，走的仍是改前那段代码，
   `parse_error` / 表名回退 / `return parsed` 一字未动（探针 U7：真语法错误仍原样报 E999 + 4 条）。
3. **`raw_sql` 保持原文**：重试只作用于送进 sqlglot 的副本，`parsed.raw_sql` 仍是原始 DDL
   ——R077/R054/R116-R118 依赖它提取分片键（实测 `gg78` 修复后 `raw_sql` 仍含原 COMMENT）。

---

## 3. 详细设计（照图施工）

### 3.1 改动点 1：模块级新增正则

**位置**：`backend/engine/parser/parser_legacy.py`，紧接现有 `_TDSQL_DIALECT_RE` 定义之后（约第 29 行后），
与下方 `@dataclass class ParsedSQL` 之间保留两个空行。

```python
# v1.6.2.2: sqlglot(30.x) 的 mysql 方言不支持 UNIQUE 索引上的 COMMENT 子句
#   UNIQUE KEY `uk` (`a`) COMMENT '说明'      ← 抛 ParseError
#   KEY        `k`  (`a`) COMMENT '说明'      ← 正常解析（普通索引不受影响）
# 整条 CREATE TABLE 抛错后 columns/engine/charset/主键/表注释全空，
# 导致 R003/R004/R005/R028 集体误报。本正则仅用于"已经抛错"的重试路径。
_UNIQUE_IDX_COMMENT_RE = re.compile(
    r"(\bunique\b\s*(?:key|index)?\s*"          # UNIQUE [KEY|INDEX]
    r"(?:`[^`]+`|\"[^\"]+\"|\w+)?\s*"            # 可选索引名
    r"\([^()]*\)"                                 # 列清单
    r"(?:\s+using\s+\w+)?)"                      # 可选 USING BTREE/HASH
    r"\s+comment\s+'(?:[^']|'')*'",              # ← 剥掉这段 COMMENT
    re.IGNORECASE,
)
```

**正则鉴别实测**（应剥 / 不应剥）：

| 片段 | 期望 | 实测 |
|---|---|---|
| `UNIQUE KEY \`u\` (\`a\`) COMMENT '注释'` | 剥 | ✅ 剥 |
| `UNIQUE KEY \`u\` (\`a\`) USING BTREE COMMENT '注释'` | 剥 | ✅ 剥 |
| `UNIQUE INDEX \`u\` (\`a\`) COMMENT '注释'` | 剥 | ✅ 剥 |
| `UNIQUE (\`a\`) COMMENT '注释'` | 剥 | ✅ 剥 |
| `UNIQUE KEY \`u\` (\`a\`)` | 不剥 | ✅ 不剥 |
| `KEY \`k\` (\`a\`) COMMENT '注释'` | 不剥 | ✅ 不剥 |
| `` `col` varchar(10) COMMENT '列注释' `` | 不剥 | ✅ 不剥 |

### 3.2 改动点 2：`parse()` 的 `except` 分支增加剥离重试

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
            # v1.6.2.2: UNIQUE 索引带 COMMENT 会让 sqlglot 抛 ParseError，整条语句
            # 结构信息全丢，R003/R004/R005/R028 集体误报。仅在"确实抛错"且"语句含
            # UNIQUE 索引 COMMENT"时剥离该子句重试一次；只有重试确实产出 exp.Create
            # 才采用。重试不适用或失败时，下方失败路径与改前逐字一致。
            _retry_ast = None
            if _UNIQUE_IDX_COMMENT_RE.search(sql_clean):
                try:
                    _cand = sqlglot.parse_one(
                        _UNIQUE_IDX_COMMENT_RE.sub(r"\1", sql_clean), read=self.dialect)
                    if isinstance(_cand, exp.Create):
                        _retry_ast = _cand
                except Exception:
                    _retry_ast = None
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

> 🚨 **施工陷阱（我在原型阶段真踩到了，务必注意）**
> 重试成功后**必须同时重绑局部变量 `ast`**，不能只赋 `parsed.ast`。
> `except` 之后的通用流程（第 157 行起的 `self._get_sql_type(ast)`、`_parse_create(ast, parsed)`、
> `_parse_common(ast, parsed)`）**直接引用局部变量 `ast`**，而该变量在抛错时从未被赋值。
> 只写 `parsed.ast = _retry_ast` 会得到：
> `UnboundLocalError: cannot access local variable 'ast' where it is not associated with a value`。
> 我的第一版原型就是这么挂的。

### 3.3 改动点 3：索引类型判据

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

**改动后**（逐字照抄）：

```python
        # 判断索引类型
        # v1.6.2.2: 原实现 `def_str = str(col_def).upper()` + 裸子串包含判断，会把
        # 列名/索引名中含 unique/primary/fulltext 的普通索引误判（实测：列名
        # list_unique_num → 该普通索引被标成 UNIQUE），进而 R054 对普通索引误报，
        # 且真唯一索引被顶替而漏检。实测 sqlglot 30.x：IndexColumnConstraint 只承载
        # kind ∈ {None,'FULLTEXT','SPATIAL'}，UNIQUE 走 UniqueColumnConstraint、
        # PRIMARY 走 exp.PrimaryKey，都不经过本函数。SPATIAL 维持判为 NORMAL，
        # 与修复前对干净列名的行为一致，输出域不变（NORMAL / FULLTEXT）。
        kind = (col_def.args.get("kind") or "").upper()
        idx_type = "FULLTEXT" if kind == "FULLTEXT" else "NORMAL"
```

> ✅ **本文档的代码块已自验证**：§3.2 与 §3.3 的「改动前」块经程序比对与
> `parser_legacy.py` **逐字匹配**；三个「改动后」块被**原样抽取**并施工到一棵干净工作树上，
> 实测语法通过、导入自检通过、行为与原型**完全一致**（生产 14 表零漂移、全语料零漂移、
> 16 例判别矩阵逐例相同）、全量回归 **1355 passed / 0 failed / 29 skipped**。
> Q 可以直接复制粘贴，无需再做适配。
>
> ⚠️ 抽取时注意：§3.3 的两个块**前者是「改动前」、后者是「改动后」**，两块开头都是
> `# 判断索引类型`，我在自验证时就先搞反过一次。

### 3.4 改动汇总

| 序号 | 文件 | 位置 | 改动 |
|---|---|---|---|
| 1 | `parser_legacy.py` | `_TDSQL_DIALECT_RE` 之后 | 新增模块级 `_UNIQUE_IDX_COMMENT_RE`（+14 行，含 5 行注释） |
| 2 | 同上 | `parse()` 的 `except` 分支 | 增加剥离重试；原失败路径整体下移一层缩进（+19/-8） |
| 3 | 同上 | `_parse_index_constraint()` | 类型判据由子串包含改为读 `kind`（+9/-7，其中 7 行是注释） |

**产品代码：1 个文件、3 个改动点、`git diff --stat` 预期约 `50 insertions(+), 18 deletions(-)`。
不新增 import（`re` / `sqlglot` / `exp` 均已在用），不新增依赖，规则层一行不动。**

---

## 4. 明确的非目标（NG，施工红线）

| 编号 | 非目标 | 说明 |
|---|---|---|
| **NG-1** | **不改任何规则文件** | `ddl.py` / `index.py` / `distributed.py` / `dml.py` / `oracle_compat.py` **零改动**。本次是解析器供数问题，不是规则判据问题 |
| **NG-2** | **不动 `distributed.py`** | v1.6.1.9 冻结代码；`_iter_unique_indexes` 的早退逻辑本次不碰——DEF-1 修好后它拿到的就是正确输入 |
| **NG-3** | **不动 `_parse_unique_constraint()`** | 它硬编码 `"type": "UNIQUE"`，本就正确 |
| **NG-4** | **不动 v1.6.2.0 的 TDSQL 方言重试** | `_TDSQL_DIALECT_RE` 及其重试块一字不改 |
| **NG-5** | **不动 v1.6.2.1 的 R061 去引号** | `index.py` 一字不改 |
| **NG-6** | **不把 SPATIAL 单独成型** | 维持判为 NORMAL，保证输出域不变 |
| **NG-7** | **不新增字段级字符集/排序规则检查** | 用户已决策：R005 维持只判表级，字段级字符集本次不纳入 |
| **NG-8** | **不在 `except` 补调 `_regex_fallback_create_table_props()`** | 见 §2.2 方案 B，登记 ADJ-10 |
| **NG-9** | **不修 E999 文案** | 现文案"可能是拉取截断/语法错误"对合法 MySQL 有误导，但属独立体验问题，登记 ADJ-12 |
| **NG-10** | **不修 `CONSTRAINT x UNIQUE (col)` 形态漏检** | 新发现的既有缺陷，登记 ADJ-11 |

---

## 5. 影响面分析（全部实测）

环境：MariaDB `127.0.0.1:13306`（`slow_query_log=ON`）+ uvicorn `127.0.0.1:8000` 均在线。
对照：基线 `29a0786` vs 原型工作树，同脚本、同语料、同参数。

### 5.1 引擎指纹与解析产物分布

| 指标 | 基线 | 修复后 |
|---|---|---|
| 规则总数 | 119 | **119** |
| 全语料索引 `type` 分布 | `{'NORMAL': 51}` | **`{'NORMAL': 51}`** |
| 全语料解析失败语句数 | 13 | **13** |

> 语料内 `type` 分布与解析失败数**均未变**——说明现有语料里既没有"名字含 unique 的索引"，
> 也没有"UNIQUE 索引带 COMMENT"，故改动对既有语料**完全无扰**。
> 这同时暴露了**语料缺少这两种生产形态**，故 §7 要求补齐。

### 5.2 生产 14 表回放（v1.6.2.1 已稳定，要求零漂移）

**变化表数 = 0。** 14 张表命中规则集合**逐表逐条相同**。✅

### 5.3 全语料 × 全规则漂移

195 条语句 × 119 条规则，逐条比对命中集合与解析错误标志：

| 指标 | 结果 |
|---|---|
| 语句数 | 195 / 195 |
| **发生变化的语句数** | **0** |

### 5.4 两个现场 SQL

| | 基线 | 修复后 |
|---|---|---|
| **gg77** 命中 | R011,R018,R019,R036,R037,**R054**,R061,R065,R067,R104 | R011,R018,R019,R036,R037,R061,R065,R067,R104 |
| gg77 `idx13` 类型 | **UNIQUE**（误判） | **NORMAL** ✅ |
| gg77 其余 9 条规则 | — | **逐条不变** ✅ |
| **gg78** 命中 | **E999,R003,R004,R005,R028** | **R036,R037** |
| gg78 解析产物 | cols=0 pk=False engine=None charset=None | cols=**75** pk=**True** engine=**'INNODB'** charset=**'UTF8MB4'** ✅ |
| gg78 `raw_sql` 保留原 COMMENT | — | **是** ✅ |

### 5.5 判别矩阵 16 例（正向 / 反向 / 边界，全部实测）

| 编号 | 用例 | 基线 | 修复后 | 判定 |
|---|---|---|---|---|
| T1 | 普通索引，列名 `list_unique_num` | type=**UNIQUE** | type=**NORMAL** | ✅ 误判消除 |
| T2 | 索引名 `unique_lookup` | type=**UNIQUE** | type=**NORMAL** | ✅ |
| T3 | 列名 `biz_primary_no` | type=**PRIMARY** | type=**NORMAL** | ✅ |
| T4 | 列名 `fulltext_body` | type=**FULLTEXT** | type=**NORMAL** | ✅ |
| **T5** | **真 FULLTEXT 索引** | type=FULLTEXT | type=**FULLTEXT** | ✅ **反向鉴别：真类型不丢** |
| **T6** | **真 UNIQUE 不含分片键** | R054 报 | R054 **仍报** | ✅ **反向鉴别** |
| T7 | 真 UNIQUE 含分片键 | 不报 | 不报 | ✅ |
| **T8** | **诱饵列名 + 真 UNIQUE 不含分片键** | **★ 不报（漏报）** | **R054 报出** | ✅ **漏报被修复** |
| U1 | `UNIQUE KEY ... COMMENT` | 解析失败 cols=0 | cols=2 ✅ | ✅ |
| U2 | `UNIQUE INDEX ... COMMENT` | 解析失败 | cols=2 ✅ | ✅ |
| U3 | `UNIQUE KEY ... USING BTREE COMMENT` | 解析失败 | cols=2 ✅ | ✅ |
| U4 | 普通 `KEY ... COMMENT` | 正常 | **不变** | ✅ 无回归 |
| U5 | `UNIQUE KEY` 无 COMMENT | 正常 | **不变** | ✅ 无回归 |
| U6 | 仅列级 COMMENT | 正常 | **不变** | ✅ 无回归 |
| **U7** | **真语法错误**（`,,`） | E999+R003/4/5/28 | **完全相同** | ✅ **失败路径逐字不变** |
| **U8** | UNIQUE COMMENT + 真 R054 违规 | E999,R003,R004,R005,R028,R054,**R118** | **R036,R037,R054** | ✅ 误报全消、真违规保留 |

**U8 的 R118 消失已核实为"另一条误报被一并修好"**：该表 `sk` 明确写了 `NOT NULL`；
R118（`oracle_compat.py:963-972`）在 `parsed.columns` 为空时走 raw-SQL 兜底正则
`\bsk\s+\w+`，该正则认不出反引号列名 `` `sk` varchar(20) ``，落到第 972 行的
`else` 无条件报违规。修复后 columns 非空，走正常分支，正确不报。

### 5.6 全量回归与审核物料校验器

```
基线   ：1355 passed, 29 skipped, 0 failed
修复后 ：1355 passed, 29 skipped, 0 failed        ← 逐项一致

verify_rules.py  基线   ：119 / 107 / 未覆盖 0 / 断言失败 3
verify_rules.py  修复后 ：119 / 107 / 未覆盖 0 / 断言失败 3   ← 逐项一致
```

3 条断言失败两侧同名同因（`01_naming_ddl.sql` 的 `R023_01`/`R098_01`/`R116_01` 期望多写了
`R036,R037`），是**先于本次改动存在的测试资产缺陷**。

> ✅ **零回归。**

---

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
| **ADJ-12** | E999 文案"可能是拉取截断/语法错误"对合法 MySQL 有误导 | 🆕 **本次登记，不修**（NG-9） |
| R036 只认两个字面名 | 🔒 用户决策：维持现状 |
| 字段级字符集检查 | 🔒 用户决策：本次不纳入（NG-7） |

---

## 7. 验收测试方案

### 7.1 新增测试（新建 `tests/test_parser_index_type_and_uk_comment.py`）

**A 组 — DEF-1 索引类型判据（8 例）**

| 编号 | 用例 | 断言 |
|---|---|---|
| A1 | 普通索引，列名 `list_unique_num` | `type == "NORMAL"`；R054 **不命中** |
| A2 | 索引名 `unique_lookup` | `type == "NORMAL"` |
| A3 | 列名 `biz_primary_no` | `type == "NORMAL"` |
| A4 | 列名 `fulltext_body` | `type == "NORMAL"` |
| **A5** | **真 `FULLTEXT KEY`** | `type == "FULLTEXT"`（**反向鉴别：真类型不得丢**） |
| **A6** | **真 UNIQUE 不含分片键** | R054 **命中**（**反向鉴别**） |
| A7 | 真 UNIQUE 含分片键 | R054 不命中 |
| **A8** | **诱饵列名 + 真 UNIQUE 不含分片键** | R054 **命中**（**锁定漏报修复**，本组最重要） |

**B 组 — DEF-2 唯一索引 COMMENT（8 例）**

| 编号 | 用例 | 断言 |
|---|---|---|
| B1 | `UNIQUE KEY u (a) COMMENT '..'` | `parse_error` 为空且 `len(columns) > 0` |
| B2 | `UNIQUE INDEX u (a) COMMENT '..'` | 同上 |
| B3 | `UNIQUE KEY u (a) USING BTREE COMMENT '..'` | 同上 |
| B4 | `UNIQUE (a) COMMENT '..'` | 同上 |
| B5 | 普通 `KEY k (a) COMMENT '..'` | 与修复前一致（**无回归**） |
| B6 | `UNIQUE KEY` 无 COMMENT | 与修复前一致（**无回归**） |
| **B7** | **真语法错误**（如 `(\`a\` int,, PRIMARY KEY ...`） | **仍命中 E999**，且 `parse_error` 非空（**失败路径不得被吃掉**） |
| **B8** | UNIQUE COMMENT + 真实 R054 违规 | R061/R003/R004/R005/R028 **不命中**，R054 **命中** |

**C 组 — 生产回放（2 例）**

| 编号 | 用例 | 断言 |
|---|---|---|
| **C1** | 报告 #6309 `kcfb_list_info` **原样 DDL** | R054 **不命中**；且 `{'R011','R018','R019','R036','R037','R061','R065','R067','R104'} <= rule_ids`（**证明只减 R054**） |
| **C2** | 报告 #6311 `biz_tx_log` **原样 DDL** | `{'R003','R004','R005','R028'} & rule_ids == set()`，`E999` 不命中，且 `{'R036','R037'} <= rule_ids` |

> ✅ **两个 fixture 我已随本设计一并提交，Q 直接读取即可，不要另行手写替代表**：
> - `tests/fixtures/report_6309_kcfb_list_info.sql`
> - `tests/fixtures/report_6311_biz_tx_log.sql`
>
> 二者均为报告 #6309 / #6311 的**原样 DDL**（已用 `diff` 校验与报告原文逐字一致，
> 仅在文件头加了 3 行来源说明注释）。实测在基线上分别复现出
> `R054` 与 `E999,R003,R004,R005,R028`，即确实能复现缺陷。
>
> 之所以由我提供而不是留给施工方：v1.6.2.1 的复测教训是——手写精简替代表会让
> 设计要求的断言无法成立，最后被迫放弃断言（该轮 G1 用例因此丢掉了"只减目标规则"
> 这条核心安全性质）。这次直接把原样 DDL 备好，从源头消除这个失败模式。

### 7.2 需修订的既有测试

**预期为无。** 实测全语料漂移 0、全量回归零变化。
若施工中出现既有测试失败，**停工复核**，不得改测试迁就代码。

### 7.3 回归门槛（准出条件）

| 门槛 | 要求 |
|---|---|
| G-1 | `pytest tests/` 全量：**1355 passed / 0 failed / 29 skipped**（+新增 18 例 → 1373 passed），无既有用例由通过转失败 |
| G-2 | `test_r077_r054_tdsql_syntax.py` **45 passed** |
| G-3 | `test_parser_tdsql_dialect_fallback.py` **14 passed** |
| G-4 | `test_r061_index_name_quoting.py` **12 passed** |
| G-5 | 新增 `tests/test_parser_index_type_and_uk_comment.py` **18 例全通过，零 skip** |
| G-6 | `verify_rules.py`：119 / 107 / 未覆盖 0 / 断言失败 **3**（与基线同名同因） |
| G-7 | 全语料 195 条 × 119 规则漂移 **= 0** |
| G-8 | 生产 14 表回放 **零漂移**（v1.6.2.1 成果不得被打破） |
| G-9 | 全语料索引 `type` 分布 = `{'NORMAL': 51}`，解析失败语句数 = **13**（均与基线相同） |
| G-10 | gg77 只减 R054、gg78 只剩 R036/R037 |

---

## 8. 风险与回滚

| 风险 | 等级 | 说明与缓解 |
|---|---|---|
| 重试正则误剥合法内容 | **极低** | 只在**已抛错**的语句上运行；正常语句永不进入该分支（实测漂移 0）。7 例正则鉴别全部符合预期 |
| 重试吃掉真语法错误 | **低** | 只有 `isinstance(_cand, exp.Create)` 才采用；探针 B7/U7 锁定真错误仍报 E999 |
| 丢失真索引类型 | **低** | `kind` 是 sqlglot 的结构化字段；A5 锁定真 FULLTEXT 不丢 |
| 删掉 PRIMARY/UNIQUE 分支导致漏判 | **极低** | 已枚举 18 种写法证明这两个分支对合法输入结构上不可达 |
| 告警数量变化引发用户疑虑 | **需沟通** | gg78 由 5 条 ERROR 降为 2 条 INFO；gg77 少 1 条 WARNING。**减少的全部是误报**，另有 1 处漏报被补上 |
| `UnboundLocalError` | **已知陷阱** | 见 §3.2 红框，必须重绑 `ast` |

**回滚**：单文件 3 个改动点，`git revert` 单个 commit 即可完全回退。
无数据迁移、无配置变更、无接口变更、无前端联动。

---

## 9. 施工检查单（Q 逐项打勾）

- [ ] **C-1** 产品代码只改 `backend/engine/parser/parser_legacy.py` 一个文件
- [ ] **C-2** 三个改动点均按 §3 逐字落地，未做自由发挥
- [ ] **C-3** ⚠️ 重试成功分支**同时**执行了 `ast = _retry_ast` 和 `parsed.ast = ast`（§3.2 陷阱）
- [ ] **C-4** 失败路径（`else` 分支内）与改前**逐字一致**，仅整体缩进一层
- [ ] **C-5** 未新增 import、未新增依赖
- [ ] **C-6** 规则层零改动：`ddl.py`/`index.py`/`distributed.py`/`dml.py`/`oracle_compat.py`（NG-1/2/5）
- [ ] **C-7** `_TDSQL_DIALECT_RE` 及 v1.6.2.0 重试块一字未动（NG-4）
- [ ] **C-8** `_parse_unique_constraint()` 一字未动（NG-3）
- [ ] **C-9** 新建 `tests/test_parser_index_type_and_uk_comment.py`，覆盖 §7.1 全部 18 例，**零 skip**
- [ ] **C-10** C1/C2 使用报告 #6309/#6311 的**原样 DDL** 并落为 fixture；fixture 文件头如实标注
- [ ] **C-11** 未修改任何既有测试文件；若确需修改，**停工回报**
- [ ] **C-12** G-1 ~ G-10 十道门槛逐条实测通过，提交说明中贴出实测数字
- [ ] **C-13** 导入自检：`python -c "from backend.engine.parser.parser_legacy import SQLParser"` 无异常
- [ ] **C-14** 版本号更新：`VERSION` 与 `backend/config.py` 的 `APP_VERSION`、`APP_DESCRIPTION` → `1.6.2.2`
- [ ] **C-15** 提交信息：`fix(v1.6.2.2): 索引类型误判与唯一索引注释解析崩溃修复`

---

## 附录 A：实测证据清单

| 编号 | 证据 | 结论 |
|---|---|---|
| A-1 | gg77 复现 + `parsed.indexes` type 打印 | idx13 被标 UNIQUE，坐实 DEF-1 |
| A-2 | `str(col_def)` 直接打印 | `INDEX "..." ("list_unique_num", ...)` 含 UNIQUE 子串 |
| A-3 | 18 种索引写法 × AST 节点/`kind` 枚举 | UNIQUE/PRIMARY 从不进 `IndexColumnConstraint` |
| A-4 | 列名/索引名暴露面探针 | unique→UNIQUE、primary→PRIMARY、fulltext→FULLTEXT |
| A-5 | T8 漏报构造实验 | 诱饵存在时 R054 完全不报；去掉诱饵立刻报出 |
| A-6 | gg78 消融实验（3 组） | 索引级 COMMENT 是唯一原因，分区块无关 |
| A-7 | 8 种索引 COMMENT 写法矩阵 | 仅 UNIQUE 系列抛错，普通 KEY 正常 |
| A-8 | gg78 "只删索引注释"对照 | cols 0→75，四条 ERROR 全消 |
| A-9 | `_UNIQUE_IDX_COMMENT_RE` 7 例鉴别 | 应剥/不应剥全部符合预期 |
| A-10 | 生产 14 表回放 | **零漂移** |
| A-11 | 全语料 195 条 × 119 规则漂移 | **0 条变化**；type 分布与解析失败数均不变 |
| A-12 | 16 例判别矩阵（含 4 例反向鉴别） | 误报全消、真类型/真违规全保、失败路径不变 |
| A-13 | 全量回归 + `verify_rules.py` 双侧对比 | **逐项一致** |
| A-14 | R118 消失机理核实 | 亦为误报（兜底正则认不出反引号列名），修复正确 |
| A-15 | `CONSTRAINT c UNIQUE (col)` 探针 | R054 完全不报 → 登记 ADJ-11 |
| A-16 | **文档代码块自验证**：抽取三个「改动后」块施工到干净工作树 | 语法/导入/行为/全量回归四项与原型逐项一致 |
| A-17 | 两个 fixture 与报告原文 `diff` 校验 + 基线复现 | 逐字一致；基线分别复现 R054 与 E999+R003/4/5/28 |

---

## 附录 B：给智能体 Q 的四句话

1. **规则层一行都不要动。** 这两个缺陷都是解析器供数错误，不是规则判据错误。
   你只需要改 `parser_legacy.py` 一个文件。
2. **§3.2 那个 `ast` 重绑的坑我真踩过。** 只赋 `parsed.ast` 会 `UnboundLocalError`，
   而且要跑到含 UNIQUE-COMMENT 的语句才炸，单测不覆盖就会漏。
3. **C 组测试请用报告里的原样 DDL。** 上一轮 v1.6.2.1 你手写精简表，
   导致设计要求的断言无法成立而被迫放弃——这次两张表的原样 DDL 在报告 #6309/#6311 里都有。
4. **这次是"净减误报 + 补一处漏报"。** 除了 gg77 少一条 R054、gg78 少四条 ERROR 之外，
   任何语句都不该出现新增告警；如果你的实现让别的语句多出规则命中，那一定是错的。
