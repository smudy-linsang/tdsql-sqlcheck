-- ============================================================================
-- TDSQL SQL审核平台 - MySQL 8.0 元数据库全量初始化与建表脚本 (v1.6.3.0)
-- 适用环境: 银河麒麟 V10 SP3 / 海光 CPU (x86_64) 本地 MySQL 8.0.28
-- 数据库名称: tdsql_sqlcheck
-- 默认应用账号: sqlcheck_app / SqlCheck_App_2026!
-- ============================================================================

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- ----------------------------------------------------------------------------
-- 第一步：创建元数据库与应用专用账号
-- ----------------------------------------------------------------------------
CREATE DATABASE IF NOT EXISTS `tdsql_sqlcheck` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
USE `tdsql_sqlcheck`;

CREATE USER IF NOT EXISTS 'sqlcheck_app'@'%' IDENTIFIED BY 'SqlCheck_App_2026!';
CREATE USER IF NOT EXISTS 'sqlcheck_app'@'localhost' IDENTIFIED BY 'SqlCheck_App_2026!';
CREATE USER IF NOT EXISTS 'sqlcheck_app'@'127.0.0.1' IDENTIFIED BY 'SqlCheck_App_2026!';
GRANT ALL PRIVILEGES ON `tdsql_sqlcheck`.* TO 'sqlcheck_app'@'%';
GRANT ALL PRIVILEGES ON `tdsql_sqlcheck`.* TO 'sqlcheck_app'@'localhost';
GRANT ALL PRIVILEGES ON `tdsql_sqlcheck`.* TO 'sqlcheck_app'@'127.0.0.1';
FLUSH PRIVILEGES;

-- ----------------------------------------------------------------------------
-- 第二步：系统表与架构版本表
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_version (
    `key`     VARCHAR(128) PRIMARY KEY,
    value     TEXT NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ----------------------------------------------------------------------------
-- 第三步：业务元数据表结构（共 40+ 张表）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS slow_queries (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            fingerprint         TEXT NOT NULL,
            sql_text            TEXT NOT NULL,
            normalized_sql      TEXT,
            db_name             VARCHAR(128) DEFAULT '',
            set_id              VARCHAR(512) DEFAULT '',
            connection_id       VARCHAR(64) DEFAULT '',
            project_id          VARCHAR(64) DEFAULT '',
            client_user         TEXT,
            client_host         TEXT,
            exec_count          INT DEFAULT 0,
            total_time_ms       DOUBLE DEFAULT 0,
            avg_time_ms         DOUBLE DEFAULT 0,
            max_time_ms         DOUBLE DEFAULT 0,
            rows_examined       INT DEFAULT 0,
            rows_sent           INT DEFAULT 0,
            rows_affected       BIGINT DEFAULT 0,
            lock_time_ms        DOUBLE DEFAULT 0,
            first_seen          VARCHAR(32),
            last_seen           VARCHAR(32),
            problem_type        VARCHAR(256) DEFAULT '',
            severity            VARCHAR(32) DEFAULT 'INFO',
            root_cause          TEXT,
            suggestion          TEXT,
            optimized_sql       TEXT,
            distributed_analysis TEXT,
            index_suggestions   TEXT,
            rewrite_suggestions TEXT,
            explain_plan        TEXT,
            explain_issues      VARCHAR(1000) DEFAULT '',
            involved_tables     VARCHAR(512) DEFAULT '',
            table_stats         TEXT,
            table_schema_ddl    TEXT,
            index_details       TEXT,
            redundant_indexes   VARCHAR(1000) DEFAULT '',
            stats_update_info   TEXT,
            stats_expired       VARCHAR(1000) DEFAULT '',
            scan_efficiency     VARCHAR(64) DEFAULT '',
            status              VARCHAR(32) DEFAULT 'pending',
            assigned_to         VARCHAR(64) DEFAULT '',
            scan_task_id        INT DEFAULT NULL,
            analysis_json       TEXT,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_slow_fingerprint (fingerprint(255)),
            INDEX idx_slow_db (db_name),
            INDEX idx_slow_set_id (set_id),
            INDEX idx_slow_status (status),
            INDEX idx_slow_connection (connection_id),
            INDEX idx_slow_project (project_id),
            INDEX idx_slow_last_seen (last_seen),
            INDEX idx_slow_severity (severity)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS audit_history (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            audit_type          VARCHAR(64) NOT NULL,
            source              TEXT,
            project_id          VARCHAR(64) DEFAULT '',
            connection_id       VARCHAR(64) DEFAULT '',
            total_sql           INT DEFAULT 0,
            passed              INT DEFAULT 0,
            failed              INT DEFAULT 0,
            error_count         INT DEFAULT 0,
            warning_count       INT DEFAULT 0,
            pass_rate           DOUBLE DEFAULT 0,
            results_json        LONGTEXT,
            gate_passed         INT DEFAULT NULL,
            gate_detail         TEXT,
            top_violations      TEXT,
            results_summary     TEXT,
            created_by          VARCHAR(64) DEFAULT '',
            db_name             VARCHAR(128) DEFAULT '',
            rule_set_id         VARCHAR(64) DEFAULT NULL,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_audit_type (audit_type),
            INDEX idx_audit_project (project_id),
            INDEX idx_audit_created (created_at),
            INDEX idx_audit_gate (gate_passed),
            INDEX idx_audit_conn (connection_id, created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS audit_results (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            audit_history_id    INT NOT NULL,
            sql_text            TEXT NOT NULL,
            sql_type            VARCHAR(32) DEFAULT '',
            line_number         INT,
            file_path           TEXT,
            passed              INT DEFAULT 1,
            violations_json     TEXT,
            error_count         INT DEFAULT 0,
            warning_count       INT DEFAULT 0,
            triggered_rules     TEXT,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (audit_history_id) REFERENCES audit_history(id) ON DELETE CASCADE,
            INDEX idx_results_history (audit_history_id),
            INDEX idx_results_passed (passed)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS rule_configs (
            rule_id             VARCHAR(64) PRIMARY KEY,
            category            VARCHAR(64) NOT NULL,
            severity            VARCHAR(32) NOT NULL,
            description         TEXT NOT NULL,
            spec_source         TEXT,
            fix_suggestion      TEXT,
            enabled             INT DEFAULT 1,
            is_builtin          INT DEFAULT 1,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_rule_category (category),
            INDEX idx_rule_enabled (enabled)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS rule_whitelist (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            rule_id             VARCHAR(64) NOT NULL,
            table_pattern       TEXT,
            sql_pattern         TEXT,
            reason              TEXT,
            created_by          VARCHAR(64) DEFAULT '',
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_whitelist_rule (rule_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS gate_rules (
            project_id          VARCHAR(64) PRIMARY KEY,
            max_error_count     INT DEFAULT 0,
            max_warning_count   INT DEFAULT -1,
            required_rules      TEXT,
            blocked_rules       TEXT,
            description         TEXT,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS instance_gate_rules (
            connection_id       VARCHAR(64) PRIMARY KEY,
            max_error_count     INT NOT NULL DEFAULT 0,
            max_warning_count   INT NOT NULL DEFAULT -1,
            mode                VARCHAR(16) NOT NULL DEFAULT 'enforce',
            description         TEXT,
            updated_by          VARCHAR(64) NOT NULL DEFAULT '',
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            CONSTRAINT fk_igr_connection FOREIGN KEY (connection_id)
                REFERENCES tdsql_connections(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS gate_audit_logs (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            project_id          VARCHAR(64) NOT NULL,
            audit_history_id    INT,
            source              TEXT,
            passed              INT NOT NULL,
            error_count         INT DEFAULT 0,
            warning_count       INT DEFAULT 0,
            blocked_by          TEXT,
            detail              TEXT,
            connection_id       VARCHAR(64) DEFAULT NULL,
            rule_set_id         VARCHAR(64) DEFAULT NULL,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (audit_history_id) REFERENCES audit_history(id) ON DELETE SET NULL,
            INDEX idx_gate_project (project_id),
            INDEX idx_gate_passed (passed),
            INDEX idx_gate_created (created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS tdsql_connections (
            id                  VARCHAR(64) PRIMARY KEY,
            name                VARCHAR(255) NOT NULL,
            host                VARCHAR(256) NOT NULL,
            port                INT NOT NULL,
            username            VARCHAR(64) NOT NULL,
            password_encrypted  TEXT NOT NULL,
            `database`          VARCHAR(128) DEFAULT '',
            charset             VARCHAR(32) DEFAULT 'utf8mb4',
            is_default          INT DEFAULT 0,
            is_distributed      INT DEFAULT 1,
            description         TEXT,
            set_list            TEXT,
            monitor_host        VARCHAR(128) DEFAULT '',
            monitor_port        INT DEFAULT 15001,
            monitor_user        VARCHAR(128) DEFAULT '',
            monitor_password_encrypted TEXT,
            monitor_db          VARCHAR(128) DEFAULT 'tdsqlpcloud_monitor',
            zk_import_batch_id  VARCHAR(36) DEFAULT NULL,
            status              VARCHAR(32) DEFAULT 'disconnected',
            last_connected_at   VARCHAR(32),
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_conn_default (is_default)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS zk_discovery_import_batches (
            id                  VARCHAR(36) PRIMARY KEY,
            discovery_id        VARCHAR(64) NOT NULL,
            operator_username   VARCHAR(128) NOT NULL,
            selected_instance_count INT NOT NULL,
            candidate_count     INT NOT NULL,
            created_count       INT NOT NULL DEFAULT 0,
            skipped_count       INT NOT NULL DEFAULT 0,
            failed_count        INT NOT NULL DEFAULT 0,
            status              VARCHAR(32) NOT NULL,
            failure_summary     TEXT NULL,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            completed_at        DATETIME NULL,
            INDEX idx_zk_import_batch_created (created_at),
            INDEX idx_zk_import_batch_operator (operator_username)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS zk_discovery_sessions (
            discovery_id        VARCHAR(64) PRIMARY KEY,
            owner               VARCHAR(64) NOT NULL,
            is_mock             INT NOT NULL DEFAULT 0,
            expires_at          DOUBLE NOT NULL,
            items_json          LONGTEXT NOT NULL,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS zk_discovery_previews (
            preview_id          VARCHAR(64) PRIMARY KEY,
            discovery_id        VARCHAR(64) NOT NULL,
            owner               VARCHAR(64) NOT NULL,
            expires_at          DOUBLE NOT NULL,
            rows_json           LONGTEXT NOT NULL,
            business_enc        TEXT,
            monitor_enc         TEXT,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS zk_discovery_import_items (
            id                  BIGINT PRIMARY KEY AUTO_INCREMENT,
            batch_id            VARCHAR(36) NOT NULL,
            source_instance_id  VARCHAR(128) NOT NULL,
            instance_kind       VARCHAR(32) NOT NULL,
            instance_type       VARCHAR(32) NOT NULL,
            primary_proxy_host  VARCHAR(255) NOT NULL,
            primary_proxy_port  INT NOT NULL,
            set_list            TEXT NOT NULL,
            resolved_instance_name VARCHAR(255) NULL,
            database_name       VARCHAR(255) NULL,
            generated_connection_name VARCHAR(255) NULL,
            connection_id       VARCHAR(64) NULL,
            result_status       VARCHAR(32) NOT NULL,
            failure_code        VARCHAR(64) NULL,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_zk_import_item_batch (batch_id),
            INDEX idx_zk_import_item_instance (source_instance_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS bigtable_inventory (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            connection_id       VARCHAR(64) NOT NULL,
            schema_name         VARCHAR(128) NOT NULL,
            table_name          VARCHAR(256) NOT NULL,
            size_gb             DOUBLE DEFAULT 0,
            size_mb             DOUBLE DEFAULT 0,
            rows_count          BIGINT DEFAULT 0,
            index_size_mb       DOUBLE DEFAULT 0,
            daily_inc_mb        DOUBLE DEFAULT 0,
            level               VARCHAR(16) NOT NULL,
            is_partitioned      INT DEFAULT 0,
            partition_count     INT DEFAULT 0,
            has_global_index    INT DEFAULT 0,
            shard_key           VARCHAR(128) DEFAULT '',
            inspection_date     VARCHAR(32) NOT NULL,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uk_bt (connection_id, schema_name(128), table_name(128), inspection_date),
            INDEX idx_bt_level (level),
            INDEX idx_bt_connection (connection_id),
            INDEX idx_bt_date (inspection_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS bigtable_classification (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            connection_id       VARCHAR(64) NOT NULL,
            schema_name         VARCHAR(128) NOT NULL,
            table_name          VARCHAR(256) NOT NULL,
            table_type          VARCHAR(32) NOT NULL,
            table_type_label    VARCHAR(64) DEFAULT '',
            retention_days      INT DEFAULT 0,
            archive_target      VARCHAR(128) DEFAULT '',
            archive_period      VARCHAR(64) DEFAULT '',
            partition_key       VARCHAR(128) DEFAULT '',
            partition_granularity VARCHAR(32) DEFAULT '',
            classified_by       VARCHAR(64) DEFAULT '',
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_bc (connection_id, schema_name(128), table_name(128))
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS partition_watermarks (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            connection_id       VARCHAR(64) NOT NULL,
            schema_name         VARCHAR(128) NOT NULL,
            table_name          VARCHAR(256) NOT NULL,
            partition_count     INT NOT NULL,
            watermark_percent   DOUBLE DEFAULT 0,
            status              VARCHAR(32) NOT NULL,
            check_date          VARCHAR(32) NOT NULL,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uk_pw (connection_id, schema_name(128), table_name(128), check_date),
            INDEX idx_pw_status (status),
            INDEX idx_pw_date (check_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS change_controls (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            connection_id       VARCHAR(64) NOT NULL,
            schema_name         VARCHAR(128) NOT NULL,
            table_name          VARCHAR(256) NOT NULL,
            table_level         VARCHAR(16) NOT NULL,
            change_type         VARCHAR(32) NOT NULL,
            change_sql          TEXT NOT NULL,
            reason              TEXT,
            stage               VARCHAR(32) DEFAULT 'submitted',
            backup_completed    INT DEFAULT 0,
            ticket_approved     INT DEFAULT 0,
            window_applied      INT DEFAULT 0,
            executed_at         VARCHAR(32),
            executed_by         VARCHAR(64) DEFAULT '',
            result              TEXT,
            post_check_status   VARCHAR(32) DEFAULT '',
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_cc_stage (stage),
            INDEX idx_cc_level (table_level)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS inspection_tasks (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            connection_id       VARCHAR(64) NOT NULL,
            inspection_type     VARCHAR(32) NOT NULL,
            status              VARCHAR(32) DEFAULT 'pending',
            started_at          VARCHAR(32),
            completed_at        VARCHAR(32),
            error_message       TEXT,
            report_path         TEXT,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_it_status (status),
            INDEX idx_it_type (inspection_type),
            INDEX idx_it_date (created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS inspection_results (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            task_id             INT NOT NULL,
            category            VARCHAR(64) NOT NULL,
            severity            VARCHAR(32) NOT NULL,
            schema_name         VARCHAR(128) DEFAULT '',
            table_name          VARCHAR(256) DEFAULT '',
            metric_name         VARCHAR(128) DEFAULT '',
            metric_value        TEXT,
            threshold           TEXT,
            message             TEXT NOT NULL,
            suggestion          TEXT,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (task_id) REFERENCES inspection_tasks(id) ON DELETE CASCADE,
            INDEX idx_ir_task (task_id),
            INDEX idx_ir_severity (severity),
            INDEX idx_ir_category (category)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS alerts (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            connection_id       VARCHAR(64) NOT NULL,
            metric_name         VARCHAR(128) NOT NULL,
            metric_value        DOUBLE NOT NULL,
            level               VARCHAR(16) NOT NULL,
            threshold           DOUBLE NOT NULL,
            message             TEXT NOT NULL,
            status              VARCHAR(32) DEFAULT 'active',
            acknowledged_by     VARCHAR(64) DEFAULT '',
            acknowledged_at     VARCHAR(32),
            resolved_at         VARCHAR(32),
            notify_channels     TEXT,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_alert_status (status),
            INDEX idx_alert_level (level),
            INDEX idx_alert_connection (connection_id),
            INDEX idx_alert_created (created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS alert_rules (
            metric_name         VARCHAR(128) PRIMARY KEY,
            warning_threshold   DOUBLE NOT NULL,
            urgent_threshold    DOUBLE NOT NULL,
            check_interval_sec  INT DEFAULT 60,
            notify_webhook      TEXT,
            notify_email        TEXT,
            enabled             INT DEFAULT 1,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS projects (
            project_id          VARCHAR(64) PRIMARY KEY,
            project_name        VARCHAR(128) NOT NULL,
            tdsql_connection_id VARCHAR(64) DEFAULT '',
            rule_set_id         VARCHAR(64) DEFAULT 'default',
            gate_rule_id        VARCHAR(64) DEFAULT 'default',
            gitlab_project_id   INT,
            gitlab_url          TEXT,
            description         TEXT,
            status              VARCHAR(32) DEFAULT 'active',
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS operation_logs (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            operator            VARCHAR(64) DEFAULT '',
            operation_type      VARCHAR(64) NOT NULL,
            target_type         VARCHAR(64) DEFAULT '',
            target_id           VARCHAR(128) DEFAULT '',
            detail              TEXT,
            ip_address          VARCHAR(64) DEFAULT '',
            user_agent          TEXT,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_log_operator (operator),
            INDEX idx_log_type (operation_type),
            INDEX idx_log_created (created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS fingerprint_stats (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            connection_id       VARCHAR(64) NOT NULL,
            fingerprint         TEXT NOT NULL,
            sample_sql          TEXT NOT NULL,
            stat_date           VARCHAR(32) NOT NULL,
            exec_count          INT DEFAULT 0,
            total_time_ms       DOUBLE DEFAULT 0,
            avg_time_ms         DOUBLE DEFAULT 0,
            max_time_ms         DOUBLE DEFAULT 0,
            rows_examined       INT DEFAULT 0,
            rows_sent           INT DEFAULT 0,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uk_fp (connection_id, fingerprint(255), stat_date),
            INDEX idx_fp_connection (connection_id),
            INDEX idx_fp_date (stat_date),
            INDEX idx_fp_total_time (total_time_ms),
            INDEX idx_fp_exec_count (exec_count)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS optimization_records (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            slow_query_id       INT,
            connection_id       VARCHAR(64) NOT NULL,
            original_sql        TEXT NOT NULL,
            optimized_sql       TEXT NOT NULL,
            before_type         VARCHAR(64) DEFAULT '',
            before_key          TEXT,
            before_rows         INT DEFAULT 0,
            before_extra        TEXT,
            before_time_ms      DOUBLE DEFAULT 0,
            after_type          VARCHAR(64) DEFAULT '',
            after_key           TEXT,
            after_rows          INT DEFAULT 0,
            after_extra         TEXT,
            after_time_ms       DOUBLE DEFAULT 0,
            improvement         VARCHAR(128) DEFAULT '',
            improvement_detail  TEXT,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (slow_query_id) REFERENCES slow_queries(id) ON DELETE SET NULL,
            INDEX idx_opt_slow_query (slow_query_id),
            INDEX idx_opt_improvement (improvement)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS scan_tasks (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            task_name           VARCHAR(256) NOT NULL,
            source              VARCHAR(32) DEFAULT 'manual',
            db_name             VARCHAR(128) DEFAULT '',
            connection_id       VARCHAR(64) DEFAULT '',
            connection_name     VARCHAR(256) DEFAULT '',
            time_window_start   VARCHAR(32) DEFAULT '',
            time_window_end     VARCHAR(32) DEFAULT '',
            created_by          VARCHAR(64) DEFAULT '',
            total_fetched       INT DEFAULT 0,
            total_analyzed      INT DEFAULT 0,
            status              VARCHAR(32) DEFAULT 'completed',
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_scan_task_db (db_name),
            INDEX idx_scan_task_source (source)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS users (
            id                    INT PRIMARY KEY AUTO_INCREMENT,
            username              VARCHAR(64) NOT NULL UNIQUE,
            display_name          VARCHAR(128) DEFAULT '',
            role                  VARCHAR(32) NOT NULL DEFAULT 'developer',
            password_hash         TEXT NOT NULL,
            salt                  TEXT NOT NULL,
            status                VARCHAR(16) DEFAULT 'active',
            must_change_password  INT DEFAULT 0,
            token_version         INT NOT NULL DEFAULT 0,
            failed_attempts       INT DEFAULT 0,
            locked_until          VARCHAR(32) DEFAULT NULL,
            last_login_at         VARCHAR(32) DEFAULT NULL,
            created_by            VARCHAR(64) DEFAULT '',
            created_at            DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at            DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_users_role (role),
            INDEX idx_users_status (status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS rule_sets (
            id                  VARCHAR(64) PRIMARY KEY,
            name                VARCHAR(128) NOT NULL,
            description         TEXT,
            is_builtin          INT DEFAULT 0,
            created_by          VARCHAR(64) DEFAULT '',
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS rule_set_items (
            rule_set_id         VARCHAR(64) NOT NULL,
            rule_id             VARCHAR(64) NOT NULL,
            enabled             INT DEFAULT 1,
            severity_override   VARCHAR(32) DEFAULT NULL,
            PRIMARY KEY (rule_set_id, rule_id),
            INDEX idx_rsi_set (rule_set_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS scan_schedules (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            connection_id       VARCHAR(64) NOT NULL,
            source              VARCHAR(32) DEFAULT 'digest',
            cron_hour           INT DEFAULT 2,
            cron_minute         INT DEFAULT 0,
            limit_rows          INT DEFAULT 100,
            min_time            DOUBLE DEFAULT 1.0,
            enabled             INT DEFAULT 1,
            last_run_at         VARCHAR(32) DEFAULT NULL,
            last_run_status     VARCHAR(32) DEFAULT '',
            created_by          VARCHAR(64) DEFAULT '',
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_sched_conn (connection_id),
            INDEX idx_sched_enabled (enabled)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS retention_policies (
            table_name          VARCHAR(64) PRIMARY KEY,
            retention_days      INT NOT NULL,
            enabled             INT DEFAULT 1,
            updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS scheduler_lease (
            id                  INT PRIMARY KEY,
            holder              VARCHAR(128) NOT NULL,
            expires_at          VARCHAR(32) NOT NULL,
            CONSTRAINT chk_lease_id CHECK (id = 1)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS system_config (
            config_key          VARCHAR(64) PRIMARY KEY,
            config_value        VARCHAR(256) DEFAULT '',
            updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS roles (
            role_id             VARCHAR(32) PRIMARY KEY,
            role_name           VARCHAR(64) NOT NULL,
            is_builtin          INT DEFAULT 0,
            description         TEXT,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS role_permissions (
            role_id             VARCHAR(32) NOT NULL,
            menu_key            VARCHAR(64) NOT NULL,
            visible             INT DEFAULT 1,
            PRIMARY KEY (role_id, menu_key),
            INDEX idx_rp_role (role_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS cluster_inspection (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            connection_id       VARCHAR(64) DEFAULT '',
            cluster_name        VARCHAR(128) DEFAULT '',
            inspect_date        VARCHAR(32) DEFAULT '',
            total_issues        INT DEFAULT 0,
            error_count         INT DEFAULT 0,
            warning_count       INT DEFAULT 0,
            info_count          INT DEFAULT 0,
            node_count          INT DEFAULT 0,
            summary_json        MEDIUMTEXT,
            created_by          VARCHAR(64) DEFAULT '',
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_ci_conn (connection_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS cluster_inspection_issue (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            inspection_id       INT NOT NULL,
            category            VARCHAR(32) DEFAULT '',
            severity            VARCHAR(32) DEFAULT 'INFO',
            node                VARCHAR(128) DEFAULT '',
            title               VARCHAR(256) DEFAULT '',
            detail              TEXT,
            metric_value        VARCHAR(64) DEFAULT '',
            threshold           VARCHAR(64) DEFAULT '',
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_cii (inspection_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS index_audit (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            connection_id       VARCHAR(64) DEFAULT '',
            database_filter     VARCHAR(128) DEFAULT '',
            total_tables        INT DEFAULT 0,
            total_indexes       INT DEFAULT 0,
            total_findings      INT DEFAULT 0,
            error_count         INT DEFAULT 0,
            warning_count       INT DEFAULT 0,
            info_count          INT DEFAULT 0,
            created_by          VARCHAR(64) DEFAULT '',
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_ia_conn (connection_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS index_audit_finding (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            audit_id            INT NOT NULL,
            db_name             VARCHAR(128) DEFAULT '',
            table_name          VARCHAR(128) DEFAULT '',
            index_name          VARCHAR(128) DEFAULT '',
            finding_type        VARCHAR(64) DEFAULT '',
            severity            VARCHAR(32) DEFAULT 'INFO',
            detail              TEXT,
            suggestion          TEXT,
            metric              VARCHAR(64) DEFAULT '',
            related_index_name  VARCHAR(128) DEFAULT '',
            index_columns       VARCHAR(512) DEFAULT '',
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_iaf (audit_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS schema_diff (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            left_conn           VARCHAR(64) DEFAULT '',
            right_conn          VARCHAR(64) DEFAULT '',
            databases_filter    VARCHAR(256) DEFAULT '',
            total_items         INT DEFAULT 0,
            error_count         INT DEFAULT 0,
            warning_count       INT DEFAULT 0,
            info_count          INT DEFAULT 0,
            created_by          VARCHAR(64) DEFAULT '',
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS schema_diff_item (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            diff_id             INT NOT NULL,
            db_name             VARCHAR(128) DEFAULT '',
            table_name          VARCHAR(128) DEFAULT '',
            object_name         VARCHAR(128) DEFAULT '',
            diff_type           VARCHAR(64) DEFAULT '',
            severity            VARCHAR(32) DEFAULT 'INFO',
            left_value          TEXT,
            right_value         TEXT,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_sdi (diff_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS emergency_report (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            connection_id       VARCHAR(64) DEFAULT '',
            actions             VARCHAR(256) DEFAULT '',
            report_json         MEDIUMTEXT,
            created_by          VARCHAR(64) DEFAULT '',
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_er_conn (connection_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS daily_inspection (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            inspect_date        VARCHAR(16) NOT NULL,
            connection_id       VARCHAR(64) DEFAULT '',
            node                VARCHAR(128) DEFAULT '',
            cpu_peak            DOUBLE DEFAULT 0,
            cpu_avg             DOUBLE DEFAULT 0,
            mem_peak            DOUBLE DEFAULT 0,
            conn_peak           DOUBLE DEFAULT 0,
            slow_query          DOUBLE DEFAULT 0,
            delay_peak          DOUBLE DEFAULT 0,
            disk_peak           DOUBLE DEFAULT 0,
            cpu_cores           INT DEFAULT 0,
            mem_gb              DOUBLE DEFAULT 0,
            data_disk_gb        DOUBLE DEFAULT 0,
            log_disk_gb         DOUBLE DEFAULT 0,
            cpu_avg_daily       DOUBLE DEFAULT 0,
            mem_avg_daily       DOUBLE DEFAULT 0,
            proxy_req_total     BIGINT DEFAULT 0,
            proxy_t_l           DOUBLE DEFAULT 0,
            proxy_t_m           DOUBLE DEFAULT 0,
            proxy_t_p           DOUBLE DEFAULT 0,
            proxy_t_n           DOUBLE DEFAULT 0,
            proxy_req_l         BIGINT DEFAULT 0,
            proxy_req_m         BIGINT DEFAULT 0,
            proxy_req_p         BIGINT DEFAULT 0,
            proxy_req_n         BIGINT DEFAULT 0,
            proxy_active_conn_peak INT DEFAULT 0,
            proxy_conn_peak     INT DEFAULT 0,
            proxy_err_sql_sum   BIGINT DEFAULT 0,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uq_daily (inspect_date, connection_id, node),
            INDEX idx_daily_conn (connection_id, inspect_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS server_daily_inspection (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            inspect_date        VARCHAR(16) NOT NULL,
            connection_id       VARCHAR(64) DEFAULT '',
            ip                  VARCHAR(128) DEFAULT '',
            hostname            VARCHAR(128) DEFAULT '',
            cpu_peak            DOUBLE DEFAULT 0,
            cpu_avg             DOUBLE DEFAULT 0,
            mem_used_str        VARCHAR(128) DEFAULT '',
            mem_pct             DOUBLE DEFAULT 0,
            disk_root_pct       DOUBLE DEFAULT 0,
            disk_data_str       VARCHAR(512) DEFAULT '',
            disk_backup_pct     VARCHAR(32) DEFAULT '',
            read_await_max      DOUBLE DEFAULT 0,
            read_await_dev      VARCHAR(128) DEFAULT '',
            write_await_max     DOUBLE DEFAULT 0,
            write_await_dev     VARCHAR(128) DEFAULT '',
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uq_srv_daily (inspect_date, connection_id, ip),
            INDEX idx_srv_daily_conn (connection_id, inspect_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS bigtable_history (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            snap_date           VARCHAR(16) NOT NULL,
            connection_id       VARCHAR(64) DEFAULT '',
            db_name             VARCHAR(128) DEFAULT '',
            table_name          VARCHAR(128) DEFAULT '',
            table_rows          BIGINT DEFAULT 0,
            size_gb             DOUBLE DEFAULT 0,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uq_bth (snap_date, connection_id, db_name, table_name),
            INDEX idx_bth (connection_id, db_name, table_name, snap_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS gateway_log_reports (
            id                  INT PRIMARY KEY AUTO_INCREMENT,
            connection_id       VARCHAR(64) DEFAULT '',
            log_file_name       VARCHAR(256),
            log_type            VARCHAR(64) DEFAULT 'interf',
            total_queries       INT DEFAULT 0,
            slow_queries        INT DEFAULT 0,
            max_time_ms         DOUBLE DEFAULT 0,
            avg_time_ms         DOUBLE DEFAULT 0,
            report_html         LONGTEXT,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_glr_conn (connection_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

SET FOREIGN_KEY_CHECKS = 1;

-- 初始化脚本完成。应用首次启动时亦会自动进行幂等补齐与规则元数据同步。