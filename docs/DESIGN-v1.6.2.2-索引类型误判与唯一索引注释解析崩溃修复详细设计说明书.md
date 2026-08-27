# DESIGN-v1.6.2.2 索引类型误判与唯一索引注释解析崩溃 修复详细设计说明书

| 项目 | 内容 |
|---|---|
| 文档版本 | **Rev.P**（在 Rev.O 原文上修订，处理智能体 A 第十四轮开发准入复审的 **3 BLOCK + 3 MAJOR**；Rev.O 及更早章节保留为历史追溯，凡与 Rev.P 冲突均以 Rev.P 为准） |
| 目标版本 | **v1.6.2.2** |
| 缺陷来源 | 内网人工扫描报告 #6309（gg77）、#6311（gg78） |
| 缺陷编号 | **DEF-1 = DEF-R054-FAKEUNIQUE**；**DEF-2 = DEF-PARSE-UKCOMMENT** |
| 原始设计 | 智能体 A |
| Rev.P 修订 | Codex |
| Rev.P 评审 | 智能体 A（待第十五轮独立评审） |
| 施工 | 待 Rev.P 通过后安排；本次修订**不改产品代码**，只修改设计说明书和 `docs/evidence/v1.6.2.2/` 设计证据 |
| 基线 commit | `03216b788412caa476bba49b9d8524de80919bf4`（main，Rev.P design 模式的不可变施工前产品基线；其产品文件与 `4d6968a` 一致） |
| 评审依据 | `docs/REVIEW-v1.6.2.2-索引解析修复设计Rev.O第十四轮开发准入独立复审报告-ClaudeA.md` |
| Rev.P 预期改动范围 | `parser_legacy.py`：Rev.O 恢复规划器、索引类型补丁、独立 `unique_constraints` 完整语义通道及全路径 KFN 预检；`distributed.py`：**只修改 R054 专属助手 `_iter_unique_indexes()` 读取新通道，R077 类与既有 `indexes/index_definitions` 行为保持不动**；`requirements.txt` / `pyproject.toml` 精确 pin；`docs/evidence/v1.6.2.2/` 落实 design/implementation 双模式证据 |
| 证据状态 | Rev.N 的 501 case / 511 pytest item 只作为**历史基线**。Rev.P design 实测：三版各 **524 passed**，发布版冻结专项 **71 passed**、全量 **1384 passed**，生成区段与 bundle 哈希一致；implementation 在产品未施工时按契约返回 `STATUS NOT_IMPLEMENTED` / 3，未伪装全绿 |

---

## Rev.P 修订说明（针对智能体 A 第十四轮开发准入复审）

> **本节是 Rev.P 的规范入口。** Rev.O 及更早版本保留用于追溯；涉及 UNIQUE 供数目的地、
> `CONSTRAINT … UNIQUE`/SERIAL 的失败关闭入口、证据交付阶段和施工 marker 的内容，均以本节、
> §3.3c～§3.3f、§5.32、§7.4 和附录 C 的 Rev.P 口径为准。

### 第十四轮六项意见的裁定

| 第十四轮项 | Rev.P 裁定 | 处理结果 |
|---|---|---|
| **BLOCK-14-01** 表级 UNIQUE 激活 R077 宽松分支 | **认可事实与 No-Go；不认可“必须再次请用户二选一”** | A 的 5 项冻结测试失败、7 条漂移及消融因果链成立。用户此前已明确永久关闭 ADJ-4，故没有重新放开 R077 收紧方案的授权。Rev.P 直接采用报告方案乙：新增 `ParsedSQL.unique_constraints` + `unique_constraints_complete`，列级/表级 UNIQUE 只进入该通道；R054 专属 `_iter_unique_indexes()` 消费它；`indexes/index_definitions` 与 R077 完全维持基线行为 |
| **BLOCK-14-02** 失败关闭仅覆盖恢复链 | **完全认可** | 增加独立于是否需要恢复的 source preflight。所有 `CREATE TABLE` 在原生 parse、Command 重试、except 三路之前均生成 `known_fidelity_failures`；`CONSTRAINT … UNIQUE` 记 `KFN-6-CONSTRAINT-UNIQUE`，SERIAL 两形态保留 KFN-5。命中后最终必须有 E999，不能出现无 E999 的空结构 |
| **BLOCK-14-03** 证据规范未落成实物 | **部分认可** | 认可 design 证据必须在评审阶段真实可执行，故本版同步升级 `docs/evidence/v1.6.2.2/` 的七个文件、回填 design 哈希并运行 `--mode design --matrix`。不认可“尚未施工时 implementation 也必须全绿”：implementation 的对象是当前产品文件，施工前全绿会证明它验错了对象；该模式本版必须可执行并以明确的 `NOT_IMPLEMENTED` 非零状态拒绝准出 |
| **MAJOR-14-01** 两个 AFTER-only 块不能机械定位 | **部分认可** | `COLUMN-UNIQUE-WIRE` 改成 BEFORE/AFTER 精确替换。`KFN-GATE` 不属于独立插入动作，它已包含在整体 `RECOVERY-MODULE-AFTER` 中；保留为 `ASSERT_CONTAINED` 是防重复插入的机械断言，不需要虚构一个基线 BEFORE。marker 更名为 `KFN-GATE-ASSERT-CONTAINED` 消除歧义 |
| **MAJOR-14-02** §7.1 标记被删除且 501/511 未标历史 | **不认可事实指控；加强可见性** | Rev.O 实际仍有 BEGIN/END 标记，且区段首行已写“Rev.N 历史基线”，见原文 marker。为避免阅读者越过 HTML 注释，本版在表格前再增加可见警示，并由更新后的生成器整体替换为 Rev.P 真值 |
| **MAJOR-14-03** manifest 三条旧期望冲突 | **完全认可** | N-01、R12-TY-23、R12-CN-08 改成新口径，并新增 R14-UQ、R14-KFN-CU、R14-KFN-SE、R14-KFN-DECOY 四组，覆盖 UNIQUE 隔离通道、三路径失败关闭与 KFN 反向鉴别；legacy 消费者零漂移及双模式状态由 runner 独立门禁 |

### Rev.P 的不可拆分语义契约

1. **完整 UNIQUE 语义与 legacy 索引列表分离。** `unique_constraints` 是 R054 的结构化真源；
   `indexes/index_definitions` 继续保持 v1.6.2.1 输出域，不能借解析器修复唤醒 R077/R061 的历史死分支。
2. **完整性必须显式。** 只有成功遍历完整 `exp.Create` 定义列表、每个支持域 UNIQUE 均精确提取后，
   `unique_constraints_complete=True`；完整时 R054 禁止 raw 回退，不完整时才允许既有 raw 回退。
3. **KFN 是全路径审核阻断，不是恢复器的偶然副作用。** source preflight 与 RecoveryPlan 共用同一个
   definition scanner/KFN 编号真源；sqlglot 原生返回 `Create` 也不得绕过。
4. **R077 冻结行为必须逐条守恒。** A 报告列出的 5 个现有测试必须恢复通过；裸索引名/反引号索引名
   两组的 R077 结论按既有冻结期望精确不变；R061/R067/R018/R019 非目标漂移必须为 0。
5. **设计证据和实现证据不能混称。** design 模式证明“文档可以机械施工且目标语义成立”；
   implementation 模式证明“当前产品文件就是该目标”。前者通过不代表产品已开发，后者施工前不得绿。

### Rev.P 六层守恒契约

| 层 | 必须保存的事实 | 准出断言 |
|---|---|---|
| Raw SQL | 原文、可执行注释字符 span、批准掩码 span | `parsed.raw_sql == input`；掩码等长且越界改写为 0 |
| Source preflight | 完整定义列表、KFN 编号、是否允许进入普通审核 | 三条解析路径对同一 KFN 给出同一 E999 结论；注释/字符串同名字样不得误阻断 |
| RecoveryPlan | head、definitions、tail、atom boundary、KFN | 每个 accepted/KFN token 都被唯一 consumer 认领；无跳过未知 token |
| CreateShape | 表名/顶层属性、列/索引/约束、表尾 | 正确候选通过；逐字段单点变异全部拒绝 |
| ParsedSQL | columns、column_comments、legacy indexes、unique_constraints、PK、table options | UNIQUE 新通道完整；legacy indexes 逐键等于基线；`unique_constraints_complete` 与解析路径一致 |
| RuleChecker | R054 目标变化；R077/R061 等非目标守恒 | R054 双向命中正确；5 个冻结测试全绿；语料非目标漂移为 0 |

---

## Rev.O 修订说明（针对 Codex 第十三轮开发准入独立复审：5 BLOCK + 2 MAJOR）

> **历史记录，不是 Rev.P 当前施工规范。** 本节保留 Rev.O 的问题来源与处置轨迹；凡与 Rev.P
> 的独立 UNIQUE 通道、全路径 KFN preflight 或证据分阶段契约冲突，均以 Rev.P 为准。
> 本轮不另起方案文件，不在规则层打补丁，也不把“恢复成 `Create`”当作完成；最终必须证明
> `raw SQL → RecoveryPlan → CreateShape → ParsedSQL → RuleChecker` 五层语义一致。

### Rev.O 的七项裁定

| 第十三轮项 | Rev.O 裁定 | 具体处理机制 | 失败策略 |
|---|---|---|---|
| **BLOCK-13-01** R054 唯一语义断流 | **认可，核心阻断** | 列级 `col TYPE UNIQUE [KEY]` 在 `_parse_create()` 中显式生成结构化索引；同时修复表级 `UniqueColumnConstraint.this=exp.Schema` 被现有函数返回空字典的问题，避免列级条目令 raw 回退提前关闭后反而漏掉表级 UNIQUE；`CONSTRAINT symbol UNIQUE` 继续按用户冻结决策在规划层失败关闭；`SERIAL` 转 **KFN-5** | 任何唯一语义不能被准确送到 `ParsedSQL.indexes` 时，整句不得恢复；混合列级/表级必须逐项等于源定义 |
| **BLOCK-13-02** 可执行注释 atom 内部搬移 | **认可，真实吞错** | 废止 `(owner_idx,payload)` 作为位置真源；用 sqlglot 主 token 的字符区间划分“无 token gap”，只在 gap 内定位 `/*!…*/` 的原始 `comment_start/comment_end`，记录左右 token 下标；`_scan_table_tail()` 只允许注释落在**完整 atom 的边界 gap** | 注释落在 ENGINE、CHARACTER SET、shardkey、TDSQL_DISTRIBUTED、PARTITION 或分区定义任一 atom 内部时，规划器返回 `None` |
| **BLOCK-13-03** 类型产生式/属性族不闭合 | **认可** | 类型匹配改为最长多 token 产生式；`TEXT/BLOB(M)` 的 M 不再硬限 65535；列约束消费器必须接收 `family`，`CHARACTER SET/COLLATE` 仅字符族可用；列级 `CHARACTER SET` 与表级共用 `_charset_kw_end()`；当前 pin 无法解析的 National varying、CHAR BYTE、ASCII/UNICODE、SERIAL DEFAULT VALUE 逐项进入 **KFN-5** | 合法但当前不能保真的形态必须 `plan.kfn` 具名、最终失败关闭；family 错配必须规划层拒绝 |
| **BLOCK-13-04** 证据不能准出 | **认可** | `run_all.py` 输出仅 ASCII；引入 `design`/`implementation` 两模式；design 从不可变 baseline blob 重建，implementation 直接验当前提交；哈希统一为“LF 规范化 UTF-8 文本 SHA256”；一键命令断言 30.14.0 pin，并提供 29.0.0/30.14.0/30.17.0 隔离矩阵 | 默认 Windows 命令、版本、哈希、正文区段、任一测试不一致均非零退出 |
| **BLOCK-13-05** 具名 PRIMARY 自身 COMMENT | **认可** | 源侧 `CONSTRAINT` 分支不得丢弃 `_consume_index_definition()` 返回的 PRIMARY COMMENT span；候选侧把 `exp.Constraint.expressions` 拆为“恰好一个 PrimaryKey + 允许的 Comment option”，比较 symbol、键列、USING 与 COMMENT 存在性 | 多主节点、未知 option、source/candidate COMMENT 存在性不一致均拒绝 |
| **MAJOR-13-01** 列 COMMENT 被门禁忽略 | **认可** | `COMMENT` 从 `_GATE_IGNORED_COL_CONSTRAINTS` 删除；门禁比较存在性，不比较文本值。文本仍由 `raw_sql` 与 `_extract_column_comment()` 保留，R029 的输入不得改变 | 删除或凭空增加列 COMMENT 的候选必须拒绝；仅转义/引号导致的等值文本差异不在门禁重复解释 |
| **MAJOR-13-02** 测试只验 AST | **认可** | `pos` 依语法类别必须声明 `parsed_oracle`/`rules_oracle`；R054 用例精确断言 indexes 与规则命中；`pos_known` 同时断言 `plan != None`、KFN 编号和最终非 Create；变异候选不可解析不得静默 `continue`；正文生成区段必须唯一且精确相等 | 只断言 `Create` 的新增 case 不得进入准出集 |

### Rev.O 的范围裁定

1. **规则文件保持不动。** R054 的 TDSQL 判据已经正确，问题在解析器没有提供完整唯一索引语义；不得在 `distributed.py` 再叠加字符串补丁。
2. **列级 UNIQUE 本期必须支持。** 腾讯 TDSQL 建表语法明确允许列级 UNIQUE；该形态又直接影响 R054，不能继续登记为 ADJ。
3. **`CONSTRAINT … UNIQUE` 本期不扩展支持。** 用户冻结决策保持不变，但“消费后顺带恢复、下游看不见”不再允许；唯一合规处置是具名失败关闭。
4. **SERIAL 本期转 KFN-5。** 只把 AST 恢复成 `SERIAL` 而不展开隐含 UNIQUE/NOT NULL/AUTO_INCREMENT 比继续 E999 更危险；未来若解除 KFN，必须一次性向所有消费者提供等价语义。
5. **TDSQL 判据优先。** 依据顺序仍是目标实例事实 → 腾讯 TDSQL 官方语法 → 用户冻结决策；只有腾讯明确声明继承 MySQL 的类型细节才引用 MySQL 手册。sqlglot 只负责词法与候选 AST，不决定语法合法性。
6. **Rev.N 证据数字全部降为历史事实。** Rev.O 新增多少 case、变异 suite 和 pytest item 只能由更新后的 manifest/生成器输出，本文不预填人工数字。

### Rev.O 的五层守恒契约

| 层 | 必须保存的事实 | 准出断言 |
|---|---|---|
| Raw SQL | 原文、可执行注释字符 span、批准掩码 span | `parsed.raw_sql == input`；掩码等长且越界改写为 0 |
| RecoveryPlan | head、definitions、tail、atom boundary、KFN | 每个 accepted/KFN token 都被唯一 consumer 认领；无跳过未知 token |
| CreateShape | 表名/顶层属性、列/索引/约束、表尾 | 正确候选通过；逐字段单点变异全部拒绝 |
| ParsedSQL | columns、column_comments、indexes、PK、table options | 与源 SQL 的审核语义精确一致，尤其列级 UNIQUE 不得丢失 |
| RuleChecker | R054/R029 及生产 fixture 精确规则集合 | 应命中/不命中双向断言；禁止只比较 AST 类型 |

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

## Rev.N 修订说明（针对 O 第十二轮"开发准入"独立复审：5 BLOCK + 2 MAJOR + 1 MINOR）

> **历史记录，不是 Rev.O 施工规范。** 本节保留 Rev.N 的问题来源与处置轨迹；其中“可执行注释
> owner”“SERIAL 可恢复”“CONSTRAINT UNIQUE 顺带消费”等结论均已被 Rev.O 推翻。发生冲突时，
> 以文首“Rev.P 修订说明”、§5.32、§7、附录 B 第 43～55 条和附录 C 为唯一有效口径。

> **本轮我方结论：8 条全部复现、全部认可，无异议条目。**
> 其中 **BLOCK-12-01/02/04 是真实的"吞错"风险**（本应失败关闭的 SQL 被恢复成 `Create`），
> **BLOCK-12-03 与 MAJOR-12-01 会把官方合法语法留在 E999 路径**。
> 按 O §14 的要求，本版按**五个闭环面**一次性收敛：
> 输入位置面 / 语句边界面 / 官方语法面 / 结构守恒面 / 证据面。

| 编号 | O 的结论 | 我方复现 | Rev.N 处置 |
|---|---|---|---|
| **BLOCK-12-01** | 可执行注释只校验"内容"，没有校验"插入位置"和全句组合 | ✅ 复现：4 类反例全部 `plan=True → Create` | `_collect_executable_comments()` 改为返回 `(owner_idx, payload)` **保留位置**；`_validate_executable_comments(toks, close_idx, …)` 增加位置判据；合法 payload 解析成 `PARTITION` atom **按源序合并进 `_scan_table_tail()` 的 atom 流**，与主 token 流**共用**同一份"二级分区至多一个"计数和同一张 capability profile 表 |
| **BLOCK-12-02** | `rstrip(";")` 在规划器之前吞掉了多终止分号 | ✅ 复现：`;;` / `;;;` / `; ;` 端到端全部恢复成 `Create` | `parse()` 新增 `sql_recover = sql.strip()`（**不删分号**），恢复链的三处调用点全部改用它；`sql_clean` 仅保留给既有正则回退与 `sqlglot.parse_one()`，那条路径行为一字不改。**全部 span 相对同一个字符串计算** |
| **BLOCK-12-03** | 结构化 `_TYPE_RULES` 仍不是官方语法的闭合集 | ✅ 复现：`FLOAT(54)` 误收、`FLOAT(0)`/`DEC`/`NCHAR`/`NVARCHAR`/`.2`/`SERIAL` 误拒、SET 成员数无上限 | 一个类型持有**一组产生式**：`FLOAT(p)`（0..53）与 `FLOAT(M,D)` 分开，逐条尝试。补齐 `DEC`/`NCHAR`/`NVARCHAR`/`CHARACTER`/`CHARACTER VARYING`/`SERIAL` 别名；实现 `SET` 成员数上限 64、`ENUM` 上限 65535；`.2` 在源侧规范成 `0.2` 与候选一致。仍不能恢复的官方形态**逐项登记 KFN-4**，不藏在普通 `plan=False` 里 |
| **BLOCK-12-04** | `SourceFingerprint.tail` 被生成但从未比较，顶层 CREATE 语义也未入指纹 | ✅ 复现：13 种单点变异**全部返回 True** | 指纹正式成为 **CreateShape**：`head`（全限定名 + `TEMPORARY` + `IF NOT EXISTS`）/ `definitions` / `tail`（本地表选项 + 分布 atom + 分区细节）。候选侧建镜像提取器 `_ast_head_shape()` / `_ast_tail_shape()`；方言 atom 与可执行注释分区标成 **source-only approved transform**，不与普通 table tail 混为一谈 |
| **BLOCK-12-05** | 测试"唯一真源"尚未成为仓库中的可执行证据 | ✅ 属实：`76df50f` 是 docs-only 提交，文档里的命令当时不能执行 | 六个资产**真正提交到仓库** `docs/evidence/v1.6.2.2/`（用户已冻结"只改 docs/"，故不放 `tests/`）；新增 `run_all.py` 一条命令在**临时目录**里重建补丁并跑通全部断言，**不需要 `git stash`**；设计说明书登记**重建目标 SHA256**，由脚本自动校验 |
| **MAJOR-12-01** | 官方 `CONSTRAINT symbol PRIMARY KEY` 被候选门禁系统性误杀 | ✅ 复现：`gate=False`，官方合法语句留在 E999 路径 | 候选侧解包 `exp.Constraint`，比较 constraint symbol 与内部 PRIMARY 结构；源侧指纹补记 symbol。**无名** `CONSTRAINT PRIMARY KEY` 三版 sqlglot 一致 ParseError → 登记 **KFN-4**。不触碰用户已冻结的 `CONSTRAINT … UNIQUE`（NG-10/ADJ-11） |
| **MAJOR-12-02** | 最终施工与验收指令仍有相互冲突的口径 | ✅ 复现 5 处 | K-1 的 `_TYPE_SPEC` → `_TYPE_RULES`；K-6 的 `_TAIL_EDGES` → `_TAIL_PROFILES`；附录 B 第 12/18 条冲突已合并成一条；第 9/13 条改为"主干只记 `baseline_observation`，不参与 pass/fail"；§7.4 的 `git stash && cp -r .` 作废，改为 `run_all.py` 的临时目录流程 |
| **MINOR-12-01** | 历史模板与统计表述仍需清理 | ✅ 复现：11 处字面量 `Rev.%s` 未替换 | 全部替换成真实版本号；文档头把"旧套件"与"含新增套件"分栏；三个计数口径（用例数 / 逐条断言数 / collect 数）在**第一次出现处**即写明定义，并由生成器输出 |

### Rev.N 的自查发现（不在 O 报告内，一并修掉）

复现 BLOCK-12-04 时把候选**回生成**送进同一套消费器，暴露出一个**跨版本词法差异**：

| 拼写 | 29.0.0 / 30.14.0 | 30.17.0 |
|---|---|---|
| `CHARSET` | 单个 `CHARACTER_SET` token | 单个 `CHARACTER_SET` token |
| `CHARACTER SET` | 单个 `CHARACTER_SET` token | **拆成 `CHAR` + `SET` 两个 token** |

Rev.M 只按 token 类型识别，于是**官方合法**的 `CHARACTER SET=utf8mb4` 在 30.17.0 上失败关闭
——这是 Rev.M 就已存在、只是没被用例覆盖到的潜伏缺陷。
Rev.N 新增 `_charset_kw_end()` 按**文本**兜住两种表现，并补 R12-CS 组 6 例锁定。

### Rev.N 新增的已知假阴性：KFN-4

以下官方合法形态在 **29.0.0 / 30.14.0 / 30.17.0 三版一致 ParseError**，
本方案的规划器已**具名接受**它们（不再藏在普通 `plan=False` 里），
但候选无法生成，故**失败关闭**并逐项登记：

| 形态 | 官方依据 | manifest cid |
|---|---|---|
| `INT SIGNED` / `BIGINT SIGNED` | MySQL 5.7 数值类型语法（腾讯声明继承） | `R12-TY-K-01/02` |
| `VARCHAR(n) BINARY` / `TEXT BINARY` | MySQL 5.7 字符串类型语法 | `R12-TY-K-03/04` |
| `NATIONAL CHAR(n)` / `NATIONAL VARCHAR(n)` | 同上 | `R12-TY-K-05/06` |
| `CONSTRAINT PRIMARY KEY (…)`（省略 symbol） | 腾讯建表语法 | `R12-CN-08` |
| 终止分号之后的普通注释 | 合法 MySQL 输入 | `R12-SC-K-01/02` |

- **与本次修复无关**：这些形态在当前生产版本 v1.6.2.1 上同样报 E999，**修复前后行为一致**；
- **语料频度**：197 条语料与生产 14 表中出现 **0 次**；
- **消除条件**：sqlglot 上游支持后自动消除，类型表与消费器已按官方语法登记完整，无需改本方案代码。

---

## Rev.M 修订说明（针对 O 第十一轮"开发准入"独立复审：7 BLOCK + 2 MAJOR + 2 MINOR）

> ⚠️ **本节为 Rev.M 历史，仅供变更说明**；其中的分类、门槛、数字**均可能已被后续修订取代**。当前准出门槛只看 §7.3，当前用例与计数只看 §7.1 由 manifest 生成的表。
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
> ⚠️ **本节为 Rev.L 历史，仅供变更说明**；其中的分类、门槛、数字**均可能已被后续修订取代**。当前准出门槛只看 §7.3，当前用例与计数只看 §7.1 由 manifest 生成的表。

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
> ⚠️ **本节为 Rev.K 历史，仅供变更说明**；其中的分类、门槛、数字**均可能已被后续修订取代**。当前准出门槛只看 §7.3，当前用例与计数只看 §7.1 由 manifest 生成的表。

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
> ⚠️ **本节为 Rev.J 历史，仅供变更说明**；其中的分类、门槛、数字**均可能已被后续修订取代**。当前准出门槛只看 §7.3，当前用例与计数只看 §7.1 由 manifest 生成的表。

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
> ⚠️ **本节为 Rev.I 历史，仅供变更说明**；其中的分类、门槛、数字**均可能已被后续修订取代**。当前准出门槛只看 §7.3，当前用例与计数只看 §7.1 由 manifest 生成的表。

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
> ⚠️ **本节为 Rev.H 历史，仅供变更说明**；其中的分类、门槛、数字**均可能已被后续修订取代**。当前准出门槛只看 §7.3，当前用例与计数只看 §7.1 由 manifest 生成的表。

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
> ⚠️ **本节为 Rev.G 历史，仅供变更说明**；其中的分类、门槛、数字**均可能已被后续修订取代**。当前准出门槛只看 §7.3，当前用例与计数只看 §7.1 由 manifest 生成的表。

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
> ⚠️ **本节为 Rev.F 历史，仅供变更说明**；其中的分类、门槛、数字**均可能已被后续修订取代**。当前准出门槛只看 §7.3，当前用例与计数只看 §7.1 由 manifest 生成的表。

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
> ⚠️ **本节为 Rev.E 历史，仅供变更说明**；其中的分类、门槛、数字**均可能已被后续修订取代**。当前准出门槛只看 §7.3，当前用例与计数只看 §7.1 由 manifest 生成的表。

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
> ⚠️ **本节为 Rev.D 历史，仅供变更说明**；其中的分类、门槛、数字**均可能已被后续修订取代**。当前准出门槛只看 §7.3，当前用例与计数只看 §7.1 由 manifest 生成的表。

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
> ⚠️ **本节为 Rev.C 历史，仅供变更说明**；其中的分类、门槛、数字**均可能已被后续修订取代**。当前准出门槛只看 §7.3，当前用例与计数只看 §7.1 由 manifest 生成的表。

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
> ⚠️ **本节为 Rev.B 历史，仅供变更说明**；其中的分类、门槛、数字**均可能已被后续修订取代**。当前准出门槛只看 §7.3，当前用例与计数只看 §7.1 由 manifest 生成的表。

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

两处核心缺陷及恢复链安全收敛主要改在 `parser_legacy.py`。Rev.P 另对
`distributed.py::_iter_unique_indexes()` 做**唯一、封闭的规则层接线**，使 R054 消费隔离的
`unique_constraints`；R077 类、其正则与 legacy `indexes/index_definitions` 输出域保持基线。
Rev.O/Rev.N 的历史机制仍保留追溯；最终改动点以 §3.4 与 §9 为准，不再沿用早期“规则层一行不动”
或“净改 3 点”的旧口径。
本说明书须经 A 独立评审通过后才能进入开发。

### 0.1 当前有效的官方判据与引用（Rev.P 复核，2026-08-27）

| 优先级 | 官方来源 | 本方案只采用的明确结论 |
|---|---|---|
| 1 | [腾讯云 TDSQL MySQL 版：建表](https://cloud.tencent.com/document/product/557/8767) | 分布式表的主键和**每一个**唯一索引都必须包含 shardkey；`column_definition` 明示 `[UNIQUE [KEY]]`，表定义明示 `[CONSTRAINT [symbol]] UNIQUE`，`index_option` 明示 `COMMENT 'string'`；广播表官方示例为 `shardkey=noshardkey_allset` |
| 2 | [腾讯云 TDSQL MySQL 版：兼容性](https://intl.cloud.tencent.com/zh/document/product/1042/38180) | 明示支持 MySQL 的所有数据类型，并逐类列出数字、字符、日期、空间和 JSON；同时明示支持 MySQL 的所有字符集和字符序。该页是本方案引用 MySQL 类型细节的 TDSQL 授权边界 |
| 3 | [MySQL 5.7：String Data Type Syntax](https://dev.mysql.com/doc/refman/5.7/en/string-type-syntax.html) | 仅补腾讯页未展开的类型产生式：National/CHAR BYTE/ASCII/UNICODE、字符族属性，以及**只有** `TEXT[(M)]`/`BLOB[(M)]` 带长度提示、六种 TINY/MEDIUM/LONG 具名变体不带 `(M)` |
| 4 | [MySQL 5.7：Numeric Type Syntax](https://dev.mysql.com/doc/refman/5.7/en/numeric-type-syntax.html) | 仅补腾讯兼容声明下的 `SERIAL` / `SERIAL DEFAULT VALUE` 隐含语义与数值参数边界，不据此覆盖 TDSQL 自有分布规则 |

判据冲突时仍按“目标实例 `SHOW CREATE TABLE` / 已验证 DDL → 腾讯 TDSQL 文档 → 用户冻结决策 →
腾讯明确兼容范围内的 MySQL 手册”处理。sqlglot 的 AST 或是否能解析只说明工具能力，**不构成语法
合法性证据**。因此，本版会把腾讯已确认合法、但当前 pin 不能保真的形态登记为 KFN 并失败关闭，
不会反过来把解析器缺口说成 TDSQL 非法。

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

<!-- BEGIN CODE: IMPORT-TOKENTYPE-AFTER -->
```python
from sqlglot.tokens import TokenType
```
<!-- END CODE: IMPORT-TOKENTYPE-AFTER -->

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

<!-- BEGIN CODE: RECOVERY-MODULE-AFTER -->
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


def _ident_text(tok):
    """标识符 token 的归一文本：去反引号、去首尾空白、转小写。"""
    return (tok.text or "").strip("` ").strip().lower()


def _tdsql_table_def_bounds(toks):
    """严格定位第一条建表语句的列定义列表，并产出顶层 CreateShape。

    返回 (左括号下标, 右括号下标, 表名, head)；任一环节不满足返回 (-1, -1, "", None)。

    `head = (qname, temporary, if_not_exists)`，其中 `qname = (schema, table)`
    —— 第十二轮 BLOCK-12-04：Rev.M 只保留最后一级表名，于是候选把 `db1.t` 换成
    `db2.t`、把 `CREATE TEMPORARY` 降成 `CREATE`、把 `IF NOT EXISTS` 删掉，
    门禁一律返回 True。这三项都有规则消费者（临时表标志直接进 R032），
    必须进入指纹。

    只接受：CREATE [TEMPORARY] TABLE [IF NOT EXISTS] <名>[.<名>] ( ... )
      * 表名只接受 VAR / IDENTIFIER，**STRING 一律拒绝**；
      * 表名之后必须**紧接**列定义左括号 —— CTAS(`AS SELECT`)、`LIKE`
        因此被拒，不会拿后续任意括号（如 CONCAT(...)）冒充定义列表。
    """
    n = len(toks)
    if n < 4 or toks[0].token_type != TokenType.CREATE:
        return -1, -1, "", None
    p = 1
    temporary = False
    if toks[p].token_type == TokenType.TEMPORARY:
        temporary = True
        p += 1
    if p >= n or toks[p].token_type != TokenType.TABLE:
        return -1, -1, "", None
    p += 1
    if_not_exists = False
    if (p + 2 < n and _is_bare_kw(toks[p], "IF")
            and toks[p + 1].token_type == TokenType.NOT
            and toks[p + 2].token_type == TokenType.EXISTS):
        if_not_exists = True
        p += 3
    if p >= n or toks[p].token_type not in _IDENT_TOKENS:
        return -1, -1, "", None
    table_name = toks[p].text
    schema = ""
    p += 1
    if (p + 1 < n and toks[p].token_type == TokenType.DOT
            and toks[p + 1].token_type in _IDENT_TOKENS):
        schema = _ident_text(toks[p - 1])
        table_name = toks[p + 1].text
        p += 2
    if p >= n or toks[p].token_type != TokenType.L_PAREN:
        return -1, -1, "", None
    head = ((schema, (table_name or "").strip("` ").strip().lower()),
            temporary, if_not_exists)
    open_idx = p
    d = 0
    while p < n:
        if toks[p].token_type == TokenType.L_PAREN:
            d += 1
        elif toks[p].token_type == TokenType.R_PAREN:
            d -= 1
            if d == 0:
                return open_idx, p, table_name, head
        p += 1
    return -1, -1, "", None




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

# ── 产生式记法（第十二轮 BLOCK-12-03）────────────────────────────────────────
#
# Rev.M 的 `名 → 单一 arity` 表达不了"同一个关键字有多条合法产生式"。
# 最典型的是 FLOAT：官方同时存在 `FLOAT(p)`（p∈0..53，单参数、语义是精度位数）
# 与 `FLOAT(M,D)`（M∈1..255、D∈0..30）。Rev.M 把两者塞进同一个 `M_D`，
# 于是**同时**造成合法下界 `FLOAT(0)` 被误拒、非法上界 `FLOAT(54)` 被误收。
#
# 本版每个类型持有**一组**产生式，逐条尝试，命中任意一条即可：
#   _P_NONE            无括号
#   _P(*ranges)        恰好 len(ranges) 个整数参数，逐个落在对应闭区间
#   _P_VALUES(n)       括号内 1..n 个字符串字面量（ENUM/SET）
_P_NONE = ("NONE", ())


def _P(*ranges):
    return ("ARGS", ranges)


def _P_VALUES(max_members):
    return ("VALUES", max_members)


_INT_P = (_P_NONE, _P((1, 255)))
# MySQL/TDSQL 只有 BLOB[(M)] / TEXT[(M)] 的 M 是“选择最小可容纳类型”的长度提示，
# 不是 TEXT 本体 65535 的硬语法上限。允许到 LONGTEXT/LONGBLOB 的最大长度；
# 具体存储类型由数据库决定，审核器只保存源参数。TINY/MEDIUM/LONG 具名变体的
# 官方产生式没有 `(M)`，必须保持 `_P_NONE`，不能复用本组而误收 TINYTEXT(256)。
_LOB_P = (_P_NONE, _P((0, 4294967295)))
_FSP_P = (_P_NONE, _P((0, 6)))
_TYPE_RULES = {
    # 源名                : (canonical,   产生式组,                                    族)
    "TINYINT":             ("TINYINT",    _INT_P,                                     _F_INT),
    "SMALLINT":            ("SMALLINT",   _INT_P,                                     _F_INT),
    "MEDIUMINT":           ("MEDIUMINT",  _INT_P,                                     _F_INT),
    "INT":                 ("INT",        _INT_P,                                     _F_INT),
    "INTEGER":             ("INT",        _INT_P,                                     _F_INT),
    "BIGINT":              ("BIGINT",     _INT_P,                                     _F_INT),
    # SERIAL = BIGINT UNSIGNED NOT NULL AUTO_INCREMENT UNIQUE。Rev.N 只保留类型名，
    # 会让 R054/R038 等消费者看不到隐含约束。Rev.O 仍让规划器具名识别它，
    # 但通过 `_TYPE_KFN_CANONICAL` 标成 KFN-5，最终必须失败关闭。
    "SERIAL":              ("SERIAL",     (_P_NONE,),                                 _F_OTHER),
    "DECIMAL":             ("DECIMAL",    (_P_NONE, _P((1, 65)), _P((1, 65), (0, 30))), _F_DEC),
    "NUMERIC":             ("DECIMAL",    (_P_NONE, _P((1, 65)), _P((1, 65), (0, 30))), _F_DEC),
    "DEC":                 ("DECIMAL",    (_P_NONE, _P((1, 65)), _P((1, 65), (0, 30))), _F_DEC),
    "FIXED":               ("DECIMAL",    (_P_NONE, _P((1, 65)), _P((1, 65), (0, 30))), _F_DEC),
    # FLOAT 有两条产生式，先试 (p) 再试 (M,D)——见上方说明
    "FLOAT":               ("FLOAT",      (_P_NONE, _P((0, 53)), _P((1, 255), (0, 30))), _F_DEC),
    "REAL":                ("FLOAT",      (_P_NONE, _P((1, 255), (0, 30))),           _F_DEC),
    "DOUBLE":              ("DOUBLE",     (_P_NONE, _P((1, 255), (0, 30))),           _F_DEC),
    "DOUBLE PRECISION":    ("DOUBLE",     (_P_NONE, _P((1, 255), (0, 30))),           _F_DEC),
    "CHAR":                ("CHAR",       (_P_NONE, _P((0, 255))),                    _F_STR),
    "NCHAR":               ("CHAR",       (_P_NONE, _P((0, 255))),                    _F_STR),
    "CHARACTER":           ("CHAR",       (_P_NONE, _P((0, 255))),                    _F_STR),
    "VARCHAR":             ("VARCHAR",    (_P((0, 65535)),),                          _F_STR),
    "NVARCHAR":            ("VARCHAR",    (_P((0, 65535)),),                          _F_STR),
    "CHARACTER VARYING":   ("VARCHAR",    (_P((0, 65535)),),                          _F_STR),
    "BINARY":              ("BINARY",     (_P_NONE, _P((0, 255))),                    _F_BIN),
    "VARBINARY":           ("VARBINARY",  (_P((0, 65535)),),                          _F_BIN),
    "TINYTEXT":            ("TINYTEXT",   (_P_NONE,),                                 _F_STR),
    "TEXT":                ("TEXT",       _LOB_P,                                     _F_STR),
    "MEDIUMTEXT":          ("MEDIUMTEXT", (_P_NONE,),                                 _F_STR),
    "LONGTEXT":            ("LONGTEXT",   (_P_NONE,),                                 _F_STR),
    "TINYBLOB":            ("TINYBLOB",   (_P_NONE,),                                 _F_BIN),
    "BLOB":                ("BLOB",       _LOB_P,                                     _F_BIN),
    "MEDIUMBLOB":          ("MEDIUMBLOB", (_P_NONE,),                                 _F_BIN),
    "LONGBLOB":            ("LONGBLOB",   (_P_NONE,),                                 _F_BIN),
    # ENUM 上限 65535 个成员、SET 上限 64 个成员（MySQL 5.7 字符串类型语法）
    "ENUM":                ("ENUM",       (_P_VALUES(65535),),                        _F_STR),
    "SET":                 ("SET",        (_P_VALUES(64),),                           _F_STR),
    "DATE":                ("DATE",       (_P_NONE,),                                 _F_TIME),
    "YEAR":                ("YEAR",       (_P_NONE, _P((4, 4))),                      _F_TIME),
    "TIME":                ("TIME",       _FSP_P,                                     _F_TIME),
    "DATETIME":            ("DATETIME",   _FSP_P,                                     _F_TIME),
    "TIMESTAMP":           ("TIMESTAMP",  _FSP_P,                                     _F_TIME),
    "BIT":                 ("BIT",        (_P_NONE, _P((1, 64))),                     _F_OTHER),
    "BOOL":                ("BOOLEAN",    (_P_NONE,),                                 _F_OTHER),
    "BOOLEAN":             ("BOOLEAN",    (_P_NONE,),                                 _F_OTHER),
    "JSON":                ("JSON",       (_P_NONE,),                                 _F_OTHER),
    "GEOMETRY":            ("GEOMETRY",   (_P_NONE,),                                 _F_OTHER),
    "POINT":               ("POINT",      (_P_NONE,),                                 _F_OTHER),
    "LINESTRING":          ("LINESTRING", (_P_NONE,),                                 _F_OTHER),
    "POLYGON":             ("POLYGON",    (_P_NONE,),                                 _F_OTHER),
    "MULTIPOINT":          ("MULTIPOINT", (_P_NONE,),                                 _F_OTHER),
    "MULTILINESTRING":     ("MULTILINESTRING",   (_P_NONE,),                          _F_OTHER),
    "MULTIPOLYGON":        ("MULTIPOLYGON",      (_P_NONE,),                          _F_OTHER),
    "GEOMETRYCOLLECTION":  ("GEOMETRYCOLLECTION", (_P_NONE,),                         _F_OTHER),
}
# 多 token 类型名。⚠️ sqlglot 对 `DOUBLE PRECISION` 的词法表现随上下文而异，
# 故两种表现都要能进：这里既登记二元组，`_TYPE_RULES` 也含单词 `DOUBLE`。
_TYPE_MULTIWORD = {
    # 最长匹配优先。sqlglot 可能把 `CHARACTER VARYING` / `CHAR VARYING`
    # 合成一个 token，也可能拆成两个 token；两种词法形态必须映射到同一 canonical。
    ("NATIONAL", "CHARACTER", "VARYING"): "VARCHAR",
    ("NATIONAL", "CHAR", "VARYING"): "VARCHAR",
    ("NATIONAL", "CHARACTER VARYING"): "VARCHAR",
    ("NATIONAL", "CHAR VARYING"): "VARCHAR",
    ("NATIONAL CHARACTER VARYING",): "VARCHAR",
    ("NATIONAL CHAR VARYING",): "VARCHAR",
    ("NCHAR", "VARCHAR"): "VARCHAR",
    ("NCHAR VARCHAR",): "VARCHAR",
    ("CHAR", "BYTE"): "BINARY",
    ("CHAR BYTE",): "BINARY",
    ("DOUBLE", "PRECISION"): "DOUBLE PRECISION",
    # `NATIONAL CHAR` / `NATIONAL VARCHAR` 是官方别名，词法上是**两个** token；
    # sqlglot 30.14.0 三版均 ParseError → 已登记 KFN-A（见 §5.21.5 KFN-4）。
    # 这里登记是为了让它落在具名 KFN，而不是藏在普通 plan=False 里。
    ("NATIONAL", "CHAR"): "CHAR",
    ("NATIONAL", "CHARACTER"): "CHAR",
    ("NATIONAL", "VARCHAR"): "VARCHAR",
    ("NATIONAL CHAR",): "CHAR",
    ("NATIONAL CHARACTER",): "CHAR",
    ("NATIONAL VARCHAR",): "VARCHAR",
}
# 这些官方形态当前发布 pin 30.14.0 不能生成可保真的候选 AST；规划器必须具名
# 接受并落入 KFN-5，不能继续藏在普通 plan=False 中。
_TYPE_KFN_MULTIWORD = {
    ("NATIONAL", "CHARACTER", "VARYING"): "KFN-5-NATIONAL-VARYING",
    ("NATIONAL", "CHAR", "VARYING"): "KFN-5-NATIONAL-VARYING",
    ("NATIONAL", "CHARACTER VARYING"): "KFN-5-NATIONAL-VARYING",
    ("NATIONAL", "CHAR VARYING"): "KFN-5-NATIONAL-VARYING",
    ("NATIONAL CHARACTER VARYING",): "KFN-5-NATIONAL-VARYING",
    ("NATIONAL CHAR VARYING",): "KFN-5-NATIONAL-VARYING",
    ("NCHAR", "VARCHAR"): "KFN-5-NCHAR-VARCHAR",
    ("NCHAR VARCHAR",): "KFN-5-NCHAR-VARCHAR",
    ("CHAR", "BYTE"): "KFN-5-CHAR-BYTE",
    ("CHAR BYTE",): "KFN-5-CHAR-BYTE",
    ("NATIONAL", "CHAR"): "KFN-4-NATIONAL",
    ("NATIONAL", "CHARACTER"): "KFN-4-NATIONAL",
    ("NATIONAL", "VARCHAR"): "KFN-4-NATIONAL",
    ("NATIONAL CHAR",): "KFN-4-NATIONAL",
    ("NATIONAL CHARACTER",): "KFN-4-NATIONAL",
    ("NATIONAL VARCHAR",): "KFN-4-NATIONAL",
}
_TYPE_KFN_CANONICAL = {
    "SERIAL": "KFN-5-SERIAL",
    "POINT": "KFN-3-SPATIAL-TYPE",
    "LINESTRING": "KFN-3-SPATIAL-TYPE",
    "POLYGON": "KFN-3-SPATIAL-TYPE",
    "MULTIPOINT": "KFN-3-SPATIAL-TYPE",
    "MULTILINESTRING": "KFN-3-SPATIAL-TYPE",
    "MULTIPOLYGON": "KFN-3-SPATIAL-TYPE",
    "GEOMETRYCOLLECTION": "KFN-3-SPATIAL-TYPE",
}
# 类型属性按**族**开放：数值族才能 UNSIGNED/ZEROFILL，字符族才能
# BINARY/ASCII/UNICODE。ASCII/UNICODE 是官方别名，但当前 pin 不能解析，故具名 KFN。
_TYPE_ATTRS_BY_FAMILY = {
    _F_INT:   ("UNSIGNED", "SIGNED", "ZEROFILL"),
    _F_DEC:   ("UNSIGNED", "SIGNED", "ZEROFILL"),
    _F_STR:   ("BINARY", "ASCII", "UNICODE"),
    _F_BIN:   (),
    _F_TIME:  (),
    _F_OTHER: (),
}
_TYPE_KFN_ATTRS = {
    "SIGNED": "KFN-4-SIGNED",
    "BINARY": "KFN-4-CHAR-BINARY",
    "ASCII": "KFN-5-ASCII",
    "UNICODE": "KFN-5-UNICODE",
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


def _try_type_production(toks, j, stop, prod):
    """按**单条产生式**消费类型参数；返回 (下一个下标, 参数元组) 或 (-1, None)。"""
    kind, spec = prod
    has_paren = j < stop and toks[j].token_type == TokenType.L_PAREN
    if kind == "NONE":
        return (j, ()) if not has_paren else (-1, None)
    if not has_paren:
        return -1, None                                # 该产生式要求括号
    k = j + 1
    if kind == "VALUES":
        vals = []
        while True:
            if k >= stop or toks[k].token_type != TokenType.STRING:
                return -1, None                        # 必须是字符串字面量
            vals.append(_unquote_str(toks[k]))
            k += 1
            if k < stop and toks[k].token_type == TokenType.COMMA:
                k += 1
                continue
            break
        if not vals or len(vals) > spec:
            return -1, None                            # 空值表 / 超出成员数上限
        args = tuple(vals)                             # **保留逐值内容**，不只记数量
    else:                                              # ARGS
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
        if len(nums) != len(spec):
            return -1, None                            # 参数个数不匹配本产生式
        for idx, v in enumerate(nums):
            if not _in_range(v, spec[idx]):
                return -1, None                        # 越界（FLOAT(54)/BIT(65)/CHAR(256)…）
        if len(nums) == 2 and nums[1] > nums[0]:
            return -1, None                            # scale 不得大于 precision
        args = tuple(nums)
    if k >= stop or toks[k].token_type != TokenType.R_PAREN:
        return -1, None
    return k + 1, args


def _consume_data_type(toks, i, stop):
    """按结构化规则表消费列数据类型。

    返回 `(下一个下标, (canonical, 参数元组, 属性元组, family, KFN元组))`
    或 `(-1, None)`。family 必须继续传给列约束消费器，禁止非字符类型接收
    CHARACTER SET/COLLATE；KFN 使规划器具名接受、候选门禁强制失败关闭。
    源侧与候选侧**共用本函数**，从而消除 `INTEGER`/`NUMERIC`/`DEC`/`NCHAR` 等别名
    以及 `ZEROFILL` 被 AST 丢弃导致的假不一致（第十一/十二轮 BLOCK-11-04 / 12-03）。
    """
    if i >= stop:
        return -1, None
    src = (toks[i].text or "").upper()
    j = i + 1
    rule = None
    matched_words = None
    # sqlglot 不同版本可能把同一产生式切成 1/2/3 个 token；按“token 数优先、
    # token 内可含空格”的登记表最长匹配，不能让 NATIONAL CHAR 抢走后面的 VARYING。
    for width in (3, 2, 1):
        if i + width <= stop:
            words = tuple((toks[q].text or "").upper() for q in range(i, i + width))
            if words in _TYPE_MULTIWORD:
                rule = _TYPE_RULES[_TYPE_MULTIWORD[words]]
                matched_words = words
                j = i + width
                break
    if rule is None:
        if toks[i].token_type in _NON_KEYWORD_TOKENS:
            return -1, None
        rule = _TYPE_RULES.get(src)
        if rule is None:
            return -1, None
    canonical, productions, family = rule
    # 逐条尝试产生式，命中任意一条即可；全部不命中 → 失败关闭
    nxt, args = -1, None
    for prod in productions:
        nxt, args = _try_type_production(toks, j, stop, prod)
        if nxt >= 0:
            break
    if nxt < 0:
        return -1, None
    j = nxt
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
    kfns = []
    if matched_words in _TYPE_KFN_MULTIWORD:
        kfns.append(_TYPE_KFN_MULTIWORD[matched_words])
    if canonical in _TYPE_KFN_CANONICAL:
        kfns.append(_TYPE_KFN_CANONICAL[canonical])
    kfns.extend(_TYPE_KFN_ATTRS[a] for a in attrs if a in _TYPE_KFN_ATTRS)
    return j, (canonical, args, keep, family, tuple(sorted(set(kfns))))


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
                        "COLUMN_FORMAT", "ENGINE_ATTRIBUTE", "KFN")
# sqlglot 回生成列定义时**不保留**这些约束（实测），故它们记入源指纹但不参与候选比对
_COL_CONSTRAINT_NOT_IN_AST = ("COLUMN_FORMAT", "ENGINE_ATTRIBUTE")


def _canonical_number(text):
    """数值字面量的规范形。

    第十二轮 BLOCK-12-03：腾讯官方把 `.2` 列为支持的数值字面量，
    sqlglot 回生成时写作 `0.2`（实测）。源侧按字面记就永远等不上候选侧，
    于是合法的 `DEFAULT .2` 被门禁误拒。这里统一补零。
    十六进制 `0x1F`、位串 `b'101'`、科学计数法保持原样（两侧一致）。
    """
    t = (text or "").strip()
    if t.startswith("."):
        return "0" + t
    if t.startswith("-.") or t.startswith("+."):
        return t[0] + "0" + t[1:]
    return t


def _consume_default_value(toks, i, stop):
    """消费 DEFAULT / ON UPDATE 的值；返回 (下一个下标, 值指纹) 或 (-1, None)。

    第十一轮 BLOCK-11-04：时间函数精度必须落在 0~6，
    `DEFAULT CURRENT_TIMESTAMP(7)` 不得放行。
    """
    if i >= stop:
        return -1, None
    tt = toks[i].token_type
    # 腾讯官方把 `.2` 列为支持的数值字面量；词法器把它切成 `DOT` + `NUMBER`
    # 两个 token（实测），Rev.M 只认单个 NUMBER，于是合法字面量被误拒
    # （第十二轮 BLOCK-12-03）。这里显式识别"无整数部分的小数"。
    if tt == TokenType.DOT:
        if i + 1 < stop and toks[i + 1].token_type == TokenType.NUMBER:
            return i + 2, ("num", _canonical_number("." + (toks[i + 1].text or "")))
        return -1, None
    if tt in (TokenType.DASH, TokenType.PLUS):
        # 符号**只能**修饰数值字面量
        sign = "-" if tt == TokenType.DASH else ""
        if i + 2 < stop and toks[i + 1].token_type == TokenType.DOT \
                and toks[i + 2].token_type == TokenType.NUMBER:
            return i + 3, ("num", _canonical_number(
                sign + "." + (toks[i + 2].text or "")))
        if i + 1 < stop and toks[i + 1].token_type == TokenType.NUMBER:
            # 正号归一：sqlglot 回生成时丢弃 `+`（实测 `DEFAULT +1` → `DEFAULT 1`），
            # 两侧必须得到同一规范形，否则合法正例会被门禁误拒。
            return i + 2, ("num", sign + _canonical_number(toks[i + 1].text))
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
        if tt == TokenType.NULL:
            return i + 1, ("null",)
        if tt == TokenType.NUMBER:
            return i + 1, ("num", _canonical_number(toks[i].text))
        if tt == TokenType.STRING:
            return i + 1, ("lit", tt.name, _unquote_str(toks[i]))
        return i + 1, ("lit", tt.name, (toks[i].text or ""))
    return -1, None                                    # 裸标识符 / 任意表达式 → 失败关闭


def _consume_column_constraints(toks, i, stop, family):
    """消费列约束序列；返回 (下一个下标, 约束元组, 可掩码 span) 或 (-1, None, [])。

    `family` 来自 `_consume_data_type()`，是列级 CHARACTER SET/COLLATE 的授权边界；
    非字符族不能因为 sqlglot 恰好能生成 AST 就被放行（第十三轮 BLOCK-13-03）。

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
        elif tt == TokenType.COLLATE:
            if family != _F_STR:
                return -1, None, []                   # INT/DATE/JSON COLLATE → 失败关闭
            if not (j + 1 < stop and toks[j + 1].token_type in _OPT_NAMEY):
                return -1, None, []
            ident, val, j = "COLLATE", (toks[j + 1].text or "").lower(), j + 2
        elif _charset_kw_end(toks, j, stop) >= 0:
            if family != _F_STR:
                return -1, None, []                   # INT/DATE/JSON CHARACTER SET → 失败关闭
            k = _charset_kw_end(toks, j, stop)
            if not (k < stop and toks[k].token_type in _OPT_NAMEY):
                return -1, None, []
            ident, val, j = "CHARACTER_SET", (toks[k].text or "").lower(), k + 1
        elif tt == TokenType.PRIMARY_KEY:
            ident, val, j = "KEYNESS", "PRIMARY", j + 1
        elif tt == TokenType.UNIQUE:
            j += 1
            if j < stop and toks[j].token_type == TokenType.KEY:
                j += 1
            ident, val = "KEYNESS", "UNIQUE"
        elif tt == TokenType.KEY:
            ident, val, j = "KEYNESS", "KEY", j + 1
        elif (_is_bare_kw(toks[j], "SERIAL") and j + 2 < stop
              and toks[j + 1].token_type == TokenType.DEFAULT
              and _is_bare_kw(toks[j + 2], "VALUE")):
            # `SERIAL DEFAULT VALUE` = NOT NULL AUTO_INCREMENT UNIQUE 的约束别名。
            # 本期不做不完整展开；规划器具名接受后由 KFN-5 强制失败关闭。
            ident, val, j = "KFN", "KFN-5-SERIAL-DEFAULT-VALUE", j + 3
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
    family = shape[3]
    j, cons, spans = _consume_column_constraints(toks, j, stop, family)
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


def _unquote_str(tok):
    """字符串字面量的归一内容：去外层引号并还原成对转义。

    源侧可能写 `COMMENT="x"`，候选回生成一律是 `COMMENT='x'`；
    不做归一会把同一个值判成不相等（第十二轮 BLOCK-12-04）。
    """
    txt = (tok.text or "")
    if len(txt) >= 2 and txt[0] == txt[-1] and txt[0] in ("'", '"'):
        q = txt[0]
        return txt[1:-1].replace(q + q, q).replace("\\" + q, q)
    return txt


def _consume_value_list(toks, i, stop):
    """消费 `( 字面量 [, 字面量]* )`；返回 (下一个下标, 值元组) 或 (-1, None)。

    第十轮 BLOCK-J5：**符号只能修饰数值**。Rev.J 先可选吃掉 DASH 再统一接受
    NUMBER 或 STRING，于是 `VALUES IN (-'x')` 被恢复为 Create。
    第十二轮 BLOCK-12-04：Rev.M 只返回**个数**，于是候选把
    `VALUES LESS THAN (10)` 改成 `(99)`、把 `VALUES IN (1,2)` 改成 `(8,9)`，
    指纹完全相同、门禁放行。本版返回逐个归一后的值。
    """
    if i >= stop or toks[i].token_type != TokenType.L_PAREN:
        return -1, None
    j, vals = i + 1, []
    while True:
        if j < stop and toks[j].token_type in (TokenType.DASH, TokenType.PLUS):
            if not (j + 1 < stop and toks[j + 1].token_type == TokenType.NUMBER):
                return -1, None                        # 符号后必须是数字
            sign = "-" if toks[j].token_type == TokenType.DASH else ""
            vals.append(("num", sign + _canonical_number(toks[j + 1].text)))
            j += 2
        elif j < stop and toks[j].token_type == TokenType.NUMBER:
            vals.append(("num", _canonical_number(toks[j].text)))
            j += 1
        elif j < stop and toks[j].token_type == TokenType.STRING:
            vals.append(("str", _unquote_str(toks[j])))
            j += 1
        else:
            return -1, None
        if j < stop and toks[j].token_type == TokenType.COMMA:
            j += 1
            continue
        if j < stop and toks[j].token_type == TokenType.R_PAREN:
            return j + 1, tuple(vals)
        return -1, None


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
        k, vals = _consume_value_list(toks, j, stop)
        return (k, ("LESS_THAN", vals)) if k >= 0 else (-1, "")
    if method == "LIST":
        if not (j < stop and toks[j].token_type == TokenType.IN):
            return -1, ""
        k, vals = _consume_value_list(toks, j + 1, stop)
        return (k, ("IN", vals)) if k >= 0 else (-1, "")
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
        defs.append((pname, vshape, oshape))
        if j < stop and toks[j].token_type == TokenType.COMMA:
            j += 1
            continue
        if j < stop and toks[j].token_type == TokenType.R_PAREN:
            return j + 1, spans, tuple(defs)
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
    return j, spans, ("part", method, eshape, dshape)


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


def _charset_kw_end(toks, i, stop):
    """识别 `CHARSET` / `CHARACTER SET` 关键字，返回其**之后**的下标；不是则返回 -1。

    ⚠️ 词法表现随 sqlglot 版本变化（三版实测）：
      · `CHARSET`          三版都是单个 `CHARACTER_SET` token；
      · `CHARACTER SET`    30.14.0 / 29.0.0 是单个 `CHARACTER_SET` token，
                           **30.17.0 拆成 `CHAR` + `SET` 两个 token**。
    只认 token 类型会让 `CHARACTER SET=utf8mb4` 在 30.17.0 上失败关闭
    （候选回生成用的正是这个拼写，于是合法正例被判成不守恒）。
    这里按**文本**兜住两种表现。
    """
    if i >= stop:
        return -1
    if toks[i].token_type == TokenType.CHARACTER_SET:
        return i + 1
    if (_is_bare_kw(toks[i], "CHARACTER") and i + 1 < stop
            and _is_bare_kw(toks[i + 1], "SET")):
        return i + 2
    return -1


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
            return j + 1, _unquote_str(t)              # 记录实际文本，不是 <str>
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
        k = _charset_kw_end(toks, i + 1, stop)
        if k >= 0:
            j, v = _take(k, "NAMEY")
            return (j, "CHARSET", ("CHARSET", v)) if j >= 0 else (-1, "", "")
        if i + 1 < stop and toks[i + 1].token_type == TokenType.COLLATE:
            j, v = _take(i + 2, "NAMEY")
            return (j, "COLLATE", ("COLLATE", v)) if j >= 0 else (-1, "", "")
        return -1, "", ""
    k = _charset_kw_end(toks, i, stop)
    if k >= 0:
        j, v = _take(k, "NAMEY")
        return (j, "CHARSET", ("CHARSET", v)) if j >= 0 else (-1, "", "")
    if tt == TokenType.COLLATE:
        j, v = _take(i + 1, "NAMEY")
        return (j, "COLLATE", ("COLLATE", v)) if j >= 0 else (-1, "", "")
    if tt == TokenType.COMMENT:
        j, v = _take(i + 1, "STR")
        return (j, "COMMENT", ("COMMENT", v)) if j >= 0 else (-1, "", "")
    if tt == TokenType.AUTO_INCREMENT:
        j, v = _take(i + 1, "POSINT")
        return (j, "AUTO_INCREMENT", ("AUTO_INCREMENT", v)) if j >= 0 else (-1, "", "")
    if tt == TokenType.VAR and txt in _TBL_OPT_SPEC:
        pred, _prov = _TBL_OPT_SPEC[txt]
        j, v = _take(i + 1, pred)
        return (j, txt, (txt, v)) if j >= 0 else (-1, "", "")
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


def _scan_table_tail(toks, start, stop, exec_atoms=()):
    """把表尾解析成 typed atoms，再整体匹配 profile。

    `exec_atoms` 是 `_validate_executable_comments()` 产出的带原始字符 span、
    `left_idx/right_idx` 与 partition_shape 的条目。只有条目的左右 token 恰好等于
    两个**完整 atom**之间的边界，才允许合并进 atom 流（第十三轮 BLOCK-13-02）。
    合并进来的分区在指纹里标成 `source_only=True`：候选 AST 里不会有它们
    （sqlglot 根本看不见可执行注释），故不参与候选侧比较。

    返回 (方言目标 span, 辅助掩码 span, 表尾指纹)；不合规返回 (None, None, None)。
    """
    tgt_spans, mask_spans, atoms, fp = [], [], [], []
    seen_local = []
    pending = sorted(exec_atoms or (), key=lambda e: e["comment_start"])
    prev_atom_last = start - 1

    def _flush_exec_at_boundary(left_idx, right_idx):
        """只在完整 atom 边界插入；返回 False 表示注释落在 atom 内部。"""
        if not pending or pending[0]["right_idx"] > right_idx:
            return True
        e = pending[0]
        if e["left_idx"] != left_idx or e["right_idx"] != right_idx:
            return False
        pending.pop(0)
        atoms.append("PARTITION")
        fp.append(("exec_partition", e["partition_shape"]))
        return True

    i = start
    while i < stop:
        if not _flush_exec_at_boundary(prev_atom_last, i):
            return None, None, None                    # COMMENT 位于复合 atom 内部
        tt = toks[i].token_type
        if tt == TokenType.PARTITION_BY:
            j, msp, pshape = _consume_secondary_partition(toks, i, stop)
            if j < 0:
                return None, None, None
            mask_spans.extend(msp)
            atoms.append("PARTITION")
            fp.append(pshape)
            prev_atom_last = j - 1
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
            prev_atom_last = j - 1
            i = j
            continue
        if _is_bare_kw(toks[i], "BROADCAST"):
            tgt_spans.append((toks[i].start, toks[i].end))
            atoms.append("BROADCAST_KEYWORD")
            fp.append(("broadcast_keyword",))
            prev_atom_last = i
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
            prev_atom_last = k - 1
            i = k
            continue
        if ident in seen_local:
            return None, None, None                    # 同名本地选项不可重复
        seen_local.append(ident)
        atoms.append("LOCAL")
        fp.append(oshape)
        prev_atom_last = j - 1
        i = j
    if not _flush_exec_at_boundary(prev_atom_last, stop) or pending:
        return None, None, None                        # 尾部之外或 atom 内部仍有未归属注释
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
# sqlglot 的词法器不会把 `/*!50100 ... */` 的内容变成主 token；不同位置、不同版本下
# `token.comments` 的归属不能证明原文插入边界。Rev.O 因而只把 token 的原始字符 span
# 当作词法保护边界，在相邻 token 之间的原文 gap 中定位可执行注释，不读取 owner。
#
# 本版在规划入口显式处理：普通注释继续忽略；`!<版本号>` 开头的可执行注释
# **必须整段通过验证**，且本版只接受**一个完整的**二级分区 payload。
_EXEC_COMMENT_IN_GAP_RE = re.compile(
    r"/\*!\s*(?P<version>\d*)\s*(?P<payload>.*?)\*/", re.DOTALL)


def _collect_executable_comments(sql, toks):
    """在 sqlglot 已证明“无主 token”的 gap 内定位可执行注释原始 span。

    不相信 `token.comments` 的 owner 推断，也不在整条 SQL 上做替换。字符串、反引号
    标识符等都已被 sqlglot 划为 token，不会进入 gap；正则只负责从 token-free gap
    中取得 `/*!...*/` 的字符区间和 payload。
    """
    gaps = []
    if toks:
        gaps.append((-1, 0, 0, toks[0].start))
        for idx in range(len(toks) - 1):
            gaps.append((idx, idx + 1, toks[idx].end + 1, toks[idx + 1].start))
        gaps.append((len(toks) - 1, len(toks), toks[-1].end + 1, len(sql)))
    else:
        gaps.append((-1, 0, 0, len(sql)))
    out = []
    for left_idx, right_idx, gs, ge in gaps:
        if ge <= gs:
            continue
        for m in _EXEC_COMMENT_IN_GAP_RE.finditer(sql[gs:ge]):
            out.append({
                "comment_start": gs + m.start(),
                "comment_end": gs + m.end(),          # 半开区间
                "left_idx": left_idx,
                "right_idx": right_idx,
                "payload": (m.group("payload") or "").strip(),
            })
    return sorted(out, key=lambda e: e["comment_start"])


def _validate_executable_comments(sql, toks, close_idx, statement_end, dialect="mysql"):
    """验证 payload 与顶层域；完整 atom 边界由 `_scan_table_tail()` 最终裁决。"""
    entries = _collect_executable_comments(sql, toks)
    if not entries:
        return True, []
    if len(entries) > 1:
        return False, None                             # 多个可执行注释 → 失败关闭
    entry = entries[0]
    if (entry["comment_start"] <= toks[close_idx].end
            or entry["comment_end"] > statement_end):
        return False, None                             # 位置越界：建表头 / 定义列表内部
    try:
        ptoks = sqlglot.Dialect.get_or_raise(dialect).tokenizer_class().tokenize(
            entry["payload"])
    except Exception:
        return False, None
    if not ptoks or ptoks[0].token_type != TokenType.PARTITION_BY:
        return False, None
    j, _msp, pshape = _consume_secondary_partition(ptoks, 0, len(ptoks))
    if j != len(ptoks):
        return False, None                             # 未消费到结尾 → 失败关闭
    entry["partition_shape"] = pshape
    return True, [entry]


def _scan_definition_list(toks, open_idx, close_idx):
    """逐项消费顶层定义列表。

    返回 (定义指纹元组, 主目标 span, 辅助掩码 span)；不合规返回 (None, [], [])。
    """
    defs, uq_spans, mask_spans = [], [], []
    i = open_idx + 1
    while i < close_idx:
        if toks[i].token_type == TokenType.CONSTRAINT:
            # 用户冻结：本期只支持具名 PRIMARY；CONSTRAINT UNIQUE 不扩能力。
            # Rev.N“消费后顺带恢复”会让该唯一语义在 ParsedSQL 中消失并造成 R054 漏报，
            # Rev.O 改为具名失败关闭，绝不恢复一个下游看不懂的合法约束。
            k = i + 1
            if k < close_idx and toks[k].token_type in _IDENT_TOKENS:
                k += 1
            symbol = _ident_text(toks[i + 1]) if k > i + 1 else ""
            j, _usp, asp, shape = _consume_index_definition(toks, k, close_idx)
            if j < 0 or shape is None:
                return None, [], []
            if shape[1] not in ("PRIMARY", "UNIQUE"):
                return None, [], []                   # 其他 CONSTRAINT 形态仍不在支持域
            # PRIMARY COMMENT 是可恢复主目标；CONSTRAINT UNIQUE 只完整消费并登记
            # KFN-6，由全路径 source preflight 与候选门禁失败关闭，绝不恢复成无语义 AST。
            if shape[1] == "PRIMARY":
                uq_spans.extend(_usp)
            mask_spans.extend(asp)
            # 第十二轮 MAJOR-12-01：symbol 记入指纹（放末位，不改既有 off 偏移）
            defs.append(("constraint",) + shape + (symbol,))
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


def _definition_kfns(defs):
    """从 SourceShape 收集具名已知假阴性；返回稳定去重后的编号元组。"""
    out = []
    for d in defs or ():
        if not d:
            continue
        if d[0] == "constraint":
            if len(d) >= 3 and d[2] == "UNIQUE":
                out.append("KFN-6-CONSTRAINT-UNIQUE")
                continue
            # `CONSTRAINT PRIMARY KEY`（省略 symbol）是既有 KFN-4：规划器能完整
            # 识别，但三版候选均 ParseError。constraint shape 的最后一项就是 symbol。
            if len(d) >= 7 and d[2] == "PRIMARY" and not d[6]:
                out.append("KFN-4-CONSTRAINT-PRIMARY-NO-SYMBOL")
            continue
        if d[0] != "col":
            continue
        type_shape, cons = d[2], d[3]
        if len(type_shape) >= 5:
            out.extend(type_shape[4] or ())
        out.extend(v for k, v in cons if k == "KFN")
    return tuple(sorted(set(out)))


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
    boundary_kfns = ()
    if (toks and toks[-1].token_type == TokenType.SEMICOLON
            and sql[toks[-1].end + 1:].strip()):
        # 普通注释不会成为主 token；若它位于终止分号之后，当前候选解析器会失败。
        # 规划器仍须把这个既有官方形态具名登记，而不是碰巧在 candidate 阶段失败。
        boundary_kfns = ("KFN-4-TRAILING-COMMENT-AFTER-SEMICOLON",)
    statement_end = (toks[-1].start if toks and toks[-1].token_type == TokenType.SEMICOLON
                     else len(sql))
    toks = _strip_terminal_semicolon(toks)
    if toks is None:
        return None
    open_idx, close_idx, table_name, head = _tdsql_table_def_bounds(toks)
    if open_idx < 0:
        return None
    # 可执行注释必须在拿到定义列表边界之后验证——位置合法性依赖 close_idx
    ok, exec_atoms = _validate_executable_comments(
        sql, toks, close_idx, statement_end, dialect)
    if not ok:
        return None                                    # 可执行注释未通过验证 → 失败关闭
    defs, uq_spans, mask_a = _scan_definition_list(toks, open_idx, close_idx)
    if defs is None:
        return None
    tgt_spans, mask_b, tail_fp = _scan_table_tail(
        toks, close_idx + 1, len(toks), exec_atoms)
    if tgt_spans is None:
        return None
    primary = list(uq_spans) + list(tgt_spans)
    if not primary:
        return None                                    # 无主目标 → 不恢复
    tok_part = any(t.token_type == TokenType.PARTITION_BY for t in toks)
    kfns = tuple(sorted(set(_definition_kfns(defs) + boundary_kfns)))
    return {
        "table": table_name,
        "primary_spans": primary,
        "auxiliary_spans": list(mask_a) + list(mask_b),
        # ── SourceFingerprint = CreateShape（第十二轮 BLOCK-12-04）──
        #   head        顶层语义：(schema, table) 全限定名 + TEMPORARY + IF NOT EXISTS
        #   definitions 定义列表形状（列 / 索引 / 具名约束）
        #   tail        表尾形状：本地表选项 + 分布 atom + 二级分区细节
        # 三者都必须进入候选比较；Rev.M 只比了 definitions，于是候选把
        # `db1.t` 换成 `db2.t`、把 ENGINE 换成 MyISAM、把分区边界改掉，
        # 门禁一律返回 True。
        "fingerprint": {
            "head": head,
            "table": (table_name or "").strip("` ").lower(),
            "definitions": defs,
            "tail": tail_fp,
        },
        # 分区保真门禁只对**主 token 流里的**分区生效；
        # 可执行注释里的分区 sqlglot 不产生节点，其完整性已由
        # `_validate_executable_comments()` 独立证明，并已按源序并入
        # 表尾 atom 流参与计数与 profile 匹配（第十二轮 BLOCK-12-01）。
        "had_partition": tok_part,
        "exec_comment_partition": bool(exec_atoms),
        # 非空时表示“官方合法但本期不能保真”。parse() 仍能证明规划器具名接受，
        # `_validate_recovery_candidate()` 则强制失败关闭，避免普通 plan=False 与 KFN 混淆。
        "known_false_negatives": kfns,
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
    "COLUMN_FORMAT",      # 官方列属性，已作辅助掩码剥离（sqlglot 不认）
    "ENGINE_ATTRIBUTE",   # 同上
)
# 列 COMMENT **不在 ignored 集合**：指纹值仍为 None，表示只比较“有/无”，
# 不重复比较文本。文本保真由 raw_sql、_extract_column_comment() 与 R029 端到端断言负责。
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


# ── 表尾里**故意**从候选 AST 移除的 atom（source-only approved transform）──
#
# 方言声明被掩码是本方案的既定动作，可执行注释里的分区 sqlglot 根本看不见；
# 它们由 raw SQL 规则与 capability profile 负责，不能与普通 table tail 混为一谈
# （第十二轮 BLOCK-12-04）。分区定义里的 `[STORAGE] ENGINE` / `COMMENT`
# 选项也是既定掩码目标，同样不参与候选比较。
_SOURCE_ONLY_TAIL_TAGS = ("dist", "broadcast_keyword", "broadcast_sentinel",
                          "shardkey", "exec_partition")


def _tail_comparable(tail_fp):
    """把表尾指纹投影成"候选侧也应当具备"的部分。

    返回 `(本地选项排序元组, 分区形状 | None)`；无法投影返回 None。
    本地选项按排序比较——表选项之间无顺序语义，排序后比较更稳，
    而 O 第十二轮列出的每一种变异（ENGINE/CHARSET/COLLATE/COMMENT/删除全部）
    都会改变多重集合，一样会被抓到。
    """
    if not tail_fp or len(tail_fp) != 3:
        return None
    locals_, part = [], None
    for e in tail_fp[2]:
        tag = e[0] if isinstance(e, tuple) and e else e
        if tag in _SOURCE_ONLY_TAIL_TAGS:
            continue
        if tag == "part":
            if part is not None:
                return None                            # 不可能：计数已保证至多一个
            _t, method, eshape, defs = e
            # 分区选项（ENGINE/COMMENT）是掩码目标 → 只比分区名与 VALUES 边界
            part = (method, eshape, tuple((d[0], d[1]) for d in defs))
            continue
        locals_.append(e)
    return tuple(sorted(locals_)), part


def _ast_head_shape(node):
    """候选 AST 的顶层语义：((schema, table), TEMPORARY, IF NOT EXISTS)。"""
    schema = node.this
    if not isinstance(schema, exp.Schema):
        return None
    t = schema.this
    if t is None:
        return None
    props = node.args.get("properties")
    names = [type(p).__name__ for p in (props.expressions if props else [])]
    return (((getattr(t, "db", "") or "").strip("` ").lower(),
             (getattr(t, "name", "") or "").strip("` ").lower()),
            "TemporaryProperty" in names,
            bool(node.args.get("exists")))


# 候选属性里**不属于表尾**的项：它们在 head 面已单独比较，不能混进 tail 扫描。
_AST_NON_TAIL_PROPERTIES = ("TemporaryProperty",)


def _ast_tail_shape(node, dialect="mysql"):
    """候选 AST 的表尾形状。

    做法与类型规范化同一套路（第十一轮 BLOCK-11-04 的教训）：把候选属性**逐个
    回生成**后拼成一段表尾，再送进**同一个** `_scan_table_tail()`，
    而不是另写一套 property 类名映射。好处是 `CHARSET` / `CHARACTER SET`、
    引号风格、`=` 有无这些差异被同一个消费器自动归一，两侧不可能各自漂移。

    ⚠️ 不能直接用 `node.sql()` 的整句文本：sqlglot 一旦遇到它不认识的表选项
    （`shardkey=`、`STATS_PERSISTENT=` 等），回生成时会把**整组**属性包进
    `WITH ( … )`（实测），tail 扫描随即失败、把合法正例判成不守恒。
    逐属性渲染就没有这个容器。
    """
    props = node.args.get("properties")
    parts = []
    for p in (props.expressions if props else []):
        if type(p).__name__ in _AST_NON_TAIL_PROPERTIES:
            continue
        try:
            txt = p.sql(dialect=dialect)
        except Exception:
            return None
        if txt:
            parts.append(txt)
    stub = "CREATE TABLE `__t__` (`__c__` INT) " + " ".join(parts)
    try:
        toks = sqlglot.Dialect.get_or_raise(dialect).tokenizer_class().tokenize(stub)
    except Exception:
        return None
    open_idx, close_idx, _nm, _head = _tdsql_table_def_bounds(toks)
    if open_idx < 0:
        return None
    _tgt, _msk, fp = _scan_table_tail(toks, close_idx + 1, len(toks))
    if fp is None:
        return None
    return _tail_comparable(fp)


def _validate_recovery_candidate(node, plan):
    """候选 AST 结构守恒门禁：逐字段比较，不再是布尔检查。

    第十二轮 BLOCK-12-04：Rev.M 只比较了定义列表，顶层 CREATE 语义与整个表尾
    都没有进入比较，于是 `CREATE TEMPORARY`→`CREATE`、删 `IF NOT EXISTS`、
    `db1.t`→`db2.t`、`ENGINE=InnoDB`→`MyISAM`、`CHARSET` 改变、表 COMMENT 改写、
    删光全部表选项、分区方法/键/名/边界改变——13 种单点变异**全部返回 True**。
    本版比较 CreateShape 的三个面：head / definitions / tail。
    """
    if plan.get("known_false_negatives"):
        return False                                  # 具名 KFN：计划可达，最终必须失败关闭
    if not isinstance(node, exp.Create):
        return False
    if str(node.args.get("kind") or "").upper() != "TABLE":
        return False
    if not _same_table_name(node, plan["table"]):
        return False
    fpr = plan["fingerprint"]
    # ① head：全限定名 + TEMPORARY + IF NOT EXISTS（都有规则消费者）
    if _ast_head_shape(node) != fpr.get("head"):
        return False
    # ② tail：本地表选项与二级分区细节
    if _ast_tail_shape(node) != _tail_comparable(fpr.get("tail")):
        return False
    schema = node.this
    if not isinstance(schema, exp.Schema):
        return False
    items = list(schema.expressions or [])
    src_defs = fpr["definitions"]
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
            off = 1 if tag == "constraint" else 0
            if tag == "constraint":
                # 第十二轮 MAJOR-12-01：官方 `[CONSTRAINT [symbol]] PRIMARY KEY (...)`
                # 在候选里是 `exp.Constraint(this=symbol, expressions=[PrimaryKey])`。
                # Rev.M 把它直接丢给只认 PrimaryKey/Unique/Index 的形状提取器，
                # 必然返回 None，于是这条**官方合法**语句被系统性误杀。
                if not isinstance(it, exp.Constraint):
                    return False
                inner = list(it.args.get("expressions") or [])
                primaries = [x for x in inner if isinstance(x, exp.PrimaryKey)]
                comments = [x for x in inner if type(x).__name__ == "CommentColumnConstraint"]
                if len(primaries) != 1 or len(inner) != len(primaries) + len(comments):
                    return False
                if (getattr(it.this, "name", "") or "").strip("` ").lower() != src[6]:
                    return False                       # constraint symbol 守恒
                # PRIMARY COMMENT 是批准掩码目标：候选通常没有 COMMENT；若 sqlglot
                # 某版本仍把 COMMENT 放在 Constraint wrapper，只允许源侧确实存在时出现。
                if comments and "COMMENT" not in src[5]:
                    return False
                it = primaries[0]
            elif isinstance(it, exp.Constraint):
                return False                           # 源侧不是具名约束，候选却是
            got = _ast_index_shape(it)
            if got is None:
                return False
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
<!-- END CODE: RECOVERY-MODULE-AFTER -->

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

<!-- BEGIN CODE: COMMAND-RETRY-BEFORE -->
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
<!-- END CODE: COMMAND-RETRY-BEFORE -->

**改动后**（逐字照抄）：

<!-- BEGIN CODE: COMMAND-RETRY-AFTER -->
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
                _plan2 = _plan_recovery(sql_recover, self.dialect)
                if _plan2 is not None:
                    _all2 = _plan2["primary_spans"] + _plan2["auxiliary_spans"]
                    _t_sql = _blank_spans(sql_recover, _all2)
                    if (_t_sql is not None
                            and _spans_only_diff(sql_recover, _t_sql, _all2)):
                        try:
                            _retry_ast = sqlglot.parse_one(_t_sql, read=self.dialect)
                        except Exception:
                            _retry_ast = None
                        if _validate_recovery_candidate(_retry_ast, _plan2):
                            ast = _retry_ast
```
<!-- END CODE: COMMAND-RETRY-AFTER -->

> **必须同时改这里，不能只改 except 分支。** O 指出：只修新路径会留下
> "无 UNIQUE COMMENT 时仍静默损坏"的同源问题——而那正是**当前生产版本正在发生的事**。
> 实测：改造后，三个反例在**首次重试路径**上同样恢复正确（列名与注释逐字保持）。

### 3.2 改动点 2：`parse()` 的 `except` 分支——受限重试 + 四道门禁

**改动前**（当前第 144-155 行，逐字现状）：

<!-- BEGIN CODE: EXCEPT-RETRY-BEFORE -->
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
<!-- END CODE: EXCEPT-RETRY-BEFORE -->

**改动后**（逐字照抄）：

<!-- BEGIN CODE: EXCEPT-RETRY-AFTER -->
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
            _plan = _plan_recovery(sql_recover, self.dialect)
            if _plan is not None:
                _all_spans = _plan["primary_spans"] + _plan["auxiliary_spans"]
                _final_sql = _blank_spans(sql_recover, _all_spans)
                if (_final_sql is not None
                        and _spans_only_diff(sql_recover, _final_sql, _all_spans)):
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
<!-- END CODE: EXCEPT-RETRY-AFTER -->

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

<!-- BEGIN CODE: INDEX-TYPE-BEFORE -->
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
<!-- END CODE: INDEX-TYPE-BEFORE -->

**改动后**（逐字照抄，已采纳 O 的 MAJOR-1 白名单映射）：

<!-- BEGIN CODE: INDEX-TYPE-AFTER -->
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
<!-- END CODE: INDEX-TYPE-AFTER -->

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

### 3.3b 改动点 3b：恢复链必须拿到**未删分号**的原串（第十二轮 BLOCK-12-02）

`_strip_terminal_semicolon()` 声明"至多一个终止分号且只能位于 EOF 前"，
逻辑本身正确；但 `parse()` 入口先做了 `sql.strip().rstrip(";")`，
两个恢复调用点拿到的都是**已被抹平**的串，于是该门槛在真实调用链上**不可达**：

| 原始结尾 | `_plan_recovery(原串)` | Rev.M 集成后实际传入 | Rev.M 最终 |
|---|---|---|---|
| 无分号 / `;` | ACCEPT | ACCEPT | `Create` ✅ |
| `;;` / `;;;` / `; ;` | **REJECT** | ACCEPT | **`Create`** ❌ |

**改动前**（`parse()` 开头，第 2 行）：

<!-- BEGIN CODE: SEMICOLON-BEFORE -->
```python
        sql_clean = sql.strip().rstrip(";")
```
<!-- END CODE: SEMICOLON-BEFORE -->

**改动后**：

<!-- BEGIN CODE: SEMICOLON-AFTER -->
```python
        sql_clean = sql.strip().rstrip(";")
        # 第十二轮 BLOCK-12-02：恢复链必须拿到**未被 rstrip(";") 处理过**的同一原串。
        # Rev.M 把 `sql_clean` 传给 `_plan_recovery()`，于是 `_strip_terminal_semicolon()`
        # 声明的"至多一个终止分号"在真实调用链上不可达——`;;`、`;;;`、`; ;` 都会
        # 先被 rstrip 抹平，再被规划器当成合法单语句接受并恢复成 Create。
        # 全部 span 都相对 `sql_recover` 计算，与 `_blank_spans()`/`_spans_only_diff()`
        # 共用同一个字符串，不存在"先改长度再套旧偏移"的问题。
        sql_recover = sql.strip()
```
<!-- END CODE: SEMICOLON-AFTER -->

`sql_clean` 保持原样供既有正则回退与 `sqlglot.parse_one()` 使用（那条路径的行为一字不改）；
恢复链改用 `sql_recover`。三处替换：`_plan_recovery()` / `_blank_spans()` / `_spans_only_diff()`
各两个调用点，**全部 span 都相对同一个 `sql_recover` 计算**，
不存在"先改长度再套旧偏移"。掩码后的串仍带那个合法的终止分号，
实测 sqlglot 对 `CREATE TABLE … ;` 正常返回 `exp.Create`（三版一致）。

### 3.3c 改动点 3c：完整 UNIQUE 语义走独立通道（BLOCK-13-01 / BLOCK-14-01）

Rev.O 把列级和表级 UNIQUE 一并写入 `parsed.indexes/index_definitions`，语义虽然完整，却唤醒了
R077/R061 等依赖 legacy 输出域的历史分支。A 的消融实验与 Rev.P 独立控制流复核一致：
5 个冻结测试失败和 7 条规则漂移都由表级 UNIQUE 进入 legacy 列表触发；回退表级提取又会让
“列级 + 表级”混合场景在 R054 静默漏掉表级 UNIQUE。因此不能靠恢复 ADJ-5 缺陷互相抵消。

Rev.P 采用**独立、显式完整性标记的 UNIQUE 语义通道**：

- `parsed.indexes/index_definitions`：严格维持 v1.6.2.1 输出域，R077/R061 等既有消费者不变；
- `parsed.unique_constraints`：列级/表级 UNIQUE 的完整、逐索引结构，只由 R054 专属助手消费；
- `parsed.unique_constraints_complete`：只有完整 `Create` schema 已遍历且每个支持域 UNIQUE 均成功
  提取时才为 `True`。`True` 时禁止 raw 回退；`False` 时保留 Command/异常路径的既有 raw 回退。

#### 3.3c.1 `ParsedSQL` 新字段

<!-- BEGIN CODE: PARSED-UNIQUE-FIELDS-BEFORE -->
```python
    indexes: list[dict] = field(default_factory=list)
    table_options: dict = field(default_factory=dict)
```
<!-- END CODE: PARSED-UNIQUE-FIELDS-BEFORE -->

<!-- BEGIN CODE: PARSED-UNIQUE-FIELDS-AFTER -->
```python
    indexes: list[dict] = field(default_factory=list)
    # v1.6.2.2 / Rev.P：完整 UNIQUE 语义的隔离通道。不得无评审地改让
    # R077/R061 等 legacy 消费者读取它；本期唯一消费者是 R054 助手。
    unique_constraints: list[dict] = field(default_factory=list)
    unique_constraints_complete: bool = False
    known_fidelity_failures: tuple[str, ...] = field(default_factory=tuple)
    table_options: dict = field(default_factory=dict)
```
<!-- END CODE: PARSED-UNIQUE-FIELDS-AFTER -->

`unique_constraints_complete=False` 不能解释为“没有 UNIQUE”，只能解释为“结构化真源不完整”；
消费者必须据此选择 raw 回退。`True + []` 才表示已完整证明该表没有支持域 UNIQUE。

#### 3.3c.2 表级 UNIQUE 提取

**`_parse_unique_constraint()` 改动前（基线精确块）**：

<!-- BEGIN CODE: TABLE-UNIQUE-BEFORE -->
```python
    def _parse_unique_constraint(self, col_def) -> dict:
        """解析 UniqueColumnConstraint (表级 UNIQUE KEY/INDEX)"""
        idx_name = ""
        idx_cols = []
        # UniqueColumnConstraint 的 this 可能是 IndexColumnConstraint 或直接的列列表
        this_node = col_def.args.get("this")
        if this_node:
            if type(this_node).__name__ == "IndexColumnConstraint":
                # UNIQUE KEY uk_name (col1, col2)
                name_node = this_node.args.get("this")
                if name_node:
                    idx_name = name_node.sql(dialect=self.dialect) if hasattr(name_node, 'sql') else str(name_node)
                for ordered_expr in this_node.expressions:
                    col_node = ordered_expr.args.get("this") if hasattr(ordered_expr, 'args') else None
                    if col_node:
                        col_name = col_node.sql(dialect=self.dialect).strip('`"')
                        if col_name:
                            idx_cols.append(col_name)
            else:
                # 直接的列引用
                name_str = this_node.sql(dialect=self.dialect) if hasattr(this_node, 'sql') else str(this_node)
                idx_name = name_str
        # 从 expressions 中提取列名
        for expr in col_def.expressions:
            if hasattr(expr, 'args'):
                col_node = expr.args.get("this")
                if col_node:
                    col_name = col_node.sql(dialect=self.dialect).strip('`"')
                    if col_name:
                        idx_cols.append(col_name)
        if idx_cols:
            return {"name": idx_name or "UNIQUE", "columns": idx_cols, "type": "UNIQUE"}
        return {}
```
<!-- END CODE: TABLE-UNIQUE-BEFORE -->

**改动后（只接受发布 pin 已锁定的结构，未知 AST 失败关闭）**：

<!-- BEGIN CODE: TABLE-UNIQUE-AFTER -->
```python
    def _parse_unique_constraint(self, unique_def) -> dict:
        """结构化提取表级 UNIQUE KEY/INDEX；未知 AST 形状失败关闭。"""
        schema = unique_def.args.get("this")
        if not isinstance(schema, exp.Schema):
            return {}
        name_node = schema.args.get("this")
        idx_name = (getattr(name_node, "name", "") or "").strip('`" ')
        idx_cols = []
        for part in (schema.expressions or []):
            if isinstance(part, exp.Ordered):
                part = part.this
            if isinstance(part, exp.Identifier):
                col_name = part.name
            elif isinstance(part, exp.Anonymous):
                # TDSQL/MySQL 前缀索引 `col(n)` 在 sqlglot 中是 Anonymous；
                # 直接解析成功路径也会调用本函数，故这里不能只信规划器：必须再次
                # 证明恰好一个正整数字面量，避免把 `lower(col)` 函数索引当成列 lower。
                base = part.args.get("this")
                pargs = list(part.expressions or [])
                if (len(pargs) != 1 or not isinstance(pargs[0], exp.Literal)
                        or pargs[0].is_string or not str(pargs[0].this).isdigit()
                        or int(pargs[0].this) <= 0):
                    return {}
                col_name = base if isinstance(base, str) else getattr(base, "name", "")
            else:
                return {}                             # 函数/表达式/未知形状不得猜测
            col_name = (col_name or "").strip('`" ')
            if not col_name:
                return {}
            idx_cols.append(col_name)
        if not idx_cols:
            return {}
        return {
            "name": idx_name or "UNIQUE",
            "columns": idx_cols,
            "type": "UNIQUE",
            "origin": "TABLE_UNIQUE",
        }
```
<!-- END CODE: TABLE-UNIQUE-AFTER -->

#### 3.3c.3 列级 UNIQUE 提取

<!-- BEGIN CODE: COLUMN-UNIQUE-METHOD-BEFORE -->
```python
    def _extract_column_comment(self, col_def: exp.ColumnDef) -> str:
```
<!-- END CODE: COLUMN-UNIQUE-METHOD-BEFORE -->

<!-- BEGIN CODE: COLUMN-UNIQUE-METHOD-AFTER -->
```python
    def _parse_column_unique_constraint(self, col_def: exp.ColumnDef):
        """把 `col TYPE UNIQUE [KEY]` 转成下游统一的 UNIQUE 索引语义。

        只遍历 ColumnDef 的**直接 constraints**，不使用 find_all()，避免把嵌套节点
        或未来 AST 结构误算成第二个唯一索引。MySQL/TDSQL 未显式命名的单列 UNIQUE
        以列名作为隐式索引名；R054 助手只读取 name/columns/type，origin 仅供诊断。
        """
        found = 0
        malformed = False
        for constraint in (col_def.args.get("constraints") or []):
            kind = constraint.args.get("kind")
            if isinstance(kind, exp.UniqueColumnConstraint):
                found += 1
                # sqlglot 29.0.0 会把第二个 UNIQUE 折叠到首个节点的 this，
                # 30.x 则可能形成第二个约束；两种 AST 都必须失败关闭。
                malformed = malformed or kind.args.get("this") is not None
        if found == 0:
            return None                               # 非唯一列，不影响完整性
        if found != 1 or malformed:
            return {}                                 # 看到了 UNIQUE 但不能形成唯一语义
        name = (col_def.name or "").strip('`" ')
        if not name:
            return {}
        return {
            "name": name,
            "columns": [name],
            "type": "UNIQUE",
            "origin": "COLUMN_UNIQUE",
        }

    def _extract_column_comment(self, col_def: exp.ColumnDef) -> str:
```
<!-- END CODE: COLUMN-UNIQUE-METHOD-AFTER -->

#### 3.3c.4 `_parse_create()` 接线与完整性

定义列表循环先建立本次解析的完整性哨兵：

<!-- BEGIN CODE: UNIQUE-INIT-BEFORE -->
```python
        # 解析列定义和索引定义
        if isinstance(schema, exp.Schema):
```
<!-- END CODE: UNIQUE-INIT-BEFORE -->

<!-- BEGIN CODE: UNIQUE-INIT-AFTER -->
```python
        # 解析列定义和索引定义
        _unique_semantics_failed = False
        if isinstance(schema, exp.Schema):
```
<!-- END CODE: UNIQUE-INIT-AFTER -->

列定义接线改为 BEFORE/AFTER 精确替换，不再依靠一句文字锚点：

<!-- BEGIN CODE: COLUMN-UNIQUE-WIRE-BEFORE -->
```python
                    # 提取列注释
                    comment = self._extract_column_comment(col_def)
                    if comment:
                        parsed.column_comments[col_info["name"]] = comment
                elif isinstance(col_def, exp.PrimaryKey):
```
<!-- END CODE: COLUMN-UNIQUE-WIRE-BEFORE -->

<!-- BEGIN CODE: COLUMN-UNIQUE-WIRE-AFTER -->
```python
                    # 提取列注释
                    comment = self._extract_column_comment(col_def)
                    if comment:
                        parsed.column_comments[col_info["name"]] = comment
                    # Rev.P：列级 UNIQUE 进入隔离语义通道，绝不写 legacy indexes。
                    col_unique = self._parse_column_unique_constraint(col_def)
                    if col_unique is None:
                        pass                          # 本列无 UNIQUE
                    elif col_unique:
                        parsed.unique_constraints.append(col_unique)
                    else:
                        _unique_semantics_failed = True
                elif isinstance(col_def, exp.PrimaryKey):
```
<!-- END CODE: COLUMN-UNIQUE-WIRE-AFTER -->

表级分支同样不得写 legacy 列表：

<!-- BEGIN CODE: TABLE-UNIQUE-WIRE-BEFORE -->
```python
                elif type(col_def).__name__ == "UniqueColumnConstraint":
                    # sqlglot UniqueColumnConstraint: 表级 UNIQUE KEY/INDEX
                    idx_info = self._parse_unique_constraint(col_def)
                    if idx_info:
                        parsed.indexes.append(idx_info)
                        parsed.index_definitions.append(idx_info)
```
<!-- END CODE: TABLE-UNIQUE-WIRE-BEFORE -->

<!-- BEGIN CODE: TABLE-UNIQUE-WIRE-AFTER -->
```python
                elif type(col_def).__name__ == "UniqueColumnConstraint":
                    # Rev.P：表级 UNIQUE 进入隔离语义通道；提取失败即保持 incomplete。
                    idx_info = self._parse_unique_constraint(col_def)
                    if idx_info:
                        parsed.unique_constraints.append(idx_info)
                    else:
                        _unique_semantics_failed = True
```
<!-- END CODE: TABLE-UNIQUE-WIRE-AFTER -->

在 schema 循环前初始化 `_unique_semantics_failed = False`；循环结束后，仅当没有提取失败且
source preflight 没有 KFN 时置完整：

<!-- BEGIN CODE: UNIQUE-COMPLETE-BEFORE -->
```python
        # 检查约束中的主键和外键
        if isinstance(schema, exp.Schema):
```
<!-- END CODE: UNIQUE-COMPLETE-BEFORE -->

<!-- BEGIN CODE: UNIQUE-COMPLETE-AFTER -->
```python
        if isinstance(schema, exp.Schema):
            if _unique_semantics_failed:
                parsed.parse_error = (
                    parsed.parse_error or "UNIQUE_SEMANTICS_INCOMPLETE"
                )
            parsed.unique_constraints_complete = (
                not _unique_semantics_failed and not parsed.known_fidelity_failures
            )

        # 检查约束中的主键和外键
        if isinstance(schema, exp.Schema):
```
<!-- END CODE: UNIQUE-COMPLETE-AFTER -->

`_unique_semantics_failed` 的初始化、两条接线和 complete 赋值必须作为一个原子改动；漏掉任一处，
design 重建器都应因 marker 未消费或目标断言失败而非零退出。

#### 3.3c.5 R054 专属消费者（允许触碰 `distributed.py` 的唯一行为点）

<!-- BEGIN CODE: R054-UNIQUE-ITER-BEFORE -->
```python
def _iter_unique_indexes(parsed: ParsedSQL, raw_sql: str):
    """逐个产出唯一索引 (名称, 列名集合)。

    J-3 要求"每一个唯一索引都包含分片键"，因此**不得展平成列并集**——
    并集只能回答"是否在任意唯一索引中"，表达不了 J-3。
    """
    seen = False
    for idx in list(parsed.indexes) + list(parsed.index_definitions):
        if (idx.get("type") or "").upper() == "UNIQUE":
            seen = True
            yield (idx.get("name") or "UNIQUE索引",
                   {c.lower() for c in idx.get("columns", [])})
    if seen:
        return
    # 回退：解析器未产出 UNIQUE 条目时走正则（索引名支持反引号——
    # SHOW CREATE TABLE 输出的索引名恒带反引号）
    for m in _UNIQUE_IDX_RE.finditer(_strip_sql_noise(raw_sql)):
        yield (m.group('qname') or m.group('bname') or "UNIQUE索引",
               {c.strip('`"\' ').lower() for c in m.group(3).split(",")})
```
<!-- END CODE: R054-UNIQUE-ITER-BEFORE -->

<!-- BEGIN CODE: R054-UNIQUE-ITER-AFTER -->
```python
def _iter_unique_indexes(parsed: ParsedSQL, raw_sql: str):
    """R054 专属：逐个产出完整唯一约束；不得被 R077 复用。"""
    if getattr(parsed, "unique_constraints_complete", False):
        for idx in parsed.unique_constraints:
            yield (idx.get("name") or "UNIQUE索引",
                   {c.lower() for c in idx.get("columns", [])})
        return
    # Command/异常等结构不完整路径保留既有可信 raw 回退；完整结构下绝不混用两真源。
    for m in _UNIQUE_IDX_RE.finditer(_strip_sql_noise(raw_sql)):
        yield (m.group('qname') or m.group('bname') or "UNIQUE索引",
               {c.strip('`"\' ').lower() for c in m.group(3).split(",")})
```
<!-- END CODE: R054-UNIQUE-ITER-AFTER -->

安全边界：

- 表级 `UNIQUE KEY/INDEX` 继续由 `_parse_unique_constraint()` 处理，不在列助手重复生成；
- 表级提取只接受 Identifier 或已证明为 `col(正整数)` 的 Anonymous；直接解析路径出现
  `lower(col)` 等函数/表达式索引时返回空，不把函数名伪造成列名；
- 列助手必须三态区分“无 UNIQUE”(`None`)、“成功”(dict) 与“看见但无法完整表达”(`{}`)；
  后两者不能都靠 truthy 判断。任何已看见却无法完整表达的列级/表级 UNIQUE 都设置
  `UNIQUE_SEMANTICS_INCOMPLETE`，最终由 Checker 形成 E999，不能只把 complete 置 False 后继续审核；
- `CONSTRAINT symbol UNIQUE` 与 SERIAL 由 §3.3d 的全路径 preflight 阻断，不能把 incomplete 误标为 complete；
- 新通道的本期消费者白名单只有 `_iter_unique_indexes()`；任何新增消费者都必须单独评审；
- 预期索引名按数据库隐式命名规则为列名，但 R061 不读取本通道，故不会新增命名误报；
- A 报告的 5 个冻结用例、三个专项文件 71 项和 201+14 表规则漂移是原子准出门，不能用更新旧期望掩盖。

### 3.3d 改动点 3d：KFN 必须覆盖原生 Create / Command / except 三条路径（BLOCK-14-02）

Rev.O 只在 RecoveryPlan 内拒绝 KFN，原生 `Create` 根本不会调用规划器。Rev.P 将 definition scanner
提升为 source preflight 的共同真源：在调用 sqlglot 之前先词法化完整 `CREATE TABLE`，提取 KFN；
普通注释、字符串与反引号标识符已由 tokenizer 隔离，不得用 raw 正则搜索关键字。

`_scan_definition_list()` 对具名 UNIQUE 不再返回不可区分的 `None`，而是完整消费并形成 constraint
shape；`_definition_kfns()` 为其登记 `KFN-6-CONSTRAINT-UNIQUE`。它仍不是恢复目标：候选门禁拒绝，
source preflight 也会让原生成功路径最终带 E999。

<!-- BEGIN CODE: SOURCE-PREFLIGHT-BEFORE -->
```python
def _strip_terminal_semicolon(toks):
```
<!-- END CODE: SOURCE-PREFLIGHT-BEFORE -->

<!-- BEGIN CODE: SOURCE-PREFLIGHT-AFTER -->
```python
def _preflight_known_fidelity_failures(sql: str, dialect: str = "mysql"):
    """从原始 token 结构提取全路径 KFN；不是合法性黑名单正则。"""
    try:
        toks = sqlglot.Dialect.get_or_raise(dialect).tokenizer_class().tokenize(sql)
    except Exception:
        return ()
    toks = _strip_terminal_semicolon(toks)
    if toks is None:
        return ()
    open_idx, close_idx, _table_name, _head = _tdsql_table_def_bounds(toks)
    if open_idx < 0:
        return ()
    defs, _primary, _auxiliary = _scan_definition_list(toks, open_idx, close_idx)
    if defs is None:
        return ()
    return _definition_kfns(defs)


def _strip_terminal_semicolon(toks):
```
<!-- END CODE: SOURCE-PREFLIGHT-AFTER -->

`parse()` 在 `_regex_pre_parse()` 之后记录结果，但**不提前丢掉原生可提取结构**；方法末尾统一把
KFN 写成稳定 `parse_error`，使 Checker 必然生成 E999。原生 Create 可以保留 AST/字段用于诊断，
但 E999 是阻断结论；测试不得再把“AST 必须不是 Create”误当失败关闭的唯一定义。

<!-- BEGIN CODE: PARSE-PREFLIGHT-BEFORE -->
```python
        # 尝试解析SQL
        try:
```
<!-- END CODE: PARSE-PREFLIGHT-BEFORE -->

<!-- BEGIN CODE: PARSE-PREFLIGHT-AFTER -->
```python
        # Rev.P / BLOCK-14-02：KFN 必须覆盖原生 Create、Command 与 except 三条路径。
        parsed.known_fidelity_failures = _preflight_known_fidelity_failures(
            sql_recover, self.dialect)

        # 尝试解析SQL
        try:
```
<!-- END CODE: PARSE-PREFLIGHT-AFTER -->

方法最终 `return parsed` 前增加：

<!-- BEGIN CODE: PARSE-KFN-FINALIZE-BEFORE -->
```python
        return parsed
```
<!-- END CODE: PARSE-KFN-FINALIZE-BEFORE -->

<!-- BEGIN CODE: PARSE-KFN-FINALIZE-AFTER -->
```python
        if parsed.known_fidelity_failures:
            parsed.parse_error = "KNOWN_FIDELITY_GAP[%s]" % ",".join(
                parsed.known_fidelity_failures)
        return parsed
```
<!-- END CODE: PARSE-KFN-FINALIZE-AFTER -->

上述 BEFORE 在文件中有多个 `return parsed`，重建动作必须限定为 `parse()` 内最后一个、
`# ── 正则预解析` 之前的那一个；结构化 locator 找不到唯一位置即非零退出。

RecoveryPlan 的候选门禁仍最先检查 KFN：

<!-- BEGIN CODE: KFN-GATE-ASSERT-CONTAINED -->
```python
    if plan.get("known_false_negatives"):
        return False
```
<!-- END CODE: KFN-GATE-ASSERT-CONTAINED -->

测试契约升级为四连断言：preflight KFN 编号、RecoveryPlan（有恢复目标时）的 KFN 编号、
`parsed.known_fidelity_failures`、最终规则集合含 E999。每个 `CONSTRAINT … UNIQUE` 与 SERIAL
必须覆盖 `{原生 Create, 方言 Command, UNIQUE COMMENT/ParseError}` 三路径；同名文本放在列 COMMENT、
表 COMMENT、DEFAULT 字符串或反引号标识符时不得误命中。

### 3.3e 改动点 3e：发布依赖精确锁定（BLOCK-13-04）

`requirements.txt`：

<!-- BEGIN CODE: REQUIREMENTS-SQLGLOT-BEFORE -->
```text
sqlglot>=26.0.0
```
<!-- END CODE: REQUIREMENTS-SQLGLOT-BEFORE -->

替换为：

<!-- BEGIN CODE: REQUIREMENTS-SQLGLOT-AFTER -->
```text
sqlglot==30.14.0
```
<!-- END CODE: REQUIREMENTS-SQLGLOT-AFTER -->

`pyproject.toml`：

<!-- BEGIN CODE: PYPROJECT-SQLGLOT-BEFORE -->
```text
    "sqlglot>=26.0",
```
<!-- END CODE: PYPROJECT-SQLGLOT-BEFORE -->

替换为：

<!-- BEGIN CODE: PYPROJECT-SQLGLOT-AFTER -->
```text
    "sqlglot==30.14.0",
```
<!-- END CODE: PYPROJECT-SQLGLOT-AFTER -->

两处 before 在基线各出现恰好一次，替换后 before 必须为 0、after 必须为 1。release runner
还要断言运行时 `sqlglot.__version__ == "30.14.0"`；只改声明、不验证实际 wheel 不算闭环。

### 3.4 改动汇总

| 序号 | 位置 | 改动 |
|---|---|---|
| 0 | 文件头 import 区 | `from sqlglot.tokens import TokenType` |
| **0b** | 原 `_TDSQL_DIALECT_RE` 处 | **删除**该全局正则及其注释 |
| **0c** | 同上位置 | 新增全部模块级恢复链代码（结构化类型表 `_TYPE_RULES`、typed atoms + capability profile、可执行注释验证、结构化指纹与守恒门禁）。函数逐个清单见下方自动生成表 |
| **2b** | `parse()` 首次 `Command` 重试 | 改用 token 剥离器 + span 校验（v1.6.2.0 代码，NG-4 已撤销） |
| 2 | `parse()` 的 `except` 分支 | 两阶段受限重试 + **联合 span 门禁** |
| 3 | `_parse_index_constraint()` | 类型判据改读 `kind` 白名单映射 |
| **3c** | `ParsedSQL` + `_parse_create()` + UNIQUE helpers | 列级与表级 UNIQUE 进入隔离 `unique_constraints` 通道；legacy `indexes/index_definitions` 输出域不变；完整性显式标记 |
| **3c-R054** | `distributed.py::_iter_unique_indexes()` | 本期唯一规则层行为改动：R054 优先消费完整隔离通道，不完整路径才走既有 raw 回退；R077 类不变 |
| **3d** | source preflight / RecoveryPlan / candidate gate / parse finalize | KFN 编号覆盖原生 Create、Command、except 三路径，最终必须有 E999 |
| **3e** | `requirements.txt` / `pyproject.toml` | 两处均精确锁定 `sqlglot==30.14.0`，runner 再断言实际运行版本 |

**产品逻辑代码改 `parser_legacy.py`，并只在 `distributed.py` 修改 R054 专属模块级助手
`_iter_unique_indexes()`；R077 类、其 `_UNIQUE_RE`、OR 判定及 legacy 索引列表全部不动。
另修改两处既有依赖声明以固定 sqlglot 版本。不新增依赖种类（`TokenType` 来自已在用的
sqlglot）。fixture 已在 Rev.C 修正。**

> **规模数字与函数清单一律由 `docs/evidence/v1.6.2.2/codestat.py` 从固定基线与最终补丁生成，不得人工维护**
> （第十一轮 MINOR-11-02）。复现命令：
>
> ```bash
> python docs/evidence/v1.6.2.2/codestat.py <基线 parser_legacy.py> <目标 parser_legacy.py>
> ```

<!-- BEGIN AUTOGENERATED CODESTAT: docs/evidence/v1.6.2.2/codestat.py -->
<!-- 本节由 docs/evidence/v1.6.2.2/codestat.py 生成，请勿手改 -->

**`backend/engine/parser/parser_legacy.py` 规模（自动生成）**

| 项 | 基线 | 目标 | 变化 |
|---|---:|---:|---:|
| 文件行数 | 849 | 2884 | +2035 |
| 模块级函数/类 | 2 | 51 | +49 |
| 模块级常量 | 1 | 34 | +33 |
| diff 行 | —— | —— | +2109 / -74 |

**新增函数（49 个）**

| 函数 | 起始行 | 行数 |
|---|---:|---:|
| `_spans_only_diff` | 43 | 8 |
| `_is_bare_kw` | 66 | 8 |
| `_ident_text` | 76 | 3 |
| `_tdsql_table_def_bounds` | 81 | 58 |
| `_P` | 184 | 2 |
| `_P_VALUES` | 188 | 2 |
| `_int_val` | 334 | 9 |
| `_in_range` | 345 | 3 |
| `_try_type_production` | 350 | 46 |
| `_consume_data_type` | 398 | 63 |
| `_canonical_type_from_sql` | 463 | 12 |
| `_canonical_number` | 492 | 14 |
| `_consume_default_value` | 508 | 51 |
| `_consume_column_constraints` | 561 | 88 |
| `_consume_column_definition` | 651 | 17 |
| `_index_lead` | 675 | 25 |
| `_consume_index_definition` | 702 | 54 |
| `_consume_index_key_parts` | 758 | 34 |
| `_consume_ident` | 794 | 6 |
| `_consume_ident_list` | 802 | 16 |
| `_consume_partition_expr` | 829 | 24 |
| `_unquote_str` | 855 | 11 |
| `_consume_value_list` | 868 | 33 |
| `_consume_partition_values` | 903 | 23 |
| `_consume_partition_options` | 928 | 41 |
| `_consume_partition_defs` | 971 | 30 |
| `_consume_secondary_partition` | 1003 | 16 |
| `_charset_kw_end` | 1043 | 19 |
| `_consume_table_option` | 1064 | 63 |
| `_match_tail_profile` | 1181 | 22 |
| `_consume_shardkey_value` | 1205 | 32 |
| `_scan_table_tail` | 1239 | 113 |
| `_collect_executable_comments` | 1366 | 28 |
| `_validate_executable_comments` | 1396 | 23 |
| `_scan_definition_list` | 1421 | 49 |
| `_definition_kfns` | 1472 | 22 |
| `_preflight_known_fidelity_failures` | 1496 | 16 |
| `_strip_terminal_semicolon` | 1514 | 9 |
| `_plan_recovery` | 1525 | 64 |
| `_same_table_name` | 1591 | 12 |
| `_blank_spans` | 1605 | 12 |
| `_canonical_default_from_sql` | 1640 | 14 |
| `_ast_column_shape` | 1656 | 29 |
| `_ast_index_using` | 1687 | 25 |
| `_ast_index_shape` | 1714 | 34 |
| `_tail_comparable` | 1760 | 24 |
| `_ast_head_shape` | 1786 | 14 |
| `_ast_tail_shape` | 1806 | 36 |
| `_validate_recovery_candidate` | 1844 | 92 |

**删除函数（0 个）**：无

**行数发生变化的既有函数（2 个）**

| 函数 | 基线行数 | 目标行数 |
|---|---:|---:|
| `class ParsedSQL` | 72 | 77 |
| `class SQLParser` | 744 | 867 |

**唯一性检查**

| 检查 | 结果 |
|---|---|
| 模块级函数重复定义 | ✅ 无 |
| 模块级常量重复定义 | ✅ 无 |
| 语法可解析 | ✅ |
<!-- END AUTOGENERATED CODESTAT -->

## 4. 明确的非目标（NG，施工红线）

| 编号 | 非目标 | 说明 |
|---|---|---|
| **NG-0** | **不再使用任何跨语义边界的正则做 SQL 改写** | Rev.A 的 `_UNIQUE_IDX_COMMENT_RE` 整体删除，不得以「再补几个分支」的方式保留 |
| ~~NG-1~~ | ~~不改任何规则文件~~ | 🚫 **Rev.P 收窄。** `ddl.py` / `index.py` / `dml.py` / `oracle_compat.py` 零改动；`distributed.py` 只允许替换 R054 专属 `_iter_unique_indexes()` 的供数入口。R077 类及其 `_UNIQUE_RE`、OR 判定必须逐字保持基线 |
| ~~NG-2~~ | ~~不动 `distributed.py`~~ | 🚫 **Rev.P 撤销。** Rev.O 把 UNIQUE 写入 legacy 索引域会激活 R077/R061 次生变化。Rev.P 以隔离 `unique_constraints` 通道承载新语义，并由 `_iter_unique_indexes()` 独占消费；这不是修改 R054 判据，而是隔离供数目的地 |
| ~~NG-3~~ | ~~不动 `_parse_unique_constraint()`~~ | 🚫 **Rev.O 撤销。** 它虽硬编码了正确 kind，却没有从发布 pin 的 `exp.Schema.expressions` 提取列，真实表级 UNIQUE 返回空字典；列级 UNIQUE 供数后还会关闭 raw 回退并放大成表级 UNIQUE 漏审。§3.3c 必须结构化修复 |
| ~~NG-4~~ | ~~不动 v1.6.2.0 的 TDSQL 方言重试~~ | 🚫 **本版撤销**。O 第三轮证明该正则会静默破坏 AST，且我正把更多语句引流进去；继续绕开它等于把已知损坏留在生产。Rev.D **删除该正则**并把两条恢复入口统一到 token 级剥离器 |
| **NG-5** | **不动 v1.6.2.1 的 R061 去引号** | `index.py` 一字不改 |
| **NG-6** | **不把 SPATIAL 单独成型** | 维持映射为 NORMAL。这是本次热修「输出域不变」的**兼容性取舍**，**不是**「空间索引语义上等同普通索引」的结论；后续如新增空间索引规则，另行立项扩展模型与消费者（O 复审同意） |
| **NG-7** | **不新增字段级字符集/排序规则检查** | 用户已决策：R005 维持只判表级，字段级字符集本次不纳入 |
| **NG-8** | **不在 `except` 补调 `_regex_fallback_create_table_props()`** | 见 §2.2 方案 B，登记 ADJ-10 |
| **NG-9** | **不修 E999 文案** | 现文案"可能是拉取截断/语法错误"对合法 MySQL 有误导，但属独立体验问题，登记 ADJ-12 |
| **NG-10** | **本期不支持 `CONSTRAINT x UNIQUE (col)` 形态** | 用户冻结决策保持。Rev.N“逐 token 消费但允许整句恢复”会造成 R054 漏审；Rev.O 在 `_scan_definition_list()` 识别到该 kind 后**具名失败关闭**。将来若扩支持，必须同时补 ParsedSQL 唯一语义、R054 双向断言并删除本条，不能只放开规划器 |

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

| **KFN-4** | `INT/BIGINT SIGNED`、`VARCHAR(n)/TEXT BINARY`、`NATIONAL CHAR/VARCHAR(n)`、无名 `CONSTRAINT PRIMARY KEY (…)`、终止分号之后的普通注释 | 腾讯建表语法 + MySQL 5.7 数值/字符串类型语法（腾讯声明继承） | **三版 sqlglot 一致 ParseError** | 规划器**具名接受**（不再藏在普通 `plan=False` 里），候选无法生成 → **失败关闭、保留原 E999** | **0 次** | 🔔 **随 Rev.N 提请用户知悉** |

**KFN-4 的确切代价**：出现上述形态时继续报 `E999_SYNTAX_ERROR`。

- **与本次修复无关**：这些形态在当前生产版本 v1.6.2.1 上同样报 E999，**修复前后行为一致**；
- **为什么仍要在类型表/消费器里登记**：第十二轮 BLOCK-12-03 明确要求"所有当前无法恢复的官方形态
  必须进入 KFN 表，不能藏在普通 `plan=False` 中"——规划器接受、候选失败关闭，
  这样它就**落在具名 KFN 上**，而不是与真正的非法语法混为一谈；
- **复检触发条件**：升级 `sqlglot` pin 时重跑 §7.1e 的 R12-TY-K / R12-CN / R12-SC-K 组；
  上游支持后这些条目可直接从 `pos_known` 改回 `pos`，**无需改本方案代码**；
- **manifest 登记位置**：`R12-TY-K-01…06`、`R12-CN-08`、`R12-SC-K-01/02`，
  分类 `pos_known`，`prov=SQLGLOT_LIMIT`。

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

### 5.29 第十一轮整改实测（Rev.M 历史）

> 全部实测在 **sqlglot 30.14.0**（发布锁定版）上取得，并在 **29.0.0 / 30.17.0** 上逐条复核一致。
> 当时的结果保留作历史证据；当前资产路径与命令以 §7.4 为准，不得照抄本节旧命令。

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

### 5.30 第十二轮整改实测（Rev.N 新增）

> **Rev.N 历史证据。** 本节描述当时实现及 501/511 结果，不得直接转写成 Rev.O 的施工或
> 准出结论。尤其 5.30.1 的 owner 定位、5.30.3 的 SERIAL 恢复与 5.30.4 的具名 UNIQUE
> 处置，均由 §5.31 的原文 span、KFN-5 和失败关闭机制替代。

> 复现命令：`python docs/evidence/v1.6.2.2/run_all.py`（临时目录重建 + 全量断言，不触碰工作区）。
> 全部结论在 **sqlglot 29.0.0 / 30.14.0 / 30.17.0 三版一致**。

#### 5.30.1 BLOCK-12-01：可执行注释的**位置**必须参与整句判定

Rev.M 的 `_collect_executable_comments()` 只返回裸 payload 字符串，丢掉了它挂在哪个 token 上。
于是它证明的只是"payload 单独看像一个合法分区"，**没有证明"把 payload 放回原位置后整条语句合法"**。

Rev.N 的三处机制改动：

1. **保留位置**：返回 `(owner_idx, payload)`。实测（三版一致）可执行注释挂在它**前面**那个主 token 上，
   所以 `owner_idx` 就是它在原 SQL 中的插入序；
2. **位置判据**：`owner_idx >= close_idx` 才算落在顶层表尾域。
   ⚠️ 边界是 `>=` 而不是 `>`——"定义列表右括号之后、且没有任何表选项"这种最常见的 `mysqldump`
   形态，owner 恰好就是那个右括号本身，它在原文里位于括号**之后**，属合法表尾位置；
3. **并入 atom 流**：合法 payload 解析成 `PARTITION` atom，按 `owner_idx` 插进
   `_scan_table_tail()` 的序列，**与主 token 流共用**同一份"二级分区至多一个"计数
   和同一张 capability profile 表——不再是旁路例外。

| 反例 / 正例 | Rev.M | Rev.N |
|---|---|---|
| 可执行分区插进**列定义内部** | plan=ACCEPT → `Create` ❌ | **plan=REJECT**（位置越界）✅ |
| 可执行分区位于 **CREATE 之前** | plan=ACCEPT → `Create` ❌ | **plan=REJECT**（位置越界）✅ |
| 主 token 流已有分区，**再追加**可执行分区 | plan=ACCEPT → `Create` ❌ | **plan=REJECT**（分区计数 = 2）✅ |
| **广播哨兵**之后追加可执行分区 | plan=ACCEPT → `Create` ❌ | **plan=REJECT**（`[…, BROADCAST_SENTINEL, PARTITION]` 无 profile）✅ |
| 表尾合法位置 + 无表选项 | ACCEPT | **ACCEPT**（`[PARTITION]` → LEGACY_PARTITION）✅ |
| 表尾合法位置 + `shardkey=sk` | ACCEPT | **ACCEPT**（官方二级分区原例）✅ |
| 表尾合法位置 + `TDSQL_DISTRIBUTED BY HASH` | ACCEPT | **ACCEPT**（TARGET_CURRENT）✅ |

**R12-EC 组按"位置 × 主表尾 atom"的笛卡尔积生成**，期望值直接由 profile 表推导，不是抄实测。

#### 5.30.2 BLOCK-12-02：规划器必须拿到未删分号的原串

`_strip_terminal_semicolon()` 本身正确，但 `parse()` 入口的 `sql.strip().rstrip(";")`
让它在真实调用链上**不可达**。改动见 §3.3b。

| 原始结尾 | `_plan_recovery(原串)` | Rev.M 端到端 | Rev.N 端到端 |
|---|---|---|---|
| 无分号 / `;` / `;` + 空白 | ACCEPT | `Create` ✅ | `Create` ✅ |
| `;;` / `;;;` / `; ;` / `;\n;` | REJECT | **`Create`** ❌ | **`NoneType` + E999** ✅ |
| `; CREATE TABLE u (…)` | REJECT | —— | **`NoneType` + E999** ✅ |
| 字符串字面量内的 `;` | ACCEPT | `Create` | `Create` ✅（词法作用域内不可见） |

**R12-SC 组全部走真实 `SQLParser.parse()`**，断言最终 AST 与 E999，不只断言 `plan=False`。

#### 5.30.3 BLOCK-12-03：一个类型可以有多条产生式

根因是 Rev.M 的 `名 → 单一 arity` 表达不了同一关键字的多种合法产生式。
`FLOAT` 是最典型的：官方同时存在 `FLOAT(p)`（p ∈ 0..53，语义是精度位数）
与 `FLOAT(M,D)`（M ∈ 1..255、D ∈ 0..30）。Rev.M 把两者塞进同一个 `M_D`，
于是**同时**造成合法下界被误拒、非法上界被误收。

| 形态 | 官方性质 | Rev.M | Rev.N |
|---|---|---|---|
| `FLOAT(0)` / `FLOAT(53)` | `FLOAT(p)` 上下界 | plan=False ❌ | **ACCEPT** ✅ |
| `FLOAT(54)` | 越 `FLOAT(p)` 上界 | ACCEPT → gate=True ❌ | **REJECT_PLAN** ✅ |
| `FLOAT(10,2)` | `FLOAT(M,D)` | ACCEPT | **ACCEPT** ✅ |
| `DEC(10,2)` / `DEC(10)` / `DEC` | DECIMAL 官方同义词 | plan=False ❌ | **ACCEPT** ✅ |
| `NCHAR(n)` / `NVARCHAR(n)` / `CHARACTER(n)` / `CHARACTER VARYING(n)` | 官方别名 | plan=False ❌ | **ACCEPT** ✅ |
| `SERIAL` | 官方别名（sqlglot 原样保留） | plan=False ❌ | **ACCEPT** ✅ |
| `SET(…64 个成员…)` | 上界 | ACCEPT | **ACCEPT** ✅ |
| `SET(…65 个成员…)` | 越上界 | ACCEPT → gate=True ❌ | **REJECT_PLAN** ✅ |
| `DEFAULT .2` / `DEFAULT -.5` | 腾讯官方列出的数值字面量 | plan=False ❌ | **ACCEPT**（源侧规范成 `0.2` / `-0.5`，与候选回生成一致）✅ |
| `INT SIGNED` / `VARCHAR(n) BINARY` / `NATIONAL CHAR(n)` | 官方合法，sqlglot 三版 ParseError | 端到端失败、**未登记** ❌ | **失败关闭 + 登记 KFN-4** ✅ |

> ⚠️ 词法实测（供施工核对）：`CHARACTER VARYING` 与 `DOUBLE PRECISION` 是**单个** token，
> `NATIONAL CHAR` 是**两个** token；`.2` 被切成 `DOT` + `NUMBER` 两个 token。
> 三者的处理分别落在 `_TYPE_RULES` 键、`_TYPE_MULTIWORD`、`_consume_default_value()`。

#### 5.30.4 BLOCK-12-04 / MAJOR-12-01：指纹升级为 CreateShape

Rev.M 的门禁只比较定义列表，`plan["fingerprint"]["tail"]` **生成了但从未被读取**，
顶层 CREATE 语义根本没进指纹。这些不是装饰信息——现有规则直接读
`parsed.engine` / `parsed.charset` / `has_table_comment` / `is_temporary_table`。

```text
CreateShape
  ├─ head          (schema, table) + TEMPORARY + IF NOT EXISTS
  ├─ definitions   列 / 索引 / 具名约束（Rev.M 已完成，本轮只补具名约束 symbol）
  └─ tail          本地表选项（排序多重集）+ 二级分区（方法/键/分区名/VALUES 边界）
```

候选侧的镜像提取器沿用**第十一轮的教训**——不另写一套 property 类名映射，
而是把候选属性**逐个回生成**后送进**同一个** `_scan_table_tail()`：
`CHARSET` / `CHARACTER SET`、引号风格、`=` 有无这些差异被同一个消费器自动归一，两侧不可能各自漂移。

> ⚠️ 不能直接用 `node.sql()` 的整句文本：sqlglot 一旦遇到它不认识的表选项
> （`shardkey=`、`STATS_PERSISTENT=`），回生成时会把**整组**属性包进 `WITH ( … )`（实测），
> tail 扫描随即失败、把合法正例判成不守恒。逐属性渲染就没有这个容器。

**source-only approved transform**：`TDSQL_DISTRIBUTED` / `BROADCAST` / `shardkey` 是既定掩码目标，
可执行注释里的分区 sqlglot 根本看不见，分区定义里的 `[STORAGE] ENGINE` / `COMMENT` 也是既定掩码目标
——这四类**不参与**候选比较，由 raw SQL 规则与 capability profile 负责，
在代码里由 `_SOURCE_ONLY_TAIL_TAGS` 与 `_tail_comparable()` 显式标出，不与普通 table tail 混为一谈。

| 单点变异 | Rev.M 门禁 | Rev.N 门禁 |
|---|---|---|
| `CREATE TEMPORARY` → `CREATE` | **True** ❌ | **False** ✅ |
| 删除 `IF NOT EXISTS` | **True** ❌ | **False** ✅ |
| `db1.t` → `db2.t` / 删除 schema | **True** ❌ | **False** ✅ |
| `ENGINE` / `CHARSET` / `COLLATE` / `ROW_FORMAT` 改变 | **True** ❌ | **False** ✅ |
| 表 `COMMENT` 文本改变 / 删光全部表选项 / 凭空多出 `AUTO_INCREMENT` | **True** ❌ | **False** ✅ |
| 分区方法 / 分区键 / 分区名 / `LESS THAN` 边界 / 分区个数 / 分区顺序改变 | **True** ❌ | **False** ✅ |
| 整个分区被抹掉 | False | **False** ✅ |
| constraint symbol 改变 / 去掉 `CONSTRAINT` 包装 / 主键列改变 | —— | **False** ✅ |

**MAJOR-12-01**：官方 `[CONSTRAINT [symbol]] PRIMARY KEY (…)` 在候选里是
`exp.Constraint(this=symbol, expressions=[PrimaryKey])`。Rev.M 把它直接丢给只认
`PrimaryKey/Unique/Index` 的形状提取器，必然返回 `None`，于是这条**官方合法**、
又带用户已冻结的合法 HASH 方言的语句被系统性误杀。Rev.N 解包并比较 symbol：

| 形态 | Rev.M | Rev.N |
|---|---|---|
| `CONSTRAINT pk PRIMARY KEY(id)` + `TDSQL_DISTRIBUTED BY HASH` | gate=False → `Command` ❌ | **gate=True → `Create`** ✅ |
| `CONSTRAINT pk PRIMARY KEY(id)` + `UNIQUE … COMMENT` | gate=False → E999 ❌ | **gate=True → `Create`** ✅ |
| `CONSTRAINT PRIMARY KEY(id)`（无 symbol） | 候选 ParseError | 候选 ParseError → **失败关闭 + 登记 KFN-4** ✅ |
| `CONSTRAINT uq UNIQUE (…)`（NG-10 冻结） | 消费但不作恢复目标 | **Rev.N 历史口径；Rev.O 已废止“顺带恢复”，改为具名失败关闭** |

### 5.31 第十三轮整改机制（Rev.O 历史规范；冲突处由 §5.32 取代）

#### 5.31.1 R054 的输入必须是完整 UNIQUE 集合

TDSQL 要求每一个唯一索引都包含 shardkey。Rev.O 对 UNIQUE 语法域作显式分流：

| 源语法 | TDSQL 性质 | Rev.O RecoveryPlan | ParsedSQL 结果 | R054 断言 |
|---|---|---|---|---|
| `id INT UNIQUE` | 官方合法 | ACCEPT | `indexes=[{name:id, columns:[id], type:UNIQUE}]` | shardkey=id 不报；shardkey=sk 时报告该索引 |
| `id INT UNIQUE KEY` | 官方合法 | ACCEPT | 同上 | 同上 |
| `UNIQUE [KEY\|INDEX] u(id,sk)` | 官方合法 | ACCEPT；COMMENT 可按既有主目标掩码 | 表级 UNIQUE 精确产出 | 每一个索引逐个判断，不做列并集 |
| 多个列级/表级 UNIQUE 混合 | 官方合法 | ACCEPT | 所有 UNIQUE 均产出且无漏项；表级从 `exp.Schema.expressions` 读取，不能依赖 raw 回退 | 任意一个不含 shardkey 即命中 |
| `CONSTRAINT uq UNIQUE(id)` | 官方合法但用户冻结本期不扩 | **REJECT_PLAN** | 不产生“看似成功”的 ParsedSQL | 保留失败关闭，不允许 R054 静默跳过 |
| `SERIAL` | 官方别名，含隐式 UNIQUE | `plan.kfn=KFN-5-SERIAL` | 最终不恢复 | 保留 E999，不能伪装成无约束普通列 |
| `BIGINT SERIAL DEFAULT VALUE` | 官方约束别名 | `plan.kfn=KFN-5-SERIAL-DEFAULT-VALUE` | 最终不恢复 | 同上 |

列级 UNIQUE 的语义对象必须同时放进 `indexes` 与 `index_definitions`，并使用两个独立 dict，
避免未来某个消费者原地修改对象时污染另一列表。测试除 R054 外还要跑所有结构消费者：
R057/R058/R059/R060/R061、DML 索引数量规则及 Oracle 兼容规则；结果以规则集合精确差分确认，
不以“没有抛异常”代替。

匿名列级 UNIQUE 的数据库有效索引名按 MySQL/TDSQL 隐式规则取列名，而不是伪造空串。
因此 R061 可能对 `id INT UNIQUE` 给出“唯一索引 id 应以 uk_ 开头”的命名提示；这是现有命名
规范应用到真实隐式索引名的结果，不是 parser 假阳性。R13-UQ 必须把该规则集合写成显式 oracle。
若产品决定豁免隐式命名，应另行修改 R061 的规则策略，不能让 parser 隐瞒索引名来规避告警。

#### 5.31.2 可执行注释使用字符 span + 完整 atom 边界

`owner_idx` 只能说明 sqlglot 把注释挂在哪个 token，不能说明注释是否位于一个复合 atom 内部。
Rev.O 的位置模型为：

```text
ExecComment = {
  comment_start, comment_end,     # 原 SQL 半开字符区间
  left_idx, right_idx,            # 注释所在 token-free gap 的左右 token
  payload, partition_shape
}

允许插入 ⇔ (left_idx, right_idx) 恰好等于两个完整 table-tail atom 的边界
```

以 `ENGINE = InnoDB` 为例，它被 `_consume_table_option()` 一次消费成一个 LOCAL atom，
合法边界只有 atom 之前和 `InnoDB` 之后；`ENGINE` 与 `=`、`=` 与 `InnoDB` 之间均不在边界集。
`TDSQL_DISTRIBUTED BY HASH(id)`、`CHARACTER SET utf8mb4`、`shardkey=(a,b)`、
`PARTITION BY … (definitions)` 同理。

定位器只在 sqlglot 主 token 之间的 gap 内查找 `/*!…*/`：字符串、反引号标识符和普通 SQL
表达式均已被词法器占据，不会进入 gap。这里的正则只提取注释边界，不改写 SQL，也不承担
SQL 语法识别；payload 仍由 sqlglot 重新词法化并由 `_consume_secondary_partition()` 完整消费。

必须生成的组合矩阵：

- 每个 compound atom：ENGINE、CHARACTER SET、COLLATE、shardkey 单/多列、
  TDSQL_DISTRIBUTED HASH/RANGE/LIST、PARTITION BY、partition definition；
- 每个 atom 的 before / 每个内部 gap / after；
- 与无分布、HASH、DIST、广播哨兵、已有二级分区五种 profile 组合；
- 终止分号之前、终止分号之后、第二条语句之前三种语句边界。

内部 gap 和终止分号之后必须 `plan=None`；合法 atom 边界仍需再经过“二级分区至多一个”与
capability profile，位置合法不代表组合必然合法。

#### 5.31.3 TypeProduction、family 与 KFN-5

Rev.O 把类型拆为五个不可缺失的维度：源 token 产生式、canonical、参数产生式、family、
隐含语义/KFN。`TypeShape` 为：

```text
(canonical, args, normalized_attrs, family, kfn_ids)
```

| 形态 | Rev.O 处置 | 原因 |
|---|---|---|
| `TEXT/BLOB(65535/65536/16777215/16777216)` | pos，保留 M | M 是选择最小可容纳类型的提示，不能硬限 65535 |
| `TEXT/BLOB(4294967295)` / `(4294967296)` | 前者 pos、后者 neg | `TEXT(M)`/`BLOB(M)` 的长度提示上界是 2^32−1 |
| `TINYTEXT/TINYBLOB/MEDIUMTEXT/MEDIUMBLOB/LONGTEXT/LONGBLOB(M)` | neg | 只有裸 `TEXT[(M)]` / `BLOB[(M)]` 有该长度提示；具名容量变体的官方产生式不带 M，禁止误用同一参数表吞错 |
| `NCHAR VARCHAR(n)` | KFN-5-NCHAR-VARCHAR | 官方合法；30.14.0 候选 ParseError |
| `NATIONAL CHARACTER VARYING(n)` / `NATIONAL CHAR VARYING(n)` | KFN-5-NATIONAL-VARYING | 最长多 token 产生式具名接受；当前 pin ParseError |
| `CHAR BYTE` | KFN-5-CHAR-BYTE | 官方 BINARY 别名；当前 pin ParseError |
| `VARCHAR(n) ASCII/UNICODE` | KFN-5-ASCII / KFN-5-UNICODE | 规划器按字符族识别；当前 pin ParseError |
| `SERIAL` / `SERIAL DEFAULT VALUE` | KFN-5 | 隐含审核语义不能只保留类型名 |
| 字符族 `CHARACTER SET` / `COLLATE` | pos | 列级与表级共用 `_charset_kw_end()` |
| `INT/DATE/JSON CHARACTER SET`、`DATE COLLATE` | neg | family 错配，规划层先行拒绝 |

KFN-5 是**本期有意识保留的误报**，不是普通能力缺口。每条必须登记官方依据、发布 pin 行为、
语料频度、用户批准状态和解除条件。解除条件不是“sqlglot 能解析了”，而是：候选可保真、
ParsedSQL 所有隐含语义已展开、规则消费者精确差分通过、用户批准从 pos_known 迁回 pos。

#### 5.31.4 具名 PRIMARY COMMENT 与列 COMMENT

源侧 `CONSTRAINT pk PRIMARY KEY(id) COMMENT 'p'` 的 PRIMARY COMMENT span 必须进入
`primary_spans`。Rev.N 在 constraint 分支把 `_usp` 丢弃，导致候选仍携带 wrapper comment，
随后又被 `len(inner)==1` 拒绝。Rev.O 同时修两端：

1. source：只接受内部 kind=PRIMARY，收集其 COMMENT span；kind=UNIQUE 立即失败关闭；
2. candidate：`expressions` 中恰好一个 PrimaryKey；可附带 CommentColumnConstraint，
   但只有 source options 含 COMMENT 时才允许；任何其他 inner node 都拒绝；
3. 比较 constraint symbol、PK 键列、USING 和批准变换后的 COMMENT 语义；
4. 端到端断言 `has_primary_key=True`、columns 完整、R003/R004/R005/R028 不出现。

列 COMMENT 采用“结构门禁比较存在性、ParsedSQL 比较实际文本”的双层策略：

- CreateShape：删除或凭空增加 COMMENT 必须拒绝；
- ParsedSQL：`column_comments[col]` 和 `columns[].has_comment/comment`（若字段存在）精确等于源值；
- 不在 CreateShape 重复实现 MySQL 字符串转义规范，避免两套解码器漂移。

#### 5.31.5 证据资产的设计/施工双模式

Rev.O 废止 Rev.N “从调用时工作树文件打补丁并与施工文件逐字节相同”的单模式。新契约：

| 模式 | 输入 | 用途 | 不变量 |
|---|---|---|---|
| `design` | 固定 baseline commit/blob + 本说明书施工块 | A 评审方案时复现期望实现 | 不读取工作树 parser 作为基线；重建产物只写临时目录 |
| `implementation` | 当前提交的 parser + 依赖文件 | 开发完成后的真实准出 | 不再次应用“改动前→改动后”；直接测试当前产品文件 |

统一哈希定义：把 UTF-8 文本的 CRLF/CR 规范成 LF，再对 UTF-8 bytes 计算 SHA256，名称固定为
`normalized_utf8_sha256`。若还需真实包字节哈希，另列 `raw_file_sha256`，两者不得混称。

Windows 默认命令必须不设置额外环境变量即可运行。runner 自身只输出 ASCII；对子进程显式使用
UTF-8 解码并 `errors="replace"`，同时给 Python 子进程传 `PYTHONUTF8=1`。`requirements.txt`、
`pyproject.toml` 和运行时 `sqlglot.__version__` 三者必须同时精确等于 30.14.0。

三版本矩阵必须使用隔离解释器/venv，不能靠当前进程临时改 `PYTHONPATH` 冒充一键复现。
29.0.0/30.17.0 是兼容对照，30.14.0 是发布阻断；三版结果可按 `expected_by_version` 显式不同，
但任何差异必须在 manifest 有原因，不能静默跳过。

#### 5.31.6 验收从 AST 下沉到 ParsedSQL/RuleChecker

每条 manifest case 新增 `oracle`，禁止由测试函数按 cid 猜测：

```text
oracle = {
  plan: ACCEPT | REJECT | KFN(id),
  ast: Create | NotCreate,
  parsed: {columns, indexes, has_primary_key, column_comments, ...},
  rules: {exact | contains | excludes},
}
```

- R054 正负例必须用真实分布式 `instance_type`，同时断言 `parsed.indexes` 和规则集合；
- 列级/表级 UNIQUE 新进入结构消费者后，R018/R019/R061/R063/R065/R066/R067 等可能出现
  **由事实恢复引起的预期告警变化**。漂移报告必须逐条标记 `expected_semantic_fix` 并给出源索引；
  不允许继续写“全语料规则集合绝对零漂移”，也不允许把未解释的新告警统称为正确；
- `pos_known` 必须断言 plan=KFN(id)，不是普通 REJECT；
- mutation candidate 解析异常直接使 suite 失败，或进入显式 `unparseable` 计数并由 manifest 声明；
- 自动生成正文使用唯一 BEGIN/END marker，marker 在全文出现次数必须恰好为 1，区段内容精确相等；
- collect 公式固定为 `len(CASES) + len(MUTATION_SUITES) + 1 fuzz item`，变异内部 assertion 数只作独立统计。

### 5.32 第十四轮整改机制（Rev.P 当前规范）

#### 5.32.1 UNIQUE 的“两域一真源”

“两域”不是两份可漂移的重复数据：parser 只从同一 AST/schema traversal 提取一次事实，然后按
消费者兼容边界分发：普通/PRIMARY/FULLTEXT 等继续进 legacy `indexes`；UNIQUE 只进
`unique_constraints`。R054 读取完整 UNIQUE 真源；R077/R061 继续读取 legacy 域。

| 路径 | `unique_constraints_complete` | R054 来源 | R077 来源 |
|---|---:|---|---|
| 原生或恢复后的完整 `exp.Create` | `True` | `unique_constraints`，禁止 raw 混入 | legacy `indexes` + 既有 raw，行为冻结 |
| `Command` / parse error / 未完整提取 | `False` | 既有 `_UNIQUE_IDX_RE` 回退 | 完全沿用既有行为 |
| KFN-5/KFN-6 | `False` | 不把不完整语义冒充完整；E999 阻断 | 行为不作为放行依据 |

双列表重复插入被禁止：任何 UNIQUE 出现在 `indexes/index_definitions` 都是测试失败；任何支持域
UNIQUE 在 complete=True 时未出现在 `unique_constraints` 也是测试失败。混合“1 列级 + 2 表级”
必须产出恰好 3 条、保持源顺序；前缀索引只记基列名，函数/表达式不得伪造成列。

#### 5.32.2 全路径 KFN 处理机制

preflight 只负责识别**已被完整消费的已知保真缺口**，不把 unknown/非法语法误标为 KFN。
命中 KFN 后仍允许 sqlglot 提取能安全获得的结构用于诊断，但 parse finalize 必须设置稳定
`KNOWN_FIDELITY_GAP[...]`，Checker 必须产生 E999。失败关闭的判据是“审核结论被 E999 阻断”，
不是“为了满足测试而强行清空 AST”。

三路径矩阵对 `CONSTRAINT uq UNIQUE(c)`、`SERIAL`、`SERIAL DEFAULT VALUE` 分别覆盖：

1. 无方言/无目标 COMMENT，sqlglot 原生 `Create`；
2. 追加 `TDSQL_DISTRIBUTED BY HASH(sk)`，首次为 `Command`；
3. 与 `UNIQUE KEY ... COMMENT` 组合，首次抛 ParseError。

每格必须断言：KFN 编号精确、E999 存在、不得出现“无 E999 + columns/indexes 为空”；并记录最终
精确规则集合。注释/DEFAULT/string/quoted identifier 内的 `SERIAL` 或 `CONSTRAINT ... UNIQUE`
是反向鉴别，preflight 必须为空且原行为不变。

#### 5.32.3 第十四轮非目标守恒门

- A 报告列出的三个专项文件必须 `45 + 14 + 12 = 71 passed`，不得改旧期望求绿；
- `pytest tests/` 必须 0 failed；
- 201 条语料 + 生产 14 表按 `(statement, rule_id, message)` 比较，除本次目标 R054/解析恢复外
  非目标漂移为 0；R061/R067/R018/R019 尤其单列；
- 两份生产 fixture 精确规则集合保持目标值；
- `distributed.py` diff 只能落在 `_iter_unique_indexes()`，R077 类代码哈希/文本必须与 baseline 相同。

#### 5.32.4 证据阶段

Rev.P 评审交付必须包含可运行的 design 模式、完整 marker action 表、真实 normalized hash、更新后的
manifest 与正文生成区段。implementation 模式也必须实现，但在产品尚未施工时应输出稳定 ASCII
状态 `NOT_IMPLEMENTED` 并非零退出；施工提交上才要求全绿。任何让 implementation 模式重新应用
设计补丁的做法都属于验错对象。

## 6. 与既有缺陷的交互 / ADJ 台账

### 6.1 ADJ-5 在 Rev.P 中的收敛边界

Rev.N 把“真 UNIQUE 仍可能不进入 `parsed.indexes`”当成可继续依赖 raw 正则的 ADJ。
第十三轮以列级 UNIQUE 证明该论证不成立：只要 parser 产出任意一个结构化 UNIQUE，
`_iter_unique_indexes` 就会早退；而 raw 正则本身也不识别列级 UNIQUE、SERIAL、
`CONSTRAINT … UNIQUE`。

Rev.P 不让 UNIQUE 进入 legacy `parsed.indexes`，而把支持域切成两个互斥集合：

- **本期支持且必须结构化供数**：表级 `UNIQUE [KEY|INDEX]`、列级 `UNIQUE [KEY]`；
- **本期不能完整供数，整句失败关闭**：`CONSTRAINT symbol UNIQUE`、`SERIAL`、
  `SERIAL DEFAULT VALUE`。

因此 ADJ-5 不再靠“R077 看不见 UNIQUE”这个 parser 缺陷维持；完整语义被隔离到
`unique_constraints`，R077 的冻结行为则由消费者边界显式维持。施工验收必须证明 complete=True 时
不存在另一个已支持但未进入该通道的 UNIQUE，同时 legacy 列表逐键等于基线。

### 6.2 ADJ 台账更新

| 编号 | 内容 | 状态 |
|---|---|---|
| ADJ-1 解析降级漏审 | ✅ v1.6.2.0 已修 |
| ADJ-2 / ADJ-3 `tdsql_connector` | ⏸ Phase 2（ADJ-3 仍是真实缺陷） |
| ADJ-4 R077 宽松 OR | 🔒 用户决策：永久关闭 |
| ADJ-5 legacy `parsed.indexes` 不完整产出 UNIQUE | ✅ **Rev.P 显式隔离**：不改变 legacy 输出域；表级/列级 UNIQUE 完整进入 `unique_constraints`，R054 独占消费；CONSTRAINT UNIQUE=KFN-6，SERIAL=KFN-5 |
| ADJ-6 BROADCAST 冲突 | 🔒 用户决策：关闭 |
| ADJ-7 R116/R117/R118 对 HASH 不感知 | ⏸ 未修 |
| ADJ-8 `oracle_compat.clean_sql()` `--` 词法 | ⏸ 未修 |
| ADJ-9 解析器索引名未去引号 | ⏸ 未修（v1.6.2.1 登记） |
| **ADJ-10** | **`except` 路径未调用 `_regex_fallback_create_table_props()`**，导致"重试也救不回来"的语句仍会让 R003/R004/R005/R028 误报。该函数不感知字符串字面量，直接启用可能引入 R003 漏报，需专项评估 | 🆕 **本次登记，不修**（NG-8） |
| **ADJ-11** | **`CONSTRAINT c UNIQUE (col)` 形态的唯一索引完全不可见** | 🔒 用户决定本期不扩支持；Rev.P 以全路径 KFN-6 + E999 阻断，不再只依赖恢复规划器。以后若扩支持，必须补 ParsedSQL/R054 端到端语义 |
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
| `docs/evidence/v1.6.2.2/parser_recovery_manifest.py` | **唯一 case manifest**。每条用例含稳定 `cid`、SQL、`klass`、`prov`、`note` 和结构化 `oracle` |
| `docs/evidence/v1.6.2.2/test_parser_recovery_manifest.py` | 参数化 pytest：逐条执行 manifest；判据完全来自 oracle，本文件不含用例数据或 cid 特判 |
| `docs/evidence/v1.6.2.2/manifest_doc.py` | 从 manifest **精确替换**下方唯一 BEGIN/END 区段并生成计数 |
| `docs/evidence/v1.6.2.2/codestat.py` | 从固定 baseline blob 与目标文件生成 §3.4 的规模表、函数清单与唯一性检查 |

> ⚠️ **禁止在任何章节人工维护第二份用例数量。** 本节以下所有数字都是
> `python docs/evidence/v1.6.2.2/manifest_doc.py` 的输出，改用例只改 manifest，重跑本命令即可。
> 施工后以 `pytest --collect-only -q` 的实际收集数为最终证据，要求**零 skip**。
> Rev.N 的 501/511 是历史基线；Rev.P 当前数量由已升级生成器从 manifest 重算，禁止人工复写。

#### 7.1.0 分类语义（`klass`）

```text
pos                   必须恢复：规划器接受 → Create → 无 E999，并满足该 case 的
                      parsed_oracle / rules_oracle；只断言 AST 不算通过
neg                   必须失败关闭：token 规划器**先行拒绝**，且最终 AST 不得为 Create
                      （不能只依赖候选 parser 或 AST 门禁恰好拒绝）
pos_known             TDSQL 官方合法、本期不能保真 → 必须具名登记 KFN；存在恢复计划的路径
                      由 plan 携带精确编号，全路径最终必须 E999 失败关闭，不得静默形成空结构
unsupported_unproven  无 TDSQL/目标实例证据 → 必须失败关闭（KFN-B），
                      既不冒充合法，也不冒充非法
fail_closed           已见审核语义但结构无法完整表达 → 必须 E999；AST 是否为 Create
                      不得替代失败判据，`unique_constraints_complete` 必须为 False
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
| **R13-UQ / R14-UQ** | 列级/表级 UNIQUE 混合必须精确进入 `unique_constraints`，并断言 legacy `indexes/index_definitions` 中 UNIQUE 数为 0；至少包含“1 个列级 + 2 个表级，其中后一表级违规”的次序反例；覆盖合法 `col(n)` 前缀；再做每个 UNIQUE 包含/不包含 shardkey 的 R054 双向断言。`unique_constraints_complete=True` 时 R054 禁止混入 raw 结果；CONSTRAINT UNIQUE=KFN-6，SERIAL=KFN-5 |
| **R14-UQ-04** | sqlglot 29.0.0 将第二个列级 UNIQUE 折叠到首节点 `this`、30.x 可能形成第二个约束；两种 AST 都必须得到 `unique_constraints_complete=False` + E999 + 精确规则集合，不能把版本差异写成 skip |
| **R13-EC** | compound atom × before/internal/after × profile；必须走真实 parse。internal gap 与分号之后均 plan 拒绝，不允许只测 comment collector |
| **R13-TY** | 本报告列出的 9 类官方形态全部 plan=pos/KFN；仅 `TEXT/BLOB(M)` 覆盖 65535、65536、16777215、16777216、4294967295/4294967296 边界；六种具名 TINY/MEDIUM/LONG TEXT/BLOB 带 `(M)` 必须为 neg；family 错配为 neg；列级 CHARACTER SET 三版按 manifest 明示期望；National varying 的 3-token、2-token（后一个 token 自带空格）与单 token 词法形态必须归一到同一 KFN |
| **R13-CN** | 具名 PRIMARY 自身 COMMENT 与 HASH/广播/独立 UNIQUE COMMENT 组合；断言 columns、PK、规则集合；CONSTRAINT UNIQUE 伴随其他主目标仍拒绝 |
| **R13-M** | 列 COMMENT 保留/删除/凭空增加；删除/增加拒绝，文本变化由 ParsedSQL 精确 oracle 判断；所有 mutation candidate 解析异常必须计数并失败 |
| **R14-KFN-CU / R14-KFN-SE** | native Create、TDSQL Command、UNIQUE COMMENT ParseError 三路径逐一断言 source preflight 精确 KFN、最终 E999、`ParsedSQL.known_fidelity_failures` 保留编号及规则集合精确相等；只有后两条恢复路径要求 RecoveryPlan 同时携带 KFN |
| **R14-KFN-DECOY** | 列 COMMENT、DEFAULT 字符串、反引号标识符内的 `SERIAL`/`CONSTRAINT UNIQUE` 不得命中 preflight，最终仍是 Create 且无 E999 |

<!-- BEGIN AUTOGENERATED MANIFEST TABLES: docs/evidence/v1.6.2.2/manifest_doc.py -->
<!-- 本节由 docs/evidence/v1.6.2.2/manifest_doc.py 生成，请勿手改 -->

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
| **N** | 5 | 作用域负向 | pos_known×1  spans×4 |
| **X** | 40 | 方言尾子句安全交叉矩阵 | pos×40 |
| **Y** | 20 | 方言语法严格性与语句边界 | pos×7  neg×10  spans×3 |
| **Z** | 22 | 方法参数与表名精确形态 | pos×11  neg×10  unsupported_unproven×1 |
| **W** | 28 | 目标上下文完整性 | pos×10  neg×15  unsupported_unproven×3 |
| **合计** | **160** | —— | pos×96  neg×39  pos_known×5  unsupported_unproven×4  ruleset×2  spans×13  contract×1 |

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

**§7.1e R12 组（第十二轮复审反例，按维度生成）**

| 子组 | 例数 | 说明 | 分类构成 |
|---|---:|---|---|
| **R12-EC** | 26 | 可执行注释位置 × 主表尾 atom（BLOCK-12-01） | pos×5  neg×21 |
| **R12-SC** | 9 | 语句终止符集成路径（BLOCK-12-02） | pos×4  neg×5 |
| **R12-SC-K** | 2 | 终止符后普通注释（KFN-4） | pos_known×2 |
| **R12-TY** | 34 | 官方类型产生式矩阵（BLOCK-12-03） | pos×21  neg×12  pos_known×1 |
| **R12-TY-K** | 6 | 官方类型：sqlglot 不支持（KFN-4） | pos_known×6 |
| **R12-CN** | 8 | 具名 PRIMARY 约束（MAJOR-12-01） | pos×6  pos_known×2 |
| **R12-CS** | 6 | 字符集拼写的跨版本词法差异（Rev.N 自查） | pos×6 |
| **合计** | **91** | —— | pos×42  neg×38  pos_known×11 |

**§7.1f R14 组（第十四轮复审反例）**

| 子组 | 例数 | 说明 | 分类构成 |
|---|---:|---|---|
| **R14-UQ** | 4 | UNIQUE 隔离语义通道 | pos×3  fail_closed×1 |
| **R14-KFN-CU** | 3 | CONSTRAINT UNIQUE 三路径 KFN | pos_known×3 |
| **R14-KFN-SE** | 3 | SERIAL 三路径 KFN | pos_known×3 |
| **R14-KFN-DECOY** | 3 | KFN 字面量/标识符反向鉴别 | pos×3 |
| **合计** | **13** | —— | pos×6  pos_known×6  fail_closed×1 |

**全局计数（唯一真源）**

| 项 | 值 |
|---|---:|
| manifest 用例总数 | **514** |
| 其中 `pos` | 269 |
| 其中 `neg` | 179 |
| 其中 `pos_known` | 32 |
| 其中 `unsupported_unproven` | 16 |
| 其中 `fail_closed` | 1 |
| 其中 `characterization` | 1 |
| 其中 `ruleset` | 2 |
| 其中 `spans` | 13 |
| 其中 `contract` | 1 |
| 变异门禁：套数 | **9** |
| 变异门禁：逐条断言数（每套 = 1 个正确候选 + N 个变异候选） | **53** |
| 模糊测试（seed=20260826，整体计 1 个 pytest item） | **6000** 条输入 |
| **`pytest --collect-only -q` 应收集** | **524** = 用例 514 + 变异套 9 + 模糊 1 |

> **三个口径不要混用**（第十二轮 MINOR-12-01）：`用例数` 是逐条 SQL；
> `逐条断言数` 是变异测试内部的 `assert` 次数；`collect 数` 是 pytest item 数——
> 一套变异是 **1 个** item 但含多条断言，模糊测试是 **1 个** item 但跑 6000 条输入。

**证据来源分布**

| provenance | 例数 |
|---|---:|
| `OFFICIAL` | 276 |
| `CORPUS` | 69 |
| `REVIEW_12` | 47 |
| `REVIEW_11` | 42 |
| `PROJECT_ACCEPTED` | 27 |
| `SQLGLOT_LIMIT` | 25 |
| `REVIEW_14` | 13 |
| `TARGET_INSTANCE` | 12 |
| `USER_DECISION` | 3 |

**已知假阴性 / 未证实能力登记（由 manifest 生成）**

| 类别 | cid | 形态 | 理由 |
|---|---|---|---|
| KFN-A（官方合法、暂不支持） | C-01 | `函数键值 ((lower(a)))` | 去掉 COMMENT 后 sqlglot 同样 ParseError → 非剥离器缺陷 |
| KFN-A（官方合法、暂不支持） | C-02 | `VISIBLE` | 去掉 COMMENT 后 sqlglot 同样 ParseError → 非剥离器缺陷 |
| KFN-A（官方合法、暂不支持） | C-03 | `KEY_BLOCK_SIZE` | 去掉 COMMENT 后 sqlglot 同样 ParseError → 非剥离器缺陷 |
| KFN-A（官方合法、暂不支持） | C-04 | `USING 前置于键值列表` | 去掉 COMMENT 后 sqlglot 同样 ParseError → 非剥离器缺陷 |
| KFN-A（官方合法、暂不支持） | N-01 | `N1 CONSTRAINT ... UNIQUE` | Rev.P：CONSTRAINT UNIQUE 本期不扩支持，三条解析路径均由 KFN-6 + E999 阻断 |
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
| KFN-A（官方合法、暂不支持） | R12-SC-K-01 | `分号后接行注释` | KFN-4：终止符后的普通注释是合法 MySQL，但三版 sqlglot 对整条语句一致 ParseError（掩码后得到 exp.Block，被守恒门禁拒绝）→ 失败关闭并具名登记 |
| KFN-A（官方合法、暂不支持） | R12-SC-K-02 | `分号后接块注释` | KFN-4：终止符后的普通注释是合法 MySQL，但三版 sqlglot 对整条语句一致 ParseError（掩码后得到 exp.Block，被守恒门禁拒绝）→ 失败关闭并具名登记 |
| KFN-A（官方合法、暂不支持） | R12-TY-23 | `SERIAL` | SERIAL 隐含 UNIQUE/NOT NULL/AUTO_INCREMENT，本期 KFN-5 阻断 |
| KFN-A（官方合法、暂不支持） | R12-TY-K-01 | `INT SIGNED` | KFN-4：SIGNED 属性：三版 sqlglot 一致 ParseError；已在类型表具名登记，不藏在普通 plan=False 里 |
| KFN-A（官方合法、暂不支持） | R12-TY-K-02 | `BIGINT SIGNED` | KFN-4：同上；已在类型表具名登记，不藏在普通 plan=False 里 |
| KFN-A（官方合法、暂不支持） | R12-TY-K-03 | `VARCHAR(20) BINARY` | KFN-4：字符族 BINARY 属性：三版一致 ParseError；已在类型表具名登记，不藏在普通 plan=False 里 |
| KFN-A（官方合法、暂不支持） | R12-TY-K-04 | `TEXT BINARY` | KFN-4：同上；已在类型表具名登记，不藏在普通 plan=False 里 |
| KFN-A（官方合法、暂不支持） | R12-TY-K-05 | `NATIONAL CHAR(10)` | KFN-4：NATIONAL 形态：三版一致 ParseError；已在类型表具名登记，不藏在普通 plan=False 里 |
| KFN-A（官方合法、暂不支持） | R12-TY-K-06 | `NATIONAL VARCHAR(10)` | KFN-4：同上；已在类型表具名登记，不藏在普通 plan=False 里 |
| KFN-A（官方合法、暂不支持） | R12-CN-07 | `无名 CONSTRAINT PRIMARY KEY` | KFN-4：官方允许省略 symbol，但三版 sqlglot 一致 ParseError → 失败关闭并具名登记 |
| KFN-A（官方合法、暂不支持） | R12-CN-08 | `CONSTRAINT symbol UNIQUE（NG-10 冻结，不作恢复目标）` | Rev.P：NG-10/ADJ-11 冻结；KFN-6 覆盖恢复路径并保留 E999 |
| KFN-A（官方合法、暂不支持） | R14-KFN-CU-01 | `CONSTRAINT UNIQUE / native Create` | 原生成功路径也必须被 source preflight 阻断 |
| KFN-A（官方合法、暂不支持） | R14-KFN-CU-02 | `CONSTRAINT UNIQUE / dialect Command` | Command 路径不得停在无 E999 的空结构 |
| KFN-A（官方合法、暂不支持） | R14-KFN-CU-03 | `CONSTRAINT UNIQUE / UNIQUE COMMENT ParseError` | except 恢复路径必须被同一 KFN 阻断 |
| KFN-A（官方合法、暂不支持） | R14-KFN-SE-01 | `SERIAL / native Create` | 原生成功路径也必须被 source preflight 阻断 |
| KFN-A（官方合法、暂不支持） | R14-KFN-SE-02 | `SERIAL / dialect Command` | Command 路径不得停在无 E999 的空结构 |
| KFN-A（官方合法、暂不支持） | R14-KFN-SE-03 | `SERIAL / UNIQUE COMMENT ParseError` | except 恢复路径必须被同一 KFN 阻断 |
<!-- END AUTOGENERATED MANIFEST TABLES -->

### 7.2 需修订的既有测试

既有测试不允许为了迁就实现而放宽，Rev.P 必须**新增/增强**以下断言：

- `test_r077_r054_tdsql_syntax.py` 增加列级 UNIQUE 与表级/列级混合的 R054 双向用例，并冻结 R077 legacy 输入域；
- parser manifest 的 pos 从 AST 断言升级为 ParsedSQL/RuleChecker oracle；
- mutation suite 对候选解析异常不再静默 `continue`；若某候选以“不可解析”为预期，必须由 manifest 显式分类并进入计数；
- 生产 fixture 仍保持精确规则集合，任何非目标漂移必须停工复核。

### 7.3 回归门槛（准出条件）

| 门槛 | 要求 |
|---|---|
| G-1 | `pytest tests/` 全量：**0 failed**。passed/skipped 数只记录本次实际输出，不作为跨环境硬编码门槛；Rev.N 的历史数字不得复制到 Rev.P 结论 |
| G-2 | `test_r077_r054_tdsql_syntax.py` 全通过、零 skip；Rev.P 新增列级/混合 UNIQUE 的隔离通道、legacy 零 UNIQUE、R054 双向用例必须被收集 |
| G-3 | `test_parser_tdsql_dialect_fallback.py` 全通过、零 skip |
| G-4 | `test_r061_index_name_quoting.py` 全通过、零 skip；同时验证 UNIQUE 不进入 legacy 索引域，R061 不出现次生告警 |
| G-5 | `docs/evidence/v1.6.2.2/test_parser_recovery_manifest.py` 全通过、零 skip；collect 数必须精确等于 `len(CASES) + len(MUTATION_SUITES) + 1 个 fuzz item`。变异 suite 内部 assertion 数单列统计，**不得**加进 collect 公式 |
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
| **K-1** | 类型名走 **`_TYPE_RULES`** 显式白名单，每型持有**一组产生式**；`RANGE`/`LIST`/`NULL` 等非类型 token 一律失败关闭 |
| **K-2** | 类型参数按类型模式校验：`VARCHAR(1,2,3)` / `INT(1,2)` / `DATE(1)` / `JSON(1)` / `DECIMAL(10,2,1)` 失败关闭；**官方 `DECIMAL(M,0)` / `DATETIME(0)` / `TIME(0)` 必须恢复** |
| **K-3** | `DEFAULT` 按官方字面量域：`foo` / `()` / `(,)` / `(SELECT 1)` 失败关闭；**`-1` / `+1` / 小数 / hex / bit / 布尔 / NULL / 时间函数必须恢复** |
| **K-4** | ~~官方列属性 `COLUMN_FORMAT` / `ENGINE_ATTRIBUTE` / 列级 `STORAGE` 必须恢复~~ → **Rev.M 更正（BLOCK-11-06）**：`COLUMN_FORMAT`（枚举仅 FIXED/DYNAMIC/DEFAULT，**删除 `COMPRESSED`**）与 `ENGINE_ATTRIBUTE` 作辅助掩码 span **端到端恢复**；列级 `STORAGE` 与 `SECONDARY_ENGINE_ATTRIBUTE` 腾讯官方建表页未列出，改判 `unsupported_unproven` **失败关闭** |
| **K-5** | 候选 AST 门禁比较**规范类型形态**：`JSON(1)`→`JSON` 这类漂移必须被发现 |
| **K-6** | 表尾走 **`_TAIL_PROFILES`** 具名 capability profile（typed atoms + 无环序列），每条允许序列有唯一 provenance；`shardkey=… ENGINE=…`、`BROADCAST…PARTITION`、`PARTITION…BROADCAST` 全部失败关闭 |
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

> **名称沿革（只写在这里，门槛表内不再出现旧名）**：
> `_TYPE_SPEC`（模式字符串）→ Rev.M `_TYPE_RULES`（结构化规则表）→ Rev.N `_TYPE_RULES`（多产生式）；
> `_TAIL_EDGES`（迁移表）→ Rev.M `_TAIL_PROFILES`（typed atoms + 无环 capability profile）。
> **施工一律以最终代码块（§3.0c）的名称为准**；历史名只在带"Rev.X 历史"提示的修订说明里出现。
| **M-1** | **可执行注释（BLOCK-11-01）**：`/*!…*/` 至多一个；payload 重新词法化后首 token 必须是 `PARTITION BY` 且被**完整消费到末尾**；`RANGE()` 空参、两条 `PARTITION BY`、`EVIL OPTION`、两个可执行注释全部失败关闭；合法 `/*!50100 PARTITION BY LIST … */` 必须恢复；普通 `/* */` 注释仍不可见、也不阻断恢复 |
| **M-2** | **表尾无回环（BLOCK-11-02）**：整条 atom 序列必须**完整匹配**一个具名 profile；`DIST→PARTITION→DIST`、`shardkey→PARTITION→DIST`、`PARTITION→DIST→PARTITION` 全部失败关闭；一级分布、二级分区各**独立计数、至多一个** |
| **M-3** | **广播哨兵精确分型（BLOCK-11-03）**：`BROADCAST_SENTINEL` 为终态原子；`shardkey=(noshardkey_allset)`、`shardkey=(noshardkey_allset,id)`、哨兵后接 `PARTITION BY` 全部失败关闭；裸哨兵必须恢复；ADJ-6 是**唯一**具名 characterization 例外 |
| **M-4** | **类型表双向闭合（BLOCK-11-04）**：TY 组矩阵（例数见 §7.1d 生成表）——官方合法形态**零回归**、越界/非法形态**零误放行**；源侧与候选侧共用同一个 `_consume_data_type()`；类型属性按族开放；仅 `TEXT/BLOB` 接收可选 M，TINY/MEDIUM/LONG 具名变体不得接收 |
| **M-5** | **门禁守恒（BLOCK-11-05）**：M 组变异断言全部通过——正确候选不得误杀，定向变异（丢约束 / 改类型 / 改 kind / 改索引名 / 改键列 / 丢前缀 / 丢或凭空多出 `USING` / 增删定义项 / 换表名 / 换序 / 抹掉分区）全部必须被拒 |
| **M-6** | **列属性端到端（BLOCK-11-06）**：`COLUMN_FORMAT` / `ENGINE_ATTRIBUTE` 断言到**最终 `Create` + 无 E999**，不得只在规划层验证就宣称"已恢复"；`grep` 确认 `_COLUMN_FORMAT_ENUM` 不含 `COMPRESSED` |
| **M-7** | **manifest 唯一真源（BLOCK-11-07）**：§7.1 唯一 marker 区段由 `python docs/evidence/v1.6.2.2/manifest_doc.py` 生成；marker 出现次数恰好为 1 且区段精确相等，否则失败 |
| **M-8** | **FULLTEXT/SPATIAL 入口一致（MAJOR-11-01）**：`_is_index_item()` 与 `_consume_index_definition()` 共用 `_index_lead()`；裸 `FULLTEXT (a)` / `SPATIAL (g)` 必须恢复；`` `fulltext` `` / `` `spatial` `` 反引号列名必须仍走列定义消费器 |
| **M-9** | **capability profile（MAJOR-11-02）**：每条 SQL 完整匹配单一 profile，禁止跨 profile 拼接；`NEW_SECONDARY`（`TDSQL_PARTITION BY`）登记于 `_TAIL_PROFILES_UNPROVEN` 且**不参与匹配**，对应用例按 `unsupported_unproven` 断言失败关闭 |
| **M-10** | **规模数字自动生成**：§3.4 的行数、函数清单、唯一性检查由 `python docs/evidence/v1.6.2.2/codestat.py <固定基线> <目标>` 生成；唯一性检查必须报告模块级函数/常量无重复定义 |
| **M-11** | **照图施工可复现**：design 模式从不可变 baseline blob 应用 Rev.P 全部 stable-id 施工块，得到唯一目标；以四文件 bundle 的 `normalized_utf8_sha256` 校验，不再声称跨平台“文件逐字节相同” |
| **M-12** | **三版一致**：manifest 全量（用例 + 变异 + 模糊）在 **sqlglot 29.0.0 / 30.14.0 / 30.17.0** 上结果逐条一致 |
| **N-1** | **Rev.O 已升级位置模型**：`_collect_executable_comments(sql,toks)` 返回原始字符 span + `left_idx/right_idx`；仅完整 atom 边界可合并，owner token 不再作为真源。R12-EC 与 R13-EC 全部走真实 `SQLParser.parse()` |
| **N-2** | **语句终止符（BLOCK-12-02）**：`_plan_recovery()` / `_blank_spans()` / `_spans_only_diff()` 三处调用点全部接收 `sql.strip()`（**未删分号**）；0/1 个分号恢复，≥2 个、分号后接第二条语句端到端**不得恢复**；`grep` 确认恢复链不再引用 `sql_clean` |
| **N-3** | **类型多产生式**：保留 FLOAT/DEC/NCHAR/NVARCHAR/CHARACTER 既有闭环；`TEXT/BLOB(M)` 覆盖跨 65535 至 2^32−1 边界，六种具名容量变体仅允许无参；SERIAL 改为 KFN-5，不再宣称可恢复；多 token 类型按实际 token 切分最长匹配 |
| **N-4** | **KFN-4 具名登记**：`INT SIGNED`、`VARCHAR(n) BINARY`、`NATIONAL CHAR/VARCHAR`、无名 `CONSTRAINT PRIMARY KEY`、终止符后普通注释——规划器**具名接受**、候选失败关闭，manifest 中分类为 `pos_known`，**不得藏在普通 `plan=False` 里** |
| **N-5** | **CreateShape 守恒（BLOCK-12-04）**：指纹含 `head`（全限定名 + TEMPORARY + IF NOT EXISTS）/ `definitions` / `tail`（本地表选项 + 分区细节）；M-CREATE / M-TAIL / M-PARTITION 三组单点变异**全部被拒**，正确候选零误杀 |
| **N-6** | **source-only approved transform 显式化**：`_SOURCE_ONLY_TAIL_TAGS` 存在且只含方言 atom 与可执行注释分区；分区选项掩码不参与候选比较；`grep` 确认没有第二处"悄悄忽略"的分支 |
| **N-7** | **具名 PRIMARY 约束**：支持自身 COMMENT 并比较 symbol/键列/USING；`CONSTRAINT … UNIQUE` 按用户冻结决策规划层失败关闭，不能“顺带恢复” |
| **N-8** | **候选侧不得用整句 `node.sql()` 做 tail 比较**：sqlglot 遇到未知表选项会把**整组**属性包进 `WITH ( … )`（实测），必须逐属性渲染。`grep` 确认 `_ast_tail_shape()` 走逐属性路径 |
| **N-9** | **字符集拼写跨版本兼容**：表级和列级均复用 `_charset_kw_end()`；只有字符 family 可接 CHARACTER SET/COLLATE，非字符族反例规划层拒绝 |
| **N-10** | **证据双模式**：`run_all.py --mode design` 与 `--mode implementation` 在默认 Windows 终端均可执行；输出 ASCII，发布模式断言依赖 pin 与运行时版本 |
| **N-11** | **哈希口径**：只把 LF 规范化 UTF-8 哈希称为 `normalized_utf8_sha256`；implementation 模式直接校验当前产品文件，不得再次套“改动前”补丁 |
| **N-12** | **施工指令唯一**：`grep` 确认准出表与附录 B 中不存在 `_TYPE_SPEC` / `_TAIL_EDGES` 等陈旧锚点（历史章节的说明性引用除外，且必须带"Rev.X 历史"提示）；附录 B 第 12/18 条已合并，第 9/13 条已改为"主干只作 `baseline_observation`" |
| **O-1** | **R054 语义闭环（Rev.P 覆盖 Rev.O 供数目的地）**：列级 UNIQUE、表级 UNIQUE 及其混合全部精确进入 `unique_constraints`；每个索引含/不含 shardkey 的 R054 双向断言通过；CONSTRAINT UNIQUE=KFN-6；SERIAL=KFN-5 |
| **O-2** | **索引消费者零次生灾害（Rev.P 覆盖 Rev.O）**：新增 UNIQUE 不得进入 `parsed.indexes/index_definitions`；R077/R061 等 legacy 消费者逐键等于基线，只有 R054 专属助手消费隔离通道 |
| **O-3** | **atom boundary**：ENGINE 与 `= value` 内部、TDSQL_DISTRIBUTED 的 BY/HASH/括号内部两个生产反例以及 R13-EC 全矩阵均规划层拒绝；合法边界不误杀 |
| **O-4** | **类型与 family**：本报告 9 类形态全部为 pos 或具名 KFN；TEXT/BLOB M 边界恢复；INT/DATE/JSON 字符属性失败关闭；列级 CHARACTER SET 在三版结果有 manifest 明示值 |
| **O-5** | **具名 PRIMARY/列 COMMENT**：具名 PRIMARY 自身 COMMENT 组合最终 Create、columns/PK/规则集合正确；删除/增加列 COMMENT 的候选门禁拒绝，评论文本 ParsedSQL oracle 精确一致 |
| **O-6** | **证据工程**：默认 CP936/PowerShell 命令不因 Unicode 输出失败；design/implementation 两模式均可复现；30.14.0 pin 三重断言；三版隔离矩阵可运行 |
| **O-7** | **证据无静默退化**：mutation parse error 不得 continue；生成 marker 唯一且精确；collect 公式正确；KFN 必须 plan 可达并核对编号 |
| **O-8** | **开发准出顺序**：先 design 模式复现期望目标，再施工，再 implementation 模式验证当前提交，最后跑专项、全量、verify_rules、两份生产 fixture 与全语料漂移；任一步失败不得进入发布 |
| **P-1** | **隔离通道完整性**：`unique_constraints_complete=True` 才允许 R054 只读结构化通道；完整路径中 raw 不得混入，不完整路径保留既有 raw 回退；UNIQUE 不得出现在 legacy 列表 |
| **P-2** | **R077 冻结**：`distributed.py` 的 diff 只能落在 `_iter_unique_indexes()`；R077 类、正则、OR 判定逐字/哈希保持 baseline，五项冻结测试与全规则差分零漂移 |
| **P-3** | **全路径 KFN**：CONSTRAINT UNIQUE=KFN-6、SERIAL=KFN-5；native Create、Command、except 三路最终均有 E999，且字面量/标识符 decoy 不误命中 |
| **P-4** | **设计证据真实可执行**：stable-id 必须全部且只被消费一次；从固定四文件 blob 重建；bundle/hash/pin/生成区段/三版 manifest/发布版专项与全量全部非零即阻断 |
| **P-5** | **实施态不伪绿**：产品尚未施工时 `--mode implementation --matrix` 必须返回 3 并输出 `STATUS NOT_IMPLEMENTED`；施工后才允许进入与 design 相同的语义矩阵及准出链 |

### 7.4 证据面资产与复现命令（Rev.P 双模式）

Rev.N 资产已在本版原目录、原文件名上升级为 Rev.P，未复制第二套真源。设计评审以 design 模式
复现目标；产品施工完成后以 implementation 模式验证当前提交。两者验证对象不同，不能互相替代。

#### 7.4.1 评审设计模式

```bash
python docs/evidence/v1.6.2.2/run_all.py --mode design --matrix
```

design 模式必须：

1. 从文档登记的不可变 baseline commit/blob 读取 parser，不读取调用时工作树 parser 作前镜像；
2. 在临时目录应用 Rev.P 明示的全部 stable-id 动作；每个 before 锚点出现次数恰好为 1，未知或漏消费 marker 立即失败；
3. 计算并核对四个目标文件的规范化哈希与 bundle `design_bundle_normalized_sha256`；
4. 校验 `requirements.txt`/`pyproject.toml` 的目标 pin，并在隔离环境运行三版矩阵；
5. 执行 manifest、mutation、fuzz、生成区段精确比对和静态唯一性检查；
6. 输出仅 ASCII；任一步失败返回非零。

#### 7.4.2 施工实现模式

```bash
python docs/evidence/v1.6.2.2/run_all.py --mode implementation --matrix
```

implementation 模式必须直接读取**当前提交**的 parser 与依赖声明，禁止再次执行 before→after
替换。它先核对当前产品文件与 design 目标的规范化哈希，再在当前实现上运行同一套语义 oracle；
若哈希不等，立即输出 ASCII `STATUS NOT_IMPLEMENTED` 并返回码 3，不能把基线产品伪装成通过；
哈希相等后才运行同一矩阵，最后由仓库命令继续跑专项、全量、verify_rules、fixture 和语料漂移。

#### 7.4.3 资产职责

| 文件 | Rev.P 职责 |
|---|---|
| `parser_recovery_manifest.py` | 唯一 case + oracle + expected_by_version；修订三条旧期望并新增 R14 四组 |
| `test_parser_recovery_manifest.py` | 通用 oracle 执行器；无 cid 特判；mutation parse error 不得静默跳过 |
| `manifest_doc.py` | 生成并精确替换唯一 marker 区段；同时输出 cases/suites/assertions/collect 四种计数 |
| `codestat.py` | 读取固定 baseline 与 design/implementation 目标，生成规模和重复定义检查 |
| `rebuild_from_design.py` | 仅供 design 模式；显式块清单/名称，不再依赖“前 N 个代码块”这种脆弱顺序 |
| `run_all.py` | 双模式、ASCII 输出、版本/pin/hash/marker/matrix 统一编排 |
| `README.md` | 两模式命令、依赖准备、失败码和 Windows 行为 |

#### 7.4.4 计数、版本与结果记录

- collect 数 = `len(CASES) + len(MUTATION_SUITES) + 1`；
- mutation assertions 单独统计；候选不可解析要么失败，要么是 manifest 明示分类；
- 发布 pin 30.14.0 的所有 oracle 必须全绿；29.0.0/30.17.0 如有差异必须由
  `expected_by_version` 明示理由，禁止测试代码 `continue`；
- Rev.P 首次运行后，把真实 cases/suites/assertions/collect 数和三版结果写回唯一自动生成区段；
- 全量 passed/skipped 数只记录本次输出，不成为跨环境固定门槛；失败必须为 0。

#### 7.4.5 Rev.P 本次设计态实测（2026-08-27）

| 检查 | 实测结果 |
|---|---|
| design 三版 manifest | 29.0.0 / 30.14.0 / 30.17.0 各 `524 passed` |
| 发布 pin 冻结专项 | `71 passed`（R077/R054、方言 fallback、R061） |
| 发布 pin 全量 | `1384 passed`，0 failed |
| 生成与身份 | manifest 区段、codestat 区段、bundle hash 全部一致 |
| implementation 施工前状态 | `STATUS NOT_IMPLEMENTED`，退出码 3，current bundle 与 design bundle 不同 |

本表证明 Rev.P 设计目标可机械重建且没有破坏现有测试；它不替代产品施工后必须再次执行的
implementation、fixture、verify_rules 与全语料漂移证据。

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
| **列级 UNIQUE 进入全部索引消费者后出现次生告警** | **中** | 不只测 R054；对所有 `parsed.indexes` 消费者做精确规则集合差分，隐式索引名按列名。任何非规范预期变化都阻断发布 |
| **修列级后关闭 raw 回退，反而漏掉表级 UNIQUE** | **高→由 3c 阻断** | 发布 pin 下表级节点为 `UniqueColumnConstraint(this=exp.Schema)`；同步替换旧提取器，R13-UQ 用“列级在前 + 两个表级且最后一个违规”证明所有条目齐全，不以首个 R054 命中代替结构断言 |
| **可执行注释被从 compound atom 内部搬移** | **高→由 O-3 阻断** | 使用原始字符 span + 左右 token gap + 完整 atom boundary；owner token 不再作真源；R13-EC 对每个内部 gap 生成反例 |
| **SERIAL 被当普通类型放行而漏 UNIQUE/NOT NULL/AUTO_INCREMENT** | **高→由 KFN-5 阻断** | plan 具名接受但 candidate gate 强制失败；只有完整展开全部 ParsedSQL 语义并经用户批准后才能解除 |
| **非字符类型字符属性被 sqlglot 宽松 AST 带过** | **高→由 family 阻断** | `_consume_column_constraints(..., family)` 在规划层拒绝 INT/DATE/JSON 的 CHARACTER SET/COLLATE，不依赖候选 AST |
| **证据只测试临时重建物，不测试施工提交** | **高→由双模式阻断** | design 模式验证期望目标，implementation 模式直接测试当前提交；两者规范化哈希一致且发布 pin 三重一致 |

**回滚**：产品提交仍应保持单一、可 `git revert` 的原子提交；范围为解析器、依赖声明和版本号文件。
证据资产与产品变更放在同一发布提交或紧邻的可追溯提交中。回滚前必须确认 v1.6.2.1 已知的
`_TDSQL_DIALECT_RE` 静默破坏问题会随之恢复，不能把“可回滚”误解为“回滚无风险”。
无数据迁移、无配置变更、无接口变更、无前端联动。

---

## 9. Rev.P 施工检查单（逐项打勾）

- [ ] **C-1 范围**：产品只改 parser、`distributed.py::_iter_unique_indexes()`、两份依赖声明和两份版本号；R077 类及其他规则文件零改动。证据资产在原 `docs/evidence/v1.6.2.2/` 升级，不新建第二套真源。
- [ ] **C-2 Rev.N 基础机制**：删除 `_TDSQL_DIALECT_RE`，两条恢复入口统一 `_plan_recovery()`，`sql_recover` 保留真实终止分号，成功后同时重绑 `ast`/`parsed.ast`。
- [ ] **C-3 R054 隔离供数**：新增 `unique_constraints` 与 `unique_constraints_complete`；列级/表级 UNIQUE 只进入隔离列表，混合顺序下逐项齐全；legacy `indexes/index_definitions` 的 UNIQUE 数始终为 0。
- [ ] **C-4 支持域闭合**：CONSTRAINT UNIQUE 全路径 source preflight 命中 KFN-6；SERIAL 与 SERIAL DEFAULT VALUE 全路径命中 KFN-5；native Create、Command、except 最终均保留 E999。
- [ ] **C-5 索引消费者**：R054 专属 `_iter_unique_indexes()` 是新通道唯一消费者；完整路径只读结构化 UNIQUE、不混 raw；不完整路径维持 raw 回退；R077/R061 及其他 legacy 消费者精确差分零漂移。
- [ ] **C-6 类型产生式**：最长 3/2/1 token（含 token 文本自带空格）匹配；仅 TEXT/BLOB M 覆盖 65535 至 2^32−1，六种具名容量变体带参拒绝；TypeShape 含 family/KFN。
- [ ] **C-7 列属性族**：`_consume_column_constraints(..., family)`；列/表级共用 `_charset_kw_end()`；非字符族 CHARACTER SET/COLLATE 全拒绝。
- [ ] **C-8 可执行注释定位**：保存原始半开 span 与左右 token gap；不再以 owner_idx 判位置；终止分号之后的可执行注释拒绝。
- [ ] **C-9 atom 边界**：注释只有在完整 atom 之间才能合并；ENGINE、CHARACTER SET、shardkey、DIST、PARTITION 内部 gap 全拒绝。
- [ ] **C-10 具名 PRIMARY**：source 收集自身 COMMENT span；candidate 恰好一个 PrimaryKey，只允许有来源凭据的 Comment option；symbol/键列/USING 守恒。
- [ ] **C-11 列 COMMENT**：COMMENT 从 ignored 集合移除；CreateShape 比存在性；ParsedSQL oracle 比实际文本；R029 不漂移。
- [ ] **C-12 KFN**：source preflight 与 `ParsedSQL.known_fidelity_failures` 编号稳定；存在恢复计划的路径还须由 plan 携带同一 KFN；candidate gate 最先拒绝；每条 pos_known 最终 E999。
- [ ] **C-13 span 安全**：所有掩码等长、保留换行、越界改写字符为 0；raw_sql 逐字符不变。
- [ ] **C-14 结构守恒**：head/definitions/tail 三面比较；所有规则消费字段有门禁或明确 approved transform，不存在无消费者证明的 ignored 项。
- [ ] **C-15 依赖 pin**：`requirements.txt`、`pyproject.toml`、运行时版本均为 `sqlglot==30.14.0`；打包 wheel 版本记录在提交说明。
- [ ] **C-16 design 证据**：`run_all.py --mode design --matrix` 默认 Windows 环境全绿；基线来自不可变 blob；输出 ASCII；目标规范化哈希稳定。
- [ ] **C-17 implementation 证据**：施工后运行 `--mode implementation --matrix`；直接测试当前提交，不再次应用 before 补丁。
- [ ] **C-18 manifest**：R13 五组与 R14-UQ/KFN-CU/KFN-SE/KFN-DECOY 四组齐全；N-01、R12-TY-23、R12-CN-08 已改新期望；每个目标 pos 有 ParsedSQL/RuleChecker oracle；测试文件无 cid 特判。
- [ ] **C-19 mutation**：候选解析异常不 `continue`；unparseable 如允许必须是 manifest 明示分类且进入统计。
- [ ] **C-20 生成器**：BEGIN/END marker 全文各恰好一次并精确相等；collect=`cases+suites+1`；assertion 数单列。
- [ ] **C-21 三版矩阵**：29.0.0/30.14.0/30.17.0 使用隔离环境；差异只能来自 `expected_by_version`，发布 pin 全绿。
- [ ] **C-22 专项/全量**：R054、方言 fallback、R061、parser 专项和 `pytest tests/` 全部 0 failed；实际 passed/skipped 只记录、不硬编码。
- [ ] **C-23 规则覆盖**：`verify_rules.py` 与基线失败项同名同因；不得新增失败或未覆盖规则。
- [ ] **C-24 生产证据**：两份 fixture 原文读取、实例类型正确、规则集合精确相等；全语料和生产 14 表逐键漂移只有批准变化。
- [ ] **C-25 版本**：`VERSION`、`APP_VERSION`、`APP_DESCRIPTION` 更新为 1.6.2.2；导入/编译自检通过。
- [ ] **C-26 静态检查**：无重复函数/常量、无未知 token 跳过、无跨语义边界 SQL 改写正则、无陈旧 `_TYPE_SPEC/_TAIL_EDGES/owner_idx` 施工口径。
- [ ] **C-27 提交**：产品与证据形成可追溯原子提交；提交说明列出 design/implementation bundle 哈希、三版结果、全量结果和已批准 KFN；产品施工前 implementation 返回 `NOT_IMPLEMENTED/3` 才是正确状态。

---

## 附录 A：实测证据清单（历史版本至 Rev.P 设计阶段）

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

### A.13 Rev.N 新增证据（第十二轮整改）

| 编号 | 证据 | 结论 |
|---|---|---|
| **A-165** | 复现 O 第十二轮 8 条发现 | **全部复现，无异议条目**。BLOCK-12-01/02/04 是真实"吞错"，BLOCK-12-03 与 MAJOR-12-01 把官方合法语法留在 E999 路径 |
| **A-166** | 可执行注释 owner token 实测（三版） | 注释挂在它**前面**那个主 token 上；四类越位反例 Rev.M 全部 `plan=True → Create`，Rev.N 全部 `plan=REJECT` |
| **A-167** | 可执行注释并入 atom 流后的 profile 判定 | `[…, BROADCAST_SENTINEL, PARTITION]` 与 `[…, PARTITION, PARTITION]` 无 profile → 拒绝；`[…, HASH_SHARDKEY, PARTITION]`、`[…, DIST, PARTITION]`、`[PARTITION]` 正常恢复 |
| **A-168** | `mysqldump` 无表选项形态的边界 | 注释 owner 恰为定义列表右括号（`owner_idx == close_idx`），故位置判据必须是 `>=` 而非 `>`；写成 `>` 会误杀最常见的一种合法形态（Rev.N 自查发现） |
| **A-169** | 终止分号集成路径实测 | `_plan_recovery(原串)` 对 `;;`/`;;;`/`; ;` 本就 REJECT，但 Rev.M 传入的是 `rstrip(";")` 后的串 → 端到端全部 `Create`；Rev.N 改传 `sql_recover` 后全部 `NoneType + E999` |
| **A-170** | 带尾分号的掩码串可解析性 | `CREATE TABLE … ;` 与 `CREATE TABLE … ) ;` 在三版上均返回 `exp.Create`，故保留合法终止符不影响恢复 |
| **A-171** | 类型产生式双向实测 | `FLOAT(0)/(24)/(25)/(53)` 恢复、`FLOAT(54)/(−1)` 拒绝、`FLOAT(10,2)` 走第二条产生式；`DEC`/`NCHAR`/`NVARCHAR`/`CHARACTER`/`CHARACTER VARYING`/`SERIAL` 全部恢复；`SET` 64 成员恢复、65 成员拒绝 |
| **A-172** | 词法实测（供施工核对） | `CHARACTER VARYING` 与 `DOUBLE PRECISION` 是**单个** token；`NATIONAL CHAR` 是**两个** token；`.2` 被切成 `DOT` + `NUMBER`；`DEFAULT .2` 回生成为 `0.2` |
| **A-173** | CreateShape 顶层/表尾变异（13 种） | Rev.M 门禁**全部返回 True**；Rev.N **全部拒绝**，正确候选零误杀 |
| **A-174** | 候选 tail 提取方式的取舍实测 | 直接用 `node.sql()` 整句：sqlglot 遇到 `shardkey=` / `STATS_PERSISTENT=` 会把**整组**属性包进 `WITH ( … )`，tail 扫描失败、合法正例被误判；改为**逐属性渲染**后两侧指纹逐字一致 |
| **A-175** | 具名 PRIMARY 约束实测 | `CONSTRAINT pk PRIMARY KEY(id)` 在候选里是 `exp.Constraint(this=symbol, expressions=[PrimaryKey])`；解包并比较 symbol 后，与 HASH 方言 / BROADCAST / UNIQUE COMMENT 组合全部恢复；无名形态三版 ParseError → KFN-4 |
| **A-176** | 字符集拼写跨版本词法差异（Rev.N 自查） | `CHARACTER SET` 在 30.17.0 上被拆成 `CHAR` + `SET`，29.0.0 / 30.14.0 是单 token；Rev.M 只认 token 类型，该拼写在 30.17.0 上失败关闭——**Rev.M 就已存在的潜伏缺陷**，本轮一并修掉并补 6 例锁定 |
| **A-177** | manifest 全量（501 用例 + 53 变异断言 + 6000 模糊） | **29.0.0 / 30.14.0 / 30.17.0 三版全绿**；`pytest --collect-only -q` 收集 **511** 项、**零 skip** |
| **A-178** | Rev.N 爆炸半径 | 全语料 + 生产 14 表共 **201 条语句逐键零漂移**；两份 fixture 精确相等；`verify_rules.py` 119/107/0/**3**（同名同因）；旧套件 **1355 passed / 0 failed**，含准出套件合计 **1866 passed / 0 failed** |
| **A-179** | 证据面可执行性（BLOCK-12-05） | 六个资产已提交到 `docs/evidence/v1.6.2.2/`；`python docs/evidence/v1.6.2.2/run_all.py` 在**当前提交上**即可执行：重建哈希校验通过、511 项全绿、两个生成器输出与正文逐字一致 |

### A.14 Rev.O 设计阶段反证与待施工证据（第十三轮整改）

> 本节只登记第十三轮已复现的**失败基线**和 Rev.O 要建立的证据义务，不冒充实现完成后的
> 通过记录。A-180～A-187 必须由新版 runner 在 design 模式固化；施工后再由 implementation
> 模式对当前提交重跑并登记真实数量、哈希和结果，禁止手填“全绿”。

| 编号 | 已确认问题 / 证据义务 | Rev.O 判据 |
|---|---|---|
| **A-180** | 列级 `UNIQUE` 在 sqlglot AST 中属于列约束，现有索引提取未纳入 R054；进一步用发布 pin 30.14.0 复现表级 UNIQUE 的 `this=exp.Schema`，现有 `_parse_unique_constraint()` 返回 `{}`。若只补列级，`_iter_unique_indexes.seen=True` 会关闭 raw 回退并新增表级漏报 | design 模式必须证明列级与表级 UNIQUE 均完整提取，混合顺序下逐项触发 R054；表级提取不得依赖 raw 回退；具名 UNIQUE 必须 `REJECT_PLAN`；`SERIAL` 两种形态必须命中 KFN-5 并保持 E999 |
| **A-181** | 仅凭 `token.comments` owner 无法证明可执行注释的原文位置，且合法表尾 atom 内部插入注释可能被错误并入 atom 流 | 覆盖定义内、CREATE 前、分号后、完整 atom 边界及 `HASH/SHARDKEY/PARTITION` atom 内部；仅完整边界可进入 payload 验证，内部位置全部失败关闭 |
| **A-182** | 类型表存在最长匹配、族属性和“解析器可表达但 sqlglot 不保真”三类风险；Rev.O 自检又复现 sqlglot 把 `NATIONAL CHARACTER VARYING` 切成 `NATIONAL` + `CHARACTER VARYING` 两 token，且只有裸 `TEXT/BLOB` 允许 `(M)` | 覆盖三词/二词（token 文本可自带空格）/一词最长匹配、TEXT/BLOB 参数边界、六种具名 TINY/MEDIUM/LONG 变体带参反例、字符族专属 `CHARSET/COLLATE/ASCII/UNICODE/BINARY`、KFN-5；所有非法族属性和空间/SERIAL 不保真形态均不得恢复 |
| **A-183** | 具名 PRIMARY 外层 COMMENT 与列 COMMENT 在结构守恒中存在漏比较风险 | 正例保留 COMMENT；删除、凭空新增及 `CONSTRAINT pk PRIMARY KEY` 外层 COMMENT 丢失必须被门禁拒绝；评论文本允许 parser 归一，但“是否存在”必须守恒 |
| **A-184** | Rev.N runner 只验证临时重建物、Windows 默认代码页不稳、哈希口径和正文选择可能漂移；Rev.O 修订后在默认 PowerShell 复跑旧 runner，确在输出非 ASCII 符号时以 `UnicodeEncodeError: gbk` 中断 | `--mode design --matrix` 与 `--mode implementation --matrix` 均须在默认 Windows/PowerShell 非交互成功；使用稳定 marker、LF 规范化 UTF-8 SHA256、固定完整 baseline commit，并断言依赖 pin |
| **A-185** | 历史 501 case / 511 item 未覆盖上述语义，不能继续作为 Rev.O 准出数字；旧资产对 Rev.O 目标原型实跑为 `3 failed, 508 passed`，三处正是已废止的 N-01、SERIAL pos 与 CONSTRAINT UNIQUE pos 期望 | 用例数、pytest collect 数、规则集合与哈希全部由生成器从新版唯一真源重算；A 评审设计时先审判据，开发验收时再填真实结果，不得沿用历史计数；这三个旧失败必须改期望，不得为追求“511 全绿”回退 Rev.O 语义 |
| **A-186** | 按当前正文施工块从固定基线临时重建完整 Rev.O parser，在 **sqlglot 30.14.0** `py_compile` 通过；“1 列级 + 2 表级”产出三个 UNIQUE，最后一个不含 shardkey 时 R054 命中；合法前缀 `id(10)` 提取为列 id；CONSTRAINT UNIQUE 与 SERIAL 保持 E999 | 这是设计目标的可施工性探针，必须迁入新版 stable-id design runner；它不等于工作树产品已经开发完成 |
| **A-187** | 30.14.0 定向探针：可执行分区位于完整 atom 后恢复，插在 ENGINE / DIST 内部均 `plan=None`；具名 PRIMARY 自身 COMMENT 恢复；无名 PRIMARY 与分号后普通注释分别命中具名 KFN；删除列 COMMENT 的候选门禁拒绝 | 所有判据须转为 manifest oracle，并在 implementation 模式对真实提交重复；只保留本表文字不构成准出 |

### A.15 Rev.P 设计阶段独立复现与证据义务（第十四轮整改）

| 编号 | 独立复现 / 设计证据 | Rev.P 结论 |
|---|---|---|
| **A-188** | 按 Rev.O 把列级/表级 UNIQUE 写入 legacy `indexes/index_definitions` 后，5 项冻结测试失败且 7 条规则结果漂移；移除该供数后漂移消失 | BLOCK-14-01 因果链成立。不得收紧已冻结的 R077；必须用隔离 `unique_constraints` 通道，并只让 R054 专属助手消费 |
| **A-189** | CONSTRAINT UNIQUE 在发布 pin 下可直接形成 native Create，旧 KFN 仅存在于 RecoveryPlan，故不会触发；Command 与 except 也存在无统一终结的路径 | BLOCK-14-02 成立。KFN-6/KFN-5 必须由 source preflight 覆盖三条控制流，并统一形成 E999 |
| **A-190** | 从固定 commit 的 parser、distributed、requirements、pyproject 四个 blob 应用 Rev.P stable-id 块，重建 bundle 哈希为 `3cd8756a327f7c18401fd174ebc19148bc01aea3110faafa12ba312db3914c38`，parser/distributed 均通过 `py_compile` | 证明设计块可机械施工，但不等于工作树产品已开发；完整准出仍以附录 C 的 design/implementation 分阶段命令为准 |
| **A-191** | `run_all.py --mode design --matrix` 实跑：三版 manifest 各 524 passed，发布版冻结专项 71 passed、全量 1384 passed；manifest/codestat/hash 均一致。implementation 实跑返回 `NOT_IMPLEMENTED/3` | BLOCK-14-03 的设计态证据已落成实物；产品施工准出仍未完成，A 第十五轮只能评审方案，不得据此批准产品发布 |
| **A-192** | Rev.P 收尾审计发现列级 helper 原先无法区分“无 UNIQUE”和“看见但无法表达”；重复 `UNIQUE UNIQUE` 在 29.0.0 与 30.x 的 AST 形态不同，旧草案会在 29.0.0 误标 complete | helper 改为三态，异常结构统一 `UNIQUE_SEMANTICS_INCOMPLETE` + E999；R14-UQ-04 三版通过，证明完整性标记不再依赖单一 sqlglot AST 形态 |

---

## 附录 C：Rev.P 证据资产契约

Rev.P 已在**同一目录、同一文件名**上升级 Rev.N 证据资产，没有复制 `evidence_rev_p/` 等第二
真源。本附录与 `docs/evidence/v1.6.2.2/README.md`、runner 共同定义设计态和实施态的验证对象。

### C.1 两条唯一命令

```bash
python docs/evidence/v1.6.2.2/run_all.py --mode design --matrix
python docs/evidence/v1.6.2.2/run_all.py --mode implementation --matrix
```

第一条用于从固定 baseline blob 复现设计目标；第二条用于验证当前提交产品代码。两条都必须在
默认 Windows/PowerShell 环境运行，不能要求操作者先设置 `PYTHONUTF8`。

### C.2 固定身份与哈希字段

证据 README 与 runner 必须共同登记：

```text
baseline_commit = 03216b788412caa476bba49b9d8524de80919bf4
target_paths = backend/engine/parser/parser_legacy.py
               backend/engine/rules/distributed.py
               requirements.txt
               pyproject.toml
release_sqlglot = 30.14.0
design_bundle_normalized_sha256 = 3cd8756a327f7c18401fd174ebc19148bc01aea3110faafa12ba312db3914c38
parser_normalized_utf8_sha256 = 185f43fcf835508f3ca0b52094cdf324cea4bb5b050df7fdade2aaed3219af9c
distributed_normalized_utf8_sha256 = 5b1884bf0a08f44f2287375cec9a2e504b80ae80cb0fe4f04aedcf81701ad0f0
requirements_normalized_utf8_sha256 = 36916e67bba0c05eaea18a64c80f63e82412b5233a3b9569a0293838d4c6a073
pyproject_normalized_utf8_sha256 = 60785ef0b35ed49fd29d174530b8a6b380777473a948f0f9306f5be5ac3ec98b
```

单文件 `normalized_utf8_sha256` 的定义：UTF-8 解码 → CRLF/CR 归一为 LF → UTF-8 编码 →
SHA256。bundle 按目标相对路径字典序，依次输入 `path + NUL + normalized_bytes + NUL` 后取 SHA256。
以上值由当前 stable-id 施工块从固定 commit 重建后实算，不是人工占位符。若需要真实落盘字节哈希，
字段名必须是 `raw_file_sha256` 并独立记录。

### C.3 重建块契约

`rebuild_from_design.py` 不再按“前 12 个 python 代码块”取值。每个施工块使用正文中已经落下的
稳定 HTML marker，marker 之外的示例代码不参与施工：

```text
<!-- BEGIN CODE: <stable-id> -->
<紧随其后的唯一 fenced code body>
<!-- END CODE: <stable-id> -->
```

runner 内的动作清单必须逐项固定，不允许按出现顺序猜测：

| 动作 | stable-id / 锚点 | 后置断言 |
|---|---|---|
| `INSERT_AFTER` | `IMPORT-TOKENTYPE-AFTER`；锚点 `from sqlglot.errors import SqlglotError` | import 恰好 1 次 |
| `REPLACE_MODULE` | `RECOVERY-MODULE-AFTER`；替换基线 `_TDSQL_DIALECT_RE` 定义及其专属注释 | 旧常量 0 次；模块级新增函数/常量唯一 |
| `REPLACE_PAIR` | `COMMAND-RETRY`、`EXCEPT-RETRY`、`INDEX-TYPE`、`SEMICOLON` 的 BEFORE/AFTER | 每个 before：施工前 1、施工后 0；after：施工后 1 |
| `REPLACE_PAIR` | `PARSED-UNIQUE-FIELDS`、`TABLE-UNIQUE`、`COLUMN-UNIQUE-METHOD` 的 BEFORE/AFTER | 新字段与 helper 各唯一；表级只读 `exp.Schema`；UNIQUE 输出目的地为隔离通道 |
| `REPLACE_PAIR` | `UNIQUE-INIT`、`COLUMN-UNIQUE-WIRE`、`TABLE-UNIQUE-WIRE`、`UNIQUE-COMPLETE` 的 BEFORE/AFTER | 初始化、列/表接线与完整性闭合各恰好一次；legacy 列表不接收 UNIQUE |
| `REPLACE_PAIR` | `SOURCE-PREFLIGHT`、`PARSE-PREFLIGHT` 的 BEFORE/AFTER | source 词法预检位于所有解析路径之前；结果接入 ParsedSQL |
| `REPLACE_LAST_WITHIN_METHOD` | `PARSE-KFN-FINALIZE-BEFORE/AFTER`；限定 `SQLParser.parse()` 最后返回点 | 命中 KFN 时统一设置 E999；不得误替换其他方法同文片段 |
| `ASSERT_CONTAINED` | `KFN-GATE-ASSERT-CONTAINED` | 该片段已包含在 `RECOVERY-MODULE-AFTER` 中，**只校验一次，不二次插入** |
| `REPLACE_PAIR` | `R054-UNIQUE-ITER-BEFORE/AFTER`（目标为 `distributed.py`） | 仅 `_iter_unique_indexes()` 变化；完整隔离通道优先，不完整才走 legacy/raw；R077 类逐字不动 |
| `REPLACE_PAIR` | `REQUIREMENTS-SQLGLOT-BEFORE/AFTER`、`PYPROJECT-SQLGLOT-BEFORE/AFTER` | 两个声明文件施工后均为精确 pin，旧范围声明为 0 |

每个 marker id 在文档中必须各出现 BEGIN/END 恰好一次，正文中登记的动作全部被消费，未知 marker
或漏消费均非零退出。这样在正文前增加示例代码不会改变施工结果，也不会把解释性 KFN 片段重复
插入产品代码。

### C.4 Rev.P 评审交接状态

本次 Codex 只修订详细设计说明书与设计证据，**未改产品代码，也未把 design 目标冒充成已完成
开发**。交付 A 第十五轮评审前，`--mode design --matrix` 已从固定四文件 blob 重建并实测通过：
三版各 524 passed、发布版冻结专项 71 passed、全量 1384 passed。当前产品仍是施工前基线，
`--mode implementation --matrix` 已按契约返回 `STATUS NOT_IMPLEMENTED`/退出码 3。A 通过后才进入产品施工；施工提交必须把 implementation
模式、专项、全量、规则覆盖、fixture 与语料漂移全部跑绿。

## 附录 B：历史施工要点与 Rev.P 追加红线

> 1~42 为 Rev.N 沿革记录；凡涉及 owner token、SERIAL 可恢复、证据单模式或旧路径/计数，
> 已由 43~56 和正文 Rev.P 规范取代。施工者不得只读历史条目。

1. **本次不是"把正则改好"，是"把正则换掉"。** Rev.A 的 `_UNIQUE_IDX_COMMENT_RE` 必须**整体删除**，
   不要保留任何跨语义边界的正则改写（NG-0）。
2. **`at_def_start` 那个状态是这一版的核心，不能省。** 少了它，`CONSTRAINT x UNIQUE (...)`、
   列内联 `UNIQUE`、定义项中部的 `UNIQUE` 都会被错误地当成目标——Rev.B 就是这么被打回来的。
   **span 门禁只能自证「改动落在自己声明的范围内」，证明不了「这个范围是对的」。**
   Rev.O 进一步规定：一旦定义项起点是 `CONSTRAINT` 且内部 kind=UNIQUE，整句规划失败关闭；
   “不把它当 COMMENT 主目标”不等于“允许它随其他主目标恢复”。
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
9. **W 组的期望值必须逐路径分别推导，不能写"一律 E999"。**
   同一批输入在 v1.6.2.1 上有三种结局：`Command`（无语法错，sqlglot 不认方言）、
   `Create`（sqlglot 自己就能解析）、E999。我上一版凭印象写"一律 E999"，
   自己把自己的复评带偏了 7 例。
   ⚠️ **但推导依据是 TDSQL 官方语法与本方案契约，不是主干行为**（第九轮 BLOCK-X1、
   第十二轮 MAJOR-12-02）：主干结果只记入 `baseline_observation` 供对照，
   **不参与 pass/fail 判定**。

10. **全部消费器是一套东西，契约必须一致：`f(toks, i, stop, context...) -> (下一个下标, 结果) | (-1, None)`。**
    `_consume_data_type()` / `_consume_column_constraints(..., family)` / `_consume_index_definition()` /
    `_consume_index_key_parts()` / `_consume_table_option()` / `_consume_secondary_partition()`
    各管一段，`_plan_recovery()` 只负责
    **组合它们 + 记录目标 span**，不要在外层再写局部语法判断——那正是前七轮反复出问题的地方。
    ⚠️ 函数清单以 §3.4 由 `codestat.py` 生成的表为准，本条只讲契约。
11. **`ROW_FORMAT` 的枚举要按文本匹配，不能按 token 类型。**
    实测 `DEFAULT`→`TokenType.DEFAULT`、`FIXED`→**`TokenType.DECIMAL`**、其余→`VAR`。
    按类型写会把这两个**合法**取值误拒。用 `_is_bare_kw()` 排除引号形态即可。
12. **`PARTITION BY` 必须被完整消费，但"完整"不等于"消费到 EOF"。**（第十二轮 MAJOR-12-02
    合并原第 12/18 条的矛盾表述）分区子句本身必须逐 token 走完 `_consume_secondary_partition()`，
    不能 `break`、也不能一律拒绝——一律拒绝会让 D5（`RANGE (YEAR(dt)) (PARTITION … VALUES LESS THAN …)`）
    从主干的 `Create`/`cols=3` 降为 `Command`，那是真实的覆盖面损失，我实测过。
    但**它之后允许还有别的表尾 atom**：官方存在 `PARTITION BY … TDSQL_DISTRIBUTED BY …`
    这种分区在前的顺序，强制到 EOF 会把官方形态判成非法。
    **整条表尾的完整性由 `_scan_table_tail()` + capability profile 统一负责，不由单个消费器负责。**
13. **反例期望值由 TDSQL 规范推导；主干只作对照，不作判据。**（第十二轮 MAJOR-12-02
    更正原表述）`neg` 的硬断言是"token 规划器必须先行拒绝，且最终 AST 不得为 `Create`"。
    主干结果记入 `baseline_observation`，用 `rank(NoneType/E999)=0 < Command=1 < Create=2`
    做**单调性对照**——候选**不得比主干更成功**。
    ⚠️ 反过来"必须与主干完全相同"是错的：**主干在"无 UNIQUE COMMENT"路径上的 `Create`
    有 14 例是旧正则的假成功**，按那个写一定会把预期内的收紧误判成回归——第六、七两轮我都栽在这里。

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
18. **（并入第 12 条）** 原第 18 条与第 12 条表述相互矛盾，第十二轮 MAJOR-12-02 已指出；
    两条合并后的唯一口径见上方第 12 条。
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
    sqlglot 不把 payload 作为主 token。`mysqldump` 导出的二级分区正是这个形态。
    必须在主 token gap 内取得原始 span，再对 payload **重新词法化**验证；不得依赖
    `token.comments` 的 owner 关系推断完整 atom 位置。
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

37. **注释的"内容合法"不等于"放回原位后整句合法"。** 可执行注释里的分区单独看没问题，
    但它插在列定义里、插在 CREATE 之前、或者主 token 流已经有一条分区了——都是非法的。
    **原始字符位置与完整 atom 边界必须进入判定**，并且和主 token 流共用同一份计数和
    同一张 profile 表；`owner_idx >= close_idx` 只证明在表尾大区间，不能证明位于 atom 边界。
38. **门槛写在函数里、调用点却把输入改了，那门槛就是不存在的。** `_strip_terminal_semicolon()`
    的逻辑一直是对的，但 `parse()` 先做了 `rstrip(";")`，于是它永远看不到真实的分号数量。
    **凡是声明了判据的函数，必须核对它在真实调用链上拿到的是什么。**
39. **一个关键字可以有多条产生式，别硬塞进一条。** `FLOAT(p)` 与 `FLOAT(M,D)` 参数意义和
    范围都不同；塞进同一条 `M_D` 的结果是**同时**误拒合法下界、误收非法上界——
    一个错误表现成两个方向的失真。
40. **"官方合法但我们支持不了"必须落在具名 KFN 上。** 藏在普通 `plan=False` 里，
    它就和真正的非法语法混为一谈，下一轮谁也说不清那是设计还是缺陷。
41. **生成了却没人读的字段，等于没有。** `SourceFingerprint.tail` 在上一版生成得好好的，
    门禁却从来没读过它——13 种表尾变异全部放行。**每加一个指纹字段，必须同时加它的比较与变异用例。**
42. **候选侧的提取器不要另起炉灶。** 把候选**回生成**后送进和源侧同一个消费器，
    `CHARSET`/`CHARACTER SET`、引号风格、`=` 有无这些差异会被自动归一。
    但要**逐属性渲染**——整句 `node.sql()` 会在遇到未知表选项时把整组属性包进 `WITH ( … )`，
    反而把合法正例判成不守恒。

43. **恢复成 Create 不是完成。** 每个新增 pos 必须继续断言 ParsedSQL 和规则集合；
    R054 用例尤其要先看 `parsed.unique_constraints`、完整性标记及 legacy UNIQUE 数为 0，再看告警。
44. **列级 UNIQUE 是本期支持域。** 必须形成隔离的结构化唯一索引；CONSTRAINT UNIQUE 与 SERIAL
    则全路径失败关闭。三者不能再共享“先恢复、以后靠规则兜底”的模糊口径。
45. **SERIAL 是一组隐含约束，不是普通类型别名。** 未同时展开 UNSIGNED、NOT NULL、
    AUTO_INCREMENT、UNIQUE 前，KFN-5 不得解除。
46. **列 COMMENT 至少比较存在性。** 它是 R029 的输入；删除 COMMENT 的候选绝不能通过门禁。
    文本值由 ParsedSQL oracle 比较，不在门禁复制字符串解码器。
47. **字符属性必须受 family 约束。** sqlglot 能解析 `INT CHARACTER SET` 不代表 TDSQL 合法；
    规划器必须先拒绝，不能把候选 AST 当语法证据。
48. **证据必须分 design/implementation。** 前者从固定 blob 重建期望，后者直接测试当前提交；
    对已经施工的文件再次套 before 补丁是错误流程。
49. **哈希名称必须说真话。** LF 归一后的值只能叫 `normalized_utf8_sha256`；真实文件字节哈希
    另列。Windows 默认命令必须直接可跑，不能把 `PYTHONUTF8=1` 当口头前置条件。
50. **生成与测试不许静默退化。** mutation parse error 不得 continue；marker 必须唯一精确；
    collect 只加 suite 数、不加 suite 内 assertion 数；跨版本差异必须由 manifest 明示。
51. **新语义不能污染 legacy 输出域。** 列级/表级 UNIQUE 只进 `unique_constraints`；写入
    `indexes/index_definitions` 会激活 R077/R061 历史分支，属于发布阻断。
52. **完整性必须显式。** `unique_constraints_complete=True` 才能关闭 R054 的 raw 回退；缺少这个
    标记时，“结构列表非空”不能证明源 SQL 已被完整表达。
53. **KFN 必须覆盖解析控制流，不只覆盖 RecoveryPlan。** source preflight 要先于 native Create、
    Command 重试与 except；最终统一落到 E999，同时用字符串/标识符 decoy 证明不误杀。
54. **用户冻结项不反复开题。** ADJ-4 已永久关闭，发现 R077 次生变化时直接采用隔离通道，
    不以“二选一”重新打开收紧 R077 的未授权方案。
55. **设计态绿不等于产品已开发。** design 从固定 blob 验证可施工目标；implementation 只认当前
    产品文件。施工前返回 `NOT_IMPLEMENTED` 是正确拒绝，套用设计补丁后再称 implementation 全绿是自证循环。
56. **空值不是一种语义。** UNIQUE helper 必须三态区分“未出现”“成功提取”“已出现但不完整”；
    最后一种必须 E999。还要覆盖 sqlglot 29 折叠到节点 `this`、30.x 拆成多节点的版本差异。
