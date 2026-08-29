# DESIGN-v1.6.3.0 深度诊断·表类型统计（G14）详细设计说明书

| 项 | 内容 |
|---|---|
| 文档编号 | DESIGN-v1.6.3.0 |
| 模块编号 | **G14 · 表类型统计**（深度诊断第 10 个子模块） |
| 目标版本 | v1.6.3.0（当前基线 v1.6.2.2，`VERSION` / `backend/config.py:APP_VERSION`） |
| 文档等级 | **照图施工级**——附录 A 给出全部新增/修改文件的逐行成品代码（已本地验证：32 项单测全通过），实施者不得二次设计 |
| 编写 | 智能体 A |
| 编写日期 | 2026-08-29 |
| 状态 | 设计与代码**已完成**；内网实测（§10）用于锚定形态与确认口径，GATE-1 仅两项可能触发微调 |
| 前置约束 | 本文档编写阶段**未修改任何代码**（用户要求）。仓库工作区在本文档提交时保持干净。 |

---

## 0. 阅读指引与本文档的三条硬约束

本文档同时承担三件事，读者请按角色取用：

* **实施者（智能体 Q 或人工）**：读 §5～§9 + 附录 A。附录 A 是**可直接落盘的成品代码**——四个新增文件 + 既有文件的 9 行改动 + 1 个前端块，逐字给出。
* **内网测试配合者**：只读 **§10**。§10 是一份不需要改任何代码、只在赤兔"在线SQL"页面或 `mysql` 客户端敲命令的实测脚本，共 12 个用例（T01～T12），每个用例都写明"敲什么、看什么、把什么填回来"。
* **评审者（智能体 O / Codex）**：读 §3（原厂口径的语义风险）、§7（决策与取舍）、§8（异常矩阵）、§9（爆炸半径）、§13（风险登记册）。

**三条硬约束**（贯穿全文，任何实现偏离即为不合格）：

1. **只读**。本模块对目标 TDSQL 实例只执行 `SHOW` / `SELECT information_schema`，不产生任何 DDL/DML，不修改任何会话级持久设置。
2. **零回归**。不得修改 119 条审核规则、解析引擎、既有 9 个深度诊断子模块的任何一行。修改面见 §9 清单，共计**新增 4 个文件 + 修改 5 个文件的 9 行**。
3. **口径可证伪**。统计结果必须自带交叉校验（§6.6），当原厂三条命令的并集与 `information_schema` 基线不一致时，产品必须**把差异摆出来**，而不是选一个数悄悄显示。本项目既有教训（V1.5 实例类型误判、v1.6.0.6 业务库枚举降级）都指向同一条：**漏掉的数据是不可见的错误**。

---

## 1. 需求

### 1.1 用户原话

> 我打算在"深度诊断"大模块下，增加一个"表类型统计"的子模块，主要的目的是用户想快速查询了解到某一个数据库的所有表一共多少张？单表多少张？广播表多少张？分片表多少张？

### 1.2 需求拆解

| 编号 | 需求 | 验收口径 |
|---|---|---|
| REQ-1 | 选定一个已登记实例，一键得到该实例下**所有业务库**的表类型统计 | 深度诊断页新增"表类型统计"页签，选实例→点按钮→出表格 |
| REQ-2 | 可只统计**指定单库** | 复用既有 `deepDb` 输入框语义（空=全部业务库） |
| REQ-3 | 输出四个数：**总表数 / 单表 / 广播表 / 分片表** | 汇总行 + 逐库明细行 |
| REQ-4 | **分布式实例**按原厂三条 `/*proxy*/` 命令口径统计 | §3.1 |
| REQ-5 | **集中式实例**按 `TABLE_TYPE='BASE TABLE'` 计入单表列，分片/广播恒 0，不统计视图 | §3.2 |
| REQ-6 | 结果可留档、可回看 | 落库两张表 + 历史列表接口 |
| REQ-7 | 受菜单权限矩阵管控 | 新增菜单键 `deep-diag-tabletype`，四内置角色默认可见 |

### 1.3 明确不做（本期范围外）

| 编号 | 不做的事 | 理由 |
|---|---|---|
| OUT-1 | 不做跨实例横向汇总 | 需求是"某一个数据库"，跨实例是另一个需求，且会引入 N×M 连接放大 |
| OUT-2 | 不做定时采集/趋势 | 无需求；且 `scheduler` 改动会扩大爆炸半径 |
| OUT-3 | 不接入 `retention_service` 自动清理 | 与既有 `index_audit` / `cluster_inspection` 一致（均未登记）。本表仅在人工点击时增长，量级为"次数 × 库数"，年增不足万行。登记在 §13 KL-3 |
| OUT-4 | 不导出 Excel/PDF | 无需求；前端表格自带浏览器复制能力 |
| OUT-5 | 不识别分区表 / 临时表 / 外部表等第四类 | 原厂口径只有三类，不自造分类 |

---

## 2. 现状勘查（代码事实，全部带行号，实施者可逐条复核）

> 本节所有结论均来自对当前 `main` 分支（`e481432`）的实读，不是推测。
> **行号锚定于 `e481432`。** 实施前请先 `git log --oneline -1` 核对；若 main 已前进，
> 用本节给出的**代码片段文本**（而非行号）重新定位——片段是稳定锚，行号是易腐锚。

### 2.1 深度诊断页的既有形态

| 事实 | 位置 |
|---|---|
| 侧边栏菜单项 `deep-diag` | `frontend/index.html:106` |
| 页面根节点 `<div v-if="currentPage==='deep-diag'">` | `frontend/index.html:1649` |
| 实例选择器 `deepConnId`（`savedConnections`） | `frontend/index.html:1653` |
| 页签容器 `<el-tabs v-model="deepTab" type="border-card">` | `frontend/index.html:1659` |
| 既有 9 个子页签 | `cluster` / `daily_inspect` / `index` / `diff` / `emergency` / `sqlstats` / `gateway_log` / `ppt_report` / `toolkit` |
| **样板页签**（本模块照抄其结构）："索引体检" | `frontend/index.html:1826-1839` |
| 前端状态声明 | `frontend/static/js/app.js:213-218` |
| 统一 POST 辅助函数 `_deepPost(key, url, payload)` | `frontend/static/js/app.js:777-787` |
| **样板方法** `runIndexAudit` | `frontend/static/js/app.js:792-795` |
| `setup()` 返回值总清单（新方法必须挂进去，否则模板取不到） | `frontend/static/js/app.js:1983` |

### 2.2 一个子模块需要登记的 4 个点（缺一即失效）

| # | 文件:行 | 内容 | 缺失后果 |
|---|---|---|---|
| P1 | `backend/services/auth_service.py:371-379` | API 前缀 → 菜单键映射 `_PATH_TO_MENU` | 写端点"未映射默认放行"（fail-open），且 `tests/test_rbac_path_coverage.py` **直接失败** |
| P2 | `backend/services/auth_service.py:491-494` | `ALL_MENU_KEYS` | 权限矩阵页看不到该菜单，无法配置 |
| P3 | `backend/services/auth_service.py:504-509` | `MENU_LABELS` | 权限矩阵页显示裸键名 |
| P4 | `backend/services/database.py:1685` | `_init_default_data` 的 `all_menus` | **致命**：`database.py:1743` 有 `DELETE FROM role_permissions WHERE menu_key NOT IN (...)`，未登记的键会在每次启动时被删掉，菜单永久不可见 |

补充事实（决定了本模块**不需要**写任何存量库订正 SQL）：
`database.py:1701-1704` 对 `all_menus × 内置角色`执行 `INSERT IGNORE INTO role_permissions(...) VALUES(...)`，`init_db()` 每次启动都会跑（`database.py:390`）。因此新键在**存量库**上会于下次启动自动补齐，`developer` / `auditor` 的默认不可见排除清单（`database.py:1696-1699`）不含本键 → 四个内置角色默认全部可见，符合 REQ-7。

### 2.3 `/*proxy*/` 前缀能否活着到达 Proxy —— **已由既有生产代码证实，非推测**

这是本设计最大的风险点，先结论后证据。

**结论：能。**

证据链：

1. `backend/services/tdsql_connector.py:280`
   ```python
   def _execute(self, sql: str, params: tuple = None) -> list[dict]:
       with self.get_connection() as conn:
           with conn.cursor() as cursor:
               cursor.execute(sql, params)
               return cursor.fetchall()
   ```
   驱动为 **PyMySQL**（`_create_connection`，`tdsql_connector.py:250`）。PyMySQL 的 `Cursor.execute()` 在 `args is None` 时**不做任何字符串改写**，原样把语句字节送入 `COM_QUERY`。PyMySQL 没有 `mysql` CLI 那种"默认剥离注释（需 `--comments` 才保留）"的行为。
2. 本项目**早已在生产路径上依赖这一点**：
   * `tdsql_connector.py:475` — `self._execute("/*proxy*/show status")`
   * `tdsql_connector.py:1227` — `self._execute("/*proxy*/show config")`
   * `instance_probe_rules.py:172` — 实例类型主判据 PR001 就是 `/*proxy*/show status`，并已于 2026-07-29 在真实内网双实例逐字核验通过。
   * `tdsql_connector.py:589-592` — 诊断采集清单含 4 条 `/*proxy*/` 命令。
3. `backend/engine/parser/pre_parser.py:27-44` 专门保护 `/*proxy*/` 不被审核引擎的注释清洗吃掉——说明该前缀在本项目是**一等公民**。

因此原厂"保留 `/*proxy*/` 注释，避免 MySQL 客户端删除命令前缀"这条要求，在本平台**天然满足**，不需要任何特殊处理。仍在 §10 安排 T03 做一次端到端确认（成本极低，收益是把这条从"推断"升格为"实测"）。

### 2.4 必须走 Proxy 端口 —— 事故记忆

`tdsql_connector.py:523-547` 记录了 V1.5 的事故复盘，两条结论对本模块直接适用：

> `/*proxy*/` 只是一个 SQL 注释。直连后端 TXSQL 执行该语句会返回完整的 458 行标准 MySQL 状态变量……
> 集中式实例的节点与分布式实例的分片节点，在【后端 TXSQL】的 SQL 层输出逐字一致。分片拓扑不是数据节点的属性——只有【经 Proxy 端口】才能看到差异。

推论（写进 §8 异常矩阵）：若连接配置指向的是后端 TXSQL 而非 Proxy，`show table with shardkey` 是**非法 MySQL 语法**，会返回 `ERROR 1064`。本模块把 1064 当作**"该实例不是分布式 Proxy 端点"的强信号**并显式提示，而不是当作普通失败吞掉。

### 2.5 业务库口径 —— 项目内已有两套，本模块取并集

| 来源 | 清单 |
|---|---|
| `index_audit_service.py:21` `_SYS` | `mysql`, `information_schema`, `performance_schema`, `sys`, `tdsqlpcloud`, `tdsqlpcloud_monitor`, `__tencentdb__` |
| `zk_scan_enrich_service.py:21` `SYSTEM_DATABASES` | `information_schema`, `mysql`, `performance_schema`, `sys`, `sysdb`, `query_rewrite`, `xa` |

两套不一致（前者缺 `sysdb`/`query_rewrite`/`xa`，后者缺 `tdsqlpcloud*`/`__tencentdb__`）。**这是既有缺陷，但本次不修既有代码**（约束 2）。本模块定义自己的 `_SYS_DB = 两者并集`，并由单测 `test_sys_db_is_superset` 钉住"必须同时是两者的超集"，使未来任一侧扩充时本模块不会落后。差异登记为 §13 KL-1，另案处理。

`sysdb` / `query_rewrite` / `xa` 的排除依据：`DESIGN-v1.6.0.8 §4`、`DESIGN-v1.6.1.1 §4`（TDSQL 实例默认管理库，非业务库）。其中 `xa` 恰是 PR004 判定分布式的正面证据（`instance_probe_rules.py:145`），但它不是业务库，必须排除。

### 2.6 实例类型解析

`backend/services/instance_type_service.py:95` `instance_type_service.resolve(connection_id) -> InstanceContext`。
* 优先级：S0 管理员锁定 > S1 Proxy 层 SQL 探测 > S2 ZK 管控面 > S3 人工声明。
* 300s 进程内缓存；任何异常回落全局默认（`resolve` 的 `except` 分支，`instance_type_service.py:112-114`）。
* `InstanceContext` 字段：`instance_type` / `source` / `conflict` / `declared` / `detected` / `zk` / `locked`。

本模块**直接复用**，不新造判定逻辑。`source` 与 `conflict` 会原样带进结果，供用户判断口径可信度（§6.7 W5）。

### 2.7 落库与迁移约定

* `backend/schema/loader.py` 扫描 `backend/schema/vN/NNN_*.sql`，按 `(version, sequence)` 升序执行。
* `backend/schema/migrator.py:41-66`：按 `sha256(文件内容)` 幂等；**逐行剔除以 `--` 开头的行**，再按 `;` 切分逐条执行；已应用的 key 跳过（checksum 变动只 WARNING 不重跑）。
* 现有最高版本目录：`v10/100_zk_scan_enrich.sql`。→ 本模块用 `v11/110_table_type_stats.sql`。
* **不动 `database.py::_create_all_tables`**：`init_db()` 在 `_create_all_tables` 之后就会调 `migrator.run_migrations()`（`database.py:379`），全新安装与存量升级都覆盖到。这样 `database.py` 只需改 P4 那 1 行。

### 2.8 元数据库访问

`backend/services/database.py::_get_connection()`，占位符风格为 `?`（见 `index_audit_service.py:189` 的 `VALUES (?,?,...)`），由该层统一改写。**本模块必须沿用 `?`，不得写 `%s`**。

---

## 3. 原厂统计逻辑解读 —— 逐条落到工程语义

### 3.1 原厂给定（用户转述，逐字保留）

```
统计逻辑
1、分布式实例逐业务库执行：
/*proxy*/show table with shardkey;          → 分片表
/*proxy*/show table with noshardkey_allset; → 广播表
/*proxy*/show table without shardkey;       → 单表
统计时：
保留 /*proxy*/ 注释，避免 MySQL 客户端删除命令前缀。
使用"数据库名 + 表名"去重。
汇总实例下所有业务库的数量。
2、集中式实例：
统计所有业务库中 TABLE_TYPE='BASE TABLE' 的数量。
数量写入单表列。
分片表和广播表写入 0。
不统计视图。
```

### 3.2 逐条翻译

| 原厂条款 | 工程语义 | 实现落点 |
|---|---|---|
| 逐业务库执行 | 命令无库限定语法，作用于**当前会话默认库**，故必须 `USE <db>`（或 `select_db`）后再执行 | §6.4 `_collect_distributed` |
| 保留 `/*proxy*/` | PyMySQL 不改写语句，天然满足（§2.3） | 常量直写，禁止任何 `strip`/`replace` |
| "数据库名 + 表名"去重 | 去重键 `(db_name, table_name)`；命令只返回裸表名，库名由遍历上下文提供 | §6.3 `_norm_key` |
| 汇总所有业务库 | 总数 = Σ 各库；**失败库不计入总数**（§7 ADR-5） | §6.5 |
| 集中式 `BASE TABLE` | `information_schema.TABLES WHERE TABLE_TYPE='BASE TABLE'`，天然排除 `VIEW` | §6.4 `_collect_centralized` |
| 分片/广播写 0 | 常量 0，不执行任何 `/*proxy*/` 命令 | 同上 |

### 3.3 **必须警惕的语义风险（本设计的核心技术判断）**

#### RISK-A：`without shardkey` 可能是 `noshardkey_allset` 的超集

字面看，"广播表"就是"没有 shardkey、但在所有 SET 上都有副本的表"。那么 `show table without shardkey` **在语义上完全可能同时返回广播表和单表**。若真如此，直接把 `without shardkey` 的行数当"单表数"，会把广播表**重复计一次**，导致：

```
总数 = 分片 + 广播 + 单表  →  比真实表数多出一个"广播表数"
```

这是一个**静默的、方向固定的高估**，且库越大错得越离谱。

原厂口径写的是"`without shardkey` → 单表"，可能是因为在他们的版本上两个集合确实互斥，也可能是原厂脚本本身就带这个偏差。**我没有真实 TDSQL，无法判定**。

**设计对策（无论实测结果如何都正确，不依赖 T04 的结论）**：
采集三个**表名集合**而不是三个**行数**，然后按优先级 `分片 > 广播 > 单表` 做**一次归一化去重**——同一个 `(db, table)` 只归入优先级最高的那一类。

* 若三集合本就互斥（原厂说法成立）→ 归一化是**恒等变换**，结果与原厂逐字一致，零偏差；
* 若 `without ⊇ noshardkey_allset` → 广播表被从"单表"里扣掉，总数恢复正确；
* 无论哪种，都在结果里输出 `overlap_count` 与 `KIND_OVERLAP` 告警，让使用者看见真相。

这一条是本设计相对"照抄原厂脚本"的**唯一实质性增强**，也是它必须存在的理由。T04 只用来**确认走的是哪个分支**，不改变代码。

#### RISK-B：三类之外可能还有第四类

`分片 ∪ 广播 ∪ 单表` 未必等于该库全部 `BASE TABLE`（可能有系统影子表、未完成 DDL 的中间表、原厂新版本引入的新类型）。

**设计对策**：始终同时采集 `information_schema` 基线（该库 `BASE TABLE` 数），与三类并集比对，不一致则输出 `RECON_MISMATCH` 告警，并列出**双向差集各前 20 个表名**（`only_in_proxy` / `only_in_information_schema`）。产品**不替用户裁决**哪个数对，两个数都摆出来。

#### RISK-C：返回结果的列形态未知

`show table with shardkey` 返回几列、列名叫什么（`Tables_in_xxx`? `table`? `table_name`? 是否附带 `shardkey` 列？是否返回 `db.table` 限定名？）——**未知**。

**设计对策**：`_extract_names()` 做**形态无关**解析（§6.3），并把实际列名原样记录进结果的 `shape` 字段与日志。T02 用来把真实形态填回文档附录 B，作为后续回归的锚点。

#### RISK-D：会话默认库切换污染连接池

`TDSQLConnectionPool` 是**线程本地长连接复用**（`tdsql_connector.py:265-279`）。在共享池连接上 `USE <db>` 会把默认库改掉并**留给下一个使用者**。项目里已有先例埋雷：`slow_enrich_service.py:219` 的 `conn.select_db(db)` 就没有恢复。

**设计对策（ADR-3）**：本模块**绝不在共享池连接上切库**。用 `dataclasses.replace(pool.config, database=...)` 克隆配置，**另建一个 `pool_size=1` 的临时 `TDSQLConnectionPool`**，全部切库操作发生在它自己的线程本地连接上，`finally` 里 `close_all()`。
* 复用既有 `TDSQLConnectionPool` 类，不新增任何连接代码；
* 对共享池零副作用，对既有模块零影响；
* 整个统计过程**只多开 1 条 TCP 连接**（不是每库一条）。

---

## 4. 总体设计

### 4.1 架构位置

```
前端 深度诊断页 › 页签「表类型统计」
   └─ POST /api/v1/table-type-stats/run   {connection_id, database}
        └─ backend/api/table_type_stats.py         （薄控制器，照抄 index_audit.py）
             └─ backend/services/table_type_stats_service.py   （全部逻辑）
                  ├─ instance_type_service.resolve()      → 分布式 / 集中式 分流
                  ├─ registry.get(conn_id) → TDSQLConnectionPool（共享池，只读用）
                  │     ├─ SHOW DATABASES                （业务库枚举）
                  │     └─ information_schema.TABLES     （基线 / 集中式口径）
                  ├─ 临时 TDSQLConnectionPool(pool_size=1)（仅分布式；隔离切库）
                  │     └─ 逐库 select_db + 3 条 /*proxy*/ 命令
                  └─ backend/services/database._get_connection()
                        └─ table_type_stat / table_type_stat_item 落库
```

### 4.2 数据流（分布式实例）

```
1. resolve 实例类型 ──► distributed
2. SHOW DATABASES ──► 过滤 _SYS_DB ──► dbs = [db1, db2, ...]
3. 一次性查 information_schema 全量名单（要名字，不只要计数）：
   SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE FROM information_schema.TABLES
   WHERE TABLE_SCHEMA IN (%s,%s,...)
        ──► baseline{db: {"base": {表名…}, "view": {视图名…}}}
4. 建临时池 tmp（pool_size=1）
5. for db in dbs:
     tmp.select_db(db)
     S = names(/*proxy*/show table with shardkey)
     B = names(/*proxy*/show table with noshardkey_allset)
     N = names(/*proxy*/show table without shardkey)
     ── 扣除视图（原厂"不统计视图"；若命令本就不返回视图，此步为恒等）──
     V = baseline[db]["view"] ;  S -= V ;  B -= V ;  N -= V
     ── 归一化去重（分片 > 广播 > 单表），见 RISK-A ──
     B' = B - S ;  N' = N - S - B
     overlap += |B∩S| + |N∩(S∪B)|
     union = S ∪ B' ∪ N'
     ── 交叉校验：与 baseline[db]["base"] 做双向集合差 ──
     if union != baseline[db]["base"]:
         记 RECON_MISMATCH（only_in_proxy / only_in_is 两侧表名各取前 20）
     item = {db, shard=|S|, broadcast=|B'|, single=|N'|, total=|union|}
6. tmp.close_all()
7. 汇总 + 告警 + 落库 + 返回
```

### 4.3 数据流（集中式实例）

```
1. resolve ──► centralized
2. SHOW DATABASES ──► 过滤 ──► dbs
3. 同一份 baseline（与分布式共用 _collect_baseline，TABLE_TYPE='BASE TABLE' 的名单）
4. item = {db, shard=0, broadcast=0, single=|base|, total=|base|}
   （VIEW 天然不在 base 集合里，"不统计视图"自动成立）
5. 不建临时池，不发任何 /*proxy*/ 命令
```

### 4.4 新增/修改文件总览

| 类型 | 文件 | 规模 |
|---|---|---|
| 新增 | `backend/services/table_type_stats_service.py` | 489 行（附录 A.1，成品） |
| 新增 | `backend/api/table_type_stats.py` | 44 行（附录 A.2，成品） |
| 新增 | `backend/schema/v11/110_table_type_stats.sql` | 37 行（附录 A.3，成品） |
| 新增 | `tests/test_table_type_stats.py` | 455 行（附录 A.4，成品） |
| 修改 | `backend/main.py` | **2 行**（import + include_router） |
| 修改 | `backend/services/auth_service.py` | **3 行**（P1/P2/P3） |
| 修改 | `backend/services/database.py` | **1 行**（P4） |
| 修改 | `frontend/index.html` | **1 处**新增 `<el-tab-pane>` 块（不改任何既有行） |
| 修改 | `frontend/static/js/app.js` | **3 行**（`deepResult` 加字段 / 新方法 / 返回清单追加） |

**合计：新增 4 文件，既有文件净改 9 行 + 1 个纯新增 HTML 块。**

---

## 5. 接口设计

### 5.1 `POST /api/v1/table-type-stats/run`

请求：
```json
{ "connection_id": "conn-xxx", "database": "" }
```
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `connection_id` | string | 否（空=默认连接） | 目标实例 |
| `database` | string | 否 | 空=全部业务库；非空=只统计该库（**仍会做 `_SYS_DB` 校验，系统库直接拒绝**） |

响应 200：
```json
{
  "stat_id": 12,
  "instance_type": "distributed",
  "type_source": "probe",
  "type_conflict": false,
  "database_count": 3,
  "total_tables": 128,
  "shard_tables": 90,
  "broadcast_tables": 8,
  "single_tables": 30,
  "failed_databases": 0,
  "overlap_count": 0,
  "items": [
    {"db_name":"db_order","total_tables":100,"shard_tables":80,
     "broadcast_tables":5,"single_tables":15,
     "baseline_tables":100,"status":"OK","detail":""}
  ],
  "warnings": [
    {"code":"RECON_MISMATCH","severity":"WARNING","db_name":"db_x",
     "detail":"三类并集 97 张，information_schema 基线 100 张；仅基线可见: t_a, t_b, t_c"}
  ],
  "shape": {
    "shard":     ["Tables_in_db_order"],
    "broadcast": ["Tables_in_db_order"],
    "single":    ["Tables_in_db_order"]
  }
}
```

错误：
| HTTP | detail | 触发条件 |
|---|---|---|
| 400 | `未连接TDSQL实例或连接不存在` | `ConnectionNotFoundError`（照抄 `index_audit.py:20-24`） |
| 400 | `不允许统计系统库: xxx` | `database` 落在 `_SYS_DB` |
| 500 | 原始异常字符串 | 其余（照抄样板） |

### 5.2 `GET /api/v1/table-type-stats/history?connection_id=&limit=20`

返回 `{"items":[{...table_type_stat 行...}]}`，按 `id DESC`。

### 5.3 `GET /api/v1/table-type-stats/detail/{stat_id}`

返回 `{"items":[{...table_type_stat_item 行...}], "warnings":[...]}`，`warnings` 由任务行 `warnings_json` 反序列化，解析失败返回 `[]`（不抛异常）。

### 5.4 权限

前缀 `/api/v1/table-type-stats` → 菜单键 `deep-diag-tabletype`（P1）。`/run` 是 POST 写端点，`tests/test_rbac_path_coverage.py::test_all_write_endpoints_are_mapped` 会强制校验此登记存在。

---

## 6. 详细设计 —— 服务层

> 成品代码见附录 A.1。本节说明**为什么这样写**，两者不一致时以附录 A.1 为准。

### 6.1 常量

```python
SQL_SHARD     = "/*proxy*/show table with shardkey"
SQL_BROADCAST = "/*proxy*/show table with noshardkey_allset"
SQL_SINGLE    = "/*proxy*/show table without shardkey"
```
**三条常量逐字来自原厂，禁止改写、禁止拼接、禁止加分号、禁止 `.strip()`。** 尾部分号在原厂文本里是行终止符，PyMySQL 单语句执行不需要它；加上反而在部分 Proxy 版本会触发 multi-statement 检查。

```python
KIND_SHARD, KIND_BROADCAST, KIND_SINGLE = "shard", "broadcast", "single"
_KIND_ORDER = (KIND_SHARD, KIND_BROADCAST, KIND_SINGLE)   # 归一化优先级，见 RISK-A
MAX_DIFF_SAMPLE = 20     # 差集样本上限，防止 detail 撑爆 VARCHAR(512)
MAX_DATABASES  = 500     # 与 zk_scan_enrich.ENRICH_MAX_INSTANCES 同量级的护栏
```

`_SYS_DB`：`index_audit_service._SYS` ∪ `zk_scan_enrich_service.SYSTEM_DATABASES`，硬编码为本模块自有 `frozenset`（**不 import 其他 service**，避免制造新的模块间耦合），由单测钉住超集关系。

### 6.2 业务库枚举 `list_business_databases(pool)`

```python
rows = pool._execute("SHOW DATABASES")
```
`SHOW DATABASES` 在 DictCursor 下列名为 `Database`（大小写随版本），故取值方式与 `zk_scan_enrich_service.py:76-79` 一致：`row.get("Database") or row.get("database")`，再兜底"取该行唯一值"。过滤 `name.lower() in _SYS_DB` 后按 `(lower, raw)` 排序返回。

超过 `MAX_DATABASES` 时截断并产生 `TOO_MANY_DATABASES` 告警——**截断必须可见**，不能悄悄少算。

### 6.3 形态无关的表名提取 `_extract_names(rows)`（应对 RISK-C）

返回 `(names:list[str], columns:list[str])`。

```
若 rows 为空          → ([], [])
columns = list(rows[0].keys())
选列 col：
  1) 只有 1 列        → 该列
  2) 有列名（小写）恰为 "table" / "table_name" / "tables"       → 该列
  3) 有列名（小写）以 "tables_in_" 开头                          → 该列
  4) 有列名（小写）含 "table" 且不含 "type"/"rows"/"schema"      → 第一个匹配列
  5) 以上都不满足     → 取第 1 列，并记 SHAPE_UNKNOWN 告警
逐行取值 → str() → strip() → 去首尾反引号 → 若含 '.' 取最后一段（去掉库限定）
       → 空串跳过
```
选列规则 2/3/4 的顺序是**从最确定到最宽松**，任何一步命中即停止，保证行为确定、可复现。`SHAPE_UNKNOWN` 是 WARNING 不是 ERROR：即使猜错列，用户看得见告警并能从 `shape` 字段看到真实列名，比直接失败更有用。

去库限定的理由：若某版本返回 `db_order.t_user`，取末段后与去重键 `(db, name)` 对齐；若返回裸名，末段就是它自己——两种形态都正确。

### 6.4 采集

#### `_collect_baseline(pool, dbs)` —— 两个分支共用

```sql
SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE FROM information_schema.TABLES
WHERE TABLE_SCHEMA IN (%s, %s, ...)
```
返回 `{db: {"base": set(表名), "view": set(视图名)}}`。

**取名字而不是取 COUNT** 的三个理由：
1. 集中式分支要的 `single = len(base)` 直接可得；
2. 分布式分支的交叉校验（§6.6）需要**双向差集的表名**，光有计数说不出"差在哪张表"；
3. "不统计视图"这条原厂要求，在分布式分支上只能靠 `view` 名单做扣除来落实——
   原厂没说三条 `/*proxy*/` 命令会不会返回视图（内网 T06 用来确认）。
   **先扣了，就不必等实测结论**：若命令本就不返回视图，扣除是恒等操作，零代价。

**占位符风格**：这是打到**目标 TDSQL 实例**的查询，走 `pool._execute`（PyMySQL），
占位符是 `%s`；元数据库那侧（`database._get_connection`）才是 `?`。
本模块两侧都有，**不得混用**（§8 E-9）。

内存代价：单实例 5 万张表 ≈ 5MB Python 字符串，可接受。

#### `_collect_centralized(dbs, baseline)`
纯内存换算，不发任何查询：`single = total = len(baseline[db]["base"])`，
`shard = broadcast = 0`。缺失的库计 0（空库合法）。

#### `_collect_distributed(pool, dbs)`
```python
cfg = dataclasses.replace(pool.config, database=(dbs[0] if dbs else pool.config.database))
tmp = TDSQLConnectionPool(cfg, pool_size=1)
try:
    with tmp.get_connection() as conn:
        for db in dbs:
            conn.select_db(db)              # 隔离连接，切库无副作用（ADR-3）
            for kind, sql in ((KIND_SHARD, SQL_SHARD), ...):
                with conn.cursor() as cur:
                    cur.execute(sql)
                    rows = cur.fetchall()
finally:
    tmp.close_all()
```
**逐库、逐命令独立 try**：单条失败只让**该库**降级为 `status='FAILED'`（三条必须全成功才算 OK），记录 errno + 消息前 200 字符，继续下一个库。理由见 ADR-5。

`select_db` 失败（库被删、无权限）→ 该库 `status='FAILED'`，`detail` 写明；不影响其余库。

**连接异常处理的关键细节**：`TDSQLConnectionPool.get_connection()` 的 `except` 分支（`tdsql_connector.py:298-306`）在**任何异常穿出 `with` 块时**会关闭并重建线程本地连接、然后重新抛出。因此本模块**必须在 `with` 块内部把每条命令的异常吃掉**，绝不让它穿出 `with`——否则一个库的 1064 会导致整条连接被重建、循环中断。这是照图施工的强制要求，附录 A.1 的 try 层次不得调整。

### 6.5 归一化与计数（应对 RISK-A）

先扣视图，再归一化：

```python
views  = baseline[db]["view"]
shard_names, broadcast_names, single_names = (
    shard_names - views, broadcast_names - views, single_names - views)

s_set = set(shard_names)
b_set = set(broadcast_names) - s_set
n_set = set(single_names) - s_set - set(broadcast_names)
overlap = (len(broadcast_names_unique & s_set)
           + len(single_names_unique & (s_set | broadcast_names_unique)))
union = s_set | b_set | n_set
item = {shard: len(s_set), broadcast: len(b_set),
        single: len(n_set), total: len(union)}
```
恒等式（实现必须满足，单测 `test_counts_are_consistent` 钉住）：
```
total_tables == shard_tables + broadcast_tables + single_tables    （逐库、且汇总行同样成立）
```
去重键为**精确大小写**的表名。理由：三个集合来自同一 Proxy 的同一会话，大小写必然一致；而 `lower_case_table_names=0` 的实例上强行小写会把两张不同的表合并成一张，造成**少算**。少算比多算危险（与项目"宁可多报不可漏报"一脉相承）。

### 6.6 交叉校验（应对 RISK-B）

对每个库，做 `union` 与 `baseline[db]["base"]` 的**双向集合差**（不是比数字）：
* 两侧差集都为空 → 无告警。
* 任一侧非空 → `RECON_MISMATCH`（WARNING），`detail` 形如：
  `三类并集 97 张，information_schema 基线 100 张；仅基线可见(3): t_a, t_b, t_c`
  两侧样本各取 `MAX_DIFF_SAMPLE=20` 个并按名排序，超出部分写 `…等 N 张`；
  `detail` 同时写入该库的 `table_type_stat_item.detail`（截断至 512 字节）。

比集合而不比计数：两个集合大小相同但内容不同（少了 A、多了 B）时，
比计数会漏报——这正是"不可见错误"的典型形态。

**为什么不直接以基线为准**：`information_schema` 在 Proxy 上的可见范围与 `/*proxy*/` 命令未必同源（V1.5 事故正是栽在"两个视角看到的东西不一样"）。产品的职责是把两个视角的差摆出来，不是替用户选一个。

集中式实例不做此校验（基线本身就是唯一数据源）。

### 6.7 告警清单（`warnings[]`）

| code | severity | 触发 | 用户该怎么办 |
|---|---|---|---|
| W1 `PROXY_CMD_FAILED` | ERROR | 某库三条命令中任一失败 | 看 `detail` 的 errno；1064→连接可能不是 Proxy 端口；1045/1142→授权不足 |
| W2 `KIND_OVERLAP` | WARNING | 三类集合有交集（RISK-A 命中） | 说明原厂"三类互斥"在本版本不成立，已按优先级去重，总数正确 |
| W3 `RECON_MISMATCH` | WARNING | 并集 ≠ 基线（RISK-B 命中） | 两个数都在 detail 里，人工判定 |
| W4 `SHAPE_UNKNOWN` | WARNING | 结果列形态未识别（RISK-C 命中） | 把 `shape` 字段贴给开发，扩充 `_extract_names` 规则表 |
| W5 `INSTANCE_TYPE_UNRELIABLE` | WARNING | `ctx.source == DEFAULT` 或 `ctx.conflict == True` | 实例类型是猜的/有冲突，统计口径可能整体走错分支；去实例管理页锁定类型后重跑 |
| W6 `NO_BUSINESS_DB` | INFO | 过滤后无业务库 | 账号权限过窄或实例确实空 |
| W7 `TOO_MANY_DATABASES` | WARNING | 库数 > 500，已截断 | 用 `database` 参数分批统计 |
| W8 `NOT_DISTRIBUTED_ENDPOINT` | ERROR | 所有库都因 errno 1064 失败 | 该连接大概率指向后端 TXSQL 而非 Proxy（§2.4） |

W5 的必要性：`resolve()` 在异常时**静默回落全局默认**（`instance_type_service.py:112-114`）。如果一个真分布式实例被回落成"集中式"，本模块会走 `information_schema` 分支，**分片表和广播表全报 0，而且看不出错**。必须把这个不确定性顶到 UI 上。

### 6.8 落库 `run_stats(...)`

照抄 `index_audit_service.run_audit`（`index_audit_service.py:178-203`）的事务形状：
```python
conn = _get_connection()
try:
    cur = conn.execute("INSERT INTO table_type_stat (...) VALUES (?,?,...)", (...))
    stat_id = cur.lastrowid
    for it in items:
        conn.execute("INSERT INTO table_type_stat_item (...) VALUES (?,?,...)", (...))
    conn.commit()
finally:
    conn.close()
```
`warnings_json = json.dumps(warnings, ensure_ascii=False)`。
**落库失败不得吞掉分析结果**：若 `INSERT` 抛异常，接口返回 500 且日志含完整栈——不做"落库失败但假装成功"的降级，因为 REQ-6 要求留档。

---

## 7. 关键决策记录（ADR）

| # | 决策 | 备选 | 选择理由 |
|---|---|---|---|
| ADR-1 | 采集**表名集合**而非行数 | 直接 `len(rows)` 三个数相加 | 行数无法去重、无法做 RISK-A 归一化、无法做 RISK-B 校验。集合的内存代价：单库 1 万张表 ≈ 1MB，可接受 |
| ADR-2 | 归一化优先级 `分片 > 广播 > 单表` | 报错 / 取原厂字面值 | 分片表是三者中语义最强、最不可能被"顺带"列进其他集合的一类；把它放最高优先级，任何重叠都不会让分片数失真。且该顺序与原厂命令的书写顺序一致 |
| ADR-3 | 另建 `pool_size=1` 临时池切库 | ① 共享池上 `select_db` 后恢复 ② 每库新建连接 | ①有残留风险（`pool.config.database` 为空时 PyMySQL 无"取消默认库"操作，恢复不彻底）；②50 库=50 次握手。临时池：1 次握手、零共享状态、复用既有类零新代码 |
| ADR-4 | 集中式**不发** `/*proxy*/` 命令 | 也发一遍看看 | 原厂已明确集中式口径；多发只会在 Proxy 上制造 1064 噪声并拖慢响应 |
| ADR-5 | 失败库**不计入**总数，且单列 `failed_databases` | 失败库按 0 计入 | 按 0 计入 = 用一个确定错误的数冒充事实。项目既有教训（v1.6.0.6 `proxy_show_partial` 降级标记）明确要求"部分成功必须显式标记" |
| ADR-6 | 走 schema 迁移文件而非 `_create_all_tables` | 加进 `database.py` 的大列表 | 迁移文件是 v1.2+ 的既定约定，且让 `database.py` 的改动从"新增 2 段 DDL"降到"1 行菜单键"，爆炸半径最小 |
| ADR-7 | 不接 `retention_service` | 登记 `table_type_stat` | 与同层级的 `index_audit` / `cluster_inspection` 保持一致；且父子表无 FK 级联，单独登记父表会造成"任务没了、明细还在"的孤儿（`retention_service.py:30-33` 已就 `inspection_results` 记过同类教训）。登记为 KL-3 |
| ADR-8 | `_SYS_DB` 本模块自持，不 import 其他 service | `from zk_scan_enrich_service import SYSTEM_DATABASES` | 跨 service import 会让"ZK 发现"的变更意外影响"深度诊断"。用单测钉超集关系，既解耦又不漏 |
| ADR-9 | 去重键用精确大小写 | 统一小写 | 小写会在 `lower_case_table_names=0` 时把不同表合并 → **少算**。少算是不可见错误 |
| ADR-10 | 命令常量不加分号、不做任何字符串处理 | 拼 `;` / `.strip()` | 原厂逐字口径；且 `pre_parser.py` 的既有实现证明本项目对 `/*proxy*/` 采取"原样保护"策略 |

---

## 8. 异常与边界矩阵

| # | 场景 | 期望行为 | 落点 |
|---|---|---|---|
| E-1 | `connection_id` 不存在/未连接 | HTTP 400 `未连接TDSQL实例或连接不存在` | `api/table_type_stats.py::_pool` |
| E-2 | `database` 指定为系统库 | HTTP 400 `不允许统计系统库: xxx` | `run_stats` 入口校验 |
| E-3 | `database` 指定的库不存在 | 该库 `status='FAILED'`，detail 含 errno 1049，`failed_databases=1`，HTTP 200 | `_collect_distributed` |
| E-4 | `SHOW DATABASES` 失败 | 抛出 → HTTP 500（无库可统计，继续没有意义） | `list_business_databases` |
| E-5 | 某库三条命令之一 1064 | 该库 FAILED + W1；若**所有**库都是 1064 → 追加 W8 | `_collect_distributed` / `analyze` |
| E-6 | 某库权限不足（1045/1142/1044） | 该库 FAILED + W1，detail 提示"授权不足" | 同上 |
| E-7 | 业务库为 0 | HTTP 200，全 0，W6 | `analyze` |
| E-8 | 空库（有库无表） | 该库全 0，`status='OK'`，**不产生任何告警** | 正常路径 |
| E-9 | 占位符风格混用 | 目标实例侧 `%s`（PyMySQL）、元数据库侧 `?`。本模块目标侧无参数化查询，元数据库侧全 `?` | 附录 A.1 |
| E-10 | 命令返回 0 行 | 视为该类 0 张，合法，不告警 | `_extract_names` 返回 `([], [])` |
| E-11 | 库名含特殊字符 | 用 `conn.select_db(db)`（驱动层转义），**不拼 `USE \`{db}\``** | ADR-3 |
| E-12 | 统计过程中连接断开 | `finally: tmp.close_all()` 保证释放；已完成的库结果保留并返回 | `_collect_distributed` |
| E-13 | 实例类型解析异常 | `resolve` 自身回落全局默认，本模块检测 `source==DEFAULT` → W5 | `analyze` |
| E-14 | 库数 > 500 | 截断 + W7 | `list_business_databases` |
| E-15 | 元数据库落库失败 | HTTP 500，不返回半成品 | `run_stats` |
| E-16 | `stat_id` 不存在 | `/detail/{id}` 返回 `{"items":[],"warnings":[]}`，HTTP 200 | `get_detail` |
| E-17 | `warnings_json` 损坏 | `json.loads` 失败 → 返回 `[]`，不抛异常 | `get_detail` |

---

## 9. 爆炸半径分析与最小化修改清单

### 9.1 逐文件影响评估

| 文件 | 改动 | 影响面 | 风险 |
|---|---|---|---|
| `backend/services/table_type_stats_service.py` | 全新 | 无人引用 | **零** |
| `backend/api/table_type_stats.py` | 全新 | 新路由前缀，与现有 25 个前缀无重叠 | **零** |
| `backend/schema/v11/110_table_type_stats.sql` | 全新 | 两张新表，`CREATE TABLE IF NOT EXISTS` 幂等 | **零** |
| `tests/test_table_type_stats.py` | 全新 | 仅测试 | **零** |
| `backend/main.py` | +2 行（第 40 行附近 import、第 176 行附近 include_router） | 路由表新增 3 条 | **极低**。`tests/test_app_routes_integrity.py` 会验证路由完整性 |
| `backend/services/auth_service.py` | +3 行（P1/P2/P3，均为字典/列表新增条目） | 权限判定 | **极低**。新增映射不改变任何既有前缀的判定；`test_rbac_path_coverage.py` 会验证 |
| `backend/services/database.py` | +1 行（`all_menus` 追加） | `role_permissions` 表新增 4 行（每角色 1 行） | **极低**。`INSERT IGNORE` 幂等；`DELETE ... NOT IN` 只删不在清单里的键，追加只会**保留**更多 |
| `frontend/index.html` | 新增一个 `<el-tab-pane>` 块 | Vue 模板 | **极低**。插在"索引体检"页签之后、"结构比对"之前；不修改任何既有行 |
| `frontend/static/js/app.js` | +3 行 | `deepResult` 多一个 key；新增一个方法；返回清单追加一个名字 | **极低**。`deepResult` 是 `reactive`，新增 key 不影响既有 key |

### 9.2 明确**不碰**的清单（评审时逐条核对 `git diff --stat`）

* `backend/engine/**`（解析器、119 条规则）——**一个字节都不改**
* `backend/services/audit_service.py` / `rule_*` / `scan_*`
* `backend/services/tdsql_connector.py`——**只使用，不修改**（`TDSQLConnectionPool` / `TDSQLConnectionConfig` 均为现成公开构造）
* `backend/services/instance_type_service.py`——只调 `resolve()`
* `backend/services/connection_registry.py`——只调 `registry.get()`
* `backend/services/retention_service.py`（ADR-7）
* `backend/services/scheduler.py`（OUT-2）
* `backend/services/index_audit_service.py`、`zk_scan_enrich_service.py`——**不修改其 `_SYS` / `SYSTEM_DATABASES`**（ADR-8，差异登记 KL-1）
* 既有 9 个深度诊断子模块的 service / api / 前端块

### 9.3 回归验证清单（合入前必须全绿）

```bash
# 1. 全量既有测试
python -m pytest tests/ -q

# 2. 重点：权限与路由完整性
python -m pytest tests/test_rbac_path_coverage.py tests/test_app_routes_integrity.py -q

# 3. 重点：119 条规则未受影响
python -m pytest tests/test_rules.py tests/test_sit_rules.py tests/test_sit_v1_rules.py -q

# 4. 新模块自测
python -m pytest tests/test_table_type_stats.py -q

# 5. 改动面核对——期望：新增 4 文件，既有文件净增 9 行 + 1 个 HTML 块
git diff --stat
```

---

## 10. 内网实测计划（**不动任何代码**）

> 致内网配合的智能体/同事：**本节全部操作都是只读查询，不需要改代码、不需要重启服务、不需要建表。**
> 执行位置：**赤兔控制台 › 实例管理 › 在线SQL**（或用 `mysql` 客户端连**实例的 Proxy 端口**，与平台"实例管理"里登记的 host:port 完全一致）。
> **如果用 `mysql` 命令行客户端，必须加 `--comments` 参数**，否则客户端会把 `/*proxy*/` 前缀吃掉：
> ```
> mysql --comments -h <proxy_host> -P <proxy_port> -u <user> -p
> ```
> 赤兔"在线SQL"页面不需要这个参数。
>
> **回填方式**：每个用例都写明了"回填项"。请把**原始输出整段贴回来**（不要摘要、不要改写、不要脱敏表名以外的内容），我按 §10.14 的映射表决定是否放行开发。

### 10.0 准备

请先确认两台样本实例，并记录：

| 项 | 分布式样本 | 集中式样本 |
|---|---|---|
| 实例名 | | |
| Proxy host:port | | |
| 平台"实例管理"里该实例显示的**实例类型**与**来源** | | |
| 用于测试的账号 | | |
| 该实例的业务库清单 | | |

> 口令不用告诉我，也不用写进回填内容。

---

### T01 · 业务库枚举一致性

**在分布式样本上执行：**
```sql
SHOW DATABASES;
```

**回填**：完整库名列表。

**我要看什么**：确认哪些库属于系统库。本模块将排除以下 10 个（不区分大小写）：
`information_schema`、`mysql`、`performance_schema`、`sys`、`sysdb`、`query_rewrite`、`xa`、`tdsqlpcloud`、`tdsqlpcloud_monitor`、`__tencentdb__`。

**如果列表里出现了不在上面 10 个之内、但你认为不是业务库的库名，请特别标注出来**——这会直接改 §6.1 的 `_SYS_DB` 常量。

---

### T02 · 三条命令的返回形态（**最关键，决定解析规则**）

**在分布式样本上，挑一个有表的业务库执行：**
```sql
USE <业务库名>;

/*proxy*/show table with shardkey;
/*proxy*/show table with noshardkey_allset;
/*proxy*/show table without shardkey;
```

**回填**（三条各一份，缺一不可）：
1. **完整的列头**（有几列、每列叫什么名字，逐字抄，注意大小写）；
2. **前 5 行数据原样**；
3. **总行数**。

**我要看什么**：
* 表名在第几列、列名是什么（是 `Tables_in_xxx`？`table`？`table_name`？还是别的）；
* 表名是裸名（`t_user`）还是带库限定（`db_order.t_user`）；
* 有没有附带 `shardkey` 之类的第二列。

这三份输出会原样进本文档附录 B，成为 `_extract_names()` 的实测锚点。

---

### T03 · `/*proxy*/` 前缀存活确认

**在分布式样本上执行（注意：**故意**去掉前缀）：**
```sql
show table with shardkey;
```

**回填**：报错内容（预期是 `ERROR 1064` 语法错误），或者——如果它居然成功了，请把输出也贴回来。

**我要看什么**：确认 `/*proxy*/` 前缀是**必需的**。这条用来反证 T02 的结果确实是 Proxy 处理的、而不是后端 MySQL 碰巧支持。

---

### T04 · 三类集合是否互斥（**决定 RISK-A 走哪个分支**）

**在同一个库上，把 T02 的三份表名列表做集合运算。** 最省事的做法是把三份输出各存成一个文本文件，然后：

```bash
# 假设已把三份表名（只留表名那一列，每行一个）存成 shard.txt / broadcast.txt / single.txt
sort -u shard.txt     > s.txt
sort -u broadcast.txt > b.txt
sort -u single.txt    > n.txt

echo "== 分片∩广播 =="; comm -12 s.txt b.txt
echo "== 分片∩单表 =="; comm -12 s.txt n.txt
echo "== 广播∩单表 =="; comm -12 b.txt n.txt
echo "== 三类去重并集行数 =="; cat s.txt b.txt n.txt | sort -u | wc -l
echo "== 各自行数 =="; wc -l s.txt b.txt n.txt
```

**回填**：上面 5 个 echo 段落的全部输出。

**我要看什么**：
* 三个交集是否**都为空**。若 `广播∩单表` 非空 → **RISK-A 成立**，原厂"`without shardkey` → 单表"是超集口径，本设计的归一化去重（§6.5）会生效，届时产品会显示 `KIND_OVERLAP` 告警——**那是正确行为，不是 bug**。
* 并集行数 vs 各自行数之和，差值即重叠量。

---

### T05 · 与 `information_schema` 基线交叉校验（**决定 RISK-B**）

**同一个库上执行：**
```sql
SELECT COUNT(*) AS base_tables
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = '<业务库名>' AND TABLE_TYPE = 'BASE TABLE';

SELECT COUNT(*) AS views
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = '<业务库名>' AND TABLE_TYPE = 'VIEW';
```

**回填**：两个数字。

**我要看什么**：`base_tables` 是否等于 T04 的"三类去重并集行数"。
* 相等 → RISK-B 不成立，皆大欢喜；
* 不等 → 请再执行一次拿到差集明细：
  ```sql
  SELECT TABLE_NAME FROM information_schema.TABLES
  WHERE TABLE_SCHEMA = '<业务库名>' AND TABLE_TYPE = 'BASE TABLE'
  ORDER BY TABLE_NAME;
  ```
  把它和 T04 的并集比一比，**回填两侧的差集表名**。这决定产品要不要长期显示 `RECON_MISMATCH`。

---

### T06 · 视图是否混入

**在有视图的库上（若没有视图，本用例可跳过并注明"无视图库"）：**
```sql
USE <含视图的业务库>;
SHOW FULL TABLES;                       -- 看哪些是 VIEW
/*proxy*/show table without shardkey;   -- 看视图有没有出现在里面
```

**回填**：`SHOW FULL TABLES` 中 `Table_type='VIEW'` 的名字，以及它们是否出现在 `without shardkey` 的输出里。

**我要看什么**：原厂集中式口径明确"不统计视图"，但**没说**分布式的三条命令会不会带视图。若带，需要在 §6.4 增加一步"用 `information_schema` 的 VIEW 名单做扣除"。

---

### T07 · 集中式实例上执行这三条命令会怎样

**在集中式样本上执行：**
```sql
USE <业务库名>;
/*proxy*/show table with shardkey;
```

**回填**：报错内容（含 errno），或输出。

**我要看什么**：确认集中式分支"完全不发这三条命令"（ADR-4）是对的。同时验证 §8 的 E-5：如果这里返回 1064，说明 1064 确实是"非分布式端点"的可靠信号（对应告警 W8）。

---

### T08 · 集中式口径核对

**在集中式样本上执行：**
```sql
SELECT TABLE_SCHEMA, COUNT(*) AS n
FROM information_schema.TABLES
WHERE TABLE_TYPE = 'BASE TABLE'
GROUP BY TABLE_SCHEMA
ORDER BY TABLE_SCHEMA;
```

**回填**：完整结果。

**我要看什么**：确认这条 SQL（本模块集中式分支唯一的查询）在真实实例上能跑通、能正确按库分组、并且系统库确实出现在结果里（从而验证 §6.4 的"按业务库白名单取用"这一步是必需的）。

---

### T09 · 权限最小面

**用平台"实例管理"里**实际登记的那个账号**（不是 root）执行 T02 的三条命令。**

**回填**：能否成功；若失败，errno 和消息。

**我要看什么**：如果登记账号跑不了 `/*proxy*/show table ...`，那这个功能上线即报错。此时需要在文档里写明所需授权，并由 DBA 补授权——**不是改代码能解决的**。

---

### T10 · 性能取样

**挑实例上**表最多的那个库**执行：**
```sql
USE <最大的业务库>;
-- 逐条计时（赤兔在线SQL页面通常直接显示耗时；命令行可用 \T 或看 Query OK 的时间）
/*proxy*/show table with shardkey;
/*proxy*/show table with noshardkey_allset;
/*proxy*/show table without shardkey;
```

**回填**：该库的表数量 + 三条命令各自耗时。

**我要看什么**：单库三条命令的耗时 × 库数 = 本功能的总响应时间。若单库超过 1 秒、库数又多，需要在 UI 上加"预计耗时"提示或改成异步任务——**这会改设计，所以必须在开发前测**。

---

### T11 · 会话默认库的影响

**在分布式样本上执行：**
```sql
USE <库A>;
/*proxy*/show table with shardkey;    -- 记下行数 NA

USE <库B>;
/*proxy*/show table with shardkey;    -- 记下行数 NB
```
再试试（**如果报错，把错误贴回来即可，这是探测性用例**）：
```sql
/*proxy*/show table with shardkey from <库A>;
```

**回填**：NA、NB 是否不同；`from <库A>` 语法是否被支持。

**我要看什么**：
* 确认命令确实作用于**当前默认库**（这是 §6.4 逐库 `select_db` 的前提）；
* 如果 `from <db>` 被支持，可以在未来优化掉 `select_db` 循环——但**本期不用**，因为它没有官方文档背书。

---

### T12 · 单分片分布式实例（若有）

如果内网存在**只有 1 个 SET 的分布式实例**，请在其上执行 T02 三条命令并回填。

**我要看什么**：`instance_probe_rules._decide_proxy_status` 的已知边界——"单分片分布式实例若不输出 cluster 行会被判集中式"（`instance_probe_rules.py:99-104`）。这类实例会走本模块的集中式分支，导致分片表被报成单表。若内网确有此形态，需要在 UI 上强化 W5 提示。**没有这类实例就注明"无"。**

---

### 10.13 汇总回填模板

请按此格式回填（缺项写"未测"或"不适用"）：

```
【T01】SHOW DATABASES 输出：
【T01】非上述 10 个系统库、但不属于业务库的库名：
【T02-shard】列头 / 前5行 / 总行数：
【T02-broadcast】列头 / 前5行 / 总行数：
【T02-single】列头 / 前5行 / 总行数：
【T03】去掉 /*proxy*/ 后的结果：
【T04】三个交集 / 并集行数 / 各自行数：
【T05】base_tables= , views= , 与 T04 并集是否相等：
【T05】（不等时）双向差集表名：
【T06】视图是否混入：
【T07】集中式上执行的结果：
【T08】集中式分组统计输出：
【T09】登记账号能否执行：
【T10】最大库表数 / 三条命令耗时：
【T11】NA= , NB= , from <db> 是否支持：
【T12】单分片实例：有/无，若有则输出：
```

### 10.14 GATE-1 放行判据（实测结论 → 设计动作）

| 实测结论 | 设计动作 | 是否阻断开发 |
|---|---|---|
| T02 表名在唯一列 / `Tables_in_*` / `table*` 列 | `_extract_names` 规则 1-4 命中，无需改 | 否 |
| T02 出现完全预料外的列形态 | **必须**把实际列名补进 `_extract_names` 规则表 | **是**（改附录 A.1 常量表，1 处） |
| T02 返回库限定名 `db.tbl` | 已被"取末段"覆盖，无需改 | 否 |
| T03 无前缀报 1064 | 确认 `/*proxy*/` 必需，无需改 | 否 |
| T04 三集合互斥 | 归一化为恒等变换，W2 永不触发 | 否 |
| T04 广播∩单表 非空（RISK-A 成立） | 归一化生效，W2 会显示——**符合设计预期** | 否 |
| T05 并集 == 基线 | W3 永不触发 | 否 |
| T05 不等（RISK-B 成立） | W3 生效并列差集——**符合设计预期** | 否 |
| T06 视图混入 `without shardkey` | 设计已无条件扣除 VIEW 名单（§6.4），无需改；实测只用于确认扣除是否真的生效 | 否 |
| T07 集中式返回 1064 | 确认 W8 判据可靠 | 否 |
| T09 登记账号无权限 | 出授权说明文档，由 DBA 补授权 | **是**（非代码问题） |
| T10 单库 > 1s 且库数 > 20 | 需追加"异步任务 + 进度"设计 | **是**（设计升版） |
| T12 存在单分片分布式实例 | UI 强化 W5 文案 | 否（前端 1 行文案） |

**只要没有命中"是"，开发即可按附录 A 照图施工。**

---

## 11. 测试设计（开发期，可在本地 MariaDB 13306 上跑）

新增 `tests/test_table_type_stats.py`，全部**不依赖真实 TDSQL**：

| 用例 | 验证 | 手法 |
|---|---|---|
| `test_sys_db_is_superset` | `_SYS_DB` 是 `index_audit_service._SYS` 与 `zk_scan_enrich_service.SYSTEM_DATABASES` 的超集 | 直接集合比较（ADR-8 的护栏） |
| `test_sql_constants_verbatim` | 三条命令常量**逐字**等于原厂文本，且以 `/*proxy*/` 开头、不含分号 | 字符串断言（ADR-10 的护栏） |
| `test_extract_names_shapes` | 6 种伪造形态：单列 / `Tables_in_x` / `table_name` / 两列带 shardkey / 库限定名 / 反引号包裹 | 纯函数测试 |
| `test_extract_names_unknown_shape` | 完全不认识的列 → 取第 1 列且返回 `SHAPE_UNKNOWN` 信号 | 纯函数测试 |
| `test_normalize_disjoint` | 三集合互斥时归一化为恒等，`overlap==0` | 纯函数测试 |
| `test_normalize_overlap` | `广播 ⊂ 单表` 时，单表数被正确扣减，`total` 不重复计数 | 纯函数测试（RISK-A 的护栏） |
| `test_counts_are_consistent` | 随机 200 组集合，恒等式 `total == shard+broadcast+single` 恒成立 | 属性测试 |
| `test_centralized_branch` | 用 FakePool 返回固定 `information_schema` 行 → 分片/广播恒 0，且**未发出任何 `/*proxy*/` 命令** | FakePool 记录所有执行过的 SQL 并断言 |
| `test_distributed_partial_failure` | FakePool 让第 2 个库抛异常 → 该库 `FAILED`、不计入总数、`failed_databases==1`、其余库正常 | ADR-5 的护栏 |
| `test_recon_mismatch_warning` | 并集 ≠ 基线 → 产生 `RECON_MISMATCH` 且 detail 含两个数字 | RISK-B 的护栏 |
| `test_shared_pool_not_mutated` | 断言 `analyze()` 全程未在共享 pool 的连接上调用 `select_db` | ADR-3 的护栏 |
| `test_run_stats_persists` | 落库后 `get_detail(stat_id)` 行数 == `len(items)`，`warnings` 可反序列化 | 需本地 MariaDB（照抄 `test_index_audit.py` 的 skipif 形态） |
| `test_reject_system_database` | `database='mysql'` → 抛出，API 层转 400 | E-2 |

**FakePool 设计**（关键，使全部分布式逻辑可离线测试）：
```python
class FakePool:
    def __init__(self, script): self.config = TDSQLConnectionConfig(...); self.script = script; self.seen = []
    def _execute(self, sql, params=None): self.seen.append(sql); return self.script[sql]
```
临时池的构造在服务层用一个模块级钩子 `_new_pool = TDSQLConnectionPool`，测试里 monkeypatch 该名字即可注入 FakePool——**这是唯一为可测性做的让步，成本 1 行**。

---

## 12. 验收清单

### 12.1 功能

- [ ] 深度诊断页出现"表类型统计"页签，位于"索引体检"与"结构比对"之间
- [ ] 未选实例时按钮禁用；选实例后可点击
- [ ] 分布式实例：返回逐库 4 个数 + 汇总行，`total == shard+broadcast+single`
- [ ] 集中式实例：分片列、广播列全 0，单表列 == `information_schema` `BASE TABLE` 数
- [ ] 指定 `database` 时只统计该库
- [ ] 指定系统库时返回 400 且提示明确
- [ ] 告警区在触发时可见（至少构造 W5 / W6 两种验证）
- [ ] 历史列表与明细接口可回看

### 12.2 权限

- [ ] `admin` / `dba` / `developer` / `auditor` 四角色默认可见该页签
- [ ] 权限矩阵页出现"深度诊断-表类型统计"条目，可勾选/取消
- [ ] 取消勾选后该角色刷新页面看不到页签，且调 `/run` 被拒
- [ ] `pytest tests/test_rbac_path_coverage.py` 通过

### 12.3 零回归

- [ ] `pytest tests/` 全绿，且**通过用例数不少于改动前**
- [ ] `git diff --stat` 与 §4.4 表格逐行一致（新增 4 文件；`main.py` +2、`auth_service.py` +3、`database.py` +1、`app.js` +3、`index.html` 仅新增块）
- [ ] `backend/engine/` 目录 `git diff` 为空
- [ ] 既有 9 个深度诊断子页签功能不变（逐个点一遍）

---

## 13. 已知限制与风险登记册

| # | 项 | 说明 | 处置 |
|---|---|---|---|
| KL-1 | 项目内两套系统库清单不一致 | `index_audit_service._SYS` 缺 `sysdb`/`query_rewrite`/`xa`；`zk_scan_enrich_service.SYSTEM_DATABASES` 缺 `tdsqlpcloud*`/`__tencentdb__` | 本模块取并集自持；**既有两处不动**（约束 2），另案统一 |
| KL-2 | `slow_enrich_service.py:219` 在共享池连接上 `select_db` 后未恢复 | 既有潜在污染 | 本模块不重蹈（ADR-3）；既有代码本次不动，另案 |
| KL-3 | 本模块两张表未接 `retention_service` | 仅人工触发时增长，年增 < 1 万行 | 与 `index_audit` 一致；若未来接入需同时补 FK 级联 |
| KL-4 | 单分片分布式实例可能被判为集中式 | `instance_probe_rules.py:99-104` 的已知边界 | W5 告警提示 + 实例管理页可手工锁定类型；T12 确认内网是否存在此形态 |
| KL-5 | 三条 `/*proxy*/` 命令无官方语法文档背书 | 来源为原厂口头提供 | T02/T03/T11 实测锚定；实测输出入附录 B 作为回归基线 |
| KL-6 | 统计为同步执行 | 库数 × 3 条命令，大实例可能较慢 | T10 定量；若超阈值则升版为异步任务（GATE-1 阻断项） |
| KL-7 | 结果为快照，不反映采集期间的 DDL 变更 | 无事务一致性保证 | 结果带 `created_at`，UI 标注"采集时刻快照" |

---

## 14. 附录 A · 成品代码（照图施工）

> **本附录的四个文件已在本地环境完整验证**：
> `python -m pytest`（用 importlib 把 A.1 挂载为 `backend.services.table_type_stats_service`，
> 仓库代码零改动）**32 项全部通过**，其中含对本地 MariaDB(13306) 的真实落库用例；
> A.2 的路由在 FastAPI 下正确注册出 3 条路径。
> 实施者可直接落盘，不需要二次设计。
>
> 唯一可能随内网实测调整的是 A.1 的 `_EXACT_NAME_COLS` / `_PREFIX_NAME_COLS` /
> `_EXCLUDE_TOKENS` 三张常量表（§10.14 GATE-1 第 2 行）。其余部分与实测结论无关。

### A.1 `backend/services/table_type_stats_service.py`（新增）

```python
# -*- coding: utf-8 -*-
"""G14 · 表类型统计（深度诊断子模块，DESIGN-v1.6.3.0）

按 TDSQL 原厂口径统计单个实例下各业务库的表类型分布：

  分布式实例——逐业务库执行三条 Proxy 命令：
      /*proxy*/show table with shardkey           → 分片表
      /*proxy*/show table with noshardkey_allset  → 广播表
      /*proxy*/show table without shardkey        → 单表
  集中式实例——information_schema.TABLES 中 TABLE_TYPE='BASE TABLE' 计入单表，
      分片表/广播表恒为 0，视图不计。

设计要点（详见 DESIGN-v1.6.3.0）：
  · /*proxy*/ 前缀逐字保留。PyMySQL 在 args is None 时不改写语句，
    项目既有 /*proxy*/show status 生产路径已证实其可达 Proxy（§2.3）。
  · 三类结果集按 分片>广播>单表 归一化去重，使"without shardkey 是否为
    noshardkey_allset 超集"这一未知不影响总数正确性（§6.5 / RISK-A）。
  · 始终与 information_schema 做双向集合差交叉校验，差异显式告警（§6.6 / RISK-B）。
  · 绝不在共享连接池上切库；另建 pool_size=1 的临时池（ADR-3 / RISK-D）。

全部只读。不修改任何既有模块。
"""
from __future__ import annotations

import dataclasses
import json
import logging
from typing import Optional

from backend.services.database import _get_connection
from backend.services.tdsql_connector import TDSQLConnectionPool

logger = logging.getLogger("tdsql.table_type_stats")

# ── 原厂命令常量。逐字保留：禁止改写 / 拼接 / 加分号 / strip（ADR-10）────────
SQL_SHARD = "/*proxy*/show table with shardkey"
SQL_BROADCAST = "/*proxy*/show table with noshardkey_allset"
SQL_SINGLE = "/*proxy*/show table without shardkey"

KIND_SHARD = "shard"
KIND_BROADCAST = "broadcast"
KIND_SINGLE = "single"

# 元组顺序即归一化优先级：分片 > 广播 > 单表（ADR-2）
_KIND_SQL = ((KIND_SHARD, SQL_SHARD),
             (KIND_BROADCAST, SQL_BROADCAST),
             (KIND_SINGLE, SQL_SINGLE))

# 系统库口径 = index_audit_service._SYS ∪ zk_scan_enrich_service.SYSTEM_DATABASES。
# 本模块自持、不 import 其他 service（ADR-8）；超集关系由单测钉住。
_SYS_DB = frozenset({
    "information_schema", "mysql", "performance_schema", "sys",
    "sysdb", "query_rewrite", "xa",
    "tdsqlpcloud", "tdsqlpcloud_monitor", "__tencentdb__",
})

MAX_DATABASES = 500      # 库数护栏。超出即截断，并显式告警（绝不静默少算）
MAX_DIFF_SAMPLE = 20     # 差集样本上限，防止 detail 撑爆 VARCHAR(512)

# 表名列识别规则（§6.3）。自上而下，命中即停。
_EXACT_NAME_COLS = ("table", "table_name", "tables", "name")
_PREFIX_NAME_COLS = ("tables_in_",)
_EXCLUDE_TOKENS = ("type", "rows", "schema", "comment", "engine", "key")

_PERM_ERRNO = (1044, 1045, 1142, 1143, 1227)
_SYNTAX_ERRNO = 1064

# 可测性钩子：单测用 monkeypatch 注入 FakePool（§11）。生产恒为真实连接池。
_new_pool = TDSQLConnectionPool


# ══════════════════════════════════════════════════════════════════
# 小工具
# ══════════════════════════════════════════════════════════════════
def _errno_of(exc: BaseException) -> Optional[int]:
    """提取数据库 errno，沿 __cause__ 链上溯一层。"""
    for e in (exc, getattr(exc, "__cause__", None)):
        args = getattr(e, "args", None) if e is not None else None
        if args and isinstance(args[0], int):
            return args[0]
    return None


def _err(exc: BaseException) -> str:
    """把异常渲染成可直接呈现给使用者的处置提示。"""
    errno_ = _errno_of(exc)
    msg = str(exc)[:200]
    if errno_ in _PERM_ERRNO:
        return f"[errno {errno_}] 授权不足：{msg}"
    if errno_ == _SYNTAX_ERRNO:
        return f"[errno {errno_}] 语法错误（该连接可能非 Proxy 端点）：{msg}"
    return f"[errno {errno_}] {msg}" if errno_ else msg


def _warn(code: str, severity: str, db_name: str, detail) -> dict:
    return {"code": code, "severity": severity,
            "db_name": db_name, "detail": str(detail)[:512]}


def _diff_sample(names) -> str:
    ordered = sorted(names)
    text = ", ".join(ordered[:MAX_DIFF_SAMPLE])
    if len(ordered) > MAX_DIFF_SAMPLE:
        text += f" …等 {len(ordered)} 张"
    return text


def _pick_name_column(columns: list):
    """选出承载表名的列。返回 (列名, 是否为兜底猜测)。"""
    if not columns:
        return None, False
    if len(columns) == 1:
        return columns[0], False
    lowers = [(c, str(c).lower()) for c in columns]
    for col, low in lowers:
        if low in _EXACT_NAME_COLS:
            return col, False
    for col, low in lowers:
        if any(low.startswith(p) for p in _PREFIX_NAME_COLS):
            return col, False
    for col, low in lowers:
        if "table" in low and not any(t in low for t in _EXCLUDE_TOKENS):
            return col, False
    return columns[0], True          # 兜底：取第一列，并标记形态未知


def _clean_name(raw, db: str = "") -> str:
    """反引号剥离 + 库限定剥离。

    只有当限定词确为当前库名时才剥离，避免误伤含点号的表名。
    """
    name = str(raw if raw is not None else "").strip()
    if "." in name:
        head, tail = name.split(".", 1)
        if db and head.strip().strip("`").lower() == db.lower():
            name = tail
    return name.strip().strip("`").strip()


def _extract_names(rows, db: str = ""):
    """形态无关地提取表名集合。返回 (名字集合, 实际列名列表, 是否形态未知)。"""
    rows = rows or []
    if not rows:
        return set(), [], False
    first = rows[0]
    if isinstance(first, dict):
        columns = list(first.keys())
        col, guessed = _pick_name_column(columns)
        values = [r.get(col) if isinstance(r, dict) else None for r in rows]
    else:
        columns, guessed = [], False
        values = [(r[0] if r else None) for r in rows]
    names = set()
    for v in values:
        n = _clean_name(v, db)
        if n:
            names.add(n)
    return names, [str(c) for c in columns], guessed


# ══════════════════════════════════════════════════════════════════
# 采集
# ══════════════════════════════════════════════════════════════════
def list_business_databases(pool):
    """枚举业务库。返回 (库名列表, 是否被 MAX_DATABASES 截断)。"""
    rows = pool._execute("SHOW DATABASES") or []
    names = []
    for row in rows:
        if isinstance(row, dict):
            val = row.get("Database") or row.get("database")
            if val is None:
                vals = list(row.values())
                val = vals[0] if vals else ""
        else:
            val = row[0] if row else ""
        name = str(val or "").strip()
        if name and name.lower() not in _SYS_DB:
            names.append(name)
    names.sort(key=lambda s: (s.lower(), s))
    truncated = len(names) > MAX_DATABASES
    return names[:MAX_DATABASES], truncated


def _collect_baseline(pool, dbs: list) -> dict:
    """取 information_schema 全量名单。返回 {db: {"base": set, "view": set}}。

    要名字不要计数：集中式分支取 len(base)；分布式分支需要名字做
    视图扣除与双向集合差（§6.4 / §6.6）。
    """
    out = {d: {"base": set(), "view": set()} for d in dbs}
    if not dbs:
        return out
    placeholders = ",".join(["%s"] * len(dbs))
    rows = pool._execute(
        "SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE "
        "FROM information_schema.TABLES "
        f"WHERE TABLE_SCHEMA IN ({placeholders})", tuple(dbs)) or []
    wanted = {d.lower(): d for d in dbs}
    for r in rows:
        if not isinstance(r, dict):
            continue
        schema = str(r.get("TABLE_SCHEMA") or r.get("table_schema") or "").strip()
        key = wanted.get(schema.lower())
        if not key:
            continue
        name = str(r.get("TABLE_NAME") or r.get("table_name") or "").strip()
        if not name:
            continue
        ttype = str(r.get("TABLE_TYPE") or r.get("table_type") or "").strip().upper()
        if ttype == "BASE TABLE":
            out[key]["base"].add(name)
        elif ttype == "VIEW":
            out[key]["view"].add(name)
    return out


def _normalize(shard: set, broadcast: set, single: set) -> dict:
    """按 分片 > 广播 > 单表 归一化去重（§6.5 / RISK-A）。

    三集合互斥时为恒等变换；存在包含关系时保证 total 不重复计数。
    """
    s = set(shard)
    b_raw = set(broadcast)
    n_raw = set(single)
    b = b_raw - s
    n = n_raw - s - b_raw
    overlap = len(b_raw & s) + len(n_raw & (s | b_raw))
    return {"shard": s, "broadcast": b, "single": n,
            "union": s | b | n, "overlap": overlap}


def _blank_item(db: str, baseline: dict) -> dict:
    return {"db_name": db, "total_tables": 0, "shard_tables": 0,
            "broadcast_tables": 0, "single_tables": 0,
            "baseline_tables": len(baseline.get(db, {}).get("base", ())),
            "status": "OK", "detail": ""}


def _collect_centralized(dbs: list, baseline: dict):
    """集中式：纯内存换算，不发任何查询，不发任何 /*proxy*/ 命令（ADR-4）。"""
    items = []
    totals = {"shard": 0, "broadcast": 0, "single": 0,
              "total": 0, "overlap": 0, "failed": 0}
    for db in dbs:
        n = len(baseline.get(db, {}).get("base", ()))
        item = _blank_item(db, baseline)
        item["total_tables"] = n
        item["single_tables"] = n
        items.append(item)
        totals["single"] += n
        totals["total"] += n
    return items, [], {}, totals


def _collect_distributed(pool, dbs: list, baseline: dict):
    """分布式：逐业务库执行三条 /*proxy*/ 命令。

    连接隔离：另建 pool_size=1 的临时池，切库不污染共享池（ADR-3）。
    异常隔离：所有异常都在 with 块内部吃掉——若让异常穿出
    TDSQLConnectionPool.get_connection() 的 with，池会重建连接并中断循环。
    """
    items, warnings, shape = [], [], {}
    totals = {"shard": 0, "broadcast": 0, "single": 0,
              "total": 0, "overlap": 0, "failed": 0}
    if not dbs:
        return items, warnings, shape, totals

    shape_reported = False
    syntax_errors = 0
    cfg = dataclasses.replace(pool.config, database=dbs[0])
    tmp = _new_pool(cfg, pool_size=1)
    try:
        with tmp.get_connection() as conn:
            for db in dbs:
                item = _blank_item(db, baseline)
                sets, failed = {}, ""
                try:
                    conn.select_db(db)
                except Exception as e:                       # noqa: BLE001
                    failed = f"切换数据库失败: {_err(e)}"
                    if _errno_of(e) == _SYNTAX_ERRNO:
                        syntax_errors += 1
                if not failed:
                    for kind, sql in _KIND_SQL:
                        try:
                            with conn.cursor() as cur:
                                cur.execute(sql)
                                rows = cur.fetchall()
                        except Exception as e:               # noqa: BLE001
                            failed = f"{sql} 执行失败: {_err(e)}"
                            if _errno_of(e) == _SYNTAX_ERRNO:
                                syntax_errors += 1
                            break
                        names, columns, guessed = _extract_names(rows, db)
                        sets[kind] = names
                        if columns and kind not in shape:
                            shape[kind] = columns
                        if guessed and not shape_reported:
                            shape_reported = True
                            warnings.append(_warn(
                                "SHAPE_UNKNOWN", "WARNING", db,
                                f"未能识别表名列，已退化为取第一列；实际列名: {columns}"))
                if failed:
                    item["status"] = "FAILED"
                    item["detail"] = failed[:512]
                    totals["failed"] += 1
                    warnings.append(_warn("PROXY_CMD_FAILED", "ERROR", db, failed))
                    items.append(item)
                    continue

                views = baseline.get(db, {}).get("view", set())
                norm = _normalize(sets.get(KIND_SHARD, set()) - views,
                                  sets.get(KIND_BROADCAST, set()) - views,
                                  sets.get(KIND_SINGLE, set()) - views)
                item["shard_tables"] = len(norm["shard"])
                item["broadcast_tables"] = len(norm["broadcast"])
                item["single_tables"] = len(norm["single"])
                item["total_tables"] = len(norm["union"])
                totals["shard"] += item["shard_tables"]
                totals["broadcast"] += item["broadcast_tables"]
                totals["single"] += item["single_tables"]
                totals["total"] += item["total_tables"]
                totals["overlap"] += norm["overlap"]

                if norm["overlap"]:
                    warnings.append(_warn(
                        "KIND_OVERLAP", "WARNING", db,
                        f"三类结果集存在 {norm['overlap']} 处重叠，"
                        f"已按 分片>广播>单表 归一化去重，总数未重复计算"))

                base = baseline.get(db, {}).get("base", set())
                only_proxy = norm["union"] - base
                only_base = base - norm["union"]
                if only_proxy or only_base:
                    detail = (f"三类并集 {len(norm['union'])} 张，"
                              f"information_schema 基线 {len(base)} 张")
                    if only_base:
                        detail += (f"；仅基线可见({len(only_base)}): "
                                   f"{_diff_sample(only_base)}")
                    if only_proxy:
                        detail += (f"；仅 Proxy 可见({len(only_proxy)}): "
                                   f"{_diff_sample(only_proxy)}")
                    item["detail"] = detail[:512]
                    warnings.append(_warn("RECON_MISMATCH", "WARNING", db, detail))
                items.append(item)
    finally:
        try:
            tmp.close_all()
        except Exception:                                    # noqa: BLE001
            logger.debug("临时连接池关闭失败（忽略）", exc_info=True)

    if dbs and syntax_errors >= len(dbs):
        warnings.append(_warn(
            "NOT_DISTRIBUTED_ENDPOINT", "ERROR", "",
            "全部业务库均因语法错误(1064)失败：该连接可能指向后端 TXSQL "
            "而非 Proxy 端口，或该实例实际并非分布式实例"))
    return items, warnings, shape, totals


# ══════════════════════════════════════════════════════════════════
# 对外
# ══════════════════════════════════════════════════════════════════
def analyze(pool, connection_id: str = "", database: str = "") -> dict:
    from backend.models import InstanceType
    from backend.services.instance_type_service import instance_type_service

    ctx = instance_type_service.resolve(connection_id)
    is_dist = ctx.instance_type == InstanceType.DISTRIBUTED
    source = getattr(ctx.source, "value", str(ctx.source))

    warnings = []
    if database:
        dbs, truncated = [database], False
    else:
        dbs, truncated = list_business_databases(pool)

    if truncated:
        warnings.append(_warn(
            "TOO_MANY_DATABASES", "WARNING", "",
            f"业务库数量超过 {MAX_DATABASES}，仅统计前 {MAX_DATABASES} 个；"
            f"请用「库名」输入框分批统计"))
    if not dbs:
        warnings.append(_warn(
            "NO_BUSINESS_DB", "INFO", "",
            "未发现业务库（账号可见范围可能过窄，或实例确实为空）"))
    if source == "default" or ctx.conflict:
        warnings.append(_warn(
            "INSTANCE_TYPE_UNRELIABLE", "WARNING", "",
            f"实例类型来源为 {source}"
            f"{'（声明与探测存在冲突）' if ctx.conflict else ''}，"
            f"当前按「{'分布式' if is_dist else '集中式'}」口径统计；"
            f"若口径不符，请在实例管理页锁定实例类型后重跑"))

    baseline = _collect_baseline(pool, dbs)
    if is_dist:
        items, warns, shape, totals = _collect_distributed(pool, dbs, baseline)
    else:
        items, warns, shape, totals = _collect_centralized(dbs, baseline)
    warnings.extend(warns)

    return {
        "instance_type": ctx.instance_type.value,
        "type_source": source,
        "type_conflict": bool(ctx.conflict),
        "database_count": len(items),
        "total_tables": totals["total"],
        "shard_tables": totals["shard"],
        "broadcast_tables": totals["broadcast"],
        "single_tables": totals["single"],
        "failed_databases": totals["failed"],
        "overlap_count": totals["overlap"],
        "items": items,
        "warnings": warnings,
        "shape": shape,
    }


def run_stats(pool, connection_id: str = "", database: str = "",
              operator: str = "") -> dict:
    """执行一次统计并落库。落库失败不降级——直接抛出（REQ-6 要求留档）。"""
    database = (database or "").strip()
    if database and database.lower() in _SYS_DB:
        raise ValueError(f"不允许统计系统库: {database}")

    res = analyze(pool, connection_id=connection_id, database=database)
    conn = _get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO table_type_stat (connection_id, database_filter, "
            "instance_type, type_source, database_count, total_tables, "
            "shard_tables, broadcast_tables, single_tables, failed_databases, "
            "overlap_count, warnings_json, created_by) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (connection_id, database, res["instance_type"], res["type_source"],
             res["database_count"], res["total_tables"], res["shard_tables"],
             res["broadcast_tables"], res["single_tables"],
             res["failed_databases"], res["overlap_count"],
             json.dumps(res["warnings"], ensure_ascii=False), operator))
        stat_id = cur.lastrowid
        for it in res["items"]:
            conn.execute(
                "INSERT INTO table_type_stat_item (stat_id, db_name, total_tables, "
                "shard_tables, broadcast_tables, single_tables, baseline_tables, "
                "status, detail) VALUES (?,?,?,?,?,?,?,?,?)",
                (stat_id, it["db_name"], it["total_tables"], it["shard_tables"],
                 it["broadcast_tables"], it["single_tables"],
                 it["baseline_tables"], it["status"], it["detail"]))
        conn.commit()
    finally:
        conn.close()
    res["stat_id"] = stat_id
    return res


def list_history(connection_id: str = "", limit: int = 20) -> list:
    limit = max(1, min(int(limit or 20), 200))
    conn = _get_connection()
    try:
        if connection_id:
            rows = conn.execute(
                "SELECT * FROM table_type_stat WHERE connection_id=? "
                "ORDER BY id DESC LIMIT ?", (connection_id, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM table_type_stat ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_detail(stat_id: int) -> dict:
    conn = _get_connection()
    try:
        items = [dict(r) for r in conn.execute(
            "SELECT * FROM table_type_stat_item WHERE stat_id=? ORDER BY id",
            (stat_id,)).fetchall()]
        head = conn.execute(
            "SELECT warnings_json FROM table_type_stat WHERE id=?",
            (stat_id,)).fetchone()
    finally:
        conn.close()
    warnings = []
    if head:
        try:
            warnings = json.loads(dict(head).get("warnings_json") or "[]")
        except Exception:                                    # noqa: BLE001
            warnings = []
    return {"items": items, "warnings": warnings}
```

### A.2 `backend/api/table_type_stats.py`（新增）

```python
# -*- coding: utf-8 -*-
"""G14 · 表类型统计 API（DESIGN-v1.6.3.0 §5）"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.services import table_type_stats_service as svc
from backend.services.connection_registry import registry, ConnectionNotFoundError

router = APIRouter(prefix="/api/v1/table-type-stats", tags=["表类型统计"])


class StatsRequest(BaseModel):
    connection_id: str = Field("", description="目标连接ID；空则用默认连接")
    database: str = Field("", description="仅统计指定库；空则全部业务库")


def _pool(cid):
    try:
        return registry.get(cid)
    except ConnectionNotFoundError:
        raise HTTPException(status_code=400, detail="未连接TDSQL实例或连接不存在")


@router.post("/run", summary="发起表类型统计")
def run(body: StatsRequest):
    pool = _pool(body.connection_id)
    try:
        return svc.run_stats(pool, connection_id=body.connection_id,
                             database=body.database)
    except ValueError as e:
        # 入参口径错误（如指定系统库）——属于调用方问题，回 400 而非 500
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history", summary="表类型统计历史")
def history(connection_id: str = "", limit: int = 20):
    return {"items": svc.list_history(connection_id, limit)}


@router.get("/detail/{stat_id}", summary="表类型统计明细")
def detail(stat_id: int):
    return svc.get_detail(stat_id)
```

### A.3 `backend/schema/v11/110_table_type_stats.sql`（新增）

> 迁移器会**逐行剔除以 `--` 开头的行**再按 `;` 切分（`backend/schema/migrator.py:52-56`），
> 因此注释必须整行独占，语句之间必须有 `;`，文件末尾的 `;` 不可省。

```sql
-- v1.6.3.0 G14 表类型统计（DESIGN-v1.6.3.0 §6.8）
-- 任务表：一次统计一行
CREATE TABLE IF NOT EXISTS table_type_stat (
    id                  INT PRIMARY KEY AUTO_INCREMENT,
    connection_id       VARCHAR(64) DEFAULT '',
    database_filter     VARCHAR(128) DEFAULT '',
    instance_type       VARCHAR(32) DEFAULT '',
    type_source         VARCHAR(32) DEFAULT '',
    database_count      INT DEFAULT 0,
    total_tables        INT DEFAULT 0,
    shard_tables        INT DEFAULT 0,
    broadcast_tables    INT DEFAULT 0,
    single_tables       INT DEFAULT 0,
    failed_databases    INT DEFAULT 0,
    overlap_count       INT DEFAULT 0,
    warnings_json       TEXT,
    created_by          VARCHAR(64) DEFAULT '',
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_tts_conn (connection_id),
    INDEX idx_tts_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 明细表：一次统计的每个业务库一行
CREATE TABLE IF NOT EXISTS table_type_stat_item (
    id                  INT PRIMARY KEY AUTO_INCREMENT,
    stat_id             INT NOT NULL,
    db_name             VARCHAR(128) DEFAULT '',
    total_tables        INT DEFAULT 0,
    shard_tables        INT DEFAULT 0,
    broadcast_tables    INT DEFAULT 0,
    single_tables       INT DEFAULT 0,
    baseline_tables     INT DEFAULT 0,
    status              VARCHAR(16) DEFAULT 'OK',
    detail              VARCHAR(512) DEFAULT '',
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_ttsi (stat_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### A.4 `tests/test_table_type_stats.py`（新增）

```python
# -*- coding: utf-8 -*-
"""G14 · 表类型统计 回归测试（DESIGN-v1.6.3.0 §11）

除 test_run_stats_persists 外全部离线，不依赖真实 TDSQL 实例。
"""
import os
import random

import pytest

from backend.services import table_type_stats_service as svc
from backend.services.tdsql_connector import TDSQLConnectionConfig

_HOST = os.environ.get("TDSQL_TEST_HOST", "127.0.0.1")
_PORT = int(os.environ.get("TDSQL_TEST_PORT", "13306"))
_USER = os.environ.get("TDSQL_TEST_USER", "root")
_PASS = os.environ.get("TDSQL_TEST_PASSWORD", "tdsql_test_2024")
try:
    import pymysql
    pymysql.connect(host=_HOST, port=_PORT, user=_USER,
                    password=_PASS, connect_timeout=3).close()
    MYSQL_AVAILABLE = True
except Exception:
    MYSQL_AVAILABLE = False


# ══════════════════════════════════════════════════════════════════
# 测试替身
# ══════════════════════════════════════════════════════════════════
class FakePool:
    """脚本化连接池替身。

    databases   : SHOW DATABASES 返回的库名
    info_schema : {db: {"base":[...], "view":[...]}}
    per_db      : {(db, sql): 行列表 或 Exception}
    """

    def __init__(self, databases=None, info_schema=None, per_db=None,
                 select_db_fail=None):
        self.config = TDSQLConnectionConfig(host="h", port=3306, user="u",
                                            password="p", database="d")
        self.databases = databases or []
        self.info_schema = info_schema or {}
        self.per_db = per_db or {}
        self.select_db_fail = select_db_fail or {}
        self.seen = []            # 所有执行过的 SQL
        self.selected = []        # 所有 select_db 目标
        self.current_db = ""
        self.closed = False

    # ── 共享池路径 ────────────────────────────────────────────────
    def _execute(self, sql, params=None):
        self.seen.append(sql)
        if sql == "SHOW DATABASES":
            return [{"Database": d} for d in self.databases]
        if "information_schema.TABLES" in sql:
            wanted = set(params or ())
            out = []
            for db, kinds in self.info_schema.items():
                if wanted and db not in wanted:
                    continue
                for n in kinds.get("base", []):
                    out.append({"TABLE_SCHEMA": db, "TABLE_NAME": n,
                                "TABLE_TYPE": "BASE TABLE"})
                for n in kinds.get("view", []):
                    out.append({"TABLE_SCHEMA": db, "TABLE_NAME": n,
                                "TABLE_TYPE": "VIEW"})
            return out
        return []

    # ── 临时池路径 ────────────────────────────────────────────────
    def get_connection(self):
        pool = self

        class _Cursor:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

            def execute(self_inner, sql, params=None):
                pool.seen.append(sql)
                val = pool.per_db.get((pool.current_db, sql))
                if isinstance(val, Exception):
                    self_inner._rows = []
                    raise val
                self_inner._rows = val or []

            def fetchall(self_inner):
                return getattr(self_inner, "_rows", [])

        class _Conn:
            def select_db(self_inner, db):
                pool.selected.append(db)
                if db in pool.select_db_fail:
                    raise pool.select_db_fail[db]
                pool.current_db = db

            def cursor(self_inner):
                return _Cursor()

        class _Ctx:
            def __enter__(self_inner):
                return _Conn()

            def __exit__(self_inner, *a):
                return False

        return _Ctx()

    def close_all(self):
        self.closed = True


def _rows(names, col="Tables_in_db"):
    return [{col: n} for n in names]


def _mysql_error(errno, msg):
    e = Exception(errno, msg)
    return e


def _patch_ctx(monkeypatch, itype, source="probed", conflict=False):
    from backend.models import InstanceType, TypeSource
    from backend.services.instance_type_service import (InstanceContext,
                                                        instance_type_service)
    monkeypatch.setattr(
        instance_type_service, "resolve",
        lambda cid="", requested=None: InstanceContext(
            InstanceType(itype), TypeSource(source), conflict=conflict))


def _patch_tmp_pool(monkeypatch, pool):
    """让 _collect_distributed 复用同一个 FakePool（ADR-3 的可测性钩子）"""
    monkeypatch.setattr(svc, "_new_pool", lambda cfg, pool_size=1: pool)


# ══════════════════════════════════════════════════════════════════
# 常量护栏
# ══════════════════════════════════════════════════════════════════
def test_sql_constants_verbatim():
    """三条命令逐字等于原厂文本（ADR-10）"""
    assert svc.SQL_SHARD == "/*proxy*/show table with shardkey"
    assert svc.SQL_BROADCAST == "/*proxy*/show table with noshardkey_allset"
    assert svc.SQL_SINGLE == "/*proxy*/show table without shardkey"
    for sql in (svc.SQL_SHARD, svc.SQL_BROADCAST, svc.SQL_SINGLE):
        assert sql.startswith("/*proxy*/"), "必须保留 /*proxy*/ 前缀"
        assert ";" not in sql, "不得附加分号"
        assert sql == sql.strip()


def test_sys_db_is_superset():
    """_SYS_DB 必须同时是项目内两套系统库清单的超集（ADR-8）"""
    from backend.services.index_audit_service import _SYS
    from backend.services.zk_scan_enrich_service import SYSTEM_DATABASES
    assert {s.lower() for s in _SYS} <= svc._SYS_DB
    assert {s.lower() for s in SYSTEM_DATABASES} <= svc._SYS_DB


# ══════════════════════════════════════════════════════════════════
# 形态无关解析（RISK-C）
# ══════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("rows,expect", [
    ([{"Tables_in_db_order": "t_a"}], {"t_a"}),
    ([{"table": "t_a"}, {"table": "t_b"}], {"t_a", "t_b"}),
    ([{"table_name": "t_a"}], {"t_a"}),
    ([{"table": "t_a", "shardkey": "id"}], {"t_a"}),
    ([{"Tables_in_db_order": "db_order.t_a"}], {"t_a"}),
    ([{"Tables_in_db_order": "`t_a`"}], {"t_a"}),
    ([{"Tables_in_db_order": "  t_a  "}], {"t_a"}),
    ([], set()),
])
def test_extract_names_shapes(rows, expect):
    names, _cols, guessed = svc._extract_names(rows, "db_order")
    assert names == expect
    assert guessed is False


def test_extract_names_keeps_dotted_table_name():
    """限定词不是当前库名时不得剥离——避免误伤含点号的表名"""
    names, _c, _g = svc._extract_names([{"table": "odd.name"}], "db_order")
    assert names == {"odd.name"}


def test_extract_names_unknown_shape():
    names, columns, guessed = svc._extract_names(
        [{"col_x": "t_a", "col_y": 1}], "db")
    assert names == {"t_a"} and guessed is True
    assert columns == ["col_x", "col_y"]


# ══════════════════════════════════════════════════════════════════
# 归一化（RISK-A）
# ══════════════════════════════════════════════════════════════════
def test_normalize_disjoint():
    r = svc._normalize({"a", "b"}, {"c"}, {"d", "e"})
    assert (len(r["shard"]), len(r["broadcast"]), len(r["single"])) == (2, 1, 2)
    assert r["overlap"] == 0 and len(r["union"]) == 5


def test_normalize_overlap():
    """without shardkey 若为 noshardkey_allset 超集，广播表不得重复计入单表"""
    r = svc._normalize({"s1"}, {"b1"}, {"b1", "n1"})
    assert len(r["shard"]) == 1 and len(r["broadcast"]) == 1
    assert len(r["single"]) == 1 and len(r["union"]) == 3
    assert r["overlap"] == 1


def test_counts_are_consistent():
    """恒等式 total == shard + broadcast + single，随机 200 组"""
    rnd = random.Random(20260829)
    pool = [f"t{i}" for i in range(30)]
    for _ in range(200):
        r = svc._normalize(set(rnd.sample(pool, rnd.randint(0, 10))),
                           set(rnd.sample(pool, rnd.randint(0, 10))),
                           set(rnd.sample(pool, rnd.randint(0, 10))))
        assert len(r["union"]) == (len(r["shard"]) + len(r["broadcast"])
                                   + len(r["single"]))


# ══════════════════════════════════════════════════════════════════
# 业务库枚举
# ══════════════════════════════════════════════════════════════════
def test_business_databases_filter_system():
    pool = FakePool(databases=["db_a", "mysql", "sysdb", "xa",
                               "information_schema", "tdsqlpcloud", "db_b"])
    dbs, truncated = svc.list_business_databases(pool)
    assert dbs == ["db_a", "db_b"] and truncated is False


def test_business_databases_truncation_is_visible(monkeypatch):
    monkeypatch.setattr(svc, "MAX_DATABASES", 2)
    _patch_ctx(monkeypatch, "centralized")
    pool = FakePool(databases=["d1", "d2", "d3"],
                    info_schema={"d1": {"base": ["t"]}, "d2": {"base": ["t"]}})
    res = svc.analyze(pool, connection_id="c1")
    assert any(w["code"] == "TOO_MANY_DATABASES" for w in res["warnings"])


# ══════════════════════════════════════════════════════════════════
# 集中式分支
# ══════════════════════════════════════════════════════════════════
def test_centralized_branch(monkeypatch):
    _patch_ctx(monkeypatch, "centralized")
    pool = FakePool(databases=["db_a", "mysql"],
                    info_schema={"db_a": {"base": ["t1", "t2", "t3"],
                                          "view": ["v1"]}})
    res = svc.analyze(pool, connection_id="c1")
    assert res["instance_type"] == "centralized"
    assert res["single_tables"] == 3 and res["total_tables"] == 3
    assert res["shard_tables"] == 0 and res["broadcast_tables"] == 0
    # 视图不计入
    assert res["items"][0]["total_tables"] == 3
    # 绝不发 /*proxy*/ 命令（ADR-4）
    assert all("/*proxy*/" not in s for s in pool.seen)
    # 绝不切库
    assert pool.selected == []


# ══════════════════════════════════════════════════════════════════
# 分布式分支
# ══════════════════════════════════════════════════════════════════
def _dist_pool(per_db, info_schema, databases, **kw):
    return FakePool(databases=databases, info_schema=info_schema,
                    per_db=per_db, **kw)


def test_distributed_happy_path(monkeypatch):
    _patch_ctx(monkeypatch, "distributed")
    per_db = {
        ("db_a", svc.SQL_SHARD): _rows(["s1", "s2"]),
        ("db_a", svc.SQL_BROADCAST): _rows(["b1"]),
        ("db_a", svc.SQL_SINGLE): _rows(["n1"]),
    }
    pool = _dist_pool(per_db,
                      {"db_a": {"base": ["s1", "s2", "b1", "n1"]}}, ["db_a"])
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert (res["shard_tables"], res["broadcast_tables"],
            res["single_tables"], res["total_tables"]) == (2, 1, 1, 4)
    assert res["warnings"] == []
    assert pool.closed is True                   # 临时池必须释放
    assert pool.selected == ["db_a"]


def test_distributed_view_is_excluded(monkeypatch):
    """原厂"不统计视图"——即使命令返回了视图也必须扣除"""
    _patch_ctx(monkeypatch, "distributed")
    per_db = {
        ("db_a", svc.SQL_SHARD): _rows(["s1"]),
        ("db_a", svc.SQL_BROADCAST): _rows([]),
        ("db_a", svc.SQL_SINGLE): _rows(["n1", "v1"]),
    }
    pool = _dist_pool(per_db,
                      {"db_a": {"base": ["s1", "n1"], "view": ["v1"]}},
                      ["db_a"])
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert res["single_tables"] == 1 and res["total_tables"] == 2
    assert not any(w["code"] == "RECON_MISMATCH" for w in res["warnings"])


def test_distributed_overlap_warns_and_does_not_double_count(monkeypatch):
    """RISK-A 命中：without shardkey 含广播表"""
    _patch_ctx(monkeypatch, "distributed")
    per_db = {
        ("db_a", svc.SQL_SHARD): _rows(["s1"]),
        ("db_a", svc.SQL_BROADCAST): _rows(["b1"]),
        ("db_a", svc.SQL_SINGLE): _rows(["b1", "n1"]),
    }
    pool = _dist_pool(per_db, {"db_a": {"base": ["s1", "b1", "n1"]}}, ["db_a"])
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert res["total_tables"] == 3 and res["single_tables"] == 1
    assert res["overlap_count"] == 1
    assert any(w["code"] == "KIND_OVERLAP" for w in res["warnings"])


def test_distributed_recon_mismatch(monkeypatch):
    """RISK-B 命中：并集与 information_schema 不一致，双向差集必须写进 detail"""
    _patch_ctx(monkeypatch, "distributed")
    per_db = {
        ("db_a", svc.SQL_SHARD): _rows(["s1"]),
        ("db_a", svc.SQL_BROADCAST): _rows([]),
        ("db_a", svc.SQL_SINGLE): _rows(["n1"]),
    }
    pool = _dist_pool(per_db,
                      {"db_a": {"base": ["s1", "n1", "ghost"]}}, ["db_a"])
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    w = [x for x in res["warnings"] if x["code"] == "RECON_MISMATCH"]
    assert w and "ghost" in w[0]["detail"]
    assert "ghost" in res["items"][0]["detail"]


def test_distributed_partial_failure(monkeypatch):
    """单库失败只降级该库：不计入总数、单列计数、其余库照常（ADR-5）"""
    _patch_ctx(monkeypatch, "distributed")
    per_db = {
        ("db_a", svc.SQL_SHARD): _rows(["s1"]),
        ("db_a", svc.SQL_BROADCAST): _rows([]),
        ("db_a", svc.SQL_SINGLE): _rows([]),
        ("db_b", svc.SQL_SHARD): _mysql_error(1142, "SELECT command denied"),
        ("db_c", svc.SQL_SHARD): _rows(["s9"]),
        ("db_c", svc.SQL_BROADCAST): _rows([]),
        ("db_c", svc.SQL_SINGLE): _rows([]),
    }
    pool = _dist_pool(per_db,
                      {"db_a": {"base": ["s1"]}, "db_b": {"base": ["x"]},
                       "db_c": {"base": ["s9"]}},
                      ["db_a", "db_b", "db_c"])
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert res["failed_databases"] == 1
    assert res["total_tables"] == 2                # 失败库不计入
    statuses = {i["db_name"]: i["status"] for i in res["items"]}
    assert statuses == {"db_a": "OK", "db_b": "FAILED", "db_c": "OK"}
    w = [x for x in res["warnings"] if x["code"] == "PROXY_CMD_FAILED"]
    assert w and "授权不足" in w[0]["detail"]


def test_distributed_all_1064_flags_wrong_endpoint(monkeypatch):
    _patch_ctx(monkeypatch, "distributed")
    per_db = {("db_a", svc.SQL_SHARD): _mysql_error(1064, "syntax error")}
    pool = _dist_pool(per_db, {"db_a": {"base": []}}, ["db_a"])
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert any(w["code"] == "NOT_DISTRIBUTED_ENDPOINT" for w in res["warnings"])


def test_select_db_failure_is_isolated(monkeypatch):
    _patch_ctx(monkeypatch, "distributed")
    pool = _dist_pool({}, {"db_a": {"base": []}}, ["db_a"],
                      select_db_fail={"db_a": _mysql_error(1049, "Unknown database")})
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert res["failed_databases"] == 1
    assert res["items"][0]["status"] == "FAILED"
    assert pool.closed is True


def test_shared_pool_is_never_switched(monkeypatch):
    """ADR-3 护栏：共享池连接上不得发生任何 select_db"""
    _patch_ctx(monkeypatch, "distributed")
    shared = FakePool(databases=["db_a"], info_schema={"db_a": {"base": ["s1"]}})
    tmp = _dist_pool({("db_a", svc.SQL_SHARD): _rows(["s1"]),
                      ("db_a", svc.SQL_BROADCAST): _rows([]),
                      ("db_a", svc.SQL_SINGLE): _rows([])},
                     {"db_a": {"base": ["s1"]}}, ["db_a"])
    _patch_tmp_pool(monkeypatch, tmp)
    svc.analyze(shared, connection_id="c1")
    assert shared.selected == []
    assert tmp.selected == ["db_a"]


def test_empty_database_is_not_an_error(monkeypatch):
    _patch_ctx(monkeypatch, "distributed")
    per_db = {("db_a", svc.SQL_SHARD): [],
              ("db_a", svc.SQL_BROADCAST): [],
              ("db_a", svc.SQL_SINGLE): []}
    pool = _dist_pool(per_db, {"db_a": {"base": []}}, ["db_a"])
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert res["total_tables"] == 0 and res["warnings"] == []


def test_no_business_db_warns(monkeypatch):
    _patch_ctx(monkeypatch, "centralized")
    pool = FakePool(databases=["mysql", "sys"])
    res = svc.analyze(pool, connection_id="c1")
    assert any(w["code"] == "NO_BUSINESS_DB" for w in res["warnings"])


def test_unreliable_instance_type_warns(monkeypatch):
    _patch_ctx(monkeypatch, "centralized", source="default")
    pool = FakePool(databases=["db_a"], info_schema={"db_a": {"base": ["t"]}})
    res = svc.analyze(pool, connection_id="")
    assert any(w["code"] == "INSTANCE_TYPE_UNRELIABLE" for w in res["warnings"])


def test_reject_system_database():
    with pytest.raises(ValueError):
        svc.run_stats(FakePool(), connection_id="c1", database="mysql")


# ══════════════════════════════════════════════════════════════════
# 落库（需本地元数据库）
# ══════════════════════════════════════════════════════════════════
@pytest.mark.skipif(not MYSQL_AVAILABLE, reason="MySQL 测试环境未启动")
def test_run_stats_persists(monkeypatch):
    os.environ.setdefault("SQLCHECK_DB_NAME", "tdsql_sqlcheck_test")
    from backend.services.database import ensure_db
    ensure_db()
    _patch_ctx(monkeypatch, "centralized")
    pool = FakePool(databases=["db_a", "db_b"],
                    info_schema={"db_a": {"base": ["t1", "t2"]},
                                 "db_b": {"base": ["t3"]}})
    res = svc.run_stats(pool, connection_id="qa", operator="pytest")
    assert res["total_tables"] == 3 and res["single_tables"] == 3
    detail = svc.get_detail(res["stat_id"])
    assert len(detail["items"]) == len(res["items"])
    assert isinstance(detail["warnings"], list)
    hist = svc.list_history("qa", limit=5)
    assert hist and hist[0]["id"] == res["stat_id"]


@pytest.mark.skipif(not MYSQL_AVAILABLE, reason="MySQL 测试环境未启动")
def test_get_detail_missing_id_is_graceful():
    os.environ.setdefault("SQLCHECK_DB_NAME", "tdsql_sqlcheck_test")
    from backend.services.database import ensure_db
    ensure_db()
    out = svc.get_detail(99999999)
    assert out == {"items": [], "warnings": []}
```

### A.5 既有文件的 9 行改动 + 1 个前端块

#### A.5.1 `backend/main.py` —— 2 行

在第 39 行 `from backend.api.index_audit import router as index_audit_router` 之后追加：

```python
from backend.api.table_type_stats import router as table_type_stats_router
```

在第 173 行 `app.include_router(toolkit_router)          # G13 运维工具箱` 之后追加：

```python
app.include_router(table_type_stats_router)  # G14 表类型统计
```

#### A.5.2 `backend/services/auth_service.py` —— 3 行

**P1** 在第 379 行 `"/api/v1/toolkit": "deep-diag-toolkit",` 之后追加：

```python
    "/api/v1/table-type-stats": "deep-diag-tabletype",
```

**P2** 在 `ALL_MENU_KEYS` 中 `'deep-diag-toolkit',`（第 494 行）之后追加：

```python
    'deep-diag-tabletype',
```

**P3** 在 `MENU_LABELS` 中 `'deep-diag-toolkit': '深度诊断-运维工具箱',`（第 509 行）之后追加：

```python
    'deep-diag-tabletype': '深度诊断-表类型统计',
```

#### A.5.3 `backend/services/database.py` —— 1 行

在 `_init_default_data` 的 `all_menus` 中 `'deep-diag-toolkit',`（第 1685 行）之后追加：

```python
        'deep-diag-tabletype',
```

> **这一行不是可选的。** `database.py:1743` 的
> `DELETE FROM role_permissions WHERE menu_key NOT IN (...)` 会在每次启动时
> 删掉不在该清单里的菜单键，导致新页签在权限矩阵里出现一次、下次重启后永久消失。

#### A.5.4 `frontend/index.html` —— 新增一个页签块（不改任何既有行）

插入位置：第 1839 行"索引体检"的 `</el-tab-pane>` 之后、第 1840 行 `<!-- 结构比对 G6 -->` 之前。

```html
              <!-- 表类型统计 G14 -->
              <el-tab-pane v-if="visibleMenus.has('deep-diag-tabletype')" label="表类型统计" name="tabletype">
                <el-input v-model="deepDb" placeholder="库名(空=全部业务库)" size="small" style="width:200px;margin-right:8px"></el-input>
                <el-button type="primary" size="small" :loading="deepLoading==='tabletype'" :disabled="!deepConnId" @click="runTableTypeStats">统计表类型</el-button>
                <span v-if="deepResult.tabletype" style="margin-left:12px;font-size:13px">
                  实例类型 <b>{{ deepResult.tabletype.instance_type==='distributed'?'分布式':'集中式' }}</b>
                  · 库 {{ deepResult.tabletype.database_count }}
                  · 总表 <b>{{ deepResult.tabletype.total_tables }}</b>
                  · 单表 <b>{{ deepResult.tabletype.single_tables }}</b>
                  · 广播表 <b style="color:var(--warning-500)">{{ deepResult.tabletype.broadcast_tables }}</b>
                  · 分片表 <b style="color:var(--success-500)">{{ deepResult.tabletype.shard_tables }}</b>
                  <span v-if="deepResult.tabletype.failed_databases"> · <b style="color:var(--danger-500)">失败库 {{ deepResult.tabletype.failed_databases }}</b></span>
                </span>
                <el-alert v-for="(w,i) in (deepResult.tabletype ? deepResult.tabletype.warnings : [])" :key="i"
                          :type="w.severity==='ERROR'?'error':(w.severity==='WARNING'?'warning':'info')"
                          :closable="false" show-icon style="margin-top:8px"
                          :title="w.code + (w.db_name ? (' · '+w.db_name) : '')" :description="w.detail"></el-alert>
                <el-table v-if="deepResult.tabletype" :data="deepResult.tabletype.items" size="small" border style="margin-top:12px" max-height="480">
                  <el-table-column prop="db_name" label="数据库" width="200"></el-table-column>
                  <el-table-column prop="total_tables" label="总表数" width="100"></el-table-column>
                  <el-table-column prop="single_tables" label="单表" width="90"></el-table-column>
                  <el-table-column prop="broadcast_tables" label="广播表" width="90"></el-table-column>
                  <el-table-column prop="shard_tables" label="分片表" width="90"></el-table-column>
                  <el-table-column prop="baseline_tables" label="基线(元数据)" width="120"></el-table-column>
                  <el-table-column prop="status" label="状态" width="90">
                    <template #default="s"><el-tag :type="s.row.status==='OK'?'success':'danger'" size="small">{{ s.row.status }}</el-tag></template>
                  </el-table-column>
                  <el-table-column prop="detail" label="说明"></el-table-column>
                </el-table>
                <div style="color:#909399;font-size:12px;margin-top:8px">
                  口径：分布式实例逐业务库执行 /*proxy*/show table with shardkey · with noshardkey_allset · without shardkey，按"库名+表名"去重；
                  集中式实例统计 information_schema.TABLES 中 TABLE_TYPE='BASE TABLE'，分片表与广播表恒为 0，不统计视图。结果为采集时刻快照。
                </div>
              </el-tab-pane>
```

#### A.5.5 `frontend/static/js/app.js` —— 3 行

**① 第 218 行**，`deepResult` 增加一个键：

```diff
-    const deepResult=reactive({cluster:null,index:null,diff:null,emergency:null,sqlstats:null});
+    const deepResult=reactive({cluster:null,index:null,diff:null,emergency:null,sqlstats:null,tabletype:null});
```

**② 第 811 行** `runSqlStats` 的 `};` 之后、`// G10: ZK Discovery` 之前追加新方法：

```javascript
    const runTableTypeStats=async()=>{
      const r=await _deepPost('tabletype','/api/v1/table-type-stats/run',{connection_id:deepConnId.value,database:deepDb.value});
      if(r){deepResult.tabletype=r;ElementPlus.ElMessage.success(`统计完成：${r.database_count} 个库 / ${r.total_tables} 张表`)}
    };
```

**③ 第 1983 行** `setup()` 返回清单，把 `runSqlStats,` 改成 `runSqlStats,runTableTypeStats,`：

```diff
-...,runClusterInspect,runIndexAudit,runSchemaDiff,runEmergency,runSqlStats,visibleMenus,...
+...,runClusterInspect,runIndexAudit,runSchemaDiff,runEmergency,runSqlStats,runTableTypeStats,visibleMenus,...
```

> 漏掉第 ③ 步的后果：页签能渲染，但点按钮报 `runTableTypeStats is not a function`。
> 这是本项目 `setup()` 显式返回清单写法的固有陷阱，必须逐条核对。

---

## 15. 附录 B · 三条命令的实测形态锚点（**待 §10 T02 回填**）

| 命令 | 实际列头 | 前 5 行样例 | 总行数 | 采集实例 | 采集日期 |
|---|---|---|---|---|---|
| `/*proxy*/show table with shardkey` | 待回填 | | | | |
| `/*proxy*/show table with noshardkey_allset` | 待回填 | | | | |
| `/*proxy*/show table without shardkey` | 待回填 | | | | |

回填后，本表会被写成 `tests/test_table_type_stats.py` 的固定夹具
（替换 `_rows()` 里的默认列名 `Tables_in_db`），使单测形态与真实 TDSQL 一致。

---

## 修订记录

| 版本 | 日期 | 作者 | 内容 |
|---|---|---|---|
| Rev.A | 2026-08-29 | 智能体 A | 首版。需求拆解、现状勘查（含 `/*proxy*/` 存活性证据链）、三大语义风险（RISK-A/B/C/D）识别与对策、总体与详细设计、10 条 ADR、17 项异常矩阵、爆炸半径分析、12 个内网实测用例与 GATE-1 放行判据、附录 A 全套成品代码（服务层 489 行 / API 44 行 / 迁移 34 行 / 单测 455 行 / 既有文件 9 行改动 + 1 个前端块）。**附录 A 代码已在本地以 importlib 挂载方式跑通 32 项单测（含真实 MariaDB 落库），仓库代码零改动。** |
