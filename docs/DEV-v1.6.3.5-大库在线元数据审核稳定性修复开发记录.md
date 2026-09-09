# DEV-v1.6.3.5 开发记录 — 大库在线元数据审核稳定性修复

| 项 | 内容 |
|---|---|
| 产品版本 | v1.6.3.5 |
| 设计依据 | `docs/DETAIL-v1.6.3.5-大库在线元数据审核稳定性修复.md`（Rev.B，2026-09-09，A 两轮评审通过） |
| 施工方 | 智能体 Q |
| 施工日期 | 2026-09-09 |
| 本批交付边界 | **DU-1（核心修复 FIX-01 / D01）**：消除 R035 全历史 O(N²) 浅拷贝 + 全批 AST 常驻，恢复流式审核。DU-2（在线任务加固 FIX-02~05，D02—D14 大子系统）为设计规定的独立后续单元，见 §6 |

---

## 1. 本批交付概述

按设计 §10.1 的两个独立交付单元推进，本批完成 **DU-1（核心修复）**——这是用户可感知故障（6000+ 表大库"拉取元数据并执行文件审核"报 `Failed to fetch`）的**直接根因修复**。

| 项 | 状态 |
|---|---|
| FIX-01 / D01 R035 平方级消除 | ✅ 完成并验证 |
| 设计 §4.3 有界见证索引 | ✅ `backend/engine/r035_context.py` 新建 |
| 流式 `audit_file`（`iter_audit_file`） | ✅ `backend/engine/checker.py` 改造 |
| ALG-01~11 回归锁 | ✅ `tests/test_v1635_r035_streaming.py` 14 用例全过 |
| 全量回归零回归 | ✅ 2020 passed + 30 skipped + 0 failed |
| 内存实测线性有界 | ✅ 2000 表峰值 ~22 MiB（外推 6000 表 ~60 MiB） |

---

## 2. 根因回顾与修复原理

### 2.1 根因（已于排查报告证实）
v1.6.3.2（REQ-05A / commit c0e5e25）为 R035 跨表字段类型检查把 `audit_file` 改为：
1. `parsed_items = [(sql, ln, parse(sql)) for ...]` —— 全批语句 AST **常驻内存**；
2. `_build_r035_cross_table_context` 对每条语句把累积同名列索引做 `{k: list(v)}` **全量浅拷贝** —— O(N²) 引用膨胀。

6000 表时 AST 常驻（数 GB）+ O(N²) 索引拷贝（上千万引用）击穿 worker 内存 → 进程猝死 → TCP RST → 前端 `Failed to fetch`。v1.6.3.0 流式（parse→audit→释放，O(1)）故正常。

### 2.2 修复原理（照 DETAIL §4.3 施工）
- **新建 `backend/engine/r035_context.py`**：`R035PriorIndex`——每个列名只保留至多 5 个见证引用槽位（anchor + outside_anchor_table≤2 + outside_anchor_type≤2），对外投影按 `(statement_index, column_index)` 去重排序。语义与全历史快照在"首个冲突来源"上**完全等价**（设计附录 B 模型已证）。
- **改造 `checker.audit_file`**：恢复流式——逐条 `parse → 见证投影 → _audit_parsed → 更新历史 → 释放`；新增内部迭代入口 `iter_audit_file(...)`，公共 `audit_file(...)` 内部 `list(iter_audit_file(...))`，**所有既有调用方（audit_service/cli/gitlab_hook）签名与返回结构不变**。
- **R035 规则类本身不改**；私有键 `__r035_cross_table_columns__` 仍只供 R035 消费（`_audit_parsed` 的 public_meta 隔离逻辑不变）。
- 旧的 `_build_r035_cross_table_context` 已从生产调用链移除（audit_file 不再调用），保留为小规模测试 oracle 供等价性比对。

---

## 3. 关键实现

### 3.1 `backend/engine/r035_context.py`（新建）
- `_ColumnSummary`（dataclass）：anchor / outside_anchor_table / outside_anchor_type 三槽，仅持标量（table_name/type/raw_type/statement_index/column_index），**不持有 ParsedSQL/AST/完整 SQL**。
- `R035PriorIndex.project_for_columns(columns)`：只投影当前语句涉及的列名 → `{列名lower: [见证引用]}`，供 R035 消费；`add_columns(table, columns, statement_index)` 在**审核之后**追加历史（先比后加）。

### 3.2 `backend/engine/checker.py`
- `audit_file(...)` → 返回 `list(self.iter_audit_file(...))`（兼容）。
- `iter_audit_file(...)`：规范化 → 拆句（保留原顺序/行号）→ R035 启用才建索引 → 逐条 parse→见证投影→审核→（审核后）更新历史→yield AuditResult。

---

## 4. 验证证据

### 4.1 回归锁（`tests/test_v1635_r035_streaming.py`，14 用例全过）
- **ALG-01**：a.id INT / b.id BIGINT / c.id INT → R035=[否,是,是]，c 的首个冲突来源为 b。
- **ALG-02**：VARCHAR(20)/(50)、DECIMAL(10,2)/(12,0) 不因长度误报；列名大小写不敏感命中。
- **ALG-03**：schema 限定名/同表反复 CREATE/空类型/重复列名——与完整历史 oracle 首个违规来源逐项一致。
- **ALG-04**：多列冲突按当前列序报首个。
- **ALG-05**：VIEW/SELECT/ALTER 不入历史；语法错误 E999 不丢。
- **ALG-06**：CRLF/注释分号/过程体拆句数与行号一致。
- **ALG-07**：R035 off 不建索引；私有键不污染 R064 等其他规则。
- **ALG-08**：驱动生产 `R035PriorIndex` 复现 DETAIL 附录 B 模型——**1,062,880 次查询 0 差异**，见证槽位峰值 ≤5。
- **ALG-09**：相同 SQL 全规则下，新流式 `audit_file` 与旧 oracle 的 AuditResult（sql/sql_type/passed/violations/line_number）**逐项一致**。
- **ALG-10**：800 表 × 20 同名列，每列见证数 ≤5（O(U)，无平方级）。
- **ALG-11**：两次请求索引隔离、无全局污染、可复现。

### 4.2 内存实测（tracemalloc，本机 Python 分配口径）
| 表数 N | tracemalloc 峰值 |
|---|---|
| 200 | 3.6 MiB |
| 800 | 9.3 MiB |
| 2000 | 21.9 MiB |

线性增长（非平方）；外推 6000 表 ≈ 60 MiB，远低于修复前的数 GB。**注**：此为 tracemalloc 的 Python 分配口径，不等于完整子进程 RSS；真实 6000 表端到端容量验收属 DU-2 验收范围（§11.4）。

### 4.3 全量回归
**2020 passed + 30 skipped + 0 failed**（约 8 分 42 秒）。较上版（质检整改后 2006）多 14 个 = 本批新增 ALG 用例；30 skipped 为既定环境性跳过（SIT/UAT 需内网凭据 + 门禁退役）。零回归——audit_file 的 R035 重构未破坏任何既有用例。

---

## 5. 本批边界声明
1. 本批仅 DU-1（核心内存缺陷），未动审核规则业务尺度、解析器类型归一、SQL 拆句语法、其他模块预算参数。
2. 6000 表真实 TDSQL 端到端容量验收、双 Web worker 下的事件循环健康检查、以及在线任务异步化（DU-2）属设计规定的后续单元，本批未实施。
3. tracemalloc 为本机 Python 分配测量，非完整子进程 RSS；真实容量验收须在内网实测。

---

## 6. 剩余工作（DU-2，设计 §10.1 独立后续单元）
DU-2（FIX-02~05，D02—D14）是在线任务加固大子系统：MySQL v15 迁移（2 表）、metadata_audit_repository / pipeline / workers(metadata_runner+metadata_audit_worker) / job_process / artifacts、新 API `metadata_audit`、旧路径 410 退役、前端任务卡/轮询/分页/取消、双服务部署与回滚、worker 可观测性、`tests/test_v1635_*`。它把重任务隔离到独立进程并消除"事件循环被同步 I/O 阻塞 250s"的健康检查风险——这是把 DU-1 修复的核心缺陷**加固为可恢复、可观测、可取消**的完整形态。DU-2 不改变 DU-1 已证实的根因修复结论。

---

施工人：智能体 Q
施工对象：v1.6.3.5 第一批（DU-1 核心修复 FIX-01/D01）
提交给：Mr.Linsang
