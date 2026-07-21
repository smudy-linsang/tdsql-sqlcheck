# TDSQL SQL审核平台 v1.2 概要设计说明书

> **版本基线**: v1.1.0.1（v1.1.0.0 缺陷整改完成版）
> **目标版本**: v1.2.0.0
> **文档类型**: 概要设计说明书（与《详细设计说明书 v1.2》配套使用）
> **作者**: Mavis（Mavis 团队） / 审核: 待定
> **更新日期**: 2026-07-21

---

## 0. 文档目的

v1.1.0.1 已经把 8 个 BUG 全部整改闭环（参考 `docs/v1.1.0.0_缺陷整改复测清单.md`），系统进入"功能稳定期"。

本概要设计聚焦 **v1.1.0.1 → v1.2.0.0 的架构级改造**，解决 4 个累积问题：
1. **前端工程化** — 从 110KB 单 HTML + 80KB 单 JS 重构为 Vite + Vue 3 SFC
2. **数据 schema 文件化** — 1500+ 行硬编码 DDL 抽离为可版本化迁移
3. **核心模块拆分** — parser / tdsql_connector / 规则库按职责瘦身
4. **测试 + 工具集成** — 补齐 RBAC 矩阵测试 + 桥接 tdsql-toolkit 离线工具集

文档配套有《详细设计说明书 v1.2》，按"照图施工"标准逐文件、逐函数、逐 SQL 写明。

---

## 1. 设计目标与原则

### 1.1 业务目标（不变）
- 覆盖开发/测试/生产全生命周期的 TDSQL SQL 质量管控与慢 SQL 治理
- 银行级：纯内网、数百套实例、四角色 RBAC、操作审计
- 不破坏 v1.1.0.1 已通过的 985 用例

### 1.2 架构原则

| 原则 | v1.2 落地策略 |
|---|---|
| **向前兼容** | 已有 119 条规则 ID、27+ 张业务表、所有 API 路径与响应体不变 |
| **可灰度** | 新前端用 `?ui=v2` 路径灰度，默认仍走 v1.1.0.1 的单页 |
| **可回滚** | 任意子模块的回滚不影响其它模块（`schema_version` 精确记录） |
| **可观测** | 所有拆分/迁移过程在 `/health` 与 `/metrics` 输出新指标 |
| **照图施工** | 每项改造都给出目录结构、文件清单、关键函数签名 |

---

## 2. 现状盘点（v1.1.0.1）

| 维度 | 现状 | 数据 |
|---|---|---|
| 后端 Python 文件 | backend/ 共 ~50 个 .py | backend/ 目录约 600KB |
| API 路由模块 | 24 个 v1 路由 | `backend/api/`，最大单文件 33KB |
| 服务模块 | 32 个 service + 2 子目录 | `backend/services/`，最大 tdsql_connector.py 71KB |
| 引擎层 | 1 个 parser（30KB）+ 1 个 checker（13KB）+ 10 个规则文件 | `backend/engine/` |
| 规则数 | 119 条（R001-R119） | 9 大分类 |
| 数据表 | 27+ 张业务表 | 全部 DDL 硬编码在 `database.py` |
| 元数据库 | TDSQL 集中式（MySQL 协议） | V2.1 起迁移 |
| 前端 | 单 index.html (110KB) + app.js (80KB) + app.css (10KB) | 无构建工具 |
| 静态资源 | vue / element-plus / echarts 全本地化 | 6 个 vendor 文件 |
| 测试 | 985 passed（v1.1.0.1 实测） | `tests/` |
| 文档 | 40+ 份 Markdown/Word | `docs/` |
| 部署 | make_release + install + preflight + verify + rollback | `deploy/` 5 脚本 |

---

## 3. v1.2 改造全景

### 3.1 改造清单（按优先级）

| # | 改造主题 | 范围 | 预计代码量 | 风险 | 兼容策略 |
|---|---|---|---|---|---|
| **C1** | 数据库 schema 文件化 | backend | ~2K 行 | 低 | 启动时检测 + 一次性迁移 |
| **C2** | engine/parser 拆分 | backend | ~600 行 | 中 | parser 公开 API 不变 |
| **C3** | tdsql_connector 拆分 | backend | ~1.5K 行 | 中 | ConnectionPool 类 API 不变 |
| **C4** | 前端 Vite 工程化 | frontend | ~3K 行（重写） | 中 | `/` 默认 v1；`/?ui=v2` 灰度 |
| **C5** | RBAC 矩阵单测补齐 | tests | ~800 行 | 低 | 仅新增 |
| **C6** | tdsql-toolkit 桥接 | backend + tools | ~1K 行 | 中 | 旧 .sh 脚本保持可用 |

### 3.2 时间线

```
Week 1:  C1 (schema文件化) + C2 (parser拆分)
Week 2:  C3 (connector拆分) + C5 (RBAC单测)
Week 3:  C4 (前端工程化) - 灰度
Week 4:  C6 (toolkit桥接) - 灰度 + 收尾
```

---

## 4. 总体架构图（v1.2）

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          用户层 (内网)                                     │
│   浏览器(Vue 3 SPA v2)  GitLab Webhook   CLI  Prometheus抓取              │
│   浏览器(单页 v1 兜底)                                                       │
└───────┬─────────────────┬──────────────┬──────────────────┬──────────────┘
        ▼                 ▼              ▼                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                    FastAPI Server (:8000)                                 │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │ RequestContextMiddleware: X-Request-ID / 访问日志 / 指标采集        │  │
│  ├────────────────────────────────────────────────────────────────────┤  │
│  │ AuthMiddleware: Bearer令牌验签 → 用户状态 → RBAC矩阵 → 操作审计      │  │
│  │   角色: admin / dba / developer / auditor（菜单级 v3 增强）         │  │
│  └────────────────────────────────────────────────────────────────────┘  │
├──────────────────────────────────────────────────────────────────────────┤
│  API 路由层 (24 模块 / FastAPI Router)                                    │
│   auth  sql_audit  slow_query  tdsql_manage  rulesets  admin             │
│   dashboard  gitlab_hook  rules  project  bigtable  gate  monitor  insp │
│   cluster_inspect  index_audit  schema_diff  emergency  daily_inspect    │
│   sql_stats  zk_discovery  gateway_log  ppt_report  toolkit              │
├──────────────────────────────────────────────────────────────────────────┤
│  服务层 (32 services, 按职责重新分组)                                       │
│  ┌────────────────────┐ ┌─────────────────────┐ ┌─────────────────────┐ │
│  │ ConnectionRegistry │ │ AuthService         │ │ Scheduler            │ │
│  │  多实例池/LRU/限流  │ │  PBKDF2/HMAC/RBAC   │ │  Leader租约          │ │
│  └────────────────────┘ └─────────────────────┘ └─────────────────────┘ │
│  AuditService  SlowQueryService  GateService  RulesetService             │
│  RetentionService  MetricsService  ReportService  ToolBridge（新）         │
├──────────────────────────────────────────────────────────────────────────┤
│  引擎层 (拆分重构)                                                          │
│  SQLParser (sqlglot)        ─┐                                         │
│  PreParser (正则预解析, 新拆)─┴─→ RuleChecker (77+42 规则)                │
│  SlowSQLAnalyzer  FingerprintEngine  IndexAdvisor  SQLRewriter          │
│  规则库: 10 个 .py 分类文件  共 119 条                                    │
├──────────────────────────────────────────────────────────────────────────┤
│  连接器层 (拆分重构)                                                        │
│  TDSQLConnectionPool (连接池, 不变)                                       │
│  MetadataFetcher (表/索引/分片键, 拆出)                                   │
│  SlowQueryFetcher (digest/processlist, 拆出)                            │
│  MonitorDBClient (monitordb 15001, 拆出)                                │
│  ProxyClient (/*proxy*/show config, 拆出)                                │
├──────────────────────────────────────────────────────────────────────────┤
│  数据层                                                                     │
│  MySQL 元数据库 (TDSQL 集中式, 27+ 表)                                     │
│  schema/ 目录下 SQL 文件 + schema_migrations 迁移表                       │
│  SQLite 兼容路径 (开发模式, v1.1 行为保留)                                │
│  目标 TDSQL 实例 (3306)  monitordb (15001)  网关日志                      │
└──────────────────────────────────────────────────────────────────────────┘
```

### 4.1 与 v1.1.0.1 的差异

| 区域 | v1.1.0.1 | v1.2 |
|---|---|---|
| 前端入口 | 单一 `index.html` + 单一 `app.js` | Vite 打包 + `/?ui=v2` 灰度入口 + v1 单页保留 |
| Parser | 单文件 30KB 含正则预解析 + AST | `parser.py` (AST) + `pre_parser.py` (正则) |
| Connector | 71KB 单类 | 5 个模块化类，按数据源/操作拆分 |
| 数据库 DDL | `database.py` 内 ~1500 行字符串 | `schema/v1*.sql` 文件 + 启动期迁移工具 |
| RBAC 测试 | 14KB（约 4 用例） | 800+ 行矩阵用例 |
| 工具集 | 独立的 `tdsql-toolkit-main/` | 平台内 `ToolBridge` 远程调用 + 任务编排 |

---

## 5. 详细改造方案概要

### 5.1 C1 — 数据库 schema 文件化

**目标**：把 `services/database.py` 中 ~1500 行 DDL 字符串抽到 `schema/` 目录下的 SQL 文件，配合 `schema_migrations` 表做版本化迁移。

**核心结构**：
```
schema/
├── v0/                    # 初始版本（与 v1.1.0.1 一致）
│   ├── 001_init.sql       # 27 张表
│   └── 002_seed.sql       # 默认数据（角色/权限）
├── v1/                    # v1.2 增量
│   ├── 010_alter.sql      # 字段加宽/索引
│   └── 011_seed.sql
└── schema.toml            # 元信息（描述/作者/依赖）
```

**迁移机制**：
- 启动时检查 `schema_migrations` 表，对比 `schema_version` 视图与磁盘文件
- 按版本号顺序执行未执行过的 `.sql` 文件
- 每个文件单事务；失败时记录 `schema_migration_errors` 表
- 完整支持回滚（备份旧数据 → 应用新 schema → 校验 → 切换）

**不破坏**：
- 27 张表的表名、字段名、索引名 100% 不变
- 所有 SQL 仍走 `_MySQLCompatConnection` 包装器，`?` → `%s` 自动转换仍生效

### 5.2 C2 — engine/parser 拆分

**目标**：把当前 30KB 的 `engine/parser.py` 中的正则预解析逻辑抽到独立 `engine/pre_parser.py`，让 parser 聚焦 AST 解析。

**拆分原则**：
| 原 parser.py 内容 | 拆分后归属 |
|---|---|
| `_regex_pre_parse()` 方法 | → `pre_parser.py::PreParser.run(parsed, sql)` |
| `_parse_select/_insert/_update/_delete/_create/_alter/_drop` | 保留 parser.py（AST 驱动） |
| `_parse_common()` | 保留 |
| `_extract_tables()` | 保留（AST 回退） |
| `_detect_sql_type_regex()` | 保留 parser.py（fallback 用） |
| `ParsedSQL` dataclass | 拆出到 `engine/parsed_sql.py`（与 parser 解耦） |

**接口兼容**：
- `RuleChecker` 仍调 `parser.parse(sql)` 返回 `ParsedSQL`
- `ParsedSQL` 字段集 100% 不变
- 新增 `pre_parser.py` 的内部函数可通过 `parser.py` 转发调用，零侵入

### 5.3 C3 — tdsql_connector 拆分

**目标**：71KB 单类拆成 5 个职责清晰的模块，公共 API 保持兼容。

```
backend/services/connector/                # 新建子包
├── __init__.py            # 重导出 TDSQLConnectionPool (向后兼容)
├── pool.py                # TDSQLConnectionPool (核心, 不动签名)
├── metadata.py            # MetadataFetcher (表/索引/分片键/分区)
├── slow_query.py          # SlowQueryFetcher (digest/processlist)
├── monitor_db.py          # MonitorDBClient (monitordb 15001)
├── proxy.py               # ProxyClient (/*proxy*/show config/内省)
└── shard.py               # ShardKeyInspector (从 DDL 提取分片键)
```

**改造要点**：
- 旧的 `tdsql_connector.py` 变为**薄壳**，仅做 `from .connector import *` 转发
- 每个新模块单文件 ≤ 20KB，单测独立
- 公共方法（`get_tables`, `get_table_metadata`, `get_proxy_config` 等）签名 100% 不变
- ConnectionPool 内部 0 改动

### 5.4 C4 — 前端 Vite 工程化

**目标**：把单 HTML + 单 JS 改造为现代前端工程，支持按页懒加载、组件化、构建优化。

**新结构**：
```
frontend/
├── index.html                # v1 单页 (兜底)
├── src/                      # 新 v2 源码
│   ├── main.ts               # Vue 3 入口
│   ├── App.vue               # 根组件 (含路由 + 顶栏 + 侧边栏)
│   ├── router/
│   │   └── index.ts          # 路由表 (按权限懒加载)
│   ├── views/                # 19 个页面 (SFC)
│   │   ├── Dashboard.vue
│   │   ├── audit/
│   │   │   ├── SqlAudit.vue
│   │   │   ├── FileAudit.vue
│   │   │   └── Rules.vue
│   │   ├── slow/
│   │   ├── instance/
│   │   ├── platform/
│   │   ├── system/
│   │   └── deep-diag/        # 9 个深度诊断子页
│   ├── components/           # 公共组件 (KpiCard / TrendChart / ...)
│   ├── stores/               # Pinia 状态管理
│   │   ├── auth.ts           # 替代原 authState
│   │   ├── connection.ts
│   │   └── project.ts
│   ├── api/                  # 拆 apiFetch
│   │   ├── http.ts
│   │   ├── audit.ts
│   │   ├── slow.ts
│   │   └── ...
│   ├── utils/                # sevTagType / formatTime / ...
│   └── styles/
│       └── element-variables.scss
├── vite.config.ts
├── package.json
└── tsconfig.json
```

**灰度方案**：
- 默认 `/` 仍返回 v1 单页（`index.html`）
- `/?ui=v2` 命中后由后端中间件改写路径到 `/v2/index.html`（新构建产物）
- 后端通过响应头 `X-UI-Version` 标识；前端 v2 调用 `/api/v1/...` 不变
- 灰度比例通过 `system_config.ui_v2_rollout` 控制（默认 0%，逐步提升到 100%）

**bundle 目标**：
- 全量 bundle ≤ 800KB（gzipped），首屏 ≤ 200KB
- 各页面通过 `defineAsyncComponent` 懒加载
- ECharts 按需引入（仅 dashboard/慢SQL趋势 用到）

### 5.5 C5 — RBAC 矩阵单测

**目标**：把"凭手感配置"变成"矩阵验证"，覆盖四角色 × 27+ 业务接口 × 9 个深度诊断子菜单。

**新测试**（`tests/test_rbac_matrix.py`）：
```python
@pytest.mark.parametrize("role,method,path,expected", [
    # 4 角色 × 24 路由模块 × 5 HTTP 方法 = 480 行基础矩阵
    ("admin", "GET", "/api/v1/audit/history", 200),
    ("dba", "GET", "/api/v1/audit/history", 200),
    ("developer", "GET", "/api/v1/audit/history", 200),
    ("auditor", "GET", "/api/v1/audit/history", 200),
    ("developer", "POST", "/api/v1/connections", 403),  # 非允许写
    ("auditor", "DELETE", "/api/v1/admin/operation-logs", 403),
    # 9 个深度诊断子菜单权限细分
    ("dba", "GET", "/api/v1/gateway-log/reports", 200),
    ("dba", "GET", "/api/v1/gateway-log/reports/1", 200),  # 路径边界
    ("developer", "GET", "/api/v1/gateway-log/reports", 403),  # 撤权后
    # 越权回归 (BUG-01)
    ("developer", "GET", "/api/v1/gate/rules/default", 200),  # gate 自身放行
    ("developer", "GET", "/api/v1/gateway-log/reports", 403),  # gateway-log 拒
    # ... 800+ 行
])
def test_permission_matrix(role, method, path, expected, auth_token):
    response = client.request(method, path, headers={"Authorization": f"Bearer {auth_token[role]}"})
    assert response.status_code == expected
```

**配套**：
- `tests/fixtures/rbac_users.py` 预置 4 个测试用户
- `tests/fixtures/rbac_matrix.csv` 矩阵数据驱动
- 接入 CI，每次 PR 必跑

### 5.6 C6 — tdsql-toolkit 桥接

**目标**：让 Web 平台能调用 `tdsql-toolkit-main/` 里的 13 个离线分析模块，生成可点击的"运维分析任务"。

**架构**：
```
┌────────────────────┐
│  Web 前端           │  "运行 daily_inspection" 按钮
│  深度诊断 / 工具箱   │
└─────────┬──────────┘
          ▼
┌────────────────────┐
│  /api/v1/toolkit/   │  POST /run/{module_id}
│  run (FastAPI)     │  → ToolBridge
└─────────┬──────────┘
          ▼
┌────────────────────┐
│  ToolBridge (新)    │  SSH 远程执行 + 实时流式返回日志
│  异步任务编排       │
└─────────┬──────────┘
          ▼
┌────────────────────┐
│  目标管理节点 (scheduler) │
│  跑 .sh 脚本       │
└────────────────────┘
```

**新增文件**：
- `backend/services/tool_bridge/__init__.py`
- `backend/services/tool_bridge/registry.py`     # 13 个工具的元数据
- `backend/services/tool_bridge/runner.py`      # SSH 异步调用
- `backend/services/tool_bridge/scheduler.py`    # 定时任务
- `backend/api/toolkit.py` 增加 `/run` `/run/{id}/status` `/run/{id}/logs` 端点
- `tests/test_tool_bridge.py` 用 Mock 测

**兼容**：
- 老的 `/api/v1/toolkit/scripts` `/api/v1/toolkit/download` 仍可用（脚本列表/下载）
- 新增运行端点不影响老接口

---

## 6. 兼容性矩阵

| 维度 | v1.1.0.1 → v1.2 兼容情况 | 兼容手段 |
|---|---|---|
| API 路径 | 100% 不变 | router 注册顺序保留 |
| API 响应体 | 100% 不变 | Pydantic 模型字段不动 |
| 数据库表结构 | 100% 不变（27+ 表） | 启动期 `schema_migrations` 自动校验 |
| 规则 ID | 100% 不变（R001-R119） | 规则文件不改 |
| 前端 v1 单页 | 100% 可用 | `index.html` 兜底 |
| 配置项 | 新增（向后兼容） | `system_config` 新增条目带默认值 |
| CLI | 100% 不变 | `cli.py` 不改 |
| 部署脚本 | 100% 兼容 | 滚动升级不破坏 `/opt/tdsql-sqlcheck/{current -> releases/v1.2}` 链 |
| 监控 | 指标不破坏（新增） | `/metrics` 保留全部 v1.1.0.1 指标 |

---

## 7. 风险与缓解

| 风险 | 等级 | 缓解 |
|---|---|---|
| 启动期 schema 迁移阻塞 | 高 | 完整备份 + 预演（dry-run）模式 + 回滚脚本 |
| parser 拆分引入新 bug | 中 | 100% 测试覆盖 + 灰度开关 `parser.use_pre_parser=true/false` |
| connector 拆分影响慢 SQL 扫描 | 中 | 慢 SQL 端到端压测 + `connector.use_legacy_pool=true` 兜底 |
| 前端 v2 引入打包问题 | 中 | 与 v1 并存可独立回滚；构建产物进 `frontend/dist/` |
| RBAC 矩阵测试发现新 bug | 低 | 测试独立运行，发现 bug 立即修；不影响主流程 |
| toolkit SSH 调用权限/网络 | 中 | 默认 dry-run；需要目标节点 `sshpass` + token 配置 |

---

## 8. 文档结构

本概要设计配套：
- **《详细设计说明书 v1.2》**（`docs/DETAIL-v1.2.md`）—— 照图施工级，含目录结构、文件清单、代码骨架、SQL 语句、接口签名、迁移步骤
- **《v1.2 升级部署说明》**（待写）—— 升级步骤、验证清单、回滚方案
- **《v1.2 测试用例规范》**（待写）—— Smoke/SIT/UAT 三级用例

---

## 9. 总结

v1.2 的核心是 **"4 个改造" + "0 个破坏"**：

- **数据库 schema 文件化**：可版本化、可迁移、可审计
- **parser 拆分**：正则预解析与 AST 解析职责清晰
- **connector 拆分**：数据源/操作/协议分层
- **前端工程化**：单文件 → 模块化、按需加载、构建优化
- **测试 + 工具桥接**：质量门 + 工具生态

所有改造均在 v1.1.0.1 已通过 985 用例的基线上灰度推进，任意阶段可独立回滚。

详细到每个文件、每个函数、每条 SQL 的施工级设计见《详细设计说明书 v1.2》。
