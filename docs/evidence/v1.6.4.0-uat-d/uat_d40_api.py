"""智能体D / v1.6.4.0 UAT 公共夹具：登录、API 调用、证据落盘。"""
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _boot  # noqa: F401,E402  （补入用户级 site-packages）

import httpx  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RUNTIME = ROOT / "data/reports/uat_d_1640"
WEB = "http://127.0.0.1:8025"
GW_HOST = os.getenv("UAT_D40_GW_HOST", "172.16.4.16")
EVID = HERE


def creds() -> dict:
    return json.loads((RUNTIME / "accounts.json").read_text(encoding="utf-8"))


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def rid() -> str:
    return uuid.uuid4().hex


def save(name: str, data) -> Path:
    p = EVID / f"{name}.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str),
                 encoding="utf-8")
    return p


class Api:
    """带 Bearer 的同步客户端。"""

    def __init__(self, username: str, password: str = None):
        self.username = username
        self.password = password or creds()[username]
        self.token = None
        self.client = httpx.Client(base_url=WEB, timeout=30.0,
                                   follow_redirects=False)

    def login(self):
        r = self.client.post("/api/v1/auth/login",
                             json={"username": self.username,
                                   "password": self.password})
        # N-02：JWT iat 必须严格晚于 subject.created_at，同秒登录会被拒
        if r.status_code != 200:
            raise RuntimeError(f"登录失败 {r.status_code}: {r.text[:300]}")
        body = r.json()
        self.token = body.get("access_token") or body.get("token")
        if not self.token:
            raise RuntimeError(f"登录响应无 token: {body}")
        self.client.headers["Authorization"] = f"Bearer {self.token}"
        return body

    def relogin_after(self, seconds: int = 2):
        time.sleep(seconds)
        return self.login()

    def req(self, method: str, path: str, **kw):
        r = self.client.request(method, path, **kw)
        out = {"method": method, "path": path, "status": r.status_code,
               "utc": utc()}
        try:
            out["body"] = r.json()
        except Exception:
            out["body_text"] = r.text[:2000]
        out["headers"] = {k.lower(): v for k, v in r.headers.items()
                          if k.lower() in ("cache-control", "content-disposition",
                                           "content-security-policy",
                                           "retry-after")}
        return out

    def get(self, p, **kw):
        return self.req("GET", p, **kw)

    def post(self, p, **kw):
        return self.req("POST", p, **kw)

    def put(self, p, **kw):
        return self.req("PUT", p, **kw)

    def close(self):
        self.client.close()


def gateway_events(port: int = None) -> list[dict]:
    f = RUNTIME / "gateway-requests.jsonl"
    if not f.exists():
        return []
    rows = [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x]
    if port:
        rows = [r for r in rows if r.get("port") == port]
    return rows


def set_gw_mode(port: int, mode: str) -> None:
    (RUNTIME / f"gw-mode-{port}.txt").write_text(mode, encoding="utf-8")


def wait_turn(api: Api, turn_id: str, timeout: float = 120.0) -> dict:
    """轮询到终态；返回最后一次状态响应。"""
    term = {"SUCCEEDED", "LOCAL_ONLY", "DEGRADED", "FAILED", "CANCELLED",
            "INTERRUPTED"}
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        last = api.get(f"/api/v1/copilot/turns/{turn_id}")
        st = (last.get("body") or {}).get("state")
        if st in term:
            return last
        time.sleep(0.5)
    return last or {"status": None, "body": {"state": "TIMEOUT_LOCAL"}}
