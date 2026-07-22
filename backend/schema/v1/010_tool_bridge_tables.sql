-- v1.2 ToolBridge 运维工具箱任务流与日志表

CREATE TABLE IF NOT EXISTS tool_runs (
    run_id VARCHAR(64) PRIMARY KEY,
    tool_name VARCHAR(64) NOT NULL,
    target_connection VARCHAR(64) DEFAULT '',
    params_json TEXT,
    status VARCHAR(32) DEFAULT 'RUNNING',
    error_message TEXT,
    created_by VARCHAR(64) NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    finished_at DATETIME,
    INDEX idx_tr_status (status),
    INDEX idx_tr_user (created_by)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
