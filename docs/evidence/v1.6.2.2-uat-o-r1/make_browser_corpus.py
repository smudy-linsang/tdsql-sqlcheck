"""Produce browser-upload SQL from the independent audited corpus."""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
data = json.loads((HERE / "rule_probe_current.json").read_text(encoding="utf-8"))
rows = [r for r in data["rows"] if r["id"].startswith("corpus:") and r["instance_type"] == "distributed"]
(HERE / "uat_core_rule_corpus.sql").write_text("\n\n".join(r["sql"].rstrip("; \r\n") + ";" for r in rows), encoding="utf-8")
guard = next(r for r in data["rows"] if r["id"] == "kfn_comment:0:block")
(HERE / "uat_kfn_comment.sql").write_text(guard["sql"], encoding="utf-8")
lines = ["# 119 条核心规则逐项覆盖账本（智能体O）", "", "引擎实际命中与浏览器全流程不是同一证据层；有命中不代表所有边界通过。", "", "本轮注册119条，实际命中114条：107条有非注入元数据输入证据，7条仅通过显式合成元数据验证分支。其余5条未触发，不能算作通过。详见主报告§3.4和§7.1。", "", "| 规则 | 分类 | 本轮引擎命中 | 首个样例 |", "|---|---|---|---|"]
for rule in data["rules"]:
    rid = rule["rule_id"]
    hits = data["coverage"].get(rid, [])
    state = "未触发/已知可达性缺口"
    if hits:
        state = "合成元数据注入下触发" if all(h.startswith("metadata:") for h in hits) else "非注入元数据输入中触发"
    lines.append(f"| {rid} | {rule['category']} | {state} | {hits[0] if hits else '见报告的既有缺陷清单'} |")
(HERE / "rule_coverage_119.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
print("BROWSER_CORPUS_CASES", len(rows))
