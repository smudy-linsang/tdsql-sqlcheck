"""G13 运维工具箱单元与集成测试"""
import pytest
from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)


def test_toolkit_scripts_list():
    """测试获取运维脚本列表 API"""
    resp = client.get("/api/v1/toolkit/scripts")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["id"] == "disk_perf_test"
    assert data[1]["id"] == "sshpass_pack"


def test_toolkit_download_ok():
    """测试合法下载脚本"""
    resp = client.get("/api/v1/toolkit/download?file_path=disk_performance_test/disk_perf_test.sh")
    assert resp.status_code == 200
    assert len(resp.content) > 0


def test_toolkit_download_traversal():
    """测试路径穿越漏洞防范"""
    resp = client.get("/api/v1/toolkit/download?file_path=../../main.py")
    assert resp.status_code == 403
    assert resp.json()["detail"] == "非法访问"


def test_toolkit_download_not_exist():
    """测试不存在的脚本文件返回 404"""
    resp = client.get("/api/v1/toolkit/download?file_path=disk_performance_test/not_exist.sh")
    assert resp.status_code == 404
