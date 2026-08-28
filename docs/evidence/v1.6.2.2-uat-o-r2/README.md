# 第二轮 UAT 证据包（智能体O）

被测 main：`d12fe8c16ab9861b85eea41e05e498a9ba305c7d`；差分基线：`6957499`（首轮报告提交，产品代码与首轮被测 a698cfc 一致）。

[主报告](../../UAT-v1.6.2.2-第二轮全项目用户验收测试报告-智能体O.md)结论为 **NO-GO**。本包是测试证据，不是产品修复；不修改原第一轮材料。

## 证据索引

| 文件 | 用途 |
|---|---|
| `browser_steps.json` + `01–64` 同名 JPG/TXT | 真实浏览器操作、页面截图及DOM；时间为UTC，北京时间加8小时 |
| `rule_probe_current.json` | 本轮重跑首轮原脚本的1000输入及119定义、命中、判据；完整输入都在rows |
| `round2_diff.json` | 1000输入命中集合的70项变化、5项剩余判据失败；新增边界160项变化 |
| `edge_probe.py`、`edge_current.json`、`edge_baseline.json` | 324边界输入；旧版与当前；KFN仍缺E999的60例 |
| `edge_29_0_0.json`、`edge_30_17_0.json` | 同一324输入的另外两个版本；三版本均60例遗漏 |
| `http_round2.py`、`http_results.json` | 鉴权开启的614条本地HTTP证据；三入口及三角色补充，不代替UI |
| `service_probe.py`、`service_current.json`、`service_baseline.json` | 17个routine引擎/服务差异在旧新版完全一致 |
| `full_regression.txt/xml`、`implementation_matrix.txt` | 两次各1384通过、680×3、71、文档和bundle校验 |
| `*_auth_override.*` | 误把鉴权开启环境带入旧测试造成4个401的失败证据，不能隐去 |
| `gateway_report.html`、`gateway_css_leak.json` | 空报告正文与全局样式泄漏 |
| `supplemental_round2.py`、`supplemental_results.json` | 报告/看板/真实数据计数/MonitorDB限制；采集索引前的证据 |
| `file_report_609/610/611.html` | 修好KFN、剩余KFN和100条语料的实际服务端导出 |
| `ops_report.pdf`、`ops_report-1/2.png` | 实际PDF及Poppler两页渲染，逐页检查过；可读不等于浏览器下载成功 |
| `download_attempt.json`、`pdf_download_attempt.json` | 浏览器实际点击后5秒未捕获下载事件；保留为未验证项 |
| `explain_context_probe.py`、`explain_context_results.json` | 相同SQL/连接，仅db_name不同得到200/500/200 |
| `performance_current/baseline.json` | 复用首轮探针的有界引擎成本对照，不是SLA |
| `prepare_browser_fixture.py`、`final_state.py/json` | 精确隔离库的合成账号/慢SQL前置和交付时只读清点 |
| `rule_coverage_119.md`、`regression_modules.json` | 本轮数据重新生成的逐规则/测试模块账本 |
| `validate_evidence.py`、`validation.json`、`evidence_manifest.json` | 离线一致性、链接、凭证模式及SHA256核验；不是产品复测 |

`25/27` EXPLAIN没有成功结果，`44`是错误提示、`45`是刷新后同SQL成功控制。`62`是精简HASH回归fixture，`63`是带索引广播fixture，不将其宣称为用户最初附件完整原文。首轮已校验一致的gg77/gg78文本在 `../v1.6.2.2-uat-o-r1/original_gg77.sql` 和 `original_gg78.sql`。

## 本轮输入与准确统计

- 1000原输入：178语料 + 20生产fixture双架构 + 7合成元数据 + 720组合 + 75KFN鉴别。119定义相同、114实际命中；5个原有缺口，不能视为通过。
- 原75KFN的72个E999遗漏，本轮消除70、剩2。1000输入判据仍5失败，其中另外3个为原有精确期望差异。
- 新324输入：252KFN路径组合 + 30真实对象/空白 + 35普通解析错误 + 3marker字面量 + 4特殊控制。包含故意损坏表尾，并非全部合法SQL。
- 三版本各有同一60个KFN遗漏；这60个在显式关闭业务规则的引擎诊断中全通过，系统实际生效规则集未改。
- 第一批614 HTTP为611个200、3个预期403；之后EXPLAIN控制另有1个500，不能宣称整轮零5xx。
- 浏览器64组、19页面和深度诊断9页签；只读入口/环境受阻/失败/成功各自区分，不声称所有业务操作均通过。

## 复现命令与隔离要求

以下在仓库根目录执行。使用本机已配置的测试数据库连接参数；**只允许本轮隔离元数据库，不得指向生产或原8000服务。** 不在命令、证据中保存真实口令/token。本轮脚本不执行所审核的TDSQL建表文本。

```powershell
$env:PYTHONUTF8='1'
C:/Python314/python.exe docs/evidence/v1.6.2.2-uat-o-r1/rule_probe.py --repo C:/Codex/tdsql-sqlcheck --out docs/evidence/v1.6.2.2-uat-o-r2/rule_probe_current.json
C:/Python314/python.exe docs/evidence/v1.6.2.2-uat-o-r2/edge_probe.py --repo C:/Codex/tdsql-sqlcheck --out docs/evidence/v1.6.2.2-uat-o-r2/edge_current.json
C:/Python314/python.exe docs/evidence/v1.6.2.2-uat-o-r2/run_round2.py compare
C:/Python314/python.exe docs/evidence/v1.6.2.2-uat-o-r2/run_round2.py full
C:/Python314/python.exe docs/evidence/v1.6.2.2-uat-o-r2/run_round2.py matrix
```

`run_round2.py` 复用首轮显式admin/慢SQL fixture；全量遵循 tests/conftest 的AUTH=false，安全测试自行启用；真实浏览器服务AUTH=true不变。不要用全量测试的无header调用证明鉴权开启场景通过。

三版本新边界复现：对29.0.0、30.14.0、30.17.0各自环境调用同一 `edge_probe.py`。本轮正式runner保留的环境位于 `C:/Users/linsa/AppData/Local/Temp/v1622-revq-evidence-h3fdnkee/venv_29_0_0`、`venv_30_14_0`、`venv_30_17_0`；临时路径不保证在另一台机器存在。

HTTP、报告和EXPLAIN脚本需要 `UAT_O_PASSWORD` 环境变量中的**合成测试账号口令**；服务地址固定为loopback8002。fixture脚本还要求 `SQLCHECK_DB_NAME=tdsql_uat_o_r2_1622_20260828`。不要为“重新测试”覆盖已归档结果；后续轮次请输出到新的证据目录。

浏览器步骤由可用的浏览器技能逐项操作，不提供绕过UI的伪点击脚本。文件输入复用第一轮目录的 `uat_kfn_comment.sql`、`uat_core_rule_corpus.sql`、`uat_mybatis.xml`、`uat_gateway_interf.txt` 及本轮 `uat_r2_remaining.sql`。

PDF由服务真实输出，使用本地Poppler渲染两页（本包PNG为1132×1600）。复现可用 `pdftoppm -png ops_report.pdf ops_report`；分辨率差异不改变内容判据。字体替代警告来自本地渲染环境；两页已目视核对，正文缺失不是截图裁切。

离线检查（只检查已有证据，不重跑产品）：

```powershell
C:/Python314/python.exe docs/evidence/v1.6.2.2-uat-o-r2/validate_evidence.py
```

本目录 `.gitattributes` 仅为本包保留原始字节，避免Git换行规范化破坏SHA256；不更改产品换行策略。清单不包含自身，主报告另列hash。

## 保留状态

浏览器已退出。8002测试服务、隔离数据库、3条合成连接（含DBA本轮创建并编辑的618d9bed）、1个Digest扫描、51条慢SQL、审核与比较留档保留供Q复现，详见 `final_state.json`。没有删除生产/用户数据，也没有声称对未执行的删除或ZK成功路径验收通过。
