# v1.6.2.2 索引解析修复设计 Rev.B 独立复审报告

## 0. 文档信息

| 项目 | 内容 |
|---|---|
| 评审对象 | `DESIGN-v1.6.2.2-索引类型误判与唯一索引注释解析崩溃修复详细设计说明书.md` Rev.B |
| Rev.B 提交 | `ea21c4d` |
| 问题版本 / 目标版本 | v1.6.2.1 / v1.6.2.2 |
| 评审日期 | 2026-08-25 |
| 独立评审人 | Codex |
| 评审方式 | 完整文档审阅、原样候选代码施工到临时 detached worktree、多版本 sqlglot 探针、生产 fixture 回放、定向反例、随机输入及全量回归 |
| 代码处置 | 本次只复审设计；主工作树不修改项目代码，候选代码只用于临时验证 |

## 1. 用户既定决策

**Rev.B 使用 sqlglot 词法器，是用户已确定的技术路线，本次不再评议“用不用词法器”。**

本报告只评审以下问题：

1. 词法器 token 是否被正确约束在目标语法作用域；
2. 新重试能否与系统既有 TDSQL 方言恢复链正确组合；
3. 接纳门禁能否防止非目标改写和错误 AST 被采用；
4. 自动化测试是否真正证明“无次生灾害”。

## 2. 结论

### 2.1 总结论：**Rev.B 有实质进步，但仍为 No-Go**

Rev.B 已经正确关闭上一轮最危险的问题：全局正则被完全废弃，列注释、表注释、默认值和 SQL 注释中的伪 `UNIQUE KEY ... COMMENT` 不再被跨词法边界修改。DEF-1 的精确 `kind` 白名单、表类型门禁、表名同一性门禁和 `raw_sql` 保留也都可以采纳。

但是独立反证发现两个上线阻断项：

1. **新重试没有与 v1.6.2.0 的 TDSQL 方言重试组合。** 同一条 DDL 同时包含 `UNIQUE ... COMMENT` 与 `TDSQL_DISTRIBUTED BY HASH/RANGE/LIST` 或 `BROADCAST` 时，Rev.B 仍然解析失败并产生 E999。这是 TDSQL 核心语法组合，不是外围扩展。
2. **剥离器没有实现文档声称的“只处理顶层定义项开头”。** 实现实际条件只是 `depth == 1 and token == UNIQUE`，会把定义项中部和后续语句中的 UNIQUE 也列为“批准 span”。span 差异门禁只能证明代码改在自己声明的 span 内，不能证明该 span 语义上属于目标索引。

另有两项必须在准出前修正：

- `CREATE TEMPORARY TABLE` 是项目已有审核对象，但 Rev.B 明确漏掉；
- F 组生产回放使用的 fixture 带有非生产 SQL 文件头，且只做子集断言，不能证明文档声称的“只减目标误报、无任何新增”。

因此 Rev.B 不能直接交给施工方照图实施。建议形成小幅但关键的 Rev.C：**保留 sqlglot 词法器方案，只修正重试链组合、定义项边界、TEMPORARY 入口和测试判据。**

### 2.2 分项裁决

| 项目 | 裁决 | 说明 |
|---|---|---|
| 使用 sqlglot 词法器 | 用户既定，按通过处理 | 本轮不讨论替换为手写 FSM |
| Rev.A 全局正则整体废弃 | 通过 | 上轮字符串字面量污染反例已关闭 |
| DEF-1 精确 `kind` 白名单 | 通过 | 当前输出正确，并具备依赖漂移护栏 |
| SPATIAL 维持 NORMAL | 通过 | 兼容性取舍表述已修正 |
| 不启用方案 B / ADJ-10 | 通过 | 继续避免不感知字符串的属性兜底 |
| 等长、span、Create、TABLE、同表名门禁 | 方向通过 | 仍需解决“span 本身是否为目标语法”的前置证明 |
| BLOCK-1 字符串越界污染整改 | 通过 | 定向负例与独立随机测试均未发现越界改写 |
| TDSQL 方言组合 | **阻断** | HASH/RANGE/LIST/BROADCAST 四类全部仍失败 |
| 顶层定义项起点约束 | **阻断** | 文档声称已实现，实际代码未实现 |
| TEMPORARY 建表 | 需整改 | 项目已有 R024/R032 与现成测试，不应遗漏 |
| 生产 fixture 与断言 | 需整改 | 原样读取会多出 R104，子集断言无法证明零新增 |

## 3. Rev.B 已经正确解决的部分

### 3.1 上轮 BLOCK-1 的字符串污染已关闭

对 Rev.B 设计中的 `_strip_unique_index_comments()` 原样执行以下反向样例：

- 列 COMMENT 内含伪 UNIQUE SQL；
- 表 COMMENT 内含伪 UNIQUE SQL；
- DEFAULT 字符串内含伪 UNIQUE SQL；
- `--`、`#`、`/* */` 注释内含伪 UNIQUE SQL；
- 反引号标识符内含伪 UNIQUE SQL；
- 同一语句另有一个真实 UNIQUE 索引 COMMENT，确保确实进入变换。

结果均为：只返回真实索引 COMMENT 的 span，字符串或注释中的伪 SQL 不可见，越界改写字符数为 0。上一轮会污染 `column_comments['b']` 的原样反例在 Rev.B 中不再污染。

独立随机验证结果：

| 验证 | 数量 | 结果 |
|---|---:|---|
| 任意字符随机输入 | 8,000 | 抛异常 0；返回非 None 时长度/span 不变量违例 0 |
| 带真实索引注释及伪 SQL 字符串的结构化随机 DDL | 2,000 | 非目标区域改写 0；span 数错误 0 |

**裁决：使用 sqlglot 词法器确实解决了 Rev.A 的跨字符串边界问题。**

### 3.2 DEF-1 与两个生产问题的基本目标结果正确

将 Rev.B 四个改动点原样施工到临时工作树后：

| 样例 | Rev.B 候选结果 |
|---|---|
| gg77 / `kcfb_list_info` | `idx13` 恢复为 NORMAL；R054 消失；其他生产 DDL 规则结果不因 DEF-1 漂移 |
| gg78 / `biz_tx_log` | 解析成功，75 列、主键、INNODB、UTF8MB4 均恢复；对报告原始 DDL仅保留 R036/R037 |
| `shardkey=a` + UNIQUE COMMENT | 解析成功 |
| 反斜杠转义、前缀索引、转义反引号索引名 | 均恢复成功 |

DEF-1 的 `kind in {'PRIMARY','UNIQUE','FULLTEXT'}` 精确映射在 sqlglot 26.0.0、30.12.0、30.14.0 三个版本中行为一致，可以保留。

### 3.3 现有回归健康

原样候选补丁的独立执行结果：

| 测试 | 结果 |
|---|---|
| R054 / TDSQL 方言 / R061 三组专项 | **71 passed** |
| 当前仓库全量 pytest | **1384 passed，0 failed，10 warnings** |

这些结果证明 Rev.B 没有破坏现有自动化覆盖范围，但本报告后续反例也证明：现有测试尚未覆盖关键组合路径，不能仅凭全绿判定可上线。

## 4. 阻断项

### BLOCK-B1：UNIQUE-COMMENT 重试与 TDSQL 方言重试没有组合

#### 4.1.1 复现结果

使用 Rev.B 原样候选分别测试：

```sql
CREATE TABLE t (
  a INT,
  PRIMARY KEY (a),
  UNIQUE KEY uk (a) COMMENT 'x'
) ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(a);
```

并将尾子句依次替换为 RANGE、LIST、BROADCAST：

| 尾部方言 | 剥离 UNIQUE COMMENT | 重试 AST | Rev.B 是否接纳 | 最终结果 |
|---|---|---|---|---|
| `TDSQL_DISTRIBUTED BY HASH(a)` | 1 span，正确 | `exp.Command` | 否 | **E999，columns=0** |
| `... RANGE(a)` | 1 span，正确 | `exp.Command` | 否 | **E999，columns=0** |
| `... LIST(a)` | 1 span，正确 | `exp.Command` | 否 | **E999，columns=0** |
| `BROADCAST` | 1 span，正确 | `exp.Command` | 否 | **E999，columns=0** |

相同结构使用 `shardkey=a` 时不会降级为 Command，Rev.B 可以恢复。这一对照进一步锁定了传导原因。

#### 4.1.2 原因

当前解析器已有两段互相独立的恢复逻辑：

```text
正常 parse_one
  └─ 返回 Command 且含 TDSQL 方言尾子句
       └─ 剥离方言尾子句再解析

首次 parse_one 抛异常
  └─ Rev.B 剥离 UNIQUE COMMENT 再解析
       └─ 只接受 Create/TABLE；Command 直接拒绝
```

当两个扩展语法同时出现时：

1. 首次解析因 UNIQUE COMMENT 直接抛异常，无法进入旧的 Command 恢复分支；
2. Rev.B 去掉 COMMENT 后，方言尾子句使结果降级为 Command；
3. 新门禁要求 `exp.Create`，因此候选被拒绝；
4. 最终回到原 E999 路径。

Rev.B 的 NG-4 要求旧方言重试“一字不改”可以保留，但**不代表新异常重试可以不复用同一能力**。

#### 4.1.3 必须修改

在 UNIQUE COMMENT 变换完成并通过 span 安全校验后：

1. 先解析 `_new_sql`；
2. 如果候选是 `exp.Command` 且满足既有 `_TDSQL_DIALECT_RE` 条件，按 v1.6.2.0 的同一规则对 `_new_sql` 再做一次方言恢复；
3. 只有最终结果为 `exp.Create`、`kind == TABLE`、同表名时才接纳；
4. 任何一步失败均保留原异常；
5. 不修改 `_TDSQL_DIALECT_RE` 本身，也不放宽现有 Command 前置门禁。

必须新增组合测试：

- HASH、RANGE、LIST、BROADCAST + UNIQUE COMMENT；
- HASH + 二级 PARTITION + UNIQUE COMMENT；
- 列名为 `broadcast` + UNIQUE COMMENT，证明正常列不被方言正则吃掉；
- 表/列注释中含伪 `TDSQL_DISTRIBUTED` + 真实 UNIQUE COMMENT，证明只有真实 Command 才进入方言恢复；
- 组合恢复后 `raw_sql` 仍为原文，R077/R054 仍从原文取得分片事实。

### BLOCK-B2：“定义项开头”约束没有真正实现

#### 4.2.1 文档与代码不一致

Rev.B 文档声称：

> 只处理顶层定义项开头的真实 `UNIQUE [KEY|INDEX]` token。

实际条件是：

```python
if depth == 1 and tt == TokenType.UNIQUE:
```

代码没有记录“当前 token 是否是左括号后或顶层逗号后的第一个 token”，因此 `UNIQUE` 只要处在深度 1 就会进入候选识别。

独立测试显示以下合法约束定义也被剥离器识别：

```sql
CONSTRAINT uq UNIQUE (a) COMMENT 'constraint comment'
```

当同一表另有一个真正触发异常的 `UNIQUE KEY ... COMMENT` 时，剥离器返回 2 个 span，而不是只处理设计目标的 1 个 span。该形态又被 NG-10/ADJ-11 明确列为本次非目标，设计和实现发生冲突。

更广泛地，定义项中部的 `UNIQUE(...) COMMENT` 组合也能被当成批准 span；在部分 sqlglot 宽松可解析形态下，重试会成功。**“差异全部位于 spans 内”的门禁无法阻止这一问题，因为错误位置已经被剥离器自己声明成了 span。**

#### 4.2.2 第二条语句也会被扫描

外层扫描在第一个 CREATE TABLE 的定义列表闭合后没有停止。如果调用者直接传入两个语句，剥离器会继续处理第二条语句的 UNIQUE COMMENT。

在依赖声明允许的 sqlglot 26.0.0 上，`parse_one()` 明确返回“第一个已解析语句”，实测 Rev.B 会修改两条语句的 2 个 span，却只接纳第一张表的 AST。sqlglot 官方 v26.0.0 源码也明确说明 `parse_one` 返回第一个解析语句：

- [sqlglot v26.0.0 `parse_one`](https://github.com/tobymao/sqlglot/blob/v26.0.0/sqlglot/__init__.py)

虽然多数文件审核入口会预先拆分 SQL，`RuleChecker.audit_sql()` 和部分 API 仍是直接单条接口；安全函数本身不应依赖所有上游永远正确拆分。

#### 4.2.3 必须修改

1. 从已经定位的建表定义列表左括号开始扫描，而不是从 token 0 重新全局扫描；
2. 显式维护 `at_definition_start`：只在定义列表左括号之后或深度 1 的逗号之后置 True；消费第一个真实 token 后置 False；
3. 本次既然维持 NG-10，就只在 `at_definition_start and token == UNIQUE` 时进入；如决定支持 `[CONSTRAINT symbol] UNIQUE`，必须显式建模并同步删除 NG-10，而不是偶然命中；
4. 匹配第一个定义列表的收尾右括号后立即停止；
5. 增加单语句门禁：拒绝尾随第二条真实语句，或使用能验证“恰好一个 AST”的解析 API；
6. span 校验除“差异位于 span”外，还必须断言每个 span 的语法来源都是目标定义项；
7. 新增定义项中部、CONSTRAINT、两个语句的负向测试。

## 5. 重要整改项

### MAJOR-B1：漏掉项目明确支持的 `CREATE TEMPORARY TABLE`

MySQL 官方 CREATE TABLE 语法为 `CREATE [TEMPORARY] TABLE`，TEMPORARY 不是另一个无关语句类型：

- [MySQL 8.0 CREATE TABLE 语法](https://dev.mysql.com/doc/refman/8.0/en/create-table.html)

项目当前也已经：

- 在解析模型中维护 `is_temporary_table`；
- 通过 R024、R032 审核临时表；
- 存在 `test_sit_v1_rules.py` 等临时表测试。

Rev.B 剥离器却硬编码首两个 token 必须是 `CREATE`、`TABLE`。实际 token 流为：

```text
CREATE, TEMPORARY, TABLE
```

独立对照：

| SQL | 结果 |
|---|---|
| TEMPORARY TABLE，无 UNIQUE COMMENT | 解析成功 |
| 同一语句增加 `UNIQUE KEY ... COMMENT` | Rev.B 不变换，仍报 E999 |

建议入口明确接受 `CREATE [TEMPORARY] TABLE`，并从实际 TABLE token 后开始提取表名。集中式和分布式分别测试：集中式不能产生假 E999；分布式仍应保留 R024/R032 的真实规则结果。

### MAJOR-B2：产品边界和生产回放断言不闭合

#### 5.2.1 产品边界少列一类官方合法语法

MySQL UNIQUE 定义允许 `[index_type]` 位于键值列表之前：

```sql
UNIQUE KEY uk USING BTREE (a) COMMENT 'x'
```

官方语法见 CREATE TABLE 文档中 `[index_name] [index_type] (key_part,...)`。Rev.B 剥离器在可选索引名之后立即要求左括号，因此该语句不产生 span；去掉 COMMENT 后当前 sqlglot 仍不能解析，所以可以继续失败关闭，但必须像函数索引、VISIBLE、KEY_BLOCK_SIZE 一样列入显式产品边界和 C 组测试，不能声称边界只有三类。

#### 5.2.2 fixture 并非报告原文，原样审核会多出 R104

两份 fixture 文件都在真实 DDL 前增加了来源说明注释。gg78 第一行包含全角括号：

```sql
-- 内网人工扫描报告 #6311（gg78）原样 DDL，未做任何删改。
```

这行不在用户上传报告的 SQL 中。R104 直接扫描 `raw_sql` 的全角括号，因此 Rev.B 候选原样读取 fixture 的结果是：

```text
gg78 fixture：R036、R037、R104
报告真实 DDL：R036、R037
```

F2 当前只断言假错误集合为空、R036/R037 是结果子集，会在额外出现 R104 时照样通过；F1 同样用子集断言，不能证明“只减 R054”。这与 G-10 的“gg78 只剩 R036/R037”和全文“无任何新增”不一致。

必须二选一：

1. fixture 文件只保留报告真实 DDL，把来源说明移到测试 docstring/旁车说明；或
2. 测试明确提取从真实 `CREATE TABLE` 开始的报告 DDL，并证明提取结果与 HTML 原文一致。

然后使用精确集合断言：

```python
assert rule_ids_gg78 == {"R036", "R037"}
assert rule_ids_gg77 == {
    "R011", "R018", "R019", "R036", "R037",
    "R061", "R065", "R067", "R104",
}
```

如果不同运行配置会影响规则启停，应先固定与报告一致的规则配置，再定义精确期望；不能退化为只验证两个子集。

## 6. 对 Rev.B 安全性质的复审

| 性质 | 结论 | 说明 |
|---|---|---|
| S-1 首次解析成功路径不变 | 通过 | 新逻辑仍只位于 except；当前正常语料无漂移 |
| S-2 变换仅在批准 span | **机械性质通过，语义性质未通过** | 词法字符串边界安全；但代码未证明 span 位于目标定义项开头，span 可被错误批准 |
| S-3 无法证明安全则失败关闭 | 部分通过 | 未闭合引号/括号能关闭；定义项起点、第二条语句未纳入证明 |
| S-4 `raw_sql` 保持原文 | 通过 | 正向及反向样例均保持原文 |

Rev.C 应把 S-2 拆成两层：

1. **词法完整性**：差异只落在 token 提供的字符串 span；
2. **语法作用域完整性**：每个 span 必须来自第一条 CREATE TABLE 的顶层、以 UNIQUE 开头的定义项。

只有两层同时成立，span 门禁才是有效的安全证明。

## 7. Rev.C 必补测试

### 7.1 保留 Rev.B 已有测试

- A 组 DEF-1 与 AST 契约；
- B 组普通 UNIQUE COMMENT 正向恢复；
- 字符串、默认值、SQL 注释、反引号等负向矩阵；
- gg77、gg78 生产报告回放；
- 随机输入不抛异常及长度/span 不变量。

### 7.2 新增组合测试

| 组别 | 用例 | 关键断言 |
|---|---|---|
| T1 | HASH + UNIQUE COMMENT | Create/TABLE，columns>0，无 E999，R077/R054 正确 |
| T2 | RANGE + UNIQUE COMMENT | 同上 |
| T3 | LIST + UNIQUE COMMENT | 同上 |
| T4 | BROADCAST + UNIQUE COMMENT | 同上 |
| T5 | HASH + PARTITION + UNIQUE COMMENT | 方言恢复后分区结构不被额外破坏 |
| T6 | `shardkey=` + UNIQUE COMMENT | 继续成功，不进入不必要的方言重试 |
| T7 | 列名 `broadcast` + UNIQUE COMMENT | 列仍存在，不误剥离 |
| T8 | 注释含伪 TDSQL 子句 + UNIQUE COMMENT | 不触发方言剥离 |
| T9 | TEMPORARY + UNIQUE COMMENT（集中式） | 无假 E999 |
| T10 | TEMPORARY + UNIQUE COMMENT（分布式） | 无假 E999，R024/R032 保留 |

### 7.3 新增作用域负向测试

1. `CONSTRAINT uq UNIQUE (...) COMMENT ...` 与真实目标同时出现；本次不支持时不得把约束 COMMENT 计入 span；
2. 列内联 `UNIQUE KEY COMMENT` 不得计入 span；
3. 定义项中部出现 `UNIQUE(...) COMMENT` 不得计入 span；
4. 两条 CREATE TABLE 拼接输入不得修改第二条或只接纳第一条；
5. 第一个定义列表闭合后的表选项、分区定义、后续文本不得被当成表定义项；
6. 每个返回 span 的起点都可追溯到顶层定义项第一个真实 token。

### 7.4 收紧生产准出断言

1. 使用与 HTML 报告逐字一致的 DDL，不把 fixture 元数据注释送入规则引擎；
2. gg77、gg78 规则集合做精确相等断言；
3. 分布式/集中式 `instance_type` 继续严格按报告上下文；
4. 生产 14 表和其余 195 条语料零漂移；
5. 全量 pytest 0 failed；
6. 在发布实际 sqlglot 版本和依赖最低版本 26.0.0 至少各跑解析器专项及新增组合测试。

## 8. 独立验证明细

| 项目 | 结果 |
|---|---|
| Rev.B 文档完整阅读 | 886 行全部审阅 |
| 原样候选代码可导入/执行 | 通过 |
| sqlglot 版本矩阵 | 26.0.0、30.12.0、30.14.0 |
| 两个生产问题基本目标 | gg77、gg78 均达到基础目标 |
| 上轮字符串污染反例 | 已关闭 |
| 随机与结构化安全输入 | 10,000 条，异常/越界违例 0 |
| TDSQL 组合反例 | HASH/RANGE/LIST/BROADCAST 4/4 失败 |
| shardkey 组合对照 | 成功 |
| TEMPORARY 组合 | 仍失败 |
| 定义项起点反例 | 证实会把非起点 UNIQUE 计入 span |
| 多语句（sqlglot 26.0.0） | 2 个 span 均被修改，只接纳第一表 AST |
| fixture 原样回放 | gg78 额外出现 R104 |
| 71 项专项测试 | 71 passed |
| 当前仓库全量测试 | 1384 passed，0 failed，10 warnings |

## 9. 准入结论

### 当前状态：**No-Go**

Rev.C 至少满足以下条件后再复审：

- [ ] UNIQUE COMMENT 恢复与既有 HASH/RANGE/LIST/BROADCAST 方言恢复正确组合；
- [ ] 剥离器真正限定到第一条 CREATE TABLE 的顶层定义项起点；
- [ ] 第一个定义列表闭合后停止扫描，并拒绝/明确处理尾随第二条语句；
- [ ] 支持 `CREATE TEMPORARY TABLE`，或取得用户明确的排除决定并评估 R024/R032 影响；
- [ ] `USING BTREE` 位于键值列表前的合法语法加入显式产品边界；
- [ ] fixture 元数据不再污染审核 SQL；gg77/gg78 改为精确规则集合断言；
- [ ] 新增 TDSQL 组合、TEMPORARY、作用域负向及多语句测试；
- [ ] 在 sqlglot 26.0.0 与发布实际版本上均通过新增专项；
- [ ] 全量回归、历史语料和生产表回放无非预期漂移。

## 10. 给 A 的最终意见

1. **Rev.B 的主方向无需推翻。** sqlglot 词法器选型保留，上一轮跨字符串污染已经真正修好。
2. **最关键的遗漏是重试链组合。** 这是 TDSQL 平台，UNIQUE COMMENT 与 `TDSQL_DISTRIBUTED/BROADCAST` 的交集必须作为第一优先级补上。
3. **不要把 `depth == 1` 等同于“定义项开头”。** 需要一个明确的定义项起点状态，否则 span 门禁只能自证，没有语义约束力。
4. **TEMPORARY 不是边缘臆造语法。** MySQL 官方语法和项目 R024/R032 都证明它属于现有产品域。
5. **生产回放必须用精确事实做精确断言。** fixture 的来源说明不能混进 `raw_sql`，子集断言不能证明“只减目标误报”。

综合判断：**Rev.B 已从“实现机制不安全”提升到“机制基本正确但组合路径和作用域护栏未闭合”。完成上述 Rev.C 小范围整改后，方案有望达到可施工状态；当前不能准入。**
