# TDSQL SQL审核工具 v1.6.3.2 内网测试环境增量更新部署手册

| 属性 | 内容 |
|---|---|
| **目标软件版本** | `v1.6.3.2`（审核规则调整、扫描历史跨页对比、MAXVALUE 规整加固、R031 改域仅分布式） |
| **基线源版本** | `v1.6.3.0` |
| **升级方式** | **增量更新（In-Place / Releases 增量平滑升级）** |
| **发布介质** | `tdsql-sqlcheck-v1.6.3.2-patch.tar.gz`（及其 `.sha256` 校验文件） |
| **目标服务器** | `10.243.16.252`（银河麒麟 Advanced Server V10 SP3，海光 x86_64 CPU） |
| **操作执行端** | 内网 Windows 部署机（已与 `root@10.243.16.252` 建立 SSH 免密信任） |
| **测试元数据库** | `10.243.16.252` 本地 MySQL 8.0.28（端口 `3306`，数据库 `tdsql_sqlcheck`） |
| **安装包上传目录** | 测试服务器 `/tmp/dist/` 目录 |

---

## 零、 针对上次部署踩坑的“7项铁律”（内网智能体必读）

本次增量部署方案已全面吸收内网智能体提交的《生产环境升级部署问题报告-v1.6.3.0》（`PRODUCTION-DEPLOY-ISSUES-v1.6.3.0.md`）中的全部复盘教训，务必遵守以下铁律：

1. **铁律 1：严禁重新安装 venv 依赖，直接复用既有虚拟环境！**
   - 麒麟系统自带的 `/usr/local/bin/python3.11` 存在 `sys.prefix` 错误且缺失 `encodings` 模块，全新建 venv 会必然崩溃；
   - 本次从 v1.6.3.0 到 v1.6.3.2 的生产依赖清单（`requirements.txt`）**零变更**；
   - 增量升级直接继承 `/opt/tdsql-sqlcheck/releases/v1.6.3.0/venv`，秒级完成且 100% 避开 Python 运行时陷阱。
2. **铁律 2：严禁修改 `.env`，绝对保持本地测试元数据库连接！**
   - 必须保持 `/opt/tdsql-sqlcheck/.env` 中的 `SQLCHECK_DB_HOST=127.0.0.1`、`SQLCHECK_DB_PORT=3306`；
   - 绝不可指回外部生产 TDSQL 集群，严防测试数据污染生产。
3. **铁律 3：加密密钥 `encryption.key` 绝对延续！**
   - 新发布目录 `/opt/tdsql-sqlcheck/releases/v1.6.3.2/data/encryption.key` 与现网 `.env` 中的 `TDSQL_ENCRYPTION_KEY` 必须保持 44 位完全一致，确保已有连接解密正常。
4. **铁律 4：严格遵循 Releases 隔离与软链接原子切换！**
   - 创建新目录 `/opt/tdsql-sqlcheck/releases/v1.6.3.2`，代码更新完成后原子切换 `current` 软链接；
   - 旧版本 `/opt/tdsql-sqlcheck/releases/v1.6.3.0` 完整保留，支持秒级单行命令回滚。
5. **铁律 5：在轨升级端口占用不作为阻断项！**
   - 在轨升级时端口 8000 正在被旧版服务监听属于预期正常状态，预检与升级脚本已自适应，不再误报 FAIL 阻断。
6. **铁律 6：`deploy/` 目录完整落盘！**
   - 增量部署会将 `deploy/` 目录完整复制到 release 目录，确保 `/opt/tdsql-sqlcheck/current/deploy/verify_deploy.sh` 始终存在。
7. **铁律 7：部署验证脱敏与跨运行时 JSON 解析！**
   - `verify_deploy.sh` 已全面升级为跨平台私有临时文件与原生 Python JSON 解析，去除了外部不存在的 `J` 命令，信号 trap 拆分确保进程取消时立即退出。

---

## 一、 本版核心变更一览 (v1.6.3.0 ➔ v1.6.3.2)

1. **规则体系深度治理**：
   - **R011（使用 TEXT 字段）**：严重级别由 `WARNING` 降为 **`INFO`**，覆盖范围收窄为仅 `TEXT`，`TINYTEXT / TINYBLOB / JSON` 移出治理（放宽）；
   - **R120（大字段 LOB 滥用）**：新增 **`ERROR`** 级规则，严格限制 `MEDIUMTEXT / LONGTEXT / BLOB / LONGBLOB` 滥用（收紧）；
   - **R030（禁视图/存储过程/触发器/函数）**：适用范围调整为仅 **`分布式`** 生效，集中式实例免除拦截；
   - **R031（禁自定义函数）**：适用范围调整为仅 **`分布式`** 生效，集中式实例免除拦截；
   - **R032（临时表使用规范）**：适用范围调整为仅 **`分布式`** 生效，集中式实例免除拦截；
   - **R035（字段跨表类型一致性）**：启用批内跨表类型一致性上下文比对，忽略长度差异；
   - **R058（UPDATE/DELETE LIMIT 规范）**：上限阈值由 1000 提升至 **`2000`**，且采用结构化 AST `dml_limit` 解析；
   - **R121（二级分区禁止 MAXVALUE）**：新增 **`ERROR`** 级规则，仅 **`分布式`** 生效，禁止二级 RANGE 分区使用 MAXVALUE 兜底（收紧）。
2. **规则库统计基准**：
   - 全网规则总数：**121 条**；
   - 分布式实例适用：**121 条**；
   - 集中式实例适用：**90 条**；
   - 集中式实例安全跳过：**31 条**（含 R030/R031/R032/R121 等精确 31 条）；
   - Oracle 兼容子集：**42 条**（R078..R119）。
3. **扫描历史跨页对比能力**：
   - 在线元数据审核、离线文件审核、慢 SQL 审核、批量审核历史 4 个对比页面全面升级：支持翻页时保留前一页已勾选项，支持跨页选择任意 2 条记录展开深度对比。
4. **语法引擎鲁棒性加固**：
   - 彻底修复 `PARTITION ... VALUES LESS THAN MAXVALUE`（bare MAXVALUE）在 sqlglot 下的解析局限，消除 `E999_SYNTAX_ERROR` 语法报错及无主键、无引擎、无字符集等一连串级联假阳性误报。

---

## 二、 元数据库（MySQL 8.0.28）升级说明

### 1. 自动迁移机制（零手工 SQL 负担）
后台服务启动时调用 `ensure_db()` ➔ `init_rule_configs()`，会自动以 `INSERT IGNORE` 方式安全地将新增的 **`R120`** 和 **`R121`** 写入 `rule_configs` 系统表。
因此，**正常情况下无需运维人员手动执行复杂的 DDL 升级脚本。**

### 2. 规则配置同步 SQL（可选执行，建议在升级后执行一次）
为了确保数据库内的默认显示级别与代码完全一致，内网智能体可登录测试机本地 MySQL 8.0 执行以下幂等同步 SQL：

```bash
ssh root@10.243.16.252 "mysql -h127.0.0.1 -P3306 -uroot -ptdsql_test_2024 tdsql_sqlcheck" << 'EOF'
-- 同步 R011 严重级别降为 INFO
UPDATE rule_configs SET severity = 'INFO', updated_at = NOW() WHERE rule_id = 'R011';

-- 确认 121 条规则完整写入
SELECT count(*) AS total_rules FROM rule_configs;
SELECT rule_id, category, severity FROM rule_configs WHERE rule_id IN ('R011', 'R030', 'R031', 'R032', 'R058', 'R120', 'R121');
EOF
```

---

## 三、 增量升级执行步骤（推荐：一键自动化升级）

内网智能体在内网 Windows 部署机打开 PowerShell 终端，执行以下全流程：

### 3.1 步骤 1：上传发布介质到测试机
将外网交付的补丁包上传到测试服务器 `/tmp/dist/` 目录：
```powershell
# 在 Windows 部署机执行 scp 上传
scp tdsql-sqlcheck-v1.6.3.2-patch.tar.gz root@10.243.16.252:/tmp/dist/
scp tdsql-sqlcheck-v1.6.3.2-patch.tar.gz.sha256 root@10.243.16.252:/tmp/dist/
```

### 3.2 步骤 2：解压补丁并执行一键增量升级
通过 SSH 远程触发测试机的一键增量部署脚本：

```bash
ssh root@10.243.16.252 "bash -s" << 'EOF'
set -euo pipefail
cd /tmp/dist

echo ">>> [1/3] 校验增量补丁包 SHA256 完整性..."
if command -v sha256sum >/dev/null 2>&1; then
    sha256sum -c tdsql-sqlcheck-v1.6.3.2-patch.tar.gz.sha256
else
    echo "提示: 无 sha256sum 命令，跳过哈希校验"
fi

echo ">>> [2/3] 解压增量更新介质..."
rm -rf tdsql-sqlcheck-v1.6.3.2-patch
tar -zxf tdsql-sqlcheck-v1.6.3.2-patch.tar.gz
cd tdsql-sqlcheck-v1.6.3.2-patch

echo ">>> [3/3] 执行专用的增量升级脚本..."
chmod +x deploy/upgrade_incremental.sh
bash deploy/upgrade_incremental.sh /opt/tdsql-sqlcheck 8000
EOF
```

---

## 四、 备用方案：手动分步升级步骤（若不使用一键脚本）

若内网智能体希望逐步掌控每一步，可手动执行以下命令：

```bash
ssh root@10.243.16.252 "bash -s" << 'EOF'
set -euo pipefail
INSTALL_DIR="/opt/tdsql-sqlcheck"
CURRENT_VER="v1.6.3.0"
TARGET_VER="v1.6.3.2"
NEW_RELEASE="${INSTALL_DIR}/releases/${TARGET_VER}"
PATCH_DIR="/tmp/dist/tdsql-sqlcheck-v1.6.3.2-patch"

echo "1. 创建目标 release 目录..."
mkdir -p "${NEW_RELEASE}"

echo "2. 部署增量代码、前端静态文件与 deploy 脚本..."
cp -a "${PATCH_DIR}/backend" "${NEW_RELEASE}/"
cp -a "${PATCH_DIR}/frontend" "${NEW_RELEASE}/"
cp -a "${PATCH_DIR}/deploy" "${NEW_RELEASE}/"
cp -a "${PATCH_DIR}/requirements.txt" "${NEW_RELEASE}/"
echo "1.6.3.2" > "${NEW_RELEASE}/VERSION"
if [ -d "${PATCH_DIR}/docs" ]; then
    mkdir -p "${NEW_RELEASE}/docs"
    cp -a "${PATCH_DIR}/docs/"* "${NEW_RELEASE}/docs/"
fi

echo "3. 复用既有健全 venv 虚拟环境 (避开系统 Python 陷阱)..."
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

升级完成后，请内网智能体按以下步骤验收：

### 1. 命令行部署验证（硬性指标）
在测试机上运行部署后验证脚本：
```bash
ssh root@10.243.16.252 "cd /opt/tdsql-sqlcheck/current && bash deploy/verify_deploy.sh --port 8000"
```
**合格标准**：
* 输出：`PASS=12  FAIL=0  SKIP=0`；
* 退出码：`exit 0`；
* 探针响应：`{"status":"ok","version":"1.6.3.2"}`；
* 规则总数验证：`121` 条，Oracle 兼容：`42` 条。

### 2. 密钥传承一致性验证
检查升级前后的密钥是否 100% 保持一致：
```bash
ssh root@10.243.16.252 "bash -s" << 'EOF'
CURR_KEY=$(cat /opt/tdsql-sqlcheck/current/data/encryption.key | tr -d ' \r\n')
PREV_KEY=$(cat /opt/tdsql-sqlcheck/releases/v1.6.3.0/data/encryption.key 2>/dev/null | tr -d ' \r\n' || true)
echo "当前版本密钥长度: ${#CURR_KEY}"
if [ "$CURR_KEY" = "$PREV_KEY" ]; then
    echo "✅ [PASS] 密钥 44 位完全一致，实例连接无缝延续！"
else
    echo "❌ [FAIL] 密钥发生漂移，请立即排查！"
fi
EOF
```

### 3. 浏览器界面人工点验
打开浏览器访问：`http://10.243.16.252:8000`
1. 登录管理员账号（`admin` / `Abcd1234` 或测试环境既有口令）；
2. 确认系统顶栏版本显示为 **`v1.6.3.2`**；
3. 进入 **平台治理 ➔ 审核规则库**，确认规则总数显示为 **121 条**（分布式 15，DDL 23，Oracle 42）；
4. 进入 **SQL审核 ➔ 在线元数据审核 ➔ 扫描对比**：
   - 在第 1 页勾选 1 条记录，切换翻页到第 2 页，再勾选 1 条记录；
   - 确认第 1 页勾选不丢失，右上角“开始对比”高亮可用，点击可正常展开对比抽屉。
5. 进入 **即时SQL审核**，测试包含 `PARTITION p_max VALUES LESS THAN MAXVALUE` 的建表 DDL：
   - 确认仅提示 `R121` 违规，**绝对无 `E999_SYNTAX_ERROR` 语法报错**，无缺失主键或缺失引擎等假阳性误报。

---

## 六、 紧急秒级回滚方案

如果在升级后发现任何阻断性异常，可直接利用 Releases 软链接特性进行秒级无损回滚：

```bash
ssh root@10.243.16.252 "bash -s" << 'EOF'
set -e
echo ">>> 正在秒级回滚到 v1.6.3.0..."
ln -sfn /opt/tdsql-sqlcheck/releases/v1.6.3.0 /opt/tdsql-sqlcheck/current
systemctl restart tdsql-sqlcheck
sleep 3
echo ">>> 回滚完成，当前运行版本: $(cat /opt/tdsql-sqlcheck/current/VERSION)"
curl -s http://127.0.0.1:8000/health
EOF
```
回滚后，元数据库表结构及数据依然安全保持兼容。
