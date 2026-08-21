# DESIGN-v1.6.1.9 TDSQL 分片表与广播表建表语法识别缺陷修复详细设计说明书

| 项 | 内容 |
|---|---|
| 版本 | v1.6.1.9（在 v1.6.1.8 基础上） |
| 缺陷等级 | **P0——核心能力误报**（SQL 审核规则对合规 TDSQL 建表语法误判为违规） |
| 缺陷来源 | 内网生产环境 v1.6.1.8 运行期用户反馈；证据 `Extracted_Schema_Report_6261.html` + TDSQL 管理平台截图 |
| 影响模块 | 规则引擎 → 分布式规范规则 R077 / R054 |
| 改动文件 | **仅 1 个**：`backend/engine/rules/distributed.py` |
| 改动类 | **仅 2 个**：`R077CreateTableMustHaveShardKey`、`R054ShardKeyMustBePrimaryKey` |
| 撰写 | 智能体 A |
| 修订 | **Rev.E**——Rev.D 已按智能体 O 评审（BLOCK）完成 7 项强制整改；本版补录目标环境 TDSQL 版本基线，并落实 ADJ-6 关闭决策（见 §11） |
| 状态 | **待复审** |

---

## 0. 一句话结论

TDSQL 内核 `SHOW CREATE TABLE` 输出的两种合规建表语法——分片表的 `TDSQL_DISTRIBUTED BY HASH(col)` 与广播表（全局表）的 `shardkey=noshardkey_allset`——在规则引擎里**从来没有被认识过**。R077 因此把合规分片表判成"未声明分片键的单表"，把广播表的哨兵值当成**真实列名**去查主键，必然查不到，于是 R077 与 R054 双双误报。

修复方式：在 `distributed.py` 内新增一组**共享的、经注释/字符串清洗且尾部锚定**的语法识别助手，**由 R077 与 R054 共同消费**——R077 负责"有没有合法分片声明"，R054 负责"分片键是否满足 J-2/J-3"。**不动解析器、不动元数据采集、不动其余 117 条规则。**

> **Rev.D 相对 Rev.C 的实质变化**：Rev.C 只让 R077 识别新语法，经 O 评审实测证明会把 **6 类非合规语句压成零违规**（注释伪造、单引号、无据的 `BY KEY`、`noshardkey_*` 前缀、以及两类 J-2/J-3 违规 HASH 表）。Rev.D 已全部封堵，并补齐 O 要求的 X1–X12 反例。

---

## 1. TDSQL 分片表的合规判据（本设计的判定基准）

> 本节由用户在评审中给定，经智能体 O 独立核对腾讯云官方文档确认准确，是全文一切判定的**唯一基准**。

一张 TDSQL 分片表合规，需**同时**满足：

| 编号 | 判据 |
|---|---|
| **J-1** | 声明了分片键 |
| **J-2** | **分片键必须是主键、或主键的一部分** |
| **J-3** | 若该表还有主键之外的唯一索引，**分片键还必须是每一个唯一索引的一部分** |

广播表（全局表）不适用 J-2/J-3，其声明形态为 `shardkey=noshardkey_allset` 或 `BROADCAST` 关键字。

### 1.1 J-1 的两种语法形态与各自的证据等级

**Rev.D 新增。** O 评审指出 J-1 的两种形态证据强度不同，不得笼统声称"所有 TDSQL 版本一致"，故分级如下：

| 形态 | 证据等级 | 出处 |
|---|---|---|
| `shardkey=col` | **公开官方规范** | 腾讯云《TDSQL MySQL 版—建表》 |
| `shardkey=noshardkey_allset` | **公开官方规范** | 腾讯云 TDSQL 开发手册（广播表建表语法） |
| `TDSQL_DISTRIBUTED BY HASH(col)` | **本项目目标环境内核已验证输出** | 本次 `SHOW CREATE TABLE`（#3）+ TDSQL 赤兔管理台显示该表类型为「hash分片表」+ 用户确认。**目标环境版本见 §1.1.1**。公开基础建表文档当前主要写作 `shardkey=col`，`TDSQL_DISTRIBUTED BY HASH` 见于 DTS 二级分区材料，存在版本口径差异 |

#### 1.1.1 目标环境版本基线（**Rev.E 补录，来源：TDSQL 赤兔管理台「系统管理 → 版本管理」页面截图**）

| 部署模块 | 版本 |
|---|---|
| 集群 | `cluster-mpntjn9j`，环境标签「**开发测试环境（分离版 V22）**」 |
| **TDSQL 独立发布版本** | **`10.3.22.8.0-4_bpVXnJUB_20260114`** |
| **DB-TXSQL（数据库内核）** | **`5.7.36-v17-txsql-22.6.8-20250218`** 与 **`8.0.33-v24-txsql-22.6.9-20250509`**（两内核并存） |
| **Proxy** | **`proxy-22.4.5`** |
| DB-Agent | `agent-22.8.4` |
| Scheduler / Manager / OSS | `keeper-22.8.4` / `oss-22.8.4` / `oss-22.8.4` |
| 赤兔管理台 / PHP / Web | `chitu-22.8.4` / `7.4.33` / `nginx-1.24.0` |
| Online DDL | `onlineddl-22.8.4` |
| 扁鹊 | `clouddba-22.8.4` |
| ZooKeeper | `3.8.4-434ad34b6b780579b83b66f7d5005138afd1dbc6` |

**据此可以确认的事实**：

1. `TDSQL_DISTRIBUTED BY HASH(col)` 与 `shardkey=noshardkey_allset` **在同一份审核报告中同时出现**（#3 为前者，#5/#8/#11/#13 为后者），因此这两种形态是**按表类型分化**（分片表 / 广播表），**不是内核版本差异导致的两套写法**。
2. 该环境 DB-TXSQL 存在 5.7 与 8.0 双内核并存。本次样本不足以判定两内核的 `SHOW CREATE TABLE` 输出是否有差异，**设计不对此作任何假设**——识别逻辑对两种形态都支持，互不依赖。

**两条精度声明（不得省略）**：

- 上述版本取自截图中**当前选中的集群**，其环境标签为「开发测试环境（分离版 V22）」。**若生产集群版本与此不同，需另行补录**；本设计的语法支持范围不随版本收窄，故该差异不影响 Phase 1 的正确性，但影响本节证据的适用声明。
- 仍**不得**声称"所有 TDSQL 版本均输出相同 HASH 形态"。本节只主张：**该版本基线下的内核确实输出该形态**。

**`TDSQL_DISTRIBUTED BY KEY(col)` 不在本次支持范围**——用户需求、生产样本、J-1 均只涉及 `HASH`，Rev.C 曾无据地额外接受 `KEY`，属无授权的行为扩张，Rev.D 已删除（团队规约 R-11：只写有实测依据的东西）。

### 1.2 三条必须记住的否定判据

| 编号 | 内容 | 为什么要单列 |
|---|---|---|
| **NJ-1** | **普通索引（`KEY`）含分片键，不构成合规** | 现场 #3 的 `` KEY `cus_bas_corp_contact_IDX1` (`CUST_NO`,`DATA_VALID_TM`) `` 是普通索引，对合规性**不起任何作用**。#3 合规靠的是 `PRIMARY KEY (`ID`,`CUST_NO`)` |
| **NJ-2** | **分片键只在唯一索引里、不在主键里，不构成合规** | 判据是 J-2 **且** J-3，不是"主键 **或** 唯一索引" |
| **NJ-3** | J-3 是**每一个**唯一索引都要满足，不是"任意一个" | 因此唯一索引**不得展平成一个列并集**，必须逐个判断（O 评审报告 §6.4） |

### 1.3 现场 #3 按判据逐条核对（实测）

```
PRIMARY KEY (`ID`,`CUST_NO`),
KEY `cus_bas_corp_contact_IDX1` (`CUST_NO`,`DATA_VALID_TM`),     ← 普通索引，与合规性无关
KEY `cus_bas_corp_contact_IDX2` (`CONTACT_NO`,`DATA_VALID_TM`),  ← 普通索引，与合规性无关
) ENGINE=InnoDB ... TDSQL_DISTRIBUTED BY HASH(`cust_no`)

实测该表 UNIQUE 索引数量 = 0
```

| 判据 | 核对 | 结论 |
|---|---|---|
| J-1 | `TDSQL_DISTRIBUTED BY HASH(`cust_no`)`，平台显示「hash分片表」 | ✅ |
| J-2 | `cust_no` ∈ 主键 `(ID, CUST_NO)` | ✅ |
| J-3 | 无主键外唯一索引，空条件成立 | ✅ |

**→ #3 合规，R077 属误报，且其合规完全来自 J-2，与两个普通 `KEY` 无关。**

---

## 2. 现场与复现

### 2.1 用户报告的两项

| 序号 | 表名 | 尾部语法 | 实际触发 | 判定 |
|---|---|---|---|---|
| #3 | `cus_bas_corp_contact` | `TDSQL_DISTRIBUTED BY HASH(\`cust_no\`)` + `PRIMARY KEY (\`ID\`,\`CUST_NO\`)` | R077 (ERROR) | **合规分片表，误报** |
| #5 | `cus_name_list_type` | `shardkey=noshardkey_allset` | R077 (ERROR) + R054 (WARNING) | **合规广播表，均误报** |

### 2.2 本地 1:1 复现（实测）

14 张表原始 DDL 逐条灌回本地引擎（`instance_type="distributed"`、`table_metadata=None`，与在线元数据审核通道口径一致），**引擎命中规则集与生产报告逐字一致**；O 独立复现结果相同。

| 序号 | 表名 | 引擎命中 = 报告命中 |
|---|---|---|
| #3 | `cus_bas_corp_contact` | `[R077]` |
| #4 | `cus_bas_corp_contact_addr_20260511` | `[R001,R036,R037,R061,R077]` |
| #5 | `cus_name_list_type` | `[R036,R037,R054,R061,R077]` |
| #8 | `t_branch` | `[R036,R054,R061,R077]` |
| #11 | `t_dict` | `[R036,R054,R061,R063,R077]` |
| #13 | `t_product` | `[R036,R054,R061,R063,R077]` |

> **真实影响面比用户报告的两项更大**：#5/#8/#11/#13 共 4 张广播表全部同时误报 R077+R054，#3 误报 R077。合计 **5 张表被误报，占 35.7%**。

### 2.3 反向鉴别基准

**#4** 尾部无任何分片/广播声明，是真正的单表，R077 触发**正确**。全部验收以"#4 必须继续报 R077"为锚点。

---

## 3. 根因分析

### 3.1 缺陷 A：R077 不认识 `TDSQL_DISTRIBUTED BY HASH(col)`（违反 J-1）

`R077._extract_shard_key()`（`distributed.py:572-586`）只有三个取值来源，对 #3 实测全部落空：

| 顺序 | 来源 | 对 #3 的实测 |
|---|---|---|
| 1 | `parsed.table_options["SHARDKEY"\|"SHARD_KEY"]` | `{}`（sqlglot 整条降级为 Command） |
| 2 | `_SHARDKEY_RE` = `\bshardkey\b...` | 不匹配 |
| 3 | `_SHARD_KEY_RE` = `\bshard_key\b...` | 不匹配 |

三者全空 → 走进"未声明分片键"分支 → **ERROR 误报**。`_BROADCAST_RE` 同样不匹配。

**全仓库检索**：`TDSQL_DISTRIBUTED` 在整个代码库（`backend/` + `tests/` + `docs/`）**零出现**——这个语法从未进入设计视野。

### 3.2 缺陷 B：`noshardkey_allset` 被当成列名

`noshardkey_allset` 是内核表示"本表无分片键"的**哨兵值**，不是列名。实测 #5：

```
table_options       = {..., 'SHARDKEY': 'noshardkey_allset'}   ← sqlglot 正常解析
_extract_shard_key  = 'noshardkey_allset'                       ← 被当成列名取走
_collect_pk_cols    = ['id']
```

- **R077** → *"分片键 'noshardkey_allset' 不在主键或唯一索引中"*
- **R054** → *"分片键 'noshardkey_allset' 不在主键中"*

广播表不适用 J-2/J-3，这个哨兵永远不可能出现在主键里，**误报 100% 复现**。

**全仓库检索**：`noshardkey` / `allset` 在 `backend/engine/` 下**零出现**。

### 3.3 共同根因

| | |
|---|---|
| **根因** | R077/R054 的分片键识别按**开发人员手写 SQL** 形态设计，从未按 **TDSQL 内核 `SHOW CREATE TABLE` 真实输出**形态验证 |
| **触发条件** | v1.6.x 新增「在线元数据审核」，第一次把内核原样输出直接喂进规则引擎 |
| **为何测试没拦住** | 全部规则物料是手写形态；仓库 17 个 `.sql`、201 条语句中 `TDSQL_DISTRIBUTED`、`noshardkey_allset` **一条都没有** |

---

## 4. 修复方案与范围边界

### 4.1 本次实施（Phase 1）

| 编号 | 内容 |
|---|---|
| **FIX-1** | 新增**共享语法助手**：注释/字符串清洗 → DDL 表选项尾部锚定 → 提取 `TDSQL_DISTRIBUTED BY HASH(col)`（仅 `HASH`，仅裸标识符/反引号标识符） |
| **FIX-2** | 广播表哨兵**精确等值**判定 `noshardkey_allset`（大小写不敏感），R077 与 R054 同时放行 |
| **FIX-3** | **R054 与 R077 共同消费** FIX-1 的结果：R077 判"有无合法声明"，**R054 判 J-2/J-3**；R054 的唯一索引提取改为**逐个索引、支持反引号索引名** |

**FIX-3 是 Rev.D 相对 Rev.C 的关键补强**。Rev.C 以 NG-3 为由禁止 R054 识别 HASH，导致 HASH 路径下 J-2/J-3 无人负责——实测会把两类非合规表压成零违规。

### 4.2 明确的非目标

| # | 不做什么 | 为什么 |
|---|---|---|
| **NG-1** | 不修改 `backend/engine/parser/parser_legacy.py` | 净化方言尾子句会让 sqlglot 从"降级为 Command"变为"完整解析"，**全部 119 条规则**输入结构随之改变（§7.4 实测）。爆炸半径与本缺陷不成比例 → Phase 2 (ADJ-1) |
| **NG-2** | 不修改 `tdsql_connector.py` 的 `_detect_shard_info()` / `parse_shard_key_from_ddl()` | 同源带病，但服务 R020/R021/R022/R053/R056/R057/R060 元数据通道与「大表治理」，改它等于一次动 7 条规则 + 1 个业务模块 → Phase 2 (ADJ-2/3) |
| **NG-3** | **不放宽 `R077._UNIQUE_RE`**（**约束升级说明见 §8.1**） | 该正则喂给的是 R077 的宽松 `或` 分支；放宽会激活它并制造漏报。**注意：这与 FIX-3 修改 R054 的唯一索引提取是两件不同的事，风险方向相反**——详见 §8.1 |
| **NG-4** | 不收紧 R077 的"主键 **或** 唯一索引"口径 | ADJ-4，**用户已决策永久关闭** |
| **NG-5** | 不改动 `BROADCAST` 快速通道的冲突处理 | 见 §8.3 与 §13 对 X10 的答复：实测该场景**不是静默放行**（R054 会报），且行为与改前完全一致 |
| **NG-6** | 不改动任何规则的 `rule_id` / `severity` / `enabled` / `instance_scope` / `category` | 规则元数据变动会穿透规则集、门禁、报表统计 |
| **NG-7** | 不改动 R077/R054 之外的任何一条规则 | 报告中的 R001/R036/R037/R061/R063 均为独立正确判定 |

> **Rev.C 的 NG-3（"不给 R054 增加 TDSQL_DISTRIBUTED 识别"）已作废**——O 评审证明它正是漏报来源。

### 4.3 R054 与 R077 的职责划分

| | R077 (ERROR) | R054 (WARNING) | 对照判据 |
|---|---|---|---|
| 现有口径 | 分片键 ∈ 主键 **∪** 唯一索引（`或`） | 分片键 ∈ **主键**；且**每一个**唯一索引须含分片键 | **R054 与 J-2/J-3 一致；R077 偏宽松** |
| 本次职责 | J-1：有无合法分片声明 | J-2 + J-3：分片键是否合格 | — |

R077 的宽松 `或` 口径是既有缺陷（ADJ-4），**用户已决策永久保留、不得收紧**。因此本设计采用**兼容性隔离**：J-2/J-3 的完整判定由 R054 承担，**不得声称 R077 已完整执行 J-2/J-3**。

---

## 5. 详细设计（照图施工）

> 全部改动位于 `backend/engine/rules/distributed.py`。行号基于 `main @ 3c5167d`。

### 5.1 改动点 1：新增模块级常量与共享助手

**位置**：第 23 行（最后一条 `from backend.models import ...`）与第 26 行（`class R020ShardKeyInWhere`）之间的空行区。

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
# 设计约束（来源：v1.6.1.9 独立评审）：
#   1) 只接受有证据的 HASH，不接受 KEY；
#   2) 列名只接受裸标识符与反引号标识符，不接受引号字符串；
#   3) 必须在剔除注释/字符串字面量后、且锚定到表选项尾部再匹配，
#      否则表注释里的伪语法可绕过审核；
#   4) 广播哨兵必须精确等值，不得前缀猜测；
#   5) 本组助手由 R077 与 R054 共同消费——R077 判"有无声明"，
#      R054 判 J-2/J-3，二者不得各自猜一次字符串。
# ═══════════════════════════════════════════════════════════════

_NOSHARDKEY_ALLSET = "noshardkey_allset"   # 广播表(全局表)哨兵，精确值

_TDSQL_HASH_RE = re.compile(
    r"\btdsql_distributed\s+by\s+hash\s*\(\s*"
    r"(?:`(?P<quoted>[^`]+)`|(?P<bare>[a-z_][a-z0-9_]*))\s*\)",
    re.IGNORECASE,
)


def _strip_sql_noise(sql: str) -> str:
    """剔除 -- / # 行注释、/* */ 块注释、单双引号字符串字面量；保留反引号标识符。

    仅用于新增语法形态判定，不改变 parsed.raw_sql，也不影响既有取值路径。
    失败方向是"识别不到"→R077 照报（可见错误），符合团队规约 R-15。
    """
    out, i, n = [], 0, len(sql)
    while i < n:
        c = sql[i]
        if c == '`':                                   # 反引号标识符：整体保留
            j = sql.find('`', i + 1)
            if j < 0:
                out.append(sql[i:]); break
            out.append(sql[i:j + 1]); i = j + 1
        elif c in ("'", '"'):                          # 字符串字面量：整体丢弃
            q, j = c, i + 1
            while j < n:
                if sql[j] == '\\':
                    j += 2; continue
                if sql[j] == q:
                    if j + 1 < n and sql[j + 1] == q:  # '' / "" 转义
                        j += 2; continue
                    break
                j += 1
            out.append(' '); i = j + 1
        elif sql.startswith('--', i) or c == '#':      # 行注释
            j = sql.find('\n', i); out.append(' ')
            i = n if j < 0 else j
        elif sql.startswith('/*', i):                  # 块注释
            j = sql.find('*/', i + 2); out.append(' ')
            i = n if j < 0 else j + 2
        else:
            out.append(c); i += 1
    return ''.join(out)


def _ddl_options_tail(cleaned: str) -> str:
    """返回列定义清单右括号之后的表选项尾部；定位不到时返回空串（保守：不识别）。

    必须在 _strip_sql_noise 之后调用——字符串里的括号已被剔除，括号配对才可靠。
    """
    start = cleaned.find('(')
    if start < 0:
        return ""
    depth = 0
    for i in range(start, len(cleaned)):
        if cleaned[i] == '(':
            depth += 1
        elif cleaned[i] == ')':
            depth -= 1
            if depth == 0:
                return cleaned[i + 1:]
    return ""


def _extract_tdsql_hash_key(raw_sql: str) -> str:
    """从清洗且尾部锚定的 DDL 中提取 TDSQL_DISTRIBUTED BY HASH(col) 的分片键。"""
    tail = _ddl_options_tail(_strip_sql_noise(raw_sql))
    if not tail:
        return ""
    m = _TDSQL_HASH_RE.search(tail)
    if not m:
        return ""
    return (m.group('quoted') or m.group('bare')).strip('` ').lower()


def _is_broadcast_sentinel(value: str) -> bool:
    """精确判定广播表(全局表)哨兵值（大小写不敏感）。

    仅接受 noshardkey_allset。新增哨兵只能通过有出处的显式白名单扩展，
    不得改回前缀匹配——`noshardkey_*` 不是 TDSQL 保留命名空间。
    """
    return value.strip('`"\' ').casefold() == _NOSHARDKEY_ALLSET


_UNIQUE_IDX_RE = re.compile(
    r"\bunique\s+(?:key|index)\s*(?:`(?P<qname>[^`]+)`|(?P<bname>\w+))?\s*\(([^)]+)\)",
    re.IGNORECASE,
)


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

> **施工注意**：`re` 已在第 18 行 import；`ParsedSQL` 已在第 21 行 import。**不需新增任何 import**（团队规约 R-17）。

---

### 5.2 改动点 2：R077 `check()` 插入哨兵放行

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

        # v1.6.1.9: 广播表(全局表) —— shardkey=noshardkey_allset 是"本表无分片键"
        # 的哨兵值而非列名，主键/唯一索引约束均不适用。必须精确等值，不得前缀匹配。
        if shard_key_col and _is_broadcast_sentinel(shard_key_col):
            return None

        if not shard_key_col:
```

---

### 5.3 改动点 3：R077 `_extract_shard_key()` 追加第 4 来源

**位置**：`distributed.py:572-586`，**仅把末尾 `return ""` 替换为新增来源，前面一字不动**。

**改后**（末尾片段）：

```python
        if shard_match:
            return shard_match.group(1).strip('`"\' ').lower()
        # v1.6.1.9 回退来源3: TDSQL_DISTRIBUTED BY HASH(col)
        # 该语法下 sqlglot 整条降级为 Command，table_options 为空，前两个正则
        # 也不匹配，故必须走到这里才能拿到分片键。取值经注释/字符串清洗并锚定
        # 到表选项尾部，防止表注释中的伪语法绕过（见 _extract_tdsql_hash_key）。
        # 取到后仍照常执行 R077 既有的主键/唯一索引判定，不放宽任何判据。
        return _extract_tdsql_hash_key(raw_sql)
```

---

### 5.4 改动点 4：R054 `check()` 追加 HASH 来源与哨兵放行

**位置**：`distributed.py:262-268`。

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
            # v1.6.1.9 新增来源: TDSQL_DISTRIBUTED BY HASH(col)。
            # R054 必须与 R077 消费同一解析结果——否则 HASH 路径下 J-2/J-3
            # 无人负责，会把违规表压成零违规（v1.6.1.9 评审实测结论）。
            shard_key = _extract_tdsql_hash_key(parsed.raw_sql)
        if not shard_key:
            return None

        # v1.6.1.9: 广播表(全局表) —— noshardkey_allset 是哨兵值而非列名，
        # J-2/J-3 均不适用。该判定同时覆盖 table_metadata 通道
        # （_detect_shard_info 会把哨兵原样写进 meta["shard_key"]）与 raw_sql 通道。
        if _is_broadcast_sentinel(shard_key):
            return None

        # 检查主键是否包含分片键
```

---

### 5.5 改动点 5：R054 的唯一索引判定改为逐索引 + 支持反引号索引名

**位置**：`distributed.py` R054 `check()` 的 E2 段（`# E2: 检查唯一索引是否包含分片键` 起，至方法末尾 `return None` 前）。

**改前**（两段来源，其中正则不认反引号索引名，且"有结构化 UNIQUE 就不走正则"）：

```python
        # E2: 检查唯一索引是否包含分片键
        # 来源1: parsed.indexes / index_definitions
        for idx in parsed.indexes + parsed.index_definitions:
            if idx.get("type", "").upper() == "UNIQUE":
                idx_cols = {c.lower() for c in idx.get("columns", [])}
                if shard_key.lower() not in idx_cols:
                    idx_name = idx.get("name", "UNIQUE索引")
                    return self._make_violation(
                        f"{idx_name}未包含分片键 '{shard_key}'，TDSQL要求唯一索引必须包含分片键",
                    )
        # 来源2: raw_sql 正则回退(解析失败时 indexes 为空)
        if not any(idx.get("type", "").upper() == "UNIQUE" for idx in parsed.indexes + parsed.index_definitions):
            for m in re.finditer(r"unique\s+(?:key|index)\s+(\w+)?\s*\(([^)]+)\)", parsed.raw_sql, re.IGNORECASE):
                idx_name = m.group(1) or "UNIQUE索引"
                idx_cols = {c.strip('`"\' ').lower() for c in m.group(2).split(",")}
                if shard_key.lower() not in idx_cols:
                    return self._make_violation(
                        f"{idx_name}未包含分片键 '{shard_key}'，TDSQL要求唯一索引必须包含分片键",
                    )
        return None
```

**改后**（语义等价的收敛写法，逐索引判断；正则支持反引号索引名）：

```python
        # E2: 检查唯一索引是否包含分片键（J-3：每一个唯一索引都必须包含）
        # v1.6.1.9: 收敛为 _iter_unique_indexes()——逐个索引判断，不展平成列并集；
        # 正则回退支持反引号索引名（SHOW CREATE TABLE 输出的索引名恒带反引号，
        # 原正则的 \w* 匹配不上，导致 J-3 在真实 TDSQL 元数据上从不生效）。
        for idx_name, idx_cols in _iter_unique_indexes(parsed, parsed.raw_sql):
            if shard_key.lower() not in idx_cols:
                return self._make_violation(
                    f"{idx_name}未包含分片键 '{shard_key}'，TDSQL要求唯一索引必须包含分片键",
                )
        return None
```

> **⚠️ 这是本次唯一一处"收紧"改动。** 反引号命名的唯一索引若不含分片键，将**新增** R054 WARNING。
> **实测影响面**：仓库全部 17 个 `.sql`、201 条语句 + 生产 14 表 → **新增违规 0 条**（§7.3）。
> **与 NG-3 的区别**：本改动修的是 **R054** 的唯一索引提取，R054 的口径本就等于 J-2/J-3，修它是**向正确对齐**；NG-3 禁止修的是 **R077** 的 `_UNIQUE_RE`，那个正则喂给的是 R077 的宽松 `或` 分支，修它是**向宽松滑动**。两者风险方向相反，不可混为一谈（§8.1）。

---

### 5.6 改动点 6：同步修订 R077 用户可见文案（**必选**）

**位置**：`distributed.py:481-501` 的 `description` 与 `fix_suggestion`。

代码接受了新语法而规则目录仍只写 `SHARDKEY=列名` / `BROADCAST`，会让规则配置页、报告详情和排障继续给出不完整建议。

**改后**：

```python
    description = (
        "TDSQL分布式实例建表必须声明分片键或广播表标记，不允许创建单表；"
        "分片键必须是主键或唯一索引的字段。支持的分片键声明形态："
        "SHARDKEY=列名、TDSQL_DISTRIBUTED BY HASH(列名)；"
        "广播表(全局表)形态：BROADCAST、shardkey=noshardkey_allset"
    )
    fix_suggestion = (
        "请按目标实例支持的形态声明分片键或广播表。示例:\n"
        "  CREATE TABLE t1 (...) ENGINE=InnoDB SHARDKEY=user_id\n"
        "  CREATE TABLE t1 (...) ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(`user_id`)\n"
        "  CREATE TABLE t1 (...) ENGINE=InnoDB BROADCAST\n"
        "  CREATE TABLE t1 (...) ENGINE=InnoDB shardkey=noshardkey_allset\n"
        "分片表的分片键必须是主键(或主键的一部分)，且必须包含在每一个唯一索引中。"
    )
```

**测试影响核查（实测）**：仓库内对 `description` 的断言全部为"非空 / 长度 ≥ 5~8"（`test_sit_rules.py:203-204`、`test_uat_rules.py:106-107,119`、`test_sit_v1_rules.py:445`、`test_uat_v1.py:441-445`），**无精确字符串断言**，本改动不会破坏既有测试。

---

### 5.7 改动点 7：`_UNIQUE_RE` 原子变更护栏（**必选，纯注释，零行为**）

**位置**：`distributed.py:519`（`# 表级 UNIQUE KEY/INDEX 列提取正则（回退方案）` 上方）。

```python
    # ⚠️ 不得单独放宽本正则：R077 仍保留 legacy 的"主键 或 唯一索引"判定
    #    （ADJ-4，已决策不收紧）。本正则一旦认出更多唯一索引，就会激活那个
    #    宽松分支并产生漏报。修改本正则、或让 parsed.indexes 开始产出 UNIQUE
    #    条目时，必须在同一次提交内把 R077 判定对齐 J-2/J-3，并通过
    #    tests/test_r077_r054_tdsql_syntax.py 中裸索引名/反引号索引名两组
    #    同语义用例。不得拆分提交。
    #    背景：docs/DESIGN-v1.6.1.9-TDSQL分片表与广播表建表语法识别缺陷修复详细设计说明书.md
```

> Rev.C 原文写的是"永久保留坏正则"，经评审更正为**原子变更约束**——见 §8.1。

---

### 5.8 改动汇总

| # | 类/位置 | 类型 | 净增行 |
|---|---|---|---|
| 1 | 模块级 | 新增常量 + 4 个助手函数 + 注释 | +115 |
| 2 | `R077.check()` | 新增 4 行 | +4 |
| 3 | `R077._extract_shard_key()` | 替换末行 `return ""` | +5 |
| 4 | `R054.check()` 取值段 | 新增 10 行 | +10 |
| 5 | `R054.check()` E2 段 | 收敛替换（净减） | −12 |
| 6 | `R077.description` / `fix_suggestion` | 文案替换 | +6 |
| 7 | `R077._UNIQUE_RE` 上方 | 纯注释护栏 | +7 |

**合计：1 个文件、2 个类、7 处，净增约 135 行；无签名变更、无新增依赖、无 import 变更、无 schema 变更、无接口变更、无前端变更。**

---

## 6. 不需要改动的部分（施工时逐项确认）

| 对象 | 结论 | 依据 |
|---|---|---|
| `R077._UNIQUE_RE` **正则本体** | **不改**（只在上方加注释） | NG-3 / §8.1 |
| `R077._PK_RE` / `_SHARDKEY_RE` / `_SHARD_KEY_RE` / `_BROADCAST_RE` | **不改** | 既有行为保留，新来源追加在其后 |
| `R077._collect_pk_cols()` / `_collect_unique_index_cols()` | **不改** | R077 判据本体 |
| `R077` 的 `或` 判定行 | **不改** | NG-4，ADJ-4 用户已关闭 |
| `backend/engine/parser/**` | **不改** | NG-1 |
| `backend/services/tdsql_connector.py` | **不改** | NG-2 |
| `backend/engine/rules/__init__.py` / `backend/api/**` / `backend/schema/**` / 前端 | **不改** | 规则未增减，无接口/schema/展示变更 |
| R020/R021/R022/R053/R055/R056/R057/R058/R059/R060 | **不改** | 实测：10 条均**只从 `table_metadata` 取分片键**，不解析 DDL，与本次零交集 |
| 其余 107 条规则 | **不改** | 与分片键判定无关 |

---

## 7. 影响面分析

### 7.1 Rev.C 的错误论证（已撤回）

Rev.C §7.1 声称"行为差集恰好等于误报集合""不可能压制真实违规"。**该论证不成立**，Rev.D 予以撤回。

错误在于：把**控制流可达性**（新分支放在函数末尾）当成了**语义安全性**。"前 3 源全空"这个集合里除了误报，还包含：注释/字符串中的伪 HASH 子句、真实存在但违反 J-2/J-3 的 HASH 表、单引号参数、无据的 `KEY` 形态。201 条语料没覆盖这些，只能说明**测试集未覆盖**，不能证明集合相等。

**O 评审实测的 6 条反例，Rev.C 全部压成零违规**：

| 反例 | 改前 | Rev.C | Rev.D |
|---|---|---|---|
| HASH 键 ∈ 主键、但唯一索引不含键（违反 J-3） | R077 | ★零违规 | **R054 触发** |
| HASH 键 ∉ 主键、只在裸名唯一索引中（违反 J-2） | R077 | ★零违规 | **R054 触发** |
| 表注释伪造 `TDSQL_DISTRIBUTED BY HASH(id)` | R077 | ★零违规 | **R077 触发** |
| `shardkey=noshardkey_shadow`（真实列，∉ 主键） | R077+R054 | ★零违规 | **R077+R054 触发** |
| `HASH('id')` 单引号 | R077 | ★零违规 | **R077 触发** |
| `BY KEY(id)` 无据形态 | R077 | ★零违规 | **R077 触发** |

### 7.2 Rev.D 的诚实影响面表述

| 改动 | 可达条件 | 效果方向 | 可能新增违规？ |
|---|---|---|---|
| FIX-1（R077 识别 HASH） | 前 3 源全空 **且** 清洗后尾部含合法 HASH 子句 | 消除误报；J-2 违规仍由 R077 报 | 否（放行集变化，判据未动） |
| FIX-2（精确哨兵） | 分片键取值精确等于 `noshardkey_allset` | 消除误报 | 否 |
| FIX-3a（R054 识别 HASH） | R054 原有两源全空 **且** 存在合法 HASH 子句 | **恢复 J-2/J-3 判定** | **是**——但这些表改前均已触发 R077，**总判定"违规/不违规"不变**，只是由更准确的规则与消息承担 |
| FIX-3b（R054 唯一索引支持反引号） | 存在反引号命名的唯一索引且不含分片键 | 使 J-3 在真实 TDSQL 元数据上生效 | **是**——**这是本次唯一的净收紧**，实测语料影响 0 条（§7.3） |

### 7.3 全语料实测漂移扫描（Rev.D）

```
输入 201 条（仓库 17 个 .sql 切分）+ 生产 14 表
成功解析 201 条，异常 0 条          ← 无静默跳过
判定发生变化: 5 条

  现场#3  cus_bas_corp_contact   R077/--    → --/--
  现场#5  cus_name_list_type     R077/R054  → --/--
  现场#8  t_branch               R077/R054  → --/--
  现场#11 t_dict                 R077/R054  → --/--
  现场#13 t_product              R077/R054  → --/--
```

> **变化的 5 条恰好就是 5 处误报现场；201 条既有语料判定 100% 逐字不变，且 FIX-3b 的收紧在既有语料上新增违规 0 条。**
> 扫描器已按评审要求输出输入总数/解析成功数/异常数，异常不再静默 `continue`。

### 7.4 必须向用户明示的副作用（非本次引入）

`TDSQL_DISTRIBUTED BY HASH(...)` 会让 **sqlglot 整条降级为 Command**：

| | `columns` | `indexes` | `table_options` | 命中规则 |
|---|---|---|---|---|
| #3 原样 | **0** | 0 | 0 | `[R077]` |
| #3 摘掉尾子句（反事实实验） | 25 | 2 | 3 | `[R036,R037,R061,R077]` |
| #4 对照（结构相同、无尾子句） | 34 | 1 | 3 | `[R001,R036,R037,R061,R077]` |

**#3 目前不仅被误报 R077，还被漏审了 R036/R037/R061。** Phase 1 修完后 #3 显示"零违规"——这个"零"是**干净的 R077/R054 判定 + 仍然存在的漏审**叠加出来的，**不代表该表通过了全量审核**。根治需 Phase 2 (ADJ-1)，必须写入交付说明。

---

## 8. 已知邻接缺陷

| 编号 | 位置 | 问题 | 后果 | 处置 |
|---|---|---|---|---|
| **ADJ-1** | `parser_legacy.py` | `TDSQL_DISTRIBUTED BY ...` 导致 sqlglot 整条降级 | 这类分片表被全套结构类规则漏审 | Phase 2：交给 sqlglot 前净化方言尾子句，**`parsed.raw_sql` 必须保留原文**。需全量回归 + 用户确认"报告违规数会上升" |
| **ADJ-2** | `tdsql_connector.py:162 / :404` | 只认 `SHARDKEY=`；把 `noshardkey_allset` 原样写进 `meta.shard_key`（docstring 与实际不符） | R020/R021/R022/R053/R056/R057/R060 元数据通道对广播表连带误报；「大表治理」展示哨兵值 | Phase 2：统一到共享解析工具 |
| **ADJ-3** | `tdsql_connector.py:1546` | 引用**未定义变量** `create_sql_upper`，`NameError` 被 `except Exception: pass` 静默吞掉 | 该类广播表识别与分片规则查询整段成死代码，`is_broadcast_table` 永不为 True | Phase 2，**建议优先级最高——真实静默失效** |
| **ADJ-4** | `R077.check()` 判定口径 | "分片键 ∈ 主键 **或** 唯一索引"，比 J-2 宽松且缺 J-3 | 手写裸索引名形态下 R077 漏报（R054 兜底） | **🔒 用户已决策：关闭，永不排期** |
| **ADJ-5** | `R077._UNIQUE_RE`、`parsed.indexes` 的 UNIQUE 产出 | 对反引号索引名失配 | 与 ADJ-4 耦合：单独修会激活 R077 宽松分支 → 漏报 | **不得单独修改**（原子变更约束，§8.1）；护栏见改动点 7 |
| **ADJ-6** | `R077` 的 `BROADCAST` 快速通道 | `BROADCAST` 与真实 `shardkey=col` 冲突声明时不做冲突检测 | 见 §8.3 | **🔒 用户已决策：不进 Phase 2，不改动。** 仅以特征化测试锁定现状 |

### 8.1 ADJ-5：原子变更约束（Rev.D 更正）

Rev.C 写的是"**永久禁止修复**"，O 评审否决了这个结论，Rev.D 采纳更正。

**成立的部分**（实测复现，O 独立验证一致）：

| 场景 | R077 结果 |
|---|---|
| 保持 `_UNIQUE_RE` 不动 | **触发**（符合 J-2） |
| 只修 `_UNIQUE_RE`、不动 R077 宽松 `或` 判定 | **不触发（漏报）** |

这证明了一个真实的**变更耦合**：不能单独修唯一索引提取而不同时对齐判定。

**不成立的部分**：由此推出"应永久保留缺陷"是错的。理由（采纳 O）：

1. 当前正确性依赖两个缺陷相互抵消，是**偶然行为**，不是稳定设计；
2. `_collect_unique_index_cols()` 还有 `parsed.indexes` 这一来源，**未来 sqlglot 升级可能在不改正则的情况下激活宽松分支**——"禁止改正则"根本挡不住；
3. 裸索引名与反引号索引名产生不同审核结果，本身已是确定的行为不一致；
4. 把正确性绑定在实现 bug 上会阻断未来正常重构。

**正确表述（写入代码注释与检查单）**：

> 修改 `R077._UNIQUE_RE`、或让 `parsed.indexes` 开始产出 UNIQUE 条目时，**必须在同一次提交内**把 R077 判定对齐 J-2/J-3，并通过裸索引名 / 反引号索引名两组同语义测试。**不得拆分提交。**

### 8.2 ADJ-4 关闭后被接受的残留漏报

手写形态 `UNIQUE KEY 裸索引名 (分片键)` 且分片键不在主键时，R077 漏报。**R054 仍会触发 WARNING 兜底**（实测用例 C4/X7）。已知、已接受、本次不处理。

### 8.3 ADJ-6：`BROADCAST` 与 `shardkey=` 冲突声明的实测现状

O 评审要求"冲突声明不得被快速通道静默放行"。**实测现状（改前 = Rev.D，本次未改动）**：

| 冲突形态 | R077 | R054 | 是否静默放行 |
|---|---|---|---|
| `BROADCAST` + `shardkey=sk`，`sk` ∉ 主键 | 放行 | **触发** | **否**——R054 兜底 |
| `BROADCAST` + `shardkey=sk`，`sk` ∈ 主键 | 放行 | 放行 | **是**（零违规） |

第二行确实是静默放行，但：① 属**既有行为**，改前改后逐字一致，非本次引入；② 修它需改动 `BROADCAST` 快速通道，会让既有语句**新增违规**，属行为扩张，与用户"严控范围"冲突。

**处置（Rev.E 更新）**：**用户已决策 ADJ-6 不进 Phase 2、不改动。** 本次及后续均不改 `BROADCAST` 快速通道（NG-5）。

仅保留一项动作：把上表两行落为**特征化测试**（用例 X10）锁定现状——它不主张当前行为正确，只保证**行为一旦变化立即报警**。这样既尊重"不改"的决策，又不让这个已知点在将来悄悄漂移。

---

## 9. 验收测试方案

### 9.1 用例矩阵（29 条，Rev.D 原型已全量实测 **29/29 通过**）

> **前置条件**：`instance_type="distributed"`；除注明外 `table_metadata=None`。R077/R054 的 `instance_scope` 为 DISTRIBUTED，集中式跳过由既有 `test_instance_scope_rules.py` 覆盖，本矩阵不重复。
> **落库要求**：必须落为 `tests/test_r077_r054_tdsql_syntax.py`（普通 pytest，进入默认 `pytest tests/` 门禁），**不得以文档片段代替门禁**。
> ★ = 反向鉴别用例；**X 系列为 O 评审要求补充**。

| 用例 | 场景 | 改前 | 期望（Rev.D） |
|---|---|---|---|
| P1 | 现场#3 HASH 分片表，`cust_no` ∈ 主键 | R077 | 零违规 |
| P2 | 现场#5 `shardkey=noshardkey_allset` | R077+R054 | 零违规 |
| P3 | 现场#8 `t_branch`（含 UNIQUE KEY） | R077+R054 | 零违规 |
| P4 | 现场#11 `t_dict` | R077+R054 | 零违规 |
| P5 | 现场#13 `t_product` | R077+R054 | 零违规 |
| **N1★** | 现场#4 无任何分片声明 | R077 | **R077** |
| **N2★** | `SHARDKEY=cust_id` ∉ 主键 | R077+R054 | **R077+R054** |
| **N3★** | HASH 分片键 ∉ 主键 | R077 | **R077+R054** |
| **N8★** | HASH 分片键只在普通 `KEY` 里（守 NJ-1） | R077 | **R077+R054** |
| **N4★** | 反引号 UNIQUE 不含分片键且 ∉ 主键 | R077+R054 | **R077+R054** |
| **N5★** | 普通 `KEY` 含分片键（守 NJ-1） | R077+R054 | **R077+R054** |
| **N7★** | 注释含 `noshardkey_allset`，真实分片键合规 | 零违规 | 零违规 |
| C1 | 合规分片表（分片键 ∈ 主键） | 零违规 | 零违规 |
| C2 | `BROADCAST` 关键字 | 零违规 | 零违规 |
| C3 | 分片键在反引号 UNIQUE 但 ∉ 主键（守 NJ-2） | R077+R054 | **R077+R054** |
| C6 | HASH 大小写混排 + 多空格，键 ∈ 主键 | R077 | 零违规 |
| C7 | CTAS | 零违规 | 零违规 |
| C8 | 临时表 | 零违规 | 零违规 |
| C9 | 非建表语句（SELECT） | 零违规 | 零违规 |
| **X1★** | 表注释含 HASH 子句，无真实声明 | R077 | **R077** |
| **X2★** | 块注释 `/* */` 含 HASH 子句 | R077 | **R077** |
| **X2b★** | 行注释 `--` 含 HASH 子句 | R077 | **R077** |
| **X3★** | `HASH('id')` 单引号非标识符 | R077 | **R077** |
| **X4★** | `BY KEY(id)` 无权威依据 | R077 | **R077** |
| **X5★** | `shardkey=noshardkey_shadow`（真实列 ∉ 主键） | R077+R054 | **R077+R054** |
| **X6★** | HASH 键 ∈ 主键，反引号 UNIQUE 不含键（违反 J-3） | R077 | **R054** |
| **X7★** | HASH 键 ∉ 主键、只在裸名 UNIQUE（违反 J-2） | R077 | **R054** |
| **X8★** | 两个 UNIQUE 仅一个含 HASH 键（守 NJ-3"每一个"） | R077 | **R054** |
| X12 | 现场#3 换行/大小写/空白变体 | R077 | 零违规 |

**补充测试（非违规判定，但必须落库）**：

| 用例 | 场景 | 期望 |
|---|---|---|
| X9 | DDL 含 `shardkey=noshardkey_allset` **且** `table_metadata` 也返回该哨兵 | 零违规（覆盖元数据通道） |
| X10 | `BROADCAST` + 真实 `shardkey=col` 冲突（两种变体） | **特征化测试**：锁定 §8.3 表中的现状，行为变化即报警 |
| X11 | 裸索引名与反引号索引名的同语义 DDL | **两者结果必须一致**（实测已一致） |
| X13 | ADJ-5 承重性对照：只修 `_UNIQUE_RE` 不动 R077 判定 | **断言会漏报** —— 防止未来依赖升级悄悄激活宽松分支 |

### 9.2 端到端验收（在线元数据审核通道）

| 序号 | 表 | 改前 | 验收期望 |
|---|---|---|---|
| #3 | cus_bas_corp_contact | R077 | **无 R077** |
| #4 | cus_bas_corp_contact_addr_20260511 | R001,R036,R037,R061,R077 | **原样不变（含 R077）** |
| #5 | cus_name_list_type | R036,R037,R054,R061,R077 | **R036,R037,R061** |
| #8 | t_branch | R036,R054,R061,R077 | **R036,R061** |
| #11 | t_dict | R036,R054,R061,R063,R077 | **R036,R061,R063** |
| #13 | t_product | R036,R054,R061,R063,R077 | **R036,R061,R063** |
| 其余 8 张 | #1,#2,#6,#7,#9,#10,#12,#14 | — | **逐条原样不变** |

### 9.3 回归门槛

| 项 | 基线 | 门槛 |
|---|---|---|
| 4 个规则测试文件 | **168 passed, 0 failed**（A 与 O 实测一致） | 必须仍为 168 passed / 0 failed |
| 4 文件 + `test_distributed.py` | **182 passed, 0 failed**（O 实测） | 不得下降 |
| 全量 `pytest tests/` | **收集总数 1313**。O 环境 1313 passed / 0 failed / 0 skipped；A 环境 1284 passed / 0 failed / **29 skipped** | **收集总数仍为 1313；0 failed；skipped 数不得高于同一环境改前基线** |
| 规则总数 | 119（92 ALL + 27 DISTRIBUTED） | 必须不变 |
| 全语料漂移扫描 | — | 输入 201 条，**异常必须为 0**，判定变化**必须 = 5** 且恰为 P1–P5。**出现第 6 条即停止施工，回到评审** |

> **关于基线数字的说明（Rev.D 更正）**：Rev.C 写的 "1312 passed / 1 skipped" 取自 v1.6.1.8 台账，**已过期**。经复核：A 与 O 的**收集总数一致均为 1313**；差异仅在于 O 环境配置了 `TDSQL_TEST_ADMIN_USER` / `TDSQL_TEST_ADMIN_PASSWORD`，29 条需可登录后端的集成用例得以实际执行，A 环境则跳过。**两份数据不矛盾**。另需 uvicorn 在 127.0.0.1:8000 运行，否则 8 条前端集成用例 `ConnectionRefusedError` 失败（环境问题，非产品缺陷）。故门槛按"同环境自比"表述，不锁死绝对数字。

---

## 10. 风险与回滚

| 风险 | 等级 | 对策 |
|---|---|---|
| FIX-3b 收紧导致存量报告新增 R054 WARNING | **中** | 实测语料影响 0 条；R054 为 WARNING 不阻断门禁；已在 §5.5 显式标注为"本次唯一收紧"，交付说明须写明 |
| #3 修完后显示"零违规"但仍被漏审 | **中** | §7.4 明示；根治需 Phase 2 (ADJ-1)；**必须写入交付说明** |
| 后来者单独放宽 `R077._UNIQUE_RE` 造成漏报 | **中** | 改动点 7 代码注释 + §8.1 论证 + §12 检查单硬项 + X13 断言测试，四重设防 |
| `sqlglot` 升级后 `parsed.indexes` 开始产出 UNIQUE 条目，静默激活 R077 宽松分支 | **中** | X13 断言测试可捕获；已在 §8.1 点名该路径 |
| `_strip_sql_noise` 对 `--` 的处理比 MySQL 严格（MySQL 要求 `-- ` 带空格） | 低 | 失败方向是"识别不到分片键"→ R077 照报（可见错误），符合团队规约 R-15 |
| 反引号标识符内含括号导致 `_ddl_options_tail` 配对失败 | 低 | 返回空串 → 不识别 → R077 照报（可见错误） |
| 回滚 | — | 单文件纯增量，`git revert` 单提交即回到 v1.6.1.8 行为；无数据/schema/接口残留 |

---

## 11. 修订记录

| 版本 | 触发 | 核心变化 |
|---|---|---|
| **Rev.A** | 初稿 | 提出 FIX-1/2/3（含放宽 `R077._UNIQUE_RE`） |
| **Rev.B** | 用户口径纠正 | 撤销放宽 `_UNIQUE_RE`（方向错误，会压制真实违规）；新增 §1 判据章节与 N8★ 用例 |
| **Rev.C** | 用户决策 ADJ-4 关闭 | ADJ-5 升级为"永久禁令"；新增 §8.1、改动点 5、附录 B |
| **Rev.D** | **智能体 O 独立评审（BLOCK）** | **见 §13 逐条答复**：7 项强制整改全部落实；撤回 Rev.C 的错误论证；新增清洗+尾部锚定、精确哨兵、R054 共享 HASH、J-3 逐索引判定、文案同步、X1–X13 反例 |
| **Rev.E**（本版） | 用户提供版本截图 + ADJ-6 决策 | ① §1.1.1 补录目标环境 TDSQL 版本基线（独立发布版本 `10.3.22.8.0-4`、内核 `5.7.36-v17-txsql-22.6.8` / `8.0.33-v24-txsql-22.6.9` 双内核并存、`proxy-22.4.5`），并据此确认两种语法形态是**按表类型分化**而非版本差异；② **ADJ-6 经用户决策不进 Phase 2、不改动**，仅保留特征化测试；③ 清除 §1.1 的施工前置待办 |

---

## 12. 施工检查单

**范围控制**

- [ ] 只改 `backend/engine/rules/distributed.py`（`git diff --stat` 只有一行）
- [ ] 只改 `R077CreateTableMustHaveShardKey`、`R054ShardKeyMustBePrimaryKey` 两个类 + 模块级助手
- [ ] 无新增 import（`re` 第 18 行、`ParsedSQL` 第 21 行已有）
- [ ] 未改动 `rule_id` / `severity` / `enabled` / `instance_scope` / `category`

**判据与安全（Rev.D 重点）**

- [ ] 广播哨兵为**精确等值** `noshardkey_allset`，**未使用前缀/正则猜测**
- [ ] HASH 正则**只接受 `HASH`**，无 `KEY`
- [ ] HASH 列名**只接受裸标识符与反引号**，**不接受单/双引号字符串**
- [ ] HASH 匹配在 `_strip_sql_noise()` **之后**、且经 `_ddl_options_tail()` **尾部锚定**
- [ ] **`R077._UNIQUE_RE` 的正则一个字符都没动**（只在上方加注释）—— 动手前必读 §8.1
- [ ] 未收紧 R077 的 `或` 判定（ADJ-4 已关闭）
- [ ] 未改动 `BROADCAST` 快速通道（NG-5）
- [ ] R054 的唯一索引判定**逐个索引**进行，**未展平成列并集**（守 NJ-3）
- [ ] R054 的 HASH 来源在原有两源**之后**，哨兵判定在 `if not shard_key: return None` **之后**（同时覆盖元数据与 raw_sql 通道）

**验收**

- [ ] §9.1 的 29 条矩阵全部通过；**X1–X8 全部通过**（O 评审反例）
- [ ] X9/X10/X11/X13 四条补充测试落库
- [ ] §9.2 端到端 14 表逐表符合期望，**#4 仍报 R077**
- [ ] 用例已落为 `tests/test_r077_r054_tdsql_syntax.py` 并进入默认 `pytest tests/` 门禁
- [ ] 漂移扫描：输入 201、**异常 0**、变化 = 5 且恰为 P1–P5
- [ ] 4 个规则文件仍 168 passed；全量收集总数仍 1313、0 failed、skipped 不高于同环境改前基线
- [ ] 规则总数仍为 119（92 ALL + 27 DISTRIBUTED）

**交付说明**

- [ ] 写明 §7.4 的 #3 漏审副作用（"零违规" ≠ "已全量审核"）
- [ ] 写明 §5.5 FIX-3b 是本次唯一收紧，可能新增 R054 WARNING
- [ ] 写明 ADJ-1/2/3 留待 Phase 2（ADJ-3 优先）；**ADJ-4、ADJ-6 已由用户决策关闭**；ADJ-5 为原子变更约束
- [ ] 建议把 §8.1 原子变更约束补录进 `docs/GUIDE-团队施工规约.md`

---

## 13. 对智能体 O 独立评审的逐条答复

评审结论：**有条件否决（BLOCK）**。A 复核后**全部实测复现 O 的 6 条反例，认可其阻断结论**。

### 13.1 七项强制整改

| # | O 的要求 | A 的答复 | 落实位置 |
|---|---|---|---|
| 1 | 广播哨兵改精确 `noshardkey_allset` | **完全认可**。前缀匹配是我主动做的无据取舍，违反团队规约 R-11（只写有实测依据的东西）。实测 `shardkey=noshardkey_shadow`（真实列 ∉ 主键）在 Rev.C 下被压成零违规 | §5.1 `_is_broadcast_sentinel()`；用例 X5 |
| 2 | 只支持有证据的 `HASH`，删除 `KEY` 与单引号列名 | **完全认可**。`BY KEY` 是我无据添加的"健壮性"扩展；单引号在 MySQL 语义中是字符串不是标识符。两者实测均可绕过 | §5.1 `_TDSQL_HASH_RE`；用例 X3/X4 |
| 3 | HASH 匹配改为清洗 + 尾部锚定 | **完全认可**。实测表注释伪造 `COMMENT='TDSQL_DISTRIBUTED BY HASH(id)'` 在 Rev.C 下被压成零违规。我原先辩称"非本次引入"**不成立**——该绕过路径正是 FIX-1 新增的 | §5.1 `_strip_sql_noise()` / `_ddl_options_tail()`；用例 X1/X2/X2b |
| 4 | R054 与 R077 共享解析结果，补齐 HASH 场景 J-2/J-3 | **完全认可，这是最重的一条**。Rev.C 的 NG-3 直接制造了两类新漏报。**补充实测**：只让 R054 取到 HASH 键还不够——R054 原有的唯一索引正则同样不认反引号索引名，X6/X8 仍会零违规，故必须一并把 J-3 改为逐索引 + 支持反引号 | §5.4 + §5.5；用例 X6/X7/X8 |
| 5 | §8.1 改为"原子变更约束"，改动点 5 改必选 | **完全认可**。O 的第 2 条理由最有力：`parsed.indexes` 是另一条来源，未来 sqlglot 升级可在不改正则的情况下激活宽松分支——"禁止改正则"根本挡不住。已补 X13 断言测试封堵该路径 | §8.1；改动点 7（必选）；用例 X13 |
| 6 | 附录 B 落成可运行 pytest；更新基线 | **认可**。**并主动更正我自己的一处不实陈述**：我上一轮向用户报告"附录 B 逐字照抄跑通"，实际上 B.3 属实，**B.4 是我在验证时自行补了驱动代码才跑出结果的**，文档原文只有类定义和字符串，照抄不会产生任何输出。O 指出得对。基线数字见下方 13.2 | §9.3；附录 B 改为落库要求 |
| 7 | 同步修订 R077 用户可见描述/建议 | **认可**。已实测核查：仓库对 `description` 的断言均为"非空/长度"，**无精确字符串断言**，改动安全 | §5.6 |

### 13.2 关于全量回归基线的复核

O 报告 **1313 passed / 0 failed / 0 skipped**；A 环境实测 **1284 passed / 0 failed / 29 skipped**。

**两份数据不矛盾，收集总数一致均为 1313。** 差异原因已定位：29 条集成用例需要 `TDSQL_TEST_ADMIN_USER` / `TDSQL_TEST_ADMIN_PASSWORD`（可登录后端的管理员口令），O 环境已配置故实际执行，A 环境未配置故跳过。此外需 uvicorn 运行，否则 8 条前端集成用例 `ConnectionRefusedError` 失败。

**A 确认 O 的数字对，Rev.C 引用的 "1312/1 skipped" 是 v1.6.1.8 台账旧值，已过期。** 门槛改为"同环境自比"表述（§9.3）。

### 13.3 部分认可 / 不认可的条目

| # | O 的要求 | A 的答复与理由 |
|---|---|---|
| **X10**「`BROADCAST` 与真实 `shardkey=col` 冲突不得被快速通道静默放行」 | **部分认可——测试采纳，行为不改。**<br>**实测发现 O 的前提只在一种变体下成立**：`sk` ∉ 主键时 **R054 会触发**，并非静默放行；只有 `sk` ∈ 主键时才是零违规。<br>对第二种变体不予修改，理由：① 属**既有行为**，改前改后逐字一致，非本次引入；② 修它需改动 `BROADCAST` 快速通道，会让既有语句**新增违规**，属行为扩张，与用户"严控修改范围、绝不影响其他核心规则"的硬约束冲突；③ 该形态只可能来自手写 SQL，TDSQL 内核不会同时输出两种声明。<br>**已采纳的部分**：把两种变体落为**特征化测试**锁定现状（用例 X10），行为一旦变化立即报警。<br>**Rev.E 更新**：该项记为 ADJ-6 上报用户后，**用户已决策不进 Phase 2、不改动**。特征化测试保留。 |
| **O 评审 §6.1「DistributionSpec 类型」** | **认可其意图，实现方式调整。**<br>O 提出用 `DistributionSpec(kind/shard_key/syntax)` 承载解析结果。A 采用**等价但更小**的实现：一组共享的模块级助手函数（`_extract_tdsql_hash_key` / `_is_broadcast_sentinel`），由 R077 与 R054 共同消费。<br>理由：引入新数据类型会要求重写两条规则**既有的**取值顺序（R077 是 `table_options→正则1→正则2`，R054 是 `metadata→正则`，二者本就不同），那会把爆炸半径从"新增分支"扩大到"重写既有路径"。当前实现已满足 O 的核心诉求——**两条规则消费同一份解析逻辑，不各自猜一次字符串**。<br>若复审坚持要 `DistributionSpec` 类型，A 可实现，但需接受既有取值顺序被重写的额外回归面。 |
| **O 评审 §6.4「唯一索引不能展平成列并集」** | **完全认可，但需指出适用位置**：该问题在 **R077** 的 `_collect_unique_index_cols()`（展平）而非 R054。由于 ADJ-4 已由用户关闭、R077 的 `或` 判定不得收紧，**J-3 的完整判定已整体交由 R054 承担**，R054 侧已按"逐个索引"实现（§5.5）。R077 的展平实现保持原样，属 ADJ-4 关闭的既有范围。 |

### 13.4 A 自查发现、O 未提及的一项

`_strip_sql_noise()` 对 `--` 的处理比 MySQL 严格（MySQL 要求 `-- ` 带空格才算注释，`a--b` 不是）。这会让极少数含 `--` 的标识符/表达式被误剔除，后果是**识别不到分片键 → R077 照报**——失败方向是可见错误而非静默放行，符合团队规约 R-15（失败不对称性：宁可报错也不要静默）。已记入 §10 风险表。

---

## 附录 A：实测证据清单

| 编号 | 结论 | 证据 |
|---|---|---|
| E-1 | 生产报告可 1:1 本地复现 | 14 表逐条比对，6 张重点表规则集逐字一致；O 独立复现一致 |
| E-2 | #3 的 `table_options`/`columns`/`indexes` 全空 | 解析器输出实测 `{}` / `0` / `[]` |
| E-3 | #5/#8/#11/#13 的 `_extract_shard_key` 返回 `'noshardkey_allset'` | 直调规则私有方法实测 |
| E-4 | #3 的 `_collect_pk_cols` 含 `cust_no`，UNIQUE 索引数 = 0 | 实测 `['cust_no','id']` → J-2 满足、J-3 空条件成立 |
| E-5 | `TDSQL_DISTRIBUTED` 全仓库零出现 | `grep -rn` 全库检索 |
| E-6 | `noshardkey`/`allset` 在 `backend/engine/` 零出现 | `grep -rn` 检索 |
| E-7 | 摘掉尾子句后 #3 多命中 R036/R037/R061 | 反事实实验（→ ADJ-1） |
| E-8 | **Rev.C 会把 6 类非合规语句压成零违规** | O 评审反例，A 独立复现 **6/6 成立** |
| E-9 | Rev.D 用例矩阵 **29/29 通过**（含 O 的 X1–X8） | Rev.D 原型全量矩阵 |
| E-10 | Rev.D 全语料漂移：输入 201、异常 0、变化 5 条且恰为误报现场 | Rev.D 原型漂移扫描 |
| E-11 | 只让 R054 取到 HASH 键不足以满足 X6/X8，必须同时修 R054 的唯一索引提取 | 最小版 vs 完整版对照实测 |
| E-12 | FIX-3b 收紧在既有语料上新增违规 **0 条** | 漂移扫描（201 条无一新增） |
| E-13 | X11 裸名 / 反引号同语义 DDL 结果一致 | 对照实测 |
| E-14 | ADJ-5 变更耦合成立：只修 `_UNIQUE_RE` 会漏报 | 对照实验；O 独立验证一致 |
| E-15 | X10 冲突声明在 `sk` ∉ 主键时 R054 会触发，非静默放行 | 两变体对照实测 |
| E-16 | 规则文案无精确字符串断言，改文案安全 | `grep` 全测试目录核查 |
| E-17 | 全量回归收集总数 1313，A/O 一致；差异为环境口令配置 | A 实测 1284 passed + 29 skipped；O 实测 1313 passed |
| E-19 | 目标环境版本基线已补录 | TDSQL 赤兔管理台「系统管理 → 版本管理」页面截图（§1.1.1） |
| E-18 | **§5.2–§5.5 的 before/after 代码块逐字实现后行为正确** | 按文档逐字实现的独立原型（非等价改写）：矩阵 **29/29 通过**，与已验证原型**差异 0 条**，漂移扫描输入 201 / 解析成功 201 / 异常 0 / 变化 5 条且恰为误报现场 |

---

## 附录 B：验收脚本落库要求

> **Rev.D 更正**：Rev.C 曾把验证脚本以文档片段形式给出，经评审指出 **B.2 缺用例与断言、B.3 静默吞异常、B.4 缺驱动代码无法产生结果**。A 认可该批评（含对自己"逐字跑通"陈述的更正，见 §13.1 第 6 条）。**Rev.D 不再以文档片段代替门禁**，改为落库要求：

| 文件 | 内容 | 门槛 |
|---|---|---|
| `tests/test_r077_r054_tdsql_syntax.py` | §9.1 的 29 条矩阵 + X9/X10/X11/X13，普通 pytest，参数化 + 显式断言 | 进入默认 `pytest tests/` 门禁；29/29 通过 |
| `tests/qa/verify_r077_r054_drift.py` | 全语料漂移扫描 | **必须输出输入总数 / 解析成功数 / 异常数**；任一异常即失败退出，不得静默 `continue`；变化数 ≠ 5 即失败 |

漂移扫描的现场物料（生产 14 表 DDL）需随测试落库为固定 fixture，不得依赖运行期从 HTML 报告解析。

**X13（承重性断言测试）要点**：以子类方式构造"只修 `_UNIQUE_RE`、不动 R077 判定"的变体，断言其在反引号 UNIQUE + 分片键 ∉ 主键的场景下**会漏报**。该测试的作用是：将来若有人（或依赖升级）激活了 R077 的宽松分支，测试立即失败。
