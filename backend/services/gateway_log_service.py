"""G11 网关 (Proxy) 日志分析服务

提供网关日志文件上传、解析、分析报告生成与落库持久化的完整业务逻辑。
调用 backend/services/gateway_log_analysis/analyze_gateway_log.py 脚本。

v1.6.3.4 / D06 重构（REQ-04，DETAIL §6.5—§6.6）：
  · analyze_log 改为接受**受控文件路径**（不再接受完整 bytes），消除
    `await file.read()` 全量进内存的风险（§6.1 问题 3）；
  · 平台侧质量统计改为**流式逐行**（log_input.iter_log_lines），不全量 decode；
  · 子进程经 gateway_process.run_analysis_process 管理（sys.executable + 受控 argv
    + stdout/stderr 有界排空 + TERM/KILL 回收），消除裸 python / 120s 固定超时 /
    仅凭 HTML 存在判成功的缺陷（§6.1 问题 4）；
  · 落库改为 report_html + report_context_json + analysis_meta_json + request_id
    **同一事务**，失败 rollback，不留"成功但无正文"的历史记录（§6.6）；
  · 保存前按驱动 mogrify 后的实际报文长度 + max_allowed_packet 预检（§6.6）。
  · 本方法为同步阻塞，由 upload 路由放到线程池执行（不阻塞事件循环，§6.1 问题 2）。
"""
import json
import logging
import os
import re
import secrets
import shutil
import sys
import tempfile
import time
from datetime import datetime
from html import escape as html_escape
from pathlib import Path

from backend import config
from backend.services.database import _get_connection, _execute_sql, ensure_db

logger = logging.getLogger("tdsql.gateway_log")

# v1.6.2.2-UAT-O-17：混合输入跳过比例阈值（可配置）。超过阈值时拒绝生成报告，
# 避免只覆盖三分之一输入却按完整报告展示、健康结论无覆盖率背书。
_MAX_SKIP_RATIO = min(max(float(os.getenv("GATEWAY_MAX_SKIP_RATIO", "0.5")), 0.0), 1.0)
# 部分有效时的最大丢弃样例条数（写进响应与报告横幅，供用户定位原因）
_SKIP_SAMPLE_LIMIT = 5

# v1.6.2.2-UAT-O-15/O-22：报告 iframe 短时一次性票据（共享元数据库 + 原子消费）。
_REPORT_TICKET_TTL_SECONDS = 90

# analysis_meta_json 上限（§6.6：限 128 KiB，不存原始日志/未脱敏异常样例）
_META_MAX_BYTES = 128 * 1024


def _hash_ticket(ticket: str) -> str:
    import hashlib
    return hashlib.sha256(ticket.encode("utf-8")).hexdigest()


# ══════════════════════════════════════════════════════════════════
# v1.6.3.4 / D06：结构化错误（§6.6），由 upload 路由映射为 HTTP 状态码
# ══════════════════════════════════════════════════════════════════
class GatewayAnalysisError(Exception):
    """网关分析错误基类：携带 code/stage/retryable，供路由映射结构化响应。"""

    http_status = 500
    code = "GATEWAY_ANALYZER_FAILED"
    retryable = False

    def __init__(self, message: str, stage: str = "analyze"):
        super().__init__(message)
        self.message = message
        self.stage = stage


class GatewayInvalidLogError(GatewayAnalysisError):
    """零有效行 / 覆盖率过低 / 超长行 / 来源冲突（422）。"""
    http_status = 422
    code = "GATEWAY_INVALID_LOG"
    retryable = False


class GatewayAnalysisTimeoutError(GatewayAnalysisError):
    """子进程分析超时（504）：先回收进程，再返回可读错误。"""
    http_status = 504
    code = "GATEWAY_ANALYSIS_TIMEOUT"
    retryable = True


class GatewayOutputInvalidError(GatewayAnalysisError):
    """产物不完整：非零退出但有 HTML / summary 缺失或不匹配（500）。"""
    http_status = 500
    code = "GATEWAY_OUTPUT_INVALID"
    retryable = False


class GatewayReportStorageError(GatewayAnalysisError):
    """有效报文能力不足（503）：给元数据库参数核查指引。"""
    http_status = 503
    code = "GATEWAY_REPORT_STORAGE_LIMIT"
    retryable = False


class GatewayTempSpaceError(GatewayAnalysisError):
    """磁盘不足 / 写入 ENOSPC（507）。"""
    http_status = 507
    code = "GATEWAY_TEMP_SPACE_LOW"
    retryable = False


class GatewayLogService:
    """网关日志服务"""

    def analyze_log(
        self,
        connection_id: str,
        file_path: str,
        file_name: str,
        log_type: str = "interf",
        report_context=None,
        request_id: str = "",
        slow_threshold_ms: float = 1000.0,
    ) -> dict:
        """分析受控日志文件并落库（v1.6.3.4 / D06 重构，§6.5—§6.6）。

        Args:
            connection_id: 用户申明的日志来源连接（仅表示来源，标题补"上传日志关联实例"）
            file_path: upload 路由已转交到受控目录的日志文件路径（不再是 bytes）
            file_name: 原始文件名（仅安全显示用）
            log_type: interf | sql
            report_context: ReportContext（D01，冻结的实例来源；可为 None）
            request_id: 请求编号（落库供人工查历史，非身份凭证）
            slow_threshold_ms: 慢查询阈值

        本方法同步阻塞，由 upload 路由放到线程池执行（不阻塞事件循环）。
        """
        from backend.services.gateway_log_analysis.log_input import (
            iter_log_lines, count_file_bytes_and_sha256,
            LINE_ENCODING_ERROR, LINE_TOO_LONG)
        from backend.services.gateway_process import run_analysis_process

        cfg = config.gateway_upload_config()
        analysis_timeout = cfg["GATEWAY_ANALYSIS_TIMEOUT_SECONDS"]
        report_max_bytes = cfg["GATEWAY_REPORT_MAX_BYTES"]
        max_line_bytes = cfg["GATEWAY_MAX_LINE_BYTES"]
        flame_points = cfg["GATEWAY_FLAME_POINTS"]

        t_start = time.monotonic()
        logger.info("开始分析网关日志: file=%s, conn=%s, type=%s, req=%s",
                    file_name, connection_id, log_type, request_id)

        # ── 1) 流式质量统计（log_input 逐行，不全量进内存）──
        total_queries = 0
        slow_queries = 0
        max_time_ms = 0.0
        sum_time_ms = 0.0
        total_lines = 0
        empty_lines = 0
        nonempty_lines = 0
        invalid_format_lines = 0
        no_timecost_lines = 0
        numeric_error_lines = 0
        encoding_error_lines = 0
        too_long_lines = 0
        skip_samples: list = []
        _header_re = re.compile(
            r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \d+\]\s+\w+\s+(.*)")

        def _record_skip(reason: str, line_text: str):
            if len(skip_samples) < _SKIP_SAMPLE_LIMIT:
                skip_samples.append({"reason": reason, "line": (line_text or "")[:160]})

        for line, status in iter_log_lines(file_path, max_line_bytes):
            total_lines += 1
            if status == LINE_ENCODING_ERROR:
                encoding_error_lines += 1
                nonempty_lines += 1
                _record_skip("非法 UTF-8 编码（未静默丢字节）", "")
                continue
            if status == LINE_TOO_LONG:
                too_long_lines += 1
                nonempty_lines += 1
                _record_skip(f"单行超过 {max_line_bytes} 字节上限", "")
                continue
            stripped = line.strip()
            if not stripped:
                empty_lines += 1
                continue
            nonempty_lines += 1
            tc = self._extract_timecost(stripped, log_type, _header_re, _record_skip)
            if tc is None:
                continue
            # 计数分类：_extract_timecost 内部已记 skip 原因并返回 None
            if tc == "invalid_format":
                invalid_format_lines += 1
                continue
            if tc == "no_timecost":
                no_timecost_lines += 1
                continue
            if tc == "numeric_error":
                numeric_error_lines += 1
                continue
            # tc 为有效非负有限耗时
            total_queries += 1
            sum_time_ms += tc
            if tc > max_time_ms:
                max_time_ms = tc
            if tc >= slow_threshold_ms:
                slow_queries += 1

        avg_time_ms = (sum_time_ms / total_queries) if total_queries > 0 else 0.0
        skipped_lines = nonempty_lines - total_queries
        skip_ratio = (skipped_lines / nonempty_lines) if nonempty_lines else 0.0
        coverage_ratio = (total_queries / nonempty_lines) if nonempty_lines else 0.0

        # 零有效行拒绝（§6.5）：不得用行数冒充查询数、不得把空报告持久化为成功
        if total_queries == 0:
            raise GatewayInvalidLogError(
                f"未从日志中解析到任何有效查询记录（文件共 {total_lines} 行，"
                f"非空 {nonempty_lines} 行；格式不匹配 {invalid_format_lines}、"
                f"缺 timecost {no_timecost_lines}、数值错误 {numeric_error_lines}、"
                f"编码错误 {encoding_error_lines}、超长行 {too_long_lines}）。"
                f"请确认上传的是 {log_type} 类型的 TDSQL 网关日志，且内容未损坏。")
        # 跳过比例超阈值拒绝（§6.5）
        if skip_ratio > _MAX_SKIP_RATIO:
            raise GatewayInvalidLogError(
                f"有效行占比过低：非空 {nonempty_lines} 行中仅解析出 {total_queries} 行"
                f"（覆盖率 {coverage_ratio:.1%}，跳过 {skipped_lines} 行），"
                f"低于阈值 {1 - _MAX_SKIP_RATIO:.0%}。请确认日志类型与文件完整性后重试。")
        parse_status = "partial" if skipped_lines > 0 else "success"

        # ── 2) SHA-256 + 净字节（流式，不全量进内存）──
        input_bytes, input_sha256 = count_file_bytes_and_sha256(file_path)

        # ── 3) 子进程分析（gateway_process：sys.executable + 受控 argv + TERM/KILL）──
        script_path = (Path(__file__).parent / "gateway_log_analysis"
                       / "analyze_gateway_log.py")
        if not script_path.exists():
            raise GatewayOutputInvalidError(f"网关日志分析脚本未找到: {script_path}")
        work_dir = Path(file_path).parent
        report_path = work_dir / f"report_{os.getpid()}.html"
        # analyze_gateway_log.py 按 <type>_instance_<port>.<date>.<seq> 命名识别文件
        # 类型与实例；file_path 已是 upload 路由构造的受控文件名，直接 --files 传入。
        cmd = [sys.executable, str(script_path),
               "--files", str(file_path),
               "-o", str(report_path),
               "--log-types", log_type,
               "-f", "html"]
        env = dict(os.environ)
        _repo_root = str(Path(__file__).resolve().parents[2])
        env["PYTHONPATH"] = _repo_root + os.pathsep + env.get("PYTHONPATH", "")
        proc = run_analysis_process(cmd, timeout=analysis_timeout, env=env)

        # ── 4) 校验退出码/产物（§6.5：非零退出但留有 HTML 不能记成功）──
        if proc.timed_out:
            raise GatewayAnalysisTimeoutError(
                f"日志分析超过允许时长（{analysis_timeout}s），已回收子进程；"
                f"结果未落库，请核查资源或缩小日志范围后重试。")
        if not proc.cleanup_ok:
            raise GatewayAnalysisError(
                "分析子进程无法确认退出，转故障处置；结果未落库（不宣称已清理）。")
        if proc.returncode != 0:
            raise GatewayOutputInvalidError(
                f"网关日志分析失败（退出码 {proc.returncode}）；"
                f"stderr 尾部: {proc.stderr_tail[-300:] or '（空）'}")
        if not report_path.exists():
            raise GatewayOutputInvalidError("分析脚本未能生成报告文件（退出码 0 但无产物）")

        # ── 5) 读报告 + 字节上限 + 完整性校验 ──
        report_bytes = report_path.read_bytes()
        if len(report_bytes) > report_max_bytes:
            raise GatewayOutputInvalidError(
                f"报告 UTF-8 字节数 {len(report_bytes)} 超过上限 {report_max_bytes}")
        report_html = report_bytes.decode("utf-8", errors="replace")
        if not report_html.strip() or "</html>" not in report_html.lower():
            raise GatewayOutputInvalidError("报告为空或缺少完整文档结束标记，不落库")

        # partial 醒目告警横幅（v1.6.2.2-UAT-O-17 保留）
        if parse_status == "partial":
            report_html = self._inject_partial_banner(
                report_html, nonempty_lines, total_queries, coverage_ratio,
                skipped_lines, invalid_format_lines, no_timecost_lines,
                numeric_error_lines, encoding_error_lines, too_long_lines,
                skip_samples)

        # ── 6) analysis_meta_json（§6.6：限 128 KiB，不存原始日志/未脱敏样例）──
        analysis_meta = {
            "version": 1,
            "input_sha256": input_sha256,
            "input_bytes": input_bytes,
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
                "encoding_error_lines": encoding_error_lines,
                "too_long_lines": too_long_lines,
                "coverage_ratio": round(coverage_ratio, 4),
                # 脱敏跳过样例（行摘要 ≤160 字符，最多 _SKIP_SAMPLE_LIMIT 条），
                # 供前端定位原因；不含原始完整日志（§6.6）
                "skip_samples": skip_samples,
            },
            "visualization": {"flame_points": flame_points, "sampled": None},
            "analysis_truncated": False,
            "process": proc.to_dict(),
            "stage_duration_ms": {"analyze_total": int((time.monotonic() - t_start) * 1000)},
        }

        # ── 7) 事务落库（report_html + context + meta + request_id 同事务）──
        report_id = self._save_report(
            connection_id=connection_id, file_name=file_name, log_type=log_type,
            total_queries=total_queries, slow_queries=slow_queries,
            max_time_ms=max_time_ms, avg_time_ms=avg_time_ms,
            report_html=report_html, report_context=report_context,
            request_id=request_id, analysis_meta=analysis_meta)

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
            "status": parse_status,
            "request_id": request_id,
            "parse_quality": analysis_meta["parse_quality"],
        }

    @staticmethod
    def _extract_timecost(stripped: str, log_type: str, header_re, record_skip):
        """从一行日志提取 timecost（ms）。返回 float（有效）/ None（跳过）/
        字符串标记（invalid_format/no_timecost/numeric_error，供调用方分类计数）。

        有效行要求（§6.5）：匹配既有日志头、对应日志类型具有 timecost、
        数值有限且非负。NaN/Infinity/负耗时不进入均值和直方图。
        """
        m = header_re.match(stripped)
        if not m:
            record_skip("行首格式不匹配（非网关日志行）", stripped)
            return "invalid_format"
        body = m.group(2)
        if log_type == "interf":
            fields = {}
            for part in body.split("&"):
                if "=" in part:
                    k, _, v = part.partition("=")
                    fields[k] = v
            if "timecost" not in fields:
                record_skip("无 timecost 字段", stripped)
                return "no_timecost"
            raw = fields["timecost"]
        elif log_type == "sql":
            tc_m = re.search(r"timecost:([\d.]+)\(ms\)", body)
            if not tc_m:
                record_skip("无 timecost 字段", stripped)
                return "no_timecost"
            raw = tc_m.group(1)
        else:
            record_skip(f"不支持的日志类型: {log_type}", stripped)
            return "invalid_format"
        try:
            tc = float(raw)
        except (ValueError, TypeError):
            record_skip("timecost 数值非法", stripped)
            return "numeric_error"
        # NaN/Infinity/负耗时排除（§6.5）
        if tc != tc or tc in (float("inf"), float("-inf")) or tc < 0:
            record_skip("timecost 非有限或为负", stripped)
            return "numeric_error"
        return tc

    @staticmethod
    def _inject_partial_banner(report_html, nonempty_lines, total_queries,
                               coverage_ratio, skipped_lines, invalid_format_lines,
                               no_timecost_lines, numeric_error_lines,
                               encoding_error_lines, too_long_lines, skip_samples):
        """混合输入报告顶部注入醒目数据完整性告警（v1.6.2.2-UAT-O-17）。"""
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
            f'缺 timecost {no_timecost_lines}、数值错误 {numeric_error_lines}、'
            f'编码错误 {encoding_error_lines}、超长行 {too_long_lines}）。'
            '<b>本报告结论仅覆盖已解析部分，不代表全量输入。</b>'
            + (f'<div style="margin-top:8px;">跳过样例（前 {len(skip_samples)} 条）：'
               f'<ul style="margin:4px 0 0 18px;">{_sample_items}</ul></div>'
               if _sample_items else '')
            + '</div>')
        _anchor = '<div class="container">'
        if _anchor in report_html:
            return report_html.replace(_anchor, _anchor + _banner, 1)
        return _banner + report_html

    def _save_report(
        self, connection_id, file_name, log_type, total_queries, slow_queries,
        max_time_ms, avg_time_ms, report_html, report_context=None,
        request_id="", analysis_meta=None,
    ) -> int:
        """事务落库（§6.6）：report_html + report_context_json + analysis_meta_json
        + request_id 在同一事务插入；任何失败 rollback，不留"成功但无正文"的历史记录。

        保存前按驱动 mogrify 后的实际报文长度 + session max_allowed_packet 预检；
        权限/参数不足返回可操作错误，应用不得 SET GLOBAL。
        """
        # 序列化 context 与 meta
        context_json = None
        if report_context is not None:
            try:
                from backend.services.report_context import context_to_json_column
                context_json = context_to_json_column(report_context)
            except Exception:                                    # noqa: BLE001
                context_json = None
        meta_json = None
        if analysis_meta is not None:
            try:
                meta_json = json.dumps(analysis_meta, ensure_ascii=False)
                if len(meta_json.encode("utf-8")) > _META_MAX_BYTES:
                    logger.warning("analysis_meta_json 超过 %d 字节，裁剪 process 详情",
                                   _META_MAX_BYTES)
                    analysis_meta.pop("process", None)
                    meta_json = json.dumps(analysis_meta, ensure_ascii=False)
            except Exception:                                    # noqa: BLE001
                meta_json = None

        conn = _get_connection()
        try:
            # max_allowed_packet 预检（§6.6）：读元数据库 session 值，与报文比对
            self._precheck_packet_size(conn, report_html, context_json, meta_json)
            cursor = _execute_sql(conn, """
                INSERT INTO gateway_log_reports
                (connection_id, log_file_name, log_type, total_queries, slow_queries,
                 max_time_ms, avg_time_ms, report_html, report_context_json,
                 analysis_meta_json, request_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                connection_id, file_name, log_type, total_queries, slow_queries,
                max_time_ms, avg_time_ms, report_html, context_json,
                meta_json, (request_id or None),
            ))
            conn.commit()
            return cursor.lastrowid
        except GatewayReportStorageError:
            conn.rollback()
            raise
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _precheck_packet_size(conn, report_html, context_json, meta_json):
        """按实际编码后报文长度 + max_allowed_packet 预检（§6.6）。

        不是只比较 Python 字符数——按 UTF-8 编码后字节 + 协议余量估算。
        权限/参数不足返回可操作错误（503），应用不得 SET GLOBAL。
        """
        try:
            row = conn.execute("SELECT @@session.max_allowed_packet AS p").fetchone()
            max_packet = int(dict(row).get("p") or 0) if row else 0
        except Exception:                                        # noqa: BLE001
            max_packet = 0
        # 估算报文：各文本字段 UTF-8 字节 + 转义余量（引号/反斜杠/中文最坏 ×2）+ 固定开销
        payload = 0
        for s in (report_html, context_json, meta_json):
            if s:
                payload += len(s.encode("utf-8"))
        estimated = payload * 2 + 4096   # 转义上界 ×2 + 协议/列名余量
        if max_packet > 0 and estimated > max_packet:
            raise GatewayReportStorageError(
                f"报告报文估算 {estimated} 字节超过元数据库 session "
                f"max_allowed_packet={max_packet} 字节；请联系 DBA 核查元数据库参数"
                f"（不建议业务库全局修改），或缩小日志范围后重试。")

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

    # ── 一次性报告票据（v1.6.2.2-UAT-O-15/O-22，共享存储 + 原子消费）────────

    def create_report_ticket(self, report_id: int, username: str) -> str:
        """为指定报告签发短时一次性票据（仅登录后由签发接口调用）。

        v1.6.2.2-UAT-O-22：写入所有 worker 共享的元数据库，跨进程可消费；
        只存 SHA-256 哈希，不明文持久化；签发时顺带批量清理过期/已消费票据。
        """
        ensure_db()
        ticket = secrets.token_urlsafe(24)
        conn = _get_connection()
        try:
            # 批量清理：过期票据与已消费超过 1 小时的票据
            conn.execute(
                "DELETE FROM gateway_report_tickets WHERE expires_at < NOW() "
                "OR (consumed_at IS NOT NULL AND consumed_at < NOW() - INTERVAL 1 HOUR)")
            conn.execute(
                "INSERT INTO gateway_report_tickets "
                "(ticket_hash, report_id, username, expires_at) "
                "VALUES (?, ?, ?, DATE_ADD(NOW(), INTERVAL %s SECOND))"
                % _REPORT_TICKET_TTL_SECONDS,
                (_hash_ticket(ticket), int(report_id), username))
            conn.commit()
        finally:
            conn.close()
        return ticket

    def consume_report_ticket(self, ticket: str, report_id: int):
        """一次性消费票据：原子 UPDATE 成功返回签发者用户名，否则 None。

        单条 UPDATE 以"未消费且未过期且报告匹配"为条件，受影响行数=1 才是成功；
        先查再改无法保证一次性。错误报告、过期、重放、跨报告均统一失败（调用方 401），
        不泄露票据存在性。
        """
        if not ticket:
            return None
        conn = _get_connection()
        try:
            cur = conn.execute(
                "UPDATE gateway_report_tickets SET consumed_at = NOW() "
                "WHERE ticket_hash = ? AND report_id = ? "
                "AND consumed_at IS NULL AND expires_at > NOW()",
                (_hash_ticket(ticket), int(report_id)))
            conn.commit()
            if getattr(cur, "rowcount", 0) != 1:
                return None
            # 原子消费已成立（本进程唯一中标），读取签发者仅作身份回填
            row = conn.execute(
                "SELECT username FROM gateway_report_tickets WHERE ticket_hash = ?",
                (_hash_ticket(ticket),)).fetchone()
            return dict(row)["username"] if row else None
        finally:
            conn.close()


gateway_log_service = GatewayLogService()
