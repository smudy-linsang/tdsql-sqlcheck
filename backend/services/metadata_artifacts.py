# -*- coding: utf-8 -*-
"""元数据审核任务产物管理（v1.6.3.5 / DU-2 / FIX-03 / D06）。

设计出处：docs/DETAIL-v1.6.3.5-大库在线元数据审核稳定性修复.md §7.3/§7.4。

产物目录 `${REPORT_OUTPUT_DIR}/metadata-audit/<job_id>/`（在 release/current 外，
Web 与 runner 同用户可读）。所有文件先写 .part，flush/fsync 后原子 rename；
manifest.json 最后写入作为"核心制备完成"标志。索引偏移以 UTF-8 字节计。
"""

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger("tdsql.metadata_artifacts")

ARTIFACT_FILES = ("schema.sql", "results.json", "results.ndjson", "report.html",
                  "manifest.json")
_PREVIEW_BYTES = 64 * 1024          # sql-preview 固定前 64 KiB
_PREVIEW_TEXT_CHARS = 4096          # 结果页 sql_preview 前 4096 字符


def artifact_root() -> Path:
    """产物根目录（${REPORT_OUTPUT_DIR}/metadata-audit）。"""
    base = os.getenv("REPORT_OUTPUT_DIR", "").strip()
    if not base:
        base = str(Path(__file__).resolve().parents[2] / "data" / "reports")
    root = Path(base) / "metadata-audit"
    root.mkdir(parents=True, exist_ok=True)
    return root


def job_dir(job_id: str) -> Path:
    """任务产物目录；job_id 必须是 32 位 hex（防路径穿越）。"""
    if not job_id or len(job_id) != 32 or not all(c in "0123456789abcdef" for c in job_id):
        raise ValueError("invalid job_id")
    return artifact_root() / job_id


def atomic_write_text(path: Path, text: str) -> int:
    """写 .part → flush/fsync → 原子 rename。返回 UTF-8 字节数。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    part = path.with_name(path.name + ".part")
    with open(part, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(part, path)
    return len(data)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def write_manifest(job_id: str, *, context_hash: str, files_meta: dict,
                   counts: dict) -> Path:
    """最后写 manifest.json（核心制备完成标志）。不含密码。"""
    d = job_dir(job_id)
    d.mkdir(parents=True, exist_ok=True)
    manifest = {
        "version": 1, "job_id": job_id, "context_hash": context_hash,
        "files": files_meta, "counts": counts,
    }
    atomic_write_text(d / "manifest.json",
                      json.dumps(manifest, ensure_ascii=False, indent=2))
    # Linux 对父目录也 fsync（保证 rename 落盘）
    try:
        fd = os.open(str(d), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except (OSError, AttributeError):
        pass
    return d / "manifest.json"


def read_sql_preview(job_id: str) -> dict:
    """读 schema.sql 前 64 KiB（按 UTF-8 完整字符边界解码）。"""
    p = job_dir(job_id) / "schema.sql"
    if not p.exists():
        return {"available": False}
    total = p.stat().st_size
    with open(p, "rb") as f:
        raw = f.read(_PREVIEW_BYTES)
    text = raw.decode("utf-8", errors="ignore")
    # 截断到完整字符边界（decode errors=ignore 已丢弃半个字符）
    return {"available": True, "preview": text, "truncated": total > _PREVIEW_BYTES,
            "total_bytes": total}


def read_full_sql(job_id: str) -> bytes:
    p = job_dir(job_id) / "schema.sql"
    with open(p, "rb") as f:
        return f.read()


def read_report_html(job_id: str) -> bytes:
    p = job_dir(job_id) / "report.html"
    with open(p, "rb") as f:
        return f.read()


def read_results_page(job_id: str, offset: int, limit: int) -> dict:
    """按 offset/limit 读 results.ndjson 的一页（逐行流式，不全量加载）。

    每条附加 sql_preview（前 4096 字符）与 detail_external 标记。结果页只读
    previews 所需条目，不向状态接口返回完整 SQL。
    """
    p = job_dir(job_id) / "results.ndjson"
    if not p.exists():
        return {"available": False, "items": [], "total": 0}
    items = []
    total = 0
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            idx = total          # 0-based statement_index
            total += 1
            if idx < offset or len(items) >= limit:
                continue         # 仍需遍历以累计 total，但不收集页外条目
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            sql_full = rec.get("sql", "") or ""
            rec["sql_preview"] = sql_full[:_PREVIEW_TEXT_CHARS]
            rec["sql_truncated"] = len(sql_full) > _PREVIEW_TEXT_CHARS
            rec["statement_index"] = idx
            items.append(rec)
    next_offset = offset + len(items)
    return {"available": True, "items": items, "total": total,
            "next_offset": next_offset if next_offset < total else None}


def read_result_detail(job_id: str, statement_index: int) -> Optional[dict]:
    """按 statement_index 读单条完整结果（含全文 SQL 与全部违规）。"""
    p = job_dir(job_id) / "results.ndjson"
    if not p.exists() or statement_index < 0:
        return None
    with open(p, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i == statement_index:
                line = line.strip()
                return json.loads(line) if line else None
    return None


def cleanup_job_dir(job_id: str) -> bool:
    """安全清理任务产物目录：仅删除位于持久根下、32 位 hex、非 symlink 的目录。"""
    d = job_dir(job_id)   # 非法 job_id 已在 job_dir 抛错
    root = artifact_root().resolve()
    target = d.resolve()
    # 防 symlink/junction/越界：目标必须严格位于 root 之下
    if not str(target).startswith(str(root) + os.sep):
        logger.warning("拒绝清理越界/符号链接目录: %s", d)
        return False
    if d.is_symlink() or not d.exists():
        return False
    import shutil
    shutil.rmtree(d, ignore_errors=True)
    return True


def job_artifact_bytes(job_id: str) -> int:
    """任务产物总字节数（用于磁盘配额核验）。"""
    d = job_dir(job_id)
    if not d.exists():
        return 0
    total = 0
    for f in d.rglob("*"):
        if f.is_file():
            total += f.stat().st_size
    return total
