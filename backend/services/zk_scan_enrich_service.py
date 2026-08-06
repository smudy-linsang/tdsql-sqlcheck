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

SYSTEM_DATABASES = {"information_schema", "mysql", "performance_schema", "sys",
                    # v1.6.1.0：sysdb 为 TDSQL 实例默认管理库，非业务库，不纳入 SQL 审核（设计 DESIGN-v1.6.0.8 §4）
                    "sysdb",
                    # v1.6.1.1：query_rewrite（查询改写）/xa（XA 事务管理）同为实例默认库，一并屏蔽（设计 DESIGN-v1.6.1.1 §4）
                    "query_rewrite", "xa"}
ENRICH_MAX_INSTANCES = 500
ENRICH_WORKERS = 8
PROXY_CONNECT_TIMEOUT = 3


def _errno_of(exc: Exception):
    """v1.6.1.0：提取数据库 errno，沿 __cause__ 链追原始异常。

    导入侧 _connect 会把 pymysql 异常包成 ZKImportPreparationError（A-P2-02），
    原始 errno 在 __cause__ 里；富集侧直连 pymysql 则在本层。
    """
    for e in (exc, getattr(exc, "__cause__", None)):
        if e is not None and getattr(e, "args", None) and isinstance(e.args[0], int):
            return e.args[0]
    return None


def _list_business_databases(endpoints: list[tuple[str, int]], username: str, password: str,
                             monitor_db: str) -> tuple[list[str], str]:
    """v1.6.0.5 修复：业务库枚举改为"≥1 个 Proxy 成功即可"（形态无关）。

    旧版要求全部 Proxy 连接成功，任一不可达/鉴权失败即整实例失败；内网分布式与
    集中式普遍双 Proxy（主+备），备 Proxy 不可达导致大面积枚举失败。
    现逐端点捕获异常不抛出：
      - 0 个成功且全部 1045 -> ([], "NO_BUSINESS_USER")（未创建监控用户/口令不符，v1.6.1.0）；
      - 0 个成功含连接类/混合 -> ([], "NO_AVAILABLE_PROXY")；
      - 1 个成功 -> 用之，source="proxy_show"；
      - 多个成功且一致 -> 用之，source="proxy_show"；
      - 多个成功但不一致 -> 取并集，source="proxy_show_partial"（不整实例失败）。
    端点顺序由 _proxy_endpoints 保证主 Proxy 在最前（已知可达优先）。
    """
    import pymysql
    import pymysql.cursors

    excluded = set(SYSTEM_DATABASES)
    if monitor_db:
        excluded.add(monitor_db.strip().lower())
    catalogues: list[set[str]] = []
    failed = 0
    auth_failures = 0
    for host, port in endpoints:
        try:
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
        except Exception as exc:
            failed += 1
            # v1.6.1.0：捕获 errno 用于失败分类（1045=鉴权失败，通常为未创建监控用户）
            errno_ = _errno_of(exc)
            if errno_ == 1045:
                auth_failures += 1
            logger.warning("ZK_ENRICH_PROXY_FAILED endpoint=%s:%s error_type=%s errno=%s",
                           host, port, type(exc).__name__, errno_)
    if not catalogues:
        # v1.6.1.0（设计 DESIGN-v1.6.0.8 §3）：全部鉴权失败 → NO_BUSINESS_USER，
        # 页面可提示"未创建监控用户"；含连接类/混合失败仍为 NO_AVAILABLE_PROXY。
        if failed and auth_failures == failed:
            return [], "NO_BUSINESS_USER"
        return [], "NO_AVAILABLE_PROXY"
    union = set().union(*catalogues)
    # v1.6.0.6（A-P2-01）：只要有 Proxy 失败就标降级，"用一个 Proxy 的目录
    # 代表整个实例"这件事用户有权知道（R-15：漏掉的库是不可见的错误）。
    if failed or any(c != catalogues[0] for c in catalogues[1:]):
        logger.warning("ZK_ENRICH_DBS_DEGRADED proxies=%s failed=%s 取并集并标降级", len(catalogues), failed)
        return sorted(union, key=lambda s: (s.lower(), s)), "proxy_show_partial"
    return sorted(union, key=lambda s: (s.lower(), s)), "proxy_show"


def _proxy_endpoints(item: dict) -> list[tuple[str, int]]:
    raw = [p.strip() for p in str(item.get("proxy_list") or "").split(";") if p.strip()]
    primary = f"{item.get('host', '')}:{item.get('port', '')}"
    # v1.6.0.6（A-P3-01）：主 Proxy 真正前移——先移除再插首位（旧实现仅在
    # proxy_list 不含主 Proxy 时才插入，实际总含，等于从未生效）。
    if item.get("host"):
        raw = [p for p in raw if p != primary]
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
                    if source in ("proxy_show", "proxy_show_partial"):
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
