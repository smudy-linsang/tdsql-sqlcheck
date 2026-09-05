# v1.6.3.2 第六轮生产门禁整改复测证据索引（智能体 O）

## 1. 基线与裁决

- 被测提交：`76752ef47113fcd9af63d4949dff024bad382978`
- 分支：`main`，测试开始时与 `origin/main` 一致
- 日期：2026-09-05
- 结论：**不通过；R5-02 关闭、GATE-3 技术复测通过；R5-01 继续 P1 未关闭、GATE-2 阻断**
- 当前缺陷：`P0=0、P1=1、P2=0、P3=0`
- O 未修改产品代码。

## 2. 文档与差异核对

已核对：

1. `docs/GATE-DECISION-v1.6.3.2-生产发布门禁签署决议与整改任务书.md`；
2. `docs/UAT-v1.6.3.2-审核规则调整与扫描历史跨页对比第五轮门禁整改复测报告-智能体O.md`；
3. `docs/DEV-v1.6.3.2-审核规则调整与扫描历史跨页对比开发报告.md` Rev.R6；
4. `204238b1..76752ef` 的 parser、AuditService、测试及文档实际 diff。

## 3. 浏览器证据

隔离服务：`127.0.0.1:8002`，当前提交，页面版本 v1.6.3.2。

```text
集中式 CREATE VIEW v_order AS SELECT 1 AS id:
PASS；SQL类型=CREATE VIEW；无建表规则误报

分布式 VALUES LESS THAN /*合法注释*/ MAXVALUE:
仅 R121；absent=E999,R003,R004,R005,R028,R118

集中式 CREATE PROCEDURE p_in(IN x INT) SELECT x:
SQL类型=CREATE PROCEDURE
E999_SYNTAX_ERROR: Expecting ). Line 1, Col: 26 ... IN x INT ...

集中式 CREATE PROCEDURE ... BEGIN IF ... END IF; SET ...; END:
SQL类型=BATCH；例程未作为单一对象返回
```

页面操作均为真实登录、真实选择实例架构、输入 SQL、点击“开始审核”并读取页面结果。仓库不保存测试口令、token、Authorization 或登录响应体。

## 4. 独立拆分矩阵

`split_sql_statements_for_audit`：

```text
simple BEGIN/END     -> 1
nested BEGIN/END     -> 1
IF/END IF            -> 3
CASE/END CASE        -> 3
WHILE/END WHILE      -> 3
LOOP/END LOOP        -> 3
REPEAT/END REPEAT    -> 3
CASE expression/END  -> 3
```

多行文件路径：

```text
RuleChecker._split_sql_file(IF) -> 3
AuditService.audit_file_content(IF) ->
  CREATE PROCEDURE, UNKNOWN, UNKNOWN
database.split_sql_statements(IF)（/batch-stream 当前调用） -> 4
```

## 5. 参数与解析矩阵

```text
centralized procedure IN     -> CREATE PROCEDURE / E999_SYNTAX_ERROR
centralized procedure OUT    -> CREATE PROCEDURE / E999_SYNTAX_ERROR
centralized procedure INOUT  -> CREATE PROCEDURE / E999_SYNTAX_ERROR
centralized no-param IF proc -> BATCH / no violations（类型与边界仍错误）
centralized IF function      -> BATCH / E999_SYNTAX_ERROR

whole parser IN/OUT/INOUT -> Expecting ) at parameter name
whole parser IF function  -> Invalid expression / Unexpected token
```

## 6. 自动化证据

```text
focused rules/scope:
99 passed, 3 warnings, 0 failed in 4.04s

rule harness:
PASS total=121 covered=109 metadata=7 exempt=5 failures=0

full tests:
1865 passed, 11 warnings, 0 failed in 402.13s

tests_3p against current isolated service 127.0.0.1:8002:
125 passed, 1 skipped, 2 warnings, 0 failed in 22.43s
```

## 7. 根因代码点

```text
backend/engine/parser/parser_legacy.py:242
  新审核拆分器只用 BEGIN/END 单深度，END IF 等误减外层 BEGIN

backend/services/audit_service.py:191
  只有即时审核入口改用新拆分器

backend/engine/checker.py:430
  文件审核保留独立的字符串 BEGIN/END 判断

backend/api/sql_audit.py:219
  /batch-stream 仍调用 database.split_sql_statements

backend/engine/parser/parser_legacy.py:118
  MAXVALUE token/span 修复有效

backend/engine/parser/parser_legacy.py:2734,3074,3096
  非 TABLE CREATE 分流修复有效
```

## 8. 官方依据

- MySQL CREATE PROCEDURE 参数与例程体：<https://dev.mysql.com/doc/mysql/8.0/en/create-procedure.html>
- MySQL Compound Statement：<https://dev.mysql.com/doc/refman/8.0/en/sql-compound-statements.html>
- MySQL IF：<https://dev.mysql.com/doc/refman/8.0/en/if.html>
- MySQL LOOP 嵌套 IF 示例：<https://dev.mysql.com/doc/refman/8.0/en/loop.html>
- TDSQL MySQL 分布式限制：<https://cloud.tencent.com/document/product/557/47511>

## 9. 边界声明

- 未直连用户内网 TDSQL；用户截图与 O 本地复测严格区分。
- 未执行目标麒麟 V10 SP3 部署后 12/0/0。
- 未执行生产容量或生产性能测试。
- 用户自有未跟踪文件 `docs/PRODUCTION-DEPLOY-ISSUES-v1.6.3.0.md` 未读取、未修改、未暂存。
- 临时测试服务仅用于当前提交复测；报告不记录凭据与内网连接信息。
