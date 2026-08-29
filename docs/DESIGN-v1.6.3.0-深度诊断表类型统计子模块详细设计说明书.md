# DESIGN-v1.6.3.0 深度诊断·表类型统计（G14）详细设计说明书

| 项 | 内容 |
|---|---|
| 文档编号 | DESIGN-v1.6.3.0 **Rev.B** |
| 模块编号 | **G14 · 表类型统计**（深度诊断第 10 个子模块） |
| 目标版本 | v1.6.3.0（当前基线 v1.6.2.2，`VERSION` / `backend/config.py:APP_VERSION`） |
| 文档等级 | **照图施工级**——附录 A 给出全部新增/修改文件的逐行成品代码（已本地验证：35 项单测全通过），实施者不得二次设计 |
| 编写 | 智能体 A |
| 编写日期 | 2026-08-29（Rev.A 首版 / Rev.B 依第一轮内网实测修订） |
| 状态 | 设计与代码**已完成并按第一轮实测修订**；**待 T13/T14/T15 三项补测**（§10.2），其中 T15 是唯一可能推翻设计的项（§10.5 GATE-2） |
| 前置约束 | 本文档编写阶段**未修改任何代码**（用户要求）。仓库工作区在本文档提交时保持干净。 |

---

## 0. 阅读指引与本文档的三条硬约束

本文档同时承担三件事，读者请按角色取用：

* **实施者（智能体 Q 或人工）**：读 §5～§9 + 附录 A。附录 A 是**可直接落盘的成品代码**——四个新增文件 + 既有文件的 9 行改动 + 1 个前端块，逐字给出。
* **内网测试配合者**：只读 **§10**。第一轮 8 个用例已完成（裁决见 §10.1），本轮只剩 **T13 / T14 / T15** 三项（§10.2）——全部是只读 SQL，不需要改任何代码。**T15 必须用 `mysql` 命令行客户端做**，因为要绕开赤兔前端。
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

> 本节所有结论均来自对当前 `main` 分支（`50a1c04`）的实读，不是推测。
> **行号锚定于 `50a1c04`。** 实施前请先 `git log --oneline -1` 核对；若 main 已前进，
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
| 统一 POST 辅助函数 `_deepPost(key, url, payload)` | `frontend/static/js/app.js:780-790` |
| **样板方法** `runIndexAudit` | `frontend/static/js/app.js:795-798` |
| `setup()` 返回值总清单（新方法必须挂进去，否则模板取不到） | `frontend/static/js/app.js:2043` |

### 2.2 一个子模块需要登记的 4 个点（缺一即失效）

| # | 文件:行 | 内容 | 缺失后果 |
|---|---|---|---|
| P1 | `backend/services/auth_service.py:371-379` | API 前缀 → 菜单键映射 `_PATH_TO_MENU` | 写端点"未映射默认放行"（fail-open），且 `tests/test_rbac_path_coverage.py` **直接失败** |
| P2 | `backend/services/auth_service.py:491-494` | `ALL_MENU_KEYS` | 权限矩阵页看不到该菜单，无法配置 |
| P3 | `backend/services/auth_service.py:504-509` | `MENU_LABELS` | 权限矩阵页显示裸键名 |
| P4 | `backend/services/database.py:1691` | `_init_default_data` 的 `all_menus` | **致命**：`database.py:1749` 有 `DELETE FROM role_permissions WHERE menu_key NOT IN (...)`，未登记的键会在每次启动时被删掉，菜单永久不可见 |

补充事实（决定了本模块**不需要**写任何存量库订正 SQL）：
`database.py:1707-1710` 对 `all_menus × 内置角色`执行 `INSERT IGNORE INTO role_permissions(...) VALUES(...)`，`init_db()` 每次启动都会跑（`database.py:394`）。因此新键在**存量库**上会于下次启动自动补齐，`developer` / `auditor` 的默认不可见排除清单（`database.py:1702-1705`）不含本键 → 四个内置角色默认全部可见，符合 REQ-7。

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
* **不动 `database.py::_create_all_tables`**：`init_db()` 在 `_create_all_tables` 之后就会调 `migrator.run_migrations()`（`database.py:383`），全新安装与存量升级都覆盖到。这样 `database.py` 只需改 P4 那 1 行。

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

### 3.3 语义风险清单（含 2026-08-29 内网实测裁决）

> 本节四条风险在 Rev.A 编写时全部未知。Rev.B 依据内网实测（附录 B）逐条裁决，
> 并新增两条实测暴露出来的风险 RISK-E / RISK-F。

#### RISK-A：`without shardkey` 可能是 `noshardkey_allset` 的超集 —— **实测证伪，对策保留**

Rev.A 的担忧：字面看"广播表"就是"没有 shardkey、但在所有 SET 上都有副本的表"，
那么 `show table without shardkey` **完全可能同时返回广播表和单表**。若直接把它的
行数当"单表数"，总数会恒定高估一个"广播表数"，且**方向固定、静默、库越大错得越多**。

**实测裁决（sqltuning 实例）**：三个结果集**互斥**。
`without shardkey` 7 张（`kcda_vchr_cmprs_old` / `kdpa_cust_acct_num_cmprs` / `t2` /
`t_max` / `t_max2` / `t_max3` / `txt`），`noshardkey_allset` 4 张（`kbrp_org` /
`kcda_bcast` / `kcda_vchr_cmprs` / `kdpp_int_rate_adjust_detl`），两者无交集
（注意 `kcda_vchr_cmprs_old` 与 `kcda_vchr_cmprs` 是**两张不同的表**，不要看串）。
原厂"`without shardkey` → 单表"的字面口径在本版本上成立。

**对策仍保留**：按 `分片 > 广播 > 单表` 归一化去重的代码**不删**。在互斥前提下它是
恒等变换、零代价；一旦换实例/换版本出现重叠，它是唯一能挡住静默高估的东西，
并会打出 `KIND_OVERLAP` 告警。**证伪一次不等于永远成立**——这条对策是保险，不是补丁。

#### RISK-B：三类之外可能还有第四类

`分片 ∪ 广播 ∪ 单表` 未必等于该库全部 `BASE TABLE`。**实测未取到可判定的数据**
（截图 2 的 `information_schema.tables` 输出停在 `mysql` 库的前 3 行，没翻到业务库），
故该风险**保持未裁决**，由 T14 补测。

**设计对策不变**：始终同时采集 `information_schema` 名单，与三类并集做**双向集合差**，
不一致则输出 `RECON_MISMATCH` 并列出两侧差集各前 20 个表名。产品不替用户裁决哪个数对。

#### RISK-C：返回结果的列形态未知 —— **实测已锚定**

实测形态（三份截图一致，跨两个不同实例）：

| 命令 | 列 | 值样例 |
|---|---|---|
| `show table with shardkey` | `db_table` + `info` | `sqltuning.kcdb_change_card` / `shardkey:orig_card_num` |
| `show table with noshardkey_allset` | `db_table` + `info` | `sqltuning.kcda_bcast` / `shardkey:noshardkey_allset` |
| `show table without shardkey` | `db_table`（**只有一列，无 info**） | `sqltuning.t_max` |

两点必须落到代码：
1. 列名是 `db_table`——已加入 `_EXACT_NAME_COLS` 首位；同时把 `info` 加入
   `_EXCLUDE_TOKENS`，杜绝双列时选错列。
2. **值是库限定名 `db.table`，不是裸表名。** 这直接引出 RISK-E。

`info` 列的内容（`shardkey:xxx` / `SHARDKEY_HASH_USE_SUB;sub_shardkey:id;sub_func:id` /
`noshardkey_allset;auto_increment:ID`）本期不使用，但形态已记录在附录 B，
为将来的"分片键分布"类需求留好锚点。

#### RISK-D：会话默认库切换污染连接池

`TDSQLConnectionPool` 是线程本地长连接复用。在共享池连接上 `USE <db>` 会把默认库
改掉并留给下一个使用者（`slow_enrich_service.py:219` 已有先例埋雷）。

**对策（ADR-3）**：另建 `pool_size=1` 的临时池，全部切库发生在它自己的连接上，
`finally` 里 `close_all()`。对共享池零副作用，整个统计只多开 1 条 TCP 连接。

#### RISK-E：命令的作用域可能是**实例级**而非当前库 —— **实测暴露的新风险，未裁决**

返回值是**库限定名** `sqltuning.t_max` 这件事本身就很可疑：如果命令只看当前会话库，
返回裸表名就够了，没有理由带库前缀。

更强的旁证是原厂那句 **"使用『数据库名 + 表名』去重"**。若命令是当前库作用域，
逐库遍历天然不会产生重复行，这句话根本无从谈起；**它存在的唯一合理解释，就是
逐库执行时会反复拿到同一批（跨库的）结果，必须靠"库名+表名"去重。**

实测样本里每个实例都只观察到单一库前缀（`sqltuning` 实例全是 `sqltuning.*`，
赤兔 `lzbj_ecif` 全是 `lzbj_ecif.*`），但这两个实例是否**只有一个业务库**未知，
所以**证明不了**是当前库作用域。由 T13 判决。

**如果不处理会怎样**：Rev.A 的实现把每一行都算在"当前遍历到的库"头上。若作用域是
实例级，N 个业务库就会各自拿到全实例的表，**总数放大 N 倍**，而且逐库明细全错。
这是一个比 RISK-A 严重得多的缺陷。

**Rev.B 对策（两种作用域下都正确，不依赖 T13 结论）**：
1. **按库限定名归属**：每一行解析成 `(库名, 表名)`，归到**行里写的那个库**，
   而不是当前会话库。非目标库（系统库、被 `database` 参数筛掉的库）的行直接丢弃。
2. **全局 `(库, 表)` 去重**：用一张全局 `kind_map[(db, table)] = kind` 累积，
   同一个 `(库, 表)` 无论被看到几次都只计一次——这正是原厂那句去重要求的实现。
3. **覆盖性跳过**：轮到库 `d` 时，若已累积到的 `d` 的表集**与 `information_schema`
   基线逐表相等**，说明 `d` 已被前面的执行完整覆盖，跳过它的三条命令。
   * 实例级作用域下：第一个库跑完就覆盖全实例，其余库全部跳过 → **3 条命令而不是 3N 条**；
   * 当前库作用域下：覆盖性永不成立 → 老老实实逐库执行；
   * 完备性由"与权威目录逐表相等"证明，不是"看着差不多"，不存在漏库风险。
4. 检测到跨库行时输出 `INSTANCE_WIDE_SCOPE`（INFO），把实际执行了几个库告诉用户。

**表名含点号的边界**：拆分只在"点号左侧确为一个已知库名"时进行（已知库名来自
`SHOW DATABASES` 的**全量**结果，含系统库）。否则整串当作当前库下的表名。
这样既不会把 `db.tbl` 漏拆，也不会把 `odd.name` 这种表名误拆后当成"未知库"丢掉——
**误拆的后果是少算，而少算是不可见的错误**。

#### RISK-F：空结果集下命令可能挂起 —— **实测发现，未裁决**

使用者实测反馈：在赤兔"在线SQL"页面对 `lzbj_ecif`（该库没有单表）执行
`/*proxy*/show table without shardkey`，**页面一直转圈出不来结果**。

有两种可能，后果差别很大：
* **可能一（大概率）**：赤兔前端渲染零行结果集时的 UI 缺陷。那么走 PyMySQL 的本模块
  拿到的是 `[]`，完全正常。截图 1 里 mysql 客户端对 `sqltuning` 执行同一条命令
  0.01 秒返回 7 行，说明命令本身没毛病。
* **可能二**：Proxy 在无匹配表时确实不返回。那么本模块逐库遍历时，**每一个没有单表的
  库都会卡住**，直到超时。

**Rev.B 对策（两种可能下都不会拖垮请求）**：
1. 临时池显式设 `read_timeout = COMMAND_READ_TIMEOUT = 30s`，单条命令挂起最多卡 30 秒，
   然后抛超时 → 该库标 `FAILED`，`detail` 写"读超时（30s）"，循环继续；
2. 整体设 `TOTAL_BUDGET_SECONDS = 180s` 总预算，超出后剩余库标 `SKIPPED`
   （**不是 FAILED，也不计入总数**）并输出 `TIME_BUDGET_EXCEEDED`，提示用户分批统计。

**必须由 T15 判决走哪种可能**：如果是可能二，本模块的可用性会严重受损
（多数库都没有单表），届时需要改设计——例如只在 `information_schema` 显示该库
非空时才发第三条命令，或整体改成异步任务。**这是当前唯一可能推翻设计的未决项。**


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
2. SHOW DATABASES ──► all_dbs（含系统库，用作 known_dbs 判定库限定名）
                  ──► 过滤 _SYS_DB ──► dbs = [db1, db2, ...]
3. 一次性查 information_schema 全量名单（要名字，不只要计数）：
   SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE FROM information_schema.TABLES
   WHERE TABLE_SCHEMA IN (%s,%s,...)
        ──► baseline{db: {"base": {表名…}, "view": {视图名…}}}
4. 建临时池 tmp（pool_size=1, read_timeout=30s）
5. kind_map = {}        # (库, 表) -> 类型，全局去重，正是原厂"库名+表名去重"
   for db in dbs:
     ── 覆盖性跳过（RISK-E 对策 3）──
     if kind_map 中该库的表集 == baseline[db]["base"]:  skip（记 coverage）
     ── 时长预算 ──
     if 已耗时 > 180s:  该库标 SKIPPED，继续
     tmp.select_db(db)
     for kind, sql in (分片, 广播, 单表):
         rows = execute(sql)                       # 失败→该库 FAILED，break
         for 每行的 db_table 值:
             (qual, name) = 按 known_dbs 拆库限定名，缺省归当前库
             if qual 不在目标库集合:  丢弃        # 系统库 / 被筛掉的库
             if name 在 baseline[qual]["view"]:  丢弃   # 原厂：不统计视图
             kinds_seen[(qual,name)] 记录该 kind
             kind_map[(qual,name)] = 优先级更高者（分片 > 广播 > 单表）
6. tmp.close_all()
7. 逐库汇总 kind_map ──► items；总数 = len(kind_map)
   ── 交叉校验：每库 got 与 baseline[db]["base"] 做双向集合差 ──
   if 不等: RECON_MISMATCH（两侧差集表名各取前 20）
8. 告警 + 落库 + 返回
```

**恒等式（单测钉住）**：逐库与汇总均满足
`total_tables == shard_tables + broadcast_tables + single_tables`，
且 `汇总 total == len(kind_map)`——不会因为实例级作用域被放大 N 倍。


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
| 新增 | `backend/services/table_type_stats_service.py` | 574 行（附录 A.1，成品） |
| 新增 | `backend/api/table_type_stats.py` | 44 行（附录 A.2，成品） |
| 新增 | `backend/schema/v11/110_table_type_stats.sql` | 37 行（附录 A.3，成品） |
| 新增 | `tests/test_table_type_stats.py` | 594 行（附录 A.4，成品） |
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
  "skipped_databases": 0,
  "overlap_count": 0,
  "items": [
    {"db_name":"db_order","total_tables":100,"shard_tables":80,
     "broadcast_tables":5,"single_tables":15,
     "baseline_tables":100,"status":"OK","detail":""}
  ],
  "warnings": [
    {"code":"RECON_MISMATCH","severity":"WARNING","db_name":"db_x",
     "detail":"三类并集 97 张，information_schema 基线 100 张；仅基线可见(3): t_a, t_b, t_c"}
  ],
  "shape": {
    "shard":     ["db_table", "info"],
    "broadcast": ["db_table", "info"],
    "single":    ["db_table"]
  }
}
```

`items[].status` 三态：
* `OK` —— 正常统计；
* `FAILED` —— 该库三条命令中有失败（含读超时），**不计入任何汇总数**；
* `SKIPPED` —— 超出总时长预算未采集，**同样不计入汇总数**。
`skipped_databases` 与 `failed_databases` 分开计数：前者是"没来得及测"，
后者是"测了但错了"，处置动作不同，不能混成一个数。

`shape` 回传三条命令**实际的列名**，用于在换版本/换实例时快速判断解析是否踩空。

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
**三条常量逐字来自原厂，禁止改写、禁止拼接、禁止加分号、禁止 `.strip()`。**
尾部分号在原厂文本里是行终止符，PyMySQL 单语句执行不需要它；加上反而在部分
Proxy 版本会触发 multi-statement 检查。

```python
KIND_SHARD, KIND_BROADCAST, KIND_SINGLE = "shard", "broadcast", "single"
_KIND_SQL      = ((分片, SQL_SHARD), (广播, SQL_BROADCAST), (单表, SQL_SINGLE))
_KIND_PRIORITY = {shard: 0, broadcast: 1, single: 2}   # 归一化优先级，见 RISK-A

MAX_DATABASES        = 500   # 库数护栏，超出截断并告警
MAX_DIFF_SAMPLE      = 20    # 差集样本上限，防止 detail 撑爆 VARCHAR(512)
COMMAND_READ_TIMEOUT = 30    # 临时池单条命令读超时（秒），见 RISK-F
TOTAL_BUDGET_SECONDS = 180   # 采集总时长预算，超出即停并标 SKIPPED
```

`_SYS_DB`：`index_audit_service._SYS` ∪ `zk_scan_enrich_service.SYSTEM_DATABASES`，
硬编码为本模块自有 `frozenset`（**不 import 其他 service**，避免制造新的模块间耦合），
由单测钉住超集关系。

### 6.2 业务库枚举

```python
show_databases(pool)          -> list[str]     # 原始全量，含系统库
list_business_databases(pool) -> (业务库, 是否截断, 全量库名)
```
`SHOW DATABASES` 在 DictCursor 下列名为 `Database`（大小写随版本），取值方式与
`zk_scan_enrich_service.py:76-79` 一致，再兜底"取该行唯一值"。

**全量库名必须一并返回**，它是 `_split_qualified` 判定"点号左侧是不是库名"的依据
（RISK-E 的边界处理）。丢掉系统库名会让 `mysql.user` 这种行被当成
"当前库下名叫 `mysql.user` 的表"而误计。

超过 `MAX_DATABASES` 时截断并产生 `TOO_MANY_DATABASES` 告警——**截断必须可见**。

### 6.3 库限定名解析（应对 RISK-C / RISK-E）

实测形态：列名 `db_table`，值 `sqltuning.t_max`；两条命令另带 `info` 列。

**选列规则**（`_pick_name_column`，自上而下命中即停）：

| # | 规则 | 命中本次实测形态 |
|---|---|---|
| 1 | 只有 1 列 → 该列 | ✅ `without shardkey` |
| 2 | 列名（小写）∈ `db_table` / `table` / `table_name` / `tables` / `name` | ✅ `with shardkey` / `noshardkey_allset` 的 `db_table` |
| 3 | 列名以 `tables_in_` 开头 | 备用（标准 `SHOW TABLES` 形态） |
| 4 | 列名含 `table` 且不含 `type`/`rows`/`schema`/`comment`/`engine`/`key`/`info` | 兜底 |
| 5 | 都不满足 → 取第 1 列，并记 `SHAPE_UNKNOWN` 告警 | — |

`info` 被列入排除词，是为了在双列场景下**确定性地**避开它（虽然规则 2 已先命中，
但排除词让规则 4 的兜底路径也安全）。

**拆库限定名**（`_split_qualified(raw, current_db, known_dbs)`）：

```
s = str(值).strip()
若 s 含 "."：
    head, tail = s.split(".", 1)         # 只切第一个点
    去反引号
    若 head.lower() ∈ known_dbs 且 tail 非空 → 返回 (head, tail)
否则 → 返回 (current_db, 去反引号后的 s)
```

**为什么要用 `known_dbs` 判定而不是无条件拆**：无条件拆会把名叫 `odd.name` 的表
拆成库 `odd` + 表 `name`，而 `odd` 不在目标库集合里 → 该行被**静默丢弃** → 少算。
少算是不可见错误，比多算危险。用已知库名做判据，两种形态都不会错。

`_extract_pairs(rows, current_db, known_dbs)` 返回
`({(库, 表)}, 实际列名, 形态是否未知, 是否含跨库行)`。最后一项用于点亮
`INSTANCE_WIDE_SCOPE`。

### 6.4 采集

#### `_collect_baseline(pool, dbs)` —— 两个分支共用

```sql
SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE FROM information_schema.TABLES
WHERE TABLE_SCHEMA IN (%s, %s, ...)
```
返回 `{db: {"base": set(表名), "view": set(视图名)}}`。

**取名字而不是取 COUNT** 的四个理由：
1. 集中式分支要的 `single = len(base)` 直接可得；
2. 分布式分支的交叉校验需要**双向差集的表名**，光有计数说不出"差在哪张表"；
3. "不统计视图"这条原厂要求，在分布式分支上靠 `view` 名单做扣除来落实；
4. **覆盖性跳过的完备性证明**（RISK-E 对策 3）必须逐表比对，计数相等不等于内容相等。

**占位符风格**：这是打到**目标 TDSQL 实例**的查询，走 `pool._execute`（PyMySQL），
占位符是 `%s`；元数据库那侧（`database._get_connection`）才是 `?`。
本模块两侧都有，**不得混用**（§8 E-9）。

内存代价：单实例 5 万张表 ≈ 5MB Python 字符串，可接受。

#### `_collect_centralized(dbs, baseline)`
纯内存换算，不发任何查询、不发任何 `/*proxy*/` 命令：
`single = total = len(baseline[db]["base"])`，`shard = broadcast = 0`。
`VIEW` 天然不在 `base` 集合里，"不统计视图"自动成立。空库计 0（合法）。

#### `_collect_distributed(pool, dbs, baseline, known_dbs)`

```python
cfg = dataclasses.replace(pool.config, database=dbs[0],
                          read_timeout=COMMAND_READ_TIMEOUT)
tmp = _new_pool(cfg, pool_size=1)
try:
    with tmp.get_connection() as conn:
        for db in dbs:
            if 覆盖性成立: skipped[db] = "coverage"; continue
            if 超预算:     skipped[db] = "budget";   continue
            conn.select_db(db)          # 隔离连接，切库无副作用（ADR-3）
            for kind, sql in _KIND_SQL:
                cur.execute(sql) → 解析 → 归属 → 写入 kind_map
finally:
    tmp.close_all()
```

**逐库、逐命令独立 try**：单条失败只让**该库**降级为 `status='FAILED'`
（三条必须全成功才算 OK），记录 errno + 消息前 200 字符，继续下一个库。

**异常层次是强制要求，不得调整**：`TDSQLConnectionPool.get_connection()` 的
`except` 分支（`tdsql_connector.py:298-306`）在**任何异常穿出 `with` 块时**会关闭
并重建线程本地连接、然后重新抛出。因此本模块**必须在 `with` 块内部把每条命令的
异常吃掉**——否则一个库的 1064 会导致整条连接被重建、循环中断。

**`select_db` 失败**（库被删、无权限）→ 该库 `FAILED`，不影响其余库。

### 6.5 归属、去重与计数（应对 RISK-A / RISK-E）

不再"每库一组集合"，而是**一张全局映射**：

```python
kind_map[(库, 表)] = 类型          # 只保留优先级最高的类型
kinds_seen[(库, 表)] = {类型, ...}  # 用于统计重叠

写入规则：
  qual 不在目标库集合           → 丢弃（系统库 / 被 database 参数筛掉的库）
  name ∈ baseline[qual]["view"] → 丢弃（原厂：不统计视图）
  kind 优先级 < 已记录的         → 覆盖（分片 > 广播 > 单表）
  否则                          → 保留原值，但计入 kinds_seen

overlap_count = Σ (len(kinds_seen[k]) - 1)，对所有出现在 ≥2 类中的 k
```

逐库计数由 `kind_map` 反查得出，汇总 `total == len(kind_map)`。
恒等式（单测 `test_counts_are_consistent` 随机 200 组钉住）：

```
total_tables == shard_tables + broadcast_tables + single_tables   （逐库 & 汇总皆成立）
```

去重键为**精确大小写**的表名。理由：三个集合来自同一 Proxy 的同一会话，大小写必然
一致；而 `lower_case_table_names=0` 的实例上强行小写会把两张不同的表合并成一张，
造成**少算**。少算比多算危险（与项目"宁可多报不可漏报"一脉相承）。
**库名比对**则用小写（`known_dbs` / 目标库集合），因为 MySQL 库名在多数平台不区分
大小写，且库名来自我们自己枚举的清单，不存在合并风险。

### 6.6 交叉校验（应对 RISK-B）

对每个库，做该库在 `kind_map` 中的表集与 `baseline[db]["base"]` 的**双向集合差**
（不是比数字）：
* 两侧差集都为空 → 无告警。
* 任一侧非空 → `RECON_MISMATCH`（WARNING），`detail` 形如：
  `三类并集 97 张，information_schema 基线 100 张；仅基线可见(3): t_a, t_b, t_c`
  两侧样本各取 `MAX_DIFF_SAMPLE=20` 个并按名排序，超出部分写 `…等 N 张`；
  同时写入该库的 `table_type_stat_item.detail`（截断至 512 字节）。

比集合而不比计数：两个集合大小相同但内容不同（少了 A、多了 B）时，比计数会漏报——
这正是"不可见错误"的典型形态。而且**覆盖性跳过依赖的就是这个逐表比对**，
计数相等的版本会让跳过变得不安全。

集中式实例不做此校验（基线本身就是唯一数据源）。

### 6.7 告警清单（`warnings[]`）

| code | severity | 触发 | 用户该怎么办 |
|---|---|---|---|
| W1 `PROXY_CMD_FAILED` | ERROR | 某库三条命令中任一失败（含读超时） | 看 `detail` 的 errno；1064→连接可能不是 Proxy 端口；1045/1142→授权不足；读超时→见 RISK-F |
| W2 `KIND_OVERLAP` | WARNING | 三类集合有交集（RISK-A 命中） | 说明"三类互斥"在本版本不成立，已按优先级去重，总数仍正确 |
| W3 `RECON_MISMATCH` | WARNING | 并集 ≠ 基线（RISK-B 命中） | 两个数与双向差集表名都在 detail 里，人工判定 |
| W4 `SHAPE_UNKNOWN` | WARNING | 结果列形态未识别（RISK-C 兜底） | 把 `shape` 字段贴给开发，扩充 `_EXACT_NAME_COLS` |
| W5 `INSTANCE_TYPE_UNRELIABLE` | WARNING | `ctx.source == DEFAULT` 或 `ctx.conflict` | 实例类型是猜的/有冲突，口径可能整体走错分支；去实例管理页锁定后重跑 |
| W6 `NO_BUSINESS_DB` | INFO | 过滤后无业务库 | 账号权限过窄或实例确实空 |
| W7 `TOO_MANY_DATABASES` | WARNING | 库数 > 500，已截断 | 用 `database` 参数分批统计 |
| W8 `NOT_DISTRIBUTED_ENDPOINT` | ERROR | 已执行的库全部因 1064 失败 | 该连接大概率指向后端 TXSQL 而非 Proxy（§2.4） |
| W9 `INSTANCE_WIDE_SCOPE` | INFO | 结果含跨库行（RISK-E 命中） | 命令是实例级作用域，已按库归属并去重；顺带告知实际只执行了几个库 |
| W10 `TIME_BUDGET_EXCEEDED` | WARNING | 超出 180s 总预算 | 剩余库标 SKIPPED 未统计，请分批 |
| W11 `DB_ENUM_FAILED` | WARNING | `SHOW DATABASES` 失败但指定了 `database` | 降级为只统计该库；库限定名判据退化，可能影响跨库行归属 |

W5 的必要性：`resolve()` 在异常时**静默回落全局默认**
（`instance_type_service.py:112-114`）。如果一个真分布式实例被回落成"集中式"，
本模块会走 `information_schema` 分支，**分片表和广播表全报 0，而且看不出错**。
必须把这个不确定性顶到 UI 上。


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
| ADR-11 | 结果按**行内的库限定名**归属，不按当前会话库 | 全部算在当前遍历到的库上（Rev.A 做法） | 实测返回值是 `sqltuning.t_max`；若命令是实例级作用域，Rev.A 做法会让总数放大 N 倍且逐库明细全错（RISK-E）。按限定名归属在两种作用域下都正确，且不依赖 T13 的结论 |
| ADR-12 | **覆盖性跳过**：某库累计表集与 `information_schema` 基线逐表相等即跳过 | ① 检测到跨库行就 break ② 无条件跑满 N 库 | ①无法证明"一次已覆盖全部"，有漏库风险；②实例级作用域下要跑 3N 条重复命令，大实例不可接受。逐表相等是**完备性证明**而非启发式，既快又不会漏 |
| ADR-13 | 单条命令 `read_timeout=30s` + 整体 `180s` 预算 | 依赖连接默认 `read_timeout=10s` | RISK-F 未裁决前必须假设命令可能挂起。10s 对慢查询太紧、对挂起又缺总量控制；30s+180s 双层兜底使最坏情况可预期 |
| ADR-14 | 超预算的库标 `SKIPPED` 而非 `FAILED` | 统一标 FAILED | "没来得及测"和"测了但错了"处置动作不同：前者重跑/分批即可，后者要查权限或端口。混成一个数会误导排障方向 |

---

## 8. 异常与边界矩阵

| # | 场景 | 期望行为 | 落点 |
|---|---|---|---|
| E-1 | `connection_id` 不存在/未连接 | HTTP 400 `未连接TDSQL实例或连接不存在` | `api/table_type_stats.py::_pool` |
| E-2 | `database` 指定为系统库 | HTTP 400 `不允许统计系统库: xxx` | `run_stats` 入口校验 |
| E-3 | `database` 指定的库不存在 | 该库 `status='FAILED'`，detail 含 errno 1049，`failed_databases=1`，HTTP 200 | `_collect_distributed` |
| E-4 | `SHOW DATABASES` 失败 | 未指定 `database` → 抛出 → HTTP 500；已指定 → 降级为只统计该库 + W11 | `analyze` |
| E-5 | 某库三条命令之一 1064 | 该库 FAILED + W1；若**所有**库都是 1064 → 追加 W8 | `_collect_distributed` / `analyze` |
| E-6 | 某库权限不足（1045/1142/1044） | 该库 FAILED + W1，detail 提示"授权不足" | 同上 |
| E-7 | 业务库为 0 | HTTP 200，全 0，W6 | `analyze` |
| E-8 | 空库（有库无表） | 该库全 0，`status='OK'`，**不产生任何告警** | 正常路径 |
| E-9 | 占位符风格混用 | 目标实例侧 `%s`（PyMySQL）、元数据库侧 `?`。本模块目标侧无参数化查询，元数据库侧全 `?` | 附录 A.1 |
| E-10 | 命令返回 0 行 | 视为该类 0 张，合法，不告警（实测 `lzbj_ecif` 无单表即此形态） | `_extract_pairs` 返回空集 |
| E-11 | 库名含特殊字符 | 用 `conn.select_db(db)`（驱动层转义），**不拼 `USE \`{db}\``** | ADR-3 |
| E-12 | 统计过程中连接断开 | `finally: tmp.close_all()` 保证释放；已完成的库结果保留并返回 | `_collect_distributed` |
| E-13 | 实例类型解析异常 | `resolve` 自身回落全局默认，本模块检测 `source==DEFAULT` → W5 | `analyze` |
| E-14 | 库数 > 500 | 截断 + W7 | `list_business_databases` |
| E-15 | 元数据库落库失败 | HTTP 500，不返回半成品 | `run_stats` |
| E-16 | `stat_id` 不存在 | `/detail/{id}` 返回 `{"items":[],"warnings":[]}`，HTTP 200 | `get_detail` |
| E-17 | `warnings_json` 损坏 | `json.loads` 失败 → 返回 `[]`，不抛异常 | `get_detail` |
| E-18 | 结果含**其他业务库**的行 | 按限定名归到那个库；同一 `(库,表)` 全局只计一次；点亮 W9 | `_extract_pairs` / `kind_map` |
| E-19 | 结果含**系统库**的行（如 `mysql.user`） | 直接丢弃，不计入任何库 | `_collect_distributed` 的 `target` 过滤 |
| E-20 | 指定 `database=db_a` 但结果含 `db_b.*` | `db_b` 的行全部丢弃，只统计 `db_a` | 同上 |
| E-21 | 单条命令挂起 | 30s 读超时 → 该库 FAILED，detail 写"读超时（30s）"，循环继续 | `COMMAND_READ_TIMEOUT` |
| E-22 | 总耗时超 180s | 剩余库标 `SKIPPED`（不计入总数）+ W10；已采集的库正常返回 | `TOTAL_BUDGET_SECONDS` |
| E-23 | 表名本身含点号（如 `odd.name`） | 点号左侧不是已知库名 → 整串当作当前库下的表名，不误拆、不丢弃 | `_split_qualified` |

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

> **第一轮实测已于 2026-08-29 完成**，结论见 §10.1 裁决表，原始形态入附录 B。
> 本轮剩下 **3 个用例（T13 / T14 / T15）** 需要补测，其中 **T15 是唯一可能推翻设计的项**。
>
> 执行位置：**赤兔控制台 › 实例管理 › 在线SQL**，或用 `mysql` 客户端连**实例的 Proxy 端口**
> （与平台"实例管理"里登记的 host:port 完全一致）。
> **用 `mysql` 命令行客户端时必须加 `--comments`**，否则客户端会吃掉 `/*proxy*/` 前缀：
> ```
> mysql --comments -h <proxy_host> -P <proxy_port> -u <user> -p
> ```
> 赤兔"在线SQL"页面不需要这个参数。
>
> **回填方式**：把**原始输出整段贴回来**（不要摘要、不要改写）。

### 10.1 第一轮裁决表（已完成，无需重测）

| 用例 | 结论 | 对设计的影响 |
|---|---|---|
| T02 返回形态 | 列名 **`db_table`**；`with shardkey` / `with noshardkey_allset` 另有 `info` 列，`without shardkey` **只有一列**；值为**库限定名** `sqltuning.t_max` | `db_table` 已加入 `_EXACT_NAME_COLS` 首位，`info` 加入排除词；**并直接引出 RISK-E**，采集逻辑改为按库限定名归属 |
| T03 `/*proxy*/` 存活 | 截图 1 三条命令均正常返回（7 / 18 / 4 行，0.01 秒级） | 确认前缀有效，设计不变 |
| T04 三类互斥 | **互斥**。`without shardkey` 的 7 张与 `noshardkey_allset` 的 4 张无交集 | **RISK-A 证伪**；归一化去重作为保险保留，`KIND_OVERLAP` 在本版本不会触发 |
| T07 集中式行为 | 未测（截图为分布式实例） | 不阻断：集中式分支根本不发这三条命令（ADR-4） |
| T08 集中式口径 | 截图 2 证实 `information_schema.TABLES` 经 Proxy 可查、且**返回系统库**（`TABLE_SCHEMA: mysql`） | 确认按业务库白名单取用这一步是必需的 |
| — 新发现 | 赤兔对 `lzbj_ecif` 执行 `without shardkey`（该库无单表）**一直转圈** | **RISK-F**，由 T15 判决 |

### 10.2 本轮待测（3 项）

---

### T13 · 命令的作用域是实例级还是当前库？（**最高优先级，判决 RISK-E**）

**前提**：需要一个**至少有 2 个业务库**的分布式实例。若内网没有这种实例，请注明"无"，
并跳到 T14——设计在两种作用域下都正确，这条只是让我们知道实际走哪条路径、以及
性能量级差多少。

**执行**：
```sql
-- 先确认这个实例确实有 ≥2 个业务库
SHOW DATABASES;

-- 站在库 A 上执行
USE <库A>;
/*proxy*/show table with shardkey;
```

**回填**：
1. `SHOW DATABASES` 的完整输出；
2. 上面那条命令返回结果里，`db_table` 列**出现过几种不同的库前缀**？
   把不同前缀各举一例贴出来（例如同时出现 `库A.xxx` 和 `库B.yyy`）；
3. 总行数。

**我要看什么**：
* 若只出现 `库A.*` → 当前库作用域。逐库遍历，N 个库跑 3N 条命令。
* 若同时出现 `库A.*` 和 `库B.*` → **实例级作用域**。此时设计的"覆盖性跳过"生效，
  实际只会执行 3 条命令而不是 3N 条，响应时间与库数无关。

> **为什么我强烈怀疑是实例级**：返回值带库前缀这件事本身就没必要（只看当前库的话
> 裸表名就够了）；更关键的是原厂那句"使用『数据库名 + 表名』去重"——如果每次执行
> 只返回当前库，逐库遍历根本不会产生重复行，这句话无从谈起。

---

### T14 · 三类并集 vs `information_schema` 基线（判决 RISK-B）

**执行**（在 `sqltuning` 这类有数据的业务库上）：
```sql
USE <业务库名>;

SELECT COUNT(*) AS base_tables FROM information_schema.TABLES
WHERE TABLE_SCHEMA = '<业务库名>' AND TABLE_TYPE = 'BASE TABLE';

SELECT COUNT(*) AS views FROM information_schema.TABLES
WHERE TABLE_SCHEMA = '<业务库名>' AND TABLE_TYPE = 'VIEW';
```

**回填**：两个数字。

**我要看什么**：`base_tables` 是否等于三条命令的行数之和。
以截图 1 的 `sqltuning` 为例：18（分片）+ 4（广播）+ 7（单表）= **29**。
所以我要确认的就是：**`sqltuning` 的 `base_tables` 是不是 29？**

* 是 29 → RISK-B 不成立，`RECON_MISMATCH` 不会触发；
* 不是 29 → 请再跑一条拿差集明细：
  ```sql
  SELECT TABLE_NAME FROM information_schema.TABLES
  WHERE TABLE_SCHEMA = '<业务库名>' AND TABLE_TYPE = 'BASE TABLE'
  ORDER BY TABLE_NAME;
  ```
  把它和三条命令的表名清单比一比，**回填两侧的差集表名**。

> 顺带一个重要的排查点：如果 `base_tables` 远大于 29（比如是它的若干倍，且表名
> 长得像 `t1_0` / `t1_1` 这种带数字后缀的），说明经 Proxy 查 `information_schema`
> 看到的是**物理分片子表**而不是逻辑表。那样的话基线口径要换，请务必把表名样例贴回来。

---

### T15 · 空结果集到底是 UI 问题还是命令挂起？（**唯一可能推翻设计的项**）

**背景**：你反馈赤兔对没有单表的库执行 `without shardkey` 会一直转圈。需要确认这是
赤兔前端渲染零行的缺陷，还是 Proxy 真的不返回。

**执行**：**必须用 `mysql` 命令行客户端**（不能用赤兔页面，因为要绕开它的前端）：
```
mysql --comments -h <proxy_host> -P <proxy_port> -u <user> -p
```
```sql
USE lzbj_ecif;        -- 或任何一个"没有单表"的库
/*proxy*/show table without shardkey;
```

**回填**（三选一，照实说）：
* **A**：立刻返回 `Empty set (0.00 sec)` → **UI 问题**，本模块无影响；
* **B**：卡住不返回（等 30 秒以上），最终超时或需要 Ctrl+C → **命令挂起**；
* **C**：报错 → 把完整错误贴回来。

**顺便**：同一个 session 里再敲一次
```sql
/*proxy*/show table with noshardkey_allset;
```
确认卡住之后连接是否还能正常用。

**结论怎么用**：
* **A** → 设计不变。30s 读超时与 180s 预算作为纯保险留着，永不触发。
* **B** → **设计要改**。因为多数库都没有单表，逐库遍历会频繁挂 30 秒，
  N 个库最坏 30N 秒。改法有两条，届时我出 Rev.C：
  ① 只在 `information_schema` 显示该库确有表时才发第三条命令（治标）；
  ② 整个模块改成异步任务 + 进度条（治本，但要动 scheduler，爆炸半径变大）。
* **C** → 按错误内容定。

---

### 10.3 补充（有条件就测，没有就跳过）

| 用例 | 内容 | 用途 |
|---|---|---|
| T09 权限 | 用平台"实例管理"里**实际登记的那个账号**（不是 root）跑一遍 T02 的三条命令，回填能否成功 / errno | 登记账号跑不了的话，功能上线即报错——这是授权问题不是代码问题 |
| T10 性能 | 在**表最多**的业务库上跑三条命令，回填表数 + 各自耗时 | 若 T13 判定是当前库作用域，总响应时间 ≈ 单库耗时 × 库数；超阈值就要改异步 |
| T12 单分片实例 | 若内网有**只有 1 个 SET** 的分布式实例，在其上跑 T02 | `instance_probe_rules.py:99-104` 的已知边界：这类实例可能被判成集中式，从而分片表被报成单表。没有就注明"无" |

### 10.4 汇总回填模板

```
【T13】SHOW DATABASES 输出：
【T13】库前缀种类（只有库A / 含库B…）：
【T13】总行数：
【T14】base_tables= ，views= ，是否等于三条命令行数之和：
【T14】（不等时）双向差集表名 / 表名是否带分片数字后缀：
【T15】选 A / B / C：
【T15】原始输出或错误：
【T15】卡住后连接是否还可用：
【T09】登记账号能否执行（是/否，errno）：
【T10】最大库表数 / 三条命令耗时：
【T12】单分片实例：有/无，若有则输出：
```

### 10.5 GATE-2 放行判据（本轮实测结论 → 设计动作）

| 实测结论 | 设计动作 | 是否阻断开发 |
|---|---|---|
| T13 = 当前库作用域 | 覆盖性跳过永不成立，逐库执行；`INSTANCE_WIDE_SCOPE` 不触发。代码无需改 | 否 |
| T13 = 实例级作用域 | 覆盖性跳过生效，3 条命令搞定；`INSTANCE_WIDE_SCOPE` 会显示——**符合设计预期** | 否 |
| T13 无多库实例可测 | 两条路径都已实现且都有单测覆盖，按现状开发 | 否 |
| T14 并集 == 基线 | `RECON_MISMATCH` 不触发 | 否 |
| T14 不等 | `RECON_MISMATCH` 生效并列差集——**符合设计预期** | 否 |
| T14 基线看到的是**物理分片子表** | 基线口径失效，需要换交叉校验数据源（或去掉该校验） | **是**（设计升 Rev.C） |
| **T15 = A（UI 问题）** | 设计不变，超时保险留作兜底 | 否 |
| **T15 = B（命令挂起）** | 必须改：加"基线非空才发命令"的短路，或改异步任务 | **是**（设计升 Rev.C） |
| T09 登记账号无权限 | 出授权说明，由 DBA 补授权 | **是**（非代码问题） |
| T10 单库 > 1s 且 T13 = 当前库作用域 且库数 > 20 | 需追加"异步任务 + 进度"设计 | **是**（设计升 Rev.C） |
| T12 存在单分片分布式实例 | UI 强化 W5 文案 | 否（前端 1 行文案） |

**只要没有命中"是"，开发即可按附录 A 照图施工。**

---


## 11. 测试设计（开发期，可在本地 MariaDB 13306 上跑）

`tests/test_table_type_stats.py`（附录 A.4），**35 项，除落库 2 项外全部离线，
不依赖真实 TDSQL**。数据夹具直接照搬 2026-08-29 内网实测形态（列名 `db_table`、
库限定名 `sqltuning.t_max`、`with*` 双列 / `without` 单列）。

| 用例 | 验证 | 护栏对象 |
|---|---|---|
| `test_sql_constants_verbatim` | 三条命令逐字等于原厂文本，以 `/*proxy*/` 开头、无分号 | ADR-10 |
| `test_sys_db_is_superset` | `_SYS_DB` 同时是项目内两套系统库清单的超集 | ADR-8 |
| `test_pick_column_prefers_db_table_over_info` | 双列时必须选 `db_table` 不选 `info` | 实测形态 |
| `test_extract_pairs_real_shapes`（5 组参数） | 单列 / 双列 / 反引号 / 空集，均正确解析出 `(库, 表)` | RISK-C |
| `test_extract_pairs_detects_cross_database_rows` | 含跨库行时点亮 `cross` 标志 | RISK-E |
| `test_extract_pairs_keeps_dotted_table_name` | `odd.name` 不被误拆（左侧非已知库名） | E-23 |
| `test_extract_pairs_unknown_shape` | 未知列形态 → 取第 1 列 + `SHAPE_UNKNOWN` | W4 |
| `test_business_databases_filter_system` | 过滤系统库，且**全量库名一并返回** | §6.2 |
| `test_business_databases_truncation_is_visible` | 超 `MAX_DATABASES` 必须告警 | W7 |
| `test_centralized_branch` | 分片/广播恒 0，视图不计，**未发任何 `/*proxy*/`、未切库** | ADR-4 |
| `test_distributed_happy_path` | 照搬 `sqltuning` 实测形态，2/1/2/5，无告警；临时池被关闭且 `read_timeout=30` | ADR-3 / ADR-13 |
| `test_distributed_instance_wide_scope` | 实例级作用域：总数按 `(库,表)` 去重不放大；**只切库一次**；点亮 W9 | **RISK-E 核心护栏** |
| `test_distributed_per_db_scope_still_loops` | 当前库作用域：覆盖性不成立，逐库执行，不点 W9 | ADR-12 |
| `test_single_database_filter_ignores_other_dbs` | 指定库时，实例级结果里其他库的行必须丢弃 | E-20 |
| `test_system_db_rows_are_dropped` | `mysql.user` / `sysdb.foo` 不得计入 | E-19 |
| `test_distributed_view_is_excluded` | 命令返回视图时按基线 VIEW 名单扣除 | 原厂口径 |
| `test_distributed_overlap_does_not_double_count` | 若三类重叠，总数不重复计算 + W2 | RISK-A 保险 |
| `test_distributed_recon_mismatch` | 双向差集写进 warning 与 `item.detail` | RISK-B |
| `test_distributed_partial_failure` | 单库失败只降级该库，其余库照常，`failed_databases=1` | ADR-5 |
| `test_command_timeout_is_reported_not_hung` | 读超时渲染为"读超时（30s）"而非裸异常 | RISK-F / E-21 |
| `test_time_budget_skips_remaining` | 超预算的库标 `SKIPPED`、不计入总数、W10 | ADR-14 / E-22 |
| `test_distributed_all_1064_flags_wrong_endpoint` | 全 1064 → W8 | §2.4 |
| `test_select_db_failure_is_isolated` | 切库失败只影响该库，临时池仍被关闭 | E-3 |
| `test_shared_pool_is_never_switched` | **共享池连接上不得发生任何 `select_db`** | **ADR-3 核心护栏** |
| `test_empty_result_set_is_not_an_error` | 空结果集 = 合法的 0，不告警（对应 `lzbj_ecif` 无单表） | E-10 |
| `test_counts_are_consistent` | 随机 200 组，逐库与汇总恒等式恒成立 | §6.5 |
| `test_no_business_db_warns` / `test_unreliable_instance_type_warns` | W6 / W5 | — |
| `test_reject_system_database` | `database='mysql'` → `ValueError` → API 400 | E-2 |
| `test_run_stats_persists` | 落库后明细行数与 items 一致，warnings 可反序列化，history 可回看 | REQ-6 |
| `test_get_detail_missing_id_is_graceful` | 不存在的 `stat_id` 返回空结构不抛异常 | E-16 |

**FakePool 设计**（关键，使全部分布式逻辑可离线测试）：脚本化
`databases` / `info_schema` / `per_db[(当前库, sql)]`，并记录 `seen`（所有执行过的
SQL）与 `selected`（所有切库动作）——后者正是 ADR-3 护栏的断言依据。
临时池的构造点是模块级钩子 `_new_pool = TDSQLConnectionPool`，测试里 monkeypatch
该名字即可注入 FakePool；**这是唯一为可测性做的让步，成本 1 行**。

**本地验证结果（2026-08-29）**：用 importlib 把附录 A.1 挂载为
`backend.services.table_type_stats_service`（**仓库代码零改动**），
`python -m pytest` **35 项全部通过**，含对本地 MariaDB(13306) 的真实落库用例。


## 12. 验收清单

### 12.1 功能

- [ ] 深度诊断页出现"表类型统计"页签，位于"索引体检"与"结构比对"之间
- [ ] 未选实例时按钮禁用；选实例后可点击
- [ ] 分布式实例：返回逐库 4 个数 + 汇总行，`total == shard+broadcast+single`
- [ ] 集中式实例：分片列、广播列全 0，单表列 == `information_schema` `BASE TABLE` 数
- [ ] 指定 `database` 时只统计该库
- [ ] 指定系统库时返回 400 且提示明确
- [ ] 告警区在触发时可见（至少构造 W5 / W6 两种验证）
- [ ] 分布式实例上核对：逐库 `total == shard + broadcast + single`，汇总同样成立
- [ ] 若结果出现 `INSTANCE_WIDE_SCOPE`（W9），核对总表数**没有**被库数放大
- [ ] 失败库与未采集库分别计入 `failed_databases` / `skipped_databases`，且都不进总数
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
| KL-6 | 统计为同步执行 | 库数 × 3 条命令，大实例可能较慢 | T10 定量；若超阈值则升版为异步任务（GATE-2 阻断项）。若 T13 判定为实例级作用域，覆盖性跳过会把命令数压到 3 条，本项自然消解 |
| KL-7 | 结果为快照，不反映采集期间的 DDL 变更 | 无事务一致性保证 | 结果带 `created_at`，UI 标注"采集时刻快照" |
| KL-8 | 命令作用域（当前库 / 实例级）未裁决 | 返回库限定名 + 原厂"库名+表名去重"的措辞都指向实例级，但缺少多业务库实例的实测 | 设计在两种作用域下都正确（ADR-11/12）；T13 只影响性能量级与 W9 是否显示 |
| KL-9 | 空结果集是否导致命令挂起未裁决 | 赤兔页面对无单表的库一直转圈，原因未定 | 30s 读超时 + 180s 总预算兜底；T15 判决，若为 B 则升 Rev.C |
| KL-10 | 经 Proxy 查 `information_schema` 看到的是逻辑表还是物理分片子表未确证 | 截图 2 只翻到 `mysql` 库 | 若为物理子表，交叉校验基线口径失效；T14 判决 |
| KL-11 | `info` 列内容（shardkey / sub_shardkey / auto_increment）本期未使用 | 形态已入附录 B | 为将来"分片键分布"类需求预留，不在本期范围 |

---

## 14. 附录 A · 成品代码（照图施工）

> **本附录四个文件已在本地环境完整验证**：用 importlib 把 A.1 挂载为
> `backend.services.table_type_stats_service`（**仓库代码零改动**），
> `python -m pytest` **35 项全部通过**，其中含对本地 MariaDB(13306) 的真实落库用例；
> A.2 的路由在 FastAPI 下正确注册出 3 条路径。实施者可直接落盘，不需要二次设计。
>
> **Rev.B 相对 Rev.A 的实质变化**（均源自 2026-08-29 内网实测）：
> 1. `_EXACT_NAME_COLS` 首位加入实测确认的列名 `db_table`，`info` 加入排除词；
> 2. `_extract_names` → `_extract_pairs`：解析 **`(库, 表)` 二元组**而不是裸表名，
>    并回报是否含跨库行（RISK-E）；
> 3. 采集从"每库三个集合"改为**全局 `kind_map[(库,表)]`**，按行内库限定名归属、
>    全局去重、逐库反查计数（ADR-11）；
> 4. 新增**覆盖性跳过**（ADR-12）：某库累计表集与 `information_schema` 基线逐表相等
>    即跳过其命令——实例级作用域下把 3N 条命令压到 3 条；
> 5. 新增 `COMMAND_READ_TIMEOUT=30` / `TOTAL_BUDGET_SECONDS=180` 双层时长兜底
>    与 `SKIPPED` 状态（ADR-13/14，应对 RISK-F）；
> 6. 落库表与响应新增 `skipped_databases` 字段。
>
> 唯一可能再变的是 T14 / T15 的结论（§10.5 GATE-2 的两个"是"）。

### A.1 `backend/services/table_type_stats_service.py`（新增，574 行）

```python
# -*- coding: utf-8 -*-
"""G14 · 表类型统计（深度诊断子模块，DESIGN-v1.6.3.0 Rev.B）

按 TDSQL 原厂口径统计单个实例下各业务库的表类型分布：

  分布式实例——逐业务库执行三条 Proxy 命令：
      /*proxy*/show table with shardkey           → 分片表
      /*proxy*/show table with noshardkey_allset  → 广播表
      /*proxy*/show table without shardkey        → 单表
  集中式实例——information_schema.TABLES 中 TABLE_TYPE='BASE TABLE' 计入单表，
      分片表/广播表恒为 0，视图不计。

2026-08-29 内网实测（设计附录 B）确定的形态：
  · 返回列名为 db_table，值为【库限定名】 sqltuning.t_max；
    with shardkey / with noshardkey_allset 另有第二列 info（shardkey:xxx），
    without shardkey 只有一列。
  · 三类结果集互斥（without shardkey 不含广播表）。

设计要点（详见 DESIGN-v1.6.3.0）：
  · 结果按【库限定名】归属到库，而不是无条件算在当前会话库上——
    命令的作用域是否为实例级尚未确证，按库归属 + (库,表) 去重使两种
    作用域都得到正确结果（§3.3 RISK-E）。这也正是原厂"使用数据库名+表名
    去重"这句话的由来。
  · 覆盖性跳过：某库的累计结果已与 information_schema 基线逐表一致时，
    不必再对该库执行命令——在实例级作用域下把 3×N 条命令压到 3 条。
  · 总时长预算 + 显式读超时：命令挂起不会拖垮整个请求（§3.3 RISK-F）。
  · 绝不在共享连接池上切库；另建 pool_size=1 的临时池（ADR-3）。

全部只读。不修改任何既有模块。
"""
from __future__ import annotations

import dataclasses
import json
import logging
import time
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
_KIND_PRIORITY = {KIND_SHARD: 0, KIND_BROADCAST: 1, KIND_SINGLE: 2}

# 系统库口径 = index_audit_service._SYS ∪ zk_scan_enrich_service.SYSTEM_DATABASES。
# 本模块自持、不 import 其他 service（ADR-8）；超集关系由单测钉住。
_SYS_DB = frozenset({
    "information_schema", "mysql", "performance_schema", "sys",
    "sysdb", "query_rewrite", "xa",
    "tdsqlpcloud", "tdsqlpcloud_monitor", "__tencentdb__",
})

MAX_DATABASES = 500           # 库数护栏。超出即截断并显式告警（绝不静默少算）
MAX_DIFF_SAMPLE = 20          # 差集样本上限，防止 detail 撑爆 VARCHAR(512)
COMMAND_READ_TIMEOUT = 30     # 临时池单条命令读超时（秒）。防命令挂起（RISK-F）
TOTAL_BUDGET_SECONDS = 180    # 采集总时长预算。超出即停并把剩余库标 SKIPPED

# 表名列识别规则（§6.3）。自上而下，命中即停。
# db_table 为 2026-08-29 内网实测确认的真实列名（附录 B）。
_EXACT_NAME_COLS = ("db_table", "table", "table_name", "tables", "name")
_PREFIX_NAME_COLS = ("tables_in_",)
_EXCLUDE_TOKENS = ("type", "rows", "schema", "comment", "engine", "key", "info")

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
    low = msg.lower()
    if "timed out" in low or "timeout" in low:
        return f"读超时（{COMMAND_READ_TIMEOUT}s）：{msg}"
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


def _split_qualified(raw, current_db: str, known_dbs: set):
    """把 db_table 值拆成 (库名, 表名)。

    实测形态为 `sqltuning.t_max`。仅当点号左侧确为一个【已知库名】时才拆分，
    否则整体视为当前库下的表名——避免把含点号的表名误拆后被过滤掉（少算）。
    """
    s = str(raw if raw is not None else "").strip()
    if not s:
        return current_db, ""
    if "." in s:
        head, tail = s.split(".", 1)
        head = head.strip().strip("`").strip()
        tail = tail.strip().strip("`").strip()
        if head and tail and head.lower() in known_dbs:
            return head, tail
    return current_db, s.strip("`").strip()


def _extract_pairs(rows, current_db: str, known_dbs: set):
    """提取 {(库名, 表名)}。返回 (集合, 实际列名, 形态是否未知, 是否含跨库行)。"""
    rows = rows or []
    if not rows:
        return set(), [], False, False
    first = rows[0]
    if isinstance(first, dict):
        columns = list(first.keys())
        col, guessed = _pick_name_column(columns)
        values = [r.get(col) if isinstance(r, dict) else None for r in rows]
    else:
        columns, guessed = [], False
        values = [(r[0] if r else None) for r in rows]
    pairs, cross = set(), False
    for v in values:
        qual, name = _split_qualified(v, current_db, known_dbs)
        if not name:
            continue
        pairs.add((qual, name))
        if qual.lower() != current_db.lower():
            cross = True
    return pairs, [str(c) for c in columns], guessed, cross


# ══════════════════════════════════════════════════════════════════
# 采集
# ══════════════════════════════════════════════════════════════════
def show_databases(pool) -> list:
    """SHOW DATABASES 原始结果（含系统库）。失败抛出。"""
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
        if name:
            names.append(name)
    return names


def list_business_databases(pool):
    """枚举业务库。返回 (业务库列表, 是否被 MAX_DATABASES 截断, 全部库名)。"""
    allnames = show_databases(pool)
    names = [n for n in allnames if n.lower() not in _SYS_DB]
    names.sort(key=lambda s: (s.lower(), s))
    truncated = len(names) > MAX_DATABASES
    return names[:MAX_DATABASES], truncated, allnames


def _collect_baseline(pool, dbs: list) -> dict:
    """取 information_schema 全量名单。返回 {db: {"base": set, "view": set}}。

    要名字不要计数：集中式分支取 len(base)；分布式分支需要名字做视图扣除、
    双向集合差、以及覆盖性跳过的完备性证明（§6.4 / §6.6 / §6.7）。
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


def _blank_item(db: str, baseline: dict) -> dict:
    return {"db_name": db, "total_tables": 0, "shard_tables": 0,
            "broadcast_tables": 0, "single_tables": 0,
            "baseline_tables": len(baseline.get(db, {}).get("base", ())),
            "status": "OK", "detail": ""}


def _collect_centralized(dbs: list, baseline: dict):
    """集中式：纯内存换算，不发任何查询，不发任何 /*proxy*/ 命令（ADR-4）。"""
    items = []
    totals = {"shard": 0, "broadcast": 0, "single": 0,
              "total": 0, "overlap": 0, "failed": 0, "skipped": 0}
    for db in dbs:
        n = len(baseline.get(db, {}).get("base", ()))
        item = _blank_item(db, baseline)
        item["total_tables"] = n
        item["single_tables"] = n
        items.append(item)
        totals["single"] += n
        totals["total"] += n
    return items, [], {}, totals


def _collect_distributed(pool, dbs: list, baseline: dict, known_dbs: set):
    """分布式：逐业务库执行三条 /*proxy*/ 命令，按【库限定名】归属去重。

    连接隔离：另建 pool_size=1 的临时池，切库不污染共享池（ADR-3）。
    异常隔离：所有异常都在 with 块内部吃掉——若让异常穿出
      TDSQLConnectionPool.get_connection() 的 with，池会重建连接并中断循环。
    时长兜底：单条命令读超时 COMMAND_READ_TIMEOUT，整体不超过 TOTAL_BUDGET_SECONDS。
    """
    items, warnings, shape = [], [], {}
    totals = {"shard": 0, "broadcast": 0, "single": 0,
              "total": 0, "overlap": 0, "failed": 0, "skipped": 0}
    if not dbs:
        return items, warnings, shape, totals

    target = {d.lower() for d in dbs}
    canon = {d.lower(): d for d in dbs}
    kind_map = {}          # (db, table) -> kind
    kinds_seen = {}        # (db, table) -> {kind, ...}
    failed, skipped = {}, {}
    shape_reported = False
    instance_wide = False
    syntax_errors = 0
    scanned = 0
    started = time.monotonic()

    cfg = dataclasses.replace(pool.config, database=dbs[0],
                              read_timeout=COMMAND_READ_TIMEOUT)
    tmp = _new_pool(cfg, pool_size=1)
    try:
        with tmp.get_connection() as conn:
            for db in dbs:
                base = baseline.get(db, {}).get("base", set())
                acc = {t for (d, t) in kind_map if d == db}
                # 覆盖性跳过：已采到的该库表集与元数据基线逐表一致 ⇒ 已完备
                if acc and acc == base:
                    skipped[db] = "coverage"
                    continue
                if time.monotonic() - started > TOTAL_BUDGET_SECONDS:
                    skipped[db] = "budget"
                    continue

                scanned += 1
                detail = ""
                try:
                    conn.select_db(db)
                except Exception as e:                       # noqa: BLE001
                    detail = f"切换数据库失败: {_err(e)}"
                    if _errno_of(e) == _SYNTAX_ERRNO:
                        syntax_errors += 1
                if not detail:
                    for kind, sql in _KIND_SQL:
                        try:
                            with conn.cursor() as cur:
                                cur.execute(sql)
                                rows = cur.fetchall()
                        except Exception as e:               # noqa: BLE001
                            detail = f"{sql} 执行失败: {_err(e)}"
                            if _errno_of(e) == _SYNTAX_ERRNO:
                                syntax_errors += 1
                            break
                        pairs, columns, guessed, cross = _extract_pairs(
                            rows, db, known_dbs)
                        if columns and kind not in shape:
                            shape[kind] = columns
                        if cross:
                            instance_wide = True
                        if guessed and not shape_reported:
                            shape_reported = True
                            warnings.append(_warn(
                                "SHAPE_UNKNOWN", "WARNING", db,
                                f"未能识别表名列，已退化为取第一列；实际列名: {columns}"))
                        for qual, name in pairs:
                            low = qual.lower()
                            if low not in target:
                                continue              # 非目标库（系统库/被筛掉的库）
                            owner = canon[low]
                            if name in baseline.get(owner, {}).get("view", ()):
                                continue              # 原厂口径：不统计视图
                            key = (owner, name)
                            kinds_seen.setdefault(key, set()).add(kind)
                            cur_kind = kind_map.get(key)
                            if (cur_kind is None
                                    or _KIND_PRIORITY[kind] < _KIND_PRIORITY[cur_kind]):
                                kind_map[key] = kind
                if detail:
                    failed[db] = detail[:512]
    finally:
        try:
            tmp.close_all()
        except Exception:                                    # noqa: BLE001
            logger.debug("临时连接池关闭失败（忽略）", exc_info=True)

    # ── 组装逐库结果 ──────────────────────────────────────────────
    per_db = {d: {KIND_SHARD: 0, KIND_BROADCAST: 0, KIND_SINGLE: 0} for d in dbs}
    for (d, _t), kind in kind_map.items():
        per_db[d][kind] += 1
    overlap_total = sum(len(v) - 1 for v in kinds_seen.values() if len(v) > 1)

    for db in dbs:
        item = _blank_item(db, baseline)
        if db in failed:
            item["status"] = "FAILED"
            item["detail"] = failed[db]
            totals["failed"] += 1
            warnings.append(_warn("PROXY_CMD_FAILED", "ERROR", db, failed[db]))
            items.append(item)
            continue
        if skipped.get(db) == "budget":
            item["status"] = "SKIPPED"
            item["detail"] = f"超出总时长预算 {TOTAL_BUDGET_SECONDS}s，未采集"
            totals["skipped"] += 1
            items.append(item)
            continue

        c = per_db[db]
        item["shard_tables"] = c[KIND_SHARD]
        item["broadcast_tables"] = c[KIND_BROADCAST]
        item["single_tables"] = c[KIND_SINGLE]
        item["total_tables"] = c[KIND_SHARD] + c[KIND_BROADCAST] + c[KIND_SINGLE]
        if skipped.get(db) == "coverage":
            item["detail"] = "结果由实例级命令一次性覆盖，未单独执行"
        totals["shard"] += item["shard_tables"]
        totals["broadcast"] += item["broadcast_tables"]
        totals["single"] += item["single_tables"]
        totals["total"] += item["total_tables"]

        got = {t for (d, t) in kind_map if d == db}
        base = baseline.get(db, {}).get("base", set())
        only_proxy, only_base = got - base, base - got
        if only_proxy or only_base:
            d2 = (f"三类并集 {len(got)} 张，information_schema 基线 {len(base)} 张")
            if only_base:
                d2 += f"；仅基线可见({len(only_base)}): {_diff_sample(only_base)}"
            if only_proxy:
                d2 += f"；仅 Proxy 可见({len(only_proxy)}): {_diff_sample(only_proxy)}"
            item["detail"] = d2[:512]
            warnings.append(_warn("RECON_MISMATCH", "WARNING", db, d2))
        items.append(item)

    totals["overlap"] = overlap_total
    if overlap_total:
        warnings.append(_warn(
            "KIND_OVERLAP", "WARNING", "",
            f"三类结果集存在 {overlap_total} 处重叠，"
            f"已按 分片>广播>单表 归一化去重，总数未重复计算"))
    if instance_wide:
        warnings.append(_warn(
            "INSTANCE_WIDE_SCOPE", "INFO", "",
            f"本版本 /*proxy*/show table 返回实例级全量（结果含跨库行），"
            f"已按库限定名归属并按(库,表)去重；"
            f"{len(dbs)} 个业务库中实际执行了 {scanned} 个，其余由覆盖性判定跳过"))
    if totals["skipped"]:
        warnings.append(_warn(
            "TIME_BUDGET_EXCEEDED", "WARNING", "",
            f"采集超出总时长预算 {TOTAL_BUDGET_SECONDS}s，"
            f"{totals['skipped']} 个库未采集（已标 SKIPPED，不计入总数）；"
            f"请用「库名」输入框分批统计"))
    if dbs and syntax_errors >= scanned > 0:
        warnings.append(_warn(
            "NOT_DISTRIBUTED_ENDPOINT", "ERROR", "",
            "全部已执行的业务库均因语法错误(1064)失败：该连接可能指向后端 TXSQL "
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
    try:
        business, truncated, allnames = list_business_databases(pool)
    except Exception as e:                                   # noqa: BLE001
        if not database:
            raise
        business, truncated, allnames = [database], False, [database]
        warnings.append(_warn("DB_ENUM_FAILED", "WARNING", "",
                              f"SHOW DATABASES 失败，仅统计指定库: {_err(e)}"))
    known_dbs = {n.lower() for n in allnames} or {database.lower()}
    dbs = [database] if database else business

    if truncated and not database:
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
        items, warns, shape, totals = _collect_distributed(
            pool, dbs, baseline, known_dbs)
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
        "skipped_databases": totals["skipped"],
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
            "skipped_databases, overlap_count, warnings_json, created_by) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (connection_id, database, res["instance_type"], res["type_source"],
             res["database_count"], res["total_tables"], res["shard_tables"],
             res["broadcast_tables"], res["single_tables"],
             res["failed_databases"], res["skipped_databases"],
             res["overlap_count"],
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

### A.2 `backend/api/table_type_stats.py`（新增，44 行）

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

### A.3 `backend/schema/v11/110_table_type_stats.sql`（新增，38 行）

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
    skipped_databases   INT DEFAULT 0,
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

### A.4 `tests/test_table_type_stats.py`（新增，594 行）

```python
# -*- coding: utf-8 -*-
"""G14 · 表类型统计 回归测试（DESIGN-v1.6.3.0 Rev.B §11）

除落库两例外全部离线，不依赖真实 TDSQL 实例。
数据夹具取自 2026-08-29 内网实测（设计附录 B）：列名 db_table，值为库限定名。
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

    databases   : SHOW DATABASES 返回的库名（含系统库）
    info_schema : {db: {"base":[...], "view":[...]}}
    per_db      : {(当前库, sql): 行列表 或 Exception}
    """

    def __init__(self, databases=None, info_schema=None, per_db=None,
                 select_db_fail=None):
        self.config = TDSQLConnectionConfig(host="h", port=3306, user="u",
                                            password="p", database="d")
        self.databases = databases or []
        self.info_schema = info_schema or {}
        self.per_db = per_db or {}
        self.select_db_fail = select_db_fail or {}
        self.seen, self.selected = [], []
        self.current_db = ""
        self.closed = False
        self.made_with_read_timeout = None

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

    def get_connection(self):
        pool = self

        class _Cursor:
            def __enter__(self_i):
                return self_i

            def __exit__(self_i, *a):
                return False

            def execute(self_i, sql, params=None):
                pool.seen.append(sql)
                val = pool.per_db.get((pool.current_db, sql))
                if isinstance(val, Exception):
                    self_i._rows = []
                    raise val
                self_i._rows = val or []

            def fetchall(self_i):
                return getattr(self_i, "_rows", [])

        class _Conn:
            def select_db(self_i, db):
                pool.selected.append(db)
                if db in pool.select_db_fail:
                    raise pool.select_db_fail[db]
                pool.current_db = db

            def cursor(self_i):
                return _Cursor()

        class _Ctx:
            def __enter__(self_i):
                return _Conn()

            def __exit__(self_i, *a):
                return False

        return _Ctx()

    def close_all(self):
        self.closed = True


def _rows(qualified_names, col="db_table", info=None):
    """按实测形态构造行：列名 db_table，值为 db.table；info 为可选第二列。"""
    if info is None:
        return [{col: n} for n in qualified_names]
    return [{col: n, "info": info} for n in qualified_names]


def _mysql_error(errno, msg):
    return Exception(errno, msg)


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
    def _factory(cfg, pool_size=1):
        pool.made_with_read_timeout = cfg.read_timeout
        return pool
    monkeypatch.setattr(svc, "_new_pool", _factory)


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
# 形态解析（锚定内网实测：db_table + 库限定名）
# ══════════════════════════════════════════════════════════════════
def test_pick_column_prefers_db_table_over_info():
    """实测形态：with shardkey 返回 db_table + info 两列，必须取 db_table"""
    col, guessed = svc._pick_name_column(["db_table", "info"])
    assert col == "db_table" and guessed is False


@pytest.mark.parametrize("rows,expect", [
    # 实测：without shardkey 单列
    (_rows(["sqltuning.t_max", "sqltuning.txt"]),
     {("sqltuning", "t_max"), ("sqltuning", "txt")}),
    # 实测：with shardkey 双列
    (_rows(["sqltuning.t1"], info="shardkey:id"), {("sqltuning", "t1")}),
    # 实测：with noshardkey_allset 双列
    (_rows(["sqltuning.kcda_bcast"], info="shardkey:noshardkey_allset"),
     {("sqltuning", "kcda_bcast")}),
    # 反引号
    ([{"db_table": "`sqltuning`.`t_max`"}], {("sqltuning", "t_max")}),
    # 空结果
    ([], set()),
])
def test_extract_pairs_real_shapes(rows, expect):
    pairs, _c, guessed, _x = svc._extract_pairs(
        rows, "sqltuning", {"sqltuning", "mysql"})
    assert pairs == expect and guessed is False


def test_extract_pairs_detects_cross_database_rows():
    """结果含其他库 ⇒ 命令作用域为实例级（RISK-E）"""
    pairs, _c, _g, cross = svc._extract_pairs(
        _rows(["db_a.t1", "db_b.t2"]), "db_a", {"db_a", "db_b"})
    assert cross is True
    assert pairs == {("db_a", "t1"), ("db_b", "t2")}


def test_extract_pairs_keeps_dotted_table_name():
    """点号左侧不是已知库名时不得拆分——避免误拆后被过滤掉（少算）"""
    pairs, _c, _g, cross = svc._extract_pairs(
        [{"db_table": "odd.name"}], "db_a", {"db_a", "db_b"})
    assert pairs == {("db_a", "odd.name")} and cross is False


def test_extract_pairs_unknown_shape():
    pairs, columns, guessed, _x = svc._extract_pairs(
        [{"col_x": "t_a", "col_y": 1}], "db", {"db"})
    assert pairs == {("db", "t_a")} and guessed is True
    assert columns == ["col_x", "col_y"]


# ══════════════════════════════════════════════════════════════════
# 业务库枚举
# ══════════════════════════════════════════════════════════════════
def test_business_databases_filter_system():
    pool = FakePool(databases=["db_a", "mysql", "sysdb", "xa",
                               "information_schema", "tdsqlpcloud", "db_b"])
    dbs, truncated, allnames = svc.list_business_databases(pool)
    assert dbs == ["db_a", "db_b"] and truncated is False
    assert len(allnames) == 7          # known_dbs 必须含系统库


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
    assert all("/*proxy*/" not in s for s in pool.seen)   # ADR-4
    assert pool.selected == []                            # 不切库


# ══════════════════════════════════════════════════════════════════
# 分布式分支（库限定名口径）
# ══════════════════════════════════════════════════════════════════
def test_distributed_happy_path(monkeypatch):
    """完全照搬内网 sqltuning 实测的三份结果形态"""
    _patch_ctx(monkeypatch, "distributed")
    per_db = {
        ("sqltuning", svc.SQL_SHARD): _rows(
            ["sqltuning.t1", "sqltuning.ts"], info="shardkey:id"),
        ("sqltuning", svc.SQL_BROADCAST): _rows(
            ["sqltuning.kcda_bcast"], info="shardkey:noshardkey_allset"),
        ("sqltuning", svc.SQL_SINGLE): _rows(
            ["sqltuning.t_max", "sqltuning.txt"]),
    }
    pool = FakePool(databases=["sqltuning", "mysql"],
                    info_schema={"sqltuning": {"base": [
                        "t1", "ts", "kcda_bcast", "t_max", "txt"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert (res["shard_tables"], res["broadcast_tables"],
            res["single_tables"], res["total_tables"]) == (2, 1, 2, 5)
    assert res["warnings"] == []
    assert pool.closed is True
    assert pool.made_with_read_timeout == svc.COMMAND_READ_TIMEOUT


def test_distributed_instance_wide_scope(monkeypatch):
    """命令返回实例级全量：按库归属拆分，(库,表)去重，且不重复扫描已覆盖的库"""
    _patch_ctx(monkeypatch, "distributed")
    allrows_shard = _rows(["db_a.s1", "db_b.s2"], info="shardkey:id")
    allrows_bcast = _rows(["db_b.b1"], info="shardkey:noshardkey_allset")
    allrows_single = _rows(["db_a.n1"])
    per_db = {}
    for d in ("db_a", "db_b"):
        per_db[(d, svc.SQL_SHARD)] = allrows_shard
        per_db[(d, svc.SQL_BROADCAST)] = allrows_bcast
        per_db[(d, svc.SQL_SINGLE)] = allrows_single
    pool = FakePool(databases=["db_a", "db_b", "mysql"],
                    info_schema={"db_a": {"base": ["s1", "n1"]},
                                 "db_b": {"base": ["s2", "b1"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    # 总数按 (库,表) 去重，不是 2 个库各算一遍
    assert res["total_tables"] == 4
    assert (res["shard_tables"], res["broadcast_tables"],
            res["single_tables"]) == (2, 1, 1)
    by_db = {i["db_name"]: i for i in res["items"]}
    assert by_db["db_a"]["total_tables"] == 2
    assert by_db["db_b"]["total_tables"] == 2
    # 覆盖性跳过：只切库一次
    assert pool.selected == ["db_a"]
    assert any(w["code"] == "INSTANCE_WIDE_SCOPE" for w in res["warnings"])


def test_distributed_per_db_scope_still_loops(monkeypatch):
    """命令若为当前库作用域，覆盖性判定不成立，必须逐库执行"""
    _patch_ctx(monkeypatch, "distributed")
    per_db = {
        ("db_a", svc.SQL_SHARD): _rows(["db_a.s1"]),
        ("db_a", svc.SQL_BROADCAST): [],
        ("db_a", svc.SQL_SINGLE): [],
        ("db_b", svc.SQL_SHARD): _rows(["db_b.s2"]),
        ("db_b", svc.SQL_BROADCAST): [],
        ("db_b", svc.SQL_SINGLE): [],
    }
    pool = FakePool(databases=["db_a", "db_b"],
                    info_schema={"db_a": {"base": ["s1"]},
                                 "db_b": {"base": ["s2"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert pool.selected == ["db_a", "db_b"]
    assert res["total_tables"] == 2
    assert not any(w["code"] == "INSTANCE_WIDE_SCOPE" for w in res["warnings"])


def test_single_database_filter_ignores_other_dbs(monkeypatch):
    """指定库时，实例级结果中其他库的表必须被排除"""
    _patch_ctx(monkeypatch, "distributed")
    rows = _rows(["db_a.s1", "db_b.s2", "db_b.s3"])
    pool = FakePool(databases=["db_a", "db_b"],
                    info_schema={"db_a": {"base": ["s1"]}},
                    per_db={("db_a", svc.SQL_SHARD): rows,
                            ("db_a", svc.SQL_BROADCAST): [],
                            ("db_a", svc.SQL_SINGLE): []})
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1", database="db_a")
    assert res["total_tables"] == 1 and res["shard_tables"] == 1


def test_system_db_rows_are_dropped(monkeypatch):
    """实例级结果里的系统库表不得计入"""
    _patch_ctx(monkeypatch, "distributed")
    rows = _rows(["db_a.s1", "mysql.user", "sysdb.foo"])
    pool = FakePool(databases=["db_a", "mysql", "sysdb"],
                    info_schema={"db_a": {"base": ["s1"]}},
                    per_db={("db_a", svc.SQL_SHARD): rows,
                            ("db_a", svc.SQL_BROADCAST): [],
                            ("db_a", svc.SQL_SINGLE): []})
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert res["total_tables"] == 1


def test_distributed_view_is_excluded(monkeypatch):
    """原厂"不统计视图"——即使命令返回了视图也必须扣除"""
    _patch_ctx(monkeypatch, "distributed")
    per_db = {
        ("db_a", svc.SQL_SHARD): _rows(["db_a.s1"]),
        ("db_a", svc.SQL_BROADCAST): [],
        ("db_a", svc.SQL_SINGLE): _rows(["db_a.n1", "db_a.v1"]),
    }
    pool = FakePool(databases=["db_a"],
                    info_schema={"db_a": {"base": ["s1", "n1"],
                                          "view": ["v1"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert res["single_tables"] == 1 and res["total_tables"] == 2
    assert not any(w["code"] == "RECON_MISMATCH" for w in res["warnings"])


def test_distributed_overlap_does_not_double_count(monkeypatch):
    """若某版本 without shardkey 含广播表（RISK-A），总数不得重复计算"""
    _patch_ctx(monkeypatch, "distributed")
    per_db = {
        ("db_a", svc.SQL_SHARD): _rows(["db_a.s1"]),
        ("db_a", svc.SQL_BROADCAST): _rows(["db_a.b1"]),
        ("db_a", svc.SQL_SINGLE): _rows(["db_a.b1", "db_a.n1"]),
    }
    pool = FakePool(databases=["db_a"],
                    info_schema={"db_a": {"base": ["s1", "b1", "n1"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert res["total_tables"] == 3 and res["single_tables"] == 1
    assert res["broadcast_tables"] == 1 and res["overlap_count"] == 1
    assert any(w["code"] == "KIND_OVERLAP" for w in res["warnings"])


def test_distributed_recon_mismatch(monkeypatch):
    """并集与 information_schema 不一致时，双向差集必须写进 detail"""
    _patch_ctx(monkeypatch, "distributed")
    per_db = {
        ("db_a", svc.SQL_SHARD): _rows(["db_a.s1"]),
        ("db_a", svc.SQL_BROADCAST): [],
        ("db_a", svc.SQL_SINGLE): _rows(["db_a.n1"]),
    }
    pool = FakePool(databases=["db_a"],
                    info_schema={"db_a": {"base": ["s1", "n1", "ghost"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    w = [x for x in res["warnings"] if x["code"] == "RECON_MISMATCH"]
    assert w and "ghost" in w[0]["detail"]
    assert "ghost" in res["items"][0]["detail"]


def test_distributed_partial_failure(monkeypatch):
    """单库失败只降级该库：不计入总数、单列计数、其余库照常（ADR-5）"""
    _patch_ctx(monkeypatch, "distributed")
    per_db = {
        ("db_a", svc.SQL_SHARD): _rows(["db_a.s1"]),
        ("db_a", svc.SQL_BROADCAST): [],
        ("db_a", svc.SQL_SINGLE): [],
        ("db_b", svc.SQL_SHARD): _mysql_error(1142, "SELECT command denied"),
        ("db_c", svc.SQL_SHARD): _rows(["db_c.s9"]),
        ("db_c", svc.SQL_BROADCAST): [],
        ("db_c", svc.SQL_SINGLE): [],
    }
    pool = FakePool(databases=["db_a", "db_b", "db_c"],
                    info_schema={"db_a": {"base": ["s1"]},
                                 "db_b": {"base": ["x"]},
                                 "db_c": {"base": ["s9"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert res["failed_databases"] == 1
    assert res["total_tables"] == 2
    statuses = {i["db_name"]: i["status"] for i in res["items"]}
    assert statuses == {"db_a": "OK", "db_b": "FAILED", "db_c": "OK"}
    w = [x for x in res["warnings"] if x["code"] == "PROXY_CMD_FAILED"]
    assert w and "授权不足" in w[0]["detail"]


def test_command_timeout_is_reported_not_hung(monkeypatch):
    """命令挂起被读超时截断，渲染为可读提示（RISK-F）"""
    _patch_ctx(monkeypatch, "distributed")
    per_db = {("db_a", svc.SQL_SINGLE): Exception("Read timed out"),
              ("db_a", svc.SQL_SHARD): _rows(["db_a.s1"]),
              ("db_a", svc.SQL_BROADCAST): []}
    pool = FakePool(databases=["db_a"], info_schema={"db_a": {"base": ["s1"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert res["failed_databases"] == 1
    assert "读超时" in res["items"][0]["detail"]


def test_time_budget_skips_remaining(monkeypatch):
    """超预算的库标 SKIPPED、不计入总数，并显式告警"""
    _patch_ctx(monkeypatch, "distributed")
    monkeypatch.setattr(svc, "TOTAL_BUDGET_SECONDS", -1)   # 立即超预算
    pool = FakePool(databases=["db_a", "db_b"],
                    info_schema={"db_a": {"base": ["s1"]},
                                 "db_b": {"base": ["s2"]}},
                    per_db={})
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert res["skipped_databases"] == 2 and res["total_tables"] == 0
    assert all(i["status"] == "SKIPPED" for i in res["items"])
    assert any(w["code"] == "TIME_BUDGET_EXCEEDED" for w in res["warnings"])


def test_distributed_all_1064_flags_wrong_endpoint(monkeypatch):
    _patch_ctx(monkeypatch, "distributed")
    per_db = {("db_a", svc.SQL_SHARD): _mysql_error(1064, "syntax error")}
    pool = FakePool(databases=["db_a"], info_schema={"db_a": {"base": []}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert any(w["code"] == "NOT_DISTRIBUTED_ENDPOINT" for w in res["warnings"])


def test_select_db_failure_is_isolated(monkeypatch):
    _patch_ctx(monkeypatch, "distributed")
    pool = FakePool(databases=["db_a"], info_schema={"db_a": {"base": []}},
                    select_db_fail={"db_a": _mysql_error(1049, "Unknown database")})
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert res["failed_databases"] == 1
    assert res["items"][0]["status"] == "FAILED"
    assert pool.closed is True


def test_shared_pool_is_never_switched(monkeypatch):
    """ADR-3 护栏：共享池连接上不得发生任何 select_db"""
    _patch_ctx(monkeypatch, "distributed")
    shared = FakePool(databases=["db_a"],
                      info_schema={"db_a": {"base": ["s1"]}})
    tmp = FakePool(databases=["db_a"], info_schema={"db_a": {"base": ["s1"]}},
                   per_db={("db_a", svc.SQL_SHARD): _rows(["db_a.s1"]),
                           ("db_a", svc.SQL_BROADCAST): [],
                           ("db_a", svc.SQL_SINGLE): []})
    _patch_tmp_pool(monkeypatch, tmp)
    svc.analyze(shared, connection_id="c1")
    assert shared.selected == []
    assert tmp.selected == ["db_a"]


def test_empty_result_set_is_not_an_error(monkeypatch):
    """实测：lzbj_ecif 无单表。空结果集必须是合法的 0，不得报错"""
    _patch_ctx(monkeypatch, "distributed")
    per_db = {("db_a", svc.SQL_SHARD): _rows(["db_a.s1"]),
              ("db_a", svc.SQL_BROADCAST): [],
              ("db_a", svc.SQL_SINGLE): []}
    pool = FakePool(databases=["db_a"], info_schema={"db_a": {"base": ["s1"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert res["single_tables"] == 0 and res["broadcast_tables"] == 0
    assert res["shard_tables"] == 1 and res["warnings"] == []


def test_counts_are_consistent():
    """恒等式 total == shard + broadcast + single，随机 200 组"""
    rnd = random.Random(20260829)
    names = [f"t{i}" for i in range(30)]

    def mk(k):
        return _rows([f"db_a.{n}" for n in rnd.sample(names, k)])

    for _ in range(200):
        per_db = {("db_a", svc.SQL_SHARD): mk(rnd.randint(0, 10)),
                  ("db_a", svc.SQL_BROADCAST): mk(rnd.randint(0, 10)),
                  ("db_a", svc.SQL_SINGLE): mk(rnd.randint(0, 10))}
        pool = FakePool(databases=["db_a"], info_schema={"db_a": {"base": names}},
                        per_db=per_db)
        svc._new_pool_backup = svc._new_pool
        svc._new_pool = lambda cfg, pool_size=1, _p=pool: _p
        try:
            items, _w, _s, totals = svc._collect_distributed(
                pool, ["db_a"], {"db_a": {"base": set(names), "view": set()}},
                {"db_a"})
        finally:
            svc._new_pool = svc._new_pool_backup
        assert totals["total"] == (totals["shard"] + totals["broadcast"]
                                   + totals["single"])
        assert items[0]["total_tables"] == (items[0]["shard_tables"]
                                            + items[0]["broadcast_tables"]
                                            + items[0]["single_tables"])


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

在 `_init_default_data` 的 `all_menus` 中 `'deep-diag-toolkit',`（第 1691 行）之后追加：

```python
        'deep-diag-tabletype',
```

> **这一行不是可选的。** `database.py:1749` 的
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
                  <span v-if="deepResult.tabletype.skipped_databases"> · <b style="color:var(--warning-500)">未采集 {{ deepResult.tabletype.skipped_databases }}</b></span>
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
                    <template #default="s"><el-tag :type="s.row.status==='OK'?'success':(s.row.status==='SKIPPED'?'warning':'danger')" size="small">{{ s.row.status }}</el-tag></template>
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

**② 第 814 行**（`runSqlStats` 的 `};`）之后、第 815 行 `// G10: ZK Discovery` 之前追加新方法：

```javascript
    const runTableTypeStats=async()=>{
      const r=await _deepPost('tabletype','/api/v1/table-type-stats/run',{connection_id:deepConnId.value,database:deepDb.value});
      if(r){deepResult.tabletype=r;ElementPlus.ElMessage.success(`统计完成：${r.database_count} 个库 / ${r.total_tables} 张表`)}
    };
```

**③ 第 2043 行** `setup()` 返回清单，把 `runSqlStats,` 改成 `runSqlStats,runTableTypeStats,`：

```diff
-...,runClusterInspect,runIndexAudit,runSchemaDiff,runEmergency,runSqlStats,visibleMenus,...
+...,runClusterInspect,runIndexAudit,runSchemaDiff,runEmergency,runSqlStats,runTableTypeStats,visibleMenus,...
```

> 漏掉第 ③ 步的后果：页签能渲染，但点按钮报 `runTableTypeStats is not a function`。
> 这是本项目 `setup()` 显式返回清单写法的固有陷阱，必须逐条核对。

---

## 15. 附录 B · 三条命令的实测形态锚点

**采集日期**：2026-08-29 　**采集方式**：`mysql` 客户端直连 Proxy 端口 + 赤兔"在线SQL"
**采集实例**：① 分布式实例，业务库 `sqltuning`；② 分布式实例
`group_1769388403_25`（开发测试环境 · ECIF-分布式-开发环境），业务库 `lzbj_ecif`

### B.1 列形态

| 命令 | 列 | 行为 |
|---|---|---|
| `/*proxy*/show table with shardkey` | `db_table`, `info` | 2 列 |
| `/*proxy*/show table with noshardkey_allset` | `db_table`, `info` | 2 列 |
| `/*proxy*/show table without shardkey` | `db_table` | **1 列，无 `info`** |

**`db_table` 的值是库限定名**，形如 `sqltuning.t_max` / `lzbj_ecif.aplt_sys_data_log`。

### B.2 原始输出摘录（单测夹具即照此构造）

`sqltuning` 实例，`USE sqltuning` 后：

```
MySQL [sqltuning]> /*proxy*/show table without shardkey;
+-----------------------------------+
| db_table                          |
+-----------------------------------+
| sqltuning.kcda_vchr_cmprs_old     |
| sqltuning.kdpa_cust_acct_num_cmprs|
| sqltuning.t2                      |
| sqltuning.t_max                   |
| sqltuning.t_max2                  |
| sqltuning.t_max3                  |
| sqltuning.txt                     |
+-----------------------------------+
7 rows in set (0.01 sec)

MySQL [sqltuning]> /*proxy*/show table with shardkey;
+--------------------------------+--------------------------------------------------------------+
| db_table                       | info                                                         |
+--------------------------------+--------------------------------------------------------------+
| sqltuning.kcdb_change_card     | shardkey:orig_card_num                                       |
| sqltuning.kceb_cust_loss       | shardkey:loss_num                                            |
| sqltuning.kdpa_acct_draw_ctrl  | shardkey:lblty_acct_num                                      |
| ...                            | ...                                                          |
| sqltuning.t_order_hash_range_new | shardkey:SHARDKEY_HASH_USE_SUB;sub_shardkey:id;sub_func:id  |
| sqltuning.t_order_hash_range_old | shardkey:id;sub_shardkey:order_time;sub_func:month          |
| sqltuning.t_user_region_hash_list_old | shardkey:id;sub_shardkey:region_code                   |
+--------------------------------+--------------------------------------------------------------+
18 rows in set (0.00 sec)

MySQL [sqltuning]> /*proxy*/show table with noshardkey_allset;
+---------------------------------+---------------------------+
| db_table                        | info                      |
+---------------------------------+---------------------------+
| sqltuning.kbrp_org              | shardkey:noshardkey_allset|
| sqltuning.kcda_bcast            | shardkey:noshardkey_allset|
| sqltuning.kcda_vchr_cmprs       | shardkey:noshardkey_allset|
| sqltuning.kdpp_int_rate_adjust_detl | shardkey:noshardkey_allset|
+---------------------------------+---------------------------+
4 rows in set (0.00 sec)
```

`lzbj_ecif`（赤兔"在线SQL"）：`with shardkey` 的 `info` 多为
`shardkey:SHARDKEY_HASH_USE_SUB;sub_shardkey:ID;sub_func:ID`；
`with noshardkey_allset` 的 `info` 出现 `shardkey:noshardkey_allset;auto_increment:ID`
这种带自增标注的变体。**两种变体都不影响本期计数**（只取 `db_table` 列）。

### B.3 由本次实测直接得出的结论

| 编号 | 结论 | 落到代码 |
|---|---|---|
| B-1 | 列名为 `db_table` | `_EXACT_NAME_COLS` 首位 |
| B-2 | `without shardkey` 只有 1 列，另两条有 2 列 | 选列规则 1 与 2 都要能命中；`info` 入排除词 |
| B-3 | 值为库限定名 | `_split_qualified` + 按库归属（ADR-11） |
| B-4 | 三类互斥（7 / 4 无交集，注意 `kcda_vchr_cmprs_old` ≠ `kcda_vchr_cmprs`） | RISK-A 证伪；归一化作为保险保留 |
| B-5 | `information_schema` 经 Proxy 可查且含系统库 | 业务库白名单过滤必需 |
| B-6 | 赤兔对无单表的库执行 `without shardkey` 会转圈 | RISK-F；30s/180s 双层兜底；T15 判决 |
| B-7 | 三条命令 0.01 秒级返回（`sqltuning` 规模） | 性能基准，T10 在更大库上复核 |

**待回填（T13/T14/T15 完成后补入本附录）**：

| 项 | 内容 | 来源 |
|---|---|---|
| 命令作用域 | 当前库 / 实例级 | T13 |
| `sqltuning` 的 `base_tables` 是否 = 29 | | T14 |
| 空结果集行为 | A / B / C | T15 |

---


## 修订记录

| 版本 | 日期 | 作者 | 内容 |
|---|---|---|---|
| Rev.B | 2026-08-29 | 智能体 A | 依第一轮内网实测（附录 B）修订。**证伪 RISK-A**（三类互斥，归一化改作保险保留）；**锚定 RISK-C**（列名 `db_table`、`without shardkey` 单列、值为库限定名）；**新增 RISK-E**（命令作用域可能为实例级——Rev.A 会让总数放大 N 倍，改为按行内库限定名归属 + 全局 `(库,表)` 去重 + 覆盖性跳过，两种作用域下均正确）；**新增 RISK-F**（无单表的库上命令可能挂起——加 30s 读超时 + 180s 总预算 + `SKIPPED` 状态）。新增 ADR-11~14、E-18~23、W9~W11、`skipped_databases` 字段。§10 重写为"第一轮裁决表 + T13/T14/T15 三项补测 + GATE-2"。附录 A 代码同步更新（服务层 574 行 / 单测 594 行），**本地 35 项单测全部通过，仓库代码零改动**。 |
| Rev.A | 2026-08-29 | 智能体 A | 首版。需求拆解、现状勘查（含 `/*proxy*/` 存活性证据链）、三大语义风险（RISK-A/B/C/D）识别与对策、总体与详细设计、10 条 ADR、17 项异常矩阵、爆炸半径分析、12 个内网实测用例与 GATE-1 放行判据、附录 A 全套成品代码（服务层 489 行 / API 44 行 / 迁移 34 行 / 单测 455 行 / 既有文件 9 行改动 + 1 个前端块）。**附录 A 代码已在本地以 importlib 挂载方式跑通 32 项单测（含真实 MariaDB 落库），仓库代码零改动。** |
