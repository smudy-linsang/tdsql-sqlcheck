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
