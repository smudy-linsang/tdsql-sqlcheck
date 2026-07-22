-- v1.2 初始数据库 Schema

CREATE TABLE IF NOT EXISTS schema_version (
    `key`      VARCHAR(128) PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS users (
    username              VARCHAR(64) PRIMARY KEY,
    display_name          VARCHAR(128) DEFAULT '',
    role                  VARCHAR(32) NOT NULL DEFAULT 'developer',
    password_hash         VARCHAR(128) NOT NULL,
    salt                  VARCHAR(32) NOT NULL,
    status                VARCHAR(16) NOT NULL DEFAULT 'active',
    must_change_password  TINYINT DEFAULT 0,
    failed_attempts       INT DEFAULT 0,
    locked_until          DATETIME DEFAULT NULL,
    created_by            VARCHAR(64) DEFAULT '',
    created_at            DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at            DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    last_login_at         DATETIME DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
