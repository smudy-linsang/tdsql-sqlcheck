# GATE-3 决策材料：质量门禁双向变化

| 项 | 内容 |
|---|---|
| 门禁 | GATE-3（`GATE-v1.6.3.2-生产发布三项书面门禁发起单`） |
| 材料承办 | 智能体 A 牵头、智能体 G 配合（按第三轮 UAT §7 分工） |
| **裁决与签字** | **林桑 / DBA 管理员 + 每条受影响流水线的真实负责人** —— 智能体不得代签 |
| 材料日期 | 2026-09-04 |
| 事实核验基线 | `main` / `d61ad7b`（审核引擎与规则实现自 SIT2 通过后零变更，我已复核） |
| **当前状态** | **材料未齐，暂不具备签字条件**——收紧侧缺"存量 SQL 预命中统计"，见 §4 |

---

## 1. 变了什么：一张双向表

`gate_service` 的判定口径未变：`strict` = ERROR 0 且 WARNING 0；`normal` = ERROR 0、WARNING 不限；
**`INFO` 两种策略都不计数**。本版的变化全部来自规则的级别与覆盖面：

| 方向 | 变化 | 机制 |
|---|---|---|
| **放宽** | R011 由 `WARNING` 降为 `INFO` | INFO 不计入门禁 → strict 下"含 TEXT 列即卡"变放行 |
| **放宽** | R011 覆盖由 9 种类型收窄为仅 `TEXT` | `TINYTEXT` / `TINYBLOB` / `JSON` **一条规则都不再命中** |
| **放宽** | R030 / R032 改为仅分布式 | 集中式上视图/存储过程/触发器/临时表不再拦（详见 GATE-2） |
| **收紧** | 新增 R120（受限 LOB，`ERROR`，全模式） | `BLOB`/`MEDIUMTEXT`/`LONGBLOB`/`MEDIUMBLOB`/`LONGTEXT` 由原来的 WARNING 变 **ERROR** → **normal 也卡** |
| **收紧** | 新增 R121（二级分区 MAXVALUE，`ERROR`，仅分布式） | 分布式实例上原本无人管的写法开始 **ERROR** |

## 2. 我在当前代码上实跑的矩阵（隔离测试，只计目标规则）

```text
场景                            方向   实例          strict  normal  命中
含 TEXT 列的建表                  放宽   distributed  通过    通过    [('R011','INFO')]
含 TINYTEXT/TINYBLOB/JSON 的建表  放宽   distributed  通过    通过    []
集中式 视图/过程/触发器            放宽   centralized  通过    通过    []
集中式 临时表                     放宽   centralized  通过    通过    []
含受限 LOB 的建表                 收紧   distributed  失败    失败    [('R120','ERROR')]
分布式二级分区 MAXVALUE           收紧   distributed  失败    失败    [('R121','ERROR')]
```

**读法**：前四行是"以前会卡、现在放行"；后两行是"以前不卡（或只是 WARNING）、现在直接卡"。
后两行对 `normal` 流水线尤其关键——`normal` 原本对 WARNING 不设限，
LOB 字段以前只是提醒，**现在会直接把流水线打红**。

## 3. 活动规则集的影响口径

| 情形 | 结果 |
|---|---|
| 规则集**未**对 R011 做覆盖 | 按新默认 `INFO` 生效（放宽） |
| 规则集**已**把 R011 覆盖为 `WARNING`/`ERROR` | **覆盖优先，仍按覆盖值生效**——这类规则集不受放宽影响 |
| R120 / R121 是新规则，存量规则集无覆盖 | 按默认 `enabled=true` + `ERROR` 生效（收紧） |
| 规则集把 R030/R032 设为启用 | **也绕不过适用域**：集中式仍跳过（适用域过滤先于规则集过滤） |

→ **需要 DBA/管理员做的第一件事**：把所有活动规则集过一遍，确认
①有多少个规则集显式覆盖过 R011（这些不受放宽影响）；
②是否接受 R120/R121 在全部规则集上默认开启，还是要对某些规则集先关掉、留整改窗口。

## 4. 缺口：收紧侧还没有影响面数字（**这是暂不能签的原因**）

放宽侧不需要数字——放宽不会打断任何流水线。
**收紧侧必须有数字才谈得上"接受"**：没有预命中统计，流水线负责人是在盲签
"上线当天我的流水线可能开始红"。

需要 G 在内网跑出来的，就三个数：

| 指标 | 含义 | 谁受影响 |
|---|---|---|
| R120 预命中语句数 / 涉及流水线 | 存量 SQL 里有多少条会因受限 LOB 变 ERROR | normal + strict 都受影响 |
| R121 预命中语句数 / 涉及流水线 | 分布式实例上多少条二级分区带 MAXVALUE | normal + strict 都受影响 |
| strict 结论翻转数 | 原本通过、改后失败的语句条数 | strict 流水线 |

我把统计脚本写好了，**离线只读、不连任何数据库**，G 直接拿去跑即可：

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GATE-3 存量 SQL 预命中统计（离线、只读，不连任何数据库）。

用途：在内网对**存量 SQL 语料**统计 v1.6.3.2 双向变化的实际影响面，
供 DBA/管理员与流水线负责人做书面确认。

用法：
    python3 prehit_stats.py <语料目录或文件> [--instance distributed|centralized]

语料来源建议（任选其一或合并）：
  · 各流水线仓库里进入过质量门禁的 .sql / MyBatis .xml
  · 平台 audit_history / file_reports 落库的历史审核 SQL
  · 在线元数据审核导出的 SHOW CREATE TABLE 汇总

输出：三张表——收紧影响（会新增拦截的语句）、放宽影响（原拦截现放行）、门禁结论翻转统计。
"""
import sys, os, json, argparse
sys.path.insert(0, os.environ.get("SQLCHECK_ROOT", "/home/user/tdsql-sqlcheck"))
from backend.engine.checker import RuleChecker

TIGHTEN = {"R120", "R121"}                 # 新增 ERROR，收紧
LOOSEN_R011 = "R011"                       # WARNING→INFO，放宽
LOOSEN_SCOPE = {"R030", "R032"}            # 改域，集中式放宽
# v1.6.3.2 之前 R011 覆盖的 9 种类型（收窄后 TINYTEXT/TINYBLOB/JSON 失去覆盖）
OLD_R011_LOST = {"TINYTEXT", "TINYBLOB", "JSON"}


def sev(v):
    return str(getattr(v.severity, "value", v.severity))


def gate(err, warn, policy):
    lim = {"strict": (0, 0), "normal": (0, -1)}[policy]
    if lim[0] >= 0 and err > lim[0]:
        return "失败"
    if lim[1] >= 0 and warn > lim[1]:
        return "失败"
    return "通过"


def collect(path):
    out = []
    if os.path.isfile(path):
        out.append(path)
        return out
    for root, _d, files in os.walk(path):
        for f in files:
            if f.lower().endswith((".sql", ".xml")):
                out.append(os.path.join(root, f))
    return sorted(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus")
    ap.add_argument("--instance", default="distributed",
                    choices=["distributed", "centralized"])
    a = ap.parse_args()
    ck = RuleChecker()
    files = collect(a.corpus)
    stat = {"files": 0, "stmts": 0, "R120": 0, "R121": 0,
            "R011_info": 0, "r011_lost_type": 0,
            "flip_strict_pass_to_fail": 0, "flip_strict_fail_to_pass": 0}
    detail = []
    for fp in files:
        try:
            content = open(fp, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        stat["files"] += 1
        for r in ck.audit_file(content, file_path=fp, instance_type=a.instance):
            stat["stmts"] += 1
            vs = r.violations
            ids = {v.rule_id for v in vs}
            err = sum(1 for v in vs if sev(v) == "ERROR")
            warn = sum(1 for v in vs if sev(v) == "WARNING")
            now = gate(err, warn, "strict")
            # 还原到 v1.6.3.2 之前：剔除 R120/R121；R011 记回 WARNING
            old_err = err - sum(1 for v in vs if v.rule_id in TIGHTEN and sev(v) == "ERROR")
            old_warn = warn + sum(1 for v in vs if v.rule_id == LOOSEN_R011)
            before = gate(old_err, old_warn, "strict")
            if before == "通过" and now == "失败":
                stat["flip_strict_pass_to_fail"] += 1
                detail.append(("收紧翻转", fp, sorted(ids & TIGHTEN), r.sql[:80]))
            if before == "失败" and now == "通过":
                stat["flip_strict_fail_to_pass"] += 1
                detail.append(("放宽翻转", fp, sorted(ids), r.sql[:80]))
            for k in ("R120", "R121"):
                if k in ids:
                    stat[k] += 1
            if LOOSEN_R011 in ids:
                stat["R011_info"] += 1
            up = (r.sql or "").upper()
            if any(t in up for t in OLD_R011_LOST):
                stat["r011_lost_type"] += 1
    print(json.dumps(stat, ensure_ascii=False, indent=2))
    print("\n翻转明细（前 30 条）：")
    for row in detail[:30]:
        print("  ", row)


if __name__ == "__main__":
    main()
```

我已用仓库自带的规则物料（`tests/rule_audit_materials/sql_audit`，5 文件 / 102 语句）
把脚本跑通，验证可用与输出格式：

```json
{ "files": 5, "stmts": 102, "R120": 1, "R121": 1, "R011_info": 2,
  "r011_lost_type": 0, "flip_strict_pass_to_fail": 0, "flip_strict_fail_to_pass": 0 }
```

（该语料本身已触发大量其他规则，strict 原本就是失败，故翻转数为 0；
真实存量语料上会出现翻转，这正是需要统计的原因。）

**语料来源建议**（任选其一或合并）：各流水线仓库里进过质量门禁的 `.sql` / MyBatis `.xml`；
平台 `audit_history` / `file_reports` 落库的历史审核 SQL；在线元数据审核导出的 `SHOW CREATE TABLE` 汇总。

## 5. 建议的执行顺序

1. **G** 在内网用 §4 脚本跑存量语料，产出三个数字与受影响流水线清单；
2. **DBA/管理员** 审阅活动规则集（§3），决定 R120/R121 是否全量默认开启；
3. **A（我）** 把 §2 矩阵 + §4 数字整理成一页发给每条受影响流水线的负责人；
4. **流水线负责人** 在各自的 strict/normal 隔离流水线上验证结果与 §2 表一致（对应 UAT-12），回填确认；
5. 约定上线窗口，人类责任方签字。

## 6. 请勾选（**§4 数字回填后**再签）

```
活动规则集审阅（DBA/管理员）：
  [ ] 已审阅，接受 R120/R121 在全部活动规则集默认开启
  [ ] 已审阅，要求对以下规则集先关闭并留整改窗口：__________________
  显式覆盖过 R011 的规则集数：______    R120 预命中：______    R121 预命中：______
  strict 结论翻转（通过→失败）语句数：______

门禁双向变化通知（流水线负责人）：
  [ ] 已接收 §2 矩阵，已在 strict/normal 隔离流水线验证结果一致
  受影响流水线清单：__________________________________

上线窗口：____________________
确认人（DBA / 管理员）：____________  日期：________
确认人（流水线负责人）：____________  日期：________
```

---

**智能体不代签**：本页整理的是决策所需的事实、口径与缺口。
"接受/不接受、姓名、日期"必须由对应人类责任方填写。
**并且在 §4 的三个数字回填之前，我不建议任何人在收紧侧签字**——那是盲签。
