# -*- coding: utf-8 -*-
"""v1.6.3.7 命名规范与时区口径回归防护锁（锁定 extracted_{db}_{YYYYMMDD_HHMMSS}.sql）。

验证：
1. L1：Worker 执行期产出的 filename 必须严格符合正则，且绝不包含 job_id 任何片段；
2. L2：文件名时间戳准确对应本地系统时间（误差 <= 2秒）；
3. L3：下载接口与历史列表读取端到端契约无损透传；
4. L4：静态代码防倒退扫描锁，确保 backend 目录下不再存在 job_id[:8] 反模式；
5. L5：Worker 落库的 created_at 必须与文件名处于相同时基（MC 变异防护）；
6. L6：历史元数据审核按当天日期能正常筛选（本地时间入库）。
"""

import re
import json
import tempfile
from pathlib import Path
from datetime import datetime, timezone
import pytest


# ══ 锁 1：Worker 执行期产出的 filename 必须严格符合正则，且绝不包含 job_id ══
def test_l1_worker_filename_format_and_no_job_id(monkeypatch):
    """L1：执行 metadata_audit_worker.run 时，生成的 filename 必须匹配
    ^extracted_<db>_\\d{8}_\\d{6}\\.sql$，且不能包含任何 job_id 的片段。"""
    from backend.workers import metadata_audit_worker as W
    from backend.services import metadata_audit_repository as RR
    from backend.services import metadata_artifacts as art
    import backend.services.connection_registry as CRG
    import backend.services.metadata_audit_pipeline as P

    captured = {}

    def _fake_audit_streaming(sql, file_path="", rule_overrides=None, instance_type=None):
        captured["file_path"] = file_path
        return []

    def _fake_extract(pool, db, scopes, instance_label="", instance_type=""):
        return (["-- header"], {"extracted_objects": 1, "skipped_objects": 0,
                                "skipped_benign": 0, "skipped_abnormal": 0})

    def _fake_publish(job_id, attempt_token, audit_columns_values=None, results_json=""):
        captured["audit_source"] = audit_columns_values[1]  # 第2项是 source
        return 9999

    with tempfile.TemporaryDirectory() as td:
        t_path = Path(td)
        job_uuid = "a27123e1b04f4a3e89c0d1e2f3a4b5c6"
        test_db = "lzbj_ecif"
        job = {
            "id": job_uuid, "attempt_token": "tok_123", "state": RR.STATE_RUNNING,
            "execution_context_json": json.dumps({"connection_name": "ECIF"}),
            "request_json": json.dumps({"scopes": ["TABLE"]}),
            "connection_id": "conn_test", "db_name": test_db, "created_by": "tester"
        }

        monkeypatch.setattr(RR.repository, "get_job", lambda jid: dict(job))
        monkeypatch.setattr(RR.repository, "cas_state", lambda *a, **k: None)
        monkeypatch.setattr(RR.repository, "update_progress", lambda *a, **k: None)
        monkeypatch.setattr(RR.repository, "publish", _fake_publish)
        monkeypatch.setattr(CRG.registry, "get", lambda cid: object())
        monkeypatch.setattr(P, "extract_metadata", _fake_extract)
        monkeypatch.setattr(P, "audit_streaming", _fake_audit_streaming)
        monkeypatch.setattr(art, "job_dir", lambda jid: t_path)

        # 执行 Worker
        ret = W.run(job_uuid, "tok_123")
        assert ret == 0

        # 1. 验证 file_path 与 audit_source 完全一致
        fn = captured.get("file_path")
        assert fn == captured.get("audit_source"), "审计流与落库 source 文件名不一致"

        # 2. 验证正则匹配标准格式：extracted_lzbj_ecif_YYYYMMDD_HHMMSS.sql
        pattern = rf"^extracted_{test_db}_\d{{8}}_\d{{6}}\.sql$"
        assert re.match(pattern, fn), f"文件名 {fn} 不符合 {pattern} 规范"

        # 3. 严格断言：绝不能包含 job_id 前缀
        assert "a27123e1" not in fn, f"文件名泄露了 job_id 字符: {fn}"


# ══ 锁 2：文件名时间戳与本地系统时间一致性 ══
def test_l2_filename_timestamp_matches_local_now(monkeypatch):
    """L2：文件名中的时间戳必须准确代表当前本地系统时间（容差 <= 2秒）。"""
    from backend.workers import metadata_audit_worker as W
    from backend.services import metadata_audit_repository as RR
    from backend.services import metadata_artifacts as art
    import backend.services.connection_registry as CRG
    import backend.services.metadata_audit_pipeline as P

    captured = {}
    with tempfile.TemporaryDirectory() as td:
        t_path = Path(td)
        monkeypatch.setattr(RR.repository, "get_job", lambda jid: {
            "id": "j_time_check", "attempt_token": "tok", "state": RR.STATE_RUNNING,
            "execution_context_json": "{}", "request_json": '{"scopes":["TABLE"]}',
            "connection_id": "c", "db_name": "order_db", "created_by": "u"
        })
        monkeypatch.setattr(RR.repository, "cas_state", lambda *a, **k: None)
        monkeypatch.setattr(RR.repository, "update_progress", lambda *a, **k: None)
        monkeypatch.setattr(RR.repository, "publish", lambda *a, **k: 1)
        monkeypatch.setattr(CRG.registry, "get", lambda cid: object())
        monkeypatch.setattr(P, "extract_metadata", lambda *a, **k: ([], {}))
        monkeypatch.setattr(P, "audit_streaming", lambda *a, file_path="", **k: captured.setdefault("fn", file_path) and [])
        monkeypatch.setattr(art, "job_dir", lambda jid: t_path)

        t_before = datetime.now()
        W.run("j_time_check", "tok")
        t_after = datetime.now()

        fn = captured["fn"]
        # 提取时间戳部分
        m = re.search(r"extracted_order_db_(\d{8}_\d{6})\.sql", fn)
        assert m, f"未提取到时间戳: {fn}"
        ts_dt = datetime.strptime(m.group(1), "%Y%m%d_%H%M%S")

        # 验证在执行前后的合理时间窗口内
        assert (ts_dt - t_before).total_seconds() >= -2
        assert (t_after - ts_dt).total_seconds() >= -2


# ══ 锁 3：下载接口与历史列表读取端到端契约 ══
def test_l3_download_endpoint_uses_source_intact():
    """L3：模拟数据库中标准文件名，验证 SQL 下载接口 Content-Disposition 无篡改。"""
    import asyncio
    from urllib.parse import unquote
    from backend.services.database import ensure_db, _get_connection
    from backend.api.sql_audit import download_extracted_report_sql

    ensure_db()
    conn = _get_connection()
    expected_fn = "extracted_payment_db_20260912_153000.sql"
    cur = conn.execute("""
        INSERT INTO audit_history (audit_type, source, total_sql, passed, failed,
            error_count, warning_count, pass_rate, results_json, created_by,
            project_id, gate_passed, gate_detail, created_at, connection_id, db_name)
        VALUES ('extracted_schema', ?, 1, 1, 0, 0, 0, 100.0, '[]', 'u', '', NULL, '',
                '2026-09-12 15:30:00', 'c', 'payment_db')
    """, (expected_fn,))
    rid = cur.lastrowid
    conn.commit()
    conn.close()

    try:
        resp = asyncio.run(download_extracted_report_sql(rid))
        cd = resp.headers.get("Content-Disposition", "")
        assert expected_fn in unquote(cd), f"下载文件名不一致: {cd}"
    finally:
        conn = _get_connection()
        conn.execute("DELETE FROM audit_history WHERE id = ?", (rid,))
        conn.commit()
        conn.close()


# ══ 锁 4：静态代码防倒退扫描锁 ══
def test_l4_static_code_no_jobid_in_filename():
    """L4：静态扫描 backend/ 目录，禁止出现 `job_id[:8]` 用于构建 sql 文件名的反模式。"""
    base = Path(__file__).resolve().parent.parent / "backend"
    for py_file in base.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        assert "job_id[:8]" not in text, f"文件 {py_file.name} 中发现了禁用的 job_id[:8] 代码！"


# ══ 锁 5：Worker 真实落库的 created_at 必须与文件名处于相同时基（F1 修复，防 MC 变异）══
def test_l5_publish_created_at_is_local_time(monkeypatch):
    """L5：worker 落库的 created_at 必须是本地时间（与文件名时间戳同一时基）。

    MC 变异（改回 _utcnow()）必须使本用例变红。
    """
    from backend.workers import metadata_audit_worker as W
    from backend.services import metadata_audit_repository as RR
    from backend.services import metadata_artifacts as art
    import backend.services.connection_registry as CRG
    import backend.services.metadata_audit_pipeline as P

    captured = {}
    with tempfile.TemporaryDirectory() as td:
        t_path = Path(td)
        monkeypatch.setattr(RR.repository, "get_job", lambda jid: {
            "id": "j_tz", "attempt_token": "t_tok", "state": RR.STATE_RUNNING,
            "execution_context_json": "{}", "request_json": '{"scopes":["TABLE"]}',
            "connection_id": "c", "db_name": "order_db", "created_by": "u"
        })
        monkeypatch.setattr(RR.repository, "cas_state", lambda *a, **k: None)
        monkeypatch.setattr(RR.repository, "update_progress", lambda *a, **k: None)
        monkeypatch.setattr(RR.repository, "publish",
                            lambda jid, tok, audit_columns_values=None, results_json="":
                            captured.setdefault("cols", audit_columns_values) or 1)
        monkeypatch.setattr(CRG.registry, "get", lambda cid: object())
        monkeypatch.setattr(P, "extract_metadata", lambda *a, **k: (["-- h"], {}))
        monkeypatch.setattr(P, "audit_streaming", lambda *a, **k: iter(()))
        monkeypatch.setattr(art, "job_dir", lambda jid: t_path)

        W.run("j_tz", "t_tok")
        cols = captured["cols"]                     # audit_cols：索引 13 = created_at
        src, created = cols[1], cols[13]            # 索引 1 = source
        m = re.search(r"(\d{8}_\d{6})\.sql$", src)
        assert m, f"未匹配到时间戳: {src}"
        ts = datetime.strptime(m.group(1), "%Y%m%d_%H%M%S")
        dt = datetime.strptime(created, "%Y-%m-%d %H:%M:%S")

        # 1. 本地时间口径：与文件名时间戳相差 ≤2s（若是 UTC 则在非零时区下相差数小时）
        assert abs((dt - ts).total_seconds()) <= 2, f"created_at({created}) 与文件名({src}) 不同时基"

        # 2. 若当前机器处于非零时区（如中国标准时间 UTC+8），则断言 created_at 绝对不是 UTC
        local_offset = datetime.now().astimezone().utcoffset()
        if local_offset and abs(local_offset.total_seconds()) >= 3600:
            utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
            assert abs((utc_now - dt).total_seconds()) > 60, "created_at 疑似 UTC（应为本地时间）"


# ══ 锁 6：历史元数据审核入库本地时间与按当天日期精准筛选（F5-2 归拢）══
def test_l6_history_created_at_is_local_and_filterable():
    """L6：验证元数据审核入库使用本地时间，且历史元数据审核按当天日期能正常筛选。"""
    from backend.workers.metadata_audit_worker import _local_now_str
    from backend.services.database import _get_connection

    # 1. 验证 _local_now_str 与 datetime.now() 本地时间口径一致
    now_str = _local_now_str()
    local_now = datetime.now()
    parsed = datetime.strptime(now_str, "%Y-%m-%d %H:%M:%S")
    assert abs((local_now - parsed).total_seconds()) < 5

    # 2. 验证写入 audit_history 并用当天日期筛选
    conn = _get_connection()
    today_str = local_now.strftime("%Y-%m-%d")
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO audit_history (audit_type, source, total_sql, passed, failed,
            error_count, warning_count, pass_rate, results_json, created_by,
            project_id, gate_passed, gate_detail, created_at, connection_id, db_name)
        VALUES ('extracted_schema', 'test_tz_check.sql', 1, 1, 0, 0, 0, 100.0,
                '[]', 'test_runner', '', NULL, '', ?, 'tz_conn', 'tz_db')
    """, (now_str,))
    new_id = getattr(cur, "lastrowid", 0)
    conn.commit()

    try:
        cur.execute("""
            SELECT id, created_at, source 
            FROM audit_history 
            WHERE audit_type = 'extracted_schema' 
              AND DATE(created_at) >= ? 
              AND DATE(created_at) <= ?
              AND id = ?
        """, (today_str, today_str, new_id))
        row = cur.fetchone()
        assert row is not None, f"历史记录按当天 {today_str} 筛选未命中入库记录 #{new_id}"
        assert row["id"] == new_id
    finally:
        conn.execute("DELETE FROM audit_history WHERE id = ?", (new_id,))
        conn.commit()
        conn.close()
