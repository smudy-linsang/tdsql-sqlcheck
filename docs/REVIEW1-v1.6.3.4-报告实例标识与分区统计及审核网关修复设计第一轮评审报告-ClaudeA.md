# REVIEW1-v1.6.3.4 详细开发设计说明书 第一轮评审报告

| 项 | 内容 |
|---|---|
| 被评审文档 | `docs/DETAIL-v1.6.3.4-报告实例标识与分区统计及审核网关修复.md`（Rev.A，提交 `402acc2`，683 行） |
| 编写方 | 智能体 O |
| 评审方 | 智能体 A |
| 评审轮次 | 第一轮设计评审 |
| 评审日期 | 2026-09-06 |
| 代码基线 | `main` / `bcbd0de`（文档锚定 `88954b3`；两者之间仅新增文档，应用代码一致，已复核） |
| 评审方式 | 逐条实证：所有"当前实现"断言与根因结论均在本机跑代码复现，附件原文实跑；不以读文档代替 |
| **评审结论** | **不通过（有条件）。1 项 P1、3 项 P2、3 项 P3。P1 关闭后可进入编码。** |
| 裁决状态 | P2-02、P2-03 已于 2026-09-06 获 Mr.Linsang 裁决，由"待拍板"转为"待落文"；裁决原文与落实口径见 §7。其余 5 项仍待 O 修订 |

---

## 1. 结论摘要

| 级别 | 编号 | 问题 | 影响 |
|---|---|---|---|
| **P1** | **P1-01** | **§5.2 把 `parsed.sql_type` 当作 R043 的防御式门闸，但它在"AST 不可用"这条路上恰恰不可信**：`_detect_sql_type_regex` 的关键词元组把 `UPDATE` 排在 `ALTER` 之前，实测 `ALTER TABLE t MODIFY ts DATETIME ON UPDATE CURRENT_TIMESTAMP` 被判为 `sql_type='UPDATE'` | 需要回退提取器的场景，正是 sql_type 最不可信的场景；实施者若照字面把 sql_type 当门闸，**REQ-03 想消灭的那一类误报会在 ALTER 上原样复活**，而这类 SQL（`ON UPDATE CURRENT_TIMESTAMP`）在本项目域内极常见——附件本身就有两列 |
| P2 | P2-01 | **REQ-02 候选集来源对"新语法族"（`TDSQL_DISTRIBUTED BY HASH` + `TDSQL_PARTITION BY`）的覆盖没有证据**：§4.3 候选取自 Proxy 分片表集合，而工程师附件只证明该命令混合了"普通分片表和二级分区表"（旧语法族） | 若新语法表不出现在 `show table with shardkey`，§4.2 表格里 MODERN 那一行**永远不会被执行**——不是 UNKNOWN，是根本没进候选集，状态却仍会算成 COMPLETE。**静默少算 + 报告完整**是最坏的失败形态 |
| P2 | P2-02<br>**已裁决** | REQ-01 对**文件审核报告**（H01，平台最常用的报告）给出"未关联实例（离线文件审核）"，与需求 ①"所有出具的 html 报告中均要添加扫描或检查的实例连接名称"的字面要求存在张力 | 这是需求解释分歧，不是技术缺陷。但设计单方面选定了一种读法而没有把它标成待裁决项；实测 `audit_file` API **本来就接收 `connection_id`**，只是前端没绑。**Mr.Linsang 已裁决：以"生成时是否指定实例连接"划线（§7 裁决一）** |
| P2 | P2-03<br>**已裁决** | `GATEWAY_MAX_CONCURRENT=1` 是**新增的用户可见限制**（当前无任何并发限制），默认不排队直接 429，而单次分析上限 540 秒 | 多 DBA 团队里第二个人最长被挡 9 分钟。设计把它放在配置表里，没有标成需要业务方接受的行为变更。**Mr.Linsang 已裁决：批准限制为 1（§7 裁决二）** |
| P3 | P3-01 | §6.3 的超时/预算数值散落在配置表、§6.4、§6.7 三处，没有一张合并的"超时链"核对表 | 部署方无法一眼验证"内层不超过外层"这个不变量 |
| P3 | P3-02 | §3.4 只为网关出口点名了 nonce/CSP 约束，实测 `backend/api/daily_inspect.py` 也有 nonce(6 处)/CSP(5 处)，H07 未一并点名 | 新增页眉块可能与该出口的 CSP/nonce 冲突 |
| P3 | P3-03 | §5.2 说保留 `is_multi_table_update` 以"兼容老调用者"，实测**全仓库只有 R043 一个消费者** | 表述偏保守会让实施者高估删除正则赋值的风险；应把爆炸半径写实 |

**一句话**：这是一份质量很高的设计——四个需求的根因都做了真复现，官方资料核对到位，反面清单（"不做什么"）写得比正面清单还细。唯一的 P1 是把一个**在关键路径上不可信的字段**当成了安全门闸。

---

## 2. 评审方法

O 在 §2.3 声明"已执行的只读复现"。我没有采信这张表，而是逐行重跑：

| 手段 | 用途 |
|---|---|
| 从附件 HTML 抠出原始 CREATE 语句，跑 `SQLParser.parse` + `RuleChecker.audit_sql` | 验证 R043 误报与四条违规是否与报告 ID 6537 逐条一致 |
| 直接对 `_regex_pre_parse` 的正则做捕获段打印 | 验证 O 对"起点是 ON UPDATE、终点是 CHARACTER SET"的因果描述 |
| 直接调 `_detect_sql_type_regex` 并构造降级语句 | 检验 §5.2 提出的门闸是否可信（**发现 P1-01**） |
| 全仓库枚举 `<!DOCTYPE html>` 生成点与 html 生成函数 | 独立核对 H01—H14 清单是否有遗漏 |
| 读 `middleware.py` / `config.py` / `gateway_log*.py` / `nginx-sqlcheck.conf` | 核对 §6.1 的六条"当前确定的代码问题" |
| 在 MariaDB 上实跑三份迁移的 ADD COLUMN 语法 | 检验 DDL 可执行性（`TEXT NULL DEFAULT NULL` 等） |
| 读 `loader.py` | 检验 v14 目录放三个文件是否被支持并有序 |

---

## 3. 核验通过的部分

### 3.1 REQ-03 根因链：完全属实，且比诊断报告更准

我用附件原文（从报告 HTML 反解，41 行 / 4575 字节）实跑：

```text
parse_error=None   sql_type='CREATE TABLE'   is_create_table=True
is_multi_table_update=True                    ← 误报的直接来源
列数=35  索引数=2
secondary_partition={'method':'RANGE','maxvalue_partitions':('p_future_max',),'source_context':'CREATE'}
违规=[R036/INFO, R037/INFO, R043/ERROR, R121/ERROR]
```

与报告 ID 6537 **逐条一致**。

再验因果链，打印那条正则的捕获段：

```text
re.search(r"\bupdate\b(.*?)\bset\b", sql_lower)
→ 捕获段 = ' current_timestamp, c varchar(20) character '
```

起点确是 `ON UPDATE` 的 `update`，终点确是 `CHARACTER SET` 的 `set`，中间横跨两个字段定义、含一个逗号。**O 特意纠正的那一点也对**——不是匹配到 `CHARSET` 内部子串，正则有单词边界，真正终点是 `CHARACTER SET`。这一点内网诊断口径里没人说清楚，O 说清楚了。

§2.3 表里九行我全部复现，结论一致：

| 输入 | O 声称基线 | 我实测 |
|---|---|---|
| §5.1 最小 CREATE（ON UPDATE + CHARACTER SET） | multi=true，误报 R043 | ✅ 一致 |
| 去掉 ON UPDATE | multi=false | ✅ |
| 去掉 CHARACTER SET（O 未列，我补测） | multi=false | ✅ 佐证因果链需要两个词同时存在 |
| `UPDATE t1 a, t2 b SET …` | multi=true，R043 | ✅ 真联表必须保留 |
| `UPDATE t1 a JOIN t2 b … SET …` | multi=true，R043 | ✅ |
| `DELETE a FROM t1 a JOIN t2 b`（我补测） | — | ✅ 命中 |
| `DELETE FROM t1,t2 USING …`（我补测） | — | ✅ 命中 |
| `UPDATE t PARTITION (p0,p1) SET …` | 误报 | ✅ 确实误报 |
| ``UPDATE `a,b` SET …`` | 误报 | ✅ 确实误报 |
| 单表 UPDATE 的 SET 子查询含 JOIN | multi=false | ✅ |
| 单表 DELETE 的 WHERE 子查询含 JOIN | multi=false | ✅ |

**§5.1 那句"仅增加 `if CREATE: return` 不能解决同源误报"是对的**：后两条误报的 sql_type 就是 UPDATE，CREATE 短路救不了它们。这个判断挡住了最容易被选中的廉价补丁。

### 3.2 REQ-01 的 H01—H14 清单：我独立枚举，无遗漏

全仓库搜 `<!DOCTYPE html>` 的生成点（排除前端模板、vendor、证据目录），共 11 个文件；再搜 html 生成/服务函数。逐一对照 H 清单：

| 我枚举到的生成点 | 对应 H 编号 |
|---|---|
| `api/sql_audit.py`（两个导出） | H01 / H02 ✅ |
| `api/slow_query.py::export_scan_task_html` | H03 ✅ |
| `api/inspection.py::export_schema_check_report` | H04 ✅ |
| `services/scan_compare_report.py`（单快照 + 对比） | H05a-d / H06a-d ✅ |
| `services/daily_inspect_service.py` | H07 ✅ |
| `api/gateway_log.py::get_report_html` | H08 ✅ |
| `api/raw_slowlog.py::export_events` | H09 ✅ |
| `gateway_log_analysis/analyze_gateway_log.py` | H10 ✅ |
| `gateway_log_analysis/merge_gateway_reports.py` | H11 ✅ |
| `gateway_log_analysis/interf_report_generator.py` | H12 ✅ |
| `gateway_log_analysis/interf_deep_analysis.py` | H13 ✅ |
| `static/scripts/disk_performance_test/generate_report.sh` | H14 ✅ |

**一个不少。** 另外 `api/scan_compare.py` 与 `api/daily_inspect.py` 的 `compare_html` 是上述生成器的服务出口，生成器已在清单内（见 P3-02 的补充要求）。

### 3.3 REQ-04 的六条"当前确定的代码问题"：逐条属实

| O 的断言 | 我实测 |
|---|---|
| 中间件只做 Content-Length 预检、无实际字节累计 | `middleware.py:68-77` 确实只读 `content-length` 头 ✅ |
| 注释写 8 MB、配置真实默认 50 MiB | docstring"默认 8MB" vs `config.max_body_bytes()`"默认 50MB" ✅ **确为误导** |
| `/upload` 是 async 路由却跑同步流程 | `async def upload_log` → `await file.read()` → 同步 `analyze_log` ✅ |
| 子进程用裸 `python` 而非当前解释器 | `cmd = ["python", str(script_path), ...]` ✅ |
| 固定 `timeout=120` | ✅ |
| 仅凭 HTML 存在判成功、不校验 returncode | 代码注释原文："**即使脚本可能有一些警告，只要生成了 HTML 就视为成功**" ✅ |
| 413 响应体没有 `detail` 字段 | 中间件返回 `{"code":413,"message":...}`，前端读 `d.detail` 必然 undefined ✅ **佐证 §6.1 第 5 条** |
| Nginx 20m | `deploy/nginx-sqlcheck.conf:21` ✅ |

**O 对内网诊断报告的三条"不认可"我也复核过，O 是对的**：
① "绕过 Nginx 就不受大小限制"——错，应用层 50 MiB 限额同样拦；
② "LONGTEXT 约 16MB / MEDIUMTEXT 约 64MB"——**说反了**，MySQL 官方是 MEDIUMTEXT < 2^24、LONGTEXT < 2^32；
③ 60%/30%/5% 的根因概率没有采样支撑。
把"概率"换成"可验证诊断分支 + 待回填归因表"是正确的方法论纠正。

### 3.4 迁移可行性：v14 槽位可用、多文件有序、DDL 可执行

* `backend/schema/` 现有 v0—v13，**v14 未被占用** ✅（v1.6.3.2 评审时删掉的那个 v14 没有落地，槽位是干净的）
* `loader.py::discover_schema_files` 按 `(version, sequence)` 升序，**同一版本目录下多文件被支持**，140→141→142 的执行顺序成立 ✅
* 三份迁移的 DDL 我在 MariaDB 上实跑：`TEXT NULL DEFAULT NULL`、`MEDIUMTEXT NULL DEFAULT NULL`、严格模式下同样接受 ✅（TEXT 不能有非 NULL 默认值，但 `DEFAULT NULL` 合法）
* 只有 `table_type_stats_service.py` 做启动期精确列集合验收——正是 REQ-02 要改的那张表，§4.4 已明确要求同步 `_STAT_CONTRACT/_ITEM_CONTRACT` ✅；REQ-01 那 7 张表没有同类验收，加列不会触发失败关闭

### 3.5 方法论上值得记一笔的地方

1. **§2.1 给了四份附件的 SHA-256**，让 Q/A/O 能证明用的是同一份输入。这个做法应该固化到后续所有涉及外部附件的设计里。
2. **§2.2 明确"官方能力说明与项目治理规则不混用"**：TDSQL 官方文档里有自动维护分区的能力，但不替代 Mr.Linsang 已定版的"业务自行维护、禁止 MAXVALUE"治理政策。这条边界划得准。
3. **§4.2 明确拒绝了工程师附件里的 shell grep 方案**，理由是注释/字符串/反引号/换行/可执行注释都会污染命中——同时又采纳了它的"先广播、再两层结构"语义。**采纳语义、拒绝实现**，处理得当。
4. **§8.5 把证据分成四类归档**（真实内网返回 / 合成 / 本地 / 未执行），并写明"不能把 Mr.Linsang 风险签署说成性能实测"。这一条直接对应我们在 v1.6.3.2 门禁上踩过的坑。


---

## 4. 评审发现

以下每条都给出：**问题** → **证据（可复现）** → **影响** → **照图施工整改**。整改条目全部写成 O 可以直接并入设计文档的成稿文字，不需要二次翻译。

### P1-01（必须整改）§5.2 第 5 条的防御式门闩建立在一个会误判的函数上，R043 误报可以原样复发

**问题**

§5.2 第 5 条把 R043 的开火条件定为：

> R043 防御式检查 `parsed.sql_type in ('UPDATE','DELETE')` 且已确认 multi 才报规则。

这句话默认了"`sql_type` 是可信的语句类型"。但在**本次要修的这条路径上，`sql_type` 恰恰不可信**——它和 `is_multi_table_update` 是同一类正则全句扫描的产物，犯的是同一种错误。设计把一个坏正则的输出，当成另一个坏正则的裁判。

**证据**

`backend/engine/parser/parser_legacy.py:3746-3767`：

```python
def _detect_sql_type_regex(self, sql: str) -> str:
    """正则检测SQL类型（解析失败时的回退方案）"""
    ...
    if "CREATE TABLE" in sql_upper:
        return "CREATE TABLE"
    ...
    for keyword in ("SELECT", "INSERT", "REPLACE", "UPDATE", "DELETE",
                    "CREATE", "ALTER", "DROP", "LOAD", "HANDLER", "FLUSH",
                    "LOCK", "UNLOCK", "BEGIN", "START", "COMMIT", "ROLLBACK",
                    "GRANT", "REVOKE", "TRUNCATE"):
        if sql_upper.startswith(keyword) or f"\n{keyword}" in sql_upper or f" {keyword} " in sql_upper:
            return keyword
    return "UNKNOWN"
```

关键事实：**元组里 `UPDATE` 排在第 4 位，`ALTER` 排在第 7 位**，而命中条件是"整句里有 ` UPDATE `"这种含空格的子串。我实测（照抄该函数逻辑独立执行）：

|输入|返回值|应为|
|---|---|---|
|`ALTER TABLE t MODIFY ts DATETIME ON UPDATE CURRENT_TIMESTAMP`|**`'UPDATE'`**|`ALTER`|
|`ALTER TABLE t ADD COLUMN c DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, ADD COLUMN d INT`|**`'UPDATE'`**|`ALTER`|
|`ALTER TABLE t COMMENT = 'delete me'`|`'ALTER'`|`ALTER`（未命中是因为引号紧贴，属于侥幸）|
|`DROP TABLE t_update_log`|`'DROP'`|`DROP`（未命中是因为下划线相连，同样是侥幸）|

`ON UPDATE CURRENT_TIMESTAMP` 是 TDSQL/MySQL 里**极其常见**的列属性，本次出问题的附件 DDL 里就有。这个函数只在 `CREATE TABLE/PROCEDURE/TRIGGER/VIEW/FUNCTION` 上做了前置短路，**`ALTER` 没有任何保护**。

赋值点在 `parser_legacy.py:3521` 与 `3578`，正是 §5.2 第 4 条点名要处理的"ParseError/Command 回退出口"。也就是说：设计要求在这两个出口谨慎判定 `dml_target`，却又让 R043 去信任同一出口上早已被污染的 `sql_type`。

**影响**

按 §5.2 施工后仍然存在一条误报通路：一条**含 `ON UPDATE CURRENT_TIMESTAMP` 的 ALTER 语句**走到 ParseError/Command 回退出口时 —— `sql_type` 被判为 `'UPDATE'`，门闩第一半直接放行；此时只要 `dml_target` 的有限回退把它的 `is_multi_table` 判成 true（例如 `ALTER TABLE a, b ...` 这类被词法头误认的形态），R043 就会重新对一条 DDL 开火，而 `parse_error` 依旧为空、报告依旧显示"审核完成"。这与本次要修的 6537 号误报是**同一类故障、同一个报告观感**。

更要紧的是方法论问题：§5.2 第 5 条把它写成"防御式检查"，会让 Q 和后续 SIT 认为"有两道闸"，从而**降低对 `dml_target` 本身的验收强度**。实际上第二道闸不但不牢，还可能反过来把不该放行的放行。

**照图施工整改**

请 O 将 §5.2 第 5 条整条替换为下述文字（可直接采用）：

> 5. R043 的**唯一开火条件**是 `parsed.dml_target.status == RESOLVED` 且 `parsed.dml_target.statement_kind in ('UPDATE','DELETE')` 且 `parsed.dml_target.is_multi_table is True`。三者缺一不报。
>
>    `parsed.sql_type` **不得**参与 R043 的开火判定。原因：`_detect_sql_type_regex`（`parser_legacy.py:3746`）在 ParseError/Command 回退出口（赋值点 `parser_legacy.py:3521`、`:3578`）按关键字元组顺序做含空格子串匹配，元组中 `UPDATE`/`DELETE` 位于 `ALTER` 之前，故 `ALTER TABLE t MODIFY ts DATETIME ON UPDATE CURRENT_TIMESTAMP` 会返回 `'UPDATE'`。本函数**本次不改**（改动它会波及 `sql_type` 的全部消费者，超出本次爆炸半径），因此在 R043 中只能把它当作不可信输入。
>
>    若实现方希望保留一致性自检，只能以**断言型旁路**存在：当 `dml_target.status == RESOLVED` 而 `parsed.sql_type` 与 `dml_target.statement_kind` 不一致时，记录一条内部诊断计数（reason code `DML_TYPE_DISAGREE`），**不改变**规则命中与否、不产生用户可见告警、不进入报告正文。
>
>    `dml_target.status == UNKNOWN` 时 R043 不命中，同时按第 5 条既有约定并入 checker 完整性门禁，不得以"R043 未命中"冒充审核通过。

同时在 §5.3（AST 识别边界）末尾新增一段：

> **回退出口的词法头判定不得复用 `_detect_sql_type_regex`。** `_extract_dml_target` 在 ParseError/Command 出口做有限回退时，必须自行对**去注释后的语句首个有效 token** 做判定：仅当首 token 为 `UPDATE` 或 `DELETE`（大小写不敏感，允许前置 `/*!...*/` 可执行注释与空白）时才进入回退分析；首 token 为其他任何值（含 `ALTER`、`CREATE`、`WITH`）一律 `status=UNKNOWN`、`statement_kind` 按首 token 记 `NOT_DML` 或 `UNKNOWN`。禁止在整句范围内搜索 `UPDATE`/`DELETE` 关键字。

并在 §8 的 DML 用例矩阵中补入下列 5 条**必须新增**的回归用例（现有 DML-01 只覆盖附件原文，覆盖不到本条）：

|用例|输入|三个出口均须满足的断言|
|---|---|---|
|DML-06|`ALTER TABLE t MODIFY ts DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP`|无 R043；`dml_target.statement_kind != 'UPDATE'`；`is_multi_table` 非 true|
|DML-07|`ALTER TABLE t ADD COLUMN a DATETIME ON UPDATE CURRENT_TIMESTAMP, ADD COLUMN b INT`|同上（逗号+ON UPDATE 双诱因同时出现）|
|DML-08|`CREATE TABLE t (id INT, ts DATETIME ON UPDATE CURRENT_TIMESTAMP, c VARCHAR(20) CHARACTER SET utf8mb4)`|无 R043（附件同源最小复现）|
|DML-09|真实 `UPDATE a JOIN b ON a.id=b.id SET a.v=1`|**必须**命中 R043（防止整改把真阳性一起关掉）|
|DML-10|真实 `DELETE a FROM a JOIN b ON a.id=b.id`|**必须**命中 R043|

DML-06/07/08 三条各自都要在**正常 AST 出口、ParseError 出口、Command 回退出口**三种情况下断言（可用注入式构造强制走回退分支），否则本条整改无法证明生效。

---

### P2-01（建议整改）REQ-02 的候选集来源未经证据支撑，新语法族可能整族漏统计而状态仍报 COMPLETE

**问题**

§4.3 第 1 条规定候选来源：

> 从当前请求最终归一化的**分片逻辑表集合**产生候选 `(库名,表名)`。

该集合在现有实现中来自 `/*proxy*/show table with shardkey`（`table_type_stats_service.py:108`）。而 §4.2 的结论表里有一行是**新语法族**：

|一级事实|二级事实|结论|
|---|---|---|
|`TDSQL_DISTRIBUTED BY HASH`|`TDSQL_PARTITION BY RANGE/LIST`|SECONDARY，新二级|

**问题在于：没有任何证据表明 `TDSQL_DISTRIBUTED BY` 建的表会出现在 `/*proxy*/show table with shardkey` 的返回里。**

**证据**

工程师附件第 7 节的原话是：

> `/*proxy*/show table with shardkey;` 同样不能直接用于互斥统计：它可用于粗略查看带分布规则的表，但会将**普通分片表和二级分区表**混在一起。

这句话只证明了**旧语法族（`shardkey=` + `PARTITION BY`）**会混在该命令的返回里。附件全文没有一处说明 `TDSQL_DISTRIBUTED BY HASH` 的表出现在哪条 `/*proxy*/show table` 命令的返回中。附件第 8.1 节给出的采集方式恰恰是 **`SHOW TABLES` 全量逻辑表 → 逐表 `SHOW CREATE TABLE`**，而不是从 `show table with shardkey` 取候选。设计把采集入口从"全量逻辑表"收窄成了"Proxy 分片集合"，这个收窄动作**在设计中没有对应的证据条目**（§2.3 只读复现表里也没有这一项，因为本地没有 TDSQL 实例可验）。

**影响**

三种可能后果，且设计当前的记账体系**无法区分它们**：

1. 若新语法表**不出现在** `with shardkey` 中 → 它们根本不进候选集 → 既不计入 `secondary_partition_candidates`，也不计入 `unknown`/`unchecked`。而 §4.4 的验收公式 `candidates = checked + unknown + unchecked` **依然成立**（自洽但空洞），状态照样判 `COMPLETE`。**结果是：整个新语法族被静默漏计，页面显示一个带"完整"标记的错误数字。**这是比报错更坏的失败模式。
2. 若新语法表落在 `without shardkey`（单表）返回里 → 那是现有三分类统计的既存偏差，本次不修但应记录在案。
3. 若新语法表两条命令都不返回 → 现有 reconciliation 的 `only_base`（`table_type_stats_service.py:672-680`，即"仅 information_schema 基线可见"）会捕获它们。**这是现成的、已在产的检测手段**，设计没有用上。

**照图施工整改**

请 O 在 §4.3 第 1 条后新增第 1a 条（可直接采用）：

> 1a. **候选集覆盖性证据与兜底。** 本设计不假定 `TDSQL_DISTRIBUTED BY` 语法族的逻辑表必然出现在 `/*proxy*/show table with shardkey` 的返回中——工程师附件只证明了旧 `shardkey=` 族会与二级分区表混返，未涉及新语法族。因此：
>
>    **(a) 证据前置。** 在 SIT 阶段，必须在内网具备新语法表的 TDSQL 实例上，先执行并留档三条命令的原始返回：`/*proxy*/show table with shardkey`、`/*proxy*/show table without shardkey`、`/*proxy*/show table with noshardkey_allset`，与同库 `SHOW TABLES` 结果比对，**确认新语法表落在哪一类**。该证据是 REQ-02 的**准入前提**：证据未取得时，D03 不得判通过。若内网当前无新语法表，则须在设计中显式声明"新语法族本版未经真实实例验证"，并在页面说明区标注该限制，不得以"已实现"表述。
>
>    **(b) 兜底覆盖（不依赖上述结论即可生效）。** 候选集在 Proxy 分片集合之外，追加"**当前库逻辑基线中、经现有 `_classify_subpartitions` 剔除物理子表后、未被三类 Proxy 命令中任何一类归属的表**"，即现有 reconciliation 的 `only_base` 集合（`table_type_stats_service.py:672-680`）。该子集同样送入 `SHOW CREATE TABLE` 识别，计入 `candidates`。此举复用已在产的差集计算，不新增 Proxy 命令、不改动三类基础统计口径与总表数不变量。
>
>    **(c) 记账口径澄清。** `secondary_partition_candidates` 的语义由"可用 Proxy 分片候选数"修改为"**本次纳入主表识别的候选数（Proxy 分片集合 ∪ only_base 集合）**"，§4.4 字段表同步改写。验收公式 `candidates = checked + unknown + unchecked`、`main <= checked` 不变。
>
>    **(d) 完整性状态收口。** §4.3 第 7 条的"不为 COMPLETE"条件中，增列一项：**若本库存在 `only_base` 非空且其中任一表未能取得 `SHOW CREATE TABLE` 结论，则该库不得判 COMPLETE，至多 PARTIAL。** 目的是杜绝"候选集本身不全，却因公式自洽而报完整"的情形。

同时在 §8 用例矩阵中增加一条：

|用例|构造|断言|
|---|---|---|
|SP-0X|库内含一张仅在 information_schema 基线可见、不在任何 Proxy 命令返回中的表，且其 DDL 含 `TDSQL_DISTRIBUTED BY HASH` + `TDSQL_PARTITION BY RANGE`|该表被纳入 `candidates` 并识别为 SECONDARY；`main` 计数含之；`check_state` 为 COMPLETE|

---

### P2-02（已裁决，待落文）REQ-01 在"最常用的那个报告"上给出的是"未关联实例"，与需求原文存在口径差；且现有 API 参数带有门禁副作用

**问题**

Mr.Linsang 的需求原文是"**在项目中所有出具的 html 报告中均要添加扫描或检查的实例连接名称**"。而 §3.1 的 H01 条（文件审核报告，`sql_audit.py::export_file_report_html`）给出的方案是：

> 当前文件审核前端未绑定连接，按**离线语义**处理

即该报告最终显示"未关联实例（离线文件审核）"。文件审核是本项目使用频率最高的入口之一，Mr.Linsang 拿到的报告里很可能**恰恰是这一张**没有实例名称。设计做了诚实的技术判断，但**没有把这个口径差显式提给需求方确认**——这是评审必须堵上的缺口。

**证据**

1. 后端 `FileAuditRequest` **已经接受** `connection_id`：`backend/api/sql_audit.py:136`（`audit_file`）、`:150-151`（透传给 `audit_service.audit_file_content`）。
2. 前端**没有传**：`frontend/static/js/app.js:525` 的请求体是 `{content, file_path, instance_type}`，界面上只有 `fileAuditInstType`（架构单选，`app.js:88`），无实例选择器。
3. **关键副作用**：`sql_audit.py:150` 写的是 `evaluate_gate=bool(request.connection_id)`。也就是说，**一旦前端开始传 `connection_id`，文件审核会同时被打开门禁评估**（`audit_service.py:236-237`），报告结论从"仅列违规"变成"带门禁通过/不通过"。这不是加一个页眉字段那么简单，会改变已在产报告的语义。设计文档没有提到这个耦合。

**影响**

- 若按 H01 现方案交付：需求"所有报告都有实例连接名称"在最常用入口上以"未关联"形式落地，验收时可能被判不满足。
- 若不加提示地改前端传 `connection_id`：文件审核报告**静默获得门禁结论**，属于计划外的功能变更，直接违反"务必严控代码修改范围，绝不能因为本次修改影响了项目的其他核心功能"。

**照图施工整改**

请 O 在 §3.1 H01 行下方新增"**H01 口径决策项（待 Mr.Linsang 裁定，裁定前 D02 不得开工 H01 部分）**"，并原样列出三个选项及其后果：

|选项|做法|报告呈现|代码影响面|风险|
|---|---|---|---|---|
|**A（设计现方案）**|不改前端，H01 按离线语义|"未关联实例（离线文件审核）"|仅报告生成器|需求口径差，须 Mr.Linsang 书面接受|
|**B（推荐）**|前端文件审核页新增**可选**"关联实例（仅用于报告标识）"下拉；后端**新增独立字段** `report_connection_id`，只用于取连接名称写入报告上下文，**不参与** `evaluate_gate`|选了则显示真实连接名称，未选显示"未关联实例（离线文件审核）"|`app.js` 文件审核表单 + `FileAuditRequest` 新增一个只读用途字段 + 报告生成器|需明确 `evaluate_gate` 仍为 `bool(request.connection_id)`，**不得**改成 `bool(report_connection_id)`，并加一条回归锁断言"传 `report_connection_id` 不产生 gate_result"|
|**C**|直接复用全局 `currentConnectionId`|显示当前页面选中的实例|最小|**不可采纳**：文件内容与该实例无任何关系，等于伪造扫描来源。设计 §1 已拒绝此路，我同意|

推荐 B。若 Mr.Linsang 选 B，请 O 在 §3.2/§3.3 的 `report_context.py` 契约中补明：文件审核的 `ReportContext.source_kind = 'FILE_OFFLINE'`，`connection_name` 可空；并在 §8 增加回归用例"**文件审核携带 `report_connection_id` 时 `gate_result` 仍为 null**"。若选 A，请在 §1 的"明确不做"栏写入"文件审核报告不显示实例名称（Mr.Linsang 已确认接受）"，避免 UAT 阶段重开此议题。

> **【裁决结果 · 2026-09-06 · Mr.Linsang】**
>
> 原文：**"通过指定实例连接的文件审核报告要显示实例名称，比如'在线元数据审核'产生的 HTML 报告。"**
>
> 裁决未采纳我提出的 B 方案，而是给出了一条更小爆炸半径的划线，**以"报告生成时是否已经指定了实例连接"为准**。落实口径与实施要求见 §7 裁决一。P2-02 由"待拍板"转为"待落文"，责任在 O。

---

### P2-03（已裁决，待落文）`GATEWAY_MAX_CONCURRENT=1` 且默认无排队，是一项新增的用户可见限制

**问题**

§6.3 配置表：

|配置|默认值|说明|
|---|---|---|
|`GATEWAY_MAX_CONCURRENT`|1|单机所有应用 worker 共用槽；**默认无排队，忙时 429**|

结合 §6.3 其他行：`GATEWAY_ANALYSIS_TIMEOUT_SECONDS=540`、`GATEWAY_PROCESSING_BUDGET_SECONDS=600`。也就是说，**第二个用户在最坏情况下会连续约 9—10 分钟收到 429**，且没有排队位置提示。

当前系统在这条路径上**没有任何并发限制**（`backend/api/gateway_log.py:80` 的 `/upload` 无信号量、无锁）。这是一项**从无到有**的功能性收紧。

**证据**

- 现状无限制：`gateway_log.py:80-92` 直接 `await file.read()` → `analyze_log`，无并发控制代码。
- §6.4 第 3 条明确"收体期间共用跨 worker 非阻塞槽……**不是进程内 Semaphore**"，并明确"多主机部署为每主机一槽"——多 worker 部署下总容量仍是每主机 1。

**影响**

从"两个人可以同时传（虽然可能都失败）"变成"第二个人被明确拒绝最长约 10 分钟"。这在功能上是正确且必要的资源保护，但它**改变了用户可感知的行为**，属于必须由需求方知情接受的范畴，不能由设计单方面定版。

**照图施工整改**

请 O 在 §6.3 表下新增一段（可直接采用）：

> **并发策略的知情项。** `GATEWAY_MAX_CONCURRENT=1` 是本版**新增**的用户可见限制（现网该路径无并发控制）。在最坏情况下，第二位用户从提交到收到 429 之间可能持续约 10 分钟（受 `GATEWAY_PROCESSING_BUDGET_SECONDS=600` 约束）。该取值的依据是：单次分析为 CPU 密集且需要 2 GiB 级临时空间（`GATEWAY_MIN_FREE_BYTES`），并发 2 会同时抬高内存、磁盘与超时风险，本版不引入队列组件（§1 已声明不引入 Celery/Redis）。
>
> 提请 Mr.Linsang 在三者中确认其一：
> **(a)** 接受 429 且无排队（设计现方案）；
> **(b)** 接受 429，但 429 响应体附带"预计可重试时间"（后端按当前占用槽的剩余预算估算，`Retry-After` 头 + 中文提示），前端据此显示"当前有分析任务进行中，约 N 分钟后可重试"——**推荐**，代码增量仅在 429 分支，不引入队列；
> **(c)** 放宽为 2 并发——**不推荐**，须先补充内存/磁盘峰值实测证据，否则等于用未验证的容量假设换体验。
>
> 无论选哪一项，429 文案必须明确"**您的文件未被处理，请稍后重传**"，不得让用户误以为已进入后台队列。

推荐 (b)。并请在 §8 网关用例矩阵中增加"**并发 2 请求：第二个必须收到 429 且响应体含 `X-Request-ID`**"（§6.4 第 1 条已要求 413/429 带 `X-Request-ID`，但用例矩阵未见对应断言）。

> **【裁决结果 · 2026-09-06 · Mr.Linsang】**
>
> 原文：**"网关日志分析要限成同时只能一个人跑。"**
>
> `GATEWAY_MAX_CONCURRENT=1` **予以批准**，按设计执行；(c) 放宽为 2 并发不采纳。429 的提示形态属同一裁决下的实现细节，Mr.Linsang 未另行指定，按本报告推荐记为 (b)。详见 §7 裁决二。P2-03 由"待拍板"转为"待落文"，责任在 O。

---

### P3-01（建议整改）超时与预算数值分散在三处，缺一张收口的链路表

**问题**

本次涉及至少 5 个时间参数，分散在 §6.3（配置表）、§6.4 第 8 条（前端）、§6.7（Nginx）：

|层级|参数|值|出处|
|---|---|---|---|
|浏览器总等待|前端保护|990 s|§6.4 第 8 条|
|Nginx|`proxy_read_timeout` / `proxy_send_timeout`|660 s|§6.7|
|应用协调预算|`GATEWAY_PROCESSING_BUDGET_SECONDS`|600 s|§6.3|
|子进程|`GATEWAY_ANALYSIS_TIMEOUT_SECONDS`|540 s|§6.3|
|应用收体|`GATEWAY_UPLOAD_RECEIVE_TIMEOUT_SECONDS`|300 s|§6.3|

这些数值本身是自洽的（540 < 600 < 660 < 990；300 + 600 = 900 < 990），**但设计里没有任何一处把它们排在一起**。Q 施工时若单独调整其中一个（例如把子进程超时从 540 调到 700 以适配 200 MiB 样本），很容易破坏 540 < 600 < 660 的包含关系而不自知——那会导致 Nginx 先于应用超时，用户拿到 504 HTML 而后端仍在跑，是最难排查的一类现象。

**照图施工整改**

请 O 在 §6.3 配置表之后新增"**§6.3a 超时链不变式**"（可直接采用）：

> 各层时限必须满足下列不变式，**任何一个数值调整都必须同时复核整条链**：
>
> `RECEIVE_TIMEOUT(300) + PROCESSING_BUDGET(600) ≤ 前端总等待(990)`
> `ANALYSIS_TIMEOUT(540) < PROCESSING_BUDGET(600) < Nginx proxy_read_timeout(660) < 前端总等待(990)`
>
> 语义边界（不得混淆）：`client_body_timeout=60s` 是**相邻读间隔**，不是"上传须在 60 秒内完成"；`proxy_read_timeout=660s` 是**代理等待上游响应**，不是全链路 SLA；`GATEWAY_PROCESSING_BUDGET_SECONDS` 是**阶段启动前检查的软预算**，不是数据库 I/O 硬上界；应用的 300 秒收体时限**不含** Nginx 前置缓冲耗时。
>
> 启动期校验：后端读取配置后必须校验上述两条不等式，不满足则**启动失败并指明冲突的两个配置名**（与 §6.3 "非法值失败并指明配置名"一致）。若部署链路上存在负载均衡或安全网关有更短超时，须在部署路径清单中登记其实际值并纳入同一不等式核验。

---

### P3-02（建议整改）§3.4 的 nonce/CSP 约束只点名了网关出口，日常巡检出口同样受约束

**问题**

§3.4 写：

> 仍由既有 `_strip_inline_handlers` 和 nonce 逻辑处理响应，保留 90 秒一次性共享票据及不透明源 sandbox。不得为显示名称放松 CSP。

这段的上下文是"新网关模板"（H08）。但 H07（`daily_inspect_service.py::generate_comparison_html_report`）的出口同样带 nonce 与 CSP 处理：`backend/api/daily_inspect.py` 中 nonce 出现 6 处、CSP/Content-Security-Policy 出现 5 处。H07 要在页眉插入实例连接名称，属于同类改动，却没有被这条安全约束显式覆盖。

**影响**

风险不高（约束在代码层客观存在，Q 大概率不会破坏），但 SIT 验收时会缺一条明确的检查项，属于设计完备性缺口。

**照图施工整改**

请 O 将 §3.4 该句改为：

> 涉及 nonce / CSP / 一次性票据 / sandbox 的 HTML 出口共两处：**H07（`backend/api/daily_inspect.py`，nonce 6 处、CSP 5 处）与 H08（网关报告）**。两处均仍由既有 `_strip_inline_handlers` 与 nonce 逻辑处理响应，保留 90 秒一次性共享票据及不透明源 sandbox。新增的实例连接名称一律走 HTML 文本转义路径注入，**不得**为显示名称放松任一处 CSP、不得新增 inline handler、不得新增 `unsafe-inline`。§8 须对这两处分别断言"改后响应头 CSP 与改前逐字节一致"。

---

### P3-03（建议整改）`is_multi_table_update` 的"兼容老调用者"是一句无谓的自我设限

**问题**

§5.2 开头写：

> 保留 `ParsedSQL.is_multi_table_update` 兼容老调用者（其历史语义也包含 DELETE）

**证据**：全仓检索，该字段**只有一个消费者**——`backend/engine/rules/dml.py:267` 的 `if parsed.is_multi_table_update:`，就是 R043 自己。不存在第二个"老调用者"。

**影响**

不构成缺陷，但会造成两处不必要的成本：一是让 Q 误以为需要维护一份双写的兼容语义（§5.2 第 3 条要求 `_parse_update/_parse_delete` "使用该结构同步兼容布尔值"），二是让 SIT 需要额外验证"双写一致性"这个本可不存在的不变量。多维护一条冗余状态，本身就是新的失步风险来源。

**照图施工整改**

请 O 将该句改为：

> `ParsedSQL.is_multi_table_update` 在全仓仅有一个消费者：`backend/engine/rules/dml.py:267`（R043 自身）。本版将其保留为 `dml_target` 的**只读派生视图**，定义为 `is_multi_table_update := (dml_target.status == RESOLVED and dml_target.is_multi_table is True)`，**不再有任何独立赋值点**（`_regex_pre_parse` 中的两段正则赋值按 §5.2 第 1 条删除，`_parse_update/_parse_delete` 不再单独写这个布尔值）。R043 改为直接读 `dml_target`。保留该字段仅为减少外部快照/序列化结构的变动面，其爆炸半径为零，**不需要维护双写一致性，也不需要为此设计双写回归用例**。

---

## 5. 需求覆盖对照

|需求|Mr.Linsang 原文要点|设计落点|覆盖判断|
|---|---|---|---|
|①|所有出具的 HTML 报告均添加扫描/检查的**实例连接名称**|§3 REQ-01，H01—H14 共 14 个出口 + `report_context.py` + `v14/140`|**基本覆盖，口径已裁定**。我逐条核对了 H01—H14 与仓库实际 HTML 出口，**未发现遗漏的出口**；H01/H02 的显示口径已由 Mr.Linsang 于 2026-09-06 裁决（§7.1），按"生成时是否已指定实例连接"划线|
|②|"深度诊断">"表类型统计"增加"**二级分区主表**"字段，统计库内二级分区主表数量；参考工程师附件，**并须与 TDSQL 官方文档交叉核对**|§4 REQ-02，新增 `tdsql_table_shape.py` + `v14/141` + 6 个字段 × 2 张表|**语义覆盖，候选集证据待补**。两层结构定义、广播优先级、拒绝 `SUBPARTITION_NAME` 口径均与附件一致，且 §2.2 明确做了官方交叉核对并划清"官方能力 ≠ 项目治理规则"的边界；缺口是候选集来源未经证据支撑（P2-01）|
|③|附件 CREATE 语句误触发 R043，**是核心审核算法缺陷，要排查原因、认真修复**|§5 REQ-03，`dml_target` 结构 + 三出口收口|**根因定位正确，门闩设计有洞**。根因（`_regex_pre_parse` 跨字段定义捕获 `ON UPDATE … CHARACTER SET`）我已独立复现确认；但 §5.2 第 5 条选错了防御门闩，误报可原样复发（P1-01）|
|④|内网大网关日志分析上传失败，**分析失败原因并修复**|§6 REQ-04，专属中间件 + 流式落盘 + 受控子进程 + `v14/142`|**覆盖充分**。设计不仅指出内网诊断报告漏掉的应用层 50 MiB 限额，还纠正了该报告的三处事实错误；并发策略已获 Mr.Linsang 批准（§7.2），剩余缺口是超时链需收口（P3-01）|

关于②的"必须与官方文档交叉核对"这条要求，我单独确认：设计 §2.2 做到了，且做法是对的——**采纳工程师附件的分类语义，拒绝其 shell grep 实现**（注释、字符串、反引号、换行、可执行注释都会污染全局 grep 命中），改为在词法界定的表尾片段上做状态机识别。同时明确"官方文档里有自动维护分区的能力"不等于"项目允许 MAXVALUE"——后者是 Mr.Linsang 已定版的治理规则，两者不混用。这条边界划得准，我不改。

## 6. 整改清单与二轮准入条件

|编号|级别|一句话|责任|二轮准入判据|
|---|---|---|---|---|
|P1-01|P1|R043 门闩不得依赖 `sql_type`；改为以 `dml_target` 三条件为唯一开火条件，并禁止回退出口复用 `_detect_sql_type_regex`|O 改设计|§5.2 第 5 条与 §5.3 按整改文字改写；§8 补 DML-06—DML-10 五条用例，且 06/07/08 三出口分别断言|
|P2-01|P2|候选集覆盖性无证据；补 `only_base` 兜底 + 证据前置 + 完整性状态收口|O 改设计|§4.3 新增 1a 条；§4.4 `candidates` 语义改写；§8 补 SP-0X 用例|
|P2-02|P2|H01 文件审核报告"未关联实例"的口径差；且 `connection_id` 带门禁副作用|**已裁决** → O 落文|按 §7 裁决一落实 (a)(b)(c)(d) 四项：§1"明确不做"补记、§3.1 H02 补四条实现约束与通则、§8 补 RPT-01/RPT-02 两条用例、遗留约束入档|
|P2-03|P2|`GATEWAY_MAX_CONCURRENT=1` 无排队是新增用户可见限制|**已裁决** → O 落文|按 §7 裁决二落实：§6.3 知情项改写为"已批准"，429 文案按 (b) 定稿；§8 补并发 429 用例（含 X-Request-ID 断言）|
|P3-01|P3|超时链五个数值分散三处，缺不变式与启动期校验|O 改设计|新增 §6.3a 不变式表 + 启动期校验要求|
|P3-02|P3|nonce/CSP 约束漏点名 H07|O 改设计|§3.4 同时点名 H07 与 H08，并要求 CSP 头逐字节比对断言|
|P3-03|P3|`is_multi_table_update` 的"兼容老调用者"是自我设限|O 改设计|改为只读派生视图表述，明确不需要双写回归|

**二轮评审准入条件**（三条全部满足我才开二轮）：

1. P1-01 按整改文字改写完毕——这是唯一的**阻断项**，不改则本次 REQ-03 等于没修干净；
2. P2-01 的 1a 条（含证据前置与 `only_base` 兜底）写入设计；
3. P2-02、P2-03 的裁决已按 §7 写入设计文档（裁决内容本身我不评价，只核验"已落文、且落文内容与裁决一致"）。**裁决已于 2026-09-06 取得，此条现在只剩"落文"一半未完成。**

P3 三条不构成阻断，但建议与上述一并改完，避免二轮再开一次。

## 7. Mr.Linsang 裁决（2026-09-06，已拍板）

本节记录需求方对 P2-02、P2-03 的正式裁决，以及我核实后给出的落实口径。**裁决内容我不评价，只负责把它翻译成 O 可以直接照抄进设计的文字，并核验其技术前提成立。**

### 7.1 裁决一（对应 P2-02）：通过指定实例连接的文件审核报告要显示实例名称

**裁决原文**

> 通过指定实例连接的文件审核报告要显示实例名称，比如"在线元数据审核"产生的 HTML 报告。

**我的理解与划线**

这条裁决没有采纳我建议的 B 方案（给离线文件审核加一个可选实例下拉），而是给了一条**判据更清晰、改动面更小**的界线：

> **报告生成时已经指定了实例连接的 → 必须显示真实的实例连接名称；生成时根本没有指定实例连接的 → 显示"未关联实例（离线文件审核）"。**

按此判据核对当前两个入口：

|入口|是否指定实例连接|裁决后应显示|本版是否改动|
|---|---|---|---|
|**H02 在线元数据审核**（`extract_and_audit` → `export_extracted_report_html`）|**必须指定**，`connection_id` 为必填参数|**真实实例连接名称**|**是**，本裁决的直接对象|
|**H01 纯离线文件审核**（`/api/v1/audit/file`）|无从指定，界面上没有实例选择|"未关联实例（离线文件审核）"|**否**，不新增实例选择器|

**技术前提核验（我逐条实证，结论：H02 可零风险落地）**

|事实|证据|意义|
|---|---|---|
|`connection_id` 是必填，缺失直接 400|`sql_audit.py:260-263`：`if not connection_id: raise HTTPException(400, "请选择目标数据库实例")`|该入口**不存在**"无实例"的情形，不需要设计降级分支|
|连接名称在提取时点已在手|`sql_audit.py:286`：`conn_info.get('name', 'TDSQL')` 已被写入生成 SQL 的注释头|名称可得，无需新增查询|
|名称已在旁路被持久化|`sql_audit.py:389`：快照写入 `"connection_name": conn_info.get("name", "")`|证明"提取时冻结名称"这个动作在本代码路径上**已经在做**，只是没有进报告|
|报告记录已落 `connection_id`|`sql_audit.py:369-379`：`_save_audit_history(..., connection_id=connection_id, db_name=target_db)`|历史记录可回溯归属|
|**H02 传 `connection_id` 不会触发门禁**|`sql_audit.py:356-361` 调用 `audit_file_content` 时**未传** `evaluate_gate`，取签名默认 `False`（`audit_service.py:275`），返回元组第三位被显式丢弃（`results, summary, _, ictx`）|**这是本裁决能零风险落地的关键。**P2-02 指出的门禁耦合 `evaluate_gate=bool(request.connection_id)` **只存在于 `/api/v1/audit/file` 这一条路由**（`sql_audit.py:150`），H02 不经过它|

**要求 O 落文（四项，可直接采用）**

**(a) §1 结论与范围，REQ-01 行的"明确不做"栏追加：**

> 纯离线文件审核（`POST /api/v1/audit/file`）产出的报告不显示实例连接名称，统一显示"未关联实例（离线文件审核）"；本版**不为该入口新增实例选择器**。依据：Mr.Linsang 2026-09-06 裁决以"报告生成时是否已指定实例连接"为判据，该入口不指定实例连接，不在需求 ① 的覆盖范围内。

**(b) §3.1 H02 行，补充四条实现约束：**

> 1. **取名时点**：在 `extract_and_audit` 进入元数据提取之前，从 `registry.get_saved(connection_id)` 返回的 `conn_info['name']` **冻结**连接名称，与既有 `scan_started_at` 同一时点取值。
> 2. **禁止反查**：HTML 导出（`export_extracted_report_html`）**不得**按 `connection_id` 到实例表反查名称——连接可能已被改名或删除，反查会让同一份历史报告在不同时间显示不同的实例名，属于伪造扫描来源。名称随报告一起持久化，导出时只读不查。
> 3. **禁止推断**：不得从生成的 SQL 文件名、注释头或 `host:port` 推断名称（与 H03 的既有缺陷同类，见 §3.1 H03）。
> 4. **门禁不变量**：本项改动**不得**为 H02 引入门禁评估。`extract_and_audit` 对 `audit_file_content` 的调用必须保持不传 `evaluate_gate`（默认 `False`），返回的 `gate_result` 恒为 `None`。

**(c) §3.1 在 H01—H14 表前增加一条通则：**

> **实例名称显示通则**：报告生成时已指定实例连接的出口，一律显示提取/扫描时点冻结的真实连接名称；生成时未指定实例连接的出口，一律显示"未关联实例（<场景>）"。二者不得互相冒充：不得用当前页面选中的实例、`host:port`、主机名或文件名充当连接名称。

**(d) §8 增加两条回归用例：**

|用例|构造|断言|
|---|---|---|
|RPT-01|在线元数据审核对连接 X（名称 `NX`）执行一次，得 `report_id`；随后将连接 X **改名**为 `NY`；再导出同一 `report_id` 的 HTML|两次导出页眉均显示 `NX`（证明冻结生效、未反查）|
|RPT-02|在线元数据审核正常执行一次|响应与持久化中 `gate_result` 恒为 `None`；报告不含门禁通过/不通过结论（**防止后续有人"顺手"给 H02 加上 `evaluate_gate`**）|

**遗留约束（请 O 单列一条，供后续版本引用，本版不实施）**

> 若将来要为纯离线文件审核增加"关联实例"能力，**必须新增独立字段**（如 `report_connection_id`）且只用于报告标识，**绝不能复用 `connection_id`**——`sql_audit.py:150` 的 `evaluate_gate=bool(request.connection_id)` 会使文件审核报告静默获得门禁结论，属于计划外的功能语义变更。

### 7.2 裁决二（对应 P2-03）：网关日志分析限成同时只能一个人跑

**裁决原文**

> 网关日志分析要限成同时只能一个人跑。

**裁决落实**

`GATEWAY_MAX_CONCURRENT=1` **予以批准**，按设计 §6.3/§6.4 第 3 条执行（跨 worker 的 advisory file lock，非进程内 Semaphore）。我在 §4 P2-03 中列出的 (c) 放宽为 2 并发**不采纳**，相应地也不需要补充内存/磁盘峰值实测来支撑放宽。

429 的**提示形态**属于同一裁决之下的实现细节，Mr.Linsang 未另行指定。我按本报告 §4 P2-03 的推荐记为 **(b)**：仍然直接拒绝、不排队，但把拒绝说清楚。**这是我在裁决范围内所作的默认取值，不是 Mr.Linsang 的原话**；若 Mr.Linsang 更倾向 (a) 纯 429 无附加提示，一句话即可改回，不影响本设计任何其他部分。

**要求 O 落文（三项，可直接采用）**

**(a) §6.3 配置表下方的知情项段落，按已批准改写：**

> **并发策略（Mr.Linsang 2026-09-06 已批准）。** `GATEWAY_MAX_CONCURRENT=1` 是本版**新增**的用户可见限制——现网该路径（`backend/api/gateway_log.py:80-92`）无任何并发控制。最坏情况下第二位提交者从提交到收到 429 之间可能持续约 10 分钟（受 `GATEWAY_PROCESSING_BUDGET_SECONDS=600` 约束）。取值依据：单次分析为 CPU 密集且需 2 GiB 级临时空间（`GATEWAY_MIN_FREE_BYTES`），并发 2 会同时抬高内存、磁盘与超时风险；本版不引入队列组件（§1 已声明不引入 Celery/Redis）。该限制已获需求方明确批准，**不再作为待确认项**。

**(b) 429 响应契约定稿：**

> 忙时返回 429，响应须同时具备：`Retry-After` 头（按当前持槽任务的剩余预算估算，取整到秒，估不出时取 `GATEWAY_PROCESSING_BUDGET_SECONDS`）、`X-Request-ID` 头（§6.4 第 1 条已要求）、以及中文提示正文：
>
> > "当前已有一个网关日志分析任务正在运行，本功能同一时刻仅允许一个任务。**您的文件未被处理，请约 N 分钟后重新上传。**"
>
> 文案**必须**包含"未被处理、请重新上传"的语义，不得使用"已提交""排队中""稍后将自动处理"等让用户误以为进入后台队列的措辞。前端按 §6.4 第 7 条的 `handledHttpError` 路径展示一次精确提示，不走全局 5xx 通用通知。

**(c) §8 网关用例矩阵增加两条：**

|用例|构造|断言|
|---|---|---|
|GW-C1|并发提交 2 个上传请求|第二个收到 **429**，响应含 `Retry-After` 与 `X-Request-ID`，正文含"未被处理"语义；第一个不受影响、正常完成|
|GW-C2|前置过滤拒绝的请求（如超限 413）|**槽必须被释放**（§6.4 第 3 条已要求"前导过滤拒绝请求也要释放槽"），后续请求不被误挡|

### 7.3 裁决对本轮结论的影响

两项裁决**不改变**本报告的总体结论。P1-01 仍然是唯一的阻断项，与本次裁决无关。P2-02、P2-03 从"待 Mr.Linsang 拍板"转为"待 O 落文"，二轮评审时我按 §7.1(a)—(d)、§7.2(a)—(c) 共七项逐条核验落文是否与裁决一致。

## 8. 评审边界声明

1. 本轮**未修改任何代码**，符合 Mr.Linsang "切记你不要动代码"的要求。本文档是本轮唯一交付物。裁决记录（§7）同样只落在本文档，未触碰设计文档与代码——按 Mr.Linsang 指示，设计文档由 O 修订。
   - §7 中 429 提示形态取 (b) 是**我在裁决范围内所作的默认取值**，不是 Mr.Linsang 的原话，已在 §7.2 就地标注，避免二轮把我的推荐误当作需求方要求。
2. 本轮所有复现均在本地只读环境完成：R043 使用附件原文 SQL 做解析与规则执行，**未向任何数据库执行 DDL**；`_detect_sql_type_regex` 的误判以照抄该函数逻辑的独立脚本实测，未改动仓库文件。
3. 本轮**没有**内网 TDSQL 实例可用，因此 REQ-02 的 Proxy 命令返回口径、REQ-04 的 71 MiB 真实样本耗时，**我都无法实测**，只能以设计自证与 SIT 阶段证据前置的方式收口——P2-01(a) 与 §9 容量门禁就是为此设的闸。我不会把"设计写了"说成"已验证"。
4. 我复核了设计对内网诊断报告的三条"不认可"，O 的判断是对的，其中"LONGTEXT 约 16MB / MEDIUMTEXT 约 64MB"确实说反了（MySQL 官方为 MEDIUMTEXT < 2^24、LONGTEXT < 2^32）。该纠正应保留在设计中，避免后续引用错误数据。

---

评审人：智能体 A（ClaudeA）
评审对象：`docs/DETAIL-v1.6.3.4-报告实例标识与分区统计及审核网关修复.md` Rev.A（commit `402acc2`）
提交给：Mr.Linsang
