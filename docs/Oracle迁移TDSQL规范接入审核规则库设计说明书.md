# Oracle迁移TDSQL适配规范接入审核规则库 — 架构规划与详细设计说明书

> 版本：V2.1 设计稿（对应后端 APP_VERSION 2.0.0 → 2.1.0）
> 规范来源：TDSQL 原厂《TDSQL兼容业务系统适配改造方案》V1.5.1（下称"原厂文档"）
> 编写日期：2026-07-06
> 交付对象：编码智能体（严格按本说明书施工）
> 基线代码：main 分支 commit `4a4a649`

---

## 第 1 章 背景与目标

### 1.1 背景

原厂交付的《TDSQL兼容业务系统适配改造方案》V1.5.1 给出了 Oracle 业务系统迁移 TDSQL 时**必须改写/必须注意**的完整规范：不支持的函数（to_char/nvl/decode/…）、不支持的语法（rownum/merge into/with as/…）、分布式限制（窗口函数/临时表/游标/分片键六条军规/…）、以及高风险注意事项（update case when 顺序/主键长度/删分区/…）。这些规范对在 TDSQL 上开发的业务系统**至关重要**，必须全部纳入本审核工具的规则库，并在每一次审核任务中确实生效。

### 1.2 目标（验收口径）

1. **无遗漏**：原厂文档的每一个规范点，都在本设计的《追溯矩阵》（第 3 章）中有归宿——要么成为新规则、要么映射到既有规则（含增强）、要么以明确理由标注为"非SQL静态审核项"。
2. **新增 42 条规则（R078–R119）**，新分类 `oracle_compat`（Oracle迁移兼容），规则总数 77 → **119**。
3. **全链路生效**：新规则在全部 5 个审核入口（即时审核/文件审核/文件上传/GitLab审核/元数据增强审核）自动生效，且服从规则集多租户覆盖机制。
4. **零回归**：既有 820 个测试用例修改口径后全部通过；新规则单测全覆盖（每条规则至少 1 正 1 反用例）。

### 1.3 明确不做（本期范围外）

- 不做 SQL 自动改写/自动转换（仅审核+给出改写建议文案）。
- 不做实例模式（分布式/集中式）区分执行——与既有 R053-R060 分布式规则同口径：**全部常开**，规则描述中注明适用模式（本行客户以分布式为主，从严审核；集中式豁免通过规则集按项目禁用实现）。
- 应用侧编码实践（流式查询/2PC/TCC 等）不做成规则，进追溯矩阵备案。

---

## 第 2 章 系统现状分析（编码前必读）

### 2.1 规则引擎架构（现状事实，已逐一核实）

| 组件 | 位置 | 关键事实 |
|---|---|---|
| 规则基类 | `backend/engine/rules/base.py` | `BaseRule`：`rule_id/category/severity/description/enabled/spec_source/fix_suggestion` + `check(parsed, table_metadata) -> Optional[Violation]`（**单条violation**）；`_make_violation()` 辅助 |
| 规则注册 | `backend/engine/rules/__init__.py` | `ALL_RULE_CLASSES` 列表（77 个类），文件头 docstring 记录分类分布 |
| 检查器 | `backend/engine/checker.py` | `RuleChecker._load_default_rules()` 实例化 ALL_RULE_CLASSES；`audit_sql()` 逐规则执行；**`category=="ddl"` 的规则仅在 CREATE/ALTER/DROP 时执行，其余分类的规则对所有 SQL 都执行**（新分类规则必须在 check 内自守卫 sql_type）；规则异常降级为 WARNING violation；按 `(rule_id, message)` 去重 |
| 解析器 | `backend/engine/parser.py` | sqlglot **mysql 方言** + `_regex_pre_parse` 正则预解析；**解析失败时 `parse_error` 置位、`ast=None`，但规则仍会执行**；`ParsedSQL.raw_sql` 始终可用 |
| 分类枚举 | `backend/models/__init__.py:34` `RuleCategory` | 现有 8 值（naming/ddl/dml/performance/distributed/index/transaction/security） |
| 级别枚举 | 同上 `Severity` | ERROR / WARNING / INFO |
| 规则集 | `backend/services/ruleset_service.py` | default 规则集 = 空覆盖 = 全规则默认执行；自定义规则集仅存差异覆盖，**未覆盖的规则按默认 enabled=True 执行 → 新规则自动在所有既有规则集中生效** |

### 2.2 审核任务全链路（新规则的生效路径，必须逐一保障）

| # | 入口 | 代码路径 | RuleChecker 来源 |
|---|---|---|---|
| 1 | 即时审核 `POST /api/v1/audit/sql` | `api/sql_audit.py` → `AuditService.audit_single_sql` | `AuditService.__init__` 的 `self.checker` |
| 2 | 文件审核 `POST /api/v1/audit/file` | 同上 → `audit_file_content` | 同上 |
| 3 | 文件上传 `POST /api/v1/audit/upload` | 同上 | 同上 |
| 4 | GitLab Diff/仓库/Webhook `POST /api/v1/gitlab/*` | `api/gitlab_hook.py:31` 模块级 `checker = RuleChecker()` | 模块级实例 |
| 5 | 元数据增强审核 `POST /api/v1/tdsql/audit/with-metadata` | `api/tdsql_manage.py:417` 内部新建 `RuleChecker` | 请求内新建 |

**结论**：5 个入口全部经 `ALL_RULE_CLASSES` 加载规则 → **新规则只要注册进 `ALL_RULE_CLASSES` 即全链路自动生效**，无需改任何入口代码。第 9 章测试将对 5 个入口逐一验证。

### 2.3 关键技术约束（决定检测策略）

**约束 A（最重要）**：解析器使用 sqlglot **mysql 方言**。原厂文档中的 Oracle 语法（`(+)` 外连接、`CONNECT BY`、`MERGE INTO`、`MINUS`、`decode(...)` 部分形态等）在 mysql 方言下**大概率解析失败**（`parse_error` 置位、`ast=None`）。因此：

> **新规则必须以 `parsed.raw_sql` 的正则检测为第一优先（regex-first）**，AST 检测仅作为解析成功时的增强手段。凡依赖 AST 的检测必须先判空 `parsed.ast`。

**约束 B**：正则检测必须防误报——关键字出现在**字符串字面量或注释中**不得命中。设计统一的清洗助手（见 4.3）。

**约束 C**：`check()` 只返回单条 violation，一条规则检测多个点时命中第一个即返回。

**约束 D**：checker 对 `category=="ddl"` 有前置过滤，新分类 `oracle_compat` 不享受该过滤——凡只对 DDL 有意义的新规则（R097/R098/R115/R116/R117/R118/R078）必须在 check 开头自守卫：`if not parsed.is_create_table: return None`（按需含 is_alter_table）。

### 2.4 既有规则与原厂文档的重叠（已逐条核实，禁止重复建规则）

| 原厂文档规范点 | 既有规则 | 处理 |
|---|---|---|
| 插入语句必须指定字段名（分布式路由） | R041"INSERT/REPLACE语句必须显式指定列名"、R048"字段列表必须包含分片键" | **映射**，不新增 |
| 分布式不支持临时表 | R024"禁止CREATE TEMPORARY TABLE"、R032"禁止临时表复杂业务" | **映射+增强 E1**（Oracle GTT 语法识别，见 6.1） |
| 分片键值不能更新 | R021"禁止对分片键UPDATE" | **映射**，不新增 |
| 查询尽量带分片键 | R020"WHERE应含分片键" | **映射**，不新增 |
| 唯一索引必须包含分片键 | R054"分片键必须是主键的一部分" | **映射+增强 E2**（扩展覆盖所有 UNIQUE 索引，见 6.2） |
| 多表关联条件应含分片键 | R053"JOIN必须在分片键上关联" | **映射+增强 E3**（建议文案补充广播表/中间表思路） |
| update/delete..limit 批量 | R058"批量UPDATE/DELETE建议加LIMIT" | **映射+增强 E4**（文案补充主键长度约束提示） |
| 表名不能用保留字 | R002（**仅查表名**，`condition` 已在 `backend/config.py:216` 的 `TDSQL_RESERVED_KEYWORDS` 中） | 别名/列名场景**新增 R101**，R002 不动 |

---

## 第 3 章 原厂文档规范点全量追溯矩阵（无遗漏承诺）

> 逐章逐条对照原厂文档 V1.5.1。**状态**：新增=本期新建规则；映射=既有规则已覆盖；增强=既有规则/解析器修改；备案=非SQL静态审核项（注明理由与归宿）。

| # | 原厂文档章节/规范点 | 状态 | 规则ID / 归宿 |
|---|---|---|---|
| 1 | 数据类型对等转换（number→int/decimal 等） | 新增 | R078 |
| 2 | ROWNUM 替换改写（limit） | 新增 | R079 |
| 3 | NVL 替换改写（ifnull） | 新增 | R080 |
| 4 | DECODE 替换改写（case when/if） | 新增 | R081 |
| 5 | TO_CHAR 替换改写（convert/date_format）+ FM精度格式（lpad/format） | 新增 | R082（FM 精度并入建议文案） |
| 6 | TO_NUMBER 替换改写（cast，注意截断差异） | 新增 | R083 |
| 7 | \|\| 拼接替换改写（concat） | 新增 | R084 |
| 8 | TO_DATE 替换改写（str_to_date + 格式符对照） | 新增 | R085 |
| 9 | TRUNC 替换改写（truncate/日期用date_format） | 新增 | R086 |
| 10 | trim/rtrim/ltrim 用法差异（双参不支持） | 新增 | R087 |
| 11 | ADD_MONTHS 替换改写（adddate/interval） | 新增 | R088 |
| 12 | SUBSTR 用法差异（start 只能从 1 开始） | 新增 | R089 |
| 13 | SYSDATE/SYSTIMESTAMP 替换（sysdate() 函数、TRUNC(sysdate)→DATE_FORMAT） | 新增 | R090 |
| 14 | MERGE INTO 替换（on duplicate key / update join / 拆分） | 新增 | R091 |
| 15 | WITH AS 不支持（改子查询/JOIN；分布式必改） | 新增 | R092 |
| 16 | COALESCE 函数 | 备案 | TDSQL 兼容 COALESCE/IFNULL，无需改写 → 不建规则（建规则会对合法用法误报） |
| 17 | LENGTH 函数差异（字节 vs 字符，需字符数用 char_length） | 新增 | R093 |
| 18 | LISTAGG WITHIN GROUP → group_concat（文档出现两处） | 新增 | R094 |
| 19 | MINUS 替换改写（left join is null） | 新增 | R095 |
| 20 | FULL JOIN 替换改写（left+right union） | 新增 | R096 |
| 21 | 建表 DEFAULT 值使用类型转换函数不支持（Proxy 报 1064） | 新增 | R097 |
| 22 | 非 int 类型做 hash 分区（murmurHash/key分区） | 新增 | R098 |
| 23 | 派生/子查询表必须加别名 | 新增 | R099 |
| 24 | DELETE 语句不支持表别名（分布式） | 新增 | R100 |
| 25 | CONDITION 保留关键字需反引号；8.1 sequence 特殊词别名（nextVal/minValue 等）报错 | 新增 | R101（合并两点：别名/列名用保留字或 sequence 特殊词） |
| 26 | ESCAPE '\' 转义符改 '/' | 新增 | R102 |
| 27 | `< =`、`> =` 运算符中间空格 | 新增 | R103 |
| 28 | 函数与括号间空格（SUM (、COUNT （）、全角括号 | 新增 | R104 |
| 29 | Oracle (+) 外连接 → left/right join | 新增 | R105 |
| 30 | START WITH...CONNECT BY → WITH RECURSIVE(集中式)/应用改造(分布式)，含结果顺序差异提醒 | 新增 | R106（顺序差异写入建议文案） |
| 31 | 查询结果大小写：* 转义为小写字段 | 备案 | 结果集行为说明，非可检出的 SQL 缺陷；已有 R012（禁 SELECT *）间接约束 |
| 32 | 分布式 INSERT 必须指定字段名 | 映射 | R041 + R048（见 2.4） |
| 33 | USERENV() 不支持 | 新增 | R110 |
| 34 | insert into select 限制（目标表有自增列/分区不支持） | 新增 | R107 |
| 35 | sequence 不支持批量获取（select …,seq from 表 / insert…select seq） | 新增 | R108 |
| 36 | update 中 case when 执行顺序与 Oracle 不一致 | 新增 | R109 |
| 37 | 主键长度限制（update/delete..limit 需 PK < varchar(250)@utf8mb4） | 新增+增强 | R115（DDL侧）+ E4（R058 文案） |
| 38 | 分布式 row_number() over 不支持（三种场景改造） | 新增 | R111 |
| 39 | 分布式 with as 替换调整 | 并入 | R092 |
| 40 | 分布式不支持临时表（含事务级/会话级 ON COMMIT） | 映射+增强 | R024/R032 + E1 |
| 41 | 份额 for update 校验、分布式事务 2PC/TCC | 备案 | 业务/应用侧改造项，无法以单条 SQL 静态判定；写入 R111/R106 建议文案不可行，归档至本矩阵备查 |
| 42 | 估值/额度/费用 To_char FM 精度格式 | 并入 | R082 建议文案专段 |
| 43 | 翻页改写方法（强排序分页/不排序分页） | 新增 | R114（深分页检测，建议文案含原厂两种处理方式） |
| 44 | 游标限制（分布式），4 种替代方案 | 新增 | R112（建议文案含 4 方案） |
| 45 | 分片键六条军规：单字段/唯一索引含分片键/值不可更新/查询带分片键/类型限制/值勿中文 | 新增+映射 | R116(单字段)+R117(类型,中文写入文案)+R118(NOT NULL)；唯一索引→R054+E2；更新→R021；查询→R020 |
| 46 | 分布式多表关联注意事项（下推/广播表/中间表） | 映射+增强 | R053 + E3 |
| 47 | proxy 执行计划字段含义 | 备案 | 知识性内容，归 EXPLAIN 分析功能文档，非规则 |
| 48 | 推荐实践：流式查询（JDBC/ODBC） | 备案 | 应用侧编码实践，无 SQL 特征可检 |
| 49 | 8.1 sql 别名语法（sequence 特殊词） | 并入 | R101 |
| 50 | 8.2 函数区别（勿沿用 Oracle 用法，date_format(sysdate()-15) 反例 → date_add) | 新增 | R119（日期算术直接±数字） |
| 51 | 8.3 业务删除分区注意事项（drop partition 高并发路由风险） | 新增 | R113 |
| 52 | 8.4 分页问题（避免排序/限制排序量） | 并入 | R114 建议文案 |

**统计**：新增规则 42 条（R078–R119）；映射既有 8 项；增强既有 4 项（E1–E4）+ 口径修正 2 项（E5/E6）；备案 5 项（#16/31/41/47/48，均已注明理由）。

---

## 第 4 章 总体设计

### 4.1 新分类与规则 ID 分配

- `RuleCategory` 枚举新增：`ORACLE_COMPAT = "oracle_compat"`（中文名：**Oracle迁移兼容**）。
- 新规则 ID：**R078–R119** 连续分配（衔接既有 R001–R077），类名沿用 `R0xx驼峰描述` 惯例。
- 全部 42 条新规则 `category = RuleCategory.ORACLE_COMPAT`，`spec_source` 统一格式：`"ORACLE迁移TDSQL改造适配方案 V1.5.1 - <章节名>"`。
- 新文件：`backend/engine/rules/oracle_compat.py`（全部 42 条规则 + 共享助手），在 `rules/__init__.py` 导入并追加进 `ALL_RULE_CLASSES` 与 `__all__`。

### 4.2 检测策略分层

| 层 | 适用 | 说明 |
|---|---|---|
| L1 正则层（主力） | 34 条规则 | 基于 `clean_sql(parsed.raw_sql)`（见 4.3）做大小写不敏感正则；**不依赖解析成功** |
| L2 AST 层（增强） | R099/R100/R109（以及 L1 规则在 ast 可用时降误报） | `parsed.ast` 非空时用 sqlglot 节点判断；ast 为空回退 L1 或跳过（逐规则注明） |
| L3 结构层 | DDL 类：R078/R097/R098/R115/R116/R117/R118 | 使用 `parsed.columns/column_types/indexes/is_create_table`，配合 raw_sql 正则提取表选项（shardkey/partition by） |
| L4 元数据层 | R107（可选增强） | `table_metadata` 传入时利用（不强制，静态检测兜底） |

### 4.3 共享清洗助手（`oracle_compat.py` 模块级，所有 L1 规则必须使用）

```python
import re
from typing import Optional

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"--[^\n]*")
_STRING_LIT = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")

def clean_sql(sql: str) -> str:
    """审核前清洗：去块注释/行注释/字符串字面量（保留''占位），转小写。
    防止关键字出现在字符串或注释中造成误报。"""
    s = _BLOCK_COMMENT.sub(" ", sql)
    s = _LINE_COMMENT.sub(" ", s)
    s = _STRING_LIT.sub("''", s)
    return s.lower()
```

注意：
- R102（ESCAPE '\'）需要检查**字面量本身**，使用 raw_sql 原文检测，不用 clean_sql。
- R104 的全角括号检测用 raw_sql 原文（clean 后仍保留全角字符，两者皆可，统一用 clean 后文本即可——全角括号不在清洗范围内）。
- 每条规则在 `check()` 内首行做一次 `text = clean_sql(parsed.raw_sql)`；为性能可在模块内做 LRU 缓存（可选，`functools.lru_cache(maxsize=256)` 包装 `clean_sql`）。

### 4.4 严重级别策略（全 42 条的定级依据）

- **ERROR（TDSQL 直接报错或产生错误结果）**：R078 R079 R080 R081 R082 R083 R085 R086 R087 R088 R089 R090 R091 R092 R094 R095 R096 R097 R098 R099 R100 R101 R103 R104 R105 R106 R108 R110 R111 R112 R116 R117 R118
- **WARNING（语义差异/条件限制/性能风险，需人工确认）**：R084 R093 R102 R107 R109 R114 R115 R119
- **INFO（运维注意事项提示）**：R113

### 4.5 版本与口径

- `backend/config.py`：`APP_VERSION = "2.1.0"`。
- 所有出现"77条"的代码注释、docstring、前端文案、测试断言、README 全部更新为 119（详见 6.5/6.6 与第 9 章）。

---

## 第 5 章 详细设计：42 条新规则逐条规格

> 每条含：类名｜级别｜description（照抄入代码）｜检测逻辑（可直接实现）｜fix_suggestion 要点｜✗命中示例｜✓通过示例｜误报防护。`spec_source` 按 4.1 统一格式填对应章节名，不再逐条重复。
> 通用约定：`text = clean_sql(parsed.raw_sql)`；正则均 `re.search`、忽略大小写已由 clean 转小写保证；DDL 自守卫规则已单独注明。

### R078 OracleDataType — 禁止使用 Oracle 专有数据类型
- 类名 `R078OracleDataType`｜ERROR｜DDL 自守卫（`is_create_table or is_alter_table`）
- description：`建表/改表禁止使用Oracle专有数据类型（NUMBER/VARCHAR2/CLOB等），需转换为TDSQL对等类型`
- 检测：遍历 `parsed.column_types`，`type` ∈ {`NUMBER`,`VARCHAR2`,`NVARCHAR2`,`CLOB`,`NCLOB`,`RAW`,`LONG`,`BFILE`,`BINARY_FLOAT`,`BINARY_DOUBLE`,`ROWID`,`UROWID`}。列信息为空（解析失败）时回退正则：`\b(number|varchar2|nvarchar2|clob|nclob|bfile|rowid)\s*[\(,\s]` 且 `is_create_table/alter` 或 text 以 `create table|alter table` 开头。
- 建议：`NUMBER→DECIMAL(p,s)或INT/BIGINT（注意：历史number放整型标志字段的，转换后勿带精度，避免Java取值类型错误）；VARCHAR2→VARCHAR；CLOB→TEXT/LONGTEXT；RAW→VARBINARY；DATE→DATETIME`
- ✗ `CREATE TABLE t (id NUMBER(10), name VARCHAR2(100))`　✓ `CREATE TABLE t (id BIGINT, name VARCHAR(100))`
- 防误报：列名恰为 number 的不命中（检测的是 type 而非 name）。

### R079 RownumUsage — 禁止使用 ROWNUM
- 类名 `R079RownumUsage`｜ERROR
- description：`TDSQL不支持Oracle伪列ROWNUM，请改用LIMIT分页`
- 检测：`re.search(r"\brownum\b", text)`
- 建议：`SELECT * FROM t WHERE 条件 LIMIT n（LIMIT n 等价 LIMIT 0,n）；需排序时LIMIT置于ORDER BY之后：… ORDER BY 字段 LIMIT m,n`
- ✗ `SELECT * FROM t WHERE rownum < 4`　✓ `SELECT * FROM t LIMIT 3`
- 防误报：clean_sql 已排除字符串/注释；列名恰叫 rownum 属命名违规，允许命中（提示反引号或改名）。

### R080 NvlFunction — 禁止使用 NVL
- 类名 `R080NvlFunction`｜ERROR
- description：`TDSQL不支持NVL函数，请改用IFNULL(expr1,expr2)或COALESCE`
- 检测：`\bnvl\s*\(`
- 建议：`NVL(a,b)→IFNULL(a,b)；多条件嵌套或多参用COALESCE(e1,e2,...)；示例：select IFNULL(max(tempa),0) from t`
- ✗ `select nvl(max(tempa),0) from t`　✓ `select ifnull(max(tempa),0) from t`

### R081 DecodeFunction — 禁止使用 DECODE
- 类名 `R081DecodeFunction`｜ERROR
- description：`TDSQL不支持DECODE函数，请改用CASE WHEN或IF()`
- 检测：`\bdecode\s*\(`
- 建议：`decode(x,'A','1','B','2',缺省)→CASE x WHEN 'A' THEN '1' WHEN 'B' THEN '2' ELSE 缺省 END；两分支简单场景可用IF(cond,v1,v2)`
- ✗ `SELECT decode(substr(c,2,1),'A','1','B','2') FROM t`　✓ `SELECT CASE substr(c,2,1) WHEN 'A' THEN '1' ELSE '0' END FROM t`

### R082 ToCharFunction — 禁止使用 TO_CHAR
- 类名 `R082ToCharFunction`｜ERROR
- description：`TDSQL不支持TO_CHAR函数，日期格式化用DATE_FORMAT，数值转换用CONVERT/CAST/FORMAT`
- 检测：`\bto_char\s*\(`
- 建议：`日期→DATE_FORMAT(col,'%Y%m%d')；类型转换→CONVERT(value,type)（type: BINARY/CHAR()/DATE/TIME/DATETIME/DECIMAL/SIGNED/UNSIGNED）；含'FM9999.0999'类精度进位格式的，用LPAD/FORMAT改写，需逐格式核实进位方式，无法满足时应用侧/数据库侧二次转换（估值/额度/费用计算场景重点复核）`
- ✗ `SELECT to_char(amt,'FM9999999999.09999999') FROM t`　✓ `SELECT FORMAT(amt,2) FROM t`

### R083 ToNumberFunction — 禁止使用 TO_NUMBER
- 类名 `R083ToNumberFunction`｜ERROR
- description：`TDSQL不支持TO_NUMBER函数，请改用CAST；注意CAST对非法数字会截断而不报错`
- 检测：`\bto_number\s*\(`
- 建议：`cast(x as unsigned int)/cast(x as decimal(10,2))；注意：cast('11a' as unsigned int)=11（截断），不像to_number直接报错，涉及数据校验的需应用侧兜底`
- ✗ `SELECT to_number(c) FROM t`　✓ `SELECT cast(c as decimal(10,2)) FROM t`

### R084 PipeConcat — 疑似 Oracle || 字符串拼接
- 类名 `R084PipeConcat`｜WARNING
- description：`检测到||运算符：MySQL/TDSQL默认语义为逻辑OR而非字符串拼接，Oracle迁移SQL请改用CONCAT()`
- 检测：`\|\|`（clean 后文本，字面量内已被清除）
- 建议：`'%'||v||'%' → CONCAT('%',v,'%')`
- ✗ `SELECT '%' || c || '%' FROM t`　✓ `SELECT CONCAT('%',c,'%') FROM t`
- 防误报：定级 WARNING（|| 作逻辑 OR 属合法但极少见的写法，人工确认）。

### R085 ToDateFunction — 禁止使用 TO_DATE
- 类名 `R085ToDateFunction`｜ERROR
- description：`TDSQL不支持TO_DATE函数，请改用STR_TO_DATE(date,format)`
- 检测：`\bto_date\s*\(`
- 建议：`to_date(v,'YYYYMMDD')→str_to_date(v,'%Y%m%d')；格式符对照：YYYY→%Y，MM→%m，DD→%d，HH24→%H，MI→%i，SS→%s`
- ✗ `to_date(#dt#,'YYYYMMDD')`　✓ `str_to_date(?,'%Y%m%d')`

### R086 TruncFunction — 禁止使用 TRUNC
- 类名 `R086TruncFunction`｜ERROR
- description：`TDSQL不支持TRUNC函数：数值截断用TRUNCATE(X,D)，日期截断用DATE_FORMAT`
- 检测：`\btrunc\s*\(`（注意 `\b` + 紧跟 `(`，不会误中 TRUNCATE(）
- 建议：`TRUNC(x,2)→TRUNCATE(x,2)；TRUNC(sysdate)→DATE_FORMAT(sysdate(),'%Y%m%d')；TRUNC(sysdate,'mm')→DATE_FORMAT(sysdate(),'%Y%m01')`
- ✗ `SELECT trunc(a/b,2) FROM dual`　✓ `SELECT truncate(a/b,2) FROM dual`

### R087 TrimTwoArgs — LTRIM/RTRIM 双参数用法不支持
- 类名 `R087TrimTwoArgs`｜ERROR
- description：`TDSQL的LTRIM/RTRIM仅支持单参数去空格，去除指定字符请用TRIM({BOTH|LEADING|TRAILING} remstr FROM str)`
- 检测：`\b[lr]trim\s*\([^()]*,`（一层括号内出现逗号即双参；括号内含嵌套函数的复杂场景允许漏检，注释说明）
- 建议：`ltrim(s,'0')→TRIM(LEADING '0' FROM s)；rtrim(s,'x')→TRIM(TRAILING 'x' FROM s)；注意TDSQL的remstr是整串匹配而非Oracle的字符集合匹配，多字符集合需嵌套或应用侧处理`
- ✗ `SELECT ltrim(code,'0') FROM t`　✓ `SELECT TRIM(LEADING '0' FROM code) FROM t`
- 防误报：clean_sql 后字面量变 `''`，`ltrim(code,'')` 形态仍含逗号，正确命中。

### R088 AddMonths — 禁止使用 ADD_MONTHS
- 类名 `R088AddMonths`｜ERROR
- description：`TDSQL不支持ADD_MONTHS函数，请改用ADDDATE(date, INTERVAL expr MONTH)`
- 检测：`\badd_months\s*\(`
- 建议：`add_months(d,-1)→adddate(d, INTERVAL -1 MONTH)；unit支持SECOND/MINUTE/HOUR/DAY/MONTH/YEAR`
- ✗ `add_months(str_to_date(?,'%Y%m%d'),-1)`　✓ `adddate(str_to_date(?,'%Y%m%d'), INTERVAL -1 MONTH)`

### R089 SubstrZeroStart — SUBSTR 起始位置不能为 0
- 类名 `R089SubstrZeroStart`｜ERROR
- description：`TDSQL的SUBSTR起始位置只能从1开始，start=0将返回空串（Oracle中0按1处理），结果错误`
- 检测：`\bsubstr(?:ing)?\s*\(\s*[^,()]+,\s*(?:0|'0')\s*[,)]`。注意 clean_sql 会把 `'0'` 清成 `''`，因此**本规则用 raw_sql 自行清洗注释但保留字面量**：实现时对 raw_sql 仅去注释（复用 `_BLOCK_COMMENT/_LINE_COMMENT`）后 lower，再匹配上式。
- 建议：`substr(c,0,9)→substr(c,1,9)（Oracle中0与1等效，TDSQL必须从1开始）`
- ✗ `SELECT substr(c,'0','9') FROM t`　✓ `SELECT substr(c,1,9) FROM t`

### R090 BareSysdate — SYSDATE/SYSTIMESTAMP 裸用
- 类名 `R090BareSysdate`｜ERROR
- description：`Oracle的SYSDATE/SYSTIMESTAMP关键字用法不支持，TDSQL需使用sysdate()/NOW()函数（带括号）`
- 检测：`\bsystimestamp\b` 或 `\bsysdate\b(?!\s*\()`
- 建议：`SYSDATE→sysdate()；SYSTIMESTAMP→NOW(3)/sysdate(3)；截取日期用DATE_FORMAT(sysdate(),'%Y%m%d')，当月第一天'%Y%m01'`
- ✗ `SELECT TRUNC(sysdate) FROM dual`（同时命中R086/R090）　✓ `SELECT sysdate() FROM dual`

### R091 MergeInto — 禁止使用 MERGE INTO
- 类名 `R091MergeInto`｜ERROR
- description：`TDSQL不支持MERGE INTO：集中式且按主键/唯一键关联可用INSERT…ON DUPLICATE KEY UPDATE，分布式需拆分为程序逻辑`
- 检测：`\bmerge\s+into\b`
- 建议：`关联字段为主键/唯一键（集中式）→INSERT INTO … ON DUPLICATE KEY UPDATE；仅更新不插入→UPDATE a JOIN b ON a.id=b.id SET a.xx=b.xx；其余场景拆SQL由程序实现（分布式一律拆分）`
- ✗ `MERGE INTO t USING s ON (t.id=s.id) WHEN MATCHED THEN UPDATE SET …`　✓ `INSERT INTO t(id,v) VALUES(1,2) ON DUPLICATE KEY UPDATE v=2`

### R092 WithAsCte — WITH AS 子查询不支持
- 类名 `R092WithAsCte`｜ERROR
- description：`分布式实例不支持WITH AS(CTE)，请改写为子查询或JOIN；集中式8.0递归场景可评估WITH RECURSIVE`
- 检测：AST 优先：`parsed.ast` 非空且 `parsed.ast.args.get("with")` 非空；回退正则：`^\s*with\s+(recursive\s+)?\w+\s+as\s*\(`（对 text 用 `re.match`，multiline 不需要——语句级检测）
- 建议：`WITH a AS(SELECT…) SELECT * FROM a JOIN b → SELECT * FROM (SELECT…) a JOIN b；或直接JOIN改写；复杂多关联+union all场景建议拆查询/引入中间表；多次交互场景由应用先落中间表再二次查询`
- ✗ `WITH a AS (SELECT id,name FROM t1 WHERE id>3) SELECT * FROM a JOIN t2 b ON a.name=b.name`　✓ `SELECT * FROM (SELECT id,name FROM t1 WHERE id>3) a JOIN t2 b ON a.name=b.name`

### R093 LengthSemantics — LENGTH 字节/字符语义差异
- 类名 `R093LengthSemantics`｜WARNING
- description：`TDSQL的LENGTH()返回字节数（Oracle为字符数），中文场景结果不一致；需字符数请用CHAR_LENGTH()`
- 检测：`\blength\s*\(`（排除 `char_length`/`octet_length`：用 `(?<!char_)(?<!octet_)\blength\s*\(` —— 注：Python re 支持定长 lookbehind，`char_` 5字符、`octet_` 6字符需分写两个 lookbehind）
- 建议：`select length(c)→select char_length(c)（需要字符数时）；确认确需字节数的可保留并在工单注明`
- ✗ `select length(name) from t`　✓ `select char_length(name) from t`

### R094 ListaggFunction — 禁止使用 LISTAGG/WITHIN GROUP
- 类名 `R094ListaggFunction`｜ERROR
- description：`TDSQL不支持LISTAGG() WITHIN GROUP()，请改用GROUP_CONCAT`
- 检测：`\blistagg\s*\(` 或 `\bwithin\s+group\b`
- 建议：`LISTAGG(a||':'||b,'|') WITHIN GROUP(ORDER BY a,b)→GROUP_CONCAT(a,':',b ORDER BY a,b SEPARATOR '|')；语法：group_concat([DISTINCT] 字段 [ORDER BY 排序 ASC/DESC] [SEPARATOR '分隔符'])`
- ✗ `select listagg(c,',') within group(order by c) from t`　✓ `select group_concat(c order by c separator ',') from t`

### R095 MinusOperator — 禁止使用 MINUS 集合运算
- 类名 `R095MinusOperator`｜ERROR
- description：`TDSQL不支持MINUS集合运算，请改用LEFT JOIN…IS NULL或NOT EXISTS实现差集`
- 检测：`(^|\s)minus(\s|$)` 且左右邻近存在 select（实现：`re.search(r"\bminus\b", text)` 且 `text.count("select") >= 2`，双条件降误报——列名 minus 且单 select 的不命中）
- 建议：`A minus B→select a.* from A a left join B b on a.id=b.id where b.id is null（注意minus含去重语义，必要时补distinct）`
- ✗ `select * from t1 minus select * from t2`　✓ `select a.* from t1 a left join t2 b on a.id=b.id where b.id is null`

### R096 FullJoin — 禁止使用 FULL JOIN
- 类名 `R096FullJoin`｜ERROR
- description：`底层MySQL不支持FULL JOIN，请改写为LEFT JOIN UNION RIGHT JOIN`
- 检测：`\bfull\s+(outer\s+)?join\b`
- 建议：`select * from A full join B on…→ select…inner join…UNION select…left join…UNION select…right join…（按业务保留需要的三段）`
- ✗ `select * from a full outer join b on a.id=b.id`　✓ `select * from a left join b on a.id=b.id union select * from a right join b on a.id=b.id`

### R097 DefaultValueFunction — 建表默认值禁用函数/类型转换
- 类名 `R097DefaultValueFunction`｜ERROR｜DDL 自守卫（is_create_table or is_alter_table）
- description：`TDSQL建表字段DEFAULT值不支持类型转换/函数表达式（Proxy报ERROR 1064），仅CURRENT_TIMESTAMP例外`
- 检测：遍历 `parsed.columns`，`has_default` 且 `default_value` 含 `(` 且其小写不以 `current_timestamp`/`now` 开头 → 命中。解析失败回退正则：`default\s+\w+\s*\(`（text 上，且排除 `default\s+current_timestamp`）。
- 建议：`如 data_dt char(8) default date_format(…)→改为 data_dt char(8) not null，默认值由应用层赋值；或改字段类型使默认值无需转换`
- ✗ `create table t (data_dt char(8) default date_format(current_timestamp,'%Y%m%d'))`　✓ `create table t (data_dt char(8) not null, ts datetime default current_timestamp)`

### R098 HashPartitionNonInt — 非整型字段做 HASH 分区
- 类名 `R098HashPartitionNonInt`｜ERROR｜DDL 自守卫
- description：`HASH分区要求分区字段为整型；char/varchar等非整型字段请改用KEY分区或murmurHashCodeAndMod改造`
- 检测：text 匹配 `partition\s+by\s+hash\s*\(\s*([a-z_][\w]*)\s*\)`（捕获单纯列名；表达式形态如含函数则跳过），在 `parsed.columns` 中查该列 type ∉ {INT,BIGINT,SMALLINT,MEDIUMINT,TINYINT,INTEGER} → 命中；列不存在或列信息为空则不判（防误报）。
- 建议：`方案1：partition by hash(murmurHashCodeAndMod(col,N))；方案2：改用KEY分区（key为hash模式延伸，支持非整型）`
- ✗ `create table t (c varchar(20)) partition by hash(c) partitions 4`　✓ `create table t (id bigint) partition by hash(id) partitions 4`

### R099 DerivedTableAlias — 派生表/子查询必须加别名
- 类名 `R099DerivedTableAlias`｜ERROR
- description：`FROM后的子查询（派生表）必须指定别名，否则TDSQL报错`
- 检测：**AST 优先**：`parsed.ast` 非空时，`for sub in parsed.ast.find_all(exp.Subquery)`：若 `sub.find_ancestor(exp.From, exp.Join)` 非空且 `not sub.alias` → 命中。AST 为空时回退正则：`from\s*\(\s*select[\s\S]*?\)\s*(where|group\s+by|order\s+by|limit|on|join|union|$)`（闭括号后紧跟关键字/结尾即无别名；仅检测单层，复杂嵌套允许漏检，代码注释说明）。需要 `from sqlglot import exp` 导入。
- 建议：`select * from (select * from t)→select * from (select * from t) B`
- ✗ `select * from (select * from t1)`　✓ `select * from (select * from t1) b`

### R100 DeleteTableAlias — DELETE 语句禁用表别名
- 类名 `R100DeleteTableAlias`｜ERROR
- description：`分布式实例DELETE语句不支持对被删表设置别名，请使用真实表名`
- 检测：仅 `parsed.sql_type == "DELETE"` 时检查。正则（text 上）：`^\s*delete\s+from\s+[\`"\w.]+\s+(?:as\s+)?([a-z_]\w*)\b`，且捕获词 ∉ {`where`,`order`,`limit`,`using`,`partition`,`for`,`join`,`inner`,`left`,`right`}。AST 可用时增强：`parsed.ast` 为 exp.Delete 且 `parsed.ast.args["this"].alias` 非空 → 命中。
- 建议：`DELETE FROM T1 a WHERE EXISTS(SELECT 1 FROM T2 b WHERE a.id=b.id)→DELETE FROM T1 WHERE EXISTS(SELECT 1 FROM T2 WHERE T1.id=T2.id)`
- ✗ `delete from t1 a where exists (select 1 from t2 b where a.id=b.id)`　✓ `delete from t1 where exists (select 1 from t2 where t1.id=t2.id)`

### R101 ReservedWordAlias — 别名/列名使用保留字或 sequence 特殊词
- 类名 `R101ReservedWordAlias`｜ERROR
- description：`别名/标识符使用了TDSQL保留字（如CONDITION）或sequence特殊词（NEXTVAL/MINVALUE等），需加反引号或改用普通别名`
- 检测：新增常量 `TDSQL_SEQUENCE_KEYWORDS = {"nextval","currval","minvalue","maxvalue","cycle","increment"}`（置于 oracle_compat.py）。检测 text：`\bas\s+(condition|nextval|currval|minvalue|maxvalue|cycle|increment)\b`（AS 别名形态）或 `(select|,)\s*(condition)\s*(,|from)`（裸引用 CONDITION 列）。命中词已被反引号包裹的不算（clean 后检查原词左右是否 \`：实现时对 raw_sql 定位确认，或简化为正则排除 `` `condition` `` 形态：`(?<![\`\w])(condition)(?![\`\w])`）。
- 建议：`select \`CONDITION\` from t（加反引号）；别名nextVal/minValue等sequence特殊词改为普通词（如nvVal），不同TDSQL版本升级后会将其视为关键字导致报错`
- ✗ `select condition from t_rule where id=1`；`select next_val as nextval from seq_cfg`　✓ ``select `condition` from t_rule``
- 防误报：仅匹配上述有限词集，不做全保留字扫描（R002 已管表名；列名全量扫描误报率高，本规则聚焦原厂文档点名的词）。

### R102 EscapeBackslash — ESCAPE '\' 转义符
- 类名 `R102EscapeBackslash`｜WARNING
- description：`LIKE…ESCAPE '\'在TDSQL中行为与Oracle不一致，建议改用其他转义符（如'/'）`
- 检测：**raw_sql 原文**（去注释后）匹配 `escape\s+'\\{1,2}'`
- 建议：`LIKE '%\_%' ESCAPE '\' → LIKE '%/_%' ESCAPE '/'`
- ✗ `where c like '%\_%' escape '\\'`　✓ `where c like '%/_%' escape '/'`

### R103 OperatorSpace — 比较运算符中间含空格
- 类名 `R103OperatorSpace`｜ERROR
- description：`比较运算符中间不能有空格（如"< ="、"> ="、"! ="、"< >"），TDSQL会语法报错`
- 检测：text 匹配 `[<>!]\s+=` 或 `<\s+>`
- 建议：`"< =" → "<="；"> =" → ">="；"! =" → "!="；"< >" → "<>"`
- ✗ `where a < = 10`　✓ `where a <= 10`
- 防误报：`a < b = c` 罕见但会命中——文档要求从严，保留 ERROR；clean 后字面量内不命中。

### R104 FunctionParenSpace — 函数与括号间空格/全角括号
- 类名 `R104FunctionParenSpace`｜ERROR
- description：`常用函数名与括号之间不能有空格（如SUM (、COUNT （），且禁止使用全角括号`
- 检测（两段，命中其一）：
  1) 全角括号：raw_sql 含 `（` 或 `）` → 命中（message 注明全角括号）。
  2) 函数空格：text 匹配 `\b(sum|count|avg|max|min|ifnull|substr|substring|concat|group_concat|char_length|date_format|str_to_date|truncate|cast|convert|coalesce|upper|lower|round|abs)\s+\(`。**注意**：白名单函数法，绝不可用通用 `\w+\s+\(`（会误伤 `in (`、`values (`、`exists (`、`and (` 等关键字）。
- 建议：`SUM (→SUM(；COUNT （→COUNT(；全角（）改半角()`
- ✗ `select sum (amt), count （1） from t`　✓ `select sum(amt), count(1) from t`

### R105 OracleOuterJoin — Oracle (+) 外连接语法
- 类名 `R105OracleOuterJoin`｜ERROR
- description：`不支持Oracle的(+)外连接语法，请改写为LEFT/RIGHT OUTER JOIN`
- 检测：text 匹配 `\(\s*\+\s*\)`
- 建议：`WHERE a.k=b.k(+)→FROM a LEFT JOIN b ON a.k=b.k（(+)在等号右侧为LEFT JOIN，在左侧为RIGHT JOIN），其余过滤条件保留在WHERE`
- ✗ `where tabx.k = tabm.k(+)`　✓ `from tabx left join tabm on tabx.k=tabm.k`

### R106 ConnectBy — START WITH…CONNECT BY 层级查询
- 类名 `R106ConnectBy`｜ERROR
- description：`不支持Oracle层级查询START WITH…CONNECT BY：集中式可用WITH RECURSIVE改写，分布式需应用代码实现递归`
- 检测：`\bconnect\s+by\b`（`start with` 单独出现不判，避免误报）
- 建议：`集中式8.0→WITH RECURSIVE（注意：返回为层次遍历序，Oracle为前序遍历，对顺序有要求需应用侧重排）；CONNECT BY NOCYCLE→WITH RECURSIVE+LIMIT；CONNECT BY LEVEL批量取数→业务代码循环实现；分布式→应用先查必要信息再while/for递归调用`
- ✗ `select * from dept start with pid=0 connect by prior id=pid`　✓ `with recursive cte as (…) select * from cte`（集中式）

### R107 InsertSelectRestriction — INSERT INTO…SELECT 受限
- 类名 `R107InsertSelectRestriction`｜WARNING
- description：`INSERT INTO…SELECT在目标表含自增列或分区时不支持，请执行前确认目标表结构`
- 检测：`parsed.sql_type in ("INSERT","REPLACE")` 且 text 匹配 `insert\s+into[\s\S]+?\bselect\b`。L4 增强：若 `table_metadata` 提供目标表且含 `has_auto_increment`/`is_partitioned` 信息则可升级为 ERROR（本期元数据结构暂无这两个字段，保留 WARNING 文案提示，代码预留 TODO 注释）。
- 建议：`确认目标表无自增列且未分区；受限场景改为程序分批SELECT后INSERT VALUES`
- ✗ `insert into ta(a,b) select a,b from tb`（提示确认）　✓ `insert into ta(a,b) values(1,2)`

### R108 SequenceBatchFetch — sequence 批量获取不支持
- 类名 `R108SequenceBatchFetch`｜ERROR
- description：`TDSQL的sequence在多行SELECT/INSERT…SELECT中不支持批量递增获取（多行返回相同值或直接报错）`
- 检测：text 含 `\b(nextval|currval)\b`（含 `seq.nextval` 点号形态，`\b` 均可命中）且（`parsed.sql_type=="SELECT"` 且 text 含 `\bfrom\b`）或（sql_type in ("INSERT","REPLACE") 且含 `select`）
- 建议：`方案1：改自增序列（多分片可能重复，需评估）；方案2：先insert into select落中间表，代码批量取序列二次赋值再回插业务表；方案3：预生成批量序列表（序列值/使用标记/时间戳），日终定时补充，使用时联查并更新标记`
- ✗ `insert into ta select a,b,seq_x.nextval from tb`　✓ `select seq_x.nextval from dual`（单行取号不命中——无多行FROM实表特征时放过：`from dual` 在检测中排除，实现：text 含 `from dual` 且 sql_type==SELECT 时跳过）

### R109 UpdateCaseWhenOrder — UPDATE 多字段 CASE WHEN 求值顺序差异
- 类名 `R109UpdateCaseWhenOrder`｜WARNING
- description：`UPDATE中后续SET的CASE WHEN会读到前面SET字段的新值（与Oracle读旧值不同），判断条件字段的赋值应放到最后`
- 检测：**纯 AST**（`parsed.ast` 为 exp.Update 才检测，否则返回 None）：取 `parsed.ast.expressions`（SET 赋值有序列表）；逐个赋值 `eq_i`（exp.EQ：this=目标列, expression=值表达式）；记录已赋值目标列名集合 `assigned`；对 i≥1 的赋值，若其 `expression.find_all(exp.Column)` 中任一列名 ∈ assigned → 命中。
- 建议：`将修改判断条件字段（如stcd）的CASE WHEN赋值移到SET列表最后，或拆分为多条UPDATE`
- ✗ `update t set stcd=case stcd when 1 then 2 else stcd end, tm=case stcd when 1 then sysdate() else tm end where stcd=1`　✓ `update t set tm=case stcd when 1 then sysdate() else tm end, stcd=case stcd when 1 then 2 else stcd end where stcd=1`

### R110 UserEnv — 禁止使用 USERENV
- 类名 `R110UserEnv`｜ERROR
- description：`TDSQL不支持USERENV()系统上下文函数，系统级参数请从应用侧获取`
- 检测：`\buserenv\s*\(`
- 建议：`USERENV('INSTANCE')/USERENV('SID')等逻辑迁移到应用侧实现`
- ✗ `select userenv('SID') from dual`　✓ 应用侧获取会话信息

### R111 WindowFunction — 分布式不支持窗口函数
- 类名 `R111WindowFunction`｜ERROR
- description：`分布式实例不支持窗口函数（row_number()/rank()等 OVER()），需改写为分组+嵌套查询或应用侧处理`
- 检测：text 匹配 `\bover\s*\(`（配合前导函数特征降误报：`\)\s*over\s*\(` —— 任何 `xxx(...) over(` 形态；`over` 作列名/别名不带 `(` 不命中）
- 建议：`普通查询→分组/嵌套查询+排序改写；复杂嵌套→拆解落中间表，设计排序字段按业务规则更新后再终查；或JDK8 stream在应用侧sort/distinct（注意数据量防OOM）；insert…select…over→先落中间态表再排序分组更新`
- ✗ `select row_number() over(partition by uid order by ts) rn from t`　✓ `select @rn:=@rn+1 …`（应用改写后）

### R112 CursorUsage — 分布式不支持游标
- 类名 `R112CursorUsage`｜ERROR
- description：`TDSQL分布式不支持游标（DECLARE…CURSOR/FETCH），请改用键集翻页或流式查询`
- 检测：text 匹配 `declare\s+\w+\s+cursor\b` 或 `\bfetch\s+\w+\s+into\b`
- 建议：`方案1：WHERE cond AND col>lastval ORDER BY col LIMIT N键集翻页；方案2：JDBC/ODBC流式查询（MyBatis ResultHandler/Cursor+@Transactional，ODBC加NO_CACHE=1）；方案3：分片透传（最低优先，结果有风险）；方案4：TDSQL-PG版`
- ✗ `declare c1 cursor for select * from t`　✓ 键集翻页 SQL

### R113 DropPartitionRisk — 删除分区高并发风险提示
- 类名 `R113DropPartitionRisk`｜INFO
- description：`高并发下DROP PARTITION与路由元数据更新存在毫秒级间隙，小概率报分区不存在；请逐表执行drop+analyze并配置重试`
- 检测：text 匹配 `\bdrop\s+partition\b`
- 建议：`将多表批量drop partition后统一analyze，改为逐表"drop partition→analyze table"循环；查询SQL限定当日时间区间以保证路由分区信息最新；应用连接配置重试机制`
- ✗ `alter table t drop partition p20240101`（提示）　✓ —（本规则仅提示，不阻断）

### R114 DeepPagination — 深分页大偏移
- 类名 `R114DeepPagination`｜WARNING
- description：`LIMIT大偏移分页在分布式实例代价高（proxy聚合各分片），请用索引有序性/键集翻页/条件初筛优化`
- 检测：`parsed.limit_offset > 10000`（阈值常量 `DEEP_PAGE_OFFSET = 10000` 置于规则类）
- 建议：`强排序分页→利用索引有序性避免排序，偏移大时用逼近算法+流式获取；不排序分页→随机/顺序选分片返回单页；均应叠加日期/业务分类等条件缩减初筛量级；最优为键集翻页where col>lastval limit N`
- ✗ `select * from t order by id limit 100000,20`　✓ `select * from t where id>? order by id limit 20`

### R115 PrimaryKeyLength — 主键长度限制（update/delete..limit 兼容）
- 类名 `R115PrimaryKeyLength`｜WARNING｜DDL 自守卫（is_create_table）
- description：`分布式实例update/delete…limit依赖proxy内嵌myisam临时表（索引限1000字节），utf8mb4下主键varchar长度须<250（utf8须<333）`
- 检测：`parsed.is_create_table` 且存在主键列（`parsed.columns` 中 `is_primary_key` 或表级 PRIMARY KEY 对应列——表级主键列名从 `parsed.indexes` 中 type==PRIMARY 取），该列 type 为 VARCHAR/CHAR 且 `length` > 250 → 命中（阈值按最严格的 utf8mb4 口径；charset 可从 `parsed.charset` 获知为 UTF8 时放宽到 333）。
- 建议：`需要使用update/delete…limit语法的表，主键varchar长度限制在250以内(utf8mb4)/333以内(utf8)；proxy 19.6已解决select场景，update/delete…limit仍需遵循`
- ✗ `create table t (id varchar(300) primary key) default charset=utf8mb4`　✓ `create table t (id varchar(64) primary key)`

### R116 ShardKeySingleColumn — 分片键仅支持单字段
- 类名 `R116ShardKeySingleColumn`｜ERROR｜DDL 自守卫
- description：`分片键只支持一个字段，不支持多字段联合分片键`
- 检测：text 匹配 `shardkey\s*=\s*([\w\`,\s]+?)(\s|$|,\s*\w+\s*=)`，取捕获组按逗号切分，段数>1 → 命中。实现建议：`m = re.search(r"shardkey\s*=\s*([^\s,]+(?:\s*,\s*[^\s,]+)*)", text)`，`len(m.group(1).split(","))>1`。
- 建议：`选择最常用于查询过滤/关联的单一字段作为shardkey`
- ✗ `create table t (a int,b int) shardkey=a,b`　✓ `… shardkey=a`

### R117 ShardKeyType — 分片键字段类型限制
- 类名 `R117ShardKeyType`｜ERROR｜DDL 自守卫
- description：`shardkey字段类型必须是int/bigint/smallint/char/varchar`
- 检测：提取 `shardkey\s*=\s*([\w]+)` 单字段名，在 `parsed.columns` 查其 type ∉ {INT,INTEGER,BIGINT,SMALLINT,CHAR,VARCHAR}（含 MEDIUMINT/TINYINT 不在原厂许可清单，同样命中）→ 命中；列不存在/列信息为空则跳过。
- 建议：`shardkey改用int/bigint/smallint/char/varchar类型；另注意：shardkey值不应含中文（proxy不转换字符集，不同字符集可能路由到不同分区）`
- ✗ `create table t (dt datetime, v int) shardkey=dt`　✓ `create table t (id bigint) shardkey=id`

### R118 ShardKeyNotNull — 分片键必须 NOT NULL
- 类名 `R118ShardKeyNotNull`｜ERROR｜DDL 自守卫
- description：`shardkey字段的值不能为NULL，建表时必须显式NOT NULL约束`
- 检测：提取 shardkey 字段名（同 R117），`parsed.columns` 中该列 `is_not_null == False` 且 `is_primary_key == False`（主键隐含 NOT NULL）→ 命中。
- 建议：`shardkey字段加NOT NULL；同时提醒：分片键值不能更新（已有R021约束DML侧）`
- ✗ `create table t (uid bigint, v int) shardkey=uid`（uid 未声明 not null）　✓ `create table t (uid bigint not null, v int) shardkey=uid`

### R119 DateArithmetic — 日期函数直接加减数字
- 类名 `R119DateArithmetic`｜WARNING
- description：`日期值直接±数字（如sysdate()-15）在TDSQL中语义与Oracle不同，请改用DATE_ADD/DATE_SUB(… INTERVAL n DAY)`
- 检测：text 匹配 `(sysdate\s*\(\s*\)|now\s*\(\s*\)|current_timestamp|curdate\s*\(\s*\))\s*[+-]\s*\d`
- 建议：`date_format(sysdate()-15,'%Y%m%d')（错误示范）→date_format(date_add(sysdate(), interval -15 day),'%Y%m%d')；一定不能直接沿用Oracle日期算术，使用前必须查询验证`
- ✗ `select date_format(sysdate()-15,'%Y%m%d')`　✓ `select date_format(date_add(sysdate(), interval -15 day),'%Y%m%d')`

---

## 第 6 章 既有代码增强项（E1–E6）

### E1 解析器识别 Oracle 全局临时表（配合 R024/R032）
- 文件：`backend/engine/parser.py` `_regex_pre_parse`
- 现状：`re.match(r"\bcreate\s+temporary\s+table\b", sql_lower)` 无法命中 Oracle `CREATE GLOBAL TEMPORARY TABLE … ON COMMIT DELETE|PRESERVE ROWS`。
- 修改：正则改为 `\bcreate\s+(global\s+)?temporary\s+table\b`；并追加：`if re.search(r"\bon\s+commit\s+(delete|preserve)\s+rows\b", sql_lower): parsed.is_temporary_table = True`。
- 效果：R024（禁临时表）自动覆盖 Oracle 事务级/会话级临时表两种形态，无需新规则。

### E2 R054 扩展覆盖唯一索引
- 文件：`backend/engine/rules/distributed.py`
- 现状：R054 描述为"分片键字段必须是主键的一部分"。原厂军规为"**唯一索引**必须包含分片键，否则无法创建"。
- 修改：check 逻辑在既有主键校验基础上，增加遍历 `parsed.indexes`/`index_definitions` 中 `type=="UNIQUE"` 的索引：其 `columns` 不含分片键（分片键名取自 table_metadata 的 shard_key，或 DDL 场景从 raw_sql 提取 `shardkey=xxx`）→ 违规。description 更新为：`分片键必须包含在主键及所有唯一索引中（唯一索引不含分片键将无法创建）`。**保持 rule_id=R054 不变**。
- 编码前先读 R054 现有实现再增量修改，勿重写。

### E3 R053 建议文案增强
- 在 R053 的 fix_suggestion 末尾追加原厂优化思路：`关联条件均为分片键且有固定值→完全下推；无固定值→join下推并尽量过滤；小配置表设为广播表使join下推；均不可下推时按日期拆分请求/用单分片中间表落数据后再join；子查询表尽量把过滤条件写入子查询内`。仅改文案，不改检测逻辑。

### E4 R058 建议文案增强
- 在 R058 的 fix_suggestion 末尾追加：`注意：update/delete…limit依赖proxy内嵌myisam临时表，主键varchar长度须<250(utf8mb4)/<333(utf8)，详见R115`。仅改文案。

### E5 后端口径修正
- `backend/config.py`：`APP_VERSION = "2.1.0"`。
- `backend/engine/rules/__init__.py` docstring：更新为 119 条 + 新增分类行 `- Oracle迁移兼容 (ORACLE_COMPAT): R078-R119`。
- `backend/engine/checker.py` `_load_default_rules` 注释"77条"→"119条"。
- `backend/api/rules.py` `list_rules` docstring 中陈旧的"22条"表述改为"全部119条（动态计数）"。
- `backend/models/__init__.py`：`RuleCategory` 新增 `ORACLE_COMPAT = "oracle_compat"`。

### E6 前端适配（必须，否则新分类在规则库页不可见）
- `frontend/static/js/app.js` 第 143 行 `categoryOrder` 数组追加：`{key:'oracle_compat',label:'Oracle迁移兼容'}`（追加到数组末尾）。
- `frontend/index.html` 第 21 行登录页文案：`77条审核规则` → `119条审核规则`。
- 规则库页/规则集编辑器均从 `GET /api/v1/rules` 动态取数，新增分类加入 categoryOrder 后自动展示，无需其他改动。

---

## 第 7 章 全链路生效保障设计

1. **注册即生效**：42 个新类加入 `ALL_RULE_CLASSES`（追加在 R077 之后，按 ID 顺序），2.2 节 5 个入口自动加载。禁止在任何入口写特殊分支。
2. **规则集兼容**：default 规则集空覆盖 → 新规则默认启用；既有自定义规则集未覆盖新 ID → 默认启用（`get_enabled_rules` 现有语义，已核实 checker.py:37-45）。规则集编辑页动态列出 119 条可逐条禁用/降级——集中式项目可借此豁免分布式限制类规则（R092/R100/R111/R112/R116-118）。
3. **门禁联动**：新规则 ERROR 命中自然计入 `max_error_count` 门禁判定（gate 基于 violations 统计，无需改动）。
4. **DDL 前置过滤规避**：oracle_compat 分类不受 checker 的 ddl 过滤影响（见约束 D），7 条 DDL 型新规则自守卫，其余规则天然全类型执行。
5. **验证手段**（编码完成后必须执行）：对 5 个入口各发 1 条 `SELECT nvl(a,0) FROM t` 断言返回 R080 违规（第 9 章用例 T-E2E-01~05）。

---

## 第 8 章 实施步骤与文件清单

**新增文件（1 个）**
| 文件 | 内容 |
|---|---|
| `backend/engine/rules/oracle_compat.py` | `clean_sql` 助手 + `TDSQL_SEQUENCE_KEYWORDS` + 42 个规则类（R078–R119，按第 5 章规格逐条实现） |

**修改文件（8 个）**
| 文件 | 修改点 |
|---|---|
| `backend/models/__init__.py` | RuleCategory 加 ORACLE_COMPAT（E5） |
| `backend/engine/rules/__init__.py` | import 42 类；ALL_RULE_CLASSES/__all__ 追加；docstring 更新（E5） |
| `backend/engine/parser.py` | GTT 识别（E1） |
| `backend/engine/rules/distributed.py` | R054 扩展（E2）、R053/R058 文案（E3/E4） |
| `backend/engine/checker.py` | 注释口径（E5） |
| `backend/api/rules.py` | docstring 口径（E5） |
| `backend/config.py` | APP_VERSION=2.1.0（E5） |
| `frontend/static/js/app.js` + `frontend/index.html` | categoryOrder + 登录页文案（E6） |

**实施顺序**：E5 枚举 → oracle_compat.py（按 R078→R119 顺序）→ 注册 → E1-E4 → E6 前端 → 第 9 章测试 → 文档（README 规则数、docs/ 相关文档口径）→ 提交推送 main。

**提交规范**：单一 feature commit 或按"引擎/前端/测试"拆 2-3 个 commit；消息注明"规则总数77→119"。

---

## 第 9 章 测试设计

### 9.1 新规则单元测试（新文件 `tests/test_oracle_compat_rules.py`）

- 每条规则至少 1 正（命中）+ 1 反（通过）用例，**正例直接采用第 5 章 ✗ 示例（源自原厂文档原文样例），反例采用 ✓ 示例**；42 条 × 2 = 84 个基础用例。
- 额外防误报专项（必须包含）：
  - `SELECT * FROM t WHERE remark = 'use nvl(a,b) here'`（字面量内关键字，全部函数类规则不命中）
  - `-- to_char(x) 注释` / `/* rownum */`（注释内不命中）
  - `SELECT truncate(a,2)`（R086 不误中 TRUNCATE）
  - `SELECT char_length(c)`（R093 不误中）
  - `WHERE a IN (1,2)`、`VALUES (1)`、`EXISTS (SELECT 1…)`（R104 白名单法不误中关键字）
  - `INSERT INTO t(a,b) VALUES(1,2)`（R107 不命中无 SELECT 的 INSERT）
  - `SELECT seq.nextval FROM dual`（R108 dual 豁免）
- 汇总断言：`RuleChecker().get_rules_info()` 长度 == 119；`oracle_compat` 分类恰 42 条；ID R078–R119 连续无缺。

### 9.2 全链路端到端（追加到既有 SIT 测试或新文件）

| 用例 | 入口 | 载荷 | 断言 |
|---|---|---|---|
| T-E2E-01 | POST /api/v1/audit/sql | `{"sql":"SELECT nvl(a,0) FROM t"}` | violations 含 rule_id=R080 |
| T-E2E-02 | POST /api/v1/audit/file | 含 `to_char`/`rownum` 的 .sql 内容 | 对应违规命中 |
| T-E2E-03 | POST /api/v1/audit/upload | 含 `merge into` 的 MyBatis XML | R091 命中 |
| T-E2E-04 | POST /api/v1/gitlab/audit/diff | diff 中含 `decode(` | R081 命中 |
| T-E2E-05 | POST /api/v1/tdsql/audit/with-metadata | `{"sql":"select * from t where rownum<4"}`（无连接时按其错误语义豁免/mock） | R079 命中 |
| T-E2E-06 | 规则集覆盖 | 建规则集禁用 R080 → 绑项目审核 nvl | R080 不出现（覆盖生效） |
| T-E2E-07 | 门禁联动 | 项目门禁 max_error=0，审核 `select nvl(a,0) from t` | gate_result.passed=false |

### 9.3 回归（存量断言必改清单——已逐一定位）

以下 9 处硬编码 77 必须更新为 119（`test_v2_uat.py:183` 的 `>=70` 可不改但建议改 `>=119`）：
```
tests/test_sit_full.py:544        tests/test_sit_v1_rules.py:347,367,371,372
tests/test_uat_rules.py:207       tests/test_uat_v1.py:421,422
tests/test_sit_round2.py:510      tests/test_v2_uat.py:183
```
另需全局 `grep -rn "77" tests/ docs/ README*` 复查漏网口径。**全量回归**：`pytest tests/ -x -q` 全绿（基线 820 通过 + 新增用例）。

### 9.4 性能红线

42 条正则规则串行执行，单条 SQL 审核耗时增量应 < 5ms（clean_sql 每 SQL 至多一次 + LRU）。用 1000 条混合 SQL 的文件审核对比改造前后耗时，劣化 > 20% 需优化（合并正则/预编译 `re.compile` 至模块级常量——**所有正则必须模块级预编译**，此为编码硬要求）。

---

## 第 10 章 验收清单（Definition of Done）

- [ ] `GET /api/v1/rules` 返回 `total=119`，`oracle_compat` 分类 42 条，每条含中文 description/spec_source（含原厂文档章节名）/fix_suggestion。
- [ ] 第 5 章 42 条规则的 ✗ 示例逐条实测命中、✓ 示例逐条实测通过（可写脚本批跑）。
- [ ] 追溯矩阵（第 3 章）52 个规范点逐项核对：新增/映射/增强/备案状态与实现一致。
- [ ] 5 个审核入口 E2E 全部命中新规则（9.2 表）。
- [ ] 规则集禁用/降级对新规则生效；门禁统计计入新规则 ERROR。
- [ ] 前端规则库页显示"Oracle迁移兼容"分类及 42 条规则；登录页文案 119 条。
- [ ] E1：`CREATE GLOBAL TEMPORARY TABLE … ON COMMIT DELETE ROWS` 命中 R024。
- [ ] E2：含唯一索引不含分片键的建表 SQL 命中 R054。
- [ ] 9 处测试断言更新，`pytest tests/` 全绿；性能红线达标。
- [ ] README 与 docs/ 中规则数口径全部更新；APP_VERSION=2.1.0；提交推送 main。

---

## 附录 A 原厂文档全文留档

原厂文档《TDSQL兼容业务系统适配改造方案》V1.5.1 提取文本已随本设计入库：编码智能体如需核对原文语义，以 `docs/vendor/TDSQL兼容业务系统适配改造方案-V1.5.1.txt` 为准（实施第一步将该文件从设计者工作区复制入库，见第 8 章）。

## 附录 B 给编码智能体的三条铁律

1. **regex-first**：Oracle 语法在 mysql 方言下必然 parse_error，任何新规则不得以"解析成功"为前提；AST 仅作增强，用前判空。
2. **防误报优先于查全**：一律经 clean_sql 清洗；函数空格检测只用白名单；拿不准的场景宁可漏检并留注释，不可误报轰炸正常 SQL。
3. **写完必须自证**：每条规则用第 5 章 ✗/✓ 示例当场验证；5 个入口各打一发 nvl 冒烟；9 处 77 断言改完跑全量 pytest——不要"代码写完就算完成"。
