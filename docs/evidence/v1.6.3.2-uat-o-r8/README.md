# v1.6.3.2 第八轮定点复测证据索引（智能体 O）

## 1. 基线与范围

- 被测提交：`e5f63d254852e06dcbd16b6176f3020e21bf4ffa`
- 日期：2026-09-05
- 只复测：`UAT-O-1632-R7-01`、`UAT-O-1632-R7-02`
- 不执行：全量回归、其他规则/模块、GATE-1/GATE-3 重验、麒麟部署、性能测试
- 裁决：R7-01 关闭；R7-02 部分修复、保持 P1 未关闭；GATE-2 继续阻断

## 2. R7-01 证据

浏览器集中式即时审核：

```text
quoted DEFINER + IN -> 审核通过，CREATE PROCEDURE，0违规
IF() + CASE表达式 -> 审核通过，CREATE PROCEDURE，0违规
缺参数括号 -> E999
FUNCTION(IN...) -> E999
空参数段 -> E999
GARBAGE TOKEN过程体 -> E999
缺FOR EACH ROW触发器 -> E999
```

独立引擎矩阵：5 个合法场景全部通过；上一轮 12 个非法场景全部 `passed=false + E999`；限定名为 `db1.p`。

缺括号过程真实 HTTP：

```text
/sql          -> 200, CREATE PROCEDURE, passed=false, [E999]
/file         -> 200, 1 result, passed=false, [E999]
/upload       -> 200, 1 result, passed=false, [E999]
/batch-stream -> 200, 1 data frame, CREATE PROCEDURE, passed=false, [E999]
```

结论：`UAT-O-1632-R7-01` 关闭。

## 3. R7-02 证据

标准脚本：

```sql
DELIMITER $$
CREATE PROCEDURE p_d(IN x INT)
BEGIN
  SET @a = x;
  SET @b = x;
END$$
DELIMITER ;
```

通过入口：

```text
split_audit_script -> 1段，line 2~6，无DELIMITER/$$
/file              -> 1 CREATE PROCEDURE，passed=true，[]
/upload            -> 1 CREATE PROCEDURE，passed=true，[]
/batch-stream      -> meta + 1 data，sql_type=CREATE PROCEDURE，passed=true，[]
文件审核页面        -> SQL总数1，通过1，未通过0，通过率100%，CREATE PROCEDURE
```

失败入口：

```text
/sql -> 200, CREATE PROCEDURE, passed=false, [E999]
即时审核页面 -> 发现1项违规，CREATE PROCEDURE，E999；错误原文仍含 DELIMITER/$$
```

根因：`audit_single_sql()` 先得到 `statements[0]`，但 `len(statements)<=1` 分支仍调用 `checker.audit_sql(sql)` 审核原文。

结论：`UAT-O-1632-R7-02` 保持未关闭，不新增缺陷编号。

## 4. 专项自动化

```text
pytest tests/test_routine_audit_r7.py -q
72 passed, 3 warnings, 0 failed in 4.14s
```

未跑全量测试，符合本轮定点复测指令。

## 5. 环境收尾

- 使用 `127.0.0.1:8002`、真实浏览器、真实认证、专用临时 MySQL 元数据库；
- 测试服务已停止，端口 8002 已释放；
- 临时 SQL 文件与本轮专用元数据库已删除；
- 现有元数据库、产品代码和用户未跟踪文档均未改动。
