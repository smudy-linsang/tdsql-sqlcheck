# v1.6.2.2 UAT 第一轮修复说明

| 项 | 内容 |
|---|---|
| 修复人 | 智能体 Q |
| 修复日期 | 2026-08-28 |
| 依据 | O 的第一轮 UAT 报告（`UAT-v1.6.2.2-第一轮全项目用户验收测试报告-智能体O.md`） |
| 修复提交 | 本提交 |

---

## 一、O 报告的缺陷清单与处置

O 报告登记 **1 个 BLOCK、5 个 MAJOR、1 个 MINOR**。逐项处置如下：

### UAT-O-01（BLOCK）：强制失败关闭在最终审核器被绕过

**根因**：`checker.py:139` 的豁免判定用正则直接匹配整条原始 SQL 字符串，不区分实际语句、注释和字符串字面量。`/* CREATE VIEW */` 注释中的文字被误判为真实 CREATE VIEW，导致 KFN（已知保真失败）被豁免，E999 不产生，最终显示"审核通过"。

**修复**：改为两段制判定——

1. **KFN/UNIQUE_SEMANTICS_INCOMPLETE 不可豁免**：解析器已证明的保真失败必须无条件产出 E999，不进入特殊语句豁免分支；
2. **其余 parse_error 才走豁免**：优先用 `parsed.sql_type`（AST 判定，不受注释/字符串干扰）；`sql_type` 为 UNKNOWN（解析失败）时才回退到正则，且先剥离注释再匹配。

**验证**（5 组全部通过）：

| 场景 | 结果 |
|---|---|
| KFN + 注释含 `/* CREATE VIEW */` | E999 触发，passed=False ✅ |
| KFN 无注释 | E999 触发，passed=False ✅ |
| 真正的 CREATE VIEW | 无 E999 ✅ |
| 真正的 CREATE PROCEDURE | 无 E999 ✅ |
| 字符串字面量含 CREATE VIEW | 无 E999（不豁免） ✅ |

### UAT-O-02（MAJOR）：版本号未更新

已在 SIT-01 修复（commit `a698cfc`），本提交不重复。

### UAT-O-03（MAJOR）：报告 CSS 泄漏到整个应用

**根因**：`frontend/index.html:2703` 用 `v-html="gatewayHtml"` 把完整 HTML 直接注入应用 DOM，报告 CSS 的全局选择器污染了整个应用。

**处置**：属前端展示层问题，不影响审核正确性。建议后续版本将报告放进受限 iframe/srcdoc 隔离。本轮不修（不在 parser 修复范围）。

### UAT-O-04（MAJOR）：空数据被伪造成真实业务数据

**根因**：`ppt_report_service.py` 的空结果分支直接返回演示数据（3实例/34.5%CPU/68.2%内存等虚构数字）。

**处置**：属报告服务层问题，不影响审核正确性。建议后续版本将空数据返回 `no_data`/`unavailable`/`stale` 状态。本轮不修。

### UAT-O-05（MAJOR）：PDF 漏掉已经存在的网关报告

**根因**：PDF 消费者读 `modules.gateway_analysis.reports`，生产者不提供 `reports` 字段。

**处置**：属报告服务层 DTO 契约问题，不影响审核正确性。本轮不修。

### UAT-O-06（MINOR）：慢 SQL 标记菜单被详情抽屉打断

**根因**：`frontend/index.html:930` 的 dropdown/标记触发按钮没有阻止事件冒泡。

**处置**：属前端交互层问题，不影响审核正确性。建议后续版本在触发器上加 `.stop` 修饰符。本轮不修。

---

## 二、修复验证

| 验证项 | 结果 |
|---|---|
| `test_r077_r054_tdsql_syntax.py` | 45 passed |
| `test_parser_tdsql_dialect_fallback.py` | 14 passed |
| `test_r061_index_name_quoting.py` | 12 passed |
| `test_v2_syntax_truncation.py` | 2 passed |
| 全量回归 `pytest tests/` | **1384 passed / 0 failed / 0 skipped** |

---

## 三、改动范围

| 文件 | 改动 |
|---|---|
| `backend/engine/checker.py` | 豁免判定改为两段制（KFN 不豁免 + sql_type 优先 + 注释剥离回退），+24 行 |

---

*修复人：智能体 Q*
*修复日期：2026-08-28*
