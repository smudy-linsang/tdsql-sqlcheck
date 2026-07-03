"""
V2.1 平台增强测试

覆盖:
- 元数据库连接池（复用/幂等close/事务清理/失效重建）
- 扫描计划到期判定（已过未跑=到期、积压补跑、今日已跑不重复、未来不触发）
"""
import threading
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest


# ══════════════════════════════════════════════════════════════
# 连接池
# ══════════════════════════════════════════════════════════════

class TestConnectionPool:
    def test_connection_reused_after_close(self):
        """close()归还池中，下次checkout复用同一底层连接"""
        from backend.services import database as db
        # 清空池，保证观测干净
        while not db._conn_pool.empty():
            try:
                db._conn_pool.get_nowait().close()
            except Exception:
                pass
        c1 = db._get_connection()
        raw1 = c1._conn
        c1.close()
        c2 = db._get_connection()
        raw2 = c2._conn
        c2.close()
        assert raw1 is raw2, "空闲连接应被复用而非新建"

    def test_close_idempotent(self):
        """重复close不会把同一连接二次入池"""
        from backend.services import database as db
        while not db._conn_pool.empty():
            try:
                db._conn_pool.get_nowait().close()
            except Exception:
                pass
        c = db._get_connection()
        c.close()
        c.close()  # 第二次应为no-op
        assert db._conn_pool.qsize() == 1, "幂等close只入池一次"
        # 清理
        db._conn_pool.get_nowait().close()

    def test_uncommitted_tx_rolled_back_on_checkin(self):
        """归还时回滚未提交事务，复用连接不携带脏数据"""
        from backend.services.database import _get_connection, ensure_db
        ensure_db()
        c1 = _get_connection()
        c1.execute(
            "INSERT INTO operation_logs(operator, operation_type) VALUES ('pool_test', 'tx_probe')")
        # 不commit直接close → 应被rollback
        c1.close()
        c2 = _get_connection()
        row = c2.execute(
            "SELECT COUNT(*) AS c FROM operation_logs "
            "WHERE operator='pool_test' AND operation_type='tx_probe'").fetchone()
        c2.close()
        assert row["c"] == 0, "未提交事务必须在归还时回滚"

    def test_pool_serves_concurrent_workers(self):
        """并发checkout互不干扰（每线程独占连接）"""
        from backend.services.database import _get_connection, ensure_db
        ensure_db()
        errors = []

        def worker():
            try:
                for _ in range(5):
                    conn = _get_connection()
                    row = conn.execute("SELECT 1 AS v").fetchone()
                    assert row["v"] == 1
                    conn.close()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert not errors, f"并发访问出错: {errors}"

    def test_stale_connection_rebuilt(self):
        """池中失效连接被ping检测并重建，不抛给调用方"""
        from backend.services import database as db
        while not db._conn_pool.empty():
            try:
                db._conn_pool.get_nowait().close()
            except Exception:
                pass
        c = db._get_connection()
        raw = c._conn
        c.close()          # 入池
        raw.close()        # 模拟服务端断开/超时
        c2 = db._get_connection()  # ping(reconnect=True)应恢复或重建
        row = c2.execute("SELECT 1 AS v").fetchone()
        assert row["v"] == 1
        c2.close()


# ══════════════════════════════════════════════════════════════
# 扫描计划到期判定（V2.1: 已过未跑=到期，支持积压补跑）
# ══════════════════════════════════════════════════════════════

class TestScanScheduleDue:
    @pytest.fixture()
    def sched_env(self):
        from backend.services.database import _get_connection, ensure_db
        ensure_db()
        conn = _get_connection()
        conn.execute("DELETE FROM scan_schedules")
        # 抢占leader租约，保证判定函数可执行
        conn.execute("DELETE FROM scheduler_lease")
        conn.commit()
        conn.close()
        yield
        conn = _get_connection()
        conn.execute("DELETE FROM scan_schedules")
        conn.execute("DELETE FROM scheduler_lease")
        conn.commit()
        conn.close()

    def _insert_schedule(self, hour, minute, last_run_at=None, enabled=1):
        from backend.services.database import _get_connection
        conn = _get_connection()
        cursor = conn.execute("""
            INSERT INTO scan_schedules(connection_id, source, cron_hour, cron_minute,
                                       limit_rows, min_time, enabled, last_run_at)
            VALUES ('sched_test_conn', 'digest', ?, ?, 10, 0.1, ?, ?)
        """, (hour, minute, enabled, last_run_at))
        conn.commit()
        sid = cursor.lastrowid
        conn.close()
        return sid

    def _run_and_collect(self):
        """执行到期检查，收集被触发的计划ID（mock实际扫描）"""
        executed = []
        from backend.services import scheduler
        with patch.object(scheduler, "_execute_scan_schedule",
                          side_effect=lambda s: executed.append(s["id"])):
            scheduler._run_due_scan_schedules()
        return executed

    def test_past_time_not_run_today_is_due(self, sched_env):
        """应跑时刻已过且今日未跑 → 到期（积压补跑核心场景）"""
        now = datetime.now()
        past = now - timedelta(hours=1)
        if past.day != now.day:
            pytest.skip("跨日边界时段，跳过该时间敏感用例")
        yesterday = (now - timedelta(days=1)).isoformat()
        sid = self._insert_schedule(past.hour, past.minute, last_run_at=yesterday)
        assert sid in self._run_and_collect(), "已过时刻且昨日最后执行的计划应补跑"

    def test_never_run_past_time_is_due(self, sched_env):
        """从未执行过且时刻已过 → 到期"""
        now = datetime.now()
        past = now - timedelta(minutes=5)
        if past.day != now.day:
            pytest.skip("跨日边界时段，跳过该时间敏感用例")
        sid = self._insert_schedule(past.hour, past.minute, last_run_at=None)
        assert sid in self._run_and_collect()

    def test_already_run_today_not_due(self, sched_env):
        """今日已执行 → 不重复触发"""
        now = datetime.now()
        past = now - timedelta(hours=1)
        if past.day != now.day:
            pytest.skip("跨日边界时段，跳过该时间敏感用例")
        sid = self._insert_schedule(past.hour, past.minute,
                                    last_run_at=now.isoformat())
        assert sid not in self._run_and_collect()

    def test_future_time_not_due(self, sched_env):
        """应跑时刻未到 → 不触发"""
        now = datetime.now()
        future = now + timedelta(hours=1)
        if future.day != now.day:
            pytest.skip("跨日边界时段，跳过该时间敏感用例")
        sid = self._insert_schedule(future.hour, future.minute)
        assert sid not in self._run_and_collect()

    def test_disabled_schedule_not_due(self, sched_env):
        """停用计划不触发"""
        now = datetime.now()
        past = now - timedelta(hours=1)
        if past.day != now.day:
            pytest.skip("跨日边界时段，跳过该时间敏感用例")
        sid = self._insert_schedule(past.hour, past.minute, enabled=0)
        assert sid not in self._run_and_collect()

    def test_backlog_processed_in_order(self, sched_env):
        """多个积压计划按时刻顺序一次性补跑（同刻风暴修复验证）"""
        now = datetime.now()
        if now.hour < 3:
            pytest.skip("凌晨时段积压场景不成立，跳过")
        sids = [self._insert_schedule(h, 0) for h in (1, 2)]
        executed = self._run_and_collect()
        assert executed[:2] == sids, f"积压计划应按时刻顺序补跑: {executed}"