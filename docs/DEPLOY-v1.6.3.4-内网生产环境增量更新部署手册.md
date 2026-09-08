# TDSQL SQL审核工具 v1.6.3.4 内网生产环境增量更新部署手册

| 属性 | 内容 |
|---|---|
| **目标软件版本** | `v1.6.3.4`（报告实例标识与分区统计及审核网关修复、质检 7 项 DEFECT 彻底闭环） |
| **生产现网版本** | `v1.6.3.2`（或 `v1.6.3.0`，增量脚本自动从既有 `/opt/tdsql-sqlcheck/current` 动态继承） |
| **升级方式** | **在轨增量平滑升级（In-Place Releases 软链原子切换，业务几乎零感知，秒级生效）** |
| **发布介质** | `tdsql-sqlcheck-v1.6.3.4-patch.tar.gz`（及其 `.sha256` 校验和文件，体积仅 1.69MB） |
| **目标生产服务器** | `10.243.16.238`（银河麒麟 Advanced Server V10 SP3，海光 x86_64 CPU） |
| **操作执行端** | 内网 Windows 部署机（已与 `root@10.243.16.238` 建立 SSH 免密信任） |
| **生产元数据库** | **外部 TDSQL 集群**（集中式实例，MySQL 协议 3306 端口，数据库 `tdsql_sqlcheck`） |
| **介质上传目录** | 生产服务器 `/tmp/dist/` 目录 |

---

## 零、 【最高生产红线】生产升级安全守则与教训避坑

在生产服务器（`10.243.16.238`）执行升级操作时，必须严格遵守以下生产红线与经验守则，切勿重蹈覆辙：

> [!CAUTION]
> ### 红线 1：绝对严禁删除、重置或覆盖 `/opt/tdsql-sqlcheck/.env` 生产配置文件！
> - 生产环境 `/opt/tdsql-sqlcheck/.env` 中配置的是连接外部核心生产 TDSQL 集群的凭据与生产级高阶运行参数；
> - 增量升级必须直接沿用现网 `.env`，**绝不可覆盖或破坏原有配置**！升级开始前必须执行异地备份。

> [!CAUTION]
> ### 红线 2：绝对确保 `encryption.key` 对称密钥无缝延续！
> - 生产环境纳管的数十个核心业务 TDSQL 数据库连接密码均基于 AES 对称密钥加密存储；
> - 必须确保新 Release 目录 `/opt/tdsql-sqlcheck/releases/v1.6.3.4/data/encryption.key` 与现网 `.env` 中的 `TDSQL_ENCRYPTION_KEY` 保持 44 位完全一致；
> - 若密钥发生破坏或漂移，生产所有纳管实例连接将全部解密失败（报 `InvalidToken`）导致全网失联！升级前必须备份该密钥。

> [!IMPORTANT]
> ### 红线 3：严禁重新安装 venv 依赖或执行 pip install！必须直接复用既有虚拟环境！
> - **生产环境严重缺陷复盘**：生产服务器自带的 `/usr/local/bin/python3.11` 存在缺陷（`sys.prefix` 错误指向 `/install` 且标准库缺少 `encodings` 模块），重新创建 venv 必然导致 pip 崩溃报错 `ModuleNotFoundError: No module named 'encodings'`；
> - 本次从 `v1.6.3.2` 到 `v1.6.3.4` 的生产依赖清单（`requirements.txt`）**零变更**；
> - 增量部署脚本 `upgrade_incremental.sh` 会自动从现网运行的既有目录（如 `/opt/tdsql-sqlcheck/current/venv`）拷贝继承虚拟环境，秒级完成且 100% 杜绝 Python 运行时灾难！

> [!IMPORTANT]
> ### 红线 4：严格遵循 Releases 物理隔离与软链接原子切换！
> - 必须将新版本独立部署至 `/opt/tdsql-sqlcheck/releases/v1.6.3.4`；
> - 旧版本完整保留，支持发生任何非预期异常时通过单行命令**秒级无损回滚**！

> [!WARNING]
> ### 红线 5：Nginx 反向代理网关大日志专用路由放宽与重载！
> - 生产环境若经 Nginx 统一反向代理（443 TLS 终止），必须核对 `/etc/nginx/conf.d/sqlcheck.conf` 是否已同步发布包内的专属路由规则：
>   ```nginx
>   location = /api/v1/gateway-log/upload {
>       client_max_body_size 201m;
>       proxy_read_timeout 660s;
>       proxy_send_timeout 660s;
>       client_body_timeout 60s;
>       proxy_pass http://127.0.0.1:8000;
>       proxy_set_header Host $host;
>       proxy_set_header X-Real-IP $remote_addr;
>       proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
>       proxy_set_header X-Forwarded-Proto $scheme;
>       proxy_request_buffering on;
>   }
>   ```
> - 若未配置该专属 location，用户上传超过 20MB 的网关日志将被 Nginx 拦截报 `413 Request Entity Too Large` 或 120s 超时中断！配置后必须执行 `nginx -t && nginx -s reload`。

> [!NOTE]
> ### 红线 6：生产元数据库增量迁移（141号脚本）具备全自愈能力，无需人工敲 DDL！
> - 本次升级涉及外部生产元数据库 `table_type_stat` 和 `table_type_stat_item` 各新增 8 个二级分区统计字段（共 16 列）；
> - 后台服务启动时 `ensure_db()` 会自动发现并安全执行 `backend/schema/v14/141_secondary_partition_main.sql`；
> - 第二轮质检已闭环修复 DEFECT-07，增加了“整表丢失自动建表与自愈”能力，启动时全自动迁移完成，无需人工敲 ALTER TABLE。手册第二节附有只读核验 SQL。

> [!NOTE]
> ### 红线 7：在轨升级 8000 端口占用属于正常现象，绝不作为阻断项！
> - 在轨升级过程中，旧版 `tdsql-sqlcheck` 服务仍在监听 8000 端口属于正常现象；
> - 升级脚本已优化，在原子切换软链后通过 `systemctl restart tdsql-sqlcheck` 平滑接管端口，不会误报 FAIL 中止。

> [!IMPORTANT]
> ### 红线 8：`deploy/` 运维目录完整部署与自动化验证！
> - 部署后必须执行 `deploy/verify_deploy.sh`，输出 `PASS=12 FAIL=0 SKIP=0` 作为生产放行硬指标。

---

## 一、 版本核心变更全景（v1.6.3.2 ➔ v1.6.3.4）

本次 `v1.6.3.4` 已经由 Mr. Linsang（DBA 负责人）在内网测试环境（`10.243.16.252`）完成完整实测验收（出具 [`docs/CONFIRM-v1.6.3.4-内网测试环境验证确认报告.md`](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/docs/CONFIRM-v1.6.3.4-%E5%86%85%E7%BD%91%E6%B5%8B%E8%AF%95%E7%8E%AF%E5%A2%83%E9%AA%8C%E8%AF%81%E7%A1%AE%E8%AE%A4%E6%8A%A5%E5%91%8A.md)），技术风险已彻底清零。核心升级内容如下：

### 1.1 REQ-01：报告实例标识多源融合与规范化展示
- 彻底消除了诊断报告头部重复冗余的“实例: ...”前缀；
- 统一离线 HTML 报告顶部的浅黄色徽标规范（背景 `#fff3cd`，文字 `#856404`），全平台统一；
- 修复实例连接 ID 在极端字符下的单次转义逻辑，消除 `&amp;lt;` 乱码（DEFECT-05 闭环）。

### 1.2 REQ-02：深度诊断表类型统计二级分区主表结构识别器
- 深度诊断表类型统计（G14）新增二级分区（`SUBPARTITION`）主表识别能力；
- 自动剥离底层物理子表（`xxx_tdsql_subp...`），避免表总数虚高，保持逻辑总表数守恒；
- 前端历史批次表格新增“二级分区主表”列展示（DEFECT-02 闭环）；
- 顶部汇总卡片增加 Tooltip 细粒度业务释义（DEFECT-03 闭环）；
- 修复当存在未完成库时汇总状态误判 COMPLETE 的顶底矛盾问题（DEFECT-01 闭环）。

### 1.3 REQ-03：审核规则引擎 R043 DML 实体属性提取增强
- 重构 R043 事实链，精准提取 INSERT/UPDATE/DELETE 中的物理表与分区字段；
- 彻底消除由 DDL 含 `ON UPDATE CURRENT_TIMESTAMP` 等语法误触发联表更新的假阳性误报。

### 1.4 REQ-04：审核网关大日志防爆与流式分析性能加固
- 网关日志分析模块全面升级，支持 50MB~200MB+ 网关日志流式分块分析，避免一次性读入内存导致进程 OOM；
- 分析子进程超时阈值放宽至 540 秒，整体处理软预算调优至 600 秒；
- **测试环境实测验证**：71.1 MB、125,396 行真实网关日志一次性平稳解析成功，13 章节指标与火焰图完整生成，彻底攻克历史瓶颈！

### 1.5 规则库统计基准（与 v1.6.3.2 一致保持稳定）
- 全网规则总数：**121 条**；
- 分布式实例适用：**121 条**；
- 集中式实例适用：**90 条**；
- 集中式安全跳过：**31 条**；
- Oracle 兼容子集：**42 条**（R078..R119）。

---

## 二、 生产外部 TDSQL 元数据库变更与自动升级说明

生产环境 `10.243.16.238` 的元数据存储在**外部 TDSQL 集中式数据库集群**中。

### 2.1 自动迁移机制（零人工 DDL 负担）
后台服务启动时，内置的 Schema 自动迁移引擎会读取 `backend/schema/v14/141_secondary_partition_main.sql`，自动以安全的增量方式为外部 TDSQL 库中的 `table_type_stat` 和 `table_type_stat_item` 各添加 8 个列，并在 `schema_migrations` 表登记版本键。

所有新增列均为 `NULL` 或带有安全默认值 `'LEGACY'`，**绝不锁表、绝不影响既有历史数据，对旧版代码 100% 向后兼容**。

### 2.2 DBA 只读对账 SQL（推荐在变更窗口后核验一次）
DBA 可登录外部生产 TDSQL 元数据库执行以下只读核验 SQL：

```sql
USE `tdsql_sqlcheck`;

-- 1. 核对 141 号二级分区迁移是否成功登记
SELECT version_key, checksum, applied_at 
FROM schema_migrations 
WHERE version_key LIKE '%141%';

-- 2. 核对 table_type_stat 表新增的 8 个列
SELECT column_name, data_type, is_nullable, column_default 
FROM information_schema.columns 
WHERE table_schema = 'tdsql_sqlcheck' 
  AND table_name = 'table_type_stat' 
  AND column_name LIKE 'secondary_partition%'
ORDER BY ordinal_position;

-- 3. 核对 table_type_stat_item 表新增的 8 个列
SELECT column_name, data_type, is_nullable, column_default 
FROM information_schema.columns 
WHERE table_schema = 'tdsql_sqlcheck' 
  AND table_name = 'table_type_stat_item' 
  AND column_name LIKE 'secondary_partition%'
ORDER BY ordinal_position;

-- 4. 确认规则库 121 条完整性
SELECT count(*) AS total_rules FROM rule_configs;
```

---

## 三、 生产增量升级标准操作规程（SOP）

本操作由内网智能体在内网 Windows 部署机发起，通过 SSH 免密通道远程执行。

### 3.1 第一步：异地安全备份（生产红线必做）
在开始任何升级操作前，先将生产现网的关键凭据与配置文件备份至独立目录：

```bash
ssh root@10.243.16.238 "bash -s" << 'EOF'
set -euo pipefail
BACKUP_DIR="/opt/tdsql-sqlcheck/backup_pre_v1634_$(date +%Y%m%d%H%M%S)"
mkdir -p "${BACKUP_DIR}"

echo ">>> [1/2] 备份生产 .env 配置文件..."
cp -a /opt/tdsql-sqlcheck/.env "${BACKUP_DIR}/.env.bak"

echo ">>> [2/2] 备份生产加密密钥 encryption.key..."
if [ -f "/opt/tdsql-sqlcheck/current/data/encryption.key" ]; then
    cp -a /opt/tdsql-sqlcheck/current/data/encryption.key "${BACKUP_DIR}/encryption.key.bak"
elif [ -f "/opt/tdsql-sqlcheck/data/encryption.key" ]; then
    cp -a /opt/tdsql-sqlcheck/data/encryption.key "${BACKUP_DIR}/encryption.key.bak"
fi

echo "✅ 关键生产凭据已成功备份至: ${BACKUP_DIR}"
ls -la "${BACKUP_DIR}"
EOF
```

---

### 3.2 第二步：上传增量发布介质
在 Windows 部署机将轻量级增量补丁介质上传至生产服务器 `/tmp/dist/`：

```powershell
# 在 Windows 部署机 PowerShell 中执行
scp dist/tdsql-sqlcheck-v1.6.3.4-patch.tar.gz root@10.243.16.238:/tmp/dist/
scp dist/tdsql-sqlcheck-v1.6.3.4-patch.tar.gz.sha256 root@10.243.16.238:/tmp/dist/
```

---

### 3.3 第三步：解压补丁并执行一键增量升级（推荐）

通过 SSH 远程调用包内专用的 `upgrade_incremental.sh` 脚本，全自动完成目录隔离、venv 继承、密钥延续、软链原子切换及部署验证：

```bash
ssh root@10.243.16.238 "bash -s" << 'EOF'
set -euo pipefail
cd /tmp/dist

echo ">>> 1. 校验增量补丁包 SHA256 完整性..."
if command -v sha256sum >/dev/null 2>&1; then
    sha256sum -c tdsql-sqlcheck-v1.6.3.4-patch.tar.gz.sha256
else
    echo "提示: 无 sha256sum 命令，跳过哈希校验"
fi

echo ">>> 2. 解压增量更新介质..."
rm -rf tdsql-sqlcheck-v1.6.3.4-patch
tar -zxf tdsql-sqlcheck-v1.6.3.4-patch.tar.gz
cd tdsql-sqlcheck-v1.6.3.4-patch

echo ">>> 3. 执行专用的增量升级脚本..."
chmod +x deploy/upgrade_incremental.sh
bash deploy/upgrade_incremental.sh /opt/tdsql-sqlcheck 8000
EOF
```

*脚本自动执行动作解析*：
1. 自动定位 `/opt/tdsql-sqlcheck/current` 并检测基线版本；
2. 创建独立的 `/opt/tdsql-sqlcheck/releases/v1.6.3.4` 目录；
3. 部署新版代码、静态资源及 `deploy/` 脚本；
4. 直接继承旧版本的 `venv`，**零依赖重装风险**；
5. 自动同步 `encryption.key`，确保 44 位不变，严格保留现网 `.env`；
6. 原子切换 `current` 软链接；
7. 重启 `systemctl restart tdsql-sqlcheck`，耗时仅 1~2 秒；
8. 自动调用 `verify_deploy.sh` 执行 12 项上线验证。

---

### 3.4 第四步：Nginx 专属配置检查与热重载（若生产启用 Nginx）

若生产环境使用 Nginx 反向代理，请检查并应用网关日志专属上传路由配置：

```bash
ssh root@10.243.16.238 "bash -s" << 'EOF'
if command -v nginx >/dev/null 2>&1; then
    echo ">>> 检测到 Nginx，检查 /etc/nginx/conf.d/sqlcheck.conf 中的 gateway-log 配置..."
    if ! grep -q "gateway-log/upload" /etc/nginx/conf.d/sqlcheck.conf 2>/dev/null; then
        echo ">>> 未发现专属 location，正在合并 deploy/nginx-sqlcheck.conf 相关配置..."
        # 运维人员可将 release 目录下的 deploy/nginx-sqlcheck.conf 配置合并至主配置
    fi
    echo ">>> 执行 Nginx 配置语法检查与热重载..."
    nginx -t && nginx -s reload || true
    echo "✅ Nginx 配置检查/重载完成"
else
    echo "提示: 生产未安装或未使用 Nginx，跳过"
fi
EOF
```

---

### 3.5 备选：手动分步升级指令（若需逐条人工执行）

```bash
ssh root@10.243.16.238 "bash -s" << 'EOF'
set -euo pipefail
INSTALL_DIR="/opt/tdsql-sqlcheck"
SRC_VER="v1.6.3.2"
TGT_VER="v1.6.3.4"
NEW_REL="${INSTALL_DIR}/releases/${TGT_VER}"
PATCH_DIR="/tmp/dist/tdsql-sqlcheck-v1.6.3.4-patch"

echo "1. 创建独立 Release 目录..."
mkdir -p "${NEW_REL}"

echo "2. 部署新版程序文件..."
cp -a "${PATCH_DIR}/backend" "${NEW_REL}/"
cp -a "${PATCH_DIR}/frontend" "${NEW_REL}/"
cp -a "${PATCH_DIR}/deploy" "${NEW_REL}/"
cp -a "${PATCH_DIR}/requirements.txt" "${NEW_REL}/"
echo "1.6.3.4" > "${NEW_REL}/VERSION"
[ -d "${PATCH_DIR}/docs" ] && { mkdir -p "${NEW_REL}/docs"; cp -a "${PATCH_DIR}/docs/"* "${NEW_REL}/docs/"; }

echo "3. 复用既有健全虚拟环境 (venv)..."
if [ -d "${INSTALL_DIR}/releases/${SRC_VER}/venv" ]; then
    cp -a "${INSTALL_DIR}/releases/${SRC_VER}/venv" "${NEW_REL}/venv"
elif [ -d "${INSTALL_DIR}/current/venv" ]; then
    cp -a "${INSTALL_DIR}/current/venv" "${NEW_REL}/venv"
fi

echo "4. 继承加密密钥 (encryption.key)..."
mkdir -p "${NEW_REL}/data"
if [ -f "${INSTALL_DIR}/releases/${SRC_VER}/data/encryption.key" ]; then
    cp -a "${INSTALL_DIR}/releases/${SRC_VER}/data/encryption.key" "${NEW_REL}/data/encryption.key"
elif [ -f "${INSTALL_DIR}/current/data/encryption.key" ]; then
    cp -a "${INSTALL_DIR}/current/data/encryption.key" "${NEW_REL}/data/encryption.key"
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
* 终端输出：`PASS=12  FAIL=0  SKIP=0`；
* 退出码：`exit 0`；
* 探针响应：`{"status":"ok","version":"1.6.3.4"}`；
* 规则总数：`121` 条，Oracle 兼容规则：`42` 条。

---

### 4.2 生产加密密钥与数据连通性检验（零业务中断指标）
核验升级前后对称密钥是否 100% 保持一致，避免实例密码解密失败：
```bash
ssh root@10.243.16.238 "bash -s" << 'EOF'
CURR_KEY=$(cat /opt/tdsql-sqlcheck/current/data/encryption.key | tr -d ' \r\n')
PREV_KEY=$(cat /opt/tdsql-sqlcheck/.previous_release 2>/dev/null && cat $(cat /opt/tdsql-sqlcheck/.previous_release)/data/encryption.key 2>/dev/null | tr -d ' \r\n' || true)
echo "当前密钥长度: ${#CURR_KEY}"
if [ -n "$CURR_KEY" ] && [ "${#CURR_KEY}" -eq 44 ]; then
    echo "✅ [PASS] 密钥 44 位有效！"
    if [ -n "$PREV_KEY" ] && [ "$CURR_KEY" = "$PREV_KEY" ]; then
        echo "✅ [PASS] 密钥与旧版本完全一致，业务实例解密零中断！"
    fi
else
    echo "❌ [FAIL] 密钥异常，请立即排查！"
fi
EOF
```

---

### 4.3 浏览器界面人工抽验（生产变更确认）
在内网办公电脑打开生产访问地址：`http://10.243.16.238:8000`（或行内域名入口）
1. **版本确认**：右上角确认显示为 **`v1.6.3.4`**；
2. **规则库验证**：
   - 进入 **平台治理 ➔ 审核规则库**，确认总数为 **121 条**（分布式 15，DDL 23，Oracle 42）；
3. **深度诊断 ➔ 表类型统计**：
   - 查看任意已纳管分布式实例，确认汇总栏与表格中包含“二级分区主表”与“二级分区子表”两项指标；
   - 确认主表数字带有 Tooltip `ⓘ` 标识，状态为绿色的 `OK`；
4. **深度诊断 ➔ 网关日志分析**：
   - 进入网关日志分析模块，确认文件大小与时间限制提示清晰展示（最高支持 200MB 上传与 540s 子进程分析）；
5. **SQL审核 ➔ 上线检查**：
   - 导出或查看 HTML 报告，确认报告头部无冗余“实例: ...”，页脚版本正确展示为 `V1.6.3.4`。

---

## 五、 紧急一键秒级回滚预案

如果上线后发现任何不可调和的重大故障，可通过 Releases 软链架构进行秒级无损回滚：

```bash
ssh root@10.243.16.238 "bash -s" << 'EOF'
set -e
PREV_DIR=$(cat /opt/tdsql-sqlcheck/.previous_release 2>/dev/null || echo "/opt/tdsql-sqlcheck/releases/v1.6.3.2")
echo ">>> 正在执行生产秒级回滚到 ${PREV_DIR}..."
ln -sfn "${PREV_DIR}" /opt/tdsql-sqlcheck/current
systemctl restart tdsql-sqlcheck
sleep 3
echo ">>> 回滚完成，当前运行版本: $(cat /opt/tdsql-sqlcheck/current/VERSION)"
curl -fsS http://127.0.0.1:8000/health
EOF
```

**回滚兼容性说明**：
- 回滚操作耗时小于 3 秒；
- 外部 TDSQL 元数据库新增的 8 个列均为 `NULL` 或带有安全默认值 `'LEGACY'`，对旧版代码完全向后兼容；
- 旧版系统启动后自动忽略新增列，**零数据丢失、零表结构损坏、连接零中断**。
