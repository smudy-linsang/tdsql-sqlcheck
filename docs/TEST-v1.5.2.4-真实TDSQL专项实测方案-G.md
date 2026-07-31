# TEST-v1.5.2.4 真实 TDSQL 专项实测方案（G）

| 项 | 内容 |
|---|---|
| 被测版本 | v1.5.2.4 + 缺陷修复批（`main @ 5fba7ad` 及之后，含 FIX-1~FIX-6） |
| 执行人 | 智能体 G（具备真实 TDSQL 网络可达性的一方） |
| 背景 | A 的独立测试（`docs/v1.5.2.4_独立测试报告_A.md` §6）因容器出网策略无法连通任何真实 TDSQL，凡依赖真实实例的路径**未实测**；开发侧修复批（`docs/v1.5.2.4_缺陷修复方案.md` §11）同样只在本机 MySQL 8.0 上验证。本方案补齐这块空白 |
| 范围 | ①实例类型多源判定 ②ZK 发现 ③monitordb/digest/processlist 扫描与时间窗 ④DISTRIBUTED 规则适用域 ⑤本批修复项的真实环境复核 |
| 不在范围 | 规则引擎逻辑正确性（1156 条套件已覆盖）、升级迁移（A 报告 R2 已 PASS）、前端 |

---

## 0. 前置条件（开测前逐项确认，缺一不开测）

| # | 条件 | 确认方式 |
|---|---|---|
| 0.1 | 构建包含全部 7 个修复提交（`ce493de`…`5fba7ad`） | `git log --oneline -8` 比对 |
| 0.2 | `bash deploy/preflight_check.sh` 全 PASS，**特别是第 7 节 "backend.main 可导入"**（该节为本批新增，尚未在任何 Linux 机上跑过，本次是首验） | 预检输出 |
| 0.3 | 一个**分布式**实例 + 一个**集中式**实例已在"实例管理"注册（凭据用 DBA 轮换后的新口令；轮换前的旧口令已泄露，不得再用） | 实例列表 |
| 0.4 | 分布式实例的 monitordb（15001/tdsqlpcloud_monitor）连接信息已配置 | `GET /api/v1/tdsql/connections/{id}/probe` 返回 `ok:true` |
| 0.5 | 可访问 ZK 管控面（zkCli 可执行、有 `tdsqlsys_zk` 认证） | 手工 `zkCli.sh ls /tdsqlzk` |
| 0.6 | `AUTH_ENABLED=true`，已获 admin 令牌（下文 curl 均需 `-H "Authorization: Bearer $TOK"`，不再重复标注） | 登录接口 |

**结果记录要求**：每个用例记录「请求原文 / 响应原文（截断至关键字段）/ 判定 PASS·FAIL / 备注」。
任何 FAIL 不要现场绕，**原样记录后继续跑完全部用例**（一次跑全，避免多轮往返）。

---

## T1 实例类型多源判定（PR001~PR004，V1.5.1 判定链）

**判定链**：S0 管理员锁定 > S1 Proxy层SQL探测 / S2 ZK管控面 / S3 人工声明 保守合并
（除锁定外，任一源判分布式即取分布式——见规约 R-15）。

### T1.1 分布式实例探测

```
POST /api/v1/tdsql/connections/{分布式conn_id}/probe-instance-type
```

| 检查点 | 通过标准 |
|---|---|
| 最终结论 | `distributed` |
| 逐源明细 | 响应含每个源的 `available / value / reason`，S1 探测源 `available:true` 且 `value:distributed` |
| 落库 | `tdsql_connections.detected_instance_type='distributed'`，`instance_type_detected_at` 为本次时间 |

### T1.2 集中式实例探测

同上，对集中式 conn_id。

| 检查点 | 通过标准 |
|---|---|
| 最终结论 | `centralized`，且 `conflict:false`（前提：声明值也是集中式） |
| 关键反证 | **不得出现**"探测不可用却硬判 centralized"——若 S1 不可用，结论必须来自 ZK 或声明，响应 message 会写明来源 |

### T1.3 全源异常兜底（R-15 核心场景）

制造探测失败（例如临时用一个网络不可达/口令错误的注册实例），再探测：

| 检查点 | 通过标准 |
|---|---|
| 不抛 5xx | 探测失败是正常业务分支，接口返回 200 且下沉至声明值 |
| 兜底方向 | 结论 = 声明值；若声明为分布式则按分布式（宁多报不漏报），**绝不因"探不到"而判 centralized** |

### T1.4 管理员锁定优先级

```
PUT /api/v1/tdsql/connections/{分布式conn_id}/instance-type-lock
   {"locked": true, "instance_type": "centralized", "reason": "G实测-锁定优先级验证"}
POST /api/v1/tdsql/connections/{分布式conn_id}/probe-instance-type
```

| 检查点 | 通过标准 |
|---|---|
| 锁定生效 | 结论 = `centralized`，source = locked，message 明示"锁定优先于一切自动判定源" |
| 审计 | 操作日志中有锁定记录及理由 |
| **恢复现场** | 测完立即解锁（`{"locked": false}`）并复测一次回到 `distributed`——锁成 centralized 会关掉 27 条规则，忘了解锁就是生产事故 |

### T1.5 诊断采集（判据留档，供换版本时复测）

```
POST /api/v1/tdsql/connections/{每个conn_id}/probe-diagnostics   {"sample_table": "<库中任一小表>"}
```

通过标准：返回 §8.3 采集清单原样输出，`endpoint / declared_instance_type / zk_instance_kind` 三个上下文字段齐全。**响应原文完整存档**（这是判据实测的原始证据，A 的 v1.5.1 复测靠的就是这份数据）。

---

## T2 ZK 发现与形态同步

### T2.1 发现

```
POST /api/v1/tdsql/discover
{"zk_server":"<ip:2118>", "zk_auth_user":"tdsqlsys_zk", "zk_auth_password":"<口令>",
 "zk_root":"/tdsqlzk", "zkcli_path":"/data/application/zookeeper/bin/zkCli.sh"}
```

| 检查点 | 通过标准 |
|---|---|
| 实例清单 | 返回集群内实例，host/port/status 与管控台一致 |
| 实例形态 | `instance_kind` 为原始形态 `noshard`（集中式）/ `groupshard`（分布式），不得为空（若为空，记录 zkCli 脚本版本——旧脚本无 `--with-type`） |
| 形态回写 | 已注册实例的 `zk_instance_kind / zk_instance_id / zk_synced_at` 被同步更新（查库或实例详情接口） |

### T2.2 注册 + 形态落库

从发现结果中挑一个未注册实例走 `POST /api/v1/tdsql/discover/register`。
通过标准：注册成功且 `kind_synced:true`；随后对它跑 T1.1/T1.2，S2 ZK 源应 `available:true`。

### T2.3 ZK 与探测交叉（保守合并实证）

对一个 ZK 判 `groupshard`（分布式）的实例，若 S1 探测因故无结论：最终结论仍须为 `distributed`（S2 单源足以定分布式）。记录一例即可。

---

## T3 monitordb 扫描与时间窗（集群级慢SQL数据源）

> 语义基线：monitordb 的 `timestramp` 字段是**采集时刻**（非 SQL 执行时刻），
> 时间窗过滤按采集时刻圈定。见《集群级慢SQL数据源(monitordb)接入设计说明书》。

### T3.1 基本抓取

先在分布式实例上制造几条慢 SQL（`SELECT SLEEP(2)` 或压测脚本），等待 monitordb 采集周期后：

```
POST /api/v1/tdsql/slow-queries/fetch
{"source":"monitordb", "connection_id":"<分布式conn_id>", "limit":50, "min_time":0.5,
 "task_name":"G实测-monitordb基本抓取"}
```

| 检查点 | 通过标准 |
|---|---|
| 任务落库 | `GET /api/v1/slow-queries/scan-tasks` 可见本任务，状态完成，记录数 > 0 |
| 字段完整 | 记录含 `client_user / client_host`（monitordb 独有）、`rows_affected`（DML 应 >0，digest 源恒 0） |
| 明细可开 | `GET /api/v1/slow-queries/{id}` 正常返回分析结果 |

### T3.2 时间窗过滤

指定一个**不含**上述慢 SQL 采集时刻的窗口（如昨天）再抓一次：结果应为 0 条或明显减少；
再指定**恰好覆盖**采集时刻的窗口：应能抓到。两次任务的 `time_window_start/end` 在任务详情中如实回显。

### T3.3 digest 与 processlist 对照

```
{"source":"digest", ...}          → 应正常返回聚合摘要（Proxy 层，多 SET 自动聚合，set_id 有值）
{"source":"processlist", "poll_duration":15, ...}   → 轮询期间在实例上手工跑一条 SLEEP(20)，应被捕获
```

### T3.4 跨 SET（仅分布式）

`GET /api/v1/slow-queries/cross-set-analysis?scan_task_id=<T3.1任务id>`：
返回各 SET 分布，不抛错；`GET /api/v1/slow-queries/set-ids` 含真实 SET 名。

---

## T4 DISTRIBUTED 规则适用域（27 条仅分布式规则）

**这是 v1.5 的核心承诺，必须在真实集中式实例上闭环。**

### T4.1 集中式实例：27 条规则不触发

对**集中式**实例走带实例上下文的审核（元数据审核入口）：

```
POST /api/v1/tdsql/audit/with-metadata
{"connection_id":"<集中式conn_id>", "sql":"CREATE TABLE g_t1 (id INT PRIMARY KEY, name VARCHAR(50)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"}
```

（该 SQL 在分布式下必中 R077——未声明 SHARDKEY/BROADCAST。）

| 检查点 | 通过标准 |
|---|---|
| R077 不触发 | 违规列表**无 R077/R020/R053** 等 DISTRIBUTED 域规则 |
| 口径自证（响应体） | `audit_result.instance_type='centralized'`、`instance_type_source` 非空、`skipped_rules_count=27`、`scope_notice` 有跳过说明 |
| 落库留痕（另测一笔） | 走正式审核入口 `POST /api/v1/audit/sql`（带同一 `connection_id`）后，`audit_history` 最新记录的 `instance_type='centralized'`、`skipped_rules_count=27` |

### T4.2 分布式实例：同一 SQL 必须触发

同一条 SQL 对分布式 conn_id 审核：R077 必须出现（ERROR）。
两次结果并排存档——这就是"同一把尺、不同实例、不同适用域"的实证。

### T4.3 声明被探测纠偏（保守方向）

把集中式实例的声明临时改为 `is_distributed=1`（或反向），复跑 T4.1：
最终生效类型应按判定链（锁定>探测/ZK/声明保守合并）而非单看声明，`audit_history.instance_type` 记录的是**实际生效值**。测完恢复声明。

---

## T5 本批修复项的真实环境复核（抽核，非全量）

| # | 项 | 操作 | 通过标准 |
|---|---|---|---|
| 5.1 | P1-02 全新库首启 | 在内网元数据库上建全新空库，指向它启动服务**一次** | 执行 `docs/v1.5.2.4_缺陷修复方案.md` §3.4 的 6 列 SQL → 恰好 6 行；admin 首次登录→改密 → **200** |
| 5.2 | P0-01 接口 | 对任一慢SQL记录 `PUT /api/v1/slow-queries/{id}/status` `{"status":"optimized"}` | 200，且传非法值返回 400（非 422 查询参数错） |
| 5.3 | R-17 预检 | 前置条件 0.2 已覆盖 | preflight 第 7 节 PASS |
| 5.4 | P2-04 文件审核 | 上传一份含截断 DDL 的 .sql（样例取 `tests/test_v2_syntax_truncation.py` 中 t1/t2/t3 内容） | 审核结果 3 条：t1/t3 通过，t2 报 E999_SYNTAX_ERROR |
| 5.5 | R043 | 对任一实例审核 `UPDATE t_order o, t_user u SET o.status=1 WHERE o.cust_id=u.id` | 命中 R043 |

---

## 6. 判定与回报

**放行标准**：
- T1、T4 全部 PASS —— 硬性（错判实例类型 = 静默关 27 条规则，不可放行）；
- T2、T3 允许个别用例因环境（zkCli 版本旧、monitordb 采集周期）标注 BLOCKED，但须写明原因；
- T5 全部 PASS —— 硬性（本批修复的现场复核）。

**回报物**：
1. 按用例编号的结果表（PASS/FAIL/BLOCKED + 证据摘要）；
2. T1.5 诊断采集原文（每实例一份）；
3. 全部 FAIL 项的请求/响应原文。

命名 `docs/REPORT-v1.5.2.4-真实TDSQL专项实测结果-G.md` 提交回仓库。

**注意事项**：
- T1.4 锁定、T4.3 改声明均为**破坏性配置操作**，测完必须恢复原状并在结果表中注明"已恢复"；
- 全程使用 DBA 轮换后的新凭据，任何文档/脚本中不得出现明文口令（守卫用例 `tests/test_no_hardcoded_secrets.py` 会拦）。
