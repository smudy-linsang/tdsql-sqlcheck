# TDSQL SQL审核工具 v1.6.3.5 内网测试环境增量更新部署手册

| 属性 | 内容 |
|---|---|
| **目标软件版本** | `v1.6.3.5`（大库在线元数据审核稳定性修复、R035有界见证索引、独立Runner执行器、断线自愈） |
| **基线源版本** | `v1.6.3.4`（当前内网测试环境运行版本） |
| **升级方式** | **增量更新（In-Place / Releases 增量平滑升级，直接复用既有虚拟环境）** |
| **发布介质** | `tdsql-sqlcheck-v1.6.3.5-patch.tar.gz`（及其 `.sha256` 校验和文件） |
| **目标测试服务器** | `10.243.16.252`（银河麒麟 Advanced Server V10 SP3，海光 x86_64 CPU） |
| **操作执行端** | 内网 Windows 部署机（已与 `root@10.243.16.252` 建立 SSH 免密信任） |
| **测试元数据库** | `10.243.16.252` 本地 MySQL 8.0.28（端口 `3306`，数据库 `tdsql_sqlcheck`） |
| **安装包上传目录** | 测试服务器 `/tmp/dist/` 目录 |

---

## 零、 针对本次升级与历史踩坑的“9项铁律”（内网智能体必读防呆）

本次增量部署方案全面继承历史部署经验，并针对 v1.6.3.5 新增的**独立执行器（`tdsql-metadata-runner`）与任务表结构**做出了严密的防呆设计。**内网智能体在执行部署时，务必严格遵守以下 9 项铁律，切勿违规操作**：

> [!CAUTION]
> ### 铁律 1：严禁重新安装 venv 依赖或执行 pip install！必须直接复用既有虚拟环境！
> - **历史血泪教训**：银河麒麟系统自带的 `/usr/local/bin/python3.11` 存在环境缺陷（标准库缺失 `encodings` 模块）。一旦重新运行 `python -m venv` 或 `pip install`，会抛出致命错误：`ModuleNotFoundError: No module named 'encodings'`。
> - **安全防呆方案**：本次从 `v1.6.3.4` 到 `v1.6.3.5` 的 Python 依赖清单（`requirements.txt`）**完全零变更**！
> - 增量升级脚本已实现自动从既有运行目录 `/opt/tdsql-sqlcheck/releases/v1.6.3.4/venv` 完整拷贝继承虚拟环境，秒级完成且 100% 杜绝 Python 解释器崩溃陷阱。**绝对不可在测试机重新创建 venv 或执行 pip install！**

> [!CAUTION]
> ### 铁律 2：绝对严禁覆盖或重置 `.env`，必须严格保持本地测试元数据库连接！
> - **测试环境核心保护**：测试服务器使用的元数据库是本地 MySQL 8.0.28（`127.0.0.1:3306`，数据库 `tdsql_sqlcheck`）。
> - 部署过程中绝对不可覆盖 `/opt/tdsql-sqlcheck/.env`；
> - 严禁修改为外部生产 TDSQL 集群地址（`10.243.16.238`），严防测试操作与测试数据污染生产环境！

> [!IMPORTANT]
> ### 铁律 3：加密密钥 `encryption.key` 绝对延续传承！
> - 系统纳管的各 TDSQL 数据库实例连接密码均基于 AES 对称密钥加密存储；
> - 新发布目录 `/opt/tdsql-sqlcheck/releases/v1.6.3.5/data/encryption.key` 与测试环境现网 `.env` 中的 `TDSQL_ENCRYPTION_KEY` 必须保持 44 位完全一致；
> - 升级脚本会自动从 `v1.6.3.4` 目录同步该文件，确保已有实例连接密码解密正常，绝不发生 `InvalidToken` 故障。

> [!IMPORTANT]
> ### 铁律 4：严格遵循 Releases 物理隔离与软链接原子切换！
> - 必须将新版本独立部署至 `/opt/tdsql-sqlcheck/releases/v1.6.3.5`；
> - 增量文件写入并校验无误后，通过 `ln -sfn /opt/tdsql-sqlcheck/releases/v1.6.3.5 /opt/tdsql-sqlcheck/current` 实现原子切换；
> - 旧版本 `/opt/tdsql-sqlcheck/releases/v1.6.3.4` 必须原样完整保留，支持发生任何非预期异常时通过单行命令**秒级无损回滚**！

> [!IMPORTANT]
> ### 铁律 5：【v1.6.3.5 核心变动】新增 `tdsql-metadata-runner` 独立执行器服务，必须严格遵循“先 Runner、后 Web”启动契约！
> - v1.6.3.5 将长耗时的元数据提取与审核彻底解耦至独立后台守护进程 `tdsql-metadata-runner`；
> - 新增系统服务单元文件：`/etc/systemd/system/tdsql-metadata-runner.service`；
> - **启动顺序铁律**：升级脚本会**先安装并启动 `tdsql-metadata-runner`**，并校验其处于 `active (running)` 状态后，**再启动/重启 `tdsql-sqlcheck` Web 服务**；若 runner 启动失败，升级脚本强制报错退出，严防服务不同步。

> [!NOTE]
> ### 铁律 6：元数据库增量迁移（150号脚本）自动执行，无需手工建表！
> - 本次升级涉及元数据库新增 `metadata_audit_jobs`（任务明细表）与 `metadata_audit_slot`（独占受理槽表）；
> - 升级脚本与服务启动时，内置的 `SchemaMigrator` 会自动发现并安全执行 `backend/schema/v15/150_metadata_audit_jobs.sql`；
> - 升级后只需通过只读 SQL 确认两表存在即可，无需运维手工执行建表 DDL。

> [!NOTE]
> ### 铁律 7：在轨升级 8000 端口占用属于正常现象，绝不作为阻断项！
> - 在轨升级过程中，旧版 `tdsql-sqlcheck` 服务仍在监听 8000 端口属于正常现象；
> - 升级脚本在切换软链接后通过 `systemctl restart tdsql-sqlcheck` 平滑重启服务接管端口，不会误报 FAIL 中止。

> [!WARNING]
> ### 铁律 8：旧接口 `/api/v1/audit/extract-and-audit` 严格退役，返回 410 Gone！
> - v1.6.3.5 旧的同步阻塞长连接接口已彻底废弃，返回 `HTTP 410 Gone`；
> - 前端已切换为调用 `POST /api/v1/audit/metadata-jobs` 并轮询，部署完成后若有外部系统或旧脚本调用旧接口，请统一改造为调用新任务接口。

> [!IMPORTANT]
> ### 铁律 9：`deploy/verify_deploy.sh` 自动化验证作为硬性放行门禁！
> - 增量升级脚本确保将 `deploy/` 目录完整拷贝至 `releases/v1.6.3.5/`；
> - 部署完成后必须执行 `verify_deploy.sh`，以输出 `PASS`（无 FAIL 报错）作为部署成功的标准。

---

## 一、 本版核心变更与根因修复说明 (v1.6.3.4 ➔ v1.6.3.5)

### 1.1 核心问题解决：彻底根除 6000+ 表在线元数据审核 "Failed to fetch" 故障
- **故障原貌**：在对 `15064-sungl_am`（6000+ 表大库）执行在线元数据拉取审核时，耗时约 250 秒后页面弹出 `提取失败: Failed to fetch`，底层报 `net::ERR_EMPTY_RESPONSE`，Uvicorn Worker 进程在第 249 秒猝死。
- **根因消除 (FIX-01)**：
  1. 根除了 v1.6.3.2 引入的 R035 批内跨表字段类型检查全量 AST 驻留与 $O(N^2)$ 字典浅拷贝爆炸；
  2. 实现**有界见证索引**（`R035PriorIndex`），每个列名最多维护 5 个轻量标量槽位，空间复杂度由 $O(N^2)$ 骤降至 $O(U)$；
  3. `audit_file` 恢复为**流式生成器**，逐句解析并即时释放 AST；
  4. **实测表现**：6000 表审核内存峰值由旧版的 **> 3.8 GiB 降至 42.82 MB（降低 99%）**，彻底消除内存击穿。

### 1.2 架构全面升级：独立 Runner 执行器与任务持久化状态机 (FIX-02)
- **双进程隔离**：元数据拉取与重型解析移至独立守护进程 `tdsql-metadata-runner`，Web 服务的 asyncio 事件循环不再发生长达数分钟的同步阻塞；
- **任务持久化**：新增 `metadata_audit_jobs` 表，记录任务各阶段（`ACCEPTED $\to$ RUNNING $\to$ PUBLISHED $\to$ SUCCEEDED`），终态必落 `finished_at`；
- **单机并发保护**：新增 `metadata_audit_slot` 表，同一时刻只允许 1 个重任务执行，并发请求返回 `409 METADATA_BUSY` 友好排队提示。

### 1.3 前端交互与断线自愈增强 (FIX-03)
- **实时进度感知**：前端改为每 2 秒短轮询，实时展示阶段与已处理表数；
- **断线自愈**：记录 `meta_submission` 生命周期，浏览器网络闪断或页面刷新后，提供独立「恢复查看上次任务」入口，只读恢复任务卡，不重复发起任务；
- **产物分页与防串台**：审核结果按页加载，带有世代守卫（`_metaPollGen`），防止慢网络响应覆盖新视图；
- **友好取消**：支持在运行态弹出确认框并取消任务，runner 安全释放独占槽位。

---

## 二、 元数据库（MySQL 8.0.28）增量迁移说明

### 2.1 自动迁移机制
后台服务启动时，内置的 Schema 自动迁移引擎会按槽位读取 `backend/schema/v15/150_metadata_audit_jobs.sql`，自动创建两张新表并在 `schema_migrations` 表登记版本键。

### 2.2 迁移状态核验 SQL（内网智能体升级后核对）
内网智能体可通过 SSH 登录测试服务器本地 MySQL 执行以下验证 SQL：

```bash
ssh root@10.243.16.252 "mysql -h127.0.0.1 -P3306 -uroot -ptdsql_test_2024 tdsql_sqlcheck" << 'EOF'
-- 1. 核对 150 号元数据任务迁移是否已登记
SELECT version_key, checksum, applied_at 
FROM schema_migrations 
WHERE version_key LIKE '%150%';

-- 2. 验证任务表 metadata_audit_jobs 结构
DESC metadata_audit_jobs;

-- 3. 验证独占受理槽表 metadata_audit_slot（默认应有 id=1 的初始行）
SELECT * FROM metadata_audit_slot;
EOF
```

**预期输出指标**：
- `schema_migrations` 中存在 `v15_150_metadata_audit_jobs` 记录；
- `metadata_audit_jobs` 包含 `job_id`, `state`, `phase`, `progress_json`, `created_at`, `finished_at` 等列；
- `metadata_audit_slot` 查出 1 行数据，`id=1`，`accepting=1`。

---

## 三、 增量升级实操步骤（Windows 部署机一键直推）

以下操作均在**内网 Windows 部署机**通过 PowerShell 执行（该机已与 `root@10.243.16.252` 建立 SSH 免密通道）：

### 步骤 1：上传增量补丁包至测试服务器
```powershell
# 1. 进入本地补丁产出目录
cd c:\TDSQL_SQLCHECK\TDSQL-SQLCheck\dist

# 2. 上传补丁包及其校验文件至测试机 /tmp/dist/
ssh root@10.243.16.252 "mkdir -p /tmp/dist"
scp tdsql-sqlcheck-v1.6.3.5-patch.tar.gz root@10.243.16.252:/tmp/dist/
scp tdsql-sqlcheck-v1.6.3.5-patch.tar.gz.sha256 root@10.243.16.252:/tmp/dist/

# 3. 在测试机上核对 SHA256 校验和（防文件传输损坏）
ssh root@10.243.16.252 "cd /tmp/dist && sha256sum -c tdsql-sqlcheck-v1.6.3.5-patch.tar.gz.sha256"
```
*预期输出*：`tdsql-sqlcheck-v1.6.3.5-patch.tar.gz: OK`。

---

### 步骤 2：解压补丁并执行增量升级脚本
```powershell
ssh root@10.243.16.252 "bash -s" << 'EOF'
set -e

echo "=== [1/4] 解压 v1.6.3.5 补丁包至 /tmp/dist/tdsql-sqlcheck-v1.6.3.5-patch ==="
rm -rf /tmp/dist/tdsql-sqlcheck-v1.6.3.5-patch
mkdir -p /tmp/dist/tdsql-sqlcheck-v1.6.3.5-patch
tar -zxf /tmp/dist/tdsql-sqlcheck-v1.6.3.5-patch.tar.gz -C /tmp/dist/tdsql-sqlcheck-v1.6.3.5-patch

echo "=== [2/4] 执行增量平滑升级脚本 ==="
chmod +x /tmp/dist/tdsql-sqlcheck-v1.6.3.5-patch/deploy/*.sh
bash /tmp/dist/tdsql-sqlcheck-v1.6.3.5-patch/deploy/upgrade_incremental.sh \
  --install-dir /opt/tdsql-sqlcheck \
  --target-version 1.6.3.5 \
  --source-dir /tmp/dist/tdsql-sqlcheck-v1.6.3.5-patch \
  --user root \
  --port 8000

echo "=== 增量升级执行完成 ==="
EOF
```

---

### 步骤 3：验证双服务状态（重要）
v1.6.3.5 包含两个服务，必须确认两个服务均处于 `active (running)`：
```powershell
ssh root@10.243.16.252 "systemctl status tdsql-metadata-runner.service tdsql-sqlcheck.service --no-pager -l"
```
*预期输出*：
- `tdsql-metadata-runner.service`: `Active: active (running)`
- `tdsql-sqlcheck.service`: `Active: active (running)`

---

### 步骤 4：执行自动化部署契约验证
```powershell
ssh root@10.243.16.252 "bash /opt/tdsql-sqlcheck/current/deploy/verify_deploy.sh --port 8000"
```
*预期输出*：所有项均为 `PASS`，无 `FAIL` 报错。

---

## 四、 秒级无损回滚方案（灾备预案）

若升级后发生不可预期的异常，可通过以下单行命令秒级无损回退至 `v1.6.3.4`：

```bash
ssh root@10.243.16.252 "bash -s" << 'EOF'
echo "=== 开始紧急回滚至 v1.6.3.4 ==="
# 1. 停止并禁用 v1.6.3.5 的 runner 服务
systemctl stop tdsql-metadata-runner || true
systemctl disable tdsql-metadata-runner || true

# 2. 将 current 软链接切回 v1.6.3.4
ln -sfn /opt/tdsql-sqlcheck/releases/v1.6.3.4 /opt/tdsql-sqlcheck/current

# 3. 重启 Web 服务
systemctl restart tdsql-sqlcheck

# 4. 验证回滚结果
echo "当前在轨版本: $(cat /opt/tdsql-sqlcheck/current/VERSION)"
systemctl status tdsql-sqlcheck --no-pager
EOF
```

---

## 五、 已知遗留问题与运维自检说明（D-04）

- **问题说明 (D-04, P3)**：若元数据库中存在由于极端断电、历史脏数据导致的 `state='ACCEPTED'` 但受理槽并未指向它的孤立残留任务，前端任务卡会显示“执行中”，且两个按钮同时禁用。
- **正常流转保障**：正常业务的所有 6 条终止流转路径均会先写终态再释放槽，正常运转不会产生该状态。
- **运维只读排查 SQL**：
  ```sql
  -- 检查是否存在非终态残留
  SELECT id, state, phase, created_at FROM metadata_audit_jobs WHERE state NOT IN ('SUCCEEDED','FAILED','CANCELLED');
  -- 查看受理槽归属
  SELECT active_job_id, accepting, runner_heartbeat_at FROM metadata_audit_slot WHERE id = 1;
  ```
  *处置*：若第 1 条查出处于 ACCEPTED 的孤立任务，执行 `UPDATE metadata_audit_jobs SET state='FAILED', finished_at=NOW() WHERE id=<ID>;` 即可自愈。
