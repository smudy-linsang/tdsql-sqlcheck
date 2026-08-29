# v1.6.2.2 UAT 第七轮修复说明

| 项 | 内容 |
|---|---|
| 修复人 | 智能体 Q |
| 修复日期 | 2026-08-30 |
| 依据 | `UAT-v1.6.2.2-第七轮全项目用户验收测试报告-智能体O.md`（被测提交 `e38c3d1`） |
| 处置口径 | 保持 O 的原缺陷 ID 不变（O-30 BLOCK + O-28/O-29 MAJOR），全部同轮完成 |

---

## 一、处置总览

| 原 ID | 等级 | 问题 | 本轮处置 |
|---|---|---|---|
| O-28 | MAJOR | `OSError` 全家族可用消息伪装成数据库连接 422 | **已修复（驱动专属短语 + 网络精确类型/errno 白名单）** |
| O-29 | MAJOR | 迁移未声明 DEFAULT 时错误默认值被静默接受 | **已修复（has_default 三态化 + 默认值规范化矩阵）** |
| O-30 | BLOCK | 历史 checksum 升级无安全闭环，持久调和变量可放行未来任意漂移 | **已修复（代码内精确三元组账本 + 结构不变量 + 原子调和；长期开关删除）** |

本轮 1 BLOCK + 2 MAJOR 全部完成整改，无延期项。

---

## 二、O-28（MAJOR）：OSError 家族消息伪装

**根因**：第六轮的 `_MESSAGE_FALLBACK_TYPES` 把整个 `OSError`/`TimeoutError`/`ConnectionError` 家族纳入消息兜底；`PermissionError`/`FileNotFoundError` 继承 `OSError`，消息含 `access denied`/`unknown database` 时仍被伪装成数据库 422。

**修复**（`backend/services/connection_errors.py`）：
1. `access denied`/`unknown database` 等文本兜底**仅用于可信驱动异常**（PyMySQL `OperationalError`/`InterfaceError`/`InternalError`），不再对泛化 `OSError` 启用；
2. 内建网络异常只认**精确类型**（`ConnectionRefusedError`/`ConnectionResetError`/`ConnectionAbortedError`/`TimeoutError`——类型本身即语义，无需文本匹配），或 `OSError.errno` 属于明确网络错误码集合（`ECONNREFUSED`/`ETIMEDOUT`/`EHOSTUNREACH`/`ECONNRESET`/`ENETUNREACH` 及 Windows `10054/10060/10061/10065`）；
3. `PermissionError`/`FileNotFoundError` 及其他泛化 `OSError` 一律返回 `None` → 统一 500 + 完整堆栈 + `X-Request-ID`。

**验证**（`tests/test_o19_offline_instance_inspect.py` 扩展至 34 用例）：
- 类型 × 短语 × errno 矩阵：`PermissionError`/`FileNotFoundError`/`IsADirectoryError`/`NotADirectoryError`/`BlockingIOError`/裸 `OSError`（含 EACCES/ENOENT）携带 5 类连接短语，直调全部 `None`；
- API 层故障注入（文件系统/权限类）返回 **500 + X-Request-ID**，响应不含“连接失败/认证失败/库不存在”；
- 真实网络异常（精确类型 + errno 10061/10060）与真实驱动 errno（2003/1045/1049、异常链包装）仍准确映射 422。

## 三、O-29（MAJOR）：未声明 DEFAULT 时默认值漏检

**根因**：`_expected_column_spec()` 用单个 `None` 同时表示“未声明 DEFAULT”和“显式 DEFAULT NULL”，`_verify_column()` 只在有值时比较——未声明 DEFAULT 的迁移完全不校验现存列默认值。

**修复**（`backend/schema/migrator.py`）：
- `_expected_column_spec()` 返回 `has_default` 与 `default` 两个字段，三态化区分“未声明/显式 NULL/有值”；
- `_verify_column()` **默认值永远参与校验**：未声明或显式 `DEFAULT NULL` → 期望 `information_schema` 中为 `NULL`（目标库对无默认值列的规范化结果）；有值时按新增 `_normalize_default()` 归一比较（关键字大小写归一、`CURRENT_TIMESTAMP()` 括号归一、布尔在整型列的 `TRUE/1` 物化归一、引号字符串精确比较）；
- 错误结构仍只失败关闭、绝不自动 ALTER 覆盖。

**验证**：新增 11 用例默认值规范化矩阵——未声明+预存错误默认值（O-29 核心反例）失败关闭、未声明+无默认值通过、`DEFAULT NULL` 两态、空串、数字相符/不符、布尔物化、带空格字符串、单双引号归一、关键字大小写归一。

## 四、O-30（BLOCK）：历史 checksum 升级无安全闭环

**根因**：第六轮的 `SCHEMA_CHECKSUM_RECONCILE` 是按 `version_key` 匹配的长期环境变量——留在持久 `.env` 中可把未来同版本文件的任意漂移重新登记为合法基线；且老库升级依赖运维手工动作，不是可验证、可回滚、不可遗留的闭环。

**修复**（`backend/schema/migrator.py` + `deploy/preflight_check.sh` + `docs/UPGRADE-v1.6.2.2-升级手册.md`）：
1. **代码内精确调和账本** `_KNOWN_RECONCILIATIONS`：仅接受精确三元组 `v9_090_connection_unique / 54ee2e97… / c6cf33bb…`；未知 key、未知旧值、未知新值、任意未来漂移**一律失败关闭**；
2. **调和前业务结构不变量**：`uq_conn_endpoint` 已存在、`uq_conn_name` 已移除、`tdsql_connections` 无重复端点，任一不满足即失败关闭；
3. **原子调和**：单条条件 UPDATE（`version_key` + 旧 checksum 同时匹配），双 worker 并发只有一个中标，另一进程重读确认后幂等通过；调和写 `operation_logs` 审计并以 ERROR 级日志留痕；
4. **长期开关已删除**：`SCHEMA_CHECKSUM_RECONCILE` 环境变量路径从代码中移除；预检脚本发现 `.env` 残留即 FAIL；
5. **升级手册**：`docs/UPGRADE-v1.6.2.2-升级手册.md` 含精确检测（预检输出四态）、强制备份、执行、启动后验证（调和日志/双 worker 就绪/幂等/审计落库）与回滚（单条 UPDATE 改回历史值）全套命令；`preflight_check.sh` 新增第 8 节升级预检（遗留开关 FAIL + 漂移状态探测）。

**验证**：
- 自动化（`tests/test_o23_migration_fail_closed.py` 扩展至 30 用例）：未知三元组失败关闭、篡改新值失败关闭且记录不被改写、精确三元组+不变量满足调和成功、调和后再篡改失败关闭、不变量缺失失败关闭、双 worker 并发恰好一次写入且结果一致；
- **端到端实测**（`scratch/verify_o30_e2e.py`，模拟 v1.6.2.1 老库）：记录置回历史 checksum → 不设任何环境变量启动 → 自动一次性调和 → 二次启动幂等 → 调和后篡改必然失败关闭；全链路通过并已恢复当前基线。

---

## 五、复测入口（给 O 第八轮）

| 项 | 复测要点 | 期望 |
|---|---|---|
| O-28 | 类型×短语矩阵：文件系统/证书/密钥/缓存/序列化异常携带 5 类连接短语，经巡检 API | 全部 500 + X-Request-ID；真实驱动错误与断连仍 422 |
| O-29 | 未声明 DEFAULT + 预存任意默认值；`DEFAULT NULL`/空串/数字/布尔/CURRENT_TIMESTAMP/带空格字符串/引号与大小写规范化 | 前者失败关闭；矩阵逐一符合目标库规范化结果 |
| O-30 | 模拟 v1.6.2.1 老库（记录=历史 checksum）无开关启动；双 worker 并发；调和后篡改文件 | 自动调和一次成功；并发恰好一次写入；篡改必然失败关闭；无环境开关残留 |
| O-30 | `preflight_check.sh` 第 8 节 | 残留 `SCHEMA_CHECKSUM_RECONCILE` 即 FAIL；漂移状态四态提示正确 |
| 回归 | 全量 pytest + 三版本门禁 + 1000/324/27/77 差分 | 全绿；`RESULT PASS` |

**回归结果**：全量自动化 **1567 passed / 0 failed / 28 skipped**（第六轮基线 1538 + 本轮新增 29 条：O-28 矩阵 13、O-29 默认值矩阵 11、O-30 调和闭环 5）；正式门禁 `run_all.py --mode implementation --matrix` 实测**退出码 0、`RESULT PASS`**（三版本矩阵、71 条冻结用例、全量回归、manifest/codestat 基线比对、设计包哈希全部通过，执行记录 `docs/evidence/v1.6.2.2/o21_gate_run.txt`）。
