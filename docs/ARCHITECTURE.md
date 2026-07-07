# TDSQL SQL审核平台 - 系统架构文档 (V2.0)

## 1. 系统架构图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                            用户层 (内网)                                  │
│   浏览器(Vue3 SPA)   GitLab Webhook   CI流水线(CLI)   Prometheus抓取      │
└──────────┬───────────────┬───────────────┬───────────────┬──────────────┘
           ▼               ▼               ▼               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     FastAPI Server (:8000)                               │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │ RequestContextMiddleware: X-Request-ID / 访问日志 / 指标采集         │ │
│  ├────────────────────────────────────────────────────────────────────┤ │
│  │ AuthMiddleware: Bearer令牌验签 → 用户状态 → RBAC矩阵 → 操作审计      │ │
│  │   角色: admin / dba / developer / auditor                          │ │
│  └────────────────────────────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────────────────┤
│  API路由层 (14模块)                                                       │
│  auth  sql_audit  slow_query  tdsql_manage  rulesets  admin              │
│  dashboard  gitlab_hook  rules  project  bigtable  gate  monitor  insp   │
├─────────────────────────────────────────────────────────────────────────┤
│  服务层                                                                   │
│  ┌──────────────────────┐  ┌──────────────────┐  ┌───────────────────┐  │
│  │ ConnectionRegistry   │  │ AuthService      │  │ Scheduler         │  │
│  │ conn_id→池(数百实例)  │  │ 用户/口令/令牌    │  │ leader租约        │  │
│  │ LRU/空闲回收          │  │ 权限矩阵          │  │ 扫描计划(每分钟)   │  │
│  │ 扫描槽位限流          │  │ 登录锁定          │  │ 保留清理(每日)     │  │
│  └──────────┬───────────┘  └──────────────────┘  └───────────────────┘  │
│  ┌──────────┴───────────┐  ┌──────────────────┐  ┌───────────────────┐  │
│  │ ScanService          │  │ RulesetService   │  │ RetentionService  │  │
│  │ 扫描编排/入库脱敏      │  │ 规则集多租户      │  │ 数据生命周期       │  │
│  └──────────────────────┘  └──────────────────┘  └───────────────────┘  │
│  AuditService  SlowQueryService  GateService  MetricsService             │
│  SecurityService(Fernet加密)  ReportService(PDF)  BigTable/Monitor/Insp  │
├─────────────────────────────────────────────────────────────────────────┤
│  引擎层（纯逻辑，可独立测试）                                               │
│  SQLParser(sqlglot+正则双轨)  RuleChecker(77规则+规则集覆盖)               │
│  SlowSQLAnalyzer  FingerprintEngine(指纹/脱敏)  IndexAdvisor  SQLRewriter │
│  规则库: naming(5) ddl(22) dml(9) index(10) distributed(14) security(8)  │
│         performance(5) transaction(4)                                   │
├─────────────────────────────────────────────────────────────────────────┤
│  数据层                                                                   │
│  SQLite data/tdsql_check.db (27表, WAL)   TDSQL远程(只读账号)   PDF报告   │
│  密钥文件 data/*.key (0600, 不入库不入Git)                                │
└─────────────────────────────────────────────────────────────────────────┘
```

## 2. 系统组件说明

### 2.1 中间件层（V2.0新增）

| 组件 | 文件 | 说明 |
|------|------|------|
| RequestContextMiddleware | `backend/middleware.py` | 请求ID透传/生成、访问日志、Prometheus指标 |
| AuthMiddleware | `backend/middleware.py` | 令牌认证、RBAC权限判定、变更操作审计 |

### 2.2 API路由层

| 路由文件 | 路径 | 功能 | 写权限 |
|----------|------|------|--------|
| `auth.py` | `/api/v1/auth/*` | 登录/用户管理 (V2.0) | 用户管理仅admin |
| `sql_audit.py` | `/api/v1/audit/*` | SQL/文件审核 | developer+ |
| `slow_query.py` | `/api/v1/slow-queries/*` | 慢SQL管理、分析 | dba+（analyze-explain: developer+） |
| `tdsql_manage.py` | `/api/v1/tdsql/*` | 多实例连接/元数据/扫描/扫描计划 | dba+ |
| `rulesets.py` | `/api/v1/rulesets/*` | 规则集管理 (V2.0) | dba+ |
| `admin.py` | `/api/v1/admin/*` | 保留策略/操作日志/系统信息 (V2.0) | dba+ |
| `dashboard.py` `rules.py` `project.py` `bigtable.py` `quality_gate.py` `monitor.py` `inspection.py` `gitlab_hook.py` | ... | 同V1.0 | dba+（webhook走Secret） |

### 2.3 服务层核心组件

| 服务 | 文件 | 功能 |
|------|------|------|
| ConnectionRegistry | `connection_registry.py` | V2.0 多实例注册表：conn_id→池、LRU淘汰、空闲回收、扫描槽位限流、加密持久化 |
| AuthService | `auth_service.py` | V2.0 用户管理、PBKDF2口令、HMAC令牌、权限矩阵、登录锁定 |
| ScanService | `scan_service.py` | V2.0 扫描编排（API与调度器共用）、入库脱敏 |
| RulesetService | `ruleset_service.py` | V2.0 规则集CRUD与覆盖解析 |
| RetentionService | `retention_service.py` | V2.0 保留策略与过期清理 |
| MetricsService | `metrics_service.py` | V2.0 进程内指标、Prometheus文本渲染 |
| SecurityService | `security_service.py` | Fernet加密（V2.0密钥管理：env→密钥文件→遗留兼容） |
| Scheduler | `scheduler.py` | V2.0 leader租约、按连接扫描计划、每日保留清理 |
| AuditService / SlowQueryService / GateService / ReportService | ... | 同V1.0（V2.0增加用户身份/规则集/门禁联动） |

### 2.4 数据层（27张表）

| 分组 | 表 |
|------|-----|
| 审核 | audit_history, audit_results, rule_configs, rule_whitelist |
| 规则集 (V2.0) | rule_sets, rule_set_items |
| 慢SQL | slow_queries, scan_tasks, fingerprint_stats, optimization_records |
| 连接与调度 | tdsql_connections(密码加密), scan_schedules (V2.0), scheduler_lease (V2.0) |
| 用户与审计 (V2.0) | users, operation_logs |
| 门禁 | gate_rules, gate_audit_logs |
| 治理 | bigtable_inventory, bigtable_classification, partition_watermarks, change_controls, retention_policies (V2.0) |
| 监控巡检 | alerts, alert_rules, inspection_tasks, inspection_results |
| 其他 | projects, schema_version |

## 3. 关键数据流

### 3.1 认证 + 审核数据流（V2.0）

```
用户登录(/auth/login) ──▶ AuthService.authenticate ──▶ 签发HMAC令牌
    │
    ▼ Bearer令牌
POST /audit/sql {sql, project_id}
    ├─ AuthMiddleware: 验签→用户状态→RBAC(developer可写audit)
    ├─ AuditService: project_id → RulesetService.get_overrides (规则集覆盖)
    ├─ RuleChecker.audit_sql(sql, rule_overrides) → 77规则(过滤/级别覆盖)
    ├─ GateService.evaluate(violations) → 门禁结果
    └─ audit_history落库(created_by=登录用户) + metrics + operation_log
```

### 3.2 多实例慢SQL扫描数据流（V2.0）

```
POST /tdsql/slow-queries/fetch {connection_id, source, time_window}
    ├─ ScanService.run_scan
    │    ├─ registry.get(connection_id)  ← 未激活时按已保存配置自动建连(解密密码)
    │    ├─ registry.scan_slot(conn)     ← 并发限流(单连接2/全局8, 超限429)
    │    ├─ digest: perf_schema聚合 / processlist: 多次轮询采样去重
    │    ├─ SlowQueryService.add_slow_query ← DATA_MASKING: 字面量→? 后落库
    │    └─ scan_tasks 任务记录 + metrics
    └─ 定时路径: Scheduler(仅leader) 每分钟检查 scan_schedules 到期计划 → 同上
```

## 4. 部署形态

- **单副本**（当前，SQLite存储）：uvicorn/容器 + data目录持久化 + 定期备份
- **多副本预留**：调度器leader租约已实现；需先完成集中式存储迁移（见V2.0设计说明书§5）
- 纯内网：前端资产本地化（frontend/static/vendor），无任何外网依赖
- 观测：/metrics 接入行内Prometheus，operation_logs 对接SIEM

## 5. 技术栈汇总

| 层级 | 技术 | 版本 |
|------|------|------|
| 后端框架 | FastAPI + Uvicorn | ≥0.115 |
| Python | Python | 3.11+ |
| SQL解析 | sqlglot | ≥26.0 |
| 数据库连接 | pymysql | ≥1.1 |
| 加密 | cryptography (Fernet) | ≥41.0 |
| 任务调度 | APScheduler | ≥3.10 |
| PDF生成 | reportlab | ≥4.0 |
| 前端 | Vue 3.4 / Element Plus 2.7 / ECharts 5.5（本地化） | - |
| 本地存储 | SQLite (WAL) | 3.x |

## 6. 版本历史

| 版本 | 日期 | 更新内容 |
|------|------|----------|
| 1.0 | 2026-06 | 77条规则、慢SQL六维分析、大表治理、质量门禁、多SET扫描 |
| 2.0 | 2026-07-03 | 银行级改造：认证与RBAC、多实例连接注册表、规则集多租户、数据脱敏与保留、可观测性、调度leader租约、前端内网化、密钥管理 |
| 2.1 | 2026-07-07 | Oracle迁移TDSQL规范接入：新增42条Oracle迁移兼容规则(R078-R119)，规则总数77→119，新增oracle_compat分类 |
