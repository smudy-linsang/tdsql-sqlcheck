# DESIGN-v1.6.3.2 审核规则调整与扫描历史跨页对比详细设计说明书

| 项 | 内容 |
|---|---|
| 文档编号 | DESIGN-v1.6.3.2 Rev.A |
| 目标版本 | v1.6.3.2 |
| 当前代码基线 | v1.6.3.0，`main` 分支，提交 `03ac422` |
| 编写日期 | 2026-09-03 |
| 设计范围 | 6 项审核规则调整、新增 2 条规则、4 个扫描历史页面的跨页选择修复 |
| 文档状态 | 开发定版，可据此进入编码与测试 |
| 本阶段约束 | 只提交本文档，不修改业务代码、测试代码、数据库脚本或版本号 |

---

## 0. 定版结论

本次版本采用以下方案，实施时不得自行扩大或缩小规则口径：

1. 保留 `R011` 规则号，将其收窄为仅检查 `TEXT`，名称为“谨慎使用TEXT大对象字段”，默认级别改为 `INFO`。
2. 新增 `R120`，名称为“禁止滥用LOB大对象字段”，只检查 `BLOB`、`MEDIUMTEXT`、`LONGBLOB`、`MEDIUMBLOB`、`LONGTEXT`，默认级别为 `ERROR`。
3. `R030`、`R032` 的 `instance_scope` 从 `ALL` 改为 `DISTRIBUTED`，规则内容和默认级别不变。
4. `R035` 只比较字段的规范化基础类型，不再比较长度、精度或小数位等括号参数；名称、提示语和修复建议同步去掉“长度必须一致”。
5. `R058` 的上限由 1000 改为 2000，并补齐“实际解析并校验 LIMIT 行数”的能力，不能继续只判断 SQL 文本里是否出现单词 `limit`。
6. 新增 `R121`，名称为“二级分区禁止使用MAXVALUE”，默认级别为 `ERROR`，仅分布式实例适用；覆盖建表和变更分区语句中的二级 `RANGE` 分区定义。
7. 四个扫描历史对比页面统一以快照主键 `id` 作为 `row-key`，开启 Element Plus `reserve-selection`；翻页和同条件刷新保留勾选，查询条件、模块、登录用户等语义上下文变化时清空勾选。
8. 规则总数由 119 条变为 121 条；分布式实例生效 121 条，集中式实例生效 91 条，集中式因适用域跳过 30 条。

本设计不采用 `R011A`、`R011B` 等非现有编号格式。当前规则编号最高为 `R119`，因此两个新增规则顺延为 `R120`、`R121`，可保持 API、规则集、历史结果和测试中 `R\d{3}` 的既有约定。

---

## 1. 需求范围与验收口径

### 1.1 需求追踪矩阵

| 需求编号 | 需求 | 设计落点 | 核心验收口径 |
|---|---|---|---|
| REQ-01 | 拆分 R011：TEXT 提示 | §4.1 | `TEXT` 命中 R011，级别 `INFO`；其他类型不误命中 R011 |
| REQ-02 | 拆分 R011：LOB 禁止 | §4.2 | 指定 5 种类型命中 R120，级别 `ERROR` |
| REQ-03 | R030 仅分布式适用 | §4.3 | 分布式执行、集中式按适用域跳过 |
| REQ-04 | R032 仅分布式适用 | §4.4 | 分布式执行、集中式按适用域跳过 |
| REQ-05 | R035 不再检查长度 | §4.5 | `VARCHAR(32)` 与 `VARCHAR(128)` 视为同类型 |
| REQ-06 | R058 上限调为 2000 | §4.6 | 无 LIMIT、超过 2000、不可静态证明时告警；0～2000 的整数字面量通过 |
| REQ-07 | 二级分区禁用 MAXVALUE | §4.7 | 二级 RANGE 定义命中 R121；注释、字符串、一级分区等不误报 |
| REQ-08 | 四处历史记录跨页选择 | §7 | 第一页选 1 条，第二页再选 1 条，两条均保留并可发起对比 |
| REQ-09 | 版本定为 v1.6.3.2 | §10 | 实施阶段统一更新运行时、发布说明和当前态文档的版本号 |
| REQ-10 | 本轮不动代码 | 全文 | 本设计提交只包含一个 Markdown 文件 |

### 1.2 明确不做

| 编号 | 本期不做 | 说明 |
|---|---|---|
| OUT-01 | 不改变 `R031` 适用域 | 用户只指定 R030、R032；R031 仍按当前规则执行，见 §4.3 风险提示 |
| OUT-02 | 不改变 `R024` 适用域或内容 | R024 与 R032 均涉及临时表，但本次只调整 R032 |
| OUT-03 | 不改变 `R022` 的 1000 条建议 | R022 与 R058 是不同场景；本次 2000 只作用于 R058 |
| OUT-04 | 不把 `TINYTEXT`、`TINYBLOB`、`JSON` 自动加入新规则 | 原需求未包含这些类型，实施不得擅自扩展 |
| OUT-05 | 不新增规则名称数据库列或 API 字段 | 现有前端以 `description` 展示规则名称，继续复用该字段 |
| OUT-06 | 不改扫描对比后端协议和数据库表结构 | 现有 `snapshot_ids` 双 ID 协议及快照主键已满足跨页选择 |
| OUT-07 | 不把选择状态持久化到浏览器或服务端 | 只在当前登录会话、当前页面语义上下文中保留 |
| OUT-08 | 不重写历史版本文档中的“119 条” | 历史记录保持原貌，只更新表示当前能力的文档和测试 |

---

## 2. 术语纠正与依据优先级

### 2.1 需求中的术语归一

| 原始写法 | 定版写法 | 依据与处理 |
|---|---|---|
| `MEDIMTEXT` | `MEDIUMTEXT` | MySQL/TDSQL 兼容数据类型的正式拼写 |
| `MEDIMTBLOB` | `MEDIUMBLOB` | MySQL/TDSQL 兼容数据类型的正式拼写 |
| `MAXVALUES` | `MAXVALUE` | TDSQL 建表语法中的关键字为单数 `MAXVALUE` |
| “规则名称” | `description` 展示值 | 当前模型无独立 `name` 字段，规则页展示 `description` |

以上纠正只消除拼写歧义，不改变业务意图。

### 2.2 依据优先级

本次设计按以下优先级裁决：

1. TDSQL 原厂专家针对本项目给出的治理要求和项目定版需求；
2. 目标 TDSQL MySQL 版的官方语法、限制和目标实例版本实测；
3. MySQL 官方手册，用于补充 TDSQL 声明兼容但腾讯云页面未展开的基础类型或语法细节；
4. 当前仓库的实现约束和兼容性；
5. 第三方文章不作为规则正误依据。

特别说明：`MAXVALUE` 本身是可被数据库语法接受的范围上界，本次 `R121` 是“本项目禁止使用”的治理规则，不应在提示语中错误描述为“TDSQL 语法不支持”。同理，2000 是本项目经原厂沟通后的治理阈值，不是腾讯云公开文档声明的通用硬上限。

### 2.3 官方资料

| 资料 | 本设计使用的事实 |
|---|---|
| [TDSQL MySQL版使用限制](https://cloud.tencent.com/document/product/557/47511) | 分布式架构不支持存储过程、触发器、自定义函数，不推荐视图，不支持 `CREATE TEMPORARY TABLE`；全局 `UPDATE/DELETE ... LIMIT` 在 1.14.4 及以上版本支持 |
| [TDSQL MySQL版建表](https://cloud.tencent.com/document/product/557/8767) | 一级分区与二级 `PARTITION BY RANGE/LIST` 的语法边界；RANGE 定义中正式关键字为 `MAXVALUE` |
| [TDSQL MySQL版二级分区](https://cloud.tencent.com/document/product/557/58907) | 二级分区的 RANGE/LIST 定义与使用方式；具体运维策略以本项目原厂定版为准 |
| [TDSQL MySQL版保留关键字](https://cloud.tencent.com/document/product/557/94204) | `MAXVALUE`、`TDSQL_DISTRIBUTED` 等关键字的正式拼写 |
| [MySQL 8.0 RANGE Partitioning](https://dev.mysql.com/doc/refman/8.0/en/partitioning-range.html) | `MAXVALUE` 在 `VALUES LESS THAN` 中作为最大上界的语义及常见书写形式 |
| [MySQL 8.0 Glossary](https://dev.mysql.com/doc/refman/8.0/en/glossary.html) | TEXT 家族与 BLOB 家族的正式类型名 |
| [Element Plus Table](https://element-plus.org/zh-CN/component/table.html) | `reserve-selection` 用于数据刷新后保留选择，且必须配置稳定的 `row-key` |

---

## 3. 当前实现勘查

> 下列行号锚定提交 `03ac422`。实施前如 `main` 已前进，应以类名、函数名和模板片段重新定位，不能机械套用旧行号。

### 3.1 规则与解析器现状

| 事实 | 代码位置 | 影响 |
|---|---|---|
| R011 当前把 9 种类型放在一个集合内，级别为 WARNING | `backend/engine/rules/ddl.py:209-231` | 必须收窄 R011 并新增 R120，不能只改提示语 |
| R030/R032 未声明适用域，继承 `ALL` | `backend/engine/rules/ddl.py:389-440` | 增加 `InstanceScope.DISTRIBUTED` 即可接入统一过滤 |
| R035 比较 `raw_type`，因此长度不同会报错 | `backend/engine/rules/ddl.py:488-510` | 要改用规范化基础类型，不得删字符串中的数字后比较 |
| R058 只用 `"limit" in raw_sql.lower()` 判断 | `backend/engine/rules/distributed.py:587-619` | 目前 `LIMIT 999999`、注释里的 `limit` 都会被错误放行 |
| `ParsedSQL` 只有 `limit_offset`，没有 LIMIT 行数 | `backend/engine/parser/parser_legacy.py:2146-2203` | 需要新增结构化 LIMIT 结果 |
| 二级分区状态机遇到 bare `MAXVALUE` 直接失败 | `backend/engine/parser/parser_legacy.py:1026-1042` | 新规则前必须先让解析器识别并保真传递该语法 |
| 默认适用域为 `ALL` | `backend/engine/rules/base.py:24-27` | 新增 R121、调整 R030/R032 时必须显式声明分布式 |
| 规则 API 从类元数据动态返回 | `backend/engine/checker.py:72-97` | 规则名称继续放 `description`，无需扩 API |
| 规则总表当前为 R001～R119 | `backend/engine/rules/__init__.py` | 新增 R120、R121 后总数为 121 |

### 3.2 元数据与规则目录现状

`rule_configs` 表保存内置规则的类别、级别、描述、规范来源和修复建议。启动初始化使用 `INSERT IGNORE`：

- 新规则可以被补插；
- 已存在的 R011、R035、R058 行不会自动刷新；
- 如果不做迁移，运行时规则 API 和数据库规则目录会出现两套文案/级别。

因此 v1.6.3.2 必须使用新的迁移槽位 `backend/schema/v14/140_rule_catalog_v1632.sql` 同步内置元数据。该迁移只改内置元数据，不覆盖管理员的规则集启停和严重度覆盖。

### 3.3 R035 的上下文限制

当前 R035 只有收到 `table_metadata["existing_columns"]` 时才会执行，但仓库主要文件审核和在线元数据审核路径均未构造这份跨表字段上下文。因此：

- “删除长度判断”不能只把 `raw_type` 换成一次正则截断；
- 实施应在批量 DDL 审核入口构造一次跨表字段索引，使 R035 在至少包含两张表定义的文件审核及在线元数据审核中可验证；
- 单条离线 SQL 没有第二张表或实时库上下文时，R035 应明确跳过，不能猜测；
- 本次不引入“字段语义词典”，仍以规范化后的同名字段代表“相同业务含义”。

### 3.4 扫描历史跨页选择现状

四个页面复用同一份 `cmpState` 和 `onSnapshotSelect`：

| 页面 | module | 表格位置 |
|---|---|---|
| SQL审核 → 在线元数据审核 → 扫描对比 | `schema_audit` | `frontend/index.html:492` |
| 慢SQL治理 → 扫描任务 → 扫描历史对比 | `slow_scan` | `frontend/index.html:714` |
| 实例检查 → 上线检查 → 扫描历史对比 | `launch_check` | `frontend/index.html:1279` |
| 实例检查 → 大表治理 → 扫描历史对比 | `bigtable` | `frontend/index.html:1514` |

当前事实：

- 四张 `el-table` 都没有 `row-key`；
- 四个 `type="selection"` 列都没有 `reserve-selection`；
- 翻页调用 `loadSnapshots()`，会用新页数据替换 `cmpState.list`；
- Element Plus 无法识别“上一页的行”和“当前页的行”是同一选择集合，故触发 `selection-change` 后上一页选择丢失；
- `scan_snapshots.id` 是 `BIGINT PRIMARY KEY AUTO_INCREMENT`，且列表 API 始终返回 `id`，可以作为稳定且全局唯一的行键；
- 后端对比接口已经接收两个 `snapshot_ids`，不需要修改接口或数据表。

---

## 4. 审核规则详细设计

### 4.1 R011：谨慎使用TEXT大对象字段

#### 4.1.1 元数据

| 属性 | 定版值 |
|---|---|
| rule_id | `R011` |
| description（展示名称） | `谨慎使用TEXT大对象字段` |
| category | `DDL` |
| severity | `INFO` |
| instance_scope | `ALL` |
| enabled | `true` |
| spec_source | `TDSQL数据库开发规范 - 列设计规范（v1.6.3.2原厂专家定版）` |
| fix_suggestion | `建议将大对象字段拆分到独立扩展表，或根据实际容量改用 VARCHAR(n) 并明确长度上限` |

#### 4.1.2 命中条件

所有条件同时满足时命中：

1. SQL 是 `CREATE TABLE`，或是 `ALTER TABLE` 中的 `ADD/MODIFY/CHANGE COLUMN`；
2. 结构化列定义的规范化基础类型严格等于 `TEXT`；
3. 不是注释、字符串、列注释或类型转换表达式中的文本。

本规则不命中 `TINYTEXT`、`MEDIUMTEXT`、`LONGTEXT`、任何 BLOB 类型和 `JSON`。这是定版边界，不使用 `endsWith("TEXT")`、`contains("TEXT")` 或 `raw_type.startswith(...)` 等扩大匹配。

#### 4.1.3 提示模板

| 用途 | 模板 |
|---|---|
| 控制说明 | `字段 {column_list} 使用了 TEXT 数据类型，可能影响 TDSQL 的读写与存储性能。` |
| 修复建议 | `建议将大对象字段拆分到独立扩展表，或根据实际容量改用 VARCHAR(n) 并明确长度上限。` |

同一条 SQL 有多个 TEXT 字段时，生成一条 R011 结果并按列定义顺序列出全部字段，避免同一规则对刷屏。

#### 4.1.4 示例

| SQL 片段 | 结果 |
|---|---|
| `body TEXT` | R011 / INFO |
| `body text COMMENT '正文'` | R011 / INFO |
| `body VARCHAR(2000)` | 不命中 R011 |
| `body MEDIUMTEXT` | 不命中 R011，改由 R120 命中 |
| `body TINYTEXT` | 不命中本次两条拆分规则 |
| `remark VARCHAR(50) COMMENT 'TEXT'` | 不命中 |

### 4.2 R120：禁止滥用LOB大对象字段

#### 4.2.1 元数据

| 属性 | 定版值 |
|---|---|
| rule_id | `R120` |
| description（展示名称） | `禁止滥用LOB大对象字段` |
| category | `DDL` |
| severity | `ERROR` |
| instance_scope | `ALL` |
| enabled | `true` |
| spec_source | `TDSQL数据库开发规范 - 列设计规范（v1.6.3.2原厂专家定版）` |
| fix_suggestion | `不要使用上述大对象类型；非结构化数据建议对接影像平台或对象存储` |

#### 4.2.2 命中集合

规范化后只匹配以下 5 个基础类型：

```text
BLOB
MEDIUMTEXT
LONGBLOB
MEDIUMBLOB
LONGTEXT
```

匹配大小写不敏感，但必须是完整类型 token。不得把 `TINYTEXT`、`TINYBLOB`、`JSON`、列名 `blob_url` 或字符串 `'LONGTEXT'` 纳入。

#### 4.2.3 提示模板

| 用途 | 模板 |
|---|---|
| 控制说明 | `字段 {column_and_type_list} 使用了受限 LOB 大对象类型；这些类型在 TDSQL 上会大幅降低性能，禁止使用。` |
| 修复建议 | `请不要使用上述大对象类型；非结构化数据建议对接影像平台或对象存储。` |

同一 SQL 中多个受限字段聚合为一条 R120 结果，字段显示格式为 `字段名(类型)`。

#### 4.2.4 与其他规则关系

- 与 R011 可以在同一建表语句同时出现：一个 TEXT 字段产生 INFO，一个 BLOB 字段产生 ERROR；两者不互相吞并。
- R066 的“BLOB/TEXT 不应建索引”仍保留，它约束索引设计，不因 R120 新增而删除。
- 存量 R011 对 `TINYTEXT`、`TINYBLOB`、`JSON` 的覆盖会在本次拆分后消失，这是按明确类型清单执行的有意变化，须在 UAT 记录中确认。

### 4.3 R030：仅分布式适用

| 属性 | 定版 |
|---|---|
| rule_id | `R030` |
| 变化 | `instance_scope: ALL → DISTRIBUTED` |
| description | 保持 `禁止使用视图、存储过程、触发器、自定义函数` |
| severity | 保持 `ERROR` |
| 检测逻辑 | 本期不改 |

腾讯云当前 TDSQL MySQL 版使用限制明确把自定义函数、存储过程、触发器列为分布式限制，并说明视图能力受限且不建议使用，因此适用域调整有官方依据。

兼容性提示：R031 也检查自定义函数，且本次未要求把 R031 改为仅分布式。因此集中式实例上 R030 会被跳过，但 R031 仍可能阻止 `CREATE FUNCTION`。这是当前定版的明确边界；若业务目标是“集中式允许自定义函数”，必须另行评审 R031，实施人员不得借本次需求顺手修改。

### 4.4 R032：仅分布式适用

| 属性 | 定版 |
|---|---|
| rule_id | `R032` |
| 变化 | `instance_scope: ALL → DISTRIBUTED` |
| description | 保持 `禁止使用临时表进行复杂业务逻辑` |
| severity | 保持 `ERROR` |
| 检测逻辑 | 保持读取 `parsed.is_temporary_table` |

TDSQL 分布式官方限制明确列出不支持 `CREATE TEMPORARY TABLE`。R024 已是仅分布式的临时表规则，本次不合并规则号，不改变历史结果兼容性。

### 4.5 R035：跨表关联字段类型必须一致

#### 4.5.1 元数据与模板

| 属性 | 定版值 |
|---|---|
| rule_id | `R035` |
| description（展示名称） | `跨表关联字段类型必须一致` |
| category | `DDL` |
| severity | `ERROR` |
| instance_scope | `ALL` |
| fix_suggestion | `请统一关联字段的数据类型；字段长度可按各表实际容量分别设置` |

| 用途 | 模板 |
|---|---|
| 控制说明 | `关联字段 {column} 在表 {current_table} 中的类型为 {current_type}，与表 {reference_table} 中的类型 {reference_type} 不一致。` |
| 修复建议 | `请统一关联字段 {column} 的数据类型；字段长度可按各表实际容量分别设置。` |

所有文案都不得再出现“长度必须一致”“统一类型和长度”等旧口径。

#### 4.5.2 类型比较口径

比较值必须来自解析器的规范化基础类型，而不是 SQL 原文：

| 当前定义 | 参考定义 | 比较结果 |
|---|---|---|
| `VARCHAR(32)` | `VARCHAR(128)` | 一致 |
| `CHAR(8)` | `CHAR(32)` | 一致 |
| `DECIMAL(10,2)` | `DECIMAL(18,4)` | 一致 |
| `DATETIME(3)` | `DATETIME(6)` | 一致 |
| `INT(11)` | `INT` | 一致 |
| `INTEGER` | `INT` | 按解析器别名归一后一致 |
| `INT UNSIGNED` | `INT` | 不一致；有符号性影响值域，不作为“长度”忽略 |
| `VARCHAR(32)` | `CHAR(32)` | 不一致 |
| `TEXT` | `MEDIUMTEXT` | 不一致 |

设计原则：所有括号参数均不参与 R035，包括字符长度、整数显示宽度、十进制精度/小数位和时间小数秒精度；基础类型及解析器保留的值域修饰（如 `UNSIGNED`）参与比较。

禁止采用如下实现：

- 用正则直接删除原 SQL 中所有括号，可能误伤表达式或约束；
- 只取 `raw_type.split("(")[0]`，会丢失 `UNSIGNED` 等值域语义；
- 用包含关系比较类型名，例如把 `INT` 和 `BIGINT` 判为相同。

#### 4.5.3 跨表上下文

批量审核在执行单条规则前构造只读索引：

```text
normalized_column_name -> [
  {table_name, base_type, raw_type, statement_index}
]
```

约束如下：

1. 列名按现有 MySQL 标识符大小写策略归一，优先保留原名用于展示；
2. 只比较不同表的同名字段，同一表内部不做跨表规则；
3. 一组同名字段存在两个以上基础类型时，相关语句均可得到确定的冲突说明；
4. 结果排序按语句顺序、列定义顺序、参考表名稳定排序，避免测试和报告漂移；
5. 在线元数据审核把全部 `SHOW CREATE TABLE` 结果合并后走同一索引构造器；
6. 只有一条离线 SQL 且没有元数据时跳过 R035；跳过不产生伪造违规；
7. 若增强元数据入口从 `information_schema` 取类型，使用 `DATA_TYPE` 作为基础类型，`COLUMN_TYPE` 只用于展示。

### 4.6 R058：批量 UPDATE/DELETE LIMIT 不超过 2000

#### 4.6.1 元数据与模板

| 属性 | 定版值 |
|---|---|
| rule_id | `R058` |
| description | `分布式表批量UPDATE/DELETE建议加LIMIT限制单次影响行数(≤2000)` |
| category | `DISTRIBUTED` |
| severity | `WARNING` |
| instance_scope | `DISTRIBUTED` |
| fix_suggestion | `请将单次 UPDATE/DELETE 控制为 LIMIT 2000 或更小，并按稳定主键分批提交；同时遵守 R115 对相关主键长度的限制` |

| 场景 | 控制说明模板 |
|---|---|
| 缺少 LIMIT | `分布式表批量 {operation} 未设置 LIMIT，可能导致长事务和锁等待；单次影响行数应不超过 2000。` |
| 超过上限 | `分布式表批量 {operation} 的 LIMIT 为 {limit_count}，超过 2000。` |
| 无法静态确定 | `分布式表批量 {operation} 的 LIMIT 无法在审核阶段确定，不能证明单次影响行数不超过 2000。` |

#### 4.6.2 结构化解析模型

在 `ParsedSQL` 中新增一个专用结构，语义上至少包含：

```text
dml_limit.present       是否存在真实 LIMIT 子句
dml_limit.row_count     非负整数字面量时的行数，否则为空
dml_limit.offset        存在 offset 形态时的偏移量，否则为空
dml_limit.parameterized 是否为 ?、:name 等占位符
dml_limit.verifiable    是否能静态证明 row_count
```

优先从 SQLGlot AST 的 `Limit.expression` 读取；只有 TDSQL 方言恢复路径无法产生可靠 AST 时，才允许使用现有词法器 token 进行有限回退。回退必须忽略注释和字符串，并验证 LIMIT token 位于 UPDATE/DELETE 顶层语法位置。不得使用全文正则或字符串包含判断作为放行依据。

#### 4.6.3 判定表

前置条件保持为：分布式实例、`UPDATE/DELETE`、存在 WHERE、元数据确认至少一张目标表为分片表。

| LIMIT 形态 | R058 结果 | 说明 |
|---|---|---|
| 无 LIMIT | WARNING | 现有控制要求 |
| `LIMIT 0` | 通过 | 0 不超过 2000；是否有业务意义不属于本规则 |
| `LIMIT 1` | 通过 | 边界内 |
| `LIMIT 2000` | 通过 | 上边界闭区间 |
| `LIMIT 2001` | WARNING | 超限 |
| `LIMIT 999999` | WARNING | 超限，修复当前错误放行 |
| `LIMIT ?` / `LIMIT :n` | WARNING | 无法静态证明运行值 |
| `LIMIT 1, 2000` | WARNING | UPDATE/DELETE 的非标准/不可证明形态不作为合规证据 |
| `LIMIT 2000 OFFSET 1` | WARNING | 同上 |
| 注释 `/* limit 10 */` | 等同无 LIMIT | 防误报 |
| 字符串 `remark='limit 10'` | 等同无 LIMIT | 防误报 |

官方资料表明全局 `UPDATE/DELETE ... LIMIT` 需要 SQL 引擎版本不低于 1.14.4。发布前必须确认所有纳管分布式实例满足该前提；若存在低版本实例，不能让 R058 给出目标环境无法执行的修复建议，应先完成版本升级或由 DBA 另行定版分批方案。

### 4.7 R121：二级分区禁止使用MAXVALUE

#### 4.7.1 元数据

| 属性 | 定版值 |
|---|---|
| rule_id | `R121` |
| description（展示名称） | `二级分区禁止使用MAXVALUE` |
| category | `DISTRIBUTED` |
| severity | `ERROR` |
| instance_scope | `DISTRIBUTED` |
| enabled | `true` |
| spec_source | `TDSQL数据库开发规范 - 二级分区规范（v1.6.3.2原厂专家定版）` |
| fix_suggestion | `请删除 MAXVALUE 兜底分区，由业务按规划提前创建并持续维护明确上界的二级分区` |

| 用途 | 模板 |
|---|---|
| 控制说明 | `二级 RANGE 分区 {partition_list} 使用了 MAXVALUE 兜底边界；本项目禁止该设计，二级分区必须由业务按明确边界维护。` |
| 修复建议 | `请删除 MAXVALUE 兜底分区，由业务按规划提前创建并持续维护明确上界的二级分区。` |

#### 4.7.2 覆盖语句

1. `CREATE TABLE ... [SHARDKEY/TDSQL_DISTRIBUTED ...] PARTITION BY RANGE (...) (...)` 中的二级分区定义；
2. `ALTER TABLE ... ADD PARTITION (...)` 中新增的 RANGE 分区定义；
3. `ALTER TABLE ... REORGANIZE PARTITION ... INTO (...)` 中重组后的 RANGE 分区定义；
4. `SHOW CREATE TABLE` 常见的可执行版本注释 `/*!... PARTITION BY RANGE ... */`，解析器应按现有可执行注释通道识别。

建表时，`PARTITION BY RANGE` 是 TDSQL 一级分片规则之后的二级分区子句；仅 `TDSQL_DISTRIBUTED BY RANGE` 的一级分区定义不属于本规则。

#### 4.7.3 语法识别

必须扩展现有二级分区 token 状态机，并将结果写入结构化字段，例如：

```text
secondary_partition.has_definition
secondary_partition.method
secondary_partition.maxvalue_partitions
secondary_partition.source_context  # CREATE / ALTER_ADD / ALTER_REORGANIZE
```

识别步骤：

1. 词法器先剥离普通注释和字符串语义，保留官方可执行版本注释；
2. 确认当前语法原子是二级 `PARTITION BY RANGE` 或 ALTER 的分区定义；
3. 只在 `VALUES LESS THAN` 的边界位置接受 `MAXVALUE`；
4. 同时接受官方语法展示的 `VALUES LESS THAN MAXVALUE` 和 MySQL 兼容常见形态 `VALUES LESS THAN (MAXVALUE)`；
5. 记录命中的真实分区名，供提示模板使用；
6. `LIST ... VALUES IN (...)` 不允许把 MAXVALUE 当普通值；非法语法继续由 `E999_SYNTAX_ERROR` 兜底；
7. 不把 `TDSQL_MAXVALUE` 序列关键字、普通标识符、注释或字符串误判为分区上界。

#### 4.7.4 正反例

| 片段 | 实例类型 | 结果 |
|---|---|---|
| 二级 `PARTITION pmax VALUES LESS THAN MAXVALUE` | distributed | R121 / ERROR |
| 二级 `PARTITION pmax VALUES LESS THAN (MAXVALUE)` | distributed | R121 / ERROR |
| 二级 `PARTITION p202701 VALUES LESS THAN (202702)` | distributed | 通过 R121 |
| 一级 `TDSQL_DISTRIBUTED BY RANGE(id) (... MAXVALUE ...)`，无二级子句 | distributed | 不命中 R121 |
| 同一 SQL 一级含 MAXVALUE、二级不含 | distributed | 不命中 R121 |
| 二级含 MAXVALUE | centralized | 按适用域跳过 R121 |
| `COMMENT 'MAXVALUE'` | distributed | 不命中 |
| `CREATE TDSQL_SEQUENCE ... TDSQL_MAXVALUE 100` | distributed | 不命中 |
| `LIST ... VALUES IN ('MAXVALUE')` | distributed | 不命中 R121 |

---

## 5. 解析器设计

### 5.1 禁止“全文正则即语义”的三类场景

以下三项都必须使用结构化解析：

| 场景 | 结构化真值源 | 正则风险 |
|---|---|---|
| TEXT/LOB 类型 | AST/列定义的规范化 `DataType` | 列名、注释、默认值、字符串误报 |
| UPDATE/DELETE LIMIT | AST 顶层 `Limit` 节点 | `remark='limit 10'` 或注释错误放行 |
| 二级分区 MAXVALUE | TDSQL token 状态机的分区值节点 | 一级分区、序列、注释、字符串误报 |

正则只可用于非语义性的展示清理或当前已有、已被测试证明安全的语句头辅助，不得成为上述规则“通过”的唯一依据。

### 5.2 DDL 列定义统一通道

为避免 R011/R120 只检查 CREATE 而被 ALTER 绕过，解析器应提供统一的只读列定义集合：

```text
ddl_column_types = [
  {name, type, raw_type, length, operation}
]
```

`operation` 至少区分 `CREATE`、`ADD`、`MODIFY`、`CHANGE`。CREATE 复用当前 `columns/column_types` 解析结果；ALTER 从 SQLGlot `Alter.actions` 中的 `ColumnDef`/`ModifyColumn` 提取。解析失败时不做正则猜测，由 E999 失败关闭。

兼容要求：保留现有 `columns`、`column_types` 字段供其他规则使用，本期新增通道只供 R011/R120 及后续明确迁移的规则消费，避免无评审改变其他规则行为。

### 5.3 LIMIT 结构

- AST 正常路径：从顶层 UPDATE/DELETE 节点读取 `limit`，确认 `expression` 是否为非负整数字面量；
- 占位符路径：识别 `Placeholder`，设置 `parameterized=true`、`verifiable=false`；
- offset 路径：保留 offset，但 R058 不把该形态作为合规证明；
- TDSQL 恢复路径：使用词法 token 扫描顶层 LIMIT，不进入括号、字符串、普通注释；
- 超大整数字面量必须安全转换，转换溢出或非法值视为不可证明，不能让审核异常；
- 解析器不得把 SELECT 子查询内部的 LIMIT 误当成外层 UPDATE/DELETE 的批量上限。

### 5.4 二级分区结构

现有 `_consume_partition_values`、`_consume_partition_defs`、`_consume_secondary_partition` 已形成小型语法状态机，应增量扩展而非另写并行正则：

1. RANGE 值解析新增 `LESS_THAN_MAXVALUE` 指纹；
2. 定义解析把命中分区名汇总到结构化结果；
3. CREATE tail 恢复计划和可执行注释路径都把该结果回填到 `ParsedSQL`；
4. 新增 ALTER ADD/REORGANIZE 的有限状态扫描，只接受官方分区定义形态；
5. 保留原有完整性门禁：未知或半解析语法不能伪装成成功；
6. 更新当前将 MAXVALUE 标成已知假阴性的注释和测试，避免文档与实际能力相反。

---

## 6. 规则注册、规则集与数据迁移

### 6.1 注册顺序

`ALL_RULE_CLASSES` 按规则号保持稳定顺序：

- R011 仍位于原位置；
- R120、R121 追加在 R119 之后；
- `__all__`、模块导入、文档头计数同步更新；
- 不因 R120 属于 DDL 就把它插到列表中间，避免依赖结果顺序的存量测试和报告发生大面积漂移。

### 6.2 数量口径

| 口径 | v1.6.3.0 | v1.6.3.2 |
|---|---:|---:|
| 规则总数 | 119 | 121 |
| DDL 分类 | 22 | 23 |
| DISTRIBUTED 分类 | 14 | 15 |
| 其他分类 | 不变 | 不变 |
| 仅分布式规则 | 27 | 30 |
| 分布式实例实际生效 | 119 | 121 |
| 集中式实例实际生效 | 92 | 91 |
| 集中式按适用域跳过 | 27 | 30 |

计算说明：R120 是通用规则，新增后集中式先增加 1；R030、R032 改为仅分布式又减少 2，因此集中式从 92 变为 91。R121 仅分布式，不增加集中式数量。

### 6.3 存量规则集行为

1. R011 的启停和严重度覆盖继续沿用原规则号，因此存量规则集配置不丢失；如某规则集显式把 R011 覆盖为 WARNING/ERROR，该覆盖仍高于新的默认 INFO。
2. R120、R121 是新规则，未配置覆盖时按默认 `enabled=true` 和默认严重度生效。
3. R030、R032 即使在规则集中被设置为启用，也不能绕过 `instance_scope` 在集中式实例上执行；适用域过滤仍先于规则集过滤。
4. 发布前由 DBA/管理员审阅所有活动规则集，明确是否接受两条新规则默认开启，避免上线后门禁结果非预期变化。

### 6.4 v14 迁移

新增 `backend/schema/v14/140_rule_catalog_v1632.sql`，职责仅为：

- 更新 R011 的 `severity`、`description`、`spec_source`、`fix_suggestion`；
- 更新 R035 的 `description`、`fix_suggestion`；
- 更新 R058 的 `description`、`fix_suggestion`；
- 插入或刷新 R120、R121 的内置元数据；
- 不修改 R011/R035/R058 的 `enabled`；
- 不修改规则集覆盖表；
- 不新增 `instance_scope` 数据库列，因为适用域的唯一真值源仍是规则类和动态 API。

迁移需幂等。对新规则使用 upsert 时，已存在行只刷新内置元数据，不覆盖管理员状态。迁移文件发布后受 checksum 管理，不得回改；后续订正使用新槽位。

---

## 7. 扫描历史跨页选择详细设计

### 7.1 根因

当前 `loadSnapshots()` 每次翻页都会把 `cmpState.list` 替换为当前页的新对象数组。未设置 `row-key` 时，Element Plus 只能以当前对象身份维护选择；未设置 `reserve-selection` 时，数据刷新后也不会保留离开当前页的行。因此第一页选择在第二页选择事件发生时被覆盖。

### 7.2 模板修改

四张表做完全一致的两项配置：

```text
el-table: row-key="id"
el-table-column type="selection": reserve-selection
```

不能使用数组下标、页内序号、`biz_ref_id` 或 `scan_label` 作为行键：

- 数组下标翻页后重复；
- 页内序号不稳定；
- `biz_ref_id` 只在 `(module, biz_ref_id)` 范围唯一；
- `scan_label` 是展示字段，可重复；
- `scan_snapshots.id` 是全表主键，符合稳定身份要求。

### 7.3 选择状态机

选择集合按快照 ID 建模，而不是依赖当前页对象：

```text
EMPTY --勾选 A--> ONE(A)
ONE(A) --翻页/刷新--> ONE(A)
ONE(A) --勾选兼容 B--> TWO(A,B)
ONE(A) --勾选不兼容 B--> ONE(A) + 提示
TWO(A,B) --勾选 C--> TWO(A,B) + 提示
TWO(A,B) --取消 A/B--> ONE(剩余项)
任意状态 --语义上下文变化--> EMPTY
```

“兼容”继续沿用现有前端和后端约束：两条记录必须属于同一实例、同一数据源/模块；后端仍是最终校验者。

### 7.4 保留与清空边界

| 操作 | 是否保留选择 | 原因 |
|---|---|---|
| 页码变化 | 保留 | 本需求核心场景 |
| 同查询条件下点击“刷新” | 保留 | 数据刷新不等于用户放弃选择 |
| 返回上一页 | 保留并恢复勾选 | 便于用户核对 |
| 点击“查询” | 清空 | 查询条件定义了新的候选集合 |
| 点击“重置” | 清空 | 语义上下文变化 |
| 切换四个 module | 清空 | 不允许跨模块比较 |
| 切换实例/数据库/数据源/日期条件并执行查询 | 清空 | 防止隐藏选中项与当前筛选不一致 |
| 成功删除已选快照 | 清空 | 已选实体失效 |
| 成功删除未选快照 | 保留 | 选择实体仍有效 |
| 退出登录/切换用户 | 清空 | 防止跨用户残留 UI 状态 |
| 浏览器整页刷新 | 清空 | 本期不做持久化 |

`cmpQuery()` 当前只写 `cmpState.selected=[]`。实施时新增统一 `clearCompareSelection()`：同时更新业务状态，并在 `nextTick` 后调用当前表格实例的 `clearSelection()`，防止 Element Plus 内部保留键与业务数组不一致。

### 7.5 超选和不兼容选择

Element Plus 开启保留选择后，`selection-change` 返回跨页已选集合。现有“最多两条”与“同实例/同数据源”校验保留，但调整为按 ID 判断本次新增项：

1. 从新 rows 与旧 selected 的 ID 差集找出 `addedRow`；
2. 超过 2 条时只取消 `addedRow`；
3. 两条不兼容时只取消 `addedRow`；
4. 取消动作后等待下一次 selection-change 收敛，不能先把错误 rows 写回 `cmpState.selected`；
5. 如果差集不可唯一确定，失败关闭为保留旧的合法选择集合，并重建当前表格勾选状态；
6. 比较按钮只在业务集合恰好两条时可执行。

这样即使第一条记录不在当前页，也不会因为 `toggleRowSelection` 只能操作当前页对象而误删第一条。

### 7.6 并发加载保护

跨页选择依赖“当前页与当前查询上下文”一致。为防止用户快速翻页造成旧请求晚返回覆盖新页，`loadSnapshots()` 增加单调递增的请求序号：

1. 发请求前记录 `requestId` 和当时的 module/page/filter 快照；
2. 响应返回时只接受仍为最新的请求；
3. 过期响应直接丢弃，不更新 `list/total/loading`；
4. loading 只由最新请求关闭。

这不是后端改造，不改变接口；它避免修复跨页选择后仍出现“页码是 2、内容却回到第 1 页”的偶发状态。

### 7.7 后端不变项

- `GET /api/v1/scan-compare/snapshots` 继续分页返回快照，其中 `id` 必须保留；
- `POST /api/v1/scan-compare/compare` 继续接收 `module` 和两个 `snapshot_ids`；
- 下载、保存报告、删除快照接口不改；
- 后端继续验证记录存在、module、实例、数据源和规则尺度兼容性；
- 不增加服务端“临时选择集”表，不产生额外清理任务。

---

## 8. API 与兼容性

### 8.1 规则列表 API

`GET /api/v1/rules` 的字段结构不变，仅内容变化：

- `total=121`；
- R011 的 description/severity 更新；
- 新增 R120、R121；
- R030、R032 的 `instance_scope=distributed`；
- R035、R058 的 description/fix_suggestion 更新；
- 带 `instance_type=distributed` 时 `effective_total=121`、`skipped_total=0`；
- 带 `instance_type=centralized` 时 `effective_total=91`、`skipped_total=30`。

无需增加 `name` 字段。若未来产品要同时展示“短名称”和“长描述”，应另行做 API 版本设计，不能在本次静默改变字段含义。

### 8.2 审核结果 API

响应结构不变。新旧客户端都继续读取：

```text
rule_id / category / severity / message / suggestion / line_number
```

历史审核结果不回算：历史中的 R011 WARNING 及旧文案保持原样；v1.6.3.2 后的新审核按新规则产生结果。报表需要以生成时间和规则集尺度解释差异。

### 8.3 扫描对比 API

无协议变化。前端提交的仍是两个全局快照 ID。选择发生在哪一页不进入后端协议，因此不需要新增 page/token 字段。

---

## 9. 实施文件清单

以下是编码阶段的预期修改面。实际实现如需新增文件，必须说明原因并补测试，不得无评审扩大到业务无关模块。

### 9.1 后端与迁移

| 文件 | 设计改动 |
|---|---|
| `backend/engine/parser/parser_legacy.py` | 新增统一 DDL 列定义通道、结构化 DML LIMIT、MAXVALUE 二级分区识别及 ParsedSQL 字段 |
| `backend/engine/rules/ddl.py` | 重写 R011、调整 R030/R032/R035、新增 R120 |
| `backend/engine/rules/distributed.py` | R058 阈值及数值判定；新增 R121 |
| `backend/engine/rules/__init__.py` | 注册 R120/R121、更新总数和分类说明 |
| `backend/engine/checker.py` | 批量 DDL 的跨表类型上下文；更新动态计数注释 |
| `backend/services/audit_service.py` | 确保文件/在线元数据入口使用统一跨表上下文；不得重复解析或绕过规则集/适用域 |
| `backend/services/database.py` | 把 `init_rule_configs` 陈旧固定计数说明改为动态描述；不改变管理员状态 |
| `backend/schema/v14/140_rule_catalog_v1632.sql` | 同步存量规则目录元数据和新增规则 |

如实现选择把 R121 放到独立规则文件，仍必须保持 category、注册顺序和测试口径不变。

### 9.2 前端

| 文件 | 设计改动 |
|---|---|
| `frontend/index.html` | 四张历史对比表增加 `row-key="id"` 和 `reserve-selection` |
| `frontend/static/js/app.js` | 选择集合按 ID 管理、统一清空函数、超选回滚、请求序号保护、删除/退出清理 |

### 9.3 测试与当前态文档

| 文件/类型 | 设计改动 |
|---|---|
| `tests/test_rules.py` 或规则专项新文件 | R011/R120/R035/R058/R121 正反例及提示模板 |
| `tests/test_parser*.py` | LIMIT、ALTER 列定义、二级 MAXVALUE 的 AST/token 结构断言 |
| `tests/test_instance_scope_rules.py` | 仅分布式集合增加 R030/R032/R121，数量更新为 121/121/91/30 |
| `tests/test_oracle_compat_rules.py` | 只更新全局规则总数断言，R078-R119 范围保持 42 条不变 |
| 前端静态契约测试 | 四张表都必须有稳定 row-key + reserve-selection |
| 前端浏览器行为测试 | 四个 module 的跨页勾选、超选、清空、慢响应竞态 |
| `README.md`、`docs/USER_GUIDE.md`、`docs/功能使用手册.md` | 只更新当前规则总数、分类数和本次涉及规则说明 |
| `VERSION`、`backend/config.py`、发布记录 | 编码验收完成后统一更新为 v1.6.3.2 |

仓库内所有 `119` 命中必须逐条分类：当前能力声明改为 121；“v1.0.2 当时发布 119 条”等历史记录不改；Oracle 子集 R078-R119 的编号范围不改。

---

## 10. 测试设计

### 10.1 规则单元测试

#### R011/R120

1. CREATE 中 TEXT 命中 R011 INFO；
2. ALTER ADD/MODIFY/CHANGE TEXT 命中 R011；
3. 5 种 R120 类型逐一大小写参数化测试；
4. 同句 TEXT + BLOB 同时得到 R011 INFO、R120 ERROR；
5. `TINYTEXT/TINYBLOB/JSON` 不命中两条拆分规则；
6. 类型词出现在注释、字符串、列名中不命中；
7. 多字段聚合顺序稳定；
8. 规则集覆盖 R011 严重度后仍可覆盖默认 INFO；
9. 新 R120 无覆盖时默认启用。

#### R030/R032

1. 分布式 CREATE VIEW/PROCEDURE/TRIGGER/FUNCTION 命中 R030；
2. 集中式同语句跳过 R030；
3. 分布式 TEMPORARY TABLE 命中 R032；
4. 集中式跳过 R032；
5. 规则集启用不能绕过适用域；
6. R031 集中式行为保持原状，作为 OUT-01 回归锁。

#### R035

1. 同名 `VARCHAR(32)`/`VARCHAR(128)` 通过；
2. `DECIMAL(10,2)`/`DECIMAL(18,4)` 通过；
3. `DATETIME(3)`/`DATETIME(6)` 通过；
4. `INT`/`BIGINT` 报错；
5. `INT`/`INT UNSIGNED` 报错；
6. 大小写及 INTEGER/INT 经过解析器归一后通过；
7. 单条无上下文 SQL 跳过；
8. 两表文件、三表文件、在线元数据合并 DDL 均能稳定找到冲突；
9. 控制说明和建议中不再出现“长度必须一致”。

#### R058

按 §4.6.3 判定表逐项参数化，另增加：

1. SELECT 子查询内部 LIMIT 不作为外层 UPDATE 的 LIMIT；
2. 无 WHERE 的 UPDATE/DELETE 仍由现有规则处理，R058 不重复报；
3. 非分片表上下文不触发；
4. 集中式实例按适用域跳过；
5. 超大整数字面量不抛异常；
6. R022 的 1000 文案保持不变。

#### R121

按 §4.7.4 逐项参数化，另增加：

1. CREATE 的普通文本形态与 `SHOW CREATE TABLE` 可执行注释形态；
2. ALTER ADD 和 ALTER REORGANIZE；
3. 多个 MAXVALUE 定义聚合分区名；
4. 语法不完整时产生 E999，不把 R121 当语法修复器；
5. parser 指纹在 MAXVALUE 与普通数值边界间可区分；
6. 一级和二级同时存在时只读取二级节点；
7. 所有测试不通过全文 `in`/正则假阳性来满足。

### 10.2 数量与注册测试

必须锁定：

```text
ALL_RULE_CLASSES = 121
rule_id 唯一 = 121
R078..R119 Oracle 子集 = 42
distributed enabled = 121
centralized enabled = 91
centralized skipped = 30
distributed-only exact set 包含 R030/R032/R121
```

另验证规则 API 的 category 数：DDL 23、DISTRIBUTED 15，其他分类不变。

### 10.3 迁移测试

1. 空库执行 v0～v14 成功；
2. v13 存量库升级后 R011/R035/R058 元数据刷新；
3. R120/R121 插入成功；
4. 二次启动幂等；
5. 存量 `enabled` 不被重置；
6. 规则集 override 不被删除或改写；
7. 运行时 API 与 `rule_configs` 的内置元数据一致；
8. migration checksum 门禁通过。

### 10.4 前端静态契约测试

对四个 module 分别断言：

- `el-table` 具有 `row-key="id"`；
- selection 列具有 `reserve-selection`；
- 翻页事件只加载数据，不调用清空选择；
- 查询/重置/模块切换调用统一清空函数；
- 比较请求仍提交两个 ID；
- 不使用页内 index 作为标识。

### 10.5 前端浏览器行为测试

建议新增 Playwright 行为测试，复用项目 dev extra 中已固定的浏览器测试依赖。四个 module 共用同一参数化测试夹具：

| 编号 | 操作 | 预期 |
|---|---|---|
| FE-01 | 第 1 页选 A → 第 2 页选 B | 已选数为 2，比较按钮可用 |
| FE-02 | 回到第 1 页 | A 自动恢复勾选 |
| FE-03 | 发起比较 | 请求体仅含 A、B 的 ID，与页码无关 |
| FE-04 | 已选 A、B 后再选 C | C 自动取消，A/B 保留，出现“最多两次”提示 |
| FE-05 | A/B 实例不同 | 新增项取消，原合法项保留 |
| FE-06 | A/B 数据源不同 | 新增项取消，原合法项保留 |
| FE-07 | 翻页后点同条件刷新 | 选择保留 |
| FE-08 | 改条件并查询/重置 | 选择清空，表格内部无残留勾选 |
| FE-09 | 切换 module | 选择清空，不可跨模块提交 |
| FE-10 | 删除已选快照 | 选择清空；再比较不会提交已删除 ID |
| FE-11 | 先发第 1 页慢请求、再发第 2 页快请求 | 最终显示第 2 页，旧响应被丢弃 |
| FE-12 | 退出再换用户登录 | 不保留前一用户选择 |

必须真实触发翻页和 selection-change，不能只用源码字符串断言代替行为测试。

### 10.6 内网 UAT

| 编号 | 前置 | 操作 | 通过标准 |
|---|---|---|---|
| UAT-01 | 分布式测试实例 | 审核含 TEXT 的 CREATE/ALTER | R011 INFO，提示和建议完全符合模板 |
| UAT-02 | 分布式测试实例 | 审核 5 种受限 LOB | 每种均 R120 ERROR |
| UAT-03 | 集中式与分布式各一套 | 审核 R030/R032 样例 | 仅分布式执行这两条 |
| UAT-04 | 两表同名 VARCHAR 不同长度 | 文件及在线元数据审核 | R035 不报 |
| UAT-05 | 两表同名 VARCHAR/CHAR | 同上 | R035 ERROR |
| UAT-06 | 分片表 UPDATE/DELETE | 分别使用 2000、2001、占位符、无 LIMIT | 结果符合判定表 |
| UAT-07 | 目标实例版本核验 | 核实 SQL 引擎版本 | 所有受控实例均满足 UPDATE/DELETE LIMIT 的版本前提，或已形成例外决议 |
| UAT-08 | 分布式实例 | 审核二级 RANGE MAXVALUE CREATE/ALTER | R121 ERROR，分区名正确 |
| UAT-09 | 分布式实例 | 审核一级 RANGE MAXVALUE、注释和字符串诱饵 | R121 不误报 |
| UAT-10 | 四个历史页面各准备至少 2 页 | 跨页选两条并比较 | 勾选、返回显示、请求 ID、结果均正确 |
| UAT-11 | 活动规则集 | 升级前后对比 | 旧 override 保留，新规则默认行为已获管理员确认 |

---

## 11. 发布、回滚与可观测性

### 11.1 发布顺序

1. 实施代码与自动化测试；
2. 在 CI 中完成全量回归和前端行为测试；
3. 在内网确认分布式实例的 UPDATE/DELETE LIMIT 版本前提；
4. DBA/管理员审阅活动规则集和两条新规则默认开启的影响；
5. 执行 v14 迁移；
6. 部署后核对 `/api/v1/rules` 数量与关键元数据；
7. 执行 UAT-01～UAT-11；
8. 最后更新并核对 v1.6.3.2 发布记录。

### 11.2 回滚原则

- 应用回滚必须与规则目录元数据回滚成套评估，避免旧代码读取新规则目录产生展示不一致；
- 已生成的 R120/R121 历史结果不删除；旧版本前端应能按通用 violation 结构展示未知规则号；
- v14 迁移不回改文件 checksum。若必须恢复元数据，创建前向修复迁移，不手工改已登记迁移；
- 前端跨页修复可随应用版本回滚，无数据库状态；
- 回滚不自动恢复用户已清空或删除的历史快照。

### 11.3 发布后核对

至少记录：

- 规则 API 总数、分布式/集中式有效数和跳过数；
- R011/R120/R121 在规则页的名称、级别、适用域和建议；
- v14 migration 状态与 checksum；
- 升级后 24 小时内 R011/R120/R121 命中量及异常激增；
- 四个 module 的 compare 请求失败率；
- 浏览器控制台是否出现重复 ref、selection store 或 Vue 只读状态警告。

---

## 12. 风险、决策与待实施门禁

| 编号 | 风险/决策 | 处置 |
|---|---|---|
| RISK-01 | 用户原文类型和关键字有拼写误差 | 按 §2.1 归一，测试只使用正式 token |
| RISK-02 | R011 收窄后 TINYTEXT/TINYBLOB/JSON 不再覆盖 | 这是明确清单的结果；UAT 确认，不擅自扩展 |
| RISK-03 | R030 改适用域但 R031 仍会在集中式拦函数 | 记录为 OUT-01；如需放行另行决策 |
| RISK-04 | R035 只改文案但仍使用 raw_type | 测试必须用不同长度同类型，防止假完成 |
| RISK-05 | R035 缺上下文导致永不触发 | 批量 DDL 构造跨表索引，单条无依据时明确跳过 |
| RISK-06 | R058 继续只查关键字会错误放行 | AST/token 结构化读取行数，注释和字符串反例锁定 |
| RISK-07 | 旧引擎版本不支持全局 DML LIMIT | UAT-07 是发布门禁 |
| RISK-08 | MAXVALUE 全文搜索误伤一级分区或序列 | 只消费二级 RANGE/ALTER 分区值节点 |
| RISK-09 | 公开文档的自动分区能力与项目人工维护策略可能因版本不同 | 将 R121 明确标为项目治理规则；以目标版本和原厂定版为准 |
| RISK-10 | 新规则默认开启改变质量门禁结果 | 发布前审阅所有活动规则集 |
| RISK-11 | `rule_configs` INSERT IGNORE 留下旧文案 | v14 迁移定向刷新元数据 |
| RISK-12 | reserve-selection 内部状态与 cmpState 不一致 | 统一 clear 函数，同时清 Element Plus store 和业务数组 |
| RISK-13 | 快速翻页旧响应覆盖新页 | 请求序号丢弃过期响应 |
| RISK-14 | 四个页面只修一个 | 静态测试和行为测试均按四个 module 参数化 |
| RISK-15 | 固定数字散落造成回归 | 当前态数字更新，历史数字保留；全仓搜索逐条分类 |

进入编码的前置门禁只有一项需要在目标环境确认：纳管分布式实例是否全部满足官方所述的 UPDATE/DELETE LIMIT 版本前提。该项不影响本设计定版，但未确认前不得发布 R058 新建议到生产。

---

## 13. 完成定义

v1.6.3.2 只有同时满足以下条件才算完成：

- [ ] R011/R120 的类型集合、级别、名称、提示和修复建议与本文一致；
- [ ] R030/R032 仅分布式适用，R031/R024 未被越权修改；
- [ ] R035 不比较任何括号参数，并具备真实跨表上下文测试；
- [ ] R058 实际校验 LIMIT 数值，上边界为 2000，注释/字符串不能放行；
- [ ] R121 只对分布式二级 RANGE 分区中的 MAXVALUE 报错，并覆盖 CREATE/ALTER；
- [ ] 规则总数、分类数、适用域数和 API 数量与 §6.2 一致；
- [ ] v14 迁移幂等且不覆盖管理员启停/严重度策略；
- [ ] 四个指定页面均可跨页选择两条记录，超选/不兼容/清空边界正确；
- [ ] 前端不存在旧响应覆盖新页；
- [ ] 全量单测、集成测试、浏览器行为测试和内网 UAT 通过；
- [ ] 目标实例版本门禁有书面结果；
- [ ] 版本号统一为 v1.6.3.2；
- [ ] 实施提交不夹带无关改动，发布提交可追溯并已推送到 `origin/main`。

---

## 14. 本设计阶段交付声明

本文件是 v1.6.3.2 的开发详细设计定版。本阶段没有修改任何运行代码、测试、数据库迁移、前端页面或版本号；上述文件清单均是后续实施范围，不代表已实现。进入开发后如发现目标 TDSQL 私有云版本语法与公开文档或本文假设不一致，应以目标实例的只读语法验证和原厂书面结论为准，先修订设计再编码，不允许用宽松全文正则绕过差异。
