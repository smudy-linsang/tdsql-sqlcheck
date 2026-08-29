"""G11 网关 (Proxy) 日志分析服务

提供网关日志文件上传、解析、分析报告生成与落库持久化的完整业务逻辑。
调用 backend/services/gateway_log_analysis/analyze_gateway_log.py 脚本。
"""
import logging
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from html import escape as html_escape
from pathlib import Path
from backend.services.database import _get_connection, _execute_sql

logger = logging.getLogger("tdsql.gateway_log")

# v1.6.2.2-UAT-O-17：混合输入跳过比例阈值（可配置）。超过阈值时拒绝生成报告，
# 避免只覆盖三分之一输入却按完整报告展示、健康结论无覆盖率背书。
_MAX_SKIP_RATIO = min(max(float(os.getenv("GATEWAY_MAX_SKIP_RATIO", "0.5")), 0.0), 1.0)
# 部分有效时的最大丢弃样例条数（写进响应与报告横幅，供用户定位原因）
_SKIP_SAMPLE_LIMIT = 5

# v1.6.2.2-UAT-O-15：报告 iframe 不再把长期令牌放进可见 URL，改用短时一次性报告票据：
# 登录后经 POST 签发（90s 有效、用后即焚、绑定报告 ID），iframe 只携带该票据。
_REPORT_TICKETS: dict = {}
_REPORT_TICKET_LOCK = threading.Lock()
_REPORT_TICKET_TTL_SECONDS = 90


class GatewayLogService:
    """网关日志服务"""

    def analyze_log(
        self,
        connection_id: str,
        file_name: str,
        file_content: bytes,
        log_type: str = "interf",
        slow_threshold_ms: float = 1000.0
    ) -> dict:
        """
        上传并分析网关日志文件，解析统计指标，生成 HTML 报告并落库。
        """
        logger.info(f"开始分析网关日志: file={file_name}, connection_id={connection_id}, type={log_type}")

        # 1) 在 Python 侧进行快速指标统计，以便存入元数据库
        total_queries = 0
        slow_queries = 0
        max_time_ms = 0.0
        sum_time_ms = 0.0

        # v1.6.2.2-UAT-O-17：结构化解析质量统计——混合输入不得静默丢行，
        # 报告必须携带覆盖率，健康结论不得在大量跳过时冒充全量。
        total_lines = 0
        empty_lines = 0
        nonempty_lines = 0
        invalid_format_lines = 0   # 行首格式不匹配（非网关日志行）
        no_timecost_lines = 0      # 格式合法但缺 timecost 字段/匹配
        numeric_error_lines = 0    # timecost 数值非法
        skip_samples: list = []    # [(原因, 行摘要)]
        _header_re = re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \d+\]\s+\w+\s+(.*)")

        def _record_skip(reason: str, line_text: str):
            if len(skip_samples) < _SKIP_SAMPLE_LIMIT:
                skip_samples.append({"reason": reason, "line": line_text[:160]})

        # 分行处理（支持 \n 或 \r\n）
        lines = file_content.decode("utf-8", errors="ignore").splitlines()
        total_lines = len(lines)

        for line in lines:
            line = line.strip()
            if not line:
                empty_lines += 1
                continue
            nonempty_lines += 1
            
            # interf 日志解析
            if log_type == "interf":
                # [2026-02-26 00:00:00 002408] INFO topic=...
                m = _header_re.match(line)
                if not m:
                    invalid_format_lines += 1
                    _record_skip("行首格式不匹配（非网关日志行）", line)
                    continue
                body = m.group(2)
                fields = {}
                for part in body.split("&"):
                    if "=" in part:
                        k, _, v = part.partition("=")
                        fields[k] = v
                
                if "timecost" not in fields:
                    no_timecost_lines += 1
                    _record_skip("无 timecost 字段", line)
                    continue
                try:
                    tc = float(fields["timecost"])
                except ValueError:
                    numeric_error_lines += 1
                    _record_skip("timecost 数值非法", line)
                    continue
                total_queries += 1
                sum_time_ms += tc
                if tc > max_time_ms:
                    max_time_ms = tc
                if tc >= slow_threshold_ms:
                    slow_queries += 1
            elif log_type == "sql":
                # sql_instance 日志解析
                m = _header_re.match(line)
                if not m:
                    invalid_format_lines += 1
                    _record_skip("行首格式不匹配（非网关日志行）", line)
                    continue
                body = m.group(2)
                tc_m = re.search(r"timecost:([\d.]+)\(ms\)", body)
                if not tc_m:
                    no_timecost_lines += 1
                    _record_skip("无 timecost 字段", line)
                    continue
                try:
                    tc = float(tc_m.group(1))
                except ValueError:
                    numeric_error_lines += 1
                    _record_skip("timecost 数值非法", line)
                    continue
                total_queries += 1
                sum_time_ms += tc
                if tc > max_time_ms:
                    max_time_ms = tc
                if tc >= slow_threshold_ms:
                    slow_queries += 1
            else:
                invalid_format_lines += 1
                _record_skip(f"不支持的日志类型: {log_type}", line)

        avg_time_ms = (sum_time_ms / total_queries) if total_queries > 0 else 0.0
        skipped_lines = nonempty_lines - total_queries
        skip_ratio = (skipped_lines / nonempty_lines) if nonempty_lines else 0.0
        coverage_ratio = (total_queries / nonempty_lines) if nonempty_lines else 0.0

        # v1.6.2.2-UAT-O-11：零有效记录必须显式失败，不得用行数冒充查询数、
        # 不得把空报告持久化为"成功/健康"——空文件/垃圾文件会让用户在正式报告里
        # 看到虚构的 total_queries 与“指标正常”结论。
        if total_queries == 0:
            raise ValueError(
                f"未从日志中解析到任何有效查询记录（文件共 {total_lines} 行，"
                f"非空 {nonempty_lines} 行；格式不匹配 {invalid_format_lines}、"
                f"缺 timecost {no_timecost_lines}、数值错误 {numeric_error_lines}）。"
                f"请确认上传的是 {log_type} 类型的 TDSQL 网关日志，且内容未损坏。"
            )

        # v1.6.2.2-UAT-O-17：跳过比例超阈值拒绝生成报告（可配置），
        # 避免大量丢行仍按完整报告展示。
        if skip_ratio > _MAX_SKIP_RATIO:
            raise ValueError(
                f"有效行占比过低：非空 {nonempty_lines} 行中仅解析出 {total_queries} 行"
                f"（覆盖率 {coverage_ratio:.1%}，跳过 {skipped_lines} 行；格式不匹配 "
                f"{invalid_format_lines}、缺 timecost {no_timecost_lines}、数值错误 "
                f"{numeric_error_lines}），低于阈值 {1 - _MAX_SKIP_RATIO:.0%}。"
                f"请确认日志类型与文件完整性后重试。"
            )

        # 混合输入：部分有效仍可生成报告，但必须以 partial 状态与醒目告警携带覆盖率。
        parse_status = "partial" if skipped_lines > 0 else "success"

        # 2) 写入临时文件，供 analyze_gateway_log.py 读取
        # v1.6.2.2-UAT-O-03：分析器 _organize_specific_files() 按
        # <type>_instance_<port>.<date>.<seq> 命名规范识别文件类型与实例；
        # 旧实现写成 uploaded_<pid>.log，命名不匹配导致文件被静默跳过，
        # 生成的报告只有页脚没有正文。此处按调用方已知的 log_type 与连接端口
        # 构造受控文件名；并使用逐请求独立临时目录，避免并发上传互相覆盖。
        temp_dir = Path(tempfile.mkdtemp(prefix="tdsql_log_analysis_"))

        # 端口尽力从实例配置取；取不到用 0 占位（仅参与报告标题，不影响解析）
        port = 0
        try:
            from backend.services.connection_registry import registry
            saved = registry.get_saved(connection_id) or {}
            port = int(saved.get("port") or 0)
        except Exception:
            pass

        safe_type = re.sub(r"[^a-z_]", "", (log_type or "interf").lower()) or "interf"
        date_str = datetime.now().strftime("%Y-%m-%d")
        temp_log_file = temp_dir / f"{safe_type}_instance_{port}.{date_str}.0"
        temp_html_file = temp_dir / f"report_{os.getpid()}.html"

        try:
            temp_log_file.write_bytes(file_content)

            # 3) 执行 analyze_gateway_log.py
            script_path = Path(__file__).parent / "gateway_log_analysis" / "analyze_gateway_log.py"
            if not script_path.exists():
                raise FileNotFoundError(f"网关日志分析脚本未找到: {script_path}")

            cmd = [
                "python", str(script_path),
                "--files", str(temp_log_file),
                "-o", str(temp_html_file),
                "--log-types", log_type,
                "-f", "html"
            ]

            logger.info(f"执行日志分析命令: {' '.join(cmd)}")
            # 子进程的 sys.path[0] 是脚本所在目录（backend/services/gateway_log_analysis/），
            # 仓库根目录不在其中；分析器 normalize_sql() 会延迟导入 backend.services.sql_masking，
            # 必须把仓库根目录注入 PYTHONPATH，否则解析到含 db 字段的日志行即
            # ModuleNotFoundError: No module named 'backend'。
            _env = dict(os.environ)
            _repo_root = str(Path(__file__).resolve().parents[2])
            _env["PYTHONPATH"] = _repo_root + os.pathsep + _env.get("PYTHONPATH", "")
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=_env)
            
            # 注意: 即使脚本可能有一些警告，只要生成了 HTML 就视为成功
            if not temp_html_file.exists():
                logger.error(f"分析脚本未能生成报告文件. stdout={res.stdout}, stderr={res.stderr}")
                raise RuntimeError(f"网关日志分析失败: {res.stderr or '未能生成报告'}")

            # 4) 读取生成的 HTML
            report_html = temp_html_file.read_text(encoding="utf-8", errors="replace")

            # v1.6.2.2-UAT-O-17：混合输入的报告必须在顶部携带醒目数据完整性告警，
            # 健康结论不得在大量跳过时冒充全量。
            if parse_status == "partial":
                _sample_items = "".join(
                    f"<li>[{html_escape(s['reason'])}] "
                    f"<code>{html_escape(s['line'])}</code></li>"
                    for s in skip_samples)
                _banner = (
                    '<div class="alert alert-danger" style="border:2px solid #dc3545;'
                    'background:#f8d7da;color:#842029;padding:14px 18px;border-radius:8px;'
                    'margin:16px 0;font-size:0.95em;">'
                    f'<strong>⚠️ 数据完整性告警（部分有效输入 / partial）：</strong>'
                    f'本次输入共 {nonempty_lines} 行非空日志，仅解析出有效查询 '
                    f'{total_queries} 行（覆盖率 {coverage_ratio:.1%}），'
                    f'跳过 {skipped_lines} 行（格式不匹配 {invalid_format_lines}、'
                    f'缺 timecost {no_timecost_lines}、数值错误 {numeric_error_lines}）。'
                    '<b>本报告结论仅覆盖已解析部分，不代表全量输入。</b>'
                    + (f'<div style="margin-top:8px;">跳过样例（前 {len(skip_samples)} 条）：'
                       f'<ul style="margin:4px 0 0 18px;">{_sample_items}</ul></div>'
                       if _sample_items else '')
                    + '</div>')
                _anchor = '<div class="container">'
                if _anchor in report_html:
                    report_html = report_html.replace(_anchor, _anchor + _banner, 1)
                else:
                    report_html = _banner + report_html

            # 5) 结果落库到 gateway_log_reports
            report_id = self._save_report(
                connection_id=connection_id,
                file_name=file_name,
                log_type=log_type,
                total_queries=total_queries,
                slow_queries=slow_queries,
                max_time_ms=max_time_ms,
                avg_time_ms=avg_time_ms,
                report_html=report_html
            )

            return {
                "id": report_id,
                "connection_id": connection_id,
                "log_file_name": file_name,
                "log_type": log_type,
                "total_queries": total_queries,
                "slow_queries": slow_queries,
                "max_time_ms": max_time_ms,
                "avg_time_ms": avg_time_ms,
                "report_html": report_html,
                # v1.6.2.2-UAT-O-17：解析质量与覆盖率随响应返回，不得静默丢行。
                "status": parse_status,
                "parse_quality": {
                    "total_lines": total_lines,
                    "empty_lines": empty_lines,
                    "nonempty_lines": nonempty_lines,
                    "parsed_lines": total_queries,
                    "skipped_lines": skipped_lines,
                    "invalid_format_lines": invalid_format_lines,
                    "no_timecost_lines": no_timecost_lines,
                    "numeric_error_lines": numeric_error_lines,
                    "coverage_ratio": round(coverage_ratio, 4),
                    "skip_samples": skip_samples,
                },
            }

        finally:
            # 清理逐请求独立临时目录（含日志与报告）
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass

    def _save_report(
        self,
        connection_id: str,
        file_name: str,
        log_type: str,
        total_queries: int,
        slow_queries: int,
        max_time_ms: float,
        avg_time_ms: float,
        report_html: str
    ) -> int:
        """将报告数据插入元数据库"""
        conn = _get_connection()
        try:
            cursor = _execute_sql(conn, """
                INSERT INTO gateway_log_reports 
                (connection_id, log_file_name, log_type, total_queries, slow_queries, 
                 max_time_ms, avg_time_ms, report_html, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                connection_id, file_name, log_type, total_queries, slow_queries,
                max_time_ms, avg_time_ms, report_html
            ))
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def get_reports(self, connection_id: str = None) -> list[dict]:
        """获取所有历史分析报告列表 (不带大字段 report_html)"""
        conn = _get_connection()
        try:
            cursor = conn.cursor()
            if connection_id:
                cursor.execute("""
                    SELECT id, connection_id, log_file_name, log_type, total_queries, 
                           slow_queries, max_time_ms, avg_time_ms, created_at 
                    FROM gateway_log_reports
                    WHERE connection_id = %s
                    ORDER BY id DESC
                """, (connection_id,))
            else:
                cursor.execute("""
                    SELECT id, connection_id, log_file_name, log_type, total_queries, 
                           slow_queries, max_time_ms, avg_time_ms, created_at 
                    FROM gateway_log_reports
                    ORDER BY id DESC
                """)
            return list(cursor.fetchall())
        finally:
            conn.close()

    def get_report_detail(self, report_id: int) -> dict:
        """获取报告详情 (包含 HTML)"""
        conn = _get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, connection_id, log_file_name, log_type, total_queries, 
                       slow_queries, max_time_ms, avg_time_ms, report_html, created_at 
                FROM gateway_log_reports
                WHERE id = %s
            """, (report_id,))
            return cursor.fetchone()
        finally:
            conn.close()

    # ── 一次性报告票据（v1.6.2.2-UAT-O-15）─────────────────────

    def _purge_expired_tickets(self):
        """清理过期票据（调用方持锁）"""
        now = time.time()
        for tk in [k for k, v in _REPORT_TICKETS.items() if v[2] < now]:
            _REPORT_TICKETS.pop(tk, None)

    def create_report_ticket(self, report_id: int, username: str) -> str:
        """为指定报告签发短时一次性票据（仅登录后由签发接口调用）"""
        ticket = secrets.token_urlsafe(24)
        with _REPORT_TICKET_LOCK:
            self._purge_expired_tickets()
            _REPORT_TICKETS[ticket] = (int(report_id), username,
                                       time.time() + _REPORT_TICKET_TTL_SECONDS)
        return ticket

    def consume_report_ticket(self, ticket: str, report_id: int):
        """一次性消费票据：有效且报告 ID 匹配返回签发者用户名，否则 None。

        无论成败均即焚——失败重试/重放不能再次命中；过期票据同样作废。
        """
        if not ticket:
            return None
        with _REPORT_TICKET_LOCK:
            item = _REPORT_TICKETS.pop(ticket, None)
        if not item:
            return None
        bound_report_id, username, expire_at = item
        if time.time() > expire_at or int(bound_report_id) != int(report_id):
            return None
        return username


gateway_log_service = GatewayLogService()
