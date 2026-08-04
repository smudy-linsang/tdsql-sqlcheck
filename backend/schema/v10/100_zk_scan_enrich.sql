-- ============================================================================
-- V1.6.0.3：ZK 扫描富集配置与导入留痕（设计 DESIGN-v1.6.0.3 §7）
--
-- 1) zk_discovery_config 增加扫描富集所需配置：
--    - IP 段替换规则（非敏感，明文 JSON）
--    - MonitorDB 与业务账号（口令仅存 Fernet 密文）
--    - 名称解析固化级别与富集开关
-- 2) zk_discovery_import_items 增加来源留痕列（name_source/databases_source）
-- 全部 ALTER 走信息库探测后执行，重复升级幂等（列存在即跳过）。
-- ============================================================================

ALTER TABLE zk_discovery_config
    ADD COLUMN octet_rules_json TEXT NULL;

ALTER TABLE zk_discovery_config
    ADD COLUMN monitor_host VARCHAR(255) NOT NULL DEFAULT '';

ALTER TABLE zk_discovery_config
    ADD COLUMN monitor_port INT NOT NULL DEFAULT 0;

ALTER TABLE zk_discovery_config
    ADD COLUMN monitor_user VARCHAR(128) NOT NULL DEFAULT '';

ALTER TABLE zk_discovery_config
    ADD COLUMN monitor_db VARCHAR(128) NOT NULL DEFAULT '';

ALTER TABLE zk_discovery_config
    ADD COLUMN monitor_password_encrypted TEXT NULL;

ALTER TABLE zk_discovery_config
    ADD COLUMN business_username VARCHAR(128) NOT NULL DEFAULT '';

ALTER TABLE zk_discovery_config
    ADD COLUMN business_password_encrypted TEXT NULL;

ALTER TABLE zk_discovery_config
    ADD COLUMN name_query_hint VARCHAR(64) NOT NULL DEFAULT '';

ALTER TABLE zk_discovery_config
    ADD COLUMN enrich_enabled TINYINT NOT NULL DEFAULT 1;

ALTER TABLE zk_discovery_import_items
    ADD COLUMN name_source VARCHAR(32) NOT NULL DEFAULT '';

ALTER TABLE zk_discovery_import_items
    ADD COLUMN databases_source VARCHAR(32) NOT NULL DEFAULT '';
