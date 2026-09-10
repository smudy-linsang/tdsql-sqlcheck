"""O UAT3 loopback-only harness. Reuse O-owned R2 fixtures; never production.

Proxy can deliberately discard one already-received POST response. Optional runner
child startup delay is test-only fault injection, not target DB capacity evidence.
No product sources or passwords are changed. Only synthetic fixture tables added.
"""
import importlib.util
import json
import os
import socket
import sys
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
spec = importlib.util.spec_from_file_location("r2fixture", HERE.parent / "v1.6.3.5-uat2-o/local_fixture.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
RUNTIME = ROOT / "data/reports/uat_o_1635_r3"
RUNTIME.mkdir(parents=True, exist_ok=True)


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def inspect(label):
    from backend.services.database import _get_connection
    from backend.services.metadata_audit_repository import repository
    from backend.api.metadata_audit import _job_summary
    cx = _get_connection()
    jobs = cx.execute("SELECT * FROM metadata_audit_jobs ORDER BY created_at").fetchall()
    count = cx.execute("SELECT COUNT(*) AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA='uat_o_1635_r2_target'").fetchone()
    cx.close()
    out = {"utc": datetime.now(timezone.utc).isoformat(), "target_tables": count["n"],
           "slot": repository.slot_state(), "jobs": [_job_summary(j) for j in jobs]}
    write_json(HERE / (label + ".json"), out)
    print(json.dumps(out, ensure_ascii=False, default=str))


def runner():
    from backend.services import metadata_job_process as jp
    from backend.workers.metadata_runner import main
    original = jp.run_metadata_worker

    def delayed(cmd, **kw):
        delay = RUNTIME / "delay-child.txt"
        seconds = int(delay.read_text()) if delay.exists() else 0
        assert 0 <= seconds <= 25
        if seconds:
            assert cmd[1:3] == ["-m", "backend.workers.metadata_audit_worker"]
            cmd = [cmd[0], "-c", "import sys,time,runpy;time.sleep(" + str(seconds) + ");"
                   "sys.argv=['backend.workers.metadata_audit_worker']+sys.argv[1:];"
                   "runpy.run_module('backend.workers.metadata_audit_worker',run_name='__main__')"] + cmd[3:]
        return original(cmd, **kw)
    jp.run_metadata_worker = delayed
    main()


class Proxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.forward()

    def do_POST(self):
        self.forward()

    def forward(self):
        import requests
        body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in ('host', 'connection', 'accept-encoding', 'content-length')}
        resp = requests.request(self.command, 'http://127.0.0.1:8005' + self.path,
                                headers=headers, data=body, timeout=60, allow_redirects=False)
        entry = {"utc": datetime.now(timezone.utc).isoformat(), "method": self.command,
                 "path": self.path.split('?')[0], "status": resp.status_code}
        if '/metadata-jobs' in self.path:
            try:
                d = resp.json()
                entry.update(job_id=d.get('job_id'), state=d.get('state'), total=d.get('total'))
            except ValueError:
                pass
        fault = RUNTIME / 'drop-next-post.txt'
        if self.command == 'POST' and self.path == '/api/v1/audit/metadata-jobs' and fault.exists() and fault.read_text().startswith('armed'):
            if fault.read_text() != 'armed-all':
                fault.write_text('consumed', encoding='utf-8')
            entry['injection'] = 'discard accepted POST response only'
            with (RUNTIME / 'proxy-events.jsonl').open('a', encoding='utf-8') as f:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
            self.close_connection = True
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return
        if '/metadata-jobs' in self.path:
            with (RUNTIME / 'proxy-events.jsonl').open('a', encoding='utf-8') as f:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        self.send_response(resp.status_code)
        for k, v in resp.headers.items():
            if k.lower() not in ('content-length', 'transfer-encoding', 'connection', 'content-encoding'):
                self.send_header(k, v)
        self.send_header('Content-Length', str(len(resp.content)))
        self.end_headers()
        try:
            self.wfile.write(resp.content)
        except (BrokenPipeError, ConnectionResetError):
            pass


if __name__ == '__main__':
    mode = sys.argv[1]
    if mode == 'web':
        import uvicorn
        uvicorn.run('backend.main:app', host='127.0.0.1', port=8005)
    elif mode == 'runner':
        runner()
    elif mode == 'proxy':
        ThreadingHTTPServer(('127.0.0.1', 8006), Proxy).serve_forever()
    elif mode == 'inspect':
        inspect(sys.argv[2])
    elif mode == 'delay':
        seconds = int(sys.argv[2]); assert 0 <= seconds <= 25
        (RUNTIME / 'delay-child.txt').write_text(str(seconds), encoding='utf-8')
    elif mode == 'drop-next-post':
        (RUNTIME / 'drop-next-post.txt').write_text('armed', encoding='utf-8')
    elif mode == 'drop-posts':
        (RUNTIME / 'drop-next-post.txt').write_text('armed-all', encoding='utf-8')
    elif mode == 'restore-posts':
        (RUNTIME / 'drop-next-post.txt').write_text('consumed', encoding='utf-8')
    elif mode == 'add-table':
        from backend.services.database import _get_connection
        cx = _get_connection()
        cx.execute("CREATE TABLE IF NOT EXISTS uat_o_1635_r2_target.t_uat3_added_after_scan (id BIGINT NOT NULL, PRIMARY KEY(id)) ENGINE=InnoDB COMMENT='O UAT3 rescan fixture'")
        cx.commit(); cx.close()
        inspect('rescan-after-ddl')
