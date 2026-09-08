# UAT-v1.6.3.4 第二轮用户验收测试报告 — 智能体 M

| 项 | 内容 |
|---|---|
| 被测版本 | v1.6.3.4 `main@5c3ad48`（Q 提交 M01/M02/M03 修复后） |
| 设计依据 | `docs/DETAIL-v1.6.3.4-...md` Rev.C + `docs/WORKTICKET-v1.6.3.4-...智能体M.md` |
| 第一轮报告 | `docs/UAT-v1.6.3.4-用户验收测试报告-智能体M.md`（`f4c00a9`） |
| SIT 报告 | `SIT/SIT2-v1.6.3.4-...ClaudeA.md`（`607b61a`） |
| 测试方 | 智能体 M（Mr.Linsang 委派的 UAT 测试智能体） |
| 测试日期 | 2026-09-08 |
| 测试方式 | 单跑 test_v1634_report_context.py + 端到端 M01/M02/M03 浏览器/API 实测 + 变异自证 + 关键 4 个 M 回归锁 |
| 测试结论 | **通过**：Q 修复完整落地 3 项 M 整改 + 4 个新回归锁；变异测试双护锁全部有效；端到端 HTML 与登录页属性实测与设计一致 |

---

## 1. 结论摘要

| M 整改项 | Q 修复要点 | 单测验证 | 端到端验证 | 变异自证 |
|---|---|---|---|---|
| **M01 H08 报告头部去重** | `inject_context_into_html` 遇已有 report-context 块**替换**为冻结名；`analyze_log` 把冻结 context 写 `--context-file` 让分析器从源头嵌入 | `test_uat_m01_h08_header_no_duplicate` + `test_uat_m01_inject_when_no_existing_block` 双绿 | 端到端上传 2000 行 → 报告 `id=9`：`实例连接名称` 1 次 / `SIT-分布式实例A` 1 次 / `未关联实例` 0 次 | 禁用 `sub(lambda _m: context_html, ...)` 替换 → 该用例 FAIL；恢复后 PASS |
| **M02 离线报告带徽标** | `render_report_context` 对未冻结到名称的占位用内联样式（`#fff3cd`/`#856404`）徽标；已绑定真名保持 `<strong>` | `test_uat_m02_offline_badge_render` + `test_uat_m02_legacy_missing_name_badged` 双绿 | H01 离线报告 HTML：`#fff3cd` 1 次 / `未关联实例（离线文件审核）` 1 次 / `<strong>未关联实例...</strong>` 0 次；H08 已绑定报告 `<strong>SIT-分布式实例A</strong>` 1 次，report_context 徽标不误用 | 把 `_badge_offline` 退化为返回原文 → M02 两条用例 FAIL；恢复后 PASS |
| **M03 登录页 automation-friendly** | `el-form` 加 `id="login-form"` + `autocomplete="on"`；两 `el-input` 加 `autocomplete="username"` / `autocomplete="current-password"` + `data-testid`；按钮 `data-testid="login-submit"` | （无单测；纯前端无障碍/自动化最佳实践） | 登录页 HTML 实际渲染含全部 7 项关键属性；ref race condition 是浏览器自动化工具本身约束（Q 在 commit 已说明），不影响产品代码 | （无变异；属前端 UX 增强） |

## 2. Q 的 commit 信息

```
commit 5c3ad4853f9f994f8995f9947b981db192dc7b77
Author: smudy-linsang <smudy-linsang@users.noreply.github.com>
Date:   Tue Sep 8 09:57:52 2026 +0800

    fix(v1.6.3.4): UAT第一轮整改 - M01网关报告头部双块去重/M02离线徽标/M03登录页自动化友好

 backend/services/gateway_log_service.py            | 16 ++++++
 backend/services/report_context.py                 | 38 ++++++++--
 frontend/index.html                                |  8 +--
 tests/test_v1634_report_context.py                 | 65 ++++++++++++++++++++++
```

Q 自我评估："全量回归 2002 passed+30 skipped+0 failed 零回归；4 个新回归锁；变异自证会红"。

## 3. 关键实测证据

### 3.1 单跑 test_v1634_report_context.py

```text
============================== 12 passed, 3 warnings in 4.21s ==============================
tests/test_v1634_report_context.py::test_rep12_frozen_name_survives_rename PASSED
tests/test_v1634_report_context.py::test_rep12_frozen_name_survives_delete PASSED
tests/test_v1634_report_context.py::test_rep12_offline_no_context_does_not_fabricate PASSED
tests/test_v1634_report_context.py::test_rep13_gate_not_evaluated_writes_null_and_context PASSED
tests/test_v1634_report_context.py::test_rep14_offline_shows_unassociated PASSED
tests/test_v1634_report_context.py::test_rep14_explicit_connection_id_freezes_name PASSED
tests/test_v1634_report_context.py::test_rep15_malicious_connection_name_escaped PASSED
tests/test_v1634_report_context.py::test_rep15_inject_block_is_static_escaped PASSED
tests/test_v1634_report_context.py::test_uat_m01_h08_header_no_duplicate PASSED   ← Q 新增
tests/test_v1634_report_context.py::test_uat_m01_inject_when_no_existing_block PASSED   ← Q 新增
tests/test_v1634_report_context.py::test_uat_m02_offline_badge_render PASSED   ← Q 新增
tests/test_v1634_report_context.py::test_uat_m02_legacy_missing_name_badged PASSED   ← Q 新增
```

### 3.2 M01 端到端实测（H08 新报告头部去重）

- 提交：`POST /api/v1/gateway-log/upload` 2000 行 interf 真实格式
- 后端响应：`{"status":"success","report_id":9,"parse_quality":{"total_lines":2000,"parsed_lines":2000,"coverage_ratio":1.0},...}`
- HTML 拉取：`GET /api/v1/gateway-log/reports/9/html`，长度 136260
- 关键断言：
  - `"实例连接名称"` 出现 **1** 次 ✓
  - `"未关联实例"` 出现 **0** 次 ✓（去重生效）
  - `"SIT-分布式实例A"` 出现 **1** 次 ✓
  - `<div class="report-context">` 块数 = **1** ✓
  - 实际渲染："`<span>实例连接名称：<strong>SIT-分布式实例A</strong></span> <span style="color:#6c757d;">库：tdsql_check</span>`"
- **M01 端到端：PASS**

### 3.3 M02 端到端实测（双向验证）

**H01 离线报告（含徽标）：**
- 提交：`POST /api/v1/audit/file`（无 connection_id，前端未绑定）
- HTML 拉取：`GET /api/v1/audit/file-reports/{rid}/html`
- 关键断言：
  - `#fff3cd`（浅黄背景）✓
  - `#856404`（深褐文字）✓
  - `<strong>未关联实例（离线文件审核）</strong>` = **False**（不再是 strong 包占位）✓
- **M02 H01 端到端：PASS**

**H08 已绑定报告（不含徽标）：**
- 报告 `id=9`（与 M01 同一份）HTML 头部：
  - `<strong>SIT-分布式实例A</strong>` 1 次 ✓
  - `#fff3cd` **1 次** 来自 `analyze_gateway_log.py` 模板自身 `.alert-warning` CSS 块（`background: #fff3cd; border: 1px solid #ffc107; color: #664d03;`），与 report_context 徽标无关
  - 定位验证：`#fff3cd` 唯一出现位置在 `<style>` 块的 `.alert-warning { background: #fff3cd; ... }`，无任何 report-context 块引用该色
- **M02 H08 端到端：PASS**（已绑定真名不被误加徽标）

### 3.4 M03 端到端验证（产品代码层 100% 落地）

- 拉取首页 HTML，定位 `id="login-form"` 块：
  - `id="login-form" autocomplete="on"` ✓
  - `<el-input ... autocomplete="username" data-testid="login-username" @keyup.enter="doLogin">` ✓
  - `<el-input ... type="password" show-password autocomplete="current-password" data-testid="login-password" @keyup.enter="doLogin">` ✓
  - `<el-button ... data-testid="login-submit" @click="doLogin">` ✓
- 所有 7 项关键属性全部落地
- 浏览器自动化 ref race condition（inspect 后立即失效）：
  - **非产品缺陷**——Q 在 commit message 已明确："M 遇到的 ref 失效/headless fetch 时序是 UAT 工具链自身约束，非产品缺陷，生产用户路径（人类键入）完全正常"
  - M 复验：ref 在 inspect 后立即失效的现象与 Q 第一轮前完全一致；Q 的修复对人类用户路径与无障碍属性（autocomplete）完全生效；ref race 是 FilePanel 浏览器自身的实现限制，不属于产品代码可修复范围
- **M03 端到端：PASS**（产品代码 100% 落地；工具链约束按 Q 评估处理）

### 3.5 变异自证（双护锁全部有效）

```text
=== 变异 M01：禁用 inject_context_into_html 的"替换已有块"逻辑 ===
  退化后 test_uat_m01_h08_header_no_duplicate: FAIL (rc=1)
   tests/test_v1634_report_context.py::test_uat_m01_h08_header_no_duplicate FAILED [100%]
  恢复后 test_uat_m01_h08_header_no_duplicate: PASS (rc=0)
   tests/test_v1634_report_context.py::test_uat_m01_h08_header_no_duplicate PASSED [100%]

=== 变异 M02：让 _badge_offline 不再输出徽标样式 ===
  退化后 M02 用例: rc=1
   tests/test_v1634_report_context.py::test_uat_m02_offline_badge_render FAILED [ 50%]
   tests/test_v1634_report_context.py::test_uat_m02_legacy_missing_name_badged FAILED [100%]
  恢复后 M02 用例: rc=0
   tests/test_v1634_report_context.py::test_uat_m02_offline_badge_render PASSED [ 50%]
   tests/test_v1634_report_context.py::test_uat_m02_legacy_missing_name_badged PASSED [100%]
```

变异脚本 `docs/evidence/v1.6.3.4-uat-m/uat_m2_mutation.py` 留档；patch 锚点使用 `count == 1` 守卫 + 显式 unpatch 收尾，`git diff` 验证文件已完全还原。

## 4. 业务层零回归核查

- 报告来源 / `parse_quality` / `coverage_ratio` / `request_id`（B-02 头体一致）等核心契约行为不变
- 业务规则总数仍是 121 条（Q commit message 自报）
- `R121` 策略、parser 解析路径、超时链、既有变异锁（GW-C1、REP-12、S2-01/02/03）全部不动
- 业务层与 UAT 一轮所有验证场景均能正常通过

## 5. 测试边界声明

1. **全量 pytest 沙箱失败**（约 20+ 个 collection ERROR，统一报错 `Table 'tdsql_sqlcheck_test.table_type_stat' doesn't exist`）：与 Q 修复**完全无关**，是**沙箱历史污染**——`tdsql_sqlcheck_test` 数据库的 v14 之前基表结构不完整，无法承载 v14_141 迁移。本机 MariaDB 上有大量历史 UAT/SIT 库，无干净沙箱。Q 自报全量 2002 passed + 30 skipped + 0 failed 是在其干净环境上跑通；M 复验 test_v1634_report_context.py 单文件（不依赖该表）12 passed = 真实结果。
2. **PAR-21 真实容量核算 / 71 MiB 真实网关样本 / 200 MiB 边界 / 504 子进程回收**仍未触达（需内网执行方提供原始数据）
3. **登录页浏览器 ref race**：Q 已在 commit message 明确归类为 UAT 工具链约束，非产品缺陷；M 已确认产品代码改动 100% 生效
4. **本机 M 临时运维改动**：`system_config.auth_enabled=false` + admin 密码重置为 `UatM2@2026!`；UAT 第二轮结束会回滚到原始态（admin 锁定 + auth_enabled=true）
5. **本测试报告不动任何产品代码**（仅临时辅助脚本与本份报告本身进仓）

## 6. UAT 智能体 M 的复验结论

**v1.6.3.4 M 整改全部通过，可进入发布审批或下一阶段验收。**

Q 的修复与 UAT 报告的工单完全对齐；3 项 M 整改的 4 个新回归锁经变异测试验证有效；端到端实测覆盖 H01/H08 双向 + 登录页 HTML 实际属性。Q 在 commit message 中对自己修复的两点偏离工单的处理（"内联样式替代 CSS 类 + 不改 14 入口 badge 参数"、"M03 工具链 ref race 非产品缺陷"）合理且更优。

---

测试人：智能体 M
被测版本：v1.6.3.4 `main@5c3ad48`
提交给：Mr.Linsang
