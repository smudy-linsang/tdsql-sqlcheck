"""G11 网关 (Proxy) 日志分析服务

提供网关日志文件上传、解析、分析报告生成与落库持久化的完整业务逻辑。
调用 backend/services/gateway_log_analysis/analyze_gateway_log.py 脚本。
"""
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from backend.services.database import _get_connection, _execute_sql

logger = logging.getLogger("tdsql.gateway_log")


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

        # 分行处理（支持 \n 或 \r\n）
        lines = file_content.decode("utf-8", errors="ignore").splitlines()

        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # interf 日志解析
            if log_type == "interf":
                # [2026-02-26 00:00:00 002408] INFO topic=...
                m = re.match(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \d+\]\s+\w+\s+(.*)", line)
                if m:
                    body = m.group(2)
                    fields = {}
                    for part in body.split("&"):
                        if "=" in part:
                            k, _, v = part.partition("=")
                            fields[k] = v
                    
                    if "timecost" in fields:
                        try:
                            tc = float(fields["timecost"])
                            total_queries += 1
                            sum_time_ms += tc
                            if tc > max_time_ms:
                                max_time_ms = tc
                            if tc >= slow_threshold_ms:
                                slow_queries += 1
                        except ValueError:
                            pass
            elif log_type == "sql":
                # sql_instance 日志解析
                m = re.match(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \d+\]\s+\w+\s+(.*)", line)
                if m:
                    body = m.group(2)
                    tc_m = re.search(r"timecost:([\d.]+)\(ms\)", body)
                    if tc_m:
                        try:
                            tc = float(tc_m.group(1))
                            total_queries += 1
                            sum_time_ms += tc
                            if tc > max_time_ms:
                                max_time_ms = tc
                            if tc >= slow_threshold_ms:
                                slow_queries += 1
                        except ValueError:
                            pass

        avg_time_ms = (sum_time_ms / total_queries) if total_queries > 0 else 0.0

        # 如果没有提取到任何 query，但文件有内容，total_queries 可默认为行数
        if total_queries == 0 and len(lines) > 0:
            total_queries = len(lines)

        # 2) 写入临时文件，供 analyze_gateway_log.py 读取
        temp_dir = Path(tempfile.gettempdir()) / "tdsql_log_analysis"
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        # 保持原有后缀
        suffix = Path(file_name).suffix or ".log"
        temp_log_file = temp_dir / f"uploaded_{os.getpid()}{suffix}"
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
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            
            # 注意: 即使脚本可能有一些警告，只要生成了 HTML 就视为成功
            if not temp_html_file.exists():
                logger.error(f"分析脚本未能生成报告文件. stdout={res.stdout}, stderr={res.stderr}")
                raise RuntimeError(f"网关日志分析失败: {res.stderr or '未能生成报告'}")

            # 4) 读取生成的 HTML
            report_html = temp_html_file.read_text(encoding="utf-8", errors="replace")

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
                "report_html": report_html
            }

        finally:
            # 清理临时文件
            if temp_log_file.exists():
                try:
                    temp_log_file.unlink()
                except Exception:
                    pass
            if temp_html_file.exists():
                try:
                    temp_html_file.unlink()
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


gateway_log_service = GatewayLogService()
