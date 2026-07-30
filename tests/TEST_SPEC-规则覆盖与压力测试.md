# TDSQL-SQLCheck 规则覆盖与SQL治理压力测试说明书

| 项 | 内容 |
|---|---|
| 适用版本 | v1.5.2.0 |
| 测试对象 | 119 条 SQL 审核规则（文件审核 / 在线元数据审核）+ 慢SQL治理扫描模块 |
| 测试环境 | 后端 `http://127.0.0.1:8000`；云上 SIT-分布式实例A（119.45.220.89:15005）、SIT-集中式实例A（119.45.220.89:15002） |
| 目录 | `tests/rule_audit_materials/`（规则覆盖物料）、`tests/pressure_test/`（压力测试） |

本说明书描述三类测试物料的工作原理与使用方法：

1. **文件审核测试物料**（`.sql` + MyBatis `.xml`）——验证 119 条规则能否被「文件审核」与「在线元数据审核」有效触发；
2. **规则覆盖验证 harness**（`verify_rules.py` / `verify_metadata_rules.py`）——自动化断言每条规则按预期触发；
3. **SQL 治理压力测试**（`run_pressure_test.py`）——对两实例施加负载，验证慢SQL扫描任务的抓取与分析是否符合规则预期。

---

## 一、119 条规则的验证路径划分

经实测，119 条规则按「触发所需信息」分为三类，**走不同的验证路径**：

| 类别 | 规则 | 触发条件 | 验证路径 |
|---|---|---|---|
| **A. 文件审核可触发** | 107 条 | 仅需 SQL 文本（静态检查） | `verify_rules.py` 驱动 `RuleChecker` |
| **B. 需真实表元数据** | 7 条：R048/R055/R056/R057/R058/R060/R064 | 需分片键/是否分片表/索引信息 | `verify_metadata_rules.py` 调 `POST /api/v1/tdsql/audit/with-metadata` |
| **C. 已知不可触发** | 5 条：R025/R035/R038/R049/R059 | 解析器/规则实现限制（见下） | 不列入覆盖要求，记录为发现 |

> **为什么「在线元数据审核」（extract-and-audit）与「文件审核」覆盖的规则相同？**
> 二者底层都调用 `audit_service.audit_file_content`，且**都不传 `table_metadata`**——
> 在线元数据审核只是反向拉取 `SHOW CREATE TABLE` 生成 DDL 文本再走文件审核引擎。
> 因此 B 类规则在两条产品路径上都不触发，必须经专用的 `audit/with-metadata` 端点（会实时拉元数据）验证。

### C 类「已知不可触发」规则（实测结论，非主观豁免）

| 规则 | 不可触发原因（实测） |
|---|---|
| R025 禁改分片键 | 依赖 `parsed.alter_actions`，而解析器对 `ALTER TABLE` 恒不填充该字段（实测为空），规则循环从不执行 |
| R035 多表同含义字段类型一致 | 需 `table_metadata['existing_columns']`，而所有产品审核路径均未填充该字段 |
| R038 大表禁自增主键 | 规则检查 `col.raw_type` 含 `auto_increment`，而解析器 `raw_type` 仅为数据类型（如 `BIGINT`），AUTO_INCREMENT 作为列约束不进入 `raw_type` |
| R049 表别名规范 | 规则体在审核分支恒 `return None`（占位，未实现检测） |
| R059 禁分布式事务 | 规则要求 `is_begin` 且 `table_metadata` 非空；而 `BEGIN` 语句无表，`with-metadata` 端点按 SQL 涉及的表拉元数据，`BEGIN` 恒得空元数据 |

> 这 5 条是**测试过程中发现的规则/解析器缺陷**，已在 `verify_rules.py` 的 `KNOWN_DEAD` 集合中登记并注明原因，建议后续版本修复解析器/规则后移除豁免。

---

## 二、文件审核测试物料（`tests/rule_audit_materials/`）

### 2.1 目录结构

```
tests/rule_audit_materials/
├── verify_rules.py              # 文件审核覆盖验证 harness（A 类 107 条）
├── verify_metadata_rules.py     # 元数据依赖规则验证（B 类 7 条，调 API）
├── sql_audit/                   # 纯 SQL 测试文件（按规则类别分组）
│   ├── 01_naming_ddl.sql        # 命名 + DDL：R001-R011,R023-R034,R036,R037,R078,R097,R098,R115-R118,R030,R031
│   ├── 02_dml_perf_sec_txn.sql  # DML/性能/安全/事务：R012-R022,R039-R053,R069-R076,R084,R092,R095,R096,R100,R107,R109,R114
│   ├── 03_index.sql             # 索引：R018,R019,R061-R063,R065-R067
│   ├── 04_distributed_ddl.sql   # 分布式建表：R054,R077
│   └── 05_oracle_compat.sql     # Oracle 兼容：R079-R083,R085-R091,R093,R094,R099,R101-R106,R108,R110-R113,R119
└── mybatis_xml/
    └── CustomerMapper.xml       # MyBatis 动态 SQL：R012,R013,R014,R016,R047,R051,R070,R076 + 动态标签清洗验证
```

### 2.2 物料格式约定

每个「样例块」以注解标注其**期望触发的规则集合**，harness 据此精确断言：

```sql
-- @case: R006_01                 -- 样例编号
-- @rules: R006                   -- 默认期望（两种实例口径通用）
-- @rules.dist: R006,R077         -- 分布式口径专用期望（可选，缺省回退 @rules）
-- @rules.cent: R006              -- 集中式口径专用期望（可选）
-- @scope: distributed            -- 仅在某口径断言（可选：all[默认]/distributed/centralized）
-- @note: 使用 ENUM 类型           -- 说明（不参与审核）
CREATE TABLE t_enum ( ... );
```

语义要点：

- **`@rules` 精确匹配**：harness 断言「实际触发集合 == 期望集合」，多触发/漏触发都判失败。这迫使每条样例必须**干净地**只触发目标规则（或如实标注合理共触发）。
- **分口径期望**：许多 DDL 在分布式口径下会额外触发 R077（分布式建表必须声明分片键），用 `@rules.dist` 标注含 R077 的完整集合，`@rules`（集中式）则不含。
- **`@scope`**：分布式专属规则（如 R077/R021/R100/R111）只在分布式口径断言。
- **注解会被剥离**：harness 送审前剥离所有 `--` 注释行（审核引擎本就会忽略注释），避免注解里的中文/全角括号/关键字误触发 R104/R071 等。

### 2.3 MyBatis XML 物料

`CustomerMapper.xml` 的每个 `<select/insert/update/delete>` 语句块上方用 XML 注释标注 `@case`/`@rules`。harness 复用**审核引擎同款** `_clean_mybatis_sql` 清洗逻辑（剥离 `<where>/<set>/<if>/<foreach>` 动态标签、`#{}`→`?`），保证与生产文件审核行为一致。重点验证：

- `${}` 动态拼接触发 **R076**（SQL 注入风险）；
- 动态标签清洗后仍能正确识别无 WHERE（R051）、全表 DELETE（R013/R014/R047/R070）等；
- `<foreach>` 清洗为 IN 查询不误报。

### 2.4 运行方法

```bash
cd TDSQL-SQLCheck
# 文件审核覆盖验证（无需启动服务，本地驱动 RuleChecker）
python tests/rule_audit_materials/verify_rules.py
# 可选：输出 JSON 明细
python tests/rule_audit_materials/verify_rules.py --json report.json
```

**判定标准**：退出码 0 = A 类 107 条规则全部按预期触发且断言 0 失败。

**开发环境实测结论**：

```
规则总数: 119  文件审核已覆盖: 107  未覆盖: 0
  其中 需元数据验证(走 with-metadata 端点): 7 -> R048,R055,R056,R057,R058,R060,R064
  其中 已知不可触发(豁免): 5 -> R025,R035,R038,R049,R059
断言失败: 0 条
结论: [PASS]
```

---

## 三、元数据依赖规则验证（`verify_metadata_rules.py`）

### 3.1 原理

B 类 7 条规则需真实表元数据。脚本调用专用端点 `POST /api/v1/tdsql/audit/with-metadata`——
该端点按 SQL 涉及的表**实时拉取**分片键/索引元数据并传入引擎，是产品中唯一传 `table_metadata` 的路径。

验证目标为 SIT-分布式实例A 的分片表 `t_customer`（分片键 `cust_id`）。

### 3.2 用例与预期

| 规则 | 测试 SQL（针对 t_customer） | 预期 |
|---|---|---|
| R048 | `INSERT INTO t_customer (cust_name, id_no) VALUES (...)`（不含分片键 cust_id） | 触发 R048 |
| R055 | `SELECT * FROM t_customer`（分片表无 WHERE） | 触发 R055 |
| R056 | `SELECT ... WHERE cust_id=1001 ORDER BY create_time`（ORDER BY 不含分片键） | 触发 R056 |
| R057 | `INSERT INTO t_customer (cust_name, phone) VALUES (...)`（批量不含分片键） | 触发 R057 |
| R058 | `UPDATE t_customer SET ... WHERE cust_id=1001`（分片表 UPDATE 无 LIMIT） | 触发 R058 |
| R060 | `SELECT cust_name FROM t_customer`（分片表无 WHERE） | 触发 R060 |
| R064 | `SELECT cust_name FROM t_customer WHERE cust_id=1001`（少字段查询） | 触发 R064 |

### 3.3 运行方法

```bash
# 前提：后端已启动，且分布式实例已在「实例管理」连接
python tests/rule_audit_materials/verify_metadata_rules.py
# 自定义参数
python tests/rule_audit_materials/verify_metadata_rules.py \
    --base http://127.0.0.1:8000 --user admin --password Admin@1234 --conn 5ea70d74
```

**判定标准**：退出码 0 = 7 条规则全部在分布式实例上正确触发（断言「目标规则出现在触发集合中」，因这些语句会合理共触发其他规则，故不做精确集合匹配）。

**开发环境实测结论**：7/7 全部通过。

---

## 四、SQL 治理压力测试（`tests/pressure_test/`）

### 4.1 目的

对云上两实例施加可控负载，制造特征明确的慢SQL，通过本工具「慢SQL扫描任务」抓取，验证：

1. **管道可用**：扫描任务能抓取并入库慢SQL（`fetched > 0`）；
2. **分析正确**：高扫描行数的全表扫描类慢SQL被分析器识别为「全表扫描/缺失索引/索引使用不充分」等问题；
3. **两实例均可扫描**：分布式（逐 SET 合并 digest）与集中式（直查 Proxy）两条分支都正常。

### 4.2 数据源选择（实测结论）

| 数据源 | DIST(15005) | CENT(15002) | 结论 |
|---|---|---|---|
| `digest`（performance_schema） | 可用（451 行） | 可用（118 行） | **采用** |
| `monitordb`（tdsqlpcloud_monitor） | 表不存在 | 库不存在 | 两实例均不可用 |

`long_query_time = 1s`。压测采用 **digest 源**。

### 4.3 工作流程

```
[1/4] 建表灌数      建 pt_slow_noindex（无二级索引）/ pt_slow_indexed（uid 有索引），各灌 N 行
                    （分布式实例建表自动声明 SHARDKEY，符合 R077）
[2/4] 制造慢查询    执行 4 类特征 SQL：
                    - ORDER BY RAND() 全表排序（重点负载，真实慢 + filesort + 全表扫描）
                    - 无索引全表扫描 + 函数过滤
                    - 自交叉连接（笛卡尔积，高 rows_examined）
                    - 走索引等值查询（对照组）
                    另执行 SLEEP(1.5) 制造确定性超阈值慢SQL
[3/4] 触发扫描      POST /api/v1/tdsql/slow-queries/fetch（source=digest, min_time=0.0）
[4/4] 验证          拉取任务记录，分层验证（见 4.4）
```

> **关键设计点**：
> - digest 会**剥离 SQL 注释**，故不能用注释标记定位，改用**表名**（`pt_slow_*`）作为特征；
> - 共享实例上高频监控查询会占据 digest Top-N（按 `SUM_TIMER_WAIT` 排序），故制造**真慢**查询（ORDER BY RAND 全表排序）累积 `SUM_TIMER_WAIT` 以提升排名；
> - 压测表用统一前缀 `pt_`，结束默认自动清理（`--keep` 可保留）。

### 4.4 分层验证逻辑

| 层 | 验证项 | 判定 |
|---|---|---|
| (a) 管道 | `fetched > 0` 且任务有入库记录 | 硬指标 |
| (b) 分析器正确性 | `rows_examined > 10000` 的记录被分析器给出有效问题判定（全表扫描/索引使用不充分/缺失索引等） | 硬指标 |
| (c) 特征SQL | 压测 `pt_slow_*` SQL 是否进入 Top-N（进入则验证其分析；共享实例未进属预期，作软性检查） | 软指标 |

### 4.5 运行方法

```bash
cd TDSQL-SQLCheck/tests/pressure_test
python run_pressure_test.py                      # 两实例全量（默认 rows=3000, repeat=3）
python run_pressure_test.py --inst DIST          # 仅分布式
python run_pressure_test.py --inst CENT --rows 5000 --repeat 3
python run_pressure_test.py --keep               # 保留压测表不清理
```

**判定标准**：退出码 0 = 两实例 (a)(b) 硬指标全部通过。

**开发环境实测结论**（rows=5000, repeat=3）：

```
DIST: [PASS]
  [PASS] 扫描抓取到 100 条慢SQL摘要（fetched>0）
  [PASS] 任务入库 100 条慢SQL记录
  [PASS] 高扫描行数慢SQL（examined=38830000）被分析器识别为 problem_type=全表扫描/severity=ERROR
  [PASS] 压测特征SQL（pt_slow_*）进入扫描 Top-N
CENT: [PASS]
  [PASS] 扫描抓取到 100 条慢SQL摘要（fetched>0）
  [PASS] 任务入库 100 条慢SQL记录
  [PASS] 高扫描行数慢SQL（examined=7377152）被分析器识别为 problem_type=索引使用不充分/severity=WARNING
  [PASS] ORDER BY RAND 全表排序慢SQL被捕获：severity=ERROR, problem_type=全表扫描, examined=90900
总结论: [PASS] 全部实例扫描结果符合规则预期
```

---

## 五、一键回归（建议顺序）

```bash
cd TDSQL-SQLCheck
# 1. 文件审核 107 条规则覆盖（本地，无需服务）
python tests/rule_audit_materials/verify_rules.py
# 2. 元数据依赖 7 条规则（需后端 + 分布式实例连接）
python tests/rule_audit_materials/verify_metadata_rules.py
# 3. SQL 治理压力测试（需后端 + 两实例连接）
python tests/pressure_test/run_pressure_test.py
# 4. 工程全量单测回归
python -m pytest tests -q
```

---

## 六、已知限制与发现汇总

1. **5 条规则当前不可触发**（R025/R035/R038/R049/R059）——属规则/解析器实现缺陷，详见第一章 C 类表，建议后续修复。
2. **monitordb 数据源在两实例不可用**——压测采用 digest 源；如需验证 monitordb 源，需具备 `tdsqlpcloud_monitor` 库的环境。
3. **共享实例 digest Top-N 被高频监控查询占据**——压测特征 SQL 可能未进 Top-N，故验证以「分析器对高扫描行数记录的正确判定」为硬指标，特征 SQL 为软指标。
4. **digest 剥离 SQL 注释**——慢SQL定位不能依赖注释标记，需用表名/指纹特征。
