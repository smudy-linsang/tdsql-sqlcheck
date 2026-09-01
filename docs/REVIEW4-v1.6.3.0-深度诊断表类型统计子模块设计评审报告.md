# REVIEW4-v1.6.3.0 深度诊断·表类型统计（G14）第四次设计评审报告

| 项 | 内容 |
|---|---|
| 评审对象 | `DESIGN-v1.6.3.0-深度诊断表类型统计子模块详细设计说明书.md` **Rev.K** |
| 评审基线 | `main` / `1ec7e80`（2026-09-01） |
| 前次报告 | `REVIEW3-v1.6.3.0-深度诊断表类型统计子模块设计评审报告.md` |
| 目标版本 | v1.6.3.0 |
| 评审人 | Codex（智能体 O） |
| 评审日期 | 2026-09-01 |
| 评审结论 | **不通过，退回修订 Rev.L；2 项 P1 关闭前不得按附录 A 落盘实施** |

---

## 1. 评审范围与证据边界

本轮针对 A 提交的 Rev.K 正文、附录服务/API/DDL/测试/前端成品代码进行第四次复核，
重点验证第三轮报告的 4 项 P1、3 项 P2、1 项文档问题是否在真实调用链中闭环，
并检查整改是否产生新的正确性、资源隔离或测试安全问题。

当前 `main` 仍未落盘以下四个实现文件：

- `backend/services/table_type_stats_service.py`；
- `backend/api/table_type_stats.py`；
- `backend/schema/v13/130_table_type_stats.sql`；
- `tests/test_table_type_stats.py`。

因此，本轮能够评审附录代码逻辑、测试有效性、现有连接池/权限/迁移机制的契合度，
但不能在当前仓库直接复现设计书所述“105 passed, 1 skipped”。该数字继续只视为 A 的
设计阶段记录，不能作为实现落盘后的发布证据。

内网实测口径未被本轮推翻，`lzbj_ecif` 的 UAT 对数基准继续为：

| 总表 | 单表 | 广播表 | 分片表 | 逻辑基线 | 二级分区物理子表 |
|---:|---:|---:|---:|---:|---:|
| 215 | 0 | 117 | 98 | 215 | 78 |

本轮还对两个关键运行时前提做了独立核查：

1. 现有 `TDSQLConnectionPool._get_thread_connection()` 在复用连接前会执行 `ping()`，
   `get_connection()` 对上下文内异常还会立即 `_create_connection()`；
2. 当前环境 PyMySQL 的 `_read_bytes()` 在每次底层读取前设置 `read_timeout`，该参数是
   “从连接读取的超时”，不是整条 SQL 的绝对墙钟 deadline。

参考：[PyMySQL Connection 参数说明](https://pymysql.readthedocs.io/en/latest/modules/connections.html)、
[MySQL INFORMATION_SCHEMA 大小写比较说明](https://dev.mysql.com/doc/refman/8.0/en/charset-collation-information-schema.html)。

---

## 2. 执行摘要

Rev.K 对第三轮意见的接受和整改是实质性的，下列内容已正确关闭，应全部保留：

- Proxy 行经全实例命名空间解析后，目标过滤改为精确成员判断；
- 预算耗尽改为标志位并正常退出连接上下文，不再触发健康连接重建；
- `_ensure_schema()` 的 11 类畸形结构已落实为参数化元数据库测试，并有干净 DDL 反向护栏；
- 迁移槽护栏不再要求 v13/130 永远是最大迁移，未来 v13/131、v14/140 可共存；
- DROP 类集成测试在破坏性操作前校验已生效的 `MYSQL_CONFIG.database`；
- 历史抽屉改为清理 `tabletypeDetailAll` 数据源，并增加 loading 状态；
- “失败 + 跳过覆盖全部库”已按有效库数显示为无有效结果；
- 明细落库改为批量 INSERT，500 库由 500 次往返降为 5 次。

但第四轮沿真实数据库和连接池路径复核后，仍发现 **2 项 P1、2 项 P2、1 项文档一致性问题**：

| 级别 | 数量 | 主要影响 |
|---|---:|---|
| P1 | 2 | 指定单库的基线仍可能吸收大小写兄弟库；215 秒“可证明上界”仍不成立 |
| P2 | 2 | 无业务库仍弹绿色成功；元数据库可用性探测没有连接目标数据库 |
| DOC | 1 | 210 秒、旧测试数、迁移护栏旧规则及“代码已完成”等文字互相矛盾 |

Rev.K 无需推翻整体方案；Rev.L 可沿现有结构做定向修订。

---

## 3. 第三轮问题关闭复核

| 第三轮编号 | Rev.K 状态 | 第四轮复核意见 |
|---|---|---|
| P1-01 指定单库吸收大小写兄弟库 | **Proxy 主路径已关闭，基线路径部分关闭** | `_collect_distributed()` 已精确过滤，但 `_collect_baseline()` 仍用目标子集做 CI 回退，见 P1-01 |
| P1-02 预算异常触发重建、上界失真 | **预算控制流已关闭，上界仍未关闭** | 标志位退出正确；215 秒公式仍漏掉连接池路径，且 `read_timeout` 不是整条命令的墙钟上界，见 P1-02 |
| P1-03 迁移护栏阻断未来迁移 | 已关闭 | 永久不变量改为同槽唯一、自有槽独占、前驱存在，未来迁移正向用例已增加 |
| P1-04 破坏性测试可能误删生产数据 | 核心安全问题已关闭 | DROP 前数据库名失败关闭有效；可用性探测仍未使用完整目标配置，降为 P2-02 |
| P2-01 完整 schema 契约缺自动化证据 | 已关闭 | 11 条参数化畸形结构 + 干净 DDL 用例已补齐 |
| P2-02 历史抽屉写只读 computed | 已关闭 | 已清理源状态，切行时先清空并置 loading |
| P2-03 零有效库仍提示“部分完成” | **部分关闭** | `n>0` 的全失败/跳过已关闭，但 `n==0` 仍走绿色成功分支，见 P2-01 |
| DOC-01 版本、时长和口径文字陈旧 | **部分关闭** | 文件头和 UI 子分区口径已更新，但仍有多处 Rev.G/J 文本，见 DOC-01 |

---

## 4. P1 阻断问题

### P1-01 指定单库的 `information_schema` 基线仍会在目标子集上二次解释库名

**位置**：附录 A.1 `_collect_baseline()`：

```python
out = {d: {"base": set(), "view": set()} for d in dbs}
...
wanted = _NameSpace(dbs)
...
key = wanted.resolve(schema)
```

Rev.K 已在 Proxy 路径确立了正确原则：库限定名经**全实例命名空间**解析为 canonical name
之后，只能做目标集合的精确成员判断，不能再对目标子集做 CI 回退。但基线路径没有执行
同一原则。用户指定 `database="Sales"` 时，`dbs` 只有 `Sales`，所以：

1. `wanted = _NameSpace(["Sales"])`；
2. 若 TDSQL/MySQL 版本、排序规则或代理改写使 `information_schema` 查询同时返回
   `Sales` 与真实存在的兄弟库 `sales`；
3. `wanted.resolve("sales")` 会把它作为唯一大小写候选回退成 `Sales`；
4. `sales` 的 BASE TABLE/VIEW 被静默并入 `Sales`。

分布式实例会得到错误的 `baseline_tables`、子分区数和对账告警；集中式实例的
`total_tables`、`single_tables` 直接来自该基线，因此四个主数字也会错误。

MySQL 官方明确提示：`INFORMATION_SCHEMA` 字符列的比较结果会受对象名、文件系统和
排序规则影响，调用方应根据需要显式选择二进制或非二进制比较。服务层不能把“当前实测
恰好精确过滤”当成永久应用不变量。

现有 `test_t3r01_named_database_must_not_absorb_case_sibling` 没有发现该问题，原因是
`FakePool._execute()` 在返回行之前用 Python 的 `db not in wanted` 做了精确过滤；它模拟了
理想结果，而不是验证服务面对多返回一行时仍能失败关闭。

**整改要求**：

- `_collect_baseline()` 接收全实例 `known`；
- 先用 `known.resolve(schema)` 做唯一一次 canonical 解析；
- 再用目标集合精确判断，非目标 canonical name 直接丢弃；
- SQL 可额外使用适合目标版本的二进制比较作为减载，但应用层仍须精确防御，不能只依赖 SQL 排序规则。

示意：

```python
target = _NameSpace(dbs)
owner = known.resolve(schema)
if owner is None or owner not in target:
    continue
out[owner][...].add(name)
```

**必须新增测试**：FakePool 在指定 `Sales` 时故意返回 `Sales.t_upper` 和 `sales.t_lower`
两条基线行，断言集中式与分布式均只计 `Sales.t_upper`。测试替身不得先替服务精确过滤。

### P1-02 `MAX_COLLECT_WALL_SECONDS = 215` 仍不是可证明的墙钟上界

Rev.K 把承诺缩小到“目标采集阶段”是正确方向，但公式仍有两个独立缺口。

#### 4.2.1 `read_timeout=30` 是单次读取等待上限，不是整条命令总耗时上限

设计把一条 Proxy 命令的最长耗时直接等同于 `COMMAND_READ_TIMEOUT=30`。实际 PyMySQL
在底层 `_read_bytes()` 的每次读取前设置 socket timeout；只要服务端持续在小于 30 秒的
间隔内发送数据包，一条大结果集的整体读取时间就可以超过 30 秒。`DictCursor.execute()`
还会缓冲结果集，`fetchall()` 返回前并不存在一个独立的“整条命令 30 秒”计时器。

因此即使忽略连接重建，`180 + 30/35` 也不能推出目标采集阶段的硬墙钟上界。

#### 4.2.2 现有连接池在最后一次 checkpoint 后还可能执行 ping、建连、切库和重建

现有 `backend/services/tdsql_connector.py` 的真实路径是：

```python
conn.ping(reconnect=False)       # 复用线程连接前
conn = self._create_connection() # ping 失败后创建
...
conn.select_db(db)
...
self._local.conn = self._create_connection()  # 上下文异常后立即重建
```

在接近 deadline 时通过“进入某库前”的检查后，后续最坏路径不只是 Rev.K 写的
`connect 5 + select_db 30 = 35`。复用连接可能先在 `ping` 等待 30 秒；ping 失败后再建连
5 秒；`select_db` 又可能等待 30 秒并抛错；异常穿出后连接池还会重建 5 秒。即使暂时把
每次 read timeout 当成操作总上限，该路径也可能是 **30 + 5 + 30 + 5 = 70 秒**。
命令执行异常路径同样会在 30 秒之后增加一次 5 秒重建。

Rev.K 的 T3-R04 仍然只是：

- 断言三个常量相加等于 215；
- 统计源码中 checkpoint 字符串出现次数；
- 断言存在 `budget_hit = True`。

它没有执行真实池的 ping/select/rebuild 路径，也无法证明 PyMySQL 的整条命令墙钟语义。
所以测试名称中的 `covers_every_declared_phase` 与实际覆盖范围不符。

**整改要求二选一**：

1. **推荐**：把 180 秒定义为软预算——到期后不再主动启动下一阶段/下一命令；删除
   `MAX_COLLECT_WALL_SECONDS=215`、“可证明墙钟上界”和按 215 秒给出的零回归承诺，
   如实登记最后一个已启动 I/O 及驱动读取可能继续等待；或
2. 若业务必须有硬上界，则需要独立于 PyMySQL `read_timeout` 的绝对 deadline/watchdog，
   到期主动关闭专用连接，并明确处理 ping、select_db、异常重建和结果集读取。仅增加常量
   或源码字符串断言不能关闭该问题。

**必须新增测试**：

- 忠实连接池替身模拟 `ping → connect → select_db → exception rebuild`，验证最终定义的边界；
- 模拟多次读取每次均小于 read timeout、但整条命令总时长超过 30 秒；
- 若选择软预算，测试只断言 deadline 后不再启动新操作，不再断言不存在的硬墙钟上界。

---

## 5. P2 必须整改问题

### P2-01 `database_count == 0` 时仍弹绿色“统计完成”

**位置**：附录 A.5.5 `runTableTypeStats()`：

```javascript
if(n>0&&ok<=0){ ...error... }
else if(bad||skip){ ...warning... }
else{ElementPlus.ElMessage.success(`统计完成：${tail}`)}
```

当账号看不到任何业务库，或实例确实没有业务库时，服务返回：

- `database_count = 0`；
- `failed_databases = 0`；
- `skipped_databases = 0`；
- `NO_BUSINESS_DB` 告警。

此时两个条件都为假，页面弹绿色“统计完成：0 个库 / 0 张表”。这会把“账号可见范围可能
过窄”弱化成可信的零表结论，与 W6 自己的说明不一致。

**整改要求**：`n===0` 单独显示 warning（建议“未发现可统计的业务库，请确认账号权限或实例是否为空”）；
`n>0 && ok<=0` 显示 error；只有 `ok>0` 且无失败/跳过时显示 success。

**必须新增测试/验收**：无业务库、全失败、全跳过、失败+跳过、真正部分成功、全部成功六种分支逐一核对。

### P2-02 元数据库可用性探测并未使用与执行相同的完整配置

**位置**：附录 A.4 `_probe_metadata_db()`：

```python
cfg = effective_db_config()
conn = pymysql.connect(host=cfg["host"], port=cfg["port"],
                       user=cfg["user"], password=cfg["password"],
                       connect_timeout=3)
```

Rev.K 正文和 `test_t3r07c_probe_and_execution_use_the_same_config` 均声称探测与 DROP 使用
同一配置，但探测没有传 `database=cfg["database"]`。因此它只证明“账号能连到服务器”，
不能证明实际目标数据库存在且可进入：

- 服务可连但测试库无权限时，`MYSQL_AVAILABLE=True`，集成用例不会 skip，之后在 fixture 中 error；
- 配置库名拼错时，探测仍可能成功；
- 当前测试只检查函数源码出现 `effective_db_config()`，以及字典含 `database` 键，
  没有断言 `pymysql.connect()` 真正收到该键。

DROP 前的数据库名守门人仍能阻止误删生产库，所以本项降为 P2，而不是再次认定 P1-04 未关闭。

**整改要求**：探测显式连接 `cfg["database"]`，并以 monkeypatch 捕获 `pymysql.connect`
实参，断言 host/port/user/database 与 `_get_connection()` 的生效配置一致。若设计有意允许
`ensure_db()` 自动创建测试库，则正文应明确“探测服务器、随后创建测试库”，不能继续称为
“与 DROP 完全相同的配置”。

---

## 6. 文档一致性问题

### DOC-01 Rev.K 仍混有旧上界、旧测试记录和旧迁移规则

至少以下当前态文字需要统一：

1. KL-6 仍写“单次墙钟上界 210 秒”，与 Rev.K 的 215 秒及“仅目标采集阶段”定义冲突；
2. 附录 A 总说明仍写“Rev.G、66 项通过 + 1 项跳过”，而文档头写 Rev.K、105 + 1；
3. `test_migration_slot_is_available_and_unique()` 的说明仍列出“自己的槽位是所有别人槽位之后的下一个”，
   与 Rev.K 已删除永久最大槽规则的实现相反；
4. 文档状态写“设计与代码已完成”，但版本记录又明确“仓库代码仍零改动”，且四个实现文件实际不存在；
5. T3-R04 名称和说明写“覆盖全部声明阶段”，实际只做常量与源码文本断言。

Rev.L 应把“附录成品代码已编制”和“仓库实现已落盘”分开表述，并统一当前测试数、
预算语义和迁移永久不变量。历史修订记录可保留旧数字，但当前态章节不能继续引用旧结论。

---

## 7. Rev.L 必须新增或修订的测试

| 编号 | 场景 | 通过条件 |
|---|---|---|
| T4-R01 | 指定库基线含大小写兄弟库行 | FakePool 故意多返回 `sales.*`，指定 `Sales` 时集中式/分布式均不得吸收 |
| T4-R02 | 真实连接池尾部路径 | ping、建连、select_db、异常重建均进入最终预算模型；不得只验常量算术 |
| T4-R03 | 多包持续读取 | 单次读取未超时但总时长超过 30 秒时，测试结果符合“软预算”或真正硬 deadline 的最终定义 |
| T4-R04 | 无业务库提示 | `database_count=0` 不得出现绿色“统计完成” |
| T4-R05 | 探测完整配置 | 捕获 connect 实参，确认 database 与实际执行目标一致 |
| T4-R06 | 当前态文档一致性 | 旧 210 秒、Rev.G 测试数、永久最大槽规则不再出现在当前态说明中 |

实现文件落盘后仍须执行：

```bash
python -m pytest tests/test_table_type_stats.py -q
python -m pytest tests/test_rbac_path_coverage.py tests/test_app_routes_integrity.py -q
python -m pytest tests/test_rules.py tests/test_sit_rules.py tests/test_sit_v1_rules.py -q
python -m pytest tests/ -q
```

并完成：

- `lzbj_ecif` 六个数字逐项对齐；
- `Sales`/`sales` 全库与指定单库两种模式分别验证，基线与 Proxy 两条链路都覆盖；
- G14 与既有 SQL 审核、慢查询扫描、巡检并发，确认共享槽位无泄漏；
- 全新元数据库及存量升级各启动两次；
- 破坏性集成测试日志明确显示并实际连接批准的 host/port/database；
- 若保留硬上界，使用真实驱动路径给出可复现证据；若改为软预算，UI/文档不得再承诺硬秒数。

---

## 8. Rev.L 复审准入条件

1. P1-01 的基线路径使用全实例 canonical 解析 + 目标精确过滤；
2. P1-02 要么实现可证明的绝对 deadline，要么删除 215 秒硬上界并统一为软预算；
3. P2-01、P2-02 有可执行测试或明确的前端验收；
4. Rev.K 已关闭的预算正常退出、未来迁移兼容、DROP 失败关闭、完整 schema 契约不得回退；
5. 当前态文档不再混用 Rev.G/J 的测试数、时长和迁移规则；
6. 四个实现文件真实落盘后重新给出测试结果，设计阶段 105+1 不作为发布证据；
7. 当前全量测试、RBAC/路由和 119 条规则回归全部通过；
8. 内网 UAT 与零回归并发证据齐全。

---

## 9. 最终结论

Rev.K 已关闭第三轮大多数实质问题，统计口径、失败隔离、结构验收、迁移兼容、测试库
防误删和历史状态清理均明显趋于可实施。剩余问题集中在两个边界：同一“只解析一次”的
库名原则没有覆盖 `information_schema` 基线，以及把连接级 read timeout 错当成整条命令
墙钟 timeout，导致 215 秒仍不能被证明。

**本次评审结论：不通过，退回 A 修订为 Rev.L。**

两项 P1 关闭、实现文件落盘并完成真实回归前，不得把 Rev.K 作为 v1.6.3.0 实施定版。
