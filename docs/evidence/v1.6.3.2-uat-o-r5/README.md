# v1.6.3.2 第五轮生产门禁整改复测证据索引（智能体 O）

## 1. 基线与裁决

- 被测提交：`204238b1ae3610581ae346bdaef700eefeebe700`
- 分支：`main`，测试开始时与 `origin/main` 一致
- 日期：2026-09-05
- 结论：**不通过；GATE-2/GATE-3 不具备复签条件；生产发布阻断**
- 新缺陷：`UAT-O-1632-R5-01`（P1）、`UAT-O-1632-R5-02`（P2）
- O 未修改产品代码。

## 2. 文档核对

已核对：

1. `docs/GATE-DECISION-v1.6.3.2-生产发布门禁签署决议与整改任务书.md`；
2. `docs/DEV-v1.6.3.2-审核规则调整与扫描历史跨页对比开发报告.md` Rev.R5；
3. `docs/DESIGN-v1.6.3.2-审核规则调整与扫描历史跨页对比详细设计说明书.md` Rev.E；
4. 当前提交对 parser、R031、规则数量及测试的实际 diff。

## 3. 用户提供的内网证据

- 材料：真实 TDSQL 控制台建表成功截图（仓库外，不复制）；
- 内容：`t_order_history`，一级 `shardkey=order_id`，二级 `RANGE(YEAR(create_time))`，含 bare `p_max VALUES LESS THAN MAXVALUE`；
- 截图 SHA-256：`339EAD545CBB350A2FE0D1C89F43B56FA298B034C7C4889BD92A041F9A8E7624`；
- 证据属性：用户提供的内网真实执行结果；O 未直连内网目标实例。

## 4. 浏览器实测摘要

当前提交隔离服务：`127.0.0.1:8001`。使用真实浏览器登录并点击即时审核：

```text
规则库：总数 121；R030/R031/R032 = 仅分布式；集中式 90 / 跳过 31

用户同形 bare MAXVALUE（分布式）：
R028,R036,R104,R121
absent=E999,R003,R004,R005,R118

集中式 VIEW：R003,R004,R005,R028
集中式 PROCEDURE：R001,R003,R004,R005,R028
集中式 FUNCTION：R001,R003,R004,R005,R028
集中式 TRIGGER：PASS
集中式 TEMPORARY TABLE：R037(INFO)，无 ERROR
集中式 compound FUNCTION：被拆为 BATCH，产生建表类误报

注释边界 MAXVALUE（分布式）：
E999,R003,R004,R005,R028,R121
```

## 5. 根因代码点

```text
backend/engine/parser/parser_legacy.py:2624
  任意 exp.Create 均进入 _parse_create

backend/engine/parser/parser_legacy.py:2954
  _parse_create 无条件 is_create_table = True

backend/services/audit_service.py:190
  即时审核先调用 split_sql_statements

backend/services/database.py:119
  分号拆分器不了解 routine BEGIN/END 过程体

backend/engine/parser/parser_legacy.py:122
  bare MAXVALUE 正则归一化按注释/字面量分段，关键字间注释会打断匹配
```

## 6. 自动化证据

```text
focused rules/scope:
78 passed, 3 warnings

rule harness:
PASS total=121 covered=109 metadata=7 exempt=5 failures=0

full tests:
1844 passed, 11 warnings, 0 failed in 408.81s

tests_3p first run (invalid environment, default 127.0.0.1:8899):
13 failed, 2 passed, 1 xfailed, 110 errors
root cause=service/login fixture unavailable; not used as product verdict

tests_3p against current isolated service 127.0.0.1:8001:
125 passed, 1 skipped, 2 warnings, 0 failed in 19.77s
```

## 7. 官方依据

- TDSQL 建表/分区语法：<https://cloud.tencent.com/document/product/557/8767>
- MySQL RANGE / bare MAXVALUE：<https://dev.mysql.com/doc/refman/8.0/en/partitioning-range.html>
- MySQL 行内块注释：<https://dev.mysql.com/doc/refman/8.0/en/comments.html>

## 8. 边界声明

- 未直连用户内网 TDSQL；截图事实与 O 本地复测严格分开。
- 未执行目标麒麟 V10 SP3 部署后 12/0/0。
- 未执行生产容量或生产性能测试。
- 本机 `8000` 的旧进程未作为证据；被测服务单独运行于 `8001`。
- 用户自有未跟踪文件 `docs/PRODUCTION-DEPLOY-ISSUES-v1.6.3.0.md` 未读取、未修改、未暂存。
- 仓库不保存测试口令、token、Authorization、登录响应体或内网连接信息。
