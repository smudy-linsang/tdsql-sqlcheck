# TDSQL SQL审核工具 v1.6.3.6 内网生产环境增量更新部署手册

| 属性 | 内容 |
|---|---|
| **目标软件版本** | `v1.6.3.6`（大库流式审核、TDSQL物理分片过滤、突破包上限、时区对齐、双服务架构） |
| **生产现网基线** | `v1.6.3.4`（生产环境当前运行版本；本次为从 v1.6.3.4 直接升级至 v1.6.3.6） |
| **升级方式** | **在轨增量平滑升级（In-Place Releases 软链原子切换，直接复用既有 venv，秒级生效）** |
| **发布介质** | `tdsql-sqlcheck-v1.6.3.6-patch.tar.gz`（及其 `.sha256` 校验和文件，体积仅约 1.7MB） |
| **目标生产服务器** | `10.243.16.238`（银河麒麟 Advanced Server V10 SP3，海光 x86_64 CPU） |
| **操作执行端** | 内网 Windows 部署机（已与 `root@10.243.16.238` 建立 SSH 免密信任） |
| **生产元数据库** | **外部核心 TDSQL 集群**（集中式实例，MySQL 协议 3306 端口，数据库 `tdsql_sqlcheck`） |
| **安装包上传目录** | 生产服务器 `/tmp/dist/` 目录 |

---

## 零、 【最高生产红线】生产升级安全守则与经验避坑（内网智能体必读防呆）

在生产服务器（`10.243.16.238`）执行升级操作时，**必须严格遵守以下 7 大生产红线，切勿违规操作**：

> [!CAUTION]
> ### 红线 1：严禁重新安装 venv 依赖或执行 pip install！必须直接复用既有虚拟环境！
> - **生产环境严重缺陷复盘**：生产服务器银河麒麟自带的 `/usr/local/bin/python3.11` 存在环境缺陷（标准库缺少 `encodings` 模块）。一旦重新运行 `python -m venv` 或 `pip install`，必然抛出致命错误：`ModuleNotFoundError: No module named 'encodings'`！
> - **安全防呆方案**：本次从 `v1.6.3.4` 到 `v1.6.3.6` 的生产 Python 依赖清单（`requirements.txt`）**完全零变更**！
> - 增量部署脚本 `upgrade_incremental.sh` 会自动从现网既有目录 `/opt/tdsql-sqlcheck/releases/v1.6.3.4/venv`（或 `current/venv`）完整拷贝继承虚拟环境，秒级完成且 100% 杜绝 Python 运行时灾难！**绝对不可在生产机重新创建 venv 或执行 pip install！**

> [!CAUTION]
> ### 红线 2：绝对严禁删除、重置或覆盖 `/opt/tdsql-sqlcheck/.env` 生产配置文件！
> - 生产环境 `/opt/tdsql-sqlcheck/.env` 中配置的是连接外部核心生产 TDSQL 元数据库集群的凭据与高阶运行参数；
> - 增量升级必须直接沿用现网 `.env`，**绝不可覆盖或破坏原有配置**！升级开始前必须先执行异地备份。

> [!IMPORTANT]
> ### 红线 3：加密密钥 `encryption.key` 绝对延续传承！
> - 生产环境纳管的数十个核心业务 TDSQL 数据库实例连接密码均基于 AES 对称密钥加密存储；
> - 必须确保新 Release 目录 `/opt/tdsql-sqlcheck/releases/v1.6.3.6/data/encryption.key` 与现网 `.env` 中的 `TDSQL_ENCRYPTION_KEY` 保持 44 位完全一致；
> - 增量升级脚本会自动从旧版本继承该文件，确保已有实例连接密码解密正常，绝不发生 `InvalidToken` 全网失联故障。

> [!IMPORTANT]
> ### 红线 4：严格遵循 Releases 物理隔离与软链接原子切换！
> - 必须将新版本独立部署至 `/opt/tdsql-sqlcheck/releases/v1.6.3.6`；
> - 增量文件写入并校验无误后，通过 `ln -sfn /opt/tdsql-sqlcheck/releases/v1.6.3.6 /opt/tdsql-sqlcheck/current` 实现原子切换；
> - 既有版本 `/opt/tdsql-sqlcheck/releases/v1.6.3.4` 必须原样完整保留，支持发生任何非预期异常时通过单行命令**秒级无损回滚**！

> [!IMPORTANT]
> ### 红线 5：【核心架构升级】首次安装并启动独立执行器 `tdsql-metadata-runner.service`！
> - **生产环境现网（v1.6.3.4）仅有单个 Web 服务**（`tdsql-sqlcheck.service`）；
> - **升级至 v1.6.3.6 后，架构升级为双服务协同**：新增后台长任务执行器守护进程 `tdsql-metadata-runner.service`；
> - **启动顺序铁律**：增量升级脚本会自动配置 `/etc/systemd/system/tdsql-metadata-runner.service`，并**先启动并校验 `tdsql-metadata-runner` 处于 active (running) 状态后，再重启 `tdsql-sqlcheck` Web 服务**；若 runner 启动失败，升级脚本强制报错退出，严防服务不同步。

> [!NOTE]
> ### 红线 6：外部生产 TDSQL 元数据库增量迁移（150号脚本）全自动执行！
> - 本次升级涉及外部生产元数据库新增 `metadata_audit_jobs`（任务明细表）与 `metadata_audit_slot`（独占受理槽表）；
> - 升级后服务启动时，内置的 `SchemaMigrator` 会自动发现并安全执行 `backend/schema/v15/150_metadata_audit_jobs.sql`；
> - 该迁移全部为全新建表，不修改任何既有业务表，**绝不锁表、绝不影响既有数据**，无需运维手动敲 DDL。

> [!NOTE]
> ### 红线 7：单机部署无 Nginx 架构说明
> - 经现场确认，内网环境为单机服务器直接部署模式（未使用 Nginx 反向代理），由 Uvicorn 直接监听 `8000` 端口对外提供服务；
> - 在轨升级过程中，旧版 Web 服务仍在监听 8000 端口属于正常现象，脚本在原子切换软链后通过 `systemctl restart tdsql-sqlcheck` 平滑接管端口。

---

## 一、 版本核心变更全景（跨版本：v1.6.3.4 ➔ v1.6.3.6）

内网生产环境当前运行版本为 **`v1.6.3.4`**。本次直接升级至 **`v1.6.3.6`**，包含了 v1.6.3.5 与 v1.6.3.6 的全部重大升级与缺陷修复：

### 1.1 彻底根除 6000+ 表在线元数据审核 Worker 内存暴涨猝死（DU-1）
- **原生产故障**：在大库（如 6000+ 表）在线元数据审核时，耗时约 250 秒后页面弹出 `提取失败: Failed to fetch`，底层报 `net::ERR_EMPTY_RESPONSE`，Web 进程因内存暴涨（>3.8GB）猝死。
- **根本解决**：
  1. 根除了全量 AST 驻留与 $O(N^2)$ 字典浅拷贝膨胀；
  2. 实现**有界见证索引**（`R035PriorIndex`），每个列名最多维护 5 个轻量标量槽位，空间复杂度由 $O(N^2)$ 骤降至 $O(U)$；
  3. `audit_file` 恢复为**流式生成器**，逐句解析并即时释放 AST；
  4. **测试环境实测验证**：6097 表大库运行全程物理内存峰值稳定在 **100MB ~ 300MB**，彻底消除内存击穿与 Worker 猝死。

### 1.2 架构全面解耦：独立 Runner 执行器与任务状态机（DU-2）
- **双服务架构**：将重型提取与规则审核彻底移至独立守护进程 `tdsql-metadata-runner`，Web 服务不再发生阻塞；
- **单机并发保护**：新增 `metadata_audit_slot` 表，同一时刻只允许 1 个重任务执行，并发请求返回 `409 METADATA_BUSY` 友好排队提示；
- **旧接口安全退役**：旧同步阻塞接口 `/api/v1/audit/extract-and-audit` 彻底退役并返回 `410 Gone`。

### 1.3 前端交互与断线自愈增强（DU-3）
- **进度实时感知**：前端每 2 秒短轮询，动态呈现当前枚举、提取、审核进度与阶段；
- **断线自愈**：用户刷新页面或网络闪断后，提供独立「📄 恢复查看上次任务」入口，只读找回任务视图，不重复发起任务；
- **友好取消**：支持在运行态确认并取消任务，Runner 安全释放独占槽位。

### 1.4 【BUG-01】TDSQL 二级分区底层物理分片表过滤与单表容错（v1.6.3.6 重点）
- **原缺陷**：在包含 TDSQL 物理分片表（如 `_tdsql_subp...`）的分布式库（如 `lzbj_ecif`）中，Proxy 拒绝对物理子分片执行 `SHOW CREATE TABLE`，导致任务在第 0 秒报错崩溃。
- **修复**：增加物理分片正则过滤器自动跳过内部子表（主表 DDL 已包含全部定义）；提取循环增加单表容错存证机制，单表偶发读取异常捕获后记入跳过并注入 SQL 注释，不再杀全库任务。
- **内网测试实测**：`15005-lzbj_ecif` 库 298 个对象仅用 6 秒顺利审核成功（`SUCCEEDED`），彻底消除崩溃。

### 1.5 【BUG-02】超大库（6000+表）审核结果突破 `max_allowed_packet` 限制（v1.6.3.6 重点）
- **原缺陷**：6097 表大库跑完审核后，在持久化阶段因虚假翻倍计算被自身代码拦截抛错 `超过元数据库 max_allowed_packet` 导致任务变 `FAILED`。
- **修复**：废除虚假翻倍计算；引入双层存储体系（超过 30MB 自动紧凑精简冗余 SQL 字段，保留产物指针与完整违规明细），确保顺利持久化。
- **内网测试实测**：`15063-sungl_busi`（6097 表）历时 344 秒全流程顺畅闭环，成功生成完整报告。

### 1.6 【FIXREQ-01 / D-04】任务终态收敛缺口与前端活动状态判定整改
- **原缺陷**：极端异常残留任务导致前端进入“双按钮同时禁用且锁死”的状态。
- **修复**：接口暴露真实槽位归属与心跳超时判定，前端根据槽位精准解耦，确保护盾按钮随时可用，彻底消除死锁。

### 1.7 【BUG-03】历史元数据审核记录落库时区对齐与当天日期精准筛选
- **原缺陷**：入库写入 UTC 时间导致比北京时间慢 8 小时（落入昨天），按“今天”筛选返回 0 条记录，不筛选时显示昨天时间误导用户。
- **修复**：发布落库统一使用本地时间（北京时间），列表查询增加 `ORDER BY h.created_at DESC, h.id DESC` 稳定排序，前端表格新增显式 **`报告ID`**（`#ID`）列。

---

## 二、 生产外部 TDSQL 元数据库增量迁移说明

生产环境 `10.243.16.238` 的元数据存储在**外部核心 TDSQL 集中式数据库集群**中。

### 2.1 自动迁移机制（零人工 DDL 负担）
后台服务启动时，内置的 Schema 自动迁移引擎会按序执行 `backend/schema/v15/150_metadata_audit_jobs.sql`，在外部 TDSQL 库中创建：
1. `metadata_audit_jobs`：元数据审核任务状态表；
2. `metadata_audit_slot`：单机独占受理槽表（默认写入 `id=1, accepting=1` 初始行）。

两张表均为全新增量表，**不修改、不触碰任何既有生产业务表，零锁表风险，零数据影响**。

### 2.2 DBA 只读对账 SQL（推荐在变更完成后核验一次）
DBA 可登录外部生产 TDSQL 元数据库执行以下只读核验 SQL：

```sql
USE `tdsql_sqlcheck`;

-- 1. 核对 150 号元数据任务迁移是否成功登记
SELECT version_key, checksum, applied_at 
FROM schema_migrations 
WHERE version_key LIKE '%150%';

-- 2. 验证任务明细表结构
DESC metadata_audit_jobs;

-- 3. 验证独占受理槽表（初始状态应当为 accepting=1, active_job_id 为 NULL）
SELECT * FROM metadata_audit_slot WHERE id = 1;
```

---

## 三、 生产增量升级标准操作规程（SOP）

本操作均在**内网 Windows 部署机**通过 PowerShell 执行（已与生产机 `root@10.243.16.238` 建立 SSH 免密通道）：

### 步骤 1：生产关键资产异地安全备份（红线必做）
在升级前，将生产现网的关键配置文件与加密密钥备份至独立的备份目录：
```powershell
ssh root@10.243.16.238 "bash -s" << 'EOF'
set -e
BACKUP_TS=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="/opt/tdsql-sqlcheck/backups/backup_v1634_${BACKUP_TS}"
mkdir -p "${BACKUP_DIR}"

echo "=== [1/2] 备份生产 .env 与 encryption.key ==="
cp -p /opt/tdsql-sqlcheck/.env "${BACKUP_DIR}/.env.bak"
if [ -f /opt/tdsql-sqlcheck/current/data/encryption.key ]; then
  cp -p /opt/tdsql-sqlcheck/current/data/encryption.key "${BACKUP_DIR}/encryption.key.bak"
fi

echo "=== [2/2] 记录当前运行版本与服务状态 ==="
cat /opt/tdsql-sqlcheck/current/VERSION > "${BACKUP_DIR}/VERSION.bak"
systemctl status tdsql-sqlcheck --no-pager > "${BACKUP_DIR}/service_status.txt" 2>&1 || true

echo "生产现网配置备份完成，备份目录: ${BACKUP_DIR}"
ls -lh "${BACKUP_DIR}"
EOF
```

---

### 步骤 2：上传增量补丁包至生产服务器并校验哈希
```powershell
# 1. 进入本地补丁发布目录
cd c:\TDSQL_SQLCHECK\TDSQL-SQLCheck\dist

# 2. 上传补丁包与校验和文件至生产服务器 /tmp/dist/
ssh root@10.243.16.238 "mkdir -p /tmp/dist"
scp tdsql-sqlcheck-v1.6.3.6-patch.tar.gz root@10.243.16.238:/tmp/dist/
scp tdsql-sqlcheck-v1.6.3.6-patch.tar.gz.sha256 root@10.243.16.238:/tmp/dist/

# 3. 校验 SHA256 哈希值（严格防范网络传输损坏）
ssh root@10.243.16.238 "cd /tmp/dist && sha256sum -c tdsql-sqlcheck-v1.6.3.6-patch.tar.gz.sha256"
```
*预期输出*：`tdsql-sqlcheck-v1.6.3.6-patch.tar.gz: OK`。

---

### 步骤 3：解压补丁并执行增量平滑升级脚本
```powershell
ssh root@10.243.16.238 "bash -s" << 'EOF'
set -e

echo "=== [1/3] 解压 v1.6.3.6 补丁包 ==="
rm -rf /tmp/dist/tdsql-sqlcheck-v1.6.3.6-patch
mkdir -p /tmp/dist/tdsql-sqlcheck-v1.6.3.6-patch
tar -zxf /tmp/dist/tdsql-sqlcheck-v1.6.3.6-patch.tar.gz -C /tmp/dist/tdsql-sqlcheck-v1.6.3.6-patch

echo "=== [2/3] 执行增量平滑升级脚本 ==="
chmod +x /tmp/dist/tdsql-sqlcheck-v1.6.3.6-patch/deploy/*.sh
bash /tmp/dist/tdsql-sqlcheck-v1.6.3.6-patch/deploy/upgrade_incremental.sh /opt/tdsql-sqlcheck 8000

echo "=== [3/3] 增量升级执行完成 ==="
EOF
```

> [!TIP]
> **升级脚本内部执行逻辑说明**：
> 1. 创建新 release 目录 `/opt/tdsql-sqlcheck/releases/v1.6.3.6`；
> 2. 复制最新代码，并直接从 `releases/v1.6.3.4/venv` 拷贝继承 Python 虚拟环境；
> 3. 从现网继承 `encryption.key` 密钥，保留根目录 `.env`；
> 4. 将 `current` 软链接原子指向 `releases/v1.6.3.6`；
> 5. 自动配置并安装 `/etc/systemd/system/tdsql-metadata-runner.service`；
> 6. **先启动/重启 Runner 执行器** 并校验状态为 active (running)；
> 7. **再重启 Web 服务** `tdsql-sqlcheck` 接管端口；
> 8. 自动调用 `verify_deploy.sh` 执行契约检验。

---

### 步骤 4：验证生产双服务运行状态
```powershell
ssh root@10.243.16.238 "systemctl status tdsql-metadata-runner.service tdsql-sqlcheck.service --no-pager -l"
```
*预期输出*：
- `tdsql-metadata-runner.service`: `Active: active (running)`
- `tdsql-sqlcheck.service`: `Active: active (running)`

---

### 步骤 5：执行自动化部署契约验证
```powershell
ssh root@10.243.16.238 "bash /opt/tdsql-sqlcheck/current/deploy/verify_deploy.sh --port 8000"
```
*预期输出*：所有检查项均为 `[PASS]`，输出 `部署验证全部通过`。

---

### 步骤 6：生产只读功能快速冒烟（1 分钟核对）
1. 浏览器打开生产系统：`http://10.243.16.238:8000` 并登录；
2. 检查右上角/系统管理信息，版本号显示为 **`1.6.3.6`**；
3. 进入「平台治理」 $\to$ 「审核规则库」，确认规则总数为 **`121`** 条；
4. 进入「实例管理」，确认存量实例列表展示正常，连接密码解密无报错；
5. 进入「SQL审核」 $\to$ 「在线元数据审核」，确认页面加载正常，双按钮就绪。

---

## 四、 生产秒级无损回滚方案（灾备预案）

若生产环境升级后发生任何未预期的异常情况，可通过以下单行命令实现**秒级无损回退至升级前版本 `v1.6.3.4`**：

```bash
ssh root@10.243.16.238 "bash -s" << 'EOF'
set -e
echo "=== 开始执行生产紧急回滚 ==="

# 1. 停止并禁用 v1.6.3.6 新增的 runner 服务
systemctl stop tdsql-metadata-runner || true
systemctl disable tdsql-metadata-runner || true

# 2. 将 current 软链接原子切回 v1.6.3.4
ln -sfn /opt/tdsql-sqlcheck/releases/v1.6.3.4 /opt/tdsql-sqlcheck/current

# 3. 重启 Web 服务
systemctl restart tdsql-sqlcheck

# 4. 验证回滚结果
echo "当前运行版本: $(cat /opt/tdsql-sqlcheck/current/VERSION)"
systemctl status tdsql-sqlcheck --no-pager
echo "=== 生产紧急回滚完成 ==="
EOF
```

回滚耗时仅需约 3 秒，业务几乎零感知。
