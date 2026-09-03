# REVIEW1-v1.6.3.2 审核规则调整与扫描历史跨页对比设计说明书 第一轮评审报告

| 项 | 内容 |
|---|---|
| 被评审文档 | `docs/DESIGN-v1.6.3.2-审核规则调整与扫描历史跨页对比详细设计说明书.md`（Rev.A，提交 `d59c7f0`，944 行） |
| 编写方 | 智能体 O |
| 评审方 | 智能体 A |
| 评审轮次 | 第一轮设计评审 |
| 评审日期 | 2026-09-03 |
| 代码基线 | `main` / `d59c7f0`（文档锚定 `03ac422`，两者之间只有本设计文档一个提交，代码面完全一致） |
| 评审方式 | 逐条实证核验：所有"当前实现"断言均在本机跑代码复现，不以读文档代替 |
| **评审结论** | **不通过（有条件）。2 项 P1、7 项 P2、5 项 P3。P1 关闭后可进入编码。** |

---

## 1. 评审结论摘要

### 1.1 问题清单

| 级别 | 编号 | 问题 | 影响 |
|---|---|---|---|
| **P1** | **P1-01** | **R121 的 bare `MAXVALUE` 形态在现有管线上不可达**：阻断点在 sqlglot 上游 ParseError，而 §5.4 只要求扩展 token 状态机，只解决了一半 | **照本设计施工后，R121 对 TDSQL 官方文档正文书写的 `VALUES LESS THAN MAXVALUE` 永远不会命中**，语句仍停在 E999。REQ-07 的主场景落空 |
| **P1** | **P1-02** | **v14 迁移建立在错误前提上**：`rule_configs` 是**只写表**，全仓库无任何读取方（唯一读取是冒烟脚本的 `COUNT(*)`），§3.2 声称的"运行时 API 与数据库目录两套文案"对用户不可见、不可达 | 为零收益引入一个**checksum 冻结、不可回改**的迁移件；且迁移会写 `severity`，正是未来管理员覆盖语义要占的字段，属提前占位 |
| P2 | P2-01 | §4.6.3 判定表 `LIMIT 2000 OFFSET 1 → WARNING` 实测不成立：sqlglot 直接 ParseError，结果是 E999 | §10.1 要求"按判定表逐项参数化"，该行会把新测试写红；实施者可能反向去"修好"解析器，反而放行 MySQL 本就非法的语法 |
| P2 | P2-02 | §9 实施文件清单遗漏 5 处硬编码 `119`，其中含**部署门禁的硬等值判断** | `deploy/verify_deploy.sh:50` 是 `[[ "$TOTAL" == "119" ]]`，改动后**每次部署冒烟必失败**；另有 3 个测试文件的硬断言未列入 |
| P2 | P2-03 | R011/R120 扩展到 `ALTER ADD/MODIFY/CHANGE` 属需求外扩围，且需在 `parser_legacy.py` 新增整条通道 | 实测 ALTER 当前 `columns`/`column_types`/`alter_actions` **全为空**；这是本项目缺陷密度最高的文件，扩围未追溯到任何 REQ 编号 |
| P2 | P2-04 | R035 的"跨表上下文构造"同属需求外扩围，§9.1 仅用一句话带过 | 这是本次第二大工作量（checker + audit_service），但没有落点函数、生命周期、与规则集/适用域过滤的先后关系，也没有降级方案 |
| P2 | P2-05 | §4.1.2/§4.2.2/§4.5.2 反复要求"解析器的规范化基础类型"却始终不指名字段 | 实测 `parsed.columns[i]["type"]` 已经完全满足 O 列出的全部正反例；不指名会导致实施者另造一套归一器，凭空增加缺陷面 |
| P2 | P2-06 | 质量门禁的**双向**变化未量化 | R011 降 INFO 使 strict 策略下"含 TEXT 即卡门禁"变放行；R120 升 ERROR 使 normal 策略下"LOB 仅 WARNING"变直接卡死。RISK-10 只提到"新规则默认开启"，两个方向都没写 |
| P2 | P2-07 | 风险登记缺"集中式零覆盖"这一面 | R030+R032 同时转 DISTRIBUTED 后，**集中式实例上"视图/存储过程/触发器"与"临时表"两类治理项将从有覆盖变为完全无覆盖**（已穷举确认无其他规则接管） |
| P3 | P3-01 | `LIMIT 1, 2000` 的实现陷阱未写明：sqlglot 把 count 放进 `limit.expression`（实测=2000），只读该字段会误判"通过" | 与 §4.6.3 期望的 WARNING 相反 |
| P3 | P3-02 | 现有 `limit_offset` 就是全文正则实现，与 §5.1"禁止全文正则即语义"直接冲突，未登记为既有例外 | 实施者可能顺手"统一"R114，超出本期范围 |
| P3 | P3-03 | §7 未点明四张表**共用同一个 `ref="cmpTableRef"`** | §7.4 要求"调用当前表格实例的 clearSelection()"，实施者需要知道该 ref 是复用的且可能已随页面卸载 |
| P3 | P3-04 | §7.4"退出登录/切换用户清空"没有落点 | 现有 `doLogout` 完全不触碰 `cmpState`，这是需要新增的清理点，不是既有行为 |
| P3 | P3-05 | §10.5 的 Playwright 行为测试称"复用项目 dev extra 中已固定的浏览器测试依赖" | 仓库当前**没有** Playwright 依赖声明（本机是我在 SIT 阶段临时 `pip install` 的），该前提不成立，需要先补依赖与夹具 |

### 1.2 一句话结论

**设计的骨架是对的、勘查是扎实的、数量口径是准确的**（§3 全部行号、§6.2 全部计数我逐条实测复核，无一处错误）。
问题集中在两点：**一个技术阻断点没有被识别到底**（P1-01，卡在 sqlglot 而不是 token 状态机），
**一个改动被论证错了必要性**（P1-02，为只写表加不可回改的迁移）。
其余 P2 主要是"需求外扩围没有追溯"和"清单不全"，都可在文档层面一次性补齐。

---

## 2. 评审方法

本轮不做"读文档挑措辞"式评审。设计中每一条"当前实现"断言，我都在本机跑代码复现，
证据全部来自本次会话的实际执行输出：

| 手段 | 用途 |
|---|---|
| 直接跑 `SQLParser().parse()` | 验证 MAXVALUE、LIMIT、列类型归一的真实行为 |
| 直接跑 `sqlglot.parse_one()` | 定位阻断点究竟在项目代码还是上游库 |
| 实例化 `ALL_RULE_CLASSES` 统计 | 验证 §6.2 的全部数量口径 |
| 全仓库 grep（含前端、脚本、部署件） | 验证 §9 文件清单的完整性、`rule_configs` 的消费者 |
| 逐行核对 O 引用的行号 | 验证勘查是否可信 |

---

## 3. 核验通过的部分

这部分先讲清楚，避免整改时把对的地方也一起改了。

### 3.1 §3 现状勘查：全部行号在当前 HEAD 仍然准确

| O 的引用 | 实测该行内容 | 结论 |
|---|---|---|
| `ddl.py:209` R011 | `class R011TextBlobType(BaseRule):` | ✅ |
| `ddl.py:389` R030 | `class R030NoViewProcTrigger(BaseRule):` | ✅ |
| `ddl.py:488` R035 | `class R035CrossTableFieldType(BaseRule):` | ✅ |
| `distributed.py:587` R058 | `class R058BatchUpdateLimit(BaseRule):` | ✅ |
| `parser_legacy.py:1026` 分区值 | `def _consume_partition_values(...)` | ✅ |
| `parser_legacy.py:2199` | `limit_offset: int = -1` | ✅ |
| `base.py:24` 默认适用域 | `# V1.5：实例类型适用域。默认 ALL 是保守取向——` | ✅ |
| `index.html:492/714/1279/1514` 四张对比表 | 四处均为 `<el-table ref="cmpTableRef" :data="cmpState.list" ...>` | ✅ |

`03ac422 → d59c7f0` 之间只有本设计文档一个提交，代码面零变化，故 §3 的行号免责声明本轮不必动用。

### 3.2 事实性断言逐条复核

| O 的断言 | 实测 | 结论 |
|---|---|---|
| R011 当前 9 种类型、级别 WARNING | `LARGE_TYPES` = TEXT/TINYTEXT/MEDIUMTEXT/LONGTEXT/BLOB/TINYBLOB/MEDIUMBLOB/LONGBLOB/JSON；`severity=WARNING` | ✅ |
| R030/R032 未声明适用域，继承 ALL | 实例化后 `scope=all` | ✅ |
| R035 比较 `raw_type` | `if col_name in existing and existing[col_name] != new_type`（`new_type = col.get("raw_type")`） | ✅ |
| R058 只用 `"limit" in raw_sql.lower()` | 第 615 行确为 `if "limit" not in raw_lower:` | ✅ |
| `ParsedSQL` 只有 `limit_offset`、无行数 | 实测 `LIMIT 2000` → `limit_offset=-1`；`LIMIT 100000, 10` → `limit_offset=100000`（存的是 **offset**，服务于 R114 深分页） | ✅ |
| 二级分区状态机遇 bare MAXVALUE 直接失败 | 第 1039-1040 行 `return -1, ""  # KFN-1` | ✅ |
| R035 的 `existing_columns` 无任何产品路径构造 | 全仓库仅 `ddl.py:501` 一个读取方，其余命中全在 `docs/evidence/` 的取证脚本 | ✅ |
| 四张表都没有 `row-key`、四个 selection 列都没有 `reserve-selection` | 493/715/1280/1515 四行均为裸 `<el-table-column type="selection" width="40">` | ✅ |
| `scan_snapshots.id` 是 BIGINT 主键且列表 API 返回 | 建表脚本 `id BIGINT PRIMARY KEY AUTO_INCREMENT`；`_LIST_COLUMNS` 首字段即 `id` | ✅ |
| 翻页只调 `loadSnapshots()`、不清选择 | `@current-change="loadSnapshots('...')"`，四处一致 | ✅ |
| `init_rule_configs` 用 `INSERT IGNORE` | 第 1824 行确认；且 `backend/main.py:85` 每次启动都调 | ✅ |
| v14 槽位可用 | `backend/schema/` 现有 v0～v13，v14 未被占用 | ✅ **（DEF-1 类槽位冲突不复现）** |

### 3.3 §6.2 数量口径：全部算对

| 口径 | O 声称 v1.6.3.2 | 我实测 v1.6.3.0 + 本次变更推演 | 结论 |
|---|---:|---|---|
| 规则总数 | 121 | 119 + R120 + R121 = **121** | ✅ |
| DDL 分类 | 23 | 实测 22 + R120 = **23** | ✅ |
| DISTRIBUTED 分类 | 15 | 实测 14 + R121 = **15** | ✅ |
| 仅分布式规则 | 30 | 实测 27 + R121 + R030 + R032 = **30** | ✅ |
| 分布式实例生效 | 121 | 无 CENTRALIZED 域规则，故 = 总数 **121** | ✅ |
| 集中式实例生效 | 91 | 121 − 30 = **91** | ✅ |
| 集中式按适用域跳过 | 30 | **30** | ✅ |

`GET /api/v1/rules` 的 `total` / `effective_total` / `skipped_total` 三个字段实测存在（`backend/api/rules.py:27,33,34`），§8.1 的接口断言成立，确实不需要扩字段。

### 3.4 方向判断正确、值得保留的部分

1. **不采用 `R011A/R011B`，顺延 `R120/R121`** —— 与仓库 `R\d{3}` 既有约定一致，保住了 API、规则集、历史结果的兼容性。
2. **§5.1"禁止全文正则即语义"的三类场景** —— 与项目在 v1.6.2.2 上付出十几轮代价换来的教训一致，方向完全正确。
3. **§7.5 改为按 ID 差集找新增项** —— 现有代码取 `rows[rows.length-1]` 当新增项，开启 `reserve-selection` 后顺序不再可靠，O 识别到了这一点，这是本次前端设计里最关键的一处。
4. **§7.6 请求序号保护** —— 虽属附带增强，但与跨页选择是同一个"当前页/当前上下文一致性"问题，纳入本期合理。
5. **§2.1 术语归一** —— 把需求里的 `MEDIMTEXT`/`MEDIMTBLOB`/`MAXVALUES` 拼写纠正并留痕，处理得当。
6. **§2.2 明确"MAXVALUE 是本项目治理规则、不是 TDSQL 语法不支持"** —— 避免了在提示语里写错事实，这一条尤其正确。

---

## 4. P1 问题与整改意见

### 4.1 P1-01：R121 的 bare `MAXVALUE` 在现有管线上不可达，§5.4 只解决了一半

#### 4.1.1 设计怎么写的

§3.1 把阻断点定位为"二级分区状态机遇到 bare `MAXVALUE` 直接失败（`parser_legacy.py:1026-1042`）"，
§5.4 据此要求"**增量扩展**现有 `_consume_partition_values` 状态机，新增 `LESS_THAN_MAXVALUE` 指纹"。

#### 4.1.2 实测：真正的阻断点在 sqlglot 上游，不在这个状态机

```text
$ sqlglot 30.14.0，read="mysql"
  ❌ CREATE TABLE t (...) PARTITION BY RANGE (YEAR(dt)) (... PARTITION pmax VALUES LESS THAN MAXVALUE)
     → ParseError: Expecting (. Line 1, Col: 142
  ✅ 同句改为 VALUES LESS THAN (MAXVALUE)      → Create
  ✅ 同句改为 VALUES LESS THAN (739000)        → Create
  ❌ ALTER TABLE t ADD PARTITION (PARTITION pmax VALUES LESS THAN MAXVALUE)
     → ParseError: Expecting ). Line 1, Col: 43
  ⚠ ALTER TABLE t REORGANIZE PARTITION p0 INTO (...)  → 降级为 Command（拿不到任何分区结构）
```

再用**项目自己的解析器**跑一遍（TDSQL 真实语法 `shardkey=id`）：

```text
A 无二级分区（对照）                 err=无   列数=2
B 二级RANGE 正常上界                 err=无   列数=2
C 二级RANGE bare MAXVALUE            err=有: Expecting (. Line 1, Col: 245   列数=0
D 二级RANGE 括号 (MAXVALUE)          err=无   列数=2
E 二级LIST（官方原例）               err=无   列数=2
```

这与 v1.6.2.2 设计说明书里 KFN-1 的原始登记完全吻合，原文是：

> **KFN-1** `PARTITION ... VALUES LESS THAN MAXVALUE` … **sqlglot 30.x ParseError（去掉方言尾子句后亦然，非本方案所致）** → 失败关闭，**保留原 E999**

"去掉方言尾子句后亦然"这句是关键：**恢复链把 TDSQL 尾子句抹掉也救不回来**。

#### 4.1.3 为什么"只改状态机"不够——顺着恢复链走一遍

`parse()` 的恢复链（`parser_legacy.py:2268` 起）是这样的：

1. `sqlglot.parse_one(sql_clean)` 抛 ParseError → 进入 except 分支；
2. `_plan_recovery()` 用 token 状态机验证整条语句，返回 `primary_spans`（TDSQL 方言声明）+ `auxiliary_spans`（分区选项 `ENGINE=`/`COMMENT=`）；
3. `_blank_spans()` 只把这两类 span 置空；
4. **`PARTITION BY RANGE (...) (... VALUES LESS THAN MAXVALUE)` 的正文不在任何 span 里，原样留在候选 SQL 中**；
5. `sqlglot.parse_one(_final_sql)` **再次 ParseError** → `_cand = None`；
6. `_validate_recovery_candidate(None, plan)` 不通过 → `_retry_ast = None`；
7. `parsed.parse_error` 保留 → checker 报 `E999_SYNTAX_ERROR`。

即：把 `_consume_partition_values` 放开只让第 2 步过关，第 5 步照样死。
**照本设计施工，R121 对 bare 形态永远不会命中。** 而 bare 形态正是 TDSQL 官方建表文档正文的书写方式。

#### 4.1.4 整改意见（照图施工级）

设计必须在 §5.4 之前先做一个**明确的路线决策**，三选一并写清代价。我按可行性和爆炸半径排序：

**方案 A（推荐）：R121 从 token 层结果判定，不依赖 AST 恢复成功**

* **依据**：`checker.py:167-217` 实测确认——`parse_error` 存在时**规则循环照常执行**（第 177 行的 `for rule in self.get_enabled_rules(...)` 在 E999 append 之后、无条件运行）。所以一条语句**可以同时**产出 `E999_SYNTAX_ERROR` 和 `R121`。
* **落点**：
  1. `_consume_partition_values()` 的 RANGE 分支，把第 1039-1040 行的 `return -1, ""` 改为**接受并分型**，返回 `("LESS_THAN", ("maxvalue",))` 之类可区分指纹；同时在 `_consume_value_list()` 里让 `(MAXVALUE)` 形态产出同一分型（当前它只收 NUMBER/STRING，见第 1002-1021 行）。**两种形态必须归一到同一指纹**，否则 §4.7.4 的两行正例只能过一行。
  2. `_consume_partition_defs()` 已经在 `defs.append((pname, vshape, oshape))` 里带了分区名，把 `vshape` 命中 MAXVALUE 的 `pname` 汇总即可满足提示模板的"分区名"要求，不需要另建结构。
  3. `_consume_secondary_partition()` 的返回值加一项结构化结果；`_plan_recovery()` 把它一路带出来。
  4. **关键新增（本设计缺的就是这一条）**：在 `parse()` 的**两个**分支里，无论 `_validate_recovery_candidate()` 是否通过，只要 `_plan_recovery()` 返回了非 None 的计划，就把 `secondary_partition` 写入 `parsed`。写入必须在 `if _retry_ast is not None:` 判断**之外**，否则 bare 形态永远写不进去。
  5. R121 只读 `parsed.secondary_partition`，不读 AST、不读 `raw_sql`。
* **代价与必须写清的边界**：
  - bare 形态的语句**仍然会同时报 E999**（KFN-1 不因本次而关闭）。这一点必须写进 §4.7、§10.1 和 UAT-08 的通过标准，否则测试期望会打架。
  - 若希望顺带关闭 KFN-1，那是**另一个课题**（要动恢复掩码），本期不做，在 §12 单列一条风险。

**方案 B：把二级分区子句纳入恢复掩码**

* 让 `_consume_secondary_partition()` 把整个 `PARTITION BY ...` 的 span 也放进 `auxiliary_spans`，抹掉后再交给 sqlglot。
* **必须同时说明**：`_spans_only_diff()` 的逐字符门禁如何仍然成立；`_validate_recovery_candidate()` 在丢掉分区子句后如何证明"候选未丢结构"。
* **必须登记的行为变化**：抹掉后 bare 形态**不再报 E999**。R121 是 `DISTRIBUTED` 域，于是**集中式实例上一条带 MAXVALUE 二级分区的建表语句将从"E999 拦住"变成"完全静默通过"**。这是方向相反的覆盖变化，必须进 §12 风险表并由 DBA 明确接受。
* 爆炸半径明显大于方案 A（动的是全项目最敏感的恢复门禁），**不推荐**。

**方案 C：本期只覆盖 `(MAXVALUE)` 形态**

* 实测 `VALUES LESS THAN (MAXVALUE)` **今天就能正常解析**（上表 D 行，列数=2），所以这条路零解析器风险。
* 但 §4.7.4 的第一行正例（bare 形态）要从"R121 / ERROR"改成"E999（KFN-1 未关闭）"，且必须回到需求方确认："官方文档正文写法暂不覆盖"是否可接受。
* 只有在方案 A 被评估为工期不可控时才走这条，且必须留下书面确认。

**无论选哪个方案，§4.7.2 的 ALTER 两条都要补一句现状**：实测 `ALTER ... ADD PARTITION (... MAXVALUE)` 是 sqlglot ParseError、
`ALTER ... REORGANIZE ... INTO (...)` 是**降级为 `Command`**（连正常上界的分区都拿不到结构）。
所以 §5.4 第 4 点"新增 ALTER ADD/REORGANIZE 的有限状态扫描"是**从零起步的独立 token 扫描器**，
不是"扩展现有状态机"。工作量口径要在 §9.1 里说实话。

#### 4.1.5 验收方式

整改后的设计必须能回答这三个问题，且答案要能被一条测试钉住：

| 问题 | 期望答案 |
|---|---|
| bare `VALUES LESS THAN MAXVALUE` 在分布式实例上，审核结果里有哪几条？ | 明确列出（方案 A 是 `E999 + R121`，方案 B 是仅 `R121`） |
| 同一语句在集中式实例上呢？ | 明确列出（方案 A 是仅 `E999`，方案 B 是**空**——必须显式接受） |
| `(MAXVALUE)` 括号形态的两种实例分别是什么？ | 明确列出 |

---

### 4.2 P1-02：v14 迁移建立在错误前提上——`rule_configs` 是只写表

#### 4.2.1 设计怎么写的

§3.2：

> 启动初始化使用 `INSERT IGNORE`：新规则可以被补插；已存在的 R011、R035、R058 行不会自动刷新；
> **如果不做迁移，运行时规则 API 和数据库规则目录会出现两套文案/级别。**
> 因此 v1.6.3.2 **必须**使用新的迁移槽位 `backend/schema/v14/140_rule_catalog_v1632.sql` 同步内置元数据。

RISK-11 同源：`rule_configs INSERT IGNORE 留下旧文案` → `v14 迁移定向刷新元数据`。

#### 4.2.2 实测：`rule_configs` 没有任何消费者

全仓库（含前端、测试、部署脚本）搜索 `rule_configs`，命中如下：

```text
backend/services/database.py:917   CREATE TABLE IF NOT EXISTS rule_configs   ← 建表
backend/services/database.py:1824  INSERT IGNORE INTO rule_configs           ← 唯一写入
backend/main.py:85                 init_rule_configs()                       ← 启动时调用写入
smoke_test.py:92                   SELECT COUNT(*) AS cnt FROM rule_configs  ← 唯一读取，只取行数
```

* **没有任何 API 读它**（`backend/api/rules.py` 走 `checker.get_rules_info()`，真值源是规则类元数据）；
* **没有任何规则引擎路径读它**；
* **前端一次都没有出现过这个词**；
* 唯一的读取是冒烟脚本的 `COUNT(*) >= 76` 断言，与文案、级别无关。

所以 §3.2 说的"两套文案/级别"在**产品上不可见、不可达**：用户在规则页看到的、审核时实际生效的，
自始至终只有规则类元数据一个来源。

再看"新规则补插"这一半——`init_rule_configs()` 在 `backend/main.py:85` **每次启动都会跑**，
`INSERT IGNORE` 会自动把 R120、R121 插进去。**这一半也不需要迁移。**

#### 4.2.3 为什么这是 P1 而不是 P3

1. 本项目的迁移器是**失败关闭**的，迁移件一旦发布就受 checksum 管理、**不得回改**（O 自己在 §6.4 写了这条）。
   为一张没有消费者的表引入一个不可回改的制品，是**净负债**。
2. 迁移会写 `severity`。而 `rule_configs.severity` 正是"管理员覆盖严重度"这一未来能力最自然的落点。
   本次把内置默认值写进去，等于**替未来的语义提前占位**：将来若该表变成覆盖源，
   这次迁移写下的值会被当成"管理员设置"，与 O 自己"不覆盖管理员状态"的原则冲突。
3. 用户对本项目的常设约束是"控制爆炸半径、最小化修改"。在论证前提不成立的情况下扩大改动面，
   正是设计评审要拦下的。

#### 4.2.4 整改意见（照图施工级）

**首选：删除 v14 迁移，本期不新增任何 schema 槽位。**

需要同步修改的地方：

| 位置 | 现文 | 改为 |
|---|---|---|
| §3.2 | "如果不做迁移，运行时规则 API 和数据库规则目录会出现两套文案/级别。因此 v1.6.3.2 必须使用新的迁移槽位…" | "`rule_configs` 当前是**只写表**：写入方仅 `init_rule_configs()`（启动时 `INSERT IGNORE`），读取方仅冒烟脚本的 `COUNT(*)`，无 API、无规则引擎、无前端消费者。因此其内容陈旧**不产生任何用户可见或功能性影响**；R120/R121 也会由启动时的 `INSERT IGNORE` 自动补插。**本期不新增 schema 迁移。**" |
| §6.4 | 整节 | 删除，或改为"本期无数据迁移"，并把"若将来 `rule_configs` 成为展示或覆盖的真值源，需另行设计其刷新与管理员覆盖的边界"记入 §12 |
| §9.1 | `backend/schema/v14/140_rule_catalog_v1632.sql` 一行 | 删除 |
| §10.3 迁移测试 8 条 | 整节 | 删除；改为一条"回归：`init_rule_configs()` 启动后 `rule_configs` 含 R120/R121，且既有行未被改写" |
| §11.1 发布顺序第 5 步 | "执行 v14 迁移" | 删除，后续步骤顺延 |
| §11.2 回滚原则第 3 条 | v14 checksum 相关 | 删除 |
| §11.3 发布后核对 | "v14 migration 状态与 checksum" | 删除 |
| §12 RISK-11 | "`rule_configs` INSERT IGNORE 留下旧文案 → v14 迁移定向刷新" | 改为"`rule_configs` 无消费者，陈旧不影响功能；**登记为已知技术债**，待其成为真值源时另行设计" |
| §13 完成定义 | "v14 迁移幂等且不覆盖管理员启停/严重度策略" | 删除该条 |

**次选（若坚持保留）：** 必须在文档里做到三件事，缺一不可——
① 把 §3.2 的论证改成事实（承认无消费者，迁移属"目录预留"而非"修复不一致"）；
② 迁移**只写 `description`/`spec_source`/`fix_suggestion`，不写 `severity`、不写 `enabled`**，避免替未来的覆盖语义占位；
③ 在 §12 明确登记"本迁移不可回改，且当前无收益，属为未来能力预留"，由用户书面接受。

---

## 5. P2 问题与整改意见

### 5.1 P2-01：§4.6.3 判定表 `LIMIT 2000 OFFSET 1` 的预期结果不成立

**实测**（sqlglot 30.14.0 与项目解析器一致）：

```text
UPDATE t SET a=1 WHERE id>0 LIMIT 2000 OFFSET 1   → ParseError: Invalid expression / Unexpected token
DELETE FROM t WHERE id>0 LIMIT 2000 OFFSET 1      → ParseError: Invalid expression / Unexpected token
```

语句直接 `parse_error`，checker 报 `E999_SYNTAX_ERROR`，**永远到不了 R058**。
而 §10.1 明文要求"按 §4.6.3 判定表逐项参数化"，这一行会把新测试直接写红。

真正的风险不是测试红，是**实施者为了让测试变绿去"修好"解析器**——
而 `UPDATE ... LIMIT n OFFSET m` 在 MySQL 里本就是非法语法（UPDATE/DELETE 只接受 `LIMIT row_count`），
把它改成能解析，等于亲手废掉一个正确的 E999。

**整改**：§4.6.3 该行改为：

| LIMIT 形态 | R058 结果 | 说明 |
|---|---|---|
| `LIMIT 2000 OFFSET 1` | **不适用（E999_SYNTAX_ERROR）** | MySQL/TDSQL 的 UPDATE/DELETE 只接受 `LIMIT row_count`，`OFFSET` 形态本就非法；sqlglot 直接 ParseError，语句在进入规则前即被 E999 拦截。**测试断言 E999，不得为了让 R058 命中而放宽解析器** |

同时在 §10.1 的 R058 用例里补一条**回归锁**：断言该语句产出 `E999_SYNTAX_ERROR` 且**不产出** R058，
防止后续有人"顺手支持"这个语法。

### 5.2 P2-02：§9 实施文件清单遗漏 5 处硬编码 `119`，含部署门禁

§9.3 只列了 `tests/test_instance_scope_rules.py` 和 `tests/test_oracle_compat_rules.py` 两个测试文件。
实测全仓库还有 5 处会直接失败或说明失真：

| 位置 | 现状 | 后果 |
|---|---|---|
| **`deploy/verify_deploy.sh:50`** | `[[ "$TOTAL" == "119" ]] && ok "规则总数 119" \|\| bad "规则总数=${TOTAL}"` | **硬等值判断。改动后每一次部署冒烟都会 `bad`**，这是发布链上的门禁 |
| `deploy/verify_deploy.sh:2`、`:46` | 注释"规则数119"/"规则库 119 条" | 说明失真 |
| `deploy/README.md:12` | "119规则" | 说明失真 |
| `tests/test_sit_full.py:544` | `assert data["rules"]["total"] == 119` | 硬断言，必红 |
| `tests/test_sit_v1_rules.py:437/461/462` | `assert len(rules_info) == 119` / `data["total"] == 119` / `len(data["rules"]) == 119` | 硬断言，必红 |
| `tests/test_uat_rules.py:258` | `assert total == 119, f"规则总数应为119条…"` | 硬断言，必红 |
| `backend/api/rules.py:20` | docstring"动态计数，当前119条" | 说明失真（§9.1 未列本文件） |
| `backend/engine/checker.py:28` | docstring"加载全部119条规则" | §9.1 已列 checker.py（"更新动态计数注释"），✅ 已覆盖 |
| `docs/功能使用手册.md` | 5 处 119 | §9.3 已列 ✅ |
| `backend/engine/parser/parser_legacy.py:693` | 注释"实测 119 条规则无消费者" | 是**一次实测结论的留痕**，不是能力声明；建议改为"实测（v1.6.3.0 / 119 条规则）无消费者"并在本次复测后更新为 121，或明确保留原样 |
| `README.md:121` v1.0.2 发布记录 | "119 条规则" | **历史记录，按 OUT-08 不改** ✅ |
| `tests/test_instance_scope_rules.py:4` 注释 | "DETAIL-v1.5 §3.3 的 119 条判定表" | 是历史基准描述，需逐句判断改不改 |

**整改**：§9.3 的表格补齐上述条目，并把 §9.1 增加一行 `backend/api/rules.py`（docstring）、
§9.2 之后增加一节 `9.4 部署与运维件`，列入 `deploy/verify_deploy.sh`、`deploy/README.md`。
"仓库内所有 119 命中必须逐条分类"这句总纲保留，但**不能替代清单**——
本项目的设计文档等级是照图施工级，清单本身就是图纸。

### 5.3 P2-03：R011/R120 扩展到 ALTER 属需求外扩围，且需要全新的解析器通道

**需求原文**只说"如果字段使用了 TEXT 数据类型 / 如果字段使用了 BLOB…这些数据类型"，
§1.1 的 REQ-01/REQ-02 验收口径也只写了"`TEXT` 命中 R011，级别 INFO"和"指定 5 种类型命中 R120，级别 ERROR"，
**没有一处提到 ALTER**。但 §4.1.2 命中条件第 1 条把 `ALTER TABLE` 的 `ADD/MODIFY/CHANGE COLUMN` 写成了强制项，
§5.2 据此要求新增 `ddl_column_types` 通道，§10.1 也据此要求 ALTER 用例。

**实测：ALTER 当前拿不到任何列信息。**

```text
ALTER TABLE t ADD COLUMN body LONGTEXT      → columns=[]  column_types=[]  alter_actions=[]
ALTER TABLE t MODIFY COLUMN body MEDIUMBLOB → columns=[]  column_types=[]  alter_actions=[]
ALTER TABLE t CHANGE COLUMN a b TEXT        → columns=[]  column_types=[]  alter_actions=[]
ALTER TABLE t ADD COLUMN c VARCHAR(50)      → columns=[]  column_types=[]  alter_actions=[]
```

也就是说这不是"补一个字段"，是在 `parser_legacy.py`（本项目缺陷密度最高、v1.6.2.2 走了十三轮评审的文件）
里新增一整条 ALTER 列定义提取通道。这个扩围本身可能是对的——
`ALTER TABLE t ADD COLUMN body LONGTEXT` 显然应当被 R120 拦住——但它必须走扩围的流程，不能藏在实现细节里。

**整改**：

1. §1.1 新增两行需求追踪，把扩围显式化：

| 需求编号 | 需求 | 设计落点 | 核心验收口径 |
|---|---|---|---|
| REQ-01a（评审新增，扩围） | R011/R120 同时覆盖 `ALTER ADD/MODIFY/CHANGE COLUMN` | §4.1.2 / §4.2.2 / §5.2 | ALTER 三种形态均能命中；解析失败时**不得**退化为正则兜底 |

2. §1.2 明确不做里补一条边界：`ALTER ... CONVERT TO CHARACTER SET`、`ALTER ... ALTER COLUMN SET DEFAULT`
   等不含类型定义的形态不在本期通道内。
3. §5.2 补一句**失败关闭口径**：`ddl_column_types` 在 ALTER 解析失败时返回空集合，
   R011/R120 对空集合一律不命中，**不得**回退到 `raw_sql` 正则——否则 §5.1 的三条禁令自相矛盾。
4. §9.1 的 `parser_legacy.py` 行把工作量说实话：当前 ALTER 零列信息，属**新增通道**而非"扩展"。
5. 若工期紧张，允许的降级路径是：本期 R011/R120 只覆盖 CREATE（与现状 R011 一致），
   ALTER 覆盖单列一条 REQ 进下个版本。**这条降级路径要写进设计**，避免实施期临时决定。

### 5.4 P2-04：R035 的跨表上下文同属扩围，§9.1 一句话带过

§3.3 的判断是对的、也是本设计最有价值的一处洞察：R035 今天**从来不会触发**
（全仓库只有 `ddl.py:501` 一个 `existing_columns` 读取方，没有任何产品路径构造它，
`tests/TEST_SPEC-规则覆盖与压力测试.md:38` 也是这么记的）。
所以如果只按需求字面改"不再比长度"，R035 改完仍然永不触发——需求被**空洞地满足**。
O 选择把它做成真的能触发，方向正确。

但 §4.5.3 给了七条约束，§9.1 对应的落点却只有一句
"`backend/services/audit_service.py` 确保文件/在线元数据入口使用统一跨表上下文"。
这是本次第二大的工作量，图纸精度明显低于其他章节。

**整改**：§4.5.3 之后补一节 §4.5.4「跨表索引的落点与生命周期」，至少写清：

| 必答项 | 要求 |
|---|---|
| 构造点 | 指名函数（文件审核批量入口、在线元数据审核入口各一处），以及是否复用同一个构造器 |
| 输入 | 是"同一批 DDL 的解析结果"还是"再查一次 `information_schema`"；若是后者，§4.5.3 第 7 条的 `DATA_TYPE`/`COLUMN_TYPE` 口径落在哪个查询上 |
| 生命周期 | 单次审核请求内构造一次、只读、请求结束即释放；**不得**跨请求缓存（否则元数据变更后会出幽灵冲突） |
| 与过滤的先后 | 必须在**规则集过滤与适用域过滤之后**才构造，避免 R035 被禁用时仍付出全量索引构造的代价 |
| 单表/无上下文 | 明确"跳过"而不是"通过"，并确认跳过不写入任何 violation |
| 降级路径 | 若本期无法交付跨表索引，R035 仍按需求完成**文案与比较口径**改造（`raw_type` → 规范化基础类型），保持"当前不触发"现状；此时 §10.1 的 R035 第 8 条用例降级为 skip 并登记。**这条要写进设计** |

同时 §1.1 补一行 REQ-05a（评审新增，扩围）：「R035 具备真实跨表上下文，从'永不触发'变为'可触发'」。

### 5.5 P2-05：反复要求"规范化基础类型"却始终不指名字段——而它已经现成

§4.1.2、§4.2.2、§4.5.2 三处都要求用"解析器的规范化基础类型"，§4.5.2 还专门禁止了三种错误实现。
但全文没有一处说这个值**叫什么、从哪来**。

**实测：`parsed.columns[i]["type"]` / `parsed.column_types[i]["type"]` 就是它，且完全满足 O 列出的全部正反例。**

```text
a VARCHAR(32)                       type=VARCHAR     raw=VARCHAR(32)
b VARCHAR(128)                      type=VARCHAR     raw=VARCHAR(128)
c CHAR(32)                          type=CHAR        raw=CHAR(32)
d DECIMAL(10,2)                     type=DECIMAL     raw=DECIMAL(10, 2)
e DATETIME(3)                       type=DATETIME    raw=DATETIME(3)
f INT(11)                           type=INT         raw=INT(11)
g INT                               type=INT         raw=INT
h INTEGER                           type=INT         raw=INT        ← 别名已归一
i INT UNSIGNED                      type=UINT        raw=INT UNSIGNED  ← 有符号性已保留
j BIGINT                            type=BIGINT      raw=BIGINT
k TEXT / l MEDIUMTEXT / m TINYTEXT  type=TEXT / MEDIUMTEXT / TINYTEXT
n JSON / o LONGBLOB / p BLOB        type=JSON / LONGBLOB / BLOB
TEXT(1000)                          type=TEXT        raw=TEXT(1000)         ← 括号参数已剥离
TEXT CHARACTER SET utf8mb4          type=TEXT        raw=TEXT
LONGTEXT ... COLLATE utf8mb4_bin    type=LONGTEXT    raw=LONGTEXT
blob_url VARCHAR(200)               type=VARCHAR                            ← 列名不误命中
g VARCHAR(20) COMMENT 'LONGTEXT'    type=VARCHAR                            ← 注释不误命中
```

逐条对照 §4.5.2 的比较表：`VARCHAR(32)`vs`VARCHAR(128)` 一致 ✅、`DECIMAL(10,2)`vs`DECIMAL(18,4)` 一致 ✅、
`DATETIME(3)`vs`DATETIME(6)` 一致 ✅、`INT(11)`vs`INT` 一致 ✅、`INTEGER`vs`INT` 一致 ✅、
`INT UNSIGNED`vs`INT` **不一致** ✅（`UINT` ≠ `INT`）、`VARCHAR(32)`vs`CHAR(32)` 不一致 ✅、`TEXT`vs`MEDIUMTEXT` 不一致 ✅。
**九行全中，一行不差。**

不指名的后果很实际：实施者看到"禁止 `raw_type.split('(')[0]`"，很可能去**另写一个归一器**，
于是同一件事有两套实现、两套边界、两倍缺陷面——而现成的这个已经被 119 条规则和十几轮评审用过了。

**整改**：

1. §4.1.2 命中条件第 2 条改为：「`parsed.column_types[i]["type"]`（解析器已归一的基础类型）**严格等于** `"TEXT"`」。
2. §4.2.2 改为：「`parsed.column_types[i]["type"]` **属于** `{"BLOB","MEDIUMTEXT","LONGBLOB","MEDIUMBLOB","LONGTEXT"}`」，
   并删去"匹配大小写不敏感"——该字段已是大写归一值，再提大小写只会引导实施者加多余的 `.upper()`。
3. §4.5.2 段末补一句：「比较值取 `col["type"]`；`raw_type` **只用于展示**」，
   并**补一条实现陷阱**：`INT UNSIGNED` 的 `type` 是内部名 `UINT`，
   §4.5.1 的提示模板 `{current_type}`/`{reference_type}` **必须回填 `raw_type`**，
   否则用户会在报告里看到 `UINT` 这个数据库里并不存在的类型名。
4. §5.2 相应收敛：CREATE 路径**不需要**新增通道（复用 `column_types` 即可），
   新增通道只为 ALTER 服务（见 P2-03）。这一句能显著缩小 §9.1 里 `parser_legacy.py` 的改动面。

### 5.6 P2-06：质量门禁的双向变化未量化

**实测** `backend/services/gate_service.py`：

```python
_POLICIES = {
  "strict": {"max_error": 0, "max_warning": 0},   # 不允许任何违规
  "normal": {"max_error": 0, "max_warning": -1},  # 不允许 ERROR，WARNING 不限
  "loose":  {"max_error": -1, "max_warning": -1},
}
error_count   = sum(1 for v in violations if v.severity == Severity.ERROR   or str(v.severity) == "ERROR")
warning_count = sum(1 for v in violations if v.severity == Severity.WARNING or str(v.severity) == "WARNING")
```

**`INFO` 完全不参与计数。** 于是本次改动会让门禁结果在**两个相反方向**同时变化：

| 变化 | 方向 | 具体影响 |
|---|---|---|
| R011 `WARNING → INFO` | **放松** | strict 策略下，"建表含 TEXT 列"今天卡门禁、改后**直接放行**（INFO 不计数） |
| R011 收窄后 `TINYTEXT/TINYBLOB/JSON` 失去覆盖 | **放松** | 这三类今天产 WARNING、改后**一条不产**；strict 策略下同样从卡变放 |
| R120 新增且为 `ERROR` | **收紧** | normal 策略下，"建表含 BLOB/MEDIUMTEXT/LONGBLOB/MEDIUMBLOB/LONGTEXT"今天只是 WARNING（可过）、改后 **ERROR 直接卡死** |
| R030/R032 转 DISTRIBUTED | **放松（仅集中式）** | 集中式实例上这两条 ERROR 消失 |
| R121 新增且为 ERROR | 收紧（仅分布式） | 已被 RISK-10 覆盖 |

§12 的 RISK-10 只写了"新规则默认开启改变质量门禁结果"，既没覆盖 R011 降级带来的**放松**，
也没覆盖 R120 对 normal 策略的**收紧**——而后者会让现有 CI 流水线在升级当天开始红灯。

**整改**：RISK-10 拆成三条，并在 §11.1 发布顺序里把"通知受影响的流水线负责人"提前到部署之前：

| 编号 | 风险 | 处置 |
|---|---|---|
| RISK-10a | R120 为 ERROR，normal 策略下含受限 LOB 的建表由"可过"变"卡死" | 发布前统计存量项目中命中该 5 类的比例；给受影响方预留整改窗口或临时规则集豁免 |
| RISK-10b | R011 降 INFO + TINYTEXT/TINYBLOB/JSON 失去覆盖，strict 策略下由"卡"变"放行" | 需求方确认接受；若不接受，须另立需求把三类型纳入 R011 或 R120 |
| RISK-10c | R030/R032 转分布式，集中式实例的对应门禁项消失 | 见 P2-07 |

§10.2 相应增加一条门禁级断言：同一份含 TEXT+LOB 的建表语句，
在 strict/normal 两种策略下的 `passed` 结果与本表一致。

### 5.7 P2-07：风险登记缺"集中式零覆盖"这一面

§4.3 只提到"集中式上 R030 被跳过，但 R031 仍可能阻止 `CREATE FUNCTION`"，
§4.4 只提到"R024 已是仅分布式"。两处都在讲**还剩什么**，没讲**没了什么**。

**穷举确认**（遍历全部 119 条规则的描述与 docstring，筛 view/procedure/trigger/临时表 关键词）：

```text
R017  scope=all          禁止 ORDER BY RAND()，会导致全表扫描和临时表     ← 与本议题无关
R024  scope=distributed  禁止使用CREATE TEMPORARY TABLE                    ← 已是分布式
R030  scope=all          禁止使用视图、存储过程、触发器、自定义函数        ← 本次转分布式
R032  scope=all          禁止使用临时表进行复杂业务逻辑                    ← 本次转分布式
R115  scope=distributed  分布式 update/delete…limit 依赖 proxy 内嵌 myisam  ← 与本议题无关
```

结论：改动后，**集中式实例上**——

* **视图 / 存储过程 / 触发器：零规则覆盖**（R030 是唯一一条；R031 只管 `CREATE FUNCTION`）；
* **临时表：零规则覆盖**（R024 与 R032 双双仅分布式）。

这**正是用户要求的**（"R030 和 R032 改为仅分布式适用"），所以不是缺陷。
但对一个治理工具来说，两类治理项从"有"变"零"是必须让 DBA 睁着眼睛签字的事实。

**整改**：§4.3 与 §4.4 各补一句"净效果"，并在 §12 新增：

| 编号 | 风险 | 处置 |
|---|---|---|
| RISK-16 | R030+R032 转分布式后，**集中式实例对"视图/存储过程/触发器"与"临时表"两类治理项将完全无规则覆盖**（已穷举确认无其他规则接管） | 这是需求的直接结果，非缺陷。发布前由 DBA 书面确认接受；若集中式仍需管控，应另立需求新增集中式适用规则，**不得**在本期擅自保留 R030/R032 的 ALL 域 |

§10.1 的 R030/R032 用例补一条**零覆盖回归锁**：集中式实例下审核
`CREATE VIEW` / `CREATE PROCEDURE` / `CREATE TRIGGER` / `CREATE TEMPORARY TABLE`，
断言结果集中**除 R031 对 FUNCTION 外不含任何相关违规**——把"零覆盖"这个事实钉成显式契约，
将来谁不小心加回来，测试会告诉他这是有意为之还是手滑。

---

## 6. P3 建议

### 6.1 P3-01：`LIMIT 1, 2000` 的实现陷阱

实测 sqlglot 对 `UPDATE t SET a=1 WHERE id>0 LIMIT 1, 2000` 的解析结果：

```text
node=Update   top_limit=('Limit', 'Literal', '2000')
```

**count 落在 `limit.expression`，值就是 2000**。实施者若照 §5.3"从顶层节点读取 `limit`，
确认 `expression` 是否为非负整数字面量"直译，会读出 2000 ≤ 2000 → **判为通过**，
与 §4.6.3 期望的 WARNING 相反。

§5.3 虽有"offset 路径：保留 offset，但 R058 不把该形态作为合规证明"，但没说**offset 藏在哪**、
也没说"两参数形态下 expression 是 count 不是 offset"。建议 §5.3 补一句：

> MySQL 两参数形态 `LIMIT <offset>, <count>` 下，sqlglot 把 **count 放在 `Limit.expression`**、
> offset 放在节点的 `offset` 参数中。R058 必须**先判定 offset 是否存在**，存在即直接落"不可静态证明"分支，
> 不得只读 `expression`。另注：`UPDATE/DELETE` 在 MySQL 语法上并不接受两参数 LIMIT，
> 本判定属于对 sqlglot 宽松解析的防御，不代表该写法合法。

### 6.2 P3-02：现有 `limit_offset` 就是全文正则，与 §5.1 冲突，应登记为既有例外

`parser_legacy.py:2455-2459` 实测：

```python
parsed.limit_offset = int(limit_match.group(1))          # 正则
limit_offset_match = re.search(r"\blimit\s+(\d+)\s+offset\s+(\d+)", sql_lower)
```

这正是 §5.1 表格里"UPDATE/DELETE LIMIT → 正则风险：`remark='limit 10'` 或注释错误放行"所禁止的做法，
而它今天服务于 R114（深分页）。§5.1 写的"正则只可用于……**当前已有、已被测试证明安全的**语句头辅助"
可以覆盖它，但没点名，实施者很可能顺手把 R114 一起"统一"了——那是本期范围外的行为变更。

**建议**：§5.1 表格下补一句：「既有例外：`limit_offset`（服务 R114 深分页）当前为全文正则实现，
**本期不动**；R058 的新结构不复用它，两者并存。R114 的口径改造另立需求。」

### 6.3 P3-03：四张对比表共用同一个 `ref="cmpTableRef"`

实测 `index.html:492/714/1279/1514` 四处全部是 `ref="cmpTableRef"`，
`app.js:64` 只有一个 `const cmpTableRef=ref(null)`。四个页面分属互斥的
`v-if="currentPage==='...'"` 分支，同一时刻只有一个挂载，所以**当前是安全的**。

但 §7.4 要求"在 `nextTick` 后调用**当前表格实例**的 `clearSelection()`"——
实施者必须知道：① 这个 ref 是四张表复用的；② `nextTick` 之后页面可能已经切走、
ref 已变成 `null`；③ 现有 `onSnapshotSelect` 里的 `try{...}catch(e){}` 正是为此而写。

**建议**：§7.4 补一句「四张表共用 `cmpTableRef`（互斥 `v-if`，同时只挂载一个）；
`clearCompareSelection()` 必须做空值保护，且不得假设调用时表格仍然挂载」。

### 6.4 P3-04：§7.4"退出登录/切换用户清空"没有落点

实测 `cmpState.selected` 全仓库只有 `cmpQuery()`（第 1953 行）一处清空，
`doLogout` 完全不触碰 `cmpState`。所以这一行是**需要新增的清理点**，不是既有行为。

**建议**：§7.4 表格该行备注改为「**新增**：`doLogout()` 与切换用户路径需调用 `clearCompareSelection()`；
当前 `doLogout` 不触碰 `cmpState`」，并在 §9.2 的 `app.js` 改动描述里点名 `doLogout`。

### 6.5 P3-05：§10.5 声称的 Playwright 依赖前提不成立

§10.5 写"复用项目 dev extra 中已固定的浏览器测试依赖"。实测仓库**没有** Playwright 依赖声明
（`requirements.txt`、`pyproject.toml` 均无）；我在 v1.6.3.0 的 SIT 阶段是临时 `pip install playwright`
并用 `/opt/pw-browsers/chromium-1194` 跑起来的，那是测试环境的临时手段，不是仓库既有能力。

**建议**：§10.5 改为「**需先补齐**浏览器测试依赖与夹具：在 `pyproject.toml` 的 dev extra 中
固定 `playwright` 版本，并提供可复用的登录/建数据夹具。此项是 FE-01～FE-12 的前置条件，
应作为独立任务排期，不能默认已存在」。同时 §9.3"前端浏览器行为测试"一行补上依赖文件。

### 6.6 P3-06：`ALTER ... REORGANIZE` 的降级形态未在设计中体现

实测 `ALTER TABLE t REORGANIZE PARTITION p0 INTO (...)` 被 sqlglot **降级为 `Command`**
（连正常上界的分区都拿不到任何结构），与 `ADD PARTITION` 的 ParseError 是**两种不同的失败形态**。
§4.7.2 把两者并列为"覆盖语句"，§5.4 第 4 点也只说"新增 ALTER ADD/REORGANIZE 的有限状态扫描"，
没有区分。建议在 §4.7.2 各注明当前失败形态，让实施者知道两条路要分别处理。

---

## 7. 需求覆盖矩阵复核

逐条对照用户原始需求，确认设计没有漏项、没有偷偷改口径：

| 用户需求 | 设计落点 | 覆盖 | 评审意见 |
|---|---|---|---|
| ①-1 R011 拆出"谨慎使用TEXT大对象字段"，只管 TEXT，级别 info | §4.1 | ✅ | 命中口径、级别、文案、修复建议均与需求一致。**但**扩到了 ALTER（P2-03），且未指名字段（P2-05） |
| ①-2 拆出"禁止滥用LOB大对象字段"，管 BLOB/MEDIUMTEXT/LONGBLOB/MEDIUMBLOB/LONGTEXT，级别 error | §4.2 | ✅ | 5 种类型一字不差；拼写纠正（`MEDIMTEXT`→`MEDIUMTEXT` 等）处理得当。**副作用**：TINYTEXT/TINYBLOB/JSON 失去覆盖，已登记 RISK-02，但门禁影响未量化（P2-06） |
| ② R030、R032 改为仅分布式适用 | §4.3 / §4.4 | ✅ | 变更正确。**风险登记缺"集中式零覆盖"这一面**（P2-07） |
| ③ R035 改为只查类型不查长度，文案与建议同步调整 | §4.5 | ✅ | 比较口径设计正确（括号参数全部不参与、`UNSIGNED` 参与）。**但**把"永不触发"改成"可触发"属扩围，图纸精度不足（P2-04） |
| ④ R058 的 limit ≤1000 改为 ≤2000 | §4.6 | ✅ | 阈值正确，且顺带修掉了"只判 `limit` 关键字"的错误放行。**判定表有一行事实错误**（P2-01） |
| ⑤ 新增"禁止二级分区表创建 MAXVALUE"，error，仅分布式 | §4.7 | ⚠ | 元数据、适用域、正反例都对，**但主场景（官方文档正文的 bare 写法）在现有管线上不可达**（P1-01） |
| ⑥ 四个模块的扫描历史跨页选择失效 | §7 | ✅ | 根因定位准确、状态机与边界表完整、`reserve-selection` + `row-key="id"` 方案正确；差集判定新增项这一点尤其到位。仅 3 个 P3 细节 |
| 版本定为 v1.6.3.2 | §9.3 / §13 | ✅ | 但 `VERSION` 与 `backend/config.py` 的同步只在 §9.3 表格里一行带过；建议在 §11.1 发布顺序里也列一步（v1.6.3.0 的 UAT 就出过版本号漏提升的问题） |

**结论：六项需求全部有对应设计，无漏项，无擅自改口径。**

---

## 8. 评审结论与准入条件

### 8.1 结论

**不通过（有条件）。**

这份设计的**勘查质量是本项目历次设计文档里靠前的**：§3 引用的 11 处行号我逐条核对，
在当前 HEAD 上全部准确；§6.2 的七项数量口径我逐项实测推演，一个不差；
§3.4 对前端根因的定位（`row-key` 缺失 + `reserve-selection` 缺失 + `loadSnapshots` 整体替换 `list`）
和 §7.5 对"不能再用 `rows[length-1]` 当新增项"的判断，都是真看过代码才写得出来的。

拦下它的是两条：

1. **P1-01** —— 阻断点找错了层。设计把 R121 的障碍定位在项目自己的 token 状态机，
   实际卡在 sqlglot 上游；照本设计施工，R121 对官方文档正文写法**永远不会命中**，
   而这正是 REQ-07 的主场景。这条不解决，编码出来的东西验收时才会发现是空的。
2. **P1-02** —— 一个改动的必要性论证是错的。`rule_configs` 是只写表、无任何消费者，
   §3.2 声称的"两套文案"用户看不见也碰不到；为它引入一个**checksum 冻结、不可回改**的迁移件，
   在"控制爆炸半径、最小化修改"的常设约束下站不住。

其余 7 项 P2 都是文档层面一次性可补齐的：扩围要追溯、清单要补全、
现成的字段要指名、门禁的双向影响要量化、集中式零覆盖要让 DBA 签字。

### 8.2 进入编码的准入条件

| # | 条件 | 判据 |
|---|---|---|
| 1 | **P1-01 关闭** | §4.7/§5.4 明确选定 A/B/C 之一，并回答 §4.1.5 的三个问题；若选 A，必须写明"bare 形态仍同时产 E999"，且 §10.1、UAT-08 的期望同步 |
| 2 | **P1-02 关闭** | v14 迁移删除并同步 §3.2/§6.4/§9.1/§10.3/§11/§12/§13；或按"次选"三条全部落实并取得书面接受 |
| 3 | P2-01～P2-07 全部落实 | 逐条对照本报告 §5 的整改意见 |
| 4 | P3-01～P3-06 处置留痕 | 采纳或说明不采纳的理由，不要求全采纳 |
| 5 | 需求追踪矩阵补齐扩围项 | §1.1 出现 REQ-01a（ALTER 覆盖）、REQ-05a（R035 跨表上下文），且各自带降级路径 |

### 8.3 给下一轮的提醒

* 本报告所有"实测"结论都是在 `main`/`d59c7f0` 上跑出来的，整改后如 `main` 前进，
  P1-01 的 sqlglot 行为需要按当时锁定的 sqlglot 版本复测一次（当前 30.14.0）。
* §12 末尾那条"进入编码的前置门禁只有一项需要在目标环境确认（UPDATE/DELETE LIMIT 版本前提）"
  在本轮之后不再成立——准入条件已增至 5 项，其中 2 项是设计自身的，不需要目标环境。
  下一版请同步这句话，避免给实施方错误的"可以开工"信号。
* R121 一旦按方案 A 落地，"一条语句同时产出 E999 和业务规则违规"在本项目里是**新形态**。
  建议在 §8.2 审核结果 API 一节补一句：前端与报表对同一语句多条违规的展示已支持（现有 violations 是数组），
  但**报告里同时出现"语法错误"和"具体规则违规"对用户是新体验**，UAT-08 要专门确认可读性。

