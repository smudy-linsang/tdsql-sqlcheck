-- v1.6.3.4 / D01：报告来源上下文（REQ-01）
-- 槽位：v14/140。设计出处：DETAIL-v1.6.3.4 §3.3。
--
-- 七张表各增加一列 report_context_json，逐表独立一条 DDL。
-- 单任务上下文 UTF-8 JSON 限 8 KiB；持久化前校验结构，不允许客户端塞任意对象。
-- 多实例合并使用输入上下文集合，不塞入单实例的 8 KiB 槽位。
--
-- 冻结结构（§3.2）：
--   {
--     "version": 1,
--     "captured_at": "2026-09-06T10:00:00+08:00",
--     "origin": "bound",
--     "connections": [
--       {
--         "connection_id": "conn-001",
--         "connection_name": "ECIF-分布式-测试环境",
--         "name_source": "snapshot",
--         "db_name": "lzbi_ecif"
--       }
--     ]
--   }
--
-- 无破坏性数据迁移：所有旧 report_context_json 为 null（§7.2 第 3 条）。
-- 历史无上下文时按 §3.2 降级规则显示，不伪造扫描时名称。

ALTER TABLE audit_history ADD COLUMN report_context_json TEXT NULL DEFAULT NULL;
ALTER TABLE scan_tasks ADD COLUMN report_context_json TEXT NULL DEFAULT NULL;
ALTER TABLE scan_snapshots ADD COLUMN report_context_json TEXT NULL DEFAULT NULL;
ALTER TABLE inspection_tasks ADD COLUMN report_context_json TEXT NULL DEFAULT NULL;
ALTER TABLE daily_inspection ADD COLUMN report_context_json TEXT NULL DEFAULT NULL;
ALTER TABLE server_daily_inspection ADD COLUMN report_context_json TEXT NULL DEFAULT NULL;
ALTER TABLE gateway_log_reports ADD COLUMN report_context_json TEXT NULL DEFAULT NULL;
