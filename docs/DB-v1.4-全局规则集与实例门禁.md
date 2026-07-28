# TDSQL-SQLCheck V1.4 数据库设计说明书
## 全局规则集 + 实例级质量门禁

| 项目 | 内容 |
|---|---|
| 文档类型 | 数据库设计说明书 |
| 版本 | V1.4（基线 v1.3.3.1） |
| 元数据库 | MySQL / MariaDB（`tdsql_sqlcheck`），InnoDB / utf8mb4 |
| 迁移方式 | `backend/schema/v3/030_global_ruleset_gate.sql` + `database.py` 幂等增列 |
| 关联文档 | 《ARCHITECTURE-v1.4》《DETAIL-v1.4》《API-v1.4》 |

---

## 1. 变更总览

| # | 对象 | 动作 | 性质 |
|---|---|---|---|
| C-1 | `system_config` | 新增一行配置键 `active_rule_set_id` | 数据初始化 |
| C-2 | `instance_gate_rules` | **新建表** | 结构新增 |
| C-3 | `audit_history` | 增列 `rule_set_id` | 结构新增（可空） |
| C-4 | `scan_snapshots` | 增列 `rule_set_id` | 结构新增（可空） |
| C-5 | `gate_audit_logs` | 增列 `connection_id`、`rule_set_id` | 结构新增（可空） |
| C-6 | `projects` | `rule_set_id` / `gate_rule_id` 标注 DEPRECATED | 仅注释，**不改结构** |
| C-7 | `gate_rules` | 整表标注 DEPRECATED | 仅注释，**不改结构** |

**全部变更均为新增，无删除、无重命名、无类型变更**，因此不存在数据丢失风险，回滚只需停止读取新字段。

---

## 2. C-1：全局启用规则集（`system_config`）

### 2.1 既有表结构（不变）

```sql
CREATE TABLE IF NOT EXISTS system_config (
    config_key          VARCHAR(64) PRIMARY KEY,
    config_value        VARCHAR(256) DEFAULT '',
    updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 2.2 新增配置键

| config_key | 含义 | 初始值 | 取值约束 |
|---|---|---|---|
| `active_rule_set_id` | 全局生效的规则集 ID | `'default'` | 必须是 `rule_sets.id` 中存在的值；空串视为未配置 |

### 2.3 初始化语句（幂等）

```sql
INSERT IGNORE INTO system_config(config_key, config_value)
VALUES ('active_rule_set_id', 'default');
```

### 2.4 为什么用单键而不是 `rule_sets.is_active` 列

MySQL 不支持部分唯一索引（`WHERE is_active = 1` 的唯一约束），无法在库层保证"有且仅有一个启用"。若用列表示，唯一性只能靠应用层事务维护，一旦并发切换或有人直接改库，就会出现两个"启用中"的规则集——而消除这种歧义正是本次的目标。

**单键结构使唯一性由数据结构本身保证**：一个主键只能有一个值，不存在不一致的可能。

列表接口返回的 `is_active` 是读取时与该键比对得出的派生值，**不落库**。

---

## 3. C-2：实例级质量门禁（新建表）

### 3.1 建表语句

```sql
CREATE TABLE IF NOT EXISTS instance_gate_rules (
    connection_id       VARCHAR(64) PRIMARY KEY COMMENT '实例ID，对应 tdsql_connections.id',
    max_error_count     INT NOT NULL DEFAULT 0   COMMENT 'ERROR 数量上限；-1 表示不限',
    max_warning_count   INT NOT NULL DEFAULT -1  COMMENT 'WARNING 数量上限；-1 表示不限（默认不限）',
    mode                VARCHAR(16) NOT NULL DEFAULT 'enforce'
                        COMMENT '判定模式：enforce=正式拦截 / observe=仅记录不拦截（可选能力）',
    description         TEXT                     COMMENT '备注',
    updated_by          VARCHAR(64) NOT NULL DEFAULT '',
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_igr_connection FOREIGN KEY (connection_id)
        REFERENCES tdsql_connections(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='实例级质量门禁规则（V1.4，替代按项目绑定的 gate_rules）';
```

### 3.2 字段设计说明

| 字段 | 设计要点 |
|---|---|
| `connection_id` | 主键即实例，天然保证"一个实例一份门禁配置"，无需额外唯一约束 |
| 外键 `ON DELETE CASCADE` | 实例被删除时门禁配置一并清理，避免留下指向不存在实例的孤儿配置。与 `audit_history` 等历史表不同——门禁配置是**配置**不是**留痕**，无保留价值 |
| `max_error_count` 默认 0 | 按决策要求，与现行 `gate_rules` 默认值一致 |
| `max_warning_count` 默认 -1 | 按决策要求（2026-07-28）。**与现行 `gate_rules` 默认值完全一致**，因此门禁判定结论不发生任何变化 |
| `-1` 语义 | "不限"，与既有 `gate_service.evaluate` 中 `>= 0 才参与判定` 的逻辑一致，**无需改判定算法** |
| `mode` | **可选能力**。`observe` 下正常计算与记录但 `passed` 恒为 true，供管理员日后把某个核心库的 WARNING 收紧到 0 前评估影响面。不在本期必须实现的路径上 |
| 无 `required_rules` / `blocked_rules` | 现状使用率为零，不迁移（见《ARCHITECTURE-v1.4》§7） |

### 3.3 未配置实例的兜底

**不预先为每个实例插入一行**。未配置的实例在判定时使用系统默认值（`enforce` / 0 / -1），由代码兜底。

理由：预插入会在新增实例时产生同步负担（漏插即行为不一致），而兜底逻辑只需一处。

### 3.4 存量迁移：不需要

**决策（2026-07-28）取 ERROR=0 / WARNING=-1，与现行 `gate_rules` 的默认值完全一致**，因此：

| 事项 | 结论 |
|---|---|
| 迁移 SQL | **无**。不需要为存量实例插入任何行 |
| 行为变化 | **无**。未配置实例走系统默认，判定结论与 V1.3 一致 |
| 灰度方案 | **不需要** |
| 发布通知 | 不需要特别通知 |

> 早先草案曾按 WARNING 默认 0 设计，并准备了三种迁移模式（保守 / 观察 / 直接生效）。
> 采用 -1 后该风险整体消失，**相关迁移语句已从脚本中删除，不要再照那版施工**。

---

## 4. C-3 / C-4 / C-5：可追溯性增列

三处增列服务于《ARCHITECTURE-v1.4》§6 的可追溯性设计，均为**可空**，不影响存量数据。

### 4.1 `audit_history` 增列

```sql
ALTER TABLE audit_history
    ADD COLUMN rule_set_id VARCHAR(64) DEFAULT NULL
    COMMENT 'V1.4：本次审核实际生效的规则集ID；NULL=V1.4前的历史记录，尺度未知';
```

### 4.2 `scan_snapshots` 增列

```sql
ALTER TABLE scan_snapshots
    ADD COLUMN rule_set_id VARCHAR(64) DEFAULT NULL
    COMMENT 'V1.4：生成本快照时生效的规则集ID；对比时校验两快照是否同尺度';
```

### 4.3 `gate_audit_logs` 增列

```sql
ALTER TABLE gate_audit_logs
    ADD COLUMN connection_id VARCHAR(64) DEFAULT NULL
    COMMENT 'V1.4：本次门禁判定所依据的实例（门禁绑定对象由项目改为实例）';
ALTER TABLE gate_audit_logs
    ADD COLUMN rule_set_id VARCHAR(64) DEFAULT NULL
    COMMENT 'V1.4：本次门禁判定时生效的规则集ID';
```

`gate_audit_logs.project_id` 保留原值不动（历史留痕）。

### 4.4 NULL 值语义（重要）

`rule_set_id IS NULL` 表示 **V1.4 之前产生的记录，尺度未知**。

对比功能对此的处理（详见《DETAIL-v1.4》§5.4）：

| 两快照的 rule_set_id | 处理 |
|---|---|
| 均非空且相等 | 正常对比 |
| 均非空但不等 | **拒绝对比**，返回 `E4007` |
| 任一为 NULL | **允许对比但给出警告**："其中一次扫描产生于 V1.4 之前，评估尺度未知，整改率仅供参考" |

第三条是刻意的取舍：若一律拒绝，全部存量快照将立即不可对比，等于废掉 V1.3 刚交付的能力；给出警告既保住可用性，又不掩盖不确定性。

---

## 5. C-6 / C-7：停用标注（不改结构）

### 5.1 `projects` 两列

```sql
ALTER TABLE projects
    MODIFY COLUMN rule_set_id VARCHAR(64) DEFAULT 'default'
    COMMENT 'DEPRECATED(V1.4)：规则集已改为全局启用，本列不再被读取，保留仅为兼容与回滚';
ALTER TABLE projects
    MODIFY COLUMN gate_rule_id VARCHAR(64) DEFAULT 'default'
    COMMENT 'DEPRECATED(V1.4)：门禁已改为绑定实例，本列不再被读取，保留仅为兼容与回滚';
```

### 5.2 `gate_rules` 表

```sql
ALTER TABLE gate_rules
    COMMENT='DEPRECATED(V1.4)：门禁绑定对象已由项目改为实例，见 instance_gate_rules。
             本表保留：gate_audit_logs 中的历史判定记录以 project_id 关联，删表会破坏合规痕迹可追溯性';
```

**不删列、不删表**的理由：

1. 历史审计痕迹的可追溯性（银行合规要求）；
2. 回滚路径完整——若 V1.4 需要回退，旧逻辑读这些列即可恢复，无需反向数据迁移；
3. `MODIFY COLUMN` 仅改注释，不触发表重建（MySQL 8.0 / MariaDB 10.x 对纯注释变更走 INSTANT / INPLACE），对大表无锁表风险。

---

## 6. 迁移脚本

### 6.1 文件位置

```
backend/schema/v3/030_global_ruleset_gate.sql
```

版本键由 `migrator.py` 生成为 `v3_030_global_ruleset_gate`。

> 注意：基线仓库 `backend/schema/` 下现有 `v0` / `v1` / `v2` 三个目录，**新增 v3 目录**。`loader.py` 按目录遍历，无需改代码。

### 6.2 脚本内容

```sql
-- ============================================================================
-- V1.4 全局规则集 + 实例级质量门禁
-- 全部为新增操作，无删除/重命名/类型变更；回滚只需停止读取新字段
-- ============================================================================

-- ── C-1 全局启用规则集 ──
INSERT IGNORE INTO system_config(config_key, config_value)
VALUES ('active_rule_set_id', 'default');

-- ── C-2 实例级质量门禁 ──
CREATE TABLE IF NOT EXISTS instance_gate_rules (
    connection_id       VARCHAR(64) PRIMARY KEY COMMENT '实例ID，对应 tdsql_connections.id',
    max_error_count     INT NOT NULL DEFAULT 0   COMMENT 'ERROR 数量上限；-1 表示不限',
    max_warning_count   INT NOT NULL DEFAULT -1  COMMENT 'WARNING 数量上限；-1 表示不限（默认不限）',
    mode                VARCHAR(16) NOT NULL DEFAULT 'enforce'
                        COMMENT '判定模式：enforce=正式拦截 / observe=仅记录不拦截（可选能力）',
    description         TEXT,
    updated_by          VARCHAR(64) NOT NULL DEFAULT '',
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_igr_connection FOREIGN KEY (connection_id)
        REFERENCES tdsql_connections(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='实例级质量门禁规则（V1.4，替代按项目绑定的 gate_rules）';

-- ── 存量实例迁移：不需要 ──
-- 默认值 (error=0 / warning=-1) 与现行 gate_rules 默认值完全一致，
-- 未配置的实例走代码兜底默认，判定结论与 V1.3 无差异，故无任何迁移语句。

-- ── C-7 旧门禁表停用标注 ──
ALTER TABLE gate_rules
    COMMENT='DEPRECATED(V1.4)：门禁绑定对象已由项目改为实例，见 instance_gate_rules';
```

### 6.3 增列走 `database.py` 幂等辅助

`audit_history` / `scan_snapshots` / `gate_audit_logs` / `projects` 的增列与注释变更，统一放在 `database.py` 的迁移段，复用既有 `_add_column_if_not_exists`，与 v1.3 的做法保持一致（见《DETAIL-v1.4》§2.1）。

---

## 7. 回滚方案

| 步骤 | 操作 |
|---|---|
| 1 | 回滚应用代码至 v1.3.3.1 |
| 2 | 无需执行任何 DDL —— 新增的表与列不被旧代码读取，不影响旧逻辑运行 |
| 3 | `instance_gate_rules` 中管理员已配置的数据成为孤立数据，不影响旧门禁逻辑（旧逻辑读 `gate_rules`） |
| 4 | 若需彻底清理：`DROP TABLE instance_gate_rules;` + `DELETE FROM system_config WHERE config_key='active_rule_set_id';` |

**回滚不丢数据**：本次所有变更均为新增，`projects.rule_set_id` / `gate_rules` 原值原封不动，旧代码回滚后立即恢复原有行为。

---

## 8. 数据量与性能评估

| 表 | 预估行数 | 访问模式 | 索引评估 |
|---|---|---|---|
| `system_config` | < 50 | 每次审核读 1 行（30s 缓存后实际频率极低） | 主键查，无需额外索引 |
| `instance_gate_rules` | = 实例数（预计数十至数百） | 每次门禁判定读 1 行 | 主键查，无需额外索引 |
| `audit_history.rule_set_id` | 随审核量增长 | 仅写入与展示，不作筛选条件 | **不建索引**（无查询场景，建了是浪费） |
| `scan_snapshots.rule_set_id` | 随扫描量增长 | 对比时按快照 ID 取出后比对，非查询条件 | **不建索引** |

**结论：本次变更不引入任何新的性能热点。** 唯一的新增读取（`active_rule_set_id`）是主键单行查询，且有 30 秒缓存。

---

## 9. 完整性约束一览

| 约束 | 实现方式 |
|---|---|
| 有且仅有一个生效规则集 | `system_config` 单键结构天然保证 |
| 生效规则集必须存在 | 应用层校验 + 兜底回落 `default`（见《ARCHITECTURE-v1.4》§3.2） |
| 一个实例仅一份门禁配置 | `instance_gate_rules.connection_id` 主键 |
| 门禁配置不指向已删实例 | 外键 `ON DELETE CASCADE` |
| 启用中的规则集不可删除 | 应用层前置校验，返回 409（库层不设约束，避免删除规则集时报晦涩的外键错误） |
