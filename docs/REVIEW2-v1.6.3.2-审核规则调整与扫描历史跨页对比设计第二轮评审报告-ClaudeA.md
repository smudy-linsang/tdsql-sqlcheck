# REVIEW2-v1.6.3.2 审核规则调整与扫描历史跨页对比设计说明书 第二轮评审报告

| 项 | 内容 |
|---|---|
| 被评审文档 | `docs/DESIGN-v1.6.3.2-…详细设计说明书.md` **Rev.B**（提交 `ab9494a`，1098 行，较 Rev.A 净增 154 行） |
| 上一轮 | `REVIEW1-…第一轮评审报告-ClaudeA.md`（`7a68e4c`，结论：不通过，2 P1 + 7 P2 + 6 P3） |
| 编写方 | 智能体 O |
| 评审方 | 智能体 A |
| 评审轮次 | 第二轮设计评审 |
| 评审日期 | 2026-09-03 |
| 代码基线 | `main` / `ab9494a`（应用代码与 Rev.A 锚定的 `03ac422` 一致，本轮两个提交只动文档与部署件） |
| 评审方式 | ① 第一轮 15 项逐条验收；② Rev.B **新增内容**的独立实证复核（不假定新写的就是对的） |
| **评审结论** | **通过（有条件）。2 项 P1 全部关闭，7 项 P2 全部关闭；本轮新发现 3 P2 + 2 P3，均为逐字文本订正，无结构性问题。订正后不需要第三轮完整评审，我做一次定点确认即可。** |

---

## 0. 先更正我自己的一处错误

**P3-05 我判错了，O 的反驳是对的。**

我在第一轮写"实测仓库没有 Playwright 依赖声明（`requirements.txt`、`pyproject.toml` 均无）"。
实际情况：

```text
pyproject.toml:23-33
[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24.0",
    "httpx>=0.27.0",
    # UAT3-O-G14-01：G14 前端异步所有权行为级测试…
    "playwright==1.62.0",
]
```

且 `git show 03ac422:pyproject.toml` 逐字相同——**这段内容早于我的评审基线**，
`tests/test_release_dependency_boundary.py::test_dev_extra_pins_playwright_exactly`
还专门把这个契约钉住了。我当时只 grep 了 `requirements*.txt`，却把结论写成"`pyproject.toml` 也没有"，
那是一句没有验证过的断言，出现在一份以"每条都实证"为前提的报告里。

O 拒绝 P3-05 并给出行号与提交依据，处理方式正确。**§10.5 保持"复用既有依赖"不变，不新增依赖任务。**

---

## 1. 第一轮 15 项验收结论

| 编号 | 上轮问题 | O 的处置 | 我的验收 |
|---|---|---|---|
| **P1-01** | R121 bare `MAXVALUE` 卡在 sqlglot，只改 token 消费器不可达 | 接受，选方案 A | ✅ **关闭**（§1.1） |
| **P1-02** | `rule_configs` 无消费者，v14 迁移无收益 | 接受，删除迁移 | ✅ **关闭**（§1.2） |
| P2-01 | `LIMIT 2000 OFFSET 1` 判定表行错误 | 接受 | ✅ 关闭 |
| P2-02 | 硬编码 119 清单不完整 | 接受并扩大核查 | ⚠ **基本关闭，残留 1 处**（本轮 N-03） |
| P2-03 | R011/R120 覆盖 ALTER 属扩围 | 接受，增 REQ-01A | ✅ 关闭 |
| P2-04 | R035 跨表上下文属扩围且图纸不足 | 接受，增 REQ-05A + §4.5.4 | ✅ 关闭 |
| P2-05 | 未指名现成的规范化类型字段 | 接受 | ✅ 关闭 |
| P2-06 | 门禁双向变化未量化 | 接受 | ✅ 关闭 |
| P2-07 | 集中式零覆盖未登记 | 接受 | ✅ 关闭 |
| P3-01 | 两参数 LIMIT 的 AST 字段陷阱 | 接受 | ⚠ **方向对，字段名写错**（本轮 N-01） |
| P3-02 | `limit_offset` 正则边界 | 接受，列 OUT-10 | ✅ 关闭 |
| P3-03 | 四表共用 `cmpTableRef` | 接受 | ✅ 关闭 |
| P3-04 | 退出登录清理无落点 | 接受 | ✅ 关闭 |
| P3-05 | Playwright 依赖 | **不接受** | ✅ **O 正确，我错误**（§0） |
| P3-06 | ALTER REORGANIZE 与 ADD 失败形态不同 | 接受 | ✅ 关闭 |

### 1.1 P1-01 关闭核验

Rev.B 采用我给的方案 A，并把关键约束写死了：

* §4.7.3 第 8 条：「扫描结果在 AST try/except **之前**写入 `parsed.secondary_partition`，
  或保证在正常 AST、Command 降级、ParseError 提前返回三条出口均被写入；
  **严禁放在 `_retry_ast is not None` 条件内**」——这正是原设计缺的那一条；
* §4.7.3 第 9 条：「R121 只读 `parsed.secondary_partition.maxvalue_partitions`，不读 AST、不读 `raw_sql`」；
* §4.7.3 第 4 条：bare 与单元素括号两种形态归一为同一个 `LESS_THAN_MAXVALUE` 指纹；
* §4.7.5 给出了完整的**用户可见结果矩阵**，逐条回答了我在评审报告 §4.1.5 提的三个问题；
* §4.7.5 明确**不采用方案 B**，理由与我给的一致（会扩大最敏感恢复门禁的爆炸半径，
  并让集中式语句从 E999 变成静默通过）；
* RISK-17 登记「关闭 bare KFN 另立课题」。

我逐条复测了 §4.7.5 矩阵里可验证的行，全部与实测一致：

| §4.7.5 断言 | 我的实测 | 结论 |
|---|---|---|
| CREATE 二级 bare MAXVALUE 当前有 E999 | `err=有: Expecting (. Line 1, Col: 245` | ✅ |
| CREATE 二级 `(MAXVALUE)` 当前无 E999 | `err=无`，列数=2 | ✅ |
| ALTER ADD 两种形态当前均 E999 | 均 `Expecting ). Line 1, Col: 43` | ✅ |
| ALTER REORGANIZE 当前为 Command、无 E999 | `err=无`，sqlglot 提示 "Falling back to parsing as a 'Command'" | ✅ |

另外我复核了方案 A 的可行性前提——`checker.py:177` 的规则循环在 `parse_error` 存在时**照常执行**
（E999 是 append 到 `violations`，不 return），所以"同一语句同时产出 E999 与 R121"在现有引擎上成立。**方案 A 可落地。**

### 1.2 P1-02 关闭核验

* §6.4 标题直接改为「本期不新增迁移」，正文写明 `rule_configs` 是只写目录快照、
  产品 API/审核执行/规则集覆盖/前端规则页均不读取；
* §3.2 补上"未来若要启用该表作为产品读源，必须另立设计，先定义'内置默认值'与'管理员覆盖值'的分栏"
  ——这正是我担心的"替未来语义提前占位"，O 把它变成了显式约束；
* §10.3 用「`rule_configs` 启动回归」8 条取代了原来的 8 条迁移测试；
* §11.1 第 7 步改为"启动初始化只补插 R120/R121，**不执行新迁移**"；
* RISK-11 改为技术债登记；§13 完成定义改为"本期没有新增迁移"。

**全文残留检查**：`grep "v14|140_rule_catalog|schema/v14"` 命中 5 处，逐条查看**全部是"本期不新增"的否定式表述**，
没有一处仍在规划迁移。✅ 清理干净。

### 1.3 其余 P2/P3 的关闭要点

* **P2-05**：§4.1.2 第 2 条已改为「CREATE 路径的 `parsed.column_types[i]["type"]`，
  或 ALTER 新通道 `parsed.alter_column_types[i]["type"]`，严格等于大写归一值 `TEXT`」——字段指名到位，
  且删掉了会引导多余 `.upper()` 的"大小写不敏感"表述。
* **P2-04**：§4.5.4 用一张"必答项/定版"表把我列的 9 项全部答完，且函数名可核对——
  我逐个验证了 `RuleChecker.audit_sql`(checker.py:120)、`RuleChecker.audit_file`(227)、
  `audit_service.audit_file_content`(260)、`/extract-and-audit`(sql_audit.py:251→353 调用 `audit_file_content`)
  **全部存在且调用链属实**。`audit_file` 当前确实是循环调 `audit_sql`（每句在内部解析），
  所以"下沉 `_audit_parsed()` 避免二次解析"这个处方是必要且正确的。
  保留键 `__r035_cross_table_columns__` 走现有 `audit_sql(table_metadata=...)` 形参，接口不用改。
* **P2-06**：§10.2 新增 strict/normal 五行矩阵，方向与我实测的 `gate_service` 口径一致
  （INFO 不计数 → TEXT 放宽；R120 ERROR → 两种策略都收紧）；RISK-10A/B/C 三条分列。
* **P2-07**：RISK-16 写明"不能用 R031 对函数的残余覆盖掩盖其他对象缺口"，并设 DBA 书面确认门禁。
* **P3-03/P3-04**：§7.4 补了互斥挂载、`?.` 空值与方法存在性保护、卸载窗口、`doLogout()` 落点，
  §9.2 也点名了 `doLogout()`。

---

## 2. 本轮新发现

Rev.B 新增的内容我按"新写的也要实证"重新过了一遍，发现 3 项 P2、2 项 P3。全部是逐字文本订正，无结构性问题。

### 2.1 N-01（P2）：§5.3 把 offset 的取值写成了 `Limit.offset`，而该属性不存在

**设计原文**（§5.3 第 1 条与第 3 条）：

> SQLGlot 两参数语法 `LIMIT offset,count` 的 count 位于 `Limit.expression`、**offset 位于 `Limit.offset`**，必须先检查 `offset`；
> offset 路径：只要 **`Limit.offset` 非空**，就设置 `verifiable=false`…

**实测（sqlglot 30.14.0）**：

```text
exp.Limit.arg_types = {'this': False, 'expression': True, 'offset': False,
                       'limit_options': False, 'expressions': False}
hasattr(exp.Limit, "offset") = False

UPDATE t SET a=1 WHERE id>0 LIMIT 1, 2000
  lim.offset             -> AttributeError: 'Limit' object has no attribute 'offset'
  lim.args.get("offset") -> Literal(this=1, is_string=False)
```

`offset` 只是 `arg_types` 里的一个 **arg key**，`Limit` 类上**没有同名属性访问器**。
照字面实现 `lim.offset` 会抛 `AttributeError`。

**后果不是"报错就发现"，而是被静默降级成一条噪声告警。**
`checker.py:193-200` 的规则循环把 `rule.check()` 的任何异常兜成一条 WARNING：

```python
except Exception as e:
    violations.append(Violation(rule_id=rule.rule_id, severity="WARNING",
                                message=f"规则 {rule.rule_id} 执行异常: {str(e)}"))
```

于是**每一条带两参数 LIMIT 的分片表 UPDATE/DELETE 都会得到一条
"规则 R058 执行异常: 'Limit' object has no attribute 'offset'"**——
R058 的真实判定完全失效，用户看到的是内部错误字符串。
这恰好是 §5.3 这一节写出来要防的那个失效面。

顺带一个容易误推广的事实：**同一个 offset 在不同节点上的位置不同**。

```text
UPDATE …  LIMIT 1, 2000   → offset 在 Limit.args["offset"]      （Literal）
SELECT …  LIMIT 1, 2000   → offset 在 Select.args["offset"]     （Offset 节点，不在 Limit 上）
```

R058 只处理 UPDATE/DELETE，但设计里不写清楚，实施者很可能照 SELECT 的心智模型去找。

**整改（逐字）**：§5.3 第 1 条与第 3 条中的 `Limit.offset` 全部改为
`Limit.args.get("offset")`，并在该节末尾补一句：

> `exp.Limit` 只在 `arg_types` 中声明 `offset`，**类上没有 `.offset` 属性访问器**
> （实测 `hasattr(exp.Limit,"offset") is False`，直接写 `lim.offset` 抛 `AttributeError`，
> 且会被 `checker.py` 的规则异常兜底降级成一条 "规则 R058 执行异常" 的 WARNING，
> 使 R058 判定完全失效）。取值一律用 `lim.args.get("offset")`。
> 另注：UPDATE/DELETE 的 offset 挂在 `Limit` 上，而 SELECT 的 offset 挂在语句节点
> `Select.args["offset"]` 且是 `Offset` 节点，两者形态不同，不得互相套用。

§10.1 的 R058 第 6 条补一句断言口径：该用例除断言"走不可证明 WARNING"外，
还要断言**结果里不含 `执行异常` 字样**，把这个失效面钉死。

### 2.2 N-02（P2）：§4.7.3/§5.4 要求"无条件"再词法化一次，与既有 Rev.Q 决策冲突

**设计原文**：

* §4.7.3 第 1 条：「在进入 sqlglot 主解析前，**无条件调用**只读 `_scan_secondary_partition_policy()`」
* §5.4 第 1 条：「新增独立只读 `_scan_secondary_partition_policy(sql)`，在 AST try/except 前运行」

**冲突事实**：`parser_legacy.py:2260` 处有一条显式的既有设计决策：

```python
# Rev.Q：KFN 与全表定义完整性同源但不互相吞没；只词法化一次。
(parsed.known_fidelity_failures,
 parsed.unique_source_definitions_complete) = (
    _preflight_create_definition_status(sql_recover, self.dialect))
```

`_preflight_create_definition_status()`（1692-1714 行）**每条语句都会 `tokenize()` 一次**，
再按 `_tdsql_table_def_bounds` 提前返回。Rev.Q 当初特意把两件事合并到这一次词法化里。
Rev.B 现在要在它旁边再加一个"无条件"的独立词法化通道，等于**把每条语句的 tokenize 次数从 1 次变成 2 次**——
文件审核动辄几百上千条语句，其中绝大多数是 SELECT/INSERT，与二级分区毫无关系，却都要多付一次全量词法化。

这不只是性能问题：它**推翻了一条有名字、有理由的既有设计决策**，而 Rev.B 没有讨论这一点。

**整改（二选一，写进 §5.4 第 1 条）**：

* **方案 i（推荐）：并入现有那一次词法化。**
  把 `_preflight_create_definition_status()` 扩展为返回三元组
  `(known_fidelity_failures, unique_source_definitions_complete, secondary_partition_policy)`，
  三者共用同一份 `toks`。注意该函数当前对非 CREATE 会在 `open_idx < 0` 处提前 `return (), False`，
  扩展时要把 ALTER 分支的扫描放在提前返回**之前**按语句头分流，不能沿用现有的提前返回位置。
* **方案 ii：保留独立函数，但加语句头门闸。**
  复用 `checker.py` 已在用的 `_lex_head_words()` 思路，或直接判首个有效 token 是否属于
  `{CREATE, ALTER}`，非 DDL 语句直接返回空事实，不做第二次全量词法化。
  这样多付的成本只落在 DDL 上。

无论选哪个，§5.4 都要显式写一句：「本改动不得使单条语句的词法化次数超过既有基线（Rev.Q：一次）」，
并在 §10 增加一条回归：对一批非 DDL 语句断言词法化调用次数不增加（可用 monkeypatch 计数）。

### 2.3 N-03（P2）：119 清点仍漏 `tests_3p/` 整个目录

§9.3/§9.4 相比 Rev.A 补得很充分——我原先点名的 5 处全部进表，O 还额外补了
`test_sit_round2.py`、`test_sit_rules.py`、`test_uat_v1.py`、`test_v2_uat.py`、
`CONTEXT.md`、`docs/全系统SIT-UAT测试用例.md`、`backend/api/rulesets.py`、`smoke_test.py`，
并且正确排除了身份证号/IP 里的 `119`。这一项做得比我要求的还全。

**但整个 `tests_3p/` 目录不在清单里**，而它有一条硬断言：

```python
tests_3p/test_1_smoke.py:80   def test_sm09_rule_library_119(self, client, admin_token):
tests_3p/test_1_smoke.py:81       """SM-09 规则库加载 119 条规则"""
tests_3p/test_1_smoke.py:85       assert body["total"] == 119, f"规则数异常: {body['total']}"
```

严重度说明：`pyproject.toml` 的 `testpaths = ["tests"]`，所以 `tests_3p` **不在默认 pytest 范围内**，
不会打断 CI；但它是第三方独立测试套件（`docs/第三方独立测试报告-v1.3.0.0.md` 对应的物料），
第三方按它做验收时会当场失败，白白消耗一个验收轮次。
函数名 `test_sm09_rule_library_119` 里也带数字，改数量时要一并处理。

**整改**：§9.3 增加一行：

| 文件/类型 | 设计改动 |
|---|---|
| `tests_3p/test_1_smoke.py` | 第三方冒烟套件（不在 `pyproject.toml` 的 `testpaths` 内、不随 CI 跑）：`test_sm09_rule_library_119` 的用例名、docstring 与 `assert body["total"] == 119` 一并更新为 121 |

并在 §9.4 的清点分类规则里补一句：**清点范围必须包含 `tests_3p/`**，
它不在默认 `testpaths` 内，容易被"跑一遍测试看哪里红"的方式漏掉。

### 2.4 N-04（P3）：§4.7.5 缺"ALTER ADD 正常上界"这一行，负例期望会写错

§4.7.5 的矩阵只写了 ALTER ADD 的 bare 与括号（即含 MAXVALUE）两种形态。
**实测：`ALTER TABLE ... ADD PARTITION (...)` 今天是 E999，与 MAXVALUE 无关**——

```text
ALTER TABLE t ADD PARTITION (PARTITION p1 VALUES LESS THAN (202702))
  → err=有: Expecting ). Line 1, Col: 41      ← 正常上界照样 ParseError
```

也就是说 sqlglot 对带 `PARTITION <name>` 的 ADD PARTITION 语法整体不支持。
§10.1 的 R121 用例里要写"ALTER ADD 正常上界不命中 R121"这条负例时，
按现有矩阵会期望"干净通过"，实际结果是 E999。

**整改**：§4.7.5 矩阵补一行——

| 形态 | distributed | centralized |
|---|---|---|
| ALTER ADD **正常上界**（无 MAXVALUE） | 当前有 E999（sqlglot 对 `ADD PARTITION (PARTITION name …)` 整体不支持，与 MAXVALUE 无关），不含 R121 | 同左 |

§10.1 的 R121 负例断言口径同步为"含 E999、不含 R121"。

### 2.5 N-05（P3）：本次新增的部署手册里有一处当前态数字，需要显式归类

本轮提交 `ab9494a` 同时新增了 `docs/DEPLOY-v1.6.3.0-内网测试环境部署手册.md`，其中：

```text
:292    [PASS] 规则总数 119
```

这是 `verify_deploy.sh` 的期望输出示例。文档标题带 `v1.6.3.0` 版本戳，
按 OUT-08「不重写历史版本文档」应当**保留不改**；但它又不是"已发布版本说明"那种明确的历史件，
而是一份刚写的、会被人照着做的操作手册——实施期做全仓清点时必然命中，届时没有依据就会来回改。

**整改**：§9.4 的分类规则补一句：

> 版本戳文档（如 `docs/DEPLOY-v1.6.3.0-…部署手册.md`）中的期望输出属于**该版本的实测样例**，
> 按 OUT-08 保留原值不改；若为 v1.6.3.2 单独出部署手册，新手册用 121。

---

## 3. 复核过但无需整改的部分

以下是我本轮重新实证、确认 Rev.B 写对的地方，列出来是为了下一轮不必再查：

| Rev.B 断言 | 我的实测 | 结论 |
|---|---|---|
| §4.5.4 的四个函数名 | `checker.py:120 audit_sql` / `:227 audit_file` / `audit_service.py:260 audit_file_content` / `sql_audit.py:251 extract-and-audit → :353 调 audit_file_content` | ✅ 全部存在，调用链属实 |
| §4.5.4「`audit_file()` 循环调 `audit_sql()`，需下沉 `_audit_parsed()` 避免二次解析」 | `audit_file` 227-261 行确为两个分支各自 `for … self.audit_sql(…)`；`audit_sql` 第 140 行才 `self.parser.parse(sql)` | ✅ 处方必要且正确 |
| §4.5.4 保留键走 `table_metadata` | `audit_sql(self, sql, file_path, line_number, table_metadata=None, …)` 形参已存在 | ✅ 接口不用改 |
| §6.4「产品 API/审核执行/规则集覆盖/前端规则页均不读 `rule_configs`」 | 全仓库（含前端、脚本）命中仅 4 处：建表、`INSERT IGNORE`、`main.py:85` 调用、`smoke_test.py:92` 的 `COUNT(*)` | ✅ |
| §6.4 第 1 条「启动会由现有 `init_rule_configs()` 幂等补插两行」 | `backend/main.py:85` 每次启动调用，函数体遍历 `ALL_RULE_CLASSES` + `INSERT IGNORE` | ✅ |
| §10.2 门禁矩阵方向 | `gate_service.py:18-20` 策略定义 + `:41-42` 只统计 ERROR/WARNING，**INFO 完全不计数** | ✅ 五行方向全对 |
| §4.7.5「CREATE 二级 `(MAXVALUE)` 分布式含 R121 不含 E999」 | 项目解析器实测 `err=无`、列数=2 | ✅ |
| §4.7.5「ALTER REORGANIZE 当前为 Command、无 E999」 | 实测 `err=无`，sqlglot 提示降级为 Command | ✅ |
| §9.3「身份证/IP 里的 119 不是规则数量」 | `test_v2_platform.py:122` 是身份证号 `110101199001011234`；`02_dml_perf_sec_txn.sql:115` 是 IN 列表 | ✅ 排除正确 |
| §5.2「当前基线对 ALTER 的列类型事实为空」 | 实测四种 ALTER 列变更的 `columns/column_types/alter_actions` **全为 `[]`** | ✅ |
| §6.2 数量口径（Rev.A 已核，Rev.B 未改） | 119→121、DDL 22→23、DISTRIBUTED 14→15、仅分布式 27→30、集中式 92→91 | ✅ 沿用第一轮结论 |
| 全文无 v14 残留 | 5 处命中全部是"本期不新增"的否定式表述 | ✅ |

另有一项**方向正确、Rev.B 主动补强、值得记一笔**的地方：
§5.2 末尾写了「编码后如果发现目标私有云 ALTER 形态无法在本期安全结构化，
默认结论是 **REQ-01A 未完成、不得宣称全量交付**；只有获得需求方书面批准才允许降级…
**实现者不能自行降级**」。§4.5.4 对 REQ-05A 也写了同样口径。
这两句把"扩围项目在实施期被悄悄砍掉"这条最常见的走样路径堵住了，是我上一轮要求"给降级路径"之外的额外收敛，写得好。

---

## 4. 评审结论与准入条件

### 4.1 结论

**通过（有条件）。**

两条 P1 关得很实：P1-01 不是把我的话抄一遍，而是把方案 A 落成了可施工的约束
（"严禁放在 `_retry_ast is not None` 条件内"、三条出口都要写、R121 只读结构化字段），
还主动补了一张用户可见结果矩阵，并写明为什么不选方案 B——理由与我给的一致。
P1-02 不仅删了迁移，还把"未来若启用该表作读源必须先定义内置默认值与管理员覆盖值的分栏"
写成了约束，正好堵住我担心的"替未来语义提前占位"。

七条 P2 全部有实质落点，其中 §4.5.4 那张"必答项/定版"表把我列的 9 项一条不落地答完，
函数名可核对、生命周期可验证、降级路径带书面批准门禁——这已经超出我的要求了。

本轮新发现的 3 P2 + 2 P3 **没有一条是结构性的**：N-01 是一个字段名写法、N-02 是加一道语句头门闸
（或并入既有那一次词法化）、N-03 是清单补一行、N-04/N-05 是矩阵补一行和归类补一句。
全部是逐字文本订正，改动不触及方案本身。

### 4.2 准入条件

| # | 条件 | 判据 |
|---|---|---|
| 1 | N-01 订正 | §5.3 的 `Limit.offset` → `lim.args.get("offset")`，并补属性不存在的说明与 SELECT/UPDATE 形态差异；§10.1 R058 第 6 条补"不含执行异常"断言 |
| 2 | N-02 订正 | §5.4 第 1 条选定方案 i 或 ii 并写明；补"词法化次数不得超过既有基线"约束与对应回归 |
| 3 | N-03 订正 | §9.3 增加 `tests_3p/test_1_smoke.py` 一行；§9.4 清点范围显式包含 `tests_3p/` |
| 4 | N-04 订正 | §4.7.5 补"ALTER ADD 正常上界"行；§10.1 R121 负例口径同步 |
| 5 | N-05 订正 | §9.4 补版本戳文档的归类规则 |

**订正后不需要第三轮完整评审。** 这五项都是可逐字比对的文本修改，
O 改完直接告诉我，我做一次定点确认（只看这五处）即可放行进入编码。

### 4.3 给实施阶段的提醒（不是评审意见，是移交事项）

* 进入编码后，`main` 若前进，N-01 的 sqlglot 字段形态需按当时锁定的版本复测一次（当前 30.14.0）。
* §12 末尾的三项生产发布书面门禁（LIMIT 版本前提、DBA 接受集中式零覆盖、
  规则集/流水线负责人接受门禁双向变化）与开发无关但与发布强相关，
  建议在开工时就发起，不要留到 UAT 才启动——v1.6.3.0 的经验是这类书面确认最容易卡在最后一公里。
* REQ-01A 与 REQ-05A 是本版已承诺范围。O 已经写明"实现者不能自行降级"，
  实施期若真要降级，是需求方书面批准 + 同步改验收口径，不是实施方一句"来不及"。

