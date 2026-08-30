# TDSQL SQL审核工具 v1.6.2.2 内网全量部署与升级手册

| 项目 | 内容 |
|---|---|
| **发布版本** | `v1.6.2.2` |
| **发布包文件** | `tdsql-sqlcheck-v1.6.2.2-linux-x86_64.tar.gz` |
| **发布日期** | 2026-08-30 |
| **适用环境** | 银河麒麟 V10 SP3 / Linux x86_64（Python 3.9+ / 3.11） |
| **元数据库要求** | **仅支持 MySQL 8.0+ / TDSQL**（不支持 MariaDB / MySQL 5.7） |
| **被审核业务库** | TDSQL 集中式/分布式（5.7/8.0 内核）、MySQL 5.7/8.0 |
| **升级方式** | **全量更新包全新部署 / 覆盖升级**（`deploy/install.sh`） |

---

## 一、 版本概述与核心修复

`v1.6.2.2` 针对内网现网 `v1.6.2.1` 在大规模生产 DDL 扫描中暴露的语法解析边界、索引类型误判及多 Worker 运行安全进行了全方位加固收口：

1. **DEF-1（分片键列名子串误报 R054 修复）**：彻底消除包含 `list_unique_num`、`shardkey_id` 等含保留子串字段被误判为唯一索引的系统性误报；
2. **DEF-2 / DEF-3（注释解析崩溃修复）**：修复 `UNIQUE KEY ... COMMENT '...'` 与 `PRIMARY KEY ... COMMENT '...'` 导致 AST 崩溃为 Command 并产生虚假 `E999` / 掩盖真实规则的缺陷；
3. **A-6.1 / A-6.2（降级与降序索引支持）**：修复降级解析中的注释残片干扰，支持 MySQL 8.0 降序索引（`DESC`/`ASC`）的完整规范化解析；
4. **O-01 ~ O-30（UAT 8 轮安全与架构加固）**：完成多 Worker 跨进程报告票据共享、KFN 结构化失败关闭、历史迁移 `v9_090_connection_unique` Checksum 自动原子调和与审计落库；
5. **超大报文支持**：请求体限制（`MAX_BODY_BYTES`）放宽至 **50MB**，原生支持万张表级别的超大元数据 SQL 文件直接上传审核。

---

## 二、 升级验收对数依据（最硬核的验收标准）

> [!IMPORTANT]
> **内网升级验收核心基准**：
> 从内网现网 `v1.6.2.1` 升级至 `v1.6.2.2` 后，在内网真实业务库的 **2,172 张表** 中，**仅有且只有 2 张表会发生规则结果变化，其余 2,170 张表逐条完全一致（零漂移、零次生灾害）**！

### 1. 两张发生变化的表核验基准

| 表名 | 规则变化明细 | 变化原因与业务含义 |
|---|---|---|
| **`kcfb_list_info`** | **`-R054`（误报消除）** | 该表的 `kcfb_list_info_idx13` 为普通索引（`KEY`），v1.6.2.1 误判为 `UNIQUE` 导致误报 R054，本版纠正为普通索引后**误报完全消除**。 |
| **`kitp_rate_plan`** | **`-E999 -R003 -R004 -R005`（误报消除）**<br/>**`+R011 +R029 +R036 +R037`（真实覆盖恢复）** | 该表含 `PRIMARY KEY (`id` DESC) COMMENT '...'`，前版因语法解析崩溃生成 E999 并抑制所有规则。本版恢复正常 AST 解析后，**虚假报警清除，真实规则检查全面恢复**。 |

### 2. 验收执行方法
部署升级完成后，直接在内网「在线元数据审核」模块重新触发对 `lzbj_ecif`（214 张表）与 `cbs_coredb`（1,958 张表）两个业务库的审核：
- 导出或比对审核报告：**仅 `kcfb_list_info` 与 `kitp_rate_plan` 两张表结果发生上述预期修正，其余所有表规则判定保持 100% 相同**，即代表本次升级对数**完全通过**。

---

## 三、 元数据库环境与兼容性约束

> [!CAUTION]
> **元数据库（存放平台自身配置与报告的数据库）仅支持 MySQL 8.0+ 或 TDSQL**。
> - **不支持 MariaDB**：实测 MariaDB 会在外键约束与特定默认值解析上导致初始化失败，服务无法启动；
> - **不支持 MySQL 5.7 作为元数据库**；
> - **业务被审核目标库不受此限制**：被审核业务库全面支持 TDSQL 集中式/分布式、MySQL 5.7 与 MySQL 8.0。

---

## 四、 性能预期与耗时说明

由于 v1.6.2.2 增加了严格的两阶段 AST 语法自愈、逐定义项 Preflight 校验、换行防注入规范化以及跨 Worker 事务安全，整体解析防线更加严密：

- **单表耗时基准**：中位数约为 **30 ms / 表**；
- **典型库耗时基准**：包含 2,000 张表的业务库，在线元数据审核总耗时约为 **1 分钟（约 57~60 秒）**；
- **耗时增量说明**：相比 v1.6.2.1 累计慢约 23%（约每千张表增加 5.5 秒），这属于完整 AST 恢复与安全防护的**正常预期开销，并非性能异常或卡死**。

---

## 五、 全量部署与升级操作步骤

### 步骤 1：上传发布包并校验 SHA256

将构建好的发布包 `tdsql-sqlcheck-v1.6.2.2-linux-x86_64.tar.gz` 及其 `.sha256` 校验文件上传至内网目标服务器 `/data/software/` 目录：

```bash
cd /data/software/
sha256sum -c tdsql-sqlcheck-v1.6.2.2-linux-x86_64.tar.gz.sha256
# 确认输出：tdsql-sqlcheck-v1.6.2.2-linux-x86_64.tar.gz: OK
```

### 步骤 2：解压安装包

```bash
tar -zxvf tdsql-sqlcheck-v1.6.2.2-linux-x86_64.tar.gz
cd tdsql-sqlcheck-v1.6.2.2-linux-x86_64
```

### 步骤 3：配置生产环境变量（`deploy/.env`）

若为全新部署，从模板复制 `.env` 并配置元数据库连接及管理员口令：

```bash
cp deploy/env.template deploy/.env
vi deploy/.env
```

核心配置项核对：
```ini
# 服务端口与绑定地址
HOST=0.0.0.0
PORT=8000

# 元数据库配置（必须为 MySQL 8.0+ 或 TDSQL）
SQLCHECK_DB_HOST=10.x.x.x
SQLCHECK_DB_PORT=3306
SQLCHECK_DB_USER=sqlcheck_meta
SQLCHECK_DB_PASSWORD=YourSecurePassword
SQLCHECK_DB_NAME=tdsql_sqlcheck
SQLCHECK_DB_POOL_SIZE=10

# 认证与请求体上限（已默认 50MB）
AUTH_ENABLED=true
MAX_BODY_BYTES=52428800
ADMIN_INITIAL_PASSWORD=Abcd1234
```

> [!NOTE]
> 如果是从旧版覆盖升级，可以直接复用并拷贝上一版本的 `deploy/.env` 文件。**请确保 `.env` 中没有残存的 `SCHEMA_CHECKSUM_RECONCILE` 变量**（该开关已废弃移除，预检若发现会提示拦截）。

### 步骤 4：执行一键安装脚本

以 `sudo` 权限运行 `deploy/install.sh`。该脚本会自动串联预检、解压依赖 wheels、执行数据库迁移与调和、更新 systemd 服务并拉起进程：

```bash
sudo ./deploy/install.sh
```

### 步骤 5：启动验证与日志核验

1. **检查服务状态**：
   ```bash
   sudo systemctl status tdsql-sqlcheck
   ```
2. **检查元数据 Checksum 自动调和日志（老库升级特有）**：
   ```bash
   journalctl -u tdsql-sqlcheck -n 100 | grep "一次性自动调和完成"
   # 预期看到类似日志：
   # 迁移 checksum 一次性自动调和完成 [v9_090_connection_unique]：54ee2e97... → c6cf33bb...，审计已落库
   ```
3. **执行一键部署验收脚本**：
   ```bash
   bash ./deploy/verify_deploy.sh
   # 预期全项 PASS，健康检查返回 HTTP 200，版本号 1.6.2.2
   ```

---

## 六、 回滚预案（Rollback）

若升级过程中遇到不可抗力需要回滚，可采用以下两种回滚路径：

### 路径 A：一键快速回滚（推荐）
系统部署时会在 `/opt/tdsql-sqlcheck-releases/` 中保留上一版本的备份快照：
```bash
sudo ./deploy/rollback.sh
```

### 路径 B：元数据 Checksum 手工复原（若回滚至 ≤v1.6.2.1）
如果需要彻底回退到 v1.6.2.1 并恢复老版本识别：
```bash
mysql -h <meta_host> -P <meta_port> -u <user> -p -e "
  UPDATE tdsql_sqlcheck.schema_migrations 
  SET checksum='54ee2e97c804f5d8ec216d9f51600c19cc8463f2cede1de07fa67635abe6de28' 
  WHERE version_key='v9_090_connection_unique' 
    AND checksum='c6cf33bb385456fef12af3d4888ea6b22dcfc2a64052d734adc4c37457915209';
"
```
旧版本启动后将继续正常运行。
