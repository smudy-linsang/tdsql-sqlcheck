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
