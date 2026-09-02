# TDSQL 本地轻量化 Docker 靶场使用说明

针对宿主机内存资源敏感（32GB 内存占用较高）的开发自测场景定制，整套靶场内存占用仅约 **0.9GB ~ 1.2GB**，秒级启停，100% 满足 TDSQL-SQLCheck 平台的各种分布式/集中式功能联调需求。

---

## 一、 组件架构与端口分布

| 组件名称 | 容器名 | 镜像 | 映射端口 | 内存配额 | 功能与模拟作用 |
|---|---|---|---|---|---|
| **TDSQL 网关** | `tdsql-proxy` | `python:3.11-slim` | **`15002`** | 150MB | **原厂 Proxy 协议网关**：原生支持 `CREATE TABLE ... shardkey=...` 建表与广播表语法；动态拦截并注入真实 DDL；原生响应 `/*proxy*/show status` 等指令，供 PR001 探针判定分布式拓扑。 |
| **ZooKeeper** | `tdsql-zk` | `zookeeper:3.8` | **`2181`** | 256MB | 真实 ZK 节点树，供平台【ZK 实例自动发现】模块检索 `/tdsqlzk` 下的分布式 group 与集中式 setrun。 |
| **MySQL 引擎** | `tdsql-mysql-test` | `mysql:8.0` | **`13306`** | ~600MB | 承载底层数据存储、分片元数据字典表、以及 TDSQL 原厂慢查询监控库 `tdsqlpcloud_monitor`。 |

---

## 二、 原厂原生语法支持与测试

通过连接 **15002** 端口（TDSQL 网关端口），即可像在真实公有云/私有云 TDSQL 集群上一样直接执行原生 DDL：

### 1. 原生分片表（无需任何 COMMENT 伪装）：
```sql
CREATE TABLE `t_order` (
  `order_id` BIGINT NOT NULL,
  `user_id` BIGINT NOT NULL,
  `amount` DECIMAL(12,2),
  PRIMARY KEY (`order_id`, `user_id`)
) ENGINE=InnoDB shardkey=user_id;
```

### 2. 原生广播表（小表广播）：
```sql
CREATE TABLE `t_dict_item` (
  `item_code` VARCHAR(32) PRIMARY KEY,
  `item_name` VARCHAR(64)
) ENGINE=InnoDB shardkey=noshardkey_allset;
```

### 3. 原厂网关拓扑探测指令：
```sql
/*proxy*/show status;
/*proxy*/show backends;
```

---

## 三、 预置业务数据库与对象清单

### 1. 分布式业务库：`tdsql_demo_distributed`
- **`big_audit_trail`**：经典分片表，分片键 `user_id`。
- **`cus_bas_corp_contact`**：哈希分片表，分片键 `cust_no`。
- **`cus_name_list_type`**：全局广播表（`shardkey=noshardkey_allset`）。
- **`t_dict`**：字典广播表（`shardkey=noshardkey_allset`）。
- **`t_single_sys_config`**：单表（用于反向对照单表审核）。

### 2. 辅助系统库：`xa`
- 预置 `xa` 库，供平台实例探测算法（PR004 判据）将实例类型精准判定为 `distributed`。

### 3. 监控数据库：`tdsqlpcloud_monitor`
- 预置 `proxy_classes_analysis` 慢 SQL 监控表（包含 31 个标准原厂字段），预注入典型慢 SQL 样本，供慢查询分析模块拉取测试。

---

## 四、 常用管理命令

在 `TDSQL-SQLCheck/deploy/tdsql-dev-cluster` 目录下打开 PowerShell 执行：

```powershell
# 1. 一键启动并初始化靶场（启动容器、启动 Proxy、灌入 ZK 树、创建表）
.\start_tdsql_cluster.ps1 start

# 2. 查看靶场容器运行状态
.\start_tdsql_cluster.ps1 status

# 3. 执行端到端全链路连通性与审核联调测试
.\start_tdsql_cluster.ps1 test

# 4. 停止靶场（释放内存）
.\start_tdsql_cluster.ps1 stop
```

---

## 五、 在 TDSQL-SQLCheck 平台中配置连接

在平台前端界面【实例管理】中，填写如下信息即可立即连接：

- **实例名称**：`TDSQL本地轻量分布式靶场(经Proxy 15002)`
- **主机地址**：`127.0.0.1`
- **业务端口**：`15002`（走 Proxy 代理，支持原厂语法与探针）
- **用户账号**：`root`
- **用户密码**：`tdsql_test_2024`
- **默认数据库**：`tdsql_demo_distributed`
- **实例类型**：`分布式`
- **监控主机**：`127.0.0.1`
- **监控端口**：`15002`（或 `13306`）
- **监控库名**：`tdsqlpcloud_monitor`

---

## 六、 ZooKeeper 自动发现与业务库枚举配置说明

在平台【ZK 实例自动发现】界面中配置：
- **ZK 连接串**：`127.0.0.1:2181`
- **ZK 根路径**：`/tdsqlzk`
- **业务库探针用户名**：`root`
- **业务库探针密码**：`tdsql_test_2024`

> **重要说明**：TDSQL 的 ZooKeeper 仅存放分布式物理拓扑，不记录业务数据库名称。配置业务探针账号密码后，平台会在发现实例时自动向实例网关发起 `SHOW DATABASES` 探测，将业务库完整枚举并填充至表格中。
