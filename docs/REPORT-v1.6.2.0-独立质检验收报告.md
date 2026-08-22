# REPORT-v1.6.2.0 TDSQL 方言解析降级导致结构类规则漏审修复 独立质检验收报告

| 质检项 | 详细内容 |
|---|---|
| **质检版本** | **v1.6.2.0** |
| **质检对象** | 核心提交 `3f9b6c5` (Q 实现)、`4a9a622` (测试断言加固)、`de21151` (A 复测结论) |
| **设计依据** | [`docs/DESIGN-v1.6.2.0-TDSQL方言语句解析降级导致结构类规则漏审修复详细设计说明书.md`](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/docs/DESIGN-v1.6.2.0-TDSQL%E6%96%B9%E8%A8%80%E8%AF%AD%E5%8F%A5%E8%A7%A3%E6%9E%90%E9%99%8D%E7%BA%A7%E5%AF%BC%E8%87%B4%E7%BB%93%E6%9E%84%E7%B1%BB%E8%A7%84%E5%88%99%E6%BC%8F%E5%AE%A1%E4%BF%AE%E5%A4%8D%E8%AF%A6%E7%BB%86%E8%AE%BE%E8%AE%A1%E8%AF%B4%E6%98%8E%E4%B9%A6.md) **Rev.B** |
| **评审依据** | [`docs/REVIEW-v1.6.2.0-TDSQL方言语句解析降级导致结构类规则漏审修复设计评审报告-Q.md`](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/docs/REVIEW-v1.6.2.0-TDSQL%E6%96%B9%E8%A8%80%E8%AF%AD%E5%8F%A5%E8%A7%A3%E6%9E%90%E9%99%8D%E7%BA%A7%E5%AF%BC%E8%87%B4%E7%BB%93%E6%9E%84%E7%B1%BB%E8%A7%84%E5%88%99%E6%BC%8F%E5%AE%A1%E4%BF%AE%E5%A4%8D%E8%AE%BE%E8%AE%A1%E8%AF%B4%E5%AE%A1%E6%8A%A5%E5%91%8A-Q.md) |
| **复测依据** | [`docs/RETEST-v1.6.2.0-TDSQL方言解析降级漏审修复独立复测报告_A.md`](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/docs/RETEST-v1.6.2.0-TDSQL%E6%96%B9%E8%A8%80%E8%AF%AD%E5%8F%A5%E8%A7%A3%E6%9E%90%E9%99%8D%E7%BA%A7%E6%BC%8F%E5%AE%A1%E4%BF%AE%E5%A4%8D%E7%8B%AC%E7%AB%8B%E5%A4%8D%E6%B5%8B%E6%8A%A5%E5%91%8A_A.md) |
| **质检结论** | **【准出（PASS）】产品代码与测试资产全部达标，准予发版** |
| **质检日期** | 2026-08-22 |

---

## 一、 验收结论概述

经过独立第三方的全量代码审查、1372 支自动化用例执行、变异注入测试、全规则语料漂移比对及生产环境 14 表回放验证：
1. **核心缺陷彻底根治**：彻底解决了 TDSQL 方言尾子句（`TDSQL_DISTRIBUTED BY HASH/RANGE/LIST(...)` 及 `BROADCAST`）导致 sqlglot 降级为 `exp.Command`，进而使列/索引信息丢失、结构类规则（R036/R037/R061 等）静默漏审并虚假显示为“通过”的 P0 级严重隐患。
2. **实现范围精准收敛**：产品代码仅在 [`backend/engine/parser/parser_legacy.py`](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/backend/engine/parser/parser_legacy.py) 增加了 30 行控制流保护代码，严格遵守九条禁令与 NG-8 施工红线，未改动任何规则实现，未改动 `raw_sql`，未改动 `AuditResult.passed` 判定模型。
3. **安全性从控制流与测试断言双重锁定**：
   - 正常解析的语句因不满足 `isinstance(ast, exp.Command)` 前置条件，**永远不会进入重试剥离分支**，对既有行为影响在控制流上证明为零。
   - `test_n1_column_named_broadcast` 与 `test_n7_unparseable_retry_keeps_command` 针对 `Command` 门与重试结果校验完成了精确断言加固，注入实验证明任何违反设计的改法均会被自动化用例即时拦截。
4. **质检结论**：**准予准出（PASS）**。

---

## 二、 变更机理与技术核验

### 2.1 缺陷根因图解

```text
[DDL 语句含 TDSQL 方言尾子句]
          │
          ▼
   sqlglot.parse_one() 不识别方言
          │
          ▼
   整条语句降级为 exp.Command
          │
          ├─► parsed.columns = []
          ├─► parsed.indexes = []
          └─► parsed.table_options = {}
          │
          ▼
   结构类规则触发内部防误报守卫 (if not parsed.columns: return None / 遍历空 indexes)
          │
          ▼
   全部规则静默跳过 ──► 报告生成 0 项违规 ──► 虚假标注为绿色 [通过] (通过率虚高)
```

### 2.2 修复代码精细审查

在 [`backend/engine/parser/parser_legacy.py`](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/backend/engine/parser/parser_legacy.py) 中实施了**降级后剥离重试机制**：

```python
# 正则仅匹配 TDSQL 方言尾子句（HASH/RANGE/LIST 与 BROADCAST）
_TDSQL_DIALECT_RE = re.compile(
    r"\btdsql_distributed\s+by\s+\w+\s*\([^)]*\)"
    r"|\bbroadcast\b",
    re.IGNORECASE,
)

# 仅在"确实已降级"且"语句含方言子句"时触发
if isinstance(ast, exp.Command) and _TDSQL_DIALECT_RE.search(sql_clean):
    try:
        _retry_ast = sqlglot.parse_one(
            _TDSQL_DIALECT_RE.sub(" ", sql_clean), read=self.dialect)
        # 仅当重试结果确实脱离 Command 状态才采纳
        if not isinstance(_retry_ast, exp.Command):
            ast = _retry_ast
    except Exception:
        pass
```

### 2.3 关键安全性质核验清单

| 安全性质 | 设计要求 | 代码实现核验 | 判定 |
|---|---|---|:---:|
| **零副作用保证** | 正常 SQL 不得受到任何正则干扰 | 必须先经 sqlglot 判定为 `exp.Command` 才触发，正常语句初次解析为 `Create/Select/Insert` 等，永不执行后续逻辑 | ✅ 合规 |
| **原始 SQL 保真** | `parsed.raw_sql` 必须保持原文 | `ParsedSQL(raw_sql=sql.strip())` 保持原始输入，供 R077/R054/R118 等正向提取分片键与广播状态 | ✅ 合规 |
| **重试防御性** | 剥离后仍无法解析时不得劣化 | `if not isinstance(_retry_ast, exp.Command)` 保证仅采纳成功解析的 AST，重试失败依然保留原 `Command` 与表名回退提取 | ✅ 合规 |
| **范围收敛性** | 严禁破坏既有规则与判定语义 | `AuditResult.passed` 语义零改动，`backend/engine/rules/` 目录零改动 | ✅ 合规 |

---

## 三、 独立测试与质检执行结果

### 3.1 全量自动化回归套件

执行整机 1372 支 pytest 自动化测试用例：
- **执行总数**：1372 passed
- **失败用例**：0 failed
- **跳过用例**：29 skipped
- **执行耗时**：276.28s (04分36秒)
- **结论**：**100% 通过，无任何历史功能回归。**

### 3.2 专项验证矩阵（14/14 全通过）

涵盖 [`tests/test_parser_tdsql_dialect_fallback.py`](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/tests/test_parser_tdsql_dialect_fallback.py) 中全部 14 支用例：

| 类别 | 用例标识 | 测试场景 | 预期行为 | 实测结果 |
|---|---|---|---|:---:|
| **D 类（方言恢复）** | `D1` | `TDSQL_DISTRIBUTED BY HASH(\`sk\`)` | 恢复解析，`columns > 0`, `indexes > 0` | ✅ PASS |
| | `D2` | `TDSQL_DISTRIBUTED BY RANGE(\`sk\`)` | 恢复解析，`columns > 0` | ✅ PASS |
| | `D3` | `TDSQL_DISTRIBUTED BY LIST(\`sk\`)` | 恢复解析，`columns > 0` | ✅ PASS |
| | `D4` | `ENGINE=InnoDB BROADCAST` | 恢复解析，`columns > 0` | ✅ PASS |
| | `D5` | `HASH(\`sk\`)` + 二级分区 `PARTITION BY RANGE` | 剥离后保留 `PARTITION BY` 正常解析 | ✅ PASS |
| | `D6` | 生产 #3 `cus_bas_corp_contact` 真实 DDL | 列提取为 25，触发 R036/R037/R061，不触发 R077 | ✅ PASS |
| **N 类（反向鉴别）** | `N1★` | 列名恰好叫 `broadcast` 的标准建表 | 初次解析为 Create，列名集合精确保留 `['id', 'broadcast']` | ✅ PASS |
| | `N2★` | 表注释含 `TDSQL_DISTRIBUTED BY HASH` | 初次解析为 Create，不触发重试，R077 正常拦截 | ✅ PASS |
| | `N3★` | 表注释含 `broadcast` 字样 | 初次解析为 Create，列名集合精确保留 `['id']` | ✅ PASS |
| | `N4★` | `SHARDKEY=sk` 传统建表语法 | 初次解析为 Create，不触发重试 | ✅ PASS |
| | `N5★` | 仅 `PARTITION BY RANGE`（无方言） | 初次解析为 Create，不触发重试 | ✅ PASS |
| | `N6★` | 残缺截断 SQL（无方言子句） | 正确识别并触发 `E999_SYNTAX_ERROR` 阻断 | ✅ PASS |
| | `N7★` | 残缺 SQL 带方言尾子句 | 剥离后仍无法解析，保留原 Command 节点，不劣于改前 | ✅ PASS |
| **G 类（前提守卫）** | `G1★` | ADJ-5 前提锁定（HASH+UNIQUE / BROADCAST+UNIQUE） | 断言 `parsed.indexes` 中 UNIQUE 条目数为 0，守住 R077 分支 | ✅ PASS |

### 3.3 变异注入实验（测试锁紧有效性验证）

通过在独立沙箱中故意注入违反设计的错误改法，检验测试套件的拦截能力：

```text
[变异 1] 移除 isinstance(ast, exp.Command) 前置条件（改为无条件剥离）：
  └── test_n1_column_named_broadcast 失败！断言精确捕获到 'broadcast' 列被误吃（列集 ['id'] != ['id', 'broadcast']）✅

[变异 2] 移除 not isinstance(_retry_ast, exp.Command) 校验（无条件采纳重试 AST）：
  └── test_n7_unparseable_retry_keeps_command 失败！断言精确捕获到 Command 文本丢失方言信息 ✅

[变异 3] 正则中移除 BROADCAST 分支：
  └── test_d4_broadcast_table 失败！✅

[变异 4] 污染 parsed.raw_sql 改为剥离后文本（违反 NG-3）：
  └── test_d6 与 R077/R054 套件多处失败！✅
```
**结论**：测试资产覆盖完备，已形成坚固的安全护栏。

### 3.4 生产 14 表回放与全量语料漂移比对

#### 1. 生产 14 表回放比对
| # | 表名 | v1.6.1.9 表现 | v1.6.2.0 实测结果 | 变动判定 |
|---:|---|---|---|:---:|
| 1 | `big_audit_trail` | R028, R029, R036, R037, R054, R077, R118 | R028, R029, R036, R037, R054, R077, R118 | 一致 ✅ |
| 2 | `big_order_log` | R028, R029, R036 | R028, R029, R036 | 一致 ✅ |
| **3** | **`cus_bas_corp_contact`** | **[通过] (漏审)** | **R028, R029, R036, R037, R061** | **★成功恢复审核** |
| 4 | `cus_corp_partner` | R028, R029, R036, R037, R077 | R028, R029, R036, R037, R077 | 一致 ✅ |
| 5 | `cus_name_list_type` | R028, R029, R036, R037 | R028, R029, R036, R037 | 一致 ✅ |
| 6~14 | 剩余 9 张表 | 各自违规项逐一匹配 | 各自违规项逐一匹配 | 一致 ✅ |

#### 2. 模拟全量 HASH 方言环境（8 张分片表）
在内网全部采用 `TDSQL_DISTRIBUTED BY HASH(...)` 的场景下：
- **改前（v1.6.1.9 及更早）**：8 张表全部被掩盖，错误显示为“通过”（漏审率 100%）。
- **改后（v1.6.2.0）**：8 张表全部恢复结构解析，每张表精准报出 2~4 条真实违规（R029/R036/R037/R061/R062/R063）。

#### 3. 语料漂移比对（201 支真实 SQL）
- **异常总数**：0
- **规则漂移**：全规则集下仅生产 #3 表发生 1 处预期变化（新增 R036、R037、R061），其余 200 支 SQL 规则判定**零差异**；R077 与 R054 专项规则**零漂移**。

---

## 四、 上线后业务表现与关键告知事项

由于 v1.6.2.0 恢复了此前被静默漏审的表结构审核能力，上线后将出现以下预期内的业务数据变动，需在发版说明中明确向用户传达：

### 1. 审核“通过率”指标将出现预期内的大幅下降
- **现场 14 表**：通过率由 `7.1% (1/14)` 变为 `0.0% (0/14)`。
- **模拟内网 HASH 表场景**：通过率由 `64.3% (9/14)` 变为 `0.0% (0/14)`。
- **原因说明**：本工具既有审核口径为“只要包含 1 条 INFO 级建议即判定为未通过”。此前由于方言导致解析降级，规则被跳过才产生了虚高的通过率。现在通过率下降是**合规检测能力恢复正常**的直接表现，并非数据库质量劣化或工具故障。

### 2. 违规项与告警数量上升属于修复目标
- 内网中大量使用 `TDSQL_DISTRIBUTED BY HASH` 的分片表，将首次检测出缺失建表时间戳（R036）、缺失逻辑删除标识（R037）、索引命名不合规（R061）等规范性建议。
- 使用 `BROADCAST` 关键字声明的广播表同样恢复全面结构审计。

### 3. 关联边界与后续规划说明
- **ADJ-7 现状**：R116/R117/R118（分片键数据类型规则）目前仅从传统 `shardkey=` 中提取字段名，暂未支持从 HASH 尾子句提取，因此对 HASH 语法表暂不触发 R116~R118（保持现状，后续排期）。
- **ADJ-3 现状**：普通唯一索引与分片键冲突存在静默失效隐患，建议在 Phase 2 优先排期修复。
- **保留现状项**：ADJ-4、ADJ-6 以及 R037 的 `status` 识别口径经用户决策，均维持现有设计。

---

## 五、 最终质检准出结论

| 验收维度 | 评估标准 | 实测状态 | 结论 |
|---|---|:---:|:---:|
| **功能正确性** | 方言表结构正常解析，规则准确命中 | 14/14 矩阵通过，#3 表成功恢复 | **PASS** |
| **非侵入安全性** | 正常 SQL 与既有规则判定零副作用 | 201 语料仅 1 处预期变更，0 异常 | **PASS** |
| **回归稳定性** | 全量单元测试与集成测试通过 | 1372 passed, 0 failed | **PASS** |
| **测试完备性** | 关键控制门与守卫断言锁定 | 变异注入 100% 拦截 | **PASS** |
| **文档与设计一致性** | 实现与 Rev.B 设计说明书逐字对齐 | 严格一致，禁令无一触犯 | **PASS** |

**综上所述，v1.6.2.0 版本已完全达到银行级质量标准与准出要求，质检验收通过（准予发版）！**
