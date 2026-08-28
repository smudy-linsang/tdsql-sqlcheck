CREATE TABLE User_Profile (
    id BIGINT NOT NULL COMMENT '主键',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户档案';

CREATE TABLE `order` (
    order_id BIGINT NOT NULL COMMENT '订单号',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (order_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='订单表';

CREATE TABLE t_bare (
    col1 INT
);

CREATE TABLE t_engine_bad (
    id BIGINT NOT NULL COMMENT '主键',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COMMENT='引擎不合规';

CREATE TABLE t_charset_bad (
    id BIGINT NOT NULL COMMENT '主键',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=latin1 COMMENT='字符集不合规';

CREATE TABLE t_enum (
    id BIGINT NOT NULL COMMENT '主键',
    status ENUM('A','B','C') NOT NULL COMMENT '状态枚举',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='枚举类型表';

CREATE TABLE t_ts (
    id BIGINT NOT NULL COMMENT '主键',
    log_time TIMESTAMP NOT NULL COMMENT '日志时间',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='时间戳类型表';

CREATE TABLE t_order_rel (
    id BIGINT NOT NULL PRIMARY KEY COMMENT '主键',
    cust_id BIGINT NOT NULL COMMENT '客户ID',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    FOREIGN KEY (cust_id) REFERENCES t_customer (cust_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='外键表';

CREATE TABLE t_finance (
    id BIGINT NOT NULL COMMENT '主键',
    amount DOUBLE NOT NULL COMMENT '金额',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='财务表';

CREATE TABLE t_longvarchar (
    id BIGINT NOT NULL COMMENT '主键',
    remark VARCHAR(3000) NOT NULL COMMENT '备注',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='超长字段表';

CREATE TABLE t_text (
    id BIGINT NOT NULL COMMENT '主键',
    content TEXT NOT NULL COMMENT '正文',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='大字段表';

CREATE TABLE t_copy AS SELECT cust_id, cust_name FROM t_customer;

CREATE TEMPORARY TABLE tmp_calc (
    id BIGINT NOT NULL COMMENT '主键'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='临时表';

ALTER TABLE t_customer MODIFY COLUMN phone VARCHAR(10) NOT NULL COMMENT '手机号';

DROP DATABASE legacy_db;

CREATE TABLE users (
    id BIGINT NOT NULL COMMENT '主键',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户表复数';

CREATE TABLE t_order_bak (
    id BIGINT NOT NULL COMMENT '主键',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='备份表';

CREATE FUNCTION fn_calc(a INT, b INT) RETURNS INT BEGIN RETURN a + b; END;

CREATE TRIGGER trg_audit BEFORE INSERT ON t_customer FOR EACH ROW SET NEW.create_time = NOW();

CREATE TABLE t_oratype (
    id BIGINT NOT NULL COMMENT '主键',
    amt NUMBER(18,2) NOT NULL COMMENT '金额',
    code VARCHAR2(32) NOT NULL COMMENT '编码',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Oracle类型表';

CREATE TABLE t_deffunc (
    id BIGINT NOT NULL COMMENT '主键',
    data_dt CHAR(8) DEFAULT (DATE_FORMAT(NOW(), '%Y%m%d')) COMMENT '数据日期',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='默认值函数表';

CREATE TABLE t_hashpart (
    id BIGINT NOT NULL COMMENT '主键',
    region_code VARCHAR(16) NOT NULL COMMENT '地区码',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='哈希分区表'
PARTITION BY HASH(region_code) PARTITIONS 4;

CREATE TABLE t_longpk (
    biz_key VARCHAR(300) NOT NULL PRIMARY KEY COMMENT '业务主键'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='长主键表';

CREATE TABLE t_multishard (
    id BIGINT NOT NULL COMMENT '主键',
    cust_id BIGINT NOT NULL COMMENT '客户ID',
    region_id INT NOT NULL COMMENT '地区ID',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id, cust_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='多字段分片键表' SHARDKEY=cust_id,region_id;

CREATE TABLE t_shardtype (
    id BIGINT NOT NULL COMMENT '主键',
    amt_key DECIMAL(18,2) NOT NULL COMMENT '金额分片键',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id, amt_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='分片键类型表' SHARDKEY=amt_key;

CREATE TABLE t_shardnull (
    id BIGINT NOT NULL COMMENT '主键',
    cust_id BIGINT COMMENT '客户ID分片键',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='分片键可空表' SHARDKEY=cust_id;

SELECT * FROM t_customer WHERE cust_id = 1001;

UPDATE t_customer SET cust_level = 'gold';

SELECT cust_id FROM t_customer WHERE cust_id IN (
    SELECT cust_id FROM t_account WHERE account_no IN (
        SELECT account_no FROM t_transaction WHERE txn_id IN (
            SELECT txn_id FROM t_audit_log WHERE log_id IN (
                SELECT log_id FROM t_deposit WHERE deposit_no = 'D1'
            )
        )
    )
);

SELECT cust_id, cust_name FROM t_customer WHERE DATE_FORMAT(create_time, '%Y%m%d') = '20240101';

SELECT cust_id, cust_name FROM t_customer WHERE cust_level = 'gold' ORDER BY RAND() LIMIT 10;

SELECT a.account_no, c.cust_name
FROM t_account a
JOIN t_customer c ON a.cust_id = c.cust_id
WHERE a.balance > 1000;

UPDATE t_shard_demo SET shard_key = 999 WHERE id = 1;

DELETE FROM t_transaction WHERE txn_time > '2020-01-01';

SELECT cust_id, cust_name FROM t_customer WHERE cust_id = 1 INTO OUTFILE '/tmp/cust.csv';

INSERT DELAYED INTO t_audit_log (log_id, cust_id) VALUES (1, 1001);

INSERT INTO t_dict VALUES (1, 'k', 'v');

LOAD DATA INFILE '/tmp/data.csv' INTO TABLE t_dict;

UPDATE t_account a JOIN t_customer c ON a.cust_id = c.cust_id SET a.account_type = 'vip';

SELECT cust_id, cust_name FROM t_customer FORCE INDEX (idx_cust_level) WHERE cust_level = 'gold';

HANDLER t_customer OPEN;

LOCK TABLES t_customer WRITE;

DELETE FROM t_audit_log;

SELECT cust_id, cust_name FROM t_customer WHERE cust_id IN (1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,50,51,52,53,54,55,56,57,58,59,60,61,62,63,64,65,66,67,68,69,70,71,72,73,74,75,76,77,78,79,80,81,82,83,84,85,86,87,88,89,90,91,92,93,94,95,96,97,98,99,100,101,102,103,104,105,106,107,108,109,110,111,112,113,114,115,116,117,118,119,120,121,122,123,124,125,126,127,128,129,130,131,132,133,134,135,136,137,138,139,140,141,142,143,144,145,146,147,148,149,150,151,152,153,154,155,156,157,158,159,160,161,162,163,164,165,166,167,168,169,170,171,172,173,174,175,176,177,178,179,180,181,182,183,184,185,186,187,188,189,190,191,192,193,194,195,196,197,198,199,200,201,202,203,204,205);

SELECT cust_id, cust_name FROM t_customer;

SELECT cust_id, cust_name FROM t_customer WHERE cust_id = '1001';

SELECT a.account_no, c.cust_name
FROM t_account a
INNER JOIN t_customer c ON a.cust_id = c.cust_id
WHERE a.balance > 0;

UPDATE t_customer SET cust_level = 'silver' WHERE cust_level = 'normal';

BEGIN;

START TRANSACTION;

SELECT balance FROM t_account WHERE account_no = 'A001' FOR UPDATE;

GRANT SELECT ON tdsql_check.t_customer TO 'appuser'@'%';

TRUNCATE TABLE t_audit_log;

SELECT cust_id, cust_name FROM t_customer WHERE cust_name = '${custName}';

SELECT cust_name || phone AS contact FROM t_customer WHERE cust_id = 1001;

WITH recent_cust AS (SELECT cust_id, cust_name FROM t_customer WHERE cust_level = 'gold')
SELECT cust_id FROM recent_cust;

SELECT cust_id FROM t_customer WHERE cust_level = 'gold'
MINUS
SELECT cust_id FROM t_account WHERE balance > 0;

SELECT a.account_no, c.cust_name
FROM t_account a FULL JOIN t_customer c ON a.cust_id = c.cust_id;

DELETE FROM t_audit_log a WHERE a.log_id = 1;

INSERT INTO t_audit_record (log_id, cust_id) SELECT log_id, cust_id FROM t_deposit_src;

UPDATE t_customer SET cust_level = 'gold', cust_level = CASE WHEN cust_level = 'gold' THEN 'vip' ELSE cust_level END WHERE cust_id = 1001;

SELECT cust_id, cust_name FROM t_customer WHERE cust_level = 'gold' ORDER BY cust_id LIMIT 20000, 20;

CREATE TABLE t_manyidx (
    id BIGINT NOT NULL COMMENT '主键',
    c1 INT NOT NULL COMMENT '列1',
    c2 INT NOT NULL COMMENT '列2',
    c3 INT NOT NULL COMMENT '列3',
    c4 INT NOT NULL COMMENT '列4',
    c5 INT NOT NULL COMMENT '列5',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id),
    INDEX idx_c1 (c1),
    INDEX idx_c2 (c2),
    INDEX idx_c3 (c3),
    INDEX idx_c4 (c4),
    INDEX idx_c5 (c5)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='多索引表';

CREATE TABLE t_redundant (
    id BIGINT NOT NULL COMMENT '主键',
    c1 INT NOT NULL COMMENT '列1',
    c2 INT NOT NULL COMMENT '列2',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id),
    INDEX idx_a (c1),
    INDEX idx_ab (c1, c2)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='冗余索引表';

CREATE TABLE t_idxname (
    id BIGINT NOT NULL COMMENT '主键',
    c1 INT NOT NULL COMMENT '列1',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id),
    INDEX my_index (c1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='索引命名表';

CREATE TABLE t_idxorder (
    id BIGINT NOT NULL COMMENT '主键',
    status INT NOT NULL COMMENT '状态',
    cust_id BIGINT NOT NULL COMMENT '客户ID',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id),
    INDEX idx_status_cust (status, cust_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='索引顺序表';

CREATE TABLE t_lowcard (
    id BIGINT NOT NULL COMMENT '主键',
    status INT NOT NULL COMMENT '状态',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='低区分度索引表';

CREATE TABLE t_idxcols (
    id BIGINT NOT NULL COMMENT '主键',
    c1 INT NOT NULL COMMENT '列1',
    c2 INT NOT NULL COMMENT '列2',
    c3 INT NOT NULL COMMENT '列3',
    c4 INT NOT NULL COMMENT '列4',
    c5 INT NOT NULL COMMENT '列5',
    c6 INT NOT NULL COMMENT '列6',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id),
    INDEX idx_many (c1, c2, c3, c4, c5, c6)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='多字段索引表';

CREATE TABLE t_blobidx (
    id BIGINT NOT NULL COMMENT '主键',
    content TEXT NOT NULL COMMENT '正文',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id),
    INDEX idx_content (content)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='大字段索引表';

CREATE TABLE t_prefixidx (
    id BIGINT NOT NULL COMMENT '主键',
    cust_name VARCHAR(200) NOT NULL COMMENT '客户名',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id),
    INDEX idx_name (cust_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='前缀索引表';

CREATE TABLE t_noshard (
    id BIGINT NOT NULL COMMENT '主键',
    cust_id BIGINT NOT NULL COMMENT '客户ID',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='缺分片键表';

CREATE TABLE t_shard_notpk (
    id BIGINT NOT NULL COMMENT '主键',
    cust_id BIGINT NOT NULL COMMENT '客户ID',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='分片键非主键表' SHARDKEY=cust_id;

CREATE TABLE t_shard_ok (
    id BIGINT NOT NULL COMMENT '主键',
    cust_id BIGINT NOT NULL COMMENT '客户ID',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id, cust_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='合规分片表' SHARDKEY=cust_id;

SELECT cust_id, cust_name FROM t_customer WHERE ROWNUM <= 10;

SELECT cust_id, NVL(email, 'none') AS email FROM t_customer WHERE cust_id = 1001;

SELECT cust_id, DECODE(cust_level, 'gold', '金', 'silver', '银', '其他') AS lvl FROM t_customer WHERE cust_id = 1001;

SELECT cust_id, TO_CHAR(create_time, 'YYYYMMDD') AS dt FROM t_customer WHERE cust_id = 1001;

SELECT cust_id FROM t_customer WHERE cust_id = TO_NUMBER('1001');

SELECT cust_id FROM t_customer WHERE create_time > TO_DATE('20240101', 'YYYYMMDD');

SELECT cust_id, TRUNC(balance, 2) AS bal FROM t_account WHERE account_no = 'A001';

SELECT cust_id, LTRIM(phone, '0') AS phone FROM t_customer WHERE cust_id = 1001;

SELECT cust_id, ADD_MONTHS(create_time, -1) AS prev FROM t_customer WHERE cust_id = 1001;

SELECT cust_id, SUBSTR(id_no, 0, 6) AS prefix FROM t_customer WHERE cust_id = 1001;

SELECT cust_id FROM t_customer WHERE create_time < SYSDATE;

MERGE INTO t_customer c USING t_deposit d ON (c.cust_id = d.cust_id)
WHEN MATCHED THEN UPDATE SET c.cust_level = 'gold';

SELECT cust_id FROM t_customer WHERE LENGTH(cust_name) > 10;

SELECT cust_level, LISTAGG(cust_name, ',') WITHIN GROUP (ORDER BY cust_name) AS names
FROM t_customer WHERE cust_level = 'gold' GROUP BY cust_level;

SELECT cust_id FROM (SELECT cust_id FROM t_customer WHERE cust_level = 'gold') WHERE cust_id > 0;

SELECT condition FROM t_rule_demo WHERE id = 1;

SELECT cust_id FROM t_customer WHERE cust_name LIKE '%\_%' ESCAPE '\';

SELECT cust_id FROM t_customer WHERE cust_id < = 1000;

SELECT COUNT（cust_id） FROM t_customer WHERE cust_level = 'gold';

SELECT a.account_no, c.cust_name
FROM t_account a, t_customer c
WHERE a.cust_id = c.cust_id(+);

SELECT cust_id, cust_name FROM t_customer
START WITH cust_id = 1 CONNECT BY PRIOR cust_id = cust_id;

SELECT seq_order.nextval FROM t_order WHERE order_id < 100;

SELECT USERENV('INSTANCE') AS inst FROM t_customer WHERE cust_id = 1;

SELECT cust_id, ROW_NUMBER() OVER (ORDER BY create_time) AS rn
FROM t_customer WHERE cust_level = 'gold';

DECLARE cur_cust CURSOR FOR SELECT cust_id FROM t_customer;

ALTER TABLE t_transaction DROP PARTITION p202401;

SELECT cust_id FROM t_customer WHERE create_time > sysdate()-15;