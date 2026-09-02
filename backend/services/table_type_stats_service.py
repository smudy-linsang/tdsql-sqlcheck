# -*- coding: utf-8 -*-
"""G14 · 表类型统计（深度诊断子模块，DESIGN-v1.6.3.0 Rev.Q）

按 TDSQL 原厂口径统计单个实例下各业务库的表类型分布：

  分布式实例——逐业务库执行三条 Proxy 命令：
      /*proxy*/show table with shardkey           → 分片表
      /*proxy*/show table with noshardkey_allset  → 广播表
      /*proxy*/show table without shardkey        → 单表
  集中式实例——information_schema.TABLES 中 TABLE_TYPE='BASE TABLE' 计入单表，
      分片表/广播表恒为 0，视图不计。

2026-08-29 内网实测（设计附录 B）确定的形态：
  · 返回列名为 db_table，值为【库限定名】 sqltuning.t_max；
    with shardkey / with noshardkey_allset 另有第二列 info（shardkey:xxx），
    without shardkey 只有一列。
  · 三类结果集互斥（without shardkey 不含广播表）。
  · 某类为空时 Proxy 返回的是【OK 包】而非空结果集
    （`Query OK, 0 rows affected`，0.001 秒返回，不是挂起）。
    PyMySQL >= 1.1.0 对 OK 包 fetchall() 返回 []，cursor.description 为 None，
    故本模块天然按"该类 0 张"处理；赤兔页面转圈是其前端等列元数据所致，与本模块无关。
  · information_schema 会把【二级分区的物理子表】也列为 BASE TABLE，命名形如
    <逻辑表>_tdsql_subp190001 / _tdsql_subp202601。lzbj_ecif 实测：
    基线 293 = 逻辑表 215 + 子分区 78（6 张 sub_func:month 的表 × 13 个子分区）。
    本模块把子分区表从基线中剔除并单列计数，剔除后逻辑基线 215 与 Proxy 口径【精确相等】。
    故不得用未剔除的基线与 Proxy 口径比对（会产生 27% 的常态误报）。

Rev.Q（O 第二轮 UAT 整改）要点：
  · run_stats() 采集完成后生成一次 captured_at（精确到秒），显式写入历史行
    created_at 并随响应返回——实时"结果范围"展示的采集时间与 stat_id 对应
    历史记录严格同源，前端不得另取本机时间冒充（UAT2-O-G14-02）。

Rev.M（A 第五轮评审定点整改）要点：
  · 基线 SQL 使用可下推的普通 TABLE_SCHEMA IN；不假定服务端大小写语义，
    正确性完全由全实例 known canonical 解析 + 目标集合精确过滤承担（P2-01）。
  · T4-R01 只验证服务端多返 Sales/sales 时的结果行为，不固化 SQL 关键字；
    最大内网实例须按 T20 留存普通 IN 与 BINARY IN 的 EXPLAIN/耗时对比。

Rev.L（O 第四轮评审整改）要点：
  · 基线查询增加应用层防御：用全实例 known 解析 canonical 后对目标库精确过滤；
    Rev.M 进一步确认 SQL 无需也不得依赖 BINARY（P1-01 / Rev.M P2-01）。
  · 删除 215 秒硬上界常量与论证。PyMySQL read_timeout 是每次底层读取超时，
    不是整条 SQL 总时间；180 秒只承诺服务层在 checkpoint 后不新开阶段/库/命令（P1-02）。

Rev.K（O 第三轮评审整改）要点：
  · 目标库过滤改为**精确成员判断**：canonical name 不得再做第二次大小写回退，
    否则指定单库时会把大小写兄弟库的行并进来（P1-01）。
  · 预算耗尽改为**标志位 + 正常退出 with**，绝不抛异常穿出连接上下文
    ——那会让一条健康连接被连接池销毁重建，并可能把 SKIPPED 误标成 FAILED（P1-02）。
  · 当时曾将目标采集上界更正为 215 秒；**Rev.L 已证明该硬上界仍不成立并删除**。
  · 明细落库改为批量（500 库 500 次往返 → 5 次）（P1-02）。
  · resolve() 之后补一次 deadline 检查——它在缓存未命中时会向目标实例发探测 SQL。

Rev.J（O 第二轮评审整改）要点：
  · 库名不再无条件小写：`Sales` 与 `sales` 在 lower_case_table_names=0 下是
    两个不同 schema，单值 lower 字典会把它们合并成一个（P1-01）。改用 _NameSpace：
    精确优先、CI 回退仅在候选唯一时生效、歧义显式报 DB_NAME_AMBIGUOUS。
  · 180 秒预算由 run_stats 统一建立并贯穿 SHOW DATABASES / 基线查询 / 每一条
    Proxy 命令；其语义以 Rev.L 的软预算为准。
  · 失败/跳过库的基线、子分区、重叠一律不进实例级汇总——先定状态再算汇总（P1-03）。
  · 结构验收升级为完整字段契约：类型 + 长度 + 字符集 + 可空性 + 默认值 + 自增
    + 索引全列序与唯一性（P1-04）。
  · 物理子表判定增加第三个条件：候选自身不得出现在 Proxy 结果中（P2-01）。

Rev.G（O 首轮评审整改）要点：
  · 取消"指纹相同即提前停止"——两库指纹相同只证明结果与当前默认库无关，
    不证明已覆盖全部目标库（P1-01）。改为无条件逐库执行，正确性优先。
  · 二级分区物理子表的识别：仅对分布式生效，且要求"逻辑父表确实出现在
    Proxy 结果里"才判定为子表（P1-03）。集中式一律不剔除。
  · 每库一个连接上下文，异常穿出 with 触发连接池重建，避免坏连接被后续库复用（P1-04）。
  · 单库三条命令暂存后原子合入全局，任一失败即整库丢弃（P1-05）。
  · run_stats 进入既有 registry.scan_slot(connection_id) 并发槽位，与 SQL 审核/
    慢查询扫描共用同一套按连接 + 全局的限流，超限抛 ScanBusyError → 429（P1-02）。
  · run_stats 入口做落库表结构验收（列 / 类型 / 索引），
    避免采集完才在 INSERT 处失败（P1-08）。
  · 指定库必须真实存在（P2-01）；connection_id 必须非空（P2-03）。

设计要点（详见 DESIGN-v1.6.3.0）：
  · 结果按【库限定名】归属到库，而不是无条件算在当前会话库上——
    命令的作用域是否为实例级尚未确证，按库归属 + (库,表) 去重使两种
    作用域都得到正确结果（§3.3 RISK-E）。这也正是原厂"使用数据库名+表名
    去重"这句话的由来。
  · 基线口径：剔除二级分区物理子表后与 Proxy 口径精确对齐，使交叉校验重新成为
    有效信号（否则每个库都会常态告警 27%，等于把告警训练成噪声）。
  · 软时长预算 + 显式读空闲超时：到期后不再开新操作，连接持续无数据时可退出；
    二者都不是整个请求的硬墙钟上界（§3.3 RISK-F，KL-19）。
  · 绝不在共享连接池上切库；另建 pool_size=1 的临时池（ADR-3）。

全部只读。不修改任何既有模块。
"""
from __future__ import annotations

import dataclasses
import json
import logging
import re
import time
from datetime import datetime
from typing import Optional

from backend.services.connection_registry import registry
from backend.services.database import _get_connection
from backend.services.tdsql_connector import TDSQLConnectionPool

logger = logging.getLogger("tdsql.table_type_stats")

# ── 原厂命令常量。逐字保留：禁止改写 / 拼接 / 加分号 / strip（ADR-10）────────
SQL_SHARD = "/*proxy*/show table with shardkey"
SQL_BROADCAST = "/*proxy*/show table with noshardkey_allset"
SQL_SINGLE = "/*proxy*/show table without shardkey"

KIND_SHARD = "shard"
KIND_BROADCAST = "broadcast"
KIND_SINGLE = "single"

# 元组顺序即归一化优先级：分片 > 广播 > 单表（ADR-2）
_KIND_SQL = ((KIND_SHARD, SQL_SHARD),
             (KIND_BROADCAST, SQL_BROADCAST),
             (KIND_SINGLE, SQL_SINGLE))
_KIND_PRIORITY = {KIND_SHARD: 0, KIND_BROADCAST: 1, KIND_SINGLE: 2}

# 系统库口径 = index_audit_service._SYS ∪ zk_scan_enrich_service.SYSTEM_DATABASES。
# 本模块自持、不 import 其他 service（ADR-8）；超集关系由单测钉住。
_SYS_DB = frozenset({
    "information_schema", "mysql", "performance_schema", "sys",
    "sysdb", "query_rewrite", "xa",
    "tdsqlpcloud", "tdsqlpcloud_monitor", "__tencentdb__",
})

MAX_DATABASES = 500           # 库数护栏。超出即截断并显式告警（绝不静默少算）
MAX_DIFF_SAMPLE = 20          # 差集样本上限，防止 detail 撑爆 VARCHAR(512)
COMMAND_READ_TIMEOUT = 30     # 临时池 socket 每次读取的空闲超时（非整条 SQL 总时间）
CONNECT_TIMEOUT = 5           # 临时池单次建连超时（秒）

# 采集软预算：服务层在 checkpoint 后不再启动新阶段/库/命令，未采完的库标 SKIPPED。
# 它是"不再开新操作"的截止线，不是墙钟上界。
#
# Rev.L / P1-02：这是**软 deadline**。在实例类型探测、库枚举、基线查询、
# 进入某库的连接上下文前、拿到连接后/切库前、每条 Proxy 命令前检查；
# checkpoint 到期后服务层不再新开阶段/库/命令。已进入的池/驱动内部步骤可继续。
# 它不是墙钟硬上界：PyMySQL read_timeout 是每次底层 socket 读取的空闲超时，
# 不是整条 SQL 总时间；连接池还可经历 ping / 建连 / select_db / 异常重建组合路径。
# `_ensure_schema()` 与结果落库也不在该 deadline 内。故不定义任何 MAX_*WALL* 常量。
TOTAL_BUDGET_SECONDS = 180
ITEM_INSERT_BATCH = 100       # 明细落库批量大小。500 库从 500 次往返降到 5 次

# 表名列识别规则（§6.3）。自上而下，命中即停。
# db_table 为 2026-08-29 内网实测确认的真实列名（附录 B）。
_EXACT_NAME_COLS = ("db_table", "table", "table_name", "tables", "name")
_PREFIX_NAME_COLS = ("tables_in_",)
_EXCLUDE_TOKENS = ("type", "rows", "schema", "comment", "engine", "key", "info")

# TDSQL 二级分区的物理子表命名（2026-08-29 内网实测，设计附录 B.5）：
#   cus_pub_translog_tdsql_subp190001 / _tdsql_subp202601 … _tdsql_subp202612
# 它们在 information_schema 里是独立的 BASE TABLE，但【不是】用户认知中的"表"，
# Proxy 的 show table 也只返回逻辑表名。故从逻辑基线中剔除并单列计数。
#
# Rev.G（P1-03）：命名匹配【只是必要条件，不是充分条件】。
#   · 集中式实例根本没有二级分区物理子表这一构造 —— 一律不剔除，
#     否则一张合法业务表 orders_tdsql_subp202601 会被静默少算，且集中式
#     没有 Proxy 交叉校验兜底，错误不可见（违反 REQ-5）。
#   · 分布式实例额外要求【逻辑父表确实出现在本库的 Proxy 结果中】才判定为子表。
#     父表 = 表名去掉 _tdsql_subp<数字> 后缀的部分。
#     实测校验：cus_pub_translog_tdsql_subp202601 → 父表 cus_pub_translog
#     确在 show table with shardkey 的 98 行内。
#   · 父表不存在时保留为逻辑表 —— 后果是 RECON_MISMATCH 显式报出（可见），
#     而不是静默少算（不可见）。方向是安全的。
_SUBPARTITION_RE = re.compile(r"^(?P<parent>.+?)_tdsql_subp\d+$", re.IGNORECASE)

_PERM_ERRNO = (1044, 1045, 1142, 1143, 1227)
_SYNTAX_ERRNO = 1064

# 可测性钩子：单测用 monkeypatch 注入 FakePool（§11）。生产恒为真实连接池。
_new_pool = TDSQLConnectionPool
# 可测性钩子（Rev.J / P1-02）：单测用可控时钟驱动 deadline，
# 让"命令各耗多少秒、什么时候不再启动新命令"成为可断言的事实，而不是靠 sleep 碰运气。
# 生产恒为 time.monotonic。第二处、也是最后一处为可测性做的让步，成本 1 行。
_now = time.monotonic


# ══════════════════════════════════════════════════════════════════
# 小工具
# ══════════════════════════════════════════════════════════════════
class _NameSpace:
    """库名命名空间：精确优先，大小写不敏感回退【仅在候选唯一时】生效。

    Rev.J / P1-01：Rev.I 用的是 `{name.lower(): name}` 单值字典。
    在 `lower_case_table_names=0` 的 Linux/TDSQL 上，`Sales` 与 `sales` 是
    两个不同的 schema——单值字典会让后者覆盖前者，于是：
      · 两个库的 information_schema 基线被归进同一个键；
      · Proxy 行被归给字典里最后那个库；
      · 另一个库显示 0，或者产生虚假的 RECON_MISMATCH。
    这是四个主数字的**静默**正确性问题，不能靠告警兜底。

    本类把"一对多"如实表示出来：
      resolve("sales")  → 精确命中 → "sales"
      resolve("SALES")  → 无精确命中；小写候选若唯一则用之，若有多个 → None（歧义）
    歧义返回 None，由调用方显式记一条 DB_NAME_AMBIGUOUS 告警——
    **把不可判定的情况变成可见的，而不是替使用者猜一个。**
    """

    __slots__ = ("_exact", "_ci")

    def __init__(self, names):
        self._exact = set()
        self._ci = {}
        for n in names:
            n = str(n)
            self._exact.add(n)
            self._ci.setdefault(n.lower(), []).append(n)

    def __contains__(self, name) -> bool:
        return str(name) in self._exact

    def __len__(self) -> int:
        return len(self._exact)

    def resolve(self, name):
        """返回 canonical 名；无法唯一确定时返回 None。"""
        s = str(name)
        if s in self._exact:
            return s
        cands = self._ci.get(s.lower(), ())
        return cands[0] if len(cands) == 1 else None

    def is_ambiguous(self, name) -> bool:
        """名字本身不精确存在，且大小写候选有多个。"""
        s = str(name)
        return s not in self._exact and len(self._ci.get(s.lower(), ())) > 1

    def variants(self, name) -> list:
        """与 name 仅大小写不同的全部真实库名（含自身）。"""
        return sorted(self._ci.get(str(name).lower(), ()))


def _errno_of(exc: BaseException) -> Optional[int]:
    """提取数据库 errno，沿 __cause__ 链上溯一层。"""
    for e in (exc, getattr(exc, "__cause__", None)):
        args = getattr(e, "args", None) if e is not None else None
        if args and isinstance(args[0], int):
            return args[0]
    return None


def _err(exc: BaseException) -> str:
    """把异常渲染成可直接呈现给使用者的处置提示。"""
    errno_ = _errno_of(exc)
    msg = str(exc)[:200]
    if errno_ in _PERM_ERRNO:
        return f"[errno {errno_}] 授权不足：{msg}"
    if errno_ == _SYNTAX_ERRNO:
        return f"[errno {errno_}] 语法错误（该连接可能非 Proxy 端点）：{msg}"
    low = msg.lower()
    if "timed out" in low or "timeout" in low:
        return f"读超时（{COMMAND_READ_TIMEOUT}s）：{msg}"
    return f"[errno {errno_}] {msg}" if errno_ else msg


def _warn(code: str, severity: str, db_name: str, detail) -> dict:
    return {"code": code, "severity": severity,
            "db_name": db_name, "detail": str(detail)[:512]}


def _diff_sample(names) -> str:
    ordered = sorted(names)
    text = ", ".join(ordered[:MAX_DIFF_SAMPLE])
    if len(ordered) > MAX_DIFF_SAMPLE:
        text += f" …等 {len(ordered)} 张"
    return text


def _pick_name_column(columns: list):
    """选出承载表名的列。返回 (列名, 是否为兜底猜测)。"""
    if not columns:
        return None, False
    if len(columns) == 1:
        return columns[0], False
    lowers = [(c, str(c).lower()) for c in columns]
    for col, low in lowers:
        if low in _EXACT_NAME_COLS:
            return col, False
    for col, low in lowers:
        if any(low.startswith(p) for p in _PREFIX_NAME_COLS):
            return col, False
    for col, low in lowers:
        if "table" in low and not any(t in low for t in _EXCLUDE_TOKENS):
            return col, False
    return columns[0], True          # 兜底：取第一列，并标记形态未知


def _split_qualified(raw, current_db: str, known: "_NameSpace"):
    """把 db_table 值拆成 (库名, 表名, 是否歧义)。

    实测形态为 `sqltuning.t_max`。仅当点号左侧确为一个【已知库名】时才拆分，
    否则整体视为当前库下的表名——避免把含点号的表名误拆后被过滤掉（少算）。

    Rev.J / P1-01：库名比对交给 _NameSpace——精确优先，大小写回退仅在候选唯一时生效。
    左侧命中了某个库名的小写形式、但真实存在多个大小写变体时，**无法判定归属**，
    此时回 (current_db, 原串, True)：不猜、不丢，由调用方记 DB_NAME_AMBIGUOUS。
    """
    s = str(raw if raw is not None else "").strip()
    if not s:
        return current_db, "", False
    if "." in s:
        head, tail = s.split(".", 1)
        head = head.strip().strip("`").strip()
        tail = tail.strip().strip("`").strip()
        if head and tail:
            owner = known.resolve(head)
            if owner is not None:
                return owner, tail, False
            if known.is_ambiguous(head):
                return current_db, s.strip("`").strip(), True
    return current_db, s.strip("`").strip(), False


def _extract_pairs(rows, current_db: str, known: "_NameSpace"):
    """提取 {(库名, 表名)}。

    返回 (集合, 实际列名, 形态是否未知, 是否含跨库行, 歧义库名样本)。
    """
    rows = rows or []
    if not rows:
        return set(), [], False, False, set()
    first = rows[0]
    if isinstance(first, dict):
        columns = list(first.keys())
        col, guessed = _pick_name_column(columns)
        values = [r.get(col) if isinstance(r, dict) else None for r in rows]
    else:
        columns, guessed = [], False
        values = [(r[0] if r else None) for r in rows]
    pairs, cross, ambiguous = set(), False, set()
    for v in values:
        qual, name, amb = _split_qualified(v, current_db, known)
        if not name:
            continue
        if amb:
            ambiguous.add(str(v))
            continue          # 归属不可判定：不猜、不误计，由告警显式报出
        pairs.add((qual, name))
        if qual != current_db:
            cross = True
    return pairs, [str(c) for c in columns], guessed, cross, ambiguous


# ══════════════════════════════════════════════════════════════════
# 采集
# ══════════════════════════════════════════════════════════════════
def show_databases(pool) -> list:
    """SHOW DATABASES 原始结果（含系统库）。失败抛出。"""
    rows = pool._execute("SHOW DATABASES") or []
    names = []
    for row in rows:
        if isinstance(row, dict):
            val = row.get("Database") or row.get("database")
            if val is None:
                vals = list(row.values())
                val = vals[0] if vals else ""
        else:
            val = row[0] if row else ""
        name = str(val or "").strip()
        if name:
            names.append(name)
    return names


def list_business_databases(pool):
    """枚举业务库。返回 (业务库列表, 是否被 MAX_DATABASES 截断, 全部库名)。"""
    allnames = show_databases(pool)
    names = [n for n in allnames if n.lower() not in _SYS_DB]
    names.sort(key=lambda s: (s.lower(), s))
    truncated = len(names) > MAX_DATABASES
    return names[:MAX_DATABASES], truncated, allnames


def _collect_baseline(pool, dbs: list, known: "_NameSpace") -> dict:
    """取 information_schema 全量名单。

    返回 {db: {"base": 全部 BASE TABLE, "view": 视图}}。
    Rev.G（P1-03）：**不在此处剔除二级分区子表**——是否为子表要等 Proxy 结果回来后
    结合"逻辑父表是否存在"才能判定，且集中式一律不剔除。分类下沉到 _classify_subpartitions。
    """
    out = {d: {"base": set(), "view": set()} for d in dbs}
    if not dbs:
        return out
    placeholders = ",".join(["%s"] * len(dbs))
    rows = pool._execute(
        "SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE "
        "FROM information_schema.TABLES "
        f"WHERE TABLE_SCHEMA IN ({placeholders})", tuple(dbs)) or []
    # Rev.M / P2-01：普通 IN 只做参数化候选集查询并保留谓词下推，
    # 不假定服务端大小写语义。正确性完全由应用层承担：先在【全实例命名空间】
    # 解析 canonical 库名，再对目标子集精确过滤；不得在目标子集上再做 CI 回退。
    target = _NameSpace(dbs)  # 只使用 __contains__ 的精确成员语义，不调 resolve()
    for r in rows:
        if not isinstance(r, dict):
            continue
        schema = str(r.get("TABLE_SCHEMA") or r.get("table_schema") or "").strip()
        owner = known.resolve(schema)
        if owner is None or owner not in target:
            continue
        name = str(r.get("TABLE_NAME") or r.get("table_name") or "").strip()
        if not name:
            continue
        ttype = str(r.get("TABLE_TYPE") or r.get("table_type") or "").strip().upper()
        if ttype == "BASE TABLE":
            out[owner]["base"].add(name)
        elif ttype == "VIEW":
            out[owner]["view"].add(name)
    return out


def _classify_subpartitions(base: set, proxy_tables: set) -> tuple:
    """把 BASE TABLE 名单拆成 (逻辑表, 二级分区物理子表)。

    判定为子表需【同时】满足三条：
      1) 名字匹配 `<父表>_tdsql_subp<数字>`；
      2) 父表确实出现在本库的 Proxy 结果里（Rev.G / P1-03）；
      3) **候选自身不在 Proxy 结果里**（Rev.J / P2-01）。

    第 3 条是 Rev.J 补的，理由很硬：**真正的物理子表根本不会被
    `/*proxy*/show table` 返回**——Proxy 只认逻辑表。所以"它自己也在 Proxy 结果里"
    就直接证伪了"它是物理子表"。少了这一条，一对合法的业务表
    `orders` 与 `orders_tdsql_subp202601`（两者都是真逻辑表、都在 Proxy 结果中）
    会让后者被错判成子分区：页面的「二级分区子表」多算一张，
    逻辑基线少算一张，还会凭空冒出一条"仅 Proxy 可见"的 RECON_MISMATCH。

    只满足 1) 不满足 2)/3) 的表一律保留为逻辑表——宁可让 RECON_MISMATCH
    把它显式报出来，也不静默少算。集中式分支传入 proxy_tables=空集，
    第 2 条恒不成立，等价于"一律不剔除"。
    """
    subp = set()
    for name in base:
        if name in proxy_tables:
            continue                                  # 条件 3：Proxy 认它是逻辑表
        m = _SUBPARTITION_RE.match(name)
        if m and m.group("parent") in proxy_tables:    # 条件 1 + 2
            subp.add(name)
    return base - subp, subp


def _blank_item(db: str) -> dict:
    return {"db_name": db, "total_tables": 0, "shard_tables": 0,
            "broadcast_tables": 0, "single_tables": 0,
            "baseline_tables": 0, "subpartition_tables": 0,
            "status": "OK", "detail": ""}


def _collect_centralized(dbs: list, baseline: dict):
    """集中式：纯内存换算，不发任何查询、不发任何 /*proxy*/ 命令（ADR-4）。

    Rev.G（P1-03）：**不剔除任何 _tdsql_subp 表**——集中式没有二级分区物理子表
    这一构造，剔除只会把合法业务表静默少算，且此分支没有 Proxy 交叉校验兜底。
    """
    items = []
    totals = {"shard": 0, "broadcast": 0, "single": 0, "total": 0,
              "baseline": 0, "subp": 0, "overlap": 0, "failed": 0, "skipped": 0}
    for db in dbs:
        base = baseline.get(db, {}).get("base", set())
        n = len(base)
        item = _blank_item(db)
        item["total_tables"] = n
        item["single_tables"] = n
        item["baseline_tables"] = n
        items.append(item)
        totals["single"] += n
        totals["total"] += n
        totals["baseline"] += n
    return items, [], {}, totals


def _collect_distributed(pool, dbs: list, baseline: dict, known: "_NameSpace",
                        deadline: float):
    """分布式：逐业务库执行三条 /*proxy*/ 命令，按【库限定名】归属去重。

    Rev.G 的三处结构性变化（保留）：
      P1-01  取消"指纹相同即提前停止"——两库指纹相同只证明结果与当前默认库无关，
             不能证明已覆盖全部目标库。改为**无条件逐库执行**。
      P1-04  **每库一个连接上下文**。异常一律穿出 with，由
             TDSQLConnectionPool.get_connection() 关闭并重建线程本地连接后再抛出，
             外层逐库捕获后继续下一库——坏连接不会被后续库复用。
      P1-05  单库三条命令先写入**暂存区**，三条全部成功才原子合入全局。

    Rev.J 新增：
      P1-01  库名归属交给 _NameSpace（精确优先 / CI 回退仅在唯一时），
             歧义行不猜归属，记 DB_NAME_AMBIGUOUS。
      P1-02  deadline 由 analyze 统一建立并传入，**拿到连接后/切库前**
             与**每条命令开始前**都检查，
             不再是"每库检查一次"——把最坏超出从 3×30s 压到 1×30s。
      P1-03  失败/跳过库的**任何**数据都不进实例级汇总（含基线、子分区、重叠）。
    """
    items, warnings, shape = [], [], {}
    totals = {"shard": 0, "broadcast": 0, "single": 0, "total": 0,
              "baseline": 0, "subp": 0, "overlap": 0, "failed": 0, "skipped": 0}
    if not dbs:
        return items, warnings, shape, totals

    target = _NameSpace(dbs)
    kind_map = {}          # (db, table) -> kind
    kinds_seen = {}        # (db, table) -> {kind, ...}
    failed, skipped = {}, {}
    shape_reported = False
    instance_wide = False
    syntax_errors = 0
    scanned = 0
    ambiguous_samples = set()

    cfg = dataclasses.replace(pool.config, database=dbs[0],
                              read_timeout=COMMAND_READ_TIMEOUT,
                              connect_timeout=CONNECT_TIMEOUT)
    tmp = _new_pool(cfg, pool_size=1)
    try:
        for db in dbs:
            if _now() >= deadline:
                skipped[db] = "budget"
                continue
            scanned += 1
            detail = ""
            staged = {}          # kind -> {(库, 表)}
            staged_cols = {}     # kind -> 实际列名
            staged_guessed = False
            staged_cross = False
            staged_amb = set()
            budget_hit = False
            try:
                # P1-04：每库独立上下文；【真实的】异常穿出即触发连接重建
                with tmp.get_connection() as conn:
                    # Rev.L / P1-02：get_connection() 内部可先 ping，ping 失败后再建连。
                    # 这串已启动的内部步骤可能跨过 deadline；拿到连接后必须再查一次，
                    # 否则会在 deadline 之后还新发 select_db。只置标志以正常退出 with。
                    if _now() >= deadline:
                        budget_hit = True
                    else:
                        conn.select_db(db)
                    for kind, sql in (() if budget_hit else _KIND_SQL):
                        # P1-02：每条命令开始前再查一次 deadline。
                        # 单库三条命令最坏 3×30s=90s，只在库开始时查一次的话，
                        # 179 秒进库就能跑到 269 秒——那个"180 秒"的承诺不成立。
                        #
                        # Rev.K / P1-02：这里【只置标志、正常退出 with】，
                        # 绝不抛异常。TDSQLConnectionPool.get_connection() 会捕获
                        # 穿出上下文的**任何**异常并关闭 + 重建连接
                        # （tdsql_connector.py:287-307）——用它承载"预算耗尽"这种
                        # 正常控制信号，等于让一条健康连接在 deadline 之后被销毁重建，
                        # 白白多付一次 connect_timeout；重连再失败的话，新异常还会
                        # 盖掉原信号，把本该 SKIPPED 的库误标成 FAILED。
                        # **连接重建只应由真实的数据库/网络异常触发。**
                        if _now() >= deadline:
                            budget_hit = True
                            break
                        with conn.cursor() as cur:
                            cur.execute(sql)
                            rows = cur.fetchall()
                        # rows 可能是 OK 包（某类为空时 TDSQL 返回
                        # `Query OK, 0 rows affected`）——此时 fetchall() 为 []
                        # 且无列元数据，_extract_pairs 按 0 张处理，不是错误。
                        pairs, columns, guessed, cross, amb = _extract_pairs(
                            rows, db, known)
                        staged[kind] = pairs
                        if columns:
                            staged_cols[kind] = columns
                        staged_guessed = staged_guessed or guessed
                        staged_cross = staged_cross or cross
                        staged_amb |= amb
            except Exception as e:                           # noqa: BLE001
                detail = f"{db} 采集失败: {_err(e)}"
                if _errno_of(e) == _SYNTAX_ERRNO:
                    syntax_errors += 1
            if budget_hit:
                # 预算在本库中途耗尽：本库【未采完】，按 SKIPPED 处理而不是 FAILED
                # ——"没来得及测"与"测了但错了"处置动作不同（ADR-14）。
                # 暂存区整体丢弃：半个库的数据不是"部分成功"，是错的。
                skipped[db] = "budget"
                continue
            if detail:
                # P1-05：整库丢弃暂存区，绝不半量合入
                failed[db] = detail[:512]
                continue

            # ── 三条全成功，原子合入全局 ──────────────────────────
            for kind, cols in staged_cols.items():
                shape.setdefault(kind, cols)
            if staged_cross:
                instance_wide = True
            ambiguous_samples |= staged_amb
            if staged_guessed and not shape_reported:
                shape_reported = True
                warnings.append(_warn(
                    "SHAPE_UNKNOWN", "WARNING", db,
                    f"未能识别表名列，已退化为取第一列；实际列名: {staged_cols}"))
            for kind, _sql in _KIND_SQL:
                for qual, name in staged.get(kind, ()):
                    # Rev.K / P1-01：qual 已由 _extract_pairs 用【全实例命名空间】
                    # known 解析成 canonical name，这里必须**精确成员判断**。
                    # Rev.J 在这里又做了一次 target.resolve()——目标子集只含一个库时，
                    # CI 回退会把另一个真实库的行"唯一命中"到目标库上：
                    # 指定 database="Sales" 时，实例级返回里的 sales.t_lower
                    # 会被算进 Sales。**canonical name 不得再做第二次大小写回退。**
                    if qual not in target:
                        continue              # 非目标库（系统库 / 被 database 筛掉的库）
                    owner = qual
                    if name in baseline.get(owner, {}).get("view", ()):
                        continue              # 原厂口径：不统计视图
                    key = (owner, name)
                    kinds_seen.setdefault(key, set()).add(kind)
                    cur_kind = kind_map.get(key)
                    if (cur_kind is None
                            or _KIND_PRIORITY[kind] < _KIND_PRIORITY[cur_kind]):
                        kind_map[key] = kind
    finally:
        try:
            tmp.close_all()
        except Exception:                                    # noqa: BLE001
            logger.debug("临时连接池关闭失败（忽略）", exc_info=True)

    # ── 组装逐库结果 ──────────────────────────────────────────────
    #
    # Rev.J / P1-03：**先定状态，再算汇总。**
    # Rev.I 把 baseline / subp 的累加写在状态判断【之前】，overlap 又是对整个
    # kinds_seen 求和——于是：
    #   · 失败库的基线、子分区照样进实例级汇总；
    #   · 某个成功库的实例级返回里携带的失败库行，会让 overlap_count 被加 1；
    #   · 极端情况（全部 SKIPPED）下 total_tables=0 而 baseline_tables 仍是全库合计。
    # 这与接口文案和 W1 告警里"未计入任何汇总数"直接矛盾。
    # 现在先算出 eligible（既未失败也未跳过的库），所有实例级汇总一律按 owner 过滤。
    eligible = _NameSpace([d for d in dbs if d not in failed and d not in skipped])
    per_db = {d: {KIND_SHARD: 0, KIND_BROADCAST: 0, KIND_SINGLE: 0} for d in dbs}
    for (d, _t), kind in kind_map.items():
        per_db[d][kind] += 1
    overlap_total = sum(len(v) - 1 for k, v in kinds_seen.items()
                        if len(v) > 1 and k[0] in eligible)
    recon = []                 # [(db, 仅Proxy可见数, 仅基线可见数)]

    for db in dbs:
        item = _blank_item(db)
        proxy_tables = {t for (d, t) in kind_map if d == db}
        raw_base = baseline.get(db, {}).get("base", set())
        # P1-03（首轮）：结合 Proxy 结果做子表判定（父表必须在 Proxy 结果里）
        logical_base, subp = _classify_subpartitions(raw_base, proxy_tables)
        # 逐库行如实显示基线与子分区（information_schema 那一侧确实查成功了），
        # 但**只有 eligible 库计入实例级汇总**，否则 total 与 baseline 覆盖的
        # 库集合不同，两个数并排放就失去了互相印证的意义。
        item["baseline_tables"] = len(logical_base)
        item["subpartition_tables"] = len(subp)

        if db in failed:
            item["status"] = "FAILED"
            item["detail"] = failed[db]
            totals["failed"] += 1
            items.append(item)
            continue
        if skipped.get(db) == "budget":
            item["status"] = "SKIPPED"
            item["detail"] = f"超出总时长预算 {TOTAL_BUDGET_SECONDS}s，未采集"
            totals["skipped"] += 1
            items.append(item)
            continue

        totals["baseline"] += len(logical_base)
        totals["subp"] += len(subp)
        c = per_db[db]
        item["shard_tables"] = c[KIND_SHARD]
        item["broadcast_tables"] = c[KIND_BROADCAST]
        item["single_tables"] = c[KIND_SINGLE]
        item["total_tables"] = c[KIND_SHARD] + c[KIND_BROADCAST] + c[KIND_SINGLE]
        totals["shard"] += item["shard_tables"]
        totals["broadcast"] += item["broadcast_tables"]
        totals["single"] += item["single_tables"]
        totals["total"] += item["total_tables"]

        only_proxy, only_base = proxy_tables - logical_base, logical_base - proxy_tables
        if only_proxy or only_base:
            recon.append((db, len(only_proxy), len(only_base)))
            d2 = (f"Proxy 口径 {len(proxy_tables)} 张，information_schema 逻辑基线 "
                  f"{len(logical_base)} 张")
            if only_base:
                d2 += f"；仅基线可见({len(only_base)}): {_diff_sample(only_base)}"
            if only_proxy:
                d2 += f"；仅 Proxy 可见({len(only_proxy)}): {_diff_sample(only_proxy)}"
            item["detail"] = d2[:512]
        items.append(item)

    totals["overlap"] = overlap_total
    if failed:
        # P1-07：失败库汇总为一条告警，逐库详情留在 item.detail，
        # 避免 500 库全失败时 warnings_json 撑爆存储、前端渲染数百条横幅
        names = ", ".join(sorted(failed)[:5])
        if len(failed) > 5:
            names += f" …等 {len(failed)} 个库"
        warnings.append(_warn(
            "PROXY_CMD_FAILED", "ERROR", "",
            f"{len(failed)} 个库采集失败，未计入任何汇总数（{names}）；"
            f"逐库失败原因见各行「说明」"))
    if ambiguous_samples:
        warnings.append(_warn(
            "DB_NAME_AMBIGUOUS", "ERROR", "",
            f"{len(ambiguous_samples)} 行的库限定名无法唯一归属"
            f"（实例上存在仅大小写不同的同名库）：{_diff_sample(ambiguous_samples)}。"
            f"这些行**未计入任何库**——归属靠猜会把两个真实的库算成一个，"
            f"宁可少算并显式报出。请用「库名」输入框逐库精确统计"))
    if overlap_total:
        warnings.append(_warn(
            "KIND_OVERLAP", "WARNING", "",
            f"三类结果集存在 {overlap_total} 处重叠，"
            f"已按 分片>广播>单表 归一化去重，总数未重复计算"))
    if recon:
        sum_proxy = sum(x[1] for x in recon)
        sum_base = sum(x[2] for x in recon)
        names = ", ".join(x[0] for x in recon[:5])
        if len(recon) > 5:
            names += f" …等 {len(recon)} 个库"
        warnings.append(_warn(
            "RECON_MISMATCH", "WARNING", "",
            f"{len(recon)} 个库的 Proxy 口径与 information_schema 逻辑基线不一致："
            f"仅基线可见合计 {sum_base} 张、仅 Proxy 可见合计 {sum_proxy} 张（{names}）。"
            f"二级分区物理子表已剔除，故此差异【不是】分区造成的，"
            f"可能存在未纳入 Proxy 路由的表；差异明细见各行「说明」"))
    if totals["subp"]:
        warnings.append(_warn(
            "SUBPARTITION_EXCLUDED", "INFO", "",
            f"information_schema 中另有 {totals['subp']} 张二级分区物理子表"
            f"（形如 xxx_tdsql_subp202601，且其逻辑父表确在 Proxy 结果中），"
            f"按逻辑表口径未计入总数；逐库数量见「二级分区子表」列"))
    if instance_wide:
        warnings.append(_warn(
            "INSTANCE_WIDE_SCOPE", "INFO", "",
            f"本版本 /*proxy*/show table 返回实例级全量（结果含跨库行），"
            f"已按库限定名归属并按(库,表)去重；"
            f"为保证覆盖完整性，仍逐库执行（Rev.G / P1-01）"))
    if totals["skipped"]:
        warnings.append(_warn(
            "TIME_BUDGET_EXCEEDED", "WARNING", "",
            f"采集超出总时长预算 {TOTAL_BUDGET_SECONDS}s，"
            f"{totals['skipped']} 个库未采集（已标 SKIPPED，不计入总数）；"
            f"请用「库名」输入框分批统计"))
    if dbs and syntax_errors >= scanned > 0:
        warnings.append(_warn(
            "NOT_DISTRIBUTED_ENDPOINT", "ERROR", "",
            "全部已执行的业务库均因语法错误(1064)失败：该连接可能指向后端 TXSQL "
            "而非 Proxy 端口，或该实例实际并非分布式实例"))
    return items, warnings, shape, totals



# ══════════════════════════════════════════════════════════════════
# 对外
# ══════════════════════════════════════════════════════════════════
def analyze(pool, connection_id: str = "", database: str = "",
            deadline: float = None) -> dict:
    """执行一次统计（只读，不落库）。

    Rev.J / P1-02：`deadline` 是**整个目标采集过程**的统一 monotonic 检查点，
    由 run_stats 在拿到并发槽位之后、任何目标实例查询之前建立。
    Rev.L / P1-02：它是软预算，只决定是否启动下一个 I/O，不强制中断已启动 I/O。
    Rev.I 的计时器直到 `_collect_distributed` 内部才启动，
    `SHOW DATABASES` 与全量基线查询（设计自己承认这才是耗时大头）根本不在预算内。
    """
    from backend.models import InstanceType
    from backend.services.instance_type_service import instance_type_service

    if deadline is None:
        deadline = _now() + TOTAL_BUDGET_SECONDS

    # Rev.K：resolve() 在缓存未命中时会向目标实例发探测 SQL
    # （instance_type_service._probe_and_persist），是本流程第一段真实的目标实例 I/O。
    # 它同样消耗 deadline——deadline 是绝对时刻，故无需在它之前额外检查；
    # 但它之后必须检查，否则一次慢探测会让后续阶段在预算外继续开工。
    ctx = instance_type_service.resolve(connection_id)
    is_dist = ctx.instance_type == InstanceType.DISTRIBUTED
    source = getattr(ctx.source, "value", str(ctx.source))

    warnings = []
    if _now() >= deadline:
        raise TimeoutError(
            f"采集总时长预算 {TOTAL_BUDGET_SECONDS}s 在实例类型探测阶段即已耗尽")
    # Rev.J / P1-02：库枚举也在预算内。它走共享池（read_timeout 默认 10s）。
    if _now() >= deadline:
        raise TimeoutError(
            f"采集总时长预算 {TOTAL_BUDGET_SECONDS}s 在枚举数据库前即已耗尽")
    # Rev.G / P2-01：库枚举失败不得静默按空库处理——集中式分支查不到行与库不存在
    # 在结果上无法区分，会得到"状态 OK、总数 0"的假成功。枚举失败一律抛出。
    business, truncated, allnames = list_business_databases(pool)
    known = _NameSpace(allnames)
    if database:
        # Rev.G / P2-01 + Rev.J / P1-01：指定库必须【精确】存在。
        # 大小写不同的库在 lower_case_table_names=0 下是两个不同的 schema，
        # 用小写去撞会把"不存在"判成"存在"，随后在 select_db 处才失败。
        if database not in known:
            variants = known.variants(database)
            hint = f"；实例上存在大小写不同的同名库: {', '.join(variants)}" if variants else ""
            raise ValueError(
                f"数据库不存在或当前账号不可见: {database}"
                f"（SHOW DATABASES 未返回该库）{hint}")
        dbs = [database]
    else:
        dbs = business

    if truncated and not database:
        warnings.append(_warn(
            "TOO_MANY_DATABASES", "WARNING", "",
            f"业务库数量超过 {MAX_DATABASES}，仅统计前 {MAX_DATABASES} 个；"
            f"请用「库名」输入框分批统计"))
    if not dbs:
        warnings.append(_warn(
            "NO_BUSINESS_DB", "INFO", "",
            "未发现业务库（账号可见范围可能过窄，或实例确实为空）"))
    if source == "default" or ctx.conflict:
        warnings.append(_warn(
            "INSTANCE_TYPE_UNRELIABLE", "WARNING", "",
            f"实例类型来源为 {source}"
            f"{'（声明与探测存在冲突）' if ctx.conflict else ''}，"
            f"当前按「{'分布式' if is_dist else '集中式'}」口径统计；"
            f"若口径不符，请在实例管理页锁定实例类型后重跑"))

    # Rev.J / P1-01：同名不同大小写的业务库如实报出——它们是两个库，不是一个。
    dupes = sorted({d.lower() for d in dbs if len(known.variants(d)) > 1})
    if dupes:
        warnings.append(_warn(
            "DB_NAME_CASE_VARIANTS", "WARNING", "",
            f"实例上存在 {len(dupes)} 组仅大小写不同的同名库"
            f"（{', '.join(known.variants(d)[0] for d in dupes[:5])} …）。"
            f"本模块按精确名分别统计；若 Proxy 返回的库限定名无法唯一归属，"
            f"该行会被记入 DB_NAME_AMBIGUOUS 而不是被猜给其中一个"))

    # Rev.J / P1-02：基线查询是耗时大头，必须在预算内
    if _now() >= deadline:
        raise TimeoutError(
            f"采集总时长预算 {TOTAL_BUDGET_SECONDS}s 在查询 information_schema 前即已耗尽")
    baseline = _collect_baseline(pool, dbs, known)
    if is_dist:
        items, warns, shape, totals = _collect_distributed(
            pool, dbs, baseline, known, deadline)
    else:
        items, warns, shape, totals = _collect_centralized(dbs, baseline)
    warnings.extend(warns)

    return {
        "instance_type": ctx.instance_type.value,
        "type_source": source,
        "type_conflict": bool(ctx.conflict),
        "database_count": len(items),
        "total_tables": totals["total"],
        "shard_tables": totals["shard"],
        "broadcast_tables": totals["broadcast"],
        "single_tables": totals["single"],
        "baseline_tables": totals["baseline"],
        "subpartition_tables": totals["subp"],
        "failed_databases": totals["failed"],
        "skipped_databases": totals["skipped"],
        "overlap_count": totals["overlap"],
        "items": items,
        "warnings": warnings,
        "shape": shape,
    }


# 落库表的**完整字段契约**（与 backend/schema/v13/130_table_type_stats.sql 逐字对应）。
#
# Rev.G / P1-08：迁移器只对 `ALTER TABLE ... ADD COLUMN` 做列级验收
# （backend/schema/migrator.py:45-48 的 _ADD_COLUMN_RE），纯 CREATE TABLE 语句
# 不进入 _structure_state() 的验收范围。于是存在这样一条静默失效路径：
#   元数据库里已存在同名但缺列 / 错类型 / 缺索引的历史残留表
#   → CREATE TABLE IF NOT EXISTS 直接跳过
#   → 迁移被登记成功
#   → 直到本模块 INSERT 才 1054 报错，用户白等一轮采集
# 迁移登记之后若表被人工删除或结构漂移，_structure_state() 同样返回 valid，
# 不会重放。故本模块自行做一次确定性结构验收，且放在【采集之前】。
#
# Rev.J / P1-04：Rev.I 只比 DATA_TYPE，仍留着一整排"采集完才失败"的路径：
#   · detail 被收窄成 VARCHAR(16)  → 500 库采完才在 INSERT 撞 1406；
#   · id 没有 AUTO_INCREMENT       → 第一条可能写进去，第二条撞主键；
#   · created_at 没有默认值        → 严格模式下 INSERT 直接失败；
#   · stat_id 可空 / 字符集非 utf8mb4 → 中文 detail 撞 1366。
# KL-15 当时以"COLUMN_TYPE 带显示宽度、跨发行版不一致"为由放弃长度校验——
# 那个理由只对**整型**成立（int(11) vs int），对 varchar 长度并不成立。
# 现在按字段分工取值，两边都拿到确定性：
#   整型  → DATA_TYPE（无显示宽度）
#   字符  → DATA_TYPE + CHARACTER_MAXIMUM_LENGTH + CHARACTER_SET_NAME
#   共通  → IS_NULLABLE、COLUMN_DEFAULT（归一化后比较）、EXTRA（自增）
#
# 契约元组：(DATA_TYPE, 长度或 None, 是否可空, 归一化默认值, 是否自增)
_COL = lambda t, ln, null, dflt, ai=False: (t, ln, null, dflt, ai)
_STAT_CONTRACT = {
    "id":                  _COL("int", None, False, None, True),
    "connection_id":       _COL("varchar", 64, True, ""),
    "database_filter":     _COL("varchar", 128, True, ""),
    "instance_type":       _COL("varchar", 32, True, ""),
    "type_source":         _COL("varchar", 32, True, ""),
    "database_count":      _COL("int", None, True, "0"),
    "total_tables":        _COL("int", None, True, "0"),
    "shard_tables":        _COL("int", None, True, "0"),
    "broadcast_tables":    _COL("int", None, True, "0"),
    "single_tables":       _COL("int", None, True, "0"),
    "baseline_tables":     _COL("int", None, True, "0"),
    "subpartition_tables": _COL("int", None, True, "0"),
    "failed_databases":    _COL("int", None, True, "0"),
    "skipped_databases":   _COL("int", None, True, "0"),
    "overlap_count":       _COL("int", None, True, "0"),
    "warnings_json":       _COL("mediumtext", None, True, None),
    "created_by":          _COL("varchar", 64, True, ""),
    "created_at":          _COL("datetime", None, True, "CURRENT_TIMESTAMP"),
}
_ITEM_CONTRACT = {
    "id":                  _COL("int", None, False, None, True),
    "stat_id":             _COL("int", None, False, None),
    "db_name":             _COL("varchar", 128, True, ""),
    "total_tables":        _COL("int", None, True, "0"),
    "shard_tables":        _COL("int", None, True, "0"),
    "broadcast_tables":    _COL("int", None, True, "0"),
    "single_tables":       _COL("int", None, True, "0"),
    "baseline_tables":     _COL("int", None, True, "0"),
    "subpartition_tables": _COL("int", None, True, "0"),
    "status":              _COL("varchar", 16, True, "OK"),
    "detail":              _COL("varchar", 512, True, ""),
    "created_at":          _COL("datetime", None, True, "CURRENT_TIMESTAMP"),
}
# INSERT 用的列清单（顺序即 SQL 里的书写顺序，单测钉住二者一致）
_STAT_COLUMNS = tuple(_STAT_CONTRACT)
_ITEM_COLUMNS = tuple(_ITEM_CONTRACT)

# 期望索引：名字 → (列序元组, 是否唯一)。PRIMARY 天然唯一。
_STAT_INDEXES = {"PRIMARY": (("id",), True),
                 "idx_tts_conn": (("connection_id",), False),
                 "idx_tts_created": (("created_at",), False)}
_ITEM_INDEXES = {"PRIMARY": (("id",), True),
                 "idx_ttsi": (("stat_id",), False)}

_SCHEMA_SPEC = (
    ("table_type_stat", _STAT_CONTRACT, _STAT_INDEXES),
    ("table_type_stat_item", _ITEM_CONTRACT, _ITEM_INDEXES),
)

# 字符列必须是 utf8mb4：latin1/utf8mb3 会让中文 detail 在 INSERT 处撞 1366，
# 又是一条"采集完才失败"的路径。
_EXPECTED_CHARSET = "utf8mb4"


def _row_get(row, *keys):
    """兼容字典游标的大小写差异（MySQL 返回大写列名，部分驱动返回小写）。"""
    d = dict(row)
    for k in keys:
        for cand in (k, k.upper(), k.lower()):
            if cand in d:
                return d[cand]
    return None


def _norm_default(value):
    """把 COLUMN_DEFAULT 归一化成可比较文本。

    MariaDB 给字符串默认值加引号返回（'' → "''"），MySQL 8 返回原值；
    CURRENT_TIMESTAMP 的大小写与括号形态也不定。剥一层成对引号 + 关键字归一。
    注意：**不做 0/1 → FALSE/TRUE 的布尔归一**（migrator._normalize_default 做了那个，
    那是为布尔列服务的），本模块的计数列默认值就是字面量 "0"，混淆会掩盖真实漂移。
    """
    if value is None:
        return None
    v = str(value).strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        v = v[1:-1]
    vu = v.upper().rstrip("()")
    if vu in ("CURRENT_TIMESTAMP", "NOW"):
        return "CURRENT_TIMESTAMP"
    if vu == "NULL":
        # MariaDB 对"可空且无默认值"的列返回字面量 'NULL'，MySQL 8 返回 SQL NULL。
        # 两系归一为 Python None（MySQL 侧为无操作）。本模块没有任何一列
        # 的默认值是字符串 'NULL'，故不存在误归一。
        return None
    return v


class SchemaNotReadyError(RuntimeError):
    """落库表结构验收失败（Rev.G / P1-08，Rev.J / P1-04 扩展为完整契约）。

    由 API 映射为 500 + 可执行提示。
    """


def _check_column(table, col, spec, info, problems):
    """逐列比对完整契约。info 为 information_schema.COLUMNS 的一行。"""
    exp_type, exp_len, exp_null, exp_default, exp_ai = spec
    dtype = str(_row_get(info, "DATA_TYPE") or "").lower()
    if dtype != exp_type:
        problems.append(f"{col} 类型不符（期望 {exp_type}，实际 {dtype}）")
        return                       # 类型都不对，长度/默认值没有比的意义
    if exp_len is not None:
        actual_len = _row_get(info, "CHARACTER_MAXIMUM_LENGTH")
        if actual_len is None or int(actual_len) != exp_len:
            problems.append(
                f"{col} 长度不符（期望 {exp_type}({exp_len})，实际 {actual_len}）")
    if exp_type in ("varchar", "char", "text", "mediumtext", "longtext"):
        cs = str(_row_get(info, "CHARACTER_SET_NAME") or "").lower()
        if cs and cs != _EXPECTED_CHARSET:
            problems.append(
                f"{col} 字符集不符（期望 {_EXPECTED_CHARSET}，实际 {cs}）")
    nullable = str(_row_get(info, "IS_NULLABLE") or "").upper() == "YES"
    if nullable != exp_null:
        problems.append(
            f"{col} 可空性不符（期望 {'NULL' if exp_null else 'NOT NULL'}，"
            f"实际 {'NULL' if nullable else 'NOT NULL'}）")
    actual_default = _norm_default(_row_get(info, "COLUMN_DEFAULT"))
    if actual_default != exp_default:
        problems.append(
            f"{col} 默认值不符（期望 {exp_default!r}，实际 {actual_default!r}）")
    extra = str(_row_get(info, "EXTRA") or "").lower()
    if exp_ai and "auto_increment" not in extra:
        problems.append(
            f"{col} 缺少 AUTO_INCREMENT——第一条可能写入成功，第二条即撞主键")


def _ensure_schema() -> None:
    """落库表结构验收：表 + 全部列的完整契约 + 索引全列序，任一不符即失败关闭。

    放在 run_stats 入口而不是进程启动期，理由见设计 ADR-20：
    表类型统计是深度诊断下的只读诊断子模块，它的留档表有问题不应当让
    整个审核平台起不来（既有 index_audit / cluster_inspection 等同级表在
    _create_all_tables 中同样没有启动期结构验收）。放在采集之前则同时满足
    "确定性验收"与"不让用户白跑一轮 180 秒采集"。
    """
    conn = _get_connection()
    try:
        for table, contract, indexes in _SCHEMA_SPEC:
            rows = conn.execute(
                "SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, "
                "CHARACTER_SET_NAME, IS_NULLABLE, COLUMN_DEFAULT, EXTRA "
                "FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = ?",
                (table,)).fetchall()
            actual = {str(_row_get(r, "COLUMN_NAME") or "").lower(): r for r in rows}
            if not actual:
                raise SchemaNotReadyError(
                    f"元数据库缺少表 {table}：迁移 v13/130_table_type_stats.sql 未生效"
                    f"（可能是升级包未带该文件，或迁移已登记后表被人工删除——"
                    f"迁移器不会重放纯 CREATE TABLE 语句）。"
                    f"处置：确认该 .sql 已随版本部署，并手工执行其中的建表语句")
            problems = []
            missing = [c for c in contract if c.lower() not in actual]
            if missing:
                raise SchemaNotReadyError(
                    f"元数据库表 {table} 缺少列: {', '.join(missing)}。"
                    f"该表很可能是同名历史残留——CREATE TABLE IF NOT EXISTS 会静默跳过，"
                    f"迁移仍登记成功。处置：核实该表无业务数据后删表重启，"
                    f"或按 130_table_type_stats.sql 补齐列")
            for col, spec in contract.items():
                _check_column(table, col, spec, actual[col.lower()], problems)
            if problems:
                raise SchemaNotReadyError(
                    f"元数据库表 {table} 结构与迁移 DDL 不符: {'; '.join(problems)}。"
                    f"处置：按 130_table_type_stats.sql 的定义 ALTER 修正，"
                    f"或核实无数据后删表重启")
            irows = conn.execute(
                "SELECT INDEX_NAME, COLUMN_NAME, SEQ_IN_INDEX, NON_UNIQUE "
                "FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = ? "
                "ORDER BY INDEX_NAME, SEQ_IN_INDEX",
                (table,)).fetchall()
            have = {}
            for r in irows:
                iname = str(_row_get(r, "INDEX_NAME") or "")
                cols, uniq = have.setdefault(iname, ([], None))
                cols.append(str(_row_get(r, "COLUMN_NAME") or "").lower())
                if uniq is None:
                    have[iname] = (cols, str(_row_get(r, "NON_UNIQUE")) in ("0", "False"))
            lost = []
            for name, (exp_cols, exp_uniq) in indexes.items():
                got = have.get(name)
                if got is None:
                    lost.append(f"{name} 缺失")
                    continue
                got_cols, got_uniq = got
                if tuple(got_cols) != tuple(c.lower() for c in exp_cols):
                    lost.append(
                        f"{name} 列序不符（期望 {exp_cols}，实际 {tuple(got_cols)}）")
                elif got_uniq != exp_uniq:
                    lost.append(
                        f"{name} 唯一性不符（期望 {'UNIQUE' if exp_uniq else '非唯一'}）")
            if lost:
                raise SchemaNotReadyError(
                    f"元数据库表 {table} 索引不符: {', '.join(lost)}。"
                    f"处置：按 130_table_type_stats.sql 补建/修正索引")
    finally:
        conn.close()


def run_stats(pool, connection_id: str = "", database: str = "",
              operator: str = "") -> dict:
    """执行一次统计并落库。落库失败不降级——直接抛出（REQ-6 要求留档）。"""
    database = (database or "").strip()
    if database and database.lower() in _SYS_DB:
        raise ValueError(f"不允许统计系统库: {database}")
    # Rev.G / P2-03：connection_id 必须显式非空——空串下 registry.get("") 取的是
    # adhoc/默认保存连接，而 instance_type_service.resolve("") 走的是全局默认类型，
    # 两者可能指向不同实例，真分布式实例会被当成集中式，分片/广播全报 0。
    if not (connection_id or "").strip():
        raise ValueError("必须指定 connection_id（本模块不接受默认连接："
                         "连接解析与实例类型解析在空 ID 下可能指向不同实例）")
    # Rev.G / P1-08：先验收落库表结构，避免采集完才在 INSERT 处失败
    _ensure_schema()

    # Rev.G / P1-02：进入既有扫描并发槽位。本模块会额外建一条
    # Proxy 连接，且 Rev.L 明确单次采集没有可证明的墙钟硬上界；
    # 不限流会挤占 SQL 审核 / 慢查询扫描 / 巡检的工作线程与目标库连接。
    # 复用 registry.scan_slot 而不是自建信号量，才能与既有扫描【共享】同一份配额
    # （scan_service.py:72 是同样的用法），否则两套限流各算各的，全局上限失去意义。
    # 超限抛 ScanBusyError，由 API 映射为 429。槽位在 with 退出时必然释放（含异常）。
    with registry.scan_slot(connection_id):
        # Rev.J / P1-02：deadline 在【拿到槽位之后、任何目标实例查询之前】建立。
        # 放在槽位之内，是因为等槽位的时间不该算进采集预算；
        # 放在查询之前，是因为 SHOW DATABASES 与基线查询也必须受同一预算约束。
        deadline = _now() + TOTAL_BUDGET_SECONDS
        res = analyze(pool, connection_id=connection_id, database=database,
                      deadline=deadline)
    conn = _get_connection()
    # Rev.Q / UAT2-O-G14-02：采集完成即生成同源 captured_at——同一值既显式写入
    # 历史行 created_at，也随响应带给前端"结果范围"展示；禁止前端另取本机时间冒充。
    captured_at = datetime.now().replace(microsecond=0)
    try:
        cur = conn.execute(
            "INSERT INTO table_type_stat (connection_id, database_filter, "
            "instance_type, type_source, database_count, total_tables, "
            "shard_tables, broadcast_tables, single_tables, baseline_tables, "
            "subpartition_tables, failed_databases, skipped_databases, "
            "overlap_count, warnings_json, created_by, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (connection_id, database, res["instance_type"], res["type_source"],
             res["database_count"], res["total_tables"], res["shard_tables"],
             res["broadcast_tables"], res["single_tables"],
             res["baseline_tables"], res["subpartition_tables"],
             res["failed_databases"], res["skipped_databases"],
             res["overlap_count"],
             json.dumps(res["warnings"], ensure_ascii=False), operator,
             captured_at))
        stat_id = cur.lastrowid
        # Rev.K / P1-02：明细【批量】落库。Rev.J 是逐行 INSERT——500 个业务库就是
        # 500 次往返，全部发生在扫描槽已释放、deadline 已不再约束的阶段，
        # 会继续占用 API 工作线程。批量后 500 库只需 5 次往返。
        rows = [(stat_id, it["db_name"], it["total_tables"], it["shard_tables"],
                 it["broadcast_tables"], it["single_tables"],
                 it["baseline_tables"], it["subpartition_tables"],
                 it["status"], it["detail"]) for it in res["items"]]
        item_sql = ("INSERT INTO table_type_stat_item (stat_id, db_name, total_tables, "
                    "shard_tables, broadcast_tables, single_tables, baseline_tables, "
                    "subpartition_tables, status, detail) VALUES (?,?,?,?,?,?,?,?,?,?)")
        for i in range(0, len(rows), ITEM_INSERT_BATCH):
            conn.cursor().executemany(item_sql, rows[i:i + ITEM_INSERT_BATCH])
        conn.commit()
    finally:
        conn.close()
    res["stat_id"] = stat_id
    # Rev.Q / UAT2-O-G14-02：响应与历史同源——前端"结果范围"展示的采集时间
    # 必须与 stat_id 对应历史行的 created_at 精确到秒一致。
    res["created_at"] = captured_at.isoformat(sep=" ")
    return res


def list_history(connection_id: str = "", limit: int = 20) -> list:
    limit = max(1, min(int(limit or 20), 200))
    conn = _get_connection()
    try:
        if connection_id:
            rows = conn.execute(
                "SELECT * FROM table_type_stat WHERE connection_id=? "
                "ORDER BY id DESC LIMIT ?", (connection_id, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM table_type_stat ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_detail(stat_id: int) -> dict:
    conn = _get_connection()
    try:
        items = [dict(r) for r in conn.execute(
            "SELECT * FROM table_type_stat_item WHERE stat_id=? ORDER BY id",
            (stat_id,)).fetchall()]
        head = conn.execute(
            "SELECT warnings_json FROM table_type_stat WHERE id=?",
            (stat_id,)).fetchone()
    finally:
        conn.close()
    warnings = []
    if head:
        try:
            warnings = json.loads(dict(head).get("warnings_json") or "[]")
        except Exception:                                    # noqa: BLE001
            warnings = []
    return {"items": items, "warnings": warnings}
