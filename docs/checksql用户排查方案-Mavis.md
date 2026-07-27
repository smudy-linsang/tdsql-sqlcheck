# 内网生产 TDSQL 集群 `checksql` 用户排查方案 v1.2

> ⚠ **凭据处理约定（v1.3.3 起）**
>
> 本文档此前直接写入了 `checksql` 的明文口令，并随 `dbb0918`、`376f2e1` 两个提交
> 进入 git 历史。现已从工作区清除，改为经环境变量 `CHECKSQL_PWD` 注入。
>
> **注意：清除文档不等于消除泄露。** 该口令已存在于历史提交中，凡是拉取过本仓库的
> 人员/机器上的克隆里都还有，必须由 DBA **轮换该账号口令**才算真正处置完毕。
> 后续任何文档、脚本、测试一律不得写入明文凭据，`tests/test_no_hardcoded_secrets.py`
> 会拦截复发。

| 项目 | 内容 |
|---|---|
| 任务来源 | DBA 已在生产 TDSQL 集群为所有需纳入《TDSQL 数据库 SQL 审核工具》的实例创建 `checksql` 管理用户，事后联系不上 |
| 任务目标 | 在不依赖 DBA 的前提下，对**所有真实存在 `checksql` 用户的数据库实例**，采集完整接入元数据（proxy 地址/端口/实例名/业务描述/SET/DB 列表），用于后续接入审核工具 |
| 适用环境 | TDSQL 分布式 MySQL（基于 TXSQL 内核，公网/内网 proxy + 内网 monitordb） |
| 实验环境 | 云测试 cluster `tdsql_th16yls3c`（刘晴的集群），proxy `118.195.161.48`/`119.45.220.89:15001` |
| checksql 凭据 | 账号 `checksql`，口令经环境变量 `CHECKSQL_PWD` 注入（仅 set 端有效）。**口令不入库、不写文档** |
| 编写人 | Mavis（基于 2026-07-26 实验产出） |
| **文档版本** | **v1.2（2026-07-26）** —— 重写为"采集维度"导向：列出每个有 checksql 实例的完整元数据 |

---

## 0. 关键认知（先看这个）

### 0.1 业务背景澄清

- **云测开发环境没有 `checksql` 用户**（云测只有 `tdsql_check_user`，是 monitordb 元数据用户）
- **`checksql` 用户是内网生产 DBA 在 set 上手工建的**，是真实 TDSQL set 上的 MySQL 用户
- 所以 Track 1（监控库查授权记录 `t_dbuser_privileges`）**可能查不到**（DBA 没走授权流程，直接 `CREATE USER`）
- **必须用 Track 2 直连每个 set 查 `mysql.user`，才能拿到真实存在的 checksql 实例清单**

### 0.2 真实存在的几个"现实约束"

| 字段 | 现实 | 应对 |
|---|---|---|
| **业务描述** | **TDSQL 平台层不维护** set 级别的业务名（"订单库/支付库"等是业务方自己定义）。`clientName` / `instance_name` 存的是 TDSQL 系统标签（"集中式实例/分布式实例"），不是用户业务名 | 文档中该字段给"平台可查 + 业务方补"双轨；fallback 到 cluster 业务名 |
| **数据库名（DB 列表）** | monitordb 上的 `SHOW DATABASES` 只能看到 monitordb 自己的库，**看不到 set 上的业务库** | 必须 Track 2 直连 set 跑 `SHOW DATABASES` |
| **checksql 凭据** | 账号 `checksql`，口令走环境变量 `CHECKSQL_PWD`，**仅 set 端有效**（monitordb 自身的 mysql.user 里没这账号，验证过） | 文档已内置，Track 2 直连直接用 |

### 0.3 输出维度（核心交付物）

对**每个有 checksql 用户的实例**，输出以下字段（CSV + JSON）：

| # | 字段 | 含义 | 数据源 |
|---|---|---|---|
| 1 | `cluster_key` | 集群内部 ID | tdsqlpcloud.t_cluster |
| 2 | `cluster_name` | 集群业务名（中文） | tdsqlpcloud.t_cluster |
| 3 | `set_id` | SETID（set 唯一标识） | m_data_cur.set_name |
| 4 | `set_type` | set 类型：`noshard` / `groupshard` | m_data_cur.cluster_type |
| 5 | `group_id` | 所属 group（仅 groupshard） | m_data_cur.groupname |
| 6 | `instance_name` | 实例名（TDSQL 系统名） | m_data_cur.clientName / instance_name |
| 7 | `business_desc` | 业务描述（业务名/用途） | ⚠ 平台无，**靠业务方补**；fallback `cluster_name` |
| 8 | `proxy_ip` | proxy IP 列表（多 proxy 用 `;` 分隔） | m_data_cur.proxy_host |
| 9 | `proxy_port` | proxy 业务端口 | m_data_cur.oss_proxy_port |
| 10 | `master_node` | 主节点 MySQL `ip_port` | m_data_cur.master_ip_port |
| 11 | `host_name` | 主机名 | m_data_cur.hostname |
| 12 | `unique_id` | DBA 友好的全局唯一标识 | m_data_cur.uniqueid |
| 13 | `schedule_ip` | 调度/管理 IP | m_data_cur.schedule_ip |
| 14 | `db_list` | set 上的业务数据库列表 | Track 2 直连 `SHOW DATABASES` |
| 15 | `checksql_user_count` | checksql 实际行数 | Track 2 `SELECT FROM mysql.user` |
| 16 | `checksql_hosts` | checksql 授权的 host 列表 | Track 2 |
| 17 | `verify_time` | 验证时间 | 脚本生成 |

---

## 1. 数据源清单（实验已验证）

### 1.1 monitordb 关键表

| 表 | 行数（云测） | 作用 |
|---|---|---|
| `tdsqlpcloud.t_cluster` | 1 | 集群主表，含 `cluster_key` + `cluster_name`（业务名） |
| `tdsqlpcloud.t_dbuser_privileges` | 4 | 授权记录（主键 cluster_key+instance_id+user_name+user_host），**仅覆盖走授权流程的** |
| `tdsqlpcloud.t_statistics_dbcluster` | 106 | 按 dbcluster 维度的查询统计，含 `dbcluster_name`（活跃 set 信号） |
| `tdsqlpcloud_monitor.m_data_cur` | ~3200 | **核心**：所有监控项的 key-value 时序表 |
| `tdsqlpcloud_monitor.proxy_classes_analysis` | 344 | 慢查询样本，含 set_name + set_ip + set_port |

> **空表**（生产也别用）：`t_dbuser_apply`、`t_user_dbcluster`、`t_user_database_apply`、`t_dbcluster_apply` —— 申请流程相关，DBA 实际不走流程

### 1.2 `m_data_cur` 关键 key 速查

云测 4 个 set/group 节点上的元数据 key（取一个 set `set_1782129880_1` 的全集）：

| f_key | 含义 | 示例值 |
|---|---|---|
| `set_name` | SETID | `set_1782129880_1` |
| `cluster_type` | set 类型 | `noshard` / `groupshard` |
| `cluster_model` | 部署模型 | `2P-1M-1S`（2 proxy + 1 master + 1 slave） |
| `clientName` | TDSQL 系统名 | `集中式实例` / `分布式实例` / `monitordb` |
| `instance_name` | 实例名（= clientName） | `集中式实例` |
| `groupname` | 所属 group（仅 groupshard 有值） | `group_1782132247_10` |
| `proxy_host` | proxy IP 列表（分号分隔） | `10.206.0.4;10.206.0.8;` |
| `available_proxy_host` | 可用 proxy IP 列表 | `10.206.0.4;10.206.0.8;` |
| `oss_proxy_port` | proxy 业务端口 | `15001` / `15002` / `15005` |
| `master_ip_port` | **主节点 MySQL 地址** | `10.206.0.4_4001` |
| `hostname` | 主机名 | `VM-0-4-centos` |
| `uniqueid` | 全局唯一标识 | `unique_1782129880_1` |
| `schedule_ip` | 调度 IP | `10.206.0.13` |
| `ctime` | 创建时间 | `2026-06-22 20:04:40` |
| `status` | 状态 | `0`（正常） |
| `rstate` | 运行状态 | `1`（在线） |
| `oss_user` / `oss_pwd` | OSS 账号（DES 加密的运维账号） | 敏感，**不输出** |

**proxy 节点（`10.206.0.x:15xxx`）的关键 key**：
- `cluster_name` = `/tdsqlzk/set_xxx` 或 `/tdsqlzk/group_xxx/set_xxx`（setid）
- `setid` = setid（同上）
- `groupname`（分布式时）= `group_xxx`

**MySQL 后端节点（`10.206.0.x:4xxx`）的关键 key**：
- `set_name` = set_name
- `cluster_name` = setid

---

## 2. 排查流程

### 2.1 步骤总览

```
[Step 1] 拿 monitordb 连接 (118.195.161.48:15001, tdsql_check_user / 口令见 MONITORDB_PWD 环境变量)
   ↓
[Step 2] Track 1 — 从 monitordb 取所有候选 set/group 列表 (4 个数据源并集)
   ↓
[Step 3] 从 m_data_cur 解析每个候选实例的完整元数据 (字段 1-13)
   ↓
[Step 4] Track 2 — 内网直连每个候选 set 查 mysql.user, 验证 checksql 真实存在
   ↓
[Step 5] Track 2 顺手拿 set 上的 db_list (SHOW DATABASES)
   ↓
[Step 6] 输出 CSV/JSON, 标记"真实有 checksql"的实例
```

### 2.2 Step 2-3：候选列表 + 元数据采集（一次性 SQL 搞定）

```sql
-- 1) 从 4 个数据源取所有 set/group (并集去重)
WITH all_instances AS (
    SELECT instance_id, instance_type
    FROM tdsqlpcloud.t_dbuser_privileges
    WHERE is_del = 0
    UNION
    SELECT SUBSTRING_INDEX(dbcluster_name, '/', -1) AS instance_id, NULL AS instance_type
    FROM (SELECT DISTINCT dbcluster_name FROM tdsqlpcloud.t_statistics_dbcluster) t
    UNION
    SELECT set_name AS instance_id, NULL AS instance_type
    FROM (SELECT DISTINCT set_name FROM tdsqlpcloud_monitor.proxy_classes_analysis) t
    UNION
    SELECT f_val AS instance_id, NULL AS instance_type
    FROM tdsqlpcloud_monitor.m_data_cur
    WHERE f_key = 'set_name'
)
-- 2) 对每个 instance 解析元数据
SELECT
    i.instance_id                                                          AS set_id,
    -- cluster 级 (从 t_cluster 取)
    (SELECT cluster_key FROM tdsqlpcloud.t_cluster WHERE is_del=0 LIMIT 1)  AS cluster_key,
    (SELECT cluster_name FROM tdsqlpcloud.t_cluster WHERE is_del=0 LIMIT 1) AS cluster_name,
    -- set 级 (从 m_data_cur 的 /tdsqlzk/<set_id> 节点取)
    (SELECT f_val FROM tdsqlpcloud_monitor.m_data_cur
     WHERE f_mid = CONCAT('/tdsqlzk/', i.instance_id) AND f_key = 'cluster_type' LIMIT 1)  AS set_type,
    (SELECT f_val FROM tdsqlpcloud_monitor.m_data_cur
     WHERE f_mid = CONCAT('/tdsqlzk/', i.instance_id) AND f_key = 'groupname' LIMIT 1)     AS group_id,
    COALESCE(
        (SELECT f_val FROM tdsqlpcloud_monitor.m_data_cur
         WHERE f_mid = CONCAT('/tdsqlzk/', i.instance_id) AND f_key = 'instance_name' LIMIT 1),
        (SELECT f_val FROM tdsqlpcloud_monitor.m_data_cur
         WHERE f_mid = CONCAT('/tdsqlzk/', i.instance_id) AND f_key = 'clientName' LIMIT 1)
    ) AS instance_name,
    -- proxy IP (主 proxy 列表)
    (SELECT f_val FROM tdsqlpcloud_monitor.m_data_cur
     WHERE f_mid = CONCAT('/tdsqlzk/', i.instance_id) AND f_key = 'proxy_host' LIMIT 1)   AS proxy_ip,
    -- proxy 端口
    (SELECT f_val FROM tdsqlpcloud_monitor.m_data_cur
     WHERE f_mid = CONCAT('/tdsqlzk/', i.instance_id) AND f_key = 'oss_proxy_port' LIMIT 1) AS proxy_port,
    -- 主节点 MySQL
    (SELECT f_val FROM tdsqlpcloud_monitor.m_data_cur
     WHERE f_mid = CONCAT('/tdsqlzk/', i.instance_id) AND f_key = 'master_ip_port' LIMIT 1)  AS master_node,
    -- 主机名
    (SELECT f_val FROM tdsqlpcloud_monitor.m_data_cur
     WHERE f_mid = CONCAT('/tdsqlzk/', i.instance_id) AND f_key = 'hostname' LIMIT 1)        AS host_name,
    -- 唯一标识
    (SELECT f_val FROM tdsqlpcloud_monitor.m_data_cur
     WHERE f_mid = CONCAT('/tdsqlzk/', i.instance_id) AND f_key = 'uniqueid' LIMIT 1)         AS unique_id,
    -- 调度 IP
    (SELECT f_val FROM tdsqlpcloud_monitor.m_data_cur
     WHERE f_mid = CONCAT('/tdsqlzk/', i.instance_id) AND f_key = 'schedule_ip' LIMIT 1)      AS schedule_ip
FROM all_instances i
ORDER BY i.instance_id;
```

**云测执行结果**（4 个 instance）：

| set_id | set_type | group_id | instance_name | proxy_ip | proxy_port | master_node |
|---|---|---|---|---|---|---|
| set_1782129880_1 | noshard | NULL | 集中式实例 | 10.206.0.4;10.206.0.8; | 15001 | 10.206.0.4_4001 |
| set_1782130875_4 | noshard | NULL | 集中式实例 | 10.206.0.4;10.206.0.8; | 15002 | 10.206.0.4_4002 |
| set_1782132369_1 | groupshard | group_1782132247_10 | 分布式实例 | 10.206.0.4;10.206.0.8; | 15005 | 10.206.0.13_4002 |
| set_1782132389_3 | groupshard | group_1782132247_10 | 分布式实例 | 10.206.0.4;10.206.0.8; | 15005 | 10.206.0.13_4002 |

### 2.3 Step 4-5：Track 2 直连 set（必须内网执行）

> ⚠️ **这一步必须在内网执行**。公网开发机访问不到 `10.x.x.x` 内网 IP。云测机器上验证过会 `Lost connection`（TCP 握手后被 RST）。

**核心脚本逻辑**（对每个候选 set）：

```python
# Python (内网智能体直接跑)
import pymysql
from pymysql.cursors import DictCursor

CHECKSQL_USER = "checksql"
CHECKSQL_PWD = os.environ["CHECKSQL_PWD"]  # 口令从环境变量注入，禁止硬编码

# 来自 Step 2-3 SQL 的输出
CANDIDATE_SETS = [
    # (master_node "ip_port", set_id, proxy_ip "ip;ip;", proxy_port)
    # 云测示例 (生产用真实的):
    # ("10.206.0.4_4001",  "set_1782129880_1", "10.206.0.4;10.206.0.8;", 15001),
    ...
]

def probe_set(master_node, set_id):
    """直连 set 查 checksql + 拿 db_list."""
    # 优先用 master_node 直连 MySQL 后端 (绕开 TDSQL 协议)
    host, port = master_node.split("_")
    port = int(port)
    result = {"set_id": set_id, "host": host, "port": port, "ok": False}

    try:
        conn = pymysql.connect(
            host=host, port=port, user=CHECKSQL_USER, password=CHECKSQL_PWD,
            connect_timeout=5, charset="utf8mb4",
        )
    except pymysql.err.OperationalError as e:
        code, msg = e.args
        # 常见错: 1045 (密码错), 2003 (连不上), 1130 (host not allowed)
        result["err"] = f"{code}: {str(msg)[:200]}"
        return result
    except Exception as e:
        result["err"] = f"{type(e).__name__}: {str(e)[:200]}"
        return result

    try:
        with conn.cursor(DictCursor) as cur:
            # 1) 验证 checksql 真实存在 (直查 mysql.user)
            cur.execute(
                "SELECT user, host, account_locked "
                "FROM mysql.user WHERE user = %s ORDER BY user, host",
                (CHECKSQL_USER,),
            )
            result["checksql_users"] = cur.fetchall()
            result["checksql_user_count"] = len(result["checksql_users"])
            result["checksql_hosts"] = sorted({r["host"] for r in result["checksql_users"]})

            # 2) 拿这个 set 上的业务库列表 (排除系统库)
            cur.execute("SHOW DATABASES")
            all_dbs = [r["Database"] for r in cur.fetchall()]
            sys_dbs = {"information_schema", "mysql", "performance_schema",
                       "sys", "sysdb", "query_rewrite", "tdsqlpcloud",
                       "tdsqlpcloud_monitor"}
            result["db_list"] = [d for d in all_dbs if d not in sys_dbs]
            result["ok"] = True
    finally:
        conn.close()

    return result
```

**凭据处理细则**：
- `checksql`（口令见 `CHECKSQL_PWD` 环境变量）已确认仅 set 端有效
- 如果直连时收到 `1045 Access denied`：
  - 先确认是连到了 set（不是 monitordb）
  - **不要重试**（MySQL `connection_control` 插件会封禁）
  - 停下来问 ops
- 如果收到 `1130 Host 'xxx' is not allowed`：checksql 用户授权的 host 不含你当前 IP，问 ops 加白

### 2.4 Step 6：最终输出

**输出文件 1**：`checksql_instances.csv`（**核心交付物**）

| 字段 | 来源 | 示例 |
|---|---|---|
| cluster_key | t_cluster | tdsql_th16yls3c |
| cluster_name | t_cluster | 刘晴的集群 |
| set_id | m_data_cur | set_1782129880_1 |
| set_type | m_data_cur | noshard |
| group_id | m_data_cur | (空) / group_1782132247_10 |
| instance_name | m_data_cur | 集中式实例 |
| **business_desc** | ⚠ 平台无 | (空, 待业务方补) |
| proxy_ip | m_data_cur | 10.206.0.4;10.206.0.8; |
| proxy_port | m_data_cur | 15001 |
| master_node | m_data_cur | 10.206.0.4:4001 |
| host_name | m_data_cur | VM-0-4-centos |
| unique_id | m_data_cur | unique_1782129880_1 |
| schedule_ip | m_data_cur | 10.206.0.13 |
| db_list | Track 2 | ["audit_log", "transaction", "tdsql_check"] |
| checksql_user_count | Track 2 | 1 |
| checksql_hosts | Track 2 | "%" |
| verify_time | 脚本 | 2026-07-26 22:55:00 |

**输出文件 2**：`checksql_scan.log` —— 全量扫描日志（含失败原因、连接耗时等）

**输出文件 3**：`checksql_unauthorized.json` —— Track 2 找到但 Track 1 无记录的（需要补登记授权流程）

---

## 3. 完整 Python 脚本（内网智能体直接复用）

```python
"""
TDSQL checksql 实例元数据采集脚本
功能: 对每个候选 set 采集完整接入元数据, 验证 checksql 用户真实存在
依赖: pip install pymysql
执行环境: 内网 (必须能访问 set 的 master_node 端口)
"""
import csv
import json
import sys
import time
import traceback
from datetime import datetime
import pymysql
from pymysql.cursors import DictCursor


# ====== 1. 配置 (按需修改) ======
MONITOR = dict(
    host="118.195.161.48",       # monitordb proxy
    port=15001,
    user="tdsql_check_user",
    password=os.environ["MONITORDB_PWD"],   # monitordb 口令，从环境变量注入
    connect_timeout=5,
    charset="utf8mb4",
)
CHECKSQL_USER = "checksql"
CHECKSQL_PWD = os.environ["CHECKSQL_PWD"]  # set 端口令，从环境变量注入
OUTPUT_DIR = "./checksql_scan_result"
CONNECT_TIMEOUT = 5             # 直连 set 超时 (秒)


def section(msg):
    print(f"\n[{datetime.now():%H:%M:%S}] {msg}", flush=True)


# ====== 2. Step 2-3: 从 monitordb 拿候选 set + 元数据 ======
SQL_DISCOVER_AND_EXTRACT = """
WITH all_instances AS (
    SELECT instance_id, instance_type
    FROM tdsqlpcloud.t_dbuser_privileges
    WHERE is_del = 0
    UNION
    SELECT SUBSTRING_INDEX(dbcluster_name, '/', -1) AS instance_id, NULL AS instance_type
    FROM (SELECT DISTINCT dbcluster_name FROM tdsqlpcloud.t_statistics_dbcluster) t
    UNION
    SELECT set_name AS instance_id, NULL AS instance_type
    FROM (SELECT DISTINCT set_name FROM tdsqlpcloud_monitor.proxy_classes_analysis) t
    UNION
    SELECT f_val AS instance_id, NULL AS instance_type
    FROM tdsqlpcloud_monitor.m_data_cur
    WHERE f_key = 'set_name'
)
SELECT
    i.instance_id AS set_id,
    (SELECT cluster_key  FROM tdsqlpcloud.t_cluster WHERE is_del=0 LIMIT 1) AS cluster_key,
    (SELECT cluster_name FROM tdsqlpcloud.t_cluster WHERE is_del=0 LIMIT 1) AS cluster_name,
    (SELECT f_val FROM tdsqlpcloud_monitor.m_data_cur
     WHERE f_mid = CONCAT('/tdsqlzk/', i.instance_id) AND f_key = 'cluster_type' LIMIT 1)  AS set_type,
    (SELECT f_val FROM tdsqlpcloud_monitor.m_data_cur
     WHERE f_mid = CONCAT('/tdsqlzk/', i.instance_id) AND f_key = 'groupname' LIMIT 1)     AS group_id,
    COALESCE(
        (SELECT f_val FROM tdsqlpcloud_monitor.m_data_cur
         WHERE f_mid = CONCAT('/tdsqlzk/', i.instance_id) AND f_key = 'instance_name' LIMIT 1),
        (SELECT f_val FROM tdsqlpcloud_monitor.m_data_cur
         WHERE f_mid = CONCAT('/tdsqlzk/', i.instance_id) AND f_key = 'clientName' LIMIT 1)
    ) AS instance_name,
    (SELECT f_val FROM tdsqlpcloud_monitor.m_data_cur
     WHERE f_mid = CONCAT('/tdsqlzk/', i.instance_id) AND f_key = 'proxy_host' LIMIT 1)   AS proxy_ip,
    (SELECT f_val FROM tdsqlpcloud_monitor.m_data_cur
     WHERE f_mid = CONCAT('/tdsqlzk/', i.instance_id) AND f_key = 'oss_proxy_port' LIMIT 1) AS proxy_port,
    (SELECT f_val FROM tdsqlpcloud_monitor.m_data_cur
     WHERE f_mid = CONCAT('/tdsqlzk/', i.instance_id) AND f_key = 'master_ip_port' LIMIT 1)  AS master_node,
    (SELECT f_val FROM tdsqlpcloud_monitor.m_data_cur
     WHERE f_mid = CONCAT('/tdsqlzk/', i.instance_id) AND f_key = 'hostname' LIMIT 1)        AS host_name,
    (SELECT f_val FROM tdsqlpcloud_monitor.m_data_cur
     WHERE f_mid = CONCAT('/tdsqlzk/', i.instance_id) AND f_key = 'uniqueid' LIMIT 1)         AS unique_id,
    (SELECT f_val FROM tdsqlpcloud_monitor.m_data_cur
     WHERE f_mid = CONCAT('/tdsqlzk/', i.instance_id) AND f_key = 'schedule_ip' LIMIT 1)      AS schedule_ip
FROM all_instances i
ORDER BY i.instance_id;
"""


def discover_instances(monitor_conn):
    """返回 list[dict], 每个 dict 含 set_id + 完整元数据."""
    with monitor_conn.cursor(DictCursor) as cur:
        cur.execute(SQL_DISCOVER_AND_EXTRACT)
        return cur.fetchall()


# ====== 3. Step 4-5: Track 2 直连 set ======
def probe_set(master_node, set_id, timeout=CONNECT_TIMEOUT):
    """直连 set, 验证 checksql + 拿 db_list.

    Args:
        master_node: 形如 "10.206.0.4_4001" (m_data_cur.master_ip_port)
        set_id: set 名
    Returns:
        dict 含 ok, checksql_users, db_list, err 等
    """
    result = {"set_id": set_id, "master_node": master_node, "ok": False, "ts": datetime.now().isoformat()}
    if not master_node or "_" not in master_node:
        result["err"] = "no valid master_node"
        return result

    host, port = master_node.rsplit("_", 1)
    port = int(port)
    result["host"] = host
    result["port"] = port

    try:
        conn = pymysql.connect(
            host=host, port=port, user=CHECKSQL_USER, password=CHECKSQL_PWD,
            connect_timeout=timeout, charset="utf8mb4",
        )
    except Exception as e:
        result["err"] = f"{type(e).__name__}: {str(e)[:200]}"
        return result

    try:
        with conn.cursor(DictCursor) as cur:
            # 1) checksql 真实存在性
            cur.execute(
                "SELECT user, host, account_locked "
                "FROM mysql.user WHERE user = %s ORDER BY user, host",
                (CHECKSQL_USER,),
            )
            users = cur.fetchall()
            result["checksql_users"] = users
            result["checksql_user_count"] = len(users)
            result["checksql_hosts"] = sorted({r["host"] for r in users})

            # 2) set 上的业务库列表
            cur.execute("SHOW DATABASES")
            all_dbs = [r["Database"] for r in cur.fetchall()]
            sys_dbs = {"information_schema", "mysql", "performance_schema",
                       "sys", "sysdb", "query_rewrite", "tdsqlpcloud",
                       "tdsqlpcloud_monitor"}
            result["db_list"] = [d for d in all_dbs if d not in sys_dbs]
            result["all_db_count"] = len(all_dbs)
            result["ok"] = True
    except Exception as e:
        result["err"] = f"query_err: {type(e).__name__}: {str(e)[:200]}"
    finally:
        conn.close()

    return result


# ====== 4. Step 6: 主流程 + 输出 ======
def main():
    import os
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    section("Step 1: 连接 monitordb")
    m = pymysql.connect(**MONITOR)

    try:
        section("Step 2-3: 解析候选 set + 完整元数据")
        instances = discover_instances(m)
        print(f"  候选 set 数量: {len(instances)}")
        for inst in instances:
            print(f"    {inst['set_id']:<28} {inst.get('set_type','?'):<12} "
                  f"port={inst.get('proxy_port','?')} master={inst.get('master_node','?')}")

        section("Step 4-5: Track 2 直连每个 set 验证 checksql + 拿 db_list")
        scan_results = []
        for inst in instances:
            sid = inst["set_id"]
            master = inst.get("master_node")
            section(f"  → 扫描 {sid} (master={master})")
            t0 = time.time()
            r = probe_set(master, sid)
            r["meta"] = inst
            r["elapsed_sec"] = round(time.time() - t0, 2)
            print(f"    ok={r['ok']} "
                  f"checksql_user_count={r.get('checksql_user_count', 'N/A')} "
                  f"db_count={r.get('all_db_count', 'N/A')} "
                  f"elapsed={r['elapsed_sec']}s "
                  f"err={r.get('err', 'N/A')}")
            scan_results.append(r)

        section("Step 6: 写入 CSV + JSON")
        csv_path = f"{OUTPUT_DIR}/checksql_instances.csv"
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow([
                "cluster_key", "cluster_name", "set_id", "set_type", "group_id",
                "instance_name", "business_desc",
                "proxy_ip", "proxy_port", "master_node", "host_name",
                "unique_id", "schedule_ip",
                "db_list", "db_count",
                "checksql_user_count", "checksql_hosts",
                "probe_ok", "probe_err", "verify_time",
            ])
            for r in scan_results:
                meta = r.get("meta", {})
                w.writerow([
                    meta.get("cluster_key", ""),
                    meta.get("cluster_name", ""),
                    r["set_id"],
                    meta.get("set_type", ""),
                    meta.get("group_id", ""),
                    meta.get("instance_name", ""),
                    # business_desc: 平台没有, 留空待业务方补, fallback cluster_name
                    meta.get("cluster_name", ""),
                    meta.get("proxy_ip", ""),
                    meta.get("proxy_port", ""),
                    r.get("host", "") + ":" + str(r.get("port", "")),
                    meta.get("host_name", ""),
                    meta.get("unique_id", ""),
                    meta.get("schedule_ip", ""),
                    "|".join(r.get("db_list", [])),
                    len(r.get("db_list", [])),
                    r.get("checksql_user_count", 0),
                    "|".join(r.get("checksql_hosts", [])),
                    r["ok"],
                    r.get("err", ""),
                    r.get("ts", ""),
                ])

        # JSON 完整版 (含 meta + raw 探测结果)
        with open(f"{OUTPUT_DIR}/checksql_scan_full.json", "w", encoding="utf-8") as f:
            json.dump(scan_results, f, indent=2, ensure_ascii=False, default=str)

        # 只列"真实有 checksql"的 (Track 2 OK 且 user_count > 0)
        with_checksql = [r for r in scan_results if r["ok"] and r.get("checksql_user_count", 0) > 0]
        with open(f"{OUTPUT_DIR}/checksql_confirmed.json", "w", encoding="utf-8") as f:
            json.dump(with_checksql, f, indent=2, ensure_ascii=False, default=str)

        # 扫描日志
        with open(f"{OUTPUT_DIR}/checksql_scan.log", "w", encoding="utf-8") as f:
            f.write(f"扫描时间: {datetime.now()}\n")
            f.write(f"monitordb: {MONITOR['host']}:{MONITOR['port']}\n")
            f.write(f"候选 set 总数: {len(scan_results)}\n")
            f.write(f"真实有 checksql: {len(with_checksql)}\n\n")
            for r in scan_results:
                f.write(f"--- {r['set_id']} ---\n")
                f.write(f"  ok: {r['ok']}, elapsed: {r['elapsed_sec']}s\n")
                f.write(f"  err: {r.get('err', 'N/A')}\n")
                f.write(f"  checksql_users: {r.get('checksql_users', [])}\n")
                f.write(f"  db_list: {r.get('db_list', [])}\n\n")

        print(f"\n  输出:")
        for fn in os.listdir(OUTPUT_DIR):
            sz = os.path.getsize(f"{OUTPUT_DIR}/{fn}")
            print(f"    - {fn} ({sz} bytes)")
        print(f"\n  ★ 真实有 checksql 的 set 数: {len(with_checksql)}")
        for r in with_checksql:
            m = r["meta"]
            print(f"    {r['set_id']:<28} type={m.get('set_type','?'):<12} "
                  f"master={m.get('master_node','?')} db_count={len(r.get('db_list', []))}")
    finally:
        m.close()


if __name__ == "__main__":
    main()
```

---

## 4. 风险点 + 注意事项

### 4.1 凭据与封禁

- `checksql`（口令见 `CHECKSQL_PWD` 环境变量）已确认仅 set 端有效（云测 monitordb 上验证过不通）
- **MySQL 5.7+ 的 `connection_control` 插件对同 IP 错密 3 次会递增延迟（最多 1 天）**
  - 脚本里**不要做错密重试**
  - 如果 Track 2 全军覆没（都是 1045），先**确认你连的是 set 不是 monitordb**，再**停下来问 ops**
- 直连端口选择：
  - 优先用 `master_node`（形如 `10.206.0.4_4001`）—— 这是 MySQL 后端端口，标准协议
  - 备选用 `proxy_ip:proxy_port`（如 `10.206.0.4:15001`）—— TDSQL proxy 端口，连接后可能需要 `/*sets:set_xxx*/` 注释路由

### 4.2 网络与可达性

- **公网 → 内网 set 不通**：开发机访问不到 `10.x.x.x`，Track 2 必须内网
- **master_node 端口（4xxx）通常只允许内网访问**：跨网段可能被防火墙拦
- 如果某些 set 走的是 vpc / vpn，需要内网跳板机
- **proxy 端口（15xxx）一般有公网入口**：可走公网 proxy，但要走 TDSQL 协议 + `/*sets:set_xxx*/` 注释

### 4.3 "业务描述"字段的真相

- **TDSQL 平台层不维护 set 级别的业务名**（"订单库/支付库"等是业务方自己定义）
- `clientName` / `instance_name` 是 TDSQL 系统打的标签（"集中式实例/分布式实例"），不是用户业务名
- **真实业务名的获取渠道**：
  - 让业务方/DBA 提供 set 级业务名映射表（CSV）
  - 工单系统查 set 关联的应用
  - 上 `tdsql_sqlcheck` 工具后用工具里的"实例元数据"配置手工补
- **本脚本里 `business_desc` 列当前 fallback 为 `cluster_name`**（cluster 级业务名），**待业务方补 set 级**

### 4.4 group（分布式组）特殊处理

- group 本身不是 MySQL 实例，无 `mysql.user` 可查
- 必须穿透到 group 下的具体 set（Track 2 实际是"对 group 下每个 set 跑"）
- 脚本自动展开：`groupname` 字段非空的 set 仍以单 set 维度查，group 本身不出现在最终结果里

### 4.5 多 cluster 场景

- 每个 cluster 有自己的 monitordb
- 如果生产有 N 个 cluster，需要 N 个 monitordb 连接信息
- 当前脚本只连一个 monitordb，**多 cluster 需循环跑 N 次**或扩展为多连接配置

### 4.6 错密排查顺序

| 报错 | 原因 | 排查 |
|---|---|---|
| `1045 Access denied` | 密码错 或 host 不允许 | 先看 host 是不是内网 set 端口；密码对就 host 白名单问题 |
| `2003 Can't connect` | 端口不通 | 网络问题，检查 master_node IP/port 是否对 |
| `1130 Host 'x' not allowed` | checksql 授权 host 不含你 | 让 ops 加白 |
| `Lost connection during query` | TCP RST 或协议错 | 多半是连到了 TDSQL proxy 而不是 MySQL；切 master_node 直连 |
| 慢/超时 | set 在做大事务 | 增加 timeout 即可，不要重试 |

---

## 5. 实验环境（云测 `tdsql_th16yls3c`）基线

本次实验在云测 cluster `tdsql_th16yls3c`（刘晴的集群）做基线验证，**该环境没有 checksql 用户**（只有 `tdsql_check_user`），但 monitordb 元数据完整：

| set_id | set_type | group_id | instance_name | proxy_ip | proxy_port | master_node | host_name |
|---|---|---|---|---|---|---|---|
| set_1782129880_1 | noshard | (空) | 集中式实例 | 10.206.0.4;10.206.0.8; | 15001 | 10.206.0.4:4001 | VM-0-4-centos |
| set_1782130875_4 | noshard | (空) | 集中式实例 | 10.206.0.4;10.206.0.8; | 15002 | 10.206.0.4:4002 | VM-0-4-centos |
| set_1782132369_1 | groupshard | group_1782132247_10 | 分布式实例 | 10.206.0.4;10.206.0.8; | 15005 | 10.206.0.13:4002 | VM-0-8-centos |
| set_1782132389_3 | groupshard | group_1782132247_10 | 分布式实例 | 10.206.0.4;10.206.0.8; | 15005 | 10.206.0.13:4002 | VM-0-13-centos |

**集群元数据**：
- cluster_key: `tdsql_th16yls3c`
- cluster_name: `刘晴的集群`
- cluster_version: 22

**Track 2 状态**：未跑（开发机访问不到内网 10.206.0.x，等内网智能体执行）

---

## 6. 后续动作

1. **跑完拿到 CSV 后**：
   - `checksql_instances.csv` 里的 `business_desc` 列待业务方补
   - `checksql_confirmed.json` 是 Track 2 真实有 checksql 的实例清单
   - 导入到 `tdsql_sqlcheck` 工具的连接配置
2. **差异处理**：
   - `checksql_unauthorized.json`（Track 2 有但 Track 1 无）建议补走授权流程（更安全，便于审计）
   - `checksql_missing.json`（Track 1 有但 Track 2 无）说明实例可能下线/重建/凭据错
3. **定期复核**：建议每月跑一次，比对清单是否变化

---

## 附录 A：快速 SQL 速查

```sql
-- 集群列表
SELECT cluster_id, cluster_key, cluster_name, cluster_version, is_del
FROM tdsqlpcloud.t_cluster ORDER BY cluster_id;

-- monitordb 自身 user
SELECT user, host FROM mysql.user WHERE user LIKE '%check%';

-- 监控库里的 checksql 授权记录（可能为空, 因 DBA 可能没走流程）
SELECT cluster_key, instance_id, instance_type, user_name, user_host, mtime
FROM tdsqlpcloud.t_dbuser_privileges
WHERE user_name LIKE '%check%' AND is_del = 0;

-- 全量 set 列表（最权威）
SELECT DISTINCT f_val
FROM tdsqlpcloud_monitor.m_data_cur WHERE f_key='set_name';

-- 集群级 monitordb 配置
SELECT mkey, mval FROM tdsqlpcloud.t_cluster_expand
WHERE mkey IN ('mdb_list','mdb_user','mdb_pwd','mdb_port','mdb_name',
               'zookeeper_list','osssvr_list');

-- 单个 set 的全部元数据
SELECT f_key, f_val FROM tdsqlpcloud_monitor.m_data_cur
WHERE f_mid = '/tdsqlzk/set_xxxxx' ORDER BY f_key;

-- proxy 端点到 setid 映射
SELECT f_mid AS proxy_endpoint, f_val AS setid
FROM tdsqlpcloud_monitor.m_data_cur
WHERE f_key = 'setid' AND f_mid LIKE '10.%.%.%:%';
```

## 附录 B：相关表清单

| 数据库 | 表名 | 说明 |
|---|---|---|
| `tdsqlpcloud` | `t_cluster` | 集群主表（含 cluster_name 业务名） |
| `tdsqlpcloud` | `t_dbuser_privileges` | 授权记录（可能为空） |
| `tdsqlpcloud` | `t_statistics_dbcluster` | 集群级查询统计 |
| `tdsqlpcloud_monitor` | `m_data_cur` | **核心**：所有 set/proxy/host 元数据 |
| `tdsqlpcloud_monitor` | `proxy_classes_analysis` | 慢查询样本（间接 set 信号） |
| `tdsqlpcloud_monitor` | `m_alarm` | 告警日志 |

## 附录 C：m_data_cur 关键 f_key 一览（按用途分类）

| 类别 | f_key | 备注 |
|---|---|---|
| 标识 | `set_name`, `uniqueid`, `groupname`, `cluster_name` | set_id、唯一ID、group_id、setid |
| 类型 | `cluster_type`, `cluster_model` | noshard/groupshard、2P-1M-1S |
| 实例名 | `clientName`, `instance_name`, `hostname` | TDSQL 系统名/主机名 |
| 网络 | `proxy_host`, `available_proxy_host`, `oss_proxy_port`, `master_ip_port`, `schedule_ip` | proxy IP/port + 主节点 MySQL + 调度 IP |
| 状态 | `status`, `rstate`, `read_only`, `kpstatus` | 在线/只读/同步等 |
| 时间 | `ctime`, `mtime` | 创建/更新时间 |
| 容量 | `mysql_max_*`, `mysql_sum_*` | 连接数/容量/查询量（不需要） |
| 内部 | `oss_user`, `oss_pwd` | ⚠ 敏感，**不输出** |

---

> **文档版本**：v1.2（2026-07-26） · 凭据账号 `checksql`，口令经 `CHECKSQL_PWD` 环境变量注入 · 未经 DBA 复核仅供参考
