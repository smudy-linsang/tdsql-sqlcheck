# FIXREQ-v1.6.3.6-01 终态收敛缺口与前端「活动任务」判定整改要求（UAT36-01 + D-04）

| 项 | 内容 |
|---|---|
| 工单号 | `FIXREQ-v1.6.3.6-01` |
| 提出方 | **智能体D**（UAT / 独立验证侧） |
| 施工方 | **智能体Q**（全栈开发） |
| 依据文档 | `docs/UAT-v1.6.3.6-内网大库问题修复-用户验收测试报告-D.md` §8（UAT36-01）<br>`docs/RETEST-v1.6.3.5-第三轮UAT整改复测报告-D.md` §6.1（D-04） |
| 缺陷数 | **2 项合并为一单**（同一根因，见 §0） |
| 代码基线 | `main@50d1d76`（v1.6.3.6 定版；本文所有行号均按此提交核对） |
| 版本归属 | **属 v1.6.3.6 版本内的整改**——v1.6.3.6 仍处开发测试阶段，本工单不单独升版、不称"补丁"，与其它整改同属 v1.6.3.6 交付内容 |
| 阻断性 | 不阻断 v1.6.3.6 对 BUG-01/BUG-02 的修复结论（那两项 UAT 已复测通过）；但两项缺陷**命中后用户无自救手段**，建议随下一版一并修完 |
| 提交给 | Mr.Linsang（转 Q 施工） |

---

## 0. 为什么合并成一单

两项缺陷的**用户可见症状完全相同**——在线元数据审核页出现一张常驻"执行中"的任务卡，
「🚀 拉取元数据并执行文件审核」与「📄 恢复查看上次任务」**两个按钮同时变灰，用户无法自救**——
根因也相同：

| 共同根因 | 说明 |
|---|---|
| ① **终态收敛缺口** | 任务在"非终态 + 唯一槽已不指向它"时没有任何回收方；现有 `reclaim_stale_accepted()` 只覆盖 `ACCEPTED` 一种状态 |
| ② **runner 静默放槽** | `metadata_runner._run_job` 在 `complete()` **返回值未检查**的情况下继续执行 `release_slot()`，会亲手制造"槽已释放、任务非终态"的不一致 |
| ③ **前端把"非终态"等同于"活动"** | `_recoverActiveJob()` 只要拿到非终态任务就设 `extractAuditing=true`，而两个按钮都绑在该标志上，没有逃生出口 |

D-04 是 `ACCEPTED` 形态，UAT36-01 是 `PUBLISHING/PUBLISHED` 形态；分开修会出现"按住葫芦起了瓢"，
故一次性收敛。

---

## 1. 缺陷清单

### 1.1 UAT36-01（中）PUBLISHED 悬挂任务锁死前端

**现象（受控复现，已取证）**：任务停在 `PUBLISHED / PERSISTING`、唯一槽不指向它时，
进入「SQL审核 → 在线元数据审核」：

| 观察 | 实测值 |
|---|---|
| 任务卡 | `元数据审核任务 #ca3b85e8`、`状态 PUBLISHED`、`阶段 PERSISTING`、`已用时长 7s`、文案"任务正在后台执行…" |
| 「拉取元数据并执行文件审核」 | `disabled = true` |
| 「恢复查看上次任务」 | `disabled = true` |

**证据**：`docs/evidence/v1.6.3.6-uat-d/probe-published-orphan.json`、
`docs/evidence/v1.6.3.6-uat-d/d36-b7-published-orphan.png`；
旁证：同轮 b1 场景首跑被此状态挡住（Playwright 30 秒点击超时，`b1_normal_flow` 未落盘），
清理悬挂任务后立即恢复正常。

**生产可达路径（代码级，行号按 `50d1d76`）**

1. `backend/workers/metadata_runner.py:119` `final = self.repo.get_job(job_id)` →
   `:136` 只有 `final.state == PUBLISHED` 才调 `complete()`。
   **runner 进程在 child publish 之后、执行本行之前死亡** → 任务永久停在 `PUBLISHED`。
2. `backend/workers/metadata_runner.py:137` 调用 `self.repo.complete(...)`，**返回值被丢弃**；
   `complete()`（`metadata_audit_repository.py:507`）走 `cas_state(... (PUBLISHED,) ...)`，
   CAS 未命中时返回 `False`，但 `:162` 仍执行 `self.repo.release_slot(job_id)`
   → **槽被释放而任务仍非终态**，正是本次复现的状态。
3. 若 CAS 未命中且槽未释放（runner 崩溃分支），用户侧表现为**新任务永久 `409 METADATA_BUSY`**。
4. `reclaim_stale_accepted()`（`metadata_audit_repository.py:537`）只处理 `state == 'ACCEPTED'`。

### 1.2 D-04（低→中）幽灵 ACCEPTED 任务锁死前端

**现象**：`state='ACCEPTED'` 但 `slot.active_job_id` 已不指向它时，页面同样出现常驻"执行中"卡片
且两个按钮同时禁用。

**证据**：`docs/evidence/v1.6.3.5-uat3-d/s17_phantom_accepted.json`、`d-s17-phantom-accepted.png`；
代码事实复现：`docs/evidence/v1.6.3.5-uat3-d/probe_stale_reclaim_d.py w3`（返回 `None`，未回收）。

**可达路径**：与 1.1 的第 2 条同源（`release_slot()` 先于/独立于终态收敛被调用），
以及升级前历史版本残留（v1.6.3.5 之前无任何回收机制）。

### 1.3 前端侧共同缺陷

| 位置 | 现状 | 问题 |
|---|---|---|
| `frontend/static/js/app.js:1728` `_recoverActiveJob()` | 非终态 → `metadataJob=d; extractAuditing.value=true`（`:1752`、`:1759`） | 把"服务端有非终态记录"直接当成"执行器正在跑" |
| `frontend/index.html:366` 拉取按钮 | `:loading="extractAuditing"` | 与恢复按钮共用同一标志，无逃生出口 |
| `frontend/index.html:368` 恢复按钮 | `:disabled="extractAuditing"` | 同上 |

---

## 2. 整改方案（照图施工）

> 施工顺序建议：2.1 → 2.2 → 2.3 → 2.4；每步完成即跑该步的锁，最后统一跑全量回归。
> 约束：**不新增数据表**、不改动既有 API 契约字段（只**新增**字段）、不改其它模块。

### 2.1 后端-1：回收扩展为「无主悬挂任务」统一收敛

**位置**：`backend/services/metadata_audit_repository.py`（在 `reclaim_stale_accepted` 之后新增；
**保留** `reclaim_stale_accepted(n)` 作为薄包装，避免打破既有 5 条用例与两个调用点）。

```python
def reclaim_stale_unowned(self, accepted_timeout_s: int,
                          publishing_timeout_s: int = 600) -> dict:
    """收敛"无主悬挂"任务。返回 {'accepted': [...], 'published': [...]} 供日志/告警。

    判定与动作（仍守 slot → job 锁序；全部动作写 finished_at + cleanup_ok=1 并 warning 留痕）：
      A) state='ACCEPTED' 且 age(created_at) > accepted_timeout_s
           且（slot.active_job_id == job.id 或 slot.active_job_id IS NULL）
         → FAILED / START_TIMEOUT（沿用现有语义）
         → 仅当槽指向它时释放槽
      B) state IN ('PUBLISHING','PUBLISHED') 且 age(updated_at) > publishing_timeout_s
         · report_id 非空 → SUCCEEDED / phase=DONE（成果已落库，属良性收口）
         · report_id 为空 → FAILED / PERSIST_TIMEOUT（发布事务未提交）
         → 仅当槽指向它时释放槽
    安全性：claim_next_accepted() 只从槽取任务，故"槽未指向它"的悬挂任务在任何时刻
    都不可能被认领；publishing_timeout_s 默认 600s 远大于正常发布耗时（本机 <1s、
    内网 6097 表 <1min），不会误伤在跑任务。
    """
```

- 参数入 `backend/services/metadata_job_process.py::MetadataLimits`：
  `PUBLISH_TIMEOUT = _env_int("METADATA_PUBLISH_TIMEOUT_SECONDS", 600)`，范围校验 `60 ≤ v ≤ 3600`
  （与既有 `validate_limits()` 同风格，越界 fail-closed）。
- 两个既有调用点改为调用新方法（行为向后兼容）：
  - `backend/services/metadata_audit_repository.py::create_job`（受理前自愈，现调用 `reclaim_stale_accepted`）
  - `backend/workers/metadata_runner.py::_tick`（每轮回收）

### 2.1b `RUNNING` 悬挂的处置——**决策已定：不自动 FAIL**

> **决策记录**：Mr.Linsang 于 2026-09-11 采纳 D 的建议——`RUNNING` 悬挂**不纳入自动回收**。
> 理由：runner 崩溃后 child 可能仍在写产物，自动判失败会误杀在跑任务并产生"任务已失败但产物仍在写"的二次不一致。

因此本轮**只做可观测、不做自动收敛**：

1. **不自动 FAIL、不自动释放槽**：`reclaim_stale_unowned()` **不得**处理 `state='RUNNING'`。
2. **必须可观测**：`backend/api/metadata_audit.py::_job_summary` 新增字段

   ```python
   "stale_running": bool,   # state=='RUNNING' 且 runner 心跳(heartbeat_at)距今 > METADATA_JOB_TIMEOUT_SECONDS
   ```

   口径说明：以 `metadata_audit_jobs.heartbeat_at`（runner 监督心跳，v1.6.3.5 起每 2 秒刷新）为判据，
   阈值复用 `MetadataLimits.JOB_TIMEOUT`（默认 1800s）；阈值可通过既有环境变量调整，**不新增参数**。
   与 `slot_owned` 一样，批量接口只读一次槽/一次心跳基准，不逐条查库。
3. **前端提示（与 §2.4 合并实现）**：`stale_running === true` 时在任务卡下追加一行提示
   `该任务长时间无执行器心跳，可能已中断；请联系运维核实后再重新发起`，**按钮保持可用**。
4. **运维处置**（人工，不写自动化）：沿用既有 `RECOVERY_REQUIRED` 语义或按 §3 自检 SQL 判读后人工收敛。


### 2.2 后端-2：runner 不得"静默放槽"

**位置**：`backend/workers/metadata_runner.py:136-138` 与 `:162`

```python
elif final and final.get("state") == R.STATE_PUBLISHED:
    ok = self.repo.complete(job_id, token, exit_code=res.returncode or 0,
                            cleanup_ok=res.cleanup_ok)
    if not ok:                      # 新增：CAS 未命中不得静默继续
        logger.error("complete() 未命中，任务仍未收敛 job=%s state=%s",
                     job_id, (self.repo.get_job(job_id) or {}).get("state"))
        self._mark_recovery(job_id, token)
        return                      # RECOVERY_REQUIRED 不释放槽位
```

并在 `:162` 之前补一道通用护栏：

```python
cur = self.repo.get_job(job_id) or {}
if cur.get("state") not in R.TERMINAL_STATES:
    logger.error("任务非终态却准备释放槽位，转入 RECOVERY_REQUIRED job=%s state=%s",
                 job_id, cur.get("state"))
    self._mark_recovery(job_id, token)
    return
self.repo.release_slot(job_id)
```

> 这两处是"不再产生新的不一致状态"的关键；缺了它们，2.1 的回收只能事后补救。

### 2.3 后端-3：状态接口暴露槽归属

**位置**：`backend/api/metadata_audit.py::_job_summary`（`:159`）

- 新增字段 `"slot_owned": bool`（含义：`metadata_audit_slot.active_job_id == job["id"]`）。
- **实现要求**：`slot_owned` 不要每条任务都查一次库。改为 `_job_summary(job, slot_active_job_id=None)`
  传入一次读到的槽值；`create/get/list` 三个调用点各读一次槽（`repo.slot_state()`）后统一传入。
- 兼容性：**只新增字段**，既有字段与语义不变（前端已用可选链，向后安全）。

### 2.4 前端-4：活动判定与逃生出口

**位置**：`frontend/static/js/app.js` `_recoverActiveJob()`（`:1728`）与
`frontend/index.html`（`:366`/`:368`）

```js
// 服务端明确告知槽归属；slot_owned===false 的非终态任务＝悬挂残留，不得判定为"正在执行"
const ACTIVE_STATES = ['ACCEPTED','RUNNING','PUBLISHING','STOPPING'];
const _isReallyActive = (d) => ACTIVE_STATES.includes(d.state) && d.slot_owned !== false;
```

- `_recoverActiveJob()`：仅当 `_isReallyActive(d)` 时 `extractAuditing.value=true` 并续轮询；
  否则**照常展示任务卡**（让用户看到发生了什么），但**保持两个按钮可用**，并提示：
  `ElementPlus.ElMessage.warning('该任务已不在执行器上运行（可能为中断残留），可重新发起或联系运维')`。
- 对 `PUBLISHED`（既非 ACTIVE 也未终态）单独文案：`'结果已落库但未收口，可下载报告；如需重扫请直接重新发起'`。
- 页面自动恢复（`onMenuSelect('schema-extractor-audit')` 触发的那次）与手动「恢复查看上次任务」
  必须走同一判定函数，避免两处逻辑分叉。

---

## 3. 存量数据处置（遵循 R-06 / R-13：不回填、不伪造）

1. **不写数据迁移、不回填历史**：悬挂态的"真实完成时间"不可考，回填等于伪造确定性。
2. 升级后由 §2.1 的回收逻辑**按时间自然收敛**（新请求自愈 + runner 每轮回收）。
3. 发布说明附**运维只读自检 SQL**（仅供判读，不自动改数据）：

```sql
-- ① 非终态任务与其槽归属；slot为NULL即命中 UAT36-01/D-04 形态
SELECT j.id, j.state, j.phase, j.created_at, j.updated_at, s.active_job_id
  FROM metadata_audit_jobs j LEFT JOIN metadata_audit_slot s ON s.id = 1
 WHERE j.state NOT IN ('SUCCEEDED','FAILED','CANCELLED') ORDER BY j.created_at;

-- ② RUNNING 悬挂判读（§2.1b：只判读、不自动处理）
SELECT id, created_at, heartbeat_at, progress_at,
       TIMESTAMPDIFF(SECOND, heartbeat_at, UTC_TIMESTAMP(6)) AS hb_age_s
  FROM metadata_audit_jobs
 WHERE state = 'RUNNING'
   AND (heartbeat_at IS NULL OR heartbeat_at < UTC_TIMESTAMP(6) - INTERVAL 1800 SECOND);
```

处置口径：升级到修复版本后**无需人工干预**（PUBLISHED/PUBLISHING 与无主 ACCEPTED 会被自动收敛）；
`RUNNING` 悬挂请人工核实 child 是否仍在运行后再决定收敛，**不要**在无核实的情况下直接改状态或清 `active_job_id`。
若旧版本现场需立即恢复，可手工把该行置 `FAILED`/`SUCCEEDED`（保留 `finished_at`），**不要**直接清
`active_job_id`（会掩盖占用）。

---

## 4. 回归锁清单（每条都必须能杀死对应变异）

> 施工规约：每条锁先注入缺陷确认变红、再恢复确认变绿（本项目四轮 SIT 的既定纪律）。

| # | 用例名（建议） | 断言要点 | 必须杀死的变异 |
|---|---|---|---|
| L1 | `test_reclaim_published_orphan_converges` | 造 `PUBLISHED` 且 `updated_at` 超时的任务 → 回收后 `SUCCEEDED`、`finished_at` 非空、槽释放 | 回收分支去掉 `PUBLISHED` 处理 |
| L2 | `test_reclaim_publishing_without_report_fails` | `PUBLISHING` 超时且 `report_id` 为空 → `FAILED` / `PERSIST_TIMEOUT` | 把无 report 也判 `SUCCEEDED` |
| L3 | `test_reclaim_accepted_unowned_still_works` | 覆盖 D-04：`ACCEPTED` + 槽为空 + 超时 → 收敛（不得因槽为空而跳过） | 条件写成"必须 `slot.active_job_id == job.id`" |
| L4 | `test_reclaim_does_not_touch_slot_owned_by_other` | 槽指向别的 job 时，不得释放槽、不得改其状态 | 释放槽时去掉 `AND active_job_id=?` |
| L5 | `test_reclaim_skips_fresh_published` | `PUBLISHED` 但未超时（如 5s）→ 不动作 | 阈值判定写成恒真 |
| L6 | `test_complete_miss_does_not_release_slot` | 令 `complete()` 返回 `False`（fake/真实 CAS 失败）→ 断言槽**未**释放、任务为 `RECOVERY_REQUIRED` | 去掉返回值检查（`ok`）或去掉终态护栏 |
| L7 | `test_job_summary_exposes_slot_owned` | 后端：槽指向该任务 → `slot_owned=true`；槽为空/指向他人 → `false` | 把 `slot_owned` 写成常量 |
| L8 | 前端契约锁 `test_recover_treats_unowned_as_inactive` | `app.js` 中活动判定必须同时看 `state` 与 `slot_owned`，且两个按钮不再共用同一禁用源 | 判定退回"仅看 state"；两按钮仍绑 `extractAuditing` |
| L9 | `test_job_summary_exposes_stale_running` | `RUNNING` 且 `heartbeat_at` 超 `JOB_TIMEOUT` → `stale_running=true`；心跳新鲜或非 `RUNNING` → `false` | 把 `stale_running` 写成常量；阈值写反（新鲜判 stale） |
| L10 | `test_reclaim_never_touches_running` | §2.1b 决策锁：`RUNNING` 任务即使超期极久，回收也必须返回"未处理"、状态不变、槽不动 | 把 `RUNNING` 也纳入回收分支 |

**变异自证要求**：至少覆盖上表"必须杀死的变异"列，并像 R3/SIT4 那样给出"注入→变红→恢复→变绿"
的完整记录（含恢复后 `git status` 产品文件无差异）。

---

## 5. 验收判据与复测命令（D 侧将按此复测）

| 判据 | 复测方式 |
|---|---|
| 悬挂任务不再锁死界面 | 复用 `docs/evidence/v1.6.3.6-uat-d/probe_published_orphan_d36.py`：造 PUBLISHED 悬挂 → 进入页面 → **两个按钮均可用**且出现"非执行器上运行"提示 |
| D-04 形态同样不再锁死 | 复用 `docs/evidence/v1.6.3.5-uat3-d/probe_stale_reclaim_d.py w3` → 期望返回被回收的 job_id，且页面按钮可用 |
| 正常任务不受影响 | `docs/evidence/v1.6.3.6-uat-d/browser_uat36_d.py b1_normal_flow`（提交→SUCCEEDED→分页） |
| 自愈不误伤 | 6 条后端锁 + 全量回归 `0 failed / 0 errors` |
| 无新增不一致 | 连续跑 3 个任务后执行 §3 自检 SQL，非终态任务应为 0 |
| `RUNNING` 悬挂"只暴露不自动收敛" | 造 `RUNNING` + 心跳超时任务：页面出现"长时间无执行器心跳"提示**且按钮可用**；回收接口对该任务**不动作**（状态/槽均不变，由 L10 锁住） |

---

## 6. 明确不做（防范围扩展）

1. **不改** 在线元数据审核的状态机语义与既有 API 字段（只新增 `slot_owned`）。
2. **不新增** 数据表/迁移（本轮不需要）。
3. **不动** BUG-01/BUG-02 已复测通过的提取与持久化逻辑。
4. **不自动处理** `RUNNING` 悬挂（已决策，见 §2.1b）：只暴露 `stale_running` 供运维判读，
   不自动 FAIL、不自动释放槽。
5. **不改** 其它模块（网关/慢 SQL/大表/巡检）任何代码。
6. 部署脚本无需改动（本单不涉及新服务/新配置）。

---

## 7. 交付与流程

1. Q 施工完成后：跑 `tests/test_v1636_bugs.py` + 新增锁 + 全量回归，提交**先提交推送再汇报**。
2. 提交信息建议：`fix(v1.6.3.6): 终态收敛缺口整改 - 无主悬挂回收+runner不放槽+前端活动判定(FIXREQ-v1.6.3.6-01)`。
3. 完成后通知 **智能体D** 复测（本单 §5 判据 + §4 变异抽查）；复测通过后再由 A 出 SIT 复核意见。
4. 若时间不允许本轮整改：请把 §1 两项写入**发布说明「已知问题」**并附 §3 的只读自检 SQL。

---

提出方：**智能体D**
施工方：智能体Q
提交给：Mr.Linsang
