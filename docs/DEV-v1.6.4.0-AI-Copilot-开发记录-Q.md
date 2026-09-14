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
