# TDSQL SQL审核工具 v1.6.4.1 内网测试环境全量升级部署手册

| 属性 | 详细规格 / 部署元数据 |
|---|---|
| **目标软件版本** | **`v1.6.4.1`**（Copilot 稳健性增强版：包含独立 SQL DDL 与台账原子登记、端点默认 `/v1`、自检锁态自动调和、连接池防耗尽加固） |
| **基线版本** | `v1.6.4.0`（或当前内网测试环境运行版本 `v1.6.3.7`） |
| **部署模式** | **全量物理隔离升级（Full Directory-Isolated Upgrade，直接复用既有运行虚拟环境 `venv`）** |
| **发布介质** | `tdsql-sqlcheck-v1.6.4.1-linux-x86_64.tar.gz`（及其同名 `.sha256` 校验和文件） |
| **目标测试服务器** | `10.243.16.252`（银河麒麟 Linux Advanced Server V10 SP3，海光/x86_64 架构） |
| **执行权限** | `root` 用户登录执行 |
| **测试元数据库** | `10.243.16.252` 本地独立运行的 **MySQL 8.0.28**（端口 `3306`，数据库 `tdsql_sqlcheck`） |
| **介质上传路径** | `/tmp/dist/` 或 `/tmp/` |
| **目标部署根目录** | `/opt/tdsql-sqlcheck`（新版本目录 `/opt/tdsql-sqlcheck/releases/v1.6.4.1`，生效软链接 `/opt/tdsql-sqlcheck/current`） |
| **编制智能体** | **智能体 G**（外网开发与打包智能体） |
| **受众智能体** | **Lingma**（内网测试环境部署与运维智能体） |

---

## 零、 针对 v1.6.4.0 部署实录反馈的针对性优化（智能体 Lingma 必读）

针对智能体 Lingma 在 `DEPLOY-RECORD-v1.6.4.0-部署实录与避坑指南.md` 中提出的 **4 项实操痛点** 与 **9 项避坑建议**，外网智能体 G 已在 `v1.6.4.1` 发布包和部署体系中进行了全方位落地：

```
┌───────────────────────────────────────┬────────────────────────────────────────────────────────┐
│ Lingma 报告的痛点 (v1.6.4.0)          │ 智能体 G 在 v1.6.4.1 中的系统性解决方案                │
├───────────────────────────────────────┼────────────────────────────────────────────────────────┤
│ 痛点 1: schema.py 迁移因连接池耗尽报1 │ 1. 升级脚本前置安全停止 Runner 释放连接池与命名锁；    │
│ (Errno 111 / 20个连接被占满)          │ 2. 发布包内置纯 DDL 脚本，支持直接通过 MySQL CLI 纳管。│
├───────────────────────────────────────┼────────────────────────────────────────────────────────┤
│ 痛点 2: copilot-runner 报"台账未登记" │ 1. 发布包根目录与 deploy/ 提供 init_copilot_tables.sql，│
│ 且校验和计算规则不透明                │    内含 9 张表建表 DDL + schema_migrations 原子登记；  │
│                                       │ 2. 提供 CHECKSUM 校验文件并文档化计算规则。            │
├───────────────────────────────────────┼────────────────────────────────────────────────────────┤
│ 痛点 3: Web 重启时短暂报              │ 规范化三服务启动依赖顺序：                            │
│ local_ready=False                     │ 先 metadata-runner -> 次 copilot-runner -> 后 Web 服务 │
├───────────────────────────────────────┼────────────────────────────────────────────────────────┤
│ 痛点 4: 模型自检报 500 / 状态卡死     │ 1. 供应商配置 base_path 默认值统一固化为 "/v1"；       │
│ INTERRUPTED 无法重新自检              │ 2. 优化自检幂等逻辑，非 SUCCEEDED 轮次允许自动重试。   │
└───────────────────────────────────────┴────────────────────────────────────────────────────────┘
```

---

## 一、 核心避坑铁律与安全防呆规范

在执行任何部署命令前，请严格复核以下 **7 项安全防呆铁律**：

> [!CAUTION]
> ### 铁律 1（致命 Python 解释器陷阱）：严禁重新安装 venv 或执行 pip install！
> 银河麒麟系统自带的全局 Python 解释器存在标准库缺陷（缺少 `encodings` 模块）。**绝不可在新目录执行 `python -m venv` 或 `pip install`**！
> 必须直接复制上一版本的既有虚拟环境：
> ```bash
> cp -a /opt/tdsql-sqlcheck/current/venv /opt/tdsql-sqlcheck/releases/v1.6.4.1/venv
> ```

> [!CAUTION]
> ### 铁律 2（严防克隆环境污染生产）：绝对严禁覆盖或重置 `.env`！
> 测试服务器 `10.243.16.252` 必须保持本地自建 MySQL（`127.0.0.1:3306`）。**发布包中的 `env.template` 严禁直接覆盖根目录 `/opt/tdsql-sqlcheck/.env`**！

> [!IMPORTANT]
> ### 铁律 3（解密密钥与端点配置连续性）：必须同步 `encryption.key` 与 `copilot-endpoints.json`
> 新版本必须继承数据目录下的核心凭据：
> 1. 加密密钥：`/opt/tdsql-sqlcheck/current/data/encryption.key` ➔ `/opt/tdsql-sqlcheck/releases/v1.6.4.1/data/encryption.key`
> 2. 端点配置：`/opt/tdsql-sqlcheck/data/copilot-endpoints.json` 软链接至 `/opt/tdsql-sqlcheck/releases/v1.6.4.1/data/copilot-endpoints.json`

> [!IMPORTANT]
> ### 铁律 4（数据库操作前必须停用后台 Runner）：释放连接池与命名锁
> 在执行 `schema.py --apply` 或 `mysql < init_copilot_tables.sql` 前，**必须先停止两个后台服务**：
> ```bash
> systemctl stop tdsql-copilot-runner
> systemctl stop tdsql-metadata-runner
> ```
> 避免后台轮询占满 20 个 MySQL 连接或霸占 `tdsql_copilot_runner` 命名锁。

> [!IMPORTANT]
> ### 铁律 5（三服务启动顺序铁律）：先后台 Runner，后 Web 主服务
> 严格按依赖顺序依次拉起：
> 1. `systemctl restart tdsql-metadata-runner` ➔ 校验 `active (running)`
> 2. `systemctl restart tdsql-copilot-runner` ➔ 校验 `active (running)`（完成 B 组结构验收）
> 3. `systemctl restart tdsql-sqlcheck` ➔ 校验 `active (running)`

> [!NOTE]
> ### 铁律 6（Copilot 模型端点配置规范）
> - **Base Path**：默认且必须为 `/v1`（除非私有网关根路径即为推理端点）。
> - **SSRF 防护**：供应商端点 URL **严禁填写 `127.0.0.1` 或 `localhost`**，必须使用内网真实物理机 IP 或域名（如 `http://10.x.x.x:8000`）。
> - **安全分级**：内网环境配置请选择 `INTERNAL`。

> [!WARNING]
> ### 铁律 7（旧 JWT 凭据刷新）：升级后请重新登录 Web 端
> `v1.6.4.1` 启用了代际身份校验，升级前浏览器保留的旧 JWT 会被判定早于 Subject 纳管时点而返回 401。**升级完成后，测试人员只需在 Web 页面点击右上角退出登录并重新登录一次即可**。

---

## 二、 发布介质明细与校验

发布包构建于外网开发环境，产物位于 `/tmp/dist/`（或内网中转机目录）：

| 文件名 | 说明 | SHA256 校验和 |
|---|---|---|
| `tdsql-sqlcheck-v1.6.4.1-linux-x86_64.tar.gz` | v1.6.4.1 完整全量发布包（包含前后端、依赖 wheels、部署脚本、文档及 SQL） | 以同级 `.sha256` 校验和文件为准 |
| `tdsql-sqlcheck-v1.6.4.1-linux-x86_64.tar.gz.sha256` | 官方 SHA256 校验和文件（当前构建值：`25951b5d548732a6cdf830e9cad0c5c522fb81975cf236e698216345d827f6ad`） | - |

### 介质完整性核验命令
在测试机终端执行：
```bash
cd /tmp/dist
sha256sum -c tdsql-sqlcheck-v1.6.4.1-linux-x86_64.tar.gz.sha256
# 控制台必须输出: tdsql-sqlcheck-v1.6.4.1-linux-x86_64.tar.gz: 确定 (或 OK)
```

---

## 三、 全量升级标准操作流程（自动化脚本方式）

发布包已全面升级 `upgrade_incremental.sh`，内部已内嵌连接池安全释放、自动回退至纯 DDL 迁移、软链接原子切换及三服务接管逻辑。

### 步骤 1：解压发布包
```bash
cd /tmp/dist
tar -xzf tdsql-sqlcheck-v1.6.4.1-linux-x86_64.tar.gz
cd tdsql-sqlcheck-v1.6.4.1-linux-x86_64
```

### 步骤 2：执行自动化升级脚本
```bash
# 以 root 权限运行升级脚本（参数指定目标安装根目录与端口）
bash deploy/upgrade_incremental.sh --target /opt/tdsql-sqlcheck --port 8000
```

升级脚本将自动执行以下全套流水线：
1. 校验版本为 `1.6.4.1` 并创建 `/opt/tdsql-sqlcheck/releases/v1.6.4.1`；
2. 继承既有 `venv` 虚拟环境，保持依赖秒级就绪；
3. 继承 `data/encryption.key` 并建立 `copilot-endpoints.json` 软链接；
4. **安全预停止后台 Runner 服务，释放连接池与锁**；
5. 执行 `schema.py --apply`；若遭遇任何环境异常，**自动无缝回退执行 `init_copilot_tables.sql` 补全 9 张业务表并原子登记台账**；
6. 原子切换 `/opt/tdsql-sqlcheck/current` 软链接指向 `releases/v1.6.4.1`；
7. 依次重新注册并拉起 `tdsql-metadata-runner`、`tdsql-copilot-runner`、`tdsql-sqlcheck`；
8. 触发自动化 14 项验收测试。

---

## 四、 全量升级分步操作流程（手动备用 / 深度掌控方式）

若内网智能体 Lingma 希望逐步排查或因特殊安全管控需逐行操作，可遵循以下分步手册：

### 4.1 准备新版本目录与依赖
```bash
INSTALL_DIR="/opt/tdsql-sqlcheck"
CURRENT_RELEASE=$(readlink -f "${INSTALL_DIR}/current")
TARGET_RELEASE="${INSTALL_DIR}/releases/v1.6.4.1"

echo "当前运行版本目录: ${CURRENT_RELEASE}"
echo "新版本目标目录: ${TARGET_RELEASE}"

# 1. 创建目标目录
mkdir -p "${TARGET_RELEASE}"

# 2. 复制源码、前端资产、脚本与文档
cp -a /tmp/dist/tdsql-sqlcheck-v1.6.4.1-linux-x86_64/backend "${TARGET_RELEASE}/"
cp -a /tmp/dist/tdsql-sqlcheck-v1.6.4.1-linux-x86_64/frontend "${TARGET_RELEASE}/"
cp -a /tmp/dist/tdsql-sqlcheck-v1.6.4.1-linux-x86_64/deploy "${TARGET_RELEASE}/"
cp -a /tmp/dist/tdsql-sqlcheck-v1.6.4.1-linux-x86_64/docs "${TARGET_RELEASE}/"
cp -a /tmp/dist/tdsql-sqlcheck-v1.6.4.1-linux-x86_64/VERSION "${TARGET_RELEASE}/"
cp -a /tmp/dist/tdsql-sqlcheck-v1.6.4.1-linux-x86_64/requirements.txt "${TARGET_RELEASE}/"

# 3. 继承虚拟环境 (避免麒麟系统 Python 缺陷)
cp -a "${CURRENT_RELEASE}/venv" "${TARGET_RELEASE}/venv"

# 4. 继承数据密钥与端点配置
mkdir -p "${TARGET_RELEASE}/data" "${INSTALL_DIR}/data"
cp "${CURRENT_RELEASE}/data/encryption.key" "${TARGET_RELEASE}/data/encryption.key" 2>/dev/null || \
cp "${INSTALL_DIR}/data/encryption.key" "${TARGET_RELEASE}/data/encryption.key"

if [[ -f "${INSTALL_DIR}/data/copilot-endpoints.json" ]]; then
  ln -sf "${INSTALL_DIR}/data/copilot-endpoints.json" "${TARGET_RELEASE}/data/copilot-endpoints.json"
fi
```

### 4.2 停止旧服务并释放连接池
```bash
# 必须先停后台执行器，防止占用 MySQL 连接池与锁
systemctl stop tdsql-copilot-runner || true
systemctl stop tdsql-metadata-runner || true
systemctl stop tdsql-sqlcheck || true
```

### 4.3 数据库结构迁移与台账核验（双重保险）

**方案 A：通过 Python 迁移入口执行**
```bash
"${TARGET_RELEASE}/venv/bin/python" -m backend.services.copilot.schema --apply
```
> 若返回 `{"status": "READY", ...}`，说明表结构与台账已全部正常就绪。

**方案 B：若方案 A 因任何环境因素报错，直接执行独立 SQL 脚本（100% 成功保底）**
```bash
# 从 .env 读取测试库凭据
DB_PASS=$(grep "^SQLCHECK_DB_PASSWORD=" "${INSTALL_DIR}/.env" | cut -d= -f2- | tr -d '\r "')

# 使用 MySQL 客户端直接执行发布包内置的纯 DDL + 台账原子登记脚本
MYSQL_PWD="${DB_PASS}" mysql -h 127.0.0.1 -P 3306 -u sqlcheck_app tdsql_sqlcheck \
  < "${TARGET_RELEASE}/deploy/init_copilot_tables.sql"
```

**台账验证核对命令**：
```bash
MYSQL_PWD="${DB_PASS}" mysql -h 127.0.0.1 -P 3306 -u sqlcheck_app tdsql_sqlcheck -e \
  "SELECT version_key, checksum, applied_at FROM schema_migrations WHERE version_key LIKE 'copilot_%';"
```
预期输出：
```
+--------------------------+------------------------------------------------------------------+---------------------+
| version_key              | checksum                                                         | applied_at          |
+--------------------------+------------------------------------------------------------------+---------------------+
| copilot_v1_001_business  | 6cdda7b12cbdc2dba6a40815fbc0318b489216c9fd838979289890a1d90112b0 | 2026-09-xx xx:xx:xx |
+--------------------------+------------------------------------------------------------------+---------------------+
```

### 4.4 切换软链接并授权
```bash
# 记录前一版本以备回滚
echo "${CURRENT_RELEASE}" > "${INSTALL_DIR}/.previous_release"

# 原子切换 current 链接
ln -sfn "${TARGET_RELEASE}" "${INSTALL_DIR}/current"

# 统一权限
chown -R root:root "${INSTALL_DIR}"
```

### 4.5 安装并更新 Systemd 服务配置
```bash
# 1. metadata-runner
sed -e "s|__INSTALL_DIR__|${INSTALL_DIR}|g" -e "s|__USER__|root|g" \
    "${TARGET_RELEASE}/deploy/tdsql-metadata-runner.service" > /etc/systemd/system/tdsql-metadata-runner.service

# 2. copilot-runner
sed -e "s|__INSTALL_DIR__|${INSTALL_DIR}|g" -e "s|__USER__|root|g" \
    "${TARGET_RELEASE}/deploy/tdsql-copilot-runner.service" > /etc/systemd/system/tdsql-copilot-runner.service

# 3. Web 主服务
sed -e "s|__INSTALL_DIR__|${INSTALL_DIR}|g" -e "s|__PORT__|8000|g" -e "s|__USER__|root|g" \
    "${TARGET_RELEASE}/deploy/tdsql-sqlcheck.service" > /etc/systemd/system/tdsql-sqlcheck.service

# 重载 systemd
systemctl daemon-reload
```

### 4.6 按照顺序拉起三服务
```bash
# 步骤 1: 启动元数据审核执行器
systemctl enable tdsql-metadata-runner >/dev/null 2>&1
systemctl restart tdsql-metadata-runner
sleep 2
systemctl is-active tdsql-metadata-runner

# 步骤 2: 启动 Copilot 助手调度器 (必须在 Web 前拉起并通过验收)
systemctl enable tdsql-copilot-runner >/dev/null 2>&1
systemctl restart tdsql-copilot-runner
sleep 2
systemctl is-active tdsql-copilot-runner

# 步骤 3: 启动 Web 主服务
systemctl enable tdsql-sqlcheck >/dev/null 2>&1
systemctl restart tdsql-sqlcheck
sleep 3
systemctl is-active tdsql-sqlcheck
```

---

## 五、 部署后全方位验收与验证

### 5.1 自动化 14 项黄金流水线验证
执行验证脚本：
```bash
bash /opt/tdsql-sqlcheck/current/deploy/verify_deploy.sh --port 8000
```
**严格放行标准**：
```
════ 部署验证 v1.6.4.1 @ http://127.0.0.1:8000 ════

  [PASS] 健康探针 HTTP 成功
  [PASS] 版本号 1.6.4.1
  [PASS] metadata-runner 服务运行中
  [PASS] copilot-runner 服务运行中
  [PASS] 首页可访问
  [PASS] 静态资产 /static/js/app.js
  [PASS] 静态资产 /static/css/app.css
  [PASS] 静态资产 /static/vendor/vue.global.prod.js
  [PASS] admin 登录成功（认证已启用）
  [PASS] 规则总数 121
  [PASS] Oracle迁移兼容规则 42 条
  [PASS] 审核引擎命中 R080(nvl)
  [PASS] 元数据库读写正常(概览 today_count=1)
  [PASS] /metrics 指标输出
════ 验证结果: PASS=14 FAIL=0 SKIP=0 ════
部署验证全部通过
```

### 5.2 日志健康度核查
检查 `tdsql-sqlcheck` 与 `tdsql-copilot-runner` 的运行日志：
```bash
# 查看 Web 服务启动状态
journalctl -u tdsql-sqlcheck -n 20 --no-pager
# 确认输出包含:
# TDSQL SQL审核平台已就绪 (V1.6.4.1)
# Copilot 启动验收完成: local_ready=True reason=

# 查看 Copilot Runner 启动状态
journalctl -u tdsql-copilot-runner -n 20 --no-pager
# 确认输出包含:
# 知识包加载完成: kb-1.6.4.0-xxx
# 数据库初始化完成 (V2.0, MySQL)
```

### 5.3 Copilot 端点与模型自检核验
1. 浏览器打开 `http://10.243.16.252:8000` 并登录（重新登录以获取新 JWT）；
2. 导航至 **系统设置 ➔ Copilot 模型管理**（或通过管理接口）：
   - 确认供应商端点基础路径已规范配置为 `/v1`；
   - 点击 **测试连接 / 模型自检**；
   - 确认返回 **自检成功 (SUCCEEDED)**；
3. 打开右下角 Copilot 悬浮对话气泡，输入一句测试语句（例如："请解释什么是 TDSQL 分布式事务？"），确认流式或正常回复，验证通过！

---

## 六、 常见问题与应急排查（Troubleshooting）

### 6.1 问题：执行 `schema.py --apply` 报错或返回非零
- **现象**：提示 `OperationalError: (2003, Can't connect...)` 或连接池无法检出。
- **原因**：历史服务未停止或连接未释放。
- **解法**：直接执行纯 DDL 脚本保底！
  ```bash
  mysql -h 127.0.0.1 -P 3306 -u sqlcheck_app -p'SqlCheck_App_2026!' tdsql_sqlcheck \
    < /opt/tdsql-sqlcheck/current/deploy/init_copilot_tables.sql
  ```

### 6.2 问题：Runner 日志报 "无法获取 tdsql_copilot_runner 命名锁"
- **现象**：`copilot-runner` 启动退出，日志显示锁获取失败。
- **原因**：可能存在孤儿 Python 进程在后台运行，或手动在命令行前台调试时霸占了锁。
- **解法**：
  ```bash
  # 查找并终止异常进程
  pkill -f "copilot_runner"
  systemctl restart tdsql-copilot-runner
  ```

### 6.3 问题：Web 页面打开报 401 Unauthorized / AUTH_REQUIRED
- **原因**：代际身份 JWT 失效（安全防护机制正常生效）。
- **解法**：在页面右上角点击 **退出登录**，然后重新输入账号口令登录即可。

### 6.4 问题：Web 登录失败连续超过 5 次导致 admin 账号被锁定
- **原因**：系统内置防暴力破解安全保护机制，连续输错 5 次口令会自动锁定该账户 15 分钟。
- **默认凭据**：平台初始超级管理员账号为 `admin`，初始密码定义在部署目标机的 `.env` 文件中的 `ADMIN_INITIAL_PASSWORD`（例如 `Admin_Test_2026!` 或测试环境自设密码），严禁盲猜口令。
- **一键解锁命令**：若不慎被锁定且不愿等待 15 分钟冷却期，可在测试机执行单行 SQL 瞬间解锁：
  ```bash
  DB_PASS=$(grep "^SQLCHECK_DB_PASSWORD=" /opt/tdsql-sqlcheck/.env | cut -d= -f2- | tr -d '\r "')
  MYSQL_PWD="${DB_PASS}" mysql -h 127.0.0.1 -P 3306 -u sqlcheck_app tdsql_sqlcheck \
    -e "UPDATE users SET failed_attempts = 0, locked_until = NULL WHERE username = 'admin';"
  ```

---

## 七、 极速一键回滚预案

若升级后发现无法容忍的异常，可随时执行以下命令秒级回滚到升级前的稳定版本：

```bash
INSTALL_DIR="/opt/tdsql-sqlcheck"
PREV_RELEASE=$(cat "${INSTALL_DIR}/.previous_release" 2>/dev/null || echo "${INSTALL_DIR}/releases/v1.6.4.0")

echo "正在触发紧急回滚 ➔ ${PREV_RELEASE} ..."

# 1. 切回旧版本软链接
ln -sfn "${PREV_RELEASE}" "${INSTALL_DIR}/current"

# 2. 停止并重新加载旧版本服务
systemctl restart tdsql-metadata-runner
systemctl restart tdsql-copilot-runner || true
systemctl restart tdsql-sqlcheck

# 3. 校验回滚版本
echo "回滚完成，当前运行版本: $(cat ${INSTALL_DIR}/current/VERSION)"
systemctl status tdsql-sqlcheck --no-pager
```

---

**编制**：智能体 G  
**交付对象**：Lingma（内网部署智能体） / MR.Linsang  
**版本归档**：v1.6.4.1  
**日期**：2026-09-21
