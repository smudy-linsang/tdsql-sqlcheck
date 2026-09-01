# -*- coding: utf-8 -*-
"""G14 · 表类型统计 回归测试（DESIGN-v1.6.3.0 Rev.M §11）

**测试依赖如实说明**（Rev.J / DOC-01——Rev.I 之前一直写"除落库两例外全部离线"，
那是 Rev.B 时期的实际情况，后来陆续加到 10 例仍没有更新，属于文档失真）：

| 类别 | 数量 | 依赖 |
|---|---:|---|
| 纯离线 | **87** | 无。FakePool + FakeClock 全内存，不连任何数据库 |
| 元数据库集成 | **22** | 本地 MySQL/MariaDB；`@skipif(not MYSQL_AVAILABLE)` |
| 需模块落盘 | **1** | T-R08 权限键登记，断言仓库文件；设计阶段 skip |
| **collect 合计** | **110** | 含参数化展开（其中 T3-R08 契约用例 11 条） |

Rev.K 历史实测口径：不连元数据库时 `83 passed, 23 skipped`；连上后
`105 passed, 1 skipped`。第五轮将 Rev.L 附录抽取后实测 110 collected、连元数据库
109 passed + 1 skipped、离线 87 passed + 23 skipped；这仍不是落盘后的仓库回归证据。

元数据库集成用例覆盖：落库与回读、`/history` 与 `created_by`、500 库告警的
`MEDIUMTEXT` 往返、以及 6 项结构契约失败关闭（缺表 / 缺列 / 错类型 / 缺索引 /
采集前拦截 / DDL 与服务列清单一致）。它们连接的是
`SQLCHECK_DB_NAME`（默认 `tdsql_sqlcheck_test`），由 `g14_schema` fixture
在每个用例前后 DROP + 按 DDL 重建，互不干扰。

**破坏性目标保护（Rev.K / P1-04）**：可用性探测与实际 DROP 使用**同一份**
`backend.services.database.MYSQL_CONFIG`；任何 DROP 之前先过
`assert_destructive_target_is_safe()`——库名不精确等于 `tdsql_sqlcheck_test`
（或显式开启 `G14_ALLOW_DESTRUCTIVE_TESTS=1` + `G14_TEST_DB_NAME`）就抛
`DestructiveTargetError`，并把 host/port/database 打进日志。

元数据库不可达时这 22 例自动 skip——
**skip 不是 pass**：模块真正落盘后必须在具备元数据库的环境上重跑，
设计阶段的通过/跳过数只能作为设计阶段记录，不能当作发布证据。

数据夹具取自内网实测（设计附录 B）：列名 db_table，值为库限定名；
子分区相关用例直接使用 2026-08-31 T17 取回的 78 个真实表名。
"""
import os
import random
import sys
import time

import pytest

from backend.services import table_type_stats_service as svc
from backend.services.tdsql_connector import TDSQLConnectionConfig

# Rev.L 定向回归（O 第四轮评审的 T4-R01…T4-R05；Rev.M 修正 T4-R01）
def test_t4r01_baseline_rejects_case_sibling_returned_by_ci_in(monkeypatch):
    """T4-R01 / P1-01：指定库基线多返兄弟库时，两个分支都不得吸收。"""
    class _CiMetadataPool(FakePool):
        def _execute(self, sql, params=None):
            if "information_schema.TABLES" in sql:
                self.seen.append(sql)
                # 模拟服务端不遵守大小写精确语义：普通 IN 只查 Sales 却带回 sales。
                return [
                    {"TABLE_SCHEMA": "Sales", "TABLE_NAME": "t_upper",
                     "TABLE_TYPE": "BASE TABLE"},
                    {"TABLE_SCHEMA": "sales", "TABLE_NAME": "t_lower",
                     "TABLE_TYPE": "BASE TABLE"},
                ]
            return super()._execute(sql, params)

    for instance_type in ("centralized", "distributed"):
        _patch_ctx(monkeypatch, instance_type)
        per_db = {}
        if instance_type == "distributed":
            per_db = {
                ("Sales", svc.SQL_SHARD): _rows(["Sales.t_upper"]),
                ("Sales", svc.SQL_BROADCAST): [],
                ("Sales", svc.SQL_SINGLE): [],
            }
        pool = _CiMetadataPool(databases=["Sales", "sales"], per_db=per_db)
        if instance_type == "distributed":
            _patch_tmp_pool(monkeypatch, pool)
        res = svc.analyze(pool, connection_id="c1", database="Sales")
        assert res["database_count"] == 1
        assert res["total_tables"] == 1
        assert res["baseline_tables"] == 1
        assert res["items"][0]["db_name"] == "Sales"
        assert not any(w["code"] == "RECON_MISMATCH" for w in res["warnings"])


def test_t4r02_real_pool_tail_path_can_exceed_old_formula(monkeypatch):
    """T4-R02 / P1-02：真实池控制流可走出旧公式未覆盖的 70s 尾部。"""
    from backend.services.tdsql_connector import TDSQLConnectionPool

    clock = FakeClock()
    started = clock.t
    pool = TDSQLConnectionPool(
        TDSQLConnectionConfig(host="h", port=3306, user="u", password="p",
                              database="d", connect_timeout=5, read_timeout=30),
        pool_size=1)

    class _OldConnection:
        def ping(self, reconnect=False):
            clock.advance(30.0)
            raise OSError("stale connection")

        def close(self):
            pass

    class _SelectFails:
        def select_db(self, db):
            clock.advance(30.0)
            raise RuntimeError("select_db failed")

        def close(self):
            pass

    class _Spare:
        def close(self):
            pass

    pool._local.conn = _OldConnection()
    created = []

    def _create():
        clock.advance(5.0)
        created.append(True)
        return _SelectFails() if len(created) == 1 else _Spare()

    monkeypatch.setattr(pool, "_create_connection", _create)
    with pytest.raises(RuntimeError, match="select_db failed"):
        with pool.get_connection() as conn:
            conn.select_db("db_a")

    # ping 30 + 建连 5 + select_db 30 + 异常后重建 5 = 70。
    assert clock.t - started == 70.0
    assert len(created) == 2
    assert not hasattr(svc, "MAX_COLLECT_WALL_SECONDS")
    assert not hasattr(svc, "MAX_WALL_SECONDS")

    # 软预算反向护栏：已进入 get_connection 的 ping→建连可跨过 deadline，
    # 但服务层在拿到连接后必须复查，不得再新发 select_db。
    _patch_ctx(monkeypatch, "distributed")
    clock2 = FakeClock()
    started2 = clock2.t
    deadline = clock2.t + 31.0
    tail = TDSQLConnectionPool(
        TDSQLConnectionConfig(host="h", port=3306, user="u", password="p",
                              database="Sales", connect_timeout=5, read_timeout=30),
        pool_size=1)
    selected = []

    class _OldAgain:
        def ping(self, reconnect=False):
            clock2.advance(30.0)
            raise OSError("stale connection")

        def close(self):
            pass

    class _AcquiredAfterDeadline:
        def select_db(self, db):
            selected.append(db)

        def close(self):
            pass

    tail._local.conn = _OldAgain()
    created2 = []

    def _create2():
        clock2.advance(5.0)
        created2.append(True)
        return _AcquiredAfterDeadline()

    monkeypatch.setattr(tail, "_create_connection", _create2)
    monkeypatch.setattr(svc, "_new_pool", lambda cfg, pool_size=1: tail)
    monkeypatch.setattr(svc, "_now", clock2)
    outer = FakePool(databases=["Sales"],
                     info_schema={"Sales": {"base": ["t_upper"]}})
    res = svc.analyze(outer, connection_id="c1", database="Sales", deadline=deadline)

    assert clock2.t - started2 == 35.0
    assert len(created2) == 1, "只允许已启动的 get_connection 内部建连"
    assert selected == [], "拿到连接时 deadline 已过，不得再新发 select_db"
    assert res["skipped_databases"] == 1
    assert res["items"][0]["status"] == "SKIPPED"


def test_t4r03_pymysql_read_timeout_is_per_read():
    """T4-R03 / P1-02：30s read_timeout 不是整条结果读取的总 deadline。"""
    from pymysql.connections import Connection

    clock = FakeClock()
    started = clock.t

    class _Sock:
        def __init__(self):
            self.timeouts = []

        def settimeout(self, value):
            self.timeouts.append(value)

        def close(self):
            pass

    class _RFile:
        def read(self, size):
            clock.advance(20.0)
            return b"x" * size

        def close(self):
            pass

    conn = object.__new__(Connection)
    conn._sock = _Sock()
    conn._rfile = _RFile()
    conn._read_timeout = 30
    assert conn._read_bytes(1) == b"x"
    assert conn._read_bytes(1) == b"x"
    assert clock.t - started == 40.0, "两次成功读取累计可超过单次 read_timeout"
    assert conn._sock.timeouts == [30, 30], "每次底层读取前都重设超时"
    conn._force_close()


def test_t4r05_probe_connects_effective_database(monkeypatch):
    """T4-R05 / P2-02：探测必须真正选中后续 DROP 使用的元数据库。"""
    import pymysql

    cfg = {"host": "meta", "port": 3307, "user": "tester", "password": "pw",
           "database": "tdsql_sqlcheck_test", "charset": "utf8mb4"}
    seen = {}

    class _Conn:
        def close(self):
            pass

    def _connect(**kwargs):
        seen.update(kwargs)
        return _Conn()

    mod = sys.modules[__name__]
    monkeypatch.setattr(mod, "effective_db_config", lambda: dict(cfg))
    monkeypatch.setattr(pymysql, "connect", _connect)
    assert _probe_metadata_db() is True
    for key in ("host", "port", "user", "password", "database", "charset"):
        assert seen[key] == cfg[key]

# ══════════════════════════════════════════════════════════════════
# Rev.K 定向回归（O 第三轮评审 §7 的 T3-R01…T3-R10）
# ══════════════════════════════════════════════════════════════════
def test_t3r01_named_database_must_not_absorb_case_sibling(monkeypatch):
    """T3-R01 / P1-01：指定 `Sales` 时，实例级返回里的 `sales.*` 必须被过滤掉。

    Rev.J 在 `_extract_pairs` 里用全实例命名空间 known 把 qual 解析成了 canonical
    名（这步对），随后又对只含目标库的 target 做了**第二次** CI 回退——
    目标子集只有 `Sales` 一个候选时，`sales` 会被"唯一命中"回 `Sales`，
    于是另一个真实库的表被算进用户指定的库。四个主数字直接错。
    """
    _patch_ctx(monkeypatch, "distributed")
    allrows = _rows(["Sales.t_upper", "sales.t_lower"], info="shardkey:id")
    per_db = {("Sales", svc.SQL_SHARD): allrows,
              ("Sales", svc.SQL_BROADCAST): [],
              ("Sales", svc.SQL_SINGLE): []}
    pool = FakePool(databases=["Sales", "sales"],
                    info_schema={"Sales": {"base": ["t_upper"]},
                                 "sales": {"base": ["t_lower"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1", database="Sales")

    assert res["database_count"] == 1
    assert [i["db_name"] for i in res["items"]] == ["Sales"]
    assert res["total_tables"] == 1, "sales.t_lower 必须被过滤，不得计入 Sales"
    assert res["shard_tables"] == 1
    assert res["baseline_tables"] == 1
    assert not any(w["code"] == "RECON_MISMATCH" for w in res["warnings"])
    # 反向：指定 sales 时同理只计自己那一张
    per_db2 = {("sales", svc.SQL_SHARD): allrows,
               ("sales", svc.SQL_BROADCAST): [],
               ("sales", svc.SQL_SINGLE): []}
    pool2 = FakePool(databases=["Sales", "sales"],
                     info_schema={"Sales": {"base": ["t_upper"]},
                                  "sales": {"base": ["t_lower"]}},
                     per_db=per_db2)
    _patch_tmp_pool(monkeypatch, pool2)
    res2 = svc.analyze(pool2, connection_id="c1", database="sales")
    assert res2["total_tables"] == 1
    assert [i["db_name"] for i in res2["items"]] == ["sales"]


def test_t3r02_budget_exit_does_not_rebuild_connection(monkeypatch):
    """T3-R02 / P1-02：预算耗尽是正常控制流，**不得**触发连接池重建。

    真实池会捕获穿出 `with` 的任何异常并 close + `_create_connection()`。
    用异常承载"预算耗尽"，等于在 deadline 之后把一条健康连接销毁重建，
    白白多付一次 connect_timeout。
    """
    _patch_ctx(monkeypatch, "distributed")
    clock = FakeClock()
    monkeypatch.setattr(svc, "_now", clock)
    deadline = clock.t + 100.0
    per_db = {}
    for d in ("db_a", "db_b"):
        for sql in (svc.SQL_SHARD, svc.SQL_BROADCAST, svc.SQL_SINGLE):
            per_db[(d, sql)] = _rows([f"{d}.t"])
    pool = FakePool(databases=["db_a", "db_b"],
                    info_schema={"db_a": {"base": ["t"]}, "db_b": {"base": ["t"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    orig = svc._extract_pairs

    def _slow(rows, cur, known):
        clock.advance(60.0)
        return orig(rows, cur, known)

    monkeypatch.setattr(svc, "_extract_pairs", _slow)
    res = svc.analyze(pool, connection_id="c1", deadline=deadline)

    assert pool.create_calls == 0, \
        f"预算控制流不得触发连接重建，实际重建 {pool.create_calls} 次"
    assert pool.generation == 0
    by_db = {i["db_name"]: i for i in res["items"]}
    assert by_db["db_a"]["status"] == "SKIPPED"
    assert by_db["db_b"]["status"] == "SKIPPED"


def test_t3r03_budget_signal_survives_a_failing_reconnect(monkeypatch):
    """T3-R03 / P1-02：即使重连会失败，预算路径也不该走到那里——状态必须是 SKIPPED。

    Rev.J 的写法下，`_BudgetExceeded` 穿出 with → 池尝试重建 → 重建抛错 →
    新异常盖掉原信号 → 本该 SKIPPED 的库被误标成 FAILED。
    """
    _patch_ctx(monkeypatch, "distributed")
    clock = FakeClock()
    monkeypatch.setattr(svc, "_now", clock)
    deadline = clock.t + 100.0
    per_db = {("db_a", sql): _rows(["db_a.t"])
              for sql in (svc.SQL_SHARD, svc.SQL_BROADCAST, svc.SQL_SINGLE)}
    pool = FakePool(databases=["db_a"], info_schema={"db_a": {"base": ["t"]}},
                    per_db=per_db)
    pool.reconnect_fail = _mysql_error(2003, "Can't connect to MySQL server")
    _patch_tmp_pool(monkeypatch, pool)
    orig = svc._extract_pairs

    def _slow(rows, cur, known):
        clock.advance(60.0)
        return orig(rows, cur, known)

    monkeypatch.setattr(svc, "_extract_pairs", _slow)
    res = svc.analyze(pool, connection_id="c1", deadline=deadline)
    assert pool.create_calls == 0, "预算路径根本不该碰到重连"
    assert res["items"][0]["status"] == "SKIPPED", "不得被重连失败盖成 FAILED"
    assert res["skipped_databases"] == 1 and res["failed_databases"] == 0


def test_t3r03b_real_error_still_rebuilds(monkeypatch):
    """T3-R03 反向护栏：**真实**的数据库异常仍必须触发重建（P1-04 不得回退）。"""
    _patch_ctx(monkeypatch, "distributed")
    per_db = {("db_a", svc.SQL_SHARD): _mysql_error(2013, "Lost connection"),
              ("db_b", svc.SQL_SHARD): _rows(["db_b.s"]),
              ("db_b", svc.SQL_BROADCAST): [],
              ("db_b", svc.SQL_SINGLE): []}
    pool = FakePool(databases=["db_a", "db_b"],
                    info_schema={"db_a": {"base": ["s"]}, "db_b": {"base": ["s"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert pool.create_calls == 1, "真实异常必须触发一次连接重建"
    by_db = {i["db_name"]: i for i in res["items"]}
    assert by_db["db_a"]["status"] == "FAILED"
    assert by_db["db_b"]["status"] == "OK"


def test_t3r04_soft_budget_scope_is_explicit():
    """T3-R04 / Rev.L P1-02：180s 是软预算，不存在伪硬上界常量。"""
    assert svc.TOTAL_BUDGET_SECONDS == 180
    assert svc.CONNECT_TIMEOUT == 5
    assert svc.COMMAND_READ_TIMEOUT == 30
    assert not hasattr(svc, "MAX_WALL_SECONDS")
    assert not hasattr(svc, "MAX_COLLECT_WALL_SECONDS")
    # 元数据库阶段不在 target deadline 内，但落库必须保持批量化。
    assert svc.ITEM_INSERT_BATCH >= 50


def test_t3r04b_deadline_checkpoints_are_where_the_soft_budget_says(monkeypatch):
    """T3-R04：钉住"到期后不再开新 I/O"依赖的检查点。"""
    import inspect
    src_analyze = inspect.getsource(svc.analyze)
    assert src_analyze.count("_now() >= deadline") >= 3, \
        "analyze 必须在实例类型探测后、库枚举前、基线查询前各检查一次"
    src_collect = inspect.getsource(svc._collect_distributed)
    assert src_collect.count("_now() >= deadline") >= 3, \
        "_collect_distributed 必须在进连接上下文前、拿到连接后/切库前、每条命令前检查"
    acquire_pos = src_collect.index("with tmp.get_connection() as conn:")
    post_acquire_pos = src_collect.index("if _now() >= deadline:", acquire_pos)
    select_pos = src_collect.index("conn.select_db(db)", acquire_pos)
    assert acquire_pos < post_acquire_pos < select_pos, \
        "ping/建连可跨过 deadline；拿到连接后、select_db 前必须复查"
    # 预算耗尽必须是"置标志 + break"，不是抛异常
    assert "budget_hit = True" in src_collect
    assert "raise _BudgetExceeded" not in src_collect


def test_t3r10_zero_effective_databases_is_not_partial_success(monkeypatch):
    """T3-R10 / P2-03：失败 + 跳过覆盖全部库时，有效库数为 0，不能叫"部分完成"。

    这条在服务端把判据钉住：前端据 `database_count - failed - skipped` 分流。
    """
    _patch_ctx(monkeypatch, "distributed")
    clock = FakeClock()
    monkeypatch.setattr(svc, "_now", clock)
    per_db = {("db_a", svc.SQL_SHARD): _mysql_error(1142, "denied")}
    pool = FakePool(databases=["db_a", "db_b"],
                    info_schema={"db_a": {"base": ["x"]}, "db_b": {"base": ["y"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    orig = svc._collect_baseline

    def _tick(p, dbs, known):
        out = orig(p, dbs, known)
        return out

    monkeypatch.setattr(svc, "_collect_baseline", _tick)

    # db_a 失败；db_b 在进入前预算耗尽
    real_extract = svc._extract_pairs

    def _boom(rows, cur, known):
        clock.advance(200.0)
        return real_extract(rows, cur, known)

    monkeypatch.setattr(svc, "_extract_pairs", _boom)
    res = svc.analyze(pool, connection_id="c1",
                      deadline=clock.t + svc.TOTAL_BUDGET_SECONDS)
    effective = (res["database_count"] - res["failed_databases"]
                 - res["skipped_databases"])
    assert effective == 0, "两个库一个失败一个跳过，有效库数必须是 0"
    assert res["total_tables"] == 0
    assert res["baseline_tables"] == 0 and res["subpartition_tables"] == 0


# ══════════════════════════════════════════════════════════════════
# 元数据库集成测试的目标保护（Rev.K / P1-04）
# ══════════════════════════════════════════════════════════════════
#
# 这些用例会 DROP 两张表。Rev.J 的做法有两个致命问题：
#   ① 可用性探测用 TDSQL_TEST_* 一套配置，实际 DROP 用 SQLCHECK_DB_* 另一套，
#      两套可以指向完全不同的服务器——探测通了就不 skip，然后往【生产元数据库】
#      执行 DROP；反过来探测不通又会把本可运行的用例错误跳过；
#   ② 唯一的"保护"是 fixture 里的 `os.environ.setdefault("SQLCHECK_DB_NAME", ...)`，
#      它既晚于 database 模块导入（MYSQL_CONFIG 早已按旧值定型），
#      又因为是 setdefault 而**不会覆盖**外部已设的 SQLCHECK_DB_NAME=tdsql_sqlcheck。
#
# Rev.K 的做法：**探测与执行使用同一份已生效的 MYSQL_CONFIG**，并在任何 DROP 之前
# 做失败关闭断言。这是安全底线，不是风格问题——测试代码删掉生产数据是不可接受的。
_APPROVED_TEST_DB = "tdsql_sqlcheck_test"
# 需要用别的库名跑破坏性用例时，必须同时显式设置这两个环境变量。
_ALLOW_DESTRUCTIVE = os.environ.get("G14_ALLOW_DESTRUCTIVE_TESTS") == "1"
_CUSTOM_TEST_DB = os.environ.get("G14_TEST_DB_NAME", "")


def effective_db_config():
    """返回 database 模块【当前真正在用】的连接配置（不是环境变量的快照）。"""
    from backend.services.database import MYSQL_CONFIG
    return dict(MYSQL_CONFIG)


def _probe_metadata_db():
    """用与 DROP 完全相同的配置探测可用性，并真正选中目标库。"""
    cfg = effective_db_config()
    try:
        import pymysql
        conn = pymysql.connect(host=cfg["host"], port=cfg["port"],
                               user=cfg["user"], password=cfg["password"],
                               database=cfg["database"],
                               charset=cfg.get("charset", "utf8mb4"),
                               connect_timeout=3)
        conn.close()
        return True
    except Exception:
        return False


MYSQL_AVAILABLE = _probe_metadata_db()


class DestructiveTargetError(RuntimeError):
    """破坏性测试的目标库不在批准清单内。

    刻意用普通异常而不是 `pytest.fail()`：后者抛的 `Failed` 继承自 BaseException，
    守门人本身就没法被单测覆盖了——而"守门人会不会放行"恰恰是这里最需要被验证的一件事。
    从 fixture 里抛出的异常同样会让用例 error 掉，失败关闭的效果完全一样。
    """


def assert_destructive_target_is_safe():
    """任何 DROP 之前的失败关闭断言。不通过即抛出，绝不"尽力而为"。"""
    cfg = effective_db_config()
    target = str(cfg.get("database") or "")
    allowed = {_APPROVED_TEST_DB}
    if _ALLOW_DESTRUCTIVE and _CUSTOM_TEST_DB:
        allowed.add(_CUSTOM_TEST_DB)
    # 破坏性操作前把目标打印出来——出了事要能立刻看出打的是哪台机器
    print(f"[G14 破坏性测试目标] host={cfg.get('host')} port={cfg.get('port')} "
          f"database={target!r} 允许集合={sorted(allowed)}")
    if target not in allowed:
        raise DestructiveTargetError(
            f"拒绝在非批准的数据库上执行 DROP：当前 SQLCHECK_DB_NAME={target!r}，"
            f"仅允许 {sorted(allowed)}。"
            f"如确需在自定义库上跑破坏性用例，请同时设置 "
            f"G14_ALLOW_DESTRUCTIVE_TESTS=1 与 G14_TEST_DB_NAME=<库名>。")
    return cfg


# ══════════════════════════════════════════════════════════════════
# 测试替身
# ══════════════════════════════════════════════════════════════════
class FakePool:
    """脚本化连接池替身。

    databases   : SHOW DATABASES 返回的库名（含系统库）
    info_schema : {db: {"base":[...], "view":[...]}}
    per_db      : {(当前库, sql): 行列表 或 Exception}
    show_db_fail: SHOW DATABASES 抛出的异常（P2-01 用）

    Rev.G / P1-04：忠实复刻 TDSQLConnectionPool.get_connection() 的重建语义
    （tdsql_connector.py:287-307）——异常【穿出】with 才会关闭并重建线程本地连接。
    generation 记录重建次数，conn_ids 记录每库实际拿到的连接代次，
    用来断言"坏连接没有被后续库复用"。
    """

    def __init__(self, databases=None, info_schema=None, per_db=None,
                 select_db_fail=None, show_db_fail=None):
        self.config = TDSQLConnectionConfig(host="h", port=3306, user="u",
                                            password="p", database="d")
        self.databases = databases or []
        self.info_schema = info_schema or {}
        self.per_db = per_db or {}
        self.select_db_fail = select_db_fail or {}
        self.show_db_fail = show_db_fail
        self.seen, self.selected = [], []
        self.current_db = ""
        self.closed = False
        self.made_with_read_timeout = None
        self.generation = 0          # 连接重建次数
        self.create_calls = 0        # _create_connection() 被调用次数（Rev.K / P1-02）
        self.reconnect_fail = None   # 令重建失败，验证信号不被覆盖
        self.ctx_count = 0           # get_connection() 进入次数
        self.conn_ids = []           # [(db, 该库拿到的连接代次)]

    def _execute(self, sql, params=None):
        self.seen.append(sql)
        if sql == "SHOW DATABASES":
            if self.show_db_fail is not None:
                raise self.show_db_fail
            return [{"Database": d} for d in self.databases]
        if "information_schema.TABLES" in sql:
            wanted = set(params or ())
            out = []
            for db, kinds in self.info_schema.items():
                if wanted and db not in wanted:
                    continue
                for n in kinds.get("base", []):
                    out.append({"TABLE_SCHEMA": db, "TABLE_NAME": n,
                                "TABLE_TYPE": "BASE TABLE"})
                for n in kinds.get("view", []):
                    out.append({"TABLE_SCHEMA": db, "TABLE_NAME": n,
                                "TABLE_TYPE": "VIEW"})
            return out
        return []

    def get_connection(self):
        pool = self
        pool.ctx_count += 1

        class _Cursor:
            def __enter__(self_i):
                return self_i

            def __exit__(self_i, *a):
                return False

            def execute(self_i, sql, params=None):
                pool.seen.append(sql)
                val = pool.per_db.get((pool.current_db, sql))
                if isinstance(val, Exception):
                    self_i._rows = []
                    raise val
                self_i._rows = val or []

            def fetchall(self_i):
                return getattr(self_i, "_rows", [])

        class _Conn:
            generation = pool.generation

            def select_db(self_i, db):
                pool.selected.append(db)
                pool.conn_ids.append((db, self_i.generation))
                if db in pool.select_db_fail:
                    raise pool.select_db_fail[db]
                pool.current_db = db

            def cursor(self_i):
                return _Cursor()

        class _Ctx:
            def __enter__(self_i):
                return _Conn()

            def __exit__(self_i, exc_type, exc, tb):
                if exc_type is not None:
                    # 异常穿出 ⇒ 关闭旧连接并【真的】重建（真实池的行为）。
                    # Rev.K / P1-02：这里必须调用 _create_connection()，否则
                    # "预算控制信号触发了一次重连"这种问题在测试里根本看不出来
                    # ——Rev.J 的 FakePool 只把 generation += 1，是个哑动作。
                    pool._create_connection()
                return False

        return _Ctx()

    def _create_connection(self):
        """真实池在异常穿出上下文时会调用它（tdsql_connector.py:298-306）。"""
        self.create_calls += 1
        self.generation += 1
        if self.reconnect_fail is not None:
            raise self.reconnect_fail
        return object()

    def close_all(self):
        self.closed = True


def _rows(qualified_names, col="db_table", info=None):
    """按实测形态构造行：列名 db_table，值为 db.table；info 为可选第二列。"""
    if info is None:
        return [{col: n} for n in qualified_names]
    return [{col: n, "info": info} for n in qualified_names]


def _mysql_error(errno, msg):
    return Exception(errno, msg)


def _patch_ctx(monkeypatch, itype, source="probed", conflict=False):
    from backend.models import InstanceType, TypeSource
    from backend.services.instance_type_service import (InstanceContext,
                                                        instance_type_service)
    monkeypatch.setattr(
        instance_type_service, "resolve",
        lambda cid="", requested=None: InstanceContext(
            InstanceType(itype), TypeSource(source), conflict=conflict))


def by_db_detail(res):
    """{库名: 明细说明}，Rev.G 起逐库失败原因只在 item.detail 里（P1-07）。"""
    return {i["db_name"]: i["detail"] for i in res["items"]}


class FakeClock:
    """可控单调时钟（Rev.J / P1-02）。每次读秒推进 step 秒，可手工 advance。

    用它代替 sleep：既让 deadline 行为可断言，又不让测试真的等 180 秒。
    """

    def __init__(self, start=1000.0, step=0.0):
        self.t = float(start)
        self.step = float(step)
        self.reads = 0

    def __call__(self):
        self.reads += 1
        v = self.t
        self.t += self.step
        return v

    def advance(self, secs):
        self.t += float(secs)


def _patch_tmp_pool(monkeypatch, pool):
    """让 _collect_distributed 复用同一个 FakePool（ADR-3 的可测性钩子）"""
    def _factory(cfg, pool_size=1):
        pool.made_with_read_timeout = cfg.read_timeout
        return pool
    monkeypatch.setattr(svc, "_new_pool", _factory)


# ══════════════════════════════════════════════════════════════════
# 常量护栏
# ══════════════════════════════════════════════════════════════════
def test_sql_constants_verbatim():
    """三条命令逐字等于原厂文本（ADR-10）"""
    assert svc.SQL_SHARD == "/*proxy*/show table with shardkey"
    assert svc.SQL_BROADCAST == "/*proxy*/show table with noshardkey_allset"
    assert svc.SQL_SINGLE == "/*proxy*/show table without shardkey"
    for sql in (svc.SQL_SHARD, svc.SQL_BROADCAST, svc.SQL_SINGLE):
        assert sql.startswith("/*proxy*/"), "必须保留 /*proxy*/ 前缀"
        assert ";" not in sql, "不得附加分号"
        assert sql == sql.strip()


def test_sys_db_is_superset():
    """_SYS_DB 必须同时是项目内两套系统库清单的超集（ADR-8）"""
    from backend.services.index_audit_service import _SYS
    from backend.services.zk_scan_enrich_service import SYSTEM_DATABASES
    assert {s.lower() for s in _SYS} <= svc._SYS_DB
    assert {s.lower() for s in SYSTEM_DATABASES} <= svc._SYS_DB


# ══════════════════════════════════════════════════════════════════
# 形态解析（锚定内网实测：db_table + 库限定名）
# ══════════════════════════════════════════════════════════════════
def test_pick_column_prefers_db_table_over_info():
    """实测形态：with shardkey 返回 db_table + info 两列，必须取 db_table"""
    col, guessed = svc._pick_name_column(["db_table", "info"])
    assert col == "db_table" and guessed is False


@pytest.mark.parametrize("rows,expect", [
    # 实测：without shardkey 单列
    (_rows(["sqltuning.t_max", "sqltuning.txt"]),
     {("sqltuning", "t_max"), ("sqltuning", "txt")}),
    # 实测：with shardkey 双列
    (_rows(["sqltuning.t1"], info="shardkey:id"), {("sqltuning", "t1")}),
    # 实测：with noshardkey_allset 双列
    (_rows(["sqltuning.kcda_bcast"], info="shardkey:noshardkey_allset"),
     {("sqltuning", "kcda_bcast")}),
    # 反引号
    ([{"db_table": "`sqltuning`.`t_max`"}], {("sqltuning", "t_max")}),
    # 空结果
    ([], set()),
])
def test_extract_pairs_real_shapes(rows, expect):
    pairs, _c, guessed, _x, _a = svc._extract_pairs(
        rows, "sqltuning", svc._NameSpace(["sqltuning", "mysql"]))
    assert pairs == expect and guessed is False


def test_extract_pairs_detects_cross_database_rows():
    """结果含其他库 ⇒ 命令作用域为实例级（RISK-E）"""
    pairs, _c, _g, cross, _a = svc._extract_pairs(
        _rows(["db_a.t1", "db_b.t2"]), "db_a", svc._NameSpace(["db_a", "db_b"]))
    assert cross is True
    assert pairs == {("db_a", "t1"), ("db_b", "t2")}


def test_extract_pairs_keeps_dotted_table_name():
    """点号左侧不是已知库名时不得拆分——避免误拆后被过滤掉（少算）"""
    pairs, _c, _g, cross, _a = svc._extract_pairs(
        [{"db_table": "odd.name"}], "db_a", svc._NameSpace(["db_a", "db_b"]))
    assert pairs == {("db_a", "odd.name")} and cross is False


def test_extract_pairs_unknown_shape():
    pairs, columns, guessed, _x, _a = svc._extract_pairs(
        [{"col_x": "t_a", "col_y": 1}], "db", svc._NameSpace(["db"]))
    assert pairs == {("db", "t_a")} and guessed is True
    assert columns == ["col_x", "col_y"]


# ══════════════════════════════════════════════════════════════════
# 业务库枚举
# ══════════════════════════════════════════════════════════════════
def test_business_databases_filter_system():
    pool = FakePool(databases=["db_a", "mysql", "sysdb", "xa",
                               "information_schema", "tdsqlpcloud", "db_b"])
    dbs, truncated, allnames = svc.list_business_databases(pool)
    assert dbs == ["db_a", "db_b"] and truncated is False
    assert len(allnames) == 7          # known_dbs 必须含系统库


def test_business_databases_truncation_is_visible(monkeypatch):
    monkeypatch.setattr(svc, "MAX_DATABASES", 2)
    _patch_ctx(monkeypatch, "centralized")
    pool = FakePool(databases=["d1", "d2", "d3"],
                    info_schema={"d1": {"base": ["t"]}, "d2": {"base": ["t"]}})
    res = svc.analyze(pool, connection_id="c1")
    assert any(w["code"] == "TOO_MANY_DATABASES" for w in res["warnings"])


# ══════════════════════════════════════════════════════════════════
# 集中式分支
# ══════════════════════════════════════════════════════════════════
def test_centralized_branch(monkeypatch):
    _patch_ctx(monkeypatch, "centralized")
    pool = FakePool(databases=["db_a", "mysql"],
                    info_schema={"db_a": {"base": ["t1", "t2", "t3"],
                                          "view": ["v1"]}})
    res = svc.analyze(pool, connection_id="c1")
    assert res["instance_type"] == "centralized"
    assert res["single_tables"] == 3 and res["total_tables"] == 3
    assert res["shard_tables"] == 0 and res["broadcast_tables"] == 0
    assert all("/*proxy*/" not in s for s in pool.seen)   # ADR-4
    assert pool.selected == []                            # 不切库


# ══════════════════════════════════════════════════════════════════
# 分布式分支（库限定名口径）
# ══════════════════════════════════════════════════════════════════
def test_distributed_happy_path(monkeypatch):
    """完全照搬内网 sqltuning 实测的三份结果形态"""
    _patch_ctx(monkeypatch, "distributed")
    per_db = {
        ("sqltuning", svc.SQL_SHARD): _rows(
            ["sqltuning.t1", "sqltuning.ts"], info="shardkey:id"),
        ("sqltuning", svc.SQL_BROADCAST): _rows(
            ["sqltuning.kcda_bcast"], info="shardkey:noshardkey_allset"),
        ("sqltuning", svc.SQL_SINGLE): _rows(
            ["sqltuning.t_max", "sqltuning.txt"]),
    }
    pool = FakePool(databases=["sqltuning", "mysql"],
                    info_schema={"sqltuning": {"base": [
                        "t1", "ts", "kcda_bcast", "t_max", "txt"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert (res["shard_tables"], res["broadcast_tables"],
            res["single_tables"], res["total_tables"]) == (2, 1, 2, 5)
    assert res["warnings"] == []
    assert pool.closed is True
    assert pool.made_with_read_timeout == svc.COMMAND_READ_TIMEOUT


def test_distributed_instance_wide_scope(monkeypatch):
    """实例级作用域：按库归属拆分、(库,表) 去重；Rev.G 起【不再提前停止】。

    判据不依赖 information_schema —— 实测 lzbj_ecif 三类并集 215 vs 基线 293，
    用"并集 == 基线"做判据会永远不成立（ADR-12 修订的由来）。
    """
    _patch_ctx(monkeypatch, "distributed")
    allrows_shard = _rows(["db_a.s1", "db_b.s2"], info="shardkey:id")
    allrows_bcast = _rows(["db_b.b1"], info="shardkey:noshardkey_allset")
    allrows_single = _rows(["db_a.n1"])
    per_db = {}
    for d in ("db_a", "db_b", "db_c"):
        per_db[(d, svc.SQL_SHARD)] = allrows_shard
        per_db[(d, svc.SQL_BROADCAST)] = allrows_bcast
        per_db[(d, svc.SQL_SINGLE)] = allrows_single
    pool = FakePool(databases=["db_a", "db_b", "db_c", "mysql"],
                    info_schema={"db_a": {"base": ["s1", "n1"]},
                                 "db_b": {"base": ["s2", "b1"]},
                                 "db_c": {"base": []}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    # 总数按 (库,表) 去重，不是每个库各算一遍
    assert res["total_tables"] == 4
    assert (res["shard_tables"], res["broadcast_tables"],
            res["single_tables"]) == (2, 1, 1)
    by_db = {i["db_name"]: i for i in res["items"]}
    assert by_db["db_a"]["total_tables"] == 2
    assert by_db["db_b"]["total_tables"] == 2
    # Rev.G / P1-01：即使前两库指纹相同，也必须把 db_c 也执行一遍
    assert pool.selected == ["db_a", "db_b", "db_c"]
    assert any(w["code"] == "INSTANCE_WIDE_SCOPE" for w in res["warnings"])


def test_r01_identical_fingerprint_must_not_skip_third_db(monkeypatch):
    """T-R01 / P1-01：前两库返回集合相同，但第三库另有表，不得提前停止。

    这正是 O 指出的反例：db_a、db_b 指纹相同只证明"换默认库没改变当前账号
    看到的集合"，不证明这个集合覆盖了 db_c。Rev.F 的提前停止会把 db_c 的
    2 张表整个漏掉，且页面四个主数字仍显示为"成功"。
    """
    _patch_ctx(monkeypatch, "distributed")
    shared = _rows(["db_a.s1", "db_b.s2"], info="shardkey:id")
    per_db = {}
    for d in ("db_a", "db_b"):
        per_db[(d, svc.SQL_SHARD)] = shared
        per_db[(d, svc.SQL_BROADCAST)] = []
        per_db[(d, svc.SQL_SINGLE)] = []
    # db_c 属于另一路由域：前两库看不到它，只有切到 db_c 才返回
    per_db[("db_c", svc.SQL_SHARD)] = _rows(["db_c.s3"], info="shardkey:id")
    per_db[("db_c", svc.SQL_BROADCAST)] = _rows(
        ["db_c.b3"], info="shardkey:noshardkey_allset")
    per_db[("db_c", svc.SQL_SINGLE)] = []
    pool = FakePool(databases=["db_a", "db_b", "db_c"],
                    info_schema={"db_a": {"base": ["s1"]},
                                 "db_b": {"base": ["s2"]},
                                 "db_c": {"base": ["s3", "b3"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert pool.selected == ["db_a", "db_b", "db_c"], "不得因指纹相同跳过 db_c"
    assert res["total_tables"] == 4
    by_db = {i["db_name"]: i for i in res["items"]}
    assert by_db["db_c"]["total_tables"] == 2
    assert by_db["db_c"]["status"] == "OK"
    # 且不得留下"仅基线可见"的漏表告警——说明确实采到了
    assert not any(w["code"] == "RECON_MISMATCH" for w in res["warnings"])


def test_distributed_per_db_scope_still_loops(monkeypatch):
    """命令若为当前库作用域，两库指纹不同，必须逐库执行"""
    _patch_ctx(monkeypatch, "distributed")
    per_db = {
        ("db_a", svc.SQL_SHARD): _rows(["db_a.s1"]),
        ("db_a", svc.SQL_BROADCAST): [],
        ("db_a", svc.SQL_SINGLE): [],
        ("db_b", svc.SQL_SHARD): _rows(["db_b.s2"]),
        ("db_b", svc.SQL_BROADCAST): [],
        ("db_b", svc.SQL_SINGLE): [],
    }
    pool = FakePool(databases=["db_a", "db_b"],
                    info_schema={"db_a": {"base": ["s1"]},
                                 "db_b": {"base": ["s2"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert pool.selected == ["db_a", "db_b"]
    assert res["total_tables"] == 2
    assert res["baseline_tables"] == 2
    assert not any(w["code"] == "INSTANCE_WIDE_SCOPE" for w in res["warnings"])


def test_single_database_filter_ignores_other_dbs(monkeypatch):
    """指定库时，实例级结果中其他库的表必须被排除"""
    _patch_ctx(monkeypatch, "distributed")
    rows = _rows(["db_a.s1", "db_b.s2", "db_b.s3"])
    pool = FakePool(databases=["db_a", "db_b"],
                    info_schema={"db_a": {"base": ["s1"]}},
                    per_db={("db_a", svc.SQL_SHARD): rows,
                            ("db_a", svc.SQL_BROADCAST): [],
                            ("db_a", svc.SQL_SINGLE): []})
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1", database="db_a")
    assert res["total_tables"] == 1 and res["shard_tables"] == 1


def test_system_db_rows_are_dropped(monkeypatch):
    """实例级结果里的系统库表不得计入"""
    _patch_ctx(monkeypatch, "distributed")
    rows = _rows(["db_a.s1", "mysql.user", "sysdb.foo"])
    pool = FakePool(databases=["db_a", "mysql", "sysdb"],
                    info_schema={"db_a": {"base": ["s1"]}},
                    per_db={("db_a", svc.SQL_SHARD): rows,
                            ("db_a", svc.SQL_BROADCAST): [],
                            ("db_a", svc.SQL_SINGLE): []})
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert res["total_tables"] == 1


def test_distributed_view_is_excluded(monkeypatch):
    """原厂"不统计视图"——即使命令返回了视图也必须扣除"""
    _patch_ctx(monkeypatch, "distributed")
    per_db = {
        ("db_a", svc.SQL_SHARD): _rows(["db_a.s1"]),
        ("db_a", svc.SQL_BROADCAST): [],
        ("db_a", svc.SQL_SINGLE): _rows(["db_a.n1", "db_a.v1"]),
    }
    pool = FakePool(databases=["db_a"],
                    info_schema={"db_a": {"base": ["s1", "n1"],
                                          "view": ["v1"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert res["single_tables"] == 1 and res["total_tables"] == 2
    assert not any(w["code"] == "RECON_MISMATCH" for w in res["warnings"])


def test_distributed_overlap_does_not_double_count(monkeypatch):
    """若某版本 without shardkey 含广播表（RISK-A），总数不得重复计算"""
    _patch_ctx(monkeypatch, "distributed")
    per_db = {
        ("db_a", svc.SQL_SHARD): _rows(["db_a.s1"]),
        ("db_a", svc.SQL_BROADCAST): _rows(["db_a.b1"]),
        ("db_a", svc.SQL_SINGLE): _rows(["db_a.b1", "db_a.n1"]),
    }
    pool = FakePool(databases=["db_a"],
                    info_schema={"db_a": {"base": ["s1", "b1", "n1"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert res["total_tables"] == 3 and res["single_tables"] == 1
    assert res["broadcast_tables"] == 1 and res["overlap_count"] == 1
    assert any(w["code"] == "KIND_OVERLAP" for w in res["warnings"])


def test_distributed_recon_mismatch(monkeypatch):
    """并集与 information_schema 不一致时，差异明细必须落到该库的 detail 上"""
    _patch_ctx(monkeypatch, "distributed")
    per_db = {
        ("db_a", svc.SQL_SHARD): _rows(["db_a.s1"]),
        ("db_a", svc.SQL_BROADCAST): [],
        ("db_a", svc.SQL_SINGLE): _rows(["db_a.n1"]),
    }
    pool = FakePool(databases=["db_a"],
                    info_schema={"db_a": {"base": ["s1", "n1", "ghost"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    w = [x for x in res["warnings"] if x["code"] == "RECON_MISMATCH"]
    assert len(w) == 1 and "db_a" in w[0]["detail"]
    assert "ghost" in res["items"][0]["detail"]
    assert res["baseline_tables"] == 3 and res["total_tables"] == 2


def test_recon_mismatch_is_aggregated_not_per_db(monkeypatch):
    """实测 lzbj_ecif 差 78/293 —— 差异会出现在每个库上，告警必须汇总成一条"""
    _patch_ctx(monkeypatch, "distributed")
    per_db, info = {}, {}
    for d in ("db_a", "db_b", "db_c"):
        per_db[(d, svc.SQL_SHARD)] = _rows([d + ".s1"])
        per_db[(d, svc.SQL_BROADCAST)] = []
        per_db[(d, svc.SQL_SINGLE)] = []
        info[d] = {"base": ["s1", "ghost1", "ghost2"]}
    pool = FakePool(databases=["db_a", "db_b", "db_c"],
                    info_schema=info, per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    w = [x for x in res["warnings"] if x["code"] == "RECON_MISMATCH"]
    assert len(w) == 1, "三个库都不一致，也只能出一条告警"
    assert "3 个库" in w[0]["detail"] and "6" in w[0]["detail"]
    assert all("ghost" in i["detail"] for i in res["items"])
    assert res["total_tables"] == 3 and res["baseline_tables"] == 9


def test_baseline_excludes_tdsql_subpartitions(monkeypatch):
    """information_schema 里的二级分区物理子表不得计入逻辑基线（实测命名形态）"""
    _patch_ctx(monkeypatch, "distributed")
    subp = ["cus_pub_translog_tdsql_subp190001"] + [
        f"cus_pub_translog_tdsql_subp2026{m:02d}" for m in range(1, 13)]
    per_db = {("db_a", svc.SQL_SHARD): _rows(["db_a.cus_pub_translog"]),
              ("db_a", svc.SQL_BROADCAST): [],
              ("db_a", svc.SQL_SINGLE): []}
    pool = FakePool(databases=["db_a"],
                    info_schema={"db_a": {"base": ["cus_pub_translog"] + subp}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert res["baseline_tables"] == 1          # 逻辑基线只算 1 张
    assert res["subpartition_tables"] == 13     # 13 个子分区单列
    assert res["total_tables"] == 1
    # 剔除后两个口径对齐 ⇒ 不得再报 RECON_MISMATCH
    assert not any(w["code"] == "RECON_MISMATCH" for w in res["warnings"])
    assert any(w["code"] == "SUBPARTITION_EXCLUDED" for w in res["warnings"])


def test_subpartition_regex_is_anchored():
    """只剔除以 _tdsql_subp<纯数字> 结尾的表，不误伤用户自建表"""
    # 实测形态
    assert svc._SUBPARTITION_RE.search("cus_pub_translog_tdsql_subp190001")
    assert svc._SUBPARTITION_RE.search("cus_pub_updatelog_detail_tdsql_subp202612")
    # 逻辑表本身不能被剔除
    assert not svc._SUBPARTITION_RE.search("cus_pub_translog")
    assert not svc._SUBPARTITION_RE.search("cus_pub_translog_his")
    # 用户自建的、后面还有后缀的表不能被误伤
    assert not svc._SUBPARTITION_RE.search("my_tdsql_subp202601_backup")
    assert not svc._SUBPARTITION_RE.search("tdsql_subp")
    assert not svc._SUBPARTITION_RE.search("t_tdsql_subp_manual")


# 内网 lzbj_ecif 的 6 张按月二级分区父表（T17 实测，2026-08-31）。
# 注意 cus_pub_updatelog 与 cus_pub_updatelog_detail 互为前缀——这是真实数据里
# 存在的形态，父表推导必须把两者分开，不能把 _detail 的子表算到 cus_pub_updatelog 头上。
_UAT_PARENTS = ("cus_bas_merge_log", "cus_pub_sync_consumer_log",
                "cus_pub_sync_log", "cus_pub_translog",
                "cus_pub_updatelog", "cus_pub_updatelog_detail")
_UAT_SUFFIXES = ("190001",) + tuple(f"2026{m:02d}" for m in range(1, 13))
_UAT_SUBP = tuple(f"{p}_tdsql_subp{s}"
                  for p in _UAT_PARENTS for s in _UAT_SUFFIXES)


def test_r07b_real_intranet_names_derive_exactly_six_parents():
    """T17 实测锚点：78 张真实子表必须推导出【正好 6 个】父表，每个 13 张。

    这条用真名而不是构造名，是因为真实数据里有一个构造夹具想不到的形态：
    cus_pub_updatelog 与 cus_pub_updatelog_detail 互为前缀，且【两者都是父表】。
    父表推导若写成贪婪或按第一个 _tdsql_subp 之前的最短前缀切，
    cus_pub_updatelog_detail 的 13 张子表就会被算到 cus_pub_updatelog 头上，
    于是 cus_pub_updatelog_detail 变成"父表未确认"，13 张子表回流进逻辑基线，
    UAT 的 215/78 变成 228/65 —— 数字错了，而且错得很像对的。
    """
    assert len(_UAT_SUBP) == 78
    parents = {}
    for name in _UAT_SUBP:
        m = svc._SUBPARTITION_RE.match(name)
        assert m, f"正则未命中真实子表名: {name}"
        parents[m.group("parent")] = parents.get(m.group("parent"), 0) + 1
    assert set(parents) == set(_UAT_PARENTS)
    assert set(parents.values()) == {13}
    # 与内网 SQL 侧 SUBSTRING_INDEX(TABLE_NAME,'_tdsql_subp',1) 的口径一致
    assert set(parents) == {n.split("_tdsql_subp")[0] for n in _UAT_SUBP}


def test_r07c_nested_prefix_parents_are_not_confused():
    """前缀嵌套的两张父表必须各归各的（T17 实测形态）。"""
    a = svc._SUBPARTITION_RE.match("cus_pub_updatelog_tdsql_subp202601")
    b = svc._SUBPARTITION_RE.match("cus_pub_updatelog_detail_tdsql_subp202601")
    assert a.group("parent") == "cus_pub_updatelog"
    assert b.group("parent") == "cus_pub_updatelog_detail"
    # 只确认了短的那个父表时，_detail 的子表【不得】被剔除
    base = {"cus_pub_updatelog", "cus_pub_updatelog_detail",
            "cus_pub_updatelog_tdsql_subp202601",
            "cus_pub_updatelog_detail_tdsql_subp202601"}
    logical, subp = svc._classify_subpartitions(base, {"cus_pub_updatelog"})
    assert subp == {"cus_pub_updatelog_tdsql_subp202601"}
    assert "cus_pub_updatelog_detail_tdsql_subp202601" in logical


def test_r07d_uat_parent_confirmation_is_all_or_nothing_per_parent(monkeypatch):
    """T17 端到端：6 个父表全确认 → 剔 78；缺一个父表 → 只剔 65 且差异显式报出。

    后一半正是"父表确认"这条规则的兜底方向：宁可多报一次 RECON_MISMATCH（可见），
    也不静默少算（不可见）。
    """
    base = set(_UAT_PARENTS) | set(_UAT_SUBP)
    logical, subp = svc._classify_subpartitions(base, set(_UAT_PARENTS))
    assert (len(logical), len(subp)) == (6, 78)

    partial = set(_UAT_PARENTS) - {"cus_pub_updatelog_detail"}
    logical2, subp2 = svc._classify_subpartitions(base, partial)
    assert len(subp2) == 65
    assert len(logical2) == 19          # 6 个父表 + 回流的 13 张子表
    assert "cus_pub_updatelog_detail_tdsql_subp202612" in logical2


def test_lzbj_ecif_uat_baseline(monkeypatch):
    """端到端对数基准：内网 lzbj_ecif 实测（设计附录 B.5）。

    Proxy: 98 分片 + 117 广播 + 0 单表 = 215
    information_schema: 293 = 逻辑 215 + 二级分区子表 78（6 张按月分区表 × 13）
    期望：总表 215 / 单表 0 / 广播 117 / 分片 98 / 逻辑基线 215 / 子分区 78，
          且【不报】RECON_MISMATCH。
    """
    _patch_ctx(monkeypatch, "distributed")
    shard = [f"t_shard_{i}" for i in range(98)]
    bcast = [f"t_bcast_{i}" for i in range(117)]
    month_tables = shard[:6]                     # 其中 6 张是按月二级分区
    subp = [f"{t}_tdsql_subp190001" for t in month_tables]
    for t in month_tables:
        subp += [f"{t}_tdsql_subp2026{m:02d}" for m in range(1, 13)]
    assert len(subp) == 78
    per_db = {
        ("lzbj_ecif", svc.SQL_SHARD): _rows([f"lzbj_ecif.{t}" for t in shard],
                                            info="shardkey:id"),
        ("lzbj_ecif", svc.SQL_BROADCAST): _rows([f"lzbj_ecif.{t}" for t in bcast],
                                                info="shardkey:noshardkey_allset"),
        ("lzbj_ecif", svc.SQL_SINGLE): None,      # OK 包：该库无单表
    }
    pool = FakePool(databases=["lzbj_ecif"],
                    info_schema={"lzbj_ecif": {"base": shard + bcast + subp}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert res["shard_tables"] == 98
    assert res["broadcast_tables"] == 117
    assert res["single_tables"] == 0
    assert res["total_tables"] == 215
    assert res["baseline_tables"] == 215          # 293 - 78
    assert res["subpartition_tables"] == 78
    assert not any(w["code"] == "RECON_MISMATCH" for w in res["warnings"]), \
        "剔除子分区后两个口径应精确对齐，不得常态告警"
    codes = {w["code"] for w in res["warnings"]}
    assert codes == {"SUBPARTITION_EXCLUDED"}


def test_distributed_partial_failure(monkeypatch):
    """单库失败只降级该库：不计入总数、单列计数、其余库照常（ADR-5）"""
    _patch_ctx(monkeypatch, "distributed")
    per_db = {
        ("db_a", svc.SQL_SHARD): _rows(["db_a.s1"]),
        ("db_a", svc.SQL_BROADCAST): [],
        ("db_a", svc.SQL_SINGLE): [],
        ("db_b", svc.SQL_SHARD): _mysql_error(1142, "SELECT command denied"),
        ("db_c", svc.SQL_SHARD): _rows(["db_c.s9"]),
        ("db_c", svc.SQL_BROADCAST): [],
        ("db_c", svc.SQL_SINGLE): [],
    }
    pool = FakePool(databases=["db_a", "db_b", "db_c"],
                    info_schema={"db_a": {"base": ["s1"]},
                                 "db_b": {"base": ["x"]},
                                 "db_c": {"base": ["s9"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert res["failed_databases"] == 1
    assert res["total_tables"] == 2
    statuses = {i["db_name"]: i["status"] for i in res["items"]}
    assert statuses == {"db_a": "OK", "db_b": "FAILED", "db_c": "OK"}
    # Rev.G / P1-07：失败库汇总为【一条】告警，逐库原因下沉到 item.detail
    w = [x for x in res["warnings"] if x["code"] == "PROXY_CMD_FAILED"]
    assert len(w) == 1
    assert "1 个库采集失败" in w[0]["detail"] and "db_b" in w[0]["detail"]
    assert "授权不足" in by_db_detail(res)["db_b"]


def test_command_timeout_is_reported_not_hung(monkeypatch):
    """命令挂起被读超时截断，渲染为可读提示（RISK-F）"""
    _patch_ctx(monkeypatch, "distributed")
    per_db = {("db_a", svc.SQL_SINGLE): Exception("Read timed out"),
              ("db_a", svc.SQL_SHARD): _rows(["db_a.s1"]),
              ("db_a", svc.SQL_BROADCAST): []}
    pool = FakePool(databases=["db_a"], info_schema={"db_a": {"base": ["s1"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert res["failed_databases"] == 1
    assert "读超时" in res["items"][0]["detail"]


def test_time_budget_skips_remaining(monkeypatch):
    """超预算的库标 SKIPPED、不计入总数，并显式告警"""
    _patch_ctx(monkeypatch, "distributed")
    clock = FakeClock()
    monkeypatch.setattr(svc, "_now", clock)
    pool = FakePool(databases=["db_a", "db_b"],
                    info_schema={"db_a": {"base": ["s1"]},
                                 "db_b": {"base": ["s2"]}},
                    per_db={})
    _patch_tmp_pool(monkeypatch, pool)
    # 前置检查（枚举库、基线）都在预算内，进入逐库循环前把时钟推过 deadline
    deadline = clock.t + svc.TOTAL_BUDGET_SECONDS
    orig = svc._collect_baseline

    def _slow_baseline(p, dbs, known):
        out = orig(p, dbs, known)
        clock.advance(svc.TOTAL_BUDGET_SECONDS + 1)     # 基线查询"耗尽"了预算
        return out

    monkeypatch.setattr(svc, "_collect_baseline", _slow_baseline)
    res = svc.analyze(pool, connection_id="c1", deadline=deadline)
    assert res["skipped_databases"] == 2 and res["total_tables"] == 0
    assert all(i["status"] == "SKIPPED" for i in res["items"])
    assert any(w["code"] == "TIME_BUDGET_EXCEEDED" for w in res["warnings"])
    # P1-03：全部 SKIPPED 时，声明为"不计入汇总"的数一律为 0
    assert res["baseline_tables"] == 0
    assert res["subpartition_tables"] == 0
    assert res["overlap_count"] == 0


def test_distributed_all_1064_flags_wrong_endpoint(monkeypatch):
    _patch_ctx(monkeypatch, "distributed")
    per_db = {("db_a", svc.SQL_SHARD): _mysql_error(1064, "syntax error")}
    pool = FakePool(databases=["db_a"], info_schema={"db_a": {"base": []}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert any(w["code"] == "NOT_DISTRIBUTED_ENDPOINT" for w in res["warnings"])


def test_select_db_failure_is_isolated(monkeypatch):
    _patch_ctx(monkeypatch, "distributed")
    pool = FakePool(databases=["db_a"], info_schema={"db_a": {"base": []}},
                    select_db_fail={"db_a": _mysql_error(1049, "Unknown database")})
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert res["failed_databases"] == 1
    assert res["items"][0]["status"] == "FAILED"
    assert pool.closed is True


def test_shared_pool_is_never_switched(monkeypatch):
    """ADR-3 护栏：共享池连接上不得发生任何 select_db"""
    _patch_ctx(monkeypatch, "distributed")
    shared = FakePool(databases=["db_a"],
                      info_schema={"db_a": {"base": ["s1"]}})
    tmp = FakePool(databases=["db_a"], info_schema={"db_a": {"base": ["s1"]}},
                   per_db={("db_a", svc.SQL_SHARD): _rows(["db_a.s1"]),
                           ("db_a", svc.SQL_BROADCAST): [],
                           ("db_a", svc.SQL_SINGLE): []})
    _patch_tmp_pool(monkeypatch, tmp)
    svc.analyze(shared, connection_id="c1")
    assert shared.selected == []
    assert tmp.selected == ["db_a"]


def test_empty_result_set_is_not_an_error(monkeypatch):
    """实测：lzbj_ecif 无单表。空结果集必须是合法的 0，不得报错"""
    _patch_ctx(monkeypatch, "distributed")
    per_db = {("db_a", svc.SQL_SHARD): _rows(["db_a.s1"]),
              ("db_a", svc.SQL_BROADCAST): [],
              ("db_a", svc.SQL_SINGLE): []}
    pool = FakePool(databases=["db_a"], info_schema={"db_a": {"base": ["s1"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert res["single_tables"] == 0 and res["broadcast_tables"] == 0
    assert res["shard_tables"] == 1 and res["warnings"] == []


def test_extract_pairs_tolerates_none_rows():
    """OK 包路径的防御：即使驱动回 None 也不得抛异常（PyMySQL>=1.1.0 回 []）"""
    pairs, columns, guessed, cross, _a = svc._extract_pairs(
        None, "db_a", svc._NameSpace(["db_a"]))
    assert pairs == set() and columns == [] and guessed is False and cross is False


def test_ok_packet_yields_zero_without_warning(monkeypatch):
    """实测 lzbj_ecif：without shardkey 返回 OK 包（0 行）。

    该类必须计 0、不得告警、不得进 shape，也不得让该库降级为 FAILED。
    """
    _patch_ctx(monkeypatch, "distributed")
    per_db = {("db_a", svc.SQL_SHARD): _rows(["db_a.s1"], info="shardkey:id"),
              ("db_a", svc.SQL_BROADCAST): _rows(["db_a.b1"],
                                                 info="shardkey:noshardkey_allset"),
              ("db_a", svc.SQL_SINGLE): None}          # OK 包 → fetchall() -> []
    pool = FakePool(databases=["db_a"],
                    info_schema={"db_a": {"base": ["s1", "b1"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert res["single_tables"] == 0
    assert (res["shard_tables"], res["broadcast_tables"],
            res["total_tables"]) == (1, 1, 2)
    assert res["failed_databases"] == 0
    assert res["warnings"] == []
    assert "single" not in res["shape"]          # OK 包无列元数据


def test_counts_are_consistent():
    """恒等式 total == shard + broadcast + single，随机 200 组"""
    rnd = random.Random(20260829)
    names = [f"t{i}" for i in range(30)]

    def mk(k):
        return _rows([f"db_a.{n}" for n in rnd.sample(names, k)])

    for _ in range(200):
        per_db = {("db_a", svc.SQL_SHARD): mk(rnd.randint(0, 10)),
                  ("db_a", svc.SQL_BROADCAST): mk(rnd.randint(0, 10)),
                  ("db_a", svc.SQL_SINGLE): mk(rnd.randint(0, 10))}
        pool = FakePool(databases=["db_a"], info_schema={"db_a": {"base": names}},
                        per_db=per_db)
        svc._new_pool_backup = svc._new_pool
        svc._new_pool = lambda cfg, pool_size=1, _p=pool: _p
        try:
            items, _w, _s, totals = svc._collect_distributed(
                pool, ["db_a"], {"db_a": {"base": set(names), "view": set()}},
                svc._NameSpace(["db_a"]),
                time.monotonic() + svc.TOTAL_BUDGET_SECONDS)
        finally:
            svc._new_pool = svc._new_pool_backup
        assert totals["total"] == (totals["shard"] + totals["broadcast"]
                                   + totals["single"])
        assert items[0]["total_tables"] == (items[0]["shard_tables"]
                                            + items[0]["broadcast_tables"]
                                            + items[0]["single_tables"])


def test_no_business_db_warns(monkeypatch):
    _patch_ctx(monkeypatch, "centralized")
    pool = FakePool(databases=["mysql", "sys"])
    res = svc.analyze(pool, connection_id="c1")
    assert any(w["code"] == "NO_BUSINESS_DB" for w in res["warnings"])


def test_unreliable_instance_type_warns(monkeypatch):
    _patch_ctx(monkeypatch, "centralized", source="default")
    pool = FakePool(databases=["db_a"], info_schema={"db_a": {"base": ["t"]}})
    res = svc.analyze(pool, connection_id="")
    assert any(w["code"] == "INSTANCE_TYPE_UNRELIABLE" for w in res["warnings"])


def test_reject_system_database():
    with pytest.raises(ValueError):
        svc.run_stats(FakePool(), connection_id="c1", database="mysql")


# ══════════════════════════════════════════════════════════════════
# Rev.G 定向回归（O 评审报告 §6 的 T-R01…T-R14）
# ══════════════════════════════════════════════════════════════════
def _uniq(prefix):
    """每个并发用例用独立 connection_id：registry 的按连接信号量按 id 缓存，
    复用同一个 id 会把上一个用例的限流配额带进来。"""
    return f"{prefix}-{random.randrange(10**9)}"


def test_r02_same_connection_concurrency_is_rejected(monkeypatch):
    """T-R02 / P1-02：同一连接的第二个请求被服务端限流；槽位在退出后释放。"""
    from backend import config
    from backend.services.connection_registry import registry, ScanBusyError
    monkeypatch.setattr(config, "max_concurrent_scans_per_connection", lambda: 1)
    monkeypatch.setattr(config, "max_concurrent_scans_global", lambda: 8)
    monkeypatch.setattr(svc, "_ensure_schema", lambda: None)
    _patch_ctx(monkeypatch, "centralized")
    pool = FakePool(databases=["db_a"], info_schema={"db_a": {"base": ["t1"]}})
    cid = _uniq("tr02")
    with registry.scan_slot(cid):
        with pytest.raises(ScanBusyError) as e:
            svc.run_stats(pool, connection_id=cid)
        assert "并发已达上限" in str(e.value)
    # 槽位已释放：同一连接可以再次进入
    with registry.scan_slot(cid):
        pass


def test_r02b_slot_is_released_when_collection_raises(monkeypatch):
    """T-R02 / P1-02：采集抛异常时槽位必须释放，不得泄漏成永久占用。"""
    from backend import config
    from backend.services.connection_registry import registry
    monkeypatch.setattr(config, "max_concurrent_scans_per_connection", lambda: 1)
    monkeypatch.setattr(svc, "_ensure_schema", lambda: None)

    def _boom(*a, **k):
        raise RuntimeError("采集炸了")

    monkeypatch.setattr(svc, "analyze", _boom)
    cid = _uniq("tr02b")
    with pytest.raises(RuntimeError):
        svc.run_stats(FakePool(), connection_id=cid)
    with registry.scan_slot(cid):        # 未泄漏
        pass


def test_r03_global_quota_is_shared_with_existing_scans(monkeypatch):
    """T-R03 / P1-02：表类型统计与既有扫描【共用】同一份全局配额。

    这条测试的意义不是"新功能能被限流"，而是"新功能不会另开一份配额"——
    若各算各的，全局上限就形同虚设，正是 O 指出的挤占既有审核/扫描的路径。
    """
    from backend import config
    from backend.services.connection_registry import registry, ScanBusyError
    monkeypatch.setattr(config, "max_concurrent_scans_global", lambda: 1)
    monkeypatch.setattr(config, "max_concurrent_scans_per_connection", lambda: 4)
    monkeypatch.setattr(svc, "_ensure_schema", lambda: None)
    _patch_ctx(monkeypatch, "centralized")
    pool = FakePool(databases=["db_a"], info_schema={"db_a": {"base": ["t1"]}})
    other = _uniq("tr03-other")
    mine = _uniq("tr03-mine")
    # 先由"既有扫描"占满全局槽位（scan_service.py:72 用的就是这个入口）
    with registry.scan_slot(other):
        with pytest.raises(ScanBusyError) as e:
            svc.run_stats(pool, connection_id=mine)
        assert "服务扫描并发已达上限" in str(e.value)
    # 反向：本模块占用时，既有扫描同样被挡住 —— 证明是同一份配额
    monkeypatch.setattr(svc, "analyze",
                        lambda *a, **k: _raise_inside_slot(registry, other))
    with pytest.raises(ScanBusyError):
        svc.run_stats(pool, connection_id=mine)


def _raise_inside_slot(registry, other_cid):
    """在本模块已持有槽位的情况下，模拟既有扫描来抢全局槽位。"""
    with registry.scan_slot(other_cid):
        return {}


def test_r02c_api_maps_scan_busy_to_429(monkeypatch):
    """T-R02 / P1-02：并发超限在 API 层映射为 429（与 tdsql_manage.py:432 同口径）。"""
    from fastapi import HTTPException
    from backend.api import table_type_stats as api
    from backend.services.connection_registry import ScanBusyError

    monkeypatch.setattr(api, "_pool", lambda cid: FakePool())

    def _busy(*a, **k):
        raise ScanBusyError("目标库 c1 扫描并发已达上限(2)，请稍后重试")

    monkeypatch.setattr(api.svc, "run_stats", _busy)
    with pytest.raises(HTTPException) as e:
        api.run(api.StatsRequest(connection_id="c1"), _FakeRequest("alice"))
    assert e.value.status_code == 429
    assert "并发已达上限" in e.value.detail


class _FakeRequest:
    def __init__(self, username=None):
        class _S:
            pass
        self.state = _S()
        if username is not None:
            self.state.username = username


def test_r04_broken_connection_is_rebuilt_before_next_db(monkeypatch):
    """T-R04 / P1-04：首库断链后连接被重建，次库用新连接并正常完成。"""
    _patch_ctx(monkeypatch, "distributed")
    per_db = {
        ("db_a", svc.SQL_SHARD): _mysql_error(
            2013, "Lost connection to MySQL server during query"),
        ("db_b", svc.SQL_SHARD): _rows(["db_b.s2"]),
        ("db_b", svc.SQL_BROADCAST): [],
        ("db_b", svc.SQL_SINGLE): [],
    }
    pool = FakePool(databases=["db_a", "db_b"],
                    info_schema={"db_a": {"base": ["s1"]},
                                 "db_b": {"base": ["s2"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    # 每库一个连接上下文（Rev.F 是全程一个）
    assert pool.ctx_count == 2
    # db_a 的异常穿出了 with ⇒ 真实池会关闭并重建线程本地连接
    assert pool.generation == 1, "异常必须穿出 with，否则坏连接不会被重建"
    gens = dict(pool.conn_ids)
    assert gens["db_a"] == 0 and gens["db_b"] == 1, "db_b 必须用重建后的新连接"
    by_db = {i["db_name"]: i for i in res["items"]}
    assert by_db["db_a"]["status"] == "FAILED"
    assert by_db["db_b"]["status"] == "OK" and by_db["db_b"]["total_tables"] == 1
    assert res["total_tables"] == 1


def test_r04b_read_timeout_also_rebuilds(monkeypatch):
    """T-R04：读超时同样必须穿出 with（超时后连接里可能残留未读结果集）。"""
    _patch_ctx(monkeypatch, "distributed")
    per_db = {("db_a", svc.SQL_SHARD): Exception("Read timed out"),
              ("db_b", svc.SQL_SHARD): _rows(["db_b.s2"]),
              ("db_b", svc.SQL_BROADCAST): [],
              ("db_b", svc.SQL_SINGLE): []}
    pool = FakePool(databases=["db_a", "db_b"],
                    info_schema={"db_a": {"base": ["s1"]},
                                 "db_b": {"base": ["s2"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert pool.generation == 1
    assert "读超时" in by_db_detail(res)["db_a"]
    assert res["total_tables"] == 1


def test_r05_failed_db_partial_result_does_not_pollute(monkeypatch):
    """T-R05 / P1-05：第一条命令返回跨库行、第二条失败 ⇒ 整库丢弃，不污染他库。"""
    _patch_ctx(monkeypatch, "distributed")
    per_db = {
        # db_a 的分片命令返回了实例级结果（含 db_b 的一张幽灵表），随后广播命令失败
        ("db_a", svc.SQL_SHARD): _rows(["db_a.s1", "db_b.ghost"],
                                       info="shardkey:id"),
        ("db_a", svc.SQL_BROADCAST): _mysql_error(1142, "SELECT command denied"),
        ("db_b", svc.SQL_SHARD): _rows(["db_b.s2"], info="shardkey:id"),
        ("db_b", svc.SQL_BROADCAST): [],
        ("db_b", svc.SQL_SINGLE): [],
    }
    pool = FakePool(databases=["db_a", "db_b"],
                    info_schema={"db_a": {"base": ["s1"]},
                                 "db_b": {"base": ["s2", "ghost"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    by_db = {i["db_name"]: i for i in res["items"]}
    assert by_db["db_a"]["status"] == "FAILED"
    # db_b 只保留它自己那一轮扫出来的 s2；db_a 那轮暂存的 ghost 已被整体丢弃
    assert by_db["db_b"]["total_tables"] == 1
    assert by_db["db_b"]["shard_tables"] == 1
    assert res["total_tables"] == 1
    assert res["failed_databases"] == 1
    # ghost 只在基线里，于是被如实报成"仅基线可见"，而不是被脏数据凑成 OK
    assert any(w["code"] == "RECON_MISMATCH" for w in res["warnings"])


def test_r05b_overlap_is_not_polluted_by_failed_db(monkeypatch):
    """T-R05 / P1-05：失败库的暂存结果不得进入重叠数统计。"""
    _patch_ctx(monkeypatch, "distributed")
    per_db = {
        ("db_a", svc.SQL_SHARD): _rows(["db_b.s2"]),      # 与 db_b 自扫结果同一张表
        ("db_a", svc.SQL_BROADCAST): _mysql_error(1142, "denied"),
        ("db_b", svc.SQL_SHARD): [],
        ("db_b", svc.SQL_BROADCAST): _rows(["db_b.s2"]),
        ("db_b", svc.SQL_SINGLE): [],
    }
    pool = FakePool(databases=["db_a", "db_b"],
                    info_schema={"db_a": {"base": []},
                                 "db_b": {"base": ["s2"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert res["overlap_count"] == 0, "失败库的暂存行不得参与重叠判定"
    by_db = {i["db_name"]: i for i in res["items"]}
    assert by_db["db_b"]["broadcast_tables"] == 1
    assert by_db["db_b"]["shard_tables"] == 0


def test_r06_centralized_keeps_legit_subp_named_table(monkeypatch):
    """T-R06 / P1-03：集中式实例的 `orders_tdsql_subp202601` 是合法业务表，必须计入。

    集中式没有二级分区物理子表这一构造，也没有 Proxy 交叉校验兜底——
    按后缀剔除就是静默少算，且不可见（违反 REQ-5）。
    """
    _patch_ctx(monkeypatch, "centralized")
    pool = FakePool(databases=["db_a"],
                    info_schema={"db_a": {"base": [
                        "orders", "orders_tdsql_subp202601",
                        "cus_pub_translog_tdsql_subp190001"]}})
    res = svc.analyze(pool, connection_id="c1")
    assert res["total_tables"] == 3
    assert res["single_tables"] == 3
    assert res["baseline_tables"] == 3
    assert res["subpartition_tables"] == 0
    assert not any(w["code"] == "SUBPARTITION_EXCLUDED" for w in res["warnings"])


def test_r07_distributed_requires_confirmed_parent(monkeypatch):
    """T-R07 / P1-03：分布式也不能只凭后缀——父表必须确实出现在 Proxy 结果里。

    db_a：父表 orders 在 Proxy 结果中 ⇒ 子表判定成立，剔除。
    db_b：父表 legacy 不在 Proxy 结果中 ⇒ 保留为逻辑表，并由 RECON_MISMATCH
          把这条不确定性【显式】报出来（可见），而不是静默少算（不可见）。
    """
    _patch_ctx(monkeypatch, "distributed")
    per_db = {
        ("db_a", svc.SQL_SHARD): _rows(["db_a.orders"], info="shardkey:id"),
        ("db_a", svc.SQL_BROADCAST): [],
        ("db_a", svc.SQL_SINGLE): [],
        ("db_b", svc.SQL_SHARD): [],
        ("db_b", svc.SQL_BROADCAST): [],
        ("db_b", svc.SQL_SINGLE): _rows(["db_b.other"]),
    }
    pool = FakePool(
        databases=["db_a", "db_b"],
        info_schema={"db_a": {"base": ["orders", "orders_tdsql_subp202601"]},
                     "db_b": {"base": ["other", "legacy_tdsql_subp202601"]}},
        per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    by_db = {i["db_name"]: i for i in res["items"]}
    assert by_db["db_a"]["subpartition_tables"] == 1     # 父表已确认 ⇒ 剔除
    assert by_db["db_a"]["baseline_tables"] == 1
    assert by_db["db_b"]["subpartition_tables"] == 0     # 父表未确认 ⇒ 不剔除
    assert by_db["db_b"]["baseline_tables"] == 2
    assert "legacy_tdsql_subp202601" in by_db["db_b"]["detail"], \
        "未确认的后缀表必须在明细里被点名，不能悄悄消失"
    assert any(w["code"] == "RECON_MISMATCH" for w in res["warnings"])


def test_r09_five_hundred_failed_databases_is_bounded(monkeypatch):
    """T-R09 / P1-07：500 库全失败时告警可序列化、体积受控、前端条数受控。"""
    import json as _json
    _patch_ctx(monkeypatch, "distributed")
    dbs = [f"db_{i:03d}" for i in range(500)]
    long_msg = ("SELECT command denied to user 'audit'@'10.0.0.1' "
                "for table 't_business_transaction_detail_history'") * 3
    per_db = {(d, svc.SQL_SHARD): _mysql_error(1142, long_msg) for d in dbs}
    pool = FakePool(databases=dbs,
                    info_schema={d: {"base": [f"t_{d}"]} for d in dbs},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert res["failed_databases"] == 500
    assert res["total_tables"] == 0
    w = [x for x in res["warnings"] if x["code"] == "PROXY_CMD_FAILED"]
    assert len(w) == 1, "500 库失败必须汇总为一条告警，不是 500 条"
    assert "500 个库采集失败" in w[0]["detail"]
    blob = _json.dumps(res["warnings"], ensure_ascii=False)
    assert len(blob.encode("utf-8")) < 8 * 1024, \
        f"warnings_json 体积失控: {len(blob.encode('utf-8'))} 字节"
    assert len(res["warnings"]) <= 6, "前端横幅条数必须受控"
    # 逐库原因没有丢，只是下沉到了明细行
    details = by_db_detail(res)
    assert len(details) == 500 and all(details.values())
    assert all(len(d) <= 512 for d in details.values())


def test_r10_centralized_nonexistent_database_is_rejected(monkeypatch):
    """T-R10 / P2-01：指定不存在的库必须报错，不得回"成功、0 张表"。"""
    _patch_ctx(monkeypatch, "centralized")
    pool = FakePool(databases=["db_a"], info_schema={"db_a": {"base": ["t1"]}})
    with pytest.raises(ValueError) as e:
        svc.analyze(pool, connection_id="c1", database="nosuch")
    assert "不存在" in str(e.value)
    # 存在但为空的库仍然正常返回 0，两者可区分
    pool2 = FakePool(databases=["db_a", "db_empty"],
                     info_schema={"db_a": {"base": ["t1"]},
                                  "db_empty": {"base": []}})
    res = svc.analyze(pool2, connection_id="c1", database="db_empty")
    assert res["total_tables"] == 0 and res["items"][0]["status"] == "OK"


def test_r10b_show_databases_failure_is_not_silent(monkeypatch):
    """T-R10 / P2-01：库枚举失败不得被吞成"空库"。"""
    _patch_ctx(monkeypatch, "centralized")
    pool = FakePool(show_db_fail=_mysql_error(1045, "Access denied for user"))
    with pytest.raises(Exception) as e:
        svc.analyze(pool, connection_id="c1")
    assert "Access denied" in str(e.value)


def test_r11_empty_connection_id_is_rejected():
    """T-R11 / P2-03：空 connection_id 下连接解析与实例类型解析可能指向不同实例。"""
    with pytest.raises(ValueError) as e:
        svc.run_stats(FakePool(), connection_id="")
    assert "connection_id" in str(e.value)
    with pytest.raises(ValueError):
        svc.run_stats(FakePool(), connection_id="   ")


def test_r11b_api_model_requires_connection_id():
    """T-R11 / P2-03：接口契约层就必须挡住空 connection_id。"""
    import pydantic
    from backend.api.table_type_stats import StatsRequest
    assert StatsRequest(connection_id="c1").database == ""
    with pytest.raises(pydantic.ValidationError):
        StatsRequest(database="x")                 # 缺字段
    with pytest.raises(pydantic.ValidationError):
        StatsRequest(connection_id="", database="x")


def test_r13_api_records_current_operator(monkeypatch):
    """T-R13 / P2-02：API 必须把 request.state.username 传给 run_stats(operator=)。"""
    import inspect
    from fastapi import Request
    from backend.api import table_type_stats as api

    sig = inspect.signature(api.run)
    assert "http_request" in sig.parameters
    assert sig.parameters["http_request"].annotation is Request

    seen = {}

    def _spy(pool, connection_id="", database="", operator=""):
        seen["operator"] = operator
        return {"ok": True}

    monkeypatch.setattr(api, "_pool", lambda cid: FakePool())
    monkeypatch.setattr(api.svc, "run_stats", _spy)
    api.run(api.StatsRequest(connection_id="c1"), _FakeRequest("zhangsan"))
    assert seen["operator"] == "zhangsan"
    # 未认证兜底不得写空串（空串会让历史留档无法追责）
    api.run(api.StatsRequest(connection_id="c1"), _FakeRequest(None))
    assert seen["operator"] == "anonymous"


def test_r08_permission_key_is_registered_at_every_point():
    """T-R08 / P1-06：新权限键必须登记到全部 6 处，缺一处就有角色进不去。

    设计阶段（模块尚未落盘）自动跳过；Q 落盘后这条即成为硬门禁。
    """
    import pathlib
    import backend
    repo = pathlib.Path(backend.__file__).resolve().parent.parent
    if not (repo / "backend" / "api" / "table_type_stats.py").exists():
        pytest.skip("G14 尚未落盘（设计阶段）")
    perm = "deep-diag-tabletype"
    points = [
        ("backend/services/auth_service.py", perm),   # API 路径 → 权限键映射
        ("backend/services/database.py", perm),       # 默认角色权限清单
        ("frontend/index.html", perm),                # el-tab-pane v-if
        ("frontend/static/js/app.js", perm),          # subtabs 回退清单
    ]
    for rel, needle in points:
        text = (repo / rel).read_text(encoding="utf-8")
        assert needle in text, f"{rel} 未登记权限键 {needle}"
    # subtabs 是 P1-06 的正主：单独钉住，防止只加了 tab-pane 忘了回退清单
    app_js = (repo / "frontend/static/js/app.js").read_text(encoding="utf-8")
    line = [l for l in app_js.splitlines() if "const subtabs=" in l]
    assert line and f"perm:'{perm}'" in line[0], \
        "深度诊断子页签回退清单 subtabs 未登记新页签"


def test_sit01_write_endpoint_is_reachable_by_non_admin_roles():
    """DEF-SIT-01：G14 写端点必须与既有深度诊断子模块处于同一放行清单。

    check_permission 是两级判定：第一级按角色 + _DEVELOPER_WRITE_PREFIXES 放行，
    第二级才查 role_permissions 菜单可见性。只登记 _PATH_TO_MENU 而不登记
    _OPERATIONAL_WRITE_PREFIXES 时，developer 与全部自定义角色会卡在第一级，
    拿到 403——而页签仍然显示，现场极易误判为权限矩阵没配对。

    本用例刻意用【与既有 G5 对照】的方式断言，而不是硬编码"必须为 True"：
    G14 与既有深度诊断子模块的可达性口径应当完全一致，将来平台整体调整
    角色策略时，这条断言会随之一起变，不会变成需要人工维护的死值。
    """
    from backend.services import auth_service as A

    G14 = "/api/v1/table-type-stats/run"
    G5 = "/api/v1/index-audit/run"          # 既有深度诊断写端点，作为基准
    for role in ("admin", "dba", "developer", "auditor"):
        assert A.check_permission(role, "POST", G14) == \
               A.check_permission(role, "POST", G5), \
            f"角色 {role} 对 G14 写端点的可达性与既有深度诊断子模块不一致"

    # 前缀本身必须登记，且带尾斜杠（判定用 startswith，不带会误命中兄弟路径）
    assert "/api/v1/table-type-stats/" in A._OPERATIONAL_WRITE_PREFIXES
    assert A._DEVELOPER_WRITE_PREFIXES is A._OPERATIONAL_WRITE_PREFIXES


def test_sit03_blank_connection_id_reports_the_right_reason(monkeypatch):
    """DEF-SIT-03：全空白 connection_id 必须报"必须指定"，而不是"未连接实例"。

    Rev.M 的 API 先做连接解析、后做入参校验，于是服务层守卫在 HTTP 路径上不可达，
    用户输入空白却被指向连接管理页。这条用例直接打 API 层，覆盖真实调用顺序。
    """
    from fastapi import HTTPException
    from backend.api import table_type_stats as api

    called = {"pool": 0}
    monkeypatch.setattr(api, "_pool", lambda cid: called.__setitem__("pool", 1))
    for blank in ("   ", "\t", " \n "):
        with pytest.raises(HTTPException) as e:
            api.run(api.StatsRequest(connection_id=blank), _FakeRequest("u"))
        assert e.value.status_code == 400
        assert "必须指定 connection_id" in e.value.detail
    assert called["pool"] == 0, "入参不合格时不得先去解析连接"


# ══════════════════════════════════════════════════════════════════
# Rev.J 定向回归（O 第二轮评审 §7 的 T2-R01…T2-R10）
# ══════════════════════════════════════════════════════════════════
def test_t2r01_case_variant_databases_are_not_merged(monkeypatch):
    """T2-R01 / P1-01：`Sales` 与 `sales` 是两个库，不得被合并成一个。

    Rev.I 用 `{name.lower(): name}` 单值字典，后者会覆盖前者：
    两库基线并进一个键、Proxy 行全归给字典里最后那个库、另一个库显示 0。
    这是四个主数字的静默错误——没有任何告警会亮。
    """
    _patch_ctx(monkeypatch, "distributed")
    per_db = {
        ("Sales", svc.SQL_SHARD): _rows(["Sales.t_upper"], info="shardkey:id"),
        ("Sales", svc.SQL_BROADCAST): [],
        ("Sales", svc.SQL_SINGLE): [],
        ("sales", svc.SQL_SHARD): [],
        ("sales", svc.SQL_BROADCAST): [],
        ("sales", svc.SQL_SINGLE): _rows(["sales.t_lower"]),
    }
    pool = FakePool(databases=["Sales", "sales"],
                    info_schema={"Sales": {"base": ["t_upper"]},
                                 "sales": {"base": ["t_lower"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")

    assert res["database_count"] == 2
    by_db = {i["db_name"]: i for i in res["items"]}
    assert set(by_db) == {"Sales", "sales"}, "两个库必须各自成行"
    assert by_db["Sales"]["shard_tables"] == 1 and by_db["Sales"]["single_tables"] == 0
    assert by_db["sales"]["single_tables"] == 1 and by_db["sales"]["shard_tables"] == 0
    assert by_db["Sales"]["baseline_tables"] == 1
    assert by_db["sales"]["baseline_tables"] == 1
    assert res["total_tables"] == 2
    # 两个口径精确对齐 ⇒ 不得出现虚假的 RECON_MISMATCH
    assert not any(w["code"] == "RECON_MISMATCH" for w in res["warnings"])
    # 但存在大小写变体这件事本身要如实告知
    assert any(w["code"] == "DB_NAME_CASE_VARIANTS" for w in res["warnings"])


def test_t2r01b_wrong_case_database_is_rejected(monkeypatch):
    """T2-R01 / P1-01：指定错误大小写的库不得被"当成存在"。"""
    _patch_ctx(monkeypatch, "centralized")
    pool = FakePool(databases=["sales"], info_schema={"sales": {"base": ["t1"]}})
    with pytest.raises(ValueError) as e:
        svc.analyze(pool, connection_id="c1", database="Sales")
    assert "不存在" in str(e.value)
    assert "sales" in str(e.value), "应提示实例上存在大小写不同的同名库"
    # 精确名当然可以
    assert svc.analyze(pool, connection_id="c1", database="sales")["total_tables"] == 1


def test_t2r01c_ambiguous_qualified_name_is_not_guessed(monkeypatch):
    """T2-R01 / P1-01：库限定名无法唯一归属时不猜，记 DB_NAME_AMBIGUOUS。

    宁可少算并显式报出，也不把两个真实的库算成一个。
    """
    ns = svc._NameSpace(["Sales", "sales"])
    assert ns.resolve("Sales") == "Sales"
    assert ns.resolve("sales") == "sales"
    assert ns.resolve("SALES") is None and ns.is_ambiguous("SALES")
    assert ns.resolve("other") is None and not ns.is_ambiguous("other")
    # 唯一候选时才允许大小写回退
    assert svc._NameSpace(["sales"]).resolve("SALES") == "sales"

    _patch_ctx(monkeypatch, "distributed")
    per_db = {}
    for d in ("Sales", "sales"):
        per_db[(d, svc.SQL_SHARD)] = _rows(["SALES.mystery"])
        per_db[(d, svc.SQL_BROADCAST)] = []
        per_db[(d, svc.SQL_SINGLE)] = []
    pool = FakePool(databases=["Sales", "sales"],
                    info_schema={"Sales": {"base": []}, "sales": {"base": []}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert res["total_tables"] == 0, "歧义行不得被猜给其中一个库"
    w = [x for x in res["warnings"] if x["code"] == "DB_NAME_AMBIGUOUS"]
    assert w and "SALES.mystery" in w[0]["detail"]


def test_t2r02_deadline_covers_prelude_and_every_command(monkeypatch):
    """T2-R02 / P1-02：deadline 覆盖前置查询，且**每条命令**开始前都检查。

    Rev.I 只在每个库开始时查一次预算，单库随后最多跑三条 30 秒命令
    ——179 秒进库能跑到 269 秒。这里用可控时钟把这条路径钉死。
    """
    _patch_ctx(monkeypatch, "distributed")
    clock = FakeClock()
    monkeypatch.setattr(svc, "_now", clock)
    deadline = clock.t + 100.0

    per_db = {}
    for d in ("db_a", "db_b"):
        per_db[(d, svc.SQL_SHARD)] = _rows([f"{d}.s"])
        per_db[(d, svc.SQL_BROADCAST)] = _rows([f"{d}.b"])
        per_db[(d, svc.SQL_SINGLE)] = _rows([f"{d}.n"])
    pool = FakePool(databases=["db_a", "db_b"],
                    info_schema={"db_a": {"base": ["s", "b", "n"]},
                                 "db_b": {"base": ["s", "b", "n"]}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)

    # 每条命令耗 60 秒：db_a 跑完两条即超预算，第三条不得再启动
    executed = []
    orig_pairs = svc._extract_pairs

    def _slow_pairs(rows, cur, known):
        executed.append(cur)
        clock.advance(60.0)          # 每条命令耗 60 秒
        return orig_pairs(rows, cur, known)

    monkeypatch.setattr(svc, "_extract_pairs", _slow_pairs)
    res = svc.analyze(pool, connection_id="c1", deadline=deadline)

    # db_a：第 1 条（0→60）、第 2 条（60→120，启动时 60<100 允许）执行；
    #       第 3 条启动时已 120>=100，抛预算 → db_a 整库 SKIPPED
    assert len(executed) == 2, f"deadline 之后不得再启动新命令，实际执行 {len(executed)} 条"
    by_db = {i["db_name"]: i for i in res["items"]}
    assert by_db["db_a"]["status"] == "SKIPPED"
    assert by_db["db_b"]["status"] == "SKIPPED"
    assert res["skipped_databases"] == 2
    assert res["total_tables"] == 0


def test_t2r02b_deadline_blocks_baseline_query(monkeypatch):
    """T2-R02 / P1-02：前置基线查询也在预算内（Rev.I 完全不计它）。"""
    _patch_ctx(monkeypatch, "centralized")
    clock = FakeClock()
    monkeypatch.setattr(svc, "_now", clock)
    pool = FakePool(databases=["db_a"], info_schema={"db_a": {"base": ["t1"]}})
    with pytest.raises(TimeoutError) as e:
        svc.analyze(pool, connection_id="c1", deadline=clock.t - 1)
    assert "预算" in str(e.value)


def test_t2r03_failed_db_rows_do_not_enter_any_rollup(monkeypatch):
    """T2-R03 / P1-03：失败库的跨库行不得进入 overlap / baseline / subp 汇总。"""
    _patch_ctx(monkeypatch, "distributed")
    # db_a 成功，其实例级返回同时带回 db_b.t1，且 t1 在两个类型中出现（重叠）
    per_db = {
        ("db_a", svc.SQL_SHARD): _rows(["db_a.s1", "db_b.t1"]),
        ("db_a", svc.SQL_BROADCAST): _rows(["db_b.t1"]),
        ("db_a", svc.SQL_SINGLE): [],
        ("db_b", svc.SQL_SHARD): _mysql_error(1142, "SELECT command denied"),
    }
    subp = [f"t1_tdsql_subp2026{m:02d}" for m in range(1, 13)]
    pool = FakePool(databases=["db_a", "db_b"],
                    info_schema={"db_a": {"base": ["s1"]},
                                 "db_b": {"base": ["t1"] + subp}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")

    by_db = {i["db_name"]: i for i in res["items"]}
    assert by_db["db_b"]["status"] == "FAILED"
    assert res["overlap_count"] == 0, "失败库的跨库行不得计入重叠"
    assert res["baseline_tables"] == 1, "只应含 db_a 的 s1"
    assert res["subpartition_tables"] == 0, "失败库的子分区不得进汇总"
    assert not any(w["code"] == "SUBPARTITION_EXCLUDED" for w in res["warnings"])
    # 逐库行仍如实显示 db_b 的基线（information_schema 那侧确实查成功了）
    assert by_db["db_b"]["baseline_tables"] >= 1


def test_t2r07_candidate_in_proxy_is_a_logical_table(monkeypatch):
    """T2-R07 / P2-01：候选自身出现在 Proxy 结果中 ⇒ 它是逻辑表，不是物理子表。

    真正的物理子表根本不会被 /*proxy*/show table 返回——"它自己也在 Proxy 结果里"
    直接证伪了"它是物理子表"。
    """
    _patch_ctx(monkeypatch, "distributed")
    per_db = {
        ("db_a", svc.SQL_SHARD): _rows(["db_a.orders",
                                        "db_a.orders_tdsql_subp202601"]),
        ("db_a", svc.SQL_BROADCAST): [],
        ("db_a", svc.SQL_SINGLE): [],
    }
    pool = FakePool(
        databases=["db_a"],
        info_schema={"db_a": {"base": ["orders", "orders_tdsql_subp202601"]}},
        per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert res["subpartition_tables"] == 0, "两张都是逻辑表，不得剔除任何一张"
    assert res["baseline_tables"] == 2
    assert res["total_tables"] == 2
    assert not any(w["code"] == "RECON_MISMATCH" for w in res["warnings"]), \
        "不得凭空产生'仅 Proxy 可见'的虚假差异"
    assert not any(w["code"] == "SUBPARTITION_EXCLUDED" for w in res["warnings"])


def test_t2r07b_real_subpartition_is_still_stripped(monkeypatch):
    """T2-R07 反向：真正的子表（不在 Proxy 结果里）仍必须被剔除——不得因 P2-01 回退。"""
    _patch_ctx(monkeypatch, "distributed")
    subp = ["cus_pub_translog_tdsql_subp190001"] + [
        f"cus_pub_translog_tdsql_subp2026{m:02d}" for m in range(1, 13)]
    per_db = {("db_a", svc.SQL_SHARD): _rows(["db_a.cus_pub_translog"]),
              ("db_a", svc.SQL_BROADCAST): [],
              ("db_a", svc.SQL_SINGLE): []}
    pool = FakePool(databases=["db_a"],
                    info_schema={"db_a": {"base": ["cus_pub_translog"] + subp}},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.analyze(pool, connection_id="c1")
    assert res["subpartition_tables"] == 13
    assert res["baseline_tables"] == 1


def test_t3r07_destructive_guard_rejects_non_test_database(monkeypatch):
    """T3-R07 / P1-04：目标不是批准的测试库时，必须在任何 DROP 之前失败关闭。

    这条用例本身不连数据库——它验证的是"守门人会不会放行"，
    所以在任何环境下都必须执行，不能挂 MYSQL_AVAILABLE。
    """
    from backend.services import database as dbmod
    for bad in ("tdsql_sqlcheck", "production", ""):
        monkeypatch.setitem(dbmod.MYSQL_CONFIG, "database", bad)
        with pytest.raises(DestructiveTargetError) as e:
            assert_destructive_target_is_safe()
        assert "拒绝在非批准的数据库上执行 DROP" in str(e.value)
    # 批准的库名放行
    monkeypatch.setitem(dbmod.MYSQL_CONFIG, "database", _APPROVED_TEST_DB)
    assert assert_destructive_target_is_safe()["database"] == _APPROVED_TEST_DB


def test_t3r07b_custom_test_db_needs_explicit_opt_in(monkeypatch):
    """T3-R07 / P1-04：自定义测试库必须【同时】给出显式破坏性开关才放行。"""
    from backend.services import database as dbmod
    import backend.api.table_type_stats as _api          # noqa: F401  (确保包已加载)
    mod = sys.modules[__name__]
    monkeypatch.setitem(dbmod.MYSQL_CONFIG, "database", "my_scratch_db")
    # 只给库名、不给开关 → 仍然拒绝
    monkeypatch.setattr(mod, "_ALLOW_DESTRUCTIVE", False)
    monkeypatch.setattr(mod, "_CUSTOM_TEST_DB", "my_scratch_db")
    with pytest.raises(DestructiveTargetError):
        assert_destructive_target_is_safe()
    # 两者齐全 → 放行
    monkeypatch.setattr(mod, "_ALLOW_DESTRUCTIVE", True)
    assert assert_destructive_target_is_safe()["database"] == "my_scratch_db"


def test_t3r07c_probe_and_execution_use_the_same_config():
    """T3-R07 / P1-04：可用性探测与实际执行必须是同一份配置。

    Rev.J 用 TDSQL_TEST_* 探测、用 SQLCHECK_DB_* 执行，两套可以指向不同服务器：
    探测通了就不 skip，然后往生产元数据库执行 DROP。
    """
    import inspect
    src = inspect.getsource(_probe_metadata_db)
    assert "effective_db_config()" in src, "探测必须读 database 模块生效中的配置"
    assert "TDSQL_TEST" not in src, "探测不得再使用另一套 TDSQL_TEST_* 配置"
    assert 'database=cfg["database"]' in src, "探测必须选中后续 DROP 使用的目标库"
    cfg = effective_db_config()
    assert set(("host", "port", "user", "password", "database")) <= set(cfg)


# ── DEF-1 / T2-R06：迁移槽位护栏 ────────────────────────────────────
_OUR_SLOT = (13, 130)
_OUR_SQL_NAME = "130_table_type_stats.sql"


def scan_migration_slots(schema_dir):
    """扫描 schema 目录，返回 {(version, sequence): [文件名, ...]}。

    Rev.J / P1-05：**值必须是列表**。Rev.I 用的是单值字典
    `taken[(v, s)] = filename`，同一槽位有两个文件时后者会覆盖前者——
    那个护栏恰好看不见 DEF-1 想防的场景（同槽重复），等于没防。
    """
    import pathlib
    slots = {}
    d = pathlib.Path(schema_dir)
    if not d.is_dir():
        return slots
    for vdir in sorted(d.iterdir()):
        if not (vdir.is_dir() and len(vdir.name) > 1
                and vdir.name[0] == "v" and vdir.name[1:].isdigit()):
            continue
        for f in sorted(vdir.iterdir()):
            if f.suffix != ".sql" or len(f.name) < 4 or not f.name[:3].isdigit():
                continue
            slots.setdefault((int(vdir.name[1:]), int(f.name[:3])), []).append(f.name)
    return slots


def _schema_dir():
    import pathlib
    import backend
    return pathlib.Path(backend.__file__).resolve().parent / "schema"


# 选槽时的已知前驱（v1.6.2.2 的 O-22 引入）。用它证明"当时选槽是连续的"，
# 而不是用"我们永远是最大槽"——后者会阻断项目此后的任何迁移。
_PREDECESSOR_SLOT = (12, 120)


def assert_slot_ok(slots):
    """迁移槽位的**永久不变量**。落盘前、落盘后、以及此后新增任意迁移都成立。

    Rev.K / P1-03：Rev.J 里还有一条 `assert _OUR_SLOT > max(others)`，
    它在项目下一次合法新增 `v13/131` 或 `v14/140` 之后**必然失败**——
    等于用一条历史测试禁止项目继续演进。我甚至把"我们不再是最大槽"写成了
    必须拒绝的场景，方向完全错了。

    真正的永久不变量只有两条：
      ① 任何 `(version, sequence)` 槽位不得有两个文件（DEF-1 想防的就是这个）；
      ② 我们的槽位有且只有我们自己的文件。
    "当前最大之后的下一个"只在**选槽那一刻**有意义，不该随仓库永久执行。
    需要证明当时选槽连续，就断言已知前驱 v12/120 存在——这条同样永久成立。
    """
    dups = {k: v for k, v in slots.items() if len(v) > 1}
    assert not dups, f"存在同槽多文件（DEF-1 形态）: {dups}"

    occupied = slots.get(_OUR_SLOT)
    if occupied is not None:
        assert occupied == [_OUR_SQL_NAME], \
            f"迁移槽位 v13/130 被 {occupied} 占用，本模块必须独占该槽"
    # ③ 选槽连续性：前驱必须仍在（防有人误删 v12/120 造成版本链断裂）。
    #    仅在仓库已有迁移文件时校验，合成的空目录不参与。
    if slots and _OUR_SLOT in slots:
        assert _PREDECESSOR_SLOT in slots, \
            f"已知前驱迁移 v12/120 不见了，版本链断裂：{sorted(slots)}"


def test_migration_slot_scanner_detects_duplicates(tmp_path):
    """扫描器本身必须能看见"同槽两个文件"——这正是 DEF-1 的形态。

    Rev.I 的护栏用单值字典保存占用者，同槽第二个文件会覆盖第一个，
    于是重复根本报不出来。先把扫描器钉死，护栏才有意义。
    """
    (tmp_path / "v11").mkdir()
    (tmp_path / "v11" / "110_index_finding_structured.sql").write_text("-- a")
    (tmp_path / "v11" / "110_table_type_stats.sql").write_text("-- b")
    (tmp_path / "v12").mkdir()
    (tmp_path / "v12" / "120_gateway_report_tickets.sql").write_text("-- c")
    (tmp_path / "notaversion").mkdir()
    (tmp_path / "notaversion" / "999_x.sql").write_text("-- ignored")

    slots = scan_migration_slots(tmp_path)
    assert slots[(11, 110)] == ["110_index_finding_structured.sql",
                                "110_table_type_stats.sql"]
    assert slots[(12, 120)] == ["120_gateway_report_tickets.sql"]
    assert all(k[0] != 999 for k in slots), "非 vN 目录不得计入"
    dups = {k: v for k, v in slots.items() if len(v) > 1}
    assert dups, "同槽两个文件必须被识别为重复"


def test_migration_slot_is_available_and_unique():
    """T2-R06 / DEF-1：v13/130 槽位必须唯一、可用，且**落盘前后都成立**。

    Rev.I 的写法是：
        assert ours not in taken or taken[ours].startswith("130_table_type_stats")
        assert ours > max(taken)
    第二句在迁移文件真正落盘后必然失败——那时 max(taken) == ours，
    `ours > max(taken)` 为假。**一条为了防错而写的护栏，会在模块上线当天把构建打红。**
    设计阶段之所以"通过"，只是因为文件还不存在，等于什么都没验证。

    Rev.K 之后的正确写法只钉永久不变量：
      ① 全局没有任何槽位重复；
      ② 若我们的文件已落盘，v13/130 下有且只有它；
      ③ 已知前驱 v12/120 仍然存在。
    后续 v13/131、v14/140 等合法迁移出现时仍必须通过；
    因此绝不再断言"本模块永远是最大槽"。
    """
    assert_slot_ok(scan_migration_slots(_schema_dir()))


def test_migration_slot_guard_passes_before_and_after_landing():
    """T2-R06 关键点：护栏在**迁移文件真正落盘之后**必须依然通过。

    Rev.I 的护栏做不到这一点——`assert ours > max(taken)` 在文件落盘后必然为假。
    这里用两份合成状态直接把两个阶段都验一遍，不用等到上线当天才发现。
    """
    current = {(11, 110): ["110_index_finding_structured.sql"],
               (12, 120): ["120_gateway_report_tickets.sql"]}
    assert_slot_ok(dict(current))                       # 阶段一：尚未落盘
    landed = dict(current); landed[_OUR_SLOT] = [_OUR_SQL_NAME]
    assert_slot_ok(landed)                              # 阶段二：已落盘 ← Rev.I 会在这里失败


def test_t3r05_guard_does_not_block_future_migrations():
    """T3-R05 / P1-03：本模块落盘之后，项目继续新增迁移**必须**照样通过。

    Rev.J 的 `assert _OUR_SLOT > max(others)` 在出现 v13/131 或 v14/140 之后
    必然为假——一条历史测试把项目此后的演进锁死了。这里把这两种未来状态直接钉住。
    """
    base = {(11, 110): ["110_index_finding_structured.sql"],
            (12, 120): ["120_gateway_report_tickets.sql"],
            _OUR_SLOT: [_OUR_SQL_NAME]}
    assert_slot_ok({**base, (13, 131): ["131_next_feature.sql"]})
    assert_slot_ok({**base, (14, 140): ["140_future.sql"]})
    assert_slot_ok({**base, (13, 131): ["131_a.sql"], (14, 140): ["140_b.sql"],
                    (15, 150): ["150_c.sql"]})


def test_t3r06_guard_rejects_duplicate_and_squatter():
    """T3-R06 / P1-03：护栏必须真的会拒——但只拒该拒的两种。"""
    base = {(11, 110): ["110_index_finding_structured.sql"],
            (12, 120): ["120_gateway_report_tickets.sql"]}
    # 同一 (version, sequence) 出现两个文件 —— DEF-1 的真实形态
    with pytest.raises(AssertionError, match="同槽多文件"):
        assert_slot_ok({**base, (11, 110): ["110_a.sql", "110_b.sql"]})
    # 我们的槽位被别人占
    with pytest.raises(AssertionError, match="必须独占该槽"):
        assert_slot_ok({**base, _OUR_SLOT: ["130_someone_else.sql"]})
    # 我们的槽位里混进第二个文件
    with pytest.raises(AssertionError, match="同槽多文件"):
        assert_slot_ok({**base, _OUR_SLOT: [_OUR_SQL_NAME, "130_other.sql"]})
    # 前驱被误删 → 版本链断裂
    with pytest.raises(AssertionError, match="版本链断裂"):
        assert_slot_ok({(11, 110): ["110_index_finding_structured.sql"],
                        _OUR_SLOT: [_OUR_SQL_NAME]})


def test_migration_slot_guard_would_reject_a_taken_slot(tmp_path):
    """从真实目录扫出来的"槽位被别人占"同样必须被拒——端到端验一次扫描器 + 护栏。"""
    (tmp_path / "v12").mkdir()
    (tmp_path / "v12" / "120_gateway_report_tickets.sql").write_text("-- pred")
    (tmp_path / "v13").mkdir()
    (tmp_path / "v13" / "130_someone_else.sql").write_text("-- squatter")
    with pytest.raises(AssertionError, match="必须独占该槽"):
        assert_slot_ok(scan_migration_slots(tmp_path))


# ══════════════════════════════════════════════════════════════════
# 落库与结构验收（需本地元数据库）
# ══════════════════════════════════════════════════════════════════
def _ddl_path():
    """建表 DDL 的位置：落盘后是 backend/schema/v13/130_table_type_stats.sql，
    设计阶段在本文件旁边。测试直接读 DDL 文件本身，保证"文档里的建表语句"
    和"服务的结构验收"是同一份真相（P1-08 的账要能对上）。"""
    import pathlib
    import backend
    here = pathlib.Path(__file__).parent / "130_table_type_stats.sql"
    if here.exists():
        return here
    repo = pathlib.Path(backend.__file__).resolve().parent.parent
    return repo / "backend" / "schema" / "v13" / "130_table_type_stats.sql"


def _exec_sql(*statements):
    from backend.services.database import _get_connection
    conn = _get_connection()
    try:
        for st in statements:
            st = st.strip()
            if st:
                conn.execute(st)
        conn.commit()
    finally:
        conn.close()


def _strip_sql_comments(text):
    """去掉整行 -- 注释后按分号切分，保留真正的语句。"""
    body = "\n".join(l for l in text.splitlines()
                     if not l.strip().startswith("--"))
    return [st for st in body.split(";") if st.strip()]


def _reset_g14_tables():
    """删表重建，回到 DDL 定义的干净状态。**每次都先过目标保护。**"""
    assert_destructive_target_is_safe()
    ddl = _ddl_path().read_text(encoding="utf-8")
    _exec_sql("DROP TABLE IF EXISTS table_type_stat_item",
              "DROP TABLE IF EXISTS table_type_stat")
    _exec_sql(*_strip_sql_comments(ddl))


@pytest.fixture()
def g14_schema():
    # Rev.K / P1-04：DROP 之前先做目标失败关闭断言，用的是 database 模块
    # 【已生效】的配置，不是环境变量快照，也不再靠 setdefault"兜底"。
    assert_destructive_target_is_safe()
    from backend.services.database import ensure_db
    ensure_db()
    _reset_g14_tables()
    yield
    _reset_g14_tables()


@pytest.mark.skipif(not MYSQL_AVAILABLE, reason="MySQL 测试环境未启动")
def test_run_stats_persists(monkeypatch, g14_schema):
    _patch_ctx(monkeypatch, "centralized")
    pool = FakePool(databases=["db_a", "db_b"],
                    info_schema={"db_a": {"base": ["t1", "t2"]},
                                 "db_b": {"base": ["t3"]}})
    res = svc.run_stats(pool, connection_id="qa", operator="pytest")
    assert res["total_tables"] == 3 and res["single_tables"] == 3
    detail = svc.get_detail(res["stat_id"])
    assert len(detail["items"]) == len(res["items"])
    assert isinstance(detail["warnings"], list)
    hist = svc.list_history("qa", limit=5)
    assert hist and hist[0]["id"] == res["stat_id"]


@pytest.mark.skipif(not MYSQL_AVAILABLE, reason="MySQL 测试环境未启动")
def test_r13_created_by_is_persisted(monkeypatch, g14_schema):
    """T-R13 / P2-02：操作人真正落到 created_by，历史可回看可追责。"""
    _patch_ctx(monkeypatch, "centralized")
    pool = FakePool(databases=["db_a"], info_schema={"db_a": {"base": ["t1"]}})
    res = svc.run_stats(pool, connection_id="qa", operator="zhangsan")
    hist = svc.list_history("qa", limit=1)
    assert hist[0]["created_by"] == "zhangsan"
    assert hist[0]["id"] == res["stat_id"]
    # /history 支持不带 connection_id 的全量回看
    assert any(h["id"] == res["stat_id"] for h in svc.list_history(limit=5))


@pytest.mark.skipif(not MYSQL_AVAILABLE, reason="MySQL 测试环境未启动")
def test_r09b_large_warnings_survive_round_trip(monkeypatch, g14_schema):
    """T-R09 / P1-07：500 库失败的告警必须能落库并原样回读（TEXT 会截断）。"""
    _patch_ctx(monkeypatch, "distributed")
    dbs = [f"db_{i:03d}" for i in range(500)]
    per_db = {(d, svc.SQL_SHARD): _mysql_error(
        1142, "SELECT command denied to user 'audit'@'10.0.0.1'") for d in dbs}
    pool = FakePool(databases=dbs,
                    info_schema={d: {"base": [f"t_{d}"]} for d in dbs},
                    per_db=per_db)
    _patch_tmp_pool(monkeypatch, pool)
    res = svc.run_stats(pool, connection_id="qa", operator="pytest")
    back = svc.get_detail(res["stat_id"])
    assert back["warnings"] == res["warnings"], "告警回读必须与写入一致（无截断）"
    assert len(back["items"]) == 500
    assert all(i["detail"] for i in back["items"])


@pytest.mark.skipif(not MYSQL_AVAILABLE, reason="MySQL 测试环境未启动")
def test_get_detail_missing_id_is_graceful(g14_schema):
    out = svc.get_detail(99999999)
    assert out == {"items": [], "warnings": []}


# ── T-R12 / P1-08：畸形同名表必须失败关闭 ────────────────────────────
@pytest.mark.skipif(not MYSQL_AVAILABLE, reason="MySQL 测试环境未启动")
def test_r12_missing_table_fails_closed(g14_schema):
    """场景一：迁移登记后表被删除。迁移器不会重放纯 CREATE TABLE，
    _structure_state() 也照样返回 valid —— 只能靠本模块自验。"""
    _exec_sql("DROP TABLE IF EXISTS table_type_stat_item",
              "DROP TABLE IF EXISTS table_type_stat")
    with pytest.raises(svc.SchemaNotReadyError) as e:
        svc._ensure_schema()
    assert "缺少表 table_type_stat" in str(e.value)


@pytest.mark.skipif(not MYSQL_AVAILABLE, reason="MySQL 测试环境未启动")
def test_r12b_missing_column_fails_closed(g14_schema):
    """场景二：同名但缺列的历史残留表 —— CREATE TABLE IF NOT EXISTS 会静默跳过。"""
    _exec_sql("ALTER TABLE table_type_stat DROP COLUMN subpartition_tables")
    with pytest.raises(svc.SchemaNotReadyError) as e:
        svc._ensure_schema()
    assert "缺少列" in str(e.value) and "subpartition_tables" in str(e.value)


@pytest.mark.skipif(not MYSQL_AVAILABLE, reason="MySQL 测试环境未启动")
def test_r12c_wrong_type_fails_closed(g14_schema):
    """场景三：列类型错误。warnings_json 退回 TEXT 就是 P1-07 的成因，必须挡住。"""
    _exec_sql("ALTER TABLE table_type_stat MODIFY COLUMN warnings_json TEXT")
    with pytest.raises(svc.SchemaNotReadyError) as e:
        svc._ensure_schema()
    assert "类型不符" in str(e.value) and "warnings_json" in str(e.value)
    _reset_g14_tables()
    _exec_sql("ALTER TABLE table_type_stat_item "
              "MODIFY COLUMN total_tables VARCHAR(32) DEFAULT '0'")
    with pytest.raises(svc.SchemaNotReadyError) as e:
        svc._ensure_schema()
    assert "total_tables" in str(e.value)


@pytest.mark.skipif(not MYSQL_AVAILABLE, reason="MySQL 测试环境未启动")
def test_r12d_missing_index_fails_closed(g14_schema):
    """场景四：缺索引。不影响正确性但会让 /history 在留档积累后全表扫描。"""
    _exec_sql("DROP INDEX idx_tts_created ON table_type_stat")
    with pytest.raises(svc.SchemaNotReadyError) as e:
        svc._ensure_schema()
    assert "索引不符" in str(e.value) and "idx_tts_created" in str(e.value)


@pytest.mark.skipif(not MYSQL_AVAILABLE, reason="MySQL 测试环境未启动")
def test_r12e_run_stats_fails_before_collecting(monkeypatch, g14_schema):
    """T-R12：结构验收必须发生在【采集之前】——否则用户白等一轮 180 秒才收 500。"""
    _exec_sql("DROP TABLE IF EXISTS table_type_stat_item")
    _patch_ctx(monkeypatch, "centralized")
    pool = FakePool(databases=["db_a"], info_schema={"db_a": {"base": ["t1"]}})
    called = {"n": 0}
    real_analyze = svc.analyze

    def _counting(*a, **k):
        called["n"] += 1
        return real_analyze(*a, **k)

    monkeypatch.setattr(svc, "analyze", _counting)
    with pytest.raises(svc.SchemaNotReadyError):
        svc.run_stats(pool, connection_id="qa")
    assert called["n"] == 0, "结构不合格时不得先跑一轮采集"


# ── T3-R08 / P2-01：完整字段契约的参数化定向用例 ────────────────────
# Rev.J 正文声称"九种畸形场景由十项单测钉住"，但附录里实际只有缺表/缺列/错类型/
# 缺索引/采集前拦截/列清单一致六类——长度、自增、默认值、可空性、字符集、索引列序
# 都只是我在本地手工 ALTER 跑过一次。**人工跑过一次不能防止落盘实现或未来修改回退。**
# 现在逐条落成参数化用例。每条形如 (用例名, 破坏语句, 恢复语句, 期望错误关键词)。
_CONTRACT_CASES = [
    ("detail 长度收窄",
     "ALTER TABLE table_type_stat_item MODIFY COLUMN detail VARCHAR(16) DEFAULT ''",
     "ALTER TABLE table_type_stat_item MODIFY COLUMN detail VARCHAR(512) DEFAULT ''",
     ("detail", "长度不符")),
    ("connection_id 长度收窄",
     "ALTER TABLE table_type_stat MODIFY COLUMN connection_id VARCHAR(8) DEFAULT ''",
     "ALTER TABLE table_type_stat MODIFY COLUMN connection_id VARCHAR(64) DEFAULT ''",
     ("connection_id", "长度不符")),
    ("id 丢失 AUTO_INCREMENT",
     "ALTER TABLE table_type_stat MODIFY COLUMN id INT NOT NULL",
     "ALTER TABLE table_type_stat MODIFY COLUMN id INT NOT NULL AUTO_INCREMENT",
     ("id", "AUTO_INCREMENT")),
    ("created_at 缺默认值",
     "ALTER TABLE table_type_stat MODIFY COLUMN created_at DATETIME NULL",
     "ALTER TABLE table_type_stat MODIFY COLUMN created_at DATETIME DEFAULT CURRENT_TIMESTAMP",
     ("created_at", "默认值不符")),
    ("status 默认值被改",
     "ALTER TABLE table_type_stat_item MODIFY COLUMN status VARCHAR(16) DEFAULT 'X'",
     "ALTER TABLE table_type_stat_item MODIFY COLUMN status VARCHAR(16) DEFAULT 'OK'",
     ("status", "默认值不符")),
    ("stat_id 变为可空",
     "ALTER TABLE table_type_stat_item MODIFY COLUMN stat_id INT NULL",
     "ALTER TABLE table_type_stat_item MODIFY COLUMN stat_id INT NOT NULL",
     ("stat_id", "可空性不符")),
    ("db_name 字符集非 utf8mb4",
     "ALTER TABLE table_type_stat_item MODIFY COLUMN db_name VARCHAR(128) "
     "CHARACTER SET latin1 DEFAULT ''",
     "ALTER TABLE table_type_stat_item MODIFY COLUMN db_name VARCHAR(128) "
     "CHARACTER SET utf8mb4 DEFAULT ''",
     ("db_name", "字符集不符")),
    ("warnings_json 退回 TEXT",
     "ALTER TABLE table_type_stat MODIFY COLUMN warnings_json TEXT",
     "ALTER TABLE table_type_stat MODIFY COLUMN warnings_json MEDIUMTEXT",
     ("warnings_json", "类型不符")),
    ("total_tables 变 VARCHAR",
     "ALTER TABLE table_type_stat_item MODIFY COLUMN total_tables VARCHAR(32) DEFAULT '0'",
     "ALTER TABLE table_type_stat_item MODIFY COLUMN total_tables INT DEFAULT 0",
     ("total_tables", "类型不符")),
    ("索引建在错误的列上",
     "DROP INDEX idx_tts_conn ON table_type_stat; "
     "CREATE INDEX idx_tts_conn ON table_type_stat (instance_type)",
     "DROP INDEX idx_tts_conn ON table_type_stat; "
     "CREATE INDEX idx_tts_conn ON table_type_stat (connection_id)",
     ("idx_tts_conn", "列序不符")),
    ("索引被建成唯一索引",
     "DROP INDEX idx_tts_created ON table_type_stat; "
     "CREATE UNIQUE INDEX idx_tts_created ON table_type_stat (created_at)",
     "DROP INDEX idx_tts_created ON table_type_stat; "
     "CREATE INDEX idx_tts_created ON table_type_stat (created_at)",
     ("idx_tts_created", "唯一性不符")),
]


@pytest.mark.skipif(not MYSQL_AVAILABLE, reason="元数据库未就绪")
@pytest.mark.parametrize("name,break_sql,fix_sql,expect",
                         _CONTRACT_CASES, ids=[c[0] for c in _CONTRACT_CASES])
def test_t3r08_full_schema_contract_fails_closed(g14_schema, name, break_sql,
                                                 fix_sql, expect):
    """T3-R08 / P2-01：完整字段契约的每一项都必须在采集之前失败关闭。"""
    _exec_sql(*[x for x in break_sql.split(";") if x.strip()])
    with pytest.raises(svc.SchemaNotReadyError) as e:
        svc._ensure_schema()
    for kw in expect:
        assert kw in str(e.value), f"{name}: 报错里应点名 {kw}，实际：{e.value}"
    _exec_sql(*[x for x in fix_sql.split(";") if x.strip()])
    svc._ensure_schema()          # 恢复后必须重新通过


@pytest.mark.skipif(not MYSQL_AVAILABLE, reason="元数据库未就绪")
def test_t3r08b_clean_ddl_passes_verification(g14_schema):
    """T3-R08 反向护栏：**干净的 DDL 必须通过验收**。

    结构验收过严比过松更难发现——干净安装通不过 = 页面永远不可用，
    而且现场会以为是"表没建好"。这条用例必须和上面 11 条一起长期存在。
    """
    svc._ensure_schema()
    # 连续两次调用也必须稳定（无副作用）
    svc._ensure_schema()


@pytest.mark.skipif(not MYSQL_AVAILABLE, reason="MySQL 测试环境未启动")
def test_r12f_ddl_and_service_column_lists_agree(g14_schema):
    """DDL 文件与服务的期望列清单必须逐字一致，防止两边各改各的。"""
    ddl = _ddl_path().read_text(encoding="utf-8").lower()
    for col in svc._STAT_COLUMNS:
        if col != "id":
            assert col in ddl, f"DDL 缺少 table_type_stat.{col}"
    for col in svc._ITEM_COLUMNS:
        if col != "id":
            assert col in ddl, f"DDL 缺少 table_type_stat_item.{col}"
    assert "mediumtext" in ddl, "warnings_json 必须是 MEDIUMTEXT（P1-07）"
    # 干净结构下验收必须通过
    svc._ensure_schema()
