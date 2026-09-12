# TDSQL SQL审核工具 v1.6.3.6 内网测试环境增量更新部署手册

| 属性 | 内容 |
|---|---|
| **目标软件版本** | `v1.6.3.6`（TDSQL物理分片过滤、超大库突破包上限、时区对齐精准筛选、终态收敛整改） |
| **基线源版本** | `v1.6.3.5` / `v1.6.3.4`（内网测试环境当前运行版本） |
| **升级方式** | **增量更新（Releases 增量平滑升级，直接复用既有虚拟环境 venv）** |
| **发布介质** | `tdsql-sqlcheck-v1.6.3.6-patch.tar.gz`（及其 `.sha256` 校验和文件） |
| **目标测试服务器** | `10.243.16.252`（银河麒麟 Advanced Server V10 SP3，海光 x86_64 CPU） |
| **操作执行端** | 内网 Windows 部署机（已与 `root@10.243.16.252` 建立 SSH 免密信任） |
| **测试元数据库** | `10.243.16.252` 本地 MySQL 8.0.28（端口 `3306`，数据库 `tdsql_sqlcheck`） |
| **安装包上传目录** | 测试服务器 `/tmp/dist/` 目录 |

---

## 零、 针对本次升级与历史踩坑的“9项铁律”（内网智能体必读防呆）

本次增量部署方案全面继承历史部署实战经验，内网智能体在执行部署时，**务必严格遵守以下 9 项铁律，切勿违规操作**：

> [!CAUTION]
> ### 铁律 1：严禁重新安装 venv 依赖或执行 pip install！必须直接复用既有虚拟环境！
> - **历史血泪教训**：银河麒麟系统自带的 `/usr/local/bin/python3.11` 存在环境缺陷（标准库缺失 `encodings` 模块）。一旦重新运行 `python -m venv` 或 `pip install`，会抛出致命错误：`ModuleNotFoundError: No module named 'encodings'`。
> - **安全防呆方案**：本次从 `v1.6.3.5` 到 `v1.6.3.6` 的 Python 依赖清单（`requirements.txt`）**完全零变更**！
> - 增量升级脚本已实现自动从既有运行目录（`current/venv`）完整拷贝继承虚拟环境，秒级完成且 100% 杜绝 Python 解释器崩溃陷阱。**绝对不可在测试机重新创建 venv 或执行 pip install！**

> [!CAUTION]
> ### 铁律 2：绝对严禁覆盖或重置 `.env`，必须严格保持本地测试元数据库连接！
> - **测试环境核心保护**：测试服务器使用的元数据库是本地 MySQL 8.0.28（`127.0.0.1:3306`，数据库 `tdsql_sqlcheck`）。
> - 部署过程中绝对不可覆盖 `/opt/tdsql-sqlcheck/.env`；
> - 严禁修改为外部生产 TDSQL 集群地址（`10.243.16.238`），严防测试操作与测试数据污染生产环境！

> [!IMPORTANT]
> ### 铁律 3：加密密钥 `encryption.key` 绝对延续传承！
> - 系统纳管的各 TDSQL 数据库实例连接密码均基于 AES 对称密钥加密存储；
> - 新发布目录 `/opt/tdsql-sqlcheck/releases/v1.6.3.6/data/encryption.key` 与测试环境现网 `.env` 中的 `TDSQL_ENCRYPTION_KEY` 必须保持 44 位完全一致；
> - 升级脚本会自动同步该文件，确保已有实例连接密码解密正常，绝不发生 `InvalidToken` 故障。

> [!IMPORTANT]
> ### 铁律 4：严格遵循 Releases 物理隔离与软链接原子切换！
> - 必须将新版本独立部署至 `/opt/tdsql-sqlcheck/releases/v1.6.3.6`；
> - 增量文件写入并校验无误后，通过 `ln -sfn /opt/tdsql-sqlcheck/releases/v1.6.3.6 /opt/tdsql-sqlcheck/current` 实现原子切换；
> - 旧版本必须原样完整保留，支持发生任何非预期异常时通过单行命令**秒级无损回滚**！

> [!IMPORTANT]
> ### 铁律 5：必须严格遵循“先 Runner、后 Web”启动契约！
> - 系统包含独立后台执行器服务 `tdsql-metadata-runner.service` 与 Web 服务 `tdsql-sqlcheck.service`；
> - **启动顺序铁律**：升级脚本会**先重启并校验 `tdsql-metadata-runner`** 处于 `active (running)` 状态后，**再重启 `tdsql-sqlcheck` Web 服务**；若 runner 启动失败，升级脚本强制报错退出，严防服务不同步。

> [!NOTE]
> ### 铁律 6：元数据库无需手工建表！
> - 若从 v1.6.3.4 升级，系统内置 `SchemaMigrator` 会自动发现并安全执行 150 号迁移脚本（`metadata_audit_jobs` 与 `metadata_audit_slot`）；若从 v1.6.3.5 升级，表结构已就绪，直接平滑衔接。

> [!NOTE]
> ### 铁律 7：在轨升级 8000 端口占用属于正常现象，绝不作为阻断项！
> - 升级过程中，旧版 `tdsql-sqlcheck` 服务仍在监听 8000 端口属于正常现象，脚本平滑重启后将自动接管端口。

> [!WARNING]
> ### 铁律 8：旧同步接口 `/api/v1/audit/extract-and-audit` 严格退役，返回 410 Gone！
> - 旧的同步阻塞接口已彻底废弃，前端已全部调用新异步状态机接口。

> [!IMPORTANT]
> ### 铁律 9：`deploy/verify_deploy.sh` 自动化验证作为硬性放行门禁！
> - 部署完成后必须执行 `verify_deploy.sh`，以输出 `PASS`（无 FAIL/SKIP 报错）作为部署成功的标准。

---

## 一、 本版核心变更与问题修复清单 (v1.6.3.5 ➔ v1.6.3.6)

本次 `v1.6.3.6` 严格针对内网测试环境实测发现的 4 项核心缺陷实施闭环修复：

### 1.1 【BUG-01】TDSQL 二级分区底层物理分片表过滤与单表容错修复
- **原故障现象**：在对 `15005-lzbj_ecif` 库点击审核时，在第 0 秒报错崩溃：`Proxy ERROR: Table '..._tdsql_subp190001' does not exist`。
- **根因分析**：TDSQL 分布式集群二级分区的物理分片表（包含 `_tdsql_subp` / `_tdsql_shard`）会被视图枚举出，但 Proxy 拒绝对其执行单独的 `SHOW CREATE TABLE`；而 v1.6.3.5 采用了“零容忍”策略，单表失败直接抛致命异常杀死全库。
- **修复方案**：
  1. 在元数据枚举阶段增加 TDSQL 底层物理分片正则过滤器，自动过滤内部子分片（主表 DDL 已完整包含全部结构定义）；
  2. 提取循环增加单表容错存证机制，单表偶发读取异常捕获后记入跳过清单并在 SQL 中注入注释，不再杀全库任务。

### 1.2 【BUG-02】超大库（6000+表）审核结果持久化突破 `max_allowed_packet` 限制
- **原故障现象**：`15063-sungl_busi`（6097 表超大库）耗时 342 秒跑完审核后，在持久化落库时被代码拦截报错：`审核结果编码后约 88381164 字节，超过元数据库 max_allowed_packet (67108864)`，任务以 `FAILED` 告终。
- **根因分析**：
  1. 预检代码人工执行了 `* 2` 虚假翻倍计算（实际 44MB 被算成 88MB）；
  2. 试图将超大 JSON 作为一个巨石参数强塞入数据库单一字段。
- **修复方案**：
  1. 废除 `* 2` 虚假翻倍，按真实编码字节数精准预检；
  2. 引入 `compact_results_for_audit_history` 双层存储体系：当超大库违规数据超过 30MB 阈值时，自动剔除完全合规的冗余 SQL 字段，保留轻量级产物指针与完整违规明细，确保落库体积安全可控，6097 表顺利收敛为 `SUCCEEDED`。

### 1.3 【FIXREQ-01 / D-04】任务终态收敛缺口与前端活动判定整改
- **原缺陷**：极端异常残留任务导致前端进入“双按钮同时禁用且锁死”的状态，用户无法重试也无法恢复。
- **修复方案**：
  1. 后端接口暴露槽归属字段 `slot_owned` 与心跳超时判定 `stale_running`；
  2. 前端根据真实槽位状态精确区分“真正在执行器上运行”与“悬挂残留”，确保护盾按钮随时可用，彻底消除死锁。

### 1.4 【BUG-03】历史元数据审核记录落库时区对齐与当天日期精准筛选
- **原缺陷**：用户在即时审核完成后，切换到“历史元数据审核记录”查不到当次记录。原因：入库写入了 UTC 时间（比北京时间慢 8 小时），记录落到了昨天下午；按“今天”筛选返回 0 条；不筛选时首行显示为昨天时间误导用户以为丢失。
- **修复方案**：
  1. 将 Worker 落库 `audit_history.created_at`、快照 `scan_started_at`/`scan_finished_at` 以及 HTML 报告头部生成时间从 UTC 统一对齐为本地系统时间（北京时间）；
  2. 接口列表排序增强为 `ORDER BY h.created_at DESC, h.id DESC`，同秒多条记录稳定置顶；
  3. 前端表格新增明确的 **`报告ID`**（`#ID`）列，用户可一眼比对即时审核通知的 ID。

---

## 二、 增量升级实操步骤（Windows 部署机一键直推）

以下操作均在**内网 Windows 部署机**通过 PowerShell 执行（该机已与 `root@10.243.16.252` 建立 SSH 免密通道）：

### 步骤 1：上传增量补丁包至测试服务器
```powershell
# 1. 进入本地发布包产出目录
cd c:\TDSQL_SQLCHECK\TDSQL-SQLCheck\dist

# 2. 上传补丁包及其校验文件至测试机 /tmp/dist/
ssh root@10.243.16.252 "mkdir -p /tmp/dist"
scp tdsql-sqlcheck-v1.6.3.6-patch.tar.gz root@10.243.16.252:/tmp/dist/
scp tdsql-sqlcheck-v1.6.3.6-patch.tar.gz.sha256 root@10.243.16.252:/tmp/dist/

# 3. 在测试机上核对 SHA256 校验和（防文件传输损坏）
ssh root@10.243.16.252 "cd /tmp/dist && sha256sum -c tdsql-sqlcheck-v1.6.3.6-patch.tar.gz.sha256"
```
*预期输出*：`tdsql-sqlcheck-v1.6.3.6-patch.tar.gz: OK`。

---

### 步骤 2：解压补丁并执行增量升级脚本
```powershell
ssh root@10.243.16.252 "bash -s" << 'EOF'
set -e

echo "=== [1/3] 解压 v1.6.3.6 补丁包至 /tmp/dist/tdsql-sqlcheck-v1.6.3.6-patch ==="
rm -rf /tmp/dist/tdsql-sqlcheck-v1.6.3.6-patch
mkdir -p /tmp/dist/tdsql-sqlcheck-v1.6.3.6-patch
tar -zxf /tmp/dist/tdsql-sqlcheck-v1.6.3.6-patch.tar.gz -C /tmp/dist/tdsql-sqlcheck-v1.6.3.6-patch

echo "=== [2/3] 执行增量平滑升级脚本 ==="
chmod +x /tmp/dist/tdsql-sqlcheck-v1.6.3.6-patch/deploy/*.sh
bash /tmp/dist/tdsql-sqlcheck-v1.6.3.6-patch/deploy/upgrade_incremental.sh /opt/tdsql-sqlcheck 8000

echo "=== [3/3] 增量升级执行完成 ==="
EOF
```

---

### 步骤 3：验证双服务状态（重要）
确认独立执行器与 Web 两个服务均处于 `active (running)`：
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
*预期输出*：所有检查项输出 `[PASS]`，最终显示：`部署验证全部通过`。

---

### 步骤 5（可选）：存量测试数据时区校准
若内网测试机在 v1.6.3.5 测试期间留有落为 UTC 时间的历史测试记录，可通过以下 SQL 将其时间纠偏回北京时间：
```bash
ssh root@10.243.16.252 "mysql -h127.0.0.1 -P3306 -uroot -ptdsql_test_2024 tdsql_sqlcheck -e \"
UPDATE audit_history 
SET created_at = DATE_ADD(created_at, INTERVAL 8 HOUR) 
WHERE audit_type = 'extracted_schema' AND created_at >= '2026-09-10 00:00:00' AND created_at <= '2026-09-12 00:00:00';
\""
```

---

## 三、 秒级无损回滚方案（灾备预案）

若升级后发生不可预期的异常，可通过以下单行命令秒级无损回退至升级前版本：

```bash
ssh root@10.243.16.252 "bash -s" << 'EOF'
echo "=== 开始紧急回滚 ==="
# 1. 获取前一版本目录
PREV=$(cat /opt/tdsql-sqlcheck/.previous_release 2>/dev/null || echo "/opt/tdsql-sqlcheck/releases/v1.6.3.5")
echo "回滚目标版本目录: ${PREV}"

# 2. 将 current 软链接切回前一版本
ln -sfn "${PREV}" /opt/tdsql-sqlcheck/current

# 3. 重启服务
systemctl restart tdsql-metadata-runner || true
systemctl restart tdsql-sqlcheck

# 4. 验证回滚结果
echo "当前在轨版本: $(cat /opt/tdsql-sqlcheck/current/VERSION)"
systemctl status tdsql-sqlcheck --no-pager
EOF
```
