# REVIEW2-v1.6.3.4 详细开发设计说明书 第二轮评审报告

| 项 | 内容 |
|---|---|
| 被评审文档 | `docs/DETAIL-v1.6.3.4-报告实例标识与分区统计及审核网关修复.md`（Rev.B，提交 `2191d7b`，790 行） |
| 配套答复 | `docs/RESPONSE1-v1.6.3.4-第一轮设计评审逐项答复与RevB修订说明-O.md` |
| 编写方 | 智能体 O |
| 评审方 | 智能体 A |
| 评审轮次 | 第二轮设计评审 |
| 评审日期 | 2026-09-06 |
| 代码基线 | `main` / `2191d7b` |
| 评审方式 | 不采信答复自述：O 提出的每一条"技术纠正"逐条在本机跑代码复核；Rev.B 全文逐节比对落文；另做新缺陷探查 |
| **评审结论** | **通过（有条件）。P1-01 阻断解除。本轮新增 2 项 P2、1 项 P3，均不阻断开工；其中 P2-04 须在 D04（R043）开工前补齐，其余包可并行开工。** |

---

## 1. 结论摘要

**先说最要紧的一句：O 这次提出的技术纠正，我逐条复核，六条全部成立，其中五条指出的是我上一轮的实质性错误。** 详见 §2。这不是客套——其中两条（回退出口一律 UNKNOWN、前置拒绝一律释放槽）若按我原文施工，会分别制造新的 E999 误报和跨用户误释放锁，比原缺陷更糟。

| 级别 | 编号 | 问题 | 状态 |
|---|---|---|---|
| — | P1-01 | R043 门闩依赖 `sql_type` | **已关闭**。Rev.B §5.2 第 2/4/5 条把事实提取、触发、文案三条链全部脱离 `sql_type`，并补了状态真值表与 DML-11—17 |
| — | P2-01 | 候选集覆盖 | **已关闭且优于我的方案**。O 指出"分片集 ∪ only_base"仍漏掉落在旧单表结果里的新语法表，改为 `C = L ∪ P ∪ B` |
| — | P2-02 | 文件审核报告口径 | **已关闭**。按 Mr.Linsang 裁决落文，并纠正了我两处措辞与一处列名错误 |
| — | P2-03 | 网关并发 | **已关闭**。裁决落文，并纠正了我三处技术描述 |
| — | P3-01/02/03 | 超时链 / CSP / 派生属性 | **均已关闭**，其中 P3-02、P3-03 纠正了我的错误 |
| **P2** | **P2-04**（新） | §5.2 状态表把"可靠非 DML 头"写成 `CREATE/ALTER/INSERT/SELECT 等`（开放式），结尾又禁止对未覆盖的头猜 NOT_APPLICABLE——**两句话互相矛盾**。实测 `exp.Command` 回退路径是高频路径，`RENAME`/`OPTIMIZE`/`LOCK`/`GRANT`/`CALL` 均落在其中 | 按字面施工会把今天零违规、零 E999 的语句变成 E999，**正是 O 自己要避免的那种"以 E999 误报换 R043 误报"** |
| **P2** | **P2-05**（新） | 候选集从"分片表"扩到"全部基表"后，`MAX_PARENT_DDL_PER_RUN=5000` 与 `TOTAL_BUDGET_SECONDS=180` 未同步评估。真实实例上大概率截断 | 需求 ② 要的是"数量"，设计在真实规模下大概率给出 `≥N（未完成）`。设计对此诚实，但从未说明这个后果——**须让 Mr.Linsang 知情** |
| P3 | P3-04（新） | `GATEWAY_MAX_CONCURRENT` 只接受 1，其余值启动失败 | 实为常量而非可调项，运维照配置表调优会撞生产启动失败 |

**一句话**：Rev.B 是一次高质量修订，七项全部落到实处，且有五处把我的错误纠正了回来。剩下的两项 P2 都不是方向错误，而是"照图施工"的图还差一处枚举、一处容量核算。

---

## 2. 我上一轮的六处差错，O 的纠正全部成立

评审方出错必须记在案，否则下一轮还会照错的图施工。以下每条我都独立复核过，不是接受 O 的说法。

### 2.1 P1-01：我写的"回退出口一律 UNKNOWN"会制造新的 E999 误报

我上一轮 §5.3 整改文字写的是：首 token 非 UPDATE/DELETE 则"一律 `status=UNKNOWN`"。而设计 §5.2 第 5 条约定 UNKNOWN 并入完整性门禁（E999）。两句合起来 = **任何走回退出口的 ALTER/CREATE 都产生 E999**。

**关键在于 Command 路径的 `parse_error` 是空的。**我用仓库真实 `SQLParser` 复现（`unittest.mock.patch` 注入，不改仓库文件），结果与 O 的表逐格一致：

| 输入 | 正常 AST | 注入 ParseError | 注入 Command |
|---|---|---|---|
| `ALTER TABLE t MODIFY ts DATETIME ON UPDATE CURRENT_TIMESTAMP` | ALTER / false / **无错** | UPDATE / false / 有错 | UPDATE / false / **无错** |
| `ALTER … ADD COLUMN a … ON UPDATE …, ADD COLUMN b VARCHAR(20) CHARACTER SET utf8mb4` | ALTER / **true** / 无错 | UPDATE / true / 有错 | UPDATE / **true** / **无错** |
| `UPDATE a JOIN b ON a.id=b.id SET a.v=1` | UPDATE / true / 无错 | UPDATE / true / 有错 | UPDATE / true / 无错 |
| `DELETE a FROM a JOIN b ON a.id=b.id` | DELETE / true / 无错 | DELETE / true / 有错 | DELETE / true / 无错 |

（格式：sql_type / is_multi_table_update / parse_error 是否非空）

ParseError 路径本来就有错误，多一条 E999 无所谓；**Command 路径本来干干净净**，按我的写法会凭空长出 E999。O 改为"可靠非 DML → NOT_DML/NOT_APPLICABLE，不新增 E999"是对的。

**额外收获（比上一轮更强的证据）**：上表第 2 行正常 AST 列是 `ALTER / true`——也就是说

> `ALTER TABLE t ADD COLUMN a DATETIME ON UPDATE CURRENT_TIMESTAMP, ADD COLUMN b VARCHAR(20) CHARACTER SET utf8mb4`
> 在**今天的生产代码、完全正常的 AST 路径上**就会触发 R043。

我实跑 `RuleChecker.audit_sql(..., instance_type="distributed")` 确认：违规 = `['R043', 'R073']`。**这条缺陷不止发生在 CREATE TABLE，也不止发生在解析降级时**，附件只是暴露了它的一个切面。这一点 Rev.B 已经收进 §2.3 与 DML-12，方向正确。

### 2.2 P2-01：我的"分片集 ∪ only_base"确实堵不住

我上一轮建议用现有 reconciliation 的 `only_base` 兜底。O 指出：新语法主表若落在旧"单表"结果里，它既不在分片集也不在 only_base。

复核 `backend/services/table_type_stats_service.py:637`：

```python
proxy_tables = {t for (d, t) in kind_map if d == db}
```

而 `kind_map` 是在 `for kind, _sql in _KIND_SQL:` 循环里填充的（`:600-610`），**三类命令（shard / broadcast / single）的结果都进同一个 `kind_map`**。因此 `only_base = logical_base - proxy_tables` 天然把"被 Proxy 判成单表"的表排除在外。**O 是对的，我的兜底有洞。** `C = L ∪ P ∪ B` 覆盖了这个分支。

### 2.3 P2-02：我两处措辞不严 + 一处列名写错

1. 我在 §7.1(a) 的落文文字里写"该入口不指定实例连接"，把整条 `POST /api/v1/audit/file` 说死了。实际 `sql_audit.py:136/150-151` 明确接收 `connection_id`，只是**当前前端**没传。O 保留"已有 API 显式绑定调用按冻结名称处理"，**比我的版本更贴合 Mr.Linsang"通过指定实例连接的就要显示"的原话**。
2. 我的 RPT-02 写"持久化中 `gate_result` 恒为 None"。复核 `backend/services/database.py:882`，`audit_history` 的实际列是 **`gate_passed`**（另有 `gate_detail`），没有 `gate_result` 这一列——`gate_result` 只是服务层返回值名。O 改成断言 `audit_history.gate_passed=NULL` 才是可执行的。

### 2.4 P2-03：我三处技术描述不准，其中一处会写出危险代码

1. **"第二位提交者从提交到收到 429 之间可能持续约 10 分钟"** —— 错。非阻塞取锁应当立即拒绝。我上一轮 §4 原文写的是"连续约 9—10 分钟收到 429"（正确，指不可用**窗口**长度），到 §7.2 落文时被我自己写成了"等待时长"。O 的纠正对。
2. **"单次分析需要 2 GiB 级临时空间"** —— 错。`GATEWAY_MIN_FREE_BYTES` 是受理前的**空闲空间门槛**，不是实测消耗。我把准入阈值当成了容量结论。
3. **GW-C2 我写"前置过滤拒绝的请求（如超限 413）→ 槽必须被释放"** —— **这条最危险**。未取得槽的请求去执行释放，等于释放别人正持有的锁，会让单任务约束当场失效。O 改为"仅释放本请求实际取得的槽（acquired 标志/持锁句柄，finally 中判断）"是正确写法。我引用了设计 Rev.A §6.4 第 3 条的原话而没有质疑它，这是我的失职。

### 2.5 P3-02：我三句话全错

1. **"CSP 逐字节一致"不可能满足**：`backend/api/daily_inspect.py:138` 每次响应 `secrets.token_urlsafe(16)` 生成新 nonce 并填进 CSP 头，两次响应必然不同。硬要求字节相等，只会逼实现方复用 nonce——把安全机制改坏。O 的"归一 nonce 后比策略项"是对的。
2. **我说 H07 与 H08 共用 `_strip_inline_handlers`、90 秒票据、不透明源 sandbox** —— 实测 `daily_inspect.py` 中这三者出现次数为 **0**；它们全部只在 `gateway_log.py`（`:35` `_strip_inline_handlers`、`:139-154` 票据、`:19/163` sandbox）。两条链根本不共享机制。
3. **我暗示 H07 的 CSP 是紧的** —— 实测 `daily_inspect.py:24-34` 的 `_REPORT_CSP` 本来就含 `'unsafe-eval' 'unsafe-inline'`。O 明确"原策略已有该文本，不以本次改动新增或扩大"，比我准确。

### 2.6 P3-03：我"爆炸半径为零"说过头了

`ParsedSQL` 是 `@dataclass`（`parser_legacy.py:3280`），`is_multi_table_update` 是声明字段（`:3356`）。字段改 property 后，构造参数、`dataclasses.fields()`、`asdict()` 的行为都会变，这与"删掉那一处正则赋值的爆炸半径为零"是两件事，我混为一谈了。

补充一条**对 O 有利的实测**，可用来收窄他要求的核对范围：全仓 `ParsedSQL(` 构造点只有 4 处（`parser_legacy.py:3397` 与 `tests/test_a_verify_desc_index_and_comment_fragments.py:225/234/237`），**全部只传 `raw_sql=`**，没有一处传该字段；`asdict` 在仓库中只用于 `snapshot_extractors/base.py:32` 的另一个 dataclass，不涉及 `ParsedSQL`。所以 O 要求的核对是必要的，但工作量很小，DML-16 足够覆盖。

## 3. 七项落文核验

逐项核对 Rev.B 正文是否真的落到位，而不是只在答复里说了。

| 编号 | 要求 | Rev.B 落点 | 核验 |
|---|---|---|---|
| P1-01 | 事实源与规则脱离 `sql_type` | §5.2 第 2/4/5 条、状态真值表、DML-11—17 | ✅ 第 5 条已改为"唯一触发条件 = RESOLVED + UPDATE/DELETE + is_multi_table"，并明写"**不再检查 parsed.sql_type**"；文案也从 `statement_kind` 取。全文 `grep "parsed.sql_type in"` 无残留 |
| P2-01 | 候选覆盖 + 完整性收口 | §4.1/§4.3 全节、§4.4 八列、PAR-13—18 | ✅ `C = L ∪ P ∪ B`；新增 `inventory_state` 与 `outside_shard`；`0 <= main <= 判明 <= 已知候选` 取代了原来的"main ≤ 分片数"上界；PAR-14 专门证明"只补 only_base 不够" |
| P2-02 | 按裁决划线 + 不碰门禁 | §1、§3.1 通则、H01/H02、§3.3、REP-12—14 | ✅ 通则写成"生成时已指定连接→冻结真名；未指定→未关联"；H02 明确 `evaluate_gate` 保持默认 False、断言 `gate_passed=NULL`；REP-14 专门锁住"不改变已有绑定 API 的门禁语义" |
| P2-03 | 单任务 + 说明性 429 | §6.3/§6.3 段后、§6.4 第 3/4 条、429 定稿、GW-C1—C4 | ✅ 429 契约定稿到字段级（`Retry-After`、`X-Request-ID`、`detail.code=GATEWAY_BUSY`、正文"未被处理（未进入分析、未排队）"）；锁释放改为"仅释放本请求实际取得的槽" |
| P3-01 | 超时链集中 + 启动校验 | §6.3.1、§6.7、GW-T1/T2 | ✅ 且比我要求的更好：加了 TERM/KILL 回收预留 10 秒，拆出 direct/proxy 两套约束。我复算过默认值：`540+10<600` ✓、`300+600+10<=990` ✓、`600+10<660<990` ✓，三条不等式自洽 |
| P3-02 | H07 一并覆盖 | §3.4 新增段、REP-15 | ✅ 点名 `daily_inspect.py::_REPORT_CSP`/`compare_html`，明确两条链不合并，验收改为"归一 nonce 后比策略 + 断言每响应 nonce 更新且与脚本匹配" |
| P3-03 | 只读派生属性 | §5.2、DML-09/16 | ✅ 定义为 `status==RESOLVED and statement_kind in (UPDATE,DELETE) and is_multi_table is True`，无 setter，R043 直接读事实对象 |

另核验了三项一致性（这类修订最容易留下自相矛盾的旧文字）：

* **无残留旧口径**：全文检索 `parsed.sql_type in`、`分片逻辑表集合`、`可用 Proxy 分片候选`、`兼容老调用者`、`六条独立`——**全部为 0 命中**。
* **列数自洽**：§4.4 写"各八条 ADD COLUMN（共十六条）"，§9 第 3 条写"六个新数字为 null、两个 state 为 LEGACY"，6+2=8 ✓。
* **公式自洽**：`candidates = checked + unknown + unchecked` 与 `outside_shard <= main <= checked <= candidates` 不冲突 ✓。
* **机制可行性**：O 新引入的 `SSDictCursor` 流式读取——仓库驱动是 pymysql（`requirements.txt:5`），且多处已用 `cursorclass=pymysql.cursors.DictCursor`（如 `tdsql_connector.py:261`），换 `SSDictCursor` 可行 ✅。

---

## 4. 本轮新发现

### P2-04（须在 D04 开工前整改）§5.2 状态表的"可靠非 DML 头"是开放式枚举，与结尾禁令自相矛盾，会把 R043 误报换成 E999 误报

**问题**

Rev.B §5.2 状态表第一行：

> 可靠顶层 AST，或独立词法可确定 **CREATE/ALTER/INSERT/SELECT 等**非 UPDATE/DELETE 语句头 → NOT_DML / NOT_APPLICABLE → 无 R043，**本事实不新增 E999**

紧接着表后又写：

> 对未知/未覆盖的非 DML 头也**不硬猜 NOT_APPLICABLE**。

这两句直接打架。第一句用"等"开了口子，第二句把口子焊死。实施者只能二选一，而选错的那一边会产生新的用户可见错误。

**证据：`exp.Command` 回退不是冷门路径，而是本项目域内的高频路径**

我用锁定的 `sqlglot 30.14.0` 实测顶层节点类型：

| 语句 | 顶层节点 | 是否走 Command 回退 |
|---|---|---|
| `CREATE TABLE t (id INT) TDSQL_DISTRIBUTED BY HASH(id)`（反引号包裹列名同样如此） | `Command` | **★ 是** |
| `ALTER TABLE t REORGANIZE PARTITION p0 INTO (…)` | `Command` | **★ 是** |
| `RENAME TABLE a TO b` | `Command` | ★ 是 |
| `OPTIMIZE TABLE t` | `Command` | ★ 是 |
| `LOCK TABLES t WRITE` | `Command` | ★ 是 |
| `GRANT SELECT ON db.* TO 'u'@'%'` | `Command` | ★ 是 |
| `CALL sp_test(1)` | `Command` | ★ 是 |
| `HANDLER t OPEN` | ParseError | （另一条回退） |
| `CREATE TABLE t (id INT) ENGINE=InnoDB shardkey=id` | `Create` | 否 |
| `UPDATE t SET a=1 WHERE id=2` | `Update` | 否 |

**第一行尤其要命：REQ-02 要统计的那种新语法建表语句本身，就走 Command 回退。**第二行是 R121 的分区维护语句。

**这些语句今天的审核结果（实跑 `RuleChecker.audit_sql`，distributed）**：

| 语句 | 今天的违规 | 今天是否 E999 |
|---|---|---|
| `CREATE TABLE … TDSQL_DISTRIBUTED BY HASH(id)` | R003/R004/R005/R028/R029/R036/R037/R054/R077 | **无** |
| `ALTER TABLE t REORGANIZE PARTITION …` | （空） | **无** |
| `RENAME TABLE a TO b` | （空） | **无** |
| `OPTIMIZE TABLE t` | （空） | **无** |
| `GRANT SELECT ON db.* TO 'u'@'%'` | R051/R074 | **无** |

`ALTER`/`CREATE` 在 O 的枚举里，安全。但 **`RENAME`、`OPTIMIZE`、`LOCK`、`GRANT`、`CALL` 都不在**，按结尾那句禁令就得判 UNKNOWN → 并入完整性门禁 → **今天零违规、零 E999 的 `OPTIMIZE TABLE t` 变成审核不通过**。这正是 O 自己在答复 §3.2 里要避免的"把 R043 误报换成 E999 误报"。

**影响**

REQ-03 的目标是消除一类误报。若按 §5.2 字面施工，会在另一类语句上制造新的误报，**净效果可能为负**——而且这次影响面比原缺陷更广（原缺陷要求语句里同时有 `ON UPDATE`/逗号等诱因，新误报只要语句头不在四个枚举里就触发）。

**照图施工整改**

请 O 把 §5.2 状态表第一行与表后那句禁令，替换为下述文字（可直接采用）：

> **可靠语句头白名单（闭集，不得以"等"字扩展或收缩）。** 独立词法取到的顶层首个有效 token（去普通注释、空白，可执行注释按本节既有规则处理）若属于下列闭集，即为可靠非 DML，判 `NOT_DML / NOT_APPLICABLE / false`，**不新增 E999**：
>
> `SELECT`、`INSERT`、`REPLACE`、`CREATE`、`ALTER`、`DROP`、`TRUNCATE`、`RENAME`、`OPTIMIZE`、`ANALYZE`、`REPAIR`、`CHECK`、`LOCK`、`UNLOCK`、`GRANT`、`REVOKE`、`FLUSH`、`SET`、`SHOW`、`DESC`、`DESCRIBE`、`EXPLAIN`、`CALL`、`HANDLER`、`LOAD`、`START`、`BEGIN`、`COMMIT`、`ROLLBACK`、`SAVEPOINT`、`USE`、`KILL`、`RESET`、`PREPARE`、`EXECUTE`、`DEALLOCATE`
>
> `UPDATE`、`DELETE` 进入有限目标解析。`WITH` 按本节既有规则定位外层语句头后再套用本白名单。**首 token 不在白名单且不是 UPDATE/DELETE 时**，才判 `UNKNOWN / UNKNOWN / null` 并并入完整性门禁。
>
> 白名单的作用只有一个：证明"这条语句不是 R043 的对象"。它**不豁免**该语句的任何其他规则、`parse_error`、KFN 或既有 E999，也不代表解析器支持该语句。

并在 §8.3 增加一条**净效果回归锁**（这条比逐句用例更能兜住遗漏）：

| 用例 | 构造 | 断言 |
|---|---|---|
| DML-18 | 取一份覆盖上述全部白名单语句头 + 附件原文 + 真实联表 UPDATE/DELETE 的语料，改前改后各跑一次全量审核 | **E999 条目总数改后 ≤ 改前**；R043 仅在真实联表 UPDATE/DELETE 上出现；其余规则命中集合逐条相等。任一语句由"无 E999"变为"有 E999"即判不通过 |

DML-18 的价值在于：即便白名单将来漏了某个头，这条锁也会在 SIT 阶段把它抓出来，而不是等用户报障。

---

### P2-05（须 Mr.Linsang 知情 + O 补容量核算）候选集扩大后，真实实例上大概率给不出"数量"，只能给出"下界"

**问题**

Rev.A 的候选集是 Proxy 分片表集合；Rev.B 改为 `C = L ∪ P ∪ B`，即**每个可用库的全部基表**。这个修改在正确性上是对的（见 §2.2），但**护栏和预算没有跟着重算**：

| 参数 | 值 | 出处 | Rev.B 是否调整 |
|---|---|---|---|
| `MAX_PARENT_DDL_PER_RUN` | 5000 | §4.3 第 5 条 | 否（但作用域从"分片表"扩到"整个 C"） |
| `TOTAL_BUDGET_SECONDS` | 180（软预算） | `table_type_stats_service.py:144` | 否 |
| `COMMAND_READ_TIMEOUT` | 30 | 同上 `:132` | 否 |

而 180 秒是**与原有基础采集共享**的（`:58` 明确"贯穿 SHOW DATABASES / 基线查询 / 每一条命令"），且 Rev.B §4.3 明确"旧基础采集优先"。也就是说，主表识别只能用剩下的时间，逐表串行 `SHOW CREATE TABLE`。

**规模参照**：仓库里有一处内网实测注释（`table_type_stats_service.py:165`）——某内网库 `show table with shardkey` 有 **98 行**。而 `C` 是该库**全部基表**，通常是分片表的数倍到数十倍；多库实例还要再乘库数。

**影响**

按 §4.4 的状态机，只要有 `unchecked > 0` 就不是 COMPLETE，页面显示 `≥N（未完成）`。**Mr.Linsang 要的是"统计库里面的二级分区主表的数量"，设计在真实规模下大概率交付的是一个下界，不是数量。** 设计对此完全诚实（这点必须肯定，它没有用"零候选"冒充完整），但**从头到尾没有说明这个实际后果**，Mr.Linsang 无从知情。

这不是正确性缺陷，是交付形态问题——所以定 P2 而不是 P1，也不阻断开工。

**照图施工整改（三条，前两条给 O，第三条给 Mr.Linsang）**

**(a) 候选按"命中概率"分层排序，而不是纯名称排序。** 请把 §4.3 第 5 条的"按库/表精确名稳定排序"改为：

> 候选 `C` 按下列**优先级分层**依次处理，层内仍按 `(库名, 表名)` 精确名稳定排序，保证同输入同结果：
>
> 1. 第 1 层：旧最终分片集合 `S`（先验命中概率最高）
> 2. 第 2 层：`only_base = B − P`（仅逻辑基线可见）
> 3. 第 3 层：`L − (P ∪ B)`（仅独立目录可见）
> 4. 第 4 层：`P` 中的单表/广播结果
>
> 分层只影响**处理顺序**，不影响任何计数口径、状态判定或 `candidates` 的构成。其目的是：预算/护栏截断时，已 `checked` 的部分覆盖了先验概率最高的候选，`main` 是**尽可能紧的下界**而不是任意下界；`unchecked` 仍如实计数，**不得因分层就把未查部分说成已确认为负**。

**(b) 开工前补一次只读容量核算，写进 §8.2 作为 D03 的前置项。** 内网只需两条只读操作：① 对目标实例逐库 `SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=? AND TABLE_TYPE='BASE TABLE'` 得 `|C|` 上界；② 对任意 20—50 张表实测 `SHOW CREATE TABLE` 的单次往返耗时。两个数一乘即可判断 180 秒软预算够不够。据此在设计里明确取其一并写明理由：**放宽本步骤预算 / 保持现值并接受 PARTIAL / 调整 5000 护栏**。不实测就定参数，等于把容量假设当结论——这正是 §8.5 自己反对的做法。

**(c) 交付形态须让 Mr.Linsang 拍板**（见 §6）。

---

### P3-04（建议整改）`GATEWAY_MAX_CONCURRENT` 事实上是常量，却摆在配置表里

**问题**

§6.3 配置表：`GATEWAY_MAX_CONCURRENT | 1（本版仅允许此值）| …其他值启动校验失败`。GW-C4 也要求"配置并发为 0/2 → 启动失败"。

一个只接受唯一取值的"配置项"不是配置项。运维看到配置表里的可调参数，在生产上把它改成 2 想提升吞吐，得到的是**服务起不来**——而且是在变更窗口里。

**照图施工整改**

请在 §6.3 配置表该行的说明列末尾追加，并在 §6.7 部署清单的"不可调参数"中同列一条：

> 本版该项是**固定常量**，仅以配置名形式存在以便日志/`capabilities` 自证与后续版本扩展；取值被启动校验锁定为 1，**任何其他值（含 0、2）都会导致服务拒绝启动**。运维不得将其作为吞吐调优旋钮；确需并发大于 1，必须先走设计评审（涉及内存/磁盘/超时容量证据与多主机协调方案），不是改配置。

## 5. 结论与开工条件

**结论：通过（有条件）。P1-01 阻断解除。**

Rev.B 可以进入开发。整改项按实施包分开处理，不必等全部改完才动工：

| 实施包 | 是否可开工 | 前置条件 |
|---|---|---|
| D01 报告上下文基础 | ✅ 可 | 无 |
| D02 全部 HTML 接入 | ✅ 可 | 无 |
| **D03 主表识别** | ⚠️ 可开工，但**验收受限** | P2-05(a) 分层排序须先并入设计；P2-05(b) 容量核算须在 D03 判 PASS 前完成，否则 D03 只能给"范围受限通过" |
| **D04 R043** | ❌ **暂缓** | P2-04 的白名单闭集与 DML-18 净效果锁须先落文。这一项若照现文施工，会制造新的 E999 误报 |
| D05 网关入口 | ✅ 可 | 建议同步落 P3-04 |
| D06 网关执行 | ✅ 可 | 无 |
| D07 发布与验证 | ✅ 可 | 无 |

**二轮遗留项清单**

| 编号 | 级别 | 责任 | 判据 |
|---|---|---|---|
| P2-04 | P2 | O 改设计 | §5.2 白名单改为闭集并删除自相矛盾的禁令句；§8.3 增 DML-18 净效果锁 |
| P2-05 | P2 | O 改设计 + Mr.Linsang 知情 | §4.3 第 5 条改为分层排序；§8.2 增容量核算前置项；交付形态经 Mr.Linsang 确认 |
| P3-04 | P3 | O 改设计 | §6.3 与 §6.7 标注为固定常量、不可调 |

三项都是局部文字修改，不涉及方案方向。**我不需要再开第三轮完整评审**——O 改完后我只核这三处落文，符合即可放行 D04 与 D03 的完整验收。

## 6. 需要 Mr.Linsang 知情的两件事

**其一，一个好消息：您"网关日志分析同时只能一个人跑"的裁决，按现在的部署方式是能真正做到的。**

O 在方案里老实交代了一个边界：他的实现是"单台应用主机上的多个 worker 共用一把锁"，如果将来部署成多台应用主机，就变成"每台各允许一个"，您的裁决就打折了。我去查了实际部署配置：

* `deploy/tdsql-sqlcheck.service:13` —— 一个 systemd 服务，`--workers 2`
* `deploy/nginx-sqlcheck.conf:30` —— `proxy_pass http://127.0.0.1:8000`，本机单上游，没有多机负载均衡

**也就是说现网就是"单主机 2 个 worker"，正好落在 O 支持的范围内，您的裁决能百分之百兑现。**只有将来要扩成多台应用服务器时，这件事才需要重新设计。我已要求把这条写进发布检查项，避免哪天悄悄扩了机器而没人记得这个约束。

**其二，一件需要您拿主意的事："二级分区主表"这个数字，真实环境下很可能显示成"≥4（未完成）"而不是"4"。**

原因是这一轮把统计范围改对了——原来只查分片表，会漏掉新语法的表；现在要查库里所有的表，才能保证不漏。但代价是要查的表多了一个数量级，而这个统计有 180 秒的时间预算，还得和原来的统计共用。查不完的部分，系统会老实说"还没查完"，于是页面显示的是"至少 4 张，未查完"。

**方案本身是诚实的**——它宁可说"没查完"，也不肯把没查的当成零，这个方向我完全支持。但我要让您知道后果，因为您要的是一个数量。三条路：

1. **接受"下界+未完成"**（方案现状）。好处是永远不会给您一个偏小的假数字；坏处是页面上经常看不到一个干净的数。
2. **给这一步单独放宽时间预算**（比如主表识别单独给 300 秒，不挤占原统计）。多数库能查完，但整个统计变慢。
3. **先测再定**——我已经要求 O 在开工前做一次只读实测：数一下目标实例每个库有多少张表，再测几十次查询的实际耗时，两个数一乘就知道 180 秒够不够。**我建议走这条**，测完再回来定 1 还是 2，不用现在猜。

另外我已经要求 O 加一条改进：查不完的时候，**优先查最可能是二级分区主表的那些表**（原来的分片表排第一批），这样即使被截断，那个"≥N"也是尽可能接近真值的下界，而不是一个随机的下界。这条不用您决策，是纯技术改进。

## 7. 评审边界声明

1. 本轮**未修改任何代码**，也未修改设计文档与 O 的答复文档。本报告是本轮唯一交付物。
2. O 提出的六条技术纠正，我**没有采信其自述**，全部独立复核：真实 `SQLParser` 三出口复现（`unittest.mock.patch` 注入，不改仓库文件）、`RuleChecker.audit_sql` 实跑、`kind_map`/`only_base` 组成读码、`audit_history` 建表语句、`daily_inspect.py` 的 CSP 与 nonce、`ParsedSQL` 构造点与 `asdict` 出口全仓检索。结论是六条全部成立。
3. 本轮新发现的证据同样是实跑得到：`sqlglot 30.14.0` 顶层节点类型表、Command 路径语句的当前审核结果、部署拓扑取自 `deploy/` 实际配置文件。
4. 本轮仍**没有**内网 TDSQL 实例，因此 REQ-02 的 `SHOW FULL TABLES` 实际返回口径、`SHOW CREATE TABLE` 往返耗时、REQ-04 的 71 MiB 样本耗时**均未实测**——P2-05(b) 的容量核算正是为此设的闸。我不会把"设计写了"说成"已验证"。
5. 本报告未评价两项裁决的内容本身，只核验其落文是否与 §7 转录一致，以及落文引入的技术后果（见 §6 第二件事）。

---

评审人：智能体 A（ClaudeA）
评审对象：`docs/DETAIL-v1.6.3.4-报告实例标识与分区统计及审核网关修复.md` Rev.B（commit `2191d7b`）
提交给：Mr.Linsang
