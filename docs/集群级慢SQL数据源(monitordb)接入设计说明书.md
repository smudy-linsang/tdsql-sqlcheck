# 集群级慢SQL数据源（monitordb / 15001）接入设计说明书

> 版本：v1.1（设计稿，仅设计不实施）
> 关联需求：TDSQL 原厂性能诊断能力升级 —— 引入集群级慢SQL权威数据源
> 文档性质：**照图施工级详细设计**，任一编码智能体据此即可完成开发
> 适用代号：本文档对应整体升级的「慢SQL数据源」章节，是整体《概要设计文档 + 详细设计说明书》的组成部分之一。
> v1.1 变更：依据用户提供的 monitordb DDL 截图 + 原厂 `slow_query_export` 源码，将 §2 schema、§6 单位/时间列全部从"待现场确认"升级为**已确认权威事实**（见下方 §0.1 校准结论），并将取数 SQL 对齐原厂 `slow_sql_analysis.sh` 的成熟口径。

---

## 0.1 现场校准结论（★已确认，覆盖本文后续所有"待确认"表述）

以下三项此前标注"需现场 DESCRIBE 确认"的事实，现已由 **monitordb 表 DDL 截图 + 原厂 `slow_query_export/slow_sql_analysis.sh` 源码** 双重确认，编码时直接采用，无需再猜：

| 事项 | 权威结论 | 依据 |
|---|---|---|
| 表引擎/字符集 | `proxy_classes_analysis`：InnoDB / utf8mb4；`proxy_global_analysis`：InnoDB / utf8 | DDL 截图 |
| 耗时单位 | `query_time_*` / `lock_time_*` 均为 **`float` 秒**（示例 `example_query_time=2.26/7.65/9.21` 即约2~9秒）→ **单位系数 = 1，无需任何换算** | DDL 截图 + 样本数据 |
| 时间列语义 | `timestramp`（原厂拼写）= `timestamp DEFAULT CURRENT_TIMESTAMP`，是**监控采集器写入该聚合行的时刻**；`ts_min`/`ts_max` = `datetime`，是该 SQL 类的**首次/最后执行时刻** | DDL 截图（备注列） |
| **时间窗过滤该用哪列** | 原厂按 **`timestramp`**（采集时刻）过滤"某天的慢SQL"，命中索引 `index_time`。我方沿用此口径。`ts_min`/`ts_max` 仅用于展示"首次/最后执行时间" | 原厂 `slow_sql_analysis.sh` L332-333 |
| 指纹主键 | `checksum`（`bigint unsigned`）= SQL 指纹校验值；`fingerprint`（`text`）= 参数化 SQL 模板 | DDL 截图 |
| 加权平均口径 | 原厂用 **`SUM(query_time_avg * query_count) / SUM(query_count)`** 重算平均（不是 `SUM(query_time_sum)`）。二者理论等价，**照图施工统一采用原厂 `avg*count` 写法** | 原厂 `slow_sql_analysis.sh` L316 |

> 因此 §6.2/§6.3 的"探测分支"在本环境**已坍缩为确定值**：时间列走 datetime/timestamp 字符串比较、单位系数恒为 1。§6.1 防御式列裁剪仍建议保留（防跨 TDSQL 版本列差异），但本集群已知列齐全。

---

## 0. 一句话背景

TDSQL 集群创建时会**默认起一个端口为 15001 的 monitordb 实例**，其中的库 `tdsqlpcloud_monitor` 由 TDSQL 监控采集器持续写入**全集群已聚合的慢SQL**（含库名、用户名、客户端IP、SET名等维度）。本设计将 monitordb 接入为我们系统慢SQL扫描的**首选数据源**，在**完整保留现有 `performance_schema` 逐SET合并、`processlist` 实时轮询两种能力**的前提下，新增一条能力更强、数据更全、口径更权威的通道。

---

## 1. 为什么要接入 monitordb（能力对比）

### 1.1 现状：我们现在怎么取慢SQL

代码位置 `backend/services/tdsql_connector.py` / `backend/services/scan_service.py`，现有两种 `source`：

| source | 取数方式 | 已知短板 |
|---|---|---|
| `digest` | 逐 SET 用 `/*sets:xxx*/` hint 查 `performance_schema.events_statements_summary_by_digest`，再在应用层按 DIGEST 合并（`_merge_digest_across_sets`） | ① 必须先在实例配置里手工填 `set_list`，漏填/填错就取不全；② performance_schema **不含执行用户、客户端IP**（digest 是脱敏聚合），无法定位"谁在跑这条慢SQL"；③ performance_schema 是内存表，实例重启即清零、digest 表有容量上限会滚动淘汰；④ 无中位数；⑤ 单位是皮秒需换算 |
| `processlist` | 轮询 `information_schema.processlist` 抓正在执行的慢SQL | 只能抓到"扫描那一瞬间正在跑"的SQL，短慢SQL极易漏抓，非统计口径 |

### 1.2 monitordb 数据源的能力增量

`tdsqlpcloud_monitor.proxy_classes_analysis` 是 TDSQL 原厂监控**已经替我们做好聚合**的慢SQL明细表，一次连接（15001）即取全集群，**无需 set_list、无需逐SET路由、无需皮秒换算**。逐项对比：

| 能力项 | performance_schema（现有 digest） | monitordb（本设计新增） |
|---|---|---|
| 覆盖范围 | 单SET（随机路由），需逐SET合并 | **全集群一次取全** |
| 是否需配 set_list | **需要** | **不需要** |
| 执行用户（user） | ❌ 无 | ✅ `user` |
| 客户端IP（host） | ❌ 无 | ✅ `host` |
| SET名 / SET IP | 需应用层拼 | ✅ `set_name` / `set_ip` / `set_port` 原生带 |
| 库名 | ✅ SCHEMA_NAME | ✅ `db` |
| 指纹分组 | DIGEST | ✅ `checksum` / `fingerprint` |
| 示例SQL（带真实字面量） | ❌ 只有参数化 DIGEST_TEXT | ✅ `example_sql`（如 `/*...*/SELECT * FROM t_transaction`） |
| 执行次数 | COUNT_STAR | ✅ `query_count` |
| 耗时 总/平均/最大/最小 | SUM/AVG/MAX（无MIN、无中位数） | ✅ sum/avg/max/min **+ median 中位数** |
| 锁等待 总/平均/最大/最小/中位 | 仅 SUM_LOCK_TIME | ✅ 全维度 + 中位数 |
| 扫描行/返回行 | SUM_ROWS_EXAMINED / SUM_ROWS_SENT | ✅ `rows_examined_sum` / `rows_sent_sum` |
| 影响行（DML） | ❌ 无 | ✅ `rows_affected_sum` / `rows_affected_max` |
| 历史留存 | 内存表，重启清零/滚动淘汰 | ✅ 持久化，`ts_min`/`ts_max` 可按任意历史时间窗查询 |
| 单位 | 皮秒（需 /1e12） | 秒（**需现场确认**，见 §6.3） |

**结论**：monitordb 在"覆盖全集群、可归因到人/IP、含DML影响、有历史留存"四个维度对现有方案是降维优势。因此设计目标定为：**新增 `source="monitordb"` 作为分布式实例慢SQL扫描的首选数据源；`digest`/`processlist` 完整保留作为回退与补充。**

---

## 2. monitordb 数据字典（★需现场 DESCRIBE 校准）

> ⚠️ **给编码智能体的强制约束**：下表 schema 系依据用户提供的截图整理，**列名/类型可能随 TDSQL 版本有差异**（例如 `timestramp` 疑似原厂拼写、时间列可能是 `datetime` 也可能是 `bigint` 时间戳、耗时单位存疑）。**开工第一步必须**对目标 monitordb 实例执行 `DESCRIBE tdsqlpcloud_monitor.proxy_classes_analysis;` 与 `DESCRIBE tdsqlpcloud_monitor.proxy_global_analysis;`，以真实返回为准校准所有列名与类型，并按 §6 做"防御式取数"（缺列不报错、单位现场探测）。不得把本表当成不可变契约硬编码。

### 2.1 库/实例定位

| 项 | 值 |
|---|---|
| 实例端口 | `15001`（monitordb 实例，与业务 Proxy 的 SQL 端口不同） |
| 库名 | `tdsqlpcloud_monitor` |
| 慢SQL明细表 | `proxy_classes_analysis`（按 指纹×SET×时间桶 聚合，逐条慢SQL类） |
| 全局统计表 | `proxy_global_analysis`（按 SET×时间桶 的全局汇总） |
| 示例账号 | 截图中 `user=tdsql_check`（原厂巡检账号；实际连接账号由用户在配置里填） |

### 2.2 `proxy_classes_analysis` 列（慢SQL明细，核心表）

| 列名 | 含义 | 映射到我方 slow_queries |
|---|---|---|
| `id` | 自增主键 | —（不落库） |
| `set_name` | SET 名，如 `set_1782132369_1` | `set_id` |
| `set_ip` | SET 主库IP，如 `10.206.0.4` | 落 `set_id` 备注或新增列（见 §5.3） |
| `set_port` | SET 端口，如 `15005` | 同上 |
| `timestramp` | 采集时间戳（原厂拼写，注意不是 timestamp） | —（用于时间窗过滤，见 §6.2） |
| `master` | 是否主库标识 | —（可选，过滤只取主库） |
| `example_sql` | 带真实字面量的示例SQL（前缀 `/*应用注释*/`） | `sql_text` |
| `example_query_time` | 示例SQL耗时 | —（可选） |
| `example_time` | 示例发生时间 | —（可选） |
| `user` | 执行数据库用户 | `client_user` ★现有digest取不到 |
| `host` | 客户端IP/主机 | `client_host` ★现有digest取不到 |
| `db` | 库名 | `db_name` |
| `checksum` | SQL指纹校验值 | 指纹主键之一 |
| `fingerprint` | 参数化后的SQL模板 | `fingerprint`（等价 DIGEST_TEXT） |
| `ts_min` | 该聚合窗内最早出现时间 | `first_seen` |
| `ts_max` | 该聚合窗内最晚出现时间 | `last_seen` |
| `query_count` | 执行次数 | `exec_count` |
| `query_time_sum` | 总耗时 | `total_time_ms`（×单位系数） |
| `query_time_median` | 耗时中位数 | 新增展示项（见 §5.3） |
| `query_time_avg` | 平均耗时 | `avg_time_ms`（×单位系数） |
| `query_time_min` | 最小耗时 | —（可选） |
| `query_time_max` | 最大耗时 | `max_time_ms`（×单位系数） |
| `lock_time_sum` | 锁等待总时长 | `lock_time_ms`（×单位系数） |
| `lock_time_median/avg/min/max` | 锁等待中位/平均/最小/最大 | —（可选展示） |
| `rows_sent_sum` | 返回行数总和 | `rows_sent` |
| `rows_examined_sum` | 扫描行数总和 | `rows_examined` |
| `rows_affected_sum` | 影响行数总和（DML） | 新增展示项 ★现有digest无 |
| `rows_affected_max` | 单次最大影响行数 | 新增展示项 ★现有digest无 |

**已知索引**（用于写 WHERE 时命中，勿全表扫）：`index_db`、`index_query(checksum,user,host,db)`、`index_set`、`index_setname_time_checksum`、`index_time`、`index_ts`、`primary`。
→ 时间窗过滤走 `index_time`/`index_ts`；按库过滤走 `index_db`；按SET过滤走 `index_set`。

### 2.3 `proxy_global_analysis` 列（全局统计，趋势/概览用）

| 列名 | 含义 |
|---|---|
| `id` / `set_name` / `set_ip` / `set_port` / `timestramp` / `master` | 同上（维度） |
| `unique_query_count` | 唯一SQL类数量（去重后的慢SQL种类数） |
| `query_time_sum/median/avg/min/max` | 该SET该时间窗的整体耗时分布 |
| `lock_time_sum/median/avg/min/max` | 整体锁等待分布 |
| `rows_sent_sum` / `rows_examined_sum` | 整体扫描/返回行 |

用途：慢SQL扫描报告"集群概览"卡片（每个SET的慢SQL种类数、整体耗时水位），以及后续趋势图数据源。**本期最小实现可只接 `proxy_classes_analysis`（明细），`proxy_global_analysis` 作为 §7 可选增强。**

---

## 3. 总体架构与数据源优先级

```
                     慢SQL扫描 run_scan(source=?)
                                │
        ┌───────────────────────┼───────────────────────┐
     source=monitordb      source=digest           source=processlist
     （本设计新增，首选）   （现有，逐SET合并回退）   （现有，实时快照）
        │                      │                        │
  连 15001 / tdsqlpcloud_    逐SET /*sets*/ 查          轮询 information_
  monitor.proxy_classes_    performance_schema         schema.processlist
  analysis（一次取全集群）    再 _merge_digest_...        poll_processlist
        │                      │                        │
        └──────────── 统一产出 raw_queries[] ────────────┘
                                │
                   slow_analyzer 规则分析（severity/root_cause/suggestion）
                                │
                        落库 slow_queries 表
                                │
                       扫描报告 / 看板 / 导出
```

**优先级策略（写进 scan_service 与前端默认值）**：
- 分布式实例（`is_distributed=True`）且已配置 monitordb 连接信息 → **默认 `monitordb`**。
- 未配置 monitordb → 回退现有 `digest`（逐SET合并）。
- 需要抓"此刻正在跑"的现场 → 用户手动选 `processlist`。

**关键收益**：接入 monitordb 后，分布式慢SQL扫描**不再依赖用户手工填 `set_list`**（set_name 由 monitordb 原生返回），这从根本上消除了此前"漏填/填错 set_list 导致数据不全"的整类问题。

---

## 4. 配置模型设计（连接信息扩展）

monitordb 是**独立于业务库端口的另一个实例**（15001）。同一 TDSQL 集群的 monitordb，其网络位置通常与业务 Proxy 同网段、可复用同一账号密码，但端口/账号仍应允许单独填写。采用**"在现有 TDSQL 连接上挂一组 monitor_* 附加字段"**的方式（而非新建独立连接实体），使一个连接=一个集群，配置最省心。

### 4.1 `TDSQLConnectionConfig`（`backend/services/tdsql_connector.py` 顶部 dataclass）

在现有字段（含 `set_list: str = ""`）后新增：

```python
# ── monitordb（集群级慢SQL数据源，端口通常 15001）──
monitor_host: str = ""      # 留空则复用业务连接 host
monitor_port: int = 15001   # monitordb 实例端口，默认 15001
monitor_user: str = ""      # 留空则复用业务连接 username
monitor_password: str = ""  # 留空则复用业务连接 password
monitor_db: str = "tdsqlpcloud_monitor"  # 监控库名，一般固定
```

**语义**：`monitor_host/user/password` 为空时，取数逻辑自动回退用业务连接的 `host/username/password`，仅把端口换成 `monitor_port`。这样绝大多数场景用户**只需勾选"启用集群级慢SQL(monitordb)"**、必要时改端口即可，无需重复填账号。

### 4.2 元数据库表 `tdsql_connections`（`backend/services/database.py`）

在建表 DDL 与迁移里，参照现有 `set_list` 的加法，新增 5 列（均可空/给默认，兼容存量）：

```sql
monitor_host      VARCHAR(128) DEFAULT '',
monitor_port      INT          DEFAULT 15001,
monitor_user      VARCHAR(128) DEFAULT '',
monitor_password  TEXT         DEFAULT '',      -- 与主密码同款加密存储，见 §4.5
monitor_db        VARCHAR(128) DEFAULT 'tdsqlpcloud_monitor',
```

迁移用现有 `_add_column_if_not_exists(conn, "tdsql_connections", ...)` 逐列加，**幂等**（列已存在则跳过）。

### 4.3 `connection_registry.save_connection`（`backend/services/connection_registry.py`）

- `save_connection(...)` 形参新增 `monitor_host=""`, `monitor_port=15001`, `monitor_user=""`, `monitor_password=""`, `monitor_db="tdsqlpcloud_monitor"`，写库时一并 UPSERT（密码字段走与主 `password` 相同的加密函数）。
- `registry.get(...)` 组装 `TDSQLConnectionConfig` 时补：`monitor_host=saved.get("monitor_host",""), monitor_port=saved.get("monitor_port",15001) or 15001, monitor_user=saved.get("monitor_user",""), monitor_password=<解密>(saved.get("monitor_password","")), monitor_db=saved.get("monitor_db","tdsqlpcloud_monitor")`。

### 4.4 API 请求模型（`backend/api/tdsql_manage.py`）

- `TDSQLConnectRequest`（以及"保存/更新连接"用到的模型）新增可选字段：
  ```python
  monitor_host: str = ""
  monitor_port: int = 15001
  monitor_user: str = ""
  monitor_password: str = ""
  monitor_db: str = "tdsqlpcloud_monitor"
  ```
- 保存/更新连接的 handler 把这 5 个字段透传给 `save_connection`。

### 4.5 密码加密

`monitor_password` **必须复用现有 `password` 字段的加密/解密路径**（当前项目里密码是加密存储的，见 connection_registry 与 密钥管理模块），不得明文落库。若用户留空该字段，取数时回退主密码，则库里存空串即可。

---

## 5. 连接器实现设计（照图施工）

### 5.1 新增：建立 monitordb 连接的私有方法

在 `TDSQLConnectionPool`（`backend/services/tdsql_connector.py`）内新增。**复用现有 `pymysql` 连接创建风格**（参照 `_create_connection`），但连的是 monitor 实例：

```python
def _monitor_conn_params(self) -> dict:
    """解析 monitordb 连接参数：monitor_* 为空则回退业务连接同名字段，仅换端口。"""
    c = self.config
    return {
        "host": c.monitor_host or c.host,
        "port": int(c.monitor_port or 15001),
        "user": c.monitor_user or c.username,
        "password": c.monitor_password or c.password,
        "database": c.monitor_db or "tdsqlpcloud_monitor",
        "charset": "utf8mb4",
        "connect_timeout": <与主连接一致>,
        "cursorclass": pymysql.cursors.DictCursor,
    }

def _monitor_execute(self, sql: str, params: tuple = None) -> list[dict]:
    """对 monitordb（15001）执行只读查询，短连接即用即关，不进主连接池。
    失败抛异常，由上层 get_cluster_slow_queries 捕获并给出可读错误。"""
    import pymysql
    conn = pymysql.connect(**self._monitor_conn_params())
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return list(cur.fetchall())
    finally:
        conn.close()
```

> 设计取舍：monitordb 查询频率低（扫描时才用），用**短连接**最简单、最不影响现有连接池；不必纳入 `TDSQLConnectionPool` 的线程连接管理。

### 5.2 新增：连通性/schema 探测方法（供"测试连接"和防御式取数）

```python
def monitor_probe(self) -> dict:
    """探测 monitordb 是否可用 + 返回真实列集合，用于：
       ① 前端'测试monitordb'按钮；② 取数前的防御式列裁剪。
    返回 {"ok": bool, "columns": set[str], "error": str}"""
    try:
        rows = self._monitor_execute(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=%s AND TABLE_NAME='proxy_classes_analysis'",
            (self.config.monitor_db or "tdsqlpcloud_monitor",))
        cols = {r["COLUMN_NAME"] for r in rows}
        return {"ok": bool(cols), "columns": cols,
                "error": "" if cols else "proxy_classes_analysis 不存在或无列"}
    except Exception as e:
        return {"ok": False, "columns": set(), "error": str(e)}
```

### 5.3 核心：`get_cluster_slow_queries`（读 proxy_classes_analysis）

**签名与语义**（与 `get_slow_queries_from_digest` 对齐，便于 scan_service 平行调用）：

```python
def get_cluster_slow_queries(
    self, limit: int = 50, min_time: float = 0.1,
    time_start: str = None, time_end: str = None,
    database: str = None, user: str = None,
) -> list[dict]:
    """从 monitordb（tdsqlpcloud_monitor.proxy_classes_analysis）取全集群 TopN 慢SQL。
    单位换算见 §6.3；防御式列裁剪见 §6.1；时间窗过滤见 §6.2。
    返回结构与 _query_digest_direct 一致（键名对齐 slow_analyzer 期望），
    额外多带 set_name/set_ip/client_user/client_host/median/rows_affected 字段。"""
```

**实现步骤（照图施工）**：

1. 调 `monitor_probe()` 拿真实列集合 `cols`；`ok=False` 时抛 `RuntimeError("monitordb不可用: <error>")`（上层转成对用户友好的报错）。
2. **按聚合口径重算**：`proxy_classes_analysis` 已是"指纹×SET×时间桶"粒度，同一 SQL 指纹在不同 SET / 不同时间桶会有多行。需在 **SQL 层用 `GROUP BY checksum`（或 `fingerprint`）二次聚合**为"每指纹一行"，口径同 `_merge_digest_across_sets`：
   - `SUM(query_count)` 作为 exec_count
   - `SUM(query_time_sum)` 作为 total
   - `SUM(query_time_sum)/SUM(query_count)` **重算平均**（不能对 avg 再求平均）
   - `MAX(query_time_max)` 作为 max
   - `MIN(ts_min)` / `MAX(ts_max)` 作为 first/last seen
   - `SUM(rows_examined_sum)` / `SUM(rows_sent_sum)` / `SUM(rows_affected_sum)` / `MAX(rows_affected_max)`
   - `SUM(lock_time_sum)` 作为 lock
   - `GROUP_CONCAT(DISTINCT set_name)` 汇总命中的 SET 列表（落 `set_id`，≤512 截断，与现有列宽一致）
   - `user`/`host`/`db`/`fingerprint`/`example_sql` 取任一代表值（`MAX()` 或 `ANY_VALUE()`，同一 checksum 下基本一致；`example_sql` 取耗时最大那条更佳，见下）
3. **示例SQL取最慢样本**：若要 example_sql 对应最慢一次，用子查询或 `SUBSTRING_INDEX(GROUP_CONCAT(example_sql ORDER BY query_time_max DESC), ',', 1)`；实现复杂度高时，本期可简单取 `MAX(example_sql)`，在文档标注为已知简化。
4. **min_time 过滤**放在**重算平均之后**（`HAVING SUM(query_time_sum)/SUM(query_count) >= :min_time_in_native_unit`），与 digest 口径一致。注意 `min_time` 入参是秒，需换成 monitordb 原生单位（见 §6.3）。
5. `ORDER BY SUM(query_time_sum) DESC LIMIT :limit`。
6. **列裁剪**：SELECT 里每个列先判 `col in cols`，不存在的列用常量占位（如 `rows_affected_sum` 不存在则选 `0 AS rows_affected_sum`），保证跨版本不因缺列报错（见 §6.1）。
7. 把原生行**映射成与 `_query_digest_direct` 相同的键名**返回（见 §5.4 映射表），使 scan_service 的下游 `slow_analyzer` 与落库逻辑**零改动即可复用**。

**参考SQL骨架**（列名以现场 DESCRIBE 为准，`{unit}` 为单位系数占位）：

```sql
SELECT
  db                                   AS SCHEMA_NAME,
  checksum                             AS DIGEST,
  MAX(fingerprint)                     AS DIGEST_TEXT,
  MAX(example_sql)                     AS example_sql,
  MAX(user)                            AS client_user,
  MAX(host)                            AS client_host,
  GROUP_CONCAT(DISTINCT set_name)      AS set_ids,
  SUM(query_count)                     AS exec_count,
  ROUND(SUM(query_time_sum)  * {unit}, 4)                          AS total_seconds,
  ROUND(SUM(query_time_sum)/NULLIF(SUM(query_count),0) * {unit},4) AS avg_seconds,
  ROUND(MAX(query_time_max)  * {unit}, 4)                          AS max_seconds,
  ROUND(SUM(lock_time_sum)   * {unit}, 4)                          AS lock_time_seconds,
  SUM(rows_examined_sum)               AS rows_examined,
  SUM(rows_sent_sum)                   AS rows_sent,
  SUM(rows_affected_sum)               AS rows_affected,
  MAX(rows_affected_max)               AS rows_affected_max,
  MIN(ts_min)                          AS FIRST_SEEN,
  MAX(ts_max)                          AS LAST_SEEN
FROM tdsqlpcloud_monitor.proxy_classes_analysis
WHERE db NOT IN ('mysql','information_schema','performance_schema','sys','tdsqlpcloud_monitor')
  AND db IS NOT NULL
  [AND ts_max >= :time_start]   -- 时间窗，命中 index_ts/index_time
  [AND ts_min <= :time_end]
  [AND db = :database]          -- 命中 index_db
  [AND user = :user]
GROUP BY db, checksum
HAVING SUM(query_time_sum)/NULLIF(SUM(query_count),0) * {unit} >= :min_time
ORDER BY SUM(query_time_sum) DESC
LIMIT :limit
```

### 5.4 字段映射表（monitordb 行 → scan_service 期望键 → slow_queries 列）

scan_service 现有落库逻辑（`backend/services/scan_service.py`）读的是这些键：`DIGEST_TEXT`、`avg_seconds`、`SCHEMA_NAME`、`set_ids`、`rows_examined`、`rows_sent` 等。monitordb 方法**必须产出同名键**，映射如下：

| monitordb 聚合值 | scan_service 期望键 | 落 slow_queries 列 | 备注 |
|---|---|---|---|
| `db` | `SCHEMA_NAME` | `db_name` | |
| `checksum` | `DIGEST` | —（去重用） | |
| `fingerprint` | `DIGEST_TEXT` | `fingerprint` | slow_analyzer 分析用 |
| `example_sql` | `example_sql` | `sql_text` | ★带真实字面量，比 digest 更利于分析 |
| `user` | `client_user` | `client_user` | ★digest 取不到 |
| `host` | `client_host` | `client_host` | ★digest 取不到 |
| `GROUP_CONCAT(set_name)` | `set_ids` | `set_id`（≤512截断） | |
| `SUM(query_count)` | `exec_count` | `exec_count` | |
| `SUM(query_time_sum)`×系数 | `total_seconds` | `total_time_ms`（×1000） | |
| 重算 avg ×系数 | `avg_seconds` | `avg_time_ms`（×1000） | |
| `MAX(query_time_max)`×系数 | `max_seconds` | `max_time_ms`（×1000） | |
| `SUM(lock_time_sum)`×系数 | `lock_time_seconds` | `lock_time_ms`（×1000） | |
| `SUM(rows_examined_sum)` | `rows_examined` | `rows_examined` | |
| `SUM(rows_sent_sum)` | `rows_sent` | `rows_sent` | |
| `SUM(rows_affected_sum)` | `rows_affected` | 新增列/展示 | ★digest 无 |
| `MIN(ts_min)` | `FIRST_SEEN` | `first_seen` | |
| `MAX(ts_max)` | `LAST_SEEN` | `last_seen` | |

> scan_service 现有代码 `avg_ms = float(raw.get("avg_seconds",0) or 0)*1000` 等换算**原样适用**，因为我们把 monitordb 值也统一成"秒"口径的 `*_seconds` 键。**这是本设计能做到下游零改动的关键。**

### 5.5 原厂成熟的噪音/系统账号过滤（★必须内置，来自 `slow_sql_analysis.sh`）

原厂脚本在 WHERE 中内置了一整套"排除无意义SQL和系统账号"的过滤，直接决定报告质量。**编码时必须原样搬进 `get_cluster_slow_queries` 的 WHERE**（可做成可配置常量 `MONITOR_SLOW_EXCLUDE_USERS` / `MONITOR_SLOW_NOISE_PATTERNS`，给默认值）：

```sql
-- 系统/ETL 账号排除（原厂默认，可在配置里增删）
AND user NOT IN ('dbman', 'incquery', 'hxyunwei', 'edwusr', 'tdsql_check')
-- 噪音语句排除（大小写各一条；本项目可用 UPPER(fingerprint) NOT LIKE 合并）
AND fingerprint NOT LIKE '%commit%'        AND fingerprint NOT LIKE 'select ?%'
AND fingerprint NOT LIKE 'select n%'       AND fingerprint NOT LIKE '%set autocommit%'
AND fingerprint NOT LIKE '%set session%'   AND fingerprint NOT LIKE '%show variables%'
AND fingerprint NOT LIKE 'select sleep%'
AND fingerprint NOT LIKE 'create %'  AND fingerprint NOT LIKE 'drop %'
AND fingerprint NOT LIKE 'alter %'   AND fingerprint NOT LIKE 'truncate %'
AND fingerprint NOT LIKE 'grant %'   AND fingerprint NOT LIKE 'revoke %'
AND fingerprint NOT LIKE 'flush %'   AND fingerprint NOT LIKE 'kill %'
AND fingerprint NOT LIKE 'analyze table%' AND fingerprint NOT LIKE 'explain %'
-- 客户端工具噪音排除（透传注释里带工具指纹）
AND example_sql NOT LIKE '%tdsql-mysql-connector-java%'
AND example_sql NOT LIKE '%dbeaver%'
```

**指纹归一化（去重关键）**：原厂对 `fingerprint` 做 `TRIM + 连续空格合并 + 去末尾分号 + 括号周围空格归一` 后再 `GROUP BY`，否则 `"( select"` 与 `"(select"` 会被当成两条。SQL 层可用嵌套 `REPLACE` 兼容 MySQL 5.7；本项目 pymysql 取回后**也可在 Python 侧再归一化一次**（见 §5.3 二次聚合）。归一化函数逻辑：

```
去首尾空白 → 去末尾 ';' → 连续空白合并为单空格 → '( '→'(' 、' )'→')'
```

### 5.6 数据作用域（★集群级 vs 实例级，务必理解）

monitordb 是**整个 TDSQL 集群共享的一个库**（一个集群一份，里面含该集群下**所有业务实例/所有 SET** 的慢SQL）。而我们系统里的一个"连接"通常对应**一个业务实例**。因此取数时要支持三种作用域，`get_cluster_slow_queries` 增可选入参：

| 作用域 | WHERE 增量 | 何时用 |
|---|---|---|
| 全集群（用户"整个集群慢SQL"的本意，**默认**） | 无额外过滤 | 集群级慢SQL总览 |
| 按库 | `AND db = :database` | 只看某业务库 |
| 按实例/SET | `AND set_port IN (:ports)` 或 `AND set_name IN (:sets)` | 精确到某实例的 SET（原厂 `slow_sql_analysis.sh` 即用 `set_port=<port>` 逐实例取） |

> 原厂按 `set_port` 逐实例出报告；我方因需求是"整个集群"，**默认不加 set 过滤**取全集群，把 `set_name`/`set_ip` 作为维度列展示；同时保留 `database` / `set_ports` 可选过滤参数以便下钻。实例↔SET 的映射关系可从 monitordb `m_data_cur`（f_key='instance_name'）或原厂 ZK 清单获得（见整体《详细设计说明书》巡检章节）。

---

## 6. 三个必须处理的健壮性问题

### 6.1 防御式列裁剪（跨版本缺列不报错）
用 §5.2 `monitor_probe()` 返回的真实列集合，在 Python 侧动态拼 SELECT：某列不在 `cols` 里就用 `0`/`''` 常量占位。**杜绝把截图里的列名当死契约**。

### 6.2 时间列语义（datetime 还是 bigint 时间戳？）
`ts_min`/`ts_max`/`timestramp` 可能是 `datetime`，也可能是 `bigint`（Unix 秒/毫秒）。开工时 `DESCRIBE` 确认类型：
- 若 `datetime` → WHERE 直接与 `'YYYY-MM-DD HH:MM:SS'` 比较（同现有 digest）。
- 若 `bigint` → 入参时间字符串先转 `UNIX_TIMESTAMP()`，并判断秒/毫秒量级。
在方法里做一次类型判定（`information_schema.COLUMNS.DATA_TYPE`）后分支，不要写死。

### 6.3 耗时单位探测（秒/毫秒/微秒？）★最关键
`query_time_*` 的单位**必须现场确认**，错了会让所有耗时数值差 1000/1000000 倍。**推荐的探测法**（写成一次性校准脚本，结论写入配置或代码常量 `MONITOR_TIME_UNIT_FACTOR`）：
1. 挑一条已知的慢SQL（例如用 processlist 现场抓到"约 2 秒"的那条）。
2. 在 monitordb 查同指纹的 `query_time_avg`，看它是 `2` / `2000` / `2000000`。
3. 据此定系数 `{unit}`：秒→`1`、毫秒→`0.001`、微秒→`0.000001`。
4. 也可与 TDSQL 原厂文档/原厂工程师二次确认。
**在拿到确切单位前，代码里用可配置常量而非魔法数**，并在报告页脚标注"耗时单位=X（已校准）"。

---

## 7. scan_service 与 API 集成

### 7.1 `backend/services/scan_service.py`
- `VALID_SOURCES = {"digest", "processlist", "monitordb"}`（加一项）。
- `source == "monitordb"` 分支：
  ```python
  raw_queries = pool.get_cluster_slow_queries(
      limit=limit, min_time=min_time,
      time_start=time_window_start, time_end=time_window_end,
      database=database or None, user=None)
  ```
- `source_labels` 加 `"monitordb": "集群级慢SQL(monitordb)"`。
- 时间窗校验：monitordb 支持历史查询，`time_window_start/end` 仍作为任务元数据；不填则默认查最近 N 小时（例如近 24h），此默认值做成常量。
- 落库映射沿用现有循环（因键名已对齐）。**唯一增量**：若要持久化 `rows_affected`，给 slow_queries 加列 `rows_affected BIGINT DEFAULT 0` 并在 INSERT 补该字段（§5.3 已带出该值）；不加则忽略该值即可，不影响主流程。

### 7.2 前端（`frontend/index.html` + `frontend/static/js/app.js`）
1. **连接抽屉**：在现有"SET列表"输入下方，加一组"集群级慢SQL(monitordb)"配置：
   - 复选框「启用 monitordb 作为慢SQL数据源」
   - 端口输入（默认 15001）、可选的独立账号/密码/库名（留空=复用主连接）
   - 「测试 monitordb」按钮 → 调后端 `monitor_probe`，回显 ok/列数/错误。
2. **新建扫描任务**：`source` 下拉在 `digest`/`processlist` 基础上加「集群级慢SQL(monitordb)」；当实例已启用 monitordb 时，此项设为**默认选中**。
3. **扫描报告**：新增列/字段展示 `执行用户`、`客户端IP`、`影响行数`、`耗时中位数`（这些是 monitordb 独有增量），digest 来源时这些列显示"—"。

### 7.3 tdsql_manage API
- 新增 `POST /api/v1/tdsql/connections/{id}/monitor-probe` → 调 `pool.monitor_probe()`，返回 `{ok, column_count, error}`（**不回列名明细给前端，避免信息泄露**，只回数量与连通性）。

---

## 8. 兼容性与回退（必须遵守）

1. **不删不改任何现有 source**：`digest` 逐SET合并、`processlist` 轮询原样保留，作为 monitordb 不可用时的回退。
2. **存量连接零影响**：新增列全部有默认值；未配置 monitordb 的老连接，扫描行为与现在完全一致。
3. **monitordb 连不上时**：`get_cluster_slow_queries` 抛可读异常，scan_service 捕获后**给出明确报错**"集群级慢SQL数据源(monitordb, 15001)连接失败：<原因>，请检查端口/账号或改用 digest 数据源"，**不要静默回退**到 digest（避免用户以为查的是全集群实则单SET，口径混淆）。是否自动回退由产品决策，默认**不自动回退、显式报错**。
4. **权限**：连接账号需对 `tdsqlpcloud_monitor` 有 SELECT 权限；文档在部署手册补一条授权说明。

---

## 9. 验收标准（AI说做完不算数——须真库/真数据验证）

> 每条都要有**真实证据**（SQL 返回、pytest 输出、报告截图），不接受"我觉得可以"。

| 编号 | 验收项 | 通过判据与证据 |
|---|---|---|
| AC-01 | schema 校准 | 在真实 monitordb 上 `DESCRIBE proxy_classes_analysis` 的输出，与 §2.2 逐列比对，差异已在代码里按现场为准修正。附 DESCRIBE 输出。 |
| AC-02 | 单位已确认 | §6.3 探测法执行记录：某条已知耗时SQL在 monitordb 的原生值 + 推导出的系数 + 报告显示的秒值一致。附对照数据。 |
| AC-03 | 连通探测 | `monitor-probe` 对正确/错误端口分别返回 ok=true / ok=false 且错误可读。 |
| AC-04 | 取数正确性 | `get_cluster_slow_queries` 返回的某指纹 exec_count/total/avg，与直接在 monitordb 手工 `GROUP BY checksum` 聚合的结果**逐值一致**。附两侧SQL与结果。 |
| AC-05 | 归因能力 | 同一条慢SQL，monitordb 源报告能显示 `执行用户`/`客户端IP`/`SET名`，digest 源显示"—"。附两份报告对比。 |
| AC-06 | 无需 set_list | 把实例 `set_list` 清空，用 monitordb 源扫描仍能取到**全部SET**的慢SQL（数量≥逐SET合并口径）。附证据。 |
| AC-07 | 防御式健壮 | 人为在探测列集合里去掉某可选列（如 rows_affected_sum），取数不报错、该值降级为0。附测试。 |
| AC-08 | 回退不破坏 | 未配置 monitordb 的老连接，digest/processlist 扫描行为与升级前一致；全量 pytest **885 passed / 55 skipped / 0 failed** 不回退（新增用例另计）。附 pytest 输出。 |
| AC-09 | 落库口径 | monitordb 源扫描落库的 slow_queries 行，severity 只出现 ERROR/WARNING/INFO（无 CRITICAL），与现有规则体系一致。 |
| AC-10 | 报告正确 | 扫描报告严重度统计口径（ERROR/WARNING/INFO）与看板一致，不出现 CRITICAL 卡片。 |

---

## 10. 实施步骤清单（给编码智能体的施工顺序）

> 全程 **仅在 main 分支**，每步"改完即自测即提交"（项目既定纪律）。DESIGN 阶段本文档不含代码实现。

1. **[探测先行]** 在真实 monitordb 上跑 `DESCRIBE` 两张表 + §6.3 单位探测，产出《monitordb现场校准记录》，据此确定列名/时间列类型/单位系数三项事实。
2. **[配置模型]** `TDSQLConnectionConfig` 加 5 个 monitor_* 字段（§4.1）。
3. **[元数据库]** `database.py` 建表DDL + `_add_column_if_not_exists` 迁移 5 列（§4.2），密码列走加密。
4. **[注册表]** `connection_registry.save_connection`/`get` 透传+加解密 monitor_* （§4.3）。
5. **[API模型]** `tdsql_manage.py` 请求模型加字段 + 保存/更新透传（§4.4）；新增 `monitor-probe` 端点（§7.3）。
6. **[连接器]** 加 `_monitor_conn_params`/`_monitor_execute`/`monitor_probe`/`get_cluster_slow_queries`（§5），含防御式列裁剪、时间列分支、单位系数、二次GROUP BY聚合。
7. **[单测]** 对 `get_cluster_slow_queries` 的**纯聚合/映射逻辑**写单测（可用 mock 行数据，验证重算平均、set 汇总、min_time HAVING、缺列降级），风格同现有 `_merge_digest_across_sets` 单测。
8. **[scan_service]** 加 `monitordb` 到 VALID_SOURCES + 分支 + source_labels + 默认时间窗常量（§7.1）；如需持久化 rows_affected 则加列。
9. **[前端]** 连接抽屉 monitordb 配置块 + 测试按钮 + 扫描源下拉新增项 + 报告新增列（§7.2）。
10. **[真库联调]** 按 §9 AC-01~AC-10 逐条验证并留证。
11. **[全量回归]** 跑全量 pytest，确认 885/55/0 基线不回退。
12. **[提交]** 分步 commit 推 main，报告页脚标注"数据源=monitordb / 单位已校准"。

---

## 11. 本设计未覆盖、留待整体设计文档的部分

本文档只覆盖"慢SQL数据源"这一章。以下 TDSQL 原厂工具能力（深度巡检 tdsql_inspect、SQL分析 analyze_sql、索引分析 analyze_index、每日巡检与报告对比 daily_inspection/compare_reports、表行数统计、表结构差异对比、主键字段发现、应急诊断、磁盘性能测试等）需在拿到原厂工具包源码后逐模块研读，纳入整体《概要设计文档》与《详细设计说明书》。**待原厂 zip 重新上传后补齐。**
