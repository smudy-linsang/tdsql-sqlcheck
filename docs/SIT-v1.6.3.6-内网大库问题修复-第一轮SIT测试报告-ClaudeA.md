# v1.6.3.6 内网大库问题修复 · 第一轮独立 SIT 测试报告

| 项 | 内容 |
|---|---|
| 版本 | v1.6.3.6（受测提交 `4617297`，文档提交 `6a7b896`） |
| 基线 | `059cb80`（v1.6.3.6 的直接父提交，即 v1.6.3.5 交付态） |
| 测试类型 | 第一轮独立 SIT（缺陷修复验证 + **次生灾害专项排查**） |
| 测试人 | 智能体 A（独立评审/测试） |
| 报告日期 | 2026-09-11 |
| 送呈 | Mr.Linsang |

---

## 0. 结论摘要

**两个缺陷的主功能均已修好，实测可证。但本轮发现 8 项次生问题，其中 3 项为高优先级，
建议本版本不放行进入 UAT，由 Q 整改后我做第二轮 SIT。**

| 判定项 | 结论 |
|---|---|
| BUG-01（ECIF 分布式库 0 秒崩溃） | **修复有效** |
| BUG-02（6097 表结果被预检误拦） | **修复有效** |
| 是否发生次生灾害 | **是** —— A-01 / A-02 / A-03 三项高优先级 |
| 全量回归是否引入新失败 | **否**（失败集合与基线逐条一致） |
| 放行建议 | **不放行**，整改后复测 |

最要紧的一条：**为修 BUG-02 而引入的 32 MiB 压缩，会让内网那个 6097 表库的历史报告
静默丢掉 96.7% 的建表语句；而实测证明，对这个体量压缩根本不需要触发。**

---

## 1. 独立的根因判断（先不看 G 的方案，自己查）

### 1.1 BUG-01：ECIF 库 0 秒失败

内网报错：

```
对象 lzbj_ecif.cus_bas_merge_log_tdsql_subp190001（TABLE）DDL 读取失败:
(660, "Proxy ERROR: Table:'lzbj_ecif.cus_bas_merge_log_tdsql_subp190001' does not exist")
```

**根因：v1.6.3.5 把一个"历来被容忍的条件"改成了"致命条件"，而不是新引入了错误。**

- TDSQL 二级分区/分片的底层物理子表（`xxx_tdsql_subp190001`）会被
  `information_schema.TABLES` 枚举出来，但 Proxy 不允许对单个物理分片
  `SHOW CREATE TABLE` —— 这是 TDSQL 的既有行为，v1.6.3.4 时就存在。
- v1.6.3.4 的 `sql_audit.extract_and_audit` 对此是 `except Exception: logger.warning(...)`
  —— **静默跳过**，所以用户侧看起来"一直是好的"。
- v1.6.3.5 的 `metadata_audit_pipeline.extract_metadata` 按设计改成 fail-closed
  （单对象失败即终止全库），于是同一个条件从"无感"变成"0 秒硬失败"。

**佐证（Mr.Linsang 提供的 v1.6.3.4 内网真实产物）**：
`extracted_lzbj_ecif_20260910_224104.sql` 共 **219 个对象，`_tdsql_sub` 出现 0 次**，
而 `cus_bas_merge_log` 作为逻辑表正常出现。这直接证明 v1.6.3.4 当时就跳过了这些子表，
只是没说。

### 1.2 BUG-02：6097 表库审核中途失败

内网报错：`审核结果编码后约 88381164 字节，超过元数据库 max_allowed_packet (67108864)`

**根因：自伤式预检。** `metadata_audit_repository.py:331`（v1.6.3.5）：

```python
payload = len(results_json.encode("utf-8")) * 2 + _PACKET_MARGIN
```

由报错值反推真实体量：`(88381164 - 65536) / 2 = 44,157,814 字节 = 42.1 MiB`。

我实测 PyMySQL 的转义膨胀（`pymysql.converters.escape_string`，元数据审核结果的真实形态）：

| 语料 | raw | 转义后 | 膨胀 |
|---|---|---|---|
| 全通过（4000 条 DDL） | 1,171,002 | 1,287,002 | **1.099×** |
| 10% 违规 | 1,242,323 | 1,365,523 | **1.099×** |
| 引号密集极端构造 | 594,000 | 717,000 | **1.207×** |

即：真实上线约 42.1 MiB × 1.10 = **46.4 MiB < 64 MiB**，本来能落库。
`*2` 这个系数没有依据，是它自己把自己拦死的。**G 的判断在这一点上正确。**

---

## 2. 修复有效性验证

### 2.1 BUG-01 —— 通过

| 用例 | 方法 | 结果 |
|---|---|---|
| B1-1 内网故障对象被正确跳过 | `is_tdsql_internal_table("cus_bas_merge_log_tdsql_subp190001")` | `True` ✅ |
| B1-2 单对象 SHOW CREATE 失败不杀全库 | 3 表 1 失败，跑 `extract_metadata` | extracted=2 / skipped=1，不抛异常 ✅ |
| B1-3 全部失败仍须硬失败 | 1 表全失败 | 抛 `NO_AUDITABLE_OBJECTS` ✅ |
| B1-4 **真实产物全量回放（误杀实测）** | 把 `extracted_lzbj_ecif_...sql` 里解析出的 **219 个真实对象名**逐一喂给新过滤器 | **命中 0 个，误杀率 0.00%** ✅ |
| B1-5 `[SKIPPED]` 注释块是否污染审核 | 同一 DDL 加/不加跳过块，跑 `audit_streaming` 对比 | 拆句数 1=1、违规数 4=4，**无污染** ✅ |

B1-4 是这轮最有说服力的一条：过滤器在内网那个库的真实表名上没有任何误伤。

### 2.2 BUG-02 —— 通过

真实 MySQL 协议库（`max_allowed_packet=67108864`）上跑通 `publish()` 全路径：

| 用例 | raw 体量 | v1.6.3.5 行为 | v1.6.3.6 实测 |
|---|---|---|---|
| B2-1 内网同量级 | 35,561,199 B（33.9 MiB） | 预检算成 71.2 MiB → 拦 | **落库成功** ✅ |
| B2-2 阈值下方 | 21,672,233 B（20.7 MiB） | 拦 | **落库成功且无损 6097 条** ✅ |

---

## 3. 次生灾害排查（本轮重点）

### 【A-01｜高】大库历史报告静默丢失 96.7% 明细，而压缩本非必需

**事实链：**

1. 内网那个库真实 `results_json` = 42.1 MiB（由报错反推），
   而 `MAX_DB_PAYLOAD_THRESHOLD = 32 MiB` → **会触发压缩**。
2. 真实库 E2E 实测（B2-1 同一次 publish）：

```
raw=35,561,199B (33.9MiB) → 落库成功 id=1
   audit_history.total_sql 列 = 6097   pass_rate 列 = 97.5
   results_json 落库长度 = 1,200,151
   results_json 实际条数 = 203 / 原始 6097  → 静默丢失 5894 条 (96.7%)
```

3. **受影响的正是 Mr.Linsang 实际在用的两个产物**（您这次的附件就是这两个口）：

| 入口 | 代码位置 | 压缩后表现 |
|---|---|---|
| 历史报告「下载元数据 SQL」 | `frontend/static/js/app.js:1968` → `backend/api/sql_audit.py:531-538` | 只输出 **203 条** CREATE TABLE（应 6097），**文件内无任何省略标记** |
| 历史报告「下载 HTML」 | `app.js:1963` → `sql_audit.py:448` | 表头 `total_sql=6097`、明细表 203 行，**页面无"已省略 N 条"说明** |
| 存量快照回填 | `scan_snapshot_service.py:354-371` `_rebuild_schema_audit` | 从 `results_json` 重建，对比快照对象数塌陷 |

（说明：运行中任务面板走 `/metadata-jobs/{job_id}/results`，读的是本地
`artifacts/results.ndjson`，**不受影响**；受影响的是长期留存的"历史报告"侧。）

4. **决定性的一条：压缩在这个体量下根本不必要。** 我把阈值临时抬到 64 MiB
   （仅测试进程内改，不动仓库代码），同一份 33.9 MiB 数据：

```
raw=35,561,199B (33.9MiB) → 不压缩也成功落库，results_json 长度=35,561,199，无损 6097 条
```

也就是说：**只要去掉 `*2`，BUG-02 就修好了；32 MiB 的压缩阈值是额外加上去的，
代价是把内网那个库的报告打掉 96.7%。**

**修改意见（择一）**

- 首选：阈值不要写死 32 MiB，改为按实际包限自适应 ——
  `threshold = max_allowed_packet / 转义系数 - _PACKET_MARGIN`（转义系数取实测 1.25 留余量），
  64 MiB 包限下阈值≈50.6 MiB，内网该库不触发压缩，完全无损；
- 若坚持保留压缩兜底，则必须同时做到三条：
  1. 压缩后在 `results_json` 末尾追加一条哨兵记录（或在 HTML/SQL 两个下载口注入显式提示），
     让用户看得见"这份是节选，完整明细在 artifacts"；
  2. `audit_history` 增列记录 `omitted_count`，不能让表头 6097 / 明细 203 这种矛盾无解释地存在；
  3. 两个下载口改为优先读 `artifacts/results.ndjson`，读不到再退回 `results_json`。

---

### 【A-02｜高】子表识别只用了命名匹配，违反本项目自己的既定规则 Rev.G / P1-03

`metadata_audit_pipeline.py:27-34`：

```python
_TDSQL_INTERNAL_PARTITION_PATTERN = re.compile(r".*_tdsql_(subp|shard)\d*$", re.IGNORECASE)
```

**无条件生效**：不看实例类型、不看父表是否存在、不看候选自身是否在 Proxy 结果里。

而本项目在 `backend/services/table_type_stats_service.py:176-186` 早就白纸黑字定过规矩：

> Rev.G（P1-03）：命名匹配【只是必要条件，不是充分条件】。
> · 集中式实例根本没有二级分区物理子表这一构造 —— 一律不剔除，
>   否则一张合法业务表 **orders_tdsql_subp202601** 会被静默少算，且集中式
>   没有 Proxy 交叉校验兜底，错误不可见（违反 REQ-5）。
> · 分布式实例额外要求【逻辑父表确实出现在本库的 Proxy 结果中】才判定为子表。

并且 `_classify_subpartitions()`（同文件 432-458 行）已经有一份**经过测试的三条件实现**
（Rev.J / P2-01 还补了第 3 条"候选自身不在 Proxy 结果里"）。

**实测（新过滤器 vs 既定规则）：**

| 表名 | 新过滤器 | 既定规则应为 | 说明 |
|---|---|---|---|
| `cus_bas_merge_log_tdsql_subp190001` | True | True | 内网故障对象，正确 |
| `t_order_tdsql_shard12` | True | True | 正确 |
| **`orders_tdsql_subp202601`** | **True** | **False** | **Rev.G 原文点名的合法业务表反例** |
| **`foo_tdsql_shard`** | **True** | **False** | `\d*` 允许零位数字 |
| **`biz_tdsql_subp`** | **True** | **False** | 同上 |

**后果比统计模块更重**：统计模块误判只是页面少算一张表；这里误判意味着
**一张真业务表的 DDL 从此不进审核，而任务照报 SUCCEEDED** —— 规则漏审且不可见。

两点补充：

- `\d*` 的零位数字是笔误级问题：`tests/test_v1636_bugs.py:22` 的注释写的是
  "不以 `_tdsql_subp/shard`+**数字** 结尾"，说明作者本意就是 `\d+`，代码写成了 `\d*`。
  变异 M10（`\d*`→`\d+`）**12/12 全绿存活**，说明这一位没有任何用例钉住。
- 好消息是收紧成本很低：`instance_type` 在 `metadata_audit_worker.py:140` 就已取到，
  早于第 153 行调用 `extract_metadata`，加一个形参即可；"父表在本次枚举结果中"
  更是零成本（枚举清单本来就在手里）。

**修改意见**：把 `table_type_stats_service._classify_subpartitions` 的三条件判据移植过来
（集中式一律不过滤；分布式要求父表存在、且候选自身不在 Proxy 结果中），
并把 `\d*` 收紧为 `\d+`。不要在 pipeline 里另起一套更松的判据。

---

### 【A-03｜高】BUG-02 的回归锁是假绿：把缺陷原样改回去，12 个用例仍然全过

变异测试（对 `tests/test_v1636_bugs.py`）：

| 变异 | 内容 | 结果 |
|---|---|---|
| M0 | 基线不变异 | 12 passed |
| **M1** | **预检还原成 `*2` 虚假翻倍（= BUG-02 本体）** | **12 passed ——存活 ❌** |
| M2 | 完整性判据还原 `extracted != selected` | 2 failed ✅ |
| M3 | 取消 TDSQL 子表前置过滤 | 1 failed ✅ |
| M4 | 取消单对象 try/except 容错 | 2 failed ✅ |
| M5 | 压缩阈值抬到 1 TiB（等于关压缩） | 1 failed ✅ |
| M6 | 压缩结果改回 dict 格式（破坏向后兼容） | 1 failed ✅ |
| M7 | 压缩时丢弃违规条目 | 1 failed ✅ |
| **M8** | **压缩回填列索引 8 改成 7（写错列）** | **12 passed ——存活 ❌** |
| **M9** | **去掉 worker 的 `skipped_list[:50]` 限长** | **12 passed ——存活 ❌** |
| **M10** | **正则 `\d*` 收紧为 `\d+`** | **12 passed ——存活 ❌** |

M1 存活的原因，看用例本身就清楚（`tests/test_v1636_bugs.py:150-160`）：

```python
def test_publish_precheck_no_false_double():
    raw_mb = 40 * 1024 * 1024
    payload = raw_mb + R._PACKET_MARGIN     # ← 公式在用例里自己又算了一遍
    max_pkt = 64 * 1024 * 1024
    assert payload < max_pkt
```

它**从头到尾没有调用 `publish()`**，只引用了 `_PACKET_MARGIN` 这个常量，
断言的是测试自己写的算式。`publish()` 里改成什么样它都不知道。
这跟 v1.6.3.5 第三轮 UAT 抓到的 D-02 假绿是同一类问题。

M8 存活另需留意：索引 8 目前**是对的**（我核过 `metadata_audit_worker.py:209-220`
的 21 列顺序，第 9 个确实是 `results_json`），但没有任何用例钉住它；
一旦有人调整列顺序，压缩后的 JSON 会被写进 `pass_rate` 列，而测试全绿。

**修改意见**：`test_publish_precheck_no_false_double` 改为真调 `publish()`
（用可控的 `@@session.max_allowed_packet` 打桩或真实库），断言"40 MiB 放行 / 超限拦截 + 错误码"；
补 M8/M9/M10 三条锁。

---

### 【A-04｜中】去掉 `*2` 后余量归零，包限被调小时预检失守，且错误归因被误导

预检现在用 **raw 字节**，但真正上线的是**转义后**的字节（实测 1.076×–1.207×），
余量只有固定 64 KiB。压缩阈值 32 MiB 是按"包限恒为 64 MiB"这个隐含假设定的。

我把测试实例的 `max_allowed_packet` 调到 16 MiB 复现（沙箱自用实例，测完已还原）：

```
载荷 raw=15,747,339   预检值(raw+64KiB)=15,812,875  vs 16,777,216 → 预检放行
转义后真实上线 16,939,263 = 1.076x → 超包 162,047 字节
```

后续链路比"报个错"更糟：

1. DB 抛 `(1153, "Got a packet bigger than 'max_allowed_packet' bytes")`，连接被重置；
2. `publish()` 的 `except Exception as e:` **第一句就是 `conn.rollback()`**
   （`metadata_audit_repository.py:438-441`），这句在死连接上又抛
   `(2006, "MySQL server has gone away")`；
3. 于是 `raise MetadataJobError("REPORT_SAVE_FAILED", ...)` **根本执行不到**，
   2006 裸穿出 `publish()`；
4. worker 兜底映射成 `WORKER_ERROR` +「**目标数据库**连接被中断（server has gone away，
   可能超时或实例重启）」——**把元数据库的包限问题说成目标库连接问题**，运维会查错系统。

v1.6.3.5 因为 `*2` 拦得太狠，这条路径够不着；v1.6.3.6 去掉了过度拦截却没补真实余量，
把它暴露了出来。暴露窗口：`max_allowed_packet < ~35 MiB`（MySQL 老默认 4 MiB、
不少行内加固配置 16 MiB，都在窗口内）。**内网当前 64 MiB 不触发，但不该靠运气。**

**修改意见**：
1. 预检系数用实测值而不是 1.0 或 2.0 —— 取 1.25 留安全边际，或直接
   `len(escape_string(results_json))` 精确计算（一次 O(n)，相对 30 MiB 的
   json.dumps 可忽略）；
2. `except Exception` 里的 `conn.rollback()` 用 try/except 包住，保证
   `MetadataJobError` 一定抛得出来。这是 publish 错误处理的结构性缺口，
   顺手补掉。

---

### 【A-05｜中】`skipped_list` 限长锁只加在最后一个写点，对峰值无效；注释声称的限制并不存在

`metadata_audit_worker.py:186` 的注释写着「progress_json 限 128KiB」。
我查过：`progress_json` 是 `MEDIUMTEXT`（`backend/schema/v15/150_metadata_audit_jobs.sql:29`），
`update_progress()`（`metadata_audit_repository.py:318-329`）是直白 UPDATE，
**代码里没有任何 128 KiB 限制**。

真正的问题是限长加错了地方 —— 四个写点里只有一个限了：

| 写点 | 代码 | 是否限长 | 次数 |
|---|---|---|---|
| EXTRACTING 收尾 | `worker.py:158-159` `json.dumps(stats)` | ❌ | 1 |
| AUDITING 每 50 句 | `worker.py:175-178` `json.dumps({..., **stats})` | ❌ | ⌈语句数/50⌉ |
| SERIALIZING 收尾 | `worker.py:186-192` `skipped_list[:50]` | ✅ | 1 |
| manifest 落盘 | `worker.py:255-258` `counts={**stats, ...}` | ❌ | 1 |

**峰值在限长之前就已经写进去了，这个锁等于没加。** 量化：

| 跳过子表数 | 单次 progress_json | vs 注释声称的 128 KiB |
|---|---|---|
| 1,000 | 140,123 B | 超 1.1× |
| 2,800 | 392,123 B | 超 3.0× |
| 5,800 | 812,123 B | 超 6.2× |
| 20,000 | 2,800,126 B | 超 21.4× |

以"跳过 5,800 张子表 / 拆句 1,200 条"算，AUDITING 阶段要把那个 812 KB 的 blob
**重复写 25 次**，加上另两处共约 **20.9 MiB 的无谓 UPDATE 流量** —— 而这恰恰发生在
本版本要救的大库路径上。变异 M9 存活，说明没有用例钉住。

**修改意见**：在 `extract_metadata` 返回处就把 `skipped_list` 截断（或分成
`skipped_objects` 计数 + `skipped_sample`），让所有下游写点天然安全；
删掉那句与代码不符的 128 KiB 注释。

---

### 【A-06｜中】完整性判据放宽后的 fail-open，且"跳过"在历史侧完全不可见

判据从 `extracted != selected` 放宽成 `extracted == 0`。方向我认可（不能因一张
Proxy 不可读的子表就杀全库），但现在是**从一个极端跳到另一个极端**。

实测（1000 张表中 999 张 `SHOW CREATE` 失败，例如 SELECT 权限被回收 / Proxy 抖动）：

```
v1.6.3.6：不抛异常，extracted=1, skipped=999 → 任务走到 PUBLISHED/SUCCEEDED
v1.6.3.5：抛 EXTRACT_INCOMPLETE → 任务 FAILED
★ 落一份只含 1 张表的"全库审核报告"，pass_rate 按这 1 张表算
```

配合可见性缺口，问题才真正成立：

| 呈现面 | 是否显示跳过数 |
|---|---|
| 运行中任务面板 | ✅ 显示（`frontend/index.html:393` 橙色"/ 跳过 N"） |
| `audit_history` 21 列 | ❌ **无 skipped 列** |
| 历史报告 HTML / SQL 下载 | ❌ 完全看不到 |
| `artifacts/report.html` 口径行 | ❌ 只有"枚举/选中/提取"，没有"跳过" |

运行中面板是**易失的**（单槽位，下一个任务就覆盖）。也就是说，事后翻这份报告的人，
无从知道当时跳过了 999 张表。

还有一点：现在两类跳过被**混为一谈** —— "TDSQL 物理子表"（预期内、良性）和
"权限/超时/Proxy 故障"（异常、需要人管）都只记成 skipped，没有分类。

**修改意见**：
1. `skipped_objects` 按原因分两类计数，非良性跳过超过阈值（比如 >5% 或绝对值 >50）
   时任务判 `PARTIAL_SUCCESS` 或至少在报告顶部打醒目告警；
2. `audit_history` 增列持久化跳过计数，历史报告 HTML 顶部显示；
3. `artifacts/report.html` 的"扫描口径"行补上"跳过 N"。

---

### 【A-07｜低】新代码里相邻两行，一行做了注释注入防护，一行没做

`metadata_audit_pipeline.py:178-179`：

```python
lines.append(f"-- Object Name: {obj_name}")                    # ← 未 sanitize
lines.append(f"-- Skip Reason: {sanitize_comment(str(e))}")    # ← 已 sanitize
```

MySQL 反引号标识符允许含换行，`information_schema` 会原样返回。含 CR/LF 的对象名
会从 `--` 注释里跑出来。同文件 140-141 行对 `instance_label` / `target_db` 都做了
`sanitize_comment`，这里漏了一处。危害有限（产物只被审核、不被执行），但属于
新代码内部不自洽，改一行的事。

### 【A-08｜低】`compact_results_for_audit_history(results_json, job_id)` 的 `job_id` 形参从未使用

日志里也没带上它。要么用起来（`logger.info` 带 job_id，大库排障时有用），要么删掉。

---

## 4. 回归验证（受控前后对比）

沙箱内 402 failed / 82 errors 属既有环境噪声（`AUTH_ENABLED` 跨文件泄漏、
MariaDB 与 MySQL 的 `int(11)` 差异等），历轮都在。因此**不看总数，只比对失败集合**。

方法：基线 `059cb80` 与受测 `6a7b896` 各起独立 git worktree、**各用独立元数据库**
（`v1636_b2` / `v1636_a`）跑全量，取 `FAILED`/`ERROR` 节点 ID 排序后 `comm` 逐条比对。

```
base(059cb80): 484 条   head(6a7b896): 484 条
=== v1.6.3.6 新增失败（真回归）===   （空）
=== v1.6.3.6 消除的失败 ===         （空）
通过数: 1570 → 1582 (+12 = 新增 test_v1636_bugs.py 的全部用例)
```

**失败/错误集合逐条一致，无新增回归；新增 12 个通过用例恰为新用例文件。**

另：`tests/test_v1636_bugs.py` 单独跑 **12 passed**。

---

## 5. 明确"没有被破坏"的部分（次生灾害排查的阴性结论）

写明阴性结论，免得后面有人重复查：

| 检查项 | 结论 |
|---|---|
| `[SKIPPED]` 注释块污染审核 | **未发生**。加/不加跳过块，拆句数 1=1、违规数 4=4 |
| `EXTRACT_INCOMPLETE` 错误码删除后留悬挂引用 | **无**。全仓 `.py/.js/.html/.md` 零残留 |
| `sanitize_comment` 在 pipeline 内未定义（NameError） | **不成立**。同文件 54 行有定义 |
| 压缩把 `audit_columns_values` 写错列 | **未发生**。索引 8 与 21 列顺序核对一致（但无用例锁，见 A-03/M8） |
| 运行中任务的分页读结果被压缩影响 | **未受影响**。走 `artifacts/results.ndjson`，非 `results_json` |
| 实时对比快照被压缩影响 | **未受影响**。worker 传的是自己的未压缩局部变量（`publish` 只重绑自己的形参） |
| 仪表盘高频违规规则统计被压缩影响 | **未受影响**。压缩保留全部违规条目 |
| 产物目录被自动清理导致 ndjson 丢失 | **当前不会**。`cleanup_job_dir` 全仓无调用方 |
| 全量回归引入新失败 | **无**（见第 4 节） |

---

## 6. 整改清单（按优先级）

| 编号 | 优先级 | 事项 | 建议做法 |
|---|---|---|---|
| A-01 | 高 | 大库历史报告静默丢 96.7% 明细 | 阈值改为按 `max_allowed_packet` 自适应；若留压缩则必须加显式省略提示 + `omitted_count` 列 + 下载口优先读 ndjson |
| A-02 | 高 | 子表识别违反 Rev.G/P1-03 | 移植 `_classify_subpartitions` 三条件判据；`\d*` → `\d+` |
| A-03 | 高 | BUG-02 回归锁假绿 | 用例真调 `publish()`；补 M8/M9/M10 三条锁 |
| A-04 | 中 | 预检余量归零 + 错误归因被误导 | 系数取实测 1.25 或精确算转义长度；`rollback()` 加 try/except |
| A-05 | 中 | 限长锁加错位置 | 在 `extract_metadata` 返回处截断；删除与代码不符的 128 KiB 注释 |
| A-06 | 中 | fail-open + 跳过在历史侧不可见 | 跳过按原因分类；超阈值判 PARTIAL_SUCCESS；`audit_history` 增列；报告口径行补"跳过 N" |
| A-07 | 低 | `-- Object Name:` 未 sanitize | 加 `sanitize_comment` |
| A-08 | 低 | `job_id` 形参未使用 | 用起来或删掉 |

---

## 7. 放行意见

**不建议放行进入 UAT。**

BUG-01、BUG-02 的主功能确实修好了，这点我实测确认。但 A-01 会让内网那个 6097 表库的
报告实际可用性严重下降（而且是在"修好了"的外表下丢数据），A-02 违反了本项目自己定过并
已有实现的规则、可能导致真业务表漏审，A-03 意味着 BUG-02 这个缺陷可以被原样改回去而
测试全绿 —— 这三条都够不上进 UAT 的门槛。

建议 Q 按第 6 节整改，我做第二轮 SIT。A-01 与 A-02 我会做定点复验 + 变异复验，
A-03 的三条新锁我会逐条做变异有效性验证。

---

*报告人：智能体 A（-ClaudeA）　送呈：Mr.Linsang*
