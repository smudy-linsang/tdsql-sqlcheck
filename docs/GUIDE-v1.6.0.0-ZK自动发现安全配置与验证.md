# TDSQL-SQLCheck v1.6.0.0 ZK 自动发现安全配置与验证手册

> 编制：智能体O
> 版本：v1.6.0.0
> 适用范围：开发、测试及生产部署；示例均为占位值，不得替换为仓库内的真实凭据。

## 1. 目的与安全边界

本手册适用于“实例管理 / 从 ZooKeeper 自动发现”。该功能读取 TDSQL 管控面实例清单，用
`noshard → centralized`、`groupshard → distributed` 回写已登记实例的 ZK 形态。

真实发现与 Mock 的边界如下：

- 未配置、ZK 不可达、`zkCli.sh` 不可执行、脚本超时或脚本失败：接口返回 **HTTP 503**；不得返回任何 Mock 记录。
- Mock 仅允许部署端显式设置 `ZK_DISCOVERY_FORCE_MOCK=1` 进行联调；响应带 `source=mock`、`is_mock=true`，且禁止形态回写和实例导入。
- 浏览器不录入 ZK 地址、ZK 口令或数据库口令；数据库口令仅保留在服务端十分钟有效的发现会话中，导入后立即进入加密存储。
- ZK 认证口令不进入进程命令行。后端以环境变量传给脚本，由脚本通过 `zkCli` 标准输入认证。

## 2. 部署配置

复制 [`../deploy/zk-discovery.env.example`](../deploy/zk-discovery.env.example) 中的非敏感配置至部署环境，
并由进程管理器、容器编排或受控环境文件注入。不得把真实值提交到 Git、写入启动脚本或粘贴到浏览器。

认证文件由 `ZK_DISCOVERY_AUTH_FILE` 指向，内容严格为：

```json
{"username":"<read-only-zk-user>","password":"<zk-secret>"}
```

该文件应放在仓库外的秘密挂载目录；Linux 建议 `root:<应用运行组>`、权限 `0640`，容器只读挂载。
账户仅授予 `/tdsqlzk` 及脚本读取节点的只读 ACL，禁止 create、delete、write、admin。

`ZK_DISCOVERY_ENDPOINT_MAP` 是必须核对的部署配置：ZK 通常返回集群内网 Proxy 地址，
而 CheckSQL 可能登记 NAT/VIP/FQDN。映射仅替换主机部分，保留端口；会同时应用到 `host`
及 `proxy_list`，使形态回写可以命中实际连接地址。

## 3. 网络与运行前置条件

1. CheckSQL **Linux 实际运行环境** 至每个 ZK 节点的 client port 可建立完整 ZooKeeper 会话；TCP 三次握手成功不等于满足该条件。
2. 运行镜像/主机具备可执行 `bash`、`python3` 与配置的 `zkCli.sh`；客户端版本须与目标 ZK 兼容。
3. 仅放行 CheckSQL 运行出口到 ZK client port。不得为此开放管理 SSH、数据库管理端口或公网全网段。
4. 若 ZK 节点列表包含多个成员，后端逐节点尝试，不将逗号连接串直接交给 `zkCli`。
5. 若数据库连接使用公网/NAT 地址，须先配置并复核地址映射；否则发现成功也无法同步已登记实例形态。

## 4. 验证顺序与准出标准

按以下顺序执行，任何一步失败均不得把功能标记为通过：

1. **部署前检查**：验证秘密文件可读、`zkCli.sh` 可执行、`python3` 可用；不得显示或记录口令。
2. **会话检查**：从 CheckSQL 的实际 Linux 运行入口执行 `zkCli.sh -server <节点>`，认证后读取 `/tdsqlzk`；必须出现 `SyncConnected`，不得只有 TCP 连通。
3. **脚本检查**：以非静默模式运行 `tdsql_inventory.sh --with-status --with-type`，将原始 CSV 保存在受控临时位置后立即销毁，只保留脱敏汇总：`总数`、`noshard`、`groupshard`。
4. **不变量检查**：`总数 = noshard + groupshard`；每个可读取且包含有效 set 的 `group_*` 应产生一条 `groupshard` 记录；未知 kind 不得映射为任一实例类型。
5. **API 检查**：调用发现接口，必须为 `source=zk`、`is_mock=false`；响应 JSON 和浏览器网络面板中不得出现字段 `password`。
6. **映射及回写检查**：选取一个集中式、一个分布式开发实例，确认映射后的地址与 CheckSQL 登记地址一致，并核对 `zk_instance_kind`、`zk_instance_id`、`zk_synced_at`。
7. **故障检查**：使用不可达测试入口或短时移除测试放行。接口必须为 503，响应不得出现实例列表，数据库中的 ZK 形态字段在检查前后不得变化。
8. **Mock 隔离检查**：仅在开发开关开启时验证 `is_mock=true`；导入接口必须返回拒绝，且无任何实例形态回写。

## 5. 常见故障处置

| 现象 | 判定 | 处置 |
|---|---|---|
| TCP 可通但 `zkCli` 一直 `CONNECTING` 后 `ConnectionLoss` | 会话层被目标 ZK、主机防火墙、NAT/L4 代理或客户端策略主动关闭 | 查目标节点 ZK 服务日志及 `clientPortAddress`、访问控制；不要以 Mock 代替真实发现。 |
| 503：ZK 客户端不可用 | 部署镜像/主机不具备配置的客户端 | 在正式 Linux 运行形态安装或挂载兼容 `zkCli.sh`，不可在 Windows 进程中假装真实成功。 |
| 发现成功但没有回写既有实例 | ZK 返回地址与实例登记地址不同 | 配置 `ZK_DISCOVERY_ENDPOINT_MAP` 并重新执行发现。 |
| 记录全部是 `noshard` 但根节点存在有效 group set | 脚本解析或测试数据存在矛盾 | 保留脱敏的非静默统计，逐个核对 group 的 `sets`；不能以“kind 合法”作为通过断言。 |

## 6. 原始慢日志的衔接说明

“原始慢日志”是独立子模块，不使用 ZK 口令、MonitorDB 或 root 密码。它要求在实际 Proxy/Gateway
日志主机上配置专用 `tdsql_log_reader`、只读日志 ACL、SSH 主机指纹和 ForceCommand 导出器。日志
目录、文件模式和格式必须经 Probe 识别 `# Time` 与 `# Query_time` 后才能启用采集。
