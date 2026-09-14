# -*- coding: utf-8 -*-
"""CP-W04 knowledge.py / CP-W05 output.py 测试（CP-TST-52/67）。"""
import json

import pytest

from backend.services.copilot import output as out_mod
from backend.services.copilot.errors import CopilotError
from backend.services.copilot.knowledge import KnowledgeStore


class TestKnowledge:
    def test_shipped_bundle_ready(self):
        """B-01 回归锁：随包知识包必须 READY（manifest hash/大小自洽），不得 INVALID。"""
        store = KnowledgeStore()
        st = store.load()  # 默认加载随包目录
        assert st == "READY", f"随包知识包未就绪: {store.status_info()}"

    def test_builder_self_verify_catches_tamper(self, tmp_path):
        """B-01：构建器写完后篡改文件，_self_verify 必须检出。"""
        from backend.copilot_knowledge.builder import _self_verify
        import json
        from pathlib import Path
        # 构造最小合法包
        d = tmp_path / "kb-t"
        d.mkdir()
        (d / "chunks.jsonl").write_text('{"chunk_id":"a","content":"x"}\n',
                                        encoding="utf-8")
        (d / "index.json").write_text('{"bundle_id":"kb-t"}', encoding="utf-8")
        import hashlib
        manifest = {
            "file_sizes": {"chunks.jsonl": (d / "chunks.jsonl").stat().st_size,
                           "index.json": (d / "index.json").stat().st_size},
            "sha256": {
                "chunks.jsonl": hashlib.sha256(
                    (d / "chunks.jsonl").read_bytes()).hexdigest(),
                "index.json": hashlib.sha256(
                    (d / "index.json").read_bytes()).hexdigest()},
        }
        assert _self_verify(d, manifest) == []
        # 篡改
        (d / "chunks.jsonl").write_text('{"chunk_id":"a","content":"YY"}\n',
                                        encoding="utf-8")
        assert _self_verify(d, manifest) != []

    def test_store_ready_and_search(self):
        store = KnowledgeStore()
        st = store.load()
        assert st == "READY"
        res = store.bundle.search("EXECUTOR_UNAVAILABLE", product_family="TDSQL-MySQL")
        assert res and any("FAULT" in r["source_id"] for r in res)

    def test_rule_id_exact_boost(self):
        store = KnowledgeStore()
        store.load()
        res = store.bundle.search("R121", product_family="TDSQL-MySQL")
        assert res

    def test_missing_bundle(self, tmp_path):
        store = KnowledgeStore()
        st = store.load(str(tmp_path / "nope"))
        assert st == "MISSING"

    def test_invalid_bundle_bad_hash(self, tmp_path):
        # 构造 hash 被篡改的包
        import hashlib
        import shutil
        from pathlib import Path
        src = Path("backend/copilot_knowledge")
        bundle_dir = next(d for d in src.iterdir()
                          if d.is_dir() and (d / "manifest.json").exists())
        dst = tmp_path / "kb-bad"
        shutil.copytree(bundle_dir, dst)
        # 篡改 chunks 内容但不改 manifest
        with open(dst / "chunks.jsonl", "a", encoding="utf-8") as f:
            f.write('{"chunk_id":"x","content":"tampered"}\n')
        store = KnowledgeStore()
        st = store.load(str(dst))
        assert st == "INVALID"

    def test_stale_bundle_expired(self, tmp_path):
        import shutil
        from pathlib import Path
        src = Path("backend/copilot_knowledge")
        bundle_dir = next(d for d in src.iterdir()
                          if d.is_dir() and (d / "manifest.json").exists())
        dst = tmp_path / "kb-stale"
        shutil.copytree(bundle_dir, dst)
        # 修改 expires_at 到过去；manifest.json 不参与 hash 核验，仅 file_sizes
        mpath = dst / "manifest.json"
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
        manifest["expires_at"] = "2020-01-01T00:00:00Z"
        mpath.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        store = KnowledgeStore()
        st = store.load(str(dst))
        assert st == "STALE"
        assert store.status_info()["reason_code"] == "KNOWLEDGE_BUNDLE_STALE"


class TestOutputValidation:
    def _raw(self, **kw):
        base = {"schema_version": 1, "summary": "s", "outcome_claims": [],
                "findings": [], "steps": [], "missing_evidence": [],
                "sql_candidates": [], "limitations": []}
        base.update(kw)
        return base

    def test_valid_answer(self):
        a = out_mod.validate_model_answer(
            self._raw(findings=[{"kind": "FACT", "text": "t",
                                 "evidence_ids": ["E1"], "knowledge_ids": []}]),
            {"E1"}, set(), set())
        assert a.summary == "s"

    def test_extra_field_rejected(self):
        raw = self._raw(); raw["evil_field"] = "x"
        with pytest.raises(CopilotError):
            out_mod.validate_model_answer(raw, set(), set(), set())

    def test_phantom_reference_rejected(self):
        with pytest.raises(CopilotError):
            out_mod.validate_model_answer(
                self._raw(findings=[{"kind": "FACT", "text": "t",
                                     "evidence_ids": ["E99"],
                                     "knowledge_ids": []}]),
                {"E1"}, set(), set())

    def test_execution_claim_rejected(self):
        with pytest.raises(CopilotError) as ei:
            out_mod.validate_model_answer(
                self._raw(summary="我已经执行了修复 SQL"),
                set(), set(), set())
        assert ei.value.code == "OUTPUT_INVALID"

    def test_unfounded_percentage_rejected(self):
        with pytest.raises(CopilotError):
            out_mod.validate_model_answer(
                self._raw(summary="优化后性能提升 90%"),
                set(), set(), set())

    def test_negation_and_history_not_killed(self):
        """合法否定与历史引用不得被误杀（N-04 反例）。"""
        a = out_mod.validate_model_answer(
            self._raw(summary="不能据此认定已通过审核；历史记录 E1 显示当次审核通过"),
            {"E1"}, set(), set())
        assert a.summary

    def test_outcome_claim_requires_evidence(self):
        with pytest.raises(CopilotError):
            out_mod.validate_model_answer(
                self._raw(outcome_claims=[{
                    "claim_type": "AUDIT_STATUS", "evidence_id": "E9",
                    "fact_key": "state", "subject_ref": "", "observed_at": None}]),
                {"E1"}, set(), set())


class TestAssertionDetection:
    def test_execution_variants(self):
        assert out_mod.detect_unfounded_assertions("助手已执行该 SQL")
        assert out_mod.detect_unfounded_assertions("AI 已完成修复")

    def test_safe_text(self):
        assert not out_mod.detect_unfounded_assertions(
            "不能据此认定已通过审核")
        assert not out_mod.detect_unfounded_assertions(
            "历史记录 E1 显示当次审核已通过；本轮没有重新审核")
