# v1.6.3.2 审核规则调整与扫描历史跨页对比
## 第五轮生产门禁整改复测报告（智能体 O）

| 项目 | 内容 |
|---|---|
| 测试版本 | v1.6.3.2 |
| 被测提交 | `204238b1ae3610581ae346bdaef700eefeebe700` |
| 整改来源 | 《GATE-DECISION-v1.6.3.2 生产发布门禁签署决议与整改任务书》及 Q 开发报告 Rev.R5 |
| 测试日期 | 2026-09-05 |
| 测试人 | 智能体 O（独立 UAT） |
| 最终结论 | **不通过；GATE-2 / GATE-3 不具备复签条件；生产发布继续阻断** |

---

## 1. 执行摘要

O 已在当前 `main` 提交上，以真实浏览器点击“SQL 审核 → 即时审核”、规则库页面检查、引擎独立矩阵和全量回归四层方式完成复测。

结论分开裁决如下：

1. **GATE-1**：林桑已经签署通过，本轮 Q 未修改其相关逻辑，O 不越权重开该门禁。
2. **GATE-2 的规则元数据整改通过**：R030、R031、R032 均显示“仅分布式”，规则数为总计 121、集中式生效 90、跳过 31。
3. **GATE-2 的业务验收失败**：集中式合法 VIEW、PROCEDURE、FUNCTION 仍被 R001/R003/R004/R005/R028 等建表规则误拦；带 `BEGIN ...; END` 的函数/过程还会被页面入口错误拆成多条 SQL。门禁决议要求的“视图、存储过程、触发器、临时表、自定义函数均不再误拦截”没有实现。
4. **GATE-3 原始缺陷已关闭**：把用户内网截图中的同形 bare `MAXVALUE` DDL 粘贴到即时审核页面，E999、R003、R004、R005、R118 全部消失，R121 独立保留。
5. **GATE-3 改动边界仍有缺陷**：合法块注释位于 `THAN` 与 `MAXVALUE` 之间时，正则归一化被注释分段打断，E999 与建表规则级联误报复现。

因此，本轮新建两个缺陷：

| 缺陷编号 | 级别 | 状态 | 发布影响 |
|---|---:|---|---|
| `UAT-O-1632-R5-01` | **P1** | 新建 / 未关闭 | GATE-2 阻断：集中式合法数据库对象仍无法通过审核 |
| `UAT-O-1632-R5-02` | **P2** | 新建 / 未关闭 | GATE-3 阻断：合法注释边界仍触发 E999 及级联误报 |

当前已知缺陷为 `P0=0、P1=1、P2=1、P3=0`。不得进入生产发布，也不得回填 GATE-2/GATE-3“通过”。

---

## 2. 证据边界与官方语法依据

### 2.1 用户提供的内网真实 TDSQL 证据

用户提供截图显示：在内网真实 TDSQL 分布式集群 `cbs_bsdb` 中，表 `t_order_history` 使用一级 `shardkey=order_id`、二级 `PARTITION BY RANGE(YEAR(create_time))`，末分区采用 bare `VALUES LESS THAN MAXVALUE`，建表于页面中执行成功，随后查询成功。

截图 SHA-256：

```text
339EAD545CBB350A2FE0D1C89F43B56FA298B034C7C4889BD92A041F9A8E7624
```

该截图是**用户提供的内网真实执行证据**。O 未直连该内网实例，不把截图描述冒充为 O 的现场执行；仓库也不复制含内网信息的原图。

### 2.2 官方依据

- 腾讯云 TDSQL MySQL 版建表文档给出的分区定义语法明确包含 `LESS THAN ... | MAXVALUE`，并说明一级分区与 shardkey 的关系：[TDSQL MySQL 版建表](https://cloud.tencent.com/document/product/557/8767)。
- MySQL 官方 RANGE 分区文档直接给出 `PARTITION ... VALUES LESS THAN MAXVALUE`，也给出 `RANGE(YEAR(...))` 与 bare MAXVALUE 的组合：[MySQL RANGE Partitioning](https://dev.mysql.com/doc/refman/8.0/en/partitioning-range.html)。
- MySQL 官方注释语法允许 `/* ... */` 行内注释出现在表达式 token 之间，因此归一化不能把合法注释视为关键字序列终止：[MySQL Comments](https://dev.mysql.com/doc/refman/8.0/en/comments.html)。

R121 是项目治理规则，目标是**准确识别并禁止**二级 RANGE 的 MAXVALUE；数据库能够创建该表不意味着审核工具应放行 R121，但审核工具不得同时制造 E999 或无关规则假阳性。

---

## 3. 测试基线与环境

| 项目 | 实际值 |
|---|---|
| 仓库 | `C:\TDSQL_SQLCHECK\TDSQL-SQLCheck` |
| 分支 | `main` |
| HEAD / origin/main（测试开始时） | `204238b1ae3610581ae346bdaef700eefeebe700` |
| 版本页 | v1.6.3.2 |
| 规则总数 | 121 |
| 隔离服务 | 当前提交启动于 `127.0.0.1:8001` |
| 浏览器 | Codex 内置浏览器，真实登录、选择实例类型、输入 SQL、点击审核并读取页面结果 |

本机 `8000` 端口已有早于 Q 提交启动的旧进程，O 没有把旧服务用于结论，也没有擅自终止；另起 `8001` 隔离服务确保页面加载的是被测提交。

测试前工作树仅存在用户自有未跟踪文件 `docs/PRODUCTION-DEPLOY-ISSUES-v1.6.3.0.md`，O 未读取、未修改、未暂存该文件。

---

## 4. Q 整改内容核对

### 4.1 GATE-2

Q 已将 R031 的 `instance_scope` 从 ALL 改为 DISTRIBUTED，并同步数量口径及测试集合。静态实现和规则库页面均确认：R030/R031/R032 为“仅分布式”，集中式 90/31 口径正确。

### 4.2 GATE-3

Q 在 `parser_legacy.py` 增加 `_normalize_bare_partition_maxvalue()`，于 `parse_one` 前把代码段中的 bare `VALUES LESS THAN MAXVALUE` 改写为 `VALUES LESS THAN (MAXVALUE)`，并删除原先针对该形态合成 E999 的逻辑。用户原始形态已能生成 `exp.Create`，基础建表信息不再丢失。

以上两处代码“按任务书修改”事实成立；但门禁验收看最终用户结果，不只看单条规则元数据或实现说明。

---

## 5. GATE-3 原始缺陷关闭结果

### 5.1 用户截图同形 DDL

在分布式实例类型下，O 将用户截图同形 SQL 输入即时审核页面，页面显示 4 项：

| 规则 | 结果性质 | 判定 |
|---|---|---|
| R028 | 表确实没有表级 COMMENT | 真阳性 |
| R036 | 表确实没有 update_time | INFO 真提示 |
| R104 | 列 COMMENT 中含中文全角括号 | 既有独立规则命中 |
| R121 | 二级 RANGE 存在 `p_max ... MAXVALUE` | 本需求应有命中 |

以下原级联项均未出现：`E999、R003、R004、R005、R118`。这证明用户拒签时的**原始 bare MAXVALUE 级联假阳性已关闭**。

门禁任务书把 R028 列入原始“级联假阳性”，但该 DDL 确实没有表级 COMMENT；O 不认可把本次仍出现的 R028 判成回归。Q 开发报告 §15.2 已作同样纠正。

### 5.2 独立变体矩阵

| 变体 | AST / 解析 | 关键结果 |
|---|---|---|
| bare MAXVALUE | Create / 成功 | R121 保留，无 E999 级联 |
| `(MAXVALUE)` | Create / 成功 | R121 保留，无 E999 级联 |
| 大小写 + 换行 | Create / 成功 | R121 保留，无 E999 级联 |
| 无 MAXVALUE | Create / 成功 | 不命中 R121 |
| 字符串内含该短语 | Create / 成功 | 不被归一、不命中 R121 |
| 非法 `MAXVALUES` | 失败关闭 | 有 E999，不伪装成合法 MAXVALUE |

原始验收点通过，但 §7 所述注释边界失败，故 GATE-3 整体仍不能复签。

---

## 6. GATE-2 浏览器验收结果

门禁决议的验收语义是集中式实例允许合法视图、存储过程、触发器、临时表及自定义函数，不是简单地“R030/R031/R032 没出现”。真实页面结果如下：

| 集中式输入 | 页面结果 | 是否满足门禁 |
|---|---|---|
| `CREATE VIEW v_order AS SELECT 1 AS id` | R003/R004/R005/R028 四项 ERROR | **否** |
| `CREATE PROCEDURE p_test() SELECT 1` | R001/R003/R004/R005/R028 五项 ERROR | **否** |
| `CREATE FUNCTION fn_calc(a INT,b INT) RETURNS INT RETURN a+b` | R001/R003/R004/R005/R028 五项 ERROR | **否** |
| `CREATE TRIGGER ... FOR EACH ROW SET @x=1` | 审核通过 | 是 |
| 合法 `CREATE TEMPORARY TABLE` | 仅 R037 INFO；无 R024/R032 与 ERROR | 是 |
| `CREATE FUNCTION ... BEGIN RETURN a+b; END;` | 被拆成 BATCH，函数体分号前后分别审核并产生误报 | **否** |

R030/R031/R032 的确已经从集中式结果消失，但合法对象仍被无关表规则拦截，不能把“指定三条规则跳过”误写成“集中式零覆盖已实现”。

---

## 7. 新缺陷

### 7.1 UAT-O-1632-R5-01（P1）：集中式合法非 TABLE 对象仍被建表规则误拦

#### 复现步骤

1. 登录 v1.6.3.2。
2. 进入“SQL 审核 → 即时审核”。
3. 实例架构选择“集中式”。
4. 分别输入 §6 的 VIEW、PROCEDURE、FUNCTION SQL。
5. 点击“开始审核”。

#### 预期

- R030/R031/R032 均跳过；
- 非 TABLE 对象不进入建表规则；
- 上述最小合法语句最终审核通过；
- 带过程体分号的合法例程作为一条语句审核。

#### 实际

- VIEW 命中 4 项建表 ERROR；
- PROCEDURE、FUNCTION 各命中 5 项建表 ERROR；
- 复合 FUNCTION/PROCEDURE 被错误拆成 BATCH。

#### 双根因

1. `backend/engine/parser/parser_legacy.py:2624` 对任意 `exp.Create` 都调用 `_parse_create()`；该函数在 `:2954` 无条件设置 `parsed.is_create_table = True`，没有检查 `ast.args["kind"]`。因此 sqlglot 能正常解析的 CREATE VIEW/PROCEDURE/FUNCTION 全被伪装成建表。
2. `backend/services/audit_service.py:190` 在即时审核入口先调用 `split_sql_statements()`；`backend/services/database.py:119` 的通用分号扫描器只保护引号和注释，不理解 `CREATE PROCEDURE/FUNCTION/TRIGGER ... BEGIN ... END` 过程体，内部合法分号被当作批量语句边界。

#### 为什么现有测试漏检

- 当前 GATE-2 用例主要断言集中式结果中“不含 R030/R031/R032”，没有断言**全部违规集合为空**。
- harness 的函数样例使用 `BEGIN/END`，直接调用引擎时 sqlglot 生成 Block，绕过了 `exp.Create → _parse_create`；页面入口又先分号拆分，路径与 harness 不同。
- 规则隔离测试通过不代表真实 API/UI 聚合结果通过。

### 7.2 UAT-O-1632-R5-02（P2）：MAXVALUE 关键字间合法注释绕过归一化

#### 复现输入

```sql
CREATE TABLE t_max_comment (
  id BIGINT NOT NULL,
  create_time DATETIME NOT NULL,
  PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='t'
SHARDKEY=id
PARTITION BY RANGE (YEAR(create_time)) (
  PARTITION p2026 VALUES LESS THAN (2027),
  PARTITION pmax VALUES LESS THAN /*合法注释*/ MAXVALUE
);
```

#### 预期

AST 为 Create；仅命中与表自身事实相符的规则及 R121，不出现 E999/R003/R004/R005/R028/R118 级联。

#### 实际

页面出现 `E999、R003、R004、R005、R028、R121`。R121 正确，其余为解析失败后的级联误报。

#### 根因

`_normalize_bare_partition_maxvalue()` 先用 `_LITERAL_OR_COMMENT_RE` 把 SQL 拆成“代码段 / 注释段”，再只在单个代码段内匹配完整正则。注释把 `VALUES LESS THAN` 与 `MAXVALUE` 分到两个代码段，完整模式永远不匹配。

MySQL 官方允许 `/* ... */` 行内注释，因此不能用“用户不应这样写”关闭缺陷。该形态尚未在用户内网目标集群单独执行，结论边界是“官方 MySQL 合法 + 本地当前版本稳定复现”；不冒充已取得该变体的 TDSQL 现场截图。

---

## 8. 给 Q 的照图施工方案

### 8.1 修复 R5-01：按 CREATE 对象类型分流

在 parser 的 `exp.Create` 分支先读取并标准化 `kind`：

```python
kind = str(ast.args.get("kind") or "").upper()
if kind == "TABLE":
    self._parse_create(ast, parsed)
else:
    self._parse_create_object(ast, parsed)
```

施工约束：

1. `_parse_create()` 内部再加一道防御，只允许 `kind == TABLE` 时设置 `is_create_table=True`；不能只在外层判断。
2. VIEW/PROCEDURE/FUNCTION/TRIGGER 的对象名不要塞入 `parsed.tables` 触发 R001 等表名规则。如业务需要展示对象名，新增独立的 `created_object_name`/`created_object_kind` 字段。
3. 保留 `parsed.sql_type` 为准确的 `CREATE VIEW/PROCEDURE/FUNCTION/TRIGGER`；未知对象仍按现有失败关闭原则处理，不得一律放行。
4. 逐条审计所有依赖 `is_create_table`、`tables`、`columns` 的建表规则，确认非 TABLE 对象不会进入。

### 8.2 修复 R5-01：例程体分号不得被即时审核拆开

不要直接扩大数据库执行器拆分器的职责后无差别影响导入/执行路径。为审核入口实现或抽取 tokenizer-aware 的审核语句切分：

1. 先用现有 `_lex_head_words` / `_is_create_routine_head` 识别顶层 `CREATE [DEFINER...] PROCEDURE|FUNCTION|TRIGGER|EVENT`。
2. 对例程 CREATE，在没有客户端 `DELIMITER` 协议时把完整输入作为一个审核单元，内部 `BEGIN...END` 分号不拆。
3. 普通多语句 SQL 继续按顶层分号拆分；字符串、反引号、`-- `、`#`、`/* */` 内分号保持不拆。
4. `backend/api/sql_audit.py` 的文件/文本入口也应复用同一审核切分契约，避免即时审核与文件审核行为分叉。

### 8.3 修复 R5-02：用 token/span 识别，不再依赖连续代码段正则

建议采用“词法 token + 原文 span”归一化：

1. 对原文词法化，忽略普通注释 token 后识别 `VALUES → LESS → THAN → MAXVALUE` 关键字序列。
2. 确认 MAXVALUE 不在字符串、标识符或注释正文中，并属于分区定义上下文。
3. 仅在 MAXVALUE 原文 span 两侧插入括号，保留全部空白和注释；已经带括号的不重复处理。
4. `raw_sql` 永远保留原文；`sql_clean` 与 `sql_recover` 使用同一 span 规划，防止恢复链偏移。
5. 非法 `MAXVALUES`、字符串/COMMENT 中的短语、普通业务表达式不得被改写。

若继续使用扫描器，也必须让注释在关键字状态机中等价于空白，能够识别：

```sql
VALUES /*a*/ LESS /*b*/ THAN /*c*/ MAXVALUE
```

单纯把正则改成跨注释的 `.*?` 不可接受，会误穿字符串、其他分区或嵌套结构。

---

## 9. 必增验收测试

### 9.1 GATE-2 全结果锁

以下用例必须通过**真实 AuditService/API 路径**，不得只挂 `rule_overrides` 单测某三条规则：

| 架构 | SQL | 必须断言 |
|---|---|---|
| 集中式 | 最小合法 VIEW | `violations == []` |
| 集中式 | 简单 PROCEDURE | `violations == []` |
| 集中式 | 简单 FUNCTION | `violations == []` |
| 集中式 | TRIGGER | `violations == []` |
| 集中式 | 合法 TEMPORARY TABLE | 无 ERROR；R030/R031/R032 不出现 |
| 集中式 | `BEGIN...;...;END` 过程/函数 | 结果不是 BATCH，内部不拆分，最终无违规 |
| 分布式 | VIEW/PROCEDURE/TRIGGER | R030 命中；不出现 R003/R004/R005/R028 等表规则 |
| 分布式 | FUNCTION | R030、R031 命中；不出现表规则 |

同时保留数量锁：总规则 121，集中式 90，跳过 31。

### 9.2 GATE-3 注释与负例锁

至少覆盖：

- `VALUES /*c*/ LESS /*c*/ THAN /*c*/ MAXVALUE`；
- `VALUES LESS THAN /*c*/ MAXVALUE`；
- `VALUES LESS THAN -- c\n MAXVALUE`；
- bare、括号、大/小写、换行；
- 字符串与 COMMENT 文本含 `VALUES LESS THAN MAXVALUE`；
- 非法 `MAXVALUES`；
- 无 MAXVALUE 的正常范围分区。

合法正例统一断言：`ast is exp.Create`、无 `E999/R003/R004/R005/R028/R118`、R121 精确一次且分区名正确。非法负例必须保留 E999 且不得伪造 R121。

### 9.3 浏览器关闭标准

Q 修复后转 O 下一轮时，必须以当前提交新启服务并在页面完成：

1. 集中式五类对象逐条点击审核；
2. 复合 FUNCTION/PROCEDURE 带内部两个以上分号；
3. 用户原始 bare MAXVALUE DDL；
4. 注释穿插 MAXVALUE DDL；
5. R030/R031/R032 规则库标签及 121/90/31 数量核对。

任何一项出现无关 ERROR、E999 级联或例程被拆为 BATCH，缺陷不得关闭。

---

## 10. 自动化回归结果

| 命令 / 测试集 | 结果 |
|---|---|
| `pytest tests/test_rules_v1632.py tests/test_instance_scope_rules.py -q` | **78 passed，3 warnings** |
| `python tests/rule_audit_materials/verify_rules.py --verbose` | **PASS；total=121，covered=109，metadata=7，exempt=5，failures=0** |
| `pytest tests -q` | **1844 passed，11 warnings，0 failed，408.81s** |
| `pytest tests_3p -q`（未设置目标，默认 8899） | **无效环境运行：13 failed / 110 errors；未用于产品结论** |
| `T3P_BASE_URL=http://127.0.0.1:8001 pytest tests_3p -q` | **125 passed，1 skipped，2 warnings，0 failed，19.77s** |

第一次三方运行失败是测试命令默认指向未就绪的 `127.0.0.1:8899`，登录夹具失败并级联；明确指向本轮隔离服务后全绿。O 如实保留这次无效运行的原因，不把它算产品缺陷，也不隐去重跑。

自动化全绿与本报告 P1/P2 不矛盾：前者证明现有测试定义无回归；后者证明现有测试缺少真实页面全结果断言和注释边界。

---

## 11. 门禁裁决

| 门禁 | 本轮状态 | O 意见 |
|---|---|---|
| GATE-1 | 已由用户签署通过 | 本轮不重开 |
| GATE-2 | **不通过** | R031 改域本身通过，但集中式对象最终仍被误拦，R5-01 阻断 |
| GATE-3 | **不通过** | 用户原始 DDL 已修复，但合法注释边界仍复现同类级联，R5-02 阻断 |
| 生产准出 | **禁止** | 等 Q 修复、O 复测关闭、用户/G 重新签署 GATE-2/GATE-3，再做目标麒麟 12/0/0 |

本轮没有执行目标麒麟 V10 SP3 部署后验证，也没有执行生产容量/性能测试；本机三方性能用例通过不得替代这两个外部门禁。

---

## 12. 最终裁决

**第五轮门禁整改复测不通过。**

Q 对 GATE-3 用户原始 bare MAXVALUE 案例的修复有效，对 R031 适用域的元数据修改也正确；但 GATE-2 的最终业务目标没有实现，并且 GATE-3 新归一化逻辑仍存在官方合法注释边界漏洞。请 Q 严格按 §8、§9 完成两项修复与测试补强后，再转 O 下一轮定点复测。

证据索引：`docs/evidence/v1.6.3.2-uat-o-r5/README.md`。
