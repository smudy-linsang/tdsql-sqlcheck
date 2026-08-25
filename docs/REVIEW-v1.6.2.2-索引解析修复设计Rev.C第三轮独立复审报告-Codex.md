# v1.6.2.2 索引解析修复设计 Rev.C 第三轮独立复审报告

| 项目 | 内容 |
|---|---|
| 复审对象 | `DESIGN-v1.6.2.2-索引类型误判与唯一索引注释解析崩溃修复详细设计说明书.md` Rev.C |
| 被审提交 | `65ad301a149ba3aa267be67f29edcad130a1cb07` |
| 复审日期 | 2026-08-25 |
| 复审人 | Codex（独立复审） |
| 复审方式 | 文档逐项核验、按文档代码块在 detached worktree 临时施工、生产附件回放、定向/全量回归、sqlglot 版本矩阵、对抗性组合探针 |
| 最终结论 | **No-Go** |

## 1. 结论摘要

Rev.C 对第二轮指出的四项问题做了实质整改：`UNIQUE ... COMMENT` 与常见 TDSQL 尾子句已经能够串联恢复；`at_def_start` 和定义列表闭合停止扫描解决了作用域越界；`CREATE TEMPORARY TABLE` 已进入恢复域；两个生产 fixture 的说明性文件头也已清理，精确规则集合符合预期。这些整改均有独立实测支撑，不予否认。

但第三轮在组合链路中发现 **1 项发布阻断（BLOCK-C1）**：Rev.C 只证明了第一阶段“唯一索引 COMMENT 剥离”的字符改写安全，第二阶段仍对整条 SQL 执行不感知 token 作用域的 `_TDSQL_DIALECT_RE.sub()`。只要语句同时含真实 TDSQL 尾子句，该正则就会被激活，并会一并删除列名 `broadcast`、篡改列注释中的 `broadcast`、清空字符串中的伪 `TDSQL_DISTRIBUTED ...`。更严重的是，损坏后的 SQL 仍可能成功解析并通过四道接纳门禁，形成**静默错误 AST**，下游规则继续基于错误结构审核。

这直接推翻了文档对 S-2a、S-3、T7、T8 和“字符串字面量风险已消除”的承重性论证。该问题不能作为“v1.6.2.0 既有问题”豁免：Rev.C 正在把原本进入 E999 的 `UNIQUE COMMENT + TDSQL` 语句接入同一条不安全转换链，扩大了错误 AST 的可达域。

另发现 **1 项 MAJOR（MAJOR-C1）**：依赖声明允许 `sqlglot==26.0.0`，但 Rev.C 的 T5（HASH + 二级 PARTITION + UNIQUE COMMENT）在 26.0.0 下仍为 E999、`columns=[]`；30.12.0 和 30.14.0 才能恢复。文档的多版本验证只覆盖 DEF-1 AST 契约，不能证明 DEF-2/TDSQL 组合在声明的整个依赖范围内成立。

因此，本版设计暂不具备进入开发/发布的安全条件。BLOCK-C1 必须修订方案并补齐交叉用例；MAJOR-C1 必须通过锁定经验证版本或实现低版本兼容二选一闭环。

## 2. 复审范围与方法

本轮没有修改或提交项目产品代码。为验证设计可执行性，我在 detached worktree 中按 Rev.C §3.1～§3.3 的代码块临时落地了以下内容：

1. 引入 `TokenType` 并实现 `_strip_unique_index_comments()`；
2. 在首次解析异常分支增加 UNIQUE COMMENT 恢复及 TDSQL 二次恢复；
3. 将 `IndexColumnConstraint` 类型判断改为 `kind` 白名单；
4. 使用真实 `SQLParser`、`RuleChecker` 和 119 条规则执行生产回放与对抗探针。

验证覆盖：

- 用户提供的 gg77、gg78 HTML 报告与仓库 fixture 一致性；
- Rev.C 的 HASH、RANGE、LIST、BROADCAST、`shardkey=`、TEMPORARY、作用域负例；
- 列名、列注释和字符串中含 `broadcast` / 伪 TDSQL 片段时的交叉组合；
- `sqlglot` 26.0.0、30.12.0、30.14.0；
- 现有 TDSQL/R054/Oracle 兼容定向测试；
- `tests/` 全量回归和 `verify_rules.py` 审核物料校验。

## 3. 第二轮意见整改核验

| 第二轮问题 | Rev.C 处置 | 第三轮独立结果 | 状态 |
|---|---|---|---|
| BLOCK-B1：UNIQUE COMMENT 未与 TDSQL 方言恢复组合 | 候选仍为 `Command` 时增加 TDSQL 二次恢复 | 30.12.0 下 HASH/RANGE/LIST/BROADCAST 均可恢复；TEMPORARY + HASH 也可恢复 | **功能目标已实现，但组合安全性被 BLOCK-C1 阻断** |
| BLOCK-B2：未限制第一张表顶层定义项起点 | `at_def_start`；定义列表深度归零即停止 | CONSTRAINT、列内联、定义项中部、第二条语句、表定义闭合后诱饵均未被批准；仅真实目标产生 span | **关闭** |
| MAJOR-B1：遗漏 TEMPORARY | 支持 `CREATE [TEMPORARY] TABLE` | 集中式与分布式 TEMPORARY 均恢复；R024/R032 保持可见 | **关闭** |
| MAJOR-B2：产品边界与 fixture 断言不严 | 补充 `USING BTREE` 边界；移除说明性文件头；精确集合断言 | 两份 fixture 与报告 SQL 按 LF/CRLF 归一化后逐字符一致；规则集合精确相等 | **关闭** |

Rev.C 的 `_strip_unique_index_comments()` 本身比 Rev.B 明显收敛。本轮没有发现该函数批准作用域越界的新反例。发布阻断发生在它返回之后的第二阶段全局 TDSQL 替换，而不是 sqlglot tokenizer 的选型问题。

## 4. 问题清单

### 4.1 BLOCK-C1：第二阶段全局 TDSQL 正则可静默破坏 AST

#### 4.1.1 涉及位置

- 设计 §2.2 S-2a/S-3：约第 275～278 行；
- 设计 §3.2 二次恢复：约第 510～523 行；
- 设计 §5.9 T7/T8：约第 759～777 行；
- 设计 §7.1 T 组断言：约第 889～895 行；
- 设计 §8 风险结论：约第 959～967 行；
- 现有实现 `_TDSQL_DIALECT_RE`：`backend/engine/parser/parser_legacy.py` 第 24～27 行；
- 现有首次解析 `Command` 重试：同文件第 135～138 行。

#### 4.1.2 根因

Rev.C 的差异门禁只检查：

```text
原 SQL -> _strip_unique_index_comments() -> _new_sql
```

它能够证明这一步的差异只落在被批准的 UNIQUE 索引 COMMENT span 内。但在 `_cand` 为 `Command` 后，又执行了：

```python
_TDSQL_DIALECT_RE.sub(" ", _new_sql)
```

该转换没有返回 span，没有参与“长度恒等 + 差异仅在批准 span”校验，也不区分：

- 建表定义体与 TDSQL 顶层尾子句；
- 标识符与关键字；
- 列/表 COMMENT、DEFAULT 字符串与真实语法；
- 真实 `BROADCAST` 尾标志与名为 `broadcast` 的列。

“只在 `_cand` 已降级为 `Command` 时执行”不是安全证明。含真实 TDSQL 尾子句的合法语句本来就会降级为 `Command`；真实尾子句负责打开门，SQL 其他位置的同名文本随后被全局正则一并清除。

候选仍可能是同表名的 `exp.Create(kind='TABLE')`，所以现有四道门禁无法发现列集合或注释内容已被破坏。`parsed.raw_sql` 保留原文也不能修复此问题，因为大量规则和页面展示使用的是结构化 `columns`、`indexes`、`column_comments` 等字段。

#### 4.1.3 可复现证据

在 30.12.0 下按 Rev.C 代码块施工后，以下输入均含一个真实 UNIQUE 索引 COMMENT 和一个真实 HASH 尾子句：

**反例一：真实列名被删除**

```sql
CREATE TABLE t (
  id BIGINT,
  sk BIGINT,
  broadcast VARCHAR(20),
  PRIMARY KEY(id, sk),
  UNIQUE KEY uk(sk) COMMENT 'x'
) ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(sk);
```

预期列名：`['id', 'sk', 'broadcast']`。

实际二次转换片段：

```text
..., sk BIGINT,   VARCHAR(20), ... ENGINE=InnoDB
```

实际解析结果：`parse_error=False`，`ast=Create`，但列名仅为 `['id', 'sk']`。这是已被接纳的错误 AST，不是失败关闭。

**反例二：列注释被篡改**

```sql
note VARCHAR(255) COMMENT 'broadcast table info'
```

实际 `column_comments['note']` 从 `broadcast table info` 变为两个空格开头的 `table info`。

**反例三：伪 TDSQL 文本被清空**

```sql
note VARCHAR(255) COMMENT 'TDSQL_DISTRIBUTED BY HASH(fake)'
```

实际 `column_comments['note']` 变为单个空格。解析仍成功，错误 AST 被接纳。

上述三个反例在 `sqlglot` 26.0.0、30.12.0、30.14.0 上均可复现，说明它不是某个 sqlglot 小版本的偶发现象。

#### 4.1.4 为什么 Rev.C 的 T7/T8 没有挡住

设计要求 T 组规则集合与“同表去掉 UNIQUE 索引 COMMENT”完全相等。这个对照对于 T1～T6 可证明本次 COMMENT 恢复没有额外改变规则口径，但不能证明 TDSQL 预处理本身正确：去掉 UNIQUE COMMENT 的对照语句仍会进入 v1.6.2.0 同一条全局正则，并遭受完全相同的列名/注释破坏。两个错误结果相等，是**同源错误对照**，不能作为安全 oracle。

仓库既有 `test_parser_tdsql_dialect_fallback.py` 的列名 `broadcast`、注释伪 TDSQL 负例没有同时携带真实 TDSQL 尾子句，因此首次解析可成功，不会打开正则重试路径，也无法覆盖本反例。

文档中“T7 列仍在”“T8 注释原样保留”的结论只有在没有真实尾子句，或使用不触发该正则的 `shardkey=` 对照时才成立；它不能支撑 Rev.C 新增的 HASH/RANGE/LIST/BROADCAST 组合链路。

#### 4.1.5 必须实施的修改意见

1. **撤销 NG-4 的绝对约束。** 不能继续要求“不动 `_TDSQL_DIALECT_RE` 及既有重试块一字”。既有全局替换已经被证实不安全，且 Rev.C 正在扩大其可达域。
2. **使用 sqlglot tokenizer 实现受限的 TDSQL 尾子句剥离器。** 只允许在第一条 `CREATE [TEMPORARY] TABLE` 定义列表闭合之后、顶层、非字符串/注释/标识符 token 中识别并剥离真实 `TDSQL_DISTRIBUTED BY HASH|RANGE|LIST(...)` 或终端 `BROADCAST`。本意见不反对 tokenizer 方案；恰恰要求把同一安全模型贯彻到第二阶段。
3. **第二阶段也必须返回精确 span。** 整个转换链的所有字符差异必须落在“唯一索引 COMMENT span ∪ TDSQL 顶层尾子句 span”内；保持长度和换行不变。不能只校验第一阶段。
4. **现有首次解析 `Command` 重试也要复用安全剥离器。** 只修 Rev.C 的 except 分支会留下“无 UNIQUE COMMENT 时仍静默损坏”的同源问题，T7/T8 仍无法成为可靠回归。
5. **增加真正独立的结构 oracle。** 对 HASH/RANGE/LIST/BROADCAST 分别与以下诱饵做交叉：列名/反引号列名为 `broadcast`、索引名或索引列含 `broadcast`、列注释/表注释/DEFAULT 含 `broadcast`、含伪 TDSQL 片段、行注释和块注释。必须精确断言列名顺序、列注释、默认值、主键、索引名/索引列、表注释和 `raw_sql`；不能只比较两个都经过同一不安全预处理的规则集合。
6. **保留失败关闭。** 若无法在 token 层唯一确定真实顶层尾子句，必须沿用原 `Command`/E999，不得用猜测性全局替换制造可接纳 AST。

BLOCK-C1 关闭标准：上述三类反例在全部支持版本中字段逐项保持；新旧恢复入口统一使用安全剥离器；定向、生产、全语料和全量测试全部通过。

### 4.2 MAJOR-C1：声明支持 sqlglot 26.0.0，但 T5 在该版本不成立

#### 4.2.1 证据

设计 §5.0 明确记录：

- `requirements.txt`：`sqlglot>=26.0.0`；
- `pyproject.toml`：`sqlglot>=26.0`；
- 无上限；
- 多版本验证只覆盖 DEF-1 AST 结论。

将 Rev.C T5（`UNIQUE ... COMMENT` + `TDSQL_DISTRIBUTED BY HASH(sk)` + 二级 `PARTITION BY RANGE`）分别运行：

| sqlglot | 结果 |
|---|---|
| 26.0.0 | `parse_error=True`，`ast=None`，`columns=[]`，命中 E999/R003/R004/R005/R028 |
| 30.12.0 | `parse_error=False`，`ast=Create`，列和注释恢复 |
| 30.14.0 | `parse_error=False`，`ast=Create`，列和注释恢复 |

同仓库既有 `test_parser_tdsql_dialect_fallback.py::test_d5_hash_plus_partition` 在 26.0.0 下也失败，在 30.14.0 下通过。由此可见，这是既有 TDSQL fallback 的版本兼容边界，但它直接进入了 Rev.C 声称覆盖的 T5 和允许安装的依赖域。

#### 4.2.2 修改意见

二选一，必须形成明确产品决策：

1. **锁定依赖（推荐的低风险路径）**：在 `requirements.txt`、`pyproject.toml`、镜像/离线包构建依赖中锁定同一个已经完成全量验证的 sqlglot 精确版本或窄范围；不得仅在文档写“发布时记录版本”。若采用范围，必须先找出真实最低兼容版本，不能直接根据本次两个通过点推定整个区间都兼容。
2. **保持 `>=26.0.0`**：则必须修复 26.0.0 的 T5 兼容并在 26.0.0 上执行 Rev.C 52 例、定向回归、生产 fixture、全量测试。

无论选择哪条，CI 至少要覆盖“最低允许版本 + 发布锁定版本”，且把 T5 纳入版本矩阵，而不只是验证 DEF-1 AST 节点类型。

### 4.3 DOC-C1：部分文字和证据标签仍停留在 Rev.B

以下不单独阻断发布，但应随方案修订一并纠正，避免施工误读：

- §3.1 对照表仍写“Rev.B 如何满足”，并称“首两个 token 必须是 CREATE+TABLE”，与 Rev.C 支持中间 `TEMPORARY` 不一致；
- §5.1 指标列、§5.13 回归结果仍标 Rev.B；
- §5.9、§8、附录 A 对 T7/T8 和字符串风险的结论需按 BLOCK-C1 撤回并重写；
- 本轮环境全量结果为 1384 passed、0 failed；设计中的 1355 passed + 29 skipped 总数相同，但应注明跳过差异来自执行环境，不能继续写成无上下文的唯一基线。

## 5. 独立测试结果

### 5.1 生产附件与 fixture

从用户附件 HTML 的首个 `sql-text` 节点解码 SQL，并仅统一 CRLF/LF 后比较：

| 报告 | fixture | 行数 | 字符数 | 结果 |
|---|---|---:|---:|---|
| gg77 | `report_6309_kcfb_list_info.sql` | 82 | 7976 | 逐字符一致 |
| gg78 | `report_6311_biz_tx_log.sql` | 112 | 7728 | 逐字符一致 |

按 Rev.C 代码临时施工后原样送审：

| fixture | instance_type | 解析 | 实际规则集合 | 结论 |
|---|---|---|---|---|
| 6309 / gg77 | distributed | 成功，68 列 | `{R011,R018,R019,R036,R037,R061,R065,R067,R104}` | 与设计精确相等 |
| 6311 / gg78 | centralized | 成功，75 列 | `{R036,R037}` | 与设计精确相等 |

### 5.2 定向与全量回归

| 项目 | 结果 |
|---|---|
| 现有 TDSQL fallback + R077/R054 + Oracle 兼容定向测试（30.12.0） | **162 passed / 0 failed** |
| 同组定向测试（30.14.0） | **162 passed / 0 failed** |
| 同组定向测试（26.0.0） | **161 passed / 1 failed**，失败为 HASH + 二级 PARTITION |
| `pytest tests -q`（30.12.0，Rev.C 临时代码） | **1384 passed / 0 failed**，263.35 秒 |
| `verify_rules.py` | 119 条规则、107 条文件覆盖、0 未覆盖、3 条既知断言失败，与文档基线同名同因 |

全量通过只能说明既有用例未退化，不能覆盖 BLOCK-C1。三个静默 AST 反例均不在现有测试的可达路径内。

## 6. Rev.C 安全性质重新判定

| 性质 | Rev.C 声称 | 第三轮判定 |
|---|---|---|
| S-1：首次解析成功语句不变 | 新逻辑只在 except | **局部成立**；但不能证明首次失败语句被恢复后的 AST 正确 |
| S-2a：词法完整性 | 差异只在 tokenizer span | **仅第一阶段成立，整个链路不成立**；第二阶段全局正则未纳入 span |
| S-2b：语法作用域完整性 | 第一条 CREATE TABLE 顶层 UNIQUE 定义项 | **成立**；本轮未发现作用域越界 |
| S-3：无法证明安全时失败关闭 | 候选门禁 | **不成立**；错误列集合/错误注释仍可形成同表 `exp.Create` 并被接纳 |
| S-4：raw_sql 保持原文 | 仅副本参与转换 | **成立但不足以兜底**；结构化字段已经错误 |

## 7. 准入条件

下一版复审至少应提交以下可验证材料：

- [ ] BLOCK-C1：安全、token-aware、仅顶层尾子句的 TDSQL 剥离器设计及完整代码块；
- [ ] 两阶段 span 联合门禁，证明所有改写字符只属于批准的 UNIQUE COMMENT 或真实 TDSQL 尾子句；
- [ ] 现有首次 `Command` fallback 与新 except fallback 统一使用安全实现；
- [ ] HASH/RANGE/LIST/BROADCAST × 列名/索引/注释/DEFAULT/伪 SQL 的交叉测试，字段级精确断言；
- [ ] 不再以“去掉 UNIQUE COMMENT 后的同源预处理结果”作为 T7/T8 唯一 oracle；
- [ ] MAJOR-C1：明确 sqlglot 依赖锁定或 26.0.0 兼容方案，并提供对应版本矩阵；
- [ ] 两个生产 fixture 精确规则集合继续不漂移；
- [ ] Rev.C 计划的全部新增用例零 skip，定向、全语料、全量测试无新增失败；
- [ ] 文档撤回已被反例推翻的风险结论并更新版本/回归环境说明。

## 8. 最终评审意见

Rev.C 已经解决第二轮指出的大部分直接缺陷，目标生产样本也能得到正确结果；但它用一个只对第一阶段成立的安全证明，覆盖了一个包含第二阶段全局正则的完整恢复链路。第三轮实测证明该链路会静默删除真实列、篡改真实注释，并把错误 AST 当作成功结果接纳。对于 SQL 审核这一核心能力，这类“看起来成功、实际结构错误”的风险高于显式 E999，不能带病进入开发或发布。

**最终结论：No-Go。**

本轮要求不是推翻 sqlglot tokenizer 方案，而是将 tokenizer 的作用域安全贯彻到 TDSQL 尾子句处理，消除同源全局正则；同时把实际支持的 sqlglot 版本从“文档提示”升级为可执行的依赖与 CI 约束。完成 BLOCK-C1 和 MAJOR-C1 后，再进入第四轮复审。
