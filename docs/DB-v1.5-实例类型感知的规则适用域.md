# TDSQL-SQLCheck v1.5 数据库设计说明书
## 实例类型感知的规则适用域

| 项 | 内容 |
|---|---|
| 版本 | v1.5.0.0 |
| 基线 | v1.4.0.1（commit `6106a9a`） |
| 元数据库 | MySQL/MariaDB 5.7+ · `tdsql_sqlcheck` · InnoDB · utf8mb4 |
| 迁移文件 | `backend/schema/v4/040_instance_type_scope.sql`（新建） |
| 变更性质 | **纯增量**：仅新增列、新增配置项。无删表、无删列、无改名、无类型变更 |
| 关联文档 | `ARCHITECTURE-v1.5-*.md` · `DETAIL-v1.5-*.md` · `API-v1.5-*.md` |

---

## 1. 设计原则

| # | 原则 | 说明 |
|---|---|---|
| P1 | **只加不改** | 全部变更为 `ADD COLUMN` / `INSERT IGNORE`。任何时候停止读取新列，系统行为即回到 v1.4.0.1 |
| P2 | **NULL 即"未知口径"** | 存量记录的 `instance_type` 一律为 `NULL`，语义是"v1.5 之前产生，评估口径未知"。**严禁回填** —— 回填等于伪造历史评估口径，让报告不再可复现、可审计 |
| P3 | **不新建表** | 实例类型是实例的属性、评估口径是报告的属性，都应作为列附着在既有实体上。新建表只会引入无谓的 JOIN 与一致性负担 |
| P4 | **配置沿用单键真相源** | 全局默认实例类型放 `system_config` 单键，与 v1.4 `active_rule_set_id` 完全同构（MySQL 无部分唯一索引，单键唯一性由主键结构保证） |
| P5 | **列名统一** | 跨表一律使用 `instance_type` / `instance_type_source`，不出现 `inst_type`、`itype` 等变体 |

> **关于 P2 的重要性**：v1.4 引入 `rule_set_id` 时确立了同一约定（`_save_audit_history` 里 `rule_set_id or None`，注释写明"NULL 语义为 V1.4 前历史记录，尺度未知"）。v1.5 严格复用这一约定，两个"口径"字段（尺度 + 实例类型）语义一致，前端可用同一套逻辑渲染"口径未知"提示。

---

## 2. 变更总览

| 表 | 变更 | 列数 | 用途 |
|---|---|---|---|
| `tdsql_connections` | 新增列 | 3 | 记录探测结果与来源，供解析器与前端冲突提示使用 |
| `audit_history` | 新增列 | 3 | 报告口径留痕，支撑报告横幅与可审计性 |
| `scan_snapshots` | 新增列 | 1 | 快照口径留痕（**只留痕，本版本不参与对比校验**，见 §3.3） |
| `system_config` | 新增配置项 | 1 行 | 全局默认实例类型（B 类通道兜底） |

**合计：3 张表 +7 列，1 张配置表 +1 行。无新建表。**

---

## 3. 表结构变更详述

### 3.1 `tdsql_connections` —— 实例注册表

#### 现状（相关列）

```sql
id                  VARCHAR(64) PRIMARY KEY
name                VARCHAR(128)
host                VARCHAR(128)
port                INT
is_distributed      INT DEFAULT 1        -- 用户在建连表单勾选，默认"分布式"
set_list            VARCHAR(512) DEFAULT ''
...
```

**问题**：`is_distributed` 是用户自填的单选值，**没有任何校验**。审核结论的正确性不能建立在一个可能被勾错的复选框上（详见 `ARCHITECTURE-v1.5` §2.2 的错勾后果不对称分析）。

#### 新增列

```sql
ALTER TABLE tdsql_connections
    ADD COLUMN detected_instance_type VARCHAR(16) NULL DEFAULT NULL
        COMMENT '探测得出的实例类型：distributed|centralized；NULL=尚未探测或探测无结论';

ALTER TABLE tdsql_connections
    ADD COLUMN instance_type_detected_at DATETIME NULL DEFAULT NULL
        COMMENT '最近一次成功探测的时间；NULL=从未探测成功';

ALTER TABLE tdsql_connections
    ADD COLUMN instance_type_probe_error VARCHAR(512) NOT NULL DEFAULT ''
        COMMENT '最近一次探测失败原因（截断512）；空串=无失败记录';
```

#### 列语义与取值

| 列 | 类型 | 可空 | 取值 | 说明 |
|---|---|---|---|---|
| `detected_instance_type` | VARCHAR(16) | ✅ | `distributed` / `centralized` / NULL | **探测结论**。NULL 表示"尚未探测成功"，此时解析器退回 `is_distributed` |
| `instance_type_detected_at` | DATETIME | ✅ | 时间 / NULL | 用于判断探测结论是否陈旧；也是前端"最近探测于 X"的数据源 |
| `instance_type_probe_error` | VARCHAR(512) | ❌ | 错误文本 / `''` | 便于运维定位"为什么这台实例探不出来"。**必须截断到 512**，异常栈可能很长 |

#### 为什么不直接改写 `is_distributed`

**刻意不改**。三条理由：

1. **区分"人说的"和"探到的"是设计要求**。合并成一列，冲突就无法呈现，`ARCHITECTURE-v1.5` §5.2 的 `TypeSource` 与 G3（冲突时以探测为准并标记）就落不了地。
2. **不能悄悄改用户填的数据**。用户在实例管理页看到的应该始终是自己填的值；系统的判断作为独立信息展示，两者并列，冲突时给出红色标记让人来处置。静默覆写会让人失去对系统的信任。
3. **回滚安全**。`is_distributed` 保持原样，停用新列即完全回到 v1.4 语义。

#### 解析优先级（数据视角）

```
detected_instance_type 非 NULL 且未过期
    → 用它                                        source = probed
否则 is_distributed = 1 → distributed
     is_distributed = 0 → centralized             source = declared
```

**`is_distributed` 永远非空**（`INT DEFAULT 1`），所以 A 类通道的解析**必定有结论**，不会落到全局默认。

#### 索引

**不加索引。** `tdsql_connections` 是低基数配置表（实例数量级为数十到数百），全部访问路径都是主键 `id` 查单行或全表扫描列表页。为 `detected_instance_type` 建索引没有任何查询会用到，只会拖慢写入。

---

### 3.2 `audit_history` —— 审核历史

#### 现状（相关列）

```sql
id                  INT PRIMARY KEY AUTO_INCREMENT
audit_type          VARCHAR(64) NOT NULL      -- sql | sql_batch | file | extracted_schema
source              TEXT
project_id          VARCHAR(64) DEFAULT ''
connection_id       VARCHAR(64) DEFAULT ''
db_name             VARCHAR(128)              -- v1.3 新增
rule_set_id         VARCHAR(64) NULL          -- v1.4 新增，NULL=尺度未知
error_count / warning_count / pass_rate / results_json ...
```

**问题**：一份报告完全无法自证是按什么口径评出来的。v1.4 解决了"哪把尺"（`rule_set_id`），v1.5 要解决"哪种实例"。**两个维度都记全，报告才是可复现、可审计的。**

#### 新增列

```sql
ALTER TABLE audit_history
    ADD COLUMN instance_type VARCHAR(16) NULL DEFAULT NULL
        COMMENT '本次审核采用的实例类型口径：distributed|centralized；NULL=v1.5前记录，口径未知';

ALTER TABLE audit_history
    ADD COLUMN instance_type_source VARCHAR(16) NOT NULL DEFAULT ''
        COMMENT '口径来源：probed|declared|request|default；空串=v1.5前记录';

ALTER TABLE audit_history
    ADD COLUMN skipped_rules_count INT NOT NULL DEFAULT 0
        COMMENT '因实例类型不适用而跳过的规则条数；0 且 instance_type IS NULL 表示未知';
```

#### 列语义

| 列 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `instance_type` | VARCHAR(16) | ✅ | **核心口径字段**。NULL 语义严格等同 `rule_set_id` 的 NULL："v1.5 前记录，口径未知" |
| `instance_type_source` | VARCHAR(16) | ❌ | 结论可信度。`probed` 最高；`default` 说明系统是猜的。报告页据此决定是否显示"口径为系统默认，请确认"提示 |
| `skipped_rules_count` | INT | ❌ | 报告横幅数据源："本次按【集中式】口径评估，已跳过 27 条仅分布式适用规则"。**冗余存储是有意的**——规则清单会随版本变化，历史报告必须能显示当时的真实数字，不能事后重算 |

#### 为什么 `skipped_rules_count` 存数字而不存清单

存清单（`skipped_rule_ids TEXT`）能提供更多信息，但：

- 每条记录多 ~200 字节，`audit_history` 是高频写入表（每次即时审核都写一行）；
- 报告页只需要一个数字做横幅；真要看是哪 27 条，`GET /api/v1/rules?instance_type=centralized` 随时可查当前清单；
- 历史报告"当时跳过了具体哪些"属于极低频审计需求，不值得为它给主表增重。

**结论：存计数。** 若后续确有审计需求，再以旁路表承载，不动主表。

#### 索引

**不加新索引。**

现有索引：`idx_audit_type`、`idx_audit_project`、`idx_audit_created`、`idx_audit_gate`。

历史记录列表页的查询形态是 `WHERE audit_type=? [AND connection_id=?] ORDER BY created_at DESC LIMIT ?`，`instance_type` 只作为**结果列展示**，不进 WHERE。为展示列建索引是纯负担。

> 若将来产品上出现"只看集中式实例的报告"这类筛选，届时再评估 `(audit_type, instance_type, created_at)` 联合索引。**现在不预建。**

---

### 3.3 `scan_snapshots` —— 扫描对比基线快照

#### 现状（相关列）

```sql
id                  BIGINT PRIMARY KEY AUTO_INCREMENT
module              VARCHAR(32)   -- schema_audit|slow_scan|bigtable
biz_ref_id          VARCHAR(64)
connection_id       VARCHAR(64)
db_name             VARCHAR(128)
scan_finished_at    DATETIME      -- 比对方向判定依据
issue_total / error_count / warning_count
fingerprint_algo    VARCHAR(16) DEFAULT 'v1'
schema_version      INT DEFAULT 1
snapshot_json       LONGTEXT
```

**背景**：快照两两相减得出"整改率"。v1.5 上线后，同一台**集中式**实例的问题数会因为不再跑 27 条分布式规则而显著下降——拿 v1.5 前的快照做基线，对比报告会呈现一次**根本没有发生过的整改**。

**产品决策（2026-07-29，负责人拍板）：本版本不做口径隔离。**

理由：内网环境尚未正式上线，仍在试运行且高频迭代，**不存在需要保护的历史基线资产**，为此增加拦截逻辑与警示条属于过度设计。

#### 新增列（仍然要加）

```sql
ALTER TABLE scan_snapshots
    ADD COLUMN instance_type VARCHAR(16) NULL DEFAULT NULL
        COMMENT '采集本快照时的实例类型口径；NULL=v1.5前快照，口径未知。本版本只留痕不参与对比校验';
```

#### 为什么砍了逻辑却保留这一列

这是有意的取舍，两者性质完全不同：

| | 性质 | 成本 | 事后可补？ |
|---|---|---|---|
| `instance_type` **列** | **数据** | 20 B/行，无索引，可忽略 | ❌ **不可能**——快照采集时刻的口径一旦过期就无法还原 |
| 跨口径**校验逻辑** | **逻辑** | 需改对比接口 + 前端警示条 + HTML 导出 | ✅ 随时可加 |

因此：**砍逻辑、留数据**。等正式上线、历史基线真正成为资产时，凭这一列即可补上校验，届时连数据回填都不需要。反过来若现在连列也不加，将来补校验就会面对一大批口径不明的快照。

#### 对比行为

`POST /api/v1/scan-compare/compare` 的行为**与 v1.4 完全一致**——不拒绝、不警示、不改逻辑。`instance_type` 仅作为快照详情的展示字段。

**风险自担项（写入发布说明）**：v1.5 上线当天，集中式实例的问题数会明显下降，**这不是整改成效**。

#### 索引

**不加。** `scan_snapshots` 的查询入口是 `module + connection_id + scan_finished_at`，`instance_type` 不参与任何筛选。

---

### 3.4 `system_config` —— 全局配置

#### 现状

```sql
CREATE TABLE system_config (
    config_key      VARCHAR(128) PRIMARY KEY,
    config_value    TEXT,
    ...
)
```

已有键：`permission_version`、`active_rule_set_id`（v1.4）。

#### 新增配置项

```sql
INSERT IGNORE INTO system_config(config_key, config_value)
VALUES ('default_instance_type', 'distributed');
```

| 键 | 取值域 | 出厂值 | 用途 |
|---|---|---|---|
| `default_instance_type` | `distributed` / `centralized` | **`distributed`** | B 类通道（文件上传/批量流式/GitLab/CLI，即**物理上没有目标实例**的场景）在调用方也未显式声明时的兜底口径 |

#### 出厂值为什么是 `distributed`

三条理由，缺一不可：

1. **安全方向正确**。兜底走 `distributed` = 跑全部 119 条规则 = **宁可多报不可漏报**。反过来兜底走 `centralized`，会让所有无实例上下文的审核静默少跑 27 条规则——而漏报是看不见的（报告里少的东西没人会发现），这是最危险的失效模式。
2. **零行为变更**。B 类通道在 v1.4.0.1 下本来就是跑全部规则。出厂值取 `distributed`，这些通道**行为与升级前逐条一致**，把回归面严格限制在"有实例上下文的通道"，也就是缺陷真正所在的地方。
3. **符合现场分布**。TDSQL 部署以分布式实例为主，`TDSQLConnectRequest.is_distributed` 的默认值本来也是 `True`。

管理员可在「系统配置」改为 `centralized`（适用于整体只用集中式实例的环境）。

#### 生效时延（必须准确表述）

生产部署为 `uvicorn --workers 2`（`deploy/tdsql-sqlcheck.service:13`），配置读取带进程内缓存。因此：

> **文档、API 响应、前端提示语一律表述为"最长 5 分钟生效"，严禁写"即时生效"。**

这是 v1.3.3 已确立的团队约定（限流配置同款处理）。多 worker 下不存在跨进程的即时一致性，写"即时生效"就是对使用者撒谎。

---

## 4. 迁移脚本

### 4.1 文件位置与命名

```
backend/schema/v4/040_instance_type_scope.sql
```

沿用既有版本目录约定（v0 `001_init_tables` → v1 `010_tool_bridge_tables` → v2 `020_scan_compare_tables` → v3 `030_global_ruleset_gate`）。`SchemaMigrator` 自动发现、按 `v{version}_{sequence:03d}_{name}` 记账并计算 SHA256 校验和。

### 4.2 完整脚本

```sql
-- ============================================================================
-- V1.5 实例类型感知的规则适用域
-- 全部为新增操作，无删除/重命名/类型变更；回滚只需停止读取新列
-- 设计依据：docs/DB-v1.5-实例类型感知的规则适用域.md
-- ============================================================================

-- ── D-1 实例注册表：探测结果与人工声明分列存放 ──
-- 不覆写 is_distributed：区分"人声明的"与"探测到的"是本次可靠性设计的前提，
-- 合并成一列则冲突无法呈现，也无从给出前端告警。
ALTER TABLE tdsql_connections
    ADD COLUMN detected_instance_type VARCHAR(16) NULL DEFAULT NULL
        COMMENT '探测得出的实例类型 distributed|centralized；NULL=尚未探测成功';

ALTER TABLE tdsql_connections
    ADD COLUMN instance_type_detected_at DATETIME NULL DEFAULT NULL
        COMMENT '最近一次成功探测时间；NULL=从未成功';

ALTER TABLE tdsql_connections
    ADD COLUMN instance_type_probe_error VARCHAR(512) NOT NULL DEFAULT ''
        COMMENT '最近一次探测失败原因（截断512）；空串=无失败';

-- ── D-2 审核历史：报告口径留痕 ──
-- instance_type 的 NULL 语义与 V1.4 的 rule_set_id 完全一致：
-- "本条为该特性上线前的记录，口径未知"。严禁回填——回填即伪造历史口径。
ALTER TABLE audit_history
    ADD COLUMN instance_type VARCHAR(16) NULL DEFAULT NULL
        COMMENT '本次审核的实例类型口径；NULL=V1.5前记录，口径未知';

ALTER TABLE audit_history
    ADD COLUMN instance_type_source VARCHAR(16) NOT NULL DEFAULT ''
        COMMENT '口径来源 probed|declared|request|default；空串=V1.5前记录';

ALTER TABLE audit_history
    ADD COLUMN skipped_rules_count INT NOT NULL DEFAULT 0
        COMMENT '因实例类型不适用而跳过的规则条数（冗余存储，供历史报告如实回显）';

-- ── D-3 基线快照：口径留痕（只留痕，本版本不参与对比校验）──
-- 负责人决策：试运行期无历史基线资产，不做跨口径拦截。
-- 但列必须现在加：快照采集时刻的口径一旦过期就无法还原，
-- 而校验逻辑随时可补。砍逻辑、留数据。
ALTER TABLE scan_snapshots
    ADD COLUMN instance_type VARCHAR(16) NULL DEFAULT NULL
        COMMENT '采集时的实例类型口径；NULL=V1.5前快照。本版本只留痕不参与对比校验';

-- ── D-4 全局默认实例类型 ──
-- 出厂 distributed：兜底=跑全部规则=宁可多报不可漏报；
-- 且使无实例上下文的通道（上传/流式/GitLab/CLI）行为与 V1.4.0.1 逐条一致。
INSERT IGNORE INTO system_config(config_key, config_value)
VALUES ('default_instance_type', 'distributed');

-- ── 存量数据迁移：不需要，且明令禁止 ──
-- audit_history / scan_snapshots 的存量记录 instance_type 保持 NULL。
-- 任何 UPDATE 回填都是在伪造这些报告当时的评估口径，破坏可审计性。
```

### 4.3 迁移器兼容性说明

`SchemaMigrator.run_migrations()` 会剔除 `--` 注释行后按 `;` 分割逐条执行，单条失败仅告警不中断。因此：

- 脚本中的注释均为整行 `--`，**不使用行尾注释**（行尾 `--` 不会被剔除逻辑处理，会连同 SQL 一起被分号切割）；
- 每条语句以 `;` 结尾；
- `COMMENT '...'` 内不含分号。

### 4.4 重复执行安全性

| 语句 | 重复执行 | 处理 |
|---|---|---|
| `ALTER TABLE ... ADD COLUMN` | ❌ MySQL 报 `Duplicate column name` | ✅ 迁移器凭 `schema_migrations` 记账，已应用的文件不再执行；即使异常重跑，迁移器捕获单条异常仅告警不中断 |
| `INSERT IGNORE` | ✅ 天然幂等 | — |

> **补强建议（施工时落实）**：为与 `database.py` 既有风格一致并彻底消除告警噪声，同时在 `_ensure_columns()` 中用 `_add_column_if_not_exists()` 补一份等价声明。两条路径都存在时，先执行的那条生效，后者自动跳过。这是本项目 v1.2/v1.3 已有的双保险惯例（见 `database.py:502-510` 的 `set_list` / `monitor_*` 列）。

---

## 5. 数据字典（新增列汇总）

| 表 | 列 | 类型 | 可空 | 默认 | 语义 |
|---|---|---|---|---|---|
| `tdsql_connections` | `detected_instance_type` | VARCHAR(16) | ✅ | NULL | 探测结论：`distributed`/`centralized`；NULL=未探测成功 |
| `tdsql_connections` | `instance_type_detected_at` | DATETIME | ✅ | NULL | 最近成功探测时间 |
| `tdsql_connections` | `instance_type_probe_error` | VARCHAR(512) | ❌ | `''` | 最近探测失败原因 |
| `audit_history` | `instance_type` | VARCHAR(16) | ✅ | NULL | 本次审核口径；NULL=v1.5前记录 |
| `audit_history` | `instance_type_source` | VARCHAR(16) | ❌ | `''` | `probed`/`declared`/`request`/`default` |
| `audit_history` | `skipped_rules_count` | INT | ❌ | 0 | 因不适用而跳过的规则条数 |
| `scan_snapshots` | `instance_type` | VARCHAR(16) | ✅ | NULL | 快照采集时口径 |
| `system_config` | 行 `default_instance_type` | TEXT | ❌ | `distributed` | B类通道兜底口径 |

### 取值域约束说明

`instance_type` 系列列**不使用 ENUM，也不加 CHECK 约束**：

- ENUM 的取值集合改动需要 `ALTER TABLE` 重建，而适用域取值将来可能扩展（如 `centralized` 之外的新形态）；
- MariaDB 与部分 MySQL 版本对 CHECK 的支持差异较大，加了反而增加环境适配成本；
- 取值合法性由应用层 `InstanceType` 枚举强制（写入前必经枚举转换），**这是唯一写入路径**。

---

## 6. 容量与性能评估

| 表 | 单行增量 | 量级 | 影响 |
|---|---|---|---|
| `tdsql_connections` | ≤ 540 B | 数十~数百行 | 可忽略 |
| `audit_history` | ≤ 40 B | 高频写入 | 相对 `results_json`（LONGTEXT，常达数十~数百 KB）可忽略 |
| `scan_snapshots` | ≤ 20 B | 中频 | 相对 `snapshot_json`（LONGTEXT）可忽略 |

**查询性能：零影响**——未新增索引，未改变任何现有查询的执行计划。

**写入性能：零影响**——新增列均为定长小字段，且未建索引，不引入额外索引维护开销。

**探测开销**：见 `DETAIL-v1.5` §4.2。单次扫描仅 1 次轻量查询，结果进程内缓存 300s；探测走连接池已有连接，不新建连接。

---

## 7. 回滚方案

| 步骤 | 操作 |
|---|---|
| 1 | 代码回滚至 v1.4.0.1 |
| 2 | **数据库不做任何操作** —— 新增列全部可空或有默认值，v1.4 代码的 `INSERT`/`SELECT` 均按显式列名进行，不受新列影响 |
| 3 | `schema_migrations` 中 `v4_040_instance_type_scope` 记录**保留**。若后续重新升级 v1.5，迁移器凭校验和识别为"已应用"，不会重复执行 |

**回滚后行为**：完全等同 v1.4.0.1，即所有实例按全量 119 条规则评估（缺陷复现，但无数据损坏）。

**回滚后的数据可读性**：v1.5 期间写入的 `instance_type` 等列在 v1.4 代码下不被读取，静默保留。若再次升级到 v1.5，这些数据可直接继续使用，无需重建。

---

## 8. 与 v1.4 数据库设计的一致性对照

本次设计严格沿用 v1.4 已确立的四条约定，不引入新范式：

| v1.4 约定 | v1.5 对应 |
|---|---|
| `audit_history.rule_set_id` NULL = "上线前记录，尺度未知"，不回填 | `audit_history.instance_type` NULL = "上线前记录，口径未知"，不回填 |
| 全局唯一配置放 `system_config` 单键（`active_rule_set_id`），不用 `is_active` 列 | `default_instance_type` 同款单键 |
| 迁移脚本纯增量，回滚只需停止读取新字段 | 同 |
| 多 worker 缓存语义一律表述"最长 N 秒/分钟生效" | 同（本次为 300s → "最长 5 分钟"） |
