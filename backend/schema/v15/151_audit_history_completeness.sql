-- v1.6.3.6 / SIT-A / R2-M-05：审核结果完整性留痕
-- 全部为新增列，无删除/重命名/类型变更；回滚只需停止读取新列。
-- NULL 语义：本特性上线前的记录，口径未知。【严禁回填】——回填即伪造历史口径。
-- 注意：列类型写 INT（不写 INT(11)）——migrator 结构验收对 MySQL 8 期望 int，
-- 写 int(11) 会在生产启动校验失败。

ALTER TABLE audit_history
    ADD COLUMN skipped_objects INT NULL DEFAULT NULL
        COMMENT '提取阶段跳过对象总数；NULL=本特性上线前的记录';
ALTER TABLE audit_history
    ADD COLUMN skipped_benign INT NULL DEFAULT NULL
        COMMENT '其中良性跳过（TDSQL 物理分片子表）';
ALTER TABLE audit_history
    ADD COLUMN skipped_abnormal INT NULL DEFAULT NULL
        COMMENT '其中异常跳过（权限/超时/Proxy 故障等，需人工核实）';
ALTER TABLE audit_history
    ADD COLUMN omitted_results INT NULL DEFAULT NULL
        COMMENT 'results_json 因超包限压缩而省略的通过项条数；0=未压缩，NULL=上线前记录';
