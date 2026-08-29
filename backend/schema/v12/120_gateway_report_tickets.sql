-- v1.6.2.2-UAT-O-22：网关报告一次性票据进入所有 worker 共享的元数据库。
-- 进程内字典在多 worker（生产 --workers 2）下无法共享，消费请求落到另一进程即随机 401。
-- 只存票据哈希（SHA-256），不明文持久化；消费端以单条原子 UPDATE 判据一次性。
CREATE TABLE IF NOT EXISTS gateway_report_tickets (
    ticket_hash  CHAR(64) PRIMARY KEY COMMENT '票据 SHA-256（不存明文）',
    report_id    INT NOT NULL COMMENT '绑定的报告 ID',
    username     VARCHAR(64) NOT NULL DEFAULT '' COMMENT '签发者',
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    expires_at   DATETIME NOT NULL COMMENT '过期时刻（90s 短时效）',
    consumed_at  DATETIME NULL DEFAULT NULL COMMENT '消费时刻（一次性语义）',
    INDEX idx_grt_expires (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
