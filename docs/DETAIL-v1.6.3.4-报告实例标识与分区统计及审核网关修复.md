# v1.6.3.4 详细开发设计说明书

版本：Rev.C（第二轮评审后定点修订；Rev.B 已获有条件通过，本轮三处落文待 A 定点确认；不是开发完成或测试通过声明）

需求提出与验收方：Mr.Linsang

编制日期：2026-09-06

研究基线：`main@88954b3`，业务功能版本 v1.6.3.2；依赖基线 `sqlglot==30.14.0`。

本轮复核基线：`main@84f0ddf`；从上述研究基线到该提交仅有文档变更。本轮输入为 [A 第二轮评审报告](./REVIEW2-v1.6.3.4-报告实例标识与分区统计及审核网关修复设计第二轮评审报告-ClaudeA.md) 和 Mr.Linsang 本轮“保持 180 秒不变”的直接裁定，处理见 [Rev.C 定点修订答复](./RESPONSE2-v1.6.3.4-第二轮设计评审定点答复与RevC修订说明-O.md)。[第一轮评审](./REVIEW1-v1.6.3.4-报告实例标识与分区统计及审核网关修复设计第一轮评审报告-ClaudeA.md)及 [Rev.B 答复](./RESPONSE1-v1.6.3.4-第一轮设计评审逐项答复与RevB修订说明-O.md)保留为历史依据，不改写评审原件。

本次交付边界：只修订设计和新增评审答复文档；未修改业务代码、测试代码、配置、数据库或内网环境。

## 1. 结论与范围

|编号|本版交付|明确不做|
|---|---|---|
|REQ-01|所有实际生成的 HTML 报告显示实例来源；已绑定连接显示扫描时固化的真实名称，未绑定显示未关联，历史/多实例来源可辨认|不把连接 ID、端口、数据库名冒充连接名称；不回写历史检查结论；不为纯离线文件审核新增实例选择器|
|REQ-02|表类型统计增加“二级分区主表”；识别逻辑表的一级分布与二级分区两层结构|不把物理子表数当主表数，不改变原有三类表及总表数口径，不新建导出功能|
|REQ-03|修复 R043 的语句作用域识别，保留真实联表 UPDATE/DELETE 的拦截|不关闭/降级 R043，不豁免整条 CREATE 的其他审核，不调整已签署的 R121 策略|
|REQ-04|打通约 71 MiB 网关日志的受控上传、分析、持久化与错误展示|不以放开全站限额或无限超时为方案；本版不引入 Celery/Redis、断点续传或新的任务平台|

关键决定：

1. R043 已在本地使用附件原文复现。根因不是 TDSQL 建表语法不合法，而是通用预解析正则跨越了语句内部的字段定义。
2. 内网诊断报告漏掉应用默认 **50 MiB 请求体限制**。本地已证明相同大小声明的请求会在上传处理函数前返回 413；但没有内网请求状态码/生效配置/异常栈，不能据此宣称已还原那一次事故的唯一原因。
3. 二级分区主表在结构语义上属于分布式逻辑表，但不能假设旧 Proxy 分片命令覆盖所有新语法主表。候选需独立枚举并与三类 Proxy/逻辑基线求并集，DDL 确认数允许大于旧“分片表”计数并明确差异；新增列不参与“总表数＝单表＋广播表＋分片表”的加法，不重分类原三类统计。
4. 采用“统一报告来源上下文＋各生成器适配”，而非导出时读取页面当前选择。网关采用“路由专属限额＋流式落盘＋非事件循环执行＋受控子进程”，保持现有同步响应契约。
5. 按 A 报告 §7 转录的 Mr.Linsang 裁定落文：报告生成时已指定连接的显示其冻结名称；网关分析同一时刻只允许一个任务，不排队。429 附带建议重试间隔是 A 的实现建议，不冒充 Mr.Linsang 原话；本版单应用主机、多 worker，不能以多主机各一槽宣称全局单槽。
6. Mr.Linsang 在第二轮评审后的本轮直接裁定：表类型统计继续使用 **180 秒共享软预算**，不另给主表识别增加 300 秒，也不重置 deadline。5000 次 DDL 护栏保持不变。全量来不及判明时按事实显示 `≥N（未完成）`、已确认 0/未完成或未知，不承诺所有规模都得到精确数；该预算选择不豁免真实容量测量或完整性验收。

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
|S10|[MySQL：SHOW TABLES](https://dev.mysql.com/doc/refman/8.0/en/show-tables.html)|`SHOW FULL TABLES FROM db` 可返回名称与 Table_type，需过滤视图；账号无权限的对象可能不可见。这只核实 SQL 语法，不证明所有 TDSQL 内核的逻辑目录口径，后者须用实际返回验收|
|S11|[PyMySQL：Cursor Objects](https://pymysql.readthedocs.io/en/latest/modules/cursors.html)|SSCursor/SSDictCursor 按需读行；fetchall 会重新聚合，cursor.close 可能读完剩余结果。因此新增目录流式读取不可只把 fetchall 改名为 fetchmany，也不能以关闭游标冒充立即取消|
|S12|[MySQL 8.0：SQL Statements](https://dev.mysql.com/doc/refman/8.0/en/sql-statements.html)|核对 §5.2.1 中顶层语句头的归属；闭集只用于排除 R043 的直接 UPDATE/DELETE 对象，不表示 TDSQL 各内核均支持整条语法，也不改变项目规则和解析完整性门禁|

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

Rev.B 补充复现：调用仓库真实 `SQLParser.parse()`，分别正常解析、注入 `sqlglot.errors.ParseError`、注入 `exp.Command`，没有复制函数替代实测，也没有执行 SQL。`ALTER TABLE t ADD COLUMN a DATETIME ON UPDATE CURRENT_TIMESTAMP, ADD COLUMN b VARCHAR(20) CHARACTER SET utf8mb4` 的正常结果为 `ALTER/multi=true`，两条回退路径均为 `UPDATE/multi=true`；只有 ParseError 路径的 parse_error 非空。真实 JOIN UPDATE 与多表形式 DELETE 在三路径均为对应 DML/multi=true。因此 Rev.A 的 `parsed.sql_type` 防线不成立；新增事实提取器与 R043 必须完全脱离这个旧分类字段。完整输入/结果在修订答复中留档；这是未修复基线证据，不是 Rev.B 实现测试通过。

Rev.C 补充复现：以锁定 sqlglot 30.14.0、真实 SQLParser/RuleChecker 只读运行 A 建议的 36 个闭集语句头代表样本。RENAME/OPTIMIZE/CALL/UNLOCK 等为 Command 且原无 E999；LOCK/GRANT 也走 Command，分别保留 R046、R051/R074。REPAIR/CHECK/HANDLER/DEALLOCATE 本来就有 E999，不能因加入闭集而消除。另核对词法器：`LOCK TABLES` 是单个 COMMAND token，不能拿整个 token.text 与单词 LOCK 比较；FLUSH/SAVEPOINT/RESET 的示例顶层为通用 Alias，不能仅因“不是 Command”便把任何表达式当可靠非 DML AST。具体输入/结果见 Rev.C 答复；尚无修复后结果或内网容量实测。

## 3. REQ-01：所有 HTML 报告添加连接名称

### 3.1 全量生成入口清单

开发和测试须按本清单逐行销项，不能用“公共方法单测通过”代替实际报告校验。

实例名称显示通则：报告生成时已指定实例连接的出口，显示提取/扫描开始时冻结的真实连接名称；未指定的显示“未关联实例（场景）”。当前离线文件审核前端不指定连接，本版不增加选择器；但现有 `/audit/file` API 允许显式 connection_id，不能把该路由的所有调用都误判成离线。人工 CLI 名称按 manual 标明，历史记录按 §3.2 降级，不伪造扫描时名称。

|ID|入口/生成位置|来源与改动|
|---|---|---|
|H01|`backend/api/sql_audit.py::export_file_report_html`，`/api/v1/audit/file-reports/{id}/html`|audit_history；前端未绑定连接时显示未关联；已有 API 显式绑定调用按冻结名称处理，不新增选择器、不改变其既有门禁语义|
|H02|同文件 `export_extracted_report_html`，`/api/v1/audit/report/{id}/html`|audit_history；extract_and_audit 在元数据提取前、与 scan_started_at 同一开始阶段，从 conn_info 冻结名称；新报告导出只读快照，不反查现名、不从文件名/SQL 注释推断；保持 evaluate_gate=False|
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

H02 的连接本就必填：复用 `extract_and_audit` 提取前取得的 `conn_info`，不在生成 HTML 时再次查连接。调用 `audit_file_content` 保持不传 `evaluate_gate`（默认 False），第三返回值 `gate_result=None`；`audit_history` 实际对应列为 `gate_passed`，本路径仍为 NULL，报告不新增门禁结论。不要把不存在的 `gate_result` 数据库列写进迁移或测试。

H01 API 已有 `evaluate_gate=bool(request.connection_id)`，名称接入不得更改这一行为；未绑定前端仍离线、无门禁，既有显式绑定 API 仍有原本的门禁评估。未来若另增“仅用于报告标识”的离线关联能力，应单独评审并使用独立字段（如 report_connection_id），不得复用审核身份字段而静默启用门禁；本版不实施该扩展。新报告禁止现名反查与旧记录显式标注 current_lookup 的降级是两条不同契约。

新装基表、数据库访问显式 SELECT 列、INSERT 参数数、返回模型、快照大小统计和保留/清理代码须同步核对。旧 `results_json`/`snapshot_json` 的主体类型不变，不能把原数组改成对象导致旧读端失效。

### 3.4 HTML 与安全约束

统一页眉标签用“实例连接名称”，在标题下、首个指标区之前；长名自动换行，打印可见。多实例用名称＋ID 的来源表及角色列，明细能对应。

所有新增名称、库名、角色说明按 HTML 文本上下文转义（包括引号）；JS 中需要的数据使用 JSON 安全序列化，转义 `< > & U+2028 U+2029`，禁止直接拼接到 `<script>`、onclick 或 URL。独立脚本也要覆盖 `</script>`、反引号、中文与换行测试。

新网关模板预留唯一 `data-report-context-version="1"` 来源区。旧 report_html 在 `<body>` 开始处或旧模板明确的 container 锚点补块一次；无 body 的历史片段用安全外层文档容纳，不能全局 replace 任意文本。仍由既有 `_strip_inline_handlers` 和 nonce 逻辑处理响应，保留 90 秒一次性共享票据及不透明源 sandbox。不得为显示名称放松 CSP。

H07 与 H08 分别保护，不合并安全链：H07 的 `daily_inspect.py::_REPORT_CSP`/`compare_html` 生成每响应 nonce，传入 `generate_comparison_html_report(script_nonce=nonce)`；它没有 H08 的网关票据、处理内联事件的函数或不透明源 iframe。保留 H07 原策略项、响应头和脚本 nonce 传递；原策略已有 unsafe-inline/unsafe-eval 文本，不以本次名称改动新增或扩大这些权限，也不借机改写既有安全策略。H08 则继续自己的票据、nonce、清理及 iframe 链。验收先将 CSP 中随机 nonce 归一为占位符再逐项/逐字比较策略，另断言每次响应 nonce 更新、与文档受授权脚本一致及危险名称不执行；不能要求两次真实 CSP 字节相等，更不能为满足这种断言复用 nonce。

## 4. REQ-02：“二级分区主表”统计

### 4.1 定义与不变量

在分布式实例、当前可见业务库范围内，一个由 Proxy 暴露的逻辑表，同时具备一级数据分布和二级 RANGE/LIST 分区结构，计为一张二级分区主表。主表有多少范围分区、有无 MAXVALUE，不改变“计一张”。有 MAXVALUE 的真实表依然要计入统计，R121 是否允许创建是另一件事。

```text
总表数 = 单表 + 广播表 + 分片表                         （维持原值）
0 <= 二级分区主表已确认数 <= 判明候选数 <= 已知候选数   （同一可用库范围）
二级分区主表不再加到总表数，也不从分片表数中扣除
二级分区子表 = 现有 information_schema + 命名/父表/Proxy 三条件口径（不变）
```

“结构上属于分布表”不等于“必然被旧 with shardkey 命令列出”。新语法主表可能落入旧单表结果，也可能仅在逻辑目录/基线可见；DDL 确认的主表不能被旧分片数封顶。此类正例照计，并显示 `MAIN_OUTSIDE_PROXY_SHARD` 与数量；不调整原 single/broadcast/shard/total/baseline/subpartition 数值，不暗示旧三分类也已完成新内核适配。

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

1. 只处理原有 eligible 库；failed/skipped 库的新列为 UNKNOWN，不记零。在原基础采集完成后，对每个可用库通过同一 Proxy 只读连接执行一次 `SHOW FULL TABLES FROM <quoted_db>`，按第二列 Table_type 仅保留 BASE TABLE 得集合 L；按列语义取值，不硬编码随库名变化的第一列名。视图不进入 L。这个独立枚举用于补齐旧命令可能未覆盖的新语法逻辑表；S10 验证语法，TDSQL 的版本/返回行为必须按 §8.2 实机核验，不能假定所有版本都支持。
2. P 为原三类 Proxy 结果的完整并集（保留 kinds_seen，不只是最终分片集合 S），B 为现有 `_classify_subpartitions` 返回的逻辑基线，X 为该方法原来确认的物理子表集合。候选 `C = L ∪ P ∪ B`，等价于补入 only_base=B−P 且覆盖旧单表/广播结果；按精确 `(库名,表名)` 去重，记录 L/P/B 来源。L 不可用时仍可处理 P∪B 得到下界，但独立枚举状态为 FAILED，绝不因此宣称 COMPLETE。不能只补 only_base：在 P 的单表集合中的新主表不属于 only_base。
3. 对 C 逐表执行 `SHOW CREATE TABLE <quoted_db>.<quoted_table>`，从 Proxy 返回 DDL 判两层结构，不直连 Set、不从物理子表数量反推。不得将原始 information_schema 物理子表全集重新加入候选；若 L 意外列出 X 中的对象，它仍进入 C 核验：DDL 为明确无一级分布的物理表可判 NOT_SECONDARY，DDL 却带完整分布结构则记 `LOGICAL_PHYSICAL_CONFLICT/UNKNOWN`，不能重复计主表，也不能单凭后缀认定正例/负例。命名像物理子表但实际被 P 作为逻辑表返回的，不在 X 中，按正常 DDL 判断。
4. 库名/表名分别反引号转义（反引号加倍），不能值占位符代替标识符，不能拼接未转义名称。同请求每候选最多一次 SHOW CREATE，不做持久缓存。单实例串行，沿用 registry.scan_slot，不创建每表线程。独立目录枚举和每次连接/命令之前均检查原 deadline；按 Mr.Linsang 本轮裁定，`TOTAL_BUDGET_SECONDS=180` 不变，仍由 run_stats 取得槽位后、首次目标查询前建立并跨基础/目录/DDL 阶段透传，不在进入主表阶段、换库或换层时重置。原 schema 验收/最终元库落库不在该采集预算内，不把它说成完整 HTTP 请求的 180 秒硬上界。
5. `MAX_PARENT_DDL_PER_RUN=5000` 不变，作用于整个 C 的 DDL 尝试，不只分片表；处理顺序改为下述四层，层内按精确 `(库名,表名)` 稳定排序。达到护栏/预算只停止新指标识别，保留原基础统计；未尝试项计 unchecked。新增目录行护栏 `MAX_PARENT_DIRECTORY_ROWS_PER_RUN=50000`（全请求、视图行也占额度），使用独立临时物理连接的 SSDictCursor 每批至多 500 行读取，不调用 fetchall；原基础统计没有该行数护栏，不假称复用已有能力。达到额度后最多多读一行确认是否截断；截断/预算耗尽时关闭该独立连接再结束游标，防止 close 隐式排空所有剩余行（S11），不把未读完的连接返共享池。inventory=PARTIAL，未见部分不伪造进 candidates；新枚举内存护栏不代表改造了原基础全量读取。每条 DDL 最多处理 2 MiB，超限 UNKNOWN，不能截断后当普通表；read_timeout 不大于原 30 秒与剩余软预算，仍只是 socket 空闲超时。
6. SHOW CREATE 无权、空返回/缺列、扫描中删除/改名、词法失败或结构不支持均记 unknown，不计 confirmed_negative。可继续的错误继续下一表，坏连接关闭重建而非循环重试。原 KIND_OVERLAP、命名归属歧义保留并使相关对象 UNKNOWN；DDL 的广播事实与 Proxy 分片事实矛盾、Proxy 广播标记与 DDL 正例矛盾也如此。唯有“完整两层 DDL，但旧 Proxy 未列入 S”本身不否定正例（尤其仅在旧单表/only_base/L 中的现代语法），照计 main/outside_shard 并告警。
7. 独立目录成功、结构有效且未截断，原相关枚举未失败/截断/归属不清，且 C 中的非物理对象均被 L 覆盖时，inventory=COMPLETE。L 与 P/B 仅分类不同不算漏枚举；L 缺少 P/B 的已知逻辑对象则 inventory=PARTIAL，保留 `INVENTORY_MISMATCH` 样本。库枚举截断、失败库、物理/逻辑冲突均禁止实例级 COMPLETE。原 baseline 与 Proxy 的数量差异告警原样保留，但经独立目录和完整 DDL 明确解释的 only_base 正例不必永久禁止新指标 COMPLETE；新指标完整不等于旧总数已校正。
8. 所有计数描述“当前账号可见业务库范围、多条只读查询采集”，不是事务级一致性快照。账号不可见对象不能自动推算；不宣称物理集群全量。独立枚举不支持时不回退为“零主表”，而是下界/未知并列明不支持原因。

候选调度（全实例同一 eligible 范围，先完成能在预算内完成的目录阶段，再形成已知 C；目录未取得仍使用已知 P/B 下界，不能伪称目录完整）：

|层|集合定义|意图|
|---|---|---|
|C1|`C ∩ S`|先检查旧最终分片集合|
|C2|`(C ∩ (B − P)) − C1`|再检查仅逻辑基线可见的候选|
|C3|`(L − (P ∪ B)) − (C1 ∪ C2)`|再检查仅独立目录可见的候选|
|C4|`C − (C1 ∪ C2 ∪ C3)`|最后检查剩余旧单表/广播候选；以差集定义兜住未来归一化边界|

四层互斥且并集必须等于 C，全实例按 C1→C2→C3→C4 调度，不按“先把某库四层全部扫完再去下库”。维护已尝试集合保证跨来源/层不重复查，同一输入与同一预算 checkpoint 注入得到同一顺序；真实网络时延变化仍可能改变截断点。发生 KIND_OVERLAP/物理冲突时沿用 §4.3 的 UNKNOWN 判据，不因排在前面就计正例。

分层是利用旧分片标签的启发式优先级，不是已实测的命中概率模型；不能保证现代语法偏多的环境中也得到最紧下界。它只改变顺序，绝不改变 C、计数公式、unknown/unchecked 或 COMPLETE 条件。若基础/目录已耗尽 180 秒，就不再启动任何 SHOW CREATE；已有基础结果保留，新列 UNKNOWN 或按已判明事实 PARTIAL；原入口本已失败时保持原错误，不为新指标合成“成功”记录。已知 C 超 5000 或预算耗尽均诚实展示未完成，不能缩小 candidates 到已查部分、把后两层当负例或自动续跑拼成同一次完整快照。

容量评估须按 §8.2.1 用实际候选并集与分层抽样做；本轮没有内网测量数据，不能断言“必然查不完”或“180 秒足够”。维持 180 秒已定，不再以容量核算为由默认延长预算。

### 4.4 API、持久化、前端

原 POST `/api/v1/table-type-stats/run`、history/detail 路径不变。头部与逐库 item 新增：

|字段|类型/存储|语义|
|---|---|---|
|secondary_partition_main_tables|INT NULL DEFAULT NULL|已确认主表数；旧记录/基础不可用为 null；0 必须结合状态解读，只有 COMPLETE/0 才表示范围内确认无主表|
|secondary_partition_check_state|VARCHAR(24) NOT NULL DEFAULT 'LEGACY'|COMPLETE、PARTIAL、UNKNOWN、NOT_APPLICABLE、LEGACY|
|secondary_partition_candidates|INT NULL DEFAULT NULL|已知 C 的去重候选数；枚举不完整时只是已知范围，不等于分片数|
|secondary_partition_checked|INT NULL DEFAULT NULL|已经得到 SECONDARY/NOT_SECONDARY 确定结论的候选数|
|secondary_partition_unknown|INT NULL DEFAULT NULL|已尝试但无法判断的数|
|secondary_partition_unchecked|INT NULL DEFAULT NULL|预算/护栏停止后未尝试的数|
|secondary_partition_inventory_state|VARCHAR(24) NOT NULL DEFAULT 'LEGACY'|COMPLETE、PARTIAL、FAILED、NOT_APPLICABLE、LEGACY；独立目录覆盖状态，与 DDL 判明状态分开|
|secondary_partition_outside_shard|INT NULL DEFAULT NULL|main 中不在旧最终分片集合 S 的已确认数；不是额外主表，不能再次相加|

新增 `backend/schema/v14/141_secondary_partition_main.sql`，对 `table_type_stat` 和 `table_type_stat_item` 分别按上表执行八条独立 ADD COLUMN（共十六条）；不修改 v13 历史迁移及校验和。同步 `_STAT_CONTRACT/_ITEM_CONTRACT`、派生 `_STAT_COLUMNS/_ITEM_COLUMNS`、INSERT、history/detail SELECT 和启动结构校验。Rev.A 尚未实施迁移，本次是待开发文件规格修订，不改动已发布账本。

计数验收公式：`candidates = checked + unknown + unchecked`，`outside_shard <= main <= checked <= candidates`；confirmed negative = checked-main。该公式只针对已知 C，未成功枚举的部分不伪造候选数。原基础不可用的新数字为 null；有已知候选但尚未确认正例的内部已确认数可为 0，前端仍必须连状态显示，不能当作全量零。

逐库 check_state：只有基础可用、inventory=COMPLETE、C 全判明且无相关元数据冲突才 COMPLETE；C 为空时也必须先满足完整枚举，不能以“零分片”直接判 COMPLETE/0。未达完整但 checked>0 则 PARTIAL；checked=0 且不能证明完整零则 UNKNOWN。集中式两种 state 均为 NOT_APPLICABLE、各数字为 0；历史两种 state 均为 LEGACY、各数字为 null。

实例汇总使用同一旧 eligible 集合，各数字只加其中已知值；基础全失败为 null。全部目标库 COMPLETE 才能汇总 COMPLETE；存在已判明结果但有失败/跳过/缺库/未判明则 PARTIAL，否则 UNKNOWN。inventory 汇总：全部目标库目录完整才 COMPLETE，部分目录可用或枚举截断为 PARTIAL，全部不可用为 FAILED；没有目标库且库枚举完整可 COMPLETE/0。缺库要告警，不能以 null 被 SUM 忽略就冒充全量。

示例：旧分片 99，全来源候选 216，确认 4 张主表（其中 1 张不在旧分片集合）；查明 207，错误 2，未查 7：

```json
{
  "secondary_partition_main_tables": 4,
  "secondary_partition_check_state": "PARTIAL",
  "secondary_partition_candidates": 216,
  "secondary_partition_checked": 207,
  "secondary_partition_unknown": 2,
  "secondary_partition_unchecked": 7,
  "secondary_partition_inventory_state": "COMPLETE",
  "secondary_partition_outside_shard": 1
}
```

页面显示 `≥4（未完成）`，说明显示“目录完整；判明 207/216，失败 2，未检查 7；其中 1 张未列入旧 Proxy 分片结果，原三类统计未调整”。inventory 不完整时显式加“候选目录不完整，数量为已知范围”。UNKNOWN 显示 `—（未知）`；LEGACY 显示 `—（历史未采集）`；不能通过 `value || 0` 抹掉 null。partial 已确认数为 0 时显示“已确认 0，未完成”，不能只显示 0；基础原状态 OK 与主表 UNKNOWN 必须分开显示。

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

新增内部结构 `dml_target` 作为唯一事实源，不新增规则编号。`ParsedSQL.is_multi_table_update` 改为只读派生属性（其历史语义也包含 DELETE），不保留第二个可写布尔状态：

```text
statement_kind: UPDATE | DELETE | NOT_DML | UNKNOWN
status: RESOLVED | UNKNOWN | NOT_APPLICABLE
form: SINGLE | UPDATE_TABLE_REFERENCES | DELETE_TARGET_LIST | DELETE_USING | NONE
is_multi_table: bool | null
reason: stable code
```

落点与执行顺序：

1. 删除 `_regex_pre_parse` 对联表 UPDATE/DELETE 的两段事实赋值。其他已有规则预解析不借机重构。
2. 每条语句新建事实对象。正常路径在取得可靠 AST 后调用 `_extract_dml_target(ast, original_sql)`，直接由顶层 AST 类型分流，**不以 parsed.sql_type 决定是否调用或判断结果**。正常强类型 DML 只利用 AST，不重复 parse/tokenize；保持 v1.6.3.2 的解析次数性能约束。Command 以及不能证明完整语句类别的通用 Alias/Column/Literal 表达式不算可靠语句 AST，进入 §5.2.1 独立词法头判定，而非因“非 Update 节点”就直接豁免。
3. `_parse_update/_parse_delete` 不再写兼容布尔值。只读属性完整派生于 `status == RESOLVED and statement_kind in (UPDATE, DELETE) and is_multi_table is True`，无 setter；字段初始化、预解析、提前返回均不能留下旧值。R043 直接读事实对象而不是该兼容属性。
4. ParseError/Command 的每个返回出口必须填充 dml_target：用独立可靠词法头定位顶层语句，不使用 `_detect_sql_type_regex` 的结果。已确定非本规则对象返回 NOT_DML/NOT_APPLICABLE；真实 UPDATE/DELETE 才进入有限目标解析；真正无法确认的语句头/目标返回 UNKNOWN，详见下表。已有 parse_error/KFN/E999 绝不因新事实成功或 NOT_APPLICABLE 被清除。
5. R043 唯一触发条件为 `dml_target.status == RESOLVED` 且 `dml_target.statement_kind in ('UPDATE','DELETE')` 且 `dml_target.is_multi_table is True`；文案类型也从 statement_kind 取值，**不再检查 parsed.sql_type**。UNKNOWN 不编造 R043，但并入 checker 原有审核完整性失败路径（已有 E999 则合并原因，不覆盖错误文本/重复制造同因条目），不能伪装为审核通过。NOT_APPLICABLE 则不因 R043 新增 E999；其他真实解析错误照常保留。
6. R043 保持仅分布式、ERROR、启停及规则集覆盖能力；文案按真实类型显示 UPDATE 或 DELETE。其他架构的审核不得因 R043 本身新增 error；真实解析不完整的既有门禁仍照常生效。

事实状态与审核行为（以下“无新增”不等于豁免其他规则）：

|可靠证据|statement_kind/status/is_multi_table|R043 与完整性行为|
|---|---|---|
|强类型 AST 已证明不是顶层 UPDATE/DELETE；或回退词法头命中 §5.2.1 的 36 项闭集|NOT_DML / NOT_APPLICABLE / false|无 R043；本事实不新增 E999，保留原解析/完整性结果；不是全规则白名单|
|可靠 AST 或有限回退完整确认顶层单表 UPDATE/常规 DELETE|UPDATE 或 DELETE / RESOLVED / false|无 R043；不清除已有错误|
|可靠 AST 或有限回退完整确认多表 DML 形式|UPDATE 或 DELETE / RESOLVED / true|按原架构/规则启停机制报 R043；ParseError 原错误仍保留|
|可确认 UPDATE/DELETE 头但目标结构不完整或不支持|对应 DML / UNKNOWN / null|无虚构 R043；明确审核不完整|
|词法失败、可执行注释条件不明，或无法完整定位 WITH 后外层语句头|UNKNOWN / UNKNOWN / null|无虚构 R043；明确审核不完整|

WITH 不是自动 UNKNOWN，也不是见到 CTE 内 UPDATE 就确认 DML：只在有限回退能够配对完整 CTE 定义、定位外层头时按该外层分类；外层头同样应用 §5.2.1 的闭集与 UPDATE/DELETE 分流。普通注释可屏蔽，可执行版本注释只能在条件和边界可证明时解释，不能“去掉所有注释后取第一个词”。回退头不在闭集且不是 UPDATE/DELETE、或外层定位失败时，明确 UNKNOWN；不再使用开放式“等”字授权实现方自行增删。

本版不顺带重写 `_detect_sql_type_regex`，因为它还服务于其他既有流程；该字段可能保留历史类型误标，已从 R043 的事实提取、触发和文案链剥离。若记录 `DML_TYPE_DISAGREE`，只作内部诊断，不以差异本身增加用户违规。

兼容范围：当前运行时代码搜索显示旧布尔值唯一规则消费者为 R043，未发现 ParsedSQL 的 asdict 序列化出口；但“字段改 property”不保证 dataclass 构造参数/asdict 字段兼容，不宣称零影响。开发时核对全部 ParsedSQL 构造/反射/序列化和测试 fixture，移除旧字段赋值；public AuditResult 结构不变。仍须有属性真值表、连续解析隔离和消费者检索证据，只是不再需要双写同步测试。

### 5.2.1 可靠回退语句头闭集（P2-04）

将下列 **36 项**定义为单一不可变集合 `R043_NON_TARGET_HEADS`，实现与测试 fixture 必须集合全等，不按不同出口维护副本：

```text
SELECT INSERT REPLACE CREATE ALTER DROP TRUNCATE RENAME
OPTIMIZE ANALYZE REPAIR CHECK LOCK UNLOCK GRANT REVOKE
FLUSH SET SHOW DESC DESCRIBE EXPLAIN CALL HANDLER LOAD
START BEGIN COMMIT ROLLBACK SAVEPOINT USE KILL RESET
PREPARE EXECUTE DEALLOCATE
```

该命名/NOT_DML 值的含义仅是“不属于 R043 的直接顶层 UPDATE/DELETE”，不是把 INSERT/SELECT 从 SQL 的 DML 分类中剔除；也不是声明 TDSQL 支持所有 MySQL 语句，见 S12。

实现契约：

1. 每个已经按项目原逻辑拆分的实际语句独立判定，只跳过普通注释/空白；字符串或反引号标识符不能作为可靠头，也不能被跳过后继续找一个有利的关键词。不从全文搜索，不信任 Command.this 或旧 sql_type 的字串。
2. 使用保留 token 类型/原始边界的独立词法结果。sqlglot 可将 `LOCK TABLES` 合成一个 COMMAND token：只有确定该 token 是未加引号的语句头关键词片段时，取片段内第一个裸词 LOCK 再查闭集。不能仅比较整个 token.text，也不能对 STRING/IDENTIFIER 的内容作 split 后当关键字；测试覆盖复合 token 与引号伪造。原 `_lex_head_words` 丢弃了 token 类型，不能直接将其输出全信为裸头。
3. 可靠头命中闭集 → NOT_DML/NOT_APPLICABLE/false；UPDATE/DELETE → 有限目标解析；WITH → 完整定位外层后重用同一判定。其余头/空头/无法定位/词法失败 → UNKNOWN/UNKNOWN/null（原本空 SQL/纯注释的跳过机制仍在上层，不制造额外语句结果）。
4. 只证明本规则不适用，不检查整个命令是否语法正确或有执行权限，不清除 parse_error、known_fidelity_failures、KFN、E999 或任何其他规则。REPAIR/CHECK/HANDLER/DEALLOCATE 原来有 E999 的仍有；GRANT 的 R051/R074、LOCK 的 R046 保留。对已有 E999 合并同因信息，不因新 UNKNOWN 再增加重复同因条目。
5. 闭集增加/删除必须提供官方语句头依据、改前/改后样本及 DML-18 对账并作定点设计修订；不能在 Q 实施时用“等”字自行扩展，更不能把整个批文件因第一条 SELECT 而跳过后续真实 multi DML。

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
|GATEWAY_MAX_CONCURRENT|固定常量 1（非调优项）|仅保留配置名用于日志/capabilities 自证和后续扩展；单应用主机所有 worker 共用槽，无排队、忙时 429；任何其他值（含 0/2）均拒绝启动，运维不得用它提升吞吐；并发扩展须先经 Mr.Linsang 决策及设计评审|
|GATEWAY_BROWSER_WAIT_SECONDS|990|capabilities 下发，前端总等待保护使用同一配置，计时包含浏览器上传；不得另写魔数|
|GATEWAY_DEPLOYMENT_MODE|direct|direct 为直连；经反向代理部署须显式配置 proxy 并提交实际链路核验|
|GATEWAY_DECLARED_PROXY_READ_TIMEOUT_SECONDS|660|仅 proxy 模式校验的部署声明值；不代表应用自动读到了外部 Nginx 生效配置|
|GATEWAY_MIN_FREE_BYTES|2147483648（2 GiB）|应用暂存卷受理前可用空间；Nginx 暂存卷运维独立检查|
|GATEWAY_MAX_LINE_BYTES|1048576（1 MiB）|逐物理行硬上限；超长输入明确 422，不静默截断|
|GATEWAY_REPORT_MAX_BYTES|25165824（24 MiB UTF-8）|子进程输出/服务读取前及入库前校验|
|GATEWAY_FLAME_POINTS|10000|Web 火焰图最多保留点数；显示“可视化抽样”，不抽样 KPI|
|GATEWAY_TMP_DIR|独立的应用可写临时目录|生产明确配置，Windows 测试用独立临时子目录；绝不放 Web 静态目录|

请求净文件限额和 multipart 总限额必须分别判断。即使 200 MiB 文件本身合规，异常多字段使总请求超过 201 MiB，也应拒绝。限定仅一个 file 字段及 connection_id/log_type 等预期表单字段；限制字段个数/长度，多文件、重复关键字段、压缩包/不支持 Content-Encoding 明确 422/415，不能自动展开归档。

临时目录仅运行账号可访问，文件名由服务生成而非用户路径；保留原文件名仅供安全显示。每请求最多同时存在 UploadFile spool、受控输入文件、报告/小摘要；不能生成第三份完整日志。记录实际磁盘峰值，不以“有 2 GiB 空闲”冒充并发无限。

并发策略已按 A 报告 §7.2 转录的 Mr.Linsang 裁定落实，不再待拍板：同时只能一个网关任务，拒绝者不入队、不分析其文件。2 GiB 是受理前空闲空间护栏，不是“每个任务实测必耗 2 GiB”的结论。本版支持单应用主机多 worker；若部署多应用主机，必须先提出共享协调方案及设计评审，不能每台各开一槽违反单任务约束。

非阻塞锁在应用完成认证并到达取锁处时立即尝试，忙则直接 429，不等待持槽任务结束再返回。Nginx 默认缓冲时，请求可能先完成代理收体才到应用，所以不承诺用户点击后立刻 429，也不声称文件未到达代理；但不存在“在应用排队约十分钟才返回 429”的设计。

### 6.3.1 超时链与启动校验

统一时钟说明与默认值：

|阶段|值|性质/起算点|
|---|---|---|
|应用 receive|300 秒|应用开始接收请求体后的总接收时限，不含代理预缓冲|
|子进程 analysis|540 秒|进程启动至分析/报告完成的硬截止；超时后仍需退出与回收|
|processing|600 秒|落盘、分析到持久化的软预算；阶段 checkpoint 不等于可打断任意 DB I/O|
|TERM/KILL 回收预留|10 秒|TERM 等待最多 5 秒，KILL 后正常回收另预留 5 秒；若无法确认退出，不宣称已清理/可复用资源，转故障处置|
|Nginx read/send|660 秒|相邻上游读/写的空闲超时，不是整个请求硬墙钟；client_body_timeout=60 秒同样为空闲超时|
|浏览器等待保护|990 秒|从浏览器发起上传计时，包含代理前上传；触发后结果待确认，不自动重传|

后端启动必须校验：所有时限为正，`analysis+10 < processing`，`receive+processing+10 <= browser_wait`；proxy 模式另校验 `processing+10 < declared_proxy_read < browser_wait`。默认满足 `540+10<600`、`300+600+10<=990`、`600+10<660<990`。失败记录具体配置名、值和违反的约束，拒绝启动，不能只打印 warning。capabilities 返回 browser_wait/config_version，前端必须使用返回值；读取失败时禁大日志提交并提示获取配置失败，不能悄悄沿用较短超时。

上述数值约束是配置防错及预算余量检查，不是完整链路成功的数学保证：代理计时为空闲时钟，浏览器包含代理预缓冲，DB 仍是软预算；不能把相加式当作无条件 SLA。后端只校验自身与部署声明，无法单凭 config.py 验证真正生效的 Nginx/LB/浏览器。发布必须检查 `nginx -T` 和所有中间代理的实际值、request buffering/上游传输条件、capabilities 与浏览器实际保护时长，演练临界超时。改为不缓冲或慢速上游等部署形态时须重算可静默等待包络并实测，不能直接套用本表声称已安全。

### 6.4 请求限制与前端

新增 `GatewayUploadPolicyMiddleware`（纯 ASGI）只匹配规范路径和 POST 方法。`BodySizeLimitMiddleware` 对该精确路由让专属策略决定，其他路由继续使用原 max_body_bytes（默认 50 MiB），不因新增网关参数放开全站。

处理要求：

1. 请求上下文最外侧保证 413/429 也有 X-Request-ID；Auth 在表单解析和重工作之前执行。调整注册顺序时须验证实际执行顺序，不能仅修改注释。跨域/安全头行为保持。
2. 对可信与不可信 Content-Length 都先作格式/上限检查，再包装 ASGI receive 累计所有 http.request body 字节；无头/分块/伪造偏小值同样不能越界。超限停止后续读取和表单解析，不返回 500、不创建报告。
3. 收体期间共用跨 worker 非阻塞槽；Linux 使用同一私有临时根的 advisory file lock，Windows 使用等价文件锁；进程退出由 OS 释放。锁文件不删除再重建，避免 inode 变化制造两个锁；不是进程内 Semaphore。所有 worker 必须共享同一锁路径，部署前验证；多应用主机不属于本版已批准的单槽实现范围。
4. 将取消/超限信号贯通 receive、multipart parser 与路由；部分暂存文件必须 close，不能只清理进入 analyze_log 后的文件。使用本请求 acquired 标志/持锁句柄，在 finally 仅释放本请求实际取得的槽：已取槽后超限/取消要释放，取槽前被 Content-Length 拒绝或取锁失败的请求不得释放他人的槽。运行中不得用线程超时退出代替实际取消子进程。
5. capabilities 返回文件上限、请求上限、支持类型、单任务/不排队提示、browser_wait_seconds、处理中可能耗时和配置版本；前端只作友好预检，后端仍是权威。
6. `app.js::onGatewayUpload` 把上传选定的 connection_id 固定在局部上下文；结果归属该实例。分析期间禁重复上传；切实例不能把 A 的成功提示/历史列表覆盖到 B。与加载历史分开维护上传 loading，finally 检查请求序号。
7. 前端按 status 和 content-type 解析：复用已有 `responseMessage`，兼容 `{detail:string}`、`{detail:{message}}`、`{message}`、新结构；HTML 413/504 用固定中文提示，不把整个 HTML 渲染到页面。现有 `apiFetch` 会对所有 5xx 先弹通用通知，须增加调用级 `handledHttpError` 选项（默认 false，传给 fetch 前从 options 删除）；仅网关上传设置 true 并自己展示一次精确提示，不能关闭全局 401 处理或其他模块错误提示。显示请求编号；网络断开提示“结果尚未确认，请查历史”，不能断言服务没有处理。
8. 不展示虚假的分析百分比。fetch 不能准确报告文件进度时显示“正在上传并分析，请勿重复提交”；完成后刷新**原实例**历史，成功/partial 文案保持区分。浏览器总等待保护取 capabilities 的 990 秒默认值，明确含上传；中止后先查询历史，不自动重传。忙时不自动重试文件，提示必须明确本次未分析/未入队。

429 定稿：响应同时有 `Retry-After`（整数秒）、`X-Request-ID` 头及 `detail.code=GATEWAY_BUSY`、message、stage=admission、request_id、retryable=true。正文为“当前已有一个网关日志任务正在上传或分析，同一时刻仅允许一个任务。您的文件未被处理（未进入分析、未排队），请约 N 分钟后重新上传；此间隔仅供参考，不保证届时空闲。”N=ceil(Retry-After/60)，不使用“已提交/排队中/自动处理”。上传可能已在代理暂存，不把“未被处理”解释为从未接收过字节。

重试间隔为建议值：持槽者在私有锁目录的独立状态文件中原子更新 owner_nonce、阶段、该阶段 monotonic deadline；receiving 用收体剩余预算，processing 用协调剩余预算，向上取整并限制为 5—600 秒；缺失/过期/不可解析/任务在收尾时回退 600。该元信息只供提示，文件锁才是准入权威，不能根据倒计时强行偷锁/杀任务；任务可能转入下一阶段或超出软预算，故不承诺预计完成时间。只在仍为同一 owner 时清理本任务状态，重启残留不作为忙闲判断依据。

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
|429 GATEWAY_BUSY|共享槽被占用|按 §6.4 返回两响应头及“未被处理/未排队、需重新上传”的单次提示；建议间隔不是完成承诺|
|422 GATEWAY_INVALID_LOG / SOURCE_MISMATCH|零有效行、覆盖率过低、长行、来源端口冲突|展示脱敏原因/覆盖率；不写成功报告|
|504 GATEWAY_ANALYSIS_TIMEOUT|子进程时限|先回收进程，再返回可读错误；不将残留 HTML 当成功|
|507 GATEWAY_TEMP_SPACE_LOW|磁盘不足/写入 ENOSPC|提示暂存空间；清理本次目录，不动其他任务|
|503 GATEWAY_REPORT_STORAGE_LIMIT|有效报文能力不足|给元数据库参数核查指引，不建议业务库全局修改|
|500 GATEWAY_ANALYZER_FAILED / OUTPUT_INVALID / STORAGE_FAILED|进程异常、产物不完整、其他落库错误|客户端仅固定文案＋请求号，异常栈只入受控服务日志|

阶段日志固定带 request_id、connection_id、净文件 bytes/hash、stage、duration_ms、returncode、报告字节、清理状态；不得记录密码、令牌、完整 argv 中的凭证或完整日志内容。应用自己返回的所有错误都应有请求号；代理在应用之前拒绝时可显示代理日志关联号或“请求未到达应用”，不能凭空造应用请求号。

### 6.7 Nginx 和部署改动

只为 `location = /api/v1/gateway-log/upload` 设置 `client_max_body_size 201m`、`proxy_read_timeout 660s`、`proxy_send_timeout 660s`、`client_body_timeout 60s`，复制现有 Host/X-Real-IP/X-Forwarded-For/Proto 透传；保留其他 location 的 20m/120s 以及 /metrics 访问控制。不全局替换配置值，不放宽认证，不修改公网暴露范围。

保留默认 request buffering；这会使用 Nginx 自身暂存空间，必须和应用卷分别核查。client_body_timeout 是相邻读间隔，不能说它保证完整上传 60 秒结束。应用的 300 秒收体时限不包含 Nginx 预先缓冲的时间，660 秒代理等待也不是全链路 SLA。负载均衡/安全网关存在更短超时的，要在部署路径清单中同步核验；不允许验收时只走直连逃过正式入口。

直接 8000 部署仍由应用专属字节限额保护，不能依赖 Nginx。代理部署显式设 deployment_mode=proxy 并核对声明值；先 `nginx -T` 检查生效 server/location 与上游路径，再 `nginx -t`，批准后 reload。实际超时/声明漂移按发布门禁阻断，不声称应用启动可自动发现全部漂移；`curl -I` 的响应 Content-Length 不能证明上传限额已修改。

不可调项发布检查：`GATEWAY_MAX_CONCURRENT=1` 是本版固定常量，不是吞吐调优旋钮；配置清单、运维说明和 capabilities 必须同标“固定、不可调”。切换服务之前先用拟发布配置执行已有配置加载/启动校验逻辑，0/2 等非法值应在验证环境被阻断，不能到生产停服后才试。确需并发大于 1，先取得 Mr.Linsang 的新裁定并评审内存/磁盘/超时及协调方案，不能只改配置。仓库 systemd/本机上游模板只证明参考拓扑，发布仍需核实真实部署的主机数、worker 数及同一锁路径，不能把模板当成内网现状已经实测的证明。

内网事故最终归因回填表至少包含：请求时间、入口 URL、浏览器 HTTP 状态/响应摘要、X-Request-ID、有效 max_body_bytes/网关新限额、是否到达 upload/analyze 阶段、代理 upstream_status/request_time、异常类型、子进程退出信息、元数据库 max_allowed_packet/报文字节数、RSS/磁盘数据。无原日志与原事故请求证据时，结论只写“已消除默认路径阻断及确认的实现缺陷”，不写“已证实原事故就是 Nginx/超时”。

## 7. 实施清单、数据库迁移与发布

### 7.1 按依赖顺序实施

|任务包|文件/函数范围|可交付验收点|
|---|---|---|
|D01 报告上下文基础|新增 report_context.py、140 迁移；audit/scan/inspection/daily/gateway 源记录写入与查询|冻结、继承、历史降级契约通过；不改变老 JSON 主体|
|D02 全部 HTML 接入|§3.1 H01—H14；scan_snapshot_service、scan_compare_service、raw_slowlog_service、bigtable_service；四个 CLI 和磁盘脚本|生成器逐项截图/解析断言；多实例可映射，无遗漏|
|D03 主表识别|新增 tdsql_table_shape.py；table_type_stats_service 的独立目录/候选并集/四层调度/DDL 判断；141 迁移两表各八列；前端即时/汇总/历史|覆盖独立目录、旧单表和 only_base 正例；共享 180 秒不变，分层/截断状态正确；§8.2.1 实测完成前不得无范围限定地判 PASS|
|D04 R043|parser_legacy.py 的预解析/AST/回退出口及 36 项闭集；dml.py R043；checker.py 完整性兜底|P2-04 落文经 A 定点确认后实施；附件及同源反例消误报；真实 multi 不漏报；DML-18 无新增 E999/其他规则退化；R121 保留|
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
3. 无破坏性数据迁移：所有旧 report_context_json/analysis_meta_json/request_id 为 null；主表统计六个新数字为 null、check_state/inventory_state 均为 LEGACY，不将未知值补零，不更新历史审核 violations。
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
|REP-12|H02 对连接 X（名 NX）做在线元数据审核并导出；提取中/之后改名 NY、再删连接，重导原 report_id|新报告始终为 NX；capture 发生在提取前，新报告导出不访问现名；对应 A 的 RPT-01|
|REP-13|H02 正常审核，观察服务参数、第三返回值、数据库和 HTML|保持 evaluate_gate 默认 False，gate_result=None，audit_history.gate_passed=NULL；HTML 无新增门禁结论；对应 A 的 RPT-02（按实际持久化列断言）|
|REP-14|H01 前端无绑定；另通过现有 API 显式传 connection_id 后改名导出|前者未关联/原架构行为不变，后者冻结真名/原门禁评估不变；本版无新增选择器或 report_connection_id|
|REP-15|H07 日常巡检对比与 H08 新旧网关报告分别带恶意名称、连续打开两次|分别核对各自归一 nonce 后的 CSP、实际每响应 nonce 更新且匹配脚本、原安全响应头及图表；H08 再验票据/清理/sandbox，H07 不虚构同一机制；危险名称无执行|

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
|PAR-10|Proxy 三命令失败、库枚举截断、kinds overlap、基线不一致|原状态/告警/总数保持，汇总同 eligible；失败/歧义不宣称全量；纯 only_base 差异经独立目录和 DDL 完整解释按 PAR-13，不永久降级新指标|
|PAR-11|旧 v13 数据、集中式空库、分布式零分片|前二者为 LEGACY/null、N/A/0；零分片不能直接推零主表，须目录完整且 C 全判明无主表才 COMPLETE/0|
|PAR-12|浏览器扫描 A→切 B→A 迟到；开历史→即时结果迟到|字段/标题/告警属于当前上下文；即时、汇总、历史列一致|
|PAR-13|MODERN 两层主表仅在 only_base 与完整 L 中，P 无该表；其他候选均判明|main=1、outside_shard=1、check/inventory=COMPLETE；旧 total/shard 不变，保留 reconciliation 和新增差异告警；对应 A 的非分片集合正例|
|PAR-14|MODERN 主表在旧 single 结果与 L 中，不在 S、也不在 only_base|仍计 1 且 outside_shard=1；证明只补 only_base 不足；不把原 single 重归类|
|PAR-15|MODERN 主表仅在独立 L 中；或同时在 L/P/B 三处|前者不漏，后者去重只查一次计一次；可出现 main>旧 shard，不截断主表数或破坏原总数公式|
|PAR-16|SHOW FULL TABLES 不支持/无权/缺列/50000 行护栏（−1/等于/+1）/预算截断/与 P/B 目录冲突，含零已知候选及部分判明两类|inventory 正确 COMPLETE/FAILED/PARTIAL；未全枚举时 check 为 UNKNOWN/PARTIAL，绝不 COMPLETE/0；公式成立，不捏造未枚举数量；截断后独立连接关闭，不排空剩余流或污染共享池|
|PAR-17|L 带视图或旧 X 物理子表；X 的 DDL 明确物理或反而带两层结构|视图不进 C；明确物理判负不计主表；物理/逻辑证据矛盾 UNKNOWN，不重复计数、不改旧子表列|
|PAR-18|141 新装/升级/重复启动/半途重入、八列默认值/类型漂移、所有读写端及实例聚合|两表各八列契约一致；旧六数字 NULL/两状态 LEGACY；未知/不适用/完整零不混淆，INSERT 参数数正确|
|PAR-19|至少两库、四层全含、名称顺序与层级相反，L/P/B 重复和 kinds overlap；固定 mock 耗时/截止点|全实例 C1→C2→C3→C4、层内精确名稳定；四层互斥且并集=C，每候选最多查一次；分类冲突仍 UNKNOWN，截断不减少 candidates 或伪造负例|
|PAR-20|基础耗时 180 秒、目录后仅余少量时间、DDL 中途跨过截止点、5000/5001 已知候选|共享 deadline 不重置，不额外加 300 秒；过 checkpoint 后不新开 I/O，已开始 I/O 不伪称被硬取消；原基础数/原失败行为不变，有结果时剩余计 unchecked/UNKNOWN/PARTIAL；不自动续扫拼接历史|
|PAR-21|目标实际内核执行 §8.2.1 容量核算，再在原 180 秒预算下全流程扫描并人工对账|留档真实并集、分层样本、原采集/目录/DDL 耗时、覆盖率和状态；未取得真实数据标待验证/范围受限，不以估算或 Mr.Linsang 预算裁定代替实测|

至少在内网实际使用的旧分区集群执行 PAR-01/04，并由 DBA 提供 SHOW CREATE 及人工清单对账。新旧样本证据均同时记录内核/Proxy 版本、三个 Proxy 命令、SHOW FULL TABLES、基线与目标 SHOW CREATE 返回（脱敏），证明“能发现表”与“能识别 DDL”两段都成立；不能只交识别器正则单测。新内核若内网没有实例，则保留官方语法单测/模拟返回证据，单列“新语法真实目录/实机未验证”，D03 不得给无范围限定的全适用 PASS；发布支持范围/限制由责任方明确验收，不由 Q 自行豁免。目标测试只发枚举及 SHOW 类只读 SQL，不在生产创建测试表。

### 8.2.1 固定 180 秒下的容量核算与准出（P2-05）

决策已完成：Mr.Linsang 指定共享预算保持 180 秒，本版不另设主表独享预算、不调整 5000 DDL 护栏、不新增续扫/后台任务。容量核算的用途改为**说明可完整覆盖的实际规模和验证未完成展示**，不是再次请求选择 180/300 秒。基础/目录占用时间越多，主表剩余越少，可能只得到下界或未知；不能宣称分层后必定查完。

执行责任：具备目标环境权限的内网执行方/DBA 提供只读原始数据；Q 随 D03 自测整理核算，A 在 D03 验收前核对数据与版本，O 在后续 UAT 核对用户可见状态。当前外网设计阶段未取得内网连通与耗时数据，以下是待执行步骤，不是已完成的基准测试。

1. 固定目标连接 ID/扫描时名称、目标库集合、账号可见范围、内核/Proxy 版本、采样日期、应用到 Proxy 的网络路径及负载。所有 SQL 只读，不在业务库创建/维护分区，不为测量关闭权限或调整超时。
2. 分别留档同一范围的 L、三类 Proxy 并集 P、剔除已知物理子表后的逻辑基线 B，并按精确名称算 `C=L∪P∪B` 与四层数量 n1—n4。`SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_TYPE='BASE TABLE'`（按驱动参数绑定）仅是原始基表规模参考：它可能含物理子表，也未证明能覆盖 L/P 的全部逻辑表，**不能单独作为 |C| 上界**。只有三集合完整、同范围时才可用 `max(|L|,|P|,|B|) <= |C| <= |L|+|P|+|B|`；缺源或截断时标“候选规模未完整取得”，不捏造界限。
3. 在已知候选中按库/层/DDL 体量分层选 20—50 张（总候选不足 20 时全取），非空层优先至少有样本；分组过多无法在 50 张覆盖时列明未覆盖组。只读取得 SHOW CREATE，记录每条往返、DDL 字节、成功/失败及连接重建；测量实际调用路径，不把连接时间排除后当端到端耗时。另记录正常基础采集 T_base、新目录阶段 T_inventory、局部分类及其他协调开销 T_other；冷/热缓存或负载状态无法确定就注明，不能用零时延 mock 代替。
4. 形成“均值规划值”和“P95 情景估算”，分层计算：`T_plan = T_base + T_inventory + T_other + Σ(n_i × mean_i)`；将 mean_i 替成该层样本 P95 得保守情景参考，**不是整体 P95 或完成保证**。缺层样本则不输出有完整置信外观的总估算。`T_remaining=max(0,180−T_base−T_inventory−T_other)`，在四层顺序和 5000 次护栏下估计可尝试数；已知 |C|>5000 时本次无法全量判明，不用均值掩盖护栏。
5. 在修复后的完整入口再跑一次原 180 秒配置，记录实际 `candidates/checked/unknown/unchecked/main/outside_shard`、两种 state、分层处理数量、停止原因（完成/预算/护栏/错误）和 UI 截图。模型估算与实际差异必须解释；不得为拿到 COMPLETE 临时延长预算、只挑小库却对外说整个实例通过。
6. 验收分开记录“受控扫描/诚实展示的功能通过”与“该目标规模是否全量完成”。COMPLETE 才能把 main 当精确数量对账；PARTIAL 只校验已确认下界和未判明范围，UNKNOWN 不提供零承诺。容量样本未取得时只能待验证/明确范围受限，不能给 D03 无限定 PASS。Mr.Linsang 保持 180 秒的裁定不是豁免 PAR-21 或把 PARTIAL 改称 COMPLETE。

核算记录模板：

|字段|必须填写内容|
|---|---|
|版本/范围|提交、内核/Proxy、连接名称/ID、目标库、可见权限、时间/负载|
|规模/覆盖|各库 L/P/B/C、n1—n4、目录是否完整、原始物理子表参考数|
|样本|20—50 张或不足时全部，库/层/DDL 字节/耗时/错误，未覆盖组|
|时间模型|T_base/T_inventory/T_other、各层样本数/均值/P95、均值规划与情景值|
|实际扫描|180 秒不变的配置证据、5000 护栏、计数字段/状态、分层数量/停止原因|
|结论边界|是否精确全量、下界覆盖范围、未验证项；Q 整理、A 核验，不能写模拟为实测|

量纲示例（**纯算例，不是内网实测**）：假设基础 30 秒、目录 10 秒、其他开销 5 秒，可用于 DDL 的规划余量为 135 秒。若 2000 张候选平均每张 0.05 秒，规划采集 145 秒；若平均 0.10 秒，则为 245 秒，按该均值大约只能尝试 1350 张。实际还受长尾、错误、分层差异及 checkpoint 影响，不能由此断言某内网实例的完成率。

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
|DML-11|`ALTER TABLE t MODIFY ts DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP` 及仅含 ON UPDATE 的简化版，正常/强制 ParseError/强制 Command 三路径|三路径事实均 NOT_DML/NOT_APPLICABLE，无 R043；旧 sql_type 的 UPDATE 误标不能影响判定；原 ParseError 保留|
|DML-12|`ALTER TABLE t ADD COLUMN a DATETIME ON UPDATE CURRENT_TIMESTAMP, ADD COLUMN b INT` 及把 b 改为 `VARCHAR(20) CHARACTER SET utf8mb4` 的强触发版，同上三路径|同上，兼容属性 false；尤其 Command 的旧 sql_type=UPDATE 不能制造 R043 或本事实新增 E999|
|DML-13|`CREATE TABLE t (id INT, ts DATETIME ON UPDATE CURRENT_TIMESTAMP, c VARCHAR(20) CHARACTER SET utf8mb4)`，并给 11—13 加合法前导注释/空白/混合大小写，同上三路径|独立头定位一致，可靠非 DML 为 NOT_APPLICABLE，无 R043；非 R043 的真实完整性错误不被清除|
|DML-14|`UPDATE a JOIN b ON a.id=b.id SET a.v=1`，同上三路径|三路径均 UPDATE/RESOLVED/true，分布式规则开启时 R043；强制 ParseError 路径同时保留原错误|
|DML-15|`DELETE a FROM a JOIN b ON a.id=b.id`，同上三路径|三路径均 DELETE/RESOLVED/true，报 R043 且显示 DELETE；不由被 mock 的 Command.this 决定事实|
|DML-16|各 status/kind/multi 真值表、ParsedSQL 构造与属性赋值检索、public AuditResult 序列化|只有 RESOLVED+DML+true 的只读兼容属性为 true；无旧 setter/双写/状态残留；原公开结构不变|
|DML-17|Command/ParseError 回退的完整 WITH→SELECT/单表 UPDATE/联表 DELETE，未闭合 WITH、可执行注释条件未知|外层 SELECT 为 NOT_APPLICABLE，外层 DML 按目标，无法证明者 UNKNOWN/完整性失败；不能一律 UNKNOWN 或简单取第一个词|
|DML-18|§5.2.1 全部 36 头的固定代表语料、附件原文、真实联表 UPDATE/DELETE；改前/改后同架构/规则覆盖各跑审核，并追加三出口注入组|闭集与语料头集合全等；每条原无 E999 的不得新增 E999，总条数改后不大于改前；原 parse_error/KFN/E999 不被清除，其余非 R043 规则命中/级别逐条一致；仅移除已确认 R043 误报，真实 multi 仍命中|
|DML-19|LOCK TABLES 复合 token、普通注释前缀、字符串/反引号伪造 SELECT/UPDATE、FLUSH/SAVEPOINT/RESET 的通用 Alias 根、闭集外/未知头、批文件第一条 SELECT 后接 multi DML|复合裸头正确识别，带引号内容不得冒充头；通用表达式走词法闭集，不全当可靠 AST；未知按完整性分支，闭集不吞原错误；逐实际语句判断，不豁免后续 DML|

Rev.A 的 DML-06—10 编号保留；A 报告建议中同名的新增 DML-06—10 在本修订分别映射 DML-11—15，避免覆盖旧验收项。三个解析出口的“无 R043”必须同时验证事实状态与是否新增完整性错误，不能仅断言 R043 不在列表中。

DML-18 实施细则：在修改 parser/checker 之前，先把当前基线每条 SQL 的 hash、原始 SQL、parse_error/KFN、规则 ID/级别及 E999 数冻结为 fixture，不能改完再生成“改前”期望。附件依 §2.1 的原 hash；36 头每头至少一个完整代表样本，包含本轮答复中的 Command/ParseError 例子。正常、强制 Command、强制 ParseError 三组分别和同路径基线比较，不拿注入了错误的结果与无注入基线混比。除了总数，还必须逐句对账，禁止靠另一条 E999 被误删来抵消新增 E999；原有完整性失败原因不得丢失。DML-19 的故意未知/非法输入另按明确预期验失败关闭，不能为满足净效果总数而把 UNKNOWN 改成 NOT_APPLICABLE。

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
|GW-C1|两个不同用户在不同 worker 同时上传；第一任务持续运行，第二请求到达应用取锁点|第二非阻塞 429，不等待第一完成；两响应头正确、正文说明未分析/未入队需重传，首任务正常；经代理另计预缓冲耗时，不能误称应用排队|
|GW-C2|Content-Length 取槽前拒绝、实际字节取槽后 413、取消和 429 三类路径|本请求已取槽则清理释放，未取槽绝不释放他人锁；第一任务活跃时连续拒绝不能让第三请求偷跑；退出后下一请求可受理|
|GW-C3|持槽阶段切换、提示元信息缺失/损坏/过期/残留，Retry-After 达边界|提示 5—600 秒有界、无法估计回退 600，绝不以元信息判忙闲；无自动重传/队列承诺，前端只提示一次|
|GW-C4|拟发布配置并发为 0/2、运维说明/capabilities 标签、不同 worker 锁路径、多应用主机部署审查|在切生产前的配置校验阻断非法值；明确“固定 1、不可调”，有效 1 行为不变；实际共享锁路径/单主机须查真实配置，不能凭仓库模板宣称全局一槽已验证|
|GW-T1|分别破坏 analysis/processing、receive/browser、proxy 声明余量不等式及值类型；direct 模式|启动失败指出具体配置；direct 只豁免 proxy 声明约束，不豁免自身预算；capabilities 与浏览器实际值一致|
|GW-T2|代理实际值短于声明、LB 更短、慢上传/不缓冲、子进程超时后 TERM/KILL、DB 迟滞|实际链路漂移门禁阻断，展示真实 408/504/待确认状态；区分空闲/墙钟/软预算，不以数值表宣称完整时限保证；异常回收未确认不报告清理成功|

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
|目标二级分区能力清单|实际内核/Proxy版本、旧/新三命令及独立目录/SHOW CREATE样本、人工主表数|新语法实机不可用时单列兼容证据限制；不得用候选全集未经证实的识别器 PASS 代替 D03 全链路验收|
|180 秒下的主表容量核算|§8.2.1 实际候选并集、分层样本/阶段耗时、固定预算全流程结果|180 秒已由 Mr.Linsang 决定，无待选预算；实测仍待回填，D03 精确全量与下界/未知验收必须分开，不以裁定代替测量|
|元数据库与代理发布参数|实际版本、packet、暂存空间、超时链、进程清理配置|决定迁移窗口及容量验收，不由应用自动修改生产参数|

本设计已经完成材料核对、官方资料校验、R043 原文及解析出口同源复现、应用默认上传限额隔离复现；本轮另核对闭集代表语料及词法边界。A 第二轮已关闭第一轮七项并解除 P1-01，给出有条件通过；本 Rev.C 修订本轮 P2-04/P2-05/P3-04，按 A 的要求提交三处定点核验，不重新发起第三轮完整评审。D04 在 P2-04 落文获确认后实施；D03 按四层调度开发，真实容量核算在无范围限定的验收前完成。尚未开发 v1.6.3.4，未执行修复后 SIT/UAT/性能或内网变更；O 不自行把设计修订声明为实现通过。180 秒保持不变，相关接口/护栏/计数若另需调整须再评审，不能由实现自行偏离。

## 10. 第一轮评审修订索引

|A 问题|处理结论|本设计落点/新增验收|
|---|---|---|
|P1-01：旧 sql_type 回退不可靠|认可；事实源与规则均脱离旧分类；可靠非 DML 用 NOT_APPLICABLE，不照搬一律 UNKNOWN|§2.3、§5.2—5.4；DML-11—17|
|P2-01：新语法主表可能不在分片候选|认可；独立目录＋三类并集＋逻辑基线，补齐 A 的 only_base 建议仍未覆盖的旧单表分支；修正原分片上界|§4 全节、§7.1—7.2；PAR-10—18|
|P2-02：文件来源判据/门禁耦合|按 §7.1 转录裁定落文；保留已有 H01 API 显式绑定，不新增选择器；H02 不引入门禁|§1、§3.1—3.3；REP-12—14|
|P2-03：单槽需明确裁定与提示|按 §7.2 转录裁定采用单任务；采纳说明性 429，纠正等待时长/空间和锁释放表述|§6.2—6.4、§6.6；GW-C1—C4|
|P3-01：超时链集中校验|认可；增加启动数值约束与外部真实配置发布校验，区分不同计时性质|§6.3.1、§6.7；GW-T1—T2|
|P3-02：H07 CSP 需覆盖|认可；分别保护 H07/H08，归一随机 nonce 后比策略，不照搬两安全链混同或动态头字节全等|§3.4；REP-15（联动 REP-10/GW-14）|
|P3-03：旧布尔字段兼容方式|认可简化；只读派生属性，补构造/序列化边界，不宣称零影响|§5.2；DML-09/16|

以上记录第一轮整改落点；A 已在第二轮报告确认这七项关闭，不代表代码已实现。两轮 A 原报告及裁定来源保持可追溯；首轮不同意见见 Rev.B 答复，本轮新增项见下节。

## 11. 第二轮定点修订与裁定索引

|事项|Rev.C 处理|核验位置与剩余证据|
|---|---|---|
|P2-04：可靠头枚举不闭合|认可；明确 36 项闭集，补复合 token/通用表达式路径，不豁免原错误|§5.2.1、DML-18/19；A 定点核验落文，Q 开发后做逐句净效果回归|
|P2-05：候选扩大后的预算/容量后果|认可；四层互斥全覆盖调度；**共享 180 秒不变**，5000 护栏不变；补实际并集与分层抽样核算|§1、§4.3、§8.2.1、PAR-19—21；预算无待决项，真实内网容量数据仍待责任方回填|
|P3-04：固定并发误作调优项|认可并同步补充；明确固定 1，非法值启动失败，切生产前核验|§6.3、§6.7、GW-C4；不改变已裁定单任务行为|

仅未直接照搬两项技术论证：分层为启发式而非保证“最紧下界”；information_schema 原始基表 COUNT 不是未经证明即可采用的 C 上界。替代核算与理由均在 §8.2.1 和 Rev.C 答复中。两项 P2 的设计整改已落文，A 定点确认与真实内网容量证据不能互相替代。
