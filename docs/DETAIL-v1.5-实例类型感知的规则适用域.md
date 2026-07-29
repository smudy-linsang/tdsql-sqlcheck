# TDSQL-SQLCheck v1.5 详细设计说明书
## 实例类型感知的规则适用域

| 项 | 内容 |
|---|---|
| 版本 | v1.5.0.0 |
| 基线 | v1.4.0.1（commit `6106a9a`） |
| 文档目标 | **照图施工**：开发者按本文逐节实施即可完成全部改造，无需二次设计决策 |
| 关联文档 | `ARCHITECTURE-v1.5-*.md` · `DB-v1.5-*.md` · `API-v1.5-*.md` |

> **施工须知**
> 1. 所有 SQL 一律用 `?` 占位符。`_MySQLCompatCursor.execute()` 会自动转 `%s`，**手写 `%s` 会出错**。
> 2. 涉及配置生效时延的一切文案，写"**最长 N 秒/分钟生效**"，**严禁写"即时生效"**（生产 `--workers 2`，无跨进程即时一致性）。
> 3. 凭据一律走环境变量，不得硬编码。
> 4. §3 的 119 条判定表是**本次改造的正确性基准**，§7.2 的一致性测试会锁定它，**擅自增删须同步改测试并说明理由**。

---

## 1. 施工总览

### 1.1 改造清单（按依赖顺序）

| 阶段 | # | 文件 | 动作 | 依赖 |
|---|---|---|---|---|
| **一、模型** | 1.1 | `backend/models/__init__.py` | 新增 3 个枚举 | — |
| | 1.2 | `backend/engine/rules/base.py` | `BaseRule` 新增 `instance_scope` | 1.1 |
| | 1.3 | `rules/{distributed,ddl,dml,oracle_compat}.py` | 27 条标注 + 4 条文本改写（§3.5） | 1.2 |
| **二、事实** | 2.1 | `backend/schema/v4/040_instance_type_scope.sql` | 新建迁移 | — |
| | 2.2 | `backend/services/database.py` | `_ensure_columns` 双保险 | 2.1 |
| | 2.3 | `backend/services/tdsql_connector.py` | 新增 `probe_instance_type()` | — |
| | 2.4 | `backend/services/instance_type_service.py` | **新建**解析器 | 1.1 / 2.2 / 2.3 |
| **三、引擎** | 3.1 | `backend/engine/checker.py` | 过滤收口 | 1.2 / 1.1 |
| | 3.2 | `backend/services/audit_service.py` | 贯通 + 硬编码分片键修复 | 3.1 / 2.4 |
| **四、通道** | 4.1 | `backend/api/sql_audit.py` | 4 个端点 | 3.2 |
| | 4.2 | `backend/api/tdsql_manage.py` | 2 个端点 + 探测端点 | 3.1 / 2.4 |
| | 4.3 | `backend/api/gitlab_hook.py` | 2 个端点 | 3.1 |
| | 4.4 | `backend/engine/schema_inspector.py` | C07 分型 | 1.1 |
| | 4.5 | `backend/api/inspection.py` | 传参 | 4.4 / 2.4 |
| | 4.6 | `backend/cli.py` | `--instance-type` | 3.1 |
| **五、留痕** | 5.1 | `backend/services/audit_service.py` | `_save_audit_history` 落列 | 2.1 |
| | 5.2 | `backend/services/scan_snapshot_service.py` | 快照落列 + 对比校验 | 2.1 |
| **六、接口** | 6.1 | `backend/api/rules.py` | 暴露 `instance_scope` | 3.1 |
| | 6.2 | `backend/api/system_config.py` | **新建**全局默认口径 | 2.4 |
| | 6.3 | `backend/services/auth_service.py` | RBAC 登记 | 6.2 |
| | 6.4 | `backend/services/ruleset_service.py` | `effective_counts` | 3.1 |
| **七、前端** | 7.1 | `frontend/index.html` | 5 处 | 六 |
| | 7.2 | `frontend/static/js/app.js` | 5 处 | 7.1 |
| **八、版本** | 8.1 | `backend/config.py` / `index.html` | → `1.5.0.0` | 全部 |

### 1.2 关键不变式（施工中必须始终成立）

| # | 不变式 | 违反后果 |
|---|---|---|
| **INV-1** | 适用域过滤**只在 `RuleChecker.get_enabled_rules()` 一处发生** | 多处过滤 → 口径不一致，`skipped_rules_count` 与实际不符 |
| **INV-2** | 适用域**只做减法**，任何配置/参数都不能反向启用不适用的规则 | 可靠性保证被绕过，G1 失效 |
| **INV-3** | `instance_type` 传入引擎时**必定是确定值**，不存在 `unknown` | 引擎需要处理三态 → 无论怎么处理都是错的（详见 `ARCHITECTURE` §5.3） |
| **INV-4** | 分布式实例的违规结果与 v1.4.0.1 **逐条一致** | 回归 |
| **INV-5** | 探测/解析的任何异常**都不得中断审核主流程** | 一次网络抖动就让审核不可用 |

---

## 2. 模型层

### 2.1 新增枚举 — `backend/models/__init__.py`

在既有 `RuleCategory` / `Severity` 枚举定义之后追加：

```python
class InstanceType(str, Enum):
    """TDSQL 实例类型（客观事实，非配置项）"""
    DISTRIBUTED = "distributed"   # 分布式实例（含 Proxy + 多 SET）
    CENTRALIZED = "centralized"   # 集中式实例（单机/主备，无分片）


class InstanceScope(str, Enum):
    """规则的实例类型适用域

    与 RuleCategory 是正交维度：category 表达"属于规范的哪一章"，
    instance_scope 表达"在哪种实例上物理有意义"。二者不可互相替代——
    例如 R111（窗口函数）category=oracle_compat 但仅分布式适用。
    """
    ALL         = "all"           # 通用（默认值，保守取向）
    DISTRIBUTED = "distributed"   # 仅分布式实例适用
    CENTRALIZED = "centralized"   # 仅集中式实例适用（当前 0 条，为规范演进预留）


class TypeSource(str, Enum):
    """实例类型结论的来源，用于表达该结论的可信度"""
    PROBED   = "probed"     # 连库探测得出（最高可信）
    DECLARED = "declared"   # 取自 tdsql_connections.is_distributed（人工声明）
    REQUEST  = "request"    # 调用方在请求中显式声明（B类通道）
    DEFAULT  = "default"    # 回落 system_config.default_instance_type
```

> **`InstanceScope.CENTRALIZED` 当前无任何规则使用**，这是刻意保留。对 119 条规则做全量关键词扫描（`集中式`/`单机实例`/`noshard 实例`/`非分布式`）后确认，现行 TDSQL 规范中**不存在仅集中式适用的规则**。保留取值使模型能表达未来的规范演进，成本为零；删掉则下次要动引擎。

### 2.2 `BaseRule` 新增属性 — `backend/engine/rules/base.py`

在 `fix_suggestion` 之后新增一行（**位置固定在类属性块末尾，便于 review 时一眼看到**）：

```python
from backend.models import RuleCategory, Severity, Violation, InstanceScope


class BaseRule(ABC):
    """审核规则基类（V1.0；V1.5 新增 instance_scope）"""

    rule_id: str = ""
    category: RuleCategory = RuleCategory.DML
    severity: Severity = Severity.ERROR
    description: str = ""
    enabled: bool = True
    spec_source: str = ""
    fix_suggestion: str = ""

    # V1.5：实例类型适用域。默认 ALL 是保守取向——
    # 漏标只会退化成 V1.4 的行为（可能误报，可见可纠），
    # 错标成 DISTRIBUTED 则导致集中式实例静默漏报（不可见，危险）。
    instance_scope: InstanceScope = InstanceScope.ALL
```

**不改 `check()` 签名。** 规则内部不需要知道实例类型——它要么被调用（说明适用），要么根本不被调用。这是方案 D 相对方案 C 的核心优势，也是 INV-1 的保证。

### 2.3 27 条规则标注

**施工方式**：在每个类的 `fix_suggestion` 之后加一行 `instance_scope = InstanceScope.DISTRIBUTED`，并在同类文件顶部 import 引入 `InstanceScope`。

| # | 规则 | 文件:类定义行 | 类名 | 判定证据（原文摘录） |
|---|---|---|---|---|
| 1 | R020 | `distributed.py:26` | `R020ShardKeyInWhere` | "**分布式表**的SELECT/UPDATE/DELETE语句应在WHERE条件中包含**分片键**字段" |
| 2 | R021 | `distributed.py:82` | `R021ShardKeyUpdate` | "禁止对**分片键**(shardkey)字段进行UPDATE操作" |
| 3 | R022 | `distributed.py:138` | `R022GlobalDeleteWithoutShardKey` | "**分布式表**禁止不带**分片键**的全局DELETE/UPDATE，防止跨所有**SET**执行" |
| 4 | R023 | `ddl.py:238` | `R023NoCreateTableSelect` | "禁止使用CREATE TABLE ... SELECT语句，TDSQL**分布式**不支持" |
| 5 | R024 | `ddl.py:256` | `R024NoTemporaryTable` | "禁止使用CREATE TEMPORARY TABLE，**分布式实例**不支持" |
| 6 | R025 | `ddl.py:274` | `R025NoAlterShardKey` | "禁止通过ALTER TABLE修改**分片键**字段" |
| 7 | R043 | `dml.py:255` | `R043NoMultiTableUpdate` | `spec_source`="DML规范**【分布式】**"；告警文案="**分布式**环境下可能导致**跨SET**操作" |
| 8 | R048 | `dml.py:342` | `R048InsertMustIncludeShardKey` | "**分布式实例**执行INSERT/REPLACE时，字段列表必须包含**分片键**" |
| 9 | R053 | `distributed.py:192` | `R053NoCrossShardJoin` | "**分布式表**JOIN时必须在**分片键**上关联，避免跨**SET**广播JOIN" |
| 10 | R054 | `distributed.py:228` | `R054ShardKeyMustBePrimaryKey` | "**分片键**必须包含在主键及所有唯一索引中" |
| 11 | R055 | `distributed.py:310` | `R055NoGlobalIndexOnly` | "**分布式表**不建议仅依赖全局索引，应优先使用本地索引+**分片键**路由" |
| 12 | R056 | `distributed.py:333` | `R056SuggestShardKeyInOrderBy` | "**分布式表**ORDER BY建议包含**分片键**，避免跨**SET**排序" |
| 13 | R057 | `distributed.py:360` | `R057NoBulkInsertWithoutShardKey` | "批量INSERT/REPLACE必须包含**分片键**字段，否则无法路由到正确**SET**" |
| 14 | R058 | `distributed.py:385` | `R058BatchUpdateLimit` | "**分布式表**批量UPDATE/DELETE建议加LIMIT限制单次影响行数" |
| 15 | R059 | `distributed.py:419` | `R059NoDistributedTransaction` | "避免跨**SET**分布式事务，单事务应只操作同一**分片**数据" |
| 16 | R060 | `distributed.py:437` | `R060ExplainShardKeyCheck` | "建议对**分布式表**查询执行EXPLAIN查看是否命中单**SET**" |
| 17 | **R077** | `distributed.py:464` | `R077CreateTableMustHaveShardKey` | "**TDSQL分布式实例**建表必须声明**分片键**(SHARDKEY)或**广播表**标记" ← **用户报告的现场** |
| 18 | R092 | `oracle_compat.py:378` | `R092WithAsCte` | "**分布式实例**不支持WITH AS(CTE)；**集中式8.0**递归场景可评估 WITH RECURSIVE" |
| 19 | **R097** | `oracle_compat.py:~470` | `R097DefaultValueExpr` | "DEFAULT 值不支持类型转换/函数表达式（**Proxy** 报 ERROR 1064）"。**负责人拍板：TDSQL 底层 MySQL 均为 8.0，而 8.0.13+ 原生支持表达式默认值 → 该限制纯粹来自 Proxy，仅分布式适用** |
| 20 | R100 | `oracle_compat.py:558` | `R100DeleteTableAlias` | "**分布式实例**DELETE语句不支持对被删表设置别名" |
| 21 | R111 | `oracle_compat.py:783` | `R111WindowFunction` | "**分布式实例**不支持窗口函数（row_number()/rank() 等 OVER()）" ← **危害最大：集中式 MySQL 8.0 完全支持** |
| 22 | R112 | `oracle_compat.py:800` | `R112CursorUsage` | "TDSQL**分布式**不支持游标（DECLARE…CURSOR/FETCH）" |
| 23 | **R113** | `oracle_compat.py:~818` | `R113DropPartitionGap` | "DROP PARTITION 与**路由元数据**更新存在毫秒级间隙"。**负责人拍板：路由元数据确系 Proxy 概念，仅分布式适用** |
| 24 | R115 | `oracle_compat.py:851` | `R115PrimaryKeyLength` | "**分布式实例** update/delete…limit 依赖 **proxy** 内嵌 myisam 临时表" |
| 25 | R116 | `oracle_compat.py:885` | `R116ShardKeySingleColumn` | "**分片键**只支持一个字段，不支持多字段联合**分片键**" |
| 26 | R117 | `oracle_compat.py:907` | `R117ShardKeyType` | "**shardkey** 字段类型必须是 int/bigint/smallint/char/varchar" |
| 27 | R118 | `oracle_compat.py:932` | `R118ShardKeyNotNull` | "**shardkey** 字段的值不能为 NULL" |

> R097 / R113 的行号为约值，施工时以 `rule_id = "R097"` / `"R113"` 定位为准。

**其余 92 条一律不动 `instance_scope`**，继承默认值 `InstanceScope.ALL`。
但其中 **R038 与 R114 需改写规则文本**，见 §3.5。

---

## 3. 119 条规则完整判定表（正确性基准）

### 3.1 判定原则（必须严格遵守）

| # | 原则 |
|---|---|
| **J1** | **默认 ALL。** 只有规则文本（`__doc__`/`description`/`spec_source`/`fix_suggestion`/告警文案）**明确限定分布式**，才判 `DISTRIBUTED` |
| **J2** | **边界一律归 ALL。** 判断依据是"错的方向哪个更危险"：多跑一条不适用规则 = 误报，看得见、可纠正；少跑一条适用规则 = **漏报，看不见、放行风险**。因此模棱两可时**必须偏向 ALL** |
| **J3** | **`category` 不作为判定依据。** 它是规范章节归类，与适用性正交（见 §3.4 反例） |
| **J4** | **存疑项须由负责人裁定，不得自行拍板。** 原 4 条存疑规则已于 2026-07-29 全部裁定，见 §3.5 |

### 3.2 判定结果汇总

| 适用域 | 条数 | 说明 |
|---|---|---|
| `ALL`（通用） | **92** | 在两种实例上都有意义 |
| `DISTRIBUTED`（仅分布式） | **27** | 见 §2.3 明细，逐条附证据 |
| `CENTRALIZED`（仅集中式） | **0** | 现行规范中不存在此类规则 |
| **合计** | **119** | |

**因此：分布式实例跑 119 条（与 v1.4 完全一致，INV-4 天然成立），集中式实例跑 92 条。**

### 3.3 逐条判定表

> 图例：`D` = DISTRIBUTED（仅分布式） · `A` = ALL（通用） · `✎` = 归 A 但须改写规则文本（见 §3.5）

| 规则 | category | 适用域 | 规则要点 |
|---|---|---|---|
| R001 | naming | A | 库表名字符限制 |
| R002 | naming | A | 表名禁保留字 |
| R003 | ddl | A | 建表须有主键 |
| R004 | ddl | A | 引擎须 InnoDB |
| R005 | ddl | A | 字符集须 utf8mb4 |
| R006 | ddl | A | 禁 ENUM/SET |
| R007 | ddl | A | 禁 TIMESTAMP |
| R008 | ddl | A | 禁外键 |
| R009 | ddl | A | 金额禁 FLOAT/DOUBLE |
| R010 | ddl | A | VARCHAR ≤ 2000 |
| R011 | ddl | A | 活跃表禁 TEXT/BLOB |
| R012 | dml | A | 禁 SELECT * |
| R013 | dml | A | UPDATE/DELETE 须带 WHERE |
| R014 | dml | A | 禁无 WHERE 的 UPDATE/DELETE |
| R015 | dml | A | 子查询 ≤ 3 层 |
| R016 | dml | A | WHERE 禁函数/全模糊/OR |
| R017 | dml | A | 禁 ORDER BY RAND() |
| R018 | index | A | 单表索引 ≤ 5 |
| R019 | index | A | 禁冗余索引 |
| **R020** | distributed | **D** | 分片键须在 WHERE |
| **R021** | distributed | **D** | 禁 UPDATE 分片键 |
| **R022** | distributed | **D** | 禁无分片键全局 DELETE/UPDATE |
| **R023** | ddl | **D** | 禁 CREATE TABLE…SELECT |
| **R024** | ddl | **D** | 禁 TEMPORARY TABLE |
| **R025** | ddl | **D** | 禁 ALTER 分片键 |
| R026 | ddl | A | 禁缩短字段长度 |
| R027 | ddl | A | 禁 DROP DATABASE |
| R028 | ddl | A | 建表须表级 COMMENT |
| R029 | ddl | A | 字段须 COMMENT |
| R030 | ddl | A | 禁视图/存储过程/触发器 |
| R031 | ddl | A | 禁自定义函数 |
| R032 | ddl | A | 禁临时表做复杂逻辑 |
| R033 | naming | A | 表名用单数 |
| R034 | naming | A | 备份表命名规范 |
| R035 | ddl | A | 同义字段类型一致 |
| R036 | ddl | A | 建议含 create/update_time |
| R037 | ddl | A | 建议逻辑删除 |
| R038 | ddl | A ✎ | 大表禁自增主键（**已拍板：集中式同样成立** → 归 A，但须改写规则文本，见 §3.5） |
| R039 | security | A | 禁 INTO OUTFILE |
| R040 | dml | A | 禁 DELAYED/LOW_PRIORITY |
| R041 | dml | A | INSERT 须显式列名 |
| R042 | security | A | 禁 LOAD DATA INFILE |
| **R043** | dml | **D** | 禁多表联表 UPDATE/DELETE |
| R044 | performance | A | 禁 INDEX HINT |
| R045 | security | A | 禁 HANDLER |
| R046 | security | A | 禁 FLUSH/LOCK TABLES |
| R047 | performance | A | 全表删用 TRUNCATE |
| **R048** | distributed | **D** | INSERT 须含分片键 |
| R049 | naming | A | 多表关联须别名 |
| R050 | performance | A | IN 列表 ≤ 200 |
| R051 | performance | A | SELECT 建议带 WHERE |
| R052 | performance | A | 禁隐式类型转换 |
| **R053** | distributed | **D** | JOIN 须在分片键上 |
| **R054** | distributed | **D** | 分片键须在主键/唯一索引 |
| **R055** | distributed | **D** | 不宜仅依赖全局索引 |
| **R056** | distributed | **D** | ORDER BY 建议含分片键 |
| **R057** | distributed | **D** | 批量 INSERT 须含分片键 |
| **R058** | distributed | **D** | 批量 UPDATE/DELETE 加 LIMIT |
| **R059** | distributed | **D** | 避免跨 SET 分布式事务 |
| **R060** | distributed | **D** | 建议 EXPLAIN 查单 SET |
| R061 | index | A | 索引命名规范 |
| R062 | index | A | 复合索引最左前缀 |
| R063 | index | A | 低区分度字段不单独建索引 |
| R064 | index | A | 建议覆盖索引 |
| R065 | index | A | 复合索引字段 ≤ 5 |
| R066 | index | A | TEXT/BLOB/JSON 禁建索引 |
| R067 | index | A | 长 VARCHAR 用前缀索引 |
| R068 | index | A | JOIN 字段建索引 |
| R069 | transaction | A | 避免长事务 |
| R070 | transaction | A | 单事务行数 ≤ 10000 |
| R071 | transaction | A | BEGIN 须 COMMIT/ROLLBACK |
| R072 | transaction | A | 慎用 SELECT…FOR UPDATE |
| R073 | security | A | ALTER/DROP 须确认备份 |
| R074 | security | A | 禁 GRANT/REVOKE |
| R075 | security | A | TRUNCATE 须确认 |
| R076 | security | A | SQL 注入风险 |
| **R077** | distributed | **D** | **建表须声明分片键/广播表** ← 现场 |
| R078 | oracle_compat | A | 禁 Oracle 专有类型 |
| R079 | oracle_compat | A | 禁 ROWNUM |
| R080 | oracle_compat | A | 禁 NVL |
| R081 | oracle_compat | A | 禁 DECODE |
| R082 | oracle_compat | A | 禁 TO_CHAR |
| R083 | oracle_compat | A | 禁 TO_NUMBER |
| R084 | oracle_compat | A | `||` 语义差异 |
| R085 | oracle_compat | A | 禁 TO_DATE |
| R086 | oracle_compat | A | 禁 TRUNC |
| R087 | oracle_compat | A | LTRIM/RTRIM 差异 |
| R088 | oracle_compat | A | 禁 ADD_MONTHS |
| R089 | oracle_compat | A | SUBSTR 起始位差异 |
| R090 | oracle_compat | A | SYSDATE 用法 |
| R091 | oracle_compat | A | 禁 MERGE INTO（**TDSQL 两种实例均不支持**，仅改写建议不同） |
| **R092** | oracle_compat | **D** | 禁 WITH AS(CTE)（集中式 8.0 支持） |
| R093 | oracle_compat | A | LENGTH 字节/字符差异 |
| R094 | oracle_compat | A | 禁 LISTAGG |
| R095 | oracle_compat | A | 禁 MINUS |
| R096 | oracle_compat | A | 禁 FULL JOIN |
| **R097** | oracle_compat | **D** | DEFAULT 值禁函数表达式（**已拍板：底层 MySQL 均为 8.0，8.0.13+ 原生支持表达式默认值 → 限制纯来自 Proxy**） |
| R098 | oracle_compat | A | HASH 分区须整型 |
| R099 | oracle_compat | A | 派生表须别名 |
| **R100** | oracle_compat | **D** | DELETE 禁表别名 |
| R101 | oracle_compat | A | 别名禁保留字 |
| R102 | oracle_compat | A | LIKE…ESCAPE 差异 |
| R103 | oracle_compat | A | 比较运算符禁空格 |
| R104 | oracle_compat | A | 函数名与括号禁空格 |
| R105 | oracle_compat | A | 禁 (+) 外连接 |
| R106 | oracle_compat | A | 禁 CONNECT BY（**两种实例均不支持**） |
| R107 | oracle_compat | A | INSERT…SELECT 目标表限制 |
| R108 | oracle_compat | A | sequence 批量获取限制 |
| R109 | oracle_compat | A | UPDATE CASE WHEN 顺序 |
| R110 | oracle_compat | A | 禁 USERENV() |
| **R111** | oracle_compat | **D** | 禁窗口函数 ← **危害最大** |
| **R112** | oracle_compat | **D** | 禁游标 |
| **R113** | oracle_compat | **D** | DROP PARTITION 路由间隙（**已拍板：路由元数据确系 Proxy 概念**） |
| R114 | oracle_compat | A ✎ | 深分页大偏移（**已拍板：集中式同样成立** → 归 A，但须改写规则文本，见 §3.5） |
| **R115** | oracle_compat | **D** | update/delete…limit 主键长度限制 |
| **R116** | oracle_compat | **D** | 分片键单字段 |
| **R117** | oracle_compat | **D** | 分片键类型限制 |
| **R118** | oracle_compat | **D** | 分片键 NOT NULL |
| R119 | oracle_compat | A | 日期算术差异 |

### 3.4 关键反例：为什么 `category` 不能用作过滤依据

若采用"跳过 `category == distributed`"这一最直觉的做法，将**漏掉 13 条**：

| 漏掉的规则 | 现 category | 后果 |
|---|---|---|
| R023 / R024 | `ddl` | 集中式仍误报"CREATE TABLE…SELECT / TEMPORARY TABLE 不支持" |
| R043 | `dml` | 集中式仍误报"禁止多表联表 UPDATE"（MySQL 完全支持） |
| R092 | `oracle_compat` | 集中式 MySQL 8.0 的合法 CTE 被判 ERROR |
| R097 | `oracle_compat` | 集中式 MySQL 8.0.13+ 的合法表达式默认值被判 ERROR |
| **R111** | `oracle_compat` | **集中式 MySQL 8.0 的合法窗口函数被判 ERROR** |
| R100 / R112 / R113 / R115 / R116 / R117 / R118 | `oracle_compat` | 各类分片键 / proxy 路由相关误报 |

**R111 最能说明问题**：开发在集中式实例上写了性能良好、语法合法的 `ROW_NUMBER() OVER(...)`，被系统判成 ERROR 并卡在门禁上，按提示"改写为分组+嵌套查询"，结果是**把好代码改坏**。这就是本条设计决策的分量。

### 3.5 4 条存疑规则的裁定与文本改写（负责人 2026-07-29 拍板）

原设计列出 4 条存疑规则待 DBA 复核，现已全部裁定：

| 规则 | 裁定 | 依据（负责人原话要点） | 施工动作 |
|---|---|---|---|
| **R097** | → **DISTRIBUTED** | TDSQL 底层 MySQL 均为 8.0；MySQL **8.0.13+ 原生支持表达式默认值**，故该限制纯粹来自 Proxy | 加 `instance_scope = DISTRIBUTED` + 改写文本（下） |
| **R113** | → **DISTRIBUTED** | "路由元数据"确系 Proxy 概念 | 加 `instance_scope = DISTRIBUTED` + 改写文本（下） |
| **R038** | 维持 **ALL** | 大表禁自增主键在集中式同样成立 | **不改 `instance_scope`**，但改写文本去除分布式框定（下） |
| **R114** | 维持 **ALL** | 深分页大偏移在集中式同样成立 | **不改 `instance_scope`**，但改写文本去除分布式框定（下） |

> **为什么 R038/R114 判 ALL 却仍要改文本**：它们的现有文案把问题**框定为分布式特有**（R038 的 `spec_source` 写"分布式规范"；R114 的 description 与告警文案通篇讲"分布式实例代价高"）。集中式实例上这两条照常触发，使用者读到的却是一段与自己实例无关的解释——**结论对、理由错**，与巡检 C07 是同一类问题（§6.3）。判定与文案必须同时自洽。

#### 逐条改写规格（施工时按此替换，逐字对照）

**① R038 `R038NoAutoIncrementForLargeTable`（`ddl.py:548`）** — 维持 ALL

| 字段 | 现值 | 改为 |
|---|---|---|
| `description` | `"预期数据量超千万的表不建议使用AUTO_INCREMENT主键"` | `"预期数据量超千万的表不建议使用AUTO_INCREMENT主键：自增锁在高并发写入下形成瓶颈，且分库分表/数据迁移/多源合并时主键易冲突"` |
| `spec_source` | `"TDSQL数据库开发规范 - 分布式规范"` | `"TDSQL数据库开发规范 - 表设计规范"` |
| `fix_suggestion` | `"大表建议使用业务主键或分布式ID生成器(如雪花算法)"` | `"大表建议使用业务主键或全局唯一ID生成器（雪花算法等）；分布式实例还需保证该主键包含分片键"` |
| 告警 `suggestion`（`check()` 内） | `"大表建议使用业务主键或分布式ID生成器"` | `"大表建议使用业务主键或全局唯一ID生成器（雪花算法等）"` |

`check()` 逻辑与告警 message **完全不动**。

**② R114 `R114DeepPagination`（`oracle_compat.py:834`）** — 维持 ALL

| 字段 | 现值 | 改为 |
|---|---|---|
| `description` | `"LIMIT大偏移分页在分布式实例代价高（proxy聚合各分片），请用索引有序性/键集翻页/条件初筛优化"` | `"LIMIT大偏移分页需扫描并丢弃前N行，代价随偏移量线性增长；分布式实例还需proxy跨分片聚合，代价更高。请用索引有序性/键集翻页/条件初筛优化"` |
| 告警 message（`check()` 内） | `f"LIMIT偏移量{...}超过{...}，分布式实例深分页代价高"` | `f"LIMIT偏移量{parsed.limit_offset}超过{self.DEEP_PAGE_OFFSET}，深分页需扫描并丢弃前{parsed.limit_offset}行，代价高"` |

`spec_source` / `fix_suggestion` / `DEEP_PAGE_OFFSET` / 判定逻辑**均不动**。

**③ R097 `R097DefaultValueFunction`（`oracle_compat.py:472`）** — 改判 DISTRIBUTED

| 字段 | 现值 | 改为 |
|---|---|---|
| `instance_scope` | （无） | `InstanceScope.DISTRIBUTED` |
| `description` | `"TDSQL建表字段DEFAULT值不支持类型转换/函数表达式（Proxy报ERROR 1064），仅CURRENT_TIMESTAMP例外"` | `"分布式实例建表字段DEFAULT值不支持类型转换/函数表达式（Proxy报ERROR 1064），仅CURRENT_TIMESTAMP例外；集中式实例（MySQL 8.0.13+）原生支持表达式默认值，不受此限"` |

`spec_source` / `fix_suggestion` / 判定逻辑**均不动**。

**④ R113 `R113DropPartitionRisk`（`oracle_compat.py:817`）** — 改判 DISTRIBUTED

| 字段 | 现值 | 改为 |
|---|---|---|
| `instance_scope` | （无） | `InstanceScope.DISTRIBUTED` |
| `description` | `"高并发下DROP PARTITION与路由元数据更新存在毫秒级间隙，小概率报分区不存在；请逐表执行drop+analyze并配置重试"` | `"分布式实例高并发下DROP PARTITION与proxy路由元数据更新存在毫秒级间隙，小概率报分区不存在；请逐表执行drop+analyze并配置重试"` |
| 告警 message（`check()` 内） | `"DROP PARTITION在高并发下可能与路由元数据更新存在间隙，建议逐表执行并配置重试"` | `"DROP PARTITION在高并发下可能与proxy路由元数据更新存在间隙，建议逐表执行并配置重试"` |

`spec_source` / `fix_suggestion` / 判定逻辑**均不动**。

> **重要**：以上 4 条只改**元数据字符串**，`check()` 的判定逻辑一律不动。因此**不会引入任何行为回归**——分布式实例上四条规则的触发条件与触发结果完全不变（R097/R113 在分布式上照常触发，只是描述文字更准确）。

---

## 4. 事实层

### 4.1 探测方法 — `backend/services/tdsql_connector.py`

> ## ⚠️ 勘误（2026-07-29）：本节方案已作废，**不得据此施工**
>
> 本节设计的两个探针经真实环境实测（TDSQL `8.0.33-v24-txsql-22.4.1`）**全部证伪**：
>
> 1. **`/*proxy*/show status`** —— `/*proxy*/` 只是一个 SQL 注释。直连后端 TXSQL 执行该语句返回完整的 458 行标准 MySQL 状态变量，即**任何 MySQL 兼容端点都返回非空**。本节"集中式实例会因无法识别该 hint 而报语法错误"的前提**不成立**，判据恒为真。
> 2. **`information_schema.TDSQL_SHARDING_RULES`** —— 该视图在本版本上**根本不存在**（`ERROR 1109`），判据恒为假。
>
> 净效果：`probe_instance_type()` 是一个**常量函数**，对任何可连实例恒返回 `distributed`，并因"探测优先于声明"覆盖了使用者正确填写的实例类型，**导致 v1.5 的核心目标 G1 完全未达成**。
>
> 更根本的结论（同物理机对照实验）：**TDSQL 后端数据节点上，集中式实例与分布式实例的分片完全同构**，分片拓扑只存在于 Proxy 路由层与管控面（ZK/赤兔）。任何在数据节点 SQL 层区分二者的探针，原理上都不可能成立。
>
> **替代方案见 `docs/DESIGN-v1.5.1-实例类型判定重构.md`**：改用 ZK 管控面权威源，SQL 探测在取得经实测确认的判据前一律返回"无结论"。
>
> 以下原文保留，仅作事故记录与设计复盘依据。


在 `discover_sets()` 之后新增：

```python
    def probe_instance_type(self) -> tuple[Optional[str], dict]:
        """探测实例类型。返回 (类型 或 None, 探针明细)。

        两个探针，任一命中"分布式"即判分布式；两个都明确指向"无分片能力"
        才判集中式；全部异常则返回 None（无结论，由上层退回人工声明）。

        重要：绝不抛异常。探测失败是正常业务分支（网络抖动、权限不足），
        不是服务端错误——INV-5 要求任何探测异常都不能中断审核主流程。
        """
        detail = {"proxy_show_status": {}, "sharding_rules_table": {}}
        votes_distributed = False
        conclusive = False

        # 探针1：/*proxy*/show status —— 仅 TDSQL Proxy 可执行，
        # 集中式实例（直连 MySQL）会因无法识别该 hint 而报语法错误。
        try:
            rows = self._execute("/*proxy*/show status")
            detail["proxy_show_status"] = {"ok": True, "rows": len(rows or [])}
            if rows:
                votes_distributed = True
            conclusive = True
        except Exception as e:
            detail["proxy_show_status"] = {"ok": False, "reason": str(e)[:200]}

        # 探针2：information_schema.TDSQL_SHARDING_RULES —— 仅分布式实例存在该视图。
        # 注意：表存在但无数据，仍然说明这是分布式实例（只是还没建分片表）。
        try:
            rows = self._execute(
                "SELECT COUNT(*) AS c FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA='information_schema' "
                "AND TABLE_NAME='TDSQL_SHARDING_RULES'")
            exists = bool(rows) and int(
                (rows[0].get("c") if isinstance(rows[0], dict) else rows[0][0]) or 0) > 0
            detail["sharding_rules_table"] = {"ok": True, "exists": exists}
            if exists:
                votes_distributed = True
            conclusive = True
        except Exception as e:
            detail["sharding_rules_table"] = {"ok": False, "reason": str(e)[:200]}

        if not conclusive:
            # 两个探针都异常 —— 很可能是连不上库，不能据此判定集中式
            return None, detail
        return ("distributed" if votes_distributed else "centralized"), detail
```

**设计要点（必须理解，否则容易改错）**：

| 要点 | 说明 |
|---|---|
| **非对称投票** | 任一探针命中即判分布式；两个都"明确无分片能力"才判集中式。因为"探到分片能力"是**阳性证据**（可信），"没探到"可能只是权限不足（不可信） |
| **`conclusive` 标志** | 两个探针**全部异常**（通常是连不上库）时返回 `None` 而非 `centralized`。若返回 `centralized`，一次网络故障就会让分布式实例被判成集中式 → 27 条规则静默失效。**这是本函数最关键的一行** |
| **探针2 查 TABLES 而非直接查视图** | 直接 `SELECT FROM TDSQL_SHARDING_RULES` 在集中式上会报表不存在（异常），无法区分"表不存在"与"没权限"。查 `information_schema.TABLES` 在两种实例上都能正常返回，语义清晰 |
| **表存在即算分布式** | 即使 `TDSQL_SHARDING_RULES` 里一行数据都没有（新实例还没建分片表），视图存在本身就说明这是分布式实例 |
| **不抛异常** | 全部 `try/except` 包裹，满足 INV-5 |

### 4.2 解析器 — `backend/services/instance_type_service.py`（新建）

```python
"""实例类型解析服务（V1.5）

职责：把"这次扫描针对什么类型的实例"这个问题，收敛为一个确定的答案。

设计要点：对外永远返回确定的 InstanceType，不存在 unknown 态。
不确定性由 TypeSource 表达（"这个结论是探来的还是猜的"），
而不是由类型本身表达——否则引擎就要处理三态，而三态无论怎么处理都是错的：
跑全部=沿用误报，只跑通用=静默漏报。
"""
import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

from backend.models import InstanceType, TypeSource

logger = logging.getLogger("tdsql.instance_type")

_PROBE_CACHE_TTL = 300.0          # 秒。多 worker 下语义为"最长 5 分钟生效"
_DEFAULT_CACHE_TTL = 300.0
_cache: dict = {}                  # {connection_id: (at, type, source)}
_cache_lock = threading.Lock()
_default_cache = {"at": 0.0, "value": None}
_default_lock = threading.Lock()


@dataclass
class InstanceContext:
    """一次扫描的实例类型上下文，随调用链向下传递"""
    instance_type: InstanceType
    source: TypeSource
    conflict: bool = False                 # 探测与人工声明冲突
    declared: Optional[InstanceType] = None
    detected: Optional[InstanceType] = None


class InstanceTypeService:

    # ── 全局默认 ──────────────────────────────────────────

    def get_default_instance_type(self) -> InstanceType:
        """读 system_config.default_instance_type，带 300s 进程内缓存。

        任何异常一律回落 DISTRIBUTED —— 兜底方向必须偏向"跑全部规则"，
        宁可多报不可漏报（见 ARCHITECTURE §5.4）。
        """
        now = time.time()
        with _default_lock:
            if now - _default_cache["at"] < _DEFAULT_CACHE_TTL \
                    and _default_cache["value"] is not None:
                return _default_cache["value"]
        value = InstanceType.DISTRIBUTED
        try:
            from backend.services.database import _get_connection, ensure_db
            ensure_db()
            conn = _get_connection()
            try:
                row = conn.execute(
                    "SELECT config_value FROM system_config WHERE config_key = ?",
                    ("default_instance_type",)).fetchone()
                if row:
                    raw = (row["config_value"] if isinstance(row, dict) else row[0]) or ""
                    if raw == InstanceType.CENTRALIZED.value:
                        value = InstanceType.CENTRALIZED
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"读取全局默认实例类型失败(按分布式兜底): {e}")
        with _default_lock:
            _default_cache["at"] = now
            _default_cache["value"] = value
        return value

    def set_default_instance_type(self, value: str) -> None:
        from backend.services.database import _get_connection, ensure_db
        if value not in (InstanceType.DISTRIBUTED.value, InstanceType.CENTRALIZED.value):
            raise ValueError("default_instance_type 仅支持 distributed 或 centralized")
        ensure_db()
        conn = _get_connection()
        try:
            conn.execute(
                "REPLACE INTO system_config(config_key, config_value) VALUES(?, ?)",
                ("default_instance_type", value))
            conn.commit()
        finally:
            conn.close()
        with _default_lock:
            _default_cache["at"] = 0.0      # 本进程立即失效；其他 worker 最长 5 分钟

    # ── 核心解析 ──────────────────────────────────────────

    def resolve(self, connection_id: str = "",
                requested: Optional[str] = None) -> InstanceContext:
        """解析一次扫描的实例类型上下文。

        优先级：
          A类（有 connection_id）：探测 > 人工声明        —— 客观事实，忽略 requested
          B类（无 connection_id）：requested > 全局默认    —— 由调用方声明

        INV-2：A 类下 requested 被有意忽略。若允许调用方指定，
        只要传 instance_type=distributed，集中式实例就又会跑出 R077，
        可靠性保证即被绕过。
        """
        if connection_id:
            try:
                return self._resolve_by_connection(connection_id)
            except Exception as e:
                logger.warning(f"实例类型解析失败(回落全局默认): {connection_id}: {e}")
                return InstanceContext(self.get_default_instance_type(), TypeSource.DEFAULT)

        if requested in (InstanceType.DISTRIBUTED.value, InstanceType.CENTRALIZED.value):
            return InstanceContext(InstanceType(requested), TypeSource.REQUEST)
        return InstanceContext(self.get_default_instance_type(), TypeSource.DEFAULT)

    def _resolve_by_connection(self, connection_id: str) -> InstanceContext:
        now = time.time()
        with _cache_lock:
            hit = _cache.get(connection_id)
            if hit and now - hit[0] < _PROBE_CACHE_TTL:
                return hit[2]

        from backend.services.connection_registry import registry
        saved = registry.get_saved(connection_id) or {}

        declared = (InstanceType.DISTRIBUTED
                    if int(saved.get("is_distributed", 1) or 0) == 1
                    else InstanceType.CENTRALIZED)

        detected = None
        raw = saved.get("detected_instance_type")
        if raw in (InstanceType.DISTRIBUTED.value, InstanceType.CENTRALIZED.value):
            detected = InstanceType(raw)
        else:
            detected = self._probe_and_persist(connection_id)

        if detected is not None:
            ctx = InstanceContext(detected, TypeSource.PROBED,
                                  conflict=(detected != declared),
                                  declared=declared, detected=detected)
            if ctx.conflict:
                logger.warning(
                    f"实例 {connection_id} 类型冲突：声明={declared.value}，"
                    f"探测={detected.value}。审核按探测结果执行。")
        else:
            ctx = InstanceContext(declared, TypeSource.DECLARED, declared=declared)

        with _cache_lock:
            _cache[connection_id] = (now, connection_id, ctx)
        return ctx

    def _probe_and_persist(self, connection_id: str) -> Optional[InstanceType]:
        """执行探测并落库。探测失败返回 None，绝不抛异常（INV-5）。"""
        from datetime import datetime
        from backend.services.connection_registry import registry
        from backend.services.database import _get_connection

        result, detail, err = None, {}, ""
        try:
            pool = registry.get(connection_id)
            result, detail = pool.probe_instance_type()
            if result is None:
                err = str(detail)[:500]
        except Exception as e:
            err = str(e)[:500]
            logger.warning(f"实例 {connection_id} 类型探测失败: {e}")

        try:
            conn = _get_connection()
            try:
                if result:
                    conn.execute(
                        "UPDATE tdsql_connections SET detected_instance_type = ?, "
                        "instance_type_detected_at = ?, instance_type_probe_error = '' "
                        "WHERE id = ?",
                        (result, datetime.now().isoformat(), connection_id))
                else:
                    conn.execute(
                        "UPDATE tdsql_connections SET instance_type_probe_error = ? "
                        "WHERE id = ?", (err, connection_id))
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"探测结果落库失败: {e}")

        return InstanceType(result) if result else None

    def invalidate(self, connection_id: str = "") -> None:
        """实例配置变更后清缓存。本进程立即生效，其他 worker 最长 5 分钟。"""
        with _cache_lock:
            if connection_id:
                _cache.pop(connection_id, None)
            else:
                _cache.clear()


instance_type_service = InstanceTypeService()
```

**缓存语义提示语（前端/文档统一口径）**：

> 实例类型探测结果缓存 5 分钟。修改实例配置或手动重新探测后，**最长 5 分钟**在全部服务进程生效。

---

## 5. 引擎层

### 5.1 过滤收口 — `backend/engine/checker.py`

#### 5.1.1 `get_enabled_rules()` 改造（INV-1 的唯一实现点）

```python
    def get_enabled_rules(self, rule_overrides: Optional[dict] = None,
                          instance_type: Optional[str] = None) -> list[BaseRule]:
        """获取本次实际生效的规则。

        两层过滤，串联，方向不对称：
          1) 适用域过滤（V1.5，客观）：规则在该类型实例上物理上是否有意义
          2) 规则集过滤（V1.4，主观）：管理员是否愿意查这条

        INV-2：适用域只做减法。规则集可以关掉一条适用的规则，
        但绝不能打开一条不适用的规则——后者不是尺度松紧问题，
        而是"这个检查在该实例上没有意义"。

        Args:
            rule_overrides: {rule_id: {"enabled": bool, "severity_override": str|None}}
            instance_type: "distributed" | "centralized"；None 表示不做适用域过滤
                           （仅用于 get_rules_info 等纯展示场景）
        """
        result = []
        for r in self.rules:
            # 1) 适用域（客观事实）
            if instance_type is not None and not self._scope_match(r, instance_type):
                continue
            # 2) 规则集（主观尺度）
            override = rule_overrides.get(r.rule_id) if rule_overrides else None
            enabled = override["enabled"] if override else r.enabled
            if enabled:
                result.append(r)
        return result

    @staticmethod
    def _scope_match(rule: BaseRule, instance_type: str) -> bool:
        """唯一判定式：适用域为 ALL，或与实例类型相等"""
        scope = getattr(rule, "instance_scope", None)
        scope = getattr(scope, "value", scope) or "all"
        return scope == "all" or scope == instance_type

    def count_skipped_by_scope(self, instance_type: Optional[str]) -> int:
        """统计因适用域不匹配而跳过的规则数，供报告横幅使用"""
        if instance_type is None:
            return 0
        return sum(1 for r in self.rules if not self._scope_match(r, instance_type))
```

> **`instance_type=None` 保留不过滤语义**，是为了让 `get_rules_info()` / `get_rules_by_category()` 这类**纯展示**接口继续返回全部 119 条。审核路径必须传值——由 §5.2 保证。

#### 5.1.2 `audit_sql()` / `audit_file()` 增加形参

```python
    def audit_sql(self, sql: str, file_path: str = "", line_number: Optional[int] = None,
                  table_metadata: Optional[dict] = None,
                  rule_overrides: Optional[dict] = None,
                  instance_type: Optional[str] = None) -> AuditResult:
        ...
        for rule in self.get_enabled_rules(rule_overrides, instance_type):
            ...   # 循环体完全不变

    def audit_file(self, content: str, file_path: str = "",
                   rule_overrides: Optional[dict] = None,
                   instance_type: Optional[str] = None) -> list[AuditResult]:
        ...
        # 两处 self.audit_sql(...) 调用透传 instance_type=instance_type
```

**改动量极小**：`get_enabled_rules` 多传一个参数，循环体一行不动。这是选择方案 D 的直接收益。

### 5.2 审核服务贯通 — `backend/services/audit_service.py`

#### 5.2.1 新增解析辅助

```python
    def _resolve_instance(self, connection_id: str = "",
                          requested: Optional[str] = None):
        """解析实例类型上下文。任何异常回落全局默认，绝不中断审核（INV-5）。"""
        try:
            from backend.services.instance_type_service import instance_type_service
            return instance_type_service.resolve(connection_id, requested)
        except Exception as e:
            logger.warning(f"实例类型解析异常(按分布式兜底): {e}")
            from backend.models import InstanceType, TypeSource
            from backend.services.instance_type_service import InstanceContext
            return InstanceContext(InstanceType.DISTRIBUTED, TypeSource.DEFAULT)
```

> 兜底为 `DISTRIBUTED` 而非集中式：解析失败时跑全部规则 = 退化为 v1.4 行为（可能误报，可见），而不是静默漏报。

#### 5.2.2 `audit_single_sql()` 改造

```python
    def audit_single_sql(self, sql: str, created_by: str = "",
                         project_id: str = "",
                         evaluate_gate: bool = False,
                         connection_id: str = "",
                         instance_type: Optional[str] = None
                         ) -> tuple[AuditResult, Optional[GateResult], "InstanceContext"]:
        ...
        rule_set_id, overrides = self._resolve_scale()
        ictx = self._resolve_instance(connection_id, instance_type)
        it = ictx.instance_type.value

        if len(statements) <= 1:
            result = self.checker.audit_sql(sql, rule_overrides=overrides,
                                            instance_type=it)
            # ↓↓↓ V1.5 重点修复：原先无条件执行、且分片键硬编码 ↓↓↓
            self._apply_shard_key_check(result, sql, ictx, table_metadata=None)
        else:
            for idx, stmt in enumerate(statements, 1):
                res = self.checker.audit_sql(stmt, rule_overrides=overrides,
                                             instance_type=it)
                ...
```

**返回值由二元组改为三元组**——调用方需要 `InstanceContext` 才能在响应里回带口径字段。全部调用点见 §6。

#### 5.2.3 硬编码分片键检查的修复（本次第二重要的修复）

**原代码问题**（`audit_service.py:132-149`）：

```python
findings = auditor.check_shard_key_presence(expr, ["order_id", "user_id"])
```

两个独立缺陷，**必须同时修，缺一不可**：

| # | 缺陷 | 后果 |
|---|---|---|
| ① | 不看实例类型，无条件执行 | 集中式实例必然误报 `DIST_001`/`DIST_002` |
| ② | 分片键**硬编码**为 `order_id`/`user_id` | 拿**虚构的分片键**去审核真实 SQL。任何一条 WHERE 里没有这两个字段名的 DML 都会被判 `DIST_002` WARNING。**即使在分布式实例上，这个结论也几乎总是错的** |

> **只修 ① 是不够的**：加了实例类型闸门后，分布式实例上的假分片键误报依然存在。这个位点必须一次性修干净。

**替换实现**：

```python
    def _apply_shard_key_check(self, result, sql: str, ictx,
                               table_metadata: Optional[dict] = None):
        """深度分布式检查（V1.5 重写）

        两处修复：
          1) 仅分布式实例执行 —— 集中式无分片概念，这些结论没有意义；
          2) 分片键取自真实表元数据，取不到就整段跳过 ——
             原实现硬编码 ["order_id","user_id"]，等于拿虚构的分片键
             审核真实 SQL，即使在分布式实例上结论也几乎总是错的。
        """
        from backend.models import InstanceType
        if ictx.instance_type != InstanceType.DISTRIBUTED:
            return

        shard_keys = []
        for meta in (table_metadata or {}).values():
            sk = (meta or {}).get("shard_key") or ""
            if sk:
                shard_keys.extend([k.strip() for k in sk.split(",") if k.strip()])
        if not shard_keys:
            # 拿不到真实分片键就不猜。宁可不报，也不拿虚构字段名产出错误结论。
            logger.debug("无真实分片键元数据，跳过深度分布式检查")
            return

        try:
            from backend.engine.parser.tdsql_auditor import TDSQLAuditor
            from backend.engine.parser.ast_parser import ASTParser
            from backend.models import Violation, Severity
            expr = ASTParser().parse(sql)
            for f in TDSQLAuditor().check_shard_key_presence(expr, shard_keys):
                sev = Severity.ERROR if f.severity == "ERROR" else Severity.WARNING
                result.violations.append(Violation(
                    rule_id=f.rule_id, severity=sev,
                    message=f.message, suggestion=f.suggestion))
                if f.severity == "ERROR":
                    result.passed = False
        except Exception as e:
            logger.debug(f"TDSQL 深度分布式规则检查跳过: {e}")
```

#### 5.2.4 `audit_file_content()` 改造

```python
    def audit_file_content(self, content: str, file_path: str = "",
                           created_by: str = "", project_id: str = "",
                           evaluate_gate: bool = False,
                           save_history: bool = True,
                           connection_id: str = "",
                           instance_type: Optional[str] = None
                           ) -> tuple[list[AuditResult], AuditSummary,
                                      Optional[GateResult], "InstanceContext"]:
        rule_set_id, overrides = self._resolve_scale()
        ictx = self._resolve_instance(connection_id, instance_type)
        results = self.checker.audit_file(content, file_path=file_path,
                                          rule_overrides=overrides,
                                          instance_type=ictx.instance_type.value)
        summary = self.checker.compute_summary(results)
        ...
        if save_history:
            _save_audit_history(..., instance_ctx=ictx,
                                skipped_rules_count=self.checker.count_skipped_by_scope(
                                    ictx.instance_type.value))
        return results, summary, gate_result, ictx
```

**返回值由三元组改为四元组。** 全部调用点：`sql_audit.py:128`、`sql_audit.py:165`、`sql_audit.py:306`。

#### 5.2.5 `_save_audit_history()` 落列

```python
def _save_audit_history(audit_type: str, source: str, results, summary,
                        created_by: str = "", project_id: str = "",
                        gate_result=None, connection_id: str = "", db_name: str = "",
                        rule_set_id: str = "",
                        instance_ctx=None, skipped_rules_count: int = 0):
    """V1.5：新增 instance_type / instance_type_source / skipped_rules_count。
    instance_ctx 为 None 时三列写入 NULL/''/0，语义与 V1.5 前记录一致。
    """
    ...
        cursor.execute("""
            INSERT INTO audit_history (audit_type, source, total_sql, passed, failed,
                error_count, warning_count, pass_rate, results_json,
                created_by, project_id, gate_passed, gate_detail, created_at,
                connection_id, db_name, rule_set_id,
                instance_type, instance_type_source, skipped_rules_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ...,
            rule_set_id or None,
            instance_ctx.instance_type.value if instance_ctx else None,
            instance_ctx.source.value if instance_ctx else "",
            int(skipped_rules_count or 0),
        ))
```

> **占位符从 17 个增加到 20 个。三处 `?` 与三个新值必须同步添加，漏改会静默错列。** 这是本次改造最容易出错的一处，务必核对数量。

---

## 6. 扫描通道贯通

### 6.1 在线元数据审核 — `backend/api/sql_audit.py::extract_and_audit`（缺陷现场）

`connection_id` 与 `conn_info` **本就在手**（第 213、222 行），只需传下去：

```python
        results, summary, _, ictx = audit_service.audit_file_content(
            full_extracted_sql,
            file_path=filename,
            created_by=_operator(http_request),
            connection_id=connection_id,      # ← V1.5 新增：A类通道，自动解析
            save_history=False
        )
        _skipped = audit_service.checker.count_skipped_by_scope(ictx.instance_type.value)

        report_id = _save_audit_history(
            ..., rule_set_id=_rule_set_id,
            instance_ctx=ictx, skipped_rules_count=_skipped,
        )
```

响应体追加：

```python
            "instance_type": ictx.instance_type.value,
            "instance_type_source": ictx.source.value,
            "instance_type_conflict": ictx.conflict,
            "skipped_rules_count": _skipped,
            "scope_notice": (
                f"本次按【{'分布式' if ictx.instance_type.value == 'distributed' else '集中式'}实例】"
                f"口径评估，已跳过 {_skipped} 条不适用于该实例类型的规则。"
                if _skipped else ""),
```

快照创建处补 `instance_type`：

```python
            snapshot_id = _snap.safe_create_snapshot("schema_audit", {
                ..., "instance_type": ictx.instance_type.value,
            })
```

**改完这一处，用户报告的 R077 误报即消失。** 其余各节是把同一治理覆盖到全系统。

### 6.2 其余端点

| 文件:函数 | 改动 |
|---|---|
| `sql_audit.py::audit_sql` | 解包三元组；`connection_id` 透传；响应加 5 字段 |
| `sql_audit.py::audit_file` | 解包四元组；`connection_id` 透传；响应加 5 字段 |
| `sql_audit.py::audit_upload` | 新增 `instance_type: str = Form(None)`；解包四元组；响应加 5 字段 |
| `sql_audit.py::audit_batch_stream` | 新增 `instance_type: str = Form(None)`；**NDJSON 首帧输出 `{"type":"meta",...}`** |
| `tdsql_manage.py::audit_with_metadata` | 解析 `connection_id` → `checker.audit_sql(..., instance_type=it)`；响应加字段 |
| `tdsql_manage.py` | **新增** `POST /connections/{id}/probe-instance-type`（响应体见 `API-v1.5` §3.1） |
| `tdsql_manage.py` | 实例列表/详情响应补 6 个字段；实例保存后调 `instance_type_service.invalidate(id)` 并异步触发探测 |
| `gitlab_hook.py::audit_diff` / `audit_repository` | 请求体读可选 `instance_type`；`_audit_sql_list` 与 `checker.audit_file` 透传 |
| `cli.py::audit` / `audit_file` | 新增 `--instance-type` 选项，默认读全局配置 |

### 6.3 元数据巡检 C07 分型 — `backend/engine/schema_inspector.py`

C07「无主键的表」**不跳过**（无主键表在任何实例上都该治理），但**理由必须分型**——原文案写死"TDSQL**分布式架构**要求所有表必须有主键"，在集中式实例上理由是错的，会误导使用者。

```python
        {
            "id": "C07",
            "name": "无主键的表",
            "severity": "ERROR",
            "instance_scope": "all",          # V1.5：检查项也带适用域
            "sql": "...",                      # 不变
            "suggestion": "无主键表存在数据一致性与复制风险，建议补充主键",
            "suggestion_by_type": {
                "distributed": "TDSQL分布式架构要求所有表必须有主键（分片键必须包含在主键中），请添加主键列",
                "centralized": "无主键表无法保证行的唯一定位，且影响主从复制效率与在线DDL，请添加主键列",
            },
        },
```

`SchemaInspector.run()` 增加 `instance_type` 形参，渲染时优先取 `suggestion_by_type[instance_type]`，缺失回落 `suggestion`。同时按 `instance_scope` 过滤检查项（当前所有检查项均为 `all`，机制先建好，为后续新增分布式专属巡检项预留）。

### 6.4 分布式 EXPLAIN 闸门

`distributed_explain` 模块整体语义（命中单 SET / 跨 SET 广播 / 分片表无 WHERE）在集中式实例上无意义。**在调用侧加闸门**，不改模块本身：

```python
if ictx.instance_type == InstanceType.DISTRIBUTED:
    report = DistributedExplainAnalyzer().analyze(...)
```

---

## 7. 测试设计

### 7.1 新建测试文件

| 文件 | 用例数 | 覆盖 |
|---|---|---|
| `tests/test_instance_scope_rules.py` | 12 | 规则标注一致性、过滤正确性 |
| `tests/test_instance_type_service.py` | 10 | 解析优先级、探测、缓存、异常回落 |
| `tests/test_instance_scope_e2e.py` | 8 | 端到端：集中式无 R077、分布式零回归 |
| `tests/test_scope_compat.py` | 5 | 存量兼容、NULL 语义、快照口径留痕 |

### 7.2 清单一致性测试（**最重要，锁定 §3 判定表**）

```python
# tests/test_instance_scope_rules.py

DISTRIBUTED_ONLY = {
    "R020", "R021", "R022", "R023", "R024", "R025", "R043", "R048",
    "R053", "R054", "R055", "R056", "R057", "R058", "R059", "R060",
    "R077", "R092", "R097", "R100", "R111", "R112", "R113",
    "R115", "R116", "R117", "R118",
}   # 共 27 条，判定依据见 DETAIL-v1.5 §3.3；R097/R113 由负责人 2026-07-29 裁定


def test_distributed_only_list_is_exactly_as_designed():
    """锁定适用域判定清单。

    本用例失败意味着有人改动了规则的 instance_scope 标注。
    这不一定是错误，但必须同步更新 DETAIL-v1.5 §3.3 判定表并说明理由——
    错标一条规则为 DISTRIBUTED，就会让集中式实例静默漏掉一项检查。
    """
    actual = {c.rule_id for c in ALL_RULE_CLASSES
              if getattr(getattr(c, "instance_scope", None), "value", "all") == "distributed"}
    assert actual == DISTRIBUTED_ONLY, (
        f"多标: {actual - DISTRIBUTED_ONLY}，漏标: {DISTRIBUTED_ONLY - actual}")


def test_no_centralized_only_rules_yet():
    """现行规范中不存在仅集中式适用的规则；若将来新增，需同步更新设计文档"""
    actual = {c.rule_id for c in ALL_RULE_CLASSES
              if getattr(getattr(c, "instance_scope", None), "value", "all") == "centralized"}
    assert actual == set()


def test_rule_counts():
    assert len(ALL_RULE_CLASSES) == 119
    checker = RuleChecker()
    assert len(checker.get_enabled_rules(None, "distributed")) == 119
    assert len(checker.get_enabled_rules(None, "centralized")) == 92


def test_every_rule_has_valid_scope():
    """防止手滑写成字符串或拼错枚举值"""
    for c in ALL_RULE_CLASSES:
        scope = getattr(getattr(c, "instance_scope", None), "value", "all")
        assert scope in ("all", "distributed", "centralized"), f"{c.rule_id}: {scope}"
```

### 7.3 核心行为用例

```python
def test_r077_not_fired_on_centralized():
    """用户报告的缺陷现场：集中式实例不得出现 R077"""
    sql = "CREATE TABLE t_user (id BIGINT PRIMARY KEY, name VARCHAR(64)) ENGINE=InnoDB"
    r = RuleChecker().audit_sql(sql, instance_type="centralized")
    assert "R077" not in {v.rule_id for v in r.violations}


def test_r077_still_fired_on_distributed():
    """反向验证：分布式实例上 R077 必须照常触发，否则等于把功能删了"""
    sql = "CREATE TABLE t_user (id BIGINT PRIMARY KEY, name VARCHAR(64)) ENGINE=InnoDB"
    r = RuleChecker().audit_sql(sql, instance_type="distributed")
    assert "R077" in {v.rule_id for v in r.violations}


def test_window_function_legal_on_centralized():
    """R111：集中式 MySQL 8.0 的合法窗口函数不得被判 ERROR"""
    sql = "SELECT id, ROW_NUMBER() OVER (PARTITION BY dept ORDER BY id) rn FROM t_emp WHERE dept = 1"
    r = RuleChecker().audit_sql(sql, instance_type="centralized")
    assert "R111" not in {v.rule_id for v in r.violations}


def test_distributed_zero_regression():
    """INV-4：分布式口径与 V1.4 的"不过滤"口径逐条一致"""
    checker = RuleChecker()
    for sql in SAMPLE_SQLS:                       # 覆盖 DDL/DML/索引/Oracle兼容
        old = {(v.rule_id, v.message) for v in checker.audit_sql(sql).violations}
        new = {(v.rule_id, v.message)
               for v in checker.audit_sql(sql, instance_type="distributed").violations}
        assert old == new, f"分布式口径发生回归: {sql[:60]}"


def test_ruleset_cannot_reenable_inapplicable_rule():
    """INV-2：规则集不得反向打开一条不适用的规则"""
    overrides = {"R077": {"enabled": True, "severity_override": None}}
    ids = {r.rule_id for r in RuleChecker().get_enabled_rules(overrides, "centralized")}
    assert "R077" not in ids


def test_ruleset_can_still_disable_applicable_rule():
    """适用域只做减法，不影响规则集正常的禁用能力"""
    overrides = {"R012": {"enabled": False, "severity_override": None}}
    ids = {r.rule_id for r in RuleChecker().get_enabled_rules(overrides, "centralized")}
    assert "R012" not in ids
```

### 7.4 解析器用例

```python
def test_probe_wins_over_declaration():
    """G3：探测优先于人工声明"""
    # 实例声明 is_distributed=1，但探测结果为 centralized
    ctx = instance_type_service.resolve("conn_mislabeled")
    assert ctx.instance_type == InstanceType.CENTRALIZED
    assert ctx.source == TypeSource.PROBED
    assert ctx.conflict is True


def test_probe_failure_falls_back_to_declaration():
    """探测失败退回声明值，不得中断（INV-5）"""
    ctx = instance_type_service.resolve("conn_unreachable")
    assert ctx.source == TypeSource.DECLARED


def test_both_probes_error_returns_none_not_centralized():
    """最关键的一条：两个探针都异常时必须返回 None。

    若此时返回 'centralized'，一次网络故障就会让分布式实例被判成集中式，
    27 条分布式规则静默失效——这是最危险的失效模式。
    """
    result, _ = broken_pool.probe_instance_type()
    assert result is None


def test_a_class_ignores_requested_type():
    """INV-2：有 connection_id 时，调用方传的 instance_type 必须被忽略"""
    ctx = instance_type_service.resolve("conn_centralized", requested="distributed")
    assert ctx.instance_type == InstanceType.CENTRALIZED


def test_b_class_uses_requested_then_default():
    assert instance_type_service.resolve("", "centralized").source == TypeSource.REQUEST
    assert instance_type_service.resolve("", None).source == TypeSource.DEFAULT
```

### 7.5 兼容性用例

```python
def test_legacy_calls_still_work():
    """存量调用（不传 instance_type）行为与 V1.4 完全一致"""
    assert len(RuleChecker().get_enabled_rules()) == 119


def test_null_instance_type_means_unknown_scope():
    """V1.5 前的历史记录 instance_type 为 NULL，前端显示"口径未知"，不得回填"""
    ...


def test_snapshot_records_instance_type():
    """快照落口径留痕（只留痕，不参与对比校验）"""
    snap = create_snapshot_for_centralized_scan()
    assert snap["instance_type"] == "centralized"


def test_compare_behavior_unchanged():
    """本版本不做口径隔离：跨口径对比照常返回，不拒绝也不加警示。

    负责人决策（2026-07-29）：试运行期无历史基线资产，该校验属过度设计。
    留 scan_snapshots.instance_type 列即可，将来需要时零成本补上。
    """
    resp = compare(snapshot_distributed, snapshot_centralized)
    assert "scope_warning" not in resp
```

### 7.6 真实环境验收（SIT）

| # | 用例 | 步骤 | 预期 |
|---|---|---|---|
| S1 | **集中式实例元数据审核** | 对真实集中式实例执行在线元数据审核 | 报告中 R077 = 0；横幅显示"按【集中式】口径评估，已跳过 27 条" |
| S2 | **分布式实例零回归** | 同一分布式实例，v1.4.0.1 与 v1.5 各扫一次同一库 | 违规条目**逐条 diff 完全一致** |
| S3 | 探测准确性 | 分别对分布式/集中式实例点「重新探测」 | 结论与实际一致，`source=probed` |
| S4 | 冲突提示 | 把分布式实例故意改标为"集中式"，重新探测 | 冲突红标；审核仍按分布式执行（**扫描结果里 R077 仍在**） |
| S5 | 门禁联动 | 集中式实例上原被 R077 卡住的变更 | 门禁放行 |
| S6 | 快照口径留痕 | 集中式实例扫描后查 `scan_snapshots` | `instance_type = 'centralized'` |
| S7 | 对比行为不变 | v1.5 前快照 vs v1.5 后快照 | 正常出对比报告（本版本不做口径隔离，见 §3.5 决策） |
| S8 | 即时审核 | 集中式实例下审核一条无 WHERE 的 UPDATE | 有 R013/R014（通用），**无 DIST_001/DIST_002** |
| S9 | 巡检文案 | 集中式实例元数据巡检 C07 | 建议文案不含"分布式"字样 |
| S10 | 全量回归 | `pytest` | 全绿 |

---

## 8. 前端改造清单

| # | 位置 | 改动 |
|---|---|---|
| F1 | 元数据审核报告页 | 顶部渲染口径横幅（`scope_notice`）；`instance_type_source == 'default'` 时追加"（口径由系统默认推定，请确认）" |
| F2 | 实例管理列表「类型」列 | 冲突时红标 + tooltip；显示类型来源徽标（探测/声明） |
| F3 | 实例管理操作列 | 新增「重新探测」按钮 → `POST /connections/{id}/probe-instance-type` |
| F4 | 文件上传 / 批量流式对话框 | 新增实例类型下拉框，默认选中全局默认值 + 说明文字 |
| F5 | 批量流式结果渲染 | **识别并跳过 `type` 字段存在的帧**（`type=meta`）——不改会把元信息帧当成一条结果渲染出来 |
| F6 | 规则管理页 | 新增「适用域」列（通用/仅分布式）；顶部可切换按实例类型查看，联动 `effective_total` |
| F7 | 规则集配置页 | 显示 `effective_counts`："启用 119 条（分布式实例实跑 119 条 / 集中式实例实跑 92 条）" |
| F8 | 历史记录列表 | 新增「口径」列；`null` 显示灰色`未知` + tooltip |
| ~~F9~~ | ~~扫描对比页~~ | **本版本不做**（负责人决策：不做口径隔离） |
| F10 | 系统配置页 | 新增「默认实例类型」设置项（仅 admin 可见），提示语写"**最长 5 分钟生效**" |

> **F5 是唯一的破坏性变更，必须与后端同版本上线。**

---

## 9. 施工检查清单

开发完成后逐项自检：

- [ ] `InstanceType` / `InstanceScope` / `TypeSource` 三个枚举已加
- [ ] `BaseRule.instance_scope` 默认值是 `ALL`（**不是 DISTRIBUTED**）
- [ ] 27 条规则已标注，且与 §3.3 判定表**逐条一致**
- [ ] §3.5 的 4 条规则文本改写已按逐字规格完成（R038/R114 只改文本不改 scope；R097/R113 改 scope + 文本）
- [ ] R038/R097/R113/R114 的 `check()` 判定逻辑**一行未动**（只改元数据字符串）
- [ ] `get_enabled_rules()` 是**唯一**的适用域过滤点（INV-1）
- [ ] 规则集**无法**反向启用不适用规则（INV-2）
- [ ] `probe_instance_type()` 两探针全异常时返回 `None`（**不是 `centralized`**）
- [ ] `_save_audit_history` 的 `?` 占位符 **20 个**，与值一一对应
- [ ] 全部 SQL 使用 `?` 占位符，无手写 `%s`
- [ ] `audit_file_content` 的 3 处调用点均已改为解包四元组
- [ ] `audit_single_sql` 的调用点均已改为解包三元组
- [ ] 硬编码 `["order_id", "user_id"]` **已从代码中彻底消失**（全库检索为 0）
- [ ] `_PATH_TO_MENU` 已登记 `/api/v1/config/default-instance-type`
- [ ] `PUT /config/default-instance-type` 处理函数内**另有** `role == "admin"` 显式校验
- [ ] 前端 NDJSON 已处理 `type=meta` 帧（F5）
- [ ] 所有生效时延文案写"**最长 5 分钟**"，全库检索无"即时生效"
- [ ] 迁移文件 `v4/040_instance_type_scope.sql` 注释均为整行 `--`，无行尾注释
- [ ] 存量 `audit_history` / `scan_snapshots` **无任何回填 UPDATE**
- [ ] 版本号已升至 `1.5.0.0`（`backend/config.py` + `frontend/index.html` 两处标题与页脚）
- [ ] `pytest` 全绿
- [ ] S1 / S2 两条核心 SIT 用例在真实实例上通过
