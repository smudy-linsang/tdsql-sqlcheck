-- v1.6.2.2-UAT-O-18：索引体检 finding 增加结构化字段，
-- 报告层不再解析面向人的 detail 文案（重复索引对方名称曾因此显示 N/A）。
ALTER TABLE index_audit_finding ADD COLUMN related_index_name VARCHAR(128) DEFAULT '';
ALTER TABLE index_audit_finding ADD COLUMN index_columns VARCHAR(512) DEFAULT '';
