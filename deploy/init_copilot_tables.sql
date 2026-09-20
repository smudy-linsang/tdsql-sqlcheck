-- ============================================================================
-- TDSQL SQL审核平台 - Copilot B组业务表独立初始化与台账登记脚本 (v1.6.4.1)
-- 包含：Copilot 9张业务表结构定义 + schema_migrations 台账原子登记
-- 用途：支持运维人员/部署智能体直接通过 MySQL 客户端执行，免除连接池耗尽或环境依赖问题
-- 执行方式: mysql -h <host> -P <port> -u <user> -p'<pwd>' tdsql_sqlcheck < init_copilot_tables.sql
-- ============================================================================

CREATE TABLE IF NOT EXISTS copilot_providers (
    id              CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    name            VARCHAR(128) NOT NULL,
    endpoint_id     VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL
        COMMENT '引用部署批准端点清单（不允许 UI 输入 URL）',
    protocol        VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL
        COMMENT '固定 OPENAI_COMPAT_CHAT',
    model_id        VARCHAR(128) NOT NULL,
    secret_envelope TEXT NULL DEFAULT NULL
        COMMENT '严格 AES-GCM 封套的模型凭据；NETWORK_IDENTITY 可空',
    auth_mode       VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL
        COMMENT 'BEARER / NETWORK_IDENTITY',
    capabilities_json TEXT NOT NULL
        COMMENT '能力契约（§7.1 闭集），≤8KiB',
    enabled         TINYINT NOT NULL DEFAULT 0
        COMMENT '未自检通过不能启用',
    revision        BIGINT NOT NULL DEFAULT 1
        COMMENT '配置版本（乐观锁），健康变化不增加',
    tested_revision BIGINT NULL DEFAULT NULL
        COMMENT '最近成功自检对应的配置版',
    cooldown_until  DATETIME(6) NULL DEFAULT NULL
        COMMENT '429/熔断冷却截止（UTC）',
    consecutive_failures INT NOT NULL DEFAULT 0,
    last_error_code VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL DEFAULT NULL,
    created_at      DATETIME(6) NOT NULL,
    updated_at      DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_copilot_providers_name (name),
    INDEX idx_copilot_providers_enabled (enabled, cooldown_until, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='v1.6.4.1 Copilot 模型提供方配置（B组）';

CREATE TABLE IF NOT EXISTS copilot_scene_routes (
    scene_code      VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL
        COMMENT '场景码（§5.1 用户场景闭集）',
    primary_provider_id  CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL DEFAULT NULL,
    fallback_provider_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL DEFAULT NULL,
    privacy_profile VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    revision        BIGINT NOT NULL DEFAULT 1,
    enabled         TINYINT NOT NULL DEFAULT 0,
    updated_by      VARCHAR(128) NOT NULL,
    updated_at      DATETIME(6) NOT NULL,
    PRIMARY KEY (scene_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='v1.6.4.1 Copilot 场景路由（B组）；空主 provider 即本地模式';

CREATE TABLE IF NOT EXISTS copilot_instance_grants (
    subject_id      CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    connection_id   VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    username        VARCHAR(128) NOT NULL COMMENT '用户名快照（显示用）',
    enabled         TINYINT NOT NULL DEFAULT 0
        COMMENT '仅 APPROVED 后由服务端置 1；PENDING/REVOKED 不得启用',
    approved_by     VARCHAR(128) NULL DEFAULT NULL COMMENT '复核人快照',
    approval_ref    VARCHAR(128) NOT NULL DEFAULT '' COMMENT '外部批准单编号',
    revision        BIGINT NOT NULL DEFAULT 1,
    updated_at      DATETIME(6) NOT NULL,
    allow_schema_identifiers TINYINT NOT NULL DEFAULT 0,
    identifier_approval_ref VARCHAR(128) NULL DEFAULT NULL,
    approval_state  VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin
        NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/APPROVED/REVOKED',
    requested_by_subject_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    approved_by_subject_id  CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL DEFAULT NULL,
    requested_at    DATETIME(6) NOT NULL,
    approved_at     DATETIME(6) NULL DEFAULT NULL,
    PRIMARY KEY (subject_id, connection_id),
    INDEX idx_copilot_grants_conn (connection_id, enabled)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='v1.6.4.1 Copilot 实例资料授权（B组，申请/复核分离）';

CREATE TABLE IF NOT EXISTS copilot_sessions (
    id              CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    owner_subject_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    owner           VARCHAR(128) NOT NULL COMMENT '用户名显示快照',
    scope_kind      VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL
        COMMENT 'GLOBAL_HELP / INSTANCE',
    connection_id   VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NULL DEFAULT NULL,
    database_name   VARCHAR(128) NULL DEFAULT NULL,
    instance_type   VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL
        COMMENT 'distributed / centralized / unknown',
    initial_page_key VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    name_snapshot   VARCHAR(255) NULL DEFAULT NULL COMMENT '实例连接名称快照',
    name_source     VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    title           VARCHAR(128) NOT NULL COMMENT '本地固定生成，不用问题原文',
    revision        BIGINT NOT NULL DEFAULT 1,
    active_turn_id  CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL DEFAULT NULL,
    state           VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL
        COMMENT 'OPEN / ARCHIVED / EXPIRED',
    created_at      DATETIME(6) NOT NULL,
    updated_at      DATETIME(6) NOT NULL,
    expires_at      DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    INDEX idx_copilot_sessions_owner (owner_subject_id, updated_at, id),
    INDEX idx_copilot_sessions_expire (expires_at, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='v1.6.4.1 Copilot 私有会话（B组）';

CREATE TABLE IF NOT EXISTS copilot_previews (
    id              CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    session_id      CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    owner_subject_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    owner           VARCHAR(128) NOT NULL,
    scene           VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    input_hash      CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL
        COMMENT 'keyring 独立 purpose HMAC，不记录敏感原文裸 hash',
    payload_envelope    MEDIUMTEXT NOT NULL COMMENT '清洗后输入封套（≤64KiB 明文）',
    evidence_envelope   MEDIUMTEXT NOT NULL COMMENT '规范证据封套（≤128KiB 明文）',
    model_projection_envelope MEDIUMTEXT NOT NULL COMMENT '已批准待发投影（≤32KiB 明文）',
    snapshot_hash   CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    permission_version VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    grant_revision  BIGINT NULL DEFAULT NULL,
    route_revision  BIGINT NULL DEFAULT NULL,
    provider_revisions_json TEXT NOT NULL,
    data_class      VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL
        COMMENT 'PUBLIC_HELP / INTERNAL_REDACTED / RESTRICTED',
    storage_reserved_bytes BIGINT NOT NULL DEFAULT 0,
    created_at      DATETIME(6) NOT NULL,
    expires_at      DATETIME(6) NOT NULL COMMENT '120 秒有效',
    consumed_turn_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL DEFAULT NULL,
    projection_mode VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL
        COMMENT 'ALIASED / SCHEMA_IDENTIFIERS',
    identifier_policy_revision CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL
        COMMENT '三闸规范快照 SHA256（无敏感值）',
    module_schema_epoch BIGINT NOT NULL
        COMMENT '预览时冻结的 A 组故障代次',
    PRIMARY KEY (id),
    INDEX idx_copilot_previews_owner (owner_subject_id, created_at, id),
    INDEX idx_copilot_previews_expire (owner_subject_id, expires_at, id),
    INDEX idx_copilot_previews_session (session_id, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='v1.6.4.1 Copilot 资料预览（B组，120秒有效）';

CREATE TABLE IF NOT EXISTS copilot_turns (
    id              CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    session_id      CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    preview_id      CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    owner           VARCHAR(128) NOT NULL COMMENT '用户名显示快照，不作身份判据',
    owner_subject_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    turn_kind       VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL
        COMMENT 'USER_QUESTION / PROVIDER_SELFTEST（仅服务端设置）',
    scene           VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    rule_snapshot_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL DEFAULT NULL,
    module_schema_epoch BIGINT NOT NULL
        COMMENT '受理时冻结的 A 组故障代次；出站/发布须与当前 READY 代次一致',
    client_request_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    request_hash    CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    sequence_no     INT NOT NULL,
    state           VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    phase           VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    attempt_token   CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL DEFAULT NULL,
    runner_id       VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NULL DEFAULT NULL,
    lease_until     DATETIME(6) NULL DEFAULT NULL,
    created_at      DATETIME(6) NOT NULL,
    deadline_at     DATETIME(6) NOT NULL COMMENT '总 deadline 受理即冻结',
    updated_at      DATETIME(6) NOT NULL,
    started_at      DATETIME(6) NULL DEFAULT NULL,
    finished_at     DATETIME(6) NULL DEFAULT NULL,
    cancel_requested_at DATETIME(6) NULL DEFAULT NULL,
    provider_id     CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL DEFAULT NULL,
    route_snapshot_envelope TEXT NOT NULL COMMENT '主备配置版与批准策略快照（≤8KiB 明文）',
    request_envelope  MEDIUMTEXT NOT NULL,
    evidence_envelope MEDIUMTEXT NOT NULL,
    response_envelope MEDIUMTEXT NULL DEFAULT NULL,
    source_hash     CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL DEFAULT NULL,
    output_hash     CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL DEFAULT NULL,
    reserved_tokens BIGINT NOT NULL DEFAULT 0,
    charged_tokens  BIGINT NOT NULL DEFAULT 0,
    error_code      VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL DEFAULT NULL,
    error_message   VARCHAR(512) NULL DEFAULT NULL,
    feedback_rating TINYINT NULL DEFAULT NULL,
    feedback_code   VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL DEFAULT NULL,
    feedback_at     DATETIME(6) NULL DEFAULT NULL,
    feedback_rule_ids_json TEXT NULL DEFAULT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_copilot_turns_idem (owner_subject_id, client_request_id),
    UNIQUE KEY uq_copilot_turns_seq (session_id, sequence_no),
    UNIQUE KEY uq_copilot_turns_preview (preview_id),
    INDEX idx_copilot_turns_state (state, created_at, id),
    INDEX idx_copilot_turns_owner (owner_subject_id, created_at, id),
    INDEX idx_copilot_turns_session (session_id, created_at, id),
    INDEX idx_copilot_turns_feedback (feedback_at, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='v1.6.4.1 Copilot 异步轮次（B组）';

CREATE TABLE IF NOT EXISTS copilot_daily_budgets (
    principal       VARCHAR(160) CHARACTER SET ascii COLLATE ascii_bin NOT NULL
        COMMENT 'user:<subject_id> 或 global',
    day_utc         DATE NOT NULL,
    reserved_tokens BIGINT NOT NULL DEFAULT 0,
    charged_tokens  BIGINT NOT NULL DEFAULT 0,
    updated_at      DATETIME(6) NOT NULL,
    PRIMARY KEY (principal, day_utc)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='v1.6.4.1 Copilot 日额度台账（B组）';

CREATE TABLE IF NOT EXISTS copilot_provider_attempts (
    id              CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    turn_id         CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    operator_subject_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    operator        VARCHAR(128) NOT NULL,
    provider_id     CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    provider_revision BIGINT NOT NULL,
    attempt_no      INT NOT NULL,
    status          VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL
        COMMENT 'STARTED / OK / FAILED / UNKNOWN',
    started_at      DATETIME(6) NOT NULL,
    finished_at     DATETIME(6) NULL DEFAULT NULL,
    latency_ms      INT NULL DEFAULT NULL,
    input_tokens    BIGINT NULL DEFAULT NULL,
    output_tokens   BIGINT NULL DEFAULT NULL,
    usage_source    VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL
        COMMENT 'PROVIDER / UPPER_BOUND / UNKNOWN',
    error_code      VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL DEFAULT NULL,
    provider_request_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NULL DEFAULT NULL,
    response_digest CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL DEFAULT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_copilot_attempt (turn_id, attempt_no),
    INDEX idx_copilot_attempts_provider (provider_id, started_at, id),
    INDEX idx_copilot_attempts_operator (operator_subject_id, started_at, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='v1.6.4.1 Copilot 模型调用台账（B组，不存响应正文/原始头）';

CREATE TABLE IF NOT EXISTS copilot_audit_events (
    id              BIGINT NOT NULL AUTO_INCREMENT,
    occurred_at     DATETIME(6) NOT NULL,
    operator        VARCHAR(128) NOT NULL,
    operator_subject_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL DEFAULT NULL
        COMMENT '后台系统事件可空',
    event_type      VARCHAR(40) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    session_id      CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL DEFAULT NULL,
    turn_id         CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL DEFAULT NULL,
    target_type     VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    target_id       VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NULL DEFAULT NULL,
    result_code     VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    detail_json     TEXT NOT NULL COMMENT '白名单元数据 ≤4KiB，无正文',
    request_id      CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    PRIMARY KEY (id),
    INDEX idx_copilot_audit_operator (operator, occurred_at, id),
    INDEX idx_copilot_audit_turn (turn_id, id),
    INDEX idx_copilot_audit_time (occurred_at, id),
    INDEX idx_copilot_audit_subject (operator_subject_id, occurred_at, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='v1.6.4.1 Copilot 审计元数据（B组，180天，不含正文）';

-- ════════════════════════════════════════════════════════════════════════════
-- 原子登记 schema_migrations 迁移台账（防止 runner 启动时报 "台账未登记" 错误）
-- ════════════════════════════════════════════════════════════════════════════
INSERT INTO schema_migrations (version_key, checksum, applied_at)
VALUES ('copilot_v1_001_business', '6cdda7b12cbdc2dba6a40815fbc0318b489216c9fd838979289890a1d90112b0', NOW())
ON DUPLICATE KEY UPDATE checksum = VALUES(checksum);
