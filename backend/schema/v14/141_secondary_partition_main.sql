-- v1.6.3.4 / D03：二级分区主表统计（REQ-02）
-- 槽位：v14/141。设计出处：DETAIL-v1.6.3.4 §4.4。
--
-- 对 table_type_stat 和 table_type_stat_item 分别执行八条独立 ADD COLUMN（共十六条）。
-- 不修改 v13 历史迁移及校验和。
--
-- 字段语义（§4.4）：
--   secondary_partition_main_tables     : 已确认主表数；旧记录/基础不可用为 null；
--                                         0 必须结合状态解读，只有 COMPLETE/0 才表示
--                                         范围内确认无主表
--   secondary_partition_check_state     : COMPLETE、PARTIAL、UNKNOWN、NOT_APPLICABLE、LEGACY
--   secondary_partition_candidates      : 已知 C 的去重候选数；枚举不完整时只是已知范围
--   secondary_partition_checked         : 已经得到 SECONDARY/NOT_SECONDARY 确定结论的候选数
--   secondary_partition_unknown         : 已尝试但无法判断的数
--   secondary_partition_unchecked       : 预算/护栏停止后未尝试的数
--   secondary_partition_inventory_state : COMPLETE、PARTIAL、FAILED、NOT_APPLICABLE、LEGACY；
--                                         独立目录覆盖状态，与 DDL 判明状态分开
--   secondary_partition_outside_shard   : main 中不在旧最终分片集合 S 的已确认数；
--                                         不是额外主表，不能再次相加
--
-- 计数验收公式（§4.4）：
--   candidates = checked + unknown + unchecked
--   outside_shard <= main <= checked <= candidates
--   confirmed negative = checked - main
--
-- 无破坏性数据迁移：所有旧记录六个新数字为 null、两个 state 均为 LEGACY（§7.2 第 3 条）。
-- 不将未知值补零，不更新历史审核 violations。

-- ── table_type_stat（实例汇总）八列 ──────────────────────────────
ALTER TABLE table_type_stat ADD COLUMN secondary_partition_main_tables INT NULL DEFAULT NULL;
ALTER TABLE table_type_stat ADD COLUMN secondary_partition_check_state VARCHAR(24) NOT NULL DEFAULT 'LEGACY';
ALTER TABLE table_type_stat ADD COLUMN secondary_partition_candidates INT NULL DEFAULT NULL;
ALTER TABLE table_type_stat ADD COLUMN secondary_partition_checked INT NULL DEFAULT NULL;
ALTER TABLE table_type_stat ADD COLUMN secondary_partition_unknown INT NULL DEFAULT NULL;
ALTER TABLE table_type_stat ADD COLUMN secondary_partition_unchecked INT NULL DEFAULT NULL;
ALTER TABLE table_type_stat ADD COLUMN secondary_partition_inventory_state VARCHAR(24) NOT NULL DEFAULT 'LEGACY';
ALTER TABLE table_type_stat ADD COLUMN secondary_partition_outside_shard INT NULL DEFAULT NULL;

-- ── table_type_stat_item（逐库明细）八列 ─────────────────────────
ALTER TABLE table_type_stat_item ADD COLUMN secondary_partition_main_tables INT NULL DEFAULT NULL;
ALTER TABLE table_type_stat_item ADD COLUMN secondary_partition_check_state VARCHAR(24) NOT NULL DEFAULT 'LEGACY';
ALTER TABLE table_type_stat_item ADD COLUMN secondary_partition_candidates INT NULL DEFAULT NULL;
ALTER TABLE table_type_stat_item ADD COLUMN secondary_partition_checked INT NULL DEFAULT NULL;
ALTER TABLE table_type_stat_item ADD COLUMN secondary_partition_unknown INT NULL DEFAULT NULL;
ALTER TABLE table_type_stat_item ADD COLUMN secondary_partition_unchecked INT NULL DEFAULT NULL;
ALTER TABLE table_type_stat_item ADD COLUMN secondary_partition_inventory_state VARCHAR(24) NOT NULL DEFAULT 'LEGACY';
ALTER TABLE table_type_stat_item ADD COLUMN secondary_partition_outside_shard INT NULL DEFAULT NULL;
