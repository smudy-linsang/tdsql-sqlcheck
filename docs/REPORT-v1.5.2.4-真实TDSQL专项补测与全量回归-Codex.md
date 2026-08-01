# REPORT-v1.5.2.4 真实 TDSQL 专项补测与全量回归（Codex）

| 项 | 内容 |
|---|---|
| 被测基线 | `main @ 52c1142`，加本次两项缺陷修复 |
| 执行日期 | 2026-08-01 |
| 执行环境 | Windows 本地隔离工作树；本地 MySQL 元数据库；真实 TDSQL 分布式、集中式及 monitordb 实例 |
| 安全措施 | 凭据仅以进程环境变量注入；报告不保存口令、地址、原始 SQL 或监控业务数据；测试连接和隔离元数据均已清理 |
| 结论 | 代码与已具备条件的真实 TDSQL 用例通过；物理 ZK 扫描仍缺 Linux + ZK 接入材料，不能将该一项表述为已现场验收 |

## 1. 本次范围与修复

本次执行 G 遗漏的五项真实 TDSQL 用例（T2.2、T2.3、T3.2、T3.3、T4.3），并重跑全量回归。

过程中定位并修复两项实际问题：

1. `GET/POST /api/v1/tdsql/test-connection` 原先只接收 `user`，而 UAT 与保存连接接口使用 `username`，导致合法请求返回 400。现兼容 `username`，并保留 `user` 作为旧调用方别名。
2. 认证中间件把 `METHOD + URL` 异步写入 `operation_logs.operation_type`。带较长连接 ID 的 URL 可超过列上限，后台任务抛出 `DataError`。现由统一 `log_operation()` 按表结构上限截断 `operator`、`operation_type`、`target_type`、`target_id`、`ip_address`，避免审计旁路异常。

新增回归用例验证长路由审计可正常落库且截断长度正确。

## 2. 前置与隔离

- 三个真实目标分别完成只读 `SELECT 1` 连通性预检：分布式、集中式、monitordb 均通过。
- 应用通过 `TestClient` 启动，认证开启并使用临时管理员令牌；不占用共享服务端口。
- 每轮使用独立的本地 MySQL 元数据库和临时 Fernet 密钥。真实实例仅执行连接、探针、元数据读取、监控读取及 `SELECT SLEEP(20)`；未执行 DDL/DML。
- 测后已恢复声明值，并删除所有临时保存连接。隔离元数据库在报告提交前统一删除。

## 3. G 遗漏五项真实用例结果

| 用例 | 实测动作与关键证据（均已脱敏） | 结果 |
|---|---|---|
| T2.2 注册 + 形态落库 | 将真实分布式端点以 `groupshard` 形态注册到隔离元数据库；接口返回 `kind_synced=true`。随后探测响应中 S2 ZK 源 `available=true`，生效类型 `distributed`。 | PASS（登记、落库与消费链） |
| T2.3 ZK 与探测交叉 | 仅将隔离元数据库中的同一临时连接口令改为无效并清空既有探测缓存；S1 `available=false`，S2 `available=true`，最终 `distributed` 且来源 `zk`。真实实例未被写入。 | PASS |
| T3.2 monitordb 时间窗 | 时间窗外抓取 `0` 条；覆盖已存在采集历史的窗口抓取 `42` 条；两条扫描任务均回显并持久化 `time_window_start/end`。 | PASS |
| T3.3 digest 聚合 | 经真实 Proxy 发现 `2` 个 SET，配置后 digest 抓取 `50` 条；关联扫描任务的已落库记录均可见 SET 归属。 | PASS |
| T3.3 processlist 轮询 | 并发执行只读 `SELECT SLEEP(20)`，12 秒轮询抓取 `1` 条；关联扫描任务的脱敏 SQL 记录包含 SLEEP 标识。 | PASS |
| T4.3 声明被探测纠偏 | 在真实分布式端点的**隔离连接配置**中故意声明为集中式；S1 探测为 `distributed`，最终生效 `distributed`、来源 `probed`、冲突标记为真。元数据审核命中 R077、跳过规则数为 0；正式 `/api/v1/audit/sql` 落库同样为 `distributed` / `0`。测试后恢复声明并删除临时连接。 | PASS |

### 3.1 ZK 物理发现边界

真实 ZK 物理发现（T2.1）未能完成，原因明确且可复现：当前执行平台为 Windows，而 `ZKDiscoveryService.discover()` 在 Windows 上按设计强制返回 Mock；同时未提供可使用的 ZK 服务地址、认证信息和 Linux `zkCli.sh` 执行环境。因此：

- T2.2/T2.3 已证明真实 TDSQL 端点上的“登记 → `groupshard` 落库 → S2 判定/保守合并”功能链；
- 不能据此替代真实 ZK 节点扫描、清单与代理列表同步的现场验收；
- 该项按 G 方案的环境规则标记为 **BLOCKED**，不是代码 FAIL。补测所需材料为：可访问的 Linux 节点、可执行的 `zkCli.sh`、ZK 地址与受控认证、目标根路径。

### 3.2 T4.3 的保守方向说明

T4.3 采用“实际分布式、声明集中式”的方向，验证探测阳性结果不会被错误的集中式声明压低，从而避免静默跳过 27 条分布式规则。反向的“实际集中式、声明分布式”会按 R-15 保守合并继续选择分布式，这是宁多报不漏报的预期行为，不属于缺陷。

## 4. 全量回归

执行命令：

```powershell
python -m pytest -q
```

执行配置：独立元数据库、`AUTH_ENABLED=false`（仓库现有全量套件未携带令牌的既定测试契约）、调度关闭、数据脱敏开启。

结果：

```text
1172 passed, 29 skipped, 10 warnings in 161.24s
```

其中：

- 原失败用例 `TestUAT44_TDSQLConnection::test_uat44_04_test_connection` 已通过，确认 `username` 参数兼容；
- 新增长路由审计截断回归用例已通过；
- 29 个跳过项为环境/可选依赖路径；
- 10 条 warnings 为既有 Pydantic 字段名、httpx/Starlette 兼容性及 pytest fixture 弃用提示，未形成失败或异常退出。

曾以 `AUTH_ENABLED=true` 做过一次全量尝试，因大量历史接口测试未携带令牌而出现 401 级联（375 failed、36 errors）。这是测试配置与套件契约不匹配，不计入产品回归；随后按既定测试配置重跑并以本节全绿结果为准。

## 5. 准出意见

建议代码准出：真实 TDSQL 的分布式/集中式/monitordb 连通、时间窗、跨 SET digest、processlist 轮询、实例类型保守纠偏与审核历史均已取得实测证据，且全量回归全绿。

准出附带一项可追溯环境边界：在取得 Linux + ZK 接入材料前，真实 ZK 物理发现不能标注为“现场验收通过”。该限制不影响本次已覆盖的 S2 数据消费与保守合并代码路径。
