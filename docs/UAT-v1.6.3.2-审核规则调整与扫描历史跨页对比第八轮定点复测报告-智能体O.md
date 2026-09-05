# v1.6.3.2 审核规则调整与扫描历史跨页对比
## 第八轮定点复测报告（智能体 O）

| 项目 | 内容 |
|---|---|
| 测试版本 | v1.6.3.2 |
| 被测提交 | `e5f63d254852e06dcbd16b6176f3020e21bf4ffa` |
| 整改依据 | 第七轮 `UAT-O-1632-R7-01`、`UAT-O-1632-R7-02` |
| 测试日期 | 2026-09-05 |
| 测试人 | 智能体 O（独立 UAT） |
| 门禁签署人 | Mr.Linsang |
| 最终结论 | **不通过；R7-01 关闭，R7-02 部分修复但仍未关闭，GATE-2 继续阻断** |

---

## 1. 范围纪律

按 Mr.Linsang 指令，本轮只复测第七轮两个未关闭问题，不做全量回归、不扫描其他模块、不扩大缺陷发现范围：

1. `UAT-O-1632-R7-01`：例程合法语法兼容与非法语法失败关闭；
2. `UAT-O-1632-R7-02`：标准 `DELIMITER` 脚本在即时、文件、上传、流式入口的一致性。

本轮未复测规则 R011/R030/R032/R035/R058/R121、扫描历史跨页选择、GATE-1、GATE-3、麒麟部署、性能及其他业务模块。未执行全量 `tests` 或 `tests_3p`，这是用户明确限定的定点复测边界，不应解读为这些范围已在本轮重新验证。

---

## 2. 执行环境与证据边界

- 分支：`main`；测试开始时 HEAD 与 `origin/main` 均为 `e5f63d2`；
- 在 `127.0.0.1:8002` 启动被测提交，使用专用临时 MySQL 元数据库、真实管理员登录和真实浏览器点击；
- HTTP 证据来自运行中服务的 `/api/v1/audit/sql`、`/file`、`/upload`、`/batch-stream`；
- 临时 SQL 上传文件、测试服务和专用元数据库均在测试后删除，端口 8002 已释放；现有元数据库和产品代码未改动；
- 用户自有未跟踪文件 `docs/PRODUCTION-DEPLOY-ISSUES-v1.6.3.0.md` 未读取、未修改、未暂存。

---

## 3. 定点裁决

| 原缺陷 | 第八轮结果 | 裁决 |
|---|---|---|
| `UAT-O-1632-R7-01`（P1） | 合法 DEFINER/控制流通过；上一轮全部非法反例均 E999；对象类型和限定名正确 | **关闭** |
| `UAT-O-1632-R7-02`（P1） | `/file`、`/upload`、`/batch-stream` 与文件审核页面通过；`/sql` 和即时审核页面仍报 E999 | **部分修复，保持未关闭** |

本轮不新增缺陷编号。当前仍有 `P1=1`，即原 `UAT-O-1632-R7-02` 的即时审核残留。

---

## 4. R7-01 定点复测：通过并关闭

### 4.1 浏览器合法场景

在“SQL审核 → 即时审核”选择“集中式规则”后逐条点击“开始审核”：

| 场景 | 实际页面结果 |
|---|---|
| quoted DEFINER：`CREATE DEFINER='admin'@'localhost' PROCEDURE p_def(IN x INT) SELECT x;` | 审核通过；`SQL类型: CREATE PROCEDURE`；0 违规 |
| `IF()` 函数 + CASE 表达式 | 审核通过；`SQL类型: CREATE PROCEDURE`；0 违规 |

直接引擎复测还确认：`DEFINER=CURRENT_USER`、合法完整触发器均通过；`CREATE PROCEDURE db1.p(...)` 的 `created_object_name` 为完整 `db1.p`。

### 4.2 浏览器非法场景

以下反例均由页面显示 1 项 `E999_SYNTAX_ERROR`，不再错误通过：

1. 缺参数括号：`CREATE PROCEDURE p_bad SELECT 1;`；
2. FUNCTION 非法模式：`CREATE FUNCTION f(IN x INT) RETURNS INT RETURN x;`；
3. 空参数段：`CREATE PROCEDURE p(,x INT) BEGIN END;`；
4. 垃圾过程体：`CREATE PROCEDURE p() GARBAGE TOKEN;`；
5. 触发器缺 `FOR EACH ROW`。

### 4.3 上一轮完整反向矩阵

独立重放上一轮 12 类非法输入：缺括号、前置/尾随/连续逗号、缺逗号、FUNCTION IN/OUT、PROCEDURE/FUNCTION 垃圾体、TRIGGER 垃圾体、缺 `FOR EACH ROW`、非法触发时机。实际全部 `passed=false` 且包含 `E999_SYNTAX_ERROR`。

同一个缺括号过程经 `/sql`、`/file`、`/upload`、`/batch-stream` 四个真实 HTTP 入口也均被 E999 拦截。故 `UAT-O-1632-R7-01` 满足关闭条件。

---

## 5. R7-02 定点复测：三入口恢复，即时入口仍失败

### 5.1 测试脚本

```sql
DELIMITER $$
CREATE PROCEDURE p_d(IN x INT)
BEGIN
  SET @a = x;
  SET @b = x;
END$$
DELIMITER ;
```

### 5.2 已通过部分

| 入口 | 实际结果 |
|---|---|
| `split_audit_script()` | 1 段；SQL 从 `CREATE PROCEDURE` 开始，以 bare `END` 结束；无 `DELIMITER`/`$$`；行号 2~6 |
| `/api/v1/audit/file` | 1 条 `CREATE PROCEDURE`，`passed=true`，0 违规 |
| `/api/v1/audit/upload` | 1 条 `CREATE PROCEDURE`，`passed=true`，0 违规 |
| `/api/v1/audit/batch-stream` | meta + 1 个数据帧；`sql_type=CREATE PROCEDURE`，`passed=true`，0 违规 |
| 文件审核页面真实上传 | SQL 总数 1、通过 1、未通过 0、通过率 100%、类型 `CREATE PROCEDURE` |

这些结果证明 Q 的统一拆分器和三个文件型入口整改有效。

### 5.3 未关闭残留：即时审核仍把原文送入 parser

把同一脚本粘贴到“SQL审核 → 即时审核”，选择集中式并点击“开始审核”，页面显示：

```text
发现 1 项违规
SQL类型: CREATE PROCEDURE
[E999_SYNTAX_ERROR] Invalid expression / Unexpected token
... DELIMITER $$ CREATE PROCEDURE ... END$$ DELIMITER ...
```

真实 `/api/v1/audit/sql` 响应同样为 `passed=false`、`E999_SYNTAX_ERROR`。这与 Q 开发报告“即时多 SQL 适配层也统一调用 `split_audit_script`、四入口一致”的验收口径直接冲突，因此原 P1 `UAT-O-1632-R7-02` 不能关闭。

---

## 6. 根因定位

文件：`backend/services/audit_service.py`，`audit_single_sql()`。

当前逻辑先正确得到：

```python
statements = [s.strip() for s, _ln, _end in split_audit_script(sql) if s.strip()]
```

但当拆分结果恰好只有 1 条时，分支仍审核原始 `sql`：

```python
if len(statements) <= 1:
    result = self.checker.audit_sql(sql, ...)
```

所以 `split_audit_script()` 已剥离的 `DELIMITER` 指令和 `$$` 没有被使用。文件型入口直接审核清洗后的分段，故只有即时入口失败。

---

## 7. 给 Q 的定点修复方案

只修改 `audit_single_sql()` 的单段分支，不改拆分器、不动其他模块：

```python
if len(statements) == 1:
    audit_sql = statements[0]
    result = self.checker.audit_sql(
        audit_sql,
        rule_overrides=overrides,
        instance_type=it,
    )
    self._apply_shard_key_check(
        result,
        audit_sql,
        ictx,
        table_metadata=table_metadata,
    )
elif len(statements) == 0:
    # 保留现有空输入/纯指令失败关闭口径，不在本轮扩展定义。
    result = self.checker.audit_sql(
        sql,
        rule_overrides=overrides,
        instance_type=it,
    )
    self._apply_shard_key_check(
        result,
        sql,
        ictx,
        table_metadata=table_metadata,
    )
else:
    ...  # 现有多段逻辑不变
```

必须同时把历史保存的 `result.sql` 保持为实际被审核的 `audit_sql`，不得一边展示清洗后类型、一边保存含客户端指令的原文作为已审核语句。

### 必增回归锁

1. 直接调用 `audit_service.audit_single_sql(DELIMITER脚本, instance_type="centralized")`：断言 `passed=true`、类型 `CREATE PROCEDURE`、0 违规；
2. 真实 `/api/v1/audit/sql`：同样断言，并断言结果/历史中不含 `DELIMITER` 与尾部 `$$`；
3. 与同脚本 `/file`、`/upload`、`/batch-stream` 对照：四入口均为一个 `CREATE PROCEDURE`、0 违规；
4. 保留 Q 现有 72 条 `tests/test_routine_audit_r7.py` 全绿。

### 下一轮关闭动作

O 第九轮只需重跑以上 4 点并在即时审核页面点击一次同一 `DELIMITER` 脚本；若页面通过且类型为 `CREATE PROCEDURE`，即可关闭 `UAT-O-1632-R7-02`。

---

## 8. 专项自动化结果

| 测试 | 结果 |
|---|---|
| `pytest tests/test_routine_audit_r7.py -q` | **72 passed，3 warnings，0 failed，4.14s** |
| 上一轮独立合法/非法矩阵 | **合法 5/5；非法 12/12；DELIMITER 引擎/文件入口通过** |
| 四个真实 HTTP 入口：缺括号过程 | **4/4 均 E999，R7-01 通过** |
| 四个真实 HTTP 入口：DELIMITER | **3/4 通过；`/sql` 失败，R7-02 未关闭** |

没有执行全量回归，符合 Mr.Linsang 本轮“不扩大范围”的明确要求。

---

## 9. 门禁裁决

| 门禁 | 第八轮状态 | O 意见 |
|---|---|---|
| GATE-1 | **Mr.Linsang 已签署通过** | 本轮不复测、不重开 |
| GATE-2 | **不通过** | R7-01 已关闭；R7-02 尚余即时入口 P1 |
| GATE-3 | **Mr.Linsang 已签署通过** | 本轮不复测、不重开 |
| 生产准出 | **禁止** | 等 Q 完成 §7 定点修复、O 第九轮关闭 R7-02、Mr.Linsang 完成 GATE-2 签署 |

---

## 10. 最终裁决

**第八轮定点复测不通过。**

Q 对例程合法/非法边界的整改有效，`UAT-O-1632-R7-01` 正式关闭；`DELIMITER` 的文件、上传、流式入口也已恢复。但是即时审核单段分支没有使用已清洗的唯一语句，用户在页面粘贴同一标准脚本仍收到 E999，因此 `UAT-O-1632-R7-02` 保持 P1 未关闭，GATE-2 和生产发布继续阻断。

证据索引：`docs/evidence/v1.6.3.2-uat-o-r8/README.md`。
