# v1.6.2.2 UAT 第三轮修复说明

| 项 | 内容 |
|---|---|
| 修复人 | 智能体 Q |
| 修复日期 | 2026-08-29 |
| 依据 | `UAT-v1.6.2.2-第三轮全项目用户验收测试报告-智能体O.md` |
| 处置口径 | 保持 O 的原缺陷 ID 不变（O-09/O-08/O-05/O-13/O-11/O-10/O-12 + A-01 风险建议） |

---

## 一、处置总览

| 原 ID | 等级 | 问题 | 本轮处置 |
|---|---|---|---|
| O-09 | BLOCK | `#` 注释含引号使剥离器吞掉真实 LOAD → R042 漏报 | **已修复（词法器语句头判定）** |
| O-08 | MAJOR | 跨库 EXPLAIN 500：池类未导入 + `close_all` 接口错 | **已修复** |
| O-05 | MAJOR | 中文 finding_type 漏计 + 零问题/未采集混淆 + PDF 假结论 | **已修复** |
| O-13 | MAJOR | 日常巡检 400 却提示"采集已完成" | **已修复** |
| O-11 | MAJOR | 网关无效输入被当成功+健康 | **已修复** |
| O-10 | MAJOR | 报告内联脚本被主文档 CSP 拦截 | **已修复（独立文档响应+专用 CSP）** |
| O-12 | MINOR | run_all.py 实现基线哈希过期致门禁提前退出 | **已修复（拆两检查+演进基线清单）** |
| A-01 | 建议 | 脱敏开关作用范围未明示 | **已加范围提示** |

本轮 7 项缺陷 + 1 项风险建议全部完成整改，无延期项。

---

## 二、O-09（BLOCK）：R042 被注释中的引号绕过

**根因**：上一轮自研的 `_strip_comments_and_literals()` 状态机缺 `#` 行注释状态，`# operator's note` 里的单引号被误当字符串起点，吞掉后面的真实 `LOAD` 关键字，`has_load_data` 判 False，R042 漏报，LOAD XML 绿色通过。

**修复**（按 O 建议"复用项目已选择的 sqlglot 词法器"）：
- `parser_legacy.py` 新增模块级 `_lex_head_words()`（sqlglot 词法器取语句头词序列，词法器完整处理 `#`/`--`/`/* */` 注释与引号/反引号字符串）+ `_is_load_statement_head()` + `_is_create_routine_head()`
- `has_load_data` 检测与 `checker.py` 豁免判定全部改用词法器语句头，删除不完整的自研状态机
- 词法化失败返回 None → 不豁免（失败关闭，E999 兜底）

**验证**：
- O 的 27 条 LOAD 注释矩阵（`load_comment_matrix.py`）：`missing_r042=[]`、`false_pass=[]`，全部正确命中
- edge_probe 324 条：`kfn_without_e999=[]`（252 KFN 全保留，无退化）
- 新增 `tests/test_kfn_fail_closed.py` 的 `TestLoadHeadWithComments`：27 参数化正例 + 5 字符串诱饵反例 + 1 失败关闭例，全过

## 三、O-08（MAJOR）：跨库 EXPLAIN 500

**根因**：`slow_query_service.py` 使用未导入的 `TDSQLConnectionPool`（NameError），且错写 `_ephemeral_pool.pool.close_all()`（池对象自身即提供 `close_all()`，无 `.pool` 属性）。

**修复**：导入 `TDSQLConnectionPool`；两处清理改为 `_ephemeral_pool.close_all()`；临时池生命周期已有外层 try/finally 覆盖。**验证**：连接不存在 → ValueError（非 NameError）；静态接口检查通过。

## 四、O-05（MAJOR）：健康报告真实数据与状态不一致

**修复**（`ppt_report_service.py`）：
- 先读父运行记录 `index_audit` 区分采集状态：`not_run` / `completed_empty` / `completed_with_findings`
- finding_type 生产端中文（重复索引/前缀冗余索引/未使用索引/表碎片）统一映射到机器枚举，兼容存量
- 索引总数/表数从父运行记录取（真实采集范围），不从 finding 数猜测；查不出的分类计数诚实标 None
- PDF 健康结论以采集状态为前提：未采集 → "未评估"；完成且零问题 → "已覆盖全部在采范围，未发现…"；不再用空数组冒充"健康度极佳"；未采集时不说"深度扫描后"
- 补 `import re`（原 `re.search` 因中文 finding 永不匹配而是死代码，修复后首次真正执行暴露缺失导入）

**验证**：真实中文 finding（重复1/前缀2/未用3）正确统计且参与计分；`completed_empty` 不说未评估；`not_run` 明说未采集。

## 五、O-13（MAJOR）：日常巡检失败却提示成功

**修复**（`app.js` `runDailyInspect`/`compareDailyInspect`）：
- 逐日检查响应状态与业务结果；部分成功逐日列明；全部失败不触发比对（避免空数据冒充"无差异"）
- 比对请求失败明示，不用旧结果/空表冒充"比对完成"；只有比对真成功才提示完成

## 六、O-11（MAJOR）：网关无效输入被当成功

**修复**：
- `gateway_log_service.py`：零有效记录抛 ValueError，不再用行数冒充查询数、不再把空报告持久化为成功
- `gateway_log.py`：ValueError → 422（可读失败语义），不落 500、不返回 200

## 七、O-10（MAJOR）：报告内联脚本被主文档 CSP 拦截

**根因**：iframe `srcdoc` 继承主文档 CSP（`script-src` 无 `unsafe-inline`），报告交互脚本被拦。

**修复**：
- `gateway_log.py`：报告文档接口返回独立文档响应，携带文档级专用 CSP（`script-src 'unsafe-inline'` + `frame-ancestors 'self'` + `X-Frame-Options: SAMEORIGIN`），覆盖全局 DENY/none 基线；仍禁止外站嵌入
- `app.js`/`index.html`：iframe 从 `srcdoc` 改为 `src` 指向鉴权文档接口；文档边界仍隔离样式/脚本

**验证**：响应头实测 `script-src 'unsafe-inline'`、`frame-ancestors 'self'`、`X-Frame-Options: SAMEORIGIN`、内联脚本保留。

## 八、O-12（MINOR）：run_all.py 正式门禁提前退出

**修复**（按 O 建议"拆为两个明确检查"）：
- `run_all.py` implementation 模式：
  - 检查 1（设计合同真实性，独立保留）：设计包哈希必须与设计文档声明一致
  - 检查 2（实现版本验证）：实现包==设计包则按设计验证；不等则属"实现演进"，需经评审的实现基线清单确认
- 新增 `implementation_baseline.json`：记录当前实现包哈希、演进来源设计包哈希与评审依据

**验证**：检查 1 设计合同真实性 PASS（6412e076…）；检查 2 实现版本验证 PASS（evolved, baseline-recorded）。

## 九、A-01（建议）：脱敏开关范围提示

系统信息页脱敏开关加 tooltip + 行内提示："仅脱敏慢SQL入库，不覆盖网关报告等导出物"，避免用户误以为报告也受保护。

---

## 十、复测入口（给 O 第四轮）

| 项 | 复测要点 | 期望 |
|---|---|---|
| O-09 | 重跑 27 条 LOAD 矩阵 + 即时/文件/上传 + `#` 注释反例 | 真实 LOAD 全命中，假 LOAD 不触发，无绿色通过 |
| O-08 | 默认/显式默认/存在的其他库/不存在库/SQL 错误 | 无 NameError，临时池清理，不存在库 422 |
| O-05 | 未执行/真实零问题/真实重复前缀 分别核对库→API→页面→PDF | 采集状态正确，计数真实，健康结论有据 |
| O-13 | 全成功/全 400/单日失败/无指标 | 提示准确，失败不冒充完成 |
| O-11 | 空/全无效/混合/错误 log_type | 零有效记录 422，不入成功报告 |
| O-10 | 打开网关报告交互控件 | 折叠/时间/火焰图生效，不污染父页面 |
| O-12 | `run_all.py --mode implementation` | 两检查均 PASS，不再提前退出 |

全量回归：**1450 passed / 0 failed / 0 skipped**（第三轮基线 1417 + 本轮新增 33 条 LOAD 注释/诱饵/失败关闭用例）。核心冻结测试（71 条）与 27 条 LOAD 矩阵、324 条 edge_probe 均无退化。
