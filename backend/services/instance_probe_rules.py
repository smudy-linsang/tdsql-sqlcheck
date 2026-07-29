"""SQL 层实例类型判据表（V1.5.1）

V1.5 的两个判据（/*proxy*/show status 非空、TDSQL_SHARDING_RULES 存在）
经真实环境实测全部证伪，详见 docs/DESIGN-v1.5.1 §2：前者对任何 MySQL
兼容端点恒为真，后者在该 TXSQL 版本上恒为假，合并净效果是常量函数。

本表判据全部来自 G 的 Proxy 层成对实测（docs/REPORT-v1.5.1，2026-07-29，
原始输出 docs/raw_probe_out_CENT.txt / raw_probe_out_DIST.txt），并经 A 按
设计文档 §8.4 三项标准评审通过后入表。

【新增判据的强制门槛】——三条全部满足才可入表，见设计文档 §8.4：
  1) 两类实例上实测输出确有差异（不是"应该有差异"）；
  2) 差异方向明确：命中即为分布式的【阳性证据】；
  3) 未命中时不得判集中式，必须返回"无结论"下沉至下一源。

【关于阴性判定的唯一例外】（A 评审决定）：
  仅 PR001 允许产出 "centralized"——它不是"未命中的兜底"，而是对
  单 SET 拓扑的阳性识别（set 行恰好 1 个 SET 且无 cluster/无含冒号键）。
  PR002/PR003/PR004 只准返回 "distributed" 或 None，绝不能返回
  "centralized"：这是整个改造里唯一能造成静默漏报的地方——
  一次网络抖动/权限差异若被判成集中式，27 条规则将被静默关闭。
  该约束由 ProbeRule.allow_negative 结构化钉死，连接器层强制执行。

新增判据必须同时补一条反向鉴别用例（对两类实例各跑一次、断言结论不同），
仅断言"某类返回某值"无法发现常量函数 —— V1.5 正是这样漏掉的。
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger("tdsql.probe_rules")

# 判定结论常量（与 backend.models.InstanceType 的 value 一致，
# 此处不 import models，保持本模块零依赖便于单测）
DISTRIBUTED = "distributed"
CENTRALIZED = "centralized"

_IDENT_RE = re.compile(r"^[A-Za-z0-9_]+$")


@dataclass(frozen=True)
class ProbeRule:
    """一条 SQL 层判据。

    Attributes:
        rule_id:  判据标识，如 "PR001"
        sql:      在目标实例上执行的语句
        decide:   (rows, execute) -> "distributed" / "centralized" / None。
                  "distributed"=阳性命中；None=本判据无结论，**不等于集中式**；
                  "centralized" 仅 allow_negative=True 的判据可产出。
                  execute 为连接器的语句执行函数，仅 PR004 需要二次查询，
                  其余判据忽略该参数。
        evidence: 判据依据（实测日期 + 数据出处），入表必填
        allow_negative: 是否允许产出 "centralized"。默认 False；
                  连接器对 allow_negative=False 的判据返回的 "centralized"
                  一律降级为 None 并告警（结构化防线，防止后续新判据
                  重蹈 G 初版代码 else: return "centralized" 的覆辙）
    """
    rule_id: str
    sql: str
    decide: Callable[[list, Optional[Callable]], Optional[str]]
    evidence: str
    allow_negative: bool = field(default=False)


# ──────────────────────────────────────────────────────────────────────────
# 行字段兼容读取：不同 TDSQL/Proxy 版本返回的键名不一
# （实测为 status_name/value；老版本文档中还有 Variable_name/Config_name）
def _row_name(row: dict) -> str:
    return str(row.get("status_name",
               row.get("Variable_name",
               row.get("Config_name",
               row.get("name", row.get("Key", ""))))) or "")


def _row_value(row: dict) -> str:
    return str(row.get("value", row.get("Value", "")) or "")


# ──────────────────────────────────────────────────────────────────────────
# PR001：/*proxy*/show status 内容差异（首选判据，唯一允许阴性）
#
# 实测（REPORT-v1.5.1 §1 判据1，2026-07-29）：
#   CENT(15002) 2 行：set=set_1782130875_4；set_1782130875_4=10.206.0.4:4002;...
#   DIST(15005) 8 行：首行 cluster=group_...；set_...:ip/:alias/:hash_range；
#                     set 行值 "set_1782132369_1,set_1782132389_3 "（注意末尾空格）
#
# 三个签名（按序判定，阳性优先）：
#   签名1 阳性：存在 status_name == 'cluster' 行            → distributed
#   签名2 阳性：存在【键名】含 ':' 的行（:ip/:alias/:hash_range）→ distributed
#              注意判的是键名，不是值——CENT 的值 "10.206.0.4:4002" 也有
#              冒号，但键名没有
#   签名3      ：set 行按 ',' 切分、逐项 strip 并过滤空串后计 SET 个数
#              （DIST 实测原文 set 行值末尾带空格，不清洗会切出空元素）
#              >=2 → distributed；==1 → centralized（单 SET 拓扑的阳性识别）
#   全部未命中（如无 set 行）→ None
def _pr001_decide(rows: list, execute: Optional[Callable] = None) -> Optional[str]:
    set_value = None
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        name = _row_name(row)
        if not name:
            continue
        if name == "cluster":
            return DISTRIBUTED                      # 签名1
        if ":" in name:
            return DISTRIBUTED                      # 签名2：键名含 ':'
        if name == "set":
            set_value = _row_value(row)
    if set_value is not None:
        sets = [s.strip() for s in set_value.split(",")]
        sets = [s for s in sets if s]               # strip + 过滤空串（A 提醒 2）
        if len(sets) >= 2:
            return DISTRIBUTED
        if len(sets) == 1:
            return CENTRALIZED                      # 签名3 阴性：恰好单 SET
    return None


# ──────────────────────────────────────────────────────────────────────────
# PR002：EXPLAIN SELECT 1 返回列结构差异（辅助，仅阳性）
#
# 实测（REPORT-v1.5.1 §1 判据2）：分布式 Proxy 会在 EXPLAIN 结果中注入
# 路由列 info（如 info="set_1782132369_1,EXPLAIN SELECT 1"）；集中式无该列。
# 无 info 列 ≠ 集中式：驱动差异/版本差异都可能导致列缺失，只准返回 None。
def _pr002_decide(rows: list, execute: Optional[Callable] = None) -> Optional[str]:
    for row in rows or []:
        if isinstance(row, dict) and "info" in row:
            return DISTRIBUTED
    return None


# ──────────────────────────────────────────────────────────────────────────
# PR003：show databases 是否存在分布式 2PC 专用系统库 xa（辅助，仅阳性）
#
# 实测（REPORT-v1.5.1 §2）：DIST 有 xa 库（auto_inc_table/gtid_log_t，
# 跨 SET 全局自增与分布式事务 GTID 跟踪）；CENT 无。
# 无 xa 库 ≠ 集中式：账号无 SHOW 权限时同样看不到，只准返回 None。
def _pr003_decide(rows: list, execute: Optional[Callable] = None) -> Optional[str]:
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        for v in row.values():
            if str(v or "").strip().lower() == "xa":
                return DISTRIBUTED
    return None


# ──────────────────────────────────────────────────────────────────────────
# PR004：业务表 DDL 是否含 shardkey（先验最强，仅阳性）
#
# 实测（REPORT-v1.5.1 §1 判据3）：DIST 的表 DDL 尾部带 "shardkey=id"；
# CENT 的表 DDL 为标准 MySQL 格式，绝无 shardkey 关键字。
# 空库无表可查 / 抽样表都无 shardkey ≠ 集中式，只准返回 None
# （分布式实例也可能恰好抽到广播表之外的空库）。
_PR004_SAMPLE_SQL = (
    "SELECT TABLE_SCHEMA AS ts, TABLE_NAME AS tn "
    "FROM information_schema.TABLES "
    "WHERE TABLE_TYPE = 'BASE TABLE' "
    "AND TABLE_SCHEMA NOT IN ('information_schema','mysql',"
    "'performance_schema','sys','xa','sysdb') "
    "ORDER BY TABLE_SCHEMA, TABLE_NAME LIMIT 5")


def _pr004_decide(rows: list, execute: Optional[Callable] = None) -> Optional[str]:
    if execute is None:
        return None
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        ts = str(row.get("ts") or row.get("TABLE_SCHEMA") or "")
        tn = str(row.get("tn") or row.get("TABLE_NAME") or "")
        # 标识符白名单：表名来自 information_schema 但仍按注入面处理
        if not _IDENT_RE.match(ts) or not _IDENT_RE.match(tn):
            continue
        try:
            ddl_rows = execute(f"SHOW CREATE TABLE `{ts}`.`{tn}`")
        except Exception as e:
            logger.debug(f"PR004 取 DDL 失败({ts}.{tn}): {e}")
            continue
        for d in ddl_rows or []:
            if isinstance(d, dict):
                text = " ".join(str(v or "") for v in d.values())
                if "shardkey" in text.lower():
                    return DISTRIBUTED
    return None


# ──────────────────────────────────────────────────────────────────────────
# 生效判据表。顺序即执行顺序；连接器按【阳性优先于阴性】合并：
# 任一判据判 distributed → distributed；否则任一 allow_negative 判据判
# centralized → centralized；全部无结论 → None（下沉至 ZK/声明）。
_EVIDENCE_BASE = ("REPORT-v1.5.1 Proxy层实例类型判据实测结果 2026-07-29，"
                  "raw_probe_out_CENT.txt / raw_probe_out_DIST.txt 成对采集，"
                  "经 A 按 DESIGN-v1.5.1 §8.4 评审入表")

ACTIVE_PROBE_RULES: list[ProbeRule] = [
    ProbeRule(
        rule_id="PR001",
        sql="/*proxy*/show status",
        decide=_pr001_decide,
        evidence=(f"{_EVIDENCE_BASE}；CENT 2 行仅单 SET，DIST 8 行含 "
                  f"cluster 行与 set_xxx:hash_range 等含冒号键"),
        allow_negative=True,
    ),
    ProbeRule(
        rule_id="PR002",
        sql="EXPLAIN SELECT 1",
        decide=_pr002_decide,
        evidence=(f"{_EVIDENCE_BASE}；DIST 结果含 Proxy 注入的 info 路由列，"
                  f"CENT 为标准 12 列无 info"),
    ),
    ProbeRule(
        rule_id="PR003",
        sql="SHOW DATABASES",
        decide=_pr003_decide,
        evidence=(f"{_EVIDENCE_BASE}；DIST 存在分布式 2PC 专用系统库 xa"
                  f"（auto_inc_table/gtid_log_t），CENT 无"),
    ),
    ProbeRule(
        rule_id="PR004",
        sql=_PR004_SAMPLE_SQL,
        decide=_pr004_decide,
        evidence=(f"{_EVIDENCE_BASE}；DIST 业务表 DDL 尾部带 shardkey=id，"
                  f"CENT 为标准 MySQL DDL 无该关键字"),
    ),
]
