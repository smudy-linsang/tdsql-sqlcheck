-- ============================================================================
-- V1.6.0.1 修复 P2-01（A 质检验收）：tdsql_connections 唯一约束
-- 设计依据：docs/v1.6.0.1_质检验收报告_A.md §5.2
--
-- 背景：连接表此前仅有 PRIMARY(id) 与非唯一索引 idx_conn_default。
-- ZK 标准化导入的事务内"先查后插"在 REPEATABLE READ 下是非锁定一致性读，
-- 两位操作者并发提交同一候选会双双通过检查、双双插入，产生重复连接；
-- 手工新建连接同样可插入同名/同端点记录。唯一约束是根治手段，
-- 同时保护导入与手工两条写入路径。
--
-- 步骤：先清理存量重复（保留 created_at 最早一条，相同时保留 id 较小者），
-- 再建唯一约束。清理只删完全同身份（同名或同 host:port:database）的冗余行。
-- ============================================================================

-- ── 1. 存量重复清理：同名连接（保留最早一条）──
DELETE c1 FROM tdsql_connections c1
JOIN tdsql_connections c2
  ON c1.name = c2.name
 AND (c1.created_at > c2.created_at
      OR (c1.created_at = c2.created_at AND c1.id > c2.id));

-- ── 2. 存量重复清理：同 host:port:database 连接（保留最早一条）──
DELETE c1 FROM tdsql_connections c1
JOIN tdsql_connections c2
  ON c1.host = c2.host AND c1.port = c2.port AND c1.`database` = c2.`database`
 AND (c1.created_at > c2.created_at
      OR (c1.created_at = c2.created_at AND c1.id > c2.id));

-- ── 3. 建唯一约束 ──
ALTER TABLE tdsql_connections ADD UNIQUE KEY uq_conn_name (name);
ALTER TABLE tdsql_connections ADD UNIQUE KEY uq_conn_endpoint (host, port, `database`);
