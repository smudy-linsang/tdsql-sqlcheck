# TDSQL 性能诊断平台升级 · 详细设计说明书（照图施工）

> 版本：v1.0（设计稿，仅设计不实施）
> 文档性质：**照图施工级详细设计**。每个能力给到：数据源+精确 SQL、精确阈值、后端方法签名、API 契约、元数据库表、前端交互、字段映射、验收标准。任一编码智能体据此即可保质保量完成开发。
> 配套：本文与《概要设计文档》《集群级慢SQL数据源(monitordb)接入设计说明书》三份配套；G1（慢SQL数据源）已单独成文，本文只做引用与衔接，不重复。
> 通用纪律（每章都适用）：仅 main 分支；改完即自测即提交；被诊断实例只读；阈值集中为可配置常量；每验收项须真实证据；全量 pytest 基线 **885 passed / 55 skipped / 0 failed** 不回退。

---

## 0. 全局约定

### 0.1 严重度映射（★所有巡检/诊断类共用）
我方体系只有 **ERROR / WARNING / INFO**。原厂巡检用 FATAL/CRITICAL/HIGH/MEDIUM/INFO。统一映射函数（放 `backend/engine/severity_map.py`，全平台共用）：

```python
def map_severity(vendor_level: str) -> str:
    v = (vendor_level or "").upper()
    if v in ("FATAL", "CRITICAL", "HIGH"):
        return "ERROR"
    if v in ("MEDIUM", "WARNING", "WARN"):
        return "WARNING"
    return "INFO"      # LOW / INFO / OK / 其它
```
> 报告与看板一律只出现 ERROR/WARNING/INFO 三级。禁止把 CRITICAL 等直接透传到前端（此前已因空 CRITICAL 卡片踩坑）。

### 0.2 monitordb 接入器（G1 已定义，本文各章直接调用）
G1 已在连接器新增：`_monitor_conn_params()` / `_monitor_execute(sql, params)` / `monitor_probe()` / `get_cluster_slow_queries(...)`。深度巡检/趋势等章节**复用 `_monitor_execute`** 读 `m_data_cur` 等表。monitordb 连接配置字段（monitor_host/port/user/password/db）见 G1 §4。

### 0.3 单位与事实（monitordb，已确认）
- `proxy_classes_analysis.query_time_*`/`lock_time_*` = **float 秒**（系数 1）。
- 时间窗过滤走 `timestramp`（采集时刻，timestamp 类型）；`ts_min/ts_max`（datetime）用于展示首末执行。
- `m_data_cur` 是 KV 指标表：`f_mid`(监控对象ID) / `f_key`(指标名) / `f_val`(值) / `f_type`(类型) / `f_pmid`(父对象)。实例过滤用 `f_mid LIKE '/tdsqlzk/<instance_id>%' OR f_pmid LIKE ...`。

---

## G2. 慢SQL 十列增强诊断（源自 `slow_sql_enrich.py`）

### G2.1 能力目标
对每条慢SQL（无论来自 monitordb / digest），连接**对应业务库**追加十项诊断，落库并在慢SQL明细页"诊断"子面板展示：

| 增强项 | 含义 | 数据来源 |
|---|---|---|
| EXPLAIN执行计划 | 安全 EXPLAIN 的可读计划 | 业务库 `EXPLAIN SELECT` |
| EXPLAIN问题标记 | type=ALL/index、filesort、临时表、rows过大、key=NULL | 解析 EXPLAIN |
| 涉及表 | 从 SQL 提取的表名 | 正则解析 |
| 表数据量 | TABLE_ROWS / DATA_LENGTH / INDEX_LENGTH / 引擎 | `information_schema.TABLES` |
| 表结构 | SHOW CREATE TABLE | 业务库 |
| 索引详情 | 索引名(列)[UNIQUE] CARDINALITY TYPE | `information_schema.STATISTICS` |
| 冗余索引 | 冗余→被包含 | `sys.schema_redundant_indexes` |
| 统计信息更新时间 | 统计更新/数据修改时间 | `mysql.innodb_table_stats` + `TABLES.UPDATE_TIME` |
| 统计信息是否过期 | >15天 → 建议 ANALYZE | 计算 |
| 扫描效率 | 返回行/扫描行，分级 | 计算 |

### G2.2 后端：新增 `backend/services/slow_enrich_service.py`
移植 `slow_sql_enrich.py` 的纯逻辑为服务方法（改 `mysql` 子进程为 pymysql，复用现有业务库连接器 `pool._execute`）：

```python
# 纯函数（可单测，无DB）
def clean_sql(sql_text: str) -> str: ...          # 去/*..*/透传注释、压缩空白
def convert_to_select(sql_text: str) -> str|None: # UPDATE/DELETE→SELECT
def safe_sql_for_explain(sql_text: str) -> tuple[str|None, str|None]:
    # 返回(explain_sql, skip_reason)。规则(照搬原厂，优先级最高)：
    #  1) 去分号后若仍含';'→拒绝(防多语句注入)
    #  2) 首词SELECT→'EXPLAIN '+s
    #  3) 首词UPDATE/DELETE→convert_to_select后'EXPLAIN '+select
    #  4) INSERT/REPLACE/SET/SHOW/USE/BEGIN/COMMIT/DDL→跳过
    #  5) 最终校验：explain_sql.upper().startswith('EXPLAIN SELECT')否则拦截
def extract_tables_from_sql(sql_text: str) -> list[str]:
    # 正则提取 FROM/JOIN/UPDATE/INSERT 目标表；过滤SQL关键字+系统库(见原厂 _filter_table_names)
def extract_explain_issues(explain_text: str) -> str:
    # type=ALL→❌全表扫描; type=index→⚠️索引全扫描; Using filesort/temporary→⚠️;
    # rows>100000→❌ rows>10000→⚠️; key=NULL→❌未用索引; 无→"无明显问题"
def calc_scan_efficiency(examined: float, sent: float) -> str:
    # eff=sent/examined; ≥0.8优秀 ≥0.5良好 ≥0.1⚠️较低 <0.1❌极低
STATS_EXPIRE_DAYS = 15   # 统计过期阈值(可配)
```

数据库联动方法（入参 pool=业务库连接池）：
- `get_explain(pool, db, sql)`：`safe_sql_for_explain`→跳过则返回`N/A(原因)`；否则执行 `EXPLAIN`，把 tab 表格转 `col=val | col=val` 可读串。
- `get_table_stats(pool, db, table)`：`SELECT TABLE_ROWS, ROUND(DATA_LENGTH/1024/1024,2), ROUND(INDEX_LENGTH/1024/1024,2), ROUND((DATA_LENGTH+INDEX_LENGTH)/1024/1024,2), ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s`。**大表注意**：分区表 TABLES 可能低估，若已接入我方大表治理的 PARTITIONS 双源取大逻辑则复用（见现有 `build_large_tables_query`）。
- `get_table_schema(pool, db, table)`：`SHOW CREATE TABLE`。
- `get_index_details(pool, db, table)`：`SELECT INDEX_NAME,COLUMN_NAME,SEQ_IN_INDEX,NON_UNIQUE,CARDINALITY,INDEX_TYPE,NULLABLE FROM information_schema.STATISTICS WHERE ... ORDER BY INDEX_NAME,SEQ_IN_INDEX`，按索引名聚合列。
- `get_redundant_indexes(pool, db, table)`：`SELECT redundant_index_name,redundant_index_columns,dominant_index_name,dominant_index_columns FROM sys.schema_redundant_indexes WHERE table_schema=%s AND table_name=%s`；sys 不可用→`N/A(sys库不可用)`。
- `get_stats_update_info(pool, db, table)`：`/*sets:allsets*/SELECT last_update,n_rows FROM mysql.innodb_table_stats WHERE database_name=%s AND table_name=%s`（分布式多行取最早 last_update、行数求和）+ `SELECT UPDATE_TIME,CREATE_TIME FROM information_schema.TABLES ...`；`(now-last_update).days>15`→过期，建议 `ANALYZE TABLE`。

**批量去重缓存**：先收集所有 `(db,table)` 对，对每张表各查一次（原厂已实现 5 个 cache dict），避免逐行重复查。每查设 30s 超时。

### G2.3 元数据库：`slow_queries` 增强列（幂等迁移）
在 `backend/services/database.py` 用 `_add_column_if_not_exists` 加：
```sql
explain_plan       TEXT,        explain_issues     VARCHAR(1000) DEFAULT '',
involved_tables    VARCHAR(512) DEFAULT '',  table_stats  TEXT,
table_schema_ddl   TEXT,        index_details      TEXT,
redundant_indexes  VARCHAR(1000) DEFAULT '', stats_update_info TEXT,
stats_expired      VARCHAR(512) DEFAULT '',  scan_efficiency  VARCHAR(64) DEFAULT '',
rows_affected      BIGINT DEFAULT 0
```
> 现有 `index_suggestions`/`rewrite_suggestions`/`optimized_sql` 列保留，与新列并存。

### G2.4 集成点
- `scan_service.run_scan(...)`：扫描落库后，若开启"增强"（新参数 `enrich=True`，默认对 monitordb/digest 源开），对 TopN 慢SQL调用 `slow_enrich_service.enrich_rows(rows, pool)`，把十列写回 `slow_queries`。增强对被诊断实例只读、可失败不阻断主流程（失败该行填 `N/A`）。
- 若与现有 `slow_analyzer` 的 EXPLAIN 分析重叠：以 `slow_analyzer` 为"结论/建议"，`slow_enrich` 为"证据/明细"，二者并存互补，不删现有。

### G2.5 API / 前端
- 复用现有慢SQL明细接口，响应体增十字段；慢SQL明细行展开"诊断"子面板分块展示（EXPLAIN、问题标记高亮红/黄、涉及表→表数据量/结构/索引/冗余/统计过期/扫描效率）。
- HTML 报告增"诊断"列或明细页。

### G2.6 验收（真实证据）
| AC | 判据 |
|---|---|
| G2-01 | 一条 `UPDATE...WHERE` 慢SQL被转写为 `EXPLAIN SELECT * FROM ... WHERE ...` 正确执行，绝不执行到真实 UPDATE。附日志。 |
| G2-02 | 含分号/多语句的 SQL 被拒绝，标记"含分号跳过"。 |
| G2-03 | 全表扫描 SQL 的 EXPLAIN问题标记出现"❌ 全表扫描(type=ALL)"。 |
| G2-04 | 统计信息>15天的表标"⚠️已N天未更新，建议 ANALYZE"。附 innodb_table_stats.last_update。 |
| G2-05 | 扫描效率对"扫描1万返回1行"的SQL给出"❌极低"。 |
| G2-06 | 批量缓存：同一张表在多条SQL中只被查询一次（日志计数）。 |
| G2-07 | 增强失败不阻断扫描主流程；失败行落 N/A。 |

---

## G3. 集群深度巡检（源自 `tdsql-deep-inspection`）

### G3.1 能力目标
一键对 TDSQL 集群做健康巡检：读 monitordb `m_data_cur` 等表，采集 DB/Proxy/实例/管控组件指标 → 按阈值判级 → 产出"异常汇总 + 分层明细 + 趋势"的巡检报告（HTML，可选 Word），落库可追溯。

### G3.2 阈值表（照搬原厂 `T`，集中为配置）
放 `backend/services/cluster_inspect_service.py` 顶部常量（可被 DB 配置覆盖）：
```python
INSPECT_THRESHOLDS = {
  "cpu_w":70,"cpu_c":90,          # DB CPU 使用率 警告/严重(%)
  "mem_w":120,"mem_c":150,        # 内存使用率
  "conn_w":70,"conn_c":85,        # 连接使用率
  "delay_w":5,"delay_c":30,       # 主备延迟(s)
  "dev_cpu_w":70,"dev_cpu_c":90,  # 宿主机CPU
  "dev_disk_w":75,"dev_disk_c":90,# 宿主机磁盘
  "zk_conn_w":500,"zk_conn_c":1500,
  # data_dir_usage(数据盘)、table_hit_rate(命中率,取min)等阈值同原厂
}
```
> `_w`=WARNING 线，`_c`=CRITICAL 线（映射后=ERROR）。

### G3.3 采集：从 `m_data_cur` 读指标
通用取值函数（复用 `_monitor_execute`）：
```python
def metric_current(instance_id, f_key, div=1, rnd=0):
    # SELECT ROUND(f_val/{div},{rnd}) FROM m_data_cur
    #  WHERE f_mid LIKE '%{instance_id}%' AND f_key=%s AND f_type=1 LIMIT 1
def metric_peak(instance_id, f_key):   # 当天峰值(MAX)
def metric_avg(instance_id, f_key):    # 当天均值
def metric_min(instance_id, f_key):    # 当天最小(如命中率)
def metric_sum(instance_id, f_key):    # 当天求和(如错误SQL数)
    # 过滤：f_pmid LIKE '/tdsqlzk/{instance_id}%' OR f_mid LIKE '/tdsqlzk/{instance_id}%'
```
关键指标键（f_key，已从源码确认）：
`cpu_usage` / `cpu_usage_max` / `mysql_max_mem_usage` / `slow_query` / `slave_delay` / `data_dir_usage` / `connect_usage` / `table_hit_rate` / `no_primary_key_table_nums` / `myisam_table_nums` / `mysql_master_switch` / `binlog_error` / `alive` / `processlist_slow_sql` / `proxy_sum_connect_count` / `proxy_sum_total_error_sql` / `mysql_sum_conn_active`；实例规格：`oss_cpu`(÷100=核) / `oss_memory` / `oss_data_disk` / `oss_log_disk`(÷1000)。

### G3.4 巡检项与判级（照搬原厂分类）
四大类 `availability / reliability / performance / maintainability`，每项 `_add(category, level, node, title, detail, value, threshold, cmd, evidence)`：
- **可用性**：`alive`（存活）、连通性、Proxy 可用。
- **可靠性**：Slave IO/SQL 线程（异常=FATAL）、`slave_delay≥delay_c`→CRITICAL、`≥delay_w`→WARNING；`mysql_master_switch`（主备切换）、`binlog_error`；**备份状态**（未备份 SET 标红）。
- **性能**：`cpu_usage`/`cpu_usage_max` vs cpu_w/c；`mysql_max_mem_usage` vs mem；`connect_usage` vs conn；`data_dir_usage`（数据盘）；`table_hit_rate`（命中率低告警）；`slow_query`（慢查询数）。
- **可维护性**：`no_primary_key_table_nums>0`（无主键表）、`myisam_table_nums>0`（非InnoDB）、版本统一性、监控覆盖。

### G3.5 元数据库表（新增）
```sql
CREATE TABLE cluster_inspection (         -- 巡检任务
  id INT PK AUTO_INCREMENT, connection_id VARCHAR(64), cluster_name VARCHAR(128),
  inspect_date VARCHAR(32), trend_days INT DEFAULT 7,
  total_issues INT, error_count INT, warning_count INT, info_count INT,
  summary_json MEDIUMTEXT,   -- 拓扑/规格/备份汇总
  created_by VARCHAR(64), created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP );
CREATE TABLE cluster_inspection_issue (   -- 巡检明细
  id INT PK AUTO_INCREMENT, inspection_id INT, category VARCHAR(32),
  severity VARCHAR(32),      -- 映射后 ERROR/WARNING/INFO
  node VARCHAR(128), title VARCHAR(256), detail TEXT,
  metric_value VARCHAR(64), threshold VARCHAR(64), evidence TEXT,
  INDEX idx_ci (inspection_id) );
```

### G3.6 后端方法 / API
`cluster_inspect_service.run_inspection(connection_id, inspect_date=None, trend_days=7) -> dict`：
1. `monitor_probe` 确认 monitordb 可达；取 `cluster_name`（`tdsqlpcloud.t_cluster` cluster_id=1）。
2. 枚举实例（`m_data_cur` f_key='instance_name' 或指定 `--id/--instances`）。
3. 逐实例采集指标→判级→`map_severity`→落 `cluster_inspection_issue`。
4. 汇总落 `cluster_inspection`；生成 HTML 报告。
API（新 router `backend/api/cluster_inspect.py`，前缀 `/api/v1/cluster-inspect`）：
- `POST /run` body `{connection_id, inspect_date?, trend_days?}` → 巡检并返回汇总。
- `GET /list/{connection_id}` → 历史巡检列表。
- `GET /report/{inspection_id}` → HTML 报告。
- `GET /issues/{inspection_id}` → 明细（支持 severity 过滤）。

### G3.7 趋势（近 N 天）
对关键指标（cpu/mem/命中率/慢查询/主备延迟/数据盘/Proxy时耗/连接/请求量）用 `metric_peak/avg` 按天取值，产出折线图数据（前端渲染）。趋势数据可来自 `m_data_cur` 的历史分表（monitordb 有 `m_data_YYYYMMDD` 日分区表，见库内表清单）。

### G3.8 前端
新"集群巡检"页：发起巡检 → 异常汇总卡（ERROR/WARNING/INFO 计数）→ 分类明细表（可按严重度/类别过滤）→ 趋势折线 → 拓扑/规格/备份卡。

### G3.9 验收
| AC | 判据 |
|---|---|
| G3-01 | 真库巡检产出的某指标值（如 cpu_usage 峰值）与直接查 `m_data_cur` 一致。附两侧SQL。 |
| G3-02 | 人为造一个 `slave_delay` 超阈值场景，巡检报 ERROR 且给出阈值与实测值。 |
| G3-03 | 报告只出现 ERROR/WARNING/INFO，无 FATAL/CRITICAL 字样透传前端。 |
| G3-04 | 未备份 SET 被标红并计入汇总。 |
| G3-05 | monitordb 不可达时明确报错，不产生空报告。 |

---

## G5. 实例级索引健康审计（源自 `index_analysis`）

### G5.1 能力目标
对一个业务实例的全部（或指定库）表做索引体检，输出 8 类问题清单 + 概览。

### G5.2 采集 SQL（业务库，只读）
- 索引元数据：`information_schema.STATISTICS`（INDEX_NAME/COLUMN_NAME/SEQ_IN_INDEX/NON_UNIQUE/CARDINALITY/INDEX_TYPE/NULLABLE/SUB_PART）。
- 表行数/大小/碎片：`information_schema.TABLES`（TABLE_ROWS/DATA_LENGTH/INDEX_LENGTH/DATA_FREE/AUTO_INCREMENT/ENGINE）。
- 索引使用统计：`performance_schema.table_io_waits_summary_by_index_usage`（count_read/count_write/count_fetch）——需 performance_schema 可用；分布式需 `/*sets:allsets*/` 或逐 SET。
- 自增列上限：结合列类型（int/bigint 有无 unsigned）算最大值，`AUTO_INCREMENT/max` 得使用率。
- 运行时长：`SHOW GLOBAL STATUS LIKE 'Uptime'`（未用索引 uptime<7天时降级为"存疑"）。

### G5.3 判定阈值（照搬原厂）
```python
SELECTIVITY_EXCELLENT=0.9; SELECTIVITY_GOOD=0.5; SELECTIVITY_FAIR=0.1; SELECTIVITY_POOR=0.01
MIN_READ_RATIO=0.05         # 低利用率<5%
MAX_INDEXES_PER_TABLE=8     # 单表索引过多
MIN_FRAG_MB=1               # 碎片≥1MB才报
AUTOINC_WARN_PCT=40         # 自增使用率≥40%告警
UNUSED_UPTIME_MIN_DAYS=7    # 未用索引需uptime≥7天才可信
```
八类检查：
1. **重复/前缀冗余索引**：列序完全相同=重复；A 的列是 B 列前缀=前缀冗余。
2. **低区分度**：`selectivity=Cardinality/TABLE_ROWS`，<0.1 报（分级 0.9/0.5/0.1/0.01），排除 PRIMARY。
3. **未使用索引**：`count_read=0` 且非 PRIMARY；UNIQUE 标"约束索引"降险；uptime<7天标存疑。
4. **低利用率**：该索引 count_read 占该表总 count_read <5%。
5. **索引空间**：按表统计 INDEX_LENGTH 占比。
6. **单表索引过多**：索引数>8。
7. **表碎片**：`frag_ratio=DATA_FREE/(DATA_LENGTH+INDEX_LENGTH+DATA_FREE)`，碎片≥1MB 报，建议 `OPTIMIZE TABLE`。
8. **自增耗尽风险**：`AUTO_INCREMENT/列类型上限 ≥40%` 告警。
（附带：无主键表、非 InnoDB 表、timestamp 字段、字符集不一致——与我方上线检查 C 系列可打通，去重展示。）

### G5.4 元数据库 / API / 前端
- 表 `index_audit`（任务）+ `index_audit_finding`（明细：db/table/index/问题类型/严重度/指标/建议）。
- Router `/api/v1/index-audit`：`POST /run {connection_id, database?}`、`GET /report/{id}`、`GET /findings/{id}`。
- 前端"索引体检"页：概览（表数/索引数/平均每表索引数）+ 8 类问题分区（可折叠）+ 每条建议。
- 严重度：冗余/未用/低区分度→WARNING，碎片大/自增≥90%→ERROR，其余 INFO（按 `map_severity` 归一）。

### G5.5 验收
| AC | 判据 |
|---|---|
| G5-01 | 造一张有完全重复索引的表，被检出"重复索引"。 |
| G5-02 | 造前缀冗余（idx(a) 与 idx(a,b)），检出"前缀冗余"。 |
| G5-03 | 低区分度索引（如 status 列 cardinality 极低）被检出并给区分度%。 |
| G5-04 | 碎片≥1MB 的表被检出并算出碎片率，建议 OPTIMIZE。 |
| G5-05 | performance_schema 不可用时，"未使用/低利用率"章节明确标"数据不可用"，不误报。 |

---

## G6. 跨实例表结构比对（源自 `table_schema_diff`）

### G6.1 能力目标
比对两个实例（如生产 vs 测试、或两个单元化 SET）的库表结构，输出分级差异报告。

### G6.2 采集
对每侧实例导出结构：库列表→表列表→每表 `SHOW CREATE TABLE` 或分解为
- 列：`information_schema.COLUMNS`（列名/类型/可空/默认/排序）。
- 索引：`information_schema.STATISTICS`（按 SEQ_IN_INDEX 组列，列名统一小写）。
- 触发器：`information_schema.TRIGGERS`。

### G6.3 比对与严重度（照搬原厂）
```
表缺失(一侧有一侧无)            → CRITICAL
索引缺失                        → CRITICAL
列缺失                          → HIGH
列类型不一致                    → MEDIUM
同名索引列不一致                → MEDIUM
多余列/多余索引                 → INFO
```
- 列名大小写不敏感比对；索引"同时按名字和列内容匹配"。
- （原厂含单元化 RZ/GZ 过滤——只比带 RZ 的单元化实例，避免非单元化重复报告；我方可作为可选开关。）

### G6.4 元数据库 / API / 前端
- 表 `schema_diff`（任务：左右实例、库范围）+ `schema_diff_item`（差异：db/table/type/severity/left/right）。
- Router `/api/v1/schema-diff`：`POST /run {left_conn, right_conn, databases}`、`GET /report/{id}`。
- 前端"结构比对"页：匹配摘要 + 差异汇总卡（CRITICAL/HIGH/MEDIUM/INFO→映射 ERROR/WARNING/INFO）+ 按库分组差异 + 严重度过滤。

### G6.5 验收
| AC | 判据 |
|---|---|
| G6-01 | 一侧缺表→CRITICAL(ERROR)。 |
| G6-02 | 同名列类型不一致→MEDIUM(WARNING)，报告显示两侧类型。 |
| G6-03 | 多余索引→INFO，不误升级。 |

---

## G7. 应急诊断一键包（源自 `mysql_emergency_diag`）

### G7.1 能力目标
对指定实例一键采集 6 大类应急快照，定位大事务/锁等待/未提交/连接打满/CPU飙高/死锁。分布式加 `/*sets:allsets*/`。

### G7.2 采集 SQL（业务库只读，全部 SELECT/SHOW）
| 模块 | 关键查询 |
|---|---|
| S1 实例健康 | `SHOW GLOBAL STATUS`(Threads_connected/running、Questions、Uptime)、`SHOW VARIABLES`(max_connections)、连接使用率 |
| S2 连接/会话 | `information_schema.processlist` 按 state/command/time 聚合，活跃会话 TopN |
| S3 大事务/未提交 | `information_schema.innodb_trx`（trx_started、trx_rows_modified、运行时长）、长事务 TopN（复用现有 `long_transaction`） |
| S4 锁等待 | `information_schema.innodb_lock_waits`/`data_lock_waits`、`sys.innodb_lock_waits`、MDL（`performance_schema.metadata_locks`） |
| S5 异常/慢SQL | `processlist` 中 time>阈值且非 Sleep；结合 monitordb 慢SQL |
| S6 InnoDB/死锁 | `SHOW ENGINE INNODB STATUS`（解析 LATEST DETECTED DEADLOCK，复用现有 `deadlock_analyzer`） |

### G7.3 落地形态
- 复用现有 `deadlock_analyzer` / `long_transaction`；新增 `emergency_diag_service` 编排 S1~S6，一次返回结构化快照 + 高亮异常。
- Router `/api/v1/emergency`：`POST /run {connection_id, actions:[status|session|bigtrx|lock|slow|innodb|all], tdsql:bool}`。
- 前端"应急诊断"页：一键全体检 → 6 卡片分区 + 异常红黄标记 + 一键复制处置建议（如 kill 会话语句仅"生成不执行"）。
- 报告落 `emergency_report` 表以便复盘。

### G7.4 验收
| AC | 判据 |
|---|---|
| G7-01 | 造一个长事务，S3 检出并给 trx 运行时长/修改行数。 |
| G7-02 | 造行锁等待，S4 检出阻塞链（who blocks whom）。 |
| G7-03 | 分布式实例加 `/*sets:allsets*/` 能取到各 SET 数据（若该 TDSQL 版本支持）；不支持则回退逐 SET 并提示。 |
| G7-04 | 所有采集均只读，无任何写/kill 被实际执行。 |

---

## G4. 每日巡检 + 多日趋势对比（源自 `daily_inspection` + `compare_reports`）

### G4.1 能力
定时（每日）采集 7 指标落库，产出趋势看板与"昨日对比"。

### G4.2 7 指标（源自 `m_data_cur`，已确认）
CPU 峰值(`cpu_usage_max` peak) / 平均CPU峰值(`cpu_usage` peak) / 全天平均CPU(`cpu_usage` avg) / 内存峰值(`mysql_max_mem_usage` peak) / 慢查询(`slow_query` sum) / 主备延迟峰值(`slave_delay` peak) / 数据盘使用率峰值(`data_dir_usage` peak)；另 Proxy 时耗分布可选。

### G4.3 落地
- 表 `daily_inspection`（date/connection_id/instance/各指标列）。接入现有 `scheduler`（每日定时）。
- `trend_service.get_trend(connection_id, date_from, date_to, metrics[])` 产出折线数据；≥5 天出趋势图。多集群合并=多 connection 汇聚。
- Router `/api/v1/daily-inspect`：`POST /run`、`GET /trend`。前端"趋势看板"。
- 验收：连续多日采集后趋势曲线与逐日 `m_data_cur` 取值一致；昨日对比 diff 正确。

---

## G8~G12（P2，方案级设计，实施前再细化）

| 能力 | 数据源/要点 | 落地形态 |
|---|---|---|
| **G8 SQL调用量分析** | monitordb `proxy_classes_analysis`（已聚合）+ 可选 performance_schema | 在 monitordb 慢SQL之上加"多维统计"视图：SQL类型分布、TOP-N 高频(query_count)/耗时(query_time_sum)/慢(query_time_avg≥阈值)/全表扫描(rows_examined大且低效率)。复用 G1 取数，纯前端+聚合，无需新数据面。 |
| **G9 大表增长趋势+类型分布** | 业务库 + 定时采集表 | 大表治理增：历史采集表 `bigtable_history`(date/db/table/rows/size)，趋势/增长排行；表类型分布(分表/单表/广播表，Proxy命令 `/*proxy*/show ...`)。接现有 scheduler 每日轻采。 |
| **G10 ZK自动发现实例** | ZooKeeper 2118（`/tdsqlzk`）| 连接管理增"从集群发现"：调用等价 `tdsql_inventory` 逻辑列出 host,port,user(tdsqlsys_normal),pass,db 供勾选登记。**需 ZK 可达 + 凭证**，作为开关式可选能力；不可达则维持手工登记。 |
| **G11 网关(Proxy)日志分析** | Proxy 节点 `interf/sql/slow_sql/sys/route/dbfw` 日志文件 | 新"网关日志分析"模块：上传/采集日志→解析→15 章节报告 + interf 深度(SQL耗时细分/去重聚合/EXPLAIN诊断，EXPLAIN 复用 G2 安全逻辑)。**需日志文件可达**，形态偏离线分析。 |
| **G12 汇报/大屏** | 各模块产出 | 汇总看板(集群总览大屏) + 可选 PPT 导出(等价 auto_report 的 P0/P1/P2 问题分级汇总)。 |

> G13（磁盘性能测试 / sshpass 批量执行）：基础设施类脚本，与 Web 平台形态不符，**建议不纳入平台**；若用户坚持保留，作为"运维脚本工具箱"页面外挂下载/说明，不做深度集成。

---

## 附录 A. 新增/改动文件总览（供拆任务）

| 层 | 新增/改动 | 说明 |
|---|---|---|
| 连接器 | `tdsql_connector.py` | G1 已加 monitor_* 方法；G3/G4 复用 `_monitor_execute` 读 `m_data_cur` |
| 服务 | 新增 `slow_enrich_service` `cluster_inspect_service` `index_audit_service` `schema_diff_service` `emergency_diag_service` `trend_service` | 各能力主体 |
| 引擎 | 新增 `severity_map.py`；复用 `slow_analyzer`/`index_advisor`/`deadlock_analyzer`/`long_transaction`/`bigtable_engine` | 严重度映射 + 复用 |
| API | 新增 `cluster_inspect.py` `index_audit.py` `schema_diff.py` `emergency.py` `daily_inspect.py`；`slow_query.py` 增强字段 | 新 router |
| 元数据库 | `database.py` 增：slow_queries 增强列；`cluster_inspection(_issue)`、`index_audit(_finding)`、`schema_diff(_item)`、`emergency_report`、`daily_inspection`、`bigtable_history` 等表（全部幂等迁移） | 建表+迁移 |
| 前端 | 新增"集群巡检/索引体检/结构比对/应急诊断/趋势看板"页；慢SQL明细"诊断"子面板；连接抽屉 monitordb + (可选)ZK发现 | SPA |
| 配置 | monitordb 连接、各阈值常量集中可配 | — |

## 附录 B. 逐能力施工顺序（里程碑内）
每个能力统一流程：**读本文对应章 → (需要则)现场 DESCRIBE/单位校准 → 建表迁移 → 服务/连接器方法 → 纯逻辑单测 → API → 前端 → 真库联调留证 → 全量回归 885/55/0 → 提交 main**。里程碑顺序：M1(G1+G2) → M2(G3) → M3(G4/G5/G6/G7) → M4(G8~G12)。

## 附录 C. 原厂源码对照索引（便于编码时回看原实现）
| 能力 | 原厂文件 | 关键函数/行为 |
|---|---|---|
| G1/G2 慢SQL+增强 | `slow_query_export/slow_sql_analysis.sh`、`slow_sql_enrich.py` | 取数SQL、噪音过滤、指纹归一；`safe_sql_for_explain`/`extract_explain_issues`/`get_stats_update_info`/`calc_scan_efficiency` |
| G3 深度巡检 | `tdsql-deep-inspection/tdsql_inspect.py` | 阈值 `T`、`check_*`、`m_data_cur` 采集、备份/趋势 |
| G4 每日巡检 | `daily_inspection/instance_check_all_in_one.sh`、`compare_reports.py` | 7 指标 SQL、趋势对比 |
| G5 索引审计 | `index_analysis/analyze_index.py` | `analyze_selectivity/unused/low_usage/duplicate/fragmentation/too_many/auto_increment_risk` 及阈值常量 |
| G6 结构比对 | `table_schema_diff/compare_db_structure.py` | `compare_columns/compare_indexes` 与 severity |
| G7 应急诊断 | `mysql_emergency_diag/diag.sh`、`quick_sql.sql` | S1~S6 SQL |
| G8 SQL分析 | `sql_analysis/analyze_sql.py` | `analyze_digest` TOP-N |
| G9 大表 | `count_table_rows/*`、`collect_table_stats/*` | 计数/趋势/表类型 |
| G11 网关日志 | `gateway_log_analysis/*` | 日志解析、interf 深度 |
