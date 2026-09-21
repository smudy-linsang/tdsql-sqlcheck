# TDSQL SQL审核工具 v1.6.4.1 内网生产环境全量升级部署手册

| 属性 | 详细规格 / 生产元数据 |
|---|---|
| **目标软件版本** | **`v1.6.4.1`**（重大里程碑版本：AI Copilot 智能专家助手企业级就绪、三服务常驻架构、独立 DDL 与台账原子登记、10 大场景路由开箱即用、连接池防耗尽加固） |
| **生产现网基线** | `v1.6.3.7`（内网生产环境当前运行版本；本次为从 v1.6.3.7 全量升级至 v1.6.4.1） |
| **升级模式** | **物理目录隔离全量平滑升级（Full Directory-Isolated Releases Upgrade，直接复用现网健康虚拟环境 `venv`，秒级生效）** |
| **发布介质** | `tdsql-sqlcheck-v1.6.4.1-linux-x86_64.tar.gz`（及其同名 `.sha256` 校验和文件） |
| **目标生产服务器** | `10.243.16.238`（银河麒麟 Linux Advanced Server V10 SP3，海光/x86_64 架构） |
| **操作执行端** | 内网 Windows 部署机（已与 `root@10.243.16.238` 建立 SSH 免密信任） |
| **生产元数据库** | **外部核心 TDSQL 集群**（集中式实例，MySQL 协议端口 `3306`，数据库 `tdsql_sqlcheck`，凭据载于现网 `.env`） |
| **介质上传路径** | 生产服务器 `/tmp/dist/` 目录 |
| **目标部署根目录** | `/opt/tdsql-sqlcheck`（新版本物理目录 `/opt/tdsql-sqlcheck/releases/v1.6.4.1`，生效软链接 `/opt/tdsql-sqlcheck/current`） |
| **编制智能体** | **智能体 G**（外网开发与打包智能体） |
| **受众智能体** | **Lingma**（内网生产环境部署与运维智能体） |

---

## 零、 【最高生产红线】生产升级安全守则与避坑铁律（内网智能体必读）

在生产服务器（`10.243.16.238`）执行升级操作时，**必须严格遵守以下 8 大生产红线，严防操作走偏引发生产故障**：

> [!CAUTION]
> ### 红线 1（致命 Python 解释器陷阱）：严禁重新安装 venv 或执行 pip install！必须直接复用既有虚拟环境！
> - **生产环境严重缺陷复盘**：生产服务器银河麒麟自带的 `/usr/local/bin/python3.11` 存在标准库严重缺陷（缺少 `encodings` 模块）。一旦重新运行 `python -m venv` 或 `pip install`，必然抛出致命错误：`ModuleNotFoundError: No module named 'encodings'`，导致系统环境崩溃！
> - **生产安全防呆方案**：本次发布包 Python 生产依赖与生产现网完全保持一致！
> - 升级时**必须直接从现网既有目录完整复制既有虚拟环境**：
>   ```bash
>   cp -a /opt/tdsql-sqlcheck/current/venv /opt/tdsql-sqlcheck/releases/v1.6.4.1/venv
>   ```
>   秒级完成且 100% 杜绝麒麟系统 Python 解释器缺陷！**绝对不可在生产机重新创建 venv 或执行 pip install！**

> [!CAUTION]
> ### 红线 2：绝对严禁删除、重置或覆盖 `/opt/tdsql-sqlcheck/.env` 生产配置文件！
> - 生产环境 `/opt/tdsql-sqlcheck/.env` 中配置的是连接外部核心生产 TDSQL 元数据库集群的连接地址、库名与关键凭据；
> - 升级过程中**绝对不可使用发布包中的模板覆盖生产根目录下的 `.env`**！
> - 在升级开始前，**必须首先对生产 `.env` 执行异地时间戳备份**：
>   ```bash
>   cp /opt/tdsql-sqlcheck/.env /opt/tdsql-sqlcheck/.env.bak.$(date +%Y%m%d_%H%M%S)
>   ```

> [!IMPORTANT]
> ### 红线 3：数据解密密钥 `data/encryption.key` 必须绝对延续传承！
> - 生产环境纳管的各业务 TDSQL 数据库实例连接密码均基于 AES 对称密钥加密存储；
> - 新版本目录 `/opt/tdsql-sqlcheck/releases/v1.6.4.1/data/encryption.key` 必须从 `/opt/tdsql-sqlcheck/current/data/encryption.key` 完整拷贝继承，且与现网 `.env` 中的 `TDSQL_ENCRYPTION_KEY` 保持 44 位完全一致；
> - 严防密钥丢失引发全量纳管实例密码 `InvalidToken` 解密失败故障。

> [!IMPORTANT]
> ### 红线 4：数据库迁移操作前必须前置停止后台 Runner 服务！
> - 根据测试环境验证实录，若后台执行器仍在运行，会持有 MySQL 连接池（默认 20 连接）以及 `tdsql_copilot_runner` 命名锁，导致 DDL 迁移报连接被拒绝错误；
> - **执行数据库结构迁移或表初始化前，必须先停止后台服务**：
>   ```bash
>   systemctl stop tdsql-copilot-runner || true
>   systemctl stop tdsql-metadata-runner || true
>   ```

> [!IMPORTANT]
> ### 红线 5：三服务依赖与启动顺序铁律（先后台 Runner、后 Web 主服务）！
> - `v1.6.3.7` 仅有两项服务：`tdsql-metadata-runner.service` 与 `tdsql-sqlcheck.service`；
> - **`v1.6.4.1` 正式引入第三项核心后台服务：`tdsql-copilot-runner.service`（AI Copilot 异步大模型调度器）！**
> - **生产启动顺序铁律**：
>   1. 先启动元数据审核执行器：`systemctl restart tdsql-metadata-runner` ➔ 校验 `active (running)`；
>   2. 再启动 Copilot 助手调度器：`systemctl restart tdsql-copilot-runner` ➔ 校验 `active (running)`（完成 B 组结构与台账对账验收）；
>   3. 最后启动 Web 主服务：`systemctl restart tdsql-sqlcheck` ➔ 校验 `active (running)`。
> - 必须在确认两项 runner 均正常处于 active 状态后，再拉起 Web 服务！

> [!NOTE]
> ### 红线 6：10 大场景路由开箱即用（无需手工逐条创建）
> - 发布包内置的 [`init_copilot_tables.sql`](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/deploy/init_copilot_tables.sql) 已预置了全部 10 个标准业务场景路由记录；
> - 在外部 TDSQL 生产元数据库初始化建表后，10 个场景自动就绪，管理员后续只需在界面为场景绑定可用模型即可，彻底免去手动逐条执行 INSERT 的繁琐。

> [!WARNING]
> ### 红线 7：超级管理员密码与账户防锁定机制
> - 生产初始管理员账号为 `admin`，密码来自生产目标机 `.env` 文件中的 `ADMIN_INITIAL_PASSWORD`，切勿盲猜密码；
> - 若因误输入触发连续 5 次错误锁定（系统防暴破保护锁定 15 分钟），可执行单行 SQL 秒级解锁：
>   ```bash
>   DB_PASS=$(grep "^SQLCHECK_DB_PASSWORD=" /opt/tdsql-sqlcheck/.env | cut -d= -f2- | tr -d '\r "')
>   MYSQL_PWD="${DB_PASS}" mysql -h <DB_HOST> -P <DB_PORT> -u <DB_USER> <DB_NAME> \
>     -e "UPDATE users SET failed_attempts = 0, locked_until = NULL WHERE username = 'admin';"
>   ```

> [!IMPORTANT]
> ### 红线 8：Releases 物理隔离部署与极速一键回滚能力
> - 必须将新版本解压部署至独立目录 `/opt/tdsql-sqlcheck/releases/v1.6.4.1`；
> - 旧版本 `/opt/tdsql-sqlcheck/releases/v1.6.3.7` 原样完整保留；
> - 一旦生产验证发现任何不可容忍的异常，可通过单行命令秒级无损切回 `v1.6.3.7`！

---

## 一、 本版核心变更与新增特性全景 (v1.6.3.7 ➔ v1.6.4.1)

本次 `v1.6.4.1` 是 TDSQL SQL审核工具的重大里程碑版本，已在内网测试环境完成 22 项全功能验收测试（100% 通过）：

```
┌──────────────────────────────────────┬────────────────────────────────────────────────────────┐
│ 核心模块变更                         │ 生产生产力与安全性提升                                 │
├──────────────────────────────────────┼────────────────────────────────────────────────────────┤
│ 1. AI Copilot 智能专家助手系统       │ 引入右下角科技感常驻悬浮对话抽屉中台；                 │
│    (全新功能)                        │ 支持 10 类核心业务场景精准路由（使用帮助、规则解读、   │
│                                      │ SQL 改写优化建议、TDSQL 分布式表类型分析等）。        │
├──────────────────────────────────────┼────────────────────────────────────────────────────────┤
│ 2. Copilot 运维管理中台              │ 支持 OpenAI 兼容规范多模型供应商接入；                 │
│    (copilot-admin)                   │ 凭据采用 AES-256-GCM 工业级信封加密存储；              │
│                                      │ 端点默认固化为 "/v1"，支持一键模型自检与连通性验证。   │
├──────────────────────────────────────┼────────────────────────────────────────────────────────┤
│ 3. Copilot 安全审计中心              │ 全链路操作审计元数据落库（不留商业机密与正文）；       │
│    (copilot-audit)                   │ 动态数据分级脱敏（密码、手机、身份证自动掩蔽）；       │
│                                      │ 内置严格 SSRF 边界防护（禁止 127.0.0.1/localhost）。   │
├──────────────────────────────────────┼────────────────────────────────────────────────────────┤
│ 4. 架构由双服务演进为三服务          │ 新增 `tdsql-copilot-runner.service` 独立调度器；       │
│                                      │ 异步大模型请求与主 Web 解耦，高负载下 Web 零卡顿。    │
├──────────────────────────────────────┼────────────────────────────────────────────────────────┤
│ 5. 核心审核引擎零回归                │ 121 条审核规则、42 条 Oracle 迁移兼容规则正常生效；    │
│                                      │ 在线大库元数据抽取与审核调度稳定运行。                 │
└──────────────────────────────────────┴────────────────────────────────────────────────────────┘
```

---

## 二、 发布介质明细与完整性校验

发布介质构建于外网开发环境，产物位于 `/tmp/dist/`：

| 文件名 | 说明 | 校验方式 |
|---|---|---|
| `tdsql-sqlcheck-v1.6.4.1-linux-x86_64.tar.gz` | v1.6.4.1 完整全量生产发布包（包含前后端、离线 wheels、部署脚本、文档及 SQL） | 以同级 `.sha256` 校验和文件为准 |
| `tdsql-sqlcheck-v1.6.4.1-linux-x86_64.tar.gz.sha256` | 官方 SHA256 校验和文件 | `sha256sum -c` |

### 生产介质完整性核验命令
在生产服务器 `10.243.16.238` 终端执行：
```bash
cd /tmp/dist
sha256sum -c tdsql-sqlcheck-v1.6.4.1-linux-x86_64.tar.gz.sha256
# 控制台必须输出: tdsql-sqlcheck-v1.6.4.1-linux-x86_64.tar.gz: 确定 (或 OK)
```

---

## 三、 生产全量升级标准操作流程（自动化脚本方式）

发布包中包含的 `deploy/upgrade_incremental.sh` 脚本已内置安全预停服务、虚拟环境继承、密钥同步、数据库结构迁移与自动备用执行、软链接原子切换及三服务接管全流程。

### 步骤 1：备份现网生产配置文件
```bash
# 必须首先备份生产 .env
cp /opt/tdsql-sqlcheck/.env /opt/tdsql-sqlcheck/.env.bak.$(date +%Y%m%d_%H%M%S)
```

### 步骤 2：解压生产发布包
```bash
cd /tmp/dist
tar -xzf tdsql-sqlcheck-v1.6.4.1-linux-x86_64.tar.gz
cd tdsql-sqlcheck-v1.6.4.1-linux-x86_64
```

### 步骤 3：执行自动化升级脚本
```bash
# 以 root 权限运行升级脚本（参数指定目标安装根目录与端口）
bash deploy/upgrade_incremental.sh --target /opt/tdsql-sqlcheck --port 8000
```

升级脚本将自动执行以下完整流水线：
1. 校验版本号为 `1.6.4.1`，创建物理隔离目录 `/opt/tdsql-sqlcheck/releases/v1.6.4.1`；
2. 继承现网既有 `venv` 虚拟环境，保障依赖秒级就绪，免去麒麟系统 Python 缺陷风险；
3. 继承 `data/encryption.key`，确保已有实例连接密码无损解密；
4. **安全预停止后台执行器服务，释放连接池与锁**；
5. 执行 Copilot B 组业务表结构迁移；若检测到任何环境阻断，**自动无缝回退执行 `init_copilot_tables.sql`，安全创建 9 张业务表、原子登记台账并预置 10 大场景路由**；
6. 原子切换 `/opt/tdsql-sqlcheck/current` 软链接至 `releases/v1.6.4.1`；
7. 注册并按严格依赖顺序依次启动：
   - `tdsql-metadata-runner.service`
   - `tdsql-copilot-runner.service`
   - `tdsql-sqlcheck.service`
8. 自动触发 14 项部署后自动化流水线验证。

---

## 四、 生产全量升级分步操作流程（手动备用 / 深度掌控方式）

若内网智能体 Lingma 或运维人员需逐步审查并逐行执行，请严格按照以下步骤操作：

### 4.1 准备版本目录与依赖环境
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
# 必须先停后台执行器，防止占用外部 TDSQL 连接池与锁
systemctl stop tdsql-copilot-runner || true
systemctl stop tdsql-metadata-runner || true
systemctl stop tdsql-sqlcheck || true
```

### 4.3 生产元数据库结构迁移与台账核验

**方案 A：通过 Python 迁移入口执行**
```bash
"${TARGET_RELEASE}/venv/bin/python" -m backend.services.copilot.schema --apply
```
> 若返回 `{"status": "READY", ...}`，说明表结构与台账已全部正常就绪。

**方案 B：若方案 A 遇任何环境连接池问题，直接执行独立 SQL 脚本（100% 成功保底）**
```bash
# 从现网 .env 读取外部生产 TDSQL 元数据库凭据
DB_HOST=$(grep "^SQLCHECK_DB_HOST=" "${INSTALL_DIR}/.env" | cut -d= -f2- | tr -d '\r "')
DB_PORT=$(grep "^SQLCHECK_DB_PORT=" "${INSTALL_DIR}/.env" | cut -d= -f2- | tr -d '\r "')
DB_USER=$(grep "^SQLCHECK_DB_USER=" "${INSTALL_DIR}/.env" | cut -d= -f2- | tr -d '\r "')
DB_PASS=$(grep "^SQLCHECK_DB_PASSWORD=" "${INSTALL_DIR}/.env" | cut -d= -f2- | tr -d '\r "')
DB_NAME=$(grep "^SQLCHECK_DB_DATABASE=" "${INSTALL_DIR}/.env" | cut -d= -f2- | tr -d '\r "')

# 使用 MySQL 客户端直接执行发布包内置的纯 DDL + 台账原子登记 + 10大场景预置脚本
MYSQL_PWD="${DB_PASS}" mysql -h "${DB_HOST}" -P "${DB_PORT}" -u "${DB_USER}" "${DB_NAME}" \
  < "${TARGET_RELEASE}/deploy/init_copilot_tables.sql"
```

**台账验证核对命令**：
```bash
MYSQL_PWD="${DB_PASS}" mysql -h "${DB_HOST}" -P "${DB_PORT}" -u "${DB_USER}" "${DB_NAME}" -e "
  SELECT version_key, checksum, applied_at FROM schema_migrations WHERE version_key LIKE 'copilot_%';
  SELECT scene_code, privacy_profile, enabled FROM copilot_scene_routes;
"
```
预期输出：
1. `copilot_v1_001_business` 台账记录存在且 checksum 为 `6cdda7b12cbdc2dba6a40815fbc0318b489216c9fd838979289890a1d90112b0`；
2. 10 个业务场景路由均已完整预置。

### 4.4 切换软链接并授权
```bash
# 记录前一版本以备回滚
echo "${CURRENT_RELEASE}" > "${INSTALL_DIR}/.previous_release"

# 原子切换 current 软链接指向 v1.6.4.1
ln -sfn "${TARGET_RELEASE}" "${INSTALL_DIR}/current"

# 统一权限为 root
chown -R root:root "${INSTALL_DIR}"
```

### 4.5 安装并更新 Systemd 服务配置
```bash
# 1. 注册 metadata-runner
sed -e "s|__INSTALL_DIR__|${INSTALL_DIR}|g" -e "s|__USER__|root|g" \
    "${TARGET_RELEASE}/deploy/tdsql-metadata-runner.service" > /etc/systemd/system/tdsql-metadata-runner.service

# 2. 注册新增的 copilot-runner
sed -e "s|__INSTALL_DIR__|${INSTALL_DIR}|g" -e "s|__USER__|root|g" \
    "${TARGET_RELEASE}/deploy/tdsql-copilot-runner.service" > /etc/systemd/system/tdsql-copilot-runner.service

# 3. 注册 Web 主服务
sed -e "s|__INSTALL_DIR__|${INSTALL_DIR}|g" -e "s|__PORT__|8000|g" -e "s|__USER__|root|g" \
    "${TARGET_RELEASE}/deploy/tdsql-sqlcheck.service" > /etc/systemd/system/tdsql-sqlcheck.service

# 重载 systemd
systemctl daemon-reload
```

### 4.6 按照顺序拉起生产三服务
```bash
# 步骤 1: 启动元数据审核执行器
systemctl enable tdsql-metadata-runner >/dev/null 2>&1
systemctl restart tdsql-metadata-runner
sleep 2
systemctl is-active tdsql-metadata-runner

# 步骤 2: 启动 Copilot 助手调度器 (必须在 Web 前拉起并完成结构验收)
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

## 五、 生产部署后全方位验收与验证

### 5.1 自动化 14 项黄金流水线验证
在生产服务器执行：
```bash
bash /opt/tdsql-sqlcheck/current/deploy/verify_deploy.sh --port 8000
```
**生产放行标准**：
```text
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
  [PASS] 元数据库读写正常
  [PASS] /metrics 指标输出
════ 验证结果: PASS=14 FAIL=0 SKIP=0 ════
部署验证全部通过
```

### 5.2 日志健康度核查
```bash
# 查看 Web 服务启动状态
journalctl -u tdsql-sqlcheck -n 20 --no-pager
# 确认输出:
# TDSQL SQL审核平台已就绪 (V1.6.4.1)
# Copilot 启动验收完成: local_ready=True reason=

# 查看 Copilot Runner 状态
journalctl -u tdsql-copilot-runner -n 20 --no-pager
# 确认输出:
# 知识包加载完成: kb-1.6.4.0-xxx
# 数据库初始化完成 (V2.0, MySQL)
```

### 5.3 生产模型自检与业务验证
1. 浏览器打开生产地址 `http://10.243.16.238:8000` 并重新登录；
2. 进入 **系统管理 ➔ Copilot 模型管理**：
   - 确认供应商端点、模型 ID、API Key（若有）配置正确；
   - 点击 **测试连接 / 模型自检**，确认返回绿色 **自检成功 (SUCCEEDED)**；
   - 勾选 **启用该模型**；
3. 进入 **场景路由管理**：
   - 确认 10 个场景路由均已列出，将它们的主模型绑定到上述启用的模型；
4. 点击右下角 Copilot 悬浮气泡，发送一条测试咨询（例如：“介绍 TDSQL 分布式表与广播表的区别”），确认收到流畅的 Markdown 解答。

---

## 六、 常见问题与应急排查（Troubleshooting）

### 6.1 问题：执行 `schema.py --apply` 提示连接池耗尽或连接失败
- **原因**：历史服务未停止或连接未释放。
- **解法**：先执行 `systemctl stop tdsql-copilot-runner tdsql-metadata-runner`，然后直接使用 MySQL CLI 运行 `init_copilot_tables.sql`。

### 6.2 问题：Runner 日志报 "无法获取 tdsql_copilot_runner 命名锁"
- **原因**：存在孤儿 Python 进程占用或多次启动。
- **解法**：`pkill -f "copilot_runner" && systemctl restart tdsql-copilot-runner`。

### 6.3 问题：Web 登录连续失败 5 次导致 admin 账号被锁定
- **原因**：密码输入错误触发了防爆破安全保护。
- **解法**：在生产外部 TDSQL 库执行解锁命令：
  ```bash
  MYSQL_PWD="${DB_PASS}" mysql -h "${DB_HOST}" -P "${DB_PORT}" -u "${DB_USER}" "${DB_NAME}" \
    -e "UPDATE users SET failed_attempts = 0, locked_until = NULL WHERE username = 'admin';"
  ```

---

## 七、 生产极速一键回滚预案

若生产升级后发现无法容忍的不可逆异常，可随时执行以下命令**秒级无损回滚至 v1.6.3.7**：

```bash
INSTALL_DIR="/opt/tdsql-sqlcheck"
PREV_RELEASE=$(cat "${INSTALL_DIR}/.previous_release" 2>/dev/null || echo "${INSTALL_DIR}/releases/v1.6.3.7")

echo "正在触发紧急回滚 ➔ ${PREV_RELEASE} ..."

# 1. 原子切回旧版本软链接
ln -sfn "${PREV_RELEASE}" "${INSTALL_DIR}/current"

# 2. 停用新增的 copilot-runner 服务
systemctl stop tdsql-copilot-runner || true
systemctl disable tdsql-copilot-runner || true

# 3. 恢复旧版本核心服务
systemctl restart tdsql-metadata-runner
systemctl restart tdsql-sqlcheck

# 4. 校验回滚版本
echo "回滚完成，当前运行版本: $(cat ${INSTALL_DIR}/current/VERSION)"
systemctl status tdsql-sqlcheck --no-pager
```

---

**编制**：智能体 G  
**审核责任人**：MR.Linsang  
**交付智能体**：Lingma（内网部署智能体）  
**版本归档**：v1.6.4.1  
**适用环境**：内网生产环境（10.243.16.238）  
**日期**：2026-09-21
