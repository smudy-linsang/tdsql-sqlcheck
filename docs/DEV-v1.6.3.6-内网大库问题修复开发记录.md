# DEV-v1.6.3.6 内网大库测试问题针对性修复 — 开发记录

| 项 | 内容 |
|---|---|
| 产品版本 | v1.6.3.6 |
| 设计依据 | `docs/DETAIL-v1.6.3.6-内网大库测试问题针对性修复详细设计.md`（G，Rev.A） |
| 问题来源 | 内网测试环境 v1.6.3.5 验收报告 + Mr.Linsang 人工实测（3 库：2668 表成功 / 6097 表持久化失败 / ECIF 分布式库 0 秒失败） |
| 施工方 | 智能体 Q |
| 施工日期 | 2026-09-11 |

---

## 一、对 G 修复方案的评审结论：**认可方向，两处修正后施工**

### 认可（根因正确、方向正确）
- **BUG-01**（ECIF 分布式库 0 秒崩溃）：根因正确。Q 用 Mr.Linsang 提供的 v1.6.3.4 对照文件
  `extracted_lzbj_ecif_20260910_224104.sql` 独立核实：该文件 **0 个 `_tdsql_subp` 子表、219 张正常表**，
  证明 v1.6.3.4 是"跳过不可读子表"成功的；v1.6.3.5 的"单表零容忍"（设计 §5.3 fail-closed）在
  TDSQL 分布式物理子表场景退化为"杀全库"。G 的"子表前置过滤 + 单表容错跳过 + 仅全失败才报错"正确。
- **BUG-02**（6097 表持久化被拦）：根因正确。`publish` 预检的 `*2` 是虚假翻倍（44MB→88MB 自杀式误拦）；
  44MB 单字段巨石存储本就脆弱。G 的"去 `*2` + 超大轻量压缩"方向正确。

### 修正一：compaction 必须用**向后兼容 list 格式**（不照抄 G 的 dict）
G 方案把超大结果压成 `{"_storage_mode":..., "records":[...]}` **字典**。Q 核实 `audit_history.results_json`
的既有消费方——`report_service.py:275`、`dashboard.py:234`、`sql_audit.py:448/528/727`、
`snapshot_extractors.schema_audit.extract_from_json`——**全部 `json.loads` 后当 list 遍历**；
dict 会直接打崩历史报告/看板/快照。故 Q 改为 **list 格式**压缩（保留全部违规条目 + 前 50 条通过项，
仍是 JSON list），完整明细始终在本地 `artifacts/results.ndjson`，前端分页读取体验无损。

### 修正二：**拒绝 G 改造点 3**（deploy 自动 `SET GLOBAL max_allowed_packet` 且硬编码口令）
`mysql -uroot -ptdsql_test_2024 -e "SET GLOBAL max_allowed_packet=..."` 把**明文口令写进部署脚本**，
违反"敏感配置禁止入库/入脚本"的既定安全规约；且自动 SET GLOBAL 违背 v1.6.3.5 设计"禁止自动改服务器
参数"的原则。compaction（>32MiB 降级）已使该调优无必要。**本轮不实施改造点 3**。

---

## 二、施工内容

| 文件 | 改动 |
|---|---|
| `backend/services/metadata_audit_pipeline.py` | ① 新增 `_TDSQL_INTERNAL_PARTITION_PATTERN` + `is_tdsql_internal_table()` 前置过滤物理子表；② 提取循环单对象 `try/except` 容错：失败记 `skipped_objects` + 在 SQL 文件注入 `[SKIPPED]` 存证注释，不杀全库；③ 仅当**全部**对象失败才抛 `NO_AUDITABLE_OBJECTS`；④ stats 增加 `skipped_objects`/`skipped_list` |
| `backend/services/metadata_audit_repository.py` | ① `publish` 预检去 `*2`（`payload = raw + _PACKET_MARGIN`）；② 新增 `compact_results_for_audit_history()`（>32MiB 触发，**list 格式**：违规全留 + 通过样例 50）；③ 压缩后同步替换 `audit_columns_values[8]` 再预检 |
| `backend/workers/metadata_audit_worker.py` | 收尾 progress 写入对 `skipped_list` 限长（前 50 条，防超 progress_json 128KiB） |
| `backend/api/metadata_audit.py` | `_job_summary.progress` 透出 `skipped_objects` |
| `frontend/index.html` | 进度行条件显示"跳过 N"；版本标记升 1.6.3.6 |
| `VERSION` / `backend/config.py` | 版本升 1.6.3.6 |
| `tests/test_v1636_bugs.py`（新） | 12 用例：子表识别 6 参数化 + 单表容错 + 子表前置过滤 + 全失败仍报错 + 压缩未触发/触发为 list + 预检无假翻倍 |

---

## 三、验证

- `tests/test_v1636_bugs.py` **12 passed**（含 `_tdsql_subp190001` 识别、单表 660 容错跳过、
  70MB 压缩为 list 且违规保留、40MB 预检不被 `*2` 误拦）。
- 版本一致性 + 交付门禁 **19 passed**。
- **全量回归 2122 passed + 30 skipped + 0 failed**（30 skip 为既定环境性跳过，非本轮引入）。

## 四、边界声明
1. 本轮**不实施** G 改造点 3（deploy 自动调 max_allowed_packet + 硬编码口令），理由见"修正二"。
2. compaction 触发后，`audit_history.results_json` 为"违规全量 + 通过样例 50"的 list；
   历史详情/看板/快照消费方契约不变（仍为 list）。完整明细以本地 artifacts 为准。
3. 真实内网 6000+ 表复测、Linux/systemd 部署验证仍需内网环境（G/内网智能体组织）。

---

施工人：智能体 Q
施工对象：v1.6.3.6（内网大库问题针对性修复）
提交给：Mr.Linsang

---

# SIT 第一轮整改（D 报告：主功能修复有效，发现 8 项次生问题，不放行 → 全部整改）

| 项 | 内容 |
|---|---|
| 整改日期 | 2026-09-11 |
| D 结论 | BUG-01/BUG-02 主功能修复**有效**；发现 3 高 + 3 中 + 2 低次生问题；不放行，整改后复测 |

## 逐项整改（8 项全部认可）

| 编号 | 级别 | 问题（D 实测） | 整改 |
|---|---|---|---|
| B-01 | 高 | 32MiB 写死压缩阈值，内网 6097 表库（42MiB）历史报告静默丢 96.7% 明细，而该体量本不需压缩 | 压缩阈值改为**按 max_allowed_packet 自适应**：`threshold=(max_pkt-margin)/1.25`。64MiB 包限下阈值≈53MiB，42MiB 不触发压缩 → 无损。预检用转义后真实大小（×1.25） |
| B-02 | 高 | 子表识别只用命名匹配，违反本项目 Rev.G/P1-03；`\d*` 是笔误 | 移植三条件判据：集中式一律不过滤 + 分布式要求父表在枚举清单 + `\d+`（至少一位数字）；候选能否 SHOW CREATE 由提取循环 try/except 兜底 |
| B-03 | 高 | BUG-02 回归锁假绿（用例只重算公式没真调 publish） | `test_publish_precheck_no_false_double` 改为**真调 publish()**（真实库，33MB 落在 *1.25 放行/*2 拦截的区分带）；补 M8（读回校验列）/M9（skipped_list 截断）/M10（`\d+`/集中式/父表）锁 |
| M-01 | 中 | 去 *2 后余量归零 + rollback 在死连接上再抛致错误归因误导 | 预检系数取实测 1.25；`except` 里 `conn.rollback()` 与 `conn.close()` 均包 try/except，保证 MetadataJobError 一定抛出 |
| M-02 | 中 | skipped_list 限长只加在最后一个写点，对峰值无效；128KiB 注释不实 | `skipped_list` 在 `extract_metadata` 返回处截断（计数全留 + 样例 50），下游写点天然安全；删错误注释 |
| M-03 | 中 | 完整性判据放宽后 fail-open，跳过在历史侧不可见 | 跳过按原因分类（`tdsql_internal` 良性 / `extract_failed` 异常）；异常跳过超阈值（>50 或 >5%）打醒目告警；`report.html` 口径行补"跳过 N（异常 M）" |
| N-01 | 低 | `-- Object Name:` 未 sanitize（CR/LF 注入） | 对象名加 `sanitize_comment` |
| N-02 | 低 | compact 的 `job_id` 形参未用 | job_id 纳入压缩日志（大库排障定位） |

## 变异自证（D 规约）
- M1（publish 恢复 `*2`）→ `test_publish_precheck_no_false_double` 真调 publish 变红（PERSIST_PAYLOAD_TOO_LARGE）。
- M10（`\d+`→`\d*`）→ `test_m10_subp_requires_digit_and_parent_and_distributed` 变红。
- 恢复后全绿，git 工作区无变异残留。

## 验证
- `test_v1636_bugs.py` 15 用例全过（子表三条件 / 单表容错 / 全失败仍报错 / 真调 publish / 自适应阈值不压缩 / 截断 / `\d+` 集中式父表）。
- 全量回归 **2125 passed + 30 skipped + 0 failed**（30 skip 既定环境性）。

## 边界声明
- "候选自身不在 Proxy 结果中"（Rev.J 第3条）在本模块由提取循环 try/except 兜底（Proxy 660 容错跳过）近似，因 pipeline 枚举自 information_schema 而非单独查 Proxy；三条件中的可判定部分（分布式门 + 父表存在 + 严格命名）已落实。
- `audit_history` 增列持久化跳过计数（M-03 建议）未加列（避免改表结构），跳过计数经 report.html 口径行 + manifest + progress 透出；如需历史列表列展示，建议下一版评估。
- 真实内网 6000 表复跑与 Linux 部署验证仍待内网。

---

施工人：智能体 Q
施工对象：v1.6.3.6（内网大库修复 + SIT 第一轮整改）
提交给：Mr.Linsang
