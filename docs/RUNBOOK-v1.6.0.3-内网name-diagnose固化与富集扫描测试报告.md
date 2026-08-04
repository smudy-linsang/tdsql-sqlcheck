# RUNBOOK-v1.6.0.3 内网 name-diagnose 固化与富集扫描 测试报告

| 项目 | 内容 |
|------|------|
| **测试版本** | V1.6.0.4 |
| **测试日期** | 2026-08-04 |
| **测试服务器** | 10.243.16.238 |
| **测试依据** | RUNBOOK-v1.6.0.3-内网name-diagnose固化与富集扫描操作手册 |
| **测试人员** | Lingma AI Agent |
| **测试状态** | 已完成 |

---

## 一、 测试环境配置

### 1.1 ZooKeeper 连接信息

| 配置项 | 值 |
|--------|-----|
| ZK服务地址 | 10.243.21.11:2118, 10.243.21.12:2118, 10.243.21.13:2118 |
| ZK根路径 | /tdsqlzk |
| ZK认证用户名 | tdsqlsys_zk |
| ZK认证口令 | ${ZK_AUTH_PASSWORD} |
| 网段替换规则 | segment:3, from:21, to:20 |

### 1.2 MonitorDB 配置

| 配置项 | 值 |
|--------|-----|
| MonitorDB主机 | 10.243.20.13 |
| MonitorDB端口 | 15001 |
| MonitorDB用户 | tdsqlpcloud |
| MonitorDB口令 | ${MONITOR_PASSWORD} |
| MonitorDB库名 | tdsqlpcloud_monitor |

### 1.3 业务只读账号

| 配置项 | 值 |
|--------|-----|
| 业务用户名 | checksql |
| 业务口令 | ${BUSINESS_PASSWORD} |

### 1.4 部署信息

| 配置项 | 值 |
|--------|-----|
| CheckSQL服务地址 | http://10.243.16.238:8000 |
| 当前版本 | V1.6.0.4 |
| 部署路径 | /opt/tdsql-sqlcheck |

---

## 二、 测试步骤与结果

### 步骤1：保存ZK基础配置（enrich_enabled=0）

**目的**：先关闭富集功能，确保基础扫描能正常运行，获取原始实例列表。

**操作**：
```bash
curl -s -X PUT $HOST/api/v1/tdsql/discover/config \
  -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
  -d '{
    "servers": "10.243.21.11:2118,10.243.21.12:2118,10.243.21.13:2118",
    "root_path": "/tdsqlzk",
    "driver": "kazoo",
    "proxy_mode": "first",
    "auth_username": "tdsqlsys_zk",
    "auth_password": "${ZK_AUTH_PASSWORD}",
    "octet_rules": [{"segment":3,"from":"21","to":"20"}],
    "monitor_host": "10.243.20.13",
    "monitor_port": 15001,
    "monitor_user": "tdsqlpcloud",
    "monitor_password": "${MONITOR_PASSWORD}",
    "monitor_db": "tdsqlpcloud_monitor",
    "business_username": "checksql",
    "business_password": "${BUSINESS_PASSWORD}",
    "name_query_hint": "",
    "enrich_enabled": 0
  }'
```

**执行结果**：

| 检查项 | 期望值 | 实际值 | 状态 |
|--------|--------|--------|------|
| 配置保存 | 200 OK | 200 OK | ✅ PASS |
| enrich_enabled | 0 | 0 | ✅ PASS |
| servers | 3节点 | 3节点 | ✅ PASS |
| octet_rules | segment:3, from:21, to:20 | 已配置 | ✅ PASS |

**结论**：ZK基础配置保存成功，富集功能已关闭。

---

### 步骤2：跑一次基础扫描获取实例ID

**目的**：在富集关闭状态下执行扫描，获取2-3个实例ID用于后续name-diagnose测试。

**操作**：
```bash
curl -s -X POST $HOST/api/v1/tdsql/discover \
  -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' -d '{}'
```

**执行结果**：

| 检查项 | 期望值 | 实际值 | 状态 |
|--------|--------|--------|------|
| HTTP状态码 | 200 | 200 | ✅ PASS |
| 返回数据量 | >0 | 110,631 字节 | ✅ PASS |
| discovery_id | 非空 | 2ef08d31d15f48aa832ec9f345b4181f | ✅ PASS |
| 总实例数 | >0 | 208 | ✅ PASS |
| resolved_name | 全部为空（富集关闭） | 全部为空 | ✅ PASS |

**提取的测试实例ID**：

| 序号 | instance_id | service_name | 原始主机 | 解析主机 |
|------|-------------|--------------|----------|----------|
| 1 | set_1768374735_1 | set_1768374735_1 | 10.243.21.13 | 10.243.20.13 |
| 2 | set_1770014695_33 | set_1770014695_33 | 10.243.21.13 | 10.243.20.13 |
| 3 | set_1770017431_41 | set_1770017431_41 | 10.243.21.15 | 10.243.20.15 |

**结论**：基础扫描成功，网段替换规则生效（10.243.21.x -> 10.243.20.x），获取208个实例。

---

### 步骤3：跑 name-diagnose 测试

**目的**：诊断实例名称在哪一级解析命中，确定固化级别。

**操作**：
```bash
curl -s -X POST $HOST/api/v1/tdsql/discover/name-diagnose \
  -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
  -d '{
    "instance_ids": ["set_1768374735_1", "set_1770014695_33", "set_1770017431_41"],
    "discovery_id": "2ef08d31d15f48aa832ec9f345b4181f",
    "monitor": {"host":"10.243.20.13","port":15001,
                "username":"tdsqlpcloud","password":"${MONITOR_PASSWORD}",
                "database":"tdsqlpcloud_monitor"}
  }'
```

**执行结果**：

| 检查项 | 期望值 | 实际值 | 状态 |
|--------|--------|--------|------|
| HTTP状态码 | 200 | 200 | ✅ PASS |
| 返回数据量 | >0 | 5,251 字节 | ✅ PASS |
| 诊断实例数 | 3 | 3 | ✅ PASS |

**name-diagnose 详细结果**：

#### 实例1：set_1768374735_1

| 命中级别 | 名称 | 来源 | f_mid | f_key |
|----------|------|------|-------|-------|
| **L1** | **monitordb-c** | **monitor_exact** | /tdsqlzk/set_1768374735_1 | instance_name |
| L2 | monitordb | monitor_like | /tdsqlzk/set_1768374735_1 | clientName |
| L3 | noshard | monitor_value | /tdsqlzk/set_1768374735_1 | cluster_type |

#### 实例2：set_1770014695_33

| 命中级别 | 名称 | 来源 | f_mid | f_key |
|----------|------|------|-------|-------|
| **L1** | **中间业务（老核心）-集中式-开发环境** | **monitor_exact** | /tdsqlzk/set_1770014695_33 | instance_name |
| L2 | 中间业务（老核心）-集中式-开发环境 | monitor_like | /tdsqlzk/set_1770014695_33 | clientName |
| L3 | noshard | monitor_value | /tdsqlzk/set_1770014695_33 | cluster_type |

#### 实例3：set_1770017431_41

| 命中级别 | 名称 | 来源 | f_mid | f_key |
|----------|------|------|-------|-------|
| **L1** | **中间业务（老核心）-集中式-UAT环境** | **monitor_exact** | /tdsqlzk/set_1770017431_41 | instance_name |
| L2 | 中间业务（老核心）-集中式-UAT环境 | monitor_like | /tdsqlzk/set_1770017431_41 | clientName |
| L3 | noshard | monitor_value | /tdsqlzk/set_1770017431_41 | cluster_type |

**matched_mids 示例**（set_1768374735_1）：

| 匹配mid | 说明 |
|---------|------|
| /tdsqlzk/set_1768374735_1 | ZK路径格式 |
| 10.243.21.13:15001 | 原始内网地址 |
| 10.243.21.14:15001 | 备用节点 |

**available_keys 示例**（共60+字段）：

| 分类 | 字段示例 |
|------|----------|
| 状态类 | alarm_ignore, kpstatus, degrade_flag |
| 资源类 | master_cpu_usage, master_mem_available, master_data_dir_usage |
| 网络类 | master_ip_port, available_proxy_host |
| 名称类 | instance_name, clientName, groupname, hostname |

**诊断结论**：

| 实例ID | 最高命中级别 | 推荐固化级别 |
|--------|-------------|-------------|
| set_1768374735_1 | **L1** | L1 |
| set_1770014695_33 | **L1** | L1 |
| set_1770017431_41 | **L1** | L1 |

**3个实例全部在L1级别精确命中**，通过 `/tdsqlzk/<instance_id>` 路径下的 `instance_name` 字段获取实例名称。

---

### 步骤4：固化 hint + 打开富集

**目的**：将第3步得到的L1级别写入配置，同时打开富集功能。

**操作**：
```bash
curl -s -X PUT $HOST/api/v1/tdsql/discover/config \
  -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
  -d '{
    "servers": "10.243.21.11:2118,10.243.21.12:2118,10.243.21.13:2118",
    "root_path": "/tdsqlzk",
    "driver": "kazoo",
    "proxy_mode": "first",
    "auth_username": "tdsqlsys_zk",
    "auth_password": "${ZK_AUTH_PASSWORD}",
    "octet_rules": [{"segment":3,"from":"21","to":"20"}],
    "monitor_host": "10.243.20.13",
    "monitor_port": 15001,
    "monitor_user": "tdsqlpcloud",
    "monitor_password": "${MONITOR_PASSWORD}",
    "monitor_db": "tdsqlpcloud_monitor",
    "business_username": "checksql",
    "business_password": "${BUSINESS_PASSWORD}",
    "name_query_hint": "L1",
    "enrich_enabled": 1
  }'
```

**执行结果**：

| 检查项 | 期望值 | 实际值 | 状态 |
|--------|--------|--------|------|
| 配置保存 | 200 OK | 200 OK | ✅ PASS |
| name_query_hint | L1 | L1 | ✅ PASS |
| enrich_enabled | 1 | 1 | ✅ PASS |
| MonitorDB配置 | 已配置 | 已配置 | ✅ PASS |
| 业务账号配置 | 已配置 | 已配置 | ✅ PASS |

**结论**：配置固化成功，富集功能已打开。

---

### 步骤5：再跑扫描验收富集效果

**目的**：验证富集扫描后，实例列表是否带出实例名称和业务库。

**操作**：
```bash
curl -s -X POST $HOST/api/v1/tdsql/discover \
  -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' -d '{}'
```

**执行结果**：

#### 5.1 总体统计

| 检查项 | 验收标准 | 实际结果 | 状态 |
|--------|---------|---------|------|
| 总实例数 | >0 | **208** | ✅ PASS |
| resolved_name非空 | 全部非空 | **208/208 (100%)** | ✅ PASS |
| name_source | monitor_exact | **208/208 monitor_exact** | ✅ PASS |
| databases_source | proxy_show（部分） | **47/208 (22.6%)** | ⚠️ 部分成功 |
| enrich_status ok | 名称非空 | **47/208 (22.6%)** | ⚠️ 部分成功 |

#### 5.2 富集状态分布

| enrich_status | 数量 | 百分比 | 说明 |
|---------------|------|--------|------|
| **ok** | **47** | **22.6%** | 名称+业务库全部富集成功 |
| **dbs_failed:BUSINESS_PROXY_FAILED:OperationalError** | **161** | **77.4%** | 名称成功，但业务库枚举失败 |

#### 5.3 富集成功实例示例（47个）

| 实例ID | resolved_name | name_source | business_dbs | databases_source |
|--------|---------------|-------------|--------------|------------------|
| set_1768374735_1 | monitordb-c | monitor_exact | query_rewrite, sysdb, tdsqlpcloud | proxy_show |
| set_1770014695_33 | 中间业务（老核心）-集中式-开发环境 | monitor_exact | dafedb, mertdb, operabf, query_rewrite, sysdb, zjywgl | proxy_show |
| set_1770017431_41 | 中间业务（老核心）-集中式-UAT环境 | monitor_exact | mertdb, query_rewrite, sysdb, zjywgl | proxy_show |

#### 5.4 富集部分成功实例示例（161个）

| 实例ID | resolved_name | name_source | business_dbs | enrich_status |
|--------|---------------|-------------|--------------|---------------|
| 部分实例 | 有值 | monitor_exact | 空 | dbs_failed:BUSINESS_PROXY_FAILED |

---

## 三、 测试结论

### 3.1 功能验证结论

| 功能模块 | 测试项 | 结果 | 状态 |
|----------|--------|------|------|
| **ZK基础配置** | 配置保存、网段替换规则 | 正常 | ✅ PASS |
| **ZK基础扫描** | 实例发现、地址解析 | 正常 | ✅ PASS |
| **name-diagnose** | 三级名称诊断（L1/L2/L3） | 3/3实例L1精确命中 | ✅ PASS |
| **hint固化** | name_query_hint=L1 | 成功固化 | ✅ PASS |
| **富集功能-名称** | resolved_name非空率 | 208/208 (100%) | ✅ PASS |
| **富集功能-名称来源** | name_source=monitor_exact | 208/208 (100%) | ✅ PASS |
| **富集功能-业务库** | business_dbs非空率 | 47/208 (22.6%) | ⚠️ 部分成功 |

### 3.2 核心功能验证通过

1. **name-diagnose功能**：能准确诊断MonitorDB中实例名称的命中级别，3个测试实例均在L1级别精确命中。

2. **名称富集功能**：208个实例100%成功富集实例名称，通过 `instance_name` 字段获取。

3. **网段替换规则**：`segment:3, from:21, to:20` 规则生效，ZK内网地址正确映射为CheckSQL可达地址。

### 3.3 已知问题与说明

| 问题 | 现象 | 影响范围 | 建议 |
|------|------|----------|------|
| 业务库枚举失败 | 161/208实例 enrich_status=dbs_failed | 77.4%实例 | BUSINESS_PROXY_FAILED:OperationalError，可能是部分实例Proxy不可达或业务账号权限不足，需检查网络连通性和checksql用户SHOW DATABASES权限 |

### 3.4 技术细节

**name-diagnose 五级诊断链**：

| 级别 | 说明 | 命中情况 |
|------|------|----------|
| **L1** | MonitorDB精确匹配（/tdsqlzk/<id> + instance_name） | **3/3实例命中** |
| L2 | MonitorDB模糊匹配（clientName LIKE） | 3/3实例命中（兜底） |
| L3 | MonitorDB值匹配（cluster_type等） | 3/3实例命中（兜底） |
| L4 | 元数据表名称列 | 未命中 |
| L5 | ZK节点名称 | 未命中 |

---

## 四、 附录

### 4.1 API调用记录

| 序号 | API路径 | 方法 | 状态码 | 说明 |
|------|---------|------|--------|------|
| 1 | /api/v1/auth/login | POST | 200 | 获取认证令牌 |
| 2 | /api/v1/tdsql/discover/config | GET | 200 | 查看现有ZK配置 |
| 3 | /api/v1/tdsql/discover/config | PUT | 200 | 保存ZK基础配置（enrich=0） |
| 4 | /api/v1/tdsql/discover | POST | 200 | 基础扫描（enrich=0） |
| 5 | /api/v1/tdsql/discover/name-diagnose | POST | 200 | name-diagnose诊断 |
| 6 | /api/v1/tdsql/discover/config | PUT | 200 | 固化hint+打开富集（enrich=1） |
| 7 | /api/v1/tdsql/discover | POST | 200 | 富集扫描验收 |

### 4.2 测试数据文件

| 文件名 | 大小 | 说明 |
|--------|------|------|
| zk_scan_result.json | 110,631 字节 | 基础扫描结果（enrich=0） |
| diagnose_full.json | 5,251 字节 | name-diagnose诊断结果 |
| scan_enriched.json | 132,271 字节 | 富集扫描结果（enrich=1） |
| final_scan.json | 132,271 字节 | 最终验收扫描结果 |

### 4.3 环境版本信息

| 项目 | 版本 |
|------|------|
| TDSQL SQL审核工具 | V1.6.0.4 |
| 操作系统 | Kylin Linux Advanced Server V10 (Halberd) |
| Python版本 | 3.11.11 |
| 数据库 | TDSQL (MySQL协议) |
| ZK驱动 | kazoo >= 2.10 |

---

**报告生成时间**：2026-08-04  
**测试执行人**：Lingma AI Agent  
**报告版本**：v1.0
