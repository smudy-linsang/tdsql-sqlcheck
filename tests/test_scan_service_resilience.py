"""回归：单条异常 SQL 不能中断整批扫描或遗留 running 任务。"""
from types import SimpleNamespace

from backend.services import scan_service
from backend.services.sql_masking import mask_sql_literals


class _DigestPool:
    config = SimpleNamespace(database="test_db", host="127.0.0.1", port=15005)

    def get_slow_queries_from_digest(self, **_kwargs):
        return [
            {"DIGEST_TEXT": "SELECT * FROM t WHERE name='unterminated", "COUNT_STAR": 1},
            {"DIGEST_TEXT": "SELECT * FROM t WHERE id=42", "COUNT_STAR": 1},
        ]


class _SlowQueryServiceSpy:
    created_task_id = 701
    persisted = []
    completed = []

    def create_scan_task(self, **_kwargs):
        return self.created_task_id

    def add_slow_query(self, record, **_kwargs):
        # 模拟真实落库前的统一脱敏校验；未闭合字符串会抛 SQLMaskingError。
        mask_sql_literals(record.sql_text)
        self.persisted.append(record.sql_text)
        return {"id": len(self.persisted)}

    def complete_scan_task(self, task_id, total_fetched, total_analyzed, status):
        self.completed.append((task_id, total_fetched, total_analyzed, status))


def test_bad_sql_does_not_abort_batch_or_leave_scan_running(monkeypatch):
    _SlowQueryServiceSpy.persisted = []
    _SlowQueryServiceSpy.completed = []
    monkeypatch.setattr("backend.services.slow_query_service.SlowQueryService", _SlowQueryServiceSpy)
    monkeypatch.setattr("backend.services.metrics_service.inc", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("backend.services.snapshot_extractors.slow_scan.extract",
                        lambda *_args, **_kwargs: ([], 0))
    monkeypatch.setattr("backend.services.scan_snapshot_service.safe_create_snapshot",
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr("backend.services.ruleset_service.ruleset_service.get_active_rule_set_id",
                        lambda: "test-rules")

    result = scan_service.run_scan(
        source="digest", limit=10, min_time=0.1, pool=_DigestPool(), enrich=False)

    assert result["fetched"] == 1
    assert _SlowQueryServiceSpy.persisted == ["SELECT * FROM t WHERE id=42"]
    assert result["errors"][0]["stage"] == "persist"
    # 原始两条均已抓到，一条成功入库；任何部分失败都必须离开 running 状态。
    assert _SlowQueryServiceSpy.completed == [(701, 2, 1, "failed")]
