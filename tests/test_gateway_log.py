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
