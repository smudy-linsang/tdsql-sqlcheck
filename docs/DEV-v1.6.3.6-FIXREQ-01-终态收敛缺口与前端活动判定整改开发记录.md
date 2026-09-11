# v1.6.3.6 补丁（FIXREQ-v1.6.3.6-01）· 终态收敛缺口与前端「活动任务」判定整改开发记录

| 项 | 内容 |
|---|---|
| 版本 | v1.6.3.6 补丁（FIXREQ-v1.6.3.6-01；**不升版号**，作为 v1.6.3.6 UAT 缺陷收尾） |
| 工单 | `FIXREQ-v1.6.3.6-01`（UAT36-01 + D-04 合并单） |
| 依据 | `docs/UAT-v1.6.3.6-内网大库问题修复-用户验收测试报告-D.md` §8<br>`docs/FIXREQ-v1.6.3.6-01-终态收敛缺口与前端活动判定-UAT36-01与D-04-智能体D.md` |
| 提出方 | 智能体D（UAT / 独立验证侧） |
| 施工方 | 智能体Q（全栈开发） |
| 施工日期 | 2026-09-11 |
| 代码基线 | `main@5521267`（A 第四轮 SIT 放行、D UAT 有条件准出版本） |

---

## 背景

D 第一轮 UAT：BUG-01 / BUG-02 的修复在功能、呈现面、锁有效性三层全部复测通过，全量回归
2130 passed / 0 failed；另发现 1 项新中等缺陷 **UAT36-01**（PUBLISHED 悬挂任务把用户界面永久
锁死），与 v1.6.3.5 遗留的 **D-04**（幽灵 ACCEPTED 锁死界面）同源，合并为 `FIXREQ-v1.6.3.6-01`。

**共同症状**：在线元数据审核页常驻一张"执行中"任务卡，「🚀 拉取元数据并执行文件审核」与
「📄 恢复查看上次任务」两个按钮同时变灰，用户无自救手段，只能等运维介入。

**共同根因**：① 终态收敛缺口——旧 `reclaim_stale_accepted` 只覆盖 `ACCEPTED` 且只"从槽出发"
（槽为空或指向他人就直接 return）；② runner 静默放槽——`_run_job` 在 `complete()` 返回值未检查
的情况下继续 `release_slot()`，亲手制造"槽已释放、任务非终态"的不一致；③ 前端把"非终态"
直接等同"活动"，两个按钮都绑在 `extractAuditing` 上，没有逃生出口。

---

## 逐项整改（照 FIXREQ §2 施工）

| 编号 | 整改 |
|---|---|
| §2.1 后端-1 | `metadata_audit_repository` 新增 `reclaim_stale_unowned(accepted_timeout_s, publishing_timeout_s=600)`：**扫描全部非终态任务**（不再只从槽出发），A) `ACCEPTED` 且 age(created_at) 超时且（槽指向它 **或** 槽为空）→ `FAILED/START_TIMEOUT`；B) `PUBLISHING/PUBLISHED` 且 age(updated_at) 超时 → report_id 非空判 `SUCCEEDED/DONE`、为空判 `FAILED/PERSIST_TIMEOUT`；两者均**仅当槽指向它时**释放槽（不抢他人占用）。`reclaim_stale_accepted(n)` 保留为薄包装（向后兼容既有 5 条用例）。`MetadataLimits` 新增 `PUBLISH_TIMEOUT`（env `METADATA_PUBLISH_TIMEOUT_SECONDS`，默认 600，范围 60–3600，`validate_limits` fail-closed 校验）。`create_job` 受理前 + `runner._tick` 每轮两个自愈调用点改调新方法 |
| §2.1b 决策 | `RUNNING` 悬挂**不自动收敛**（Mr.Linsang 2026-09-11 采纳 D 建议：runner 崩溃后 child 可能仍在写产物，自动判失败会误杀在跑任务并产生"任务已失败但产物仍在写"的二次不一致）。`reclaim_stale_unowned` 不处理 `RUNNING/STOPPING/RECOVERY_REQUIRED`；RUNNING 悬挂仅由 `_job_summary.stale_running` 暴露供运维判读 |
| §2.2 后端-2 | `metadata_runner._run_job`：① `complete()` 返回值检查（`not ok` → `logger.error` + `_mark_recovery` + `return`，不放槽）；② `release_slot` 前补通用终态护栏（`cur.state not in TERMINAL_STATES` → `_mark_recovery` + `return`）。杜绝"槽已释放、任务非终态"这一 UAT36-01/D-04 的产生源 |
| §2.3 后端-3 | `api/metadata_audit.py::_job_summary(job, slot_active_job_id=None)` 新增 `slot_owned`（`slot.active_job_id == job.id`）；`create/get/list` 三个调用点各读一次 `slot_state()` 后统一传入（不逐条查库）。只新增字段，既有字段语义不变 |
| §2.1b+2.3 | `_job_summary` 新增 `stale_running`（`state=='RUNNING'` 且 `heartbeat_at` 超 `JOB_TIMEOUT`，复用共享 `is_fresh_heartbeat`，缺失/过期/未来一律 fail-closed 判 stale） |
| §2.4 前端-4 | `app.js` 新增 `META_ACTIVE_STATES` + `_isReallyActive(d)`（state ∈ ACTIVE 且 `slot_owned!==false` 且 `!stale_running`）；`_recoverActiveJob` 非终态分支改造：仅真活动才 `extractAuditing=true` 续轮询，`PUBLISHED`/悬挂残留/`stale_running` 三态**照常展示任务卡但保持两按钮可用**并给对应 warning 提示。新增 `metadataRecovering` 独立禁用源，恢复按钮改绑它（L8：两按钮不再共用 `extractAuditing`）。`index.html` 任务卡加 `stale_running`/悬挂两条告警行，"正在后台执行"灰字限定为真活动 |

---

## 回归锁 L1–L10（`tests/test_v1637_fixreq01.py`，10 用例全过）

| # | 用例 | 断言要点 |
|---|---|---|
| L1 | `test_reclaim_published_orphan_converges` | PUBLISHED 悬挂(有 report)→ SUCCEEDED + finished_at 非空 + 释放槽 |
| L2 | `test_reclaim_publishing_without_report_fails` | PUBLISHING 悬挂(无 report)→ FAILED/PERSIST_TIMEOUT |
| L3 | `test_reclaim_accepted_unowned_still_works` | ACCEPTED + 槽空(D-04)→ 仍收敛(不因槽空跳过) |
| L4 | `test_reclaim_does_not_touch_slot_owned_by_other` | 槽指向他人时 orphan 不动(不改状态/不放槽) |
| L5 | `test_reclaim_skips_fresh_published` | PUBLISHED 未超时 → 不动作 |
| L6 | `test_complete_miss_does_not_release_slot` | complete() 未命中 → 不释放槽 + RECOVERY_REQUIRED |
| L7 | `test_job_summary_exposes_slot_owned` | 槽指向它→true；槽空/指向他人→false |
| L8 | `test_recover_treats_unowned_as_inactive` | 前端契约：活动判定看 state+slot_owned+stale_running，两按钮不共用禁用源 |
| L9 | `test_job_summary_exposes_stale_running` | RUNNING+心跳超时→true；心跳新鲜/无心跳 fail-closed/非 RUNNING→对应值 |
| L10 | `test_reclaim_never_touches_running` | §2.1b 决策锁：RUNNING 即使超期极久也不收敛、槽不动 |

---

## 变异自证（FIXREQ §4 硬要求）

`scratch/mutation_selfcheck_v1637.py` 对 **12 个变异体**（L1–L10，含 L8a/L8b、L9a/L9b）逐条
「注入缺陷 → 确认对应用例变红 → 立即原地恢复」（正向/反向原地替换、保持各文件原始换行、
源文件字节级恢复校验一致）：**KILLED 12/12**。

**L10 首轮 SURVIVED（假绿），已修正**：原变异只把 A 分支判定 `if state==STATE_ACCEPTED` 改成
含 `RUNNING`，但 A 分支 UPDATE 的 `WHERE id=? AND state=?`（硬编码 `STATE_ACCEPTED`）这道 CAS
二次防护把 RUNNING 中和了（UPDATE 匹配 0 行 → continue），变异并未真正引入"RUNNING 被回收"的
缺陷，故 L10 仍绿。修正变异为**同时**改判定 + UPDATE 的 WHERE（用实际 `state`）后 L10 被杀死。
这再次印证 A 三轮强调的纪律：变异必须真正注入缺陷、确认变红，锁才算写完。

> 说明：§2.2 的 complete() 返回值检查与终态护栏对"PUBLISHED + complete 失败"场景是纵深防御
> （去其一另一兜底），故 L6 变异同时注入两处；终态护栏另独立覆盖 complete 之外的非终态放槽路径。

---

## 验证

- `test_v1637_fixreq01.py` **10 passed**；`test_v1635_uat3.py` **14 passed**（D-02 两条接线锁
  同步把方法名 `reclaim_stale_accepted`→`reclaim_stale_unowned`，意图不变）；`test_v1636_bugs.py` **20 passed**。
- `node --check frontend/static/js/app.js` 通过；全部改动 `py_compile` 通过。
- **全量受控回归**（同一 docker MySQL 8.0 / `max_allowed_packet=64MiB` 环境）：

  | | failed | passed | errors | skipped |
  |---|---|---|---|---|
  | 基线 5521267 | 419 | 1596 | 114 | 31 |
  | head（本次整改） | 419 | **1606 (+10)** | 114 | 31 |

  failed / errors / skipped 计数**完全一致**，passed **+10** 恰为新增 `test_v1637_fixreq01.py`
  的 10 把锁 → **零新增失败**。419 failed / 114 errors 为环境性（本地 docker 空库缺 RBAC 用户、
  auth secret、慢查询 monitordb 数据，集中在 test_v2_uat、test_v3_rbac_instances、
  test_raw_slowlog_integration 等需完整部署的集成套件），与本次改动无关。

---

## 版本标记

本整改作为 **v1.6.3.6 的补丁**，`VERSION`、`config.APP_VERSION`、`index.html` 5 处版本标记
**保持 `1.6.3.6` 不变**（与 A 第四轮 SIT 准出、D UAT 验收、内网即将上线的版本链路一致）。

> 施工时曾一度按 FIXREQ §7.2 的提交信息模板把版本标记升至 `1.6.3.7`（提交 17a2ad2）；经
> Mr.Linsang 确认——本次是 v1.6.3.6 在 UAT 暴露缺陷的收尾，应作为 **v1.6.3.6 补丁**而非新版——
> 已回退全部版本标记（见紧随其后的回退提交）。`index.html:396` 的 `FIXREQ-v1.6.3.6-01`
> 工单号注释始终保留不改（工单号是历史标识，非版本标记）。

---

## 边界声明（遵循 FIXREQ §3 / §6）

- **不新增表 / 迁移**；只**新增** API 字段（`slot_owned` / `stale_running`），既有字段与状态机语义不变。
- **存量悬挂数据不回填、不伪造**：升级后由 `reclaim_stale_unowned` 按时间自然收敛（新请求受理前
  自愈 + runner 每轮回收）；`RUNNING` 悬挂仅暴露 `stale_running`，须人工核实 child 是否仍在运行后
  再决定收敛，不在无核实下自动改状态或清 `active_job_id`。
- **不动** BUG-01/BUG-02 已复测通过的提取与持久化逻辑；**不改**其它模块（网关/慢 SQL/大表/巡检）；
  部署脚本无需改动（本单不涉及新服务/新配置）。
- 内网真机复跑（`15063-sungl_busi` 6097 表、`15064-sungl_am` 2668 表、`15005-lzbj_ecif`）+
  Linux/systemd 部署与回退门禁验证仍待 G / 内网智能体组织。

---

施工人：智能体 Q
施工对象：v1.6.3.6 补丁（FIXREQ-v1.6.3.6-01：UAT36-01 + D-04 终态收敛缺口与前端活动判定整改）
提交给：Mr.Linsang
