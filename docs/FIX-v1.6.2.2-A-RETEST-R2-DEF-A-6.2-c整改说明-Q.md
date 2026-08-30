# v1.6.2.2 A 复测 R2 遗留项 DEF-A-6.2-c 整改说明

| 项 | 内容 |
|---|---|
| 修复人 | 智能体 Q |
| 修复日期 | 2026-08-30 |
| 依据 | `RETEST-v1.6.2.2-A复测遗留三项整改复测报告-ClaudeA.md`（R2.1，被测提交 `974561a`） |
| 处置口径 | R2 收敛后唯一遗留项 DEF-A-6.2-c；DEF-A-6.2-b 与 ENV-1 本轮确认关闭，无需改动 |

---

## 一、处置总览

| 编号 | 项 | R2 结论 | 本轮处置 |
|---|---|---|---|
| DEF-A-6.2-b | 转义引号分段正则 | ✅ 已关闭（上轮修复，A 复测确认） | 无需改动 |
| **DEF-A-6.2-c** | OBS-1 门控放宽引入的新洞 | ⚠️ 唯一遗留项 | **已修复**（`select` 判定收敛到普通代码段） |
| ENV-1 | MariaDB 默认值引号 | ✅ 不适用（内网元数据库为 TDSQL/MySQL），Q 改动已实测在 MySQL 侧为空操作，无需回退 | 按 A §4.3 建议在部署说明写明支持范围 |

---

## 二、DEF-A-6.2-c（唯一遗留项）：CTAS 门控收敛到普通代码段

**根因**（与 A §3.2 判定一致）：上一轮 OBS-1 把 CTAS 门控改成"全文找 `\bselect\b`"，代价是**注释/反引号标识符里出现 `select` 的普通建表也被误判为 CTAS**，A-6.2 对这些表静默失效（8 例探测中 5 例失效）——换掉的洞比补上的更容易被踩到。

**修复**（`parser_legacy.py`，按 A §3.3 已验证方案）：`_strip_index_order_modifiers()` 复用已有的 `_LITERAL_OR_COMMENT_RE` 分段结果，CTAS 判定只在**普通代码段**（字面量/注释/反引号标识符之外，即 `split` 结果的偶数下标段）查找 `select`：

```python
parts = _LITERAL_OR_COMMENT_RE.split(sql)
if any(re.search(r"\bselect\b", seg, re.IGNORECASE) for seg in parts[0::2]):
    return sql  # CTAS：select 在代码段内
for i in range(0, len(parts), 2):
    parts[i] = _INDEX_ORDER_STRIP_RE.sub("", parts[i])
```

- CTAS 三种形态（`AS SELECT` / 无 `AS` / `AS (SELECT …)`）的 `select` 都在代码段内 → 仍不剥离；
- 注释含 `select`（`'select 结果缓存'` / `-- select 说明`）、反引号列名 `` `select` `` 均在非代码段 → 不再误判为 CTAS，剥离正常生效；
- 分段只算一次，门控与剥离共用同一份分段结果（无重复开销）。

**验证**（新增 3 用例）：
- 表注释含 `select` 的建表 + DESC 主键：无 E999、无 R003、R054/R077 不误报（A 核心反例）；
- 行注释含 `select` 的建表：剥离正常生效；
- 反引号列名 `` `select` `` + DESC：剥离正常生效；
- 既有 CTAS 三形态与全部守卫用例保持。

## 三、ENV-1 关闭落档

内网元数据库经使用方确认为 TDSQL/MySQL，ENV-1 判定不适用；`_normalize_default` 的引号剥离在 MySQL 侧为空操作，无需回退。按 A §4.3 建议，已在 `deploy/README.md` 顶部写明：**元数据库仅支持 MySQL 8 / TDSQL（MariaDB 不在支持范围内）**。

---

## 四、验证汇总

| 项 | 结果 |
|---|---|
| 新增用例（DEF-A-6.2-c ×3） | 全部通过 |
| A 前两轮全部定向用例（16 条）+ R077/R054 既有 41 条 + O-14 | 全部保持 |
| 全量自动化 | **1622 passed / 0 failed**（上轮 1619 + 本轮新增 3） |
| 正式门禁 `run_all.py --mode implementation --matrix` | **退出码 0、`RESULT PASS`**（三版本矩阵、冻结 71 项、manifest/codestat 基线、设计包哈希全通过） |
| 实现基线 | 已同步重算（bundle `a58b6a7a…`，codestat `1a037af0…`，manifest 不变） |

**建议**：本轮后 A-6.1 / A-6.2 / DEF-A-6.2-b / DEF-A-6.2-c / OBS-1 / ENV-1 全部收口，可请 A 按 R2 建议做最终复测关闭。
