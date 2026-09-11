-- ============================================================================
-- TDSQL 自动拉取的最新在线元数据描述文件
-- 目标实例: D36-分布式库-含子表命名
-- 目标数据库: uat_d_1636_dist
-- 提取日期: 2026-09-11 22:18:09
-- ============================================================================

-- SQL Object: CREATE TABLE
-- Table: biz_tab_00
CREATE TABLE `biz_tab_00` (
  `id` bigint NOT NULL COMMENT 'ID',
  `name` varchar(64) NOT NULL DEFAULT '' COMMENT 'name',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='D36 real table';

-- ============================================================
-- [SKIPPED] SQL Object: CREATE TABLE
-- Object Name: biz_tab_01
-- Skip Reason: (1045, "Access denied for user 'ro'@'%' to database 'uat_d_1636_dist'")
-- ============================================================

-- SQL Object: CREATE TABLE
-- Table: biz_tab_02
CREATE TABLE `biz_tab_02` (
  `id` bigint NOT NULL COMMENT 'ID',
  `name` varchar(64) NOT NULL DEFAULT '' COMMENT 'name',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='D36 real table';

-- SQL Object: CREATE TABLE
-- Table: biz_tab_03
CREATE TABLE `biz_tab_03` (
  `id` bigint NOT NULL COMMENT 'ID',
  `name` varchar(64) NOT NULL DEFAULT '' COMMENT 'name',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='D36 real table';

-- SQL Object: CREATE TABLE
-- Table: biz_tab_04
CREATE TABLE `biz_tab_04` (
  `id` bigint NOT NULL COMMENT 'ID',
  `name` varchar(64) NOT NULL DEFAULT '' COMMENT 'name',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='D36 real table';

-- SQL Object: CREATE TABLE
-- Table: biz_tab_05
CREATE TABLE `biz_tab_05` (
  `id` bigint NOT NULL COMMENT 'ID',
  `name` varchar(64) NOT NULL DEFAULT '' COMMENT 'name',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='D36 real table';

-- SQL Object: CREATE TABLE
-- Table: cus_bas_merge_log
CREATE TABLE `cus_bas_merge_log` (
  `id` bigint NOT NULL COMMENT 'ID',
  `payload` varchar(128) NOT NULL DEFAULT '' COMMENT 'p',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='parent table';

-- [SKIPPED-SUMMARY] TDSQL 物理分片子表 2 张已跳过（父表 DDL 已纳管），明细见任务跳过清单
