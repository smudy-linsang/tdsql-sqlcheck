-- 生产报告 #3: cus_bas_corp_contact（HASH 分片表，cust_no 在主键中）
CREATE TABLE `cus_bas_corp_contact` (
  `ID` varchar(64) NOT NULL,
  `CUST_NO` varchar(20) NOT NULL COMMENT '客户编号',
  `DATA_VALID_TM` datetime DEFAULT NULL,
  `CONTACT_NO` varchar(20) DEFAULT NULL,
  PRIMARY KEY (`ID`,`CUST_NO`),
  KEY `cus_bas_corp_contact_IDX1` (`CUST_NO`,`DATA_VALID_TM`),
  KEY `cus_bas_corp_contact_IDX2` (`CONTACT_NO`,`DATA_VALID_TM`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 TDSQL_DISTRIBUTED BY HASH(`cust_no`)
