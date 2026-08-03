-- V1.6.0.0: ZooKeeper 自动发现运行配置（认证口令仅保存加密密文）
CREATE TABLE IF NOT EXISTS zk_discovery_config (
    config_id TINYINT UNSIGNED NOT NULL PRIMARY KEY,
    servers TEXT NOT NULL,
    root_path VARCHAR(512) NOT NULL DEFAULT '/tdsqlzk',
    driver VARCHAR(16) NOT NULL DEFAULT 'kazoo',
    zkcli_path VARCHAR(1024) NOT NULL DEFAULT '',
    proxy_mode VARCHAR(16) NOT NULL DEFAULT 'first',
    default_database VARCHAR(128) NOT NULL DEFAULT 'ALL',
    endpoint_map_json TEXT NOT NULL,
    auth_username VARCHAR(128) NOT NULL DEFAULT '',
    auth_password_encrypted TEXT NOT NULL,
    updated_by VARCHAR(64) NOT NULL DEFAULT '',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
