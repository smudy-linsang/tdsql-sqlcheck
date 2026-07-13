# 集群级慢SQL数据源 (monitordb) 接入设计说明书评审与可行性分析报告

我已对以下文件进行了深度审查：
- monitordb 接入设计说明书：[集群级慢SQL数据源(monitordb)接入设计说明书.md](file:///c:/TDSQL_SQLCHECK/%E9%9B%86%E7%BE%A4%E7%BB%A7%E6%85%A2SQL%E6%95%B0%E6%8D%AE%E6%BA%90%28monitordb%29%E6%8E%A5%E5%85%A5%E8%AE%BE%E8%AE%A1%E8%AF%B4%E6%98%8E%E4%B9%A6.md)

以下是针对该文档的整体评价、可行性分析以及在实际开发和生产落地中需要修改的细节。

---

## 🌟 整体评价

### 1. 结构与质量：**极高（照图施工级）**
- **校准详实**：v1.1 结合了真实的 DDL 截图与原厂 `slow_query_export` 源码，将耗时单位（秒，系数1）、主键、字段语义（`timestramp` 的特殊拼写）完全校准，排除了所有“待确认”的不确定因素。
- **降维优势明显**：慢SQL的 `monitordb` 接入方案相比原有的 `digest`（performance_schema 逐 SET 扫描）能直接获取执行用户（`user`）、客户端 IP（`host`）和 SET 详细信息，且无需手工配置 `set_list`，解决了漏配/错配痛点，具备极高的业务价值。
- **业务健壮性高**：包含列裁剪、单位探测和噪音过滤等防御性设计，能较好地应对 TDSQL 不同版本的字段差异。

---

## 🔍 可行性评估与关键发现

本接入说明书在技术上**完全可行**，可以直接指导编码。但在细节上，有以下几个**关键地方需要修改或补充**：

### ⚠️ 修改 1：时间窗过滤的 SQL 索引命中与性能隐患（高风险 ⚡）
* **涉及章节**：`§5.3` 中的 SQL 骨架与 `§0.1` 现场校准结论。
* **冲突点分析**：
  - 在 `§0.1` 中提到：“时间窗过滤该用哪列：原厂按 **`timestramp`**（采集时刻）过滤某天的慢SQL，**命中索引 `index_time`**”。
  - 但在 `§5.3` 的 SQL 骨架中，WHERE 条件写的是：
    ```sql
    [AND ts_max >= :time_start]
    [AND ts_min <= :time_end]
    ```
  - `ts_min` 和 `ts_max` 是 `datetime` 类型，**并不是索引 `index_time` 或 `index_ts` 的列**。如果采用这二者进行过滤，Proxy 会在 monitordb 上执行**全表扫描/全索引扫描**，在数据量庞大的生产监控库上会引发严重的性能危机，甚至拖慢 monitordb。
* **修改对策**：
  必须将时间窗过滤改回索引列 `timestramp`，SQL 骨架的 WHERE 应修改为：
  ```sql
  AND timestramp >= :time_start
  AND timestramp <= :time_end
  ```
  这样能确保命中 `index_time`（或 `index_ts`）索引，实现毫秒级响应。

---

### ⚠️ 修改 2：时间参数类型的转换处理（中风险 ⚙️）
* **涉及章节**：`§6.2` 时间列语义。
* **可行性障碍**：
  `timestramp` 列在 DDL 中为 `timestamp` 类型。我们系统传入的 `time_start`/`time_end` 通常是 ISO 字符串（如 `"2026-07-13 00:00:00"`）。
* **整改建议**：
  在拼装 SQL 查询时，应确保占位符参数类型正确。在 Python 侧：
  - 如果 `timestramp` 是标准 `timestamp`/`datetime` 类型，直接传入日期时间字符串即可。
  - 如果某些极少数旧版本中 `timestramp` 存放的是 `bigint` 秒数，必须在 Python 侧使用 `int(datetime.strptime(...).timestamp())` 转换为时间戳后再传入，避免因隐式类型转换导致索引失效。

---

### ℹ️ 建议 3：Python 侧指纹归一化与 SQL 侧 GROUP BY 的分工（优化项 💡）
* **涉及章节**：`§5.5` 指纹归一化。
* **可行性分析**：
  设计中提到“本项目 pymysql 取回后也可在 Python 侧再归一化一次”。
  - 由于 monitordb 已经在 SQL 层面通过监控采集器将 `checksum` 算好，**同一种慢SQL的 `checksum` 是唯一的**。
  - 因此，我们在 SQL 中执行 `GROUP BY db, checksum` 已经完成了精确的分类聚合。
* **整改建议**：
  Python 侧不需要再对 `fingerprint` 进行耗时的“二次聚合分组”操作。Python 端的归一化函数（去空格、去分号等）只需用于**美化/规范化展示** `fingerprint` 文本，这能极大减少 CPU 开销。

---

### ℹ️ 建议 4：`rows_affected` 的落库适配（细节补充 📝）
* **涉及章节**：`§5.4` 与 `§7.1` 的集成点。
* **整改建议**：
  由于 `rows_affected_sum` 和 `rows_affected_max` 是 monitordb 独有且极具价值的字段（可以反映 UPDATE/DELETE 到底影响了多少数据），建议在 M1 里**必须在元数据库 `slow_queries` 表中新增 `rows_affected` 列**并予以持久化，同时在前端慢SQL详情页展示。如果数据源回退至 `digest`，该列存默认值 `0` 即可。

---

## 🛠️ 建议执行的修改计划

如果您同意对上述问题进行修正，我建议在实施 M1 时，将以下 2 条强制写入开发任务：
1. **修改时间过滤**：将慢SQL查询的过滤条件由 `ts_min/ts_max` 改为 `timestramp`，确保命中 `index_time` 索引。
2. **新增影响行落库**：在 `slow_queries` 表中新增 `rows_affected` 字段的元数据定义。
