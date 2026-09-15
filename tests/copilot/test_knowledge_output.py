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

    @pytest.mark.parametrize("damaged_file", ["chunks.jsonl", "index.json"])
    def test_build_calls_self_verify_end_to_end(self, tmp_path, monkeypatch,
                                               damaged_file):
        """X1：manifest 落盘后注入损坏，真实 build() 必须拒绝交付。"""
        from pathlib import Path
        from backend.copilot_knowledge.builder import build

        src = tmp_path / "src"
        src.mkdir()
        (src / "s1.md").write_text(
            "---\nsource_id: S1\ntitle: t\nauthority: USER_GUIDE\n---\n\n# 标题\n\n正文内容\n",
            encoding="utf-8")
        out_root = tmp_path / "kbout"
        out = build(src, "tester", "2027-01-01T00:00:00Z", out_root=out_root)
        write_text = Path.write_text
        injected = []

        def damage_after_manifest(path, data, *args, **kwargs):
            result = write_text(path, data, *args, **kwargs)
            if path == out / "manifest.json":
                victim = out / damaged_file
                victim.write_bytes(victim.read_bytes() + b"tampered\n")
                injected.append(damaged_file)
            return result

        # 不替换 _self_verify；损坏发生在摘要登记之后，避免被 build 重写掩盖。
        with monkeypatch.context() as patch:
            patch.setattr(Path, "write_text", damage_after_manifest)
            with pytest.raises(RuntimeError, match=damaged_file):
                build(src, "tester", "2027-01-01T00:00:00Z", out_root=out_root)
        assert injected == [damaged_file]
        assert build(src, "tester", "2027-01-01T00:00:00Z", out_root=out_root) == out

    def test_build_outputs_lf_bytes(self, tmp_path):
        """B-01：Windows 构建也必须写 LF，不能仅在存仓后才转换。"""
        import hashlib
        from pathlib import Path
        from backend.copilot_knowledge.builder import build

        sources = Path(__file__).resolve().parents[2] / "backend/copilot_knowledge/sources"
        out = build(sources, "tester", "2027-01-01T00:00:00Z", out_root=tmp_path)
        manifest = json.loads((out / "manifest.json").read_bytes())
        for name in ("chunks.jsonl", "index.json", "manifest.json"):
            data = (out / name).read_bytes()
            assert b"\r" not in data, f"{name} 必须使用 LF"
            assert b"\n" in data
            if name != "manifest.json":
                assert len(data) == manifest["file_sizes"][name]
                assert hashlib.sha256(data).hexdigest() == manifest["sha256"][name]

    def test_sources_rebuild_matches_shipped(self, tmp_path):
        """N-03：随包产物必须是当前 sources/ 的构建结果，不得静默漂移。

        从 sources/ 确定性重建到临时目录，比对 bundle_id 与产物 SHA256。
        approved_by / reviewed_at 是构建入参，排除比对。
        比对对象取运行期加载器解析出的包，与 KnowledgeStore 实际使用的一致。
        """
        import hashlib
        from pathlib import Path
        from backend.copilot_knowledge.builder import build
        from backend.services.copilot.knowledge import KnowledgeStore

        # 用运行期加载器解析出实际加载的包（不写死目录名）
        store = KnowledgeStore()
        assert store.load() == "READY", f"随包知识包未就绪: {store.status_info()}"
        shipped = store._bundle.dir
        shipped_manifest = json.loads(
            (shipped / "manifest.json").read_bytes())
        shipped_bundle_id = shipped_manifest["bundle_id"]

        # 从 sources 重建到隔离临时目录
        sources = Path(__file__).resolve().parents[2] / "backend/copilot_knowledge/sources"
        rebuilt = build(sources, "contract-check", "2099-01-01T00:00:00Z",
                        out_root=tmp_path)
        rebuilt_manifest = json.loads((rebuilt / "manifest.json").read_bytes())

        # 确定性比对：bundle_id 由内容 hash 决定，源漂移 → hash 变 → id 变
        assert rebuilt_manifest["bundle_id"] == shipped_bundle_id, (
            f"源文档漂移：重建 bundle_id={rebuilt_manifest['bundle_id']} "
            f"!= 随包 {shipped_bundle_id}；改了 sources/ 必须重建知识包，"
            f"由审批人重新签署 approved_by / reviewed_at，且删除旧包目录")

        for name in ("chunks.jsonl", "index.json"):
            a = hashlib.sha256((rebuilt / name).read_bytes()).hexdigest()
            b = hashlib.sha256((shipped / name).read_bytes()).hexdigest()
            assert a == b, (
                f"{name} 与按源文档重建的结果不一致（{a[:16]} vs {b[:16]}）")

    def test_builder_has_no_translating_write(self):
        """N-04：builder 落盘不得走可能翻译换行的文本模式（平台无关断言）。

        在 POSIX 上 newline="\\n" 与默认行为等价，行为断言不会红；
        本条用 AST 检查源码，任何平台上撤掉 newline 参数都会被检出。
        """
        import ast
        from pathlib import Path

        builder = Path(__file__).resolve().parents[2] / "backend/copilot_knowledge/builder.py"
        problems = []
        for node in ast.walk(ast.parse(builder.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            kws = {k.arg: k.value for k in node.keywords if k.arg}
            if name == "write_text":
                nl = kws.get("newline")
                if not (isinstance(nl, ast.Constant) and nl.value == "\n"):
                    problems.append(f"第{node.lineno}行 write_text 未显式 newline='\\n'")
            elif name == "open":
                mode = None
                if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                    mode = node.args[1].value
                elif isinstance(kws.get("mode"), ast.Constant):
                    mode = kws["mode"].value
                mode = str(mode or "r")
                if "w" in mode and "b" not in mode and "newline" not in kws:
                    problems.append(f"第{node.lineno}行 open(mode={mode!r}) 文本模式写且未固定换行")
        assert not problems, (
            "builder 存在可能翻译换行的落盘调用（Windows 上会写出 CRLF，导致 manifest "
            "摘要与存仓字节不符——B-01 根因）：" + "；".join(problems))

    def test_bundle_dir_is_unambiguous(self, tmp_path, monkeypatch):
        """N-05：知识包根目录下同时存在多个包时必须失败关闭，不得按名字猜。"""
        from pathlib import Path
        from backend.copilot_knowledge.builder import build
        from backend.services.copilot.knowledge import KnowledgeStore

        sources = Path(__file__).resolve().parents[2] / "backend/copilot_knowledge/sources"
        root = tmp_path / "kbroot"
        root.mkdir()
        build(sources, "Mr.Linsang", "2099-01-01T00:00:00Z", out_root=root)
        # 构造内容漂移的第二个包
        drifted = tmp_path / "src2"
        drifted.mkdir()
        for f in sorted(sources.glob("*.md")):
            (drifted / f.name).write_text(f.read_text(encoding="utf-8"),
                                          encoding="utf-8")
        first = drifted / sorted(p.name for p in sources.glob("*.md"))[0]
        first.write_text(first.read_text(encoding="utf-8") + "\n## 漂移\n\n内容。\n",
                         encoding="utf-8")
        build(drifted, "Mr.Linsang", "2099-01-01T00:00:00Z", out_root=root)
        assert len([d for d in root.iterdir()
                    if (d / "manifest.json").exists()]) == 2

        monkeypatch.setenv("COPILOT_KNOWLEDGE_BUNDLE", str(root))
        store = KnowledgeStore()
        assert store.load() != "READY", (
            f"知识包根目录下有 2 个包却仍加载成功（选中 {store.bundle_id}）")
        assert store.status_info()["reason_code"] == "KNOWLEDGE_BUNDLE_AMBIGUOUS"

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
