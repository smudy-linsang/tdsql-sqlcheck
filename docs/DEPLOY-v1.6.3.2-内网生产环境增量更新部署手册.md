# TDSQL SQL审核工具 v1.6.3.2 内网生产环境增量更新部署手册

| 属性 | 内容 |
|---|---|
| **目标软件版本** | `v1.6.3.2`（审核规则调整、扫描历史跨页对比、MAXVALUE 规整加固、R031 改域仅分布式） |
| **生产现网版本** | `v1.6.3.0` |
| **升级方式** | **在轨增量平滑升级（In-Place Releases 软链原子切换，业务几乎零感知）** |
| **发布介质** | `tdsql-sqlcheck-v1.6.3.2-patch.tar.gz`（及其 `.sha256` 校验文件，体积仅 1.6MB） |
| **目标生产服务器** | `10.243.16.238`（银河麒麟 Advanced Server V10 SP3，海光 x86_64 CPU） |
| **操作执行端** | 内网 Windows 部署机（已与 `root@10.243.16.238` 建立 SSH 免密信任） |
| **生产元数据库** | **外部 TDSQL 集群**（集中式实例，MySQL 协议 3306 端口，数据库 `tdsql_sqlcheck`） |
| **介质上传目录** | 生产服务器 `/tmp/dist/` 目录 |

---

## 零、 【最高生产红线】生产升级安全守则与教训避坑

在生产环境（`10.243.16.238`）执行操作时，必须严格遵守以下生产红线：

> [!CAUTION]
> **红线 1：绝对严禁删除或重置 `/opt/tdsql-sqlcheck/.env` 配置文件！**
> 生产环境 `/opt/tdsql-sqlcheck/.env` 中配置的是连接外部核心生产 TDSQL 集群的凭据与高阶生产参数。
> 增量升级必须直接沿用现网 `.env`，**绝不可覆盖或破坏原有配置**！

> [!CAUTION]
> **红线 2：绝对确保 `encryption.key` 密钥无缝延续！**
> 生产环境纳管的数十个业务 TDSQL 数据库连接密码均基于该对称密钥加密存储。
> 必须确保 `/opt/tdsql-sqlcheck/releases/v1.6.3.2/data/encryption.key` 与现网 `.env` 中的 `TDSQL_ENCRYPTION_KEY` 保持 44 位完全一致，否则现有所有实例连接将全部解密失败（报 InvalidToken）！

> [!IMPORTANT]
> **红线 3：严禁重新安装 venv 依赖，必须直接复用既有虚拟环境！**
> 针对上一次内网智能体复盘指出的严重问题：生产服务器自带的 `/usr/local/bin/python3.11` 存在缺陷（`sys.prefix` 错误且缺少 `encodings` 模块），重新创建 venv 必然导致 pip 崩溃！
> 本次从 v1.6.3.0 到 v1.6.3.2 的生产依赖清单（`requirements.txt`）**零变更**。
> 增量升级必须直接从 `/opt/tdsql-sqlcheck/releases/v1.6.3.0/venv` 拷贝复用，秒级完成且 100% 杜绝 Python 运行时灾难！

> [!IMPORTANT]
> **红线 4：严格遵循 Releases 隔离与软链接原子切换！**
> 必须将新版本独立部署至 `/opt/tdsql-sqlcheck/releases/v1.6.3.2`，旧版本 `v1.6.3.0` 完整保留，支持发生任何非预期异常时通过单行命令**秒级无损回滚**！

---

## 一、 版本核心变更全景（v1.6.3.0 ➔ v1.6.3.2）

本次 `v1.6.3.2` 已经由 Mr.Linsang 签署 GATE-1 / GATE-2 / GATE-3 三大门禁，并通过了内网测试机（`10.243.16.252`，MySQL 8.0.28）的完整增量升级与人工点验，技术风险已彻底清零。核心升级内容如下：

### 1.1 规则体系深度优化与适用域理顺
* **R011（使用 TEXT 字段）**：严重级别由 `WARNING` 降为 **`INFO`**，覆盖范围收窄为仅 `TEXT`，`TINYTEXT / TINYBLOB / JSON` 移出治理（合理放宽存量建表拦截）；
* **R120（大字段 LOB 滥用）**：新增 **`ERROR`** 级规则，严格限制 `MEDIUMTEXT / LONGTEXT / BLOB / LONGBLOB` 等超大字段滥用（收紧生产性能卡口）；
* **R030 / R031 / R032（对象类型治理三剑客）**：
  - `R030`（禁视图/存储过程/触发器/函数）：改为仅 **`分布式`** 生效；
  - `R031`（禁自定义函数）：经 Mr.Linsang（DBA负责人）裁决，同步改为仅 **`分布式`** 生效；
  - `R032`（临时表规范）：改为仅 **`分布式`** 生效；
  - **集中式实例全面放行**，彻底消除视图/过程/触发器放行而函数被误拦截的逻辑割裂。
* **R035（字段跨表类型一致性）**：启用批内跨表类型一致性上下文比对，忽略长度差异；
* **R058（UPDATE/DELETE LIMIT 规范）**：上限阈值由 1000 提升至 **`2000`**，且改用结构化 AST `dml_limit` 解析，杜绝注释/字符串误触发；
* **R121（二级分区禁止 MAXVALUE）**：新增 **`ERROR`** 级规则，仅 **`分布式`** 生效，禁止二级 RANGE 分区使用 MAXVALUE 兜底（收紧生产容量维护卡口）。

### 1.2 规则库最新基准统计（对账依据）
* 全网规则总数：**121 条**；
* 分布式实例适用：**121 条**；
* 集中式实例适用：**90 条**；
* 集中式安全跳过：**31 条**（严格等于 `DISTRIBUTED_ONLY` 集合，含 R030/R031/R032/R121 等）；
* Oracle 兼容子集：**42 条**（R078..R119）。

### 1.3 核心交互升级：四个扫描历史跨页对比
* 在线元数据审核、离线文件审核、慢 SQL 审核、批量审核历史 4 个对比页面全面升级：支持翻页时保留前一页已勾选状态，支持跨页勾选任意 2 条记录展开多维度差异比对。

### 1.4 语法解析加固（消灭级联假阳性）
* 彻底修复 `PARTITION ... VALUES LESS THAN MAXVALUE`（bare MAXVALUE）在 sqlglot 语法分析下的方言兼容性，消灭 `E999_SYNTAX_ERROR` 语法报错，根治由此引发的无主键、无引擎、无字符集等级联假阳性误报。

---

## 二、 生产元数据库变更与自动升级机理

生产环境 `10.243.16.238` 的元数据存储在**外部 TDSQL 数据库集群**中。

### 2.1 自动升级机理（平台内置能力，零人工负担）
1. **表结构稳定性**：从 `v1.6.3.0` 到 `v1.6.3.2`，系统 27 张核心数据表结构完全保持稳定，**无任何破坏性 DDL 变更**。
2. **规则配置自动补全**：服务重启时调用 `ensure_db()` ➔ `init_rule_configs()`，会自动向 `rule_configs` 表以 `INSERT IGNORE` 方式幂等写入新增的 **`R120`** 与 **`R121`** 规则。

### 2.2 DBA 手动可选 SQL（推荐在上线窗口执行一次同步）
为确保生产元数据库内规则元数据与代码完全保持一致，DBA 可在变更窗口通过数据库客户端对外部生产 TDSQL 元数据库执行以下同步：

```sql
USE `tdsql_sqlcheck`;

-- 1. 同步 R011 严重级别降为 INFO
UPDATE rule_configs SET severity = 'INFO', updated_at = NOW() WHERE rule_id = 'R011';

-- 2. 检查规则总数（升级后启动应为 121 条）
SELECT count(*) AS total_rules FROM rule_configs;

-- 3. 核对重点调整规则状态
SELECT rule_id, category, severity, enabled FROM rule_configs 
WHERE rule_id IN ('R011', 'R030', 'R031', 'R032', 'R035', 'R058', 'R120', 'R121');
```

---

## 三、 生产增量升级标准操作规程（SOP）

本操作由内网智能体在 Windows 部署机发起，通过 SSH 免密通道远程执行。

### 3.1 第一步：异地安全备份（生产红线必做）
在开始任何升级操作前，先将现网运行中的关键凭据备份至安全路径：

```bash
ssh root@10.243.16.238 "bash -s" << 'EOF'
set -euo pipefail
BACKUP_DIR="/opt/tdsql-sqlcheck/backup_pre_v1632_$(date +%Y%m%d%H%M%S)"
mkdir -p "${BACKUP_DIR}"

echo ">>> [1/2] 备份生产 .env 配置文件..."
cp -a /opt/tdsql-sqlcheck/.env "${BACKUP_DIR}/.env.bak"

echo ">>> [2/2] 备份生产加密密钥 encryption.key..."
if [ -f "/opt/tdsql-sqlcheck/current/data/encryption.key" ]; then
    cp -a /opt/tdsql-sqlcheck/current/data/encryption.key "${BACKUP_DIR}/encryption.key.bak"
elif [ -f "/opt/tdsql-sqlcheck/data/encryption.key" ]; then
    cp -a /opt/tdsql-sqlcheck/data/encryption.key "${BACKUP_DIR}/encryption.key.bak"
fi

echo "✅ 关键凭据已成功备份至: ${BACKUP_DIR}"
ls -la "${BACKUP_DIR}"
EOF
```

---

### 3.2 第二步：上传增量发布介质
在 Windows 部署机将轻量级增量补丁介质上传至生产服务器 `/tmp/dist/`：

```powershell
# 在 Windows 部署机 PowerShell 中执行
scp dist/tdsql-sqlcheck-v1.6.3.2-patch.tar.gz root@10.243.16.238:/tmp/dist/
scp dist/tdsql-sqlcheck-v1.6.3.2-patch.tar.gz.sha256 root@10.243.16.238:/tmp/dist/
```

---

### 3.3 第三步：解压补丁并执行一键增量升级（推荐）

通过 SSH 远程调用包内专用的 `upgrade_incremental.sh` 脚本，全自动完成目录隔离、venv 继承、密钥延续、软链切换及部署验证：

```bash
ssh root@10.243.16.238 "bash -s" << 'EOF'
set -euo pipefail
cd /tmp/dist

echo ">>> 1. 校验增量补丁包 SHA256 完整性..."
if command -v sha256sum >/dev/null 2>&1; then
    sha256sum -c tdsql-sqlcheck-v1.6.3.2-patch.tar.gz.sha256
fi

echo ">>> 2. 解压增量发布包..."
rm -rf tdsql-sqlcheck-v1.6.3.2-patch
tar -zxf tdsql-sqlcheck-v1.6.3.2-patch.tar.gz
cd tdsql-sqlcheck-v1.6.3.2-patch

echo ">>> 3. 执行专用的增量升级脚本..."
chmod +x deploy/upgrade_incremental.sh
bash deploy/upgrade_incremental.sh /opt/tdsql-sqlcheck 8000
EOF
```

*脚本自动执行动作解析*：
1. 自动定位 `/opt/tdsql-sqlcheck/releases/v1.6.3.0` 并确认版本；
2. 创建独立的 `/opt/tdsql-sqlcheck/releases/v1.6.3.2` 目录；
3. 部署新版代码、静态资源及 `deploy/` 脚本；
4. 直接继承旧版本的 `venv`，**零依赖重装风险**；
5. 自动同步 `encryption.key`，确保 44 位不变，保留现网 `.env`；
6. 原子切换 `current` 软链接；
7. 重启 `systemctl restart tdsql-sqlcheck`，耗时仅 1~2 秒；
8. 自动调用 `verify_deploy.sh` 执行 12 项上线验证。

---

### 3.4 备选：手动分步升级指令（若需逐条人工执行）

```bash
ssh root@10.243.16.238 "bash -s" << 'EOF'
set -euo pipefail
INSTALL_DIR="/opt/tdsql-sqlcheck"
SRC_VER="v1.6.3.0"
TGT_VER="v1.6.3.2"
NEW_REL="${INSTALL_DIR}/releases/${TGT_VER}"
PATCH_DIR="/tmp/dist/tdsql-sqlcheck-v1.6.3.2-patch"

echo "1. 创建独立 Release 目录..."
mkdir -p "${NEW_REL}"

echo "2. 部署新版程序文件..."
cp -a "${PATCH_DIR}/backend" "${NEW_REL}/"
cp -a "${PATCH_DIR}/frontend" "${NEW_REL}/"
cp -a "${PATCH_DIR}/deploy" "${NEW_REL}/"
cp -a "${PATCH_DIR}/requirements.txt" "${NEW_REL}/"
echo "1.6.3.2" > "${NEW_REL}/VERSION"
[ -d "${PATCH_DIR}/docs" ] && { mkdir -p "${NEW_REL}/docs"; cp -a "${PATCH_DIR}/docs/"* "${NEW_REL}/docs/"; }

echo "3. 复用既有健全虚拟环境 (venv)..."
cp -a "${INSTALL_DIR}/releases/${SRC_VER}/venv" "${NEW_REL}/venv"

echo "4. 继承加密密钥 (encryption.key)..."
mkdir -p "${NEW_REL}/data"
if [ -f "${INSTALL_DIR}/releases/${SRC_VER}/data/encryption.key" ]; then
    cp -a "${INSTALL_DIR}/releases/${SRC_VER}/data/encryption.key" "${NEW_REL}/data/encryption.key"
elif [ -f "${INSTALL_DIR}/data/encryption.key" ]; then
    cp -a "${INSTALL_DIR}/data/encryption.key" "${NEW_REL}/data/encryption.key"
fi

echo "5. 原子切换 current 软链接..."
echo "${INSTALL_DIR}/releases/${SRC_VER}" > "${INSTALL_DIR}/.previous_release"
ln -sfn "${NEW_REL}" "${INSTALL_DIR}/current"
chown -R sqlcheck:sqlcheck "${INSTALL_DIR}"

echo "6. 重启服务..."
systemctl restart tdsql-sqlcheck

echo "7. 执行部署验证..."
sleep 5
bash "${NEW_REL}/deploy/verify_deploy.sh" --port 8000
EOF
```

---

## 四、 生产上线验收标准（全量核验项）

升级完成后，内网智能体必须逐项执行以下验收：

### 4.1 命令行自动化部署检验（准出硬指标）
在生产机上运行部署后验证脚本：
```bash
ssh root@10.243.16.238 "cd /opt/tdsql-sqlcheck/current && bash deploy/verify_deploy.sh --port 8000"
```
**合格判定标准**：
* 输出：`PASS=12  FAIL=0  SKIP=0`；
* 退出码：`exit 0`；
* 探针响应：`{"status":"ok","version":"1.6.3.2"}`；
* 规则总数：`121` 条，Oracle 兼容规则：`42` 条。

---

### 4.2 生产加密密钥与数据连通性检验（零业务中断指标）
```bash
ssh root@10.243.16.238 "bash -s" << 'EOF'
CURR_KEY=$(cat /opt/tdsql-sqlcheck/current/data/encryption.key | tr -d ' \r\n')
PREV_KEY=$(cat /opt/tdsql-sqlcheck/releases/v1.6.3.0/data/encryption.key 2>/dev/null | tr -d ' \r\n' || true)
echo "当前密钥长度: ${#CURR_KEY}"
if [ "$CURR_KEY" = "$PREV_KEY" ] && [ "${#CURR_KEY}" -eq 44 ]; then
    echo "✅ [PASS] 密钥 44 位无缝延续，业务实例解密零中断！"
else
    echo "❌ [FAIL] 密钥发生漂移，请立即排查！"
fi
EOF
```

---

### 4.3 浏览器界面人工抽验（生产变更确认）
在内网办公电脑打开生产访问地址：`http://10.243.16.238:8000`
1. **版本确认**：右上角确认显示为 **`v1.6.3.2`**；
2. **规则库验证**：
   - 进入 **平台治理 ➔ 审核规则库**，确认总数为 **121 条**（分布式 15，DDL 23，Oracle 42）；
   - 抽查 `R011` 显示级别为 `INFO`；`R120` 和 `R121` 显示级别为 `ERROR`；
3. **跨页对比验证**：
   - 进入 **SQL审核 ➔ 在线元数据审核 ➔ 扫描对比**（或慢SQL对比）；
   - 勾选第 1 页 1 条，翻到第 2 页勾选 1 条，确认勾选未丢失，“开始对比”按钮高亮可用并能正常展开比对详情；
4. **即时审核验证**：
   - 测试包含 `PARTITION p_max VALUES LESS THAN MAXVALUE` 的二级分区建表语句，确认仅提示 `R121`，**绝无 `E999_SYNTAX_ERROR`**，无主键/引擎缺失假阳性误报。

---

## 五、 紧急一键秒级回滚预案

如果上线后发现任何不可调和的重大故障，可通过 Releases 软链架构进行秒级无损回滚：

```bash
ssh root@10.243.16.238 "bash -s" << 'EOF'
set -e
echo ">>> 正在执行生产秒级回滚到 v1.6.3.0..."
ln -sfn /opt/tdsql-sqlcheck/releases/v1.6.3.0 /opt/tdsql-sqlcheck/current
systemctl restart tdsql-sqlcheck
sleep 3
echo ">>> 回滚完成，当前运行版本: $(cat /opt/tdsql-sqlcheck/current/VERSION)"
curl -s http://127.0.0.1:8000/health
EOF
```

回滚耗时小于 3 秒，元数据与数据库连接均不受任何影响。
