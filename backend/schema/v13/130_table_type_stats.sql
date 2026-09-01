-- v1.6.3.0 G14 表类型统计（DESIGN-v1.6.3.0 Rev.M §6.8）
-- 槽位：v13/130。v11/110 与 v12/120 已被 v1.6.2.2 的 O-18 / O-22 占用（Rev.I 更正）。
-- 任务表：一次统计一行
CREATE TABLE IF NOT EXISTS table_type_stat (
    id                  INT PRIMARY KEY AUTO_INCREMENT,
    connection_id       VARCHAR(64) DEFAULT '',
    database_filter     VARCHAR(128) DEFAULT '',
    instance_type       VARCHAR(32) DEFAULT '',
    type_source         VARCHAR(32) DEFAULT '',
    database_count      INT DEFAULT 0,
    total_tables        INT DEFAULT 0,
    shard_tables        INT DEFAULT 0,
    broadcast_tables    INT DEFAULT 0,
    single_tables       INT DEFAULT 0,
    baseline_tables     INT DEFAULT 0,
    subpartition_tables INT DEFAULT 0,
    failed_databases    INT DEFAULT 0,
    skipped_databases   INT DEFAULT 0,
    overlap_count       INT DEFAULT 0,
    -- Rev.G / P1-07：MEDIUMTEXT 而非 TEXT。MAX_DATABASES=500，最坏情况下每库
    -- 一条告警；虽然 Rev.G 已把 PROXY_CMD_FAILED 汇总成一条，RECON_MISMATCH 等
    -- 逐库告警仍可能达数百条，中文 UTF-8 一个字符 3 字节，TEXT 的 64 KiB 会先触顶。
    -- 采集已完成却在落库处 1406/截断失败，是最贵的一种失败。
    warnings_json       MEDIUMTEXT,
    created_by          VARCHAR(64) DEFAULT '',
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_tts_conn (connection_id),
    INDEX idx_tts_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 明细表：一次统计的每个业务库一行
CREATE TABLE IF NOT EXISTS table_type_stat_item (
    id                  INT PRIMARY KEY AUTO_INCREMENT,
    stat_id             INT NOT NULL,
    db_name             VARCHAR(128) DEFAULT '',
    total_tables        INT DEFAULT 0,
    shard_tables        INT DEFAULT 0,
    broadcast_tables    INT DEFAULT 0,
    single_tables       INT DEFAULT 0,
    baseline_tables     INT DEFAULT 0,
    subpartition_tables INT DEFAULT 0,
    status              VARCHAR(16) DEFAULT 'OK',
    detail              VARCHAR(512) DEFAULT '',
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_ttsi (stat_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
