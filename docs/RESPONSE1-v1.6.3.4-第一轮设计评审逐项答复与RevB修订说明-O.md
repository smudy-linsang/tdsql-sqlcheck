# v1.6.3.4 第一轮设计评审逐项答复与 Rev.B 修订说明

提交给：Mr.Linsang

编制：O；日期：2026-09-06

评审输入：[A 第一轮评审报告](./REVIEW1-v1.6.3.4-报告实例标识与分区统计及审核网关修复设计第一轮评审报告-ClaudeA.md)，含 §7 两项裁定转录；复核基线 `main@dc7181b`。

修订交付：[详细设计 Rev.B](./DETAIL-v1.6.3.4-报告实例标识与分区统计及审核网关修复.md)。A 原报告保持不变。

## 1. 结论

A 指出的七项问题本身均认可，已在 Rev.B 修订，没有整项驳回。其中 P1-01 确实是原设计的阻断缺口：旧 sql_type 在回退路径不可靠，不能作为 R043 的判定依据。

但“认可问题”不等于逐字采用全部整改建议。对会漏计主表、引入新的完整性误报、改变既有门禁或错误描述安全/并发机制的具体文字，本答复说明不照搬的原因及替代方案。A 报告 §7 转录的 Mr.Linsang 裁定已按其业务含义落文，不再重复请 Mr.Linsang 拍板；A 的默认实现建议与裁定原话分开标记。

本次仅修改两份 Markdown 文档。没有修改业务/测试代码、配置、数据库、内网环境或既有审核结论；下列测试矩阵是开发后的验收要求，不是已经修复并通过的证据。当前状态是“设计整改完成，提交 A 二轮复核”，不是 O 自行关闭评审门禁。

## 2. 七项处理总表

|A 编号/级别|认可部分|本轮落文|不直接照搬的部分|
|---|---|---|---|
|P1-01 / P1|sql_type 在 ParseError/Command 路径可能把 ALTER 判成 UPDATE|dml_target 独立生产；R043 只认 RESOLVED＋UPDATE/DELETE＋multi=true；三出口验收|确定的非 DML 不能一律 UNKNOWN；否则可能新增 E999 误报|
|P2-01 / P2|只遍历 Proxy 分片集可能整族漏计新语法|独立逻辑目录＋三类 Proxy 并集＋逻辑基线；目录完整性/差异数显式存储|只加 only_base 仍漏掉旧 single 结果中的主表；main≤旧 shard 的上界也必须撤销|
|P2-02 / P2|名称取决于生成时是否绑定；不能因报告标识引入门禁|无新离线选择器；H02 提取前冻结且无新增门禁；保留已有绑定 API|不能说整个 /audit/file 路由“不指定实例”；旧报告有标签的现名降级不等于新报告反查|
|P2-03 / P2|Mr.Linsang 已批准同时单任务；429 必须说清未处理/未入队|单主机跨 worker 单槽；说明性 429＋两响应头；仅释放自己的锁|非阻塞拒绝不是等十分钟；2 GiB 是空闲护栏不是实测消耗；多主机各一槽不是全局一槽|
|P3-01 / P3|超时链需集中、启动时校验|统一表/配置不等式；浏览器从 capabilities 取值；真实代理另设发布检查|后端启动不能自动证明外部 Nginx/LB 生效值；空闲超时与软预算不能当总 SLA|
|P3-02 / P3|H07 也须明确 CSP/nonce 回归|H07/H08 分别保护，归一随机 nonce 后比较策略，再验证 nonce 更新与脚本对应|两条链不共享全部票据/清理/sandbox 机制；动态 CSP 不能跨响应原字节全等|
|P3-03 / P3|旧布尔值只有一个规则消费者，应去掉双写|只读派生属性，R043 直接读事实，保留真值表/调用面验证|property 不等于 dataclass 原字段的构造/asdict 兼容，不能宣称零影响或免回归|

## 3. P1-01：认可阻断问题，修正唯一事实源

### 3.1 本轮实际复现

使用仓库真实 SQLParser，不向数据库执行任何语句。正常路径调用 `SQLParser.parse(sql)`；另两组仅在进程内用 `unittest.mock.patch` 把 `backend.engine.parser.parser_legacy.sqlglot.parse_one` 分别替换为抛 `ParseError` 和返回 `exp.Command`，不编辑仓库代码。输出下表的现有字段，不把未来 dml_target 当作已存在字段。

|输入|正常 AST：sql_type / multi / parse_error 非空|注入 ParseError|注入 Command|
|---|---|---|---|
|`ALTER TABLE t MODIFY ts DATETIME ON UPDATE CURRENT_TIMESTAMP`|ALTER / false / false|UPDATE / false / true|UPDATE / false / false|
|`ALTER TABLE t ADD COLUMN a DATETIME ON UPDATE CURRENT_TIMESTAMP, ADD COLUMN b VARCHAR(20) CHARACTER SET utf8mb4`|ALTER / true / false|UPDATE / true / true|UPDATE / true / false|
|`UPDATE a JOIN b ON a.id=b.id SET a.v=1`|UPDATE / true / false|UPDATE / true / true|UPDATE / true / false|
|`DELETE a FROM a JOIN b ON a.id=b.id`|DELETE / true / false|DELETE / true / true|DELETE / true / false|

原因链与 A 判断一致：`parser_legacy.py::_detect_sql_type_regex` 在全句找到 UPDATE 就可优先于 ALTER；通用预解析又会把 ON UPDATE 到 CHARACTER SET 间的逗号当联表证据。因而仅在 R043 加 `parsed.sql_type in (...)` 无法关闭此缺口。

### 3.2 已修订内容与不同意见

Rev.B §5.2 不仅删除规则门口的旧类型判定，也禁止事实提取器和提前返回路径依赖该类型。正常 AST 直接识别顶层节点；回退从独立词法结构取得真实头及目标；规则和文案仅读 dml_target。`_detect_sql_type_regex` 的其他消费者本版不重构，明确保留其既有风险而不是谎称全局类型检测已修好。

不采纳“可靠头不是 UPDATE/DELETE 就一律 UNKNOWN”的建议。CREATE/ALTER/INSERT/SELECT 对本规则是明确不适用，若一律 UNKNOWN 再接完整性兜底，会把原来的 R043 误报换成 E999 误报。替代为可靠非 DML → NOT_DML/NOT_APPLICABLE；真实 DML 的不完整目标或真正不明的头 → UNKNOWN。任何原本的 ParseError/KFN/E999 均保留，NOT_APPLICABLE 不等于整条 SQL 被豁免。

WITH 按完整外层语句判断；无法配对才 UNKNOWN，不简单按第一个 WITH 词决定。可执行版本注释也不能当普通注释全部丢掉。附件 R036/R037/R121 保留；真实 multi UPDATE/DELETE 三路径仍须报 R043。

落点：设计 §2.3、§5.2—5.4、DML-11—17。A 新用例 DML-06—10 分别落到 DML-11—15，保留 A 原输入并补强触发版；Rev.A 原 DML-06—10 不被覆盖。

## 4. P2-01：认可候选覆盖缺口，不采用不完整的 only_base 单独补丁

当前 `table_type_stats_service.py` 的 only_base 是逻辑基线减去三类 Proxy 并集。如果新语法主表已在旧“单表”结果里，它既不属于分片集合，也不属于 only_base。A §4 已指出这种可能性，但其建议的“分片集 ∪ only_base”没有覆盖这个分支。

Rev.B §4 改为：先完成原基础统计，再独立读取 `SHOW FULL TABLES FROM <库>` 的 BASE TABLE 目录 L，结合三类 Proxy 并集 P、现有逻辑基线 B，取 `C=L∪P∪B`。其中包含 only_base，也包含落入旧单表结果的候选；不改三条原厂 Proxy 命令、原子表剔除和旧三类统计。

补独立枚举不是无依据扩展业务：工程师附件 §5/§8 本就要求从 SHOW TABLES 遍历逻辑表。FULL 形式用于排除视图，语法由 [MySQL 官方文档](https://dev.mysql.com/doc/refman/8.0/en/show-tables.html) 核实；但该网页不能证明 TDSQL 每个内核的目录行为，故实机证据前置仍保留。附件没有按内核列出原始命令返回，不能据此断言“只证明旧族、完全未涉及新族”；准确结论是缺少版本化覆盖证据。

同步收口，不仅改候选一行：

- DDL 证明为主表但不在旧分片 S 的照计，增加 outside_shard 与告警，不受旧 shard 数封顶；原总表数仍等于原三类之和，不重新归类旧 single。
- 独立目录不支持/失败/截断不能由“已知候选恰好全判明”冒充 COMPLETE。新增 inventory_state，保留已确认下界/未知展示；原状态 OK 与新指标 UNKNOWN 分开展示。
- 141 迁移设计从两表各六列增至各八列，更新公式、聚合、历史、NULL/LEGACY、前端和用例。此迁移尚未实施，不改已发布迁移 checksum。
- 新目录读取有明确行数/流式护栏，DDL 仍受 5000 次与原 180 秒软预算保护；旧基础采集优先。新增目录开销须计入验收，不能拿原分片候选规模估算新版耗时。
- 逻辑/物理证据矛盾不计正例；不因名称像物理子表就排除真正 Proxy 逻辑表，也不把物理副本重复计主表。

落点：设计 §4、§7.1—7.2、PAR-10—18。A 的 SP-0X 对应 PAR-13：only_base 主表纳入且独立目录完整、全部判明时可 COMPLETE；若目录失败就不得强行 COMPLETE。新内核不存在可测实例时，须显式标明实机/目录未验证并限定验收范围，不能用模拟返回给无范围限定的 D03 PASS。

## 5. P2-02：裁定落文，保持现有审核语义

A §7.1 转录的判据是“报告生成时是否已指定实例连接”。本轮按此办理：当前前端纯离线文件审核继续显示未关联，不新增选择器；在线元数据审核 H02 在提取前冻结真实名称，新报告导出只读快照。

不照搬的两点有代码依据：

1. A §7.1(a) 将整个 `POST /audit/file` 说成不指定连接，过于绝对。其自身 §1/§4 及 `sql_audit.py::audit_file` 均确认 API 支持 connection_id，并以 `bool(request.connection_id)` 决定既有 evaluate_gate。Rev.B 保留明确绑定 API 的名称和门禁行为，不把这类既有调用降成离线。
2. 新记录不能反查现名，但历史缺快照允许按原设计展示“非扫描时快照”的 current_lookup 标签。二者不能混为一谈；不会用现名回写历史，也不从 SQL 文件注释、文件名或 endpoint 推断名称。

H02 `extract_and_audit` 已在提取前持有 conn_info；复用而不是新增反查。调用 `audit_file_content` 仍不传 evaluate_gate（默认 False），第三返回值 gate_result=None。数据库实际对应列是 audit_history.gate_passed，因此验收持久化 NULL，而不是虚构一个 gate_result 列。未来纯报告关联另设字段、另行评审，本版不实施。

落点：设计 §1、§3.1—3.3、REP-12—14。A RPT-01/02 对应 REP-12/13，另用 REP-14 防止破坏原绑定 API。

## 6. P2-03：裁定单任务，纠正等待、空间和释放语义

A §7.2 转录“网关日志分析要限成同时只能一个人跑”，已落文为本版 GATEWAY_MAX_CONCURRENT 只允许 1，单应用主机多 worker 共用非阻塞锁；无队列，不重新申请放宽并发。采纳 A 建议 (b) 的说明性 429，明确它是实现建议而非 Mr.Linsang 原话。

不照搬以下技术描述：

- “第二提交者在应用等约十分钟才收到 429”：非阻塞取锁失败应直接拒绝。用户可能先向 Nginx 完成上传缓冲，不能把这段时间说成应用等待前一任务，更不能承诺从点击起立即响应。
- “单次任务需 2 GiB 级临时空间”：MIN_FREE_BYTES 是空闲空间准入阈值，不是目标环境的实测消耗；实际磁盘/RSS/时间仍须容量测试。
- “前置拒绝都释放槽”：未取得槽的 413/429 不能释放另一个用户的锁。改为仅清理/释放本请求实际持有资源。
- 多应用主机各一槽不能满足全局同一时刻仅一个任务；本版支持范围明确为单应用主机。多主机需要另行评审共享协调，不隐藏该边界。

429 同时返回 Retry-After、X-Request-ID、结构化请求号和“未被处理（未进入分析、未排队），请重新上传”的中文信息；重试间隔基于阶段预算、5—600 秒有界，缺失/过期回退 600 秒，只是建议，不保证届时空闲、不自动重传。状态文件只辅助提示，文件锁是唯一准入依据，不因倒计时结束偷锁。

落点：设计 §6.2—6.4/§6.6、GW-C1—C4；包含 A GW-C1/2 并细分锁所有权断言。

## 7. P3 三项

### 7.1 P3-01：合并超时链并区分可自动校验范围

认可。设计 §6.3.1 集中列明 receive=300、analysis=540、processing=600、退出预留 10、代理 read/send=660、浏览器=990 秒；写入启动配置约束和失败报错字段，浏览器从 capabilities 取同一值。

不同意见是不能只写“config.py 启动时验证 NginxTimeout”就假设外部代理已核验：后端只能验证自身和配置声明，实际 Nginx/LB 由发布检查记录生效配置。并且 [Nginx 官方说明](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_read_timeout) 的 read_timeout 是相邻读取空闲间隔，与 processing 的软预算、浏览器全链路计时不同，数值大小关系不是无条件成功保证。

落点：§6.3.1、§6.7、GW-T1/T2；声明校验与实际链路漂移各自有验收，不把后者伪装为已自动解决。

### 7.2 P3-02：H07/H08 分别保护，不能要求随机头原字节一致

认可 H07 必须补覆盖。实际 `backend/api/daily_inspect.py::_REPORT_CSP` 与 `compare_html` 每次生成 token_urlsafe(16)，将同一 nonce 传给生成器并填响应 CSP；H07 没有网关的一次性票据、_strip_inline_handlers 和不透明源 iframe。

因此不采用“两个出口都沿既有票据/清理/sandbox 链”的统一描述。也不采用跨响应 CSP 原字节全等：nonce 本来就变化，硬要求相等反而可能诱使复用 nonce。改为归一 nonce 后比较原策略不放宽，分别验证新 nonce 与脚本匹配、危险名称不执行和原图表可用。H07 基线已有 unsafe-inline/unsafe-eval 文本，不能声称它原本不存在；本版不扩大权限或顺带调整安全策略。

落点：§3.4、REP-15，保留 REP-10/GW-14 网关安全回归。无需为 H07 新增网关票据系统。

### 7.3 P3-03：单事实派生，仍须验证兼容范围

认可去掉双写。当前 `git grep` 在 backend/tests 找到旧字段定义、一次预解析写入及 R043 消费，没有第二个规则消费者。Rev.B 把该名称定义为只读派生属性，使用完整三条件，R043 直接读 dml_target。

认可 A 所说“不需要双写回归”，但不照搬“保留以减少外部快照/序列化变动，爆炸半径为零”的论证：dataclass 字段改 property 后，构造参数和 asdict 不会天然保持旧字段语义。当前未找到 ParsedSQL asdict 对外出口，public AuditResult 也不是它，因此可以采用派生属性，但仍要求检查所有构造/反射/fixture、属性真值表与连续解析隔离。取消双写同步测试与保留必要契约测试并不冲突。

落点：§5.2、DML-09/16。

## 8. 本轮验证与下一步

本轮实际完成：阅读 A 全报告及裁定转录；核对工程师材料、当前 parser/checker/报告出口/统计逻辑；对真实 parser 的三出口做本地只读基线复现；核实新增目录 SQL 语法及相关读取/超时文档；检查设计跨节字段/状态/公式和验收编号一致性。

文档静态校验：设计包含 72 个唯一的 REP/PAR/DML/GW 主验收编号（编号可展开多路径，不等于已运行 72 个测试）；两文档代码围栏配对、相对文档链接有效、Git 空白检查通过。提交范围限定设计和本答复，A 的原评审文档不修改。

未执行：v1.6.3.4 代码修复、数据库迁移、修复后单测/SIT/UAT、真实 TDSQL 新旧目录对账、71 MiB 真实日志性能测试和内网部署变更。本轮文档检查不能替代这些证据。

请 A 按 Rev.B §10 的七项索引复核，重点确认 P1-01 三出口不依赖旧类型、P2-01 候选覆盖/完整性与两项裁定准确落文；由 A 回填是否关闭设计阻断。设计通过后 Q 按修订版实施，A/O 再执行对应测试阶段，不以本答复代替开发准入或生产发布签署。
