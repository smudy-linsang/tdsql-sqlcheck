# TDSQL 性能诊断平台升级 · 概要设计文档

> 版本：v1.0（设计稿，仅设计不实施）
> 编制目的：吸收 TDSQL 原厂性能诊断工具集（`tdsql-toolkit`，15 个模块）的全部有效能力，在**完整保留我方现有功能**的前提下，对本平台做一次大面积能力升级与重构。
> 配套文档：本文为**概要设计**（讲清"做什么、为什么、怎么分层、优先级"）；照图施工级的"怎么实现"见配套《TDSQL 性能诊断平台升级 · 详细设计说明书》与已交付的《集群级慢SQL数据源(monitordb)接入设计说明书》。
> 阅读对象：DBA 专家（评审范围与优先级）+ 编码智能体（据此拆解开发任务）。

---

## 1. 升级背景与总目标

### 1.1 背景
- 我方已有一套 Web 化的「TDSQL SQL 审核 / 慢SQL / 大表治理 / 质量门禁」平台（FastAPI 后端 + 单页前端 + MySQL 元数据库）。
- TDSQL 原厂专家提供了一套成熟的命令行诊断工具集 `tdsql-toolkit`（15 模块，约 4.5 万行），沉淀了大量 **TDSQL 特有的运维诊断know-how**：尤其是**集群自带 monitordb（15001 端口 / `tdsqlpcloud_monitor` 库）作为全集群监控与慢SQL的权威数据源**这一关键入口。
- 原厂工具是"脚本 + CSV/HTML/Word/PPT 报告"形态，无 Web、无权限、无持久化平台化管理；我方平台恰好补足这些，但**诊断深度不及原厂**。二者互补。

### 1.2 总目标
1. **能力对齐**：把原厂"我方没有"的诊断能力，逐项吸收进平台（详见 §4 差距分析）。
2. **数据源升维**：以 monitordb（15001）为新的核心数据源，取代/增强当前"逐 SET 查 performance_schema"的取数方式。
3. **保留 + 平台化**：所有现有功能零回退；原厂脚本能力以"平台模块 + 持久化 + 权限 + 定时 + Web 报告"的形态重构落地。
4. **交付可施工蓝图**：每个升级项都给到照图施工级详细设计，任一编码智能体可独立完成。

### 1.3 硬约束（贯穿始终）
- 现有功能与全量回归**不得回退**（基线：885 passed / 55 skipped / 0 failed）。
- 所有改动落 **main** 分支；改完即自测即提交。
- "AI 说做完不算数"：每个验收项须有**真实证据**（真库 SQL 返回 / pytest 输出 / 报告截图）。
- 严格只读/最小权限：对被诊断实例只做 `SELECT`/`EXPLAIN`/`SHOW`，写操作一律禁止（EXPLAIN 安全见详细设计）。

---

## 2. 原厂工具能力全景（15 模块）

| # | 模块 | 一句话能力 | 核心数据源 | 与我方产品的契合度 |
|---|---|---|---|---|
| 1 | `slow_query_export` | 全集群慢SQL统计 + EXPLAIN/表结构/索引/统计信息**十列增强** | monitordb `proxy_classes_analysis` + 业务库 | ★★★ 极高 |
| 2 | `tdsql-deep-inspection` | 集群**深度巡检**：DB/Proxy/管控组件 29+ 项 + 拓扑 + 备份 + 趋势 + Word报告 | monitordb `m_data_cur` 等 | ★★★ 极高 |
| 3 | `daily_inspection` | **每日巡检** 7 指标 + 多日趋势对比 + 多集群合并 | monitordb `m_data_cur` | ★★★ 极高 |
| 4 | `index_analysis` | **索引深度分析**：区分度/未用/低用/冗余/碎片/过多/自增耗尽 | 业务库 information_schema + performance_schema | ★★★ 极高 |
| 5 | `sql_analysis` | SQL 调用量/耗时/全表扫描 TOP-N 分析 | performance_schema digest / processlist / slowlog | ★★☆ 高（与我方慢SQL部分重叠） |
| 6 | `table_schema_diff` | **表结构比对**（生产 vs 测试）：表/列/索引/触发器，分级 | 业务库导出 | ★★★ 高（可增强上线检查） |
| 7 | `count_table_rows` | 表行数统计 + 大表排行 + **增长趋势** + 表类型分布 | 业务库 | ★★☆ 高（与我方大表治理重叠+增强） |
| 8 | `collect_table_stats` | 大表统计信息定时采集（ANALYZE / light） | 业务库 | ★★☆ 中（大表治理补充） |
| 9 | `gateway_log_analysis` | **Proxy(Gateway)日志分析**：6 类日志 15 章节 + interf 深度诊断 | Proxy 节点日志文件 | ★★☆ 中（新数据面，需日志可达） |
| 10 | `mysql_emergency_diag` | **应急诊断**：大事务/锁等待/未提交/连接打满/CPU飙高 一键定位 | 业务库实时视图 | ★★☆ 高（补齐应急场景） |
| 11 | `find_pk_field` | 查找主键最左字段=X 的表 | 业务库 information_schema | ★☆☆ 低（工具型） |
| 12 | `auto_report` | 汇聚各模块产出 → 运维汇报 **PPT** 自动生成 | 各模块输出 | ★★☆ 中（我方已有HTML报告，可加PPT/汇总） |
| 13 | `disk_performance_test` | 磁盘 IO 基准（dd/fio）批量测试 | 主机 SSH | ☆ 基础设施类，Web平台契合度低 |
| 14 | `sshpass_pack` | 借 scheduler 批量远程执行命令 | scheduler | ☆ 基础设施类，Web平台契合度低 |
| 15 | `tdsql_inventory.sh` + `tdsql_env_loader.sh` | **ZK 自动发现集群全部实例**（输出 host,port,user,pass,db） | ZooKeeper 2118 | ★★★ 高（免手工登记实例，重大易用性提升） |

> 结论：15 个模块里，**10 个是"数据分析/诊断"类**，与我方 Web 平台高度契合，是本次升级主体；`disk_performance_test`/`sshpass_pack` 是主机基础设施脚本，Web 平台契合度低，列为**可选/低优先**（见 §5）。

---

## 3. 我方现状能力盘点

| 领域 | 现有能力（代码位置） | 深度 |
|---|---|---|
| SQL 静态审核 | `sql_audit` + `engine/checker` + `engine/rules` + 规则集多租户 | 成熟 |
| 上线检查 | `inspection`（C01~C12 schema 检查：字符集/无主键/长表名/索引数/timestamp/varchar 等） | 成熟 |
| 慢SQL 分析 | `slow_query` + `engine/slow_analyzer`（EXPLAIN 分析：全表扫描/filesort/临时表/join buffer/覆盖索引）+ `index_advisor` + `sql_rewriter` | 较成熟，取数=逐SET performance_schema |
| 大表治理 | `bigtable` + `engine/bigtable_engine`（清单/分区下钻/分片键/合理性标记） | 较成熟 |
| 分布式辅助 | `engine/distributed_explain`、`charset_diagnoser`、`deadlock_analyzer`、`long_transaction` | 局部 |
| 质量门禁/CI | `quality_gate` + `gate_service` + `gitlab_hook` | 成熟 |
| 平台能力 | RBAC 认证、多连接注册表、项目、规则集、调度、数据保留、脱敏、可观测性、Web 前端 | 成熟 |

**我方短板**（相对原厂）：
- 取数依赖逐 SET performance_schema（需手工 set_list、无 user/host、内存易失）——已在 monitordb 设计中解决。
- **无集群级健康巡检**（DB/Proxy/管控组件/备份/趋势）。
- **无实例级索引健康审计**（只有 per-SQL 的 index_advisor）。
- **无跨实例表结构比对**。
- **无每日巡检 + 多日趋势**。
- **无应急诊断一键包**。
- **无 Proxy 日志分析**。
- **无 ZK 自动发现实例**（靠手工登记）。
- 慢SQL **无统计信息过期/冗余索引/扫描效率**等增强诊断维度。

---

## 4. 能力差距分析（原厂有、我方没有 → 升级范围）

> 这是本次升级的**范围清单**。每项标注：能力、原厂来源、我方现状、落地形态、优先级。

| 编号 | 新增能力 | 原厂来源 | 我方现状 | 平台落地形态 | 优先级 |
|---|---|---|---|---|---|
| **G1** | **monitordb 集群级慢SQL数据源** | `slow_query_export` | 逐SET performance_schema | 慢SQL新增 `source=monitordb`（已出详细设计） | **P0** |
| **G2** | 慢SQL **十列增强诊断**（EXPLAIN/涉及表/表数据量/表结构/索引详情/冗余索引/统计信息过期/扫描效率/EXPLAIN问题标记） | `slow_sql_enrich.py` | 部分（slow_analyzer 有 EXPLAIN 问题识别） | 慢SQL明细"诊断"子面板 + 落库增强字段 | **P0** |
| **G3** | **集群深度巡检**（DB/Proxy/ZK/Scheduler/OSS 29+项 + 拓扑 + 备份 + 阈值告警 + 趋势） | `tdsql-deep-inspection` | 无 | 新"集群巡检"模块 + 巡检报告(HTML/Word) | **P0** |
| **G4** | **每日巡检 + 多日趋势对比**（7 指标 + 折线图 + 多集群合并） | `daily_inspection` + `compare_reports` | 无 | 新"每日巡检"模块 + 定时任务 + 趋势看板 | **P1** |
| **G5** | **实例级索引健康审计**（区分度/未用/低用/重复/前缀冗余/碎片/过多/自增耗尽） | `index_analysis` | per-SQL index_advisor | 新"索引体检"模块（复用/扩展 index_advisor） | **P1** |
| **G6** | **跨实例表结构比对**（表/列/索引/触发器，CRITICAL/HIGH/MEDIUM/INFO） | `table_schema_diff` | 无 | 新"结构比对"模块 + HTML 差异报告 | **P1** |
| **G7** | **应急诊断一键包**（大事务/锁等待/未提交/连接打满/CPU飙高/InnoDB状态/死锁） | `mysql_emergency_diag` | 局部(deadlock/long_transaction) | 新"应急诊断"模块（整合现有analyzer） | **P1** |
| **G8** | **SQL 调用量分析**（TOP-N 高频/耗时/慢/全表扫描 + 类型分布） | `sql_analysis` | 慢SQL 部分重叠 | 并入 monitordb 慢SQL的多维统计视图 | **P2** |
| **G9** | **大表增长趋势 + 表类型分布**（估算/精确计数、增长排行） | `count_table_rows` / `collect_table_stats` | 大表治理清单 | 大表治理增"趋势/类型分布"，接 monitordb/定时采集 | **P2** |
| **G10** | **ZK 自动发现实例**（免手工登记） | `tdsql_inventory.sh` | 手工登记连接 | 连接管理增"从集群自动发现"（可选，需ZK可达） | **P2** |
| **G11** | **Proxy(Gateway) 日志分析**（6 类日志 15 章节 + interf 深度） | `gateway_log_analysis` | 无 | 新"网关日志分析"模块（需日志文件可达） | **P2** |
| **G12** | **运维汇报 PPT / 集群总览大屏** | `auto_report` | HTML 报告 | 汇总看板 + 可选 PPT 导出 | **P2** |
| **G13** | 主机磁盘性能测试 / 批量远程执行 | `disk_performance_test` / `sshpass_pack` | 无 | **不建议纳入 Web 平台**（基础设施类，形态不符）；如需保留可作为"运维脚本工具箱"外挂 | **可选** |

> 优先级定义：**P0** = 本次升级核心、直接服务用户诉求（慢SQL权威数据源 + 深度巡检），先做；**P1** = 高价值诊断能力，紧随其后；**P2** = 增强/易用性，视资源排期；**可选** = 形态不符，建议不纳入或外挂。

---

## 5. 升级总体架构

### 5.1 分层视图

```
┌──────────────────────────────────────────────────────────────────────┐
│  前端 SPA（保留现有 + 新增：集群巡检 / 索引体检 / 结构比对 / 应急诊断 / 趋势看板）│
├──────────────────────────────────────────────────────────────────────┤
│  API 层（FastAPI，新增 inspection_cluster / index_audit / schema_diff /   │
│          emergency_diag / trend 等 router，保留全部现有 router）           │
├──────────────────────────────────────────────────────────────────────┤
│  服务/引擎层                                                             │
│   现有：scan/slow_query/bigtable/inspection/gate/ruleset/... （保留）      │
│   新增：cluster_inspect_service / index_audit_service /                   │
│         schema_diff_service / emergency_diag_service /                    │
│         slow_enrich_service / trend_service                              │
├──────────────────────────────────────────────────────────────────────┤
│  数据源接入层（本次重构核心）                                              │
│   ① monitordb 接入器（15001 / tdsqlpcloud_monitor）——新核心              │
│      · proxy_classes_analysis  → 集群慢SQL                               │
│      · proxy_global_analysis   → 每SET全局统计                           │
│      · m_data_cur (KV指标)     → 巡检/趋势/健康                          │
│      · t_cluster / 备份表 / 拓扑 → 深度巡检                              │
│   ② 业务库接入器（现有 tdsql_connector）——EXPLAIN/表结构/索引/应急        │
│   ③ (可选) ZK 发现器 / Proxy 日志采集器                                   │
├──────────────────────────────────────────────────────────────────────┤
│  元数据库（MySQL，现有 27 表 + 新增巡检结果/趋势/索引审计/结构差异等表）      │
└──────────────────────────────────────────────────────────────────────┘
```

### 5.2 核心设计原则
1. **monitordb 优先**：凡"集群级、已聚合、可归因"的数据（慢SQL、健康指标、趋势、备份、拓扑）一律优先取 monitordb；凡"需要实时/明细/EXPLAIN/表结构"的数据取业务库。
2. **保留即回退红线**：新增能力全部以"新模块/新 source/新字段（带默认值迁移）"方式加入，不改动现有路径语义。
3. **原厂算法照搬阈值**：区分度/碎片率/统计过期天数/巡检告警阈值等，**直接采用原厂经过生产验证的阈值**（见详细设计各章），不自创。
4. **平台化增值**：在原厂脚本基础上补齐——持久化、RBAC、定时调度、Web 交互报告、历史留存、多实例并管。

### 5.3 monitordb 数据源清单（新核心数据面）
> 一个 TDSQL 集群一份，端口 15001，库 `tdsqlpcloud_monitor`。经源码确认，本次要用到的表/入口：

| 用途 | 表 / 入口 | 关键列/键 |
|---|---|---|
| 集群慢SQL明细 | `proxy_classes_analysis` | checksum/fingerprint/user/host/db/set_name/query_time_*/rows_*/ts_min/ts_max/timestramp |
| 每SET全局统计 | `proxy_global_analysis` | unique_query_count/query_time_*/lock_time_*/rows_* |
| 健康/巡检指标(KV) | `m_data_cur` | f_mid, f_key（cpu_usage/slow_query/slave_delay/data_dir_usage/table_hit_rate/no_primary_key_table_nums/oss_cpu/oss_memory/...）, f_val, f_type |
| 集群名 | `tdsqlpcloud.t_cluster` | cluster_id=1 → cluster_name |
| 实例名映射 | `m_data_cur` (f_key='instance_name') / ZK | f_mid → instance_id/name |
| 备份/趋势 | 深度巡检相关监控表 | 见详细设计 §深度巡检 |

---

## 6. 与现有系统的集成与兼容策略

| 方面 | 策略 |
|---|---|
| 慢SQL | 现有 `digest`/`processlist` 两 source 保留；新增 `monitordb` source 作为分布式实例默认。互不影响。 |
| 连接模型 | 现有连接不变；新增 monitordb 附加字段（monitor_host/port/user/password/db，默认空=复用主连接换端口）。存量连接零影响。 |
| 元数据库 | 所有新表/新列走 `_add_column_if_not_exists` 幂等迁移，带默认值；不改现有表语义。 |
| 权限 | 新模块纳入现有 RBAC；对被诊断实例仅只读。 |
| 报告 | 沿用现有 HTML 报告风格与严重度体系（**ERROR/WARNING/INFO**，无 CRITICAL——与我方慢SQL规则体系一致）；深度巡检/结构比对因原厂用 FATAL/CRITICAL/HIGH/MEDIUM/INFO，需**做一次严重度映射**（见详细设计，避免报告口径混乱）。 |
| 定时 | 每日巡检/大表采集接入现有 scheduler。 |
| 回退红线 | 全量 pytest 基线 885/55/0 不回退，作为每次提交门槛。 |

> ⚠️ **严重度口径统一（重要）**：我方慢SQL/审核体系只有 **ERROR/WARNING/INFO**。原厂巡检类用 FATAL/CRITICAL/HIGH/MEDIUM/INFO。落地时**必须在服务层做映射**（建议：FATAL/CRITICAL→ERROR、HIGH→ERROR 或独立"高"、MEDIUM→WARNING、INFO→INFO），并在报告与看板统一展示，杜绝"报告里冒出体系里不存在的等级"（此前已因 CRITICAL 空卡片踩过坑）。

---

## 7. 非功能性设计

| 项 | 要求 |
|---|---|
| 安全 | 被诊断实例只读；EXPLAIN 仅 `EXPLAIN SELECT`（UPDATE/DELETE 转写 SELECT，拒绝分号，最终校验前缀）；monitordb 密码加密存储（复用现有密钥管理）。 |
| 性能 | monitordb 查询命中既有索引（index_time/index_db/index_set）；巡检/增强类查询设超时（原厂 30s）；批量表信息查询去重缓存（原厂已实现）。 |
| 兼容 | 防御式列裁剪防跨 TDSQL 版本列差异；MySQL 5.7/8.0 语法兼容（避免 REGEXP_REPLACE，用 REPLACE 嵌套）。 |
| 可观测 | 复用现有可观测性；每个诊断任务落任务表，可追溯。 |
| 可维护 | 阈值全部集中为可配置常量/配置段，不散落魔法数。 |

---

## 8. 交付物与建议里程碑

| 阶段 | 交付 | 内容 |
|---|---|---|
| M0（本阶段，设计） | 3 份设计文档 | 本概要 + 详细说明书 + monitordb 接入说明书（已出） |
| M1（P0） | monitordb 慢SQL数据源 + 慢SQL十列增强 | G1 + G2 |
| M2（P0） | 集群深度巡检 | G3 |
| M3（P1） | 每日巡检趋势 / 索引体检 / 结构比对 / 应急诊断 | G4~G7 |
| M4（P2） | SQL调用量分析 / 大表趋势 / ZK发现 / 网关日志 / 汇报 | G8~G12 |

> 每个里程碑内部遵循：详细设计已就绪 → 编码 → 真库自测(留证) → 全量回归 885/55/0 → 提交 main。

---

## 9. 风险与对策

| 风险 | 对策 |
|---|---|
| monitordb 账号/端口在客户环境不可达或权限不足 | 设计"连通探测"端点先行验证；不可达时明确报错并回退现有 source（慢SQL）或跳过（巡检） |
| 不同 TDSQL 版本 monitordb 表结构差异 | 防御式列裁剪 + 现场 DESCRIBE 校准（monitordb 设计 §6 已具备） |
| 原厂部分能力依赖 ZK/日志文件/主机SSH，Web 平台可能不具备该通路 | 这些能力（G10/G11/G13）设为 P2/可选，且以"通路可用才启用"的开关式集成 |
| 严重度体系不一致导致报告口径混乱 | §6 强制严重度映射 |
| 一次性重构面过大 | 按 P0→P2 分里程碑增量交付，每步不破坏回退红线 |

---

## 10. 附：原厂关键 know-how 速查（供详细设计引用）

- monitordb 慢SQL：单位=秒（无需换算）；时间窗过滤走 `timestramp`；加权平均=`SUM(avg*count)/SUM(count)`；系统账号 + 噪音SQL过滤清单；指纹归一化去重。
- 安全 EXPLAIN：只 `EXPLAIN SELECT`，UPDATE/DELETE→SELECT，拒绝分号，最终前缀校验。
- 索引审计阈值：区分度 0.9/0.5/0.1/0.01；未用索引 count_read=0（uptime<7天存疑）；低用<5%；过多>8；碎片=DATA_FREE/(DATA+INDEX+DATA_FREE) 且≥1MB；自增使用率≥40% 告警。
- 统计信息过期：`mysql.innodb_table_stats.last_update` 超 15 天 → 建议 ANALYZE（TDSQL 需 `/*sets:allsets*/`）。
- 扫描效率：返回行/扫描行；≥0.8优秀 / ≥0.5良好 / ≥0.1较低 / <0.1极低。
- 巡检阈值：CPU 70/90，内存 120/150，连接 70/85，主备延迟 5/30（warning/critical）。
- 结构比对严重度：缺表/缺索引=CRITICAL，缺列=HIGH，类型/索引列不一致=MEDIUM，多余=INFO。
