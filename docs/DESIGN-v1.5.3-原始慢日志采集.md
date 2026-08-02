# TDSQL-SQLCheck v1.5.3 设计说明书

## 「慢SQL治理 / 原始慢日志」独立子模块

| 项 | 内容 |
|---|---|
| 版本 | v1.5.3.0（实施后设计基线） |
| 基线 | `main` @ `68954e5`（含 A 对初版 `33947f9` 的设计评审） |
| 文档类型 | **概要设计 + 详细设计 + 接口设计 + 数据库设计** |
| 状态 | 已完成开发自测；外部日志格式、账号和目录仍须按 §16 的实测门禁确认后才能启用采集 |
| 编制日期 | 2026-08-02 |
| 施工规约 | [`GUIDE-团队施工规约.md`](GUIDE-团队施工规约.md)，尤其是 R-02~R-05、R-08~R-10、R-11~R-17 |
| 关联模块 | `慢SQL治理`；新增子模块，**不复用**现有“扫描任务 / 慢SQL记录 / 网关日志分析报告”的数据表和路由 |
| 修订 | 2026-08-02：接收 `REVIEW-v1.5.3-原始慢日志采集设计评审_A.md` 的 B1~B3、C1~C4 及 §4 小项；修订内容见 §4.9 |

---

## 0. 执行摘要

本设计新增「**原始慢日志**」子模块，用于持续收集 **TDSQL Proxy / Gateway 已写出的逐条慢 SQL 日志事件**。它解决当前三个数据源的固有边界：

| 既有来源 | 能回答什么 | 不能回答什么 |
|---|---|---|
| `monitordb` | 管控面采集到的聚合慢 SQL | 单条 SQL 实际何时记录；同一指纹的每次执行 |
| `digest` | 当前 `performance_schema` 累计摘要 | 任意历史时间窗口 |
| `processlist` | 轮询期间仍在执行的实时 SQL | 已结束的慢 SQL 历史 |

新增来源的定位是：

> **以 Proxy 慢日志中的一条完整日志块为一条事件，按日志记录时间检索，而不是按采集时间或累计摘要时间检索。**

实现主路径为 **CheckSQL 部署机主动经内网 SSH 只读拉取 Proxy / Gateway 主机日志**。客户端固定使用经部署前置门禁验证的 OpenSSH CLI，远端使用静态单文件导出器；不依赖赤兔未公开 Web 接口；不向 TDSQL 写入数据；不开放公网 SSH；不使用 root、口令认证、`sshpass`、SCP 上传脚本或任意远程 Shell。

赤兔是否存在本版本私有的、受支持的日志 API，尚无可验证的官方接口契约。本版本为未来的官方 API / Elasticsearch 适配器预留 `transport` 扩展点，但**只实现 `ssh_exporter_v1`**；不得逆向调用赤兔页面的内部 HTTP 请求作为生产接口。

---

## 1. 背景、事实与问题边界

### 1.1 当前工程事实

1. `backend/services/tdsql_connector.py` 已明确：分布式实例 SET 节点的 `mysql.slow_log` 不记录慢日志，慢日志由 Proxy 层统一管理；现有扫描改读 digest / monitordb / processlist。
2. `backend/services/scan_service.py` 已把 `digest` 和 `processlist` 定义为无历史时间窗的数据源；只有 `monitordb` 的 `timestramp` 能筛选，但该字段是赤兔采集时刻，不是每条 SQL 的执行时刻。
3. `backend/services/gateway_log_analysis/analyze_gateway_log.py` 已有对 MySQL 风格慢日志块的低层解析能力，能识别 `# Time`、`# User@Host`、`# Backend_host`、`# Thread_id`、`# Query_time`、`Rows_sent`、`Rows_examined` 和后续 SQL 文本。
4. 旧的 `gateway_collect.sh` / `interf_batch_analyze.sh` 是一次性离线分析工具，包含 root、密码、上传远程脚本等旧做法，只能作为“日志命名和解析经验”的参考，**不得**用于本模块。

### 1.2 外部资料结论

公开的 TDSQL 私有云手册确认赤兔可在“实例详情 → 日志管理”查看 Proxy 慢查询日志，并存在接口授权机制；其公开接口目录未确认慢日志导出 API。腾讯云托管 TDSQL 的 `DescribeDBLogFiles` / `DescribeDBSlowLogs` 为另一套需云实例 ID 与 CAM 授权的服务，其中后者返回聚合慢日志信息，不可当作本私有云逐条事件接口。

**设计结论**：在取得“本环境赤兔接口文档中明确的日志 API 契约”之前，SSH 只读采集是可审计、可验证且不依赖未公开实现的主路径。

### 1.3 本设计解决与不解决的事项

| 范围 | 本版本结论 |
|---|---|
| Proxy / Gateway 原始慢日志的定时、增量、去重采集 | ✅ 实现 |
| 按日志记录时间的逐条检索、筛选、导出 | ✅ 实现 |
| 开发人员从事件进入既有 EXPLAIN 分析 | ✅ 提供跳转上下文；仍使用既有安全校验 |
| 自动从 ZK、赤兔或数据库猜测日志主机和目录 | ❌ 不做；主机、端口、路径均由管理员配置 |
| 解析未实测的 `interf` / `slow_sql` 格式 | ❌ 不假设；先走 §16 格式验收门禁 |
| 调用未公开的赤兔页面内部接口 | ❌ 不做 |
| 上传脚本到 TDSQL 主机、远程执行 Python、远程写文件 | ❌ 不做 |
| 代替既有 monitordb、digest、processlist 扫描 | ❌ 不替代；四类数据源口径分开呈现 |

---

## 2. 术语与时间口径

| 术语 | 定义 | 绝不等同于 |
|---|---|---|
| 原始慢日志事件（Event） | 一个被解析成功的完整 Proxy 慢日志块 | 摘要聚合行 |
| 日志记录时间 `event_time` | 日志块 `# Time` 字段；通常是 SQL 完成后写入日志的时间 | SQL 开始时间、CheckSQL 采集时间 |
| 采集时间 `collected_at` | CheckSQL 收到并成功写入事件的时间 | SQL 执行时间 |
| 源节点（Node） | 实际保存 Proxy / Gateway 日志的一台主机 | ZK 节点、SET 节点或数据库连接端点 |
| 远端源键 `remote_source_key` | 目标主机导出器中一项已批准日志白名单的标识 | 路径或可执行 Shell 命令 |
| 游标（Cursor） | 某个文件代际中最后一个**完整且已提交**日志块末尾的字节偏移 | 文件行号或采集时间 |

页面、接口和报告必须显示：

```text
时间口径：Proxy 慢日志记录时间（非采集时间；不代表 SQL 开始时间）
```

若一条日志没有可解析的 `# Time`，该块记为 `parse_error`，不得伪造为当前时间，也不得写入可检索事件表。

---

## 3. 概要设计

### 3.1 模块边界与页面位置

在左侧导航“慢SQL治理”下新增独立项：

```text
慢SQL治理
├── 扫描任务                    （既有：monitordb / digest / processlist）
├── 慢SQL记录                  （既有：扫描结果与人工录入）
├── 原始慢日志                  （新增：本设计）
├── EXPLAIN分析                 （既有）
└── 扫描计划                    （既有）
```

路由页键为 `slow-raw-log`，API 前缀为 `/api/v1/raw-slowlogs`，数据库表全部以 `slow_log_` 开头。**禁止**把原始事件写入 `slow_queries`、把采集运行写入 `scan_tasks`，也禁止把页面塞入“网关日志分析”子页面。

理由：它们的粒度、时间含义、删除关系和安全等级不同；混表会让“一个聚合指纹”和“某次实际执行”看似同一条数据，造成不可见的时间语义错误。

### 3.2 总体数据流

```mermaid
flowchart LR
    A["慢SQL治理 / 原始慢日志\n管理员配置源节点"] --> B["APScheduler\nleader + 源级租约"]
    B --> C["SSH 严格主机指纹校验\n专用 tdsql_log_reader"]
    C --> D["目标 Proxy/Gateway 主机\nForceCommand 导出器"]
    D --> E["只读 NDJSON/字节块\n受白名单路径约束"]
    E --> F["完整块边界识别\n格式解析、SQL 脱敏、指纹"]
    F --> G["事务：事件去重 + 游标推进"]
    G --> H["slow_log_events\n按日志记录时间查询"]
    H --> I["开发人员：筛选、导出、EXPLAIN"]
```

### 3.3 组件职责

| 组件 | 责任 | 不得承担 |
|---|---|---|
| `RawSlowLogSourceService` | 配置校验、源/节点状态、源级租约 | 直接执行任意 SSH 命令 |
| `RawSlowLogCollector` | 固定协议的 SSH 通信、清单和字节块读取 | 持久化明文私钥、接受路径作为 Shell 参数 |
| `SlowLogBlockParser` | 完整块切分、字段解析、脱敏和指纹 | 推断未知日志格式为“成功” |
| `RawSlowLogRepository` | 事件、游标和运行记录的原子保存 | 调用 TDSQL 业务库 |
| `RawSlowLogScheduler` | 到期源调度、已有 leader 租约复用、失败重试 | 绕过源级锁并发采同一源 |
| `raw_slowlog.py` API | RBAC、参数校验、任务查询/触发 | 返回私钥、完整主机信息给非管理员 |
| 前端 `slow-raw-log` | 呈现独立事件、运行健康、配置入口 | 把“日志记录时间”标为“执行开始时间” |

---

## 4. 核心设计决策

### D1：所有内网信息配置化，仓库零环境地址

主机名/IP、SSH 端口、节点别名、Proxy 端口、日志目录、文件模式、时区、抓取阈值和采样上限均通过管理员配置录入；仓库、测试夹具、示例文件和错误信息不得包含真实内网地址、真实路径或密钥。

`connection_id` 仅作为与既有“实例管理”登记项的关联键，**不**据此自动推导日志主机。

### D2：远端路径必须“双层配置”，而非由应用任意指定

管理员需要能配置慢日志目录，但中央配置中的 `declared_path_template` 仅用于展示、审计和一致性校验。真正允许读取的绝对路径由每台目标主机的受限导出器白名单配置决定。

```text
CheckSQL 数据库：node_key + remote_source_key + declared_path_template
                                  │
                                  ▼
目标主机导出器：remote_source_key → approved absolute path glob(s)
```

客户端 SSH 请求只传 `remote_source_key` 和游标，**不传路径、通配符、文件名或 Shell 参数**。这条边界防止“拥有 CheckSQL 配置权限的人”借采集账户读取目标主机任意文件。

### D3：SSH 采用受限导出器协议，不使用普通 Shell/SCP

远端账户名固定建议为 `tdsql_log_reader`，公钥在目标主机上使用 `restrict` / `no-pty` / 禁止端口转发等限制，并以 `ForceCommand` 指向单一导出器。CheckSQL 端以 `subprocess.Popen(argv, shell=False)` 调用 OpenSSH CLI，参数由固定模板与经正则验证的配置组成；导出器只实现 `probe` 和 `pull` 两种 JSON 请求，拒绝原始 SSH 命令。

禁止项：root 账户、口令认证、`sshpass`、`scp` 上传脚本、`sudo` 任意命令、`bash -c` 拼接、关闭 `StrictHostKeyChecking`。

选择 OpenSSH CLI 而非新增 Python SSH 库的原因是本项目离线发布支持 `x86_64` / `aarch64` 和多 Python ABI；不引入 Paramiko/AsyncSSH 及其可能含原生扩展的传递闭包。OpenSSH 客户端是**明确的操作系统依赖**，不是隐含假设；详见 §8.1 与 §8.4 的交付门禁。

### D4：游标按文件身份和字节偏移推进，保证至少一次读取、恰好一次入库

“恰好一次”不能靠网络调用保证，必须由数据库唯一键实现：

```text
origin = (source_node_id, file_identity, generation, record_start_offset)
```

该组合在 `slow_log_events` 上唯一。每次在同一数据库事务中：先 `INSERT IGNORE` 事件，再更新该文件游标。网络中断或进程重启最多重读数据，唯一键保证不会重复入库。游标还保存“末尾锚点”哈希；续读前必须验证该偏移前的原始字节仍未改变，防止 copytruncate 后在一个轮询周期内快速重新长回而从错误中段续读。

### D5：最后一条不完整块绝不推进游标

当字节块末尾没有完整的 SQL 日志块边界时，游标保持在该块起点；下个周期从同一位置重读。宁可延迟一个轮询周期，也不得把半条 SQL 当完整事件或静默丢弃。

### D6：SQL 只保存脱敏模板，不保存完整原始日志

采集内容只在内存中存在到“解析并脱敏完成”为止。持久化字段为 `sql_template`、`sql_fingerprint` 和受控结构化指标；不保存原始字节块、连接私钥、业务参数值或完整日志文件。

新增唯一规范实现 `backend/services/sql_masking.py`：`mask_sql_literals()`、`fingerprint_masked_sql()` 和 `truncate_masked_sql_for_storage()`。既有 `slow_query_service.py` 的 `FingerprintEngine.normalize_for_display()` 和两份网关日志 `normalize_sql()` 必须改为调用该公共实现，禁止再产生第四份规则。

指纹必须基于**完整脱敏 SQL**计算 SHA-256，绝不能先截断再哈希；持久化展示文本默认最多 8 KiB，超过时保存前缀并写 `sql_template_truncated=1`、`sql_template_original_bytes`。页面和导出必须显式显示“SQL 模板已截断”。`TEXT` 的 64 KiB 是数据库类型上限，不是业务侧可无限使用的展示上限。脱敏失败时该事件进入运行错误计数，默认不落库，不能以明文降级。

### D7：调度在 CheckSQL 服务内运行，复用已有多副本租约

新增任务纳入现有 APScheduler，而不是依赖某台运维主机手工 crontab。调度器先取得既有 `scheduler_lease`，再按 `slow_log_sources.lease_*` 取得源级租约；因此多 worker、多容器副本或异常接管时不会同时采同一源。单次运行在 `max_run_seconds` 预算内连续读取多批，直到追平或预算耗尽；不能把 `max_events_per_batch` 错当成每轮绝对上限而永久积压。

### D8：未证实的日志格式不得进入生产解析器

现有解析器可作为候选复用，但“文件名叫 `interf`”不等于“它是 MySQL 风格 Proxy 慢日志”。每一个 `parser_profile` 必须在 §16 的实测门禁中证明其字段和时间口径；未通过时源状态只能为 `invalid_format`，不可启用定时采集。

### D9：跨节点重复在启用前机械拒绝

Probe 必须记录远端 SSH 主机密钥指纹、存储身份和文件身份。启用节点前，服务端检查同一源中是否已有启用节点出现相同 `(ssh_host_key_fingerprint, file_identity)`，或相同 `(storage_identity, file_identity)`；命中即拒绝启用，要求管理员消除重复配置。第二组用于发现共享存储/NFS 场景，不能只依赖人工勾对。

### D10：逐条事件采用短保留期 + 可控分批清理

逐条执行事件的量级不能套用聚合 `slow_queries` 的 180 天策略。本版默认保留 30 天，事件表按 `event_time` 建组合索引，使用独立事务、有限批次的 `DELETE ... LIMIT` 清理；不在首版强行引入会破坏现有唯一去重键的 MySQL 分区约束。容量超出 §13 的“典型档”时，必须先评估专门的冷热分层/分区演进，不得静默上调保留期。

### 4.9 评审意见采纳记录

| 评审项 | 处理 | 落点 |
|---|---|---|
| B1 离线 SSH 依赖 | 接收；改为 OpenSSH CLI，无新增 Python SSH 依赖；新增部署门禁 | D3、§8.1、§8.4、§15 |
| B2 导出器无交付物 | 接收；确定静态 Go 单文件、版本协商、随包分发、安装/升级/回滚 | §8.4、§11、§15 |
| B3 容量/保留不可用 | 接收；30 天默认、容量三档、独立事务分批删、容量准入 | D10、§13、§15 |
| C1 copytruncate 快速回长 | 接收；游标锚点哈希和协议校验 | D4、§6、§8.2、§8.3、U17 |
| C2 脱敏/指纹不统一 | 接收；新增公共脱敏模块、完整模板哈希、截断标记 | D6、§6、§9、U19 |
| C3 跨节点重复 | 接收；Probe 文件身份表和启用前冲突拒绝 | D9、§6、§9、U18 |
| C4 吞吐追赶不可见 | 接收；批次与运行预算分离、积压指标/告警 | D7、§9、§13、U20 |
| §4 小项 | 接收 | §6.3、U16 |

---

## 5. 配置设计

### 5.1 配置分层

| 层级 | 存放位置 | 内容 | 是否含秘密 |
|---|---|---|---|
| 平台源配置 | CheckSQL 元数据库 | 关联实例、节点别名、地址、端口、期望路径、抓取策略、密钥引用名 | 否 |
| 部署秘密 | CheckSQL 容器/主机的 Secret 挂载 | 私钥文件、`known_hosts` | 是，不入数据库、不进日志 |
| 远端导出器配置 | 每台 Proxy/Gateway 的 `/etc/tdsql-sqlcheck/slowlog-exporter.d/*.json` | 已批准的绝对路径、文件模式、最大返回量 | 否（但属于基础设施配置） |
| SSH 服务配置 | 目标主机 sshd / `authorized_keys` | 专用账户、公钥、强制命令与来源 IP 限制 | 否 |

### 5.2 平台源配置字段

#### 源（`slow_log_sources`）

| 字段 | 示例占位值 | 规则 |
|---|---|---|
| `source_key` | `sit_proxy_slowlog` | `^[a-z][a-z0-9_-]{2,63}$`；全局唯一；创建后不可改 |
| `connection_id` | `<已登记实例 ID>` | 必填；仅关联，不能自动发现节点 |
| `display_name` | `SIT 分布式实例 - 原始慢日志` | 1~128 字符 |
| `transport` | `ssh_exporter_v1` | 本版唯一可选值 |
| `timezone` | `Asia/Shanghai` | IANA 时区；解析 `# Time` 使用它 |
| `poll_interval_seconds` | `60` | 30~600 秒；默认 60 |
| `max_batch_bytes` | `8388608` | 64 KiB~16 MiB；默认 8 MiB / 节点 / 次 |
| `max_events_per_batch` | `2000` | 1~10000；一次协议批次的事件上限，不是整轮上限 |
| `max_run_seconds` | `25` | 5~120 秒；本轮连续追赶的时间预算 |
| `lag_alert_seconds` | `600` | 60~3600 秒；未读积压超过此阈值须标红并告警 |
| `initial_position` | `tail` | `tail` / `lookback`；首次上线默认 `tail` |
| `initial_lookback_seconds` | `300` | 仅 `lookback` 有效，60~86400 秒 |
| `min_query_time_ms` | `1000` | 0~3600000；平台二次筛选，不改变远端日志生成阈值 |
| `credential_ref` | `sit_proxy_reader_v1` | 仅引用名，不含路径/密码；正则同 `source_key` |
| `known_hosts_ref` | `tdsql_proxy_hosts_v1` | 仅引用名；必须启用严格主机密钥校验 |
| `enabled` | `false` | 新源创建后固定为 `false`，只有 probe 通过才允许启用 |

#### 节点（`slow_log_source_nodes`）

| 字段 | 示例占位值 | 规则 |
|---|---|---|
| `node_key` | `proxy_a` | 源内唯一；`^[a-z][a-z0-9_-]{1,63}$` |
| `display_name` | `Proxy A` | 展示名，不含密码 |
| `ssh_host` | `<FQDN-or-private-IP>` | 管理员配置；非管理员 API 仅返回掩码 |
| `ssh_port` | `22` | 1~65535；必须是内网可达端口 |
| `host_key_alias` | `proxy-a.sit` | `known_hosts` 匹配别名，禁止接受新指纹 |
| `ssh_host_key_fingerprint` | `SHA256:<base64>` | Probe 后由严格 SSH 握手写入；管理员不能手填伪造 |
| `remote_source_key` | `sit_proxy_a_slowlog` | 远端白名单项名称；不能是路径或命令 |
| `declared_path_template` | `/absolute/log/dir/slow_sql_instance_<port>*` | 管理员填写的审计/展示路径；必须绝对路径，且不参与 SSH 参数拼接 |
| `parser_profile` | `tdsql_mysql_slowlog_v1` | 通过格式门禁后才能选择 |
| `enabled` | `false` | 节点 probe 成功、与远端配置一致后才可启用 |

**目录配置示例（仅格式示意，不是默认值）：**

```json
{
  "node_key": "proxy_a",
  "ssh_host": "<private-host-or-ip>",
  "ssh_port": 22,
  "remote_source_key": "sit_proxy_a_slowlog",
  "declared_path_template": "/<absolute-log-root>/<proxy-port>/gateway/log/<slow-log-file-pattern>",
  "parser_profile": "tdsql_mysql_slowlog_v1"
}
```

工程不得给出“猜测的默认目录”并自动读取。真实目录只由管理员在配置页和目标机导出器白名单中成对填写，并由 probe 对比确认。

### 5.3 部署秘密解析规则

环境变量只指向挂载目录，不直接放私钥内容：

```text
SLOWLOG_SECRETS_DIR=/run/secrets/tdsql-sqlcheck/slowlog
SLOWLOG_SSH_CONNECT_TIMEOUT_SECONDS=10
SLOWLOG_SSH_COMMAND_TIMEOUT_SECONDS=45
SLOWLOG_MAX_CONCURRENT_NODES=3
SLOWLOG_OPENSSH_MIN_VERSION=7.4
```

解析规则：

```text
credential_ref = sit_proxy_reader_v1
private key    = ${SLOWLOG_SECRETS_DIR}/keys/sit_proxy_reader_v1

known_hosts_ref = tdsql_proxy_hosts_v1
known_hosts     = ${SLOWLOG_SECRETS_DIR}/known_hosts/tdsql_proxy_hosts_v1
```

路径必须经 `Path.resolve()` 后验证仍在 `SLOWLOG_SECRETS_DIR` 内；引用名不满足正则直接拒绝。私钥权限必须为仅运行用户可读；任何响应、操作日志和异常信息都不得输出该路径以外的秘密内容。

### 5.4 目标主机受限导出器配置

每台目标 Proxy/Gateway 主机由系统管理员安装一次导出器和白名单配置，示意如下：

```json
{
  "remote_source_key": "sit_proxy_a_slowlog",
  "allowed_path_globs": [
    "/<absolute-log-root>/<proxy-port>/gateway/log/<slow-log-file-pattern>"
  ],
  "parser_profile": "tdsql_mysql_slowlog_v1",
  "max_chunk_bytes": 8388608,
  "max_files": 8,
  "storage_identity": "<stable-filesystem-id>",
  "read_only": true
}
```

约束：

1. `allowed_path_globs` 必须为绝对路径，且管理员只为真实慢日志目录配置；不允许 `..`、换行、控制字符或根目录通配。
2. 导出器返回文件身份、存储身份、文件显示名、大小、mtime 和块数据；不返回其他目录条目，不接受客户端指定的 glob。`storage_identity` 必须来自目标主机的稳定文件系统标识或由运维在共享存储配置中显式统一，供跨节点重复检测使用；为空的 Probe 直接失败，不能启用。
3. 平台节点的 `declared_path_template` 与远端 `allowed_path_globs` 至少有一项规范化匹配；不匹配时 probe 失败并禁止启用。
4. 导出器运行账户对日志仅有读取权限；CheckSQL 客户端没有目标主机普通 Shell。

### 5.5 SSH 信任的最小权限要求

用户侧需要准备的是**到全部实际 Proxy/Gateway 日志主机**的内网信任，而非仅 ZK 或 SET/DB 节点：

```text
CheckSQL 部署机私网地址 ──TCP/22（源地址精确限制）──> Proxy/Gateway 主机
```

推荐 `authorized_keys` 限制等价于：禁止 PTY、代理/X11/端口转发、用户 rc，并强制执行导出器。可叠加 `from="<CheckSQL 私网地址>"`。私钥只保留在 CheckSQL 部署机；不得在聊天、数据库、仓库或目标主机保存私钥副本。

若无法提供“专用账户 + 慢日志只读 ACL + ForceCommand”，本模块不得以 root 免密登录作为替代方案。

---

## 6. 数据库设计

### 6.1 实体关系

```text
tdsql_connections (既有)
        1
        │ connection_id（逻辑关联，不加跨历史外键）
        N
slow_log_sources
        1 ─────── N slow_log_source_nodes
        1 ─────── N slow_log_collection_runs
        1 ─────── N slow_log_events
                         N ─────── 1 slow_log_source_nodes
slow_log_source_nodes
        1 ─────── N slow_log_cursors
        1 ─────── N slow_log_node_probe_files
```

不为 `connection_id` 建外键：既有连接登记支持删除/替换，强外键会阻塞现有生命周期。源删除采用“先停用、后显式清理”的受控流程，不在本版本提供级联物理删除按钮。

### 6.2 表与字段

#### T1 `slow_log_sources`：一个实例的一组采集策略

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | BIGINT PK | 自增主键 |
| `source_key` | VARCHAR(64) UNIQUE | 不可变业务键 |
| `connection_id` | VARCHAR(64) | 关联既有实例 |
| `display_name` | VARCHAR(128) | 页面展示名 |
| `transport` | VARCHAR(32) | 本版固定 `ssh_exporter_v1` |
| `timezone` | VARCHAR(64) | 日志时间解析时区 |
| `poll_interval_seconds` | INT | 周期 |
| `max_batch_bytes` / `max_events_per_batch` | INT | 单次协议批次流量保护 |
| `max_run_seconds` / `lag_alert_seconds` | INT | 追赶预算与可见性告警阈值 |
| `initial_position` / `initial_lookback_seconds` | VARCHAR / INT | 首次接入策略 |
| `min_query_time_ms` | BIGINT | 平台二次阈值 |
| `credential_ref` / `known_hosts_ref` | VARCHAR(64) | 秘密引用名，不存秘密 |
| `enabled` | TINYINT | 是否允许调度 |
| `last_success_at` / `last_backlog_bytes` / `last_lag_seconds` / `last_error_*` | DATETIME / BIGINT / VARCHAR | 运行健康摘要与积压 |
| `lease_holder` / `lease_expires_at` | VARCHAR / DATETIME | 源级互斥 |
| `created_by` / `created_at` / `updated_at` | 审计字段 | 变更留痕 |

#### T2 `slow_log_source_nodes`：源下的一台日志主机

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | BIGINT PK | 自增主键 |
| `source_id` | BIGINT | 逻辑关联源 |
| `node_key` | VARCHAR(64) | 源内唯一键 |
| `display_name` | VARCHAR(128) | 节点展示名 |
| `ssh_host` / `ssh_port` | VARCHAR / INT | 仅管理员可见的连接信息 |
| `host_key_alias` | VARCHAR(128) | 严格主机密钥验证别名 |
| `ssh_host_key_fingerprint` | VARCHAR(128) | Probe 严格握手得到的主机密钥指纹 |
| `remote_source_key` | VARCHAR(64) | 远端受限白名单键 |
| `declared_path_template` | TEXT | 审计路径；不得拼入命令 |
| `parser_profile` | VARCHAR(64) | 已验收的解析器版本 |
| `enabled` | TINYINT | 是否参与运行 |
| `last_probe_at` / `last_probe_status` / `last_probe_detail` | 探测证据摘要 | 不保存 SQL 样本 |
| `last_success_at` / `last_error_*` | 运行健康摘要 | |

#### T3 `slow_log_cursors`：按文件代际保存进度

| 字段 | 类型 | 说明 |
|---|---|---|
| `source_node_id` | BIGINT | 所属节点 |
| `file_identity` | VARCHAR(256) | 导出器提供的 `device:inode` 或等价稳定身份 |
| `generation` | INT | 同一身份 copytruncate 后递增 |
| `file_label` | VARCHAR(512) | 仅展示用文件名/安全相对标识 |
| `cursor_offset` | BIGINT | 最后一条已提交完整块的末尾偏移 |
| `last_file_size` | BIGINT | 轮转与截断判定 |
| `anchor_start_offset` / `anchor_length` / `anchor_sha256` | BIGINT / INT / CHAR(64) | 游标末尾原始字节锚点；续读前必须验证 |
| `last_event_time` | DATETIME(6) NULL | 可选诊断，不参与去重 |
| `status` | VARCHAR(32) | `active` / `rotated` / `truncated` / `error` |

唯一约束：`(source_node_id, file_identity, generation)`。

#### T4 `slow_log_events`：核心逐条慢 SQL 事件

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | BIGINT PK | 事件 ID |
| `source_id` / `source_node_id` | BIGINT | 来源定位 |
| `origin_file_identity` / `origin_generation` | VARCHAR / INT | 事件来源文件代际 |
| `origin_offset_start` / `origin_offset_end` | BIGINT | 完整日志块字节区间 |
| `event_time` | DATETIME(6) | **Proxy 日志记录时间** |
| `event_time_source` | VARCHAR(32) | 固定 `proxy_log_time` |
| `db_name` / `client_user` / `client_host` / `backend_host` | VARCHAR | 可解析的连接维度；长度受限 |
| `thread_id` | VARCHAR(128) | 原日志线程标识，非整数时仍可保留 |
| `query_time_us` / `lock_time_us` | BIGINT | 微秒；防止浮点比较误差 |
| `rows_sent` / `rows_examined` | BIGINT | 原始计数 |
| `statement_type` | VARCHAR(16) | `SELECT` / `INSERT` / `UPDATE` / `DELETE` / `OTHER` |
| `sql_fingerprint` | CHAR(64) | **完整脱敏 SQL** SHA-256，先哈希后截断 |
| `sql_template` | TEXT | 至多 8 KiB 的脱敏 SQL 模板 |
| `sql_template_truncated` / `sql_template_original_bytes` | TINYINT / INT | 截断可见性；原始指的是完整脱敏文本，非明文 SQL |
| `parse_version` | VARCHAR(32) | 如 `tdsql_mysql_slowlog_v1` |
| `extra_json` | TEXT | 有限白名单附加字段的 JSON 文本，不存原始块 |
| `collected_at` / `created_at` | DATETIME(6) | 采集和入库时间 |

唯一约束：`(source_node_id, origin_file_identity, origin_generation, origin_offset_start)`。

#### T5 `slow_log_collection_runs`：一次采集运行审计

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | BIGINT PK | 运行 ID |
| `source_id` | BIGINT | 所属源 |
| `trigger_type` | VARCHAR(16) | `schedule` / `manual` / `probe` |
| `requested_by` | VARCHAR(64) | 手动触发人或 `scheduler` |
| `status` | VARCHAR(32) | `running` / `completed` / `partial_failed` / `failed` / `skipped` |
| `started_at` / `finished_at` | DATETIME(6) | 运行时间 |
| `nodes_total` / `nodes_success` | INT | 节点结果 |
| `files_seen` / `bytes_read` / `blocks_parsed` | BIGINT | 采集统计 |
| `events_inserted` / `events_duplicate` / `events_filtered` | BIGINT | 入库统计 |
| `incomplete_tail_count` / `parse_error_count` | BIGINT | 数据质量统计 |
| `error_code` / `error_detail` | VARCHAR / TEXT | 已脱敏错误，不含 SQL / 密钥 |

> `sql_template`、`extra_json`、`error_detail` 为 `TEXT NOT NULL`，MySQL 不允许给 TEXT 设置 DEFAULT；所有 INSERT 必须显式传入非 NULL 值（空值用 `''`），这是施工时必须覆盖的严格模式用例。

#### T6 `slow_log_node_probe_files`：启用前重复文件防护证据

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | BIGINT PK | 自增主键 |
| `source_node_id` | BIGINT | Probe 所属节点 |
| `ssh_host_key_fingerprint` | VARCHAR(128) | 严格握手取得的主机身份 |
| `storage_identity` | VARCHAR(256) | 导出器返回的稳定存储身份 |
| `file_identity` | VARCHAR(256) | 导出器返回的文件身份 |
| `file_label` | VARCHAR(512) | 仅安全展示名 |
| `observed_at` | DATETIME(6) | 最近一次 probe 发现时间 |

索引：`(ssh_host_key_fingerprint, file_identity)` 与 `(storage_identity, file_identity)`。启用节点前以这两组键和其他**已启用**节点比对；任一冲突即拒绝启用。

### 6.3 迁移脚本（施工蓝图）

新增文件：`backend/schema/v7/070_raw_slow_log_collection.sql`。只允许纯增量 `CREATE TABLE IF NOT EXISTS` 和 `INSERT IGNORE`，遵守施工规约 R-02、R-03。`INSERT retention_policies` 依赖当前确定的初始化顺序 `_create_all_tables → run_migrations → _migrate_old_tables → _init_default_data`：其中 `retention_policies` 已由 `_create_all_tables` 创建。U16 必须锁定此顺序，未来调整时不能无声破坏迁移。

```sql
CREATE TABLE IF NOT EXISTS slow_log_sources (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    source_key VARCHAR(64) NOT NULL,
    connection_id VARCHAR(64) NOT NULL,
    display_name VARCHAR(128) NOT NULL,
    transport VARCHAR(32) NOT NULL DEFAULT 'ssh_exporter_v1',
    timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Shanghai',
    poll_interval_seconds INT NOT NULL DEFAULT 60,
    max_batch_bytes INT NOT NULL DEFAULT 8388608,
    max_events_per_batch INT NOT NULL DEFAULT 2000,
    max_run_seconds INT NOT NULL DEFAULT 25,
    lag_alert_seconds INT NOT NULL DEFAULT 600,
    initial_position VARCHAR(16) NOT NULL DEFAULT 'tail',
    initial_lookback_seconds INT NOT NULL DEFAULT 300,
    min_query_time_ms BIGINT NOT NULL DEFAULT 1000,
    credential_ref VARCHAR(64) NOT NULL DEFAULT '',
    known_hosts_ref VARCHAR(64) NOT NULL DEFAULT '',
    enabled TINYINT NOT NULL DEFAULT 0,
    last_success_at DATETIME NULL DEFAULT NULL,
    last_backlog_bytes BIGINT NOT NULL DEFAULT 0,
    last_lag_seconds BIGINT NULL DEFAULT NULL,
    last_error_code VARCHAR(64) NOT NULL DEFAULT '',
    last_error_detail VARCHAR(512) NOT NULL DEFAULT '',
    lease_holder VARCHAR(128) NOT NULL DEFAULT '',
    lease_expires_at DATETIME NULL DEFAULT NULL,
    created_by VARCHAR(64) NOT NULL DEFAULT '',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_sls_source_key (source_key),
    KEY idx_sls_connection (connection_id),
    KEY idx_sls_due (enabled, last_success_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS slow_log_source_nodes (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    source_id BIGINT NOT NULL,
    node_key VARCHAR(64) NOT NULL,
    display_name VARCHAR(128) NOT NULL,
    ssh_host VARCHAR(255) NOT NULL,
    ssh_port INT NOT NULL DEFAULT 22,
    host_key_alias VARCHAR(128) NOT NULL,
    ssh_host_key_fingerprint VARCHAR(128) NOT NULL DEFAULT '',
    remote_source_key VARCHAR(64) NOT NULL,
    declared_path_template TEXT NOT NULL,
    parser_profile VARCHAR(64) NOT NULL,
    enabled TINYINT NOT NULL DEFAULT 0,
    last_probe_at DATETIME NULL DEFAULT NULL,
    last_probe_status VARCHAR(32) NOT NULL DEFAULT 'never',
    last_probe_detail VARCHAR(512) NOT NULL DEFAULT '',
    last_success_at DATETIME NULL DEFAULT NULL,
    last_error_code VARCHAR(64) NOT NULL DEFAULT '',
    last_error_detail VARCHAR(512) NOT NULL DEFAULT '',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_slsn_source_node (source_id, node_key),
    KEY idx_slsn_source_enabled (source_id, enabled)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS slow_log_cursors (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    source_node_id BIGINT NOT NULL,
    file_identity VARCHAR(256) NOT NULL,
    generation INT NOT NULL DEFAULT 0,
    file_label VARCHAR(512) NOT NULL DEFAULT '',
    cursor_offset BIGINT NOT NULL DEFAULT 0,
    last_file_size BIGINT NOT NULL DEFAULT 0,
    anchor_start_offset BIGINT NOT NULL DEFAULT 0,
    anchor_length INT NOT NULL DEFAULT 0,
    anchor_sha256 CHAR(64) NOT NULL DEFAULT '',
    last_event_time DATETIME(6) NULL DEFAULT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_slc_file_generation (source_node_id, file_identity, generation),
    KEY idx_slc_node_status (source_node_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS slow_log_events (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    source_id BIGINT NOT NULL,
    source_node_id BIGINT NOT NULL,
    origin_file_identity VARCHAR(256) NOT NULL,
    origin_generation INT NOT NULL DEFAULT 0,
    origin_offset_start BIGINT NOT NULL,
    origin_offset_end BIGINT NOT NULL,
    event_time DATETIME(6) NOT NULL,
    event_time_source VARCHAR(32) NOT NULL DEFAULT 'proxy_log_time',
    db_name VARCHAR(256) NOT NULL DEFAULT '',
    client_user VARCHAR(512) NOT NULL DEFAULT '',
    client_host VARCHAR(512) NOT NULL DEFAULT '',
    backend_host VARCHAR(512) NOT NULL DEFAULT '',
    thread_id VARCHAR(128) NOT NULL DEFAULT '',
    query_time_us BIGINT NOT NULL DEFAULT 0,
    lock_time_us BIGINT NOT NULL DEFAULT 0,
    rows_sent BIGINT NOT NULL DEFAULT 0,
    rows_examined BIGINT NOT NULL DEFAULT 0,
    statement_type VARCHAR(16) NOT NULL DEFAULT 'OTHER',
    sql_fingerprint CHAR(64) NOT NULL,
    sql_template TEXT NOT NULL,
    sql_template_truncated TINYINT NOT NULL DEFAULT 0,
    sql_template_original_bytes INT NOT NULL DEFAULT 0,
    parse_version VARCHAR(32) NOT NULL,
    extra_json TEXT NOT NULL,
    collected_at DATETIME(6) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    UNIQUE KEY uq_sle_origin (source_node_id, origin_file_identity, origin_generation, origin_offset_start),
    KEY idx_sle_source_time (source_id, event_time),
    KEY idx_sle_node_time (source_node_id, event_time),
    KEY idx_sle_fingerprint_time (sql_fingerprint, event_time),
    KEY idx_sle_db_time (db_name, event_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS slow_log_node_probe_files (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    source_node_id BIGINT NOT NULL,
    ssh_host_key_fingerprint VARCHAR(128) NOT NULL,
    storage_identity VARCHAR(256) NOT NULL DEFAULT '',
    file_identity VARCHAR(256) NOT NULL,
    file_label VARCHAR(512) NOT NULL DEFAULT '',
    observed_at DATETIME(6) NOT NULL,
    UNIQUE KEY uq_slnpf_node_file (source_node_id, file_identity),
    KEY idx_slnpf_host_file (ssh_host_key_fingerprint, file_identity),
    KEY idx_slnpf_storage_file (storage_identity, file_identity)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS slow_log_collection_runs (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    source_id BIGINT NOT NULL,
    trigger_type VARCHAR(16) NOT NULL,
    requested_by VARCHAR(64) NOT NULL DEFAULT '',
    status VARCHAR(32) NOT NULL DEFAULT 'running',
    started_at DATETIME(6) NOT NULL,
    finished_at DATETIME(6) NULL DEFAULT NULL,
    nodes_total INT NOT NULL DEFAULT 0,
    nodes_success INT NOT NULL DEFAULT 0,
    files_seen BIGINT NOT NULL DEFAULT 0,
    bytes_read BIGINT NOT NULL DEFAULT 0,
    blocks_parsed BIGINT NOT NULL DEFAULT 0,
    events_inserted BIGINT NOT NULL DEFAULT 0,
    events_duplicate BIGINT NOT NULL DEFAULT 0,
    events_filtered BIGINT NOT NULL DEFAULT 0,
    incomplete_tail_count BIGINT NOT NULL DEFAULT 0,
    parse_error_count BIGINT NOT NULL DEFAULT 0,
    error_code VARCHAR(64) NOT NULL DEFAULT '',
    error_detail TEXT NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    KEY idx_slcr_source_started (source_id, started_at),
    KEY idx_slcr_status_started (status, started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT IGNORE INTO retention_policies(table_name, retention_days, enabled)
VALUES ('slow_log_events', 30, 1);

INSERT IGNORE INTO retention_policies(table_name, retention_days, enabled)
VALUES ('slow_log_collection_runs', 90, 1);
```

配套修改：

1. `retention_service` 重构为“策略注册表 + 清理处理器”：既有小表保持当前通用删除器；`slow_log_events` / `slow_log_collection_runs` 注册 `RawSlowLogRetentionHandler`。处理器按 `event_time` / `started_at` 各自连接、每批 5,000 行、每批独立提交，单轮最多 20 批；绝不放进当前“同一连接、单次大 DELETE、最后统一 commit”的通用路径。保留策略仍统一存于 `retention_policies`。
2. `database._init_default_data()` 的 `all_menus` 增加 `slow-raw-log`，并为存量角色执行显式补齐；不能依赖“默认全可见”。
3. 由于新增均为建表/初始化，不需要在 `_migrate_old_tables()` 添加列双保险；不得借此修改既有 `slow_queries` / `scan_tasks` 表。

---

## 7. 接口设计

### 7.1 路由与鉴权

新增 `backend/api/raw_slowlog.py`：

```text
API prefix : /api/v1/raw-slowlogs
菜单键     : slow-raw-log
前端页键   : slow-raw-log
```

必须在 `auth_service._PATH_TO_MENU` 登记完整前缀；所有配置和运行控制处理函数再次显式检查角色（R-09 双保险）。

| 能力 | admin | dba | developer | auditor |
|---|---:|---:|---:|---:|
| 查询脱敏事件 / 导出 | ✓ | ✓ | ✓ | ✓ |
| 查看运行统计 | ✓ | ✓ | ✓ | ✓ |
| 查看主机、路径、密钥引用 | ✓ | 掩码 | 掩码 | 掩码 |
| 新建/修改源、节点、路径 | ✓ | ✗ | ✗ | ✗ |
| Probe、启停、手动采集 | ✓ | ✓ | ✗ | ✗ |
| 删除历史事件或源 | ✓ | ✗ | ✗ | ✗ |

`dba` 手动采集时使用已保存的配置，不能修改目标、路径、认证引用；服务端仍以源 ID 查库，拒绝客户端提交的 `ssh_host` / `path`。

### 7.2 REST 接口清单

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/sources` | 登录 | 源列表；非 admin 主机/路径/引用掩码 |
| POST | `/sources` | admin | 创建禁用源和节点配置 |
| GET | `/sources/{source_id}` | 登录 | 源详情；字段按角色脱敏 |
| PUT | `/sources/{source_id}` | admin | 更新源/节点；更新后自动置 `enabled=0` |
| POST | `/sources/{source_id}/probe` | admin, dba | 只读连通/格式探测，创建一条 `probe` 运行 |
| PUT | `/sources/{source_id}/enabled` | admin | 启用/停用；仅 probe 已通过且有启用节点时允许启用 |
| POST | `/sources/{source_id}/collect` | admin, dba | 异步手动采集，返回 `202 + run_id` |
| GET | `/runs` | 登录 | 采集运行列表 |
| GET | `/runs/{run_id}` | 登录 | 单次运行详情，错误已脱敏 |
| GET | `/events` | 登录 | 按事件时间、实例、库、节点、指纹、阈值筛选 |
| GET | `/events/{event_id}` | 登录 | 单条脱敏事件详情 |
| GET | `/events/export` | 登录 | CSV/HTML 导出；最大 10,000 行 |

删除接口不在首个实施迭代开放；先通过既有数据保留策略管理历史。这样避免一个页面误删使游标与事件产生不可审计的断裂。

### 7.3 创建源请求

```http
POST /api/v1/raw-slowlogs/sources
Content-Type: application/json
```

```json
{
  "source_key": "sit_proxy_slowlog",
  "connection_id": "<registered-connection-id>",
  "display_name": "SIT Proxy 原始慢日志",
  "transport": "ssh_exporter_v1",
  "timezone": "Asia/Shanghai",
  "poll_interval_seconds": 60,
  "max_batch_bytes": 8388608,
  "max_events_per_batch": 2000,
  "max_run_seconds": 25,
  "lag_alert_seconds": 600,
  "initial_position": "tail",
  "initial_lookback_seconds": 300,
  "min_query_time_ms": 1000,
  "credential_ref": "sit_proxy_reader_v1",
  "known_hosts_ref": "tdsql_proxy_hosts_v1",
  "nodes": [
    {
      "node_key": "proxy_a",
      "display_name": "Proxy A",
      "ssh_host": "<private-host-or-ip>",
      "ssh_port": 22,
      "host_key_alias": "proxy-a.sit",
      "remote_source_key": "sit_proxy_a_slowlog",
      "declared_path_template": "/<absolute-log-root>/<proxy-port>/gateway/log/<slow-log-file-pattern>",
      "parser_profile": "tdsql_mysql_slowlog_v1",
      "enabled": false
    }
  ]
}
```

成功响应：

```json
{
  "id": 41,
  "source_key": "sit_proxy_slowlog",
  "enabled": false,
  "message": "已创建但未启用。请先完成各节点只读 probe；配置最长 30 秒在全部服务进程生效。"
}
```

### 7.4 Probe 接口

```http
POST /api/v1/raw-slowlogs/sources/41/probe
```

Probe 必须做到：验证私钥可读、严格主机密钥匹配、强制命令协议、`remote_source_key` 存在、返回目录白名单摘要、日志文件清单和格式签名；**不得**把完整 SQL 返回给浏览器或写入事件表。

```json
{
  "run_id": 892,
  "status": "completed",
  "nodes": [
    {
      "node_key": "proxy_a",
      "status": "passed",
      "format_signature": {
      "time_header": true,
      "query_time_header": true,
      "sql_block_boundary": true,
      "timezone": "Asia/Shanghai",
      "protocol_version": 1,
      "exporter_version": "1.0.0"
      }
    }
  ],
  "message": "Probe 通过；仍需用 §16 的受控慢 SQL 用例确认时间和字段语义后再启用。"
}
```

### 7.5 手动采集与运行查询

```http
POST /api/v1/raw-slowlogs/sources/41/collect
```

```json
{ "run_id": 893, "status": "accepted", "message": "已受理；请查询运行状态。" }
```

`collect` 仅入队；HTTP 请求不得等待远端日志读取。相同源已有未过期租约时返回 `409 E4091 SOURCE_BUSY`，不并发启动第二个采集。

```http
GET /api/v1/raw-slowlogs/runs/893
```

```json
{
  "id": 893,
  "source_id": 41,
  "trigger_type": "manual",
  "status": "completed",
  "started_at": "2026-08-02T10:00:00.123456+08:00",
  "finished_at": "2026-08-02T10:00:03.491276+08:00",
  "nodes_total": 2,
  "nodes_success": 2,
  "files_seen": 4,
  "bytes_read": 35621,
  "blocks_parsed": 16,
  "events_inserted": 14,
  "events_duplicate": 2,
  "incomplete_tail_count": 1,
  "parse_error_count": 0,
  "error_code": "",
  "error_detail": ""
}
```

### 7.6 事件查询接口

```text
GET /api/v1/raw-slowlogs/events?
  connection_id=<id>&source_id=41&db_name=<db>&node_key=proxy_a&
  start_time=2026-08-02T00:00:00+08:00&end_time=2026-08-02T23:59:59.999999+08:00&
  min_query_time_ms=1000&fingerprint=<sha-or-keyword>&limit=50&offset=0
```

校验规则：

1. `start_time` / `end_time` 是 `event_time` 的闭区间；最大跨度 31 天；开始不得晚于结束。
2. `limit` 1~200；导出上限 10,000。
3. `node_key` 先按 `source_id` 校验归属；所有 SQL 值参数化。
4. SQL 搜索只对 `sql_template` 和 `sql_fingerprint` 做受控模糊检索；不支持任意 SQL 表达式。

响应关键字段：

```json
{
  "items": [
    {
      "id": 10001,
      "event_time": "2026-08-02T10:00:01.123456+08:00",
      "time_label": "Proxy 慢日志记录时间",
      "connection_id": "<registered-connection-id>",
      "source_name": "SIT Proxy 原始慢日志",
      "node_name": "Proxy A",
      "db_name": "example_db",
      "query_time_ms": 1532.4,
      "lock_time_ms": 0.0,
      "rows_examined": 12034,
      "statement_type": "SELECT",
      "sql_fingerprint": "<sha256>",
      "sql_template": "SELECT * FROM order_detail WHERE id = ?",
      "sql_template_truncated": false,
      "sql_template_original_bytes": 45
    }
  ],
  "total": 1,
  "time_semantics": "Proxy 慢日志记录时间；不代表 SQL 开始时间"
}
```

### 7.7 错误码

| 错误码 | HTTP | 含义 | 是否可重试 |
|---|---:|---|---|
| `E4001` | 400 | 配置字段/正则/范围非法 | 否 |
| `E4002` | 400 | 时间窗非法或跨度超过 31 天 | 否 |
| `E4031` | 403 | 非管理员修改源/节点/启停 | 否 |
| `E4032` | 403 | 非 DBA/管理员手动采集 | 否 |
| `E4041` | 404 | 源、节点、运行或事件不存在 | 否 |
| `E4091` | 409 | 源正在采集，租约未过期 | 是，稍后 |
| `E4092` | 409 | Probe 未通过，禁止启用 | 否 |
| `E4221` | 422 | 私钥/known_hosts 引用不存在或权限错误 | 否，修复部署 |
| `E4222` | 422 | 远端 source key / 路径白名单不匹配 | 否，修复配置 |
| `E4223` | 422 | 日志格式未通过门禁 | 否，新增解析器或修复格式 |
| `E4224` | 422 | 导出器协议或版本不兼容 | 否，按 §8.4 升级/回滚 |
| `E5021` | 502 | SSH 主机指纹、连接或协议失败 | 是，视故障 |
| `E5022` | 502 | 导出器返回非法清单/块校验失败 | 否，调查远端 |

---

## 8. 受限 SSH 导出协议

### 8.1 连接约束

| 项 | 规定 |
|---|---|
| 客户端实现 | **OpenSSH CLI >= 7.4**，由 `subprocess.Popen(argv, shell=False)` 调用；无 Python SSH 库 |
| 固定选项 | `BatchMode=yes`、`IdentitiesOnly=yes`、`StrictHostKeyChecking=yes`、`UserKnownHostsFile=<ref>`、`GlobalKnownHostsFile=/dev/null`、`PasswordAuthentication=no`、`KbdInteractiveAuthentication=no`、`RequestTTY=no` |
| 主机验证 | 严格验证；未知或变化指纹立即失败，不允许自动接受；Probe 从指定 known_hosts 解析并记录 SHA-256 主机指纹 |
| 认证 | 仅私钥；私钥来自部署 Secret |
| 账户 | `tdsql_log_reader`，无交互 Shell 能力 |
| 命令 | SSH 账户以强制命令启动导出器；客户端不追加任何用户输入命令 |
| 网络 | CheckSQL 私网源地址至 Proxy/Gateway SSH 端口的最小放行；不开放公网 |
| 限流 | 每源每周期串行；全局并发节点数受 `SLOWLOG_MAX_CONCURRENT_NODES` 限制 |

应用容器镜像须显式安装 `openssh-client`；非容器部署的 `deploy/preflight_check.sh` 须检查 `ssh -V`、固定选项支持及 `ssh-keygen` 可用性。若任一正式交付平台不具备合格客户端，发布前置检查失败，不能降级为 Python 库或关闭主机验证。

### 8.2 协议帧

标准输入为一行 UTF-8 JSON；标准输出为 NDJSON。单个字段不可跨行，块内容 Base64 编码。当前固定协议标识为 `raw_slowlog_exporter_v1`，导出器版本为语义版本字符串；请求、Probe 响应与每一个 chunk 必须回显该协议标识和受控 `source_key`。应用收到任一不匹配、空响应、非 NDJSON 或超出 `max_bytes` 的数据，均失败关闭且不推进游标。

Probe 请求：

```json
{"op":"probe","protocol":"raw_slowlog_exporter_v1","source_key":"sit_proxy_a_slowlog"}
```

Pull 请求：

```json
{
  "op": "pull",
  "protocol": "raw_slowlog_exporter_v1",
  "source_key": "sit_proxy_a_slowlog",
  "max_bytes": 8388608,
  "initial_position": "tail",
  "initial_lookback_seconds": 300,
  "timezone": "Asia/Shanghai",
  "cursors": [
    {
      "file_identity":"<known-id>",
      "generation":0,
      "offset":2048,
      "anchor_start_offset":1984,
      "anchor_length":64
    }
  ]
}
```

Probe 响应：

```json
{
  "type": "probe",
  "protocol": "raw_slowlog_exporter_v1",
  "version": "1.6.0.0",
  "source_key": "sit_proxy_a_slowlog",
  "storage_identity": "<stable-filesystem-id>",
  "files": [
    {
      "file_identity": "<device:inode-or-equivalent>",
      "file_label": "<safe-basename>",
      "file_size": 1048576,
      "modified_at": "2026-08-02T10:00:00Z"
    }
  ]
}
```

数据帧：

```json
{
  "type": "chunk",
  "protocol": "raw_slowlog_exporter_v1",
  "source_key": "sit_proxy_a_slowlog",
  "file_identity": "<device:inode-or-equivalent>",
  "file_label": "<safe-basename>",
  "file_size": 1048576,
  "offset": 2048,
  "next_offset": 4096,
  "eof": false,
  "pre_anchor_base64": "<bytes-at-known-anchor>",
  "post_anchor_base64": "<up-to-64-bytes-ending-at-next_offset>",
  "data_base64": "<base64>"
}
```

每个 chunk 的 `eof` 表示该文件本次读至尾部；导出器在未传入 cursor 的 `tail` 初始接入中仍必须返回 `post_anchor_base64`，平台据此建立可校验的初始尾部游标。`lookback` 初始接入由导出器按 `# Time` 和请求时区定位首条落入窗口的记录；未找到时安全地建立尾部游标。客户端必须验证：协议/源标识、Base64、偏移连续性、锚点、最大字节数、文件身份及存储身份归属。任意一项失败即本节点本次失败，游标不推进。

### 8.3 轮转与截断算法

1. 导出器清单中出现新 `file_identity`：为该文件新建游标，按 `initial_position` 决定从尾部或回溯位置读取；即使首次取“当前尾部”，也要同时读取并保存尾部锚点，不能留下无校验的初始游标。
2. 已知身份的文件 `size >= cursor_offset`：先验证该游标保存的末尾 64 字节锚点，再从游标读取新增字节。
3. `size < cursor_offset` **或锚点不匹配**：均认定 copytruncate / 内容替换；将旧游标标为 `truncated`，新建同身份 `generation + 1` 游标，从 0 读取。即使截断后在一个轮询周期内迅速写回超过旧偏移，锚点不匹配仍能发现。
4. 重命名轮转会产生新身份；旧文件仍在白名单清单内时继续读取到末尾，不能因文件名变化漏数据。
5. 每次完整块提交后计算“末尾 `min(64, cursor_offset)` 字节”的 SHA-256，连同起点和长度存入游标；只有完整块的 `origin_offset_end` 才能推进游标，不完整尾块下一周期重读。

### 8.4 远端导出器交付、安装与兼容

导出器是本设计的正式交付物：`raw_slowlog_exporter`。它采用 **Go 1.22 标准库**实现，构建为 `CGO_ENABLED=0` 的 Linux 单文件静态二进制，目标产物为 `raw_slowlog_exporter-linux-amd64` 与 `raw_slowlog_exporter-linux-arm64`（发布脚本的 `x86_64 → amd64`、`aarch64 → arm64` 映射必须显式测试）；目标 Proxy/Gateway 主机不需要 Python、Go、pip 或任何第三方运行时。它不是常驻服务，只在受限 SSH 会话的 ForceCommand 中被调用。

| 事项 | 施工规定 |
|---|---|
| 源码与构建 | 新增 `deploy/raw_slowlog_exporter/` 源码及 `deploy/build_raw_slowlog_exporter.sh`；构建脚本固定 Go 版本、`GOOS=linux`、`GOARCH`、`CGO_ENABLED=0`，生成 SHA-256 manifest |
| 发布包 | `deploy/make_release.sh` 为**当前目标架构**构建二进制、SHA-256 文件、样例白名单配置与安装手册，并复制到发布包 `deploy/raw_slowlog_exporter/`；找不到 Go 工具链或匹配产物即失败，不得 `|| true` |
| 协议协商 | Probe 与 chunk 带 `protocol=raw_slowlog_exporter_v1`，Probe 另带 `version=1.x.y.z`；客户端拒绝协议、源键或主版本不兼容的响应，不能降级猜测 |
| 安装 | 由目标主机的受控变更流程执行 `install -o root -g tdsql_log_reader -m 0750` 至 `/usr/local/libexec/raw_slowlog_exporter`；白名单配置为 `root:tdsql_log_reader`、`0640`，账户仅可读；`authorized_keys` 强制命令只指向该绝对路径 |
| 升级 | 每台节点先校验 SHA-256、执行本地 `--version` 和一次 probe，再原子替换；先灰度一个节点，probe 成功再滚动其余节点 |
| 回滚 | 保留上一版受校验二进制和配置；发生协议/格式异常时原子切回上一版，平台源保持禁用或降级，随后重新 probe |

ForceCommand 示例仅表达限制形态，公钥和真实路径由受控部署产生：

```text
restrict,command="/usr/local/libexec/raw_slowlog_exporter --stdio --config /etc/tdsql-sqlcheck/raw-slowlog-exporter.json" <public-key>
```

发布包可以携带导出器，**不得**由 CheckSQL 自动上传或替换目标主机二进制；安装、升级和回滚是 Proxy/Gateway 主机的独立变更，必须留存其版本与 probe 证据。

---

## 9. 详细处理流程

### 9.1 启用前 Probe

```text
管理员保存源（enabled=false）
  → 检查 Secret 引用在部署机存在、权限正确
  → 对每个节点严格 SSH 连接
  → 调用受限导出器 probe
  → 校验远端 source key、协议/导出器版本、路径摘要、存储身份、文件清单、格式签名
  → 从 pinned known_hosts 记录 SSH 主机密钥指纹；写 slow_log_node_probe_files
  → 与其他已启用节点按 (host fingerprint, file identity) / (storage identity, file identity) 检查冲突
  → 写 slow_log_collection_runs(trigger=probe)
  → 节点全部 passed 才允许管理员启用源
```

任何节点失败或存在文件冲突时，源保持禁用；页面显示节点级脱敏原因和冲突节点 key。不存在“部分节点自动启用”的隐式行为，避免集群某个 Proxy 漏采而仍显示整体健康。

### 9.2 定时采集

```text
APScheduler 每 30 秒 tick
  → 取得既有 scheduler_lease；非 leader 立即返回
  → 查 enabled=1 且到期的 slow_log_sources
  → 针对每个源 CAS 获取 source lease（5 分钟）
  → 创建 running run
  → 在 max_run_seconds 预算内，顺序处理 enabled 节点并连续拉批：manifest → anchor → chunk → 完整块 → parse/mask → transaction
  → 每批更新未读字节；未追平则继续下一批，预算耗尽才结束本轮
  → 更新每个节点健康及源摘要
  → 完成 run，释放 source lease
```

到期判断使用 `last_success_at + poll_interval_seconds <= now`；失败源的最短重试间隔固定 60 秒，防止不可达主机每 30 秒刷日志。`max_events_per_batch=2000` 只是单批上限，单源实际吞吐由批次大小、网络和 `max_run_seconds` 决定；运行末尾记录 `last_backlog_bytes`，若连续未清零或首个未读完整块时间落后超过 `lag_alert_seconds`，源置 `degraded` 并触发告警。单源运行超过 5 分钟时在每个节点完成后续租；续租失败立即停止后续节点，当前已提交事务保持有效。

### 9.3 事件解析与事务提交

```text
字节块
  → UTF-8（replace）解码
  → 以完整“# Time”块边界切分
  → 最后半块保留，不提交、不推进
  → parser_profile 提取字段
  → 时间解析（source.timezone）
  → 完整 SQL 脱敏 → 完整脱敏文本 SHA-256 指纹 → 8 KiB 展示截断标记 + SQL 类型
  → query_time_us >= source.min_query_time_ms * 1000 ?
  → BEGIN
       INSERT IGNORE slow_log_events (... origin unique key ...)
       UPDATE/INSERT slow_log_cursors (cursor_offset=完整块末尾, anchor=末尾64字节哈希)
     COMMIT
```

解析失败不推进该失败块之后的游标，以免跳过未知格式数据；一次运行针对同一文件最多记录 100 个解析错误摘要，超过后中止该文件并标记 `E4223`。错误摘要只能保存行号/偏移、字段名和哈希，不能保存 SQL 原文。

### 9.4 与既有 EXPLAIN 的衔接

事件详情页仅在 `sql_template_truncated=0` 时提供“在 EXPLAIN 分析中打开”按钮，传递：

```json
{
  "connection_id": "<event.source.connection_id>",
  "db_name": "<event.db_name>",
  "sql": "<event.sql_template>"
}
```

按钮复用既有 EXPLAIN 安全校验；不自动在生产/测试库执行 SQL，不读取未脱敏原文。对于 DML，沿用既有“安全改写/拒绝”的 EXPLAIN 策略。SQL 模板已截断的事件只允许查看指纹和指标，不允许把不完整 SQL 送进 EXPLAIN。

---

## 10. 前端详细设计

### 10.1 页面布局

`slow-raw-log` 页面共三个页签：

| 页签 | 可见角色 | 内容 |
|---|---|---|
| 原始慢日志事件 | 全部登录角色 | 时间窗、实例、库、节点、耗时、指纹筛选；事件列表和详情抽屉 |
| 采集运行 | 全部登录角色 | 源健康、最近运行、节点成功/失败、滞后时间、错误摘要 |
| 采集源配置 | admin | 源/节点表单、路径一致性、Probe、启停；dba 仅看掩码状态，不显示编辑入口 |

事件列表固定列：日志记录时间、实例、节点、数据库、SQL 类型、耗时、锁等待、扫描行、SQL 模板、指纹、操作。`sql_template_truncated=1` 时 SQL 模板列显示“已截断（原脱敏文本 N 字节）”，详情与导出同样显示，且 EXPLAIN 操作禁用。列表顶端固定显示时间口径提示，不能省略。

### 10.2 表单交互约束

1. 新建源默认禁用，表单不提供“保存并启用”。
2. 修改 `ssh_host`、端口、`remote_source_key`、路径模板、解析器、私钥引用或 known-hosts 引用，保存后必须自动禁用源并清空该节点 probe 成功状态；游标保留但不自动继续读取，管理员须重新 probe 后明确启用。
3. 首次位置默认“从当前末尾开始”；选择“回溯”时必须显示估算窗口并二次确认，防止一次拉取大量历史日志。
4. 手动采集不弹出“正在完成”的假成功提示，只显示 `run_id` 并轮询运行状态。
5. 非管理员界面不渲染真实地址、绝对路径、远端 source key、密钥引用；不能仅靠 CSS 隐藏，后端响应同样脱敏。

### 10.3 报告与导出

导出标题使用：

```text
TDSQL 原始慢日志事件报告
时间范围：Proxy 慢日志记录时间 [开始 ~ 结束]
采集时间：报告生成时间
```

报告统计值包含事件总数、P50/P95/P99（样本数不足时显示 N/A）、指纹 TopN、节点分布和未采节点警告。不得把“采集成功”解释为“该窗口没有慢 SQL”；若源不可用或某节点失败，报告必须显示覆盖范围不完整。

---

## 11. 后端文件与实施映射

| 文件 | 变更 |
|---|---|
| `backend/schema/v7/070_raw_slow_log_collection.sql` | 新增 6 张表和两项保留策略 |
| `backend/services/database.py` | 初始化菜单权限；不修改既有慢 SQL 表语义 |
| `backend/services/retention_service.py` | 策略注册表分派；逐条事件走独立批量清理处理器 |
| `backend/services/raw_slowlog_models.py` | 新增 Pydantic/内部数据类、枚举、字段校验 |
| `backend/services/raw_slowlog_repository.py` | 源、节点、游标、事件、运行事务 |
| `backend/services/sql_masking.py` | 唯一 SQL 脱敏、完整文本指纹与展示截断实现；收敛既有三份规则 |
| `backend/services/raw_slowlog_parser.py` | 完整块切分、`tdsql_mysql_slowlog_v1`、偏移/锚点与脱敏适配 |
| `backend/services/raw_slowlog_ssh.py` | OpenSSH CLI / NDJSON 协议；固定 argv、`shell=False` |
| `backend/services/raw_slowlog_service.py` | Probe、采集、租约、状态汇总 |
| `backend/services/scheduler.py` | 注册源到期调度任务，复用 leader lease |
| `backend/api/raw_slowlog.py` | §7 全部接口及显式 RBAC |
| `backend/services/auth_service.py` | 增加 `/api/v1/raw-slowlogs` → `slow-raw-log` 映射 |
| `backend/main.py` | 注册新 Router |
| `frontend/index.html` | “慢SQL治理 / 原始慢日志”菜单与页面模板 |
| `frontend/static/js/app.js` | 独立状态、筛选、运行轮询、配置/权限渲染 |
| `Dockerfile` / `deploy/preflight_check.sh` | 安装并验证 OpenSSH client；不新增 Python SSH wheel |
| `deploy/raw_slowlog_exporter/` / `deploy/build_raw_slowlog_exporter.sh` | 静态 Go 导出器、交叉构建、版本/哈希 manifest |
| `deploy/make_release.sh` | 按交付架构带上导出器、安装说明和哈希；产物缺失即失败 |
| `tests/test_raw_slowlog_*.py` | 单元、契约、迁移、安全和回归测试 |

解析器可以提取既有 `parse_slow_sql_blocks` 的**无状态字段解析逻辑**，但不能直接调用其“把最后一块无条件视作完整”的行为。新增解析器必须返回：

```python
ParsedChunk(
    complete_blocks: list[ParsedBlock],
    next_safe_offset: int,
    incomplete_tail_start: int | None,
    parse_errors: list[ParseError],
)
```

这样游标的推进依据是字节偏移而非“Python 列表中有几条记录”。

---

## 12. 安全、审计与隐私设计

### 12.1 安全控制清单

| 风险 | 控制 |
|---|---|
| 采集账户变成远程执行入口 | ForceCommand + 无 PTY/转发 + 固定 JSON 协议 + 不接收命令/路径 |
| 私钥泄漏 | 仅部署 Secret；数据库存引用名；日志和 API 不回显 |
| 中间人或连错主机 | 严格 `known_hosts` / host key alias；指纹变化即失败 |
| 配置被篡改后读任意文件 | 远端白名单二次约束；应用路径不传进 SSH |
| SQL 含业务敏感值 | 内存解析后立即脱敏；仅存模板和哈希；脱敏失败不落库 |
| 大日志拖垮服务 | 单节点/单源字节数、事件数、超时、全局并发上限 |
| 运行问题不可审计 | 运行表 + `operation_logs` 记录配置、probe、启停、手动触发 |
| 普通用户获知网络拓扑 | 后端角色级字段掩码；前端不持有真实值 |

### 12.2 操作审计事件

必须调用既有 `log_operation` 记录：

```text
raw_slowlog_source_create
raw_slowlog_source_update
raw_slowlog_probe
raw_slowlog_enable
raw_slowlog_disable
raw_slowlog_collect_manual
raw_slowlog_events_export
```

审计详情仅记录源 ID、节点 key、配置字段名、运行 ID、计数和结果；不得记录主机、路径、SQL、私钥引用值或原始错误回显。

---

## 13. 容量、保留与可观测性

### 13.1 保留策略

| 表 | 默认保留 | 依据 |
|---|---:|---|
| `slow_log_events` | **30 天** | 逐条执行事件，量级远高于聚合 `slow_queries`；按 `event_time` 清理 |
| `slow_log_collection_runs` | 90 天 | 保留足够的调度和故障审计；按 `started_at` 清理 |
| `slow_log_cursors` | 不自动清理 | 运行连续性所需；仅停用源的受控清理流程处理 |
| 源/节点配置 | 不自动清理 | 配置审计所需；使用停用替代删除 |

`retention_policies` 可调整，但最低 7 天沿用现有系统约束。设置较短保留期前，前端须显示“会按日志记录时间删除事件”的明确告警。`slow_log_events` / `slow_log_collection_runs` 的清理器每张表单独连接：每批 `DELETE ... LIMIT 5000` 后立即提交，单轮最多 20 批；任一表失败不回滚或阻塞其他表。首版不使用 MySQL RANGE 分区，因为分区唯一键必须包含分区键，会改变本设计以 origin 为核心的去重约束；当规模超过 §13.2 典型档时，必须以专项设计处理分区/冷热分层，不能直接 ALTER 上线表。

### 13.2 容量预算与准入阈值

以下为**单个采集源**的规划预算，不是对真实环境的猜测。估算假设是平均每条事件（脱敏 SQL 模板、行记录、4 个二级索引和 InnoDB 余量）按约 **2 KiB** 计；`sql_template` 的业务保存上限固定为 8 KiB，超长模板会截断并可见。

| 档位 | 每源每天事件数 | 30 天行数 | 预计数据+索引空间 | 处理要求 |
|---|---:|---:|---:|---|
| 保守 | 10,000 | 300,000 | 约 0.6 GiB | 默认配置可准入 |
| 典型 | 100,000 | 3,000,000 | 约 5.6 GiB | 准入前确认元数据库剩余空间至少 3 倍预算 |
| 高峰 | 1,000,000 | 30,000,000 | 约 56 GiB | 禁止直接启用；先完成容量专项（冷热分层/分区/独立存储） |

空间公式：`event_count_per_day × retention_days × 2 KiB`。实际上线前必须用至少 24 小时 probe/受控采集结果测得平均 `sql_template_original_bytes`、索引大小和事件速率，更新该估算；不能以 `max_events_per_batch` 代替真实速率。

### 13.3 积压追赶、指标与健康定义

`max_events_per_batch=2000` 是单个协议批次限制，若每 60 秒只跑一批则理论上限约为 33 事件/秒，必然可能落后。因此运行器在 `max_run_seconds` 内连续拉批：无积压时一批即结束；存在积压时持续读取，直至所有启用节点的 `backlog_bytes=0` 或预算耗尽。下一次到期优先继续有积压的源。

当满足任一条件，必须记录告警并把页面源状态置为 `degraded`：

1. `last_backlog_bytes > 0` 连续 3 次运行，且趋势上升；
2. 首个未读完整块的 `event_time` 落后当前时间超过 `lag_alert_seconds`（默认 600 秒）；
3. 运行因预算耗尽而结束且存在未读字节。

仅“很久没有新慢日志”不算积压，不能因为空闲实例误报故障；积压判断依据是未读字节和首个未读完整块，而不是最后一条已入库事件的时间。

新增指标：

```text
raw_slowlog_runs_total{status}
raw_slowlog_events_inserted_total{source_key}
raw_slowlog_parse_errors_total{source_key,node_key}
raw_slowlog_source_lag_seconds{source_key}
raw_slowlog_backlog_bytes{source_key,node_key}
raw_slowlog_run_budget_exhausted_total{source_key}
raw_slowlog_ssh_failures_total{reason}
```

源健康：

| 状态 | 条件 |
|---|---|
| `healthy` | 全部启用节点成功，且最新成功时间未超过 `max(3 × poll_interval, 5 分钟)` |
| `degraded` | 最近运行部分节点失败，或 §13.3 的积压/滞后阈值命中 |
| `failed` | 连续 3 次运行全部失败 |
| `disabled` | 管理员未启用 |
| `invalid_format` | Probe 或格式门禁未通过 |

---

## 14. 失败处理与失效方向

| 场景 | 行为 | 禁止行为 |
|---|---|---|
| SSH 不通 / 主机指纹不匹配 | 节点失败、游标不动、源降级、记录脱敏错误 | 自动接受新指纹、改用密码 |
| 单节点失败而其他节点成功 | 写 `partial_failed`；事件页面/报告显示覆盖不完整 | 显示“本时间段无慢 SQL” |
| 远端文件被轮转 | 按身份/代际继续读取 | 仅按文件名覆盖游标 |
| 尾块不完整 | 不推进该块，下一轮重读 | 截断 SQL 后入库 |
| 格式变更 | `invalid_format`，停止该节点 | 用当前时间/空 SQL 勉强入库 |
| 同一块重读 | 唯一键判为 duplicate，游标可安全继续 | 因重复异常终止整源 |
| 应用重启 | 未提交数据重读，已提交数据去重 | 依据内存状态断言已采完 |
| 某源长期无日志 | healthy，但显示“最近 N 小时无新事件” | 误报采集故障 |

本模块的失效方向遵循 R-15：**宁可把覆盖不完整显式标红，也不能给出“零慢 SQL”的假结论。**

---

## 15. 实施顺序（照图施工）

### 阶段 0：施工前置门禁（未通过不得写业务代码或建 v7 表）

1. **离线发布门禁**：确认 `requirements.txt` 无新增 Python SSH 库；对每个实际交付的 `(arch, pytag)` 执行现有 `deploy/make_release.sh --arch <x86_64|aarch64> --py <tag>`，其 `pip download --only-binary` 必须全量成功。Docker 交付还须为实际架构构建镜像，确认其中 `ssh` 与 `ssh-keygen` 可执行。
2. **OpenSSH 门禁**：在正式 CheckSQL 部署形态执行 `ssh -V`、`ssh -G <test-host>`，确认版本不低于 7.4 且支持 §8.1 固定选项；缺少 `openssh-client` 必须通过受控系统包/镜像变更补齐，不能临时 pip 安装替代。
3. **导出器门禁**：用 Go 1.22 构建 `linux-amd64`、`linux-arm64` 静态产物，分别校验 `--version`、协议 v1 fake exporter 测试和 SHA-256 manifest；`make_release.sh` 对缺失/错误架构产物必须失败。
4. **容量门禁**：根据 §13.2 选择保留期和容量档；高峰档或超过典型档的接入必须先有单独的冷热分层/分区设计评审，不能先建表后补救。

### 阶段 A：离线可测核心

1. 建 `v7/070` 迁移，执行空库和升级库迁移测试；U16 必须断言 `retention_policies` 在迁移 INSERT 前已存在。
2. 建模型、仓储和服务骨架；事件/游标保存必须先有事务与唯一键测试。
3. 从既有解析器提取字段匹配逻辑，先建 `sql_masking.py` 并把三份既有规则收敛；新建带字节偏移、锚点和完整块语义的解析器。
4. 完成 SQL 脱敏失败即拒绝入库、完整脱敏文本指纹、展示截断可见的测试。
5. 实现 OpenSSH CLI 调用的 fake exporter 测试服务器；先测协议、主机密钥、锚点、输入校验和不使用 Shell，不接真实环境。

### 阶段 B：平台接口与页面

1. 新增 Router、菜单、路径映射和 RBAC 双保险。
2. 实现源/节点配置、probe、启停、运行和事件查询接口。
3. 实现独立页面、时间口径常驻提示、运行健康和管理员配置入口。
4. 导出 HTML/CSV，包含覆盖不完整警告和时间定义。

### 阶段 C：调度与保留

1. 在现有 scheduler 中注册 30 秒 tick，不改动既有扫描计划语义。
2. 复用 leader lease；实现源级 CAS 租约、超时续租和失败退避。
3. 接入数据保留、指标、操作审计和健康状态。

### 阶段 D：真实环境准入（必须后置）

仅当 §16 所列账号、白名单和受控样本具备时执行。完成格式验收、轮转验证、跨节点覆盖验证后，管理员才可启用定时采集。

---

## 16. 测试设计与真实环境准入门禁

### 16.1 自动化测试矩阵

| 编号 | 层级 | 用例 | 验收点 |
|---|---|---|---|
| U01 | 解析 | 标准完整日志块 | 时间、耗时、行数、SQL 模板正确 |
| U02 | 解析 | SQL 跨多行 | 保留完整模板，偏移正确 |
| U03 | 解析 | 最后块无结束边界 | 不提交、不推进游标 |
| U04 | 脱敏 | 字符串/数字/JSON/注释 | 数据库中无原值 |
| U05 | 仓储 | 同 origin 重放 | 仅 1 条事件，duplicate 计数正确 |
| U06 | 仓储 | 插入后游标更新失败 | 整体回滚；下次可重读 |
| U07 | 轮转 | rename rotation | 新旧文件都不漏读 |
| U08 | 轮转 | copytruncate | generation 增加，不与旧事件冲突 |
| U09 | SSH | 未知 host key | `E5021`，不发起读取 |
| U10 | SSH | 路径/命令注入字符 | 请求模型直接 400；无远端连接 |
| U11 | SSH | 块哈希/偏移异常 | `E5022`，游标不动 |
| U12 | API | developer 修改配置 | 403，且无数据变化 |
| U13 | API | non-admin 读取源 | 地址、路径、引用全部掩码 |
| U14 | 调度 | 双副本同时 tick | 每源仅一个 run 取得租约 |
| U15 | 报告 | 一节点失败 | 报告显示覆盖不完整，不能显示零事件结论 |
| U16 | 迁移 | 存量 v1.5.2.5 升级及初始化顺序 | `retention_policies` 先存在；迁移成功，既有表/数据不变 |
| U17 | 轮转 | copytruncate 后一周期内写回超过旧偏移 | 锚点不匹配，强制新 generation，从 0 读取 |
| U18 | Probe/配置 | 两个启用节点声明同一主机/存储的同一文件 | 启用被拒，返回冲突节点 key，无事件重复写入 |
| U19 | 脱敏 | 超过 64 KiB 的 SQL | 完整脱敏文本参与指纹；仅存 8 KiB；截断标记在 API/页面/导出可见 |
| U20 | 调度 | 输入速率连续超过单批上限 | 运行在预算内连续拉批；积压持续时告警、页面标红、不得静默落后 |

### 16.2 真实环境格式门禁（R-11）

由于当前没有真实 Proxy 主机、路径和样本可供本地验证，以下为**启用前强制门禁，不是可选建议**：

1. 管理员在测试实例执行一条可识别、经批准的受控慢 SQL；记录业务侧完成时间和目标实例。
2. 用只读导出器读取对应日志文件的脱敏样本，证明存在可解析的完整块边界。
3. 证明 `# Time` 与该 SQL 的日志记录时间相符，并明确时区；不得只看文件 mtime。
4. 证明 `Query_time` 单位、`Rows_examined`、库名和用户字段在目标 TDSQL 版本中实际存在或记录为缺失。
5. 在日志轮转后重复读取，包含“copytruncate 后快速写回超过旧偏移”的锚点校验，证明不会从新文件中段错读。
6. 同一实例存在多个 Proxy 时，在每个 Proxy 产生受控样本；Probe 必须记录主机/存储/文件身份，并证明重复节点会被启用前机械拒绝。
7. Probe / 收集的连接入口、账户、SSH 端口必须就是 CheckSQL 正式部署时的入口（R-14），不能用管理员临时 root 会话替代。

验收材料只保存字段存在性、偏移、时间差和脱敏模板；不把真实原始日志、主机地址或凭据提交到 GitHub。

### 16.3 UAT 准出标准

| 类别 | 准出条件 |
|---|---|
| 安全 | 不使用 root/密码/SCP；主机指纹校验和 ForceCommand 实测拒绝非法命令 |
| 完整性 | 受控样本逐条落库；重跑零重复；轮转无漏读 |
| 数据保真 | 超长脱敏 SQL 指纹稳定、截断标记可见；禁止对截断 SQL 执行 EXPLAIN |
| 时间 | 事件页面/报告时间与 Proxy 日志 `# Time` 一致；不出现“执行开始时间”误标 |
| 隔离 | `slow_queries`、`scan_tasks`、`gateway_log_reports` 无新增写入 |
| 故障 | 单节点失败时整体报告明确提示覆盖不完整 |
| 吞吐 | 高于单批上限时能连续追赶；积压超阈值告警且页面标红 |
| 可运维 | 可从运行记录定位源、节点、错误码、最后成功时间；不泄露秘密 |

---

## 17. 需要用户/运维在实施阶段提供的最小条件

本设计不要求现在提供内网主机资料，也不把它写入仓库。实际实施和 SIT 验收时，仅需在受控配置渠道完成：

1. 在**全部实际 Proxy/Gateway 日志主机**创建 `tdsql_log_reader` 专用账户，授予真实慢日志目录只读 ACL。
2. 在这些主机安装受限导出器，并配置每项 `remote_source_key → allowed_path_globs`；真实目录以此配置为准。
3. 将 CheckSQL 部署机生成的公钥加入专用账户，配置强制命令和严格限制；私钥只挂载到 CheckSQL 服务器 Secret。
4. 仅从 CheckSQL 部署机私网地址到目标主机 SSH 端口放行；不开放公网入口。
5. 在 CheckSQL 管理页面录入与远端同名的 `remote_source_key`、节点别名、地址、端口和路径模板，执行 probe。
6. 提供一次经批准的测试窗口，以完成 §16 受控慢 SQL、日志格式和轮转验证。

如果只可提供“root 免密 SSH”而不能提供专用账户、日志 ACL 和强制命令，本设计的安全前提不成立，应暂停而不是降级实现。

---

## 18. 未决项与后续扩展

| 项目 | 当前处理 | 后续触发条件 |
|---|---|---|
| 赤兔官方日志 API | 不实现，保留 `transport` 扩展点 | 赤兔接口文档明确给出授权方式、稳定请求/响应、限流与支持范围 |
| Elasticsearch/Kibana | 不实现，保留适配器扩展点 | 确认集群已部署、索引字段和只读授权；不得抓取 Kibana 页面 |
| 多种 TDSQL 日志格式 | 仅 `tdsql_mysql_slowlog_v1` | §16 实测证明另一格式并给出独立 parser profile |
| 项目/应用维度权限 | 延续当前慢 SQL 可见性模型 | 平台完成连接—项目授权模型后再做行级过滤 |
| 事件与优化工单闭环 | 事件可跳 EXPLAIN，不新建工单 | 单独立项，避免把采集与流程管理耦合 |

---

## 19. 参考资料

1. [TDSQL MySQL 私有云：实例日志管理、网关日志、接口授权（腾讯云官方 PDF）](https://main.qcloudimg.com/raw/document/product/pdf/1515_62029_cn.pdf)
2. [TDSQL 分布式数据库：DescribeDBLogFiles（腾讯云官方 API）](https://cloud.tencent.com/document/api/557/16133)
3. [TDSQL 分布式数据库：DescribeDBSlowLogs（腾讯云官方 API）](https://cloud.tencent.com/document/api/557/70099)
4. 本仓库 `backend/services/scan_service.py`、`backend/services/tdsql_connector.py`、`backend/services/gateway_log_analysis/analyze_gateway_log.py`、`backend/services/scheduler.py`、`docs/GUIDE-团队施工规约.md`。
