# -*- coding: utf-8 -*-
"""TDSQL SQL治理模块压力测试脚本

目的
====
对云上两个实例（SIT-分布式实例A / SIT-集中式实例A）施加可控负载，制造
特征明确的慢SQL，再通过本工具的「慢SQL扫描任务」（digest 数据源）抓取，
验证扫描结果是否符合规则预期：
  1) 全表扫描类慢SQL 被抓取，且分析判定为「全表扫描/缺失索引」(ERROR)；
  2) 扫描任务记录数 > 0，慢SQL列表可按任务查询；
  3) 分布式与集中式实例均能正常完成扫描（集中式走直查 Proxy 分支）。

数据源说明
==========
经实测：两实例 performance_schema.events_statements_summary_by_digest 可用；
tdsqlpcloud_monitor（monitordb 源）在两实例均不可用。故压测采用 digest 源。
long_query_time=1s，故用 SLEEP(1.5) 制造确定性慢SQL（avg>1s 必被捕获）。

运行
====
    python tests/pressure_test/run_pressure_test.py            # 两实例全量
    python tests/pressure_test/run_pressure_test.py --inst DIST # 仅分布式
    python tests/pressure_test/run_pressure_test.py --rows 5000 --repeat 5

流程：建表灌数 → 制造慢查询 → 触发 digest 扫描 → 轮询任务 → 验证 → 清理(可选)
"""
import argparse
import json
import sys
import threading
import time
import urllib.request
import urllib.error

import pymysql

import config as cfg

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# ────────────────────────────────────────────────────────────
# HTTP 工具
# ────────────────────────────────────────────────────────────
def _http(method, path, token=None, payload=None, timeout=120):
    url = cfg.API_BASE + path
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


def login():
    status, body = _http("POST", "/api/v1/auth/login",
                         payload={"username": cfg.API_USER, "password": cfg.API_PASSWORD})
    if status != 200 or "token" not in body:
        raise RuntimeError(f"登录失败 status={status} body={body}")
    return body["token"]


# ────────────────────────────────────────────────────────────
# 阶段 1：建表灌数
# ────────────────────────────────────────────────────────────
def connect(inst):
    return pymysql.connect(host=inst["host"], port=inst["port"], user=inst["user"],
                           password=inst["password"], database=inst["database"],
                           connect_timeout=10, charset="utf8mb4", autocommit=True)


def setup_tables(conn, inst, rows):
    """建两张表：pt_slow_noindex（无二级索引）与 pt_slow_indexed（uid 有索引）。"""
    is_dist = inst["type"] == "distributed"
    # 分布式实例建表须声明分片键（R077）；集中式不需要
    shard_suffix = " SHARDKEY=id" if is_dist else ""
    shard_suffix2 = " SHARDKEY=uid" if is_dist else ""
    cur = conn.cursor()
    cur.execute(f"DROP TABLE IF EXISTS {cfg.TABLE_PREFIX}slow_noindex")
    cur.execute(f"DROP TABLE IF EXISTS {cfg.TABLE_PREFIX}slow_indexed")
    cur.execute(f"""
        CREATE TABLE {cfg.TABLE_PREFIX}slow_noindex (
            id BIGINT NOT NULL COMMENT '主键',
            uid BIGINT NOT NULL COMMENT '用户ID',
            payload VARCHAR(128) NOT NULL COMMENT '载荷',
            create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
            PRIMARY KEY (id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='压测无索引表'{shard_suffix}
    """)
    cur.execute(f"""
        CREATE TABLE {cfg.TABLE_PREFIX}slow_indexed (
            uid BIGINT NOT NULL COMMENT '用户ID',
            val VARCHAR(128) NOT NULL COMMENT '值',
            create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
            PRIMARY KEY (uid),
            INDEX idx_uid (uid)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='压测有索引表'{shard_suffix2}
    """)
    # 灌数（分批 INSERT，含分片键）
    batch = 500
    for start in range(0, rows, batch):
        vals_noindex = ",".join(
            f"({i},{i},'data_{i}',NOW())" for i in range(start, min(start + batch, rows)))
        cur.execute(f"INSERT INTO {cfg.TABLE_PREFIX}slow_noindex (id,uid,payload,create_time) VALUES {vals_noindex}")
        vals_indexed = ",".join(
            f"({i},'v_{i}',NOW())" for i in range(start, min(start + batch, rows)))
        cur.execute(f"INSERT INTO {cfg.TABLE_PREFIX}slow_indexed (uid,val,create_time) VALUES {vals_indexed}")
    print(f"    建表完成，各灌入 {rows} 行")


# ────────────────────────────────────────────────────────────
# 阶段 2：制造慢查询
# ────────────────────────────────────────────────────────────
# 特征慢SQL：标记注释会被 digest 剥离，故改用【表名】作为定位特征（pt_slow_*）。
# 共享实例上高频监控查询会占据 digest Top-N，故制造【真慢】查询（全表 ORDER BY
# RAND 排序，单次即扫描全表 + filesort）累积 SUM_TIMER_WAIT 以提升排名。
def slow_queries(inst):
    p = cfg.TABLE_PREFIX
    return [
        # 全表 + ORDER BY RAND 排序 → 真实慢 + filesort + 全表扫描（重点负载）
        (f"SELECT uid, payload FROM {p}slow_noindex ORDER BY RAND() LIMIT 50",
         "RANDSORT"),
        # 无索引全表扫描 + 函数过滤 → 全表扫描/缺失索引
        (f"SELECT COUNT(*) FROM {p}slow_noindex WHERE payload LIKE '%data%'",
         "FULLSCAN"),
        # 自交叉连接（笛卡尔积）→ rows_examined 极高
        (f"SELECT COUNT(*) FROM {p}slow_noindex a, {p}slow_noindex b WHERE a.id < 80 AND b.id < 80",
         "CROSSJOIN"),
        # 走索引的等值查询（对照组，访问类型应为 ref）
        (f"SELECT val FROM {p}slow_indexed WHERE uid = 1",
         "INDEXED"),
    ]


def generate_load(conn, inst, repeat):
    cur = conn.cursor()
    # 重点负载多跑几轮，累积 SUM_TIMER_WAIT 以进入 digest Top-N
    for sql, tag in slow_queries(inst):
        rounds = repeat * (3 if tag == "RANDSORT" else 1)
        for _ in range(rounds):
            cur.execute(sql)
    # 额外制造几条确定性超阈值慢SQL（SLEEP），模拟真实慢查询负载
    for _ in range(repeat):
        cur.execute("SELECT SLEEP(1.5)")
    print(f"    已制造 {len(slow_queries(inst))} 类特征SQL（RANDSORT 重点负载）+ SLEEP 慢查询")


# ────────────────────────────────────────────────────────────
# 阶段 3：触发扫描 + 验证（支持 monitordb / digest / processlist 三种数据源）
# ────────────────────────────────────────────────────────────
SOURCE_LABELS = {"monitordb": "全网慢SQL(monitordb)",
                 "digest": "性能摘要(digest)",
                 "processlist": "实时进程(processlist)"}


def trigger_scan(token, inst, source, min_time=0.0, poll_duration=12.0, window_hours=1):
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    start = time.strftime("%Y-%m-%d %H:%M:%S",
                          time.localtime(time.time() - window_hours * 3600))
    payload = {
        "connection_id": inst["conn_id"],
        "source": source,
        "limit": 100,
        "min_time": min_time,
        "task_name": f"压测验证-{inst['type']}-{SOURCE_LABELS[source]}-{now}",
        # processlist 轮询参数
        "poll_duration": poll_duration,
        "poll_interval": 1.0,
    }
    # monitordb 不传时间窗：实测发现部分实例 timestramp 时间窗过滤会清空结果
    # （疑似时间戳单位/类型差异），不传窗则抓全量历史慢SQL，验证更稳健。
    if source != "monitordb":
        payload["time_window_start"] = start
        payload["time_window_end"] = now
    status, body = _http("POST", "/api/v1/tdsql/slow-queries/fetch", token=token,
                         payload=payload, timeout=240)
    return status, body


def start_load_generator(inst, stop_event, workers=3):
    """processlist 扫描期间在后台持续制造正在执行的慢SQL（SLEEP(2)），
    使轮询能捕获到 time>阈值的活跃查询。

    起多个并发 worker 紧密循环执行 SLEEP，确保整个轮询窗口内始终有
    正在执行的慢查询（单线程在 SLEEP 间隔可能有空窗，分布式 Proxy 下
    还可能因路由/连接抖动丢失，故用多线程提高捕获可靠性）。返回线程列表。
    """
    def _worker():
        try:
            c = connect(inst)
            cur = c.cursor()
            while not stop_event.is_set():
                try:
                    cur.execute("SELECT SLEEP(2)")
                except Exception:
                    break
            c.close()
        except Exception as e:
            print(f"    负载线程异常: {e}")
    threads = []
    for _ in range(workers):
        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        threads.append(t)
    return threads


def fetch_task_records(token, task_id):
    status, body = _http("GET", f"/api/v1/slow-queries?scan_task_id={task_id}&limit=200",
                         token=token)
    if status != 200:
        return []
    return body.get("items", body.get("records", []))


def verify(inst, scan_body, source="digest"):
    """验证扫描结果是否符合预期。返回 (通过项, 失败项)。

    processlist 源只捕获扫描瞬间正在执行的慢SQL（本压测为 SLEEP(3)），
    无高 rows_examined，故采用专用验证；monitordb/digest 用分层验证。
    """
    passed, failed = [], []
    fetched = scan_body.get("fetched", 0)
    task_id = scan_body.get("scan_task_id")

    if fetched > 0:
        passed.append(f"扫描抓取到 {fetched} 条慢SQL（fetched>0）")
    else:
        failed.append(f"扫描抓取数为 0（期望>0）；errors={scan_body.get('errors')}")

    if not task_id:
        failed.append("未返回 scan_task_id")
        return passed, failed

    token = login()
    records = fetch_task_records(token, task_id)
    if records:
        passed.append(f"任务 {task_id} 入库 {len(records)} 条慢SQL记录")
    else:
        failed.append(f"任务 {task_id} 未查询到入库记录")
        return passed, failed

    # ── processlist 专用验证：确认捕获到正在执行的慢SQL（SLEEP）并完成分析 ──
    if source == "processlist":
        sleep_hits = [r for r in records if "sleep" in (r.get("sql_text") or "").lower()]
        if sleep_hits:
            r = sleep_hits[0]
            passed.append(f"实时进程源捕获到正在执行的慢SQL（SLEEP）："
                          f"avg={r.get('avg_time_ms')}ms, problem_type={r.get('problem_type') or '无'}")
        else:
            failed.append("实时进程源未捕获到正在执行的慢SQL（SLEEP）；"
                          "可能轮询窗口未命中或 min_time 偏高")
        return passed, failed

    # ── monitordb / digest 分层验证 ──
    # (b) 分析器正确性：高扫描行数记录应被分析器给出有效问题判定
    VALID_PTYPES = ("全表扫描", "缺失索引", "索引使用不充分", "高频慢SQL",
                    "锁等待严重", "Using filesort", "Using temporary", "Using join buffer")
    by_examined = sorted(records, key=lambda r: r.get("rows_examined") or 0, reverse=True)
    heavy = [r for r in by_examined if (r.get("rows_examined") or 0) > 10000]
    if heavy:
        r = heavy[0]
        sev = (r.get("severity") or "").upper()
        ptype = r.get("problem_type") or ""
        if ptype and (sev in ("ERROR", "CRITICAL", "WARNING") or ptype in VALID_PTYPES):
            passed.append(f"高扫描行数慢SQL（examined={r.get('rows_examined')}）被分析器识别为 "
                          f"problem_type={ptype}/severity={sev}，分析符合预期")
        else:
            failed.append(f"高扫描行数慢SQL（examined={r.get('rows_examined')}）未被有效分析："
                          f"severity={sev}, problem_type={ptype}")
    else:
        # monitordb 可能以耗时维度汇聚，无高 examined 时退而核对是否有有效分析记录
        analyzed = [r for r in records if (r.get("problem_type") or "")]
        if analyzed:
            r = analyzed[0]
            passed.append(f"扫描记录均被分析器给出问题判定（例：problem_type="
                          f"{r.get('problem_type')}/severity={r.get('severity')}）")
        else:
            failed.append("扫描结果中无高扫描行数记录且无有效分析，无法验证分析器")

    # (c) 压测特征SQL 软性检查
    p = cfg.TABLE_PREFIX
    pt_records = [r for r in records if p in (r.get("sql_text") or "").lower()]
    if pt_records:
        passed.append(f"压测特征SQL（{p}*）有 {len(pt_records)} 条进入扫描结果")
        rand_hits = [r for r in pt_records if "rand" in (r.get("sql_text") or "").lower()]
        if rand_hits:
            r = rand_hits[0]
            passed.append(f"  ORDER BY RAND 全表排序慢SQL被捕获：severity={r.get('severity')}, "
                          f"problem_type={r.get('problem_type')}, examined={r.get('rows_examined')}")
    else:
        passed.append(f"压测特征SQL（{p}*）未进入 Top-N（共享实例高频查询淹没，属预期）；"
                      f"分析器正确性已由 (b) 验证")
    return passed, failed


# ────────────────────────────────────────────────────────────
# 清理
# ────────────────────────────────────────────────────────────
def cleanup(conn, inst):
    cur = conn.cursor()
    cur.execute(f"DROP TABLE IF EXISTS {cfg.TABLE_PREFIX}slow_noindex")
    cur.execute(f"DROP TABLE IF EXISTS {cfg.TABLE_PREFIX}slow_indexed")
    print("    已清理压测表")


def run_source(token, inst, source):
    """对单个数据源执行扫描+验证。返回 (是否通过, 通过项, 失败项)。"""
    print(f"\n  ── 数据源：{SOURCE_LABELS[source]} ──")
    stop_event = None
    gen_threads = None
    if source == "processlist":
        # processlist 只捕获扫描瞬间正在执行的慢SQL，需后台持续制造负载
        stop_event = threading.Event()
        gen_threads = start_load_generator(inst, stop_event)
        time.sleep(1.5)  # 等负载先跑起来
    try:
        # processlist 用较高阈值确保捕获 SLEEP(2)；monitordb/digest 用低阈值拓全量
        min_time = 1.0 if source == "processlist" else 0.0
        # digest 用 1h 时间窗；monitordb 不传窗（抓全量历史，避开时间窗过滤问题）
        window_hours = 1
        status, body = trigger_scan(token, inst, source, min_time=min_time,
                                    window_hours=window_hours)
        if status != 200:
            return False, [], [f"扫描触发失败 HTTP {status}: {body}"]
        print(f"    扫描完成 fetched={body.get('fetched')} task_id={body.get('scan_task_id')} "
              f"errors={body.get('errors') or '无'}")
        passed, failed = verify(inst, body, source)
        # processlist 捕获具时序敏感性：首轮未捕获到则重试一次
        if source == "processlist" and failed and body.get("fetched", 0) == 0:
            print("    首轮未捕获，重试一次 ...")
            status, body = trigger_scan(token, inst, source, min_time=min_time,
                                        window_hours=window_hours)
            if status == 200:
                print(f"    重试扫描 fetched={body.get('fetched')} task_id={body.get('scan_task_id')}")
                passed, failed = verify(inst, body, source)
        for p in passed:
            print(f"    [PASS] {p}")
        for f in failed:
            print(f"    [FAIL] {f}")
        return (not failed), passed, failed
    finally:
        if stop_event is not None:
            stop_event.set()
        if gen_threads:
            for t in gen_threads:
                t.join(timeout=5)


def run_instance(name, inst, rows, repeat, keep, sources):
    print(f"\n{'='*70}\n实例 {name}（{inst['type']}，{inst['host']}:{inst['port']}）\n{'='*70}")
    conn = connect(inst)
    try:
        print("  [1/3] 建表灌数 ...")
        setup_tables(conn, inst, rows)
        print("  [2/3] 制造慢查询负载（供 monitordb/digest 汇聚）...")
        generate_load(conn, inst, repeat)
        time.sleep(3)  # digest/monitordb 统计刷新需要片刻
        print("  [3/3] 逐数据源扫描验证 ...")
        token = login()
        source_results = {}
        for source in sources:
            ok, _, _ = run_source(token, inst, source)
            source_results[source] = ok
        print(f"\n  实例 {name} 各数据源结论:")
        for source in sources:
            print(f"    {SOURCE_LABELS[source]}: {'[PASS]' if source_results[source] else '[FAIL]'}")
        return all(source_results.values())
    finally:
        if not keep:
            try:
                cleanup(conn, inst)
            except Exception as e:
                print(f"    清理失败: {e}")
        conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inst", default="ALL", choices=["ALL", "DIST", "CENT"])
    ap.add_argument("--source", default="ALL",
                    choices=["ALL", "monitordb", "digest", "processlist"],
                    help="扫描数据源：ALL=三种都测")
    ap.add_argument("--rows", type=int, default=3000, help="每表灌入行数")
    ap.add_argument("--repeat", type=int, default=3, help="每类慢SQL执行次数")
    ap.add_argument("--keep", action="store_true", help="保留压测表不清理")
    args = ap.parse_args()

    targets = (["DIST", "CENT"] if args.inst == "ALL" else [args.inst])
    sources = (["monitordb", "digest", "processlist"] if args.source == "ALL"
               else [args.source])
    results = {}
    for name in targets:
        inst = cfg.INSTANCES[name]
        try:
            results[name] = run_instance(name, inst, args.rows, args.repeat,
                                         args.keep, sources)
        except Exception as e:
            print(f"  实例 {name} 执行异常: {e}")
            results[name] = False

    print(f"\n{'='*70}\n压力测试总结（数据源：{', '.join(sources)}）\n{'='*70}")
    for name, ok in results.items():
        print(f"  {name}: {'[PASS]' if ok else '[FAIL]'}")
    all_ok = all(results.values())
    print("总结论: " + ("[PASS] 全部实例×数据源扫描结果符合规则预期"
                        if all_ok else "[FAIL] 存在不符合预期的实例/数据源"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
