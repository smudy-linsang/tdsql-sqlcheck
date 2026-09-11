# v1.6.3.6 内网大库问题修复 · UAT 用户验收测试报告（智能体D）

| 项 | 内容 |
|---|---|
| 产品版本 | v1.6.3.6 |
| 被测提交 | `main@5521267`（A 第四轮 SIT 放行版本；产品代码与我实跑的 `5bac1f9` 之后的文档提交一致） |
| 版本定位 | 针对内网 v1.6.3.5 实测暴露的 **BUG-01（ECIF 分布式库 0 秒崩溃）** 与 **BUG-02（6097 表持久化自杀式拦截）** 的定向修复 |
| 测试类型 | UAT（真实浏览器操作 + 行为级复测 + 独立变异验证） |
| 测试方 | **智能体D**（接替无额度的 O；与施工方 Q、SIT 方 A 分离） |
| 测试日期 | 2026-09-11 |
| 证据目录 | `docs/evidence/v1.6.3.6-uat-d/` |
| 提交给 | Mr.Linsang |

---

## 1. 结论

**BUG-01 与 BUG-02 的修复在功能、呈现面与锁有效性三个层面均复测通过；发现 1 项新的中等级缺陷
（UAT36-01：PUBLISHED 悬挂任务把用户界面永久锁死），已给出照图施工级方案。**

| 复测项 | 结论 |
|---|---|
| BUG-01 子表容错跳过（零误杀 / 良性 / 异常 / 集中式门禁 / 全失败 fail-closed） | ✅ **5/5 通过** |
| BUG-01 端到端（真实 worker：含良性 2 + 异常 1 的完整任务与产物） | ✅ 通过 |
| BUG-02 预检不再虚假翻倍（区分带双向） | ✅ 通过（53.6 MB 真实落库 / 55.7 MB 友好拦截） |
| BUG-02 自适应阈值 + 超大库压缩（omitted 自洽、list 契约、pass_rate 不被污染） | ✅ 通过 |
| BUG-02 包限调小（4 MiB）后的表现 | ✅ 通过（正确压缩 / 友好拦截，**无裸 PyMySQL 报错**） |
| 四个呈现面（运行面板 / 历史列表 / 历史报告 / 下载）与口径一致性 | ✅ 通过（report 4 = 跳过 3 = 良性 2 + 异常 1，四处一致） |
| 回归锁有效性（独立变异抽查 5 项） | ✅ **5/5 全杀**，恢复后 20 例全绿 |
| 全量回归 | ✅ **2130 passed / 0 failed / 0 errors / 30 skipped** |
| **UAT36-01（新发现）** | ⚠️ 中等级，**不影响本次两个缺陷的修复结论**，建议随下一版整改 |

---

## 2. 测试环境与边界（先说清不能代表什么）

| 项 | 值 |
|---|---|
| Web / runner / 注入代理 | `127.0.0.1:8025` / 独立 runner / `127.0.0.1:8026`（丢弃受理响应注入） |
| 元数据库 | 本机 MySQL 8.0.45，专用库 `uat_d_1636_meta`（`max_allowed_packet` = 64 MiB，实测读取，未写死） |
| 目标库 | `uat_d_1636_dist`（9 对象，含 2 张"命名像 TDSQL 物理子表"的**真实可读表**）、`uat_d_1636_cent`（2 对象，含 1 张同命名可读表） |
| 测试账号 | `uat_d_1636`（admin，运行期生成口令，不入库） |
| 浏览器 | Playwright 1.60 驱动本机 Chrome（headless）：真实输入、真实点击、真实下载事件 |

**边界（不得据此宣告的结论）**：
1. 本机是 MySQL 8.0，**没有 TDSQL Proxy**，因此"物理子表不可读"用**等价的 SHOW CREATE 异常注入**
   模拟，错误码与文案取自内网实测（`660 Proxy ERROR: Table ... does not exist`；
   `1146`）。注入只发生在 `_show_create` 边界，其余全程走真实代码路径（真实连接、真实枚举、
   真实分类、真实产物与落库）。
2. 本轮**未也无法**复现真实 TDSQL 分布式集群的 6000+ 表容量：内网 `15063-sungl_busi`（6097 表）、
   `15064-sungl_am`（2668 表）、`15005-lzbj_ecif` 的**真机复跑仍须内网**。
3. Linux/systemd 部署与回退门禁、CORE_SAFE 制品、断电重入等仍属内网验收活动。

---

## 3. BUG-01 复测：TDSQL 物理子表容错

### 3.1 五场景矩阵（行为级，`probe-bug01-matrix.json`）

| 场景 | 构造 | 期望 | 实测 | 结论 |
|---|---|---|---|---|
| ① 零误杀 | 分布式库，9 对象全部可读（含 2 张 `*_tdsql_subp*` 命名） | 全部提取、无跳过 | 提取 9 / 跳过 0 | ✅ |
| ② 良性跳过 | 对 2 张 `cus_bas_merge_log_tdsql_subp19xxxx` 注入 660（父表存在、分布式） | 良性 2、无逐个块、文件末尾一行汇总 | 良性 2 / 异常 0；`[SKIPPED-SUMMARY]` ✅；逐个块 **0** | ✅ |
| ③ 异常跳过 | 对普通表 `biz_tab_01` 注入 1045（非"对象不存在"） | 判异常、保留逐个溯源块 | 异常 1 / 良性 0；逐个 `[SKIPPED]` 块 **1** | ✅ |
| ④ 集中式门禁 | **集中式**实例 + 命中命名的对象注入 660 | 不得判良性 | 异常 1 / 良性 **0** | ✅ |
| ⑤ 全失败 | 所有对象注入 1045 | 仍 fail-closed 抛错 | `NO_AUDITABLE_OBJECTS` | ✅ |

> 场景①正是 A 在 SIT 里强调的"零误杀硬指标"：**命名像子表但可读的表不得被跳过**，本次独立复现通过。

### 3.2 端到端（`probe-bug01-e2e.json`）

真实 `metadata_audit_worker.run()` 全流程（注入 2 良性 + 1 异常）：

| 项 | 实测 |
|---|---|
| 任务 | `a0278f77…` → **SUCCEEDED**，report_id=**4** |
| 进度 | 枚举 9 / 提取 6 / 审核 6 / **跳过 3（异常 1）** |
| `audit_history` 25 列 | skipped_objects=3 / skipped_benign=2 / skipped_abnormal=1 / omitted_results=0 |
| 产物 `schema.sql` | 含 `[SKIPPED-SUMMARY]` **1 行** + 异常逐个块 **1 个**；`-- SQL Object: CREATE` 6 个 |
| 产物 | manifest.json、results.ndjson（6 行）齐全 |

---

## 4. BUG-02 复测：预检与持久化

### 4.1 预检不再"虚假翻倍"（区分带双向，`probe-bug02-band.json`）

本机实测包限 64 MiB（67,108,864）。构造**恰好落在区分带**的载荷：

| 方向 | 载荷 | ×1.25（新逻辑） | ×2（旧逻辑） | 实测 |
|---|---|---|---|---|
| **应放行** | 53,630,501 B | 67,103,662 < 64 MiB | **107,326,538 > 64 MiB（旧版会误杀）** | ✅ **真实落库成功（report_id=3）** |
| **应拦截** | 55,731,749 B | 69,730,222 > 64 MiB | — | ✅ **友好拦截 `PERSIST_PAYLOAD_TOO_LARGE`**（含中文指引，非裸异常） |

> 这条直接回答"v1.6.3.5 在内网 6097 表库上 342 秒后被自己拦掉"的问题：新版在同样的体量（≈53.6 MB）
> 上真实落库成功。

### 4.2 自适应阈值与超大库压缩（`probe-bug02-compact.json`）

| 项 | 实测 |
|---|---|
| 包限 / 自适应阈值 | 67,108,864 / **53,634,662**（= (包限-64KiB)/1.25，**不再写死 32 MiB**） |
| 载荷 | 56,599,740 B（超阈值） |
| 落库 | **压缩为 list**，70 条（违规 20 全留 + 通过样例 50），`omitted_results=1950` |
| 自洽性 | 70 + 1950 = 2020 = 原记录数 ✅ |
| 列契约 | `pass_rate=80.0` **未被 results_json 污染** ✅；跳过计数 9/7/2 正确落库 |
| 消费方契约 | 压缩后仍是 **JSON list**（既有 5 处消费方按 list 遍历，不被 dict 打崩） |

### 4.3 包限调小后的表现（4 MiB，独立进程；`probe-bug02-packet.json`）

| 场景 | 期望 | 实测 |
|---|---|---|
| A 可压缩到限内 | 压缩落库 | ✅ 载荷 5,709,730 B → 落库 100,830 B，omitted=2950 |
| B 违规过多、压缩后仍超限 | **友好拦截**，不得裸穿 1153/2006 | ✅ `PERSIST_PAYLOAD_TOO_LARGE`（"审核结果转义后约 4,591,853 字节，超过…请评估元数据库容量后重试"） |
| 收尾 | 还原全局包限 | ✅ 已还原 67,108,864 |

> 说明：包限调整后必须**新开进程**才生效（连接池会复用旧会话的包限）——这是本机复测的方法学要点，
> 也是"为什么不能用同进程反复改包限来测"的原因。

---

## 5. 四个呈现面与口径一致性（真实浏览器）

以含跳过的真实任务 `a0278f77…`（report 4：跳过 3 = 良性 2 + 异常 1）与节选任务（report 7：
omitted=1950）为准：

| 呈现面 | 位置 | 实测 |
|---|---|---|
| **P4 运行面板** | 在线元数据审核 → 任务卡「进度」 | `枚举 9 / 提取 6 / 审核 6 / **跳过 3（异常 1）**` ✅ |
| **P3 历史列表** | 历史元数据审核记录 → 「跳过」列 | report 4 → `3 ⚠`；clean 任务 → `-`；report 7 → `9 ⚠` + `节选` 标签 ✅ |
| **P1 历史报告 HTML** | 下载 HTML 报告 | report 4：**红色告警**（1 个对象未读取 DDL）+ KPI「跳过（良性 2 / 异常 1）」；report 7：**橙色"本报告明细为节选"** ✅ |
| **P2 下载 .sql** | ①历史列表「下载 .sql」②任务卡「下载 .sql 文件」 | ①按 results_json 重建并带 `[跳过]`（report 4）/ `[节选]`（report 7）文件头 ✅；②**产物原件**含 `[SKIPPED-SUMMARY]` 1 行 + 异常逐个块 1 个 ✅ |

**口径一致性**：运行面板 3（异常 1）＝ 历史列表 3 ⚠ ＝ 历史报告"共跳过 3、异常 1"＝ 产物汇总行
与逐个块计数，**四处一致**；`audit_history` 25 列回读数值一致。

**一处需向用户说明的差异（非缺陷，建议落文档）**：历史列表的「下载 .sql」是**按审核结果重建**的
语句文件（带 `[跳过]/[节选]` 文件头），任务卡的「下载 .sql 文件」是**产物原件**（含 `[SKIPPED-SUMMARY]`
与逐个跳过块）。两者内容定位不同，建议在用户手册中注明，避免现场误判"下载的文件不一样"。

---

## 6. 回归锁独立变异验证（抽查 5 项，`probe-mutation-1636.json`）

对 A 的关键锁做"回退式变异"，看锁是否真能变红：

| 变异 | 内容 | 结果 |
|---|---|---|
| M1 | 预检恢复 `×2`（BUG-02 本体） | ✅ 变红 |
| M2 | 压缩阈值写死 32 MiB（SIT-D/B-01 本体） | ✅ 变红 |
| M3 | 良性跳过恢复逐个 `[SKIPPED]` 块（R3-B-01 本体） | ✅ 变红 |
| M4 | worker 恢复 `or "distributed"`（R2-M-03 本体） | ✅ 变红 |
| M5 | 去掉 `skipped_list[:50]` 截断 | ✅ 变红 |
| 恢复后 | — | ✅ `test_v1636_bugs.py` **20 passed**；`git status` 产品文件**无改动** |

---

## 7. 全量回归

| 运行 | 结果 |
|---|---|
| v1.6.3.6（专用回归库 `uat_d_1636_regression`，与既往同口径解释器/环境） | **2130 passed / 0 failed / 0 errors / 30 skipped（494.04s）** |

较 v1.6.3.5（2110）多 **20** 项，与 Q 新增的 `tests/test_v1636_bugs.py` 用例数一致（无删除、无跳过新增）。
30 skip 为既定环境性跳过（集成模块需内网凭据），按 R-18 不计入通过。
证据：`data/reports/uat_d_1636/full-regression.xml`。

---

## 8. 新发现问题与照图施工级整改方案

### UAT36-01（中）PUBLISHED 悬挂任务把用户界面永久锁死

**现象（受控复现，`probe-published-orphan.json` + `d36-b7-published-orphan.png`）**

| 步骤 | 观察 |
|---|---|
| 造一个已发布（PUBLISHED / PERSISTING）但未 `complete()` 的任务，并让唯一槽不指向它 | job `ca3b85e8…`，report_id=12，`slot.active_job_id = null` |
| 真实浏览器进入「SQL审核 → 在线元数据审核」 | 任务卡常驻 `状态 PUBLISHED / 阶段 PERSISTING / 已用时长 7s`，文案"任务正在后台执行…" |
| 「🚀 拉取元数据并执行文件审核」 | **disabled = true** |
| 「📄 恢复查看上次任务」 | **disabled = true** |

**用户影响**：该用户既不能发起新审核、也不能切换恢复，页面永久显示"执行中"，
**只能等运维介入**。本轮 b1 场景首次运行时即被此状态挡住（Playwright 30 秒点击超时日志可作旁证），
清理悬挂任务后才恢复正常。

**生产可达路径（代码级）**

1. `backend/workers/metadata_runner.py::_run_job`：子进程退出后读 `final = get_job()`；
   若 `final.state == PUBLISHED` 才调 `complete()`。**runner 进程若在 publish 与 complete 之间死亡**，
   任务永久停在 PUBLISHED。
2. `complete()` 走 `cas_state(... (PUBLISHED,) ...)`，**返回值未被检查**。若 CAS 未命中
   （状态/令牌已变），runner 仍继续执行 `release_slot()` → **槽被释放而任务仍非终态**
   （即本次复现的状态）。若 CAS 未命中且槽未释放，则表现为**新任务永久 409 METADATA_BUSY**。
3. v1.6.3.5 的回收（`reclaim_stale_accepted`）**只覆盖 `ACCEPTED`**，不覆盖
   `PUBLISHING` / `PUBLISHED` 悬挂。
4. 前端 `_recoverActiveJob()` 只要拿到"非终态"任务就设 `extractAuditing=true`，
   而两个按钮都绑在它上面（`拉取` 是 `:loading`、`恢复查看` 是 `:disabled`），**没有任何逃生出口**。

**整改方案（照图施工）**

1. **后端回收扩展到 PUBLISHED/PUBLISHING 悬挂**（`metadata_audit_repository.py` 新增或扩展现有方法，
   仍守 slot→job 锁序）：

   ```python
   def reclaim_stale_unowned(self, accepted_timeout_s: int, publishing_timeout_s: int = 600) -> dict:
       """回收"无主"悬挂任务。返回 {'accepted': [...], 'published': [...]} 供日志/告警。
         · ACCEPTED 且（槽指向它 或 槽空闲）且 age > accepted_timeout_s
             → FAILED / START_TIMEOUT（沿用现有语义）
         · PUBLISHING/PUBLISHED 且 age > publishing_timeout_s（默认 10 分钟，远大于正常发布耗时）
             → report_id 非空 ⇒ SUCCEEDED（成果已落库，属良性收口）/ 否则 FAILED（PERSIST_FAILED）
             → 并且**仅当槽指向它时**释放槽（不抢他人占用）
       所有收敛动作写 finished_at + cleanup_ok=1，并 logger.warning 留痕。"""
   ```

   **安全性**：与 D-02 同理——`claim_next_accepted()` 只从槽取任务，故"槽未指向它"的悬挂任务
   在任何时刻都不可能被认领；`PUBLISHING` 超时阈值取 10 分钟，远大于正常发布（本机 < 1s，内网
   6097 表 < 1 分钟），不会误伤在跑任务。
2. **runner 不得"静默放槽"**：`_run_job` 中把

   ```python
   elif final and final.get("state") == R.STATE_PUBLISHED:
       self.repo.complete(job_id, token, exit_code=res.returncode or 0, cleanup_ok=res.cleanup_ok)
   ```

   改为**检查返回值**：未命中时进入 `RECOVERY_REQUIRED`（现有 `_mark_recovery`）而**不**执行
   `release_slot()`，使"任务非终态 + 槽空闲"这一不一致组合不再产生。
3. **前端加逃生出口**（两条，成本都低）：
   - 服务端 `_job_summary` 增加 `slot_owned: bool`（`slot.active_job_id == job_id`）；
   - `_recoverActiveJob()` 仅在 `slot_owned === true` 时才把任务视为活动（设 `extractAuditing=true`），
     否则展示任务卡但**保持两个按钮可用**，并提示"该任务已不在执行器上运行，可重新发起或联系运维"。
4. **回归锁**（`tests/test_v1636_bugs.py` 或新文件）：
   - `test_reclaim_published_orphan_converges`：造 PUBLISHED 超时任务 → 回收后为 SUCCEEDED 且 `finished_at` 非空；
   - `test_reclaim_publishing_without_report_fails`：PUBLISHING 超时且无 report_id → FAILED/PERSIST_FAILED；
   - `test_complete_miss_does_not_release_slot`：令 `complete()` 返回 False → 断言槽**未**被释放、
     状态为 RECOVERY_REQUIRED（防"静默放槽"回归）；
   - `test_job_summary_exposes_slot_owned`（后端）+ 前端契约锁（恢复逻辑读 `slot_owned`）。
5. **运维只读自检**（发布后巡检，不改数据）：

   ```sql
   -- 非终态任务与其槽归属（槽为空即为 UAT36-01 命中态）
   SELECT j.id, j.state, j.phase, j.created_at, s.active_job_id
     FROM metadata_audit_jobs j LEFT JOIN metadata_audit_slot s ON s.id = 1
    WHERE j.state NOT IN ('SUCCEEDED','FAILED','CANCELLED') ORDER BY j.created_at;
   ```

**定级说明**：判为**中**而非阻断——需要"runner 在 publish 与 complete 之间死亡"或
"complete() 未命中"这类窄窗口，且只影响在线元数据审核一个页面；但一旦命中，用户侧无自救手段，
且与 v1.6.3.5 遗留的 D-04（幽灵 ACCEPTED 锁死界面）同源，**建议与 D-04 合并一次修完**。

---

## 9. 证据索引

目录 `docs/evidence/v1.6.3.6-uat-d/`：

| 类别 | 文件 |
|---|---|
| 夹具/驱动 | `local_harness_d36.py`、`uat36_scenarios_d.py`、`browser_uat36_d.py`、`probe_mutation_1636_d.py`、`probe_published_orphan_d36.py`、`cleanup_stuck_d36.py`、`reset_slot_d36.py`、`diag_jobs_d36.py`、`prepare_regression_d36.py` |
| BUG-01 | `probe-bug01-matrix.json`、`probe-bug01-e2e.json` |
| BUG-02 | `probe-bug02-band.json`、`probe-bug02-compact.json`、`probe-bug02-packet.json` |
| 呈现面 | `b1_normal_flow.json`、`b2_running_panel.json`、`b3_history_list.json`、`b4_history_html.json`、`b5_sql_download.json`、`b6_errors.json`、`d36-skip_report.html/.sql`、`d36-omitted_report.html/.sql`、`d36-artifact-skip.sql/.html` |
| 新发现 | `probe-published-orphan.json`、`d36-b7-published-orphan.png` |
| 变异验证 | `probe-mutation-1636.json` |
| 回归 | `data/reports/uat_d_1636/full-regression.xml` |
| 截图 | `d36-b1-*.png`、`d36-b2-running-panel.png`、`d36-b3-history-list.png`、`d36-b5-downloads.png`、`d36-b6-offline-error.png`、`d36-b7-published-orphan.png` |

---

## 10. 放行意见

1. **本次两个缺陷的修复，UAT 侧准出**：BUG-01 五场景 + 端到端全通过；BUG-02 区分带双向、
   自适应阈值、压缩自洽、包限调小后的表现全部通过；四个呈现面口径一致；回归零失败零错误；
   锁经独立变异抽查 5/5 有效。
2. **UAT36-01 建议在准出前评估**：若时间允许，请 Q 按 §8 一并整改（含 v1.6.3.5 遗留的 D-04），
   成本可控（1 个回收分支 + runner 返回值检查 + 前端 `slot_owned` 判定 + 4 条锁）；若不整改，
   请写入发布说明"已知问题"并附运维自检 SQL。
3. **内网真机复跑仍不可替代**：请 G/内网智能体在 v1.6.3.6 部署后，用原三个库
   （`15005-lzbj_ecif` 219 真实表 + 大量物理子表、`15063-sungl_busi` 6097 表、`15064-sungl_am` 2668 表）
   复跑并核对：任务终态、跳过计数与四处口径、`.sql`/HTML 下载内容、耗时与 RSS。

---

测试责任方：**智能体D**
提交给：Mr.Linsang
