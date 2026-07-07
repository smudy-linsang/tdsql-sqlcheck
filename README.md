# TDSQL数据库SQL审核工具

> **当前版本：v1.0.2**（首次生产上线版本）｜ 更新日期：2026-07-07
> 面向商业银行生产环境的 TDSQL SQL 质量管控与慢 SQL 治理平台，纯内网部署、支持数百套 TDSQL 实例纳管。

## 核心能力

### 📝 SQL 审核（119 条规则 / 9 大分类）

| 类别 | 规则数 | 说明 |
|------|--------|------|
| 命名规范 | 5 | 表名/列名长度、格式、保留字、复数 |
| DDL规范 | 22 | 主键、引擎、字符集、字段类型、注释 |
| DML规范 | 9 | SELECT*、无WHERE、子查询深度 |
| 索引规范 | 10 | 索引数量、冗余索引、前缀索引 |
| 分布式规范 | 14 | 分片键查询/更新/建表声明、跨SET操作 |
| 安全规范 | 8 | INTO OUTFILE、LOAD DATA、GRANT |
| 性能规范 | 5 | 函数索引失效、IN列表、隐式转换 |
| 事务规范 | 4 | 长事务、大事务、事务未提交 |
| **Oracle迁移兼容** | **42** | 依据 TDSQL 原厂《TDSQL兼容业务系统适配改造方案 V1.5.1》全量落地：to_char/nvl/decode/rownum/merge into/with as/窗口函数/分片键军规等，每条附原厂改写建议 |

- 5 个审核入口：即时审核、SQL/MyBatis-XML 文件审核、文件上传、GitLab（Diff/仓库/Webhook）、元数据增强审核（取真实分片键/索引提升精度）
- 审核报告 HTML/PDF 导出，历史可追溯
- **规则集多租户**：不同项目/团队/环境绑定不同规则集，按项目覆盖规则启停与严重级别
- **质量门禁**：ERROR/WARNING 阈值（strict/loose/自定义），门禁结果随审核返回

### 🐌 慢SQL治理闭环
- 多实例扫描（Proxy digest 聚合 / processlist 轮询采样）→ 六维分析 → 状态流转（待处理/已优化/已忽略）→ EXPLAIN 执行计划分析
- 定时扫描计划（按实例独立配置，调度器 leader 租约防多副本重复执行）
- 多 SET 支持：SET 发现、跨 SET 对比；SQL 文本入库脱敏（字面量→`?`）

### 🏛️ 实例与治理
- 连接注册表：数百实例并存（LRU+空闲回收），口令 Fernet AES 加密存储，扫描双重并发限流保护目标库
- 数据库体检（字符集一致性）、大表 L1/L2/L3 分级治理、巡检管理、业务监控告警与确认闭环
- 数据保留策略按表配置保留天数，每日自动清理

### 🔐 安全（银行级）
- 登录认证：PBKDF2 口令哈希（240,000 轮+盐）+ HMAC 自包含令牌；连续失败 5 次锁定 15 分钟；首登强制改密
- 四角色 RBAC + 权限矩阵：

| 角色 | 说明 | 权限 |
|------|------|------|
| admin | 系统管理员 | 全部操作 + 用户管理 |
| dba | 数据库管理员 | 实例/规则集/门禁/扫描/治理读写 |
| developer | 开发人员 | SQL审核/EXPLAIN + 受限只读 |
| auditor | 审计员 | 全局只读 + 操作审计（合规岗） |

- 操作审计（操作人/IP/时间）、前端资产全本地化**零外网请求**

### 📊 可观测
- `/health` 健康检查、`/metrics` Prometheus 指标（请求/审核量/违规分布/扫描/登录/RBAC拒绝/活跃连接）、X-Request-ID 链路标识

## 快速开始

### 生产部署（麒麟 V10 SP3 + TDSQL 集中式元数据库）

详见《[docs/部署手册-v1.0.2.md](docs/部署手册-v1.0.2.md)》。一键流程：

```bash
# 打包机
./deploy/make_release.sh --arch x86_64        # 或 aarch64
# 目标机
tar -xzf tdsql-sqlcheck-v1.0.2-linux-x86_64.tar.gz && cd tdsql-sqlcheck-*
cp deploy/env.template deploy/.env && vi deploy/.env   # 4 个必填项
sudo ./deploy/install.sh                                # 预检→安装→启动→自动验证
```

### 开发环境

```bash
pip install -r requirements.txt
export SQLCHECK_DB_HOST=... SQLCHECK_DB_PORT=... SQLCHECK_DB_USER=... \
       SQLCHECK_DB_PASSWORD=... SQLCHECK_DB_NAME=tdsql_sqlcheck_dev
uvicorn backend.main:app --reload --port 8000
pytest tests/ -q        # 880+ 用例
```

## 文档体系（出口版本统一 v1.0.2）

| 文档 | 说明 |
|---|---|
| [发布说明-v1.0.2](docs/发布说明-v1.0.2.md) | 版本定位、功能全景、已知限制 |
| [部署手册-v1.0.2](docs/部署手册-v1.0.2.md) | 麒麟V10SP3 一键部署（**部署实施主文档**） |
| [运维手册-v1.0.2](docs/运维手册-v1.0.2.md) | 启停/日志/监控/备份/故障/升级 |
| [上线检查清单-v1.0.2](docs/上线检查清单-v1.0.2.md) | Go-Live 逐项检查 |
| [用户使用手册](docs/USER_GUIDE.md)、[功能使用手册](docs/功能使用手册.md) | 角色与功能操作 |
| [系统架构说明](docs/ARCHITECTURE.md) | 架构与模块 |
| [接口设计](docs/系统接口设计说明书-v1.0.2.md)、[数据库设计](docs/系统数据库设计说明书-v1.0.2.md)、[概要设计](docs/系统概要设计说明书-v1.0.2.md)、[详细设计](docs/系统详细设计说明书-v1.0.2.md)、[安全与权限设计](docs/安全与权限设计说明书-v1.0.2.md) | 设计文档 |
| [全系统SIT-UAT测试用例](docs/全系统SIT-UAT测试用例.md) | 160 用例测试集 |
| docs/archive/ | 研发过程记录归档（历轮设计输入/验收/整改） |

## 目录结构

```
├── backend/                 # FastAPI 后端
│   ├── api/                 # 14 个路由模块 (/api/v1/*)
│   ├── engine/              # 审核引擎（sqlglot 解析器 + 119 条规则/9 分类）
│   ├── services/            # 认证/连接注册/规则集/扫描/保留/指标/调度
│   └── main.py              # 应用入口（/health、静态资源、启动自动建表）
├── frontend/                # Vue3 + Element Plus 单页（纯内网资产）
├── deploy/                  # 一键部署套件（打包/安装/预检/验证/回滚/中间件配置）
├── tests/                   # 880+ 自动化用例（含 Oracle 兼容专项 103 项）
└── docs/                    # v1.0.2 文档体系
```

## 技术栈与运行环境

- 后端：Python 3.9+（推荐 3.11）· FastAPI · sqlglot · PyMySQL · APScheduler · ReportLab
- 前端：Vue 3 · Element Plus · ECharts（全本地化）
- 元数据库：TDSQL 集中式实例（MySQL 协议）
- 部署：麒麟 V10 SP3 · systemd · 可选 Nginx（TLS）· 全离线安装

## 版本历史

| 版本 | 日期 | 说明 |
|---|---|---|
| **v1.0.2** | 2026-07-07 | 首次生产上线版本：119 条规则（含 Oracle迁移兼容 42 条）、慢SQL治理闭环、多实例纳管、四角色 RBAC、质量门禁、一键部署套件 |
