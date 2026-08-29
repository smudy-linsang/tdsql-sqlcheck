# v1.6.2.2 UAT 第六轮修复说明

| 项 | 内容 |
|---|---|
| 修复人 | 智能体 Q |
| 修复日期 | 2026-08-29 |
| 依据 | `UAT-v1.6.2.2-第六轮全项目用户验收测试报告-智能体O.md`（被测提交 `8fee172`） |
| 处置口径 | 保持 O 的原缺陷 ID 不变（O-25/O-26 MAJOR + O-27 MINOR），全部同轮完成 |

---

## 一、处置总览

| 原 ID | 等级 | 问题 | 本轮处置 |
|---|---|---|---|
| O-25 | MAJOR | 程序异常消息含连接短语仍被伪装成 422 | **已修复（异常类型/errno 双白名单）** |
| O-26 | MAJOR | 已登记迁移只验"列存在"，错误结构静默通过启动 | **已修复（结构状态机：valid/missing/mismatch + checksum 漂移失败关闭）** |
| O-27 | MINOR | 8 个 HTTP 用例依赖外部 8000 服务，干净环境不可复现 | **已修复（改为进程内 TestClient）** |

本轮 2 MAJOR + 1 MINOR 全部完成整改，无延期项。

---

## 二、O-25（MAJOR）：消息伪装绕过类型白名单

**根因**：第五轮的 `translate_db_error()` 虽声称"RuntimeError 等程序异常一律返回 None"，但 errno 判断之后的文本兜底对**所有异常类型**做消息子串匹配——`RuntimeError("can't connect to internal cache")` 仍被映射为 422 连接失败。

**修复**（`backend/services/connection_errors.py`，双白名单）：
1. **errno 只从可信驱动异常链提取**：沿 `__cause__`/`__context__` 链仅识别 PyMySQL 驱动异常（`OperationalError`/`InterfaceError`/`InternalError）`args` 中的整数 errno；任意 `BaseException.args` 的整数不再被认定为 MySQL errno；
2. **文本兜底受类型族约束**：仅当异常类型属于连接异常族（驱动异常 / `OSError` / `TimeoutError` / `ConnectionError`）时才启用精确短语匹配；`RuntimeError`/`AttributeError`/`TypeError` 及未知业务异常**直接返回 `None`**；
3. 调用端机制不变：映射成功 → 可读 422；映射 `None` → 原样抛出，统一 500 + 完整堆栈日志 + `X-Request-ID`。

**验证**（`tests/test_o19_offline_instance_inspect.py` 扩展至 18 用例）：
- 伪装矩阵（直调 + 经日常巡检 API 双通道）：`RuntimeError/AttributeError/TypeError` 分别携带 `can't connect`/`connection refused`/`timed out`/`access denied`/`unknown database` 文本，全部返回 **500**（含 `X-Request-ID`，响应不含"实例连接失败"）；
- 真实 `pymysql.err.OperationalError(2003/1045/1049)` 仍准确映射 422；驱动异常链（`__cause__` 包装）同样可识别；
- 连接异常族消息兜底（`OSError`/`ConnectionRefusedError`/`TimeoutError`）仍映射连接拒绝 422；离线实例真实链路 422 保持。

## 三、O-26（MAJOR）：已登记迁移只验"列存在"

**根因**：`_needs_reapply()` 对每个声明列只判 `None`，版本键与 checksum 一致时错误列结构（类型/长度/可空性/默认值）被静默接受；checksum 漂移仅记 warning 继续。

**修复**（`backend/schema/migrator.py`）：
1. **结构状态机** `_structure_state()`：`missing`（可幂等补齐）/ `mismatch`（抛 `MigrationError` 失败关闭）/ `valid`（允许跳过）——已登记且 checksum 一致的迁移每次启动逐列调用 `_verify_column()` 严格核对数据类型、长度、NULL、DEFAULT；
2. **错误结构绝不自动 ALTER 覆盖**：mismatch 直接失败关闭，错误信息含表名/列名/期望与实际结构，要求专门修复迁移或人工审批；
3. **checksum 漂移失败关闭**：默认抛 `MigrationError`（含两个 checksum 与操作指引），不再 warning 后继续；仅在 operator 显式设置 `SCHEMA_CHECKSUM_RECONCILE=<version_key>` 且结构验收通过时重设基线，并以 ERROR 级日志审计留痕；
4. 已对本项目历史漂移做一次性调和：`v9_090_connection_unique` 在 v1.6.0.4 被有意改为 no-op（提交 `08ce65c`），本地两个库已按契约完成调和（结构验收通过→重设基线 `c6cf33bb…`），生产库升级时按同一指引执行一次即可（详见复测入口）。

**验证**（`tests/test_o23_migration_fail_closed.py` 扩展至 17 用例）：矩阵覆盖 正确列 valid / 缺列 missing / 错误类型 / 错误长度 / 错误 NULL / 错误 DEFAULT / 漂移无白名单失败关闭 / 白名单但结构缺失拒绝调和 / 结构合法时调和重设基线；既有 8 用例（失败关闭、自愈、并发幂等）全部保持。

## 四、O-27（MINOR）：门禁依赖外部 8000 服务

**修复**：按 O 建议第 1 条，将 8 个静态资源/首页用例从 `requests` 打固定端口改为进程内 `fastapi.testclient.TestClient`（`/、/static/js/app.js、/static/css/app.css` 由应用自身托管，进程内语义等价）：
- `tests/test_sit_rules.py::TestFrontendIntegration`（3 项）；
- `tests/test_uat_rules.py` 中 5 项静态资源用例。

其余 API 用例维持原"有服务则集成、无服务则 skip"语义不变（不打 mock、不伪绿）。

**验证**：**停止全部外部服务后**干净环境直接执行全量 pytest：**1538 passed / 0 failed / 28 skipped**，8 项原失败用例全部通过，无残留服务依赖；正式门禁 `run_all.py --mode implementation --matrix` 单命令复现，实测 **退出码 0、`RESULT PASS`**（执行记录 `docs/evidence/v1.6.2.2/o21_gate_run.txt`）。

---

## 五、复测入口（给 O 第七轮）

| 项 | 复测要点 | 期望 |
|---|---|---|
| O-25 | 程序异常分别携带 5 类连接短语，经巡检 API 故障注入 | 全部 500 + X-Request-ID；真实 2003/1045/1049 与真实断连仍 422 |
| O-26 | 预建错误类型/长度/NULL/DEFAULT 列 + 正确版本键与 checksum 后启动 | 启动失败关闭且错误可定位；缺列可幂等补齐；结构合法正常跳过 |
| O-26 | checksum 漂移（不设置调和白名单 / 设置白名单且结构合法） | 前者失败关闭；后者重设基线且 ERROR 日志留痕 |
| O-27 | 端口 8000 空闲与已占用两种场景、干净环境单命令跑全量与正式门禁 | 两种场景结果确定；无残留服务 |
| 回归 | 全量 pytest + 三版本门禁 + 1000/324/27/77 差分 | 全绿；`RESULT PASS` |

**回归结果**：全量自动化 **1538 passed / 0 failed / 28 skipped**（第六轮基线 1521 + 本轮新增 17 条：O-25 伪装矩阵 8 + O-26 结构/漂移 9 等）；三版本矩阵、71 条冻结用例、正式门禁全部通过。
