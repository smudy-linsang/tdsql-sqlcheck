# v1.6.4.0 AI Copilot 第一轮质检验收报告（O）

> 质检日期：2026-09-16—2026-09-17
> 被测基线：`c35c697e72ada135d79d1dcd46672563aebc447d`（功能实现主体 `b0a2194`）
> 设计基线：Rev.D＋N-10，冻结提交 `4fb1f2e462dd17c036ed69f18b2128128614fdbd`
> 验收角色：设计责任人 O；本报告不复用 A 的 SIT 或 D 的 UAT 结论代替独立判定

## 1. 质检结论

**第一轮质检不通过，当前版本不得作为 v1.6.4.0 生产发布候选。**

本轮发现 **6 项 BLOCKER、5 项 MAJOR、1 项 MINOR**。最关键的事实是：后端受控模型链路能够调用成功，原有主要功能的浏览器抽样也能正常工作；但人类用户无法仅靠 WebUI 完成模型配置，浏览器多轮对话无法稳定继续，业务页上下文没有送入助手，退出登录后私有草稿会泄露给下一个账号，实例授权撤销后旧回答/动作/导出仍可访问，候选 SQL 还会一键覆盖未保存草稿。

因此，Q/A/D 已完成的开发、SIT 和 UAT 放行结论可以作为已有证据，但不能覆盖本轮在人类浏览器路径上发现的新阻断项。

### 用户重点关切的直接回答

| 问题 | 本轮结论 | 证据边界 |
|---|---|---|
| 人类能否在 WebUI 顺利配置大模型 API | **不能** | AI 配置页只有列表、自检、启停；没有新增/编辑 provider、密钥、场景路由、实例授权申请/审批、运行设置表单。空环境无法靠页面完成首配；十条路由经 API 配好后，页面仍显示十行空白。见截图 04、05、06、15。 |
| 配置后能否通过 API 正确调用大模型 | **受控链路可以** | 通过管理 API 创建 provider、场景路由和双管理员合成授权后，HTTPS/TLS、Bearer、OpenAI-compatible 请求、结构化响应、三轮调用均成功；首轮浏览器模型回答也成功。该证据使用本机受控合成模型网关，证明调用机制，不证明真实批准模型的回答质量。 |
| 能否与 Copilot 一言一语交互咨询本项目 | **不能达到设计验收标准** | 后端按正确 revision 和每轮新请求 ID 可连续完成三轮；浏览器第二轮出现 `SESSION_CONFLICT`，新会话又出现 `IDEMPOTENCY_CONFLICT`。即使绕过页面，实际出站三轮的 `history` 均为 0，模型无法可靠理解“接着上一问”。 |

## 2. 测试方法与证据边界

本轮从真实人类用户视角使用 Codex 内置浏览器操作 `http://127.0.0.1:8026/`，包括登录、菜单、表单、上传、任务等待、报告查看、账号切换和模型配置页面。补充使用 HTTP API 检查不可由现有页面完成的管理配置、归属校验和撤权时序；使用 pytest、断网容器安装和源码静态核对定位原因。

环境全部为隔离合成资源：`qc_o_1640_meta` 元数据库、`qc_o_1640_dist`/`qc_o_1640_cent` 测试库、`q40-dist`/`q40-cent` 测试连接、两名管理员和开发/审计测试账号。本机受控 HTTPS 网关真实接收应用发出的 OpenAI-compatible HTTP 请求，但返回固定合成答案。普通 MySQL 8.0 仅用于原功能和边界验证，不等同真实 TDSQL Proxy/内核。

以下结论必须分开理解：

- 浏览器与 API 功能结论是本轮实测；
- 受控网关证明 TLS、鉴权、请求投影和响应解析链路，不证明真实模型质量；
- 普通 MySQL 合成表证明页面和 SQL 工具链，不证明真实 TDSQL 大规模容量；
- 两个测试管理员账号证明系统的申请/复核技术约束，不证明两名真实自然人的职责分离；
- 未执行真实批准模型 100 题黄金集、200 轮压力、真实内网 TDSQL 容量和全发布/回退演练，不能由 SIT/UAT 或 mock 结果替代。

原始证据位于 [`docs/evidence/v1.6.4.0-qc1-o/`](./evidence/v1.6.4.0-qc1-o/)；证据边界和文件映射见 [`EVIDENCE-INDEX.md`](./evidence/v1.6.4.0-qc1-o/EVIDENCE-INDEX.md)。

## 3. 已通过项目

### 3.1 原有功能浏览器回归

| 功能 | 结果 | 实测内容 |
|---|---|---|
| 即时 SQL 审核 | PASS | 合成 `CREATE TABLE` 返回 R003/R004/R005/R028/R029/R036/R037/R077 共 8 项；Copilot DB 开关关闭时仍正常。 |
| 文件审核 | PASS | 浏览器上传 `qc1-audit.sql`，2 条 SQL 均产生报告历史。 |
| 在线元数据审核 | PASS | 后台任务 SUCCEEDED，4/4 表枚举、提取、审核完成，耗时 3 秒。 |
| EXPLAIN | PASS | 对合成表只读查询返回执行计划。 |
| 大表治理 | PASS（小样本） | 合成库采集完成并诚实显示“未发现大表”。 |
| 集中式表类型统计 | PASS（合成 MySQL） | 在页面完成实例类型锁定后，返回总表 1、单表 1、广播/分片 0、状态 OK；锁定前对错误分布式口径明确报 `NOT_DISTRIBUTED_ENDPOINT`，没有把失败当 0。 |
| 网关日志分析 | PASS | 上传 2 行合成 interf 日志，得到总请求 2、慢请求 1、均值 756.35ms、最大 1500.2ms。 |
| 角色菜单 | PASS（抽样） | 管理员可见 AI 配置，开发员不可见系统管理；原业务菜单按角色显示。 |

本轮没有把共享 MySQL 上“上线检查”扫描出的 97,089 条结果当成四表样本结论，也没有把普通 MySQL 的结果写成 TDSQL 容量结论。

### 3.2 Copilot 后端与发布基础

- `tests/copilot`：**117 passed**；包含共享迁移器 N-10 正反锁。
- 管理 API 可创建 provider、配置十个场景路由、执行两管理员实例授权、自检、启用 provider。
- 受控 HTTPS 模型：浏览器首轮回答 SUCCEEDED；补充 API 三轮均 SUCCEEDED；实际请求记录均有鉴权、TLS 和结构化响应。
- 跨账号直接读取他人 session/turn：404，服务端对象所有权基本校验有效。
- 生产 `requirements.txt` 不含 Playwright；Linux Python 3.11、`--network none`、只使用 `dist/wheels_tmp` 的干净安装和 `pip check` 均通过。
- Copilot 关闭时原即时 SQL 审核仍正常，证明原功能未被总闸直接阻断。

## 4. 缺陷与施工级整改方案

### QC1-B01：账号切换后泄露上一账号的私有前端状态（BLOCKER）

**复现与影响**：管理员甲在问题框输入未提交私有草稿并退出；同一浏览器标签登录开发员后，页面仍显示管理员草稿、旧会话标题、旧预览和幂等错误。证据见 [`33-cross-account-private-state-leak.png`](./evidence/v1.6.4.0-qc1-o/shots/33-cross-account-private-state-leak.png)。服务端猜测他人对象仍为 404，故当前证据指向前端跨身份泄露，不扩写为服务端 IDOR。

**根因**：`copilot.js` 的组合状态在应用生命周期只创建一次；退出、401、重新登录没有调用身份重置。`subjectId` 依赖前端身份对象中并不稳定存在的 `subject_id`，存储键会退化为 `anon`；`_clearSubmission()` 没有调用点。现有 generation 检查也没有同时绑定 subject/session/turn/request sequence。

**施工方案**：

1. 在 Copilot 状态模块新增唯一入口 `resetForIdentityChange(oldSubject,newSubject,reason)`：先递增 generation/request sequence、abort 全部 fetch、停止 timer，再清空 question/draft/sourceRefs/preview/result/error/currentSession/turns/selectedConnection/submission journal。
2. 登录成功以后先通过 capabilities 取得服务端 `subject_id`，未取得前禁止加载任何 Copilot 私有缓存；禁止使用 `anon` 读写私有 submission。
3. `doLogout`、全局 401、用户切换和应用重新初始化都必须调用 reset；旧 subject 的 sessionStorage 记录按精确键删除。
4. 每一个 `await fetch`、`await response.json()` 和 polling 回调赋值前，比较 `{subject_id, session_id, turn_id, generation, request_seq}` 完整所有权令牌。

**复测门槛**：两个不同账号、同名删除重建账号、401、慢 body 迟到、poll 迟到、两个标签页各跑一次；新账号页面中旧草稿/预览/正文/名称/错误均为 0 字节，服务端继续 404。

### QC1-B02：实例授权撤销不影响历史结果、动作和导出（BLOCKER）

**复现与影响**：实例会话完成模型回答后撤销 `q40-dist` grant；随后 `GET result`、`POST actions/resolve`、`GET export.html` 仍全部 200，来源卡动作仍返回 `source:E1`。证据见 [`api-revocation.json`](./evidence/v1.6.4.0-qc1-o/api-revocation.json)。这违反冻结设计“撤销即时、历史/导出/候选动作重新鉴权”，会在授权取消后继续暴露已冻结业务资料。

**根因**：三个端点只调用 `require_turn_owner`，未根据 session 的 `connection_id` 和 source envelope 重新执行实例 grant、源菜单和源对象 ACL；导出也直接渲染已存 response。

**施工方案**：

1. 新增服务端统一函数 `authorize_turn_material(identity, turn, action)`，覆盖 RESULT、OPEN_SOURCE、COPY/EDITOR、EXPORT。
2. 对实例会话先验证当前 enabled grant；逐 source 复核源菜单、对象归属/存在性和源 ACL。任何一项撤销后，结果正文和来源 excerpt 均不得返回。
3. 结果端建议返回 403/404 的稳定闭集错误 `SOURCE_AUTH_REVOKED`；动作和导出使用相同函数，避免三个端点口径漂移。
4. 撤销事务提交后再响应；所有后续读取以当前 grant revision 为准，不因 turn 保存了旧 revision 而放行。

**复测门槛**：在预览后、出站前、模型返回前、终态后、导出前分别撤销；五个时点验证模型调用次数、结果、来源、动作、导出均符合 CP-TST-35/43，重新授权不能自动恢复已经按撤销关闭的旧资料读取。

### QC1-B03：WebUI 无法完成人类可用的大模型配置（BLOCKER）

**复现与影响**：全新环境 AI 配置页只有空表；模型页只支持自检/启停，场景路由和实例授权均只读，没有 provider 新增/编辑、密钥替换、路由配置、授权申请/审批/撤销、运行设置表单。管理员只能绕过页面调用 API，普通部署人员无法“在 WebUI 见面”完成配置。

**施工方案**：

1. 模型页新增“新增/编辑”抽屉，字段严格映射 `ProviderCreateRequest/ProviderUpdateRequest`：name、批准 `endpoint_id` 下拉、model_id、auth_mode、能力闭集、secret_action。secret 永不回显，编辑默认 KEEP。
2. 场景路由页逐场景提供主/备 provider、privacy profile 和 revision 冲突处理；显示 API 实际字段 `scene_code/provider_id/profile`，不要继续读取不存在的 `scene/primary_name/fallback_name/mode`。
3. 实例授权页提供申请、另一管理员审批、撤销和 revision；客户端不允许自填操作者主体，审批人不得是申请人或目标主体。
4. 增加运行设置、health、知识状态、反馈汇总页；所有保存后重新 GET 验证服务端最终状态。
5. 自检提交后按 turn_id 轮询到终态，刷新 tested_revision；页面离开时停止轮询，返回后可恢复。

**复测门槛**：从空 B 组开始，只用浏览器完成 provider→自检→启用→十场景路由→双管理员 grant→普通用户首轮模型回答；网络面板不得出现手工 API 或控制台注入，所有 secret 不回显、不进 URL/日志/截图。

### QC1-B04：业务页上下文桥未接入 Copilot 状态（BLOCKER）

**复现与影响**：即时审核已有 9 条违规时点“解释违规规则”，助手仍是 `USAGE_HELP`、问题空、来源空；选择 `RULE_EXPLAIN` 后报必须选择 rule。点击“生成修改建议”后草稿框仍空。类似地，文件审核、上线检查、大表、表类型按钮传空 source，无法解读用户当前看到的结果。

**根因**：`app.js:70` 的 `openCopilotWith` 只写 `copilotSelection` 并打开抽屉；`copilot.js` 预览使用自己的 `scene/sourceRefs/draftText`，没有从 `contextBridge.getCurrentSelection()` 导入。`pageKey` 也未同步，draft revision 读取层级不一致。

**施工方案**：定义一个 `applyBusinessContext(selection)` 原子入口，在打开抽屉前：校验 page_key 与 source_ref 判别联合、映射目标 scene、复制用户当时选中的 source IDs、复制 draft 和 revision、清旧 preview/submit journal，并按实例/库创建或选择新 scope session。每个首期入口做独立 adapter；没有结构化来源的入口明确进入本地帮助或 USER_DRAFT，不伪装读取了业务结果。

**复测门槛**：冻结设计列出的即时审核、文件、元数据、慢 SQL/EXPLAIN、四类对比、上线检查/大表、表类型、网关报告全部从真实按钮进入；逐项核对浏览器请求体、预览卡和服务端冻结 payload 中的 scene/page/source/draft/revision/instance/database 完全一致。

### QC1-B05：浏览器会话生命周期错误，模型请求又没有历史（BLOCKER）

**复现与影响**：首轮可成功；同一会话第二轮预览返回 `SESSION_CONFLICT`，新会话提交又返回 `IDEMPOTENCY_CONFLICT`。补充 API 在每轮读取新 revision、使用新 request ID 后三轮均 SUCCEEDED，说明后端基本状态机可用；但实际三个出站请求 `history` 长度均为 0，无法理解“接着解释”。

**根因**：前端创建后不刷新 session revision，选择会话只加载 turn 列表而不加载 session 详情；terminal 后没有刷新 revision。submission journal 按 subject/tab 存一份，`pending_client_request_id` 终态后不清除，`_clearSubmission` 未调用。`preview_service.py:238` 将 history 固定写死为空数组。

**施工方案**：

1. 每个“新意图”生成新的 intent_id/client_request_id；只有响应未知的同一意图可复用原键。终态、新建会话、切会话、修改问题/资料都清除旧 preview 和旧 pending ID。
2. 创建、选择、终态、恢复后统一 GET session 取得当前 revision；提交使用预览冻结的 expected revision，冲突后刷新而不是盲目重试。
3. 刷新页面时恢复 `{subject,session,intent,client_request_id,preview_id,turn_id}`，先查询已有 turn，再决定是否重放原键；最多一个 poll 在途。
4. 服务端按当前用户同 session 最近最多 6 条已校验问答构造 ≤4KiB history，排除失败/撤权/跨实例内容；每轮重建，不使用供应商保存会话。历史只作上下文，不能作事实证据。

**复测门槛**：浏览器连续 10 轮含“继续/它/上一条”指代；刷新发生在 preview、202 响应丢失、running、terminal 四个时点；验证无重复 turn/模型调用/额度、revision 不冲突、出站 history 从第二轮起非空且无跨账号内容。

### QC1-B06：候选 SQL 未执行 T09，且一键覆盖用户未保存草稿（BLOCKER）

**复现与影响**：受控模型返回 `SELECT * FROM qc_candidate_missing;`。结果对象和页面没有 `validation/executable/skipped_checks/semantic_equivalence`，页面却统一标“候选 SQL（仅文本复核）”。点击“送入审核编辑器”后，原未保存草稿立即被覆盖，没有 diff、确认或 revision 检查。证据见 [`34-editor-before-candidate.png`](./evidence/v1.6.4.0-qc1-o/shots/34-editor-before-candidate.png)、[`35-candidate-without-validation.png`](./evidence/v1.6.4.0-qc1-o/shots/35-candidate-without-validation.png) 和 [`36-editor-after-candidate.png`](./evidence/v1.6.4.0-qc1-o/shots/36-editor-after-candidate.png)。

**根因**：模型 schema 中 `SqlCandidate` 只有 sql/reason/evidence_ids；workflow 发布模型回答前未调用 T09；`sendToEditor` 直接调用 `previewReplacement`，而 `app.js:83` 的该函数直接赋值。已有 `applyDraftIfRevision` 未被该路径使用。

**施工方案**：

1. 模型答案校验后，对每个候选调用受控 T09；服务端附加闭集 `validation`、`executable`、`violations`、`skipped_checks`、`semantic_equivalence=NOT_PROVEN`。缺实例类型/必要条件、超时或解析不可靠必须 UNKNOWN/INCOMPLETE，不能冒充已复核。
2. 前端按四态显示，不再使用笼统“仅文本复核”；只有符合合同的候选才提供“比较到编辑器”，仍不得自动执行审核。
3. action resolve 返回候选时附带 frozen editor revision；页面展示旧/新 SQL diff 和风险说明。用户确认后调用 `applyDraftIfRevision(expected,text)`；revision 变化则拒绝覆盖并要求重比。
4. 原草稿在用户明确确认前保持原值；取消或关闭对话零改动。

**复测门槛**：覆盖占位符、缺 WHERE/DEFAULT/分区边界、未知方言、阻断违规、文本通过、T09 超时；对每态核对 executable。编辑器在比较前、中、确认后被人工修改，均不得静默覆盖。

### QC1-M01：浏览器导出始终 401（MAJOR）

`copilot.js:483` 用 `window.open('/api/.../export.html')` 打开新标签，没有 Authorization header；活跃登录用户看到 `401 未认证`。服务端 API 携带 token 时可返回 200，说明问题在浏览器下载方式。

**施工方案**：用 `apiFetch` 带 Bearer 获取 blob，验证状态和 `Content-Type` 后创建短生命周期 object URL 并触发下载，最后 revoke；不得把 token 放 URL，也不得放宽端点为匿名/cookie 隐式授权。覆盖过期 token、他人 turn、撤权、CSP 和 >1MiB 有界下载。

### QC1-M02：预览不是完整、可核对的冻结投影，修改后也不失效（MAJOR）

页面只显示问题、证据条数、知识条数；没有展示完整 history/evidence/knowledge、实例/库、别名、裁剪、路由/域和实际将出站的冻结数据。当前模板还读取错误的 `knowledge_count` 字段。预览后修改问题或业务选择，旧确认状态不会立即失效。

**施工方案**：以服务端 preview 响应为唯一来源，做只读投影查看器，逐项显示实际冻结 payload、来源名称/别名、字节数/裁剪、provider/数据域/隐私级别；隐藏 secret/system 内部模板。对 question/source/draft/scene/session/instance/database 任一变化同步清 preview、禁提交；submit 继续以 snapshot_hash 校验。

### QC1-M03：FAILED 结果弹窗为空，用户不知道失败原因和下一步（MAJOR）

首轮无有效本地资料时服务端返回 FAILED/EVIDENCE_UNAVAILABLE，页面结果弹窗仅有“来源/结论摘要/来源”空壳，没有 error_code、error_message、是否调用模型和安全重试指引。

**施工方案**：终态组件分别渲染 SUCCEEDED/LOCAL_ONLY/DEGRADED/FAILED/CANCELLED/INTERRUPTED；失败态显示闭集原因、人类说明、是否发生出站、request/turn 追踪号和允许的下一步。不得用空摘要或成功色。补网络失败、知识缺失、来源撤回、输出不合格、超时、取消回归。

### QC1-M04：自检终态和路由 DTO 未刷新（MAJOR）

页面点击自检后只弹“已受理”，后台 turn 已 SUCCEEDED、tested_revision 已写入，但页面一直“未通过/配置已变更”，直到离开重进。十条场景路由经 API 建立后页面出现十行空白，因为列读取不存在的字段。

**施工方案**：自检按 turn_id 恢复轮询并在终态刷新 provider；错误态显示 provider error。增加 DTO adapter 或直接按 API schema 绑定 `scene_code/primary_provider_id/fallback_provider_id/privacy_profile/revision`，provider 名称通过同次 provider map 显示。每次保存按 revision 刷新并处理 409。

### QC1-M05：关闭 Copilot 时页面没有可用的本地帮助路径（MAJOR）

数据库设置关闭后，页面显示“未启用”，但仍只提供 session→preview→submit；没有调用设计中独立于 B 组和模型的 `GET /help`。本轮直接调用 `/help` 为 200，原 SQL 审核也正常，说明服务端能力存在而页面未接线。

**施工方案**：在 DISABLED/LOCAL_HELP/UNAVAILABLE 模式显示本地帮助搜索框和常见问题，调用 `/help?query&page_key`，不创建 session、不要求 grant、不提交模型；清晰说明启用条件。遍历所有可见菜单测试模型 HTTP=0、原功能正常、帮助可追溯。

### QC1-m01：健康端点和知识原因码与冻结合同漂移（MINOR）

实现中的 `/copilot-admin/health` 在 B 组异常时返回 200＋`ready=false`，而 Rev.D 表格写 503；实现新增 `KNOWLEDGE_BUNDLE_AMBIGUOUS`，未进入设计原因码闭集。两项本身有合理性，但未经基线变更会造成运维和测试口径分裂。

**处置**：保持冻结合同的可观测性目标：health 在结构不可用时返回 HTTP 503，同时保留完整诊断 JSON；把 `KNOWLEDGE_BUNDLE_AMBIGUOUS` 正式加入下一版受控设计枚举、错误文案和运维手册，明确多个候选包时失败关闭。代码、设计、测试三者在同一整改提交中统一。

## 5. 自动化回归与基线对照

### 5.1 已完成

| 门禁 | 结果 |
|---|---|
| Copilot focused | 117 passed / 0 failed / 0 error |
| 离线生产依赖安装 | PASS，Python 3.11 容器、无网络、`pip check` 无破损依赖 |
| 当前干净 HEAD 密钥守卫 | 2 passed |
| 干净 HEAD 原测试套件（排除 `tests/copilot`） | 2176 collected；1639 passed、417 failed、89 errors、31 skipped |
| 冻结基线原测试套件 | 2176 collected；1639 passed、417 failed、89 errors、31 skipped |
| 前后失败集合 | 506 个 unsuccessful case ID 完全相同；HEAD-only=0，baseline-only=0 |

第一次当前树对照出现 1 个 HEAD-only 失败，是测试扫描了工作区既有 ignored `scratch/verify_copilot_r2.py`，不是 HEAD 内容；从干净 HEAD 导出树重跑后，原功能测试的 506 个 unsuccessful case ID 与冻结基线完全一致，密钥守卫为 2 passed。本轮没有从这组测试发现新增失败集合，但两侧共同的 417 failed＋89 errors 仍是未清零的历史/环境门禁，不能写成“全量回归通过”。

### 5.2 83 项设计矩阵覆盖判定

| 组 | 本轮状态 |
|---|---|
| CP-TST-01—11 基础/上下文/SQL候选 | 原功能、模型首轮部分通过；01、03、04、09、10、11 在真实页面失败，阻断 |
| CP-TST-20—30 会话/幂等/并发恢复 | 后端基本 API 与所有权部分通过；浏览器 revision、intent key、恢复和多轮失败，20—27 不能放行 |
| CP-TST-32—43 权限/撤销/安全 | 角色菜单和跨账号服务端 404 通过；前端跨账号状态、撤权后 result/action/export 失败，35、43 阻断 |
| CP-TST-44—52 provider/降级/导出 | 受控 provider 调用通过；Web 配置、自检刷新、禁用帮助、导出失败 |
| CP-TST-53—57 原功能/安装/回退 | 浏览器抽样和离线安装通过；完整发布五路径、真实回退未执行 |
| CP-TST-58 真实模型黄金集 | 未执行；受控合成网关不能替代 |
| CP-TST-59—70 身份/密钥/标识符/反馈 | 自动化覆盖一部分；真实轮换、三主体反馈、完整并发时序未执行 |
| CP-TST-71—83 B 组故障/恢复/N-10 | focused 自动化及 N-10 通过；多 Web＋runner、真实备份恢复和全部故障时序未由本轮浏览器重演 |

现有 117 项 focused 测试证明服务层若干合同，不等于 83 个设计场景全部验收；本轮多个缺陷正是“单测绿、浏览器红”。

## 6. 未执行项与不能外推的结论

以下项目仍须在缺陷修复后执行，不能作为本轮不通过的替代解释，也不能在修复前推动发布：

1. 获准真实内网模型的 100 题黄金集、两轮复跑和人工 O/A 复核；
2. 真实 TDSQL Proxy/内核、批准增长规模的近期任务查询和 180 秒容量下限；
3. 200 轮压力、失败/取消/DB 断连、RSS/孤儿进程/额度回收；
4. 生产同版预演、B 组隔离验证、全新安装、补丁、旧版回退五条发布路径；
5. 真实两名自然人管理员、数据批准、TLS/供应商日志留存政策和 SCHEMA_IDENTIFIERS 独立批准；
6. 三主体反馈聚合、>1MiB 导出、长期稳态和真实浏览器多标签容量。

## 7. 第二轮质检准入条件

Q 应先按 QC1-B01—B06 完成阻断整改，再修复 M01—M05；每个缺陷提交对应的浏览器回归和服务端负例。第二轮质检至少满足：

- 空环境仅用 WebUI 完成模型配置、双管理员授权、路由和首轮调用；
- 同一浏览器连续 10 轮项目咨询，第二轮起实际出站 history 非空；
- 账号切换、撤权、导出、来源动作均无旧资料泄漏；
- 候选 SQL 四态真实生成，草稿比较/确认/revision 冲突全通过；
- 业务入口全量逐项带入正确 scene/source/draft/instance/database；
- Copilot 关闭/B 组故障时，本地帮助可用且原功能完整；
- focused、干净 HEAD 原回归、离线安装和针对性浏览器用例均提交可复核证据。

在上述条件完成前，本轮结论保持：**QC1 REJECT / NO-GO**。
