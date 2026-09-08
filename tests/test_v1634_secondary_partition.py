# -*- coding: utf-8 -*-
"""v1.6.3.4 / D03 二级分区主表识别 SIT 回归（DETAIL-v1.6.3.4 §8.2 PAR-13—20）。

直接驱动 `_identify_secondary_partition_mains`（在原有基线与 Proxy 采集完成后执行），
注入三处可测性钩子，全程离线、不连任何数据库：
  · `_now`                    —— 可控单调时钟（FakeClock），断言 180s 共享 deadline 不重置
  · `_open_directory_connection` —— 注入独立目录 L（SHOW FULL TABLES）返回行/异常
  · `_new_pool`               —— 注入 SHOW CREATE TABLE 的 DDL 返回（逐表精确映射）

PAR-21（真实容量核算，§8.2.1）依赖内网真实内核/Proxy 只读数据，本机无法闭环，
不属于本离线套件；其"待验证"边界已在设计 §8.2.1 与开发记录中列明。
"""
import re

import pytest

from backend.services import table_type_stats_service as svc
from backend.services.tdsql_connector import TDSQLConnectionConfig
from backend.services.tdsql_table_shape import (
    classify_logical_ddl, STATE_SECONDARY, STATE_NOT_SECONDARY)


# ────────────────────────────────────────────────────────────────────────────
# 可控时钟与 DDL 常量
# ────────────────────────────────────────────────────────────────────────────
class FakeClock:
    """可控单调时钟：每次调用返回当前 t 并推进 step；可手工 advance。"""

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


def _ddl_modern(tname):
    return (f"CREATE TABLE `{tname}` (`id` bigint NOT NULL) ENGINE=InnoDB "
            f"TDSQL_DISTRIBUTED BY HASH(`id`) "
            f"TDSQL_PARTITION BY RANGE(`id`) (PARTITION p0 VALUES LESS THAN (100), "
            f"PARTITION pmax VALUES LESS THAN MAXVALUE)")


def _ddl_legacy(tname):
    return (f"CREATE TABLE `{tname}` (`id` bigint NOT NULL) ENGINE=InnoDB "
            f"shardkey=id PARTITION BY RANGE(`id`) "
            f"(PARTITION p0 VALUES LESS THAN (100), PARTITION pmax VALUES LESS THAN MAXVALUE)")


def _ddl_plain(tname):
    return f"CREATE TABLE `{tname}` (`id` bigint NOT NULL) ENGINE=InnoDB"


# ────────────────────────────────────────────────────────────────────────────
# 驱动器：直接调用 _identify_secondary_partition_mains，注入 L / SHOW CREATE / 时钟
# ────────────────────────────────────────────────────────────────────────────
def _run_identify(monkeypatch, eligible_dbs, db_proxy, db_logical_base, db_shard,
                  dir_rows_by_db=None, dir_error_by_db=None, ddl_by_key=None,
                  clock=None, ddl_query_log=None, has_incomplete_dbs=False):
    """返回 (db_sp, sp_totals, sp_warnings)。

    dir_rows_by_db: {db: [表名 或 (表名, 类型)]}，默认空目录。
    dir_error_by_db: {db: Exception}，注入目录枚举异常（M-01/PAR-16 失败路径）。
    ddl_by_key: {(db, table): ddl_text}，SHOW CREATE 返回（缺省→空行→UNKNOWN）。
    ddl_query_log: 可选 list，记录每次 SHOW CREATE 的 (db, table)（验证去重/层序/预算）。
    """
    cfg = TDSQLConnectionConfig(host="h", port=3306, user="u", password="p",
                                database="d")
    clock = clock or FakeClock()
    monkeypatch.setattr(svc, "_now", clock)

    # 独立目录 L（SHOW FULL TABLES FROM `db`）
    class _DirCursor:
        def __init__(self, db):
            self._db = db
            self._rows = []

        def execute(self, sql, params=None):
            if dir_error_by_db and self._db in dir_error_by_db:
                raise dir_error_by_db[self._db]
            out = []
            for item in (dir_rows_by_db or {}).get(self._db, []):
                if isinstance(item, tuple):
                    name, ttype = item
                else:
                    name, ttype = item, "BASE TABLE"
                out.append({f"Tables_in_{self._db}": name, "Table_type": ttype})
            self._rows = out

        def fetchmany(self, n=None):
            rows, self._rows = self._rows, []
            return rows

        def close(self):
            pass

    class _DirConn:
        def __init__(self, db):
            self._db = db

        def cursor(self):
            return _DirCursor(self._db)

        def close(self):
            pass

    monkeypatch.setattr(
        svc, "_open_directory_connection", lambda c, db: _DirConn(db))

    # SHOW CREATE TABLE（_new_pool → 单连接 fake 池）
    ddl_by_key = ddl_by_key or {}

    class _Cur:
        def __init__(self):
            self._rows = []

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=None):
            m = re.search(r"SHOW CREATE TABLE\s+`?([^`.\s]+)`?\.`?([^`.\s]+)`?", sql)
            key = (m.group(1), m.group(2)) if m else None
            if ddl_query_log is not None and key:
                ddl_query_log.append(key)
            ddl = ddl_by_key.get(key)
            # 模拟 SHOW CREATE：有 DDL 返回一行，无则返回空行（→ UNKNOWN）
            self._rows = [{"Table": key[1], "Create Table": ddl}] if (key and ddl) else []

        def fetchall(self):
            return self._rows

    class _Conn:
        def cursor(self):
            return _Cur()

    class _Ctx:
        def __enter__(self):
            return _Conn()

        def __exit__(self, *a):
            return False

    class _Pool:
        def get_connection(self):
            return _Ctx()

        def close_all(self):
            pass

    monkeypatch.setattr(svc, "_new_pool", lambda c, pool_size=1: _Pool())

    deadline = clock() + svc.TOTAL_BUDGET_SECONDS
    return svc._identify_secondary_partition_mains(
        cfg, eligible_dbs, db_proxy, db_logical_base, db_shard, deadline,
        has_incomplete_dbs=has_incomplete_dbs)


def _warn_codes(sp_warnings):
    return [w.get("code") for w in sp_warnings]


# ════════════════════════════════════════════════════════════════════════════
# PAR-13：MODERN 主表仅在 only_base(B) 与完整 L 中，P 无该表 → main=1 / outside_shard=1 / COMPLETE
# ════════════════════════════════════════════════════════════════════════════
def test_par13_modern_main_only_in_base_and_l(monkeypatch):
    db = "db1"
    ddl = _ddl_modern("t_mod")
    db_sp, sp, warns = _run_identify(
        monkeypatch,
        eligible_dbs=[db],
        db_proxy={db: set()},                 # P 空（无该表）
        db_logical_base={db: {"t_mod"}},      # only_base：仅在 B
        db_shard={db: set()},                 # S 空
        dir_rows_by_db={db: ["t_mod"]},       # L 完整含该表
        ddl_by_key={(db, "t_mod"): ddl},
    )
    assert sp["main"] == 1
    assert sp["outside_shard"] == 1           # 不在旧分片集合 S
    assert sp["inventory_state"] == svc.SP_STATE_COMPLETE
    assert sp["check_state"] == svc.SP_STATE_COMPLETE
    # 计数恒等式
    assert sp["candidates"] == sp["checked"] + sp["unknown"] + sp["unchecked"]
    assert sp["outside_shard"] <= sp["main"] <= sp["checked"] <= sp["candidates"]


# ════════════════════════════════════════════════════════════════════════════
# PAR-14：MODERN 主表落在旧 single（经 P）与 L 中，不在 S、也不在 only_base(B) → 仍计 1
# ════════════════════════════════════════════════════════════════════════════
def test_par14_modern_main_in_old_single_not_in_base(monkeypatch):
    db = "db1"
    ddl = _ddl_modern("t_mod")
    db_sp, sp, warns = _run_identify(
        monkeypatch,
        eligible_dbs=[db],
        db_proxy={db: {"t_mod"}},             # 经 P（旧"单表"结果）可见
        db_logical_base={db: set()},          # 不在 only_base
        db_shard={db: set()},                 # 不在 S
        dir_rows_by_db={db: ["t_mod"]},       # L 含该表
        ddl_by_key={(db, "t_mod"): ddl},
    )
    # 证明"只补 only_base 不足"：该表不在 B，旧口径会漏；新四层在 C4 捞回
    assert sp["main"] == 1
    assert sp["outside_shard"] == 1
    assert sp["check_state"] == svc.SP_STATE_COMPLETE


# ════════════════════════════════════════════════════════════════════════════
# PAR-15：MODERN 主表仅在独立 L；或同时在 L/P/B 三处（去重只查一次计一次）
# ════════════════════════════════════════════════════════════════════════════
def test_par15_main_only_in_l(monkeypatch):
    db = "db1"
    db_sp, sp, warns = _run_identify(
        monkeypatch,
        eligible_dbs=[db],
        db_proxy={db: set()},
        db_logical_base={db: set()},
        db_shard={db: set()},
        dir_rows_by_db={db: ["t_mod"]},       # 仅在独立目录 L
        ddl_by_key={(db, "t_mod"): _ddl_modern("t_mod")},
    )
    assert sp["main"] == 1                    # 不漏
    assert sp["outside_shard"] == 1


def test_par15_main_in_l_p_b_dedup_query_once(monkeypatch):
    db = "db1"
    log = []
    db_sp, sp, warns = _run_identify(
        monkeypatch,
        eligible_dbs=[db],
        db_proxy={db: {"t_mod"}},             # P 含
        db_logical_base={db: {"t_mod"}},      # B 含
        db_shard={db: set()},
        dir_rows_by_db={db: ["t_mod"]},       # L 含 → 三处重复
        ddl_by_key={(db, "t_mod"): _ddl_modern("t_mod")},
        ddl_query_log=log,
    )
    # 三处重复只计一次、只 SHOW CREATE 一次（去重）
    assert sp["main"] == 1
    assert sp["candidates"] == 1
    assert log.count((db, "t_mod")) == 1


# ════════════════════════════════════════════════════════════════════════════
# PAR-16：目录枚举失败（无权/连接错误）→ inventory=FAILED，check 非 COMPLETE；
#         M-01 回归锁：失败路径不得附带 SP_DIRECTORY_TRUNCATED 假告警
# ════════════════════════════════════════════════════════════════════════════
def test_par16_directory_failure_no_false_truncated(monkeypatch):
    db = "db1"
    # 该表在 P/B 中（使候选非空），但目录枚举连接异常
    db_sp, sp, warns = _run_identify(
        monkeypatch,
        eligible_dbs=[db],
        db_proxy={db: {"t_mod"}},
        db_logical_base={db: set()},
        db_shard={db: set()},
        dir_error_by_db={db: Exception("SHOW FULL TABLES access denied")},
        ddl_by_key={(db, "t_mod"): _ddl_modern("t_mod")},
    )
    codes = _warn_codes(warns)
    assert "SP_DIRECTORY_FAILED" in codes
    # M-01：失败一行都没读到，不得叠加"触发 50000 行护栏/预算截断"的假告警
    assert "SP_DIRECTORY_TRUNCATED" not in codes
    assert sp["inventory_state"] == svc.SP_STATE_FAILED
    assert sp["inventory_state"] != svc.SP_STATE_COMPLETE
    assert sp["check_state"] != svc.SP_STATE_COMPLETE


def test_par16_directory_execute_1064_no_false_truncated(monkeypatch):
    db = "db1"
    db_sp, sp, warns = _run_identify(
        monkeypatch,
        eligible_dbs=[db],
        db_proxy={db: {"t_mod"}},
        db_logical_base={db: set()},
        db_shard={db: set()},
        dir_error_by_db={db: Exception(1064, "syntax error")},
        ddl_by_key={(db, "t_mod"): _ddl_modern("t_mod")},
    )
    codes = _warn_codes(warns)
    assert "SP_DIRECTORY_FAILED" in codes
    assert "SP_DIRECTORY_TRUNCATED" not in codes
    assert sp["inventory_state"] == svc.SP_STATE_FAILED


# ════════════════════════════════════════════════════════════════════════════
# PAR-17：L 中的视图不进候选；明确物理表（plain DDL）判负不计主表
# ════════════════════════════════════════════════════════════════════════════
def test_par17_view_excluded_and_physical_not_counted(monkeypatch):
    db = "db1"
    db_sp, sp, warns = _run_identify(
        monkeypatch,
        eligible_dbs=[db],
        db_proxy={db: set()},
        db_logical_base={db: set()},
        db_shard={db: set()},
        # L 含一个 VIEW（应被排除）+ 一个 plain 物理表（应判负）
        dir_rows_by_db={db: [("v_view", "VIEW"), ("t_phys", "BASE TABLE")]},
        ddl_by_key={(db, "t_phys"): _ddl_plain("t_phys")},
    )
    # 视图不进 C（candidates 只含 t_phys）
    assert sp["candidates"] == 1
    # plain 物理表判负：不计主表
    assert sp["main"] == 0
    assert sp["checked"] == 1                 # 已判明（非主表）
    assert sp["unknown"] == 0
    assert sp["check_state"] == svc.SP_STATE_COMPLETE


# ════════════════════════════════════════════════════════════════════════════
# PAR-18：141 迁移两表各八列契约（列数与默认值/类型语义）
# ════════════════════════════════════════════════════════════════════════════
def test_par18_migration_141_eight_columns_contract():
    import io
    import os
    path = os.path.join(os.path.dirname(__file__), "..",
                        "backend", "schema", "v14", "141_secondary_partition_main.sql")
    with io.open(path, encoding="utf-8") as f:
        sql = f.read()
    # 8 个新列（6 INT + 2 VARCHAR 状态），两表各一份
    cols = ["secondary_partition_main_tables", "secondary_partition_check_state",
            "secondary_partition_candidates", "secondary_partition_checked",
            "secondary_partition_unknown", "secondary_partition_unchecked",
            "secondary_partition_inventory_state", "secondary_partition_outside_shard"]
    for col in cols:
        # 每列在两表（table_type_stat / table_type_stat_item）各出现一次
        assert sql.count(col) >= 2, f"141 迁移缺列 {col}"
    for tbl in ("table_type_stat_item", "table_type_stat"):
        assert tbl in sql
    # 两个状态列默认 'LEGACY'
    assert sql.count("'LEGACY'") >= 4


# ════════════════════════════════════════════════════════════════════════════
# PAR-19：两库四层全含 + 名称顺序与层级相反 + L/P/B 重复 → C1→C2→C3→C4 调度、
#         层互斥且并集=C、每候选最多查一次
# ════════════════════════════════════════════════════════════════════════════
def test_par19_four_layers_exclusive_union_and_order(monkeypatch):
    log = []
    # 构造四层（全实例 (db,table) 维度）：
    #   C1 = C∩S：          db1.s_in  （在 S 与 L）
    #   C2 = C∩(B−P)：      db1.b_only （仅在 B，不在 P；在 L）
    #   C3 = L−(P∪B)：      db2.l_only （仅在 L）
    #   C4 = 剩余：          db2.rest   （在 P 与 L，不在 S/B；不在 B−P、不在 L−(P∪B)）
    dbs = ["db1", "db2"]
    db_proxy = {"db1": set(), "db2": {"rest"}}
    db_logical_base = {"db1": {"b_only"}, "db2": set()}
    db_shard = {"db1": {"s_in"}, "db2": set()}
    dir_rows = {"db1": ["s_in", "b_only"], "db2": ["l_only", "rest"]}
    ddl = {
        ("db1", "s_in"): _ddl_legacy("s_in"),
        ("db1", "b_only"): _ddl_modern("b_only"),
        ("db2", "l_only"): _ddl_modern("l_only"),
        ("db2", "rest"): _ddl_plain("rest"),
    }
    db_sp, sp, warns = _run_identify(
        monkeypatch, eligible_dbs=dbs, db_proxy=db_proxy,
        db_logical_base=db_logical_base, db_shard=db_shard,
        dir_rows_by_db=dir_rows, ddl_by_key=ddl, ddl_query_log=log)

    # 并集 = C，每候选最多查一次
    assert len(log) == len(set(log)) == 4
    # 调度层序：C1(db1.s_in) → C2(db1.b_only) → C3(db2.l_only) → C4(db2.rest)
    assert log.index(("db1", "s_in")) < log.index(("db1", "b_only"))
    assert log.index(("db1", "b_only")) < log.index(("db2", "l_only"))
    assert log.index(("db2", "l_only")) < log.index(("db2", "rest"))
    # 主表数：s_in(legacy) + b_only(modern) + l_only(modern) = 3；rest(plain) 判负
    assert sp["main"] == 3
    # 计数恒等式
    assert sp["candidates"] == sp["checked"] + sp["unknown"] + sp["unchecked"]


# ════════════════════════════════════════════════════════════════════════════
# PAR-20：共享 180s deadline 不重置；DDL 中途跨过截止点后不新开 I/O
# ════════════════════════════════════════════════════════════════════════════
def test_par20_shared_deadline_stops_new_io(monkeypatch):
    db = "db1"
    # 时钟：起步 0，每次 _now() 读 step=0；我们先让目录阶段读一次（返回 t），
    # 然后在 DDL 阶段前把时钟推到 deadline 之外，验证不再发起 SHOW CREATE。
    clock = FakeClock(start=0.0, step=0.0)
    log = []
    cfg_tables = [f"t{i}" for i in range(5)]
    ddl = {(db, t): _ddl_modern(t) for t in cfg_tables}

    # 目录阶段后立刻跨过 deadline：目录 execute 触发 _now()，之后把时钟推过界
    class _StopClock:
        def __init__(self):
            self.t = 0.0
            self.calls = 0

        def __call__(self):
            self.calls += 1
            # 前两次读（目录 deadline 判断）返回界内，之后推到界外
            return 0.0 if self.calls <= 2 else (svc.TOTAL_BUDGET_SECONDS + 10)

    stop_clock = _StopClock()
    db_sp, sp, warns = _run_identify(
        monkeypatch, eligible_dbs=[db],
        db_proxy={db: set(cfg_tables)}, db_logical_base={db: set()},
        db_shard={db: set()}, dir_rows_by_db={db: list(cfg_tables)},
        ddl_by_key=ddl, clock=stop_clock, ddl_query_log=log)

    # 跨过截止点后不新开 DDL I/O（一张都没查或在中途停止），剩余计 unchecked
    assert sp["unchecked"] + sp["unknown"] + sp["checked"] == sp["candidates"]
    assert sp["candidates"] == 5
    # 预算停止告警出现
    assert "SP_BUDGET_EXCEEDED" in _warn_codes(warns)
    # deadline 是 now+TOTAL_BUDGET_SECONDS（共享 180s），未被重置为更大值：
    # 通过"越过 180 即停"间接证明（若被重置 +300，则越过 180 不会停）
    assert len(log) < 5


# ════════════════════════════════════════════════════════════════════════════
# PAR（补）：广播表优先——shardkey=noshardkey_allset + PARTITION BY 不得计为
#            二级分区主表（SIT2 S2-02；工程师附件 §4.2 点名警告；M12b 证明此前无锁）
# ════════════════════════════════════════════════════════════════════════════
def test_par_broadcast_precedence_not_counted_as_main():
    """识别器级：广播+分区 → NOT_SECONDARY / BROADCAST，不计主表。"""
    ddl = ("CREATE TABLE `t` (`id` int) ENGINE=InnoDB shardkey=noshardkey_allset\n"
           "PARTITION BY RANGE (id) (PARTITION p0 VALUES LESS THAN (2))")
    e = classify_logical_ddl(ddl, "db", "t")
    assert e.state == STATE_NOT_SECONDARY
    assert e.distribution == "BROADCAST"


def test_par_broadcast_no_partition_also_not_main():
    """识别器级：广播无分区同样不计主表（对照组）。"""
    e = classify_logical_ddl(
        "CREATE TABLE `t2` (`id` int) ENGINE=InnoDB shardkey=noshardkey_allset",
        "db", "t2")
    assert e.state == STATE_NOT_SECONDARY
    assert e.distribution == "BROADCAST"


def test_par_broadcast_not_counted_in_collection(monkeypatch):
    """采集级：库内含广播分区表时 main 不把它算进去（与 Mr.Linsang 要的数字直接相关）。"""
    db = "db1"
    bc_ddl = ("CREATE TABLE `t_bc` (`id` int) ENGINE=InnoDB shardkey=noshardkey_allset\n"
              "PARTITION BY RANGE (id) (PARTITION p0 VALUES LESS THAN (2))")
    db_sp, sp, warns = _run_identify(
        monkeypatch,
        eligible_dbs=[db],
        db_proxy={db: set()},
        db_logical_base={db: {"t_bc"}},       # 广播表在逻辑基线 B
        db_shard={db: set()},
        dir_rows_by_db={db: ["t_bc"]},        # 目录 L 也含
        ddl_by_key={(db, "t_bc"): bc_ddl},
    )
    assert sp["main"] == 0                    # 广播分区表不得计为二级分区主表
    assert sp["checked"] == 1                 # 已判明（判负）
    assert sp["unknown"] == 0
    assert sp["outside_shard"] == 0


# ════════════════════════════════════════════════════════════════════════════
# QC-DEFECT-01：存在失败/跳过库时，实例汇总状态绝不能误判 COMPLETE
# ════════════════════════════════════════════════════════════════════════════
def test_qc_defect01_summary_not_complete_when_incomplete_dbs(monkeypatch):
    """QC-DEFECT-01：eligible 库全 COMPLETE，但存在失败/跳过库时，实例汇总必须
    降级为 PARTIAL，绝不能 COMPLETE（设计 §4.4：全部目标库均完成才 COMPLETE）。
    否则顶部卡片 COMPLETE 与明细失败行自相矛盾，掩盖分区遗漏风险。"""
    db = "db1"
    db_sp, sp, warns = _run_identify(
        monkeypatch,
        eligible_dbs=[db],
        db_proxy={db: {"t_mod"}},
        db_logical_base={db: set()},
        db_shard={db: set()},
        dir_rows_by_db={db: ["t_mod"]},
        ddl_by_key={(db, "t_mod"): _ddl_modern("t_mod")},
        has_incomplete_dbs=True,            # 模拟另有 1 个库 failed/skipped
    )
    # eligible 库本身判明为主表（main=1），但因存在失败/跳过库，实例汇总降级
    assert sp["main"] == 1
    assert sp["check_state"] == svc.SP_STATE_PARTIAL
    assert sp["check_state"] != svc.SP_STATE_COMPLETE
    assert sp["inventory_state"] == svc.SP_STATE_PARTIAL
    assert sp["inventory_state"] != svc.SP_STATE_COMPLETE


def test_qc_defect01_summary_complete_when_no_incomplete_dbs(monkeypatch):
    """QC-DEFECT-01 对照：无失败/跳过库时，eligible 全 COMPLETE → 实例汇总 COMPLETE（不误降级）。"""
    db = "db1"
    db_sp, sp, warns = _run_identify(
        monkeypatch,
        eligible_dbs=[db],
        db_proxy={db: {"t_mod"}},
        db_logical_base={db: set()},
        db_shard={db: set()},
        dir_rows_by_db={db: ["t_mod"]},
        ddl_by_key={(db, "t_mod"): _ddl_modern("t_mod")},
        has_incomplete_dbs=False,
    )
    assert sp["check_state"] == svc.SP_STATE_COMPLETE
    assert sp["inventory_state"] == svc.SP_STATE_COMPLETE
