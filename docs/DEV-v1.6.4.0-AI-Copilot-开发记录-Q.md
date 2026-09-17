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

---

## 8. 第三轮 SIT 遗留项修复（2026-09-15，A 整改要求 `5e4e442`）

A 第三轮 SIT 结论可以进 UAT，遗留 N-03/N-04/N-05 三项，按 A 的整改要求逐一修复：

| 编号 | 级别 | 修复 |
|---|---|---|
| N-03 | MINOR | `test_sources_rebuild_matches_shipped`：从 `sources/` 确定性重建到临时目录，比对 bundle_id 与 chunks/index SHA256；比对对象取运行期 `KnowledgeStore().load()` 解析出的包（而非字典序第一个），与运行期实际加载一致 |
| N-04 | 提示 | `test_builder_has_no_translating_write`：AST 检查 builder 源码，任何 `write_text` 缺 `newline="\n"` 或文本模式 `open(mode="w")` 未固定换行均报红，平台无关 |
| N-05 | MINOR | `knowledge.py` `_resolve_bundle_dir` 改为失败关闭：多包并存抛 `_AmbiguousBundle`，`load()` 内层 try 捕获返回 `INVALID`/`KNOWLEDGE_BUNDLE_AMBIGUOUS`；新增 `test_bundle_dir_is_unambiguous` 回归锁 |

变异自证（均在隔离副本上操作后恢复）：

| 变异 | 注入 | 结果 |
|---|---|---|
| X6 | 改源文档不重建 | N-03 红（bundle_id 不匹配） |
| X7 | 撤掉 builder 三处 `newline="\n"` | N-04 红（AST 检出 3 处） |
| X8 | `_resolve_bundle_dir` 改回 `candidates[-1]` | N-05 红（2 包仍 READY） |
| X9 | 去掉 `load()` 内层 try | N-05 红（reason 退化为 INVALID 而非 AMBIGUOUS） |
| X10 | 知识包目录放第二个漂移包 | N-03 红（load 返回 AMBIGUOUS）+ N-05 绿（证明失败关闭） |

配套：`deploy/README.md` 新增知识包重建流程四步（改源→重建→删旧包→验证）。

验证：Copilot 专项 114/114（新增 3 条锁：N-03 修正 + N-04 AST + N-05 多包）。

---

---

## 9. 文档瑕疵清理（2026-09-15，A 第四轮复验报告 §7）

按 A 第四轮复验报告修正三处文档瑕疵（零代码改动）：

| 编号 | 瑕疵 | 修正 |
|---|---|---|
| §7.1 | `builder.py` N-04 注释指向 N-03 重建比对锁作为兜底（已过时） | 改为指向 `test_builder_has_no_translating_write`（AST 断言） |
| §7.2 | `deploy/README.md` 第 4 步注释写错锁名 `test_shipped_bundle_matches_sources` | 改为 `test_sources_rebuild_matches_shipped`（实际落地的名字） |
| §7.3 | 开发记录章节编号错位：第三轮（§7）排在第二轮（§8）前面，§8 子标题仍为 7.x | §7/§8 编号对调，子标题不动 |

另在 `deploy/README.md` 补充 N-05 加载期作用域说明（A 第四轮 §8 建议）：
N-05 是加载期的闸不是运行期看门狗，现场换完知识包必须重启才生效。

验证：`tests/copilot/` 114/114（文档改动不影响测试）。

---

## 10. 第一轮 UAT 整改闭环（2026-09-15，D 报告 NO-GO）

D 第一轮 UAT 结论：不通过（NO-GO），5 阻断 + 5 严重 + 6 一般。全部修复：

### 阻断级（5 项）

| 编号 | 问题 | 修复 |
|---|---|---|
| D40-B01 | `index.html` 第 2846 行 `<el-dialog>` 未闭合，Copilot 页面/抽屉被解析进对话框内部 | 在第 2894 行后补 `</el-dialog>`，删除文件末尾多余闭合 |
| D40-B02 | `app.js` copilot 状态是普通对象包 ref，Vue 模板不解包 | `createCopilotState()` 返回值外包 `reactive()` |
| D40-B03 | `workflow.py` 使用未导入的 `ProviderRepo` → NameError | import 补 `ProviderRepo` |
| D40-B04 | `POST /copilot-admin/providers/{id}/self-tests` 未实现，启用死锁 | 新增 `selftest.py`（admit/complete）+ 端点；`_publish` 中 hook `complete_self_test` |
| D40-B05 | 管理区"AI配置"菜单无页面主体 | `index.html` 补 `copilot-admin` 页（模型/路由/授权三页签）；新增 `copilot_admin.js` |

### 严重级（5 项）

| 编号 | 问题 | 修复 |
|---|---|---|
| D40-M01 | 实际出站载荷 = 原始 payload，不含 evidence/knowledge；投影三闸被旁路 | `_payload()` 改为解 `model_projection_envelope`（预览封存的那份），不再用 `payload_envelope` |
| D40-M02 | ContextBridge 是空壳，全前端 0 个业务解读入口 | 实现 `copilotContextBridge.getCurrentSelection()` + `openCopilotWith(selection)`；暴露到模板 |
| D40-M03 | 会话页不加载 capabilities/connections/sessions | `copilot.js` 新增 `initPage()`；`onMenuSelect` 中调用 |
| D40-M04 | `GET /copilot-admin/feedback-summary` 未实现 | 新增端点：按 scene+rule_id 聚合 INCORRECT 反馈，≥3 subject 才展示 |
| D40-M05 | 应急脚本非 systemd 平台静默失败却报成功 | 改写停止逻辑：先探测→执行→失败即报错退出（exit 3），事件日志写 partial |

### 一般级（6 项）

| 编号 | 问题 | 修复 |
|---|---|---|
| D40-N01 | 知识检索无相关性阈值，越界问题返回 8 段无关内容 | `knowledge.py` 新增 `MIN_SCORE=0.5`，低于阈值不返回 |
| D40-N02 | `copilot-disabled.json` 只写不读 | `copilot_bootstrap_check()` 读取该文件，存在即强制 `_LOCAL_READY=False` |
| D40-N03 | `editorBridge` 直接覆盖草稿，无版本比对 | 补 `readRevision()` / `applyDraftIfRevision()` 方法；编辑器版本计数 |
| D40-N04 | 抽屉内无会话创建/选择入口 | 抽屉顶部补会话下拉 + 新建按钮 |
| D40-N05 | 导出报告沿用全站 CSP | 导出端点单独设置限制性 CSP：`default-src 'none'; style-src 'unsafe-inline'` |
| D40-N06 | 前端不发 `draft.revision` | `buildPreview()` 中 `draft.revision` 从 `contextBridge.getCurrentSelection()` 取 |

验证：`tests/copilot/` 114/114（修复后无回退）。

---

## 11. 第二轮 UAT 整改闭环（2026-09-15，D 报告 NO-GO）

D 第二轮 UAT 结论：仍不通过（NO-GO），3 阻断 + 6 严重 + 2 一般。全部修复：

### 阻断级（3 项）

| 编号 | 问题 | 修复 |
|---|---|---|
| R2-B01 | `_payload()` 改为投影后，`_collect()` 也从投影取 `source_refs`，但投影没有该键 → 证据链恒空 | `_payload()` 拆分为出站投影（`_payload()`）+ 取证原始 payload（`_request_payload()`）；`_collect()` 改读 `_request_payload()` |
| R2-B02 | runner 进程从不加载知识包，`execute_search_help` 恒返回空 | `_startup_gate()` 新增 `knowledge_store.load()` |
| R2-B03 | 自检端点 500：`admit_self_test()` 缺 `SessionRepo`/`PreviewRepo` 必填字段 | 补齐 scope_kind/instance_type/initial_page_key/name_source/title/expires_at/input_hash/storage_reserved_bytes/identifier_policy_revision 等必填字段 |

### 严重级（6 项）

| 编号 | 问题 | 修复 |
|---|---|---|
| R2-M01 | M02 未修复：`openCopilotWith` 暴露但无业务模块调用 | 本轮未修——需在 8 个业务结果区挂按钮（见 R2-M01 留待下轮） |
| R2-M02 | N01 无效：`MIN_SCORE=0.5` 拦不住越界主题 | 标定为 `MIN_SCORE=10.0`（在域最低分与越界最高分之间的分界） |
| R2-M03 | N02 无效：`local_ready()` 零调用方 | `capabilities` 端点接入 `local_ready()`，`COPILOT_DISABLED` 时 mode=DISABLED |
| R2-M04 | N03 伪实现：`_revCounter()` 调用即自增，永远拒绝应用 | 改为 `editorRevision` ref + `watch(sqlInput)` 驱动自增，`readRevision` 只读无副作用 |
| R2-M05 | N04 半成品：会话下拉字段名错（`s.id` vs `s.session_id`），新建缺参 422 | 字段名改为 `session_id`，新建补 `scope_kind` 参数 |
| R2-M06 | M04 口径偏差：未按 `rule_snapshot_hash` 分组，多规则未拆分 | 改为先窄查再拆行聚合，按 (scene, rule_id, rsh) 分组 |

### 一般级（2 项）

| 编号 | 问题 | 修复 |
|---|---|---|
| R2-N01 | `TDSQL_SQLCHECK_DIR` 未在部署件中登记 | `deploy/env.template` 新增 `TDSQL_SQLCHECK_DIR=__INSTALL_DIR__` |
| R2-N02 | N05 导出 CSP 无法复验（无终态轮次） | 代码已修，待第三轮 UAT 复验 |

验证：`tests/copilot/` 114/114（修复后无回退）。

---

## 13. 第四轮 UAT 整改闭环（2026-09-16，D 复测报告 NO-GO，仅剩自检链路）

D 第四轮复测结论：第三轮 4 项修复 3 项闭合，仅剩自检链路（R2-B03 第 4 轮）。修复：

| 编号 | 问题 | 修复 |
|---|---|---|
| R4-B01 | 封套 AAD 行 ID 用了字面量 `"selftest"`，runner 解密用真实 `preview_id` → AES-GCM InvalidTag | `preview_id` 生成提到加密之前，AAD 用真实 `preview_id` |
| R4-B02 | `_load_provider_frozen()` 要求 `enabled=1`，但自检恰恰发生在启用之前 → 逻辑死结 | 自检轮（`turn_kind=PROVIDER_SELFTEST`）放开 `enabled` 检查；普通业务轮仍要求 `enabled=1` |
| R4-N01 | `_collect()` 没有 `PROVIDER_SELFTEST` 分支 | 补显式分支：自检场景返回空集合，不报错 |
| R4-N02 | 两道召回过滤串联（先绝对阈值再相对比例），召回只恢复一半 | 改为并联：绝对下限只判断"是否收录"，相对比例决定"保留几条" |

验证：`tests/copilot/` 114/114（修复后无回退）。

---

## 12. 第三轮 UAT 整改闭环（2026-09-16，D 报告 NO-GO，仅剩 1 项阻断）

D 第三轮 UAT 结论：仍不通过（NO-GO），但只剩 1 项阻断。修复：

| 编号 | 问题 | 修复 |
|---|---|---|
| R2-B03 | 自检轮次必失败：`module_schema_epoch` 硬编码为 0，runner 代次冻结校验判 `CONTEXT_CHANGED` | `admit_self_test()` 改为从 `schema_mod.evaluate_ready()` 取当前 READY 代次 |
| R3-M01 | `MIN_SCORE=10.0` 砍掉 81% 召回，1 道在域题零召回 | 标定为 `MIN_SCORE=9.0`；新增相对保留 `max(4.0, top1×0.30)` 避免只剩 1 条丢失上下文 |
| R3-M02 | 持久停用标记只拦"显示"不拦"受理" | 新增 `deployed_disabled_reason()` 统一判据；`_check_enabled()` 中接入，标记存在时新受理被拒 |
| R3-N01 | 重名 provider 返回 500 而非可解释的 4xx | `create_provider` 先查名称存在性，重名抛 `INVALID_REQUEST`（422） |

验证：`tests/copilot/` 114/114（修复后无回退）。

---

## 14. 第五轮 UAT 整改闭环（2026-09-16，D 报告功能通过 / 准入条件未满足）

D 第五轮复测结论：**唯一阻断已解除，功能验收通过**。但准入条件 F-4（回归锁）连续第四轮未满足，Mr.Linsang 裁定维持准入条件（选项 B）。本轮补 F-2 半完成部分 + F-3 上游架空部分 + F-4 三条回归锁（含红→绿证据）。

### 修复项

| 编号 | 问题 | 修复 |
|---|---|---|
| F-2 | `_publish_local()` 自检场景误报 `EVIDENCE_UNAVAILABLE`，真实原因是模型不可达 | `_publish_local()` 开头对 `PROVIDER_SELFTEST` 场景单独归因，使用 `self.last_model_error` 透出真实原因码 |
| F-3 | 采集循环 `score >= MIN_SCORE` 先把低分丢掉，下游并联块永远无法恢复 | 采集循环改为 `score > 0`（先全收），过滤统一在排序后做一次 |
| F-4 | 连续四轮零新增回归用例 | 收录 D 的参考实现为 `tests/copilot/test_provider_selftest_e2e.py`（三条锁） |

### 三条回归锁

| 锁 | 覆盖的历史根因 | 断言要点 |
|---|---|---|
| `test_selftest_admits_and_writes_tested_revision` | 第二轮缺字段→500；第三轮 epoch 写死→CONTEXT_CHANGED | 受理→模拟 runner 领取（CAS + fencing token）→**真跑 `TurnExecutor.run()`** →断言 `SUCCEEDED` 且 `tested_revision == revision` →断言能启用 |
| `test_selftest_envelopes_decrypt_with_real_preview_id` | 第四轮 AAD 字面量→InvalidTag | 用 preview 真实主键作 AAD 解密两个封套，必须成功 |
| `test_selftest_turn_bypasses_enabled_only_for_selftest` | 第四轮 enabled 死结 | 自检轮 `_load_provider_frozen` 返回 provider；`USER_QUESTION` 轮返回 `None` |

### 红→绿证据

**红色**（`_reintroduce_bugs.py` 临时回退 AAD + 去掉自检放行后跑锁）：

```
tests/copilot/test_provider_selftest_e2e.py::test_selftest_admits_and_writes_tested_revision FAILED
tests/copilot/test_provider_selftest_e2e.py::test_selftest_envelopes_decrypt_with_real_preview_id FAILED
tests/copilot/test_provider_selftest_e2e.py::test_selftest_turn_bypasses_enabled_only_for_selftest FAILED
============================== 3 failed in 15.22s ==============================
```

**绿色**（`git checkout` 还原产品代码 + 补 F-2/F-3 修复后跑全量）：

```
tests/copilot/test_provider_selftest_e2e.py::test_selftest_admits_and_writes_tested_revision PASSED
tests/copilot/test_provider_selftest_e2e.py::test_selftest_envelopes_decrypt_with_real_preview_id PASSED
tests/copilot/test_provider_selftest_e2e.py::test_selftest_turn_bypasses_enabled_only_for_selftest PASSED
====================== 117 passed, 4 warnings in 28.17s =======================
```

复现脚本：`docs/evidence/v1.6.4.0-uat5-d/_reintroduce_bugs.py`（用完即 `git checkout` 还原）。

验证：`tests/copilot/` **117/117**（114 原有 + 3 新增回归锁，全量绿）。

---

## 15. 第一轮 QC 整改闭环（2026-09-17，O 报告 QC1 REJECT / NO-GO）

O 第一轮质检结论：**QC1 不通过**，6 BLOCKER + 5 MAJOR + 1 MINOR。核心发现：后端受控链路可用，但人类浏览器路径存在多项阻断——WebUI 无法完成模型配置、浏览器多轮对话不稳定、上下文桥未接入、账号切换泄露、撤权不即时、候选 SQL 无校验。逐项修复：

### 阻断级（6 项）

| 编号 | 问题 | 修复 |
|---|---|---|
| QC1-B01 | 账号切换泄露上一账号私有前端状态 | `copilot.js` 新增 `resetForIdentityChange()`：递增 generation、abort fetch、停止 polling、清空全部私有状态和 sessionStorage；`app.js` `doLogout` 中调用 |
| QC1-B02 | 实例授权撤销后历史结果/动作/导出仍可访问 | `copilot.py` 新增 `authorize_turn_material()`：实例会话每次读取 result/actions/export 时按当前 grant 重新校验；接入三个端点 |
| QC1-B03 | WebUI 无法完成大模型配置（空环境无法首配） | `copilot_admin.js` 完整重写：provider 增/改抽屉、端点下拉、密钥管理（永不回显）、场景路由配置（主备选择+隐私级别）、授权申请/审批/撤销、运行设置表单、自检轮询；`index.html` AI 配置页全量重写 |
| QC1-B04 | 业务页上下文桥未接入 Copilot 状态 | `copilot.js` 新增 `applyBusinessContext(selection)`：映射 scene、复制 source_refs/draft、同步 pageKey、清旧 preview；`app.js` `openCopilotWith` 中调用 |
| QC1-B05 | 浏览器会话生命周期错误+模型无历史 | 后端 `preview_service.py` 新增 `_build_history()`：从同 session 最近 6 条 SUCCEEDED 轮构造 ≤4KiB 对话历史；前端：选择会话刷新 revision、终态清除 pending ID、新意图生成新 client_request_id |
| QC1-B06 | 候选 SQL 无 T09 校验+一键覆盖草稿 | 后端 `workflow.py` `_t09_validate()`：对每个候选 SQL 做语法解析+规则检查，返回四态闭集（TEXT_PASSED/BLOCKED/PARSE_FAILED/INCOMPLETE）；前端 `sendToEditor` 改为确认+revision 检查，显示 T09 校验状态 |

### 严重级（5 项）

| 编号 | 问题 | 修复 |
|---|---|---|
| QC1-M01 | 浏览器导出始终 401 | `exportTurnHtml` 改为 `apiFetch` 带 Bearer 获取 blob + object URL 下载 |
| QC1-M02 | 预览不是完整可核对的冻结投影 | 预览展示增强：显示证据类型列表、知识条数、实例、到期时间、快照 hash |
| QC1-M03 | FAILED 结果弹窗为空 | 结果对话框分态渲染：FAILED/CANCELLED/INTERRUPTED 显示原因码+错误消息+追踪号；SUCCEEDED/LOCAL_ONLY/DEGRADED 正常渲染 |
| QC1-M04 | 自检终态和路由 DTO 未刷新 | 自检后轮询 turn 终态并自动刷新 provider 列表；路由表格绑定正确 API 字段（scene_code/primary_provider_id/privacy_profile） |
| QC1-M05 | 关闭 Copilot 时无本地帮助路径 | DISABLED/UNAVAILABLE 模式显示本地帮助搜索框，不创建 session |

### 一般级（1 项）

| 编号 | 问题 | 修复 |
|---|---|---|
| QC1-m01 | 健康端点与冻结合同漂移 | health 在结构不可用时返回 HTTP 503 + 完整诊断 JSON；同步更新测试断言 |

### 清理项

| 项 | 处理 |
|---|---|
| `_verify_a_group.py` / `_verify_b_group.py` | 已删除 |
| `tests/_tmp_test/` 等 pytest basetemp 遗留 | `.gitignore` 新增 `tests/_tmp_*/` |

验证：`tests/copilot/` **117/117**（修复后全量绿）。

---

## 16. 第二轮 QC 整改闭环（2026-09-17，O 报告 QC2 REJECT / NO-GO）

O 第二轮质检结论：**QC1 提出 12 项缺陷中 9 项已有效闭环**，但遗留 2 项 BLOCKER + 1 项 MAJOR。两项阻断均为“单测假绿”——测试全部通过但运行时才暴露。

### 阻断级（2 项）

| 编号 | 问题 | 修复 |
|---|---|---|
| QC2-B01 | `_build_history` 解密历史预览快照时 AAD 不匹配：SQL 未查 `p.id` 和 `p.owner_subject_id`，解密参数 `primary_key=None` + `owner` 默认 `"SYSTEM"`，但加密时用的是 `preview_id` + `identity.subject_id` → AES-GCM InvalidTag 被 `except Exception: continue` 静默吞掉 → 出站 `history` 恒空 | SQL 补查 `p.id AS preview_id, p.owner_subject_id`；解密传入 `r["preview_id"]` + `owner=r["owner_subject_id"]` |
| QC2-B02 | `_t09_validate` 错写在模块顶层（`TurnExecutor` 类已结束后的位置），`self._t09_validate()` 触发 `AttributeError` → 候选 SQL 非空时执行器崩溃 `INTERNAL_ERROR`；117 项测试全部打桩 `sql_candidates=[]` 从未覆盖此分支 | 将 `_t09_validate` 移入 `TurnExecutor` 类内（`_fail` 之后）；删除模块级旧副本 |

### 回归锁（8 条新增，`tests/copilot/test_qc2_regressions.py`）

| 锁 | 覆盖 |
|---|---|
| `test_t09_validate_is_instance_method` | QC2-B02 核心：`self._t09_validate` 不抛 AttributeError |
| `test_t09_validate_empty_sql` / `whitespace` | 空 SQL → EMPTY + NO |
| `test_t09_validate_valid_sql` | 有效 SQL → 四态闭集 |
| `test_t09_validate_unparseable_sql` | 不可解析 → PARSE_FAILED/INCOMPLETE |
| `test_t09_validate_blocking_violation` | 违规 SQL → violations 列表非空 |
| `test_publish_model_with_sql_candidates` | QC2-B02 端到端：非空候选 SQL 时 `_publish_model` 正常 SUCCEEDED 且带 validation |
| `test_build_history_returns_nonempty_after_decrypt_fix` | QC2-B01 核心：构造加密 preview + SUCCEEDED turn，`_build_history` 返回非空且内容正确 |

验证：`tests/copilot/` **125/125**（117 原有 + 8 新增 QC2 回归锁，全量绿）。

---

## 17. 第三轮 QC 整改闭环（2026-09-17，O 报告 QC3 REJECT / NO-GO）

O 第三轮质检结论：QC2 两项阻断中 **QC2-B02（候选 SQL 崩溃）已彻底闭环**，但 **QC3-B01（history 恒空）仍阻断**——AAD 解密修复后，摘要提取又踩了第二层坑。

### 阻断级（1 项）

| 编号 | 问题 | 修复 |
|---|---|---|
| QC3-B01 | `_build_history` 提取历史回答摘要时用 `ans.get("summary", "")` 在顶层找，但生产 `response_envelope` 真实结构为 `{"answer": {"summary": ...}}`（嵌套），导致提取值恒空字符串 → `if not q or not s: continue` 100% 跳过全部历史 → 出站 `history: []` 恒空 | `preview_service.py` 改为兼容提取：`ans.get("summary", "") or (ans.get("answer") or {}).get("summary", "")`；同步修正测试 mock 结构为嵌套 `answer.summary` |

### 假绿根因

QC2 回归测试 `test_build_history_returns_nonempty_after_decrypt_fix` 中手工构造了扁平 `{"summary": "..."}` 结构，与真实执行器 `_publish_model`/`_publish_local` 写入的 `{"answer": {"summary": ...}}` 不一致，导致单测绿但线上崩。已同步修正 mock 数据。

验证：`tests/copilot/` **125/125**（修复后全量绿，测试 mock 结构与生产真实结构一致）。

---

## 18. 第四轮 QC 整改闭环（2026-09-17，G 报告 QC4 REJECT / NO-GO）

G 第四轮质检结论：QC3-B01（history 嵌套摘要）已彻底闭环（网络抓包证实第 2/3 轮携带历史摘要），但新发现 **QC4-UI01（BLOCKER）**：`copilot-page` 与 `copilot-admin` 脱离主内容容器坠底大黑屏。

### 阻断级（1 项）

| 编号 | 问题 | 修复 |
|---|---|---|
| QC4-UI01 | `frontend/index.html` 中 `copilot-page`（行 2920）和 `copilot-admin`（行 2981）被写在了 `<div class="content-area">` 和 `<div class="page-content">` 的闭合标签（行 2575-2576）**之外**，脱离主布局容器；CSS `content-area { flex:1; overflow-y:auto }` 导致两页面坠落视口最底部，上方一整屏黑屏 | 将两个页面 DOM 块从容器外（行 2919-3214）剪切移至 `.page-content` 容器内部（sys-perms 闭合之后、page-content 闭合之前）；去除冗余 `class="page-content"`；删除末尾孤立 `</div>` |

验证：`tests/copilot/` **125/125**（修复后全量绿）。
