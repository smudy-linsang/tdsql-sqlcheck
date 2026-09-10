# v1.6.3.5 第三轮 UAT 整改复测报告（D-01 / D-02 / D-03）—— 智能体D

| 项 | 内容 |
|---|---|
| 产品版本 | v1.6.3.5 |
| 被复测提交 | `main@2ab7bf3`（Q 的第三轮 UAT 整改），基线 `7349fe6`（智能体D 的第三轮 UAT 报告） |
| 整改依据 | `docs/UAT3-v1.6.3.5-在线元数据审核第三轮用户验收报告-D.md` §6 |
| 复测方 | **智能体D**（与施工方 Q 分离；不变更产品代码，仅取证与变异验证） |
| 复测日期 | 2026-09-10 |
| 证据目录 | `docs/evidence/v1.6.3.5-uat3-d/`（新增/更新 6 份 JSON、5 张截图、3 个探针脚本） |
| 提交给 | Mr.Linsang |

---

## 1. 结论

**D-01 / D-02 / D-03 三项整改的功能实现全部复测通过；但 D-02 的回归锁存在 2 处假绿缺口，
另发现 1 项与 D-02 同源的鲁棒性缺陷（D-04）。**

| 项 | 功能复测 | 回归锁有效性（变异验证） |
|---|---|---|
| D-01 取消任务完成时间与耗时稳定性 | **通过** | 有效（2 处变异均被抓住） |
| D-02 过期未认领任务回收 | **通过**（两条生产接线均实证生效） | **假绿**：移除两条生产调用后 11 个用例仍全绿 |
| D-03 submission 生命周期与恢复动作 | **通过** | 有效（2 处变异均被抓住） |
| 全量回归 | **2107 passed / 0 failed / 0 errors / 30 skipped**（较整改前 2096 多 11 项，即 Q 新增用例） | — |
| **D-04（新发现，P3）** | 幽灵 ACCEPTED 任务（不在唯一槽）致前端两个按钮同时禁用，用户无法自救 | 建议随下次一并整改 |

---

## 2. 复测方法与立场

- **施工与验收分离**：本人只读产品代码，不改动、不提交产品文件；所有结论来自
  ①真实浏览器操作 ②数据库/接口反证 ③对源码施加**回退式变异**后跑 Q 的用例。
- **变异验证**（本轮新增的关键手段）：把 Q 的修复逐条"改回缺陷态"，再看 Q 的回归锁是否变红。
  变红=锁有效；仍全绿=假绿。变异执行后一律从 git 恢复文件，脚本已改为**字节级读写**以避免
  行尾污染（首轮曾把两个前端文件改成 CRLF，已 `git checkout` 恢复，最终工作区仅剩本人证据文件）。
- 夹具沿用第三轮 UAT 的独立环境（`uat_d_1635_r3_meta` / `uat_d_1635_r3_target`，真实 Chrome）。

---

## 3. 逐项复测

### 3.1 D-01 取消任务无完成时间、耗时膨胀 —— 通过

**新代码路径（真实浏览器取消）**：注入 25s child 启动延迟 → 点击「取消任务」→ 确认。

| 项 | 结果 |
|---|---|
| job | `6451f17a…`（CANCELLED / CLEANUP） |
| `finished_at` | **`2026-09-10T10:42:53.002881`（已落库）** |
| 页面「已用时长」 | 3s |
| 接口 elapsed（间隔 **75 秒**两次查询） | **3 → 3（增长 0）** |
| cleanup_ok / exit_code | 1 / 1，无 report_id |

**存量数据（不回填，遵 R-06/R-13）**：整改前产生的取消任务 `5a667fe6…`（`finished_at` 为空）

| 项 | 复测前 | 复测后 |
|---|---|---|
| elapsed | 104 → 179（**+75**，随时间膨胀） | **null → null（增长 0）** |
| `finished_at` | 空 | **仍为空（未被回填）** |

证据：`probe-cancel-elapsed.json`、`s9_cancel.json`、`d-s9-03-after-cancel.png`。

> 结论：新任务耗时稳定、存量任务停止膨胀且未被伪造完成时间，符合施工图与数据语义规约。

### 3.2 D-02 过期未认领任务不回收 —— 通过（两条生产接线均实证）

施工图要求"制造 + **两个调用点**"。Q 的用例只直接调用方法，因此本人专门补了两条**接线**复测：

**W1 受理路径自愈（`create_job` 内的调用）**

| 步骤 | 观察 |
|---|---|
| 造一个 60 秒前受理、从未被认领的 ACCEPTED 幽灵任务（占用唯一槽） | `ghost_state_before = ACCEPTED` |
| 发起**新受理**（同一路径，模拟用户重试） | **`new_job_created = true`（非 409）** |
| 幽灵任务终态 | **`FAILED` / `START_TIMEOUT`**，`finished_at` 已写 |
| 日志 | `回收过期未认领任务 job_id=… age_s=60.0` |

**W2 runner 进程回收（`MetadataRunner._tick` 内的调用）**

| 步骤 | 观察 |
|---|---|
| runner 停机状态下造 60 秒前 ACCEPTED 幽灵任务 | 槽被占 |
| 启动 runner | **8 秒内**幽灵任务 → `FAILED / START_TIMEOUT`，`finished_at` 落库，`cleanup_ok=1`，**槽已释放**（`slot_active_job = null`） |

证据：`probe-d02-w1.json`、`probe-d02-w2.json`、`probe_stale_reclaim_d.py`。

> 结论：受理自愈与 runner 回收**两条真实路径都生效**，设计意图（runner 已死时用户可自救 / runner
> 回归时自动收口）均已闭环。

### 3.3 D-03 submission 生命周期与"恢复/重扫"分离 —— 通过（真实浏览器）

| 用例 | 观察 |
|---|---|
| 入口存在性 | 「📄 恢复查看上次任务」按钮**存在且可用**；未选实例时「拉取元数据并执行文件审核」正确禁用 |
| 无历史时点击恢复 | 提示 `无历史任务可恢复`，**新建任务数 = 0**（恢复不建任务） |
| submission 记录 | `{"user":"uat_d_1635_r3","intent_id":"ec57e15d…","key":"ec57e15d…","job_id":"608cb06f…","submission_state":"closed"}` —— **四个生命周期字段齐全且被写入** |
| 刷新后点击恢复 | 恢复**同一 job**（`recovered_same_job = true`）、任务卡含实例/库/状态/进度，**未新增任务** |
| 分页 generation 守卫 | 源码存在且被变异验证有效（见 §4 M5） |

证据：`s16_recover_action.json`、`d-s16-01-recover-no-history.png`、`d-s16-02-recovered.png`。

---

## 4. 回归锁变异有效性验证（本轮重点）

对每处修复施加"改回缺陷态"的变异，再跑 Q 的 `tests/test_v1635_uat3.py`：

| 变异 | 内容 | Q 的锁 | 判定 |
|---|---|---|---|
| **M1** | `_job_summary` 终态耗时回退到当前时间（复原 D-01 缺陷） | `test_terminal_without_finished_at_is_none` 变红 | ✅ 有效 |
| **M2** | `repository.cancel()` 不写 `finished_at` | `test_cancelled_job_writes_finished_at` 变红 | ✅ 有效 |
| **M3** | **移除 `create_job` 中的 `reclaim_stale_accepted()` 调用** | 整套 11 个用例**仍全绿** | ❌ **假绿** |
| **M4** | **移除 `runner._tick` 中的 `reclaim_stale_accepted()` 调用** | 整套 11 个用例**仍全绿** | ❌ **假绿** |
| **M5** | 移除分页回写的 generation 守卫 | `test_load_metadata_results_has_generation_guard` 变红 | ✅ 有效 |
| **M6** | 移除「恢复查看上次任务」入口 | `test_two_distinct_actions_recover_and_rescan` 变红 | ✅ 有效 |
| 恢复后 | 未变异源码 | **11 passed** | ✅ 无残留 |

**缺口说明（M3/M4）**：Q 的 `test_stale_accepted_reclaimed_allows_new_job` 等用例**直接调用
`repo.reclaim_stale_accepted(30)`**，因此只锁住了"方法本身正确"，没有锁住"**方法被生产代码调用**"。
这两个调用点正是 D-02 生效的全部入口 —— 若后续重构删除其中任意一处（乃至两处），测试全绿而
缺陷复活。历史上 A 的两轮 SIT、O 的 R2-04 都栽在同一类"断言存在但锁不住"的问题上。

**建议补锁（照图施工）**：在 `tests/test_v1635_uat3.py` 增加接线级断言（二选一，建议都做）：

```python
def test_reclaim_is_wired_into_acceptance_path():
    """受理路径必须调用回收（防 M3：方法在但没人调）。"""
    import inspect
    from backend.services import metadata_audit_repository as R
    src = inspect.getsource(R.MetadataJobRepository.create_job)
    assert "reclaim_stale_accepted" in src

def test_reclaim_is_wired_into_runner_tick():
    """runner 每轮必须调用回收（防 M4）。"""
    import inspect
    from backend.workers.metadata_runner import MetadataRunner
    src = inspect.getsource(MetadataRunner._tick)
    assert "reclaim_stale_accepted" in src

def test_stale_reclaim_selfheal_end_to_end():
    """行为级：存在超期未认领占用时，经 create_job 受理必须成功且幽灵任务判 START_TIMEOUT。
    （构造 60 秒前的 ACCEPTED + 占用槽 → create_job 新任务 → 断言 created=True 且旧任务 FAILED/
      START_TIMEOUT；对应本人 probe-d02-w1 的自动化版本）"""
```

> 另附一处**锁强度建议（NIT）**：`test_cancelled_elapsed_stable` 两次调用间隔不足 1 秒，
> 因 elapsed 为整数秒，**即使把缺陷改回来也会通过**（M1 就是被另一条用例抓住的）。
> 建议改为跨 1 秒以上的两次查询，或直接断言"有 finished_at 时耗时不随查询时刻变化"。

---

## 5. 全量回归（独立复核）

| 运行 | 环境 | 结果 |
|---|---|---|
| 本轮（整改后） | 与既往同口径：`C:\Python314` + 用户站点包；专用回归库 `uat_d_1635_r3_regression` | **2107 passed / 0 failed / 0 errors / 30 skipped（503.83s）** |

较整改前（2096 passed）多 **11** 项，与 Q 新增的 `tests/test_v1635_uat3.py` 用例数一致（无遗漏、无删除）；
30 个 skip 仍为既定环境性跳过（集成模块需内网凭据），按 R-18 不计入通过。
证据：`data/reports/uat_d_1635_r3/full-regression-after-fix.xml`。

---

## 6. 新发现

### 6.1 D-04（P3）幽灵 ACCEPTED 任务不在槽时，前端两个按钮同时禁用，用户无法自救

**现象（真实浏览器，已取证）**：当库中存在一个 `state='ACCEPTED'` 但 `slot.active_job_id`
已不指向它的任务时，进入「在线元数据审核」页：

| 观察 | 值 |
|---|---|
| 任务卡 | `#fec0cb49`，状态 ACCEPTED / WAITING，文案"任务正在后台执行…" |
| 「🚀 拉取元数据并执行文件审核」 | **disabled = true** |
| 「📄 恢复查看上次任务」 | **disabled = true** |
| 该任务是否占用槽 | 否（`active_job_id = null`） |

用户既不能开始新审核、也不能切换恢复，页面常驻"执行中"，**只能等运维介入**。
证据：`s17_phantom_accepted.json`、`d-s17-phantom-accepted.png`（本轮 s9/s16 首次运行即被此状态
阻断，Playwright 30 秒点击超时日志可作旁证）。

**触发条件与边界**：本次状态由本人上一轮探针构造（`release_slot` 后未同步终态）。生产正常流转
不会产生该组合，但两类情形可残留：① 历史版本升级前遗留的 ACCEPTED 记录；② `_run_job` 中
"未命中任何终态分支却走到 `release_slot`"的兜底路径。**D-02 的回收只处理"槽仍归该任务"的幽灵，
槽已空但状态仍为 ACCEPTED 的残留不在其覆盖范围。**

**整改建议（照图施工）**：在 `reclaim_stale_accepted()` 中增加第二条分支（与现有分支同一事务、
同一日志口径）：

- 条件：`state='ACCEPTED'` 且 `created_at` 超期（> `START_TIMEOUT`）且**未被 slot 引用**
  （`slot.active_job_id IS NULL OR slot.active_job_id <> job.id`）。
- 动作：判 `FAILED / START_TIMEOUT`，写 `finished_at`、`cleanup_ok=1`；不触碰槽（本就未被引用）。
- **安全性论证**：`claim_next_accepted()` 只从 `slot.active_job_id` 取任务，故"未被槽引用的
  ACCEPTED 任务"在任何时刻都不可能被认领，判失败不会误杀在跑任务；`RUNNING` 不纳入本分支。
- 建议同时给前端加一条兜底：`_recoverActiveJob()` 恢复到的任务若在若干次轮询内状态不变且
  阶段停在 WAITING，提示"任务未在执行器上运行，可重新发起"。
- 回归锁：`test_unreferenced_stale_accepted_reclaimed`、`test_running_job_never_reclaimed`（后者已有）。

---

## 7. 证据索引（本轮新增/更新）

| 类别 | 文件 |
|---|---|
| 探针脚本 | `probe_stale_reclaim_d.py`（D-02 两条接线）、`probe_mutation_q_d.py`（变异验证）、`cleanup_probe_residue_d.py` |
| 探针结果 | `probe-d02-w1.json`、`probe-d02-w2.json`、`probe-mutation-q.json`、`probe-cancel-elapsed.json`（更新） |
| 浏览器场景 | `s9_cancel.json`（更新）、`s16_recover_action.json`（新增）、`s17_phantom_accepted.json`（新增） |
| 截图 | `d-s9-01/02/02b/03`（更新）、`d-s16-01-recover-no-history.png`、`d-s16-02-recovered.png`、`d-s17-phantom-accepted.png` |
| 回归 | `data/reports/uat_d_1635_r3/full-regression-after-fix.xml` |

复现命令见 `docs/evidence/v1.6.3.5-uat3-d/README.md`（新增 D-02 接线与变异验证两节）。

---

## 8. 边界与建议

1. 本轮复测**只覆盖 D-01/D-02/D-03 三项整改**及其接线与锁有效性；第三轮 UAT 的其余结论（R2-01~R2-06
   已关闭部分）未重复执行，仍然有效。
2. 内网 6000+ 表容量、Linux/systemd 部署与回退、CORE_SAFE、断电重入等门禁**仍未覆盖**，不因本轮通过而改变。
3. 建议处置顺序：**先补 M3/M4 的接线锁**（成本极低、风险为零，防止 D-02 在后续重构中静默复活），
   D-04 可随下一版一并整改（当前触发条件需要状态残留，非日常路径）。

---

测试责任方：**智能体D**
提交给：Mr.Linsang
