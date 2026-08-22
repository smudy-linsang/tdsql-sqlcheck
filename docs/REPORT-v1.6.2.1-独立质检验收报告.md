# REPORT-v1.6.2.1 R061 索引命名规则反引号未剥离导致系统性误报修复 独立质检验收报告

| 质检项 | 详细内容 |
|---|---|
| **质检版本** | **v1.6.2.1** |
| **质检对象** | 核心提交 `db6d78c` (Q 修复实现)、`e02d15f` (测试资产加固)、`29a0786` (A 复测准出) |
| **设计依据** | [`docs/DESIGN-v1.6.2.1-R061索引命名规则反引号未剥离导致系统性误报修复详细设计说明书.md`](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/docs/DESIGN-v1.6.2.1-R061%E7%B4%A2%E5%BC%95%E5%91%BD%E5%90%8D%E8%A7%84%E5%88%99%E5%8F%8D%E5%BC%95%E5%8F%B7%E6%9C%AA%E5%89%95%E7%A6%BB%E5%AF%BC%E8%87%B4%E7%B3%BB%E7%BB%9F%E6%80%A7%E8%AF%AF%E6%8A%A5%E4%BF%AE%E5%A4%8D%E8%AF%A6%E7%BB%86%E8%AE%BE%E8%AE%A1%E8%AF%B4%E6%98%8E%E4%B9%A6.md) Rev.A |
| **现场来源** | [`docs/ANALYSIS-v1.6.2.0-上线后扫描结果分析_A.md`](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/docs/ANALYSIS-v1.6.2.0-%E4%B8%8A%E7%BA%BF%E5%90%8E%E6%89%AB%E6%8F%8F%E7%BB%93%E6%9E%90_A.md)（用户人工测试报告 `Extracted_Schema_Report_6297.html`） |
| **复测依据** | [`docs/RETEST-v1.6.2.1-R061索引名反引号误报修复独立复测报告_A.md`](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/docs/RETEST-v1.6.2.1-R061%E7%B4%A2%E5%BC%95%E5%90%8D%E5%8F%8D%E5%BC%95%E5%8F%B7%E8%AF%AF%E6%8A%A5%E4%BF%AE%E5%A4%8D%E7%8B%AC%E7%AB%8B%E5%A4%8D%E6%B5%8B%E6%8A%A5%E5%91%8A_A.md) |
| **质检结论** | **【准出（PASS）】误报彻底消除，真实违规完整保留，测试护栏坚固，准予发版** |
| **质检日期** | 2026-08-22 |

---

## 一、 验收结论概述

经过独立第三方的全量代码审查、生产 14 表 1:1 回放复测、12 支专用测试套件核验、四向变异注入实验及全语料漂移扫描：
1. **78.6% 系统性误报彻底消除**：
   - 现场扫描报告中被错误报警“应以 idx_ 开头”的 11 张合规索引表（`idx_trace`, `idx_user`, `idx_cust_id`, `idx_user_id`, `idx_city`, `idx_id_no`, `idx_dict_type`, `idx_product_type`, `idx_account_no` 等）**误报全部清零**。
   - 真实违规的 3 张表（#3 `cus_bas_corp_contact_IDX1`、#4 `cus_bas_corp_contact_addr_IDX1`、#5 `CUS_NAME_LIST_TYPE_IDX1`）**100% 准确命中 R061**。
2. **告警可读性显著提升**：
   - 告警提示信息中完整保留了 DDL 原生大小写索引名（如 `CUS_NAME_LIST_TYPE_IDX1` 而非全小写 `cus_name_list_type_idx1`），确保开发与 DBA 能按提示快速在库中检索定位。
3. **实现极致收敛与非侵入**：
   - 产品代码仅修改 [`backend/engine/rules/index.py`](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/backend/engine/rules/index.py) 1 处（+14 行），严格遵循 8 条 NG 红线，未触碰解析器与其他任何规则。
4. **测试资产加固到位**：
   - T-1 / T-2 测试资产缺陷均已闭合，变异测试（M1~M4）证明新增测试套件具备对退化、放宽及误伤关联规则的即时硬拦截能力。
5. **质检结论**：**准予准出（PASS）**。

---

## 二、 缺陷根因与修复核验

### 2.1 缺陷根因图解

```text
[TDSQL / MySQL 内核 SHOW CREATE TABLE 导出 DDL]
          │
          ▼
   KEY `idx_trace` (`user_id`)  ──► 索引名恒带有反引号
          │
          ▼
   解析器提取 parsed.indexes: [{"name": "`idx_trace`", "type": "NORMAL", ...}]
          │
          ▼
   R061 规则原实现: idx_name = idx.get("name", "").lower()  ──► 未剥离反引号！
          │
          ▼
   判断: idx_name.startswith("idx_")
          └── "`idx_trace`" 首字符为反引号 '`'，恒等于 False ──► 【系统性误报！】
```

### 2.2 修复代码精细审查

在 [`backend/engine/rules/index.py`](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/backend/engine/rules/index.py) 中补齐了针对索引名称的引号剥离与展示名称分离：

```python
for idx in parsed.indexes:
    # 补齐去引号，字符集与 naming.py:98、distributed.py:187 保持一致
    raw_name = idx.get("name", "").strip('`"\' ')
    idx_name = raw_name.lower()
    idx_type = idx.get("type", "NORMAL")
    if not idx_name:
        continue
    if idx_type == "PRIMARY":
        if not idx_name.startswith("pk_"):
            return self._make_violation(f"主键索引 '{raw_name}' 应以 pk_ 开头")
    elif idx_type == "UNIQUE":
        if not idx_name.startswith("uk_"):
            return self._make_violation(f"唯一索引 '{raw_name}' 应以 uk_ 开头")
    else:
        if not idx_name.startswith("idx_"):
            return self._make_violation(f"普通索引 '{raw_name}' 应以 idx_ 开头")
```

### 2.3 关键质量要求核验清单

| 质量要求 | 设计与准出标准 | 实测状态 | 判定 |
|---|---|---|:---:|
| **去引号完整性** | 支持剥离反引号 `` ` ``、单双引号 `"` / `'` 及首尾空格 | `strip('`"\' ')` 精确覆盖 | ✅ 合规 |
| **大小写一致性** | 判定逻辑不区分大小写，提示信息保留原大小写 | 比对使用 `idx_name`，告警展示 `raw_name` | ✅ 合规 |
| **判定口径保真** | 严禁放宽 R061 的 `pk_` / `uk_` / `idx_` 前缀规则 | 前缀判定逻辑原样保留 | ✅ 合规 |
| **零副作用保证** | 不改动 parser，不改动其他规则，不破坏已有通过判定 | 8 条 NG 红线 100% 遵守 | ✅ 合规 |

---

## 三、 独立质检与实测验证结果

### 3.1 生产 14 表 1:1 回放比对（报告 6297 实测）

对 `Extracted_Schema_Report_6297` 涉及的全部 14 张表执行完整规则引擎复测：

| # | 表名 | 索引名称（DDL 原文） | v1.6.2.0 现状 | v1.6.2.1 实测结果 | 变动判定 |
|---:|---|---|---|---|:---:|
| 1 | `big_audit_trail` | `` `idx_trace` ``, `` `idx_event` `` | 误报 R061 | **不触发 R061** | ✅ 误报消除 |
| 2 | `big_order_log` | `` `idx_user` `` | 误报 R061 | **不触发 R061** | ✅ 误报消除 |
| **3** | **`cus_bas_corp_contact`** | `` `cus_bas_corp_contact_IDX1` `` | 触发 R061 | **触发 R061**（提示含原名 `cus_bas_corp_contact_IDX1`） | ✅ 真实违规保留 |
| **4** | **`cus_bas_corp_contact_addr_20260511`** | `` `cus_bas_corp_contact_addr_IDX1` `` | 触发 R061 | **触发 R061**（提示含原名 `cus_bas_corp_contact_addr_IDX1`） | ✅ 真实违规保留 |
| **5** | **`cus_name_list_type`** | `` `CUS_NAME_LIST_TYPE_IDX1` `` | 触发 R061 | **触发 R061**（提示含原名 `CUS_NAME_LIST_TYPE_IDX1`） | ✅ 真实违规保留 |
| 6 | `t_account` | `` `idx_cust_id` `` | 误报 R061 | **不触发 R061** | ✅ 误报消除 |
| 7 | `t_audit_log` | `` `idx_user_id` `` | 误报 R061 | **不触发 R061** | ✅ 误报消除 |
| 8 | `t_branch` | `` `idx_city` `` | 误报 R061 | **不触发 R061** | ✅ 误报消除 |
| 9 | `t_customer` | `` `idx_id_no` `` | 误报 R061 | **不触发 R061** | ✅ 误报消除 |
| 10 | `t_deposit` | `` `idx_cust_id` `` | 误报 R061 | **不触发 R061** | ✅ 误报消除 |
| 11 | `t_dict` | `` `idx_dict_type` `` | 误报 R061 | **不触发 R061** | ✅ 误报消除 |
| 12 | `t_loan` | `` `idx_cust_id` `` | 误报 R061 | **不触发 R061** | ✅ 误报消除 |
| 13 | `t_product` | `` `idx_product_type` `` | 误报 R061 | **不触发 R061** | ✅ 误报消除 |
| 14 | `t_transaction` | `` `idx_account_no` `` | 误报 R061 | **不触发 R061** | ✅ 误报消除 |

- **统计结果**：
  - R061 报出总数由 **14 张降至 3 张**（11 张虚假误报 100% 消除）。
  - 全表违规条目总数由 **43 条降至 32 条（−25.6%）**，减少的 11 条全部对应虚假 R061。
  - 通过表数保持 **0 → 0**（所有存在其他真实违规的表均未被误判为“通过”，安全边界完好）。

### 3.2 自动化测试套件执行

执行 [`tests/test_r061_index_name_quoting.py`](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/tests/test_r061_index_name_quoting.py)：
- **执行结果**：12 passed, 0 failed (耗时 1.99s)。
- **覆盖场景**：
  - `P1/P2/E1/E4`：反引号合规索引、裸名索引、大写 `IDX_` 索引、长索引名均不误报。
  - `N1/N2/N3`：反引号非 idx 索引、裸名非 idx 索引、混合多索引场景精确报警并保留原始大小写。
  - `U1/U2`：ADJ-5 UNIQUE 死代码分支现状锁定。
  - `G1/G2`：生产 #1 与生产 #5 DDL 回放及子集安全断言。

### 3.3 变异注入实验（拦截能力验证）

在独立沙箱中故意引入四种错误改法，检验用例集拦截能力：
1. **变异 M1（退回反引号未剥离缺陷版）**：`test_p1` 等 5 支用例即刻红灯拦截 ✅
2. **变异 M2（过度放宽判定口径）**：`test_n1` 等 4 支用例即刻红灯拦截 ✅
3. **变异 M3（告警文本丢失大小写）**：`test_n1`、`test_g2` 2 支用例即刻红灯拦截 ✅
4. **变异 M4（人为误伤关联规则 R036）**：`test_g1` 核心安全性质断言即刻红灯拦截 ✅

### 3.4 版本号与全平台一致性核验

| 检查项 | 目标值 | 当前实测值 | 状态 |
|---|---|---|:---:|
| [`VERSION`](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/VERSION) | `1.6.2.1` | `1.6.2.1` | ✅ |
| [`backend/config.py`](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/backend/config.py) | `APP_VERSION = "1.6.2.1"` | `1.6.2.1`（描述对齐） | ✅ |
| [`frontend/index.html`](file:///c:/TDSQL_SQLCHECK/TDSQL-SQLCheck/frontend/index.html) | Title / 品牌 / 静态资源缓存戳对齐 `V1.6.2.1` | 全部对齐 `1.6.2.1` | ✅ |

---

## 四、 最终质检准出结论

| 验收维度 | 评估标准 | 实测状态 | 结论 |
|---|---|:---:|:---:|
| **误报清除率** | 11 条假告警 100% 消除 | 11/11 清除 | **PASS** |
| **真告警留存率** | 3 条真实违规 100% 命中且保留大小写 | 3/3 命中 | **PASS** |
| **非侵入安全性** | 全语料除 R061 外 0 漂移，通过数 0→0 | 0 漂移，0 误放行 | **PASS** |
| **测试完备性** | 覆盖反引号/大小写/混合场景，变异硬拦截 | 12/12 通过，M1-M4 均拦截 | **PASS** |
| **版本一致性** | 核心文件与前端资源版本号全量对齐 | 100% 对齐 | **PASS** |

**综上所述，v1.6.2.1 版本已彻底解决 R061 索引名反引号误报问题，各项技术与业务指标全量达标，质检验收通过（准予发版）！**
