# v1.6.4.0 AI Copilot 专家助手 开发记录（Q 施工）

| 项目 | 内容 |
|---|---|
| 版本 | v1.6.4.0（CP-1 受控只读专家助手） |
| 施工基线 | Rev.D 冻结（含 N-10），见 BASELINE-v1.6.4.0 施工基线冻结记录 |
| 施工入口 commit | 4fb1f2e（冻结提交） |
| 施工方 | Q |
| 施工日期 | 2026-09-13/14 |

## 1. 交付范围（按 DETAIL §14 工作包）

| 工作包 | 交付物 | 状态 |
|---|---|---|
| CP-W01 契约 | `backend/models/copilot.py`、`services/copilot/errors.py`（错误码闭集） | 完成 |
| CP-W02 数据 | `schema/v16/160_copilot_identity_runtime.sql`（A组2表）、`copilot_schema/v1/001_business.sql`（B组9表）、`schema/contracts.py`（完整结构合同）、`services/copilot/schema.py`（B组维护入口，退出码 0/20/1）、`repository.py`、`recovery.py` | 完成 |
| CP-W03 授权加密 | `services/copilot/crypto.py`（AES-GCM 密钥环+AAD）、`policy.py`（端点白名单/出域闸）、`authz.py`（四道闸/subject 代际/N-09 双管理员）；最小修改 `auth_service.py`（菜单/check_permission 分支/账户生命周期接点）、`database.py`（角色播种） | 完成 |
| CP-W04 知识资料 | `knowledge.py`（离线包+BM25+双字词）、`redaction.py`（敏感闭集+别名投影）、`evidence.py`（只读适配器）、`tools.py`（T01—T09）、`copilot_knowledge/` 构建器与初始包 | 完成 |
| CP-W05 模型 | `providers.py`（OPENAI_COMPAT_CHAT/能力契约/限长）、`routing.py`（主备/冷却/熔断/同域）、`output.py`（schema 校验/引用/断言/动作卡/本地模板） | 完成 |
| CP-W06 执行 | `workers/copilot_runner.py`（命名锁/心跳/租约/fencing/结构复验）、`workers/copilot_text_worker.py`（T09 受控子进程）、`workflow.py`（场景固定编排/整轮 deadline/降级） | 完成 |
| CP-W07 API | `api/copilot.py`（用户侧 16 端点）、`copilot_admin.py`（管理端）、`copilot_audit.py`；main.py 注册 + 全局 CopilotError 处理器 + lifespan 接 B组验收 | 完成 |
| CP-W08 页面 | `frontend/static/js/copilot.js`、`css/copilot.css`；index.html/app.js 接线（页头按钮/侧栏菜单/抽屉/会话页/归属防护） | 完成 |
| CP-W09 导出 | `services/copilot/report.py`（单轮独立脱敏 HTML，无 JS/外链） | 完成 |
| CP-W10 运维 | `deploy/tdsql-copilot-runner.service`、端点/keyring 样例、`copilot_emergency_disable.sh`；install/upgrade_incremental/apply_patch/rollback/verify_deploy 五脚本接线 | 完成 |
| CP-W11 测试文档 | `tests/copilot/`（契约/加密/策略/脱敏/知识/输出/N-10/B组/API/部署契约）、smoke_test.py 新增 CP-1 段、USER_GUIDE 新增第 0 章 | 完成 |

## 2. 关键设计落实要点

- **方案乙分组**：A组（subjects/runtime）进核心失败关闭链路；B组九表独立 `backend/copilot_schema/`，核心 loader 不发现，维护入口 `python -m backend.services.copilot.schema --apply` 独立执行。
- **N-10**：共享 migrator 按精确 version_key 区分——已登记 A组缺表抛 MigrationError 禁止自愈重建；既有迁移缺表自愈保持；首装可建。双向正反例见 `tests/copilot/test_migrator_n10.py`。
- **身份代际**：`copilot_subjects.subject_id` 为不可变身份；账户 create/delete/bootstrap 同事务维护；删除重建不继承旧授权/会话。
- **四道闸**：copilot 菜单 → 实例 grant（APPROVED+enabled）→ 源模块权限 → 会话所有者；撤权即时生效。
- **出域策略**：部署端点白名单（https/禁回环/链路本地/云元数据/泛域）；INTERNAL_REDACTED 只走 INTERNAL 端点；RESTRICTED 永不出站；标识符投影三闸（部署+逐实例+端点）。
- **幂等**：(owner_subject_id, client_request_id) 唯一；同键重放返回原 turn；preview 一次性消费。
- **降级**：无路由 LOCAL_ONLY；模型失败有本地证据 DEGRADED；无证据 FAILED；B组故障仅 capabilities/help 可读，其余 503。
- **runner**：命名锁 `tdsql_copilot_runner` + 2s 心跳 + 20s 租约 + fencing token；租约过期/排队超时/取消均有终态收敛；60s 结构复验失败即停受理。

## 3. 施工中发现并修复的问题

| 问题 | 处置 |
|---|---|
| CIDR 校验把「属于 0.0.0.0/0」误判为全部拒绝 | 改为仅拒绝等宽/更宽段，子集放行（policy.py） |
| 标识符正则漏分号/双横线 | 补入 `_PATH_CRED_RE` |
| `CopilotError` 未按闭集表取 HTTP 状态 | errors.py 构造器默认读 ERROR_TABLE |
| 会话预览后 expected_revision 误 +1 | 预览不改会话版本，响应取当前 revision |
| copilot_turns INSERT 占位符 19/参数 18 不匹配 | 重写 VALUES（18 占位 + 字面值） |
| `exception_handler` 误注册到 APIRouter | 移到 main.py 全局注册 |
| app.js 注入引用了 setup 域不存在的 `onBeforeUnmount` | 移除该键（Vue 解构对齐） |
| bootstrap 验收的 `SET SESSION MAX_EXECUTION_TIME` 污染连接池致他处 3024 | 改用不入池的独立直连（`__init__.py`） |
| 凭据守卫误判测试字面量 | 测试改用已登记测试值 |

## 4. 验证证据

- Copilot 专项：`tests/copilot/` 93 项全过（含 N-10 双向、B组逐表破坏、API 幂等/跨用户 404、部署契约 13 项）。
- 冒烟：`smoke_test.py` 99/99 通过（测试库 tdsql_sqlcheck_test）。
- 全量回归：`pytest tests/` 2226 passed / 30 skipped / 0 failed。
- 部署脚本：install/upgrade_incremental/apply_patch/rollback/verify_deploy/copilot_emergency_disable 全部 `bash -n` 通过。

## 5. 明确边界（未做/待后续门禁）

- 真实模型接入、数据出域批准、双管理员名单属 CP-GATE-DATA，本次未配置真实 key/端点；
- CP-TST-58 黄金集真实模型评测、内网实机 UAT、容量实测待 G/A/O 执行；
- 默认 COPILOT_ENABLED=false，未批准前助手为本地帮助模式。

---

## 6. 第一轮 SIT 整改闭环（2026-09-14，A 报告 `0bfb3c5`）

A 第一轮 SIT 结论 2 BLOCK / 3 MAJOR，不能进 UAT。五项全部认可并完成整改：

| 编号 | 级别 | 整改 |
|---|---|---|
| B-01 | BLOCK | 重建知识包（`kb-1.6.4.0-5b8426f429c6a89a` manifest 与实际逐字节自洽，状态 READY）；builder 增加构建期 `_self_verify`（写完即回读核验 file_sizes/sha256，不符即拒绝）；新增 `test_shipped_bundle_ready` + `test_builder_self_verify_catches_tamper` 回归锁 |
| B-02 | BLOCK | `schema.py` 新增 `is_structural_error`（1054/1146/1149/1091/1050 族 + 包因递归识别）、`fail_open_to_unavailable`（独立新连接置 UNAVAILABLE + 推进 epoch）、`guard_structural` 端点守卫；三个 API 模块 27 个 B 组端点统一接入；运行期删 `copilot_sessions` 实测由裸 500 收敛为 503 `COPILOT_SCHEMA_UNAVAILABLE` 且状态失效；新增 `test_runtime_guard.py` 回归锁 |
| M-01 | MAJOR | `preview_service.py` 预览阶段一并评估主备两端点 `allows_schema_identifiers`，任一不满足即回落 ALIASED（预览=实际出站，不偷偷缩减） |
| M-02 | MAJOR | `Limits.validate()` 越界即报；bootstrap（`copilot/__init__.py`）与受理入口（`_check_enabled`）双重拒绝启用，不静默夹值运行 |
| M-03 | MAJOR | `/copilot-admin/settings` GET/PUT 补 `_require_ready` 门禁（B组 UNAVAILABLE → 503）；health 保持只读例外（A 裁定实现正确，设计文本另行订正） |

变异自证：B-01（变异 `_self_verify` 失效）与 B-02（变异守卫失效）均打红后恢复全绿。

验证：Copilot 专项 103/103；冒烟 99/99（测试库）；全量回归 2249 passed / 30 skipped / 0 failed。


---

## 7. 第二轮 SIT 整改与交付证据订正（2026-09-15）

### 7.1 历史结论订正

保留 §4/§6 原文作为历史记录，但其中“B-01 已闭环、随包 READY”“X1 变异有效”“专项 103/103、全量零失败”不能作为 `74c3e5c` 的 Git 交付验收结论。A 在 `7ff356d` 的第二轮 SIT 已证明随包校验失败，以及 X1/X3 接线变异存活。本轮不沿用旧计数或中断前未核验的结果，以下仅记录本轮实际执行证据。

B-01 的进一步根因已核实：旧 manifest 两个摘要和大小，分别精确对应旧 Git blob 将 LF 扩展为 CRLF 后的字节；直接核验 Git 中 LF blob 则不符。因此“本地构建自洽”不等于“存仓后可用”。不能再将这类产物的 CRLF→LF 警告当作无害警告。

### 7.2 实际整改

| 编号 | 变更与防回退锁 |
|---|---|
| B-01 | builder 三个产物显式以 `newline="\n"` 写入，重建随包 manifest；`.gitattributes` 对知识包 JSON/JSONL 显式固定 LF，未扩大到全仓 JSON。新增 LF/大小/SHA256 行为断言。 |
| N-01 / X1 | `build()` 增加独立 `out_root` 供测试使用；在真实 manifest 写完后分别损坏 chunks/index，真实 `build()` 必须抛 `RuntimeError`，恢复后可重建。不替换 `_self_verify`，不再用调用计数代替损坏拒绝。 |
| N-01 / X3 | 真调 `build_preview()`，使用真实数据库中的 grant/provider/route/session；覆盖主备均允许、主拒绝、备拒绝、均拒绝、仅主端点、部署拒绝、实例拒绝、无路由八种情况，同时核对真实路由快照。测试事务统一回滚，端点配置与缓存恢复。产品 `preview_service.py` 无语义改动。 |
| N-02 | M-03 使用随机管理员名，不再盲删固定账号；回收仅由本次测试创建的用户和 subject。READY 的 GET 为正例，UNAVAILABLE 时 GET/PUT 均须 503，health 保持 200。 |
| 测试隔离 | 会话 keyring 用有退出清理的 fixture，恢复环境变量与缓存；B组 schema 初始化错误直接报错，不再被宽泛异常捕获伪装为 skip。 |

### 7.3 Git 字节证据与验证口径

- 基线：`7ff356d5a499453443b71cdeadd55683033d1f07`；开始时 `git pull --ff-only` 返回已是最新。
- 受测候选 Git tree：`73b43907f7a4809d2cefc0e5dec3fb11ac894402`。使用独立 `GIT_INDEX_FILE` 从 Git 对象导出至 `scratch/cp_r2_0915/checkout_73b43907f7a4`，没有复制 `.env`、本地 keyring 或其他未跟踪配置；真实 index 与 HEAD 未改。这是未提交候选快照，不是已经推送的 commit。
- 导出后和验证结束后均逐一核对 2096 个受版本控制文件的原始 Git blob；全部一致。历史三份 HTML blob 自带 CRLF，Git clean filter 会报告换行差异，故全树使用原始 blob 对比；backend/tests/deploy/属性文件另经 Git diff 核验。
- 知识包工作区字节＝Git blob＝检出字节；运行期加载 READY。重建保留已有审批人和有效期，本轮没有新增内容审批或数据出域审批。

| 文件 | LF 字节数 | SHA256 |
|---|---:|---|
| chunks.jsonl | 15517 | `951cb7260832bc3f911020c2b6035184c6db47746c0a8929d2ec277502a26152` |
| index.json | 140 | `bdd6cc3cf8b5423ee678754c49c7a141353be083121a598c6d7a4a5010eb3713` |
| manifest.json | 1700 | `dda5935b54dabbaff4f6e21f56c65f8f86e6e616be5b7b82019d0c85ec3427da` |

### 7.4 本轮实跑结果

环境：Windows、Python 3.14.6、pytest 9.1.1、MySQL 8.0.45、FastAPI 0.139.0、Pydantic 2.13.4。所有测试使用本轮独立库，未使用部署库；pytest 临时目录显式置于工作区。原始 stdout、JUnit XML、命令和结果 JSON 保留在 `scratch/cp_r2_0915/`，本地复跑驱动为 `scratch/verify_copilot_r2.py`；这些本地 scratch 证据尚未提交。

| 执行项 | 数据库 / 证据前缀 | 实际结果 |
|---|---|---|
| Copilot 连跑第 1 轮 | `tdsql_cp_r2_clean_0915` / `copilot_round_1` | 111 passed，0 failed / errors / skipped，49.17s |
| 同库第 2 轮（未清空库） | 同上 / `copilot_round_2` | 111 passed，0 failed / errors / skipped，26.09s |
| 同库第 3 轮（未清空库） | 同上 / `copilot_round_3` | 111 passed，0 failed / errors / skipped，25.27s |
| 变异全部恢复后专项 | `tdsql_cp_r2_mut_0915` / `mutation_restored` | 111 passed，0 failed / errors / skipped，25.18s |
| 冒烟 | `tdsql_cp_r2_smoke_0915` / `smoke` | 99/99，退出码 0 |
| 六份部署 shell 语法 | install/upgrade_incremental/apply_patch/rollback/verify_deploy/copilot_emergency_disable | `bash -n` 全部退出码 0 |
| 原始新库全量（前置未补齐） | `tdsql_cp_r2_regress_0915` / `regression` | 1725 passed / 417 failed / 114 errors / 31 skipped；不是有效的全绿证据 |
| 补齐前置后整改全量 | `tdsql_cp_r2_ctrl_h_0915` / `controlled_head` | 2255 passed / 2 failed / 0 errors / 30 skipped，525.83s |
| 同前置基线对照 | `tdsql_cp_r2_ctrl_b_0915` / `controlled_base` | 2279 tests / 6 failed / 0 errors / 30 skipped，498.41s |
| 回归对照结论 | `regression_comparison` | 新增失败 0；HEAD 较基线修复 4 项（知识包 B-01 重建锁住的 4 个 copilot 用例），剩余 2 项两侧相同 |

原始全量失败中的环境前置：冷库尚无 `admin`，旧 fixture 直接 reset/login 失败且未进入 teardown，造成认证状态连锁污染；G14 的破坏性测试另要求显式批准自定义测试库。受控对照两侧均先调用真实 `ensure_bootstrap_admin()`，使用测试初始口令，并设置 `G14_ALLOW_DESTRUCTIVE_TESTS=1`、`G14_TEST_DB_NAME` 精确指向各自隔离库，未修改产品或旧用例。整改侧剩余两项均来自 `test_fix_user_issues.py`：PDF 导出和慢 SQL 状态更新直接读取预存慢 SQL，而冷库没有该记录；本轮未补置该预存数据，全量结果仍非全绿。基线侧同样这 2 项失败，另加 4 个知识包用例（旧 commit 的随包校验失败，正是 B-01 整改对象）——HEAD 已全部修复。

变异只修改隔离检出副本，每项 `finally` 按原始字节恢复；真实工作区从未施加变异：

| 变异 | 实际结果 |
|---|---|
| X1 摘除 `build()` 的 `_self_verify` 调用 | 2 failed，均为未抛出预期 RuntimeError |
| X3 摘除预览端点闸 | 4 failed / 4 passed；主拒绝、备拒绝、均拒绝、无路由被抓住 |
| B01-LF 将 index 输出改成 CRLF | 1 failed，LF 字节断言失败 |
| X2 结构错误分类恒 False | 2 failed |
| X4 Limits.validate 恒空 | 3 failed / 13 passed |
| X5 分别摘除 settings GET / PUT 门禁 | 各 1 failed |

三轮完成后，M01/M03 的 users、subjects、providers、sessions、测试实例残留均为 0（`final_verification.json`）；代码与受测候选 tree 一致。IDE 语言服务未就绪，静态问题面板不能作为“无错误”依据；以 pytest 实跑和 Git 检查为准。

### 7.5 尚未覆盖与交接边界

- health 第三条只读例外仍待 O 在设计 §10.4 订正，本轮未擅改冻结设计。
- 未接真实模型、未批准数据出域、未做内网实机 UAT/容量或黄金集模型评测；不据本轮单元/集成测试自行宣布 UAT 放行。
- 本轮交付状态为本地整改与候选快照验证；scratch 证据保留在 `scratch/cp_r2_0915/`，验证驱动为 `scratch/verify_copilot_r2.py`，均不纳入版本控制。后续提交后还需确保提交中的代码和知识包与该候选字节一致。
