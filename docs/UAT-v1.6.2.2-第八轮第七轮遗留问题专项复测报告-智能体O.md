# v1.6.2.2 第八轮第七轮遗留问题专项复测报告

| 项目 | 内容 |
|---|---|
| 测试执行人 | 智能体O |
| 测试日期 | 2026-08-30 |
| 被测分支 | `main` |
| 被测提交 | `d40cf739420be984a2805253ba671c890fe17c66` |
| 复测范围 | 仅 O-28、O-29、O-30 |
| 明确不在范围 | 全项目回归、119 条规则、浏览器全流程及新问题探索 |
| 总体结论 | **不通过：O-28、O-29 已关闭；O-30 未完全关闭** |

## 一、结论

本轮严格遵守“只测试上一轮问题是否关闭”的限定，没有打开或登记新问题。

| 第七轮问题 | 第八轮状态 | 结论 |
|---|---:|---|
| O-28：泛化 `OSError` 可伪装为数据库 422 | **CLOSED** | 原始 `PermissionError` / `FileNotFoundError` 反例均为 500 + `X-Request-ID`，真实驱动和网络错误仍为 422 |
| O-29：未声明 DEFAULT 时错误默认值漏检 | **CLOSED** | 错误默认值已失败关闭，合规无默认值结构仍为 `valid` |
| O-30：历史 checksum 无安全闭环、长期变量可放行未来漂移 | **OPEN** | 精确三元组、无开关升级、后续篡改失败关闭均生效；但既定“数据库审计落库”没有发生 |

因此本轮不能签署三项全部关闭，O-30 仍保持原 **BLOCK** 状态。修复 O-30 的审计写入后，只需再做该项定向复测。

## 二、执行结果

| 测试 | 结果 |
|---|---:|
| `tests/test_o19_offline_instance_inspect.py` + `tests/test_o23_migration_fail_closed.py` | `64 passed, 5 warnings` |
| O-28 原始反例 API 探针 | PASS |
| O-29 原始 DEFAULT 反例探针 | PASS |
| O-30 历史 checksum → 当前 checksum | PASS |
| O-30 二次运行幂等 | PASS |
| O-30 长期环境变量无法覆盖未知漂移 | PASS |
| O-30 调和后未来篡改失败关闭 | PASS |
| O-30 `operation_logs` 审计 | **FAIL：0 条** |
| `deploy/preflight_check.sh` Bash 语法检查 | PASS |

## 三、O-28 关闭验证

独立探针结果：

| 异常 | 转换结果 | API | 结论 |
|---|---|---:|---|
| `PermissionError("access denied reading encryption key")` | `None` | 500 + `X-Request-ID` | PASS |
| `FileNotFoundError("unknown database catalog file")` | `None` | 500 + `X-Request-ID` | PASS |
| PyMySQL 2003 | `ConnectionRefusedError_` | 领域 422 | PASS |
| PyMySQL 1045 | `AuthenticationFailedError` | 领域 422 | PASS |
| PyMySQL 1049 | `DatabaseNotFoundError` | 领域 422 | PASS |
| `ConnectionRefusedError` / `TimeoutError` | `ConnectionRefusedError_` | 领域 422 | PASS |

Q 已把认证/数据库文本兜底限制到可信驱动异常，并把内建网络错误限制为精确类型或网络 errno。O-28 的原始发生机制已消除，判定关闭。

## 四、O-29 关闭验证

隔离表预建 `note VARCHAR(32) DEFAULT 'unexpected'`，迁移声明仍为 `ADD COLUMN note VARCHAR(32)`。当前 `_structure_state()` 抛出：

> 默认值不符：期望无声明（规范化为 NULL），实际 `unexpected`

随后预建相同类型且无默认值的合规列，结构状态为 `valid`。配套测试还覆盖 `DEFAULT NULL`、空串、数字、布尔、带空格字符串和引号/关键字规范化。O-29 的原始漏检已消除，判定关闭。

## 五、O-30 未关闭的直接证据

### 5.1 已通过部分

- 模拟 v1.6.2.1 历史 checksum `54ee2e97…`，不设置任何调和变量执行真实 `run_migrations()`，记录成功更新为 `c6cf33bb…`。
- 第二次执行保持当前 checksum，没有再次改变基线。
- 即使人为设置已删除的 `SCHEMA_CHECKSUM_RECONCILE`，未知未来 checksum 仍抛 `MigrationError`，数据库记录保持当前值。
- 代码内精确三元组与结构不变量路径已经生效，预检脚本语法正确。

### 5.2 未通过部分：承诺的审计没有落库

独立探针在调和前、第一次调和后、第二次运行后分别统计：

| 时点 | `operation_type='schema_checksum_reconcile'` 数量 |
|---|---:|
| 调和前 | 0 |
| 第一次调和后 | 0 |
| 第二次运行后 | 0 |

运行日志也没有“迁移 checksum 一次性自动调和完成”，而是把单进程成功更新误记为“另一进程已完成”的并发幂等。

### 5.3 发生原因

`SchemaMigrator._auto_reconcile()` 当前写法为：

1. `cur = cursor.execute(UPDATE...)`；
2. `conn.commit()`；
3. 通过 `getattr(cur, "rowcount", 0)` 判断是否更新一行。

但本项目 `_MySQLCompatCursor.execute()` 直接返回 PyMySQL 的执行结果，即整数。独立合同探针得到：

- `execute_return_type = int`
- `execute_return_value = 1`
- `cursor.rowcount = 1`

因此 `getattr(1, "rowcount", 0)` 永远得到 0。实际成功更新的进程被误判为未中标，重读到新 checksum 后从“并发幂等”分支提前返回，永远到不了后面的 ERROR 日志与 `log_operation()`。现有并发测试只断言两个线程返回成功和 checksum 正确，没有断言审计条数，因此出现测试全绿但审计合同未兑现。

## 六、O-30 可直接实施的修复

1. 执行 UPDATE 后从游标本身读取受影响行数：

   ```python
   cursor.execute(update_sql, params)
   affected = cursor.rowcount
   ```

   不得再从 `cursor.execute()` 的返回值读取 `rowcount`。

2. 当 `affected == 1` 时，由同一连接、同一事务直接插入 `operation_logs`，然后统一 `commit()`；审计插入失败应回滚 checksum 更新并使启动失败关闭。这样才能保证“基线更新”和“审计记录”原子一致。
3. 当 `affected == 0` 时，回读 checksum；只有确认为当前 checksum 才按并发幂等返回，否则继续失败关闭。
4. 增加定向断言：
   - 单 worker 精确调和后审计新增恰好 1 条；
   - 双 worker 并发后审计总共新增恰好 1 条；
   - 第二次启动不新增审计；
   - 审计写入故障时 checksum 不改变且启动失败；
   - 首次中标进程输出一次 ERROR 调和日志，未中标进程才输出并发幂等 INFO。
5. 更新端到端证据，必须同时展示 checksum、ERROR 日志和 `operation_logs` 查询结果，不能只凭 checksum 更新判定闭环完成。

## 七、下一轮最小复测范围

Q 修复后无需再次开展全项目 UAT。只需复测 O-30：

1. 历史 checksum 单 worker 自动调和，审计新增 1 条；
2. 双 worker 并发调和，审计仍只新增 1 条；
3. 二次启动无新增审计；
4. 审计写入失败时事务回滚、启动失败；
5. 后续未知漂移继续失败关闭。

全部满足后，可将 O-30 关闭；本轮没有提出任何新问题。

## 八、证据索引

证据目录：`docs/evidence/v1.6.2.2-uat-o-r8/`

- `targeted_o28_o29_o30.txt/xml`
- `targeted_closeout_probe.py/json/txt`
- `summary.json`
- `README.md`
