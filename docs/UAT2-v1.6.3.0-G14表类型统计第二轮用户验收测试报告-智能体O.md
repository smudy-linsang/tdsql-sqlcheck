# v1.6.3.0 G14 表类型统计第二轮用户验收测试报告

测试人：智能体 O

测试日期：2026-09-02

被测提交：`4c7a737c93e0a069c63ad929e6af2a4f854dafe8`

测试方式：真实浏览器点击 + 独立 `information_schema` 对账 + API/RBAC 复核 + 自动化全量回归

结论：**第二轮 UAT 不通过，暂不签字。发现 1 个 MAJOR、1 个 MINOR；两项关闭后进入第三轮复测。**

## 1. 给项目负责人的结论

Q 对第一轮四项缺陷的主要整改有效：

- 发布版本已统一为 `1.6.3.0`；
- 改库、切实例、失败、退出换用户时，旧统计摘要、告警、明细和历史抽屉均不再残留；
- 离线实例由未处理 500 改为可读 HTTP 422，且不产生历史记录；
- auditor 的统计按钮已禁用并提示“审计员仅可查看历史”，直接构造 POST 仍被后端 403 拒绝；
- 最小权限自定义角色仍可选择实例并成功统计，没有因权限整改误伤业务角色；
- 集中式指定库返回 2 张 BASE TABLE、排除 1 个 VIEW，与独立 SQL 一致；全量自动化为 **1755 passed、0 failed、0 skipped**。

但真实浏览器继续向异步边界施压后，发现两个未闭合点：

1. **MAJOR：旧请求的迟到错误提示会串到新实例上下文。** 在离线实例发起统计后立即切换到集中式实例，页面当前实例已经是集中式，但约 2 秒后仍弹出旧离线实例的“实例连接失败”。结果数据没有串台，但错误反馈串台，用户会误判当前实例不可用。
2. **MINOR：实时结果范围的采集时间为空。** 页面实际显示“结果范围：实例 / 库 /”，API 成功响应没有 `created_at`；虽然历史表中的 `created_at` 非空，但实时页面没有拿到它，与 Q 整改报告承诺的“实例 / 库 / 采集时间”不一致。

外网没有真实 TDSQL Proxy，且唯一真实集群为内网生产环境。该环境事实不记为产品缺陷，也不要求为了本轮缺陷复现而冒险先上生产。正确流程是：先在外网关闭上述两项并完成第三轮复测，再在批准的生产变更窗口受控上线，由内网智能体立即完成真实分布式对账和 T20；验证失败则收回入口并回滚应用。真实分布式结论在内网验证完成前仍标记为“未验证”，不能写成“已通过”。

## 2. 基线、环境与证据口径

| 项目 | 实际值 |
|---|---|
| 代码分支/提交 | `main` / `4c7a737c93e0a069c63ad929e6af2a4f854dafe8` |
| 整改依据 | `UAT-FIX-v1.6.3.0-G14表类型统计第一轮UAT整改完成情况报告-ClaudeQ.md`，设计 Rev.P |
| 浏览器入口 | `http://127.0.0.1:18801/`，从登录、菜单、页签、输入、按钮、提示和历史抽屉实际点击 |
| 元数据库 | 全新隔离库 `tdsql_uat_o_g14_r2_1630` |
| 合成业务库 | A：2 BASE TABLE + 1 VIEW；B：1 BASE TABLE；EMPTY：0 表 |
| 实例样本 | 集中式可用；声明为分布式但实际为普通 MariaDB；端口 1 离线 |
| 角色 | developer、auditor、仅有“深度诊断-表类型统计”的最小权限自定义角色 |
| 凭据处理 | 仅使用本地合成凭据；口令、令牌不写入报告或截图说明 |
| 服务隔离 | 原有服务未操作；本轮从被测提交启动独立 18801 服务 |
| 浏览器控制台 | 0 条 error / warn |

证据目录：[v1.6.3.0-uat-o-r2](evidence/v1.6.3.0-uat-o-r2/README.md)。截图证明用户实际看到的页面状态；数值正确性用独立 SQL 对账；权限与错误状态码再用真实 HTTP 复核。三者不能互相替代。

## 3. 第一轮缺陷关闭复核

| 第一轮缺陷 | 第二轮复核结果 | 结论 |
|---|---|---|
| O-G14-01：旧结果跨查询/实例/用户残留 | 改库立即清空；不存在库、系统库、离线错误后保持空态；切实例立即清空；换用户不继承实例、输入、结果和抽屉 | **数据展示主体已关闭；迟到错误提示仍有缺口，转 UAT2-O-G14-01** |
| O-G14-02：版本未提升 | 浏览器 title 与 `/health` 均为 1.6.3.0；版本一致性自动化通过 | **关闭** |
| O-G14-03：auditor 假写入口/错误文案 | 按钮禁用；悬停提示“审计员仅可查看历史”；历史可读；直调 POST 403 且文案可读 | **关闭** |
| O-G14-04：离线实例未处理 500 | HTTP 422，页面提示“实例连接失败……本次未产生统计结果”，loading 恢复，离线实例历史数为 0 | **关闭** |

## 4. 浏览器与接口测试结果

| 编号 | 人类用户操作 | 实际结果 | 判定 |
|---|---|---|---|
| UAT2-G14-01 | 打开登录页并检查版本 | title 为 `TDSQL数据库SQL审核工具 V1.6.3.0`；`/health` 为 1.6.3.0 | 通过 |
| UAT2-G14-02 | developer 进入表类型统计，不选实例 | 统计按钮禁用；点历史提示“请先选择实例” | 通过 |
| UAT2-G14-03 | 选择集中式样本，统计 A 库 | 1 库、总表 2、单表 2、广播 0、分片 0、逻辑基线 2 | 通过 |
| UAT2-G14-04 | 独立查询 A 库 `information_schema.TABLES` | BASE TABLE=2、VIEW=1；页面未统计 VIEW | 通过 |
| UAT2-G14-05 | A 库成功后，只把输入改为不存在库，不点击 | 旧摘要、明细、范围立即消失 | 通过 |
| UAT2-G14-06 | 点击统计不存在库 | HTTP 400；提示数据库不存在/不可见；页面保持空态 | 通过 |
| UAT2-G14-07 | A 库成功后切到声明分布式的普通 MariaDB | 切换瞬间旧结果消失；1064 被标记 `PROXY_CMD_FAILED`、`NOT_DISTRIBUTED_ENDPOINT`；失败库不进汇总 | 通过（异常路径） |
| UAT2-G14-08 | 切到端口 1 离线实例并统计 | HTTP 422；提示可执行；旧结果不恢复；按钮恢复可用；离线连接无历史记录 | 通过 |
| UAT2-G14-09 | developer 有结果后退出，最小权限用户登录 | 新用户初始实例、库名、结果、历史均为空；只显示授权菜单/页签 | 通过 |
| UAT2-G14-10 | 最小权限用户选择集中式样本并统计 A 库 | 正常得到 2/2/0/0 | 通过 |
| UAT2-G14-11 | auditor 登录、选择实例 | 统计按钮禁用并提示“审计员仅可查看历史” | 通过 |
| UAT2-G14-12 | auditor 打开历史并点击记录 | 可见时间、操作人、库名、汇总和逐库明细 | 通过 |
| UAT2-G14-13 | auditor 绕过页面直接 POST `/run` | HTTP 403，`当前角色(auditor)无权执行该操作` | 通过 |
| UAT2-G14-14 | 统计空业务库 | 1 库/0 表，逐库状态 OK | 通过 |
| UAT2-G14-15 | 统计系统库 `mysql` | HTTP 400；提示“不允许统计系统库”；页面保持空态 | 通过 |
| UAT2-G14-16 | 离线统计请求尚未返回时立即切换到集中式实例 | 当前已显示集中式实例，但旧请求错误提示随后弹出 | **失败：UAT2-O-G14-01** |
| UAT2-G14-17 | 查看集中式成功结果的范围行 | 显示“实例 / 库 /”，采集时间为空；API 无 `created_at` | **失败：UAT2-O-G14-02** |
| UAT2-G14-18 | 抽样打开即时审核、扫描任务、上线检查、实例管理 | 页面主体、输入和表格正常渲染 | 通过（未改模块简验） |

关键截图：

- [集中式指定库结果](evidence/v1.6.3.0-uat-o-r2/02-central-specific-scope.png)
- [改库立即清空旧结果](evidence/v1.6.3.0-uat-o-r2/03-input-change-clears.png)
- [离线可读错误且无旧结果](evidence/v1.6.3.0-uat-o-r2/07-offline-readable-clean.png)
- [跨用户初始状态为空](evidence/v1.6.3.0-uat-o-r2/08-cross-user-clean.png)
- [auditor 禁用提示](evidence/v1.6.3.0-uat-o-r2/09-auditor-disabled.png)
- [历史操作人及逐库明细](evidence/v1.6.3.0-uat-o-r2/10-history-detail.png)
- [切换实例后的迟到错误提示](evidence/v1.6.3.0-uat-o-r2/11-stale-error-after-instance-switch.png)

## 5. 自动化回归

### 5.1 G14、版本、权限、路由专项

```text
python -m pytest tests/test_table_type_stats.py \
  tests/test_g14_frontend_state_binding.py \
  tests/test_version_consistency.py \
  tests/test_rbac_path_coverage.py \
  tests/test_design_appendix_matches_repo.py \
  tests/test_app_routes_integrity.py -q
=> 139 passed, 0 failed
```

### 5.2 未改模块抽样

```text
python -m pytest tests/test_rules.py tests/test_sit_rules.py \
  tests/test_uat_frontend.py tests/test_v2_rbac_matrix.py \
  tests/test_v3_rbac_instances.py -q
=> 80 passed, 0 failed

python -m pytest tests/test_no_hardcoded_secrets.py -q
=> 2 passed, 0 failed
```

### 5.3 全量

```text
SQLCHECK_DB_NAME=tdsql_sqlcheck_test python -m pytest tests -q
=> 1755 passed, 0 failed, 0 skipped, 11 warnings, 305.80s
```

执行偏差说明：全量首跑误用了自定义库名 `tdsql_sqlcheck_test_uat2_full`，G14 集成测试的破坏性目标保护因此在任何 DROP 前拒绝执行，并引发后续历史用例的环境连锁失败。该次结果不计入产品判定；确认无生产目标、无数据删除后，按仓库规定的固定测试库 `tdsql_sqlcheck_test` 重跑，得到上面的 1755 全绿。此过程同时验证了防误删护栏真实生效。

自动化全绿与本报告的两个浏览器缺陷不矛盾：现有静态门禁只检查序号和清理代码存在，未覆盖“旧请求错误提示也必须静默作废”；后端用例也未要求实时成功响应携带 `created_at`。

收尾阶段的工作区边界说明：完成上述测试后，另一个并发工作活动在 `deploy/tdsql-dev-cluster` 下产生了与本轮无关的已修改文件及未跟踪 Proxy/TXSQL 厂商文件。它们不属于被测提交，也未纳入本报告提交。此后再次运行敏感信息检查时，会命中未跟踪厂商文档中的示例口令字面量；这不推翻干净被测提交上的 `2 passed`，但这些厂商文件未来若要正式入库，必须由其负责人先完成来源、许可证、体积和敏感信息专项审查。

## 6. 正式缺陷与照图施工方案

### 6.1 UAT2-O-G14-01（MAJOR）：迟到错误提示串到新实例上下文

#### 稳定复现

1. developer 进入“深度诊断 → 表类型统计”。
2. 选择 `G14 UAT2 离线样本 (127.0.0.1:1)`，点击“统计表类型”。
3. 请求未结束时立即把实例切换为 `G14 UAT2 集中式样本 (127.0.0.1:13306)`。
4. 顶部当前实例已是集中式，旧结果也正确为空；约 2 秒后仍弹出“实例连接失败……”。见 [证据](evidence/v1.6.3.0-uat-o-r2/11-stale-error-after-instance-switch.png)。

#### 根因

- `frontend/static/js/app.js` 的 `runTableTypeStats()` 只在 `_deepPost()` 返回后检查 `mySeq` 和 scope。
- 失败提示在通用 `_deepPost()` 内部、序号检查之前已经调用 `ElementPlus.ElMessage.error(...)`，所以数据响应能丢弃，错误副作用却无法撤回。
- `deepLoading` 是所有深度诊断共用的字符串；切换实例虽递增 `tabletypeSeq`，却不会立即释放属于已作废请求的 G14 loading，也无法区分“旧请求 finally”和“新请求 loading”的所有权。

#### 必须按以下结构修改

1. 在 G14 状态区新增独立 `tabletypeLoading = ref(false)`，页面按钮改用 `:loading="tabletypeLoading"`。不要改造其他深度诊断页签的 `_deepPost`，缩小回归面。
2. 新增纯判定函数 `isTableTypeRequestCurrent(mySeq, scope)`，同时比较：
   - `mySeq === tabletypeSeq.value`
   - 当前登录用户名
   - 当前 `deepConnId`
   - 规范化后的当前 `deepDb`
3. `runTableTypeStats()` 不再委托通用 `_deepPost()`显示错误。它应自己完成该接口的 `apiFetch`、JSON 解析和错误提示，执行顺序固定为：

```text
++sequence → 保存 scope → 清空旧结果 → tabletypeLoading=true → 发请求
响应/异常回来 → 先 isCurrent → 过期则直接 return，不显示任何成功/失败提示
仍为当前请求 → 再显示业务错误或写入结果并显示完成提示
finally → 只有本请求仍拥有 loading 时才关闭 loading
```

4. `watch([deepConnId, deepDb], ...)` 和 `clearRoleScopedState()` 在递增序号、清空 G14 状态的同时，将 `tabletypeLoading=false`。这样用户切换后不会被旧请求继续锁住按钮。
5. 防止旧请求 finally 误关新请求 loading：新请求开始会得到更大的序号；finally 只能在 `mySeq === tabletypeSeq.value` 时写 `tabletypeLoading=false`，不得无条件清理。
6. 把 `tabletypeLoading` 暴露给模板；保持 auditor 的 `canRunTableTypeStats` 与后端 403 不变。

#### 必须新增测试

- 用可控 Promise 发起 A 请求，切到 B 后让 A 返回 422：B 上下文无错误 toast、无结果、loading 已释放。
- A 未返回时切到 B 并发起 B 请求：A 的 finally 不得关闭 B 的 loading；只有 B 完成才关闭。
- A 迟到成功：不得出现 A 的成功 toast 或数据。
- 不切 scope 的普通 400/422/500：仍显示服务端可读消息，不能因抑制迟到提示而把当前错误也吞掉。
- 真实浏览器复跑本节 4 步，并保留“当前集中式实例、无旧离线错误”的截图。

关闭标准：过期请求的**数据、成功提示、警告提示、错误提示、loading 副作用**全部作废；当前请求的反馈不受影响。

### 6.2 UAT2-O-G14-02（MINOR）：实时结果范围缺少采集时间

#### 复现与根因

成功统计 A 库后，页面显示：

```text
结果范围：G14 UAT2 集中式样本（127.0.0.1:13306） / tdsql_uat_g14_r2_a /
```

接口复核显示 `created_at_present=false`，而元数据库 `table_type_stat.created_at IS NULL` 的行数为 0。即时间已经正确落库，但 `backend/services/table_type_stats_service.py::run_stats()` 返回的 `res` 只补了 `stat_id`，没有把本次落库时间带给前端；`tabletypeScopeText` 又直接格式化 `r.created_at || ''`，最终留下空白尾段。

#### 必须按以下结构修改

1. 在 `run_stats()` 完成采集后生成一次 `captured_at`，精确到秒；INSERT 时显式写入同一个 `captured_at`，并把同一个值写入 `res["created_at"]`。响应与历史必须同源，禁止前端另取本机时间冒充服务端采集时间。
2. 建议实现形式：

```python
captured_at = datetime.now().replace(microsecond=0)
# INSERT 列表增加 created_at，参数使用 captured_at
res["stat_id"] = stat_id
res["created_at"] = captured_at.isoformat(sep=" ")
```

3. 前端保留防御性显示：有值时 `formatTime(r.created_at)`；异常缺失时显示明确的“采集时间不可用”，不得生成空白 `/`。
4. 历史接口及现有数据库默认值保留，旧记录不迁移、不回填；本次只是让新 `/run` 响应与新历史行一致。

#### 必须新增测试

- `/run` 200 响应包含非空、可解析的 `created_at`。
- 用 `stat_id` 查询历史行，断言响应时间与落库时间精确到秒一致。
- 前端范围文本包含实例、库和格式化时间；构造缺失时间时显示“采集时间不可用”，不出现空白尾段。
- 空库、部分失败和全部失败的 200 响应也要携带时间，避免只修全成功分支。

关闭标准：实时页面显示“实例 / 库（或全部业务库）/ 采集时间”，且该时间与 `stat_id` 对应历史记录一致。

## 7. 无真实 TDSQL 环境下的准入与生产后验证

用户已明确：外网没有可用的真实 TDSQL 分布式集群；腾讯云测试集群欠费停机；内网只有生产集群。因此本轮按以下边界裁决：

- 外网能验的逻辑、权限、错误语义、集中式口径、状态隔离和全量回归必须在上生产前全绿；本报告两个缺陷均可在外网关闭，不能借环境限制延期。
- 真实 Proxy 三类表成功统计和 T20 无法在外网伪造，不记成代码失败，也绝不记成已通过。
- 两项代码缺陷关闭并经第三轮复测后，才可申请受控生产上线；最终 UAT 签字要等内网智能体完成以下只读验证。

### 7.1 受控上线顺序

1. 在批准的变更窗口备份元数据库并检查 v13 表结构；先部署单节点/单入口。若权限配置允许，G14 初始只开放给内网验证账号和 DBA。
2. 校验启动日志、`/health` 和页面 title 均为 1.6.3.0；先打开未改模块做最小冒烟。
3. 选择一个低风险、规模较小的业务库，同一时间窗执行页面统计与原厂只读命令：

```sql
/*proxy*/show table without shardkey;
/*proxy*/show table with noshardkey_allset;
/*proxy*/show table with shardkey;

SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE
FROM information_schema.TABLES
WHERE TABLE_SCHEMA IN ('目标库');
```

4. 按“库名+表名”集合对账：单表、广播表、分片表分别对应三条 Proxy 结果；总表按三类并集去重；逻辑基线只取 BASE TABLE 并按设计规则剔除经三条件确认的物理子表。任何差异必须保留原始行集，不得只报汇总数字。
5. 小库通过后再测业务库数量最多的实例。T20 对同一目标库比较普通 `IN` 与 `BINARY IN`：各预热 1 次、实跑至少 5 次，记录 EXPLAIN 的扫描库数、每次耗时、中位数、返回行数和集合差异。
6. 普通 `IN` 的扫描库数或中位耗时不得劣于 `BINARY IN`；若劣化，按设计阻断继续放量并分析优化器行为，禁止无证据直接恢复 `BINARY`/`CAST`。

### 7.2 停止与回滚条件

出现任一项即停止验证和放量：四个主数字与原始集合不一致；系统库混入；成功库被标 FAILED/SKIPPED；HTTP 500；元数据查询造成明显负载或长时间不返回；未改模块异常；T20 触发设计中的劣化判据。

回滚动作固定为：先撤销普通用户的 `deep-diag-tabletype` 入口或停止新请求，再把应用回滚到上一发布镜像/提交；v13 为新增留档表，旧应用不引用时可原地保留，紧急回滚禁止在生产 DROP 表。原始响应、三条 Proxy 输出、`information_schema` 行集、EXPLAIN、耗时、request id 和时间戳全部归档后再分析。

## 8. 第三轮复测准入条件

1. UAT2-O-G14-01 MAJOR 按请求所有权方案修复，迟到请求不再产生任何可见副作用。
2. UAT2-O-G14-02 MINOR 补齐服务端同源采集时间，实时结果与历史一致。
3. 新增自动化能在删掉关键检查时失败，不接受只加字符串存在性断言。
4. G14 专项、80 条未改模块抽样、敏感信息检查和 `pytest tests -q` 全量均为 0 failed。
5. Q 的整改报告和设计修订记录如实写明外网环境边界；不得宣称真实 TDSQL 成功路径或 T20 已通过。

最终判定：**第二轮 UAT 不通过，暂不准出；关闭 1 个 MAJOR 和 1 个 MINOR 后进入第三轮。真实 TDSQL 分布式与 T20 转为受控生产上线后的内网最终验收项。**
