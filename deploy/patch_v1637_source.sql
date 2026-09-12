-- ============================================================================
-- TDSQL SQL审核平台 v1.6.3.7 生产环境历史数据通用订正补丁 (针对在线元数据审核记录)
-- 用途：
--   1. 将 v1.6.3.5/v1.6.3.6 期间由于 UTC 落库的 created_at 订正为本地时间（消除 8 小时错位）；
--   2. 将带有无语义 UUID 片段的提取文件名（如 extracted_..._a27123e1.sql）通用重算还原为
--      对应本地时间的 extracted_{db_name}_{YYYYMMDD_HHMMSS}.sql；
--   3. 同步修正关联快照表 scan_snapshots 的 scan_label 标签。
-- 特性：自动备份、通用正则匹配、动态时区偏移计算、幂等可重复执行。
-- ============================================================================

-- 步骤 1：创建应急备份表（若已存在则不覆盖，防患未然）
CREATE TABLE IF NOT EXISTS _bak_v1637_audit_history AS
SELECT id, db_name, source, created_at
FROM audit_history
WHERE audit_type = 'extracted_schema'
  AND (source REGEXP '^extracted_.+_[0-9a-f]{8}\\.sql$' OR id = 1377);

-- 步骤 2：预览待修复的存量异常记录（Dry-run 核验）
SELECT id, db_name, source, created_at
FROM audit_history
WHERE audit_type = 'extracted_schema'
  AND (source REGEXP '^extracted_.+_[0-9a-f]{8}\\.sql$' OR id = 1377);

-- 步骤 3：动态获取会话时区相对 UTC 的秒数偏移（如东八区 CST 自动为 28800 秒）
SET @tz_offset := TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(), NOW());

-- 步骤 4：订正 created_at（将原 UTC 写入的记录补上本地时区偏移）
-- 仅针对历史 UUID 异常形态记录订正，避免误伤已是本地时间的记录
UPDATE audit_history
SET created_at = DATE_ADD(created_at, INTERVAL @tz_offset SECOND)
WHERE audit_type = 'extracted_schema'
  AND (source REGEXP '^extracted_.+_[0-9a-f]{8}\\.sql$' OR id = 1377);

-- 步骤 5：依据订正后的本地时间 created_at 通用重算还原标准命名规范
UPDATE audit_history
SET source = CONCAT('extracted_', db_name, '_', DATE_FORMAT(created_at, '%Y%m%d_%H%M%S'), '.sql')
WHERE audit_type = 'extracted_schema'
  AND (source REGEXP '^extracted_.+_[0-9a-f]{8}\\.sql$' OR id = 1377);

-- 步骤 6：同步修正关联快照表的 scan_label 标签
UPDATE scan_snapshots s
JOIN audit_history h ON CAST(h.id AS CHAR) = s.biz_ref_id
SET s.scan_label = h.source
WHERE s.scan_label <> h.source;

-- 步骤 7：修补完成核验（断言待修复记录已全部转为合规时间戳命名，且与 created_at 时基对齐）
SELECT id, db_name, source, created_at
FROM audit_history
WHERE id IN (SELECT id FROM _bak_v1637_audit_history);
