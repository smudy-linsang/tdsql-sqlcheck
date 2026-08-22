-- 生产报告 6297 #1: big_audit_trail（原样 DDL，反引号 idx_ 前缀索引——R061 误报修复的目标场景）
CREATE TABLE `big_audit_trail` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `trace_id` varchar(64) NOT NULL,
  `operator` varchar(64) NOT NULL,
  `event` varchar(32) NOT NULL,
  `detail` text,
  PRIMARY KEY (`id`),
  KEY `idx_trace` (`trace_id`),
  KEY `idx_operator` (`operator`),
  KEY `idx_event` (`event`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 shardkey=id
