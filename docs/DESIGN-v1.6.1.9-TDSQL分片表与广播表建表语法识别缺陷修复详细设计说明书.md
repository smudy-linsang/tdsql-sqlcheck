# DESIGN-v1.6.1.9 TDSQL 分片表与广播表建表语法识别缺陷修复详细设计说明书

| 项 | 内容 |
|---|---|
| 版本 | v1.6.1.9（在 v1.6.1.8 基础上） |
| 缺陷等级 | **P0——核心能力误报**（SQL 审核规则对合规 TDSQL 建表语法误判为违规） |
| 缺陷来源 | 内网生产环境 v1.6.1.8 运行期用户反馈；证据文件 `Extracted_Schema_Report_6261.html`（在线元数据审核报告，14 张表） |
| 影响模块 | 规则引擎 → 分布式规范规则 R077 / R054 |
| 改动文件 | **仅 1 个**：`backend/engine/rules/distributed.py` |
| 改动类 | **仅 2 个**：`R077CreateTableMustHaveShardKey`、`R054ShardKeyMustBePrimaryKey` |
| 撰写 | 智能体 A |
| 修订 | **Rev.C**——ADJ-4 经用户决策关闭、ADJ-5 升级为永久禁令（见 §8.1、§11 修订记录） |
| 评审 | 待智能体 O 评审；可复现验证脚本见**附录 B** |
| 状态 | **待评审——未动任何代码** |

---

## 0. 一句话结论

TDSQL 真实内核输出的两种合规建表语法——分片表的 `TDSQL_DISTRIBUTED BY HASH(col)` 与广播表（全局表）的 `shardkey=noshardkey_allset`——在规则引擎里**从来没有被认识过**（全仓库检索：这两个 token 在 `backend/` 下零出现）。R077 因此把合规分片表判成"未声明分片键的单表"，把广播表的哨兵值 `noshardkey_allset` 当成一个**真实列名**去查主键，必然查不到，于是 R077 与 R054 双双误报。修复方式是在这两条规则内部补齐对这两种语法的识别，**不动解析器、不动元数据采集、不动其余 117 条规则，也不放宽任何既有的合规判据**。

---

## 1. TDSQL 分片表的合规判据（本设计的判定基准）

> 本节由用户在评审中明确给定，是全文一切判定的**唯一基准**，施工时不得自行放宽。

一张 TDSQL 分片表合规，需**同时**满足：

| 编号 | 判据 |
|---|---|
| **J-1** | 声明了分片键。语法形态有两种：`shardkey=col`，或 `TDSQL_DISTRIBUTED BY HASH(col)` |
| **J-2** | **分片键必须是主键、或主键的一部分** |
| **J-3** | 若该表还有主键之外的唯一索引，**分片键还必须是每一个唯一索引的一部分** |

广播表（全局表）不适用 J-2/J-3，其声明形态为 `shardkey=noshardkey_allset` 或 `BROADCAST` 关键字。

### 1.1 三条必须记住的否定判据

| 编号 | 内容 | 为什么要单列 |
|---|---|---|
| **NJ-1** | **普通索引（`KEY`）含分片键，不构成合规** | 现场 #3 里的 `` KEY `cus_bas_corp_contact_IDX1` (`CUST_NO`,`DATA_VALID_TM`) `` 是**普通索引**，它对合规性**不起任何作用**。#3 之所以合规，靠的是 `PRIMARY KEY (`ID`,`CUST_NO`)`——`cust_no` 在主键里 |
| **NJ-2** | **分片键只在唯一索引里、不在主键里，不构成合规** | 判据是 J-2 **且** J-3，不是"主键 **或** 唯一索引"。R077 现有实现的 `或` 口径比真实约束宽松（见 §8 ADJ-4），本次**不予放宽、不予收紧**，原样保留 |
| **NJ-3** | J-3 是**每一个**唯一索引都要满足，不是"任意一个" | 这正是 R054 现有 E2 分支的口径 |

### 1.2 现场 #3 按判据逐条核对（实测）

```
PRIMARY KEY (`ID`,`CUST_NO`),
KEY `cus_bas_corp_contact_IDX1` (`CUST_NO`,`DATA_VALID_TM`),     ← 普通索引，与合规性无关
KEY `cus_bas_corp_contact_IDX2` (`CONTACT_NO`,`DATA_VALID_TM`),  ← 普通索引，与合规性无关
) ENGINE=InnoDB ... TDSQL_DISTRIBUTED BY HASH(`cust_no`)

实测该表 UNIQUE 索引数量 = 0
```

| 判据 | 核对 | 结论 |
|---|---|---|
| J-1 | `TDSQL_DISTRIBUTED BY HASH(`cust_no`)` | ✅ 已声明 |
| J-2 | `cust_no` ∈ 主键 `(ID, CUST_NO)` | ✅ 满足 |
| J-3 | 无主键外唯一索引 | ✅ 空条件成立 |

**→ #3 合规，R077 属误报。** 且它的合规**完全来自 J-2（主键），与两个普通 `KEY` 无关**。

---

## 2. 现场与复现

### 2.1 用户报告的两项

| 报告序号 | 表名 | 尾部语法 | 实际触发 | 判定 |
|---|---|---|---|---|
| #3 | `cus_bas_corp_contact` | `TDSQL_DISTRIBUTED BY HASH(\`cust_no\`)` + `PRIMARY KEY (\`ID\`,\`CUST_NO\`)` | R077 (ERROR) | **合规分片表，误报** |
| #5 | `cus_name_list_type` | `shardkey=noshardkey_allset` | R077 (ERROR) + R054 (WARNING) | **合规广播表（全局表），均误报** |

### 2.2 本地 1:1 复现（实测，非推断）

把报告里 14 张表的原始 DDL 逐条灌回本地引擎（`instance_type="distributed"`，`table_metadata=None`，与在线元数据审核通道口径一致），**引擎命中的规则集与生产报告逐字一致**：

| 序号 | 表名 | 引擎命中 | 报告命中 | 一致性 |
|---|---|---|---|---|
| #3 | `cus_bas_corp_contact` | `[R077]` | `[R077]` | ✅ |
| #4 | `cus_bas_corp_contact_addr_20260511` | `[R001,R036,R037,R061,R077]` | 同左 | ✅ |
| #5 | `cus_name_list_type` | `[R036,R037,R054,R061,R077]` | 同左 | ✅ |
| #8 | `t_branch` | `[R036,R054,R061,R077]` | 同左 | ✅ |
| #11 | `t_dict` | `[R036,R054,R061,R063,R077]` | 同左 | ✅ |
| #13 | `t_product` | `[R036,R054,R061,R063,R077]` | 同左 | ✅ |

> **本次缺陷的真实影响面比用户报告的两项更大**：报告 14 张表中，**#5、#8、#11、#13 共 4 张广播表**全部同时误报 R077+R054，**#3** 误报 R077。合计 **5 张表被误报，占全表 35.7%**。

### 2.3 反向鉴别基准（必须保持触发的对照）

**#4 `cus_bas_corp_contact_addr_20260511`** 尾部没有任何分片/广播声明（以 `COMMENT='…'` 结束），是**真正的单表**，R077 触发**正确**。本设计的全部验收都以"#4 必须继续报 R077"为反向鉴别锚点——若修完 #4 也不报了，等于把 R077 废掉，属于施工失败。

---

## 3. 根因分析

### 3.1 缺陷 A：R077 不认识 `TDSQL_DISTRIBUTED BY HASH(col)`（违反 J-1）

`R077._extract_shard_key()`（`distributed.py:572-586`）**只有三个取值来源**：

| 顺序 | 来源 | 对 #3 的实测结果 |
|---|---|---|
| 1 | `parsed.table_options["SHARDKEY"]` / `["SHARD_KEY"]` | `table_options == {}`（空） |
| 2 | `_SHARDKEY_RE` = `\bshardkey\b\s*=?\s*...` | 不匹配 |
| 3 | `_SHARD_KEY_RE` = `\bshard_key\b\s*=?\s*...` | 不匹配 |

三者全空 → `shard_key_col == ""` → 走进"未声明分片键"分支 → **ERROR 误报**。

两个关键细节：

1. `_SHARDKEY_RE` 的 `\b` 词边界是**上一轮为防止列名子串误匹配特意加的**，它恰好也把 `TDSQL_DISTRIBUTED` 里的 `DISTRIBUTED` 挡在门外——即使去掉 `\b` 也匹配不上，因为这个语法里根本没有 `shardkey` 这个词。
2. `_BROADCAST_RE`（`\bbroadcast\b`）同样不匹配，所以广播表快速通道也拦不住。

**全仓库检索证据**：`TDSQL_DISTRIBUTED` 这个 token 在整个代码库（含 `backend/`、`tests/`、`docs/`）**零出现**。这不是"正则写窄了"，是"这个语法从未进入过设计视野"。

> **注意**：本缺陷是**取不到分片键**，不是"判据放宽"。补上识别之后，J-2/J-3 的判定照常进行——现场 #3 能通过，是因为它**真的满足 J-2**（`_collect_pk_cols` 实测为 `['cust_no','id']`），而不是因为放行了什么。

### 3.2 缺陷 B：`noshardkey_allset` 被当成列名

TDSQL 对广播表（全局表）在 `SHOW CREATE TABLE` 尾部输出 `shardkey=noshardkey_allset`。`noshardkey_allset` 是内核的**哨兵值**，语义是"本表无分片键"，**它不是列名**。

实测 #5 的解析结果：

```
table_options = {'engine':'INNODB', 'charset':'UTF8MB4',
                 'COMMENT':'特殊名单主题-名单类型管理表',
                 'SHARDKEY':'noshardkey_allset'}      ← sqlglot 正常解析出来了
_extract_shard_key = 'noshardkey_allset'              ← 被当成列名取走
_collect_pk_cols   = ['id']                            ← 主键是 ID
```

于是：

- **R077** 走到第二分支：`'noshardkey_allset' not in {'id'}` → 报 *"分片键 'noshardkey_allset' 不在主键或唯一索引中"*。
- **R054** 走 raw_sql 回退正则 `shardkey\s*=\s*['"\`]?(\w+)` 同样取到 `noshardkey_allset` → 报 *"分片键 'noshardkey_allset' 不在主键中"*。

**这是一个必然失败的判定**：广播表不适用 J-2/J-3，这个哨兵值永远不可能出现在主键里，误报 100% 复现。

**全仓库检索证据**：`noshardkey` / `allset` 在 `backend/engine/` 下**零出现**（`backend/services/` 下的 `/*sets:allsets*/` 是 TDSQL 查询提示，与此无关）。

**附加事实**：`R054` 从头到尾**没有任何 BROADCAST 放行分支**——R077 至少有 `_BROADCAST_RE` 快速通道，R054 连这个都没有。所以哪怕一张表老老实实写了 `BROADCAST` 关键字，只要同时出现 `shardkey=` 字样，R054 依然会误报。本次一并收口。

### 3.3 共同根因

| | |
|---|---|
| **根因** | R077/R054 的分片键识别逻辑，是按**开发人员手写的建表 SQL**形态设计的（`SHARDKEY=id`），从未按**TDSQL 内核 `SHOW CREATE TABLE` 的真实输出**形态验证过 |
| **触发条件** | v1.6.x 新增「在线元数据审核」，第一次把内核原样输出的 DDL 直接喂进规则引擎，形态差异立刻暴露 |
| **为何测试没拦住** | 全部规则物料（`tests/rule_audit_materials/sql_audit/*.sql`）是手写形态；仓库内 17 个 `.sql` 文件、201 条语句中，`TDSQL_DISTRIBUTED`、`noshardkey_allset` **一条都没有** |

---

## 4. 修复方案与范围边界

### 4.1 本次实施（Phase 1）——两个改动点，全部落在一个文件的两个类里

| 编号 | 位置 | 改动 | 方向性质 |
|---|---|---|---|
| **FIX-1** | `R077._extract_shard_key()` | 追加**第 4 个**取值来源：`TDSQL_DISTRIBUTED BY HASH/KEY(col)` | 仅在前 3 个来源**全空**时才可达；取到之后 J-2/J-3 判定照常执行 |
| **FIX-2** | `R077.check()` + `R054.check()` | 识别 `noshardkey*` 哨兵值 → 判为广播表 → 放行 | 仅在分片键取值命中哨兵时可达 |

**本次不放宽任何合规判据。** FIX-1 补的是"看不看得见分片键"，不是"分片键合不合格"；FIX-2 处理的是"这张表根本不是分片表"。

### 4.2 明确的非目标（本次绝不触碰）

| # | 不做什么 | 为什么不做 |
|---|---|---|
| **NG-1** | **不修改 `backend/engine/parser/parser_legacy.py`** | 净化 `TDSQL_DISTRIBUTED` 尾子句会让 sqlglot 从"降级为 Command"变成"完整解析"，**全部 119 条规则**看到的输入结构随之改变（见 §7.3 实测）。爆炸半径与本次缺陷不成比例，单独排期 |
| **NG-2** | **不修改 `tdsql_connector.py` 的 `_detect_shard_info()` / `parse_shard_key_from_ddl()`** | 它们同源带病（§8），但服务的是 R020/R021/R022/R053/R056/R057/R060 的元数据通道与「大表治理」。改它等于一次性动 7 条规则 + 1 个业务模块 |
| **NG-3** | **不给 R054 增加 `TDSQL_DISTRIBUTED` 识别** | R054 当前对该语法**取不到分片键、直接返回 None**，属"漏报"而非"误报"。补上会让**过去不报的语句开始报**——这是行为扩张，不是缺陷修复。R077 已覆盖 J-2 且消息是超集 |
| **NG-4** | **不修改 R054 的 UNIQUE 正则** | R054 的 E2 分支是**产出违规**的分支（J-3 判据），放宽正则会让它发现更多唯一索引 → 新增违规 |
| **NG-5** | **不放宽 R077 的 `_UNIQUE_RE`（永久约束，非仅本次）** | **Rev.B 提出、Rev.C 升级为永久禁令。** 该正则服务于 R077 的"主键 **或** 唯一索引"口径；按 J-2/NJ-2，分片键在唯一索引里但不在主键里**并不合规**。且 ADJ-4 已由用户决策关闭，**这个"坏正则"正是 R077 在真实 TDSQL 元数据上贴合 J-2 的唯一原因——它是承重的**。放宽它会立即制造漏报。**实测论证见 §8.1，务必先读再动手** |
| **NG-6** | **不改动任何规则的 `severity` / `enabled` / `instance_scope`** | 规则元数据一旦变动会穿透规则集、门禁、报表统计 |
| **NG-7** | **不改动 R077/R054 之外的任何一条规则** | 报告中同时出现的 R001/R036/R037/R061/R063 均为独立正确判定，与本缺陷无关 |

### 4.3 R054 与 R077 的口径差异（施工时不得"顺手统一"）

对照 §1 的判据：

| | R077 (ERROR) | R054 (WARNING) | 对照 J-2/J-3 |
|---|---|---|---|
| 主键判定 | 分片键 ∈ 主键 **∪ 唯一索引**（`或`） | 分片键 ∈ **主键** | **R054 与 J-2 一致；R077 偏宽松** |
| 唯一索引判定 | 无独立判定 | **每一个**唯一索引都须含分片键 | **R054 与 J-3 一致；R077 无此判定** |

> **结论：真正贴合 TDSQL 约束的是 R054，R077 的 `或` 口径偏宽松、且缺 J-3 判定。** 这是一个**既有的口径缺陷**（ADJ-4），但收紧 R077 会让过去不报的语句开始报 ERROR，属行为扩张，**本次一律不动**，原样保留。施工时不得为了"让两条规则看起来一致"而修改任何一边。

---

## 5. 详细设计（照图施工）

> 全部改动位于 `backend/engine/rules/distributed.py`。行号基于当前 `main`（commit `5cbafa4`）。

### 5.1 改动点 1：新增两个模块级常量

**位置**：文件顶部 import 区之后、`class R020ShardKeyInWhere` 之前，即**第 23 行（最后一条 `from backend.models import ...`）与第 26 行（`class R020ShardKeyInWhere`）之间的空行区**。

**新增内容**（原样插入）：

```python
# ═══════════════════════════════════════════════════════════════
# TDSQL 内核建表语法补充识别（v1.6.1.9）
#
# TDSQL 的 SHOW CREATE TABLE 对分片表/广播表存在两种输出形态，均非
# `SHARDKEY=col` 写法，v1.6.1.8 之前不被识别，导致 R077/R054 误报：
#
#   分片表:  ) ENGINE=InnoDB ... TDSQL_DISTRIBUTED BY HASH(`cust_no`)
#   广播表:  ) ENGINE=InnoDB ... shardkey=noshardkey_allset
#
# `noshardkey_allset` 是内核表示"本表无分片键"的哨兵值，不是列名，
# 拿它去比对主键必然失败，是误报的直接成因。
#
# 注意: 这两个常量只解决"能否识别出分片键"，不改变"分片键是否合格"
# 的判定——分片键必须在主键中（且必须在每个唯一索引中）的既有口径
# 原样保留，不得借本次改动放宽。
#
# 来源：内网 v1.6.1.8 生产环境在线元数据审核报告（14 表中 5 张误报）
# ═══════════════════════════════════════════════════════════════

# 分片表：TDSQL_DISTRIBUTED BY HASH(col) / BY KEY(col)
_TDSQL_DISTRIBUTED_RE = re.compile(
    r"\btdsql_distributed\s+by\s+(?:hash|key)\s*\(\s*[`\"']?([a-z_][a-z0-9_]*)[`\"']?\s*\)",
    re.IGNORECASE,
)
# 广播表(全局表)哨兵值：shardkey=noshardkey_allset。用前缀式匹配兼容
# 内核可能的 noshardkey_* 变体；代价是名为 noshardkey* 的真实列会被
# 判为广播表——该命名与 TDSQL 保留语义冲突，实际不可能出现，接受此取舍。
_NOSHARDKEY_SENTINEL_RE = re.compile(r"^noshardkey(?:_[a-z0-9_]+)?$", re.IGNORECASE)
```

> **施工注意**：`re` 已在第 18 行 import，不需新增 import（团队规约 R-17）。

---

### 5.2 改动点 2：R077 `check()` 插入哨兵放行（FIX-2）

**位置**：`distributed.py:543-546`。

**改前**：

```python
        # 提取分片键列名（优先使用解析器结构化数据，回退到正则）
        shard_key_col = self._extract_shard_key(parsed, raw_sql)

        if not shard_key_col:
```

**改后**：

```python
        # 提取分片键列名（优先使用解析器结构化数据，回退到正则）
        shard_key_col = self._extract_shard_key(parsed, raw_sql)

        # v1.6.1.9: 广播表(全局表) —— TDSQL 以 shardkey=noshardkey_allset
        # 表达"本表无分片键"，该值是哨兵而非列名，主键/唯一索引约束均不适用
        if shard_key_col and _NOSHARDKEY_SENTINEL_RE.match(shard_key_col):
            return None

        if not shard_key_col:
```

**可达性**：仅当 `_extract_shard_key` 返回值形如 `noshardkey*` 时才生效。改前这类语句 100% 落入"分片键不在主键或唯一索引中"分支。**改动前后的差集恰好等于误报集合**。

---

### 5.3 改动点 3：R077 `_extract_shard_key()` 追加第 4 来源（FIX-1）

**位置**：`distributed.py:572-586`。

**改前**：

```python
    def _extract_shard_key(self, parsed: ParsedSQL, raw_sql: str) -> str:
        """提取分片键列名，优先使用解析器结构化数据，回退到正则"""
        # 优先来源: parsed.table_options（sqlglot 已解析的表选项）
        for key in ("SHARDKEY", "SHARD_KEY"):
            val = parsed.table_options.get(key, "")
            if val:
                return val.strip('`"\' ').lower()
        # 回退来源1: SHARDKEY 正则
        shard_match = self._SHARDKEY_RE.search(raw_sql)
        if not shard_match:
            # 回退来源2: shard_key 正则
            shard_match = self._SHARD_KEY_RE.search(raw_sql)
        if shard_match:
            return shard_match.group(1).strip('`"\' ').lower()
        return ""
```

**改后**（仅把末尾 `return ""` 替换为新增来源，前面一字不动）：

```python
    def _extract_shard_key(self, parsed: ParsedSQL, raw_sql: str) -> str:
        """提取分片键列名，优先使用解析器结构化数据，回退到正则"""
        # 优先来源: parsed.table_options（sqlglot 已解析的表选项）
        for key in ("SHARDKEY", "SHARD_KEY"):
            val = parsed.table_options.get(key, "")
            if val:
                return val.strip('`"\' ').lower()
        # 回退来源1: SHARDKEY 正则
        shard_match = self._SHARDKEY_RE.search(raw_sql)
        if not shard_match:
            # 回退来源2: shard_key 正则
            shard_match = self._SHARD_KEY_RE.search(raw_sql)
        if shard_match:
            return shard_match.group(1).strip('`"\' ').lower()
        # v1.6.1.9 回退来源3: TDSQL_DISTRIBUTED BY HASH/KEY(col)
        # 该语法下 sqlglot 整条降级为 Command，table_options 为空，
        # 前两个正则也不匹配，故必须走到这里才能拿到分片键。
        # 取到后仍照常执行"分片键须在主键/唯一索引中"的既有判定。
        td_match = _TDSQL_DISTRIBUTED_RE.search(raw_sql)
        if td_match:
            return td_match.group(1).strip('`"\' ').lower()
        return ""
```

**关键的范围控制性质**：新分支被放在**函数最末尾**，只有前 3 个来源**全部返回空**时才可达。而前 3 个来源全空时，改前的行为是**必定报"未声明分片键"**。因此：

> 本改动的行为差集 = {前 3 源全空} ∩ {含 `TDSQL_DISTRIBUTED BY HASH/KEY(...)`}
> ——即**恰好且仅有**当前被误报的那一类语句。任何其它语句逐字不变。

---

### 5.4 改动点 4：R054 `check()` 插入哨兵放行（FIX-2）

**位置**：`distributed.py:262-268`（第二个 `if not shard_key: return None` 之后、`# 检查主键是否包含分片键` 之前）。

**改前**：

```python
        if not shard_key:
            # raw_sql 回退提取 shardkey=xxx
            sk_match = re.search(r"shardkey\s*=\s*['\"`]?(\w+)", parsed.raw_sql, re.IGNORECASE)
            if sk_match:
                shard_key = sk_match.group(1)
        if not shard_key:
            return None

        # 检查主键是否包含分片键
```

**改后**：

```python
        if not shard_key:
            # raw_sql 回退提取 shardkey=xxx
            sk_match = re.search(r"shardkey\s*=\s*['\"`]?(\w+)", parsed.raw_sql, re.IGNORECASE)
            if sk_match:
                shard_key = sk_match.group(1)
        if not shard_key:
            return None

        # v1.6.1.9: 广播表(全局表) —— noshardkey_allset 是"本表无分片键"的
        # 哨兵值而非列名，主键/唯一索引约束均不适用，直接放行。
        # 该判定同时覆盖 table_metadata 通道（_detect_shard_info 会把哨兵值
        # 原样写进 meta["shard_key"]）与 raw_sql 正则通道。
        if _NOSHARDKEY_SENTINEL_RE.match(shard_key.strip('`"\' ')):
            return None

        # 检查主键是否包含分片键
```

**覆盖两条通道**：放在 `shard_key` 取值汇合点之后，**元数据通道**（`backend/api/tdsql_manage.py:559` 的即时审核带连接场景）与 **raw_sql 通道**（在线元数据审核 / 文件审核）同时收口，无需改动 `_detect_shard_info`。

---

### 5.5 改动点 5（**可选，纯注释，零行为变化**）：为 `_UNIQUE_RE` 加地雷警示

**背景**：§8.1 论证了 `_UNIQUE_RE` 认不出反引号是**承重**的。但代码现场看不出这一点——后来者极易把它当成一个显而易见的正则 bug 顺手"修好"，从而静默制造漏报。

**位置**：`distributed.py:519`（`# 表级 UNIQUE KEY/INDEX 列提取正则（回退方案）` 那一行的上方）。

**建议新增**（**只加注释，不改任何一个字符的正则**）：

```python
    # ⚠️ 禁止放宽本正则（v1.6.1.9 决策，来源见
    #    docs/DESIGN-v1.6.1.9-...说明书.md §8.1）
    #    它认不出反引号索引名，看起来像 bug，实际是承重的：
    #    R077 采用"分片键 ∈ 主键 或 唯一索引"的宽松口径（ADJ-4，已决策
    #    永久保留），只有当本正则在真实 TDSQL 元数据上取不到唯一索引时，
    #    该判定才恰好等价于"分片键必须在主键中"这一真实约束。
    #    放宽本正则 = 激活 OR 分支 = 立即产生漏报。
    #    若确需修改，必须与 ADJ-4 一起收紧，二者是原子决策。
```

**性质**：纯注释，**零行为变化**，`git diff` 只增注释行。

> **本条留给评审决策**：采纳则 Phase 1 为「4 处行为改动 + 1 处注释」；不采纳则为「4 处行为改动」，§8.1 的约束仅存在于本文档中。**建议采纳**——文档会被遗忘，代码旁的注释不会。

---

### 5.6 改动汇总

| # | 文件 | 类/方法 | 类型 | 净增行 |
|---|---|---|---|---|
| 1 | `distributed.py` | 模块级 | 新增 2 个常量 + 注释 | +26 |
| 2 | `distributed.py` | `R077.check()` | 新增 4 行 | +4 |
| 3 | `distributed.py` | `R077._extract_shard_key()` | 新增 7 行 | +7 |
| 4 | `distributed.py` | `R054.check()` | 新增 6 行 | +6 |

| 5 | `distributed.py` | `R077._UNIQUE_RE` 上方 | **（可选）**纯注释护栏 | +7 |

**合计：1 个文件、2 个类、4 处行为改动（+1 处可选纯注释），净增约 43（或 50）行，无删除、无正则放宽、无签名变更、无新增依赖、无 import 变更、无数据库变更、无接口变更、无前端变更。**

---

## 6. 不需要改动的部分（施工时逐项确认，防止范围蔓延）

| 对象 | 结论 | 依据 |
|---|---|---|
| `R077._UNIQUE_RE` | **不改** | NG-5。放宽会压制真实违规 |
| `R077._PK_RE` / `_SHARDKEY_RE` / `_SHARD_KEY_RE` / `_BROADCAST_RE` | **不改** | 现有行为正确，新分支追加在其后 |
| `R077._collect_pk_cols()` / `_collect_unique_index_cols()` | **不改** | 判据逻辑本体，本次不触碰 |
| `backend/engine/rules/__init__.py` | **不改** | 规则未增减 |
| `backend/engine/parser/**` | **不改** | 见 NG-1 |
| `backend/services/tdsql_connector.py` | **不改** | 见 NG-2、§8 |
| `backend/services/audit_service.py` | **不改** | 只是调用方，不含分片键判定 |
| `backend/api/**` / `backend/schema/**` / 前端 | **不改** | 无接口、schema、展示层变更 |
| R020/R021/R022/R053/R055/R056/R057/R058/R059/R060 | **不改** | 实测：这 10 条均**只从 `table_metadata` 取分片键**，不解析 DDL，与本次改动零交集 |
| 其余 107 条规则 | **不改** | 与分片键判定无关 |

---

## 7. 影响面分析（爆炸半径）

### 7.1 逻辑论证：两处改动的可达域

| 改动 | 可达前置条件 | 改前该条件下的行为 | 改后 | 是否可能压制真实违规 |
|---|---|---|---|---|
| FIX-1 | 三个既有分片键来源全空 **且** SQL 含 `TDSQL_DISTRIBUTED BY HASH/KEY(...)` | 必报"未声明分片键" | 取到分片键后按**既有** J-2/J-3 口径判定 | **否**——判据一字未改；分片键不在主键时照常报（用例 N3/N8） |
| FIX-2 | 分片键取值匹配 `^noshardkey(_\w+)?$` | 必报"不在主键中" | 放行 | **否**——广播表本就不适用 J-2/J-3 |

### 7.2 全语料实测漂移扫描

把仓库内**全部 17 个 `.sql` 文件切出的 201 条可解析语句** + **生产报告 14 张表的原始 DDL**，逐条同时灌进"改前规则"与"Rev.B 改后原型"，比对 (R077, R054) 判定：

```
扫描 .sql 文件 17 个 + 现场 14 表，可解析语句 201 条
判定发生变化: 5 条

  现场报告#3  cus_bas_corp_contact   R077/--    → --/--
  现场报告#5  cus_name_list_type     R077/R054  → --/--
  现场报告#8  t_branch               R077/R054  → --/--
  现场报告#11 t_dict                 R077/R054  → --/--
  现场报告#13 t_product              R077/R054  → --/--
```

> **发生变化的 5 条，恰好就是本次要修的 5 处误报；仓库内 201 条既有语料判定 100% 逐字不变。**（撤销 FIX-3 后该结果不变——#3 靠 J-2 通过，#8 靠哨兵在唯一索引判定之前就已返回。）

### 7.3 一个必须向用户明示的副作用（不是本次引入，但会因本次而"显形"）

#3 的 `TDSQL_DISTRIBUTED BY HASH(...)` 会让 **sqlglot 整条降级为 Command**，实测后果：

| | `columns` | `indexes` | `table_options` | 命中规则 |
|---|---|---|---|---|
| #3 原样 | **0** | 0 | 0 | `[R077]` |
| #3 摘掉尾子句（反事实实验） | 25 | 2 | 3 | `[R036,R037,R061,R077]` |
| #4 对照（结构相同、无尾子句） | 34 | 1 | 3 | `[R001,R036,R037,R061,R077]` |

也就是说：**#3 目前不仅被误报 R077，还被漏审了 R036/R037/R061**。Phase 1 修完后，#3 会显示为"零违规"——**这个"零"是干净的 R077 判定 + 仍然存在的漏审叠加出来的**，并非该表真的通过了全量审核。

- 消除漏审的唯一办法是 NG-1（解析器净化 TDSQL 方言尾子句）；
- 该改动会让**所有** `TDSQL_DISTRIBUTED` 建表语句突然被全套 DDL 规则审到，报告违规数上升，属于用户可感知的行为变化；
- 故**列为 Phase 2 单独排期，由用户决策**，不在本次夹带。

---

## 8. 已知邻接缺陷（同源，本次**不修**）

> **状态说明**：ADJ-1/2/3 建议 Phase 2 排期；**ADJ-4 已由用户决策关闭、永不排期**；**ADJ-5 因 ADJ-4 关闭而升级为「永久禁止修复」**，见下表与 §8.1。

| 编号 | 位置 | 问题 | 后果 | 建议 |
|---|---|---|---|---|
| **ADJ-1** | `parser_legacy.py` | `TDSQL_DISTRIBUTED BY ...` 导致 sqlglot 整条降级为 Command，`columns/indexes/table_options` 全空 | 这类分片表被**全套结构类规则漏审**（实测 #3 漏掉 R036/R037/R061） | Phase 2：在交给 sqlglot 前净化方言尾子句，**`parsed.raw_sql` 必须保留原文**（R077 依赖它取分片键）。需全量回归 + 用户确认"报告违规数会上升" |
| **ADJ-2** | `tdsql_connector.py:162` `parse_shard_key_from_ddl()`、`:404` `_detect_shard_info()` | 同样只认 `SHARDKEY=`，且把 `noshardkey_allset` 原样当分片键写进 `meta.shard_key`（`parse_shard_key_from_ddl` 的 docstring 声称"broadcast 表返回 ''"，与实际不符） | ① R020/R021/R022/R053/R056/R057/R060 在元数据通道对广播表连带误报（如"分片表 t_branch 的分片键 'noshardkey_allset' 未在 WHERE 条件中"）；② 「大表治理」展示的分片键为哨兵值 | Phase 2：统一到一个共享的分片键解析工具函数 |
| **ADJ-3** | `tdsql_connector.py:1546` `TDSQLConnector._detect_shard_info()` | 与 `:404` 近似重复实现；其中引用了**未定义变量** `create_sql_upper`（该方法内定义的是 `create_sql`），触发 `NameError` 被外层 `except Exception: pass` 静默吞掉 | 该类的**广播表识别与 `TDSQL_SHARDING_RULES` 查询整段成为死代码**，`is_broadcast_table` 永不为 True | Phase 2：与 ADJ-2 一并去重收敛。**建议优先级最高——这是真实的静默失效** |
| **ADJ-4** | `R077.check()` 的判定口径 | R077 用"分片键 ∈ 主键 **或** 唯一索引"，与 §1 判据 J-2 + J-3 不符，**偏宽松且缺 J-3** | 分片键只在唯一索引、不在主键的表，R077 在**手写裸索引名**形态下漏报；在**真实 TDSQL 反引号形态**下因 ADJ-5 恰好仍会报 | **🔒 用户已决策：关闭，永不排期，不得改动。** 收紧会让存量语句大批新增 ERROR，收益不抵风险 |
| **ADJ-5** | `R077._UNIQUE_RE`、`parsed.indexes` | 唯一索引识别对反引号索引名失配（实测 `` UNIQUE KEY `uk_code` (`code`) `` → 提取结果为空）；`parsed.indexes` 也从不产出 UNIQUE 条目 | **因 ADJ-4 关闭，此"缺陷"反而是 R077 在真实 TDSQL 元数据上贴合 J-2 的唯一原因——它是承重的** | **🔒 永久禁止修复。** 单独修会立即制造漏报，详见 §8.1 |

### 8.1 ⚠️ 为什么 ADJ-5 必须永久保持"不修"——一条留给后来者的地雷警示

ADJ-4 关闭（R077 保留"主键 **或** 唯一索引"的宽松口径）之后，出现一个**反直觉但必须遵守**的结论：

> **`_UNIQUE_RE` 认不出反引号索引名这个"缺陷"，是 R077 在真实 TDSQL 元数据上仍然正确的唯一原因。它是承重的，不是待修的。**

推理：`_collect_unique_index_cols()` 的两个来源在真实 TDSQL 元数据上**同时失效**（`parsed.indexes` 从不产出 UNIQUE 条目；`_UNIQUE_RE` 不认反引号），返回恒为空集。于是 R077 的判定

```python
if shard_key_col not in pk_cols and shard_key_col not in unique_index_cols:
```

在 `unique_index_cols == set()` 时**恒等于** `if shard_key_col not in pk_cols:`——**这正好就是判据 J-2**。也就是说 R077 目前是"歪打正着地正确"。

**实测验证**（同一条 SQL，唯一变量是 ADJ-5 修不修）：

| 场景 | ADJ-5 保持不修 | ADJ-5 若被"顺手修好" |
|---|---|---|
| **TDSQL 真实形态**：`` UNIQUE KEY `uk_code` (`code`) ``，分片键 `code` ∉ 主键（违反 J-2，应报） | **报 R077** ✅ | **★漏报** ❌ |
| 手写形态：`UNIQUE KEY uk_code (code)`，分片键 ∉ 主键（违反 J-2，应报） | ★漏报（ADJ-4 关闭的既有残留） | ★漏报 |

**结论：修 ADJ-5 会在生产环境真正出现的那一种形态上直接制造漏报。** 因此：

- ❌ **任何人（含后续智能体）不得以"正则写得不对/不支持反引号"为由修改 `R077._UNIQUE_RE`**
- ❌ 不得为 `parsed.indexes` 补充 UNIQUE 条目产出（同样会激活 OR 分支）
- ✅ 若将来确需修 ADJ-5，**必须与 ADJ-4 同时收紧**（把 R077 改为 J-2 且 J-3），二者是一个原子决策，不可拆开

> 建议施工后把本条写入 `docs/GUIDE-团队施工规约.md`（该文档要求每条规约注明来源事故——本条来源即 v1.6.1.9 缺陷调查）。

### 8.2 ADJ-4 关闭后被接受的残留漏报（如实记录）

手写形态 SQL 中，`UNIQUE KEY 裸索引名 (分片键)` 且分片键不在主键时，R077 漏报（上表第 2 行）。这是 ADJ-4 关闭的直接后果，**已知、已接受、本次不处理**。R054 对该场景仍会报 WARNING，具备兜底。

---

## 9. 验收测试方案

### 9.1 正反用例矩阵（20 条，已用 Rev.B 原型全量实测通过 20/20）

> **前置条件**：全部用例均在 `instance_type="distributed"`、`table_metadata=None` 下执行（R077/R054 的 `instance_scope` 为 DISTRIBUTED，集中式实例本就跳过，由既有测试 `test_instance_scope_rules.py` 覆盖，本矩阵不重复）。
>
> 建议落库为 `tests/test_r077_r054_tdsql_syntax.py`；可复现脚本见**附录 B**。★ = 反向鉴别用例（团队规约 R-12：必须证明"没把功能删掉"）。

| 用例 | 场景 | 改前 | 期望（改后） |
|---|---|---|---|
| P1 | 现场#3：`TDSQL_DISTRIBUTED BY HASH(cust_no)` 且 `cust_no ∈ PRIMARY KEY(ID,CUST_NO)` | R077 | 零违规 |
| P2 | 现场#5 `shardkey=noshardkey_allset` 广播表 | R077+R054 | 零违规 |
| P3 | 现场#8 `t_branch` 广播表（含 UNIQUE KEY） | R077+R054 | 零违规 |
| P4 | 现场#11 `t_dict` 广播表 | R077+R054 | 零违规 |
| P5 | 现场#13 `t_product` 广播表 | R077+R054 | 零违规 |
| **N1★** | 现场#4 无任何分片声明 | R077 | **R077 仍触发** |
| **N2★** | `SHARDKEY=cust_id` 但不在主键（违反 J-2） | R077+R054 | **R077+R054 仍触发** |
| **N3★** | `TDSQL_DISTRIBUTED BY HASH(cust_no)` 但 cust_no 不在主键 | R077 | **R077 仍触发**（识别到分片键 ≠ 放行） |
| **N8★** | `TDSQL_DISTRIBUTED BY HASH(cust_no)`，cust_no **只在普通 KEY 里**、不在主键——**直接对应 NJ-1，防止把普通索引当合规依据** | R077 | **R077 仍触发** |
| **N4★** | 反引号 UNIQUE 不含分片键、分片键也不在主键 | R077+R054 | **R077+R054 仍触发** |
| **N5★** | 普通 `KEY`（非 UNIQUE）含分片键（NJ-1） | R077+R054 | **仍触发** |
| **N7★** | 表注释里含 `noshardkey_allset` 字样、真实分片键合规 | 零违规 | **零违规**（哨兵不得被注释文本诱发） |
| C1 | 合规分片表（分片键在主键，J-2 满足） | 零违规 | 零违规 |
| C2 | `BROADCAST` 关键字广播表 | 零违规 | 零违规 |
| **C3** | 分片键在**反引号** UNIQUE 中但**不在主键**（违反 J-2/NJ-2） | R077+R054 | **R077+R054 仍触发（改前改后逐字一致）** |
| C5 | `TDSQL_DISTRIBUTED BY KEY(sk)`，sk ∈ 主键 | R077 | 零违规 |
| C6 | `tdsql_Distributed  By  Hash( SK )` 大小写混排+多空格+无反引号，sk ∈ 主键 | R077 | 零违规 |
| C7 | CTAS `CREATE TABLE ... AS SELECT` | 零违规 | 零违规 |
| C8 | `CREATE TEMPORARY TABLE` | 零违规 | 零违规 |
| C9 | 非建表语句（SELECT） | 零违规 | 零违规 |

### 9.2 端到端验收（在线元数据审核通道）

把 `Extracted_Schema_Report_6261.html` 的 14 张表原样组成 `.sql`，走**在线元数据审核 / 文件审核**通道（`instance_type=distributed`），逐表比对：

| 序号 | 表 | 改前 | 验收期望 |
|---|---|---|---|
| #3 | cus_bas_corp_contact | R077 | **无 R077** |
| #4 | cus_bas_corp_contact_addr_20260511 | R001,R036,R037,R061,R077 | **原样不变（含 R077）** |
| #5 | cus_name_list_type | R036,R037,R054,R061,R077 | **R036,R037,R061** |
| #8 | t_branch | R036,R054,R061,R077 | **R036,R061** |
| #11 | t_dict | R036,R054,R061,R063,R077 | **R036,R061,R063** |
| #13 | t_product | R036,R054,R061,R063,R077 | **R036,R061,R063** |
| #1,#2,#6,#7,#9,#10,#12,#14 | 其余 8 张 | — | **逐条原样不变** |

### 9.3 回归门槛（团队规约 R-18：零跳过）

| 项 | 基线 | 门槛 |
|---|---|---|
| `test_rules.py` + `test_instance_scope_rules.py` + `test_oracle_compat_rules.py` + `test_instance_type_service.py` | **168 passed**（已实测） | **必须仍为 168 passed，0 failed** |
| 全量回归 `pytest tests/` | v1.6.1.8 基线 1312 passed / 0 failed / 1 skipped | **不得新增 failed，skipped 不得增加** |
| 规则总数 | 119（92 ALL + 27 DISTRIBUTED） | **必须仍为 119 / 92 / 27** |
| 全语料漂移扫描（§7.2 脚本） | — | **变化语句数必须 = 5，且就是 P1–P5** |

> 注：本地环境 `test_uat47_05_slow_query_config` / `test_uat53_02_slow_query_workflow` 依赖 MariaDB `slow_query_log=ON`，跑全量前需 `SET GLOBAL slow_query_log=ON; SET GLOBAL long_query_time=0.1`，否则会有 2 条环境性失败（非产品缺陷）。

---

## 10. 风险与回滚

| 风险 | 等级 | 说明与对策 |
|---|---|---|
| 名为 `noshardkey*` 的真实列被误判为广播表 | 极低 | 该命名与 TDSQL 保留语义直接冲突，现实中不存在；已在 §5.1 注释中显式记录取舍 |
| `TDSQL_DISTRIBUTED` 出现在注释/字符串里造成误放行 | 低 | 与既有 `_BROADCAST_RE`、`_SHARDKEY_RE` 同等特性（现状下表注释含 "broadcast" 已可绕过 R077），**非本次引入**；用例 N7 覆盖哨兵侧 |
| N3/N8 类语句由"不报"变"报 R077" | 低 | 这是**恢复鉴别力**（分片键确实不在主键，违反 J-2）；已列入验收矩阵明示 |
| #3 修完后显示"零违规"但实际仍被漏审 | **中** | 已在 §7.3 明示；根治需 Phase 2 (ADJ-1)。**必须在交付说明中写清楚** |
| ADJ-4 关闭后的残留漏报 | **中** | 手写裸索引名 `UNIQUE KEY uk (分片键)` 且分片键不在主键时 R077 漏报，R054 仍报 WARNING 兜底。**已知、已接受**（§8.2） |
| 后来者"顺手修好" `_UNIQUE_RE` 从而制造漏报 | **中高** | 这是本次最容易踩的地雷。对策：§8.1 实测论证 + NG-5 永久禁令 + 改动点 5 的代码旁注释 + §12 检查单硬项 |
| 回滚 | — | 单文件 4 处纯增量改动，`git revert` 单个提交即可完全回到 v1.6.1.8 行为，无数据、无 schema、无接口残留 |

---

## 11. 修订记录

### Rev.C（本版）——ADJ-4 用户决策关闭，ADJ-5 升级为永久禁令

**用户决策**：*"ADJ-4 不要排，这个别动了。"*

**处置与连锁影响**：

| 项 | Rev.B | Rev.C |
|---|---|---|
| ADJ-4（收紧 R077 到 J-2/J-3） | 待用户决策 | **🔒 关闭，永不排期** |
| ADJ-5（`_UNIQUE_RE` 不认反引号） | "Phase 2 与 ADJ-4 一并决策" | **🔒 永久禁止修复**——ADJ-4 关闭后它变成承重件 |
| NG-5 | 本次不放宽 | **升级为永久约束**（非仅本次） |
| 新增章节 | — | **§8.1 地雷警示**（含实测论证表）、**§8.2 已接受的残留漏报** |
| 新增改动点 | — | **改动点 5（可选，纯注释）**：在 `_UNIQUE_RE` 旁留代码级警示 |
| 新增附录 | — | **附录 B 可复现验证脚本**（供评审独立复核） |

**关键结论（Rev.C 的核心增量）**：ADJ-4 关闭后，`_UNIQUE_RE` 认不出反引号这个"缺陷"，**反而是 R077 在真实 TDSQL 元数据上贴合判据 J-2 的唯一原因**。实测：同一条违反 J-2 的真实形态 SQL，ADJ-5 不修 → 正确报 R077；ADJ-5 若被修 → **漏报**。故必须把"禁止修复"写死在文档、检查单与代码注释三处。

**Phase 1 修复方案本身不受影响**：FIX-1/FIX-2 与 ADJ-4/ADJ-5 无交集，用例矩阵与漂移扫描结果均不变。

---

### Rev.B——按用户口径纠正撤销 FIX-3

**用户纠正原文**：*"这张表之所以顺利的创建成分片表的关键是 `TDSQL_DISTRIBUTED BY HASH(cust_no)` 以及 `PRIMARY KEY (ID, CUST_NO)`，也就是 cust_no 字段既是分片键又是主键或主键的一部分，同时如果这张表有除主键外的其他唯一索引，那么 cust_no 还应该是这些唯一索引的一部分。"*

**Rev.A 的问题**：Rev.A 把现场 #3 里的 `` KEY `cus_bas_corp_contact_IDX1` (`CUST_NO`,`DATA_VALID_TM`) `` 与合规性联系了起来，并据此提出 **FIX-3**——放宽 `R077._UNIQUE_RE` 以支持反引号索引名，让"分片键在唯一索引中"的放行通道生效。

**按 §1 判据，这是错的**：

1. `KEY ... (CUST_NO, ...)` 是**普通索引**，对合规性不起任何作用（NJ-1）。#3 合规靠的是 J-2（`cust_no ∈ PRIMARY KEY`）。
2. 更严重的是，"分片键在唯一索引里但不在主键里"**本身就不合规**（NJ-2）。FIX-3 会让这类表被 R077 放行，**等于压制真实违规**——方向恰好反了。实测用例 C3 印证：`PRIMARY KEY(id) + UNIQUE KEY uk_code(code) + shardkey=code` 在 FIX-3 下会被放行，而按 J-2 它应当报错。

**处置**：

| 项 | Rev.A | Rev.B |
|---|---|---|
| FIX-3（放宽 `_UNIQUE_RE`） | 列入 Phase 1 | **撤销**，改列为 NG-5 非目标 + ADJ-5 邻接缺陷 |
| 改动点数 | 5 处 | **4 处** |
| 用例 C3 期望 | R077 放行 | **R077 保持触发** |
| 新增用例 | — | **N8★**：分片键只在普通 `KEY` 里、不在主键 → R077 必须触发（直接守 NJ-1） |
| §4.3 对 R077/R054 口径差异的定性 | "故意不同严，是既有设计" | **更正**：R054 才贴合 J-2/J-3，R077 的 `或` 口径偏宽松且缺 J-3，是既有口径缺陷 ADJ-4 |
| 漂移扫描结果 | 5 条 | **5 条（不变）** |
| 用例矩阵 | 20/20 | **20/20（已用 Rev.B 原型重测）** |

**未受影响的部分**：FIX-1、FIX-2 与该口误无关——#3 的通过完全来自 J-2（实测 `_collect_pk_cols = ['cust_no','id']`），广播表放行与主键/唯一索引判据无关。核心修复不需返工。

---

## 12. 施工检查单（逐项打勾）

**范围控制**

- [ ] 只改了 `backend/engine/rules/distributed.py` 一个文件（`git diff --stat` 必须只有一行）
- [ ] 只改了 `R077CreateTableMustHaveShardKey` 与 `R054ShardKeyMustBePrimaryKey` 两个类 + 模块级常量
- [ ] 全文件共 **4 处**改动（模块级常量、`R077.check()`、`R077._extract_shard_key()`、`R054.check()`）
- [ ] 没有新增 import（`re` 已在第 18 行）
- [ ] 没有改动任何规则的 `rule_id` / `severity` / `enabled` / `instance_scope` / `category` / `description`

**判据不得放宽（Rev.B 重点）**

- [ ] **`_UNIQUE_RE` 的正则一个字符都没动**（NG-5 永久禁令。动手前必读 §8.1——它看起来像 bug，实际是承重的，修了就会制造漏报）
- [ ] 没有为 `parsed.indexes` 补充 UNIQUE 条目产出（同样会激活 R077 的 OR 分支）
- [ ] 没有收紧 R077 的 `或` 口径（ADJ-4 已由用户决策永久关闭）
- [ ] `_PK_RE` / `_SHARDKEY_RE` / `_SHARD_KEY_RE` / `_BROADCAST_RE` 均未改动
- [ ] `_collect_pk_cols()` / `_collect_unique_index_cols()` 均未改动
- [ ] R077 的 `shard_key_col not in pk_cols and shard_key_col not in unique_index_cols` 判定行原样保留
- [ ] 没有给 R054 增加 `TDSQL_DISTRIBUTED` 识别（NG-3）
- [ ] 没有改动 R054 内联的 UNIQUE 正则（NG-4）

**插入位置**

- [ ] `_extract_shard_key()` 的新分支在**函数最末尾**，前 3 个来源的代码逐字未动
- [ ] R077 的哨兵判定在 `_extract_shard_key()` 调用**之后**、`if not shard_key_col:` **之前**
- [ ] R054 的哨兵判定在第二个 `if not shard_key: return None` **之后**（保证同时覆盖元数据通道与 raw_sql 通道）

**验收**

- [ ] §9.1 的 20 条用例全部通过，其中 7 条 ★ 反向鉴别用例必须仍然触发
- [ ] **N8★ 必须通过**——分片键只在普通 `KEY` 里、不在主键时 R077 必须报（守 NJ-1，防止重犯 Rev.A 的误读）
- [ ] **C3 必须保持触发**——分片键在唯一索引但不在主键时 R077 不得放行（守 NJ-2）
- [ ] §9.2 端到端 14 张表逐表比对符合期望，**#4 仍报 R077**
- [ ] §9.3 回归：4 个规则测试文件仍为 168 passed；全量 `pytest tests/` 无新增 failed、skipped 不增加
- [ ] 规则总数仍为 119（92 ALL + 27 DISTRIBUTED）
- [ ] §7.2 全语料漂移扫描：变化语句数恰为 5，且就是 P1–P5

**交付说明**

- [ ] 写明 §7.3 的 #3 漏审副作用（"零违规"不等于"已全量审核"）
- [ ] 写明 §8 的五项邻接缺陷：ADJ-1/2/3 建议 Phase 2 排期（ADJ-3 优先）；**ADJ-4/ADJ-5 已永久关闭，交付说明中须明确"不得修复"**
- [ ] 若采纳改动点 5，确认新增内容**只有注释行**（`git diff` 中不含任何非注释的代码变更）
- [ ] 建议把 §8.1 的禁令补录进 `docs/GUIDE-团队施工规约.md`（来源：v1.6.1.9 缺陷调查）

---

## 附录 A：设计阶段的实测证据清单

本设计的每一条判断均有实测支撑，无推断结论（团队规约 R-11）：

| 编号 | 结论 | 证据 |
|---|---|---|
| E-1 | 生产报告可 1:1 本地复现 | 14 表逐条比对，6 张重点表规则集逐字一致 |
| E-2 | #3 的 `table_options`/`columns`/`indexes` 全空 | 解析器输出实测 `{}` / `0` / `[]` |
| E-3 | #5/#8/#11/#13 的 `_extract_shard_key` 返回 `'noshardkey_allset'` | 直调规则私有方法实测 |
| E-4 | #3 的 `_collect_pk_cols` 含 `cust_no`，且 UNIQUE 索引数 = 0 | 直调实测 `['cust_no','id']`；正则统计 UNIQUE = 0 → **J-2 满足、J-3 空条件成立** |
| E-5 | `_UNIQUE_RE` 对反引号索引名失配 | 4 组正则样本实测，2 组 MISS（→ ADJ-5，本次不修） |
| E-6 | `parsed.indexes` 恒不产出 UNIQUE 条目 | 3 组 UNIQUE 写法实测均为 `[]`（→ ADJ-5，本次不修） |
| E-7 | `TDSQL_DISTRIBUTED` 全仓库零出现 | `grep -rn` 全库检索 |
| E-8 | `noshardkey`/`allset` 在 `backend/engine/` 零出现 | `grep -rn` 检索 |
| E-9 | 摘掉尾子句后 #3 多命中 R036/R037/R061 | 反事实实验实测（→ ADJ-1） |
| E-10 | Rev.B 补丁 20/20 用例通过（含 N8★、修正后的 C3） | 原型子类全量矩阵 |
| E-11 | 全语料 201 条语句仅 5 条判定变化 | Rev.B 原型漂移扫描实测 |
| E-12 | 规则测试基线 168 passed | 改动前实测 |
| E-13 | `_UNIQUE_RE` 是承重件：修了它会在真实 TDSQL 形态上制造漏报 | 对照实验实测——同一条违反 J-2 的反引号 UNIQUE 语句，ADJ-5 不修→报 R077，ADJ-5 修→漏报（§8.1 表） |
| E-14 | #3 的 UNIQUE 索引数量 = 0，两个 `KEY` 均为普通索引 | 正则统计实测 → 其合规完全来自 J-2，与普通索引无关（守 NJ-1） |

---

## 附录 B：可复现的验证脚本（供评审独立复核）

> 本设计的全部实测结论均可由下列脚本复现。评审人不必采信文中数字，可直接跑。
> 施工时建议把 B.2/B.3 合并落库为 `tests/qa/verify_r077_r054_tdsql_syntax.py`（沿用
> `tests/qa/verify_oracle_rules_acceptance.py` 的既有约定）。

**运行前置**：工作目录为仓库根目录；`AUTH_ENABLED=false SCHEDULER_ENABLED=false`。

### B.1 从生产 HTML 报告提取 14 张表的原始 DDL

```python
import re, html, json
p = "Extracted_Schema_Report_6261.html"          # 用户提供的现场报告
s = open(p, encoding="utf-8", errors="replace").read()
items = []
for pt in re.split(r'<h3[^>]*>', s)[1:]:
    no   = re.match(r'#(\d+)', pt)
    sql  = re.search(r'<div class="sql-box">(.*?)</div>', pt, re.S)
    txt  = html.unescape(re.sub(r'<[^>]+>', '', sql.group(1))).strip() if sql else ""
    name = re.search(r'--\s*Table:\s*(\S+)', txt)
    items.append({"no": int(no.group(1)) if no else 0,
                  "table": name.group(1) if name else "",
                  "rules": sorted(set(re.findall(r'\b(R\d{3})\b', pt))),
                  "sql": txt})
json.dump(items, open("report_items.json", "w"), ensure_ascii=False, indent=1)
```

### B.2 正反用例矩阵（§9.1）

拟议补丁以**子类覆写**方式模拟，因此可在**不改动任何源码**的前提下复核：

```python
import re, os, sys, io, json, contextlib, warnings
warnings.filterwarnings("ignore")
os.environ.update(AUTH_ENABLED="false", SCHEDULER_ENABLED="false")
buf = io.StringIO()
with contextlib.redirect_stderr(buf):
    from backend.engine.checker import SQLParser
    from backend.engine.rules.distributed import (
        R077CreateTableMustHaveShardKey as O77, R054ShardKeyMustBePrimaryKey as O54)

# ——— 拟议新增的两个模块级常量（与 §5.1 逐字一致）———
_TDSQL_DISTRIBUTED_RE = re.compile(
    r"\btdsql_distributed\s+by\s+(?:hash|key)\s*\(\s*[`\"']?([a-z_][a-z0-9_]*)[`\"']?\s*\)",
    re.IGNORECASE)
_NOSHARDKEY_SENTINEL_RE = re.compile(r"^noshardkey(?:_[a-z0-9_]+)?$", re.IGNORECASE)

class R077V2(O77):
    # 注意：_UNIQUE_RE 原样继承，绝不覆写（NG-5 / §8.1）
    def _extract_shard_key(self, parsed, raw_sql):
        v = super()._extract_shard_key(parsed, raw_sql)
        if v:
            return v
        m = _TDSQL_DISTRIBUTED_RE.search(raw_sql)
        return m.group(1).strip('`"\' ').lower() if m else ""

    def check(self, parsed, table_metadata=None):
        if not parsed.is_create_table or parsed.is_create_table_select \
                or parsed.is_temporary_table:
            return None
        raw = parsed.raw_sql
        if self._BROADCAST_RE.search(raw):
            return None
        sk = self._extract_shard_key(parsed, raw)
        if sk and _NOSHARDKEY_SENTINEL_RE.match(sk):        # FIX-2
            return None
        if not sk:
            t = parsed.tables[0] if parsed.tables else ""
            return self._make_violation(f"建表语句未声明分片键…（表 {t}）")
        if sk not in self._collect_pk_cols(parsed, raw) \
                and sk not in self._collect_unique_index_cols(parsed, raw):
            return self._make_violation(f"分片键 '{sk}' 不在主键或唯一索引中")
        return None

class R054V2(O54):
    def check(self, parsed, table_metadata=None):
        sk = ""
        if table_metadata:
            for t in parsed.tables:
                v = (table_metadata.get(t) or {}).get("shard_key", "")
                if v:
                    sk = v; break
        if not sk:
            m = re.search(r"shardkey\s*=\s*['\"`]?(\w+)", parsed.raw_sql, re.IGNORECASE)
            if m:
                sk = m.group(1)
        if sk and _NOSHARDKEY_SENTINEL_RE.match(sk.strip('`"\' ')):   # FIX-2
            return None
        return super().check(parsed, table_metadata)
```

用例集与期望值见 §9.1 表格（P1–P5 / N1–N8 / C1–C9）；判定方式：
`(R077触发?, R054触发?) == (期望R077, 期望R054)`。**要求 20/20 通过。**

### B.3 全语料漂移扫描（§7.2，最关键的一条证据）

```python
import glob, os
stmts = []
for f in sorted(glob.glob("**/*.sql", recursive=True)):
    for st in open(f, encoding="utf-8", errors="replace").read().split(";"):
        if len(st.strip()) > 20:
            stmts.append((f, st.strip()))
for it in json.load(open("report_items.json")):          # 追加现场 14 表
    stmts.append((f"现场#{it['no']} {it['table']}",
                  "\n".join(l for l in it['sql'].splitlines()
                            if not l.strip().startswith('--'))))

p_ = SQLParser(); o77, o54, n77, n54 = O77(), O54(), R077V2(), R054V2()
diff = []
for src, st in stmts:
    try:
        with contextlib.redirect_stderr(buf):
            pr = p_.parse(st)
            old = (o77.check(pr) is not None, o54.check(pr) is not None)
            new = (n77.check(pr) is not None, n54.check(pr) is not None)
    except Exception:
        continue
    if old != new:
        diff.append((src, old, new))
print(f"判定变化 {len(diff)} 条")     # 门槛：必须 == 5，且恰为现场 #3/#5/#8/#11/#13
```

**验收门槛**：`len(diff) == 5`，且这 5 条恰为现场 `#3 / #5 / #8 / #11 / #13`。
任何第 6 条变化都意味着爆炸半径超出设计，**必须停止施工并回到评审**。

### B.4 ADJ-5 承重性对照实验（§8.1）

```python
class R077IfADJ5Fixed(R077V2):          # 假设有人"顺手"把 ADJ-5 修了
    _UNIQUE_RE = re.compile(
        r"unique\s+(?:key|index)\s*(?:[`\"']?\w+[`\"']?)?\s*\(([^)]+)\)", re.IGNORECASE)

SQL = ("CREATE TABLE `t1` (`id` bigint NOT NULL, `code` varchar(16) NOT NULL, "
       "PRIMARY KEY (`id`), UNIQUE KEY `uk_code` (`code`)) ENGINE=InnoDB shardkey=code")
# 该表违反 J-2（分片键 code 不在主键），应当报 R077
# 期望输出： ADJ-5不修 → 报R077（正确）； ADJ-5被修 → 漏报（回归）
```

### B.5 回归基线

```bash
pytest tests/test_rules.py tests/test_instance_scope_rules.py \
       tests/test_oracle_compat_rules.py tests/test_instance_type_service.py -q
# 门槛：168 passed, 0 failed（改前实测基线）

pytest tests/ -q
# 门槛：不得新增 failed；skipped 不得增加
# 注：跑前需 SET GLOBAL slow_query_log=ON; SET GLOBAL long_query_time=0.1
#     否则 test_uat47_05 / test_uat53_02 会因本地环境失败（非产品缺陷）
```
