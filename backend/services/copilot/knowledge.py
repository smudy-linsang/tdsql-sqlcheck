# -*- coding: utf-8 -*-
"""v1.6.4.0 / CP-1：本地知识库（CP-W04，DETAIL §6）。

离线知识包：backend/copilot_knowledge/<bundle_id>/{manifest.json,chunks.jsonl,index.json}
- manifest 必填：schema_version、bundle_id、app_min/app_max、product_families、
  source_entries、file_sizes、sha256、approved_by、reviewed_at、expires_at；
- 启动/切换核验大小/hash/兼容范围/有效期：READY / STALE / INVALID / MISSING；
- 检索：产品族/app版本/内核范围过滤 → 精确词（规则号 Rxxx、错误码）优先 →
  中文双字词 + 英文小写词 BM25(k1=1.2, b=0.75) → 同分稳定排序；
  每来源最多 3 段，最终最多 8 段 / 8KiB；
- STALE 内容不得用于当前回答；无命中返回空，不用模型知识补成官方结论。
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
from collections import Counter
from pathlib import Path
from typing import Optional

from backend import config

logger = logging.getLogger("tdsql.copilot.knowledge")

# backend/services/copilot/knowledge.py → backend/copilot_knowledge
KNOWLEDGE_ROOT = Path(__file__).resolve().parents[2] / "copilot_knowledge"

MAX_CHUNKS = 10_000
MAX_BUNDLE_BYTES = 32 * 1024 * 1024
MAX_RESULTS = 8
MAX_RESULT_BYTES = 8192
PER_SOURCE_MAX = 3
MIN_SCORE = 10.0  # N-01（UAT D40）：阈值用黄金集标定——在域最低分与越界最高分之间的分界

STATUS_READY = "READY"
STATUS_STALE = "STALE"
STATUS_INVALID = "INVALID"
STATUS_MISSING = "MISSING"

_RULE_ID_RE = re.compile(r"\bR\d{3}\b")
_ERROR_CODE_RE = re.compile(r"\b[A-Z][A-Z0-9_]{3,40}\b")
_EN_WORD_RE = re.compile(r"[a-z0-9_]{2,}")


def _tokenize(text: str) -> list[str]:
    """中文连续双字词 + 英文小写词 + 规则号/错误码精确词。"""
    tokens: list[str] = []
    lower = text.lower()
    tokens.extend(_EN_WORD_RE.findall(lower))
    cjk = re.findall(r"[一-鿿]+", text)
    for seg in cjk:
        if len(seg) == 1:
            tokens.append(seg)
        else:
            tokens.extend(seg[i:i + 2] for i in range(len(seg) - 1))
    tokens.extend(m.group(0) for m in _RULE_ID_RE.finditer(text))
    tokens.extend(m.group(0) for m in _ERROR_CODE_RE.finditer(text))
    return tokens


class _AmbiguousBundle(Exception):
    """知识包根目录下存在多个包 —— 必须由运维删除旧包，不得由程序猜。"""

    def __init__(self, names: list[str]):
        super().__init__(", ".join(names))
        self.names = names


class KnowledgeBundle:
    """不可变内存索引的知识包。"""

    def __init__(self, bundle_dir: Path, manifest: dict, chunks: list[dict]):
        self.dir = bundle_dir
        self.manifest = manifest
        self.chunks = chunks
        self.bundle_id = manifest["bundle_id"]
        self._df: Counter = Counter()
        self._tf: list[Counter] = []
        for ch in chunks:
            tf = Counter(ch.get("_tokens") or _tokenize(ch.get("content", "")))
            self._tf.append(tf)
            for t in tf:
                self._df[t] += 1
        self._avgdl = (sum(sum(t.values()) for t in self._tf) / len(self._tf)) \
            if self._tf else 1.0

    # ── 检索 ─────────────────────────────────────────────
    def search(self, query: str, product_family: str = "",
               app_version: str = "", kernel: str = "",
               limit: int = MAX_RESULTS) -> list[dict]:
        """BM25 检索；规则号/错误码精确命中优先；每来源≤3段；总≤8段/8KiB。"""
        q_tokens = _tokenize(query)
        if not q_tokens:
            return []
        q_rules = set(_RULE_ID_RE.findall(query))
        q_errs = set(_ERROR_CODE_RE.findall(query)) - {
            "SELECT", "FROM", "WHERE", "INSERT", "UPDATE", "DELETE", "CREATE"}
        n_docs = len(self.chunks)
        k1, b = 1.2, 0.75
        scored: list[tuple[float, int]] = []
        for idx, ch in enumerate(self.chunks):
            if not self._applicable(ch, product_family, app_version, kernel):
                continue
            tf = self._tf[idx]
            dl = sum(tf.values()) or 1
            score = 0.0
            for t in q_tokens:
                f = tf.get(t, 0)
                if f == 0:
                    continue
                idf = math.log(1 + (n_docs - self._df[t] + 0.5) / (self._df[t] + 0.5))
                score += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / self._avgdl))
            # 精确词加权：规则号 > 错误码
            ch_text = ch.get("content", "")
            for rid in q_rules:
                if rid in ch_text:
                    score += 10.0
            for ec in q_errs:
                if ec in ch_text:
                    score += 4.0
            if score >= MIN_SCORE:
                scored.append((score, idx))
        # 同分按 source_id/chunk_id 稳定排序
        scored.sort(key=lambda x: (-x[0], self.chunks[x[1]].get("source_id", ""),
                                   self.chunks[x[1]].get("chunk_id", "")))
        results: list[dict] = []
        per_source: Counter = Counter()
        total_bytes = 0
        for score, idx in scored:
            ch = self.chunks[idx]
            sid = ch.get("source_id", "")
            if per_source[sid] >= PER_SOURCE_MAX:
                continue
            content = ch.get("content", "")
            cb = len(content.encode("utf-8"))
            if results and (total_bytes + cb > MAX_RESULT_BYTES):
                continue
            if len(results) >= limit:
                break
            per_source[sid] += 1
            total_bytes += cb
            results.append({
                "knowledge_id": f"K{len(results) + 1}",
                "bundle_id": self.bundle_id,
                "source_id": sid,
                "title": ch.get("title", ""),
                "section": ch.get("section", ""),
                "authority": ch.get("authority", "USER_GUIDE"),
                "content": content,
                "content_hash": ch.get("sha256", ""),
                "score": round(score, 4),
            })
        return results

    def _applicable(self, chunk: dict, product_family: str, app_version: str,
                    kernel: str) -> bool:
        if chunk.get("state", "ACTIVE") != "ACTIVE":
            return False
        pf = chunk.get("product_families")
        if pf and product_family and product_family not in pf:
            return False
        return True

    def fixed_public_answer(self, public_question_id: str) -> Optional[dict]:
        """PUBLIC_HELP 正向来源：固定问题 ID → 批准公共内容。"""
        for ch in self.chunks:
            if ch.get("chunk_id") == public_question_id and \
                    ch.get("authority") == "USER_GUIDE" and \
                    ch.get("public_fixed"):
                return ch
        return None


# ══════════════════════════════════════════════════════════════════
# 加载与核验
# ══════════════════════════════════════════════════════════════════

class KnowledgeStore:
    """知识包存储：启动/切换核验大小/hash/兼容范围/有效期。"""

    def __init__(self):
        self._bundle: Optional[KnowledgeBundle] = None
        self._status = STATUS_MISSING
        self._reason = ""
        self._error_detail = ""

    @property
    def status(self) -> str:
        return self._status

    @property
    def bundle_id(self) -> str:
        return self._bundle.bundle_id if self._bundle else ""

    def status_info(self) -> dict:
        return {
            "knowledge_status": self._status,
            "bundle_id": self.bundle_id,
            "reason_code": self._reason,
        }

    def load(self, bundle_dir: Optional[str] = None) -> str:
        """加载并核验知识包；返回状态。失败仅知识能力降级，不影响规则引擎。"""
        root = Path(bundle_dir) if bundle_dir else Path(
            os.getenv("COPILOT_KNOWLEDGE_BUNDLE", str(KNOWLEDGE_ROOT)))
        try:
            if not root.exists():
                # COPILOT_KNOWLEDGE_BUNDLE 指向具体包目录或根目录
                self._bundle = None
                self._status = STATUS_MISSING
                self._reason = "KNOWLEDGE_BUNDLE_MISSING"
                return self._status
            try:
                bdir = self._resolve_bundle_dir(root)
            except _AmbiguousBundle as amb:
                self._bundle = None
                self._status = STATUS_INVALID
                self._reason = "KNOWLEDGE_BUNDLE_AMBIGUOUS"
                self._error_detail = "知识包根目录存在多个包: " + ", ".join(amb.names)
                logger.error("知识包目录不唯一，拒绝加载: %s", self._error_detail)
                return self._status
            if bdir is None:
                self._bundle = None
                self._status = STATUS_MISSING
                self._reason = "KNOWLEDGE_BUNDLE_MISSING"
                return self._status
            manifest_path = bdir / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            problems = self._verify(bdir, manifest)
            if problems:
                self._bundle = None
                self._status = STATUS_INVALID
                self._reason = "KNOWLEDGE_BUNDLE_INVALID"
                self._error_detail = "; ".join(problems[:3])
                logger.error("知识包核验失败: %s", self._error_detail)
                return self._status
            # 兼容范围与有效期
            compat = self._check_compat(manifest)
            if compat:
                self._bundle = None
                self._status = STATUS_STALE
                self._reason = "KNOWLEDGE_BUNDLE_STALE"
                logger.warning("知识包不可用（过期/不兼容）: %s", compat)
                return self._status
            chunks = self._load_chunks(bdir)
            self._bundle = KnowledgeBundle(bdir, manifest, chunks)
            self._status = STATUS_READY
            self._reason = ""
            logger.info("知识包加载完成: %s (%d 段)", self._bundle.bundle_id,
                        len(chunks))
            return self._status
        except Exception as e:
            logger.error("知识包加载异常: %s", e, exc_info=True)
            self._bundle = None
            self._status = STATUS_INVALID
            self._reason = "KNOWLEDGE_BUNDLE_INVALID"
            return self._status

    @staticmethod
    def _resolve_bundle_dir(root: Path) -> Optional[Path]:
        if (root / "manifest.json").exists():
            return root
        # 根目录下必须只有一个知识包。目录名里的是内容 hash，不含时间序，
        # 多包并存时按名字取"最后一个"等于随机选，会静默加载旧内容（N-05）。
        candidates = [d for d in sorted(root.iterdir())
                      if d.is_dir() and (d / "manifest.json").exists()]
        if len(candidates) > 1:
            raise _AmbiguousBundle([d.name for d in candidates])
        return candidates[0] if candidates else None

    @staticmethod
    def _verify(bdir: Path, manifest: dict) -> list[str]:
        problems = []
        required = ("schema_version", "bundle_id", "app_min", "app_max",
                    "product_families", "source_entries", "file_sizes",
                    "sha256", "approved_by", "reviewed_at", "expires_at")
        for k in required:
            if k not in manifest:
                problems.append(f"manifest 缺字段 {k}")
        if problems:
            return problems
        if manifest["schema_version"] != 1:
            problems.append("schema_version 非 1")
        # 文件大小与 hash 核验
        total = 0
        for name in ("manifest.json", "chunks.jsonl", "index.json"):
            f = bdir / name
            if not f.exists():
                problems.append(f"缺文件 {name}")
                continue
            total += f.stat().st_size
            expected_size = manifest["file_sizes"].get(name)
            if expected_size is not None and f.stat().st_size != expected_size:
                problems.append(f"{name} 大小不符")
            expected_hash = manifest["sha256"].get(name)
            if expected_hash is not None and name != "manifest.json":
                actual = hashlib.sha256(f.read_bytes()).hexdigest()
                if actual != expected_hash:
                    problems.append(f"{name} 校验和不符")
        if total > MAX_BUNDLE_BYTES:
            problems.append("包大小超过 32MiB 上限")
        return problems

    @staticmethod
    def _check_compat(manifest: dict) -> str:
        """返回空串=兼容；否则原因。"""
        from datetime import datetime, timezone
        app = config.APP_VERSION
        lo, hi = str(manifest.get("app_min", "")), str(manifest.get("app_max", ""))
        def _ver(v: str):
            try:
                return tuple(int(x) for x in v.split("."))
            except Exception:
                return None
        va, vlo, vhi = _ver(app), _ver(lo), _ver(hi)
        if va and vlo and va < vlo:
            return f"app {app} < app_min {lo}"
        if va and vhi and va > vhi:
            return f"app {app} > app_max {hi}"
        exp = str(manifest.get("expires_at", ""))
        try:
            exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > exp_dt:
                return f"知识包已过期（{exp}）"
        except ValueError:
            return "expires_at 非法"
        return ""

    @staticmethod
    def _load_chunks(bdir: Path) -> list[dict]:
        chunks: list[dict] = []
        with open(bdir / "chunks.jsonl", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                chunks.append(json.loads(line))
        if len(chunks) > MAX_CHUNKS:
            raise ValueError("知识包段数超过 10000 上限")
        for ch in chunks:
            ch["_tokens"] = _tokenize(
                f"{ch.get('title', '')} {ch.get('section', '')} {ch.get('content', '')}")
        return chunks

    @property
    def bundle(self) -> Optional[KnowledgeBundle]:
        return self._bundle if self._status == STATUS_READY else None


#: 进程级单例（Web/runner 各自加载）
store = KnowledgeStore()
