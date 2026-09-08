# TDSQL SQL审核工具 v1.6.3.4 内网测试环境增量更新部署手册

| 属性 | 内容 |
|---|---|
| **目标软件版本** | `v1.6.3.4`（报告实例标识与分区统计及审核网关修复、质检 7 项 DEFECT 彻底闭环） |
| **基线源版本** | `v1.6.3.2`（当前内网测试环境运行版本） |
| **升级方式** | **增量更新（In-Place / Releases 增量平滑升级，直接复用既有虚拟环境）** |
| **发布介质** | `tdsql-sqlcheck-v1.6.3.4-patch.tar.gz`（及其 `.sha256` 校验和文件） |
| **目标测试服务器** | `10.243.16.252`（银河麒麟 Advanced Server V10 SP3，海光 x86_64 CPU） |
| **操作执行端** | 内网 Windows 部署机（已与 `root@10.243.16.252` 建立 SSH 免密信任） |
| **测试元数据库** | `10.243.16.252` 本地 MySQL 8.0.28（端口 `3306`，数据库 `tdsql_sqlcheck`） |
| **安装包上传目录** | 测试服务器 `/tmp/dist/` 目录 |

---

## 零、 针对历史部署踩坑与本次升级的“8项铁律”（内网智能体必读防呆）

本次增量部署方案已全面吸收内网智能体提交的《生产环境升级部署问题报告-v1.6.3.0》（`PRODUCTION-DEPLOY-ISSUES-v1.6.3.0.md`）及后续实测中的全部经验教训。**内网智能体在执行部署时，务必严格遵守以下 8 项铁律，切勿重蹈覆辙**：

> [!CAUTION]
> ### 铁律 1：严禁重新安装 venv 依赖或执行 pip install！必须直接复用既有虚拟环境！
> - **历史血泪教训**：银河麒麟系统自带的 `/usr/local/bin/python3.11` 存在严重环境缺陷（`sys.prefix` 错误指向 `/install` 且标准库缺失 `encodings` 模块）。一旦重新运行 `python -m venv` 或 `pip install`，会必然抛出致命错误：
>   ```text
>   Fatal Python error: init_fs_encoding: failed to get the Python codec of the filesystem encoding
>   ModuleNotFoundError: No module named 'encodings'
>   ```
> - **安全防呆方案**：本次从 `v1.6.3.2` 到 `v1.6.3.4` 的 Python 依赖清单（`requirements.txt`）**完全零变更**！
> - 增量升级脚本已实现自动从既有运行目录 `/opt/tdsql-sqlcheck/releases/v1.6.3.2/venv` 完整拷贝继承虚拟环境，秒级完成且 100% 杜绝 Python 解释器崩溃陷阱。**绝对不可在测试机重新创建 venv 或在线/离线执行 pip install！**

> [!CAUTION]
> ### 铁律 2：绝对严禁覆盖或重置 `.env`，必须严格保持本地测试元数据库连接！
> - **测试环境核心保护**：测试服务器使用的元数据库是本地 MySQL 8.0.28（`127.0.0.1:3306`，数据库 `tdsql_sqlcheck`）。
> - 部署过程中绝对不可覆盖 `/opt/tdsql-sqlcheck/.env`；
> - 严禁修改为外部生产 TDSQL 集群地址（`10.243.16.238`），严防测试操作与测试数据污染生产环境！

> [!IMPORTANT]
> ### 铁律 3：加密密钥 `encryption.key` 绝对延续传承！
> - 系统纳管的各 TDSQL 数据库实例连接密码均基于 AES 对称密钥加密存储；
> - 新发布目录 `/opt/tdsql-sqlcheck/releases/v1.6.3.4/data/encryption.key` 与测试环境现网 `.env` 中的 `TDSQL_ENCRYPTION_KEY` 必须保持 44 位完全一致；
> - 升级脚本会自动从 `v1.6.3.2` 目录同步该文件，确保已有实例连接密码解密正常，绝不发生 `InvalidToken` 故障。

> [!IMPORTANT]
> ### 铁律 4：严格遵循 Releases 物理隔离与软链接原子切换！
> - 必须将新版本独立部署至 `/opt/tdsql-sqlcheck/releases/v1.6.3.4`；
> - 增量文件写入并校验无误后，通过 `ln -sfn /opt/tdsql-sqlcheck/releases/v1.6.3.4 /opt/tdsql-sqlcheck/current` 实现原子切换；
> - 旧版本 `/opt/tdsql-sqlcheck/releases/v1.6.3.2` 必须原样完整保留，支持发生任何非预期异常时通过单行命令**秒级无损回滚**！

> [!NOTE]
> ### 铁律 5：在轨升级 8000 端口占用属于正常现象，绝不作为阻断项！
> - 在轨升级过程中，旧版 `tdsql-sqlcheck` 服务仍在监听 8000 端口属于正常现象；
> - 升级脚本 `deploy/upgrade_incremental.sh` 已经过自适应优化，在切换软链接后通过 `systemctl restart tdsql-sqlcheck` 平滑重启服务接管端口，不会误报 FAIL 中止。

> [!NOTE]
> ### 铁律 6：元数据库增量迁移（141号脚本）具备全自愈能力，无需运维手工执行 DDL！
> - 本次升级涉及元数据库 `table_type_stat` 和 `table_type_stat_item` 各新增 8 个二级分区统计字段（共 16 列）；
> - 后台服务启动时 `ensure_db()` 内部的 `SchemaMigrator` 会自动发现并安全执行 `backend/schema/v14/141_secondary_partition_main.sql`；
> - 第二轮质检已闭环修复 DEFECT-07，增加了“整表丢失自动建表与自愈”能力，启动时全自动迁移完成，无需人工敲 ALTER TABLE。手册第二节附有对账 SQL 仅供内网智能体验收比对。

> [!WARNING]
> ### 铁律 7：Nginx 反向代理网关大日志上传配置（若测试环境启用 Nginx 必读）！
> - 本次 v1.6.3.4 核心增强了网关日志分析模块（REQ-04），支持 50MB~200MB+ 网关大日志文件的流式解析；
> - 若测试环境前端部署了 Nginx 反向代理，**必须更新 `/etc/nginx/conf.d/sqlcheck.conf`**（参考发布包内 `deploy/nginx-sqlcheck.conf`），确保为精确路由 `location = /api/v1/gateway-log/upload` 配置了 `client_max_body_size 201m;` 和 `proxy_read_timeout 660s;`，并执行 `nginx -t && nginx -s reload`；
> - 否则用户上传超过 20MB 的网关日志将被 Nginx 拦截报 `413 Request Entity Too Large` 或 120s 超时中断！（若测试环境浏览器直连 8000 端口，则无需担心 Nginx 限制）。

> [!IMPORTANT]
> ### 铁律 8：`deploy/` 运维目录完整部署与自动化验证！
> - 增量升级脚本确保将 `deploy/` 目录完整拷贝至 `releases/v1.6.3.4/` 目录中；
> - 部署验证脚本 `deploy/verify_deploy.sh` 已全面重构，使用原生 Python 针对 UTF-8 私有临时响应落盘解析，消除了历史未定义命令（如 `J`）与 Base64 符号转义问题；
> - 部署完成后必须执行该验证脚本，以输出 `PASS=12 FAIL=0 SKIP=0` 作为硬性放行标准。

---

## 一、 本版核心变更一览 (v1.6.3.2 ➔ v1.6.3.4)

本次 `v1.6.3.4` 是在 `v1.6.3.2` 稳定运行基础上的功能增强与质检验收整改版本，已通过两轮独立第三方验收质检（第二轮 7 项缺陷 100% 闭环，全系统 2006 项回归用例全绿准出）。核心升级亮点包括：

### 1.1 REQ-01：报告实例标识多源融合与标准化展示
- **彻底消除了诊断报告头部重复冗余的“实例: ...”前缀**；
- 统一离线 HTML 报告顶部的浅黄色徽标规范（背景 `#fff3cd`，文字 `#856404`），消除平台间样式漂移；
- 修正多源实例连接 ID 在极端字符下的单次转义逻辑，消除 `&amp;lt;` 界面乱码（DEFECT-05 闭环）。

### 1.2 REQ-02：深度诊断表类型统计二级分区主表结构识别器
- 深度诊断表类型统计（G14）新增二级分区（`SUBPARTITION`）主表识别与统计能力；
- 扩展元数据库字段契约，支持二级分区主表数、候选主表数、判明状态及覆盖状态的多层统计；
- 前端“扫描历史”弹窗表格补齐新增“二级分区主表”指标列（DEFECT-02 闭环）；
- 页面顶部汇总卡片增加 Tooltip 细粒度业务释义（DEFECT-03 闭环）；
- 修复当存在未完成库时汇总状态误判为 COMPLETE 的顶底矛盾问题（DEFECT-01 闭环）。

### 1.3 REQ-03：审核规则引擎 R043 DML 实体属性提取增强
- 重构 R043 事实链，精准提取 INSERT/UPDATE/DELETE 语句中的物理表名与分区键字段；
- 彻底消除由 DDL 含 `ON UPDATE CURRENT_TIMESTAMP` 等语法误触发联表更新的假阳性误报。

### 1.4 REQ-04：审核网关大日志流式防爆与分析性能加固
- 网关日志分析模块全面升级，支持 50MB~200MB+ 网关日志的流式分块分析，避免一次性读入内存导致进程 OOM；
- 分析子进程超时阈值放宽至 540 秒，整体流水线处理预算调优至 600 秒；
- 增强日志文件名解析兼容性，支持含连字符、多段日期及时间戳的网关文件名；
- 新增只读 `GET /api/v1/gateway-log/capabilities` 接口，前端自适应展示环境上传上限。

### 1.5 全量规则库统计基准（与 v1.6.3.2 一致保持稳定）
- 全网规则总数：**121 条**；
- 分布式实例适用：**121 条**；
- 集中式实例适用：**90 条**；
- 集中式安全跳过：**31 条**；
- Oracle 兼容子集：**42 条**（R078..R119）。

---

## 二、 元数据库（MySQL 8.0.28）升级说明

### 2.1 自动迁移机制（零手工 DDL 负担）
后台服务启动时，内置的 Schema 自动迁移引擎会按槽位读取 `backend/schema/v14/141_secondary_partition_main.sql`，自动以安全的 DDL 补齐方式为 `table_type_stat` 和 `table_type_stat_item` 各添加 8 个列，并在 `schema_migrations` 表登记版本键。

**正常情况下内网智能体无需手动执行任何 DDL 语句。**

### 2.2 迁移状态对账与验证 SQL（可选执行，建议在升级后核验）
内网智能体可通过 SSH 登录测试服务器本地 MySQL 执行以下只读验证 SQL，核对迁移结果：

```bash
ssh root@10.243.16.252 "mysql -h127.0.0.1 -P3306 -uroot -ptdsql_test_2024 tdsql_sqlcheck" << 'EOF'
-- 1. 核对 141 号二级分区迁移是否已成功登记
SELECT version_key, checksum, applied_at 
FROM schema_migrations 
WHERE version_key LIKE '%141%';

-- 2. 核对 table_type_stat 汇总表新增的 8 个字段
SELECT column_name, data_type, is_nullable, column_default 
FROM information_schema.columns 
WHERE table_schema = 'tdsql_sqlcheck' 
  AND table_name = 'table_type_stat' 
  AND column_name LIKE 'secondary_partition%'
ORDER BY ordinal_position;

-- 3. 核对 table_type_stat_item 逐库明细表新增的 8 个字段
SELECT column_name, data_type, is_nullable, column_default 
FROM information_schema.columns 
WHERE table_schema = 'tdsql_sqlcheck' 
  AND table_name = 'table_type_stat_item' 
  AND column_name LIKE 'secondary_partition%'
ORDER BY ordinal_position;

-- 4. 核对规则库 121 条规则完整性
SELECT count(*) AS total_rules FROM rule_configs;
EOF
```

**预期输出指标**：
- `schema_migrations` 中存在 `v14_141_secondary_partition_main` 记录；
- `table_type_stat` 和 `table_type_stat_item` 分别查出 8 个以 `secondary_partition_` 开头的列；
- `total_rules` 严格等于 **121**。

---

## 三、 增量升级执行步骤（推荐：一键自动化升级）

内网智能体在内网 Windows 部署机打开 PowerShell 终端，执行以下全流程操作：

### 3.1 步骤 1：上传增量发布介质到测试服务器
将外网构建输出的补丁包及哈希文件上传至测试服务器 `/tmp/dist/` 目录：

```powershell
# 在内网 Windows 部署机 PowerShell 中执行
scp dist/tdsql-sqlcheck-v1.6.3.4-patch.tar.gz root@10.243.16.252:/tmp/dist/
scp dist/tdsql-sqlcheck-v1.6.3.4-patch.tar.gz.sha256 root@10.243.16.252:/tmp/dist/
```

### 3.2 步骤 2：解压补丁并执行一键增量升级
通过 SSH 登录或远程执行测试机上的增量部署升级命令：

```bash
ssh root@10.243.16.252 "bash -s" << 'EOF'
set -euo pipefail
cd /tmp/dist

echo ">>> [1/3] 校验增量补丁包 SHA256 完整性..."
if command -v sha256sum >/dev/null 2>&1; then
    sha256sum -c tdsql-sqlcheck-v1.6.3.4-patch.tar.gz.sha256
else
    echo "提示: 无 sha256sum 命令，跳过哈希校验"
fi

echo ">>> [2/3] 解压增量更新介质..."
rm -rf tdsql-sqlcheck-v1.6.3.4-patch
tar -zxf tdsql-sqlcheck-v1.6.3.4-patch.tar.gz
cd tdsql-sqlcheck-v1.6.3.4-patch

echo ">>> [3/3] 执行专用的增量升级脚本..."
chmod +x deploy/upgrade_incremental.sh
bash deploy/upgrade_incremental.sh /opt/tdsql-sqlcheck 8000
EOF
```

脚本将自动完成：
1. 校验现网版本环境（确认基线版本为 `v1.6.3.2`）；
2. 创建独立目录 `/opt/tdsql-sqlcheck/releases/v1.6.3.4`；
3. 部署 `backend`、`frontend`、`deploy`、`requirements.txt`、`VERSION` 及文档；
4. 自动继承复用 `/opt/tdsql-sqlcheck/releases/v1.6.3.2/venv` 虚拟环境；
5. 自动同步继承 `data/encryption.key` 对称加密密钥；
6. 原子切换 `/opt/tdsql-sqlcheck/current` 软链接；
7. 重启 `tdsql-sqlcheck` 服务并执行 `verify_deploy.sh` 冒烟验证。

---

## 四、 备用方案：手动分步升级步骤（若不使用一键脚本）

若内网智能体需要手工逐步执行以细粒度观察每一步骤，可执行以下命令：

```bash
ssh root@10.243.16.252 "bash -s" << 'EOF'
set -euo pipefail
INSTALL_DIR="/opt/tdsql-sqlcheck"
CURRENT_VER="v1.6.3.2"
TARGET_VER="v1.6.3.4"
NEW_RELEASE="${INSTALL_DIR}/releases/${TARGET_VER}"
PATCH_DIR="/tmp/dist/tdsql-sqlcheck-v1.6.3.4-patch"

echo "1. 创建目标 release 目录..."
mkdir -p "${NEW_RELEASE}"

echo "2. 部署增量代码、前端静态文件与 deploy 运维脚本..."
cp -a "${PATCH_DIR}/backend" "${NEW_RELEASE}/"
cp -a "${PATCH_DIR}/frontend" "${NEW_RELEASE}/"
cp -a "${PATCH_DIR}/deploy" "${NEW_RELEASE}/"
cp -a "${PATCH_DIR}/requirements.txt" "${NEW_RELEASE}/"
echo "1.6.3.4" > "${NEW_RELEASE}/VERSION"
if [ -d "${PATCH_DIR}/docs" ]; then
    mkdir -p "${NEW_RELEASE}/docs"
    cp -a "${PATCH_DIR}/docs/"* "${NEW_RELEASE}/docs/"
fi

echo "3. 复用既有健全 venv 虚拟环境 (严格避开系统 Python 陷阱)..."
cp -a "${INSTALL_DIR}/releases/${CURRENT_VER}/venv" "${NEW_RELEASE}/venv"

echo "4. 同步加密密钥与配置延续..."
mkdir -p "${NEW_RELEASE}/data"
if [ -f "${INSTALL_DIR}/releases/${CURRENT_VER}/data/encryption.key" ]; then
    cp -a "${INSTALL_DIR}/releases/${CURRENT_VER}/data/encryption.key" "${NEW_RELEASE}/data/encryption.key"
elif [ -f "${INSTALL_DIR}/data/encryption.key" ]; then
    cp -a "${INSTALL_DIR}/data/encryption.key" "${NEW_RELEASE}/data/encryption.key"
fi

echo "5. 原子切换 current 软链接..."
echo "${INSTALL_DIR}/releases/${CURRENT_VER}" > "${INSTALL_DIR}/.previous_release"
ln -sfn "${NEW_RELEASE}" "${INSTALL_DIR}/current"
chown -R sqlcheck:sqlcheck "${INSTALL_DIR}"

echo "6. 重启服务..."
systemctl restart tdsql-sqlcheck

echo "7. 等待服务就绪并执行部署后验证..."
sleep 5
bash "${NEW_RELEASE}/deploy/verify_deploy.sh" --port 8000
EOF
```

---

## 五、 部署后验收与测试标准

升级完成后，请内网智能体按以下步骤进行完整验收：

### 5.1 命令行自动化部署验证（硬性放行指标）
在测试服务器上运行部署后验证脚本：
```bash
ssh root@10.243.16.252 "cd /opt/tdsql-sqlcheck/current && bash deploy/verify_deploy.sh --port 8000"
```
**合格标准**：
* 终端输出必须满足：`PASS=12  FAIL=0  SKIP=0`；
* 退出码：`exit 0`；
* 健康探针：`{"status":"ok","version":"1.6.3.4"}`；
* 规则总数验证：`121` 条，Oracle 兼容规则：`42` 条。

### 5.2 密钥传承一致性验证
检查升级前后对称密钥是否 100% 保持一致，避免纳管实例解密失败：
```bash
ssh root@10.243.16.252 "bash -s" << 'EOF'
CURR_KEY=$(cat /opt/tdsql-sqlcheck/current/data/encryption.key | tr -d ' \r\n')
PREV_KEY=$(cat /opt/tdsql-sqlcheck/releases/v1.6.3.2/data/encryption.key 2>/dev/null | tr -d ' \r\n' || true)
echo "当前版本密钥长度: ${#CURR_KEY}"
if [ "$CURR_KEY" = "$PREV_KEY" ]; then
    echo "✅ [PASS] 密钥 44 位完全一致，实例连接解密无缝延续！"
else
    echo "❌ [FAIL] 密钥发生漂移，请立即排查！"
fi
EOF
```

### 5.3 浏览器界面人工点验走查
打开浏览器访问：`http://10.243.16.252:8000`
1. **登录验证**：使用管理员账号（`admin` / `Abcd1234` 或测试环境既有口令）登录系统；
2. **顶栏版本核验**：确认系统右上角/顶栏版本号清晰显示为 **`v1.6.3.4`**；
3. **平台治理 ➔ 审核规则库**：
   - 确认规则总数显示为 **121 条**（分布式 15，DDL 23，Oracle 42）；
4. **深度诊断 ➔ 表类型统计（G14 重点核验）**：
   - 触发或查看某一实例的表类型统计结果；
   - 查看顶部汇总卡片中的状态指标，将鼠标悬停在 Tooltip 提示符 `ⓘ` 上，确认浮窗清晰显示候选数、已判明数、未判明数等细粒度释义；
   - 点击右上角“扫描历史”，确认弹出的历史批次抽屉表格中包含 **`二级分区主表`** 这一列指标；
5. **深度诊断 ➔ 网关日志分析（G11 重点核验）**：
   - 进入网关日志分析页面，确认界面清晰提示上传文件限制与说明；
   - 可尝试上传一个网关日志文件（如 `interf_instance_15005.2026-09-04.0`），确认流式分析正常完成，无 OOM 或 500 报错；
6. **SQL审核 ➔ 上线检查**：
   - 查看或导出一份 HTML 上线检查报告，确认报告头部无重复的 `实例: ...`，页脚版本号正确显示为 `V1.6.3.4`。

---

## 六、 紧急秒级回滚方案

如果在升级后发现任何阻断性不可接受异常，可直接利用 Releases 软链接特性进行秒级无损回滚：

```bash
ssh root@10.243.16.252 "bash -s" << 'EOF'
set -e
echo ">>> 正在秒级回滚到既有稳定版本 v1.6.3.2..."
ln -sfn /opt/tdsql-sqlcheck/releases/v1.6.3.2 /opt/tdsql-sqlcheck/current
systemctl restart tdsql-sqlcheck
sleep 3
echo ">>> 回滚完成，当前运行版本: $(cat /opt/tdsql-sqlcheck/current/VERSION)"
curl -fsS http://127.0.0.1:8000/health
EOF
```

**回滚兼容性说明**：
- 元数据库中新增的 8 个列均为 `NULL` 或带有安全默认值 `'LEGACY'`，对旧版 `v1.6.3.2` 代码完全向后兼容；
- 旧版 `v1.6.3.2` 在读取元数据时会自动忽略新列，回滚过程**零数据丢失、零表结构损坏**。
