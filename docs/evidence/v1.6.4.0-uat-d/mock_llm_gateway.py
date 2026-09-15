"""智能体D / v1.6.4.0 UAT：受控模型网关（HTTPS，OpenAI-compatible chat completions）。

用途（对应 GATE-v1.6.4.0 §5 第 1 条）：
  "真实模型接入后，projection_mode 与实际出站载荷的一致性抽检"。
  本机无获准内网模型网关，故以**同协议受控网关**承载：runner 仍走真实
  policy 端点解析 → 真实 TLS 握手 → 真实 httpx 出站，唯一被替换的是
  "供应商那一段"；本脚本把**实际出站请求体逐字节**落盘，作为投影一致性的
  第一手证据。证据边界已在报告中写明。

监听：默认 172.16.4.16:8443（主）与 :8444（备用），TLS 证书由 prepare_uat_d40.py 生成。
行为由控制文件 <runtime>/gw-mode-<port>.txt 决定（默认 ok）：
  ok | rate429 | unavail503 | auth401 | badjson | length | slow | refuse
"""

import json
import os
import ssl
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNTIME = Path(os.getenv("UAT_D40_RUNTIME", str(HERE / "runtime")))

_LOCK = threading.Lock()

# 与 CP-SYSTEM-1 §8.1 对齐的最小合规答案（服务端仍会做结构/引用/断言三层校验）
ANSWER_TEMPLATE = {
    "schema_version": 1,
    "summary": "已根据本轮证据整理要点；这是受控网关返回的合成答案。",
    "outcome_claims": [],
    "findings": [],
    "steps": [],
    "missing_evidence": ["进程退出信号与资源记录"],
    "sql_candidates": [],
    "limitations": ["合成网关答案，仅用于链路与投影一致性验证"],
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mode(port: int) -> str:
    f = RUNTIME / f"gw-mode-{port}.txt"
    if f.exists():
        return f.read_text(encoding="utf-8").strip() or "ok"
    return "ok"


def _log(record: dict) -> None:
    with _LOCK:
        RUNTIME.mkdir(parents=True, exist_ok=True)
        with (RUNTIME / "gateway-requests.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    # ── 辅助 ──────────────────────────────────────────────────────────
    def _send(self, status: int, payload: dict, extra: dict | None = None):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("x-request-id", f"gw-{int(time.time() * 1000)}")
        for k, v in (extra or {}).items():
            self.send_header(k, str(v))
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if self.path.startswith("/admin/requests"):
            f = RUNTIME / "gateway-requests.jsonl"
            n = len(f.read_text(encoding="utf-8").splitlines()) if f.exists() else 0
            self._send(200, {"count": n})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self):
        port = self.server.server_address[1]
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b""
        auth = self.headers.get("Authorization") or ""
        record = {
            "utc": _now(),
            "port": port,
            "path": self.path,
            "content_type": self.headers.get("Content-Type"),
            "authorization_present": bool(auth),
            "authorization_scheme": auth.split(" ", 1)[0] if auth else None,
            "body_bytes": len(raw),
            "body_sha256": __import__("hashlib").sha256(raw).hexdigest(),
            "body": raw.decode("utf-8", errors="replace"),
        }
        mode = _mode(port)

        if mode == "refuse":
            record["injected"] = "connection closed before response"
            _log(record)
            try:
                self.close_connection = True
                self.connection.close()
            except Exception:
                pass
            return

        if mode == "slow":
            record["injected"] = "slow (sleep 30s)"
            _log(record)
            time.sleep(30)

        if mode == "rate429":
            record["injected"] = "429"
            _log(record)
            self._send(429, {"error": {"message": "rate limited"}},
                       {"Retry-After": "7"})
            return
        if mode == "unavail503":
            record["injected"] = "503"
            _log(record)
            self._send(503, {"error": {"message": "upstream unavailable"}})
            return
        if mode == "auth401":
            record["injected"] = "401"
            _log(record)
            self._send(401, {"error": {"message": "invalid api key"}})
            return

        # 正常路径：解析请求体，回一个引用真实 evidence_id / knowledge_id 的答案
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            body = {}
        answer = json.loads(json.dumps(ANSWER_TEMPLATE))
        user_msg = ""
        for m in (body.get("messages") or []):
            if m.get("role") == "user":
                user_msg = m.get("content") or ""
        try:
            payload = json.loads(user_msg)
        except Exception:
            payload = {}
        # 让答案引用本轮真实存在的 E/K 编号，以通过服务端引用校验
        ev_ids = [c.get("evidence_id") for c in (payload.get("evidence") or [])
                  if c.get("evidence_id")]
        kn_ids = [c.get("knowledge_id") for c in (payload.get("knowledge") or [])
                  if c.get("knowledge_id")]
        if ev_ids:
            answer["findings"].append({
                "kind": "FACT",
                "text": "本轮资料卡已随请求送达，编号见 evidence_ids。",
                "evidence_ids": [ev_ids[0]], "knowledge_ids": []})
        if kn_ids:
            answer["findings"].append({
                "kind": "POLICY",
                "text": "本轮知识条目已随请求送达，编号见 knowledge_ids。",
                "evidence_ids": [], "knowledge_ids": [kn_ids[0]]})

        if mode == "badjson":
            content = "这不是一个 JSON 对象"
        elif mode == "length":
            content = json.dumps(answer, ensure_ascii=False)
        else:
            content = json.dumps(answer, ensure_ascii=False)

        resp = {
            "id": f"chatcmpl-uatd40-{int(time.time() * 1000)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.get("model") or "uat-mock-model",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "length" if mode == "length" else "stop",
            }],
            "usage": {"prompt_tokens": 1234, "completion_tokens": 56,
                      "total_tokens": 1290},
        }
        record["injected"] = mode
        record["response_content_bytes"] = len(content.encode("utf-8"))
        _log(record)
        self._send(200, resp)


def main():
    ports = [int(x) for x in (sys.argv[1:] or ["8443"])]
    cert = RUNTIME / "gw-server.pem"
    key = RUNTIME / "gw-server.key"
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=str(cert), keyfile=str(key))
    servers = []
    for p in ports:
        host = os.getenv("UAT_D40_GW_HOST", "172.16.4.16")
        srv = ThreadingHTTPServer((host, p), Handler)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        servers.append(srv)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        print(f"[gw] https://{host}:{p} ready (mode={_mode(p)})", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        for s in servers:
            s.shutdown()


if __name__ == "__main__":
    main()
