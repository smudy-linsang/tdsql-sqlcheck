# DESIGN-v1.6.3.0 深度诊断·表类型统计（G14）详细设计说明书

| 项 | 内容 |
|---|---|
| 文档编号 | DESIGN-v1.6.3.0 **Rev.F** |
| 模块编号 | **G14 · 表类型统计**（深度诊断第 10 个子模块） |
| 目标版本 | v1.6.3.0（当前基线 v1.6.2.2，`VERSION` / `backend/config.py:APP_VERSION`） |
| 文档等级 | **照图施工级**——附录 A 给出全部新增/修改文件的逐行成品代码（已本地验证：42 项单测全通过），实施者不得二次设计 |
| 编写 | 智能体 A |
| 编写日期 | 2026-08-29 首版；2026-08-31 Rev.F 依 v1.6.2.2 上线后的代码变更复核修订 |
| 状态 | 设计与代码**已完成**；四轮内网实测均未推翻；**v1.6.2.2 已上线，本设计依赖的代码事实经逐条复核后仅迁移器一处需要适配（已在 §2.7 写入）**。**GATE-2 无阻断项，可进入开发**。剩余 T13 一项，不阻断（§10.2） |
| 前置约束 | 本文档编写阶段**未修改任何代码**（用户要求）。仓库工作区在本文档提交时保持干净。 |

---

## 0. 阅读指引与本文档的三条硬约束

本文档同时承担三件事，读者请按角色取用：

* **实施者（智能体 Q 或人工）**：读 §5～§9 + 附录 A。附录 A 是**可直接落盘的成品代码**——四个新增文件 + 既有文件的 9 行改动 + 1 个前端块，逐字给出。
* **内网测试配合者**：只读 **§10**。四轮共 11 个用例已完成（裁决见 §10.1），**只剩 T13** 一项（§10.2），**不阻断开发**——一句 `SHOW DATABASES` 多数情况下就能结案。
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

> 本节所有结论均来自对当前 `main` 分支（`01e2914`，v1.6.2.2 上线后）的实读，不是推测。
> **行号锚定于 `01e2914`。** 实施前请先 `git log --oneline -1` 核对；若 main 已前进，
> 用本节给出的**代码片段文本**（而非行号）重新定位——片段是稳定锚，行号是易腐锚。

> **Rev.F 复核结论（2026-08-31）**：v1.6.2.2 已在内网上线。对本设计依赖的 13 个文件
> 做了 `git diff 8fee172..01e2914` 逐一比对，**只有两个文件动过**：
> `backend/schema/migrator.py`（+197/−24，失败关闭改造，影响见 §2.7）与
> `frontend/static/js/app.js`（2 行，请求体上限与刷新交互，与本模块无关）。
> **`auth_service.py` / `database.py` / `main.py` / `frontend/index.html` /
> `tdsql_connector.py` / `instance_type_service.py` / `connection_registry.py` /
> `index_audit_service.py` / `zk_scan_enrich_service.py` / `api/index_audit.py` /
> RBAC 与路由完整性测试——全部未变**，本节 §2.1～§2.6、§2.8 的全部行号与结论继续成立
> （已逐条重新核对，非沿用）。

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
| P4 | `backend/services/database.py:1717` | `_init_default_data` 的 `all_menus` | **致命**：`database.py:1775` 有 `DELETE FROM role_permissions WHERE menu_key NOT IN (...)`，未登记的键会在每次启动时被删掉，菜单永久不可见 |

补充事实（决定了本模块**不需要**写任何存量库订正 SQL）：
`database.py:1733-1736` 对 `all_menus × 内置角色`执行 `INSERT IGNORE INTO role_permissions(...) VALUES(...)`，`init_db()` 每次启动都会跑（`database.py:420`）。因此新键在**存量库**上会于下次启动自动补齐，`developer` / `auditor` 的默认不可见排除清单（`database.py:1728-1731`）不含本键 → 四个内置角色默认全部可见，符合 REQ-7。

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

### 2.7 落库与迁移约定（**v1.6.2.2 上线后已大改，本节 Rev.F 重写**）

* `backend/schema/loader.py` 扫描 `backend/schema/vN/NNN_*.sql`，按 `(version, sequence)` 升序执行。
* `migrator._split_statements()`（`migrator.py:159-164`）：**逐行剔除以 `--` 开头的行**，
  再按 `;` 切分逐条执行——与 Rev.A 时一致，本模块的 SQL 写法不变。
* 现有最高版本目录：`v10/100_zk_scan_enrich.sql`。→ 本模块用 `v11/110_table_type_stats.sql`。
* **不动 `database.py::_create_all_tables`**：`init_db()` 在 `_create_all_tables` 之后调
  `migrator.run_migrations()`（`database.py:411`），全新安装与存量升级都覆盖到。

**v1.6.2.2（O-23 / O-26 / O-29 / O-30）把迁移器从"宽容"改成了"失败关闭"，
新增三条硬约束，本模块必须逐条满足：**

| # | 新行为 | 位置 | 对 G14 的影响 |
|---|---|---|---|
| M-1 | **任一语句执行失败即 `MigrationError` → 启动中止**（旧版只记 WARNING 继续） | `migrator.py:191` | 本模块的 DDL **必须保证在 MySQL 8 / TDSQL 上一次执行成功**。用 `CREATE TABLE IF NOT EXISTS` 保证幂等，重复启动不会二次失败 |
| M-2 | **结构严格验收只作用于 `ALTER TABLE … ADD COLUMN`**（`_ADD_COLUMN_RE`，`migrator.py:45-48`） | `_apply_file` / `_structure_state` | 本模块是**纯 `CREATE TABLE`**，不匹配该正则 → **不进入列级结构验收**；已登记后 `_structure_state` 返回 `valid` 直接跳过。**无额外适配成本** |
| M-3 | **checksum 漂移 → 启动失败关闭**，除非精确命中代码内 `_KNOWN_RECONCILIATIONS` 三元组账本（`migrator.py:281-296`） | `_auto_reconcile` | **发布即冻结**——见下方警示 |

> ### ⚠️ M-3 是本次新增的最重要约束：迁移文件发布即冻结
>
> `v11/110_table_type_stats.sql` **一旦随版本发布并在任一实例上被应用，文件内容即被冻结**。
> 事后任何修改——**哪怕只是改一个注释、加一个空格**——都会让**所有已部署实例
> 在下次启动时失败关闭**，报"迁移版本记录与文件内容漂移……不在已知调和账本中"。
>
> 唯一的补救是同步往 `migrator._KNOWN_RECONCILIATIONS` 里加一条精确的
> `(version_key, 历史 checksum, 当前 checksum, 原因)` 四元组——那是一次
> 需要人工核实、走评审的变更，不是随手改。
>
> **因此表结构必须在首次发布前定稿。** 本设计的 DDL 从 Rev.B 到 Rev.E 已经增补过三次字段
> （`skipped_databases` / `baseline_tables` / `subpartition_tables`），
> 幸而尚未发布；**进入开发后若还要改字段，务必在打包前改完**。
> 若发布后确需扩列，正确做法是**新增 `v11/111_*.sql` 用 `ALTER TABLE … ADD COLUMN`**，
> 而不是回头编辑 `110_*.sql`——注意 `ADD COLUMN` 会进入 M-2 的列级严格验收，
> 类型/可空性/默认值三项必须与既有列逐字相符。


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

> 本节四条风险在 Rev.A 编写时全部未知。Rev.B 依据第一轮内网实测逐条裁决，
> 并新增两条实测暴露出来的风险 RISK-E / RISK-F。Rev.C 裁决 RISK-F；
> Rev.D 量化 RISK-B、Rev.E 查明其成因（二级分区物理子表）并**将其消除**。
> **当前仅剩 RISK-E（T13，命令作用域）未裁决**——不阻断开发，
> 设计已在两种作用域下都保证正确。

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

#### RISK-B：三类之外还有别的表 —— **成因已查明并消除，两个口径精确对齐**

Rev.A 的担忧：`分片 ∪ 广播 ∪ 单表` 未必等于该库全部 `BASE TABLE`。

**T14 实测（`lzbj_ecif`）**：Proxy 口径 215（98+117+0），`information_schema` 基线 293，
**差 78 张（27%）**。

**D3 实测查明成因：78 张全部是 TDSQL 二级分区的物理子表。**

命名形态（实测）：

```
cus_pub_translog                      ← 逻辑表，Proxy 的 show table 返回它
cus_pub_translog_tdsql_subp190001     ← 兜底/溢出分区
cus_pub_translog_tdsql_subp202601     ← 2026-01
…
cus_pub_translog_tdsql_subp202612     ← 2026-12
```

**账目精确闭合**：

| 项 | 数 |
|---|---|
| `info` 含 `sub_func:month` 的按月二级分区表 | 6 张 |
| 每张的物理子表数（`190001` 兜底 + `202601`…`202612`） | 13 |
| 子表合计 | **6 × 13 = 78** |
| `information_schema` 基线 | 293 |
| **293 − 78** | **215 = Proxy 口径** |

（D3 本身回了 71 行：5 张有子表的逻辑表 × 13 = 65，加 6 个逻辑表名
——第 6 个 `cus_pub_translog_his` 是被 `LIKE 'cus_pub_translog%'` 顺带捞到的、
没有二级分区的表；`cus_pub_sync_consumer_log` 的 13 个子表没被本次 LIKE 匹配到。
65 + 6 = 71 ✓）

**设计对策（Rev.E，ADR-17）：把二级分区物理子表从基线中剔除并单列计数。**

```python
_SUBPARTITION_RE = re.compile(r"_tdsql_subp\d+$", re.IGNORECASE)
```

剔除后：**逻辑基线 215 == Proxy 口径 215，两个口径精确相等。**

**为什么这件事必须做，而不是像 Rev.D 那样"两个数并排摆着让用户自己判断"**：

Rev.D 的做法会让 `RECON_MISMATCH` 在**每一个有二级分区的库上永久亮着**。
一个永远亮的告警不是告警，是背景噪声——用户学会无视它之后，
真正的不一致（比如某张表没进 Proxy 路由表）就再也没人看得见了。
**把已知且无害的差异解释掉，剩下的告警才有信号价值。**
这也正是我在 Rev.C 里写下的那句话的直接应用：交叉校验是静默失效模式的唯一探测器
——探测器不能一直响。

**数据没有被藏起来**：子表数单列为 `subpartition_tables`（实例级汇总 + 逐库一列），
并配一条 `SUBPARTITION_EXCLUDED`（INFO）说明剔除了多少张、为什么剔除。

**误判方向是安全的**：正则限定后缀为**纯数字**并锚定末尾，
`my_tdsql_subp202601_backup` 这类用户自建表不会被误剔。
万一某版本用了非数字后缀，本模块会把它当逻辑表——后果是 `RECON_MISMATCH`
**把它显式报出来（可见）**，而不是静默少算（不可见）。

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
3. **作用域指纹比对**（Rev.D 修订，原为"与 information_schema 基线逐表相等"）：
   记录**首个非空库**三条命令返回的原始 `(库, 表)` 集合作为指纹；
   **第二个非空库**若返回逐条相同的集合，即证明命令与当前会话库无关
   —— 因为当前库作用域下两库的库限定名前缀必然不同，非空集合不可能相等。
   判定为实例级后，其余库全部跳过。
   * 实例级作用域：**6 条命令**（两个库各 3 条）即可覆盖全实例；
   * 当前库作用域：第二个库指纹不同 → 判定为当前库作用域，逐库老实执行；
   * 判据是**充分必要**的，不是启发式，不存在漏库风险。

   > **为什么不用"与 information_schema 基线相等"**（Rev.B 的做法）：
   > T14 实测 `lzbj_ecif` Proxy 口径 215 vs 基线 293，两者**基本不可能相等**，
   > 那个判据永远不成立、优化永远不生效。见 ADR-12。
4. 检测到跨库行时输出 `INSTANCE_WIDE_SCOPE`（INFO），把实际执行了几个库告诉用户。

**表名含点号的边界**：拆分只在"点号左侧确为一个已知库名"时进行（已知库名来自
`SHOW DATABASES` 的**全量**结果，含系统库）。否则整串当作当前库下的表名。
这样既不会把 `db.tbl` 漏拆，也不会把 `odd.name` 这种表名误拆后当成"未知库"丢掉——
**误拆的后果是少算，而少算是不可见的错误**。

#### RISK-F：空结果集下命令可能挂起 —— **已裁决：不挂起，赤兔前端问题**

使用者第一轮反馈：赤兔"在线SQL"对 `lzbj_ecif`（该库没有单表）执行
`/*proxy*/show table without shardkey`，**页面一直转圈**。

**第二轮实测（T15，`mysql` 客户端直连 Proxy 10.243.20.13:15005）判决**：

```
MySQL [lzbj_ecif]> /*proxy*/show table without shardkey;
Query OK, 0 rows affected (0.001 sec)
```

**不挂起，0.001 秒返回。** 但返回的不是"空结果集"，而是一个 **OK 包**
（`Query OK, 0 rows affected`）——**没有列元数据、没有结果集结构**。
赤兔转圈的原因由此确定：它的前端在等一个结果集（列头 + 行），
拿到的却是 OK 包，于是永远等不到渲染条件。**是赤兔的前端缺陷，与命令无关。**

**对本模块的影响：无需改设计。** 已核对 PyMySQL 的实际行为：

| 形态 | `cursor.execute()` | `cursor.fetchall()` | `cursor.description` |
|---|---|---|---|
| 结果集（`SELECT 1 AS a`） | `1` | `[{'a': 1}]` | 有 |
| **OK 包**（`DO 1` / `SET @x=1`，与 TDSQL 此处同一种协议响应） | `0` | **`[]`** | **`None`** |

`fetchall()` 对 OK 包返回 `[]` 而不是 `None` —— 已在**本机 PyMySQL 2.2.8**
与**项目下限版本 1.1.0 的 wheel 源码**上双向核对（`if self._rows is None: return []`），
项目 `requirements.txt` 钉的是 `pymysql>=1.1.0`，区间内行为一致。
且 OK 包之后**同一连接可继续正常查询**（实测 `SELECT 2` 正常返回）。

因此本模块拿到的就是"该类 0 张"，走正常路径、不告警、不降级。
`_extract_pairs` 里的 `rows = rows or []` 作为额外防御保留。

**由此新增一条必须记住的语义**：**OK 包与"命令未被支持"在协议上无法区分。**
若将来某个 TDSQL 版本不再支持三条命令之一、且返回 OK 包而不是报错，
本模块会**静默地把该类计为 0**。这正是 §6.6 的 `information_schema` 交叉校验
必须存在的理由——并集会比基线少一大截，`RECON_MISMATCH` 会把它顶出来。
**这条校验不是锦上添花，它是这个静默失效模式的唯一探测器。**

`COMMAND_READ_TIMEOUT=30` 与 `TOTAL_BUDGET_SECONDS=180` 保留为纯保险，
在已实测的形态下永不触发。


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
5. kind_map = {}        # (库, 表) -> 类型，全局去重，正是原厂「库名+表名去重」
   scope_signature = None ; scope_decided = ""
   for db in dbs:
     if scope_decided == 实例级:  该库跳过（结果已被覆盖）
     if 已耗时 > 180s:           该库标 SKIPPED，继续
     tmp.select_db(db)
     raw_pairs = ∅
     for kind, sql in (分片, 广播, 单表):
         rows = execute(sql)               # 失败→该库 FAILED，break
         # rows 可能是 OK 包（Query OK, 0 rows affected）→ fetchall() 为 []
         pairs = 按 known_dbs 拆库限定名，缺省归当前库
         raw_pairs |= pairs
         for (qual, name) in pairs:
             if qual 不在目标库集合:  丢弃    # 系统库 / 被筛掉的库
             if name 在 baseline[qual]["view"]:  丢弃   # 原厂：不统计视图
             kind_map[(qual,name)] = 优先级更高者（分片 > 广播 > 单表）
     ── 作用域自判（ADR-12 修订，不依赖 information_schema）──
     if 未判定 and raw_pairs 非空:
         首个非空库 → 记指纹；第二个非空库 → 指纹相同判实例级，不同判当前库
6. tmp.close_all()
7. 逐库汇总 kind_map ──► items；Proxy 口径总数 = len(kind_map)
   ── 交叉校验：每库 got 与 baseline[db]["base"] 做双向集合差 ──
   差集写入该库 item.detail；全部处理完后汇总成【一条】RECON_MISMATCH
8. 告警 + 落库 + 返回（total_tables 与 baseline_tables 并排）
```

**恒等式（单测钉住）**：逐库与汇总均满足
`total_tables == shard_tables + broadcast_tables + single_tables`，
且 `汇总 total == len(kind_map)`——不会因为实例级作用域被放大 N 倍。

**`baseline_tables` 不参与上述恒等式**：它是 `information_schema` 的独立口径，
实测与 Proxy 口径相差可达 27%（`lzbj_ecif` 215 vs 293），只并排呈现、不做换算。

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
| 新增 | `backend/services/table_type_stats_service.py` | 656 行（附录 A.1，成品） |
| 新增 | `backend/api/table_type_stats.py` | 44 行（附录 A.2，成品） |
| 新增 | `backend/schema/v11/110_table_type_stats.sql` | 41 行（附录 A.3，成品） |
| 新增 | `tests/test_table_type_stats.py` | 749 行（附录 A.4，成品） |
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
  "baseline_tables": 176,
  "subpartition_tables": 26,
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

`baseline_tables` 是 `information_schema` 的**逻辑** `BASE TABLE` 合计
（已剔除 `_tdsql_subp<数字>` 结尾的二级分区物理子表，ADR-17），与 `total_tables`
（Proxy 口径）**并排呈现、互相印证**——实测两者应当精确相等，不等即 `RECON_MISMATCH`。

`subpartition_tables` 是被剔除的二级分区物理子表数，单列呈现，数据不藏。

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
返回 `{db: {"base": 逻辑表, "view": 视图, "subp": 二级分区物理子表}}`。

**三分类而不是两分类**：`BASE TABLE` 里混着 `_tdsql_subp<数字>` 结尾的
二级分区物理子表（实测 `lzbj_ecif` 293 张里有 78 张是），它们不是用户认知中的
"表"，Proxy 也只返回逻辑表名。剔除后逻辑基线与 Proxy 口径精确相等（ADR-17）。

**取名字而不是取 COUNT** 的四个理由：
1. 集中式分支要的 `single = len(base)` 直接可得；
2. 分布式分支的交叉校验需要**双向差集的表名**，光有计数说不出"差在哪张表"；
3. "不统计视图"这条原厂要求，在分布式分支上靠 `view` 名单做扣除来落实；
4. 逐库「说明」列要写出**差的是哪几张表**，光有计数说不出。
   （注意：作用域判定**不**使用基线——见 ADR-12，实测证明两者基本不等。）

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
            if 已判定实例级: skipped[db] = "coverage"; continue
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
* 两侧差集都为空 → 该库无记录。
* 任一侧非空 → 写入该库的 `item.detail`（也就是 `table_type_stat_item.detail`，
  截断至 512 字节），形如：
  `Proxy 口径 215 张，information_schema 基线 293 张；仅基线可见(78): a, b, c …等 78 张`
  两侧样本各取 `MAX_DIFF_SAMPLE=20` 个并按名排序。
* 全部库处理完后，**汇总成一条** `RECON_MISMATCH` 告警（ADR-15），给出
  涉及库数、两侧合计张数、前 5 个库名，并明确写出"本页四个数字采用 Proxy 口径"。

**为什么必须汇总**：T14 实测 `lzbj_ecif` 差 78/293（27%），这种差异极可能
**每个库都有**。逐库一条告警在 50 库实例上就是 50 条横幅。

比集合而不比计数：两个集合大小相同但内容不同（少了 A、多了 B）时，比计数会漏报——
这正是"不可见错误"的典型形态。

**这里的比对结果不参与任何控制流**（Rev.D 起作用域判定改用指纹比对），
它只负责如实呈现两个口径的差异。

集中式实例不做此校验（基线本身就是唯一数据源）。

### 6.7 告警清单（`warnings[]`）

| code | severity | 触发 | 用户该怎么办 |
|---|---|---|---|
| W1 `PROXY_CMD_FAILED` | ERROR | 某库三条命令中任一失败（含读超时） | 看 `detail` 的 errno；1064→连接可能不是 Proxy 端口；1045/1142→授权不足；读超时→保险触发，见 RISK-F |
| W2 `KIND_OVERLAP` | WARNING | 三类集合有交集（RISK-A 命中） | 说明"三类互斥"在本版本不成立，已按优先级去重，总数仍正确 |
| W3 `RECON_MISMATCH` | WARNING | 任一库的 Proxy 口径 ≠ **逻辑**基线（已剔除二级分区子表） | **全实例汇总为一条**。Rev.E 后这条不再常态触发——一旦亮起就意味着真有表没进 Proxy 路由表，值得查。逐库差集表名在表格「说明」列 |
| W4 `SHAPE_UNKNOWN` | WARNING | 结果列形态未识别（RISK-C 兜底） | 把 `shape` 字段贴给开发，扩充 `_EXACT_NAME_COLS` |
| W5 `INSTANCE_TYPE_UNRELIABLE` | WARNING | `ctx.source == DEFAULT` 或 `ctx.conflict` | 实例类型是猜的/有冲突，口径可能整体走错分支；去实例管理页锁定后重跑 |
| W6 `NO_BUSINESS_DB` | INFO | 过滤后无业务库 | 账号权限过窄或实例确实空 |
| W7 `TOO_MANY_DATABASES` | WARNING | 库数 > 500，已截断 | 用 `database` 参数分批统计 |
| W8 `NOT_DISTRIBUTED_ENDPOINT` | ERROR | 已执行的库全部因 1064 失败 | 该连接大概率指向后端 TXSQL 而非 Proxy（§2.4） |
| W9 `INSTANCE_WIDE_SCOPE` | INFO | 结果含跨库行，或前两个非空库指纹相同（RISK-E 命中） | 命令是实例级作用域，已按库归属并去重；顺带告知 N 个库里实际执行了几个 |
| W10 `TIME_BUDGET_EXCEEDED` | WARNING | 超出 180s 总预算 | 剩余库标 SKIPPED 未统计，请分批 |
| W11 `DB_ENUM_FAILED` | WARNING | `SHOW DATABASES` 失败但指定了 `database` | 降级为只统计该库；库限定名判据退化，可能影响跨库行归属 |
| W12 `SUBPARTITION_EXCLUDED` | INFO | 基线中存在 `_tdsql_subp<数字>` 结尾的二级分区物理子表 | 告知剔除了多少张、为什么剔除；逐库数量见「二级分区子表」列。这是正常现象，不是问题 |

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
| ADR-12（Rev.D 修订） | **作用域指纹比对**：连续两个非空库的原始 `(库,表)` 结果集若逐条相同，即判定实例级作用域，其余库跳过 | ① Rev.B 的"累计表集 == information_schema 基线" ② 检测到跨库行就 break ③ 无条件跑满 N 库 | **① 已被 T14 实测打死**：`lzbj_ecif` Proxy 口径 215 vs 基线 293，两者基本不可能相等，该判据永远不成立、优化永远不生效；②"看到跨库行"只证明不限于当前库，不证明**已覆盖全部**；③实例级作用域下要跑 3N 条重复命令。指纹比对是**充分必要**的：当前库作用域下两库的库限定名前缀必然不同，非空集合不可能相等——相等即证明与当前库无关。代价固定为 6 条命令 |
| ADR-13 | 单条命令 `read_timeout=30s` + 整体 `180s` 预算 | 依赖连接默认 `read_timeout=10s` | 保留。RISK-F 已裁决为不挂起，但连接默认 10s 对大库仍偏紧（`lzbj_ecif` 215 张表虽只用 0.002s，更大的库未取样）；30s+180s 双层兜底让最坏情况可预期，代价为零 |
| ADR-14 | 超预算的库标 `SKIPPED` 而非 `FAILED` | 统一标 FAILED | "没来得及测"和"测了但错了"处置动作不同：前者重跑/分批即可，后者要查权限或端口。混成一个数会误导排障方向 |
| ADR-15（Rev.D 新增） | `RECON_MISMATCH` **汇总成一条**告警，逐库明细放 `item.detail` | 逐库一条告警 | 保留。Rev.E 剔除子分区后该告警不再常态触发，但**真出问题时仍可能多库同时命中**（例如一批表漏进 Proxy 路由表），50 库实例上逐库一条就是 50 条横幅。汇总告警给合计与库名，明细留在表格行里，信息一点不少 |
| ADR-16（Rev.D 新增，Rev.E 修订） | 四个数字采用 **Proxy 口径**，逻辑基线数并排呈现 | ① 用 `information_schema` 当准 ② 只显示 Proxy 口径 | 需求问的是"单表/广播表/分片表各多少张"，这三个概念**只有 Proxy 知道**，`information_schema` 没有这个维度。Rev.E 剔除二级分区子表后两个口径精确相等（215 == 215），并排呈现从"让用户自己判断"变成"互相印证" |
| ADR-17（Rev.E 新增） | 把 `_tdsql_subp<数字>` 结尾的二级分区物理子表**从基线中剔除并单列计数** | ① 计入基线（Rev.D 做法） ② 计入总表数 | ①会让 `RECON_MISMATCH` 在每个有二级分区的库上**永久亮着**——一个永远亮的告警是背景噪声，用户学会无视后真正的不一致就再没人看得见；②用户认知里 `cus_pub_translog` 是一张表不是十三张，Proxy 也只返回逻辑表名。剔除后 215 == 215，告警重获信号价值。子表数单列 + `SUBPARTITION_EXCLUDED` 告警，数据不藏 |
| ADR-18（Rev.F 新增） | 表结构在**首次发布前定稿**；发布后若需扩列，**新增 `v11/111_*.sql` 用 `ALTER TABLE … ADD COLUMN`**，绝不回头编辑 `110_*.sql` | ① 直接改 `110_*.sql` ② 把表并进 `database.py::_create_all_tables` | ①v1.6.2.2 起 checksum 漂移会让**所有已部署实例启动失败关闭**（§2.7 M-3），补救需人工往 `_KNOWN_RECONCILIATIONS` 加账本三元组，代价远高于新增一个迁移文件；②`_create_all_tables` 是 27 张表的大列表，改它等于把 `database.py` 的改动面从 1 行放大到一整段 DDL，与最小化修改原则冲突（ADR-6 已述） |

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
| `backend/schema/v11/110_table_type_stats.sql` | 全新 | 两张新表，`CREATE TABLE IF NOT EXISTS` 幂等 | **低**（Rev.F 上调）。v1.6.2.2 起迁移失败即**启动关闭**（§2.7 M-1），且**发布即冻结**（M-3）。缓解：纯 `CREATE TABLE IF NOT EXISTS`、不进列级严格验收（M-2）、表结构须在打包前定稿（ADR-18） |
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

> **四轮实测已完成**（2026-08-29），结论见 §10.1 裁决表，原始数据入附录 B。
> **T14 / T15 / D3 均已判决**，其中 T14 与 D3 触发了实质性设计修订（ADR-12/15/16/17）
> 但**均未推翻设计**。现在只剩 **T13**（命令作用域）一项，**不阻断开发**。
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
| — 新发现 | 赤兔对 `lzbj_ecif` 执行 `without shardkey`（该库无单表）**一直转圈** | **RISK-F**，由第二轮 T15 判决 |
| **T15 空结果行为**（第二轮） | **不挂起**。`mysql` 直连 Proxy 返回 `Query OK, 0 rows affected (0.001 sec)`——是 **OK 包**不是空结果集；赤兔转圈是其前端等列元数据所致 | **设计不改**。PyMySQL≥1.1.0 对 OK 包 `fetchall()` 返回 `[]`、`description` 为 `None`，本模块按"该类 0 张"正常处理。超时保险保留但永不触发。**新增语义**：OK 包与"命令不被支持"协议上不可区分，交叉校验是唯一探测器 |
| **T10 性能**（第二轮顺带取得） | `lzbj_ecif` 共 215 张表（98 分片 + 117 广播 + 0 单表），三条命令分别 0.001 / 0.002 / 0.001 秒 | 单库开销可忽略；总耗时瓶颈只可能来自库数（取决于 T13 的作用域结论） |
| **T14 交叉校验**（第三轮） | **不一致，且差得很大**：`lzbj_ecif` Proxy 口径 **215**（98+117+0）vs `information_schema` 基线 **293**，**差 78 张（27%）** | **RISK-B 确认成立**，触发三处修订：**ADR-16** 四个数字用 Proxy 口径、基线并排呈现不覆盖；**ADR-15** `RECON_MISMATCH` 汇总成一条（差异每库都有，逐库告警会刷屏）；**ADR-12 改写** —— Rev.B 用"累计表集 == 基线"做作用域探测的完备性证明，实测证明两者基本不可能相等，该判据永远不成立，改用不依赖基线的**指纹比对** |
| **D3 差异成因**（第四轮） | **78 张全部是二级分区物理子表**，命名 `<逻辑表>_tdsql_subp190001` / `_tdsql_subp202601`…`202612`；6 张 `sub_func:month` 的表 × 13 = 78，账目精确闭合 | **ADR-17**：`_tdsql_subp<数字>` 结尾的表从基线剔除、单列 `subpartition_tables`。剔除后**逻辑基线 215 == Proxy 口径 215**，`RECON_MISMATCH` 不再常态触发，重获信号价值 |

### 10.2 仍待测（T13 一项，不阻断）

---

### T13 · 命令的作用域是实例级还是当前库？（判决 RISK-E，**不阻断开发**）

**前提**：需要一个**至少有 2 个业务库**的分布式实例。若内网没有这种实例，请注明"无"——
设计在两种作用域下都正确，这条只是让我们知道实际走哪条路径、以及性能量级差多少。

> 最省事的做法：就用 T15 那个会话（`10.243.20.13:15005` / `checksql`），
> 先敲一句 `SHOW DATABASES;` 看这个实例除了 `lzbj_ecif` 还有没有别的业务库。
> 如果有，站在 `lzbj_ecif` 上执行的 `with shardkey` 结果里（附录 B 已有 98 行完整输出）
> **全部都是 `lzbj_ecif.*` 前缀，一条别的库都没有** —— 那就直接判定为**当前库作用域**，
> 这条测试当场就结了，不用再敲第二条命令。

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
* 若同时出现 `库A.*` 和 `库B.*` → **实例级作用域**。此时设计的指纹比对会在第二个库上生效，
  实际只会执行 3 条命令而不是 3N 条，响应时间与库数无关。

> **为什么我强烈怀疑是实例级**：返回值带库前缀这件事本身就没必要（只看当前库的话
> 裸表名就够了）；更关键的是原厂那句"使用『数据库名 + 表名』去重"——如果每次执行
> 只返回当前库，逐库遍历根本不会产生重复行，这句话无从谈起。

---

### T14 · 三类并集 vs `information_schema` 基线 —— ✅ **已完成（2026-08-29）**

```sql
MySQL [lzbj_ecif]> SELECT COUNT(*) FROM information_schema.TABLES
                   WHERE TABLE_SCHEMA='lzbj_ecif' AND TABLE_TYPE='BASE TABLE';
+----------+
| COUNT(*) |
+----------+
|      293 |
+----------+
```

**结论：RISK-B 成立。** Proxy 口径 215（98+117+0），基线 293，**差 78 张（27%）**。

**设计动作（已落地，见 §3.3 RISK-B / ADR-15 / ADR-16）**：
四个数字采用 Proxy 口径；`baseline_tables` 并排呈现；差集明细进「说明」列；
`RECON_MISMATCH` 汇总成一条。**并连带修订了 ADR-12**——原先用
"累计表集 == 基线"作为作用域探测的完备性证明，现已知两者基本不可能相等，
该判据永远不成立，改用不依赖基线的**指纹比对**。

---

### D1～D3 · 那 78 张是什么？—— ✅ **已完成（2026-08-29）：二级分区物理子表**

D3 一条查询即定案，回了 71 行，形态如下（节选）：

```
cus_bas_merge_log                          ← 逻辑表
cus_bas_merge_log_tdsql_subp190001         ← 兜底/溢出分区
cus_bas_merge_log_tdsql_subp202601
…
cus_bas_merge_log_tdsql_subp202612         ← 每张 13 个子分区
cus_pub_translog_his                       ← 无二级分区，被 LIKE 顺带捞到
```

**账目闭合**：6 张 `sub_func:month` 的表 × 13 个子分区 = **78**，
正是 293 − 215 的差值。剔除后 **逻辑基线 215 == Proxy 口径 215**。

**设计动作（ADR-17，已落地）**：`_tdsql_subp<数字>` 结尾的表从基线中剔除、
单列为 `subpartition_tables`，并出一条 `SUBPARTITION_EXCLUDED`（INFO）。
`RECON_MISMATCH` 从此不再常态触发——**一旦亮起就意味着真有表没进 Proxy 路由表**。

D1 / D2 已无必要执行。

---

### T15 · 空结果集：UI 问题还是命令挂起？—— ✅ **已完成（2026-08-29）**

**执行**（`mysql --comments -h 10.243.20.13 -P 15005 -u checksql -p`，
服务端 `8.0.33-v24-txsql-22.6.9-20250509`）：

```
MySQL [lzbj_ecif]> /*proxy*/show table without shardkey;
Query OK, 0 rows affected (0.001 sec)
```

**结论 = A（UI 问题），且比 A 更有信息量**：返回的不是"空结果集"而是
**OK 包**（无列元数据）。0.001 秒返回，同一 session 随后的
`with noshardkey_allset`（117 行）与 `with shardkey`（98 行）均正常，
说明连接完全没受影响。赤兔转圈是它的前端在等结果集结构，与命令无关。

**设计动作**：不改。详见 §3.3 RISK-F 的完整裁决与 PyMySQL 行为核对表。
新增两条护栏用例：`test_extract_pairs_tolerates_none_rows` 与
`test_ok_packet_yields_zero_without_warning`。

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
【T13】除 lzbj_ecif 外是否还有业务库（有/无）：
【T13】（有的话）with shardkey 结果里出现过几种库前缀：
【T14】lzbj_ecif 的 base_tables= ，views= ，是否 = 215：
【T14】（或）sqltuning 的 base_tables= ，是否 = 29：
【T14】（不等时）双向差集表名 / 表名是否带分片数字后缀：
【T09】登记账号能否执行（是/否，errno）：
【T12】单分片实例：有/无，若有则输出：
```

> T09 其实已经被 T15 顺带证明了一半：那次用的 `checksql` 账号能正常执行三条命令。
> 只要平台"实例管理"里登记的就是 `checksql`，T09 即视为通过。

### 10.5 GATE-2 放行判据（本轮实测结论 → 设计动作）

| 实测结论 | 设计动作 | 是否阻断开发 |
|---|---|---|
| T13 = 当前库作用域 | 第二个库指纹不同 → 逐库执行；`INSTANCE_WIDE_SCOPE` 不触发。代码无需改 | 否 |
| T13 = 实例级作用域 | 指纹比对在第二个库上判定成立，**6 条命令**搞定；`INSTANCE_WIDE_SCOPE` 会显示——**符合设计预期** | 否 |
| T13 无多库实例可测 | 两条路径都已实现且都有单测覆盖，按现状开发 | 否 |
| ~~T14 / D3~~ | ✅ **已完成**。差异 78 张查明为二级分区物理子表，已按 ADR-17 剔除，两口径精确对齐 | — |
| ~~T15~~ | ✅ **已完成 = A**。设计不变，超时保险留作兜底 | — |
| T09 登记账号无权限 | 出授权说明，由 DBA 补授权 | **是**（非代码问题） |
| T10 单库 > 1s 且 T13 = 当前库作用域 且库数 > 20 | 需追加"异步任务 + 进度"设计 | **是**（设计升版）。已知 `lzbj_ecif`（215 张表）单库仅 0.004s，风险很低 |
| T12 存在单分片分布式实例 | UI 强化 W5 文案 | 否（前端 1 行文案） |

**只要没有命中"是"，开发即可按附录 A 照图施工。**

---


## 11. 测试设计（开发期，可在本地 MariaDB 13306 上跑）

`tests/test_table_type_stats.py`（附录 A.4），**42 项，除落库 2 项外全部离线，
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
| `test_distributed_instance_wide_scope` | 实例级作用域：总数按 `(库,表)` 去重不放大；**前两库指纹相同即停止扫描**；点亮 W9 | **RISK-E 核心护栏** |
| `test_distributed_per_db_scope_still_loops` | 当前库作用域：两库指纹不同，逐库执行，不点 W9 | ADR-12 |
| `test_single_database_filter_ignores_other_dbs` | 指定库时，实例级结果里其他库的行必须丢弃 | E-20 |
| `test_system_db_rows_are_dropped` | `mysql.user` / `sysdb.foo` 不得计入 | E-19 |
| `test_distributed_view_is_excluded` | 命令返回视图时按基线 VIEW 名单扣除 | 原厂口径 |
| `test_distributed_overlap_does_not_double_count` | 若三类重叠，总数不重复计算 + W2 | RISK-A 保险 |
| `test_distributed_recon_mismatch` | 双向差集写进 `item.detail`，告警只出一条 | RISK-B |
| `test_recon_mismatch_is_aggregated_not_per_db` | 三个库都不一致时**只出一条**告警，含库数与合计 | **ADR-15 护栏** |
| `test_scope_probe_ignores_baseline_mismatch` | 基线与 Proxy 口径差得再远，作用域判定照样成立 | **ADR-12 修订护栏** |
| `test_baseline_excludes_tdsql_subpartitions` | 13 个 `_tdsql_subp` 子表不计入逻辑基线、单列计数、不报 `RECON_MISMATCH` | **ADR-17 核心护栏** |
| `test_subpartition_regex_is_anchored` | 只剔除 `_tdsql_subp<纯数字>` 结尾；`my_tdsql_subp202601_backup` 不误伤 | ADR-17 边界 |
| `test_lzbj_ecif_uat_baseline` | **端到端对数基准**：98/117/0/215/215/78，告警仅 `SUBPARTITION_EXCLUDED` | **UAT 基准编码为单测** |
| `test_distributed_partial_failure` | 单库失败只降级该库，其余库照常，`failed_databases=1` | ADR-5 |
| `test_command_timeout_is_reported_not_hung` | 读超时渲染为"读超时（30s）"而非裸异常 | RISK-F / E-21 |
| `test_time_budget_skips_remaining` | 超预算的库标 `SKIPPED`、不计入总数、W10 | ADR-14 / E-22 |
| `test_distributed_all_1064_flags_wrong_endpoint` | 全 1064 → W8 | §2.4 |
| `test_select_db_failure_is_isolated` | 切库失败只影响该库，临时池仍被关闭 | E-3 |
| `test_shared_pool_is_never_switched` | **共享池连接上不得发生任何 `select_db`** | **ADR-3 核心护栏** |
| `test_empty_result_set_is_not_an_error` | 空结果集 = 合法的 0，不告警（对应 `lzbj_ecif` 无单表） | E-10 |
| `test_extract_pairs_tolerates_none_rows` | 驱动即使回 `None` 也不抛异常（OK 包路径防御） | RISK-F |
| `test_ok_packet_yields_zero_without_warning` | **OK 包**（`Query OK, 0 rows affected`）→ 该类计 0、不告警、不降级、不进 `shape` | **RISK-F 核心护栏** |
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
`python -m pytest` **42 项全部通过**，含对本地 MariaDB(13306) 的真实落库用例。


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
- [ ] **对数基准**：`lzbj_ecif` 应输出 总表 **215** / 单表 **0** / 广播表 **117** / 分片表 **98** / 逻辑基线 **215** / 二级分区子表 **78**，**且不出现 `RECON_MISMATCH`**，只有一条 `SUBPARTITION_EXCLUDED`（INFO）。六个数字全对才算通过
- [ ] `RECON_MISMATCH` 无论涉及几个库都**只出一条**告警；逐库差异在表格「说明」列
- [ ] 二级分区子表不计入总表数，但逐库「二级分区子表」列如实显示
- [ ] **迁移专项**：全新安装与存量升级各跑一次 `init_db()`，两次启动均无 `MigrationError`；
      `schema_migrations` 中出现 `v11_110_table_type_stats` 且 checksum 与文件一致
- [ ] **迁移专项**：连续启动两次（模拟重启），第二次走 `_structure_state` → `valid` 跳过，不重复执行
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
| KL-6 | 统计为同步执行 | 库数 × 3 条命令，大实例可能较慢 | T10 定量；若超阈值则升版为异步任务（GATE-2 阻断项）。若 T13 判定为实例级作用域，指纹比对会把命令数压到 6 条，本项自然消解 |
| KL-7 | 结果为快照，不反映采集期间的 DDL 变更 | 无事务一致性保证 | 结果带 `created_at`，UI 标注"采集时刻快照" |
| KL-8 | 命令作用域（当前库 / 实例级）未裁决 | 返回库限定名 + 原厂"库名+表名去重"的措辞都指向实例级，但缺少多业务库实例的实测 | 设计在两种作用域下都正确（ADR-11/12）；T13 只影响性能量级与 W9 是否显示。**作用域判据已改为不依赖 information_schema 的指纹比对**，固定代价 6 条命令 |
| KL-9 | 空结果集是否导致命令挂起未裁决 | 赤兔页面对无单表的库一直转圈，原因未定 | 30s 读超时 + 180s 总预算兜底；T15 判决，若为 B 则升 Rev.C |
| KL-10 | 二级分区子表识别依赖命名约定 `_tdsql_subp<数字>` | D3 实测确认该命名（`_tdsql_subp190001` / `_tdsql_subp202601`…），但无官方文档背书 | 正则锚定末尾+纯数字后缀，误判方向安全：漏识别 → `RECON_MISMATCH` 显式报出（可见），不会静默少算 |
| KL-11 | `info` 列内容（shardkey / sub_shardkey / auto_increment）本期未使用 | 形态已入附录 B | 为将来"分片键分布"类需求预留，不在本期范围 |
| KL-12（Rev.F 新增） | 迁移文件发布后**内容冻结**，改一个字符都会让已部署实例启动失败关闭 | v1.6.2.2 的 O-30 调和账本机制（§2.7 M-3） | 表结构须在打包前定稿；发布后扩列走新增 `111_*.sql`（ADR-18）。**这是全项目所有新增迁移文件的共性约束，不是 G14 特有** |

---

## 14. 附录 A · 成品代码（照图施工）

> **本附录四个文件已在本地环境完整验证**：用 importlib 把 A.1 挂载为
> `backend.services.table_type_stats_service`（**仓库代码零改动**），
> `python -m pytest` **42 项全部通过**，其中含对本地 MariaDB(13306) 的真实落库用例；
> A.2 的路由在 FastAPI 下正确注册出 3 条路径。实施者可直接落盘，不需要二次设计。
>
> **Rev.B～Rev.D 相对 Rev.A 的实质变化**（全部源自三轮内网实测）：
> 1. `_EXACT_NAME_COLS` 首位加入实测确认的列名 `db_table`，`info` 加入排除词；
> 2. `_extract_names` → `_extract_pairs`：解析 **`(库, 表)` 二元组**而不是裸表名，
>    并回报是否含跨库行（RISK-E）；
> 3. 采集从"每库三个集合"改为**全局 `kind_map[(库,表)]`**，按行内库限定名归属、
>    全局去重、逐库反查计数（ADR-11）；
> 4. **作用域指纹比对**（ADR-12，Rev.D 改写）：连续两个非空库的原始结果集逐条相同
>    即证明实例级作用域，其余库跳过。**判据不依赖 `information_schema`**——
>    T14 实测证明 Proxy 口径与基线基本不等，Rev.B 那版基于基线的判据永远不成立；
> 5. `COMMAND_READ_TIMEOUT=30` / `TOTAL_BUDGET_SECONDS=180` 双层时长兜底
>    与 `SKIPPED` 状态（ADR-13/14）；OK 包（`Query OK, 0 rows affected`）按 0 张处理；
> 6. 新增 `skipped_databases` 与 `baseline_tables` 字段（ADR-16 双口径并排呈现）；
> 7. `RECON_MISMATCH` 汇总成一条告警（ADR-15）；
> 8. **剔除 `_tdsql_subp<数字>` 二级分区物理子表**并单列 `subpartition_tables`
>    （ADR-17，Rev.E）——剔除后逻辑基线与 Proxy 口径精确相等，
>    交叉校验从"永久亮着的噪声"变回"亮起就有事"的信号。
>
> **GATE-2 已无阻断项**；剩余的 T13 与 D1～D3 都不会改动这四个文件。

### A.1 `backend/services/table_type_stats_service.py`（新增，656 行）

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
  · 某类为空时 Proxy 返回的是【OK 包】而非空结果集
    （`Query OK, 0 rows affected`，0.001 秒返回，不是挂起）。
    PyMySQL >= 1.1.0 对 OK 包 fetchall() 返回 []，cursor.description 为 None，
    故本模块天然按"该类 0 张"处理；赤兔页面转圈是其前端等列元数据所致，与本模块无关。
  · information_schema 会把【二级分区的物理子表】也列为 BASE TABLE，命名形如
    <逻辑表>_tdsql_subp190001 / _tdsql_subp202601。lzbj_ecif 实测：
    基线 293 = 逻辑表 215 + 子分区 78（6 张 sub_func:month 的表 × 13 个子分区）。
    本模块把子分区表从基线中剔除并单列计数，剔除后逻辑基线 215 与 Proxy 口径【精确相等】。
    故不得用未剔除的基线与 Proxy 口径比对（会产生 27% 的常态误报）。

设计要点（详见 DESIGN-v1.6.3.0）：
  · 结果按【库限定名】归属到库，而不是无条件算在当前会话库上——
    命令的作用域是否为实例级尚未确证，按库归属 + (库,表) 去重使两种
    作用域都得到正确结果（§3.3 RISK-E）。这也正是原厂"使用数据库名+表名
    去重"这句话的由来。
  · 基线口径：剔除二级分区物理子表后与 Proxy 口径精确对齐，使交叉校验重新成为
    有效信号（否则每个库都会常态告警 27%，等于把告警训练成噪声）。
  · 作用域自判：连续两个非空库的原始结果集若逐条相同，即证明命令是实例级作用域，
    其余库无需再执行——把 3×N 条命令压到 6 条。判据不依赖 information_schema
    （实测证明并集与基线不等，用基线做判据会永不成立）。
  · 总时长预算 + 显式读超时：命令挂起不会拖垮整个请求（§3.3 RISK-F）。
  · 绝不在共享连接池上切库；另建 pool_size=1 的临时池（ADR-3）。

全部只读。不修改任何既有模块。
"""
from __future__ import annotations

import dataclasses
import json
import logging
import re
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

# TDSQL 二级分区的物理子表命名（2026-08-29 内网实测，设计附录 B.5）：
#   cus_pub_translog_tdsql_subp190001 / _tdsql_subp202601 … _tdsql_subp202612
# 它们在 information_schema 里是独立的 BASE TABLE，但【不是】用户认知中的"表"，
# Proxy 的 show table 也只返回逻辑表名。故从基线中剔除并单列计数。
# 后缀限定为【纯数字】并锚定到末尾：既覆盖实测的 190001 / 202601-202612，
# 又不会误伤 my_tdsql_subp202601_backup 这类用户自建表。
# 万一某版本用了非数字后缀，本模块会把它当逻辑表 —— 后果是 RECON_MISMATCH
# 把它显式报出来（可见），而不是静默少算（不可见）。方向是安全的。
_SUBPARTITION_RE = re.compile(r"_tdsql_subp\d+$", re.IGNORECASE)

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
    """取 information_schema 全量名单。

    返回 {db: {"base": 逻辑表, "view": 视图, "subp": 二级分区物理子表}}。

    要名字不要计数：集中式分支取 len(base)；分布式分支需要名字做视图扣除，
    以及逐库双向集合差（差的是哪几张表要写进「说明」列）。
    注意：作用域判定【不】使用基线——实测 Proxy 口径与基线相差可达 27%
    （lzbj_ecif 215 vs 293），用基线做判据会永不成立（ADR-12）。
    """
    out = {d: {"base": set(), "view": set(), "subp": set()} for d in dbs}
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
            # 二级分区物理子表单独归类，不计入逻辑基线
            if _SUBPARTITION_RE.search(name):
                out[key]["subp"].add(name)
            else:
                out[key]["base"].add(name)
        elif ttype == "VIEW":
            out[key]["view"].add(name)
    return out


def _blank_item(db: str, baseline: dict) -> dict:
    b = baseline.get(db, {})
    return {"db_name": db, "total_tables": 0, "shard_tables": 0,
            "broadcast_tables": 0, "single_tables": 0,
            "baseline_tables": len(b.get("base", ())),
            "subpartition_tables": len(b.get("subp", ())),
            "status": "OK", "detail": ""}


def _collect_centralized(dbs: list, baseline: dict):
    """集中式：纯内存换算，不发任何查询，不发任何 /*proxy*/ 命令（ADR-4）。"""
    items = []
    totals = {"shard": 0, "broadcast": 0, "single": 0, "total": 0,
              "baseline": 0, "subp": 0, "overlap": 0, "failed": 0, "skipped": 0}
    for db in dbs:
        n = len(baseline.get(db, {}).get("base", ()))
        item = _blank_item(db, baseline)
        item["total_tables"] = n
        item["single_tables"] = n
        items.append(item)
        totals["single"] += n
        totals["total"] += n
        totals["baseline"] += n
        totals["subp"] += item["subpartition_tables"]
    return items, [], {}, totals


def _collect_distributed(pool, dbs: list, baseline: dict, known_dbs: set):
    """分布式：逐业务库执行三条 /*proxy*/ 命令，按【库限定名】归属去重。

    连接隔离：另建 pool_size=1 的临时池，切库不污染共享池（ADR-3）。
    异常隔离：所有异常都在 with 块内部吃掉——若让异常穿出
      TDSQLConnectionPool.get_connection() 的 with，池会重建连接并中断循环。
    作用域自判（ADR-12 修订）：记录首个非空库的原始 (库,表) 结果指纹；
      第二个非空库若返回逐条相同的集合，即证明命令是实例级作用域
      （当前库作用域下两库的库限定名前缀必然不同，集合不可能相等），
      其余库全部跳过。
    时长兜底：单条命令读超时 COMMAND_READ_TIMEOUT，整体不超过 TOTAL_BUDGET_SECONDS。
    """
    items, warnings, shape = [], [], {}
    totals = {"shard": 0, "broadcast": 0, "single": 0, "total": 0,
              "baseline": 0, "subp": 0, "overlap": 0, "failed": 0, "skipped": 0}
    if not dbs:
        return items, warnings, shape, totals

    target = {d.lower() for d in dbs}
    canon = {d.lower(): d for d in dbs}
    kind_map = {}          # (db, table) -> kind
    kinds_seen = {}        # (db, table) -> {kind, ...}
    failed, skipped = {}, {}
    shape_reported = False
    instance_wide = False
    scope_signature = None     # 首个非空库的原始 (库,表) 指纹
    scope_decided = ""         # "" | "instance_wide" | "per_db"
    syntax_errors = 0
    scanned = 0
    started = time.monotonic()

    cfg = dataclasses.replace(pool.config, database=dbs[0],
                              read_timeout=COMMAND_READ_TIMEOUT)
    tmp = _new_pool(cfg, pool_size=1)
    try:
        with tmp.get_connection() as conn:
            for db in dbs:
                if scope_decided == "instance_wide":
                    skipped[db] = "coverage"
                    continue
                if time.monotonic() - started > TOTAL_BUDGET_SECONDS:
                    skipped[db] = "budget"
                    continue

                scanned += 1
                detail = ""
                raw_pairs = set()      # 本库三条命令返回的原始并集，未过滤
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
                        # rows 可能是 OK 包（某类为空时 TDSQL 返回
                        # `Query OK, 0 rows affected`）——此时 fetchall() 为 []
                        # 且无列元数据，_extract_pairs 按 0 张处理，不是错误。
                        pairs, columns, guessed, cross = _extract_pairs(
                            rows, db, known_dbs)
                        raw_pairs |= pairs
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
                    continue
                # ── 作用域自判：只用命令自身的返回，不依赖 information_schema ──
                if not scope_decided and raw_pairs:
                    sig = frozenset(raw_pairs)
                    if scope_signature is None:
                        scope_signature = sig
                    elif sig == scope_signature:
                        scope_decided = "instance_wide"
                        instance_wide = True
                    else:
                        scope_decided = "per_db"
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
    recon = []                 # [(db, 仅Proxy可见数, 仅基线可见数)]

    for db in dbs:
        item = _blank_item(db, baseline)
        totals["baseline"] += item["baseline_tables"]
        totals["subp"] += item["subpartition_tables"]
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
            recon.append((db, len(only_proxy), len(only_base)))
            d2 = (f"Proxy 口径 {len(got)} 张，information_schema 基线 {len(base)} 张")
            if only_base:
                d2 += (f"；仅基线可见({len(only_base)}): {_diff_sample(only_base)}")
            if only_proxy:
                d2 += (f"；仅 Proxy 可见({len(only_proxy)}): {_diff_sample(only_proxy)}")
            sep = "；" if item["detail"] else ""
            item["detail"] = (item["detail"] + sep + d2)[:512]
        items.append(item)

    totals["overlap"] = overlap_total
    if overlap_total:
        warnings.append(_warn(
            "KIND_OVERLAP", "WARNING", "",
            f"三类结果集存在 {overlap_total} 处重叠，"
            f"已按 分片>广播>单表 归一化去重，总数未重复计算"))
    if recon:
        # 汇总成一条，避免逐库刷屏（实测该差异在每个库上都会出现）
        sum_proxy = sum(x[1] for x in recon)
        sum_base = sum(x[2] for x in recon)
        names = ", ".join(x[0] for x in recon[:5])
        if len(recon) > 5:
            names += f" …等 {len(recon)} 个库"
        warnings.append(_warn(
            "RECON_MISMATCH", "WARNING", "",
            f"{len(recon)} 个库的 Proxy 口径与 information_schema 逻辑基线不一致："
            f"仅基线可见合计 {sum_base} 张、仅 Proxy 可见合计 {sum_proxy} 张（{names}）。"
            f"二级分区物理子表已剔除，故此差异【不是】分区造成的，"
            f"可能存在未纳入 Proxy 路由的表；差异明细见各行「说明」"))
    if totals["subp"]:
        warnings.append(_warn(
            "SUBPARTITION_EXCLUDED", "INFO", "",
            f"information_schema 中另有 {totals['subp']} 张二级分区物理子表"
            f"（形如 xxx_tdsql_subp202601），按逻辑表口径未计入总数；"
            f"逐库数量见「二级分区子表」列"))
    if instance_wide:
        warnings.append(_warn(
            "INSTANCE_WIDE_SCOPE", "INFO", "",
            f"本版本 /*proxy*/show table 返回实例级全量，"
            f"已按库限定名归属并按(库,表)去重；"
            f"{len(dbs)} 个业务库中实际执行了 {scanned} 个，其余经作用域判定跳过"))
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
        "baseline_tables": totals["baseline"],
        "subpartition_tables": totals["subp"],
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
            "shard_tables, broadcast_tables, single_tables, baseline_tables, "
            "subpartition_tables, failed_databases, skipped_databases, "
            "overlap_count, warnings_json, created_by) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (connection_id, database, res["instance_type"], res["type_source"],
             res["database_count"], res["total_tables"], res["shard_tables"],
             res["broadcast_tables"], res["single_tables"],
             res["baseline_tables"], res["subpartition_tables"],
             res["failed_databases"], res["skipped_databases"],
             res["overlap_count"],
             json.dumps(res["warnings"], ensure_ascii=False), operator))
        stat_id = cur.lastrowid
        for it in res["items"]:
            conn.execute(
                "INSERT INTO table_type_stat_item (stat_id, db_name, total_tables, "
                "shard_tables, broadcast_tables, single_tables, baseline_tables, "
                "subpartition_tables, status, detail) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (stat_id, it["db_name"], it["total_tables"], it["shard_tables"],
                 it["broadcast_tables"], it["single_tables"],
                 it["baseline_tables"], it["subpartition_tables"],
                 it["status"], it["detail"]))
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

### A.3 `backend/schema/v11/110_table_type_stats.sql`（新增，41 行）

> 迁移器会**逐行剔除以 `--` 开头的行**再按 `;` 切分（`backend/schema/migrator.py:159-164`，v1.6.2.2 后行号），
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
    baseline_tables     INT DEFAULT 0,
    subpartition_tables INT DEFAULT 0,
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
    subpartition_tables INT DEFAULT 0,
    status              VARCHAR(16) DEFAULT 'OK',
    detail              VARCHAR(512) DEFAULT '',
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_ttsi (stat_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### A.4 `tests/test_table_type_stats.py`（新增，749 行）

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
    """实例级作用域：按库归属拆分、(库,表) 去重；连续两库指纹相同即停止扫描。

    判据不依赖 information_schema —— 实测 lzbj_ecif 三类并集 215 vs 基线 293，
    用"并集 == 基线"做判据会永远不成立（ADR-12 修订的由来）。
    """
    _patch_ctx(monkeypatch, "distributed")
    allrows_shard = _rows(["db_a.s1", "db_b.s2"], info="shardkey:id")
    allrows_bcast = _rows(["db_b.b1"], info="shardkey:noshardkey_allset")
    allrows_single = _rows(["db_a.n1"])
    per_db = {}
    for d in ("db_a", "db_b", "db_c"):
        per_db[(d, svc.SQL_SHARD)] = allrows_shard
        per_db[(d, svc.SQL_BROADCAST)] = allrows_bcast
        per_db[(d, svc.SQL_SINGLE)] = allrows_single
    pool = FakePool(databases=["db_a", "db_b", "db_c", "mysql"],
                    info_schema={"db_a": {"base": ["s1", "n1"]},
                                 "db_b": {"base": ["s2", "b1"]},
                                 "db_c": {"base": []}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    # 总数按 (库,表) 去重，不是每个库各算一遍
    assert res["total_tables"] == 4
    assert (res["shard_tables"], res["broadcast_tables"],
            res["single_tables"]) == (2, 1, 1)
    by_db = {i["db_name"]: i for i in res["items"]}
    assert by_db["db_a"]["total_tables"] == 2
    assert by_db["db_b"]["total_tables"] == 2
    # 前两库指纹相同 ⇒ 判定实例级，db_c 不再执行
    assert pool.selected == ["db_a", "db_b"]
    assert any(w["code"] == "INSTANCE_WIDE_SCOPE" for w in res["warnings"])


def test_scope_probe_ignores_baseline_mismatch(monkeypatch):
    """作用域判定不得依赖 information_schema：基线与并集差得再远也要判对。

    夹具照搬 lzbj_ecif 的真实比例：Proxy 口径显著少于基线。
    """
    _patch_ctx(monkeypatch, "distributed")
    rows = _rows(["db_a.s1", "db_b.s2"])
    per_db = {}
    for d in ("db_a", "db_b", "db_c"):
        per_db[(d, svc.SQL_SHARD)] = rows
        per_db[(d, svc.SQL_BROADCAST)] = []
        per_db[(d, svc.SQL_SINGLE)] = []
    pool = FakePool(databases=["db_a", "db_b", "db_c"],
                    info_schema={"db_a": {"base": ["s1", "ghost1", "ghost2"]},
                                 "db_b": {"base": ["s2", "ghost3"]},
                                 "db_c": {"base": ["ghost4"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert pool.selected == ["db_a", "db_b"]      # 判定成立，db_c 跳过
    assert res["total_tables"] == 2
    assert res["baseline_tables"] == 6            # 基线合计仍如实回报


def test_distributed_per_db_scope_still_loops(monkeypatch):
    """命令若为当前库作用域，两库指纹不同，必须逐库执行"""
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
    assert res["baseline_tables"] == 2
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
    """并集与 information_schema 不一致时，差异明细必须落到该库的 detail 上"""
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
    assert len(w) == 1 and "db_a" in w[0]["detail"]
    assert "ghost" in res["items"][0]["detail"]
    assert res["baseline_tables"] == 3 and res["total_tables"] == 2


def test_recon_mismatch_is_aggregated_not_per_db(monkeypatch):
    """实测 lzbj_ecif 差 78/293 —— 差异会出现在每个库上，告警必须汇总成一条"""
    _patch_ctx(monkeypatch, "distributed")
    per_db, info = {}, {}
    for d in ("db_a", "db_b", "db_c"):
        per_db[(d, svc.SQL_SHARD)] = _rows([d + ".s1"])
        per_db[(d, svc.SQL_BROADCAST)] = []
        per_db[(d, svc.SQL_SINGLE)] = []
        info[d] = {"base": ["s1", "ghost1", "ghost2"]}
    pool = FakePool(databases=["db_a", "db_b", "db_c"],
                    info_schema=info, per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    w = [x for x in res["warnings"] if x["code"] == "RECON_MISMATCH"]
    assert len(w) == 1, "三个库都不一致，也只能出一条告警"
    assert "3 个库" in w[0]["detail"] and "6" in w[0]["detail"]
    assert all("ghost" in i["detail"] for i in res["items"])
    assert res["total_tables"] == 3 and res["baseline_tables"] == 9


def test_baseline_excludes_tdsql_subpartitions(monkeypatch):
    """information_schema 里的二级分区物理子表不得计入逻辑基线（实测命名形态）"""
    _patch_ctx(monkeypatch, "distributed")
    subp = ["cus_pub_translog_tdsql_subp190001"] + [
        f"cus_pub_translog_tdsql_subp2026{m:02d}" for m in range(1, 13)]
    per_db = {("db_a", svc.SQL_SHARD): _rows(["db_a.cus_pub_translog"]),
              ("db_a", svc.SQL_BROADCAST): [],
              ("db_a", svc.SQL_SINGLE): []}
    pool = FakePool(databases=["db_a"],
                    info_schema={"db_a": {"base": ["cus_pub_translog"] + subp}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert res["baseline_tables"] == 1          # 逻辑基线只算 1 张
    assert res["subpartition_tables"] == 13     # 13 个子分区单列
    assert res["total_tables"] == 1
    # 剔除后两个口径对齐 ⇒ 不得再报 RECON_MISMATCH
    assert not any(w["code"] == "RECON_MISMATCH" for w in res["warnings"])
    assert any(w["code"] == "SUBPARTITION_EXCLUDED" for w in res["warnings"])


def test_subpartition_regex_is_anchored():
    """只剔除以 _tdsql_subp<纯数字> 结尾的表，不误伤用户自建表"""
    # 实测形态
    assert svc._SUBPARTITION_RE.search("cus_pub_translog_tdsql_subp190001")
    assert svc._SUBPARTITION_RE.search("cus_pub_updatelog_detail_tdsql_subp202612")
    # 逻辑表本身不能被剔除
    assert not svc._SUBPARTITION_RE.search("cus_pub_translog")
    assert not svc._SUBPARTITION_RE.search("cus_pub_translog_his")
    # 用户自建的、后面还有后缀的表不能被误伤
    assert not svc._SUBPARTITION_RE.search("my_tdsql_subp202601_backup")
    assert not svc._SUBPARTITION_RE.search("tdsql_subp")
    assert not svc._SUBPARTITION_RE.search("t_tdsql_subp_manual")


def test_lzbj_ecif_uat_baseline(monkeypatch):
    """端到端对数基准：内网 lzbj_ecif 实测（设计附录 B.5）。

    Proxy: 98 分片 + 117 广播 + 0 单表 = 215
    information_schema: 293 = 逻辑 215 + 二级分区子表 78（6 张按月分区表 × 13）
    期望：总表 215 / 单表 0 / 广播 117 / 分片 98 / 逻辑基线 215 / 子分区 78，
          且【不报】RECON_MISMATCH。
    """
    _patch_ctx(monkeypatch, "distributed")
    shard = [f"t_shard_{i}" for i in range(98)]
    bcast = [f"t_bcast_{i}" for i in range(117)]
    month_tables = shard[:6]                     # 其中 6 张是按月二级分区
    subp = [f"{t}_tdsql_subp190001" for t in month_tables]
    for t in month_tables:
        subp += [f"{t}_tdsql_subp2026{m:02d}" for m in range(1, 13)]
    assert len(subp) == 78
    per_db = {
        ("lzbj_ecif", svc.SQL_SHARD): _rows([f"lzbj_ecif.{t}" for t in shard],
                                            info="shardkey:id"),
        ("lzbj_ecif", svc.SQL_BROADCAST): _rows([f"lzbj_ecif.{t}" for t in bcast],
                                                info="shardkey:noshardkey_allset"),
        ("lzbj_ecif", svc.SQL_SINGLE): None,      # OK 包：该库无单表
    }
    pool = FakePool(databases=["lzbj_ecif"],
                    info_schema={"lzbj_ecif": {"base": shard + bcast + subp}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert res["shard_tables"] == 98
    assert res["broadcast_tables"] == 117
    assert res["single_tables"] == 0
    assert res["total_tables"] == 215
    assert res["baseline_tables"] == 215          # 293 - 78
    assert res["subpartition_tables"] == 78
    assert not any(w["code"] == "RECON_MISMATCH" for w in res["warnings"]), \
        "剔除子分区后两个口径应精确对齐，不得常态告警"
    codes = {w["code"] for w in res["warnings"]}
    assert codes == {"SUBPARTITION_EXCLUDED"}


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


def test_extract_pairs_tolerates_none_rows():
    """OK 包路径的防御：即使驱动回 None 也不得抛异常（PyMySQL>=1.1.0 回 []）"""
    pairs, columns, guessed, cross = svc._extract_pairs(None, "db_a", {"db_a"})
    assert pairs == set() and columns == [] and guessed is False and cross is False


def test_ok_packet_yields_zero_without_warning(monkeypatch):
    """实测 lzbj_ecif：without shardkey 返回 OK 包（0 行）。

    该类必须计 0、不得告警、不得进 shape，也不得让该库降级为 FAILED。
    """
    _patch_ctx(monkeypatch, "distributed")
    per_db = {("db_a", svc.SQL_SHARD): _rows(["db_a.s1"], info="shardkey:id"),
              ("db_a", svc.SQL_BROADCAST): _rows(["db_a.b1"],
                                                 info="shardkey:noshardkey_allset"),
              ("db_a", svc.SQL_SINGLE): None}          # OK 包 → fetchall() -> []
    pool = FakePool(databases=["db_a"],
                    info_schema={"db_a": {"base": ["s1", "b1"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert res["single_tables"] == 0
    assert (res["shard_tables"], res["broadcast_tables"],
            res["total_tables"]) == (1, 1, 2)
    assert res["failed_databases"] == 0
    assert res["warnings"] == []
    assert "single" not in res["shape"]          # OK 包无列元数据


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

在 `_init_default_data` 的 `all_menus` 中 `'deep-diag-toolkit',`（第 1717 行）之后追加：

```python
        'deep-diag-tabletype',
```

> **这一行不是可选的。** `database.py:1775` 的
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
                  <span style="color:#909399"> · 逻辑基线 {{ deepResult.tabletype.baseline_tables }}</span>
                  <span v-if="deepResult.tabletype.subpartition_tables" style="color:#909399"> · 二级分区子表 {{ deepResult.tabletype.subpartition_tables }}（未计入）</span>
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
                  <el-table-column prop="baseline_tables" label="逻辑基线" width="100"></el-table-column>
                  <el-table-column prop="subpartition_tables" label="二级分区子表" width="120"></el-table-column>
                  <el-table-column prop="status" label="状态" width="90">
                    <template #default="s"><el-tag :type="s.row.status==='OK'?'success':(s.row.status==='SKIPPED'?'warning':'danger')" size="small">{{ s.row.status }}</el-tag></template>
                  </el-table-column>
                  <el-table-column prop="detail" label="说明"></el-table-column>
                </el-table>
                <div style="color:#909399;font-size:12px;margin-top:8px">
                  口径：分布式实例逐业务库执行 /*proxy*/show table with shardkey · with noshardkey_allset · without shardkey，按"库名+表名"去重；
                  集中式实例统计 information_schema.TABLES 中 TABLE_TYPE='BASE TABLE'，分片表与广播表恒为 0，不统计视图。
                  逻辑基线已剔除 xxx_tdsql_subp202601 这类二级分区物理子表（单列显示，不计入总数）。结果为采集时刻快照。
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

### B.3 第二轮实测（2026-08-29，`mysql` 客户端直连 Proxy）

**环境**：`mysql --comments -h 10.243.20.13 -P 15005 -u checksql -p`；
服务端 `8.0.33-v24-txsql-22.6.9-20250509`，库 `lzbj_ecif`

**B.3.1 空结果集的真实形态（关键）**

```
MySQL [lzbj_ecif]> /*proxy*/show table without shardkey;
Query OK, 0 rows affected (0.001 sec)
```

不是 `Empty set`，是 **`Query OK`** —— OK 包，无列元数据。**这解释了赤兔转圈**，
也确定了本模块无需改设计（详见 §3.3 RISK-F）。

**B.3.2 `lzbj_ecif` 三类计数**

| 命令 | 行数 | 耗时 |
|---|---|---|
| `/*proxy*/show table with shardkey` | **98** | 0.001 sec |
| `/*proxy*/show table with noshardkey_allset` | **117** | 0.002 sec |
| `/*proxy*/show table without shardkey` | **0**（OK 包） | 0.001 sec |
| **合计** | **215** | — |

→ 本模块对该库应输出：**总表 215 / 单表 0 / 广播表 117 / 分片表 98**。
这是开发完成后 UAT 的**第一个对数基准**。

**B.3.3 `info` 列的取值谱系（本期不使用，为将来预留）**

| 形态 | 样例 |
|---|---|
| 广播表 | `shardkey:noshardkey_allset` |
| 广播表 + 自增 | `shardkey:noshardkey_allset;auto_increment:ID` / `;auto_increment:NID` |
| 一级 hash 分片 | `shardkey:cust_no` / `shardkey:emp_no` / `shardkey:log_id` |
| 二级分区（hash 子键） | `shardkey:SHARDKEY_HASH_USE_SUB;sub_shardkey:CUST_NO;sub_func:CUST_NO` |
| 二级分区（按月） | `shardkey:id;sub_shardkey:CREATE_DATE;sub_func:month` |
| 分片 + 自增 | `shardkey:SHARDKEY_HASH_USE_SUB;auto_increment:ID;sub_shardkey:CUST_NO;sub_func:CUST_NO` |

注意 `sub_shardkey` 的列名**大小写不统一**（`CUST_NO` 与 `cust_no` 并存），
将来若要解析 `info` 需按不区分大小写处理。**本期只取 `db_table` 列，不受影响。**

**B.3.4 连接可用性**

OK 包之后同一 session 继续执行 `with noshardkey_allset`（117 行）与
`with shardkey`（98 行）均正常 —— **OK 包不会污染连接状态**。

### B.4 第三轮实测（2026-08-29，T14 交叉校验）

```sql
MySQL [lzbj_ecif]> SELECT COUNT(*) FROM information_schema.TABLES
                   WHERE TABLE_SCHEMA = 'lzbj_ecif' AND TABLE_TYPE = 'BASE TABLE';
+----------+
| COUNT(*) |
+----------+
|      293 |
+----------+
1 row in set (0.004 sec)
```

| 口径 | `lzbj_ecif` |
|---|---|
| Proxy：分片 | 98 |
| Proxy：广播 | 117 |
| Proxy：单表 | 0 |
| **Proxy 口径合计** | **215** |
| `information_schema` `BASE TABLE` | **293** |
| **差** | **78（占基线 27%）** |

**待查**：78 张是什么。最有力假说是二级分区的物理子表——
`info` 含 `sub_func:month` 的表恰好 6 张，且 78 = 6 × 13。由 D1～D3 判定（§10.2）。

> **注**：本节写于第三轮，当时 78 张的成因未明，曾把 UAT 基准定为
> "五个数字 + 一条 `RECON_MISMATCH`"。第四轮（B.5）查明成因并按 ADR-17 剔除后，
> **该基准已作废**——最终基准见 B.5 末尾（六个数字，且**不得**出现 `RECON_MISMATCH`）。

### B.5 第四轮实测（2026-08-29，D3 差异成因）

```
MySQL [lzbj_ecif]> SELECT TABLE_NAME FROM information_schema.TABLES
                   WHERE TABLE_SCHEMA='lzbj_ecif' AND TABLE_TYPE='BASE TABLE'
                     AND (TABLE_NAME LIKE 'cus_pub_translog%'
                       OR TABLE_NAME LIKE 'cus_pub_updatelog%'
                       OR TABLE_NAME LIKE 'cus_pub_sync_log%'
                       OR TABLE_NAME LIKE 'cus_bas_merge_log%')
                   ORDER BY TABLE_NAME;
+-------------------------------------------+
| cus_bas_merge_log                         |   ← 逻辑表
| cus_bas_merge_log_tdsql_subp190001        |   ← 兜底/溢出分区
| cus_bas_merge_log_tdsql_subp202601        |
| …                                         |
| cus_bas_merge_log_tdsql_subp202612        |   ← 每张 13 个
| cus_pub_translog_his                      |   ← 无二级分区（被 LIKE 顺带捞到）
+-------------------------------------------+
71 rows in set (0.004 sec)
```

**二级分区物理子表命名约定（实测锚点）**：`<逻辑表名>_tdsql_subp<数字>`
* 兜底/溢出分区：`_tdsql_subp190001`
* 按月分区：`_tdsql_subp202601` … `_tdsql_subp202612`

**账目闭合验算**：

| 项 | 数 |
|---|---|
| D3 结果中有子表的逻辑表 | 5 |
| 每张的子表数（190001 + 12 个月） | 13 |
| 小计 | 5 × 13 = 65 |
| D3 结果中的逻辑表名（含无子表的 `cus_pub_translog_his`） | 6 |
| **D3 合计** | **65 + 6 = 71** ✓ 与 `71 rows` 吻合 |
| 全库 `sub_func:month` 的表（附录 B.3.3） | **6** 张（第 6 张 `cus_pub_sync_consumer_log` 未被本次 LIKE 匹配） |
| **全库子表合计** | **6 × 13 = 78** ✓ 正是 293 − 215 |
| **剔除后逻辑基线** | **293 − 78 = 215 == Proxy 口径** ✓ |

**注**：`SHARDKEY_HASH_USE_SUB`（hash 二级分区）的表**不产生**物理子表——
若产生，差值会远大于 78。只有 `sub_func:month`（按时间二级分区）会展开成物理子表。

**UAT 对数基准（最终版，六个数字）**：本模块对 `lzbj_ecif` 应输出
**总表 215 / 单表 0 / 广播表 117 / 分片表 98 / 逻辑基线 215 / 二级分区子表 78**，
告警**只有一条** `SUBPARTITION_EXCLUDED`（INFO），**不得出现** `RECON_MISMATCH`。
该基准已编码为单测 `test_lzbj_ecif_uat_baseline`。

### B.6 由四轮实测直接得出的结论

| 编号 | 结论 | 落到代码 |
|---|---|---|
| B-1 | 列名为 `db_table` | `_EXACT_NAME_COLS` 首位 |
| B-2 | `without shardkey` 只有 1 列，另两条有 2 列 | 选列规则 1 与 2 都要能命中；`info` 入排除词 |
| B-3 | 值为库限定名 | `_split_qualified` + 按库归属（ADR-11） |
| B-4 | 三类互斥（7 / 4 无交集，注意 `kcda_vchr_cmprs_old` ≠ `kcda_vchr_cmprs`） | RISK-A 证伪；归一化作为保险保留 |
| B-5 | `information_schema` 经 Proxy 可查且含系统库 | 业务库白名单过滤必需 |
| B-6 | 赤兔对无单表的库执行 `without shardkey` 会转圈 | RISK-F；30s/180s 双层兜底；T15 判决 |
| B-7 | 三条命令 0.01 秒级返回（`sqltuning` 规模） | 性能基准，T10 在更大库上复核 |
| B-8 | 某类为空时返回 **OK 包**而非空结果集 | RISK-F 裁决；PyMySQL 按 `[]` 处理；交叉校验是该静默失效模式的唯一探测器 |
| B-9 | `lzbj_ecif` = 98 分片 + 117 广播 + 0 单表 = 215 | UAT 对数基准（B.3.2） |
| B-10 | 215 张表的库，三条命令合计 0.004 秒 | 单库开销可忽略；总耗时只取决于库数（见 T13） |
| B-11 | `lzbj_ecif` Proxy 口径 215 vs 基线 293，差 78（27%） | **RISK-B 成立**；ADR-16 双口径并排；ADR-15 告警汇总；**ADR-12 作用域判据改指纹比对** |
| B-12 | 78 张全部是二级分区物理子表，命名 `<逻辑表>_tdsql_subp<数字>` | **ADR-17**：从基线剔除并单列计数；剔除后 215 == 215，`RECON_MISMATCH` 重获信号价值 |
| B-13 | 仅 `sub_func:month` 产生物理子表，`SHARDKEY_HASH_USE_SUB` 不产生 | 正则只需覆盖 `_tdsql_subp<数字>` 一种形态 |

**待回填（T13 / T14 完成后补入本附录）**：

| 项 | 内容 | 来源 | 阻断 |
|---|---|---|---|
| 命令作用域 | 当前库 / 实例级 | T13 | 否 |
| ~~那 78 张表是什么~~ | 已完成：二级分区物理子表，`_tdsql_subp<数字>` | D3 | — |
| ~~基线交叉校验~~ | 已完成：293 vs 215，差 78（已解释并消除） | T14 | — |
| ~~空结果集行为~~ | 已完成：OK 包，0.001s，不挂起 | T15 | — |

---


## 修订记录

| 版本 | 日期 | 作者 | 内容 |
|---|---|---|---|
| Rev.F | 2026-08-31 | 智能体 A | **v1.6.2.2 上线后的代码变更复核修订。**对本设计依赖的 13 个文件做 `git diff 8fee172..01e2914` 逐一比对，只有 `migrator.py`（失败关闭改造）与 `app.js`（2 行，无关）动过；其余全部未变，§2 的行号与结论逐条重新核对后继续成立。**§2.7 重写**，写入迁移器的三条新硬约束：M-1 任一语句失败即启动关闭（旧版只 WARNING）、M-2 列级严格验收只作用于 `ADD COLUMN`（本模块纯 `CREATE TABLE`，不受影响、无适配成本）、**M-3 checksum 漂移即启动失败关闭——迁移文件发布即冻结**。据此新增 **ADR-18**（表结构须在打包前定稿；发布后扩列走新增 `111_*.sql` 而非回头编辑）、**KL-12**，并把 §9 中迁移文件的风险由"零"上调为"低"、补两条迁移专项验收。附录 A 代码在当前 main 上重跑 **42 项全过**（沙箱 MariaDB 的 `int(11)` 差异非 G14 问题，加兼容垫片后全绿）。设计主体（口径、算法、接口、爆炸半径）**无实质变更**。 |
| Rev.E | 2026-08-29 | 智能体 A | 依第四轮内网实测（D3）**查明并消除 RISK-B 的差异**：那 78 张全部是 TDSQL 二级分区的物理子表，命名 `<逻辑表>_tdsql_subp190001` / `_tdsql_subp202601`…`202612`；6 张 `sub_func:month` 的表 × 13 个子分区 = 78，与 293 − 215 精确闭合。**ADR-17（新增）**：`_tdsql_subp<数字>` 结尾的表从基线剔除、单列 `subpartition_tables`（响应 + 两张表的 DDL 列 + 前端一列），并出一条 `SUBPARTITION_EXCLUDED`（INFO）说明剔除量。剔除后**逻辑基线 215 == Proxy 口径 215**，`RECON_MISMATCH` 从"在每个有二级分区的库上永久亮着的噪声"变回"亮起就意味着真有表没进 Proxy 路由表"的信号——推翻 Rev.D"两个数并排让用户自己判断"的做法（ADR-16 随之修订）。新增护栏用例 3 项，其中 `test_lzbj_ecif_uat_baseline` 把内网对数基准（98/117/0/215/215/78 且不报 RECON）直接编码为单测（共 42 项，全部通过）。§10 归档 D3，仅剩 T13；附录 B 增补第四轮原始数据与账目闭合验算。**GATE-2 仍无阻断项。** |
| Rev.D | 2026-08-29 | 智能体 A | 依第三轮内网实测（T14）**裁决 RISK-B：确认成立且差异达 27%**——`lzbj_ecif` Proxy 口径 215（98 分片 + 117 广播 + 0 单表）vs `information_schema` 基线 293，差 **78 张**。三处修订：**ADR-16**（四个数字采用 Proxy 口径，基线数并排呈现而不覆盖，新增 `baseline_tables` 字段与 DDL 列）；**ADR-15**（`RECON_MISMATCH` 汇总成一条——差异每库都有，逐库告警在 50 库实例上就是 50 条横幅）；**ADR-12 改写**——Rev.B 用"某库累计表集 == information_schema 基线"作为作用域探测的完备性证明，本次实测证明两者基本不可能相等、该判据永远不成立，改为**不依赖基线的指纹比对**（连续两个非空库的原始结果集逐条相同即证明实例级作用域，固定代价 6 条命令）。新增护栏用例 2 项（共 39 项，全部通过）。§10 归档 T14 并新增 D1～D3 诊断查询（78 = 6 × 13 且 `sub_func:month` 的表恰好 6 张，二级分区物理子表为最有力假说，待验证）。附录 B 增补第三轮数据与修订后的 UAT 对数基准（五个数字）。**GATE-2 仍无阻断项。** |
| Rev.C | 2026-08-29 | 智能体 A | 依第二轮内网实测（T15）**裁决 RISK-F**：命令不挂起，`mysql` 直连 Proxy 返回 `Query OK, 0 rows affected (0.001 sec)`—— 是 **OK 包**不是空结果集，赤兔转圈系其前端等列元数据所致。核对 PyMySQL 行为（本机 2.2.8 + 项目下限 1.1.0 wheel 源码）：OK 包 `fetchall()` 返回 `[]`、`description` 为 `None`，本模块天然按该类 0 张处理，**设计不改**。新增语义记录：OK 包与命令不被支持在协议上不可区分，§6.6 交叉校验是该静默失效模式的唯一探测器。新增护栏用例 2 项（共 37 项，全部通过）。附录 B 增补第二轮原始数据：`lzbj_ecif` = 98 分片 + 117 广播 + 0 单表 = **215**（UAT 对数基准）、`info` 列取值谱系、三条命令合计 0.004 秒。§10 归档 T15，剩余 T13 / T14 均标注**不阻断开发**；**GATE-2 无阻断项，可进入开发**。 |
| Rev.B | 2026-08-29 | 智能体 A | 依第一轮内网实测（附录 B）修订。**证伪 RISK-A**（三类互斥，归一化改作保险保留）；**锚定 RISK-C**（列名 `db_table`、`without shardkey` 单列、值为库限定名）；**新增 RISK-E**（命令作用域可能为实例级——Rev.A 会让总数放大 N 倍，改为按行内库限定名归属 + 全局 `(库,表)` 去重 + 覆盖性跳过，两种作用域下均正确）；**新增 RISK-F**（无单表的库上命令可能挂起——加 30s 读超时 + 180s 总预算 + `SKIPPED` 状态）。新增 ADR-11~14、E-18~23、W9~W11、`skipped_databases` 字段。§10 重写为"第一轮裁决表 + T13/T14/T15 三项补测 + GATE-2"。附录 A 代码同步更新（服务层 574 行 / 单测 594 行），**本地 35 项单测全部通过，仓库代码零改动**。 |
| Rev.A | 2026-08-29 | 智能体 A | 首版。需求拆解、现状勘查（含 `/*proxy*/` 存活性证据链）、三大语义风险（RISK-A/B/C/D）识别与对策、总体与详细设计、10 条 ADR、17 项异常矩阵、爆炸半径分析、12 个内网实测用例与 GATE-1 放行判据、附录 A 全套成品代码（服务层 489 行 / API 44 行 / 迁移 34 行 / 单测 455 行 / 既有文件 9 行改动 + 1 个前端块）。**附录 A 代码已在本地以 importlib 挂载方式跑通 32 项单测（含真实 MariaDB 落库），仓库代码零改动。** |
