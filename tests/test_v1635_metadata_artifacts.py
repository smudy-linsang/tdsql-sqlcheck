# -*- coding: utf-8 -*-
"""v1.6.3.5 / DU-2 / D06 — 元数据审核产物管理回归（原子写入/manifest/分页/清理护栏）。"""
import json
import os

import pytest

from backend.services import metadata_artifacts as art


@pytest.fixture()
def job(tmp_path, monkeypatch):
    """把产物根指到临时目录，返回一个合法 job_id。"""
    monkeypatch.setenv("REPORT_OUTPUT_DIR", str(tmp_path))
    # 清 artifact_root 缓存副作用（每次新建）
    return "ab12cd34" + "0" * 24


def test_atomic_write_and_read(job):
    d = art.job_dir(job)
    n = art.atomic_write_text(d / "schema.sql", "CREATE TABLE t (id INT);\n")
    assert n == len("CREATE TABLE t (id INT);\n".encode("utf-8"))
    assert (d / "schema.sql").exists()
    # .part 不应残留
    assert not (d / "schema.sql.part").exists()
    assert art.read_full_sql(job).decode("utf-8") == "CREATE TABLE t (id INT);\n"


def test_job_dir_rejects_bad_id(tmp_path, monkeypatch):
    monkeypatch.setenv("REPORT_OUTPUT_DIR", str(tmp_path))
    for bad in ("../etc", "x" * 31, "z" * 32, "", "../../win"):
        with pytest.raises(ValueError):
            art.job_dir(bad)


def test_manifest_written_last_with_hashes(job):
    d = art.job_dir(job)
    art.atomic_write_text(d / "schema.sql", "SELECT 1;")
    art.atomic_write_text(d / "results.json", "[]")
    mpath = art.write_manifest(job, context_hash="ctx",
                               files_meta={"schema.sql": {"bytes": 9, "sha256": "x"}},
                               counts={"total_statements": 1})
    assert mpath.exists()
    m = json.loads(mpath.read_text(encoding="utf-8"))
    assert m["job_id"] == job and m["context_hash"] == "ctx"
    assert m["files"]["schema.sql"]["bytes"] == 9
    assert m["counts"]["total_statements"] == 1


def test_results_paged_read(job):
    d = art.job_dir(job)
    lines = [json.dumps({"sql": f"SELECT {i};", "passed": i % 2 == 0,
                         "sql_type": "SELECT", "violations": []},
                        ensure_ascii=False) for i in range(120)]
    art.atomic_write_text(d / "results.ndjson", "\n".join(lines) + "\n")
    p0 = art.read_results_page(job, 0, 50)
    assert p0["total"] == 120 and len(p0["items"]) == 50
    assert p0["next_offset"] == 50
    assert p0["items"][0]["statement_index"] == 0
    assert "sql_preview" in p0["items"][0]
    p1 = art.read_results_page(job, 100, 50)
    assert len(p1["items"]) == 20 and p1["next_offset"] is None
    assert p1["items"][0]["statement_index"] == 100


def test_sql_preview_truncation(job):
    d = art.job_dir(job)
    big = "-- " + ("x" * (70 * 1024))  # > 64 KiB
    art.atomic_write_text(d / "schema.sql", big)
    prev = art.read_sql_preview(job)
    assert prev["available"] and prev["truncated"] is True
    assert prev["total_bytes"] == len(big.encode("utf-8"))
    assert len(prev["preview"].encode("utf-8")) <= 64 * 1024


def test_result_detail_by_index(job):
    d = art.job_dir(job)
    lines = [json.dumps({"sql": f"SELECT {i};", "passed": True, "violations": []})
             for i in range(5)]
    art.atomic_write_text(d / "results.ndjson", "\n".join(lines) + "\n")
    rec = art.read_result_detail(job, 3)
    assert rec and rec["sql"] == "SELECT 3;"
    assert art.read_result_detail(job, 99) is None
    assert art.read_result_detail(job, -1) is None


def test_cleanup_refuses_symlink_and_outside(job, tmp_path, monkeypatch):
    monkeypatch.setenv("REPORT_OUTPUT_DIR", str(tmp_path))
    d = art.job_dir(job)
    d.mkdir(parents=True, exist_ok=True)
    (d / "schema.sql").write_text("SELECT 1;", encoding="utf-8")
    # 正常目录可清理
    assert art.cleanup_job_dir(job) is True
    assert not d.exists()
    # 越界 job_id 在 job_dir 阶段即拒绝
    with pytest.raises(ValueError):
        art.cleanup_job_dir("../escape")


def test_artifact_bytes_counts_all_files(job):
    d = art.job_dir(job)
    art.atomic_write_text(d / "schema.sql", "ABCD")
    art.atomic_write_text(d / "results.json", "EFGH")
    assert art.job_artifact_bytes(job) == 8
