# Python TDSQL 协议模拟靶场使用说明 (Python TDSQL Protocol Mock Range)

针对本地开发测试资源受限环境（Windows 宿主机高内存占用）定制，整套靶场基于 **“Python 协议网关 + 标准开源 MySQL 8.0 + ZooKeeper 3.8”** 构建，内存占用控制在 **约 0.9GB ~ 1.2GB**。

> **架构定位澄清**：  
> 本靶场为 **TDSQL 通信协议与接口形态测试桩（Mock Stub）**，后端底层存储引擎为标准单节点 MySQL 8.0。网关指令（如 `/*proxy*/show status`、`/*proxy*/show table with ...`）由 Python 协议网关进行通信层模拟响应。**本靶场并非腾讯云官方多节点闭源物理集群，不具备真实物理数据分片与跨节点分布式路由执行能力。**

---

## 一、 能力验证边界矩阵 (Capability Matrix)

为了保证测试与交付验收的严谨性，特此明确本模拟靶场的能力验证边界：

| 维度 | 能力项 | 靶场判定 | 详细说明 |
|---|---|---|---|
| **通信与协议** | TCP MySQL 二进制协议包通信 | **可验证** | 基于原生 Socket/AsyncIO 转发，支持明文 MySQL 协议通信。 |
| **通信与协议** | 连接握手默认库跟踪 | **可验证** | 解析 `HandshakeResponse41` 中的 `CLIENT_CONNECT_WITH_DB` 标志与库名字段。 |
| **通信与协议** | COM_INIT_DB 会话库切换 | **可验证** | 支持抓取 `0x02` 命令码，使 PyMySQL `select_db()` 能够准确切换库上下文。 |
| **通信与协议** | COM_QUERY USE db 跟踪 | **可验证** | 支持解析文本 SQL 中的 `USE <db>` 语句。 |
| **语法与指令** | DDL 原生分片/广播语法下推 | **可验证** | 拦截 `shardkey=`、`noshardkey_allset`，过滤下推至底层 MySQL 避免 1064 报错。 |
| **语法与指令** | SHOW CREATE TABLE 逆向呈现 | **可验证** | 结合元数据表动态将真实 `shardkey=` 拼入建表语句，摆脱 COMMENT 伪装。 |
| **语法与指令** | G14 原厂网关拓扑与表统计指令 | **可验证** | 模拟响应 `/*proxy*/show status`、`show table with/without shardkey` 的结果集形态。 |
| **平台业务链路** | SQLCheck 规则审核与元数据联动 | **可验证** | 成功验证 R012、R020 分片表审核规则触发与元数据联动。 |
| **平台业务链路** | ZooKeeper 实例自动发现与库枚举 | **可验证** | 成功验证 `/tdsqlzk` 拓扑探测与业务库 `SHOW DATABASES` 枚举。 |
| **分布式物理层** | 真实跨节点物理哈希路由 | **不可验证** | 仅单节点 MySQL 存储，无真实 Set 间分片分发。 |
| **分布式物理层** | 物理分区落盘与跨分片事务 (XA) | **不可验证** | 无法验证多物理节点两阶段提交与死锁仲裁。 |
| **网关核心引擎** | TDSQL 原厂自研分布式优化器 | **不可验证** | 无法模拟多表跨分片 JOIN 重写、下推聚合与算子计算。 |
| **权限与版本** | TDSQL 原厂细粒度安全权限与内核差异 | **不可验证** | 无法模拟 TDSQL 各小版本（如 TXSQL 各补丁包）私有行为。 |

---

## 二、 组件架构与端口映射

| 组件名称 | 容器名 | 镜像 | 映射端口 | 内存配额 | 功能与模拟作用 |
|---|---|---|---|---|---|
| **TDSQL 模拟网关** | `tdsql-proxy` | `tdsql-proxy:mock-v1.0` | **`15002`** | 150MB | **Python 协议模拟网关**：解析并过滤分片 DDL；模拟响应 `/*proxy*/show` 系列指令；跟踪会话库上下文。 |
| **ZooKeeper 服务** | `tdsql-zk` | `zookeeper:3.8` | **`2181`** | 256MB | 真实 ZK 节点树，供平台【ZK 实例自动发现】检索 `/tdsqlzk` 下的分布式与集中式拓扑。 |
| **底层存储数据节点** | `tdsql-mysql-test` | `mysql:8.0` | **`13306`** | ~600MB | 承载业务数据表、分片字典元数据表 `_tdsql_sys_meta` 以及慢查询库 `tdsqlpcloud_monitor`。 |

---

## 三、 镜像构建与内网复现确定性

为杜绝在无外网或内网构建环境中启动容器时在线 `pip install` 产生的不确定性与超时风险，网关已封装为固化镜像：

- **镜像名称**：`tdsql-proxy:mock-v1.0`
- **基础镜像**：`python:3.11-slim`
- **锁定依赖**：`pymysql==1.1.1`
- **构建命令**：
  ```powershell
  docker build -t tdsql-proxy:mock-v1.0 deploy/tdsql-dev-cluster
  ```
- **镜像摘要参考**：
  - Image Config SHA256: `sha256:347969b55dbd4cb1bae0d38bdd29abe1af2238d5c43e9abd8f5557dbd041abaf`

---

## 四、 常用运维管理命令

在 `TDSQL-SQLCheck/deploy/tdsql-dev-cluster` 目录下打开 PowerShell 执行：

```powershell
# 1. 一键启动并初始化靶场（启动容器、启动模拟网关、灌入 ZK 树、初始化元数据）
.\start_tdsql_cluster.ps1 start

# 2. 查看靶场容器运行状态
.\start_tdsql_cluster.ps1 status

# 3. 执行端到端全链路连通性与审核联调测试
.\start_tdsql_cluster.ps1 test

# 4. 停止靶场（释放内存）
.\start_tdsql_cluster.ps1 stop
```

---

## 五、 在 TDSQL-SQLCheck 平台中的配置信息

在平台【实例管理】中，填写如下信息即可连接靶场：

- **实例名称**：`TDSQL本地协议模拟靶场(Proxy 15002)`
- **主机地址**：`127.0.0.1`
- **业务端口**：`15002`（走模拟网关，支持语法过滤与探针）
- **用户账号**：`root`
- **用户密码**：`tdsql_test_2024`
- **默认数据库**：`tdsql_demo_distributed`
- **实例类型**：`分布式`
- **监控主机**：`127.0.0.1`
- **监控端口**：`15002`（或直连 `13306`）
- **监控库名**：`tdsqlpcloud_monitor`

在平台【ZK 实例自动发现】中配置：
- **ZK 连接串**：`127.0.0.1:2181`
- **ZK 根路径**：`/tdsqlzk`
- **业务库探针用户名**：`root`
- **业务库探针密码**：`tdsql_test_2024`

---

## 六、 仿真度后续演进准则

1. **协议语料脱敏回放**：若后续需进一步提升对真实 TDSQL 网关返回形态的保真度，应从真实 TDSQL 生产/测试环境（覆盖各主要版本）采集实际执行的结果集二进制与文本通信语料，脱敏后纳入测试固件，通过回放机制进行兼容性验证。
2. **严禁盲目硬编码宣称等价**：不得仅通过在网关中手工添加特定 SQL 字符串匹配与硬编码分支便宣称与官方 TDSQL 等价。必须在能力矩阵中清晰标定支持范围与未支持范围。
