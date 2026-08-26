# DESIGN-v1.6.2.2 索引类型误判与唯一索引注释解析崩溃 修复详细设计说明书

| 项目 | 内容 |
|---|---|
| 文档版本 | **Rev.I**（O 八轮独立复审；本版按第八轮 BLOCK-H1/H2/H3、MAJOR-H1/H2、MINOR-H1/H2 整改，并把判据切换为 **TDSQL 官方语法**） |
| 目标版本 | **v1.6.2.2** |
| 缺陷来源 | 内网人工扫描报告 #6309（gg77）、#6311（gg78） |
| 缺陷编号 | **DEF-1 = DEF-R054-FAKEUNIQUE**；**DEF-2 = DEF-PARSE-UKCOMMENT** |
| 撰写 | 智能体 A |
| 施工 | 智能体 Q |
| 基线 commit | `03216b7`（main） |
| 评审依据 | `docs/REVIEW-v1.6.2.2-...独立复审报告-Codex.md` |
| 改动范围 | **`parser_legacy.py` 5 个改动点**（含**删除 v1.6.2.0 的 `_TDSQL_DIALECT_RE` 全局正则**）+ **`requirements.txt` / `pyproject.toml` 各 1 行依赖 pin**；fixture 已在 Rev.C 修正 |
| 实测结论 | 生产 14 表**零漂移**；全语料 197 条中**恰好 2 条**变化且都是目标缺陷；全量回归 **1355 passed / 0 failed / 29 skipped**；TDSQL 四种方言组合**全部恢复**；5 类作用域负例 span **全部为 1**；模糊 6000 条**零违例**；生产回放**精确集合相等** |

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

## Rev.I 修订说明（针对 O 第八轮独立复审）

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

Rev.H 的两个剥离器各自决定"要不要改写"，谁也不为整条语句负责，这正是 BLOCK-H1 的根因。
Rev.I 改为：

```text
_plan_recovery(sql)                     ← 唯一入口，一次性验证整条 CREATE TABLE
  ├─ _tdsql_table_def_bounds()          定位建表头与定义列表
  ├─ _scan_definition_list()            逐个定义项普查（列类型、索引键列、索引选项）
  │    └─ _consume_index_key_parts()    TDSQL key_part：col [(len)] [ASC|DESC]
  └─ _scan_table_tail()                 表尾**始终**完整验证，直到语句结束
       ├─ _consume_table_option()       每选项专属值谓词
       ├─ _consume_partition_clause()   TDSQL 二级分区
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

O 对 Rev.F 判定 **No-Go**，开出 2 项 BLOCK、2 项 MAJOR、2 项 MINOR。**我逐条独立复现，全部成立，全部接受。**

| 编号 | O 的意见 | 我的复核 | Rev.G 处置 |
|---|---|---|---|
| **BLOCK-F1** | 方言目标"内部"合法，但**所处表选项上下文**未验证 | ✅ 目标前紧邻残缺 `DEFAULT` / `CHECKSUM` / `INDEX DIRECTORY` 时，**12 种组合全部**得 span=1、`ast=Create`、**E999 消失**（主干对照：均报 E999） | 表选项区改为**完整 atom 消费**：目标之外每个 token 都必须被 `_consume_table_option()` 消费；**不再有"跳过不认识的 token"** |
| **BLOCK-F2** | UNIQUE COMMENT 的**索引选项上下文**未验证 | ✅ `USING COMMENT 'x'` / `COMMENT 'x' USING`（缺 BTREE/HASH）→ Rev.F 得 span=1、`Create`、**E999 消失**（主干：E999） | 索引选项区同样改为**完整消费**：只接受 `USING (BTREE\|HASH)` 与 `COMMENT STRING`，其余一律失败关闭 |
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
2. **两个剥离器共用同一个严格头部定位器** `_tdsql_table_def_bounds()`。
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
| §3.2 门禁表 ③b | 「复用 v1.6.2.0 同一规则（`_TDSQL_DIALECT_RE`）」 | 改为「调用 `_strip_tdsql_dialect_tail()`」 |
| 施工清单 C-10 | 「`_TDSQL_DIALECT_RE` 及旧重试块**一字未动**」 | 改为「**确认该常量已删除**」 |
| 施工清单 C-11 | 「A~F+T+N 共 **52 例**」 | 改为「A~F+T+N+X 共 **90 例**」 |
| G-13 | 「T 组 **10 例**」 | 改为「T 组 **8 例**」（T7/T8 已撤销） |
| 附录 B 第 3 条 | 「复用**同一条** `_TDSQL_DIALECT_RE`」 | 改为「调用新的 token 剥离器，**不得**恢复旧正则」 |
| §9 C-1/C-2、§8 回滚 | 「只改 1 个产品文件、4 个改动点」 | 改为「`parser_legacy.py` 5 个改动点 + 2 处依赖声明」 |
| §5.1 标题重复、附录 B「六句话」实为 7 条 | — | 已更正 |

---

## Rev.D 修订说明（针对 O 第三轮独立复审）

O 对 Rev.C 判定 **No-Go**，开出 1 项 BLOCK、1 项 MAJOR、1 项 DOC。**我逐条独立复现，全部成立，全部接受。**

| 编号 | O 的意见 | 我的复核 | Rev.D 处置 |
|---|---|---|---|
| **BLOCK-C1** | 第二阶段仍对整条 SQL 做不感知作用域的 `_TDSQL_DIALECT_RE.sub()`，会删真实列、改真实注释，且错误 AST 能通过四道门禁 | ✅ **三个反例全部复现**，并进一步查明**当前生产版本 v1.6.2.1 上已经如此** | **删除 `_TDSQL_DIALECT_RE`**，新增 token 级 `_strip_tdsql_dialect_tail()`；**新旧两条恢复入口统一使用它**；两阶段 span **联合门禁** |
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
| **S-1** | 不改变"首次解析即成功"语句的控制流与结果 | 恢复链只有**三条入口**，各自都要先拿到批准 span：① 首次解析得到**非 `Command` 的成功 AST** → **直接返回，不进入任何恢复**；② 首次得到 `exp.Command` → 仅当 `_strip_tdsql_dialect_tail()` 返回批准 span 时才重试（改动点 2b）；③ 抛异常进入 `except` → 仅当 `_strip_unique_index_comments()` 返回批准 span 时才重试（改动点 2）。**Rev.G 之前此处写作"新逻辑只在 `except` 内"，与 2b 冲突，第七轮 MAJOR-G2 已更正** | 全语料 197 条中仅 2 条变化，且均为本次目标缺陷 |
| **S-2a 词法完整性** | **整条恢复链**（阶段一 UNIQUE COMMENT + 阶段二 TDSQL 尾子句）的差异只落在两阶段 span 并集内 | 两阶段均为 token 级剥离并各自返回 span；最终做 `sql_clean → _final_sql` 的**联合**逐字符校验 | BLOCK-1 反例越界改写 **0**；X 组 40 例字段级精确保持（生产版本 36 例失败） |
| **S-2b 语法作用域与形态完整性** | **UNIQUE 阶段**：span 必须来自第一条 CREATE TABLE 顶层、以 UNIQUE 开头的定义项；**TDSQL 阶段**：span 必须是定义列表**闭合之后**顶层的**完整合法**方言尾子句 | UNIQUE 阶段用 `at_def_start`；TDSQL 阶段用**严格形态定位** + 必选 token 强校验 + 单声明约束 + 分号即失败关闭 | N 组 5 例 span 全为 1；§5.15 的 D1a/D1b/D1d 非法形态 span **全为 0**；CTAS / LIKE / 多语句 span **全为 0** |
| **S-2c 上下文完整性（Rev.G 引入，Rev.H 扩展到内部结构）** | 目标 span 所在的**整个语法单元及其内部结构**必须被逐 token 完整消费：表选项区逐 atom 且**每个选项使用专属值谓词**；UNIQUE 索引选项区只接受 `USING (BTREE\|HASH)` 与 `COMMENT STRING`；**键值列表逐 key-part**；**分区子句消费到语句结束**。**存在任何未被认领的 token 即整体失败关闭** | 五个消费器统一契约 `f(toks,i) -> 下一个下标 \| -1`；三条红线：不得配平后跳过内容、不得无条件 `break`、不得用大类 token 代替选项专属值谓词 | §5.17 W 组 28 例 + §5.19 **H 组 81 例**，在 sqlglot 30.14.0 与 29.0.0 上**逐条一致**：非法输入 0 例被修成合法，合法形态 0 例被收紧过头 |
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

**位置**：原 `_TDSQL_DIALECT_RE` 所在处（即 import 区之后、`_strip_unique_index_comments` 之前）。

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


_TDSQL_SHARD_METHODS = {"HASH", "RANGE", "LIST"}

# ── 表选项区的"完整 atom"白名单 ─────────────────────────────────────────────
# 第六轮 BLOCK-F1 的教训：只验证"目标片段"合法是不够的——目标周边若有**缺值的
# 残缺选项**（如孤立的 DEFAULT / CHECKSUM / INDEX DIRECTORY），删掉目标后
# sqlglot 会宽松接纳剩余残片并返回 Create，于是原本的 E999 被悄悄抹掉。
# 因此本版要求：目标所在的整个表选项区**必须被完整 atom 序列逐个消费干净**，
# 出现任何无法消费的 token 一律失败关闭。**不再有"跳过不认识的 token"这条路。**
#
# 白名单依据：对仓内全部 *.sql 语料与两份生产 fixture 的表选项区做 token 实测，
# 实际出现的只有下列有限组合（见设计说明书 §5.17.1 / §5.19.3）。
# 合法但不在白名单内的选项（如 INDEX DIRECTORY）**保持原 Command/E999**——
# 这是刻意的保守取舍：漏一次恢复，好过把非法 SQL 恢复成"可信 AST"。
#
# Rev.H（第七轮 BLOCK-G3）：值谓词由"四种宽类型任选其一"改为**每个选项专属**。
# Rev.G 把 ENGINE/ROW_FORMAT/SHARDKEY 统一放行 VAR/IDENTIFIER/STRING/NUMBER，
# 于是 `ENGINE=123`、`ROW_FORMAT=123` 这类非法选项被批准为"完整上下文"，
# 删除目标 span 后 sqlglot 宽松返回 Create，原 E999 被吞掉。
# 现在每个选项各自定义合法取值域；不符即失败关闭。

# 引擎名 / 字符集 / 排序规则：裸名、反引号名、引号名都合法，但**不能是数字**
_OPT_NAMEY = (TokenType.VAR, TokenType.IDENTIFIER, TokenType.STRING)
# 分片键：TDSQL 契约为单个列标识符（含 noshardkey_allset 哨兵）；不接受引号串/数字
_OPT_SHARDKEY = (TokenType.VAR, TokenType.IDENTIFIER)
# 纯数值选项
_TBL_OPT_VALUE_NUM = ("AUTO_INCREMENT", "CHECKSUM", "AVG_ROW_LENGTH",
                      "KEY_BLOCK_SIZE", "MAX_ROWS", "MIN_ROWS")
# ROW_FORMAT 的官方枚举。注意实测：DEFAULT→TokenType.DEFAULT、FIXED→TokenType.DECIMAL，
# 其余→VAR。因此按**文本**匹配，并用 _is_bare_kw 排除引号形态。
_ROW_FORMAT_ENUM = ("DEFAULT", "DYNAMIC", "FIXED", "COMPRESSED", "REDUNDANT", "COMPACT")
# 三值开关：官方取值为 0 / 1 / DEFAULT
_TBL_OPT_TRISTATE = ("STATS_PERSISTENT", "PACK_KEYS", "DELAY_KEY_WRITE")


def _consume_table_option(toks, i):
    """消费**一个**完整表选项 atom，返回下一个待消费下标；无法消费返回 -1。

    每个选项使用**专属值谓词**（`[=]` 表示等号可省略）：
      ENGINE [=] 引擎名(VAR/IDENTIFIER/STRING，拒绝 NUMBER)
      [DEFAULT] CHARSET|CHARACTER SET [=] 字符集名      [DEFAULT] COLLATE [=] 排序规则名
      COMMENT [=] STRING                                AUTO_INCREMENT [=] NUMBER
      ROW_FORMAT [=] DEFAULT|DYNAMIC|FIXED|COMPRESSED|REDUNDANT|COMPACT（裸词）
      SHARDKEY [=] 单标识符                             CHECKSUM/MAX_ROWS/... [=] NUMBER
      STATS_PERSISTENT|PACK_KEYS|DELAY_KEY_WRITE [=] 0|1|DEFAULT
    """
    n = len(toks)
    if i >= n:
        return -1
    tt = toks[i].token_type
    txt = (toks[i].text or "").upper()

    def _eq(j):
        """跳过可选等号，返回值 token 下标。"""
        return j + 1 if (j < n and toks[j].token_type == TokenType.EQ) else j

    def _val(j, kinds):
        j = _eq(j)
        if j < n and toks[j].token_type in kinds:
            return j + 1
        return -1

    def _val_words(j, words):
        """值必须是**裸关键字**且文本落在枚举内（拒绝 STRING / IDENTIFIER）。"""
        j = _eq(j)
        if j < n and _is_bare_kw(toks[j], None) and (toks[j].text or "").upper() in words:
            return j + 1
        return -1

    if tt == TokenType.DEFAULT:                      # DEFAULT 必须带 CHARSET/COLLATE
        if i + 1 < n and toks[i + 1].token_type in (TokenType.CHARACTER_SET,
                                                    TokenType.COLLATE):
            return _val(i + 2, _OPT_NAMEY)
        return -1
    if tt in (TokenType.CHARACTER_SET, TokenType.COLLATE):
        return _val(i + 1, _OPT_NAMEY)
    if tt == TokenType.COMMENT:
        return _val(i + 1, (TokenType.STRING,))
    if tt == TokenType.AUTO_INCREMENT:
        return _val(i + 1, (TokenType.NUMBER,))
    if tt != TokenType.VAR:
        return -1
    if txt == "ENGINE":
        return _val(i + 1, _OPT_NAMEY)
    if txt == "SHARDKEY":
        # TDSQL 官方分片键：单列 `shardkey=col`、多列 `shardkey=(a,b)`，
        # 以及全局表哨兵 `shardkey=noshardkey_allset`。
        # 多列形态由项目内 tdsql_connector.parse_shard_key_from_ddl() 证实
        # （"或多列 shardkey=(a,b)"）—— Rev.H 只认单标识符，会把官方合法
        # 的多列分片表判成非法（第八轮我方自查发现）。
        j = _eq(i + 1)
        if j < n and toks[j].token_type == TokenType.L_PAREN:
            return _consume_ident_list(toks, j)
        return _val(i + 1, _OPT_SHARDKEY)
    if txt == "ROW_FORMAT":
        return _val_words(i + 1, _ROW_FORMAT_ENUM)
    if txt in _TBL_OPT_TRISTATE:
        j = _eq(i + 1)
        if j < n and toks[j].token_type == TokenType.NUMBER and (toks[j].text or "") in ("0", "1"):
            return j + 1
        return _val_words(i + 1, ("DEFAULT",))
    if txt in _TBL_OPT_VALUE_NUM:
        return _val(i + 1, (TokenType.NUMBER,))
    return -1


# ── TDSQL 官方语法消费器（Rev.I：判据由 MySQL/sqlglot 切换为 TDSQL 官方语法）──
#
# 判据优先级（第八轮确立）：
#   ① 目标实例真实 SHOW CREATE TABLE / 已验证生产 DDL
#   ② 腾讯云 TDSQL MySQL 版官方语法
#   ③ 项目已冻结的产品规则与用户决策
#   ④ MySQL 官方语法
#   ⑤ sqlglot 当前解析能力
# sqlglot 只是**词法器与候选 AST 生成器**，不是 TDSQL 合规性判据：
# 既不能把"sqlglot 能解析"当作 TDSQL 合法，也不能把"sqlglot 解析失败"当作 TDSQL 非法。
#
# TDSQL 官方建表语法（本次建模所依据的形态）：
#   hash / broadcast:
#     CREATE TABLE ... [local_table_options] shardkey=col | shardkey=(col,...) | noshardkey_allset
#   range / list:
#     CREATE TABLE ... [local_table_options] TDSQL_DISTRIBUTED BY range|list (col) [partition_options]
#   index_type : USING {BTREE}              ← 官方**只有 BTREE**，没有 HASH
#   key_part   : {col_name [(length)]} [ASC | DESC]
#   二级分区   : PARTITION BY RANGE|LIST(expr) (partition_definition, ...)
#              子句顺序两种官方形态都存在：
#                shardkey=col PARTITION BY ...            （分片在前）
#                PARTITION BY ... TDSQL_DISTRIBUTED BY ... （分区在前）

# 官方 index_type 只有 BTREE。HASH 是 MySQL 某些引擎的能力，**不是 TDSQL 合规 DDL**，
# 且 119 条规则中没有任何一条负责否决 HASH 索引类型 —— 放行即次生放行（第八轮 BLOCK-H3）。
_TDSQL_INDEX_TYPES = ("BTREE",)
# 分区方法：官方二级分区支持 Range / List；HASH / KEY 为 MySQL 侧形态，一并接受但不放宽校验
_PARTITION_METHODS = ("RANGE", "LIST", "HASH", "KEY")
# 分片方法：TDSQL_DISTRIBUTED BY 后可接的方法
_TDSQL_SHARD_METHODS = ("HASH", "RANGE", "LIST")
# 分区表达式允许的函数（官方示例中的日期函数；其余一律失败关闭）
_PARTITION_FUNCS = ("YEAR", "TO_DAYS", "TO_SECONDS", "UNIX_TIMESTAMP", "MONTH", "DAYOFMONTH")


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


def _consume_index_key_parts(toks, i):
    """消费索引键值列表 `( key_part [, key_part]* )`。

    TDSQL 官方 key_part：`{col_name [(length)]} [ASC | DESC]`

    `i` 必须指向左括号；返回 `(下一个下标, ASC/DESC 的 span 列表)`，不合规返回 `(-1, [])`。

    **ASC / DESC 是 TDSQL 官方合法形态**，但 sqlglot 30.x 对
    `UNIQUE KEY uk (id ASC)` 直接 ParseError（实测）。因此本函数把它们作为
    **可掩码 span** 返回：由调用方等长置空后送进 sqlglot，从而在不牺牲 TDSQL
    合规性的前提下绕开解析器缺口。规则层不消费排序方向（实测 119 条规则
    无一引用 ASC/DESC），`raw_sql` 亦保持原文，故掩码不影响任何审核结论。
    """
    n = len(toks)
    if i >= n or toks[i].token_type != TokenType.L_PAREN:
        return -1, []
    spans = []
    j = i + 1
    while True:
        if j >= n or toks[j].token_type not in _IDENT_TOKENS:
            return -1, []                              # 空列表 / 前导逗号 / 非列名键
        j += 1
        if j < n and toks[j].token_type == TokenType.L_PAREN:      # 可选前缀长度
            if not (j + 2 < n and toks[j + 1].token_type == TokenType.NUMBER
                    and toks[j + 2].token_type == TokenType.R_PAREN):
                return -1, []
            j += 3
        if j < n and toks[j].token_type in (TokenType.ASC, TokenType.DESC):
            spans.append((toks[j].start, toks[j].end))             # 记为可掩码 span
            j += 1
        if j < n and toks[j].token_type == TokenType.COMMA:
            j += 1
            continue                                   # 逗号后必须还有 key_part
        if j < n and toks[j].token_type == TokenType.R_PAREN:
            return j + 1, spans
        return -1, []                                  # 尾随逗号 / 未闭合 / 未知 token


def _consume_partition_expr(toks, i):
    """消费分区表达式 `( col )` 或 `( FUNC(col) )`（官方形态），返回下一个下标；否则 -1。

    第八轮 BLOCK-H2：Rev.H 用"非空且括号配平"当充分条件，`(,)` / `(+)` / `(id,)`
    都能通过。现按官方形态精确建模，其余一律失败关闭。
    """
    n = len(toks)
    if i >= n or toks[i].token_type != TokenType.L_PAREN:
        return -1
    j = i + 1
    if j < n and toks[j].token_type in _IDENT_TOKENS:
        j += 1
    elif j < n and _is_bare_kw(toks[j]) and (toks[j].text or "").upper() in _PARTITION_FUNCS:
        j = _consume_ident_list(toks, j + 1)
        if j < 0:
            return -1
    else:
        return -1
    return j + 1 if (j < n and toks[j].token_type == TokenType.R_PAREN) else -1


def _consume_partition_values(toks, i):
    """消费 `VALUES LESS THAN ( ... )` / `VALUES IN ( ... )` / `VALUES LESS THAN MAXVALUE`。"""
    n = len(toks)
    if i >= n or toks[i].token_type != TokenType.VALUES:
        return -1
    j = i + 1
    if j < n and _is_bare_kw(toks[j], "LESS"):
        j += 1
        if not (j < n and _is_bare_kw(toks[j], "THAN")):
            return -1
        j += 1
        if j < n and _is_bare_kw(toks[j], "MAXVALUE"):
            return j + 1
    elif j < n and toks[j].token_type == TokenType.IN:
        j += 1
    else:
        return -1
    # 值列表：( 字面量 [, 字面量]* )，至少一个
    if j >= n or toks[j].token_type != TokenType.L_PAREN:
        return -1
    j += 1
    _LIT = (TokenType.NUMBER, TokenType.STRING, TokenType.VAR, TokenType.IDENTIFIER,
            TokenType.NULL)
    while True:
        if j >= n or toks[j].token_type not in _LIT:
            return -1
        j += 1
        if j < n and toks[j].token_type == TokenType.COMMA:
            j += 1
            continue
        if j < n and toks[j].token_type == TokenType.R_PAREN:
            return j + 1
        return -1


def _consume_partition_defs(toks, i):
    """消费分区定义表 `( partition_def [, partition_def]* )`。

    官方 partition_definition（本次建模的子集）：
        PARTITION name VALUES (LESS THAN (...) | LESS THAN MAXVALUE | IN (...))
        [ENGINE [=] name] [COMMENT [=] STRING]

    返回 `(下一个下标, 需掩码的 span 列表)`；不合规返回 `(-1, [])`。

    分区定义里的 `ENGINE = InnoDB` / `COMMENT = 'x'` 是**官方合法**的
    partition_option，但 sqlglot 30.x 遇到即 ParseError（实测）。同 ASC/DESC，
    按**可掩码 span** 处理，不因解析器缺口把官方语法判成非法。

    TDSQL 官方 `TDSQL_DISTRIBUTED BY range|list(col) (s1 VALUES LESS THAN(100), ...)`
    的分片定义表**没有 `PARTITION` 前缀**（官方原例即 `s1 values less than(100)`），
    故 `PARTITION` 关键字在此处可选。
    """
    n = len(toks)
    if i >= n or toks[i].token_type != TokenType.L_PAREN:
        return -1, []
    spans = []
    j = i + 1
    while True:
        if j < n and toks[j].token_type == TokenType.PARTITION:
            j += 1                                     # 二级分区形态带 PARTITION 前缀
        j = _consume_ident(toks, j)                    # 分区名
        if j < 0:
            return -1, []
        j = _consume_partition_values(toks, j)
        if j < 0:
            return -1, []
        while True:                                    # 可选 partition_option（可掩码）
            start = j
            if j < n and (toks[j].token_type == TokenType.VAR
                          and (toks[j].text or "").upper() == "ENGINE"):
                k = j + 1
                if k < n and toks[k].token_type == TokenType.EQ:
                    k += 1
                if k < n and toks[k].token_type in _OPT_NAMEY:
                    spans.append((toks[j].start, toks[k].end))
                    j = k + 1
                    continue
                return -1, []
            if j < n and toks[j].token_type == TokenType.COMMENT:
                k = j + 1
                if k < n and toks[k].token_type == TokenType.EQ:
                    k += 1
                if k < n and toks[k].token_type == TokenType.STRING:
                    spans.append((toks[j].start, toks[k].end))
                    j = k + 1
                    continue
                return -1, []
            if j == start:
                break
        if j < n and toks[j].token_type == TokenType.COMMA:
            j += 1
            continue
        if j < n and toks[j].token_type == TokenType.R_PAREN:
            return j + 1, spans
        return -1, []


def _consume_partition_clause(toks, i):
    """消费 `PARTITION BY ...` 一整个二级分区子句。

    `i` 必须指向 PARTITION_BY token；返回 `(下一个下标, 需掩码的 span 列表)`，
    不合规返回 `(-1, [])`。

    **不再要求消费到语句结束**——TDSQL 官方存在
    `PARTITION BY LIST(o) (...) TDSQL_DISTRIBUTED BY RANGE(id)` 这种
    分区在前、分片声明在后的合法顺序（官方原例 tb_sub_r_l）。
    Rev.H 强制消费到 EOF，会把该官方形态判成非法（第八轮 MAJOR-H1）。
    尾部完整性改由调用方的统一尾部扫描保证。
    """
    n = len(toks)
    if i >= n or toks[i].token_type != TokenType.PARTITION_BY:
        return -1, []
    j = i + 1
    if j < n and _is_bare_kw(toks[j], "LINEAR"):
        j += 1
    if not (j < n and _is_bare_kw(toks[j])
            and (toks[j].text or "").upper() in _PARTITION_METHODS):
        return -1, []                                  # 缺方法 / DEFAULT / 引号形态
    j += 1
    if j < n and _is_bare_kw(toks[j], "COLUMNS"):
        j += 1
    j = _consume_partition_expr(toks, j)
    if j < 0:
        return -1, []
    spans = []
    if j + 1 < n and _is_bare_kw(toks[j], "PARTITIONS") and toks[j + 1].token_type == TokenType.NUMBER:
        j += 2
    if j < n and toks[j].token_type == TokenType.L_PAREN:
        j, spans = _consume_partition_defs(toks, j)
        if j < 0:
            return -1, []
    return j, spans


def _scan_table_tail(toks, start, want_dialect=True):
    """扫描定义列表收尾右括号之后的**全部** token，直到语句结束。

    第八轮 BLOCK-H1：Rev.H 只在"要剥离方言目标"时才验证表尾；
    UNIQUE-COMMENT 单独恢复路径根本不看表尾，于是 `ENGINE=123`、孤立 `DEFAULT`、
    `PARTITION BY RANGE(,)` 这些与目标无关的非法结构被 sqlglot 静默丢弃后
    仍返回 `exp.Create`，原 E999 消失。现在**只要进入恢复链就必须完整验证表尾**。

    尾部允许（顺序不限，各自必须被完整消费）：
      * 表选项 atom（`_consume_table_option`）
      * 二级分区子句（`_consume_partition_clause`）—— 可能带需掩码的 partition_option
      * **恰好一个**分片声明：
          `TDSQL_DISTRIBUTED BY hash|range|list ( col ) [ (分片定义表) ]`
          `BROADCAST`
        其中 range/list 的分片定义表是 TDSQL 官方形态
        （`tdsql_distributed by range(a) (s1 values less than(100), ...)`）。

    返回 `(方言目标 span 列表, 需掩码的官方语法 span 列表)`；
    任一 token 无法认领即返回 `(None, None)` —— 失败关闭。

    `want_dialect=False` 时只做**验证**、不产生方言 span（供 UNIQUE 单独恢复路径调用）。
    """
    n = len(toks)
    tgt_spans = []
    mask_spans = []
    seen_decl = False
    i = start
    while i < n:
        tt = toks[i].token_type
        if tt == TokenType.PARTITION_BY:
            nxt, msp = _consume_partition_clause(toks, i)
            if nxt < 0:
                return None, None
            mask_spans.extend(msp)
            i = nxt
            continue
        if _is_bare_kw(toks[i], "TDSQL_DISTRIBUTED"):
            if seen_decl:
                return None, None
            if not (i + 1 < n and _is_bare_kw(toks[i + 1], "BY")):
                return None, None
            if not (i + 2 < n and _is_bare_kw(toks[i + 2])
                    and (toks[i + 2].text or "").upper() in _TDSQL_SHARD_METHODS):
                return None, None
            # TDSQL 官方：`TDSQL_DISTRIBUTED BY range|list (column_name)` —— **单列**。
            # 多列分片只有 `shardkey=(a,b)` 一种写法（见 _consume_table_option）。
            # 且 v1.6.1.9 冻结的 _extract_tdsql_hash_key() 也只提取单个分片键。
            j = i + 3
            if not (j < n and toks[j].token_type == TokenType.L_PAREN
                    and j + 2 < n and toks[j + 1].token_type in _IDENT_TOKENS
                    and toks[j + 2].token_type == TokenType.R_PAREN):
                return None, None
            j += 3
            end_tok = j - 1
            if j < n and toks[j].token_type == TokenType.L_PAREN:
                # TDSQL 官方：range/list 分片声明后可直接跟分片定义表
                j2, msp = _consume_partition_defs(toks, j)
                if j2 < 0:
                    return None, None
                mask_spans.extend(msp)
                end_tok = j2 - 1
                j = j2
            tgt_spans.append((toks[i].start, toks[end_tok].end))
            seen_decl = True
            i = j
            continue
        if _is_bare_kw(toks[i], "BROADCAST"):
            if seen_decl:
                return None, None
            tgt_spans.append((toks[i].start, toks[i].end))
            seen_decl = True
            i += 1
            continue
        nxt = _consume_table_option(toks, i)
        if nxt < 0:
            return None, None                # 残缺/未知表选项 → 失败关闭
        i = nxt
    if want_dialect and not tgt_spans:
        return None, None
    return tgt_spans, mask_spans


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


def _scan_definition_list(toks, open_idx, close_idx):
    """普查定义列表的顶层定义项，并顺带收集 UNIQUE COMMENT / ASC-DESC 的可掩码 span。

    第八轮 BLOCK-H1：Rev.H 只验证目标 UNIQUE 自身，其他定义项完全不看；
    `KEY k ()`（空索引）、`id INT,,`（空定义项）被 sqlglot 静默丢弃后仍返回 Create。

    返回 `(顶层定义项数, UNIQUE COMMENT span 列表, 需掩码的官方语法 span 列表)`；
    任一定义项不合规返回 `(-1, [], [])`。

    校验内容：
      * 顶层定义项**不得为空**（拒绝前导/尾随/连续逗号）；
      * 索引类定义项（`[UNIQUE|PRIMARY|FULLTEXT] KEY|INDEX`）的键值列表必须
        由完整 key_part 组成（`_consume_index_key_parts`）；
      * 索引选项区只接受 TDSQL 官方 `USING BTREE` 与 `COMMENT STRING`；
      * 列定义项必须以标识符开头且**带数据类型**。
    """
    n = len(toks)
    ndef = 0
    uq_spans = []
    mask_spans = []
    i = open_idx + 1
    while i < close_idx:
        item_start = i
        # ── 定义项前缀：CONSTRAINT name / UNIQUE / PRIMARY / FULLTEXT / SPATIAL ──
        j = i
        is_index = False
        is_unique_top = False
        if j < close_idx and toks[j].token_type == TokenType.CONSTRAINT:
            j += 1
            if j < close_idx and toks[j].token_type in _IDENT_TOKENS:
                j += 1
        if j < close_idx and toks[j].token_type == TokenType.UNIQUE:
            is_index = True
            is_unique_top = (j == item_start)      # 仅"定义项起点即 UNIQUE"才是目标
            j += 1
        elif j < close_idx and toks[j].token_type == TokenType.PRIMARY_KEY:
            is_index = True
            j += 1
        elif j < close_idx and _is_bare_kw(toks[j]) and (toks[j].text or "").upper() in ("FULLTEXT", "SPATIAL"):
            is_index = True
            j += 1
        if j < close_idx and toks[j].token_type in (TokenType.KEY, TokenType.INDEX):
            is_index = True
            j += 1
        if is_index:
            if j < close_idx and toks[j].token_type in _IDENT_TOKENS:
                j += 1                                    # 索引名（可选）
            if j < close_idx and toks[j].token_type == TokenType.USING:   # index_type 前置
                if not (j + 1 < close_idx and _is_bare_kw(toks[j + 1])
                        and (toks[j + 1].text or "").upper() in _TDSQL_INDEX_TYPES):
                    return -1, [], []
                j += 2
            j2, asc_spans = _consume_index_key_parts(toks, j)
            if j2 < 0:
                return -1, [], []                         # 空/残缺键值列表（BLOCK-G1）
            mask_spans.extend(asc_spans)
            j = j2
            # ── 索引选项区：TDSQL 官方只有 USING BTREE 与 COMMENT ──
            while j < close_idx and toks[j].token_type not in (TokenType.COMMA,):
                tj = toks[j].token_type
                if tj == TokenType.USING:
                    if not (j + 1 < close_idx and _is_bare_kw(toks[j + 1])
                            and (toks[j + 1].text or "").upper() in _TDSQL_INDEX_TYPES):
                        return -1, [], []                 # USING HASH 等 → 失败关闭
                    j += 2
                    continue
                if tj == TokenType.COMMENT:
                    if not (j + 1 < close_idx and toks[j + 1].token_type == TokenType.STRING):
                        return -1, [], []
                    if is_unique_top:
                        uq_spans.append((toks[j].start, toks[j + 1].end))
                    j += 2
                    continue
                return -1, [], []                         # 未知索引选项 → 失败关闭
        else:
            # ── 列定义项：标识符 + 数据类型 + 其余到顶层逗号 ──
            if j >= close_idx or toks[j].token_type not in _IDENT_TOKENS:
                return -1, [], []                         # 空定义项 / 非法起点
            j += 1
            if j >= close_idx or toks[j].token_type == TokenType.COMMA:
                return -1, [], []                         # 列缺数据类型 → 失败关闭
            depth = 0
            while j < close_idx:
                tt = toks[j].token_type
                if tt == TokenType.L_PAREN:
                    depth += 1
                elif tt == TokenType.R_PAREN:
                    depth -= 1
                elif tt == TokenType.COMMA and depth == 0:
                    break
                j += 1
        if j > close_idx:
            return -1, [], []
        ndef += 1
        if j < close_idx and toks[j].token_type == TokenType.COMMA:
            j += 1
            if j >= close_idx:
                return -1, [], []                         # 尾随逗号 → 失败关闭
        elif j < close_idx:
            return -1, [], []                             # 定义项未在逗号处收尾
        i = j
    return (ndef, uq_spans, mask_spans) if ndef else (-1, [], [])


def _plan_recovery(sql: str, dialect: str = "mysql"):
    """统一恢复规划器：一次性验证**整条建表语句**，并给出全部可改写 span。

    这是第八轮 BLOCK-H1 的核心整改——把"目标 UNIQUE 看起来完整"升级为
    "整条 CREATE TABLE 都按 TDSQL 官方语法验证通过"。

    返回 `(表名, 顶层定义项数, uq_spans, dialect_spans, mask_spans)`；
    任一环节不能证明完整时返回 `(None, -1, [], [], [])` —— 失败关闭。

    * `uq_spans`   ：目标 UNIQUE 索引 COMMENT（DEF-2 的修复目标）
    * `dialect_spans`：TDSQL 分片声明（sqlglot 不认的方言）
    * `mask_spans` ：TDSQL **官方合法**但 sqlglot 30.x 解析不了的形态
                     （key_part 的 ASC/DESC、分区定义的 ENGINE/COMMENT 选项）
    """
    try:
        toks = sqlglot.Dialect.get_or_raise(dialect).tokenizer_class().tokenize(sql)
    except Exception:
        return None, -1, [], [], []
    if any(t.token_type == TokenType.SEMICOLON for t in toks):
        return None, -1, [], [], []          # 多语句：调用方已有拆分能力，不猜测
    open_idx, close_idx, table_name = _tdsql_table_def_bounds(toks)
    if open_idx < 0:
        return None, -1, [], [], []
    ndef, uq_spans, mask_a = _scan_definition_list(toks, open_idx, close_idx)
    if ndef < 0:
        return None, -1, [], [], []
    tgt_spans, mask_b = _scan_table_tail(toks, close_idx + 1, want_dialect=False)
    if tgt_spans is None:
        return None, -1, [], [], []
    return table_name, ndef, uq_spans, tgt_spans, (mask_a + mask_b)


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
_PARTITION_PROP_PREFIX = "PartitionBy"


def _had_partition(sql: str, dialect: str = "mysql") -> bool:
    """原文 token 流中是否出现 `PARTITION BY`（供分区保真门禁使用）。"""
    try:
        toks = sqlglot.Dialect.get_or_raise(dialect).tokenizer_class().tokenize(sql)
    except Exception:
        return False
    return any(t.token_type == TokenType.PARTITION_BY for t in toks)


def _validate_recovery_candidate(node, expected_table, ndef, had_partition):
    """候选 AST 结构保真门禁（第八轮 BLOCK-H1 第 4 条）。

    span 门禁只能证明"改写没越界"，证明不了"候选 AST 没有把原文结构静默丢掉"。
    sqlglot 对多种非法结构采取宽松恢复：丢弃后仍返回同表名 `exp.Create`。
    本函数在 span 门禁**之外**再加一道结构校验：

      ① 必须是 `exp.Create` 且 `kind == TABLE`、表名与原文一致；
      ② 候选定义项数必须与原文顶层定义项数**严格相等**（防静默丢定义项）；
      ③ 每个列定义必须有数据类型；每个索引类定义必须有非空键列（防空结构）；
      ④ 原文含 `PARTITION BY` 时，候选必须仍保留分区 property（防静默丢分区）。

    任一条不成立返回 False —— 保持原异常/E999，不得仅凭
    "`exp.Create` + 同表名" 接纳。
    """
    if not isinstance(node, exp.Create):
        return False
    if str(node.args.get("kind") or "").upper() != "TABLE":
        return False
    if not _same_table_name(node, expected_table):
        return False
    schema = node.this
    if not isinstance(schema, exp.Schema):
        return False
    items = list(schema.expressions or [])
    if len(items) != ndef:
        return False                                  # ② 定义项数不符 → 丢结构
    for it in items:                                  # ③ 必要结构非空
        if isinstance(it, exp.ColumnDef):
            if it.args.get("kind") is None:
                return False                          # 列缺数据类型
        else:
            if not list(it.find_all(exp.Column)) and not list(it.find_all(exp.Identifier)):
                return False                          # 空索引 / 空约束
    if had_partition:                                 # ④ 分区必须被保留
        props = node.args.get("properties")
        names = [type(p).__name__ for p in (props.expressions if props else [])]
        if not any(nm.startswith(_PARTITION_PROP_PREFIX) for nm in names):
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
| ⑥ **只在整个索引定义被完整消费之后**才移除 `COMMENT '...'` | 键值列表逐 key-part 消费（`_consume_index_key_parts()`）；选项区只接受 `USING (BTREE\|HASH)` 与 `COMMENT STRING` 两种完整 atom，**其余一律失败关闭**（不是"保留"，是"整体放弃"）；只在 `COMMENT`+`STRING` token 对上记 span |
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
                _tbl2, _ndef2, _uq2, _dia2, _msk2 = _plan_recovery(
                    sql_clean, self.dialect)
                _all2 = list(_uq2) + list(_dia2) + list(_msk2)
                if _tbl2 is not None and _all2:
                    _t_sql = _blank_spans(sql_clean, _all2)
                    if (_t_sql is not None
                            and _spans_only_diff(sql_clean, _t_sql, _all2)):
                        try:
                            _retry_ast = sqlglot.parse_one(_t_sql, read=self.dialect)
                        except Exception:
                            _retry_ast = None
                        if _validate_recovery_candidate(
                                _retry_ast, _tbl2, _ndef2, _had_partition(sql_clean, self.dialect)):
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
            _tbl, _ndef, _uq, _dia, _msk = _plan_recovery(sql_clean, self.dialect)
            _all_spans = list(_uq) + list(_dia) + list(_msk)
            if _tbl is not None and _all_spans:
                _final_sql = _blank_spans(sql_clean, _all_spans)
                if (_final_sql is not None
                        and _spans_only_diff(sql_clean, _final_sql, _all_spans)):
                    try:
                        _cand = sqlglot.parse_one(_final_sql, read=self.dialect)
                    except Exception:
                        _cand = None
                    if _validate_recovery_candidate(
                            _cand, _tbl, _ndef, _had_partition(sql_clean, self.dialect)):
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
| **③b（BLOCK-B1/C1/D1/D2）** | **候选若降级为 `exp.Command`，调用 `_strip_tdsql_dialect_tail()` 再恢复一次**，并把其 span 并入联合门禁。🚫 **不得**使用任何全局正则替换 |
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
> 语法通过、导入自检通过、**H 组 81 例全通过**、**W 组 28 例全通过**、**Z 组 22 例全通过**、
> **Y 组 20 例全通过**、**X 组 40 例全通过**、T/N/C/F 与 6000 条模糊测试逐项相同、
> 专项 **176 passed**、全量回归 **1355 passed / 0 failed / 29 skipped**、
> **上述矩阵在 sqlglot 29.0.0 与 30.14.0 上逐条一致**、
> `grep _tdsql_table_def_bounds` 确认两个剥离器共用同一定位器。Q 可以直接复制粘贴。
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
| 0 | 文件头 import 区 | `from sqlglot.tokens import TokenType`（+1 行） |
| **0b** | 原 `_TDSQL_DIALECT_RE` 处 | **删除**该全局正则及其注释（-14 行） |
| **0c** | 同上位置 | 新增 `_spans_only_diff()` / `_is_bare_kw()` / `_tdsql_table_def_bounds()` / **`_consume_table_option()`** / **`_consume_index_key_parts()`** / **`_consume_partition_clause()`** / `_strip_tdsql_dialect_tail()` / `_same_table_name()`（+403 行，含约 170 行注释）。**加粗三个为消费器，统一契约 `f(toks,i) -> 下一个下标 \| -1`** |
| 1 | 紧随其后 | 新增 `_strip_unique_index_comments()`（+104 行） |
| **2b** | `parse()` 首次 `Command` 重试 | 改用 token 剥离器 + span 校验（v1.6.2.0 代码，NG-4 已撤销） |
| 2 | `parse()` 的 `except` 分支 | 两阶段受限重试 + **联合 span 门禁** |
| 3 | `_parse_index_constraint()` | 类型判据改读 `kind` 白名单映射 |

**产品代码：`parser_legacy.py` 一个文件，`git diff --stat` 实测 `626 insertions(+), 38 deletions(-)`。
fixture 已在 Rev.C 修正。不新增第三方依赖（`TokenType` 来自已在用的 sqlglot），规则层一行不动。**

> 本版改动量明显大于 Rev.C——因为 NG-4 被撤销，v1.6.2.0 的方言处理被纳入修复范围。
> 这是必要的：那段代码**正在生产环境静默破坏审核数据**（§5.14.1）。

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

#### 5.16.3 两个剥离器已合并到同一严格头部定位器

`_strip_unique_index_comments()` 与 `_strip_tdsql_dialect_tail()` 现在都调用
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

本方案删除该正则后，这类语句失败关闭、停在 `Command`。H 组 81 例中：

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
② **始终**完整验证表尾（`_scan_table_tail(..., want_dialect=False)`）；
③ 候选 AST 过 `_validate_recovery_candidate()` 结构保真门禁。

> 🚨 **施工要点**：`_scan_table_tail()` 的 `want_dialect=False` 是"只验证、不产 span"模式。
> 少了它，UNIQUE 单独恢复路径又会回到"表尾不看"的老路——这正是 BLOCK-H1 的本体。

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

#### 5.21.5 已知假阴性（`pos_known`，须单独登记）

| 形态 | 依据 | sqlglot 30.x | Rev.I | 语料/生产出现次数 |
|---|---|---|---|---|
| `VALUES LESS THAN MAXVALUE` | TDSQL/MySQL 官方 | ParseError（去方言后亦然） | **失败关闭，保留 E999** | **0** |

> 这一条**不计入"非法反例"**，而是登记为 TDSQL 合法、本版未支持的已知假阴性（O 的 I-7）。
> 处置是安全的（失败关闭），代价是：同时带 MAXVALUE 兜底分区**与** UNIQUE COMMENT 的表
> 会继续误报 E999。实测语料 197 条与生产 14 表中该组合出现 **0 次**。
> 若后续在目标实例上遇到，需专项处理，不得靠"配平即通过"蒙混。

#### 5.21.6 依赖锁定（MAJOR-H2）

O 指出 `sqlglot>=29,<31` 不是可复现构建，两个端点证明不了区间内所有版本。**成立。**

| 版本 | H 组 85 例 | W/Z/Y/X 矩阵 |
|---|---|---|
| 29.0.0（原下界） | 85/85 | 全通过 |
| **30.14.0（本次全量验证版本）** | 85/85 | 全通过 |
| 30.17.0（当前最新 30.x） | 85/85 | 全通过 |

三版**逐条一致，0 例差异**。据此：

- `requirements.txt` / `pyproject.toml` 均改为**精确锁定 `sqlglot==30.14.0`**；
- 上表作为将来移动 pin 的依据：**换版本必须重跑全部矩阵**，不得只凭区间放行。


### 5.22 全量回归与审核物料校验器

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

**T 组 — TDSQL 方言组合（8 例，对应 §5.9）**：T1 HASH、T2 RANGE、T3 LIST、T4 BROADCAST、
T5 HASH+二级分区、T6 `shardkey=`（对照）、T9 TEMPORARY（集中式）、T10 TEMPORARY（分布式）。
（原 T7/T8 已撤回并由 X 组取代——它们的尾子句写成 `shardkey=`，根本不触发方言处理路径。）

每例断言：解析成功、`columns > 0`、无 E999、`raw_sql` 逐字等于输入，
**且规则命中集合与「同一张表去掉 UNIQUE 索引 COMMENT」完全相等**
——这条相等断言是最强的护栏，它证明恢复**没有引入任何自己的口径**。
T9/T10 额外断言 R032（集中式）与 R024+R032（分布式）仍正常命中。

**X 组 — 方言尾子句安全交叉矩阵（40 例，对应 §5.14，BLOCK-C1，本轮最重要）**：
4 种尾子句（HASH / RANGE / LIST / BROADCAST）× 5 类诱饵（列名为 `` `broadcast` ``、
裸列名 `broadcast`、列注释含 `broadcast`、列注释含伪 `TDSQL_DISTRIBUTED ...`、
`DEFAULT` 值含 `broadcast`）× 带 / 不带 UNIQUE 索引 COMMENT（覆盖**两条**恢复入口）。

每例做**字段级精确断言**，不得退化为"与去掉 COMMENT 的结果相等"这类同源对照：

1. 列名序列 `[c["name"] for c in parsed.columns]` **精确相等**于期望列表；
2. 目标列的 `column_comments[...]` **逐字等于**原文注释；
3. `DEFAULT` 值保持；
4. `parsed.raw_sql` 逐字符等于输入。

> ⚠️ 这 40 例中有 **36 例在当前生产版本 v1.6.2.1 上是失败的**，务必确认它们在你的实现上全绿。

**Y 组 — 方言语法严格性与语句边界（20 例，对应 §5.15，BLOCK-D1/D2）**：

| 子组 | 用例 | 断言 |
|---|---|---|
| Y1~Y5 | 缺 BY / 缺方法 / 缺 BY 有方法 / 未知方法 / 缺括号 | **span == 0**，且最终**仍报原错误**（不得被修成合法） |
| Y6~Y8 | `'TDSQL_DISTRIBUTED'`（字符串）/ `` `TDSQL_DISTRIBUTED` ``（反引号）/ `` `broadcast` `` | **span == 0** |
| **Y9~Y10** | **`COMMENT='TDSQL_DISTRIBUTED'` / `COMMENT='BROADCAST'` + 真实尾子句** | **span == 1 且正常恢复**（表注释不得阻断真实尾子句） |
| Y11~Y12 | `HASH+BROADCAST` / `HASH+RANGE` 双声明 | **span == 0** |
| **Y13~Y15** | **CTAS（含函数括号）/ `CREATE TABLE ... LIKE` / 两条语句拼接** | **span == 0**；CTAS 的 SELECT 列与真实尾子句**都不得被改** |
| **Y16~Y19** | **合法 HASH / RANGE / LIST / BROADCAST 四种形态**（逐条一例） | **span == 1 且解析成功**——⚠️ 这四条防的是"收紧过头"，RANGE/LIST 在实现中回归过一次 |
| **Y20** | **反引号列名 `` `broadcast` `` + 真实 HASH 尾子句** | **span == 1 且解析成功**（诱饵列名不得阻断真实尾子句） |

**Z 组 — 方法参数与表名精确形态（22 例，对应 §5.16，BLOCK-E1/E2）**：

| 子组 | 用例 | 断言 |
|---|---|---|
| Z1（7 例） | `HASH()` / `HASH(,)` / `HASH('id')` / `HASH(id+1)` / `HASH(lower(id))` / `HASH(a,b)` / `HASH("id")`，**均带 UNIQUE COMMENT** | **span == 0 且最终 `ast is None` + `E999_SYNTAX_ERROR`**。⚠️ 该断言**只对带 UNIQUE COMMENT 的路径成立**：同样输入不带 UNIQUE COMMENT 时主干本就是 `exp.Command`、**根本没有 E999**，此时应断言"仍是 `Command`、不得升级为 `Create`"（第七轮 MAJOR-G1） |
| Z2（8 例） | `HASH/RANGE/LIST` ×（反引号 / 裸名）、`BROADCAST`、`BROADCAST COMMENT='x'` | **span == 1 且解析成功**（防收紧过头） |
| Z3（3 例） | 单引号表名 / 双引号表名 / 单引号表名+HASH，均带 UNIQUE COMMENT | **仍报 E999** |
| Z4（4 例） | 裸表名 / 反引号表名 / 库限定 `` `db`.`t` `` / `IF NOT EXISTS` | **解析成功且 `cols>0`** |

> ⚠️ Z1/Z3 的断言必须包含"**仍报 E999**"，只断言 `span==0` 不够——
> Rev.E 正是在 `span` 层面看着正常、却在最终结论上吞掉了 E999。

**W 组 — 目标上下文完整性（28 例，对应 §5.17，BLOCK-F1/F2）**：

| 子组 | 例数 | 用例 | 断言 |
|---|---:|---|---|
| W1 | 12 | 3 类残缺选项（`DEFAULT`/`CHECKSUM`/`INDEX DIRECTORY`）× 2 类目标 × 2 条路径 | **span==0**；带 UNIQUE COMMENT → 最终 `ast is None`（E999 保留）；不带 → 最终仍 `exp.Command`（**不得升级为 `Create`**） |
| W2 | 8 | 完整表选项正例 + 生产同款全套组合 | **span==1 且最终 `exp.Create`** |
| W3 | 3 | `USING COMMENT` / `COMMENT ... USING` / `COMMENT` 后非字符串 | **span==0 且 E999 保留** |
| W4 | 2 | `USING BTREE COMMENT` / 纯 `COMMENT` 正例 | **span==1 且 `exp.Create`** |
| W5 | 1 | `HASH + 二级 PARTITION BY`（既有 D5 场景） | `cols > 0`，不回归 |
| W6 | 2 | `INDEX DIRECTORY='/p'` 完整形态 × 带/不带 UNIQUE COMMENT 两条路径 | **span==0 且两条路径均 `ast is None` + E999**（与主干逐条一致：sqlglot 本就不支持 `INDEX DIRECTORY`）。属白名单外的保守取舍，须在注释写明 |

> ⚠️ **W1 是本组的关键**：这 12 例在 **Rev.F 上全部失败**。
> 且**必须按路径分别断言最终 AST 类型**，不能统一写成"应报 E999"。

**H 组 — 语法单元内部结构完整性（81 例，对应 §5.19，BLOCK-G1/G2/G3）**：

⚠️ **本组不手写期望值。** 先在主干上跑一遍记录每例的 `(ast 类型, 是否 E999)`，
再用**单调不变松**判据比对候选：

```text
rank: NoneType/E999 = 0  <  Command = 1  <  Create = 2
neg       （非法 DDL）        ：rank(候选) <= rank(主干)，且主干的 E999 不得消失
pos       （TDSQL 官方合法）  ：候选必须是 Create
pos_known （TDSQL 官方合法、
           sqlglot 暂不支持） ：必须失败关闭（与主干同结论），**单独计数登记**
```

> **`pos_known` 是 Rev.I 新增的第三类（第八轮 MAJOR-H1 / O 的 I-7）**：
> 不能把"TDSQL 合法但我们暂时做不到"和"非法 SQL"混在同一个 neg 口径里，
> 更不能据此声称"合法形态 0 例收紧"。它必须作为**有账可查的已知假阴性**单独登记。

| 子组 | 例数 | 用例 | 类别 |
|---|---:|---|---|
| **H0** | 14 | **第八轮 BLOCK-H1/H2/H3 原始反例**：`ENGINE=123` / 空普通索引 / 重复逗号 / 孤立 `DEFAULT` / `RANGE(,)` / 列缺类型 / 空主键 / `RANGE(+)` / `RANGE(id,)` / `USING HASH` 等 | neg |
| **H1** | 11 | 空清单 / 只有逗号 / 前导、尾随、连续逗号 / 字符串键 / 数字键 / 函数键 / 表达式键 / 前缀长度非数字 / 前缀括号未闭合 | neg |
| **H7** | 10 | **TDSQL 官方合法形态**：官方 RANGE/LIST + 分片定义表、`PARTITION BY` 在前方言在后、多列 `shardkey=(a,b)`、`shardkey=noshardkey_allset`、`USING BTREE` 等 | **pos** |
| **H2** | 5 | 裸列名 / 反引号列 / 多列 / 前缀索引 / 前缀+多列 | **pos** |
| **H2b** | 3 | `ASC` / `DESC` / 前缀+DESC+多列 —— **TDSQL 官方 key_part 含 `[ASC\|DESC]`** | **pos**（Rev.I 起必须恢复） |
| **H3** | 16 | 8 类残缺分区尾巴 × 带/不带 UNIQUE COMMENT 两条路径 | neg |
| **H4** | 6 | TDSQL 官方二级分区：`RANGE`+分区定义表 / `LIST`+分区定义表+partition `ENGINE` / `LIST`+多值 `VALUES IN` × 两条路径（**D5 不得回归**） | **pos** |
| **H4b** | 8 | `HASH+PARTITIONS n` / `LINEAR HASH` / `KEY(col)` / `RANGE COLUMNS` × 两条路径 —— **TDSQL 官方二级分区文档只列 Range 与 List**，这四种保守失败关闭 | neg |
| **H4c** | 2 | `VALUES LESS THAN MAXVALUE` × 两条路径 —— **TDSQL 官方合法，sqlglot 30.x ParseError** | **pos_known**（须失败关闭，单独登记） |
| **H5** | 22 | 11 类非法选项取值 × 两条路径 | neg |
| **H6** | 12 | 12 类合法选项取值（含生产同款全套组合） | **pos** |

> 🚨 **H4 是本组最容易做错的一条**：`PARTITION BY LIST (...) (PARTITION p1 VALUES IN (1) ENGINE = InnoDB)`
> 看起来和 `RANGE` 正例同构，但**实测 sqlglot 自身即 ParseError**，必须归入 H4b。
> 我第一版把它写进 H4，跑出 2 个红。**归类前先量 sqlglot 的原生能力，不要看语法像不像。**

> 🚨 **H3/H5 的"无 UK"路径不要断言"与主干相同"**：主干在这条路径上的 `Create`
> 有 14 例是旧正则对非法 DDL 的假成功，候选降为 `Command` 是**预期收紧**（§5.19.4）。
> 用上面的 rank 判据就不会踩这个坑。


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

**合计 245 例，要求零 skip。**

> **计数以逐条参数化 case 为唯一来源**（O MAJOR-F1）：
> A9 + B12 + C4 + D6 + E4 + F2 + T8 + N5 + X40 + **Y20**（Y1~Y15 + **Y16~Y19 四种合法形态逐条** + **Y20 诱饵列名**）+ **Z22**（Z1 7 + Z2 8 + Z3 3 + **Z4 4**）+ **W28**（W1 12 + W2 8 + W3 3 + W4 2 + W5 1 + W6 2）+ **H85**（H0 14 + H1 11 + H2 5 + H2b 3 + H3 16 + H4 6 + H4b 8 + H4c 2 + H5 22 + H6 12 + H7 10 —— 其中 H0 与 H7 为第八轮新增，H4/H4b/H4c 按 TDSQL 官方语法重排，故子组之和以**逐条 case 清单**为准）= **245**。
> §7.1、G-1、G-5、G-19、G-21、C-11 与附录必须同源于这一张明细表，不得各写各的。

### 7.2 需修订的既有测试

**预期为无。** 实测全语料除两条目标 fixture 外零漂移、全量回归零变化。
若施工中出现既有测试失败，**停工复核**，不得改测试迁就代码。

### 7.3 回归门槛（准出条件）

| 门槛 | 要求 |
|---|---|
| G-1 | `pytest tests/` 全量：**1355 passed / 0 failed / 29 skipped**（+新增 245 例 → 1600 passed），无既有用例由通过转失败 |
| G-2 | `test_r077_r054_tdsql_syntax.py` **45 passed** |
| G-3 | `test_parser_tdsql_dialect_fallback.py` **14 passed** |
| G-4 | `test_r061_index_name_quoting.py` **12 passed** |
| G-5 | 新增 `tests/test_parser_index_type_and_uk_comment.py` **245 例全通过，零 skip** |
| G-6 | `verify_rules.py`：119 / 107 / 未覆盖 0 / 断言失败 **3**（与基线同名同因） |
| G-7 | 全语料 197 条 × 119 规则：**恰好 2 条变化**，且均为两个目标 fixture；其余 195 条零漂移 |
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
| **G-20** | **两个剥离器共用 `_tdsql_table_def_bounds()`**；`grep` 确认代码中不存在第二套建表头部定位逻辑 |
| **G-21** | **W 组 28 例全通过**；W1 必须按路径分别断言最终 AST 类型 |
| **G-23** | **H1 11 例 + H2 5 例**：非法 key-part 全部保住主干结论；合法 key-part 全部恢复为 `Create`（BLOCK-G1） |
| **G-24** | **H3 16 例 + H4 2 例**：残缺/尾随垃圾/内藏声明的分区子句全部失败关闭；**D5 的 `RANGE`+分区定义表两条路径仍 `Create`、`cols=3`**（BLOCK-G2） |
| **G-25** | **H5 22 例 + H6 12 例**：`ENGINE=123` / `ROW_FORMAT=123` 等非法取值全部失败关闭；12 类合法取值全部恢复（BLOCK-G3） |
| **G-26** | **H 组 81 例在 sqlglot 29.0.0 与 30.x 上结果逐条一致**（依赖矩阵，对应 O 的 H-5） |
| **G-27** | 五个消费器统一契约 `f(toks,i) -> 下一个下标 \| -1`；静态检查断言**扫描循环内不存在"看不懂就跳过"分支**、无重复函数定义、无不可达语句 |
| **I-1** | 第八轮 H1-1 ~ H1-5（外加我方补充的列缺类型、空主键）**全部保留原 E999**，不得变成 `Command`/`Create` |
| **I-2** | `USING HASH COMMENT` 按 TDSQL 官方口径失败关闭；`USING BTREE COMMENT` 正常恢复 |
| **I-3** | `PARTITION BY RANGE(,)` / `RANGE(+)` / `RANGE(id,)` 及分区定义结构反例全部失败关闭 |
| **I-4** | 进入恢复的语句，**原顶层定义项数 == 候选 AST 定义项数**；列类型与索引键列不得为空 |
| **I-5** | 原文存在 `PARTITION BY` 时，候选 AST 必须保留分区 property（`PartitionBy*`） |
| **I-6** | UNIQUE-COMMENT 单独路径、HASH 路径、BROADCAST 路径、Range/List **双子句顺序**路径均覆盖 |
| **I-7** | `ASC/DESC`、官方 LIST + partition `ENGINE`、官方 RANGE/LIST 分片定义表、多列 `shardkey=(a,b)` **按 pos 断言必须恢复**；`MAXVALUE` 按 `pos_known` 单独登记，**不得归入非法 neg** |
| **I-8** | TDSQL 官方二级分区示例进 fixture，并记录适用 TDSQL 内核版本 |
| **I-9** | 实际发布版本 `sqlglot==30.14.0` 通过全部新增专项、既有 71 例、全量 tests、生产 fixture 与语料漂移；29.0.0 / 30.17.0 作为对照实测记录 |
| **I-10** | 两个用户报告 fixture 仍达预期，规则集合继续用**精确相等**断言 |
| **G-22** | **代码中不存在"跳过未知 token"分支**：两个剥离器的选项扫描循环里，未被白名单消费的 token 必须导致 `return None`；`grep` 确认无裸 `i += 1` 兜底 |
| **G-11** | **模糊测试（O §6.4-5）**：对 `_strip_unique_index_comments()` 随机组合引号、括号、逗号、注释、转义生成 ≥2000 条输入，断言**不抛异常**，且凡返回非 `None` 者必满足「长度恒等 + 差异全在 span 内」 |
| **G-12** | 提交说明记录实际 `sqlglot.__version__` |

## 8. 风险与回滚

| 风险 | 等级 | 说明与缓解 |
|---|---|---|
| **改坏字符串字面量内容（Rev.A 的 BLOCK-1）** | **已消除** | 词法器令伪 SQL 结构上不可见；门禁①逐字符校验；6 例负向用例 + 4000 条模糊测试越界改写均为 0 |
| 接纳了不该接纳的候选 AST | **中→低（Rev.H 关闭）** | AST 门禁是**最后防线，不能替代 token 语法完整性**——第六、七轮连续证明目标片段合法、AST 门禁全过，语句整体仍可能非法。现由五个消费器在 token 层先行把关（表选项 / 索引选项 / 键值列表 / 分区子句 / 方言尾子句），门禁只做兜底。H 组 81 例锁定 |
| 吃掉真语法错误 | **中→低（Rev.H 关闭）** | 第六轮（BLOCK-F1/F2）与第七轮（BLOCK-G1/G2/G3）各查出一批 `E999→Create`，说明此前的"低"评级证据不足。现由 W 组 28 例 + H 组 81 例双版本锁定，判据为「rank(候选) ≤ rank(主干) 且 E999 不得消失」。边界见 §5.7 末尾与 §5.19 |
| 合法但 sqlglot 不支持的语法仍误报 | **已知边界** | §5.4 三类，显式声明为产品边界，失败关闭，不用字符串兜底伪造事实 |
| sqlglot 升级导致 AST 假设失效 | **中→低** | 白名单映射不会静默降级；A9 契约测试在升级时显式失败；§5.0 记录版本 |
| 丢失真索引类型 | **低** | A5 锁定真 FULLTEXT |
| 告警数量变化引发用户疑虑 | **需沟通** | gg78 由 5 条 ERROR 变为 2 条 INFO；gg77 少 1 条 WARNING。减少的**全部是误报**，另有 1 处漏报被补上 |
| **UNIQUE-COMMENT 与 TDSQL 方言组合仍失败** | **已消除** | 方言恢复串联；T1~T6 实测全部恢复 |
| **方言全局正则静默破坏 AST（BLOCK-C1）** | **已消除，且顺带修好一个生产在跑的缺陷** | 删除 `_TDSQL_DIALECT_RE`；两条入口统一 token 剥离器；X 组 40 例字段级精确断言全过（生产版本 36 例失败） |
| **sqlglot 版本漂移致 T5 失效** | **已决并纳入改动** | 实测下界 29.0.0；`requirements.txt` / `pyproject.toml` 均**精确锁定** `sqlglot==30.14.0`（§5.21.6、C-19、G-18、I-9） |
| **span 被错误批准（作用域越界）** | **已消除** | `at_def_start` + 定义列表闭合即停；5 类作用域负例 span 全为 1 |
| `UnboundLocalError` | **已知陷阱** | §3.2 红框 |

**回滚**：5 个文件（解析器 1 + 依赖声明 2 + 版本号 2）、5 个解析改动点，`git revert` 单个 commit 即可完全回退。
无数据迁移、无配置变更、无接口变更、无前端联动。

---

## 9. 施工检查单（Q 逐项打勾）

- [ ] **C-1** 产品代码改 `backend/engine/parser/parser_legacy.py`（5 个改动点）+ `requirements.txt` / `pyproject.toml` 各 1 行依赖 pin + `VERSION` 与 `backend/config.py` 版本号（C-16），**共 5 个文件**
- [ ] **C-2** 五个改动点（含 import、删除旧正则）均按 §3 逐字落地，未做自由发挥
- [ ] **C-3** **Rev.A 的 `_UNIQUE_IDX_COMMENT_RE` 不得出现在代码中**（NG-0）——本次不是「改正则」，是「换实现」
- [ ] **C-4** ⚠️ 重试成功分支**同时**执行 `ast = _retry_ast` 与 `parsed.ast = ast`（§3.2 陷阱）
- [ ] **C-5** 失败路径（`else` 分支内）与改前**逐字一致**，仅整体缩进一层
- [ ] **C-6** 五道门禁一个不少（等长+差异仅在 span、`exp.Create`、`kind=='TABLE'`、表名同一性、**TDSQL 方言恢复串联**）
- [ ] **C-6b** 剥离器入口接受 `CREATE [TEMPORARY] TABLE`；`at_def_start` 状态存在且正确；定义列表闭合即 `break`
- [ ] **C-6c** **`_TDSQL_DIALECT_RE` 已删除**；两条恢复入口（首次 `Command` 重试、`except` 重试）**都**改用 `_strip_tdsql_dialect_tail()` + `_spans_only_diff()`
- [ ] **C-6d** except 路径做的是**两阶段 span 联合门禁**（`sql_clean → _final_sql` 的全部差异落在 `_all_spans` 内），不是只校验阶段一
- [ ] **C-7** 剥离器在词法异常 / 括号未闭合 / 非建表 / 无 span / span 越界时一律返回 `(None, [], "")`
- [ ] **C-8** 未新增第三方依赖；只新增 `from sqlglot.tokens import TokenType`
- [ ] **C-9** 规则层零改动：`ddl.py`/`index.py`/`distributed.py`/`dml.py`/`oracle_compat.py`
- [ ] **C-10** ✅ **确认 `_TDSQL_DIALECT_RE` 常量已从代码中删除**（仅允许出现在解释性注释里）；`_parse_unique_constraint()` 一字未动
- [ ] **C-11** 新建 `tests/test_parser_index_type_and_uk_comment.py`，覆盖 §7.1 A~F+T+N+X+Y+Z+W+**H** 共 **245 例**，零 skip（计数以逐条 case 明细为准）
- [ ] **C-12** F 组**原样读取**已提交的两个纯 DDL fixture（**不要过滤注释行**），6309 用**分布式**、6311 用**集中式**，且用**精确集合相等**断言
- [ ] **C-12b** **不得**给这两个 fixture 重新添加任何文件头注释
- [ ] **C-13** 未修改任何既有测试文件；若确需修改，**停工回报**
- [ ] **C-14** G-1 ~ G-27 与 I-1 ~ I-10 全部门槛逐条实测通过，提交说明中贴出实测数字
- [ ] **C-19** **依赖 pin**：`requirements.txt` 与 `pyproject.toml` 的 `sqlglot` 声明改为 **`sqlglot==30.14.0`**（精确锁定，第八轮 MAJOR-H2），并在提交说明记录打包 wheel 实际版本
- [ ] **C-20b** ⚠️ **两个剥离器必须调用同一个 `_tdsql_table_def_bounds()`**，不得各写一套头部定位
- [ ] **C-20** ⚠️ **确认 `BY RANGE(...)` / `BY LIST(...)` 仍能恢复**——严格化时若写成"只认 `TokenType.VAR`"，这两种会静默回归（我实现时踩到过）
- [ ] **C-15** 导入自检：`python -c "from backend.engine.parser.parser_legacy import SQLParser, _strip_unique_index_comments"` 无异常
- [ ] **C-16** 版本号更新：`VERSION` 与 `backend/config.py` 的 `APP_VERSION`、`APP_DESCRIPTION` → `1.6.2.2`
- [ ] **C-17** 提交说明记录实际 `sqlglot.__version__`
- [ ] **C-18** 提交信息：`fix(v1.6.2.2): 索引类型误判与唯一索引注释解析崩溃修复`

---

## 附录 A：实测证据清单（Rev.I）

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
| **A-65** | 两个剥离器头部定位器合并 | 均调用 `_tdsql_table_def_bounds()`，代码中不存在第二套头部逻辑 |

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
| **A-88** | Rev.H H 组 81 例（sqlglot 30.14.0） | **失败 0**；其中较主干**收紧 14 例**（非法 DDL 由假 `Create` 降为 `Command`），**覆盖面损失 0 例** |
| **A-89** | Rev.H H 组 81 例（sqlglot 29.0.0，依赖下界） | **失败 0，与 30.14.0 逐条一致**（收紧同样 14 例）—— 满足 O 的 H-5 门禁 |
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
| **A-107** | `MAXVALUE` 的处置依据 | `VALUES LESS THAN MAXVALUE` 在 sqlglot 30.x 上 ParseError（去方言后亦然）；语料 197 条与生产 14 表中出现 **0 次** → 登记为 `pos_known` 已知假阴性，失败关闭 |
| **A-108** | Rev.I H 组 85 例 | **失败 0**：14 例第八轮原始反例全部保留 E999；10 例 TDSQL 官方形态全部恢复；2 例 `pos_known` 单独登记；14 例较主干收紧（旧正则假成功） |
| **A-109** | **依赖三版矩阵**（MAJOR-H2） | 29.0.0 / 30.14.0 / 30.17.0 上 H 组 85 例与 W/Z/Y/X 矩阵**逐条一致，0 例差异** → 依赖改为**精确锁定 `sqlglot==30.14.0`**，三版记录作为将来移动 pin 的依据 |
| **A-110** | Rev.I 对前七轮全部矩阵复跑（三版本） | W 28 例、Z 22 例、Y 20 例、X 40 例、T/N/C/F、模糊 6000 条（0 崩溃、0 不变量违例）**全部保持通过** |
| **A-111** | Rev.I 生产 14 表 + 全语料 197 条 + 两份 fixture | 14 表**零漂移**；语料**恰好 2 条**变化（均为目标 fixture）；**与 Rev.H 逐键完全一致** —— 本轮整改只作用于非法输入与此前被误拒的官方形态 |
| **A-112** | Rev.I 全量回归 | **1355 passed / 0 failed / 29 skipped**，与主干逐条相同 |

---

## 附录 B：给智能体 Q 的二十句话

1. **本次不是"把正则改好"，是"把正则换掉"。** Rev.A 的 `_UNIQUE_IDX_COMMENT_RE` 必须**整体删除**，
   不要保留任何跨语义边界的正则改写（NG-0）。
2. **`at_def_start` 那个状态是这一版的核心，不能省。** 少了它，`CONSTRAINT x UNIQUE (...)`、
   列内联 `UNIQUE`、定义项中部的 `UNIQUE` 都会被错误地当成目标——Rev.B 就是这么被打回来的。
   **span 门禁只能自证「改动落在自己声明的范围内」，证明不了「这个范围是对的」。**
3. **方言恢复必须串联，但必须走新的 token 剥离器。**
   🚫 **绝对不要恢复 `_TDSQL_DIALECT_RE`，也不要另写任何全局正则**——那条正则正在生产环境
   静默删列、篡改注释（§5.14.1）。串联的是 `_strip_tdsql_dialect_tail()`，并把它的 span
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
   索引选项区只接受 `USING (BTREE|HASH)` 与 `COMMENT STRING`。
   **凡有一个 token 认领不了，整个函数 `return None, [], ""` / 放弃剥离。**
   宁可不修（保持原结论），也绝不在没看懂上下文的情况下动刀——
   前五轮被打回，根子都在"目标 token 序列对了就动手"。
9. **W 组的期望值必须逐路径量取主干，不能写"一律 E999"。**
   同一批输入在 v1.6.2.1 上有三种结局：`Command`（无语法错，sqlglot 不认方言）、
   `Create`（sqlglot 自己就能解析）、E999。**先跑主干记录，再拿它当期望**——
   我上一版就是凭印象写"一律 E999"，自己把自己的复评带偏了 7 例。

10. **五个消费器是一套东西，契约必须一致：`f(toks, i) -> 下一个下标 | -1`。**
    `_consume_table_option()` / `_consume_index_key_parts()` / `_consume_partition_clause()`
    各管一段，`_strip_tdsql_dialect_tail()` 与 `_strip_unique_index_comments()` 只负责
    **组合它们 + 记录目标 span**，不要在外层再写局部语法判断——那正是前七轮反复出问题的地方。
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
16. **`_scan_table_tail(..., want_dialect=False)` 那个参数不能省。**
    它是"只验证、不产 span"模式，让 UNIQUE-COMMENT 单独恢复路径也必须完整验证表尾。
    少了它就退回 BLOCK-H1 的老路：`ENGINE=123`、孤立 `DEFAULT` 又会被静默放行。
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
