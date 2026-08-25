# v1.6.2.2 索引解析修复设计 Rev.E 第五轮独立复审报告

| 项目 | 内容 |
|---|---|
| 复审对象 | `DESIGN-v1.6.2.2-索引类型误判与唯一索引注释解析崩溃修复详细设计说明书.md` Rev.E |
| 被审提交 | `ba8a9d8a98257cc47ea025a73f79e82b69200767` |
| 复审日期 | 2026-08-25 |
| 复审人 | Codex（独立复审） |
| 复审方式 | 文档逐段核验、按 Rev.E 代码块在 detached worktree 临时施工、主干前后双侧对比、非法语法对抗探针、sqlglot 上下界复测、现场 fixture 精确集合断言、专项与全量回归 |
| 最终结论 | **No-Go** |

## 1. 结论摘要

Rev.E 对第四轮问题的整改是实质性的。经独立施工与复测，以下问题已经关闭：

1. `TDSQL_DISTRIBUTED` 后的 `BY`、方法名和左括号已改为顺序必选；
2. STRING / IDENTIFIER 不再被当作裸 `TDSQL_DISTRIBUTED`、`BY`、方法名或 `BROADCAST`；
3. 表注释中的同名字样不再阻断后续真实方言尾子句；
4. HASH+BROADCAST、HASH+RANGE 等双声明已失败关闭；
5. 表名后必须紧接定义列表左括号，CTAS / LIKE 不再借用后续函数括号；
6. 含分号输入失败关闭，首次重试只接纳“同表名的 `CREATE TABLE`”，不再接纳 `Block`；
7. `sqlglot>=29,<31` 已成为确定改动，不再是待拍板事项；
8. 第四轮会指导施工者恢复旧全局正则的危险旧指令已经清理。

两项原始缺陷的主要修复方向也成立：

- DEF-1 用 AST `kind` 代替 AST 字符串的裸子串判断，可以根治普通索引被伪装成 UNIQUE/PRIMARY/FULLTEXT；
- DEF-2 用 tokenizer 精确剥离顶层 UNIQUE 索引 COMMENT，再在严格门禁下重试，可以恢复目标 DDL 的完整结构；
- 删除 `_TDSQL_DIALECT_RE` 全局替换并统一使用 token 级尾子句剥离器，是正确且必要的附带修复。

但是第五轮发现两个新的发布阻断：

- **BLOCK-E1**：方言方法的括号内部只检查“括号能配平”，没有验证分片键语法。`HASH()`、`HASH(,)`、`HASH('id')`、`HASH(id + 1)`、`HASH(lower(id))` 等均被批准为可删除 span。与 UNIQUE COMMENT 组合时，现版本保留的 `E999_SYNTAX_ERROR` 会消失，非法 SQL 被恢复成完整 `Create` AST。
- **BLOCK-E2**：两个剥离器的表名白名单都包含 `TokenType.STRING`，且同表名门禁主动剥离单引号比较。`CREATE TABLE 't' (...)` 与 UNIQUE COMMENT 组合时，同样会由 `E999` 变成成功 `Create`。当前 MySQL tokenizer 已明确把单引号表名标为 STRING，本设计却将它作为合法表名接纳。

这两项都直接违反设计自己的 S-2b 与 S-3：span 的字符边界虽然正确，但“被批准删除的内容是否确为产品支持的完整合法语法”仍未得到证明。全量回归和现有 Y 组没有覆盖这两个维度，所以可以在全部绿灯的同时存在次生灾害。

**最终结论：No-Go。** Rev.E 的总体架构不需要推翻；应继续保留 tokenizer、联合 span 和 AST 门禁，只把“方法参数”和“表名头部”收紧为明确语法，并补齐对应反例。

## 2. 复审边界与方法

本轮没有修改项目产品代码。为验证“文档能否按图施工”，我在 detached worktree 中原样落地 Rev.E 的候选实现：

- 删除 `_TDSQL_DIALECT_RE`；
- 新增 `_spans_only_diff()`、`_is_bare_kw()`、`_tdsql_table_def_end()`、`_strip_tdsql_dialect_tail()` 与 `_same_table_name()`；
- 落地 `_strip_unique_index_comments()`；
- 改造首次 `Command` 恢复与异常恢复的两阶段链路；
- 落地 DEF-1 `kind` 白名单；
- 将两处依赖声明临时改为 `sqlglot>=29,<31`。

候选代码仅用于复审验证，没有进入主分支。验证使用仓库真实 `SQLParser`、`RuleChecker`、119 条规则与两份现场 fixture；并在 29.0.0、当前环境 30.12.0、30.14.0 三个 sqlglot 版本上交叉运行关键探针。

本报告只评审方案，不实施产品修复。

## 3. 两个原始问题的发生原因与处理机制

### 3.1 DEF-1：普通索引为什么会被误判成 UNIQUE

#### 3.1.1 触发条件

普通索引的索引名或列名中包含 `unique`、`primary`、`fulltext` 子串。例如现场表中的普通索引：

```sql
KEY `kcfb_list_info_idx13` (`list_unique_num`,`lgl_pern_code`)
```

该索引并没有 UNIQUE 属性，但列名 `list_unique_num` 含有 `unique`。

#### 3.1.2 发生原因

当前 `_parse_index_constraint()` 不读取 AST 的类型字段，而是把整个索引 AST 字符串化后做裸子串搜索：

```python
def_str = str(col_def).upper()
if "PRIMARY" in def_str:
    idx_type = "PRIMARY"
elif "UNIQUE" in def_str:
    idx_type = "UNIQUE"
elif "FULLTEXT" in def_str:
    idx_type = "FULLTEXT"
```

`str(col_def)` 同时包含索引名和所有列名，所以这里判断的不是“语法类型”，而是“整段文本中是否恰好出现某个单词片段”。现场普通索引因此被写入：

```text
{"name": "kcfb_list_info_idx13", "type": "UNIQUE", ...}
```

#### 3.1.3 错误如何传导到规则层

R054 的 `_iter_unique_indexes()` 优先消费 `parsed.indexes` / `parsed.index_definitions`。只要找到任何 `type == "UNIQUE"`，就把 `seen` 置为真，并停止使用 raw SQL 唯一索引兜底：

```text
普通索引含 unique 子串
  → parser 错标为 UNIQUE
  → R054 把普通索引当唯一索引检查
  → 现场出现 R054 假告警
  → seen=True，raw SQL 兜底不再执行
  → 真正的 UNIQUE 索引可能完全没有被检查
```

因此该缺陷同时存在三种后果：

1. R054 对普通索引误报；
2. 假 UNIQUE 抑制兜底后，真正违反 J-3 的 UNIQUE 索引可能漏报；
3. R061 等索引类型消费者可能给普通索引套用唯一索引文案或命名要求。

#### 3.1.4 Rev.E 的处理机制

Rev.E 改为读取 `IndexColumnConstraint.args["kind"]`，并使用有限白名单：

```python
kind = (col_def.args.get("kind") or "").upper()
idx_type = kind if kind in {"PRIMARY", "UNIQUE", "FULLTEXT"} else "NORMAL"
```

该机制把判据从“名称字符串”改成“语法树结构”：

- 普通 KEY/INDEX 的 `kind=None`，稳定映射为 NORMAL；
- FULLTEXT 的 `kind=FULLTEXT`，保持原输出；
- UNIQUE 由 `UniqueColumnConstraint` 和既有 `_parse_unique_constraint()` 处理；
- PRIMARY 由 `exp.PrimaryKey` 处理；
- SPATIAL 本次继续映射为 NORMAL，保持既有输出域。

第五轮对现场 fixture 的独立结果为：

| 版本 | `report_6309_kcfb_list_info.sql` 规则集合 |
|---|---|
| 当前主干 | `{R011,R018,R019,R036,R037,R054,R061,R065,R067,R104}` |
| Rev.E 候选 | `{R011,R018,R019,R036,R037,R061,R065,R067,R104}` |

变化精确为删除假 R054，无新增规则。DEF-1 的原因分析和处理机制本轮评审通过。

### 3.2 DEF-2：唯一索引 COMMENT 为什么导致整条 DDL“解析崩溃”

#### 3.2.1 触发条件

CREATE TABLE 顶层定义中存在合法 MySQL 形态：

```sql
UNIQUE KEY `uk_biztxlog` (...) COMMENT '唯一索引说明'
```

当前使用的 sqlglot 29/30 MySQL parser 不接受 UNIQUE 索引后的 COMMENT，抛出 `ParseError`；普通 KEY 上的 COMMENT 则可以解析。

#### 3.2.2 发生原因

词法器能够正确识别这段 SQL，但 parser 的 MySQL 语法实现无法把 COMMENT 挂到 UNIQUE 索引节点。因此整个 `CREATE TABLE` 没有 AST，而不是只有这条索引丢失。

当前 `SQLParser.parse()` 的异常分支只做正则表名回退并返回：

```text
is_create_table=True
parse_error!=None
columns=[]
has_primary_key=False
engine=None
charset=None
has_table_comment=False
```

R003/R004/R005/R028 的入口条件只关心“是不是 CREATE TABLE”，随后把空结构当成事实上的“未声明”，所以错误传导链为：

```text
UNIQUE COMMENT
  → sqlglot ParseError
  → 整张表的结构事实全部拿不到
  → 异常回退仍标记 is_create_table=True
  → E999 + R003 + R004 + R005 + R028
  → 把“解析器拿不到”错误解释成“用户没有写”
```

#### 3.2.3 Rev.E 的处理机制

Rev.E 不伪造字段事实，也不在规则层补正则，而是做一次受限的“解析兼容视图”：

1. sqlglot tokenizer 对原 SQL 分词；字符串、反引号标识符和注释都是独立 token；
2. 只在第一条 `CREATE [TEMPORARY] TABLE` 的定义列表深度 1、且定义项首 token 为 UNIQUE 时识别；
3. 跳过完整索引列清单，只在该定义项的索引选项区识别真实 `COMMENT + STRING` token 对；
4. 只把批准 span 等长替换为空格并保留换行，不改变其他字符位置；
5. 用变换后的副本重试 sqlglot；若仍为 TDSQL `Command`，再对定义列表之后的真实方言尾子句做第二阶段 token 剥离；
6. 最终必须同时通过“差异只在两阶段 span 并集、候选是 Create、kind=TABLE、表名相同”门禁；
7. 任一条件失败则保留原异常与 E999；`parsed.raw_sql` 始终保留用户原文，下游规则仍从原文读取分片信息。

这套机制的正确目的不是“修改用户 SQL”，而是只为第三方 parser 构造一个等位的兼容输入，使它恢复列、主键、引擎、字符集、表注释和索引等完整结构。

第五轮现场 fixture 结果为：

| 版本 | `report_6311_biz_tx_log.sql` |
|---|---|
| 当前主干 | `parse_error=True`；`{E999,R003,R004,R005,R028}` |
| Rev.E 候选 | `parse_error=False`；`{R036,R037}` |

目标结果与设计一致。DEF-2 的主链路可以实现用户需求；本轮阻断来自恢复边界仍不完整，而不是目标机制错误。

### 3.3 为什么必须同时删除旧 `_TDSQL_DIALECT_RE`

v1.6.2.0 的旧实现对整条 SQL 做全局正则替换。只要语句含真实 TDSQL 尾子句，列名、DEFAULT、列注释、表注释中的同名文本也可能一起被删除；损坏后的 SQL 又可能成功解析并通过“非 Command”旧门禁，产生静默错误 AST。

Rev.E 改为：先严格定位真实列定义右括号，只扫描它之后的顶层 token，并对首次 `Command` 路径和 UNIQUE COMMENT 异常路径统一使用同一 token 剥离器。这一机制可以消除第四轮确认的列名 `broadcast` 消失、注释被改写、CTAS 被误扫和跨分号改写问题，方向正确。

本轮已按设计中的用户决策处理：该生产缺陷不另出独立热修、不另行知会内网，只要求随 v1.6.2.2 一并解决；本报告不会把它重新列为独立待办。本文的 No-Go 仅表示 Rev.E 作为 v1.6.2.2 施工依据仍需先关闭 BLOCK-E1/E2。

## 4. 第四轮意见关闭情况

| 第四轮意见 | Rev.E 处置 | 第五轮独立验证 | 状态 |
|---|---|---|---|
| BLOCK-D1a：BY/方法为可选 | 改为逐 token 必选，任一缺失返回 None | 缺 BY、缺方法、未知方法、缺左括号均不产生 span | **关闭** |
| BLOCK-D1b：STRING/IDENTIFIER 被当关键字 | `_is_bare_kw()` 排除 STRING/IDENTIFIER | 字符串、反引号关键字诱饵不产生 span；RANGE/LIST 仍可恢复 | **关闭** |
| BLOCK-D1c：表注释同名阻断真实尾部 | 仅裸关键字进入分支 | 两种表注释 + 真实尾部均恢复 | **关闭** |
| BLOCK-D1d：双声明被全部删除 | `seen_decl` 限制只允许一个声明 | HASH+BROADCAST、HASH+RANGE 均 span=0 | **关闭** |
| BLOCK-D2a：CTAS 函数括号冒充定义列表 | 严格解析 CREATE TABLE 头部并要求表名后紧接左括号 | CTAS/LIKE 均不产生 span | **关闭** |
| BLOCK-D2b：跨分号与 Block 接纳 | 发现分号失败关闭；首次门禁补齐 Create/TABLE/同表名 | 多语句不变，Block 不再接纳 | **关闭** |
| MAJOR-D1：依赖 pin 未落定 | 两处声明改为 `sqlglot>=29,<31` | 29.0.0 与 30.14.0 专项均通过 | **关闭** |
| MAJOR-D2：危险旧指令冲突 | 清理“保留/复用旧正则”等指令 | 未再发现会恢复旧正则的施工命令 | **关闭；仍有少量文档残留，见 DOC-E1** |

第四轮问题关闭不等于完整 S-3 已证明。Rev.E 只补了上一轮列出的非法“外壳”，没有验证方法括号内部和 CREATE TABLE 名称 token，这正是本轮两个阻断的来源。

## 5. 问题清单

### 5.1 BLOCK-E1：分片方法参数未建模，平衡括号中的任意内容都会被当作合法尾子句

#### 5.1.1 触发条件

语句满足外层形态：

```text
TDSQL_DISTRIBUTED BY HASH|RANGE|LIST ( ...能配平的任意 token... )
```

但括号内部不是本项目支持的单分片键标识符。例如：

```sql
TDSQL_DISTRIBUTED BY HASH()
TDSQL_DISTRIBUTED BY HASH(,)
TDSQL_DISTRIBUTED BY HASH('id')
TDSQL_DISTRIBUTED BY HASH(id + 1)
TDSQL_DISTRIBUTED BY HASH(lower(id))
```

#### 5.1.2 发生原因

Rev.E 对 `TDSQL_DISTRIBUTED`、`BY`、方法名和左括号做了严格验证，但进入左括号后只有一个深度计数器：

```python
d2 = 0
closed = False
while j < n:
    if toks[j].token_type == TokenType.L_PAREN:
        d2 += 1
    elif toks[j].token_type == TokenType.R_PAREN:
        d2 -= 1
        if d2 == 0:
            closed = True
            break
    j += 1
```

该代码只能证明右括号存在，不能证明括号中：

- 非空；
- 只有一个参数；
- 参数是裸标识符或反引号标识符；
- 没有逗号、字符串、运算符或函数；
- 符合本项目“一个分片键字段”的既有契约。

随后代码把从 `TDSQL_DISTRIBUTED` 到配对右括号的整段加入 span。字符门禁只会证明“确实只改了自己批准的 span”，无法证明“批准对象在语义上合法”。

#### 5.1.3 错误处理链路

首次 `Command` 路径：

```text
非法 HASH(...)
  → sqlglot 降级为 Command
  → 剥离器只见括号配平，批准整个方言 span
  → 去掉该 span 后剩余普通 CREATE TABLE
  → sqlglot 返回 Create
  → Create/TABLE/同表名/字符差异门禁全部通过
  → AST 被接纳，columns 等结构规则开始运行
```

带 UNIQUE COMMENT 的异常路径更严重：

```text
UNIQUE COMMENT + 非法 HASH(...)
  → 初次 ParseError
  → 阶段一删 UNIQUE COMMENT，候选变为 Command
  → 阶段二误删非法 HASH(...)
  → 普通 CREATE TABLE 解析成功
  → 两阶段 span 联合门禁通过
  → 原 E999 消失，非法 SQL 被恢复为 Create
```

#### 5.1.4 独立复现证据

以下结果在 sqlglot 29.0.0、当前环境 30.12.0、30.14.0 三个版本一致：

| 输入尾部 | `_strip_tdsql_dialect_tail` | 最终 AST | `parse_error` |
|---|---:|---|---:|
| `HASH()` | span=1 | `Create` | False |
| `HASH(,)` | span=1 | `Create` | False |
| `HASH('id')` | span=1 | `Create` | False |
| `HASH(id + 1)` | span=1 | `Create` | False |
| `HASH(lower(id))` | span=1 | `Create` | False |

决定性主干前后对比：

```sql
CREATE TABLE t (
  id INT,
  UNIQUE KEY uk (id) COMMENT 'u'
) TDSQL_DISTRIBUTED BY HASH();
```

| 版本 | AST | E999 | 其他可见变化 |
|---|---|---:|---|
| 当前主干 | None | **有** | 走原异常路径 |
| Rev.E 候选 | Create | **无** | 新增 R029/R036/R037 等基于恢复结构的结果 |

因此这不是“Command 本来就没有 E999”的争论：在 Rev.E 新增的两阶段目标路径上，现有明确语法错误被实际吞掉。

#### 5.1.5 为什么现有门禁和测试没有发现

- `_spans_only_diff()` 只验证字符位置，不验证被删片段的语法合法性；
- `Create + TABLE + 同表名` 只验证剥离后的 AST，不验证剥离前的尾子句；
- Y1～Y5 覆盖了缺 BY、缺方法、未知方法、缺左括号，却没有覆盖左括号之后的参数语法；
- 现有 X3 虽然有 `HASH('id')` 的 R077 反例，但只断言 R077/R054，没有断言 parser 不得恢复为成功 AST，也没有与 UNIQUE COMMENT 两阶段链路交叉。

#### 5.1.6 必须修改的处理机制

保留现有外层 token 校验，但把“平衡任意括号体”改为“方法参数语法识别”。按仓库 v1.6.1.9 已冻结的 HASH 契约和单分片键规则，热修至少应只接纳：

```text
L_PAREN + 一个合法裸标识符或反引号标识符 + R_PAREN
```

建议实现为：

1. 方法 token 后必须是 `L_PAREN`；
2. 下一个 token 必须是产品允许的单字段标识符 token；
3. 再下一个 token 必须立即是 `R_PAREN`；
4. 空参数、STRING、逗号、多字段、运算符、函数或嵌套括号一律返回 `(None, [], "")`；
5. HASH/RANGE/LIST 若存在不同的官方参数语法，必须分别建立方法级白名单与证据，不得继续共用“任意平衡内容”；
6. 不要依赖 R077 事后拦截来弥补 parser 的错误恢复：RANGE/LIST 当前并不由 R077 完整识别，而且语法错误职责本就不应转嫁给业务规则。

### 5.2 BLOCK-E2：STRING 被当作合法表名，两道“同表名”门禁会接纳单引号表名

#### 5.2.1 触发条件

CREATE TABLE 表名使用字符串 token，并同时命中任一恢复路径。最小决定性反例：

```sql
CREATE TABLE 't' (
  id INT,
  UNIQUE KEY uk (id) COMMENT 'u'
);
```

或再组合合法/非法 TDSQL 尾子句。

#### 5.2.2 发生原因

Rev.E 在两个独立定位器中都把 STRING 列为合法表名：

```python
_NAMEY = (TokenType.VAR, TokenType.IDENTIFIER, TokenType.STRING)
```

当前 MySQL tokenizer 的独立结果是：

| 表名写法 | token 类型 |
|---|---|
| `t` | VAR |
| `` `t` `` | IDENTIFIER |
| `'t'` | **STRING** |
| `"t"` | **STRING** |

`_same_table_name()` 以及异常路径的内联表名比较又使用：

```python
.strip('`"\' ')
```

也就是主动把单引号去掉后比较。结果不是“STRING 误入后仍会被门禁挡住”，而是“定位器和门禁共同把它正常化成同一个表名”。

#### 5.2.3 错误处理链路与证据

```text
CREATE TABLE 't' + UNIQUE COMMENT
  → 当前主干 ParseError，保留 E999
  → Rev.E 定位器接受 STRING 表名并记录 table_name='t'
  → 删除 UNIQUE COMMENT 后 sqlglot 返回 Create
  → AST 表名同样归一为 t
  → 去引号同表名门禁通过
  → E999 消失
```

独立结果：

| 输入 | 当前主干 | Rev.E 候选 |
|---|---|---|
| 单引号表名 + UNIQUE COMMENT | `ast=None, parse_error=True, E999` | `ast=Create, parse_error=False, 无 E999` |
| 单引号表名 + UNIQUE COMMENT + HASH(id) | 同上 | 同上 |

这同样违反 S-3，并且同时影响 `_strip_unique_index_comments()` 和 `_strip_tdsql_dialect_tail()` 两条路径。

#### 5.2.4 必须修改的处理机制

1. 两处 `_NAMEY` 均删除 `TokenType.STRING`，MySQL 默认方言下仅允许裸标识符 VAR 与反引号标识符 IDENTIFIER；
2. 不要只修改 `_same_table_name()`：STRING 必须在源 SQL 语法定位阶段就失败关闭；
3. 建议把两套 CREATE TABLE 头部定位逻辑合并为一个共享的严格 helper，统一处理：
   - `CREATE [TEMPORARY] TABLE`；
   - 可选 `IF NOT EXISTS`；
   - 裸名或反引号名，可选一层 schema 限定；
   - 表名后立即是定义列表左括号；
   - 返回定义列表起止位置和规范化表名。
4. `_strip_unique_index_comments()` 当前仍采用“从 TABLE 后一直找第一个左括号”的独立逻辑；即使现有垃圾 token 反例最终被 AST 门禁挡住，也应复用严格头部 helper，防止两个安全模型再次漂移；
5. 增加单引号、双引号字符串表名 × 仅 UNIQUE COMMENT、× TDSQL 两阶段组合测试，断言保留原 E999。

### 5.3 DOC-E1：文档仍有少量自相矛盾，需随下一版一并清理

这些残留不会改变上述产品代码结论，但会降低施工与验收可执行性：

| 位置 | 问题 | 修改建议 |
|---|---|---|
| §5.1 标题 | `### 5.1 引擎指纹与解析产物` 连续重复三次 | 保留一次 |
| §3.1 位置说明 | 仍写“紧接 `_TDSQL_DIALECT_RE` 定义之后”，但该常量已删除 | 改为“紧接新 token helper 之后”或给出稳定锚点 |
| §8 风险表 | 仍写 sqlglot pin“需产品决策/建议 pin” | 改为“已决定并纳入改动”，与 §5.0、G-18 一致 |
| C-14 | 仍写 G-1～G-16 十六道门槛 | 改为覆盖 G-1～G-18，并按顺序排列 G-11/G-12/G-13～G-18 |
| Y 组 | 声称覆盖 S-3，但只覆盖外层 token 和语句边界 | 新增方法参数与表名 token 的 Z 组；不要继续用“13 类全部失败关闭”概括未覆盖维度 |

## 6. 安全性质重新判定

| 性质 | Rev.E 声称 | 第五轮判定 |
|---|---|---|
| S-1：首次成功语句不变 | 新逻辑只在异常/Command 恢复路径 | **基本成立** |
| S-2a：全部字符差异只在批准 span | token span + 联合逐字符校验 | **成立**；本轮没有发现越界字符改写 |
| S-2b：span 是明确语法作用域中的完整合法目标 | 严格 CREATE TABLE 头部、必选 token、唯一声明 | **部分成立**；定义体/CTAS/多语句已收口，但方法参数未验证，STRING 表名仍被认可 |
| S-3：无法证明时失败关闭，绝不把非法 DDL 修成合法 | Y 组 13 类反例 | **不成立**；BLOCK-E1/E2 均能使原 E999 消失并接纳 Create |
| S-4：`raw_sql` 保持原文 | 只解析副本 | **成立**，但无法补救错误 AST 的接纳 |

## 7. 独立测试结果

### 7.1 目标功能与现场结果

| 项目 | 结果 |
|---|---|
| gg77 / `report_6309_kcfb_list_info.sql` | 精确删除假 R054，无新增规则 |
| gg78 / `report_6311_biz_tx_log.sql` | 由 E999/R003/R004/R005/R028 恢复为 R036/R037 |
| Rev.E 合法 HASH/RANGE/LIST/BROADCAST | 均恢复为 Create |
| 表 COMMENT 同名诱饵 + 真实尾部 | 注释保留，真实尾部恢复 |
| CTAS / LIKE / 多语句 / 双声明 | 均失败关闭，不产生剥离 span |

### 7.2 依赖与回归

| 项目 | 结果 |
|---|---|
| sqlglot 29.0.0：TDSQL fallback + R077/R054 | **59 passed / 0 failed** |
| sqlglot 30.14.0：同一专项 | **59 passed / 0 failed** |
| 当前环境 30.12.0：fallback + R077/R054 + parser | **73 passed / 0 failed** |
| `pytest -q tests`（Rev.E 临时候选） | **1384 passed / 0 failed**，262.84 秒 |
| `verify_rules.py` | 119 条规则、107 条文件覆盖、0 未覆盖、3 条既有断言失败，与设计基线同名同因 |
| F1 现场规则精确集合 | `{R011,R018,R019,R036,R037,R061,R065,R067,R104}` |
| F2 现场规则精确集合 | `{R036,R037}` |

全量回归证明常规路径没有明显退化，但不能抵消明确反例。现有测试没有覆盖方法参数和 STRING 表名，因此“1384 passed”与 BLOCK-E1/E2 可以同时成立。

### 7.3 新增对抗探针

| 对抗维度 | 结果 |
|---|---|
| 空参数 `HASH()` | 被误批准并恢复为 Create |
| 逗号空参数 `HASH(,)` | 被误批准并恢复为 Create |
| 字符串参数 `HASH('id')` | 被误批准并恢复为 Create |
| 表达式参数 `HASH(id+1)` | 被误批准并恢复为 Create |
| 函数参数 `HASH(lower(id))` | 被误批准并恢复为 Create |
| 上述非法参数 + UNIQUE COMMENT | 原 E999 消失 |
| 单引号表名 + UNIQUE COMMENT | 原 E999 消失 |
| 单引号表名 + UNIQUE COMMENT + TDSQL 尾部 | 原 E999 消失 |

## 8. 下一版准入条件

- [ ] HASH/RANGE/LIST 参数不再只做括号配平，必须按支持语法验证完整参数；
- [ ] 空参数、逗号、多字段、STRING、表达式、函数/嵌套括号在无权威支持证据时全部失败关闭；
- [ ] 两处表名白名单删除 `TokenType.STRING`；
- [ ] UNIQUE COMMENT 与 TDSQL 两个剥离器复用同一个严格 CREATE TABLE 头部/定义列表定位器；
- [ ] 单引号表名在源定位阶段直接拒绝，不依赖候选 AST 表名比较；
- [ ] 新增 Z 组：参数语法 × 首次 Command / UNIQUE COMMENT 两阶段路径，逐例断言 span=0 且原 Command/E999 保持；
- [ ] 新增表名 token 组：单引号、双引号字符串表名 × 两条恢复路径，逐例断言 E999 保持；
- [ ] 合法裸字段、反引号字段、schema-qualified 表名、IF NOT EXISTS、TEMPORARY、HASH/RANGE/LIST/BROADCAST 全部继续通过；
- [ ] 两份现场 fixture 继续使用原始文本、原实例类型和规则集合精确相等断言；
- [ ] sqlglot 29.0.0 与发布 wheel 两端均运行新增反例；
- [ ] 全量回归、119 规则物料校验、生产回放和全语料漂移继续满足既定门槛；
- [ ] 清理 §5.1 重复标题、已删除正则位置说明、pin 风险状态和 C-14 编号残留。

## 9. 最终评审意见

Rev.E 已经正确关闭第四轮的外层关键字、CTAS、多语句、双声明、候选 AST 类型和依赖 pin 问题；DEF-1 的 `kind` 修复与 DEF-2 的 tokenizer 受限重试也都能实现两份现场报告的目标结果。A 不需要推翻方案，更不需要更换用户已经决定的 sqlglot 词法器。

剩余问题发生在“批准删除的语法是否完整合法”这一层：Rev.E 目前验证了方言子句的壳，却没有验证括号里的分片键；同时把 STRING 当作合法表名。两者都能在 UNIQUE COMMENT 两阶段恢复中吞掉原有 E999。SQL 审核系统不能以恢复更多结构为理由，把非法 DDL 变成成功 AST；这会使审核结果建立在一个数据库本身不会执行的结构上。

**最终结论：No-Go。**

建议 Rev.F 只做可达域最小的两处收口：严格验证单分片键参数、统一并收紧 CREATE TABLE 头部定位；补齐反例后再进入第六轮复审。
