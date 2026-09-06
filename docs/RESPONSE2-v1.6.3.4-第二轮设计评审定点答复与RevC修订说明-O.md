# v1.6.3.4 第二轮设计评审定点答复与 Rev.C 修订说明

提交给：Mr.Linsang

编制：O；日期：2026-09-06

复核基线：`main@84f0ddf`，业务代码仍为 v1.6.3.2；本轮同步到的新增提交仅为 A 的文档。

评审输入：[A 第二轮评审报告](./REVIEW2-v1.6.3.4-报告实例标识与分区统计及审核网关修复设计第二轮评审报告-ClaudeA.md)。修订交付：[详细设计 Rev.C](./DETAIL-v1.6.3.4-报告实例标识与分区统计及审核网关修复.md)。两轮 A 原报告及 O 首轮答复保持不变。

## 1. 处理结论与 Mr.Linsang 裁定

两项新 P2 均认可并已修订；顺带采纳同一报告的 P3-04 文档提示，不改变已定方案方向。本轮只修改详细设计和本答复，不改业务代码、测试代码、配置、迁移文件、数据库或内网环境。

Mr.Linsang 本轮直接要求“保持 180 秒不变”，已落文为：

- `TOTAL_BUDGET_SECONDS=180` 继续是基础采集、独立目录、主表 DDL 共用的软预算；不在主表阶段、换库、换层时重新计时，不额外增加 300 秒。
- `MAX_PARENT_DDL_PER_RUN=5000` 保持不变，本轮没有获授权放宽这一护栏，也不新增并行扫描/后台续扫。
- 若在预算/护栏内不能全量判明，结果如实为下界、已确认 0/未完成或未知，不能冒称精确总数。保持预算并不代表已测得所有实例能完成，也不豁免容量实测。
- 不再把“180 秒还是 300 秒”列为待 Mr.Linsang 决策项；剩下待回填的是实际规模、耗时及覆盖范围。

|评审项|是否认可|Rev.C 落点|当前状态|
|---|---|---|---|
|P2-04：可靠非 DML 头开放式枚举|认可|§5.2.1 的 36 项闭集；DML-18/19|设计已补齐，待 A 定点确认；尚未实施/验证修复|
|P2-05：候选扩大后容量影响未交代|认可|§4.3 四层调度；§8.2.1 容量核算；PAR-19—21|180 秒裁定已落文，真实内网容量证据待责任方回填|
|P3-04：固定并发项易误调|认可|§6.3/§6.7、GW-C4|明确固定 1、不可调及切生产前校验，行为不变|

A 已确认第一轮七项关闭、P1-01 阻断解除，本轮不重新评价那些已关闭项；按 A 第二轮 §5 的要求，仅提交这三处落文定点核验，不需要第三轮完整方案评审。不能把本答复当作 Q 已完成开发或 A 已验收新实现。

## 2. P2-04：明确闭集，避免以 E999 替代 R043 误报

### 2.1 认可依据

Rev.B 用“CREATE/ALTER/INSERT/SELECT 等”描述可靠非 DML，又不允许对未覆盖头猜 NOT_APPLICABLE，确实给 Q 留下不一致解释空间。A 的 Command 路径风险经真实 `sqlglot.parse_one`、`SQLParser.parse`、`RuleChecker.audit_sql(..., instance_type='distributed')` 复核成立：RENAME/OPTIMIZE/CALL 等没有原有 E999，不能仅因本次 R043 改动而新增 E999。

本轮只在本地解析文本，没有连接目标实例或执行其中的 SQL。基线为 sqlglot 30.14.0；36 项代表输入和结果见 §5。本轮还试验 DO/RELEASE SAVEPOINT/XA 三个闭集外探针，均已有 ParseError/E999；未据此扩展支持声明或改动名单。闭集不是整个 MySQL 语法目录，未知输入继续按完整性合同处理。

### 2.2 已落文的施工要求

1. 采用 A 建议的 36 项显式闭集 `R043_NON_TARGET_HEADS`，不使用“等”，所有回退出口共用一份不可变集合，测试语料覆盖集合必须相等。定义只用于证明“不属于 R043 的直接顶层 UPDATE/DELETE”，不是全规则放行，也不是 TDSQL 方言开放清单。
2. 命中闭集 → NOT_DML/NOT_APPLICABLE/false；UPDATE/DELETE → 目标解析；WITH → 先完整定位外层再套同一规则；未知/不可靠头 → UNKNOWN。原有 parse_error/KFN/E999 及其他规则一律不被名单抹掉。
3. 增加 DML-18：修改代码前冻结每句基线，改后逐句核对 E999 与其他规则；总体 E999 不增加只是附加检查，不能用删除别句错误抵消新增误报。真实 multi 必须仍报 R043，附件仍保留其他原违规。正常及注入 Command/ParseError 各与同路径基线比较。
4. 增加 DML-19，补到可施工的词法细节：锁定版词法器把 `LOCK TABLES` 合为一个 COMMAND token，仅比 token.text == LOCK 会失败；需在确认未加引号的关键词 token 边界内取得首个裸词。字符串 `'SELECT'` 与反引号标识符不能冒充头。
5. 通用 Alias/Column/Literal 不自动等于“可靠非 DML AST”。本轮 FLUSH/SAVEPOINT/RESET 的示例根节点实际为 Alias，因此补明它们仍需独立词法闭集判断；强类型正常 UPDATE/DELETE 不增加重复解析。

参考 [MySQL 官方语句目录](https://dev.mysql.com/doc/refman/8.0/en/sql-statements.html) 仅核对语句头归属；TDSQL 建表/分区仍按原设计的官方依据和实例版本边界处理，不因为新语法返回 Command 就宣布整条语句支持或豁免 R121。

## 3. P2-05：保持 180 秒，补顺序与真实容量核算

### 3.1 认可并落实的内容

旧预算由 `table_type_stats_service.py::run_stats` 在取得槽位后、首次目标查询前建立；`TOTAL_BUDGET_SECONDS=180`、`COMMAND_READ_TIMEOUT=30` 仍是当前代码值。新指标只能使用基础/目录之后剩余的软预算，不能把 180 秒误理解为每个步骤各有一份。

Rev.C 采用全实例四层顺序：旧分片 S → only_base → 仅独立目录可见 → 剩余单表/广播。按集合差集消除重叠，层内精确库表名排序，四层并集必须等于 C；不按某一库先扫完全部四层。预算不足时停止后续 I/O，未知/未查仍分别计数，不缩小 candidates 或改变 COMPLETE 的判据。

Mr.Linsang 已确定 180 秒不变。Rev.C §8.2.1 不是重新请示预算，而是要求给出此预算下实际可覆盖范围：同一目标范围的 L/P/B 去重并集与四层规模、20—50 张分层样本、基础/目录/DDL 各阶段耗时、固定 180 秒完整入口实扫结果；将精确数量验收和诚实下界显示验收分开。

具备内网权限的执行方/DBA 提供只读数据，Q 整理核算和自测，A 在 D03 无限定验收前核对，O 在后续 UAT 核对展示。本轮无内网连通/实测数据，因此这些数据明确待回填，不写“容量已验证”或“必然查不完”。

### 3.2 未直接照搬的两处论证

**第一，分层不能保证最紧下界。** 旧分片优先是合理启发式，但若某环境的大部分现代主表恰在旧单表结果里，优先旧分片未必比其他顺序找到更多主表。没有实测不能写“先验命中概率最高”“下界尽可能接近真值”作为保证。Rev.C 只承诺确定性顺序和不漏记候选，保留完整性告警。

**第二，information_schema 基表 COUNT 不是未经证明的候选上界。** 设计之所以使用 `C=L∪P∪B`，就是没有证明三个来源完全一致。原 COUNT 既可能含物理子表，也不能保证包含独立目录/Proxy 的全部逻辑对象；既然原来允许 only_proxy，就不能单靠这个 COUNT 当完整规模。

替代为在同一账号、同一目标范围记录实际三集合并集；只在各源完整时使用 `max(|L|,|P|,|B|) <= |C| <= |L|+|P|+|B|`，缺源时标未知。抽样也不采用任意 20 张均值简单乘法冒充结论：按库/层/DDL 体量采样，记录失败和连接成本，补基础/目录耗时；均值与 P95 情景仅作规划，不是全程 P95 或完成承诺，最终用原配置实扫核验。

设计中的算例仅说明量纲：若基础/目录/其他开销共 45 秒，余量为 135 秒；2000 张候选每张均值 0.05 秒规划为 145 秒，每张 0.10 秒则规划为 245 秒。**这些值是明确标注的假设，不是内网测量结果。** 不据此改变 Mr.Linsang 已定预算。

## 4. P3-04：同步澄清固定并发，不改行为

认可“运维可能误把它当可调项”的文档风险。`GATEWAY_MAX_CONCURRENT=1` 仍保留配置名用于日志/capabilities 自证，但明确为固定、不可调；0/2 等非法值会拒绝启动，应在切换生产服务之前校验拟发布配置，而不是停服后才试。

需求若改为并发大于 1，需要先取得 Mr.Linsang 的新裁定并评审容量/协调方案，不是改一个参数。实际单主机、多 worker、共享锁路径仍须发布时核验；仓库 systemd 与 Nginx 模板本身不能证明内网现状已经核实。本次仅补说明与 GW-C4 断言，不扩展实施范围。

## 5. 本轮只读基线语料（供 DML-18 冻结参考）

下面是原代码的自然解析路径结果，不是修复后期望全部 PASS；`—` 表示没有违规。Q 实施前仍需对实施基线重新冻结 hash/完整性字段与规则级别，并补三出口测试。语句中的建表、授权、锁表、事务等**均未向数据库执行**。

|头|代表 SQL|sqlglot 根节点|当前规则 ID|
|---|---|---|---|
|SELECT|`SELECT 1`|Select|R051|
|INSERT|`INSERT INTO t(id) VALUES (1)`|Insert|—|
|REPLACE|`REPLACE INTO t(id) VALUES (1)`|Command|—|
|CREATE|`CREATE TABLE t (id INT) TDSQL_DISTRIBUTED BY HASH(id)`|Command|R003/R004/R005/R028/R029/R036/R037/R054/R077|
|ALTER|`ALTER TABLE t REORGANIZE PARTITION p0 INTO (PARTITION p1 VALUES LESS THAN (10))`|Command|—|
|DROP|`DROP TABLE t`|Drop|R073|
|TRUNCATE|`TRUNCATE TABLE t`|TruncateTable|R075|
|RENAME|`RENAME TABLE a TO b`|Command|—|
|OPTIMIZE|`OPTIMIZE TABLE t`|Command|—|
|ANALYZE|`ANALYZE TABLE t`|Analyze|—|
|REPAIR|`REPAIR TABLE t`|ParseError|E999_SYNTAX_ERROR|
|CHECK|`CHECK TABLE t`|ParseError|E999_SYNTAX_ERROR|
|LOCK|`LOCK TABLES t WRITE`|Command|R046|
|UNLOCK|`UNLOCK TABLES`|Command|—|
|GRANT|`GRANT SELECT ON db.* TO 'u'@'%'`|Command|R051/R074|
|REVOKE|`REVOKE SELECT ON db.* FROM 'u'@'%'`|Command|R051/R074|
|FLUSH|`FLUSH TABLES`|Alias|R046|
|SET|`SET @a=1`|Set|—|
|SHOW|`SHOW TABLES`|Show|—|
|DESC|`DESC t`|Describe|—|
|DESCRIBE|`DESCRIBE t`|Describe|—|
|EXPLAIN|`EXPLAIN SELECT * FROM t`|Describe|R051|
|CALL|`CALL sp_test(1)`|Command|—|
|HANDLER|`HANDLER t OPEN`|ParseError|E999_SYNTAX_ERROR/R045|
|LOAD|`LOAD DATA INFILE '/tmp/sample.csv' INTO TABLE t`|ParseError|R042（原 parse_error 仍非空，不假称无错）|
|START|`START TRANSACTION`|Transaction|R069/R071|
|BEGIN|`BEGIN`|Transaction|R069/R071|
|COMMIT|`COMMIT`|Commit|—|
|ROLLBACK|`ROLLBACK`|Rollback|—|
|SAVEPOINT|`SAVEPOINT sp1`|Alias|—|
|USE|`USE db`|Use|—|
|KILL|`KILL QUERY 12345`|Kill|—|
|RESET|`RESET MASTER`|Alias|—|
|PREPARE|`PREPARE s FROM 'SELECT 1'`|Command|R001/R002|
|EXECUTE|`EXECUTE s`|Command|—|
|DEALLOCATE|`DEALLOCATE PREPARE s`|ParseError|E999_SYNTAX_ERROR|

这些结果说明：白名单是 R043 的适用性判断，不是语法完整性和安全审核的放行名单。原 REPAIR/CHECK/HANDLER/DEALLOCATE 的 E999、LOAD 的 parse_error/R042 与其他规则都应保持，不用“净效果不增加”作删除旧错误的理由。

## 6. 交付与剩余边界

已完成本轮文档修订、当前基线只读复现和字段/用例一致性核对；A 两份原报告不修改。新增 PAR-19—21、DML-18/19，原编号保留，GW-C4 仅补充断言。修订中没有扩大时间预算、改并发值或动代码。

静态校验：闭集 36 项与本答复 36 条代表样本集合一致；设计主验收编号 77 个、无重复（不是已跑 77 个测试）；4 个 JSON 示例有效，代码围栏配对、相对文档链接有效、Git 空白检查通过。

尚未完成的是修复后的自动化/SIT/UAT，以及内网候选目录和 180 秒容量实测。请 A 按设计 §11 核对三处落文；D04 的开工条件按其 P2-04 定点确认处理，D03 的真实容量准出证据由责任方回填。保持 180 秒的决策已完成，不再占用 Mr.Linsang 做同一选择。
