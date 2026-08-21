-- 生产报告 #8: t_branch（广播表，含 UNIQUE KEY）
CREATE TABLE `t_branch` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键',
  `branch_code` varchar(32) NOT NULL COMMENT '网点编号',
  `branch_name` varchar(128) NOT NULL COMMENT '网点名称',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `is_deleted` tinyint NOT NULL DEFAULT 0,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_branch_code` (`branch_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='网点表' shardkey=noshardkey_allset
