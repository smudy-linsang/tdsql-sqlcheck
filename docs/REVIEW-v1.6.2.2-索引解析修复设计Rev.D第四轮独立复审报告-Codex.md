# v1.6.2.2 索引解析修复设计 Rev.D 第四轮独立复审报告

| 项目 | 内容 |
|---|---|
| 复审对象 | `DESIGN-v1.6.2.2-索引类型误判与唯一索引注释解析崩溃修复详细设计说明书.md` Rev.D |
| 被审提交 | `3d643cc7a0e9fb02a2c320d0b3c21cb530dbf199` |
| 复审日期 | 2026-08-25 |
| 复审人 | Codex（独立复审） |
| 复审方式 | 文档逐行核验、按代码块在 detached worktree 临时施工、字段级交叉矩阵、非法语法/CTAS/多语句对抗探针、sqlglot 七版本矩阵、生产 fixture 对照、专项与全量回归 |
| 最终结论 | **No-Go** |

## 1. 结论摘要

Rev.D 的核心整改方向正确，并且确有实效：

1. 删除 `_TDSQL_DIALECT_RE` 全局正则，避免继续在整条 SQL 上无差别替换；
2. 新旧两条 TDSQL 恢复入口统一调用 token 级剥离器；
3. UNIQUE COMMENT 与 TDSQL 两阶段改写纳入 span 联合校验；
4. 上一轮三个静默损坏反例已经消除；
5. 4 种方言尾子句 × 5 类诱饵 × 带/不带 UNIQUE COMMENT 共 40 个字段级交叉场景，独立复测为 **40/40 通过**；
6. A 给出的 sqlglot 版本下界判断成立：26/27/28 的 T5 失败，29.0.0 起通过。

但是，新的 `_strip_tdsql_dialect_tail()` 仍没有达到文档声称的“真实关键字 token、明确语法作用域、无法证明时失败关闭”。第四轮发现：

- **BLOCK-D1**：`BY` 和 `HASH/RANGE/LIST` 被写成可选判断，token 类型也未严格约束。三类缺少必选关键字的非法 DDL 会被修剪成合法 `CREATE TABLE`；字符串或反引号标识符也可能被当作真实方言关键字。合法表注释恰好为 `'TDSQL_DISTRIBUTED'` 时，反而会阻断真实尾子句恢复。
- **BLOCK-D2**：定义列表定位器只是寻找 `TABLE` 后任意第一个左括号，会把 CTAS 中函数调用的括号误认成建表定义列表；扫描也不在第一条语句分号处停止。实测可删除 CTAS 查询中的 `broadcast` 列，并跨到第二条建表语句继续修改；首次重试又会接纳任意“非 Command”节点，包括 `exp.Block`。
- **MAJOR-D1**：sqlglot 下界分析已完成，但依赖声明仍为 `>=26`，方案明确写着“待用户拍板”，所以 MAJOR-C1 只是分析清楚，尚未工程闭环。
- **MAJOR-D2**：施工清单和附录仍保留“旧正则一字不动、继续复用旧正则、只做 52 例”等与 Rev.D 正文直接冲突的指令。该文档定位为“逐字照图施工”，这些不是普通笔误，可能直接让施工智能体恢复已删除的不安全实现。

因此 Rev.D 暂不能进入施工。上一轮 BLOCK-C1 的目标场景已经关闭，但完整恢复链的 S-2/S-3 安全证明仍不成立。

## 2. 复审方法与边界

本轮未修改或提交项目产品代码。为验证设计可执行性，我在 detached worktree 中按 Rev.D 代码块临时完成：

- 删除 `_TDSQL_DIALECT_RE`；
- 加入 `_spans_only_diff()`、`_tdsql_table_def_end()`、`_strip_tdsql_dialect_tail()`；
- 沿用 Rev.C `_strip_unique_index_comments()`；
- 改造首次 `Command` 重试与异常恢复入口；
- 落地 DEF-1 `kind` 白名单。

临时实现通过语法编译和导入自检，使用仓库真实 `SQLParser`、`RuleChecker` 与现有 119 条规则执行测试。所有临时代码仅用于验证，不进入主分支。

## 3. 第三轮意见关闭情况

| 第三轮意见 | Rev.D 处置 | 第四轮独立验证 | 状态 |
|---|---|---|---|
| BLOCK-C1：全局正则静默删除列、篡改注释 | 删除旧正则；新旧入口统一 token 剥离；联合 span 门禁 | 上轮三个反例均恢复正确；X 组 40/40 通过 | **目标反例关闭；但剥离器仍有 BLOCK-D1/D2，完整安全性质未关闭** |
| MAJOR-C1：`>=26` 与 T5 不兼容 | 二分版本下界；建议 `>=29,<31` | 26/27/28 失败，29.0/30.0/30.12/30.14 通过，结论成立 | **分析关闭，依赖变更仍待决** |
| DOC-C1：Rev.B 残留文字 | 正文多数已更新 | 施工清单、门槛和附录仍有多处相互矛盾的旧指令 | **未关闭** |

## 4. 问题清单

### 4.1 BLOCK-D1：方言识别不是严格语法匹配，会吞掉非法 DDL 或阻断合法 DDL

#### 4.1.1 涉及代码块

设计 §3.0c 约第 454～478 行：

```python
if depth == 0 and tx == "TDSQL_DISTRIBUTED":
    j = i + 1
    if j < n and ... == "BY":
        j += 1
    if j < n and ... in _TDSQL_SHARD_METHODS:
        j += 1
    if j < n and toks[j].token_type == TokenType.L_PAREN:
        ...
```

两个连续的 `if` 只在 token 存在时前移，并未在 token 缺失或错误时返回失败。因此 `BY` 和分片方法实际上是可选的。

同时：

- 判断 `TDSQL_DISTRIBUTED` 时只比较 `token.text`，不检查 token 类型；
- 判断 `BROADCAST` 时允许 `TokenType.IDENTIFIER`，会把反引号标识符当成关键字；
- 允许同一语句记录多个分布 span，没有拒绝 HASH+BROADCAST 或 HASH+RANGE 的互斥冲突。

这与文档“必须是真实关键字 token，不是字符串/注释/标识符内容”及 S-3“形态不完整即失败关闭”直接矛盾。

#### 4.1.2 非法语法被修剪成成功 AST

以下三条均缺少 TDSQL 合规语法的必选组成：

```sql
CREATE TABLE t (...) ENGINE=InnoDB TDSQL_DISTRIBUTED (sk);
CREATE TABLE t (...) ENGINE=InnoDB TDSQL_DISTRIBUTED BY (sk);
CREATE TABLE t (...) ENGINE=InnoDB TDSQL_DISTRIBUTED HASH(sk);
```

Rev.D 对三条都返回 1 个批准 span，剥离后得到普通 `CREATE TABLE`。真实解析结果均为：

```text
parse_error=False
ast=Create
columns=['id', 'sk']
```

给同样的三条非法语法增加 `UNIQUE KEY uk(sk) COMMENT 'u'` 后，异常恢复入口也会先剥离索引 COMMENT，再剥离错误方言片段，最终同样接纳为成功 `Create`。这证明两条入口都受影响。

此外：

```sql
... TDSQL_DISTRIBUTED BY HASH(sk) BROADCAST;
... TDSQL_DISTRIBUTED BY HASH(sk) TDSQL_DISTRIBUTED BY RANGE(sk);
```

分别产生 2 个 span，冲突声明全部被删除后也会成功解析。当前实现没有证明这些组合合法，不应猜测性接纳。

#### 4.1.3 字符串和标识符并非“天然不可见”

以下非法尾部诱饵均被当成真实方言语法删除，随后普通建表成功：

```sql
... ENGINE=InnoDB 'TDSQL_DISTRIBUTED' BY HASH(sk);
... ENGINE=InnoDB `TDSQL_DISTRIBUTED` BY HASH(sk);
... ENGINE=InnoDB `broadcast`;
```

三条均得到 `spans=1`、`ast=Create`、`parse_error=False`。tokenizer 正确区分了 STRING/IDENTIFIER，但代码在判断时没有使用这项信息。

更重要的是，下面是**合法表注释 + 合法 TDSQL 尾子句**：

```sql
CREATE TABLE t (
  id BIGINT,
  sk BIGINT,
  PRIMARY KEY(id, sk)
) ENGINE=InnoDB
  COMMENT='TDSQL_DISTRIBUTED'
  TDSQL_DISTRIBUTED BY HASH(sk);
```

扫描器先遇到 STRING token `TDSQL_DISTRIBUTED`，将其误判为方言关键字；因其后不是 `BY`，整个剥离器返回 `None`，真实尾子句永远得不到处理：

| 场景 | 实际结果 |
|---|---|
| 无 UNIQUE COMMENT | `ast=Command`，`columns=[]`，结构规则继续漏审 |
| 带 UNIQUE COMMENT | `parse_error=True`，`ast=None`，重新出现 E999 和结构类集体误报 |

X 组只覆盖了定义列表内部的列注释，没有覆盖定义列表闭合后的表 COMMENT，因此没有发现这一合法反例。

#### 4.1.4 必须修改

1. `TDSQL_DISTRIBUTED`、`BY`、`HASH/RANGE/LIST` 均必须验证为预期的裸关键字 token；当前 sqlglot 实测均为 `TokenType.VAR`。STRING、IDENTIFIER 等一律不得进入关键字分支。
2. 将三个组成改为顺序必选校验，任一缺失立即返回 `(None, [])`：

   ```python
   if token_i 不是裸 TDSQL_DISTRIBUTED: 不进入该分支
   if token_i+1 不是裸 BY: return None, []
   if token_i+2 不是裸 HASH/RANGE/LIST: return None, []
   if token_i+3 不是 L_PAREN: return None, []
   ```

3. `BROADCAST` 只接受裸关键字 token，不得接受反引号 `IDENTIFIER`。
4. 一张表只能批准一种分布声明；发现第二个 TDSQL/BROADCAST 声明或 HASH 与 BROADCAST 并存时失败关闭。若官方语法明确允许某种组合，应把允许关系显式建模并增加正反用例，不能默认全部删除。
5. X 组增加表 COMMENT、表选项字符串、反引号标识符、缺 BY、缺方法、缺括号、未知方法、双声明等反例；断言非法输入保持原 Command/E999 路径，合法表注释逐字保留且结构恢复。

### 4.2 BLOCK-D2：定义列表和语句边界定位错误，可破坏 CTAS 并跨语句改写

#### 4.2.1 CTAS 函数括号被误认成建表定义列表

`_tdsql_table_def_end()` 在确认 `CREATE [TEMPORARY] TABLE` 后，从当前位置一直寻找任意第一个 `L_PAREN`。它没有验证该左括号是否紧跟表名、是否真的是列定义列表。

CTAS 是仓库既有产品域：`tests/test_r077_r054_tdsql_syntax.py` 的 C7 和 `tests/test_sit_v1_rules.py` 均有明确用例。下面的 CTAS 中，第一个左括号来自 `CONCAT()`：

```sql
CREATE TABLE t AS
SELECT CONCAT('a', 'b') AS c, broadcast
FROM src
TDSQL_DISTRIBUTED BY HASH(c);
```

Rev.D 将 `CONCAT(...)` 的右括号当成“建表定义结束”，从那里开始扫描，随后同时删除 SELECT 列 `broadcast` 和真实 TDSQL 尾子句：

```sql
CREATE TABLE t AS
SELECT CONCAT('a', 'b') AS c,           FROM src;
```

损坏结果仍解析为 `exp.Create` 并被首次重试接纳。这再次形成“看似成功、实际语义被改写”的 AST。

#### 4.2.2 扫描越过第一条语句

剥离器没有处理分号，会从第一张表定义结束一直扫描到 token 流末尾：

```sql
CREATE TABLE t (...) TDSQL_DISTRIBUTED BY HASH(sk);
CREATE TABLE u (x INT) BROADCAST;
```

直接调用剥离器得到 `spans=2`，两条语句的方言尾部都被修改。剥离后 `sqlglot.parse_one()` 返回 `exp.Block`，而首次重试只判断“不是 `exp.Command`”就接纳，因此 `SQLParser.parse()` 最终持有 `Block`、`parse_error=False`、`columns=[]`。

这与 Rev.C 已经确立的“只处理第一条 CREATE TABLE、第一条语句结束即停止”的安全边界相冲突。

#### 4.2.3 必须修改

1. `TABLE` 后只允许：可选 `IF NOT EXISTS`、合法的单段或 schema-qualified 表名，然后**紧接**列定义 `L_PAREN`；遇到 `AS`、`LIKE`、其他 token 或语句结束符时返回 -1。不得搜索后续任意括号。
2. CTAS/LIKE 若本次不支持 TDSQL 方言恢复，应明确失败关闭并增加产品边界用例；若要支持，必须单独建模，不能复用列定义括号定位逻辑。
3. token 流出现第一条内部 `SEMICOLON` 时返回 `(None, [])`，不允许跨语句改写。调用方已有 SQL 拆分能力，解析器无需猜测多语句含义。
4. 首次重试不得以“非 `Command`”作为唯一接纳条件。至少要求：候选为 `exp.Create`、`kind=='TABLE'`、表名与原文一致；`exp.Block`、其他表达式一律拒绝。
5. 增加 CTAS（无函数/有函数）、CREATE TABLE LIKE、多语句、第二语句含 BROADCAST/TDSQL、候选为 Block 的失败关闭测试。

### 4.3 MAJOR-D1：sqlglot 版本结论正确，但依赖 pin 尚未落定

本轮独立安装并运行同一 T5 与 X 组探针：

| sqlglot | T5：HASH + 二级 PARTITION + UNIQUE COMMENT | X 组 40 例 |
|---|---|---|
| 26.0.0 | 失败 | 40/40 通过 |
| 27.0.0 | 失败 | 40/40 通过 |
| 28.0.0 | 失败 | 40/40 通过 |
| 29.0.0 | 通过 | 40/40 通过 |
| 30.0.0 | 通过 | 40/40 通过 |
| 30.12.0 | 通过 | 40/40 通过 |
| 30.14.0 | 通过 | 40/40 通过 |

因此 A 的“29.0.0 是实测下界”成立，建议 `>=29,<31` 也有明确技术依据。但当前仓库仍是：

```text
requirements.txt: sqlglot>=26.0.0
pyproject.toml:    sqlglot>=26.0
```

文档又明确写“待用户拍板”，故 MAJOR-C1 尚未关闭。我的建议是：

- 项目声明采用 `sqlglot>=29,<31`；
- 内网发布包继续只携带一个经过完整验收的确定 wheel，并记录准确版本与哈希；
- CI 覆盖最低允许版本 29.0.0 和本次发布 wheel 版本；
- `requirements.txt`、`pyproject.toml`、离线 wheels/发布说明必须一致。

若用户决定精确 pin，也可采用本次全量验证版本；无论选择哪条，不能继续保留 `>=26` 后宣称 T5 已解决。

### 4.4 MAJOR-D2：施工指令内部冲突，可能让 Q 恢复旧缺陷

Rev.D 正文要求删除旧正则，但以下旧指令仍存在：

| 位置 | 冲突内容 |
|---|---|
| §3.2 门禁表约第 795 行 | 仍要求命中并复用 `_TDSQL_DIALECT_RE` |
| 施工清单 C-10 | 要求 `_TDSQL_DIALECT_RE` 及旧重试块“一字未动” |
| 施工清单 C-11 | 仍写 A～F+T+N 共 52 例，遗漏 X 组且与 90 例冲突 |
| G-13 | 仍写 T 组 10 例，正文已撤销 T7/T8、实际为 8 例 |
| 附录 B 第 3 条 | 再次要求“复用同一条 `_TDSQL_DIALECT_RE`” |
| §9 C-1/C-2、§8 回滚 | 仍写只改 1 个产品文件、4 个改动点，与 5 个解析改动点及待 pin 依赖不一致 |
| §5.1 标题 | `### 5.1 引擎指纹与解析产物` 重复两次 |
| 附录 B 标题 | 写“六句话”，实际列出 7 条 |

其中 C-10 和附录 B 第 3 条会直接指导施工智能体保留已经确认危险的全局正则，不能作为普通排版问题放过。必须全局搜索并删除 Rev.C 的旧施工指令，确保正文、代码块、测试矩阵、检查单和附录只有一套口径。

## 5. 独立测试结果

### 5.1 目标修复与生产 fixture

| 项目 | 结果 |
|---|---|
| Rev.D X 组独立重建：4 尾子句 × 5 诱饵 × 两入口 | **40/40 通过** |
| 上轮列名 `broadcast` 静默删除反例 | 已修复 |
| 上轮列注释含 `broadcast` 篡改反例 | 已修复 |
| 上轮列注释含伪 TDSQL 清空反例 | 已修复 |
| 仓库 10 份 `report_*.sql` 候选/基线对照 | 恰好 2 份变化，均为 gg77/gg78 目标 fixture |
| gg77 候选规则集合 | `{R011,R018,R019,R036,R037,R061,R065,R067,R104}`，精确符合设计 |
| gg78 候选规则集合 | `{R036,R037}`，精确符合设计 |

### 5.2 回归

| 项目 | 结果 |
|---|---|
| TDSQL fallback 14 + R077/R054 45 + R061 12 | **71 passed / 0 failed** |
| `pytest tests -q`（sqlglot 30.12.0，Rev.D 临时代码） | **1384 passed / 0 failed**，264.40 秒 |
| `verify_rules.py` | 119 条规则、107 条文件覆盖、0 未覆盖、3 条既知断言失败，与基线同名同因 |

全量回归通过说明常规输入未出现明显退化，但现有测试没有覆盖 BLOCK-D1/D2。非法语法被吞和 CTAS 语义损坏均能在全量全绿的同时存在。

## 6. 安全性质重新判定

| 性质 | Rev.D 声称 | 第四轮判定 |
|---|---|---|
| S-1：首次成功语句不变 | 新逻辑只在恢复路径 | **基本成立** |
| S-2a：整条链路差异只在 token span | 两阶段联合 span | **字符位置成立，语义批准不成立**；STRING/IDENTIFIER 和非法方言也会被错误批准成 span |
| S-2b：明确语法作用域 | 第一条 CREATE TABLE 顶层 | **UNIQUE COMMENT 阶段成立；TDSQL 阶段不成立**，可误入 CTAS SELECT、跨越分号 |
| S-3：无法证明时失败关闭 | 形态不完整返回 None | **不成立**；缺 BY/缺方法、双声明、错误 token 均可被接纳 |
| S-4：raw_sql 原文保持 | 副本解析 | **成立但不能修复错误 AST** |

## 7. 下一版准入条件

- [ ] 严格验证裸 `TDSQL_DISTRIBUTED BY HASH|RANGE|LIST (...)` 的每个必选 token；
- [ ] STRING、IDENTIFIER、注释 token 不得进入方言关键字分支；
- [ ] BROADCAST 只接受真实裸关键字；双分布/冲突声明失败关闭；
- [ ] 表 COMMENT 恰为 `TDSQL_DISTRIBUTED`/`BROADCAST` 时仍能正确处理后续真实尾子句；
- [ ] 列定义左括号必须紧跟合法表名，CTAS/LIKE 不得用任意后续括号冒充定义列表；
- [ ] 剥离器不得越过第一条语句分号；
- [ ] 首次重试接纳门禁至少包含 Create、TABLE、同表名，不得接纳 Block；
- [ ] 新增非法语法、表选项、CTAS、LIKE、多语句、Block 候选反例；
- [ ] 明确并落地 sqlglot pin，最低允许版本和发布 wheel 进入 CI；
- [ ] 清除 C-10、C-11、G-13、附录 B 等所有旧正则/旧数量冲突；
- [ ] X 组、目标 fixture、71 项专项、全量回归继续全绿且无新增漂移。

## 8. 最终评审意见

Rev.D 已经把上一轮最危险的“定义体被全局正则静默破坏”问题从根上移除，40 个目标交叉场景全部通过，这一进步是成立的；A 对 sqlglot 29.0.0 下界的分析也经得起独立复测。

但新剥离器目前仍是“基于 token 扫描”，还不是“按完整语法和作用域严格识别”。它会把缺少必选关键字的非法 DDL 修成成功 AST，也会把 CTAS 查询中的函数括号当作建表定义边界，并允许跨分号修改第二条语句。对 SQL 审核系统而言，“把错误语句修成成功”以及“静默改变 CTAS 查询语义”均属于发布阻断。

**最终结论：No-Go。**

建议 A 保留 Rev.D 的总体架构，不需要推翻 tokenizer 方案；下一版只需把方言剥离器从“宽松 token 搜索”收紧为“单语句、真列定义列表、严格必选 token、唯一分布声明”，并清理文档旧指令、完成依赖 pin。完成后再进入第五轮复审。
