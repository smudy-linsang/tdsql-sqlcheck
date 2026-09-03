# SIT-v1.6.3.2 审核规则调整与扫描历史跨页对比 第一轮 SIT 测试报告

| 项 | 内容 |
|---|---|
| 被测对象 | v1.6.3.2（提交 `c0e5e25`，41 文件 / +2220 −189） |
| 设计基线 | `DESIGN-v1.6.3.2-…详细设计说明书.md` **Rev.C**（`2f53de8`，经两轮评审 + 定点确认放行） |
| 开发方 | 智能体 Q（开发报告 `DEV-v1.6.3.2-…开发报告.md`） |
| 测试人 | 智能体 A |
| 测试轮次 | 第一轮 SIT |
| 测试日期 | 2026-09-03 |
| 测试环境 | 沙箱 MariaDB 10.11.14 @13306；后端 uvicorn @18800；Chromium 真实浏览器；sqlglot 30.14.0 |
| **测试结论** | **不通过。2 项 BLOCK、2 项 MINOR、1 项 NIT。R011/R120/R035/R058、数量口径、门禁矩阵、四模块跨页选择全部通过；两项 BLOCK 均集中在 R121。** |

---

## 1. 结论摘要

| 级别 | 编号 | 问题 | 影响 |
|---|---|---|---|
| **BLOCK** | **DEF-SIT-01** | **R121 对绝大多数真实分区表达式失明**：`_PARTITION_FUNCS` 只有 `("YEAR","MONTH","DAY")` 三项白名单，`TO_DAYS` / `UNIX_TIMESTAMP` / `COLUMNS(...)` / 多列表达式全部读不出 `maxvalue_partitions` | **真实 `SHOW CREATE TABLE` 输出（`RANGE (to_days(\`dt\`))`）完全漏检**——在线元数据审核是 R121 的主战场；更严重的是 `(MAXVALUE)` 括号形态下**连 E999 都没有，完全静默通过** |
| **BLOCK** | **DEF-SIT-02** | **合成 KFN 守卫的立论事实错误，且只在 `ALTER REORGANIZE` 上生效**：代码注释称"sqlglot 30.14 把 CREATE bare MAXVALUE 静默降级为 Command"，实测 8 种 CREATE 形态无一进入该分支 | **合法 MySQL DDL `ALTER … REORGANIZE … MAXVALUE` 被新判为 `E999_SYNTAX_ERROR`**，集中式实例上是纯新增误报（ERROR 级，strict/normal 双门禁均失败）；直接违反设计 §4.7.5 明文 |
| MINOR | DEF-SIT-03 | LIMIT token 回退在 AST 完好时仍多做一次全量词法化，违反设计 §5.4 写明的性能不变量 | 非 DDL 批实测 15→17 次；逐条定位为「无 LIMIT 的 UPDATE/DELETE」3→4。设计要求的验收测试按原文写会直接红 |
| MINOR | DEF-SIT-04 | 设计 §9.3 点名的 `tests/TEST_SPEC-规则覆盖与压力测试.md` **未被改动** | 仍声明"119 条 SQL 审核规则"、"规则总数: 119 文件审核已覆盖: 107"；规则覆盖基线失真，R120/R121 无覆盖目标 |
| NIT | DEF-SIT-05 | `tests/test_oracle_compat_rules.py::test_total_rules_119` 用例名残留 119（断言已是 121） | 与 `tests_3p` 已改名为 `test_sm09_rule_library_121` 的做法不一致 |

**两项 BLOCK 都只落在 R121 一条规则上；其余五项需求（REQ-01/01A/02/03/04/05/05A/06/08）全部实测通过。**

---

## 2. 测试方法

不以"跑一遍 Q 的测试全绿"为结论。所有判定都是我按设计文档的正反例表**独立构造用例、独立跑**得出的：

| 手段 | 用途 |
|---|---|
| 直接跑 `RuleChecker.audit_sql/audit_file` | 逐条复现 §4.1.4 / §4.2.2 / §4.5.2 / §4.6.3 / §4.7.4 的正反例表 |
| 直接跑 `SQLParser().parse()` | 核对 `alter_column_types` / `dml_limit` / `secondary_partition` 三个新结构 |
| 直接跑 `sqlglot` + 真实 MariaDB `SHOW CREATE TABLE` | 用**真实产物**而不是手写样例验证 R121 |
| tokenizer spy + 前后基线对照 | 验证 §5.4 的性能不变量 |
| Chromium 驱动真实前端 | 四个 module 各跑一遍 FE-01…FE-09 |
| 变异验证（临时加门闸再还原） | 证明我给的整改方案确实能把结果扳回设计口径 |

---

## 3. 通过项（实测明细）

### 3.1 REQ-01 / REQ-02：R011 与 R120（CREATE 路径）

17 条正反例全中：

| 用例 | 期望 | 实测 |
|---|---|---|
| `body TEXT` / `body text COMMENT '正文'` / `body TEXT(1000)` | R011 INFO | R011 INFO ✅ |
| `body VARCHAR(2000)` | 不命中 | `[]` ✅ |
| `body MEDIUMTEXT` | 不命中 R011，改由 R120 | R120 ERROR ✅ |
| `body TINYTEXT` / `TINYBLOB` / `JSON` | 两条拆分规则都不命中 | `[]` ✅（OUT-04 边界成立） |
| `BLOB/MEDIUMTEXT/LONGBLOB/MEDIUMBLOB/LONGTEXT` 各大小写两种写法 | 均 R120 ERROR | 10/10 ✅ |
| `blob_url VARCHAR(200)`（列名诱饵） | 不命中 | `[]` ✅ |
| `remark VARCHAR(50) COMMENT 'TEXT'` / `COMMENT 'LONGTEXT'`（注释诱饵） | 不命中 | `[]` ✅ |
| `a TEXT, b BLOB` 同句 | R011 INFO + R120 ERROR，互不吞并 | 两条都在 ✅ |

### 3.2 REQ-01A：ALTER 列类型通道（评审确认的扩围）

15 条全中，`parsed.alter_column_types` 结构与设计 §5.2 逐字一致：

| ALTER 形态 | 实测 |
|---|---|
| `ADD [COLUMN] body TEXT` | R011 INFO ✅ |
| `MODIFY [COLUMN] body LONGTEXT / MEDIUMBLOB` | R120 ERROR ✅ |
| `CHANGE [COLUMN] a b TEXT / LONGBLOB` | R011 / R120 ✅（且记录的是**新列名** `b`） |
| `ADD COLUMN a TEXT, ADD COLUMN b BLOB` 多 action | 两条都命中，顺序保留 ✅ |
| `DROP COLUMN` / `CONVERT TO CHARACTER SET` / `ALTER COLUMN SET DEFAULT` / `RENAME COLUMN` / `COMMENT='LONGTEXT'` | 集合为空、不命中 ✅（OUT-09 边界成立） |

### 3.3 REQ-05 / REQ-05A：R035 跨表类型一致

§4.5.2 的九行比较表 **9/9 全中**，另加 `INT vs BIGINT`：

| 对比 | 期望 | 实测 |
|---|---|---|
| `VARCHAR(32)` vs `VARCHAR(128)` / `CHAR(8)` vs `CHAR(32)` / `DECIMAL(10,2)` vs `(18,4)` / `DATETIME(3)` vs `(6)` / `INT(11)` vs `INT` / `INTEGER` vs `INT` | 一致（不报） | 全部不报 ✅ |
| `INT UNSIGNED` vs `INT` | 不一致 | 报，且消息显示 `INT UNSIGNED` 而非内部名 `UINT` ✅ |
| `VARCHAR(32)` vs `CHAR(32)` / `TEXT` vs `MEDIUMTEXT` / `INT` vs `BIGINT` | 不一致 | 均报 ✅ |

上下文边界：单条 SQL 无上下文→跳过不报 ✅；同表内同名列不做跨表→不报 ✅；三表递进冲突→指向最早基准表 `t_a` ✅；同类型三表→不报 ✅。
文案已无"长度必须一致"旧口径，消息与建议与 §4.5.1 模板逐字一致 ✅。

### 3.4 REQ-06：R058

§4.6.3 判定表 **16/16 全中**，含 P2-01 订正后的那一行：

| 形态 | 期望 | 实测 |
|---|---|---|
| 无 LIMIT / `LIMIT 2001` / `999999` / `?` / `:n` / `1, 2000` / 超大整数 | WARNING | 全部 R058 WARNING ✅ |
| `LIMIT 0` / `1` / `2000`（含 DELETE） | 通过 | 全部 `[]` ✅ |
| `LIMIT 2000 OFFSET 1` | E999、无 R058 | 仅 `E999_SYNTAX_ERROR` ✅ |
| 注释 `/* limit 10 */`、字符串 `remark='limit 10'` | 等同无 LIMIT | R058 WARNING ✅（旧版全文包含判断的错误放行已修复） |
| 子查询内 LIMIT | 不当作外层上限 | R058 WARNING ✅ |

前置条件：无 WHERE / 非分片表 / 无元数据 / 集中式 —— 四种情况均不触发 ✅。
**N-01 回归锁**：全部用例结果中无"执行异常"或 `AttributeError` 字样 ✅。

### 3.5 REQ-03 / REQ-04 与 §6.2 数量口径

| 口径 | 设计 | 实测 |
|---|---|---|
| 规则总数 / 唯一 ID / 最大编号 / 编号无缺口 | 121 | **121 / 121 / R121 / 无缺口** ✅ |
| DDL 分类 | 23 | **23** ✅ |
| DISTRIBUTED 分类 | 15 | **15** ✅ |
| 仅分布式规则（含 R030/R032/R121） | 30 | **30**，三条均在 ✅ |
| 集中式实例生效 | 91 | **91** ✅ |
| Oracle 子集 R078-R119 | 42 | **42** ✅ |

### 3.6 §10.2 质量门禁矩阵（隔离测试）

| 用例 | strict | normal | 实测命中 |
|---|---|---|---|
| 仅 TEXT（R011 默认 INFO） | 通过 | 通过 | `[R011/INFO]`，ERROR=0 WARNING=0 ✅ |
| 任一 R120 LOB | 失败 | 失败 | `[R120/ERROR]` ✅ |
| 仅 TINYTEXT/TINYBLOB/JSON | 通过 | 通过 | `[]` ✅ |
| 集中式仅触发 R030 / R032 的语句 | 通过 | 通过 | `[]` ✅（适用域跳过成立） |
| 分布式二级 MAXVALUE | 失败 | 失败 | `[R121/ERROR]` ✅ |
| R058 `LIMIT 2001` | 失败 | 通过 | `[R058/WARNING]` ✅ |

### 3.7 REQ-08：四模块跨页选择（真实浏览器）

四个 module 各造 25 条同实例快照（3 页），Chromium 全流程实跑：

| 观测项 | schema_audit | slow_scan | launch_check | bigtable |
|---|---|---|---|---|
| 第 1 页勾 A | 1 ✅ | 1 ✅ | 1 ✅ | 1 ✅ |
| 翻到第 2 页（A 不在本页） | 0 ✅ | 0 ✅ | 0 ✅ | 0 ✅ |
| 第 2 页勾 B 后本页勾选数 | 1 ✅ | 1 ✅ | 1 ✅ | 1 ✅ |
| **「开始对比」按钮可用** | **是** ✅ | **是** ✅ | **是** ✅ | **是** ✅ |
| 回到第 1 页 A 是否恢复勾选 | 1 ✅ | 1 ✅ | 1 ✅ | 1 ✅ |
| 对比请求体 | `{"module":"schema_audit","snapshot_ids":[407,397]}` ✅ | `[432,422]` ✅ | `[457,447]` ✅ | `[482,472]` ✅ |
| 超选第三条 | 自动取消 + "最多只能选择两次扫描结果进行对比" ✅ | ✅ | ✅ | ✅ |
| 点「查询」后勾选清空 | 0 ✅ | 0 ✅ | 0 ✅ | 0 ✅ |
| JS 控制台报错 | 无 ✅ | 无 ✅ | 无 ✅ | 无 ✅ |

另测：**切换 module 清空**——在 schema_audit 选满两条（按钮可用）后切到 bigtable 对比页，勾选数 0、按钮不可用 ✅。
**不兼容选择**：跨实例勾选时提示"实例不一致…"并只取消新增项，原合法项保留 ✅（用污染前的混合数据实测到）。
**退出登录清空**：`doLogout()` 第 502 行确调用 `clearCompareSelection()`（代码级确认）；浏览器级我的下拉菜单定位失败，**未完成端到端验证**，如实记录。

### 3.8 回归与门禁

| 项 | 结果 |
|---|---|
| 全量 `tests/` | **4 failed / 1691 passed / 83 skipped / 29 errors** |
| 4 项失败 | 与 v1.6.3.0 SIT 完全相同的沙箱环境项（`o23` 默认值归一 ×2、`monitordb` 夹具、`file_report_delete` 夹具），**与本次改动无关** |
| 29 项 error | 25 项是 G14 破坏性用例的库名闸（需 `SQLCHECK_DB_NAME=tdsql_sqlcheck_test`），4 项是 G14 浏览器测试缺夹具；**均与 v1.6.3.2 无关** |
| `tests/test_rules_v1632.py` | 39 passed ✅ |
| `tests/test_instance_scope_rules.py` | 13 passed ✅ |
| `tests/test_oracle_compat_rules.py` | 103 passed ✅ |
| `tests/test_rbac_path_coverage.py` | 4 passed ✅ |
| `tests/test_design_appendix_matches_repo.py` | 4 passed ✅ |
| `VERSION` / `backend/config.py` | 均为 `1.6.3.2` ✅ |
| `deploy/verify_deploy.sh` | 硬断言已改 `== "121"` ✅ |
| `tests_3p/test_1_smoke.py` | 用例名 `test_sm09_rule_library_121`、断言 121 ✅ |
| `smoke_test.py` | 下限 `>= 121` + R120/R121 存在性检查 ✅ |

---

## 4. DEF-SIT-01（BLOCK）：R121 对绝大多数真实分区表达式失明

### 4.1 现象

用**真实 MariaDB 的 `SHOW CREATE TABLE` 产物**（不是手写样例）喂给审核引擎：

```sql
CREATE TABLE `t_part` (
  `id` bigint(20) NOT NULL,
  `dt` date NOT NULL,
  PRIMARY KEY (`id`,`dt`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
 PARTITION BY RANGE (to_days(`dt`))
(PARTITION `p0` VALUES LESS THAN (738000) ENGINE = InnoDB,
 PARTITION `pmax` VALUES LESS THAN MAXVALUE ENGINE = InnoDB)
```

实测（distributed）：

```text
secondary_partition = {'has_definition': True, 'method': '', 'maxvalue_partitions': (), ...}
审核结果 = ['E999_SYNTAX_ERROR']          ← R121 没有命中
```

逐项拆解到底是哪一维触发的（去掉反引号、去掉 `ENGINE = InnoDB`、改单行都不解决），最终定位到**分区表达式**：

| `PARTITION BY RANGE` 的表达式 | `maxvalue_partitions` | bare 形态审核结果 | 括号 `(MAXVALUE)` 形态审核结果 |
|---|---|---|---|
| `(dt)` 裸列 | `('pmax',)` | E999 + **R121** ✅ | **R121** ✅ |
| `` (`dt`) `` 反引号列 | `('pmax',)` | E999 + **R121** ✅ | **R121** ✅ |
| `(YEAR(dt))` | `('pmax',)` | E999 + **R121** ✅ | **R121** ✅ |
| **`(TO_DAYS(dt))`** | **`()`** | 仅 E999 ❌ | **完全无违规** ❌❌ |
| **`` (to_days(`dt`)) ``** | **`()`** | 仅 E999 ❌ | **完全无违规** ❌❌ |
| **`(UNIX_TIMESTAMP(dt))`** | **`()`** | 仅 E999 ❌ | **完全无违规** ❌❌ |
| **`COLUMNS(dt)`** | **`()`** | 仅 E999 ❌ | 仅 E999 ❌ |
| **多列 `(id,dt)`** | **`()`** | 仅 E999 ❌ | **完全无违规** ❌❌ |

### 4.2 根因

`backend/engine/parser/parser_legacy.py:949`：

```python
_PARTITION_FUNCS = ("YEAR", "MONTH", "DAY")
```

`_consume_partition_expr()`（954-977 行）只接受两种形态：**单个列标识符**，或**白名单里的三个函数 + 恰好一个列参数**。其余一律 `return -1, ""`，导致 `_consume_secondary_partition()` 整体失败，策略事实里 `method` 为空、`maxvalue_partitions` 为空。

这个白名单是 v1.6.2.2 索引解析修复时为**恢复计划**写的——那时"认不出"的代价只是"不做恢复"，是安全的失败关闭。v1.6.3.2 让 R121 直接依赖它之后，"认不出"的代价变成了**规则漏报**，安全方向反了。

### 4.3 为什么是 BLOCK

1. `TO_DAYS` 是 MySQL 官方手册 RANGE 分区示例里的首选函数，也是本机 MariaDB `SHOW CREATE TABLE` 实际吐出来的形态。**在线元数据审核（`/api/v1/audit/extract-and-audit`）正是把 `SHOW CREATE TABLE` 的结果送进审核**，这是 R121 的主战场，现在整条主战场失明。
2. `RANGE COLUMNS(...)` 和多列 RANGE 是日期分区的另一主流写法，同样失明。
3. **最危险的是括号形态**：`RANGE (TO_DAYS(dt)) … VALUES LESS THAN (MAXVALUE)` 这条语句语法完全合法、sqlglot 正常解析，于是**既没有 E999 也没有 R121，审核结果一片干净**——用户会以为通过了治理检查。bare 形态至少还剩一条 E999 兜底，括号形态是彻底静默。
4. 设计 §4.7.2/§5.4 承诺覆盖 `SHOW CREATE TABLE` 形态（§10.1 R121 第 1 条还专门要求测这个），实际未达成。

### 4.4 整改方案（照图施工）

**推荐方案 A：给策略扫描配一套只跳过、不校验的表达式消费器，`_consume_partition_expr` 一个字不动。**

理由：`_consume_partition_expr` 同时服务 `_plan_recovery()` 的**恢复门禁**。放宽它会让更多语句被"证明合规"从而进入 AST 恢复，那是 v1.6.2.2 花十三轮评审才收敛住的最敏感面。R121 的策略扫描根本不需要**校验**表达式，只需要**跳过**它、找到后面的分区定义表——校验仍由恢复计划各自负责。二者分离后爆炸半径为零。

**① 在 `backend/engine/parser/parser_legacy.py` 中新增（放在 `_consume_partition_expr` 之后）：**

```python
def _skip_balanced_parens(toks, i, stop):
    """从 `(` 开始跳过一整段配平括号，返回下一个下标；不配平返回 -1。

    只供 R121 的**策略扫描**使用：策略扫描的目标是找到分区定义表并读出
    VALUES LESS THAN 的边界，不需要证明分区表达式合法——表达式的合法性
    校验仍由 `_consume_partition_expr()` 负责，它服务 AST 恢复门禁，本函数
    绝不替代它，也不得被 `_plan_recovery()` 调用。
    """
    if i >= stop or toks[i].token_type != TokenType.L_PAREN:
        return -1
    depth, j = 0, i
    while j < stop:
        tt = toks[j].token_type
        if tt == TokenType.L_PAREN:
            depth += 1
        elif tt == TokenType.R_PAREN:
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return -1


def _consume_partition_expr_lenient(toks, i, stop):
    """策略扫描专用：接受 `(任意配平表达式)` 与 `COLUMNS(...)` 两种形态。

    覆盖 MySQL/TDSQL 允许的全部分区表达式（TO_DAYS/UNIX_TIMESTAMP/EXTRACT/
    多列 RANGE COLUMNS 等），不做白名单。返回 (下一个下标, "lenient") 或 (-1, "")。
    """
    j = i
    if j < stop and _is_bare_kw(toks[j], "COLUMNS"):
        j += 1
    k = _skip_balanced_parens(toks, j, stop)
    return (k, "lenient") if k >= 0 else (-1, "")
```

**② 让二级分区的**策略扫描入口**改用宽松版。**
`_consume_secondary_partition()` 目前同时被恢复计划和策略扫描调用，必须**按用途分流**，不能直接改它。落点为：给它加一个只由策略扫描传入的开关，默认值保持现状：

```python
def _consume_secondary_partition(toks, i, stop, lenient_expr=False):
    ...
    j, eshape = (_consume_partition_expr_lenient(toks, j + 1, stop)
                 if lenient_expr else _consume_partition_expr(toks, j + 1, stop))
    ...
```

`_plan_recovery()` 的调用点**不传该参数**（保持严格）；R121 的策略扫描调用点传 `lenient_expr=True`。
ALTER ADD / ALTER REORGANIZE 的分区定义扫描本就不经过 `PARTITION BY <method> (expr)`，不受影响。

> ⚠️ 实施必须核对：`_consume_secondary_partition` 的现有调用点共 3 处
> （`_scan_create_tail`、可执行注释通道、以及 v1.6.3.2 新增的策略扫描）。
> 只有第三处允许传 `True`。改完用 `grep -n "_consume_secondary_partition(" ` 逐处确认。

**③ `method` 字段的回填。** 现在表达式失败时 `method` 为空串；宽松通道下必须正常回填 `RANGE`/`LIST`，否则 §4.7.3 的结构化字段名不副实。

**④ 必须补的测试（`tests/test_rules_v1632.py`）：**

```python
@pytest.mark.parametrize("expr", [
    "(dt)", "(`dt`)", "(YEAR(dt))", "(MONTH(dt))", "(DAY(dt))",
    "(TO_DAYS(dt))", "(to_days(`dt`))", "(TO_SECONDS(dt))",
    "(UNIX_TIMESTAMP(ts))", "(EXTRACT(YEAR FROM dt))",
    "(ABS(id))", "(MOD(id,7))", "(FLOOR(id/100))",
    " COLUMNS(dt)", " COLUMNS(`dt`,id)", "(id,dt)",
])
def test_r121_covers_all_partition_expression_forms(expr):
    """R121 不得因分区表达式形态而漏报——这是 SHOW CREATE TABLE 的真实形态面。"""
    for boundary in ("MAXVALUE", "(MAXVALUE)"):
        sql = (BASE_CREATE + f" PARTITION BY RANGE {expr} "
               f"(PARTITION p0 VALUES LESS THAN (738000), "
               f"PARTITION pmax VALUES LESS THAN {boundary}) shardkey=id")
        ids = {v.rule_id for v in
               checker.audit_sql(sql, instance_type="distributed").violations}
        assert "R121" in ids, f"{expr} + {boundary} 漏报 R121"


def test_r121_hits_real_show_create_table_output():
    """用真实 SHOW CREATE TABLE 产物（含反引号、ENGINE = InnoDB、多行）做端到端锁。"""
    sql = REAL_SHOW_CREATE_TABLE_WITH_MAXVALUE   # 见本报告 §4.1 的原文
    ids = {v.rule_id for v in
           checker.audit_sql(sql, instance_type="distributed").violations}
    assert "R121" in ids
```

**⑤ 必须补的反向锁**（防止宽松化把恢复门禁一起放宽）：

```python
def test_lenient_expr_does_not_widen_recovery_gate():
    """宽松表达式只服务策略扫描，不得让 _plan_recovery 接受更多语句。"""
    import backend.engine.parser.parser_legacy as PL
    src = inspect.getsource(PL._plan_recovery) + inspect.getsource(PL._scan_create_tail)
    assert "lenient_expr=True" not in src, "恢复门禁不得使用宽松表达式消费器"
```

**⑥ 设计文档同步（Rev.C → Rev.D）**：§4.7.3 增加一条「分区表达式形态不参与 R121 的命中判定；策略扫描对表达式只跳过不校验，校验由恢复门禁独立负责」；§10.1 R121 增加"全表达式形态参数化"与"真实 `SHOW CREATE TABLE` 产物"两条；§12 增加 RISK-19 记录"表达式白名单曾使 R121 主战场失明"。

**方案 B（不推荐）**：直接把 `_PARTITION_FUNCS` 扩成 MySQL 官方分区函数全集
（`ABS/CEILING/DATEDIFF/DAY/DAYOFMONTH/DAYOFWEEK/DAYOFYEAR/EXTRACT/FLOOR/HOUR/MICROSECOND/MINUTE/MOD/MONTH/QUARTER/SECOND/TIME_TO_SEC/TO_DAYS/TO_SECONDS/UNIX_TIMESTAMP/WEEKDAY/YEAR/YEARWEEK`）
并支持 `COLUMNS(...)` 与多列。能解决漏报，但**同时放宽了 AST 恢复门禁**——原先因表达式不认识而失败关闭的语句会开始被恢复，影响面超出 R121。若选此方案，必须补一轮针对恢复门禁的全量回归证据，并在设计里显式登记该行为变化。

---

## 5. DEF-SIT-02（BLOCK）：合成 KFN 守卫立论错误，把合法 DDL 判成语法错误

### 5.1 现象

设计 §4.7.5 的矩阵明确写着：

| 形态 | distributed | centralized |
|---|---|---|
| ALTER REORGANIZE bare 或括号形态 | **当前为 Command、无 E999**；token 命中后 distributed 含 R121 | **不含 R121/E999** |

实测：

| 用例 | 设计要求 dist | 实测 dist | 设计要求 cent | 实测 cent |
|---|---|---|---|---|
| `ALTER … REORGANIZE … LESS THAN MAXVALUE` | R121（无 E999） | **E999 + R121** ❌ | 空 | **E999** ❌ |
| `ALTER … REORGANIZE … LESS THAN (MAXVALUE)` | R121（无 E999） | **E999 + R121** ❌ | 空 | **E999** ❌ |

`parse_error` 的内容是 `KNOWN_FIDELITY_GAP[SECONDARY-PARTITION-MAXVALUE]: 二级分区 MAXVALUE 形态无法恢复为结构化 AST（sqlglot 降级为 Command）`——是**代码自己合成的**，不是 sqlglot 报的。

### 5.2 根因与立论错误

`parser_legacy.py:2461-2470`：

```python
# v1.6.3.2 / §4.7.5：sqlglot 30.14 把 CREATE bare MAXVALUE 静默降级为 Command
# （不抛 ParseError，RISK-17 的实测形态）…
if (isinstance(ast, exp.Command)
        and parsed.secondary_partition.get("maxvalue_partitions")):
    parsed.parse_error = ("KNOWN_FIDELITY_GAP[SECONDARY-PARTITION-MAXVALUE]: …")
```

注释里的前提**与实测相反**。我把 8 种 CREATE 形态（无尾 / `shardkey=` / `TDSQL_DISTRIBUTED` / `broadcast` × bare / 括号）逐个测了：

| CREATE 形态 | `ast` | `parse_error` 来源 |
|---|---|---|
| 四种尾子句 × **bare** MAXVALUE | **`None`** | sqlglot 真实 ParseError |
| 四种尾子句 × **括号** MAXVALUE | **`Create`** | 无 |
| 可执行注释 `/*!50100 … */` 两种 | `Create` | 无 |

**没有任何一种 CREATE 形态会进入 `isinstance(ast, exp.Command)` 分支。** 这段守卫对它声称要保护的 CREATE 场景**一次都不会执行**；唯一会命中它的是 `ALTER … REORGANIZE`——那本来就是 sqlglot 对该语法的正常降级形态，不是缺陷。

### 5.3 为什么是 BLOCK

* `ALTER TABLE … REORGANIZE PARTITION … INTO (… VALUES LESS THAN MAXVALUE)` 是**完全合法**的 MySQL/TDSQL DDL。v1.6.3.2 之前它审核干净，现在被判 `E999_SYNTAX_ERROR`（ERROR 级）。
* **集中式实例是纯损失**：R121 按适用域跳过，用户拿不到任何可执行的规则说明，只拿到一句"SQL 语句无法解析或结构不完整（可能是拉取截断/语法错误）"——语句根本没有语法错误。
* ERROR 级意味着 **strict 与 normal 两种门禁策略都会失败**，直接卡住正常的分区运维变更。
* 本项目在 v1.6.2.2 用十三轮评审收敛的就是"对合法 TDSQL 语法误报 E999"这一类问题，这是同类回潮。

### 5.4 整改方案（照图施工，已变异验证）

**一行门闸：把该守卫限定在 CREATE 来源。**

`backend/engine/parser/parser_legacy.py:2466`，把

```python
                if (isinstance(ast, exp.Command)
                        and parsed.secondary_partition.get("maxvalue_partitions")):
```

改为

```python
                if (isinstance(ast, exp.Command)
                        and parsed.secondary_partition.get("source_context") == "CREATE"
                        and parsed.secondary_partition.get("maxvalue_partitions")):
```

并把上方注释的事实改对：

```python
                # v1.6.3.2 / §4.7.5：仅针对 CREATE 来源的兜底。实测（sqlglot 30.14.0）
                # CREATE 的 bare MAXVALUE 是真实 ParseError（ast=None）、括号形态正常
                # 产出 Create，两者都不会落到本分支；本分支只在将来 sqlglot 改变
                # CREATE 降级行为时才生效。ALTER REORGANIZE 的 Command 是该语法的
                # 正常降级形态、不是缺陷，**不得**据此合成 parse_error（SIT DEF-SIT-02）。
```

**变异验证（我已实测，整改后请复现）**：加上 `source_context == "CREATE"` 门闸后，§4.7.5 矩阵**逐行归位**：

```text
CREATE bare    dist=['E999_SYNTAX_ERROR','R121']   cent=['E999_SYNTAX_ERROR']   ← 与设计一致
CREATE 括号     dist=['R121']                       cent=[]                      ← 与设计一致
REORG bare     dist=['R121']                       cent=[]                      ← 与设计一致
REORG 括号      dist=['R121']                       cent=[]                      ← 与设计一致
REORG 正常      dist=[]                             cent=[]                      ← 与设计一致
```

**必须补的回归锁（`tests/test_rules_v1632.py`）：**

```python
@pytest.mark.parametrize("boundary", ["MAXVALUE", "(MAXVALUE)"])
@pytest.mark.parametrize("inst", ["distributed", "centralized"])
def test_reorganize_maxvalue_must_not_fabricate_e999(boundary, inst):
    """ALTER … REORGANIZE 的 Command 降级是该语法的正常形态，不得被合成为语法错误。

    DEF-SIT-02：v1.6.3.2 首版对任何 Command + MAXVALUE 都合成 parse_error，
    使合法 DDL 在集中式实例上凭空多出一条 ERROR 级 E999。
    """
    sql = ("ALTER TABLE t REORGANIZE PARTITION p0 INTO ("
           "PARTITION p0 VALUES LESS THAN (2020), "
           f"PARTITION pmax VALUES LESS THAN {boundary})")
    ids = {v.rule_id for v in checker.audit_sql(sql, instance_type=inst).violations}
    assert "E999_SYNTAX_ERROR" not in ids, "REORGANIZE 的正常 Command 降级不得报语法错误"
    assert ("R121" in ids) is (inst == "distributed")


def test_create_bare_maxvalue_still_fails_closed():
    """整改不得削弱 CREATE bare 形态的失败关闭（KFN-1 保持）。"""
    ids = {v.rule_id for v in checker.audit_sql(
        CREATE_BARE_MAXVALUE, instance_type="distributed").violations}
    assert {"E999_SYNTAX_ERROR", "R121"} <= ids
```

**设计文档同步**：§4.7.5 增加一行「ALTER REORGANIZE 正常上界（无 MAXVALUE）：双实例类型均无 E999、无 R121」，并把该守卫的适用范围写进 §5.4——现文只说"策略事实三条出口都要保留"，没说"不得反过来合成 parse_error"。

---

## 6. DEF-SIT-03（MINOR）：LIMIT token 回退在 AST 完好时仍多做一次全量词法化

### 6.1 现象

设计 §5.4 写明的性能不变量：

> 本改动不得使单条语句的**预检词法化**次数超过 Rev.Q 既有基线一次。…
> 测试使用 tokenizer spy/monkeypatch 对**一批 SELECT、INSERT 等非 DDL 语句**比较调用次数，
> **必须证明相对当前基线没有新增 tokenization**。

我用 `sqlglot.tokens.Tokenizer.tokenize` 打桩计数，同一批语句在 `2f145bc`（改前）与 `c0e5e25`（改后）各跑一遍：

| 批次 | 改前 | 改后 |
|---|---:|---:|
| 非 DDL 5 条（SELECT×2 / INSERT / UPDATE / DELETE） | **15** | **17** |
| DDL 2 条（CREATE / ALTER ADD COLUMN） | 6 | 6 |

逐条定位：

| 语句 | 改前 | 改后 |
|---|---:|---:|
| `SELECT * FROM t WHERE id=1` | 3 | 3 |
| `INSERT INTO t (a) VALUES (1)` | 3 | 3 |
| `SELECT a,b FROM t JOIN s ON t.id=s.id` | 3 | 3 |
| `CREATE TABLE t (…)` | 3 | 3 |
| `ALTER TABLE t ADD COLUMN a TEXT` | 3 | 3 |
| `UPDATE t SET a=1 WHERE id>0 LIMIT 2000` | 3 | 3 |
| **`UPDATE t SET a=1 WHERE id=1`（无 LIMIT）** | 3 | **4** |
| **`DELETE FROM t WHERE id=1`（无 LIMIT）** | 3 | **4** |

**R121 的策略扫描并入既有预检这一点做对了**（CREATE/ALTER 都是 3→3）。超标的是另一处：DML LIMIT 的 token 回退。

### 6.2 根因

`backend/engine/parser/parser_legacy.py:3258` `_extract_dml_limit()`：

```python
        lim = None
        if ast is not None and not isinstance(ast, exp.Command):
            args = getattr(ast, "args", None)
            if isinstance(args, dict):
                lim = args.get("limit")
        if isinstance(lim, exp.Limit):
            ...   # AST 路径
            return fact
        # token 回退：仅当 AST 不可靠时使用
        try:
            toks = sqlglot.Dialect.get_or_raise(
                self.dialect).tokenizer_class().tokenize(sql)     # ← 又一次全量词法化
```

回退的**触发条件把两件事混为一谈**：

* "AST 不可靠"（`ast is None` 或 `Command`）——确实需要回退；
* "AST 里没有 `limit` 节点"——对一个**完好解析**的 `Update`/`Delete` 节点来说，这是**权威结论**：语句就是没有 LIMIT，不需要再去词法确认一遍。

现在两者都掉进同一个 `if isinstance(lim, exp.Limit)` 的 else 分支，于是每一条**无 LIMIT 的 UPDATE/DELETE** 都白白多付一次全量词法化。文件审核里这类语句数量可观。

### 6.3 整改方案（照图施工）

把回退条件收紧到"AST 确实不可靠"。`_extract_dml_limit()` 中，在 token 回退之前插入一道早退：

```python
        if isinstance(lim, exp.Limit):
            ...
            return fact
        # DEF-SIT-03：AST 完好（非 None、非 Command）时，"没有 limit 节点"本身
        # 就是权威结论——语句确实没有 LIMIT，无需再做一次全量词法化。
        # 只有 AST 不可靠（ast is None 或降级为 Command）才允许 token 回退。
        if ast is not None and not isinstance(ast, exp.Command):
            return fact                      # present=False，verifiable=False
        # token 回退：仅当 AST 不可靠时使用
        try:
            toks = sqlglot.Dialect.get_or_raise(
                self.dialect).tokenizer_class().tokenize(sql)
```

**安全性核对（我已实测）**：`LIMIT ?` 与 `LIMIT :n` 都不依赖回退——前者 sqlglot 产出 `Limit(Placeholder)` 走 AST 路径；后者若 sqlglot 解析失败则 `ast is None`，仍会进回退。改后 §4.6.3 判定表 16 行结果不变，需在整改后复跑确认。

**必须补的回归锁：**

```python
def test_dml_limit_does_not_add_tokenization_when_ast_is_sound():
    """DEF-SIT-03：AST 完好时不得为"确认没有 LIMIT"再做一次全量词法化。"""
    import sqlglot
    orig = sqlglot.tokens.Tokenizer.tokenize
    calls = {"n": 0}
    def spy(self, sql, *a, **k):
        calls["n"] += 1
        return orig(self, sql, *a, **k)
    sqlglot.tokens.Tokenizer.tokenize = spy
    try:
        p = SQLParser()
        base = {}
        for s in ("SELECT * FROM t WHERE id=1",
                  "UPDATE t SET a=1 WHERE id=1",
                  "DELETE FROM t WHERE id=1",
                  "UPDATE t SET a=1 WHERE id>0 LIMIT 2000"):
            calls["n"] = 0
            p.parse(s)
            base[s] = calls["n"]
    finally:
        sqlglot.tokens.Tokenizer.tokenize = orig
    n = set(base.values())
    assert len(n) == 1, f"各类语句的词法化次数应一致，实测 {base}"
```

**若不修**（例如认为一次额外词法化可接受），则必须走文档路径：在设计 §5.4 把性能不变量的适用范围**显式限定到二级分区策略扫描**，另立一条登记「DML LIMIT 回退对无 LIMIT 的 UPDATE/DELETE 多一次词法化」并给出实测量级，由需求方书面接受。**不能让设计写着"必须证明没有新增 tokenization"、实测却是 15→17 而无人处置。**

---

## 7. DEF-SIT-04（MINOR）：设计点名的 `TEST_SPEC` 未更新

设计 §9.3 明确列出：

> `tests/rule_audit_materials/verify_rules.py`、`verify_metadata_rules.py`、**`tests/TEST_SPEC-规则覆盖与压力测试.md`** | 扩充 R120/R121 物料，更新当前 121 条覆盖目标

实测 `git diff 2f145bc c0e5e25 --name-only` **不含该文件**。前两个 `.py` 都改了（`verify_rules.py` 的头注释已改 121、R035 豁免说明已更新），物料 SQL 也补了 `R120_01`/`R121_01` 用例，唯独这份规格文档没动。它现在仍写着：

```text
:6    | 测试对象 | 119 条 SQL 审核规则（文件审核 / 在线元数据审核）+ 慢SQL治理扫描模块 |
:12   1. **文件审核测试物料**…验证 119 条规则能否被「文件审核」…
:18   ## 一、119 条规则的验证路径划分
:20   经实测，119 条规则按「触发所需信息」分为三类…
:109  规则总数: 119  文件审核已覆盖: 107  未覆盖: 0
```

（同文件第 7、256、257、258 行的 `119.45.220.89` 是内网 IP，**不得改写**，与设计 §9.3 的排除口径一致。）

**整改**：把上述 5 处当前能力声明改为 121，并把 `:109` 的覆盖统计按实际重跑 `verify_rules.py` 后回填（新增 R120/R121 后"文件审核已覆盖"数会变）。**不要盲替换**——同文件的 IP 保持原样。

---

## 8. DEF-SIT-05（NIT）：Oracle 兼容测试的用例名残留 119

`tests/test_oracle_compat_rules.py:30`：

```python
    def test_total_rules_119(self, checker):
        info = checker.get_rules_info()
        assert len(info) == 121          # ← 断言已更新
```

断言对了，**函数名没改**。同一轮里 `tests_3p/test_1_smoke.py` 的 `test_sm09_rule_library_119` 已经改名成 `..._121`，两处做法不一致。

**整改**：改名为 `test_total_rules_121`。注意同文件 `test_r078_to_r119_continuous` 里的 `R119` 是 Oracle 子集**编号上界**，按设计 §9.4 分类规则**保持不变**。

---

## 9. 遗留与结论

### 9.1 未完成/未覆盖项

| 项 | 说明 |
|---|---|
| FE-12 退出登录清空的浏览器级验证 | `doLogout()` 第 502 行调用 `clearCompareSelection()` 已代码级确认；我的下拉菜单定位失败，**端到端未跑通**，如实记录，建议整改轮补 |
| FE-11 慢响应竞态 | 请求序号代码已实现（`cmpReqSeq`），但需要可控延迟的桩，本轮未构造 |
| 内网 UAT-01～UAT-11 | 需真实 TDSQL 分布式/集中式实例，本轮沙箱无法覆盖 |
| §12 三项生产发布书面门禁 | 属发布环节，Q 已建 `GATE-v1.6.3.2-…发起.md`，本轮不判定 |
| 目标环境（TDSQL/MySQL 8）全量回归 | 沙箱是 MariaDB，门禁数据不能替代内网重跑 |

### 9.2 结论

**不通过。**

R011/R120（含 ALTER 扩围）、R035（含批内跨表上下文）、R058、数量口径、门禁矩阵、四个模块的跨页选择——**这六块做得干净**，我按设计的正反例表逐条独立复现，一处未偏。前端尤其扎实：四个 module 真实浏览器全流程跑通，对比请求体只带两个 ID、与页码无关，超选/不兼容/清空/切模块的边界全部正确。

问题集中在 R121 一条规则上，两项都是 BLOCK：

* **DEF-SIT-01** 是覆盖面问题——规则对 `TO_DAYS` 这类**真实 `SHOW CREATE TABLE` 会吐出来的**分区表达式失明，而在线元数据审核正是 R121 的主战场；括号形态下更是连 E999 都没有，静默通过。
* **DEF-SIT-02** 是误报问题——一段基于**错误事实前提**写的守卫，对它声称要保护的 CREATE 场景一次都不会执行，却把合法的 `ALTER … REORGANIZE` 判成语法错误，在集中式实例上凭空多出一条 ERROR 级 E999，双门禁全卡。

两项的整改都不大：DEF-SIT-02 是一行门闸（我已变异验证，加上后 §4.7.5 矩阵五行全部归位）；DEF-SIT-01 是新增一个"只跳过不校验"的宽松表达式消费器并按用途分流，关键是**不能去放宽 `_consume_partition_expr` 本身**——那会连带放宽 AST 恢复门禁，爆炸半径远超本需求。

三项 MINOR/NIT 都是一次提交可以带走的。建议五项合并为一次整改，整改后重跑本报告 §3～§8 的全部用例。
