-- ============================================================================
-- TDSQL SQL审核平台 v1.6.3.7 生产环境历史数据修补回滚脚本
-- 用途：
--   从备份表 _bak_v1637_audit_history 完整还原 audit_history 与 scan_snapshots。
-- ============================================================================

-- 1. 回滚 audit_history 的 source 与 created_at
UPDATE audit_history h
JOIN _bak_v1637_audit_history b ON h.id = b.id
SET h.source = b.source,
    h.created_at = b.created_at;

-- 2. 同步回滚 scan_snapshots 的 scan_label 标签
UPDATE scan_snapshots s
JOIN _bak_v1637_audit_history b ON s.biz_ref_id = CAST(b.id AS CHAR)
SET s.scan_label = b.source;

-- 3. 核验回滚结果
SELECT h.id, h.db_name, h.source, h.created_at
FROM audit_history h
JOIN _bak_v1637_audit_history b ON h.id = b.id;
