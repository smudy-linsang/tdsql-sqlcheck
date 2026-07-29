# TDSQL-SQLCheck v1.5.1 实例类型判定重构设计说明书

| 项 | 内容 |
|---|---|
| 版本 | v1.5.1.0 |
| 基线 | v1.5.0.0（commit `96772d3`） |
| 文档类型 | 缺陷修复设计说明书（含概要 / 数据库 / 接口 / 详细四部分） |
| 编制 | 智能体 A（质量/架构） |
| 缺陷定级 | **P0 — v1.5 核心目标 G1 完全未达成** |
| 责任归属 | **v1.5 设计缺陷（本人）**，非实现偏差 |
| 前置文档 | `ARCHITECTURE-v1.5-*.md` · `DB-v1.5-*.md` · `API-v1.5-*.md` · `DETAIL-v1.5-*.md` |
| 实测依据 | `TEST-v1.5.1-Proxy层实例类型判据实测方案-G.md`（方案） · `REPORT-v1.5.1-Proxy层实例类型判据实测结果-G.md`（结果） |
| 修订 | **2026-07-29 据 G 实测结果修订**：Proxy 层判据成立，SQL 探测由「无判据」升为**主判定源**；判据裁定见 §8 |

> **本文档为什么不拆成四份**：v1.5 是新建能力，四份文档各自成体量；v1.5.1 是对其中**一个判定环节**的定点重构，代码面 6 个文件、数据库 4 列、接口 2 处。拆四份会让读者在文档间反复跳转去拼一条本来很短的链路。因此合为一份，内部仍按概要（§1–§4）/ 数据库（§5）/ 接口（§6）/ 详细（§7–§9）分节，详细设计部分保持照图施工粒度。

---

## 1. 缺陷描述

### 1.1 现象

开发环境实例管理中有一台 **`SIT-集中式实例A`**（`119.45.220.89:15002`），使用者百分百确认其为集中式实例，且已在实例配置中如实勾选「集中式实例」。

系统「探测类型」给出的结论是 **分布式**，并弹出冲突提示：

> 探测结论为「分布式」，与实例配置中声明的「集中式」不一致。审核将按探测结果（分布式）执行。

### 1.2 真实影响（比表面现象严重）

由于 v1.5 的解析策略是**探测优先于人工声明**（`instance_type_service.py:137-144`），使用者正确勾选的「集中式」**每次都被覆盖为「分布式」**。

因此：

> **该实例的审核仍按 119 条全量规则执行，R077 等 27 条仅分布式适用的规则照常误报。v1.5 立项要解决的原始缺陷，对这台实例一点没修好。**

对照 `ARCHITECTURE-v1.5` §3.1 的功能目标：

| 目标 | 状态 |
|---|---|
| **G1** 集中式实例的任何扫描不得出现仅分布式适用的规则告警 | ❌ **完全未达成** |
| G2 分布式实例零回归 | ✅ 达成（因为一切都被判成分布式） |
| **G3** 实例类型由系统自动探测得出 | ❌ **未达成**（探测无鉴别力，见 §2） |

**G1 是 v1.5 的立项理由。G1 未达成 = v1.5 对存在集中式实例的环境整体无效。**

---

## 2. 实测证据链

以下数据由使用者在真实环境采集，是本次设计的**唯一事实依据**。

### 2.1 证据一：`/*proxy*/` 只是一个 SQL 注释

在后端 TXSQL 节点（`/data/tdsql_run/4002`，通过本地 socket 直连）执行：

```
txsql> /*proxy*/show status;
...
458 rows in set (0.02 sec)
```

返回的是**完整的 458 行标准 MySQL 状态变量**（`Aborted_clients`、`Innodb_buffer_pool_*`、`Threads_*`、`Ssl_*` …）。

**判读**：`/*proxy*/` 被 MySQL 当作注释直接吃掉，实际执行的就是 `SHOW STATUS`。

> **任何 MySQL 兼容端点执行这条语句都会返回非空结果集**——是不是 Proxy、是不是分布式，一概如此。

而 v1.5 的探针 1 判定逻辑是：

```python
rows = self._execute("/*proxy*/show status")
if rows:
    votes_distributed = True     # ← 只要非空就投"分布式"
```

**结论：探针 1 恒投「分布式」。**

（经 Proxy 端口时该 hint 被 Proxy 拦截，返回 2 行 Proxy 自身状态——同样非空，同样投分布式。两条路径殊途同归。）

### 2.2 证据二：探针 2 依赖的视图在本版本根本不存在

```
txsql> SELECT COUNT(*) AS tbl_exists FROM information_schema.TABLES
    ->  WHERE TABLE_SCHEMA='information_schema' AND TABLE_NAME='TDSQL_SHARDING_RULES';
+------------+
| tbl_exists |
+------------+
|          0 |
+------------+

txsql> SELECT COUNT(*) AS rule_rows FROM information_schema.TDSQL_SHARDING_RULES;
ERROR 1109 (42S02): Unknown table 'TDSQL_SHARDING_RULES' in information_schema
```

版本：`8.0.33-v24-txsql-22.4.1-20230926`。

**结论：探针 2 从未投过票。** v1.5 设计中"仅分布式实例存在该视图"的说法，在本版本上不成立。

### 2.3 证据三（最关键）：后端数据节点上两类实例完全同构

使用者选取的两个端口构成一组**同物理机对照实验**（宿主 `VM-0-8-centos` = `10.206.0.8`）：

| 端口 | 身份 | 依据 |
|---|---|---|
| **4002** | **集中式实例**的备机 | 赤兔「集中式实例」详情：DB节点【主】`10.206.0.4:4002`；【备】`10.206.0.8:4002` |
| **4003** | **分布式实例**分片 `set_1782132369_1` 的主机 | 赤兔 DB监控：`set_1782132369_1 (0-7)` 【主】`10.206.0.8:4003` |

两个端口的三条查询**输出逐字一致**：`/*proxy*/show status` 均 458 行；`TDSQL_SHARDING_RULES` 均不存在。

> **架构结论：分布式与否根本不是后端 TXSQL 数据节点的属性。分片拓扑只存在于 Proxy 路由层与管控面（ZK / 赤兔）。任何试图在数据节点 SQL 层区分二者的探针，原理上都不可能成立。**

### 2.4 合并判定：探测是一个常量函数

| 探针 | 实际行为 | 投票 |
|---|---|---|
| P1 `/*proxy*/show status` | 任何可连端点均返回非空 | **恒投 distributed** |
| P2 `TDSQL_SHARDING_RULES` | 视图不存在，恒 `exists=False` | 从不投票 |

```python
return ("distributed" if votes_distributed else "centralized"), detail
```

**`votes_distributed` 恒为 `True` ⟹ `probe_instance_type()` 对任何能连上的实例恒返回 `"distributed"`。**

唯一例外具有讽刺意味：两个探针**同时抛异常**（即实例连不上）时 `conclusive=False`，返回 `None`，才会回落人工声明。

> **也就是说：只有连不上的实例，才会尊重使用者填的实例类型。**

### 2.5 对智能体 G 归因的评价

G 的第 1、2 步（端口对应 Proxy 网关；Proxy 响应了探针，`rows: 2`）**观察正确、证据扎实**。

第 3 步结论"15002 具有 Proxy 代理网关响应能力，**符合分布式 Proxy 特征**"存在两个问题：

1. **原样继承了 v1.5 设计中的错误前提**。赤兔「集中式实例」详情页明载 `网关列表(proxy_host)：【可用】10.206.0.4:15002 【可用】10.206.0.8:15002`——集中式实例同样架设在 Proxy 之后，"有 Proxy"不是分布式的特征。
2. **归因范围过窄**。只定位到探针 1，未发现探针 2 从未生效，也未发现整个探测是**恒定输出**而非"偶发误判"。二者的严重性相差一个数量级。

---

## 3. 设计复盘：v1.5 为什么会写错

不做归因就会再犯，因此必须写清楚。

| # | 失误 | 具体表现 |
|---|---|---|
| **F1** | **凭架构假设写判据，未经真实环境验证** | 设计时假设"集中式实例 = 直连 MySQL，无 Proxy"。该假设对通用 MySQL 中间件成立，对 **TDSQL 不成立**——TDSQL 两类实例统一由 Proxy 接入 |
| **F2** | **把"能力探测"误当"类型探测"** | `/*proxy*/show status` 与 `TDSQL_SHARDING_RULES` 测的都是"我是否接在 TDSQL Proxy 上 / 是否有分片视图"，与"我是不是分布式实例"是两个问题 |
| **F3** | **未验证 hint 语义** | 未意识到 `/*proxy*/` 在非 Proxy 端点上就是注释，导致探针**永不失败**——而设计的整个失败分支逻辑（`conclusive`）建立在"集中式会报语法错误"之上 |
| **F4** | **测试锁定的是错误的规格** | Q 的 `test_probe_distributed_when_proxy_ok` 逐字实现了错误假设（proxy ok → distributed），因此全绿的测试**不可能**发现该缺陷。测试只验证了"实现符合设计"，未验证"设计符合现实" |
| **F5** | **冲突策略放大了后果** | "探测一律优先"使一个错误的探测得以覆盖正确的人工声明。若当初采用"取更保守者"，本缺陷的后果会被限制在"多报"而非"目标完全未达成" |
| **F6** | **忽视了已有的权威数据源** | `deploy/tdsql_inventory.sh:350-353` 早已写明 ZK 的 noshard/groupshard 结构，v1.5 设计时未检索既有资产就自行发明判据 |

### 流程改进（写入团队约定）

> **凡涉及"从外部系统探测事实"的判据，设计阶段必须附上真实环境的实测输出作为依据；未经实测的判据不得进入详细设计。**
>
> **测试必须包含至少一条"反向鉴别用例"**：对两类目标各跑一次，断言结论**不同**。仅断言"某类返回某值"无法发现常量函数。

本次设计严格遵循该约定：§2 的全部判据均有实测输出支撑；尚未实测的部分（§8）明确标注为**待验证**，且在验证前**不产出任何结论**。

---

## 4. 修复方案总体设计

### 4.1 判定源分层

放弃"连库猜类型"的单一路线，改为**多源分级，权威优先**：

```
┌───────────────────────────────────────────────────────────────────┐
│ S1 Proxy 层 SQL 探测 —— 实时源                        置信度：确定 │
│    /*proxy*/show status 的拓扑签名（PR001，见 §8.5）：              │
│      cluster 行 / :hash_range 行 / 多 SET  → 分布式                 │
│      单 SET 且无上述特征                   → 集中式                 │
│    EXPLAIN 的 info 列（PR002，仅阳性）     → 分布式                 │
│    直接读取【正在被审核的那个连接端点】的实时事实，无外部依赖        │
└───────────────────────────────┬───────────────────────────────────┘
                                │ 无结论时下沉
┌───────────────────────────────┴───────────────────────────────────┐
│ S2 管控面（ZooKeeper）—— 权威源                        置信度：确定 │
│    /tdsqlzk/sets/set@xxx          → noshard    → 集中式            │
│    /tdsqlzk/group_xxx/sets/set@y  → groupshard → 分布式            │
│    落库缓存，不在扫描链路上实时访问 ZK；实例不可达时仍可用          │
└───────────────────────────────┬───────────────────────────────────┘
                                │ 无 ZK 数据时下沉
┌───────────────────────────────┴───────────────────────────────────┐
│ S3 人工声明 tdsql_connections.is_distributed          置信度：中     │
└───────────────────────────────┬───────────────────────────────────┘
                                │ A 类通道必有值，不会下沉；B 类通道用
┌───────────────────────────────┴───────────────────────────────────┐
│ S4 全局默认 system_config.default_instance_type       置信度：低     │
└───────────────────────────────────────────────────────────────────┘

               ┌─────────────────────────────────────┐
               │ S0 管理员锁定（最高优先级，覆盖一切）  │
               │    instance_type_locked = 1          │
               └─────────────────────────────────────┘
```

#### 为什么 SQL 探测排在 ZK 之前（2026-07-29 实测后调整）

本设计初稿把 ZK 列为 S1、SQL 探测列为 S2，因为当时**不知道 SQL 层有没有可用判据**。G 的实测（§8）改变了这个前提：

| 对比项 | Proxy 探测（PR001） | ZK 管控面 |
|---|---|---|
| 读到的事实 | `cluster` = `group_1782132247_10` | `groupshard` / `group_1782132247_10` |
| 权威性 | **同一个事实**，只是来源通道不同 | 同左 |
| 时效 | **实时**，反映当下连接的那个端点 | 上次 `zk_synced_at` 的快照 |
| 依赖 | **无**（用已有连接） | zkCli + ZK 网络可达 + inventory 脚本 |
| 实例不可达时 | 不可用 | **仍可用** |

两者读的是**同一个底层事实**（赤兔里的 group/set 标识），权威性相同。因此排序应按**可用性与时效**：

> **探测只需要一条已有的连接；ZK 需要一整套外部依赖。把一个有硬性部署依赖的源放在首位，而旁边就有一个零依赖、等权威、更实时的源，是不合理的。**

ZK 保留在 S2 有三个不可替代的作用：**① 探测失败时兜底；② 与探测互为交叉校验，不一致即暴露异常；③ 实例连不上时依然能给出类型。**

### 4.2 三项核心变更

| # | 变更 | 解决什么 |
|---|---|---|
| **C1** | **判据换成拓扑签名**：不再看"有没有返回"，改看返回内容里的 `cluster` / `hash_range` / SET 个数（PR001）。无有效判据时返回 `None`（无结论）而非 `distributed` | **止血 + 根治**。探测第一次真正具备鉴别力 |
| **C2** | **接入 ZK 权威源**：`tdsql_inventory.sh` 输出 `instance_kind` / `instance_id` / `proxy_list`，贯通至 `tdsql_connections` | 交叉校验 + 探测失败/实例不可达时的兜底 |
| **C3** | **冲突策略改为"取更保守者"**：任一来源判定为分布式即按分布式执行；并新增管理员锁定作为终审 | 缩小任何单点误判的影响面；给确定性场景一个可审计的终审通道 |

### 4.3 C3 详解：为什么"取更保守者"严格优于"探测优先"

两个方向的误判后果**严重不对称**（`ARCHITECTURE-v1.5` §2.2 已确立）：

| 误判方向 | 后果 | 可见性 |
|---|---|---|
| 分布式 → 判成集中式 | 27 条规则被跳过 | **不可见**（报告里少的东西没人会发现）→ 致命 |
| 集中式 → 判成分布式 | 多出误报 | 一眼可见 → 可忍 |

因此策略矩阵：

| 来源 A | 来源 B | v1.5 旧策略 | **v1.5.1 新策略** | 说明 |
|---|---|---|---|---|
| 探测=distributed | 声明=centralized | distributed | **distributed** | 保守取值，与旧策略同 |
| 探测=centralized | 声明=distributed | centralized ⚠ | **distributed** | 旧策略在此**静默漏报**，新策略修正 |
| ZK=centralized | 声明=distributed | — | **distributed** + 前端提示核实 | ZK 虽权威，但方向危险，仍取保守值并要求人工确认 |
| ZK=distributed | 声明=centralized | — | **distributed** | 一致于保守原则 |
| 锁定=centralized | 任意 | — | **centralized** | 管理员终审，落审计日志 |

> **关键设计取舍**：ZK 是权威源，但当它说"集中式"而声明说"分布式"时，**仍取分布式**。理由是本次事故的教训——**任何自动判定源都可能因环境差异而失灵**，而"判成集中式"是不可见的失效方向。要让集中式真正生效，走 S0 管理员锁定这条**显式、可审计**的路径，而不是依赖某个自动源单方面判定。
>
> 这条规则的代价是：ZK 正确识别出集中式、而使用者恰好把声明勾错成分布式时，仍会多报。此时前端会给出"ZK 判定为集中式，与声明不符，请核实"的提示，使用者改声明或加锁定即可。**代价是可见的、可自助解决的；反向的代价是不可见的。**

### 4.4 与 v1.5 既有不变式的关系

| v1.5 不变式 | v1.5.1 状态 |
|---|---|
| INV-1 适用域过滤只在 `get_enabled_rules()` 一处发生 | ✅ 不变 |
| INV-2 适用域只做减法，请求参数不得绕过 | ✅ 不变。**管理员锁定不是绕过**——它是实例级、持久化、可审计的配置，不是每次调用传参 |
| INV-3 传入引擎的 `instance_type` 必定是确定值 | ✅ 不变。多源分级后仍保证有确定输出 |
| INV-4 分布式实例零回归 | ✅ 不变 |
| INV-5 探测异常不得中断审核主流程 | ✅ 强化。ZK 查询走落库缓存，扫描链路零网络开销 |

---

## 5. 数据库设计

### 5.1 变更总览

| 表 | 变更 | 列数 |
|---|---|---|
| `tdsql_connections` | 新增列 | 5 |
| 迁移文件 | `backend/schema/v5/050_instance_type_authority.sql`（新建） | — |

**纯增量，无删除 / 无改名 / 无类型变更。** v1.5 已加的 3 列（`detected_instance_type` / `instance_type_detected_at` / `instance_type_probe_error`）**全部保留**，语义不变（它们记录的是 S2 SQL 探测的结果）。

### 5.2 新增列

```sql
ALTER TABLE tdsql_connections
    ADD COLUMN zk_instance_kind VARCHAR(16) NULL DEFAULT NULL
        COMMENT 'ZK 管控面实例形态：noshard=集中式 / groupshard=分布式；NULL=未同步';

ALTER TABLE tdsql_connections
    ADD COLUMN zk_instance_id VARCHAR(64) NOT NULL DEFAULT ''
        COMMENT 'ZK 实例标识：set_xxx(集中式) / group_xxx(分布式)，供人工核对';

ALTER TABLE tdsql_connections
    ADD COLUMN zk_synced_at DATETIME NULL DEFAULT NULL
        COMMENT '最近一次从 ZK 同步实例形态的时间；NULL=从未同步';

ALTER TABLE tdsql_connections
    ADD COLUMN instance_type_locked TINYINT NOT NULL DEFAULT 0
        COMMENT '管理员锁定实例类型：1=锁定，优先级高于一切自动判定源';

ALTER TABLE tdsql_connections
    ADD COLUMN instance_type_locked_value VARCHAR(16) NOT NULL DEFAULT ''
        COMMENT '锁定值 distributed|centralized；instance_type_locked=1 时生效';
```

### 5.3 列语义

| 列 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `zk_instance_kind` | VARCHAR(16) | ✅ | `noshard` / `groupshard` / NULL。**S1 权威源的落地字段** |
| `zk_instance_id` | VARCHAR(64) | ❌ | `set_1782130875_4` / `group_1782132247_10`。仅供人工核对与排障，不参与判定 |
| `zk_synced_at` | DATETIME | ✅ | 判断 ZK 数据是否陈旧；前端显示"ZK 同步于 X" |
| `instance_type_locked` | TINYINT | ❌ | 0/1。**S0 终审开关** |
| `instance_type_locked_value` | VARCHAR(16) | ❌ | 锁定的具体类型 |

### 5.4 关键设计说明

**（1）为什么存 `kind` 原文而不是直接存 `distributed`/`centralized`**

`noshard` / `groupshard` 是 TDSQL 管控面的**原始事实**；`distributed` / `centralized` 是本系统的**业务语义**。原样保存原始事实，映射在代码里做（`groupshard → distributed`，`noshard → centralized`）。

好处：排障时能直接与赤兔、ZK 对账；将来 TDSQL 若引入第三种形态，数据层不用改，只改映射。

**（2）为什么锁定要拆成两列而不是一列三态**

`instance_type_locked_value` 单列（空串=未锁定）看似更省，但会丢失一个信息：**管理员曾经锁定过、后来解锁了**。拆两列后，解锁只需 `locked=0`，`locked_value` 保留历史选择，重新加锁时可以回显上次的值。运维体验上这很重要——锁定通常是应对某个反复出问题的实例。

**（3）不加索引**

`tdsql_connections` 是低基数配置表（数十~数百行），全部访问路径为主键查单行或全表列表。新增列均为展示与判定用，不进 WHERE。

**（4）存量数据不回填**

升级后所有实例 `zk_instance_kind = NULL`（未同步），判定自动下沉至 S2/S3。首次运行「ZK 自动发现」或点击「探测类型」后填充。**不做任何 UPDATE 回填**——回填就意味着凭猜测写入权威字段，是本次事故的同类错误。

### 5.5 迁移脚本

```sql
-- ============================================================================
-- V1.5.1 实例类型判定重构：接入 ZK 管控面权威源 + 管理员锁定
-- 全部为新增列，无删除/重命名/类型变更；回滚只需停止读取新列
-- 设计依据：docs/DESIGN-v1.5.1-实例类型判定重构.md §5
-- ============================================================================

-- ── E-1 ZK 管控面判定结果（S1 权威源）──
-- 存 TDSQL 原始形态而非业务语义：便于与赤兔/ZK 对账，且形态扩展时数据层不用改。
ALTER TABLE tdsql_connections
    ADD COLUMN zk_instance_kind VARCHAR(16) NULL DEFAULT NULL
        COMMENT 'ZK 实例形态 noshard=集中式/groupshard=分布式；NULL=未同步';

ALTER TABLE tdsql_connections
    ADD COLUMN zk_instance_id VARCHAR(64) NOT NULL DEFAULT ''
        COMMENT 'ZK 实例标识 set_xxx/group_xxx，供人工核对';

ALTER TABLE tdsql_connections
    ADD COLUMN zk_synced_at DATETIME NULL DEFAULT NULL
        COMMENT '最近一次 ZK 同步时间；NULL=从未同步';

-- ── E-2 管理员锁定（S0 终审）──
-- 拆两列而非单列三态：解锁后保留上次锁定值，重新加锁可回显。
ALTER TABLE tdsql_connections
    ADD COLUMN instance_type_locked TINYINT NOT NULL DEFAULT 0
        COMMENT '1=管理员锁定实例类型，优先级高于一切自动判定源';

ALTER TABLE tdsql_connections
    ADD COLUMN instance_type_locked_value VARCHAR(16) NOT NULL DEFAULT ''
        COMMENT '锁定值 distributed|centralized';

-- ── 存量数据迁移：不需要，且明令禁止 ──
-- zk_instance_kind 保持 NULL，判定自动下沉至声明值。
-- 任何回填都是凭猜测写入权威字段，与本次事故同源。
```

> 施工时同步在 `backend/services/database.py::_ensure_columns()` 中用 `_add_column_if_not_exists()` 补一份等价声明（本项目 v1.2/v1.3 已有的双保险惯例，见 `database.py:502-510`）。

---

## 6. 接口设计

### 6.1 变更总览

| 接口 | 变更 |
|---|---|
| `POST /api/v1/tdsql/connections/{id}/probe-instance-type` | 🔀 语义扩展：返回多源判定明细 |
| `PUT /api/v1/tdsql/connections/{id}/instance-type-lock` | 🆕 新增：管理员锁定/解锁 |
| `POST /api/v1/tdsql/discover` | 📤 响应扩展：新增 `instance_kind` / `instance_id` |
| `GET /api/v1/tdsql/connections` · `/{id}` | 📤 响应扩展：新增 ZK 与锁定字段 |
| 其余全部接口 | ⚪ 无变更 |

### 6.2 探测接口（语义扩展）

```
POST /api/v1/tdsql/connections/{connection_id}/probe-instance-type
```

**权限**：`admin` / `dba`（沿用，路径前缀已在 `_PATH_TO_MENU` 登记为 `instances`）

**行为变更**：不再只跑 SQL 探针，而是**依次尝试全部判定源**并返回明细。

**响应 200（本次缺陷实例的预期返回）**

```json
{
  "connection_id": "5ea70d74",
  "effective_instance_type": "centralized",
  "instance_type_source": "declared",
  "conflict": false,
  "sources": {
    "locked":   {"available": false, "value": null},
    "zk":       {"available": false, "value": null, "kind": null,
                 "reason": "尚未执行 ZK 自动发现，或该实例未在 ZK 清单中匹配到"},
    "probe":    {"available": false, "value": null,
                 "reason": "当前版本无可用的 SQL 层判据，本源不产出结论（见设计文档 §8）",
                 "detail": {}},
    "declared": {"available": true,  "value": "centralized"}
  },
  "message": "本次采用实例配置中声明的「集中式」。SQL 层探测在 TDSQL 上无法区分实例类型，已停用；如需权威判定，请执行「ZK 自动发现」同步管控面数据，或由管理员锁定实例类型。"
}
```

**字段说明**

| 字段 | 说明 |
|---|---|
| `effective_instance_type` | 按 §4.1 分级 + §4.3 保守策略得出的最终值 |
| `instance_type_source` | `locked` / `zk` / `probe` / `declared` / `default` |
| `conflict` | 是否存在两个可用源给出不同结论 |
| `sources` | **每个源各自的结论与不可用原因**。排障的关键——本次事故中，若当初有这个结构，"探针恒真"会立刻暴露 |
| `message` | 面向使用者的自然语言结论 + 下一步建议 |

> **`sources` 明细是本次事故直接催生的设计。** v1.5 的接口只回一个 `probe_detail`，无法看出"哪个源投了票、哪个源根本没参与"。多源分级后，每个源的可用性必须逐一可见。

### 6.3 管理员锁定（新增）

```
PUT /api/v1/tdsql/connections/{connection_id}/instance-type-lock
```

**权限**：**仅 `admin`**（终审能力，可覆盖权威源）

**请求体**

```json
{ "locked": true, "instance_type": "centralized", "reason": "赤兔确认为非分布式实例 set_1782130875_4" }
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `locked` | ✅ | `true` 加锁 / `false` 解锁 |
| `instance_type` | `locked=true` 时必填 | `distributed` / `centralized` |
| `reason` | `locked=true` 且锁定值为 `centralized` 时**必填** | 落审计日志 |

> **为什么只有锁 `centralized` 才强制填写理由**：锁成 `distributed` 是保守方向（多跑规则），错了也只是多报；锁成 `centralized` 会**关掉 27 条规则**，是唯一能造成静默漏报的操作，必须留下人为决策记录。

**响应 200**

```json
{
  "success": true,
  "connection_id": "5ea70d74",
  "locked": true,
  "instance_type": "centralized",
  "message": "已锁定实例类型为「集中式」。该实例后续审核将跳过 27 条仅分布式适用的规则。配置最长 5 分钟后在全部服务进程生效。"
}
```

**响应 400**

- `instance_type` 取值非法
- `locked=true` 且锁定值为 `centralized` 但未填 `reason`

**响应 403**：非 `admin`

**审计**：加锁 / 解锁均写 `operation_logs`，`operation_type = 'instance_type_lock'`，内容含操作人、目标实例、锁定值、理由。

### 6.4 ZK 自动发现（响应扩展）

```
POST /api/v1/tdsql/discover
```

**请求不变。响应每项新增：**

```json
{
  "service_name": "集中式实例",
  "host": "10.206.0.4",
  "port": 15002,
  "user": "tdsqlsys_normal",
  "password": "***",
  "database": "ALL",
  "status_code": "0",
  "status_text": "运营中",

  "instance_kind": "noshard",
  "instance_id": "set_1782130875_4",
  "instance_type": "centralized",
  "proxy_list": "10.206.0.4:15002,10.206.0.8:15002"
}
```

`proxy_list` 是**匹配已注册实例的关键**——见 §7.2 的设计说明。

### 6.5 实例列表 / 详情（响应扩展）

每个实例对象新增：

```json
{
  "zk_instance_kind": "noshard",
  "zk_instance_id": "set_1782130875_4",
  "zk_instance_type": "centralized",
  "zk_synced_at": "2026-07-29T11:03:22",
  "instance_type_locked": true,
  "instance_type_locked_value": "centralized",
  "effective_instance_type": "centralized",
  "instance_type_source": "locked"
}
```

v1.5 已有的 `declared_instance_type` / `detected_instance_type` / `instance_type_conflict` 等字段**保留不变**。

### 6.6 探测诊断采集（新增，C 组）

```
POST /api/v1/tdsql/connections/{connection_id}/probe-diagnostics
```

**用途**：用**系统自身的连接**在目标实例上执行 §8.3 的采集清单，原样返回输出，供 G 做 Proxy 层判据实测。

**权限**：`admin` / `dba`

**请求体**

```json
{ "sample_table": "tdsql_check.t_order" }
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `sample_table` | ❌ | 可选的样本表（建议给一张真实业务表）。用于取 `SHOW CREATE TABLE`，观察分布式实例的 DDL 是否带 `shardkey=` |

**`sample_table` 安全校验（必做）**：仅允许 `^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)?$`，不匹配直接 400。该值会进入 `SHOW CREATE TABLE` 语句，**不能有任何拼接注入面**。

**响应 200**

```json
{
  "connection_id": "5ea70d74",
  "instance_label": "SIT-集中式实例A",
  "endpoint": "119.45.220.89:15002",
  "declared_instance_type": "centralized",
  "zk_instance_kind": "noshard",
  "collected_at": "2026-07-29T12:05:41",
  "diagnostics": {
    "statements": {
      "proxy_show_status":    {"ok": true, "sql": "/*proxy*/show status", "row_count": 2, "rows": [ ... ], "truncated": false},
      "proxy_connectionpool": {"ok": false, "sql": "/*proxy*/show connectionpool", "reason": "..."},
      "proxy_show_shard":     {"ok": false, "sql": "/*proxy*/show shard", "reason": "..."},
      "proxy_show_sets":      {"ok": false, "sql": "/*proxy*/show sets", "reason": "..."},
      "show_databases":       {"ok": true, "sql": "show databases", "row_count": 7, "rows": [ ... ], "truncated": false}
    },
    "sample_table_ddl": {"ok": true, "table": "tdsql_check.t_order", "rows": [ ... ]}
  }
}
```

**响应中必须回带 `endpoint` 与 `declared_instance_type` / `zk_instance_kind`**：实测比对的前提是知道"这份输出来自哪台、它到底是什么类型"。缺了这两项，采回来的数据无法配对分析。

**前端（C.4）**：实例操作列新增「采集探测诊断」，结果以 JSON 展示并提供**下载按钮**——G 需要把两类实例的输出并排比对，能存成文件最省事。

---

## 7. 详细设计（照图施工）

### 7.0 改造清单（按依赖顺序）

> **交付方式（负责人 2026-07-29 决定）：P0 / P1 / P2 一次性开发完成，单次交付，不分批上线。**

| 组 | # | 文件 | 动作 |
|---|---|---|---|
| **A 止血** | A.1 | `backend/services/tdsql_connector.py` | 重写 `probe_instance_type()`：摘除失效判据，返回无结论 |
| | A.2 | `backend/services/instance_type_service.py` | 冲突策略改保守取值 |
| | A.3 | `tests/test_instance_type_service.py` | 删除锁定错误规格的用例，补反向鉴别用例 |
| **B 权威源** | B.1 | `backend/schema/v5/050_instance_type_authority.sql` | 新建迁移 |
| | B.2 | `backend/services/database.py` | `_ensure_columns` 双保险 |
| | B.3 | `deploy/tdsql_inventory.sh` | 输出 `instance_kind` / `instance_id` / `proxy_list` |
| | B.4 | `backend/services/zk_discovery_service.py` | 解析新列 + 回写实例形态 |
| | B.5 | `backend/services/instance_type_service.py` | 接入 S1 源 + S0 锁定 |
| | B.6 | `backend/api/tdsql_manage.py` | 探测接口改多源；新增锁定接口 |
| | B.7 | `backend/services/auth_service.py` | RBAC 校验（锁定接口 admin-only） |
| | B.8 | `frontend/index.html` · `static/js/app.js` | 类型来源徽标、锁定开关、多源明细 |
| **C 判据表** | C.1 | `backend/services/instance_probe_rules.py` | **新建**：判据表，含 PR001/PR002 已启用 + PR003 可选 |
| | C.2 | `backend/services/tdsql_connector.py` | `collect_probe_diagnostics()` 采集器 |
| | C.3 | `backend/api/tdsql_manage.py` | **新增** `POST /connections/{id}/probe-diagnostics` |
| | C.4 | `frontend/index.html` · `static/js/app.js` | 「采集探测诊断」按钮 + 结果导出 |
| **D 收尾** | D.1 | `backend/config.py` · `frontend/index.html` | 版本号 → `1.5.1.0` |
| | D.2 | `docs/DETAIL-v1.5-*.md` | 勘误：标注 §4.1 探测方案作废，指向本文档 |

#### 关于 C 组：判据已就位，框架仍然要

原计划把 P2 拆成「框架 + 判据」两半，是因为当时实测排在开发之后、Q 开工时没有判据可写。**负责人后续复议改为 G 先实测，该顺序矛盾已消失——判据（PR001/PR002/PR003）现在直接随本次交付上线。**

**但「可插拔判据表」这个结构仍然保留**，理由有三：

1. **判据会变**。TDSQL 版本升级、客户现场差异，都可能让某条判据失效或新增。判据独立成表后，增删只动 `instance_probe_rules.py` **一个文件**，不碰连接器逻辑，也不碰判定流程。
2. **`evidence` 字段是强制约束的载体**。§8.4 要求每条判据必须写明实测出处，`test_every_rule_has_evidence` 会把它钉死。**把"必须有实测依据"变成结构上填不满就过不了的字段，比写在文档里靠人自觉可靠得多**——这正是本次事故的教训。
3. **PR003 需要一个"登记但不启用"的位置**。它有效但昂贵（需先选表、空库无表可查），`enabled=False` 让它登记在案、由诊断接口按需调用，而不是散落在别处。

**C.2 / C.3 诊断采集端点同样保留**（负责人此前问过去留）。本次采集用途虽已由 G 手工完成，但它有两个持续价值：

- **排障**：某台实例探测返回"无结论"时，运维需要看到各判据各自返回了什么。没有它就只能登机器手工敲。
- **把纪律变成代码**：它用系统自己的连接池、账号、驱动去采，天然满足"采集环境 = 判定环境"。**这条约束靠人遵守失败过一次，交给代码保证。**


### 7.0.1 判据表 — `backend/services/instance_probe_rules.py`（新建）

> **v1.5.1 实测后更新**：本表**出厂即带 2 条已验证判据**（PR001 / PR002），另有 1 条可选判据（PR003）默认不启用。判据依据见 §8.5，全部经真实环境实测并与赤兔管理台交叉核验。

```python
"""SQL 层实例类型判据表（V1.5.1）

判据全部来自 2026-07-29 的真实环境实测（TDSQL 8.0.33-v24-txsql-22.4.1），
原始数据见 docs/REPORT-v1.5.1-Proxy层实例类型判据实测结果-G.md，
采纳裁定见 docs/DESIGN-v1.5.1-实例类型判定重构.md §8。

【铁律】判据只做阳性判定，禁止把"未命中"当作反向结论。
  V1.5 的教训是"恒判分布式"（误报，可见、可纠正）；
  比它更危险的是"易判集中式"（漏报，不可见、直接放行风险）。
  因此除 PR001 命中【集中式正面签名】外，任何判据未命中一律返回 None。

【新增判据的强制门槛】——三条全部满足才可入表，见设计文档 §8.4：
  1) 两类实例上实测输出确有差异，evidence 必须写明实测出处；
  2) 差异方向明确：命中即为分布式的阳性证据；
  3) 未命中时不得判集中式，必须返回 None 下沉至下一源。
外加：每条判据必须配套一条反向鉴别用例（两类实例各跑一次、断言结论不同）。
"""
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

# 结果行取字段名的兼容顺序（沿用 discover_sets() 的多版本兼容口径）
_NAME_KEYS = ("status_name", "Variable_name", "Config_name", "name", "Key")
_VALUE_KEYS = ("Value", "value")


def _row_name(row) -> str:
    if isinstance(row, dict):
        for k in _NAME_KEYS:
            if k in row and row[k] is not None:
                return str(row[k])
    return ""


def _row_value(row) -> str:
    if isinstance(row, dict):
        for k in _VALUE_KEYS:
            if k in row and row[k] is not None:
                return str(row[k])
    return ""


@dataclass(frozen=True)
class ProbeRule:
    """一条 SQL 层判据。

    Attributes:
        rule_id:  判据标识
        sql:      在目标实例上执行的语句
        decide:   (rows) -> "distributed" | "centralized" | None
                  返回 None 表示【本判据无结论】，绝不等同于集中式
        evidence: 实测依据（日期 + 数据出处），入表必填
        enabled:  是否参与自动探测
    """
    rule_id: str
    sql: str
    decide: Callable[[list], Optional[str]]
    evidence: str
    enabled: bool = True


# ── PR001：/*proxy*/show status 拓扑签名（主判据）────────────────────────
def _decide_proxy_status(rows: list) -> Optional[str]:
    """依据 Proxy 返回的拓扑签名判定。判定表见设计文档 §8.5。

    实测（2026-07-29）：
      CENT 2 行 —— set=set_1782130875_4 ；无 cluster / 无 hash_range
      DIST 8 行 —— cluster=group_1782132247_10 ；
                   set_1782132369_1:hash_range=0---7 ；
                   set_1782132389_3:hash_range=8---15 ；
                   set="set_1782132369_1,set_1782132389_3 "  ← 注意末尾空格
    """
    if not rows:
        return None

    set_values = []
    for r in rows:
        name = _row_name(r)
        # 签名 1/2：cluster 行 或 hash_range 行 —— 分布式的结构性阳性证据
        if name == "cluster" or ":hash_range" in name:
            return "distributed"
        if name == "set":
            set_values.append(_row_value(r))

    if not set_values:
        return None                      # 形态不符，不猜

    # 实测中 value 末尾带空格，且可能出现尾随逗号，必须先 strip 再过滤空串
    sets = [s.strip() for s in set_values[0].split(",")]
    sets = [s for s in sets if s]

    if len(sets) >= 2:
        return "distributed"             # 签名 3：多 SET 拓扑
    if len(sets) == 1:
        # 签名 4：单 SET 拓扑 + 无 cluster + 无 hash_range
        # 这是【集中式的正面签名】，不是"没看到分布式特征"，故允许下结论。
        # 已知边界：单分片分布式实例若不输出 cluster 行会落到这里被判集中式。
        # 该形态未实测。缓解措施是 §4.3 的保守合并——只有人工声明也为集中式
        # 时该结论才会真正生效。
        return "centralized"
    return None


# ── PR002：EXPLAIN 路由信息注入（辅助，仅阳性）──────────────────────────
def _decide_explain_info(rows: list) -> Optional[str]:
    """Proxy 为分布式实例的 EXPLAIN 注入 info 列（含路由到的 SET）。

    实测（2026-07-29）：
      CENT: 标准 12 列，无 info
      DIST: info = "set_1782132369_1,EXPLAIN SELECT 1"

    【只判阳性】无 info 什么也证明不了 —— EXPLAIN SELECT 1 不涉及任何表，
    Proxy 是否注入路由信息带有偶然性，不同版本/配置未必一致。
    """
    if rows and isinstance(rows[0], dict) and "info" in rows[0]:
        return "distributed"
    return None


# ── PR003：表 DDL 分片标记（可选兜底，仅阳性，默认不启用）───────────────
_RE_SHARDKEY = re.compile(r"\bshardkey\s*=", re.IGNORECASE)


def _decide_table_ddl(rows: list) -> Optional[str]:
    """分布式实例的表 DDL 尾部带 shardkey= 或广播表标记。

    实测（2026-07-29）：
      CENT: ... COLLATE=utf8mb4_bin COMMENT='...'      （无 shardkey）
      DIST: ... COLLATE=utf8mb4_bin shardkey=id

    默认不启用：需先选定一张表，比 PR001/PR002 昂贵，且空库无表可查。
    仅由诊断接口在 PR001/PR002 均无结论时按需调用。
    """
    for r in rows or []:
        text = " ".join(str(v) for v in r.values()) if isinstance(r, dict) else str(r)
        if _RE_SHARDKEY.search(text) or "broadcast" in text.lower():
            return "distributed"
    return None


ACTIVE_PROBE_RULES: list[ProbeRule] = [
    ProbeRule(
        rule_id="PR001",
        sql="/*proxy*/show status",
        decide=_decide_proxy_status,
        evidence=("2026-07-29 实测，REPORT-v1.5.1 §1 判据1；"
                  "与赤兔管理台 group/set 标识及 hash 区间逐字核验一致"),
    ),
    ProbeRule(
        rule_id="PR002",
        sql="EXPLAIN SELECT 1",
        decide=_decide_explain_info,
        evidence="2026-07-29 实测，REPORT-v1.5.1 §1 判据2（仅阳性方向）",
    ),
    ProbeRule(
        rule_id="PR003",
        sql="",                     # 由调用方拼入具体表名，见 §7.1
        decide=_decide_table_ddl,
        evidence="2026-07-29 实测，REPORT-v1.5.1 §1 判据3（仅阳性方向）",
        enabled=False,              # 默认不参与自动探测
    ),
]
```

> **`sets[0]` 的取法说明**：实测中 `set` 行只出现一次。若某版本出现多行 `set`，取第一行即可——因为多 SET 的情况已被签名 1/2（`cluster` / `hash_range`）先行拦截，走不到这里。

---

### 7.1 A.1 / C.2：重写 `probe_instance_type()`

**文件**：`backend/services/tdsql_connector.py:520`

**全量替换**（docstring 中的事故记录**必须原样保留**——它的作用是阻止后来者把这个函数"改回"原来的写法）：

```python
    def probe_instance_type(self) -> tuple:
        """SQL 层实例类型探测（V1.5.1 重写）。返回 (类型 或 None, 探针明细)。

        ── 事故记录：V1.5 的两个判据为什么全错 ──────────────────────────
        V1.5 用了两个判据，经真实环境实测（8.0.33-v24-txsql-22.4.1）全部证伪：

          1) /*proxy*/show status 返回非空
             /*proxy*/ 只是一个 SQL 注释。直连后端 TXSQL 执行该语句会返回
             完整的 458 行标准 MySQL 状态变量 —— 任何 MySQL 兼容端点都返回
             非空结果。原判据"非空即分布式"因此恒为真。
             【正确的用法是看返回内容的拓扑签名，而不是看有没有返回。】

          2) information_schema.TDSQL_SHARDING_RULES 存在
             该视图在本版本 TDSQL 上根本不存在（ERROR 1109），原判据恒为假。

        净效果：旧实现对任何可连实例恒返回 "distributed"，是一个常量函数，
        毫无鉴别力，并因"探测优先于声明"覆盖了使用者正确填写的实例类型，
        导致 V1.5 的核心目标 G1 完全未达成。

        另一条同样重要的结论（同物理机对照实验，10.206.0.8 的 4002/4003）：
        集中式实例的节点与分布式实例的分片节点，在【后端 TXSQL】的 SQL 层
        输出逐字一致。分片拓扑不是数据节点的属性 —— 只有【经 Proxy 端口】
        才能看到差异。因此本方法必须走实例配置的 Proxy 端口，
        任何绕过 Proxy 直连后端节点的探测都不可能成立。

        ── V1.5.1 的做法 ────────────────────────────────────────────────
        遍历 instance_probe_rules.ACTIVE_PROBE_RULES：
          · 任一判据返回 "distributed" → 立即判分布式（阳性证据优先）
          · 无任何阳性命中，但有判据返回 "centralized" → 判集中式
          · 全部无结论 / 全部执行失败 → 返回 None，下沉至 ZK 或人工声明

        【铁律】未命中不等于集中式。只有命中【集中式的正面签名】才可判集中式
        （目前仅 PR001 的"单 SET 拓扑"具备该签名）。把"没看到分布式特征"
        当成"是集中式"，会让一次网络抖动就静默关掉 27 条规则 —— 那是比 V1.5
        的误报危险得多的失效方向。

        Returns:
            (类型字符串 或 None, 明细 dict)
        """
        from backend.services.instance_probe_rules import ACTIVE_PROBE_RULES

        detail, distributed_hit, centralized_hit = {}, None, None

        for rule in ACTIVE_PROBE_RULES:
            if not rule.enabled or not rule.sql:
                continue
            try:
                rows = self._execute(rule.sql)
                verdict = rule.decide(rows or [])
                detail[rule.rule_id] = {"ok": True, "verdict": verdict,
                                        "row_count": len(rows or [])}
                if verdict == "distributed" and distributed_hit is None:
                    distributed_hit = rule.rule_id
                elif verdict == "centralized" and centralized_hit is None:
                    centralized_hit = rule.rule_id
            except Exception as e:
                # 判据执行失败仅记录，不参与判定，绝不抛出（INV-5）
                detail[rule.rule_id] = {"ok": False, "reason": str(e)[:200]}

        if distributed_hit:
            return "distributed", {"matched": distributed_hit, "rules": detail}
        if centralized_hit:
            return "centralized", {"matched": centralized_hit, "rules": detail}
        return None, {"matched": None, "rules": detail}
```

**阳性优先于阴性**：即使某条判据判了集中式，只要另一条判了分布式，仍取分布式。与 §4.3 的保守合并同一原则——**判错成分布式只是多报，判错成集中式是静默漏报。**

**为什么保留方法而不是删除**：`_probe_and_persist()` / `probe_now()` / 测试均在调用它。后续增删判据只需改 `instance_probe_rules.py` **一个文件**，不再动连接器代码。
---

### 7.1.1 C.2：诊断采集器 `collect_probe_diagnostics()`

**文件**：`backend/services/tdsql_connector.py`（紧邻 `probe_instance_type()` 新增）

```python
    # §8.3 采集清单。只读语句，逐条独立执行，单条失败不影响其余。
    _DIAGNOSTIC_STATEMENTS = [
        ("proxy_show_status",   "/*proxy*/show status"),
        ("proxy_connectionpool", "/*proxy*/show connectionpool"),
        ("proxy_show_shard",    "/*proxy*/show shard"),
        ("proxy_show_sets",     "/*proxy*/show sets"),
        ("show_databases",      "show databases"),
    ]

    def collect_probe_diagnostics(self, sample_table: str = "") -> dict:
        """采集实例类型判据的候选证据（V1.5.1 C 组）。

        用于让 G 通过系统自身的连接采集实测数据，而不是登机器手工敲。

        这一点是硬要求，不是便利性考虑：手工从后端 socket 采到的输出，与
        系统经 Proxy 端口能看到的输出【未必相同】—— 本次事故正是栽在这个
        差别上（/*proxy*/show status 直连后端 458 行、经 Proxy 仅 2 行）。
        采集环境必须与判定环境一致，否则实测结论不可迁移到生产判定。

        只执行只读语句；单条失败仅记录，不中断整体采集。
        """
        out = {"statements": {}, "sample_table_ddl": None}
        for key, sql in self._DIAGNOSTIC_STATEMENTS:
            try:
                rows = self._execute(sql)
                out["statements"][key] = {
                    "ok": True, "sql": sql,
                    "row_count": len(rows or []),
                    "rows": (rows or [])[:200],   # 截断防止响应体过大
                    "truncated": len(rows or []) > 200,
                }
            except Exception as e:
                out["statements"][key] = {"ok": False, "sql": sql,
                                          "reason": str(e)[:500]}

        # 表 DDL 是 §8.3 中先验最强的候选判据（分布式实例的表必带 shardkey=）
        if sample_table:
            try:
                rows = self._execute(f"SHOW CREATE TABLE {sample_table}")
                out["sample_table_ddl"] = {"ok": True, "table": sample_table,
                                           "rows": rows}
            except Exception as e:
                out["sample_table_ddl"] = {"ok": False, "table": sample_table,
                                           "reason": str(e)[:500]}
        return out
```

> **`sample_table` 由调用方传入，不在此处自行拼接表名**——避免把用户输入直接拼进 SQL。API 层需对其做标识符白名单校验（见 §6.6）。

---

### 7.2 B.3：`tdsql_inventory.sh` 输出实例形态

**文件**：`deploy/tdsql_inventory.sh`

#### （1）新增命令行开关

在 `WITH_STATUS=0`（第 109 行附近）后新增：

```bash
WITH_TYPE=0                # 是否在 CSV 末尾追加 instance_kind,instance_id,proxy_list 三列
```

在参数解析 `--with-status)` 分支（第 168 行附近）后新增：

```bash
        --with-type)            WITH_TYPE=1; shift ;;
```

在 `usage()` 的选项说明中同步增加一行：

```
      --with-type            CSV 末尾追加 instance_kind,instance_id,proxy_list 三列
```

> **为什么做成开关而非默认输出**：脚本头部声明"与工程使用的 db_config.conf 完全兼容的 CSV 格式"。无条件加列会影响按固定列数解析的既有消费方。开关式扩展 + **新列一律追加在末尾**，可保证按前 6 / 前 8 列取值的消费方完全不受影响。

#### （2）把 `WITH_TYPE` 传入内嵌 Python

内嵌 Python 段（`INVENTORY_FILE = """${_inventory_records}"""` 附近，第 524 行）已通过 shell 变量插值传参，同法新增：

```python
WITH_TYPE = int("""${WITH_TYPE}""")
```

#### （3）构造 CSV 行时带上形态

**定位**：第 793-797 行

**现状**：

```python
        if WITH_STATUS:
            row = [service_name, host, port, user, password, DEFAULT_DATABASE,
                   str(status_code), status_text(status_code)]
        else:
            row = [service_name, host, port, user, password, DEFAULT_DATABASE]
        rows.append(row)
```

**改为**：

```python
        if WITH_STATUS:
            row = [service_name, host, port, user, password, DEFAULT_DATABASE,
                   str(status_code), status_text(status_code)]
        else:
            row = [service_name, host, port, user, password, DEFAULT_DATABASE]

        # V1.5.1：实例形态是审核规则适用域判定的权威依据。
        # kind 与 instance_id 在第 743 行已从 inventory_records 解出，
        # 此前被丢弃在此处 —— 权威事实算出来了却没送出去，正是 V1.5 误判的根因之一。
        # proxy_list 输出该实例【全部】网关，而非上面随机选中的那一个：
        # 系统中登记的实例可能配的是同实例的另一个网关（如 10.206.0.8:15002
        # 而非 10.206.0.4:15002），只按选中项匹配会漏配。
        if WITH_TYPE:
            row += [kind, instance_id, ";".join(sorted(set(proxy_names)))]

        rows.append(row)
```

> **`proxy_list` 用 `;` 而非 `,` 分隔**：CSV 字段分隔符是 `,`，虽然 `csv_escape()` 会加引号处理，但用 `;` 可让原始 CSV 保持无引号、便于人工阅读与 grep 排障。

#### （4）表头同步

**定位**：`emit_rows()`，第 816-820 行

```python
def emit_rows(fp):
    if WITH_STATUS:
        header = "# service_name,host,port,user,password,database,status_code,status_text"
    else:
        header = "# service_name,host,port,user,password,database"
    if WITH_TYPE:
        header += ",instance_kind,instance_id,proxy_list"
    fp.write(header + "\n")
    fp.write("# 自动生成于: " + os.popen("date '+%Y-%m-%d %H:%M:%S'").read())
    fp.write("# 来源: tdsql_inventory.sh (ZK 自动发现)\n")
    fp.write("\n")
    for row in rows:
        fp.write(",".join(csv_escape(c) for c in row) + "\n")
```

#### （5）文件头注释同步

第 14 行附近的格式说明补充 `--with-type` 的列定义，与 `--with-status` 的写法一致。

---

### 7.3 B.4：`zk_discovery_service.py` 解析并回写

**文件**：`backend/services/zk_discovery_service.py`

#### （1）调用时带上新开关

`discover()` 的 `cmd` 列表（第 100 行附近），在 `"--with-status",` 后新增：

```python
            "--with-type",      # V1.5.1：取实例形态（规则适用域判定的权威源）
```

#### （2）`parse_csv()` 支持变长行

**全量替换** `parse_csv()`：

```python
    # kind → 本系统业务语义的映射。存原始 kind、映射在代码里做，
    # 便于与赤兔/ZK 对账；TDSQL 将来若增加形态，只改这张表。
    _KIND_TO_TYPE = {
        "noshard":    "centralized",   # 单 SET 实例 = 集中式
        "groupshard": "distributed",   # group 下多 SET = 分布式
    }

    def parse_csv(self, csv_content: str) -> list[dict]:
        """解析发现导出的 CSV。

        列布局（新列一律追加在末尾，保证旧消费方按前 N 列取值不受影响）：
            base            : service_name,host,port,user,password,database          (6)
            +--with-status  : ,status_code,status_text                               (8)
            +--with-type    : ,instance_kind,instance_id,proxy_list                  (11)

        V1.5.1：instance_kind 是实例类型判定的权威依据。
        脚本内部一直有这个字段，此前未导出。
        """
        results = []
        f = io.StringIO(csv_content.strip())
        for row in csv.reader(f):
            if not row or row[0].startswith("#"):
                continue
            if len(row) < 6:
                continue

            item = {
                "service_name": row[0],
                "host": row[1],
                "port": int(row[2]) if row[2].isdigit() else 15001,
                "user": row[3],
                "password": row[4],
                "database": row[5],
            }
            # 状态列（--with-status）
            if len(row) >= 8:
                item["status_code"] = row[6]
                item["status_text"] = row[7]
            # 形态列（--with-type）
            if len(row) >= 11:
                kind = (row[8] or "").strip()
                item["instance_kind"] = kind
                item["instance_id"] = (row[9] or "").strip()
                item["proxy_list"] = (row[10] or "").strip()
                item["instance_type"] = self._KIND_TO_TYPE.get(kind)
                if kind and item["instance_type"] is None:
                    logger.warning(
                        f"ZK 返回未知实例形态 kind={kind!r} "
                        f"(instance_id={item['instance_id']})，本条不参与类型判定")
            results.append(item)
        return results
```

> **未知 `kind` 必须告警且不判定**：若 TDSQL 将来引入新形态，静默映射成某一类就会重演本次事故（凭假设给结论）。这里选择"不给结论 + 告警"，判定自动下沉至声明值。

#### （3）新增：把形态同步回已注册实例

`ZKDiscoveryService` 新增方法：

```python
    def sync_instance_kinds(self, discovered: list[dict]) -> int:
        """把 ZK 发现的实例形态回写到已注册实例（V1.5.1）。

        匹配规则：已注册实例的 host:port ∈ 该 ZK 实例的 proxy_list。
        不用"等于 CSV 里的 host:port"—— 脚本按 --proxy-mode random 随机选一个
        网关输出，而系统里登记的可能是同实例的另一个网关
        （如 10.206.0.8:15002 vs 10.206.0.4:15002），只比选中项会漏配。

        Returns: 成功同步的实例数
        """
        from datetime import datetime
        from backend.services.database import _get_connection, ensure_db

        # 构建 "host:port" → (kind, instance_id) 索引
        index = {}
        for d in discovered:
            kind = d.get("instance_kind")
            if not kind:
                continue
            endpoints = [e.strip() for e in (d.get("proxy_list") or "").split(";") if e.strip()]
            # proxy_list 为空时退回 CSV 里选中的那一个
            if not endpoints:
                endpoints = [f"{d.get('host')}:{d.get('port')}"]
            for ep in endpoints:
                index[ep] = (kind, d.get("instance_id") or "")

        if not index:
            return 0

        synced = 0
        ensure_db()
        conn = _get_connection()
        try:
            rows = conn.execute(
                "SELECT id, host, port FROM tdsql_connections").fetchall()
            now = datetime.now().isoformat()
            for r in rows:
                r = dict(r)
                hit = index.get(f"{r.get('host')}:{r.get('port')}")
                if not hit:
                    continue
                kind, inst_id = hit
                conn.execute(
                    "UPDATE tdsql_connections SET zk_instance_kind = ?, "
                    "zk_instance_id = ?, zk_synced_at = ? WHERE id = ?",
                    (kind, inst_id, now, r["id"]))
                synced += 1
            conn.commit()
        finally:
            conn.close()

        if synced:
            from backend.services.instance_type_service import instance_type_service
            instance_type_service.invalidate()   # 全量失效，本进程立即生效
            logger.info(f"ZK 实例形态已同步 {synced} 个实例")
        return synced
```

**调用点**：`backend/api/zk_discovery.py::discover_instances()` 在拿到发现结果后、返回响应前调用，同步失败仅告警不影响发现结果返回。

---

### 7.4 B.5：`instance_type_service.py` 多源分级

**文件**：`backend/services/instance_type_service.py`

#### （1）`InstanceContext` 扩展

```python
@dataclass
class InstanceContext:
    """一次扫描的实例类型上下文，随调用链向下传递"""
    instance_type: InstanceType
    source: TypeSource
    conflict: bool = False
    declared: Optional[InstanceType] = None
    detected: Optional[InstanceType] = None
    zk: Optional[InstanceType] = None            # V1.5.1
    locked: Optional[InstanceType] = None        # V1.5.1
```

#### （2）`TypeSource` 新增取值

`backend/models/__init__.py`：

```python
class TypeSource(str, Enum):
    LOCKED   = "locked"     # V1.5.1 管理员锁定（最高）
    ZK       = "zk"         # V1.5.1 ZK 管控面（权威）
    PROBED   = "probed"     # SQL 层探测（V1.5.1 起暂无判据）
    DECLARED = "declared"   # 人工声明
    REQUEST  = "request"    # 调用方显式声明（B 类通道）
    DEFAULT  = "default"    # 全局默认
```

#### （3）`_resolve_by_connection()` 全量替换

```python
    _KIND_TO_TYPE = {"noshard": InstanceType.CENTRALIZED,
                     "groupshard": InstanceType.DISTRIBUTED}

    def _resolve_by_connection(self, connection_id: str) -> InstanceContext:
        """多源分级解析（V1.5.1）。

        优先级：S0 管理员锁定 > S1 ZK 管控面 > S2 SQL探测 > S3 人工声明

        除 S0 外，各源之间采用【取更保守者】合并：任一可用源判定为分布式，
        即按分布式执行。理由是两个方向的误判后果严重不对称——
        判成分布式只会多报（可见、可纠正），判成集中式会静默跳过 27 条规则
        （不可见、放行风险）。V1.5 的"探测一律优先"正是在此翻车。
        """
        now = time.time()
        with _cache_lock:
            hit = _cache.get(connection_id)
            if hit and now - hit[0] < _PROBE_CACHE_TTL:
                return hit[2]

        from backend.services.connection_registry import registry
        saved = registry.get_saved(connection_id) or {}

        # ── S3 人工声明（永远有值：is_distributed INT DEFAULT 1）──
        declared = (InstanceType.DISTRIBUTED
                    if int(saved.get("is_distributed", 1) or 0) == 1
                    else InstanceType.CENTRALIZED)

        # ── S0 管理员锁定：终审，直接返回，不参与保守合并 ──
        locked = None
        if int(saved.get("instance_type_locked", 0) or 0) == 1:
            raw = (saved.get("instance_type_locked_value") or "").strip()
            if raw in (InstanceType.DISTRIBUTED.value, InstanceType.CENTRALIZED.value):
                locked = InstanceType(raw)
        if locked is not None:
            ctx = InstanceContext(locked, TypeSource.LOCKED,
                                  declared=declared, locked=locked)
            with _cache_lock:
                _cache[connection_id] = (now, connection_id, ctx)
            return ctx

        # ── S1 Proxy 层 SQL 探测（PR001/PR002，见 instance_probe_rules）──
        # 排在 ZK 之前：读的是同一个底层事实，但实时、且无外部依赖（§4.1）
        detected = None
        raw = saved.get("detected_instance_type")
        if raw in (InstanceType.DISTRIBUTED.value, InstanceType.CENTRALIZED.value):
            detected = InstanceType(raw)
        else:
            detected = self._probe_and_persist(connection_id)

        # ── S2 ZK 管控面（交叉校验 + 探测失败/实例不可达时兜底）──
        zk = self._KIND_TO_TYPE.get((saved.get("zk_instance_kind") or "").strip())

        # ── 保守合并 ──
        candidates = [(TypeSource.PROBED, detected), (TypeSource.ZK, zk),
                      (TypeSource.DECLARED, declared)]
        available = [(s, v) for s, v in candidates if v is not None]

        # 任一源说分布式 → 分布式（保守）
        dist = [(s, v) for s, v in available if v == InstanceType.DISTRIBUTED]
        if dist:
            src, val = dist[0]              # 取优先级最高的那个作为 source 标注
        else:
            src, val = available[0]         # 全部为集中式，取最高优先级源

        conflict = len({v for _, v in available}) > 1
        ctx = InstanceContext(val, src, conflict=conflict,
                              declared=declared, detected=detected, zk=zk)

        if conflict:
            logger.warning(
                f"实例 {connection_id} 类型判定存在分歧："
                f"探测={detected.value if detected else '无'}，"
                f"ZK={zk.value if zk else '无'}，"
                f"声明={declared.value}。按保守原则采用 {val.value}"
                f"（来源 {src.value}）。")

        with _cache_lock:
            _cache[connection_id] = (now, connection_id, ctx)
        return ctx
```

> **`candidates` 列表顺序即优先级**，`dist[0]` / `available[0]` 依赖该顺序，**调整顺序会改变 `source` 标注**，施工时不要重排。

#### （4）`_probe_and_persist()` 适配

`probe_instance_type()` 现返回 `(None, {...})`，落库分支走 `else`，写入 `instance_type_probe_error`。将该分支的错误文案改为中性描述，避免在实例详情里显示成"探测失败"（它不是失败，是**已停用**）：

```python
        if result:
            ...  # 不变
        else:
            note = detail.get("reason", "") if isinstance(detail, dict) else ""
            conn.execute(
                "UPDATE tdsql_connections SET instance_type_probe_error = ? "
                "WHERE id = ?", (note[:500], connection_id))
```

#### （5）新增锁定读写方法

```python
    def set_lock(self, connection_id: str, locked: bool,
                 instance_type: Optional[str] = None) -> None:
        """管理员锁定/解锁实例类型（V1.5.1）。

        解锁时保留 instance_type_locked_value，便于前端回显上次选择。
        """
        from backend.services.database import _get_connection, ensure_db
        if locked:
            if instance_type not in (InstanceType.DISTRIBUTED.value,
                                     InstanceType.CENTRALIZED.value):
                raise ValueError("instance_type 仅支持 distributed 或 centralized")
        ensure_db()
        conn = _get_connection()
        try:
            if locked:
                conn.execute(
                    "UPDATE tdsql_connections SET instance_type_locked = 1, "
                    "instance_type_locked_value = ? WHERE id = ?",
                    (instance_type, connection_id))
            else:
                conn.execute(
                    "UPDATE tdsql_connections SET instance_type_locked = 0 "
                    "WHERE id = ?", (connection_id,))
            conn.commit()
        finally:
            conn.close()
        self.invalidate(connection_id)
```

---

### 7.5 B.6 / C.3：API 层

**文件**：`backend/api/tdsql_manage.py`

#### （1）探测接口改多源

`probe_now()` 改为返回 §6.2 的 `sources` 结构。要点：

- 逐源给出 `available` / `value` / `reason`
- `probe` 源的 `reason` 直接取 `probe_instance_type()` 返回明细里的 `reason`
- `message` 按最终 source 分支生成，且**必须给出下一步建议**（跑 ZK 发现 / 管理员锁定）

#### （2）锁定接口

```python
@router.put("/connections/{connection_id}/instance-type-lock",
            summary="管理员锁定/解锁实例类型")
def set_instance_type_lock(connection_id: str, payload: dict, http_request: Request):
    """V1.5.1：管理员终审实例类型，优先级高于一切自动判定源。

    锁成 centralized 会关掉 27 条仅分布式适用的规则，是唯一可能造成
    静默漏报的操作，因此强制填写理由并落审计日志。
    """
    if _role(http_request) != "admin":
        raise HTTPException(status_code=403, detail="仅系统管理员可锁定实例类型")

    locked = bool(payload.get("locked"))
    itype = (payload.get("instance_type") or "").strip()
    reason = (payload.get("reason") or "").strip()

    if locked:
        if itype not in ("distributed", "centralized"):
            raise HTTPException(status_code=400,
                                detail="instance_type 仅支持 distributed 或 centralized")
        if itype == "centralized" and not reason:
            raise HTTPException(
                status_code=400,
                detail="锁定为「集中式」将跳过 27 条仅分布式适用的规则，请填写锁定理由")

    from backend.services.instance_type_service import instance_type_service
    instance_type_service.set_lock(connection_id, locked, itype if locked else None)

    # 审计
    try:
        from backend.services.audit_log_service import write_log
        write_log(operation_type="instance_type_lock",
                  operator=_operator(http_request),
                  detail=f"connection_id={connection_id} locked={locked} "
                         f"value={itype} reason={reason}")
    except Exception as e:
        logger.warning(f"锁定操作审计日志写入失败: {e}")

    _cn = {"distributed": "分布式", "centralized": "集中式"}
    return {
        "success": True,
        "connection_id": connection_id,
        "locked": locked,
        "instance_type": itype if locked else "",
        "message": (f"已锁定实例类型为「{_cn.get(itype, itype)}」。"
                    f"配置最长 5 分钟后在全部服务进程生效。" if locked
                    else "已解除实例类型锁定，恢复按自动判定源解析。"),
    }
```

> **审计日志的写入函数名以仓库实际实现为准**，施工时按 `operation_logs` 既有写入路径对齐，不要新造。

#### （3）RBAC

路径前缀 `/api/v1/tdsql/connections` 已在 `auth_service._PATH_TO_MENU` 登记为 `instances`，中间件自动覆盖。但 `instances` 菜单可能同时授予 `dba`，因此**处理函数内的 `_role(...) != "admin"` 显式校验不可省略**（双保险，与 v1.3 `_require_admin` 同款处理）。

---

### 7.6 B.8 / C.4：前端

| # | 位置 | 改动 |
|---|---|---|
| **F1** | 实例列表「类型」列 | 徽标显示**生效类型 + 来源**：`分布式·ZK` / `集中式·锁定` / `分布式·声明`。来源为 `declared` 且无任何权威源时显示灰色（提示"未经权威源确认"） |
| **F2** | 实例列表「类型」列 | `conflict=true` 时红色叹号 + tooltip 列出各源结论 |
| **F3** | 「探测类型」按钮 | 结果弹窗改为**多源明细表**（源 / 是否可用 / 结论 / 原因），而非单句结论 |
| **F4** | 实例操作列 | 新增「锁定类型」按钮（仅 admin 可见），弹窗含类型单选 + 理由输入框（选「集中式」时理由必填） |
| **F5** | 实例详情 | 显示 `zk_instance_id`、`zk_synced_at`；SQL 探测一栏显示"已停用（TDSQL 无 SQL 层判据）"，**不显示为错误** |
| **F6** | ZK 自动发现结果表 | 新增「实例形态」列（分布式/集中式），并在注册后提示"已同步 N 个已注册实例的实例形态" |

**F3 的弹窗形态**（本次事故催生，务必落实）：

| 判定源 | 可用 | 结论 | 说明 |
|---|---|---|---|
| 管理员锁定 | ✗ | — | 未锁定 |
| ZK 管控面 | ✗ | — | 尚未执行 ZK 自动发现 |
| SQL 探测 | ✗ | — | 已停用：TDSQL 后端节点无法区分实例类型 |
| 实例声明 | ✓ | 集中式 | 来自实例配置 |
| **最终生效** | | **集中式** | 来源：实例声明 |

---

## 8. Proxy 层判据：实测结果与采纳裁定

### 8.1 实测已完成（2026-07-29）

| 项 | 内容 |
|---|---|
| 执行人 | 智能体 G |
| 原始报告 | `docs/REPORT-v1.5.1-Proxy层实例类型判据实测结果-G.md` |
| 被测对象 | `SIT-集中式实例A`（Proxy `:15002`） / `SIT-分布式实例A`（Proxy `:15005`） |
| 采集方式 | **PyMySQL 直连 Proxy 端口**，账号与「实例管理」配置一致 |
| **结论** | **Proxy 层存在决定性差异，判据成立** |

**G 的方法偏离及评价**：测试文档规定用 `mysql` CLI + `--comments`，G 实际用了 **PyMySQL**。

> **这个偏离是正确的，且优于原方案。** PyMySQL 正是系统自身使用的驱动，比 CLI 更贴近"采集环境 = 判定环境"这条硬性前提。此外它天然不剥离注释，绕开了 `--comments` 这个最易翻车的点。**后续复测应直接沿用 PyMySQL 方案，测试文档据此更新。**

**独立交叉核验**：G 报告的 T01 输出与赤兔管理台数据**逐字吻合**——

| 项 | 赤兔管理台（用户独立提供） | G 实测 T01 |
|---|---|---|
| 分布式 groupid | `group_1782132247_10` | `cluster` = `group_1782132247_10` |
| 分片 1 | `set_1782132369_1` **(0-7)** | `set_1782132369_1:hash_range` = `0---7` |
| 分片 2 | `set_1782132389_3` **(8-15)** | `set_1782132389_3:hash_range` = `8---15` |
| 集中式实例 ID | `set_1782130875_4` | `set` = `set_1782130875_4` |
| 集中式后端节点 | 【主】`10.206.0.4:4002`【备】`10.206.0.8:4002` | `10.206.0.4:4002;s1@10.206.0.8:4002@...` |

**两路独立来源完全一致，数据可信度确认。**

**另一项自证**：CENT 的 T01 只返回 **2 行**。而普通 `SHOW STATUS` 在本版本返回 **458 行**（上一轮后端实测已确认）。**行数从 458 降到 2，本身就证明 `/*proxy*/` hint 确实被 Proxy 拦截并改写了**——原设计中"T01 vs T09 对照"这个自检目的，由此以另一条路径达成。

> **测试文档缺陷（本人责任）**：T09 用了 `information_schema.SESSION_STATUS`，该表在 MySQL 8.0 已移除，两侧均报 `Unknown table`。原定的对照校验落空。所幸 458→2 的行数差已提供等价证据。测试文档需修正此用例。

### 8.2 判据采纳裁定

G 提出 3 项候选判据。**逐条对照 §8.4 三项标准**：

| 判据 | 标准 1<br>确有差异 | 标准 2<br>阳性方向明确 | 标准 3<br>未命中不判集中式 | 裁定 |
|---|---|---|---|---|
| **PR001** T01 `/*proxy*/show status` 内容 | ✅ | ✅ | ⚠️ G 的写法违反 | **采纳（重写后）· 主判据** |
| **PR002** T10 `EXPLAIN` 的 `info` 列 | ✅ | ✅ | ❌ G 的写法违反 | **采纳（仅阳性方向）· 辅助** |
| **PR003** T11 `SHOW CREATE TABLE` 的 `shardkey=` | ✅ | ✅ | ❌ G 的写法未涉及 | **采纳（仅阳性方向）· 可选兜底** |

**G 提出的代码实现不予采纳。** 原因见 §8.3。

### 8.3 为什么 G 的代码实现不能直接用

G 在报告 §4 给出的 `probe_instance_type()` 实现有**两处会重演本次事故**，且方向更危险：

```python
# G 的原始实现（节选）
        is_cent = any(r.get("status_name") == "set" for r in rows ...)
        if is_cent:
            return "centralized", detail          # ← 问题 1
    except Exception as e:
        detail["proxy_show_status"] = {"ok": False, ...}

    # 兜底探针
    try:
        exp_rows = self._execute("EXPLAIN SELECT 1")
        if exp_rows and "info" in exp_rows[0]:
            return "distributed", detail
        else:
            return "centralized", detail          # ← 问题 2（更严重）
```

**问题 1：把"没看到 cluster"当成"是集中式"。**
这是**阴性证据当阳性用**。若某个 TDSQL 版本不输出 `cluster` 行、或单分片分布式实例的输出形态不同，真正的分布式实例会被判成集中式——**27 条规则静默失效**。

**问题 2（更严重）：`else: return "centralized"` 是一个兜底判定。**
注意它的触发路径：**T01 抛异常**（网络抖动、权限不足、Proxy 忙）→ 落入 EXPLAIN 分支 → EXPLAIN 成功但没有 `info` → **判集中式**。

> **一次网络抖动就能让一台分布式实例的 27 条规则静默关闭。** 这正是 §8.4 标准 3 要防的东西，也是本次事故的镜像——v1.5 是"恒判分布式"（误报，可见），这个写法是"易判集中式"（**漏报，不可见**）。**后者危险得多。**

**问题 3：`EXPLAIN` 的 `info` 列缺失是弱证据。**
`EXPLAIN SELECT 1` 不涉及任何表，Proxy 为它注入路由信息（`set_1782132369_1,EXPLAIN SELECT 1`）带有偶然性。**有 `info` 是强阳性；没有 `info` 什么也证明不了。**

### 8.4 判据采纳标准（原文保留，本次据此裁定）

一条判据只有**同时满足**下列三条，才可填入 `ACTIVE_PROBE_RULES`：

1. **两类实例上实测输出确有差异** —— 必须把两份真实输出写进 `evidence` 字段，**不接受"按文档应该有差异"**；
2. 差异**方向明确**：命中即为分布式的**阳性证据**；
3. 未命中时**不得**判集中式，必须返回"无结论"下沉至下一源。

**外加一条硬性配套**：每新增一条判据，**必须同时补一条反向鉴别用例**（对两类实例各跑一次、断言结论**不同**）。

> **本次裁定对标准 3 的一处澄清**：标准 3 禁止的是**以"未命中"为由判集中式**（阴性证据当阳性用）。它**不禁止**基于**集中式自身的阳性特征**做判定。
>
> T01 恰好提供了这样的阳性特征：**`set` 行的 value 只含一个 SET（无逗号）+ 存在以该 SET ID 为键的明细行 + 无 `cluster` 行**——这是"单 SET 拓扑"的**结构性正面签名**，不是"没看到分布式特征"。
>
> 因此 PR001 允许输出 `centralized`，但**必须命中该正面签名**；形态不符（如无 `set` 行、值为空、格式异常）一律返回**无结论**。**PR002 / PR003 没有对应的集中式正面签名，故只允许输出 `distributed` 或无结论。**

### 8.5 采纳的判据定义（照图施工）

以下内容直接落入 `backend/services/instance_probe_rules.py`。

#### PR001 — `/*proxy*/show status` 拓扑签名（主判据）

**实测原文**

```
CENT (:15002)  —— 共 2 行
  {"status_name": "set",                "value": "set_1782130875_4"}
  {"status_name": "set_1782130875_4",   "value": "10.206.0.4:4002;s1@10.206.0.8:4002@100@IDC3@0"}

DIST (:15005)  —— 共 8 行（G 报告列出其中 4 行）
  {"status_name": "cluster",                       "value": "group_1782132247_10"}
  {"status_name": "set_1782132369_1:hash_range",   "value": "0---7"}
  {"status_name": "set_1782132389_3:hash_range",   "value": "8---15"}
  {"status_name": "set",                           "value": "set_1782132369_1,set_1782132389_3 "}
```

**判定逻辑**

| 优先级 | 签名 | 结论 |
|---|---|---|
| 1 | 存在 `status_name == "cluster"` 的行 | **distributed** |
| 2 | 任一 `status_name` 含 `":hash_range"` | **distributed** |
| 3 | `set` 行 value 按逗号切分后 **≥ 2 个非空 SET** | **distributed** |
| 4 | `set` 行 value 恰好 **1 个非空 SET**，且无上述 1/2/3 | **centralized** |
| 5 | 其余一切情况（无 `set` 行、value 为空、语句失败） | **无结论** |

**实现要点**

- `value` 必须 `.strip()`——实测 DIST 的 `set` 行 value 末尾带一个空格（`"set_..._1,set_..._3 "`），不处理会导致切分出空元素；
- 切分后**过滤空串**再计数，否则末尾逗号会让 1 个 SET 被数成 2 个；
- 行可能是 dict 也可能是 tuple，取值需兼容（沿用 `discover_sets()` 的多字段名兼容写法：`status_name` / `Variable_name` / `name` / `Key`）。

**已知边界**（写入代码注释）：单分片分布式实例若不输出 `cluster` 行，将命中签名 4 被判集中式。该形态未实测。缓解措施是 §4.3 的保守合并——只有当人工声明**也**为集中式时该结论才会生效。

#### PR002 — `EXPLAIN` 路由信息注入（辅助，仅阳性）

**实测原文**

```
CENT: {id, select_type, table, partitions, type, possible_keys, key,
       key_len, ref, rows, filtered, Extra}                      ← 标准 12 列，无 info
DIST: {..., Extra: "No tables used",
       info: "set_1782132369_1,EXPLAIN SELECT 1"}                ← Proxy 注入 info
```

**判定逻辑**：结果集首行含 `info` 键 → **distributed**；**其余一切情况 → 无结论**。

**严禁**由"无 `info`"推出集中式（§8.3 问题 3）。

#### PR003 — 表 DDL 分片标记（可选兜底，仅阳性）

**实测原文**

```
CENT: ... ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='...'
DIST: ... ENGINE=InnoDB AUTO_INCREMENT=25600001 DEFAULT CHARSET=utf8mb4
          COLLATE=utf8mb4_bin shardkey=id
```

**判定逻辑**：DDL 匹配 `\bshardkey\s*=` 或广播表标记 → **distributed**；其余 → **无结论**。

**默认不启用。** 它需要先选定一张表，比 PR001/PR002 昂贵且有副作用（空库无表可查）。仅在 PR001 与 PR002 均无结论时，由诊断接口按需调用。

### 8.6 未采纳与待确认项

| 用例 | G 的评价 | 本次裁定 |
|---|---|---|
| T02 `show connectionpool` · T03 `show shard` · T04 `show sets` | 两侧均 `Command is not supported` | **不采纳**，本版本 Proxy 不支持 |
| T05 `show variables` | 两侧 199 项完全一致 | **不采纳** |
| T07 / T08 information_schema | 两侧完全一致 | **不采纳**（与后端节点结论一致） |
| **T06 `show databases`** | G 评为"弱（业务库差异）" | ⚠️ **不同意此评价，列为待确认** |

**关于 T06**：G 记录 DIST 比 CENT 多出 **`xa`** 库，并归因为"业务库差异"。

> **`xa` 不是业务库。** XA 是分布式事务协议，`xa` 极可能是 TDSQL 为**跨分片分布式事务**准备的系统库——而这正是分布式实例独有的能力（赤兔实例详情页对分布式实例显示 `分布式事务(xa_support): 开启分布式事务(1)`，集中式实例详情页无此项）。
>
> 若属实，"存在 `xa` 库"是一条**结构性阳性判据**，成本极低（一条 `show databases`）。
>
> **待确认**：需要 G 补充确认 `xa` 库的归属（是 TDSQL 系统库还是业务自建）。**确认前不入表**——这正是 §8.4 标准 1 的要求，不接受"看起来应该是"。

### 8.7 需要 G 补充的材料

| # | 材料 | 原因 |
|---|---|---|
| 1 | **`out_CENT.txt` / `out_DIST.txt` 原始文件提交入库** | 报告称已存于 `scratch/`，但**未随提交入库**，本人无法核验。§8.5 各判据的 `evidence` 字段需引用原文出处 |
| 2 | **DIST 的 T01 完整 8 行**（报告只列出 4 行） | 未列出的 4 行可能含额外签名；且 PR001 的边界处理需要看全 |
| 3 | `xa` 库归属确认（见 §8.6） | 决定是否新增 PR004 |

> 材料 1、2 **不阻塞 Q 开工**——PR001/PR002/PR003 的判定逻辑已由现有数据充分支撑，且经赤兔交叉核验。补充材料用于完善 `evidence` 与边界处理，可与开发并行。

---

## 9. 测试设计

### 9.1 必须删除的既有用例

`tests/test_instance_type_service.py`：

| 用例 | 处理 | 原因 |
|---|---|---|
| `test_probe_distributed_when_proxy_ok` | **删除** | 该用例逐字锁定了错误规格（proxy ok → distributed），它通过恰恰证明缺陷存在 |
| `test_probe_wins_over_declaration` | **改写** | 策略已改为保守取值，"探测一律优先"不再成立 |
| `test_both_probes_error_returns_none_not_centralized` | **保留** | 结论仍正确（无结论时不得判集中式），只是现在恒走该分支 |

### 9.2 新增用例

#### （1）反向鉴别用例（本次事故的直接防线）

```python
# 实测原文（2026-07-29，REPORT-v1.5.1）。用作 mock 数据源，
# 使单元测试不依赖真实实例即可复现两类实例的差异。
FAKE_STATUS_CENT = [
    {"status_name": "set", "value": "set_1782130875_4"},
    {"status_name": "set_1782130875_4",
     "value": "10.206.0.4:4002;s1@10.206.0.8:4002@100@IDC3@0"},
]
FAKE_STATUS_DIST = [
    {"status_name": "cluster", "value": "group_1782132247_10"},
    {"status_name": "set_1782132369_1:hash_range", "value": "0---7"},
    {"status_name": "set_1782132389_3:hash_range", "value": "8---15"},
    # 注意末尾空格 —— 实测原样，用于验证 strip 处理
    {"status_name": "set", "value": "set_1782132369_1,set_1782132389_3 "},
]


def test_probe_must_not_be_a_constant_function():
    """探测源不得对两类实例返回相同结论。

    这是 V1.5 缺陷的直接防线：当时的探测对任何实例恒返回 "distributed"，
    是一个常量函数。任何"某类返回某值"式的断言都发现不了这一点，
    只有对两类目标各跑一次、断言结论【不同】，才能捕获。

    【新增判据时必须同步扩充本用例】——见 §8.4 的硬性配套要求。
    """
    from backend.services.instance_probe_rules import _decide_proxy_status
    assert _decide_proxy_status(FAKE_STATUS_DIST) == "distributed"
    assert _decide_proxy_status(FAKE_STATUS_CENT) == "centralized"
    assert (_decide_proxy_status(FAKE_STATUS_DIST)
            != _decide_proxy_status(FAKE_STATUS_CENT)), "探测源无鉴别力"
```

#### （2）P0 止血验证

```python
def test_disabled_probe_does_not_override_declaration():
    """P0 核心：探测停用后，使用者声明的「集中式」必须生效。

    复现缺陷现场：SIT-集中式实例A 声明 centralized，
    V1.5 下被探测覆盖成 distributed，R077 照报。
    """
    ctx = instance_type_service.resolve("conn_declared_centralized")
    assert ctx.instance_type == InstanceType.CENTRALIZED
    assert ctx.source == TypeSource.DECLARED


def test_r077_gone_for_declared_centralized_instance():
    """端到端：声明为集中式的实例，审核不得出现 R077"""
    ctx = instance_type_service.resolve("conn_declared_centralized")
    sql = "CREATE TABLE t_user (id BIGINT PRIMARY KEY) ENGINE=InnoDB"
    r = RuleChecker().audit_sql(sql, instance_type=ctx.instance_type.value)
    assert "R077" not in {v.rule_id for v in r.violations}
```

#### （3）保守合并策略

```python
@pytest.mark.parametrize("zk,detected,declared,expect", [
    ("groupshard", None, "centralized", InstanceType.DISTRIBUTED),  # ZK 说分布式 → 分布式
    ("noshard",    None, "distributed", InstanceType.DISTRIBUTED),  # 声明说分布式 → 仍分布式（保守）
    ("noshard",    None, "centralized", InstanceType.CENTRALIZED),  # 一致 → 集中式
    (None,         None, "centralized", InstanceType.CENTRALIZED),  # 仅声明可用
])
def test_conservative_merge(zk, detected, declared, expect):
    """任一源说分布式即按分布式：判成集中式是不可见的失效方向"""
    ...


def test_lock_overrides_everything():
    """S0 管理员锁定是终审，覆盖 ZK 权威源"""
    # zk_instance_kind=groupshard，但管理员锁定 centralized
    ctx = instance_type_service.resolve("conn_locked_centralized")
    assert ctx.instance_type == InstanceType.CENTRALIZED
    assert ctx.source == TypeSource.LOCKED
```

#### （4）ZK 解析与同步

```python
def test_parse_csv_11_columns():
    csv_text = ("# header\n"
                "集中式实例,10.206.0.4,15002,u,p,ALL,0,运营中,"
                "noshard,set_1782130875_4,10.206.0.4:15002;10.206.0.8:15002\n")
    item = ZKDiscoveryService().parse_csv(csv_text)[0]
    assert item["instance_kind"] == "noshard"
    assert item["instance_type"] == "centralized"
    assert item["instance_id"] == "set_1782130875_4"


def test_parse_csv_backward_compatible():
    """6 列 / 8 列旧格式必须继续可解析（新列一律追加在末尾）"""
    assert ZKDiscoveryService().parse_csv("a,h,15002,u,p,ALL\n")[0]["host"] == "h"


def test_unknown_kind_does_not_guess():
    """未知形态不得静默映射 —— 那是本次事故的同类错误"""
    item = ZKDiscoveryService().parse_csv(
        "n,h,1,u,p,ALL,0,ok,brand_new_kind,x_1,h:1\n")[0]
    assert item["instance_type"] is None


def test_sync_matches_any_proxy_of_instance():
    """系统登记的是同实例的另一个网关时也必须匹配上"""
    # ZK CSV 选中 10.206.0.4:15002，系统登记的是 10.206.0.8:15002
    # proxy_list 含两者 → 应同步成功
    ...
```

#### （5）判据表（C 组）

```python
def test_pr001_signature_priority():
    """PR001 判定表逐行验证（§8.5）"""
    from backend.services.instance_probe_rules import _decide_proxy_status as d
    # 签名 1：cluster 行
    assert d([{"status_name": "cluster", "value": "group_x"}]) == "distributed"
    # 签名 2：hash_range 行（即使没有 cluster 行）
    assert d([{"status_name": "set_a:hash_range", "value": "0---7"}]) == "distributed"
    # 签名 3：多 SET
    assert d([{"status_name": "set", "value": "set_a,set_b"}]) == "distributed"
    # 签名 4：单 SET → 集中式正面签名
    assert d([{"status_name": "set", "value": "set_a"}]) == "centralized"


def test_pr001_handles_trailing_space_and_comma():
    """实测 DIST 的 set 行 value 末尾带空格；尾随逗号也不得把 1 个数成 2 个"""
    from backend.services.instance_probe_rules import _decide_proxy_status as d
    assert d([{"status_name": "set", "value": "set_a,set_b "}]) == "distributed"
    assert d([{"status_name": "set", "value": "set_a,"}]) == "centralized"
    assert d([{"status_name": "set", "value": " set_a "}]) == "centralized"


@pytest.mark.parametrize("rows", [
    [],                                              # 空结果
    [{"status_name": "other", "value": "x"}],        # 无 set 行
    [{"status_name": "set", "value": ""}],           # set 行值为空
    [{"status_name": "set", "value": " , "}],        # 全是分隔符
])
def test_pr001_unknown_shape_returns_none(rows):
    """形态不符一律返回无结论，绝不猜（§8.4 标准 3）"""
    from backend.services.instance_probe_rules import _decide_proxy_status as d
    assert d(rows) is None


def test_pr002_only_positive():
    """PR002 只判阳性：有 info → 分布式；无 info → 无结论（不是集中式）"""
    from backend.services.instance_probe_rules import _decide_explain_info as d
    assert d([{"id": 1, "Extra": "No tables used",
               "info": "set_1782132369_1,EXPLAIN SELECT 1"}]) == "distributed"
    assert d([{"id": 1, "Extra": "No tables used"}]) is None    # 不是 "centralized"
    assert d([]) is None


def test_pr003_only_positive():
    from backend.services.instance_probe_rules import _decide_table_ddl as d
    assert d([{"Create Table": "... COLLATE=utf8mb4_bin shardkey=id"}]) == "distributed"
    assert d([{"Create Table": "... COLLATE=utf8mb4_bin COMMENT='x'"}]) is None
    assert d([]) is None


def test_pr003_disabled_by_default():
    """PR003 需要先选表，默认不参与自动探测"""
    from backend.services.instance_probe_rules import ACTIVE_PROBE_RULES
    pr003 = [r for r in ACTIVE_PROBE_RULES if r.rule_id == "PR003"]
    assert pr003 and pr003[0].enabled is False


def test_every_rule_has_evidence():
    """§8.4 标准 1：入表判据必须写明实测出处，不接受空 evidence"""
    from backend.services.instance_probe_rules import ACTIVE_PROBE_RULES
    for r in ACTIVE_PROBE_RULES:
        assert r.evidence and "实测" in r.evidence, f"{r.rule_id} 缺少实测依据"


def test_positive_beats_negative():
    """阳性优先：一条判分布式、一条判集中式时，必须取分布式"""
    # PR001 给集中式、PR002 给分布式 → 结果应为 distributed
    result, detail = pool_cent_status_but_explain_info.probe_instance_type()
    assert result == "distributed"


def test_rule_failure_does_not_raise():
    """判据执行异常仅记录，不得抛出（INV-5）"""
    result, _ = broken_pool.probe_instance_type()
    assert result is None


def test_all_rules_no_conclusion_returns_none():
    """全部判据无结论 → None，绝不回退成某个默认类型"""
    result, detail = pool_with_unknown_shape.probe_instance_type()
    assert result is None
    assert detail["matched"] is None


def test_diagnostics_collects_all_statements():
    """诊断采集：单条语句失败不影响其余条目"""
    out = pool_with_partial_support.collect_probe_diagnostics()
    assert set(out["statements"]) >= {"proxy_show_status", "show_databases"}


@pytest.mark.parametrize("bad", ["a;drop table t", "a b", "`x`", "a.b.c", "-- x"])
def test_diagnostics_rejects_bad_sample_table(bad):
    """sample_table 进入 SHOW CREATE TABLE，必须无任何拼接注入面"""
    with pytest.raises(HTTPException) as e:
        probe_diagnostics("conn_x", {"sample_table": bad})
    assert e.value.status_code == 400
```


### 9.3 真实环境验收（SIT）

| # | 用例 | 步骤 | 预期 |
|---|---|---|---|
| **H1** | **缺陷现场复验（本次交付核心验收项）** | 对 `SIT-集中式实例A` 执行在线元数据审核 | 报告中 **R077 = 0**；横幅显示"按【集中式】口径评估，已跳过 27 条" |
| **H2** | **探测判对集中式** | 对 `SIT-集中式实例A` 点「探测类型」 | 生效类型 = **集中式**，来源 = **探测(PR001)**；多源明细中 PR001 命中"单 SET 拓扑" |
| **H2b** | **探测判对分布式** | 对 `SIT-分布式实例A` 点「探测类型」 | 生效类型 = **分布式**，来源 = 探测(PR001)；明细显示命中 `cluster` 签名 |
| H3 | 分布式零回归 | 对 `SIT-分布式实例A` 扫描，与 v1.5.0.0 逐条 diff | **完全一致** |
| H4 | ZK 形态同步 | 执行「ZK 自动发现」 | 集中式实例 `zk_instance_kind=noshard`、`zk_instance_id=set_1782130875_4`；分布式 `groupshard` / `group_1782132247_10`（与赤兔一致） |
| H5 | 跨网关匹配 | 系统登记 `10.206.0.8:15002`，ZK CSV 选中 `10.206.0.4:15002` | 仍同步成功 |
| H6 | 保守合并 | 把分布式实例声明故意改为「集中式」 | 生效仍为分布式（探测/ZK 均判分布式），扫描中 R077 仍在 |
| **H6b** | **探测与 ZK 交叉一致** | 两台实例分别比对探测结论与 `zk_instance_kind` | **两路结论必须一致**；不一致即为异常，须查明原因 |
| H7 | 管理员锁定 | admin 锁定某实例为「集中式」并填理由 | 生效类型 = 集中式，来源 = 锁定；`operation_logs` 有记录 |
| H8 | 锁定权限 | dba 调用锁定接口 | 403 |
| H9 | 锁定理由强制 | 锁「集中式」不填理由 | 400 + 明确提示 |
| H10 | 全量回归 | `pytest` | 全绿 |

---

## 10. 施工检查清单

> **交付方式：A / B / C 三组一次性开发完成，单次交付。** 下列各组不分先后上线，但组内顺序按 §7.0 的依赖关系施工。

**A 组 止血**
- [ ] `probe_instance_type()` 由**判据表驱动**；全部判据无结论时返回 `(None, {...})`
- [ ] **阳性优先**：一条判分布式、一条判集中式时取分布式
- [ ] docstring 中的事故记录**完整保留**，未被精简
- [ ] `_resolve_by_connection()` 冲突策略为**取更保守者**，不再"探测一律优先"
- [ ] `test_probe_distributed_when_proxy_ok` **已删除**
- [ ] 新增 `test_probe_must_not_be_a_constant_function` 反向鉴别用例

**B 组 权威源**
- [ ] 迁移 `v5/050_instance_type_authority.sql` 已建，注释均为整行 `--`，无行尾注释
- [ ] `database.py::_ensure_columns` 已补 5 列双保险
- [ ] `tdsql_inventory.sh` 新增 `--with-type`，新列**追加在末尾**，表头同步
- [ ] `parse_csv()` 兼容 6 / 8 / 11 列三种长度
- [ ] 未知 `kind` **不猜**，`instance_type=None` + 告警
- [ ] `sync_instance_kinds()` 按 **proxy_list 全集**匹配，不是只比 CSV 选中项
- [ ] `zk_discovery.py` 已调用同步，失败仅告警不影响发现结果
- [ ] `candidates` 列表顺序未被重排（**顺序即优先级**）
- [ ] 锁定接口 admin-only：中间件 + 处理函数内显式校验**双保险**
- [ ] 锁「集中式」强制填理由，且落 `operation_logs`
- [ ] 前端 F3 多源明细弹窗已实现（不是单句结论）

**C 组 判据表**
- [ ] `instance_probe_rules.py` 已建，含 **PR001 / PR002 / PR003** 三条判据
- [ ] **PR003 `enabled=False`**（需先选表，默认不参与自动探测）
- [ ] PR001 的 `value` 做了 **`.strip()` + 过滤空串**（实测原文末尾带空格）
- [ ] PR001 形态不符（无 `set` 行 / 值为空）返回 **`None`**，不猜
- [ ] **PR002 / PR003 只判阳性**，`return None` 而非 `"centralized"`
- [ ] 每条判据 `evidence` 写明实测出处（`test_every_rule_has_evidence` 通过）
- [ ] 反向鉴别用例用**实测原文**做 mock，断言两侧结论**不同**
- [ ] `collect_probe_diagnostics()` 单条语句失败不影响其余
- [ ] `POST /probe-diagnostics` 的 `sample_table` 已做标识符白名单校验（`^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)?$`）
- [ ] 诊断响应回带 `endpoint` / `declared_instance_type` / `zk_instance_kind`（缺了无法配对分析）
- [ ] 前端「采集探测诊断」按钮 + **结果下载**已实现

**D 组 收尾**
- [ ] 全部 SQL 使用 `?` 占位符，无手写 `%s`
- [ ] 生效时延文案一律"**最长 5 分钟**"，全库检索无"即时生效"
- [ ] 存量数据**无任何回填 UPDATE**
- [ ] 版本号 → `1.5.1.0`（`backend/config.py` + `frontend/index.html` 标题与页脚）
- [ ] `DETAIL-v1.5-*.md` §4.1 已加勘误标注，指向本文档
- [ ] `pytest` 全绿
- [ ] SIT **H1 通过**：`SIT-集中式实例A` 的 R077 归零（**本次交付的核心验收项**）
- [ ] SIT **H3 通过**：分布式实例逐条 diff 零回归

**实测与判据评审（✅ 已完成，2026-07-29）**
- [x] G 已完成两类实例成对采集（PyMySQL 直连 Proxy 端口）
- [x] 采集环境确认表全部为「是」
- [x] 实测结论与赤兔管理台交叉核验一致
- [x] 候选判据逐条对照 §8.4 评审：PR001/PR002/PR003 采纳，T02–T05/T07/T08 不采纳
- [x] G 提出的代码实现**不予采纳**（两处违反标准 3，见 §8.3）

**待 G 补充（不阻塞开工，见 §8.7）**
- [ ] `out_CENT.txt` / `out_DIST.txt` 原始文件提交入库
- [ ] DIST 的 T01 完整 8 行（报告只列出 4 行）
- [ ] `xa` 库归属确认 → 决定是否新增 PR004

---

## 11. 交付说明

### 11.1 交付方式

**A / B / C 三组一次性开发完成，单次交付**（负责人 2026-07-29 决定）。编码由智能体 Q 执行。

原设计建议 P0 先行独立上线，现按负责人决定合并为单次交付；且 Proxy 层实测提前至开发之前（见 §8.1）。**这不影响修复效果**——A 组（止血）的行为在合并交付后完全一致，`SIT-集中式实例A` 的 R077 同样归零；差别只在于 A 组不再单独经历一轮上线验证，因此 **SIT H1 是本次交付不可妥协的核心验收项**。

### 11.2 交付后的状态

| 判定源 | 交付后状态 |
|---|---|
| S0 管理员锁定 | ✅ 可用 |
| **S1 Proxy 层 SQL 探测** | ✅ **可用，含 2 条已验证判据（PR001/PR002）+ 1 条可选（PR003）** |
| S2 ZK 管控面 | ✅ 可用（需先跑一次「ZK 自动发现」同步形态） |
| S3 人工声明 | ✅ 可用 |
| S4 全局默认 | ✅ 可用 |

**五个判定源全部可用。** 实测的最大收获是 S1 从"可能没有"变成了"可用且零依赖"——即使某个环境部署不了 ZK（zkCli / 网络不可达），仅凭一条已有连接就能得出与管控面等价的结论。

### 11.4 交付后的判定链路（以本次缺陷现场为例）

```
SIT-集中式实例A（Proxy :15002）
   ↓ S1 探测 PR001：/*proxy*/show status → 2 行
                     无 cluster、无 hash_range、set 行单值
                     → 命中「单 SET 拓扑」正面签名 → centralized
   ↓ S2 ZK：zk_instance_kind = noshard → centralized      （交叉一致 ✓）
   ↓ S3 声明：centralized                                  （交叉一致 ✓）
   ↓ 保守合并：三源一致 → centralized
   ↓ 引擎按集中式口径过滤 → 跳过 27 条仅分布式规则
   ↓ 【R077 不再出现】
```

对照 v1.5 的链路：探测恒判 distributed → 覆盖正确的声明 → 跑满 119 条 → R077 误报。**同一条链路上，只有"判据"这一环变了。**

### 11.3 后续动作

| # | 动作 | 责任 | 状态 |
|---|---|---|---|
| 1 | 按 `TEST-v1.5.1-…-G.md` 采集两类实例数据 | G | ✅ 已完成 2026-07-29 |
| 2 | 按 §8.4 评审候选判据，据实修订 §8.5 | A | ✅ 已完成，本次修订 |
| 3 | **一次性完成 A/B/C/D 四组开发** | **Q** | ⬜ **可开工** |
| 4 | 补交原始输出 / 完整 8 行 / `xa` 库确认（§8.7） | G | ⬜ 与开发并行，不阻塞 |
| 5 | 交付后 SIT（重点 H1 / H2 / H2b / H3 / H6b） | G | ⬜ 待 Q 交付 |

> **动作 4 不阻塞动作 3**：PR001/PR002/PR003 的判定逻辑已由现有数据充分支撑并经赤兔交叉核验，补充材料用于完善 `evidence` 与边界处理。
