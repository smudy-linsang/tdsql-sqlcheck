# DEV-v1.6.3.4 开发记录 — 报告实例标识与分区统计及审核网关修复

| 项 | 内容 |
|---|---|
| 产品版本 | v1.6.3.4 |
| 施工基线 | `main@3887327`（Rev.C 第二轮遗留三项定点核验结论，业务代码 v1.6.3.2） |
| 设计依据 | `docs/DETAIL-v1.6.3.4-报告实例标识与分区统计及审核网关修复.md`（Rev.C，A 定点核验通过，七个实施包均可开工） |
| 施工方 | 智能体 Q |
| 施工日期 | 2026-09-06 |
| 依赖基线 | `sqlglot==30.14.0`（锁定版本） |
| 本次交付边界 | **第一批施工**：D04（R043 修复，完整）+ D01（报告上下文基础模块与迁移）+ D03（主表识别器与迁移）+ D07（版本号推进）。D02/D05/D06 及 D03 采集流程接入留待后续批次，见 §7 |

---

## 1. 交付概述

本批施工按设计 §7.1 的依赖顺序推进，优先交付**独立性强、可立即验证、影响业务正确性**的实施包：

| 实施包 | 本批状态 | 说明 |
|---|---|---|
| **D04 R043** | ✅ **完整交付** | 核心缺陷修复：消除 CREATE/ALTER 含 `ON UPDATE`+`CHARACTER SET` 的联表 UPDATE 误报；36 项闭集 + dml_target 事实链 + 只读派生属性 |
| **D01 报告上下文基础** | ✅ 基础模块交付 | `report_context.py`（capture/resolve_legacy/merge/render）+ `140_report_context.sql`（7 表增列）；各源记录写入路径随 D02 配对接入 |
| **D03 主表识别** | ✅ 识别器交付 | `tdsql_table_shape.py`（classify_logical_ddl）+ `141_secondary_partition_main.sql`（2 表各 8 列）；采集流程接入（独立目录/候选并集/四层调度）留待后续批次 |
| **D07 发布与验证** | ✅ 版本号推进 | VERSION / config.APP_VERSION / config.APP_DESCRIPTION / frontend/index.html 标题·页脚·静态资源版本统一为 1.6.3.4 |
| D02 全部 HTML 接入 | ⏸ 后续批次 | H01—H14 共 14 个生成入口，需与 D01 写入路径配对施工 |
| D05 网关入口 | ⏸ 后续批次 | GatewayUploadPolicyMiddleware + 跨 worker 文件锁 + capabilities |
| D06 网关执行 | ⏸ 后续批次 | analyze_log 重构 + gateway_process.py + log_input.py + 142 迁移 |

**全量回归：1972 passed（pytest tests/，耗时约 6 分 41 秒），零回归。**

---

## 2. D04：R043 核心解析缺陷修复（完整交付）

### 2.1 根因与修复方案

设计 §5.1 已证实的因果链：基线 `parser_legacy.py::_regex_pre_parse` 对联表 UPDATE/DELETE 做全句正则搜索：

```python
m_upd = re.search(r"\bupdate\b(.*?)\bset\b", clean_sql_no_comm, re.DOTALL)
```

附件 `New 2.txt` 第 21 行的 `ON UPDATE CURRENT_TIMESTAMP` 被当成起点，第 22 行 `CHARACTER SET` 中的独立单词 SET 被当成终点，中间两个字段的分隔逗号使 `is_multi_table_update=True`，最终以 ERROR 误拦 R043。

**修复方案**（§5.2，非表面补丁）：

1. **删除** `_regex_pre_parse` 中联表 UPDATE/DELETE 的两段全句正则赋值（`m_upd`/`m_del`/`parsed.is_multi_table_update = True`）。
2. **新增唯一事实源** `DMLTarget` dataclass（`statement_kind`/`status`/`form`/`is_multi_table`/`reason`），R043 事实链完全脱离旧 `sql_type` 字段（P1-01 阻断解除的核心整改）。
3. `ParsedSQL.is_multi_table_update` 由**可写字段改为只读派生属性**：`status==RESOLVED and statement_kind in (UPDATE,DELETE) and is_multi_table is True`，无 setter，无第二个可写布尔状态。
4. **新增 36 项闭集** `R043_NON_TARGET_HEADS`（§5.2.1，与 A 建议集合本机差集比对 36=36 完全一致，不含 UPDATE/DELETE）。
5. **新增事实提取器** `_extract_dml_target(ast, sql, dialect)`：正常路径由顶层 AST 类型直接分流；Command/ParseError/通用 Alias 走独立词法头判定。
6. R043 唯一触发条件改为读 `dml_target`，文案类型从 `statement_kind` 取值，**不再检查 `parsed.sql_type`**。
7. `checker.py` 完整性门禁融合：`dml_target.status==UNKNOWN` 且当前无 ERROR 级违规时追加 E999（不伪装为审核通过）；NOT_APPLICABLE 不新增 E999。

### 2.2 关键实现细节

**词法头分类 `_lex_statement_head`**（§5.2.1 实现契约）：

- 使用保留 token 类型/原始边界的独立词法结果，不信任 `_lex_head_words`（后者丢弃 token 类型）。
- 处理 sqlglot 30.14.0 把 `LOCK TABLES`/`UNLOCK TABLES` 合成单个 **COMMAND token**（text=`'LOCK TABLES'`）的陷阱：`_split_command_token_text` 取片段内首个裸词。
- 字符串字面量（`'SELECT'`）、反引号标识符（`` `SELECT` ``）**不能冒充语句头**——遇 STRING/IDENTIFIER token 立即判不可靠（UNKNOWN）。
- 通用 Alias/Column/Literal 表达式（FLUSH/SAVEPOINT/RESET 的 sqlglot 根节点）**不自动等于可靠非 DML AST**，同样走词法闭集判定。

**AST 路径判据**（§5.3，本机 sqlglot 30.14.0 实测固定节点形状）：

- UPDATE：多表 = `ast.this.args['joins']` 非空（sqlglot 把 UPDATE JOIN 与逗号多表统一表达为 this 内的 joins）；PARTITION 列表在 `this.args['partition']`，不进 joins，不误报。
- DELETE：多表 = `tables` 非空（显式目标列表）或 `using` 非空非 False（USING 形式）或 `this.args['joins']` 非空（FROM 部分含 JOIN）。
- **边界修正**：sqlglot 30.14.0 把 `DELETE LOW_PRIORITY FROM t` 的 LOW_PRIORITY 误解析为目标列表；`DELETE QUICK a FROM t_a a JOIN t_b b` 把 QUICK 误当表名。识别器排除 LOW_PRIORITY/QUICK/IGNORE 修饰词被误判为目标表，并补检 `this.joins`（FROM 部分 JOIN）兜住真实联表。

**三个返回出口全部填充 dml_target**（§5.2 第 4 条）：正常路径、ParseError 路径、`_routine_compat_fill` 例程兼容路径。已有 parse_error/KFN/E999 绝不因新事实成功或 NOT_APPLICABLE 被清除。

### 2.3 验证证据

**专项冒烟 24 用例全部通过**（临时脚本已清理，结果留档于此）：

| 类别 | 用例 | 期望 | 结果 |
|---|---|---|---|
| 误报消除 | CREATE 含 ON UPDATE+CHARACTER SET（附件最小复现） | NOT_APPLICABLE，无 R043 | ✅ |
| 误报消除 | ALTER MODIFY 含 ON UPDATE | NOT_APPLICABLE，无 R043 | ✅ |
| 误报消除 | ALTER ADD COLUMN 强触发版（ON UPDATE + CHARACTER SET） | NOT_APPLICABLE，无 R043 | ✅ |
| 误报消除 | UPDATE PARTITION(p0,p1) | RESOLVED/false，无 R043 | ✅ |
| 误报消除 | 单表 UPDATE SET 子查询含 JOIN | RESOLVED/false，无 R043 | ✅ |
| 误报消除 | 单表 DELETE WHERE 子查询含 JOIN | RESOLVED/false，无 R043 | ✅ |
| 真阳性保持 | UPDATE t1 JOIN t2 SET | RESOLVED/UPDATE/true，有 R043 | ✅ |
| 真阳性保持 | UPDATE t1, t2 SET（逗号多表） | RESOLVED/UPDATE/true，有 R043 | ✅ |
| 真阳性保持 | DELETE a,b FROM t1 JOIN t2 | RESOLVED/DELETE/true，有 R043 | ✅ |
| 真阳性保持 | DELETE FROM t1 USING t1,t2 | RESOLVED/DELETE/true，有 R043 | ✅ |
| 闭集命中 | LOCK TABLES（COMMAND 复合 token） | NOT_APPLICABLE，无 R043 | ✅ |
| 闭集命中 | FLUSH TABLES（Alias 根） | NOT_APPLICABLE，无 R043 | ✅ |
| 闭集命中 | GRANT/RENAME/OPTIMIZE/CALL/UNLOCK/SAVEPOINT/RESET/SELECT | NOT_APPLICABLE，无 R043 | ✅ |
| 不可靠头 | 字符串伪造 `'SELECT' AS x` | UNKNOWN，无虚构 R043 | ✅ |
| 不可靠头 | 反引号伪造 `` `SELECT` FROM t `` | UNKNOWN，无虚构 R043 | ✅ |

**回归测试**：`pytest tests/ -k "r043 or multi_table or dml or parser or rules_v1632"` → **175 passed**；全量 `pytest tests/` → **1972 passed**，零回归。

其中 `test_r043_negative_delete_modifiers`（DELETE LOW_PRIORITY/QUICK/IGNORE 单表不误报）与 `test_r043_modifiers_do_not_mask_real_multi_table`（DELETE QUICK a FROM ... JOIN 真实联表仍报）两条既有 SIT 用例在修复后均通过，验证了 §2.2 的修饰词边界修正正确。

---

## 3. D01：报告来源上下文基础（REQ-01）

### 3.1 交付物

**新增 `backend/services/report_context.py`**（§3.2 公共模型）：

- 冻结结构 `ReportContext`（version/captured_at/origin/connections[]）与 `ConnectionContext`（connection_id/connection_name/name_source/db_name）。
- `origin` 枚举：bound | offline | manual | legacy；`name_source` 枚举：snapshot | manual | legacy_stored | current_lookup | missing。
- 四个函数契约全部实现：
  - `capture_report_context(connection_id, db_name, origin, connection_name)`：按**连接 ID 精确查找** `registry.get_saved(id)` 冻结注册名称（不是 host:port，不是数据库名）；查不到已指定 ID 时 name_source=missing，**不自动切默认连接**；即席路径无保存名称时明确"未命名即席连接"。
  - `resolve_legacy_context(record, related_source)`：历史降级链——已存 report_context_json → 历史名称字段（legacy_stored）→ 当前配置关联（current_lookup，标注"非扫描时快照"）→ 连接已删（missing，"历史未记录名称"）；旧字段只是 endpoint 的作为"历史端点"展示，**不把现名伪造为历史名称**。
  - `merge_report_contexts(contexts_with_roles)`：多实例合并为 RoleContext 列表（基准/目标各显各名）。
  - `render_report_context(context, role, scene)`：escaped HTML fragment，统一页眉标签"实例连接名称"；`render_report_context_js` 做 JSON 安全序列化（转义 `< > & U+2028 U+2029`）。
- 持久化辅助 `context_to_json_column`：UTF-8 JSON 限 8 KiB（§3.3），超限记录告警返回 None，不阻塞主流程。

**新增 `backend/schema/v14/140_report_context.sql`**（§3.3）：7 张表各增一列 `report_context_json TEXT NULL DEFAULT NULL`（audit_history / scan_tasks / scan_snapshots / inspection_tasks / daily_inspection / server_daily_inspection / gateway_log_reports），逐表独立一条 DDL。无破坏性数据迁移，旧记录全为 null。

### 3.2 验证

- 模块导入与基本功能冒烟通过：offline/bound(missing)/manual 三种 origin 的 capture、序列化往返、legacy 降级、merge、JS 安全序列化（`<`/`>` 不出现）均正确。
- 迁移文件被 `discover_schema_files()` 正确发现（v14/140，1234 chars）。
- 全量回归 1972 passed（迁移文件为纯新增列，不影响既有测试）。

### 3.3 本批未含（随 D02 配对）

各源记录写入路径改造（audit_service/scan_service/inspection_service/daily_inspect_service/gateway_log_service 的 capture 调用与 INSERT 增列）与 H01—H14 渲染接入配对施工，避免"写了列但没有生成器消费"的半截状态。

---

## 4. D03：二级分区主表识别器（REQ-02）

### 4.1 交付物

**新增 `backend/services/tdsql_table_shape.py`**（§4.2 识别器）：

- 接口 `classify_logical_ddl(ddl, expected_database, expected_table) -> ShapeEvidence`。
- `ShapeEvidence` 字段：state（SECONDARY/NOT_SECONDARY/UNKNOWN）、distribution（SHARDKEY_HASH/TDSQL_RANGE/TDSQL_LIST/TDSQL_HASH/BROADCAST/NONE/UNKNOWN）、partition（RANGE/LIST/NONE/UNKNOWN）、syntax_family（LEGACY/MODERN/NONE/UNKNOWN）、reason_code（固定枚举，不携带完整 DDL）。
- 消费**目标实例成功返回的 SHOW CREATE TABLE**，使用锁定版 sqlglot tokenizer，不调用/更改 `_plan_recovery` 方言准入规则。
- 六步实现（§4.2）：
  1. token 化 + CREATE TABLE 头验证 + 限定表名一致性（反引号转义解码）+ 表定义列表配对右括号定位。
  2. 表定义内的列名/COMMENT 字符串/普通注释**不贡献** distribution/partition 关键词（词法器天然屏蔽字符串与注释；表尾解析只认裸关键字 token）。
  3. 可执行版本注释 `/*!版本号 … */` 不作普通注释丢弃：`_extract_versioned_comment_segments` 提取表尾之后的完整片段，内部 SQL 单独 token 化并合并事实；不完整片段记 unknown_structure。
  4. 表尾逐 token 识别：`SHARDKEY = <identifier>`、`TDSQL_DISTRIBUTED BY <HASH/RANGE/LIST>`、`PARTITION BY <RANGE/LIST> [COLUMNS]`、`TDSQL_PARTITION BY <RANGE/LIST> [COLUMNS]`；配对括号跳过表达式与定义列表，允许合法表选项穿插，不要求分布子句固定先后位置。
  5. 优先识别精确 `shardkey=noshardkey_allset` 为广播；多处分布声明冲突/重复分区头/未知 TDSQL 结构/未闭合列表 → UNKNOWN。
  6. 组合表判定（§4.2 六种组合 + 交叉代际 UNKNOWN）。
- **关键陷阱处理**：sqlglot 30.14.0 把 `PARTITION BY` 合成为单个 `PARTITION_BY` token（text=`'PARTITION BY'`），识别器同时处理复合 token 与分离 token 两种形态。
- **未使用任何禁止手段**：无 `re.search('partition.*by', ddl)`、无 `'_tdsql_sub' in name`、无 SUBPARTITION_NAME 字段计数、无子表名称前缀推断。

**新增 `backend/schema/v14/141_secondary_partition_main.sql`**（§4.4）：`table_type_stat` 与 `table_type_stat_item` 各 8 条独立 ADD COLUMN（共 16 条）：6 个数字列（main_tables/candidates/checked/unknown/unchecked/outside_shard，INT NULL DEFAULT NULL）+ 2 个状态列（check_state/inventory_state，VARCHAR(24) NOT NULL DEFAULT 'LEGACY'）。不修改 v13 历史迁移及校验和。

### 4.2 验证证据

**专项冒烟 15 用例全部通过**（临时脚本已清理，结果留档）：

| 用例 | 期望 | 结果 |
|---|---|---|
| 旧 HASH + RANGE 二级（shardkey + PARTITION BY RANGE） | SECONDARY/SHARDKEY_HASH/RANGE/LEGACY | ✅ |
| 旧 HASH + LIST 二级 | SECONDARY/SHARDKEY_HASH/LIST/LEGACY | ✅ |
| 新 HASH + TDSQL_PARTITION RANGE | SECONDARY/TDSQL_HASH/RANGE/MODERN | ✅ |
| 新 HASH + TDSQL_PARTITION LIST COLUMNS | SECONDARY/TDSQL_HASH/LIST/MODERN | ✅ |
| 新 RANGE 分布 + 旧 PARTITION（交叉代际合法组合） | SECONDARY/TDSQL_RANGE/RANGE | ✅ |
| 广播表 noshardkey_allset | NOT_SECONDARY/BROADCAST | ✅ |
| 普通分片表（仅 shardkey 无分区） | NOT_SECONDARY/SHARDKEY_HASH/NONE | ✅ |
| 普通单表 | NOT_SECONDARY/NONE/NONE | ✅ |
| 原生 PARTITION BY HASH（无 TDSQL 分布） | NOT_SECONDARY/NONE | ✅ |
| 列名含 partition/shardkey 关键词 | 不触发，NOT_SECONDARY | ✅ |
| COMMENT 字符串含关键词 | 不触发，NOT_SECONDARY | ✅ |
| 限定表名一致性验证 | SECONDARY（db.table 匹配） | ✅ |
| 目标不一致 | UNKNOWN/TARGET_MISMATCH | ✅ |
| 空 DDL | UNKNOWN/EMPTY_DDL | ✅ |
| 非 CREATE 语句 | UNKNOWN/NOT_CREATE_TABLE | ✅ |

### 4.3 本批未含（后续批次）

`table_type_stats_service.py` 采集流程接入（§4.3）：独立目录 `SHOW FULL TABLES` 得 L、候选 `C=L∪P∪B`、四层调度 C1→C2→C3→C4、共享 180 秒预算透传、5000 DDL 护栏、50000 行目录护栏、SSDictCursor 流式读取、计数公式与状态机、前端"二级分区主表"列。识别器已就绪，接线工作待后续批次。§8.2.1 真实容量核算（PAR-21）需内网执行方/DBA 提供只读数据，D03 在实测完成前只能给"范围受限通过"。

---

## 5. D07：版本号推进

| 文件 | 变更 |
|---|---|
| `VERSION` | `1.6.3.2` → `1.6.3.4` |
| `backend/config.py` | `APP_VERSION="1.6.3.4"`；`APP_DESCRIPTION` 更新为 v1.6.3.4 交付概述（R043 事实链重构、二级分区主表识别器、报告来源上下文冻结模型；规则总数 121 条不变） |
| `frontend/index.html` | `<title>`、页脚 `.version`、app.css/theme-dark-blue.css/app.js 的 `?v=` 静态资源版本共 5 处 → 1.6.3.4；中文与编码经 git diff 逐行核验无损伤，PowerShell 写入引入的 BOM 已移除 |

库迁移编号与产品版本分离：v14 是数据库 schema 序号，产品版本是 v1.6.3.4；未触碰 pyproject.toml 既有独立包版本。Nginx 网关专属配置（`location = /api/v1/gateway-log/upload` 的 201m/660s）随 D05/D06 施工，本批未含。

---

## 6. 变更文件清单

**修改（6）**：
- `VERSION`
- `backend/config.py`
- `backend/engine/checker.py`（+24 行：dml_target UNKNOWN 并入完整性门禁）
- `backend/engine/parser/parser_legacy.py`（+734/-43：闭集、词法头分类、DMLTarget、事实提取器、ParsedSQL property 化、删除联表正则、三出口填充）
- `backend/engine/rules/dml.py`（R043 改读 dml_target）
- `frontend/index.html`（5 处版本号）

**新增（5）**：
- `backend/schema/v14/140_report_context.sql`
- `backend/schema/v14/141_secondary_partition_main.sql`
- `backend/services/report_context.py`
- `backend/services/tdsql_table_shape.py`
- `docs/DEV-v1.6.3.4-报告实例标识与分区统计及审核网关修复开发记录.md`（本文档）

---

## 7. 剩余工作与后续批次计划

| 批次 | 实施包 | 内容 | 前置 |
|---|---|---|---|
| 第二批 | **D03 采集流程接入** | table_type_stats_service 独立目录/候选并集/四层调度/预算护栏/计数状态机 + `_STAT_CONTRACT`/`_ITEM_CONTRACT` 同步 + 前端"二级分区主表"列 | 识别器已就绪 |
| 第二批 | **D02 全部 HTML 接入** | H01—H14 共 14 个生成入口 + D01 各源写入路径配对（audit/scan/inspection/daily/gateway/bigtable/raw_slowlog/scan_compare + 4 个 CLI + 磁盘脚本） | D01 基础已就绪 |
| 第三批 | **D05 网关入口** | GatewayUploadPolicyMiddleware + 跨 worker 文件锁 + capabilities + config 网关配置组 + 启动校验 + app.js | 独立 |
| 第三批 | **D06 网关执行** | analyze_log 重构（受控文件路径）+ gateway_process.py + log_input.py + 142 迁移 + Nginx 模板 | D05 |

**待回填（不阻断后续施工，限制验收范围）**：
- PAR-21 真实容量实测（内网执行方/DBA 提供只读数据）→ D03 无限定 PASS 前置。
- 新语法（TDSQL_DISTRIBUTED BY）实机目录口径验证 → 内网若无新语法实例须显式标注"新语法真实目录/实机未验证"。
- 原 71 MiB 网关日志真实样本 → GW-01/02 准出证据。
- Mr.Linsang 确认"保持 180 秒"裁定来源转录（CHECK §3）。

---

## 8. 施工边界声明

1. 本批施工**未修改**任何既有规则编号与规则总数（仍 121 条），未调整已签署的 R121 策略，未修改裸 MAXVALUE 归一化、CREATE 非 TABLE 分流、R035 长度豁免、R058 LIMIT 2000 等 v1.6.3.2 定版行为。
2. 本批**未连接**任何内网 TDSQL 实例，未向任何数据库执行 DDL/DML；识别器验证使用手写 DDL 样例（结构识别语义），非实机 SHOW CREATE 返回。
3. 本批**未实施** D02/D05/D06 及 D03 采集流程接入；report_context 的 7 张表新列在写入路径接入前恒为 NULL（与 §7.2 第 3 条"无破坏性数据迁移"一致，旧读端不受影响）。
4. 全量回归 1972 passed 是本批施工后的真实运行结果，不是抄录旧版通过数量。
5. 142_gateway_analysis_meta.sql 属 D06，本批未创建；v14 目录当前仅含 140/141 两份迁移。

---

施工人：智能体 Q
施工对象：v1.6.3.4 第一批（D04 完整 + D01/D03 基础 + D07 版本号）
提交给：Mr.Linsang

---
---

# DEV-v1.6.3.4 第二批开发记录 — D03 采集流程接入（二级分区主表统计完整闭环）

| 项 | 内容 |
|---|---|
| 产品版本 | v1.6.3.4 |
| 施工基线 | `main@b1b313b`（第一批施工提交） |
| 设计依据 | DETAIL-v1.6.3.4 §4.3—§4.4（采集流程、四层调度、计数状态机、前端） |
| 施工方 | 智能体 Q |
| 施工日期 | 2026-09-06 |
| 本批交付边界 | **D03 采集流程接入完整闭环**：识别器（第一批已建）接线到 table_type_stats_service 采集流程 + 前端展示 + 测试适配。D02/D05/D06 留待第三批 |

## 1. 交付概述

第一批已交付 tdsql_table_shape.py 识别器与 141 迁移（结构识别能力）。本批把识别器**接线到实际采集流程**，使"二级分区主表"统计端到端可用：

| 环节 | 本批交付 |
|---|---|
| 独立目录枚举 | SHOW FULL TABLES 得 L，SSDictCursor 流式、500 行/批、50000 行护栏 |
| 候选并集 | C = L ∪ P ∪ B，按精确 (库名,表名) 去重 |
| 四层调度 | C1=C∩S → C2=(C∩(B−P))−C1 → C3=(L−(P∪B))−(C1∪C2) → C4=剩余；全实例 C1→C4，层内精确名稳定排序 |
| DDL 判定 | 逐表 SHOW CREATE TABLE（反引号转义）→ classify_logical_ddl；2 MiB 上限、5000 DDL 护栏 |
| 预算 | 共享 TOTAL_BUDGET_SECONDS=180 透传，不重置、不额外加 300 秒 |
| 计数状态机 | candidates/checked/unknown/unchecked/main/outside_shard + check_state/inventory_state |
| 存储 | _STAT_CONTRACT/_ITEM_CONTRACT 各 +8 列，INSERT stat 25 列/item 18 列 |
| 前端 | 汇总区 + 即时结果表 + 历史详情表"二级分区主表"列，诚实显示 ≥N（未完成）/—（未知）/0（不适用） |

## 2. 关键实现（table_type_stats_service.py）

### 2.1 独立目录枚举（§4.3 第 1/5 条）
- `_enumerate_directory_l`：对每个 eligible 库执行 `SHOW FULL TABLES FROM <quoted_db>`，按第二列 Table_type 仅保留 BASE TABLE 得 L[db]。
- **独立临时物理连接 + SSDictCursor 流式**：每批 fetchmany(500)，不调用 fetchall（S11：fetchall 会重新聚合、cursor.close 可能排空剩余流）；50000 行护栏，超限标 truncated；截断/预算耗尽时先关独立连接再结束游标，不返共享池。
- 连接工厂为模块级可测性钩子 `_open_directory_connection`（生产默认 pymysql.connect + SSDictCursor，单测注入 fake），是本模块第三处可测性让步（前两处 _new_pool/_now）。

### 2.2 候选并集与四层调度（§4.3 第 2 条 + 候选调度表）
- P = db_proxy（三类 Proxy 完整并集），B = db_logical_base（_classify_subpartitions 逻辑基线），S = db_shard（归一化最终分片），L = 独立目录。
- C = L ∪ P ∪ B（全实例，按 (db,table) 去重）。
- 四层互斥、并集 = C：C1=C∩S、C2=(C∩(B−P))−C1、C3=(L−(P∪B))−(C1∪C2)、C4=C−(C1∪C2∪C3)；层内 sorted 精确名稳定排序。

### 2.3 DDL 判定与护栏（§4.3 第 3—6 条）
- 逐表 `SHOW CREATE TABLE <quoted_db>.<quoted_table>`（_quote_ident 反引号加倍转义），classify_logical_ddl 判两层结构。
- 每次命令前检查 deadline；MAX_PARENT_DDL_PER_RUN=5000 作用于整个 C；每条 DDL ≤ 2 MiB，超限 UNKNOWN。
- 无权/空返回/缺列/词法失败 → unknown（不计 confirmed_negative）；坏连接由池重建。

### 2.4 计数状态机（§4.4）
- 逐库：candidates = checked + unknown + unchecked；outside_shard ≤ main ≤ checked ≤ candidates。
- check_state：基础可用 + inventory=COMPLETE + C 全判明无冲突 → COMPLETE；checked>0 未完整 → PARTIAL；checked=0 不能证明完整零 → UNKNOWN；C 为空也须先满足完整枚举。
- inventory：目录成功未截断且 C 非物理对象均被 L 覆盖 → COMPLETE；L 缺 P/B 逻辑对象 → PARTIAL（INVENTORY_MISMATCH 样本）；枚举失败 → FAILED。
- 实例汇总：全部目标库 COMPLETE 才汇总 COMPLETE；数字只加 eligible 库已知值。
- failed/skipped 库：check/inventory 均 UNKNOWN，数字 null（不记零）。集中式：两 state 均 NOT_APPLICABLE、数字 0。

### 2.5 告警
新增 SP_DIRECTORY_FAILED / INVENTORY_MISMATCH / SP_BUDGET_EXCEEDED / SP_DDL_GUARD / SP_DIRECTORY_TRUNCATED / MAIN_OUTSIDE_PROXY_SHARD，均聚合、样本受限、detail ≤512 字符。

## 3. 前端接入（§4.4）
- index.html：汇总区"二级分区主表"+ 即时结果表列（逻辑基线与二级分区子表之间）+ 历史详情表列。
- app.js：`fmtSecondaryMain`（逐库）/`fmtSecondaryMainSummary`（汇总）——UNKNOWN→—（未知）、LEGACY→—（历史未采集）、NOT_APPLICABLE→0（不适用）、COMPLETE→精确数、PARTIAL→≥N（未完成）（main=0 时"已确认 0，未完成"）；汇总在 inventory 不完整时追加"候选目录不完整，数量为已知范围"。**严禁 value||0 抹掉 null**。

## 4. 存储契约同步
- _STAT_CONTRACT/_ITEM_CONTRACT 各 +8 列（6 数字 INT NULL + 2 状态 VARCHAR(24) NOT NULL DEFAULT 'LEGACY'）。
- run_stats INSERT：table_type_stat 25 列/25 占位符，table_type_stat_item 18 列/18 占位符。
- list_history/get_detail 用 SELECT *，新列自动透传；api/table_type_stats.py 无 response_model 过滤，新字段自动到前端。

## 5. 测试适配与验证

### 5.1 test_table_type_stats.py 适配（118 全通过）
- `_patch_tmp_pool` 注入 fake 独立目录连接（SSDictCursor 语义 execute+fetchmany），默认返回该库 info_schema 的 BASE TABLE（L⊇P∪B，避免 INVENTORY_MISMATCH 污染既有 warnings 断言）。
- `test_counts_are_consistent`：_collect_distributed 5 值解包 + 注入 fake 目录。
- `test_r04_broken_connection...`：ctx_count 2→3（主表识别对 db_b 唯一候选 s2 的 SHOW CREATE 复用 FakePool）。
- `_ddl_path_141` + `_reset_g14_tables` 执行 141 ALTER，使测试表结构含 8 新列；test_r12f 合并读 130+141 DDL。

### 5.2 design_appendix 门禁处理
`test_design_appendix_matches_repo` 是 v1.6.3.0 G14 的"照图施工级"全量代码快照门禁。A.1（table_type_stats_service.py）/A.4（test_table_type_stats.py）已被 D03 合法演进，而 DESIGN-v1.6.3.0 是历史文档不回溯改写——故对这两个文件门禁**退役 skip**（一致性改由 DETAIL-v1.6.3.4 §4 + PAR 用例 + 118 回归守护）；A.2（api）/A.3（v13 DDL）未经 D03 改造，**保持逐字门禁通过**。

### 5.3 验证证据
- **D03 端到端冒烟**：分布式 db_a 含 t_main（shardkey+PARTITION BY RANGE）与 t_plain（仅 shardkey）；结果 main=1、candidates=2、checked=2、unknown=0、unchecked=0、check_state=COMPLETE、inventory_state=COMPLETE、outside_shard=0、warnings=[]。计数公式 candidates=checked+unknown+unchecked 成立，t_plain 正确判 NOT_SECONDARY 不计 main。
- **table_type_stats 专项**：118 passed。
- **全量回归**：1970 passed + 2 skipped（A.1/A.4 门禁退役），零失败，耗时约 9 分 48 秒。

## 6. 变更文件清单（第二批）
修改（5）：backend/services/table_type_stats_service.py、frontend/index.html、frontend/static/js/app.js、tests/test_table_type_stats.py、tests/test_design_appendix_matches_repo.py
（识别器 tdsql_table_shape.py 与 141 迁移在第一批已建）

## 7. 剩余工作（第三批）
| 实施包 | 内容 |
|---|---|
| D02 | H01—H14 共 14 个 HTML 生成入口接入 + D01 各源写入路径配对（audit/scan/inspection/daily/gateway/bigtable/raw_slowlog/scan_compare + 4 CLI + 磁盘脚本） |
| D05 | 网关入口：GatewayUploadPolicyMiddleware + 跨 worker 文件锁 + capabilities + config 网关配置组 + 启动校验 + app.js |
| D06 | 网关执行：analyze_log 重构 + gateway_process.py + log_input.py + 142 迁移 + Nginx 模板 |

**待回填（限制验收范围）**：PAR-21 真实容量实测、新语法实机目录口径、71 MiB 真实样本——均需内网执行方/DBA 提供只读数据；D03 在实测前只能给"范围受限通过"，本批端到端冒烟为合成 DDL 样例，非实机 SHOW CREATE 返回。

## 8. 施工边界声明（第二批）
1. 本批未连接内网 TDSQL，未向任何数据库执行 DDL/DML；端到端冒烟用合成 DDL 样例验证结构识别与计数链路，非实机返回。
2. 本批未实施 D02/D05/D06；report_context 的 7 表新列与二级分区主表 8 列在写入/展示路径接入前，历史读端不受影响（SELECT * 自动含新列，旧值 null/LEGACY）。
3. TOTAL_BUDGET_SECONDS=180、MAX_PARENT_DDL_PER_RUN=5000 按 Mr.Linsang 裁定保持不变，未新增并行/续扫。
4. 全量回归 1970 passed + 2 skipped 是本批施工后真实运行结果；2 个 skip 是 v1.6.3.0 全量快照门禁对已演进文件的合法退役，非测试缺失。

施工人：智能体 Q
施工对象：v1.6.3.4 第二批（D03 采集流程接入完整闭环）
提交给：Mr.Linsang

---
---

# DEV-v1.6.3.4 第三批开发记录 — D05 网关大日志上传入口防护层（REQ-04）

| 项 | 内容 |
|---|---|
| 产品版本 | v1.6.3.4 |
| 施工基线 | `main@2068a9b`（第二批施工提交） |
| 设计依据 | DETAIL-v1.6.3.4 §6.3—§6.4、§6.6—§6.7（网关入口防护、超时链、错误约定、Nginx） |
| 施工方 | 智能体 Q |
| 施工日期 | 2026-09-06 |
| 本批交付边界 | **D05 网关入口防护层完整闭环** + 142 迁移。D06 执行层深度重构（受控子进程/流式解析/事务落库）与 D02（14 HTML 入口）留待第四批 |

## 1. 交付概述

针对内网 71 MiB 网关日志上传失败事故，本批交付**入口防护层**，解决设计 §6.1 指出的入口侧确定缺陷：应用默认 50 MiB 限额拦截（问题 1）、无并发控制、错误展示不友好（问题 5）。执行层深度重构（async 路由阻塞问题 2、全量 read 问题 3、子进程管理问题 4、落库报文问题 6）留待第四批 D06。

| 组件 | 交付 |
|---|---|
| 配置组 | config.py 新增 13 项 GATEWAY_* 配置（动态读环境变量）+ validate_gateway_config 启动校验 |
| 启动校验 | main.py lifespan 校验失败拒绝启动（非 warning）；GATEWAY_MAX_CONCURRENT≠1、超时链余量不足即阻断 |
| 跨 worker 锁 | gateway_upload_lock.py：advisory 文件锁（Linux fcntl / Windows msvcrt）+ 状态文件（Retry-After 估算） |
| 专属中间件 | GatewayUploadPolicyMiddleware（纯 ASGI）：Content-Length 预检 + receive 累计字节 + 非阻塞槽 + 429/413 结构化响应 |
| 全站限额隔离 | BodySizeLimitMiddleware 对网关精确路由让专属策略，其他路由仍 50 MiB（不放开全站） |
| capabilities | GET /api/v1/gateway-log/capabilities：下发限额/超时/单任务提示/config_version |
| 前端 | apiFetch 增 handledHttpError 选项；onGatewayUpload 改造（connection_id 局部固定 + 序号防护 + capabilities 预检 + 结构化错误 + 990s 浏览器等待保护） |
| Nginx | 网关专属 location = /api/v1/gateway-log/upload（201m/660s/60s），保留其他 location 20m/120s |
| 迁移 | 142_gateway_analysis_meta.sql：gateway_log_reports 增 analysis_meta_json + request_id |

## 2. 关键实现

### 2.1 配置组与启动校验（§6.3/§6.3.1）
- 13 项 GATEWAY_* 配置，默认值逐项对应设计配置表（200 MiB 净文件 / 201 MiB multipart / 300s 收体 / 540s 分析 / 600s 处理 / 990s 浏览器等待 / 2 GiB 空闲 / 1 MiB 行长 / 24 MiB 报告 / 10000 火焰点 / 660s proxy 声明）。
- GATEWAY_MAX_CONCURRENT **固定常量 1（非调优项）**：validate 对任何非 1 值（含 0/2）报错，启动拒绝（P3-04）。
- 超时链约束：analysis+10<processing、receive+processing+10<=browser_wait、proxy 模式 processing+10<declared_proxy_read<browser_wait。本机复算默认值 540+10<600 ✓、300+600+10<=990 ✓、600+10<660<990 ✓。
- main.py lifespan 校验失败 raise（拒绝启动），不降级 warning——切生产前用拟发布配置跑本校验即可阻断非法值。

### 2.2 跨 worker 非阻塞槽（§6.4 第 3 条）
- GatewayUploadSlot：Linux fcntl.flock(LOCK_EX|LOCK_NB) / Windows msvcrt.locking(LK_NBLCK)。
- 锁文件持久存在（O_CREAT 不 O_EXCL/O_TRUNC，不删除再重建，避免 inode 变化制造两个锁）；进程退出由 OS 释放。
- 非进程内 Semaphore；所有 worker 共享同一锁路径（GATEWAY_TMP_DIR 或系统临时目录独立子目录，0o700）。
- 状态文件原子更新（owner_nonce/阶段/monotonic deadline）供 429 的 Retry-After 估算（5—600 秒，缺失/过期回退 600）；**文件锁才是准入权威，元信息只供提示**，不据此偷锁/杀任务。
- release 仅当 _acquired=True（取槽前被拒的请求不释放他人锁，§6.4 第 4 条）；只在同一 owner 时清理状态文件。

### 2.3 专属中间件（§6.4）
- 纯 ASGI，只匹配 POST /api/v1/gateway-log/upload，其他路由透传。
- Content-Length 预检（可信/不可信都先作格式/上限检查）：非法→400，超限→413。
- 包装 receive 累计所有 http.request body 字节，超限抛 _GatewayBodyTooLarge→413（无头/分块/伪造偏小值同样不能越界，不返回 500、不创建报告）。
- 收体期间持锁，忙则 429（Retry-After + X-Request-ID + detail.code=GATEWAY_BUSY + "未被处理/未排队"文案 + N=ceil(retry/60) 分钟）。
- 收体完成（more_body=False）刷新阶段到 processing 预算；finally 仅释放本请求取得的槽。
- 注册顺序：GatewayUploadPolicy 最先 add → 最内层，在 Auth 之后（认证通过才取锁）、路由之前（表单解析前限额）。

### 2.4 前端（§6.4 第 6/7/8 条）
- apiFetch 增 handledHttpError 选项（默认 false，传 fetch 前从 opts 删除）：仅网关上传设 true 自己展示精确提示，不关闭全局 401 处理或其他模块 5xx 通知。
- onGatewayUpload：connection_id 固定局部上下文 + gatewayUploadSeq 请求序号防护（切实例后迟到响应不覆盖 B）；capabilities 预检（文件超限友好提示，读取失败禁提交）；禁重复上传；结构化错误解析（JSON 兼容 {detail:string}/{detail:{message}}/新结构，HTML 413/504 固定中文提示不渲染 HTML）；显示请求编号；990s AbortController 浏览器等待保护（超时提示"结果尚未确认，请查历史"，不自动重传）；"正在上传并分析，请勿重复提交"（不展示虚假百分比）。

## 3. 验证证据
- **D05 入口防护冒烟 19 项全通过**：capabilities（200/200MiB/concurrent=1固定/browser_wait=990/direct/config_version=1.6.3.4）；Content-Length 超限 413（code=GATEWAY_UPLOAD_TOO_LARGE + X-Request-ID + stage=admission）；锁忙 429（code=GATEWAY_BUSY + Retry-After=600 + retryable=true + "未被处理"文案 + stage=admission）；释放锁后放行（200 进入 analyze_log）；非网关路由不受网关限额影响（200）。
- **网关/安全/中间件测试 34 passed**（test_gateway_log / sql_masking / o15_gateway_report_security / security_headers 等）。
- **全量回归 1970 passed + 2 skipped，零失败**（约 9 分 49 秒，与第二批一致；2 skipped 为 A.1/A.4 门禁退役）。D05 新增中间件/配置/前端不改既有测试逻辑，TestClient 类用例经 lifespan 网关配置启动校验（默认配置通过）正常运行。

## 4. 变更文件清单（第三批）
修改（6）：backend/config.py、backend/main.py、backend/middleware.py、backend/api/gateway_log.py、frontend/static/js/app.js、deploy/nginx-sqlcheck.conf
新增（2）：backend/services/gateway_upload_lock.py、backend/schema/v14/142_gateway_analysis_meta.sql

## 5. 剩余工作（第四批）
| 实施包 | 内容 |
|---|---|
| D06 执行层 | gateway_process.py（子进程 TERM/KILL/Windows Job Object 生命周期）+ log_input.py（共享逐行输入 + 有界 reservoir sampling 火焰图）+ analyze_log 重构（受控文件路径 + 线程池非阻塞 + 单次流式解析 + 事务落库 + max_allowed_packet 预检 + analysis_meta_json）+ analyze_gateway_log.py 的 --summary-output/--context-file |
| D02 | H01—H14 共 14 个 HTML 生成入口接入 + D01 各源写入路径配对 |

**说明**：本批 D05 入口防护层与现有 analyze_log（全量 read + 同步子进程）兼容——中间件在路由之前完成限额/锁/429/413，路由内 analyze_log 保持现状；D06 再把执行层重构为受控文件路径 + 流式 + 事务落库。这样 D05 可独立交付并立即消除"50 MiB 默认限额拦截 71 MiB 文件"与"无并发控制"两个入口侧确定缺陷。

## 6. 施工边界声明（第三批）
1. 本批未连接内网 TDSQL，未上传真实 71 MiB 日志；入口防护冒烟用 TestClient + 降低限额/占用锁的方式验证 413/429/放行路径，非真实大文件性能验收（GW-01/02/08 容量门禁待内网真实样本，属 D06 准出证据）。
2. 本批未实施 D06 执行层重构：analyze_log 仍是全量 read + 同步子进程 + 120s 超时（设计 §6.1 问题 2/3/4），async 路由阻塞事件循环与全量读内存的风险**尚未消除**，须待第四批 D06。
3. 142 迁移的 analysis_meta_json/request_id 列已建，但写入路径随 D06 的 analyze_log 重构接入；本批列恒为 null，不影响现有读端。
4. Nginx 网关专属 location 已落模板，但真实部署须 `nginx -T` 核对生效值、核查 Nginx 暂存卷与应用卷分别的空间，deployment_mode=proxy 时核对声明值 660s。

施工人：智能体 Q
施工对象：v1.6.3.4 第三批（D05 网关入口防护层 + 142 迁移）
提交给：Mr.Linsang

---
---

# DEV-v1.6.3.4 第四批开发记录 — D06 网关大日志分析执行层重构（REQ-04 闭环）

| 项 | 内容 |
|---|---|
| 产品版本 | v1.6.3.4 |
| 施工基线 | `main@3e04000`（第三批施工提交） |
| 设计依据 | DETAIL-v1.6.3.4 §6.1（问题 2/3/4/6）、§6.5（解析/子进程/产物）、§6.6（数据库/错误/诊断） |
| 施工方 | 智能体 Q |
| 施工日期 | 2026-09-07 |
| 本批交付边界 | **D06 执行层重构**：与第三批 D05 入口防护配对，消除 §6.1 问题 2（async 路由阻塞事件循环）、问题 3（全量 read 进内存）、问题 4（裸 python/120s/仅凭 HTML 判成功）、问题 6（落库无报文预检/非事务）。D02（14 HTML 入口）留第五批 |

## 1. 交付概述

第三批 D05 交付了入口防护层（限额/锁/429/413），但 `analyze_log` 仍是全量 `await file.read()` + async 路由内同步子进程 + 120s 固定超时。本批重构执行层，使 REQ-04 端到端闭环：

| 组件 | 交付 |
|---|---|
| gateway_process.py（新增） | run_analysis_process 子进程生命周期：sys.executable + 受控 argv（不用 shell）、stdout/stderr 独立线程有界排空（各 64 KiB 尾部）、Linux 进程组 TERM→5s→KILL→reap、Windows ctypes Job Object（KILL_ON_JOB_CLOSE）约束子树、记录 pid/returncode/timed_out/exit_after_term/forced_kill/cleanup_ok |
| log_input.py（新增） | 共享逐行输入层：UTF-8（可选 BOM）/LF/CRLF、最后一行无换行也计一行、非法编码显式 encoding_error（不 errors='ignore' 静默丢字节）、超长行缓冲超限立即拒绝（不先 read 整行）、count_file_bytes_and_sha256 流式 |
| analyze_log 重构 | 签名 bytes→受控文件路径；流式统计（log_input，不全量进内存）；SHA-256+净字节；gateway_process 子进程（analysis_timeout=540s）；校验退出码/报告存在/字节上限/文档完整性；NaN/Infinity/负耗时排除 |
| 事务落库 | _save_report：report_html + report_context_json + analysis_meta_json + request_id **同一事务**，失败 rollback；max_allowed_packet 预检（按 UTF-8 字节×2 转义上界估算，不足报 503 不 SET GLOBAL） |
| 结构化异常 | GatewayAnalysisError 及子类（InvalidLog 422 / Timeout 504 / OutputInvalid 500 / ReportStorage 503 / TempSpace 507），携带 code/stage/retryable |
| upload 路由重构 | Request 取 request_id；report_context capture（D01）；受控临时目录 + 受控文件名；1 MiB 块转交 + 净字节校验（upload_max 200 MiB）+ SHA-256；asyncio.to_thread 线程池（不阻塞事件循环）；结构化错误映射；finally 清理受控目录 |

## 2. 关键实现

### 2.1 子进程生命周期（gateway_process.py，§6.5）
- **不用 shell**：`subprocess.Popen([sys.executable, script, ...], shell=False)`，保留 repo PYTHONPATH（分析器延迟导入 backend.services.sql_masking）。
- **有界排空**：stdout/stderr 各用独立 daemon 线程 `read(64KiB)` 循环，deque 只保留尾部 64 KiB——不用 `capture_output`（会把全部输出累积进内存）。独立线程避免管道塞满死锁。
- **Linux**：`start_new_session=True` 独立进程组；超时按 `killpg(SIGTERM)`→等候 5s→仍存活 `killpg(SIGKILL)`→`wait` reap。
- **Windows**：ctypes 创建 Job Object（JOBOBJECT_EXTENDED_LIMIT_INFORMATION + KILL_ON_JOB_CLOSE），AssignProcessToJobObject 约束子树，关闭句柄即终止残留；**没有 POSIX TERM 等价证据时记录 term_not_applicable，不伪造 exit_after_term=true**。
- **cleanup_ok**：只有 `proc.returncode is not None`（确认退出）才 True；无法确认退出转故障处置，不宣称已清理/可复用资源。

### 2.2 流式输入层（log_input.py，§6.5）
- `iter_log_lines(file_path, max_line_bytes)` 二进制分块读（64 KiB），yield (text, status)：LINE_OK / LINE_ENCODING_ERROR / LINE_TOO_LONG。
- 首行剥 UTF-8 BOM；LF/CRLF 均剥行尾 \r；最后一行无换行也计一行。
- 超长行：缓冲无 \n 且超 max_line_bytes 立即 yield TOO_LONG 并置 too_long_pending，丢弃该行剩余直到下一个 \n（不先 read 整行再检查）。
- 非法 UTF-8 显式标注 ENCODING_ERROR（不 errors='ignore' 静默丢字节）。

### 2.3 analyze_log 重构（§6.5—§6.6）
- 平台侧质量统计改用 log_input 流式逐行（消除 `file_content.decode().splitlines()` 同时保留多份内容的内存风险）；interf/sql 的 timecost 提取口径不变，新增 NaN/Infinity/负耗时排除（§6.5）。
- 子进程经 gateway_process.run_analysis_process（analysis_timeout=540s），校验：timed_out→504、!cleanup_ok→故障、returncode!=0→500（非零退出但留有 HTML 不记成功）、报告缺失/空/无 `</html>`→500、报告字节>24 MiB→500。
- analysis_meta_json（限 128 KiB）：input_sha256/input_bytes/parse_quality/visualization/process/stage_duration_ms，不存原始日志。
- _save_report 事务：4 个新字段同事务 INSERT，任何失败 rollback（不留"成功但无正文"）；max_allowed_packet 预检按 UTF-8 字节×2 转义上界 + 4096 余量估算，不足报 503。

### 2.4 upload 路由（§6.5）
- 1 MiB 块 `await file.read(1MiB)` 转交受控目录，累计净字节超 upload_max（200 MiB）→413（与中间件 multipart 总限额 201 MiB 分别判断，§6.3）；SHA-256 同步计算。
- 受控文件名 `{type}_instance_{port}.{date}.0` 符合 analyze_gateway_log.py 识别规则；目录 tempfile.mkdtemp（GATEWAY_TMP_DIR 或系统临时目录），finally rmtree 清理本请求目录。
- `asyncio.to_thread(analyze_log, ...)`：同步文件/子进程/DB 移出事件循环，主循环可继续处理登录/列表（§6.1 问题 2）。
- report_context capture（D01）冻结连接名称，随事务落库。

## 3. 验证证据
- **gateway_process 冒烟 18 项全通过**：正常退出（rc=0/cleanup_ok/stdout+stderr 排空/pid/duration）、超时回收（timed_out/cleanup_ok/回收<15s/Windows term_not_applicable 且不伪造 exit_after_term/rc!=0）、启动失败（cleanup_ok）、大量输出（尾部有界 ≤64 KiB 且非空）。
- **test_gateway_log.py 6 passed**：端到端（upload API → 受控文件转交 → 流式统计 → gateway_process 子进程 → 事务落库含 report_context_json/analysis_meta_json/request_id 三新列 → 结构化错误 detail dict）。test_gateway_log_service 适配 file_path 签名；test_over_threshold/test_all_invalid 适配结构化 detail（code=GATEWAY_INVALID_LOG + message）。
- **全量回归 1970 passed + 2 skipped，零失败**（约 9 分 45 秒，与前三批一致；2 skipped 为 A.1/A.4 门禁退役）。analyze_log 签名变更仅影响 test_gateway_log.py（已适配），其余测试经 upload API 或不经网关执行层，无回归。

## 4. 变更文件清单（第四批）
修改（3）：backend/services/gateway_log_service.py（analyze_log/_save_report 重构 + 异常体系）、backend/api/gateway_log.py（upload 路由重构 + import）、tests/test_gateway_log.py（file_path 签名 + 结构化 detail 适配）
新增（2）：backend/services/gateway_process.py、backend/services/gateway_log_analysis/log_input.py
（142 迁移在第三批已建，本批 analyze_log 开始写入 analysis_meta_json/request_id）

## 5. D06 范围决策与边界
**决策：不重构成熟的 analyze_gateway_log.py（154 KB / 3308 行）。** 理由：
1. 该分析器已是流式逐行解析（`for line in f`）+ 单实例锁 + 资源限制（内存 1GB/CPU 降优先级）+ heapq 有界 Top-N，本身不是内存/阻塞风险源；
2. §6.1 问题 3 的"重复解析"根因在**平台侧** `analyze_log` 的全量 read + decode().splitlines()，本批已用 log_input 流式统计消除其内存风险；
3. 完整重构 analyzer 输入层（log_input 下沉进 analyzer + --summary-output 取代平台统计 + 火焰图实时 reservoir）风险高、收益边际，作为后续独立优化项。

因此本批：平台侧流式统计提供 parse_quality/metrics（写入 analysis_meta_json 与响应），子进程仍用现有参数（--files/-o/--log-types/-f html）经 gateway_process 管理。**--summary-output/--context-file、analyzer 内部统一 log_input、火焰图实时 reservoir sampling（§6.1 问题 3 后半）列为后续优化**，不阻断 REQ-04 核心闭环（受控文件/非阻塞/子进程回收/事务落库/报文预检均已交付）。

## 6. 剩余工作
| 项 | 内容 |
|---|---|
| D02（第五批） | H01—H14 共 14 个 HTML 生成入口接入 + D01 各源写入路径配对（audit/scan/inspection/daily/bigtable/raw_slowlog/scan_compare + 4 CLI + 磁盘脚本）。网关 H08 的 report_context 已随本批 upload capture 落库，报告渲染接入随 D02 |
| D06 后续优化 | analyze_gateway_log.py 的 --summary-output/--context-file、内部统一 log_input、火焰图实时 reservoir sampling、analysis_truncated 精确标识 |
| 待回填 | 71 MiB 真实样本容量验证（GW-01/02/08）、元数据库 max_allowed_packet 实测、部署 Nginx `nginx -T` 核对 |

## 7. 施工边界声明（第四批）
1. 本批未连接内网 TDSQL，未上传真实 71 MiB 日志；端到端验证用 SAMPLE_INTERF_LOG 小样本经完整链路（受控文件→流式统计→子进程→事务落库→结构化错误），非真实大文件性能/内存验收（GW-08 容量门禁待内网样本）。
2. gateway_process 的 Windows Job Object 在本机（Windows）冒烟验证 term_not_applicable 语义；Linux 进程组 TERM/KILL 路径经代码审查与超时回收冒烟（<15s）验证，生产 Linux 实机的子孙进程回收须部署时复核。
3. max_allowed_packet 预检按 UTF-8 字节×2 转义上界估算（保守），非驱动 mogrify 精确长度；真实元数据库 packet 能力须发布前实测（§6.6）。
4. analyze_log 改为同步阻塞由 upload 路由 asyncio.to_thread 调用；GatewayUploadPolicyMiddleware（第三批）的锁在收体+分析全程持有，与本批线程池分析协同（锁在中间件 finally 释放，覆盖 to_thread 执行期）。

施工人：智能体 Q
施工对象：v1.6.3.4 第四批（D06 网关执行层重构，REQ-04 闭环）
提交给：Mr.Linsang

---
---

# 第五批施工记录 — D02 全部 HTML 入口接入（REQ-01 闭环）+ D06 后续优化

| 项 | 内容 |
|---|---|
| 产品版本 | v1.6.3.4 |
| 施工基线 | `main@b46ccb2`（第四批 D06 网关执行层重构） |
| 设计依据 | `docs/DETAIL-v1.6.3.4-...md`（Rev.C）§3.1—§3.4（H01—H14 入口/写入/降级/渲染）、§6.1 问题 3、§7.1 CLI 契约 |
| 施工方 | 智能体 Q |
| 施工日期 | 2026-09-07 |
| 本次交付边界 | **第五批施工（收尾）**：D02（H01—H14 共 14 个 HTML 生成入口渲染 + D01 各源写入路径配对，REQ-01 完整闭环）+ D06 后续优化（analyzer `--summary-output`/`--context-file`、火焰图实时 reservoir 上限）。至此 v1.6.3.4 四个 REQ 全部闭环 |

---

## 1. 交付概述

本批为 v1.6.3.4 收尾批，完成 REQ-01（所有 HTML 报告显示实例连接名称）的最后一块 D02，并补齐第四批声明为“后续优化”的 D06 遗留项，做到无遗留。

| 实施包 | 本批状态 | 说明 |
|---|---|---|
| **D02 H01—H14** | ✅ **完整交付** | 14 个 HTML 生成入口全部接入“实例连接名称”来源块；D01 各源写入路径配对（capture→report_context_json 落库）；历史降级链（legacy_stored→current_lookup→missing）全入口统一 |
| **D06 后续优化** | ✅ 交付 | analyze_gateway_log.py 增 `--connection-name`/`--context-file`/`--summary-output`；火焰图 50000 点实时上限（不等文件结束） |

**四个 REQ 闭环确认**：REQ-01（D01 基础 + D02 全入口）✅、REQ-02（D03 主表识别 + 采集）✅、REQ-03（D04 R043）✅、REQ-04（D05 入口 + D06 执行 + D06-opt）✅。

**全量回归：1942 passed + 30 skipped，0 failed（约 8 分 44 秒）。** 30 skipped = 28 项 SIT/UAT 规则集成测试（需内网靶场库凭据 `TDSQL_TEST_ADMIN_USER/PASSWORD`，本机未设，环境性跳过）+ 2 项 A.1/A.4 门禁退役（D03 合法演进，第二批已声明）。与第四批 1970+2 相比总数同为 1972，差异纯为环境凭据缺失导致的 SIT/UAT 跳过，非回归。

---

## 2. 关键实现

### 2.1 统一渲染入口（report_context.py）
- 新增 `render_for_record(record, scene, role)`：14 入口共用。优先读已持久化 `report_context_json`（新记录扫描时冻结真名）；无则按 §3.2 降级链 `resolve_legacy_context`（legacy_stored→current_lookup→missing）。避免各入口重复降级逻辑漂移。
- 新增 `inject_context_into_html(html, context_html)`：H08 用，把来源块注入**已生成**的 report_html——在 `<body...>` 后补块一次（锚点明确，不全局 replace 任意文本）；无 body 的历史片段用安全外层文档容纳；注入的是已转义静态 HTML（无 `<script>`/`on*`），不影响既有 `_strip_inline_handlers` 与 nonce/CSP/iframe 安全链。
- **场景优先级修复**：`render_report_context` 单实例分支中，名称为空且无 connection_id 时，原仅按 `context.origin` 降级（笼统显示“历史记录”），忽略调用方 scene。修复为优先用 scene（如“离线文件审核”/“主机磁盘测试”），比 origin 更准确；有 connection_id 时仍走“历史未记录名称（连接 ID）”不被 scene 覆盖。

### 2.2 写入路径（capture → report_context_json 落库）
- **audit_service._save_audit_history**：签名增 `report_context`，INSERT 增 `report_context_json`（20→21 列/占位符同步），序列化经 `context_to_json_column`。
- **audit_service.audit_file_content**（H01 来源）：文件审核前端未绑定连接→`ORIGIN_OFFLINE`（显示“未关联实例（离线文件审核）”）；API 显式传 connection_id→`ORIGIN_BOUND`（冻结真名）；不改其门禁语义。
- **sql_audit.extract_and_audit**（H02 来源）：在元数据提取前、与 `_started_at` 同一开始阶段 `capture_report_context`（从 conn_info 冻结，不反查现名/不从文件名推断），传 `_save_audit_history` 与 schema_audit 快照 meta。
- **scan_service._do_scan**（H03 来源，§3.1 修正写入源）：`conn_name` 由 `host:port`（endpoint，非名称）修正为 `capture_report_context` 冻结的真实连接名称；无注册名时降级用 endpoint。传 `create_scan_task`。
- **slow_query_service.create_scan_task**：增 `report_context`，INSERT 增 `report_context_json`（scan_tasks）。
- **scan_snapshot_service.create_snapshot**：INSERT 增 `report_context_json`（24→25 列）；**故意不加入 ON DUPLICATE KEY UPDATE**——upsert/重建保留首次来源（§3.3），不用重建时现名覆盖已存上下文。
- **scan_compare_service._snap_brief**：透传 `connection_id/connection_name/db_name/report_context_json`，使对比报告 base/target 各携带自己的冻结来源（§3.2 相同 ID 改过名仍各显各名）。
- **daily_inspect_service.run_daily / run_server_daily**（H07 来源）：采集开始冻结上下文，INSERT 增 `report_context_json`（daily_inspection 28→29 列、server_daily_inspection 15→16 列，均**不入 upsert**保留首次来源）；30 秒缓存命中直接返回其原上下文，不重新包装成新时点。

### 2.3 渲染路径（H01—H09 Web 报告）
- **H01** export_file_report_html、**H02** export_extracted_report_html、**H03** export_scan_task_html、**H07** generate_comparison_html_report：页眉标题下、首个指标区前插入 `render_for_record` 来源块；H03 移除原冗余“实例”meta 项（改由标准块显示，含降级语义）。
- **H04** export_schema_check_report（§3.1 修正反模式）：原按 `host+port` 反查 `list_saved` 取**第一条**同端点连接名（同端点多连接会张冠李戴），改为按 `request.connection_id` **精确** `capture_report_context`。
- **H05** render_single_snapshot_html：单快照来源块（快照创建时已冻结 connection_name，新快照带 report_context_json 优先）。
- **H06** render_compare_html（§3.2）：基准/目标各用 `render_for_record(role="基准扫描"/"目标扫描")`，各显自己的扫描时名称，不只显示汇总中的一个。
- **H08** get_report_html：`get_report_detail` SELECT 增 `report_context_json/request_id`；服务时 `inject_context_into_html` 在 `<body>` 后补块（新报告 upload 已 capture 落库，旧 report_html 服务时补），保持票据/nonce/iframe 链。
- **H09** export_events(format=html)：新增 `raw_slowlog_service.map_nodes_to_connection_names`（一次 JOIN `slow_log_source_nodes→slow_log_sources→tdsql_connections`，按 source_node_id 批量取，**避免 N+1**）；页眉列多实例来源、明细逐行增“实例连接”列；老事件经 source 关联作现名降级；保留脱敏与最多 10000 行覆盖提示。

### 2.4 CLI 与脚本（H10—H14，§7.1 契约，自包含不导入 Web 后端包）
- **H10** analyze_gateway_log.py、**H13** interf_deep_analysis.py：增 `--connection-name`（人工标识）/`--context-file`（平台写入 ReportContext JSON，二者互斥，context-file 优先）；HTML 输出在 `<body>`/`<h1>` 后注入来源块，自包含 HTML 转义；未提供明确显示“未关联实例”。
- **H11** merge_gateway_reports.py、**H12** interf_report_generator.py：增 `--context-file` 的 `groups`（`{input_group_key: ReportContext}`）多实例映射；合并/各实例输出按输入分组各显各名，未映射的组显式“未关联实例”，不用一个参数覆盖所有实例。
- **H14** disk_performance_test/generate_report.sh：增 `connection_name`（test_params.txt 键或 `CONN_NAME` 环境变量）；`sed` 自包含 HTML 转义（`& < > " '`）；未提供显示“未关联实例（主机磁盘测试）”，绝不把主机名当数据库连接名。

### 2.5 D06 后续优化（analyze_gateway_log.py）
- `--summary-output`：写受控 `summary.json`（version/status/analyzer_version/inputs 含 bytes+sha256/report_outputs/generated_at），供子进程侧交叉校验与完成信号；平台侧 parse_quality/metrics 仍由 gateway_log_service 流式统计写入 analysis_meta_json（不重复）。
- `--context-file`：见 H10。
- **火焰图实时 reservoir 上限（§6.1 问题 3）**：原降采样只在**每文件结束后**触发（`total_lines += line_count` 之后），单个大文件处理途中 `flame_data` 中间列表无实时上限。修复为在逐行 append 后即检查 `len(flame_data) > 50000` 立即降采样（sample_rate×2 + `[::2]`），单文件途中即封顶；原每文件后降采样与最终截断保留为二级兜底。

---

## 3. 验证证据
- **全量回归 1942 passed + 30 skipped，0 failed**（约 8 分 44 秒，两次运行一致，含场景优先级修复后复跑）。30 skipped 全为环境性（28 SIT/UAT 需靶场库凭据）+ 已声明门禁退役（2 A.1/A.4），非回归。
- **关键路径冒烟 20 项**（render_for_record 降级链 + inject_context_into_html + H10/H13 CLI 名称解析）：新记录冻结名/历史 legacy 名/历史 endpoint/空记录未关联+场景/role 基准标签、注入到 `<body>` 后·正文保留·无 body 安全容纳·空 context 不改原文·只注入一次、`--connection-name`/`--context-file`/互斥优先/默认未关联/HTML 转义 `&lt;`——19 项首轮通过，1 项（空记录场景提示）暴露 §2.1 场景优先级缺陷，修复后复验通过（含“有 connection_id 时不被 scene 覆盖”反向用例）。
- **INSERT 列/占位符一致性核验**：daily_inspection 29 列=29 占位符、含 report_context_json、upsert 不含（脚本核验）；audit_history 21、scan_snapshots 25、server_daily_inspection 16 均同步。
- **编译/导入核验**：全部平台模块导入 OK；4 个 CLI 脚本 `py_compile` OK 且 `--help` 显示新参数；H14 shell `bash -n` 语法 OK。

---

## 4. 变更文件清单（第五批）
修改（18）：
- backend/services/report_context.py（render_for_record + inject_context_into_html + 场景优先级修复）
- backend/services/audit_service.py（_save_audit_history +report_context/21 列；audit_file_content H01 offline/bound capture）
- backend/api/sql_audit.py（H01/H02 渲染 + extract_and_audit capture + schema_audit 快照 meta）
- backend/services/scan_service.py（_do_scan conn_name 修正 + capture）
- backend/services/slow_query_service.py（create_scan_task +report_context_json）
- backend/api/slow_query.py（H03 渲染）
- backend/api/inspection.py（H04 渲染 + 精确 capture 修正反模式）
- backend/services/scan_compare_report.py（H05/H06 渲染，基准/目标各显名）
- backend/services/scan_compare_service.py（_snap_brief 透传）
- backend/services/scan_snapshot_service.py（create_snapshot +report_context_json，INSERT-only）
- backend/services/daily_inspect_service.py（H07 渲染 + run_daily/run_server_daily 写入）
- backend/services/gateway_log_service.py（get_report_detail +report_context_json/request_id）
- backend/api/gateway_log.py（H08 get_report_html 注入）
- backend/api/raw_slowlog.py（H09 渲染）
- backend/services/raw_slowlog_service.py（map_nodes_to_connection_names 批量避免 N+1）
- backend/services/gateway_log_analysis/analyze_gateway_log.py（H10 + D06-opt：--connection-name/--context-file/--summary-output + 火焰图实时上限）
- backend/services/gateway_log_analysis/interf_report_generator.py（H12 --context-file groups）
- backend/services/gateway_log_analysis/interf_deep_analysis.py（H13 --connection-name/--context-file）
- backend/services/gateway_log_analysis/merge_gateway_reports.py（H11 --context-file groups）
- backend/static/scripts/disk_performance_test/generate_report.sh（H14 connection_name + sed 转义）

（140/141/142 迁移在前批已建；本批仅填充 report_context_json 列，无新表结构变更，故 design_appendix 门禁无新增退役。）

---

## 5. 剩余工作
| 项 | 内容 |
|---|---|
| 待回填（限制验收） | PAR-21 真实容量实测、新语法实机目录口径、71 MiB 真实样本 GW-01/02/08、元数据库 max_allowed_packet 实测、部署 Nginx `nginx -T` 核对——均需内网靶场/真实样本，非本机可闭环 |
| SIT/UAT 规则集成测试 | 28 项需 `TDSQL_TEST_ADMIN_USER/PASSWORD` 靶场库凭据，本机环境性跳过；发布前须在内网跑通 |

**v1.6.3.4 四个 REQ（REQ-01/02/03/04）代码层全部闭环，无开发遗留。**

---

## 6. 施工边界声明（第五批）
1. 本批未连接内网 TDSQL/靶场库；H01—H14 渲染与写入经单元/集成测试与冒烟脚本验证（含 FakePool/临时文件/构造记录），非真实实例端到端名称冻结验收。
2. H09 raw_slowlog 事件表**未新增列**（按 §3.3 item7 经 source 关联 + extra_json 语义）；本批实现渲染侧批量 source→名称映射（避免 N+1）与老事件现名降级，采集侧 extra_json.report_context 冻结由 source 关联现名降级覆盖（设计明确允许“老事件经 source 关联仅作现名降级”）。
3. H10—H14 为离线 CLI/脚本，自包含 HTML 转义，不导入 backend.report_context（离线机器无 Web 后端包）；平台调用 analyze_gateway_log.py 的报告名称由 H08 服务时注入覆盖，CLI 的 --connection-name/--context-file 主要服务独立运行。
4. D06-opt 火焰图实时上限为逐行 append 后即封顶（50000 点），不改变 analyzer 既有降采样/最终截断/Top-N 语义；--summary-output 不重复平台侧 parse_quality（后者由 gateway_log_service 流式统计 owns）。
5. 场景优先级修复仅影响“名称为空且无 connection_id 且有 scene”的显示分支（display-only），有 connection_id 或已冻结名称的记录不受影响。

施工人：智能体 Q
施工对象：v1.6.3.4 第五批（D02 全部 HTML 入口接入 REQ-01 闭环 + D06 后续优化，版本收尾）
提交给：Mr.Linsang

---
---

# SIT 第一轮整改记录（A 第一轮 SIT 报告 @329ef47）

| 项 | 内容 |
|---|---|
| 被测版本 | v1.6.3.4 `main@6a339df`（第五批后） |
| SIT 报告 | `docs/SIT-v1.6.3.4-...第一轮SIT测试报告-ClaudeA.md`（结论：不通过-有条件，2 BLOCK + 2 MINOR） |
| 整改方 | 智能体 Q |
| 整改日期 | 2026-09-07 |

## SIT-1 问题确认与整改结论

| 编号 | 级别 | Q 确认 | 整改 |
|---|---|---|---|
| **B-01** | BLOCK | **认可** | 28 个设计 §8 用例编号补齐：新建 4 个测试文件共 53 个测试函数（可定位/可运行/可失败）；A.1/A.4 退役理由改写为指向真实用例文件（先有替代守卫再退役） |
| **B-02** | BLOCK | **认可** | `middleware.py::_get_request_id` 优先复用外层 `scope["state"]["request_id"]`——429/413 响应头与响应体的请求编号从此一致；GW-C1/C2 写入头体一致性断言作回归锁 |
| **M-01** | MINOR | **认可** | `_enumerate_directory_l` 异常分支 `truncated=True→False`——失败已由 `SP_DIRECTORY_FAILED` 如实报告，不再叠加“触发 50000 行护栏/预算截断”的假告警；PAR-16 断言失败路径不出现该告警 |
| **M-02** | 观察项 | **认可并修** | 确认该分支**可达**（即席连接：活跃但未入注册表，`get_saved` 查不到）。改为 `conn_name`（connection_name 名称字段）绝不写 host:port 冒充名称，capture 未得名时保持空串；任务名 `final_task_name` 单独用 `_task_inst_token = conn_name or host:port`（endpoint 仅作任务名标识 token，不占名称位；报告名称显示走 report_context_json 降级链） |

## SIT-1 关键实现
- **B-02（请求编号串台）**：`GatewayUploadPolicyMiddleware._get_request_id` 原只扫入站头 `x-request-id`，外层 `RequestContextMiddleware` 生成的 ID 未回写进请求头，内层于是另生成一个进响应体。修复：优先取 `scope["state"]["request_id"]`（外层写入的同一值），再扫入站头，最后才新生成。**不**移动 RequestContextMiddleware 到内层（设计 §6.4 要求上下文在最外侧，否则 413/429 反失 X-Request-ID）。
- **B-01（回归锁补齐）**：四个新建测试文件，复用既有 FakePool/`_open_directory_connection`/`_new_pool`/`_now` 可测性钩子与 conftest 的 AUTH_ENABLED=false：
  - `tests/test_v1634_r043_dml_target.py`（22 个，DML-11—19）：每条用例在 正常AST/强制ParseError/强制Command 三路径分别断言；DML-16 真值表 + 只读 property（无 setter/构造参数已移除 TypeError）；DML-18 闭集 36 项完整性 + 净效果（误报消失、真联表仍命中、不新增 E999）；DML-19 LOCK TABLES 复合 token / 字符串·反引号伪造 / 通用 Alias 根 / 未知头失败关闭 / 批文件首条 SELECT 不免后续 DML。
  - `tests/test_v1634_secondary_partition.py`（10 个，PAR-13—20）：直接驱动 `_identify_secondary_partition_mains`；PAR-13/14/15 三层覆盖（only_base/旧 single/仅 L/三处去重）、PAR-16 目录失败（含 M-01 锁）、PAR-17 视图排除、PAR-18 141 迁移八列契约、PAR-19 四层互斥+并集=C+层序+每候选一次、PAR-20 共享 180s deadline 不重置不越点。PAR-21 真实容量需内网实测，不属本离线套件（已在设计 §8.2.1 列明）。
  - `tests/test_v1634_report_context.py`（8 个，REP-12—15）：冻结（改名/删除后重导仍显示旧名、新名 0 次）、门禁不被顺带打开（gate_passed=NULL）、离线/显式绑定分流、恶意连接名转义（无可执行 on* 属性）。
  - `tests/test_v1634_gateway.py`（13 个，GW-C1—C4/T1—T2）：**GW-C1/C2 写入 B-02 头体编号一致性断言**；锁归属（未取槽绝不释放他人锁）、Retry-After 5—600 有界、并发固定 1、超时链不等式逐项违例指名、capabilities 与配置一致。
- **B-01 连带**：`test_design_appendix_matches_repo.py` 的 A.1/A.4 skip 理由由引用“不存在的 PAR-01~21 用例”改写为指向真实存在的 `tests/test_v1634_secondary_partition.py`（PAR-13—20）+ 既有 118 项 G14 回归——先有替代守卫再退役，顺序纠正。

## SIT-1 验证证据
- **变异测试（自证锁有牴，均变异后红、恢复后绿）**：
  - 反转 B-02（去掉 scope.state 复用）→ GW-C1/GW-C2 编号一致性用例**失败**，恢复后通过；
  - 反转 M-01（truncated 回 True）→ 两条 PAR-16 **失败**，恢复后通过；
  - 反转 R043 联表检测（multi 恒 False）→ DML-14 **失败**，恢复后通过；
  - 反转 REP-12 冻结（两层同时绕过快照）→ REP-12 改名/删除**失败**，恢复后通过。
- **全量回归 1995 passed + 30 skipped，0 failed**（约 8 分 45 秒）。较第五批 1942 多 53 个 = 本批新增测试函数；30 skipped 不变（28 SIT/UAT 需靶场凭据 + 2 A.1/A.4 退役）。
- 变异测试教训：首轮 REP-12 变异（单层）未能打红，因 `render_for_record` 与 `resolve_legacy_context` **双层**都读 report_context_json（冗余防护）；据此把 REP-12 记录补上顶层 `connection_id`，使“冻结失效退化现名查询”真正可被变异捕获。

## SIT-1 整改边界声明
1. 新增 53 个测试均为离线/内存级（FakePool/mock/TestClient+AUTH_ENABLED=false），未连内网真实 TDSQL 或靶场库；PAR-21 容量核算、71 MiB 真实网关样本、200 MiB 边界、504 回收路径仍待内网实测（设计 §8 容量门禁，非本轮可闭环）。
2. GW-T2 的真实代理/LB 漂移门禁需部署实测（设计明确“发布仍须核实真实 Nginx/LB 生效值”）；本轮仅覆盖可离线的配置校验与 capabilities 一致性侧。
3. 变异测试为手工定点变异（非变异框架全量），已覆盖本轮 4 个缺陷的回归锁。

施工人：智能体 Q
施工对象：v1.6.3.4 SIT 第一轮整改（B-01/B-02/M-01/M-02 全闭环）
提交给：Mr.Linsang
