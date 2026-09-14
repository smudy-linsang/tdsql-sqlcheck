-- v1.6.4.0 / CP-1 Rev.D：AI Copilot A组（核心耦合组）——身份代际与全局控制行
-- 设计出处：DETAIL §10.1/§10.2（B-01 方案乙：A组2表+B组9表）
-- version_key: v16_160_copilot_identity_runtime
--
-- A组契约（方案乙）：
--   · 本组两张表与账户生命周期同事务，沿用核心统一迁移失败关闭原则；
--   · N-10：本组已登记后缺表禁止走 QC-DEFECT-07 自愈重建（否则存量账号会被
--     静默重新分配 subject，旧 grant/session 全部孤儿化），必须失败关闭人工介入；
--     首次未登记的正常安装仍允许创建；
--   · ID/hash/key 使用 ASCII 二进制比较；时间一律 DATETIME(6) UTC 由应用显式写入；
--   · 账户事务只访问本组（runtime/subjects），绝不依赖 B 组九表。

CREATE TABLE IF NOT EXISTS copilot_subjects (
    subject_id      CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL
        COMMENT '账号代际身份（UUID hex，永不重用）',
    username        VARCHAR(64) NOT NULL
        COMMENT '用户名（显示/审计快照，不作为身份判据）',
    user_created_at DATETIME NOT NULL
        COMMENT 'users.created_at 快照',
    state           VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL
        COMMENT 'ACTIVE / REVOKED',
    created_at      DATETIME(6) NOT NULL
        COMMENT 'subject 创建时间（UTC）；早于该时点的 JWT 在 Copilot 侧拒绝',
    revoked_at      DATETIME(6) NULL DEFAULT NULL
        COMMENT '吊销时间（UTC）',
    PRIMARY KEY (subject_id),
    INDEX idx_copilot_subjects_user (username, state, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='v1.6.4.0 Copilot 账号代际身份（A组，失败关闭）';

CREATE TABLE IF NOT EXISTS copilot_runtime (
    id              TINYINT NOT NULL
        COMMENT '固定为 1 的全局控制行',
    runner_id       VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NULL DEFAULT NULL
        COMMENT '当前 runner 身份',
    heartbeat_at    DATETIME(6) NULL DEFAULT NULL
        COMMENT 'runner 心跳（UTC）',
    accepting       TINYINT NOT NULL DEFAULT 0
        COMMENT '是否受理新任务（0/1）',
    config_revision BIGINT NOT NULL DEFAULT 1
        COMMENT 'settings 乐观锁版本',
    settings_json   TEXT NOT NULL
        COMMENT '闭集设置：enabled/allow_schema_identifiers/额度/保留期',
    content_used_bytes      BIGINT NOT NULL DEFAULT 0
        COMMENT '正文实际占用（密文字节）',
    content_reserved_bytes  BIGINT NOT NULL DEFAULT 0
        COMMENT '正文预留（密文字节上界）',
    updated_at      DATETIME(6) NOT NULL,
    module_schema_state     VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin
        NOT NULL DEFAULT 'UNAVAILABLE'
        COMMENT 'B组模块结构状态：READY / UNAVAILABLE',
    module_schema_revision  CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL DEFAULT NULL
        COMMENT '该发布 B组迁移+结构合同 hash',
    module_schema_epoch     BIGINT NOT NULL DEFAULT 1
        COMMENT 'B组故障代次（失效/恢复幂等）',
    module_reconciled_epoch BIGINT NOT NULL DEFAULT 0
        COMMENT '已完成恢复对账的代次',
    module_schema_checked_at DATETIME(6) NULL DEFAULT NULL
        COMMENT '最近一次结构验收时间（UTC）',
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='v1.6.4.0 Copilot 全局控制行（A组，失败关闭）';

-- 种子控制行：缺行才插，不覆盖活跃 runner；初始 enabled=false、标识符投影关闭。
INSERT IGNORE INTO copilot_runtime
    (id, runner_id, heartbeat_at, accepting, config_revision, settings_json,
     content_used_bytes, content_reserved_bytes, updated_at,
     module_schema_state, module_schema_revision, module_schema_epoch,
     module_reconciled_epoch, module_schema_checked_at)
VALUES
    (1, NULL, NULL, 0, 1,
     '{"enabled": false, "allow_schema_identifiers": false, "user_daily_tokens": 1000000, "global_daily_tokens": 10000000, "session_retention_days": 30, "audit_retention_days": 180}',
     0, 0, UTC_TIMESTAMP(6), 'UNAVAILABLE', NULL, 1, 0, NULL);

-- 首次迁移为存量账号分配代际 subject（DETAIL §9.1）：确定性 subject_id 保证幂等；
-- 分配时点之后，早于该时点的既有 JWT 在 Copilot 侧将收到 401 提示重新登录（N-02）。
-- 新账号由账户生命周期事务接点分配随机 UUID；删除重建由接点吊销旧 subject 后另配新值。
INSERT IGNORE INTO copilot_subjects
    (subject_id, username, user_created_at, state, created_at, revoked_at)
SELECT LOWER(MD5(CONCAT('subj-v1-', username, '-',
                        COALESCE(CAST(created_at AS CHAR), '')))),
       username, COALESCE(created_at, UTC_TIMESTAMP()), 'ACTIVE',
       UTC_TIMESTAMP(6), NULL
FROM users;
