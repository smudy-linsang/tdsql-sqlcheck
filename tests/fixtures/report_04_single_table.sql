-- 生产报告 #4: cus_bas_corp_contact_addr_20260511（无分片声明的单表，反向鉴别锚点）
CREATE TABLE `cus_bas_corp_contact_addr_20260511` (
  `ID` varchar(64) NOT NULL,
  `CUST_NO` varchar(20) NOT NULL COMMENT '客户编号',
  `ADDR_DETAIL` varchar(256) DEFAULT NULL,
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `is_deleted` tinyint NOT NULL DEFAULT 0,
  PRIMARY KEY (`ID`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='客户联系地址表'
