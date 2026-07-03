# TDSQL SQL审核平台 (V2.0)

面向商业银行生产环境的 TDSQL SQL 质量管控与慢 SQL 分析平台。
覆盖开发、测试、生产全生命周期，支持**纯内网部署**、**数百套数据库实例并存接入**、
**用户与角色权限管理**。

## V2.0 核心能力

### 🔐 认证与权限（V2.0新增）

- 登录认证：PBKDF2 口令哈希（240,000轮+随机盐）、HMAC 签名令牌、连续失败锁定
- 四级角色 RBAC：

| 角色 | 说明 | 权限 |
|------|------|------|
| admin | 系统管理员 | 全部操作 + 用户管理 |
| dba | 数据库管理员 | 连接/规则集/门禁/扫描/治理读写 |
| developer | 开发人员 | SQL审核/EXPLAIN分析 + 全局只读 |
| auditor | 审计员 | 全局只读（合规审计岗） |

- 操作审计：所有变更操作记录操作人、IP、时间（operation_logs），审核历史带用户身份
- 口令策略：≥8位、大小写/数字/特殊字符至少三类、首次登录强制修改、失败5次锁定15分钟

### 🗄️ 多实例连接管理（V2.0重构）

- 连接注册表：`connection_id → 连接池`，数百实例并存，LRU淘汰+空闲回收
- 连接配置持久化到系统 MySQL 元数据库，密码 **Fernet AES 加密**存储（密钥来自环境变量/密钥文件）
- 所有查询类 API 支持 `connection_id` 参数路由到指定实例
- 扫描限流：按连接 + 全局双重并发信号量，保护目标库

### 📝 SQL审核（77条规则 / 8大分类）

基于《TDSQL数据库开发规范》构建：

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

**规则集多租户（V2.0新增）**：不同项目/团队/环境可绑定不同规则集，
按规则集覆盖规则启停与严重级别（如开发环境将 SELECT* 降级为 INFO）。

### 🐌 慢SQL分析

基于《TDSQL-MySQL慢查询发现与优化方案》：

- 数据源：Proxy 层 `performance_schema` digest 聚合 + `processlist` 多次轮询采样
  （`mysql.slow_log` 在TDSQL分布式实例中不可用，已废弃）
- EXPLAIN 执行计划分析、SQL 文本静态分析、索引推荐、SQL 改写建议
- 多 SET 支持：SET 发现、跨 SET 对比分析
- **入库脱敏（V2.0新增）**：SQL 文本字面量替换为 `?`，客户敏感数据不落地
- **按连接扫描计划（V2.0新增）**：每个实例独立配置每日扫描时间，调度器 leader 租约防多副本重复执行

### 🧹 数据治理（V2.0新增）

- 数据保留策略：慢SQL/审核历史/告警/操作日志按表配置保留天数，每日自动清理
- 大表治理：L1/L2/L3 三级分类、分区水位、变更管控

### 📊 可观测性（V2.0新增）

- `/metrics` Prometheus 指标：HTTP请求/耗时、审核量、违规分布、扫描任务、登录、RBAC拒绝、活跃连接数
- X-Request-ID 请求链路标识 + 结构化访问日志

### 🔗 GitLab集成 / 质量门禁 / 项目管理

- MR Webhook 自动审核（V2.0：未配置 Secret 默认拒绝，杜绝裸奔）
- 质量门禁：strict/normal/loose 三策略，ERROR 违规阻断发版
- 项目绑定规则集与门禁策略

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python 3.11+ / FastAPI |
| SQL解析 | sqlglot |
| 数据库连接 | pymysql |
| 加密 | cryptography (Fernet AES) |
| 前端 | Vue 3 / Element Plus / ECharts（**全部本地化，纯内网可用**） |
| 存储 | MySQL 5.7+/8.0（V2.1 系统元数据库，支撑生产级并发与多副本；环境变量 SQLCHECK_DB_* 配置） |

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 生产环境必要配置

```bash
# 系统元数据库（V2.1: MySQL，替代SQLite）
export SQLCHECK_DB_HOST=127.0.0.1
export SQLCHECK_DB_PORT=3306
export SQLCHECK_DB_USER=sqlcheck
export SQLCHECK_DB_PASSWORD='<元数据库口令>'
export SQLCHECK_DB_NAME=tdsql_sqlcheck   # 不存在时自动创建
# 认证令牌签名密钥（多副本必须统一；未配置则自动生成密钥文件）
export AUTH_SECRET_KEY='<从KMS/配置中心注入的随机密钥>'
# 连接密码加密密钥
export TDSQL_ENCRYPTION_KEY='<Fernet密钥，openssl/KMS生成>'
# 初始管理员口令（仅首次启动生效；未配置则随机生成并打印日志一次）
export ADMIN_INITIAL_PASSWORD='<初始口令>'
```

### 3. 启动服务

```bash
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

### 4. 访问

- **前端界面**: http://localhost:8000 （登录页，默认账户 admin）
- **API文档**: http://localhost:8000/docs
- **健康检查**: http://localhost:8000/health
- **Prometheus指标**: http://localhost:8000/metrics

## API接口示例

### 登录与认证

```bash
# 登录获取令牌
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "<口令>"}' | jq -r .token)

# 后续请求携带令牌
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/auth/me
```

### SQL审核

```bash
curl -X POST http://localhost:8000/api/v1/audit/sql \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"sql": "SELECT * FROM t_user ORDER BY RAND()", "project_id": "my_project"}'
```

### 多实例连接管理

```bash
# 保存连接配置（密码加密存储）
curl -X POST http://localhost:8000/api/v1/tdsql/connections \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name": "生产核心库", "host": "10.0.0.1", "port": 15000,
       "user": "audit_ro", "password": "xxx", "database": "core_db"}'

# 按连接ID连接与查询
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/v1/tdsql/connections/<conn_id>/connect
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/v1/tdsql/tables?connection_id=<conn_id>"

# 抓取慢SQL（带限流保护和入库脱敏）
curl -X POST http://localhost:8000/api/v1/tdsql/slow-queries/fetch \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"source": "digest", "connection_id": "<conn_id>", "limit": 50,
       "time_window_start": "2026-07-01 00:00:00",
       "time_window_end": "2026-07-02 00:00:00"}'

# 为连接配置每日定时扫描计划
curl -X POST http://localhost:8000/api/v1/tdsql/scan-schedules \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"connection_id": "<conn_id>", "source": "digest",
       "cron_hour": 2, "cron_minute": 30}'
```

### 规则集与数据治理

```bash
# 创建规则集（R012禁用示例）
curl -X POST http://localhost:8000/api/v1/rulesets \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"id": "dev_loose", "name": "开发环境规则集",
       "items": [{"rule_id": "R012", "enabled": false}]}'

# 数据保留策略
curl -X PUT http://localhost:8000/api/v1/admin/retention \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"table_name": "slow_queries", "retention_days": 180}'
```

## 项目结构

```
tdsql-sqlcheck/
├── backend/
│   ├── main.py                     # FastAPI入口（中间件/路由/静态资源）
│   ├── config.py                   # 配置管理（V2.0动态安全配置）
│   ├── middleware.py               # V2.0 认证/RBAC/请求ID/指标/审计中间件
│   ├── cli.py                      # CLI工具（CI流水线集成）
│   ├── api/                        # API路由层
│   │   ├── auth.py                 # V2.0 认证与用户管理
│   │   ├── rulesets.py             # V2.0 规则集管理
│   │   ├── admin.py                # V2.0 系统管理（保留策略/操作日志）
│   │   ├── sql_audit.py            # SQL审核
│   │   ├── slow_query.py           # 慢SQL
│   │   ├── tdsql_manage.py         # TDSQL多实例管理
│   │   ├── gitlab_hook.py          # GitLab Webhook
│   │   └── ...                     # dashboard/project/bigtable/gate/monitor/inspection
│   ├── engine/                     # 核心引擎（无外部依赖，可独立测试）
│   │   ├── parser.py               # sqlglot SQL解析
│   │   ├── checker.py              # 规则检查器（V2.0支持规则集覆盖）
│   │   ├── slow_analyzer.py        # 慢SQL分析
│   │   ├── fingerprint.py          # SQL指纹/脱敏归一化
│   │   └── rules/                  # 77条规则库（8分类）
│   └── services/                   # 服务层
│       ├── auth_service.py         # V2.0 认证授权/用户管理/权限矩阵
│       ├── connection_registry.py  # V2.0 多实例连接注册表
│       ├── scan_service.py         # V2.0 扫描服务（限流/脱敏）
│       ├── ruleset_service.py      # V2.0 规则集服务
│       ├── retention_service.py    # V2.0 数据保留服务
│       ├── metrics_service.py      # V2.0 Prometheus指标
│       ├── security_service.py     # 密码加密（V2.0密钥管理）
│       ├── scheduler.py            # 调度器（V2.0 leader租约+扫描计划）
│       └── ...                     # audit/slow_query/gate/bigtable/database等
├── frontend/
│   ├── index.html                  # Vue3 SPA（V2.0登录页+用户管理）
│   └── static/vendor/              # V2.0 本地化前端资产（纯内网可用）
├── tests/                          # 821个测试（含V2.0冒烟/SIT/UAT，766通过55环境跳过）
├── docs/                           # 完整中文文档套件
├── smoke_test.py                   # 独立冒烟测试脚本（83项）
├── docker-compose.yml
└── README.md
```

## 运行测试

```bash
# 全量测试（766 通过 / 55 跳过[需Docker MySQL] / 0 失败）
python -m pytest tests/ -q

# V2.0专项：认证RBAC / 平台能力 / SIT / UAT
python -m pytest tests/test_v2_auth.py tests/test_v2_platform.py \
                 tests/test_v2_sit.py tests/test_v2_uat.py -v

# 独立冒烟脚本（83项检查）
python smoke_test.py
```

## 安全配置清单（生产上线必读）

| 环境变量 | 默认值 | 生产要求 |
|----------|--------|----------|
| `AUTH_ENABLED` | true | **必须保持 true** |
| `AUTH_SECRET_KEY` | 自动生成密钥文件 | 从KMS注入，多副本统一 |
| `TDSQL_ENCRYPTION_KEY` | 自动生成密钥文件 | 从KMS注入并备份 |
| `ADMIN_INITIAL_PASSWORD` | 随机生成打印一次 | 首次部署设置并立即修改 |
| `DATA_MASKING_ENABLED` | true | **必须保持 true**（敏感数据不落地） |
| `GITLAB_WEBHOOK_SECRET` | 空(拒绝webhook) | 必须配置 |
| `DOCS_PUBLIC` | true | 建议 false（API文档需认证） |
| `CORS_ALLOW_ORIGINS` | 空(同源) | 按需最小化配置 |

完整配置矩阵与部署架构见 [docs/部署手册.md](docs/部署手册.md)。

## 文档

| 文档 | 说明 |
|------|------|
| [docs/V2.0银行级改造设计说明书.md](docs/V2.0银行级改造设计说明书.md) | V2.0改造背景/架构/差距闭环 |
| [docs/安全与权限设计说明书-V2.0.md](docs/安全与权限设计说明书-V2.0.md) | 认证/RBAC/密钥/审计设计 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 系统架构 |
| [docs/部署手册.md](docs/部署手册.md) / [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | 部署与运维 |
| [docs/功能使用手册.md](docs/功能使用手册.md) / [docs/USER_GUIDE.md](docs/USER_GUIDE.md) | 功能使用 |
| [CONTEXT.md](CONTEXT.md) | 领域术语表（统一语言） |

## 参考资料

- 《TDSQL数据库开发规范》
- 《TDSQL-MySQL慢查询发现与优化方案》
- 《TDSQL大表定义与清理治理规范》
- 《北京农商银行SQL审核平台建设与运维实践》
