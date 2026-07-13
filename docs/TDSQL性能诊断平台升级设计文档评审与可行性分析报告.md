# TDSQL 性能诊断平台升级设计文档评审与可行性分析报告

我已对以下两份设计文档进行了深度审查：
- 概要设计：[TDSQL性能诊断平台升级_概要设计文档.md](file:///c:/TDSQL_SQLCHECK/TDSQL%E6%80%A7%E8%83%BD%E8%AF%8A%E6%96%AD%E5%B9%B3%E5%8F%B0%E5%8D%87%E7%BA%A7_%E6%A6%82%E8%A6%81%E8%AE%BE%E8%AE%A1%E6%96%87%E6%A1%A3.md)
- 详细设计：[TDSQL性能诊断平台升级_详细设计说明书.md](file:///c:/TDSQL_SQLCHECK/TDSQL%E6%80%A7%E8%83%BD%E8%AF%8A%E6%96%AD%E5%B9%B3%E5%8F%B0%E5%8D%87%E7%BA%A7_%E8%AF%A6%E7%BB%86%E8%AE%BE%E8%AE%A1%E8%AF%B4%E6%98%8E%E4%B9%A6.md)

以下是针对设计文档的整体评价、可行性分析以及在实际编码和生产落地中需要注意/修改的地方。

---

## 🌟 整体评价

### 1. 结构与质量：**优秀（达标，具备极强的指导性）**
- **设计粒度深**：文档达到了“照图施工”级别。它为每个功能详细列出了数据源、精确 SQL、判定阈值、后端方法命名、API 契约以及元数据库迁移方案。
- **现状分析透彻**：概要设计中对原厂 15 个组件和我方现有代码进行了细致的 Gap 差距分析，并合理划分了优先级（P0/P1/P2）。
- **质量红线清晰**：强调了只读检查、安全 `EXPLAIN` 防注入、严重度口径统一以及 940+ 测试用例不回退的基线，符合企业级和银行级的研发要求。

---

## 🔍 可行性评估与关键发现

各模块的核心设计在技术上**完全可行**，但在生产环境落地时，存在以下几个**中高风险点**需要对详细设计进行补充与修改。

### ⚠️ 风险 1：系统库与视图的访问权限限制（高风险 ⚡）
* **涉及章节**：
  - `G2.2` 统计信息获取（查询 `mysql.innodb_table_stats`）
  - `G2.2` 冗余索引获取（查询 `sys.schema_redundant_indexes`）
* **可行性障碍**：
  在银行等严格受控的生产环境中，平台连接业务库所使用的账号通常为只读账号或限制权限账号，**极大概率没有读取 `mysql` 系统库或 `sys` 库的权限**（会报 `Error 1142: SELECT command denied to user...`）。
* **整改建议**：
  - **优雅降级机制**：在 `slow_enrich_service.py` 中执行这些查询时，必须加上 `try...except` 异常捕获。如果因权限不足（Error 1142/1044）报错，应当允许程序继续运行，将该项诊断结果填为 `"N/A (权限不足)"`，绝对不能让整条慢SQL的扫描主流程因权限问题崩溃。
  - **替代取数源**：对于统计信息时间，如果 `mysql.innodb_table_stats` 不可读，应回退到只读账号必定可读的 `information_schema.TABLES.UPDATE_TIME` 或 `CREATE_TIME`。

---

### ⚠️ 风险 2：多版本 TDSQL/MySQL 的锁等待表兼容性（中风险 ⚙️）
* **涉及章节**：
  - `G7.2` 应急诊断一键包（S4 锁等待采集）
* **可行性障碍**：
  TDSQL 的底层 MySQL 内核有不同版本：
  - 基于 MySQL 5.7 的版本：锁等待信息存放在 `information_schema.innodb_lock_waits`（以及 `innodb_locks`）中。
  - 基于 MySQL 8.0 的版本：上述表已被废弃，改用 `performance_schema.data_lock_waits`（以及 `data_locks`）。
  如果不做版本适配，直接执行硬编码的 SQL，在不同版本的数据库上会发生“表不存在”的报错。
* **整改建议**：
  - 在 `emergency_diag_service` 内部，在查询锁等待前，应先通过 `SELECT VERSION()` 识别当前实例的大版本（5.7 或 8.0）。
  - 或者使用尝试机制：优先查询 8.0 的 `performance_schema.data_lock_waits`，若报错表不存在，则降级查询 5.7 的 `information_schema.innodb_lock_waits`。

---

### ⚠️ 风险 3：DML 转换为 SELECT EXPLAIN 的鲁棒性（低风险 🔒）
* **涉及章节**：
  - `G2.2` 的 `convert_to_select` 转换逻辑
* **潜在隐患**：
  设计中提到“将 UPDATE/DELETE 转换为 SELECT 语句以进行安全 EXPLAIN”。使用简单的正则表达式进行转换非常危险，容易因为复杂的子查询、多表关联 `UPDATE...JOIN` 或复杂的 `WHERE` 子句导致生成的 SELECT 语法错误，从而引发 EXPLAIN 失败。
* **整改建议**：
  - 建议在 `slow_enrich_service.py` 中，**引入 `sqlglot` 来实现 AST（抽象语法树）级别的 SQL 重写**，而不是用正则表达式。
  - 示例方案：
    ```python
    import sqlglot
    from sqlglot import exp

    def convert_to_select(sql_text: str) -> str | None:
        try:
            expression = sqlglot.parse_one(sql_text, read="mysql")
            if isinstance(expression, exp.Update):
                # 提取 update 的 table 和 where 条件，重写为 Select
                # sqlglot 提供了方便的 rewrite/select 构造器
                ...
            elif isinstance(expression, exp.Delete):
                ...
        except Exception:
            return None # 转换失败则放弃增强EXPLAIN，安全第一
    ```

---

### ⚠️ 风险 4：除以零异常（低风险 🧮）
* **涉及章节**：
  - `G5.3` 区分度计算：`selectivity = Cardinality / TABLE_ROWS`
* **可行性障碍**：
  如果是一张新表，或者统计信息尚未收集，`TABLE_ROWS` 的值可能为 `0`，这会导致 Python 代码抛出 `ZeroDivisionError`。
* **整改建议**：
  - 在计算时必须显式防御：
    ```python
    selectivity = (cardinality / table_rows) if table_rows > 0 else 0.0
    ```

---

### ⚠️ 风险 5：分布式分布式路由注解（中风险 📡）
* **涉及章节**：
  - `G2.2` 统计信息查询（`mysql.innodb_table_stats`）
  - `G7.3` 应急诊断一键包
* **技术细节**：
  TDSQL 作为分布式数据库，部分系统表（如 `mysql.innodb_table_stats`）在分布式实例中是分布在各个物理 SET 上的。设计中已经非常敏锐地写到了需要加 `/*sets:allsets*/` 注解。
* **整改建议**：
  - 需特别注意：有些私有云环境的 Proxy 对 `/*sets:allsets*/` 的支持情况不同，或者返回的数据是多行（每个 SET 一行）。
  - 在服务层（如 `get_stats_update_info`）聚合数据时，对于多行返回，需要使用 `min(last_update)` 取最老更新时间，`sum(n_rows)` 求和，这在详细设计中写得很好，应在代码实现中予以固化。

---

## 📝 总结与修改结论

两份文档在**整体设计架构、落地里程碑和技术路线上完全达标**，可以直接作为开发依据。

建议在进入开发前，将上述 **5 个建议（权限降级、版本适配、SQLGlot重写、防除零、分布式聚合）** 作为补充条款更新到详细设计的实现要求中。这能避免编码阶段因权限和数据库版本问题引发的返工。
