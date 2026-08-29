# v1.6.2.2 UAT 第五轮修复说明

| 项 | 内容 |
|---|---|
| 修复人 | 智能体 Q |
| 修复日期 | 2026-08-29 |
| 依据 | `UAT-v1.6.2.2-第五轮全项目用户验收测试报告-智能体O.md`（被测提交 `50a1c04`） |
| 处置口径 | 保持 O 的原缺陷 ID 不变（O-22 BLOCK + O-23/O-24 MAJOR），全部同轮完成 |

---

## 一、处置总览

| 原 ID | 等级 | 问题 | 本轮处置 |
|---|---|---|---|
| O-22 | BLOCK | 一次性票据存进程内字典，双 worker 下 12/30 次 401 | **已修复（共享元数据库 + 原子消费 + POST 签发）** |
| O-23 | MAJOR | 迁移器吞 ALTER 失败仍写版本键（假 applied） | **已修复（失败关闭 + 结构验收 + 自愈重放）** |
| O-24 | MAJOR | 未知程序异常被包装成 422 连接失败 | **已修复（严格白名单映射，未知异常保持 500）** |

本轮 1 BLOCK + 2 MAJOR 全部完成整改，无延期项。另发现并修复一个次生问题：双 worker 并发启动时 `_init_default_data` 死锁（见 §五.4）。

---

## 二、O-22（BLOCK）：报告一次性票据跨进程随机 401

**根因**：票据保存在进程内模块级字典；生产 `--workers 2`（`deploy/tdsql-sqlcheck.service` 明确配置）下，签发与消费是两个独立 HTTP 请求，落在不同 worker 时必然找不到票据。Q 第四轮的浏览器验证因连接复用与 `AUTH_ENABLED=false` 未覆盖生产链路。

**修复**：
1. **共享存储**：新增迁移 `backend/schema/v12/120_gateway_report_tickets.sql`，票据进入元数据库 `gateway_report_tickets` 表；只存 **SHA-256 哈希**、`report_id`、`username`、`expires_at`、`consumed_at`，不明文持久化；
2. **原子消费**：消费端改为**单条 UPDATE**（`WHERE ticket_hash=? AND report_id=? AND consumed_at IS NULL AND expires_at > NOW()`），以受影响行数=1 作为成功判据，不再先查再改；签发者用户名仅在原子消费成立后回填；
3. **签发改 POST**：`POST /api/v1/gateway-log/reports/{id}/ticket`（产生状态的操作不再用 GET），前端 `viewGatewayReport` 同步改为 POST；长期 access token 继续禁止进入 iframe URL；
4. **批量清理**：每次签发顺带清理过期票据与已消费超过 1 小时的票据；错误报告、过期、重放、跨报告均统一 401，不泄露票据存在性。

**验证**：
- **真实生产形态实测**（`AUTH_ENABLED=true` + `uvicorn --workers 2` + 每次全新 TCP 连接）：100 次跨连接首次消费 **100/100 返回 200**、100 次重放 **100/100 返回 401**；伪造票据与无票据均 401；GET 签发被拒（405）。执行记录 `docs/evidence/v1.6.2.2-uat-q-r5/o22_workers2_verification.txt`；
- 自动化：`tests/test_o15_gateway_report_security.py` 扩展——一次性消费、绑定报告、伪造/空票据拒绝、**明文不落库（只存哈希）**、过期拒绝、**并发消费恰好一次成功**；`tests/test_gateway_log.py` 同步改为 POST 签发。

## 三、O-23（MAJOR）：迁移器假 applied

**根因**：`migrator.py` 对每条迁移语句宽泛 `except Exception` 只记 warning，随后无条件写入 `schema_migrations`——锁超时/权限/空间等真实失败被永久记录为"已应用"，缺列不会自愈。

**修复**（`backend/schema/migrator.py` 重构 + `database.py` 启动链）：
1. **失败关闭**：任一 DDL 语句失败立即抛 `MigrationError`，绝不写版本键；`init_db` 不再把迁移失败降级为告警，启动直接失败（健康检查不可用）；
2. **幂等不靠吞 Duplicate column**：`ADD COLUMN` 前先查 `information_schema.columns`；列已存在则**严格校验类型/可空性/默认值**，不符即失败关闭；
3. **最终结构验收**：声明列全部存在且结构相符后才写 `schema_migrations`（DDL 隐式提交，不靠事务回滚）；
4. **假 applied 自愈**：已登记文件每次启动做结构验收，声明列缺失即按幂等流程自动补齐（覆盖第三轮被假成功的历史库）；
5. **并发安全**：双 worker 并发启动——DDL 竞态的 `Duplicate column(1060)` 复核对方列结构后幂等通过；版本键唯一约束冲突仅当记录确实存在时视为幂等；另在 `init_db` 入口加 **MySQL 命名锁** `tdsql_sqlcheck_init` 串行化启动初始化（修复了实测发现的双 worker 启动死锁，见 §五.4）。

**验证**：`tests/test_o23_migration_fail_closed.py` 8 用例——全新补齐、双列幂等跳过、单列缺失只补缺失列、结构不符失败关闭、首/次 ALTER 注入锁超时与权限错误（无版本键 + 恢复后重试成功）、假 applied 自愈判定、**并发应用恰好一条版本记录**。

## 四、O-24（MAJOR）：未知异常被包装成 422

**根因**：`translate_db_error()` 对不认识的异常无条件返回 `InstanceConnectionError` 基类，合成 `RuntimeError` 也被转成 422"实例连接失败"，掩盖真实代码缺陷。

**修复**：
- `translate_db_error()` 改为**严格白名单**：errno 优先（2003/2004/2005/2006/2013→连接拒绝，1045→认证失败，1049→库不存在），其次精确短语兜底（"can't connect"/"connection refused"/"timed out"/"access denied"/"unknown database"）；**未知异常返回 `None`**；
- 调用端（`daily_inspect.py /run`）只在映射成功时抛领域异常；映射失败原样 `raise`——由统一 500 分支记录完整堆栈日志、返回通用文案并携带 `X-Request-ID`，响应不再泄漏内部异常细节。

**验证**：`tests/test_o19_offline_instance_inspect.py` 扩展——表驱动 errno 映射（2003/1045/1049）、`RuntimeError/AttributeError/TypeError` 直调返回 `None`、API 层故障注入（`registry.get_saved` 抛 `RuntimeError`）返回 **500 + X-Request-ID** 且不含"实例连接失败"字样；已知连接异常（离线实例）仍返回可读 422。

## 五、次生发现与说明

1. **双 worker 启动死锁（顺带修复）**：O-22 生产形态验证时实测两个 worker 并发初始化在 `_init_default_data` 的 `UPDATE role_permissions` 上死锁（1213）。已在 `init_db` 入口加命名锁修复，两个 worker 串行初始化，实测双进程均正常就绪；
2. **测试口令不落明文**：验收脚本口令改环境变量注入（`O22_VERIFY_PASSWORD`），通过 `test_no_hardcoded_secrets` 明文凭据防复发守护；
3. **实现包哈希不变**：本轮整改不涉及门禁目标文件（parser_legacy/distributed/requirements/pyproject），实现基线 `fea7a873…` 继续有效，门禁按既有基线同源验证。

---

## 六、复测入口（给 O 第六轮）

| 项 | 复测要点 | 期望 |
|---|---|---|
| O-22 | `AUTH_ENABLED=true` + `--workers 2` + 新 TCP 连接：100 次签发/消费、100 次重放 | 首次 200 成功率 100%，重放 401 率 100%；URL 无长期令牌 |
| O-22 | 普通浏览器查看报告 + 旧 XSS/partial 用例 | 报告可见可交互，CSP/nonce/sandbox 不变 |
| O-23 | 注入 ALTER 锁超时/权限失败；构造假 applied（有版本键无列）后重启 | 失败时不写版本键且启动失败关闭；恢复后重启自动补齐并完成索引体检 |
| O-23 | 双 worker 并发启动 | 结构一致、版本记录唯一、无死锁 |
| O-24 | `RuntimeError` 故障注入巡检接口 | 500 + X-Request-ID + 日志完整堆栈，不再伪装 422 |

**回归结果**：全量自动化 **1521 passed / 0 failed / 28 skipped**（第五轮基线 1506 + 本轮新增 15 条：O-23 8、O-24 4、O-22 票据扩展 3）；正式门禁 `run_all.py --mode implementation --matrix` 实测**退出码 0、`RESULT PASS`**（三版本矩阵、71 条冻结用例、全量回归、manifest/codestat 基线比对、设计包哈希全部通过，执行记录 `docs/evidence/v1.6.2.2/o21_gate_run.txt`）。
