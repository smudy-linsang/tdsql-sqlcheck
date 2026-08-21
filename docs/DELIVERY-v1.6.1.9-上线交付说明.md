# v1.6.1.9 上线交付说明

| 项 | 内容 |
|---|---|
| 版本 | v1.6.1.9 |
| 主题 | R077/R054 TDSQL 分片表与广播表建表语法识别缺陷修复 |
| 缺陷等级 | P0（核心能力误报） |
| 影响范围 | 在线元数据审核（`audit/extract-and-audit`）中对含 `TDSQL_DISTRIBUTED BY HASH(col)` 或 `shardkey=noshardkey_allset` 的建表语句的 R077/R054 判定 |
| 修复提交 | `c9e1e3c` |
| 部署说明 | 见《V1.6.1.9 增量更新部署说明》 |

---

## 一、修复了什么

生产环境中，TDSQL 内核 `SHOW CREATE TABLE` 输出的两种合规建表语法在规则引擎中从未被识别：

| 表类型 | 语法形态 | 改前行为 | 改后行为 |
|---|---|---|---|
| HASH 分片表 | `TDSQL_DISTRIBUTED BY HASH(col)` | R077 误报（"未声明分片键"） | 正确识别为分片表 |
| 广播表（全局表） | `shardkey=noshardkey_allset` | R077+R054 双误报（哨兵当列名） | 正确识别为广播表 |

---

## 二、必须向用户明示的四条

### 1. `#3` 显示"零违规" ≠ 通过了全量审核

`TDSQL_DISTRIBUTED BY HASH(...)` 会导致 **sqlglot 将整条 DDL 降级为 Command**，`columns` / `indexes` / `table_options` 全部为空。这意味着该表的 **R036（缺时间戳字段）、R037（缺逻辑删除字段）、R061（索引命名规范）等结构类规则仍被漏审**。

本次修复只消除了 R077/R054 的误报，不改变 sqlglot 降级的事实。该表的审核报告应继续人工关注上述规则的覆盖情况。根治需 Phase 2（ADJ-1）。

### 2. 本次有两处净收紧，可能新增 R054 WARNING

| 收紧点 | 说明 | 实测影响 |
|---|---|---|
| **FIX-3b**：R054 唯一索引判定支持反引号索引名 | 改前 `_UNIQUE_RE` 的 `\w*` 不匹配反引号索引名（如 `` `uk_code` ``），改后支持。反引号命名的唯一索引若不含分片键，将**新增** R054 WARNING | 既有语料影响 **0 条** |
| **FIX-5**：R054 空主键集合判 J-2 失败 | 改前 `if pk_cols and ...` 会把"根本没有主键"当作无需检查，改后空主键集合也判为失败 | 既有语料影响 **0 条** |

两处均为 **WARNING 级**，不阻断质量门禁。

### 3. HASH 语法分片表在 R116/R117/R118 侧仍未覆盖

`oracle_compat.py` 的 R116（分片键类型限制）、R117（分片键长度限制）、R118（分片键 NOT NULL）三条规则使用独立的 `_RE_SHARDKEY` / `_RE_SHARDKEY_SINGLE` 正则提取分片键，**只认 `shardkey=` 形态，看不见 `TDSQL_DISTRIBUTED BY HASH`**。

这意味着同一张表用 HASH 语法写就逃过这三条规则，用 legacy `shardkey=` 语法写则被审。属于**漏审**（非本次用户报告的误报），已记录为 **ADJ-7**，留待 Phase 2 处理。

### 4. 邻接缺陷清单（留 Phase 2）

| 编号 | 问题 | 优先级 |
|---|---|---|
| **ADJ-1** | `TDSQL_DISTRIBUTED BY HASH` 导致 sqlglot 降级，结构类规则漏审 | Phase 2 |
| **ADJ-2** | `tdsql_connector.py` 只认 `SHARDKEY=`，不识别 `noshardkey_allset` | Phase 2 |
| **ADJ-3** | `tdsql_connector.py:1546` 引用未定义变量 `create_sql_upper`，`is_broadcast_table` 永不为 True（**真实静默失效，建议最高优先级**） | Phase 2 |
| **ADJ-7** | R116/R117/R118 看不见 HASH 语法 | Phase 2 |
| **ADJ-8** | `oracle_compat.clean_sql()` 的 `--` 词法缺陷（`a--b` 被误当注释截断） | Phase 2 |

已关闭：ADJ-4（R077 宽松判定，用户决策永久关闭）、ADJ-6（BROADCAST 冲突，用户决策不改动）。

---

## 三、验证证据

| 验证项 | 结果 |
|---|---|
| 41 条验收用例 + 4 条补充测试 | **45/45 通过** |
| 全量回归 `pytest tests/` | **1358 passed / 0 failed / 0 skipped** |
| 双侧对比漂移扫描 | **13 条变化全部解释**（9 条生产 fixture + 4 条 FIX-4 附带修复），异常 0 |
| 规则总数 | 119（all=92, distributed=27），不变 |
| 施工检查单 | 全部通过 |

---

## 四、回滚

单文件纯增量，`git revert c9e1e3c` 即回到 v1.6.1.8 行为。无数据/schema/接口残留。

---

*交付人：智能体 Q*
*交付日期：2026-08-22*
