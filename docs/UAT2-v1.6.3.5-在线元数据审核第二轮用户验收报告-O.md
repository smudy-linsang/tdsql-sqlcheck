# v1.6.3.5 在线元数据审核第二轮 UAT 报告（O）

## 1. 结论

**不通过，退回 Q 整改。共 6 项：3 项 P1、3 项 P2。**

本轮实际使用浏览器登录、选择实例、点击审核、查看完成结果、翻页、下载、刷新及再次扫描；不是仅调用接口或复述上一轮测试结论。基础提取、流式审核、产物下载能够运行，但 runner 停止后仍受理、再次扫描返回旧报告、刷新后活动任务丢失等用户流程不满足设计。

| 项目 | 本轮记录 |
|---|---|
| 委托方 | Mr.Linsang |
| 测试方 | O |
| 日期 | 2026-09-09～2026-09-10（限额中断后继续，未重新认定已完成用例） |
| 被测代码 | `main@701957b7da39c8b5f978d8748389f51155012de7` |
| 直接整改提交 | `701957b`：UAT-M1/M2/M3、A 的 R3-01 |
| 前置阅读 | Rev.B 详细设计；Q 开发记录；A SIT/SIT2/CHECK3；M 第一轮 UAT；DU-1/DU-2 及整改代码差异 |
| 浏览器环境 | Codex 内置 Chromium 浏览器，`http://127.0.0.1:8005/`，真实 UI 输入和点击 |
| 服务环境 | Windows / Python 3.14；新建隔离 Web＋runner，鉴权开启、专用测试账号 |
| 元数据库及目标 | 本机 `127.0.0.1:13306`，**实查 `SELECT VERSION()` = MySQL 8.0.45**；不是内网 TDSQL，也不沿用旧报告的 MariaDB 标签 |
| 隔离范围 | `uat_o_1635_r2_meta`；目标 `uat_o_1635_r2_target`；回归库 `uat_o_1635_r2_tests` / `uat_o_1635_r2_regression` |
| 合成数据 | 初始 63 张两列表；再次扫描验证时新增第 64 张专用测试表，无业务数据 |
| 代码变更边界 | **未修改 backend、frontend、deploy、生产配置、迁移或正式测试代码。**仅新增本报告及 `docs/evidence/v1.6.3.5-uat2-o/` 测试工具与证据 |

不得将本机 MySQL 合成目标、模拟异常、静态脚本断言，写成真实 TDSQL 大库容量或 Linux/systemd 发布通过。SUCCEEDED 表示执行和保存完成，不表示 SQL 无违规。

## 2. 上轮整改的关闭判定

| 上轮项 | 本轮证据 | 结论 / 本轮编号 |
|---|---|---|
| UAT-M1：runner 缺失 | 首次未启动且 accepting=0 时，浏览器立即提示未就绪，HTTP 503；但启动过后停止，过期真实心跳仍通过准入，HTTP 202、WAITING | **未关闭**，R2-01。不能仅归为 dev 启动方式问题 |
| UAT-M2：progress | None / 空串等常规响应确实返回 dict；但 63 条完成后 audited=51、total=null，已用时长为空 | **字段类型修复点通过，业务显示未闭环**，R2-02 |
| UAT-M3：中文诊断 | 真实本机端口拒绝连接 2003 已有中文建议；1049 数据库不存在在持久任务中也有中文；SHOW CREATE 的 2013 被另一异常分支绕过 | **部分通过**，R2-03 |
| A R3-01：部署契约 | 删除 install/apply_patch 的 runner restart 可被抓住；删除 upgrade 的 restart、把三份 restart 注释掉仍全绿 | **部分通过，不能关闭**，R2-04 |

另在本版修改的在线审核页面实际使用时发现 R2-05、R2-06。它们属于新任务流程的正常使用，不是重开无关模块需求。

## 3. 可复核执行证据

### 3.1 浏览器与实际后台执行

| 用例 | 操作 / 观察 | 结果 |
|---|---|---|
| B01 | 未起专用 runner，选择本机实例并点击审核；503，中文“元数据执行服务未就绪” | 通过该子场景，无新增任务 |
| B02 | 起 runner，再从浏览器点击审核 | `79e476ed4e1c43bbadae9736b3b0d919`，63 条，report=1、snapshot=1、exit=0、cleanup=1；SUCCEEDED。**进度不通过** |
| B03 | 审核结果点下一页 | 第二页为 51～63，共 13 条；接口 total=63、next_offset=null |
| B04 | 点击“下载 .sql 文件”“导出 HTML 报告” | 页面提示下载成功；同产物鉴权 GET=200；SQL 18,804 字节、HTML 59,373 字节，均含冻结实例名称 |
| B05 | B02 完成后相同条件再点 | 同一个 job，未增加任务。后续 B08 用新增表排除“只是正常幂等重试”的解释 |
| B06 | 确认空闲后只停止本轮 runner PID 59920，超过心跳窗口后新条件提交 | 仍 202；`a939f06c371b4c79b25ca4d3adc3e13f` 停在 ACCEPTED/WAITING，后台未自行恢复 |
| B07 | B06 等待时刷新浏览器，再进入在线审核 | 活动任务卡消失，不能直接查询/取消。随后 O 重新启动专用 runner，原任务才执行成功；不是自动恢复通过 |
| B08 | 新浏览器会话先扫描 63 表成功，再增加一张测试表，相同条件再点 | 目标实有 64 表；仍返回 `c1613af22164446e8d8e541b69171085` / report=3 / 63 条；前后任务总数均为 5 |
| B09 | 选择本机未监听端口 13307 并点击审核 | `278d3fb5fc6f4df7924625e8fa726e8f` FAILED / WORKER_ERROR / exit=1 / cleanup=1，无 report；页面有“无法连接目标数据库”建议 |
| B10 | 选择本机不存在库并提交 | `beb603c103cc41afa3253ecd20da4344` FAILED，无 report；持久结果有 1049 中文建议。**终态发生在限额暂停期间，仅按后台/API证据认定，不冒充已看到浏览器终态** |
| B11 | 即时审核输入 `SELECT * FROM t_uat WHERE id=1 LIMIT 2001;` 并执行 | HTTP 200，页面展示 R012 / ERROR，未执行目标 SQL |
| B12 | 打开治理概览、慢 SQL 扫描任务、上线检查、大表治理、深度诊断、网关日志页 | 页面/空清单可用，无新扫描、无日志上传；仅页面冒烟，不代表这些模块全部功能验收 |

完整任务摘要及产物哈希见 [contract-probes-final.json](evidence/v1.6.3.5-uat2-o/contract-probes-final.json)。浏览器提交对应的服务访问记录及自动化结果见 [verification-summary.json](evidence/v1.6.3.5-uat2-o/verification-summary.json)。

### 3.2 自动化与环境前置

- 本版 7 个 `test_v1635_*` 文件＋`test_verify_deploy_contract.py`：**87 passed**。包括 R035 精简索引/流式语义、任务状态/幂等、产物、护栏及部署契约；不等于本报告追加的反例通过。
- 全量第一次：1550 passed、417 failed、114 errors、31 skipped。新隔离库缺 bootstrap admin，部分 fixture 初始化失败导致鉴权状态没有正常收尾；另有自定义库破坏性测试白名单拒绝。**这次环境未准备齐，不能把这些数字当新增产品缺陷**。
- 准备专用 admin 与专用 G14 测试库白名单后：2080 passed、2 failed、30 skipped。剩余两项均在 `test_fix_user_issues.py`，依赖库里已有一条慢 SQL；空库 `SELECT id FROM slow_queries LIMIT 1` 为 None。
- 补充一条仅用于本轮回归的合成慢 SQL 后，全量最终结果：**2082 passed、30 skipped、0 failed、0 errors，耗时497.74秒**。没有调整断言、删除测试或放宽产品规则来消除失败。30个skip逐项留在证据摘要，不算通过。
- 最终运行有12条warning（含既有Pydantic/pytest兼容提示与Windows子进程GBK解码线程警告）；保留披露，不称“零告警”，也不据此认定Linux生产发布行为已验证。
- `probe_contracts.py` 是**证据探针**：记录实际结果，不是“全部断言通过”的测试套件。它显示心跳反例、异常分支漏接及变异漏检；退出码 0 仅代表取证脚本正常结束。

## 4. 缺陷与照图施工级整改要求

### UAT-O-1635-R2-01 / P1：runner 过期心跳解析失败被放行

**复现与影响**

1. 使用本轮隔离站点完成一次审核，确认 slot 空闲，停止本轮 runner（未停止原有 8003 服务或其 runner）。
2. 最后心跳为 UTC `2026-09-09T13:58:26.763733`；新任务受理时间 `13:58:42.382027`，已相差约 **15.6 秒**，超过设计 10 秒窗口。
3. 请求仍返回 202，写入 a939f06c… 并占用唯一槽；直到本轮手动重启 runner 后才继续。见 [页面证据](evidence/v1.6.3.5-uat2-o/03-runner-stopped-still-accepted.png)。

**根因定位（701957b 行号）**

- `backend/services/database.py:89` 将数据库 datetime 转为 `v.isoformat()`，形如 `YYYY-MM-DDTHH:MM:SS.ffffff`。
- `backend/api/metadata_audit.py:85` 的 `_check_runner_ready()` 按含空格的 `%Y-%m-%d %H:%M:%S.%f` 解析；失败进入 `except Exception: pass`。`hb=None` 也直接跳过。
- 新增 `backend/main.py:128` 启动日志采用类似解析方式，只提示 WAITING，既不能修复准入，也会把真实新鲜 ISO 心跳误判为缺失。
- 探针：过期 datetime 对象能返回 503，但同一时间的真实 ISO 字符串、空心跳、坏字符串均 `ACCEPTS`。现有 M1 用例只测 accepting=0，未覆盖真正出错的分支。

**Q 修改要求**

1. 在元数据模块内增加共享 UTC 时间转换函数，支持 DB 返回的 datetime 与标准 ISO datetime（含 `T`/空格、可选小数、显式时区）；无时区按本模块 UTC 契约解释。不要全局修改数据库兼容层而影响其他模块。
2. 准入：心跳缺失、解析失败、过期、明显未来时间均失败关闭为 `503 / EXECUTOR_UNAVAILABLE`，不得 `pass`；使用同一个 10 秒窗口。事务内占槽前再次验证新鲜度，失败不创建 job、不占槽、不连接目标。
3. 启动提示复用同一判定，缺失时说明“新任务拒绝受理，需启动执行器”，不要再承诺“任务停 WAITING”。
4. **联动修复**：`MetadataRunner._run_job()` 阻塞在 `run_metadata_worker()`，`run_forever()` 心跳目前只在任务前后更新。不能只改解析后让长任务期间全部被误判离线。用监督循环中的有界回调每 2 秒更新 runner 心跳，失败按原资源/恢复协议处理，不新开无监督的重计算路径。
5. 对已有 WAITING 任务按设计 30 秒启动期限和认领身份做恢复判定；能证明从未派生 child 的过期任务明确失败并释放槽。不能把“过期”当成杀任意 PID 或抢占其他 runner 的授权。

**必须补的回归锁**

实际 MySQL 读回时间格式；新鲜/过期 datetime 和 ISO；空/非法/未来心跳；accepting=0；心跳 10 秒边界；真实起停 runner 的 UI 503＋任务数/slot 不变；合法任务运行 >10 秒期间心跳仍刷新、同 key 可恢复、新请求不能误受理。

### UAT-O-1635-R2-02 / P2：终态计数停在 51，耗时为空

**事实**：B02/B08 的页面同时显示 SUCCEEDED、结果共63条、进度“枚举63 / 提取63 / 审核51”；API `total_statements=null`、`elapsed_seconds=null`。见 [截图](evidence/v1.6.3.5-uat2-o/02-complete-63-but-progress-51.png)。完整结果确有63条，**不是认定漏审12条**；缺陷是最终进度不真实。

**根因**：`metadata_audit_worker.py:175` 仅在 `idx % 50 == 0` 写计数，因此写1、51、101…；循环结束和发布前未强制写最终语句总数。`metadata_audit.py:176/182` 的耗时解析又踩中 R2-01 同一个 ISO 格式问题。M2 的“progress 是 dict”用例不能证明最终计数正确。

**Q 修改要求**

1. 流式循环维护真实 `audited_count`，正常耗尽后、发布前强制写 `{enumerated_objects,selected_objects,extracted_objects,total_statements,audited_statements}`；`total_statements` 来自实际拆句/结果数量，不用表数冒充。
2. 按设计保持 selected=extracted、实际总语句数=已审核数；不满足则禁止成功发布。最终更新受 attempt_token＋合法状态约束，检查更新命中，禁止迟到进度覆盖终态。
3. 状态 API 复用共享 UTC 转换，已完成任务按 started_at/finished_at 算实际秒数，不把 created_at 至完成的排队时间冒充执行时间；运行中按当前 UTC 算。NULL 只用于确实未开始的任务。
4. 前端继续保留可选链；本次展示缺陷不能用把63硬改为51、清空进度或不显示耗时解决。

**验收**：1、49、50、51、63、100、101 条，以及表＋视图、单对象多语句；终态计数与 results/history/manifest 一致；真实 DB 时间回读后的 elapsed 非负有效。早期失败仍为 dict，不能伪造完成数。

### UAT-O-1635-R2-03 / P2：逐对象 DDL 异常未接中文诊断

**通过部分**：真实端口连接失败的2003已有可读建议；该用例截图见 [05](evidence/v1.6.3.5-uat2-o/05-connection-error-humanized.png)。

**未通过部分**：对真实 `_show_create()` 注入 PyMySQL 2013，它包装成 `MetadataExtractError(EXTRACT_OBJECT_FAILED, ...)`；运行 worker 的真实 catch 分支，最终仍仅为“对象 synthetic_db.t_demo（TABLE）DDL读取失败: (2013, 'Lost connection…')”，没有新增的网络/实例建议。

**定位**：`metadata_audit_pipeline.py:_show_create()` → `metadata_audit_worker.py:246` 直接 `repo.fail(... e.message)`；只有后面的通用 Exception 分支调用 `humanize_db_error()`。这是**单元级故障注入证据**，没有宣称在内网制造断网。

**Q 修改要求**

1. 将“目标库异常→用户诊断”的处理放在管道的连接/枚举/SHOW CREATE 边界统一转换；或者两种 worker 分支均调用同一函数，但保留 `EXTRACT_OBJECT_FAILED`、库/表/VIEW定位及原始错误码。
2. 优先从异常 `args[0]` / 原因链提取整数错误码，再兼容旧文本；不要对任意文本中的四位数字套“目标数据库故障”。元数据库落库失败仍保留 `REPORT_SAVE_FAILED` 等来源，不能被改写为目标实例错误。
3. 用户消息限长、转义/脱敏后展示；未知码保留诊断线索，不能吞掉错误或伪装成功。

**验收**：已列出的10种映射码（2013/2003/2002/2006/1045/1044/1049/1146/1213/1205）逐项验证；至少让2013/1146分别穿过原始异常和 MetadataExtractError 分支；未知码、无错误码、元数据库异常不得误分类。不得仅断言工具函数自身通过。

### UAT-O-1635-R2-04 / P2：部署断言仍会把未启动 runner 判为通过

本轮不改脚本文件，只在内存替换 `Path.read_text` 的返回值，然后执行 Q 的真实 `test_runner_starts_before_web()`：

| 变异 | install | upgrade_incremental | apply_patch |
|---|---|---|---|
| 删除 runner 的 systemctl restart 行 | 能发现 | **发现不了** | 能发现 |
| 将 runner 的 systemctl restart 行改为注释 | **发现不了** | **发现不了** | **发现不了** |

**根因**：`tests/test_v1635_deploy_contract.py:45` 仍对全文 find。upgrade 删除 systemd 启动后，会退而命中非 systemd 分支更早的 **pkill** 字符串 `backend.workers.metadata_runner`，把停止动作当启动。注释同样会命中。当前脚本是否能运行与断言是否有效是两个问题；本项不是声称现有三个启动行已经缺失。

**Q 修改要求**

1. 三份脚本的 systemd 分支必须各自断言可执行的完整 runner restart 命令先于 Web restart，不能用另一个分支的 nohup 或 pkill 代替。
2. upgrade 非 systemd 分支单独检查真实 `nohup ... -m backend.workers.metadata_runner` 启动动作及先后顺序，排除注释、echo/log、pkill。
3. 最小实现可按行剥离注释、限定完整动作及分支区间；更可靠的做法是用假 systemctl/nohup 命令记录调用序列，分别走两条分支。只做静态锁时报告仍须注明未执行真实 systemd。
4. 变异必须包括删除、注释、挪到 Web 后、只保留 unit安装/日志/pkill 四类，并确保所有坏变异变红、正常脚本变绿；不以本次仅两个变异被抓住宣称全闭环。

### UAT-O-1635-R2-05 / P1：相同条件再次扫描永远重放旧任务

**业务影响**：用户改完目标库后再次审核，看似执行成功，实际拿到上次结果；新表/新违规可能完全没有被检查。

**严格复现**

1. 2026-09-10 07:15:02（本地）首次浏览器提交 → HTTP202，任务 c1613af2…，63条，report=3。
2. 完成后，在本轮合成目标库新增 `t_uat_added_after_scan`，`information_schema.TABLES` 实数变64。
3. 07:15:44 不改表单条件再次点击 → HTTP200，仍 c1613af2…、report=3、结果63；总任务数仍5，无新提取。

前后 DB 证据：[before](evidence/v1.6.3.5-uat2-o/rescan-before-click.json)、[after](evidence/v1.6.3.5-uat2-o/rescan-after-click.json)；[页面](evidence/v1.6.3.5-uat2-o/06-rescan-reuses-stale-report.png)。这不是双击去重的预期行为，而是**前次已明确结束后的新扫描意图被吞掉**。

**定位**：`frontend/static/js/app.js:1718` `_metaJobKey()` 以“实例|库|范围”永久复用 sessionStorage 键；终态不轮换。因此 API 正确地执行了同幂等键重放，错误在前端没有区分“恢复原提交”和“新发起一次”。

**Q 修改要求**

1. 引入明确的 submission 生命周期记录 `{user, intent_id, normalized_input, key, job_id, submission_state}`。**每次确认的新扫描生成新 key**；仅同一次未决提交/响应丢失重试才复用。
2. 终态 SUCCEEDED/FAILED/CANCELLED 后，保留 job 查询信息，但将该提交标为 closed；用户再点创建新 submission。不能在请求刚发出就删 key，否则失去响应丢失去重能力。
3. 将“恢复上次任务”与“重新扫描”分为两个明确动作；登录用户隔离 key，登出清理该用户未决本地视图；不改变后端相同 key 相同参数幂等、不同参数409的语义。
4. 首次提交前规范化 scopes/默认库策略；前端参数变化后不允许旧响应覆盖新视图。结合 R2-06 统一设计，避免各写一套存储。

**验收**：已完成→目标DDL改变→同条件新扫必须不同job且包含新对象；失败修复后新扫、取消后新扫同理；双击只一个任务；响应丢失恢复同一任务；两用户/两标签页不得串任务。

### UAT-O-1635-R2-06 / P1：刷新后活动任务没有恢复入口

**复现**：B06 的 a939f06c… 仍 ACCEPTED/WAITING 时按浏览器刷新，再点击“SQL审核→在线元数据审核”，只剩空表单，任务卡和取消入口消失。后台任务还存在且占槽；见 [截图](evidence/v1.6.3.5-uat2-o/04-refresh-lost-active-job.png)。本轮随后手动恢复 runner 不构成页面自动恢复通过。

**定位**：`frontend/static/js/app.js:1740` 只写 `sessionStorage.meta_active_job`，全文没有读取恢复逻辑，也没有查询任务列表的 UI；刷新使 Vue 的 `metadataJob` 回到 null。

**Q 修改要求**

1. 在鉴权完成、进入在线审核页时读取当前用户的未决 submission/job_id，GET任务状态；找不到本地缓存时可查询服务端该用户任务列表，提供“恢复查看”入口。
2. 恢复 GET，**不得为了恢复直接新建 POST**。取得任务后显示其冻结实例名称/库/范围/阶段/进度，绑定取消和下载；已完成则分页加载结果。
3. 401/403停止轮询并等待重新登录；404说明任务不存在或无权；网络断开仅显示“状态待确认”，不可改后台FAILED或清除唯一恢复线索。其他用户只能看到自己或权限允许的任务。
4. 用页面/身份 generation＋请求序号保护每次异步提交，包括 JSON解析后、分页结果回写前；离页停止当前视图轮询但不取消后台任务，重入恢复单飞轮询。

**验收**：ACCEPTED/RUNNING/PUBLISHED时刷新；切页再回来；终态刷新；断网重连；令牌失效重新登录；用户切换；慢旧响应后到。均需真实浏览器证据，断网/延迟注入单独注明模拟。

## 5. 证据边界与准出门禁

1. 本轮证明了合成小库完整结果可保存，但**真实内网6000+表连续3次容量、默认1024MiB完整child RSS、默认1800秒下完成、阶段峰值与长于300秒任务的证据仍未补齐**。不修改 Mr.Linsang 已定的表类型统计180秒，也不拿它作为本模块任务预算。
2. Linux/systemd安装/增量升级/补丁/回滚的真实执行，以及 CORE_SAFE 制品往返、真实RSS超限、断电重入/回收门禁未由本轮实测覆盖。静态变异不能代替这些门禁。
3. 所有权/鉴权的自动化回归与本机测试账号浏览器登录不等于已完成真实环境权限验收。SQL/HTML接口核验是独立API层证据；浏览器层只陈述实际点过且观察到的动作。
4. 对 M 第一轮“均非产品缺陷/本机可验证项全通过”的归类，本轮不予直接沿用：R2-01、R2-02已有真实浏览器＋DB反证；未测项也不能因上一份报告写通过而转为已通过。
5. 本轮没有复现内网279秒故障，不把旧报告推断的 OOM/TCP RST/固定250秒写成此次实测因果；也不把限额暂停时间算成数据库执行耗时。

**准出要求**：Q 先完成本报告6项整改并补足定向锁；O 按对应反例复测，A 可复核新增自动化与变异有效性；G 在具备内网条件时组织真实容量和部署/回退门禁。6项未关闭前不得用自动化全绿替代 UAT 通过，未完成的容量/发布门禁须保持待验证。

## 6. 重复执行、交付与现场保护

- 工具：`local_fixture.py` 只作用本机专用库；setup 所需测试账号口令由 `UAT_FIXTURE_PASSWORD` 注入，不存入报告/脚本。`probe_contracts.py` 仅只读取证＋内存mock（旧接口410零业务副作用验证除外）。`prepare_regression.py` 补齐专用回归库前置样本。
- 真实数据库连接密码、浏览器JWT、完整带认证日志均不进入证据提交。归档为本轮合成数据摘要、截图、有限访问日志字段与测试结果。
- 原有8003服务及其runner未停止；原管理员账号、用户业务连接、业务库未修改。临时停止/恢复的均为 O 本轮创建且核对过命令行的专用runner。
- 测试数据库与合成数据保留供Q复现，不自动DROP，不删除已有报告。收尾已确认5个任务均终态（3成功、2失败，cleanup_ok均为1）、slot为空，停止本轮独立8005 Web（60988）和runner（36580）；8005不再监听。原8003 Web（37220）及runner（14564）仍在运行。
- `git diff --exit-code HEAD -- backend frontend deploy tests` 已通过，确认产品代码及正式测试均未被本轮修改。报告/证据作为独立文档提交交付；复现说明见证据目录README。

测试责任方：O

提交给：Mr.Linsang
