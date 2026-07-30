-- ============================================================================
-- 文件审核测试物料 04：分布式建表规范（文件审核可触发部分）
-- 覆盖规则：R054,R077
-- 说明：R048/R055/R056/R057/R058/R060 需真实表元数据（分片键/分片表标记），
--       文件审核（B类通道，无元数据）下不触发，统一在「在线元数据审核」
--       场景验证（见测试说明书 第5章）。
-- ============================================================================

-- @case: R077_01
-- @rules: R077
-- @scope: distributed
-- @note: 分布式建表未声明 SHARDKEY/BROADCAST（R077 仅分布式口径触发）
CREATE TABLE t_noshard (
    id BIGINT NOT NULL COMMENT '主键',
    cust_id BIGINT NOT NULL COMMENT '客户ID',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='缺分片键表';

-- @case: R054_R077_01
-- @rules: R054,R077
-- @scope: distributed
-- @note: 声明了分片键 cust_id，但不在主键中（R054 + R077 第二分支同时触发）
CREATE TABLE t_shard_notpk (
    id BIGINT NOT NULL COMMENT '主键',
    cust_id BIGINT NOT NULL COMMENT '客户ID',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='分片键非主键表' SHARDKEY=cust_id;

-- @case: DIST_CLEAN_01
-- @rules:
-- @scope: distributed
-- @note: 合规的分布式建表（分片键在主键中），作为对照样例，期望零违规
CREATE TABLE t_shard_ok (
    id BIGINT NOT NULL COMMENT '主键',
    cust_id BIGINT NOT NULL COMMENT '客户ID',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
    is_deleted TINYINT DEFAULT 0 COMMENT '逻辑删除',
    PRIMARY KEY (id, cust_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='合规分片表' SHARDKEY=cust_id;
