# -*- coding: utf-8 -*-
"""全局扫描 openapi 全部路径，定位慢SQL扫描创建端点"""
import json
import urllib.request

BASE = "http://127.0.0.1:8899"
with urllib.request.urlopen(BASE + "/openapi.json", timeout=15) as r:
    spec = json.loads(r.read().decode())

for path, ops in sorted(spec["paths"].items()):
    for method in ops:
        if method.upper() in ("POST", "PUT", "DELETE", "PATCH"):
            summary = ops[method].get("summary", "")
            print(f"{method.upper():6} {path}  -- {summary}")
