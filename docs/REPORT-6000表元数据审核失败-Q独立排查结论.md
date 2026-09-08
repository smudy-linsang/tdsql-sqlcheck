# REPORT — 6000+ 表大库"拉取元数据并执行文件审核"报 `Failed to fetch` 独立排查结论（Q）

| 项 | 内容 |
|---|---|
| 报告性质 | 开发智能体 Q 的**独立代码级排查结论**，供其他智能体交叉排查比对 |
| 针对版本 | v1.6.3.2 ~ v1.6.3.4（v1.6.3.0 确认可用） |
| 故障环境 | 内网单机 `10.243.16.252:8000`（**无 Nginx**，systemd 托管 uvicorn `--workers 2`） |
| 故障对象 | 总账集中式开发库 `15064-sungl_am`（6000+ 表） |
| 现象 | 点击"拉取元数据并执行文件审核"，等待约 **250s（4 分 10 秒）** 后前端报 `提取失败: Failed to fetch`；多次复现时间高度一致 |
| 参考材料 | 内网智能体分析 `tdsql_metadata_extract_failure_analysis.md`；服务状态 `内网测试环境排查输出.txt`；G 排查报告 `REPORT-6000表元数据审核失败根因深度排查与解决方案报告.md` |

---

## 一、结论（一句话）

**根因是 v1.6.3.2（REQ-05A / R035 跨表字段类型检查）把 `RuleChecker.audit_file` 从"流式逐条审核"改成了"全量预解析 + O(N²) 跨表索引浅拷贝"，6000 表时内存爆炸导致 uvicorn worker 进程猝死，操作系统重置 TCP 连接，浏览器 fetch 抛 `TypeError: Failed to fetch`。** 与 Nginx 超时无关（本环境无 Nginx），与 v1.6.3.4 新增的 `capture_report_context`/`report_context_json` 无关（二者耗时 <1ms，是伪因）。

**置信度：高**（代码级证实 + 秒级时间线吻合 + 版本差异吻合）。唯一未 100% 钉死的是 worker 的**具体死亡信号**（OOM-killer vs SIGSEGV/abort），需内网按 §六 命令补证，但不影响根因结论与修复方向。

---

## 二、证据链

### 2.1 worker 进程猝死是直接死因（铁证）
`内网测试环境排查输出.txt` 第 100-101 行：
```
18:10:09 ... [846885]: INFO: Waiting for child process [850881]
18:10:09 ... [846885]: INFO: Child process [850881] died
18:10:10 ... 重新拉起新 worker [870996]
```
- 请求于 **18:06:00** 注册连接（同文件第 83 行），worker 于 **18:10:09** 死亡 = **第 249 秒**，与用户"每次 ~250s 失败"精确吻合。
- 18:06:11~18:09:11 该 worker 内 APScheduler 线程每分钟正常打点 → 前 ~191s 进程健康（在做串行 DDL 网络 I/O）；**死亡发生在 DDL 拉取完成、进入审核引擎的瞬间**。
- worker 死亡 → 内核关闭其 socket → 向浏览器发 **TCP RST** → 浏览器原生 `fetch()` 抛 `TypeError: Failed to fetch`（**不是** 500/504 响应体，故前端走 catch 分支显示笼统错误）。这解释了为何不是干净的 HTTP 错误码。
- `systemctl status` 事后仅 150MB 内存，因为那是**新拉起进程**的初始内存，旧进程内存已随死亡释放——不能据此排除旧进程曾内存爆炸。

### 2.2 代码级根因（已在本仓库当前代码证实）
`backend/engine/checker.py::audit_file`（v1.6.3.2 / REQ-05A 引入，现仍存在）：
```python
# checker.py:300-303  —— 全量预解析，6000 个 AST 全部常驻内存
parsed_items = [(sql_text, line_no, self.parser.parse(sql_text))
                for sql_text, line_no in stmts]
metas = self._build_r035_cross_table_context(parsed_items, rule_overrides, instance_type)
```
```python
# checker.py:340-342  —— O(N²) 浅拷贝爆炸
for idx, (_sql, _line, parsed) in enumerate(parsed_items):
    metas[idx][self._R035_CROSS_KEY] = {k: list(v) for k, v in index.items()}  # 每表全量拷贝累积索引
    ...向 index 追加当前表字段...
```
- **内存模型退化**：① 6000 个 sqlglot AST 同时常驻（单表 AST 含成百上千 Python 对象，6000 表约数 GB）；② 对每个 idx 把**累积到当前的整个 index 字典**做 `{k: list(v)}` 浅拷贝，累计 O(N²) 的 dict/list 引用（6000 表 → 上千万引用，再数 GB）。二者叠加瞬间击穿 worker 可用内存 → 崩溃。
- **R035 默认启用**（`rules/ddl.py:553 enabled = True`），故 `_r035_enabled` 为真，O(N²) 循环**必然执行**。即使关掉 R035，第 300 行的全量 AST 常驻仍是 O(N) 重内存，超大 N 下仍可能崩，只是阈值更高。
- **v1.6.3.0 为何正常**：其 `audit_file` 为流式——逐条 `parse → audit → 离开作用域 GC 回收 AST`，空间 O(1)，内存稳定 100~150MB，6000 表平稳通过。v1.6.3.2 改成全量常驻后才崩。**这与"v1.6.3.0 正常、v1.6.3.2 起失败"完全吻合。**

### 2.3 时间线为何精准 ~250s
- 串行 6000 次 `SHOW CREATE TABLE`（`backend/api/sql_audit.py:316-345`）≈ 6000 × 35~40ms ≈ **210~240s**（同步阻塞事件循环）。
- 拉取完进入 `audit_file_content → audit_file`：全量 parse + R035 O(N²) 索引 ≈ 数秒内内存击穿 → **第 ~249s 崩溃**。两阶段相加正好落在 245~250s，故"每次都在 250s 左右失败"具有物理必然性。

### 2.4 排除的伪因
| 怀疑点 | 结论 | 依据 |
|---|---|---|
| Nginx `proxy_read_timeout` 120s 超时（内网智能体主结论） | **排除** | 本环境**无 Nginx**（直连 :8000，systemd 托管 uvicorn）；且若 120s 超时应报 504 响应体而非 `Failed to fetch`，且不会每次精准 250s |
| v1.6.3.4 新增 `capture_report_context()` / `report_context_json`（内网智能体"瓶颈1/2"） | **排除** | 仅开头一次内存深拷贝+校验，<1ms；与 250s 崩溃无关。内网报告自身也承认"DDL 拉取逻辑 v1.6.3.0 与 v1.6.3.4 相同"，却仍归因于这两个新增点，自相矛盾 |
| TDSQL 主动断连 | **排除** | 断连会抛 `OperationalError` 被 FastAPI 捕获返回 4xx/5xx，进程不会死 |
| 前端 fetch 自身超时 | **排除** | `apiFetch` 无超时设置（无 AbortController），不会主动 abort |

---

## 三、什么情况下会报这个错（触发条件）

1. **单批语句数 N 足够大**：`audit_file` 一次性接收全部语句（在线元数据审核把 6000 张表 DDL 拼成**一个** .sql 一次提交）。内存 ≈ O(N) AST 常驻 + （R035 启用时）O(N²) 索引拷贝。存在阈值 N\*：N < N\* 正常（小库/中库秒级返回），N ≥ N\* 内存击穿崩溃。6000 表已远超 N\*。
2. **R035 处于启用状态**（默认启用）：触发 O(N²) 索引拷贝，显著降低 N\*。关闭 R035 可抬高阈值（仅 O(N) AST 常驻），但**不是根治**，超大 N 仍会崩。
3. **走"在线元数据审核/拉取元数据并执行文件审核"这类"一次提交全库 DDL"的入口**：普通小文件审核（几十条 SQL）N 很小，不触发。
4. **伴随现象**：请求耗时 ≈ 串行 DDL 拉取时间（O(N) 网络 RTT）+ 崩溃点；worker 死亡后被 uvicorn 主进程重新拉起（服务"看起来"还活着，但本次请求连接已 RST）。
5. **部署形态放大**：`--workers 2` 多进程下每个 worker 独立内存预算，单 worker 承载该重型请求即崩；同步阻塞事件循环还导致该 worker 在 4 分钟内无法响应任何其他请求/健康检查。

**反例（不报错的情况）**：小/中库（N 小）；或单次只提交少量 SQL；或内存足够大的机器（阈值 N\* 更高，但 O(N²) 迟早击穿，仅推迟）。

---

## 四、对三份参考材料的交叉评述

- **G 的报告**：根因判断**正确**（R035 O(N²) + 全量 AST 常驻 → worker 猝死 → TCP RST），与我在当前代码的核实一致（checker.py:300/342 确实存在）。其 §6.1 修复方向（R035 改流式 O(N) 共享 `seen_columns`）正确。
- **内网智能体分析**：把根因归为"Nginx 120s 超时 + capture_report_context/report_context_json 新增开销"，**两点均不成立**（无 Nginx；新增点 <1ms）。但其对"串行 6000 次 SHOW CREATE TABLE ≈ 200~240s"的耗时估算、以及"批量拉取 DDL / 异步任务化"的优化方向是有价值的**次级优化**（治"慢"，不治"崩"）。
- **服务状态排查输出**：提供了 worker 猝死的铁证（`Child process died` @249s）与"事后内存仅 150MB 是新进程"的关键解读，支撑根因。

---

## 五、修复建议（按优先级，供修复轮采纳）

1. **P0 根治：恢复 `audit_file` 流式 + R035 改 O(N)**（`backend/engine/checker.py`）
   - 去掉第 300 行"全量预解析常驻"，改回**逐条 parse→audit→释放**（O(1) 内存）。
   - R035 跨表上下文改为**单一共享查找表** `seen_columns: dict[col_name, FirstSeenInfo]`，逐条更新、零字典克隆（O(N) 空间）；保持"只与更早出现的表比较、首表建基准不报"的语义，保证零漏报零误报。
   - 预期：内存回到 100~150MB，worker 不再猝死，`Failed to fetch` 消失。
2. **P1 提速：元数据拉取批量化/并发化**（`backend/api/sql_audit.py:316-345`）：6000 次串行 SHOW CREATE TABLE 改批量（mysqldump --no-data 或多连接 ThreadPoolExecutor），240s → 10~30s。治"慢"，缩短暴露窗口。
3. **P2 架构：长任务异步化 + 进度轮询**：`extract-and-audit` 改"立即返回 task_id + 后台执行 + 前端轮询进度"，彻底摆脱单 HTTP 长连接。治"体验/超时面"。
4. **配套**：为 `audit_file` 增加内存/规模护栏（如单批语句数上限 + 明确报错而非崩溃）；前端 `apiFetch` 增加 AbortController 超时与友好提示（区分 AbortError 与网络错误）。

---

## 六、需内网补证项（不改变结论，仅钉死细节）

1. worker 具体死亡信号：`dmesg -T | grep -iE "segfault|traps|killed|oom"`、`journalctl -k --since "18:05" --until "18:12"`、`grep -E "(850881|oom-killer|Out of memory)" /var/log/messages`。区分 OOM-killer vs SIGSEGV/abort。
2. 机器内存与 cgroup 上限：`free -h`、`systemctl show tdsql-sqlcheck -p MemoryCurrent -p MemoryMax -p MemoryLimit`。
3. **A/B 对照**：临时关闭 R035 复跑 6000 表——若不再崩溃（或崩溃阈值明显后移），进一步坐实 O(N²) 索引为主因；若仍崩，则全量 AST 常驻（O(N)）亦为共因（二者同属 v1.6.3.2 的 audit_file 重构，修复方案一致）。
4. 修复后压测：6000+ 表复跑，监测 worker 内存峰值（应 <200MB）与响应完整性。

---

## 七、结论复述（供交叉排查比对）

- **直接死因**：uvicorn worker 在第 ~249s 猝死 → TCP RST → 浏览器 `Failed to fetch`。
- **根本原因**：v1.6.3.2 起 `audit_file` 全量预解析 AST 常驻 + R035 O(N²) 跨表索引浅拷贝，6000 表内存爆炸。
- **触发条件**：单批语句数 N 大（全库 DDL 一次提交）且 R035 启用；小批量不触发。
- **版本差异**：v1.6.3.0 流式 O(1) 内存故正常；v1.6.3.2+ 全量常驻故崩。
- **与 Nginx / capture_report_context 无关**（伪因，已排除）。
- **修复**：audit_file 恢复流式 + R035 改 O(N) 共享查找表（P0）；辅以 DDL 批量化（P1）与异步任务化（P2）。

**报告人**：智能体 Q
**日期**：2026-09-08
