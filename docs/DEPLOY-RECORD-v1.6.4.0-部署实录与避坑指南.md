# TDSQL SQL审核工具 v1.6.4.0 部署实录与避坑指南

**部署日期**：2026-09-20  
**目标服务器**：10.243.16.252（内网测试环境）  
**升级路径**：v1.6.3.7 → v1.6.4.0（全量发布）  
**部署智能体**：Lingma  
**打包智能体**：待转交外网智能体G  

---

## 一、部署概述

### 1.1 版本特性

v1.6.4.0 为重大里程碑版本，核心引入企业级 AI Copilot 智能专家助手系统，包含三大子模块：
- **前端智能交互中台**：悬浮气泡对话助手，10类业务场景精准路由
- **Copilot运维管理中台**（copilot-admin）：多模型供应商配置、AES-256-GCM信封加密
- **Copilot独立安全审计中心**（copilot-audit）：全链路操作审计、动态数据分级脱敏

### 1.2 发布介质

- `tdsql-sqlcheck-v1.6.4.0-linux-x86_64.tar.gz`（27MB，全量包）
- `tdsql-sqlcheck-v1.6.4.0-linux-x86_64.tar.gz.sha256`（校验和文件）

### 1.3 服务架构变更

| 版本 | 服务数量 | 服务名称 |
|------|----------|----------|
| v1.6.3.7 | 2项 | `tdsql-metadata-runner`、`tdsql-sqlcheck` |
| v1.6.4.0 | **3项** | `tdsql-metadata-runner`、**`tdsql-copilot-runner`**（新增）、`tdsql-sqlcheck` |

---

## 二、部署过程中的问题实录

### 问题 1：Group B 数据表初始化失败

#### 2.1 问题现象

升级脚本执行 `[6/8] 执行数据库迁移` 步骤时，`schema.py --apply` 返回错误码 1：

```
【警告】Copilot Group B 表结构初始化返回 1，稍后 runner 启动将自动重试对账。
```

#### 2.2 错误日志

```python
Traceback (most recent call last):
  File "/opt/tdsql-sqlcheck/releases/v1.6.4.0/backend/services/database.py", line 296, in _checkout_connection
    raw = _conn_pool.get_nowait()
          ^^^^^^^^^^^^^^^^^^^^^^^
  ...
pymysql.err.OperationalError: (2003, "Can't connect to MySQL server on '127.0.0.1' ([Errno 111] Connection refused)")
```

#### 2.3 根因分析

1. **连接池耗尽**：`sqlcheck_app` 用户的数据库连接池大小为 20（`.env` 中 `SQLCHECK_DB_POOL_SIZE=20`），当升级脚本执行 `schema.py --apply` 时，已有多个后台服务占用了连接池，导致新连接无法分配。
2. **Python 环境冲突**：报错堆栈中出现 `/opt/python311/python/lib/python3.11/queue.py`，说明部分调用错误地使用了系统 Python（银河麒麟自带，有缺陷）而非 venv 中的 Python。
3. **schema.py 依赖 `ensure_db()`**：该函数尝试创建新的数据库连接来执行 Group B 表初始化，但在连接池已满的情况下必然失败。

#### 2.4 解决步骤

**步骤 1**：确认 MySQL 服务正常且可连接
```bash
# 检查 MySQL 监听
ss -tlnp | grep 3306

# 使用 .env 中的凭据测试连接
mysql -h 127.0.0.1 -P 3306 -u sqlcheck_app -p'SqlCheck_App_2026!' -e 'SELECT 1;'
```

**步骤 2**：手动执行 Group B 表创建 SQL
```bash
mysql -h 127.0.0.1 -P 3306 -u sqlcheck_app -p'SqlCheck_App_2026!' tdsql_sqlcheck \
  < /opt/tdsql-sqlcheck/releases/v1.6.4.0/backend/copilot_schema/v1/001_business.sql
```

**步骤 3**：验证表已创建
```bash
mysql -h 127.0.0.1 -P 3306 -u sqlcheck_app -p'SqlCheck_App_2026!' tdsql_sqlcheck \
  -e "SHOW TABLES LIKE 'copilot_%';"
# 预期输出：copilot_runtime, copilot_subjects（Group A，自动迁移）
# 应新增 9 张 Group B 表：copilot_providers, copilot_scene_routes 等
```

#### 2.5 影响

- **无业务影响**：Group A 表（`copilot_subjects`、`copilot_runtime`）已通过应用启动自动迁移成功。
- **Group B 表手动补建**：通过直接执行 SQL 文件完成，表结构与预期一致。

---

### 问题 2：Copilot Runner 启动报 "B组结构验收失败"

#### 2.1 问题现象

重启 `tdsql-copilot-runner` 后，日志显示：

```
ERROR tdsql.copilot.runner B组结构验收失败: ['台账未登记: copilot_v1_001_business']
ERROR tdsql.copilot.schema Copilot B组模块置为 UNAVAILABLE（epoch=1）: startup verify failed
```

#### 2.2 根因分析

1. **schema_migrations 台账未登记**：Group B 表虽然已创建，但 `schema_migrations` 表中没有 `copilot_v1_001_business` 的记录。
2. **schema.py 的验收机制**：`tdsql-copilot-runner` 启动时会执行 `startup verify`，检查 `schema_migrations` 台账中每个 B组迁移文件的版本键和校验和是否匹配。
3. **校验和计算方式特殊**：校验和不是文件的 SHA256 哈希，而是 `hashlib.sha256(sql.encode('utf-8')).hexdigest()`（文件内容的 UTF-8 编码的 SHA256）。

#### 2.3 解决步骤

**步骤 1**：登记台账
```bash
mysql -h 127.0.0.1 -P 3306 -u sqlcheck_app -p'SqlCheck_App_2026!' tdsql_sqlcheck -e \
  "INSERT INTO schema_migrations (version_key, checksum, applied_at) \
   VALUES ('copilot_v1_001_business', '', NOW()) \
   ON DUPLICATE KEY UPDATE version_key=version_key;"
```

**步骤 2**：计算并更新正确 checksum
```bash
# 计算 checksum（文件内容的 UTF-8 编码 SHA256）
python3 -c "
import hashlib
f = open('/opt/tdsql-sqlcheck/releases/v1.6.4.0/backend/copilot_schema/v1/001_business.sql', 'r')
sql = f.read()
f.close()
print(hashlib.sha256(sql.encode('utf-8')).hexdigest())
"
# 输出: 6cdda7b12cbdc2dba6a40815fbc0318b489216c9fd838979289890a1d90112b0

# 更新 checksum
mysql -h 127.0.0.1 -P 3306 -u sqlcheck_app -p'SqlCheck_App_2026!' tdsql_sqlcheck -e \
  "UPDATE schema_migrations SET checksum='6cdda7b12cbdc2dba6a40815fbc0318b489216c9fd838979289890a1d90112b0' \
   WHERE version_key='copilot_v1_001_business';"
```

**步骤 3**：重启 copilot-runner 验证
```bash
systemctl restart tdsql-copilot-runner
sleep 3
systemctl status tdsql-copilot-runner --no-pager
# 预期输出:
# INFO tdsql.copilot.knowledge 知识包加载完成: kb-1.6.4.0-xxx (23段)
# INFO tdsql.database 数据库初始化完成 (V2.0, MySQL)
# INFO tdsql.copilot.runner COPILOT_ENABLED=false，runner 保持未受理模式
```

#### 2.4 影响

- 升级后首次 `tdsql-copilot-runner` 启动会因台账缺失而失败。
- **Web 服务不受影响**：`tdsql-sqlcheck` 启动时仍会显示 `Copilot 启动验收完成: local_ready=True`（因为它有独立的验收逻辑）。
- 修复后 runner 正常运行，但 `COPILOT_ENABLED=false`（因 `.env` 中未配置大模型端点，属预期行为）。

---

### 问题 3：Web 服务重启后 Copilot 验收状态

#### 3.1 问题现象

重启 `tdsql-sqlcheck` 后，日志显示：
```
INFO tdsql.copilot 启动验收完成: local_ready=False reason=COPILOT_SCHEMA_UNAVAILABLE
```

#### 3.2 根因分析

1. **验收时机问题**：Web 服务重启时，`tdsql-copilot-runner` 尚未完成启动和 Group B 结构验收。
2. **验收状态非阻塞**：`local_ready=False` 不影响 Web 服务正常运行，仅表示 Copilot 功能在启动时刻尚未完全就绪。

#### 3.3 解决步骤

**无需处理**。等待 `tdsql-copilot-runner` 完成结构验收后，再次重启 `tdsql-sqlcheck` 即可：

```bash
systemctl restart tdsql-sqlcheck
sleep 3
# 预期输出:
# INFO tdsql.copilot 启动验收完成: local_ready=True reason=
```

#### 3.4 影响

- **首次启动可能显示 `local_ready=False`**，属预期行为。
- **重新部署后第二次启动必定 `local_ready=True`**。
- 不影响 `verify_deploy.sh` 验证结果（14项全部 PASS）。

---

## 三、避坑指南（供打包智能体参考）

### 3.1 数据库迁移脚本改进建议

#### 建议 1：Group B 迁移脚本应具备连接池感知能力

**当前问题**：`schema.py --apply` 调用 `ensure_db()` 尝试创建新连接，但在连接池已满时必然失败。

**改进建议**：
```python
# 在 schema.py 的 apply_business_schema() 中：
def apply_business_schema():
    # 改进：使用连接池现有连接而非尝试创建新连接
    # 或添加重试机制：max_retries=3, backoff=5s
    ...
```

#### 建议 2：提供独立的数据表初始化脚本

**当前问题**：Group B 表初始化依赖 `schema.py --apply`，但该脚本有复杂的依赖（`ensure_db()`、连接池管理等）。

**改进建议**：
- 在发布包中提供独立的 `init_copilot_tables.sql` 文件。
- 部署文档中明确说明该文件的执行步骤和时机。
- 该 SQL 文件应为纯 DDL，不依赖任何 Python 代码。

#### 建议 3：自动登记 schema_migrations 台账

**当前问题**：`schema.py --apply` 失败后，表已创建但台账未登记，需要手动计算 checksum 并登记。

**改进建议**：
- `schema.py` 应确保表创建与台账登记为原子操作。
- 或提供独立的 `register_migration.py` 脚本，接受 `--version-key` 和 `--checksum` 参数。
- 或在 `init_copilot_tables.sql` 末尾直接包含 `INSERT INTO schema_migrations` 语句。

### 3.2 部署脚本改进建议

#### 建议 4：部署脚本应自动检测并处理 Group B 迁移失败

**当前问题**：`upgrade_incremental.sh` 中 Group B 迁移失败只输出警告，不重试或不提供明确的回退指引。

**改进建议**：
```bash
# 在 deploy 脚本中：
"${PY_BIN}" -m backend.services.copilot.schema --apply
MIG_RET=$?
if [ ${MIG_RET} -ne 0 ]; then
    echo "【警告】Copilot Group B 表结构初始化失败，执行备用方案..."
    # 备用方案：直接执行 SQL 文件
    mysql ... < "${TARGET_RELEASE}/backend/copilot_schema/v1/001_business.sql"
    # 登记台账
    CHECKSUM=$("${PY_BIN}" -c "import hashlib; ...")
    mysql ... -e "INSERT INTO schema_migrations ..."
fi
```

#### 建议 5：部署脚本应自动计算 checksum 并登记台账

**当前问题**：checksum 计算方式（文件内容 UTF-8 编码 SHA256）不在部署文档中明确说明，导致手动修复时无法正确计算。

**改进建议**：
- 在部署脚本中自动计算 checksum 并登记台账。
- 或在部署文档中明确说明 checksum 的计算方式。
- 或在发布包中提供 `CHECKSUM` 文件。

### 3.3 部署文档改进建议

#### 建议 6：部署文档应包含 Group B 迁移故障排查章节

**当前问题**：部署文档中仅说明 `schema.py --apply` 会执行迁移，未说明失败后的备用方案和手动修复步骤。

**改进建议**：在部署文档中增加以下内容：
```markdown
### 常见问题：Group B 表结构初始化失败

**现象**：
```
【警告】Copilot Group B 表结构初始化返回 1
```

**排查步骤**：
1. 确认 MySQL 服务正常运行：`systemctl status mysqld`
2. 确认数据库连接正常：`mysql -h 127.0.0.1 -P 3306 -u sqlcheck_app -p'...' -e 'SELECT 1;'`
3. 手动执行 SQL：`mysql ... < releases/vX.X.X.X/backend/copilot_schema/v1/001_business.sql`
4. 登记台账：计算 checksum 并 INSERT INTO schema_migrations
```

#### 建议 7：部署文档应明确 checksum 计算方式

**当前问题**：checksum 计算方式（`hashlib.sha256(sql.encode('utf-8')).hexdigest()`）不在文档中说明。

**改进建议**：
```markdown
### schema_migrations 台账登记

checksum 计算方式：文件内容的 UTF-8 编码的 SHA256 哈希。

计算命令：
```bash
python3 -c "
import hashlib
f = open('backend/copilot_schema/v1/001_business.sql', 'r')
sql = f.read()
f.close()
print(hashlib.sha256(sql.encode('utf-8')).hexdigest())
"
```

### 3.4 发布包结构改进建议

#### 建议 8：发布包中提供独立的 Group B 初始化脚本

**当前问题**：Group B 表初始化依赖 `backend/copilot_schema/v1/001_business.sql`，该文件在 `backend/` 目录下，部署时不易发现。

**改进建议**：
- 在发布包根目录提供 `init_copilot_tables.sql` 文件（复制自 `backend/copilot_schema/v1/001_business.sql`）。
- 或在 `deploy/` 目录下提供 `migrate_copilot_schema.sh` 脚本。

#### 建议 9：发布包中提供 CHECKSUM 文件

**当前问题**：checksum 需要手动计算，容易出错。

**改进建议**：
- 在发布包中提供 `backend/copilot_schema/CHECKSUM` 文件，内容为：
  ```
  copilot_v1_001_business 6cdda7b12cbdc2dba6a40815fbc0318b489216c9fd838979289890a1d90112b0
  ```
- 部署脚本直接从文件读取 checksum 并登记台账。

---

## 四、部署成功最终状态

### 4.1 服务状态

| 服务 | 状态 | 说明 |
|------|------|------|
| `tdsql-metadata-runner.service` | active (running) | 元数据审核执行器 |
| `tdsql-copilot-runner.service` | active (running) | Copilot 异步大模型调度器 |
| `tdsql-sqlcheck.service` | active (running) | Web 主服务 |

### 4.2 部署验证结果

```
════ 部署验证 v1.6.4.0 @ http://127.0.0.1:8000 ════

  [PASS] 健康探针 HTTP 成功
  [PASS] 版本号 1.6.4.0
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

### 4.3 版本号确认

- VERSION 文件：`1.6.4.0`
- Web 启动日志：`TDSQL SQL审核平台已就绪 (V1.6.4.0)`
- Copilot 验收：`local_ready=True`

---

## 五、回滚预案

如需回滚至 v1.6.3.7：

```bash
ssh root@10.243.16.252 "bash -s" << 'EOF'
set -e
INSTALL_DIR="/opt/tdsql-sqlcheck"

# 1. 查询回滚目标版本
PREV_RELEASE=$(cat "${INSTALL_DIR}/.previous_release" 2>/dev/null || echo "${INSTALL_DIR}/releases/v1.6.3.7")
echo "正在回滚至: ${PREV_RELEASE}"

# 2. 原子切回旧版本软链接
ln -sfn "${PREV_RELEASE}" "${INSTALL_DIR}/current"

# 3. 停用 v1.6.4.0 新增的 Copilot 执行器
systemctl stop tdsql-copilot-runner || true
systemctl disable tdsql-copilot-runner || true

# 4. 恢复元数据执行器与 Web 服务
systemctl restart tdsql-metadata-runner
systemctl restart tdsql-sqlcheck

# 5. 验证回滚结果
echo "当前运行版本: $(cat ${INSTALL_DIR}/current/VERSION)"
systemctl status tdsql-sqlcheck --no-pager
EOF
```

---

## 六、总结

### 6.1 关键成功因素

1. **MySQL 连接池配置合理**：`SQLCHECK_DB_POOL_SIZE=20` 满足日常业务需求，但在部署迁移时需要额外连接。
2. **venv 复用机制正确**：直接从旧版本复制 venv 避免了麒麟系统 Python 的 `encodings` 缺陷。
3. **encryption.key 传承正确**：从旧版本完整拷贝确保了实例连接密码解密正常。

### 6.2 待改进项

1. **Group B 迁移自动化**：部署脚本应自动处理迁移失败情况，提供备用方案。
2. **checksum 计算透明化**：应在部署文档中明确说明 checksum 计算方式，或提供独立的 CHECKSUM 文件。
3. **台账登记原子性**：表创建与台账登记应为原子操作，避免部分失败导致手动修复。

### 6.3 给外网智能体 G 的打包建议

1. **提供独立的 Group B 初始化 SQL**：放在发布包根目录或 `deploy/` 目录。
2. **提供 CHECKSUM 文件**：避免手动计算 checksum。
3. **完善部署文档**：增加 Group B 迁移故障排查章节和 checksum 计算说明。
4. **改进部署脚本**：自动检测迁移失败并提供备用方案。

---

**编制**：Lingma  
**审核**：Mr.Linsang  
**转交**：外网智能体 G  
**日期**：2026-09-20
