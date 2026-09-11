# 智能体D / v1.6.3.6 UAT 证据索引

被测 `main@5521267`（v1.6.3.6 内网大库问题修复）。主报告：
`docs/UAT-v1.6.3.6-内网大库问题修复-用户验收测试报告-D.md`。

## 来源边界

- 全部 PNG 为 **Playwright 驱动本机 Chrome（headless）** 的真实截图（真实输入/点击/下载事件）。
- **本机无 TDSQL Proxy**：物理子表不可读用 `_show_create` 边界的**等价异常注入**模拟，
  错误码/文案取自内网实测（660 / 1146）；注入之外全程真实代码路径。
- 合成 publish 探针会在库内留下 PUBLISHED/PUBLISHING 状态的任务（不会自行 complete），
  故提供 `cleanup_stuck_d36.py` 收尾；该状态本身也是 UAT36-01 的复现素材。
- 不含口令/JWT；账号口令运行期生成于 `data/reports/uat_d_1636/admin.password`（`data/` 已 gitignore）。

## 一键复现

```powershell
$PY='C:\Users\linsa\AppData\Local\Python\pythoncore-3.14-64\python.exe'
$env:PYTHONIOENCODING='utf-8'
& $PY docs/evidence/v1.6.3.6-uat-d/local_harness_d36.py setup
& $PY docs/evidence/v1.6.3.6-uat-d/local_harness_d36.py web      # 8025
& $PY docs/evidence/v1.6.3.6-uat-d/local_harness_d36.py runner   # 独立终端
& $PY docs/evidence/v1.6.3.6-uat-d/local_harness_d36.py proxy    # 8026
```

## 复测命令

```powershell
# BUG-01 五场景矩阵 / 端到端（含跳过）
& $PY docs/evidence/v1.6.3.6-uat-d/uat36_scenarios_d.py bug01_matrix
& $PY docs/evidence/v1.6.3.6-uat-d/uat36_scenarios_d.py bug01_e2e
# BUG-02 区分带 / 压缩 / 包限调小（包限场景须先调 GLOBAL 并新开进程）
& $PY docs/evidence/v1.6.3.6-uat-d/uat36_scenarios_d.py bug02_band
& $PY docs/evidence/v1.6.3.6-uat-d/uat36_scenarios_d.py bug02_compact
& $PY docs/evidence/v1.6.3.6-uat-d/uat36_scenarios_d.py bug02_packet
# 真实浏览器呈现面（参数：跳过任务id 跳过报告id 节选报告id）
& $PY docs/evidence/v1.6.3.6-uat-d/browser_uat36_d.py b1_normal_flow
& $PY docs/evidence/v1.6.3.6-uat-d/browser_uat36_d.py b5_sql_download <job> <report_skip> <report_omitted>
# 独立变异抽查 / UAT36-01 受控复现
& $PY docs/evidence/v1.6.3.6-uat-d/probe_mutation_1636_d.py
& $PY docs/evidence/v1.6.3.6-uat-d/probe_published_orphan_d36.py
# 收尾：清理悬挂任务与槽
& $PY docs/evidence/v1.6.3.6-uat-d/cleanup_stuck_d36.py
```

## 全量回归

```powershell
$PY='C:\Python314\python.exe'
$env:PYTHONPATH='C:\Users\linsa\AppData\Roaming\Python\Python314\site-packages'
$env:SQLCHECK_DB_NAME='uat_d_1636_regression'; $env:ADMIN_INITIAL_PASSWORD='Abcd1234'
$env:TDSQL_TEST_ADMIN_PASSWORD='Abcd1234'
$env:G14_ALLOW_DESTRUCTIVE_TESTS='1'; $env:G14_TEST_DB_NAME='uat_d_1636_regression'
& $PY docs/evidence/v1.6.3.6-uat-d/prepare_regression_d36.py
& $PY -m pytest tests -q --tb=line --junitxml=data/reports/uat_d_1636/full-regression.xml
```

> 仅使用 `uat_d_1636_*` 专用库与本机 loopback；未改动产品代码、未触碰 v1.6.3.5 夹具与生产。

## 第二轮复测（FIXREQ-v1.6.3.6-01，被测 `7d515e5`）

```powershell
# 三种"悬挂残留"形态的界面表现（published / accepted / running_stale）
& $PY docs/evidence/v1.6.3.6-uat-d/probe_published_orphan_d36.py published
& $PY docs/evidence/v1.6.3.6-uat-d/probe_published_orphan_d36.py accepted
& $PY docs/evidence/v1.6.3.6-uat-d/probe_published_orphan_d36.py running_stale   # 只回拨心跳，勿动 created_at
# 回收行为矩阵（L1/L2/L3/L4/L5/L10）
& $PY docs/evidence/v1.6.3.6-uat-d/probe_reclaim_matrix_d36.py
# 锁有效性独立变异（含组合变异 M7/M8：整层移除才代表真实缺陷）
$env:SQLCHECK_DB_NAME='uat_d_1636_meta'
& $PY docs/evidence/v1.6.3.6-uat-d/probe_mutation_fixreq01_d.py
```

判读要点：**单个变异不变红 ≠ 假绿**——若另一层防护仍能维持行为不变（如 `complete()` 检查与"放槽前终态护栏"、
回收分支条件与 SQL 内层 `state` 守卫），属等价变异；须施加**组合变异**再判。
