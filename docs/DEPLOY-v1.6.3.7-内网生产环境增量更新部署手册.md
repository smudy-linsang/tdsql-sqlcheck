# TDSQL SQL审核工具 v1.6.3.7 内网生产环境增量更新部署手册

| 属性 | 内容 |
|---|---|
| **目标软件版本** | `v1.6.3.7`（在线元数据提取SQL文件命名规则还原、时基对齐精准筛选、防重排序增强） |
| **生产现网基线** | `v1.6.3.6`（生产环境当前运行版本；本次为从 v1.6.3.6 平滑升级至 v1.6.3.7） |
| **升级方式** | **在轨增量平滑升级（In-Place Releases 软链原子切换，直接复用既有 venv，秒级生效）** |
| **发布介质** | `tdsql-sqlcheck-v1.6.3.7-patch.tar.gz`（及其 `.sha256` 校验和文件，体积约 1.7MB） |
| **目标生产服务器** | `10.243.16.238`（银河麒麟 Advanced Server V10 SP3，海光 x86_64 CPU） |
| **操作执行端** | 内网 Windows 部署机（已与 `root@10.243.16.238` 建立 SSH 免密信任） |
| **生产元数据库** | **外部核心 TDSQL 集群**（集中式实例，MySQL 协议 3306 端口，数据库 `tdsql_sqlcheck`） |
| **安装包上传目录** | 生产服务器 `/tmp/dist/` 目录 |

---

## 零、 【最高生产红线】生产升级安全守则与经验避坑（内网智能体必读防呆）

在生产服务器（`10.243.16.238`）执行升级操作时，**必须严格遵守以下 8 大生产红线，切勿违规操作**：

> [!CAUTION]
> ### 红线 1：严禁重新安装 venv 依赖或执行 pip install！必须直接复用既有虚拟环境！
> - **生产环境严重缺陷复盘**：生产服务器银河麒麟自带的 `/usr/local/bin/python3.11` 存在环境缺陷（标准库缺少 `encodings` 模块）。一旦重新运行 `python -m venv` 或 `pip install`，必然抛出致命错误：`ModuleNotFoundError: No module named 'encodings'`！
> - **安全防呆方案**：本次从 `v1.6.3.6` 到 `v1.6.3.7` 的生产 Python 依赖清单（`requirements.txt`）**完全零变更**！
> - 增量部署脚本 `upgrade_incremental.sh` 会自动从现网既有目录 `/opt/tdsql-sqlcheck/releases/v1.6.3.6/venv`（或 `current/venv`）完整拷贝继承虚拟环境，秒级完成且 100% 杜绝 Python 运行时灾难！**绝对不可在生产机重新创建 venv 或执行 pip install！**

> [!CAUTION]
> ### 红线 2：绝对严禁删除、重置或覆盖 `/opt/tdsql-sqlcheck/.env` 生产配置文件！
> - 生产环境 `/opt/tdsql-sqlcheck/.env` 中配置的是连接外部核心生产 TDSQL 元数据库集群的凭据与关键生产参数；
> - 增量升级必须直接沿用现网 `.env`，**绝不可覆盖或破坏原有配置**！升级开始前必须先执行异地备份。

> [!IMPORTANT]
> ### 红线 3：加密密钥 `encryption.key` 绝对延续传承！
> - 生产环境纳管的各业务 TDSQL 数据库实例连接密码均基于 AES 对称密钥加密存储；
> - 必须确保新 Release 目录 `/opt/tdsql-sqlcheck/releases/v1.6.3.7/data/encryption.key` 与现网 `.env` 中的 `TDSQL_ENCRYPTION_KEY` 保持 44 位完全一致；
> - 增量升级脚本会自动从旧版本继承该文件，确保已有实例连接密码解密正常，绝不发生 `InvalidToken` 故障。

> [!IMPORTANT]
> ### 红线 4：严格遵循 Releases 物理隔离与软链接原子切换！
> - 必须将新版本独立部署至 `/opt/tdsql-sqlcheck/releases/v1.6.3.7`；
> - 增量文件写入并校验无误后，通过 `ln -sfn /opt/tdsql-sqlcheck/releases/v1.6.3.7 /opt/tdsql-sqlcheck/current` 实现原子切换；
> - 既有版本 `/opt/tdsql-sqlcheck/releases/v1.6.3.6` 必须原样完整保留，支持发生任何非预期异常时通过单行命令**秒级无损回滚**！

> [!IMPORTANT]
> ### 红线 5：必须严格遵循“先 Runner、后 Web”启动契约！
> - 系统包含独立后台执行器服务 `tdsql-metadata-runner.service` 与 Web 服务 `tdsql-sqlcheck.service`；
> - **启动顺序铁律**：增量升级脚本会**先重启并校验 `tdsql-metadata-runner` 处于 active (running) 状态后，再重启 `tdsql-sqlcheck` Web 服务**；若 runner 启动失败，升级脚本强制报错退出，严防服务不同步。

> [!NOTE]
> ### 红线 6：外部生产 TDSQL 元数据库结构完全兼容，零 DDL 变更！
> - 本次升级不涉及任何数据库表结构变更，无需在外部 TDSQL 元数据库执行任何 DDL 迁移语句，零锁表风险，零数据影响。

> [!CAUTION]
> ### 红线 7：【生产绝对禁令】严禁在生产元数据库执行任何存量历史数据订正 SQL 脚本！
> - **架构决策与裁决依据**：在 v1.6.3.7 研发阶段交付的存量历史数据订正脚本 `deploy/patch_v1637_source.sql`，经独立智能体 D 在 SIT 与 UAT 阶段深度实跑复测，指出了其在生产历史数据订正中的时间格式占位符与非幂等重复漂移缺陷，判定为“生产存量订正脚本不可执行”；项目主管 Mr.Linsang 已正式审批同意该观点。
> - **生产执行禁令**：本次生产升级为**纯应用代码与前端静态资源增量平滑升级**，**绝对不要、严禁在生产外部 TDSQL 元数据库上执行任何数据订正 SQL 脚本**！
> - **生产业务预期**：生产现网在 v1.6.3.6 运行期间留存的历史记录保持原状（只读档案）；自 v1.6.3.7 升级生效那一刻起，**所有新发起的在线元数据提取与审核任务，产出的 SQL 文件名 100% 严格遵循经典规范，时间全面对齐北京时间，按天筛选与下载体验完全闭环**！

> [!NOTE]
> ### 红线 8：单机部署无 Nginx 架构说明
> - 生产环境为单机服务器直接部署模式（未启用 Nginx 反向代理），由 Uvicorn 直接监听 `8000` 端口对外提供服务；
> - 在轨升级过程中，旧版 Web 服务仍在监听 8000 端口属于正常现象，脚本在原子切换软链后通过 `systemctl restart tdsql-sqlcheck` 平滑接管端口。

---

## 一、 本版核心变更全景 (v1.6.3.6 ➔ v1.6.3.7)

内网生产环境当前运行版本为 **`v1.6.3.6`**。本次升级为 **v1.6.3.7 本体整改**（不称补丁，不递增小版本），重点解决以下用户反馈的核心问题：

### 1.1 在线元数据提取生成的 SQL 文件命名规则彻底还原
- **原生产问题**：在生产升级到 v1.6.3.6 后，用户对数据库执行扫描审核时，在“历史元数据审核记录”中提取生成的 SQL 文件名格式变成了 `extracted_{db}_{job_id[:8]}.sql`（例如 `extracted_lzbj_ecif_a27123e1.sql`），篡改了原有的业务提取时间戳命名规则，给生产归档、核对及报告追溯造成困扰。
- **根本解决**：彻底去除 `job_id[:8]` 哈希后缀，严格还原为经典标准命名规范：
  $$\mathbf{extracted\_\{db\_name\}\_\{YYYYMMDD\_HHMMSS\}.sql}$$
  例如：`extracted_lzbj_ecif_20260912_161741.sql`。

### 1.2 时基全面对齐本地时间（消除列表早 8 小时错位）
- **原生产问题**：Worker 在将元数据审核记录持久化落库时，部分时间字段写入了 UTC 时间，导致在生产“历史元数据审核记录”列表中展示的提取时间比实际本地操作时间提前了 8 小时（如 16:00 提取的记录显示为 08:00），且在生产页面按“开始日期 = 今天”无法筛出当天记录。
- **根本解决**：
  1. Worker 内部生成的文件名时间戳、`audit_history.created_at`、快照 `scan_snapshots.scan_started_at` 以及 HTML 报告头部的生成时间全面对齐为**系统本地时间（北京时间）**；
  2. 彻底解决时间倒错，在生产页面选择“今天”能 100% 精准筛选出当天全部操作记录。

### 1.3 列表稳定排序与前端新增显式「报告ID」列
- **原生产问题**：同秒内多次操作时记录顺序容易跳变，且界面上缺少唯一编号供口头沟通与问题定位。
- **根本解决**：
  1. 后端历史记录查询增加 `ORDER BY h.created_at DESC, h.id DESC` 稳定排序二级键；
  2. 前端“历史元数据审核记录”表格新增显式 **`#报告ID`**（`#{{ row.id }}`）列，生产运维与用户可基于 `#ID`（如 `#1386`）绝对定位任何一条历史记录。

### 1.4 文件下载与列表行名逐字一致性闭环
- **修复效果**：点击历史记录操作栏的「下载 .sql」，服务端返回的 HTTP 响应头 `Content-Disposition: attachment; filename=extracted_...` 与表格中的文件名**逐字完全一致**；点击「下载 HTML 报告」返回 `Extracted_Schema_Report_{id}.html`。

### 1.5 代码健壮性加固与全量自动化回归
- 清理无用死代码 `_utcnow()`；
- 新增 L1~L6 六道回归防护锁，并经真实变异测试（杀死 UTC 与 UUID 变异）自证有效；
- 经智能体 D 独立复测，全量回归套件 **2146 passed / 0 failed / 30 skipped** 全部通过，UAT 真实浏览器 4 个业务场景 100% 准出；
- 内网测试环境已经过 Mr.Linsang 人工验证核准通过。

---

## 二、 生产增量升级标准操作规程（SOP）

本操作均在**内网 Windows 部署机**通过 PowerShell 执行（已与生产机 `root@10.243.16.238` 建立 SSH 免密通道）：

### 步骤 1：生产关键资产异地安全备份（红线必做）
在升级开始前，必须将生产现网的关键配置文件与加密密钥备份至独立的备份目录：
```powershell
ssh root@10.243.16.238 "bash -s" << 'EOF'
set -e
BACKUP_TS=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="/opt/tdsql-sqlcheck/backups/backup_v1636_${BACKUP_TS}"
mkdir -p "${BACKUP_DIR}"

echo "=== [1/2] 备份生产 .env 与 encryption.key ==="
cp -p /opt/tdsql-sqlcheck/.env "${BACKUP_DIR}/.env.bak"
if [ -f /opt/tdsql-sqlcheck/current/data/encryption.key ]; then
  cp -p /opt/tdsql-sqlcheck/current/data/encryption.key "${BACKUP_DIR}/encryption.key.bak"
fi

echo "=== [2/2] 记录当前运行版本与服务状态 ==="
cat /opt/tdsql-sqlcheck/current/VERSION > "${BACKUP_DIR}/VERSION.bak"
systemctl status tdsql-metadata-runner tdsql-sqlcheck --no-pager > "${BACKUP_DIR}/service_status.txt" 2>&1 || true

echo "生产现网配置备份完成，备份目录: ${BACKUP_DIR}"
ls -lh "${BACKUP_DIR}"
EOF
```

---

### 步骤 2：上传增量补丁包至生产服务器并校验哈希
在内网 Windows 部署机执行：
```powershell
# 1. 进入本地补丁发布目录
cd c:\TDSQL_SQLCHECK\TDSQL-SQLCheck\dist

# 2. 上传补丁包与校验和文件至生产服务器 /tmp/dist/
ssh root@10.243.16.238 "mkdir -p /tmp/dist"
scp tdsql-sqlcheck-v1.6.3.7-patch.tar.gz root@10.243.16.238:/tmp/dist/
scp tdsql-sqlcheck-v1.6.3.7-patch.tar.gz.sha256 root@10.243.16.238:/tmp/dist/

# 3. 校验 SHA256 哈希值（严格防范网络传输损坏）
ssh root@10.243.16.238 "cd /tmp/dist && sha256sum -c tdsql-sqlcheck-v1.6.3.7-patch.tar.gz.sha256"
```
*预期输出*：`tdsql-sqlcheck-v1.6.3.7-patch.tar.gz: OK`。
> [!CAUTION]
> 如果校验输出不是 `OK`，说明传输包损坏，严禁继续执行部署！

---

### 步骤 3：解压补丁并执行增量平滑升级脚本
```powershell
ssh root@10.243.16.238 "bash -s" << 'EOF'
set -e

echo "=== [1/3] 解压 v1.6.3.7 补丁包 ==="
rm -rf /tmp/dist/tdsql-sqlcheck-v1.6.3.7-patch
mkdir -p /tmp/dist/tdsql-sqlcheck-v1.6.3.7-patch
tar -zxf /tmp/dist/tdsql-sqlcheck-v1.6.3.7-patch.tar.gz -C /tmp/dist/tdsql-sqlcheck-v1.6.3.7-patch

echo "=== [2/3] 执行增量平滑升级脚本 ==="
chmod +x /tmp/dist/tdsql-sqlcheck-v1.6.3.7-patch/deploy/*.sh
bash /tmp/dist/tdsql-sqlcheck-v1.6.3.7-patch/deploy/upgrade_incremental.sh /opt/tdsql-sqlcheck 8000

echo "=== [3/3] 增量升级执行完成 ==="
EOF
```

> [!TIP]
> **升级脚本内部执行逻辑保障**：
> 1. 创建独立目录 `/opt/tdsql-sqlcheck/releases/v1.6.3.7`；
> 2. 复制最新代码，并直接从 `releases/v1.6.3.6/venv` 拷贝继承 Python 虚拟环境（**零环境破坏风险**）；
> 3. 从现网继承 `encryption.key` 密钥，保持根目录 `.env`；
> 4. 将 `current` 软链接原子指向 `releases/v1.6.3.7`；
> 5. **先重启 Runner 执行器** 并校验状态为 `active (running)`；
> 6. **后重启 Web 服务** `tdsql-sqlcheck` 平滑接管端口；
> 7. 自动调用 `verify_deploy.sh` 执行 12 项契约检验。

---

### 步骤 4：验证生产双服务运行状态
```powershell
ssh root@10.243.16.238 "systemctl status tdsql-metadata-runner.service tdsql-sqlcheck.service --no-pager -l"
```
*预期输出*：
- `tdsql-metadata-runner.service`: `Active: active (running)`
- `tdsql-sqlcheck.service`: `Active: active (running)`

---

### 步骤 5：执行自动化部署契约验证门禁
```powershell
ssh root@10.243.16.238 "bash /opt/tdsql-sqlcheck/current/deploy/verify_deploy.sh --port 8000"
```
*预期输出*：
```text
════ 验证结果: PASS=12 FAIL=0 SKIP=0 ════
部署验证全部通过
```

---

### 步骤 6：生产业务快速冒烟验证（2 分钟核对）

请内网智能体在浏览器（建议使用无痕模式，或 `Ctrl+F5` 强制刷新）中打开生产系统：
`http://10.243.16.238:8000/`，登录后进行核验：

1. **版本标识核验**：
   - 查看页面标题/页脚，确认版本号显示为 **`V1.6.3.7`**；
2. **实例与规则核验**：
   - 进入「审核规则库」，确认规则总数保持为 **`121`** 条；
   - 进入「实例管理」，确认存量实例展示正常，解密无报错；
3. **在线元数据审核功能核验**：
   - 进入「在线元数据审核」页面，选择实例与数据库，点击「拉取元数据并执行文件审核」；
   - 确认任务顺利执行并成功收敛为 **`SUCCEEDED`**；
   - 向下滚动查看「历史元数据审核记录」：
     - 确认表头包含 **`#报告ID`**（如 `#1388`）；
     - 确认新产出的文件名严格遵循 **`extracted_{库名}_{YYYYMMDD_HHMMSS}.sql`** 规范（如 `extracted_lzbj_ecif_20260912_170015.sql`，绝无 8 位哈希后缀）；
     - 确认「提取时间」显示为**当前系统本地时间**（北京时间），绝不提前 8 小时；
   - 点击「下载 .sql」，确认浏览器下载的文件名与表格中显示完全一致；
   - 在「开始日期」选择**今天**查询，确认当次任务记录准确被筛出。

---

## 三、 生产秒级无损应急回滚方案（灾备预案）

若生产环境升级后发生任何未预期的异常情况，可通过以下命令实现**秒级无损回退至升级前版本 `v1.6.3.6`**：

```powershell
ssh root@10.243.16.238 "bash -s" << 'EOF'
set -e
echo "=== 开始执行生产紧急回滚 ==="

# 1. 将 current 软链接原子切回 v1.6.3.6
ln -sfn /opt/tdsql-sqlcheck/releases/v1.6.3.6 /opt/tdsql-sqlcheck/current

# 2. 先重启后台 runner 执行器
systemctl restart tdsql-metadata-runner

# 3. 后重启 Web 服务
systemctl restart tdsql-sqlcheck

# 4. 验证回滚结果
echo "当前运行版本: $(cat /opt/tdsql-sqlcheck/current/VERSION)"
systemctl status tdsql-metadata-runner tdsql-sqlcheck --no-pager
echo "=== 生产紧急回滚完成 ==="
EOF
```

回滚耗时仅需约 3 秒，业务几乎零感知。

---

**手册编制**：Antigravity 交付团队  
**基线状态**：经 SIT 复测（4/4 变异击穿）、UAT 真实浏览器全链路准出、测试环境人工实测准出  
**日期**：2026-09-12
