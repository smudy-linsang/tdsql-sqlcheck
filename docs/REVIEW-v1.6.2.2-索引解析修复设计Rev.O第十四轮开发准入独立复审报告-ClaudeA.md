# v1.6.2.2 索引解析修复设计 Rev.O 第十四轮开发准入独立复审报告（智能体 A）

## 1. 评审结论

**结论：Rev.O 暂不通过开发准入（No-Go）。**

Rev.O 对第十三轮意见的**语法层整改质量很高**：可执行注释的 atom 内部位置、类型产生式与属性族、
KFN"计划可达 + 最终失败"、具名 PRIMARY 自身 COMMENT、列 COMMENT 存在性守恒，
我逐条造反例复跑，**在 sqlglot 29.0.0 / 30.14.0 / 30.17.0 三版上全部真实关闭**（见 §5）。
把稳定 marker 引入代码块、把 §3.4 的陈旧数字**如实标注为 Rev.N 历史生成物**而不是伪造成新数字，
也都是明显的改进。

但本轮发现 **3 项 BLOCK、3 项 MAJOR**：

- **BLOCK-14-01**：为 BLOCK-13-01 顺带做的表级 `_parse_unique_constraint()` 修复，
  **激活了 R077 的 legacy 宽松分支**，造成 **R077 漏报**；仓库 5 项冻结用例由通过转失败，
  语料 7 条产生非目标漂移。该改动**违反 `distributed.py` 内写明的"不得拆分提交"契约**，
  且与 Rev.O 自己的裁定 #1（规则文件保持不动）互斥 —— **在当前范围裁定下该 BLOCK 无解，
  需要用户就 ADJ-4 作一次决策**；
- **BLOCK-14-02**：`CONSTRAINT … UNIQUE` 的"具名失败关闭"只在**恢复链内**生效。
  语句本身不需要恢复时规划器根本不被调用，于是 R054 静默漏审；
  其中方言尾子句一路还从 `Create` 退化成 `Command`，**R029/R036/R037 一并消失且不报 E999**；
- **BLOCK-14-03**：BLOCK-13-04 与 MAJOR-13-02 **只写了规范、没有实现**。
  `docs/evidence/v1.6.2.2/` 六个文件本轮**一个字节都没动**，
  一键命令在当前提交上直接失败，文档登记的哈希是未回填的占位符；
- **MAJOR-14-01**：两个"只有 AFTER、没有 BEFORE"的代码块使机械重建器无法定位，
  「照图施工」在这两处退回人工判断；
- **MAJOR-14-02**：§7.1 的生成器标记被删除，正文留着 Rev.N 的 501 / 511 陈旧数字且**未标注为历史**
  —— 与 Rev.O 自己的裁定 #6 直接冲突（§3.4 标注了，§7.1 没有）；
- **MAJOR-14-03**：manifest 有 3 条用例与 Rev.O 新口径冲突，未同步更新。

BLOCK-14-01 与 BLOCK-14-02 都直接落在本次要修的 **R054 / R077 核心能力**上，
且都属于"无 E999、结论静默变化"的次生灾害类型 —— 正是 Rev.O 自己在 BLOCK-13-05 判定为不可接受的形态。
因此不能带着这两项进入开发。

## 2. 评审对象与边界

| 项 | 内容 |
|---|---|
| 仓库 / 分支 | `smudy-linsang/tdsql-sqlcheck` / `main` |
| 第十三轮复审报告 | `13937fd` |
| Codex 的 Rev.O 提交 | `f92b20e` |
| 主干基线 | `4d6968a`（Rev.N，产品代码仍为 v1.6.2.1） |
| 设计文档 | `docs/DESIGN-v1.6.2.2-索引类型误判与唯一索引注释解析崩溃修复详细设计说明书.md` |
| 证据目录 | `docs/evidence/v1.6.2.2/`（本轮 **未被 Rev.O 修改**，`git diff 4d6968a..f92b20e -- docs/evidence/` 为空） |
| 本轮动作 | 只评审、只测试、**不修改产品代码、不修改设计文档** |

沿用用户已冻结决策，不重新争论：目标实例 `TDSQL_DISTRIBUTED BY HASH` 合法、
`shardkey=noshardkey_allset` 为广播哨兵、使用 sqlglot 词法器、`SPATIAL` 映射为 `NORMAL`、
KFN-1 `MAXVALUE`、ADJ-6、`CONSTRAINT … UNIQUE` 本期不扩能力、`NEW_SECONDARY` 登记不放行。

> ⚠️ **利益冲突声明**：Rev.N 由我编写。为避免确认偏误，本轮**不以"设计说明书怎么写"为依据**，
> 一律直接构造输入、执行、看行为；凡 Rev.O 推翻 Rev.N 的判断，我先假定 Rev.O 正确再去复现。
> §5 中确认关闭的 5 项，都是我按 Rev.O 的机制自行造反例验证后确认的。

## 3. 独立验证方法

1. 从 `f92b20e` 按 Rev.O 的 marker 契约**独立实现重建器**（不复用仓库里那份已失效的），
   把 19 对代码块应用到干净主干，得到 Rev.O 目标 parser；
2. 两个"AFTER-only"块按正文文字描述人工定位插入（见 MAJOR-14-01）；
3. 逐条构造 Rev.O 七项裁定的正反例，断言推进到 `ParsedSQL.indexes` 与 `RuleChecker` 规则集合，
   **不停留在"恢复成 Create"**；
4. 用**变体消融**定位回归根因：分别回退表级 UNIQUE 修复与列级 UNIQUE 接线，对比失败集合；
5. 三版 sqlglot 分别复跑全部反例；
6. 全量仓库测试、`verify_rules.py`、201 条语料 + 生产 14 表漂移、两份生产 fixture 精确规则集合；
7. 核对 `docs/evidence/v1.6.2.2/` 实物与 Rev.O 正文规范的差距。

## 4. 结果总览

| 检查 | 结果 |
|---|---|
| marker 契约（19 对 BEGIN/END） | **全部配对、无重名、无孤儿** ✅ |
| Rev.O 目标 parser 可构建、可导入 | ✅ |
| 全量 `pytest tests/` | **5 failed / 1350 passed / 29 skipped** ❌（主干基线为 **0 failed / 1355 passed**） |
| `test_r077_r054_tdsql_syntax.py`（门槛 G-2 = 45 passed） | **3 failed / 42 passed** ❌ |
| `test_parser_tdsql_dialect_fallback.py`（G-3 = 14 passed） | **1 failed / 13 passed** ❌ |
| `test_r061_index_name_quoting.py`（G-4 = 12 passed） | **1 failed / 11 passed** ❌ |
| `test_parser.py` | 14 passed ✅ |
| `verify_rules.py` | 119 / 107 / 未覆盖 0 / 断言失败 3（与基线同名同因）✅ |
| 语料 + 生产 14 表（201 条 × 119 规则） | **7 条非目标漂移** ❌（Rev.N 为 0 条） |
| 两份生产 fixture 精确规则集合 | **与预期一致** ✅ |
| 现有 501 条 manifest | 3 条失败（口径变更未同步，见 MAJOR-14-03） |
| `run_all.py` 一键复现 | **失败**：哈希缺失 + manifest 未全绿 + 两个生成区段不一致 ❌ |
| 可执行注释 atom 内部位置（8 反例 + 2 正例） | **三版全部符合预期** ✅ |
| 类型产生式 / 属性族 / KFN 计划可达 | **三版全部符合预期** ✅ |

## 5. 确认已真实关闭的部分（不必再改）

为避免 Codex 在已正确的机制上反复返工，本轮明确确认：

1. **BLOCK-13-01 的列级 UNIQUE 供数方向正确且有效。** 已声明主键、分片键 `sk` 的表上逐项实测：

   | 形态 | `parsed.indexes` | R054 | 期望 | |
   |---|---|---|---|---|
   | 表级 UNIQUE 含分片键 | `uk(sk,c)` | 不命中 | 不命中 | ✅ |
   | 表级 UNIQUE 不含分片键 | `uk(c)` | 命中 | 命中 | ✅ |
   | 列级 UNIQUE 恰为分片键 | `sk(sk)` | 不命中 | 不命中 | ✅ |
   | 列级 UNIQUE 在非分片键列 | `c(c)` | 命中 | 命中 | ✅ |
   | 列级(合规) + 表级(违规) 共存 | 两条 | 命中 | 命中 | ✅ |
   | 列级(违规) + 表级(合规) 共存 | 两条 | 命中 | 命中 | ✅ |

2. **BLOCK-13-02 的 atom 边界判据成立。** `ENGINE` 与 `=` 之间、`ENGINE=` 与值之间、
   `CHARACTER` 与 `SET` 之间、`shardkey=` 与值之间、`TDSQL_DISTRIBUTED` 与 `BY` 之间、
   `HASH(` 与列名之间、`PARTITION` 与 `BY` 之间、分区定义表内部 —— **8 类全部 `plan=REJECT`**；
   两个合法 atom 边界正例仍恢复。三版一致。
3. **BLOCK-13-03 的类型/属性族与 KFN 机制成立。** `INT SIGNED` / `VARCHAR(20) BINARY` /
   `NATIONAL CHAR(10)` / `SERIAL` / `POINT` 全部 `plan=OK` + 具名 KFN 编号 + 最终非 `Create` + 保留 E999；
   `INT CHARACTER SET utf8mb4`、`INT COLLATE utf8mb4_bin` 在**规划层**即因 family 错配拒绝；
   `TEXT/BLOB(M)` 上界放到 `4294967295`、越界与负值拒绝。
4. **BLOCK-13-05 关闭。** `CONSTRAINT pk PRIMARY KEY (id) COMMENT 'pk'` 在**有方言尾子句**、
   **无方言尾子句**两条路径上都恢复为 `Create` 且 `cols=2`、无 E999。
5. **MAJOR-13-01 关闭。** 候选删除列 COMMENT、凭空增加列 COMMENT 均被门禁拒绝，正确候选通过。
6. **marker 契约本身是正确方向。** 19 对标记全部配对，比 Rev.N 的位置索引稳健得多。
7. **§3.4 把陈旧规模表如实标注为"Rev.N 历史生成物、必须整段替换"**，没有伪造数字，这一点应予肯定。

## 6. BLOCK-14-01：表级 UNIQUE 修复激活 R077 宽松分支，造成核心规则漏报

### 6.1 事实

Rev.O §3.3c 在 BLOCK-13-01 之外**自行追加**了一处修复（`TABLE-UNIQUE-BEFORE/AFTER` 代码块）：
让 `_parse_unique_constraint()` 能从 `UniqueColumnConstraint.this = exp.Schema` 提取表级 UNIQUE。

该改动**单独**造成以下后果（消融实验见 §6.3）：

| 冻结用例 | 基线 | Rev.O | 断言原文 |
|---|---|---|---|
| `test_r077_r054_matrix[C3]` | pass | **fail** | R077 期望=触发，实际=不触发 |
| `test_r077_r054_matrix[X20]` | pass | **fail** | R077 期望=触发，实际=不触发 |
| `TestSupplementary::test_x13_unique_re_atomic_guard` | pass | **fail** | 原版 R077 应触发 |
| `TestADJ5Guard::test_g1_unique_not_in_indexes` | pass | **fail** | 「ADJ-5 前提被打破：parsed.indexes 产出了 1 条 UNIQUE。**这会激活 R077 的宽松分支导致漏报**」 |
| `TestUniqueDeadCode::test_u2_unique_ux_prefix` | pass | **fail** | 「UNIQUE 分支当前为死代码（ADJ-5），不应命中」 |

C3 与 X20 的具体表现是 **R077 由触发变为不触发** —— 即**核心规则产生漏报**，
而不是报错，下游不会有任何提示。

### 6.2 这不是意外，代码里写着不许这么做

`backend/engine/rules/distributed.py` 第 677~684 行原文：

```text
# ⚠️ 不得单独放宽本正则：R077 仍保留 legacy 的"主键 或 唯一索引"判定
#    （ADJ-4，已决策不收紧）。本正则一旦认出更多唯一索引，就会激活那个
#    宽松分支并产生漏报。修改本正则、或让 parsed.indexes 开始产出 UNIQUE
#    条目时，必须在同一次提交内把 R077 判定对齐 J-2/J-3，并通过
#    tests/test_r077_r054_tdsql_syntax.py 中裸索引名/反引号索引名两组
#    同语义用例。不得拆分提交。
```

Rev.O 让 `parsed.indexes` 开始产出 UNIQUE，**却没有在同一次提交内对齐 R077**。
而 ADJ-4（R077 宽松 OR）在 Rev.O 自己的台账里仍标着 **🔒 用户决策：永久关闭**。

### 6.3 消融实验：根因唯一

| 变体 | 三个专项套件 | 语料+生产漂移 |
|---|---|---|
| Rev.O 全量 | **5 failed / 66 passed** | **7 条** |
| 回退**表级** UNIQUE 修复、保留列级接线 | **71 passed** | **0 条** |
| 回退**列级** UNIQUE 接线、保留表级修复 | **5 failed / 66 passed** | **7 条** |

⇒ 5 项失败与 7 条漂移**全部且仅仅**来自表级 `_parse_unique_constraint()` 修复。
7 条漂移的表现是凭空多出 `R061` / `R067` / `R018` / `R019`
（R061 的 UNIQUE 分支正是上表 `test_u2` 守着的那段死代码被唤醒），
与 Rev.O §3.3c 安全边界自己预告的"隐式索引名引起次生误报"完全吻合 —— 预告写了，验证没做。

### 6.4 但是"回退它"不是解法

Rev.O 的耦合论证是**成立且承重**的。在回退表级修复的变体上实测：

```sql
CREATE TABLE `t` (`id` INT NOT NULL, `sk` INT NOT NULL UNIQUE, `c` VARCHAR(20),
  PRIMARY KEY (`id`,`sk`), UNIQUE KEY `uk` (`c`)) ENGINE=InnoDB shardkey=sk
```

| 变体 | `parsed.indexes` | R054（期望命中） |
|---|---|---|
| Rev.O 全量 | 列级 `sk` + 表级 `uk(c)` | **命中** ✅ |
| 回退表级修复 | 只有列级 `sk` | **不命中** ❌ 表级 UNIQUE 被静默吞掉 |

一旦列级 UNIQUE 置 `seen=True`，`_iter_unique_indexes` 早退，raw 正则回退关闭，
**同表的表级 UNIQUE 就整个消失**。所以两处必须同时成立。

### 6.5 结论：这是一个需要用户决策的死结

三者两两互斥：

| | 内容 | 出处 |
|---|---|---|
| A | 列级 UNIQUE 必须进入 `parsed.indexes` | Rev.O 裁定 #2（为修 R054 漏报） |
| B | `parsed.indexes` 一旦产出 UNIQUE，**必须同一次提交对齐 R077** | `distributed.py` 代码内契约 |
| C | **规则文件保持不动**，不得改 `distributed.py` | Rev.O 裁定 #1 |

A + C ⇒ 违反 B ⇒ R077 漏报（已实测）。**在 Rev.O 当前的范围裁定下，BLOCK-13-01 无法被正确实现。**

**整改要求**：不要在解析器侧继续绕。请把这个取舍原样交给用户裁决，二选一：

- **方案甲（推荐）**：撤销裁定 #1 中"规则文件一行不动"的限制，
  在**同一次提交**内把 R077 的"主键 或 唯一索引"判定对齐 J-2/J-3，
  并补齐 `test_r077_r054_tdsql_syntax.py` 裸索引名 / 反引号索引名两组同语义用例。
  代价：触及 v1.6.1.9 冻结代码与 ADJ-4 冻结决策，**必须取得用户明确批准**；
- **方案乙**：本期不让 `parsed.indexes` 产出 UNIQUE，
  BLOCK-13-01 改由**不经过 `_iter_unique_indexes` 早退路径**的独立通道供数
  （例如新增 `parsed.unique_index_semantics` 只给 R054 消费，R077 继续走原路），
  代价：多一条并行语义通道，需证明两条通道不会再次漂移。

无论走哪条，**准出必须包含"R077 在裸索引名与反引号索引名两组用例上结论不变"的双向断言**。

## 7. BLOCK-14-02：`CONSTRAINT … UNIQUE` 的失败关闭只覆盖恢复链，主路径仍静默漏审

### 7.1 事实

Rev.O 裁定 #3 明确写：「"消费后顺带恢复、下游看不见"不再允许；唯一合规处置是具名失败关闭。」
但失败关闭发生在 `_plan_recovery()` 里，而 `_plan_recovery()` **只在两处被调用**：
首次解析降级为 `Command` 时的重试、以及 `except` 分支。
**语句本身能被 sqlglot 正常解析时，规划器根本不会被调用。**

同一张表（已声明主键、分片键 `sk`、唯一索引在 `c` 上，**应命中 R054**）三条路径实测：

| 路径 | sqlglot 原生 | 最终 AST | E999 | `indexes` | R054 | |
|---|---|---|---|---|---|---|
| ① `CONSTRAINT uq UNIQUE (c)` | `Create` | `Create` | 无 | `[]` | **不命中** | ❌ 静默漏审 |
| ② 同上 + `TDSQL_DISTRIBUTED BY HASH(sk)` | `Command` | **`Command`** | **无** | `[]` | **不命中** | ❌ 静默漏审 |
| ③ 同上 + 目标 `UNIQUE … COMMENT` | ParseError | `NoneType` | 有 | `[]` | 命中 | ✅ |
| 对照 ④ 换成 `UNIQUE KEY uq (c)` | `Create` | `Create` | 无 | `uq(c)` | 命中 | ✅ |

三版 sqlglot 一致。`_UNIQUE_IDX_RE` 只匹配 `UNIQUE (KEY|INDEX)`，
**不认识 `CONSTRAINT … UNIQUE`**，所以 raw 正则回退也救不回来。

### 7.2 ② 号路径还是一处相对基线的**净退化**

| | 最终 AST | E999 | cols | 规则集合 |
|---|---|---|---|---|
| 主干基线 v1.6.2.1 | `Create` | 无 | 3 | `R005, R028, R029, R036, R037` |
| Rev.O | **`Command`** | **无** | **0** | `R005, R028` |

**`R029` / `R036` / `R037` 静默消失，且不报 E999。**
这正是 Rev.O 自己在 BLOCK-13-05 判定为不可接受的"无 E999 但结构为空"形态 ——
只不过这次出现在 `CONSTRAINT … UNIQUE` 上。

### 7.3 整改要求

- 失败关闭必须覆盖**所有三条路径**，不能只在恢复链内成立。
  语句无需恢复时也要有一道"唯一语义完整性"检查，
  发现已知不可完整供数的形态（`CONSTRAINT … UNIQUE`、`SERIAL`）就让整句失败关闭并保留 E999；
- ② 号路径**不得停在无 E999 的 `Command`**：要么恢复出完整结构，要么报 E999；
  "结构为空且不报错"是三者中最坏的一种；
- manifest 必须补这三条路径 × {`CONSTRAINT … UNIQUE`, `SERIAL`} 的笛卡尔积，
  并断言**最终规则集合**，不是断言 AST 类型。

## 8. BLOCK-14-03：证据面只写了规范，没有实现

`git diff 4d6968a..f92b20e -- docs/evidence/` **为空**。Rev.O 正文对 BLOCK-13-04 与 MAJOR-13-02
写了完整规范，但仓库里的六个文件一个字节都没改。逐项核对：

| Rev.O 正文规范 | 仓库实物 | |
|---|---|---|
| `run_all.py` 引入 `--mode design` / `--mode implementation` | 无 `--mode`、无 `argparse` | ❌ |
| 提供 29.0.0 / 30.14.0 / 30.17.0 隔离矩阵 `--matrix` | 无 `--matrix` | ❌ |
| 一键命令断言 30.14.0 pin | 脚本内无 `30.14.0` 字样 | ❌ |
| **输出仅 ASCII**（BLOCK-13-04 的直接起因是 Windows GBK 崩溃） | 脚本仍含 **424 个非 ASCII 字符** | ❌ |
| 重建器改用稳定 marker | `rebuild_from_design.py` 仍按 `blocks[n]` 位置索引，**不认 marker** | ❌ |
| 重建器认得 Rev.O 新增的 5 个代码块 | 硬编码只处理前 12 个块 | ❌ |
| manifest 的 `pos` 声明 `parsed_oracle` / `rules_oracle` | manifest 中 0 处 | ❌ |
| `pos_known` 断言 KFN 编号 | manifest 中 0 处 | ❌ |
| 变异候选不可解析不得静默 `continue` | 测试里仍有静默 `continue` | ❌ |
| 文档登记 `normalized_utf8_sha256` | 正文是占位符 `<design 生成后由脚本回填>`，**未回填** | ❌ |

在当前提交上直接执行文档给出的一键命令，结果是：

```text
❌ 设计说明书里找不到登记的目标哈希
❌ manifest 未全绿
❌ §7.1 与 manifest_doc.py 输出不一致
❌ §3.4 与 codestat.py 输出不一致
```

这与第十二轮 BLOCK-12-05 是同一条：**"文档写了"不等于"仓库交付了"**。
本轮情况还更麻烦一层 —— 旧脚本仍然存在且能跑，开发者会拿到一个**基于失效契约的假结论**。

**整改要求**：把六个文件按 Rev.O 规范实际改掉并提交；
`run_all.py` 必须在**当前提交上**即可执行并全绿；哈希占位符必须由脚本回填成真值。

## 9. MAJOR-14-01：两个代码块没有 BEFORE 锚点，机械重建无法定位

`COLUMN-UNIQUE-WIRE-AFTER` 与 `KFN-GATE-AFTER` 只有 AFTER、没有配对的 BEFORE。
正文用文字描述插入点（"`_parse_create()` 的 `exp.ColumnDef` 分支"、"候选门禁最先检查"），
而 marker 契约的全部意义就是**消除**这种文字定位。

我本轮是按文字描述人工插入的 —— 这意味着**我构建出的目标文件与 Codex 心中的目标未必逐字节相同**，
「照图施工」在这两处不成立。

**整改要求**：为这两处补 `*-BEFORE` 锚点块（哪怕只是紧邻的 2~3 行既有代码），
使全部改动点都能被机械定位；重建器逐对应用后必须能算出确定的哈希。

## 10. MAJOR-14-02：§7.1 的生成器标记被删除，陈旧数字未标注

- §3.4：Rev.O 保留了 `<!-- BEGIN AUTOGENERATED CODESTAT -->` 并**明确标注**
  "以下规模表是 Rev.N 历史生成物…评审或开发不得据此预设 2653 行" —— **处理正确**；
- §7.1：生成器标记被**整段删除**，正文仍留着 Rev.N 的
  `manifest 用例总数 501` / `pytest --collect-only -q 应收集 511`，**没有任何历史标注**。

而 Rev.O 裁定 #6 自己写着：「Rev.N 证据数字全部降为历史事实…**本文不预填人工数字**。」
§7.1 与该裁定直接冲突，且 501/511 现在既非当前值、也未被声明为历史值。

**整改要求**：§7.1 按 §3.4 的做法处理 —— 恢复生成区段标记，
要么标注为历史生成物待整段替换，要么由更新后的 `manifest_doc.py` 重新生成。

## 11. MAJOR-14-03：manifest 三条用例与 Rev.O 新口径冲突

| cid | 用例 | manifest 期望 | Rev.O 实际 | 原因 |
|---|---|---|---|---|
| `N-01` | `CONSTRAINT uq UNIQUE` + 真实目标，span 应为 1 | `spans=1` | `plan=REJECT`、`spans=0` | 裁定 #3 改为失败关闭 |
| `R12-TY-23` | `SERIAL` 应恢复 | `pos` | `plan=OK` + `KFN-5-SERIAL` + 最终 E999 | 裁定 #4 转 KFN-5 |
| `R12-CN-08` | `CONSTRAINT uq UNIQUE` 共存时整句仍可恢复 | `pos` | `plan=REJECT` | 裁定 #3 |

这三条**不是代码缺陷，是口径变更未同步真源**。但它使"唯一真源"当前处于自相矛盾状态。

**整改要求**：随 BLOCK-14-03 一并更新 manifest，
把这三条改判为 `neg` / `pos_known`（附 KFN 编号），并补 §7 的新反例族。

## 12. 下一轮开发准入门槛

- [ ] **BLOCK-14-01**：用户就 §6.5 的方案甲/乙作出决策；无论哪条，
      `test_r077_r054_tdsql_syntax.py` 恢复 **45 passed**，
      `test_parser_tdsql_dialect_fallback.py` **14 passed**，
      `test_r061_index_name_quoting.py` **12 passed**，全量 **0 failed**；
      语料 + 生产 14 表**非目标漂移回到 0 条**；
      R077 在裸索引名 / 反引号索引名两组同语义用例上结论不变；
- [ ] **BLOCK-14-02**：`CONSTRAINT … UNIQUE` 与 `SERIAL` 在**三条路径**（原生 Create / 方言 Command /
      需恢复）上均失败关闭并保留 E999；不存在"无 E999 且 `indexes` 为空"的状态；
      断言推进到规则集合；
- [ ] **BLOCK-14-03**：六个证据面文件按 Rev.O 规范实际提交；
      `python docs/evidence/v1.6.2.2/run_all.py` 在提交上直接全绿；
      哈希占位符已回填真值；默认 Windows/GBK 环境不崩溃；
- [ ] **MAJOR-14-01**：全部改动点均有 BEFORE 锚点，重建可机械完成；
- [ ] **MAJOR-14-02**：§7.1 与 §3.4 口径一致，无未标注的陈旧数字；
- [ ] **MAJOR-14-03**：manifest 与 Rev.O 口径一致，三条冲突用例已改判；
- [ ] 用户冻结决策保持不变；两份生产 fixture 规则集合精确相等；
- [ ] `verify_rules.py` 119 / 107 / 0 / 3（同名同因）；
- [ ] 三版 sqlglot 结论一致，发布锁定 30.14.0。

## 13. 本轮测试记录

```text
# 主干基线 4d6968a
python -m pytest tests/ -q                    → 1355 passed, 29 skipped, 0 failed

# Rev.O 目标 parser（按 marker 契约重建）
python -m pytest tests/ -q                    → 5 failed, 1350 passed, 29 skipped
python -m pytest tests/test_r077_r054_tdsql_syntax.py -q        → 3 failed, 42 passed
python -m pytest tests/test_parser_tdsql_dialect_fallback.py -q → 1 failed, 13 passed
python -m pytest tests/test_r061_index_name_quoting.py -q       → 1 failed, 11 passed
python -m pytest tests/test_parser.py -q                        → 14 passed
python tests/rule_audit_materials/verify_rules.py → 119/107/0/断言失败 3（同名同因）

# 消融实验（三个专项套件 + 语料漂移）
Rev.O 全量                  → 5 failed / 66 passed，漂移 7 条
回退表级 UNIQUE 修复          → 71 passed，          漂移 0 条
回退列级 UNIQUE 接线          → 5 failed / 66 passed，漂移 7 条

# 证据面
python docs/evidence/v1.6.2.2/run_all.py → 非零退出（哈希缺失 / manifest 3 失败 / 两个生成区段不一致）
现有 511 项 manifest（三版）              → 508 passed / 3 failed，三版一致

# 三版一致性（自造反例）
BLOCK-13-02 atom 内部位置 10 例   → 29.0.0 / 30.14.0 / 30.17.0 均 0 失败
CONSTRAINT UNIQUE 静默漏审        → 三版均 2 例
```

## 14. 最终意见

Rev.O 在**语法层**已经接近可施工：可执行注释位置、类型产生式、KFN 机制、具名 PRIMARY、
列 COMMENT 守恒五项我都独立验证为真实关闭，marker 契约方向也正确。

No-Go 的原因集中在三点：

1. **为修一个漏报，引入了另一个核心规则的漏报**，而且违反了代码里写明的"不得拆分提交"契约 ——
   这不是收紧尺度的问题，是范围裁定与既有契约冲突，**需要用户拍板**；
2. **"具名失败关闭"只在恢复链内成立**，主路径上 `CONSTRAINT … UNIQUE` 仍静默漏审，
   其中一条路径还比基线更差；
3. **证据面写了规范却没有实现**，一键命令在当前提交上直接失败 ——
   这恰是第十二轮 BLOCK-12-05 的同一条。

前两项都落在 R054 / R077 这两条本次要修的核心规则上，且都是"无 E999、结论静默变化"，
因此必须在进入开发前闭环。
