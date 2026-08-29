# v1.6.2.2 第七轮全项目用户验收测试报告

| 项目 | 内容 |
|---|---|
| 测试执行人 | 智能体O |
| 测试日期 | 2026-08-29 至 2026-08-30 |
| 被测分支 | `main` |
| 被测提交 | `e38c3d1f9d4e012a3a49cb15eb10d5862b90c630` |
| 对照基线 | 第六轮被测提交 `8fee172a67daacc42809e173ecd20c5e3b8aac1b` |
| 被测版本 | `V1.6.2.2` |
| 测试类型 | 第七轮 UAT：第六轮整改复核、全项目回归、三版本兼容、119 条核心规则、双 worker 真实 HTTP、五类权限场景真实浏览器点击、PDF 视觉检查、安全与次生灾害故障注入 |
| 总体结论 | **不通过，不准进入发布** |

## 一、验收结论

Q 对第六轮三项缺陷的已知主路径均做出了有效整改：

- O-25 的 `RuntimeError` / `AttributeError` / `TypeError` 消息伪装矩阵已恢复为 500，真实 MySQL/TDSQL errno `2003/1045/1049` 仍能得到可读 422；
- O-26 的错误列类型、长度、NULL、显式 DEFAULT 以及 checksum 默认失败关闭主路径已生效；
- O-27 已彻底消除 8 项静态资源测试对偶然 `127.0.0.1:8000` 服务的依赖。8000 空闲和被无关 HTTP 服务占用两种场景，完整测试均为 `1538 passed, 28 skipped`。

全量自动化、正式实现门禁、三套 sqlglot 版本矩阵均通过；119 条规则的 1000/324/27/77 四组结果与第六轮基线逐项一致；10 个真实浏览器步骤、双 worker HTTP、RBAC、网关报告、日常巡检、索引 PDF 和跨库 EXPLAIN 均通过。

但独立故障注入发现 **1 个 BLOCK、2 个 MAJOR**，其中 O-30 会直接导致既有 v1.6.2.1 生产元数据库升级后无法启动，且当前“调和白名单”若长期留在 `.env` 中，可把未来同版本文件的任意漂移重新登记为合法基线。因此当前提交仍不得发布：

1. **O-28（MAJOR）**：双白名单仍把整个 `OSError` 家族视为可信连接异常。`PermissionError` / `FileNotFoundError` 只要消息含 `access denied` / `unknown database`，程序或文件系统错误仍被伪装成数据库 422。
2. **O-29（MAJOR）**：迁移声明未写 DEFAULT 时，验收逻辑完全跳过默认值比较；预存任意错误默认值仍被判为 `valid`。
3. **O-30（BLOCK）**：历史 v9 checksum 的生产升级没有安全、自动、一次性的闭环；不设置变量必然启动失败，设置持久变量又可为未来任意同版本漂移重新盖章。

## 二、范围、环境与方法

- Windows 隔离环境；元数据库 `tdsql_uat_o_r7_1622_20260829`，功能库和用户均使用 `tdsql_uat_o_r7` / `uat_o_r7` 前缀合成数据，未接触生产数据。
- 应用以 `AUTH_ENABLED=true`、Uvicorn `--workers 2`、`http://127.0.0.1:8008` 启动，真实网络 HTTP 与浏览器均覆盖多 worker 行为。
- 浏览器使用 Codex 内置 Chromium，按用户操作方式登录、展开菜单、选择实例/日期、输入 SQL、上传日志、点击审核/报告/EXPLAIN、查看大屏与权限按钮。
- 角色覆盖管理员、DBA、开发、审计员，以及“仅授予在线元数据审核、明确不授予实例管理”的隔离自定义角色。
- 核心审核覆盖 119 条注册规则、1000 条主语料、324 条解析边界、27 条 LOAD、77 条语句头、三套 sqlglot 版本，以及 R054/R077、UNIQUE 注释、KFN、CR/LF/CRLF 失败关闭。
- 次生灾害覆盖异常类型/消息交叉伪装、迁移列结构、历史 checksum、未来漂移、双 worker、端口污染、票据重放、XSS、旧巡检结果、跨库 EXPLAIN 和 PDF 视觉成品。

## 三、结果总览

| 测试组 | 结果 | 说明 |
|---|---:|---|
| Q 定向整改回归 | PASS | 43 passed / 28 skipped；O-25/O-26/O-27 已知用例均通过 |
| 全量自动化（8000 空闲） | PASS | `1538 passed, 28 skipped, 11 warnings` |
| 全量自动化（8000 被无关服务占用） | PASS | `1538 passed, 28 skipped, 11 warnings`，结束后精确清理本轮进程 |
| 正式实现门禁 | PASS | 三版本、冻结 71 项、全量回归、manifest、codestat、设计包哈希均通过；`RESULT PASS` |
| 三版本独立矩阵 | PASS | sqlglot `29.0.0 / 30.14.0 / 30.17.0`；每版 788 项 manifest、324 条边界、27 条 LOAD、77 条语句头均成功 |
| 119 条规则与 1000 条主语料 | PARTIAL PASS | 注册 119；主语料观察 114，补充命中 R035/R059 后共 116；R025/R038/R049 仍缺有效阳性证据 |
| 与第六轮基线差分 | PASS | 1000/324/27/77 四组 JSON 逐项完全相同 |
| 原始 R054/R077 问题 | PASS | TDSQL HASH 分片表和 `shardkey=noshardkey_allset` 广播表均未触发 R054/R077 |
| 双 worker 真实 HTTP | PASS | 29 个业务步骤 0 个 5xx；票据 100/100 首次消费成功、100/100 重放拒绝 |
| 真实浏览器 UAT | PASS | 10/10 步骤通过，10 张真实页面截图 |
| PDF 成品 | PASS | A4 单页、4190 字节；150 DPI 渲染后中文、得分、重复索引和列名均完整可读，无截断重叠 |
| O-25 整改 | **FAIL** | 程序异常主矩阵通过，但泛化 `OSError` 仍存在消息伪装，见 O-28 |
| O-26 整改 | **FAIL** | 显式错误结构失败关闭；无 DEFAULT 声明和历史 checksum 调和仍不安全，见 O-29/O-30 |
| O-27 整改 | PASS | 不再依赖外部 8000 服务，空闲/占用结果一致 |
| 安全与次生灾害 | **FAIL** | XSS、票据、RBAC 等通过；发现异常误分类、默认值漏检和 checksum 持久绕过 |

## 四、第六轮整改逐项复核

| 第六轮编号 | 第七轮结论 | 独立验证 |
|---|---|---|
| O-25 程序异常消息伪装成 422 | **主样例通过，类型边界仍未收口** | 5 组普通程序异常均为 500 + `X-Request-ID`；真实 2003/1045/1049 与断连仍准确映射；但 `PermissionError` / `FileNotFoundError` 被误映射为 422 |
| O-26 已登记迁移错误结构静默通过 | **部分通过，仍有发布阻断** | 错误类型已失败关闭；无 DEFAULT 声明时错误默认值仍通过；历史 v9 无安全升级闭环，持久调和变量还能放行未来漂移 |
| O-27 门禁依赖外部 8000 服务 | **完全通过** | 无服务时全量 1538；8000 被无关服务占用时仍为 1538，说明静态资源测试已进程内自洽 |

## 五、缺陷明细与可实施整改

### O-28（MAJOR）：`OSError` 全家族仍可用消息伪装成数据库连接 422

**独立复现：**

| 注入异常 | 当前转换 | API 状态 | 应有结果 |
|---|---|---:|---|
| `RuntimeError("can't connect to internal cache")` | `None` | 500 | 500，正确 |
| `AttributeError("connection refused while reading object")` | `None` | 500 | 500，正确 |
| `TypeError("timed out during pickle decode")` | `None` | 500 | 500，正确 |
| `PermissionError("access denied reading encryption key")` | `AuthenticationFailedError` | 422 | 500 |
| `FileNotFoundError("unknown database catalog file")` | `DatabaseNotFoundError` | 422 | 500 |

**发生原因：** `backend/services/connection_errors.py` 将 `_MESSAGE_FALLBACK_TYPES` 定义为驱动异常加整个 `OSError`、`TimeoutError`、`ConnectionError` 家族；而 `PermissionError` 和 `FileNotFoundError` 都继承 `OSError`。随后同一消息分支对 `access denied`、`unknown database` 直接做文本匹配，因此本地密钥文件权限错误、文件不存在等程序/部署缺陷仍会被包装成目标数据库认证或库不存在。

**处理机制：**

1. errno `2003/1045/1049` 继续只从可信 PyMySQL/TDSQL 驱动异常链提取。
2. `access denied` 和 `unknown database` 的文本兜底只允许用于可信驱动异常；不得用于泛化 `OSError`。
3. 网络类内建异常仅允许明确类型或明确网络 errno：`ConnectionRefusedError`、`ConnectionResetError`、`TimeoutError` / `socket.timeout`，或 `OSError.errno` 属于 `ECONNREFUSED/ETIMEDOUT/EHOSTUNREACH` 及对应 Windows 错误码。不要把整个 `OSError` 交给文本匹配。
4. 明确排除 `PermissionError`、`FileNotFoundError`；它们以及未知 `OSError` 返回 `None`，由统一 500 处理器记录完整堆栈并向用户返回脱敏信息和 `X-Request-ID`。
5. 新增“异常类型 × 五类连接短语 × errno 有/无 × 直接/因果链 × API”表驱动矩阵，至少包含文件系统、证书、密钥、缓存和序列化错误。

**验收标准：** 非驱动的文件系统/程序异常无论消息内容如何均为 500；真实数据库认证、库不存在、拒绝连接和超时仍准确为 422。

### O-29（MAJOR）：迁移未声明 DEFAULT 时错误默认值被静默接受

**独立复现：** 迁移声明 `ADD COLUMN note VARCHAR(32)`，预建同名列 `VARCHAR(32) DEFAULT 'unexpected'`，登记正确版本键和 checksum 后运行迁移。当前 `migration_error=null`，实际默认值仍为 `unexpected`，`failed_closed=false`。

**发生原因：** `_expected_column_spec()` 用 `None` 同时表示“DDL 没写 DEFAULT”和“显式默认 NULL”；`_verify_column()` 又只在 `exp["default"] is not None` 时比较。结果是未声明 DEFAULT 的迁移完全不校验现存列默认值，与文档宣称的“严格核对 DEFAULT”不一致。

**处理机制：**

1. 期望结构必须区分“是否声明 DEFAULT”和“DEFAULT 的值”，例如返回 `has_default` 与 `default_value` 两个字段，不能继续用单个 `None` 承担两种语义。
2. 对受控迁移文件，未声明 DEFAULT 时也必须验证 `information_schema.COLUMNS.COLUMN_DEFAULT` 是否符合该 DDL 在目标 TDSQL 版本上的实际规范化结果；不符即 `MigrationError`。
3. 显式覆盖：无 DEFAULT、`DEFAULT NULL`、空字符串、数字、布尔、`CURRENT_TIMESTAMP`、带空格字符串、大小写及引号规范化。若 TDSQL 对 TIMESTAMP 等类型存在隐式默认规则，应按“字段类型 + TDSQL 版本”建立明确规范化，而不是整体跳过。
4. 继续保持错误结构只失败关闭、不自动 ALTER 的原则，避免数据截断、锁表和不可逆覆盖。

**验收标准：** 任一已登记列的默认值与迁移声明或目标 TDSQL 规范化结果不一致时，启动失败且错误同时打印表、列、期望与实际值。

### O-30（BLOCK）：历史 checksum 升级无安全闭环，持久调和变量可放行未来任意漂移

**独立复现：**

1. 历史 `v9_090_connection_unique` checksum 为 `54ee2e97…`，当前 no-op 文件 checksum 为 `c6cf33bb…`。模拟现网旧登记后，不设置 `SCHEMA_CHECKSUM_RECONCILE`，启动确定抛出 `MigrationError`。
2. 设置 `SCHEMA_CHECKSUM_RECONCILE=v9_090_connection_unique` 后会重设为当前 checksum。
3. 不移除该变量，再把同一版本文件改为任意不同内容；由于当前文件没有 `ADD COLUMN` 声明，结构验收集合为空，迁移器再次无异常并把任意新 checksum 写成基线，`tamper_rebaselined=true`。

**生产影响：** 当前 systemd 服务永久读取安装目录 `.env`；`install.sh` 升级时保留既有 `.env`，只把新模板放到 `.env.new`；模板又没有该变量和一次性操作说明。已从历史版本升级到 v1.6.2.1、数据库仍登记旧 v9 checksum 的内网实例，升级本提交后会在两个 worker 启动阶段失败并被 systemd 循环重启。若运维为恢复服务把当前指引写入持久 `.env`，忘记删除又形成未来漂移绕过。Q 文档中的“生产库执行一次即可”不是可验证、可回滚、不可遗留的部署机制。

**首选处理机制：**

1. 对这一条已知历史变更建立代码内精确调和账本，键必须至少为 `{version_key, historical_checksum, current_checksum}`，只允许精确三元组 `v9_090_connection_unique / 54ee2e97… / c6cf33bb…`。
2. 自动调和前验证业务结构不变量：端点唯一约束 `uq_conn_endpoint` 已存在、名称唯一约束 `uq_conn_name` 已由后续迁移移除、端点无重复；任一不满足均失败关闭。
3. 精确三元组且结构不变量全部满足时，单事务更新 checksum，并写数据库操作审计与 ERROR 级日志。未知旧 checksum、未知新 checksum、其他版本或任意未来漂移一律失败关闭。
4. 删除或禁用当前仅按 `version_key` 匹配的长期环境变量路径。若必须保留人工通道，令牌也必须绑定 `version_key:old_checksum:new_checksum`，并做成一次性预检 CLI；成功后令牌不可再次生效，服务启动发现遗留令牌应拒绝继续。
5. 随版本补齐 v1.6.2.2 升级手册和 `install.sh` 预检：升级前备份、检测历史 checksum/结构、执行精确调和、验证两个 worker ready、失败回滚；不得要求运维手工长期编辑生产 `.env`。

**验收标准：**

- 携带历史已知 v9 checksum 的 v1.6.2.1 数据库无需持久开关即可一次升级成功；
- 已知 old→new 以外的任意 checksum 漂移均启动失败，设置旧变量也不得改写基线；
- 双 worker 并发只发生一次原子调和且结果一致；
- 升级完成后没有环境开关残留，后续同版本文件被篡改时必然失败关闭；
- 升级手册含精确检测、备份、执行、验证和回滚命令。

## 六、119 条核心规则与次生灾害结论

- 注册规则仍为 119 条，1000 条主语料结果与第六轮被测基线逐项完全相同，未发现 Q 本轮修改造成规则范围漂移。
- 主语料观察到 114 条规则；独立补充语料命中 R035、R059，共证明 116 条可观测命中。R025、R038、R049 的现有样例仍未命中目标规则，不能把“注册存在”写成“有效性已证明”。
- 1000 条语料仍有 3 个已知精确期望差异，均为 R036/R037 缺失；与第六轮完全一致，不是本轮新增回归。
- 324 条解析边界、27 条 LOAD、77 条语句头及三版本执行均成功；四组输出与第六轮基线 JSON 完全一致。
- TDSQL HASH 分片表未触发 R077；`shardkey=noshardkey_allset` 广播表未触发 R054/R077；CR 残缺 VIEW 命中 R030；真实浏览器 `#` 注释 LOAD XML 命中 R042 ERROR。
- 结论是“本次修改没有破坏 119 条规则的既有行为”，而不是“119 条规则全部已有独立阳性证明”。后续仍应为 R025/R038/R049补足有效 TDSQL 实物或可信合成夹具。

## 七、真实浏览器用户验收

| 步骤 | 角色 | 实际操作 | 结果 |
|---|---|---|---|
| B01 | 管理员 | 即时审核 `#` 注释后的 LOAD XML | PASS：显示 R042 ERROR |
| B02 | 管理员 | 上传 4 条有效、3 条无效的网关日志并查看报告 | PASS：4/7、57.1%、跳过 3 条及样例均显示 |
| B03 | 管理员 | 打开包含脚本闭合和 `onerror` 载荷的报告 | PASS：脚本未执行，父页面不可达，sandbox 生效，URL 无 access token |
| B04 | 管理员 | 2026-08-26/27 巡检对比后切换 2026-08-28/29 手动采集 | PASS：先显示 13 行；失败后旧结果清为 0 行 |
| B05 | 管理员 | 查看索引大屏并导出 PDF | PASS：健康分 96、重复组 1；PDF 显示 `idx_code_copy` / `code` |
| B06 | 管理员 | 指定其他数据库执行 EXPLAIN | PASS：`key=idx_customer`、`Using index` |
| B07 | 开发 | 查看实例与在线元数据实例列表 | PASS：3 条实例、管理按钮 0；在线元数据选项 3 个 |
| B08 | 审计员 | 查看实例管理 | PASS：3 条只读，新建/编辑/删除均为 0 |
| B09 | DBA | 查看实例管理 | PASS：新建 1，编辑 3，删除 3 |
| B10 | 自定义元数据只读角色 | 仅分配在线元数据审核，不分配实例管理；登录并打开在线元数据 | PASS：实例管理菜单 0，实例选项仍为 3 个 |

B07/B08/B09 证明被分配实例管理能力的非管理员角色只能读取，只有 DBA 和管理员拥有写控件；B10 独立证明实例读取已与实例管理菜单解耦，未分配菜单也不会导致其他模块下拉为空。

## 八、HTTP、安全与成品体验

- 认证开启、双 worker 下完成 29 个 HTTP 业务步骤，未出现 5xx；网关票据跨 worker 首次消费 100/100 成功，重放 100/100 拒绝，GET 签发 405，错绑定 401，数据库仅存票据哈希。
- 空文件、全无效、低于覆盖阈值、部分可解析、XSS 载荷均得到符合预期的状态与用户提示；报告 iframe 只使用一次性票据并启用 sandbox。
- 离线实例返回可读 422；本轮发现的 O-28 不是普通离线链路，而是特制文件系统异常的错误分类。
- PDF 为 A4 单页，150 DPI 渲染后中文可读、表格不重叠、不截断，健康分 96、重复索引组和包含列准确。
- 所有服务、8000 占位进程和浏览器标签均在测试结束后精确关闭；未留下测试监听端口。

## 九、放行门槛

第八轮 UAT 前至少必须完成：

1. 按 O-28 收窄异常类型与网络 errno 白名单，补足文件系统/密钥/证书消息伪装 API 矩阵。
2. 按 O-29 区分“未声明 DEFAULT”和“显式 DEFAULT NULL”，补齐 TDSQL 默认值规范化矩阵。
3. 按 O-30 建立只接受已知 old→new checksum 三元组的一次性迁移闭环，移除长期 version-key 开关绕过，并补齐 v1.6.2.2 升级手册和双 worker 生产升级测试。
4. 全量 pytest、正式门禁、三版本 788/324/27/77、1000 条规则差分继续全部通过。
5. 重跑真实浏览器管理员/DBA/只读/未分配菜单四类权限、巡检、网关报告、索引 PDF 和跨库 EXPLAIN。

在 O-30 关闭且 O-28/O-29 完成复测前，**不建议创建发布标签，不准将 v1.6.2.2 部署到生产环境**。

## 十、证据索引

完整证据位于 `docs/evidence/v1.6.2.2-uat-o-r7/`：

- 自动化与门禁：`full_regression*.txt/xml`、`implementation_gate.txt`、`independent_matrix.json`；
- 规则差分：`rule_probe_current.*`、`round7_diff.json`、`edge/load/head_*.json`、`supplemental_rule_probe.*`；
- 缺陷复现：`targeted_probe.py/json`；
- HTTP：`http_round7.py`、`http_results.json`；
- 浏览器：`browser_steps.json`、`01` 至 `10` PNG；
- PDF：`index_actual.pdf`、`index_actual_page1.png`；
- 汇总与复现：`summary.json`、`README.md`、`run_round7.py`、`run_gate.py`、`run_occupied_port.py`。
