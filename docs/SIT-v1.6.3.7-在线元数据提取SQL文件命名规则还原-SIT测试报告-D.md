# v1.6.3.7 在线元数据提取 SQL 文件命名规则还原 · SIT 测试报告（智能体D）

| 项 | 内容 |
|---|---|
| 被测对象 | **工作区未提交的 G 实现**（`backend/workers/metadata_audit_worker.py` 命名还原、版本标记、时区口径变更、`deploy/patch_v1637_source.sql`、`tests/test_v1637_filename_rule.py`） |
| 设计依据 | `docs/DETAIL-v1.6.3.7-在线元数据提取SQL文件命名规则还原详细设计说明书.md`（Rev.A） |
| 问题来源 | 内网生产 `10.243.16.238:8000` 升级 v1.6.3.6 后，历史记录中提取 SQL 文件名由 `库名_时间戳` 变为 `库名_UUID前8位`（报告 #1377） |
| 测试类型 | **SIT（系统集成测试）**：设计-施工一致性核对 + 端到端行为验证 + 边界用例 + 独立变异验证 |
| 测试方 | **智能体D**（未改动任何产品代码；变异脚本字节级改写在 `finally` 中原样恢复） |
| 测试日期 | 2026-09-12 |
| 证据目录 | `docs/evidence/v1.6.3.7-sit-d/` |
| 提交给 | Mr.Linsang |

---

## 1. 结论

**命名还原主体（设计 §3.1/§3.2 改造点 1）SIT 通过**：文件名已恢复为
`extracted_{库名}_{YYYYMMDD_HHMMSS}.sql`，在**五个消费面**上表现一致，无 UUID 残留；
版本标记、回归均正常。但施工**超出设计范围**并引入一处**未评估的存量口径变更**，
且 G 的时区锁经独立变异验证为**假绿锁**，另有数据订正脚本的三处工程问题。**共 6 项待改**
（2 项 MAJOR、2 项 MINOR、2 项 NIT/观察），均给出照图施工级方案。

| 维度 | 结论 |
|---|---|
| 命名还原（核心诉求） | ✅ **通过**（5 个消费面 + 边界用例） |
| 版本标记一致性 | ✅ 通过（VERSION / config / 前端 5 处 + 版本一致性用例 4 passed） |
| 设计-施工一致性 | ⚠️ **6 处施工超设计**（其中时区口径变更与报告ID列需补设计/产品确认） |
| 回归锁有效性（独立变异） | ⚠️ **4 中 3 有效、1 假绿**（F1） |
| 全量回归 | ✅ **2145 passed / 0 failed / 0 errors / 30 skipped** |
| 数据订正脚本 | ⚠️ 3 项问题（F3） |
| **放行意见** | **命名还原可放行；F1/F2/F3 建议在发布前闭合**（F1、F3 成本极低） |

---

## 2. 设计-施工一致性核对

| 设计条目 | 实现情况 | 结论 |
|---|---|---|
| §3.2 改造点 1：worker 命名还原为 `extracted_{db}_{now_ts}.sql` | 已实现（`metadata_audit_worker.py:149-150`） | ✅ 一致 |
| §3.2 改造点 2：`VERSION`/`config.py`/`index.html` 5 处 → 1.6.3.7 | 已实现 | ✅ 一致 |
| §5.1：新增 `tests/test_v1637_filename_rule.py` 四道锁 | 已实现（L1~L4） | ✅ 一致 |
| §4.2：交付订正 SQL **与回滚 SQL** | 只有 `deploy/patch_v1637_source.sql`，**无回滚脚本文件** | ⚠️ F3-2 |
| §4.1：订正"按其 `created_at` **自动计算**" | 实现为**硬编码** `id=1377` + 硬编码目标名 | ⚠️ F3-1 |
| §6.1 变更清单（5 项） | 实际另有 4 项未列入：**`audit_history.created_at` UTC→本地**、`sql_audit.py` 的 `ORDER BY ... , id DESC`、前端新增「报告ID」列、`deploy/make_patch.py` 文档清单 | ⚠️ F2 / F6 |

---

## 3. 功能验证：命名在五个消费面的表现（`sit-naming.json`、`sit-surfaces.json`）

真实任务 `21a461e9…`（库 `uat_d_1636_dist`，分布式）：

| # | 消费面 | 实测 | 结论 |
|---|---|---|---|
| 1 | `audit_history.source` | `extracted_uat_d_1636_dist_20260912_151849.sql` | ✅ 符合 `^extracted_.+_\d{8}_\d{6}\.sql$` |
| 2 | 是否含 job_id 片段 | `false` | ✅ 无语义哈希残留 |
| 3 | `scan_snapshots.scan_label` | 与 `source` **完全相同** | ✅ |
| 4 | 历史 SQL 下载 `Content-Disposition` | `attachment; filename=extracted_uat_d_1636_dist_20260912_153000.sql` | ✅ 与 `source` 一致 |
| 5 | 历史 HTML 报告抬头 | 展示该文件名；**无** `_<8位十六进制>.sql` 形态 | ✅ |
| 附 | 前端历史列表（真实浏览器） | 5 条记录**全部**为时间戳命名、`any_uuid_form=false`；「报告ID」列已出现（#31 等） | ✅ |

**时间戳口径**：文件名时间戳与服务器本地时间差 **0.3 s**；SQL 文件头 `-- 提取日期: 2026-09-12 15:18:50`
与文件名 `..._151849` 相差 **1 秒**（文件名在 `cas_state` 之前生成，头注释在 `extract_metadata` 内生成）。
属正常工程时序，**但设计 §3.1 声称"秒级完全对齐"表述过强**（见 F5-3）。

---

## 4. 边界用例

| 用例 | 构造 | 实测 | 结论 |
|---|---|---|---|
| 超长库名 | MySQL 库名上限 **64 字符** | 文件名 94 字符，`source` 完整落库、格式合规 | ✅ 通过 |
| 列宽安全性（分析） | `audit_history.source` = **TEXT**；`scan_snapshots.scan_label` = **VARCHAR(512)** | 最坏 94 字符 ≪ 512 | ✅ 结构性安全 |
| 同秒同名 | 冻结时钟使两次任务同秒 | 两条记录 `source` **完全相同**（`..._20260912_153000.sql`） | ⚠️ F4（与历史契约一致，非回归） |

---

## 5. 时区口径变更专项（F1 + F2）

### 5.1 变更内容

施工把 `audit_history.created_at` 的写入由 `_utcnow()`（UTC）改为 `_local_now_str()`（本地时间），
并同步把 `scan_started_at/scan_finished_at` 改为本地；**设计文档未包含此变更**。

### 5.2 动机是成立的（值得肯定）

后端在 v1.6.3.5/v1.6.3.6 期间用 UTC 写 `created_at`，导致历史列表"提取时间"比操作时间**早 8 小时**
（实测：`extracted_lzbj_ecif_20260910_224104.sql` 对应记录若由 worker 写入即会错位）。若不改，
"还原后的文件名（本地时间）"与"列表时间（UTC）"会当场相差 8 小时。

### 5.3 但存量评估缺失（F2，对应施工规约 R-13）

- 存量：v1.6.3.5/v1.6.3.6 期间 worker 写入的记录 `created_at` 仍是 UTC，**可用 `source` 形态精确识别**
  （`^extracted_.+_[0-9a-f]{8}\.sql$`，因为那两版正是 UUID 命名）；
- 后果：升级后**同列混用两种时基** → 这些历史记录在列表里显示的时间偏早 8 小时，按日期筛选会漏/错；
- **实测佐证**（`sit-browser.json` 行数据）：SIT 缺陷态复现产生的记录
  `extracted_uat_d_1636_dist_20260912_152048.sql` 在页面上显示"提取时间 **2026-09-12 07:20**"
  —— **同一条记录文件名 15:20、列表 07:20**，正是存量记录的显示语义。

### 5.4 G 的时区锁是假绿锁（F1，已确证）

变异 `MC`：把 worker 落库时间回退为 `_utcnow()`（即缺陷态）后：

| 观测 | 结果 |
|---|---|
| `tests/test_v1637_filename_rule.py` + `test_v1636_bugs.py` + `test_v1635_uat3.py` + `test_v1635_metadata_jobs.py` | **46 passed（全绿，无一变红）** |
| 真实任务落库 | `source = ..._20260912_152048.sql`（本地 15:20:48）而 `created_at = 2026-09-12T07:20:49` → **错位 −8.0 小时**，SQL 头注释为 15:20:49（本地） |

**根因**：`test_metadata_audit_history_created_at_is_local_time` 只做了两件事——
① 自检 `_local_now_str()` 与 `datetime.now()` 一致（**只测 helper 本身**）；
② 由**测试自己** INSERT 一条记录再按日期查询（**没走 worker 的 publish 路径**）。
因此它锁不住"worker 是否真的用本地时间落库"。

---

## 6. 数据订正脚本审查（F3）

`deploy/patch_v1637_source.sql` 现状：

| 问题 | 说明 |
|---|---|
| F3-1 覆盖不全 | 第 1 步 SELECT 用通配符 `source LIKE 'extracted_%_a27123e1.sql'`，第 2 步 UPDATE 却用**硬编码** `id = 1377 AND source = 'extracted_lzbj_ecif_a27123e1.sql'` → v1.6.3.5/v1.6.3.6 期间**任何**在线任务都是 UUID 形态，其余污染记录不会被订正；且目标名硬编码，与设计 §4.1"按其 `created_at` 自动计算"不符 |
| F3-2 缺回滚脚本 | 设计 §4.2 要求同时交付 `rollback_v1637_source.sql`，仓库内缺失（`deploy/rollback.sh` 是部署回滚脚本，非数据回滚） |
| F3-3 未覆盖时区存量 | 与 F2 同源：脚本只改 `source`/`scan_label`，未处理存量 `created_at` 的时基不一致 |

---

## 7. 回归与锁验证

| 项 | 结果 |
|---|---|
| 全量回归（专用回归库 `uat_d_1636_regression`） | ✅ **2145 passed / 0 failed / 0 errors / 30 skipped（483.49s）** |
| G 的四道命名锁 | ✅ 4 passed |
| 版本一致性用例 | ✅ 4 passed |
| 独立变异 **MA**（命名回退为 UUID） | ✅ 变红 |
| 独立变异 **MB**（时间戳改用 UTC） | ✅ 变红 |
| 独立变异 **MD**（`file_path` 与落库 `source` 解耦） | ✅ 变红 |
| 独立变异 **MC**（落库时间回退 UTC） | ❌ **未变红** → F1 假绿锁 |

证据：`sit-mutation.json`、`sit-mc-gap.json`。

---

## 8. 问题清单与照图施工级整改方案

### F1（MAJOR）时区锁假绿，锁不住真实落库路径

**补锁（照图施工）**——把"worker 是否用本地时间落库"变成可被变异杀死的断言：

```python
# tests/test_v1637_filename_rule.py 追加
def test_l5_publish_created_at_is_local_time(monkeypatch, tmp_path):
    """L5：worker 落库的 created_at 必须是本地时间（与文件名时间戳同一时基）。

    MC 变异（改回 _utcnow()）必须使本用例变红。
    """
    from datetime import datetime, timezone
    from backend.workers import metadata_audit_worker as W
    from backend.services import metadata_audit_repository as RR, metadata_artifacts as art
    import backend.services.connection_registry as CRG
    import backend.services.metadata_audit_pipeline as P

    captured = {}
    monkeypatch.setattr(RR.repository, "get_job", lambda jid: {
        "id": "j", "attempt_token": "t", "state": RR.STATE_RUNNING,
        "execution_context_json": "{}", "request_json": '{"scopes":["TABLE"]}',
        "connection_id": "c", "db_name": "order_db", "created_by": "u"})
    monkeypatch.setattr(RR.repository, "cas_state", lambda *a, **k: None)
    monkeypatch.setattr(RR.repository, "update_progress", lambda *a, **k: None)
    monkeypatch.setattr(RR.repository, "publish",
                        lambda jid, tok, audit_columns_values=None, results_json="":
                        captured.setdefault("cols", audit_columns_values) or 1)
    monkeypatch.setattr(CRG.registry, "get", lambda cid: object())
    monkeypatch.setattr(P, "extract_metadata", lambda *a, **k: (["-- h"], {}))
    monkeypatch.setattr(P, "audit_streaming", lambda *a, **k: iter(()))
    monkeypatch.setattr(art, "job_dir", lambda jid: tmp_path)

    W.run("j", "t")
    cols = captured["cols"]                     # audit_cols：索引 13 = created_at
    src, created = cols[1], cols[13]            # 索引 1 = source
    ts = datetime.strptime(src.split("_")[-1].replace(".sql", ""), "%Y%m%d_%H%M%S")
    dt = datetime.strptime(created, "%Y-%m-%d %H:%M:%S")
    # 本地时间口径：与文件名时间戳相差 ≤2s；且不得等于 UTC（东八区下会差 8h）
    assert abs((dt - ts).total_seconds()) <= 2, f"created_at({created}) 与文件名({src}) 不同时基"
    assert abs((datetime.now(timezone.utc).replace(tzinfo=None) - dt).total_seconds()) > 60, \
        "created_at 疑似 UTC（应为本地时间）"
```

**验收**：施加 MC 变异 → 该用例变红；恢复后全绿（须给出红/绿记录）。

### F2（MAJOR）时区口径变更：补设计 + 存量处置（R-13）

1. **补设计**：把该变更写入 `DETAIL-v1.6.3.7` §3.2（新增改造点）与 §6.1 变更清单，含动机
   （消除"文件名本地时间 vs 列表 UTC"的 8 小时错位）与影响面。
2. **存量处置二选一（须显式决策并留痕）**：
   - **方案 A（推荐）一次性订正**：把 v1.6.3.5/v1.6.3.6 期间 worker 写入的记录 `created_at`
     加上服务器时区偏移（**动态取偏移，勿写死 8**），并按订正后的时间同步重算 `source`；
   - **方案 B 不订正**：在发布说明中声明"该期间记录的时间显示偏早一个时区"，并在历史页面
     对这批记录加提示（成本更高，且用户已明确在意时间语义，不建议）。
3. **防再犯**：见 F1 的 L5 锁。

### F3（MINOR→建议按 MAJOR 对待，因涉及生产数据）订正脚本通用化 + 回滚脚本

```sql
-- deploy/patch_v1637_source.sql（通用化、幂等、可 dry-run）
-- 步骤 1：预览（dry-run）——精确圈定 UUID 形态的在线元数据记录
SELECT id, db_name, source, created_at
  FROM audit_history
 WHERE audit_type = 'extracted_schema'
   AND source REGEXP '^extracted_.+_[0-9a-f]{8}\\.sql$';

-- 步骤 2：订正文件名（用记录自身 created_at + 会话时区偏移重算；先订正时区再订正名字见步骤 3）
SET @tz_offset := TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(), NOW());

UPDATE audit_history
   SET source = CONCAT('extracted_', db_name, '_',
                       DATE_FORMAT(DATE_ADD(created_at, INTERVAL @tz_offset SECOND),
                                   '%Y%m%d_%H%M%S'), '.sql')
 WHERE audit_type = 'extracted_schema'
   AND source REGEXP '^extracted_.+_[0-9a-f]{8}\\.sql$';

-- 步骤 3（与 F2 方案 A 合并执行）：存量时区订正
UPDATE audit_history
   SET created_at = DATE_ADD(created_at, INTERVAL @tz_offset SECOND)
 WHERE audit_type = 'extracted_schema'
   AND source REGEXP '^extracted_.+_[0-9]{8}_[0-9]{6}\\.sql$'
   AND created_at < DATE_SUB(NOW(), INTERVAL 1 DAY);   -- 仅订正升级前存量，避免误伤新记录

-- 步骤 4：同步快照标签
UPDATE scan_snapshots s
  JOIN audit_history h ON h.id = s.biz_ref_id
   SET s.scan_label = h.source
 WHERE s.scan_label <> h.source;
```

**回滚脚本**（按设计 §4.2 交付 `deploy/rollback_v1637_source.sql`）：以执行前导出的
`(id, 原 source, 原 created_at)` 快照表为准做等值回写；建议订正脚本第 1 步先
`CREATE TABLE audit_history_v1637_backup AS SELECT id, source, created_at FROM ...`，回滚即按备份表还原。

### F4（MINOR）同秒同名

与历史契约一致（旧同步路径同样如此），磁盘产物按 `job_id` 隔离，**不构成功能缺陷**。
建议二选一：① 在用户手册注明"同库同秒的两次提取文件名相同，请以报告ID/提取时间区分"；
② 后续版本在**检测到同名时**追加序号（`_20260912_153000_2.sql`），保持既有契约不变。

### F5（NIT）

1. `metadata_audit_worker._utcnow()` 已成为**死代码**，建议删除或注明保留原因；
2. 新增时区用例放在 `tests/test_v1636_bugs.py`（v1.6.3.6 套件）中，与 v1.6.3.7 变更不同批，
   建议移入 `tests/test_v1637_filename_rule.py`（或新建 `test_v1637_tz.py`）；
3. 设计 §3.1"秒级**完全**对齐"实测存在 1 秒差，建议改为"秒级一致（允许 ≤2s）"，避免后续被当缺陷追。

### F6（观察）其余施工超设计项

`sql_audit.py` 的 `ORDER BY h.created_at DESC, h.id DESC`、前端新增「报告ID」列、
`deploy/make_patch.py` 的文档清单：均未写入设计 §6.1 变更清单。
其中 **`id DESC` tiebreaker 是必要的**（本次同秒同名用例正说明同秒记录排序需要稳定键），
建议补进设计并保留；「报告ID」列属 UI 增量，请产品侧确认是否随本版保留。

---

## 9. 证据索引

目录 `docs/evidence/v1.6.3.7-sit-d/`：

| 文件 | 内容 |
|---|---|
| `sit_d37_probe.py` | 探针：`env` / `naming` / `edge_dup` / `edge_long` / `mixed_tz` |
| `sit_d37_surfaces.py` | 下载与报告抬头（HTTP）核验 |
| `sit_d37_browser.py` | 历史列表真实浏览器核验（含「报告ID」列与按当天筛选） |
| `sit_d37_mutation.py` | 四道命名锁的独立变异验证（MA/MB/MC/MD） |
| `sit_d37_mc_gap.py` | F1 假绿锁的定向确证（缺陷态下 46 例全绿 + 真实落库 −8h 错位） |
| `sit-env.json` / `sit-naming.json` / `sit-surfaces.json` / `sit-edge-dup.json` / `sit-edge-long.json` / `sit-mixed-tz.json` / `sit-mutation.json` / `sit-mc-gap.json` / `sit-browser.json` | 结果数据 |
| `sit-history-list.png` / `sit-history-filter-today.png` | 页面截图 |
| `data/reports/uat_d_1636/sit-v1637-regression.xml` | 全量回归产物 |

---

## 10. 放行意见

1. **命名还原本体可以放行**：核心诉求（恢复"库名 + 提取操作时间"命名、拒绝无语义哈希）在五个消费面
   与边界用例上均已验证通过，回归零失败零错误。
2. **发布前建议闭合 3 项**：
   - **F1**（补一条能杀死 MC 的锁，成本 ≈ 20 行用例）；
   - **F2**（把时区口径变更补进设计 + 对存量做一次性订正或显式声明）；
   - **F3**（订正脚本通用化 + 补回滚脚本；当前脚本只改得到 #1377 一条）。
   这三项都直指"用户可见的时间语义"，而本次问题的起因正是命名/时间语义被擅自更改——
   建议不要让同一类问题以另一种形式留在版本里。
3. F4/F5/F6 可在本版或下一版处理，不阻断。
4. **内网真机验证仍不可替代**：本报告结论基于本机 MySQL 8.0 + 合成库；生产库（`lzbj_ecif` 等）
   的存量订正须在生产窗口按 §8-F3 脚本 dry-run → 备份 → 执行 → 核验的流程进行。

---

测试责任方：**智能体D**
提交给：Mr.Linsang
