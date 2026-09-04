CREATE TABLE uat_customer (
  relation_id VARCHAR(32) COMMENT '关联编号'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='UAT customer';

CREATE TABLE uat_order (
  relation_id VARCHAR(128) COMMENT '关联编号'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='UAT order';
