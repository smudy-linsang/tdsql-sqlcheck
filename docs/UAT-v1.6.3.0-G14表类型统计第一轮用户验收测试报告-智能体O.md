# v1.6.3.0 G14 表类型统计第一轮用户验收测试报告

测试人：智能体 O

测试日期：2026-09-02

被测提交：`5f4e54b1a84db5fe4b8d99506fd818d0884083c3`

测试方式：真实浏览器点击 + 独立 `information_schema` 核对 + 自动化回归

结论：**不通过，当前不准 UAT 签字。**

## 1. 给项目负责人的结论

G14 的主要业务能力已经具备：用户能查询全部业务库或指定库；集中式实例只统计 `BASE TABLE`、不统计视图；空库返回 0；历史记录包含操作人且可回放；连接到非 Proxy 端点时能明确识别 1064，并保证失败库不污染汇总。浏览器结果与独立 SQL 核对一致。

但是，本轮发现 **1 个 BLOCK、2 个 MAJOR、1 个 MINOR**：

1. **BLOCK：表类型统计结果没有绑定当前用户、实例和查询条件。** 查询失败、切换实例、退出后换用户，旧结果仍继续显示。除误读风险外，换用户后还能看到前一用户的实例名、库名和统计结果，属于会话隔离失效。
2. **MAJOR：被测发布的版本标识仍为 1.6.2.2。** 登录页、页面标题、静态资源版本参数、`VERSION`、`APP_VERSION` 与 `/health` 均未提升到 1.6.3.0，UAT 证据无法与目标发布准确对应。
3. **MAJOR：离线实例在连接解析阶段抛出未处理异常。** `POST /api/v1/table-type-stats/run` 返回 HTTP 500，服务端记录完整堆栈，页面没有得到可执行的连接失败提示。
4. **MINOR：auditor 的写操作反馈不符合人类用户预期。** 页面给 auditor 展示可点击的“统计表类型”按钮，后端虽正确返回 403，前端却只显示“执行失败”，没有说明权限不足。

此外，因本地没有真实 TDSQL Proxy，本轮不能签署分布式成功路径及 T20 最大实例性能结论。这两项不是本轮新发现的代码缺陷，但仍是上线前必须补齐的环境验收门槛。

## 2. 基线、环境与证据口径

| 项目 | 实际值 |
|---|---|
| 代码分支/提交 | `main` / `5f4e54b1a84db5fe4b8d99506fd818d0884083c3` |
| 上一阶段依据 | `SIT3-v1.6.3.0-G14表类型统计SIT放行结论报告-ClaudeA.md`，结论为准入 UAT |
| 浏览器入口 | `http://127.0.0.1:18800/`，从登录、菜单、页签、输入、按钮和抽屉实际操作 |
| 元数据库 | 隔离库 `tdsql_uat_o_g14_r1_1630` |
| 合成业务库 | A：2 BASE TABLE + 1 VIEW；B：1 BASE TABLE；EMPTY：0 表 |
| 实例样本 | 集中式可用、强制分布式但实际为普通 MariaDB、端口 1 离线实例 |
| 角色 | developer、auditor、仅有“深度诊断-表类型统计”的最小权限自定义角色 |
| 凭据处理 | 使用本地合成凭据；口令和令牌不写入报告及截图说明 |
| 旧服务保护 | 原有端口 8000 服务未操作；本轮使用独立 18800 服务 |

证据目录：[v1.6.3.0-uat-o-r1](evidence/v1.6.3.0-uat-o-r1/README.md)。截图只证明画面发生过；数值正确性另用 `information_schema` 独立查询核对。HTTP 200 也不自动等同于业务验收通过。

## 3. 测试结果总览

| 编号 | 用户真实操作 | 实际结果 | 判定 |
|---|---|---|---|
| UAT-G14-01 | 打开登录页，查看版本 | 显示 `V1.6.2.2` | **失败：O-G14-02** |
| UAT-G14-02 | developer 进入深度诊断；未选实例时查看按钮和历史 | 统计按钮禁用；历史提示“请先选择实例” | 通过 |
| UAT-G14-03 | 选集中式实例，库名留空，点击统计 | 57 库、2207 总表、2207 单表、广播/分片 0；完成提示正确 | 通过（仅本地数据集） |
| UAT-G14-04 | 输入 `tdsql_uat_g14_r1_a`，点击统计 | 2 总表/2 单表；独立 SQL 为 2 BASE TABLE + 1 VIEW | 通过，视图排除正确 |
| UAT-G14-05 | 输入空库 | 1 库/0 表，逐库状态 OK | 通过 |
| UAT-G14-06 | 输入不存在库 | 错误提示明确，但页面仍显示上一轮空库结果 | **失败：O-G14-01** |
| UAT-G14-07 | 输入系统库 `mysql` | 明确提示“不允许统计系统库”，但旧结果仍保留 | 保护通过；展示失败并入 O-G14-01 |
| UAT-G14-08 | 打开历史，点击指定库运行记录 | 时间、操作人、范围、汇总和逐库明细正确 | 通过 |
| UAT-G14-09 | 强制分布式连接普通 MariaDB，统计指定库 | `PROXY_CMD_FAILED` + `NOT_DISTRIBUTED_ENDPOINT`；1 失败库不进汇总；主数字标记不可用 | 通过（异常路径） |
| UAT-G14-10 | 从分布式样本切换到离线实例并点击统计 | 顶部已显示离线实例，但正文仍是上一实例结果；POST 返回未处理的 HTTP 500 | **失败：O-G14-01、O-G14-04** |
| UAT-G14-11 | developer 退出，最小权限用户登录并进入深度诊断 | 菜单只剩治理概览/深度诊断，页签只剩表类型统计；但直接看到上一用户结果 | 权限裁剪通过；**会话隔离失败 O-G14-01** |
| UAT-G14-12 | 最小权限用户选择集中式样本并统计 A 库 | 1 库/2 表，功能可用 | 通过 |
| UAT-G14-13 | auditor 进入表类型统计，查历史并点击统计 | 历史可读；POST 被 403 拒绝；仅提示“执行失败” | 安全拒绝通过；**体验失败 O-G14-03** |
| UAT-G14-14 | 冒烟打开即时审核、慢 SQL、实例体检和原有深度诊断页签 | 页面与主要输入/按钮正常呈现 | 抽样通过 |
| UAT-G14-15 | 执行专项自动化 | 123 passed | 通过 |
| UAT-G14-16 | 执行未改模块抽样回归 | 80 passed | 通过 |
| UAT-G14-17 | 真实 TDSQL Proxy 分类与最大实例性能 | 本地无对应环境 | **未验收，发布门槛待补** |

## 4. 正向能力证据

### 4.1 集中式全部业务库与指定库

全部业务库结果见 [02-central-all.png](evidence/v1.6.3.0-uat-o-r1/02-central-all.png)。页面显示 57 个业务库、2207 张总表，集中式口径下全部为单表，广播表和分片表为 0。

指定库结果见 [03-central-specific-view-excluded.png](evidence/v1.6.3.0-uat-o-r1/03-central-specific-view-excluded.png)。独立查询结果为：

```text
tdsql_uat_g14_r1_a / BASE TABLE = 2
tdsql_uat_g14_r1_a / VIEW       = 1
```

浏览器返回总表 2、单表 2、广播表 0、分片表 0，与 `TABLE_TYPE='BASE TABLE'` 口径一致，VIEW 未计入。

### 4.2 历史回放与异常路径

- [05-history-detail.png](evidence/v1.6.3.0-uat-o-r1/05-history-detail.png)：历史记录含操作人 `uat_g14_developer`，点击行可看到 A 库 2/2/0/0 的逐库明细。
- [06-distributed-nonproxy.png](evidence/v1.6.3.0-uat-o-r1/06-distributed-nonproxy.png)：普通 MariaDB 不支持 `/*proxy*/show table ...`，页面明确提示可能连接到 TXSQL 后端而非 Proxy，失败库不进任何汇总，未把 0 冒充真实结论。
- 不存在库和系统库均返回可理解的业务错误；问题在于失败后没有清除旧结果，而不是后端错误语义缺失。

### 4.3 自动化回归

```text
python -m pytest tests/test_table_type_stats.py \
  tests/test_rbac_path_coverage.py \
  tests/test_design_appendix_matches_repo.py \
  tests/test_app_routes_integrity.py -q
=> 123 passed, 3 warnings, 27.18s

python -m pytest tests/test_rules.py \
  tests/test_sit_rules.py \
  tests/test_uat_frontend.py \
  tests/test_v2_rbac_matrix.py \
  tests/test_v3_rbac_instances.py -q
=> 80 passed, 5 warnings, 41.64s
```

这些结果证明既有自动化门禁未回退，但测试没有覆盖“同一浏览器跨用户/跨实例旧结果残留”，因此与 BLOCK 并不矛盾。

## 5. 正式缺陷与照图施工整改方案

### 5.1 UAT-O-G14-01（BLOCK）：结果未绑定用户、实例和查询条件

#### 复现

可用以下任一条稳定复现：

1. developer 先统计空库成功，再把输入改成不存在库并点击统计。页面弹出“数据库不存在”，但统计摘要和明细仍是上一轮空库。[证据](evidence/v1.6.3.0-uat-o-r1/04-error-stale-result.png)
2. 先在分布式样本产生失败结果，再切换到离线实例。顶部实例名已变化，正文仍显示上一实例的库名、告警和逐库行。[证据](evidence/v1.6.3.0-uat-o-r1/07-offline-stale-result.png)
3. developer 退出，最小权限用户登录并进入表类型统计。新用户无需查询即可看到上一用户的实例、库名和结果。[证据](evidence/v1.6.3.0-uat-o-r1/08-cross-user-stale-result.png)

#### 原因

- `frontend/static/js/app.js:218` 把结果保存在全局响应式对象 `deepResult.tabletype`。
- `runTableTypeStats`（约 832 行）只在成功响应后覆盖结果；发起新查询和失败时不清空旧结果。
- `clearRoleScopedState`（约 415 行）没有清理 `savedConnections`、`deepConnId`、`deepDb`、`deepResult` 及 G14 历史抽屉状态。
- `watch(deepConnId)`（约 2047 行）只加载网关/PPT数据，没有使已有深度诊断结果失效。
- 页面（`frontend/index.html:1844` 起）只判断 `deepResult.tabletype` 是否存在，没有校验它是否属于当前用户、当前实例和当前输入。

#### 必须按以下顺序修改

1. **建立一个统一清理函数。** 在 `frontend/static/js/app.js` 的 G14 状态附近新增 `resetTableTypeState()`，一次性清理：
   - `deepResult.tabletype = null`
   - `tabletypeWarnAll = false`
   - `tabletypeHistoryVisible = false`
   - `tabletypeHistory = []`
   - `tabletypeDetailItems = []`
   - `tabletypeDetailAll = []`
   - `tabletypeDetailExpand = false`
   - `tabletypeDetailLoading = false`

2. **查询开始即失效旧结果。** `runTableTypeStats()` 先保存本次 `{username, connectionId, database, sequence}`，再调用 `resetTableTypeState()`，然后请求接口。接口错误时保持空态，绝不能恢复旧结果。

3. **防止异步串台。** 每次统计递增 `sequence`。响应回来时，如果序号、登录用户名、`deepConnId` 或规范化后的 `deepDb` 任一与发起时不同，直接丢弃响应，不能把旧实例的迟到响应写回新页面。

4. **实例或库名变化即清空实时结果。** 用 `watch([deepConnId, deepDb], ...)` 使旧结果失效；实例变化时还要关闭历史抽屉并清空历史列表/明细。不要等用户再点一次按钮才清。

5. **登录态彻底隔离。** 在 `clearRoleScopedState()` 中至少增加：
   - `savedConnections.value = []`
   - `currentConnectionId.value = ''`
   - `deepConnId.value = ''`
   - `deepRightConnId.value = ''`
   - `deepDb.value = ''`
   - `deepLoading.value = ''`
   - 把 `deepResult` 的全部键恢复为 `null`
   - 调用 `resetTableTypeState()`
   - 删除或重新校验 `localStorage.tdsql_conn`，不能把前一用户无权访问的实例自动带给后一用户。

6. **页面展示增加范围绑定。** 只有结果携带的 `connectionId/database/username` 与当前上下文完全一致时，才渲染摘要、告警和明细；摘要旁明确显示“结果范围：实例名 / 全部业务库或指定库 / 采集时间”。前端本地补充范围字段可以用于展示，但后端历史记录仍是事实源。

#### 必须新增的自动化与浏览器复测

- 成功查询 A 库 → 改不存在库 → 失败后摘要、告警、明细全部为空。
- 集中式成功 → 切分布式/离线实例 → 切换瞬间旧结果消失；连接失败后也不恢复。
- developer 查询 → 退出 → auditor/最小权限用户登录 → 不出现前一用户实例、输入、结果或抽屉数据。
- 发起慢请求 → 请求未结束时切实例 → 旧响应回来后仍不得显示。
- A 库成功 → 只修改输入为 B 库但不点击 → 页面不得继续把 A 库数字放在 B 库输入框下面。

关闭标准：以上 5 条浏览器路径全部通过，且新增静态/单元门禁能在删掉任一清理点时失败。只在错误回调里加 `deepResult.tabletype=null`，不能关闭本缺陷。

### 5.2 UAT-O-G14-02（MAJOR）：1.6.3.0 发布标识未落盘

#### 复现与原因

[01-login-version.png](evidence/v1.6.3.0-uat-o-r1/01-login-version.png) 显示登录页 `V1.6.2.2`；`GET /health` 返回 `{"status":"ok","version":"1.6.2.2"}`。当前硬编码位置包括：

- `VERSION:1`
- `backend/config.py:25-26`
- `frontend/index.html:8` 页面标题
- `frontend/index.html:16,18` 两个 CSS 缓存参数
- `frontend/index.html:30` 登录页版本
- `frontend/index.html:2870` app.js 缓存参数

历史注释中的 `v1.6.2.2-UAT-*` 是缺陷追踪标识，不应批量替换。

#### 施工方案

1. 将上述发布标识全部改为 `1.6.3.0`；`APP_DESCRIPTION` 改为本次 G14“深度诊断-表类型统计”说明。
2. 确保静态资源 URL 的版本参数同步为 `1.6.3.0`，避免浏览器继续使用旧 JS/CSS。
3. 新增发布一致性测试，至少断言：`VERSION`、`config.APP_VERSION`、`/health`、OpenAPI version、HTML title、登录页、CSS/JS 查询参数全部为同一版本。
4. 重启全新进程并用无缓存新会话截图；不能用旧进程或只改页面文字作为关闭证据。

关闭标准：新登录页、浏览器 title、`/health`、系统信息、启动日志与 `VERSION` 均显示 1.6.3.0，且自动化一致性门禁通过。

### 5.3 UAT-O-G14-03（MINOR）：auditor 按钮可点且 403 文案被吞

#### 复现与原因

auditor 能进入表类型统计并查看历史，符合只读角色定位。但“统计表类型”仍可点击；后端中间件正确返回：

```json
{"code":403,"message":"当前角色(auditor)无权执行该操作"}
```

`frontend/static/js/app.js:787` 只读取 `d.detail`，未读取中间件使用的 `d.message`，所以用户只看到兜底文案“执行失败”。

#### 施工方案

1. 新增统一 `apiErrorMessage(data, fallback)`：依次安全读取字符串型 `detail`、`message`，若 `detail` 是 FastAPI 校验数组则提取首条可读消息；禁止输出 `[object Object]`。
2. `_deepPost`、历史加载和明细加载统一使用该函数，不要只局部修一处。
3. 新增 `canRunTableTypeStats`：admin、dba、developer 及拥有该菜单写权限的自定义业务角色可执行；auditor 按钮禁用或隐藏，并给出“审计员仅可查看历史”的提示。后端 403 保持不变，不能依赖前端做授权。
4. 测试 auditor 的按钮状态、直接构造 POST 仍为 403且文案可读；developer/DBA/最小权限自定义角色仍能执行，避免误伤。

关闭标准：auditor 不再遇到可点击后才失败的假入口；即使绕过 UI，403 仍存在且前端能显示服务端可读原因。

### 5.4 UAT-O-G14-04（MAJOR）：离线实例连接失败变成未处理 500

#### 复现与原因

developer 选择 `G14 UAT 离线样本 (127.0.0.1:1)`，点击“统计表类型”。请求返回 HTTP 500；服务日志见 [10-offline-500.txt](evidence/v1.6.3.0-uat-o-r1/10-offline-500.txt)，页面在请求结束后仍保留旧结果见 [10-offline-500.png](evidence/v1.6.3.0-uat-o-r1/10-offline-500.png)。

调用链是：

```text
backend/api/table_type_stats.py:53  pool = _pool(body.connection_id)
backend/api/table_type_stats.py:38  return registry.get(cid)
connection_registry.register -> pool.get_connection -> pymysql OperationalError(2003)
```

`run()` 的 `try` 从当前第 54 行才开始，因此 `_pool()` 在第 53 行抛出的连接异常完全绕过后面的 `except Exception`，进入全局 500。现有 `_pool()` 又只转换 `ConnectionNotFoundError`，没有转换“登记存在但当前不可连接”的数据库驱动异常。

#### 施工方案

1. 将 `pool = _pool(body.connection_id)` 放入路由的完整 `try` 边界，不能只包 `svc.run_stats()`。
2. 复用项目既有 `backend/services/connection_errors.py` 的严格白名单转换能力，只把可信驱动异常链中的连接类 errno（本例 2003，以及项目已认可的连接拒绝/超时/认证类编号）映射为连接领域错误；未知程序异常必须继续为 500，禁止 `except Exception -> 422`。
3. 对可预期离线/超时返回稳定的 422 或项目统一连接错误状态码，响应只含可执行信息，例如“实例连接失败：请检查地址、端口、网络和账号；本次未产生统计结果”。不得把主机口令、驱动堆栈或 SQL 原文返回浏览器。
4. 前端通过统一 `apiErrorMessage` 显示服务端业务消息，并按 O-G14-01 要求保持结果空态。
5. 日志保留 request id 与内部异常堆栈用于运维排查，但访问日志应体现已处理的 4xx，而不是应用异常 500。

#### 必须新增的测试

- `registry.get()` 抛 `pymysql.err.OperationalError(2003, ...)`：接口为可读 4xx，无未处理异常，结果不落历史。
- 连接超时和认证失败：各自映射稳定且不泄漏凭据。
- `registry.get()` 抛普通 `RuntimeError`：仍为 500，证明没有把代码缺陷伪装成连接错误。
- 浏览器离线点击：loading 能恢复、旧结果为空、提示可读、可切回可用实例继续成功查询。

关闭标准：上述四类测试通过，服务日志不再出现离线实例的未处理 ASGI traceback，且未知程序异常仍然失败关闭。

## 6. 未改模块回归结论

浏览器抽样打开治理概览、即时审核、慢 SQL、实例体检、集群巡检等入口，主要输入与按钮均正常呈现；再以 80 条规则、前端、RBAC 和实例权限测试做有界回归，未发现本次 G14 改动导致的新增退化。

这只是“未改模块简单校验后放行”，不代表本轮重新执行了全项目所有业务场景。专项之外不扩大验收结论。

## 7. 内网 UAT 必补门槛

### 7.1 真实分布式结果对账

在用户已提供语法可用的 TDSQL Proxy 端口，对同一业务库执行产品页面与三条原厂查询：

```sql
/*proxy*/show table without shardkey;
/*proxy*/show table with noshardkey_allset;
/*proxy*/show table with shardkey;
```

并用 `information_schema.TABLES WHERE TABLE_TYPE='BASE TABLE'` 对总基线核对。按现有 SIT 放行条件，至少完成 `lzbj_ecif` 的六数对账：总表 215、二级物理子表 0、单表 117、广播表 98、逻辑基线 215、分片表 78；若内网当前数据已变化，应附同一采集时刻原始输出并解释差异，不能机械套旧数字。

### 7.2 T20 最大实例性能

在最大可见库/表量实例分别记录普通 `IN (...)` 与 `BINARY ... IN (...)` 方案的 `EXPLAIN`、实际耗时、返回行数和采集总时长；验证不会因大小写修正造成不可接受的全表扫描或超时。需保留 SQL、执行计划和时间戳，不接受口头“很快”。

## 8. 下一轮准入条件

第二轮 UAT 前必须同时满足：

1. O-G14-01 BLOCK 已修复并补齐跨查询、跨实例、跨用户、迟到响应自动化。
2. O-G14-02 版本标识全部统一为 1.6.3.0。
3. O-G14-04 离线实例异常边界修复，可信连接错误不再成为未处理 500。
4. O-G14-03 权限按钮与错误文案修复。
5. 专项 123、抽样回归 80 及项目正式全量测试无新增失败。
6. 提供真实内网 TDSQL 分布式六数对账与 T20 性能证据；若环境仍不可用，第二轮可复测本轮缺陷，但仍不能签署最终上线放行。

最终判定：**第一轮 UAT 不通过，当前不准出。**
