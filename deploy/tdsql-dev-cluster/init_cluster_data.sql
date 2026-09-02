-- ==============================================================================
-- TDSQL 轻量 Docker 靶场初始化数据脚本
-- 覆盖分布式分片表、广播表、单表、系统 xa 库以及慢 SQL 监控库
-- ==============================================================================

-- 1. 创建核心业务库与辅助系统库
CREATE DATABASE IF NOT EXISTS `tdsql_demo_distributed` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
CREATE DATABASE IF NOT EXISTS `tdsql_demo_centralized` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
CREATE DATABASE IF NOT EXISTS `xa` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
CREATE DATABASE IF NOT EXISTS `tdsqlpcloud_monitor` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;

USE `tdsql_demo_distributed`;

-- 2. 分片表 1: big_audit_trail (经典分片表, shardkey=user_id)
DROP TABLE IF EXISTS `big_audit_trail`;
CREATE TABLE `big_audit_trail` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `user_id` bigint(20) NOT NULL COMMENT '用户ID(分片键)',
  `action` varchar(64) NOT NULL COMMENT '操作行为',
  `event_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '事件时间',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `is_deleted` tinyint(1) NOT NULL DEFAULT 0 COMMENT '逻辑删除标识',
  PRIMARY KEY (`id`, `user_id`),
  KEY `idx_trace` (`user_id`),
  KEY `idx_event` (`event_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='shardkey=user_id';

-- 3. 分片表 2: cus_bas_corp_contact (分片表, shardkey=cust_no)
DROP TABLE IF EXISTS `cus_bas_corp_contact`;
CREATE TABLE `cus_bas_corp_contact` (
  `id` varchar(64) NOT NULL COMMENT '主键标识',
  `cust_no` varchar(20) NOT NULL COMMENT '客户号(分片键)',
  `data_valid_tm` datetime DEFAULT NULL COMMENT '有效时间',
  `contact_no` varchar(20) DEFAULT NULL COMMENT '联系方式',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`, `cust_no`),
  KEY `idx_contact` (`contact_no`, `data_valid_tm`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='shardkey=cust_no';

-- 4. 广播表 1: cus_name_list_type (shardkey=noshardkey_allset 全局广播表)
DROP TABLE IF EXISTS `cus_name_list_type`;
CREATE TABLE `cus_name_list_type` (
  `type_code` varchar(10) NOT NULL COMMENT '名单类型编码',
  `list_number` varchar(32) NOT NULL COMMENT '名单编号',
  `type_name` varchar(64) NOT NULL COMMENT '名单名称',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`type_code`),
  KEY `idx_list` (`list_number`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='shardkey=noshardkey_allset BROADCAST';

-- 5. 广播表 2: t_dict (字典全局表)
DROP TABLE IF EXISTS `t_dict`;
CREATE TABLE `t_dict` (
  `dict_code` varchar(32) NOT NULL COMMENT '字典编码',
  `dict_type` varchar(32) NOT NULL COMMENT '字典类型',
  `dict_value` varchar(128) NOT NULL COMMENT '字典值',
  PRIMARY KEY (`dict_code`),
  KEY `idx_type` (`dict_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='shardkey=noshardkey_allset BROADCAST';

-- 6. 普通单表: t_single_sys_config (无任何分片键，用于反向对照单表审核)
DROP TABLE IF EXISTS `t_single_sys_config`;
CREATE TABLE `t_single_sys_config` (
  `config_key` varchar(64) NOT NULL COMMENT '配置键名',
  `config_val` varchar(255) DEFAULT NULL COMMENT '配置内容',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`config_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='系统全局参数配置表';

-- 7. 集中式业务库表
USE `tdsql_demo_centralized`;
DROP TABLE IF EXISTS `t_account_central`;
CREATE TABLE `t_account_central` (
  `account_no` varchar(32) NOT NULL COMMENT '账号',
  `cust_id` bigint(20) NOT NULL COMMENT '客户ID',
  `balance` decimal(18, 2) NOT NULL DEFAULT 0.00 COMMENT '账户余额',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '开户时间',
  PRIMARY KEY (`account_no`),
  KEY `idx_cust` (`cust_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='集中式账户表';

-- 8. 监控库 tdsqlpcloud_monitor 初始化
USE `tdsqlpcloud_monitor`;

DROP TABLE IF EXISTS `slow_query_log`;
CREATE TABLE `slow_query_log` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `start_time` datetime NOT NULL,
  `user_host` varchar(128) DEFAULT NULL,
  `query_time` double NOT NULL,
  `lock_time` double NOT NULL,
  `rows_sent` bigint(20) NOT NULL,
  `rows_examined` bigint(20) NOT NULL,
  `db` varchar(64) DEFAULT NULL,
  `sql_text` longtext NOT NULL,
  `thread_id` bigint(20) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_time` (`start_time`),
  KEY `idx_db` (`db`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

DROP TABLE IF EXISTS `sql_digest_stat`;
CREATE TABLE `sql_digest_stat` (
  `digest` varchar(64) NOT NULL,
  `digest_text` text,
  `schema_name` varchar(64) DEFAULT NULL,
  `count_star` bigint(20) DEFAULT '0',
  `sum_timer_wait` bigint(20) DEFAULT '0',
  `min_timer_wait` bigint(20) DEFAULT '0',
  `avg_timer_wait` bigint(20) DEFAULT '0',
  `max_timer_wait` bigint(20) DEFAULT '0',
  `sum_rows_examined` bigint(20) DEFAULT '0',
  `sum_rows_sent` bigint(20) DEFAULT '0',
  `first_seen` timestamp NULL DEFAULT NULL,
  `last_seen` timestamp NULL DEFAULT NULL,
  PRIMARY KEY (`digest`),
  KEY `idx_schema` (`schema_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 插入典型慢SQL样例数据
INSERT INTO `slow_query_log` (`start_time`, `user_host`, `query_time`, `lock_time`, `rows_sent`, `rows_examined`, `db`, `sql_text`) VALUES
(NOW(), 'app_user[app] @ [10.0.0.1]', 2.45, 0.01, 100, 500000, 'tdsql_demo_distributed', 'SELECT * FROM big_audit_trail WHERE action = \'LOGIN\' ORDER BY event_time DESC LIMIT 100'),
(NOW(), 'app_user[app] @ [10.0.0.2]', 1.82, 0.02, 1, 200000, 'tdsql_demo_distributed', 'SELECT * FROM cus_bas_corp_contact WHERE contact_no = \'13800000000\''),
(NOW(), 'batch_job[batch] @ [10.0.0.5]', 5.12, 0.15, 0, 1000000, 'tdsql_demo_distributed', 'UPDATE big_audit_trail SET is_deleted = 1 WHERE event_time < DATE_SUB(NOW(), INTERVAL 180 DAY)');

INSERT INTO `sql_digest_stat` (`digest`, `digest_text`, `schema_name`, `count_star`, `sum_timer_wait`, `avg_timer_wait`, `max_timer_wait`, `sum_rows_examined`, `sum_rows_sent`, `first_seen`, `last_seen`) VALUES
('d1a2b3c4d5e6f7', 'SELECT * FROM `big_audit_trail` WHERE `action` = ? ORDER BY `event_time` DESC LIMIT ?', 'tdsql_demo_distributed', 125, 306250000000, 2450000000, 4200000000, 62500000, 12500, DATE_SUB(NOW(), INTERVAL 1 DAY), NOW()),
('a9b8c7d6e5f4e3', 'SELECT * FROM `cus_bas_corp_contact` WHERE `contact_no` = ?', 'tdsql_demo_distributed', 450, 819000000000, 1820000000, 3100000000, 90000000, 450, DATE_SUB(NOW(), INTERVAL 1 DAY), NOW());
