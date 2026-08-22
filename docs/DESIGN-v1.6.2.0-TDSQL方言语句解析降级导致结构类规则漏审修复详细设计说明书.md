# DESIGN-v1.6.2.0 TDSQL 方言语句解析降级导致结构类规则漏审 修复详细设计说明书

| 项 | 内容 |
|---|---|
| 版本 | **v1.6.2.0**（在 v1.6.1.9 基础上） |
| 版本号选择 | 取次版本号而非补丁号：本次虽然产品代码只改 26 行，但**用户可见的审核结果会明显变化**——此前显示"通过"的 HASH/BROADCAST 语法表将开始报出违规（模拟实测 8/8 张由"通过"变为 2–4 条）。这是行为面的实质变化，用补丁号会低估其影响 |
| 缺陷等级 | **P0——核心能力静默漏审**（合规性问题被完全掩盖，且报告显示为"通过"） |
| 缺陷来源 | 用户对 v1.6.1.9 内网复测（报告 `Extracted_Schema_Report_6286.html`）+ 设计说明书 v1.6.1.9 §8 已登记的 **ADJ-1** |
| 影响模块 | 解析器 `backend/engine/parser/parser_legacy.py` |
| 改动文件 | **产品代码仅 1 个**：`parser_legacy.py`；另需**修订 1 条既有测试**的断言 |
| 撰写 | 智能体 A |
| 修订 | **Rev.B**——Q 评审通过；实测澄清其报告中关于 `passed` 的一处误述，并补录"通过率跌至 0%"的连带后果与 NG-8 施工红线 |
| 状态 | **可施工**——已通过智能体 Q 评审；编码由 Q 承担 |

---

## 0. 一句话结论

TDSQL 内核输出的 `TDSQL_DISTRIBUTED BY <method>(...)` 与 `BROADCAST` 两种尾子句，**都会让 sqlglot 把整条建表语句降级为 `Command` 节点**，导致 `columns` / `indexes` / `table_options` 全空，所有依赖表结构的规则**静默跳过**，报告还把这类表显示为绿色 **[通过]**。

修复方式：**只在 sqlglot 已经降级时**，剥离方言子句重新解析一次；**正常解析的语句永不进入该分支**，因此对既有行为的影响可以从控制流上被证明为零。**不改任何一条规则，不改 `raw_sql`，不动 v1.6.1.9 的 `distributed.py`。**

---

## 1. 缺陷事实

### 1.1 用户现场（v1.6.1.9 上线后复测）

报告第 #3 项 `cus_bas_corp_contact` 显示 **[通过]**，并计入 KPI"通过数 1 / 通过率 7.1%"。

实测反事实实验——把 `TDSQL_DISTRIBUTED BY HASH(\`cust_no\`)` 尾子句摘掉后重跑：

| 场景 | `columns` | `indexes` | `table_options` | 命中规则 |
|---|---|---|---|---|
| **#3 原样** | **0** | **0** | **0** | **(通过)** |
| #3 摘掉尾子句 | 25 | 2 | 3 | R036, R037, R061, (R077*) |
| #4 对照（结构同类、本就无尾子句） | 34 | 1 | 3 | R001, R036, R037, R061, R077 |

> \* 反事实里的 R077 是因为实验把分片声明一并摘掉了，属实验产物，不计入漏审。

**#3 真实被漏审 3 条：R036、R037、R061。**

### 1.2 影响面远不止 1 张表——用户原话是关键

> *"内网里有的库几乎所有的分片表都是用 `TDSQL_DISTRIBUTED BY HASH` 这种语法去写的。当前状况会把大量问题给掩盖掉。"*

**模拟实测**：把本次报告中 8 张带 `shardkey=col` 的分片表全部改写成 `TDSQL_DISTRIBUTED BY HASH(col)`（即内网的真实写法），再跑整机：

| # | 表 | 改写为 HASH 语法后（现状） | 本该报出的违规 |
|---:|---|---|---|
| 1 | big_audit_trail | **(通过)** | R029, R036, R037, R061 |
| 2 | big_order_log | **(通过)** | R029, R061 |
| 6 | t_account | **(通过)** | R036, R061, R063 |
| 7 | t_audit_log | **(通过)** | R036, R037, R061, R062 |
| 9 | t_customer | **(通过)** | R061, R063 |
| 10 | t_deposit | **(通过)** | R036, R061 |
| 12 | t_loan | **(通过)** | R036, R061, R063 |
| 14 | t_transaction | **(通过)** | R036, R037, R061, R063 |

> **8 张里 8 张全部显示"通过"，实际每张藏着 2–4 条违规。命中率 0%。**
>
> 换句话说：**在一个分片表全用 HASH 语法书写的库里，本工具对这些表的结构审核能力等于零，而报告会告诉用户"全部通过"。**

### 1.3 `BROADCAST` 同样中招（本次调研新发现）

此前只登记了 `TDSQL_DISTRIBUTED`。逐形态实测后发现 **`BROADCAST` 关键字也会导致降级**：

| 尾子句形态 | `columns` | `indexes` | 是否降级 |
|---|---|---|---|
| （无尾子句） | 2 | 1 | 正常 |
| `shardkey=sk` | 2 | 1 | 正常 |
| `shardkey=noshardkey_allset` | 2 | 1 | 正常 |
| **`BROADCAST`** | **0** | **0** | **★降级** |
| **`TDSQL_DISTRIBUTED BY HASH(\`sk\`)`** | **0** | **0** | **★降级** |
| **`TDSQL_DISTRIBUTED BY RANGE(\`sk\`)`** | **0** | **0** | **★降级** |
| **`TDSQL_DISTRIBUTED BY LIST(\`sk\`)`** | **0** | **0** | **★降级** |
| **`HASH(...)` + 二级分区 `PARTITION BY ...`** | **0** | **0** | **★降级** |
| 仅 `PARTITION BY RANGE(...)`（无 TDSQL 子句） | 2 | 1 | 正常 |

**结论**：需要处理的是**两类**方言子句，不是一类。`shardkey=` 与单独的 `PARTITION BY` 不受影响，无需处理。

### 1.4 三条规则各自的失效方式

```
方言尾子句  →  sqlglot 无法解析，整条降级为 exp.Command
           →  parsed.columns = []，parsed.indexes = []，parsed.table_options = {}
```

| 规则 | 失效方式 |
|---|---|
| **R036** | `ddl.py:528` 显式守卫 `if not parsed.columns: return None` |
| **R037** | `ddl.py:552` 同样的守卫，注释写明"列信息缺失时无判定依据，不做建议（避免解析降级时误报）" |
| **R061** | `index.py:27` 遍历 `parsed.indexes`，空列表 → 循环体一次都不执行 |

**这三处守卫本身没写错**（降级时不瞎报是对的）。问题在于**降级本身不该发生**——所以正确的修复位置是解析器，而不是去动这三条规则。

### 1.5 报告把"审不了"显示成"通过"

降级表在报告中显示为绿色 **[通过]**，与真正合规的表**完全无法区分**，并计入通过率。

用户已明确否决"仅加一条『部分规则未执行』提示"的缓解方案：

> *"你检查结果写『部分规则未执行』，不写清楚触发的规则，人家用户不会同意的……还是要根治。"*

**本设计因此采用根治方案。** 修复后这类表将正常参与全部规则审核，"通过"恢复其本来含义，无需额外提示。

---

## 2. 方案选型

### 2.1 四个候选与取舍

| 方案 | 做法 | 判定 |
|---|---|---|
| A. 无条件预剥离 | 送 sqlglot 前一律用正则剥掉方言子句 | ❌ 所有语句都被改写，正则一旦误伤字符串字面量会波及正常语句；影响面无法证明为零 |
| B. 正则兜底补全结构 | 扩展 `_regex_fallback_create_table_props()`，用正则自己解析列/索引 | ❌ 需自写列清单解析器，代码量与出错面远大于本缺陷；产出的列类型/主键标记保真度低于 sqlglot，可能造成新误判 |
| C. 抽取共享清洗工具 | 把 `distributed.py` 的 `_strip_sql_noise` / `_ddl_options_tail` 提到公共模块，解析器与规则共用 | ❌ `distributed.py` 依赖 `backend.engine.parser` 的 `ParsedSQL`，反向引用会**构成循环导入**；且需改动 v1.6.1.9 刚上线的代码 |
| **D. 降级后剥离重试（本设计采用）** | **仅当 sqlglot 已返回 `Command` 且语句含方言子句时**，剥离后重解析一次；只有重试确实产出非 `Command` 节点才采用 | ✅ 正常语句永不进入该分支，影响面可从控制流证明为零 |

### 2.2 方案 D 的关键安全性质

```
第一次 parse_one 成功解析（非 Command）  →  直接返回，不进入重试  →  行为与改前逐字一致
第一次降级为 Command                    →  才尝试剥离重试
重试仍为 Command 或抛异常                →  保留原 Command 结果      →  不劣于改前
```

**这道 `isinstance(ast, exp.Command)` 的门，把剥离正则的不精确性完全隔离在正常语句之外。** 实测边界：

| 边界场景 | 是否进入重试 | 结果 |
|---|---|---|
| 列名恰好叫 `broadcast` | **否**（第一次就解析成功） | 安全 |
| 表注释里含 `TDSQL_DISTRIBUTED BY HASH(id)` | **否**（第一次就解析成功） | 安全 |
| 表注释里含 `broadcast` 字样 | **否**（第一次就解析成功） | 安全 |
| 普通 SELECT / 普通建表 | **否** | 安全 |

> 正因为有这道门，**剥离正则不需要做注释/字符串感知**，也就不需要方案 C 的共享清洗工具，避免了循环导入与改动既有代码。

---

## 3. 详细设计（照图施工）

> 改动全部位于 `backend/engine/parser/parser_legacy.py`。行号基于 `main @ 4e2ede2`。

### 3.1 改动点 1：新增模块级常量

**位置**：`@dataclass` / `class ParsedSQL:` 声明之前（第 15–16 行附近）。

```python
# TDSQL 方言尾子句：sqlglot 不认识，会导致整条建表语句降级为 exp.Command，
# 进而使 columns/indexes/table_options 全空，所有结构类规则静默跳过。
#   分片表:  ) ENGINE=InnoDB ... TDSQL_DISTRIBUTED BY HASH(`cust_no`)
#            （HASH / RANGE / LIST 三种方法均会降级）
#   广播表:  ) ENGINE=InnoDB ... BROADCAST
# 注: `shardkey=col` 与单独的 `PARTITION BY ...` 不会降级，无需处理。
# 本正则只在"已经降级"的重试路径上使用，故无需做注释/字符串感知——
# 正常解析的语句根本不会走到那里（见 parse() 内注释）。
_TDSQL_DIALECT_RE = re.compile(
    r"\btdsql_distributed\s+by\s+\w+\s*\([^)]*\)"   # 分片表：BY HASH/RANGE/LIST(col)
    r"|\bbroadcast\b",                               # 广播表关键字
    re.IGNORECASE,
)
```

> **施工注意**：`re` 已在第 7 行 import，`sqlglot` 与 `exp` 已在第 11–12 行 import。**不需新增任何 import**（团队规约 R-17）。

### 3.2 改动点 2：`parse()` 中插入降级重试

**位置**：`parser_legacy.py:110-113`。

**改前**：

```python
        # 尝试解析SQL
        try:
            ast = sqlglot.parse_one(sql_clean, read=self.dialect)
            parsed.ast = ast
```

**改后**：

```python
        # 尝试解析SQL
        try:
            ast = sqlglot.parse_one(sql_clean, read=self.dialect)
            # v1.6.2.0: TDSQL 方言尾子句会让 sqlglot 把整条语句降级为 Command，
            # 导致 columns/indexes/table_options 全空、结构类规则静默漏审。
            # 仅在"确实已降级"且"语句含方言子句"时，剥离该子句重试一次；
            # 且只有重试确实产出非 Command 节点才采用其结果。
            # 正常解析的语句不会进入本分支，故对既有行为的影响可证明为零；
            # 重试失败时保留原 Command 结果，不劣于改前。
            # 注意: parsed.raw_sql 始终保持原文——R077/R054 依赖它提取分片键。
            if isinstance(ast, exp.Command) and _TDSQL_DIALECT_RE.search(sql_clean):
                try:
                    _retry_ast = sqlglot.parse_one(
                        _TDSQL_DIALECT_RE.sub(" ", sql_clean), read=self.dialect)
                    if not isinstance(_retry_ast, exp.Command):
                        ast = _retry_ast
                except Exception:
                    pass
            parsed.ast = ast
```

### 3.3 改动汇总

| # | 位置 | 类型 | 净增行 |
|---|---|---|---|
| 1 | 模块级 | 新增 1 个常量 + 注释 | +14 |
| 2 | `SQLParser.parse()` | 新增 12 行（含注释） | +12 |

**合计：产品代码 1 个文件、2 处、净增约 26 行；无签名变更、无新增依赖、无 import 变更、无 schema 变更、无接口变更、无前端变更、不改任何一条规则。**

---

## 4. 明确的非目标

| # | 不做什么 | 为什么 |
|---|---|---|
| **NG-1** | **不改动任何一条规则**（含 R036/R037/R061 的 `if not parsed.columns` 守卫） | 那三处守卫本身是对的——降级时不瞎报。缺陷根源在解析器，改规则是治标且会引入降级场景下的误报 |
| **NG-2** | **不改动 `backend/engine/rules/distributed.py`** | v1.6.1.9 刚上线的代码，本次零触碰。R077/R054 依赖 `raw_sql` 提取分片键，而本设计**不改 `raw_sql`**，故其行为不变（§5.2 实测 0 漂移） |
| **NG-3** | **不改写 `parsed.raw_sql`** | R077/R054/R116-118 等多条规则以它为准。只改"喂给 sqlglot 的那一份文本" |
| **NG-4** | **不把方言子句"翻译"成 `table_options["SHARDKEY"]`** | R077 已能从 `raw_sql` 取到分片键，无需解析器代劳；伪造表选项会改变 `table_options` 的契约并扩大影响面 |
| **NG-5** | **不修 `parsed.indexes` 不产出 UNIQUE 条目的问题** | 那是 **ADJ-5** 的另一半，与 R077 的宽松 `或` 判定（ADJ-4，用户已决策永久保留）构成原子约束，**不得单独修**。详见 §6.1 |
| **NG-6** | **不改 R037 的 `delete_flags`（`status` 保留）** | 用户已决策：*"问题 2 不动了，status 保留吧。"* |
| **NG-7** | 不处理 `shardkey=` 与单独 `PARTITION BY` | 实测不降级，无需处理 |
| **NG-8**<br>*(Rev.B)* | **不得改动 `AuditResult.passed` 的判定语义**（`checker.py:182` 的 `len(violations) == 0`） | 本次会让不少表新增 INFO 级违规，从而由"通过"变为"未通过"。**这是正确结果，不是需要被"修掉"的副作用。** 改动该判定会波及全部 119 条规则的通过语义与所有历史报表口径，量级远超本缺陷 |

---

## 5. 影响面分析（全部实测）

### 5.1 全规则维度漂移扫描

同一份语料（仓库全部 `.sql` 切分 **201 条** + 生产报告 14 表原始 DDL），分别灌进基线引擎与本方案引擎，比对**完整违规规则集**：

```
语料 201 条 | 基线解析异常 0 | 修复后解析异常 0 | 完整规则集变化 1 条

  现场#3 cus_bas_corp_contact
      改前 (通过)
      改后 R036,R037,R061        新增[R036,R037,R061]
```

> **201 条语料中只有 1 条变化，且恰为设计预期的那一条，新增规则与 §1.1 反事实实验完全一致。零附带影响。**

### 5.2 R077 / R054 专项漂移

```
语料 201 条 | R077/R054 判定变化 0 条
```

v1.6.1.9 的 **45 条验收用例在本方案下全部通过**（`45 passed`）。这印证了 NG-2/NG-3 的设计意图：因为不改 `raw_sql`，R077/R054 的取值路径完全不受影响。

### 5.3 现有 14 张生产表

| 结果 | 数量 |
|---|---|
| 规则集发生变化 | **1 张**（#3，新增 R036/R037/R061） |
| 逐条不变 | **13 张** |

### 5.4 全量回归

| 项 | 基线 | 本方案 | 判定 |
|---|---|---|---|
| 全量 `pytest tests/` | 1329 passed / 0 failed / 29 skipped | **1328 passed / 1 failed / 29 skipped** | **1 条既有测试需修订，见 §5.5** |
| `tests/test_r077_r054_tdsql_syntax.py`（v1.6.1.9 验收） | 45 passed | **45 passed** | ✅ 不受影响 |

### 5.5 唯一失败的既有测试：它本身就是缺陷的又一个实例

失败用例：`tests/test_v2_syntax_truncation.py::test_split_truncated_sql_file`

该测试的**真实意图**（见文件 docstring）是：验证残缺截断 SQL 能被正确分割，且截断的那条被 `E999_SYNTAX_ERROR` 阻断。测试数据里 `t1` / `t3` 两条完整语句的尾部恰好带 `BROADCAST`：

```sql
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 BROADCAST COMMENT='测试表';
```

实测：

| | `columns` | 命中规则 |
|---|---|---|
| 基线（BROADCAST 导致降级） | **0** | **无** |
| 本方案（可正常解析） | **3** | **R037 (INFO)** |

第 44、47 行的断言 `results[0].passed is True` / `results[2].passed is True` 之所以成立，**正是因为 `BROADCAST` 触发了降级、把 R037 掩盖掉了**——**这条测试一直在为缺陷背书**。

**⚠️ 评审分歧已实测澄清（Rev.B 补充）**：智能体 Q 的评审报告 §2.3 称该测试"改后 `passed` 仍 True，不破坏既有断言，仅需追加新断言"。**该结论与实测不符。**

决定性依据 —— `backend/engine/checker.py:182`：

```python
            passed=len(violations) == 0,
```

**`passed` 只看违规条数，不按严重度分档：任何一条违规（含 INFO 级）都会使其为 False。** 实测：

```
违规: [('R037', 'Severity.INFO')]
result.passed = False
pytest tests/test_v2_syntax_truncation.py → 1 failed
```

**因此该测试的既有断言确实会失败，必须修订，不能只做追加。**

> **🚨 施工红线**：发现该测试失败后，**绝不允许**通过"让 INFO 级违规不计入 `passed`"来解决——那会改变**全部 119 条规则**的通过判定语义，是远大于本次缺陷的行为变更。正确做法只有下面这一种。

**修订方式**（把断言改回其真实意图，而非放宽）：

```python
    # 改前
    assert results[0].passed is True, "标准的 t1 语句应该审计通过"
    assert results[2].passed is True, "标准的 t3 语句应该审计通过"

    # 改后（本测试的意图是"语句分割 + 语法错误阻断"，与 INFO 级建议无关）
    assert not any(v.rule_id == "E999_SYNTAX_ERROR" for v in results[0].violations), \
        "完整的 t1 语句不应被语法错误阻断"
    assert not any(v.rule_id == "E999_SYNTAX_ERROR" for v in results[2].violations), \
        "完整的 t3 语句不应被语法错误阻断"
```

> **这是修正一条断言错了的测试，不是为了让构建变绿而放宽门槛。** 若评审认为应保留 `passed is True` 的强断言，则应改测试数据（去掉 `BROADCAST`），但那会削弱本用例对方言语句的覆盖，A 不建议。

**全仓库核查**：`grep` 确认只有该文件受影响。`test_r077_r054_tdsql_syntax.py` 中大量使用 `TDSQL_DISTRIBUTED` / `BROADCAST` 的用例只断言 R077/R054，故全部不受影响（实测 45 passed）。

---

## 6. 与既有已知缺陷的交互

### 6.1 ⚠️ 与 ADJ-5 的交互（必须核查，已实测通过）

v1.6.1.9 设计说明书（Rev.H）§8.1 确立了一条**原子变更约束**：R077 保留"分片键 ∈ 主键 **或** 唯一索引"的宽松判定（ADJ-4，用户已决策永久保留）；一旦唯一索引提取能力增强，就会激活那个宽松分支并**产生漏报**。

本方案让 `parsed.indexes` 由空变为有内容，**必须核查是否踩雷**。实测：

| 场景 | 改前 | 本方案后 | `parsed.indexes` 中的 UNIQUE 条目数 |
|---|---|---|---|
| HASH，分片键 ∉ 主键、只在反引号 UNIQUE 中（违反 J-2，必须报） | R077+R054 | **R077+R054** | 改前 0 → 改后 **0** |
| HASH，分片键 ∈ 主键但某 UNIQUE 不含它（违反 J-3，必须报） | R054 | **R054** | 改前 0 → 改后 **0** |
| HASH，合规 | 零违规 | 零违规 | 0 → 0 |
| `BROADCAST` 广播表 | 零违规 | 零违规 | 0 → 0 |

**结论：未踩雷。** 原因是解析器只把普通 `KEY` 归入 `parsed.indexes`（type=NORMAL），**始终不产出 UNIQUE 条目**——这是 ADJ-5 的另一半，本方案（NG-5）未触碰。R077 的 `_collect_unique_index_cols` 仍取不到唯一索引，宽松分支保持未激活。

> **⚠️ 这是一条"条件性"安全性质，不是永久性质。** 若将来有人补上 `parsed.indexes` 的 UNIQUE 产出，ADJ-5 的雷立刻被激活。故 §7 要求新增一条断言测试锁定该前提。

### 6.2 与 ADJ-7 的关系（本次不覆盖）

R116/R117/R118（`oracle_compat.py`）用自己的正则从 DDL 取分片键，**只认 `shardkey=`**。本方案让这类语句可以结构化解析，但**不会**让这三条规则看见 HASH 语法的分片键——它们的取值逻辑与解析结果无关。

**ADJ-7 仍然存在**，交付说明须继续声明。

### 6.3 ADJ 台账更新

| 编号 | 状态 |
|---|---|
| **ADJ-1** | **本设计根治**（并扩展覆盖 `BROADCAST`——调研新发现） |
| ADJ-2 / ADJ-3 | 仍留 Phase 2（**ADJ-3 是真实静默失效，建议优先**） |
| ADJ-4 | 用户已决策关闭，不动 |
| ADJ-5 | 原子变更约束，本次未触碰；**新增断言测试锁定其前提**（§7） |
| ADJ-6 | 用户已决策关闭，不动 |
| ADJ-7 | 仍留 Phase 2，本方案不覆盖 |
| ADJ-8 | 仍留 Phase 2，本方案未复用 `clean_sql()`，不受影响 |

---

## 7. 验收测试方案

### 7.1 新增测试（建议落库 `tests/test_parser_tdsql_dialect_fallback.py`）

| 用例 | 场景 | 期望 |
|---|---|---|
| **D1** | `TDSQL_DISTRIBUTED BY HASH(\`sk\`)` 建表 | `columns > 0`、`indexes` 含普通索引；命中 R036/R037/R061 等结构类规则 |
| **D2** | `TDSQL_DISTRIBUTED BY RANGE(...)` | 同上（可结构化解析） |
| **D3** | `TDSQL_DISTRIBUTED BY LIST(...)` | 同上 |
| **D4** | `BROADCAST` 关键字建表 | 同上 |
| **D5** | `HASH(...)` + 二级分区 `PARTITION BY RANGE(...)` | 同上（剥离后 `PARTITION BY` 仍可解析） |
| **D6** | 生产 #3 原始 DDL | `columns == 25`，命中 **R036, R037, R061**，且**无 R077** |
| **N1★** | 列名恰好叫 `broadcast` 的普通建表 | **不进入重试**；解析结果与改前逐字一致 |
| **N2★** | 表注释含 `TDSQL_DISTRIBUTED BY HASH(id)` | **不进入重试**；R077 仍触发（v1.6.1.9 的 X1 行为不变） |
| **N3★** | 表注释含 `broadcast` 字样 | **不进入重试**；解析结果与改前一致 |
| **N4★** | `shardkey=col` 建表 | **不进入重试**（本就正常解析） |
| **N5★** | 单独 `PARTITION BY RANGE(...)`（无方言子句） | **不进入重试** |
| **N6★** | 残缺截断 SQL（无方言子句） | 仍产出 `E999_SYNTAX_ERROR`，阻断能力不变 |
| **N7★** | 剥离后仍无法解析的构造语句 | 保留原 `Command` 结果，不劣于改前 |
| **G1★** | **ADJ-5 前提锁定**：断言 `parsed.indexes` / `index_definitions` 在 HASH+UNIQUE 建表下**仍不产出 UNIQUE 条目** | 一旦该前提被打破，测试立即失败，提示必须同时处理 ADJ-4/ADJ-5 |

★ = 反向鉴别用例。

### 7.2 需修订的既有测试

`tests/test_v2_syntax_truncation.py::test_split_truncated_sql_file` 第 44、47 行——按 §5.5 的方式修订断言。**修订须在提交信息中说明理由**，避免后人误读为"为过测试而放宽"。

### 7.3 回归门槛

| 项 | 门槛 |
|---|---|
| 全量 `pytest tests/` | 收集数 = 基线 + 新增用例数；**0 failed**；skipped 不高于同环境改前基线 |
| `tests/test_r077_r054_tdsql_syntax.py` | **45 passed，一条不改**（本方案不得影响 v1.6.1.9 的成果） |
| 规则总数 | 119（92 ALL + 27 DISTRIBUTED），必须不变 |
| **全规则维度漂移扫描** | 201 条语料，**异常必须为 0**；变化**必须仅 1 条**（现场 #3 新增 R036/R037/R061）。**出现第 2 条即停止施工，回到评审** |
| R077/R054 专项漂移 | **必须 0 条变化** |
| 生产 14 表整机回放 | **仅 #3 变化**；#4 的 R077 仍保留 |

---

## 8. 风险与回滚

| 风险 | 等级 | 对策 |
|---|---|---|
| **报告"通过率"KPI 大幅下跌** | **高（预期内，须提前告知）** | 实测：现场 14 表由 **7.1% → 0.0%**；模拟内网（分片表用 HASH 语法）由 **64.3% → 0.0%**。成因是 `passed = len(violations) == 0`——一条 INFO 级"建议"也会让整表判为未通过。**这是既有口径被本次修复放大后的表现，不是本次引入**。不得为了让数字好看而改判定语义（NG-8）。**必须在上线说明中提前告知，否则用户看到 0% 会当成系统故障** |
| **用户可见的违规数上升** | **高（预期内）** | 这是修复的**目的**——把被掩盖的问题暴露出来。模拟实测：内网 HASH 语法分片表将从"全部通过"变为每张 2–4 条违规（多为 INFO/WARNING 级）。**必须在上线说明中提前告知**，否则用户会误以为是新引入的问题 |
| 剥离正则误伤（如字符串内含方言关键字） | **低** | 由 `isinstance(ast, exp.Command)` 门隔离——正常语句永不进入重试。4 组边界用例实测均不进入 |
| 重试后解析结果与真实语义有偏差 | 低 | 剥离的只是方言尾子句，列定义与索引部分逐字未动；且只在重试产出非 `Command` 时采用 |
| 触发 ADJ-5 漏报 | **中→已排除** | §6.1 实测未踩雷；并以 **G1★** 断言测试锁定前提 |
| 二次解析带来的性能开销 | 低 | 只在已降级的语句上多解析一次；正常语句零额外开销 |
| 回滚 | — | 单文件 2 处纯增量，`git revert` 单提交即回到 v1.6.1.9 行为；无数据/schema/接口残留 |

---

## 9. 施工检查单

**范围控制**

- [ ] 产品代码只改 `backend/engine/parser/parser_legacy.py`（`git diff --stat` 中产品代码只有这一行）
- [ ] 无新增 import（`re` / `sqlglot` / `exp` 均已存在）
- [ ] **未改动 `backend/engine/rules/` 下的任何文件**（NG-1 / NG-2）
- [ ] **`parsed.raw_sql` 仍为原文**，只有喂给 `sqlglot.parse_one` 的那份文本被剥离（NG-3）
- [ ] 未向 `table_options` 写入任何伪造的分片键（NG-4）
- [ ] 未改动 `parsed.indexes` 的 UNIQUE 产出逻辑（NG-5 / ADJ-5）
- [ ] 未改动 R037 的 `delete_flags`（NG-6）
- [ ] **未改动 `AuditResult.passed` 的判定语义**（NG-8 施工红线）——测试失败必须靠修订断言解决，绝不能靠改通过判定

**实现要点**

- [ ] 重试的**前置条件是 `isinstance(ast, exp.Command)`**——不得改成无条件剥离
- [ ] 重试结果**只在 `not isinstance(_retry_ast, exp.Command)` 时才采用**
- [ ] 重试异常被吞掉并保留原 `ast`（不得让重试失败影响既有路径）
- [ ] 方言正则覆盖 `TDSQL_DISTRIBUTED BY <method>(...)` 与 `BROADCAST` 两类

**验收**

- [ ] §7.1 的 14 条用例全部通过，其中 **7 条 ★ 反向鉴别用例**必须证明"不进入重试"
- [ ] **G1★ ADJ-5 前提锁定测试**已落库
- [ ] §7.2 的既有测试已按理由修订，提交信息写明依据
- [ ] `test_r077_r054_tdsql_syntax.py` 仍 **45 passed，一条未改**
- [ ] 全规则漂移：201 条语料、异常 0、**变化恰为 1 条**
- [ ] 生产 14 表整机回放：仅 #3 变化，#4 的 R077 保留
- [ ] 规则总数仍 119（92/27）

**交付说明（必须写明）**

- [ ] **报告"通过率"会大幅下跌**——实测现场 14 表 7.1%→0.0%，模拟内网 64.3%→0.0%。成因是"一条 INFO 建议即判未通过"的**既有**口径被放大，**不是本次引入**。须说明：数字下跌代表"此前被掩盖的建议项现在可见了"，不代表数据库质量变差
- [ ] **本次修复会让报告中的违规数明显上升**——这是把此前被掩盖的问题暴露出来，不是新引入的缺陷。内网 HASH 语法分片表尤其明显（模拟实测 8/8 张由"通过"变为 2–4 条违规）
- [ ] `BROADCAST` 关键字声明的广播表同样在本次恢复审核（此前也被掩盖）
- [ ] **ADJ-7 仍然存在**：R116/R117/R118 依旧看不见 HASH 语法的分片键，不要误以为"HASH 表已被全套分片键规则覆盖"
- [ ] ADJ-2/ADJ-3/ADJ-8 仍留 Phase 2，**ADJ-3 是真实静默失效，建议优先**
- [ ] ADJ-4、ADJ-6 及 R037 的 `status` 口径均由用户决策保持现状

---

## 附录 A：实测证据清单

| 编号 | 结论 | 证据 |
|---|---|---|
| E-1 | #3 因降级被漏审 R036/R037/R061 | 反事实实验：摘掉尾子句后 `columns` 0→25、`indexes` 0→2，命中新增三条 |
| E-2 | **`BROADCAST` 同样导致降级**（调研新发现） | 10 组尾子句形态逐一实测 |
| E-3 | `TDSQL_DISTRIBUTED BY` 的 HASH/RANGE/LIST 三种方法均降级 | 同上 |
| E-4 | `shardkey=` 与单独 `PARTITION BY` 不降级 | 同上 |
| E-5 | **模拟内网：8 张 HASH 语法分片表 8 张全部误显"通过"** | 把 8 张带 `shardkey=` 的表改写为 HASH 语法后整机回放 |
| E-6 | 方案 D 使全部 6 种降级形态恢复结构化解析 | 原型实测，AST 类型由 `Command` 变为 `Create` |
| E-7 | 4 组边界场景**均不进入重试分支** | 列名叫 broadcast / 注释含方言子句 / 普通 SQL |
| E-8 | 全规则维度漂移：201 条语料**仅 1 条变化** | 基线 ↔ 方案 D 全语料对跑，异常 0 |
| E-9 | R077/R054 专项漂移 **0 条变化**；v1.6.1.9 的 45 条用例全过 | 同上 |
| E-10 | **未触发 ADJ-5**：修复后 `parsed.indexes` 仍不产出 UNIQUE 条目 | 4 组 HASH+UNIQUE 场景实测 |
| E-11 | 全量回归仅 1 条既有测试失败，且该测试本身在为缺陷背书 | `test_v2_syntax_truncation.py` 的 t1/t3 带 `BROADCAST`，改前 0 列/无违规，改后 3 列/命中 R037 |
| E-12 | 受影响的既有测试**仅此 1 个文件** | 全仓库 `grep` `BROADCAST` / `TDSQL_DISTRIBUTED` 逐处核查 |
