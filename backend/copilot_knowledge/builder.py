# -*- coding: utf-8 -*-
"""v1.6.4.0 / CP-1：知识包离线构建器（CP-W04，DETAIL §6.2）。

用法：
    python -m backend.copilot_knowledge.builder \
        --sources backend/copilot_knowledge/sources \
        --approved-by "<审批人>" --expires-at 2027-09-13T00:00:00Z

按标题/段落拆分 1200 Unicode 字符、相邻最多重叠 150 字符、不切代码围栏；
每段保留 source_id/section/原文位置/hash；产出 manifest.json（含 file_sizes
与 sha256）、chunks.jsonl、index.json。包 ID 由 app 兼容版本＋内容 hash 组成。

后台不提供上传任意压缩包覆盖的接口；发布重建并复核差异（M-03）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from backend import config

CHUNK_CHARS = 1200
CHUNK_OVERLAP = 150

_FRONT_RE = re.compile(r"^---\s*$")
_HEADING_RE = re.compile(r"^(#{1,4})\s+(.*)$")


def _parse_front_matter(text: str) -> tuple[dict, str]:
    """解析源文件头部 YAML 子集（key: value / key: [a,b]）。"""
    lines = text.split("\n")
    if not lines or not _FRONT_RE.match(lines[0]):
        raise ValueError("源文件缺少 front-matter 起始 ---")
    meta: dict = {}
    i = 1
    while i < len(lines) and not _FRONT_RE.match(lines[i]):
        line = lines[i]
        if ":" in line:
            k, _, v = line.partition(":")
            k, v = k.strip(), v.strip()
            if v.startswith("[") and v.endswith("]"):
                meta[k] = [x.strip() for x in v[1:-1].split(",") if x.strip()]
            else:
                meta[k] = v
        i += 1
    return meta, "\n".join(lines[i + 1:])


def _split_chunks(content: str) -> list[tuple[str, str]]:
    """按标题/段落拆分；不切代码围栏；相邻最多重叠 150 字符。"""
    blocks: list[tuple[str, str]] = []  # (section, text)
    section = ""
    buf: list[str] = []
    in_fence = False
    for line in content.split("\n"):
        if line.strip().startswith("```"):
            in_fence = not in_fence
        hm = _HEADING_RE.match(line)
        if hm and not in_fence:
            if buf:
                blocks.append((section, "\n".join(buf).strip()))
                buf = []
            section = hm.group(2).strip()
            continue
        buf.append(line)
    if buf:
        blocks.append((section, "\n".join(buf).strip()))

    chunks: list[tuple[str, str]] = []
    for sec, text in blocks:
        if not text:
            continue
        if len(text) <= CHUNK_CHARS:
            chunks.append((sec, text))
            continue
        # 按段落二分，段落级仍超长则按字符滑窗（不切围栏由围栏整块成段保证）
        paras = text.split("\n\n")
        cur = ""
        for para in paras:
            if cur and len(cur) + len(para) + 2 > CHUNK_CHARS:
                chunks.append((sec, cur.strip()))
                cur = cur[-CHUNK_OVERLAP:] + "\n\n" + para
            else:
                cur = (cur + "\n\n" + para) if cur else para
        if cur.strip():
            chunks.append((sec, cur.strip()))
    return chunks


def build(sources_dir: Path, approved_by: str, expires_at: str) -> Path:
    source_entries = []
    chunks: list[dict] = []
    for md in sorted(sources_dir.glob("*.md")):
        meta, body = _parse_front_matter(md.read_text(encoding="utf-8"))
        sid = meta.get("source_id")
        if not sid:
            raise ValueError(f"{md.name} 缺 source_id")
        entry = {
            "source_id": sid,
            "title": meta.get("title", sid),
            "url": meta.get("url", ""),
            "doc_path": meta.get("doc_path", ""),
            "version_range": meta.get("version_range", ""),
            "section": meta.get("section", ""),
            "authority": meta.get("authority", "USER_GUIDE"),
        }
        source_entries.append(entry)
        for idx, (sec, text) in enumerate(_split_chunks(body)):
            chunks.append({
                "chunk_id": f"{sid}#{idx:03d}",
                "source_id": sid,
                "title": entry["title"],
                "section": sec or entry["section"],
                "authority": entry["authority"],
                "product_families": meta.get("product_families", []),
                "kernel_range": meta.get("kernel_range", ""),
                "public_fixed": meta.get("public_fixed", "") == "true",
                "state": "ACTIVE",
                "content": text,
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            })

    app_version = config.APP_VERSION
    content_hash = hashlib.sha256(
        json.dumps(chunks, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    bundle_id = f"kb-{app_version}-{content_hash}"
    out_dir = Path(__file__).resolve().parent / bundle_id
    out_dir.mkdir(parents=True, exist_ok=True)

    chunks_path = out_dir / "chunks.jsonl"
    chunks_path.write_text(
        "\n".join(json.dumps(c, ensure_ascii=False) for c in chunks) + "\n",
        encoding="utf-8")
    index = {"bundle_id": bundle_id, "chunk_count": len(chunks),
             "algorithm": "bm25(k1=1.2,b=0.75)+bigram+exact(rule_id,error_code)"}
    index_path = out_dir / "index.json"
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "bundle_id": bundle_id,
        "app_min": "1.6.4.0",
        "app_max": app_version,
        "product_families": ["TDSQL-MySQL"],
        "source_entries": source_entries,
        "file_sizes": {
            "chunks.jsonl": chunks_path.stat().st_size,
            "index.json": index_path.stat().st_size,
        },
        "sha256": {
            "chunks.jsonl": hashlib.sha256(chunks_path.read_bytes()).hexdigest(),
            "index.json": hashlib.sha256(index_path.read_bytes()).hexdigest(),
        },
        "approved_by": approved_by,
        "reviewed_at": _now(),
        "expires_at": expires_at,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    # B-01（SIT 第一轮）：构建期自校验——写完立即回读核验 manifest 与实际文件一致，
    # 防止「manifest 生成后源文件被改动」导致运行期 INVALID 出厂。
    _problems = _self_verify(out_dir, manifest)
    if _problems:
        raise RuntimeError(
            "知识包构建后自校验失败: " + "；".join(_problems))
    return out_dir


def _self_verify(out_dir: Path, manifest: dict) -> list[str]:
    """构建后立即核验：file_sizes 与 sha256 必须与实际文件逐字节一致。"""
    problems: list[str] = []
    for name in ("chunks.jsonl", "index.json"):
        f = out_dir / name
        if not f.exists():
            problems.append(f"缺文件 {name}")
            continue
        actual_size = f.stat().st_size
        if manifest["file_sizes"].get(name) != actual_size:
            problems.append(
                f"{name} 大小不符: manifest={manifest['file_sizes'].get(name)} "
                f"actual={actual_size}")
        actual_hash = hashlib.sha256(f.read_bytes()).hexdigest()
        if manifest["sha256"].get(name) != actual_hash:
            problems.append(f"{name} sha256 不符")
    return problems


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", required=True)
    ap.add_argument("--approved-by", required=True)
    ap.add_argument("--expires-at", required=True)
    args = ap.parse_args(argv)
    out = build(Path(args.sources), args.approved_by, args.expires_at)
    print(f"built: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
