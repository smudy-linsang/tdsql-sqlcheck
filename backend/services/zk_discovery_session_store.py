# -*- coding: utf-8 -*-
"""V1.6.0.5 修复：ZK 发现会话/导入预览改存元数据库（worker 无关）。

旧版把发现会话 `_sessions` 与导入预览 `_previews` 存在进程内存，部署
`uvicorn --workers 2` 时预览请求落到无会话的 worker → 频繁 410"会话已过期"。
本模块把两者持久化到元数据库，跨 worker 共享；business/monitor 口令以现有
Fernet `encrypt_password` 加密入库（旧版内存还是明文，入库顺带提升安全）。

过期判据改用 `time.time()` 墙上时钟（`time.monotonic()` 是进程内单调时钟，
跨 worker 不一致，不能用于共享存储）。
"""
from __future__ import annotations

import json
import logging
import time
import uuid

from backend.services.database import _get_connection
from backend.services.security_service import decrypt_password, encrypt_password

logger = logging.getLogger("tdsql.zk_session")

SESSION_TTL_SECONDS = 10 * 60
PREVIEW_TTL_SECONDS = 5 * 60


def ensure_tables(conn) -> None:
    """幂等建表（IF NOT EXISTS）。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS zk_discovery_sessions (
            discovery_id VARCHAR(64) PRIMARY KEY,
            owner        VARCHAR(64) NOT NULL,
            is_mock      INT NOT NULL DEFAULT 0,
            expires_at   DOUBLE NOT NULL,
            items_json   LONGTEXT NOT NULL,
            created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS zk_discovery_previews (
            preview_id   VARCHAR(64) PRIMARY KEY,
            discovery_id VARCHAR(64) NOT NULL,
            owner        VARCHAR(64) NOT NULL,
            expires_at   DOUBLE NOT NULL,
            rows_json    LONGTEXT NOT NULL,
            business_enc TEXT,
            monitor_enc  TEXT,
            created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
    conn.commit()


def purge_expired(conn) -> None:
    now = time.time()
    conn.execute("DELETE FROM zk_discovery_sessions WHERE expires_at <= %s", (now,))
    conn.execute("DELETE FROM zk_discovery_previews WHERE expires_at <= %s", (now,))
    conn.commit()


def store_session(results: list, owner: str) -> tuple[str, list]:
    """脱敏后入库；返回 (discovery_id, visible_items)。口令永不进入响应。"""
    conn = _get_connection()
    try:
        ensure_tables(conn)
        purge_expired(conn)
        discovery_id = uuid.uuid4().hex
        visible_items: list = []
        raw_items: dict = {}
        is_mock = bool(results and results[0].get("is_mock"))
        for result in results:
            item_token = uuid.uuid4().hex
            raw = {k: v for k, v in result.items() if k not in {"password", "user", "database"}}
            raw_items[item_token] = raw
            visible = dict(raw)
            set_ids = sorted({str(v).strip() for v in (visible.get("set_ids") or []) if str(v).strip()})
            if not set_ids and visible.get("instance_kind") == "noshard" and visible.get("instance_id"):
                set_ids = [str(visible["instance_id"])]
            visible["set_ids"] = set_ids
            visible["proxy_count"] = len([p for p in str(visible.get("proxy_list") or "").split(";") if p.strip()])
            visible["primary_proxy"] = f"{visible.get('host', '')}:{visible.get('port', '')}"
            visible["item_token"] = item_token
            visible_items.append(visible)
        conn.execute(
            "INSERT INTO zk_discovery_sessions (discovery_id, owner, is_mock, expires_at, items_json) "
            "VALUES (%s, %s, %s, %s, %s)",
            (discovery_id, owner, 1 if is_mock else 0, time.time() + SESSION_TTL_SECONDS,
             json.dumps(raw_items, ensure_ascii=False)))
        conn.commit()
        return discovery_id, visible_items
    finally:
        conn.close()


def _read_session(conn, discovery_id: str) -> dict:
    row = conn.execute(
        "SELECT owner, is_mock, expires_at, items_json FROM zk_discovery_sessions "
        "WHERE discovery_id = %s", (discovery_id,)).fetchone()
    if not row or float(row["expires_at"]) <= time.time():
        conn.execute("DELETE FROM zk_discovery_sessions WHERE discovery_id = %s", (discovery_id,))
        conn.commit()
        raise _gone("发现会话已过期，请重新扫描")
    return dict(row)


def load_session_items(discovery_id: str, item_tokens: list, owner: str) -> list:
    """读取并校验本操作者的多个发现项；Mock 禁止进入预检。"""
    unique_tokens = list(dict.fromkeys(item_tokens))
    if len(unique_tokens) != len(item_tokens):
        raise _unprocessable("发现记录不可重复选择")
    conn = _get_connection()
    try:
        row = _read_session(conn, discovery_id)
        if row["owner"] != owner:
            raise _forbidden("无权使用其他操作者的发现会话")
        if int(row["is_mock"]):
            raise _conflict("Mock 发现结果禁止生成导入预览")
        items_map = json.loads(row["items_json"] or "{}")
        items = []
        for token in unique_tokens:
            item = items_map.get(token)
            if not item:
                raise _notfound("发现记录不存在")
            items.append(dict(item))
        return items
    finally:
        conn.close()


def get_session_items_map(discovery_id: str) -> dict:
    """name-diagnose 用：返回 {instance_id: raw}（管理员诊断，不校验 owner）。"""
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT items_json, expires_at FROM zk_discovery_sessions WHERE discovery_id = %s",
            (discovery_id,)).fetchone()
        if not row or float(row["expires_at"]) <= time.time():
            return {}
        items_map = json.loads(row["items_json"] or "{}")
        out: dict = {}
        for raw in items_map.values():
            out[str(raw.get("instance_id") or "")] = raw
        return out
    except Exception:
        return {}
    finally:
        conn.close()


def store_preview(discovery_id: str, owner: str, rows: list, business, monitor) -> tuple[str, list]:
    """凭据加密入库；返回 (preview_id, visible_rows)。"""
    conn = _get_connection()
    try:
        ensure_tables(conn)
        purge_expired(conn)
        preview_id = uuid.uuid4().hex
        private_rows: dict = {}
        visible_rows: list = []
        for row in rows:
            row_token = uuid.uuid4().hex
            private_rows[row_token] = dict(row)
            visible = dict(row)
            visible["row_token"] = row_token
            visible_rows.append(visible)
        business_enc = encrypt_password(json.dumps(
            {"username": business.username, "password": business.password}, ensure_ascii=False))
        monitor_enc = encrypt_password(json.dumps(
            {"host": monitor.host, "port": monitor.port, "username": monitor.username,
             "password": monitor.password, "database": monitor.database}, ensure_ascii=False))
        conn.execute(
            "INSERT INTO zk_discovery_previews (preview_id, discovery_id, owner, expires_at, "
            "rows_json, business_enc, monitor_enc) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (preview_id, discovery_id, owner, time.time() + PREVIEW_TTL_SECONDS,
             json.dumps(private_rows, ensure_ascii=False), business_enc, monitor_enc))
        conn.commit()
        return preview_id, visible_rows
    finally:
        conn.close()


def load_preview(preview_id: str, discovery_id: str, row_tokens: list, owner: str) -> tuple[dict, list]:
    """返回 (preview, selected)；preview 含 rows(dict)/business/monitor（解密后）。"""
    from backend.services.zk_connection_import_service import ImportCredentials, MonitorCredentials
    selected_tokens = list(dict.fromkeys(row_tokens))
    if len(selected_tokens) != len(row_tokens):
        raise _unprocessable("导入候选项不可重复选择")
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT owner, discovery_id, expires_at, rows_json, business_enc, monitor_enc "
            "FROM zk_discovery_previews WHERE preview_id = %s", (preview_id,)).fetchone()
        if not row or float(row["expires_at"]) <= time.time():
            conn.execute("DELETE FROM zk_discovery_previews WHERE preview_id = %s", (preview_id,))
            conn.commit()
            raise _gone("导入预览已过期，请重新生成")
        if row["owner"] != owner or row["discovery_id"] != discovery_id:
            raise _forbidden("无权使用该导入预览")
        rows_map = json.loads(row["rows_json"] or "{}")
        selected = []
        for token in selected_tokens:
            row_item = rows_map.get(token)
            if not row_item:
                raise _notfound("导入候选项不存在")
            selected.append(dict(row_item))
        business = ImportCredentials(**json.loads(decrypt_password(row["business_enc"] or "") or "{}"))
        monitor = MonitorCredentials(**json.loads(decrypt_password(row["monitor_enc"] or "") or "{}"))
        preview = {"rows": rows_map, "business": business, "monitor": monitor}
        return preview, selected
    finally:
        conn.close()


def delete_preview(preview_id: str) -> None:
    """提交后销毁预览（防重放）。"""
    conn = _get_connection()
    try:
        conn.execute("DELETE FROM zk_discovery_previews WHERE preview_id = %s", (preview_id,))
        conn.commit()
    finally:
        conn.close()


# ── 直接抛 FastAPI HTTPException，调用方（endpoint）无需翻译 ──
from fastapi import HTTPException as _HTTPException


def _gone(msg):
    return _HTTPException(status_code=410, detail=msg)


def _forbidden(msg):
    return _HTTPException(status_code=403, detail=msg)


def _conflict(msg):
    return _HTTPException(status_code=409, detail=msg)


def _notfound(msg):
    return _HTTPException(status_code=404, detail=msg)


def _unprocessable(msg):
    return _HTTPException(status_code=422, detail=msg)
