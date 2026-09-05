# v1.6.3.2 第七轮生产门禁整改复测证据索引（智能体 O）

## 1. 基线与裁决

- 被测提交：`995a38bf3dad4a90be82351b19f992621bbb38e2`
- 分支：`main`，测试开始时与 `origin/main` 一致
- 日期：2026-09-05
- 结论：**不通过；新增 P1×2，GATE-2 继续阻断**
- 缺陷：`UAT-O-1632-R7-01`、`UAT-O-1632-R7-02`
- GATE-1/GATE-3：Mr.Linsang 已签署，状态保持
- O 未修改产品代码。

## 2. 浏览器证据

当前提交隔离服务：`127.0.0.1:8002`。

```text
合法复杂过程：
CREATE PROCEDURE p_x(IN pid INT) BEGIN IF ... END IF; UPDATE ...; END;
-> 审核通过；SQL类型=CREATE PROCEDURE；集中式；跳过31条分布式规则

非法缺参数括号过程：
CREATE PROCEDURE p_bad SELECT 1;
-> 审核通过；SQL类型=CREATE PROCEDURE（错误放行）

合法 quoted DEFINER 过程：
CREATE DEFINER='admin'@'localhost' PROCEDURE p_def(IN x INT) SELECT x;
-> SQL类型=SELECT；R051 + E999_SYNTAX_ERROR（错误拦截）
```

页面证据均为真实登录、选择“集中式”、输入 SQL、点击“开始审核”后读取；不保存口令、token、Authorization 或登录响应体。

## 3. API 证据

普通合法复杂过程：

```text
/sql    -> 1, CREATE PROCEDURE, passed=true, []
/file   -> 1, CREATE PROCEDURE, passed=true, []
/stream -> 1 data frame, passed=true, []
```

非法 `CREATE PROCEDURE p_bad SELECT 1;`：

```text
/sql    -> 200, CREATE PROCEDURE, passed=true, []
/file   -> 200, 1 result, passed=true, []
/upload -> 200, 1 result, passed=true, []
/stream -> 200, 1 data frame, passed=true, []
```

标准 DELIMITER 文件：

```text
/file   -> 1 CREATE PROCEDURE, passed=false, E999
/upload -> 1 CREATE PROCEDURE, passed=false, E999
/stream -> 3 data frames; first E999, later fragments pass
```

## 4. 反向语法矩阵

当前错误通过：

```text
procedure missing ()
leading/trailing/double comma parameters
missing comma between parameters
function IN / function OUT
procedure/function GARBAGE TOKEN body
trigger GARBAGE TOKEN
trigger missing FOR EACH ROW
trigger invalid timing
```

当前错误拦截或元数据错误：

```text
quoted DEFINER procedure -> SELECT + R051 + E999
CURRENT_USER DEFINER procedure -> SELECT + R051 + E999
IF() + CASE expression -> E999
schema-qualified procedure -> created_object_name only records schema
```

## 5. DELIMITER 根因证据

```text
_split_sql_file result:
CREATE PROCEDURE ... END$$

尾部分隔符 $$ 未剥离，parser 在 IN 参数处报 E999；
batch-stream 对 DELIMITER 指令无适配，按体内分号拆成多个结果。
```

Q 的 `test_split_delimiter_dollar_keeps_routine` 只断言结果长度为 1，没有审核返回文本或断言 `$$` 已移除。

## 6. 自动化结果

```text
focused routine/rules/scope:
131 passed, 3 warnings, 0 failed in 4.36s

rule harness:
PASS total=121 covered=109 metadata=7 exempt=5 failures=0

full tests:
1897 passed, 11 warnings, 0 failed in 402.30s

tests_3p against current isolated service:
125 passed, 1 skipped, 2 warnings, 0 failed in 23.35s
```

环境事件：三方错误登录测试触发 IP 登录频率窗口，首次后续登录返回 429；60 秒窗口后成功。按设计的安全控制，不计产品缺陷。

## 7. 根因代码点

```text
backend/engine/parser/parser_legacy.py:242  _if_is_statement
backend/engine/parser/parser_legacy.py:321  _find_routine_head
backend/engine/parser/parser_legacy.py:351  _routine_params_ok
backend/engine/parser/parser_legacy.py:398  _routine_body_complete
backend/engine/parser/parser_legacy.py:438  _routine_structure
backend/engine/parser/parser_legacy.py:483  _routine_compat_fill
backend/engine/parser/parser_legacy.py:497  split_sql_statements_for_audit
backend/engine/checker.py:430                _split_sql_file
backend/api/sql_audit.py:219                batch-stream shared splitter call
tests/test_routine_audit_r6.py:51            delimiter test only locks length
```

## 8. 官方依据

- MySQL CREATE PROCEDURE/FUNCTION：<https://dev.mysql.com/doc/mysql/8.0/en/create-procedure.html>
- MySQL CREATE TRIGGER：<https://dev.mysql.com/doc/refman/8.0/en/create-trigger.html>
- MySQL BEGIN...END / DELIMITER：<https://dev.mysql.com/doc/refman/8.0/en/begin-end.html>
- TDSQL MySQL 分布式使用限制：<https://cloud.tencent.com/document/product/557/47511>

## 9. 边界声明

- 未直连 Mr.Linsang 的内网 TDSQL。
- 未执行目标麒麟 V10 SP3 部署后 12/0/0。
- 未执行生产容量或生产性能测试。
- 用户未跟踪文档未读取、未修改、未暂存。
- 本轮仅新增 UAT 报告与证据索引，不改产品代码。
