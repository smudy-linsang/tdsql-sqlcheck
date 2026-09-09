-- v1.6.3.5 / DU-2 / FIX-02：在线元数据审核任务表 + 全局受理槽位表
-- 设计出处：docs/DETAIL-v1.6.3.5-大库在线元数据审核稳定性修复.md §6.1
-- InnoDB + utf8mb4；ID/hash/token 用 ascii_bin 比较。幂等 CREATE TABLE IF NOT EXISTS。

CREATE TABLE IF NOT EXISTS metadata_audit_jobs (
    id                      CHAR(32)      NOT NULL COMMENT '服务端 UUID hex，所有产物文件名由它派生',
    created_by              VARCHAR(128)  NOT NULL COMMENT '已认证服务端身份（前端不得覆盖）',
    request_id              VARCHAR(64)   NOT NULL COMMENT '服务端请求关联号',
    idempotency_key         CHAR(32)      NOT NULL COMMENT '客户端幂等键',
    request_hash            CHAR(64)      NOT NULL COMMENT '规范化请求内容 hash（不含可变服务端状态）',
    connection_id           VARCHAR(128)  NOT NULL COMMENT '实际目标连接',
    db_name                 VARCHAR(128)  NOT NULL COMMENT '最终实际库名',
    request_json            MEDIUMTEXT    NOT NULL COMMENT '规范化请求',
    execution_context_json  MEDIUMTEXT    NOT NULL COMMENT '冻结规则尺度/实例口径/报告来源/执行版本',
    connection_fingerprint  CHAR(64)      NOT NULL COMMENT '非口令连接配置+密文凭据哈希（检测受理后变化）',
    state                   VARCHAR(24)   NOT NULL COMMENT 'ACCEPTED/RUNNING/PUBLISHING/PUBLISHED/SUCCEEDED/STOPPING/FAILED/CANCELLED/RECOVERY_REQUIRED',
    phase                   VARCHAR(24)   NOT NULL COMMENT 'WAITING/ENUMERATING/EXTRACTING/AUDITING/SERIALIZING/SNAPSHOTTING/PERSISTING/CLEANUP/DONE',
    attempt_token           CHAR(32)      NULL COMMENT '一次执行的随机 fencing token',
    runner_id               VARCHAR(128)  NULL COMMENT 'runner 启动身份',
    child_pid               BIGINT        NULL COMMENT '子进程 PID',
    child_start_identity    VARCHAR(128)  NULL COMMENT '子进程操作系统启动身份（避免 PID 复用误杀）',
    created_at              DATETIME(6)   NOT NULL,
    updated_at              DATETIME(6)   NOT NULL,
    started_at              DATETIME(6)   NULL,
    finished_at             DATETIME(6)   NULL,
    deadline_at             DATETIME(6)   NULL,
    heartbeat_at            DATETIME(6)   NULL COMMENT '监督活性心跳（与 progress 区分）',
    progress_at             DATETIME(6)   NULL COMMENT '实际阶段进展时间',
    progress_json           MEDIUMTEXT    NULL COMMENT '计数/字节/摘要（不含全量结果）',
    result_meta_json        MEDIUMTEXT    NULL COMMENT '摘要/告警/artifact hash',
    cancel_requested_at     DATETIME(6)   NULL COMMENT '用户取消意图（不等于已 CANCELLED）',
    error_code              VARCHAR(64)   NULL COMMENT '脱敏稳定错误码',
    error_message           VARCHAR(1024) NULL COMMENT '用户提示',
    report_id               BIGINT        NULL COMMENT 'audit_history 关联（唯一发布关系）',
    snapshot_id             BIGINT        NULL,
    exit_code               INT           NULL COMMENT 'NULL=尚未确认',
    cleanup_ok              TINYINT       NULL COMMENT 'NULL=尚未确认，不得默认 true',
    artifact_state          VARCHAR(16)   NOT NULL DEFAULT 'NONE' COMMENT 'NONE/READY/MISSING/DELETED',
    PRIMARY KEY (id),
    UNIQUE KEY uq_metadata_job_idem (created_by, idempotency_key),
    UNIQUE KEY uq_metadata_job_report (report_id),
    KEY idx_metadata_job_creator (created_by, created_at, id),
    KEY idx_metadata_job_state (state, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='在线元数据审核任务（v1.6.3.5）';

CREATE TABLE IF NOT EXISTS metadata_audit_slot (
    id                    TINYINT      NOT NULL COMMENT '固定 1，所有 Web worker 的原子受理锁',
    active_job_id         CHAR(32)     NULL COMMENT '唯一占用中的 job',
    runner_id             VARCHAR(128) NULL COMMENT '当前 runner 服务身份',
    runner_heartbeat_at   DATETIME(6)  NULL COMMENT 'runner 监督心跳',
    accepting             TINYINT      NOT NULL DEFAULT 0 COMMENT '初始不接受，runner 恢复校验后才开放',
    storage_instance_id   CHAR(32)     NULL COMMENT '持久产物目录安装标识',
    updated_at            DATETIME(6)  NOT NULL,
    artifact_used_bytes   BIGINT       NOT NULL DEFAULT 0 COMMENT '已核验产物总量',
    storage_checked_at    DATETIME(6)  NULL COMMENT '最近存储核验时间；NULL/过期拒绝新受理',
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='元数据审核全局受理槽位（固定单行 id=1，v1.6.3.5）';

-- 幂等插入受理槽位（不覆盖已有 active_job_id/runner_id）
INSERT INTO metadata_audit_slot (id, active_job_id, runner_id, runner_heartbeat_at,
                                 accepting, storage_instance_id, updated_at,
                                 artifact_used_bytes, storage_checked_at)
SELECT 1, NULL, NULL, NULL, 0, NULL, UTC_TIMESTAMP(6), 0, NULL
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM metadata_audit_slot WHERE id = 1);
