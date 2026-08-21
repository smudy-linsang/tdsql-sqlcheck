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
| 状态 | **待评审——未动任何代码** |

---

## 0. 一句话结论

TDSQL 真实内核输出的两种合规建表语法——分片表的 `TDSQL_DISTRIBUTED BY HASH(col)` 与广播表（全局表）的 `shardkey=noshardkey_allset`——在规则引擎里**从来没有被认识过**（全仓库检索：这两个 token 在 `backend/` 下零出现）。R077 因此把合规分片表判成"未声明分片键的单表"，把广播表的哨兵值 `noshardkey_allset` 当成一个**真实列名**去查主键，必然查不到，于是 R077 与 R054 双双误报。修复方式是在这两条规则内部补齐对这两种语法的识别，**不动解析器、不动元数据采集、不动其余 117 条规则**。

---

## 1. 现场与复现

### 1.1 用户报告的两项

| 报告序号 | 表名 | 尾部语法 | 实际触发 | 用户判定 |
|---|---|---|---|---|
| #3 | `cus_bas_corp_contact` | `TDSQL_DISTRIBUTED BY HASH(\`cust_no\`)` | R077 (ERROR) | **合规分片表，不应触发** |
| #5 | `cus_name_list_type` | `shardkey=noshardkey_allset` | R077 (ERROR) + R054 (WARNING) | **合规广播表（全局表），均不应触发** |

### 1.2 本地 1:1 复现（实测，非推断）

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

### 1.3 反向鉴别基准（必须保持触发的对照）

**#4 `cus_bas_corp_contact_addr_20260511`** 尾部没有任何分片/广播声明（以 `COMMENT='…'` 结束），是**真正的单表**，R077 触发**正确**。本设计的全部验收都以"#4 必须继续报 R077"为反向鉴别锚点——若修完 #4 也不报了，等于把 R077 废掉，属于施工失败。

---

## 2. 根因分析

### 2.1 缺陷 A：R077 不认识 `TDSQL_DISTRIBUTED BY HASH(col)`

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

### 2.2 缺陷 B：`noshardkey_allset` 被当成列名

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

**这是一个必然失败的判定**：只要一张表是广播表，这个哨兵值永远不可能出现在主键里，误报 100% 复现。

**全仓库检索证据**：`noshardkey` / `allset` 在 `backend/engine/` 下**零出现**（`backend/services/` 下的 `/*sets:allsets*/` 是 TDSQL 查询提示，与此无关）。

**附加事实**：`R054` 从头到尾**没有任何 BROADCAST 放行分支**——R077 至少有 `_BROADCAST_RE` 快速通道，R054 连这个都没有。所以哪怕一张表老老实实写了 `BROADCAST` 关键字，只要同时出现 `shardkey=` 字样，R054 依然会误报。本次一并收口。

### 2.3 缺陷 C（同类、未被用户发现）：R077 的"唯一索引"放行通道对真实 TDSQL 输出完全失效

R077 的规则描述明确写着"分片键必须是主键**或唯一索引**的一个字段"，`_collect_unique_index_cols()` 就是这条放行通道。实测它**在真实 TDSQL 元数据上从不生效**：

```
CREATE TABLE `t_u1` (`id` bigint NOT NULL, `code` varchar(16) NOT NULL,
  PRIMARY KEY (`id`), UNIQUE KEY `uk_code` (`code`)) ENGINE=InnoDB shardkey=code

parsed.indexes        = []          ← 解析器压根没输出 UNIQUE 条目
index_definitions     = []
_collect_unique_cols  = []          ← 两个来源都空
R077 → ★误报「分片键 'code' 不在主键或唯一索引中」
```

两个来源同时失效：

| 来源 | 失效原因 |
|---|---|
| `parsed.indexes` 中 `type=="UNIQUE"` | 解析器对 `UNIQUE KEY` 未产出 UNIQUE 类型条目（实测恒为空，裸名写法也一样） |
| `_UNIQUE_RE` = `unique\s+(?:key\|index)\s+\w*\s*\(...\)` | 索引名的 `\w*` **不接受反引号包裹**。而 `SHOW CREATE TABLE` 输出的索引名**永远带反引号** |

对照实测：

| 写法 | `_collect_unique_index_cols` | R077 |
|---|---|---|
| `` UNIQUE KEY `uk_code` (`code`) ``（TDSQL 真实输出） | `[]` | ★误报 |
| `UNIQUE KEY uk_code (code)`（测试用例写法） | `['code']` | 通过 |
| `` UNIQUE INDEX `uk_x` (`code`) `` | `[]` | ★误报 |

**这就是为什么现有测试全绿却挡不住线上误报**：`tests/rule_audit_materials/` 里的 UNIQUE 用例用的是裸索引名，而生产元数据一律带反引号。这是同一缺陷类（"规则按手写 SQL 的形态设计，没按 `SHOW CREATE TABLE` 的真实输出形态验证"）的第三个实例，**只要不修，下一张以唯一键做分片键的分片表上线就会复现同样的投诉**，故纳入本次范围。

### 2.4 三个缺陷的共同根因

| | |
|---|---|
| **根因** | R077/R054 的分片键识别逻辑，是按**开发人员手写的建表 SQL**形态设计的（`SHARDKEY=id`、裸索引名），从未按**TDSQL 内核 `SHOW CREATE TABLE` 的真实输出**形态验证过 |
| **触发条件** | v1.6.x 新增「在线元数据审核」，第一次把内核原样输出的 DDL 直接喂进规则引擎，形态差异立刻暴露 |
| **为何测试没拦住** | 全部规则物料（`tests/rule_audit_materials/sql_audit/*.sql`）是手写形态；仓库内 17 个 `.sql` 文件、201 条语句中，`TDSQL_DISTRIBUTED`、`noshardkey_allset`、反引号 UNIQUE 索引名**一条都没有** |

---

## 3. 修复方案与范围边界

### 3.1 本次实施（Phase 1）——三个改动点，全部落在一个文件的两个类里

| 编号 | 位置 | 改动 | 方向性质 |
|---|---|---|---|
| **FIX-1** | `R077._extract_shard_key()` | 追加**第 4 个**取值来源：`TDSQL_DISTRIBUTED BY HASH/KEY(col)` | 仅在前 3 个来源**全空**时才可达 |
| **FIX-2** | `R077.check()` + `R054.check()` | 识别 `noshardkey*` 哨兵值 → 判为广播表 → 放行 | 仅在分片键取值命中哨兵时可达 |
| **FIX-3** | `R077._UNIQUE_RE` | 索引名允许被反引号/引号包裹 | **只扩大放行集，不可能产生新违规** |

### 3.2 明确的非目标（本次绝不触碰）

| # | 不做什么 | 为什么不做 |
|---|---|---|
| NG-1 | **不修改 `backend/engine/parser/parser_legacy.py`** | 净化 `TDSQL_DISTRIBUTED` 尾子句会让 sqlglot 从"降级为 Command"变成"完整解析"，**全部 119 条规则**看到的输入结构随之改变（见 §6.3 实测）。爆炸半径与本次缺陷不成比例，单独排期 |
| NG-2 | **不修改 `tdsql_connector.py` 的 `_detect_shard_info()` / `parse_shard_key_from_ddl()`** | 它们同源带病（§8），但服务的是 R020/R021/R022/R053/R056/R057/R060 的元数据通道与「大表治理」。改它等于一次性动 7 条规则 + 1 个业务模块，违反"严控范围" |
| NG-3 | **不给 R054 增加 `TDSQL_DISTRIBUTED` 识别** | R054 当前对该语法**取不到分片键、直接返回 None**，属"漏报"而非"误报"。补上会让**过去不报的语句开始报**——这是行为扩张，不是缺陷修复。R077 已覆盖同一约束且消息是超集 |
| NG-4 | **不修改 R054 的 UNIQUE 正则** | 同 NG-3：R054 的 E2 分支是**产出违规**的分支，放宽正则会让它发现更多唯一索引 → 新增违规。FIX-3 只放宽 R077 的**放行**分支，方向相反、风险相反 |
| NG-5 | **不改动任何规则的 `severity` / `enabled` / `instance_scope`** | 规则元数据一旦变动会穿透规则集、门禁、报表统计 |
| NG-6 | **不改动 R077/R054 之外的任何一条规则** | 报告中同时出现的 R001/R036/R037/R061/R063 均为独立正确判定，与本缺陷无关 |

### 3.3 R054 与 R077 的口径差异（施工时不得"顺手统一"）

两条规则**故意不同严**，这是既有设计，不是 bug：

| | R077 (ERROR) | R054 (WARNING) |
|---|---|---|
| 判定 | 分片键 ∈ 主键 **∪ 唯一索引** | 分片键 ∈ **主键**（更严）；且所有唯一索引须含分片键 |

因此 FIX-3 之后会出现"R077 放行、R054 仍报 WARNING"的组合（见 §7 用例 C3/C4）——**这是正确的**，不得为了让两条规则"看起来一致"而去动 R054。

---

## 4. 详细设计（照图施工）

> 全部改动位于 `backend/engine/rules/distributed.py`。行号基于当前 `main`（commit `4ee5961`）。

### 4.1 改动点 1：新增两个模块级常量

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

### 4.2 改动点 2：R077 `_UNIQUE_RE` 放宽索引名（FIX-3）

**位置**：`distributed.py:519-523`。

**改前**：

```python
    # 表级 UNIQUE KEY/INDEX 列提取正则（回退方案）
    _UNIQUE_RE = re.compile(
        r"unique\s+(?:key|index)\s+\w*\s*\(([^)]+)\)",
        re.IGNORECASE,
    )
```

**改后**：

```python
    # 表级 UNIQUE KEY/INDEX 列提取正则（回退方案）
    # v1.6.1.9: 索引名允许被反引号/引号包裹——SHOW CREATE TABLE 输出的
    # 索引名恒带反引号，原 `\w*` 匹配不上，导致"唯一索引"放行通道在真实
    # TDSQL 元数据上完全失效（见设计说明书 §2.3）。同时把 key|index 之后
    # 的 \s+ 放宽为 \s*，兼容 `UNIQUE KEY(col)` 无空格写法。
    _UNIQUE_RE = re.compile(
        r"unique\s+(?:key|index)\s*(?:[`\"']?\w+[`\"']?)?\s*\(([^)]+)\)",
        re.IGNORECASE,
    )
```

**性质**：该正则**唯一被 `_collect_unique_index_cols()` 使用**，其返回值**只用于放行判定**（`shard_key_col not in unique_index_cols` 才违规）。因此本改动**在数学上不可能新增任何违规**，只可能消除违规。

---

### 4.3 改动点 3：R077 `check()` 插入哨兵放行（FIX-2）

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
        # 表达"本表无分片键"，该值是哨兵而非列名，不适用分片键约束
        if shard_key_col and _NOSHARDKEY_SENTINEL_RE.match(shard_key_col):
            return None

        if not shard_key_col:
```

**可达性**：仅当 `_extract_shard_key` 返回值形如 `noshardkey*` 时才生效。改前这类语句 100% 落入"分片键不在主键或唯一索引中"分支。**改动前后的差集恰好等于误报集合**。

---

### 4.4 改动点 4：R077 `_extract_shard_key()` 追加第 4 来源（FIX-1）

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
        # 前两个正则也不匹配，故必须走到这里才能拿到分片键
        td_match = _TDSQL_DISTRIBUTED_RE.search(raw_sql)
        if td_match:
            return td_match.group(1).strip('`"\' ').lower()
        return ""
```

**关键的范围控制性质**：新分支被放在**函数最末尾**，只有前 3 个来源**全部返回空**时才可达。而前 3 个来源全空时，改前的行为是**必定报"未声明分片键"**。因此：

> 本改动的行为差集 = {前 3 源全空} ∩ {含 `TDSQL_DISTRIBUTED BY HASH/KEY(...)`}
> ——即**恰好且仅有**当前被误报的那一类语句。任何其它语句逐字不变。

---

### 4.5 改动点 5：R054 `check()` 插入哨兵放行（FIX-2）

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

### 4.6 改动汇总

| # | 文件 | 类/方法 | 类型 | 净增行 |
|---|---|---|---|---|
| 1 | `distributed.py` | 模块级 | 新增 2 个常量 + 注释 | +22 |
| 2 | `distributed.py` | `R077._UNIQUE_RE` | 改 1 行正则 + 注释 | +4 |
| 3 | `distributed.py` | `R077.check()` | 新增 4 行 | +4 |
| 4 | `distributed.py` | `R077._extract_shard_key()` | 新增 6 行 | +6 |
| 5 | `distributed.py` | `R054.check()` | 新增 6 行 | +6 |

**合计：1 个文件、2 个类、5 处、净增约 42 行，无删除、无签名变更、无新增依赖、无 import 变更、无数据库变更、无接口变更、无前端变更。**

---

## 5. 不需要改动的部分（施工时逐项确认，防止范围蔓延）

| 对象 | 结论 | 依据 |
|---|---|---|
| `backend/engine/rules/__init__.py` | **不改** | 规则未增减，注册表与文档串不变 |
| `backend/engine/parser/**` | **不改** | 见 NG-1 |
| `backend/services/tdsql_connector.py` | **不改** | 见 NG-2、§8 |
| `backend/services/audit_service.py` | **不改** | 只是调用方，不含分片键判定 |
| `backend/api/**` | **不改** | 无接口/字段变更 |
| `backend/schema/**` | **不改** | 无 schema 变更 |
| 前端 `index.html` / 静态资源 | **不改** | 无展示层变更 |
| R020/R021/R022/R053/R055/R056/R057/R058/R059/R060 | **不改** | 实测：这 10 条均**只从 `table_metadata` 取分片键**，不解析 DDL，与本次改动零交集 |
| 其余 107 条规则 | **不改** | 与分片键判定无关 |

---

## 6. 影响面分析（爆炸半径）

### 6.1 逻辑论证：三处改动的可达域

| 改动 | 可达前置条件 | 改前该条件下的行为 | 改后 | 是否可能新增违规 |
|---|---|---|---|---|
| FIX-1 | 三个既有分片键来源全空 **且** SQL 含 `TDSQL_DISTRIBUTED BY HASH/KEY(...)` | 必报"未声明分片键" | 取到分片键后按既有主键/唯一索引口径判定 | **可能**——分片键不在主键/唯一索引时会报（用例 N3，属正确行为，是恢复鉴别力） |
| FIX-2 | 分片键取值匹配 `^noshardkey(_\w+)?$` | 必报"不在主键中" | 放行 | **不可能** |
| FIX-3 | SQL 含 `UNIQUE KEY/INDEX` 且索引名带引号 | 该唯一索引不被计入放行集 | 计入放行集 | **不可能**（只扩大放行集） |

### 6.2 全语料实测漂移扫描

把仓库内**全部 17 个 `.sql` 文件切出的 201 条可解析语句** + **生产报告 14 张表的原始 DDL**，逐条同时灌进"改前规则"与"改后原型"，比对 (R077, R054) 判定：

```
扫描 .sql 文件 17 个，可解析语句 201 条
改前/改后判定发生变化的语句: 5 条

  现场报告#3  cus_bas_corp_contact   R077/--    → --/--
  现场报告#5  cus_name_list_type     R077/R054  → --/--
  现场报告#8  t_branch               R077/R054  → --/--
  现场报告#11 t_dict                 R077/R054  → --/--
  现场报告#13 t_product              R077/R054  → --/--
```

> **发生变化的 5 条，恰好就是本次要修的 5 处误报；仓库内 201 条既有语料判定 100% 逐字不变。**

### 6.3 一个必须向用户明示的副作用（不是本次引入，但会因本次而"显形"）

#3 `cus_bas_corp_contact` 的 `TDSQL_DISTRIBUTED BY HASH(...)` 会让 **sqlglot 整条降级为 Command**，实测后果：

| | `columns` | `indexes` | `table_options` | 命中规则 |
|---|---|---|---|---|
| #3 原样 | **0** | 0 | 0 | `[R077]` |
| #3 摘掉尾子句（反事实实验） | 25 | 2 | 3 | `[R036,R037,R061,R077]` |
| #4 对照（结构相同、无尾子句） | 34 | 1 | 3 | `[R001,R036,R037,R061,R077]` |

也就是说：**#3 目前不仅被误报 R077，还被漏审了 R036/R037/R061**。Phase 1 修完后，#3 会显示为"零违规"——**这个"零"是干净的 R077 判定 + 仍然存在的漏审叠加出来的**，并非该表真的完全合规。

- 消除漏审的唯一办法是 NG-1（解析器净化 TDSQL 方言尾子句）；
- 该改动会让**所有** `TDSQL_DISTRIBUTED` 建表语句突然被全套 DDL 规则审到，报告违规数上升，属于用户可感知的行为变化；
- 故**列为 Phase 2 单独排期，由用户决策**，不在本次夹带。

---

## 7. 验收测试方案

### 7.1 正反用例矩阵（20 条，已在设计阶段用原型全量实测通过 20/20）

> 建议落库为 `tests/test_r077_r054_tdsql_syntax.py`。★ = 反向鉴别用例（团队规约 R-12：必须证明"没把功能删掉"）。

| 用例 | 场景 | 改前 | 期望（改后） |
|---|---|---|---|
| P1 | 现场#3 `TDSQL_DISTRIBUTED BY HASH(cust_no)` 分片表 | R077 | 零违规 |
| P2 | 现场#5 `shardkey=noshardkey_allset` 广播表 | R077+R054 | 零违规 |
| P3 | 现场#8 `t_branch` 广播表（含 UNIQUE KEY） | R077+R054 | 零违规 |
| P4 | 现场#11 `t_dict` 广播表 | R077+R054 | 零违规 |
| P5 | 现场#13 `t_product` 广播表 | R077+R054 | 零违规 |
| **N1★** | 现场#4 无任何分片声明 | R077 | **R077 仍触发** |
| **N2★** | `SHARDKEY=cust_id` 但不在主键 | R077+R054 | **R077+R054 仍触发** |
| **N3★** | `TDSQL_DISTRIBUTED BY HASH(cust_no)` 但 cust_no 不在主键 | R077 | **R077 仍触发**（新增鉴别力，非放行） |
| **N4★** | 反引号 UNIQUE 不含分片键、分片键也不在主键 | R077+R054 | **R077+R054 仍触发** |
| **N5★** | 普通 `KEY`（非 UNIQUE）含分片键 | R077+R054 | **仍触发**（不得把普通索引当唯一索引放行） |
| **N7★** | 表注释里含 `noshardkey_allset` 字样、真实分片键合规 | 零违规 | **零违规**（哨兵不得被注释文本诱发） |
| C1 | 合规分片表（分片键在主键） | 零违规 | 零违规 |
| C2 | `BROADCAST` 关键字广播表 | 零违规 | 零违规 |
| C3 | 分片键在**反引号** UNIQUE 中 | R077+R054 | R054（R077 放行，R054 按更严口径保留） |
| C4 | 分片键在**裸名** UNIQUE 中 | R054 | R054（**改前改后逐字一致**） |
| C5 | `TDSQL_DISTRIBUTED BY KEY(sk)` 写法 | R077 | 零违规 |
| C6 | `tdsql_Distributed  By  Hash( SK )` 大小写混排+多空格+无反引号 | R077 | 零违规 |
| C7 | CTAS `CREATE TABLE ... AS SELECT` | 零违规 | 零违规（跳过分支不受影响） |
| C8 | `CREATE TEMPORARY TABLE` | 零违规 | 零违规 |
| C9 | 非建表语句（SELECT） | 零违规 | 零违规 |

### 7.2 端到端验收（在线元数据审核通道）

把 `Extracted_Schema_Report_6261.html` 的 14 张表原样组成 `.sql`，走**在线元数据审核 / 文件审核**通道（`instance_type=distributed`），逐表比对：

| 序号 | 表 | 改前 | 验收期望 |
|---|---|---|---|
| #3 | cus_bas_corp_contact | R077 | **无 R077** |
| #4 | cus_bas_corp_contact_addr_20260511 | R001,R036,R037,R061,R077 | **原样不变（含 R077）** |
| #5 | cus_name_list_type | R036,R037,R054,R061,R077 | **R036,R037,R061**（去掉 R054/R077） |
| #8 | t_branch | R036,R054,R061,R077 | **R036,R061** |
| #11 | t_dict | R036,R054,R061,R063,R077 | **R036,R061,R063** |
| #13 | t_product | R036,R054,R061,R063,R077 | **R036,R061,R063** |
| #1,#2,#6,#7,#9,#10,#12,#14 | 其余 8 张 | — | **逐条原样不变** |

### 7.3 回归门槛（团队规约 R-18：零跳过）

| 项 | 基线 | 门槛 |
|---|---|---|
| `tests/test_rules.py` + `test_instance_scope_rules.py` + `test_oracle_compat_rules.py` + `test_instance_type_service.py` | **168 passed**（已实测） | **必须仍为 168 passed，0 failed** |
| 全量回归 `pytest tests/` | v1.6.1.8 基线 1312 passed / 0 failed / 1 skipped | **不得新增 failed，skipped 不得增加** |
| 规则总数 | 119（92 ALL + 27 DISTRIBUTED） | **必须仍为 119 / 92 / 27** |
| 全语料漂移扫描（§6.2 脚本） | — | **变化语句数必须 = 5，且就是 P1–P5** |

> 注：本地环境 `test_uat47_05_slow_query_config` / `test_uat53_02_slow_query_workflow` 两条依赖 MariaDB `slow_query_log=ON`，跑全量前需 `SET GLOBAL slow_query_log=ON; SET GLOBAL long_query_time=0.1`，否则会有 2 条环境性失败（非产品缺陷）。

---

## 8. 已知邻接缺陷（同源，本次**不修**，建议单独排期）

调查过程中发现三处**同一根因**的问题。它们不在用户报告范围内，且修复会显著扩大爆炸半径，故全部剔出本次范围、单独立项：

| 编号 | 位置 | 问题 | 后果 | 建议 |
|---|---|---|---|---|
| **ADJ-1** | `parser_legacy.py` | `TDSQL_DISTRIBUTED BY ...` 导致 sqlglot 整条降级为 Command，`columns/indexes/table_options` 全空 | 这类分片表被**全套结构类规则漏审**（实测 #3 漏掉 R036/R037/R061） | Phase 2：在交给 sqlglot 前净化方言尾子句，**`parsed.raw_sql` 必须保留原文**（R077 依赖它取分片键）。需全量回归 + 用户确认"报告违规数会上升" |
| **ADJ-2** | `tdsql_connector.py:404` `TDSQLConnectionPool._detect_shard_info()` 与 `:162` `parse_shard_key_from_ddl()` | 同样只认 `SHARDKEY=`，且把 `noshardkey_allset` 原样当分片键写进 `meta.shard_key`（`parse_shard_key_from_ddl` 的 docstring 声称"broadcast 表返回 ''"，与实际不符） | ① R020/R021/R022/R053/R056/R057/R060 在元数据通道对广播表连带误报（如"分片表 t_branch 的分片键 'noshardkey_allset' 未在 WHERE 条件中"）；② 「大表治理」展示的分片键为哨兵值 | Phase 2：统一到一个共享的分片键解析工具函数 |
| **ADJ-3** | `tdsql_connector.py:1546` `TDSQLConnector._detect_shard_info()` | 与 `:404` 近似重复实现；其中引用了**未定义变量** `create_sql_upper`（该方法内定义的是 `create_sql`），触发 `NameError` 被外层 `except Exception: pass` 静默吞掉 | 该类的**广播表识别与 `TDSQL_SHARDING_RULES` 查询整段成为死代码**，`is_broadcast_table` 永不为 True | Phase 2：与 ADJ-2 一并去重收敛 |

> ADJ-3 是一个**真实的静默失效**，建议 Phase 2 优先级高于 ADJ-1。

---

## 9. 风险与回滚

| 风险 | 等级 | 说明与对策 |
|---|---|---|
| 名为 `noshardkey*` 的真实列被误判为广播表 | 极低 | 该命名与 TDSQL 保留语义直接冲突，现实中不存在；已在 §4.1 注释中显式记录取舍 |
| `TDSQL_DISTRIBUTED` 出现在注释/字符串里造成误放行 | 低 | 与既有 `_BROADCAST_RE`、`_SHARDKEY_RE` 同等特性（现状下表注释含 "broadcast" 已可绕过 R077），**非本次引入**；用例 N7 覆盖哨兵侧 |
| N3 类语句由"不报"变"报 R077" | 低 | 这是**恢复鉴别力**（分片键确实不在主键），属正确行为；已列入验收矩阵明示 |
| #3 修完后显示"零违规"但实际仍被漏审 | **中** | 已在 §6.3 明示；根治需 Phase 2 (ADJ-1)。**必须在交付说明中写清楚，避免用户误以为该表已通过全量审核** |
| 回滚 | — | 单文件 5 处纯增量改动，`git revert` 单个提交即可完全回到 v1.6.1.8 行为，无数据、无 schema、无接口残留 |

---

## 10. 施工检查单（逐项打勾）

- [ ] 只改了 `backend/engine/rules/distributed.py` 一个文件（`git diff --stat` 必须只有一行）
- [ ] 只改了 `R077CreateTableMustHaveShardKey` 与 `R054ShardKeyMustBePrimaryKey` 两个类 + 模块级常量
- [ ] 没有新增 import（`re` 已在第 18 行）
- [ ] 没有改动任何规则的 `rule_id` / `severity` / `enabled` / `instance_scope` / `category` / `description`
- [ ] `_extract_shard_key()` 的新分支在**函数最末尾**，前 3 个来源的代码逐字未动
- [ ] R054 的哨兵判定在 `if not shard_key: return None` **之后**（保证同时覆盖元数据通道与 raw_sql 通道）
- [ ] 没有给 R054 增加 `TDSQL_DISTRIBUTED` 识别（NG-3）
- [ ] 没有改动 R054 内联的 UNIQUE 正则（NG-4）
- [ ] §7.1 的 20 条正反用例全部通过，其中 6 条 ★ 反向鉴别用例必须仍然触发
- [ ] §7.2 端到端 14 张表逐表比对符合期望，**#4 仍报 R077**
- [ ] §7.3 回归：4 个规则测试文件仍为 168 passed；全量 `pytest tests/` 无新增 failed、skipped 不增加
- [ ] 规则总数仍为 119（92 ALL + 27 DISTRIBUTED）
- [ ] §6.2 全语料漂移扫描：变化语句数恰为 5，且就是 P1–P5
- [ ] 交付说明中写明 §6.3 的漏审副作用与 §8 的三项邻接缺陷

---

## 附录 A：设计阶段的实测证据清单

本设计的每一条判断均有实测支撑，无推断结论（团队规约 R-11）：

| 编号 | 结论 | 证据 |
|---|---|---|
| E-1 | 生产报告可 1:1 本地复现 | 14 表逐条比对，6 张重点表规则集逐字一致 |
| E-2 | #3 的 `table_options`/`columns`/`indexes` 全空 | 解析器输出实测 `{}` / `0` / `[]` |
| E-3 | #5/#8/#11/#13 的 `_extract_shard_key` 返回 `'noshardkey_allset'` | 直调规则私有方法实测 |
| E-4 | #3 的 `_collect_pk_cols` 已含 `cust_no` | 直调实测 `['cust_no','id']` → FIX-1 后必然放行 |
| E-5 | `_UNIQUE_RE` 对反引号索引名失配 | 4 组正则样本实测，2 组 MISS |
| E-6 | `parsed.indexes` 恒不产出 UNIQUE 条目 | 3 组 UNIQUE 写法实测均为 `[]` |
| E-7 | `TDSQL_DISTRIBUTED` 全仓库零出现 | `grep -rn` 全库检索 |
| E-8 | `noshardkey`/`allset` 在 `backend/engine/` 零出现 | `grep -rn` 检索 |
| E-9 | 摘掉尾子句后 #3 多命中 R036/R037/R061 | 反事实实验实测 |
| E-10 | 拟议补丁 20/20 用例通过 | 原型子类全量矩阵 |
| E-11 | 全语料 201 条语句仅 5 条判定变化 | 漂移扫描实测 |
| E-12 | 规则测试基线 168 passed | 改动前实测 |
