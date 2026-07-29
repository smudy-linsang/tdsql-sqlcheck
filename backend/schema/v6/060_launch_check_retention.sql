-- ============================================================================
-- V1.5.2 上线检查结果历史保留与对比
-- 无表结构变更；仅补入保留策略配置
-- 设计依据：docs/DESIGN-v1.5.2-上线检查历史保留与对比.md §5
-- ============================================================================

-- ── F-1 上线检查明细纳入保留策略 ──
-- 此前 inspection_tasks / inspection_results 不在 CLEANABLE_TABLES 中，永不清理。
-- 每执行一次上线检查最多写入 12×100=1200 行明细，属当下正在发生的增长问题。
--
-- 只登记 inspection_tasks：inspection_results 有 ON DELETE CASCADE 外键
-- （database.py 建表语句），随任务级联清理。若把 results 也单独登记、按其自身
-- created_at 清理，会留下"任务还在、明细被删一半"的残缺记录，比不清理更糟。
--
-- 180 天与 scan_tasks 对齐。快照（scan_snapshots）保留 365 天且自包含，
-- 故明细清理后历史对比照常可用。
INSERT IGNORE INTO retention_policies(table_name, retention_days, enabled)
VALUES ('inspection_tasks', 180, 1);

-- ── 存量数据：不做任何处理 ──
-- 存量 inspection_tasks 将在首次清理时按 180 天规则自然淘汰，符合预期。
-- 不补建历史快照（理由见设计文档 §4.4：明细已截断至 100 行，回填出的
-- 快照与实时快照不可比，会在对比中制造虚假的"已解决"）。
