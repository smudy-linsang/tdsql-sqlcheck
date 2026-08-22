-- 生产报告 6297 #5: cus_name_list_type（原样形态，非 idx_ 前缀索引——R061 真实违规）
-- 注意：本 fixture 与 report_05_cus_name_list_type.sql 不同——后者是 v1.6.1.9 为
-- R077/R054 广播表测试造的简化版（无任何索引），本文件保留了真实的非 idx_ 前缀索引。
CREATE TABLE `cus_name_list_type` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `type_name` varchar(128) NOT NULL,
  `type_code` varchar(64) NOT NULL,
  `remark` varchar(256) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `CUS_NAME_LIST_TYPE_IDX1` (`type_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='名单类型表' shardkey=noshardkey_allset
