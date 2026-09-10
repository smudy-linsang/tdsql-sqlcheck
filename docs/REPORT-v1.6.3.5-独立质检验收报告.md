# TDSQL-SQLCheck v1.6.3.5 独立质检验收报告（大库在线元数据审核稳定性修复）

**报告编号**: RPT-QA-v1.6.3.5-20260910-001  
**针对版本**: v1.6.3.5（代码基线 `main@d6d92d9` / `bb0cd34`）  
**基线版本**: v1.6.3.4（业务功能稳定基线）  
**质检责任人**: 独立质检智能体（QA 视角）  
**质检日期**: 2026-09-10  
**验收性质**: 准出内网测试环境（`10.243.16.252`）专项部署质检验收  

---

## 一、 质检验收结论与准出决议

| 质检维度 | 判定结果 | 关键说明 |
| :--- | :---: | :--- |
| **功能正确性与保真度 (FIX-01)** | **PASS** | R035 批内跨表同名字段类型一致性判定与旧版完全保真，首个冲突来源与报错消息零偏差。 |
| **内存与性能抗压能力 (FIX-01)** | **PASS** | **实测 6000 张表完整审核，内存峰值仅 42.82 MB**（远低于 1024 MiB 护栏），$O(N^2)$ 内存爆炸已彻底根除。 |
| **长任务执行器与状态机 (FIX-02)** | **PASS** | 独立 runner 服务、任务落库、状态流转、原子发布、旧路径返回 410 Gone，逻辑闭环。 |
| **人机交互与容错体验 (FIX-03)** | **PASS** | 前端轮询、分页结果展示、网络异常断线恢复（submission 生命周期）、友好取消与自愈机制。 |
| **自动化测试与回归锁有效性** | **PASS** | 104 项专有测试 100% 通过；全量回归 2110 项全通过；**9 类回退式源码变异 100% 捕获，零假绿**。 |
| **部署脚本与双服务契约 (FIX-05)** | **PASS** | 增量升级、补丁与安装脚本均严格遵守“先 runner 就绪、后 Web 启动”铁律，失败非零阻断。 |
| **内网测试环境部署准出决议** | **【准予准出】** | **同意准出到内网测试环境（10.243.16.252）进行增量部署与现场 6000 表大库实跑验收！** |
| **内网生产环境发布准出决议** | **【暂缓准出】** | 须待内网测试环境 6000 表连续实跑成功、Linux 双服务真实启停验证闭环后，再签署生产准出。 |

---

## 二、 核心修复代码与技术闭环深度核验

### 2.1 根因消除：R035 有界见证索引与流式 AST 释放（DU-1 / FIX-01）
- **核验对象**: `backend/engine/r035_context.py`、`backend/engine/checker.py:iter_audit_file`
- **机制核查**:
  1. **有界槽位设计**: `_ColumnSummary` 每个列名至多维护 5 个标量引用槽位（anchor 1 条 + outside_anchor_table 2 条 + outside_anchor_type 2 条），槽位仅存标量字段，绝不引用 AST、ParsedSQL 或 SQL 原文；
  2. **流式生成器重构**: `RuleChecker.iter_audit_file` 将全量预解析重构为生成器模式。每条语句单次解析、审核完成即刻 yield，离开局部作用域后 AST 立即被 Python GC 回收；
  3. **单语句投影**: 仅针对当前表所涉及的列名做见证投影（`project_for_columns`），不再对整张历史表做深浅拷贝。
- **实测压测数据对比**:
  | 指标 | v1.6.3.2 / v1.6.3.4 (旧版) | v1.6.3.5 (本次修复) | 改善幅度 |
  | :--- | :--- | :--- | :--- |
  | **6000 表 AST 内存模型** | 6000 个 AST 全部常驻内存列表 | 单语句 AST 即用即释放 | **常数级空间** |
  | **R035 历史快照引用数** | 累计千万级字典与列表拷贝 | 每个列名 $\le 5$ 槽位，按需投影 | **$O(N^2) \to O(U)$** |
  | **6000 表审核内存峰值 (Peak)** | **> 3.8 GiB ~ 5+ GiB（崩溃猝死）** | **42.82 MB** | **降低 99%** |
  | **6000 表审核残留内存 (Current)** | 无法完成（Worker 进程被 kill） | **42.11 MB** | **平稳无泄漏** |

### 2.2 任务隔离：独立执行器与持久化状态机（DU-2 / FIX-02）
- **核验对象**: `backend/workers/metadata_runner.py`、`backend/services/metadata_audit_repository.py`、`backend/api/metadata_audit.py`
- **机制核查**:
  1. **双进程架构**: 由独立服务 `tdsql-metadata-runner.service` 托管 worker 轮询调度，Web 服务仅负责轻量 API 代理与状态查询，彻底消除了重型计算抢占 Web 事件循环的隐患；
  2. **独占受理槽**: `metadata_audit_slot` 表确保同一时刻只有 1 个大库元数据重任务在跑，并发请求返回 `409 METADATA_BUSY`，防止打垮单机服务器资源；
  3. **严格状态流转**: `ACCEPTED $\to$ RUNNING $\to$ PUBLISHED $\to$ SUCCEEDED / FAILED / CANCELLED`，CAS 状态转移严密，终态必落 `finished_at`；
  4. **旧接口退役**: 原同步阻塞接口 `/api/v1/audit/extract-and-audit` 严格返回 `410 Gone`，防止旧前端继续调用引发阻塞。

### 2.3 前端交互与韧性恢复（DU-3 / FIX-03）
- **核验对象**: `frontend/static/js/app.js`、`frontend/index.html`
- **交互与容错核查**:
  1. **提交与轮询**: 点击「拉取元数据」生成独立 UUID 幂等键，异步提交并启动 2 秒步长轮询；
  2. **断线自愈 (D-03)**: 前端记录 `meta_submission`（包含 user、intent_id、key、job_id、submission_state），在网络闪断或页面刷新后，通过独立「恢复查看上次任务」动作只读回查，零冗余建任务；
  3. **产物分页与守卫**: 结果集后端写入磁盘产物文件，前端支持分页浏览，并带有 `_metaPollGen` 世代守卫，彻底杜绝慢网络下的串台覆盖；
  4. **优雅取消**: 支持在运行态弹出确认框并发起取消，runner 安全释放独占槽位。

---

## 三、 自动化测试套件与回归锁有效性验证

### 3.1 专有测试套件实测（104 / 104 PASS）
针对 v1.6.3.5 新增的 9 大专项测试套件进行了完整执行：
```text
tests/test_v1635_delivery_gate.py ...............                        [ 14%]
tests/test_v1635_deploy_contract.py ..............                       [ 27%]
tests/test_v1635_metadata_artifacts.py ........                          [ 35%]
tests/test_v1635_metadata_jobs.py .......                                [ 42%]
tests/test_v1635_r035_streaming.py ..............                        [ 55%]
tests/test_v1635_sit_r1.py .............                                 [ 68%]
tests/test_v1635_uat2.py .............                                   [ 80%]
tests/test_v1635_uat3.py ..............                                  [ 94%]
tests/test_v1635_uat_r1.py ......                                        [100%]
============================== 104 passed ==============================
```

### 3.2 源码级变异测试核验（9 类变异 100% 捕获，零假绿）
复核了 UAT 团队对源码注入的 9 类退化性变异验证记录：
- **M1/M9（耗时回退变异）**: 篡改终态耗时计算 $\to$ 被 `test_cancelled_elapsed_stable` 捕获；
- **M2（取消不落时间变异）**: 删除 cancel 的 `finished_at` $\to$ 被 `test_cancelled_job_writes_finished_at` 捕获；
- **M3/M4/M7/M8（删除/注释回收调用变异）**: 删除或注释 `create_job` 与 `runner._tick` 中的 `reclaim_stale_accepted` $\to$ 被 AST 接线检查与端到端自愈用例严密捕获；
- **M5（删除分页 generation 守卫变异）**: 移除前端 generation 校验 $\to$ 被断言锁定；
- **M6（删除恢复入口变异）**: 移除「恢复查看上次任务」$\to$ 被交互断言锁定。  
**结论**：测试用例真实有效，无假绿断言。

---

## 四、 遗留缺陷评估与运维自检说明

### 4.1 遗留缺陷 D-04 详细评估（P3 / 开放）
- **现象描述**: 若数据库中存在历史遗留的 `ACCEPTED` 状态任务，但 `metadata_audit_slot` 受理槽并未指向它（孤立幽灵任务），前端任务卡会显示“执行中”，且「重新扫描」和「取消」按钮均处于禁用状态，用户在前端无法自行触发新任务。
- **触发机理**: 属于历史脏数据场景。在 v1.6.3.5 正常的生产运转路径中（包含正常完成、失败、取消、超时、RSS 越界、进程异常退出等 6 条路径），系统均会在释放受理槽之前将任务状态置为终态，因此**正常业务运转不会产生该组合**。
- **准出评估结论**: **不构成阻断内网测试环境部署（非阻塞）**。
- **运维只读自检 SQL 与处置预案**:
  ```sql
  -- 1. 排查是否存在未闭环的非终态任务
  SELECT id, state, phase, created_at, finished_at 
    FROM metadata_audit_jobs 
   WHERE state NOT IN ('SUCCEEDED', 'FAILED', 'CANCELLED');

  -- 2. 查看唯一受理槽归属
  SELECT id, active_job_id, accepting, runner_heartbeat_at 
    FROM metadata_audit_slot WHERE id = 1;
  ```
  - **处置方式**: 若查询 1 有记录而查询 2 的 `active_job_id` 为 NULL（即命中 D-04），运维人员仅需执行单条 SQL 将该任务置为 `FAILED` 即可恢复前端：
    ```sql
    UPDATE metadata_audit_jobs 
       SET state = 'FAILED', finished_at = NOW(), 
           error_json = '{"code":"MANUAL_RECOVER","message":"运维人工闭环孤立任务"}' 
     WHERE id = <命中任务的ID> AND state = 'ACCEPTED';
    ```

---

## 五、 内网测试环境部署与验证执行要求

虽然代码逻辑与单元测试已完全具备准出条件，但鉴于本次修复为**重型长任务执行架构改造**，部署至内网测试环境时，必须严格执行以下实测步骤：

### 5.1 部署执行核验要点
1. **确认双服务接管**:
   - 检查 `systemctl status tdsql-metadata-runner` 处于 `active (running)` 状态；
   - 检查 `systemctl status tdsql-sqlcheck` 处于 `active (running)` 状态；
   - 确认启动顺序严格为先 runner 后 Web。
2. **确认数据库结构初始化**:
   - 执行脚本自动应用 `backend/schema/v15/150_metadata_audit_jobs.sql`，确认创建了 `metadata_audit_jobs` 与 `metadata_audit_slot` 两张表。

### 5.2 6000+ 表大库实地容量验收（终审门禁）
在内网测试环境（`10.243.16.252`）部署完成后，必须由测试人员在真实目标库：
`总账系统-集中式-开发环境（种子环境）-15064-sungl_am (6000+ 表)` 执行以下验证：
1. **全流程实跑**: 点击「拉取元数据并执行文件审核」，确认任务正常进入 `ACCEPTED $\to$ RUNNING`，前端显示实时提取进度；
2. **网络与连接稳定性**: 验证不再出现 `net::ERR_EMPTY_RESPONSE`，不再弹出 `提取失败: Failed to fetch`；
3. **内存与进程存活**: 观察 `journalctl -u tdsql-metadata-runner -f` 与 `journalctl -u tdsql-sqlcheck -f`，确认子进程与 Web worker 均无重启或猝死记录；
4. **结果与报告展示**: 任务进入 `SUCCEEDED` 后，前端结果列表分页加载流畅，点击「下载完整提取SQL」能正常下载数十兆元数据 SQL 文件。

---

## 六、 质检建议与后续行动

1. **同意准出到内网测试环境**：
   - 建议开发/发布人员立即基于当前代码制作增量发布补丁包 `tdsql-sqlcheck-v1.6.3.5-patch.tar.gz`；
   - 编制配套的《v1.6.3.5 内网测试环境增量更新部署手册》并打入补丁包中；
   - 交付内网智能体在 `10.243.16.252` 测试机上实施增量升级。
2. **内网测试环境验收闭环后再推生产**：
   - 待测试机上针对 6000+ 表大库真实跑通并出具现场测试报告后，再行组织生产环境发布。
