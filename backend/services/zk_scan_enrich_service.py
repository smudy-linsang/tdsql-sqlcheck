"""V1.6.0.3 扫描期富集服务（设计 DESIGN-v1.6.0.3 §3-§5）。

扫描完成后，对每个发现实例：
  1) 实例名称：zk_name_resolution_service 五级解析链（MonitorDB + ZK setrun）；
  2) 业务库：  以业务账号对适配后 Proxy 做 SHOW DATABASES（沿用从严口径：
     精确排除系统库与 MonitorDB 库、跨 Proxy 一致性、任一失败即标记失败）。

并发：线程池默认 8；单 Proxy 连接超时 3s；实例上限默认 500（超出 skipped_limit）。
任何富集失败都不阻断扫描本身，状态入 enrich_status，UI 可筛选处置。
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Optional

from backend.services.zk_name_resolution_service import zk_name_resolution_service

logger = logging.getLogger("tdsql.zk_enrich")

SYSTEM_DATABASES = {"information_schema", "mysql", "performance_schema", "sys"}
ENRICH_MAX_INSTANCES = 500
ENRICH_WORKERS = 8
PROXY_CONNECT_TIMEOUT = 3


def _list_business_databases(endpoints: list[tuple[str, int]], username: str, password: str,
                             monitor_db: str) -> tuple[list[str], str]:
    """对每个端点 SHOW DATABASES；返回 (sorted_dbs, source_or_errorcode)。"""
    import pymysql
    import pymysql.cursors

    excluded = set(SYSTEM_DATABASES)
    if monitor_db:
        excluded.add(monitor_db.strip().lower())
    catalogues: list[set[str]] = []
    for host, port in endpoints:
        conn = pymysql.connect(host=host, port=int(port), user=username, password=password,
                               charset="utf8mb4", connect_timeout=PROXY_CONNECT_TIMEOUT,
                               read_timeout=10, cursorclass=pymysql.cursors.DictCursor,
                               autocommit=True)
        try:
            with conn.cursor() as cur:
                cur.execute("SHOW DATABASES")
                values = {
                    str((row or {}).get("Database") or (row or {}).get("database") or "").strip()
                    for row in (cur.fetchall() or [])
                }
        finally:
            conn.close()
        catalogues.append({n for n in values if n and n.lower() not in excluded})
    if not catalogues:
        return [], "NO_AVAILABLE_PROXY"
    canonical = catalogues[0]
    if any(values != canonical for values in catalogues[1:]):
        return [], "DATABASE_LIST_INCONSISTENT"
    if not canonical:
        return [], "NO_BUSINESS_DATABASE"
    return sorted(canonical, key=lambda item: (item.lower(), item)), "proxy_show"


def _proxy_endpoints(item: dict) -> list[tuple[str, int]]:
    raw = [p.strip() for p in str(item.get("proxy_list") or "").split(";") if p.strip()]
    primary = f"{item.get('host', '')}:{item.get('port', '')}"
    if primary not in raw and item.get("host"):
        raw.insert(0, primary)
    endpoints: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for text in raw:
        try:
            host, port_text = text.rsplit(":", 1)
            endpoint = (host.strip(), int(port_text))
        except (AttributeError, TypeError, ValueError):
            continue
        if endpoint[0] and 1 <= endpoint[1] <= 65535 and endpoint not in seen:
            endpoints.append(endpoint)
            seen.add(endpoint)
    return endpoints


def enrich_discovered_items(results: list[dict], monitor: Optional[dict],
                            business: Optional[dict], hint: str = "") -> list[dict]:
    """就地富集发现结果。monitor/business 缺凭据时仅做可做的部分。"""
    monitor_ready = bool(monitor and monitor.get("host") and monitor.get("port")
                         and monitor.get("username") and monitor.get("password")
                         and monitor.get("database"))
    business_ready = bool(business and business.get("username") and business.get("password"))

    monitor_conn = None
    if monitor_ready:
        try:
            import pymysql
            import pymysql.cursors
            monitor_conn = pymysql.connect(
                host=monitor["host"], port=int(monitor["port"]), user=monitor["username"],
                password=monitor["password"], database=monitor["database"],
                charset="utf8mb4", connect_timeout=PROXY_CONNECT_TIMEOUT,
                read_timeout=10, cursorclass=pymysql.cursors.DictCursor, autocommit=True)
        except Exception as exc:
            logger.warning("ZK_ENRICH_MONITOR_CONNECT_FAILED error_type=%s", type(exc).__name__)
            monitor_conn = None

    try:
        for index, item in enumerate(results):
            if index >= ENRICH_MAX_INSTANCES:
                item["enrich_status"] = "skipped_limit"
                item.setdefault("resolved_name", "")
                item.setdefault("name_source", "")
                item.setdefault("business_dbs", [])
                item.setdefault("databases_source", "")
                continue
            item["resolved_name"] = ""
            item["name_source"] = ""
            item["business_dbs"] = []
            item["databases_source"] = ""
            # 名称解析（串行，复用单条 MonitorDB 连接）
            if monitor_conn is not None or (item.get("zk_name_fields") or {}):
                name, source, _detail = zk_name_resolution_service.resolve(
                    monitor_conn, item.get("instance_id", ""), item.get("set_ids", []),
                    item.get("instance_kind", ""), hint=hint,
                    zk_name_fields=item.get("zk_name_fields"))
                item["resolved_name"] = name
                item["name_source"] = source
            # 业务库（池化）
            status_parts = []
            if business_ready:
                endpoints = _proxy_endpoints(item)
                if not endpoints:
                    status_parts.append("dbs_failed:NO_AVAILABLE_PROXY")
                else:
                    with ThreadPoolExecutor(max_workers=min(ENRICH_WORKERS, len(endpoints))) as pool:
                        future = pool.submit(
                            _list_business_databases, endpoints,
                            business["username"], business["password"],
                            monitor.get("database") if monitor else "")
                        try:
                            dbs, source = future.result(timeout=PROXY_CONNECT_TIMEOUT * len(endpoints) + 10)
                        except FuturesTimeoutError:
                            dbs, source = [], "BUSINESS_PROXY_TIMEOUT"
                        except Exception as exc:
                            dbs, source = [], f"BUSINESS_PROXY_FAILED:{type(exc).__name__}"
                    if source == "proxy_show":
                        item["business_dbs"] = dbs
                        item["databases_source"] = source
                    else:
                        status_parts.append(f"dbs_failed:{source}")
            if status_parts:
                item["enrich_status"] = ";".join(status_parts)
            elif item["resolved_name"] and item["business_dbs"]:
                item["enrich_status"] = "ok"
            elif item["resolved_name"]:
                item["enrich_status"] = "name_only"
            elif item["business_dbs"]:
                item["enrich_status"] = "dbs_only"
            else:
                item["enrich_status"] = "disabled" if not (monitor_ready or business_ready) else "unresolved"
    finally:
        if monitor_conn is not None:
            try:
                monitor_conn.close()
            except Exception:
                pass
    resolved = sum(1 for i in results if i.get("resolved_name"))
    with_dbs = sum(1 for i in results if i.get("business_dbs"))
    logger.info("ZK_ENRICH_COMPLETED records=%s named=%s with_dbs=%s",
                len(results), resolved, with_dbs)
    return results
