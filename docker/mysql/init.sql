-- ============================================================
-- TDSQL SQL审核工具 - MySQL模拟环境初始化脚本
-- 模拟TDSQL分布式数据库集群环境，用于UAT测试
-- ============================================================

-- 创建测试数据库
CREATE DATABASE IF NOT EXISTS tdsql_test DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
USE tdsql_test;

-- ============================================================
-- 1. 分片表 - 订单主表（SHARDKEY=user_id）
-- ============================================================
CREATE TABLE t_order (
    id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    order_no VARCHAR(32) NOT NULL COMMENT '订单编号',
    user_id BIGINT NOT NULL COMMENT '用户ID（分片键）',
    total_amount DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '订单总金额',
    status TINYINT NOT NULL DEFAULT 0 COMMENT '订单状态: 0待支付 1已支付 2已发货 3已完成 4已取消',
    is_deleted TINYINT NOT NULL DEFAULT 0 COMMENT '逻辑删除标记',
    create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_order_no (order_no),
    KEY idx_user_id (user_id),
    KEY idx_status_create (status, create_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='订单主表 SHARDKEY=user_id';

-- ============================================================
-- 2. 分片表 - 订单明细表（SHARDKEY=order_id）
-- ============================================================
CREATE TABLE t_order_detail (
    id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    order_id BIGINT NOT NULL COMMENT '订单ID（分片键）',
    product_id BIGINT NOT NULL COMMENT '商品ID',
    product_name VARCHAR(128) NOT NULL COMMENT '商品名称',
    quantity INT NOT NULL DEFAULT 1 COMMENT '购买数量',
    unit_price DECIMAL(12,2) NOT NULL COMMENT '单价',
    subtotal DECIMAL(12,2) NOT NULL COMMENT '小计金额',
    is_deleted TINYINT NOT NULL DEFAULT 0 COMMENT '逻辑删除标记',
    create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (id),
    KEY idx_order_id (order_id),
    KEY idx_product_id (product_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='订单明细表 SHARDKEY=order_id';

-- ============================================================
-- 3. 单表 - 用户表
-- ============================================================
CREATE TABLE t_user (
    id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    user_name VARCHAR(64) NOT NULL COMMENT '用户名',
    phone VARCHAR(20) NOT NULL COMMENT '手机号',
    email VARCHAR(128) DEFAULT NULL COMMENT '邮箱',
    status TINYINT NOT NULL DEFAULT 1 COMMENT '状态: 0禁用 1启用',
    is_deleted TINYINT NOT NULL DEFAULT 0 COMMENT '逻辑删除标记',
    create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_user_name (user_name),
    UNIQUE KEY uk_phone (phone),
    KEY idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='用户表';

-- ============================================================
-- 4. 广播表 - 配置表（BROADCAST）
-- ============================================================
CREATE TABLE t_config (
    id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    config_key VARCHAR(128) NOT NULL COMMENT '配置键',
    config_value TEXT COMMENT '配置值',
    config_desc VARCHAR(256) DEFAULT NULL COMMENT '配置描述',
    is_deleted TINYINT NOT NULL DEFAULT 0 COMMENT '逻辑删除标记',
    create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_config_key (config_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='系统配置表 BROADCAST';

-- ============================================================
-- 5. 商品表 - 正常表（用于审核测试）
-- ============================================================
CREATE TABLE t_product (
    id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    product_code VARCHAR(32) NOT NULL COMMENT '商品编码',
    product_name VARCHAR(128) NOT NULL COMMENT '商品名称',
    category_id INT NOT NULL COMMENT '分类ID',
    price DECIMAL(12,2) NOT NULL COMMENT '价格',
    stock INT NOT NULL DEFAULT 0 COMMENT '库存',
    status TINYINT NOT NULL DEFAULT 1 COMMENT '状态: 0下架 1上架',
    is_deleted TINYINT NOT NULL DEFAULT 0 COMMENT '逻辑删除标记',
    create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_product_code (product_code),
    KEY idx_category (category_id),
    KEY idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='商品表';

-- ============================================================
-- 6. 无索引表 - 用于慢查询测试
-- ============================================================
CREATE TABLE t_no_index (
    id BIGINT NOT NULL AUTO_INCREMENT,
    biz_key VARCHAR(64) NOT NULL COMMENT '业务键',
    biz_value TEXT COMMENT '业务值',
    create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='无索引测试表';

-- ============================================================
-- 7. 字符集不一致表 - 用于字符集检查测试
-- ============================================================
CREATE TABLE t_charset_latin1 (
    id BIGINT NOT NULL AUTO_INCREMENT,
    name VARCHAR(64) CHARACTER SET latin1 COLLATE latin1_swedish_ci NOT NULL COMMENT '名称(latin1)',
    description VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci DEFAULT NULL COMMENT '描述(utf8mb4)',
    create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=latin1 COLLATE=latin1_swedish_ci COMMENT='字符集不一致测试表';

-- ============================================================
-- 8. 大表模拟 - 插入大量数据
-- ============================================================
CREATE TABLE t_large_order_log (
    id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    order_id BIGINT NOT NULL COMMENT '订单ID',
    action VARCHAR(32) NOT NULL COMMENT '操作类型',
    operator VARCHAR(64) NOT NULL COMMENT '操作人',
    remark VARCHAR(512) DEFAULT NULL COMMENT '备注',
    create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (id),
    KEY idx_order_id (order_id),
    KEY idx_create_time (create_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='订单操作日志大表 SHARDKEY=order_id';

-- ============================================================
-- 9. 二级分区表模拟（TDSQL特有）
-- ============================================================
CREATE TABLE t_sub_partition_tdsql_subp (
    id BIGINT NOT NULL AUTO_INCREMENT,
    user_id BIGINT NOT NULL,
    data_value VARCHAR(128),
    create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_user_id (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='二级分区表';

-- ============================================================
-- 10. 退款表 - 用于复杂审核场景
-- ============================================================
CREATE TABLE t_refund (
    id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    refund_no VARCHAR(32) NOT NULL COMMENT '退款编号',
    order_id BIGINT NOT NULL COMMENT '订单ID',
    user_id BIGINT NOT NULL COMMENT '用户ID',
    refund_amount DECIMAL(12,2) NOT NULL COMMENT '退款金额',
    refund_reason VARCHAR(256) DEFAULT NULL COMMENT '退款原因',
    status TINYINT NOT NULL DEFAULT 0 COMMENT '状态: 0待审核 1已退款 2已拒绝',
    is_deleted TINYINT NOT NULL DEFAULT 0 COMMENT '逻辑删除标记',
    create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uk_refund_no (refund_no),
    KEY idx_order_id (order_id),
    KEY idx_user_id (user_id),
    KEY idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='退款表 SHARDKEY=user_id';

-- ============================================================
-- 插入测试数据
-- ============================================================

-- 用户数据
INSERT INTO t_user (user_name, phone, email, status) VALUES
('zhangsan', '13800138001', 'zhangsan@test.com', 1),
('lisi', '13800138002', 'lisi@test.com', 1),
('wangwu', '13800138003', 'wangwu@test.com', 0),
('zhaoliu', '13800138004', NULL, 1),
('qianqi', '13800138005', 'qianqi@test.com', 1);

-- 商品数据
INSERT INTO t_product (product_code, product_name, category_id, price, stock, status) VALUES
('P001', 'TDSQL实战指南', 1, 89.00, 100, 1),
('P002', 'MySQL高性能', 1, 128.00, 50, 1),
('P003', '分布式数据库原理', 1, 99.00, 200, 1),
('P004', '数据库运维手册', 2, 75.00, 0, 0),
('P005', 'SQL优化实战', 1, 69.00, 150, 1);

-- 订单数据
INSERT INTO t_order (order_no, user_id, total_amount, status) VALUES
('ORD20240101001', 1, 217.00, 3),
('ORD20240101002', 2, 128.00, 1),
('ORD20240102001', 1, 99.00, 3),
('ORD20240102002', 3, 75.00, 4),
('ORD20240103001', 4, 168.00, 2),
('ORD20240103002', 5, 89.00, 3),
('ORD20240104001', 2, 197.00, 0),
('ORD20240104002', 1, 256.00, 3);

-- 订单明细数据
INSERT INTO t_order_detail (order_id, product_id, product_name, quantity, unit_price, subtotal) VALUES
(1, 1, 'TDSQL实战指南', 1, 89.00, 89.00),
(1, 3, '分布式数据库原理', 1, 99.00, 99.00),
(1, 5, 'SQL优化实战', 1, 29.00, 29.00),
(2, 2, 'MySQL高性能', 1, 128.00, 128.00),
(3, 3, '分布式数据库原理', 1, 99.00, 99.00),
(5, 1, 'TDSQL实战指南', 1, 89.00, 89.00),
(5, 5, 'SQL优化实战', 1, 79.00, 79.00),
(6, 1, 'TDSQL实战指南', 1, 89.00, 89.00),
(8, 2, 'MySQL高性能', 1, 128.00, 128.00),
(8, 3, '分布式数据库原理', 1, 99.00, 99.00),
(8, 5, 'SQL优化实战', 1, 29.00, 29.00);

-- 退款数据
INSERT INTO t_refund (refund_no, order_id, user_id, refund_amount, refund_reason, status) VALUES
('REF20240105001', 4, 3, 75.00, '商品已下架', 1),
('REF20240105002', 7, 2, 197.00, '用户取消', 2);

-- 配置数据
INSERT INTO t_config (config_key, config_value, config_desc) VALUES
('max_order_amount', '100000', '单笔订单最大金额'),
('refund_timeout_hours', '72', '退款超时时间(小时)'),
('enable_flash_sale', 'true', '是否开启秒杀'),
('default_page_size', '20', '默认分页大小');

-- 无索引表数据
INSERT INTO t_no_index (biz_key, biz_value) VALUES
('KEY001', 'value001'),
('KEY002', 'value002'),
('KEY003', 'value003'),
('KEY004', 'value004'),
('KEY005', 'value005');

-- 字符集表数据
INSERT INTO t_charset_latin1 (name, description) VALUES
('test1', '描述1'),
('test2', '描述2');

-- 大表数据（批量插入2000行模拟）
DELIMITER //
CREATE PROCEDURE IF NOT EXISTS insert_large_data()
BEGIN
    DECLARE i INT DEFAULT 1;
    WHILE i <= 2000 DO
        INSERT INTO t_large_order_log (order_id, action, operator, remark)
        VALUES (
            FLOOR(1 + RAND() * 8),
            ELT(1 + FLOOR(RAND() * 4), 'CREATE', 'PAY', 'SHIP', 'COMPLETE'),
            ELT(1 + FLOOR(RAND() * 3), 'system', 'admin', 'operator'),
            CONCAT('操作记录-', i)
        );
        SET i = i + 1;
    END WHILE;
END //
DELIMITER ;
CALL insert_large_data();
DROP PROCEDURE IF EXISTS insert_large_data;

-- ============================================================
-- 开启慢查询日志
-- ============================================================
SET GLOBAL slow_query_log = 'ON';
SET GLOBAL long_query_time = 0.5;
SET GLOBAL log_output = 'TABLE';
SET GLOBAL log_queries_not_using_indexes = 'ON';
SET GLOBAL min_examined_row_limit = 0;

-- ============================================================
-- 执行一些慢查询来生成performance_schema数据
-- ============================================================
-- 全表扫描（无索引）
SELECT * FROM t_no_index WHERE biz_value LIKE '%value%' AND biz_key <> 'KEY001';
SELECT * FROM t_no_index WHERE biz_value = 'value003';
SELECT COUNT(*) FROM t_no_index WHERE biz_key LIKE 'KEY%';

-- 深分页查询
SELECT * FROM t_large_order_log LIMIT 1000, 50;
SELECT * FROM t_large_order_log WHERE remark LIKE '%操作%' LIMIT 500, 30;

-- 无索引JOIN
SELECT a.*, b.product_name FROM t_order_detail a, t_product b WHERE a.product_name = b.product_name;

-- 函数索引扫描
SELECT * FROM t_order WHERE DATE(create_time) = CURDATE();
SELECT * FROM t_user WHERE LEFT(phone, 3) = '138';

-- 模糊查询
SELECT * FROM t_product WHERE product_name LIKE '%数据库%';
SELECT * FROM t_order WHERE order_no LIKE '%2401%';

-- ============================================================
-- 刷新performance_schema数据
-- ============================================================
FLUSH STATUS;
