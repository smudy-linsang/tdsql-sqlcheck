-- 生产报告 #5: cus_name_list_type（广播表 noshardkey_allset）
CREATE TABLE `cus_name_list_type` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键',
  `type_name` varchar(128) NOT NULL COMMENT '名单类型名称',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `is_deleted` tinyint NOT NULL DEFAULT 0,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='名单类型表' shardkey=noshardkey_allset
