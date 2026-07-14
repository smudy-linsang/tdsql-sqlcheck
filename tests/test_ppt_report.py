"""G12 PPT报告与大屏单元与集成测试"""
import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.services.ppt_report_service import ppt_report_service

client = TestClient(app)


def test_ppt_report_service_data():
    """测试 PPT 汇报报告的数据组装结构"""
    data = ppt_report_service.generate_report_data("test_conn")
    assert "meta" in data
    assert "modules" in data
    assert "daily_inspection" in data["modules"]
    assert "count_table_rows" in data["modules"]
    assert "index_analysis" in data["modules"]
    assert "sql_analysis" in data["modules"]


def test_ppt_generate_api():
    """测试 PDF 一键导出下载 API 接口"""
    # 模拟导出 API
    resp = client.post("/api/v1/ppt-report/generate?connection_id=test_conn")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert len(resp.content) > 0


def test_ppt_dashboard_api():
    """测试总览大屏数据 API"""
    resp = client.get("/api/v1/ppt-report/dashboard?connection_id=test_conn")
    assert resp.status_code == 200
    data = resp.json()
    assert "score" in data
    assert "total_alerts" in data
    assert "inspection" in data
    assert "index" in data
