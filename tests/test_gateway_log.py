"""G11 网关日志分析单元与集成测试"""
import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.services.gateway_log_service import gateway_log_service
from backend.services.database import _get_connection

client = TestClient(app)

SAMPLE_INTERF_LOG = (
    "[2026-02-26 00:00:01 12345] INFO topic=test&timecost=12.5&sql=select * from t1&db=biz&user=root&host=127.0.0.1\n"
    "[2026-02-26 00:00:02 12346] INFO topic=test&timecost=1500.2&sql=select * from t2&db=biz&user=root&host=127.0.0.1\n"
)


def test_gateway_log_service():
    """测试网关日志服务分析与解析统计功能"""
    res = gateway_log_service.analyze_log(
        connection_id="test_conn",
        file_name="interf_test.log",
        file_content=SAMPLE_INTERF_LOG.encode("utf-8")
    )
    assert res["total_queries"] == 2
    assert res["slow_queries"] == 1
    assert res["max_time_ms"] == 1500.2
    assert res["avg_time_ms"] == (12.5 + 1500.2) / 2
    assert "report_html" in res


def test_gateway_log_upload_api():
    """测试网关日志上传与分析 API"""
    files = {"file": ("interf_test.log", SAMPLE_INTERF_LOG.encode("utf-8"), "text/plain")}
    data = {"connection_id": "test_conn", "log_type": "interf"}
    resp = client.post("/api/v1/gateway-log/upload", data=data, files=files)
    assert resp.status_code == 200
    res = resp.json()
    assert res["status"] == "success"
    assert res["total_queries"] == 2
    assert res["slow_queries"] == 1

    report_id = res["report_id"]

    # 验证获取报告列表 API
    resp_list = client.get("/api/v1/gateway-log/reports?connection_id=test_conn")
    assert resp_list.status_code == 200
    items = resp_list.json()
    assert len(items) > 0
    assert items[0]["id"] == report_id

    # 验证获取报告 HTML API
    resp_html = client.get(f"/api/v1/gateway-log/reports/{report_id}/html")
    assert resp_html.status_code == 200
    assert "html" in resp_html.text.lower()

    # v1.6.2.2-UAT-O-15：nonce 制 CSP（无 unsafe-inline）、无 X-Frame-Options
    csp = resp_html.headers.get("Content-Security-Policy", "")
    assert "script-src 'nonce-" in csp, "脚本必须由响应级随机 nonce 放行"
    assert "unsafe-inline" not in csp.split("style-src")[0], "script-src 不得含 unsafe-inline"
    assert "frame-ancestors 'self'" in csp
    assert "X-Frame-Options" not in resp_html.headers, \
        "不得发送与不透明源 sandbox 冲突的 X-Frame-Options"
    # 裸 <script> 必须全部 nonce 化
    import re as _re
    assert not _re.findall(r"<script>", resp_html.text), "存在未 nonce 化的裸 script"


def test_gateway_report_ticket_api():
    """v1.6.2.2-UAT-O-15：一次性报告票据签发与消费（iframe 不再携带长期令牌）"""
    files = {"file": ("interf_ticket.log", SAMPLE_INTERF_LOG.encode("utf-8"), "text/plain")}
    data = {"connection_id": "test_conn_ticket", "log_type": "interf"}
    resp = client.post("/api/v1/gateway-log/upload", data=data, files=files)
    assert resp.status_code == 200
    report_id = resp.json()["report_id"]

    # 签发票据（头部令牌鉴权路径，测试环境认证关闭时以匿名管理员放行）
    # v1.6.2.2-UAT-O-22：签发是产生状态的操作，必须为 POST
    resp_tk = client.post(f"/api/v1/gateway-log/reports/{report_id}/ticket")
    assert resp_tk.status_code == 200
    ticket = resp_tk.json()["ticket"]
    assert ticket and len(ticket) >= 20

    # 票据消费后即可访问 /html；重放不得再次命中（用后即焚）
    assert gateway_log_service.consume_report_ticket(ticket, report_id) is not None
    assert gateway_log_service.consume_report_ticket(ticket, report_id) is None

    # 报告不存在时不得签发票据
    resp_404 = client.post("/api/v1/gateway-log/reports/999999/ticket")
    assert resp_404.status_code == 404


# v1.6.2.2-UAT-O-17：混合输入不得静默丢行并按完整报告展示。
# 4 行有效 + 3 行无效（格式/缺字段/数值各一）：覆盖率 57.1% > 50% 阈值 → partial。
_MIXED_LOG = (
    "[2026-02-26 00:00:01 12345] INFO topic=test&timecost=12.5&sql=select 1&db=biz\n"
    "[2026-02-26 00:00:02 12346] INFO topic=test&timecost=13.5&sql=select 2&db=biz\n"
    "[2026-02-26 00:00:03 12347] INFO topic=test&timecost=14.5&sql=select 3&db=biz\n"
    "[2026-02-26 00:00:04 12348] INFO topic=test&timecost=15.5&sql=select 4&db=biz\n"
    "this line is not a gateway log entry at all\n"
    "[2026-02-26 00:00:05 12349] INFO topic=test&sql=select 5&db=biz\n"
    "[2026-02-26 00:00:06 12350] INFO topic=test&timecost=abc&sql=select 6&db=biz\n"
)


def test_mixed_input_reports_coverage_not_silent():
    """混合输入：返回 partial 状态 + 覆盖率统计，报告携带数据完整性告警"""
    files = {"file": ("interf_mixed.log", _MIXED_LOG.encode("utf-8"), "text/plain")}
    data = {"connection_id": "test_conn_mixed", "log_type": "interf"}
    resp = client.post("/api/v1/gateway-log/upload", data=data, files=files)
    assert resp.status_code == 200
    res = resp.json()
    assert res["status"] == "partial", "混合输入必须标记 partial"
    q = res["parse_quality"]
    assert q["nonempty_lines"] == 7
    assert q["parsed_lines"] == 4
    assert q["skipped_lines"] == 3
    assert q["invalid_format_lines"] == 1
    assert q["no_timecost_lines"] == 1
    assert q["numeric_error_lines"] == 1
    assert q["coverage_ratio"] == round(4 / 7, 4)
    assert q["skip_samples"], "必须给出跳过样例供用户定位原因"
    # 报告正文必须携带醒目数据完整性告警（含覆盖率）
    detail = client.get(f"/api/v1/gateway-log/reports/{res['report_id']}").json()
    html_text = detail["report_html"]
    assert "数据完整性告警" in html_text
    assert "覆盖率 57.1%" in html_text
    assert "不代表全量输入" in html_text


def test_over_threshold_input_rejected():
    """有效行占比低于阈值（默认 50%）时必须拒绝生成报告（422）"""
    lines = ["[2026-02-26 00:00:01 1] INFO topic=t&timecost=5&sql=select 1&db=b"]
    lines += [f"garbage line {i}" for i in range(9)]
    content = "\n".join(lines) + "\n"
    files = {"file": ("interf_lowcov.log", content.encode("utf-8"), "text/plain")}
    data = {"connection_id": "test_conn_lowcov", "log_type": "interf"}
    resp = client.post("/api/v1/gateway-log/upload", data=data, files=files)
    assert resp.status_code == 422, "覆盖率 10% 低于 50% 阈值，必须拒绝"
    assert "覆盖率" in resp.json()["detail"]


def test_all_invalid_input_rejected_with_breakdown():
    """全无效输入：422 且错误信息携带分类原因统计"""
    content = "garbage a\ngarbage b\n"
    files = {"file": ("interf_allbad.log", content.encode("utf-8"), "text/plain")}
    data = {"connection_id": "test_conn_allbad", "log_type": "interf"}
    resp = client.post("/api/v1/gateway-log/upload", data=data, files=files)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "格式不匹配" in detail
