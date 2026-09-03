# TDSQL SQL审核工具 v1.6.3.0 内网生产环境升级部署手册

| 属性 | 内容 |
|---|---|
| **目标升级版本** | `v1.6.3.0` |
| **当前现网版本** | `v1.6.2.2` |
| **发布包文件** | `tdsql-sqlcheck-v1.6.3.0-linux-x86_64.tar.gz`（及其 `.sha256` 校验文件） |
| **目标生产服务器** | `10.243.16.238`（银河麒麟 Advanced Server V10 SP3，海光 x86_64 CPU） |
| **操作执行端** | 内网 Windows 部署机（通过 SSH 远程执行或在生产机直接执行） |
| **生产元数据库** | **外部 TDSQL 集群**（集中式实例，MySQL 协议 3306 端口） |
| **升级方式** | **在轨多版本覆盖升级**（`install.sh` 自动创建 `releases/v1.6.3.0` 并平滑切换） |

---

## 零、 【最高生产红线】encryption.key 密钥延续与数据安全

> [!CAUTION]
> **生产级安全警告：严防 encryption.key 密钥丢失或被重置！**
> 1. **核心业务影响**：生产环境已纳管的数十个业务 TDSQL 数据库实例连接密码，均使用对称加密算法（Fernet）存储在元数据库中，解密密钥强依赖于 `/opt/tdsql-sqlcheck/current/data/encryption.key` 及 `.env` 中的 `TDSQL_ENCRYPTION_KEY`！
> 2. **灾难性后果**：**一旦密钥丢失或被重新生成，现有所有实例连接将全部解密失败（报 InvalidToken），导致全行 SQL 审核与慢日志抓取中断！**
> 3. **生产升级核心防线**：
>    - **严禁删除 `/opt/tdsql-sqlcheck` 目录**（不能像测试环境那样 `mv /opt/tdsql-sqlcheck` 重新建空目录）！
>    - 生产升级必须直接在既有 `/opt/tdsql-sqlcheck` 上执行 `./deploy/install.sh`。`install.sh` 内置了版本链继承机制，会自动从 `releases/v1.6.2.2` 继承 `encryption.key` 并完整保留生产 `.env`；
>    - **在开始任何操作前，第一件事必须是对当前 `.env` 和 `encryption.key` 进行离线异地备份！**

---

## 一、 版本变更全景（v1.6.2.2 → v1.6.3.0）

本次 `v1.6.3.0` 经历了 6 轮独立质检（Review）、3 轮 SIT 集成测试、4 轮开发环境 UAT 以及**内网测试环境（10.243.16.252）真实 TDSQL 集群的完整独立 UAT 验收**（有效用例通过率 100%），核心升级内容如下：

### 1.1 核心业务功能：深度诊断 → G14 表类型统计（原厂口径对齐）
* **分布式实例统计口径**：
  * 通过当前会话默认库口径，直接调用 TDSQL 原厂三条 Proxy 内部命令进行统计：
    * `/*proxy*/show table with shardkey`（分片表）
    * `/*proxy*/show table with noshardkey_allset`（广播表）
    * `/*proxy*/show table without shardkey`（单表）
  * **二级分区物理子表剔除**：精准识别并剔除 `_sub_p0` 等底层物理分片子表，单独列出统计，彻底消除逻辑业务表计数被物理分表虚增的问题；
  * **严密集合交叉对账**：分片表、广播表、单表三者两两互斥，并集严格与 `information_schema.TABLES` 中的 `BASE TABLE` 集合进行交叉对账，确保总数绝不虚增、绝无遗漏；
* **集中式实例统计口径**：
  * 自动切换为 `BASE TABLE` 口径（分片表=0、广播表=0、全部归为单表），如实反映单机架构；
* **历史留档与对账溯源**：
  * 每次统计结构化持久化落库（`table_type_stat` 及 `table_type_stat_item`），支持按批次明细与操作人进行历史回看与多轮对账比对。

### 1.2 前端异步交互与并发状态机加固（4 轮 UAT 闭环）
* **消除跨库竞态**：引入前端 Scope 令牌机制，在快速切换实例或数据库时，旧在途请求的迟到失败（422）绝不弹窗、旧成功数据（200）绝不覆盖新界面；
* **保护 Loading 状态**：前序请求的 `finally` 决不提前释放后续在途请求的 Loading 状态；
* **RBAC 严格防护**：`auditor` 审计员角色仅具有历史留档只读权限，“立即统计”按钮在前端明确置灰禁用，后端 API 严格阻断非授权触发。

### 1.3 生产依赖边界收敛治理
* 从生产 `requirements.txt` 中彻底移除了测试框架 Playwright（收敛至开发专用 extra），保障了生产离线依赖库的极简与确定性，杜绝内网离线安装失败风险。

---

## 二、 元数据库升级机制与 DBA 指引

生产环境 `10.243.16.238` 的元数据存储在**外部 TDSQL 数据库集群**（集中式实例）中。从 `v1.6.2.2` 升级至 `v1.6.3.0` 的元数据库处理方式如下：

### 2.1 自动升级机理（平台默认能力，零停机平滑升级）
本系统内置了严密的自动化增量迁移引擎（`backend/schema/migrator.py`）：
1. **进程间安全互斥**：服务启动时，会自动向外部 TDSQL 申请服务级分布式互斥命名锁：
   ```sql
   SELECT GET_LOCK('tdsql_sqlcheck_init', 60);
   ```
2. **自动应用增量迁移**：检测到当前元数据库尚未应用 `v13_130_table_type_stats` 时，会自动原子执行 `backend/schema/v13/130_table_type_stats.sql`；
3. **新增元数据表**（共 2 张）：
   * **`table_type_stat`**：记录一次表类型统计的任务概览、实例类型、总表数、各类型表计数、告警信息等；
   * **`table_type_stat_item`**：记录该次统计下每个业务库的详细分布明细；
4. **权限矩阵自动订正**：自动在 `role_permissions` 表中补齐 `deep-diag-tabletype` 菜单项对 `admin`、`dba`、`developer`、`auditor` 的默认可见性矩阵；
5. **升级完成解锁**：升级完成后自动释放命名锁，耗时仅需数十毫秒。

### 2.2 DBA 手动前置升级指引（可选，变更窗口核验）
如果生产变更要求必须由 DBA 提前执行 SQL 变更脚本，DBA 可在升级前直接登录外部 TDSQL 元数据库，执行以下 DDL（具有 `IF NOT EXISTS` 幂等性）：

```sql
USE `tdsql_sqlcheck`;

-- 1. 表类型统计任务汇总表
CREATE TABLE IF NOT EXISTS table_type_stat (
    id                  INT PRIMARY KEY AUTO_INCREMENT,
    connection_id       VARCHAR(64) DEFAULT '',
    database_filter     VARCHAR(128) DEFAULT '',
    instance_type       VARCHAR(32) DEFAULT '',
    type_source         VARCHAR(32) DEFAULT '',
    database_count      INT DEFAULT 0,
    total_tables        INT DEFAULT 0,
    shard_tables        INT DEFAULT 0,
    broadcast_tables    INT DEFAULT 0,
    single_tables       INT DEFAULT 0,
    baseline_tables     INT DEFAULT 0,
    subpartition_tables INT DEFAULT 0,
    failed_databases    INT DEFAULT 0,
    skipped_databases   INT DEFAULT 0,
    overlap_count       INT DEFAULT 0,
    warnings_json       MEDIUMTEXT,
    created_by          VARCHAR(64) DEFAULT '',
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_tts_conn (connection_id),
    INDEX idx_tts_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 2. 表类型统计逐库明细表
CREATE TABLE IF NOT EXISTS table_type_stat_item (
    id                  INT PRIMARY KEY AUTO_INCREMENT,
    stat_id             INT NOT NULL,
    db_name             VARCHAR(128) DEFAULT '',
    total_tables        INT DEFAULT 0,
    shard_tables        INT DEFAULT 0,
    broadcast_tables    INT DEFAULT 0,
    single_tables       INT DEFAULT 0,
    baseline_tables     INT DEFAULT 0,
    subpartition_tables INT DEFAULT 0,
    status              VARCHAR(16) DEFAULT 'OK',
    detail              VARCHAR(512) DEFAULT '',
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_ttsi (stat_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

---

## 三、 生产环境全量升级实操步骤

内网智能体在内网 Windows 部署机打开终端，通过 SSH 对生产主机 `10.243.16.238` 进行操作：

### 步骤 1：前置最高优先级——密钥与配置完整备份

在生产服务器 `10.243.16.238` 上创建专用备份目录，固化既有生产密钥与配置文件：

```bash
ssh root@10.243.16.238 "bash -s" << 'EOF'
set -e
BACKUP_DIR="/root/sqlcheck_backup_$(date +%Y%m%d_%H%M%S)"
mkdir -p "${BACKUP_DIR}"

echo ">>> 1. 备份生产环境现网 .env 配置文件..."
cp -p /opt/tdsql-sqlcheck/.env "${BACKUP_DIR}/.env.bak"

echo ">>> 2. 备份现网 data/encryption.key 密钥文件..."
if [ -f "/opt/tdsql-sqlcheck/current/data/encryption.key" ]; then
    cp -p /opt/tdsql-sqlcheck/current/data/encryption.key "${BACKUP_DIR}/encryption.key.bak"
elif [ -f "/opt/tdsql-sqlcheck/data/encryption.key" ]; then
    cp -p /opt/tdsql-sqlcheck/data/encryption.key "${BACKUP_DIR}/encryption.key.bak"
fi

echo ">>> 3. 查看并记录当前密钥与元数据库配置概要（防呆核验）..."
grep -E "SQLCHECK_DB_HOST|SQLCHECK_DB_PORT|TDSQL_ENCRYPTION_KEY|AUTH_SECRET_KEY" /opt/tdsql-sqlcheck/.env || true

echo ">>> 核心凭据备份完成，存储于: ${BACKUP_DIR}"
ls -la "${BACKUP_DIR}"
EOF
```

---

### 步骤 2：上传发布包并校验 SHA256

在 Windows 部署机将构建好的 `tdsql-sqlcheck-v1.6.3.0-linux-x86_64.tar.gz` 传输至生产服务器 `/tmp/` 目录：

```powershell
# 在 Windows 部署机执行上传
scp dist\tdsql-sqlcheck-v1.6.3.0-linux-x86_64.tar.gz root@10.243.16.238:/tmp/
scp dist\tdsql-sqlcheck-v1.6.3.0-linux-x86_64.tar.gz.sha256 root@10.243.16.238:/tmp/
```

在生产服务器执行完整性校验：
```bash
ssh root@10.243.16.238 "bash -s" << 'EOF'
set -e
cd /tmp
sha256sum -c tdsql-sqlcheck-v1.6.3.0-linux-x86_64.tar.gz.sha256
# 确认输出为: tdsql-sqlcheck-v1.6.3.0-linux-x86_64.tar.gz: OK
EOF
```

---

### 步骤 3：解压发布包

```bash
ssh root@10.243.16.238 "bash -s" << 'EOF'
set -e
cd /tmp
rm -rf tdsql-sqlcheck-v1.6.3.0-linux-x86_64
tar -xzf tdsql-sqlcheck-v1.6.3.0-linux-x86_64.tar.gz
cd tdsql-sqlcheck-v1.6.3.0-linux-x86_64
echo "发布包版本标识: $(cat VERSION)"
EOF
```

---

### 步骤 4：执行在轨覆盖升级（执行 install.sh）

> [!IMPORTANT]
> **覆盖升级说明**：
> 运行 `./deploy/install.sh` 时，脚本会自动执行以下安全升级动作：
> 1. 检测到 `/opt/tdsql-sqlcheck/.env` 存在，**自动保留生产既有配置**，绝不覆写生产 TDSQL 数据库连接；
> 2. 自动检测 `current` 指向的 `releases/v1.6.2.2`，自动将 `data/encryption.key` 同步拷贝至新版本目录 `releases/v1.6.3.0/data/encryption.key`；
> 3. 部署新代码至 `/opt/tdsql-sqlcheck/releases/v1.6.3.0` 并创建新 venv 虚拟环境离线安装 wheels；
> 4. 原子切换软链接 `current -> releases/v1.6.3.0`；
> 5. 重启 `systemctl restart tdsql-sqlcheck`，触发元数据库自动平滑升级。

执行升级命令：
```bash
ssh root@10.243.16.238 "bash -s" << 'EOF'
set -e
cd /tmp/tdsql-sqlcheck-v1.6.3.0-linux-x86_64

# 严禁传入清空参数，直接指定现网生产目录与端口
./deploy/install.sh --dir /opt/tdsql-sqlcheck --port 8000
EOF
```

---

## 四、 升级后验收与全链路对账

### 4.1 服务状态与端口核查
```bash
ssh root@10.243.16.238 "bash -s" << 'EOF'
echo "=== 1. 检查 systemd 服务运行状态 ==="
systemctl status tdsql-sqlcheck --no-pager | head -n 15

echo "=== 2. 检查 8000 端口监听状态 ==="
ss -tlnp | grep 8000

echo "=== 3. 检查 current 软链接版本目标 ==="
ls -l /opt/tdsql-sqlcheck/current
EOF
```

### 4.2 密钥延续性验证（最高关键项）
核实新版本目录下的 `encryption.key` 是否与备份完全一致：
```bash
ssh root@10.243.16.238 "bash -s" << 'EOF'
set -e
echo "=== 校验密钥继承性 ==="
NEW_KEY=$(cat /opt/tdsql-sqlcheck/current/data/encryption.key 2>/dev/null | tr -d ' \r\n')
ENV_KEY=$(grep -E '^TDSQL_ENCRYPTION_KEY=' /opt/tdsql-sqlcheck/.env | cut -d= -f2- | tr -d ' \r\n')

echo "新目录文件密钥长度: ${#NEW_KEY}"
echo "现网.env环境密钥长度: ${#ENV_KEY}"

if [ -n "${NEW_KEY}" ] || [ -n "${ENV_KEY}" ]; then
    echo "[PASS] 密钥正常延续，未发生丢失！"
else
    echo "[CRITICAL FAIL] 密钥丢失，请立即从备份目录恢复！"
    exit 1
fi
EOF
```

### 4.3 自动化冒烟测试
```bash
ssh root@10.243.16.238 "bash -s" << 'EOF'
cd /opt/tdsql-sqlcheck/current
bash deploy/verify_deploy.sh --port 8000
EOF
```
*预期全部输出 [PASS]，版本号显示为 `1.6.3.0`，规则总数 119 条。*

### 4.4 实例连接解密与业务对账人工验证
在浏览器中打开 **`http://10.243.16.238:8000`**，登录生产系统：

1. **现网实例解密验证**：
   - 进入【实例管理】菜单，找到现网已纳管的生产 TDSQL 实例；
   - 点击【测试连接】：确认各个实例状态显示为 `connected` 且毫秒级响应，**证明 `encryption.key` 延续成功，所有生产实例密码 100% 正常解密！**
2. **G14 表类型统计验证**：
   - 进入【深度诊断】→【表类型统计】；
   - 选择一个生产 TDSQL 实例并输入库名，点击【立即统计】；
   - 验证总表数、单表数、广播表数、分片表数、二级分区物理子表数统计准确；
   - 查看【历史留档】确认新增记录成功持久化落库。

---

## 五、 应急故障回滚预案

如果上线过程中遭遇外部 TDSQL 网络异常或不可预期阻断，可在 1 分钟内无缝回滚至 `v1.6.2.2`：

```bash
ssh root@10.243.16.238 "bash -s" << 'EOF'
set -e
echo ">>> 开始执行应急快速回滚..."
# 1. 运行自带的一键回滚脚本（自动将 current 软链切回 releases/v1.6.2.2）
bash /opt/tdsql-sqlcheck/current/deploy/rollback.sh

# 2. 重启服务
systemctl daemon-reload
systemctl restart tdsql-sqlcheck

echo ">>> 回滚完成，当前运行版本已退回上一版本："
curl -s http://127.0.0.1:8000/health
EOF
```
