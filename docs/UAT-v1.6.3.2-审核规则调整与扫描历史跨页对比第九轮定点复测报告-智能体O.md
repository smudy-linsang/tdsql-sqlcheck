# v1.6.3.2 审核规则调整与扫描历史跨页对比
## 第九轮定点复测报告（智能体 O）

| 项目 | 内容 |
|---|---|
| 测试版本 | v1.6.3.2 |
| 被测提交 | `5775ddfd6efc101bf4969ae78590d1fa4379903b` |
| 唯一复测对象 | `UAT-O-1632-R7-02` 的即时审核残留 |
| 测试日期 | 2026-09-05 |
| 测试人 | 智能体 O（独立 UAT） |
| 门禁签署人 | Mr.Linsang |
| 最终结论 | **通过；UAT-O-1632-R7-02 关闭，GATE-2 技术缺陷清零，待 Mr.Linsang/G 完成书面签署** |

---

## 1. 范围纪律

按 Mr.Linsang 指令，本轮只复测第八轮尚未关闭的一个残留：

> `audit_service.audit_single_sql()` 在 `split_audit_script()` 只返回一条清洗后语句时，是否使用 `statements[0]`，从而使标准 `DELIMITER` 脚本经 `/api/v1/audit/sql` 和即时审核页面正确通过。

本轮没有重新测试第八轮已关闭的 `UAT-O-1632-R7-01`，也没有重新进行文件/上传/流式页面或真实 HTTP 入口复测；专项锁仅按 Q 新增用例调用文件检查器与即时结果作对照。其他规则、跨页选择、其他模块、GATE-1/GATE-3、目标麒麟部署及性能均未测试；没有执行全量回归和 `tests_3p`。这些项目沿用既有结论或保持未验证状态，不得把本报告解释为全项目重新验收。

---

## 2. 代码范围核对

Q 提交 `5775ddf` 的产品代码只修改：

```text
backend/services/audit_service.py
```

实际实现与第八轮“照图施工”要求一致：

1. `len(statements) == 1` 时设置 `audit_sql = statements[0]`；
2. `checker.audit_sql()` 审核 `audit_sql`，不再审核含客户端指令的原始 `sql`；
3. `_apply_shard_key_check()` 同样接收 `audit_sql`；
4. `len(statements) == 0` 保留原失败关闭口径；
5. 多段分支未改；
6. 新增 3 条即时入口回归锁。

未发现超出第八轮整改方案的产品代码变更。

---

## 3. 定点测试脚本

```sql
DELIMITER $$
CREATE PROCEDURE p_d(IN x INT)
BEGIN
  SET @a = x;
  SET @b = x;
END$$
DELIMITER ;
```

---

## 4. 复测结果

### 4.1 专项自动化

执行：

```text
python -m pytest tests/test_routine_audit_r7.py -q -k immediate_entry
```

结果：

```text
3 passed, 72 deselected, 3 warnings in 4.28s
```

三条测试分别锁定：

- 即时单段使用清洗后 SQL；
- 即时入口与文件入口结果一致；
- 普通单语句不受该分支修改影响。

### 4.2 真实 HTTP `/api/v1/audit/sql`

在隔离服务上使用真实管理员令牌提交上述脚本及 `instance_type=centralized`：

```text
HTTP 200
sql_type = CREATE PROCEDURE
passed = true
violations = []
```

第八轮相同请求的 `passed=false + E999` 已消失。

### 4.3 真实浏览器点击

操作路径：

1. 登录 v1.6.3.2 隔离环境；
2. 点击“SQL审核 → 即时审核”；
3. 选择“集中式规则”；
4. 粘贴第 3 节完整 `DELIMITER $$` 脚本；
5. 点击“开始审核”。

页面实际显示：

```text
审核通过
SQL类型: CREATE PROCEDURE
适用架构: 集中式
（集中式审核：已排除 31 条分布式规则）
```

页面不再显示 E999，满足第八轮定义的唯一页面关闭动作。

### 4.4 审计历史

页面操作写入的最新审计记录：

```text
audit_type = sql
source = api
total_sql = 1
passed = 1
failed = 0
result.sql_type = CREATE PROCEDURE
result.passed = true
```

`results_json` 中保存的实际 SQL：

- 以 `CREATE PROCEDURE` 开始；
- 不含 `DELIMITER` 指令；
- 不含尾部分隔符 `$$`。

因此“审核结果与历史保存对象一致”的关闭条件也已满足。

---

## 5. 缺陷与门禁裁决

| 项目 | 第九轮裁决 |
|---|---|
| `UAT-O-1632-R7-01` | 沿用第八轮 **已关闭**；本轮未重测 |
| `UAT-O-1632-R7-02` | **关闭** |
| 第七轮遗留 P1 | **0** |
| 本轮新增缺陷 | **0** |
| GATE-1 | Mr.Linsang 已签署通过；本轮不重验 |
| GATE-2 | **技术缺陷已清零，具备书面签署条件** |
| GATE-3 | Mr.Linsang 已签署通过；本轮不重验 |

本报告只完成技术复测裁决，不代替 Mr.Linsang/G 的 GATE-2 书面签署，也不代替目标麒麟环境发布验证。

---

## 6. 环境收尾

- 使用 `127.0.0.1:8002`、真实浏览器、真实认证和本轮专用临时 MySQL 元数据库；
- 测试服务已停止，端口 8002 已释放；
- 本轮专用临时元数据库已删除；
- 现有元数据库、产品代码和用户未跟踪文档未改动。

---

## 7. 最终裁决

**第九轮定点复测通过。**

Q 已正确修复即时审核单段分支。`UAT-O-1632-R7-02` 关闭，第七轮两项 P1 已全部清零，GATE-2 已具备技术签署条件。后续由 Mr.Linsang/G 完成 GATE-2 书面签署；生产发布仍应遵循既定发布门禁及目标环境验证流程。

证据索引：`docs/evidence/v1.6.3.2-uat-o-r9/README.md`。
