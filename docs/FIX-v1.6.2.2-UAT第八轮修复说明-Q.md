# v1.6.2.2 UAT 第八轮修复说明（O-30 审计落库收口）

| 项 | 内容 |
|---|---|
| 修复人 | 智能体 Q |
| 修复日期 | 2026-08-30 |
| 依据 | `UAT-v1.6.2.2-第八轮第七轮遗留问题专项复测报告-智能体O.md`（被测提交 `d40cf73`，第七轮遗留专项复测） |
| 处置口径 | 第八轮仅登记 O-30 未关闭（O-28/O-29 已关闭）；本轮仅修复该项并补定向复测 |

---

## 一、处置总览

| 原 ID | 等级 | 第八轮状态 | 本轮处置 |
|---|---|---|---|
| O-28 | MAJOR | CLOSED（本轮确认） | 无需整改 |
| O-29 | MAJOR | CLOSED（本轮确认） | 无需整改 |
| O-30 | BLOCK | OPEN：精确三元组/无开关/篡改失败关闭均生效，但承诺的 `operation_logs` 审计 0 条落库 | **已修复（游标 rowcount 读取 + 同事务原子审计 + 故障回滚）** |

---

## 二、O-30（BLOCK）收口：调和审计未落库

**根因**（与 O 报告 §5.3 判定一致）：`_auto_reconcile()` 以 `cur = cursor.execute(UPDATE…)` 取返回值再 `getattr(cur, "rowcount", 0)` 判断中标——但本项目 `_MySQLCompatCursor.execute()` **返回 int（受影响行数）而非游标**，`getattr(1, "rowcount", 0)` 恒为 0；中标进程被误判为未中标，从“并发幂等”分支提前返回，永远走不到 ERROR 调和日志与 `log_operation()` 审计。原实现还把审计放在独立连接的 `log_operation()` 上（失败仅告警不回滚），即使到达也违背“基线更新与审计原子一致”的承诺。

**修复**（`backend/schema/migrator.py`，按 O §六可直接实施的五条逐项落地）：
1. **受影响行数从游标自身读取**：`cursor.execute(UPDATE…)` 后 `affected = cursor.rowcount`，不再从 `execute()` 返回值取 `rowcount`；
2. **基线更新与审计同连接、同事务、单次 commit**：新增 `_insert_reconcile_audit()` 在同一游标上直接 `INSERT INTO operation_logs`，写入成功才统一 `commit()`；审计插入失败 → `conn.rollback()`（含已执行的 checksum UPDATE）并抛 `MigrationError` 使启动失败关闭——绝不出现“只改基线不落审计”；
3. **未中标分支**：`affected == 0` 时先提交本线程空事务刷新 REPEATABLE READ 快照（否则回读落在中标进程提交前的旧快照、误报失败关闭），再回读 checksum；仅当确认已是新值才按并发幂等返回，否则失败关闭；
4. **日志分级**：中标进程输出一次 ERROR“一次性自动调和完成…审计已落库”；未中标进程仅输出 INFO“并发幂等…基线重设与审计”；
5. **定向断言**（`tests/test_o23_migration_fail_closed.py` 扩展，32 用例）：
   - 单进程精确调和后审计新增**恰好 1 条** + ERROR 日志断言；
   - 双进程并发后审计总共新增**恰好 1 条**，两进程均返回成功；
   - 二次启动（并发幂等/无漂移）**不新增审计**，且不得产生重复 ERROR 调和日志；
   - 审计写入故障注入 → `MigrationError` + **checksum 回滚保持历史值**；
   - 未知三元组/篡改仍失败关闭（既有断言保持）。

**验证**：
- 单元测试：`tests/test_o23_migration_fail_closed.py` **32 passed**；
- **端到端实测**（`scratch/verify_o30_e2e.py` → `docs/evidence/v1.6.2.2-uat-q-r8/o30_e2e_verification_r2.txt`，同时展示 checksum、ERROR 日志、`operation_logs` 查询结果）：
  - 历史 checksum 置回 → 无开关启动自动调和，审计 7→8（+1），ERROR 日志含“审计已落库”；
  - 二次启动幂等：审计条数不变、无新增调和；
  - `operation_logs` 查询输出调和记录（operator=system、target_id、detail）；
  - 篡改后启动失败关闭，审计不新增。

---

## 三、复测入口（给 O 第九轮，按报告 §七最小复测范围）

| # | 复测要点 | 期望 |
|---|---|---|
| 1 | 历史 checksum 单 worker 自动调和 | 审计新增 1 条 |
| 2 | 双 worker 并发调和 | 审计仍只新增 1 条 |
| 3 | 二次启动 | 无新增审计 |
| 4 | 审计写入故障注入 | 事务回滚、启动失败关闭 |
| 5 | 后续未知漂移 | 继续失败关闭 |

**回归结果**：全量自动化 **1569 passed / 0 failed / 28 skipped**（第七轮基线 1567 + 本轮新增 2 条：二次启动无新增审计、审计故障回滚）；正式门禁 `run_all.py --mode implementation --matrix` 实测**退出码 0、`RESULT PASS`**（执行记录 `docs/evidence/v1.6.2.2/o21_gate_run.txt`）。
