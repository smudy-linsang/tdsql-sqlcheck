# TDSQL 本地轻量化 Docker 靶场使用说明

针对宿主机内存资源敏感（32GB 内存占用较高）的开发自测场景定制，整套靶场内存占用仅约 **0.8GB ~ 1.2GB**，秒级启停，100% 满足 TDSQL-SQLCheck 平台的各种分布式/集中式功能联调需求。

---

## 一、 组件架构与端口分布

| 组件名称 | 容器名 | 镜像 | 映射端口 | 内存配额 | 功能与模拟作用 |
|---|---|---|---|---|---|
| **ZooKeeper** | `tdsql-zk` | `zookeeper:3.8` | `2181` | 256MB | 真实 ZK 节点树，供平台【ZK 实例自动发现】模块检索 `/tdsqlzk` 下的分布式 group 与集中式 setrun。 |
| **MySQL 引擎** | `tdsql-mysql-test` | `mysql:8.0` | `13306` | ~600MB | 承载业务库、分片表/广播表元数据、以及 TDSQL 原厂慢查询监控库 `tdsqlpcloud_monitor`。 |

---

## 二、 预置业务数据库与对象清单

### 1. 分布式业务库：`tdsql_demo_distributed`
- **`big_audit_trail`**：经典分片表，分片键 `user_id`（`COMMENT='shardkey=user_id'`）。
- **`cus_bas_corp_contact`**：哈希分片表，分片键 `cust_no`（`COMMENT='shardkey=cust_no'`）。
- **`cus_name_list_type`**：全局广播表（`COMMENT='shardkey=noshardkey_allset BROADCAST'`）。
- **`t_dict`**：字典广播表（`COMMENT='shardkey=noshardkey_allset BROADCAST'`）。
- **`t_single_sys_config`**：无分片键单表（用于反向对照单表审核）。

### 2. 辅助系统库：`xa`
- 预置 `xa` 库，供平台实例探测算法（PR004 判据）将实例类型精准判定为 `distributed`。

### 3. 监控数据库：`tdsqlpcloud_monitor`
- 预置 `proxy_classes_analysis` 慢 SQL 监控表（包含 31 个标准原厂字段），预注入典型慢 SQL 样本，供慢查询分析模块拉取测试。

---

## 三、 常用管理命令

在 `TDSQL-SQLCheck/deploy/tdsql-dev-cluster` 目录下打开 PowerShell 执行：

```powershell
# 1. 一键启动并初始化靶场（启动容器、灌入 ZK 树、创建表）
.\start_tdsql_cluster.ps1 start

# 2. 查看靶场容器运行状态
.\start_tdsql_cluster.ps1 status

# 3. 执行端到端全链路连通性与审核联调测试
.\start_tdsql_cluster.ps1 test

# 4. 停止靶场（释放内存）
.\start_tdsql_cluster.ps1 stop
```

---

## 四、 在 TDSQL-SQLCheck 平台中配置连接

在平台前端界面【实例管理】中，填写如下信息即可立即连接：

- **实例名称**：`TDSQL本地轻量分布式靶场`
- **主机地址**：`127.0.0.1`
- **业务端口**：`13306`
- **用户账号**：`root`
- **连接密码**：`tdsql_test_2024`
- **默认数据库**：`tdsql_demo_distributed`
- **实例类型**：`分布式 (distributed)`
- **SET 列表**：`set_1782132369_1,set_1782132389_2`
- **监控主机/端口**：`127.0.0.1:13306`
- **监控数据库**：`tdsqlpcloud_monitor`
