# v1.6.3.6 内网大库问题修复 · 第二轮独立 SIT 测试报告

| 项 | 内容 |
|---|---|
| 版本 | v1.6.3.6（受测提交 `be9374d`，工作基线 `877f46b`） |
| 对照基线 | `efe0972`（`be9374d` 的直接父提交，即第一轮受测态） |
| 测试类型 | 第二轮 SIT（第一轮 8 项整改复验 + 整改引入的次生问题排查） |
| 测试人 | 智能体A（独立评审/测试） |
| 报告日期 | 2026-09-11 |
| 送呈 | Mr.Linsang |

---

## 0. 结论摘要

**第一轮 8 项：5 项通过，3 项部分通过。无 BLOCK 级问题，全量回归零新增失败。
本轮新发现 7 项（MAJOR 5 / MINOR 2），其中 3 项是几行代码的事。**

| 判定项 | 结论 |
|---|---|
| 第一轮 B-01（大库丢 96.7% 明细） | **通过** —— 同场景 E2E 无损 6097/6097 |
| 第一轮 B-02（子表识别违反 Rev.G） | **部分通过** —— 3 条件已落 2.5 条，Rev.J 第 3 条未实现 |
| 第一轮 B-03（回归锁假绿） | **部分通过** —— 预检锁已真调 publish，但同一假绿模式复发在新的阈值锁上 |
| 第一轮 M-01 / M-02 / N-01 / N-02 | **通过** |
| 第一轮 M-03（fail-open + 历史侧不可见） | **部分通过** —— 分类已做，持久可见性仍缺 |
| 整改是否引入回归 | **否** —— 失败/错误节点集合与基线逐条一致（484 = 484） |
| 放行建议 | **可以进 UAT**；R2-M-01 / R2-M-03 / R2-M-04 三项须在**准出门禁前**补齐 |

一句话：**Q 这轮改得是实的，第一轮那两个要命的问题（丢数据、误杀真表）确实按我的首选方案解决了；
剩下的主要是"锁没锁住"和一处判据没做满。**

---

## 1. 第一轮 8 项整改复验

### 1.1 B-01（高）大库历史报告静默丢 96.7% —— **通过**

Q 采纳了我的首选方案：阈值不再写死，改为按包限自适应
（`metadata_audit_repository.py:387-388`）：

```python
adaptive_threshold = (int((max_pkt - _PACKET_MARGIN) / _ESCAPE_FACTOR) if max_pkt else MAX_DB_PAYLOAD_THRESHOLD)
```

真实 MySQL 协议库（`max_allowed_packet=67108864`）E2E 复测，**与第一轮完全相同的场景**：

```
自适应压缩阈值 = (64MiB - 64KiB)/1.25 = 53,634,662 (51.1 MiB)   [第一轮写死 32 MiB]

A 内网同量级(42MiB级): raw=35,561,199B (33.9MiB) → 落库 id=1
   total_sql列=6097  results_json条数=6097/6097  【无损】
   /report/1/sql 将输出 6097 条 DDL（应 6097）
B 20MiB级:            raw=21,672,233B (20.7MiB) → 【无损】6097/6097
```

第一轮同一场景是 6097 → 203 条（丢 96.7%）。**修复有效。**

另外这个设计是自洽的：阈值 `(max_pkt-margin)/1.25` 与预检 `raw*1.25+margin` 互为逆运算，
压缩一旦触发就必然能过预检，不会出现"压了还是被拦"的死循环。这点我认可。

### 1.2 B-02（高）子表识别违反 Rev.G/P1-03 —— **部分通过**

已落实 2.5 条：

| 判据 | 状态 | 实测 |
|---|---|---|
| 条件 1：集中式一律不剔除 | ✅ | `('cus_bas_merge_log_tdsql_subp190001','centralized')` → False |
| 条件 2：`\d+` 至少一位数字 | ✅ | `biz_tdsql_subp` / `foo_tdsql_shard` → False |
| 条件 3：父表须在清单中 | ⚠️ **打了折** | 用的是 information_schema 枚举集，不是 Proxy 逻辑表集 |
| Rev.J/P2-01 第 3 条：候选自身不在 Proxy 结果中 | ❌ **未实现** | 见下 |

**真实产物回放仍然干净**：`extracted_lzbj_ecif_20260910_224104.sql` 的 219 个真实对象名，
新判据命中 **0 个，误杀率 0.00%**；内网故障对象仍被正确跳过。

**但 Rev.G 原文点名的那个反例还在**：

```
orders_tdsql_subp202601  distributed  枚举集={orders, orders_tdsql_subp202601}
   → pipeline 新判据 = True（会被跳过）     ← 应为 False
   → 项目既有 _classify_subpartitions = 判为逻辑表，物理子表集为空（不误杀）
```

原因是 Q 把条件 3 实现成了「父表在**本库枚举清单**里」，而枚举清单来自
`information_schema.TABLES` —— 它**同时包含逻辑表和物理子表**。所以：

- 真子表 `cus_pub_translog_tdsql_subp202601`：父表在枚举里 → True ✅
- 真业务表 `orders_tdsql_subp202601`（`orders` 也真实存在）：父表也在枚举里 → True ❌

**这一条对它想防的场景完全没有分辨力。** 真正能分辨的是 Rev.J/P2-01 的第 3 条
（「候选自身出现在 Proxy 结果里」就直接证伪它是物理子表），因为 Proxy 只认逻辑表。

还有一处代码注释与事实不符（`metadata_audit_pipeline.py:44`）：

> 候选自身能否被 Proxy SHOW CREATE 由提取循环的 try/except 兜底（660 容错跳过）。

**这个兜底不成立**：前置过滤发生在 `SHOW CREATE` **之前**（第 184-192 行，命中即 `continue`），
被误判的表根本走不到第 194 行的 try/except。注释把一个够不着的机制写成了安全网。

### 1.3 B-03（高）回归锁假绿 —— **部分通过**

`test_publish_precheck_no_false_double` 已改为**真调 `publish()`**，用 33MB 区分带
（×1.25→41MB 放行；×2→66MB 拦），并顺带读回 `results_json` 校验列位。变异复验：
**把 `*2` 原样改回去，用例变红** ✅ —— 第一轮那条假绿确实堵上了。

但**同一个假绿模式复发在新加的阈值用例上**，详见第 2 节 R2-M-01。

### 1.4 M-01（中）余量归零 + 错误归因被误导 —— **通过**

两处都改了，两处都实测：

```
(a) 包限 16MiB + 不可压缩载荷(全违规 20.1MiB)：
    ✅ 预检拦住 code=PERSIST_PAYLOAD_TOO_LARGE
       消息: 审核结果转义后约 26373873 字节，超过元数据库 max_allowed_packet (16777216)；请评估元数据库容量后重试。

(b) 注入 INSERT 抛 1153 + rollback 再抛 2006：
    ✅ 抛出 MetadataJobError，消息携带真因 (1153, "Got a packet bigger than 'max_allowed_packet' bytes")
       —— 不再裸穿 2006、不再被误归因成「目标数据库连接被中断」
```

第一轮那条「1153 → 连接重置 → rollback 抛 2006 → MetadataJobError 抛不出来 → 甩锅给目标库」
的链路已经断掉。

### 1.5 M-02（中）限长锁加错位置 —— **通过**

截断挪到了 `extract_metadata` 返回处，所有下游写点天然安全：

| 跳过数 | skipped_objects（计数） | len(skipped_list) | 单次 progress_json |
|---|---|---|---|
| 120 | 120 | 50 | 9,084 B |
| 2,800 | 2,800 | 50 | 9,088 B |
| 5,800 | 5,800 | 50 | 9,088 B |

第一轮 5,800 跳过时单次 812,123 B、AUDITING 阶段重复写 25 次约 20.9 MiB；
现在恒定 ~9 KB。那句与代码不符的「128 KiB」注释也删了。

### 1.6 M-03（中）fail-open + 跳过在历史侧不可见 —— **部分通过**

| 我第一轮提的 | 状态 | 实测 |
|---|---|---|
| 跳过按原因分类 | ✅ | `tdsql_internal`（良性）/ `extract_failed`（异常）双计数，标记落到每条记录 |
| `artifacts/report.html` 口径行补跳过 | ✅ | 「枚举 3019 / 选中 3019 / 提取 219 / 跳过 2800（异常 2）」；缺字段时回退 0，不炸 |
| 异常超阈值时任务判 PARTIAL_SUCCESS 或报告顶部醒目告警 | ❌ | 只有 `logger.warning`，任务仍 **SUCCEEDED** |
| `audit_history` 增列持久化跳过计数 | ❌ | 21 列中 `skipped` 命中 **0** |
| 历史报告 HTML 展示跳过 | ❌ | `sql_audit.py` 中 `skipped_objects`/`skipped_abnormal` 命中 **0**（现有 14 处 `skipped` 全是 `skipped_by_scope`/`skipped_ids`/`skipped_rules_count`，无关功能） |

也就是说：**事后翻历史报告的人，依然看不到当时跳过了多少、跳过了什么。**
运行中任务面板显示跳过数（`frontend/index.html:393`），但那是易失的，单槽位下一个任务就覆盖。

### 1.7 N-01 / N-02（低）—— **通过**

`-- Object Name:` 已走 `sanitize_comment`，连正常路径的 `-- Table:` 也一并加了（超出我提的范围，是好事）。
含 CR/LF 的对象名实测全部仍留在 `--` 注释内，无逃逸。`job_id` 已纳入压缩日志。

---

## 2. 本轮新发现

### 【R2-M-01｜MAJOR】阈值自适应的回归锁还是假绿 —— B-03 同类复发

变异 N3：把 `publish()` 里的自适应阈值改回写死的 `MAX_DB_PAYLOAD_THRESHOLD`
（**即 B-01 缺陷本体**）——

```
N3  压缩阈值还原写死 32MiB（B-01 本体）        15 passed   ← 存活
```

原因和第一轮那条一模一样，看用例就明白（`tests/test_v1636_bugs.py`）：

```python
def test_adaptive_threshold_no_compact_at_64mb():
    adaptive = int((max_pkt - R._PACKET_MARGIN) / R._ESCAPE_FACTOR)   # ← 公式在用例里自己又算了一遍
    out = R.compact_results_for_audit_history(js, "j", threshold_bytes=adaptive)  # ← 把算好的值喂进去
    assert out == js
```

它验的是「给定阈值，压缩函数会不会误压」，**没有验 `publish()` 自己算不算得对阈值**。
`publish()` 里那行改成什么，它都不知道。

第一轮 B-03 我指出的就是这个模式，Q 在预检那条上改对了，却在新加的这条上又犯了一次。

**建议**：这条用例改为真调 `publish()`，断言 42MiB 载荷落库后 `results_json` 条数 == 原始条数。

### 【R2-M-02｜MAJOR】B-02 残留：Rev.J 第 3 条未实现，注释里的"兜底"够不着

见 1.2。**后果没变**：分布式实例上，一张真业务表若恰好叫 `<某存在的表>_tdsql_subp<数字>`，
它的 DDL 永远不进审核，而任务报 SUCCEEDED —— 漏审且不可见。

**两条路，我推荐第二条**：

1. 补齐 Rev.J 第 3 条：pipeline 对目标库发一次 `/*proxy*/show table` 三连
   （`table_type_stats_service.py:108-110` 已有现成常量），拿到逻辑表集合，
   「候选自身在 Proxy 结果里 → 不是物理子表」。成本：每个任务多 3 条查询。
2. **干脆取消前置过滤，全靠 try/except**。理由：物理子表按定义就是 Proxy `SHOW CREATE`
   读不到的，失败即跳过，**这个判据零误杀、自带分辨力**，也正是 v1.6.3.4 能跑通的原因。
   前置过滤的唯一价值是省掉那些注定失败的查询，属于性能优化，不该承担正确性职责。
   代价是 ECIF 这类库多发约 2800 次注定失败的 `SHOW CREATE`——这个开销我在沙箱里
   测不到真实 TDSQL Proxy 的报错耗时，**需要内网实测一次再定**，这是我给不出结论的地方。

### 【R2-M-03｜MAJOR】`instance_type` 缺省 "distributed"，安全方向与 Rev.G 相反

```python
def is_tdsql_internal_table(table_name, instance_type="distributed", available_tables=None)
def extract_metadata(pool, target_db, scopes, instance_label="", instance_type="distributed")
# worker: instance_type=instance_type or "distributed"
```

三处缺省都倒向"按分布式过滤"。两个后果：

- **实例类型探测失败/ 为空时**（`ctx["instance_type"]` 取不到），按分布式过滤。
  而 Rev.G 定的安全方向是**拿不准就不剔除**——宁可 RECON_MISMATCH 显式报出来，也不静默少算。
- `is_tdsql_internal_table('orders_tdsql_subp202601')` 只传表名时返回 **True**，
  等价于第一轮那个无条件过滤器。现在生产调用点两个参数都传了，但缺省值把"忘传"变成了静默的错。

**建议**：缺省改为不过滤（`instance_type=""` 或直接把两个参数设为必传），
worker 那句 `or "distributed"` 去掉——探测不出类型就不该按分布式处理。

### 【R2-M-04｜MAJOR】新用例与元数据库 `max_allowed_packet` 耦合，包限小于约 52MiB 时全套变红

`test_publish_precheck_no_false_double` 改成真调 `publish()` 是对的（这是我第一轮要的），
但它把"包限一定是 64MiB"写死进了假设。我把沙箱元数据库包限调成 16MiB（MySQL 老默认 4MiB、
行内加固常见 16MiB）后重跑：

```
FAILED tests/test_v1636_bugs.py::test_publish_precheck_no_false_double
1 failed, 14 passed
```

这会让门禁在**环境原因**上变红，而不是产品缺陷，排查成本很高。

**建议**：用例先读 `@@session.max_allowed_packet`，按它反算载荷大小；
包限不足以构造区分带时 `pytest.skip` 并说明原因。

### 【R2-M-05｜MAJOR】M-03 的持久可见性仍缺（同 1.6）

分类做了、artifact 报告口径行做了，但 `audit_history` 无列、历史报告 HTML 无展示、
异常超阈值仅落日志且任务仍 SUCCEEDED。

**建议分两步**：本版先做「历史报告 HTML 顶部告警 + 异常跳过超阈值判 PARTIAL_SUCCESS」
（不动表结构，成本低）；`audit_history` 增列涉及迁移，可以下版做。

### 【R2-N-01｜MINOR】6 个新锁缺失或无效

第二轮变异复验（12 个变异体）：

| 变异 | 内容 | 结果 |
|---|---|---|
| N0 | 基线不变异 | 15 passed |
| N1 | 预检还原 `*2`（BUG-02 本体） | **1 failed ✅ 第一轮假绿已堵** |
| N2 | 预检系数 1.25 → 1.0（M-01 本体） | 15 passed ❌ 存活 |
| **N3** | **压缩阈值还原写死 32MiB（B-01 本体）** | **15 passed ❌ 存活** |
| N4 | 压缩回填列索引 8 → 7（写错列） | 15 passed ❌ 存活 |
| N5 | 去掉 `skipped_list[:50]`（M-02 本体） | 1 failed ✅ |
| N6 | 正则 `\d+` 放松回 `\d*` | 1 failed ✅ |
| N7 | 去掉集中式门禁（条件 1） | 1 failed ✅ |
| N8 | 去掉父表存在性门禁（条件 3） | 1 failed ✅ |
| N9 | 去掉 rollback 的 try/except（M-01b 本体） | 15 passed ❌ 存活 |
| N10 | 去掉 Object Name 的 sanitize（N-01 本体） | 15 passed ❌ 存活 |
| N11 | 去掉良性/异常跳过分类（M-03 本体） | 1 failed ✅ |
| N12 | report.html 口径行去掉跳过数（M-03 本体） | 15 passed ❌ 存活 |

**杀死 6 / 存活 6。** N3 见 R2-M-01（单列）。

**N4 值得单说**：commit message 写了「补 M8 列索引」，但这条锁**打不响**——
`test_publish_precheck_no_false_double` 用的 33MB 载荷低于 51.1MiB 阈值，压缩根本不触发，
那段 `compacted if i == 8 else v` 是死代码，索引改成几都无所谓。列位校验必须在
**触发压缩**的路径上做才有意义。

N2/N9/N10/N12 是纯缺锁：产品行为我逐条实测是对的（见第 1 节），但没有用例钉住，
下次有人动到就没人报警。

### 【R2-N-02｜MINOR】超过自适应阈值时，压缩仍是静默节选

我第一轮给的是二选一：**首选**自适应阈值，**若坚持保留压缩兜底**则必须加显式省略提示 +
`omitted_count` 列 + 下载口优先读 ndjson。Q 做了首选那条，第二条分支没做。实测：

```
raw=69,693,611 (66.5MiB) > 阈值 51.1MiB → 触发压缩
   落库 total_sql列=12000  results_json条数=350  → 省略 11650 条 (97.1%)
   results_json 内含省略标记/哨兵记录: 无
   audit_history 有无 omitted 计数列:   无
```

内网 42MiB 那个库已经不会触发了，所以这条我降为 MINOR；但阈值之上仍是无声丢数据，
建议补一条哨兵记录或在两个下载口加一行提示。

---

## 3. 回归验证（受控前后对比）

沙箱 402 failed / 82 errors 是既有环境噪声（`AUTH_ENABLED` 跨文件泄漏、MariaDB `int(11)` 差异），
历轮都在，**只比对失败集合，不比总数**。

基线取 **`efe0972`**（`be9374d` 的直接父提交）——第一轮我误用过更早的提交，这次先核对了提交顺序。
两边各起独立 git worktree、各用独立元数据库（`r2_b2` / `r2_h`）跑全量，
取 `FAILED`/`ERROR` 节点 ID 排序后逐条 `comm` 比对：

```
base(efe0972): 484 条   head(877f46b): 484 条
=== Q 修复引入的新失败（真回归）===   （空）
=== Q 修复消除的失败 ===             （空）
通过数: 1582 → 1585 (+3 = 新增 3 个用例)
```

**零新增回归。** `tests/test_v1636_bugs.py` 单跑 15 passed。

---

## 4. 阴性结论（查过、没问题的）

| 检查项 | 结论 |
|---|---|
| 新注解 `Optional[int]` 是否漏 import（v1.6.3.5 B-01 同类） | **已导入**（`metadata_audit_repository.py:22`） |
| 三个改动模块 + `backend.main:app` 能否构建 | **能**（38 条路由） |
| `report.html` 遇到旧版 stats（无 `skipped_*`）是否崩 | **不崩**，`.get(...,0)` 优雅回退 |
| `[SKIPPED]` 注释块是否污染审核 | **不污染**，加/不加拆句数 1=1、违规数 4=4 |
| 真实产物 219 个对象名是否被新判据误杀 | **0 个，0.00%** |
| 自适应阈值与预检是否自洽（压了还被拦） | **自洽**，互为逆运算 |
| 压缩不触发时列位回填是否受影响 | **不受影响**（`compacted == results_json` 时不重写元组） |

---

## 5. 整改清单

| 编号 | 级别 | 事项 | 建议做法 | 何时 |
|---|---|---|---|---|
| R2-M-01 | MAJOR | 阈值锁假绿（N3 存活） | 用例真调 `publish()`，断言 42MiB 无损 | **准出前必补** |
| R2-M-03 | MAJOR | `instance_type` 缺省倒向过滤 | 缺省改不过滤 / 设为必传；去掉 `or "distributed"` | **准出前必补** |
| R2-M-04 | MAJOR | 用例与包限耦合 | 读 `@@max_allowed_packet` 反算载荷；不足则 skip | **准出前必补** |
| R2-M-02 | MAJOR | B-02 残留（Rev.J 第 3 条） | 补 Proxy 逻辑表集合校验，或取消前置过滤全靠 try/except（需内网实测开销） | 请 Mr.Linsang 定 |
| R2-M-05 | MAJOR | M-03 持久可见性 | 本版：历史报告顶部告警 + 超阈值判 PARTIAL；下版：`audit_history` 增列 | 请 Mr.Linsang 定 |
| R2-N-01 | MINOR | N2/N4/N9/N10/N12 缺锁 | 补 5 条；N4 必须在触发压缩的路径上验列位 | 准出前 |
| R2-N-02 | MINOR | 超阈值仍静默节选 | 加哨兵记录或下载口提示 | 可下版 |

---

## 6. 放行意见

**可以进 UAT。**

第一轮那两个真正要命的问题——大库报告静默丢 96.7% 明细、真业务表被误杀且报 SUCCEEDED——
前者已彻底解决并 E2E 可证，后者从"无条件误杀"收窄成"分布式实例上的特定命名碰撞"，
集中式实例已完全安全。回归零新增失败。**没有 BLOCK 级问题拦着 UAT。**

但有两件事要说清楚：

1. **R2-M-01 / R2-M-03 / R2-M-04 必须在准出门禁前补齐**，都是几行的改动。
   它们不影响 UAT 跑通（UAT 期间没人改代码），但带着假绿的锁和倒向不安全的缺省值签准出，
   我签不了。
2. **R2-M-02 和 R2-M-05 需要您定**。R2-M-02 我倾向取消前置过滤、全靠 try/except 兜底
   （零误杀、自带分辨力），但那要先在内网量一次「约 2800 次注定失败的 SHOW CREATE」
   到底多久——这个数我在沙箱里造不出来，不能拍脑袋。R2-M-05 建议拆两步走，
   本版只做不动表结构的那半。

O 做 UAT 时，建议重点压这两处：分布式大库的跳过计数与历史报告的一致性、
元数据库包限不是 64MiB 时的表现。

---

*报告人：智能体A（-ClaudeA）　送呈：Mr.Linsang*
