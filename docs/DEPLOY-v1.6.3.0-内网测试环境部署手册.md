# TDSQL SQL审核工具 v1.6.3.0 内网测试环境全量部署说明书

| 属性 | 内容 |
|---|---|
| **目标软件版本** | `v1.6.3.0`（新增 G14 表类型统计原厂口径、并发状态机加固、发布依赖隔离治理） |
| **发布介质** | `tdsql-sqlcheck-v1.6.3.0-linux-x86_64.tar.gz`（及其 `.sha256` 校验文件） |
| **目标服务器** | `10.243.16.252`（银河麒麟 Advanced Server V10 SP3，海光 x86_64 CPU） |
| **操作执行端** | 内网 Windows 部署机（已与 `root@10.243.16.252` 建立 SSH 信任互信） |
| **测试元数据库** | `10.243.16.252` 本地独立安装的 **MySQL 8.0.28**（端口 `3306`） |
| **安装包预置路径** | 测试服务器 `/tmp/dist/` 目录 |

---

## 零、 核心安全红线与环境背景（内网智能体必读）

> [!CAUTION]
> **最高安全警告：克隆环境严防生产数据污染！**
> 1. **环境来源**：测试服务器 `10.243.16.252` 是由生产服务器 `10.243.16.238` 通过虚拟化平台整机克隆生成的；
> 2. **遗留风险**：机器上目前仍残留旧版 `v1.6.2.2` 程序，其既有配置文件 `/opt/tdsql-sqlcheck/.env` 中配置的元数据库是**生产环境外部 TDSQL 集群**！
> 3. **防呆铁律**：**严禁直接执行 `./deploy/install.sh`！** 因为安装脚本若检测到既有 `.env` 会默认继承。一旦在测试服务器上运行继承了旧配置的服务，**测试环境的操作将直接写入生产 TDSQL 数据库，导致生产数据灾难性污染！**
> 4. **核心原则**：必须先停掉旧版服务，将旧版目录完全移走备份，在本地独立安装 MySQL 8.0.28，并在新 `.env` 中明确将元数据库指向 `127.0.0.1:3306`！

---

## 一、 部署前准备与架构规划

### 1.1 端口与路径规划

| 组件 | 监听地址/路径 | 说明 |
|---|---|---|
| **SQLCheck Web/API** | `0.0.0.0:8000` | 主 Web 界面与 FastAPI 服务端口 |
| **本地 MySQL 元数据库** | `127.0.0.1:3306` | 本地自建 MySQL 8.0.28，仅供测试平台自身存储元数据 |
| **程序安装根目录** | `/opt/tdsql-sqlcheck` | 软链接至 `/opt/tdsql-sqlcheck/current` |
| **备份历史目录** | `/opt/tdsql-sqlcheck.bak.v1622` | 原克隆遗留的 v1.6.2.2 完整归档目录 |
| **离线介质工作区** | `/tmp/dist/` | 放置 MySQL 安装包及 `tdsql-sqlcheck-v1.6.3.0-linux-x86_64.tar.gz` |

### 1.2 Windows 部署机联通性验证
内网智能体在 Windows 部署机打开 PowerShell 终端，验证 SSH 互信连接：
```powershell
# 验证与测试机的免密 SSH 通信
ssh root@10.243.16.252 "uname -m && cat /etc/kylin-release"
```
*预期输出*：
```text
x86_64
Kylin Linux Advanced Server release V10 (Sword)
```

---

## 二、 第一阶段：停止旧版服务与隔离清理

在 Windows 部署机执行以下远程命令，停用生产克隆残留的 `v1.6.2.2` 进程并安全隔离目录：

```bash
ssh root@10.243.16.252 "bash -s" << 'EOF'
set -e
echo ">>> [1/3] 停止可能正在运行的旧版 systemd 服务..."
systemctl stop tdsql-sqlcheck 2>/dev/null || true
systemctl disable tdsql-sqlcheck 2>/dev/null || true

echo ">>> [2/3] 检查并清理残留的 python/uvicorn 孤儿进程..."
pkill -f "backend.main:app" || true

echo ">>> [3/3] 彻底备份并移走生产克隆残留的旧程序目录..."
if [ -d "/opt/tdsql-sqlcheck" ]; then
    BACKUP_NAME="/opt/tdsql-sqlcheck.bak.v1622.$(date +%Y%m%d%H%M%S)"
    mv /opt/tdsql-sqlcheck "${BACKUP_NAME}"
    echo "已成功将克隆的生产旧目录隔离归档至: ${BACKUP_NAME}"
fi
EOF
```

---

## 三、 第二阶段：在 10.243.16.252 本地离线安装 MySQL 8.0.28

管理员已将 MySQL 安装包放置在测试机的 `/tmp/dist/` 目录下。

### 3.1 RPM Bundle 离线安装方法（推荐方案）
如果 `/tmp/dist/` 下放置的是 `mysql-8.0.28-1.el8.x86_64.rpm-bundle.tar`：

```bash
ssh root@10.243.16.252 "bash -s" << 'EOF'
set -e
cd /tmp/dist

echo ">>> 1. 检查并清理麒麟预装可能冲突的 mariadb 依赖..."
rpm -qa | grep -iE "mariadb|postfix" | xargs -r rpm -e --nodeps 2>/dev/null || true

echo ">>> 2. 解压 MySQL 8.0.28 RPM Bundle..."
mkdir -p /tmp/dist/mysql-pkg
tar -xf mysql-8.0.28-1.el8.x86_64.rpm-bundle.tar -C /tmp/dist/mysql-pkg/
cd /tmp/dist/mysql-pkg

echo ">>> 3. 按严格依赖顺序离线安装 RPM 包..."
rpm -ivh \
  mysql-community-common-8.0.28-1.el8.x86_64.rpm \
  mysql-community-client-plugins-8.0.28-1.el8.x86_64.rpm \
  mysql-community-libs-8.0.28-1.el8.x86_64.rpm \
  mysql-community-client-8.0.28-1.el8.x86_64.rpm \
  mysql-community-server-8.0.28-1.el8.x86_64.rpm

echo ">>> 4. 优化 MySQL 配置文件 /etc/my.cnf..."
cat << 'MYCNF' > /etc/my.cnf
[mysqld]
port = 3306
datadir = /var/lib/mysql
socket = /var/lib/mysql/mysql.sock
log-error = /var/log/mysqld.log
pid-file = /var/run/mysqld/mysqld.pid

# 字符集与大小写规范
character-set-server = utf8mb4
collation-server = utf8mb4_bin
lower_case_table_names = 1

# 连接池与内存优化
max_connections = 500
innodb_buffer_pool_size = 512M
default_authentication_plugin = mysql_native_password
MYCNF

echo ">>> 5. 启动 mysqld 服务并设置开机自启..."
systemctl daemon-reload
systemctl enable mysqld
systemctl start mysqld
systemctl status mysqld --no-pager | grep Active
EOF
```

### 3.2 初始化 MySQL 密码策略与 Root 口令
首次启动 MySQL 后，从日志中提取初始密码并修改为测试密码（如 `Mysql_Root_2026!`）：

```bash
ssh root@10.243.16.252 "bash -s" << 'EOF'
set -e
# 提取临时初始密码
INIT_PWD=$(grep 'temporary password' /var/log/mysqld.log | awk '{print $NF}' | tail -n 1)
echo "提取到的 MySQL 临时密码为: [${INIT_PWD}]"

# 修改 root 密码并放开测试环境密码策略
mysql -uroot -p"${INIT_PWD}" --connect-expired-password << 'SQL'
SET GLOBAL validate_password.policy = 0;
SET GLOBAL validate_password.length = 8;
ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY 'Mysql_Root_2026!';
FLUSH PRIVILEGES;
SQL

echo ">>> MySQL Root 密码重置成功: Mysql_Root_2026!"
EOF
```

---

## 四、 第三阶段：初始化元数据库与授权用户

执行发布包自带的 `init_metadata_mysql8.sql`，完成 `tdsql_sqlcheck` 库的建库、建表与应用专属用户 `sqlcheck_app`（口令 `SqlCheck_App_2026!`）授权。

在测试服务器上执行建库与授权命令：

```bash
ssh root@10.243.16.252 "bash -s" << 'EOF'
set -e
mysql -uroot -p'Mysql_Root_2026!' << 'SQL'
-- 1. 创建元数据库
CREATE DATABASE IF NOT EXISTS `tdsql_sqlcheck` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;

-- 2. 创建应用服务专属账号并授权
CREATE USER IF NOT EXISTS 'sqlcheck_app'@'%' IDENTIFIED WITH mysql_native_password BY 'SqlCheck_App_2026!';
CREATE USER IF NOT EXISTS 'sqlcheck_app'@'localhost' IDENTIFIED WITH mysql_native_password BY 'SqlCheck_App_2026!';
CREATE USER IF NOT EXISTS 'sqlcheck_app'@'127.0.0.1' IDENTIFIED WITH mysql_native_password BY 'SqlCheck_App_2026!';

GRANT ALL PRIVILEGES ON `tdsql_sqlcheck`.* TO 'sqlcheck_app'@'%';
GRANT ALL PRIVILEGES ON `tdsql_sqlcheck`.* TO 'sqlcheck_app'@'localhost';
GRANT ALL PRIVILEGES ON `tdsql_sqlcheck`.* TO 'sqlcheck_app'@'127.0.0.1';
FLUSH PRIVILEGES;
SQL

echo ">>> 测试使用 sqlcheck_app 账号登录元数据库..."
mysql -usqlcheck_app -p'SqlCheck_App_2026!' -h127.0.0.1 -D tdsql_sqlcheck -e "SELECT DATABASE(), USER(), VERSION();"
echo ">>> 元数据库及账号就绪！"
EOF
```

---

## 五、 第四阶段：全量部署 TDSQL-SQLCheck v1.6.3.0

### 5.1 上传并解压发布包
在 Windows 部署机将构建好的 `tdsql-sqlcheck-v1.6.3.0-linux-x86_64.tar.gz` 上传至测试机 `/tmp/`：

```powershell
# 在 Windows 部署机执行上传（PowerShell）
scp dist\tdsql-sqlcheck-v1.6.3.0-linux-x86_64.tar.gz root@10.243.16.252:/tmp/
```

### 5.2 导入完整表结构、准备专用 `.env` 并执行部署
在测试服务器上解压发布包，执行发布包内自带的 `deploy/init_metadata_mysql8.sql` 导入基础表结构，编写严格指向本地 MySQL 的 `deploy/.env`，执行 `install.sh`：

```bash
ssh root@10.243.16.252 "bash -s" << 'EOF'
set -e
cd /tmp
rm -rf tdsql-sqlcheck-v1.6.3.0-linux-x86_64
tar -xzf tdsql-sqlcheck-v1.6.3.0-linux-x86_64.tar.gz
cd /tmp/tdsql-sqlcheck-v1.6.3.0-linux-x86_64

echo ">>> 1. 导入完整元数据全量表结构 (deploy/init_metadata_mysql8.sql)..."
if [ -f "deploy/init_metadata_mysql8.sql" ]; then
    mysql -uroot -p'Mysql_Root_2026!' < deploy/init_metadata_mysql8.sql
    echo ">>> 初始元数据表导入成功！"
fi

echo ">>> 2. 编写测试环境专用 deploy/.env (严格绑定本地 127.0.0.1 MySQL)..."
cat << 'ENVEOF' > deploy/.env
# ============================================================================
# TDSQL SQL审核工具 v1.6.3.0 内网测试环境配置文件 (10.243.16.252)
# ============================================================================

# 系统元数据库：强制指向 10.243.16.252 本地 MySQL 8.0.28（切勿指向生产 TDSQL！）
SQLCHECK_DB_HOST=127.0.0.1
SQLCHECK_DB_PORT=3306
SQLCHECK_DB_USER=sqlcheck_app
SQLCHECK_DB_PASSWORD=SqlCheck_App_2026!
SQLCHECK_DB_NAME=tdsql_sqlcheck
SQLCHECK_DB_POOL_SIZE=20

# 认证与基础安全
AUTH_ENABLED=true
AUTH_SECRET_KEY=
ADMIN_INITIAL_PASSWORD=Admin_Test_2026!
AUTH_TOKEN_TTL_HOURS=12
AUTH_MAX_LOGIN_FAILURES=5
AUTH_LOCK_MINUTES=15
DATA_MASKING_ENABLED=true
DOCS_PUBLIC=false
LOGIN_IP_FAIL_LIMIT=0
LOGIN_IP_FAIL_WINDOW=60
MAX_BODY_BYTES=52428800

# 调度与容量
SCHEDULER_ENABLED=true
CONNECTION_POOL_MAX_INSTANCES=200
MAX_CONCURRENT_SCANS_PER_CONNECTION=2
MAX_CONCURRENT_SCANS_GLOBAL=16
ENVEOF

chmod 600 deploy/.env

echo ">>> 3. 执行安装脚本 deploy/install.sh --dir /opt/tdsql-sqlcheck --port 8000..."
./deploy/install.sh --dir /opt/tdsql-sqlcheck --port 8000
EOF
```

---

## 六、 第五阶段：部署验收与联调验证

### 6.1 服务进程与端口检查
```bash
ssh root@10.243.16.252 "bash -s" << 'EOF'
echo "=== 1. 检查 systemd 服务状态 ==="
systemctl status tdsql-sqlcheck --no-pager | head -n 15

echo "=== 2. 检查 8000 端口监听状态 ==="
ss -tlnp | grep 8000

echo "=== 3. 检查系统日志 ==="
journalctl -u tdsql-sqlcheck -n 30 --no-pager
EOF
```

### 6.2 自动化冒烟接口验证
```bash
ssh root@10.243.16.252 "bash -s" << 'EOF'
cd /opt/tdsql-sqlcheck/current
export SQLCHECK_VERIFY_PASSWORD="Admin_Test_2026!"
bash deploy/verify_deploy.sh --port 8000
EOF
```
*预期输出*：
```text
════ 部署验证 v1.6.3.0 @ http://127.0.0.1:8000 ════
  [PASS] 探针响应 {"status":"ok","version":"1.6.3.0"}
  [PASS] 版本号 1.6.3.0
  [PASS] 首页可访问
  [PASS] 静态资产 /static/js/app.js
  [PASS] 静态资产 /static/css/app.css
  [PASS] 静态资产 /static/vendor/vue.global.prod.js
  [PASS] admin 登录成功（认证已启用）
  [PASS] 规则总数 119
  [PASS] Oracle迁移兼容规则 42 条
  [PASS] 审核引擎命中 R080(nvl)
  [PASS] 元数据库读写正常
```

### 6.3 人工浏览器端到端验收指南

在 Windows 部署机浏览器中访问：**`http://10.243.16.252:8000`**

1. **登录验证**：
   - 登录账号：`admin`
   - 初始密码：`Admin_Test_2026!`（首次登录按系统提示修改密码并妥善记录）；
   - 确认右上角与系统信息版本号明确显示为 **`V1.6.3.0`**。
2. **G14 表类型统计验证**：
   - 点击导航栏 **【深度诊断】→【表类型统计】**；
   - 注册或选择测试 TDSQL 实例与库名，点击【立即统计】；
   - 检查卡片：确认总表数、单表数、广播表数、分片表数、二级分区物理子表数展示清晰无误；
   - 点击【查看历史留档】，确认历史批次记录可正常回看与比对。
3. **数据隔离核实（最关键）**：
   - 登录测试机 MySQL：
     ```bash
     mysql -usqlcheck_app -p'SqlCheck_App_2026!' -h127.0.0.1 tdsql_sqlcheck -e "SELECT COUNT(*) FROM audit_history; SELECT COUNT(*) FROM users;"
     ```
   - 确认表记录全部分布在本地 `10.243.16.252` 的 MySQL 中，**绝无任何向 10.243.16.238 外部生产 TDSQL 的跨机流量**！

---

## 七、 常用运维命令速查

| 操作场景 | 执行命令 |
|---|---|
| **查看服务状态** | `systemctl status tdsql-sqlcheck` |
| **重启平台服务** | `systemctl restart tdsql-sqlcheck` |
| **停止平台服务** | `systemctl stop tdsql-sqlcheck` |
| **实时查看日志** | `journalctl -u tdsql-sqlcheck -f` |
| **查看本地 MySQL** | `systemctl status mysqld` |
| **查看配置文件** | `cat /opt/tdsql-sqlcheck/.env` |
| **回滚旧版本** | `bash /opt/tdsql-sqlcheck/current/deploy/rollback.sh` |
