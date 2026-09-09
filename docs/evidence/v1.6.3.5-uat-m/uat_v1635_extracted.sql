-- ============================================================================
-- TDSQL 自动拉取的最新在线元数据描述文件
-- 目标实例: smoke-g14-local
-- 目标数据库: tdsql_sqlcheck
-- 提取日期: 2026-09-09 20:11:58
-- ============================================================================

-- SQL Object: CREATE TABLE
-- Table: alert_rules
CREATE TABLE `alert_rules` (
  `metric_name` varchar(128) NOT NULL,
  `warning_threshold` double NOT NULL,
  `urgent_threshold` double NOT NULL,
  `check_interval_sec` int DEFAULT '60',
  `notify_webhook` text,
  `notify_email` text,
  `enabled` int DEFAULT '1',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`metric_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: alerts
CREATE TABLE `alerts` (
  `id` int NOT NULL AUTO_INCREMENT,
  `connection_id` varchar(64) NOT NULL,
  `metric_name` varchar(128) NOT NULL,
  `metric_value` double NOT NULL,
  `level` varchar(16) NOT NULL,
  `threshold` double NOT NULL,
  `message` text NOT NULL,
  `status` varchar(32) DEFAULT 'active',
  `acknowledged_by` varchar(64) DEFAULT '',
  `acknowledged_at` varchar(32) DEFAULT NULL,
  `resolved_at` varchar(32) DEFAULT NULL,
  `notify_channels` text,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_alert_status` (`status`),
  KEY `idx_alert_level` (`level`),
  KEY `idx_alert_connection` (`connection_id`),
  KEY `idx_alert_created` (`created_at`)
) ENGINE=InnoDB AUTO_INCREMENT=16 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: audit_history
CREATE TABLE `audit_history` (
  `id` int NOT NULL AUTO_INCREMENT,
  `audit_type` varchar(64) NOT NULL,
  `source` text,
  `project_id` varchar(64) DEFAULT '',
  `connection_id` varchar(64) DEFAULT '',
  `total_sql` int DEFAULT '0',
  `passed` int DEFAULT '0',
  `failed` int DEFAULT '0',
  `error_count` int DEFAULT '0',
  `warning_count` int DEFAULT '0',
  `pass_rate` double DEFAULT '0',
  `results_json` longtext,
  `gate_passed` int DEFAULT NULL,
  `gate_detail` text,
  `top_violations` text,
  `results_summary` text,
  `created_by` varchar(64) DEFAULT '',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `db_name` varchar(128) DEFAULT '',
  `rule_set_id` varchar(64) DEFAULT NULL,
  `instance_type` varchar(16) DEFAULT NULL,
  `instance_type_source` varchar(16) NOT NULL DEFAULT '',
  `skipped_rules_count` int NOT NULL DEFAULT '0',
  `report_context_json` text,
  PRIMARY KEY (`id`),
  KEY `idx_audit_type` (`audit_type`),
  KEY `idx_audit_project` (`project_id`),
  KEY `idx_audit_created` (`created_at`),
  KEY `idx_audit_gate` (`gate_passed`),
  KEY `idx_audit_conn` (`connection_id`,`created_at`)
) ENGINE=InnoDB AUTO_INCREMENT=6632 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: audit_results
CREATE TABLE `audit_results` (
  `id` int NOT NULL AUTO_INCREMENT,
  `audit_history_id` int NOT NULL,
  `sql_text` text NOT NULL,
  `sql_type` varchar(32) DEFAULT '',
  `line_number` int DEFAULT NULL,
  `file_path` text,
  `passed` int DEFAULT '1',
  `violations_json` text,
  `error_count` int DEFAULT '0',
  `warning_count` int DEFAULT '0',
  `triggered_rules` text,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_results_history` (`audit_history_id`),
  KEY `idx_results_passed` (`passed`),
  CONSTRAINT `audit_results_ibfk_1` FOREIGN KEY (`audit_history_id`) REFERENCES `audit_history` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: bigtable_classification
CREATE TABLE `bigtable_classification` (
  `id` int NOT NULL AUTO_INCREMENT,
  `connection_id` varchar(64) NOT NULL,
  `schema_name` varchar(128) NOT NULL,
  `table_name` varchar(256) NOT NULL,
  `table_type` varchar(32) NOT NULL,
  `table_type_label` varchar(64) DEFAULT '',
  `retention_days` int DEFAULT '0',
  `archive_target` varchar(128) DEFAULT '',
  `archive_period` varchar(64) DEFAULT '',
  `partition_key` varchar(128) DEFAULT '',
  `partition_granularity` varchar(32) DEFAULT '',
  `classified_by` varchar(64) DEFAULT '',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_bc` (`connection_id`,`schema_name`,`table_name`(128))
) ENGINE=InnoDB AUTO_INCREMENT=9 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: bigtable_history
CREATE TABLE `bigtable_history` (
  `id` int NOT NULL AUTO_INCREMENT,
  `snap_date` varchar(16) NOT NULL,
  `connection_id` varchar(64) DEFAULT '',
  `db_name` varchar(128) DEFAULT '',
  `table_name` varchar(128) DEFAULT '',
  `table_rows` bigint DEFAULT '0',
  `size_gb` double DEFAULT '0',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_bth` (`snap_date`,`connection_id`,`db_name`,`table_name`),
  KEY `idx_bth` (`connection_id`,`db_name`,`table_name`,`snap_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: bigtable_inventory
CREATE TABLE `bigtable_inventory` (
  `id` int NOT NULL AUTO_INCREMENT,
  `connection_id` varchar(64) NOT NULL,
  `schema_name` varchar(128) NOT NULL,
  `table_name` varchar(256) NOT NULL,
  `size_gb` double DEFAULT '0',
  `size_mb` double DEFAULT '0',
  `rows_count` bigint DEFAULT '0',
  `index_size_mb` double DEFAULT '0',
  `daily_inc_mb` double DEFAULT '0',
  `level` varchar(16) NOT NULL,
  `is_partitioned` int DEFAULT '0',
  `partition_count` int DEFAULT '0',
  `has_global_index` int DEFAULT '0',
  `shard_key` varchar(128) DEFAULT '',
  `inspection_date` varchar(32) NOT NULL,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_bt` (`connection_id`,`schema_name`,`table_name`(128),`inspection_date`),
  KEY `idx_bt_level` (`level`),
  KEY `idx_bt_connection` (`connection_id`),
  KEY `idx_bt_date` (`inspection_date`)
) ENGINE=InnoDB AUTO_INCREMENT=47 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: change_controls
CREATE TABLE `change_controls` (
  `id` int NOT NULL AUTO_INCREMENT,
  `connection_id` varchar(64) NOT NULL,
  `schema_name` varchar(128) NOT NULL,
  `table_name` varchar(256) NOT NULL,
  `table_level` varchar(16) NOT NULL,
  `change_type` varchar(32) NOT NULL,
  `change_sql` text NOT NULL,
  `reason` text,
  `stage` varchar(32) DEFAULT 'submitted',
  `backup_completed` int DEFAULT '0',
  `ticket_approved` int DEFAULT '0',
  `window_applied` int DEFAULT '0',
  `executed_at` varchar(32) DEFAULT NULL,
  `executed_by` varchar(64) DEFAULT '',
  `result` text,
  `post_check_status` varchar(32) DEFAULT '',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_cc_stage` (`stage`),
  KEY `idx_cc_level` (`table_level`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: cluster_inspection
CREATE TABLE `cluster_inspection` (
  `id` int NOT NULL AUTO_INCREMENT,
  `connection_id` varchar(64) DEFAULT '',
  `cluster_name` varchar(128) DEFAULT '',
  `inspect_date` varchar(32) DEFAULT '',
  `total_issues` int DEFAULT '0',
  `error_count` int DEFAULT '0',
  `warning_count` int DEFAULT '0',
  `info_count` int DEFAULT '0',
  `node_count` int DEFAULT '0',
  `summary_json` mediumtext,
  `created_by` varchar(64) DEFAULT '',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_ci_conn` (`connection_id`)
) ENGINE=InnoDB AUTO_INCREMENT=7 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: cluster_inspection_issue
CREATE TABLE `cluster_inspection_issue` (
  `id` int NOT NULL AUTO_INCREMENT,
  `inspection_id` int NOT NULL,
  `category` varchar(32) DEFAULT '',
  `severity` varchar(32) DEFAULT 'INFO',
  `node` varchar(128) DEFAULT '',
  `title` varchar(256) DEFAULT '',
  `detail` text,
  `metric_value` varchar(64) DEFAULT '',
  `threshold` varchar(64) DEFAULT '',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_cii` (`inspection_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: daily_inspection
CREATE TABLE `daily_inspection` (
  `id` int NOT NULL AUTO_INCREMENT,
  `inspect_date` varchar(16) NOT NULL,
  `connection_id` varchar(64) DEFAULT '',
  `node` varchar(128) DEFAULT '',
  `cpu_peak` double DEFAULT '0',
  `cpu_avg` double DEFAULT '0',
  `mem_peak` double DEFAULT '0',
  `conn_peak` double DEFAULT '0',
  `slow_query` double DEFAULT '0',
  `delay_peak` double DEFAULT '0',
  `disk_peak` double DEFAULT '0',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `cpu_cores` int DEFAULT '0',
  `mem_gb` double DEFAULT '0',
  `data_disk_gb` double DEFAULT '0',
  `log_disk_gb` double DEFAULT '0',
  `cpu_avg_daily` double DEFAULT '0',
  `mem_avg_daily` double DEFAULT '0',
  `proxy_req_total` bigint DEFAULT '0',
  `proxy_t_l` double DEFAULT '0',
  `proxy_t_m` double DEFAULT '0',
  `proxy_t_p` double DEFAULT '0',
  `proxy_t_n` double DEFAULT '0',
  `proxy_req_l` bigint DEFAULT '0',
  `proxy_req_m` bigint DEFAULT '0',
  `proxy_req_p` bigint DEFAULT '0',
  `proxy_req_n` bigint DEFAULT '0',
  `proxy_active_conn_peak` int DEFAULT '0',
  `proxy_conn_peak` int DEFAULT '0',
  `proxy_err_sql_sum` bigint DEFAULT '0',
  `report_context_json` text,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_daily` (`inspect_date`,`connection_id`,`node`),
  KEY `idx_daily_conn` (`connection_id`,`inspect_date`)
) ENGINE=InnoDB AUTO_INCREMENT=725 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: emergency_report
CREATE TABLE `emergency_report` (
  `id` int NOT NULL AUTO_INCREMENT,
  `connection_id` varchar(64) DEFAULT '',
  `actions` varchar(256) DEFAULT '',
  `report_json` mediumtext,
  `created_by` varchar(64) DEFAULT '',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_er_conn` (`connection_id`)
) ENGINE=InnoDB AUTO_INCREMENT=13 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: fingerprint_stats
CREATE TABLE `fingerprint_stats` (
  `id` int NOT NULL AUTO_INCREMENT,
  `connection_id` varchar(64) NOT NULL,
  `fingerprint` text NOT NULL,
  `sample_sql` text NOT NULL,
  `stat_date` varchar(32) NOT NULL,
  `exec_count` int DEFAULT '0',
  `total_time_ms` double DEFAULT '0',
  `avg_time_ms` double DEFAULT '0',
  `max_time_ms` double DEFAULT '0',
  `rows_examined` int DEFAULT '0',
  `rows_sent` int DEFAULT '0',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_fp` (`connection_id`,`fingerprint`(255),`stat_date`),
  KEY `idx_fp_connection` (`connection_id`),
  KEY `idx_fp_date` (`stat_date`),
  KEY `idx_fp_total_time` (`total_time_ms`),
  KEY `idx_fp_exec_count` (`exec_count`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: gate_audit_logs
CREATE TABLE `gate_audit_logs` (
  `id` int NOT NULL AUTO_INCREMENT,
  `project_id` varchar(64) NOT NULL,
  `audit_history_id` int DEFAULT NULL,
  `source` text,
  `passed` int NOT NULL,
  `error_count` int DEFAULT '0',
  `warning_count` int DEFAULT '0',
  `blocked_by` text,
  `detail` text,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `connection_id` varchar(64) DEFAULT NULL,
  `rule_set_id` varchar(64) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `audit_history_id` (`audit_history_id`),
  KEY `idx_gate_project` (`project_id`),
  KEY `idx_gate_passed` (`passed`),
  KEY `idx_gate_created` (`created_at`),
  CONSTRAINT `gate_audit_logs_ibfk_1` FOREIGN KEY (`audit_history_id`) REFERENCES `audit_history` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: gate_rules
CREATE TABLE `gate_rules` (
  `project_id` varchar(64) NOT NULL,
  `max_error_count` int DEFAULT '0',
  `max_warning_count` int DEFAULT '-1',
  `required_rules` text,
  `blocked_rules` text,
  `description` text,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`project_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='DEPRECATED(V1.4)：门禁绑定对象已由项目改为实例，见 instance_gate_rules';

-- SQL Object: CREATE TABLE
-- Table: gateway_log_reports
CREATE TABLE `gateway_log_reports` (
  `id` int NOT NULL AUTO_INCREMENT,
  `connection_id` varchar(64) DEFAULT '',
  `log_file_name` varchar(256) DEFAULT NULL,
  `log_type` varchar(64) DEFAULT 'interf',
  `total_queries` int DEFAULT '0',
  `slow_queries` int DEFAULT '0',
  `max_time_ms` double DEFAULT '0',
  `avg_time_ms` double DEFAULT '0',
  `report_html` longtext,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `report_context_json` text,
  `analysis_meta_json` mediumtext,
  `request_id` varchar(64) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_glr_conn` (`connection_id`)
) ENGINE=InnoDB AUTO_INCREMENT=10 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: gateway_report_tickets
CREATE TABLE `gateway_report_tickets` (
  `ticket_hash` char(64) NOT NULL COMMENT '票据 SHA-256（不存明文）',
  `report_id` int NOT NULL COMMENT '绑定的报告 ID',
  `username` varchar(64) NOT NULL DEFAULT '' COMMENT '签发者',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `expires_at` datetime NOT NULL COMMENT '过期时刻（90s 短时效）',
  `consumed_at` datetime DEFAULT NULL COMMENT '消费时刻（一次性语义）',
  PRIMARY KEY (`ticket_hash`),
  KEY `idx_grt_expires` (`expires_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: index_audit
CREATE TABLE `index_audit` (
  `id` int NOT NULL AUTO_INCREMENT,
  `connection_id` varchar(64) DEFAULT '',
  `database_filter` varchar(128) DEFAULT '',
  `total_tables` int DEFAULT '0',
  `total_indexes` int DEFAULT '0',
  `total_findings` int DEFAULT '0',
  `error_count` int DEFAULT '0',
  `warning_count` int DEFAULT '0',
  `info_count` int DEFAULT '0',
  `created_by` varchar(64) DEFAULT '',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_ia_conn` (`connection_id`)
) ENGINE=InnoDB AUTO_INCREMENT=17 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: index_audit_finding
CREATE TABLE `index_audit_finding` (
  `id` int NOT NULL AUTO_INCREMENT,
  `audit_id` int NOT NULL,
  `db_name` varchar(128) DEFAULT '',
  `table_name` varchar(128) DEFAULT '',
  `index_name` varchar(128) DEFAULT '',
  `finding_type` varchar(64) DEFAULT '',
  `severity` varchar(32) DEFAULT 'INFO',
  `detail` text,
  `suggestion` text,
  `metric` varchar(64) DEFAULT '',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `related_index_name` varchar(128) DEFAULT '',
  `index_columns` varchar(512) DEFAULT '',
  PRIMARY KEY (`id`),
  KEY `idx_iaf` (`audit_id`)
) ENGINE=InnoDB AUTO_INCREMENT=484 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: inspection_results
CREATE TABLE `inspection_results` (
  `id` int NOT NULL AUTO_INCREMENT,
  `task_id` int NOT NULL,
  `category` varchar(64) NOT NULL,
  `severity` varchar(32) NOT NULL,
  `schema_name` varchar(128) DEFAULT '',
  `table_name` varchar(256) DEFAULT '',
  `metric_name` varchar(128) DEFAULT '',
  `metric_value` text,
  `threshold` text,
  `message` text NOT NULL,
  `suggestion` text,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_ir_task` (`task_id`),
  KEY `idx_ir_severity` (`severity`),
  KEY `idx_ir_category` (`category`),
  CONSTRAINT `inspection_results_ibfk_1` FOREIGN KEY (`task_id`) REFERENCES `inspection_tasks` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=1003 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: inspection_tasks
CREATE TABLE `inspection_tasks` (
  `id` int NOT NULL AUTO_INCREMENT,
  `connection_id` varchar(64) NOT NULL,
  `inspection_type` varchar(32) NOT NULL,
  `status` varchar(32) DEFAULT 'pending',
  `started_at` varchar(32) DEFAULT NULL,
  `completed_at` varchar(32) DEFAULT NULL,
  `error_message` text,
  `report_path` text,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `report_context_json` text,
  PRIMARY KEY (`id`),
  KEY `idx_it_status` (`status`),
  KEY `idx_it_type` (`inspection_type`),
  KEY `idx_it_date` (`created_at`)
) ENGINE=InnoDB AUTO_INCREMENT=215 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: instance_gate_rules
CREATE TABLE `instance_gate_rules` (
  `connection_id` varchar(64) NOT NULL,
  `max_error_count` int NOT NULL DEFAULT '0',
  `max_warning_count` int NOT NULL DEFAULT '-1',
  `mode` varchar(16) NOT NULL DEFAULT 'enforce',
  `description` text,
  `updated_by` varchar(64) NOT NULL DEFAULT '',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`connection_id`),
  CONSTRAINT `fk_igr_connection` FOREIGN KEY (`connection_id`) REFERENCES `tdsql_connections` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: metadata_audit_jobs
CREATE TABLE `metadata_audit_jobs` (
  `id` char(32) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '服务端 UUID hex，所有产物文件名由它派生',
  `created_by` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '已认证服务端身份（前端不得覆盖）',
  `request_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '服务端请求关联号',
  `idempotency_key` char(32) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '客户端幂等键',
  `request_hash` char(64) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '规范化请求内容 hash（不含可变服务端状态）',
  `connection_id` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '实际目标连接',
  `db_name` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '最终实际库名',
  `request_json` mediumtext COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '规范化请求',
  `execution_context_json` mediumtext COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '冻结规则尺度/实例口径/报告来源/执行版本',
  `connection_fingerprint` char(64) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '非口令连接配置+密文凭据哈希（检测受理后变化）',
  `state` varchar(24) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'ACCEPTED/RUNNING/PUBLISHING/PUBLISHED/SUCCEEDED/STOPPING/FAILED/CANCELLED/RECOVERY_REQUIRED',
  `phase` varchar(24) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'WAITING/ENUMERATING/EXTRACTING/AUDITING/SERIALIZING/SNAPSHOTTING/PERSISTING/CLEANUP/DONE',
  `attempt_token` char(32) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '一次执行的随机 fencing token',
  `runner_id` varchar(128) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'runner 启动身份',
  `child_pid` bigint DEFAULT NULL COMMENT '子进程 PID',
  `child_start_identity` varchar(128) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '子进程操作系统启动身份（避免 PID 复用误杀）',
  `created_at` datetime(6) NOT NULL,
  `updated_at` datetime(6) NOT NULL,
  `started_at` datetime(6) DEFAULT NULL,
  `finished_at` datetime(6) DEFAULT NULL,
  `deadline_at` datetime(6) DEFAULT NULL,
  `heartbeat_at` datetime(6) DEFAULT NULL COMMENT '监督活性心跳（与 progress 区分）',
  `progress_at` datetime(6) DEFAULT NULL COMMENT '实际阶段进展时间',
  `progress_json` mediumtext COLLATE utf8mb4_unicode_ci COMMENT '计数/字节/摘要（不含全量结果）',
  `result_meta_json` mediumtext COLLATE utf8mb4_unicode_ci COMMENT '摘要/告警/artifact hash',
  `cancel_requested_at` datetime(6) DEFAULT NULL COMMENT '用户取消意图（不等于已 CANCELLED）',
  `error_code` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '脱敏稳定错误码',
  `error_message` varchar(1024) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '用户提示',
  `report_id` bigint DEFAULT NULL COMMENT 'audit_history 关联（唯一发布关系）',
  `snapshot_id` bigint DEFAULT NULL,
  `exit_code` int DEFAULT NULL COMMENT 'NULL=尚未确认',
  `cleanup_ok` tinyint DEFAULT NULL COMMENT 'NULL=尚未确认，不得默认 true',
  `artifact_state` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'NONE' COMMENT 'NONE/READY/MISSING/DELETED',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_metadata_job_idem` (`created_by`,`idempotency_key`),
  UNIQUE KEY `uq_metadata_job_report` (`report_id`),
  KEY `idx_metadata_job_creator` (`created_by`,`created_at`,`id`),
  KEY `idx_metadata_job_state` (`state`,`updated_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='在线元数据审核任务（v1.6.3.5）';

-- SQL Object: CREATE TABLE
-- Table: metadata_audit_slot
CREATE TABLE `metadata_audit_slot` (
  `id` tinyint NOT NULL COMMENT '固定 1，所有 Web worker 的原子受理锁',
  `active_job_id` char(32) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '唯一占用中的 job',
  `runner_id` varchar(128) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '当前 runner 服务身份',
  `runner_heartbeat_at` datetime(6) DEFAULT NULL COMMENT 'runner 监督心跳',
  `accepting` tinyint NOT NULL DEFAULT '0' COMMENT '初始不接受，runner 恢复校验后才开放',
  `storage_instance_id` char(32) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '持久产物目录安装标识',
  `updated_at` datetime(6) NOT NULL,
  `artifact_used_bytes` bigint NOT NULL DEFAULT '0' COMMENT '已核验产物总量',
  `storage_checked_at` datetime(6) DEFAULT NULL COMMENT '最近存储核验时间；NULL/过期拒绝新受理',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='元数据审核全局受理槽位（固定单行 id=1，v1.6.3.5）';

-- SQL Object: CREATE TABLE
-- Table: operation_logs
CREATE TABLE `operation_logs` (
  `id` int NOT NULL AUTO_INCREMENT,
  `operator` varchar(64) DEFAULT '',
  `operation_type` varchar(64) NOT NULL,
  `target_type` varchar(64) DEFAULT '',
  `target_id` varchar(128) DEFAULT '',
  `detail` text,
  `ip_address` varchar(64) DEFAULT '',
  `user_agent` text,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_log_operator` (`operator`),
  KEY `idx_log_type` (`operation_type`),
  KEY `idx_log_created` (`created_at`),
  KEY `idx_log_type_ip_time` (`operation_type`,`ip_address`,`created_at`)
) ENGINE=InnoDB AUTO_INCREMENT=8067 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: optimization_records
CREATE TABLE `optimization_records` (
  `id` int NOT NULL AUTO_INCREMENT,
  `slow_query_id` int DEFAULT NULL,
  `connection_id` varchar(64) NOT NULL,
  `original_sql` text NOT NULL,
  `optimized_sql` text NOT NULL,
  `before_type` varchar(64) DEFAULT '',
  `before_key` text,
  `before_rows` int DEFAULT '0',
  `before_extra` text,
  `before_time_ms` double DEFAULT '0',
  `after_type` varchar(64) DEFAULT '',
  `after_key` text,
  `after_rows` int DEFAULT '0',
  `after_extra` text,
  `after_time_ms` double DEFAULT '0',
  `improvement` varchar(128) DEFAULT '',
  `improvement_detail` text,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_opt_slow_query` (`slow_query_id`),
  KEY `idx_opt_improvement` (`improvement`),
  CONSTRAINT `optimization_records_ibfk_1` FOREIGN KEY (`slow_query_id`) REFERENCES `slow_queries` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: partition_watermarks
CREATE TABLE `partition_watermarks` (
  `id` int NOT NULL AUTO_INCREMENT,
  `connection_id` varchar(64) NOT NULL,
  `schema_name` varchar(128) NOT NULL,
  `table_name` varchar(256) NOT NULL,
  `partition_count` int NOT NULL,
  `watermark_percent` double DEFAULT '0',
  `status` varchar(32) NOT NULL,
  `check_date` varchar(32) NOT NULL,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_pw` (`connection_id`,`schema_name`,`table_name`(128),`check_date`),
  KEY `idx_pw_status` (`status`),
  KEY `idx_pw_date` (`check_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: projects
CREATE TABLE `projects` (
  `project_id` varchar(64) NOT NULL,
  `project_name` varchar(128) NOT NULL,
  `tdsql_connection_id` varchar(64) DEFAULT '',
  `rule_set_id` varchar(64) DEFAULT 'default',
  `gate_rule_id` varchar(64) DEFAULT 'default',
  `gitlab_project_id` int DEFAULT NULL,
  `gitlab_url` text,
  `description` text,
  `status` varchar(32) DEFAULT 'active',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`project_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: retention_policies
CREATE TABLE `retention_policies` (
  `table_name` varchar(64) NOT NULL,
  `retention_days` int NOT NULL,
  `enabled` int DEFAULT '1',
  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`table_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: role_permissions
CREATE TABLE `role_permissions` (
  `role_id` varchar(32) NOT NULL,
  `menu_key` varchar(64) NOT NULL,
  `visible` int DEFAULT '1',
  PRIMARY KEY (`role_id`,`menu_key`),
  KEY `idx_rp_role` (`role_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: roles
CREATE TABLE `roles` (
  `role_id` varchar(32) NOT NULL,
  `role_name` varchar(64) NOT NULL,
  `is_builtin` int DEFAULT '0',
  `description` text,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`role_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: rule_configs
CREATE TABLE `rule_configs` (
  `rule_id` varchar(64) NOT NULL,
  `category` varchar(64) NOT NULL,
  `severity` varchar(32) NOT NULL,
  `description` text NOT NULL,
  `spec_source` text,
  `fix_suggestion` text,
  `enabled` int DEFAULT '1',
  `is_builtin` int DEFAULT '1',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`rule_id`),
  KEY `idx_rule_category` (`category`),
  KEY `idx_rule_enabled` (`enabled`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: rule_set_items
CREATE TABLE `rule_set_items` (
  `rule_set_id` varchar(64) NOT NULL,
  `rule_id` varchar(64) NOT NULL,
  `enabled` int DEFAULT '1',
  `severity_override` varchar(32) DEFAULT NULL,
  PRIMARY KEY (`rule_set_id`,`rule_id`),
  KEY `idx_rsi_set` (`rule_set_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: rule_sets
CREATE TABLE `rule_sets` (
  `id` varchar(64) NOT NULL,
  `name` varchar(128) NOT NULL,
  `description` text,
  `is_builtin` int DEFAULT '0',
  `created_by` varchar(64) DEFAULT '',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: rule_whitelist
CREATE TABLE `rule_whitelist` (
  `id` int NOT NULL AUTO_INCREMENT,
  `rule_id` varchar(64) NOT NULL,
  `table_pattern` text,
  `sql_pattern` text,
  `reason` text,
  `created_by` varchar(64) DEFAULT '',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_whitelist_rule` (`rule_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: scan_compare_reports
CREATE TABLE `scan_compare_reports` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `module` varchar(32) NOT NULL,
  `connection_id` varchar(64) NOT NULL DEFAULT '',
  `connection_name` varchar(256) NOT NULL DEFAULT '',
  `db_name` varchar(128) NOT NULL DEFAULT '',
  `base_snapshot_id` bigint NOT NULL,
  `target_snapshot_id` bigint NOT NULL,
  `base_scan_at` datetime DEFAULT NULL,
  `target_scan_at` datetime DEFAULT NULL,
  `title` varchar(512) NOT NULL DEFAULT '',
  `base_total` int NOT NULL DEFAULT '0',
  `target_total` int NOT NULL DEFAULT '0',
  `fixed_count` int NOT NULL DEFAULT '0',
  `new_count` int NOT NULL DEFAULT '0',
  `remain_count` int NOT NULL DEFAULT '0',
  `changed_count` int NOT NULL DEFAULT '0',
  `fix_rate` double NOT NULL DEFAULT '0',
  `summary_json` longtext COMMENT '汇总，不含明细',
  `created_by` varchar(64) NOT NULL DEFAULT '',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_cmp_query` (`module`,`connection_id`,`created_at`),
  KEY `idx_cmp_snap` (`base_snapshot_id`,`target_snapshot_id`)
) ENGINE=InnoDB AUTO_INCREMENT=26 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: scan_schedules
CREATE TABLE `scan_schedules` (
  `id` int NOT NULL AUTO_INCREMENT,
  `connection_id` varchar(64) NOT NULL,
  `source` varchar(32) DEFAULT 'digest',
  `cron_hour` int DEFAULT '2',
  `cron_minute` int DEFAULT '0',
  `limit_rows` int DEFAULT '100',
  `min_time` double DEFAULT '1',
  `enabled` int DEFAULT '1',
  `last_run_at` varchar(32) DEFAULT NULL,
  `last_run_status` varchar(32) DEFAULT '',
  `created_by` varchar(64) DEFAULT '',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_sched_conn` (`connection_id`),
  KEY `idx_sched_enabled` (`enabled`)
) ENGINE=InnoDB AUTO_INCREMENT=5 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: scan_snapshots
CREATE TABLE `scan_snapshots` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `module` varchar(32) NOT NULL COMMENT 'schema_audit|slow_scan|bigtable',
  `biz_ref_id` varchar(64) NOT NULL DEFAULT '' COMMENT '源记录ID',
  `connection_id` varchar(64) NOT NULL DEFAULT '',
  `connection_name` varchar(256) NOT NULL DEFAULT '',
  `db_name` varchar(128) NOT NULL DEFAULT '',
  `scan_label` varchar(512) NOT NULL DEFAULT '' COMMENT '展示名',
  `scan_started_at` datetime DEFAULT NULL,
  `scan_finished_at` datetime NOT NULL COMMENT '比对方向判定依据',
  `time_window_start` varchar(32) NOT NULL DEFAULT '' COMMENT '慢SQL可比性',
  `time_window_end` varchar(32) NOT NULL DEFAULT '',
  `object_total` int NOT NULL DEFAULT '0',
  `issue_total` int NOT NULL DEFAULT '0',
  `error_count` int NOT NULL DEFAULT '0',
  `warning_count` int NOT NULL DEFAULT '0',
  `fingerprint_algo` varchar(16) NOT NULL DEFAULT 'v1',
  `schema_version` int NOT NULL DEFAULT '1',
  `truncated` tinyint NOT NULL DEFAULT '0',
  `truncated_count` int NOT NULL DEFAULT '0',
  `snapshot_json` longtext COMMENT '快照主体',
  `snapshot_size` int NOT NULL DEFAULT '0',
  `source_kind` varchar(16) NOT NULL DEFAULT 'live' COMMENT 'live=扫描实时生成, rebuild=回填',
  `created_by` varchar(64) NOT NULL DEFAULT '',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `rule_set_id` varchar(64) DEFAULT NULL,
  `instance_type` varchar(16) DEFAULT NULL,
  `report_context_json` text,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_snap_module_biz` (`module`,`biz_ref_id`),
  KEY `idx_snap_query` (`module`,`connection_id`,`scan_finished_at`),
  KEY `idx_snap_db` (`module`,`db_name`),
  KEY `idx_snap_created` (`created_at`)
) ENGINE=InnoDB AUTO_INCREMENT=118 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: scan_tasks
CREATE TABLE `scan_tasks` (
  `id` int NOT NULL AUTO_INCREMENT,
  `task_name` varchar(256) NOT NULL,
  `source` varchar(32) DEFAULT 'manual',
  `db_name` varchar(128) DEFAULT '',
  `connection_id` varchar(64) DEFAULT '',
  `connection_name` varchar(256) DEFAULT '',
  `time_window_start` varchar(32) DEFAULT '',
  `time_window_end` varchar(32) DEFAULT '',
  `total_fetched` int DEFAULT '0',
  `total_analyzed` int DEFAULT '0',
  `status` varchar(32) DEFAULT 'completed',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `created_by` varchar(64) DEFAULT '',
  `report_context_json` text,
  PRIMARY KEY (`id`),
  KEY `idx_scan_task_db` (`db_name`),
  KEY `idx_scan_task_source` (`source`)
) ENGINE=InnoDB AUTO_INCREMENT=161 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: scheduler_lease
CREATE TABLE `scheduler_lease` (
  `id` int NOT NULL,
  `holder` varchar(128) NOT NULL,
  `expires_at` varchar(32) NOT NULL,
  PRIMARY KEY (`id`),
  CONSTRAINT `chk_lease_id` CHECK ((`id` = 1))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: schema_diff
CREATE TABLE `schema_diff` (
  `id` int NOT NULL AUTO_INCREMENT,
  `left_conn` varchar(64) DEFAULT '',
  `right_conn` varchar(64) DEFAULT '',
  `databases_filter` varchar(256) DEFAULT '',
  `total_items` int DEFAULT '0',
  `error_count` int DEFAULT '0',
  `warning_count` int DEFAULT '0',
  `info_count` int DEFAULT '0',
  `created_by` varchar(64) DEFAULT '',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=25 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: schema_diff_item
CREATE TABLE `schema_diff_item` (
  `id` int NOT NULL AUTO_INCREMENT,
  `diff_id` int NOT NULL,
  `db_name` varchar(128) DEFAULT '',
  `table_name` varchar(128) DEFAULT '',
  `object_name` varchar(128) DEFAULT '',
  `diff_type` varchar(64) DEFAULT '',
  `severity` varchar(32) DEFAULT 'INFO',
  `left_value` text,
  `right_value` text,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_sdi` (`diff_id`)
) ENGINE=InnoDB AUTO_INCREMENT=227 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: schema_migrations
CREATE TABLE `schema_migrations` (
  `version_key` varchar(128) NOT NULL,
  `checksum` varchar(64) NOT NULL,
  `applied_at` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`version_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: schema_version
CREATE TABLE `schema_version` (
  `key` varchar(128) NOT NULL,
  `value` text NOT NULL,
  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: server_daily_inspection
CREATE TABLE `server_daily_inspection` (
  `id` int NOT NULL AUTO_INCREMENT,
  `inspect_date` varchar(16) NOT NULL,
  `connection_id` varchar(64) DEFAULT '',
  `ip` varchar(128) DEFAULT '',
  `hostname` varchar(128) DEFAULT '',
  `cpu_peak` double DEFAULT '0',
  `cpu_avg` double DEFAULT '0',
  `mem_used_str` varchar(128) DEFAULT '',
  `mem_pct` double DEFAULT '0',
  `disk_root_pct` double DEFAULT '0',
  `disk_data_str` varchar(512) DEFAULT '',
  `disk_backup_pct` varchar(32) DEFAULT '',
  `read_await_max` double DEFAULT '0',
  `read_await_dev` varchar(128) DEFAULT '',
  `write_await_max` double DEFAULT '0',
  `write_await_dev` varchar(128) DEFAULT '',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `report_context_json` text,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_srv_daily` (`inspect_date`,`connection_id`,`ip`),
  KEY `idx_srv_daily_conn` (`connection_id`,`inspect_date`)
) ENGINE=InnoDB AUTO_INCREMENT=175 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: slow_log_collection_runs
CREATE TABLE `slow_log_collection_runs` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `source_id` bigint NOT NULL,
  `trigger_type` varchar(16) NOT NULL,
  `requested_by` varchar(64) NOT NULL DEFAULT '',
  `status` varchar(32) NOT NULL DEFAULT 'running',
  `started_at` datetime(6) NOT NULL,
  `finished_at` datetime(6) DEFAULT NULL,
  `nodes_total` int NOT NULL DEFAULT '0',
  `nodes_success` int NOT NULL DEFAULT '0',
  `files_seen` bigint NOT NULL DEFAULT '0',
  `bytes_read` bigint NOT NULL DEFAULT '0',
  `blocks_parsed` bigint NOT NULL DEFAULT '0',
  `events_inserted` bigint NOT NULL DEFAULT '0',
  `events_duplicate` bigint NOT NULL DEFAULT '0',
  `events_filtered` bigint NOT NULL DEFAULT '0',
  `incomplete_tail_count` bigint NOT NULL DEFAULT '0',
  `parse_error_count` bigint NOT NULL DEFAULT '0',
  `error_code` varchar(64) NOT NULL DEFAULT '',
  `error_detail` text NOT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  KEY `idx_slcr_source_started` (`source_id`,`started_at`),
  KEY `idx_slcr_status_started` (`status`,`started_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: slow_log_cursors
CREATE TABLE `slow_log_cursors` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `source_node_id` bigint NOT NULL,
  `file_identity` varchar(256) NOT NULL,
  `generation` int NOT NULL DEFAULT '0',
  `file_label` varchar(512) NOT NULL DEFAULT '',
  `cursor_offset` bigint NOT NULL DEFAULT '0',
  `last_file_size` bigint NOT NULL DEFAULT '0',
  `anchor_start_offset` bigint NOT NULL DEFAULT '0',
  `anchor_length` int NOT NULL DEFAULT '0',
  `anchor_sha256` char(64) NOT NULL DEFAULT '',
  `last_event_time` datetime(6) DEFAULT NULL,
  `status` varchar(32) NOT NULL DEFAULT 'active',
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_slc_file_generation` (`source_node_id`,`file_identity`,`generation`),
  KEY `idx_slc_node_status` (`source_node_id`,`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: slow_log_events
CREATE TABLE `slow_log_events` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `source_id` bigint NOT NULL,
  `source_node_id` bigint NOT NULL,
  `origin_file_identity` varchar(256) NOT NULL,
  `origin_generation` int NOT NULL DEFAULT '0',
  `origin_offset_start` bigint NOT NULL,
  `origin_offset_end` bigint NOT NULL,
  `event_time` datetime(6) NOT NULL,
  `event_time_source` varchar(32) NOT NULL DEFAULT 'proxy_log_time',
  `db_name` varchar(256) NOT NULL DEFAULT '',
  `client_user` varchar(512) NOT NULL DEFAULT '',
  `client_host` varchar(512) NOT NULL DEFAULT '',
  `backend_host` varchar(512) NOT NULL DEFAULT '',
  `thread_id` varchar(128) NOT NULL DEFAULT '',
  `query_time_us` bigint NOT NULL DEFAULT '0',
  `lock_time_us` bigint NOT NULL DEFAULT '0',
  `rows_sent` bigint NOT NULL DEFAULT '0',
  `rows_examined` bigint NOT NULL DEFAULT '0',
  `statement_type` varchar(16) NOT NULL DEFAULT 'OTHER',
  `sql_fingerprint` char(64) NOT NULL,
  `sql_template` text NOT NULL,
  `sql_template_truncated` tinyint NOT NULL DEFAULT '0',
  `sql_template_original_bytes` int NOT NULL DEFAULT '0',
  `parse_version` varchar(32) NOT NULL,
  `extra_json` text NOT NULL,
  `collected_at` datetime(6) NOT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_sle_origin` (`source_node_id`,`origin_file_identity`,`origin_generation`,`origin_offset_start`),
  KEY `idx_sle_source_time` (`source_id`,`event_time`),
  KEY `idx_sle_node_time` (`source_node_id`,`event_time`),
  KEY `idx_sle_fingerprint_time` (`sql_fingerprint`,`event_time`),
  KEY `idx_sle_db_time` (`db_name`,`event_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: slow_log_node_probe_files
CREATE TABLE `slow_log_node_probe_files` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `source_node_id` bigint NOT NULL,
  `ssh_host_key_fingerprint` varchar(128) NOT NULL,
  `storage_identity` varchar(256) NOT NULL DEFAULT '',
  `file_identity` varchar(256) NOT NULL,
  `file_label` varchar(512) NOT NULL DEFAULT '',
  `observed_at` datetime(6) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_slnpf_node_file` (`source_node_id`,`file_identity`),
  KEY `idx_slnpf_host_file` (`ssh_host_key_fingerprint`,`file_identity`),
  KEY `idx_slnpf_storage_file` (`storage_identity`,`file_identity`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: slow_log_source_nodes
CREATE TABLE `slow_log_source_nodes` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `source_id` bigint NOT NULL,
  `node_key` varchar(64) NOT NULL,
  `display_name` varchar(128) NOT NULL,
  `ssh_host` varchar(255) NOT NULL,
  `ssh_port` int NOT NULL DEFAULT '22',
  `host_key_alias` varchar(128) NOT NULL,
  `ssh_host_key_fingerprint` varchar(128) NOT NULL DEFAULT '',
  `remote_source_key` varchar(64) NOT NULL,
  `declared_path_template` text NOT NULL,
  `parser_profile` varchar(64) NOT NULL,
  `enabled` tinyint NOT NULL DEFAULT '0',
  `last_probe_at` datetime DEFAULT NULL,
  `last_probe_status` varchar(32) NOT NULL DEFAULT 'never',
  `last_probe_detail` varchar(512) NOT NULL DEFAULT '',
  `last_success_at` datetime DEFAULT NULL,
  `last_error_code` varchar(64) NOT NULL DEFAULT '',
  `last_error_detail` varchar(512) NOT NULL DEFAULT '',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_slsn_source_node` (`source_id`,`node_key`),
  KEY `idx_slsn_source_enabled` (`source_id`,`enabled`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: slow_log_sources
CREATE TABLE `slow_log_sources` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `source_key` varchar(64) NOT NULL,
  `connection_id` varchar(64) NOT NULL,
  `display_name` varchar(128) NOT NULL,
  `transport` varchar(32) NOT NULL DEFAULT 'ssh_exporter_v1',
  `timezone` varchar(64) NOT NULL DEFAULT 'Asia/Shanghai',
  `poll_interval_seconds` int NOT NULL DEFAULT '60',
  `max_batch_bytes` int NOT NULL DEFAULT '8388608',
  `max_events_per_batch` int NOT NULL DEFAULT '2000',
  `max_run_seconds` int NOT NULL DEFAULT '25',
  `lag_alert_seconds` int NOT NULL DEFAULT '600',
  `initial_position` varchar(16) NOT NULL DEFAULT 'tail',
  `initial_lookback_seconds` int NOT NULL DEFAULT '300',
  `min_query_time_ms` bigint NOT NULL DEFAULT '1000',
  `credential_ref` varchar(64) NOT NULL DEFAULT '',
  `known_hosts_ref` varchar(64) NOT NULL DEFAULT '',
  `enabled` tinyint NOT NULL DEFAULT '0',
  `last_success_at` datetime DEFAULT NULL,
  `last_backlog_bytes` bigint NOT NULL DEFAULT '0',
  `last_lag_seconds` bigint DEFAULT NULL,
  `last_error_code` varchar(64) NOT NULL DEFAULT '',
  `last_error_detail` varchar(512) NOT NULL DEFAULT '',
  `lease_holder` varchar(128) NOT NULL DEFAULT '',
  `lease_expires_at` datetime DEFAULT NULL,
  `created_by` varchar(64) NOT NULL DEFAULT '',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_sls_source_key` (`source_key`),
  KEY `idx_sls_connection` (`connection_id`),
  KEY `idx_sls_due` (`enabled`,`last_success_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: slow_queries
CREATE TABLE `slow_queries` (
  `id` int NOT NULL AUTO_INCREMENT,
  `fingerprint` text NOT NULL,
  `sql_text` text NOT NULL,
  `normalized_sql` text,
  `db_name` varchar(128) DEFAULT '',
  `set_id` varchar(512) DEFAULT '',
  `connection_id` varchar(64) DEFAULT '',
  `project_id` varchar(64) DEFAULT '',
  `client_user` text,
  `client_host` text,
  `exec_count` int DEFAULT '0',
  `total_time_ms` double DEFAULT '0',
  `avg_time_ms` double DEFAULT '0',
  `max_time_ms` double DEFAULT '0',
  `rows_examined` int DEFAULT '0',
  `rows_sent` int DEFAULT '0',
  `lock_time_ms` double DEFAULT '0',
  `first_seen` varchar(32) DEFAULT NULL,
  `last_seen` varchar(32) DEFAULT NULL,
  `problem_type` varchar(256) DEFAULT '',
  `severity` varchar(32) DEFAULT 'INFO',
  `root_cause` text,
  `suggestion` text,
  `optimized_sql` text,
  `distributed_analysis` text,
  `index_suggestions` text,
  `rewrite_suggestions` text,
  `status` varchar(32) DEFAULT 'pending',
  `assigned_to` varchar(64) DEFAULT '',
  `scan_task_id` int DEFAULT NULL,
  `analysis_json` text,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `rows_affected` bigint DEFAULT '0',
  `explain_plan` text,
  `explain_issues` varchar(1000) DEFAULT '',
  `involved_tables` varchar(512) DEFAULT '',
  `table_stats` text,
  `table_schema_ddl` text,
  `index_details` text,
  `redundant_indexes` varchar(1000) DEFAULT '',
  `stats_update_info` text,
  `stats_expired` varchar(1000) DEFAULT '',
  `scan_efficiency` varchar(64) DEFAULT '',
  PRIMARY KEY (`id`),
  KEY `idx_slow_fingerprint` (`fingerprint`(255)),
  KEY `idx_slow_db` (`db_name`),
  KEY `idx_slow_set_id` (`set_id`),
  KEY `idx_slow_status` (`status`),
  KEY `idx_slow_connection` (`connection_id`),
  KEY `idx_slow_project` (`project_id`),
  KEY `idx_slow_last_seen` (`last_seen`),
  KEY `idx_slow_severity` (`severity`)
) ENGINE=InnoDB AUTO_INCREMENT=4083 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: system_config
CREATE TABLE `system_config` (
  `config_key` varchar(64) NOT NULL,
  `config_value` varchar(256) DEFAULT '',
  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`config_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: table_type_stat
CREATE TABLE `table_type_stat` (
  `id` int NOT NULL AUTO_INCREMENT,
  `connection_id` varchar(64) DEFAULT '',
  `database_filter` varchar(128) DEFAULT '',
  `instance_type` varchar(32) DEFAULT '',
  `type_source` varchar(32) DEFAULT '',
  `database_count` int DEFAULT '0',
  `total_tables` int DEFAULT '0',
  `shard_tables` int DEFAULT '0',
  `broadcast_tables` int DEFAULT '0',
  `single_tables` int DEFAULT '0',
  `baseline_tables` int DEFAULT '0',
  `subpartition_tables` int DEFAULT '0',
  `failed_databases` int DEFAULT '0',
  `skipped_databases` int DEFAULT '0',
  `overlap_count` int DEFAULT '0',
  `warnings_json` mediumtext,
  `created_by` varchar(64) DEFAULT '',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `secondary_partition_main_tables` int DEFAULT NULL,
  `secondary_partition_check_state` varchar(24) NOT NULL DEFAULT 'LEGACY',
  `secondary_partition_candidates` int DEFAULT NULL,
  `secondary_partition_checked` int DEFAULT NULL,
  `secondary_partition_unknown` int DEFAULT NULL,
  `secondary_partition_unchecked` int DEFAULT NULL,
  `secondary_partition_inventory_state` varchar(24) NOT NULL DEFAULT 'LEGACY',
  `secondary_partition_outside_shard` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_tts_conn` (`connection_id`),
  KEY `idx_tts_created` (`created_at`)
) ENGINE=InnoDB AUTO_INCREMENT=20 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: table_type_stat_item
CREATE TABLE `table_type_stat_item` (
  `id` int NOT NULL AUTO_INCREMENT,
  `stat_id` int NOT NULL,
  `db_name` varchar(128) DEFAULT '',
  `total_tables` int DEFAULT '0',
  `shard_tables` int DEFAULT '0',
  `broadcast_tables` int DEFAULT '0',
  `single_tables` int DEFAULT '0',
  `baseline_tables` int DEFAULT '0',
  `subpartition_tables` int DEFAULT '0',
  `status` varchar(16) DEFAULT 'OK',
  `detail` varchar(512) DEFAULT '',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `secondary_partition_main_tables` int DEFAULT NULL,
  `secondary_partition_check_state` varchar(24) NOT NULL DEFAULT 'LEGACY',
  `secondary_partition_candidates` int DEFAULT NULL,
  `secondary_partition_checked` int DEFAULT NULL,
  `secondary_partition_unknown` int DEFAULT NULL,
  `secondary_partition_unchecked` int DEFAULT NULL,
  `secondary_partition_inventory_state` varchar(24) NOT NULL DEFAULT 'LEGACY',
  `secondary_partition_outside_shard` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_ttsi` (`stat_id`)
) ENGINE=InnoDB AUTO_INCREMENT=873 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: tdsql_connections
CREATE TABLE `tdsql_connections` (
  `id` varchar(64) NOT NULL,
  `name` varchar(255) NOT NULL,
  `host` varchar(256) NOT NULL,
  `port` int NOT NULL,
  `username` varchar(64) NOT NULL,
  `password_encrypted` text NOT NULL,
  `database` varchar(128) DEFAULT '',
  `charset` varchar(32) DEFAULT 'utf8mb4',
  `is_default` int DEFAULT '0',
  `is_distributed` int DEFAULT '1',
  `description` text,
  `status` varchar(32) DEFAULT 'disconnected',
  `last_connected_at` varchar(32) DEFAULT NULL,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `set_list` text,
  `monitor_host` varchar(128) DEFAULT '',
  `monitor_port` int DEFAULT '15001',
  `monitor_user` varchar(128) DEFAULT '',
  `monitor_password_encrypted` text,
  `monitor_db` varchar(128) DEFAULT 'tdsqlpcloud_monitor',
  `detected_instance_type` varchar(16) DEFAULT NULL,
  `instance_type_detected_at` datetime DEFAULT NULL,
  `instance_type_probe_error` varchar(512) NOT NULL DEFAULT '',
  `zk_instance_kind` varchar(16) DEFAULT NULL,
  `zk_instance_id` varchar(64) NOT NULL DEFAULT '',
  `zk_synced_at` datetime DEFAULT NULL,
  `instance_type_locked` tinyint NOT NULL DEFAULT '0',
  `instance_type_locked_value` varchar(16) NOT NULL DEFAULT '',
  `zk_import_batch_id` varchar(36) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_conn_endpoint` (`host`,`port`,`database`),
  KEY `idx_conn_default` (`is_default`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: tool_runs
CREATE TABLE `tool_runs` (
  `run_id` varchar(64) NOT NULL,
  `tool_name` varchar(64) NOT NULL,
  `target_connection` varchar(64) DEFAULT '',
  `params_json` text,
  `status` varchar(32) DEFAULT 'RUNNING',
  `error_message` text,
  `created_by` varchar(64) NOT NULL,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `finished_at` datetime DEFAULT NULL,
  PRIMARY KEY (`run_id`),
  KEY `idx_tr_status` (`status`),
  KEY `idx_tr_user` (`created_by`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: users
CREATE TABLE `users` (
  `id` int NOT NULL AUTO_INCREMENT,
  `username` varchar(64) NOT NULL,
  `display_name` varchar(128) DEFAULT '',
  `role` varchar(32) NOT NULL DEFAULT 'developer',
  `password_hash` text NOT NULL,
  `salt` text NOT NULL,
  `status` varchar(16) DEFAULT 'active',
  `must_change_password` int DEFAULT '0',
  `failed_attempts` int DEFAULT '0',
  `locked_until` varchar(32) DEFAULT NULL,
  `last_login_at` varchar(32) DEFAULT NULL,
  `created_by` varchar(64) DEFAULT '',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `token_version` int NOT NULL DEFAULT '0',
  PRIMARY KEY (`id`),
  UNIQUE KEY `username` (`username`),
  KEY `idx_users_role` (`role`),
  KEY `idx_users_status` (`status`)
) ENGINE=InnoDB AUTO_INCREMENT=188 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: zk_discovery_config
CREATE TABLE `zk_discovery_config` (
  `config_id` tinyint unsigned NOT NULL,
  `servers` text NOT NULL,
  `root_path` varchar(512) NOT NULL DEFAULT '/tdsqlzk',
  `driver` varchar(16) NOT NULL DEFAULT 'kazoo',
  `zkcli_path` varchar(1024) NOT NULL DEFAULT '',
  `proxy_mode` varchar(16) NOT NULL DEFAULT 'first',
  `default_database` varchar(128) NOT NULL DEFAULT 'ALL',
  `endpoint_map_json` text NOT NULL,
  `auth_username` varchar(128) NOT NULL DEFAULT '',
  `auth_password_encrypted` text NOT NULL,
  `updated_by` varchar(64) NOT NULL DEFAULT '',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `octet_rules_json` text,
  `monitor_host` varchar(255) NOT NULL DEFAULT '',
  `monitor_port` int NOT NULL DEFAULT '0',
  `monitor_user` varchar(128) NOT NULL DEFAULT '',
  `monitor_db` varchar(128) NOT NULL DEFAULT '',
  `monitor_password_encrypted` text,
  `business_username` varchar(128) NOT NULL DEFAULT '',
  `business_password_encrypted` text,
  `name_query_hint` varchar(64) NOT NULL DEFAULT '',
  `enrich_enabled` tinyint NOT NULL DEFAULT '1',
  PRIMARY KEY (`config_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: zk_discovery_import_batches
CREATE TABLE `zk_discovery_import_batches` (
  `id` varchar(36) NOT NULL,
  `discovery_id` varchar(64) NOT NULL,
  `operator_username` varchar(128) NOT NULL,
  `selected_instance_count` int NOT NULL,
  `candidate_count` int NOT NULL,
  `created_count` int NOT NULL DEFAULT '0',
  `skipped_count` int NOT NULL DEFAULT '0',
  `failed_count` int NOT NULL DEFAULT '0',
  `status` varchar(32) NOT NULL,
  `failure_summary` text,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `completed_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_zk_import_batch_created` (`created_at`),
  KEY `idx_zk_import_batch_operator` (`operator_username`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: zk_discovery_import_items
CREATE TABLE `zk_discovery_import_items` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `batch_id` varchar(36) NOT NULL,
  `source_instance_id` varchar(128) NOT NULL,
  `instance_kind` varchar(32) NOT NULL,
  `instance_type` varchar(32) NOT NULL,
  `primary_proxy_host` varchar(255) NOT NULL,
  `primary_proxy_port` int NOT NULL,
  `set_list` text NOT NULL,
  `resolved_instance_name` varchar(255) DEFAULT NULL,
  `database_name` varchar(255) DEFAULT NULL,
  `generated_connection_name` varchar(255) DEFAULT NULL,
  `connection_id` varchar(64) DEFAULT NULL,
  `result_status` varchar(32) NOT NULL,
  `failure_code` varchar(64) DEFAULT NULL,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `name_source` varchar(32) NOT NULL DEFAULT '',
  `databases_source` varchar(32) NOT NULL DEFAULT '',
  PRIMARY KEY (`id`),
  KEY `idx_zk_import_item_batch` (`batch_id`),
  KEY `idx_zk_import_item_instance` (`source_instance_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: zk_discovery_previews
CREATE TABLE `zk_discovery_previews` (
  `preview_id` varchar(64) NOT NULL,
  `discovery_id` varchar(64) NOT NULL,
  `owner` varchar(64) NOT NULL,
  `expires_at` double NOT NULL,
  `rows_json` longtext NOT NULL,
  `business_enc` text,
  `monitor_enc` text,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`preview_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- SQL Object: CREATE TABLE
-- Table: zk_discovery_sessions
CREATE TABLE `zk_discovery_sessions` (
  `discovery_id` varchar(64) NOT NULL,
  `owner` varchar(64) NOT NULL,
  `is_mock` int NOT NULL DEFAULT '0',
  `expires_at` double NOT NULL,
  `items_json` longtext NOT NULL,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`discovery_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
