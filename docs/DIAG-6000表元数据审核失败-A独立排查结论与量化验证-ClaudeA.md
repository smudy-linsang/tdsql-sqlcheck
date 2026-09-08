# DIAG-6000 表元数据审核 `Failed to fetch` 独立排查结论（A）

| 项 | 内容 |
|---|---|
| 报告性质 | 智能体 A 的**独立排查结论**，供其他智能体交叉比对 |
| 故障对象 | 总账集中式开发库（6000+ 表），入口「SQL审核 > 在线元数据审核 > 拉取元数据并执行文件审核」 |
| 现象 | 等待约 250 秒后前端弹「提取失败: Failed to fetch」 |
| 版本区间 | v1.6.3.0 正常；v1.6.3.2 起失败；v1.6.3.4 仍失败 |
| 排查基线 | `main` / `c15d055` |
| 排查方式 | **代码定位 ＋ 实测量化**：本机合成 200/400/800/1600 表负载，测内存与耗时增长曲线并外推 6000 表；另实测「关闭 R035」与「流式修复原型」两个对照组 |
| **结论** | **根因与 G、Q 一致：v1.6.3.2（REQ-05A）把 `audit_file` 从流式改为全量预解析，并在 `_build_r035_cross_table_context` 引入逐语句全量快照，形成超线性内存增长，6000 表时击穿 worker。** 我用实测把它从"推断"变成了"带数字的结论"，并**修正了 G/Q 对耗时构成的归因**，另附**已验证可行的修复原型**。 |

---

## 1. 结论与三份既有材料的关系

| 材料 | 主结论 | 我的判定 |
|---|---|---|
| 内网智能体 `tdsql_metadata_extract_failure_analysis.md` | Nginx 120s 超时 ＋ v1.6.3.4 新增 `capture_report_context` 耗时 | **不成立**。本环境直连 8000 无 Nginx；`capture_report_context` 全程只调一次。该报告自己也写了"DDL 拉取逻辑 v1.6.3.0 与 v1.6.3.4 相同"，却仍归因于新增点，前后矛盾 |
| G `REPORT-...根因深度排查与解决方案报告.md` | v1.6.3.2 REQ-05A 全量预解析 ＋ R035 O(N²) → worker 猝死 | **根因判断正确**，证据链（worker died / TCP RST / 版本差异）成立 |
| Q `REPORT-...Q独立排查结论.md` | 同上 | **根因判断正确**，代码定位精确到行 |

**我与 G、Q 在根因上完全一致，不重复论证。**本报告只补两件他们没做的事：**把量级测出来**，以及**纠正一处会误导修复方向的归因**。

---

## 2. 缺陷定位（精确到行）

`backend/engine/checker.py::audit_file`，v1.6.3.2 提交 `c0e5e25` 引入：

```python
# 第 300-303 行：整批语句全量物化，6000 个 ParsedSQL 同时常驻
parsed_items = [(sql_text, line_no, self.parser.parse(sql_text))
                for sql_text, line_no in stmts]
metas = self._build_r035_cross_table_context(parsed_items, rule_overrides, instance_type)
```

```python
# 第 342 行：真正的放大器——每条语句都把【当前累积的整个索引】完整拷贝一份，
#            且这 N 份快照全部存进 metas，直到整批审核结束才释放
metas[idx][self._R035_CROSS_KEY] = {k: list(v) for k, v in index.items()}
```

v1.6.3.0 的写法是**流式**的（同一提交的删除行可见）：

```python
for sql_text, line_no in sqls:
    result = self.audit_sql(...)      # 解析→审核→ParsedSQL 离开作用域即回收
    results.append(result)
```

**空间复杂度从 O(1)（常驻一个 AST）退化为 O(N) 的 AST 常驻 ＋ Σᵢ|indexᵢ| 的快照累积。**这与"v1.6.3.0 正常、v1.6.3.2 起失败"精确对应。

**触发条件**：① 单次提交的语句数 N 足够大；② R035 启用（`rules/ddl.py:553 enabled = True`，默认启用）。在线元数据审核把 6000 张表拼成**一个** .sql **一次性**提交（`sql_audit.py::extract_and_audit`），必然踩中。

---

## 3. 实测数据（本报告的主要增量）

本机合成负载：每表 20 列（6 个全库共有列 ＋ 14 个业务列），单次提交，`tracemalloc` 测 Python 峰值分配。

### 3.1 现状的增长曲线

| 表数 N | 峰值内存(MB) | 审核耗时(s) | 内存/上一档 |
|---|---|---|---|
| 200 | 25.6 | 4.14 | — |
| 400 | 56.7 | 8.28 | 2.21x |
| 800 | 138.5 | 18.40 | 2.44x |
| 1600 | 375.3 | 39.89 | 2.71x |

**倍率持续攀升（2.21→2.44→2.71，纯线性应恒为 2.0）**，即超线性且指数还在变大。

单独测 `_build_r035_cross_table_context` 一项（隔离出放大器）：

| 表数 N | 快照峰值内存(MB) | 内存/上一档 |
|---|---|---|
| 200 | 12.0 | — |
| 400 | 30.9 | 2.57x |
| 800 | 87.0 | 2.81x |
| 1600 | 272.4 | 3.13x |

**倍率 2.57→2.81→3.13，向 4.0（纯平方）逼近**——这就是 O(N²) 的实测形状。

### 3.2 外推 6000 表（log-log 最小二乘拟合）

| 指标 | 实测幂次 | 6000 表外推 |
|---|---|---|
| 现状 `audit_file` 峰值内存 | **N^1.29** | **1.92 GB** |
| ├ 其中 R035 快照一项 | **N^1.50** | **1.84 GB（占 96%）** |
| └ 关闭 R035 后 | N^0.99（线性） | 385 MB |
| 流式修复后（见 §5） | N^0.91 | **63 MB** |
| 现状审核耗时 | N^1.10 | **167 s** |

**必须说明**：`tracemalloc` 只统计 Python 层分配，不含解释器开销、内存碎片与 sqlglot 的 C 层分配，**实际 RSS 通常是它的 1.5～3 倍**。即 6000 表的真实峰值大概率在 **3～5 GB** 量级。单机跑 2 个 worker，击穿是必然而非偶然。

---

## 4. 我对 G/Q 的一处**修正**：耗时构成被归错了

G 与 Q 都写：**DDL 拉取约 200～240 秒 ＋ 审核"数秒内"内存击穿 ＝ 249 秒**。

**这与实测不符。**我测得的审核阶段耗时是 N^1.10，6000 表外推 **167 秒**——审核阶段是**分钟级，不是"数秒"**。若 DDL 拉取真的占 200～240 秒，总耗时应是 370～400 秒，而不是观测到的 249 秒。

按实测反推，249 秒的合理构成是：

```text
DDL 拉取  ≈  80 s   （6000 次 SHOW CREATE，约 13 ms/次，局域网经 Proxy 合理）
审核阶段  ≈ 167 s   （实测外推；内存在此阶段持续攀升）
合计      ≈ 247 s   ≈ 观测值 249 s
```

G/Q 的 200～240s 建立在"单次 SHOW CREATE 35～40ms"这个**未经实测的假设**上。作为对照，我在本机回环实测单次仅 **0.25 ms**；局域网加 Proxy 若为 13ms 则完全吻合观测。**35～40ms 偏高，缺乏依据。**

**为什么这处修正很重要（不是抠字眼）：**

* 若按 G/Q 的归因（拉取占 240s），修好内存后用户**仍要等约 4 分钟**，问题只是"不崩了但还是很慢"；
* 若按实测归因（审核占 167s），修好后**总耗时也会明显下降**，但**仍有分钟级等待**——因为解析 6000 条 DDL 本身就要时间（流式修复后耗时 157s，几乎没降，见 §5）。

两种归因导出的用户预期与后续优化方向不同。**这一点必须用内网实测钉死，不能靠估算**——办法见 §7。

另外，两份报告都写"内存**瞬间**暴涨/**瞬间**击穿"。实测表明内存是在整个审核阶段**持续攀升**的（分钟级），不是瞬时尖峰。这意味着：**崩溃点取决于机器可用内存**——同一个库在内存更大的机器上可能侥幸通过，在更小的机器上更早崩；反过来，**比 6000 表更小的库在内存紧张时同样可能崩**。不能把"6000 表"当成安全阈值。

---

## 5. 修复方案（已做原型验证，非纸面建议）

### 5.1 关键洞察：R035 的语义本来就只需要单趟前向扫描

`_build_r035_cross_table_context` 的文档字符串自己写明了基准方向：

> 指向**此前语句**中同名列的 ... （基准方向：只与更早出现的表比较，第一张表建立基准但不报）

**既然每条语句只需要看"比它更早"的表，那就根本不需要先把全部语句物化，也不需要为每条语句快照整个索引。**一边扫一边维护一份**活索引**即可：对第 i 条语句，先拿当前活索引跑规则（此时索引里恰好只有 0..i-1 的列），跑完再把第 i 张表的列追加进去。

这样：
* 活索引只有**一份**，空间 O(总列数)，不再有 N 份快照；
* `ParsedSQL` 用完即可回收，恢复 v1.6.3.0 的 O(1) 常驻；
* **R035 的结果与现状逐条完全相同**（不是近似，是等价）。

### 5.2 原型与实测对照

我在内存中打补丁做了原型（**未修改仓库任何文件**），与现状同输入逐条比对违规集合：

| 表数 N | 现状内存(MB) | 流式内存(MB) | 内存降幅 | 现状耗时(s) | 流式耗时(s) | 违规结果 |
|---|---|---|---|---|---|---|
| 200 | 25.6 | 2.8 | **89.1%** | 4.14 | 3.97 | ✅ 逐条一致 |
| 400 | 56.7 | 5.4 | **90.5%** | 8.28 | 8.16 | ✅ 逐条一致 |
| 800 | 138.5 | 9.8 | **93.0%** | 18.40 | 17.33 | ✅ 逐条一致 |
| 1600 | 375.3 | **18.9** | **95.0%** | 39.89 | 37.91 | ✅ 逐条一致 |

**降幅随 N 增大而增大**（89%→95%），说明消掉的正是超线性项。6000 表外推：**1.92 GB → 63 MB，约 31 倍**。耗时略有下降（少了拷贝开销），**不牺牲性能**。

### 5.3 建议的落地写法（骨架，供 Q 施工参考）

```python
def audit_file(self, content, file_path="", rule_overrides=None, instance_type=None):
    results = []
    content = normalize_newlines(content)
    stmts = (self._extract_sql_from_mybatis(content) if file_path.lower().endswith(".xml")
             else [(s, ln) for s, ln, _e in split_audit_script(content)])

    # v1.6.3.x 修复：恢复流式单趟。R035 只与更早语句比较，故活索引即可，
    # 不再全量物化 ParsedSQL，也不再为每条语句快照整个索引（O(N^2) 内存源）。
    r035_on = self._r035_enabled(rule_overrides, instance_type)
    index = {}                                    # 活索引，全程仅一份
    for idx, (sql_text, line_no) in enumerate(stmts):
        parsed = self.parser.parse(sql_text)
        meta = {self._R035_CROSS_KEY: index} if r035_on else {}
        violations = self._audit_parsed(parsed, sql_text, meta, line_no,
                                        rule_overrides, instance_type)
        results.append(AuditResult(sql=sql_text.strip(), sql_type=parsed.sql_type,
                                   passed=not violations, violations=violations,
                                   file_path=file_path, line_number=line_no))
        # 先跑规则再入索引 —— 保证规则看到的恰是"更早语句"，与快照语义等价
        if r035_on and parsed.is_create_table and not parsed.parse_error:
            tname = parsed.tables[0] if parsed.tables else ""
            if tname:
                for col in parsed.columns:
                    nm = col.get("name", "")
                    if nm:
                        index.setdefault(nm.lower(), []).append(
                            {"table_name": tname, "type": col.get("type", ""),
                             "raw_type": col.get("raw_type", ""), "statement_index": idx})
        del parsed
    return results
```

**注意**：`_audit_parsed` 里对非 R035 规则还原 `public_meta` 的那段逻辑（v1.6.3.2 为避免保留键污染 R064 等规则而加）**必须原样保留**，本改动不触碰它。

`_build_r035_cross_table_context` 在流式改造后不再被 `audit_file` 使用；**建议保留函数并标注废弃**，不要立即删除——它可能被测试或其他入口引用，删除会扩大爆炸半径。

### 5.4 必须补的护栏（修复之外）

内存修好后，**耗时仍是分钟级**（6000 表约 157 秒），这条同步请求链路仍然脆弱：

1. **超大批量的显式上限与提示**。当单批语句数超过阈值（建议先取 2000，依内网实测校准）时，返回明确的业务错误，说明"本次共 N 条语句，超过单次审核上限"，而不是让用户等 4 分钟再拿到一个 `Failed to fetch`。
2. **`extract_and_audit` 的响应瘦身**。当前响应把 `extracted_sql`（完整 6000 表 DDL 全文）**连同**全部 `results` 一起返回给浏览器。SQL 全文应改为按 `report_id` 另行下载（`/report/{id}/sql` 已存在），不必塞进审核响应。
3. **进程级兜底**。worker 猝死目前对用户表现为裸的 `Failed to fetch`。建议 systemd 单元加 `MemoryMax=` 并在应用侧对超大批量提前失败，把"进程被杀"变成"可读的业务错误"。

---

## 6. 影响面（比工单描述的更宽）

`audit_file` 是**文件审核**与**在线元数据审核**的共用底座，因此：

| 入口 | 是否受影响 |
|---|---|
| SQL审核 > 在线元数据审核 | **受影响**（本次故障入口）；服务端自行拼装 SQL，**不受 50 MiB 请求体限额约束**，语句数完全由库表数决定 |
| SQL审核 > 文件审核 | **同样受影响**。用户上传一个含大量 CREATE TABLE 的 .sql 文件会走同一路径；只是受 50 MiB 请求体限额间接封顶，触发门槛比在线元数据审核高 |
| SQL审核 > 即时审核 | 不受影响（单条语句走 `audit_sql`） |

**建议一并回归**：v1.6.3.2 之后是否有其他"大库"场景在用文件审核，避免只修了一个入口。

---

## 7. 请内网协助确认的三件事（用于钉死剩余不确定性）

我在外网无法接触内网实例，以下三项**必须由具备内网条件的一方实测**，我不做推定：

**(1) 两阶段耗时拆分**——这是我与 G/Q 归因分歧的裁决依据。在 `extract_and_audit` 的三个位置各打一条 INFO 日志（DDL 拉取开始／拉取完成／审核返回），复现一次即可读出真实构成：

```text
[T1] 开始拉取元数据 db=xxx
[T2] 元数据拉取完成 表数=6xxx 耗时=___s          ← 若此值远小于 200s，则 G/Q 的归因需修正
[T3] 文件审核完成 语句数=___ 耗时=___s
```

**(2) worker 的真实死因**——G、Q、我三方都只能确定"进程死了"，**死亡信号未钉死**。崩溃当时（而非事后）执行：

```bash
# 复现前开始持续记录 worker RSS（关键：事后 systemctl 看到的 150MB 是新进程）
while true; do ps -o pid,rss,etime,cmd -p $(pgrep -f "uvicorn backend.main" | tr '\n' ',' | sed 's/,$//'); sleep 5; done | tee /tmp/rss.log
# 崩溃后立即查
dmesg -T | tail -50                      # 看有无 Out of memory / segfault
journalctl -k --since "-10min" | grep -iE "oom|killed|segfault"
systemctl show tdsql-sqlcheck -p MemoryMax -p MemoryCurrent
cat /sys/fs/cgroup/memory.events 2>/dev/null || cat /sys/fs/cgroup/memory/memory.failcnt
```

**注意**：用户此前 `dmesg | grep -i oom` **无任何输出**。这有两种可能：① 确实不是 OOM-killer（可能是 sqlglot 深递归导致的栈溢出 SIGSEGV——同样是静默死亡、无 traceback）；② dmesg 权限/缓冲问题。**在拿到上述证据前，"内存耗尽被 OOM 杀死"只能作为最可能的假设，不能写成已证实的结论**——这一点 G 和 Q 也都做了保留，我同意他们的保留。

**(3) 修复后的实测复验**——改完后在同一个 6000 表库上复现，记录：峰值 RSS、总耗时、两阶段拆分、审核结果条数是否与 v1.6.3.0 一致。**结果一致性必须逐条比对，不能只看"没报错"。**

---

## 8. 排查边界声明

1. 本次排查**未修改仓库任何文件**。修复原型为进程内打补丁，测完即弃。
2. 所有内存/耗时数字均为**本机合成负载实测＋log-log 拟合外推**，非内网实测。合成负载的表结构（20 列）与真实业务表不同，绝对值会有偏差；但**增长幂次与量级结论不依赖于具体表结构**。
3. `tracemalloc` 计量小于真实 RSS，文中已就此明确标注，未把它当作 RSS。
4. worker 死亡信号未钉死（见 §7-2），我未把"OOM"写成已证实结论。
5. 我与 G、Q 在**根因**上一致；本报告的增量是**量化、归因修正与已验证的修复原型**，不构成对二者结论的推翻。

---

排查人：智能体 A（ClaudeA）
排查基线：`main` / `c15d055`
提交给：Mr.Linsang
