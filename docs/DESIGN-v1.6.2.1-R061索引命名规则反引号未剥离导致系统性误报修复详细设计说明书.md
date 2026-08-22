# DESIGN-v1.6.2.1 R061 索引命名规则反引号未剥离导致系统性误报 修复详细设计说明书

| 项目 | 内容 |
|---|---|
| 文档版本 | Rev.A |
| 目标版本 | **v1.6.2.1**（补丁版本：本次只**减少**误报，不新增任何告警） |
| 缺陷来源 | v1.6.2.0 内网上线后手工扫描报告 `Extracted_Schema_Report_6297.html` |
| 缺陷编号 | **DEF-R061-BACKTICK** |
| 撰写 | 智能体 A |
| 施工 | 智能体 Q |
| 基线 commit | `3f3523d`（main） |
| 改动范围 | **单文件、单函数、4 行**：`backend/engine/rules/index.py` 的 `R061IndexNaming.check()` |
| 实测结论 | 生产 14 表回放：R061 误报 **11 条全部消除**，真实违规 **3 条全部保留**；全语料 193 条 × 119 条规则漂移 **0**；全量回归与基线 **完全一致** |

---

## 0. 一句话结论

R061 拿到的索引名是**带反引号的原始字面量**（`` `idx_trace` ``），却直接做 `startswith("idx_")` 判断，
于是**所有符合规范的索引反而被判为不合规**——生产报告中 R061 的 14 条命中里 **11 条是误报（78.6%）**。
根因是解析器 `_parse_index_constraint()` 对**索引列名**做了 ``.strip('`"')``、却唯独漏掉了**索引名本身**。
修复只需在 R061 内部对索引名做一次与仓内既有写法字面一致的去引号处理。

---

## 1. 缺陷事实

### 1.1 用户现场（v1.6.2.0 上线后手工扫描，报告 6297）

14 张表**每一张**都命中了 R061。逐表实测（`instance_type=distributed`）：

| # | 表名 | 解析出的索引名 | 当前 R061 告警 | 判定 |
|---|---|---|---|---|
| 1 | `big_audit_trail` | `` `idx_trace` `` `` `idx_operator` `` `` `idx_event` `` | 普通索引 `` '`idx_trace`' `` 应以 idx_ 开头 | ❌ **误报** |
| 2 | `big_order_log` | `` `idx_user` `` `` `idx_order` `` `` `idx_create` `` | 普通索引 `` '`idx_user`' `` 应以 idx_ 开头 | ❌ **误报** |
| 3 | `cus_bas_corp_contact` | `` `cus_bas_corp_contact_IDX1` `` 等 | 普通索引 `'cus_bas_corp_contact_idx1'` 应以 idx_ 开头 | ✅ 真实违规 |
| 4 | `cus_bas_corp_contact_addr_20260511` | `` `cus_bas_corp_contact_addr_IDX1` `` | 同上形态 | ✅ 真实违规 |
| 5 | `cus_name_list_type` | `` `CUS_NAME_LIST_TYPE_IDX1` `` | 同上形态 | ✅ 真实违规 |
| 6 | `t_account` | `` `idx_cust_id` `` `` `idx_status` `` | 普通索引 `` '`idx_cust_id`' `` 应以 idx_ 开头 | ❌ **误报** |
| 7 | `t_audit_log` | `` `idx_user_id` `` 等 3 个 | ❌ **误报** | ❌ **误报** |
| 8 | `t_branch` | `` `idx_city` `` | ❌ **误报** | ❌ **误报** |
| 9 | `t_customer` | `` `idx_id_no` `` 等 3 个 | ❌ **误报** | ❌ **误报** |
| 10 | `t_deposit` | `` `idx_cust_id` `` `` `idx_account_no` `` | ❌ **误报** | ❌ **误报** |
| 11 | `t_dict` | `` `idx_dict_type` `` | ❌ **误报** | ❌ **误报** |
| 12 | `t_loan` | `` `idx_cust_id` `` `` `idx_status` `` | ❌ **误报** | ❌ **误报** |
| 13 | `t_product` | `` `idx_product_type` `` | ❌ **误报** | ❌ **误报** |
| 14 | `t_transaction` | `` `idx_account_no` `` 等 3 个 | ❌ **误报** | ❌ **误报** |

**合计：R061 命中 14 条，其中误报 11 条、真实违规 3 条，误报率 78.6%。**

值得注意：#3/#4/#5 是 Oracle 迁移过来的 `*_IDX1` 命名，**确实不符合规范**，R061 报它们是对的。
也就是说这条规则的**判定方向本身没错，错的只是拿到的输入没去引号**。

### 1.2 根因：解析器给索引列去了引号，唯独漏了索引名

`backend/engine/parser/parser_legacy.py::_parse_index_constraint()`（第 569-591 行）：

```python
def _parse_index_constraint(self, col_def) -> dict:
    """解析 IndexColumnConstraint"""
    idx_name_node = col_def.args.get("this")
    idx_name = idx_name_node.sql(dialect=self.dialect) if idx_name_node else ""   # ← 第572行：未去引号
    idx_cols = []
    idx_type = "NORMAL"
    for ordered_expr in col_def.expressions:
        col_node = ordered_expr.args.get("this") if hasattr(ordered_expr, 'args') else None
        if col_node:
            col_name = col_node.sql(dialect=self.dialect).strip('`"')             # ← 第578行：去了引号
```

同一个函数里，**索引列名**（578 行）做了 ``.strip('`"')``，**索引名**（572 行）没做。
`_parse_unique_constraint()` 第 604 行与 608 行存在**完全相同的一对**（名未剥、列已剥）。

于是 `parsed.indexes` 的实际内容是（实测）：

```
{'name': '`idx_a`', 'columns': ['a'], 'type': 'NORMAL'}
              ↑ 带反引号        ↑ 已去引号
```

R061 在 `index.py:28` 直接 `idx.get("name","").lower()` 拿到 `` `idx_trace` ``，
`"`idx_trace`".startswith("idx_")` → **False** → 报违规。

**控制变量实证**（同一张表，只改索引名写法）：

| 探针 | 索引定义 | 当前行为 |
|---|---|---|
| P1 | `` KEY `idx_cust` (`cust_no`) `` | R061 **命中**（误报） |
| P2 | `` KEY idx_cust (`cust_no`) `` | R061 不命中（正确） |

同一个名字，**加不加反引号决定了结论**——这就是根因的直接证据。

### 1.3 为什么必须现在修：v1.6.2.0 把它放大了

这个缺陷**先于 v1.6.2.0 存在**（报告 6286 中同样可见）。但在 v1.6.2.0 之前，
`TDSQL_DISTRIBUTED BY HASH(...)` 语句会整条降级为 `exp.Command`，`parsed.indexes` **全空**，
R061 **根本不执行**——缺陷被漏审掩盖了。

v1.6.2.0 修好了降级漏审之后，这批分片表**第一次真正进入 R061**，
于是**每一张用 `SHOW CREATE TABLE` 导出的分片表都稳定地多出一条假告警**。
而 `SHOW CREATE TABLE` 的输出**索引名恒带反引号**——这意味着：

> **凡是从生产库导出的 DDL 送审，R061 必然误报。** 这是 100% 复现、系统性的，不是偶发。

### 1.4 附带缺陷：告警消息显示的是被小写化后的名字

`index.py:34/37/40` 的消息里用的是 `idx_name`，而 `idx_name` 已经在第 28 行被 `.lower()` 过。
所以 #5 的真实索引名是 `` `CUS_NAME_LIST_TYPE_IDX1` ``，报告里却显示成 `'cus_name_list_type_idx1'`——
**用户拿着告警去库里搜索索引名会搜不到**。本次一并修正（用未小写的名字做展示）。

---

## 2. 方案选型

### 2.1 三个候选

| 方案 | 做法 | 改动面 | 取舍 |
|---|---|---|---|
| **A（采纳）** | 在 `R061.check()` 内部对索引名去引号 | `index.py` 单函数 4 行 | 只影响 R061 一条规则；不改共享数据结构 |
| B | 在解析器 `_parse_index_constraint()` / `_parse_unique_constraint()` 补 `.strip()` | `parser_legacy.py` 2 行 | 更"根治"，但改的是 119 条规则共用的数据结构 |
| C | 放宽 R061 判定（如允许任意前后缀） | `index.py` | ❌ 直接否决：会把 #3/#4/#5 三条**真实违规**一起放过，制造漏报 |

### 2.2 为什么不选 B——已实测，不是猜测

我把 B 方案**实际实现在独立工作树中跑了完整对比**：

- **审核结论层面：A 与 B 完全等价。** 生产 14 表逐表规则集合**逐条相同**；全语料 193 条语句 × 119 条规则，**A 与 B 差异 0 条**；12 个判别探针**结论全同**。
- **但 B 有 A 没有的副作用：它改变了其他规则的输出文本。** 实测 R019（冗余索引规则）：

  | | R019 告警文本 |
  |---|---|
  | 基线 | 索引 `` '`idx_a`' `` (a) 是 `` '`idx_ab`' `` (a,b) 的前缀，存在冗余 |
  | **A 方案** | 索引 `` '`idx_a`' `` (a) 是 `` '`idx_ab`' `` (a,b) 的前缀，存在冗余 ← **与基线逐字相同** |
  | **B 方案** | 索引 `'idx_a'` (a) 是 `'idx_ab'` (a,b) 的前缀，存在冗余 ← **变了** |

  `parsed.indexes[*]["name"]` 全仓共有 3 处消费点：`index.py:28`（R061，**语义判定**）、
  `dml.py:175`（R019，**仅作展示文案**）、`distributed.py:179`（R054/R077 的 `_iter_unique_indexes`，
  **仅作展示文案**，且属 v1.6.1.9 刚稳定下来的冻结代码）。
  B 会同时改动后两处的输出。

**结论**：在"审核结论完全等价"的前提下，选择**不去碰 119 条规则共用的解析器**、
也**不去碰 v1.6.1.9 刚冻结的 `distributed.py` 输出**的那一个。这符合用户反复强调的
「务必严控代码修改范围，绝不能因为本次修改影响了项目的其他核心功能和其他核心审核规则」。

B 方案的价值不是零——它消除了数据结构层面的不一致。**本次不做，登记为 ADJ-9 留待专项**（见 §6.2）。

### 2.3 去引号字符集的选择

采用 ``.strip('`"\' ')``（反引号、双引号、单引号、空格）。理由：**与仓内规则层既有写法字面一致**，
Q 施工时可直接比对：

- `backend/engine/rules/naming.py:98` → ``col.get("name", "").strip("`\"' ").lower()``
- `backend/engine/rules/distributed.py:187` → ``{c.strip('`"\' ').lower() for c in ...}``（v1.6.1.9 代码）

> ⚠️ **已知边界（显式声明，非疏漏）**：字符集含空格，意味着病理写法 `` KEY ` idx_cust ` (...) ``
> （反引号**内部**带首尾空格的索引名）修复后**不会**再报 R061。实测探针 E5 确认此行为。
> 接受此差异的依据：(a) 实测生产 6297 全部 29 个索引名**无一**为该形态；
> (b) 与规则层既有两处调用点保持字面一致，优先于覆盖一个生产不存在的病理构造。
> 若后续评审要求收紧，改法为 ``.strip().strip('`"\'')``（先剥外围空白、再剥引号），**一行可切换**。

---

## 3. 详细设计（照图施工）

### 3.1 唯一改动点：`backend/engine/rules/index.py` 第 27-41 行

**改动前**（第 27-41 行，逐字现状）：

```python
        for idx in parsed.indexes:
            idx_name = idx.get("name", "").lower()
            idx_type = idx.get("type", "NORMAL")
            if not idx_name:
                continue
            if idx_type == "PRIMARY":
                if not idx_name.startswith("pk_"):
                    return self._make_violation(f"主键索引 '{idx_name}' 应以 pk_ 开头")
            elif idx_type == "UNIQUE":
                if not idx_name.startswith("uk_"):
                    return self._make_violation(f"唯一索引 '{idx_name}' 应以 uk_ 开头")
            else:
                if not idx_name.startswith("idx_"):
                    return self._make_violation(f"普通索引 '{idx_name}' 应以 idx_ 开头")
        return None
```

**改动后**（逐字照抄即可）：

```python
        for idx in parsed.indexes:
            # 解析器对索引"列名"做了去引号、唯独"索引名"未做（parser_legacy.py:572/604），
            # 而 SHOW CREATE TABLE 导出的索引名恒带反引号，直接 startswith 会把
            # 合规的 `idx_xxx` 判成不合规。此处补齐去引号，字符集与 naming.py:98、
            # distributed.py:187 保持一致。raw_name 保留原始大小写，仅用于告警展示，
            # 使用户能按告警文本在库中检索到该索引。
            raw_name = idx.get("name", "").strip('`"\' ')
            idx_name = raw_name.lower()
            idx_type = idx.get("type", "NORMAL")
            if not idx_name:
                continue
            if idx_type == "PRIMARY":
                if not idx_name.startswith("pk_"):
                    return self._make_violation(f"主键索引 '{raw_name}' 应以 pk_ 开头")
            elif idx_type == "UNIQUE":
                if not idx_name.startswith("uk_"):
                    return self._make_violation(f"唯一索引 '{raw_name}' 应以 uk_ 开头")
            else:
                if not idx_name.startswith("idx_"):
                    return self._make_violation(f"普通索引 '{raw_name}' 应以 idx_ 开头")
        return None
```

> ✅ **本文档的代码块已自验证**：`3.1` 的「改动前」块经程序比对与 `index.py` **逐字匹配**；
> 「改动后」块被**原样抽取**并施工到一棵干净工作树上，实测语法通过、导入自检通过、
> 生产 14 表与判别矩阵结果与原型**完全一致**、全量回归 **2 failed / 1341 passed / 29 skipped**（与基线同）。
> Q 可以直接复制粘贴，无需再做适配。

### 3.2 改动汇总

| 序号 | 文件 | 位置 | 改动 | 行数 |
|---|---|---|---|---|
| 1 | `backend/engine/rules/index.py` | 第 28 行 | 拆成 `raw_name`（去引号、保留大小写）+ `idx_name`（小写，用于判定） | +1/-1 |
| 2 | 同上 | 第 34/37/40 行 | 告警文案由 `{idx_name}` 改为 `{raw_name}` | ±3 |
| 3 | 同上 | 第 27 行前 | 增加 6 行说明注释 | +6 |

**产品代码净改动：1 个文件、1 个函数、逻辑行 4 行（+注释 6 行）。不新增 import，不新增模块级常量。**

---

## 4. 明确的非目标（NG，施工红线）

| 编号 | 非目标 | 说明 |
|---|---|---|
| **NG-1** | **不改 `parser_legacy.py`** | B 方案不在本次范围。v1.6.2.0 刚改过解析器，需要稳定观察期 |
| **NG-2** | **不改 `distributed.py`** | v1.6.1.9 冻结代码，R054/R077 刚稳定，一个字符都不动 |
| **NG-3** | **不改 `dml.py`（R019）** | 其索引名展示文案保持现状（仍带反引号），本次不统一 |
| **NG-4** | **不改 R062-R068** | 同文件其余 7 条规则不得触碰；它们读的是**索引列名**，本就已去引号 |
| **NG-5** | **不放宽 R061 判定口径** | `pk_`/`uk_`/`idx_` 三个前缀要求原样保留；#3/#4/#5 必须继续报 |
| **NG-6** | **不修复 UNIQUE / PRIMARY 分支的"死代码"问题** | 见 §6.1，属 ADJ-5 范畴，本次不动 |
| **NG-7** | **不改 R036 口径** | 用户已明确决策：R036 只认 `create_time`/`update_time` 两个字面名，**维持现状** |
| **NG-8** | **不修订任何既有测试用例** | 已逐条审计，**无一条依赖缺陷行为**（见 §5.6）。若施工中发现必须改测试，说明改动跑偏了，**停下来复核** |

---

## 5. 影响面分析（全部实测，非推演）

测试环境：MariaDB `127.0.0.1:13306`（库 `rbac18`）+ uvicorn `127.0.0.1:8000` 均在线。
对照方式：基线 `/home/user/tdsql-sqlcheck`（commit `3f3523d`）vs 修复工作树，同脚本、同语料、同参数。

### 5.1 规则引擎指纹

| | 基线 | 修复后 |
|---|---|---|
| 规则总数 | 119 | **119** |
| `InstanceScope.ALL` | 92 | **92** |
| `InstanceScope.DISTRIBUTED` | 27 | **27** |

### 5.2 全语料 × 全规则漂移扫描

对仓内**全部 `*.sql`** 递归收集，切分得 **193 条**语句，逐条以 `instance_type=distributed`
过**全部 119 条规则**，比对命中规则集合与 `passed` 标志：

| 指标 | 结果 |
|---|---|
| 语句数 | 193（基线/修复后一致） |
| 解析异常数 | 0 / 0 |
| **发生变化的语句数** | **0** |

> 语料内**零漂移**的原因已查明：仓内测试语料的索引名**全部是裸名**（`tests/rule_audit_materials/sql_audit/03_index.sql` 中带反引号索引名计数 = **0**）。
> 这既说明本次改动对既有语料**完全无扰**，也反过来暴露了**语料本身缺少生产形态覆盖**——
> 这正是缺陷能长期潜伏的原因，故 §7.1 要求补齐带反引号的用例。

### 5.3 生产 14 表回放（报告 6297 原样 DDL）

| # | 表名 | 基线命中规则 | 修复后命中规则 | 变化 |
|---|---|---|---|---|
| 1 | `big_audit_trail` | R029,R036,R037,**R061** | R029,R036,R037 | −R061 |
| 2 | `big_order_log` | R029,**R061** | R029 | −R061 |
| 3 | `cus_bas_corp_contact` | R036,R037,R061 | R036,R037,**R061** | **不变（真实违规保留）** |
| 4 | `cus_bas_corp_contact_addr_20260511` | R001,R036,R037,R061,R077 | R001,R036,R037,**R061**,R077 | **不变（真实违规保留）** |
| 5 | `cus_name_list_type` | R036,R037,R061 | R036,R037,**R061** | **不变（真实违规保留）** |
| 6 | `t_account` | R036,**R061**,R063 | R036,R063 | −R061 |
| 7 | `t_audit_log` | R036,R037,**R061**,R062 | R036,R037,R062 | −R061 |
| 8 | `t_branch` | R036,**R061** | R036 | −R061 |
| 9 | `t_customer` | **R061**,R063 | R063 | −R061 |
| 10 | `t_deposit` | R036,**R061** | R036 | −R061 |
| 11 | `t_dict` | R036,**R061**,R063 | R036,R063 | −R061 |
| 12 | `t_loan` | R036,**R061**,R063 | R036,R063 | −R061 |
| 13 | `t_product` | R036,**R061**,R063 | R036,R063 | −R061 |
| 14 | `t_transaction` | R036,R037,**R061**,R063 | R036,R037,R063 | −R061 |

**关键性质（逐行程序化校验通过）：11 处变化全部是"仅移除 R061"，
没有任何一行新增任何规则、也没有任何一行移除 R061 以外的规则。**

KPI 变化：

| 指标 | 基线 | 修复后 |
|---|---|---|
| R061 命中表数 | 14 / 14 | **3 / 14** |
| 违规条目总数 | 43 | **32**（−11，−25.6%） |
| 通过表数 | 0 / 14 | 0 / 14（**不变**——本修复不会把任何一张有真实问题的表洗成"通过"） |

> 「通过表数不变」是一条重要的安全性质：本次修复**没有**让任何表的结论从"不通过"翻成"通过"，
> 因此**不存在把真实问题掩盖掉的风险**。

### 5.4 定向判别矩阵（正向 / 反向 / 边界，12 例全部实测）

| 编号 | 索引定义 | 基线 R061 | 修复后 R061 | 期望 | 判定 |
|---|---|---|---|---|---|
| P1 | `` KEY `idx_cust` `` | 命中 | **不命中** | 不命中 | ✅ 误报消除 |
| P2 | `KEY idx_cust` | 不命中 | 不命中 | 不命中 | ✅ 无回归 |
| P4 | `` UNIQUE KEY `uk_cust` `` | 不命中 | 不命中 | 不命中 | ✅（UNIQUE 走不到，见 §6.1） |
| E1 | `` KEY `IDX_CUST` `` | 命中 | **不命中** | 不命中 | ✅ 大小写不敏感仍生效 |
| E4 | `` KEY `idx_cust_no` `` | 命中 | **不命中** | 不命中 | ✅ |
| **N1** | `` KEY `cus_IDX1` `` | 命中 | **仍命中** | 仍命中 | ✅ **反向鉴别：真实违规不放过** |
| **N2** | `KEY cus_IDX1` | 命中 | **仍命中** | 仍命中 | ✅ **反向鉴别** |
| **E2** | `` KEY `idx_a`, KEY `bad_b` `` | 命中 | **仍命中** | 仍命中 | ✅ **混合场景：只放过合规的那个** |
| E3 | 无索引 | 不命中 | 不命中 | 不命中 | ✅ |
| E5 | `` KEY ` idx_cust ` `` | 命中 | **不命中** | — | ⚠️ 已知边界，见 §2.3 |

N1/N2/E2 三例修复后的告警文本实测为 `普通索引 'cus_IDX1' 应以 idx_ 开头` /
`普通索引 'bad_b' 应以 idx_ 开头`——**大小写已按原样保留**（基线为小写化的 `cus_idx1`），
§1.4 的附带缺陷同步修好。

### 5.5 全量回归（基线 vs 修复后，同环境同轮次）

```
基线   ：2 failed, 1341 passed, 29 skipped, in 70.48s
修复后 ：2 failed, 1341 passed, 29 skipped, in 69.90s
```

两处失败**两侧同名、同因**，均为环境依赖（慢查询日志未开启），**与本改动无关**：

- `tests/test_uat_round2_db.py::TestUAT47_SlowQueryFetch::test_uat47_05_slow_query_config`
- `tests/test_uat_round2_db.py::TestUAT53_EndToEndWorkflows::test_uat53_02_slow_query_workflow`

29 skipped 为需要 `TDSQL_TEST_ADMIN_USER` / `TDSQL_TEST_ADMIN_PASSWORD` 的用例，
本机未配置该组环境变量所致；**收集总数两侧一致**，数据可比。

> ✅ **零回归：passed / failed / skipped 三项与基线逐项相同。**

### 5.6 既有测试审计——为什么"一条都不用改"

逐条审计了全部可能受影响的测试点：

| 位置 | 内容 | 是否受影响 | 依据 |
|---|---|---|---|
| `tests/test_sit_v1_rules.py:327-337` | R061 用例，`KEY bad_index_name (name)` | ❌ 不受影响 | **裸名**且是**真实违规**，修复后仍命中 |
| `tests/rule_audit_materials/sql_audit/03_index.sql:46-57` | `R061_01`，`INDEX my_index (c1)` | ❌ 不受影响 | **裸名**且是**真实违规**；该文件带反引号索引名计数 = 0 |
| `tests/test_parser_tdsql_dialect_fallback.py:71-89` | D6，`` KEY `cus_bas_corp_contact_IDX1` `` | ❌ 不受影响 | `*_IDX1` 是**真实违规**，修复后仍命中；断言本身也是宽松的 `len(structural_hits) > 0` |
| `tests/test_sit_v1_engines.py:598-603` | `` KEY `idx_event` `` | ❌ 不受影响 | 该 DDL 只喂给 `parse_shard_key_from_ddl()`，**从不进入规则引擎** |
| `tests/test_r077_r054_tdsql_syntax.py` | 多处带反引号索引名 | ❌ 不受影响 | 45 例全通过；断言针对 R077/R054，不涉 R061 |

**审计结论：不存在任何一条把"带反引号的合规索引名应报 R061"写进断言的用例。**
这与 v1.6.2.0 的情形不同（当时 `test_v2_syntax_truncation.py` 因 `passed` 语义必须修订），
本次**不需要修订任何既有测试**（对应 NG-8）。

### 5.7 审核物料校验器

```
基线   ：规则总数 119  已覆盖 107  未覆盖 0  断言失败 3 条
修复后 ：规则总数 119  已覆盖 107  未覆盖 0  断言失败 3 条
```

3 条失败**两侧完全相同**，全部是 `01_naming_ddl.sql` 中 `R023_01`/`R098_01`/`R116_01`
期望里多写了 `R036,R037`（**既有的测试资产缺陷，先于本次改动存在**），与 R061 无关。

---

## 6. 与既有已知缺陷的交互

### 6.1 R061 的 PRIMARY / UNIQUE 分支在真实数据下是死代码（本次不动，NG-6）

实测发现：当建表语句含 `UNIQUE KEY` 时，`parsed.indexes` 为**空列表**；
生产 14 表解析出的 29 个索引条目，类型分布为 **`{'NORMAL': 29}`**——
一个 `PRIMARY`、一个 `UNIQUE` 都没有。

这意味着 `index.py:32-37` 的 `pk_` / `uk_` 两个分支**在真实元数据下从不执行**。

- 该现象**先于本次改动存在**，本次修复**不改变**它（P4 探针两侧同为"不命中"）。
- 它属于 **ADJ-5**（`parsed.indexes` 不产出 UNIQUE 条目）的范畴，`distributed.py:679` 已有注释记录。
- **本次不碰**：动它会牵扯 R054/R077 的 `_iter_unique_indexes` 回退逻辑，违反 NG-2。

> 但仍要求把 `uk_` 分支写进新增测试（§7.1 的 U1/U2），**用于锁定"当前不命中"这一事实**，
> 使将来 ADJ-5 被修复时，这两个用例能第一时间暴露 R061 的 UNIQUE 分支开始生效。

### 6.2 新增 ADJ-9

| 编号 | 内容 | 处置 |
|---|---|---|
| **ADJ-9** | 解析器 `_parse_index_constraint()`:572 / `_parse_unique_constraint()`:604 对**索引名**未去引号，与同函数内**索引列名**（578/608）处理不一致，导致 `parsed.indexes[*]["name"]` 在数据结构层面携带引号 | **本次不修**（B 方案）。已实测 B 与 A 审核结论等价，但会改动 R019 与 R054/R077 的告警文本。留待专项，**须与 ADJ-5 一并评估** |

### 6.3 ADJ 台账现状

| 编号 | 状态 |
|---|---|
| ADJ-1 解析降级漏审 | ✅ v1.6.2.0 已修复，报告 6297 确认生效 |
| ADJ-2 / ADJ-3 `tdsql_connector` | ⏸ Phase 2（ADJ-3 为 `create_sql_upper` 未定义导致的静默失败，**仍是真实缺陷**） |
| ADJ-4 R077 宽松 OR | 🔒 用户决策：**永久关闭** |
| ADJ-5 `parsed.indexes` 不产出 UNIQUE | ⏸ 未修（本次 §6.1 触及但不动） |
| ADJ-6 BROADCAST 冲突 | 🔒 用户决策：**关闭** |
| ADJ-7 R116/R117/R118 对 HASH 不感知 | ⏸ 未修 |
| ADJ-8 `oracle_compat.clean_sql()` `--` 词法 | ⏸ 未修 |
| **ADJ-9 索引名未去引号（解析器层）** | 🆕 **本次登记，不修** |
| **R036 只认两个字面名** | 🔒 **用户决策：维持现状，不改**（NG-7） |

---

## 7. 验收测试方案

### 7.1 新增测试（新建 `tests/test_r061_index_name_quoting.py`）

**必须覆盖的用例矩阵**（与 §5.4 判别矩阵一一对应，团队规约 R-12 反向鉴别）：

| 编号 | 用例 | 断言 |
|---|---|---|
| P1 | `` KEY `idx_cust` (`cust_no`) `` | R061 **不命中** ← 核心修复点 |
| P2 | `KEY idx_cust (cust_no)` | R061 不命中（裸名回归保护） |
| E1 | `` KEY `IDX_CUST` (`cust_no`) `` | R061 不命中（大小写不敏感） |
| E4 | `` KEY `idx_cust_no` (`cust_no`) `` | R061 不命中 |
| **N1** | `` KEY `cus_IDX1` (`cust_no`) `` | R061 **命中**，且消息含 `cus_IDX1`（**原始大小写**） |
| **N2** | `KEY cus_IDX1 (cust_no)` | R061 **命中** |
| **N3** | `` KEY `idx_a` (`a`), KEY `bad_b` (`b`) `` | R061 **命中**，且消息指向 `bad_b`（**不是** `idx_a`） |
| E3 | 无任何索引 | R061 不命中 |
| **U1** | `` UNIQUE KEY `uk_code` (`code`) `` | R061 不命中（**锁定 §6.1 现状**） |
| **U2** | `` UNIQUE KEY `ux_code` (`code`) `` | R061 不命中（**锁定 §6.1 现状**，附注释说明这是 ADJ-5 的表现，非期望语义） |
| **G1** | 生产 #1 `big_audit_trail` 原样 DDL | R061 **不命中**，且 R029/R036/R037 **仍命中**（证明只减 R061） |
| **G2** | 生产 #5 `cus_name_list_type` 原样 DDL | R061 **仍命中**（证明真实违规不放过） |

> **U1/U2 的断言必须配注释**，写明"当前不命中是因为 `parsed.indexes` 不产出 UNIQUE 条目（ADJ-5），
> 而非 R061 认可 `ux_` 前缀；若此断言将来失败，说明 ADJ-5 已被修复，需重新评估 R061 的 UNIQUE 分支"。
> 否则后人会误读成"规则允许 `ux_`"。

### 7.2 需修订的既有测试

**无。** 依据见 §5.6。这是一条**硬约束**：施工中若发现必须修改既有测试才能通过，
说明改动超出了设计范围，**必须停下来复核，不得擅自改测试迁就代码**（NG-8）。

### 7.3 回归门槛（准出条件）

| 门槛 | 要求 |
|---|---|
| G-1 | `pytest tests/` 全量：**2 failed / 1341 passed / 29 skipped**，失败项与基线**同名同因**（慢查询日志环境依赖） |
| G-2 | `tests/test_r077_r054_tdsql_syntax.py` **45 passed**，零失败 |
| G-3 | `tests/test_parser_tdsql_dialect_fallback.py` **14 passed**，零失败 |
| G-4 | `tests/test_sit_v1_rules.py -k r061` 通过 |
| G-5 | 新增 `tests/test_r061_index_name_quoting.py` **12 例全通过**，**零 skip** |
| G-6 | `python tests/rule_audit_materials/verify_rules.py`：规则总数 119 / 已覆盖 107 / 未覆盖 0 / 断言失败 **3 条**（与基线同名同因） |
| G-7 | 全语料全规则漂移：**除 R061 外零变化**，且所有变化均为"仅移除"、**无任何新增规则** |
| G-8 | 生产 14 表回放：R061 命中数 14 → **3**；#3/#4/#5 **必须**仍命中 |
| G-9 | 生产 14 表"通过表数"**保持 0**（不得把任何表洗成通过） |

---

## 8. 风险与回滚

| 风险 | 等级 | 说明与缓解 |
|---|---|---|
| 漏报风险（把真实违规放过） | **低** | 判定口径未放宽，只是把输入从"带引号"还原成"不带引号"。N1/N2/N3/G2 四例反向鉴别锁定 |
| 影响其他规则 | **极低** | 改动完全封闭在 `R061.check()` 函数体内，不触碰共享数据结构；实测 R019 告警文本逐字未变 |
| 影响 v1.6.1.9 的 R054/R077 | **无** | `distributed.py` 一个字符未动；45 例专项测试全通过 |
| 用户侧告警数量下降引发疑虑 | **需沟通** | 报告 6297 口径下违规条目 43 → 32（−25.6%），**下降的全部是误报**。建议在增量更新说明中列出 §5.3 表格 |
| E5 病理写法不再报 | **可忽略** | 已在 §2.3 显式声明，且实测生产 29 个索引名无一为该形态 |

**回滚方案**：本次改动为单文件 4 行，`git revert` 单个 commit 即可完全回退，
无数据迁移、无配置变更、无接口变更、无前端联动。

---

## 9. 施工检查单（Q 逐项打勾）

- [ ] **C-1** 仅修改 `backend/engine/rules/index.py`，`git diff --stat` 应显示 **1 file changed**
- [ ] **C-2** 改动完全位于 `R061IndexNaming.check()` 函数体内，未触碰 R062-R068（NG-4）
- [ ] **C-3** 去引号字符集逐字为 ``'`"\' '``，与 `naming.py:98`、`distributed.py:187` 一致（§2.3）
- [ ] **C-4** 三处告警文案均改用 `raw_name`（保留原始大小写），未遗漏任一分支（§1.4）
- [ ] **C-5** 未新增 import、未新增模块级常量
- [ ] **C-6** `parser_legacy.py` / `distributed.py` / `dml.py` **零改动**（NG-1/2/3）
- [ ] **C-7** 新建 `tests/test_r061_index_name_quoting.py`，覆盖 §7.1 全部 12 例，**零 skip**
- [ ] **C-8** U1/U2 两例已按 §7.1 要求配上 ADJ-5 说明注释
- [ ] **C-9** **未修改任何既有测试文件**；若确需修改，停工并回报（NG-8）
- [ ] **C-10** G-1 ~ G-9 九道门槛逐条实测通过，并在提交说明中贴出实测数字
- [ ] **C-11** 导入自检：`python -c "from backend.engine.rules.index import R061IndexNaming"` 无异常（团队规约 R-17）
- [ ] **C-12** **版本号补齐**：`VERSION` 与 `backend/config.py:25 APP_VERSION` 当前仍为 `1.6.1.9`——
      **v1.6.2.0 上线时漏改**。本次一并更新为 `1.6.2.1`
- [ ] **C-13** 提交信息格式：`fix(v1.6.2.1): R061 索引名反引号未剥离导致系统性误报修复`

---

## 附录 A：实测证据清单

| 编号 | 证据 | 结论 |
|---|---|---|
| A-1 | 生产 6297 十四表逐表回放（基线 vs 修复后） | R061 14→3；11 处变化全为"仅移除 R061" |
| A-2 | 控制变量探针 P1/P2（同名，仅差反引号） | 命中/不命中，直接坐实根因 |
| A-3 | 全语料 193 条 × 119 规则漂移 | 变化 0 条；解析异常 0/0 |
| A-4 | 12 例判别矩阵（含 3 例反向鉴别 + 1 例混合场景） | 误报全消、真实违规全保 |
| A-5 | A 方案 vs B 方案全量对比 | 审核结论**完全等价**；B 额外改动 R019 告警文本 |
| A-6 | R019 告警文本三方对比（基线/A/B） | A 与基线**逐字相同** |
| A-7 | 全量回归双侧对比 | 2 failed / 1341 passed / 29 skipped，**逐项一致** |
| A-8 | `verify_rules.py` 双侧对比 | 119/107/0/3，**逐项一致**，3 条失败为既有资产缺陷 |
| A-9 | 生产 14 表索引类型分布 | `{'NORMAL': 29}`——PRIMARY/UNIQUE 分支为死代码（§6.1） |
| A-10 | 既有测试逐条审计（5 处） | 无一条依赖缺陷行为 → 无需修订测试 |
| A-11 | `03_index.sql` 带反引号索引名计数 | **0**——语料缺少生产形态覆盖，故须补 §7.1 用例 |
| A-12 | **文档代码块自验证**：抽取 §3.1「改动后」块施工到干净工作树 | 语法/导入/行为/全量回归四项与原型逐项一致，文档可直接照抄 |

---

## 附录 B：给智能体 Q 的三句话

1. **这次只做减法。** 修复后任何一条语句都不应该**多出**任何告警；
   如果你的实现让某条语句多出了任何规则命中，那一定是错的。
2. **不要改测试。** §5.6 已逐条审计过，既有测试**没有一条**依赖缺陷行为。
   一旦你发现"必须改测试才能过"，请停下来找 A 复核，不要改测试迁就代码。
3. **`distributed.py` 和 `parser_legacy.py` 一个字符都别碰。** 它们分别是 v1.6.1.9 和 v1.6.2.0
   刚刚稳定下来的代码，本次修复完全不需要它们。
