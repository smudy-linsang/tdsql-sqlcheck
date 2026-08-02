# TDSQL-SQLCheck v1.6.0.0：ZK 自动发现与原始慢日志真实环境验证报告

| 项目 | 内容 |
|---|---|
| 编写人 | 智能体O |
| 测试日期 | 2026-08-02 |
| 测试阶段 | 开发环境真实连通性验证 + 代码回归 |
| 测试对象 | 实例管理 / ZK 自动发现；慢SQL治理 / 原始慢日志独立子模块；质检发现的两项慢SQL回归 |
| 数据与凭据控制 | 未将真实账号、口令、私钥、主机内网地址、日志内容或连接串写入仓库或本报告 |
| 总体结论 | **代码回归通过；经受控 SSH 隧道的真实 ZK 发现通过；公网 ZK 会话路径仍不通过；原始慢日志真实采集尚缺最小权限前置条件。** |

## 1. 变更与测试原则

本轮只在开发环境实施，遵循失败关闭、最小权限、测试证据可追溯原则：

1. 对真实 ZK 请求，服务不可达、客户端不可用、脚本执行失败或认证失败均返回显式服务不可用，不回退为模拟实例。
2. 模拟数据仅可由部署端受控开关启用；响应带 `is_mock`，模拟结果不能导入连接配置，也不能同步实例形态权威字段。
3. 真实 ZK 账号与口令只从部署机受控文件读取，不经浏览器提交、不置入命令参数、不返回 API 响应。
4. 原始慢日志采集仍限定为 `tdsql_log_reader` 的 SSH 密钥认证 + ForceCommand 导出器；本轮 root SSH 仅用于一次性只读环境核查，未修改集群配置、未下载真实 SQL 正文，也未将 root 作为模块认证方案。

## 2. 已修复的质检回归

### 2.1 扫描批次被单条异常 SQL 中断（P1-A）

`backend/services/scan_service.py` 已改为逐条持久化隔离：

- 单条 SQL 在脱敏或落库失败时登记 `stage=persist` 错误并继续处理后续记录；
- `scan_tasks` 在 `finally` 中落终态，不再因一条异常记录遗留 `running`；
- `total_fetched` 记录源端抓取数，`total_analyzed` 记录成功落库数；批次存在错误时终态为 `failed`，从而使运行人员可见并可复核。

回归用例构造“未闭合字符串 + 正常 SQL”两条 digest 数据，验证正常 SQL 仍入库、任务由 `running` 转为 `failed`，计数为抓取 2 / 成功 1。

### 2.2 网关日志截断后再脱敏导致记录丢失（P1-B）

`analyze_gateway_log.py` 与 `interf_deep_analysis.py` 均改为：

1. 先对完整 SQL 做 URL 解码替换与字面量脱敏；
2. 再对已脱敏模板做展示长度截断。

新增边界测试覆盖字面量跨越旧 200/800 字符预截断点的情况，结果仍生成不含字面量的聚合模板，不会静默返回空模式。

## 3. 自动化测试证据

| 测试项 | 命令/范围 | 结果 |
|---|---|---|
| P1-A、P1-B 与 ZK 定向回归 | `python -m pytest -q tests/test_scan_service_resilience.py tests/test_gateway_log_sql_masking.py tests/test_zk_discovery.py` | **14 passed** |
| Python 全量回归 | `python -m pytest -q` | **1198 passed, 29 skipped, 0 failed** |
| 变更语法 | `python -m py_compile`（扫描服务、两个网关分析模块） | 通过 |
| 差异格式 | `git diff --check` | 通过 |

全量运行中的 10 条 warning 为既有依赖弃用及 Pydantic 字段名提示，未产生测试失败。

## 4. 真实 ZooKeeper 验证

### 4.1 已确认项

在临时开放的开发环境网络窗口内，对三个受控 ZK 入口完成 TCP 连通性探测：端口均可建立 TCP 连接。直接通过公网入口使用 Linux 容器中的 ZooKeeper 3.8 与 3.9 客户端分别发起客户端会话，结果一致：

- TCP 已建立；
- 未进入 `SyncConnected`；
- 服务端约数秒后关闭会话；
- 对根节点的读取以 `ConnectionLoss` 失败。

目标 ZK 节点日志未出现该外部客户端会话，说明公网入口后的 NAT/L4 路径没有将其完整转交给 ZK 进程；仅放通安全组不足以保证 ZooKeeper 协议可用。

随后通过临时、加密的 SSH 本地隧道直达一台开发 ZK 节点的本机 client port：

- ZooKeeper 3.8 客户端出现 `SyncConnected`，可读取 `/tdsqlzk` 和 `sets`；
- 修复非交互 `zkCli` 提示符与结果同一行时的解析兼容后，`tdsql_inventory.sh` 真实运行成功；
- 脱敏汇总发现 **3** 个有效实例：**2 个 `noshard`（集中式）**、**1 个 `groupshard`（分布式）**，与开发控制台展示的两类实例一致；
- 新增默认 Python `kazoo` 驱动后，通过相同真实隧道直接调用产品发现服务，返回同一 3 条记录；密码未输出。

因此，ZK 数据结构、认证、两种实例形态映射、Shell 兼容路径和默认 Python 驱动均已得到真实环境证实。正式/常态部署仍需为 CheckSQL 到 ZK 建立经批准的完整会话路由，不能依赖 root SSH 隧道或公网 2118 入口。

### 4.2 本次代码的失败关闭验证

单元/API 回归覆盖以下行为且通过：

- 未配置、端口不可达或脚本失败的真实发现请求返回 503，不返回虚构实例；
- 浏览器不再提交 ZK 地址、账号或口令；
- 真实结果中的口令不出现在 API 响应；
- 服务器端短时令牌注册真实发现结果；
- Mock 结果有标识、不能导入、不能触发 `sync_instance_kinds`；
- ZK 私网地址经部署配置映射为 CheckSQL 可连接地址；多入口按顺序尝试。

### 4.3 真实通过门槛与所需协作

公网直连修复时，请在任一 ZK 节点本机由有权限的运维人员执行以下只读检查，并提供**脱敏后的输出**（不要提供账号口令）：

```bash
grep -nE 'clientPort|clientPortAddress|maxClientCnxns|authProvider|requireClientAuthScheme|secureClientPort' \
  /data/application/zookeeper/conf/zoo.cfg

grep -Ei 'connection|close|refused|auth|session' <zookeeper-log-file> | tail -n 100
```

日志时间范围应覆盖本次公网客户端尝试时刻。修复后应再执行不经 SSH 隧道的产品路径复测；此问题不影响本轮已完成的受控隧道真实发现结论。

## 5. 原始慢日志真实采集

已从两个实际 Proxy 节点以只读方式核对到慢日志模式：

```text
/data/tdsql_run/<proxy-port>/gateway/log/slow_sql_instance_<proxy-port>.*
```

集中式与分布式入口端口均有对应目录；文件属主/组为 TDSQL 运行账户和用户组，权限为 owner/group 可读写、其他不可读。一个非空历史样本已只读取 `# Time`、`# Query_time` 两类头部行，确认符合 `tdsql_mysql_slowlog_v1` 解析特征；没有下载或记录 SQL 正文。另一节点当日文件为空，属于当前采集状态，不能替代带数据样本。

尚缺以下受控前置条件，故不能执行真实采集：

1. 每个实际日志节点的 `tdsql_log_reader` 专用账户及其仅允许 CheckSQL 部署机使用的 SSH 公钥；
2. 该账户的 ForceCommand 导出器、禁止交互 Shell/转发/SCP 的 SSH 限制；
3. 将上述经核对的实际慢日志 glob 写入导出器白名单，并为 `tdsql_log_reader` 配置只读 ACL；
4. 对应节点的主机密钥指纹/`known_hosts`，以及日志节点 CPU 架构。

这些信息必须按部署设计配置为 Secret 引用与受控目录 ACL，不能放入页面、数据库明文字段、Git 或测试报告。root 密码不是原始慢日志模块的可接受长期认证方式，本轮也未使用它。

在以上条件就绪后，依次执行：Probe → 单节点手工采集 → 跨节点重复文件防护 → 日志轮转（rename/copytruncate）→ 受控慢 SQL 的事件时间核对 → 积压追赶与调度复测。任一 Probe 失败时采集源保持禁用。

## 6. 结论与后续动作

1. P1-A/P1-B 已修复并由定向、全量回归验证，可随本次提交进入复测。
2. ZK 自动发现代码现已失败关闭，消除了不可达时伪造成功并写入实例形态权威字段的风险。
3. 真实 ZK 发现经受控隧道通过；公网入口的会话级路由问题应按第 4.3 节修复后复测，不得以 TCP 探测替代。
4. 原始慢日志独立模块的本地/模拟测试已具备，真实目录模式和格式已确认；真实采集必须等待最小权限读者账户、导出器和只读 ACL 到位。

本报告不构成生产准出或真实环境验收结论。
