# v1.6.3.4 详细开发设计说明书

版本：Rev.A（研究完成，提交评审；不是开发完成或测试通过声明）

需求提出与验收方：Mr.Linsang

编制日期：2026-09-06

研究基线：`main@88954b3`，业务功能版本 v1.6.3.2；依赖基线 `sqlglot==30.14.0`。

本次交付边界：只新增本设计文档；未修改业务代码、测试代码、配置、数据库或内网环境。

## 1. 结论与范围

|编号|本版交付|明确不做|
|---|---|---|
|REQ-01|所有实际生成的 HTML 报告显示实例连接名称；扫描时固化名称，历史/离线/多实例来源可辨认|不把连接 ID、端口、数据库名冒充连接名称；不回写历史检查结论|
|REQ-02|表类型统计增加“二级分区主表”；识别逻辑表的一级分布与二级分区两层结构|不把物理子表数当主表数，不改变原有三类表及总表数口径，不新建导出功能|
|REQ-03|修复 R043 的语句作用域识别，保留真实联表 UPDATE/DELETE 的拦截|不关闭/降级 R043，不豁免整条 CREATE 的其他审核，不调整已签署的 R121 策略|
|REQ-04|打通约 71 MiB 网关日志的受控上传、分析、持久化与错误展示|不以放开全站限额或无限超时为方案；本版不引入 Celery/Redis、断点续传或新的任务平台|

关键决定：

1. R043 已在本地使用附件原文复现。根因不是 TDSQL 建表语法不合法，而是通用预解析正则跨越了语句内部的字段定义。
2. 内网诊断报告漏掉应用默认 **50 MiB 请求体限制**。本地已证明相同大小声明的请求会在上传处理函数前返回 413；但没有内网请求状态码/生效配置/异常栈，不能据此宣称已还原那一次事故的唯一原因。
3. 二级分区主表是分片逻辑表的子集。新增列不参与“总表数＝单表＋广播表＋分片表”的加法。
4. 采用“统一报告来源上下文＋各生成器适配”，而非导出时读取页面当前选择。网关采用“路由专属限额＋流式落盘＋非事件循环执行＋受控子进程”，保持现有同步响应契约。

## 2. 研究证据与官方核对

### 2.1 输入材料

|材料|位置/标识|用途与证据边界|
|---|---|---|
|工程师说明|`C:/Users/linsa/OneDrive/Desktop/tdsql_二级分区表识别逻辑.md`|认可两层结构定义；原文 grep 方案不是可直接投产的解析算法|
|误报报告|`C:/Users/linsa/OneDrive/Desktop/TDSQL审核报告_New 2.txt_2026-09-05.html`，报告 ID 6537|记录 CREATE 触发 R036、R037、R043、R121|
|原始 SQL|`C:/Users/linsa/OneDrive/Desktop/New 2.txt`|本地只读解析、规则执行的输入；未向任何数据库执行 DDL|
|内网诊断|[网关日志分析-上传失败诊断报告.md](./网关日志分析-上传失败诊断报告.md)|记录文件 74,560,116 字节、125,398 行、访问地址端口 8000；其根因概率和耗时估算不作为实测证据|

SHA-256（便于 Q/A/O 核验使用同一份附件）：

```text
New 2.txt
92388E20A4F537A47A9470C8A5ED9DAFABF7D684EBB2ED2E0CA61494222EDA78
TDSQL审核报告_New 2.txt_2026-09-05.html
D5119581410544EB0985C8739A5DA4E00853ABDCDC01F17750F65A045FA3B9E7
tdsql_二级分区表识别逻辑.md
4BF80EE17CDF616FC32812C5F1D5691B015E2BDEB80F435D3EC1F1FDA995D78E
网关日志分析-上传失败诊断报告.md
92FA615B5FB1B1BAE0FA6C0400BF94D10B91A0478036FC8794A20E2D7B04094F
```

### 2.2 官方依据及适用范围

以下网页已于 2026-09-06 检索核对。TDSQL MySQL 版与 TDSQL-C、Boundless 不混用；官方能力说明与项目治理规则也不混用。

|编号|官方来源|本设计采用的事实|
|---|---|---|
|S01|[腾讯云 TDSQL MySQL 版：建表](https://cloud.tencent.com/document/product/557/8767)|一级 HASH 的 shardkey、一级 RANGE/LIST 的 TDSQL_DISTRIBUTED；广播标记 noshardkey_allset；不同内核有语法差异|
|S02|[腾讯云 TDSQL MySQL 版：二级分区](https://cloud.tencent.com/document/product/557/58907)|一级分布叠加 RANGE/LIST 二级分区；示例既有 shardkey 在 PARTITION 前，也有 TDSQL_DISTRIBUTED 在 PARTITION 后|
|S03|[腾讯云 DTS：使用说明，分区表同步](https://cloud.tencent.com/document/product/571/105000)|明确列出新二级组合 TDSQL_DISTRIBUTED BY HASH + TDSQL_PARTITION BY RANGE/LIST；这是腾讯云官方兼容说明，不是完整新内核语法手册|
|S04|[MySQL：自动初始化和自动更新](https://dev.mysql.com/doc/refman/8.0/en/timestamp-initialization.html)|ON UPDATE CURRENT_TIMESTAMP 是时间字段属性，不是顶层 UPDATE 语句|
|S05|[MySQL：UPDATE](https://dev.mysql.com/doc/refman/8.0/en/update.html)、[DELETE](https://dev.mysql.com/doc/refman/8.0/en/delete.html)|区分修改目标表引用、赋值表达式与子查询；DELETE 有目标列表和 USING 两种多表形式|
|S06|[NGINX：client_max_body_size](https://nginx.org/en/docs/http/ngx_http_core_module.html#client_max_body_size)|请求体超限返回 413；限制可按 location 设置|
|S07|[NGINX：proxy_read_timeout](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_read_timeout)|这是相邻读取之间的空闲超时，不是整个请求的总时限|
|S08|[FastAPI：文件上传](https://fastapi.tiangolo.com/tutorial/request-files/)|UploadFile 自带内存/磁盘暂存机制；调用无界 read() 会重新把完整文件读进内存|
|S09|[MySQL：存储空间要求](https://dev.mysql.com/doc/refman/8.4/en/storage-requirements.html)、[Packet Too Large](https://dev.mysql.com/doc/refman/8.0/en/packet-too-large.html)|MEDIUMTEXT 是小于 2^24 字节，LONGTEXT 是小于 2^32 字节；报文限制独立于列容量|

采纳工程师材料的“先识别广播，再判断两层结构”语义，但不直接使用原始文本 grep：注释、字符串、反引号标识符、换行、可执行注释及分区表达式中的关键词都会影响命中。S03 不足以证明所有新语法变体可在所有内核上执行；本版只识别目标实际返回的 DDL，不扩展核心 SQL 审核器的新方言恢复白名单。S02 中自动维护分区的能力说明，也不替代 Mr.Linsang 已确定的业务自行维护、禁止 MAXVALUE 的治理政策。

### 2.3 已执行的只读复现

调用现有 `SQLParser.parse()` 和 `RuleChecker.audit_sql(..., instance_type='distributed')`，没有写库：

|输入|基线结果|本版期望|
|---|---|---|
|附件 New 2.txt|CREATE TABLE；parse_error=null；multi=true；R036/R037/R043/R121|只消除 R043；其余三条保留|
|§5.1 最小 CREATE|multi=true，误报 R043|multi=false，无 R043|
|去掉最小 CREATE 的 ON UPDATE|multi=false|保持|
|UPDATE t1 a, t2 b SET …|multi=true，R043|保持|
|UPDATE t1 a JOIN t2 b … SET …|multi=true，R043|保持|
|UPDATE t PARTITION (p0,p1) SET v=1 WHERE id=1|multi=true，误报 R043|分区列表不是表列表，无 R043|
|UPDATE `a,b` SET v=1 WHERE id=1|multi=true，误报 R043|标识符内逗号不算表分隔，无 R043|
|单表 UPDATE 的 SET 子查询含 JOIN|multi=false|保持|
|单表 DELETE 的 WHERE 子查询含 JOIN|multi=false|保持|

另对当前 `BodySizeLimitMiddleware.dispatch` 隔离调用：屏蔽数据库配置覆盖和环境变量覆盖，仅注入 `Content-Length=74560116`，有效默认值为 `52428800`，返回 HTTP 413，上传处理函数没有执行。此实验验证当前代码默认路径，不代表已经检查内网配置或分析过原始 71 MiB 日志。

## 3. REQ-01：所有 HTML 报告添加连接名称

### 3.1 全量生成入口清单

开发和测试须按本清单逐行销项，不能用“公共方法单测通过”代替实际报告校验。

|ID|入口/生成位置|来源与改动|
|---|---|---|
|H01|`backend/api/sql_audit.py::export_file_report_html`，`/api/v1/audit/file-reports/{id}/html`|audit_history；目前页眉只有文件、审核人、架构等，补上下文。当前文件审核前端未绑定连接，按离线语义处理|
|H02|同文件 `export_extracted_report_html`，`/api/v1/audit/report/{id}/html`|audit_history；在线提取开始时冻结连接名称，不从生成的 SQL 文件名推断|
|H03|`backend/api/slow_query.py::export_scan_task_html`|scan_tasks；已有 connection_name，但 `scan_service._do_scan` 当前写入 host:port，须修正写入源|
|H04|`backend/api/inspection.py::export_schema_check_report`|该入口会重新检查；在本次检查开始时按 request.connection_id 取名，不按 host+port 反查第一条同端点连接|
|H05a-d|`scan_compare_report.py::render_single_snapshot_html`|schema_audit、slow_scan、launch_check、bigtable 四种快照分别验收；继承源任务的上下文|
|H06a-d|同文件 `render_compare_html`|上述四类对比；基准和目标各显示自己的扫描时名称，不能只显示汇总中的一个名称|
|H07|`daily_inspect_service.py::generate_comparison_html_report`|daily_inspection、server_daily_inspection；连接名称与 Set/节点 instance_names 区分；各日期来源分别保留|
|H08|`gateway_log_service.py` + `gateway_log.py::get_report_html`|新报告生成时嵌入冻结名称；旧 report_html 服务时补来源块，保持票据、nonce、iframe 安全链|
|H09|`backend/api/raw_slowlog.py::export_events(format='html')`|slow_log_events 经 source_id 关联连接；多实例时页眉列来源，明细逐行增加连接名称；保留脱敏与最多 10000 行覆盖提示|
|H10|`gateway_log_analysis/analyze_gateway_log.py` HTML 输出|平台调用传上下文文件；独立 CLI 可传人工连接名称，未提供明确显示未关联|
|H11|`gateway_log_analysis/merge_gateway_reports.py` HTML 输出|从各输入 JSON 继承来源；合并保留多实例映射，不能用一个参数覆盖所有实例|
|H12|`gateway_log_analysis/interf_report_generator.py::generate_html_report`|combined/各实例两种输出，按输入分组映射上下文；原分组名/IP 不是必然的平台注册名称|
|H13|`gateway_log_analysis/interf_deep_analysis.py::generate_html`|新增可选名称上下文并传给 meta；不改变已有分析/数据库操作|
|H14|`backend/static/scripts/disk_performance_test/generate_report.sh`|提供显式人工名称参数/上下文；未关联显示“未关联实例（主机磁盘测试）”，不把主机名当数据库连接名|

`frontend/index.html`、错误响应片段、vendor 资源及 `docs/evidence` 中既有历史测试证据不是新生成报告，不改写。`report_service.py` 当前直接生成 PDF，没有 HTML 生成入口；本版不要求 PDF 改版。后续开发若检索发现其他生产 HTML 生成器，必须补入 H 清单后接入同一契约，不得静默遗漏。

### 3.2 公共模型与名称优先级

新增 `backend/services/report_context.py`，只处理来源值和安全渲染，不承担审核、采集或鉴权。冻结结构：

```json
{
  "version": 1,
  "captured_at": "2026-09-06T10:00:00+08:00",
  "origin": "bound",
  "connections": [
    {
      "connection_id": "conn-001",
      "connection_name": "ECIF-分布式-测试环境",
      "name_source": "snapshot",
      "db_name": "lzbi_ecif"
    }
  ]
}
```

函数契约：

```text
capture_report_context(connection_id, db_name, origin) -> ReportContext
resolve_legacy_context(record, related_source=None) -> ReportContext
merge_report_contexts(contexts_with_roles) -> list[RoleContext]
render_report_context(context, role=None) -> escaped HTML fragment
```

规则：

- 新的绑定检查：在扫描/任务受理时按**连接 ID 精确查找** `registry.get_saved(id)`，一次取值并传递；名称不是 host:port，也不是数据库名。查不到已指定 ID 时拒绝新任务，不能自动切默认连接。已有默认/即席连接路径要传递实际解析后的连接身份；即席路径没有保存名称时明确“未命名即席连接”。
- `origin=bound|offline|manual|legacy`；`name_source=snapshot|manual|legacy_stored|current_lookup|missing`。名称用注册值原文，按现有连接名称最大长度校验；不额外截断中文名称。连接 ID 和库名可作为辅助字段，但不代替名称。
- 当前文件审核是离线输入，未传 connection_id：显示“实例连接名称：未关联实例（离线文件审核）”。不偷偷绑定页面的全局 currentConnectionId，不改变其手选分布式/集中式架构行为。API 调用原本明确提供连接 ID 的，按绑定处理。
- 新记录一经完成，重命名/删除连接不改扫描快照。扫描途中改名，仍用开始时冻结的名称。历史重建、快照 upsert 也不得用重建时现名覆盖已存上下文。
- 历史无上下文：先读有明确来源的历史名称；旧字段只是 endpoint 的，作为“历史端点”展示。仅能关联当前配置时，显示“扫描时名称未记录；当前连接名称：X（非扫描时快照）”；连接已删则显示“历史未记录名称（连接 ID：…）”。不能把现名伪造为历史名称，不批量补写历史列。
- 对比报告必须标“基准扫描”“目标扫描”，相同 ID 改过名仍各显各名。日常巡检以日期/行上下文分组，不任取第一行；同日重采保留与当前指标一起更新的上下文。同日期出现多份采集上下文时原样分组，注明不是单次一致性快照。
- 原始慢日志按采集时来源固化；来源配置后续重新绑定实例，不得污染旧事件。无筛选导出以实际返回事件去重列出来源，并逐行映射；零行时区分“指定来源零行”与“未筛选且无事件”，不虚构实例。
- CLI 传入的名称标 `manual`，不宣称已在平台核验。多文件合并上下文用输入分组键映射，无法关联的组单独标未知。保留老命令可运行，但报告必须出现明确的未关联提示。

### 3.3 存储及调用链

新增迁移 `backend/schema/v14/140_report_context.sql`，下列七张表各增加一列，逐表独立一条 DDL：

```sql
ALTER TABLE audit_history ADD COLUMN report_context_json TEXT NULL DEFAULT NULL;
ALTER TABLE scan_tasks ADD COLUMN report_context_json TEXT NULL DEFAULT NULL;
ALTER TABLE scan_snapshots ADD COLUMN report_context_json TEXT NULL DEFAULT NULL;
ALTER TABLE inspection_tasks ADD COLUMN report_context_json TEXT NULL DEFAULT NULL;
ALTER TABLE daily_inspection ADD COLUMN report_context_json TEXT NULL DEFAULT NULL;
ALTER TABLE server_daily_inspection ADD COLUMN report_context_json TEXT NULL DEFAULT NULL;
ALTER TABLE gateway_log_reports ADD COLUMN report_context_json TEXT NULL DEFAULT NULL;
```

单任务上下文 UTF-8 JSON 限 8 KiB；持久化前校验结构，不允许客户端塞任意对象。多实例合并使用输入上下文集合，不塞入单实例的 8 KiB 槽位。

具体写链：

1. `audit_service.py` 审核入口/在线提取调用 → 一次 capture → `_save_audit_history` 增参数、INSERT → 源快照创建继承。不要在 `_save_audit_history` 才首次取名。
2. `scan_service.py::_do_scan` → `slow_query_service.py::create_scan_task` 存名称及上下文 → slow_scan 快照继承。任务名称原有格式可保留，不再以其字符串作为来源。
3. `inspection.py` 检查开始 → `inspection_service.py` 任务持久化 → launch_check 快照；直接 HTML 检查出口同样 capture，不误用历史结果时间。
4. `scan_snapshot_service.py` 创建/查询/重建均透传字段；有上下文的同 biz_ref_id 记录 upsert 保留首次来源。`scan_compare_service.py::run_compare/_snap_brief` 组合并保留 base/target 的上下文；`scan_compare_reports.summary_json` 新增 `report_contexts`，不另加字段、不改变比较算法。
5. `bigtable_service.py` 采集开始 capture，传入 `_create_snapshot` 并继承至 live 快照；不能在当前 `_create_snapshot` 的扫描结束阶段才首次取名，更不能等用户导出才解析名称。
6. `daily_inspect_service.py::run_daily/run_server_daily` 用同一采集上下文入指标行；30 秒缓存须返回其原上下文，不能包装成新时点。
7. `raw_slowlog_service` 的采集运行开始冻结，通过事件现有 `extra_json.report_context` 持久化，不为高容量事件表新增列；老事件经 source 关联仅作现名降级。导出结果必须带逐事件上下文，避免 N+1 查询，按 source_id 批量取旧来源。
8. 网关调用使用受理时上下文生成 HTML，同时与统计结果一次事务入 `gateway_log_reports`；历史报告 GET 不写库。

新装基表、数据库访问显式 SELECT 列、INSERT 参数数、返回模型、快照大小统计和保留/清理代码须同步核对。旧 `results_json`/`snapshot_json` 的主体类型不变，不能把原数组改成对象导致旧读端失效。

### 3.4 HTML 与安全约束

统一页眉标签用“实例连接名称”，在标题下、首个指标区之前；长名自动换行，打印可见。多实例用名称＋ID 的来源表及角色列，明细能对应。

所有新增名称、库名、角色说明按 HTML 文本上下文转义（包括引号）；JS 中需要的数据使用 JSON 安全序列化，转义 `< > & U+2028 U+2029`，禁止直接拼接到 `<script>`、onclick 或 URL。独立脚本也要覆盖 `</script>`、反引号、中文与换行测试。

新网关模板预留唯一 `data-report-context-version="1"` 来源区。旧 report_html 在 `<body>` 开始处或旧模板明确的 container 锚点补块一次；无 body 的历史片段用安全外层文档容纳，不能全局 replace 任意文本。仍由既有 `_strip_inline_handlers` 和 nonce 逻辑处理响应，保留 90 秒一次性共享票据及不透明源 sandbox。不得为显示名称放松 CSP。

## 4. REQ-02：“二级分区主表”统计

### 4.1 定义与不变量

在分布式实例、当前可见业务库范围内，一个由 Proxy 暴露的逻辑表，同时具备一级数据分布和二级 RANGE/LIST 分区结构，计为一张二级分区主表。主表有多少范围分区、有无 MAXVALUE，不改变“计一张”。有 MAXVALUE 的真实表依然要计入统计，R121 是否允许创建是另一件事。

```text
总表数 = 单表 + 广播表 + 分片表                         （维持原值）
0 <= 二级分区主表已确认数 <= 分片表数                   （同一可用库范围）
二级分区主表不再加到总表数，也不从分片表数中扣除
二级分区子表 = 现有 information_schema + 命名/父表/Proxy 三条件口径（不变）
```

集中式按此定义不存在“一级 Set 分布＋二级分区”，新增列显示 `0（不适用）`，即使原生 MySQL PARTITION/SUBPARTITION 非空也不计入。新字段不是原生 MySQL 的 SUBPARTITION_NAME 计数。

### 4.2 识别器

新增 `backend/services/tdsql_table_shape.py`，接口 `classify_logical_ddl(ddl, expected_database, expected_table) -> ShapeEvidence`：

```text
state: SECONDARY | NOT_SECONDARY | UNKNOWN
distribution: SHARDKEY_HASH | TDSQL_RANGE | TDSQL_LIST | TDSQL_HASH | BROADCAST | NONE | UNKNOWN
partition: RANGE | LIST | NONE | UNKNOWN
syntax_family: LEGACY | MODERN | NONE | UNKNOWN
reason_code: 固定枚举；不得携带完整 DDL
```

该识别器消费**目标实例成功返回的 SHOW CREATE TABLE**，做结构识别，不代替核心审核器证明 SQL 可执行。词法模块可使用锁定版本 sqlglot tokenizer，但不得调用/更改 `_plan_recovery` 的方言准入规则。

步骤：

1. token 化并验证 CREATE TABLE 头、限定表名与请求一致；反引号中的转义反引号正确解码。找到表定义列表配对右括号，之后才进入表尾解析。
2. 表定义内的列名、COMMENT 字符串、普通注释均不得贡献 distribution/partition 关键词。词法失败、返回截断、目标不一致返回 UNKNOWN。
3. MySQL 可执行版本注释不能作为普通注释丢弃。仅对 SHOW CREATE 来源中完整 `/*!版本号 … */` 片段解析内部 token，并保留位置/边界；不跨字符串匹配，不把普通提示注释当 SQL。无法确定版本条件或不完整片段则 UNKNOWN，不能认定没有分区。
4. 表尾逐个识别完整 token：`SHARDKEY = <identifier>`；`TDSQL_DISTRIBUTED BY <HASH/RANGE/LIST> (...)`；`PARTITION BY <RANGE/LIST> [COLUMNS] (...) (...)`；`TDSQL_PARTITION BY <RANGE/LIST> [COLUMNS] (...) (...)`。配对括号跳过表达式与定义列表，允许合法表选项穿插，不要求分布子句固定先后位置。COLUMNS 只在目标实际返回的对应头中识别，不由本设计宣称新内核全部支持该语法。
5. 优先识别精确 `shardkey=noshardkey_allset` 为广播；不能因字符串包含 shardkey 就计分片。多处分布声明冲突、重复分区头、未知 TDSQL 结构、未闭合列表返回 UNKNOWN。
6. 按下表组合。单独出现 PARTITION 或只有一级分布均为 NOT_SECONDARY。未知组合不“猜成”普通分片。

|一级事实|二级事实|结论|
|---|---|---|
|SHARDKEY（非广播）|PARTITION BY RANGE/LIST|SECONDARY，旧 HASH 二级|
|TDSQL_DISTRIBUTED BY RANGE/LIST|PARTITION BY RANGE/LIST|SECONDARY，旧 RANGE/LIST 二级|
|TDSQL_DISTRIBUTED BY HASH|TDSQL_PARTITION BY RANGE/LIST|SECONDARY，新二级|
|广播|无或分区结构|不计主表；若与 Proxy 分片候选冲突记元数据冲突，不能当完整结果|
|仅一级分布|无分区|NOT_SECONDARY|
|无分布|原生 PARTITION 或 SUBPARTITION|NOT_SECONDARY|
|其他交叉代际组合/不完整结构|任意|UNKNOWN，记录原因，等待官方/实际返回样例覆盖|

禁止使用 `re.search('partition.*by', ddl)`、`'_tdsql_sub' in name`、字段 SUBPARTITION_NAME 或子表名称前缀数量推断主表数。若以正则实现局部 token 识别，必须在词法界定的表尾片段上完整匹配且经过上述状态机，不能对 raw DDL 全局搜索。

### 4.3 采集流程、负载与错误

在 `table_type_stats_service._collect_distributed/analyze` 保留现有 Proxy 三命令、名称归属、大小写歧义、重叠优先级、子表剔除和 reconciliation。先完成原有基线与 Proxy 采集，之后再执行主表识别；新增扫描不能抢先耗尽预算而把原来能统计的库变为失败。

1. 从当前请求最终归一化的分片逻辑表集合产生候选 `(库名,表名)`，保留其归属、原始 kinds_seen。只处理原有 eligible 库；failed/skipped 库的新列为 UNKNOWN，不记零。
2. 对候选执行 `SHOW CREATE TABLE <quoted_db>.<quoted_table>`，通过现有只读临时连接访问 Proxy，不直连 Set。不再遍历 information_schema 的物理子表。库名/表名分别反引号转义（反引号加倍），不能值占位符代替标识符，不能字符串拼接未转义名称。
3. 同请求去重后每候选最多一次 SHOW CREATE，不做持久缓存，避免 DDL 变化后陈旧结果。单实例串行 DDL 读取，沿用 registry.scan_slot，不创建每表线程。每次连接/命令前检查原 `deadline`，沿用 180 秒**软预算**，新增不定义假的硬墙钟承诺。
4. 新护栏 `MAX_PARENT_DDL_PER_RUN=5000`，按库/表精确名稳定排序。超过护栏/预算只停止主表识别，保留已完成基础统计。每条 DDL 最多接收处理 2 MiB；超出返回 UNKNOWN，不能截断后当普通表。连接 read_timeout 取不大于原 30 秒与剩余软预算的值，并明确它仍是 socket 空闲超时。
5. SHOW CREATE 权限不足、表在扫描中删除/改名、Proxy 与 DDL 的分片/广播事实冲突、缺少 DDL 列、词法不支持，均记 unknown，不计 confirmed_negative。可继续的错误继续下一表；连接已不可用则关闭重建，禁止在坏会话上循环重试。
6. 原有 KIND_OVERLAP 不改优先级。若候选同时被 Proxy 返回为广播，或者 DDL 表明不是分布表，新增 METADATA_CONFLICT 告警并标 UNKNOWN，不能用“广播优先”改动原来总数。
7. 参数含全部库且枚举截断、名字归属歧义或 Proxy 失败时，实例级完整性不为 COMPLETE。无权看到的库不可能自动推算；报告说明“账号可见范围”，不能宣称物理集群全量。

### 4.4 API、持久化、前端

原 POST `/api/v1/table-type-stats/run`、history/detail 路径不变。头部与逐库 item 新增：

|字段|类型/存储|语义|
|---|---|---|
|secondary_partition_main_tables|INT NULL DEFAULT NULL|已确认主表数；旧记录/基础采集失败为 null；完成且无主表才是 0|
|secondary_partition_check_state|VARCHAR(24) NOT NULL DEFAULT 'LEGACY'|COMPLETE、PARTIAL、UNKNOWN、NOT_APPLICABLE、LEGACY|
|secondary_partition_candidates|INT NULL DEFAULT NULL|可用 Proxy 分片候选数|
|secondary_partition_checked|INT NULL DEFAULT NULL|已经得到 SECONDARY/NOT_SECONDARY 确定结论的候选数|
|secondary_partition_unknown|INT NULL DEFAULT NULL|已尝试但无法判断的数|
|secondary_partition_unchecked|INT NULL DEFAULT NULL|预算/护栏停止后未尝试的数|

新增 `backend/schema/v14/141_secondary_partition_main.sql`，对 `table_type_stat` 和 `table_type_stat_item` 分别按上表执行六条独立 ADD COLUMN；不修改 v13 历史迁移及校验和。同步 `_STAT_CONTRACT/_ITEM_CONTRACT`、派生 `_STAT_COLUMNS/_ITEM_COLUMNS`、INSERT、history/detail SELECT 和启动结构校验。

计数验收公式：`candidates = checked + unknown + unchecked`，`main <= checked`；confirmed negative = checked-main。该公式只针对已知候选集合，未成功枚举的库不伪造候选数。

状态：基础成功且所有候选判明为 COMPLETE（零候选也完整）；至少有一项判明但仍有 unknown/unchecked 为 PARTIAL；无一项判明或基础不可用为 UNKNOWN；集中式为 NOT_APPLICABLE（数值 0）；老记录为 LEGACY（null）。实例汇总主表数只加 eligible 库的已确认值，缺库时状态降级；基础全失败为 null。

示例：分片表 99，确认 4 张主表；查明 90，错误 2，未查 7：

```json
{
  "secondary_partition_main_tables": 4,
  "secondary_partition_check_state": "PARTIAL",
  "secondary_partition_candidates": 99,
  "secondary_partition_checked": 90,
  "secondary_partition_unknown": 2,
  "secondary_partition_unchecked": 7
}
```

页面显示 `≥4（未完成）`，说明显示“判明 90/99，失败 2，未检查 7”。UNKNOWN 显示 `—（未知）`；LEGACY 显示 `—（历史未采集）`；不能通过 `value || 0` 抹掉 null。partial 已确认数为 0 时显示“已确认 0，未完成”，不能只显示 0。

`frontend/index.html` 中即时结果、汇总、历史列表/历史详情同步增加“二级分区主表”列，放在“二级分区子表”之前。前端仍使用 app.js 现有请求归属/序号防护，切实例/切页签/加载历史后的迟到响应不能覆盖当前上下文。告警仍聚合、样本最多 20 个、detail 不超过 512 字符，不能输出全部 DDL 或 5000 条横幅。

## 5. REQ-03：R043 核心解析缺陷

### 5.1 已证实的因果链

基线 `backend/engine/parser/parser_legacy.py::_regex_pre_parse`（约 3706—3734 行）对所有语句执行：

```python
m_upd = re.search(r"\bupdate\b(.*?)\bset\b", clean_sql_no_comm, re.DOTALL)
upd_multi = bool(m_upd and ("," in m_upd.group(1)
                           or re.search(r"\bjoin\b", m_upd.group(1))))
```

附件第 21 行的 `ON UPDATE` 被当成起点，第 22 行 `CHARACTER SET` 中的独立单词 SET 被当成终点。中间有两个字段的分隔逗号，因而 `is_multi_table_update=True`。这里匹配到的**不是** CHARSET 内部子串；正则存在单词边界，真正终点是 CHARACTER SET。

最小复现（不是推荐业务建表模板）：

```sql
CREATE TABLE t (
    ts DATETIME ON UPDATE CURRENT_TIMESTAMP,
    c VARCHAR(20) CHARACTER SET utf8mb4
);
```

后续 AST 虽正确识别 CREATE TABLE，也没有清除预解析的假 multi 标志。`backend/engine/rules/dml.py::R043NoMultiTableUpdate.check` 只读此布尔值，没有检查 sql_type，最终以 ERROR 误拦。DELETE 分支同样全句搜索，须作为同一个规则事实生产器一起收口。

仅增加 `if CREATE: return` 不能解决 `UPDATE t PARTITION(p0,p1)`、反引号逗号等同源误报；仅把正则改成 `^UPDATE` 又会遗漏 WITH、合法前导注释等情况。这两种补丁都不作为验收方案。

### 5.2 事实模型及落点

保留 `ParsedSQL.is_multi_table_update` 兼容老调用者（其历史语义也包含 DELETE）；新增内部结构 `dml_target`，不新增规则编号：

```text
statement_kind: UPDATE | DELETE | NOT_DML | UNKNOWN
status: RESOLVED | UNKNOWN | NOT_APPLICABLE
form: SINGLE | UPDATE_TABLE_REFERENCES | DELETE_TARGET_LIST | DELETE_USING | NONE
is_multi_table: bool | null
reason: stable code
```

落点与执行顺序：

1. 删除 `_regex_pre_parse` 对联表 UPDATE/DELETE 的两段事实赋值。其他已有规则预解析不借机重构。
2. 在顶层 AST 得到并确定 sql_type 后，调用新增 `_extract_dml_target(ast, original_sql)`。正常解析只利用 AST，不再为每条正常 DML 重复 tokenize；保持 v1.6.3.2 已建立的解析次数性能约束。
3. `_parse_update/_parse_delete` 使用该结构同步兼容布尔值；任何非 UPDATE/DELETE 顶层节点都使其为 false，不能复用上条 SQL 的状态。
4. ParseError/Command 回退出口在返回前也要处理：只有能以可靠词法头确认 UPDATE/DELETE 才进入有限回退；无法可靠判断则 dml_target.status=UNKNOWN。原有解析错误、KFN、E999 不能因提取出 multi 而被清除。
5. R043 防御式检查 `parsed.sql_type in ('UPDATE','DELETE')` 且已确认 multi 才报规则。UNKNOWN 不伪造联表违规，但必须由审核完整性路径输出 E999/不可完成审核，不能以“R043 不命中”冒充审核通过。新增 DML 事实失败原因并入 checker 已有完整性门禁，不覆盖既有 parse_error 文本。
6. R043 保持仅分布式、ERROR、启停及规则集覆盖能力；文案按真实类型显示 UPDATE 或 DELETE。其他架构的审核不得因 R043 本身新增 error；真实解析不完整的既有门禁仍照常生效。

### 5.3 AST 识别边界

以锁定的 sqlglot 30.14.0 AST 为实现基准，先用单测固定节点形状，再写遍历器。绝不能用 `len(parsed.tables)>1` 或 `ast.find_all(Table)` 的全树表数量判断。

|对象|允许读取的区域|不可计入的区域|
|---|---|---|
|UPDATE|顶层 Update.this 表引用及其本层 Join/逗号等价 Join、括号包裹的表引用组|SET expressions 中的 SELECT、WHERE 子查询、CTE 定义体|
|DELETE|顶层 Delete 的 targets/tables、using 与 from/this 表引用结构|WHERE 子查询、RETURNING 表达式（若方言支持）、CTE 定义体|

具体判据：

- UPDATE 顶层表引用有 JOIN 或多个关系项即联表；`STRAIGHT_JOIN`、LEFT/RIGHT/INNER/CROSS/NATURAL JOIN 的等价节点都覆盖。自关联按**关系项**而非去重后的表名数量判断。
- 表引用内部的 PARTITION 列表、USE/FORCE INDEX 列表、函数参数、带引号标识符中的逗号不算关系分隔。括号中的 JOIN 表引用组需要递归，不能“一见括号就忽略全部”。遇到 Select/Subquery 的查询体则止步，不数其中的基础表。
- DELETE 显式目标列表（`DELETE a FROM …`）或 USING 属多表语法形式，延续现有规则管控；即使目标只列一个别名也不放行此多表形式。常规 `DELETE FROM t [AS a] …` 是单表形式，LOW_PRIORITY/QUICK/IGNORE 不是别名。
- WITH 后的顶层 UPDATE/DELETE 按其目标判断；CTE 自身 JOIN 不污染外层。语法是否被特定 TDSQL 内核支持，由原有方言/完整性规则处理，本次不额外宣布能力开放。
- CREATE/ALTER 的字段自动更新、外键 ON UPDATE/ON DELETE、INSERT ON DUPLICATE KEY UPDATE、字符串里的 UPDATE/DELETE 均不属于顶层 DML。

有限回退只在 AST 不可用时使用：tokenizer 屏蔽普通注释、字符串内容和带引号标识符内容；解析合法修饰词、可完整配对的 WITH 定义，定位真实顶层语句头；以配对括号和保留字解析 table_reference/table_references，而非 raw 字符扫描。只接受已写测试的完整形式，遇到未知 token/不完整目标/可执行注释语义不明返回 UNKNOWN。不得以“未见 JOIN”推导 SINGLE，更不得重启旧全句正则兜底。

### 5.4 不回退的既有行为

- 附件的 MAXVALUE 仍必须命中 R121；去掉 R043 后不能把该附件整条 SQL 改成 PASS。
- 不修改裸 MAXVALUE 归一化、CREATE 非 TABLE 分流、R035 长度豁免、R058 LIMIT 2000 等 v1.6.3.2 定版行为。
- 即时审核、文件批量审核、在线元数据审核共用 parser/checker；不能只在 HTML 报告过滤 R043，或只在一个 API 做 SQL 文本替换。
- 原始 SQL、文件行号、报错定位、主键/唯一键/字段属性及规则集覆盖保持原值。新增结构不改变 public AuditResult 的现有字段类型。

## 6. REQ-04：网关大日志失败诊断与修复

### 6.1 对内网诊断报告的采纳与纠正

|结论|处理|依据|
|---|---|---|
|Nginx 样例限制 20m|认可为潜在独立阻断|`deploy/nginx-sqlcheck.conf` 确实配置 20m；但直连 8000 未必经过该代理|
|绕过 Nginx 就不受大小限制|不认可|BodySizeLimitMiddleware + config.max_body_bytes 默认 50 MiB，已本地复现 413|
|120 秒子进程超时可能失败|认可风险，不认可“该文件必需 2—4 分钟”|代码硬编码 timeout=120；没有原文件和目标硬件计时证据|
|LONGTEXT 约 16 MB、MEDIUMTEXT 约 64 MB|不认可|与官方列容量定义相反/不符；见 S09。应检查实际列结构及 max_allowed_packet|
|快速统计不会失败|不认可绝对结论|read→decode→splitlines 同时保留多份内容，低内存/并发/长行可失败；倍数和 RSS 必须实测|
|文件格式全部正确|暂不作为全量事实|当前只有报告中的局部样例，没有完整 125,398 行原始日志|
|按 60%/30% 等给根因概率|不采用|无采样或日志支持；改用可验证诊断分支|
|全局调到 200m，超时加大即可|不足|遗漏应用限额、非 JSON 错误展示、事件循环阻塞、子进程退出/输出完整性、落库报文与资源上限|

当前确定的代码问题：

1. 请求体限制只有 Content-Length 预检，没有按实际接收字节累计；无该头的请求可能绕过。中间件注释仍写 8 MB，而配置真实默认 50 MiB，容易误导运维。
2. `/upload` 是 async 路由，却直接执行同步 CPU/文件/子进程/数据库流程；该 worker 的事件循环会被阻塞。
3. `await file.read()` 全量读取；服务再次解码分行并在子进程重复解析。分析器虽逐行读文件，火焰图降采样却放在**每文件结束后**，单个大文件的中间列表没有 50000 点的实时上限。
4. 子进程命令使用裸 `python` 而非当前解释器；120 秒固定超时；仅凭 HTML 文件存在判成功，没有要求 returncode=0 及完整结果。
5. 错误出口有 `str(e)`，前端只有 `d.detail` 且无条件 resp.json()。应用 413 返回的 `message`、代理 HTML 413/504 都可能变成无帮助的错误提示。
6. 数据库保存整份 report_html，应按**实际编码后的 HTML/SQL 报文**控制，不能以原日志大小推断字段或报文必然超限。

### 6.2 选定方案与兼容边界

本版维持 `POST /api/v1/gateway-log/upload` 等待结果、成功返回 report_id 的调用方式；不把 200 悄悄改成 202，不在 FastAPI BackgroundTasks 里假装持久任务。大文件性能目标以 §8 的实测门禁验收，不承诺任意大小和任意硬件。

```text
身份校验/请求上下文
    → 专属请求体字节限额 + 单机跨 worker 上传槽
    → UploadFile 磁盘暂存 → 有界分块转交独立临时目录
    → 非事件循环线程协调受控子进程
    → 单次流式解析：质量指标 + 分析报告 + summary.json
    → 校验退出码/覆盖率/产物大小/数据库报文
    → 事务落库 → 返回 report_id
任一失败 → 明确错误码、进程回收、关闭 UploadFile、清理本请求目录/释放槽位
```

暂存、分析与报告只是本机任务资源，不接触业务实例数据写入。网关选择的 connection_id 仅表示用户申明日志来源，标题补“上传日志关联实例”；若文件可识别端口与所选连接端口不符，受理前提示来源不一致并拒绝（422），不能静默改成所选端口。非标准文件名允许按既有规则规范化，但标注“来源由上传者关联，未从文件名验证”。

### 6.3 大小、时间及资源配置

新增配置由后端统一读取，前端通过新只读 `GET /api/v1/gateway-log/capabilities` 获取；其鉴权/RBAC 与网关列表读取一致。数值启动时校验，非法值失败并指明配置名；网关限额不允许用 0 关闭。非网关原有配置保持语义。

|配置|默认值|执行位置|
|---|---|---|
|GATEWAY_UPLOAD_MAX_BYTES|209715200（200 MiB 文件净字节）|前端提示＋后端文件计数；MIME 不能作为真实性依据|
|GATEWAY_REQUEST_MAX_BYTES|210763776（201 MiB，含 multipart）|网关专属 ASGI 接收计数；部署 Nginx 同步 `201m`|
|GATEWAY_UPLOAD_RECEIVE_TIMEOUT_SECONDS|300|应用接收请求体总时限；不含 Nginx 已缓冲在前面的时间|
|GATEWAY_ANALYSIS_TIMEOUT_SECONDS|540|子进程从启动至分析和报告输出完毕的墙钟限制|
|GATEWAY_PROCESSING_BUDGET_SECONDS|600|文件落盘开始至持久化的协调软预算，每阶段启动前检查；不是数据库 I/O 硬上界|
|GATEWAY_MAX_CONCURRENT|1|单机所有应用 worker 共用槽；默认无排队，忙时 429|
|GATEWAY_MIN_FREE_BYTES|2147483648（2 GiB）|应用暂存卷受理前可用空间；Nginx 暂存卷运维独立检查|
|GATEWAY_MAX_LINE_BYTES|1048576（1 MiB）|逐物理行硬上限；超长输入明确 422，不静默截断|
|GATEWAY_REPORT_MAX_BYTES|25165824（24 MiB UTF-8）|子进程输出/服务读取前及入库前校验|
|GATEWAY_FLAME_POINTS|10000|Web 火焰图最多保留点数；显示“可视化抽样”，不抽样 KPI|
|GATEWAY_TMP_DIR|独立的应用可写临时目录|生产明确配置，Windows 测试用独立临时子目录；绝不放 Web 静态目录|

请求净文件限额和 multipart 总限额必须分别判断。即使 200 MiB 文件本身合规，异常多字段使总请求超过 201 MiB，也应拒绝。限定仅一个 file 字段及 connection_id/log_type 等预期表单字段；限制字段个数/长度，多文件、重复关键字段、压缩包/不支持 Content-Encoding 明确 422/415，不能自动展开归档。

临时目录仅运行账号可访问，文件名由服务生成而非用户路径；保留原文件名仅供安全显示。每请求最多同时存在 UploadFile spool、受控输入文件、报告/小摘要；不能生成第三份完整日志。记录实际磁盘峰值，不以“有 2 GiB 空闲”冒充并发无限。

### 6.4 请求限制与前端

新增 `GatewayUploadPolicyMiddleware`（纯 ASGI）只匹配规范路径和 POST 方法。`BodySizeLimitMiddleware` 对该精确路由让专属策略决定，其他路由继续使用原 max_body_bytes（默认 50 MiB），不因新增网关参数放开全站。

处理要求：

1. 请求上下文最外侧保证 413/429 也有 X-Request-ID；Auth 在表单解析和重工作之前执行。调整注册顺序时须验证实际执行顺序，不能仅修改注释。跨域/安全头行为保持。
2. 对可信与不可信 Content-Length 都先作格式/上限检查，再包装 ASGI receive 累计所有 http.request body 字节；无头/分块/伪造偏小值同样不能越界。超限停止后续读取和表单解析，不返回 500、不创建报告。
3. 收体期间共用跨 worker 非阻塞槽；Linux 使用同一临时根的 advisory file lock，Windows 使用等价文件锁；进程退出由 OS 释放。锁文件不删除再重建，避免 inode 变化制造两个锁；不是进程内 Semaphore。多主机部署为每主机一槽并标明总容量，不宣称全局只有一槽。
4. 将取消/超限信号贯通 receive、multipart parser 与路由；部分暂存文件必须 close，不能只清理进入 analyze_log 后的文件。前导过滤拒绝请求也要释放槽。运行中不得用线程超时退出代替实际取消子进程。
5. capabilities 返回文件上限、请求上限、支持类型、处理中可能耗时和配置版本；前端只作友好预检，后端仍是权威。
6. `app.js::onGatewayUpload` 把上传选定的 connection_id 固定在局部上下文；结果归属该实例。分析期间禁重复上传；切实例不能把 A 的成功提示/历史列表覆盖到 B。与加载历史分开维护上传 loading，finally 检查请求序号。
7. 前端按 status 和 content-type 解析：复用已有 `responseMessage`，兼容 `{detail:string}`、`{detail:{message}}`、`{message}`、新结构；HTML 413/504 用固定中文提示，不把整个 HTML 渲染到页面。现有 `apiFetch` 会对所有 5xx 先弹通用通知，须增加调用级 `handledHttpError` 选项（默认 false，传给 fetch 前从 options 删除）；仅网关上传设置 true 并自己展示一次精确提示，不能关闭全局 401 处理或其他模块错误提示。显示请求编号；网络断开提示“结果尚未确认，请查历史”，不能断言服务没有处理。
8. 不展示虚假的分析百分比。fetch 不能准确报告文件进度时显示“正在上传并分析，请勿重复提交”；完成后刷新**原实例**历史，成功/partial 文案保持区分。若增加浏览器总等待保护，默认 990 秒，并明确计时含上传；中止后先查询历史，不自动重传。

### 6.5 解析、子进程与产物

`gateway_log_service.analyze_log` 改为接受受控文件路径及 ReportContext，不再接受完整 bytes。API 用 1 MiB 块将 UploadFile 转交受控目录，累计净文件字节并计算 SHA-256；同步文件/数据库与进程等待移到线程池，主事件循环可继续处理登录、列表等请求。

分析器增加标准库实现的共享逐行输入层（建议 `gateway_log_analysis/log_input.py`），由 interf/sql 两种 Web 日志消费；合并原快速统计与报告输入的有效行判据。新 `--summary-output`、`--context-file` 参数分别输出受控摘要/读取平台上下文，平台调用不要再预统计一遍：

- 增量处理 UTF-8（可选 BOM）、LF/CRLF，最后一行没有换行也计一行；不得 errors='ignore' 静默丢字节。非法编码记录 encoding_error 行、列入跳过覆盖率，保留最多 5 条经过脱敏的原因样例；超长单行在缓冲超过上限时立即拒绝，不能先 read 整行再检查。
- 统一有效行要求：匹配既有日志头、对应日志类型具有 timecost、数值有限且非负。NaN/Infinity/负耗时不进入均值和直方图。不得为了性能绕过现有 SQL 脱敏函数。
- `total_lines=empty_lines+nonempty_lines`；`nonempty_lines=parsed_lines+skipped_lines`；各跳过原因互斥且相加等于 skipped_lines。所有全量 KPI、报告总查询数与 summary 使用同一批 parsed 行。
- `parsed_lines=0` 拒绝；跳过比例严格大于现有 GATEWAY_MAX_SKIP_RATIO（默认 0.5）拒绝；等于阈值且有有效行仍 partial。其余有跳过的输入保留醒目 partial/覆盖率告警。源格式不支持不是“健康零查询”。
- 火焰图使用逐行有界 reservoir sampling（局部固定种子、最多配置点数），最后按时间排序；测试固定种子，验证点来自全时段。不能在文件末尾才降采样或单纯截前 N 条。显示原始有效行数、展示点数和抽样说明，不把可视化抽样记为输入丢失。
- 模式计数沿用有界策略，达到 distinct-key 上限时必须输出 `analysis_truncated=true` 和受影响指标说明，不能静默丢新 pattern 后宣称精确 Top N。完整 KPI 不受影响；输入覆盖率与分析维度截断分别标识。脱敏缓存如使用，最多 4096 项，原始 key 超 4 KiB 不入缓存，避免拿整份大日志做缓存键。
- 同一输入只作一次语义解析，不更改日志格式正则的含义；解析性能优化以缓存、有界数据结构、复用结果为主。SQL 中复杂分隔符的格式支持沿用基线，对未知格式明确质量告警，不凭没有原始日志扩写一个未证实的新协议。

子进程通过 `sys.executable` 与受控 argv 启动，不用 shell；保留必要的 repo PYTHONPATH 和运行环境。stdout/stderr 分开连续排空，内存只留各 64 KiB 尾部；若落盘则各最多 1 MiB，不能 capture_output 无限累积。

新增 `backend/services/gateway_process.py::run_analysis_process` 封装本模块生命周期。Linux 独立进程组，超时/取消按 TERM→等候最多 5 秒→仍存活则 KILL→wait/reap；Windows 使用标准库 ctypes 管理 Job Object（KILL_ON_JOB_CLOSE）约束子树，不能声称 `CREATE_NEW_PROCESS_GROUP` 本身能杀子孙进程。不要修改此前已验收的其他模块进程退出行为。记录 pid、returncode、timed_out、exit_after_term、forced_kill 和清理结果；只有确认退出后才能删其目录、释放分析资源。关机/worker 异常退出须由服务进程组清理兜底。Windows 没有 POSIX TERM 等价证据时记录 not_applicable，不能伪造 exit_after_term=true。

生成 `report.html.tmp` 与 `summary.json.tmp` 完成后原子改名；成功必须同时满足：退出码 0、摘要协议版本正确、输入 hash/净字节数一致、有效记录非零且覆盖阈值通过、报告非空且有完整文档结束标记/规定根元素、报告 UTF-8 字节数不超过上限。非零退出但留有 HTML 文件不能记成功；summary/HTML 不一致也不能落库。

摘要建议结构（所有计数示例仅为协议演示）：

```json
{
  "version": 1,
  "input_sha256": "<64 hex>",
  "input_bytes": 74560116,
  "status": "success",
  "parse_quality": {
    "total_lines": 125398, "empty_lines": 0, "nonempty_lines": 125398,
    "parsed_lines": 125398, "skipped_lines": 0, "coverage_ratio": 1.0
  },
  "metrics": {"total_queries": 125398, "slow_queries": 10, "max_time_ms": 2000, "avg_time_ms": 3.5},
  "visualization": {"flame_points": 10000, "sampled": true},
  "analysis_truncated": false,
  "warnings": []
}
```

### 6.6 数据库、错误与诊断

保留 gateway_log_reports.report_html 的 LONGTEXT 类型，不执行“改成 MEDIUMTEXT”或无依据扩容。新增 `backend/schema/v14/142_gateway_analysis_meta.sql`：

```sql
ALTER TABLE gateway_log_reports ADD COLUMN analysis_meta_json MEDIUMTEXT NULL DEFAULT NULL;
ALTER TABLE gateway_log_reports ADD COLUMN request_id VARCHAR(64) NULL DEFAULT NULL;
```

analysis_meta_json 保存解析质量、分析/图形截断、文件 hash/字节数、阶段耗时、分析器版本，限 128 KiB；不存原始日志、不存未经脱敏的异常样例。request_id 用于响应不确定时人工查历史和日志，不能作为身份凭证。report_context_json 由 140 迁移添加，不重复。

保存前在**元数据库**读取 session max_allowed_packet，与客户端/代理限制共同核对；不是去业务 TDSQL 改参数。按驱动最终 mogrify/编码后的 INSERT 字节长度＋协议余量检查，或采用可证明安全的转义上界估算；不能只比较 Python 字符数。默认 24 MiB 报告上限下建议元数据库链路具备至少 64 MiB 有效报文能力，并以带引号/反斜杠/中文的最大产物实测。权限/参数不足返回可操作错误，应用不得 SET GLOBAL。

报告 HTML、上下文、摘要、request_id 在同一事务插入；任何失败 rollback，不留下“成功但无正文”的历史记录。不得返回 200 后异步补报告。提交时断网存在结果不确定性，响应说明按 request_id 查历史，不能保证绝无落库或自动重试插入。

新结构化错误约定：

```json
{"detail":{"code":"GATEWAY_ANALYSIS_TIMEOUT","message":"日志分析超过允许时长，请联系管理员核查资源。","stage":"analyze","request_id":"...","retryable":true}}
```

|HTTP/错误码|场景|用户处理与服务行为|
|---|---|---|
|413 GATEWAY_UPLOAD_TOO_LARGE|文件或 multipart 总体超限|显示各自上限/实际已知字节数；不启动分析|
|408 GATEWAY_UPLOAD_TIMEOUT|应用收体超时|提示上传未完成；关闭部分文件|
|429 GATEWAY_BUSY|共享槽被占用|Retry-After，稍后重试；不排队无界存文件|
|422 GATEWAY_INVALID_LOG / SOURCE_MISMATCH|零有效行、覆盖率过低、长行、来源端口冲突|展示脱敏原因/覆盖率；不写成功报告|
|504 GATEWAY_ANALYSIS_TIMEOUT|子进程时限|先回收进程，再返回可读错误；不将残留 HTML 当成功|
|507 GATEWAY_TEMP_SPACE_LOW|磁盘不足/写入 ENOSPC|提示暂存空间；清理本次目录，不动其他任务|
|503 GATEWAY_REPORT_STORAGE_LIMIT|有效报文能力不足|给元数据库参数核查指引，不建议业务库全局修改|
|500 GATEWAY_ANALYZER_FAILED / OUTPUT_INVALID / STORAGE_FAILED|进程异常、产物不完整、其他落库错误|客户端仅固定文案＋请求号，异常栈只入受控服务日志|

阶段日志固定带 request_id、connection_id、净文件 bytes/hash、stage、duration_ms、returncode、报告字节、清理状态；不得记录密码、令牌、完整 argv 中的凭证或完整日志内容。应用自己返回的所有错误都应有请求号；代理在应用之前拒绝时可显示代理日志关联号或“请求未到达应用”，不能凭空造应用请求号。

### 6.7 Nginx 和部署改动

只为 `location = /api/v1/gateway-log/upload` 设置 `client_max_body_size 201m`、`proxy_read_timeout 660s`、`proxy_send_timeout 660s`、`client_body_timeout 60s`，复制现有 Host/X-Real-IP/X-Forwarded-For/Proto 透传；保留其他 location 的 20m/120s 以及 /metrics 访问控制。不全局替换配置值，不放宽认证，不修改公网暴露范围。

保留默认 request buffering；这会使用 Nginx 自身暂存空间，必须和应用卷分别核查。client_body_timeout 是相邻读间隔，不能说它保证完整上传 60 秒结束。应用的 300 秒收体时限不包含 Nginx 预先缓冲的时间，660 秒代理等待也不是全链路 SLA。负载均衡/安全网关存在更短超时的，要在部署路径清单中同步核验；不允许验收时只走直连逃过正式入口。

直接 8000 部署仍由应用专属字节限额保护，不能依赖 Nginx。先 `nginx -T` 检查生效 server/location 与上游路径，再 `nginx -t`，批准后 reload；`curl -I` 的响应 Content-Length 不能证明上传限额已修改。

内网事故最终归因回填表至少包含：请求时间、入口 URL、浏览器 HTTP 状态/响应摘要、X-Request-ID、有效 max_body_bytes/网关新限额、是否到达 upload/analyze 阶段、代理 upstream_status/request_time、异常类型、子进程退出信息、元数据库 max_allowed_packet/报文字节数、RSS/磁盘数据。无原日志与原事故请求证据时，结论只写“已消除默认路径阻断及确认的实现缺陷”，不写“已证实原事故就是 Nginx/超时”。

## 7. 实施清单、数据库迁移与发布

### 7.1 按依赖顺序实施

|任务包|文件/函数范围|可交付验收点|
|---|---|---|
|D01 报告上下文基础|新增 report_context.py、140 迁移；audit/scan/inspection/daily/gateway 源记录写入与查询|冻结、继承、历史降级契约通过；不改变老 JSON 主体|
|D02 全部 HTML 接入|§3.1 H01—H14；scan_snapshot_service、scan_compare_service、raw_slowlog_service、bigtable_service；四个 CLI 和磁盘脚本|生成器逐项截图/解析断言；多实例可映射，无遗漏|
|D03 主表识别|新增 tdsql_table_shape.py；table_type_stats_service；141 迁移；前端即时/汇总/历史|新旧语法、未知态、原统计不变量、预算和请求归属全部通过|
|D04 R043|parser_legacy.py 的预解析/AST/回退出口；dml.py R043；checker.py 完整性兜底|附件及同源反例消误报；真实 multi 不漏报；R121 保留|
|D05 网关入口|config.py、middleware.py、main.py、gateway_log.py、app.js；新增专属策略/跨 worker 文件锁封装|直连/代理大文件可受理；实际字节保护；其他接口限额不变|
|D06 网关执行|gateway_log_service.py；新增 gateway_process.py、gateway_log_analysis/log_input.py；analyze_gateway_log.py；142 迁移|单次流式解析、质量一致、进程回收、持久化与可诊断错误|
|D07 发布与验证|VERSION、config.APP_VERSION/APP_DESCRIPTION、frontend/index.html 标题/页脚/静态资源版本；部署模板和现有增量打包入口|统一 v1.6.3.4；迁移/构建/离线安装/浏览器矩阵完成|

新 CLI 参数契约：单实例工具支持 `--connection-name`（人工标识）及 `--context-file`（结构化输入，二者互斥）；多实例 merge/interf_report_generator 支持 `--context-file` 的 `groups: {input_group_key: ReportContext}` 映射。未映射的组显式未知；平台创建文件而非传入任意客户端路径。磁盘报告脚本同名可选参数，按自身输出方式进行 HTML 转义，不能要求离线机器导入 Web 后端包。脚本现有用法与输出类型保留，新增上下文同时带入中间 JSON，避免合并时丢来源。

库迁移编号与产品版本分离：v14 是数据库 schema 序号，产品版本是 v1.6.3.4。不得把 pyproject.toml 既有独立包版本 2.0.0 无依据批量替换；发布版本以当前 VERSION/config/前端/部署说明约定对齐。

### 7.2 迁移与回退

迁移落库目标仅为 SQLCheck 自身元数据库，不是扫描的业务 TDSQL。按 loader 顺序执行 140→141→142。新增文件使用现有 SchemaMigrator 的“检查现存列→执行→验收→登记 checksum”，禁止手工吞 Duplicate column 后宣称成功。

实施步骤：

1. 备份元数据库并记录基线 VERSION、提交、迁移账本、表行数、现有结构。检查新列不存在或完全匹配；若同名异型、null/default 不符，失败关闭，交由明确的整改迁移处理。
2. 先在 v1.6.3.2 数据副本测试三份 ADD COLUMN；MySQL 5.7/8.0/MariaDB 实际元数据库类型分别验证。不能假设 ADD COLUMN 都是瞬时无锁操作；生产执行前测锁等待和表规模，安排维护窗口。
3. 无破坏性数据迁移：所有旧 report_context_json/analysis_meta_json/request_id 为 null；主表统计新数字为 null、state=LEGACY，不将未知值补零，不更新历史审核 violations。
4. 上线前检验新装、旧库升级、同版本重复启动、半途断电后重入、列漂移/缺列阻断及多 worker 同时启动。服务开始采集前完成必要结构验证，避免目标扫描 180 秒后才 INSERT 报缺列。
5. 仅新增列使旧版业务读写原则上可继续；但项目 `_ensure_schema` 的旧版字段集合验收及启动迁移行为必须在实际回退演练中验证。保留新增列和迁移账本，不 DROP COLUMN、不修改既有历史 checksum。
6. 回退应用到 v1.6.3.2 后新增主表指标不展示，新功能停止；带新字段的已生成 HTML 可保留。配置同步回退，不能让旧版无界读路径承受新 200 MiB 上限。若回退库备份会丢失上线后新数据，必须另行批准，不能当默认步骤。

### 7.3 发布包与依赖约束

本版设计不增加生产第三方依赖：复用 FastAPI/Starlette、PyMySQL、锁定 sqlglot，新增逻辑使用标准库。不为解决网关上传引入 Redis/Celery，不为 UAT 把浏览器测试框架放进 requirements.txt。

Q 交付必须包含三份 schema/v14 SQL、新增模块、变更生成器/脚本、前端静态文件、部署配置示例、升级/回退说明和变更清单。现有增量包的文件白名单必须覆盖新模块与 v14 目录；仅更新 Python 文件而漏发 SQL 视为发布阻断。

构建产物在干净环境按生产 requirements 和离线 wheel 目录完成安装验证；检查 dist/wheels_tmp 是否齐全、平台/架构匹配。单元测试通过不代表离线发布可用。安装/升级测试不能使用开发机已安装的额外依赖掩盖缺包。

## 8. SIT/UAT 验收矩阵

以下是开发后的必测用例，不是本次已执行结果。Q 提交自测证据，A 进行设计/实现审计及 SIT，O 从真实浏览器点击进行 UAT；内网样本/参数采集由具备相应权限的责任方提供，最终业务验收由 Mr.Linsang 决定。既有 v1.6.3.2 签署结果不自动迁移为 v1.6.3.4 的发布许可。

### 8.1 HTML 报告

|ID|操作|期望与证据|
|---|---|---|
|REP-01|逐一调用 H01—H14；H05/H06 展开全部四模块，H12 展开 combined/单组|每个实际输出均有首屏“实例连接名称”；保留生成文件及截图；不能只检查共用 helper|
|REP-02|在连接 A 产生结果，切换 B 再导出 A|报告仍为 A；不读取导出时的页面选择|
|REP-03|A 扫描中/结束后分别改名，再查看历史/重新导出|各新报告显示开始时名称；对比基准/目标分别显示各自名称|
|REP-04|新任务时输入不存在的 ID；扫描后删除连接|前者拒绝；后者历史仍可显示原名，不自动选择默认实例|
|REP-05|老记录无上下文；同端点有两条不同连接；无法查当前名称|按精确 ID 和证据降级；不按 host:port 首项匹配；未知不伪造|
|REP-06|不绑定实例的文件审核；独立 CLI 未传/传名称|前者清楚注明离线；后者区分人工指定与平台快照；规则架构选择不变|
|REP-07|多来源原始慢日志/合并日志；同来源 ID 后续改绑|首屏来源集合与每行/分组一致，历史事件不被改绑污染|
|REP-08|名称含中文、长文本、`& < > ' " </script>`|显示完整安全文本，无脚本执行、乱码、错位；打印页眉可见|
|REP-09|扫描快照重建和重复 upsert；日常巡检缓存|不以重建/缓存读取的现名覆盖原来源；上下文与指标批次一致|
|REP-10|旧/新网关报告签发一次性票据，跨 worker 读取、重复使用|首次有效，重放被拒；nonce/iframe 图表交互正常，添加来源不改变安全边界|
|REP-11|原始慢日志零行、超过 10000 行、多实例筛选|零行不假称无慢 SQL；展示导出覆盖范围/截断；来源名称与真实输出对应|

### 8.2 二级分区主表

|ID|测试集合|期望|
|---|---|---|
|PAR-01|官方旧 HASH+RANGE、HASH+LIST、RANGE+LIST、LIST+RANGE 样例；分布头前后两种顺序|每张逻辑主表计 1；端到端使用实际 SHOW CREATE 返回而非只拿手写输入|
|PAR-02|官方新 HASH+TDSQL_PARTITION RANGE/LIST；实际内核返回 COLUMNS 变体|能结构识别的计 1；缺乏真实样例的变体单列未验证，不伪造执行证据|
|PAR-03|普通分片、普通单表、广播、单表 PARTITION、集中式原生 SUBPARTITION|普通分片不算主表；其他均不误计；集中式显示 0（不适用）|
|PAR-04|同一主表 1/12/多分区、有/无 MAXVALUE；多 Set 物理副本|每主表均只计 1；原子表列不变；R121 不影响现存表计数|
|PAR-05|关键词仅在列名/COMMENT/字符串/普通注释；合法版本注释；未闭合文本|前者不触发；版本注释按边界处理；不完整返回 UNKNOWN|
|PAR-06|名为 orders_tdsql_subp202601 的真实逻辑表；父表存在/不存在各组合|保持原三条件子表识别；不会只因命名把逻辑表剔除或推断主表|
|PAR-07|SHOW CREATE 无权/空返回/缺列/2 MiB 超限/中途删表/冲突/未知语法|数量、checked/unknown 状态正确；不显示 OK+0 掩盖失败|
|PAR-08|0/1/5000/5001 候选，180 秒预算边界，多库先后|原有基础统计先完成；护栏/预算不足新字段显式 partial/unknown；不新增 I/O 越过 checkpoint|
|PAR-09|仅大小写不同库表、反引号及点号、含 SQL 片段的名称|精确归属、正确引用，无注入、串库或统一 lower() 合并|
|PAR-10|Proxy 三命令失败、库枚举截断、kinds overlap、基线不一致|原状态/告警/总数保持；主表不宣称全量，汇总同 eligible 集合|
|PAR-11|旧 v13 数据、集中式空库、分布式零分片|分别为 LEGACY/null、N/A/0、COMPLETE/0，不混淆|
|PAR-12|浏览器扫描 A→切 B→A 迟到；开历史→即时结果迟到|字段/标题/告警属于当前上下文；即时、汇总、历史列一致|

至少在内网实际使用的旧分区集群执行 PAR-01/04，并由 DBA 提供 SHOW CREATE 及人工清单对账；新内核若内网没有实例，则保留官方语法单测/模拟返回证据，不能标“真实新内核验收通过”。目标测试只发枚举及 SHOW 类只读 SQL，不在生产创建测试表。

### 8.3 R043

|ID|输入/入口|期望|
|---|---|---|
|DML-01|附件原文，以相同 hash 存测试 fixture；即时/文件/在线元数据三入口|sql_type=CREATE TABLE、无 R043；R036/R037/R121 与基线一致；parse_error 仍为空|
|DML-02|§5.1 最小反例、ALTER 自动更新属性、外键 ON DELETE/ON UPDATE、INSERT ON DUPLICATE KEY UPDATE|不冒充顶层联表 DML；不要求其他规则全 PASS|
|DML-03|逗号、各 JOIN、STRAIGHT_JOIN、自关联、括号表引用组 UPDATE|真正联表全部命中；不按基础表去重漏掉 self join|
|DML-04|单表 SET/WHERE 子查询含 JOIN；CTE 含 JOIN、外层单表|不因子查询误报；CTE 外层真实 multi 仍报|
|DML-05|PARTITION(p0,p1)、索引提示列表、带逗号/JOIN/SET 字样的反引号表名|不误算多表；提示规则本身保留|
|DML-06|DELETE a,b FROM；DELETE a FROM…JOIN；DELETE FROM a USING…；DELETE LOW_PRIORITY QUICK IGNORE FROM t|前三者 R043；最后单表不误报；文案显示 DELETE|
|DML-07|普通注释、`#`、合法 `-- `、字符串内伪造关键词、CRLF/LF、大小写|词法作用域一致；不靠会误删字符串的注释正则|
|DML-08|AST ParseError/Command 强制降级、token 失败、未支持目标结构|保留解析/完整性错误；不能静默给通过，也不编造联表事实|
|DML-09|同一 parser 连续解析 multi→CREATE→single→DELETE|无状态串扰；正常 DML 解析次数不增加|
|DML-10|分布式/集中式，规则启停和 override，R035/R058/R121 回归|规则范围与已签署策略不变；不是过滤导出结果“修误报”|

### 8.4 网关上传与分析

|ID|操作/故障注入|期望|
|---|---|---|
|GW-01|复现默认50 MiB旧路径；修复后直连上传原71 MiB文件|基线413可重现；新版完整成功/partial按真实质量判定，不能预设125398全为有效行|
|GW-02|同文件经部署真实 Nginx/网关入口；有效配置核验|不再被旧20m/50 MiB双重限制；完整记录HTTP/阶段/资源/产物证据|
|GW-03|净文件limit−1/limit/limit+1；multipart总limit边界；0字节|边界精确，净文件与请求体分别验收；空日志422，不产生空成功报告|
|GW-04|无Content-Length、chunked、异常Content-Length、多文件/多字段、压缩包|累计字节限额不可绕过；清理解析前暂存；不产生500或无界内存|
|GW-05|未登录/无权限上传，capabilities/历史/票据访问|继承RBAC，不因新增接口或中间件重排绕过认证|
|GW-06|代理HTML413/504、应用message/detail、网络中断|中文提示对应真实错误类型，不出现JSON解析异常或展示代理HTML|
|GW-07|2个worker同时上传；另一个用户执行列表/小SQL审核|仅一个上传/分析占槽，其余429；其他事件循环正常，无全局loading串扰|
|GW-08|原文件、两倍样本、约200 MiB合规样本；单大文件不是多个小文件|统计全量、资源有界，火焰点数中途也不超过上限，不仅结束时截断|
|GW-09|正常/混合/全垃圾，跳过比例0/0.5/大于0.5，NaN/Infinity/负数、非法UTF-8、超长行|计数恒等式、阈值/partial/编码错误一致；报告和列表指标同源|
|GW-10|子进程慢、忽略TERM、非零退出但有HTML、summary缺失/不匹配|超时回收；TERM退出和强杀可区分；不完整产物不落库|
|GW-11|客户端断开、worker退出、服务重启，磁盘满/只读/残留目录|无孤儿分析进程；已关闭资源可清理，忙任务目录不误删；锁可恢复|
|GW-12|max_allowed_packet过小、引号/反斜杠/中文大HTML、事务失败/提交结果不确定|按实际报文预检；不降低列容量、不自动改全局参数；不伪造成功/重复自动提交|
|GW-13|跨实例迟到响应、端口冲突、非标准文件名、连接改名|来源语义和页面归属正确；名称冻结；冲突422，未知来源明确|
|GW-14|日志含SQL敏感值/HTML载荷，新旧报告脱敏、一次性票据、多worker|原有脱敏/CSP/nonce/sandbox保护不退化|
|GW-15|普通SQL文件、logo等非网关接口的大小边界|仍受既有上限保护；200 MiB特权不泄漏到其他路由|
|GW-16|导出超过distinct pattern上限、火焰图采样、旧报告重新查看|全量KPI与分析维度截断区分；历史null不冒充完整新质量数据|

容量门禁：以部署同等级硬件、Python/依赖版本、worker 数和脱敏配置测量。71 MiB真实样本及200 MiB边界样本（标明合成）须在540秒子进程限时内完成；否则此需求不能记通过，应先定位 normalization/解析/渲染瓶颈或提出有证据的容量调整评审，不能只把 timeout 改无限。

应用 worker 相对空闲基线新增 RSS 目标不超过256 MiB，分析子进程峰值不超过1024 MiB；默认单槽，部署总内存须覆盖应用基线、该增量、子进程、OS及余量。每个阶段记录峰值，Windows 的 resource mock 不能作为RSS实测值，使用系统进程计数器；Linux 使用 /proc 或系统工具。50次重复小/中日志后无持续增长的临时文件、活跃子进程及文件描述符。分析期间另一用户的简单列表/即时小SQL操作连续采样20次，P95不超过空闲基线2倍或2秒中的较大者；失败时不能把“事件循环已移线程”当验收证据。

### 8.5 建议测试落点及证据要求

新增测试建议：

```text
tests/test_report_context_v1634.py
tests/test_html_report_context_v1634.py
tests/test_secondary_partition_main_v1634.py
tests/test_r043_target_scope_v1634.py
tests/test_gateway_upload_limits_v1634.py
tests/test_gateway_streaming_v1634.py
tests/test_gateway_process_v1634.py
tests/test_schema_v14.py
tests/test_v1634_browser_uat.py                  # 测试依赖，不进入生产 requirements
```

必须回归既有 `test_table_type_stats.py`、`test_g14_request_ownership_browser.py`、`test_g14_frontend_state_binding.py`、`test_scan_compare.py`、`test_rules_v1632.py`、`test_gateway_log.py`、`test_gateway_log_sql_masking.py`、`test_o15_gateway_report_security.py`、`test_security_headers.py`，并执行全量自动化；不动代码的模块按正常用户路径做简短冒烟。项目当前真实测试数量以该次运行结果记录，不能抄旧版的通过数量。

浏览器 UAT 必须从菜单选择、连接切换、文件选择、点击扫描/导出、历史查看等真实交互进入，保留页面截图、下载报告、脱敏网络状态和后端阶段日志；直接调用渲染函数不算浏览器验收。每条失败报告给出输入、步骤、期望/实际、根因文件/函数、修复要求和定点回归集。

证据分四类归档：实际内网TDSQL返回、模拟目标/合成大日志、本地单元/浏览器、未执行或待确认项。不能把模拟的SHOW CREATE结果说成真实集群验证，也不能把Mr.Linsang风险签署说成性能实测。

## 9. 交付门槛与待回填事项

开发完成的最低条件：

- D01—D07 代码/迁移/部署材料齐全，H报告清单和REQ用例逐项有结果；不允许“名称加了但历史串实例”或“主表加了但失败记零”。
- R043 原附件已消误报且 R121 保留；真实联表更新/删除不漏审；审核完整性失败不被伪装成通过。
- 原71 MiB日志在内网真实入口可完成受控分析；真实文件未取得时 GW-01/02 必须写待验证，不能以同大小随机文件替代格式与性能验收。
- 新装、增量升级、回退、生产离线依赖验证通过；业务规则总数仍121，不因修复R043新增/删除规则。
- 无未关闭阻断缺陷；如容量或新内核适用性需接受限制，必须列明边界和责任方，不能由开发自行把未测项标PASS。

待回填但不阻塞编写设计：

|事项|所需信息|影响|
|---|---|---|
|原网关事故最终归因|原请求HTTP/响应、有效应用/代理配置、阶段异常|没有则只能判定代码默认路径和实现风险，不能断言那次事故唯一原因|
|71 MiB真实文件容量验证|原文件或内网可执行测试、hash、有效/丢弃行统计、硬件/RSS/时间|属于REQ-04准出证据，不是凭设计可豁免的测试|
|目标二级分区能力清单|实际内核/Proxy版本、旧/新SHOW CREATE样本、人工主表数|新语法实机不可用时单列兼容证据限制；不影响已证实旧语法实现|
|元数据库与代理发布参数|实际版本、packet、暂存空间、超时链、进程清理配置|决定迁移窗口及容量验收，不由应用自动修改生产参数|

本设计已经完成材料核对、官方资料校验、R043原文及同源用例复现、应用默认上传限额的隔离复现。尚未开发v1.6.3.4，未执行本版SIT/UAT/性能或内网变更。提交本Rev.A供A评审、Q据此实施；若评审改变统计口径、支持容量或同步/异步接口，必须修订本设计并重新核对相关验收矩阵，不能由实现自行偏离。
