# v1.6.3.6 第二轮 SIT 整改要求 · R2-M-02 / R2-M-05

| 项 | 内容 |
|---|---|
| 版本 | v1.6.3.6（不升版号，仍属本版整改） |
| 工作基线 | `7bf0fea` |
| 上游 | `docs/SIT2-v1.6.3.6-内网大库问题修复-第二轮SIT测试报告-ClaudeA.md` |
| 决策 | Mr.Linsang 2026-09-11：R2-M-02 按"取消前置过滤、全靠 try/except 兜底"做；R2-M-05 一次做完，不拆两步 |
| 提出人 | 智能体A（独立评审/测试） |
| 施工方 | Q |

---

## 0. 本文性质

**这份是可直接施工的要求，不是选项清单。** 所有工程判断我已经做完（含我上轮说"要内网实测"
的那个开销问题，见 §1.1），Q 照做即可。若对某条有技术异议，请直接反驳并给出依据，
但不要因为"没定"而停工——没有未定项。

一并把第二轮遗留的 R2-M-01 / R2-M-03 / R2-M-04 / R2-N-01 / R2-N-02 收进来，本次一起清掉。

---

## 1. R2-M-02：取消前置过滤，命名模式降级为"失败之后的分类依据"

### 1.1 开销问题已经量化，不需要内网实测再定

我上轮说"约 2800 次注定失败的 SHOW CREATE 开销测不出来"，这话说早了。失败查询**没有结果集要传**，
比成功的还便宜，本机 MySQL 协议实测：

| 操作 | n | 单次 |
|---|---|---|
| 成功的 `SHOW CREATE TABLE`（对照） | 2000 | 0.080 ms |
| 失败的 `SHOW CREATE TABLE`（1146） | 2000 | **0.061 ms** |

外推（本机为下界，Proxy 多一跳路由+网络）：

| 场景 | 本机下界 | ×3 | ×10 悲观 | ×30 极悲观 |
|---|---|---|---|---|
| ECIF 约 2800 张子表 | 0.17 s | 0.51 s | 1.70 s | 5.10 s |
| 6000 表库约 5800 张子表 | 0.35 s | 1.06 s | 3.52 s | **10.56 s** |

对照内网实测：2668 表库全流程 **263 s**，6097 表库跑到 **342 s**。
即便按 30 倍极悲观算，多出来的也只占 3%。**开销不构成反对理由，此条结案。**

唯一需要留意的副作用是 Proxy 侧错误日志量（每任务多约 N 条 660）。这属于运维观感，
不影响正确性；如内网 DBA 反映刺眼，再议降噪，**不作为本次前置条件**。

### 1.2 改法

**核心是一次倒置**：命名模式**不再决定"要不要提取"**，只决定**"这次已经失败的提取，
该不该算良性"**。

#### 1.2.1 删除前置过滤分支

`backend/services/metadata_audit_pipeline.py` 第 184-192 行整段删除
（`if kind == "TABLE" and is_tdsql_internal_table(...): ... continue`）。
**所有选中对象一律走 `SHOW CREATE`。**

#### 1.2.2 失败后再分类

在第 194 行起的 `except` 分支内判定 category：

```
category == "tdsql_internal"  当且仅当以下四条同时成立：
   a) instance_type == "distributed"（集中式一律不算良性）
   b) 错误可判定为"对象不存在"（Proxy 660 / MySQL 1146；从异常 args[0] 或文本 (NNNN, 提取）
   c) 名字匹配 ^(?P<parent>.+?)_tdsql_(?:subp|shard)\d+$
   d) parent 在本库枚举清单 available_tables 中
否则 category == "extract_failed"
```

**为什么这样就零误杀**：能走到这个分支，说明该对象的 DDL **已经被目标库确认读不到**——
无论它是不是物理子表，它的 DDL 本来就取不到、本来就进不了审核。命名模式此时最坏的
后果只是"把一条异常跳过错记成良性跳过"（计数分类错），再也不可能造成"一张能读的真业务表
被跳过"。Rev.G/Rev.J 三条件想防的事，被这个倒置从根上消掉了。

顺带：`orders_tdsql_subp202601` 若是真业务表，`SHOW CREATE` 会**成功**，直接走正常提取路径，
连分类都轮不到。第二轮 R2-M-02 残留至此彻底关闭。

#### 1.2.3 函数改名与签名（同时关掉 R2-M-03）

```python
def classify_extract_failure(table_name: str, error: Exception,
                             instance_type: str, available_tables: set) -> str:
    """把一次已经失败的 DDL 提取归类为 'tdsql_internal'（良性）或 'extract_failed'（异常）。

    注意：本函数**不参与**"要不要提取"的决策——所有对象一律先尝试 SHOW CREATE，
    失败之后才调用它。命名模式在这里最坏只会把异常错记成良性，不会造成漏审。
    """
```

- **四个参数全部必传，不留缺省值**（R2-M-03：缺省 `"distributed"` 是倒向不安全的一侧）。
- `is_tdsql_internal_table` 删除，不保留别名——留着就还会有人拿它当过滤器用。
- `extract_metadata` 的 `instance_type` 形参**改为必传**，删掉 `= "distributed"`。
- `metadata_audit_worker.py` 第 156 行 `instance_type=instance_type or "distributed"`
  **去掉 `or "distributed"`**，原值直传。`instance_type` 为空（探测失败）时，
  条件 a 不成立 → 一律记 `extract_failed`。**拿不准就当异常，这是安全的方向。**
- `_TDSQL_INTERNAL_PARTITION_PATTERN` 正则保留 `\d+`（不得放松回 `\d*`）。

### 1.3 本条必须补的锁

| 锁 | 断言 | 对应变异体（必须变红） |
|---|---|---|
| L1 | 名字像子表但 `SHOW CREATE` **成功** → 正常提取，计入 extracted，不进 skipped | 恢复前置过滤 |
| L2 | 分布式 + 660 + 名字匹配 + 父表在清单 → `tdsql_internal` | 去掉四条件中任意一条 |
| L3 | 集中式 + 660 + 名字匹配 → `extract_failed`（不是良性） | 去掉条件 a |
| L4 | `instance_type=""` + 660 + 名字匹配 → `extract_failed` | 恢复 `or "distributed"` |
| L5 | 非"不存在"类错误（如 1044 无权限）+ 名字匹配 → `extract_failed` | 去掉条件 b |
| L6 | `classify_extract_failure` / `extract_metadata` 缺参调用抛 TypeError | 恢复缺省值 |

---

## 2. R2-M-05：跳过与节选的可见性，一次做完

### 2.1 不新增终态 —— 这条我定了，理由写在这里

我查了 `SUCCEEDED` 的消费面：`backend/api/metadata_audit.py` 有 **8 处**
（第 298/315/329/344/357/369/384 行一带）拿它当"能不能读结果 / 能不能下载"的门禁，
外加 `TERMINAL_STATES` 集合、`frontend/static/js/app.js:1736` 的
`['SUCCEEDED','FAILED','CANCELLED']`、过期任务回收逻辑。

**新增 `PARTIAL_SUCCESS` 会让"部分成功"的任务连报告都下载不了**——正好和本条的目的相反，
还要动一个刚扛完三轮 UAT 的状态机。**终态保持不变，用显式标志位 + 四个呈现面达成可见性。**
目标是"看得见"，不是"多一个状态名"。

### 2.2 迁移：`backend/schema/v15/151_audit_history_completeness.sql`

沿用 `backend/schema/v4/040_instance_type_scope.sql` 的写法与 NULL 语义：

```sql
-- v1.6.3.6 / SIT-R2 / R2-M-05：审核结果完整性留痕
-- 全部为新增列，无删除/重命名/类型变更；回滚只需停止读取新列。
-- NULL 语义同 040 的 instance_type：本特性上线前的记录，口径未知。
-- 【严禁回填】——回填即伪造历史口径。

ALTER TABLE audit_history
    ADD COLUMN skipped_objects INT NULL DEFAULT NULL
        COMMENT '提取阶段跳过对象总数；NULL=本特性上线前的记录';
ALTER TABLE audit_history
    ADD COLUMN skipped_benign INT NULL DEFAULT NULL
        COMMENT '其中良性跳过（TDSQL 物理分片子表）';
ALTER TABLE audit_history
    ADD COLUMN skipped_abnormal INT NULL DEFAULT NULL
        COMMENT '其中异常跳过（权限/超时/Proxy 故障等，需人工核实）';
ALTER TABLE audit_history
    ADD COLUMN omitted_results INT NULL DEFAULT NULL
        COMMENT 'results_json 因超包限压缩而省略的通过项条数；0=未压缩，NULL=上线前记录';
```

**两个坑**：

1. 列类型写 `INT`，**不要写 `INT(11)`**。migrator 每次启动对已登记迁移做结构验收
   （`backend/schema/migrator.py:141 _verify_column`），MySQL 8 回 `int`，
   写成 `int(11)` 会在生产直接启动失败。
2. `backend/services/database.py:869` 的 `audit_history` 基线 DDL **不要动**。
   本项目既有列（`instance_type` / `report_context_json` / `skipped_rules_count`）
   全部只靠迁移 ALTER 加，基线保持原样——照这个来。

### 2.3 publish 契约：21 列 → 25 列

- 四个新列**追加在末尾**，`results_json` 仍在索引 8 —— 保住 `publish()` 里
  `compacted if i == 8 else v` 的既有假设，不要重排。
- `metadata_audit_repository.py:422` 的 INSERT 列表与占位符同步加 4 个。
- `metadata_audit_worker.py:206` 的 `audit_cols` 末尾追加
  `stats.get("skipped_objects")`、`stats.get("skipped_benign")`、
  `stats.get("skipped_abnormal")`、`omitted`。
- **`backend/services/audit_service.py:76` 那个写入方不要动**——它是离线文件审核，
  没有"跳过"概念，四个新列取 NULL 即可（语义正确）。

### 2.4 `omitted_results` 的来源

`compact_results_for_audit_history()` 现在只把 `omitted` 写进日志就丢了。
改为返回 `(compacted_json, omitted_count)`，由 `publish()` 取到后写进第 25 列；
未触发压缩时返回 `0`（**不是 NULL**——0 表示"确认无省略"，NULL 表示"上线前不知道"）。

### 2.5 四个呈现面，一个都不能少

| # | 面 | 位置 | 要求 |
|---|---|---|---|
| P1 | 历史报告 HTML | `backend/api/sql_audit.py:436` 路由，第 448 行读 results_json 处 | 报告**顶部**加告警条：`skipped_abnormal>0` 时红色「本次有 N 个对象未能读取 DDL，未纳入审核，请人工核实」；`omitted_results>0` 时橙色「明细为节选，已省略 N 条通过项，完整明细见任务产物」。指标区补「跳过 N（良性 A / 异常 B）」 |
| P2 | SQL 文件下载 | `backend/api/sql_audit.py:516` 路由，第 528 行 | `omitted_results>0` 时在文件**开头**插入注释块，写明本文件为节选、省略条数、完整文件路径；`skipped_abnormal>0` 时同样写明未读取到 DDL 的对象数 |
| P3 | 历史记录列表 | API：`sql_audit.py:310-314` 的 SELECT 加 4 列；前端：`frontend/index.html:453` 一带加列 | 「对象数」列旁加「跳过」列；异常跳过 >0 显示橙色角标；节选记录在「操作」列前加「节选」标签 |
| P4 | 运行中任务面板 | `frontend/index.html:393` | **已有**，保持；把「跳过 N」细化为「跳过 N（异常 B）」，与 P1/P3 口径一致 |

`artifacts/report.html` 的口径行（`metadata_audit_worker.py:105`）Q 已经做了，保持。

### 2.6 异常跳过的告警阈值

沿用 Q 已写的 `abnormal > 50 或 abnormal > selected * 5%`，不改。
超阈值时除现有 `logger.warning` 外，P1 的告警条**必须**出现（不依赖阈值，`>0` 即显示；
阈值只控制日志级别与措辞强度）。

### 2.7 本条必须补的锁

| 锁 | 断言 | 变异体 |
|---|---|---|
| L7 | 迁移后 `audit_history` 四个新列存在且类型为 `int` | 改成 `INT(11)` → 结构验收失败 |
| L8 | `publish()` 把四个计数真写进对应列（读回校验） | 列顺序错排 / 漏传 |
| L9 | 压缩触发时 `omitted_results` == 实际省略条数；未压缩时 == 0 | 返回值丢掉 omitted |
| L10 | 离线文件审核（audit_service）写入后四列为 NULL | 误改 audit_service |
| L11 | `skipped_abnormal>0` 的报告，P1 HTML 含告警条文案 | 去掉告警条 |
| L12 | `omitted_results>0` 的报告，P2 SQL 文件首部含节选说明 | 去掉文件头注释 |

---

## 3. 一并清掉第二轮遗留

| 编号 | 事项 | 改法 |
|---|---|---|
| R2-M-01 | 阈值锁假绿（变异 N3 存活） | `test_adaptive_threshold_no_compact_at_64mb` 改为**真调 `publish()`**：造 42MiB 载荷，断言落库后 `results_json` 条数 == 原始条数、`omitted_results == 0`。**不许在用例里自己算阈值再喂进去** |
| R2-M-04 | 用例与元数据库包限耦合（16MiB 下变红） | 用例先读 `@@session.max_allowed_packet` 反算载荷大小；包限不足以构造区分带时 `pytest.skip("元数据库 max_allowed_packet=%d，不足以构造区分带")`。**不要把 64MiB 写死进假设** |
| R2-N-01/N2 | 预检系数 1.25 无锁 | 补：改成 1.0 后，某个刚好卡在区分带的载荷应从"拦"变"放"，用例变红 |
| R2-N-01/N4 | 列索引锁打不响 | 列位校验必须在**触发压缩**的路径上做——造超阈值载荷，读回确认 `results_json` 是 list 且 `pass_rate` 列未被污染 |
| R2-N-01/N9 | rollback 兜底无锁 | 注入 INSERT 抛 1153 + rollback 抛 2006，断言抛出的是 `MetadataJobError` 且消息含 1153 |
| R2-N-01/N10 | Object Name sanitize 无锁 | 含 CR/LF 的对象名，断言产出行全部仍以 `--` 开头 |
| R2-N-01/N12 | report.html 口径行无锁 | 断言口径行含「跳过」与「异常」字样 |
| R2-N-02 | 超阈值仍静默节选 | 由 §2.4 + P1/P2 覆盖，**本条随 R2-M-05 一并关闭** |

另外把 `metadata_audit_pipeline.py:44` 那句「由提取循环的 try/except 兜底」删掉——
取消前置过滤后它才真正成立，但那时也不必再写了。

---

## 4. 我第三轮会这么验（提前公开判据）

1. **零误杀硬指标**：名字匹配子表模式、但 `SHOW CREATE` 能成功的表，必须进 extracted。
   这一条现在是可判定的，不再依赖命名规则的"分辨力"。
2. **真实产物回放**：`extracted_lzbj_ecif_20260910_224104.sql` 的 219 个对象，误杀仍须为 0。
3. **12 条新锁逐条变异**：L1–L12 每条都要有能杀死它的变异体；存活即不通过。
4. **R2-M-01/M-04 复验**：N3 变异必须变红；包限 16MiB 下全套必须绿（靠 skip 而非靠碰运气）。
5. **迁移双向**：全新库建表 + 存量库升级两条路径都要过 migrator 结构验收；
   存量记录四列须为 NULL（严禁回填）。
6. **回归**：与整改前提交做受控对比，失败/错误节点集合必须逐条一致。
7. **开销回归**：取消前置过滤后，大库提取阶段耗时增幅须在我 §1.1 的外推范围内；
   超出 30 倍下界（6000 表库 >10.6 s）要给出解释。

---

*提出人：智能体A（-ClaudeA）　送呈：Mr.Linsang　施工：Q*
