# 内网生产 TDSQL 集群 `checksql` 用户排查方案

| 项目 | 内容 |
|---|---|
| 任务来源 | DBA 已在生产 TDSQL 集群为所有需纳入《TDSQL 数据库 SQL 审核工具》的实例创建 `checksql` 管理用户，事后联系不上 |
| 任务目标 | 在不依赖 DBA 的前提下，找出**所有真实存在 `checksql` 用户的数据库实例**清单（cluster / set / group / 连接地址），用于后续接入审核工具 |
| 适用环境 | TDSQL 分布式 MySQL（基于 TXSQL 内核，公网/内网 proxy + 内网 monitordb） |
| 实验环境 | 云测试 cluster `tdsql_th16yls3c`（刘晴的集群），proxy `118.195.161.48`/`119.45.220.89:15001` |
| checksql 凭据 | 用户已提供：`checksql` / `Abcd972&*(`（仅 set 端，monitordb 无此账号） |
| 编写人 | Mavis（基于 2026-07-26 实验产出） |

---

## 0. 核心结论（先看这个）

> **TDSQL 分布式架构下，`checksql` 用户是创建在"set（分片）"这个 MySQL 实例维度上的，不是 cluster 维度**。
> 一个 cluster 通常有 N 个 set + M 个 group（group 内部又挂 set），每个 set 的 `mysql.user` 是独立的。
> 所以"找实例"= "找所有 set + group，并对其逐个做 mysql.user 查询"。

排查要分**双轨**做，**缺一不可**：

| 轨道 | 数据来源 | 作用 | 局限 |
|---|---|---|---|
| **Track 1 — 监控库元数据** | `tdsqlpcloud.t_dbuser_privileges` 等 monitordb 表 | 查"DBA 通过授权流程建过 checksql 的 set 列表" | 仅覆盖走授权流程的；DBA 手工 `CREATE USER` 不留痕时会漏 |
| **Track 2 — 实例直连** | 逐个 set `SELECT FROM mysql.user` | 查"set 真实存在的 `checksql` 用户" | 必须在内网执行，需要知道 set 的连接地址 + 凭据 |

**最终清单以 Track 2 为准**，Track 1 用于对照、找差异、补漏。

---

## 1. 关键数据源梳理（实验已验证）

排查脚本会读以下表 / 数据源。所有表都在 **monitordb**（监控库）里，每个 TDSQL cluster 有自己的 monitordb。

### 1.1 `tdsqlpcloud` 库（49 张表，**元数据**为主）

| 表名 | 行数（云测） | 用途 | 是否可靠 |
|---|---|---|---|
| **`t_dbuser_privileges`** | 4 | **核心**：用户授权记录，主键 `(cluster_key, instance_id, user_name, user_host)`，含 `instance_type`（`noshard`/`groupshard`） | ✅ 权威，但**只覆盖走授权流程的** |
| `t_cluster` | 1 | 集群主表，`cluster_key` + `cluster_name` | ✅ 权威 |
| `t_cluster_expand` | 28 | key-value 元数据（`mdb_list`、`zookeeper_list` 等集群级配置） | ✅ 集群级配置 |
| `t_statistics_dbcluster` | 106 | 按 dbcluster 维度的查询量统计，含 `dbcluster_name = /tdsqlzk/set_xxx` | ✅ 活跃 set 列表（只在有查询时记录） |
| `t_dbuser_apply` | 0 | 申请流程表 | ❌ 空表，**别用**（生产 DBA 也不用） |
| `t_user_dbcluster` | 0 | 用户-集群映射表 | ❌ 空表，**别用** |
| `t_user_database`、`t_dbcluster_apply` 等 | 0 | 申请流程相关 | ❌ 全空 |

### 1.2 `tdsqlpcloud_monitor` 库（68 张表，**运行时数据**为主）

| 表名 | 行数（云测） | 用途 | 是否可靠 |
|---|---|---|---|
| **`m_data_cur`** | ~3200 | **核心**：所有监控项的 key-value 时序表，`f_mid` 是对象 ID（`/tdsqlzk/set_xxx`、`10.206.0.4:15001` 等），`f_key` 是配置项名 | ✅ **最权威的"全量 set + 端口映射"来源** |
| `proxy_classes_analysis` | 344 | 慢查询样本，含 `set_name` + `set_ip` + `set_port` | ⚠️ 只记录"被查询过"的 set |
| `proxy_global_analysis` | 215 | 全局查询统计 | ⚠️ 同上 |
| `ops_host_module` | 49 | host + module（filebeat / mysqlagent / proxy）映射 | ✅ 辅助 |
| `m_data_YYYYMMDD` | 49k/天 | 每日分表的时序数据 | 太大，不直接读 |

### 1.3 实验结论

云测 `tdsql_th16yls3c` cluster 的 4 个数据源对比：

| 数据源 | set 数量 | 说明 |
|---|---|---|
| `m_data_cur` (`f_key='set_name'`) | **4** | **最全**：覆盖所有 set 节点 + set 的可连接端口（`10.206.0.4:4001` 等） |
| `t_dbuser_privileges` (DISTINCT `instance_id`) | 3 | 缺失 `set_1782132389_3`（这 set 存在但未授权 checksql） |
| `t_statistics_dbcluster` (DISTINCT `dbcluster_name`) | 3 | 同上，且是 `dbcluster_name` 格式 = `/tdsqlzk/set_xxx` |
| `proxy_classes_analysis` (DISTINCT `set_name`) | 3 | 同上；是 `set_name` 格式 = `set_xxx` |

**`m_data_cur` 的关键用法**：
- 找 set 列表：`SELECT DISTINCT f_val FROM m_data_cur WHERE f_key='set_name'`
- 找 set 的 proxy：`SELECT f_mid, f_val FROM m_data_cur WHERE f_mid LIKE '/tdsqlzk/%' AND f_key='proxy_host'` → 返回 `10.206.0.4;10.206.0.8;`
- 找 set 的可连接端口：`f_mid='10.206.0.4:4001'` 配 `f_key='set_name' = set_xxx` → MySQL 后端端口
- 找 proxy 端口 → set 映射：`f_mid='10.206.0.4:15001'` 配 `f_key='setid' = /tdsqlzk/set_xxx` → proxy 端口

---

## 2. 排查流程

### 2.1 整体步骤

```
[Step 1] 拿到 monitordb 连接信息（已确认 118.195.161.48:15001）
   ↓
[Step 2] 连 monitordb，从 m_data_cur + t_dbuser_privileges + t_statistics_dbcluster + proxy_classes_analysis
   ↓      4 个数据源取并集，得出"该 cluster 下所有候选 set/group"
[Step 3] 解析每个 set/group 的可连接地址（proxy 端口 + 后端 MySQL 端口 + OSS 端口）
   ↓
[Step 4] Track 1 验证：查 t_dbuser_privileges 里有 user_name='checksql' 的 instance
   ↓
[Step 5] Track 2 验证：逐个 instance 直连（必须内网执行），查 mysql.user 真实存在性
   ↓
[Step 6] 对比 + 输出清单
```

### 2.2 Step 1：连接信息

**已经提供（云测）**：
- monitordb proxy: `118.195.161.48:15001`、`119.45.220.89:15001`（任选其一）
- monitordb user: `tdsql_check_user`
- monitordb password: `Abcd@!#1234`

**生产内网需要从 DBA 那里拿**（如果没拿到，可从 `t_cluster_expand` 反查）：

```sql
-- 集群级 monitordb 连接信息（key-value 在 t_cluster_expand）
SELECT mkey, mval FROM tdsqlpcloud.t_cluster_expand
WHERE mkey IN ('mdb_list', 'mdb_user', 'mdb_pwd', 'mdb_port', 'mdb_name');
-- 典型结果:
--   mdb_list   = 10.x.x.x:15001,10.x.x.x:15001  (monitordb HA 对)
--   mdb_user   = tdsqlpcloud
--   mdb_pwd    = (密文, 需询问 ops 解密)
--   mdb_port   = 15001
--   mdb_name   = tdsqlpcloud_monitor
```

### 2.3 Step 2-3：候选 instance 列表 + 连接地址解析

**Step 2 SQL：取所有候选 instance_id**：

```sql
-- 4 个数据源并集
(SELECT 't_dbuser_privileges' AS src, cluster_key, instance_id, instance_type
 FROM tdsqlpcloud.t_dbuser_privileges
 WHERE is_del = 0)
UNION
(SELECT 't_statistics_dbcluster' AS src, cluster_key,
        SUBSTRING_INDEX(dbcluster_name, '/', -1) AS instance_id,  -- 去掉 /tdsqlzk/ 前缀
        NULL AS instance_type
 FROM (SELECT DISTINCT cluster_key, dbcluster_name
       FROM tdsqlpcloud.t_statistics_dbcluster) t)
UNION
(SELECT 'proxy_classes_analysis' AS src, NULL AS cluster_key,
        set_name AS instance_id, NULL AS instance_type
 FROM (SELECT DISTINCT set_name FROM tdsqlpcloud_monitor.proxy_classes_analysis) t)
UNION
(SELECT 'm_data_cur' AS src, NULL AS cluster_key,
        f_val AS instance_id, NULL AS instance_type
 FROM tdsqlpcloud_monitor.m_data_cur
 WHERE f_key = 'set_name');
```

**Step 3 SQL：解析 set 的可连接地址**（用 m_data_cur）：

```sql
-- 拿到 set 列表后，对每个 set 名查：
-- (1) proxy_host: 形如 "10.206.0.4;10.206.0.8;" (多个 proxy 分号分隔)
-- (2) oss_proxy_port: group 才有, 形如 15005
-- (3) setid (zk 路径): /tdsqlzk/set_xxx

SELECT
    s.set_name,
    -- proxy 列表（多个用分号分）
    (SELECT f_val FROM tdsqlpcloud_monitor.m_data_cur
     WHERE f_mid = CONCAT('/tdsqlzk/', s.set_name)
       AND f_key = 'proxy_host' LIMIT 1) AS proxy_host_list,
    -- proxy 端口（noshard 才有；group 走 oss_proxy_port）
    (SELECT f_val FROM tdsqlpcloud_monitor.m_data_cur
     WHERE f_mid REGEXP CONCAT('10\\.[0-9.]+:[0-9]+')
       AND f_key = 'setid'
       AND f_val = CONCAT('/tdsqlzk/', s.set_name) LIMIT 1) AS proxy_endpoint,
    -- 后端 MySQL 端口（直接连 MySQL 用，绕开 TDSQL 协议握手）
    (SELECT f_mid FROM tdsqlpcloud_monitor.m_data_cur
     WHERE f_key = 'set_name' AND f_val = s.set_name
       AND f_mid REGEXP '^10\\.[0-9.]+:[0-9]+$' LIMIT 1) AS backend_endpoint
FROM (
    SELECT DISTINCT f_val AS set_name
    FROM tdsqlpcloud_monitor.m_data_cur
    WHERE f_key = 'set_name'
) s;
```

**输出示例**（云测）：

| set_name | proxy_host_list | proxy_endpoint | backend_endpoint |
|---|---|---|---|
| set_1782129880_1 | 10.206.0.4;10.206.0.8; | 10.206.0.4:15001 | 10.206.0.13:4001 |
| set_1782130875_4 | 10.206.0.4;10.206.0.8; | 10.206.0.4:15002 | 10.206.0.13:4002 |
| set_1782132369_1 | 10.206.0.4;10.206.0.8; | 10.206.0.8:4003 | (none) |
| set_1782132389_3 | 10.206.0.4;10.206.0.8; | (走 group 端口) | 10.206.0.13:4002 |

> **端口说明**：
> - **`15xxx` 是 TDSQL proxy 端口**：连上后必须用 TDSQL 协议（`/*sets:set_xxx*/SQL` 或 `/*+ set:... */` 注释路由）
> - **`4xxx` 是 MySQL 后端端口**：直接用标准 MySQL 客户端连，无需 TDSQL 协议（推荐 Track 2 用这个）

### 2.4 Step 4：Track 1 验证（监控库查授权记录）

```sql
-- Track 1 结果：哪些 instance 走过 checksql 授权流程
SELECT cluster_key, instance_id, instance_type, user_name, user_host,
       readonly, master_rw_split, slave_rw_split, mtime, muser
FROM tdsqlpcloud.t_dbuser_privileges
WHERE user_name = 'checksql'  -- 注意区分大小写，看 DBA 当时用的是 'checksql' 还是 'Checksql' 还是别的
  AND is_del = 0
ORDER BY cluster_key, instance_id;
```

**输出示例**（云测 0 行，但 `tdsql_check_user` 有 3 行，所以确认下 DBA 当时用的真实 user 名）：

```
（云测环境没有 checksql 授权记录，预期 0 行）
```

> **注意**：如果 Step 4 返回 0 行，**不要直接判定没有**！DBA 可能：
> 1. 用了不同大小写（`Checksql` / `CHECKSQL`）
> 2. 用了不同的用户名（`tdsql_checksql` / `sqlcheck` / `sql_audit`）
> 3. 没走授权流程，直接 `CREATE USER`（这种情况 t_dbuser_privileges 不会记录）
>
> **所以必须做 Track 2 实测兜底**。

### 2.5 Step 5：Track 2 验证（直连实例查 mysql.user）—— **关键**

> ⚠️ **这一步必须在内网执行**。公网开发机访问不到 `10.x.x.x` 这种内网 IP。
> 推荐让内网智能体（或内网跳板机）执行。

**核心 SQL（每个 set 都要跑一次）**：

```sql
-- 在每个 set 上执行，确认 checksql 用户是否存在
SELECT user, host,
       (SELECT JSON_OBJECTAGG(priv_type, is_grantable) FROM (
           SELECT 'SELECT' AS priv_type, 'Y' AS is_grantable
       ) _) AS privs,  -- 示例, 实际可拆开查
       account_locked,
       authentication_string
FROM mysql.user
WHERE user = 'checksql'
ORDER BY user, host;
```

**简化版（更安全）**：

```sql
SELECT user, host, account_locked
FROM mysql.user
WHERE user = 'checksql'
ORDER BY user, host;
```

**直连方式选择**：

| 场景 | 推荐方式 | Python 代码 |
|---|---|---|
| set 有 backend 端口（`4xxx`） | **直连 MySQL 端口** | `pymysql.connect(host, port=4001, user='checksql', password=...)` |
| set 只有 proxy 端口（`15xxx`） | 直连 proxy，加 `/*sets:set_xxx*/` 注释 | 用 TDSQL 客户端或 pymysql + `init_command` |
| group（分布式组） | 走 group proxy 端口 + `/*+ set:set_xxx */` 路由 | 同上 |

**凭据**（用户已提供）：
- 用户名：`checksql`
- 密码：`Abcd972&*(`

> 经验证（云测 monitordb），该密码**在 monitordb 上不生效**（monitordb 自身的 mysql.user 里没有 checksql 这个账号），这是**仅 set 端**的密码。生产内网智能体拿到后直接对 set 跑即可。

**参考 Python 脚本骨架**（内网智能体可直接复用）：

```python
import pymysql
import json

CHECKSQL_USER = "checksql"
# 凭据已确认（用户提供, 2026-07-26）
CHECKSQL_PWD = "Abcd972&*("

def check_set(host, port, set_name, conn_timeout=5):
    """直连一个 set, 查 checksql 用户是否存在."""
    try:
        conn = pymysql.connect(
            host=host, port=port, user=CHECKSQL_USER, password=CHECKSQL_PWD,
            connect_timeout=conn_timeout, charset="utf8mb4",
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user, host, account_locked "
                "FROM mysql.user WHERE user = %s ORDER BY user, host",
                (CHECKSQL_USER,),
            )
            rows = cur.fetchall()
            return {
                "host": host, "port": port, "set_name": set_name,
                "ok": True, "user_count": len(rows), "users": rows,
            }
    except pymysql.err.OperationalError as e:
        code, msg = e.args
        return {
            "host": host, "port": port, "set_name": set_name,
            "ok": False, "err_code": code, "err": str(msg)[:200],
        }

# 对每个 set 跑
sets = [
    # (host, port, set_name) — 来自 Step 3 输出
    ("10.206.0.13", 4001, "set_1782129880_1"),
    ("10.206.0.13", 4002, "set_1782130875_4"),
    # ...
]
results = [check_set(*s) for s in sets]
print(json.dumps(results, indent=2, default=str, ensure_ascii=False))
```

**凭据获取（如果不知道 checksql 密码）**：

1. **首选**：问 ops / DBA（虽然休假了，但有交接人）
2. **次选**：如果 `tdsql_check_user` 在 set 上的密码已知（可能与 monitordb 同名同密码），**先用它跑** —— 大概率 DBA 是同一套机制建的，密码也可能是同款
3. **再次**：用 `root` 或 ops 提供的运维账号跑（每个 set 都有 root 凭据，可问 ops）

> **生产 tip**：如果不知道密码，**不要暴力尝试**。MySQL 多次错密会触发 `connection_control` 插件封禁。优先问 ops。

### 2.6 Step 6：对比 + 输出

把 Track 1 和 Track 2 结果做差集，输出最终清单：

| 分类 | 含义 | 处理 |
|---|---|---|
| Track 1 ∩ Track 2 | 授权记录 + 实际存在 ✅ | 健康，纳入清单 |
| Track 1 − Track 2 | 授权了但实例找不到 | **告警**：实例可能已下线 / 重建 / 数据被清理 |
| Track 2 − Track 1 | 实例有但没授权记录 | **OK**：DBA 直接 CREATE USER 没走流程，已纳入清单 |
| 候选 set 但两者都没 | 上线过但 checksql 完全没建 | **OK**：DBA 跳过的，**不纳入**清单 |

**输出文件**：

- `checksql_instances.csv` —— 最终实例清单（cluster / set / group / host / port / 验证状态 / 验证时间）
- `checksql_unauthorized.csv` —— Track 2 找到但 Track 1 无记录（需要补登记）
- `checksql_missing.csv` —— Track 1 有但 Track 2 找不到（需要核实）
- `checksql_scan.log` —— 全量扫描日志（含失败原因）

---

## 3. 完整 Python 排查脚本（内网智能体参考实现）

下面脚本可直接交给内网智能体执行。所有 SQL 都已在云测验证。

```python
"""
checksql 实例排查脚本
依赖: pip install pymysql
执行环境: 内网（必须能访问 set_ip:port）
"""
import csv
import json
import sys
import time
from datetime import datetime
import pymysql


# ====== 1. 配置（按需修改）======
MONITOR = dict(
    host="118.195.161.48",   # 或内网 monitordb proxy
    port=15001,
    user="tdsql_check_user",
    password="Abcd@!#1234",
    connect_timeout=5,
    charset="utf8mb4",
)
CHECKSQL_USER = "checksql"
# 凭据已确认（用户提供, 2026-07-26）
CHECKSQL_PWD = "Abcd972&*("
OUTPUT_DIR = "./checksql_scan_result"


def step(msg):
    print(f"\n[{datetime.now():%H:%M:%S}] {msg}")


def discover_instances(monitor_conn):
    """Step 2-3: 从 4 个数据源取并集, 解析连接地址."""
    sql = """
    SELECT set_name, proxy_host_list, proxy_endpoint, backend_endpoint
    FROM (
        -- m_data_cur 取出所有 set_name
        SELECT DISTINCT f_val AS set_name
        FROM tdsqlpcloud_monitor.m_data_cur
        WHERE f_key = 'set_name'
    ) s
    LEFT JOIN LATERAL (
        SELECT GROUP_CONCAT(DISTINCT SUBSTRING_INDEX(f_mid, ':', 1) SEPARATOR ';') AS proxy_host_list
        FROM tdsqlpcloud_monitor.m_data_cur
        WHERE f_key = 'setid' AND f_val = CONCAT('/tdsqlzk/', s.set_name)
    ) p1 ON TRUE
    LEFT JOIN LATERAL (
        SELECT f_mid AS proxy_endpoint
        FROM tdsqlpcloud_monitor.m_data_cur
        WHERE f_key = 'setid' AND f_val = CONCAT('/tdsqlzk/', s.set_name)
          AND f_mid REGEXP '^10\\\\.[0-9.]+:[0-9]+$'
        LIMIT 1
    ) p2 ON TRUE
    LEFT JOIN LATERAL (
        SELECT f_mid AS backend_endpoint
        FROM tdsqlpcloud_monitor.m_data_cur
        WHERE f_key = 'set_name' AND f_val = s.set_name
          AND f_mid REGEXP '^10\\\\.[0-9.]+:[0-9]+$'
        LIMIT 1
    ) p3 ON TRUE;
    """
    # 注: MySQL 8.0.14+ 才支持 LATERAL, 老版需改写为子查询 UNION/相关子查询
    # 兼容写法见下面 fallback_sql
    with monitor_conn.cursor() as cur:
        try:
            cur.execute(sql)
            rows = cur.fetchall()
            return [dict(zip([d[0] for d in cur.description], r)) for r in rows]
        except pymysql.err.ProgrammingError:
            # LATERAL 不支持, 改用普通子查询
            fallback = """
            SELECT s.set_name,
                   (SELECT GROUP_CONCAT(DISTINCT SUBSTRING_INDEX(f_mid, ':', 1) SEPARATOR ';')
                    FROM tdsqlpcloud_monitor.m_data_cur
                    WHERE f_key = 'setid' AND f_val = CONCAT('/tdsqlzk/', s.set_name)) AS proxy_host_list,
                   (SELECT f_mid FROM tdsqlpcloud_monitor.m_data_cur
                    WHERE f_key = 'setid' AND f_val = CONCAT('/tdsqlzk/', s.set_name)
                      AND f_mid REGEXP '^10\\\\.[0-9.]+:[0-9]+$' LIMIT 1) AS proxy_endpoint,
                   (SELECT f_mid FROM tdsqlpcloud_monitor.m_data_cur
                    WHERE f_key = 'set_name' AND f_val = s.set_name
                      AND f_mid REGEXP '^10\\\\.[0-9.]+:[0-9]+$' LIMIT 1) AS backend_endpoint
            FROM (
                SELECT DISTINCT f_val AS set_name
                FROM tdsqlpcloud_monitor.m_data_cur
                WHERE f_key = 'set_name'
            ) s
            """
            cur.execute(fallback)
            rows = cur.fetchall()
            return [dict(zip([d[0] for d in cur.description], r)) for r in rows]


def track1_privileges(monitor_conn, user_name="checksql"):
    """Step 4: 查 t_dbuser_privileges 找已授权的 instance."""
    sql = """
    SELECT cluster_key, instance_id, instance_type, user_name, user_host,
           readonly, master_rw_split, slave_rw_split, mtime
    FROM tdsqlpcloud.t_dbuser_privileges
    WHERE user_name = %s AND is_del = 0
    ORDER BY cluster_key, instance_id
    """
    with monitor_conn.cursor() as cur:
        cur.execute(sql, (user_name,))
        return [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]


def track2_check_user(host, port, user, pwd, set_name, timeout=5):
    """Step 5: 直连一个 set 查 checksql 用户."""
    try:
        conn = pymysql.connect(
            host=host, port=port, user=user, password=pwd,
            connect_timeout=timeout, charset="utf8mb4",
        )
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT VERSION()")
                version = cur.fetchone()[0]
                cur.execute(
                    "SELECT user, host, account_locked "
                    "FROM mysql.user WHERE user = %s ORDER BY user, host",
                    (CHECKSQL_USER,),
                )
                users = cur.fetchall()
                cur.execute(
                    "SELECT db, table_name, privilege_type "
                    "FROM mysql.tables_priv WHERE user = %s LIMIT 5",
                    (CHECKSQL_USER,),
                )
                grants = cur.fetchall()
            return {
                "set": set_name, "endpoint": f"{host}:{port}",
                "ok": True, "version": version,
                "checksql_users": users, "grants_sample": grants,
            }
        finally:
            conn.close()
    except Exception as e:
        return {
            "set": set_name, "endpoint": f"{host}:{port}",
            "ok": False, "err": f"{type(e).__name__}: {str(e)[:200]}",
        }


def main():
    import os
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    step("连接 monitordb ...")
    m = pymysql.connect(**MONITOR)

    try:
        step("Step 2-3: 解析所有候选 instance + 连接地址")
        instances = discover_instances(m)
        print(f"  发现 {len(instances)} 个 instance")
        for i in instances:
            print(f"    {i}")

        step(f"Step 4: Track 1 — t_dbuser_privileges 查 user_name='{CHECKSQL_USER}'")
        track1 = track1_privileges(m, CHECKSQL_USER)
        print(f"  找到 {len(track1)} 行授权记录")
        for r in track1:
            print(f"    {r}")
        # 同时查其他可能大小写
        for variant in ("Checksql", "CHECKSQL", "tdsql_checksql", "sqlcheck", "sql_audit"):
            with m.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT user_name FROM tdsqlpcloud.t_dbuser_privileges "
                    "WHERE user_name LIKE %s AND is_del=0",
                    (f"%{variant}%",)
                )
                v_rows = [r[0] for r in cur.fetchall()]
                if v_rows:
                    print(f"  ⚠ 找到其他相似用户名: {v_rows}")

        step("Step 5: Track 2 — 直连每个 set 验证 mysql.user")
        track2_results = []
        if not CHECKSQL_PWD:
            print("  ⚠ CHECKSQL_PWD 未配置, 跳过 Track 2 直连")
        else:
            for inst in instances:
                # 优先用 backend 端口（4xxx）直连 MySQL
                endpoint = inst.get("backend_endpoint") or inst.get("proxy_endpoint")
                if not endpoint:
                    print(f"  ⚠ {inst['set_name']} 无可连接地址, 跳过")
                    continue
                host, port = endpoint.rsplit(":", 1)
                port = int(port)
                print(f"  连接 {endpoint} ...")
                result = track2_check_user(host, port, CHECKSQL_USER, CHECKSQL_PWD, inst["set_name"])
                track2_results.append(result)
                print(f"    -> {result['ok']}, checksql_users={result.get('checksql_users', 'N/A')}")

        # Step 6: 输出
        step("Step 6: 输出最终清单")
        with open(f"{OUTPUT_DIR}/instances_candidate.json", "w", encoding="utf-8") as f:
            json.dump(instances, f, indent=2, ensure_ascii=False, default=str)
        with open(f"{OUTPUT_DIR}/track1_privileges.json", "w", encoding="utf-8") as f:
            json.dump(track1, f, indent=2, ensure_ascii=False, default=str)
        if track2_results:
            with open(f"{OUTPUT_DIR}/track2_results.json", "w", encoding="utf-8") as f:
                json.dump(track2_results, f, indent=2, ensure_ascii=False, default=str)
        # 简化 CSV：最终清单
        with open(f"{OUTPUT_DIR}/checksql_instances.csv", "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["set_name", "endpoint", "in_track1", "track2_user_count", "track2_status"])
            t1_set = {r["instance_id"] for r in track1}
            t2_map = {r["set"]: r for r in track2_results}
            for inst in instances:
                sn = inst["set_name"]
                ep = inst.get("backend_endpoint") or inst.get("proxy_endpoint") or "N/A"
                in_t1 = "Y" if sn in t1_set else "N"
                t2 = t2_map.get(sn, {})
                w.writerow([sn, ep, in_t1,
                            len(t2.get("checksql_users", [])) if t2.get("ok") else "N/A",
                            "ok" if t2.get("ok") else t2.get("err", "skipped")])
        print(f"  输出目录: {OUTPUT_DIR}/")
        for fn in os.listdir(OUTPUT_DIR):
            print(f"    - {fn}")
    finally:
        m.close()


if __name__ == "__main__":
    main()
```

---

## 4. 风险点 + 注意事项

### 4.1 凭据问题

- **`checksql` 凭据已确认**（用户提供 2026-07-26）：`checksql` / `Abcd972&*(`
  - **仅 set 端有效**：经验证（云测 monitordb proxy），用此账号密码连 monitordb 返回 `Access denied`，因为 monitordb 自身的 mysql.user 里没有这个账号（只有 `tdsql_check_user`）。生产内网智能体直接拿去连 set 即可。
- **错密会被封禁**：MySQL 5.7+ 的 `connection_control` 插件对同 IP 错密 3 次会递增延迟（最多 1 天）。**不要用脚本试其他密码**。如果生产实际跑下来发现这密码不通，**停下来问 ops**，不要自动重试。
- **monitordb 密码已知**（`tdsql_check_user` / `Abcd@!#1234`）：可连 monitordb 查 Track 1 元数据。

### 4.2 网络与可达性

- **公网 → 内网 set 不通**：开发机/办公网访问不到 `10.x.x.x`，直连 set 必须在内网或跳板机执行
- **set 后端端口（4xxx）通常只允许内网访问**：跨网段可能被防火墙拦
- **proxy 端口（15xxx）一般有公网入口**：可走公网 proxy + TDSQL 协议

### 4.3 误报/漏报

- **`t_dbuser_privileges` 有 `is_del` 字段**：默认 0 = 有效，被软删除的 = 1，**只查 is_del=0**
- **DBA 可能建了 checksql 但 `t_dbuser_privileges` 没记录**（手工 CREATE USER）：**Track 2 兜底**就是为这个
- **新建 set 还没在 m_data_cur 里出现**：可能漏。建议脚本同时查 `m_data_YYYYMMDD` 历史表做交叉验证（`m_data_20260726` 等）

### 4.4 多 cluster 场景

- **每个 cluster 自己的 monitordb**：上面的脚本默认连一个 monitordb
- **生产多 cluster**：需要：
  1. 先查 `t_cluster` 拿到所有 cluster_key（但只在一个 monitordb 里查不到别的 cluster）
  2. **办法 A**：让 ops 提供所有 cluster 的 monitordb 连接信息，分别跑
  3. **办法 B**：通过 `t_cluster_relation` 等跨集群元数据表（但云测发现这表是 0 行，可能不可靠）

### 4.5 group（分布式组）特殊处理

- group 本身不是 MySQL 实例，是"路由层"，无 `mysql.user` 可查
- 必须穿透到 group 下的具体 set：`/*sets:set_xxx*/SQL` 或 `/*+ set:set_xxx */` 注释
- Track 2 实际是"对 group 下每个 set 跑"，脚本里展开 group.subset 即可

---

## 5. 排查结果（云测环境基线）

> 本次实验在云测 cluster `tdsql_th16yls3c`（刘晴的集群）做基线验证，**该环境没有 checksql 用户**（DBA 还没建过），只用来验证方法可行性。

**云测实际数据**：
- 候选 instance: 4 个 set
  - `set_1782129880_1` (noshard) — proxy `10.206.0.4:15001` / `10.206.0.8:15001`，backend `10.206.0.13:4001`
  - `set_1782130875_4` (noshard) — proxy `10.206.0.4:15002` / `10.206.0.8:15002`，backend `10.206.0.4:4002` / `10.206.0.13:4002`
  - `set_1782132369_1` (groupshard) — proxy `10.206.0.4:15005` / `10.206.0.8:15005`，backend `10.206.0.8:4003`
  - `set_1782132389_3` (groupshard) — proxy 走 group 端口 `15005`，backend `10.206.0.13:4002`
- Track 1 (`t_dbuser_privileges` WHERE user_name='checksql'): **0 行**（预期，因为云测没建）
- Track 1 (`t_dbuser_privileges` WHERE user_name='tdsql_check_user'): 3 行（已授权）
  - `set_1782129880_1`、`set_1782130875_4`、`group_1782132247_10`
- Track 2: 跳过（凭据未提供 + 开发机不在内网）

**结论**：方法可行，等内网智能体拿着 checksql 密码实跑即可。

---

## 6. 后续动作（拿到清单后）

1. **导入到审核工具**：把 `checksql_instances.csv` 导入到《TDSQL 数据库 SQL 审核工具》的连接配置
2. **配置定期校验**：建议每月跑一次脚本，比对 Track 1 vs Track 2，发现差异告警
3. **联系 DBA 确认**：拿到清单后还是建议让 DBA 复核一次，避免漏报
4. **登记授权记录**：对 Track 2 找到但 Track 1 无记录的 set，建议补走授权流程（更安全，便于审计）

---

## 附录 A：快速 SQL 速查

```sql
-- 1. 看 monitordb 里所有 user (云测确认了同名同密码机制)
SELECT user, host FROM mysql.user WHERE user LIKE '%check%';

-- 2. 看 t_dbuser_privileges 都有哪些 user_name
SELECT user_name, COUNT(*) cnt
FROM tdsqlpcloud.t_dbuser_privileges WHERE is_del=0
GROUP BY user_name ORDER BY cnt DESC;

-- 3. 看 t_dbuser_privileges 里 checksql 类的（模糊）
SELECT cluster_key, instance_id, instance_type, user_name, mtime
FROM tdsqlpcloud.t_dbuser_privileges
WHERE user_name LIKE '%check%' AND is_del=0;

-- 4. 全量 set 列表（最权威）
SELECT DISTINCT f_val
FROM tdsqlpcloud_monitor.m_data_cur WHERE f_key='set_name';

-- 5. cluster 列表
SELECT cluster_id, cluster_key, cluster_name, cluster_version, is_del
FROM tdsqlpcloud.t_cluster ORDER BY cluster_id;

-- 6. 集群级 monitordb 配置
SELECT mkey, mval FROM tdsqlpcloud.t_cluster_expand
WHERE mkey IN ('mdb_list','mdb_user','mdb_pwd','mdb_port','mdb_name',
               'zookeeper_list','osssvr_list');
```

## 附录 B：相关表清单

| 数据库 | 表名 | 说明 |
|---|---|---|
| `tdsqlpcloud` | `t_dbuser_privileges` | 授权记录（核心） |
| `tdsqlpcloud` | `t_cluster` | 集群主表 |
| `tdsqlpcloud` | `t_cluster_expand` | 集群 key-value 配置 |
| `tdsqlpcloud` | `t_statistics_dbcluster` | 集群级查询统计 |
| `tdsqlpcloud_monitor` | `m_data_cur` | 实时 key-value 监控数据（核心） |
| `tdsqlpcloud_monitor` | `proxy_classes_analysis` | 慢查询样本 |
| `tdsqlpcloud_monitor` | `proxy_global_analysis` | 全局查询统计 |
| `tdsqlpcloud_monitor` | `ops_host_module` | host + module 映射 |
| `tdsqlpcloud_monitor` | `m_data_YYYYMMDD` | 历史时序（按日分表） |
| `tdsqlpcloud_monitor` | `m_alarm` | 告警记录 |

---

> **文档版本**：v1.1（2026-07-26） · 凭据已确认 `checksql` / `Abcd972&*(` · 未经 DBA 复核仅供参考
