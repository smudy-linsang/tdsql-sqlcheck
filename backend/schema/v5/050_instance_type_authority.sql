-- ============================================================================
-- V1.5.1 实例类型判定重构：接入 ZK 管控面权威源 + 管理员锁定
-- 全部为新增列，无删除/重命名/类型变更；回滚只需停止读取新列
-- 设计依据：docs/DESIGN-v1.5.1-实例类型判定重构.md §5
-- ============================================================================

-- ── E-1 ZK 管控面判定结果（S1 权威源）──
-- 存 TDSQL 原始形态而非业务语义：便于与赤兔/ZK 对账，且形态扩展时数据层不用改。
ALTER TABLE tdsql_connections
    ADD COLUMN zk_instance_kind VARCHAR(16) NULL DEFAULT NULL
        COMMENT 'ZK 实例形态 noshard=集中式/groupshard=分布式；NULL=未同步';

ALTER TABLE tdsql_connections
    ADD COLUMN zk_instance_id VARCHAR(64) NOT NULL DEFAULT ''
        COMMENT 'ZK 实例标识 set_xxx/group_xxx，供人工核对';

ALTER TABLE tdsql_connections
    ADD COLUMN zk_synced_at DATETIME NULL DEFAULT NULL
        COMMENT '最近一次 ZK 同步时间；NULL=从未同步';

-- ── E-2 管理员锁定（S0 终审）──
-- 拆两列而非单列三态：解锁后保留上次锁定值，重新加锁可回显。
ALTER TABLE tdsql_connections
    ADD COLUMN instance_type_locked TINYINT NOT NULL DEFAULT 0
        COMMENT '1=管理员锁定实例类型，优先级高于一切自动判定源';

ALTER TABLE tdsql_connections
    ADD COLUMN instance_type_locked_value VARCHAR(16) NOT NULL DEFAULT ''
        COMMENT '锁定值 distributed|centralized';

-- ── E-3 作废 V1.5 被证伪的探测存量（一次性，随本迁移仅执行一次）──
-- V1.5 的探测经实测为常量函数（对任何可连实例恒写 distributed，见设计文档 §2.4），
-- 存量 detected_instance_type 全部无鉴别力。若不作废，保守合并会继续采用
-- 这批脏数据，集中式实例的声明依旧被压制，G1 仍不达成。
-- 这不是"凭猜测回填"（明令禁止的是往权威字段写猜测值）——这是清除已被
-- 实测证伪的数据，将判定如实下沉至声明值，等待 ZK 同步或重新探测。
UPDATE tdsql_connections
   SET detected_instance_type = NULL,
       instance_type_detected_at = NULL,
       instance_type_probe_error = 'v1.5 探测判据经实测证伪，结果已作废（v1.5.1 迁移）'
 WHERE detected_instance_type IS NOT NULL;

-- ── 存量数据迁移（ZK 列）：不需要，且明令禁止 ──
-- zk_instance_kind 保持 NULL，判定自动下沉至声明值。
-- 任何回填都是凭猜测写入权威字段，与本次事故同源。
