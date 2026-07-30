# -*- coding: utf-8 -*-
"""需表元数据规则的验证脚本（在线元数据增强审核路径）

背景
====
119 条规则中有 7 条（R048/R055/R056/R057/R058/R060/R064）必须拿到真实表
元数据（分片键/是否分片表/索引）才能触发。文件审核与在线元数据审核
（extract-and-audit）均不传 table_metadata，故这 7 条不在 verify_rules.py 中
验证，而由本脚本调用专用端点 POST /api/v1/tdsql/audit/with-metadata 验证——
该端点会按 SQL 涉及的表实时拉取元数据并传入引擎。

（R025/R059 亦需元数据，但经实测为结构性不可达：R025 依赖解析器
alter_actions 而其恒为空；R059 要求 BEGIN 且有表元数据而 BEGIN 无表。
二者已列入 verify_rules.py 的 KNOWN_DEAD，见测试说明书“已知限制”。）

验证目标实例：SIT-分布式实例A（119.45.220.89:15005，库 tdsql_check）。
其分片表 t_customer 分片键为 cust_id，是验证分布式元数据规则的理想对象。

运行
====
    # 前提：后端已启动（默认 http://127.0.0.1:8000），且分布式实例已在实例管理连接
    python tests/rule_audit_materials/verify_metadata_rules.py
    python tests/rule_audit_materials/verify_metadata_rules.py --base http://127.0.0.1:8000 \
        --user admin --password Admin@1234 --conn 5ea70d74

判定方式：断言"目标规则出现在触发集合中"（这些语句会合理共触发其他规则，
故不做精确集合匹配），并打印完整触发集供人工核对。
"""
import argparse
import json
import sys
import urllib.request
import urllib.error

# 每条用例：目标规则 + tailored SQL（针对 t_customer，分片键 cust_id）
CASES = [
    ("R048", "INSERT INTO t_customer (cust_name, id_no) VALUES ('张三', '110101199001011234')"),
    ("R055", "SELECT * FROM t_customer"),
    ("R056", "SELECT cust_id, cust_name FROM t_customer WHERE cust_id = 1001 ORDER BY create_time"),
    ("R057", "INSERT INTO t_customer (cust_name, phone) VALUES ('李四', '13800138000')"),
    ("R058", "UPDATE t_customer SET cust_level = 'gold' WHERE cust_id = 1001"),
    ("R060", "SELECT cust_name FROM t_customer"),
    ("R064", "SELECT cust_name FROM t_customer WHERE cust_id = 1001"),
]


def _http(method, url, token=None, payload=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


def login(base, user, password):
    status, body = _http("POST", f"{base}/api/v1/auth/login",
                         payload={"username": user, "password": password})
    if status != 200 or "token" not in body:
        raise RuntimeError(f"登录失败 status={status} body={body}")
    return body["token"]


def ensure_connected(base, token, conn_id):
    """确保目标实例已激活（with-metadata 需要活跃连接池）。"""
    status, body = _http("POST", f"{base}/api/v1/tdsql/connections/{conn_id}/connect",
                         token=token)
    return status in (200, 400)  # 400 可能为已连接，忽略


def audit_with_metadata(base, token, conn_id, sql):
    status, body = _http("POST", f"{base}/api/v1/tdsql/audit/with-metadata",
                         token=token, payload={"sql": sql, "connection_id": conn_id})
    if status != 200:
        return None, f"HTTP {status}: {body}"
    violations = body.get("audit_result", {}).get("violations", [])
    return {v["rule_id"] for v in violations}, body.get("table_metadata", {})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", default="Admin@1234")
    ap.add_argument("--conn", default="5ea70d74",
                    help="分布式实例连接ID（SIT-分布式实例A）")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    print(f"登录 {args.base} ...")
    token = login(args.base, args.user, args.password)
    ensure_connected(args.base, token, args.conn)

    passed, failed = 0, []
    print("=" * 70)
    for target, sql in CASES:
        fired, meta = audit_with_metadata(args.base, token, args.conn, sql)
        if fired is None:
            failed.append((target, sql, meta))
            print(f"  [ERROR] {target}: {meta}")
            continue
        ok = target in fired
        meta_brief = {t: (m or {}).get("shard_key") for t, m in (meta or {}).items()}
        print(f"  [{'PASS' if ok else 'FAIL'}] {target} 触发={sorted(fired)} "
              f"分片键={meta_brief}")
        if ok:
            passed += 1
        else:
            failed.append((target, sql, sorted(fired)))
    print("=" * 70)
    print(f"需元数据规则验证：{passed}/{len(CASES)} 通过")
    if failed:
        print("未通过：")
        for target, sql, info in failed:
            print(f"  {target}: {sql}  -> {info}")
    print("结论: " + ("[PASS] 7 条需元数据规则全部在分布式实例上正确触发"
                       if not failed else "[FAIL] 存在未触发规则"))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
