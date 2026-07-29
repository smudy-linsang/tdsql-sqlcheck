"""SQL 层实例类型判据表（V1.5.1）

判据全部来自 2026-07-29 的真实环境实测（TDSQL 8.0.33-v24-txsql-22.4.1），
原始数据见 docs/REPORT-v1.5.1-Proxy层实例类型判据实测结果-G.md，
采纳裁定见 docs/DESIGN-v1.5.1-实例类型判定重构.md §8。

【铁律】判据只做阳性判定，禁止把"未命中"当作反向结论。
  V1.5 的教训是"恒判分布式"（误报，可见、可纠正）；
  比它更危险的是"易判集中式"（漏报，不可见、直接放行风险）。
  因此除 PR001 命中【集中式正面签名】外，任何判据未命中一律返回 None。

【新增判据的强制门槛】——三条全部满足才可入表，见设计文档 §8.4：
  1) 两类实例上实测输出确有差异，evidence 必须写明实测出处；
  2) 差异方向明确：命中即为分布式的阳性证据；
  3) 未命中时不得判集中式，必须返回 None 下沉至下一源。
外加：每条判据必须配套一条反向鉴别用例（两类实例各跑一次、断言结论不同）。
"""
import re
from dataclasses import dataclass
from typing import Callable, Optional

# 结果行取字段名的兼容顺序（沿用 discover_sets() 的多版本兼容口径）
_NAME_KEYS = ("status_name", "Variable_name", "Config_name", "name", "Key")
_VALUE_KEYS = ("Value", "value")


def _row_name(row) -> str:
    if isinstance(row, dict):
        for k in _NAME_KEYS:
            if k in row and row[k] is not None:
                return str(row[k])
    return ""


def _row_value(row) -> str:
    if isinstance(row, dict):
        for k in _VALUE_KEYS:
            if k in row and row[k] is not None:
                return str(row[k])
    return ""


@dataclass(frozen=True)
class ProbeRule:
    """一条 SQL 层判据。

    Attributes:
        rule_id:  判据标识
        sql:      在目标实例上执行的语句
        decide:   (rows) -> "distributed" | "centralized" | None
                  返回 None 表示【本判据无结论】，绝不等同于集中式
        evidence: 实测依据（日期 + 数据出处），入表必填
        enabled:  是否参与自动探测
    """
    rule_id: str
    sql: str
    decide: Callable[[list], Optional[str]]
    evidence: str
    enabled: bool = True


# ── PR001：/*proxy*/show status 拓扑签名（主判据）────────────────────────
def _decide_proxy_status(rows: list) -> Optional[str]:
    """依据 Proxy 返回的拓扑签名判定。判定表见设计文档 §8.5。

    实测（2026-07-29）：
      CENT 2 行 —— set=set_1782130875_4 ；无 cluster / 无 hash_range
      DIST 8 行 —— cluster=group_1782132247_10 ；
                   set_1782132369_1:hash_range=0---7 ；
                   set_1782132389_3:hash_range=8---15 ；
                   set="set_1782132369_1,set_1782132389_3 "  ← 注意末尾空格
    """
    if not rows:
        return None

    set_values = []
    for r in rows:
        name = _row_name(r)
        # 签名 1：cluster 行 —— 分布式的结构性阳性证据
        # 签名 2：键名含冒号 —— DIST 用 <set_id>:<属性> 命名空间式键名
        #        （实测形态 :ip / :alias / :hash_range）；
        #        CENT 用裸键名 <set_id>，不含冒号。
        #        泛化为"含冒号"而非仅 :hash_range —— 更灵敏，且误判方向安全。
        if name == "cluster" or ":" in name:
            return "distributed"
        if name == "set":
            set_values.append(_row_value(r))

    if not set_values:
        return None                      # 形态不符，不猜

    # 实测中 value 末尾带空格，且可能出现尾随逗号，必须先 strip 再过滤空串
    sets = [s.strip() for s in set_values[0].split(",")]
    sets = [s for s in sets if s]

    if len(sets) >= 2:
        return "distributed"             # 签名 3：多 SET 拓扑
    if len(sets) == 1:
        # 签名 4：单 SET 拓扑 + 无 cluster + 无 hash_range
        # 这是【集中式的正面签名】，不是"没看到分布式特征"，故允许下结论。
        # 已知边界：单分片分布式实例若不输出 cluster 行会落到这里被判集中式。
        # 该形态未实测。缓解措施是 §4.3 的保守合并——只有人工声明也为集中式
        # 时该结论才会真正生效。
        return "centralized"
    return None


# ── PR002：EXPLAIN 路由信息注入（辅助，仅阳性）──────────────────────────
def _decide_explain_info(rows: list) -> Optional[str]:
    """Proxy 为分布式实例的 EXPLAIN 注入 info 列（含路由到的 SET）。

    实测（2026-07-29）：
      CENT: 标准 12 列，无 info
      DIST: info = "set_1782132369_1,EXPLAIN SELECT 1"

    【只判阳性】无 info 什么也证明不了 —— EXPLAIN SELECT 1 不涉及任何表，
    Proxy 是否注入路由信息带有偶然性，不同版本/配置未必一致。
    """
    if rows and isinstance(rows[0], dict) and "info" in rows[0]:
        return "distributed"
    return None


# ── PR003：表 DDL 分片标记（可选兜底，仅阳性，默认不启用）───────────────
_RE_SHARDKEY = re.compile(r"\bshardkey\s*=", re.IGNORECASE)


def _decide_table_ddl(rows: list) -> Optional[str]:
    """分布式实例的表 DDL 尾部带 shardkey= 或广播表标记。

    实测（2026-07-29）：
      CENT: ... COLLATE=utf8mb4_bin COMMENT='...'      （无 shardkey）
      DIST: ... COLLATE=utf8mb4_bin shardkey=id

    默认不启用：需先选定一张表，比 PR001/PR002 昂贵，且空库无表可查。
    仅由诊断接口在 PR001/PR002 均无结论时按需调用。
    """
    for r in rows or []:
        text = " ".join(str(v) for v in r.values()) if isinstance(r, dict) else str(r)
        if _RE_SHARDKEY.search(text) or "broadcast" in text.lower():
            return "distributed"
    return None


# ── PR004：xa 系统库存在性（辅助，仅阳性）────────────────────────────
def _decide_xa_database(rows: list) -> Optional[str]:
    """分布式实例存在 xa 系统库（跨 SET 全局自增 + 跨分片 2PC 协调）。

    实测（2026-07-29）：
      CENT: 7 库，无 xa
      DIST: 8 库，含 xa（内含 auto_inc_table / gtid_log_t）
      两侧 6 个系统库完全一致，差异有且仅有 xa。

    【只判阳性，且这条尤其重要】
    SHOW DATABASES 只返回当前账号【有权限看到】的库。权限更窄的账号在
    真正的分布式实例上也可能看不到 xa。若反推"无 xa → 集中式"，等于让
    账号权限决定审核口径 —— 换个账号 27 条规则就静默关了。
    """
    for r in rows or []:
        if isinstance(r, dict):
            vals = [str(v).strip().lower() for v in r.values()]
        else:
            vals = [str(r).strip().lower()]
        if "xa" in vals:
            return "distributed"
    return None


ACTIVE_PROBE_RULES: list[ProbeRule] = [
    ProbeRule(
        rule_id="PR001",
        sql="/*proxy*/show status",
        decide=_decide_proxy_status,
        evidence=("2026-07-29 实测，REPORT-v1.5.1 §1 判据1；"
                  "与赤兔管理台 group/set 标识及 hash 区间逐字核验一致"),
    ),
    ProbeRule(
        rule_id="PR002",
        sql="EXPLAIN SELECT 1",
        decide=_decide_explain_info,
        evidence="2026-07-29 实测，REPORT-v1.5.1 §1 判据2（仅阳性方向）",
    ),
    ProbeRule(
        rule_id="PR004",
        sql="SHOW DATABASES",
        decide=_decide_xa_database,
        evidence=("2026-07-29 实测 T06，raw_probe_out_{CENT,DIST}.txt；"
                  "xa 库归属经 SHOW TABLES FROM xa 专项确认"
                  "（auto_inc_table / gtid_log_t，均为分布式协调设施）"),
    ),
    ProbeRule(
        rule_id="PR003",
        sql="",                     # 由调用方拼入具体表名，见 §7.1
        decide=_decide_table_ddl,
        evidence="2026-07-29 实测，REPORT-v1.5.1 §1 判据3（仅阳性方向）",
        enabled=False,              # 默认不参与自动探测
    ),
]
