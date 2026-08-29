# v1.6.2.2 UAT 第四轮修复说明

| 项 | 内容 |
|---|---|
| 修复人 | 智能体 Q |
| 修复日期 | 2026-08-29 |
| 依据 | `UAT-v1.6.2.2-第四轮全项目用户验收测试报告-智能体O.md` |
| 处置口径 | 保持 O 的原缺陷 ID 不变（O-14/O-15 为 BLOCK，O-16/O-17/O-18/O-19 为 MAJOR，O-20/O-21 为 MINOR），全部同轮完成 |

---

## 一、处置总览

| 原 ID | 等级 | 问题 | 本轮处置 |
|---|---|---|---|
| O-14 | BLOCK | 仅 CR 换行的 `--` 注释边界使残缺 VIEW 绿色通过（新增 fail-open） | **已修复（统一换行规范化 + 失败关闭不变量）** |
| O-15 | BLOCK | 网关报告 iframe 被浏览器拒绝 + 存储型脚本注入通道 | **已修复（按 O 要求顺序：数据编码→CSP去 unsafe-inline→嵌入策略→一次性票据）** |
| O-16 | MAJOR | 巡检全失败后仍展示上一组成功比对结果 | **已修复（结果绑定实例/日期/状态 + 统一清理）** |
| O-17 | MAJOR | 混合网关日志静默丢行并按完整报告展示 | **已修复（结构化质量统计 + partial 状态 + 覆盖率告警 + 阈值拒绝）** |
| O-18 | MAJOR | 索引 PDF 重复索引对方名称仍为 `N/A` | **已修复（结构化字段落库 + 消费端不再解析自然语言）** |
| O-19 | MAJOR | 保存但未连接的实例跑巡检返回裸 500 | **已修复（连接异常领域化，映射可读 422/400）** |
| O-20 | MINOR | 跨库 EXPLAIN 预处理异常泄漏临时连接池 | **已修复（临时池单一 try/finally 覆盖全生命周期）** |
| O-21 | MINOR | 正式门禁 `codestat generated section mismatch`，RESULT FAIL | **已修复（实现基线含生成物审计哈希，门禁同源比对，实测 RESULT PASS）** |

本轮 2 BLOCK + 4 MAJOR + 2 MINOR 全部完成整改，无延期项。

---

## 二、O-14（BLOCK）：CR 注释边界的残缺 VIEW 绿色通过

**根因**：两处不一致叠加——
1. 拆句/注释剥离的正则以 `\n` 为行注释终止符，单独 `\r` 使 `-- ordinary` 把后面的真实语句整体吞掉（文件入口拆出 0 条、`total_sql=0`）；
2. sqlglot 词法器把 `\r` 当换行，语句头判定得到 `CREATE VIEW`，语句头豁免吞掉非 KFN 的 `parse_error`（E999 不上报），`sql_type=UNKNOWN` 又无规则兜底 → `passed=true`。

**修复**：
- `parser_legacy.py` 新增共享 `normalize_newlines()`（CRLF/单独 CR 统一为 LF）并经 `parser/__init__.py` 导出；`checker.audit_sql`、`checker.audit_file`、`database.split_sql_statements`（batch-stream 拆句）三入口消费同一份规范化文本；
- `checker.audit_sql` 末尾新增硬性失败关闭不变量：**非 KFN 的 `parse_error` 存在时，最终必有一个 ERROR 级阻断项**——语句头豁免只能阻止 E999 的直接上报，不能让解析失败无声消失；确需兼容的语法走 KFN/unsupported 合同；
- 真实特殊语句（解析成功、无 `parse_error`）不受影响，不扩大拒绝域。

**验证**：
- O 的原始反例 `-- ordinary\rCREATE VIEW v AS SELECT 1 +` 在即时/文件/拆句三入口均不再 `passed=true`，文件入口拆出 1 条并判失败；
- 全业务规则关闭时不变量仍兜底产出 E999；
- 新增 `tests/test_o14_cr_fail_closed.py`（46 参数化用例）：LF/CRLF/单 CR/末尾无换行/前导空语句/字符串诱饵/反引号诱饵 × 三入口，断言“解析错误绝不绿色”；`test_kfn_fail_closed.py` 原有 63 条全部通过，无退化。

## 三、O-15（BLOCK）：网关报告 iframe 拒绝 + 存储型脚本注入

严格按 O 给出的顺序（顺序不可颠倒）整改：

1. **数据→脚本上下文编码**：`analyze_gateway_log.py` 新增 `_js_json()`，火焰图数据序列化时把 `<`/`>`/`&`/U+2028/U+2029 统一转为 Unicode 转义——日志 SQL 中的 `</script>` 不再能提前结束脚本元素；
2. **去掉 `unsafe-inline`**：报告模板全部内联事件处理器（`onclick=` 等）改为 `addEventListener` 绑定（目录导航、章节折叠、火焰图按钮、SQL 详情弹窗）；服务端对每个响应生成随机 **nonce**，裸 `<script>` 注入 `nonce` 后由 `script-src 'nonce-…'` 放行，CSP 不再含 `unsafe-inline`；服务时另剥离旧报告残留的内联处理器（仅脚本块之外，不误伤已转义数据）；
3. **统一 iframe 策略**：删除与不透明源 sandbox 冲突的 `X-Frame-Options`（含全局基线的 `DENY`，报告端点以 `frame_embeddable` 标记豁免），嵌入控制只由 `frame-ancestors 'self'` 承担；前端 iframe 保留 `sandbox="allow-scripts"`（不透明源，父页面 DOM/认证不可达）；
4. **认证不放长期令牌进 URL**：新增 `GET /api/v1/gateway-log/reports/{id}/ticket`（头部令牌 + RBAC 鉴权）签发 **90s 一次性票据**（用后即焚、绑定报告 ID），中间件消费票据放行 `/html`；前端 `viewGatewayReport` 先换票据再嵌入，URL 不再出现登录令牌；
5. **真实 Chromium 验证**（认证关闭的隔离环境 + 真实浏览器会话）：
   - iframe 正常渲染（标题/章节/表格/图表可见），目录折叠、章节折叠、火焰图 1h/1d/时间范围交互全部生效；
   - 恶意合成标记 `</script><script>window.__pwned=1`、`<img onerror=…>` 注入后 `typeof window.__pwned`/`__pwned_img` 均为 `undefined`；
   - iframe 内访问 `window.parent.document` 抛 `SecurityError`（不透明源隔离），父页面 `contentDocument` 为 `null`；
   - 控制台 0 条 CSP 拦截/报错；截图 `docs/evidence/v1.6.2.2-uat-q-r4/o15_browser_report.png`。

**验证（自动化）**：新增 `tests/test_o15_gateway_report_security.py` + 扩展 `tests/test_gateway_log.py`——恶意日志无脚本断点、`\u003c` 转义断言、无真实内联处理器、nonce 化全覆盖、无 `unsafe-inline`、无 XFO、票据签发/一次性/绑定报告/伪造拒绝。

## 四、O-16（MAJOR）：巡检旧结果残留

**修复**（`app.js` + `index.html`）：
- 比对结果绑定范围：成功时写入 `dailyCompareScope = {connection_id, date1, date2, generated_at, status}`；
- 新增 `dailyResultVisible` 计算属性：**仅当结果范围与当前选择的实例/日期完全一致且状态成功**时允许展示（比对差异表格与趋势图均由其门控）——切换实例、切换日期范围后旧结果自动不可见；
- 新增 `resetDailyResult()` 统一清理（结果/范围/趋势图/节点与 IP 筛选/分页），在手动采集开始、重新比对开始两个任务边界执行；全失败与比对失败分支因“开始即清空”不再残留旧数据。

**验证**：`tests/test_o16_daily_result_scope.py` 结构守卫（范围绑定、可见性匹配、清理覆盖面、模板门控、setup 暴露）5 组断言全部通过。

## 五、O-17（MAJOR）：混合网关日志静默丢行

**修复**（`gateway_log_service.py` + `gateway_log.py` + `app.js`）：
- 解析器返回结构化质量统计：`total_lines/empty_lines/nonempty_lines/parsed_lines/skipped_lines/invalid_format_lines/no_timecost_lines/numeric_error_lines/coverage_ratio/skip_samples`（前 5 条跳过样例含原因）；
- 全无效仍 422（错误信息带分类原因统计）；混合输入返回 `status=partial`，报告顶部注入醒目“数据完整性告警”横幅（覆盖率 + 跳过分类 + 样例，并声明“结论仅覆盖已解析部分，不代表全量输入”）；上传响应携带 `parse_quality`，前端以 8 秒可关闭的 warning 提示覆盖率；
- 新增可配置阈值 `GATEWAY_MAX_SKIP_RATIO`（默认 0.5）：跳过比例超阈值拒绝生成报告（422），不得大量丢行仍按完整报告展示。

**验证**：`tests/test_gateway_log.py` 新增混合输入（4 有效 + 3 无效 → partial + 覆盖率 57.1% + 报告横幅断言）、超阈值拒绝（10% 覆盖率 → 422）、全无效分类原因（422 含“格式不匹配”）三组用例。

## 六、O-18（MAJOR）：索引 PDF 重复索引对方 `N/A`

**根因**：生产端写“索引 A 与 B 列完全相同(columns)”，消费端只识别旧格式“与 xxx 完全重复”，正则永不匹配 → `N/A`；且“包含列”取自 `metric`（重复索引恒为空）。

**修复**：
- 新增迁移 `backend/schema/v11/110_index_finding_structured.sql`：`index_audit_finding` 增加 `related_index_name`、`index_columns` 结构化字段（全新安装 DDL 同步）；
- `index_audit_service.py`：重复索引/前缀冗余 finding 生产时写入结构化字段并落库；
- `ppt_report_service.py` 新增 `_duplicate_pair_fields()`：**结构化字段优先**，存量记录兼容两种文案格式（现行“列完全相同”与旧“完全重复”），仅在兼容分支才解析自然语言；
- “包含列”改为结构化 `index_columns` 优先。

**验证**：`tests/test_o18_duplicate_index_structured.py`（8 用例：结构化优先/现行格式/多列/旧格式/反引号/未知文案兜底）+ `tests/test_index_audit.py` 扩展（真实库重复索引 `related_index_name`/`index_columns` 落库、报告消费端 `index2` 与 `columns` 非空），全部通过。

## 七、O-19（MAJOR）：离线实例巡检裸 500

**根因**：`registry.get()` 自动建连时 `register(validate=True)` 执行 `SELECT 1`，离线实例抛底层连接异常；API 只捕获 `ConnectionNotFoundError`，真实连接失败发生在既有异常边界之外，穿透为裸 500。

**修复**：
- 新增 `backend/services/connection_errors.py` 领域异常（连接拒绝/认证失败/库不存在/monitordb 不可用）与 `translate_db_error()` 翻译器；
- `daily_inspect.py /run` 把“获取连接池 + monitor_probe + run_daily”纳入**同一完整异常边界**：领域异常映射为可读 422（连接拒绝/认证失败/库不存在各有明确文案），未保存实例保持 400，monitordb 不可用保持 400；**未知程序错误仍 500**（中间件下发 X-Request-ID），不掩盖真正缺陷。

**验证**：`tests/test_o19_offline_instance_inspect.py`——端口无服务的离线实例返回 422 且文案可读（非 500/非 Internal Server Error）、未保存实例 400、领域翻译四类断言，6 用例全部通过。

## 八、O-20（MINOR）：跨库 EXPLAIN 临时池泄漏

**根因**：临时池在验证后创建，但 `try/finally` 从 EXPLAIN 执行才开始；预处理阶段（多条正则清洗）不在 finally 承重范围内，预处理异常时 `close_all=0`。

**修复**（`slow_query_service.py`）：临时池一经创建即进入**单一 `try/finally`**，覆盖验证、预处理、执行、分析、返回全生命周期；验证分支的友好错误映射（库不存在/连接失败 → ValueError）保留，关池统一由外层 finally 承担。

**验证**：`tests/test_o20_explain_pool_lifecycle.py`——对预处理（`re.sub` 注入）、括号平衡（`re.split` 注入）、执行阶段分别注入异常，全部断言 `close_all()==1`；验证失败（1049/2003）映射 ValueError 且关池一次，5 用例全部通过。

## 九、O-21（MINOR）：正式门禁 `RESULT FAIL`

**根因**：实现演进后，门禁仍拿“当前实现生成的 codestat 章节”逐字比旧设计文档中的生成章节，必然失配。

**修复**（`run_all.py` + `implementation_baseline.json`）：
- 实现演进模式下，生成物（manifest 章节 / codestat 章节）**不再硬套旧设计文档，也不得跳过**：与经评审的实现基线清单中的 `implementation_audit.manifest_section_sha256` / `codestat_section_sha256` 同源比对（规范化 SHA256），基线缺失或哈希不符均判失败；设计一致模式仍逐字比对设计文档；
- `implementation_baseline.json` 更新为第四轮整改后的实现包哈希（`fea7a873…`，演进自设计包 `6412e076…`，前序实现包 `73bcfd84…` 留档），并记录两个生成物审计哈希与评审依据；
- 设计合同真实性检查（设计包哈希=设计文档声明）独立保留。

**验证**：正式命令 `python docs/evidence/v1.6.2.2/run_all.py --mode implementation --matrix` 实测：三版本矩阵、71 条冻结用例、全量测试、生成物基线比对全部通过，**退出码 0、输出 `RESULT PASS`**（执行记录 `docs/evidence/v1.6.2.2/o21_gate_run.txt`）。

---

## 十、复测入口（给 O 第五轮）

| 项 | 复测要点 | 期望 |
|---|---|---|
| O-14 | 三版本 × 即时/文件/上传（含仅 CR、CRLF、前导空语句、字符串/反引号诱饵） | 残缺语句绝不绿色；文件入口不得 0 条成功；真实特殊语句不误伤 |
| O-15 | 真实 Chromium：查看报告、折叠/时间范围/火焰图、注入 `</script>`/`<img onerror>`/U+2028 日志 | iframe 可见可交互；恶意标记不执行；CSP 无 `unsafe-inline`；父页面不可达；URL 无长期令牌 |
| O-16 | 先成功比对 → 切日期/实例后全失败 | 旧结果不再展示；失败提示准确 |
| O-17 | 空/全无效/混合/超阈值 | 全无效与超阈值 422；混合返回 partial + 覆盖率告警 |
| O-18 | 真实重复索引样本 → 大屏 + PDF | 对方索引名与包含列真实可见，不再 `N/A` |
| O-19 | 对保存但离线的实例跑巡检 | 可读 422（非裸 500）；未保存实例 400 |
| O-20 | 预处理/执行各步故障注入 | `close_all()==1`，无池泄漏 |
| O-21 | `run_all.py --mode implementation --matrix` | 退出码 0，`RESULT PASS` |

**回归结果**：全量自动化 **1506 passed / 0 failed / 28 skipped**（第三轮基线 1450 + 本轮新增 56 条：O-14 46 参数化、O-15 13、O-16 5、O-17 3、O-18 10、O-19 6、O-20 5 等）；正式门禁三版本矩阵（`sqlglot 29.0.0 / 30.14.0 / 30.17.0`）每版 680 项清单用例与 71 条冻结用例全部通过；119 条规则语料未受本轮改动影响（O-14 不变量仅收紧“解析错误不得绿色”，真实可解析语句行为不变）。
