# REVIEW3-v1.6.3.0 深度诊断·表类型统计（G14）第三次设计评审报告

| 项 | 内容 |
|---|---|
| 评审对象 | `DESIGN-v1.6.3.0-深度诊断表类型统计子模块详细设计说明书.md` **Rev.J** |
| 评审基线 | `main` / `88b2eb6`（2026-09-01） |
| 前次报告 | `REVIEW2-v1.6.3.0-深度诊断表类型统计子模块设计评审报告.md` |
| 目标版本 | v1.6.3.0 |
| 评审人 | Codex（智能体 O） |
| 评审日期 | 2026-09-01 |
| 评审结论 | **不通过，退回修订 Rev.K；4 项 P1 关闭前不得按附录 A 落盘实施** |

---

## 1. 评审范围

本轮评审 A 提交的 Rev.J 设计正文、附录服务/API/DDL/测试/前端成品代码，
重点验证第二轮报告的 5 项 P1、3 项 P2 和 1 项文档问题是否在真实数据路径中闭环。
设计书中的“代码已完成”“照图施工”属于被评审材料，不替代独立验证。

当前 `main` 仍未落盘以下四个实现文件：

- `backend/services/table_type_stats_service.py`；
- `backend/api/table_type_stats.py`；
- `backend/schema/v13/130_table_type_stats.sql`；
- `tests/test_table_type_stats.py`。

所以本轮可以评审附录代码逻辑、测试有效性、现有工程契合度和零回归风险，
但不能在当前 `main` 复现设计书所述“83 passed, 1 skipped”。

内网实测口径没有被本轮推翻，`lzbj_ecif` 的验收基准继续为：

| 总表 | 单表 | 广播表 | 分片表 | 逻辑基线 | 二级分区物理子表 |
|---:|---:|---:|---:|---:|---:|
| 215 | 0 | 117 | 98 | 215 | 78 |

---

## 2. 执行摘要

Rev.J 对第二轮意见的接受和整改是实质性的，以下改动方向正确，应予保留：

- `_NameSpace` 不再用单值小写字典表示数据库命名空间；
- deadline 已前移到扫描槽内，并覆盖库枚举、基线查询和逐条 Proxy 命令的启动判断；
- 失败/跳过库不再进入实例级基线、子分区和重叠汇总；
- `_ensure_schema()` 已扩展到字段长度、字符集、可空性、默认值、自增及索引列序；
- 物理子表判定增加“候选自身不在 Proxy 结果中”；
- 历史告警增加总数和展开入口，结果消息已区分成功与部分失败；
- 迁移槽扫描器改为 `slot -> [files]`，能够识别同槽多文件；
- 测试文档已承认 skip 不是 pass，设计阶段数字不能替代发布回归。

但复核真实调用链后仍发现 **4 项 P1 阻断、3 项 P2 必须整改和 1 项文档一致性问题**：

| 级别 | 数量 | 主要影响 |
|---|---:|---|
| P1 | 4 | 指定库统计仍可合并另一个真实库；210 秒上界不成立；旧测试会阻断未来迁移；集成测试可误删非测试库数据 |
| P2 | 3 | 结构契约缺少自动化定向证据；历史抽屉清理只写计算属性；零有效库仍提示“部分完成” |
| DOC | 1 | 版本头、时长说明和子分区口径仍有 Rev.I/G 陈旧文本 |

Rev.J 不需要推翻重做，Rev.K 可在现有架构上定向修正。

---

## 3. 第二轮问题关闭复核

| 第二轮编号 | Rev.J 状态 | 第三轮复核意见 |
|---|---|---|
| P1-01 数据库名小写合并 | **部分关闭** | 全库统计的 `Sales`/`sales` 已分开，但指定单库时会在第二次解析中重新合并，见本报告 P1-01 |
| P1-02 180 秒不是真总预算 | **部分关闭** | 查询启动检查已改善，但预算异常触发连接重建，且 `/run` 的 schema 验收和落库不在 210 秒内，见 P1-02 |
| P1-03 失败/跳过库污染汇总 | 已关闭 | `eligible` 先确定，实例级汇总按 owner 过滤 |
| P1-04 结构验收不完整 | 代码方向已关闭，测试未闭环 | 字段契约实现基本覆盖要求，但第二轮指定的五类畸形没有落为自动化用例，见 P2-01 |
| P1-05 槽位护栏落盘后自失败 | **部分关闭** | 落盘前后已能通过，但护栏要求 v13/130 永远是最大槽，会阻断未来 v13/131、v14/140，见 P1-03 |
| P2-01 后缀逻辑表误判子表 | 已关闭 | 候选自身在 Proxy 时不再剔除，正反用例均有 |
| P2-02 历史告警静默截断 | **部分关闭** | 展开机制已增加，但打开历史时向只读 computed 赋值，旧告警无法正确清空，见 P2-02 |
| P2-03 全失败仍绿色成功 | **部分关闭** | 全失败已改红色，但“失败 + 跳过覆盖全部库”仍被称为部分完成，见 P2-03 |
| DOC-01 测试依赖说明失真 | **部分关闭** | 数量已更新，但可用性探测与实际 DROP 所用连接不是同一配置，见 P1-04 |

---

## 4. P1 阻断问题

### P1-01 指定单库时，大小写兄弟库仍会被重新归入目标库

**位置**：附录 A.1 `_collect_distributed()`：

```python
target = _NameSpace(dbs)
...
owner = target.resolve(qual)
```

Rev.J 在 `_extract_pairs()` 中已经通过全实例 `known` 把 Proxy 行的库名解析成真实 canonical 名，
这一步是正确的。但随后又用只包含目标库的 `target` 做一次大小写不敏感回退。

**可复现场景**：

1. 实例真实存在两个库：`Sales` 和 `sales`；
2. 用户明确指定只统计 `Sales`，所以 `target = _NameSpace(["Sales"])`；
3. Proxy 命令是实例级作用域，同时返回 `Sales.t_upper` 和 `sales.t_lower`；
4. `known.resolve("sales")` 精确得到真实库 `sales`；
5. `target.resolve("sales")` 没有精确命中，但它的小写候选只有 `Sales`，于是回退成 `Sales`；
6. `sales.t_lower` 被错误计入用户指定的 `Sales`。

这意味着 Rev.J 的全库用例能通过，指定库仍会把两个真实 schema 合并，四个主数字错误。
当前 `test_t2r01_case_variant_databases_are_not_merged` 只测全库，
`test_t2r01b_wrong_case_database_is_rejected` 只测输入校验，都没有覆盖该调用链。

**整改要求**：`qual` 经 `known.resolve()` 后已经是 canonical name，目标过滤必须使用精确成员判断：

```python
if qual not in target:
    continue
owner = qual
```

不得对 canonical name 再做第二次 CI 回退。若需要兼容 Proxy 大小写变化，只能在第一次
`known.resolve()` 中按实例完整命名空间判定，不能在目标子集上重新解释。

**必须新增测试**：实例有 `Sales`/`sales`，指定 `database="Sales"`，
两库命令均返回实例级全量，断言只计 `Sales.t_upper`，`sales.t_lower` 必须被过滤。

### P1-02 210 秒“可证明墙钟上界”仍不成立

Rev.J 有两个独立问题共同推翻该结论。

#### 4.2.1 正常预算信号会被连接池当成连接异常并重建

**位置**：附录 A.1 `_collect_distributed()` 在连接上下文内部抛 `_BudgetExceeded`；
现有 `backend/services/tdsql_connector.py:287-307`。

当前代码在第二或第三条命令开始前发现 deadline 已过时：

```python
with tmp.get_connection() as conn:
    ...
    if _now() >= deadline:
        raise _BudgetExceeded()
```

`TDSQLConnectionPool.get_connection()` 会捕获穿出上下文的**所有**异常，关闭当前连接并立即
`self._create_connection()`。因此一个正常的“预算已耗尽”控制信号会：

- 关闭本来健康的连接；
- 在 deadline 之后新建一条目标连接；
- 额外消耗 `connect_timeout`；
- 若重连失败，用新连接异常覆盖 `_BudgetExceeded`，该库最终可能被误标为 `FAILED` 而非 `SKIPPED`。

此外，每库只在进入上下文前检查一次 deadline，随后 `_get_thread_connection()` 的建连和
`conn.select_db(db)` 已经发生，下一次 deadline 检查才位于第一条 Proxy 命令之前。
若 179 秒时进入该库，建连与 `COM_INIT_DB` 的等待时间也会叠加在预算之后，
它们没有进入 `180 + 最后一条命令 30` 的公式。

这既违反“到期后不再启动新操作”，也使 180+30 的推导少算了一次重连时间。
FakePool 只把 `generation += 1`，没有模拟真实 `_create_connection()`，现有 deadline 测试看不到该问题。

**整改要求**：预算耗尽不得以异常穿出 `get_connection()`。应在上下文内设置标志并正常退出，
退出后再把本库标记 `SKIPPED`；只有真实数据库/网络异常才允许触发连接池重建。

#### 4.2.2 `/run` 还有两段不受 deadline 约束的元数据库工作

`run_stats()` 在建立 deadline 前执行 `_ensure_schema()`；采集完成并释放扫描槽后，
又执行一次任务 INSERT、最多 500 次明细 INSERT 和 COMMIT。这些都不在 deadline 中，
元数据库连接配置也没有由本模块提供 30 秒读写上界。

所以 Rev.J §5.3、§9、KL-19 和专项验收声称的“`/run` 端到端墙钟不超过 210 秒”不成立。
即使目标采集严格控制在 210 秒，元数据库阻塞或 500 行逐条落库仍可继续占用 API 工作线程。

**整改要求**二选一：

1. 如果承诺的是目标实例采集上界，统一改名为“目标采集阶段上界”，删除 `/run` 端到端
   210 秒断言，并单独说明 schema 验收/持久化的边界；或
2. 如果继续承诺 `/run` 端到端上界，则必须把 schema 验收、持久化和 COMMIT 纳入同一
   deadline/超时控制，并用批量 INSERT 减少 500 次往返。

**必须新增测试**：

- deadline 在第二条命令后耗尽，断言真实/忠实 FakePool 不发生重建；
- `_create_connection()` 在预算信号路径上不得被调用；
- deadline 接近耗尽时，建连和 `select_db` 也必须计入可证明上界；
- 端到端上界测试必须包含 `_ensure_schema()` 和持久化阶段，不能只断言两个常量相加等于 210。

### P1-03 迁移槽护栏要求本模块永远是“最新迁移”，会阻断未来版本

**位置**：附录 A.4 `assert_slot_ok()`：

```python
others = ...  # 排除本模块自己的 v13/130
assert _OUR_SLOT > max(others)
```

该断言在 Rev.J 落盘时成立，但项目下一次合法增加 `v13/131_x.sql` 或 `v14/140_x.sql` 后，
`max(others)` 必然大于 `(13, 130)`，于是历史测试永久失败。Rev.J 甚至把
“我们不再是最大槽”写成必须拒绝的测试场景，这相当于禁止项目在 G14 之后再增加迁移。

迁移的正确永久不变量是“每个 `(version, sequence)` 槽位唯一、自己的槽位只有自己的文件”，
不是“每一个历史迁移永远保持最大”。“当前最大之后的下一个”只在选槽和首次落盘时有意义，
不应成为随仓库永久执行的历史断言。

**整改要求**：

- 保留全局同槽重复检测；
- v13/130 已落盘时只断言该槽有且只有 `130_table_type_stats.sql`；
- 删除“存在未来更高迁移就失败”的永久规则；
- 如需证明当时选槽连续，可断言已知前驱 v12/120 存在，或把“落盘前最大值”作为一次性评审证据，
  不能阻断未来合法迁移。

**必须新增测试**：v13/130 与未来 v13/131、v14/140 同时存在时护栏仍通过；
只有同一 `(version, sequence)` 出现两个文件时才失败。

### P1-04 元数据库集成测试缺少破坏性目标保护，可能 DROP 非测试库表

**位置**：附录 A.4 `MYSQL_AVAILABLE`、`g14_schema`、`_reset_g14_tables()`。

测试夹具每个用例前后都会执行：

```sql
DROP TABLE IF EXISTS table_type_stat_item;
DROP TABLE IF EXISTS table_type_stat;
```

但安全控制只有：

```python
os.environ.setdefault("SQLCHECK_DB_NAME", "tdsql_sqlcheck_test")
```

该语句位于 `svc` 已导入之后，而且 `setdefault` 不会覆盖外部已有配置。
如果执行测试的环境已经设置 `SQLCHECK_DB_NAME=tdsql_sqlcheck`，
`backend.services.database.MYSQL_CONFIG` 会继续指向真实元数据库，夹具将删除真实 G14 历史表。

同时，是否跳过测试是用 `TDSQL_TEST_HOST/PORT/USER/PASSWORD` 探测的，
真正执行 DROP 的却是 `SQLCHECK_DB_*`。两套配置可以指向完全不同的服务器：

- TDSQL_TEST 可连、SQLCHECK 指向生产：测试不跳过并执行破坏性 DROP；
- TDSQL_TEST 不可连、SQLCHECK 测试库正常：测试被错误跳过，发布证据缺失。

这与“不能影响项目现有功能和数据”的底线直接冲突。

**整改要求**：

1. 可用性探测和实际执行必须使用同一份 `SQLCHECK_DB_*` / `MYSQL_CONFIG`；
2. 在任何 DROP 前做失败关闭断言，例如数据库名必须精确等于批准的测试库
   `tdsql_sqlcheck_test`，否则直接 `pytest.fail`，不能只靠默认值；
3. 安全断言必须读取数据库模块已经生效的配置，而不是在 fixture 中事后修改环境变量；
4. 若允许自定义测试库，必须要求显式的破坏性测试开关，并在日志中打印目标 host/port/database。

**必须新增测试**：把有效配置指向 `tdsql_sqlcheck`，断言 fixture 在任何 DROP 前拒绝执行；
探测连接与 `_get_connection()` 的 host/port/database 必须一致。

---

## 5. P2 必须整改问题

### P2-01 第二轮要求的完整结构契约定向测试并未落盘

Rev.J 的 `_check_column()` 实现方向基本正确，但附录测试中实际仍只有：缺表、缺列、错类型、
缺索引、采集前拦截和干净结构六类。设计声称已验证的以下场景没有对应自动化代码：

- `detail VARCHAR(512)` 被收窄；
- `id` 丢失 `AUTO_INCREMENT`；
- `created_at` 缺少默认值；
- `stat_id` 变成可空；
- 字符列不是 `utf8mb4`；
- 索引列序或唯一性错误。

正文说“九种畸形场景由十项单测钉住”，§11 又改称“另在本地 MariaDB 逐项验证”，
两者与附录代码不一致。人工跑过一次不能防止落盘实现或未来修改回退。

**整改要求**：将以上场景做成参数化元数据库测试，逐项 ALTER 后断言
`_ensure_schema()` 在采集前失败；另增加干净 DDL 通过用例。测试数量与通过记录据实更新。

### P2-02 历史抽屉试图给只读 computed 赋值，旧告警不会正确清空

**位置**：附录 A.5.5：

```javascript
const tabletypeDetailWarnings=computed(...);
...
tabletypeDetailItems.value=[];tabletypeDetailWarnings.value=[];
```

`tabletypeDetailWarnings` 是无 setter 的计算属性。给它的 `.value` 赋值会产生 Vue 只读警告，
不会清空真正的数据源 `tabletypeDetailAll`。重新打开历史抽屉、特别是切换实例后，
上一条历史记录的告警会继续显示，直到用户选择新行且请求成功。

**整改要求**：打开抽屉时清理源状态：

```javascript
tabletypeDetailItems.value=[];
tabletypeDetailAll.value=[];
tabletypeDetailExpand.value=false;
```

切换历史行开始加载时也应清空或显示 loading，避免请求期间展示上一行告警。

### P2-03 “失败 + 跳过覆盖全部库”仍被误称为“部分完成”

当前前端只有 `failed_databases >= database_count` 才显示红色失败。
若 100 个库中 60 个失败、40 个超预算跳过，则没有任何一个有效库，
但页面进入 `bad || skip` 分支并提示“部分完成”。

**整改要求**：以有效库数判断结果：

```javascript
const ok = database_count - failed_databases - skipped_databases;
```

`ok == 0 && database_count > 0` 时应显示“无有效统计结果”；仅 `ok > 0` 才能称“部分完成”。
增加全失败、全跳过、失败+跳过、真正部分成功四种前端测试或可执行验收。

---

## 6. 文档一致性问题

### DOC-01 附录仍保留与 Rev.J 实现不一致的版本和口径文字

以下文本需要统一：

- API 文件头仍写 `DESIGN ... Rev.G`；
- v13/130 SQL 文件头仍写 `Rev.I`；
- `run_stats` 注释仍写“单次最长占用 180 秒”，正文已改为 210 秒软上界；
- 前端口径说明只写“父表在 Proxy 中”即可剔除，没有写 Rev.J 新增的
  “候选自身不在 Proxy 中”；
- 正文宣称 `/run` 端到端 210 秒，与实际 deadline 仅覆盖目标采集不一致。

Rev.K 应统一模块头、DDL 头、UI 口径、时长名称和验收清单，避免实施者复制陈旧文字。

---

## 7. Rev.K 必须新增或修订的测试

| 编号 | 场景 | 通过条件 |
|---|---|---|
| T3-R01 | 指定大小写兄弟库 | 指定 `Sales` 时不得计入实例级返回中的 `sales.*` |
| T3-R02 | 预算正常退出 | deadline 到期不重建连接，状态为 SKIPPED |
| T3-R03 | 预算信号重连失败 | `_create_connection()` 不得被预算控制流调用，原信号不被覆盖 |
| T3-R04 | 真实时长边界 | 上界定义覆盖其声称的全部阶段；不得只检查常量算术 |
| T3-R05 | 未来迁移兼容 | v13/131、v14/140 存在时 v13/130 护栏继续通过 |
| T3-R06 | 同槽冲突 | 相同 `(version, sequence)` 双文件必须失败 |
| T3-R07 | 破坏性测试保护 | 非批准测试库时，在执行任何 DROP 前失败关闭 |
| T3-R08 | 完整 schema 契约 | 长度、自增、默认值、NULL、字符集、索引列序/唯一性逐项失败关闭 |
| T3-R09 | 历史状态清空 | 重开/切换实例时无旧告警、无只读 computed 写入 |
| T3-R10 | 零有效库提示 | 全失败、全跳过、混合无成功均不得显示“部分完成” |

实施文件落盘后仍须执行：

```bash
python -m pytest tests/test_table_type_stats.py -q
python -m pytest tests/test_rbac_path_coverage.py tests/test_app_routes_integrity.py -q
python -m pytest tests/test_rules.py tests/test_sit_rules.py tests/test_sit_v1_rules.py -q
python -m pytest tests/ -q
```

并完成：

- `lzbj_ecif` 六个数字逐项对齐；
- 指定单库与全库两种模式分别验证；
- G14 与既有审核/扫描并发，确认共享槽位且既有功能无影响；
- 全新元数据库及存量升级各启动两次；
- 真实端到端耗时记录必须与 Rev.K 最终定义的上界一致；
- 破坏性集成测试日志明确显示测试 host/port/database。

---

## 8. Rev.K 复审准入条件

1. 本报告 P1-01～P1-04 全部落实到正文、附录代码和测试；
2. P2-01～P2-03 有可执行自动化或明确的前端验证；
3. 第二轮已经关闭的 `eligible` 汇总、完整字段检查和子分区三条件不得回退；
4. 迁移护栏既能防重复，又不阻断任何未来更高迁移；
5. 所有 DROP 类测试对目标数据库失败关闭；
6. 实现文件真实落盘后重新给出测试结果，设计阶段 83+1 不作为发布证据；
7. 当前全量测试、RBAC/路由和 119 条规则回归全部通过；
8. 内网 UAT 与零回归并发、时长证据齐全。

---

## 9. 最终结论

Rev.J 已关闭第二轮多数问题，统计口径、失败汇总和结构验收的主体方向已经接近可实施状态。
但指定单库仍可能合并另一个真实数据库，deadline 正常控制流会触发连接重建，
迁移护栏会阻断后续版本，集成测试又缺少非测试库 DROP 保护。

**本次评审结论：不通过，退回 A 修订为 Rev.K。**

四项 P1 关闭、实现文件落盘并完成真实回归前，不得把 Rev.J 作为 v1.6.3.0 实施定版。
