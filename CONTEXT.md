# TDSQL-SQLCheck 领域术语表 (CONTEXT.md)

> 本文件是项目的统一语言（Ubiquitous Language）参考。
> 所有代码命名、API 设计、文档编写、沟通讨论都应使用以下术语。
> 当发现术语缺失或不一致时，更新本文件。

## 项目定位

**TDSQL-SQLCheck** 是覆盖开发、测试、生产全生命周期的 TDSQL SQL 质量管控与慢 SQL 分析平台。基于《TDSQL 数据库开发规范》和《TDSQL-MySQL 慢查询发现与优化方案》构建。

当前版本：**V1.0**（76 条审核规则 / 8 大分类 / 3 级严重级别）

---

## 一、核心业务术语

### SQL 审核 (SQL Audit)

| 术语 | 英文 | 定义 |
|------|------|------|
| SQL 审核 | SQL Audit | 对 SQL 语句进行自动化规则检查，识别潜在风险 |
| 审核规则 | Audit Rule | 单条检查规则，如"表必须有主键"(R003) |
| 违规 | Violation | 规则检查发现的问题记录 |
| 审核结果 | Audit Result | 一次审核的完整输出，包含所有违规 |
| 审核历史 | Audit History | 持久化的审核记录，存于 SQLite audit_history 表 |
| 批量审核 | Batch Audit | 对文件或 MyBatis XML 中的多条 SQL 进行审核 |

### 审核规则分类 (Rule Categories)

| 术语 | 英文 | 规则数 | 说明 |
|------|------|--------|------|
| 命名规范 | Naming | 5 | 表名/列名长度、格式、保留字 |
| DDL 规范 | DDL | 22 | 建表语句：主键、引擎、字符集、字段类型 |
| DML 规范 | DML | 9 | 增删改查：SELECT*、无 WHERE、子查询深度 |
| 索引规范 | Index | 10 | 索引数量、冗余索引、前缀索引 |
| 分布式规范 | Distributed | 13 | 分片键查询、更新分片键、跨分片操作 |
| 安全规范 | Security | 8 | 权限、敏感数据、SQL 注入风险 |
| 性能规范 | Performance | 5 | 大表全表扫描、ORDER BY RAND、笛卡尔积 |
| 事务规范 | Transaction | 4 | 长事务、自动提交、事务隔离级别 |

### 严重级别 (Severity)

| 级别 | 含义 | 处理方式 |
|------|------|---------|
| ERROR | 必须修复，阻断发版 | 质量门禁拦截 |
| WARNING | 建议修复，不阻断 | 提示开发者 |
| INFO | 提示信息，供参考 | 记录但不强制 |

### 质量门禁 (Quality Gate)

| 术语 | 定义 |
|------|------|
| 质量门禁 | 发版前的强制 SQL 审核检查点，ERROR 级别违规阻断发版 |
| 门禁规则 | 质量门禁启用的规则子集 |
| 发版审批 | 通过质量门禁后的放行流程 |

---

## 二、慢 SQL 分析术语

### 数据源 (Data Source)

| 术语 | 英文 | 定义 |
|------|------|------|
| 性能摘要 | performance_schema | 从 `performance_schema.events_statements_summary_by_digest` 抓取按 SQL 模板聚合的统计 |
| 实时进程 | processlist | 从 `information_schema.processlist` 多次轮询抓取正在执行的 SQL 快照，按(db, sql)去重 |

> **注意**: `mysql.slow_log` 数据源已废弃。TDSQL分布式实例中各SET的slow_log表不记录数据，慢日志由Proxy层统一管理。

### 扫描任务管理

| 术语 | 定义 |
|------|------|
| 扫描任务 | 一次慢 SQL 抓取操作的记录，含数据源、时间窗口、状态 |
| 时间窗口 | 扫描的开始和结束时间范围，**所有数据源必填**，防止拉取海量数据 |
| 耗时阈值 | min_time 参数，过滤执行时间低于阈值的记录（秒） |
| 抓取条数 | limit 参数，单次扫描最大返回记录数 |
| 扫描状态 | pending / running / completed / failed |

### 慢 SQL 记录

| 术语 | 英文 | 定义 |
|------|------|------|
| 慢 SQL 记录 | SlowQueryRecord | 单条慢查询的结构化记录 |
| SQL 指纹 | Fingerprint / DIGEST_TEXT | SQL 模板的归一化文本，常量替换为 `?` |
| 执行次数 | exec_count / COUNT_STAR | 该 SQL 模板累计执行次数 |
| 总耗时 | total_time_ms | 累计执行时间（毫秒） |
| 平均耗时 | avg_time_ms | 平均单次执行时间 |
| 最大耗时 | max_time_ms | 单次最大执行时间 |
| 扫描行数 | rows_examined | 累计扫描行数 |
| 返回行数 | rows_sent | 累计返回行数 |

### 慢 SQL 分析

| 术语 | 定义 |
|------|------|
| EXPLAIN 分析 | 通过 MySQL EXPLAIN 获取执行计划，分析索引使用和扫描类型 |
| SQL 文本分析 | 对 SQL 文本进行静态分析，识别潜在性能问题 |
| 索引建议 | 基于慢 SQL 分析结果推荐索引优化方案 |
| SQL 改写 | 将低效 SQL 改写为等效的高效写法 |

---

## 三、TDSQL 管理术语

| 术语 | 定义 |
|------|------|
| TDSQL 连接池 | 线程本地存储的 MySQL 连接管理器，支持多实例切换 |
| TDSQL 实例 | 一个 TDSQL 数据库连接配置（host/port/user/password） |
| 表元数据 | 从 TDSQL 抓取的表结构信息（字段、索引、引擎、字符集） |
| 分片键 | TDSQL 分布式表的水平分片字段，分布式规则检查的核心依据 |
| 字符集诊断 | 检测表/列字符集不一致问题 |
| 大表治理 | 大表识别（三级分类）、保留期管理、分区监控、变更管控 |

---

## 四、大表治理术语

| 术语 | 定义 |
|------|------|
| 大表 | 行数或存储空间超过阈值的表 |
| 三级分类 | 按表大小分为 L1（关注）/ L2（治理）/ L3（紧急）三级 |
| 保留期 | 数据保留时间策略，超期应归档或清理 |
| 分区监控 | 监控分区表分区数量、大小、过期分区 |
| 变更管控 | 对大表的 DDL 操作进行额外审批和风险评估 |

---

## 五、技术架构术语

### 分层架构

| 层级 | 目录 | 职责 |
|------|------|------|
| API 路由层 | `backend/api/` | FastAPI 路由定义、请求/响应模型、参数校验 |
| 服务层 | `backend/services/` | 业务逻辑、数据持久化、外部系统集成 |
| 引擎层 | `backend/engine/` | SQL 解析、规则检查、慢 SQL 分析 |
| 规则库 | `backend/engine/rules/` | 76 条审核规则的实现 |
| 数据层 | `data/` + TDSQL | SQLite 本地存储 + TDSQL 远程数据库 + PDF 报告 |

### 核心组件

| 术语 | 文件 | 定义 |
|------|------|------|
| SQL 解析器 | `engine/parser.py` | 基于 sqlglot 的 SQL 解析，输出 ParsedSQL 结构 |
| 规则检查器 | `engine/checker.py` | 调度规则库，对 SQL 执行检查并汇总结果 |
| 慢 SQL 分析器 | `engine/slow_analyzer.py` | EXPLAIN 分析 + SQL 文本静态分析 |
| TDSQL 连接器 | `services/tdsql_connector.py` | 连接池管理、元数据抓取、慢查询采集 |
| 审核服务 | `services/audit_service.py` | 审核流程编排、SQLite 历史持久化 |
| 慢查询服务 | `services/slow_query_service.py` | 慢 SQL 记录管理、分页查询、筛选 |
| 报告服务 | `services/report_service.py` | PDF 审核报告生成 |
| 定时调度器 | `services/scheduler.py` | APScheduler 定时慢日志拉取 |

### 数据模型

| 术语 | 定义 |
|------|------|
| ParsedSQL | sqlglot 解析后的结构化 SQL 信息 |
| Violation | 单条违规记录（rule_id, severity, message, suggestion） |
| AuditResult | 审核结果（sql_text, violations, stats） |
| SlowQueryRecord | 慢 SQL 记录（fingerprint, sql_text, exec_count, times...） |
| AnalysisResult | 慢 SQL 分析结果（index suggestions, issues） |
| TableMetadata | 表元数据（columns, indexes, engine, charset, shard_key） |

### 数据库表

| 表名 | 位置 | 说明 |
|------|------|------|
| `audit_history` | SQLite | 审核历史记录 |
| `slow_queries` | SQLite | 慢 SQL 记录 + 分析结果 |
| `scan_tasks` | SQLite | 慢 SQL 扫描任务记录 |
| `performance_schema.events_statements_summary_by_digest` | TDSQL Proxy | SQL 摘要统计表（Proxy层聚合所有SET数据） |
| `information_schema.processlist` | TDSQL Proxy | 当前执行进程列表 |

---

## 六、测试术语

| 术语 | 定义 |
|------|------|
| SIT 测试 | 系统集成测试，验证模块间集成正确性 |
| UAT 测试 | 用户验收测试，验证功能满足用户需求 |
| 冒烟测试 | 部署后的快速验证，确保核心功能可用 |
| 回归测试 | Bug 修复后重新运行测试，确保不复发 |
| connected_client | 测试 fixture，自动连接 Docker MySQL 的 TestClient |

### 测试文件命名

| 文件 | 类型 | 覆盖内容 |
|------|------|---------|
| `test_rules.py` | 单元测试 | 审核规则逐条测试 |
| `test_parser.py` | 单元测试 | SQL 解析器测试 |
| `test_distributed.py` | 单元测试 | 分布式规则测试 |
| `test_sit_full.py` | SIT | API 集成测试 |
| `test_sit_rules.py` | SIT | 规则系统测试 |
| `test_uat_round1.py` | UAT | 基础功能验收 |
| `test_uat_round2_db.py` | UAT | TDSQL 数据库交互验收 |
| `test_uat_rules.py` | UAT | 审核规则页面验收 |
| `test_uat_frontend.py` | UAT | 前端页面验收 |

---

## 七、部署术语

| 术语 | 定义 |
|------|------|
| Docker MySQL | 用于测试的 MySQL 容器（tdsql-mysql-test），端口 13306 |
| slow_log_ms | Proxy层慢日志阈值配置（毫秒），通过 `/*proxy*/show config` 获取 |
| slow_log_level | Proxy层慢日志级别配置，通过 `/*proxy*/show config` 获取 |

---

## 八、GitLab 集成术语

| 术语 | 定义 |
|------|------|
| MR Webhook | GitLab Merge Request 的 Webhook 回调 |
| 自动审核 | MR 提交时自动触发 SQL 审核 |
| 评论回写 | 将审核结果作为评论写入 GitLab MR |

---

## 术语变更记录

| 日期 | 变更 | 原因 |
|------|------|------|
| 2026-06-17 | 初始创建 | V1.0 版本领域术语梳理 |
| 2026-06-17 | 新增扫描任务、质量门禁、大表治理术语 | V1.0 新增模块 |
