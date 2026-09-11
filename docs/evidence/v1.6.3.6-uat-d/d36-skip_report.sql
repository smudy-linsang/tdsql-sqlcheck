-- ============================================================
-- [跳过] 本次有 1 个对象未能读取 DDL，未纳入审核（共跳过 3 个），请人工核实。
-- ============================================================

-- SQL Object: CREATE TABLE
-- Table: biz_tab_00
CREATE TABLE `biz_tab_00` (
  `id` bigint NOT NULL COMMENT 'ID',
  `name` varchar(64) NOT NULL DEFAULT '' COMMENT 'name',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='D36 real table'

-- SQL Object: CREATE TABLE
-- Table: biz_tab_02
CREATE TABLE `biz_tab_02` (
  `id` bigint NOT NULL COMMENT 'ID',
  `name` varchar(64) NOT NULL DEFAULT '' COMMENT 'name',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='D36 real table'

-- SQL Object: CREATE TABLE
-- Table: biz_tab_03
CREATE TABLE `biz_tab_03` (
  `id` bigint NOT NULL COMMENT 'ID',
  `name` varchar(64) NOT NULL DEFAULT '' COMMENT 'name',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='D36 real table'

-- SQL Object: CREATE TABLE
-- Table: biz_tab_04
CREATE TABLE `biz_tab_04` (
  `id` bigint NOT NULL COMMENT 'ID',
  `name` varchar(64) NOT NULL DEFAULT '' COMMENT 'name',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='D36 real table'

-- SQL Object: CREATE TABLE
-- Table: biz_tab_05
CREATE TABLE `biz_tab_05` (
  `id` bigint NOT NULL COMMENT 'ID',
  `name` varchar(64) NOT NULL DEFAULT '' COMMENT 'name',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='D36 real table'

-- SQL Object: CREATE TABLE
-- Table: cus_bas_merge_log
CREATE TABLE `cus_bas_merge_log` (
  `id` bigint NOT NULL COMMENT 'ID',
  `payload` varchar(128) NOT NULL DEFAULT '' COMMENT 'p',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='parent table'