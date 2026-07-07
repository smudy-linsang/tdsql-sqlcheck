# Oracle迁移TDSQL规范接入 — 最终质检验收报告

> 验收对象：commits `a0bfb87` + `f619c0b`（backend/engine/rules/oracle_compat.py 978 行 + 8 文件修改）
> 验收基线：`docs/Oracle迁移TDSQL规范接入审核规则库设计说明书.md`（第 10 章验收清单）
> 验收方式：**全部实测**——本地起 MariaDB(13306) + uvicorn(8000)，184 项规则级检查逐条执行、5 审核入口 E2E、规则集/门禁联动、性能基准、820+ 全量回归
> 验收日期：2026-07-07
> **验收结论：不通过（有条件）。引擎核心质量很高，但存在 2 个引擎缺陷 + 测试/文档工作未做，整改点位明确、工作量小，修完即可准出。**

---

## 一、总体评价

先说好的：这是几轮交付里**引擎代码质量最高的一次**。42 条规则（R078–R119）ID 连续无缺、分类/级别/spec_source/fix_suggestion 与设计逐条一致；49 处正则全部模块级预编译、39 处过 clean_sql 清洗、AST 全部判空——设计附录 B 三条铁律基本执行到位。**我按设计第 5 章 42 组正反示例 + 12 项防误报专项共 184 项逐条实测，183 项通过**；5 个审核入口全部自动生效；规则集禁用覆盖、门禁阻断联动实测生效；性能 0.60ms/条，远优于红线。

但仍不能准出，因为：**1 个设计明确要求的军规是死代码、1 个高频误报会打击银行最常见的合规建表、设计第 9 章的测试工作完全没做**——这直接导致全量回归 34 个失败，与"复测全部通过准出"的说法不符（谁跑一遍 `pytest tests/` 都会看到）。

## 二、验收清单执行结果（对照设计第 10 章）

| 验收项 | 结果 | 实测证据 |
|---|:-:|---|
| /rules 返回 total=119，oracle_compat 42 条，元数据完整 | ✅ | 实测 119/42，R078–R119 无缺，42 条 spec_source 均含原厂文档章节 |
| 42 条规则 ✗ 命中 / ✓ 通过逐条实测 | ✅ 183/184 | 唯一失败为 E2（见缺陷2），42 条新规则本体全部通过 |
| 防误报专项（字面量/注释/TRUNCATE/char_length/IN(/VALUES(/dual…） | ✅ 12/12 | 零误报 |
| 级别与设计 4.4 一致（ERROR33/WARNING8/INFO1） | ✅ | 42 条逐条比对通过 |
| 5 审核入口 E2E | ✅ 4+1 | sql/file/upload/gitlab-diff 实测命中 R080/R082/R079/R085/R081；with-metadata 需真实 TDSQL 连接（沙箱无），已用进程内 table_metadata 路径等效验证 |
| 规则集覆盖生效 | ✅ | 建规则集禁用 R080 → 绑项目审核 nvl → R080 不出现 |
| 门禁联动 | ✅ | max_error=0 + decode SQL → R081 命中且 gate_result.passed=false |
| E1 GTT 识别 | ✅ | `CREATE GLOBAL TEMPORARY TABLE … ON COMMIT DELETE ROWS` 命中 R024 |
| E2 R054 唯一索引扩展 | ❌ | **死代码，两条路径均不触发**（缺陷2） |
| E3/E4/E5/E6（文案/口径/前端分类） | ✅ | 广播表文案、R115 提示、APP_VERSION=2.1.0、docstring 119、categoryOrder 已加 oracle_compat |
| 性能红线 | ✅ | 1000 条混合 SQL 0.60s，0.60ms/条 |
| 测试断言更新 + 新增单测 + pytest 全绿 | ❌ | **完全未做**（缺陷3），全量回归 34 failed / 717 passed |
| README/docs 口径 77→119 | ❌ | 4 处未改（缺陷4） |

## 三、整改清单

### 🔴 缺陷1（严重）：R117 把 `BIGINT UNSIGNED` 分片键误报为非法类型

- **现象**：`CREATE TABLE t_order (id BIGINT UNSIGNED NOT NULL … PRIMARY KEY …) SHARDKEY=id` → 误报 `R117: shardkey字段 id 类型为UBIGINT，不在许可类型内`。
- **根因**：sqlglot 把无符号整型规范化为 `UBIGINT/UINT/USMALLINT/UMEDIUMINT/UTINYINT`，R117 的许可类型集合没有覆盖无符号变体。
- **影响**：银行建表主键普遍是 `BIGINT UNSIGNED`，此误报会命中大量**完全合规**的 DDL（存量 5 个"合规DDL"测试因此失败：test_rules/test_sit_full/test_sit_round2/test_sit_round3/test_uat_round1 各 1 个）。
- **修复**（backend/engine/rules/oracle_compat.py R117）：类型判断前做归一化——`t = col_type.upper().lstrip('U') if col_type.upper() in {'UBIGINT','UINT','USMALLINT','UMEDIUMINT','UTINYINT'} else col_type.upper()`，或直接把这 5 个无符号名加入许可集合（注意 `UINT→INT`、含 `INTEGER`）。修完用上面那条 t_order DDL 自测必须通过。

### 🔴 缺陷2（严重）：E2「唯一索引必须包含分片键」是死代码，从不触发

- **现象**：`create table t (uid bigint not null, c varchar(20) not null, primary key (uid), unique key uk_c (c)) shardkey=uid` —— uk_c 不含分片键，**任何路径都不报 R054**（原厂军规：唯一索引不含分片键将无法建表）。
- **根因（两层）**：
  1. **解析层**：sqlglot 把表级 `UNIQUE KEY uk_c (c)` 解析为 `exp.UniqueColumnConstraint` 节点，而 `parser.py _parse_create` 只处理 `exp.IndexColumnConstraint` → `parsed.indexes`/`index_definitions` 里**永远没有 UNIQUE 索引** → R054 新增的两个 UNIQUE 遍历循环遍历空列表。
  2. **规则层**：R054 开头 `if not parsed.is_create_table or not table_metadata: return None` 强依赖元数据；设计 E2 明确要求"**DDL 场景从 raw_sql 提取 `shardkey=xxx`**"作为回退，未实现。且含 `shardkey=` 的建表语句 sqlglot 会整体解析失败（fallback Command），`is_create_table` 为 False，连元数据路径都进不去。
- **修复**：
  1. `parser.py _parse_create`：增加 `elif isinstance(col_def, exp.UniqueColumnConstraint):` 分支——从 `col_def.this`（IndexColumnConstraint 或 Schema 形态，打印节点确认）提取索引名与列列表，`type="UNIQUE"`，追加进 `parsed.indexes` 与 `index_definitions`。
  2. `R054.check`：shardkey 获取顺序改为 `table_metadata → raw_sql 正则 re.search(r"shardkey\s*=\s*['\"\`]?(\w+)", raw_sql, re.I)`；`is_create_table` 为 False 但 raw_sql 匹配 `^\s*create\s+table` 且含 `shardkey=` 时也要进入检查（参照 f619c0b 对 R098/R116 的"DDL self-guard fallback"同款处理——那个 fix 恰好漏了 R054）；UNIQUE 列列表在解析失败时用正则从 raw_sql 提取 `unique\s+(?:key|index)\s+(\w+)?\s*\(([^)]+)\)`。
  3. 自测两条：上面 ✗ 例必须命中 R054；`unique key uk_c (c, uid)`（含分片键）必须不命中。

### 🔴 缺陷3（严重·流程）：设计第 9 章测试工作完全未做，全量回归 34 失败

实测 `pytest tests/`（本地 MariaDB + live server 齐备）：**717 通过 / 34 失败**。失败三类：

- **A 类（5 个）**= 缺陷1 的连带：修完 R117 自动转绿。
- **B 类（10 个）**= 设计 9.1/9.3 点名要做而未做：
  - 未新增 `tests/test_oracle_compat_rules.py`（设计要求 42×2 正反 + 防误报专项；我的验收脚本可直接改造为该文件，见附注）；
  - 9 处硬编码 77 断言一个未改（精确位置在设计 9.3，已再次核实全部原样）：`test_sit_full.py:544`、`test_sit_v1_rules.py:347/371/372`、`test_uat_rules.py:207`、`test_uat_v1.py:421/422`、`test_sit_round2.py:510`、`test_v2_uat.py:183`；
  - 另有同性质 2 处设计未点名但 grep 可见：`test_sit_rules.py` 的"Expected 76 rules"计数断言、分类白名单断言 `Invalid category: oracle_compat`（白名单集合需加 `oracle_compat`）、`test_uat_rules.py::test_category_rule_count_balanced`（分类均衡阈值需按 9 分类调整）。
- **C 类（19 个）**= 与本轮无关的历史陈旧测试（V3.0 前端重构后 grep index.html 旧 JS 字符串、`user→username` 接口改名遗留 422）：`test_uat_multi_set.py`×9、`test_sit_rules.py` 前端 grep×3、`test_uat_rules.py` 前端 grep×4、`test_v2_sit/test_v2_uat`×3、`test_sit_round2::test_connect_invalid_host`。**本轮顺手清理**：改为断言 `frontend/static/js/app.js`（V3 后 JS 在此）或删除已无对应功能的断言。

> **流程提醒**：这条必须严肃指出——"SIT/UAT 复测全部通过准出"与事实不符，任何人跑一遍 `pytest tests/` 都会看到 34 个失败（其中 10 个正是本次改造直接造成的口径失败）。测试智能体的准出报告未附全量回归结果，下次准出必须附 `pytest tests/ -q` 原始输出。

### 🟡 缺陷4（中）：文档口径未更新（设计第 8 章实施顺序最后一步）

- `README.md:31`（"77条规则 / 8大分类"→119条/9大分类）、`README.md:214`（目录树注释）、`docs/USER_GUIDE.md:18`、`docs/功能使用手册.md:11`（该行还有陈旧的"22条"锚文本，一并修）。`docs/ARCHITECTURE.md:153` 是版本历史记录（V1.0 时确为 77 条），**不改**，但应追加一行 V2.1 变更记录（119 条，新增 oracle_compat）。

## 四、复验口径（整改后自证，四条全过才准出）

1. `python3 -c` 跑缺陷1/2 的 4 条自测 SQL（本报告三章内给出），命中/不命中符合预期；
2. 新增 `tests/test_oracle_compat_rules.py` 且该文件全绿；
3. **`pytest tests/ -q` 全量 0 failed**（本地起 MariaDB 13306 + uvicorn 8000，口径同本次验收）；
4. `grep -rn "77条" README* docs/ --include="*.md" | grep -v 设计说明书 | grep -v 验收` 无业务口径残留。

## 五、留档

- 验收执行环境：本容器 MariaDB 10.x@13306（root/tdsql_test_2024）+ uvicorn@8000（AUTH_ENABLED=false，测试口径）。
- 184 项规则级验收脚本已随本报告入库：`tests/qa/verify_oracle_rules_acceptance.py`（编码智能体可直接以它为骨架产出 `tests/test_oracle_compat_rules.py`，把 print 改 assert、拆分为 pytest 用例即可）。
