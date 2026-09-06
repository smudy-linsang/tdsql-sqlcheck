-- v1.6.3.4 / D06：网关大日志分析元数据（REQ-04）
-- 槽位：v14/142。设计出处：DETAIL-v1.6.3.4 §6.6。
--
-- 保留 gateway_log_reports.report_html 的 LONGTEXT 类型（不执行"改成 MEDIUMTEXT"
-- 或无依据扩容）。新增两列：
--
--   analysis_meta_json MEDIUMTEXT NULL：保存解析质量、分析/图形截断、文件 hash/
--     字节数、阶段耗时、分析器版本，限 128 KiB；不存原始日志、不存未经脱敏的
--     异常样例。
--   request_id VARCHAR(64) NULL：用于响应不确定时人工查历史和日志，不能作为
--     身份凭证。
--
-- report_context_json 由 140 迁移添加，本迁移不重复。
-- 无破坏性数据迁移：旧记录两列均为 null（§7.2 第 3 条）。

ALTER TABLE gateway_log_reports ADD COLUMN analysis_meta_json MEDIUMTEXT NULL DEFAULT NULL;
ALTER TABLE gateway_log_reports ADD COLUMN request_id VARCHAR(64) NULL DEFAULT NULL;
