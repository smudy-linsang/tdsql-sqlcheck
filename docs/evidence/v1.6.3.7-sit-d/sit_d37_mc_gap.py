"""智能体D / v1.6.3.7 SIT：定向验证「时区锁是否真锁」。

做法：把 worker 落库时间回退为 UTC（缺陷态 MC），随后
  ① 跑全部相关测试套件 —— 看是否有任何锁变红；
  ② 跑一次真实任务 —— 直接观测 source（本地时间戳）与 created_at（UTC）是否错位。
最后无条件恢复源码。
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
PY = sys.executable
W = ROOT / "backend/workers/metadata_audit_worker.py"
FIND = '            results_json, created_by, "", None, "", now_local,'
REPL = '            results_json, created_by, "", None, "", _utcnow(),'
SUITES = ["tests/test_v1637_filename_rule.py", "tests/test_v1636_bugs.py",
          "tests/test_v1635_uat3.py", "tests/test_v1635_metadata_jobs.py"]

raw = W.read_bytes()
text = raw.decode("utf-8")
nl = "\r\n" if "\r\n" in text else "\n"
find, repl = FIND.replace("\n", nl), REPL.replace("\n", nl)
out = {"anchor_found": find in text}
try:
    assert find in text
    W.write_bytes(text.replace(find, repl, 1).encode("utf-8"))
    # ① 全部相关套件在缺陷态下是否变红
    r = subprocess.run([PY, "-m", "pytest", *SUITES, "-q", "--tb=no", "-p", "no:cacheprovider"],
                       cwd=str(ROOT), capture_output=True, text=True, timeout=1800)
    out["suites_rc_under_defect"] = r.returncode
    out["suites_tail"] = (r.stdout or "").strip().splitlines()[-1] if r.stdout.strip() else ""
    out["locks_caught_it"] = r.returncode != 0
    # ② 真实任务：观测 source 与 created_at 是否错位
    env = dict(os.environ)
    env.update({"SQLCHECK_DB_HOST": "127.0.0.1", "SQLCHECK_DB_PORT": "13306",
                "SQLCHECK_DB_NAME": "uat_d_1636_meta", "AUTH_ENABLED": "true",
                "REPORT_OUTPUT_DIR": str(ROOT / "data/reports/uat_d_1636/reports"),
                "PYTHONIOENCODING": "utf-8"})
    p = subprocess.run([PY, str(HERE / "sit_d37_probe.py"), "naming"],
                       cwd=str(ROOT), capture_output=True, text=True, timeout=600, env=env)
    try:
        payload = json.loads(p.stdout[p.stdout.find("{"):])
    except Exception:  # noqa: BLE001
        payload = {"raw": (p.stdout or "")[-300:], "err": (p.stderr or "")[-300:]}
    out["real_run"] = {k: payload.get(k) for k in
                       ("source", "ts_part", "audit_history_created_at",
                        "sql_header_line", "state")}
    src = payload.get("source") or ""
    ts = (re.search(r"_(\d{8}_\d{6})\.sql$", src) or [None, None])[1]
    created = (payload.get("audit_history_created_at") or "").replace("T", " ")
    if ts and created:
        try:
            from datetime import datetime
            drift = round((datetime.strptime(created, "%Y-%m-%d %H:%M:%S")
                           - datetime.strptime(ts, "%Y%m%d_%H%M%S")).total_seconds() / 3600, 2)
            out["drift_hours_source_vs_created_at"] = drift
            out["verdict"] = (f"确证假绿：缺陷态下 {len(SUITES)} 个套件全绿，"
                              f"而真实落库出现 {drift} 小时错位（source 本地时间 / created_at UTC）"
                              if not out["locks_caught_it"] else "锁已抓住，无需整改")
        except ValueError as e:
            out["verdict"] = f"解析失败: {e}"
    else:
        out["verdict"] = "未取得可比对的时间字段"
finally:
    W.write_bytes(raw)
    out["restored"] = True

(HERE / "sit-mc-gap.json").write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
print(json.dumps(out, ensure_ascii=False, indent=2))
