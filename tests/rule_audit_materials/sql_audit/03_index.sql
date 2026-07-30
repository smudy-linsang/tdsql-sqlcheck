-- ============================================================================
-- 文件审核测试物料 03：索引规范
-- 覆盖规则：R018,R019,R061,R062,R063,R065,R066,R067
-- 说明：R064（覆盖索引建议）与 R068（JOIN关联字段建索引建议）需表元数据，
--       文件审核不触发，在「在线元数据审核」场景验证（见测试说明书）。
-- ============================================================================

-- @case: R018_01
-- @rules: R018
-- @rules.dist: R018,R077
-- @note: 单表索引 6 个（含主键），超过建议的 5 个
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

-- @case: R019_01
-- @rules: R019
-- @rules.dist: R019,R077
-- @note: idx_a(c1) 是 idx_ab(c1,c2) 的前缀，存在冗余索引
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

-- @case: R061_01
-- @rules: R061
-- @rules.dist: R061,R077
-- @note: 普通索引未以 idx_ 开头
CREATE TABLE t_idxname (
    id BIGINT NOT NULL COMMENT '主键',
    c1 INT NOT NULL COMMENT '列1',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id),
    INDEX my_index (c1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='索引命名表';

-- @case: R062_01
-- @rules: R062
-- @rules.dist: R062,R077
-- @note: 复合索引 (status,cust_id) 区分度低的字段在前
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

-- @case: R063_01
-- @rules: R063
-- @rules.dist: R063,R077
-- @note: 低区分度字段 status 单独建索引
CREATE TABLE t_lowcard (
    id BIGINT NOT NULL COMMENT '主键',
    status INT NOT NULL COMMENT '状态',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='低区分度索引表';

-- @case: R065_01
-- @rules: R033,R065
-- @rules.dist: R033,R065,R077
-- @note: 复合索引字段 6 个，超过建议的 5 个（表名 t_idxcols 复数共触发 R033）
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

-- @case: R066_01
-- @rules: R011,R066
-- @rules.dist: R011,R066,R077
-- @note: TEXT 大字段建索引（TEXT 同时触发 R011）
CREATE TABLE t_blobidx (
    id BIGINT NOT NULL COMMENT '主键',
    content TEXT NOT NULL COMMENT '正文',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id),
    INDEX idx_content (content)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='大字段索引表';

-- @case: R067_01
-- @rules: R067
-- @rules.dist: R067,R077
-- @note: VARCHAR(200) 长度>100 建索引，建议前缀索引
CREATE TABLE t_prefixidx (
    id BIGINT NOT NULL COMMENT '主键',
    cust_name VARCHAR(200) NOT NULL COMMENT '客户名',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id),
    INDEX idx_name (cust_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='前缀索引表';
