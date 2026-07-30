-- ============================================================================
-- 文件审核测试物料 01：命名规范 + DDL 规范
-- 覆盖规则：R001,R002,R003,R004,R005,R006,R007,R008,R009,R010,R011,
--           R023,R024,R026,R027,R028,R029,R032,R033,R034,R036,R037,
--           R078,R097,R098,R115,R116,R117,R118
-- 用法：python tests/rule_audit_materials/verify_rules.py
-- 约定：@rules 为默认期望；@rules.dist / @rules.cent 为分实例口径期望（可选）。
--       DDL 通用规则在分布式口径下会额外共触发 R077（分布式建表必须声明分片键），
--       属正确行为，故用 @rules.dist 标注含 R077 的完整集合。
-- 已知：R038（大表禁自增主键）依赖解析器 raw_type 含 auto_increment，而当前
--       解析器 raw_type 仅含数据类型、不含 AUTO_INCREMENT，故文件审核路径无法
--       触发，列入 harness KNOWN_DEAD（见测试说明书"已知限制"）。
-- ============================================================================

-- @case: R001_01
-- @rules: R001
-- @rules.dist: R001,R077
-- @note: 表名含大写字母，违反命名规范
CREATE TABLE User_Profile (
    id BIGINT NOT NULL COMMENT '主键',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户档案';

-- @case: R002_01
-- @rules: R002
-- @rules.dist: R002,R077
-- @note: 表名 order 是 MySQL 保留关键字
CREATE TABLE `order` (
    order_id BIGINT NOT NULL COMMENT '订单号',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (order_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='订单表';

-- @case: DDL_MULTI_01
-- @rules: R003,R004,R005,R028,R029,R036,R037
-- @rules.dist: R003,R004,R005,R028,R029,R036,R037,R077
-- @note: 一条"裸"建表同时触发 缺主键/缺引擎/缺字符集/缺表注释/缺列注释/缺时间戳列/缺逻辑删除
CREATE TABLE t_bare (
    col1 INT
);

-- @case: R004_01
-- @rules: R004
-- @rules.dist: R004,R077
-- @note: 存储引擎为 MyISAM
CREATE TABLE t_engine_bad (
    id BIGINT NOT NULL COMMENT '主键',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COMMENT='引擎不合规';

-- @case: R005_01
-- @rules: R005
-- @rules.dist: R005,R077
-- @note: 字符集为 latin1
CREATE TABLE t_charset_bad (
    id BIGINT NOT NULL COMMENT '主键',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=latin1 COMMENT='字符集不合规';

-- @case: R006_01
-- @rules: R006
-- @rules.dist: R006,R077
-- @note: 使用 ENUM 类型
CREATE TABLE t_enum (
    id BIGINT NOT NULL COMMENT '主键',
    status ENUM('A','B','C') NOT NULL COMMENT '状态枚举',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='枚举类型表';

-- @case: R007_01
-- @rules: R007,R033
-- @rules.dist: R007,R033,R077
-- @note: 使用 TIMESTAMP 类型（表名 t_ts 复数同时触发 R033）
CREATE TABLE t_ts (
    id BIGINT NOT NULL COMMENT '主键',
    log_time TIMESTAMP NOT NULL COMMENT '日志时间',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='时间戳类型表';

-- @case: R008_01
-- @rules: R008
-- @rules.dist: R008,R077
-- @note: 表级外键约束（解析器仅识别 CREATE TABLE 表级 FOREIGN KEY，ALTER 外键不触发）
CREATE TABLE t_order_rel (
    id BIGINT NOT NULL PRIMARY KEY COMMENT '主键',
    cust_id BIGINT NOT NULL COMMENT '客户ID',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    FOREIGN KEY (cust_id) REFERENCES t_customer (cust_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='外键表';

-- @case: R009_01
-- @rules: R009
-- @rules.dist: R009,R077
-- @note: 财务字段 amount 使用 DOUBLE 类型
CREATE TABLE t_finance (
    id BIGINT NOT NULL COMMENT '主键',
    amount DOUBLE NOT NULL COMMENT '金额',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='财务表';

-- @case: R010_01
-- @rules: R010
-- @rules.dist: R010,R077
-- @note: VARCHAR 长度 3000 超过建议的 2000
CREATE TABLE t_longvarchar (
    id BIGINT NOT NULL COMMENT '主键',
    remark VARCHAR(3000) NOT NULL COMMENT '备注',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='超长字段表';

-- @case: R011_01
-- @rules: R011
-- @rules.dist: R011,R077
-- @note: 活跃表使用 TEXT 类型
CREATE TABLE t_text (
    id BIGINT NOT NULL COMMENT '主键',
    content TEXT NOT NULL COMMENT '正文',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='大字段表';

-- @case: R023_01
-- @rules: R003,R004,R005,R023,R028,R036,R037
-- @scope: distributed
-- @note: CREATE TABLE ... SELECT，分布式不支持（CTAS 跳过 R077；缺主键等共触发）
CREATE TABLE t_copy AS SELECT cust_id, cust_name FROM t_customer;

-- @case: R024_01
-- @rules: R003,R024,R032,R036,R037
-- @scope: distributed
-- @note: 临时表 R024(分布式)+R032(通用)，缺主键/时间戳/逻辑删除共触发
CREATE TEMPORARY TABLE tmp_calc (
    id BIGINT NOT NULL COMMENT '主键'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='临时表';

-- @case: R026_01
-- @rules: R026,R073
-- @note: ALTER MODIFY 可能缩短字段长度（ALTER 同时触发 R073）
ALTER TABLE t_customer MODIFY COLUMN phone VARCHAR(10) NOT NULL COMMENT '手机号';

-- @case: R027_01
-- @rules: R027,R073
-- @note: DROP DATABASE 不可逆（同时触发 R073）
DROP DATABASE legacy_db;

-- @case: R033_01
-- @rules: R033
-- @rules.dist: R033,R077
-- @note: 表名 users 为复数形式
CREATE TABLE users (
    id BIGINT NOT NULL COMMENT '主键',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户表复数';

-- @case: R034_01
-- @rules: R034
-- @rules.dist: R034,R077
-- @note: 备份表名含 bak 但缺少 YYYYMMDD 日期后缀
CREATE TABLE t_order_bak (
    id BIGINT NOT NULL COMMENT '主键',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='备份表';

-- @case: R030_R031_01
-- @rules: R030,R031
-- @note: 创建自定义函数（R030 禁视图/存储过程/触发器/函数 + R031 禁自定义函数）
CREATE FUNCTION fn_calc(a INT, b INT) RETURNS INT BEGIN RETURN a + b; END;

-- @case: R030_02
-- @rules: R030
-- @note: 创建触发器（R030 禁视图/存储过程/触发器/函数）
CREATE TRIGGER trg_audit BEFORE INSERT ON t_customer FOR EACH ROW SET NEW.create_time = NOW();

-- @case: R078_01
-- @rules: R078
-- @rules.dist: R077,R078
-- @note: 使用 Oracle 专有数据类型 NUMBER/VARCHAR2
CREATE TABLE t_oratype (
    id BIGINT NOT NULL COMMENT '主键',
    amt NUMBER(18,2) NOT NULL COMMENT '金额',
    code VARCHAR2(32) NOT NULL COMMENT '编码',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Oracle类型表';

-- @case: R097_01
-- @rules: R077,R097
-- @scope: distributed
-- @note: 字段 DEFAULT 使用函数表达式（非 CURRENT_TIMESTAMP），分布式不支持
CREATE TABLE t_deffunc (
    id BIGINT NOT NULL COMMENT '主键',
    data_dt CHAR(8) DEFAULT (DATE_FORMAT(NOW(), '%Y%m%d')) COMMENT '数据日期',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='默认值函数表';

-- @case: R098_01
-- @rules: R036,R037,R098
-- @rules.dist: R036,R037,R077,R098
-- @note: 非整型字段做 HASH 分区（PARTITION 子句致时间戳/逻辑删除列解析丢失，共触发 R036/R037）
CREATE TABLE t_hashpart (
    id BIGINT NOT NULL COMMENT '主键',
    region_code VARCHAR(16) NOT NULL COMMENT '地区码',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='哈希分区表'
PARTITION BY HASH(region_code) PARTITIONS 4;

-- @case: R115_01
-- @rules: R036,R037,R077,R115
-- @rules.cent: R036,R037
-- @note: 主键 VARCHAR(300) 超过 250（列级主键），update/delete..limit 将受限（R115 仅分布式）
CREATE TABLE t_longpk (
    biz_key VARCHAR(300) NOT NULL PRIMARY KEY COMMENT '业务主键'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='长主键表';

-- @case: R116_01
-- @rules: R036,R037,R116
-- @scope: distributed
-- @note: 多字段联合分片键，TDSQL 仅支持单字段（R116 仅分布式；SHARDKEY 子句致 R036/R037 共触发）
CREATE TABLE t_multishard (
    id BIGINT NOT NULL COMMENT '主键',
    cust_id BIGINT NOT NULL COMMENT '客户ID',
    region_id INT NOT NULL COMMENT '地区ID',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id, cust_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='多字段分片键表' SHARDKEY=cust_id,region_id;

-- @case: R117_01
-- @rules: R117
-- @scope: distributed
-- @note: 分片键类型为 DECIMAL，不在许可类型内（分片键在主键中，R077 第二分支不触发；R117 仅分布式）
CREATE TABLE t_shardtype (
    id BIGINT NOT NULL COMMENT '主键',
    amt_key DECIMAL(18,2) NOT NULL COMMENT '金额分片键',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id, amt_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='分片键类型表' SHARDKEY=amt_key;

-- @case: R118_01
-- @rules: R054,R077,R118
-- @scope: distributed
-- @note: 分片键未声明 NOT NULL（且不在主键中，R054/R077 共触发；R118 仅分布式）
CREATE TABLE t_shardnull (
    id BIGINT NOT NULL COMMENT '主键',
    cust_id BIGINT COMMENT '客户ID分片键',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='分片键可空表' SHARDKEY=cust_id;
