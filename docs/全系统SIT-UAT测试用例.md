# TDSQL SQL 审核平台 · 全系统 SIT & UAT 测试用例

> 文档版本：v1.0.2 ｜ 适用系统版本：v1.0.2 ｜ 更新日期：2026-07-07

> 适用范围：v1.0.2 全系统（后端+前端）
> 用途：交付测试智能体执行的**全系统**集成测试（SIT）与用户验收测试（UAT）用例集
> 编写日期：2026-07-05

---

## 第 0 部分 · 测试总纲

### 0.1 被测系统概览

- **后端**：Python 3.11 / FastAPI，14 个路由模块，统一前缀 `/api/v1/*`。
- **审核引擎**：119 条审核规则，9 大分类（命名 5 / DDL 22 / DML 9 / 索引 10 / 分布式 14 / 安全 8 / 性能 5 / 事务 4 / Oracle迁移兼容 42）。
- **RBAC**：4 角色 —— `admin`（系统管理员）、`dba`（数据库管理员）、`developer`（开发）、`auditor`（审计员）。
- **鉴权**：PBKDF2 口令 + HMAC 自包含 Token（Bearer）；连续登录失败 5 次锁定 15 分钟；首登强制改密。
- **元数据库**：MySQL（经 SQLite 兼容层，环境变量 `SQLCHECK_DB_*`）。
- **目标库**：TDSQL 分布式实例 + 集中式实例（多实例注册表管理）。
- **前端**：Vue3 + Element Plus 单页，19 页面 / 5 导航域，纯内网（无外网请求）。
- **可观测性**：Prometheus `/metrics`；操作审计日志。
- **调度**：leader 租约 + 定时扫描计划（cron 时/分）。

### 0.2 测试环境要求

| 项 | 要求 |
|---|---|
| 后端服务 | `uvicorn backend.main:app --host 0.0.0.0 --port 8000`，`AUTH_ENABLED=true` |
| 元数据库 | MySQL 可用，`SQLCHECK_DB_*` 已配置，已执行建表/迁移 |
| 目标实例 A | TDSQL **分布式**实例（可连通），用于扫描/体检/EXPLAIN |
| 目标实例 B | TDSQL **集中式**实例（可连通），用于多实例切换验证 |
| 前端 | 与后端同源部署（`API_BASE=''`），浏览器 Chrome/Edge 最新版 |
| 抓包工具 | 浏览器 DevTools（验证纯内网、请求契约）、curl（SIT 接口级） |

### 0.3 测试账号矩阵（执行前由 admin 预置）

| 账号 | 角色 | 用途 |
|---|---|---|
| `admin` | admin | 系统管理、用户/保留/门禁全权 |
| `dba01` | dba | 实例/扫描/规则集/门禁/巡检 |
| `dev01` | developer | SQL 审核、只读慢SQL |
| `audit01` | auditor | 概览、审核历史、操作审计、导出 |

> 首次登录各账号需完成强制改密；后续用例默认已改密并可正常登录。

### 0.4 用例编号与优先级

- 编号：`SIT-<模块>-<序号>` / `UAT-<角色或场景>-<序号>`。
- 优先级：**P0**=冒烟（阻断，必过）；**P1**=核心功能；**P2**=扩展/边界。
- 每个用例结果记：**通过 / 失败 / 阻塞 / 不适用**；失败须附实际现象、请求响应、截图。

### 0.5 缺陷分级

- **致命**：核心流程不可用、数据错误、越权、崩溃。
- **严重**：主要功能缺陷、契约不符（4xx/5xx）、显示错误数据。
- **一般**：次要功能、体验、文案。
- **轻微**：样式、提示优化。

### 0.6 通过标准（Exit Criteria）

- SIT：P0 用例 100% 通过；P1 ≥ 95% 通过；无致命/严重未关闭缺陷。
- UAT：全部角色核心业务流（P0/P1）100% 走通；无阻断用户完成工作的缺陷。

---

# 第一部分 · SIT 系统集成测试用例

> 视角：技术/接口级，验证模块间集成、接口契约、数据流、鉴权与边界。可用 curl 或前端触发。

## SIT-AUTH 认证与 RBAC

| 用例ID | 目的 | 前置 | 步骤 | 预期结果 | 优先级 |
|---|---|---|---|---|---|
| SIT-AUTH-01 | 正确口令登录 | admin 已建 | `POST /auth/login {username,password}` | 200；返回 `token` 与 `user.role=admin` | P0 |
| SIT-AUTH-02 | 错误口令 | — | 用错误口令登录 | 401；`detail` 提示口令错误；不返回 token | P0 |
| SIT-AUTH-03 | 连续失败锁定 | 新测试账号 | 连续 5 次错误口令登录，第 6 次用正确口令 | 第 6 次仍失败，提示"账户已锁定，请 X 分钟后重试" | P1 |
| SIT-AUTH-04 | 管理员解锁 | 承接 03 | admin `POST /auth/users/{u}/unlock` 后该账号正确口令登录 | 解锁成功；登录 200 | P1 |
| SIT-AUTH-05 | 首登强制改密 | 新建用户 | 新用户首次登录 | `user.must_change_password=true`；`change-password` 后需重新登录 | P1 |
| SIT-AUTH-06 | Token 鉴权 | 已登录 | 携带 `Authorization: Bearer <token>` 访问 `GET /auth/me` | 200，返回当前用户信息 | P0 |
| SIT-AUTH-07 | 无 Token 访问受保护接口 | — | 不带 token 访问 `GET /auth/users` | 401 | P0 |
| SIT-AUTH-08 | 过期/伪造 Token | — | 篡改 token 后访问 `GET /auth/me` | 401 | P1 |
| SIT-AUTH-09 | 改密弱口令拒绝 | 已登录 | `change-password` 提交弱口令（<8位/缺类别） | 400，提示口令复杂度不足 | P1 |
| SIT-AUTH-10 | 越权-开发创建用户 | dev01 登录 | dev01 `POST /auth/users` | 403 | P0 |
| SIT-AUTH-11 | 越权-审计员改保留策略 | audit01 登录 | audit01 `PUT /admin/retention` | 403 | P0 |
| SIT-AUTH-12 | 角色清单 | 已登录 | `GET /auth/roles` | 200，返回 4 个角色 | P2 |
| SIT-AUTH-13 | 用户 CRUD | admin | 建/改状态/重置口令/删用户全链路 | 各步 200；列表相应变化；不能删除自身/最后一个 admin（如有该保护） | P1 |
| SIT-AUTH-14 | 登出失效 | 已登录 | `POST /auth/logout` 后用原 token 访问 | 登出成功；原 token 访问受保护接口 401（若服务端失效）或前端已清 token | P2 |

## SIT-CONN 连接注册与多实例

| 用例ID | 目的 | 步骤 | 预期结果 | 优先级 |
|---|---|---|---|---|
| SIT-CONN-01 | 测试连接（成功） | `GET /tdsql/test-connection?host&port&user&password&database`（实例A） | 200，`status=connected`，含 `server_version`、`latency_ms` | P0 |
| SIT-CONN-02 | 测试连接（失败） | 错误口令/端口 | `status` 非 connected，返回错误信息，不抛 500 | P1 |
| SIT-CONN-03 | 保存连接 | `POST /tdsql/connections`（实例A、is_distributed=true） | 200；`GET /tdsql/connections` 列表出现该连接，口令不回显明文 | P0 |
| SIT-CONN-04 | 保存第二连接 | 保存实例B（集中式） | 列表含 2 条 | P1 |
| SIT-CONN-05 | 设为默认 | `POST /tdsql/connections/{id}/set-default` | 该连接 `is_default=true`，其余为 false | P1 |
| SIT-CONN-06 | 建立连接 | `POST /tdsql/connections/{id}/connect` | 200；`GET /tdsql/status` 显示已连接 | P0 |
| SIT-CONN-07 | 发现 SET | 分布式实例已连 → `GET /tdsql/sets` | 返回该分布式实例的 SET 列表 | P1 |
| SIT-CONN-08 | 表清单/元数据 | `GET /tdsql/tables`、`/tables/{name}/metadata` | 返回库内表及指定表结构 | P1 |
| SIT-CONN-09 | 删除连接 | `DELETE /tdsql/connections/{id}` | 200；列表移除 | P1 |
| SIT-CONN-10 | 口令加密存储 | 直查元数据库连接表 | 口令为密文，非明文 | P1 |

## SIT-AUDIT SQL 审核（即时 / 文件 / 规则）

| 用例ID | 目的 | 步骤 | 预期结果 | 优先级 |
|---|---|---|---|---|
| SIT-AUDIT-01 | 规则总量 | `GET /rules` | `total=119`；`rules[]` 含 rule_id/category/severity/description | P0 |
| SIT-AUDIT-02 | 分类统计 | `GET /rules/categories` | 9 分类，数量 5/22/9/10/14/8/5/4/42 | P1 |
| SIT-AUDIT-03 | 合规 SQL 通过 | `POST /audit/sql {sql:"SELECT id FROM t WHERE id=1"}` | `passed=true`，violations 空或仅 INFO | P0 |
| SIT-AUDIT-04 | DELETE 无 WHERE 命中 | `POST /audit/sql {sql:"DELETE FROM t_order"}` | `passed=false`，命中高危 DML 规则（ERROR） | P0 |
| SIT-AUDIT-05 | SELECT * / ORDER BY RAND 命中 | 审核示例 SELECT | 命中相应性能/规范规则并给 suggestion | P1 |
| SIT-AUDIT-06 | 建表缺主键/大字段命中 | 审核示例 CREATE | 命中 DDL 规则（无主键、FLOAT 金额、缺 NOT NULL 等） | P1 |
| SIT-AUDIT-07 | 语法错误 SQL | 提交非法 SQL | 返回明确解析错误，不 500 | P1 |
| SIT-AUDIT-08 | 文件审核（.sql） | `POST /audit/file {content,file_path:"a.sql"}` | 返回 `summary{total_sql,passed,failed,pass_rate}` 与 `results[]` | P0 |
| SIT-AUDIT-09 | MyBatis XML 审核 | 上传 .xml | 正确解析 XML 中 SQL 并逐条审核 | P1 |
| SIT-AUDIT-10 | 文件审核报告列表 | `GET /audit/file-reports?limit&offset` | `{items,total}`，分页正确 | P1 |
| SIT-AUDIT-11 | HTML 报告下载 | `GET /audit/file-reports/{id}/html?access_token=` | 返回 HTML，中文不乱码，Content-Disposition 正确 | P1 |
| SIT-AUDIT-12 | 带 project_id 审核走项目规则集/门禁 | 先建项目并绑非默认规则集 → 审核带 `project_id` | 审核按项目规则集执行；返回 `gate_result` | P1 |
| SIT-AUDIT-13 | 审核历史入库 | 审核后查 `GET /dashboard/summary` | 今日审核计数增加 | P2 |
| SIT-AUDIT-14 | SQL 脱敏落地 | 审核含字面量 SQL 后直查审核历史存储 | 存储的 SQL 字面量已被替换为 `?`（防敏感数据落地） | P1 |

## SIT-SLOW 慢SQL 治理（扫描 / 记录 / EXPLAIN / 计划）

| 用例ID | 目的 | 步骤 | 预期结果 | 优先级 |
|---|---|---|---|---|
| SIT-SLOW-01 | digest 扫描 | 连实例A → `POST /tdsql/slow-queries/fetch {source:"digest",connection_id,time_window_start/end,...}` | 200，返回 `fetched` 与 `scan_task_id`；生成扫描任务 | P0 |
| SIT-SLOW-02 | digest 缺时间窗 | 不传时间窗 | 校验失败（时间窗必填），返回明确错误 | P1 |
| SIT-SLOW-03 | processlist 扫描 | `source:"processlist"` + 轮询参数 | 200，抓取实时进程慢SQL | P1 |
| SIT-SLOW-04 | 非法数据源 | `source:"slow_log"` | 报错并提示 TDSQL 分布式仅支持 digest/processlist | P1 |
| SIT-SLOW-05 | 扫描任务列表分页 | `GET /slow-queries/scan-tasks?limit&offset` | `{items,total}`，分页正确 | P1 |
| SIT-SLOW-06 | 慢SQL列表筛选 | `GET /slow-queries?db_name&severity&status&keyword&scan_task_id&created_by` | 各筛选条件生效，返回 `{items,total}` | P1 |
| SIT-SLOW-07 | 慢SQL详情 | `GET /slow-queries/{id}` | 返回指纹、耗时、扫描行数、分析结果 analyses[] | P1 |
| SIT-SLOW-08 | 状态流转 | `PUT /slow-queries/{id}/status {status:"optimized"}` | 200；列表状态更新 | P0 |
| SIT-SLOW-09 | EXPLAIN(SQL直连) | `POST /slow-queries/analyze-explain-by-sql {sql,connection_id}` | 返回 explain_rows/columns 与 analyses | P1 |
| SIT-SLOW-10 | EXPLAIN(JSON) | `POST /slow-queries/analyze-explain {explain_data}` | 正确解析并给出分析 | P1 |
| SIT-SLOW-11 | 跨SET对比 | `GET /slow-queries/cross-set-analysis` | 返回跨 SET 对比数据 | P2 |
| SIT-SLOW-12 | 删除扫描任务级联 | `DELETE /slow-queries/scan-tasks/{id}` | 200；该任务关联慢SQL一并删除 | P1 |
| SIT-SLOW-13 | 清理孤儿记录 | `DELETE /slow-queries/orphan-records` | 200；无任务关联记录被清理 | P2 |
| SIT-SLOW-14 | 扫描任务 HTML 报告 | `GET /slow-queries/scan-tasks/{id}/html?access_token=` | 返回 HTML 报告 | P2 |
| SIT-SLOW-15 | CRITICAL 级别标识 | 造一条 CRITICAL 慢SQL，查列表/详情 | 级别为 CRITICAL（前端应显红色，见 UAT-UX） | P1 |
| SIT-SLOW-16 | 扫描计划 CRUD | `POST/GET/PUT/DELETE /tdsql/scan-schedules` | 创建/列出（`{schedules}`）/改启用/删除全链路 200 | P1 |
| SIT-SLOW-17 | 计划启用开关（完整体） | `PUT /tdsql/scan-schedules/{id}` 传完整字段（含 connection_id）翻转 enabled | 200，不返回 422（回归 3 轮 BUG-2） | P1 |

## SIT-HEALTH 数据库体检

| 用例ID | 目的 | 步骤 | 预期结果 | 优先级 |
|---|---|---|---|---|
| SIT-HEALTH-01 | 字符集检查 | `GET /tdsql/check/charset?connection_id&database` | 返回 `is_consistent`、`column_mismatches[]`、`cross_table_mismatches[]`、`table_charset_distribution[]` | P1 |
| SIT-HEALTH-02 | 大表检查 | `GET /tdsql/check/large-tables?connection_id&threshold_gb=1` | 返回 `{total,tables:[{TABLE_NAME,size_gb,TABLE_ROWS,level}]}`，level 分级 L1/L2/L3 | P1 |
| SIT-HEALTH-03 | 未连实例体检 | 不选实例触发 | 明确提示需先选实例，不 500 | P2 |

## SIT-BIGTABLE 大表治理

| 用例ID | 目的 | 步骤 | 预期结果 | 优先级 |
|---|---|---|---|---|
| SIT-BIG-01 | 采集入库 | `POST /bigtable/inventory/{conn}` 传大表数组 | 200，`data` 返回治理报告；清单入库 | P1 |
| SIT-BIG-02 | 查询清单 | `GET /bigtable/inventory/{conn}` | 返回 `data:[{schema_name,table_name,size_gb,rows_count,level,is_partitioned,shard_key}]` | P1 |
| SIT-BIG-03 | 治理报告 | `GET /bigtable/report/{conn}` | 返回分级统计报告 | P2 |
| SIT-BIG-04 | 单表分类 | `GET /bigtable/classify/{table}` | 返回该表分类建议 | P2 |

## SIT-PROJECT / RULESET / GATE 平台治理

| 用例ID | 目的 | 步骤 | 预期结果 | 优先级 |
|---|---|---|---|---|
| SIT-PROJ-01 | 项目 CRUD | `POST/GET/DELETE /projects` | 创建/列出（`data:[]`）/删除全链路 200 | P1 |
| SIT-PROJ-02 | 项目绑定规则集/门禁 | 创建时带 `rule_set_id`、`gate_rule_id` | 绑定生效，列表可见 | P2 |
| SIT-RS-01 | 规则集列表 | `GET /rulesets` | 返回 `{rulesets:[]}`，含内置规则集 | P1 |
| SIT-RS-02 | 创建规则集 | `POST /rulesets {id,name,description,items}` | 200；列表新增 | P1 |
| SIT-RS-03 | 更新规则集 | `PUT /rulesets/{id}` | 200；内容更新 | P2 |
| SIT-RS-04 | 删除内置规则集拒绝 | `DELETE /rulesets/<内置id>` | 拒绝或前端拦截（内置不可删） | P2 |
| SIT-RS-05 | 规则集多租户覆盖 | 项目A绑规则集X，项目B绑Y → 分别审核同一SQL | 两项目按各自规则集判定，互不影响 | P1 |
| SIT-GATE-01 | 门禁策略列表 | `GET /gate/strategies` | 返回 `data`（策略字典，如 strict/loose 等） | P1 |
| SIT-GATE-02 | 应用预设策略 | `POST /gate/strategy/{project_id}?strategy=strict` | 200；该项目门禁规则更新 | P1 |
| SIT-GATE-03 | 自定义门禁 | `POST /gate/rules {project_id,max_error_count,max_warning_count}` | 200；`GET /gate/rules/{project_id}` 回读一致 | P1 |
| SIT-GATE-04 | 门禁拦截生效 | 项目门禁设 max_error=0 → 审核含 ERROR 的 SQL 带该 project_id | 返回 `gate_result.passed=false`，说明拦截原因 | P0 |
| SIT-GATE-05 | 门禁放行 | 合规 SQL 带 project_id | `gate_result.passed=true` | P1 |

## SIT-MONITOR 监控告警

| 用例ID | 目的 | 步骤 | 预期结果 | 优先级 |
|---|---|---|---|---|
| SIT-MON-01 | 告警规则创建 | `POST /monitor/rules {metric_name,warning_threshold,urgent_threshold,...}` | 200；`GET /monitor/rules` 返回 `data` 含该规则 | P1 |
| SIT-MON-02 | 指标评估触发告警 | `POST /monitor/evaluate?connection_id&metric_name&value`（超阈值） | 返回 `data` 含告警对象 | P1 |
| SIT-MON-03 | 活跃告警列表 | `GET /monitor/alerts` | 返回 `data:[]` | P1 |
| SIT-MON-04 | 确认告警 | `POST /monitor/alerts/{id}/acknowledge` | 200；该告警状态变更；活跃告警数减少 | P1 |
| SIT-MON-05 | 指标未超阈 | evaluate 传正常值 | 返回"指标正常，未触发告警" | P2 |

## SIT-INSPECT 巡检管理

| 用例ID | 目的 | 步骤 | 预期结果 | 优先级 |
|---|---|---|---|---|
| SIT-INS-01 | 创建巡检（query 参数） | `POST /inspection/tasks?connection_id=X&inspection_type=full` | 200，返回 `data.task_id`（回归 3 轮 BUG-1：不 422） | P1 |
| SIT-INS-02 | 巡检任务列表 | `GET /inspection/tasks` | 返回 `data:[]` | P1 |
| SIT-INS-03 | 巡检详情含结果 | `GET /inspection/tasks/{id}` | 返回 `data`，含 `results[]` | P1 |
| SIT-INS-04 | 更新状态 | `POST /inspection/tasks/{id}/status?status=completed` | 200；状态更新 | P2 |
| SIT-INS-05 | 保存结果 | `POST /inspection/tasks/{id}/results` | 200；结果入库 | P2 |

## SIT-ADMIN 系统管理

| 用例ID | 目的 | 步骤 | 预期结果 | 优先级 |
|---|---|---|---|---|
| SIT-ADM-01 | 系统信息 | `GET /admin/info` | 返回 version=2.0.0、auth/masking/metrics 开关、扫描/连接池限额（不含敏感值） | P1 |
| SIT-ADM-02 | 保留策略读 | `GET /admin/retention` | 返回 `{policies:[]}` | P1 |
| SIT-ADM-03 | 保留策略写 | `PUT /admin/retention {table_name,retention_days,enabled}` | 200；回读生效 | P1 |
| SIT-ADM-04 | 手动清理 | `POST /admin/retention/run` | 200，返回删除统计 | P2 |
| SIT-ADM-05 | 操作审计日志 | 执行若干写操作后 `GET /admin/operation-logs?limit&offset` | 返回 `{total,logs:[]}`，含操作人/类型/详情/IP/时间 | P1 |
| SIT-ADM-06 | 审计日志越权 | dev01 访问 operation-logs | 403（仅 admin/dba/auditor 可看，按附录B） | P1 |

## SIT-DASH 概览仪表盘

| 用例ID | 目的 | 步骤 | 预期结果 | 优先级 |
|---|---|---|---|---|
| SIT-DASH-01 | 概览汇总 | `GET /dashboard/summary` | 返回 audit（今日计数/通过率/ERROR/WARNING）、slow_queries（pending/optimized/critical_count/top3_time）、recent_audits、rules | P0 |
| SIT-DASH-02 | 审核趋势 | `GET /dashboard/audit-trend?days=7` | 返回 dates/passed/failed 数组 | P1 |
| SIT-DASH-03 | 高频违规 | `GET /dashboard/rule-stats` | 返回 rules[]，含命中次数 | P1 |
| SIT-DASH-04 | 空库兜底 | 空数据环境请求 summary | 各计数为 0，不报错 | P2 |

## SIT-GITLAB GitLab 集成

| 用例ID | 目的 | 步骤 | 预期结果 | 优先级 |
|---|---|---|---|---|
| SIT-GIT-01 | 集成配置说明 | `GET /gitlab/config` | 返回配置说明 | P2 |
| SIT-GIT-02 | Diff 审核 | `POST /gitlab/audit/diff` 提交含 SQL 的 diff | 返回 diff 中 SQL 的审核结果 | P1 |
| SIT-GIT-03 | 仓库审核 | `POST /gitlab/audit/repository` | 返回仓库 SQL 文件审核汇总 | P2 |
| SIT-GIT-04 | Webhook 校验 | `POST /gitlab/webhook/merge-request`（无/错签名） | 严格校验，拒绝非法请求 | P1 |

## SIT-OBS / SEC 可观测性与安全

| 用例ID | 目的 | 步骤 | 预期结果 | 优先级 |
|---|---|---|---|---|
| SIT-OBS-01 | Prometheus 指标 | `GET /metrics` | 返回 Prometheus 文本格式，含 `tdsql_scan_tasks_total` 等 | P1 |
| SIT-SEC-01 | 403 契约 | 越权操作 | 返回 403 + `detail`（前端应 friendly 提示） | P1 |
| SIT-SEC-02 | 5xx 兜底 | 构造服务异常（如连不可达实例扫描） | 返回 5xx + `detail`，不泄露堆栈敏感信息 | P1 |
| SIT-SEC-03 | SQL 注入面 | 在筛选/关键词参数注入特殊字符 | 参数化查询，无注入、无 500 | P1 |
| SIT-SEC-04 | 口令不回显 | 各处涉及口令的返回 | 均不回显明文口令 | P1 |

## SIT-SCHED 调度器

| 用例ID | 目的 | 步骤 | 预期结果 | 优先级 |
|---|---|---|---|---|
| SIT-SCHED-01 | 调度状态 | `GET /tdsql/scheduler/status` | 返回调度器状态（leader、下一次执行等） | P2 |
| SIT-SCHED-02 | 手动触发 | `POST /tdsql/scheduler/trigger` | 200，触发一次拉取 | P2 |
| SIT-SCHED-03 | 计划到点执行 | 建一个近未来时刻的扫描计划，等待到点 | 到点自动执行扫描，生成扫描任务 | P2 |
| SIT-SCHED-04 | 单 leader 不重复 | 多实例部署下 | 仅 leader 执行，不重复扫描（如可模拟） | P2 |

---

# 第二部分 · UAT 用户验收测试用例

> 视角：业务/角色端到端，全程通过前端 UI 完成，不借助 curl。验证"某角色能否用一个账号完成本职工作"。

## UAT-ADMIN 系统管理员业务流

| 用例ID | 业务场景 | 步骤 | 预期结果（验收点） | 优先级 |
|---|---|---|---|---|
| UAT-ADMIN-01 | 首登改密 | admin 首次登录 → 按提示改密 → 重登 | 改密成功，重登进入工作台 | P0 |
| UAT-ADMIN-02 | 建齐 4 类账号 | 用户管理 → 新建 dba01/dev01/audit01 | 列表出现 3 账号，角色正确 | P0 |
| UAT-ADMIN-03 | 禁用/启用/重置/解锁 | 对 dev01 依次禁用（二次确认）、启用、重置口令、解锁 | 每步有确认与成功提示，状态正确联动 | P1 |
| UAT-ADMIN-04 | 配置数据保留 | 数据保留页 → 新增策略（表+天数）→ 立即清理（二次确认） | 策略入列表，清理有结果提示 | P1 |
| UAT-ADMIN-05 | 查系统信息 | 系统信息页 | 展示版本、认证/脱敏/指标开关等 | P2 |
| UAT-ADMIN-06 | 查操作审计 | 操作审计页 | 表格有分页、能看到刚才的操作记录 | P1 |
| UAT-ADMIN-07 | 全菜单可见 | 观察左侧导航 | admin 可见全部 5 域菜单项 | P1 |

## UAT-DBA 数据库管理员业务流（核心闭环）

| 用例ID | 业务场景 | 步骤 | 预期结果（验收点） | 优先级 |
|---|---|---|---|---|
| UAT-DBA-01 | 纳管实例 | 实例管理 → 新建连接（实例A）→ 测试连接 → 保存 → 设默认 | 连接测试成功、保存、置默认 | P0 |
| UAT-DBA-02 | 顶栏切实例刷新 | 顶栏实例切换器选实例A → 观察当前页数据 | 自动建连并提示，当前页数据随之刷新（回归第2轮P1-04） | P0 |
| UAT-DBA-03 | 发起慢SQL扫描 | 扫描任务 → 新建扫描（digest+时间窗）→ 开始 | 扫描完成提示抓取条数，任务入列表 | P0 |
| UAT-DBA-04 | 查慢SQL并标记 | 慢SQL记录 → 筛选 → 打开详情 → 标记"已优化" | 筛选生效、详情完整、状态更新 | P0 |
| UAT-DBA-05 | CRITICAL 显示正确 | 慢SQL列表含 CRITICAL 记录 | CRITICAL 显示为**红色**危险标签（非绿色，回归第2轮P1-06） | P1 |
| UAT-DBA-06 | EXPLAIN 分析 | EXPLAIN 页 → SQL直连（选实例）→ 分析 | 返回执行计划表 + 分析建议 | P1 |
| UAT-DBA-07 | 配置定时扫描计划 | 扫描计划 → 新建计划 → 拨动"启用"开关 | 计划创建成功；开关切换成功、不回弹（回归第3轮BUG-2） | P0 |
| UAT-DBA-08 | 数据库体检 | 数据库体检 → 字符集/大表检查 | 两类检查各出表格数据（非空白） | P1 |
| UAT-DBA-09 | 大表治理采集 | 大表治理 → 采集大表清单 → 刷新 | 采集后表格出现大表数据 | P1 |
| UAT-DBA-10 | 定制规则集 | 规则集 → 新建规则集 | 列表新增；内置规则集不可删 | P1 |
| UAT-DBA-11 | 建项目并配门禁 | 项目管理建项目 → 门禁页选该项目 → 应用 strict / 自定义阈值保存 | 门禁规则保存并回显 | P1 |
| UAT-DBA-12 | 多实例切换对比 | 顶栏在实例A/实例B间切换 | 各页数据随实例变化，互不串数据 | P1 |
| UAT-DBA-13 | 菜单可见性 | 观察导航 | dba 可见实例/慢SQL/体检/平台治理；不可见"用户管理" | P1 |

## UAT-DEV 开发人员业务流

| 用例ID | 业务场景 | 步骤 | 预期结果（验收点） | 优先级 |
|---|---|---|---|---|
| UAT-DEV-01 | 即时审核 | 即时审核 → 粘贴 SQL → Ctrl+Enter | 返回违规清单，每条含规则号/级别/建议 | P0 |
| UAT-DEV-02 | 门禁反馈 | 顶栏选项目 → 审核含 ERROR 的 SQL | 展示门禁"阻断"结果与原因 | P1 |
| UAT-DEV-03 | 文件审核 | 文件审核 → 拖拽 .sql/.xml | 汇总 + 逐条折叠结果 | P1 |
| UAT-DEV-04 | 浏览规则库 | 审核规则库 → 搜索/展开分类 | 119 条规则按 9 分类展示，可搜索 | P2 |
| UAT-DEV-05 | 权限边界 | 观察导航 + 尝试进入平台治理写操作 | 开发看不到"用户管理/监控告警/巡检/扫描计划"等；平台治理项为只读或隐藏（对照附录B） | P1 |
| UAT-DEV-06 | 慢SQL 只读 | 进入慢SQL记录 | 可查看，但无状态流转等写操作（只读） | P2 |

## UAT-AUDITOR 审计员业务流

| 用例ID | 业务场景 | 步骤 | 预期结果（验收点） | 优先级 |
|---|---|---|---|---|
| UAT-AUD-01 | 看治理概览 | 登录 → 治理概览 | KPI、趋势图、TOP 违规、慢SQL 均展示 | P0 |
| UAT-AUD-02 | 查审核历史 | 文件审核 → 审核报告 | 历史列表可见、可下载 HTML 报告 | P1 |
| UAT-AUD-03 | 查操作审计日志 | 操作审计页 | 可看全量操作日志（审计核心职责） | P0 |
| UAT-AUD-04 | 导出报告 | 慢SQL/审核报告导出 | 成功导出 PDF/HTML | P1 |
| UAT-AUD-05 | 只读约束 | 尝试新建用户/删连接等写操作 | 无入口或被拒（只读角色） | P1 |
| UAT-AUD-06 | 全程无需 curl | 用一个 auditor 账号完成 01-04 | 全部在 UI 内闭环完成 | P1 |

## UAT-E2E 跨角色端到端业务流

| 用例ID | 业务场景 | 步骤 | 预期结果（验收点） | 优先级 |
|---|---|---|---|---|
| UAT-E2E-01 | 从纳管到拦截 | admin 建账号 → dba 纳管实例+建项目+配 strict 门禁+定制规则集 → dev 用该项目审核问题 SQL | 门禁按项目规则集正确拦截，全链路打通 | P0 |
| UAT-E2E-02 | 扫描到治理闭环 | dba 发起扫描 → 出慢SQL → 详情去 EXPLAIN → 标记已优化 → 概览"已优化"计数+1 | 数据在各页一致联动 | P1 |
| UAT-E2E-03 | 监控闭环 | dba 建告警规则 → 触发评估 → 顶栏铃铛红点亮 → 监控页确认告警 → 铃铛清零 | 告警数在铃铛与监控页一致联动 | P1 |
| UAT-E2E-04 | 多租户隔离 | 项目A/B 绑不同规则集，dev 分别用两项目审核同一 SQL | 结果按各自规则集不同，互不干扰 | P1 |

## UAT-UX 前端体验验收（V3.0 设计验收）

| 用例ID | 验收点 | 步骤 | 预期结果 | 优先级 |
|---|---|---|---|---|
| UAT-UX-01 | 纯内网无外链 | DevTools Network 面板加载全站 | 0 个外网请求（vendor/css/js 均本地） | P0 |
| UAT-UX-02 | 分组导航 | 观察左侧菜单 | 5 域分组：治理概览/SQL审核/慢SQL治理/实例与体检/平台治理/系统管理 | P1 |
| UAT-UX-03 | 全局切换器 | 顶栏实例、项目切换器 | 实例切换刷新数据；项目切换后审核带 project_id | P1 |
| UAT-UX-04 | 全 Element Plus 化 | 检查表单控件 | 无原生 input/select/textarea，全 Element Plus | P1 |
| UAT-UX-05 | 列表统一 | 各列表页 | 均用 el-table + 统一 el-pagination；可排序/筛选 | P1 |
| UAT-UX-06 | 加载/空态 | 打开各页 | 首屏骨架屏（KPI）；空数据用 el-empty 带引导；无"加载中…"裸文字 | P2 |
| UAT-UX-07 | 破坏性二次确认 | 删除连接/任务/用户/规则集、禁用用户、清理记录 | 均弹 ElMessageBox 二次确认，文案写明后果 | P1 |
| UAT-UX-08 | 401/403/5xx 处理 | 令 token 失效/越权/服务异常 | 401 跳登录；403 友好提示；5xx 通知兜底 | P1 |
| UAT-UX-09 | RBAC 可见性 | 4 角色分别登录逐一比对附录B | developer 看不到监控告警/巡检等；各角色可见性符合附录B | P0 |
| UAT-UX-10 | 响应式 | 1280/1440/1920 三档宽度 | 无横向滚动；侧栏可折叠 | P2 |
| UAT-UX-11 | 数据真渲染（回归） | 逐一打开：项目/规则集/门禁/监控/巡检/大表/体检/保留/审计/系统信息 | 每页表格/描述有真实数据（非空壳，回归第2轮响应解包根因） | P0 |

## UAT-NFR 非功能验收

| 用例ID | 验收点 | 方法 | 预期结果 | 优先级 |
|---|---|---|---|---|
| UAT-NFR-01 | 多实例规模 | 纳管数十个连接配置 | 连接列表/切换器正常，无明显卡顿 | P2 |
| UAT-NFR-02 | 扫描并发限流 | 对同一实例并发发起多次扫描 | 超限时按并发限流返回 busy，不压垮目标库 | P1 |
| UAT-NFR-03 | 大文件审核 | 上传含数百条 SQL 的文件 | 正常返回，响应时间可接受 | P2 |
| UAT-NFR-04 | 元数据库连接池 | 高频接口连续请求 | 无连接泄漏/耗尽，响应稳定 | P1 |
| UAT-NFR-05 | 会话稳定性 | 长时间使用 | Token 有效期内会话稳定；到期正确跳登录 | P2 |
| UAT-NFR-06 | 浏览器兼容 | Chrome/Edge 最新版 | 功能与样式一致 | P2 |

---

## 附录 A · 回归测试清单（四轮质检修复项，必测）

| 回归项 | 对应用例 | 期望 |
|---|---|---|
| 11 页空壳→真实数据 | UAT-UX-11 | 每页有真实数据渲染 |
| 12 处响应信封解包 | SIT-PROJ/RS/GATE/MON/INS/BIG/ADM 列表类 | 列表/描述正确取到数据 |
| 实例切换刷新数据 | UAT-DBA-02 | 切换后当前页刷新 |
| 项目切换带 project_id | UAT-DEV-02 | 审核请求含 project_id + 门禁结果 |
| CRITICAL 颜色 | UAT-DBA-05 | 红色危险标签 |
| 体检字段对齐 | SIT-HEALTH-01/02 | 字段/级别正确显示 |
| 门禁策略 dict→数组 | SIT-GATE-01 | 策略按钮正常渲染 |
| 新建巡检（query 参数） | SIT-INS-01 | 200 不 422 |
| 扫描计划启用开关 | UAT-DBA-07 / SIT-SLOW-17 | 200 不 422，不回弹 |
| 403/5xx 兜底 | SIT-SEC-01/02、UAT-UX-08 | friendly 提示 / 通知 |
| RBAC 附录B | UAT-UX-09、各角色菜单用例 | 可见性一致 |
| 代码拆分/内联样式 | 静态检查 | app.css/app.js 外置，内联 style ≤20 |
| 调试脚本清理 | 仓库检查 | 无 `_test_*.py` 混入根目录 |

## 附录 B · 角色 × 菜单可见性矩阵（可见性验收基线）

> ✓=可见可用，R=只读可见，—=隐藏。前端据 `authState.role` 控制，后端为最终鉴权。

| 菜单 / 能力 | admin | dba | developer | auditor |
|---|:-:|:-:|:-:|:-:|
| 治理概览 | ✓ | ✓ | ✓ | R |
| 即时审核 / 文件审核 / 规则库 | ✓ | ✓ | ✓ | R |
| 慢SQL 扫描任务 / 记录 | ✓ | ✓ | R | R |
| 慢SQL 状态流转 / 扫描计划 | ✓ | ✓ | — | — |
| EXPLAIN 分析 | ✓ | ✓ | ✓ | R |
| 实例管理 / 数据库体检 | ✓ | ✓ | R | R |
| 大表治理 | ✓ | ✓ | — | R |
| 项目管理 / 规则集 / 质量门禁 | ✓ | ✓ | R | R |
| 监控告警 / 巡检管理 | ✓ | ✓ | — | R |
| 系统-用户管理 | ✓ | — | — | — |
| 系统-数据保留 | ✓ | ✓ | — | — |
| 系统-操作审计日志 | ✓ | R | — | ✓ |
| 系统-系统信息 | ✓ | R | — | R |

## 附录 C · 缺陷记录模板

```
缺陷ID：BUG-<日期>-<序号>
关联用例：SIT-xxx / UAT-xxx
严重级别：致命 / 严重 / 一般 / 轻微
环境：后端版本 / 浏览器 / 角色账号
复现步骤：1. … 2. … 3. …
预期结果：…
实际结果：…（附请求URL/入参、响应体、截图、控制台/后端日志）
备注：…
```

## 附录 D · 执行记录模板

| 用例ID | 执行人 | 执行时间 | 结果(通过/失败/阻塞/NA) | 关联缺陷 | 备注 |
|---|---|---|---|---|---|
| SIT-AUTH-01 | | | | | |
| … | | | | | |
