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
-- 本条为该特性上线前的记录，口径未知。严禁回填，回填即伪造历史口径。
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
-- 但列必须现在加：快照采集时刻的口径一旦过期就无法还原，而校验逻辑随时可补。
ALTER TABLE scan_snapshots
    ADD COLUMN instance_type VARCHAR(16) NULL DEFAULT NULL
        COMMENT '采集时的实例类型口径；NULL=V1.5前快照。本版本只留痕不参与对比校验';

-- ── D-4 全局默认实例类型 ──
-- 出厂 distributed：兜底=跑全部规则=宁可多报不可漏报；
-- 且使无实例上下文的通道（上传/流式/GitLab/CLI）行为与 V1.4.0.1 逐条一致。
INSERT IGNORE INTO system_config(config_key, config_value)
VALUES ('default_instance_type', 'distributed');

-- ── 存量数据迁移：不需要，且明令禁止 ──
-- audit_history 与 scan_snapshots 的存量记录 instance_type 保持 NULL。
-- 任何 UPDATE 回填都是在伪造这些报告当时的评估口径，破坏可审计性。
