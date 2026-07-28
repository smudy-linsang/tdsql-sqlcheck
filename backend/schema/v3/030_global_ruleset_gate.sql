-- ============================================================================
-- V1.4 全局规则集 + 实例级质量门禁
-- 全部为新增操作，无删除/重命名/类型变更；回滚只需停止读取新字段
-- 设计依据：docs/DB-v1.4-全局规则集与实例门禁.md
-- ============================================================================

-- ── C-1 全局启用规则集 ──
-- 单键作为唯一真相源：MySQL 不支持部分唯一索引，用 rule_sets.is_active 列
-- 无法在库层保证"有且仅有一个启用"，并发切换即生歧义。单键唯一性由结构保证。
INSERT IGNORE INTO system_config(config_key, config_value)
VALUES ('active_rule_set_id', 'default');

-- ── C-2 实例级质量门禁 ──
-- 门禁绑定对象由「项目」改为「实例」：同一把全局尺度下，不同实例可有不同放行标准。
-- 默认值 error=0 / warning=-1 与 V1.3 gate_rules 完全一致，判定结论不变、无需迁移。
CREATE TABLE IF NOT EXISTS instance_gate_rules (
    connection_id       VARCHAR(64) PRIMARY KEY COMMENT '实例ID，对应 tdsql_connections.id',
    max_error_count     INT NOT NULL DEFAULT 0   COMMENT 'ERROR 数量上限；-1 表示不限',
    max_warning_count   INT NOT NULL DEFAULT -1  COMMENT 'WARNING 数量上限；-1 表示不限（默认不限）',
    mode                VARCHAR(16) NOT NULL DEFAULT 'enforce'
                        COMMENT '判定模式：enforce=正式拦截 / observe=仅记录不拦截',
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
-- 不删表：gate_audit_logs 中历史判定记录以 project_id 关联，删表破坏合规可追溯性。
ALTER TABLE gate_rules
    COMMENT='DEPRECATED(V1.4)：门禁绑定对象已由项目改为实例，见 instance_gate_rules';
