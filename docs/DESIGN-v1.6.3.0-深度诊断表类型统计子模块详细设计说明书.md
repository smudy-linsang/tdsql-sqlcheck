# DESIGN-v1.6.3.0 深度诊断·表类型统计（G14）详细设计说明书

| 项 | 内容 |
|---|---|
| 文档编号 | DESIGN-v1.6.3.0 **Rev.N** |
| 模块编号 | **G14 · 表类型统计**（深度诊断第 10 个子模块） |
| 目标版本 | v1.6.3.0（当前基线 v1.6.2.2，`VERSION` / `backend/config.py:APP_VERSION`） |
| 文档等级 | **照图施工级**——附录 A 给出全部新增/修改文件的逐行成品代码；Rev.N collect **112 项**（离线 89 / 元数据库 22 / 落盘后仓库检查 1）。Rev.M 已由 Q 照图施工落盘（提交 `0c0b3b4`），第一轮 SIT（提交 `35e05ad`）发现 1 BLOCK + 2 MINOR，Rev.N 为 **SIT 整改定版** |
| 编写 | 智能体 A（Rev.A～K、第五轮评审、第一轮 SIT）；智能体 O / Codex（Rev.L～M 修订）；智能体 Q（Rev.N：依第一轮 SIT 报告整改落盘） |
| 编写日期 | 2026-08-29 首版；2026-08-31 Rev.F～J 多轮复核与整改；2026-09-01 Rev.K 依 O 第三轮评审整改；2026-09-01 Rev.L 依第四轮评审整改；2026-09-01 Rev.M 依 A 第五轮评审定点整改（1 P2）；2026-09-01 **Rev.N 依 A 第一轮 SIT 整改（1 BLOCK + 2 MINOR）** |
| 状态 | **Rev.N 已完成第一轮 SIT 三项缺陷整改并落盘**：DEF-SIT-01（BLOCK）补登记 `_OPERATIONAL_WRITE_PREFIXES`；DEF-SIT-02（MINOR）修正 §5/§8 契约文字（422 语义）；DEF-SIT-03（MINOR）API 层入参校验提前至连接解析之前。§2.2 登记点由 6 处改为 **7 处**（新增 P7），ADR-21 / §12.2 / KL-17 同步，附录 A.2 与测试同步更新。 |
| 前置约束 | 本文档编写阶段**未修改任何代码**（用户要求）。仓库工作区在本文档提交时保持干净。 |

---

## 0. 阅读指引与本文档的三条硬约束

本文档同时承担三件事，读者请按角色取用：

* **实施者（智能体 Q 或人工）**：读 §5～§9 + 附录 A。附录 A 是**可直接落盘的成品代码**——四个新增文件 + 既有文件的 **10 行**改动 + **2 个前端块**（页签块 + 历史抽屉块），逐字给出。
* **内网测试配合者**：只读 **§10**。既有六轮用例的裁决见 §10.1 与 §10.2；另执行 T20，记录普通 `IN` 与 `BINARY IN` 的 `EXPLAIN` 扫描库数及耗时。T13 不影响数字，T20 不阻断开发、但属于发布前性能证据门禁。
* **单点复核者（智能体 A，Rev.M）**：核对 §6.4、ADR-27、E-36、T4-R01 与 T20 是否逐项关闭第五轮 P2-01；无需重开第四轮已关闭项。

**三条硬约束**（贯穿全文，任何实现偏离即为不合格）：

1. **只读**。本模块对目标 TDSQL 实例只执行 `SHOW` / `SELECT information_schema`，不产生任何 DDL/DML，不修改任何会话级持久设置。
2. **零回归**。不得修改 119 条审核规则、解析引擎、既有 9 个深度诊断子模块的任何一行。修改面见 §9 清单，共计**新增 4 个文件 + 修改 5 个文件的 10 行 + 2 个前端块**。零回归不只是「不改既有代码」，还包括**不挤占既有资源**——本模块与既有扫描共用 `registry.scan_slot` 的同一份并发配额（ADR-19）。
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

### 2.2 一个子模块需要登记的 7 个点（缺一即失效）

> **Rev.G 从 4 个点改为 6 个点**：Rev.F 只数了后端的 4 处 + 一个页签块，
> 漏掉了 `app.js` 的**子页签回退清单**（O 评审 P1-06）。见 P6 与 ADR-21。
> **Rev.N 从 6 个点改为 7 个点**：第一轮 SIT 发现漏掉了
> `_OPERATIONAL_WRITE_PREFIXES`（DEF-SIT-01）。见 P7 与 ADR-21。

| # | 文件:行 | 内容 | 缺失后果 |
|---|---|---|---|
| P1 | `backend/services/auth_service.py:371-379` | API 前缀 → 菜单键映射 `_PATH_TO_MENU` | 写端点"未映射默认放行"（fail-open），且 `tests/test_rbac_path_coverage.py` **直接失败** |
| P2 | `backend/services/auth_service.py:491-494` | `ALL_MENU_KEYS` | 权限矩阵页看不到该菜单，无法配置 |
| P3 | `backend/services/auth_service.py:504-509` | `MENU_LABELS` | 权限矩阵页显示裸键名 |
| P4 | `backend/services/database.py:1717` | `_init_default_data` 的 `all_menus` | **致命**：`database.py:1775` 有 `DELETE FROM role_permissions WHERE menu_key NOT IN (...)`，未登记的键会在每次启动时被删掉，菜单永久不可见 |
| P5 | `frontend/index.html`（页签块的 `v-if="visibleMenus.has(...)"`） | 页签本身的可见性 | 页签对所有人可见 / 或对所有人不可见 |
| **P6（Rev.G 新增）** | `frontend/static/js/app.js:1960` 的 `subtabs` | 进入"深度诊断"时**按角色选默认活动页签**的回退清单 | 只拥有该子菜单权限的自定义角色进入页面后 `deepTab` 停在不可见的 `'cluster'`，**页面没有活动页签**。admin 账号下永远测不出来（它拥有全部子页签，循环第一项必定命中） |
| **P7（Rev.N 新增）** | `backend/services/auth_service.py:278-298` 的 `_OPERATIONAL_WRITE_PREFIXES` | 业务操作性**写端点**放行清单（`_DEVELOPER_WRITE_PREFIXES` 即其别名） | **致命**：`developer` 与全部自定义角色在 `check_permission` **第一级**即被拒，写端点恒 403；而菜单可见性正常、页签照常显示，现场极易误判为权限配置问题。admin/dba 因短路放行而测不出来 |

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
* **现有最高版本目录：`v12/120_gateway_report_tickets.sql`。→ 本模块用 `v13/130_table_type_stats.sql`。**

> ### ⚠️ DEF-1（Rev.I 更正）：`v11/110` 槽位已被占用，且这是我自己漏掉的
>
> Rev.A（`5e9f438`，2026-08-29 02:23 UTC）编写时，schema 目录的最高版本**确实**是
> `v10/100_zk_scan_enrich.sql`，当时写 `v11/110` 没有错。
> 但 v1.6.2.2 的 UAT 第四、五轮随后各加了一个：
>
> | 提交 | 新增文件 | 来源 |
> |---|---|---|
> | `50a1c04` | `backend/schema/v11/110_index_finding_structured.sql` | O-18 索引体检结构化字段 |
> | `8fee172` | `backend/schema/v12/120_gateway_report_tickets.sql` | O-22 网关票据共享 |
>
> **Rev.F 那一版的任务就是"依 v1.6.2.2 上线后的代码变更复核"，却没抓到这条。**
> 原因是我把复核范围定义成了"本设计**引用到的** 13 个文件有没有变"，
> 逐个 `git diff` 过去——而**一个新文件要落进去的槽位，本来就不在"我引用的文件"里**，
> 那种 diff 无论多仔细都不可能发现它。这是复核**方法**的缺口，不是执行不仔细。
> 教训登记为 **KL-17**。
>
> **实际后果（评估后确认不是灾难，但必须改）**：迁移键是
> `f"v{version}_{sequence:03d}_{name}"`（`migrator.py:354`），所以
> 原方案的 `v11_110_table_type_stats` 与既有的 `v11_110_index_finding_structured`
> **并不撞主键**，两个文件都能被加载和登记，不会启动失败。
> 但同一个 `v11` 目录里出现两个 `110_` 前缀，二者的执行先后**只由
> `sorted(vdir.iterdir())` 的文件名字典序决定**（`loader.py`）——
> 这是一个没人打算建立的隐式依赖，也直接违反本项目"vN 目录 ↔ NNN 序号"的约定。
>
> **Rev.I 的处置**：槽位改为 **`v13/130_table_type_stats.sql`**（选槽当时的最大之后一位）。
> **Rev.K 又将护栏收敛为永久不变量**：全库不得有同槽多文件；本模块独占 v13/130；
> 已知前驱 v12/120 仍在。不再要求本模块永远是最大槽，否则后续合法迁移会把历史测试打红。
> **把"槽位不冲突"从一件需要人去记的事，变成一条会失败的测试。**
* **不动 `database.py::_create_all_tables`**：`init_db()` 在 `_create_all_tables` 之后调
  `migrator.run_migrations()`（`database.py:411`），全新安装与存量升级都覆盖到。

**v1.6.2.2（O-23 / O-26 / O-29 / O-30）把迁移器从"宽容"改成了"失败关闭"，
新增三条硬约束，本模块必须逐条满足：**

| # | 新行为 | 位置 | 对 G14 的影响 |
|---|---|---|---|
| M-1 | **任一语句执行失败即 `MigrationError` → 启动中止**（旧版只记 WARNING 继续） | `migrator.py:191` | 本模块的 DDL **必须保证在 MySQL 8 / TDSQL 上一次执行成功**。用 `CREATE TABLE IF NOT EXISTS` 保证幂等，重复启动不会二次失败 |
| M-2 | **结构严格验收只作用于 `ALTER TABLE … ADD COLUMN`**（`_ADD_COLUMN_RE`，`migrator.py:45-48`） | `_apply_file` / `_structure_state` | 本模块是**纯 `CREATE TABLE`**，不匹配该正则 → **不进入列级结构验收**；已登记后 `_structure_state` 返回 `valid` 直接跳过。**Rev.F 曾据此判定"无额外适配成本"——Rev.G 撤回这个判断**，理由见下方 M-2 补注与 ADR-20 |
| M-3 | **checksum 漂移 → 启动失败关闭**，除非精确命中代码内 `_KNOWN_RECONCILIATIONS` 三元组账本（`migrator.py:281-296`） | `_auto_reconcile` | **发布即冻结**——见下方警示 |

> ### ⚠️ M-3 是本次新增的最重要约束：迁移文件发布即冻结
>
> `v13/130_table_type_stats.sql` **一旦随版本发布并在任一实例上被应用，文件内容即被冻结**。
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
> 若发布后确需扩列，正确做法是**新增 `v13/131_*.sql` 用 `ALTER TABLE … ADD COLUMN`**，
> 而不是回头编辑 `130_*.sql`——注意 `ADD COLUMN` 会进入 M-2 的列级严格验收，
> 类型/可空性/默认值三项必须与既有列逐字相符。

> ### ⚠️ M-2 补注（Rev.G 新增）：**"不进入验收"不是安全性依据**
>
> Rev.F 把"纯 `CREATE TABLE` 不进入列级验收"当成一件好事（无适配成本）。
> O 在 P1-08 里指出这是一个方向性错误，我复核后**完全接受**：
> 迁移器不验收，只说明**没人替我们检查**，不说明表一定是对的。具体的失效路径有两条：
>
> 1. **建表被静默跳过**：元数据库里若已存在同名但缺列 / 错类型 / 缺索引的表
>    （手工试验残留、历史同名表、上一次部署中途失败），`CREATE TABLE IF NOT EXISTS`
>    直接跳过，**迁移仍被登记成功**，直到本模块 `INSERT` 才 1054 报错；
> 2. **登记后结构漂移**：迁移一旦登记，`_structure_state()` 因为没有 `ADD COLUMN`
>    声明恒返回 `valid`，即便两张表被人工删除也不会重放。
>
> Rev.F 新增的"连续启动两次"验收只能证明**跳过**，不能证明**表结构正确**——
> 这两件事被我混为一谈了。
>
> **Rev.G 的处置**：由模块自己在 `run_stats` 入口做一次确定性结构验收
> （表 / 全部列 / 关键列类型 / 索引，任一不符即抛 `SchemaNotReadyError`），
> 见 ADR-20 与附录 A.1 的 `_ensure_schema()`。
> **不改迁移器**——那是 v1.6.2.2 刚上线的全平台启动路径，为一个诊断子模块
> 动它不成比例；**也不做启动期失败关闭**——理由同样在 ADR-20。


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
> **Rev.G 依 O 评审报告收紧两处**：RISK-B 的子表识别从「只看后缀」改为
> 「后缀 + 逻辑父表已确认」且集中式一律不剔除（P1-03）；RISK-E 的
> 「指纹相同即提前停止」优化**整体删除**（P1-01）。
> **当前仅剩 RISK-E（T13，命令作用域）未裁决**——取消提前停止后它已
> **不影响正确性**，只影响耗时预期。

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

**设计对策（Rev.E 提出，Rev.G 收紧，ADR-17）：把二级分区物理子表从基线中
剔除并单列计数——但「叫这个名字」只是必要条件，不是充分条件。**

```python
_SUBPARTITION_RE = re.compile(r"^(?P<parent>.+?)_tdsql_subp\d+$", re.IGNORECASE)
```

判定为物理子表需**同时**满足两条（Rev.G / P1-03）：

1. 表名匹配 `<父表>_tdsql_subp<纯数字>`；
2. **逻辑父表确实出现在本库的 Proxy 结果里**。

且**集中式实例一律不做这个剔除**。

> **为什么 Rev.F 的「只看后缀」不够**（O 评审 P1-03，本人复核后认可）：
> 内网证据只证明了「这 78 张物理子表叫这个名字」，**没有**证明
> 「叫这个名字的一定是物理子表」。两者是不同的命题。
> 集中式实例根本没有二级分区这个构造，一张合法业务表若恰好叫
> `orders_tdsql_subp202601`，Rev.F 会把它**静默少算**，而集中式分支
> 没有 Proxy 交叉校验兜底——这个错误**不可见**，直接违反 REQ-5。
> 加上父表确认后，误判方向仍然是安全的：父表没确认就保留为逻辑表，
> 后果是 `RECON_MISMATCH` **显式报出来（可见）**。

剔除后：**逻辑基线 215 == Proxy 口径 215，两个口径精确相等**
（实测的 6 张父表全部出现在 `show table with shardkey` 的 98 行内，
父表确认条件在真实数据上成立，加这一条不会破坏已验证的对数结果）。

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
3. **无条件逐库执行**（Rev.G / P1-01 修订，原为"指纹相同即提前停止"）：
   对每一个目标业务库都执行三条命令，不做任何提前停止。
4. 检测到跨库行时输出 `INSTANCE_WIDE_SCOPE`（INFO），说明本版本返回实例级全量、
   已按库限定名归属并去重，**且为保证覆盖完整性仍逐库执行**。

> **Rev.F 的「指纹相同即提前停止」为什么被整体删除**（O 评审 P1-01，本人复核后认可）：
>
> Rev.D/F 的论证是："当前库作用域下两库的库限定名前缀必然不同，
> 非空集合不可能相等；故集合相等 ⟹ 实例级作用域。" 这一步推理本身没错。
> 错在**下一步**：从"命令是实例级作用域"推不出"这一次返回的集合覆盖了全部目标库"。
> 前者是命令的语义属性，后者是这一次调用的**结果完整性**，两者之间隔着
> 账号可见范围、路由域、租户切分、路由元数据缺失等一整排东西。
> 只要 db_a 与 db_b 在同一个路由域、db_c 在另一个域，两库指纹相同而 db_c 整个漏掉
> ——页面上四个主数字照样是"成功"，这正是本项目一贯最怕的**不可见的错误**。
>
> 我原本准备的兜底是 `RECON_MISMATCH`。复核后认为这个兜底站不住：
> 用告警替代正确的统计数字，等于承认主结果可以是错的。**性能优化不能拿正确性换。**
>
> O 给出的保留条件（返回的库限定名已覆盖全部目标库、且每库都完成一致性校验后
> 才允许停止）在逻辑上成立，但那时"省下的"只剩下命令的往返开销，
> 而校验本身仍要逐库查 `information_schema`——收益已经不足以支撑这份复杂度。
> **故整体删除，不保留任何形式的提前停止。** 代价见 §9 的耗时评估。

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
   每行先在 all_dbs 全实例命名空间解析一次 canonical 库名，
   再对 dbs 做精确成员过滤；SQL 端不承担大小写正确性，
   禁止在目标子集上二次大小写回退（Rev.M / P2-01）
        ──► baseline{db: {"base": {表名…}, "view": {视图名…}}}
4. 建临时池 tmp（pool_size=1, read_timeout=30s）
5. kind_map = {}        # (库, 表) -> 类型，全局去重，正是原厂「库名+表名去重」
   for db in dbs:                       # ← Rev.G：无条件逐库，不提前停止（P1-01）
     if 已过 deadline:           该库标 SKIPPED，继续    # 180s 是软预算：不再启动新操作
     staged = {}                        # ← Rev.G：本库暂存区（P1-05）
     try:
       with tmp.get_connection() as conn:      # ← Rev.G：每库一个连接上下文（P1-04）
         if 拿到连接后已过 deadline: 置 budget_hit，正常退出 with
         else: conn.select_db(db)
         for kind, sql in (() if budget_hit else (分片, 广播, 单表)):
             if 已过 deadline: 置 budget_hit 并 break   # Rev.K：正常退出 with，不触发重建
             rows = execute(sql)
             # rows 可能是 OK 包（Query OK, 0 rows affected）→ fetchall() 为 []
             staged[kind] = 按 known_dbs 拆库限定名，缺省归当前库
     except Exception:                  # 异常【穿出】with ⇒ 触发连接重建
       该库标 FAILED，丢弃 staged 全部内容，continue
     ── 三条命令全部成功，才原子合入全局（P1-05）──
     for kind, pairs in staged:
         for (qual, name) in pairs:
             if qual 不在目标库集合:  丢弃    # 系统库 / 被筛掉的库
             if name 在 baseline[qual]["view"]:  丢弃   # 原厂：不统计视图
             kind_map[(qual,name)] = 优先级更高者（分片 > 广播 > 单表）
6. tmp.close_all()
7. 逐库汇总 kind_map ──► items；Proxy 口径总数 = len(kind_map)
   ── 先定状态：eligible = 全部库 − 失败库 − 跳过库（Rev.J / P1-03）──
   ── 子表判定三条件：匹配 <父表>_tdsql_subp<数字>、父表在 Proxy 结果里、
      且【候选自身不在 Proxy 结果里】（Rev.J / P2-01）；其余保留为逻辑表 ──
   ── 实例级汇总（含 baseline / subp / overlap）只累加 eligible 库 ──
   ── 交叉校验：每库 got 与 baseline[db]["base"]（去子表后）做双向集合差 ──
   差集写入该库 item.detail；全部处理完后汇总成【一条】RECON_MISMATCH
8. 告警 + 落库 + 返回（total_tables 与 baseline_tables 并排）
```

> **步骤 5 的三处 Rev.G 变化都只为一件事：让"失败"是局部的、可见的、可恢复的。**
> 不提前停止 ⇒ 覆盖不会因为一次巧合的指纹相同而残缺；每库一个连接上下文 ⇒
> 一条坏连接不会把后面所有库连坐；暂存后原子合入 ⇒ 失败库不会把半截结果
> 混进别的库的账里。

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
| 新增 | `backend/services/table_type_stats_service.py` | 附录 A.1 给出完整成品代码（不以易漂移的行数作验收条件） |
| 新增 | `backend/api/table_type_stats.py` | 81 行（附录 A.2，成品；Rev.N 因 DEF-SIT-03 由 71 行增至 81 行） |
| 新增 | `backend/schema/v13/130_table_type_stats.sql` | 46 行（附录 A.3，成品）。**槽位 v13/130**——`v11/110` 与 `v12/120` 已被 v1.6.2.2 占用（DEF-1，§2.7） |
| 新增 | `tests/test_table_type_stats.py` | 附录 A.4 给出完整成品代码；Rev.N collect 112 项 |
| 修改 | `backend/main.py` | **2 行**（import + include_router） |
| 修改 | `backend/services/auth_service.py` | **4 行**（P1/P2/P3 + Rev.N 新增 P7：`_OPERATIONAL_WRITE_PREFIXES`） |
| 修改 | `backend/services/database.py` | **1 行**（P4） |
| 修改 | `frontend/index.html` | **1 处**新增 `<el-tab-pane>` 块（内含结果区 + 历史抽屉，不改任何既有行） |
| 修改 | `frontend/static/js/app.js` | **4 行**（`deepResult` 加字段 / 新方法 / 返回清单追加 / **`subtabs` 回退清单追加**）+ 1 个纯新增方法块 |

**合计：新增 4 文件，既有文件净改 11 行 + 2 个纯新增前端块。**（Rev.N 相对 Rev.M 净增 1 行：`auth_service.py` 的 P7。）

> Rev.G 相对 Rev.F 多出来的 1 行，就是 O 在 P1-06 里点出的
> `app.js:1960` 的 `subtabs` 回退清单。**这 1 行不加，一个只被授予
> 「深度诊断 + 表类型统计」的自定义角色进入页面后会没有活动页签**——
> 权限矩阵配置成功、功能却进不去，属于典型的"配了等于没配"。

---

## 5. 接口设计

### 5.1 `POST /api/v1/table-type-stats/run`

请求：
```json
{ "connection_id": "conn-xxx", "database": "" }
```
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `connection_id` | string | **是**（Rev.G / P2-03，`min_length=1`） | 目标实例。**不接受空串** |
| `database` | string | 否 | 空=全部业务库；非空=只统计该库（**仍会做 `_SYS_DB` 校验 + 存在性校验**） |

> **为什么 Rev.G 把 `connection_id` 从"可空"改成必填**（O 评审 P2-03，本人复核后认可）：
> 空串下 `registry.get("")` 取的是 adhoc 或默认保存连接，而
> `instance_type_service.resolve("")` 走的是**不带连接 ID 的全局默认类型逻辑**。
> 两者在实现上是两条独立的解析路径，**不保证指向同一个实例**。
> 最坏情况：池是真分布式实例、类型解析回了集中式，于是走 `information_schema` 分支，
> 分片/广播**全部报 0**，页面却显示"成功"。这是一个安静的、方向固定的错误
> ——正是本文档 §0 硬约束 3 要杜绝的那一类。
> 前端本来就持有当前连接 ID，改必填对使用者零影响。

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
| 422 | `string_too_short` / `Field required`（FastAPI 请求体校验） | `connection_id` 缺失或为空串——由 `StatsRequest` 的 `min_length=1` 在进入路由前拦截（Rev.N / DEF-SIT-02：422 是 FastAPI 请求体校验失败的标准语义） |
| 400 | `必须指定 connection_id（…）` | `connection_id` 为**全空白**字符串（通过了 `min_length` 但 `.strip()` 为空），由 **API 层**在连接解析之前拦截（Rev.N / DEF-SIT-03）；服务层同名守卫保留，作为服务被直接调用时的兜底 |
| 400 | `数据库不存在或当前账号不可见: xxx（SHOW DATABASES 未返回该库）` | 指定库不在 `SHOW DATABASES` 结果里（Rev.G / P2-01） |
| **429** | `目标库 xxx 扫描并发已达上限(N)，请稍后重试` / `服务扫描并发已达上限(N)，请稍后重试` | `ScanBusyError`——与既有慢查询扫描共用同一份配额（Rev.G / P1-02，口径同 `tdsql_manage.py:432`） |
| 500 | `元数据库缺少表 …` / `… 缺少列 …` / `… 列类型不符 …` / `… 缺少索引 …` | `SchemaNotReadyError`——留档表结构验收未通过（Rev.G / P1-08）。消息里带可执行的处置步骤，**原样透出，不被兜底 except 吞掉** |
| **503** | `采集总时长预算 180s 在…前即已耗尽` | `TimeoutError`——deadline 在探测/枚举库/基线查询之前就已耗尽。**Rev.K 起用 503 而非 500**：这是"稍后重试或缩小 `database` 范围就可能成功"的暂时性状况，与 500 的"结构不对、重试也没用"语义不同 |
| 500 | 原始异常字符串 | 其余（照抄样板） |

**并发与耗时边界（Rev.L / 第四轮 P1-02）**：

| 阶段 | 预算控制 | 可承诺边界 |
|---|---|---|
| 实例类型探测 → `SHOW DATABASES` → `information_schema` 基线 → 逐库三条 Proxy 命令 | 180 秒 monotonic **软 deadline**；阶段/库/命令开始前检查 | checkpoint 到期后服务层不再启动新阶段/库/命令；已进入的池/驱动操作可继续；**无硬墙钟上界** |
| `_ensure_schema()`（拿槽位之前，4 条 information_schema 查询，本地元数据库） | 不纳入目标软预算 | 无硬墙钟上界（KL-20） |
| 结果落库（释放槽位之后，1 条任务 INSERT + 明细批量 INSERT + COMMIT） | 不纳入目标软预算 | 无硬墙钟上界；已批量化，500 库 5 次往返 |

deadline 自拿到并发槽位后建立，并在实例类型探测之后、`SHOW DATABASES` 之前、
基线查询之前、每个库进入连接上下文之前、**拿到连接后且 `select_db`
之前**、每条 Proxy 命令之前检查。已进入连接池的一次获取动作可在内部继续
`ping → 建连`；这是已启动操作的内部步骤，不是服务层在过期后新开的库/命令。

> **Rev.L 删除 Rev.K 的 215 秒硬上界。** 原公式把 PyMySQL 的 `read_timeout=30`
> 当成整条命令的总耗时上限，但它实际上是连接读取等待超时；一个结果集可以经历多次读取。
> 现有连接池还会在复用时 `ping()`，并在真实异常后立即重建连接。这些路径不能由
> `180 + 5 + 30` 覆盖。故本设计只保留可兑现的承诺：**服务层在下一 checkpoint
> 不再启动新阶段、新库或新命令**；已进入的驱动/连接池操作不强制中断。

**并发语义（Rev.G / P1-02）**：`/run` 在 `run_stats()` 内进入
`registry.scan_slot(connection_id)`（与 `scan_service.py:72` 同一个入口），
受 `SQLCHECK_MAX_CONCURRENT_SCANS_PER_CONNECTION`（默认 2）和
`SQLCHECK_MAX_CONCURRENT_SCANS_GLOBAL`（默认 8）双重限制。
**不新建一份配额**——新建就等于全局上限失效，见 ADR-19。
前端在请求期间禁用按钮只是体验优化，**不能替代服务端限流**。

### 5.2 `GET /api/v1/table-type-stats/history?connection_id=&limit=20`

返回 `{"items":[{...table_type_stat 行...}]}`，按 `id DESC`。
`limit` 服务端夹取到 `[1, 200]`。行内含 `created_by`（操作人）与 `created_at`。

**`created_by` 必须是真实登录用户名（Rev.G / P2-02）**：API 声明
`http_request: Request`，取 `request.state.username`（未认证兜底 `anonymous`），
传给 `run_stats(operator=...)`。Rev.F 的接口签名没有 `Request`，
`created_by` 在真实调用中**恒为空串**——留档存在但不可追责，等于 REQ-6 只做了一半。

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
COMMAND_READ_TIMEOUT = 30    # 临时池每次连接读取的无数据等待上限，不是整条 SQL 墙钟上限
TOTAL_BUDGET_SECONDS = 180   # 软预算：checkpoint 后不再启动新阶段/库/命令；未采集库标 SKIPPED
```

> **Rev.L 时间语义**：PyMySQL `read_timeout` 作用于连接读取等待；一条结果集可包含多次读取，
> 因而 30 秒不能推导成整条命令最多 30 秒。`TOTAL_BUDGET_SECONDS` 只承诺 checkpoint
> 到期后不再主动开始下一阶段、下一库或下一条命令，**不承诺 `/run` 或目标采集阶段的硬墙钟秒数**。

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

#### `_collect_baseline(pool, dbs, known)` —— 两个分支共用

```sql
SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE FROM information_schema.TABLES
WHERE TABLE_SCHEMA IN (%s, %s, ...)
```
返回 `{db: {"base": 全部 BASE TABLE, "view": 视图}}`。

**Rev.M / 第五轮 P2-01：SQL 端不做大小写语义假设。** 参数化 `IN` 负责防注入并
保留 `information_schema` 的谓词下推能力；它既不被当成大小写精确保证，也不承担
最终正确性。服务端即使因排序规则、Proxy 行为或版本差异多返回 `sales.*`，代码端仍先用
**全实例** `_NameSpace known` 将返回值解析为 canonical 库名，再对目标子集做
**精确成员判断**。绝不在目标子集上再做 CI 回退；否则目标只有 `Sales` 时，真实兄弟库
`sales` 会被误并。该应用层防线严格覆盖 SQL 可能多返的情形，T4-R01 只钉行为、不再钉
SQL 字符串形态。

第五轮在 MariaDB 10.11.14、120 个业务库 × 10 张表环境实测：普通 `IN` 的
`EXPLAIN` 为 `Scanned 1 database`、中位 0.32 ms；`BINARY IN` 退化为
`Scanned all databases`、中位 2.04 ms，返回行数同为 10。故附录不得在
`TABLE_SCHEMA` 上施加 `BINARY`、`CAST` 或其他可能阻断下推的函数/操作符；目标 TDSQL
仍须按 T20 在最大实例留存同口径 `EXPLAIN` 与耗时证据。

**Rev.G：基线阶段不再拆出 `subp`（P1-03）。** Rev.F 在这里就按后缀把子表分了出来，
两个分支共用同一份结果——于是集中式实例也被剔了一遍，成了 P1-03 的静默少算。
Rev.G 把子表判定**下沉**到 `_classify_subpartitions(base, proxy_tables)`：

* **集中式**：传 `proxy_tables=空集`，等价于一律不剔除，全部 `BASE TABLE` 计入单表（REQ-5）；
* **分布式**：只有「名字匹配 `<父表>_tdsql_subp<数字>`」**且**「父表出现在本库
  Proxy 结果里」才判为物理子表。

**原始 `BASE TABLE` 数量保留在 `base` 里**，分类前后账目可核对：
`len(base) == len(logical_base) + len(subp)`，恒等式由单测钉住。

**取名字而不是取 COUNT** 的四个理由：
1. 集中式分支要的 `single = len(base)` 直接可得；
2. 分布式分支的交叉校验需要**双向差集的表名**，光有计数说不出"差在哪张表"；
3. "不统计视图"这条原厂要求，在分布式分支上靠 `view` 名单做扣除来落实；
4. 逐库「说明」列要写出**差的是哪几张表**，光有计数说不出。
   （注意：Rev.G 起已无作用域判定这一控制流——见 ADR-12。）

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
    for db in dbs:                       # Rev.G / P1-01：无条件逐库，无提前停止
        if 超预算: skipped[db] = "budget"; continue
        staged = {}                      # Rev.G / P1-05：本库暂存区
        try:
            with tmp.get_connection() as conn:   # Rev.G / P1-04：每库一个连接上下文
                conn.select_db(db)       # 隔离连接，切库无副作用（ADR-3）
                for kind, sql in _KIND_SQL:
                    cur.execute(sql) → 解析 → staged[kind] = pairs
        except Exception as e:           # 异常【穿出】with ⇒ 池关闭并重建连接
            failed[db] = 渲染(e); continue          # 整库丢弃 staged
        # 三条全部成功，才原子合入全局
        merge(staged → kind_map / kinds_seen / shape)
finally:
    tmp.close_all()
```

**三条 Rev.G 结构性变化（不得再改回去）**：

**① 每库一个连接上下文（P1-04）。** Rev.F 全程只进一次 `with tmp.get_connection()`，
并在 `with` **内部**把每条命令的异常全部吃掉，理由是"不让一个库的 1064 打断循环"。
这个理由只对**语法/权限类错误**成立——它们不破坏连接。但**读超时、服务端主动断开、
网络复位之后，当前这条连接已经不可用了**（超时后连接里还可能残留没读完的结果集），
异常被内部吞掉就意味着后续所有库继续复用这条坏连接，**从"一个库失败"变成
"从这里往后全部失败"**。Rev.G 让异常一律穿出当前库的 `with`，由
`TDSQLConnectionPool.get_connection()`（`tdsql_connector.py:287-307`）关闭并重建
线程本地连接后再抛出，外层逐库捕获、继续下一库。**临时池可以复用，坏连接不能复用。**

**② 单库三条命令原子合并（P1-05）。** Rev.F 每条命令成功就立刻写全局 `kind_map`。
在命令返回**跨库行**（RISK-E 的实例级作用域形态）时，一个失败库先前写入的行会
留在全局映射里，污染**其他库**的计数与重叠数——"失败库不计入任何汇总数"这个承诺
在数据结构层根本没有成立。Rev.G 先写本库暂存区，三条全成功才一次性合入；
任一条失败，本轮暂存**整体丢弃**。

**③ 不提前停止（P1-01）。** 见 §3.3 RISK-E。

**`select_db` 失败**（库被删、无权限）→ 与命令失败同一条路径：该库 `FAILED`，
异常穿出触发连接重建，不影响其余库。

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

**库名比对同样必须精确（Rev.J / P1-01 修正）。** Rev.I 及此前各版对库名用的是
`{name.lower(): name}` 单值字典，理由写的是"MySQL 库名在多数平台不区分大小写，
且库名来自我们自己枚举的清单，不存在合并风险"——**这个理由是错的**：

* 风险不来自外部输入，而来自**两个都真实存在的库**。Linux + `lower_case_table_names=0`
  的 MySQL/TDSQL 上，`Sales` 与 `sales` 是两个不同的 schema；
* 单值字典会让后者覆盖前者，于是两库的基线并进一个键、Proxy 行全归给字典里最后
  那一个、另一个库显示 0 或凭空产生 `RECON_MISMATCH`；
* 更糟的是**指定库存在性校验**也用小写去撞，会把"不存在"判成"存在"，
  一直到 `select_db()` 才失败；
* 全程**没有任何告警会亮**——这正是本文档 §0 硬约束 3 要杜绝的那一类错误。
  我在表名那一侧写对了道理，却没有把同一条道理用到库名上。

Rev.J 引入 `_NameSpace`（附录 A.1），把"一对多"如实表示出来：

| 情形 | 行为 |
|---|---|
| 精确命中 | 直接返回该 canonical 名 |
| 无精确命中，小写候选**唯一** | 返回该唯一候选（容忍 Proxy 回了不同大小写） |
| 无精确命中，小写候选**多个** | 返回 `None` —— **不猜**，该行不计入任何库，记 `DB_NAME_AMBIGUOUS`（ERROR） |
| 指定库参数 | **只接受精确名**；存在大小写变体时在报错里点名，便于使用者改对 |

`_collect_baseline`、`_split_qualified`、目标库过滤、owner 归属四处**共用同一个
`_NameSpace` 实例**，不存在两套规则打架的可能。实例上一旦存在大小写变体，
另出一条 `DB_NAME_CASE_VARIANTS`（WARNING）如实告知。

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

**这里的比对结果不参与任何控制流**（Rev.G 起已无作用域判定这一控制流），
它只负责如实呈现两个口径的差异。

**Rev.G 新增的一个信号**：一张名字匹配 `_tdsql_subp<数字>` 但**父表未确认**的表，
会留在逻辑基线里，于是在这里作为"仅基线可见"被点名报出（P1-03）。
这正是我们要的方向——**不确定的东西要显式报出来，而不是悄悄扣掉。**

集中式实例不做此校验（基线本身就是唯一数据源）。

### 6.7 告警清单（`warnings[]`）

| code | severity | 触发 | 用户该怎么办 |
|---|---|---|---|
| W1 `PROXY_CMD_FAILED` | ERROR | 某库三条命令中任一失败（含读超时） | **全实例汇总为一条**（Rev.G / P1-07），给出失败库数与前 5 个库名；**逐库 errno + 原因在表格「说明」列**。1064→连接可能不是 Proxy 端口；1045/1142→授权不足；读超时→保险触发，见 RISK-F |
| W2 `KIND_OVERLAP` | WARNING | 三类集合有交集（RISK-A 命中） | 说明"三类互斥"在本版本不成立，已按优先级去重，总数仍正确 |
| W3 `RECON_MISMATCH` | WARNING | 任一库的 Proxy 口径 ≠ **逻辑**基线（已剔除二级分区子表） | **全实例汇总为一条**。Rev.E 后这条不再常态触发——一旦亮起就意味着真有表没进 Proxy 路由表，值得查。逐库差集表名在表格「说明」列 |
| W4 `SHAPE_UNKNOWN` | WARNING | 结果列形态未识别（RISK-C 兜底） | 把 `shape` 字段贴给开发，扩充 `_EXACT_NAME_COLS` |
| W5 `INSTANCE_TYPE_UNRELIABLE` | WARNING | `ctx.source == DEFAULT` 或 `ctx.conflict` | 实例类型是猜的/有冲突，口径可能整体走错分支；去实例管理页锁定后重跑 |
| W6 `NO_BUSINESS_DB` | INFO | 过滤后无业务库 | 账号权限过窄或实例确实空 |
| W7 `TOO_MANY_DATABASES` | WARNING | 库数 > 500，已截断 | 用 `database` 参数分批统计 |
| W8 `NOT_DISTRIBUTED_ENDPOINT` | ERROR | 已执行的库全部因 1064 失败 | 该连接大概率指向后端 TXSQL 而非 Proxy（§2.4） |
| W9 `INSTANCE_WIDE_SCOPE` | INFO | 结果含跨库行（RISK-E 命中） | 本版本命令返回实例级全量，已按库限定名归属并按 `(库,表)` 去重；**为保证覆盖完整性仍逐库执行**（Rev.G / P1-01） |
| W10 `TIME_BUDGET_EXCEEDED` | WARNING | 超出 180s deadline | 未采完的库标 SKIPPED 未统计，请分批 |
| W13 `DB_NAME_CASE_VARIANTS` | WARNING | 实例上存在仅大小写不同的同名库（Rev.J / P1-01） | 它们是不同的 schema，本模块按精确名分别统计；知悉即可 |
| W14 `DB_NAME_AMBIGUOUS` | ERROR | Proxy 返回的库限定名无法唯一归属到某个真实库（Rev.J / P1-01） | 这些行**未计入任何库**（不猜归属）。请用「库名」输入框逐库精确统计 |
| ~~W11 `DB_ENUM_FAILED`~~ | — | **Rev.G 删除**（P2-01） | `SHOW DATABASES` 失败不再降级为"只统计指定库"，一律抛出。降级路径下 `known_dbs` 为空，库限定名无法拆分，统计结果**已经不可信**，却仍以"成功 + 一条 WARNING"呈现——这就是把错误藏在告警里 |
| W12 `SUBPARTITION_EXCLUDED` | INFO | 基线中存在 `_tdsql_subp<数字>` 结尾**且父表已在 Proxy 结果中确认**的物理子表 | 告知剔除了多少张、为什么剔除；逐库数量见「二级分区子表」列。这是正常现象，不是问题。**集中式实例永不触发**（Rev.G / P1-03） |

**告警条数上限（Rev.G / P1-07）**：`warnings[]` 中逐库级别的条目已全部汇总
——`PROXY_CMD_FAILED`、`RECON_MISMATCH`、`SUBPARTITION_EXCLUDED` 各最多一条。
即便 500 个库全部失败，`warnings[]` 也不超过 6 条、序列化后 < 8 KiB
（单测 `test_r09_five_hundred_failed_databases_is_bounded` 钉住）。
逐库详情落在 `table_type_stat_item.detail`（`VARCHAR(512)`，服务端截断）。
前端告警区**默认最多渲染 10 条**，超出折叠为「展开查看全部」，
避免几百个 `el-alert` 把页面卡住。

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
`warnings_json = json.dumps(warnings, ensure_ascii=False)`，列类型 **`MEDIUMTEXT`**（P1-07）。
**落库失败不得吞掉分析结果**：若 `INSERT` 抛异常，接口返回 500 且日志含完整栈——不做"落库失败但假装成功"的降级，因为 REQ-6 要求留档。

**Rev.G 在 `run_stats` 入口新增三道闸（全部在采集【之前】）：**

```python
def run_stats(pool, connection_id="", database="", operator=""):
    1. database 落在 _SYS_DB          → ValueError → 400
    2. connection_id 为空/全空白       → ValueError → 400   （P2-03）
    3. _ensure_schema()               → SchemaNotReadyError → 500（P1-08）
    # 三道闸都过了才真正开工
    with registry.scan_slot(connection_id):        # ScanBusyError → 429（P1-02）
        res = analyze(...)
    ... INSERT ...
```

**三道闸的次序不是随意的**：入参校验最便宜、结构验收次之、并发槽位最后。
把 `_ensure_schema()` 放在 `scan_slot` 之前，是为了不让一次注定失败的请求
白占一个并发名额；把它放在 `analyze` 之前，是为了不让用户白等最长 180 秒的采集
才在 `INSERT` 处收一个 1054。

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
| ADR-12（Rev.D 修订，**Rev.G 推翻**） | **无条件逐库执行，取消一切提前停止** | ① Rev.B 的"累计表集 == information_schema 基线"（已被 T14 实测打死：215 vs 293，判据永不成立）② Rev.D/F 的**作用域指纹比对**（连续两库结果集逐条相同即判实例级、其余库跳过）③ 检测到跨库行就 break | ②的推理只走到一半：从"两库结果集相同"能推出"命令与当前会话库无关"，**推不出"这一次返回的集合已覆盖全部目标库"**。账号可见范围、路由域、租户切分、路由元数据缺失都能造出"指纹相同但集合不完整"的实例，届时页面四个主数字是残缺的、却显示成功。我原本用 `RECON_MISMATCH` 兜底，复核后认为站不住——**用告警替代正确的数字，等于承认主结果可以是错的**。③"看到跨库行"同样只证明不限于当前库。代价：实例级作用域下多跑 3(N−2) 条命令，`lzbj_ecif` 单库实测 0.002s/条，量级可接受（§9） |
| ADR-13（Rev.J～K 两次修正，**Rev.L 最终收敛为软预算**） | 临时池 `read_timeout=30s`、`connect_timeout=5s`；统一 `180s` monotonic deadline 只控制**服务层是否启动下一阶段/库/命令**，不承诺硬墙钟上界 | ① 依赖连接默认 `read_timeout=10s` ② 每库只检查一次 ③ 动态修改 read_timeout ④ Rev.J 的 `/run` 210 秒硬上界 ⑤ Rev.K 的目标采集 215 秒硬上界 | Rev.J 把前置查询漏出预算，Rev.K 又把 PyMySQL 的连接读取超时误当成整条命令总超时。真实路径还包含连接复用 `ping()`、失败后建连、`select_db` 和异常重建。Rev.L 保留阶段/库/命令前的 checkpoint，又在拿到连接后/切库前补检查；只承诺服务层在 checkpoint 后不开新工作，已进入的池/驱动内部步骤可继续。这条承诺可由代码兑现，也不会拿驱动参数冒充绝对 deadline |
| ADR-14 | 超预算的库标 `SKIPPED` 而非 `FAILED` | 统一标 FAILED | "没来得及测"和"测了但错了"处置动作不同：前者重跑/分批即可，后者要查权限或端口。混成一个数会误导排障方向 |
| ADR-15（Rev.D 新增） | `RECON_MISMATCH` **汇总成一条**告警，逐库明细放 `item.detail` | 逐库一条告警 | 保留。Rev.E 剔除子分区后该告警不再常态触发，但**真出问题时仍可能多库同时命中**（例如一批表漏进 Proxy 路由表），50 库实例上逐库一条就是 50 条横幅。汇总告警给合计与库名，明细留在表格行里，信息一点不少 |
| ADR-16（Rev.D 新增，Rev.E 修订） | 四个数字采用 **Proxy 口径**，逻辑基线数并排呈现 | ① 用 `information_schema` 当准 ② 只显示 Proxy 口径 | 需求问的是"单表/广播表/分片表各多少张"，这三个概念**只有 Proxy 知道**，`information_schema` 没有这个维度。Rev.E 剔除二级分区子表后两个口径精确相等（215 == 215），并排呈现从"让用户自己判断"变成"互相印证" |
| ADR-17（Rev.E 新增，**Rev.G 收紧**） | 二级分区物理子表的判定 = **后缀匹配 且 逻辑父表已在本库 Proxy 结果中确认**；**集中式实例一律不剔除** | ① 计入基线（Rev.D 做法） ② 计入总表数 ③ 只看后缀（Rev.E/F 做法） | ①会让 `RECON_MISMATCH` 在每个有二级分区的库上**永久亮着**——一个永远亮的告警是背景噪声；②用户认知里 `cus_pub_translog` 是一张表不是十三张，Proxy 也只返回逻辑表名；③**实测只证明了"这 78 张子表叫这个名字"，没证明"叫这个名字的一定是子表"**。集中式实例没有二级分区这个构造、也没有 Proxy 交叉校验兜底，一张合法业务表 `orders_tdsql_subp202601` 会被静默少算且不可见（违反 REQ-5）。加上父表确认后，未确认者保留为逻辑表 → `RECON_MISMATCH` **显式报出**，误判方向仍然安全。**T17 第五轮实测已坐实**：78 张子表精确推导出 6 个父表（各 13 张），且由「基线 293、Proxy 215、后缀表 78」三个基数的算术闭合可证 6 个父表全部落在 Proxy 结果内（推导见 §10.2 T17），收紧后 215 == 215 不变 |
| ADR-18（Rev.F 新增） | 表结构在**首次发布前定稿**；发布后若需扩列，**新增 `v13/131_*.sql` 用 `ALTER TABLE … ADD COLUMN`**，绝不回头编辑 `130_*.sql` | ① 直接改 `130_*.sql` ② 把表并进 `database.py::_create_all_tables` | ①v1.6.2.2 起 checksum 漂移会让**所有已部署实例启动失败关闭**（§2.7 M-3），补救需人工往 `_KNOWN_RECONCILIATIONS` 加账本三元组，代价远高于新增一个迁移文件；②`_create_all_tables` 是 **46 张表、828 行**的大列表（Rev.I 复核实测；Rev.A～H 一直写的"27 张"是旧数），改它等于把 `database.py` 的改动面从 1 行放大到一整段 DDL，与最小化修改原则冲突（ADR-6 已述） |
| ADR-19（Rev.G 新增，Rev.L 修正文案） | `/run` 复用既有 `registry.scan_slot(connection_id)`，**不新建并发控制** | ① 不限流 ② 本模块自建一套信号量 ③ 本期改成异步任务 | 本模块是同步目标 I/O，且 Rev.L 明确没有硬墙钟上界；若不限流，重复点击会挤占 FastAPI 工作线程和目标库连接。自建配额会让两套上限各算各的，保护失效。复用 `scan_slot` 至少把新增压力纳入与既有扫描相同的全局/单实例上限；若内网 T10 显示软预算下尾部过长，再升版为异步任务（KL-6） |
| ADR-20（Rev.G 新增，**Rev.J 扩展验收范围**） | 留档表结构验收**在 `run_stats` 入口做**（模块级、首次使用时），**不做启动期失败关闭**；验收范围 = 表存在 + 全部列的**完整字段契约**（类型 / 长度 / 字符集 / 可空性 / 默认值 / 自增）+ **索引全列序与唯一性** | ① 不验收（Rev.F：迁移器不严格验收 CREATE TABLE，故"无额外适配成本"）② 扩展迁移器支持 CREATE TABLE 声明验收 ③ 进程启动期专用 schema assertion（O 的建议） | ①站不住：元数据库里若有同名但缺列/错类型的历史残留表，`CREATE TABLE IF NOT EXISTS` 会**静默跳过**、迁移仍登记成功，直到 INSERT 才 1054；迁移登记后表被删或结构漂移，`_structure_state()` 也照样返回 valid。**"不进入验收"不是安全性依据**，这一点我完全接受。②改迁移器意味着动 v1.6.2.2 刚上线的启动路径，是全平台级别的爆炸半径，为一个诊断子模块付这个代价不成比例。③**本设计与 O 的分歧仅在这一点**：表类型统计是深度诊断下的只读诊断子模块，它的留档表有问题**不应当让整个审核平台起不来**——同层级的 `index_audit`、`cluster_inspection` 在 `_create_all_tables` 里同样没有启动期结构验收，只为新模块加这一道，标准不一致且风险方向相反（把"一个页面不可用"放大成"平台不可用"）。放在 `run_stats` 入口、且在采集与并发槽位**之前**，同时满足"确定性验收"和"不让用户白跑一轮采集"。**Rev.J 扩展**：O 二轮指出 Rev.I 只比 `DATA_TYPE`，仍留着一整排"采集完才失败"的路径——`detail` 收窄成 `VARCHAR(16)` 会在 500 库采完后撞 1406、`id` 丢了 `AUTO_INCREMENT` 会在第二条记录撞主键、`created_at` 没默认值在严格模式下直接失败、字符集非 utf8mb4 会让中文 detail 撞 1366。**这条我完全接受，而且 KL-15 当时的免责理由是站不住的**：我以"COLUMN_TYPE 带显示宽度、跨发行版不一致"为由放弃长度校验，但那个问题只对**整型**成立（`int(11)` vs `int`），对 varchar 长度并不成立。现按字段分工取值——整型比 `DATA_TYPE`、字符列比 `DATA_TYPE + CHARACTER_MAXIMUM_LENGTH + CHARACTER_SET_NAME`、共通比 `IS_NULLABLE` / 归一化后的 `COLUMN_DEFAULT` / `EXTRA`（自增），索引比全列序与唯一性。默认值归一自持一份（剥一层引号 + `CURRENT_TIMESTAMP` / MariaDB 字面量 `'NULL'` 归一），**刻意不复用 `migrator._normalize_default`**——后者把 `0`/`1` 归一成 `FALSE`/`TRUE` 服务于布尔列，用在本模块的计数列上会把真实漂移掩盖掉。九种畸形场景均失败关闭，由 T-R12 与 T2-R05 共十项单测钉住，且**已在本地 MariaDB 上逐项验证过"干净结构必须通过"**（过严会把功能整个打死，比过松更难发现） |
| ADR-24（Rev.K 新增，**Rev.L 扩展**） | 元数据库阶段（`_ensure_schema` + 落库）不纳入目标软预算；明细使用批量 INSERT | ① 宣称 `/run` 或目标采集有 210/215 秒硬上界 ② 把结果落库强行纳入目标 deadline | `_ensure_schema()` 在拿槽位之前，落库在释放槽位之后；采集已完成时因 deadline 丢弃结果会让整轮工作白费。Rev.L 同时承认目标阶段本身也没有硬墙钟上界，不再把边界问题只归因于元数据库。能确定优化的是 `ITEM_INSERT_BATCH=100`，500 库由 500 次往返降为 5 次 |
| ADR-25（Rev.K 新增，P1-02） | 预算耗尽用**标志位 + 正常退出 `with`**，绝不以异常穿出连接上下文 | ① Rev.J 的 `raise _BudgetExceeded()` ② 在 `with` 外层判断剩余时间再决定进不进 | ①`TDSQLConnectionPool.get_connection()` 捕获穿出上下文的**任何**异常并 close + `_create_connection()`（`tdsql_connector.py:298-306`）——它无法区分"连接坏了"和"我们主动不干了"。于是一个正常控制信号会：销毁一条健康连接、在 deadline **之后**多付一次建连、若重连再失败还会用新异常盖掉原信号，把本该 `SKIPPED` 的库误标成 `FAILED`（"没来得及测"变成"测了但错了"，排障方向直接反了）；②外层判断挡不住"进入上下文之后、三条命令之间"耗尽的情形。**连接重建只应由真实的数据库/网络异常触发**——这条边界一旦模糊，连接池的自愈机制就会被正常控制流反复触发 |
| ADR-26（Rev.K 新增，P1-03） | 迁移槽护栏只钉**永久不变量**：槽位不重复 + 本模块独占自己的槽 + 已知前驱存在；**删除"必须是最大槽"** | ① Rev.J 的 `assert _OUR_SLOT > max(others)` ② 完全不做槽位检查 | ①它在项目下一次合法新增 `v13/131` 或 `v14/140` 之后**必然为假**——一条历史测试把项目此后的演进锁死了；我甚至把"我们不再是最大槽"写成了必须拒绝的场景，方向完全错了。"当前最大之后的下一个"只在**选槽那一刻**有意义，不该随仓库永久执行；②DEF-1 已经证明没有护栏会出事。**"选槽连续"这件事改用"已知前驱 v12/120 仍在"来证明**——这条同样永久成立，且能顺带发现版本链被误删 |
| ADR-27（Rev.L 新增，**Rev.M 修正**） | 基线查询保持可下推的 `TABLE_SCHEMA IN (...)`；**SQL 端不做大小写正确性假设**。返回行一律经全实例 `_NameSpace` 解析 canonical 库名，再对目标子集精确过滤 | ① `BINARY TABLE_SCHEMA IN (...)` ② `CAST(TABLE_SCHEMA AS BINARY)` ③ 仅依赖普通 `IN` 的服务端比较结果 ④ 在目标子集上做 CI 回退 ⑤ 全部只做精确匹配 | ①第五轮本地实测使 `information_schema` 从扫描 1 库退化为扫描全库、耗时 6.4 倍，且对应用层已覆盖的正确性零增益；②同样可能阻断下推；③参数化只防注入，不保证服务端大小写语义，故仍须检查每一返回行；④会重现 ADR-22 已封堵的兄弟库误并；⑤对 Proxy 偶发返回不同大小写过严。普通 `IN` 只做候选集减载，正确性边界固定在“全实例 canonical 解析 + 目标集精确判断”；T20 在最大内网实例留存 `EXPLAIN` 与耗时证据 |
| ADR-22（Rev.J 新增，P1-01） | 库名命名空间用 `_NameSpace`：**精确优先、CI 回退仅在候选唯一时、歧义不猜** | ① 全小写单值字典（Rev.I 及以前） ② 全部改为精确匹配、完全不容忍大小写差异 | ①在 `lower_case_table_names=0` 的实例上会把 `Sales` 与 `sales` **静默合并**——四个主数字直接错，且没有任何告警会亮。我在表名那侧已经写对了这个道理（ADR-9），却没有把它用到库名上，理由还写成"库名来自我们自己枚举的清单，不存在合并风险"——风险恰恰来自两个都真实存在的库，与输入来源无关；②过严：Proxy 若在某个版本回了不同大小写的库前缀，全精确会把整批行丢掉（**少算，且不可见**）。折中方案在候选唯一时容忍、在候选多个时拒绝并显式报 `DB_NAME_AMBIGUOUS`——**把不可判定的情况变成可见的，而不是替使用者猜**。**Rev.K 补一条使用边界（O 三轮 P1-01）**：CI 回退**只允许发生一次**，且必须在**全实例命名空间**上做。Rev.J 在 `_extract_pairs` 用全实例 `known` 解析出 canonical 名之后，又对只含目标库的 `target` 做了第二次回退——目标子集只有 `Sales` 时，另一个真实库 `sales` 的行会被"唯一命中"回 `Sales`，指定单库的统计直接错。**canonical name 之后只能做精确成员判断** |
| ADR-23（Rev.J 新增，P1-03） | **先定状态，再算汇总**：`eligible = 全部库 − 失败 − 跳过`，所有实例级汇总（含 baseline / subpartition / overlap）一律按 owner 过滤 | ① Rev.I 的"边遍历边累加，之后才判状态" ② 改文案，承认失败库的基线也计入 | ①与接口文案和 W1 告警里"未计入任何汇总数"直接矛盾，且矛盾**恰好发生在出问题的时候**：全部库 SKIPPED 时 `total_tables=0` 而 `baseline_tables` 仍是全库合计，两个并排的数字看起来像"Proxy 漏了一整个实例"；某个成功库的实例级返回里携带的失败库行还会让 `overlap_count` 凭空加 1。②改文案也是一种关闭方式，但 `baseline_tables` 的**唯一用途**就是与 `total_tables` 互相印证，两者覆盖的库集合不同则这个用途直接消失——所以该改的是代码不是文案。逐库行仍如实显示各自的基线（information_schema 那侧确实查成功了），只是不进实例级汇总 |
| ADR-21（Rev.G 新增，P1-06；**Rev.N 扩展为 7 处**） | 新权限键必须同时登记到 **7 处**（API 路径映射 / 菜单全集 / 标签 / 默认角色清单 / `index.html` 页签 / **`app.js` 的 `subtabs` 回退清单** / **`_OPERATIONAL_WRITE_PREFIXES` 第一级写端点放行清单**） | Rev.F 只登记 4 处 + 页签；Rev.M 漏 `_OPERATIONAL_WRITE_PREFIXES`（第一轮 SIT DEF-SIT-01） | 缺 `subtabs` 这一处时，只被授予"深度诊断 + 表类型统计"的自定义角色进入页面后，默认 `deepTab='cluster'` 对应的页签不可见，**页面没有活动页签**；缺放行清单时，`developer` 与全部自定义角色的写端点恒 403 而页签照常显示（DEF-SIT-01）。两类缺陷在 admin 账号下**都永远测不出来**（admin 有全部子页签且短路放行），只有最小权限角色才暴露，故必须由单测钉住而不是靠人工回归。**第一级放行清单（`_OPERATIONAL_WRITE_PREFIXES`）与第二级菜单可见性（`_PATH_TO_MENU` + `role_permissions`）是两套独立机制，缺任一处都不可用；只有 admin/dba 会短路跳过第一级，故必须用 developer 或自定义角色验收** |

---

## 8. 异常与边界矩阵

| # | 场景 | 期望行为 | 落点 |
|---|---|---|---|
| E-1 | `connection_id` 不存在/未连接 | HTTP 400 `未连接TDSQL实例或连接不存在` | `api/table_type_stats.py::_pool` |
| E-2 | `database` 指定为系统库 | HTTP 400 `不允许统计系统库: xxx` | `run_stats` 入口校验 |
| E-3 | `database` 指定的库不存在（或当前账号不可见） | **HTTP 400** `数据库不存在或当前账号不可见: xxx`（Rev.G / P2-01）。Rev.F 在集中式分支下会回“成功、0 张表”——存在但空的库与不存在的库在 `information_schema` 结果上无法区分 | `analyze` 入口用 `SHOW DATABASES` 结果校验 |
| E-4 | `SHOW DATABASES` 失败 | **一律抛出 → HTTP 500**（Rev.G / P2-01）。不再降级为“只统计指定库”：降级后 `known_dbs` 为空、库限定名无法拆分，结果已不可信却仍以“成功 + 一条 WARNING”呈现 | `analyze` |
| E-5 | 某库三条命令之一 1064 | 该库 FAILED + W1；若**所有**库都是 1064 → 追加 W8 | `_collect_distributed` / `analyze` |
| E-6 | 某库权限不足（1045/1142/1044） | 该库 FAILED + W1，detail 提示"授权不足" | 同上 |
| E-7 | 业务库为 0 | HTTP 200，全 0，W6 | `analyze` |
| E-8 | 空库（有库无表） | 该库全 0，`status='OK'`，**不产生任何告警** | 正常路径 |
| E-9 | 占位符风格混用 | 目标实例侧 `%s`（PyMySQL）、元数据库侧 `?`。本模块目标侧无参数化查询，元数据库侧全 `?` | 附录 A.1 |
| E-10 | 命令返回 0 行 | 视为该类 0 张，合法，不告警（实测 `lzbj_ecif` 无单表即此形态） | `_extract_pairs` 返回空集 |
| E-11 | 库名含特殊字符 | 用 `conn.select_db(db)`（驱动层转义），**不拼 `USE \`{db}\``** | ADR-3 |
| E-12 | 统计过程中连接断开 | 异常**穿出当前库的 `with`** → 池关闭并重建线程本地连接 → 外层捕获标该库 FAILED → **下一库用新连接继续**（Rev.G / P1-04）。`finally: tmp.close_all()` 保证最终释放 | `_collect_distributed` |
| E-13 | 实例类型解析异常 | `resolve` 自身回落全局默认，本模块检测 `source==DEFAULT` → W5 | `analyze` |
| E-14 | 库数 > 500 | 截断 + W7 | `list_business_databases` |
| E-15 | 元数据库落库失败 | HTTP 500，不返回半成品 | `run_stats` |
| E-24 | 留档表缺失 / 缺列 / 类型错 / 缺索引 | **采集之前**即 `SchemaNotReadyError` → HTTP 500，消息含可执行处置步骤；不先跑一轮 180 秒采集（Rev.G / P1-08） | `_ensure_schema` |
| E-25 | 同一实例并发发起统计 | 超过每连接上限 → `ScanBusyError` → **HTTP 429**；超过全局上限同理。槽位在异常路径下也必然释放（Rev.G / P1-02） | `registry.scan_slot` |
| E-26 | `connection_id` 为空串、缺字段或全空白 | **HTTP 422（空串/缺字段）**：`StatsRequest.min_length=1` 在进入路由前拦截；**或 400（全空白）**：提示必须显式指定，由 API 层在连接解析之前拦截（Rev.N / DEF-SIT-02 / DEF-SIT-03） | `StatsRequest` / API `run` / `run_stats` |
| E-27 | 集中式实例存在名为 `xxx_tdsql_subp202601` 的合法业务表 | **计入单表与总表**，`subpartition_tables=0`，不告警（Rev.G / P1-03） | `_collect_centralized` |
| E-28 | 分布式实例的 `xxx_tdsql_subp<数字>` 表，父表未出现在 Proxy 结果里 | **保留为逻辑表**，并作为“仅基线可见”在该库 `detail` 中点名 + `RECON_MISMATCH`（Rev.G / P1-03） | `_classify_subpartitions` |
| E-29 | 500 个库全部采集失败 | `warnings[]` ≤ 6 条、序列化 < 8 KiB，可落库可回读（`MEDIUMTEXT`）；逐库原因在 `item.detail`（Rev.G / P1-07） | `_collect_distributed` / DDL |
| E-16 | `stat_id` 不存在 | `/detail/{id}` 返回 `{"items":[],"warnings":[]}`，HTTP 200 | `get_detail` |
| E-17 | `warnings_json` 损坏 | `json.loads` 失败 → 返回 `[]`，不抛异常 | `get_detail` |
| E-18 | 结果含**其他业务库**的行 | 按限定名归到那个库；同一 `(库,表)` 全局只计一次；点亮 W9 | `_extract_pairs` / `kind_map` |
| E-19 | 结果含**系统库**的行（如 `mysql.user`） | 直接丢弃，不计入任何库 | `_collect_distributed` 的 `target` 过滤 |
| E-20 | 指定 `database=db_a` 但结果含 `db_b.*` | `db_b` 的行全部丢弃，只统计 `db_a` | 同上 |
| E-21 | 单条命令挂起 | 30s 读超时 → 该库 FAILED，detail 写"读超时（30s）"，循环继续 | `COMMAND_READ_TIMEOUT` |
| E-22 | 采集超出 180s deadline | 服务层在下一 checkpoint **不再启动新阶段/库/命令**；未采完的库标 `SKIPPED`（不计入任何汇总）+ W10；已采完的库正常返回。**单库三条命令跑到一半到期，该库整体记 `SKIPPED` 而非 `FAILED`**——"没来得及测"与"测了但错了"处置动作不同（ADR-14） | `TOTAL_BUDGET_SECONDS` / `budget_hit` 正常控制流 |
| E-30 | deadline 在探测/枚举库/基线查询之前就已耗尽 | 抛 `TimeoutError` → **HTTP 503**，消息点明在哪个阶段耗尽（Rev.K / P1-02） | `analyze` |
| E-34 | deadline 在某库的三条命令中途耗尽 | 该库整体记 `SKIPPED`（暂存整体丢弃），**且不触发连接重建**——预算是正常控制流，不是连接故障（Rev.K / P1-02，ADR-25） | `_collect_distributed` |
| E-35 | 指定 `database=Sales`，实例级结果含 `sales.*` | `sales.*` **被过滤**，不得并入 `Sales`——canonical 名之后只做精确成员判断（Rev.K / P1-01） | `_collect_distributed` |
| E-36 | 指定 `database=Sales`，普通基线 `IN` 因排序规则或服务端行为返回 `sales.*` | SQL 返回集不作为正确性边界；代码端用全实例 `known` 解析 canonical 后对目标集精确过滤，`sales.*` 不得进入 `Sales` 基线 | `_collect_baseline` / ADR-27 / T4-R01 |
| E-37 | `database_count == 0` 且接口正常返回 | 前端弹**黄色提示**"未发现可统计的业务库"，不弹绿色"统计完成" | `runTableTypeStats` |
| E-31 | 实例上存在仅大小写不同的同名库（`Sales` / `sales`） | 两库**各自独立统计**；另出一条 `DB_NAME_CASE_VARIANTS`（WARNING）（Rev.J / P1-01） | `_NameSpace` |
| E-32 | Proxy 返回的库限定名无法唯一归属（大小写歧义） | 该行**不计入任何库**，记 `DB_NAME_AMBIGUOUS`（ERROR）——不猜归属，宁可少算并显式报出（Rev.J / P1-01） | `_split_qualified` |
| E-33 | 指定库大小写与实例上的真实库不一致 | HTTP 400，并在消息里点名存在的大小写变体（Rev.J / P1-01） | `analyze` |
| E-23 | 表名本身含点号（如 `odd.name`） | 点号左侧不是已知库名 → 整串当作当前库下的表名，不误拆、不丢弃 | `_split_qualified` |

---

## 9. 爆炸半径分析与最小化修改清单

### 9.1 逐文件影响评估

| 文件 | 改动 | 影响面 | 风险 |
|---|---|---|---|
| `backend/services/table_type_stats_service.py` | 全新 | 无人引用 | **零** |
| `backend/api/table_type_stats.py` | 全新 | 新路由前缀，与现有 **26** 个 `/api/v1/*` 前缀无重叠（Rev.I 复核实测，已确认 `table-type-stats` 在仓库中零命中） | **零** |
| `backend/schema/v13/130_table_type_stats.sql` | 全新 | 两张新表，`CREATE TABLE IF NOT EXISTS` 幂等 | **低**（Rev.F 上调）。v1.6.2.2 起迁移失败即**启动关闭**（§2.7 M-1），且**发布即冻结**（M-3）。缓解：纯 `CREATE TABLE IF NOT EXISTS`、表结构须在打包前定稿（ADR-18）、**结构正确性由模块自身在首次使用时验收**（ADR-20，不依赖迁移器的列级验收） |
| `tests/test_table_type_stats.py` | 全新 | 仅测试 | **零** |
| `backend/main.py` | +2 行（第 40 行附近 import、第 176 行附近 include_router） | 路由表新增 3 条 | **极低**。`tests/test_app_routes_integrity.py` 会验证路由完整性 |
| `backend/services/auth_service.py` | +3 行（P1/P2/P3，均为字典/列表新增条目） | 权限判定 | **极低**。新增映射不改变任何既有前缀的判定；`test_rbac_path_coverage.py` 会验证 |
| `backend/services/database.py` | +1 行（`all_menus` 追加） | `role_permissions` 表新增 4 行（每角色 1 行） | **极低**。`INSERT IGNORE` 幂等；`DELETE ... NOT IN` 只删不在清单里的键，追加只会**保留**更多 |
| `frontend/index.html` | 新增一个 `<el-tab-pane>` 块（内含结果区 + 历史抽屉） | Vue 模板 | **极低**。插在“索引体检”页签之后、“结构比对”之前；不修改任何既有行 |
| `frontend/static/js/app.js` | **+4 行** + 1 个纯新增方法块 | `deepResult` 多一个 key；新增方法；返回清单追加名字；**`subtabs` 回退清单追加一项（Rev.G / P1-06）** | **极低**。`deepResult` 是 `reactive`，新增 key 不影响既有 key；`subtabs` 是纯追加，不改既有 9 项的顺序与内容——既有角色的默认落点不变 |

**Rev.G 新增的资源占用评估（P1-02 要求的证据）**：

| 维度 | Rev.F | Rev.G |
|---|---|---|
| 并发上限 | **无**（可无限并发） | 每连接 2、全局 8，**与既有慢查询扫描共用同一份配额** |
| 超限行为 | 吃满 FastAPI 工作线程 | HTTP 429 + 可读提示，既有审核/扫描不受影响 |
| 目标库连接数 | 每个并发请求 +1 条 | 同左，但请求数已被槽位封顶 |
| 单次最长占用（目标采集阶段） | 无上界 | **仍无硬上界**；180s 软 deadline 在 checkpoint 后不再新开阶段/库/命令（Rev.L） |
| 元数据库阶段 | 逐行 INSERT，500 库 = 500 次往返 | 批量 INSERT，500 库 = 5 次往返（无硬上界，KL-20） |

**取消提前停止后的耗时代价（P1-01 的账要算清楚）**：
实例级作用域下，Rev.F 最好情况是 6 条命令，Rev.G 是 3N 条。
`lzbj_ecif` 实测单条命令 0.001～0.002 秒（215 张表的库），
按 0.01 秒/条的保守估计，50 库实例 = 150 条 ≈ 1.5 秒，
500 库实例 = 1500 条 ≈ 15 秒，**均在 180 秒预算内**。
真正的耗时大头是 `information_schema` 基线查询（一次性），不是这三条命令。
**这个代价买回来的是"覆盖完整"这件事本身，值。**

### 9.2 明确**不碰**的清单（评审时逐条核对 `git diff --stat`）

* `backend/engine/**`（解析器、119 条规则）——**一个字节都不改**
* `backend/services/audit_service.py` / `rule_*` / `scan_*`
* `backend/services/tdsql_connector.py`——**只使用，不修改**（`TDSQLConnectionPool` / `TDSQLConnectionConfig` 均为现成公开构造）
* `backend/services/instance_type_service.py`——只调 `resolve()`
* `backend/services/connection_registry.py`——只调 `registry.get()` 与 `registry.scan_slot()`，**不修改其任何一行**（ADR-19）
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

# 5. 改动面核对——期望：新增 4 文件，既有文件净增 11 行 + 2 个前端块（Rev.N）
git diff --stat

# 6. Rev.G 定向回归（O 评审报告 §6 的 T-R01…T-R14）
python -m pytest tests/test_table_type_stats.py -q -k "r0 or r1"
```

**人工回归（不可由单测替代）**：

1. 既有 9 个深度诊断子页签逐个进入并完成一次核心操作；
2. admin / dba / developer / auditor，以及**仅授予 `deep-diag` + `deep-diag-tabletype` 的自定义角色**分别验证（后者是 P1-06 的唯一暴露路径）；
3. 同一实例并发执行「表类型统计」与一次既有 SQL 审核 / 慢查询扫描，确认后者响应与结果不受影响，且第 3 个并发请求收到 429；
4. 全新元数据库、由 v1.6.2.2 升级的存量元数据库**各启动两次**，确认迁移登记幂等；
5. 手工把 `table_type_stat` 改坏（删一列）后调用 `/run`，确认**在采集之前**就返回带处置提示的 500；
6. 内网 Proxy 账号执行三条命令，与平台结果逐表对数（六个数字见 §12.1）。

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

### 10.2 仍待测（**只剩 T13 一项**，不阻断且不影响任何数字）

> **Rev.I 说明**：Rev.G 新增的 4 项核查（T16～T19）**已全部完成**，
> 裁决归档在下方各"✅ 已完成"小节。四条此前只有推测的事实前提
> **全部被证据坐实**，且 **UAT 六个数字不变**。
> T13 在 Rev.G 删除提前停止后已**不影响统计正确性**，
> 只影响耗时预期与 `INSTANCE_WIDE_SCOPE` 是否显示，**不阻断开发**。

---

### T17 · 78 张子表的父表核对 —— ✅ **已完成（2026-08-31，第五轮）**

**环境**：`mysql -h 10.243.20.13 -P 15005 -u checksql`，服务端
`8.0.33-v24-txsql-22.6.9-20250509`，库 `lzbj_ecif`

**① 全部物理子表：78 行**（与 T14 算出的 293 − 215 = 78 **精确吻合**）。

**② 去重后的父表候选：正好 6 个**，每个各 13 张子表（`190001` 兜底 + `202601`…`202612`）：

| 父表 | 子表数 |
|---|---:|
| `cus_bas_merge_log` | 13 |
| `cus_pub_sync_consumer_log` | 13 |
| `cus_pub_sync_log` | 13 |
| `cus_pub_translog` | 13 |
| `cus_pub_updatelog` | 13 |
| `cus_pub_updatelog_detail` | 13 |
| **合计** | **6 × 13 = 78** ✓ |

这 6 个名字与 Rev.E 从 D3 推出来的完全一致：D3 的 `LIKE` 直接命中了前 5 个，
第 6 个 `cus_pub_sync_consumer_log` 当时被判断为"未被 LIKE 匹配到的那一张"——
**本次实测确认这个推断是对的**。

#### 结论一：父表全部在 Proxy 结果中（**算术闭合，不需要再取 98 行原始输出**）

设 `BASE` = `information_schema` 的 `BASE TABLE` 集合，`P` = 三条 Proxy 命令按
`(库,表)` 去重后的集合，`S` = 78 张后缀匹配的子表集合。已测得
`|BASE| = 293`、`|P| = 98 + 117 + 0 = 215`、`|S| = 78`。

1. Proxy 报出来的都是该库真实存在的表 ⟹ `P ⊆ BASE`；
2. 三类结果集互斥（B-4 实测）⟹ `|P| = 215` 是**去重后的真实基数**；
3. 于是 `|BASE − P| = 293 − 215 = 78`，**与 `|S|` 相等**；
4. Proxy 的 `show table` 只返回逻辑表、不返回物理子表（B-13）⟹ `S ⊆ BASE − P`；
5. 由 3、4 与 `|S| = |BASE − P| = 78` ⟹ **`S = BASE − P`**，即
   `P = BASE − S` = 逻辑基线；
6. 6 个父表都在 `BASE` 里、且都不匹配后缀（不在 `S` 里）⟹ **6 个父表全部 ∈ `P`**。∎

**残留假设只有第 4 步**（Proxy 不返回物理子表）。若它不成立，第 5 步的等号会破，
`RECON_MISMATCH` 会在 UAT 上**直接亮起来**——也就是说，
**这条假设在 UAT 时会被免费验证一次，且失败是可见的**（Rev.G 的 P1-03 设计本意）。
故不再要求内网补取 98 行原始输出。

#### 结论二：真实数据里存在**前缀嵌套的父表**，这是构造夹具想不到的形态

`cus_pub_updatelog` 与 `cus_pub_updatelog_detail` **两者都是父表**，且前者是后者的前缀。
父表推导若写成"切到第一个 `_tdsql_subp` 之前"以外的任何近似做法，
`cus_pub_updatelog_detail` 的 13 张子表就会被算到 `cus_pub_updatelog` 头上，
于是 `cus_pub_updatelog_detail` 变成"父表未确认"，13 张子表回流进逻辑基线：
**UAT 的 215/78 会变成 228/65 —— 数字错了，而且错得很像对的。**

本设计的 `_SUBPARTITION_RE` 用的是**非贪婪** `^(?P<parent>.+?)_tdsql_subp\d+$`，
已用这 78 个**真实表名**逐条验算：命中 78/78，推导出正好 6 个父表、各 13 张，
且与内网 SQL 侧 `SUBSTRING_INDEX(TABLE_NAME,'_tdsql_subp',1)` 的口径逐字一致。
新增三项定向测试 `test_r07b/c/d_*` 直接用真名钉住（§11）。

**UAT 六个数字维持不变**：`215 / 0 / 117 / 98 / 215 / 78`。

---

### T18 · 集中式实例是否存在 `_tdsql_subp` 表 —— ✅ **已完成（2026-08-31，第五轮）**

**环境**：`mysql -h 10.243.20.15 -P 15158 -u checksql`（集中式），库 `zjywgl`
（注：`10.243.20.13:15158` 连不上，实际用的是 `.15`）

```
WHERE TABLE_SCHEMA='zjywgl' AND TABLE_TYPE='BASE TABLE'
  AND TABLE_NAME REGEXP '_tdsql_subp[0-9]+$'
→ Empty set (0.005 sec)
```

**结论：0 行。** 集中式实例上不存在二级分区物理子表这一构造，
**P1-03「集中式一律不剔除」这条规则在真实环境上零风险**——它不会把任何东西
多算，也不会把任何东西少算，纯粹是一道防御。

> **证据范围要说清楚**：本次查的是集中式实例上的**一个库 `zjywgl`**，
> 不是整个实例的全部库（原用例写的是不带 `TABLE_SCHEMA` 过滤的全实例扫描）。
> 这是支持性证据、不是全称证明。但方向已经明确，且**即便某个集中式库里
> 真有一张 `xxx_tdsql_subp202601`，Rev.G 的做法（不剔除）也正是正确的那一种**
> ——所以这条不必再补测。

---

### T16 · Proxy 命令的返回是否随账号变化 —— ✅ **已关闭（2026-08-31，使用者裁决）**

使用者说明：内网所有库统一使用 `checksql` 账号执行，权限充足，
平台"实例管理"里登记的也是该账号，并对结果真实性负责。

**结论：不存在"DBA 账号与登记账号可见范围不同"的情形**，
P1-01 里"账号只能看到部分 Proxy 路由信息"这一场景在本环境**不成立**。
本用例关闭，不再要求执行。原 T09（登记账号能否执行）也一并由此结案。

---

### T19 · 元数据库是否已存在同名的 `table_type_stat` 表 —— ✅ **已完成（2026-08-31，第六轮）**

**环境**：`mysql -h 10.243.20.15 -P 15197 -u checksql`，库 `tdsql_sqlcheck`，
服务端 `8.0.33-v24-txsql-22.6.9-20250509`

```
MySQL [(none)]> SELECT TABLE_NAME, TABLE_ROWS, CREATE_TIME
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = 'tdsql_sqlcheck'
                  AND TABLE_NAME IN ('table_type_stat', 'table_type_stat_item');
Empty set (0.003 sec)
```

**结论：0 行。** 生产元数据库中**不存在同名残留表**，是干净的全新落地环境。

**对设计的影响**：

1. **部署文档无需增加"先核实/删除同名表"的步骤**（原本准备写的那条可以不写）；
2. **P1-08 的"同名残留表"失效路径在当前环境下不会触发**，`_ensure_schema()`
   对这条路径而言目前是**纯防御**。这一点我如实登记，**但不因此削弱 ADR-20**——
   P1-08 还有第二条失效路径（**迁移登记后表被人工删除或结构漂移**，
   `_structure_state()` 因无 `ADD COLUMN` 声明恒返回 `valid`、不会重放），
   那条与残留表无关、随时可能发生；而"同名残留表"本身也可能由**一次中途失败的
   部署**在将来制造出来。**一条现在不触发的防御，不等于一条不需要的防御。**
3. 顺带确认了元数据库的目标环境（见下表），与"生产元数据库是 TDSQL/MySQL、
   **MariaDB 非支持目标**"这一既有结论一致。

| 项 | 值 |
|---|---|
| 平台元数据库 | `10.243.20.15:15197`，库 `tdsql_sqlcheck` |
| 服务端版本 | `8.0.33-v24-txsql-22.6.9-20250509`（TDSQL） |
| 现有 `table_type_stat*` 表 | **无** |

> **一条顺带得到的、比 T19 本身更有价值的旁证**：这套元数据库上
> v0～v12 共 13 个迁移版本**全部成功应用过**（v1.6.2.2 已在此环境上线），
> 其中 `v12/120_gateway_report_tickets.sql` 就是一条纯
> `CREATE TABLE IF NOT EXISTS … ENGINE=InnoDB DEFAULT CHARSET=utf8mb4` 语句。
> 这说明本模块 DDL 的写法（`INT PRIMARY KEY AUTO_INCREMENT` + `INDEX` +
> InnoDB/utf8mb4，**不带 shardkey**）**在这台 TDSQL 元数据库上已被既有版本证明可用**
> ——"新建表在 TDSQL 上要不要指定分片键"这个本来会在 UAT 才暴露的问题，
> 由此提前排除。

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
* 若同时出现 `库A.*` 和 `库B.*` → **实例级作用域**。页面会显示 `INSTANCE_WIDE_SCOPE`，
  统计结果仍按 `(库,表)` 去重，不会放大 N 倍。
  **Rev.G 起两种情况的命令条数都是 3N**——提前停止优化已删除（ADR-12），
  所以这条测试的结论只影响"我们知道走的是哪条路径"，不影响任何数字。

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
该判据永远不成立，Rev.D 改用不依赖基线的指纹比对。
（**Rev.G 后续把整个作用域提前停止优化删除了**，见 ADR-12 与 §3.3 RISK-E。）

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
| ~~T09 权限~~ | ✅ **已结案（2026-08-31）**：内网统一使用 `checksql` 账号，平台登记的也是该账号，权限充足（使用者裁决，见 T16） | — |
| T10 性能 | 在**表最多**的业务库上跑三条命令，回填表数 + 各自耗时 | 若 T13 判定是当前库作用域，总响应时间 ≈ 单库耗时 × 库数；超阈值就要改异步 |
| T12 单分片实例 | 若内网有**只有 1 个 SET** 的分布式实例，在其上跑 T02 | `instance_probe_rules.py:99-104` 的已知边界：这类实例可能被判成集中式，从而分片表被报成单表。没有就注明"无" |
| **T20 基线谓词性能（Rev.M / 发布前必测）** | 在**业务库数量最多**的内网实例选同一目标库，分别对 `TABLE_SCHEMA IN ('目标库')` 与 `BINARY TABLE_SCHEMA IN ('目标库')` 执行 `EXPLAIN`，各预热 1 次后实跑至少 5 次；记录 `Scanned N database(s)`、每次耗时/中位数、返回行数，并确认两种写法的 `(TABLE_SCHEMA,TABLE_NAME,TABLE_TYPE)` 集合一致 | 验证普通 `IN` 保留元数据谓词下推；正确性仍由应用层过滤保证，不以服务端大小写行为为前提。**普通 `IN` 扫描库数或中位耗时不得劣于 `BINARY IN`；否则发布前分析原因并寻找不破坏下推的替代写法，禁止直接恢复 `BINARY`/`CAST`** |

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
【T20】目标实例业务库数 / 目标库：
【T20】普通 IN：EXPLAIN Extra= ，5 次耗时= ，中位数= ，返回行数= ：
【T20】BINARY IN：EXPLAIN Extra= ，5 次耗时= ，中位数= ，返回行数= ：
【T20】两种返回集合是否一致（是/否；否则附差集）：

—— Rev.G 新增的 T16～T19 已于第五、六轮全部回填并归档，本模板不再需要 ——
```

> T09 其实已经被 T15 顺带证明了一半：那次用的 `checksql` 账号能正常执行三条命令。
> 只要平台"实例管理"里登记的就是 `checksql`，T09 即视为通过。

### 10.5 GATE-2 放行判据（本轮实测结论 → 设计动作）

| 实测结论 | 设计动作 | 是否阻断开发 |
|---|---|---|
| T13 = 当前库作用域 | 逐库执行，`INSTANCE_WIDE_SCOPE` 不触发。代码无需改 | 否 |
| T13 = 实例级作用域 | 仍逐库执行（Rev.G 已删除提前停止），按 `(库,表)` 去重，`INSTANCE_WIDE_SCOPE` 会显示——**符合设计预期** | 否 |
| T13 无多库实例可测 | 两条路径都已实现且都有单测覆盖，按现状开发 | 否 |
| ~~T16~~ | ✅ **已关闭**：内网统一 `checksql` 账号，不存在可见范围差异（使用者裁决）。P1-01 的"账号可见范围"场景在本环境不成立 | — |
| ~~T17~~ | ✅ **已完成**：78 张子表 → 正好 6 个父表（各 13 张），算术闭合证明 6 个父表全部在 Proxy 结果中。**UAT 六个数字不变**。另暴露出真实数据里存在**前缀嵌套父表**（`cus_pub_updatelog` / `cus_pub_updatelog_detail`），已用真名新增 3 项定向测试钉住 | — |
| ~~T18~~ | ✅ **已完成**：集中式实例 `zjywgl` 库 0 行 `_tdsql_subp` 表，P1-03「集中式一律不剔除」零风险 | — |
| ~~T19~~ | ✅ **已完成**：元数据库 `tdsql_sqlcheck` 中无同名残留表，部署文档无需额外步骤；顺带证明本模块 DDL 写法在该 TDSQL 元数据库上已被 v12 迁移证明可用 | — |
| ~~T14 / D3~~ | ✅ **已完成**。差异 78 张查明为二级分区物理子表，已按 ADR-17 剔除，两口径精确对齐 | — |
| ~~T15~~ | ✅ **已完成 = A**。设计不变，超时保险留作兜底 | — |
| T09 登记账号无权限 | 出授权说明，由 DBA 补授权 | **是**（非代码问题） |
| T10 单库 > 1s 且 T13 = 当前库作用域 且库数 > 20 | 需追加"异步任务 + 进度"设计 | **是**（设计升版）。已知 `lzbj_ecif`（215 张表）单库仅 0.004s，风险很低 |
| T12 存在单分片分布式实例 | UI 强化 W5 文案 | 否（前端 1 行文案） |
| T20 普通 `IN` 扫描库数和中位耗时均不劣于 `BINARY IN`，且返回集合一致 | 保持 Rev.M 普通 `IN`；归档 `EXPLAIN` 与原始耗时 | 否 |
| T20 普通 `IN` 返回集合不同 | **不改变应用层过滤**；先确认差异行经服务层过滤后的最终结果一致，并记录服务端大小写语义 | 否（正确性已有 T4-R01），但必须归档 |
| T20 普通 `IN` 的扫描库数或中位耗时反而更差 | 发布前分析 TDSQL 优化器行为并给出不破坏下推的替代谓词及新证据；禁止无证据恢复 `BINARY`/`CAST` | **是（阻断发布，不阻断开发）** |

**只要没有命中"是"，开发即可按附录 A 照图施工。**

---


## 11. 测试设计（开发期，可在本地 MariaDB 13306 上跑）

`tests/test_table_type_stats.py`（附录 A.4），**Rev.N collect 112 项**。数据夹具直接照搬
内网实测形态（列名 `db_table`、库限定名 `sqltuning.t_max`、`with*` 双列 /
`without` 单列），**Rev.H 起子分区相关用例直接使用 2026-08-31 T17 取回的
78 个真实表名**。

**测试依赖如实说明（Rev.J / DOC-01）**——Rev.I 之前一直写"除落库两例外全部离线"，
那是 Rev.B 时期的实际情况，后来陆续加到 10 例却一直没更新，属于文档失真：

| 类别 | 数量 | 依赖 |
|---|---:|---|
| 纯离线 | **89** | 无。`FakePool` + `FakeClock` 全内存，不连任何数据库 |
| 元数据库集成 | **22** | 本地 MySQL/MariaDB（`SQLCHECK_DB_NAME`）；`@skipif(not MYSQL_AVAILABLE)` |
| 需模块落盘 | **1** | T-R08 权限键登记，断言仓库文件；设计阶段 skip |
| **collect 合计** | **112** | 含参数化展开（T3-R08 契约用例 11 条） |

Rev.K 历史实测口径：不连元数据库时 `83 passed, 23 skipped`；连上后
`105 passed, 1 skipped`。第五轮评审中，A 将 Rev.L 的 A.1～A.4 逐字抽取到临时目录，
以 importlib 挂载设计模块后实测：`pytest --collect-only` 为 **110 collected**；连元数据库
**109 passed, 1 skipped**；离线 **87 passed, 23 skipped**。Rev.M 只删除基线 SQL 的
`BINARY` 与 T4-R01 的 SQL 形态断言，不增加测试项，collect 仍为 110。
**这些数字是设计附录抽取验证，不是真实仓库模块落盘后的回归证据。**
元数据库集成用例覆盖落库与回读、`/history` 与 `created_by`、500 库告警的
`MEDIUMTEXT` 往返，以及**十一项完整字段契约失败关闭 + 一项干净 DDL 通过**；
由 `g14_schema` fixture 在每例前后 DROP + 按 DDL 重建，互不干扰。

> **破坏性目标保护（Rev.K / P1-04）——这是安全底线，不是风格问题。**
> Rev.J 的可用性探测用 `TDSQL_TEST_*`、实际 DROP 用 `SQLCHECK_DB_*`，
> **两套配置可以指向完全不同的服务器**：探测通了就不 skip，然后往
> 【生产元数据库】执行 `DROP TABLE table_type_stat`。唯一的"保护"是 fixture 里的
> `os.environ.setdefault(...)`，它既晚于 `database` 模块导入（`MYSQL_CONFIG` 早已定型），
> 又因为是 `setdefault` 而**不会覆盖**外部已设的 `SQLCHECK_DB_NAME=tdsql_sqlcheck`。
> 这条我完全接受——**测试代码删掉生产数据是不可接受的**。
>
> Rev.K：探测与执行共用 `backend.services.database.MYSQL_CONFIG`；任何 DROP 之前先过
> `assert_destructive_target_is_safe()`——库名不精确等于 `tdsql_sqlcheck_test`
> （或显式设置 `G14_ALLOW_DESTRUCTIVE_TESTS=1` + `G14_TEST_DB_NAME`）即抛
> `DestructiveTargetError`，并把 host/port/database 打进日志。
> 守门人本身由 3 条**离线**用例覆盖（不挂 `MYSQL_AVAILABLE`，任何环境都必跑）。

> **`skip` 不是 `pass`。** 设计阶段的通过/跳过数只能作为设计阶段记录，
> **不能当作发布证据**——模块真正落盘后必须在具备元数据库的环境上重跑全套，
> 这一点 O 在二轮 DOC-01 里说得对，本文档不再用设计阶段数字冒充回归结果。

> **Rev.G 新增 25 项**，全部是 O 评审报告 §6 要求的**缺陷定向测试**——
> 每一项都对应一个具体的 P1/P2，且都是"在 Rev.F 的代码上会失败、在 Rev.G 上通过"。
> 我特意没有写成"再多测几个正常路径"：那种测试通过与否说明不了任何事。

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
| `test_distributed_instance_wide_scope` | 实例级作用域：总数按 `(库,表)` 去重不放大；**三个库全部执行**；点亮 W9 | **RISK-E 核心护栏** |
| `test_distributed_per_db_scope_still_loops` | 当前库作用域：逐库执行，不点 W9 | ADR-12 |
| `test_single_database_filter_ignores_other_dbs` | 指定库时，实例级结果里其他库的行必须丢弃 | E-20 |
| `test_system_db_rows_are_dropped` | `mysql.user` / `sysdb.foo` 不得计入 | E-19 |
| `test_distributed_view_is_excluded` | 命令返回视图时按基线 VIEW 名单扣除 | 原厂口径 |
| `test_distributed_overlap_does_not_double_count` | 若三类重叠，总数不重复计算 + W2 | RISK-A 保险 |
| `test_distributed_recon_mismatch` | 双向差集写进 `item.detail`，告警只出一条 | RISK-B |
| `test_recon_mismatch_is_aggregated_not_per_db` | 三个库都不一致时**只出一条**告警，含库数与合计 | **ADR-15 护栏** |

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

**Rev.G 新增的缺陷定向测试（对应 O 评审报告 §6 的 T-R01～T-R14）**：

| O 编号 | 用例 | 验证 | 关闭的问题 |
|---|---|---|---|
| T-R01 | `test_r01_identical_fingerprint_must_not_skip_third_db` | 前两库返回集合完全相同、第三库另有 2 张表 → **必须执行第三库**，总数 4 不是 2，且不留 `RECON_MISMATCH` | **P1-01** |
| T-R02 | `test_r02_same_connection_concurrency_is_rejected` | 同连接第二个请求抛 `ScanBusyError`，首请求退出后槽位可再次获取 | **P1-02** |
| T-R02 | `test_r02b_slot_is_released_when_collection_raises` | 采集抛异常时槽位仍被释放，不泄漏成永久占用 | **P1-02** |
| T-R02 | `test_r02c_api_maps_scan_busy_to_429` | `ScanBusyError` → HTTP 429 + 可读提示 | **P1-02** |
| T-R03 | `test_r03_global_quota_is_shared_with_existing_scans` | **双向验证共用配额**：既有扫描占满全局槽位时本模块被拒；本模块占用时既有扫描同样被拒 | **P1-02** |
| T-R04 | `test_r04_broken_connection_is_rebuilt_before_next_db` | 首库断链（2013）→ `ctx_count==2`、`generation==1`、次库拿到**重建后的新连接**并正常完成 | **P1-04** |
| T-R04 | `test_r04b_read_timeout_also_rebuilds` | 读超时同样穿出 `with` 触发重建 | **P1-04** |
| T-R05 | `test_r05_failed_db_partial_result_does_not_pollute` | db_a 第一条命令返回跨库行（含 db_b 的表）、第二条失败 → db_a 整库丢弃，db_b 只保留自扫结果 | **P1-05** |
| T-R05 | `test_r05b_overlap_is_not_polluted_by_failed_db` | 失败库的暂存行不参与重叠判定，`overlap_count==0` | **P1-05** |
| T-R06 | `test_r06_centralized_keeps_legit_subp_named_table` | 集中式的 `orders_tdsql_subp202601` 计入单表与总表，`subpartition_tables==0`，不告警 | **P1-03** |
| T-R07 | `test_r07_distributed_requires_confirmed_parent` | 父表已确认 → 剔除；父表未确认 → 保留为逻辑表，且在该库 `detail` 中**被点名** + `RECON_MISMATCH` | **P1-03** |
| T-R07（Rev.H） | `test_r07b_real_intranet_names_derive_exactly_six_parents` | **78 个真实表名**（T17）必须推导出正好 6 个父表、各 13 张，且与内网 SQL 的 `SUBSTRING_INDEX` 口径逐字一致 | **P1-03 / KL-16** |
| T-R07（Rev.H） | `test_r07c_nested_prefix_parents_are_not_confused` | 前缀嵌套的 `cus_pub_updatelog` 与 `cus_pub_updatelog_detail` 各归各的；只确认短的那个时，`_detail` 的子表**不得**被剔除 | **KL-16** |
| T-R07（Rev.H） | `test_r07d_uat_parent_confirmation_is_all_or_nothing_per_parent` | 6 个父表全确认 → 剔 78 张；缺一个父表 → 只剔 65 张且 13 张回流进逻辑基线（对应 UAT 会变成 228/65 并报 `RECON_MISMATCH`） | **P1-03 / KL-16** |
| T-R08 | `test_r08_permission_key_is_registered_at_every_point` | 权限键在 4 个后端/前端文件中均已登记，且**逐字校验 `app.js` 的 `subtabs` 行**含新页签（设计阶段自动跳过，落盘后即为硬门禁） | **P1-06** |
| T-R09 | `test_r09_five_hundred_failed_databases_is_bounded` | 500 库全失败 → 告警**仅 1 条** `PROXY_CMD_FAILED`、`warnings[]` ≤ 6 条、序列化 < 8 KiB、逐库原因仍在 `detail` 且 ≤ 512 字符 | **P1-07** |
| T-R09 | `test_r09b_large_warnings_survive_round_trip` | 500 库失败的告警落库后**原样回读**（`MEDIUMTEXT`，无截断），500 条明细齐全 | **P1-07** |
| T-R10 | `test_r10_centralized_nonexistent_database_is_rejected` | 不存在的库 → `ValueError`；**存在但空的库仍正常返回 0 且 status=OK**（两者可区分） | **P2-01** |
| T-R10 | `test_r10b_show_databases_failure_is_not_silent` | 库枚举失败一律抛出，不吞成"空库" | **P2-01** |
| T-R11 | `test_r11_empty_connection_id_is_rejected` | 空串与全空白均 `ValueError` | **P2-03** |
| T-R11 | `test_r11b_api_model_requires_connection_id` | `StatsRequest` 在契约层就挡住缺字段/空串 | **P2-03** |
| T-R12 | `test_r12_missing_table_fails_closed` | 迁移登记后表被删 → `SchemaNotReadyError` | **P1-08** |
| T-R12 | `test_r12b_missing_column_fails_closed` | 同名缺列表 → 失败关闭，消息点名缺哪一列 | **P1-08** |
| T-R12 | `test_r12c_wrong_type_fails_closed` | `warnings_json` 退回 `TEXT`、计数列变 `VARCHAR` → 均失败关闭 | **P1-08 / P1-07** |
| T-R12 | `test_r12d_missing_index_fails_closed` | 缺 `idx_tts_created` → 失败关闭 | **P1-08** |
| T-R12 | `test_r12e_run_stats_fails_before_collecting` | 结构不合格时 `analyze` **一次都没被调用**——不让用户白跑 180 秒 | **P1-08 / ADR-20** |
| T-R12 | `test_r12f_ddl_and_service_column_lists_agree` | DDL 文件的列清单与服务的 `_STAT_COLUMNS`/`_ITEM_COLUMNS` 逐字一致，且 `warnings_json` 必须是 `MEDIUMTEXT` | **P1-07 / P1-08** |

**Rev.J 新增的缺陷定向测试（对应 O 第二轮评审 §7 的 T2-R01～T2-R10）**：

| O 编号 | 用例 | 验证 | 关闭的问题 |
|---|---|---|---|
| T2-R01 | `test_t2r01_case_variant_databases_are_not_merged` | `Sales` 与 `sales` 各自成行、分片/单表各归各的、`database_count==2`、`total==2`，且不产生虚假 `RECON_MISMATCH`；另出 `DB_NAME_CASE_VARIANTS` | **P1-01** |
| T2-R01 | `test_t2r01b_wrong_case_database_is_rejected` | 指定 `Sales` 而实例上只有 `sales` → 400，且消息点名真实变体；精确名正常 | **P1-01** |
| T2-R01 | `test_t2r01c_ambiguous_qualified_name_is_not_guessed` | `_NameSpace` 四种解析语义逐条钉住；歧义行**不计入任何库**并记 `DB_NAME_AMBIGUOUS` | **P1-01** |
| T2-R02 | `test_t2r02_deadline_covers_prelude_and_every_command` | 可控时钟下每条命令耗 60s：第 3 条**不得启动**，该库记 `SKIPPED`（Rev.I 会跑满三条） | **P1-02** |
| T2-R02 | `test_t2r02b_deadline_blocks_baseline_query` | deadline 已过时，基线查询之前即抛 `TimeoutError`（Rev.I 完全不计前置查询） | **P1-02** |
| T2-R02 | ~~`test_t2r02c_provable_wall_upper_bound_is_documented`~~ | **Rev.K 删除并由 T3-R04 取代**——它断言的 `MAX_WALL_SECONDS == 210` 本身就是错的公式（漏算建连 + `select_db`），而且只做常量算术、不覆盖它声称的阶段 | **P1-02 / KL-19** |
| T2-R03 | `test_t2r03_failed_db_rows_do_not_enter_any_rollup` | 成功库的跨库行含失败库同一张表的两个类型 → `overlap_count==0`；失败库的基线与子分区不进实例级汇总，也不点亮 `SUBPARTITION_EXCLUDED`；逐库行仍如实显示该库基线 | **P1-03** |
| T2-R04 | `test_time_budget_skips_remaining`（扩展） | 全部 `SKIPPED` 时 `total/baseline/subpartition/overlap` **一律为 0** | **P1-03** |
| T2-R05 | `test_r12*` 六项（扩展为完整契约） | 缺表 / 缺列 / 错类型 / 缺索引 / 采集前拦截 / DDL 与服务列清单一致；另在本地 MariaDB 上逐项验证 `detail` 收窄、`id` 丢自增、`created_at` 缺默认、`stat_id` 可空、`warnings_json` 退回 TEXT 五种畸形均失败关闭，且**干净结构必须通过** | **P1-04** |
| T2-R06 | `test_migration_slot_scanner_detects_duplicates` | 扫描器能看见"同槽两个文件"——Rev.I 的单值字典看不见，等于没防 | **P1-05** |
| T2-R06 | `test_migration_slot_guard_passes_before_and_after_landing` | 用两份合成状态把**落盘前**与**落盘后**都验一遍（Rev.I 的写法在落盘后必失败） | **P1-05** |
| T2-R06 | ~~`test_migration_slot_guard_rejects_duplicate_and_squatter`（旧版）~~ | Rev.J 曾错误要求"我们不再是最大槽"也必须失败；Rev.K 已由 T3-R05/T3-R06 改写，只拒绝同槽多文件、槽被占、前驱被删 | **P1-05 / 三轮 P1-03** |
| T2-R06 | `test_migration_slot_is_available_and_unique` | 对**真实仓库目录**执行同一套断言 | **P1-05 / KL-17** |
| T2-R07 | `test_t2r07_candidate_in_proxy_is_a_logical_table` | `orders` 与 `orders_tdsql_subp202601` 同时出现在 Proxy 结果中 → 两者都是逻辑表，`subpartition_tables==0`，不产生虚假差异告警 | **P2-01** |
| T2-R07 | `test_t2r07b_real_subpartition_is_still_stripped` | 反向护栏：真正的子表（不在 Proxy 结果里）仍必须被剔除，不得因 P2-01 回退 | **P2-01** |
| T2-R10 | 首轮 T-R01～T-R14 全套 | 继续通过，不因本轮修正回退 | 首轮全部 |

**Rev.K 新增的缺陷定向测试（对应 O 第三轮评审 §7 的 T3-R01～T3-R10）**：

| O 编号 | 用例 | 验证 | 关闭的问题 |
|---|---|---|---|
| T3-R01 | `test_t3r01_named_database_must_not_absorb_case_sibling` | 指定 `database="Sales"` 时，实例级返回里的 `sales.t_lower` **必须被过滤**，总数 1 不是 2；反向指定 `sales` 同理。Rev.J 的全库用例通不过这条 | **P1-01** |
| T3-R02 | `test_t3r02_budget_exit_does_not_rebuild_connection` | 预算耗尽时 `pool.create_calls == 0` —— 正常控制流不得触发连接池重建 | **P1-02** |
| T3-R03 | `test_t3r03_budget_signal_survives_a_failing_reconnect` | 令重连必失败，预算路径仍须得到 `SKIPPED` 而非被新异常盖成 `FAILED` | **P1-02** |
| T3-R03 | `test_t3r03b_real_error_still_rebuilds` | 反向护栏：**真实**数据库异常仍必须触发一次重建（P1-04 不得回退） | **P1-02 / 首轮 P1-04** |
| T3-R04 | ~~`test_t3r04_collect_upper_bound_covers_every_declared_phase`~~ | **Rev.L 删除**：该用例把 `read_timeout` 误当整条 SQL 上界，通过也不能证明实际墙钟时间 | **四轮 P1-02 / KL-19** |
| T3-R04 | `test_t3r04_soft_budget_scope_is_explicit` | **Rev.L 改写**：删除不成立的 215 秒硬上界断言；钉住 `TOTAL_BUDGET_SECONDS=180`、无任何 `MAX_*WALL*` 常量，以及"到期后不再启动新操作"的软预算作用域 | **四轮 P1-02** |
| T3-R04 | `test_t3r04b_deadline_checkpoints_are_where_the_soft_budget_says` | 钉住阶段/库/命令前检查，及"拿到连接后 < checkpoint < `select_db`"的顺序；预算路径是"置标志 + 正常退出"而非 `raise` | **四轮 P1-02** |
| T3-R05 | `test_t3r05_guard_does_not_block_future_migrations` | 本模块落盘后，`v13/131`、`v14/140`、乃至 `v15/150` 同时存在时护栏**仍须通过** | **P1-03** |
| T3-R06 | `test_t3r06_guard_rejects_duplicate_and_squatter` | 只拒该拒的：同槽双文件、槽被别人占、我们槽里混进第二个文件、前驱被误删 | **P1-03** |
| T3-R07 | `test_t3r07_destructive_guard_rejects_non_test_database` | 目标为 `tdsql_sqlcheck` / `production` / 空 时，**在任何 DROP 之前**抛 `DestructiveTargetError` | **P1-04** |
| T3-R07 | `test_t3r07b_custom_test_db_needs_explicit_opt_in` | 自定义测试库必须**同时**给出库名与破坏性开关才放行 | **P1-04** |
| T3-R07 | `test_t3r07c_probe_and_execution_use_the_same_config` | 源码级断言：探测读的是 `effective_db_config()`，不再出现 `TDSQL_TEST`，且必须传入 `database` | **P1-04 / 四轮 P2-02** |
| T3-R08 | `test_t3r08_full_schema_contract_fails_closed`（**11 条参数化**） | 长度收窄 ×2、丢自增、缺默认值、默认值被改、可空性、字符集、类型 ×2、索引列序、索引唯一性——逐项在采集前失败关闭且报错点名具体列 | **P2-01** |
| T3-R08 | `test_t3r08b_clean_ddl_passes_verification` | 反向护栏：**干净 DDL 必须通过**，且连续两次调用稳定无副作用 | **P2-01** |
| T3-R10 | `test_t3r10_zero_effective_databases_is_not_partial_success` | 一失败一跳过时有效库数为 0，所有"不计入"的汇总均为 0（前端据此报红而非"部分完成"） | **P2-03** |

**Rev.L 新增、Rev.M 定点修正的缺陷定向测试（对应 O 第四轮与 A 第五轮评审）**：

| O 编号 | 用例 | 验证 | 关闭的问题 |
|---|---|---|---|
| T4-R01 | `test_t4r01_baseline_rejects_case_sibling_returned_by_ci_in` | 在普通 `IN` 下让测试替身故意同时返回 `Sales` / `sales`；只断言全实例 canonical 后保留 `Sales` 的行为，**不再断言 SQL 含 `BINARY`** | **P1-01 / 五轮 P2-01** |
| T4-R02 | `test_t4r02_real_pool_tail_path_can_exceed_old_formula` | 直接跑真实 `TDSQLConnectionPool.get_connection()` 控制流，模拟 `ping` 30s + 建连 5s + `select_db` 30s + 异常重建 5s = 70s，证明旧公式不成立；反向再断言 `ping→建连` 跨过 deadline 后，服务层在拿到连接复查并不发 `select_db` | **P1-02** |
| T4-R03 | `test_t4r03_pymysql_read_timeout_is_per_read` | 对 PyMySQL `_read_bytes` 连续两次模拟各 20s 成功读取，总时长 40s 仍不超时，钉住 `read_timeout=30` 是**每次底层读取**而非整条 SQL 总时间 | **P1-02** |
| T4-R04 | 前端 UAT（§12.5） | `database_count=0` 弹黄色"未发现可统计的业务库"，不弹绿色"统计完成" | **P2-01** |
| T4-R05 | `test_t4r05_probe_connects_effective_database` | monkeypatch `pymysql.connect`，逐项断言 host/port/user/password/**database**/charset 全部来自 `effective_db_config()` | **P2-02** |
| T4-R06 | 全文一致性扫描（§12.5） | 当前态不再声称 210/215 秒硬上界，Rev.K 数字只留在历史记录 | **DOC-01** |

**Rev.N 新增的缺陷定向测试（对应 A 第一轮 SIT 报告）**：

| SIT 编号 | 用例 | 验证 | 关闭的问题 |
|---|---|---|---|
| DEF-SIT-01 | `test_sit01_write_endpoint_is_reachable_by_non_admin_roles` | 以既有 G5 `index-audit/run` 为基准，断言四个内置角色对 G14 写端点的可达性与既有深度诊断子模块**完全一致**；并钉住 `/api/v1/table-type-stats/` 已登记于 `_OPERATIONAL_WRITE_PREFIXES` 且带尾斜杠 | **BLOCK：developer 与全部自定义角色写端点恒 403** |
| DEF-SIT-03 | `test_sit03_blank_connection_id_reports_the_right_reason` | 直接打 API 层：全空白 `connection_id` 必须 400 且提示"必须指定 connection_id"，且**不得先去解析连接**（覆盖真实调用顺序，替代此前只测服务层不可达路径的虚假信心） | **MINOR：报错文案误导、服务层守卫经 HTTP 不可达** |

> **T3-R09（历史抽屉状态清空）与 T3-R10 的前端分支**属于前端行为，
> 代码在附录 A.5.4 / A.5.5，验收方式见 §12.5——本项目前端无自动化测试框架，
> 沿用既有惯例以可执行的人工验收条目覆盖，**不假装它们有单测**。
| T-R13 | `test_r13_api_records_current_operator` | API 签名含 `http_request: Request`，`operator` 收到真实用户名；未认证兜底 `anonymous`（**不写空串**） | **P2-02** |
| T-R13 | `test_r13_created_by_is_persisted` | `created_by` 真正落库并可从 `/history` 回读 | **P2-02** |
| T-R14 | `test_lzbj_ecif_uat_baseline`（已有） | 端到端对数：215/0/117/98/215/78，告警仅 `SUBPARTITION_EXCLUDED` | UAT 基准 |

**FakePool 设计**（关键，使全部分布式逻辑可离线测试）：脚本化
`databases` / `info_schema` / `per_db[(当前库, sql)]` / `show_db_fail`，并记录
`seen`（所有执行过的 SQL）与 `selected`（所有切库动作）——后者正是 ADR-3 护栏的断言依据。
临时池的构造点是模块级钩子 `_new_pool = TDSQLConnectionPool`，测试里 monkeypatch
该名字即可注入 FakePool；**这是唯一为可测性做的让步，成本 1 行**。

**Rev.G 给 FakePool 补的连接重建语义**（P1-04 的可测性前提）：
`get_connection()` 忠实复刻 `tdsql_connector.py:287-307` 的行为——
只有**异常穿出 `with`** 时才递增 `generation`（等价于真实池的"关闭并重建"）。
测试断言 `ctx_count`（进了几次上下文）与 `conn_ids`（每个库拿到的是第几代连接），
于是"坏连接有没有被后续库复用"变成了一个**可以断言的事实**，而不是靠读代码相信。
这是本轮我认为最有价值的一处测试设计：**P1-04 描述的缺陷在 Rev.F 的测试体系下
根本无法被发现**，因为旧 FakePool 对异常穿不穿出 `with` 完全无感。

**设计附录验证结果（2026-09-01，第五轮评审）**：A 用 importlib 把 Rev.L 附录
A.1 / A.2 挂载为 `backend.services.table_type_stats_service` 与
`backend.api.table_type_stats`（**仓库功能代码零改动**），实测 **110 collected**；
连接元数据库时 **109 passed + 1 skipped**，离线模式 **87 passed + 23 skipped**。
其中唯一长期跳过项 T-R08 断言模块落盘后的仓库权限键，设计阶段无从满足。
Rev.M 不新增测试项，只删除生产 SQL 的 `BINARY` 与 T4-R01 的 SQL 字符串断言。
**这是设计阶段记录，不是发布证据**；仓库实现落盘后仍必须重跑全部 110 项。


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
      `schema_migrations` 中出现 `v13_130_table_type_stats` 且 checksum 与文件一致
- [ ] **迁移专项**：连续启动两次（模拟重启），第二次走 `_structure_state` → `valid` 跳过，不重复执行
- [ ] **结构验收专项（Rev.G / P1-08）**：手工 `ALTER TABLE table_type_stat DROP COLUMN subpartition_tables` 后调用 `/run`，必须在**采集之前**返回 500 且消息点名缺失列与处置步骤；恢复后功能正常
- [ ] **结构验收专项**：手工 `DROP TABLE table_type_stat` 后重启服务——**服务必须能正常启动**（诊断子模块的表问题不得阻断平台启动，ADR-20），进入该页签点击统计时才报错
- [ ] 历史列表与明细可在**页面上**回看（历史抽屉可打开、可选中某次查看逐库明细），且 `created_by` 显示当前登录用户名（Rev.G / P2-02）

### 12.2 权限

- [ ] `admin` / `dba` / `developer` / `auditor` 四角色默认可见该页签
- [ ] 权限矩阵页出现"深度诊断-表类型统计"条目，可勾选/取消
- [ ] 取消勾选后该角色刷新页面看不到页签，且调 `/run` 被拒（403）
- [ ] **仅授予 `deep-diag` + `deep-diag-tabletype` 的自定义角色**进入深度诊断页时，活动页签**自动落到"表类型统计"**（Rev.G / P1-06；这是该缺陷的唯一暴露路径，admin 账号下永远测不出来）
- [ ] **developer 角色登录后，点击"统计表类型"按钮可正常执行（非 403）**（Rev.N / DEF-SIT-01；第一级放行清单缺失时 admin/dba 测不出来，必须用 developer 或自定义角色验收）
- [ ] `pytest tests/test_rbac_path_coverage.py` 通过

### 12.3 零回归与并发

- [ ] `pytest tests/` 全绿，且**通过用例数不少于改动前**
- [ ] `git diff --stat` 与 §4.4 表格逐行一致（新增 4 文件；`main.py` +2、`auth_service.py` +3、`database.py` +1、`app.js` +4、`index.html` 仅新增块）
- [ ] `backend/engine/` 目录 `git diff` 为空
- [ ] 既有 9 个深度诊断子页签功能不变（逐个点一遍）
- [ ] **并发专项（Rev.G / P1-02）**：同一实例连续发起 3 次统计，第 3 次返回 **429** 且提示可读；期间在**另一个浏览器标签**发起一次既有 SQL 审核 / 慢查询扫描，确认其响应时间与结果不受影响
- [ ] **并发专项**：统计过程中断开网络或杀掉目标库连接，确认槽位被释放（随后可再次发起统计，不出现"永远 429"）
- [ ] **软预算专项（Rev.L / P1-02）**：在库数最多的实例上分别记录目标采集与元数据库落库耗时；把 `TOTAL_BUDGET_SECONDS` 临时调小，确认 deadline 到期后不再启动下一条 Proxy 命令、未开始的库标 `SKIPPED`。**不再以 215 秒为通过条件**；若长尾明显影响既有扫描，按 KL-6 升版异步

### 12.4 Rev.M 定点整改专项（第五轮评审）

- [ ] **基线谓词性能（P2-01 / T20）**：在业务库数量最多的内网实例，对同一目标库分别执行普通 `TABLE_SCHEMA IN (...)` 与 `BINARY TABLE_SCHEMA IN (...)`；按 §10.3 记录 `EXPLAIN` 的 `Scanned N database(s)`、至少 5 次耗时及中位数、返回集合差异。普通 `IN` 的扫描库数或中位耗时不得更差；异常时阻断发布并分析优化器，禁止直接恢复 `BINARY` / `CAST`
- [ ] **应用层行为不依赖 SQL 大小写语义（P2-01 / T4-R01）**：让测试替身在普通 `IN` 下故意同时返回 `Sales.*` 与 `sales.*`，集中式/分布式均只保留 `Sales.*`；测试只断言结果，不断言 SQL 必须包含某个关键字
- [ ] **文档与附录一致性**：§4.2、§6.4、ADR-27、E-36、附录 A.1 均为普通 `TABLE_SCHEMA IN (...)`；除第五轮历史证据、T20 对照 SQL 和修订记录外，当前态不得把 `BINARY` 描述为生产方案

### 12.5 Rev.L 新增专项（第四轮评审）

- [ ] **基线不吞大小写兄弟库（P1-01 / T4-R01）**：指定 `Sales`，令 `information_schema` 返回 `Sales.*` 与 `sales.*`，集中式/分布式均只保留 `Sales.*`
- [ ] **连接池尾部路径（P1-02 / T4-R02）**：覆盖 `ping → connect → select_db → 异常重建`；确认文档和代码均不再声称 180+35 是硬上界
- [ ] **多次读取语义（P1-02 / T4-R03）**：模拟每次读取均小于 30s、累计超过 30s，确认只触发软 deadline checkpoint 语义，不产生虚假的硬上界断言
- [ ] **无业务库提示（P2-01 / T4-R04）**：`database_count=0` 时为 warning，文案提示检查权限/空实例，不得出现绿色“统计完成”
- [ ] **探测完整目标配置（P2-02 / T4-R05）**：捕获 `pymysql.connect` 实参，host/port/user/database 必须与 `MYSQL_CONFIG` 一致
- [ ] **当前态文档一致性（DOC-01 / T4-R06）**：当前章节不再出现 210/215 秒硬上界、Rev.G 的 66+1 当前测试数或“本模块必须永远是最大迁移”

### 12.6 Rev.K 专项（O 第三轮评审）

- [ ] **指定单库不吞兄弟库（P1-01）**：若内网有仅大小写不同的同名库，指定其中一个统计，确认另一个库的表**没有**被计入；无此形态则在测试环境建 `Sales`/`sales` 各一张表验证
- [ ] **预算不触发重连（P1-02）**：把 `TOTAL_BUDGET_SECONDS` 临时调小到能在库中途耗尽，观察日志确认**没有**出现连接重建/重连日志，且该库状态为 `SKIPPED` 而非 `FAILED`
- [ ] **耗时分段记录（P1-02）**：见 §12.3 的软预算专项，采集阶段与落库阶段**分别记录**，只验证 checkpoint 后不启动新操作
- [ ] **迁移护栏不挡未来（P1-03）**：在本地临时新建一个 `backend/schema/v14/140_dummy.sql`，确认 `pytest -k migration_slot` **仍然全绿**；删除该临时文件
- [ ] **破坏性目标保护（P1-04）**：故意用 `SQLCHECK_DB_NAME=tdsql_sqlcheck` 跑一次集成用例，确认在**任何 DROP 之前**就报错退出，且日志打印了 host/port/database
- [ ] **历史抽屉状态清空（P2-02 / T3-R09）**：打开历史→选中一行看到告警→关闭→**切换到另一个实例**→再打开历史，确认**没有**残留上一条的告警；浏览器控制台**无** Vue 只读告警
- [ ] **零有效库提示（P2-03 / T3-R10）**：分别构造「无业务库」「全失败」「全跳过」「失败+跳过覆盖全部」「真正部分成功」「全部成功」六种情形；无业务库为 warning，后三类无有效结果不得称“部分完成”，仅全部成功为 success

### 12.7 Rev.J 专项（O 第二轮评审）

- [ ] **库名大小写（P1-01）**：若内网存在仅大小写不同的同名库，确认两库**各自成行**、总数不合并，且出现 `DB_NAME_CASE_VARIANTS`。无此形态则注明"无"，并改为在测试环境人工建 `Sales` / `sales` 各一张表验证
- [ ] **指定库大小写**：输入错误大小写的库名，必须回 400 并在消息里点名真实存在的变体，**不得**当成存在
- [ ] **失败库不进汇总（P1-03）**：人为让一个库的账号权限不足，确认该库 `FAILED`，且实例级 `baseline_tables` / `subpartition_tables` / `overlap_count` **均不含该库**；逐库行仍显示该库自己的基线
- [ ] **结构契约（P1-04）**：依次把 `detail` 收窄为 `VARCHAR(16)`、去掉 `id` 的 `AUTO_INCREMENT`、去掉 `created_at` 默认值、把 `stat_id` 改为可空，四次都必须在**采集之前**返回 500 且消息点名具体列；恢复后功能正常
- [ ] **迁移槽护栏（P1-05）**：`backend/schema/v13/130_table_type_stats.sql` **真正落盘后**，`pytest -k migration_slot` 必须全绿（Rev.I 的写法会在此处失败）
- [ ] **合法后缀逻辑表（P2-01）**：若内网有 `xxx` 与 `xxx_tdsql_subp<数字>` 同时出现在 Proxy 结果中的情形，确认两者都计入逻辑表、`subpartition_tables` 不含它
- [ ] **历史告警展开（P2-02）**：制造 >10 条告警的一次统计，打开历史抽屉，确认显示总条数且可展开全部
- [ ] **失败提示（P2-03）**：部分失败时提示为 warning 并写明成功/失败/跳过库数；全部失败时为 error，**不得**出现绿色"统计完成"

---

## 13. 已知限制与风险登记册

| # | 项 | 说明 | 处置 |
|---|---|---|---|
| KL-1 | 项目内两套系统库清单不一致 | `index_audit_service._SYS` 缺 `sysdb`/`query_rewrite`/`xa`；`zk_scan_enrich_service.SYSTEM_DATABASES` 缺 `tdsqlpcloud*`/`__tencentdb__` | 本模块取并集自持；**既有两处不动**（约束 2），另案统一 |
| KL-2 | `slow_enrich_service.py:219` 在共享池连接上 `select_db` 后未恢复 | 既有潜在污染 | 本模块不重蹈（ADR-3）；既有代码本次不动，另案 |
| KL-3 | 本模块两张表未接 `retention_service` | 仅人工触发时增长，年增 < 1 万行 | 与 `index_audit` 一致；若未来接入需同时补 FK 级联 |
| KL-4 | 单分片分布式实例可能被判为集中式 | `instance_probe_rules.py:99-104` 的已知边界 | W5 告警提示 + 实例管理页可手工锁定类型；T12 确认内网是否存在此形态 |
| KL-5 | 三条 `/*proxy*/` 命令无官方语法文档背书 | 来源为原厂口头提供 | T02/T03/T11 实测锚定；实测输出入附录 B 作为回归基线 |
| KL-6 | 统计为同步执行 | 库数 × 3 条命令，大实例可能较慢；Rev.G 取消提前停止后**恒为 3N 条** | T10 定量；并发由 `registry.scan_slot` 封顶（ADR-19）。180s 为软 deadline：到期后不再启动新目标 I/O，**不承诺墙钟硬上界**（ADR-13 / KL-19）。若 T10 实测长尾影响交互或挤占既有扫描，则升版为异步任务 + 进度查询 |
| KL-7 | 结果为快照，不反映采集期间的 DDL 变更 | 无事务一致性保证 | 结果带 `created_at`，UI 标注"采集时刻快照" |
| KL-8 | 命令作用域（当前库 / 实例级）未裁决 | 返回库限定名 + 原厂"库名+表名去重"的措辞都指向实例级，但缺少多业务库实例的实测 | 设计在两种作用域下都正确（ADR-11）。**Rev.G 取消提前停止后，T13 已不影响任何数字**，只影响耗时预期与 W9 是否显示 |
| ~~KL-9~~ | ~~空结果集是否导致命令挂起未裁决~~ | **已裁决（Rev.C / §3.3 RISK-F / §10.1 T15）**：命令以 **OK 包**在 0.001 秒返回，`Query OK, 0 rows affected`，不挂起；赤兔转圈是其前端在等结果集列元数据所致，与命令无关 | **本项关闭**。30s 读超时 + 180s 总预算保留为纯保险，在已实测形态下永不触发 |
| KL-10 | 二级分区子表识别依赖命名约定 `_tdsql_subp<数字>` | D3 + **T17 全量实测**确认该命名（78/78 命中，推导出正好 6 个父表各 13 张），但无官方文档背书；实测证明的是"这些子表叫这个名字"，**"叫这个名字的一定是子表"仍无法证明** | Rev.G 收紧为「后缀匹配 **且** 逻辑父表已在 Proxy 结果中确认」，集中式一律不剔除（ADR-17）。误判方向安全：未确认者保留为逻辑表 → `RECON_MISMATCH` 显式报出（可见），不会静默少算。T17 已在全量数据上取证；**T18 另证实集中式实例无此构造** |
| KL-17（Rev.I 新增，**Rev.N 第三次复发补记**） | **"版本复核"若只 diff 自己引用到的文件，就发现不了"自己要落进去的槽位被占了"** | Rev.F 的任务是"依 v1.6.2.2 上线后的代码变更复核"，我把范围定义成"本设计引用的 13 个文件有没有变"，逐个 diff 过去——**而新文件的槽位本来就不在这 13 个文件里**，那种 diff 无论多仔细都不可能命中。结果 `v11/110` 被 v1.6.2.2 占用一事直到 Rev.I 才发现（DEF-1，§2.7）。**这是"登记点枚举不全"的第三次复发**：DEF-1（迁移槽位）、P1-06（`subtabs`）、DEF-SIT-01（`_OPERATIONAL_WRITE_PREFIXES`，第一轮 SIT，Rev.N 补 P7），三次同源 | 三条：①**方法上**——今后凡"新增文件/新增标识符"类的设计，复核必须包含一次**目录与命名空间的重新枚举**（schema 槽位、路由前缀、权限键、菜单键、表名），而不只是 diff 引用文件；②**工程上**——把能自动化的部分变成测试：`test_migration_slot_is_not_already_taken` 已钉住 schema 槽位，`test_r08_permission_key_is_registered_at_every_point` 已钉住权限键，**`test_sit01_write_endpoint_is_reachable_by_non_admin_roles`（Rev.N 新增）以与既有 G5 的行为对照钉住第一级放行清单**。能被测试钉住的约定，就不要靠人去记；③**防复发的可执行做法（Rev.N 补充）**——新增子模块时，用既有同类子模块（如 G5 `index-audit`）做**全仓库 grep 对照**，逐处确认登记面一致，而不是依赖设计文档里的清单 |
| KL-16（Rev.H 新增） | 父表推导对**前缀嵌套**敏感 | 内网真实数据里 `cus_pub_updatelog` 与 `cus_pub_updatelog_detail` **两者都是父表**且前者是后者的前缀（T17）。父表推导若写成任何"取最短前缀"的近似做法，`_detail` 的 13 张子表会被算到 `cus_pub_updatelog` 头上，UAT 的 215/78 变成 228/65——**数字错了，而且错得很像对的** | `_SUBPARTITION_RE` 使用**非贪婪** `^(?P<parent>.+?)_tdsql_subp\d+$`，与内网 SQL 侧 `SUBSTRING_INDEX(...,'_tdsql_subp',1)` 口径逐字一致；已用 78 个真实表名逐条验算，并由 `test_r07b/c/d_*` 三项定向测试钉住。**后续任何人改这个正则，先跑这三项** |
| KL-11 | `info` 列内容（shardkey / sub_shardkey / auto_increment）本期未使用 | 形态已入附录 B | 为将来"分片键分布"类需求预留，不在本期范围 |
| KL-12（Rev.F 新增） | 迁移文件发布后**内容冻结**，改一个字符都会让已部署实例启动失败关闭 | v1.6.2.2 的 O-30 调和账本机制（§2.7 M-3） | 表结构须在打包前定稿；发布后扩列走新增 `111_*.sql`（ADR-18）。**这是全项目所有新增迁移文件的共性约束，不是 G14 特有** |
| KL-13（Rev.G 新增） | 结构验收在**首次调用时**而非启动期，故"表被删/结构漂移"要等到有人点统计才暴露 | ADR-20 的取舍：不让诊断子模块的表问题阻断平台启动 | 报错消息带可执行处置步骤；`/run` 在**采集之前**就失败，用户等待 < 1 秒。若将来该模块被接入定时任务（OUT-2 目前明确不做），需要重新评估是否加启动期探测 |
| KL-14（Rev.G 新增） | 并发配额与既有扫描**共享**，重度扫描期间统计可能被 429 | ADR-19 的取舍：共享配额才能真正保护既有功能 | 提示文案已写明"请稍后重试"；配额可由 `SQLCHECK_MAX_CONCURRENT_SCANS_*` 调整。**不为本模块单开配额**——单开等于全局上限失效 |
| ~~KL-15~~（Rev.G 新增，**Rev.J 关闭**） | ~~`_ensure_schema` 不校验长度/精度~~ | 当时的免责理由是"`COLUMN_TYPE` 带显示宽度、跨发行版不一致"——**那个理由只对整型成立，对 varchar 长度不成立**，我把两件事混为一谈了 | **本项关闭**。Rev.J 按字段分工取值：整型比 `DATA_TYPE`（无显示宽度），字符列比 `CHARACTER_MAXIMUM_LENGTH` + `CHARACTER_SET_NAME`，共通比 `IS_NULLABLE` / `COLUMN_DEFAULT` / `EXTRA`，索引比全列序与唯一性（ADR-20）。**"已知"不等于"已关闭"**——这是 O 的原话，我接受 |
| KL-18（Rev.J 新增） | 结构验收不校验排序规则（collation）与外键 | 排序规则错误不会导致 INSERT 失败，只影响排序与比较；本模块两张表无外键 | 有意留白：验收**过严**会把功能整个打死（干净安装通不过验收 = 页面永远不可用），比过松更难被发现。只钉住"错了就会写不进去或算错"的属性 |
| KL-19（Rev.J 新增，**Rev.L 最终更正，Rev.M 补横向口径**） | 180 秒只是**软预算检查点**；目标采集阶段与 `/run` 端到端都**没有可证明的硬墙钟上界** | Rev.K 把 PyMySQL `read_timeout=30` 当成"整条 SQL 最多 30s"，因而推导 215s，这个前提不成立：它在 `_read_bytes` 中每次底层读取前重设 socket timeout，持续有分片数据到达时整条语句可超过 30s。真实池还有复用连接 `ping()`、失败建连、`select_db`、异常后重建的组合路径，旧的 35s 尾部公式无法覆盖。第五轮横向核查确认：既有 `scan_service` 与深度诊断服务同样没有可证明的墙钟硬上界；G14 另有 180s 软预算与显式 30s 读取超时，标准不低于既有模块，该风险不是 G14 特有 | 删除 `MAX_COLLECT_WALL_SECONDS`。保留阶段/库/命令前的 monotonic checkpoint，只承诺"到期后不再主动启动新的目标 I/O"。T4-R02 用真实连接池控制流证明旧公式可被突破，T4-R03 钉住 PyMySQL 每次读取语义；长尾不可接受时升版异步任务。若平台未来统一引入采集看门狗，G14 与既有扫描/巡检一并接入 |
| KL-20（Rev.K 新增） | 元数据库的结构验收与落库不在任何超时控制内 | 它打的是本地元数据库；落库发生在采集完成之后，为它设 deadline 不可执行——超时了只能丢弃整轮采集结果，代价远大于慢一点（ADR-24） | 已把明细落库改为批量（`ITEM_INSERT_BATCH=100`，500 库 5 次往返）。若将来元数据库出现远程部署形态，需要重新评估是否给它单独的连接级超时 |

---

## 14. 附录 A · 成品代码（照图施工）

> **验证证据边界（Rev.M）**：第五轮评审中，A 将 Rev.L 的 A.1～A.4 逐字抽出并以 importlib
> 挂载设计模块（**仓库功能代码零改动**），得到 110 collected、连元数据库 109 passed +
> 1 skipped、离线 87 passed + 23 skipped。Rev.M 只恢复可下推的普通 `IN` 并删除
> T4-R01 的 SQL 关键字断言，不增加测试项；本次再做语法、T4-R01 定点行为与全文一致性验证。
> 上述结果都只是设计附录证据，**不能冒充真实仓库模块落盘后的回归**；发布门禁仍以落盘后
> 110 项实际 collect 与执行结果为准。
>
> **Rev.B～Rev.E 相对 Rev.A 的实质变化**（源自四轮内网实测）：
> 1. `_EXACT_NAME_COLS` 首位加入实测确认的列名 `db_table`，`info` 加入排除词；
> 2. `_extract_names` → `_extract_pairs`：解析 **`(库, 表)` 二元组**而不是裸表名，
>    并回报是否含跨库行（RISK-E）；
> 3. 采集从"每库三个集合"改为**全局 `kind_map[(库,表)]`**，按行内库限定名归属、
>    全局去重、逐库反查计数（ADR-11）；
> 4. `COMMAND_READ_TIMEOUT=30` / `TOTAL_BUDGET_SECONDS=180` 双层时长兜底
>    与 `SKIPPED` 状态（ADR-13/14）；OK 包（`Query OK, 0 rows affected`）按 0 张处理；
> 5. 新增 `skipped_databases` 与 `baseline_tables` 字段（ADR-16 双口径并排呈现）；
> 6. `RECON_MISMATCH` 汇总成一条告警（ADR-15）；
> 7. **剔除 `_tdsql_subp<数字>` 二级分区物理子表**并单列 `subpartition_tables`
>    （ADR-17，Rev.E）——剔除后逻辑基线与 Proxy 口径精确相等，
>    交叉校验从"永久亮着的噪声"变回"亮起就有事"的信号。
>
> **Rev.G 相对 Rev.F 的实质变化**（全部源自 O 的评审报告，逐条对应）：
>
> | 变化 | 关闭 |
> |---|---|
> | 删除 `scope_signature` / `scope_decided` 与一切提前停止，**无条件逐库执行** | P1-01 |
> | `run_stats` 进入 `registry.scan_slot(connection_id)`，`ScanBusyError` → 429 | P1-02 |
> | 子表判定改为 `_classify_subpartitions(base, proxy_tables)`：**后缀 + 父表已确认**；集中式传空集，等价于一律不剔除 | P1-03 |
> | `_collect_distributed` 改为**每库一个 `with tmp.get_connection()`**，异常穿出触发连接重建 | P1-04 |
> | 单库三条命令先写 `staged`，全成功才原子合入全局；任一失败整库丢弃 | P1-05 |
> | `PROXY_CMD_FAILED` 汇总为一条，`warnings_json` 改 `MEDIUMTEXT` | P1-07 |
> | 新增 `_ensure_schema()` + `SchemaNotReadyError`，在采集与并发槽位**之前**验收表结构（列 / 类型 / 索引） | P1-08 |
> | `analyze` 校验指定库真实存在；`SHOW DATABASES` 失败一律抛出，删除 `DB_ENUM_FAILED` 降级 | P2-01 |
> | API 接收 `Request`，`operator=request.state.username` | P2-02 |
> | `connection_id` 改必填（`min_length=1`），服务层再校验一次 | P2-03 |
> | 测试从 42 项增至 **67 项**，新增 25 项全部是缺陷定向测试 | §6 全表 |
>
> **P1-06 的 `subtabs` 登记在 A.5.5**；前端历史抽屉与告警展示上限在 A.5.4。
>
> **Rev.J 相对 Rev.I 的实质变化**（全部源自 O 的第二轮评审，逐条对应）：
>
> | 变化 | 关闭 |
> |---|---|
> | 新增 `_NameSpace`；`_collect_baseline` / `_split_qualified` / 目标过滤 / owner 归属四处共用同一实例；指定库只接受精确名；新增 `DB_NAME_CASE_VARIANTS` 与 `DB_NAME_AMBIGUOUS` | P1-01 |
> | deadline 由 `run_stats` 在拿到槽位后建立并贯穿全程；**每条命令**开始前检查；新增 `_BudgetExceeded`、`MAX_WALL_SECONDS=210`、可控时钟钩子 `_now` | P1-02 |
> | 先算 `eligible = 全部 − 失败 − 跳过`，`overlap` / `baseline` / `subp` 一律按 owner 过滤 | P1-03 |
> | `_ensure_schema` 升级为完整字段契约（类型 / 长度 / 字符集 / 可空性 / 默认值 / 自增 + 索引全列序与唯一性），自持 `_norm_default` | P1-04 |
> | 槽位护栏重写：`scan_migration_slots` 返回 `slot → [files]`，`assert_slot_ok` 四条断言**落盘前后都成立** | P1-05 |
> | `_classify_subpartitions` 增加第三条件"候选自身不在 Proxy 结果中" | P2-01 |
> | 历史抽屉告警改为 `computed` + 总数提示 + 展开全部（A.5.4 / A.5.5） | P2-02 |
> | `runTableTypeStats` 按 failed/skipped 分流 success / warning / error 提示 | P2-03 |
> | A.4 模块头如实列出离线 73 / 元数据库 10 / 需落盘 1 | DOC-01 |
>
> **Rev.K 相对 Rev.J 的实质变化**（全部源自 O 的第三轮评审，逐条对应）：
>
> | 变化 | 关闭 |
> |---|---|
> | 目标过滤改精确成员判断（`if qual not in target`），canonical 名不再二次 CI 回退 | P1-01 |
> | 预算耗尽改标志位 + 正常退出 `with`，删除 `_BudgetExceeded`；`resolve()` 后补一次 deadline 检查 | P1-02 |
> | 显式 `CONNECT_TIMEOUT=5`；`MAX_WALL_SECONDS(210)` → `MAX_COLLECT_WALL_SECONDS(215)`，作用域限定为目标采集阶段；明细落库批量化 | P1-02 |
> | `assert_slot_ok` 删除"必须是最大槽"，改钉「槽位不重复 + 独占自己的槽 + 前驱存在」 | P1-03 |
> | 探测与 DROP 共用 `MYSQL_CONFIG`；新增 `assert_destructive_target_is_safe()` 与 `DestructiveTargetError` | P1-04 |
> | 11 条参数化字段契约用例 + 1 条干净 DDL 反向护栏 | P2-01 |
> | 历史抽屉清理数据源而非只读 computed，切行时清空并 loading | P2-02 |
> | 结果提示按**有效库数**分流 success / warning / error | P2-03 |
> | API 头、DDL 头、`run_stats` 注释、前端子分区口径、`/run` 上界说法全部对齐 | DOC-01 |
> | `TimeoutError` 映射 503（暂时性，与 500 语义区分） | Rev.K 自查 |

> **Rev.L 相对 Rev.K 的实质变化**（全部源自 O 的第四轮评审）：
>
> | 变化 | 关闭 |
> |---|---|
> | `_collect_baseline(pool, dbs, known)` 改为 `BINARY TABLE_SCHEMA IN`；用全实例 `known` canonical 解析后对目标库精确过滤 | P1-01 |
> | 删除 `MAX_COLLECT_WALL_SECONDS` 及 215 秒硬上界论证；180 秒明确为"到期后不再启动新目标 I/O"的软预算 | P1-02 |
> | 前端 `database_count===0` 单独弹黄色提示，不再落入绿色成功分支 | P2-01 |
> | `_probe_metadata_db()` 传入 `database` 与 `charset`，真正探测即将被破坏性测试使用的目标库 | P2-02 |
> | 新增 T4-R01/R02/R03/R05 四条离线回归；当前态文档全部收敛到 Rev.L | DOC-01 |

> **Rev.M 相对 Rev.L 的实质变化**（源自 A 的第五轮评审，定点关闭唯一 P2）：
>
> | 变化 | 关闭 |
> |---|---|
> | 基线 SQL 删除 `BINARY`，恢复可下推的 `TABLE_SCHEMA IN (...)`；大小写正确性完全由全实例 canonical 解析 + 目标集精确过滤承担 | P2-01 |
> | T4-R01 删除 `BINARY TABLE_SCHEMA IN` 字符串断言，只保留“服务端多返兄弟库也不得吸收”的行为断言 | P2-01 |
> | §6.4、ADR-27、E-36 同步改为“SQL 不做大小写假设”；§10.3 / §12.4 新增最大内网实例 T20 性能证据门禁 | P2-01 |
> | KL-19 补充与既有扫描/巡检的横向口径；第五轮 110 项抽取实测结果按设计证据回填 | 非阻断建议 / 证据边界 |

> **Rev.N 相对 Rev.M 的实质变化**（源自 A 第一轮 SIT 报告，1 BLOCK + 2 MINOR 全部关闭）：
>
> | 变化 | 关闭 |
> |---|---|
> | `auth_service.py::_OPERATIONAL_WRITE_PREFIXES` 补登记 `/api/v1/table-type-stats/`（带尾斜杠）；§2.2 登记点由 6 处改为 7 处（新增 P7），ADR-21 / §12.2 / KL-17 同步；新增行为级用例 `test_sit01_*`（与既有 G5 对照，不硬编码死值） | **DEF-SIT-01（BLOCK）**：developer 与全部自定义角色写端点恒 403 |
> | §5 错误表与 §8 E-26 修正：空串/缺字段为 422（FastAPI 请求体校验标准语义），全空白为 400；实现不动（`min_length=1` 合理） | **DEF-SIT-02（MINOR）**：契约文档与实现不符 |
> | 附录 A.2 `run()` 的空白校验提前到 `_pool()` 连接解析之前；服务层同名守卫保留为直接调用兜底；新增 `test_sit03_*` 直接打 API 层覆盖真实调用顺序 | **DEF-SIT-03（MINOR）**：报错文案误导、服务层守卫经 HTTP 不可达 |
> | collect 110 → **112**（新增 2 项均为纯离线） | 测试加固 |

### A.1 `backend/services/table_type_stats_service.py`（新增，完整成品）

```python
# -*- coding: utf-8 -*-
"""G14 · 表类型统计（深度诊断子模块，DESIGN-v1.6.3.0 Rev.M）

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

Rev.M（A 第五轮评审定点整改）要点：
  · 基线 SQL 使用可下推的普通 TABLE_SCHEMA IN；不假定服务端大小写语义，
    正确性完全由全实例 known canonical 解析 + 目标集合精确过滤承担（P2-01）。
  · T4-R01 只验证服务端多返 Sales/sales 时的结果行为，不固化 SQL 关键字；
    最大内网实例须按 T20 留存普通 IN 与 BINARY IN 的 EXPLAIN/耗时对比。

Rev.L（O 第四轮评审整改）要点：
  · 基线查询增加应用层防御：用全实例 known 解析 canonical 后对目标库精确过滤；
    Rev.M 进一步确认 SQL 无需也不得依赖 BINARY（P1-01 / Rev.M P2-01）。
  · 删除 215 秒硬上界常量与论证。PyMySQL read_timeout 是每次底层读取超时，
    不是整条 SQL 总时间；180 秒只承诺服务层在 checkpoint 后不新开阶段/库/命令（P1-02）。

Rev.K（O 第三轮评审整改）要点：
  · 目标库过滤改为**精确成员判断**：canonical name 不得再做第二次大小写回退，
    否则指定单库时会把大小写兄弟库的行并进来（P1-01）。
  · 预算耗尽改为**标志位 + 正常退出 with**，绝不抛异常穿出连接上下文
    ——那会让一条健康连接被连接池销毁重建，并可能把 SKIPPED 误标成 FAILED（P1-02）。
  · 当时曾将目标采集上界更正为 215 秒；**Rev.L 已证明该硬上界仍不成立并删除**。
  · 明细落库改为批量（500 库 500 次往返 → 5 次）（P1-02）。
  · resolve() 之后补一次 deadline 检查——它在缓存未命中时会向目标实例发探测 SQL。

Rev.J（O 第二轮评审整改）要点：
  · 库名不再无条件小写：`Sales` 与 `sales` 在 lower_case_table_names=0 下是
    两个不同 schema，单值 lower 字典会把它们合并成一个（P1-01）。改用 _NameSpace：
    精确优先、CI 回退仅在候选唯一时生效、歧义显式报 DB_NAME_AMBIGUOUS。
  · 180 秒预算由 run_stats 统一建立并贯穿 SHOW DATABASES / 基线查询 / 每一条
    Proxy 命令；其语义以 Rev.L 的软预算为准。
  · 失败/跳过库的基线、子分区、重叠一律不进实例级汇总——先定状态再算汇总（P1-03）。
  · 结构验收升级为完整字段契约：类型 + 长度 + 字符集 + 可空性 + 默认值 + 自增
    + 索引全列序与唯一性（P1-04）。
  · 物理子表判定增加第三个条件：候选自身不得出现在 Proxy 结果中（P2-01）。

Rev.G（O 首轮评审整改）要点：
  · 取消"指纹相同即提前停止"——两库指纹相同只证明结果与当前默认库无关，
    不证明已覆盖全部目标库（P1-01）。改为无条件逐库执行，正确性优先。
  · 二级分区物理子表的识别：仅对分布式生效，且要求"逻辑父表确实出现在
    Proxy 结果里"才判定为子表（P1-03）。集中式一律不剔除。
  · 每库一个连接上下文，异常穿出 with 触发连接池重建，避免坏连接被后续库复用（P1-04）。
  · 单库三条命令暂存后原子合入全局，任一失败即整库丢弃（P1-05）。
  · run_stats 进入既有 registry.scan_slot(connection_id) 并发槽位，与 SQL 审核/
    慢查询扫描共用同一套按连接 + 全局的限流，超限抛 ScanBusyError → 429（P1-02）。
  · run_stats 入口做落库表结构验收（列 / 类型 / 索引），
    避免采集完才在 INSERT 处失败（P1-08）。
  · 指定库必须真实存在（P2-01）；connection_id 必须非空（P2-03）。

设计要点（详见 DESIGN-v1.6.3.0）：
  · 结果按【库限定名】归属到库，而不是无条件算在当前会话库上——
    命令的作用域是否为实例级尚未确证，按库归属 + (库,表) 去重使两种
    作用域都得到正确结果（§3.3 RISK-E）。这也正是原厂"使用数据库名+表名
    去重"这句话的由来。
  · 基线口径：剔除二级分区物理子表后与 Proxy 口径精确对齐，使交叉校验重新成为
    有效信号（否则每个库都会常态告警 27%，等于把告警训练成噪声）。
  · 软时长预算 + 显式读空闲超时：到期后不再开新操作，连接持续无数据时可退出；
    二者都不是整个请求的硬墙钟上界（§3.3 RISK-F，KL-19）。
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

from backend.services.connection_registry import registry
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
COMMAND_READ_TIMEOUT = 30     # 临时池 socket 每次读取的空闲超时（非整条 SQL 总时间）
CONNECT_TIMEOUT = 5           # 临时池单次建连超时（秒）

# 采集软预算：服务层在 checkpoint 后不再启动新阶段/库/命令，未采完的库标 SKIPPED。
# 它是"不再开新操作"的截止线，不是墙钟上界。
#
# Rev.L / P1-02：这是**软 deadline**。在实例类型探测、库枚举、基线查询、
# 进入某库的连接上下文前、拿到连接后/切库前、每条 Proxy 命令前检查；
# checkpoint 到期后服务层不再新开阶段/库/命令。已进入的池/驱动内部步骤可继续。
# 它不是墙钟硬上界：PyMySQL read_timeout 是每次底层 socket 读取的空闲超时，
# 不是整条 SQL 总时间；连接池还可经历 ping / 建连 / select_db / 异常重建组合路径。
# `_ensure_schema()` 与结果落库也不在该 deadline 内。故不定义任何 MAX_*WALL* 常量。
TOTAL_BUDGET_SECONDS = 180
ITEM_INSERT_BATCH = 100       # 明细落库批量大小。500 库从 500 次往返降到 5 次

# 表名列识别规则（§6.3）。自上而下，命中即停。
# db_table 为 2026-08-29 内网实测确认的真实列名（附录 B）。
_EXACT_NAME_COLS = ("db_table", "table", "table_name", "tables", "name")
_PREFIX_NAME_COLS = ("tables_in_",)
_EXCLUDE_TOKENS = ("type", "rows", "schema", "comment", "engine", "key", "info")

# TDSQL 二级分区的物理子表命名（2026-08-29 内网实测，设计附录 B.5）：
#   cus_pub_translog_tdsql_subp190001 / _tdsql_subp202601 … _tdsql_subp202612
# 它们在 information_schema 里是独立的 BASE TABLE，但【不是】用户认知中的"表"，
# Proxy 的 show table 也只返回逻辑表名。故从逻辑基线中剔除并单列计数。
#
# Rev.G（P1-03）：命名匹配【只是必要条件，不是充分条件】。
#   · 集中式实例根本没有二级分区物理子表这一构造 —— 一律不剔除，
#     否则一张合法业务表 orders_tdsql_subp202601 会被静默少算，且集中式
#     没有 Proxy 交叉校验兜底，错误不可见（违反 REQ-5）。
#   · 分布式实例额外要求【逻辑父表确实出现在本库的 Proxy 结果中】才判定为子表。
#     父表 = 表名去掉 _tdsql_subp<数字> 后缀的部分。
#     实测校验：cus_pub_translog_tdsql_subp202601 → 父表 cus_pub_translog
#     确在 show table with shardkey 的 98 行内。
#   · 父表不存在时保留为逻辑表 —— 后果是 RECON_MISMATCH 显式报出（可见），
#     而不是静默少算（不可见）。方向是安全的。
_SUBPARTITION_RE = re.compile(r"^(?P<parent>.+?)_tdsql_subp\d+$", re.IGNORECASE)

_PERM_ERRNO = (1044, 1045, 1142, 1143, 1227)
_SYNTAX_ERRNO = 1064

# 可测性钩子：单测用 monkeypatch 注入 FakePool（§11）。生产恒为真实连接池。
_new_pool = TDSQLConnectionPool
# 可测性钩子（Rev.J / P1-02）：单测用可控时钟驱动 deadline，
# 让"命令各耗多少秒、什么时候不再启动新命令"成为可断言的事实，而不是靠 sleep 碰运气。
# 生产恒为 time.monotonic。第二处、也是最后一处为可测性做的让步，成本 1 行。
_now = time.monotonic


# ══════════════════════════════════════════════════════════════════
# 小工具
# ══════════════════════════════════════════════════════════════════
class _NameSpace:
    """库名命名空间：精确优先，大小写不敏感回退【仅在候选唯一时】生效。

    Rev.J / P1-01：Rev.I 用的是 `{name.lower(): name}` 单值字典。
    在 `lower_case_table_names=0` 的 Linux/TDSQL 上，`Sales` 与 `sales` 是
    两个不同的 schema——单值字典会让后者覆盖前者，于是：
      · 两个库的 information_schema 基线被归进同一个键；
      · Proxy 行被归给字典里最后那个库；
      · 另一个库显示 0，或者产生虚假的 RECON_MISMATCH。
    这是四个主数字的**静默**正确性问题，不能靠告警兜底。

    本类把"一对多"如实表示出来：
      resolve("sales")  → 精确命中 → "sales"
      resolve("SALES")  → 无精确命中；小写候选若唯一则用之，若有多个 → None（歧义）
    歧义返回 None，由调用方显式记一条 DB_NAME_AMBIGUOUS 告警——
    **把不可判定的情况变成可见的，而不是替使用者猜一个。**
    """

    __slots__ = ("_exact", "_ci")

    def __init__(self, names):
        self._exact = set()
        self._ci = {}
        for n in names:
            n = str(n)
            self._exact.add(n)
            self._ci.setdefault(n.lower(), []).append(n)

    def __contains__(self, name) -> bool:
        return str(name) in self._exact

    def __len__(self) -> int:
        return len(self._exact)

    def resolve(self, name):
        """返回 canonical 名；无法唯一确定时返回 None。"""
        s = str(name)
        if s in self._exact:
            return s
        cands = self._ci.get(s.lower(), ())
        return cands[0] if len(cands) == 1 else None

    def is_ambiguous(self, name) -> bool:
        """名字本身不精确存在，且大小写候选有多个。"""
        s = str(name)
        return s not in self._exact and len(self._ci.get(s.lower(), ())) > 1

    def variants(self, name) -> list:
        """与 name 仅大小写不同的全部真实库名（含自身）。"""
        return sorted(self._ci.get(str(name).lower(), ()))


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


def _split_qualified(raw, current_db: str, known: "_NameSpace"):
    """把 db_table 值拆成 (库名, 表名, 是否歧义)。

    实测形态为 `sqltuning.t_max`。仅当点号左侧确为一个【已知库名】时才拆分，
    否则整体视为当前库下的表名——避免把含点号的表名误拆后被过滤掉（少算）。

    Rev.J / P1-01：库名比对交给 _NameSpace——精确优先，大小写回退仅在候选唯一时生效。
    左侧命中了某个库名的小写形式、但真实存在多个大小写变体时，**无法判定归属**，
    此时回 (current_db, 原串, True)：不猜、不丢，由调用方记 DB_NAME_AMBIGUOUS。
    """
    s = str(raw if raw is not None else "").strip()
    if not s:
        return current_db, "", False
    if "." in s:
        head, tail = s.split(".", 1)
        head = head.strip().strip("`").strip()
        tail = tail.strip().strip("`").strip()
        if head and tail:
            owner = known.resolve(head)
            if owner is not None:
                return owner, tail, False
            if known.is_ambiguous(head):
                return current_db, s.strip("`").strip(), True
    return current_db, s.strip("`").strip(), False


def _extract_pairs(rows, current_db: str, known: "_NameSpace"):
    """提取 {(库名, 表名)}。

    返回 (集合, 实际列名, 形态是否未知, 是否含跨库行, 歧义库名样本)。
    """
    rows = rows or []
    if not rows:
        return set(), [], False, False, set()
    first = rows[0]
    if isinstance(first, dict):
        columns = list(first.keys())
        col, guessed = _pick_name_column(columns)
        values = [r.get(col) if isinstance(r, dict) else None for r in rows]
    else:
        columns, guessed = [], False
        values = [(r[0] if r else None) for r in rows]
    pairs, cross, ambiguous = set(), False, set()
    for v in values:
        qual, name, amb = _split_qualified(v, current_db, known)
        if not name:
            continue
        if amb:
            ambiguous.add(str(v))
            continue          # 归属不可判定：不猜、不误计，由告警显式报出
        pairs.add((qual, name))
        if qual != current_db:
            cross = True
    return pairs, [str(c) for c in columns], guessed, cross, ambiguous


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


def _collect_baseline(pool, dbs: list, known: "_NameSpace") -> dict:
    """取 information_schema 全量名单。

    返回 {db: {"base": 全部 BASE TABLE, "view": 视图}}。
    Rev.G（P1-03）：**不在此处剔除二级分区子表**——是否为子表要等 Proxy 结果回来后
    结合"逻辑父表是否存在"才能判定，且集中式一律不剔除。分类下沉到 _classify_subpartitions。
    """
    out = {d: {"base": set(), "view": set()} for d in dbs}
    if not dbs:
        return out
    placeholders = ",".join(["%s"] * len(dbs))
    rows = pool._execute(
        "SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE "
        "FROM information_schema.TABLES "
        f"WHERE TABLE_SCHEMA IN ({placeholders})", tuple(dbs)) or []
    # Rev.M / P2-01：普通 IN 只做参数化候选集查询并保留谓词下推，
    # 不假定服务端大小写语义。正确性完全由应用层承担：先在【全实例命名空间】
    # 解析 canonical 库名，再对目标子集精确过滤；不得在目标子集上再做 CI 回退。
    target = _NameSpace(dbs)  # 只使用 __contains__ 的精确成员语义，不调 resolve()
    for r in rows:
        if not isinstance(r, dict):
            continue
        schema = str(r.get("TABLE_SCHEMA") or r.get("table_schema") or "").strip()
        owner = known.resolve(schema)
        if owner is None or owner not in target:
            continue
        name = str(r.get("TABLE_NAME") or r.get("table_name") or "").strip()
        if not name:
            continue
        ttype = str(r.get("TABLE_TYPE") or r.get("table_type") or "").strip().upper()
        if ttype == "BASE TABLE":
            out[owner]["base"].add(name)
        elif ttype == "VIEW":
            out[owner]["view"].add(name)
    return out


def _classify_subpartitions(base: set, proxy_tables: set) -> tuple:
    """把 BASE TABLE 名单拆成 (逻辑表, 二级分区物理子表)。

    判定为子表需【同时】满足三条：
      1) 名字匹配 `<父表>_tdsql_subp<数字>`；
      2) 父表确实出现在本库的 Proxy 结果里（Rev.G / P1-03）；
      3) **候选自身不在 Proxy 结果里**（Rev.J / P2-01）。

    第 3 条是 Rev.J 补的，理由很硬：**真正的物理子表根本不会被
    `/*proxy*/show table` 返回**——Proxy 只认逻辑表。所以"它自己也在 Proxy 结果里"
    就直接证伪了"它是物理子表"。少了这一条，一对合法的业务表
    `orders` 与 `orders_tdsql_subp202601`（两者都是真逻辑表、都在 Proxy 结果中）
    会让后者被错判成子分区：页面的「二级分区子表」多算一张，
    逻辑基线少算一张，还会凭空冒出一条"仅 Proxy 可见"的 RECON_MISMATCH。

    只满足 1) 不满足 2)/3) 的表一律保留为逻辑表——宁可让 RECON_MISMATCH
    把它显式报出来，也不静默少算。集中式分支传入 proxy_tables=空集，
    第 2 条恒不成立，等价于"一律不剔除"。
    """
    subp = set()
    for name in base:
        if name in proxy_tables:
            continue                                  # 条件 3：Proxy 认它是逻辑表
        m = _SUBPARTITION_RE.match(name)
        if m and m.group("parent") in proxy_tables:    # 条件 1 + 2
            subp.add(name)
    return base - subp, subp


def _blank_item(db: str) -> dict:
    return {"db_name": db, "total_tables": 0, "shard_tables": 0,
            "broadcast_tables": 0, "single_tables": 0,
            "baseline_tables": 0, "subpartition_tables": 0,
            "status": "OK", "detail": ""}


def _collect_centralized(dbs: list, baseline: dict):
    """集中式：纯内存换算，不发任何查询、不发任何 /*proxy*/ 命令（ADR-4）。

    Rev.G（P1-03）：**不剔除任何 _tdsql_subp 表**——集中式没有二级分区物理子表
    这一构造，剔除只会把合法业务表静默少算，且此分支没有 Proxy 交叉校验兜底。
    """
    items = []
    totals = {"shard": 0, "broadcast": 0, "single": 0, "total": 0,
              "baseline": 0, "subp": 0, "overlap": 0, "failed": 0, "skipped": 0}
    for db in dbs:
        base = baseline.get(db, {}).get("base", set())
        n = len(base)
        item = _blank_item(db)
        item["total_tables"] = n
        item["single_tables"] = n
        item["baseline_tables"] = n
        items.append(item)
        totals["single"] += n
        totals["total"] += n
        totals["baseline"] += n
    return items, [], {}, totals


def _collect_distributed(pool, dbs: list, baseline: dict, known: "_NameSpace",
                        deadline: float):
    """分布式：逐业务库执行三条 /*proxy*/ 命令，按【库限定名】归属去重。

    Rev.G 的三处结构性变化（保留）：
      P1-01  取消"指纹相同即提前停止"——两库指纹相同只证明结果与当前默认库无关，
             不能证明已覆盖全部目标库。改为**无条件逐库执行**。
      P1-04  **每库一个连接上下文**。异常一律穿出 with，由
             TDSQLConnectionPool.get_connection() 关闭并重建线程本地连接后再抛出，
             外层逐库捕获后继续下一库——坏连接不会被后续库复用。
      P1-05  单库三条命令先写入**暂存区**，三条全部成功才原子合入全局。

    Rev.J 新增：
      P1-01  库名归属交给 _NameSpace（精确优先 / CI 回退仅在唯一时），
             歧义行不猜归属，记 DB_NAME_AMBIGUOUS。
      P1-02  deadline 由 analyze 统一建立并传入，**拿到连接后/切库前**
             与**每条命令开始前**都检查，
             不再是"每库检查一次"——把最坏超出从 3×30s 压到 1×30s。
      P1-03  失败/跳过库的**任何**数据都不进实例级汇总（含基线、子分区、重叠）。
    """
    items, warnings, shape = [], [], {}
    totals = {"shard": 0, "broadcast": 0, "single": 0, "total": 0,
              "baseline": 0, "subp": 0, "overlap": 0, "failed": 0, "skipped": 0}
    if not dbs:
        return items, warnings, shape, totals

    target = _NameSpace(dbs)
    kind_map = {}          # (db, table) -> kind
    kinds_seen = {}        # (db, table) -> {kind, ...}
    failed, skipped = {}, {}
    shape_reported = False
    instance_wide = False
    syntax_errors = 0
    scanned = 0
    ambiguous_samples = set()

    cfg = dataclasses.replace(pool.config, database=dbs[0],
                              read_timeout=COMMAND_READ_TIMEOUT,
                              connect_timeout=CONNECT_TIMEOUT)
    tmp = _new_pool(cfg, pool_size=1)
    try:
        for db in dbs:
            if _now() >= deadline:
                skipped[db] = "budget"
                continue
            scanned += 1
            detail = ""
            staged = {}          # kind -> {(库, 表)}
            staged_cols = {}     # kind -> 实际列名
            staged_guessed = False
            staged_cross = False
            staged_amb = set()
            budget_hit = False
            try:
                # P1-04：每库独立上下文；【真实的】异常穿出即触发连接重建
                with tmp.get_connection() as conn:
                    # Rev.L / P1-02：get_connection() 内部可先 ping，ping 失败后再建连。
                    # 这串已启动的内部步骤可能跨过 deadline；拿到连接后必须再查一次，
                    # 否则会在 deadline 之后还新发 select_db。只置标志以正常退出 with。
                    if _now() >= deadline:
                        budget_hit = True
                    else:
                        conn.select_db(db)
                    for kind, sql in (() if budget_hit else _KIND_SQL):
                        # P1-02：每条命令开始前再查一次 deadline。
                        # 单库三条命令最坏 3×30s=90s，只在库开始时查一次的话，
                        # 179 秒进库就能跑到 269 秒——那个"180 秒"的承诺不成立。
                        #
                        # Rev.K / P1-02：这里【只置标志、正常退出 with】，
                        # 绝不抛异常。TDSQLConnectionPool.get_connection() 会捕获
                        # 穿出上下文的**任何**异常并关闭 + 重建连接
                        # （tdsql_connector.py:287-307）——用它承载"预算耗尽"这种
                        # 正常控制信号，等于让一条健康连接在 deadline 之后被销毁重建，
                        # 白白多付一次 connect_timeout；重连再失败的话，新异常还会
                        # 盖掉原信号，把本该 SKIPPED 的库误标成 FAILED。
                        # **连接重建只应由真实的数据库/网络异常触发。**
                        if _now() >= deadline:
                            budget_hit = True
                            break
                        with conn.cursor() as cur:
                            cur.execute(sql)
                            rows = cur.fetchall()
                        # rows 可能是 OK 包（某类为空时 TDSQL 返回
                        # `Query OK, 0 rows affected`）——此时 fetchall() 为 []
                        # 且无列元数据，_extract_pairs 按 0 张处理，不是错误。
                        pairs, columns, guessed, cross, amb = _extract_pairs(
                            rows, db, known)
                        staged[kind] = pairs
                        if columns:
                            staged_cols[kind] = columns
                        staged_guessed = staged_guessed or guessed
                        staged_cross = staged_cross or cross
                        staged_amb |= amb
            except Exception as e:                           # noqa: BLE001
                detail = f"{db} 采集失败: {_err(e)}"
                if _errno_of(e) == _SYNTAX_ERRNO:
                    syntax_errors += 1
            if budget_hit:
                # 预算在本库中途耗尽：本库【未采完】，按 SKIPPED 处理而不是 FAILED
                # ——"没来得及测"与"测了但错了"处置动作不同（ADR-14）。
                # 暂存区整体丢弃：半个库的数据不是"部分成功"，是错的。
                skipped[db] = "budget"
                continue
            if detail:
                # P1-05：整库丢弃暂存区，绝不半量合入
                failed[db] = detail[:512]
                continue

            # ── 三条全成功，原子合入全局 ──────────────────────────
            for kind, cols in staged_cols.items():
                shape.setdefault(kind, cols)
            if staged_cross:
                instance_wide = True
            ambiguous_samples |= staged_amb
            if staged_guessed and not shape_reported:
                shape_reported = True
                warnings.append(_warn(
                    "SHAPE_UNKNOWN", "WARNING", db,
                    f"未能识别表名列，已退化为取第一列；实际列名: {staged_cols}"))
            for kind, _sql in _KIND_SQL:
                for qual, name in staged.get(kind, ()):
                    # Rev.K / P1-01：qual 已由 _extract_pairs 用【全实例命名空间】
                    # known 解析成 canonical name，这里必须**精确成员判断**。
                    # Rev.J 在这里又做了一次 target.resolve()——目标子集只含一个库时，
                    # CI 回退会把另一个真实库的行"唯一命中"到目标库上：
                    # 指定 database="Sales" 时，实例级返回里的 sales.t_lower
                    # 会被算进 Sales。**canonical name 不得再做第二次大小写回退。**
                    if qual not in target:
                        continue              # 非目标库（系统库 / 被 database 筛掉的库）
                    owner = qual
                    if name in baseline.get(owner, {}).get("view", ()):
                        continue              # 原厂口径：不统计视图
                    key = (owner, name)
                    kinds_seen.setdefault(key, set()).add(kind)
                    cur_kind = kind_map.get(key)
                    if (cur_kind is None
                            or _KIND_PRIORITY[kind] < _KIND_PRIORITY[cur_kind]):
                        kind_map[key] = kind
    finally:
        try:
            tmp.close_all()
        except Exception:                                    # noqa: BLE001
            logger.debug("临时连接池关闭失败（忽略）", exc_info=True)

    # ── 组装逐库结果 ──────────────────────────────────────────────
    #
    # Rev.J / P1-03：**先定状态，再算汇总。**
    # Rev.I 把 baseline / subp 的累加写在状态判断【之前】，overlap 又是对整个
    # kinds_seen 求和——于是：
    #   · 失败库的基线、子分区照样进实例级汇总；
    #   · 某个成功库的实例级返回里携带的失败库行，会让 overlap_count 被加 1；
    #   · 极端情况（全部 SKIPPED）下 total_tables=0 而 baseline_tables 仍是全库合计。
    # 这与接口文案和 W1 告警里"未计入任何汇总数"直接矛盾。
    # 现在先算出 eligible（既未失败也未跳过的库），所有实例级汇总一律按 owner 过滤。
    eligible = _NameSpace([d for d in dbs if d not in failed and d not in skipped])
    per_db = {d: {KIND_SHARD: 0, KIND_BROADCAST: 0, KIND_SINGLE: 0} for d in dbs}
    for (d, _t), kind in kind_map.items():
        per_db[d][kind] += 1
    overlap_total = sum(len(v) - 1 for k, v in kinds_seen.items()
                        if len(v) > 1 and k[0] in eligible)
    recon = []                 # [(db, 仅Proxy可见数, 仅基线可见数)]

    for db in dbs:
        item = _blank_item(db)
        proxy_tables = {t for (d, t) in kind_map if d == db}
        raw_base = baseline.get(db, {}).get("base", set())
        # P1-03（首轮）：结合 Proxy 结果做子表判定（父表必须在 Proxy 结果里）
        logical_base, subp = _classify_subpartitions(raw_base, proxy_tables)
        # 逐库行如实显示基线与子分区（information_schema 那一侧确实查成功了），
        # 但**只有 eligible 库计入实例级汇总**，否则 total 与 baseline 覆盖的
        # 库集合不同，两个数并排放就失去了互相印证的意义。
        item["baseline_tables"] = len(logical_base)
        item["subpartition_tables"] = len(subp)

        if db in failed:
            item["status"] = "FAILED"
            item["detail"] = failed[db]
            totals["failed"] += 1
            items.append(item)
            continue
        if skipped.get(db) == "budget":
            item["status"] = "SKIPPED"
            item["detail"] = f"超出总时长预算 {TOTAL_BUDGET_SECONDS}s，未采集"
            totals["skipped"] += 1
            items.append(item)
            continue

        totals["baseline"] += len(logical_base)
        totals["subp"] += len(subp)
        c = per_db[db]
        item["shard_tables"] = c[KIND_SHARD]
        item["broadcast_tables"] = c[KIND_BROADCAST]
        item["single_tables"] = c[KIND_SINGLE]
        item["total_tables"] = c[KIND_SHARD] + c[KIND_BROADCAST] + c[KIND_SINGLE]
        totals["shard"] += item["shard_tables"]
        totals["broadcast"] += item["broadcast_tables"]
        totals["single"] += item["single_tables"]
        totals["total"] += item["total_tables"]

        only_proxy, only_base = proxy_tables - logical_base, logical_base - proxy_tables
        if only_proxy or only_base:
            recon.append((db, len(only_proxy), len(only_base)))
            d2 = (f"Proxy 口径 {len(proxy_tables)} 张，information_schema 逻辑基线 "
                  f"{len(logical_base)} 张")
            if only_base:
                d2 += f"；仅基线可见({len(only_base)}): {_diff_sample(only_base)}"
            if only_proxy:
                d2 += f"；仅 Proxy 可见({len(only_proxy)}): {_diff_sample(only_proxy)}"
            item["detail"] = d2[:512]
        items.append(item)

    totals["overlap"] = overlap_total
    if failed:
        # P1-07：失败库汇总为一条告警，逐库详情留在 item.detail，
        # 避免 500 库全失败时 warnings_json 撑爆存储、前端渲染数百条横幅
        names = ", ".join(sorted(failed)[:5])
        if len(failed) > 5:
            names += f" …等 {len(failed)} 个库"
        warnings.append(_warn(
            "PROXY_CMD_FAILED", "ERROR", "",
            f"{len(failed)} 个库采集失败，未计入任何汇总数（{names}）；"
            f"逐库失败原因见各行「说明」"))
    if ambiguous_samples:
        warnings.append(_warn(
            "DB_NAME_AMBIGUOUS", "ERROR", "",
            f"{len(ambiguous_samples)} 行的库限定名无法唯一归属"
            f"（实例上存在仅大小写不同的同名库）：{_diff_sample(ambiguous_samples)}。"
            f"这些行**未计入任何库**——归属靠猜会把两个真实的库算成一个，"
            f"宁可少算并显式报出。请用「库名」输入框逐库精确统计"))
    if overlap_total:
        warnings.append(_warn(
            "KIND_OVERLAP", "WARNING", "",
            f"三类结果集存在 {overlap_total} 处重叠，"
            f"已按 分片>广播>单表 归一化去重，总数未重复计算"))
    if recon:
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
            f"（形如 xxx_tdsql_subp202601，且其逻辑父表确在 Proxy 结果中），"
            f"按逻辑表口径未计入总数；逐库数量见「二级分区子表」列"))
    if instance_wide:
        warnings.append(_warn(
            "INSTANCE_WIDE_SCOPE", "INFO", "",
            f"本版本 /*proxy*/show table 返回实例级全量（结果含跨库行），"
            f"已按库限定名归属并按(库,表)去重；"
            f"为保证覆盖完整性，仍逐库执行（Rev.G / P1-01）"))
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
def analyze(pool, connection_id: str = "", database: str = "",
            deadline: float = None) -> dict:
    """执行一次统计（只读，不落库）。

    Rev.J / P1-02：`deadline` 是**整个目标采集过程**的统一 monotonic 检查点，
    由 run_stats 在拿到并发槽位之后、任何目标实例查询之前建立。
    Rev.L / P1-02：它是软预算，只决定是否启动下一个 I/O，不强制中断已启动 I/O。
    Rev.I 的计时器直到 `_collect_distributed` 内部才启动，
    `SHOW DATABASES` 与全量基线查询（设计自己承认这才是耗时大头）根本不在预算内。
    """
    from backend.models import InstanceType
    from backend.services.instance_type_service import instance_type_service

    if deadline is None:
        deadline = _now() + TOTAL_BUDGET_SECONDS

    # Rev.K：resolve() 在缓存未命中时会向目标实例发探测 SQL
    # （instance_type_service._probe_and_persist），是本流程第一段真实的目标实例 I/O。
    # 它同样消耗 deadline——deadline 是绝对时刻，故无需在它之前额外检查；
    # 但它之后必须检查，否则一次慢探测会让后续阶段在预算外继续开工。
    ctx = instance_type_service.resolve(connection_id)
    is_dist = ctx.instance_type == InstanceType.DISTRIBUTED
    source = getattr(ctx.source, "value", str(ctx.source))

    warnings = []
    if _now() >= deadline:
        raise TimeoutError(
            f"采集总时长预算 {TOTAL_BUDGET_SECONDS}s 在实例类型探测阶段即已耗尽")
    # Rev.J / P1-02：库枚举也在预算内。它走共享池（read_timeout 默认 10s）。
    if _now() >= deadline:
        raise TimeoutError(
            f"采集总时长预算 {TOTAL_BUDGET_SECONDS}s 在枚举数据库前即已耗尽")
    # Rev.G / P2-01：库枚举失败不得静默按空库处理——集中式分支查不到行与库不存在
    # 在结果上无法区分，会得到"状态 OK、总数 0"的假成功。枚举失败一律抛出。
    business, truncated, allnames = list_business_databases(pool)
    known = _NameSpace(allnames)
    if database:
        # Rev.G / P2-01 + Rev.J / P1-01：指定库必须【精确】存在。
        # 大小写不同的库在 lower_case_table_names=0 下是两个不同的 schema，
        # 用小写去撞会把"不存在"判成"存在"，随后在 select_db 处才失败。
        if database not in known:
            variants = known.variants(database)
            hint = f"；实例上存在大小写不同的同名库: {', '.join(variants)}" if variants else ""
            raise ValueError(
                f"数据库不存在或当前账号不可见: {database}"
                f"（SHOW DATABASES 未返回该库）{hint}")
        dbs = [database]
    else:
        dbs = business

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

    # Rev.J / P1-01：同名不同大小写的业务库如实报出——它们是两个库，不是一个。
    dupes = sorted({d.lower() for d in dbs if len(known.variants(d)) > 1})
    if dupes:
        warnings.append(_warn(
            "DB_NAME_CASE_VARIANTS", "WARNING", "",
            f"实例上存在 {len(dupes)} 组仅大小写不同的同名库"
            f"（{', '.join(known.variants(d)[0] for d in dupes[:5])} …）。"
            f"本模块按精确名分别统计；若 Proxy 返回的库限定名无法唯一归属，"
            f"该行会被记入 DB_NAME_AMBIGUOUS 而不是被猜给其中一个"))

    # Rev.J / P1-02：基线查询是耗时大头，必须在预算内
    if _now() >= deadline:
        raise TimeoutError(
            f"采集总时长预算 {TOTAL_BUDGET_SECONDS}s 在查询 information_schema 前即已耗尽")
    baseline = _collect_baseline(pool, dbs, known)
    if is_dist:
        items, warns, shape, totals = _collect_distributed(
            pool, dbs, baseline, known, deadline)
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


# 落库表的**完整字段契约**（与 backend/schema/v13/130_table_type_stats.sql 逐字对应）。
#
# Rev.G / P1-08：迁移器只对 `ALTER TABLE ... ADD COLUMN` 做列级验收
# （backend/schema/migrator.py:45-48 的 _ADD_COLUMN_RE），纯 CREATE TABLE 语句
# 不进入 _structure_state() 的验收范围。于是存在这样一条静默失效路径：
#   元数据库里已存在同名但缺列 / 错类型 / 缺索引的历史残留表
#   → CREATE TABLE IF NOT EXISTS 直接跳过
#   → 迁移被登记成功
#   → 直到本模块 INSERT 才 1054 报错，用户白等一轮采集
# 迁移登记之后若表被人工删除或结构漂移，_structure_state() 同样返回 valid，
# 不会重放。故本模块自行做一次确定性结构验收，且放在【采集之前】。
#
# Rev.J / P1-04：Rev.I 只比 DATA_TYPE，仍留着一整排"采集完才失败"的路径：
#   · detail 被收窄成 VARCHAR(16)  → 500 库采完才在 INSERT 撞 1406；
#   · id 没有 AUTO_INCREMENT       → 第一条可能写进去，第二条撞主键；
#   · created_at 没有默认值        → 严格模式下 INSERT 直接失败；
#   · stat_id 可空 / 字符集非 utf8mb4 → 中文 detail 撞 1366。
# KL-15 当时以"COLUMN_TYPE 带显示宽度、跨发行版不一致"为由放弃长度校验——
# 那个理由只对**整型**成立（int(11) vs int），对 varchar 长度并不成立。
# 现在按字段分工取值，两边都拿到确定性：
#   整型  → DATA_TYPE（无显示宽度）
#   字符  → DATA_TYPE + CHARACTER_MAXIMUM_LENGTH + CHARACTER_SET_NAME
#   共通  → IS_NULLABLE、COLUMN_DEFAULT（归一化后比较）、EXTRA（自增）
#
# 契约元组：(DATA_TYPE, 长度或 None, 是否可空, 归一化默认值, 是否自增)
_COL = lambda t, ln, null, dflt, ai=False: (t, ln, null, dflt, ai)
_STAT_CONTRACT = {
    "id":                  _COL("int", None, False, None, True),
    "connection_id":       _COL("varchar", 64, True, ""),
    "database_filter":     _COL("varchar", 128, True, ""),
    "instance_type":       _COL("varchar", 32, True, ""),
    "type_source":         _COL("varchar", 32, True, ""),
    "database_count":      _COL("int", None, True, "0"),
    "total_tables":        _COL("int", None, True, "0"),
    "shard_tables":        _COL("int", None, True, "0"),
    "broadcast_tables":    _COL("int", None, True, "0"),
    "single_tables":       _COL("int", None, True, "0"),
    "baseline_tables":     _COL("int", None, True, "0"),
    "subpartition_tables": _COL("int", None, True, "0"),
    "failed_databases":    _COL("int", None, True, "0"),
    "skipped_databases":   _COL("int", None, True, "0"),
    "overlap_count":       _COL("int", None, True, "0"),
    "warnings_json":       _COL("mediumtext", None, True, None),
    "created_by":          _COL("varchar", 64, True, ""),
    "created_at":          _COL("datetime", None, True, "CURRENT_TIMESTAMP"),
}
_ITEM_CONTRACT = {
    "id":                  _COL("int", None, False, None, True),
    "stat_id":             _COL("int", None, False, None),
    "db_name":             _COL("varchar", 128, True, ""),
    "total_tables":        _COL("int", None, True, "0"),
    "shard_tables":        _COL("int", None, True, "0"),
    "broadcast_tables":    _COL("int", None, True, "0"),
    "single_tables":       _COL("int", None, True, "0"),
    "baseline_tables":     _COL("int", None, True, "0"),
    "subpartition_tables": _COL("int", None, True, "0"),
    "status":              _COL("varchar", 16, True, "OK"),
    "detail":              _COL("varchar", 512, True, ""),
    "created_at":          _COL("datetime", None, True, "CURRENT_TIMESTAMP"),
}
# INSERT 用的列清单（顺序即 SQL 里的书写顺序，单测钉住二者一致）
_STAT_COLUMNS = tuple(_STAT_CONTRACT)
_ITEM_COLUMNS = tuple(_ITEM_CONTRACT)

# 期望索引：名字 → (列序元组, 是否唯一)。PRIMARY 天然唯一。
_STAT_INDEXES = {"PRIMARY": (("id",), True),
                 "idx_tts_conn": (("connection_id",), False),
                 "idx_tts_created": (("created_at",), False)}
_ITEM_INDEXES = {"PRIMARY": (("id",), True),
                 "idx_ttsi": (("stat_id",), False)}

_SCHEMA_SPEC = (
    ("table_type_stat", _STAT_CONTRACT, _STAT_INDEXES),
    ("table_type_stat_item", _ITEM_CONTRACT, _ITEM_INDEXES),
)

# 字符列必须是 utf8mb4：latin1/utf8mb3 会让中文 detail 在 INSERT 处撞 1366，
# 又是一条"采集完才失败"的路径。
_EXPECTED_CHARSET = "utf8mb4"


def _row_get(row, *keys):
    """兼容字典游标的大小写差异（MySQL 返回大写列名，部分驱动返回小写）。"""
    d = dict(row)
    for k in keys:
        for cand in (k, k.upper(), k.lower()):
            if cand in d:
                return d[cand]
    return None


def _norm_default(value):
    """把 COLUMN_DEFAULT 归一化成可比较文本。

    MariaDB 给字符串默认值加引号返回（'' → "''"），MySQL 8 返回原值；
    CURRENT_TIMESTAMP 的大小写与括号形态也不定。剥一层成对引号 + 关键字归一。
    注意：**不做 0/1 → FALSE/TRUE 的布尔归一**（migrator._normalize_default 做了那个，
    那是为布尔列服务的），本模块的计数列默认值就是字面量 "0"，混淆会掩盖真实漂移。
    """
    if value is None:
        return None
    v = str(value).strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        v = v[1:-1]
    vu = v.upper().rstrip("()")
    if vu in ("CURRENT_TIMESTAMP", "NOW"):
        return "CURRENT_TIMESTAMP"
    if vu == "NULL":
        # MariaDB 对"可空且无默认值"的列返回字面量 'NULL'，MySQL 8 返回 SQL NULL。
        # 两系归一为 Python None（MySQL 侧为无操作）。本模块没有任何一列
        # 的默认值是字符串 'NULL'，故不存在误归一。
        return None
    return v


class SchemaNotReadyError(RuntimeError):
    """落库表结构验收失败（Rev.G / P1-08，Rev.J / P1-04 扩展为完整契约）。

    由 API 映射为 500 + 可执行提示。
    """


def _check_column(table, col, spec, info, problems):
    """逐列比对完整契约。info 为 information_schema.COLUMNS 的一行。"""
    exp_type, exp_len, exp_null, exp_default, exp_ai = spec
    dtype = str(_row_get(info, "DATA_TYPE") or "").lower()
    if dtype != exp_type:
        problems.append(f"{col} 类型不符（期望 {exp_type}，实际 {dtype}）")
        return                       # 类型都不对，长度/默认值没有比的意义
    if exp_len is not None:
        actual_len = _row_get(info, "CHARACTER_MAXIMUM_LENGTH")
        if actual_len is None or int(actual_len) != exp_len:
            problems.append(
                f"{col} 长度不符（期望 {exp_type}({exp_len})，实际 {actual_len}）")
    if exp_type in ("varchar", "char", "text", "mediumtext", "longtext"):
        cs = str(_row_get(info, "CHARACTER_SET_NAME") or "").lower()
        if cs and cs != _EXPECTED_CHARSET:
            problems.append(
                f"{col} 字符集不符（期望 {_EXPECTED_CHARSET}，实际 {cs}）")
    nullable = str(_row_get(info, "IS_NULLABLE") or "").upper() == "YES"
    if nullable != exp_null:
        problems.append(
            f"{col} 可空性不符（期望 {'NULL' if exp_null else 'NOT NULL'}，"
            f"实际 {'NULL' if nullable else 'NOT NULL'}）")
    actual_default = _norm_default(_row_get(info, "COLUMN_DEFAULT"))
    if actual_default != exp_default:
        problems.append(
            f"{col} 默认值不符（期望 {exp_default!r}，实际 {actual_default!r}）")
    extra = str(_row_get(info, "EXTRA") or "").lower()
    if exp_ai and "auto_increment" not in extra:
        problems.append(
            f"{col} 缺少 AUTO_INCREMENT——第一条可能写入成功，第二条即撞主键")


def _ensure_schema() -> None:
    """落库表结构验收：表 + 全部列的完整契约 + 索引全列序，任一不符即失败关闭。

    放在 run_stats 入口而不是进程启动期，理由见设计 ADR-20：
    表类型统计是深度诊断下的只读诊断子模块，它的留档表有问题不应当让
    整个审核平台起不来（既有 index_audit / cluster_inspection 等同级表在
    _create_all_tables 中同样没有启动期结构验收）。放在采集之前则同时满足
    "确定性验收"与"不让用户白跑一轮 180 秒采集"。
    """
    conn = _get_connection()
    try:
        for table, contract, indexes in _SCHEMA_SPEC:
            rows = conn.execute(
                "SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, "
                "CHARACTER_SET_NAME, IS_NULLABLE, COLUMN_DEFAULT, EXTRA "
                "FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = ?",
                (table,)).fetchall()
            actual = {str(_row_get(r, "COLUMN_NAME") or "").lower(): r for r in rows}
            if not actual:
                raise SchemaNotReadyError(
                    f"元数据库缺少表 {table}：迁移 v13/130_table_type_stats.sql 未生效"
                    f"（可能是升级包未带该文件，或迁移已登记后表被人工删除——"
                    f"迁移器不会重放纯 CREATE TABLE 语句）。"
                    f"处置：确认该 .sql 已随版本部署，并手工执行其中的建表语句")
            problems = []
            missing = [c for c in contract if c.lower() not in actual]
            if missing:
                raise SchemaNotReadyError(
                    f"元数据库表 {table} 缺少列: {', '.join(missing)}。"
                    f"该表很可能是同名历史残留——CREATE TABLE IF NOT EXISTS 会静默跳过，"
                    f"迁移仍登记成功。处置：核实该表无业务数据后删表重启，"
                    f"或按 130_table_type_stats.sql 补齐列")
            for col, spec in contract.items():
                _check_column(table, col, spec, actual[col.lower()], problems)
            if problems:
                raise SchemaNotReadyError(
                    f"元数据库表 {table} 结构与迁移 DDL 不符: {'; '.join(problems)}。"
                    f"处置：按 130_table_type_stats.sql 的定义 ALTER 修正，"
                    f"或核实无数据后删表重启")
            irows = conn.execute(
                "SELECT INDEX_NAME, COLUMN_NAME, SEQ_IN_INDEX, NON_UNIQUE "
                "FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = ? "
                "ORDER BY INDEX_NAME, SEQ_IN_INDEX",
                (table,)).fetchall()
            have = {}
            for r in irows:
                iname = str(_row_get(r, "INDEX_NAME") or "")
                cols, uniq = have.setdefault(iname, ([], None))
                cols.append(str(_row_get(r, "COLUMN_NAME") or "").lower())
                if uniq is None:
                    have[iname] = (cols, str(_row_get(r, "NON_UNIQUE")) in ("0", "False"))
            lost = []
            for name, (exp_cols, exp_uniq) in indexes.items():
                got = have.get(name)
                if got is None:
                    lost.append(f"{name} 缺失")
                    continue
                got_cols, got_uniq = got
                if tuple(got_cols) != tuple(c.lower() for c in exp_cols):
                    lost.append(
                        f"{name} 列序不符（期望 {exp_cols}，实际 {tuple(got_cols)}）")
                elif got_uniq != exp_uniq:
                    lost.append(
                        f"{name} 唯一性不符（期望 {'UNIQUE' if exp_uniq else '非唯一'}）")
            if lost:
                raise SchemaNotReadyError(
                    f"元数据库表 {table} 索引不符: {', '.join(lost)}。"
                    f"处置：按 130_table_type_stats.sql 补建/修正索引")
    finally:
        conn.close()


def run_stats(pool, connection_id: str = "", database: str = "",
              operator: str = "") -> dict:
    """执行一次统计并落库。落库失败不降级——直接抛出（REQ-6 要求留档）。"""
    database = (database or "").strip()
    if database and database.lower() in _SYS_DB:
        raise ValueError(f"不允许统计系统库: {database}")
    # Rev.G / P2-03：connection_id 必须显式非空——空串下 registry.get("") 取的是
    # adhoc/默认保存连接，而 instance_type_service.resolve("") 走的是全局默认类型，
    # 两者可能指向不同实例，真分布式实例会被当成集中式，分片/广播全报 0。
    if not (connection_id or "").strip():
        raise ValueError("必须指定 connection_id（本模块不接受默认连接："
                         "连接解析与实例类型解析在空 ID 下可能指向不同实例）")
    # Rev.G / P1-08：先验收落库表结构，避免采集完才在 INSERT 处失败
    _ensure_schema()

    # Rev.G / P1-02：进入既有扫描并发槽位。本模块会额外建一条
    # Proxy 连接，且 Rev.L 明确单次采集没有可证明的墙钟硬上界；
    # 不限流会挤占 SQL 审核 / 慢查询扫描 / 巡检的工作线程与目标库连接。
    # 复用 registry.scan_slot 而不是自建信号量，才能与既有扫描【共享】同一份配额
    # （scan_service.py:72 是同样的用法），否则两套限流各算各的，全局上限失去意义。
    # 超限抛 ScanBusyError，由 API 映射为 429。槽位在 with 退出时必然释放（含异常）。
    with registry.scan_slot(connection_id):
        # Rev.J / P1-02：deadline 在【拿到槽位之后、任何目标实例查询之前】建立。
        # 放在槽位之内，是因为等槽位的时间不该算进采集预算；
        # 放在查询之前，是因为 SHOW DATABASES 与基线查询也必须受同一预算约束。
        deadline = _now() + TOTAL_BUDGET_SECONDS
        res = analyze(pool, connection_id=connection_id, database=database,
                      deadline=deadline)
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
        # Rev.K / P1-02：明细【批量】落库。Rev.J 是逐行 INSERT——500 个业务库就是
        # 500 次往返，全部发生在扫描槽已释放、deadline 已不再约束的阶段，
        # 会继续占用 API 工作线程。批量后 500 库只需 5 次往返。
        rows = [(stat_id, it["db_name"], it["total_tables"], it["shard_tables"],
                 it["broadcast_tables"], it["single_tables"],
                 it["baseline_tables"], it["subpartition_tables"],
                 it["status"], it["detail"]) for it in res["items"]]
        item_sql = ("INSERT INTO table_type_stat_item (stat_id, db_name, total_tables, "
                    "shard_tables, broadcast_tables, single_tables, baseline_tables, "
                    "subpartition_tables, status, detail) VALUES (?,?,?,?,?,?,?,?,?,?)")
        for i in range(0, len(rows), ITEM_INSERT_BATCH):
            conn.cursor().executemany(item_sql, rows[i:i + ITEM_INSERT_BATCH])
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

### A.2 `backend/api/table_type_stats.py`（新增，81 行）

```python
# -*- coding: utf-8 -*-
"""G14 · 表类型统计 API（DESIGN-v1.6.3.0 Rev.M §5）

Rev.G（O 评审整改）：
  · P1-02  /run 由 service 层进入 registry.scan_slot(connection_id)，
           本层只负责把 ScanBusyError 映射为 429（与 tdsql_manage.py:432 同口径）。
  · P1-08  SchemaNotReadyError 单独映射，把可执行的处置提示原样带给用户，
           不被兜底 except 吞成一句无信息的 500。
  · P2-02  接收 Request 并把 request.state.username 传给 run_stats(operator=)，
           否则 created_by 在真实调用中永远为空，REQ-6 的"可回看"缺了操作人。
  · P2-03  connection_id 必须显式非空：空串/缺字段由模型 `min_length=1` 在进入路由前
           拦截（422）；**全空白由本层在连接解析之前拦截（400，Rev.N / DEF-SIT-03）**；
           service 层保留同名守卫作为服务被直接调用时的兜底。
  · Rev.K  TimeoutError（采集预算耗尽）单独映射为 503——它是"稍后重试可能成功"
           的暂时性状况，与 500 的"结构不对，重试也没用"语义不同。
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.services import table_type_stats_service as svc
from backend.services.connection_registry import (
    registry, ConnectionNotFoundError, ScanBusyError)

router = APIRouter(prefix="/api/v1/table-type-stats", tags=["表类型统计"])


class StatsRequest(BaseModel):
    connection_id: str = Field(..., min_length=1, description="目标连接ID（必填）")
    database: str = Field("", description="仅统计指定库；空则全部业务库")


def _operator(request: Request) -> str:
    return getattr(request.state, "username", "anonymous")


def _pool(cid):
    try:
        return registry.get(cid)
    except ConnectionNotFoundError:
        raise HTTPException(status_code=400, detail="未连接TDSQL实例或连接不存在")


@router.post("/run", summary="发起表类型统计")
def run(body: StatsRequest, http_request: Request):
    # DEF-SIT-03：入参口径校验必须先于连接解析。否则 registry.get("   ") 会先抛
    # ConnectionNotFoundError，用户输入空白却被告知"未连接TDSQL实例"，排查方向跑偏；
    # 服务层同名守卫也因此在 HTTP 路径上永远不可达（单测直调服务层，测不出来）。
    if not body.connection_id.strip():
        raise HTTPException(
            status_code=400,
            detail="必须指定 connection_id（本模块不接受默认连接："
                   "连接解析与实例类型解析在空 ID 下可能指向不同实例）")
    pool = _pool(body.connection_id)
    try:
        return svc.run_stats(pool, connection_id=body.connection_id,
                             database=body.database,
                             operator=_operator(http_request))
    except ScanBusyError as e:
        # 并发超限：与既有慢查询扫描共用同一份配额，口径与 tdsql_manage.py:432 一致
        raise HTTPException(status_code=429, detail=str(e))
    except TimeoutError as e:
        # 采集总时长预算耗尽：暂时性，稍后重试或缩小 database 范围即可
        raise HTTPException(status_code=503, detail=str(e))
    except svc.SchemaNotReadyError as e:
        # 留档表结构不符：消息里已带可执行处置步骤，原样透出
        raise HTTPException(status_code=500, detail=str(e))
    except ValueError as e:
        # 入参口径错误（系统库 / 空 connection_id / 指定库不存在）——回 400 而非 500
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

### A.3 `backend/schema/v13/130_table_type_stats.sql`（新增，46 行）

> 迁移器会**逐行剔除以 `--` 开头的行**再按 `;` 切分（`backend/schema/migrator.py:159-164`，v1.6.2.2 后行号），
> 因此注释必须整行独占，语句之间必须有 `;`，文件末尾的 `;` 不可省。

```sql
-- v1.6.3.0 G14 表类型统计（DESIGN-v1.6.3.0 Rev.M §6.8）
-- 槽位：v13/130。v11/110 与 v12/120 已被 v1.6.2.2 的 O-18 / O-22 占用（Rev.I 更正）。
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
    -- Rev.G / P1-07：MEDIUMTEXT 而非 TEXT。MAX_DATABASES=500，最坏情况下每库
    -- 一条告警；虽然 Rev.G 已把 PROXY_CMD_FAILED 汇总成一条，RECON_MISMATCH 等
    -- 逐库告警仍可能达数百条，中文 UTF-8 一个字符 3 字节，TEXT 的 64 KiB 会先触顶。
    -- 采集已完成却在落库处 1406/截断失败，是最贵的一种失败。
    warnings_json       MEDIUMTEXT,
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

### A.4 `tests/test_table_type_stats.py`（新增，完整成品）

```python
# -*- coding: utf-8 -*-
"""G14 · 表类型统计 回归测试（DESIGN-v1.6.3.0 Rev.M §11）

**测试依赖如实说明**（Rev.J / DOC-01——Rev.I 之前一直写"除落库两例外全部离线"，
那是 Rev.B 时期的实际情况，后来陆续加到 10 例仍没有更新，属于文档失真）：

| 类别 | 数量 | 依赖 |
|---|---:|---|
| 纯离线 | **87** | 无。FakePool + FakeClock 全内存，不连任何数据库 |
| 元数据库集成 | **22** | 本地 MySQL/MariaDB；`@skipif(not MYSQL_AVAILABLE)` |
| 需模块落盘 | **1** | T-R08 权限键登记，断言仓库文件；设计阶段 skip |
| **collect 合计** | **110** | 含参数化展开（其中 T3-R08 契约用例 11 条） |

Rev.K 历史实测口径：不连元数据库时 `83 passed, 23 skipped`；连上后
`105 passed, 1 skipped`。第五轮将 Rev.L 附录抽取后实测 110 collected、连元数据库
109 passed + 1 skipped、离线 87 passed + 23 skipped；这仍不是落盘后的仓库回归证据。

元数据库集成用例覆盖：落库与回读、`/history` 与 `created_by`、500 库告警的
`MEDIUMTEXT` 往返、以及 6 项结构契约失败关闭（缺表 / 缺列 / 错类型 / 缺索引 /
采集前拦截 / DDL 与服务列清单一致）。它们连接的是
`SQLCHECK_DB_NAME`（默认 `tdsql_sqlcheck_test`），由 `g14_schema` fixture
在每个用例前后 DROP + 按 DDL 重建，互不干扰。

**破坏性目标保护（Rev.K / P1-04）**：可用性探测与实际 DROP 使用**同一份**
`backend.services.database.MYSQL_CONFIG`；任何 DROP 之前先过
`assert_destructive_target_is_safe()`——库名不精确等于 `tdsql_sqlcheck_test`
（或显式开启 `G14_ALLOW_DESTRUCTIVE_TESTS=1` + `G14_TEST_DB_NAME`）就抛
`DestructiveTargetError`，并把 host/port/database 打进日志。

元数据库不可达时这 22 例自动 skip——
**skip 不是 pass**：模块真正落盘后必须在具备元数据库的环境上重跑，
设计阶段的通过/跳过数只能作为设计阶段记录，不能当作发布证据。

数据夹具取自内网实测（设计附录 B）：列名 db_table，值为库限定名；
子分区相关用例直接使用 2026-08-31 T17 取回的 78 个真实表名。
"""
import os
import random
import sys
import time

import pytest

from backend.services import table_type_stats_service as svc
from backend.services.tdsql_connector import TDSQLConnectionConfig

# Rev.L 定向回归（O 第四轮评审的 T4-R01…T4-R05；Rev.M 修正 T4-R01）
def test_t4r01_baseline_rejects_case_sibling_returned_by_ci_in(monkeypatch):
    """T4-R01 / P1-01：指定库基线多返兄弟库时，两个分支都不得吸收。"""
    class _CiMetadataPool(FakePool):
        def _execute(self, sql, params=None):
            if "information_schema.TABLES" in sql:
                self.seen.append(sql)
                # 模拟服务端不遵守大小写精确语义：普通 IN 只查 Sales 却带回 sales。
                return [
                    {"TABLE_SCHEMA": "Sales", "TABLE_NAME": "t_upper",
                     "TABLE_TYPE": "BASE TABLE"},
                    {"TABLE_SCHEMA": "sales", "TABLE_NAME": "t_lower",
                     "TABLE_TYPE": "BASE TABLE"},
                ]
            return super()._execute(sql, params)

    for instance_type in ("centralized", "distributed"):
        _patch_ctx(monkeypatch, instance_type)
        per_db = {}
        if instance_type == "distributed":
            per_db = {
                ("Sales", svc.SQL_SHARD): _rows(["Sales.t_upper"]),
                ("Sales", svc.SQL_BROADCAST): [],
                ("Sales", svc.SQL_SINGLE): [],
            }
        pool = _CiMetadataPool(databases=["Sales", "sales"], per_db=per_db)
        if instance_type == "distributed":
            _patch_tmp_pool(monkeypatch, pool)
        res = svc.analyze(pool, connection_id="c1", database="Sales")
        assert res["database_count"] == 1
        assert res["total_tables"] == 1
        assert res["baseline_tables"] == 1
        assert res["items"][0]["db_name"] == "Sales"
        assert not any(w["code"] == "RECON_MISMATCH" for w in res["warnings"])


def test_t4r02_real_pool_tail_path_can_exceed_old_formula(monkeypatch):
    """T4-R02 / P1-02：真实池控制流可走出旧公式未覆盖的 70s 尾部。"""
    from backend.services.tdsql_connector import TDSQLConnectionPool

    clock = FakeClock()
    started = clock.t
    pool = TDSQLConnectionPool(
        TDSQLConnectionConfig(host="h", port=3306, user="u", password="p",
                              database="d", connect_timeout=5, read_timeout=30),
        pool_size=1)

    class _OldConnection:
        def ping(self, reconnect=False):
            clock.advance(30.0)
            raise OSError("stale connection")

        def close(self):
            pass

    class _SelectFails:
        def select_db(self, db):
            clock.advance(30.0)
            raise RuntimeError("select_db failed")

        def close(self):
            pass

    class _Spare:
        def close(self):
            pass

    pool._local.conn = _OldConnection()
    created = []

    def _create():
        clock.advance(5.0)
        created.append(True)
        return _SelectFails() if len(created) == 1 else _Spare()

    monkeypatch.setattr(pool, "_create_connection", _create)
    with pytest.raises(RuntimeError, match="select_db failed"):
        with pool.get_connection() as conn:
            conn.select_db("db_a")

    # ping 30 + 建连 5 + select_db 30 + 异常后重建 5 = 70。
    assert clock.t - started == 70.0
    assert len(created) == 2
    assert not hasattr(svc, "MAX_COLLECT_WALL_SECONDS")
    assert not hasattr(svc, "MAX_WALL_SECONDS")

    # 软预算反向护栏：已进入 get_connection 的 ping→建连可跨过 deadline，
    # 但服务层在拿到连接后必须复查，不得再新发 select_db。
    _patch_ctx(monkeypatch, "distributed")
    clock2 = FakeClock()
    started2 = clock2.t
    deadline = clock2.t + 31.0
    tail = TDSQLConnectionPool(
        TDSQLConnectionConfig(host="h", port=3306, user="u", password="p",
                              database="Sales", connect_timeout=5, read_timeout=30),
        pool_size=1)
    selected = []

    class _OldAgain:
        def ping(self, reconnect=False):
            clock2.advance(30.0)
            raise OSError("stale connection")

        def close(self):
            pass

    class _AcquiredAfterDeadline:
        def select_db(self, db):
            selected.append(db)

        def close(self):
            pass

    tail._local.conn = _OldAgain()
    created2 = []

    def _create2():
        clock2.advance(5.0)
        created2.append(True)
        return _AcquiredAfterDeadline()

    monkeypatch.setattr(tail, "_create_connection", _create2)
    monkeypatch.setattr(svc, "_new_pool", lambda cfg, pool_size=1: tail)
    monkeypatch.setattr(svc, "_now", clock2)
    outer = FakePool(databases=["Sales"],
                     info_schema={"Sales": {"base": ["t_upper"]}})
    res = svc.analyze(outer, connection_id="c1", database="Sales", deadline=deadline)

    assert clock2.t - started2 == 35.0
    assert len(created2) == 1, "只允许已启动的 get_connection 内部建连"
    assert selected == [], "拿到连接时 deadline 已过，不得再新发 select_db"
    assert res["skipped_databases"] == 1
    assert res["items"][0]["status"] == "SKIPPED"


def test_t4r03_pymysql_read_timeout_is_per_read():
    """T4-R03 / P1-02：30s read_timeout 不是整条结果读取的总 deadline。"""
    from pymysql.connections import Connection

    clock = FakeClock()
    started = clock.t

    class _Sock:
        def __init__(self):
            self.timeouts = []

        def settimeout(self, value):
            self.timeouts.append(value)

        def close(self):
            pass

    class _RFile:
        def read(self, size):
            clock.advance(20.0)
            return b"x" * size

        def close(self):
            pass

    conn = object.__new__(Connection)
    conn._sock = _Sock()
    conn._rfile = _RFile()
    conn._read_timeout = 30
    assert conn._read_bytes(1) == b"x"
    assert conn._read_bytes(1) == b"x"
    assert clock.t - started == 40.0, "两次成功读取累计可超过单次 read_timeout"
    assert conn._sock.timeouts == [30, 30], "每次底层读取前都重设超时"
    conn._force_close()


def test_t4r05_probe_connects_effective_database(monkeypatch):
    """T4-R05 / P2-02：探测必须真正选中后续 DROP 使用的元数据库。"""
    import pymysql

    cfg = {"host": "meta", "port": 3307, "user": "tester", "password": "pw",
           "database": "tdsql_sqlcheck_test", "charset": "utf8mb4"}
    seen = {}

    class _Conn:
        def close(self):
            pass

    def _connect(**kwargs):
        seen.update(kwargs)
        return _Conn()

    mod = sys.modules[__name__]
    monkeypatch.setattr(mod, "effective_db_config", lambda: dict(cfg))
    monkeypatch.setattr(pymysql, "connect", _connect)
    assert _probe_metadata_db() is True
    for key in ("host", "port", "user", "password", "database", "charset"):
        assert seen[key] == cfg[key]

# ══════════════════════════════════════════════════════════════════
# Rev.K 定向回归（O 第三轮评审 §7 的 T3-R01…T3-R10）
# ══════════════════════════════════════════════════════════════════
def test_t3r01_named_database_must_not_absorb_case_sibling(monkeypatch):
    """T3-R01 / P1-01：指定 `Sales` 时，实例级返回里的 `sales.*` 必须被过滤掉。

    Rev.J 在 `_extract_pairs` 里用全实例命名空间 known 把 qual 解析成了 canonical
    名（这步对），随后又对只含目标库的 target 做了**第二次** CI 回退——
    目标子集只有 `Sales` 一个候选时，`sales` 会被"唯一命中"回 `Sales`，
    于是另一个真实库的表被算进用户指定的库。四个主数字直接错。
    """
    _patch_ctx(monkeypatch, "distributed")
    allrows = _rows(["Sales.t_upper", "sales.t_lower"], info="shardkey:id")
    per_db = {("Sales", svc.SQL_SHARD): allrows,
              ("Sales", svc.SQL_BROADCAST): [],
              ("Sales", svc.SQL_SINGLE): []}
    pool = FakePool(databases=["Sales", "sales"],
                    info_schema={"Sales": {"base": ["t_upper"]},
                                 "sales": {"base": ["t_lower"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1", database="Sales")

    assert res["database_count"] == 1
    assert [i["db_name"] for i in res["items"]] == ["Sales"]
    assert res["total_tables"] == 1, "sales.t_lower 必须被过滤，不得计入 Sales"
    assert res["shard_tables"] == 1
    assert res["baseline_tables"] == 1
    assert not any(w["code"] == "RECON_MISMATCH" for w in res["warnings"])
    # 反向：指定 sales 时同理只计自己那一张
    per_db2 = {("sales", svc.SQL_SHARD): allrows,
               ("sales", svc.SQL_BROADCAST): [],
               ("sales", svc.SQL_SINGLE): []}
    pool2 = FakePool(databases=["Sales", "sales"],
                     info_schema={"Sales": {"base": ["t_upper"]},
                                  "sales": {"base": ["t_lower"]}},
                     per_db=per_db2)
    _patch_tmp_pool(monkeypatch, pool2)
    res2 = svc.analyze(pool2, connection_id="c1", database="sales")
    assert res2["total_tables"] == 1
    assert [i["db_name"] for i in res2["items"]] == ["sales"]


def test_t3r02_budget_exit_does_not_rebuild_connection(monkeypatch):
    """T3-R02 / P1-02：预算耗尽是正常控制流，**不得**触发连接池重建。

    真实池会捕获穿出 `with` 的任何异常并 close + `_create_connection()`。
    用异常承载"预算耗尽"，等于在 deadline 之后把一条健康连接销毁重建，
    白白多付一次 connect_timeout。
    """
    _patch_ctx(monkeypatch, "distributed")
    clock = FakeClock()
    monkeypatch.setattr(svc, "_now", clock)
    deadline = clock.t + 100.0
    per_db = {}
    for d in ("db_a", "db_b"):
        for sql in (svc.SQL_SHARD, svc.SQL_BROADCAST, svc.SQL_SINGLE):
            per_db[(d, sql)] = _rows([f"{d}.t"])
    pool = FakePool(databases=["db_a", "db_b"],
                    info_schema={"db_a": {"base": ["t"]}, "db_b": {"base": ["t"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    orig = svc._extract_pairs

    def _slow(rows, cur, known):
        clock.advance(60.0)
        return orig(rows, cur, known)

    monkeypatch.setattr(svc, "_extract_pairs", _slow)
    res = svc.analyze(pool, connection_id="c1", deadline=deadline)

    assert pool.create_calls == 0, \
        f"预算控制流不得触发连接重建，实际重建 {pool.create_calls} 次"
    assert pool.generation == 0
    by_db = {i["db_name"]: i for i in res["items"]}
    assert by_db["db_a"]["status"] == "SKIPPED"
    assert by_db["db_b"]["status"] == "SKIPPED"


def test_t3r03_budget_signal_survives_a_failing_reconnect(monkeypatch):
    """T3-R03 / P1-02：即使重连会失败，预算路径也不该走到那里——状态必须是 SKIPPED。

    Rev.J 的写法下，`_BudgetExceeded` 穿出 with → 池尝试重建 → 重建抛错 →
    新异常盖掉原信号 → 本该 SKIPPED 的库被误标成 FAILED。
    """
    _patch_ctx(monkeypatch, "distributed")
    clock = FakeClock()
    monkeypatch.setattr(svc, "_now", clock)
    deadline = clock.t + 100.0
    per_db = {("db_a", sql): _rows(["db_a.t"])
              for sql in (svc.SQL_SHARD, svc.SQL_BROADCAST, svc.SQL_SINGLE)}
    pool = FakePool(databases=["db_a"], info_schema={"db_a": {"base": ["t"]}},
                    per_db=per_db)
    pool.reconnect_fail = _mysql_error(2003, "Can't connect to MySQL server")
    _patch_tmp_pool(monkeypatch, pool)
    orig = svc._extract_pairs

    def _slow(rows, cur, known):
        clock.advance(60.0)
        return orig(rows, cur, known)

    monkeypatch.setattr(svc, "_extract_pairs", _slow)
    res = svc.analyze(pool, connection_id="c1", deadline=deadline)
    assert pool.create_calls == 0, "预算路径根本不该碰到重连"
    assert res["items"][0]["status"] == "SKIPPED", "不得被重连失败盖成 FAILED"
    assert res["skipped_databases"] == 1 and res["failed_databases"] == 0


def test_t3r03b_real_error_still_rebuilds(monkeypatch):
    """T3-R03 反向护栏：**真实**的数据库异常仍必须触发重建（P1-04 不得回退）。"""
    _patch_ctx(monkeypatch, "distributed")
    per_db = {("db_a", svc.SQL_SHARD): _mysql_error(2013, "Lost connection"),
              ("db_b", svc.SQL_SHARD): _rows(["db_b.s"]),
              ("db_b", svc.SQL_BROADCAST): [],
              ("db_b", svc.SQL_SINGLE): []}
    pool = FakePool(databases=["db_a", "db_b"],
                    info_schema={"db_a": {"base": ["s"]}, "db_b": {"base": ["s"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert pool.create_calls == 1, "真实异常必须触发一次连接重建"
    by_db = {i["db_name"]: i for i in res["items"]}
    assert by_db["db_a"]["status"] == "FAILED"
    assert by_db["db_b"]["status"] == "OK"


def test_t3r04_soft_budget_scope_is_explicit():
    """T3-R04 / Rev.L P1-02：180s 是软预算，不存在伪硬上界常量。"""
    assert svc.TOTAL_BUDGET_SECONDS == 180
    assert svc.CONNECT_TIMEOUT == 5
    assert svc.COMMAND_READ_TIMEOUT == 30
    assert not hasattr(svc, "MAX_WALL_SECONDS")
    assert not hasattr(svc, "MAX_COLLECT_WALL_SECONDS")
    # 元数据库阶段不在 target deadline 内，但落库必须保持批量化。
    assert svc.ITEM_INSERT_BATCH >= 50


def test_t3r04b_deadline_checkpoints_are_where_the_soft_budget_says(monkeypatch):
    """T3-R04：钉住"到期后不再开新 I/O"依赖的检查点。"""
    import inspect
    src_analyze = inspect.getsource(svc.analyze)
    assert src_analyze.count("_now() >= deadline") >= 3, \
        "analyze 必须在实例类型探测后、库枚举前、基线查询前各检查一次"
    src_collect = inspect.getsource(svc._collect_distributed)
    assert src_collect.count("_now() >= deadline") >= 3, \
        "_collect_distributed 必须在进连接上下文前、拿到连接后/切库前、每条命令前检查"
    acquire_pos = src_collect.index("with tmp.get_connection() as conn:")
    post_acquire_pos = src_collect.index("if _now() >= deadline:", acquire_pos)
    select_pos = src_collect.index("conn.select_db(db)", acquire_pos)
    assert acquire_pos < post_acquire_pos < select_pos, \
        "ping/建连可跨过 deadline；拿到连接后、select_db 前必须复查"
    # 预算耗尽必须是"置标志 + break"，不是抛异常
    assert "budget_hit = True" in src_collect
    assert "raise _BudgetExceeded" not in src_collect


def test_t3r10_zero_effective_databases_is_not_partial_success(monkeypatch):
    """T3-R10 / P2-03：失败 + 跳过覆盖全部库时，有效库数为 0，不能叫"部分完成"。

    这条在服务端把判据钉住：前端据 `database_count - failed - skipped` 分流。
    """
    _patch_ctx(monkeypatch, "distributed")
    clock = FakeClock()
    monkeypatch.setattr(svc, "_now", clock)
    per_db = {("db_a", svc.SQL_SHARD): _mysql_error(1142, "denied")}
    pool = FakePool(databases=["db_a", "db_b"],
                    info_schema={"db_a": {"base": ["x"]}, "db_b": {"base": ["y"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    orig = svc._collect_baseline

    def _tick(p, dbs, known):
        out = orig(p, dbs, known)
        return out

    monkeypatch.setattr(svc, "_collect_baseline", _tick)

    # db_a 失败；db_b 在进入前预算耗尽
    real_extract = svc._extract_pairs

    def _boom(rows, cur, known):
        clock.advance(200.0)
        return real_extract(rows, cur, known)

    monkeypatch.setattr(svc, "_extract_pairs", _boom)
    res = svc.analyze(pool, connection_id="c1",
                      deadline=clock.t + svc.TOTAL_BUDGET_SECONDS)
    effective = (res["database_count"] - res["failed_databases"]
                 - res["skipped_databases"])
    assert effective == 0, "两个库一个失败一个跳过，有效库数必须是 0"
    assert res["total_tables"] == 0
    assert res["baseline_tables"] == 0 and res["subpartition_tables"] == 0


# ══════════════════════════════════════════════════════════════════
# 元数据库集成测试的目标保护（Rev.K / P1-04）
# ══════════════════════════════════════════════════════════════════
#
# 这些用例会 DROP 两张表。Rev.J 的做法有两个致命问题：
#   ① 可用性探测用 TDSQL_TEST_* 一套配置，实际 DROP 用 SQLCHECK_DB_* 另一套，
#      两套可以指向完全不同的服务器——探测通了就不 skip，然后往【生产元数据库】
#      执行 DROP；反过来探测不通又会把本可运行的用例错误跳过；
#   ② 唯一的"保护"是 fixture 里的 `os.environ.setdefault("SQLCHECK_DB_NAME", ...)`，
#      它既晚于 database 模块导入（MYSQL_CONFIG 早已按旧值定型），
#      又因为是 setdefault 而**不会覆盖**外部已设的 SQLCHECK_DB_NAME=tdsql_sqlcheck。
#
# Rev.K 的做法：**探测与执行使用同一份已生效的 MYSQL_CONFIG**，并在任何 DROP 之前
# 做失败关闭断言。这是安全底线，不是风格问题——测试代码删掉生产数据是不可接受的。
_APPROVED_TEST_DB = "tdsql_sqlcheck_test"
# 需要用别的库名跑破坏性用例时，必须同时显式设置这两个环境变量。
_ALLOW_DESTRUCTIVE = os.environ.get("G14_ALLOW_DESTRUCTIVE_TESTS") == "1"
_CUSTOM_TEST_DB = os.environ.get("G14_TEST_DB_NAME", "")


def effective_db_config():
    """返回 database 模块【当前真正在用】的连接配置（不是环境变量的快照）。"""
    from backend.services.database import MYSQL_CONFIG
    return dict(MYSQL_CONFIG)


def _probe_metadata_db():
    """用与 DROP 完全相同的配置探测可用性，并真正选中目标库。"""
    cfg = effective_db_config()
    try:
        import pymysql
        conn = pymysql.connect(host=cfg["host"], port=cfg["port"],
                               user=cfg["user"], password=cfg["password"],
                               database=cfg["database"],
                               charset=cfg.get("charset", "utf8mb4"),
                               connect_timeout=3)
        conn.close()
        return True
    except Exception:
        return False


MYSQL_AVAILABLE = _probe_metadata_db()


class DestructiveTargetError(RuntimeError):
    """破坏性测试的目标库不在批准清单内。

    刻意用普通异常而不是 `pytest.fail()`：后者抛的 `Failed` 继承自 BaseException，
    守门人本身就没法被单测覆盖了——而"守门人会不会放行"恰恰是这里最需要被验证的一件事。
    从 fixture 里抛出的异常同样会让用例 error 掉，失败关闭的效果完全一样。
    """


def assert_destructive_target_is_safe():
    """任何 DROP 之前的失败关闭断言。不通过即抛出，绝不"尽力而为"。"""
    cfg = effective_db_config()
    target = str(cfg.get("database") or "")
    allowed = {_APPROVED_TEST_DB}
    if _ALLOW_DESTRUCTIVE and _CUSTOM_TEST_DB:
        allowed.add(_CUSTOM_TEST_DB)
    # 破坏性操作前把目标打印出来——出了事要能立刻看出打的是哪台机器
    print(f"[G14 破坏性测试目标] host={cfg.get('host')} port={cfg.get('port')} "
          f"database={target!r} 允许集合={sorted(allowed)}")
    if target not in allowed:
        raise DestructiveTargetError(
            f"拒绝在非批准的数据库上执行 DROP：当前 SQLCHECK_DB_NAME={target!r}，"
            f"仅允许 {sorted(allowed)}。"
            f"如确需在自定义库上跑破坏性用例，请同时设置 "
            f"G14_ALLOW_DESTRUCTIVE_TESTS=1 与 G14_TEST_DB_NAME=<库名>。")
    return cfg


# ══════════════════════════════════════════════════════════════════
# 测试替身
# ══════════════════════════════════════════════════════════════════
class FakePool:
    """脚本化连接池替身。

    databases   : SHOW DATABASES 返回的库名（含系统库）
    info_schema : {db: {"base":[...], "view":[...]}}
    per_db      : {(当前库, sql): 行列表 或 Exception}
    show_db_fail: SHOW DATABASES 抛出的异常（P2-01 用）

    Rev.G / P1-04：忠实复刻 TDSQLConnectionPool.get_connection() 的重建语义
    （tdsql_connector.py:287-307）——异常【穿出】with 才会关闭并重建线程本地连接。
    generation 记录重建次数，conn_ids 记录每库实际拿到的连接代次，
    用来断言"坏连接没有被后续库复用"。
    """

    def __init__(self, databases=None, info_schema=None, per_db=None,
                 select_db_fail=None, show_db_fail=None):
        self.config = TDSQLConnectionConfig(host="h", port=3306, user="u",
                                            password="p", database="d")
        self.databases = databases or []
        self.info_schema = info_schema or {}
        self.per_db = per_db or {}
        self.select_db_fail = select_db_fail or {}
        self.show_db_fail = show_db_fail
        self.seen, self.selected = [], []
        self.current_db = ""
        self.closed = False
        self.made_with_read_timeout = None
        self.generation = 0          # 连接重建次数
        self.create_calls = 0        # _create_connection() 被调用次数（Rev.K / P1-02）
        self.reconnect_fail = None   # 令重建失败，验证信号不被覆盖
        self.ctx_count = 0           # get_connection() 进入次数
        self.conn_ids = []           # [(db, 该库拿到的连接代次)]

    def _execute(self, sql, params=None):
        self.seen.append(sql)
        if sql == "SHOW DATABASES":
            if self.show_db_fail is not None:
                raise self.show_db_fail
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
        pool.ctx_count += 1

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
            generation = pool.generation

            def select_db(self_i, db):
                pool.selected.append(db)
                pool.conn_ids.append((db, self_i.generation))
                if db in pool.select_db_fail:
                    raise pool.select_db_fail[db]
                pool.current_db = db

            def cursor(self_i):
                return _Cursor()

        class _Ctx:
            def __enter__(self_i):
                return _Conn()

            def __exit__(self_i, exc_type, exc, tb):
                if exc_type is not None:
                    # 异常穿出 ⇒ 关闭旧连接并【真的】重建（真实池的行为）。
                    # Rev.K / P1-02：这里必须调用 _create_connection()，否则
                    # "预算控制信号触发了一次重连"这种问题在测试里根本看不出来
                    # ——Rev.J 的 FakePool 只把 generation += 1，是个哑动作。
                    pool._create_connection()
                return False

        return _Ctx()

    def _create_connection(self):
        """真实池在异常穿出上下文时会调用它（tdsql_connector.py:298-306）。"""
        self.create_calls += 1
        self.generation += 1
        if self.reconnect_fail is not None:
            raise self.reconnect_fail
        return object()

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


def by_db_detail(res):
    """{库名: 明细说明}，Rev.G 起逐库失败原因只在 item.detail 里（P1-07）。"""
    return {i["db_name"]: i["detail"] for i in res["items"]}


class FakeClock:
    """可控单调时钟（Rev.J / P1-02）。每次读秒推进 step 秒，可手工 advance。

    用它代替 sleep：既让 deadline 行为可断言，又不让测试真的等 180 秒。
    """

    def __init__(self, start=1000.0, step=0.0):
        self.t = float(start)
        self.step = float(step)
        self.reads = 0

    def __call__(self):
        self.reads += 1
        v = self.t
        self.t += self.step
        return v

    def advance(self, secs):
        self.t += float(secs)


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
    pairs, _c, guessed, _x, _a = svc._extract_pairs(
        rows, "sqltuning", svc._NameSpace(["sqltuning", "mysql"]))
    assert pairs == expect and guessed is False


def test_extract_pairs_detects_cross_database_rows():
    """结果含其他库 ⇒ 命令作用域为实例级（RISK-E）"""
    pairs, _c, _g, cross, _a = svc._extract_pairs(
        _rows(["db_a.t1", "db_b.t2"]), "db_a", svc._NameSpace(["db_a", "db_b"]))
    assert cross is True
    assert pairs == {("db_a", "t1"), ("db_b", "t2")}


def test_extract_pairs_keeps_dotted_table_name():
    """点号左侧不是已知库名时不得拆分——避免误拆后被过滤掉（少算）"""
    pairs, _c, _g, cross, _a = svc._extract_pairs(
        [{"db_table": "odd.name"}], "db_a", svc._NameSpace(["db_a", "db_b"]))
    assert pairs == {("db_a", "odd.name")} and cross is False


def test_extract_pairs_unknown_shape():
    pairs, columns, guessed, _x, _a = svc._extract_pairs(
        [{"col_x": "t_a", "col_y": 1}], "db", svc._NameSpace(["db"]))
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
    """实例级作用域：按库归属拆分、(库,表) 去重；Rev.G 起【不再提前停止】。

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
    # Rev.G / P1-01：即使前两库指纹相同，也必须把 db_c 也执行一遍
    assert pool.selected == ["db_a", "db_b", "db_c"]
    assert any(w["code"] == "INSTANCE_WIDE_SCOPE" for w in res["warnings"])


def test_r01_identical_fingerprint_must_not_skip_third_db(monkeypatch):
    """T-R01 / P1-01：前两库返回集合相同，但第三库另有表，不得提前停止。

    这正是 O 指出的反例：db_a、db_b 指纹相同只证明"换默认库没改变当前账号
    看到的集合"，不证明这个集合覆盖了 db_c。Rev.F 的提前停止会把 db_c 的
    2 张表整个漏掉，且页面四个主数字仍显示为"成功"。
    """
    _patch_ctx(monkeypatch, "distributed")
    shared = _rows(["db_a.s1", "db_b.s2"], info="shardkey:id")
    per_db = {}
    for d in ("db_a", "db_b"):
        per_db[(d, svc.SQL_SHARD)] = shared
        per_db[(d, svc.SQL_BROADCAST)] = []
        per_db[(d, svc.SQL_SINGLE)] = []
    # db_c 属于另一路由域：前两库看不到它，只有切到 db_c 才返回
    per_db[("db_c", svc.SQL_SHARD)] = _rows(["db_c.s3"], info="shardkey:id")
    per_db[("db_c", svc.SQL_BROADCAST)] = _rows(
        ["db_c.b3"], info="shardkey:noshardkey_allset")
    per_db[("db_c", svc.SQL_SINGLE)] = []
    pool = FakePool(databases=["db_a", "db_b", "db_c"],
                    info_schema={"db_a": {"base": ["s1"]},
                                 "db_b": {"base": ["s2"]},
                                 "db_c": {"base": ["s3", "b3"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert pool.selected == ["db_a", "db_b", "db_c"], "不得因指纹相同跳过 db_c"
    assert res["total_tables"] == 4
    by_db = {i["db_name"]: i for i in res["items"]}
    assert by_db["db_c"]["total_tables"] == 2
    assert by_db["db_c"]["status"] == "OK"
    # 且不得留下"仅基线可见"的漏表告警——说明确实采到了
    assert not any(w["code"] == "RECON_MISMATCH" for w in res["warnings"])


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


# 内网 lzbj_ecif 的 6 张按月二级分区父表（T17 实测，2026-08-31）。
# 注意 cus_pub_updatelog 与 cus_pub_updatelog_detail 互为前缀——这是真实数据里
# 存在的形态，父表推导必须把两者分开，不能把 _detail 的子表算到 cus_pub_updatelog 头上。
_UAT_PARENTS = ("cus_bas_merge_log", "cus_pub_sync_consumer_log",
                "cus_pub_sync_log", "cus_pub_translog",
                "cus_pub_updatelog", "cus_pub_updatelog_detail")
_UAT_SUFFIXES = ("190001",) + tuple(f"2026{m:02d}" for m in range(1, 13))
_UAT_SUBP = tuple(f"{p}_tdsql_subp{s}"
                  for p in _UAT_PARENTS for s in _UAT_SUFFIXES)


def test_r07b_real_intranet_names_derive_exactly_six_parents():
    """T17 实测锚点：78 张真实子表必须推导出【正好 6 个】父表，每个 13 张。

    这条用真名而不是构造名，是因为真实数据里有一个构造夹具想不到的形态：
    cus_pub_updatelog 与 cus_pub_updatelog_detail 互为前缀，且【两者都是父表】。
    父表推导若写成贪婪或按第一个 _tdsql_subp 之前的最短前缀切，
    cus_pub_updatelog_detail 的 13 张子表就会被算到 cus_pub_updatelog 头上，
    于是 cus_pub_updatelog_detail 变成"父表未确认"，13 张子表回流进逻辑基线，
    UAT 的 215/78 变成 228/65 —— 数字错了，而且错得很像对的。
    """
    assert len(_UAT_SUBP) == 78
    parents = {}
    for name in _UAT_SUBP:
        m = svc._SUBPARTITION_RE.match(name)
        assert m, f"正则未命中真实子表名: {name}"
        parents[m.group("parent")] = parents.get(m.group("parent"), 0) + 1
    assert set(parents) == set(_UAT_PARENTS)
    assert set(parents.values()) == {13}
    # 与内网 SQL 侧 SUBSTRING_INDEX(TABLE_NAME,'_tdsql_subp',1) 的口径一致
    assert set(parents) == {n.split("_tdsql_subp")[0] for n in _UAT_SUBP}


def test_r07c_nested_prefix_parents_are_not_confused():
    """前缀嵌套的两张父表必须各归各的（T17 实测形态）。"""
    a = svc._SUBPARTITION_RE.match("cus_pub_updatelog_tdsql_subp202601")
    b = svc._SUBPARTITION_RE.match("cus_pub_updatelog_detail_tdsql_subp202601")
    assert a.group("parent") == "cus_pub_updatelog"
    assert b.group("parent") == "cus_pub_updatelog_detail"
    # 只确认了短的那个父表时，_detail 的子表【不得】被剔除
    base = {"cus_pub_updatelog", "cus_pub_updatelog_detail",
            "cus_pub_updatelog_tdsql_subp202601",
            "cus_pub_updatelog_detail_tdsql_subp202601"}
    logical, subp = svc._classify_subpartitions(base, {"cus_pub_updatelog"})
    assert subp == {"cus_pub_updatelog_tdsql_subp202601"}
    assert "cus_pub_updatelog_detail_tdsql_subp202601" in logical


def test_r07d_uat_parent_confirmation_is_all_or_nothing_per_parent(monkeypatch):
    """T17 端到端：6 个父表全确认 → 剔 78；缺一个父表 → 只剔 65 且差异显式报出。

    后一半正是"父表确认"这条规则的兜底方向：宁可多报一次 RECON_MISMATCH（可见），
    也不静默少算（不可见）。
    """
    base = set(_UAT_PARENTS) | set(_UAT_SUBP)
    logical, subp = svc._classify_subpartitions(base, set(_UAT_PARENTS))
    assert (len(logical), len(subp)) == (6, 78)

    partial = set(_UAT_PARENTS) - {"cus_pub_updatelog_detail"}
    logical2, subp2 = svc._classify_subpartitions(base, partial)
    assert len(subp2) == 65
    assert len(logical2) == 19          # 6 个父表 + 回流的 13 张子表
    assert "cus_pub_updatelog_detail_tdsql_subp202612" in logical2


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
    # Rev.G / P1-07：失败库汇总为【一条】告警，逐库原因下沉到 item.detail
    w = [x for x in res["warnings"] if x["code"] == "PROXY_CMD_FAILED"]
    assert len(w) == 1
    assert "1 个库采集失败" in w[0]["detail"] and "db_b" in w[0]["detail"]
    assert "授权不足" in by_db_detail(res)["db_b"]


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
    clock = FakeClock()
    monkeypatch.setattr(svc, "_now", clock)
    pool = FakePool(databases=["db_a", "db_b"],
                    info_schema={"db_a": {"base": ["s1"]},
                                 "db_b": {"base": ["s2"]}},
                    per_db={})
    _patch_tmp_pool(monkeypatch, pool)
    # 前置检查（枚举库、基线）都在预算内，进入逐库循环前把时钟推过 deadline
    deadline = clock.t + svc.TOTAL_BUDGET_SECONDS
    orig = svc._collect_baseline

    def _slow_baseline(p, dbs, known):
        out = orig(p, dbs, known)
        clock.advance(svc.TOTAL_BUDGET_SECONDS + 1)     # 基线查询"耗尽"了预算
        return out

    monkeypatch.setattr(svc, "_collect_baseline", _slow_baseline)
    res = svc.analyze(pool, connection_id="c1", deadline=deadline)
    assert res["skipped_databases"] == 2 and res["total_tables"] == 0
    assert all(i["status"] == "SKIPPED" for i in res["items"])
    assert any(w["code"] == "TIME_BUDGET_EXCEEDED" for w in res["warnings"])
    # P1-03：全部 SKIPPED 时，声明为"不计入汇总"的数一律为 0
    assert res["baseline_tables"] == 0
    assert res["subpartition_tables"] == 0
    assert res["overlap_count"] == 0


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
    pairs, columns, guessed, cross, _a = svc._extract_pairs(
        None, "db_a", svc._NameSpace(["db_a"]))
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
                svc._NameSpace(["db_a"]),
                time.monotonic() + svc.TOTAL_BUDGET_SECONDS)
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
# Rev.G 定向回归（O 评审报告 §6 的 T-R01…T-R14）
# ══════════════════════════════════════════════════════════════════
def _uniq(prefix):
    """每个并发用例用独立 connection_id：registry 的按连接信号量按 id 缓存，
    复用同一个 id 会把上一个用例的限流配额带进来。"""
    return f"{prefix}-{random.randrange(10**9)}"


def test_r02_same_connection_concurrency_is_rejected(monkeypatch):
    """T-R02 / P1-02：同一连接的第二个请求被服务端限流；槽位在退出后释放。"""
    from backend import config
    from backend.services.connection_registry import registry, ScanBusyError
    monkeypatch.setattr(config, "max_concurrent_scans_per_connection", lambda: 1)
    monkeypatch.setattr(config, "max_concurrent_scans_global", lambda: 8)
    monkeypatch.setattr(svc, "_ensure_schema", lambda: None)
    _patch_ctx(monkeypatch, "centralized")
    pool = FakePool(databases=["db_a"], info_schema={"db_a": {"base": ["t1"]}})
    cid = _uniq("tr02")
    with registry.scan_slot(cid):
        with pytest.raises(ScanBusyError) as e:
            svc.run_stats(pool, connection_id=cid)
        assert "并发已达上限" in str(e.value)
    # 槽位已释放：同一连接可以再次进入
    with registry.scan_slot(cid):
        pass


def test_r02b_slot_is_released_when_collection_raises(monkeypatch):
    """T-R02 / P1-02：采集抛异常时槽位必须释放，不得泄漏成永久占用。"""
    from backend import config
    from backend.services.connection_registry import registry
    monkeypatch.setattr(config, "max_concurrent_scans_per_connection", lambda: 1)
    monkeypatch.setattr(svc, "_ensure_schema", lambda: None)

    def _boom(*a, **k):
        raise RuntimeError("采集炸了")

    monkeypatch.setattr(svc, "analyze", _boom)
    cid = _uniq("tr02b")
    with pytest.raises(RuntimeError):
        svc.run_stats(FakePool(), connection_id=cid)
    with registry.scan_slot(cid):        # 未泄漏
        pass


def test_r03_global_quota_is_shared_with_existing_scans(monkeypatch):
    """T-R03 / P1-02：表类型统计与既有扫描【共用】同一份全局配额。

    这条测试的意义不是"新功能能被限流"，而是"新功能不会另开一份配额"——
    若各算各的，全局上限就形同虚设，正是 O 指出的挤占既有审核/扫描的路径。
    """
    from backend import config
    from backend.services.connection_registry import registry, ScanBusyError
    monkeypatch.setattr(config, "max_concurrent_scans_global", lambda: 1)
    monkeypatch.setattr(config, "max_concurrent_scans_per_connection", lambda: 4)
    monkeypatch.setattr(svc, "_ensure_schema", lambda: None)
    _patch_ctx(monkeypatch, "centralized")
    pool = FakePool(databases=["db_a"], info_schema={"db_a": {"base": ["t1"]}})
    other = _uniq("tr03-other")
    mine = _uniq("tr03-mine")
    # 先由"既有扫描"占满全局槽位（scan_service.py:72 用的就是这个入口）
    with registry.scan_slot(other):
        with pytest.raises(ScanBusyError) as e:
            svc.run_stats(pool, connection_id=mine)
        assert "服务扫描并发已达上限" in str(e.value)
    # 反向：本模块占用时，既有扫描同样被挡住 —— 证明是同一份配额
    monkeypatch.setattr(svc, "analyze",
                        lambda *a, **k: _raise_inside_slot(registry, other))
    with pytest.raises(ScanBusyError):
        svc.run_stats(pool, connection_id=mine)


def _raise_inside_slot(registry, other_cid):
    """在本模块已持有槽位的情况下，模拟既有扫描来抢全局槽位。"""
    with registry.scan_slot(other_cid):
        return {}


def test_r02c_api_maps_scan_busy_to_429(monkeypatch):
    """T-R02 / P1-02：并发超限在 API 层映射为 429（与 tdsql_manage.py:432 同口径）。"""
    from fastapi import HTTPException
    from backend.api import table_type_stats as api
    from backend.services.connection_registry import ScanBusyError

    monkeypatch.setattr(api, "_pool", lambda cid: FakePool())

    def _busy(*a, **k):
        raise ScanBusyError("目标库 c1 扫描并发已达上限(2)，请稍后重试")

    monkeypatch.setattr(api.svc, "run_stats", _busy)
    with pytest.raises(HTTPException) as e:
        api.run(api.StatsRequest(connection_id="c1"), _FakeRequest("alice"))
    assert e.value.status_code == 429
    assert "并发已达上限" in e.value.detail


class _FakeRequest:
    def __init__(self, username=None):
        class _S:
            pass
        self.state = _S()
        if username is not None:
            self.state.username = username


def test_r04_broken_connection_is_rebuilt_before_next_db(monkeypatch):
    """T-R04 / P1-04：首库断链后连接被重建，次库用新连接并正常完成。"""
    _patch_ctx(monkeypatch, "distributed")
    per_db = {
        ("db_a", svc.SQL_SHARD): _mysql_error(
            2013, "Lost connection to MySQL server during query"),
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
    # 每库一个连接上下文（Rev.F 是全程一个）
    assert pool.ctx_count == 2
    # db_a 的异常穿出了 with ⇒ 真实池会关闭并重建线程本地连接
    assert pool.generation == 1, "异常必须穿出 with，否则坏连接不会被重建"
    gens = dict(pool.conn_ids)
    assert gens["db_a"] == 0 and gens["db_b"] == 1, "db_b 必须用重建后的新连接"
    by_db = {i["db_name"]: i for i in res["items"]}
    assert by_db["db_a"]["status"] == "FAILED"
    assert by_db["db_b"]["status"] == "OK" and by_db["db_b"]["total_tables"] == 1
    assert res["total_tables"] == 1


def test_r04b_read_timeout_also_rebuilds(monkeypatch):
    """T-R04：读超时同样必须穿出 with（超时后连接里可能残留未读结果集）。"""
    _patch_ctx(monkeypatch, "distributed")
    per_db = {("db_a", svc.SQL_SHARD): Exception("Read timed out"),
              ("db_b", svc.SQL_SHARD): _rows(["db_b.s2"]),
              ("db_b", svc.SQL_BROADCAST): [],
              ("db_b", svc.SQL_SINGLE): []}
    pool = FakePool(databases=["db_a", "db_b"],
                    info_schema={"db_a": {"base": ["s1"]},
                                 "db_b": {"base": ["s2"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert pool.generation == 1
    assert "读超时" in by_db_detail(res)["db_a"]
    assert res["total_tables"] == 1


def test_r05_failed_db_partial_result_does_not_pollute(monkeypatch):
    """T-R05 / P1-05：第一条命令返回跨库行、第二条失败 ⇒ 整库丢弃，不污染他库。"""
    _patch_ctx(monkeypatch, "distributed")
    per_db = {
        # db_a 的分片命令返回了实例级结果（含 db_b 的一张幽灵表），随后广播命令失败
        ("db_a", svc.SQL_SHARD): _rows(["db_a.s1", "db_b.ghost"],
                                       info="shardkey:id"),
        ("db_a", svc.SQL_BROADCAST): _mysql_error(1142, "SELECT command denied"),
        ("db_b", svc.SQL_SHARD): _rows(["db_b.s2"], info="shardkey:id"),
        ("db_b", svc.SQL_BROADCAST): [],
        ("db_b", svc.SQL_SINGLE): [],
    }
    pool = FakePool(databases=["db_a", "db_b"],
                    info_schema={"db_a": {"base": ["s1"]},
                                 "db_b": {"base": ["s2", "ghost"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    by_db = {i["db_name"]: i for i in res["items"]}
    assert by_db["db_a"]["status"] == "FAILED"
    # db_b 只保留它自己那一轮扫出来的 s2；db_a 那轮暂存的 ghost 已被整体丢弃
    assert by_db["db_b"]["total_tables"] == 1
    assert by_db["db_b"]["shard_tables"] == 1
    assert res["total_tables"] == 1
    assert res["failed_databases"] == 1
    # ghost 只在基线里，于是被如实报成"仅基线可见"，而不是被脏数据凑成 OK
    assert any(w["code"] == "RECON_MISMATCH" for w in res["warnings"])


def test_r05b_overlap_is_not_polluted_by_failed_db(monkeypatch):
    """T-R05 / P1-05：失败库的暂存结果不得进入重叠数统计。"""
    _patch_ctx(monkeypatch, "distributed")
    per_db = {
        ("db_a", svc.SQL_SHARD): _rows(["db_b.s2"]),      # 与 db_b 自扫结果同一张表
        ("db_a", svc.SQL_BROADCAST): _mysql_error(1142, "denied"),
        ("db_b", svc.SQL_SHARD): [],
        ("db_b", svc.SQL_BROADCAST): _rows(["db_b.s2"]),
        ("db_b", svc.SQL_SINGLE): [],
    }
    pool = FakePool(databases=["db_a", "db_b"],
                    info_schema={"db_a": {"base": []},
                                 "db_b": {"base": ["s2"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert res["overlap_count"] == 0, "失败库的暂存行不得参与重叠判定"
    by_db = {i["db_name"]: i for i in res["items"]}
    assert by_db["db_b"]["broadcast_tables"] == 1
    assert by_db["db_b"]["shard_tables"] == 0


def test_r06_centralized_keeps_legit_subp_named_table(monkeypatch):
    """T-R06 / P1-03：集中式实例的 `orders_tdsql_subp202601` 是合法业务表，必须计入。

    集中式没有二级分区物理子表这一构造，也没有 Proxy 交叉校验兜底——
    按后缀剔除就是静默少算，且不可见（违反 REQ-5）。
    """
    _patch_ctx(monkeypatch, "centralized")
    pool = FakePool(databases=["db_a"],
                    info_schema={"db_a": {"base": [
                        "orders", "orders_tdsql_subp202601",
                        "cus_pub_translog_tdsql_subp190001"]}})
    res = svc.analyze(pool, connection_id="c1")
    assert res["total_tables"] == 3
    assert res["single_tables"] == 3
    assert res["baseline_tables"] == 3
    assert res["subpartition_tables"] == 0
    assert not any(w["code"] == "SUBPARTITION_EXCLUDED" for w in res["warnings"])


def test_r07_distributed_requires_confirmed_parent(monkeypatch):
    """T-R07 / P1-03：分布式也不能只凭后缀——父表必须确实出现在 Proxy 结果里。

    db_a：父表 orders 在 Proxy 结果中 ⇒ 子表判定成立，剔除。
    db_b：父表 legacy 不在 Proxy 结果中 ⇒ 保留为逻辑表，并由 RECON_MISMATCH
          把这条不确定性【显式】报出来（可见），而不是静默少算（不可见）。
    """
    _patch_ctx(monkeypatch, "distributed")
    per_db = {
        ("db_a", svc.SQL_SHARD): _rows(["db_a.orders"], info="shardkey:id"),
        ("db_a", svc.SQL_BROADCAST): [],
        ("db_a", svc.SQL_SINGLE): [],
        ("db_b", svc.SQL_SHARD): [],
        ("db_b", svc.SQL_BROADCAST): [],
        ("db_b", svc.SQL_SINGLE): _rows(["db_b.other"]),
    }
    pool = FakePool(
        databases=["db_a", "db_b"],
        info_schema={"db_a": {"base": ["orders", "orders_tdsql_subp202601"]},
                     "db_b": {"base": ["other", "legacy_tdsql_subp202601"]}},
        per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    by_db = {i["db_name"]: i for i in res["items"]}
    assert by_db["db_a"]["subpartition_tables"] == 1     # 父表已确认 ⇒ 剔除
    assert by_db["db_a"]["baseline_tables"] == 1
    assert by_db["db_b"]["subpartition_tables"] == 0     # 父表未确认 ⇒ 不剔除
    assert by_db["db_b"]["baseline_tables"] == 2
    assert "legacy_tdsql_subp202601" in by_db["db_b"]["detail"], \
        "未确认的后缀表必须在明细里被点名，不能悄悄消失"
    assert any(w["code"] == "RECON_MISMATCH" for w in res["warnings"])


def test_r09_five_hundred_failed_databases_is_bounded(monkeypatch):
    """T-R09 / P1-07：500 库全失败时告警可序列化、体积受控、前端条数受控。"""
    import json as _json
    _patch_ctx(monkeypatch, "distributed")
    dbs = [f"db_{i:03d}" for i in range(500)]
    long_msg = ("SELECT command denied to user 'audit'@'10.0.0.1' "
                "for table 't_business_transaction_detail_history'") * 3
    per_db = {(d, svc.SQL_SHARD): _mysql_error(1142, long_msg) for d in dbs}
    pool = FakePool(databases=dbs,
                    info_schema={d: {"base": [f"t_{d}"]} for d in dbs},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert res["failed_databases"] == 500
    assert res["total_tables"] == 0
    w = [x for x in res["warnings"] if x["code"] == "PROXY_CMD_FAILED"]
    assert len(w) == 1, "500 库失败必须汇总为一条告警，不是 500 条"
    assert "500 个库采集失败" in w[0]["detail"]
    blob = _json.dumps(res["warnings"], ensure_ascii=False)
    assert len(blob.encode("utf-8")) < 8 * 1024, \
        f"warnings_json 体积失控: {len(blob.encode('utf-8'))} 字节"
    assert len(res["warnings"]) <= 6, "前端横幅条数必须受控"
    # 逐库原因没有丢，只是下沉到了明细行
    details = by_db_detail(res)
    assert len(details) == 500 and all(details.values())
    assert all(len(d) <= 512 for d in details.values())


def test_r10_centralized_nonexistent_database_is_rejected(monkeypatch):
    """T-R10 / P2-01：指定不存在的库必须报错，不得回"成功、0 张表"。"""
    _patch_ctx(monkeypatch, "centralized")
    pool = FakePool(databases=["db_a"], info_schema={"db_a": {"base": ["t1"]}})
    with pytest.raises(ValueError) as e:
        svc.analyze(pool, connection_id="c1", database="nosuch")
    assert "不存在" in str(e.value)
    # 存在但为空的库仍然正常返回 0，两者可区分
    pool2 = FakePool(databases=["db_a", "db_empty"],
                     info_schema={"db_a": {"base": ["t1"]},
                                  "db_empty": {"base": []}})
    res = svc.analyze(pool2, connection_id="c1", database="db_empty")
    assert res["total_tables"] == 0 and res["items"][0]["status"] == "OK"


def test_r10b_show_databases_failure_is_not_silent(monkeypatch):
    """T-R10 / P2-01：库枚举失败不得被吞成"空库"。"""
    _patch_ctx(monkeypatch, "centralized")
    pool = FakePool(show_db_fail=_mysql_error(1045, "Access denied for user"))
    with pytest.raises(Exception) as e:
        svc.analyze(pool, connection_id="c1")
    assert "Access denied" in str(e.value)


def test_r11_empty_connection_id_is_rejected():
    """T-R11 / P2-03：空 connection_id 下连接解析与实例类型解析可能指向不同实例。"""
    with pytest.raises(ValueError) as e:
        svc.run_stats(FakePool(), connection_id="")
    assert "connection_id" in str(e.value)
    with pytest.raises(ValueError):
        svc.run_stats(FakePool(), connection_id="   ")


def test_r11b_api_model_requires_connection_id():
    """T-R11 / P2-03：接口契约层就必须挡住空 connection_id。"""
    import pydantic
    from backend.api.table_type_stats import StatsRequest
    assert StatsRequest(connection_id="c1").database == ""
    with pytest.raises(pydantic.ValidationError):
        StatsRequest(database="x")                 # 缺字段
    with pytest.raises(pydantic.ValidationError):
        StatsRequest(connection_id="", database="x")


def test_r13_api_records_current_operator(monkeypatch):
    """T-R13 / P2-02：API 必须把 request.state.username 传给 run_stats(operator=)。"""
    import inspect
    from fastapi import Request
    from backend.api import table_type_stats as api

    sig = inspect.signature(api.run)
    assert "http_request" in sig.parameters
    assert sig.parameters["http_request"].annotation is Request

    seen = {}

    def _spy(pool, connection_id="", database="", operator=""):
        seen["operator"] = operator
        return {"ok": True}

    monkeypatch.setattr(api, "_pool", lambda cid: FakePool())
    monkeypatch.setattr(api.svc, "run_stats", _spy)
    api.run(api.StatsRequest(connection_id="c1"), _FakeRequest("zhangsan"))
    assert seen["operator"] == "zhangsan"
    # 未认证兜底不得写空串（空串会让历史留档无法追责）
    api.run(api.StatsRequest(connection_id="c1"), _FakeRequest(None))
    assert seen["operator"] == "anonymous"


def test_r08_permission_key_is_registered_at_every_point():
    """T-R08 / P1-06：新权限键必须登记到全部 6 处，缺一处就有角色进不去。

    设计阶段（模块尚未落盘）自动跳过；Q 落盘后这条即成为硬门禁。
    """
    import pathlib
    import backend
    repo = pathlib.Path(backend.__file__).resolve().parent.parent
    if not (repo / "backend" / "api" / "table_type_stats.py").exists():
        pytest.skip("G14 尚未落盘（设计阶段）")
    perm = "deep-diag-tabletype"
    points = [
        ("backend/services/auth_service.py", perm),   # API 路径 → 权限键映射
        ("backend/services/database.py", perm),       # 默认角色权限清单
        ("frontend/index.html", perm),                # el-tab-pane v-if
        ("frontend/static/js/app.js", perm),          # subtabs 回退清单
    ]
    for rel, needle in points:
        text = (repo / rel).read_text(encoding="utf-8")
        assert needle in text, f"{rel} 未登记权限键 {needle}"
    # subtabs 是 P1-06 的正主：单独钉住，防止只加了 tab-pane 忘了回退清单
    app_js = (repo / "frontend/static/js/app.js").read_text(encoding="utf-8")
    line = [l for l in app_js.splitlines() if "const subtabs=" in l]
    assert line and f"perm:'{perm}'" in line[0], \
        "深度诊断子页签回退清单 subtabs 未登记新页签"


# ══════════════════════════════════════════════════════════════════
# Rev.J 定向回归（O 第二轮评审 §7 的 T2-R01…T2-R10）
# ══════════════════════════════════════════════════════════════════
def test_t2r01_case_variant_databases_are_not_merged(monkeypatch):
    """T2-R01 / P1-01：`Sales` 与 `sales` 是两个库，不得被合并成一个。

    Rev.I 用 `{name.lower(): name}` 单值字典，后者会覆盖前者：
    两库基线并进一个键、Proxy 行全归给字典里最后那个库、另一个库显示 0。
    这是四个主数字的静默错误——没有任何告警会亮。
    """
    _patch_ctx(monkeypatch, "distributed")
    per_db = {
        ("Sales", svc.SQL_SHARD): _rows(["Sales.t_upper"], info="shardkey:id"),
        ("Sales", svc.SQL_BROADCAST): [],
        ("Sales", svc.SQL_SINGLE): [],
        ("sales", svc.SQL_SHARD): [],
        ("sales", svc.SQL_BROADCAST): [],
        ("sales", svc.SQL_SINGLE): _rows(["sales.t_lower"]),
    }
    pool = FakePool(databases=["Sales", "sales"],
                    info_schema={"Sales": {"base": ["t_upper"]},
                                 "sales": {"base": ["t_lower"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")

    assert res["database_count"] == 2
    by_db = {i["db_name"]: i for i in res["items"]}
    assert set(by_db) == {"Sales", "sales"}, "两个库必须各自成行"
    assert by_db["Sales"]["shard_tables"] == 1 and by_db["Sales"]["single_tables"] == 0
    assert by_db["sales"]["single_tables"] == 1 and by_db["sales"]["shard_tables"] == 0
    assert by_db["Sales"]["baseline_tables"] == 1
    assert by_db["sales"]["baseline_tables"] == 1
    assert res["total_tables"] == 2
    # 两个口径精确对齐 ⇒ 不得出现虚假的 RECON_MISMATCH
    assert not any(w["code"] == "RECON_MISMATCH" for w in res["warnings"])
    # 但存在大小写变体这件事本身要如实告知
    assert any(w["code"] == "DB_NAME_CASE_VARIANTS" for w in res["warnings"])


def test_t2r01b_wrong_case_database_is_rejected(monkeypatch):
    """T2-R01 / P1-01：指定错误大小写的库不得被"当成存在"。"""
    _patch_ctx(monkeypatch, "centralized")
    pool = FakePool(databases=["sales"], info_schema={"sales": {"base": ["t1"]}})
    with pytest.raises(ValueError) as e:
        svc.analyze(pool, connection_id="c1", database="Sales")
    assert "不存在" in str(e.value)
    assert "sales" in str(e.value), "应提示实例上存在大小写不同的同名库"
    # 精确名当然可以
    assert svc.analyze(pool, connection_id="c1", database="sales")["total_tables"] == 1


def test_t2r01c_ambiguous_qualified_name_is_not_guessed(monkeypatch):
    """T2-R01 / P1-01：库限定名无法唯一归属时不猜，记 DB_NAME_AMBIGUOUS。

    宁可少算并显式报出，也不把两个真实的库算成一个。
    """
    ns = svc._NameSpace(["Sales", "sales"])
    assert ns.resolve("Sales") == "Sales"
    assert ns.resolve("sales") == "sales"
    assert ns.resolve("SALES") is None and ns.is_ambiguous("SALES")
    assert ns.resolve("other") is None and not ns.is_ambiguous("other")
    # 唯一候选时才允许大小写回退
    assert svc._NameSpace(["sales"]).resolve("SALES") == "sales"

    _patch_ctx(monkeypatch, "distributed")
    per_db = {}
    for d in ("Sales", "sales"):
        per_db[(d, svc.SQL_SHARD)] = _rows(["SALES.mystery"])
        per_db[(d, svc.SQL_BROADCAST)] = []
        per_db[(d, svc.SQL_SINGLE)] = []
    pool = FakePool(databases=["Sales", "sales"],
                    info_schema={"Sales": {"base": []}, "sales": {"base": []}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert res["total_tables"] == 0, "歧义行不得被猜给其中一个库"
    w = [x for x in res["warnings"] if x["code"] == "DB_NAME_AMBIGUOUS"]
    assert w and "SALES.mystery" in w[0]["detail"]


def test_t2r02_deadline_covers_prelude_and_every_command(monkeypatch):
    """T2-R02 / P1-02：deadline 覆盖前置查询，且**每条命令**开始前都检查。

    Rev.I 只在每个库开始时查一次预算，单库随后最多跑三条 30 秒命令
    ——179 秒进库能跑到 269 秒。这里用可控时钟把这条路径钉死。
    """
    _patch_ctx(monkeypatch, "distributed")
    clock = FakeClock()
    monkeypatch.setattr(svc, "_now", clock)
    deadline = clock.t + 100.0

    per_db = {}
    for d in ("db_a", "db_b"):
        per_db[(d, svc.SQL_SHARD)] = _rows([f"{d}.s"])
        per_db[(d, svc.SQL_BROADCAST)] = _rows([f"{d}.b"])
        per_db[(d, svc.SQL_SINGLE)] = _rows([f"{d}.n"])
    pool = FakePool(databases=["db_a", "db_b"],
                    info_schema={"db_a": {"base": ["s", "b", "n"]},
                                 "db_b": {"base": ["s", "b", "n"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)

    # 每条命令耗 60 秒：db_a 跑完两条即超预算，第三条不得再启动
    executed = []
    orig_pairs = svc._extract_pairs

    def _slow_pairs(rows, cur, known):
        executed.append(cur)
        clock.advance(60.0)          # 每条命令耗 60 秒
        return orig_pairs(rows, cur, known)

    monkeypatch.setattr(svc, "_extract_pairs", _slow_pairs)
    res = svc.analyze(pool, connection_id="c1", deadline=deadline)

    # db_a：第 1 条（0→60）、第 2 条（60→120，启动时 60<100 允许）执行；
    #       第 3 条启动时已 120>=100，抛预算 → db_a 整库 SKIPPED
    assert len(executed) == 2, f"deadline 之后不得再启动新命令，实际执行 {len(executed)} 条"
    by_db = {i["db_name"]: i for i in res["items"]}
    assert by_db["db_a"]["status"] == "SKIPPED"
    assert by_db["db_b"]["status"] == "SKIPPED"
    assert res["skipped_databases"] == 2
    assert res["total_tables"] == 0


def test_t2r02b_deadline_blocks_baseline_query(monkeypatch):
    """T2-R02 / P1-02：前置基线查询也在预算内（Rev.I 完全不计它）。"""
    _patch_ctx(monkeypatch, "centralized")
    clock = FakeClock()
    monkeypatch.setattr(svc, "_now", clock)
    pool = FakePool(databases=["db_a"], info_schema={"db_a": {"base": ["t1"]}})
    with pytest.raises(TimeoutError) as e:
        svc.analyze(pool, connection_id="c1", deadline=clock.t - 1)
    assert "预算" in str(e.value)


def test_t2r03_failed_db_rows_do_not_enter_any_rollup(monkeypatch):
    """T2-R03 / P1-03：失败库的跨库行不得进入 overlap / baseline / subp 汇总。"""
    _patch_ctx(monkeypatch, "distributed")
    # db_a 成功，其实例级返回同时带回 db_b.t1，且 t1 在两个类型中出现（重叠）
    per_db = {
        ("db_a", svc.SQL_SHARD): _rows(["db_a.s1", "db_b.t1"]),
        ("db_a", svc.SQL_BROADCAST): _rows(["db_b.t1"]),
        ("db_a", svc.SQL_SINGLE): [],
        ("db_b", svc.SQL_SHARD): _mysql_error(1142, "SELECT command denied"),
    }
    subp = [f"t1_tdsql_subp2026{m:02d}" for m in range(1, 13)]
    pool = FakePool(databases=["db_a", "db_b"],
                    info_schema={"db_a": {"base": ["s1"]},
                                 "db_b": {"base": ["t1"] + subp}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")

    by_db = {i["db_name"]: i for i in res["items"]}
    assert by_db["db_b"]["status"] == "FAILED"
    assert res["overlap_count"] == 0, "失败库的跨库行不得计入重叠"
    assert res["baseline_tables"] == 1, "只应含 db_a 的 s1"
    assert res["subpartition_tables"] == 0, "失败库的子分区不得进汇总"
    assert not any(w["code"] == "SUBPARTITION_EXCLUDED" for w in res["warnings"])
    # 逐库行仍如实显示 db_b 的基线（information_schema 那侧确实查成功了）
    assert by_db["db_b"]["baseline_tables"] >= 1


def test_t2r07_candidate_in_proxy_is_a_logical_table(monkeypatch):
    """T2-R07 / P2-01：候选自身出现在 Proxy 结果中 ⇒ 它是逻辑表，不是物理子表。

    真正的物理子表根本不会被 /*proxy*/show table 返回——"它自己也在 Proxy 结果里"
    直接证伪了"它是物理子表"。
    """
    _patch_ctx(monkeypatch, "distributed")
    per_db = {
        ("db_a", svc.SQL_SHARD): _rows(["db_a.orders",
                                        "db_a.orders_tdsql_subp202601"]),
        ("db_a", svc.SQL_BROADCAST): [],
        ("db_a", svc.SQL_SINGLE): [],
    }
    pool = FakePool(
        databases=["db_a"],
        info_schema={"db_a": {"base": ["orders", "orders_tdsql_subp202601"]}},
        per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert res["subpartition_tables"] == 0, "两张都是逻辑表，不得剔除任何一张"
    assert res["baseline_tables"] == 2
    assert res["total_tables"] == 2
    assert not any(w["code"] == "RECON_MISMATCH" for w in res["warnings"]), \
        "不得凭空产生'仅 Proxy 可见'的虚假差异"
    assert not any(w["code"] == "SUBPARTITION_EXCLUDED" for w in res["warnings"])


def test_t2r07b_real_subpartition_is_still_stripped(monkeypatch):
    """T2-R07 反向：真正的子表（不在 Proxy 结果里）仍必须被剔除——不得因 P2-01 回退。"""
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
    assert res["subpartition_tables"] == 13
    assert res["baseline_tables"] == 1


def test_t3r07_destructive_guard_rejects_non_test_database(monkeypatch):
    """T3-R07 / P1-04：目标不是批准的测试库时，必须在任何 DROP 之前失败关闭。

    这条用例本身不连数据库——它验证的是"守门人会不会放行"，
    所以在任何环境下都必须执行，不能挂 MYSQL_AVAILABLE。
    """
    from backend.services import database as dbmod
    for bad in ("tdsql_sqlcheck", "production", ""):
        monkeypatch.setitem(dbmod.MYSQL_CONFIG, "database", bad)
        with pytest.raises(DestructiveTargetError) as e:
            assert_destructive_target_is_safe()
        assert "拒绝在非批准的数据库上执行 DROP" in str(e.value)
    # 批准的库名放行
    monkeypatch.setitem(dbmod.MYSQL_CONFIG, "database", _APPROVED_TEST_DB)
    assert assert_destructive_target_is_safe()["database"] == _APPROVED_TEST_DB


def test_t3r07b_custom_test_db_needs_explicit_opt_in(monkeypatch):
    """T3-R07 / P1-04：自定义测试库必须【同时】给出显式破坏性开关才放行。"""
    from backend.services import database as dbmod
    import backend.api.table_type_stats as _api          # noqa: F401  (确保包已加载)
    mod = sys.modules[__name__]
    monkeypatch.setitem(dbmod.MYSQL_CONFIG, "database", "my_scratch_db")
    # 只给库名、不给开关 → 仍然拒绝
    monkeypatch.setattr(mod, "_ALLOW_DESTRUCTIVE", False)
    monkeypatch.setattr(mod, "_CUSTOM_TEST_DB", "my_scratch_db")
    with pytest.raises(DestructiveTargetError):
        assert_destructive_target_is_safe()
    # 两者齐全 → 放行
    monkeypatch.setattr(mod, "_ALLOW_DESTRUCTIVE", True)
    assert assert_destructive_target_is_safe()["database"] == "my_scratch_db"


def test_t3r07c_probe_and_execution_use_the_same_config():
    """T3-R07 / P1-04：可用性探测与实际执行必须是同一份配置。

    Rev.J 用 TDSQL_TEST_* 探测、用 SQLCHECK_DB_* 执行，两套可以指向不同服务器：
    探测通了就不 skip，然后往生产元数据库执行 DROP。
    """
    import inspect
    src = inspect.getsource(_probe_metadata_db)
    assert "effective_db_config()" in src, "探测必须读 database 模块生效中的配置"
    assert "TDSQL_TEST" not in src, "探测不得再使用另一套 TDSQL_TEST_* 配置"
    assert 'database=cfg["database"]' in src, "探测必须选中后续 DROP 使用的目标库"
    cfg = effective_db_config()
    assert set(("host", "port", "user", "password", "database")) <= set(cfg)


# ── DEF-1 / T2-R06：迁移槽位护栏 ────────────────────────────────────
_OUR_SLOT = (13, 130)
_OUR_SQL_NAME = "130_table_type_stats.sql"


def scan_migration_slots(schema_dir):
    """扫描 schema 目录，返回 {(version, sequence): [文件名, ...]}。

    Rev.J / P1-05：**值必须是列表**。Rev.I 用的是单值字典
    `taken[(v, s)] = filename`，同一槽位有两个文件时后者会覆盖前者——
    那个护栏恰好看不见 DEF-1 想防的场景（同槽重复），等于没防。
    """
    import pathlib
    slots = {}
    d = pathlib.Path(schema_dir)
    if not d.is_dir():
        return slots
    for vdir in sorted(d.iterdir()):
        if not (vdir.is_dir() and len(vdir.name) > 1
                and vdir.name[0] == "v" and vdir.name[1:].isdigit()):
            continue
        for f in sorted(vdir.iterdir()):
            if f.suffix != ".sql" or len(f.name) < 4 or not f.name[:3].isdigit():
                continue
            slots.setdefault((int(vdir.name[1:]), int(f.name[:3])), []).append(f.name)
    return slots


def _schema_dir():
    import pathlib
    import backend
    return pathlib.Path(backend.__file__).resolve().parent / "schema"


# 选槽时的已知前驱（v1.6.2.2 的 O-22 引入）。用它证明"当时选槽是连续的"，
# 而不是用"我们永远是最大槽"——后者会阻断项目此后的任何迁移。
_PREDECESSOR_SLOT = (12, 120)


def assert_slot_ok(slots):
    """迁移槽位的**永久不变量**。落盘前、落盘后、以及此后新增任意迁移都成立。

    Rev.K / P1-03：Rev.J 里还有一条 `assert _OUR_SLOT > max(others)`，
    它在项目下一次合法新增 `v13/131` 或 `v14/140` 之后**必然失败**——
    等于用一条历史测试禁止项目继续演进。我甚至把"我们不再是最大槽"写成了
    必须拒绝的场景，方向完全错了。

    真正的永久不变量只有两条：
      ① 任何 `(version, sequence)` 槽位不得有两个文件（DEF-1 想防的就是这个）；
      ② 我们的槽位有且只有我们自己的文件。
    "当前最大之后的下一个"只在**选槽那一刻**有意义，不该随仓库永久执行。
    需要证明当时选槽连续，就断言已知前驱 v12/120 存在——这条同样永久成立。
    """
    dups = {k: v for k, v in slots.items() if len(v) > 1}
    assert not dups, f"存在同槽多文件（DEF-1 形态）: {dups}"

    occupied = slots.get(_OUR_SLOT)
    if occupied is not None:
        assert occupied == [_OUR_SQL_NAME], \
            f"迁移槽位 v13/130 被 {occupied} 占用，本模块必须独占该槽"
    # ③ 选槽连续性：前驱必须仍在（防有人误删 v12/120 造成版本链断裂）。
    #    仅在仓库已有迁移文件时校验，合成的空目录不参与。
    if slots and _OUR_SLOT in slots:
        assert _PREDECESSOR_SLOT in slots, \
            f"已知前驱迁移 v12/120 不见了，版本链断裂：{sorted(slots)}"


def test_migration_slot_scanner_detects_duplicates(tmp_path):
    """扫描器本身必须能看见"同槽两个文件"——这正是 DEF-1 的形态。

    Rev.I 的护栏用单值字典保存占用者，同槽第二个文件会覆盖第一个，
    于是重复根本报不出来。先把扫描器钉死，护栏才有意义。
    """
    (tmp_path / "v11").mkdir()
    (tmp_path / "v11" / "110_index_finding_structured.sql").write_text("-- a")
    (tmp_path / "v11" / "110_table_type_stats.sql").write_text("-- b")
    (tmp_path / "v12").mkdir()
    (tmp_path / "v12" / "120_gateway_report_tickets.sql").write_text("-- c")
    (tmp_path / "notaversion").mkdir()
    (tmp_path / "notaversion" / "999_x.sql").write_text("-- ignored")

    slots = scan_migration_slots(tmp_path)
    assert slots[(11, 110)] == ["110_index_finding_structured.sql",
                                "110_table_type_stats.sql"]
    assert slots[(12, 120)] == ["120_gateway_report_tickets.sql"]
    assert all(k[0] != 999 for k in slots), "非 vN 目录不得计入"
    dups = {k: v for k, v in slots.items() if len(v) > 1}
    assert dups, "同槽两个文件必须被识别为重复"


def test_migration_slot_is_available_and_unique():
    """T2-R06 / DEF-1：v13/130 槽位必须唯一、可用，且**落盘前后都成立**。

    Rev.I 的写法是：
        assert ours not in taken or taken[ours].startswith("130_table_type_stats")
        assert ours > max(taken)
    第二句在迁移文件真正落盘后必然失败——那时 max(taken) == ours，
    `ours > max(taken)` 为假。**一条为了防错而写的护栏，会在模块上线当天把构建打红。**
    设计阶段之所以"通过"，只是因为文件还不存在，等于什么都没验证。

    Rev.K 之后的正确写法只钉永久不变量：
      ① 全局没有任何槽位重复；
      ② 若我们的文件已落盘，v13/130 下有且只有它；
      ③ 已知前驱 v12/120 仍然存在。
    后续 v13/131、v14/140 等合法迁移出现时仍必须通过；
    因此绝不再断言"本模块永远是最大槽"。
    """
    assert_slot_ok(scan_migration_slots(_schema_dir()))


def test_migration_slot_guard_passes_before_and_after_landing():
    """T2-R06 关键点：护栏在**迁移文件真正落盘之后**必须依然通过。

    Rev.I 的护栏做不到这一点——`assert ours > max(taken)` 在文件落盘后必然为假。
    这里用两份合成状态直接把两个阶段都验一遍，不用等到上线当天才发现。
    """
    current = {(11, 110): ["110_index_finding_structured.sql"],
               (12, 120): ["120_gateway_report_tickets.sql"]}
    assert_slot_ok(dict(current))                       # 阶段一：尚未落盘
    landed = dict(current); landed[_OUR_SLOT] = [_OUR_SQL_NAME]
    assert_slot_ok(landed)                              # 阶段二：已落盘 ← Rev.I 会在这里失败


def test_t3r05_guard_does_not_block_future_migrations():
    """T3-R05 / P1-03：本模块落盘之后，项目继续新增迁移**必须**照样通过。

    Rev.J 的 `assert _OUR_SLOT > max(others)` 在出现 v13/131 或 v14/140 之后
    必然为假——一条历史测试把项目此后的演进锁死了。这里把这两种未来状态直接钉住。
    """
    base = {(11, 110): ["110_index_finding_structured.sql"],
            (12, 120): ["120_gateway_report_tickets.sql"],
            _OUR_SLOT: [_OUR_SQL_NAME]}
    assert_slot_ok({**base, (13, 131): ["131_next_feature.sql"]})
    assert_slot_ok({**base, (14, 140): ["140_future.sql"]})
    assert_slot_ok({**base, (13, 131): ["131_a.sql"], (14, 140): ["140_b.sql"],
                    (15, 150): ["150_c.sql"]})


def test_t3r06_guard_rejects_duplicate_and_squatter():
    """T3-R06 / P1-03：护栏必须真的会拒——但只拒该拒的两种。"""
    base = {(11, 110): ["110_index_finding_structured.sql"],
            (12, 120): ["120_gateway_report_tickets.sql"]}
    # 同一 (version, sequence) 出现两个文件 —— DEF-1 的真实形态
    with pytest.raises(AssertionError, match="同槽多文件"):
        assert_slot_ok({**base, (11, 110): ["110_a.sql", "110_b.sql"]})
    # 我们的槽位被别人占
    with pytest.raises(AssertionError, match="必须独占该槽"):
        assert_slot_ok({**base, _OUR_SLOT: ["130_someone_else.sql"]})
    # 我们的槽位里混进第二个文件
    with pytest.raises(AssertionError, match="同槽多文件"):
        assert_slot_ok({**base, _OUR_SLOT: [_OUR_SQL_NAME, "130_other.sql"]})
    # 前驱被误删 → 版本链断裂
    with pytest.raises(AssertionError, match="版本链断裂"):
        assert_slot_ok({(11, 110): ["110_index_finding_structured.sql"],
                        _OUR_SLOT: [_OUR_SQL_NAME]})


def test_migration_slot_guard_would_reject_a_taken_slot(tmp_path):
    """从真实目录扫出来的"槽位被别人占"同样必须被拒——端到端验一次扫描器 + 护栏。"""
    (tmp_path / "v12").mkdir()
    (tmp_path / "v12" / "120_gateway_report_tickets.sql").write_text("-- pred")
    (tmp_path / "v13").mkdir()
    (tmp_path / "v13" / "130_someone_else.sql").write_text("-- squatter")
    with pytest.raises(AssertionError, match="必须独占该槽"):
        assert_slot_ok(scan_migration_slots(tmp_path))


# ══════════════════════════════════════════════════════════════════
# 落库与结构验收（需本地元数据库）
# ══════════════════════════════════════════════════════════════════
def _ddl_path():
    """建表 DDL 的位置：落盘后是 backend/schema/v13/130_table_type_stats.sql，
    设计阶段在本文件旁边。测试直接读 DDL 文件本身，保证"文档里的建表语句"
    和"服务的结构验收"是同一份真相（P1-08 的账要能对上）。"""
    import pathlib
    import backend
    here = pathlib.Path(__file__).parent / "130_table_type_stats.sql"
    if here.exists():
        return here
    repo = pathlib.Path(backend.__file__).resolve().parent.parent
    return repo / "backend" / "schema" / "v13" / "130_table_type_stats.sql"


def _exec_sql(*statements):
    from backend.services.database import _get_connection
    conn = _get_connection()
    try:
        for st in statements:
            st = st.strip()
            if st:
                conn.execute(st)
        conn.commit()
    finally:
        conn.close()


def _strip_sql_comments(text):
    """去掉整行 -- 注释后按分号切分，保留真正的语句。"""
    body = "\n".join(l for l in text.splitlines()
                     if not l.strip().startswith("--"))
    return [st for st in body.split(";") if st.strip()]


def _reset_g14_tables():
    """删表重建，回到 DDL 定义的干净状态。**每次都先过目标保护。**"""
    assert_destructive_target_is_safe()
    ddl = _ddl_path().read_text(encoding="utf-8")
    _exec_sql("DROP TABLE IF EXISTS table_type_stat_item",
              "DROP TABLE IF EXISTS table_type_stat")
    _exec_sql(*_strip_sql_comments(ddl))


@pytest.fixture()
def g14_schema():
    # Rev.K / P1-04：DROP 之前先做目标失败关闭断言，用的是 database 模块
    # 【已生效】的配置，不是环境变量快照，也不再靠 setdefault"兜底"。
    assert_destructive_target_is_safe()
    from backend.services.database import ensure_db
    ensure_db()
    _reset_g14_tables()
    yield
    _reset_g14_tables()


@pytest.mark.skipif(not MYSQL_AVAILABLE, reason="MySQL 测试环境未启动")
def test_run_stats_persists(monkeypatch, g14_schema):
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
def test_r13_created_by_is_persisted(monkeypatch, g14_schema):
    """T-R13 / P2-02：操作人真正落到 created_by，历史可回看可追责。"""
    _patch_ctx(monkeypatch, "centralized")
    pool = FakePool(databases=["db_a"], info_schema={"db_a": {"base": ["t1"]}})
    res = svc.run_stats(pool, connection_id="qa", operator="zhangsan")
    hist = svc.list_history("qa", limit=1)
    assert hist[0]["created_by"] == "zhangsan"
    assert hist[0]["id"] == res["stat_id"]
    # /history 支持不带 connection_id 的全量回看
    assert any(h["id"] == res["stat_id"] for h in svc.list_history(limit=5))


@pytest.mark.skipif(not MYSQL_AVAILABLE, reason="MySQL 测试环境未启动")
def test_r09b_large_warnings_survive_round_trip(monkeypatch, g14_schema):
    """T-R09 / P1-07：500 库失败的告警必须能落库并原样回读（TEXT 会截断）。"""
    _patch_ctx(monkeypatch, "distributed")
    dbs = [f"db_{i:03d}" for i in range(500)]
    per_db = {(d, svc.SQL_SHARD): _mysql_error(
        1142, "SELECT command denied to user 'audit'@'10.0.0.1'") for d in dbs}
    pool = FakePool(databases=dbs,
                    info_schema={d: {"base": [f"t_{d}"]} for d in dbs},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.run_stats(pool, connection_id="qa", operator="pytest")
    back = svc.get_detail(res["stat_id"])
    assert back["warnings"] == res["warnings"], "告警回读必须与写入一致（无截断）"
    assert len(back["items"]) == 500
    assert all(i["detail"] for i in back["items"])


@pytest.mark.skipif(not MYSQL_AVAILABLE, reason="MySQL 测试环境未启动")
def test_get_detail_missing_id_is_graceful(g14_schema):
    out = svc.get_detail(99999999)
    assert out == {"items": [], "warnings": []}


# ── T-R12 / P1-08：畸形同名表必须失败关闭 ────────────────────────────
@pytest.mark.skipif(not MYSQL_AVAILABLE, reason="MySQL 测试环境未启动")
def test_r12_missing_table_fails_closed(g14_schema):
    """场景一：迁移登记后表被删除。迁移器不会重放纯 CREATE TABLE，
    _structure_state() 也照样返回 valid —— 只能靠本模块自验。"""
    _exec_sql("DROP TABLE IF EXISTS table_type_stat_item",
              "DROP TABLE IF EXISTS table_type_stat")
    with pytest.raises(svc.SchemaNotReadyError) as e:
        svc._ensure_schema()
    assert "缺少表 table_type_stat" in str(e.value)


@pytest.mark.skipif(not MYSQL_AVAILABLE, reason="MySQL 测试环境未启动")
def test_r12b_missing_column_fails_closed(g14_schema):
    """场景二：同名但缺列的历史残留表 —— CREATE TABLE IF NOT EXISTS 会静默跳过。"""
    _exec_sql("ALTER TABLE table_type_stat DROP COLUMN subpartition_tables")
    with pytest.raises(svc.SchemaNotReadyError) as e:
        svc._ensure_schema()
    assert "缺少列" in str(e.value) and "subpartition_tables" in str(e.value)


@pytest.mark.skipif(not MYSQL_AVAILABLE, reason="MySQL 测试环境未启动")
def test_r12c_wrong_type_fails_closed(g14_schema):
    """场景三：列类型错误。warnings_json 退回 TEXT 就是 P1-07 的成因，必须挡住。"""
    _exec_sql("ALTER TABLE table_type_stat MODIFY COLUMN warnings_json TEXT")
    with pytest.raises(svc.SchemaNotReadyError) as e:
        svc._ensure_schema()
    assert "类型不符" in str(e.value) and "warnings_json" in str(e.value)
    _reset_g14_tables()
    _exec_sql("ALTER TABLE table_type_stat_item "
              "MODIFY COLUMN total_tables VARCHAR(32) DEFAULT '0'")
    with pytest.raises(svc.SchemaNotReadyError) as e:
        svc._ensure_schema()
    assert "total_tables" in str(e.value)


@pytest.mark.skipif(not MYSQL_AVAILABLE, reason="MySQL 测试环境未启动")
def test_r12d_missing_index_fails_closed(g14_schema):
    """场景四：缺索引。不影响正确性但会让 /history 在留档积累后全表扫描。"""
    _exec_sql("DROP INDEX idx_tts_created ON table_type_stat")
    with pytest.raises(svc.SchemaNotReadyError) as e:
        svc._ensure_schema()
    assert "索引不符" in str(e.value) and "idx_tts_created" in str(e.value)


@pytest.mark.skipif(not MYSQL_AVAILABLE, reason="MySQL 测试环境未启动")
def test_r12e_run_stats_fails_before_collecting(monkeypatch, g14_schema):
    """T-R12：结构验收必须发生在【采集之前】——否则用户白等一轮 180 秒才收 500。"""
    _exec_sql("DROP TABLE IF EXISTS table_type_stat_item")
    _patch_ctx(monkeypatch, "centralized")
    pool = FakePool(databases=["db_a"], info_schema={"db_a": {"base": ["t1"]}})
    called = {"n": 0}
    real_analyze = svc.analyze

    def _counting(*a, **k):
        called["n"] += 1
        return real_analyze(*a, **k)

    monkeypatch.setattr(svc, "analyze", _counting)
    with pytest.raises(svc.SchemaNotReadyError):
        svc.run_stats(pool, connection_id="qa")
    assert called["n"] == 0, "结构不合格时不得先跑一轮采集"


# ── T3-R08 / P2-01：完整字段契约的参数化定向用例 ────────────────────
# Rev.J 正文声称"九种畸形场景由十项单测钉住"，但附录里实际只有缺表/缺列/错类型/
# 缺索引/采集前拦截/列清单一致六类——长度、自增、默认值、可空性、字符集、索引列序
# 都只是我在本地手工 ALTER 跑过一次。**人工跑过一次不能防止落盘实现或未来修改回退。**
# 现在逐条落成参数化用例。每条形如 (用例名, 破坏语句, 恢复语句, 期望错误关键词)。
_CONTRACT_CASES = [
    ("detail 长度收窄",
     "ALTER TABLE table_type_stat_item MODIFY COLUMN detail VARCHAR(16) DEFAULT ''",
     "ALTER TABLE table_type_stat_item MODIFY COLUMN detail VARCHAR(512) DEFAULT ''",
     ("detail", "长度不符")),
    ("connection_id 长度收窄",
     "ALTER TABLE table_type_stat MODIFY COLUMN connection_id VARCHAR(8) DEFAULT ''",
     "ALTER TABLE table_type_stat MODIFY COLUMN connection_id VARCHAR(64) DEFAULT ''",
     ("connection_id", "长度不符")),
    ("id 丢失 AUTO_INCREMENT",
     "ALTER TABLE table_type_stat MODIFY COLUMN id INT NOT NULL",
     "ALTER TABLE table_type_stat MODIFY COLUMN id INT NOT NULL AUTO_INCREMENT",
     ("id", "AUTO_INCREMENT")),
    ("created_at 缺默认值",
     "ALTER TABLE table_type_stat MODIFY COLUMN created_at DATETIME NULL",
     "ALTER TABLE table_type_stat MODIFY COLUMN created_at DATETIME DEFAULT CURRENT_TIMESTAMP",
     ("created_at", "默认值不符")),
    ("status 默认值被改",
     "ALTER TABLE table_type_stat_item MODIFY COLUMN status VARCHAR(16) DEFAULT 'X'",
     "ALTER TABLE table_type_stat_item MODIFY COLUMN status VARCHAR(16) DEFAULT 'OK'",
     ("status", "默认值不符")),
    ("stat_id 变为可空",
     "ALTER TABLE table_type_stat_item MODIFY COLUMN stat_id INT NULL",
     "ALTER TABLE table_type_stat_item MODIFY COLUMN stat_id INT NOT NULL",
     ("stat_id", "可空性不符")),
    ("db_name 字符集非 utf8mb4",
     "ALTER TABLE table_type_stat_item MODIFY COLUMN db_name VARCHAR(128) "
     "CHARACTER SET latin1 DEFAULT ''",
     "ALTER TABLE table_type_stat_item MODIFY COLUMN db_name VARCHAR(128) "
     "CHARACTER SET utf8mb4 DEFAULT ''",
     ("db_name", "字符集不符")),
    ("warnings_json 退回 TEXT",
     "ALTER TABLE table_type_stat MODIFY COLUMN warnings_json TEXT",
     "ALTER TABLE table_type_stat MODIFY COLUMN warnings_json MEDIUMTEXT",
     ("warnings_json", "类型不符")),
    ("total_tables 变 VARCHAR",
     "ALTER TABLE table_type_stat_item MODIFY COLUMN total_tables VARCHAR(32) DEFAULT '0'",
     "ALTER TABLE table_type_stat_item MODIFY COLUMN total_tables INT DEFAULT 0",
     ("total_tables", "类型不符")),
    ("索引建在错误的列上",
     "DROP INDEX idx_tts_conn ON table_type_stat; "
     "CREATE INDEX idx_tts_conn ON table_type_stat (instance_type)",
     "DROP INDEX idx_tts_conn ON table_type_stat; "
     "CREATE INDEX idx_tts_conn ON table_type_stat (connection_id)",
     ("idx_tts_conn", "列序不符")),
    ("索引被建成唯一索引",
     "DROP INDEX idx_tts_created ON table_type_stat; "
     "CREATE UNIQUE INDEX idx_tts_created ON table_type_stat (created_at)",
     "DROP INDEX idx_tts_created ON table_type_stat; "
     "CREATE INDEX idx_tts_created ON table_type_stat (created_at)",
     ("idx_tts_created", "唯一性不符")),
]


@pytest.mark.skipif(not MYSQL_AVAILABLE, reason="元数据库未就绪")
@pytest.mark.parametrize("name,break_sql,fix_sql,expect",
                         _CONTRACT_CASES, ids=[c[0] for c in _CONTRACT_CASES])
def test_t3r08_full_schema_contract_fails_closed(g14_schema, name, break_sql,
                                                 fix_sql, expect):
    """T3-R08 / P2-01：完整字段契约的每一项都必须在采集之前失败关闭。"""
    _exec_sql(*[x for x in break_sql.split(";") if x.strip()])
    with pytest.raises(svc.SchemaNotReadyError) as e:
        svc._ensure_schema()
    for kw in expect:
        assert kw in str(e.value), f"{name}: 报错里应点名 {kw}，实际：{e.value}"
    _exec_sql(*[x for x in fix_sql.split(";") if x.strip()])
    svc._ensure_schema()          # 恢复后必须重新通过


@pytest.mark.skipif(not MYSQL_AVAILABLE, reason="元数据库未就绪")
def test_t3r08b_clean_ddl_passes_verification(g14_schema):
    """T3-R08 反向护栏：**干净的 DDL 必须通过验收**。

    结构验收过严比过松更难发现——干净安装通不过 = 页面永远不可用，
    而且现场会以为是"表没建好"。这条用例必须和上面 11 条一起长期存在。
    """
    svc._ensure_schema()
    # 连续两次调用也必须稳定（无副作用）
    svc._ensure_schema()


@pytest.mark.skipif(not MYSQL_AVAILABLE, reason="MySQL 测试环境未启动")
def test_r12f_ddl_and_service_column_lists_agree(g14_schema):
    """DDL 文件与服务的期望列清单必须逐字一致，防止两边各改各的。"""
    ddl = _ddl_path().read_text(encoding="utf-8").lower()
    for col in svc._STAT_COLUMNS:
        if col != "id":
            assert col in ddl, f"DDL 缺少 table_type_stat.{col}"
    for col in svc._ITEM_COLUMNS:
        if col != "id":
            assert col in ddl, f"DDL 缺少 table_type_stat_item.{col}"
    assert "mediumtext" in ddl, "warnings_json 必须是 MEDIUMTEXT（P1-07）"
    # 干净结构下验收必须通过
    svc._ensure_schema()
```


### A.5 既有文件的 10 行改动 + 2 个前端块

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

> **Rev.G 相对 Rev.F 的两处前端变化**（关闭 P1-07 与 P2-02）：
> 1. 告警区改为渲染 `visibleTableTypeWarnings`（默认最多 10 条）+ 一个"展开查看全部"按钮
>    ——500 库失败时不能往页面上糊几百个 `el-alert`；
> 2. 新增「历史」按钮 + 历史抽屉，点某一次即拉 `/detail/{id}` 展示逐库明细与告警。
>    Rev.F 只给了 `/history`、`/detail` 两个接口却没有任何入口，
>    REQ-6 的"可回看"在 UI 上等于不存在。

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
                <el-button size="small" style="margin-left:8px" @click="openTableTypeHistory">历史</el-button>
                <el-alert v-for="(w,i) in visibleTableTypeWarnings" :key="i"
                          :type="w.severity==='ERROR'?'error':(w.severity==='WARNING'?'warning':'info')"
                          :closable="false" show-icon style="margin-top:8px"
                          :title="w.code + (w.db_name ? (' · '+w.db_name) : '')" :description="w.detail"></el-alert>
                <el-button v-if="tabletypeWarnTotal>visibleTableTypeWarnings.length" link type="primary" size="small"
                           style="margin-top:4px" @click="tabletypeWarnAll=true">
                  展开查看全部 {{ tabletypeWarnTotal }} 条告警
                </el-button>
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
                  逻辑基线已剔除 xxx_tdsql_subp202601 这类二级分区物理子表——判定需同时满足：命名匹配、其逻辑父表出现在 Proxy 结果中、且该表自身不在 Proxy 结果中（单列显示，不计入总数）。结果为采集时刻快照。
                </div>

                <!-- 历史回看抽屉（Rev.G / P2-02） -->
                <el-drawer v-model="tabletypeHistoryVisible" title="表类型统计历史" size="60%">
                  <el-table :data="tabletypeHistory" size="small" border highlight-current-row
                            @current-change="loadTableTypeHistoryDetail" max-height="300">
                    <el-table-column prop="created_at" label="统计时间" width="170">
                      <template #default="s">{{ formatTime(s.row.created_at) }}</template>
                    </el-table-column>
                    <el-table-column prop="created_by" label="操作人" width="110"></el-table-column>
                    <el-table-column prop="database_filter" label="库名" width="130">
                      <template #default="s">{{ s.row.database_filter || '全部业务库' }}</template>
                    </el-table-column>
                    <el-table-column prop="instance_type" label="实例类型" width="90">
                      <template #default="s">{{ s.row.instance_type==='distributed'?'分布式':'集中式' }}</template>
                    </el-table-column>
                    <el-table-column prop="database_count" label="库数" width="70"></el-table-column>
                    <el-table-column prop="total_tables" label="总表" width="80"></el-table-column>
                    <el-table-column prop="single_tables" label="单表" width="80"></el-table-column>
                    <el-table-column prop="broadcast_tables" label="广播表" width="80"></el-table-column>
                    <el-table-column prop="shard_tables" label="分片表" width="80"></el-table-column>
                    <el-table-column prop="failed_databases" label="失败库" width="80"></el-table-column>
                  </el-table>
                  <div style="color:#909399;font-size:12px;margin:8px 0">点击上表任意一行，查看该次统计的逐库明细。</div>
                  <div v-if="tabletypeDetailAll.length" style="color:#909399;font-size:12px;margin-bottom:6px">
                    该次统计共 {{ tabletypeDetailAll.length }} 条告警，当前显示 {{ tabletypeDetailWarnings.length }} 条。
                  </div>
                  <el-alert v-for="(w,i) in tabletypeDetailWarnings" :key="i"
                            :type="w.severity==='ERROR'?'error':(w.severity==='WARNING'?'warning':'info')"
                            :closable="false" show-icon style="margin-bottom:6px"
                            :title="w.code + (w.db_name ? (' · '+w.db_name) : '')" :description="w.detail"></el-alert>
                  <el-button v-if="tabletypeDetailAll.length>tabletypeDetailWarnings.length" link type="primary" size="small"
                             style="margin-bottom:8px" @click="tabletypeDetailExpand=true">
                    展开查看全部 {{ tabletypeDetailAll.length }} 条告警
                  </el-button>
                  <el-table :data="tabletypeDetailItems" size="small" border max-height="360"
                            v-loading="tabletypeDetailLoading">
                    <el-table-column prop="db_name" label="数据库" width="180"></el-table-column>
                    <el-table-column prop="total_tables" label="总表数" width="90"></el-table-column>
                    <el-table-column prop="single_tables" label="单表" width="80"></el-table-column>
                    <el-table-column prop="broadcast_tables" label="广播表" width="80"></el-table-column>
                    <el-table-column prop="shard_tables" label="分片表" width="80"></el-table-column>
                    <el-table-column prop="baseline_tables" label="逻辑基线" width="90"></el-table-column>
                    <el-table-column prop="subpartition_tables" label="二级分区子表" width="110"></el-table-column>
                    <el-table-column prop="status" label="状态" width="90">
                      <template #default="s"><el-tag :type="s.row.status==='OK'?'success':(s.row.status==='SKIPPED'?'warning':'danger')" size="small">{{ s.row.status }}</el-tag></template>
                    </el-table-column>
                    <el-table-column prop="detail" label="说明"></el-table-column>
                  </el-table>
                </el-drawer>
              </el-tab-pane>
```

#### A.5.5 `frontend/static/js/app.js` —— 4 行 + 1 个纯新增方法块

**① 第 218 行**，`deepResult` 增加一个键：

```diff
-    const deepResult=reactive({cluster:null,index:null,diff:null,emergency:null,sqlstats:null});
+    const deepResult=reactive({cluster:null,index:null,diff:null,emergency:null,sqlstats:null,tabletype:null});
```

**② 第 814 行**（`runSqlStats` 的 `};`）之后、第 815 行 `// G10: ZK Discovery` 之前追加新方法块
（Rev.G：含告警展示上限与历史抽屉）：

```javascript
    // G14: 表类型统计
    const tabletypeWarnAll=ref(false);
    const tabletypeHistoryVisible=ref(false);
    const tabletypeHistory=ref([]);
    const tabletypeDetailItems=ref([]);
    const tabletypeDetailAll=ref([]);      // Rev.J / P2-02：历史告警全量（唯一数据源）
    const tabletypeDetailExpand=ref(false);
    const tabletypeDetailLoading=ref(false);
    const TABLETYPE_WARN_CAP=10;
    const tabletypeDetailWarnings=computed(()=>
      tabletypeDetailExpand.value?tabletypeDetailAll.value
                                 :tabletypeDetailAll.value.slice(0,TABLETYPE_WARN_CAP));
    const tabletypeWarnTotal=computed(()=>deepResult.tabletype?deepResult.tabletype.warnings.length:0);
    const visibleTableTypeWarnings=computed(()=>{
      const all=deepResult.tabletype?deepResult.tabletype.warnings:[];
      return tabletypeWarnAll.value?all:all.slice(0,TABLETYPE_WARN_CAP);
    });
    const runTableTypeStats=async()=>{
      tabletypeWarnAll.value=false;
      const r=await _deepPost('tabletype','/api/v1/table-type-stats/run',{connection_id:deepConnId.value,database:deepDb.value});
      if(!r)return;
      deepResult.tabletype=r;
      // Rev.J / P2-03：提示必须与结果状态一致。只要接口回 200 就弹绿色"统计完成"，
      // 在 failed_databases=500、主结果为 0 时会把"0 张"暗示成真实结论。
      // Rev.K / P2-03：判据是【有效库数】，不是"失败数是否等于总库数"。
      // Rev.J 只在 failed>=database_count 时报红，于是 100 个库里 60 个失败、
      // 40 个超预算跳过时——一个有效库都没有——页面仍然提示"部分完成"。
      const n=(r.database_count||0), bad=(r.failed_databases||0), skip=(r.skipped_databases||0);
      const ok=n-bad-skip;
      const tail=`${n} 个库 / ${r.total_tables} 张表`
                +(bad?` · 失败 ${bad} 库`:'')+(skip?` · 未采集 ${skip} 库`:'');
      // Rev.L / P2-01：没有业务库是有效的空结果，但不能弹绿色"统计完成"。
      if(n===0){ElementPlus.ElMessage.warning('未发现可统计的业务库（请检查账号可见范围，或确认实例是否为空）')}
      else if(ok<=0){ElementPlus.ElMessage.error(`无有效统计结果：${n} 个库中失败 ${bad}、未采集 ${skip}，主数字不可用`)}
      else if(bad||skip){ElementPlus.ElMessage.warning(`部分完成：成功 ${ok} 库 · ${tail}（失败/未采集的库未计入任何汇总）`)}
      else{ElementPlus.ElMessage.success(`统计完成：${tail}`)}
    };
    const openTableTypeHistory=async()=>{
      if(!deepConnId.value){ElementPlus.ElMessage.warning('请先选择实例');return}
      // Rev.K / P2-02：清的必须是【数据源】。tabletypeDetailWarnings 是无 setter 的
      // computed，给它的 .value 赋值只会触发 Vue 只读告警、什么都清不掉——
      // 于是重开抽屉（尤其是切换实例后）会继续显示上一条历史的告警。
      tabletypeDetailItems.value=[];
      tabletypeDetailAll.value=[];
      tabletypeDetailExpand.value=false;
      try{
        const resp=await apiFetch(`${API_BASE}/api/v1/table-type-stats/history?connection_id=${encodeURIComponent(deepConnId.value)}&limit=20`);
        const d=await resp.json();
        if(!resp.ok){ElementPlus.ElMessage.error(d.detail||'加载历史失败');return}
        tabletypeHistory.value=(d.data||d).items||[];
        tabletypeHistoryVisible.value=true;
      }catch(e){ElementPlus.ElMessage.error('加载历史失败: '+e.message)}
    };
    const loadTableTypeHistoryDetail=async(row)=>{
      if(!row)return;
      // 切行时先清空并置 loading，避免请求期间还挂着上一行的明细与告警
      tabletypeDetailItems.value=[];
      tabletypeDetailAll.value=[];
      tabletypeDetailExpand.value=false;
      tabletypeDetailLoading.value=true;
      try{
        const resp=await apiFetch(`${API_BASE}/api/v1/table-type-stats/detail/${row.id}`);
        const d=await resp.json();
        if(!resp.ok){ElementPlus.ElMessage.error(d.detail||'加载明细失败');return}
        const body=d.data||d;
        tabletypeDetailItems.value=body.items||[];
        // Rev.J / P2-02：历史抽屉与实时结果共用同一套"默认 10 条 + 总数 + 展开全部"。
        // Rev.I 在这里直接 slice 且不给任何提示，超过 10 条时用户会以为历史就这 10 条。
        tabletypeDetailAll.value=body.warnings||[];
        tabletypeDetailExpand.value=false;
      }catch(e){ElementPlus.ElMessage.error('加载明细失败: '+e.message)}
      finally{tabletypeDetailLoading.value=false}
    };
```

**③ 第 1960 行** 深度诊断子页签**回退清单** `subtabs`，末尾追加一项（**Rev.G / P1-06**）：

```diff
-const subtabs=[...,{perm:'deep-diag-ppt',tab:'ppt_report'},{perm:'deep-diag-toolkit',tab:'toolkit'}];
+const subtabs=[...,{perm:'deep-diag-ppt',tab:'ppt_report'},{perm:'deep-diag-toolkit',tab:'toolkit'},{perm:'deep-diag-tabletype',tab:'tabletype'}];
```

> **追加在末尾而不是插在中间**：这个清单是"当前角色的默认落点"的优先级顺序，
> 插在中间会改变**既有角色**进入深度诊断时落到哪个页签——那是行为回归。
> 追加在末尾只对"其他子页签一个都没有"的角色生效，对既有角色零影响。
>
> **漏掉这一步的后果**：只被授予 `deep-diag` + `deep-diag-tabletype` 的自定义角色
> 进入深度诊断页时，`deepTab` 停在默认的 `'cluster'`，而该页签对它不可见
> ——**页面没有活动页签**。这个缺陷在 admin 账号下永远测不出来
> （admin 拥有全部子页签，循环第一项必定命中），必须靠 T-R08 单测 + 最小权限角色人工回归。

**④ 第 2043 行** `setup()` 返回清单，把 `runSqlStats,` 改成下面这一串：

```diff
-...,runClusterInspect,runIndexAudit,runSchemaDiff,runEmergency,runSqlStats,visibleMenus,...
+...,runClusterInspect,runIndexAudit,runSchemaDiff,runEmergency,runSqlStats,runTableTypeStats,openTableTypeHistory,loadTableTypeHistoryDetail,visibleTableTypeWarnings,tabletypeWarnTotal,tabletypeWarnAll,tabletypeHistoryVisible,tabletypeHistory,tabletypeDetailItems,tabletypeDetailWarnings,tabletypeDetailAll,tabletypeDetailExpand,tabletypeDetailLoading,visibleMenus,...
```

> 漏掉第 ④ 步的后果：页签能渲染，但点按钮报 `runTableTypeStats is not a function`，
> 或历史抽屉的 `v-model` 绑到 `undefined` 上静默失效。
> 这是本项目 `setup()` 显式返回清单写法的固有陷阱，必须逐条核对。
>
> `computed` 已在文件顶部的 Vue 解构中引入（`app.js:1` 附近），无需新增 import。

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

### B.55 第五轮实测（2026-08-31，T17 / T18，Rev.H 回填）

**B.55.1 `lzbj_ecif` 全量二级分区物理子表（T17 ①）**

```
MySQL [(none)]> SELECT TABLE_NAME,
                       SUBSTRING_INDEX(TABLE_NAME,'_tdsql_subp',1) AS parent_guess
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA='lzbj_ecif' AND TABLE_TYPE='BASE TABLE'
                  AND TABLE_NAME REGEXP '_tdsql_subp[0-9]+$'
                ORDER BY parent_guess, TABLE_NAME;
…
78 rows in set (0.004 sec)
```

**B.55.2 去重后的父表（T17 ②）—— 正好 6 个，各 13 张**

| 父表 | 子表数 | Rev.E 是否已预测 |
|---|---:|---|
| `cus_bas_merge_log` | 13 | ✅ D3 的 `LIKE` 直接命中 |
| `cus_pub_sync_consumer_log` | 13 | ✅ 被判为"未被 LIKE 匹配的第 6 张"，**本次证实** |
| `cus_pub_sync_log` | 13 | ✅ D3 命中 |
| `cus_pub_translog` | 13 | ✅ D3 命中 |
| `cus_pub_updatelog` | 13 | ✅ D3 命中 |
| `cus_pub_updatelog_detail` | 13 | ✅ D3 命中（经 `cus_pub_updatelog%`） |
| **合计** | **6 × 13 = 78** | 与 293 − 215 精确吻合 |

**B.55.3 集中式实例（T18）**

```
MySQL [10.243.20.15:15158]> …WHERE TABLE_SCHEMA='zjywgl' AND TABLE_TYPE='BASE TABLE'
                              AND TABLE_NAME REGEXP '_tdsql_subp[0-9]+$';
Empty set (0.005 sec)
```

**B.55.4 环境信息（顺带取得，记录备查）**

| 项 | 值 |
|---|---|
| 分布式 Proxy | `10.243.20.13:15005`，库 `lzbj_ecif` |
| 集中式实例 | `10.243.20.15:15158`，库 `zjywgl`（`10.243.20.13:15158` 不可达） |
| **平台元数据库** | **`10.243.20.15:15197`，库 `tdsql_sqlcheck`** |
| 三者服务端版本 | 均为 `8.0.33-v24-txsql-22.6.9-20250509` |
| 统一账号 | `checksql`（权限充足，与平台"实例管理"登记账号一致） |

> 元数据库为 **TDSQL**（`txsql` 8.0.33），与"生产元数据库是 TDSQL/MySQL、
> **MariaDB 非支持目标**"这一既有结论一致；本地沙箱的 MariaDB 只用于离线单测。

### B.6 由五轮实测直接得出的结论

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
| B-14（第五轮） | `lzbj_ecif` 的 78 张子表精确推导出 **6 个父表、各 13 张**，与 Rev.E 的预测逐名吻合 | ADR-17 父表确认规则取证；`test_r07b_*` 用真名钉住 |
| B-15（第五轮） | 真实数据里存在**前缀嵌套的父表**：`cus_pub_updatelog` 与 `cus_pub_updatelog_detail` **两者都是父表** | **KL-16**：父表推导必须用非贪婪正则；`test_r07c/d_*` 钉住。取最短前缀的近似做法会把 UAT 的 215/78 变成 228/65 |
| B-16（第五轮） | 集中式实例（`zjywgl`）**无** `_tdsql_subp` 命名的表 | P1-03「集中式一律不剔除」零风险 |
| B-17（第五轮） | 内网元数据库为 TDSQL `8.0.33-v24-txsql`，`10.243.20.15:15197` / 库 `tdsql_sqlcheck` | 迁移与 `_ensure_schema` 的目标环境确认；MariaDB 仅沙箱离线用 |
| B-18（第六轮） | 元数据库中**无** `table_type_stat` / `table_type_stat_item` 同名残留表 | T19 结案；部署文档无需"先删表"步骤；ADR-20 的"同名残留表"路径当前是纯防御（另一条"登记后结构漂移"路径仍在） |
| B-19（第六轮，旁证） | 该元数据库上 v0～v12 共 13 个迁移版本全部成功应用过，其中 `v12/120` 是纯 `CREATE TABLE IF NOT EXISTS … ENGINE=InnoDB DEFAULT CHARSET=utf8mb4` | **本模块 DDL 写法（不带 shardkey）在这台 TDSQL 元数据库上已被既有版本证明可用**——"TDSQL 建表要不要指定分片键"这个本会在 UAT 才暴露的问题提前排除 |

**待回填**：

| 项 | 内容 | 来源 | 阻断 |
|---|---|---|---|
| 命令作用域 | 当前库 / 实例级 | T13 | 否 |
| ~~元数据库是否有同名残留表~~ | 已完成：0 行 | T19 | — |
| ~~父表是否全在 Proxy 结果中~~ | 已完成：算术闭合可证，6 个父表全在 | T17 | — |
| ~~集中式是否有 `_tdsql_subp` 表~~ | 已完成：0 行 | T18 | — |
| ~~账号可见范围差异~~ | 已关闭：统一 `checksql`，无差异 | T16 | — |
| ~~那 78 张表是什么~~ | 已完成：二级分区物理子表，`_tdsql_subp<数字>` | D3 | — |
| ~~基线交叉校验~~ | 已完成：293 vs 215，差 78（已解释并消除） | T14 | — |
| ~~空结果集行为~~ | 已完成：OK 包，0.001s，不挂起 | T15 | — |

---


## 修订记录

| 版本 | 日期 | 作者 | 内容 |
|---|---|---|---|
| **Rev.N** | **2026-09-01** | **智能体 Q** | **依 A 第一轮 SIT 报告（`SIT-v1.6.3.0-…第一轮SIT测试报告-ClaudeA.md`，结论"不通过：1 BLOCK + 2 MINOR"）整改并落盘。三项全部按报告给出的方案关闭。** **DEF-SIT-01（BLOCK）**：`_OPERATIONAL_WRITE_PREFIXES` 漏登记 `/api/v1/table-type-stats/`，导致 `developer` 与全部自定义角色在 `check_permission` 第一级即被拒、写端点恒 403 而页签照常显示（设计遗漏——§2.2 的 6 处登记清单未含第一级放行清单）。补登记 1 行（带尾斜杠），§2.2 改为 **7 处**（新增 P7），ADR-21 / §12.2（新增 developer 验收项）/ KL-17（第三次复发补记 + 全仓库 grep 对照法）同步；新增行为级用例 `test_sit01_*`（以既有 G5 为基准对照四角色可达性，不硬编码死值）。**DEF-SIT-02（MINOR）**：纯文档修订——空串/缺字段实际为 422（FastAPI 请求体校验标准语义），§5 错误表与 §8 E-26 修正为"422（空串/缺字段）或 400（全空白）"，实现不动。**DEF-SIT-03（MINOR）**：附录 A.2 `run()` 的全空白校验提前到 `_pool()` 连接解析之前（否则服务层守卫经 HTTP 永远不可达、报错文案误导为"未连接实例"）；服务层同名守卫保留为直接调用兜底；新增 `test_sit03_*` 直接打 API 层、断言不合格时不得先解析连接。collect 110 → **112**（离线 87 → 89）。**SIT 报告同时确认：四个新增文件与设计附录逐字一致、零次生灾害。** |
| **Rev.M** | **2026-09-01** | **O / Codex** | **依 A 第五轮评审报告定点整改，唯一 P2-01 全部接受。** 生产基线查询删除 `BINARY`，恢复可下推的 `TABLE_SCHEMA IN (...)`；SQL 端不再承担或假定大小写正确性，所有返回行仍经全实例 `_NameSpace known` 解析 canonical 库名，再对目标集合做精确成员判断。同步重写 §4.2 / §6.4 / ADR-27 / E-36 与附录 A.1；T4-R01 删除 `BINARY TABLE_SCHEMA IN` 字符串断言，只钉“服务端多返 `Sales.*` / `sales.*` 时两个分支均只计 `Sales.*`”的行为。新增 T20：在业务库数量最多的内网实例比较普通 `IN` 与 `BINARY IN` 的 `EXPLAIN Scanned N database(s)`、至少 5 次耗时中位数及返回集合，普通 `IN` 异常劣化时阻断发布、但禁止无证据恢复阻断下推的 `BINARY` / `CAST`。回填第五轮独立抽取实测：110 collected；连元数据库 109 passed + 1 skipped；离线 87 passed + 23 skipped，明确仅为设计附录证据。按非阻断建议在 KL-19 补充既有扫描/巡检横向口径。**测试项仍为 110，仓库实现尚未落盘，待 A 单点复核。** |
| **Rev.L** | **2026-09-01** | **O / Codex** | **依 O 第四轮评审结果整改，2 项 P1、2 项 P2、1 项 DOC 全部接受。** **P1-01**：Rev.K 只在 Proxy 结果过滤上封堵了大小写兄弟库，基线查询仍用受排序规则影响的 `TABLE_SCHEMA IN (...)`，指定 `Sales` 可带回 `sales.*`；改为 `BINARY TABLE_SCHEMA IN`，并把全实例 `_NameSpace known` 传入 `_collect_baseline`，先解析 canonical 库名、再对目标子集精确过滤（ADR-27 / E-36 / T4-R01）。**P1-02**：Rev.K 的 215 秒目标采集硬上界仍不成立；PyMySQL `read_timeout` 是每次底层读取的空闲超时，不是整条 SQL 总时间，且真实池存在 `ping` / 建连 / `select_db` / 异常重建组合路径。删除 `MAX_COLLECT_WALL_SECONDS`，180 秒只承诺"到期后不再启动新目标 I/O"（ADR-13 / KL-19 / T4-R02/R03）。**P2-01**：前端对 `database_count===0` 单独弹黄色"未发现可统计的业务库"，不再把空结果表述为绿色成功（E-37 / T4-R04）。**P2-02**：`_probe_metadata_db()` 补传 `database` 与 `charset`，确保探测的就是后续破坏性测试使用的元数据库（T4-R05）。**DOC-01**：当前态标题、测试数、KL-6/KL-19、附录注释和验收口径统一到 Rev.L；预计 collect **110 项**（离线 87 / 元数据库 22 / 落盘后仓库检查 1），因功能尚未落盘，不伪造新 passed 数字。**仓库实现仍零改动，待智能体 A 第五轮评审。** |
| **Rev.K** | **2026-09-01** | **智能体 A** | **依 O 第三轮评审报告（`REVIEW3-…`，结论"不通过，退回修订 Rev.K"）整改。4 项 P1、3 项 P2、1 项文档问题全部接受，无一条不认可——四项 P1 全部是我自己代码里的实际缺陷。** **P1-01**：`_extract_pairs` 用全实例 `known` 把 qual 解析成 canonical 名之后，`_collect_distributed` 又对只含目标库的 `target` 做了**第二次** CI 回退——指定 `database="Sales"` 时，实例级返回里的 `sales.t_lower` 会被"唯一命中"回 `Sales`，指定单库的四个主数字直接错，而 Rev.J 的全库用例恰好测不到这条链路。改为 `if qual not in target: continue; owner = qual`，**canonical 名之后只做精确成员判断**（ADR-22 补充使用边界）。**P1-02（两个独立问题）**：其一，用 `raise _BudgetExceeded()` 承载"预算耗尽"这个正常控制信号，而 `TDSQLConnectionPool.get_connection()` 会捕获穿出上下文的**任何**异常并 close + `_create_connection()`——于是一条健康连接在 deadline 之后被销毁重建、白付一次建连，重连再失败还会用新异常盖掉原信号，把本该 `SKIPPED` 的库误标成 `FAILED`；改为**标志位 + 正常退出 `with`**，**连接重建只应由真实的数据库/网络异常触发**（ADR-25）。其二，Rev.J 的"210 秒 `/run` 端到端上界"**不成立**：漏算了"刚过库级检查、随后建连 + `select_db`"这条 35 秒路径，且 `_ensure_schema()`（拿槽位前）与结果落库（释放槽位后）根本不在 deadline 内；更正为**目标采集阶段上界 215 秒**（`MAX_COLLECT_WALL_SECONDS = 180 + 5 + 30`，显式钉住 `CONNECT_TIMEOUT`），元数据库阶段单独说明边界并把明细落库改为批量（`ITEM_INSERT_BATCH=100`，500 库 500 次往返 → 5 次）（ADR-13 更正 / ADR-24 / KL-19 更正 / KL-20）。另自查补一处 O 未提的同类问题：`instance_type_service.resolve()` 在缓存未命中时会向目标实例发探测 SQL，其后补一次 deadline 检查。**P1-03**：槽位护栏里的 `assert _OUR_SLOT > max(others)` 在项目下一次合法新增 `v13/131` 或 `v14/140` 之后**必然为假**——一条历史测试把项目此后的演进锁死了，我甚至把"我们不再是最大槽"写成了必须拒绝的场景；改为只钉永久不变量（槽位不重复 + 本模块独占自己的槽 + 已知前驱 v12/120 仍在），并新增未来迁移共存的正向用例（ADR-26）。**P1-04（安全底线）**：集成测试的可用性探测用 `TDSQL_TEST_*`、实际 DROP 用 `SQLCHECK_DB_*`，两套配置可指向不同服务器，唯一"保护"还是个晚于模块导入、且不会覆盖外部值的 `setdefault`——**测试代码可能删掉生产元数据库的表**；改为探测与执行共用 `MYSQL_CONFIG`，任何 DROP 前先过 `assert_destructive_target_is_safe()` 失败关闭并打印目标 host/port/database，守门人本身由 3 条离线用例覆盖。**P2-01** 第二轮要求的完整契约用例此前只是我手工跑过一次，现落为 **11 条参数化元数据库用例 + 1 条干净 DDL 反向护栏**。**P2-02** 历史抽屉此前给无 setter 的 `computed` 赋值（Vue 只读告警 + 清不掉），改为清理数据源并加 loading。**P2-03** 判据改为**有效库数** `n - failed - skipped`，"失败+跳过覆盖全部库"不再被称作"部分完成"。**DOC-01** API 头 Rev.G→Rev.K、DDL 头 Rev.I→Rev.K、`run_stats` 注释的"180 秒"、前端子分区口径补上第三个条件、`/run` 端到端 210 秒的说法全部更正。测试 84 → **106 项**（本地 105 通过 + 1 跳过；离线 83 / 元数据库集成 22 / 需落盘 1），`TimeoutError` 新增映射为 **503**。新增 ADR-24/25/26、KL-20、E-34/E-35、§12.4 七项专项验收。**仓库代码仍零改动。** |
| **Rev.J** | **2026-08-31** | **智能体 A** | **依 O 第二轮评审报告（`REVIEW2-…`，结论"不通过，退回修订 Rev.J"）整改。5 项 P1、3 项 P2、1 项文档问题全部接受，无一条不认可。** 其中三项是我自己论证或代码的硬伤：**P1-01** 库名被无条件小写——`Sales` 与 `sales` 在 `lower_case_table_names=0` 下是两个 schema，单值 lower 字典会把它们**静默合并**，四个主数字直接错且无任何告警；我在表名那侧写对了这个道理（ADR-9），却把库名的免责理由写成"库名来自我们自己枚举的清单，不存在合并风险"——风险恰恰来自两个都真实存在的库。改用 `_NameSpace`（精确优先 / CI 回退仅在唯一时 / 歧义不猜并报 `DB_NAME_AMBIGUOUS`），新增 ADR-22。**P1-02** "180 秒总预算"根本不成立：计时器直到 `_collect_distributed` 内部才启动，`SHOW DATABASES` 与基线查询（设计自己承认是耗时大头）完全不计；进入循环后只在每库开始时查一次，单库三条 30 秒命令——179 秒进库能跑到 **269 秒**，集中式分支更是毫无预算。改为 deadline 自 `run_stats` 拿到槽位后建立、贯穿前置查询与**每一条命令**，并如实给出**可证明上界 210 秒**（`MAX_WALL_SECONDS`），文档与 UI 不再宣称"硬 180 秒"（ADR-13 修正 / KL-19）。**P1-05** 我为防 DEF-1 而写的槽位护栏本身有两个错：`assert ours > max(taken)` 在迁移文件真正落盘后必然为假——**这条护栏会在模块上线当天把构建打红**；而它用单值字典保存占用者，同槽两个文件时后者覆盖前者，**恰好看不见 DEF-1 想防的那个场景**。重写为 `scan_migration_slots`（`slot → [files]`）+ `assert_slot_ok`（四条断言落盘前后都成立），并用合成状态把两个阶段都验一遍。另：**P1-03** 先定状态再算汇总，失败/跳过库的基线、子分区、重叠一律不进实例级汇总（ADR-23）；**P1-04** 结构验收升级为完整字段契约，**KL-15 关闭**——当时"COLUMN_TYPE 带显示宽度"的免责理由只对整型成立、对 varchar 长度不成立，"已知"不等于"已关闭"；**P2-01** 物理子表判定增加第三条件"候选自身不在 Proxy 结果中"（真子表不会被 `show table` 返回，它自己在结果里就直接证伪了）；**P2-02** 历史抽屉告警不再静默截断；**P2-03** 部分/全部失败不再弹绿色"统计完成"；**DOC-01** 如实列出离线 73 / 元数据库集成 10 / 需落盘 1，并写明 **skip 不是 pass、设计阶段数字不能当发布证据**。测试 71 → **84 项**（本地 83 通过 + 1 跳过），新增可控时钟 `FakeClock` 与 `_now` 钩子使 deadline 行为可断言。新增 ADR-22/23、KL-18/19、W13/W14、E-30～E-33、§12.4 八项专项验收。**仓库代码仍零改动。** |
| **Rev.I** | **2026-08-31** | **智能体 A** | **回填 T19（第六轮），并更正一处 Rev.A～H 各版均未发现的硬伤。** **DEF-1（必须改）**：迁移文件槽位 `v11/110` 早在 v1.6.2.2 的 UAT 第四、五轮就被 `v11/110_index_finding_structured.sql`（O-18）与 `v12/120_gateway_report_tickets.sql`（O-22）占用。Rev.A 写这句时（`5e9f438`，最高版本确为 v10/100）是对的，**但 Rev.F 那一版的任务恰恰是"依 v1.6.2.2 上线后的代码变更复核"却没抓到**——原因是我把复核范围定义成"本设计**引用到的** 13 个文件有没有变"，而**新文件要落进去的槽位本来就不在这 13 个文件里**，那种 diff 无论多仔细都不可能命中。这是复核**方法**的缺口，教训登记为 **KL-17**（今后凡"新增文件/新增标识符"类设计，复核必须含一次目录与命名空间的重新枚举；能自动化的一律变成测试）。影响评估：迁移键为 `f"v{version}_{sequence:03d}_{name}"`，故**不撞主键、不会启动失败**，但同目录两个 `110_` 前缀的执行先后只由文件名字典序决定，是没人打算建立的隐式依赖，且违反项目约定。**处置**：槽位改为 **`v13/130_table_type_stats.sql`**（正文、附录 A.1/A.3/A.4、验收项、服务的报错文案共 19 处同步更正），并新增护栏测试 `test_migration_slot_is_not_already_taken`——扫描真实目录断言槽位未被占用且为最大槽位。**T19**：元数据库 `tdsql_sqlcheck`（TDSQL `8.0.33-v24-txsql`，`10.243.20.15:15197`）无同名残留表，部署文档无需"先删表"步骤；如实登记"该失效路径当前是纯防御"，但**不削弱 ADR-20**（第二条路径"登记后结构漂移"与残留表无关且随时可能发生）。顺带得到旁证 **B-19**：该库上 v0～v12 共 13 个迁移全部成功应用过，其中 v12/120 是纯 `CREATE TABLE IF NOT EXISTS … InnoDB/utf8mb4`，**证明本模块不带 shardkey 的建表写法在这台 TDSQL 元数据库上可用**，提前排除了一个本会在 UAT 才暴露的问题。另更正两处陈旧计数（`_create_all_tables` 27 → **46 张/828 行**；路由前缀 25 → **26**）。测试 70 → **71 项**（本地 70 通过 + 1 跳过）。**至此 T16～T19 全部完成，仅剩不影响任何数字的 T13。** |
| **Rev.H** | **2026-08-31** | **智能体 A** | **回填第五轮内网实测（T16 / T17 / T18），设计与算法无实质变更。** **T17**：`lzbj_ecif` 的 78 张 `_tdsql_subp` 表精确推导出 **6 个父表、各 13 张**，与 Rev.E 从 D3 推出的 6 个名字逐名吻合（含当时判为"未被 LIKE 匹配的第 6 张"的 `cus_pub_sync_consumer_log`）。给出**算术闭合证明**——由基线 293、Proxy 215、后缀表 78 三个基数可推出 Proxy 结果集恰为逻辑基线，故 6 个父表全部在 Proxy 结果中，**不必再取 98 行原始输出**；残留假设仅"Proxy 不返回物理子表"一条，而它会在 UAT 时被 `RECON_MISMATCH` 免费验证且失败可见。**UAT 六个数字维持 215/0/117/98/215/78 不变。** T17 另暴露出一个构造夹具想不到的真实形态：`cus_pub_updatelog` 与 `cus_pub_updatelog_detail` **互为前缀且两者都是父表**——父表推导若用任何"取最短前缀"的近似做法，`_detail` 的 13 张子表会被错算给 `cus_pub_updatelog`，UAT 变成 228/65，**数字错了还很像对的**。现行非贪婪正则已用 78 个真名逐条验算通过，并新增 3 项定向测试（`test_r07b/c/d_*`，共 **70 项**，本地 69 通过 + 1 跳过）；登记 **KL-16**。**T18**：集中式实例 `zjywgl` 库 0 行 `_tdsql_subp` 表，P1-03「集中式一律不剔除」零风险（证据范围为单库，已在文中注明）。**T16**：使用者裁决关闭——内网统一 `checksql` 账号、权限充足、与平台登记账号一致，P1-01 的"账号可见范围"场景在本环境不成立；原 T09 一并结案。**T19 仍待测**：第五轮误把 T17 的 REGEXP 语句带到元数据库上跑了，本用例未被回答（不阻断，`_ensure_schema` 两种结果都能正确处理）。附录 B 新增 **B.55 第五轮原始数据**与 **B-14～B-17** 四条结论，含环境信息（元数据库 = TDSQL `8.0.33-v24-txsql`，`10.243.20.15:15197` / 库 `tdsql_sqlcheck`）。 |
| **Rev.G** | **2026-08-31** | **智能体 A** | **依 O 的评审报告（`REVIEW-v1.6.3.0-…设计评审报告.md`，结论"不通过，退回修订 Rev.G"）整改。8 项 P1、3 项 P2、2 项文档一致性问题全部关闭，其中 **12 条我完全认可并按 O 的要求改**，**1 条（P1-08）认可问题、但整改方式与 O 的建议不同**（见 ADR-20：不做启动期失败关闭，改为模块首次使用时的结构验收——诊断子模块的留档表问题不应把"一个页面不可用"放大成"平台起不来"，且同层级的 `index_audit` / `cluster_inspection` 也没有启动期验收，只给新模块加一道标准不一致）。逐条：**P1-01** 删除「指纹相同即提前停止」的全部代码与论证（ADR-12 被自己推翻——从"命令是实例级作用域"推不出"这次返回已覆盖全部目标库"，用告警替代正确数字等于承认主结果可以是错的）；**P1-02** `run_stats` 进入既有 `registry.scan_slot`，与既有扫描**共用**配额，`ScanBusyError` → 429（ADR-19）；**P1-03** 子表判定收紧为「后缀 **且** 逻辑父表已在 Proxy 结果中确认」，集中式**一律不剔除**（ADR-17 修订）；**P1-04** 改为每库一个连接上下文、异常穿出触发连接重建；**P1-05** 单库三条命令暂存后原子合入，任一失败整库丢弃；**P1-06** 权限键补登记到 `app.js` 的 `subtabs` 回退清单（ADR-21）；**P1-07** `PROXY_CMD_FAILED` 汇总为一条、`warnings_json` 改 `MEDIUMTEXT`、前端告警上限 10 条可展开；**P1-08** 新增 `_ensure_schema()`（表 / 列 / 类型 / 索引，四种畸形均失败关闭）+ `SchemaNotReadyError`，位置在采集与并发槽位**之前**；**P2-01** 指定库必须真实存在、`SHOW DATABASES` 失败一律抛出（删除 `DB_ENUM_FAILED` 降级）；**P2-02** API 接收 `Request` 并记录操作人，前端补历史抽屉；**P2-03** `connection_id` 改必填；**DOC-01** KL-9 标记为已裁决并关闭；**DOC-02** 全文版本头、文件清单、行数、爆炸半径、接口契约统一到 Rev.G。测试从 42 项增至 **67 项**（新增 25 项全部为缺陷定向测试，逐一对应 O 的 T-R01～T-R14），本地 **66 通过 + 1 跳过**（T-R08 断言落盘后的仓库文件）。爆炸半径由「净改 9 行 + 1 个前端块」修正为「净改 **10 行** + **2 个前端块**」。另新增 4 项**不阻断**的内网核查 T16～T19，为 P1-01/P1-03/P1-08 的事实前提取证。 |
| Rev.F | 2026-08-31 | 智能体 A | **v1.6.2.2 上线后的代码变更复核修订。**对本设计依赖的 13 个文件做 `git diff 8fee172..01e2914` 逐一比对，只有 `migrator.py`（失败关闭改造）与 `app.js`（2 行，无关）动过；其余全部未变，§2 的行号与结论逐条重新核对后继续成立。**§2.7 重写**，写入迁移器的三条新硬约束：M-1 任一语句失败即启动关闭（旧版只 WARNING）、M-2 列级严格验收只作用于 `ADD COLUMN`（本模块纯 `CREATE TABLE`，不受影响、无适配成本）、**M-3 checksum 漂移即启动失败关闭——迁移文件发布即冻结**。据此新增 **ADR-18**（表结构须在打包前定稿；发布后扩列走新增 `111_*.sql` 而非回头编辑）、**KL-12**，并把 §9 中迁移文件的风险由"零"上调为"低"、补两条迁移专项验收。附录 A 代码在当前 main 上重跑 **42 项全过**（沙箱 MariaDB 的 `int(11)` 差异非 G14 问题，加兼容垫片后全绿）。设计主体（口径、算法、接口、爆炸半径）**无实质变更**。 |
| Rev.E | 2026-08-29 | 智能体 A | 依第四轮内网实测（D3）**查明并消除 RISK-B 的差异**：那 78 张全部是 TDSQL 二级分区的物理子表，命名 `<逻辑表>_tdsql_subp190001` / `_tdsql_subp202601`…`202612`；6 张 `sub_func:month` 的表 × 13 个子分区 = 78，与 293 − 215 精确闭合。**ADR-17（新增）**：`_tdsql_subp<数字>` 结尾的表从基线剔除、单列 `subpartition_tables`（响应 + 两张表的 DDL 列 + 前端一列），并出一条 `SUBPARTITION_EXCLUDED`（INFO）说明剔除量。剔除后**逻辑基线 215 == Proxy 口径 215**，`RECON_MISMATCH` 从"在每个有二级分区的库上永久亮着的噪声"变回"亮起就意味着真有表没进 Proxy 路由表"的信号——推翻 Rev.D"两个数并排让用户自己判断"的做法（ADR-16 随之修订）。新增护栏用例 3 项，其中 `test_lzbj_ecif_uat_baseline` 把内网对数基准（98/117/0/215/215/78 且不报 RECON）直接编码为单测（共 42 项，全部通过）。§10 归档 D3，仅剩 T13；附录 B 增补第四轮原始数据与账目闭合验算。**GATE-2 仍无阻断项。** |
| Rev.D | 2026-08-29 | 智能体 A | 依第三轮内网实测（T14）**裁决 RISK-B：确认成立且差异达 27%**——`lzbj_ecif` Proxy 口径 215（98 分片 + 117 广播 + 0 单表）vs `information_schema` 基线 293，差 **78 张**。三处修订：**ADR-16**（四个数字采用 Proxy 口径，基线数并排呈现而不覆盖，新增 `baseline_tables` 字段与 DDL 列）；**ADR-15**（`RECON_MISMATCH` 汇总成一条——差异每库都有，逐库告警在 50 库实例上就是 50 条横幅）；**ADR-12 改写**——Rev.B 用"某库累计表集 == information_schema 基线"作为作用域探测的完备性证明，本次实测证明两者基本不可能相等、该判据永远不成立，改为**不依赖基线的指纹比对**（连续两个非空库的原始结果集逐条相同即证明实例级作用域，固定代价 6 条命令）。新增护栏用例 2 项（共 39 项，全部通过）。§10 归档 T14 并新增 D1～D3 诊断查询（78 = 6 × 13 且 `sub_func:month` 的表恰好 6 张，二级分区物理子表为最有力假说，待验证）。附录 B 增补第三轮数据与修订后的 UAT 对数基准（五个数字）。**GATE-2 仍无阻断项。** |
| Rev.C | 2026-08-29 | 智能体 A | 依第二轮内网实测（T15）**裁决 RISK-F**：命令不挂起，`mysql` 直连 Proxy 返回 `Query OK, 0 rows affected (0.001 sec)`—— 是 **OK 包**不是空结果集，赤兔转圈系其前端等列元数据所致。核对 PyMySQL 行为（本机 2.2.8 + 项目下限 1.1.0 wheel 源码）：OK 包 `fetchall()` 返回 `[]`、`description` 为 `None`，本模块天然按该类 0 张处理，**设计不改**。新增语义记录：OK 包与命令不被支持在协议上不可区分，§6.6 交叉校验是该静默失效模式的唯一探测器。新增护栏用例 2 项（共 37 项，全部通过）。附录 B 增补第二轮原始数据：`lzbj_ecif` = 98 分片 + 117 广播 + 0 单表 = **215**（UAT 对数基准）、`info` 列取值谱系、三条命令合计 0.004 秒。§10 归档 T15，剩余 T13 / T14 均标注**不阻断开发**；**GATE-2 无阻断项，可进入开发**。 |
| Rev.B | 2026-08-29 | 智能体 A | 依第一轮内网实测（附录 B）修订。**证伪 RISK-A**（三类互斥，归一化改作保险保留）；**锚定 RISK-C**（列名 `db_table`、`without shardkey` 单列、值为库限定名）；**新增 RISK-E**（命令作用域可能为实例级——Rev.A 会让总数放大 N 倍，改为按行内库限定名归属 + 全局 `(库,表)` 去重 + 覆盖性跳过，两种作用域下均正确）；**新增 RISK-F**（无单表的库上命令可能挂起——加 30s 读超时 + 180s 总预算 + `SKIPPED` 状态）。新增 ADR-11~14、E-18~23、W9~W11、`skipped_databases` 字段。§10 重写为"第一轮裁决表 + T13/T14/T15 三项补测 + GATE-2"。附录 A 代码同步更新（服务层 574 行 / 单测 594 行），**本地 35 项单测全部通过，仓库代码零改动**。 |
| Rev.A | 2026-08-29 | 智能体 A | 首版。需求拆解、现状勘查（含 `/*proxy*/` 存活性证据链）、三大语义风险（RISK-A/B/C/D）识别与对策、总体与详细设计、10 条 ADR、17 项异常矩阵、爆炸半径分析、12 个内网实测用例与 GATE-1 放行判据、附录 A 全套成品代码（服务层 489 行 / API 44 行 / 迁移 34 行 / 单测 455 行 / 既有文件 9 行改动 + 1 个前端块）。**附录 A 代码已在本地以 importlib 挂载方式跑通 32 项单测（含真实 MariaDB 落库），仓库代码零改动。** |
