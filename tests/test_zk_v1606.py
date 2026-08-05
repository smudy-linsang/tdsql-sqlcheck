# -*- coding: utf-8 -*-
"""v1.6.0.6 A 复测问题修复回归用例（docs/v1.6.0.5_独立复测结论_A.md）。

A-P1-01 导入预检枚举对齐 ≥1 成功口径（富集关闭路径的反向鉴别）；
A-P1-02 monitor_port 允许 null、可选段全空也能保存；
A-P1-03 tdsql_inventory.sh gid 截取修复（计数与输出对账）；
A-P2-01 备 Proxy 失败降级可见（source=proxy_show_partial + 前端标记守卫）；
A-P3-01 主 Proxy 真正前移（两处实现）。
"""
import shutil
import subprocess
from pathlib import Path

import pymysql
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services import zk_scan_enrich_service as enrich
from backend.services.database import _get_connection
from backend.services.zk_connection_import_service import (
    ImportCredentials, MonitorCredentials, zk_connection_import_service)

client = TestClient(app)
_ROOT = Path(__file__).resolve().parents[1]


class _FakeCursor:
    def __init__(self, dbs):
        self._dbs = dbs

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        pass

    def fetchall(self):
        return [{"Database": d} for d in self._dbs]


class _FakeConn:
    def __init__(self, dbs):
        self._dbs = dbs

    def cursor(self):
        return _FakeCursor(self._dbs)

    def close(self):
        pass


def _instance_without_enrich():
    """模拟富集关闭：发现项不带 business_dbs，预检必落自带枚举兜底。"""
    return {
        "instance_id": "set_v1606", "instance_kind": "noshard",
        "instance_type": "centralized", "host": "10.0.0.1", "port": 15001,
        "proxy_list": "10.0.0.1:15001;10.0.0.2:15001", "set_ids": ["set_v1606"],
        "is_mock": False,
    }


def test_ap101_import_preview_survives_dead_backup_proxy(monkeypatch):
    """A-P1-01 反向鉴别：富集关闭 + 双 Proxy 其一不可达 → 预检仍产出候选行。"""
    def fake_connect(host, port, **kw):
        if host == "10.0.0.2":
            raise pymysql.err.OperationalError(2003, "Can't connect")
        return _FakeConn(["biz_a", "biz_b"])
    monkeypatch.setattr(pymysql, "connect", fake_connect)
    rows = zk_connection_import_service.build_preview(
        [_instance_without_enrich()],
        ImportCredentials("u", "p"),
        MonitorCredentials("mon", 15001, "mu", "mp", "tdsqlpcloud_monitor"),
        name_overrides={"set_v1606": "回归实例"})
    ready = [r for r in rows if r["status"] == "ready"]
    assert [r["database"] for r in ready] == ["biz_a", "biz_b"], f"主活备死应仍产出候选: {rows}"
    # A-P2-01：降级必须可见
    assert all(r["databases_source"] == "proxy_show_partial" for r in ready)


def test_ap101_import_preview_all_proxies_down_no_fake_data(monkeypatch):
    """A-P1-01：全部 Proxy 不可达 → 逐实例 NO_AVAILABLE_PROXY，不造假数据。"""
    def fake_connect(host, port, **kw):
        raise pymysql.err.OperationalError(2003, "Can't connect")
    monkeypatch.setattr(pymysql, "connect", fake_connect)
    rows = zk_connection_import_service.build_preview(
        [_instance_without_enrich()],
        ImportCredentials("u", "p"),
        MonitorCredentials("mon", 15001, "mu", "mp", "tdsqlpcloud_monitor"),
        name_overrides={"set_v1606": "回归实例"})
    assert len(rows) == 1 and rows[0]["status"] == "error"
    assert rows[0]["failure_code"] == "NO_AVAILABLE_PROXY"
    # 名称已解析的部分仍保留，便于 UI 展示与手工兜底
    assert rows[0]["resolved_instance_name"] == "回归实例"


def test_ap102_config_accepts_null_monitor_port_and_empty_optional_block():
    """A-P1-02：只填三项必填、可选段留空（端口显式 null）→ 必须 200。"""
    conn = _get_connection()
    try:
        conn.execute("DELETE FROM zk_discovery_config WHERE config_id=1")
        conn.commit()
    finally:
        conn.close()
    payload = {
        "servers": "zk-a.example:2118",
        "auth_username": "zk_reader",
        "auth_password": "v1606-zk-secret",
        "monitor_port": None,  # 前端 el-input-number 空值形态
    }
    resp = client.put("/api/v1/tdsql/discover/config", json=payload)
    assert resp.status_code == 200, f"可选段留空必须能保存: {resp.status_code} {resp.text}"
    # 清理，避免影响其他 ZK 配置用例
    conn = _get_connection()
    try:
        conn.execute("DELETE FROM zk_discovery_config WHERE config_id=1")
        conn.commit()
    finally:
        conn.close()


def test_ap102_frontend_renders_field_level_422_detail():
    """A-P1-02 结构守卫：responseMessage 必须能渲染 FastAPI 数组型 detail。"""
    js = (_ROOT / "frontend" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert "Array.isArray(value)" in js, "responseMessage 缺少数组型 detail 分支"
    assert "first.loc" in js


def _find_bash():
    """找真正可用的 bash：Git 自带优先；Windows 的 system32\bash.EXE 是
    WSL 占位（未装 WSL 时必失败），探测不过则降级为结构守卫。"""
    candidates = [r"C:\Program Files\Git\bin\bash.exe",
                  r"C:\Program Files (x86)\Git\bin\bash.exe"]
    which = shutil.which("bash")
    if which:
        candidates.append(which)
    for path in candidates:
        if not Path(path).exists():
            continue
        try:
            probe = subprocess.run([path, "-c", "echo ok"],
                                   capture_output=True, text=True, timeout=10)
        except Exception:
            continue
        if probe.returncode == 0 and "ok" in probe.stdout:
            return path
    return None


def test_ap103_inventory_gid_parsing_count_reconciliation(tmp_path):
    """A-P1-03 计数对账：groupshard 输入条数 == 记录条数，且 gid 不混入其他字段。

    从 tdsql_inventory.sh 原样抽取 groupshard 记录生成块执行（防脚本与用例漂移）；
    无 bash 环境时降级为结构守卫（断言修复写法存在、旧错误写法消失），不跳过。
    """
    script = (_ROOT / "deploy" / "tdsql_inventory.sh").read_text(encoding="utf-8")
    assert 'gid="${entry%%|*}"' in script, "gid 必须用 %%|* 取首个分隔符之前"
    assert 'gid="${entry%|*}"' not in script.replace('gid="${entry%%|*}"', ""), \
        "旧错误写法 ${entry%|*} 不得残留"
    assert "inventory 记录字段数异常" in script, "Python 侧字段数异常必须打 WARN"

    bash = _find_bash()
    if not bash:
        return  # 结构守卫已生效；有 bash 时继续做行为对账
    lines = script.splitlines()
    start = next(i for i, l in enumerate(lines) if "while IFS= read -r entry; do" in l
                 and "group" in "".join(lines[i:i + 10]))
    end = next(i for i in range(start, len(lines)) if lines[i].strip() == "${_group_repr_set}")
    block = "\n".join(lines[start:end + 2])  # 含 done <<EOF / 数据行 / EOF
    records = tmp_path / "records.txt"
    harness = (
        "set -e\n"
        f"ZK_ROOT=/tdsqlzk\n_inventory_records={records.as_posix()}\n"
        "_group_repr_set='group_uat_1|set_uat_d1|set_uat_d1;set_uat_d2\n"
        "group_uat_2|set_uat_d3|set_uat_d3'\n"
        f"{block}\n"
    )
    subprocess.run([bash, "-c", harness], check=True, capture_output=True, text=True)
    out_lines = [l for l in records.read_text(encoding="utf-8").splitlines() if l.strip()]
    # 计数对账：2 条 group 输入 → 2 条 groupshard 记录（旧 bug 下记录为 7 字段被下游丢弃）
    assert len(out_lines) == 2
    for line in out_lines:
        parts = line.split("|")
        assert len(parts) == 5, f"记录必须恰好 5 字段: {line}"
        assert parts[0] == "groupshard"
        assert "|" not in parts[1], f"gid 不得混入其他字段: {parts[1]}"
    assert [l.split("|")[1] for l in out_lines] == ["group_uat_1", "group_uat_2"]


def test_ap201_enrich_degradation_marked_and_visible():
    """A-P2-01：富集侧 failed>0 一律 partial + 前端业务库列有降级标记守卫。"""
    html = (_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    assert html.count("部分 Proxy") >= 2, "发现列表与导入预览的业务库列都应有降级标记"
    assert "databases_source" in html, "前端必须消费 databases_source 字段"


def test_ap301_primary_proxy_moved_to_front_both_implementations():
    """A-P3-01：proxy_list 已含主 Proxy 时也必须前移（两处实现口径一致）。"""
    item = {
        "host": "10.0.0.9", "port": 15005,
        "proxy_list": "10.0.0.7:15005;10.0.0.9:15005;10.0.0.8:15005",
    }
    assert enrich._proxy_endpoints(item)[0] == ("10.0.0.9", 15005)
    assert zk_connection_import_service._proxy_endpoints(item)[0] == ("10.0.0.9", 15005)
    # 不重不漏：前移不得产生重复端点
    got = zk_connection_import_service._proxy_endpoints(item)
    assert len(got) == 3 and len(set(got)) == 3


def test_ap202_bad_monitor_endpoint_yields_per_row_error_not_500(monkeypatch):
    """A-P2-02：MonitorDB 端口不通/口令错 → 逐行可读失败，不得裸抛掀翻整批。

    旧行为：OperationalError 逃出 build_preview → 接口兜底 500
    "生成 ZK 导入预览失败"，无任何指向。build_preview 不抛即意味着
    接口层不会走到 500 兜底分支。
    """
    for errno_, scenario in ((2003, "端口不通"), (1045, "口令错")):
        def fake_connect(host, port, **kw):
            raise pymysql.err.OperationalError(errno_, f"{scenario}")
        monkeypatch.setattr(pymysql, "connect", fake_connect)
        rows = zk_connection_import_service.build_preview(
            [_instance_without_enrich()],
            ImportCredentials("u", "p"),
            MonitorCredentials("10.9.9.9", 15999, "mu", "wrong", "tdsqlpcloud_monitor"))
        assert len(rows) == 1 and rows[0]["status"] == "error", f"{scenario}应逐行失败而非整批炸掉"
        assert rows[0]["failure_code"] == "MONITOR_CONNECT_FAILED"
        assert "10.9.9.9:15999" in rows[0]["failure_detail"], "失败提示必须带端点指向"
        assert "wrong" not in rows[0]["failure_detail"], "提示不得回显口令"


def _put_config(payload):
    base = {
        "servers": "zk-a.example:2118",
        "auth_username": "zk_reader",
        "auth_password": "v1606-zk-secret",
    }
    base.update(payload)
    return client.put("/api/v1/tdsql/discover/config", json=base)


def _config_row():
    conn = _get_connection()
    try:
        return conn.execute(
            "SELECT * FROM zk_discovery_config WHERE config_id=1").fetchone()
    finally:
        conn.close()


def test_ap302_saved_business_credentials_can_be_cleared():
    """A-P3-02：已存业务凭据必须能被清除（凭据轮换/人员离场硬要求）。"""
    conn = _get_connection()
    try:
        conn.execute("DELETE FROM zk_discovery_config WHERE config_id=1")
        conn.commit()
    finally:
        conn.close()
    try:
        # ① 先存上业务凭据
        assert _put_config({"business_username": "bizuser",
                            "business_password": "bizpw"}).status_code == 200
        assert _config_row()["business_password_encrypted"]
        # ② 用户名口令同时留空 → 必须 200 且密文被清空（旧逻辑在此 400 死路）
        resp = _put_config({"business_username": "", "business_password": ""})
        assert resp.status_code == 200, f"清空业务凭据被拒: {resp.status_code} {resp.text}"
        row = _config_row()
        assert row["business_username"] == "" and row["business_password_encrypted"] == ""
        # ③ MonitorDB 整段清空 → 口令密文一并清除
        assert _put_config({"monitor_host": "mon.example", "monitor_port": 15001,
                            "monitor_user": "mu", "monitor_password": "mpw",
                            "monitor_db": "mdb"}).status_code == 200
        assert _config_row()["monitor_password_encrypted"]
        resp = _put_config({"monitor_host": "", "monitor_port": 0, "monitor_user": "",
                            "monitor_password": "", "monitor_db": ""})
        assert resp.status_code == 200, resp.text
        assert _config_row()["monitor_password_encrypted"] == ""
        # ④ 反向鉴别：保留用户名、口令留空 → 仍继承旧密文（原设计未被破坏）
        assert _put_config({"business_username": "bizuser",
                            "business_password": "keepme"}).status_code == 200
        before = _config_row()["business_password_encrypted"]
        assert _put_config({"business_username": "bizuser",
                            "business_password": ""}).status_code == 200
        assert _config_row()["business_password_encrypted"] == before
    finally:
        conn = _get_connection()
        try:
            conn.execute("DELETE FROM zk_discovery_config WHERE config_id=1")
            conn.commit()
        finally:
            conn.close()
