# TDSQL-SQLCheck v1.5.3 原始慢日志采集开发自测报告

| 项目 | 结论 |
|---|---|
| 测试对象 | 慢SQL治理 / 原始慢日志独立子模块（`ssh_exporter_v1`） |
| 测试阶段 | 开发自测（SIT 前） |
| 测试日期 | 2026-08-02 |
| 测试基线 | `main@68954e5` 之上的隔离实施分支 |
| 判定 | **工程自测通过，允许提交独立复测/质检；不等同于真实环境投产准入** |
| 数据分级 | 仅合成慢日志、独立测试元数据库；未使用真实主机、真实日志、真实账号、私钥或口令 |

## 1. 测试控制与可追溯性

本轮按商业银行信息科技项目的开发、测试、变更分离原则执行：

1. 在 `C:\TDSQL_SQLCHECK\_raw_slowlog_implementation` 隔离工作区实施；未修改共享 `main` 工作区和既有本地运行环境。
2. 测试元数据库为本地 Docker MySQL 的 `tdsql_sqlcheck_test`，与业务库、真实 TDSQL 和生产元数据库隔离。
3. 测试日志均为构造样本；测试账号文件只在测试临时目录创建，并在用例结束后删除。仓库中仅保存 Secret 引用名，不保存私钥或口令。
4. 本报告记录通过项、基线失败项与未执行的真实环境门禁；未将“模拟通过”表述为“生产已验证”。
5. 未进行外部写操作：没有登录真实 TDSQL、没有 SSH 到内网主机、没有创建远端账户、没有上传二进制或修改真实日志目录。

## 2. 本次实现范围

新增独立的原始慢日志链路，不写入或复用既有 `slow_queries`、`scan_tasks`：

- 迁移 `v7/070_raw_slow_log_collection.sql`：采集源、节点、游标、事件、Probe 证据、运行审计和保留策略；
- `raw_slowlog_exporter`：Go 静态受限导出器，ForceCommand 标准输入 JSON、标准输出 NDJSON；
- OpenSSH 严格主机密钥校验、固定 argv、无远端命令、无 shell、Secret 引用解析；
- 完整块解析、SQL 统一脱敏、完整模板指纹、8 KiB 展示截断、事件/游标同事务和 origin 幂等；
- 源配置、Probe、启停、异步手工采集、运行审计、事件筛选/HTML/CSV 导出、调度、保留、Prometheus 指标和积压告警；
- 前端“慢SQL治理 / 原始慢日志”独立页面；
- 容器增加 `openssh-client`，发布脚本随包构建目标架构导出器和 SHA-256 文件。

设计依据见 [DESIGN-v1.5.3-原始慢日志采集.md](DESIGN-v1.5.3-原始慢日志采集.md)。

## 3. 执行环境与证据

| 类别 | 环境/命令 | 结果 |
|---|---|---|
| Python 定向 | `python -m pytest -q tests/test_raw_slowlog_parser.py tests/test_raw_slowlog_ssh.py tests/test_raw_slowlog_contract.py tests/test_raw_slowlog_integration.py tests/test_rbac_path_coverage.py tests/test_no_hardcoded_secrets.py` | **23 passed** |
| Go 导出器 | `go test ./...`（导出器目录） | **passed**（4 个用例） |
| Linux 产物 | `GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go build ...` | **passed**，生成 Linux amd64 静态二进制 |
| 前端语法 | `node --check frontend/static/js/app.js` | **passed** |
| Python 语法 | `python -m py_compile ...` | **passed** |
| 差异格式 | `git diff --check` | **passed** |
| Compose | `docker compose config -q` | **passed**；仅提示既有 `version` 字段已废弃 |
| 脚本语法 | `bash -n deploy/build_raw_slowlog_exporter.sh deploy/make_release.sh deploy/preflight_check.sh` | **passed** |
| 镜像 | `docker build --pull=false -t tdsql-sqlcheck:raw-slowlog-selftest .` | **passed** |
| 镜像依赖 | `docker run --rm tdsql-sqlcheck:raw-slowlog-selftest ssh -V` | **passed**，镜像内 OpenSSH `10.0p2` |

说明：Python 测试框架仍输出既有 FastAPI/Starlette 弃用警告和 Pydantic `schema` 字段遮蔽警告；均非本次模块新增失败，未改变测试判定。本机 `bash.exe` 指向 WSL，WSL 内未安装 Go，因此未在该 WSL 环境实际运行打包脚本；已以 Windows Go 完成等效 Linux 交叉编译。正式打包机必须具备 Linux Go 工具链，脚本会在缺失时失败关闭。

## 4. 需求—测试追踪

| 编号 | 覆盖方式 | 结果 |
|---|---|---|
| U01/U02 | 标准块、跨行 SQL、偏移和 `# Time` 解析 | 通过 |
| U03 | EOF 不完整尾块不提交、不推进 | 通过 |
| U04/U19 | 字符串、数值、JSON 字符串、十六进制、注释脱敏；完整指纹与 8 KiB 截断 | 通过 |
| U05/U06 | 重放去重；模拟游标写入故障后事件事务回滚 | 通过 |
| U07/U08/U17 | 初始 tail 后置锚点；锚点不一致创建新 generation | 模拟通过；真实 rename/copytruncate 仍需 §6 验证 |
| U09/U10/U11 | 严格 known_hosts 失败映射 E5021；固定 argv；路径控制字符拒绝；协议/源键/锚点异常失败关闭 | 通过 |
| U12/U13 | 路径权限映射、服务层角色校验；非管理员源详情掩码 | 通过 |
| U14 | 源级租约冲突时拒绝第二个手工 run | 通过 |
| U15 | HTML 报告静态覆盖不完整/零行非无慢SQL提示代码审查 | 通过代码审查；由独立复测补浏览器验收 |
| U16 | 独立 v7 迁移、实际测试元数据库初始化、`retention_policies` 保留策略 | 通过 |
| U18 | Probe 以主机密钥摘要/存储身份/文件身份拒绝已启用源重复文件 | 通过代码审查；由独立复测补双节点实测 |
| U20 | 单轮连续拉批实现、积压字节/滞后指标、告警去重与恢复 | 告警去重/恢复模拟通过；由独立复测补高吞吐追赶压测 |

## 5. 全量回归结果与基线例外

执行 `python -m pytest -q` 的结果：**1191 passed，28 skipped，3 failed**，总耗时约 152 秒。

三个失败项均属于既有“旧慢 SQL 扫描的 monitordb/digest 时间窗口”行为：

1. `tests/test_monitordb_time_window.py::test_time_window_has_per_source_note`
2. `tests/test_monitordb_time_window.py::test_time_window_tooltip_covers_all_three_sources`
3. `tests/test_smoke_multi_set.py::TestFetchSlowQueries::test_fetch_digest_requires_time_window`

已在未改动的共享基线 `C:\TDSQL_SQLCHECK\TDSQL-SQLCheck`、`main@68954e5` 单独重跑上述三项，结果同样为 **3 failed**。因此本轮将其登记为**既有基线失败**，不修改旧功能，也不作为本模块的回归引入项。独立质检时应单列既有缺陷单，不应因本模块合并而关闭或掩盖。

## 6. 发布前必须完成的真实环境门禁

以下项目未在开发自测中执行，任一未满足时采集源必须保持禁用：

1. 由运维按受控变更在每台实际 Proxy/Gateway 日志主机安装对应架构的导出器，核验 SHA-256、`--version` 和 ForceCommand；不得由 CheckSQL 自动上传。
2. 创建 `tdsql_log_reader` 专用账户，仅授予审批慢日志目录的只读 ACL；禁止 root、密码认证、交互 shell、端口转发和 SCP。
3. 在 CheckSQL 部署机的受控 Secret 目录配置 `<credential_ref>.key` 和 `<known_hosts_ref>.known_hosts`，私钥/known_hosts 内容不得进入数据库、页面、日志或 Git。
4. 仅允许 CheckSQL 部署机私网地址访问目标 SSH 端口；保持公网关闭。
5. 管理员录入源后先 Probe。Probe 必须确认协议、导出器 1.x 版本、`# Time` / `# Query_time` 格式签名、文件清单和重复文件防护，所有节点通过后才允许启用。
6. 在经批准的 SIT 窗口执行可识别的受控慢 SQL，确认 `event_time` 与 Proxy 日志 `# Time` 一致；再做 rename rotation、copytruncate 后快速写回、双 Proxy 和高于单批上限的追赶压测。
7. 复测通过后，按项目变更流程完成代码审查、测试报告审核、发布审批、回滚包/回滚步骤核验和生产上线窗口审批。

## 7. 自测结论与准出建议

开发自测结论为：**提交独立复测/质检**。新增模块的本地功能、安全失败方向、迁移隔离、容器依赖和 Linux 导出器构建均已验证；全量回归中不存在由本次改动引入的失败。

本结论不授予生产上线权限。真实主机、SSH 信任、只读目录、日志字段、轮转行为和容量/吞吐均未提供给本地开发环境，必须按第 6 节完成独立 SIT/UAT 实测，并由变更审批流程决定最终投产。
