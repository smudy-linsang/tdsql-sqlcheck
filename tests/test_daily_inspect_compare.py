"""G4 每日巡检与差异比对分析单元与集成测试"""
import pytest
from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)

from backend.services.connection_registry import registry
from backend.services.tdsql_connector import TDSQLConnectionConfig

registry.register("test_conn", TDSQLConnectionConfig(host="127.0.0.1", port=3306, user="root", password="", database="test"), validate=False)


def test_daily_inspect_run_api():
    """测试手动发起每日巡检指标采集"""
    body = {
        "connection_id": "test_conn",
        "inspect_date": "2026-07-14"
    }
    resp = client.post("/api/v1/daily-inspect/run", json=body)
    assert resp.status_code == 200
    data = resp.json()
    assert data["inspect_date"] == "2026-07-14"
    assert data["node_count"] > 0
    assert len(data["rows"]) > 0


def test_daily_inspect_compare_api():
    """测试双日及多日比对 API"""
    # 先采集两天的数据以保证有数据可以比对
    client.post("/api/v1/daily-inspect/run", json={"connection_id": "test_conn", "inspect_date": "2026-07-13"})
    client.post("/api/v1/daily-inspect/run", json={"connection_id": "test_conn", "inspect_date": "2026-07-14"})

    # 1. 双日对比
    resp = client.get("/api/v1/daily-inspect/compare?connection_id=test_conn&date1=2026-07-13&date2=2026-07-14")
    assert resp.status_code == 200
    data = resp.json()
    assert data["date1"] == "2026-07-13"
    assert data["date2"] == "2026-07-14"
    assert "instance_diffs" in data
    assert "server_diffs" in data
    assert len(data["instance_diffs"]) > 0

    # 2. 多日对比
    resp_multi = client.get("/api/v1/daily-inspect/compare?connection_id=test_conn&dates=2026-07-13,2026-07-14")
    assert resp_multi.status_code == 200
    data_multi = resp_multi.json()
    assert "dates" in data_multi
    assert "instance_trend" in data_multi
    assert "server_trend" in data_multi


def test_daily_inspect_compare_html_api():
    """测试比对报告 HTML 生成接口"""
    # 采集数据
    client.post("/api/v1/daily-inspect/run", json={"connection_id": "test_conn", "inspect_date": "2026-07-13"})
    client.post("/api/v1/daily-inspect/run", json={"connection_id": "test_conn", "inspect_date": "2026-07-14"})

    resp = client.get("/api/v1/daily-inspect/compare/html?connection_id=test_conn&date1=2026-07-13&date2=2026-07-14")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    html_text = resp.text
    assert "TDSQL 巡检深度对比分析报告" in html_text
    assert "Chart" in html_text
    assert "trendChart" in html_text


def test_daily_inspect_trend_api():
    """测试趋势查询 API"""
    client.post("/api/v1/daily-inspect/run", json={"connection_id": "test_conn", "inspect_date": "2026-07-14"})
    
    resp = client.get("/api/v1/daily-inspect/trend?connection_id=test_conn&date_from=2026-07-13&date_to=2026-07-15&metrics=cpu_peak,slow_query")
    assert resp.status_code == 200
    data = resp.json()
    assert "metrics" in data
    assert "series" in data
    assert "cpu_peak" in data["series"]
