# 智能体D / v1.6.3.5 / UAT 第三轮 证据索引

被测 `main@7cfda09`（v1.6.3.5，Q 的 UAT 第二轮整改提交）；执行日期 2026-09-10。
主报告：`docs/UAT3-v1.6.3.5-在线元数据审核第三轮用户验收报告-D.md`。

本目录是**智能体D 接替 O 完成第三轮 UAT** 的取证材料。O 已完成的第三轮材料在
`docs/evidence/v1.6.3.5-uat3-o/`，作为已确认事实引用，未改写、未重复。

## 来源边界

- 28 张 PNG 均为 **Playwright 驱动本机 Chrome（headless）** 的真实截图：真实登录输入、真实鼠标点击、
  真实下载与刷新；**不是**接口替身，也**不是**人工鼠标操作（差异已在报告 §8 标注）。
- `s*.json` 为场景取证结果（页面文本、接口快照、任务/槽状态、产物哈希）。
- `probe-*.json` 为独立探针结果，其中：
  - `probe-stale-waiting.json` 是**状态级受控复现**（runner 停止期间用 repository 直接受理），
    不是"受理瞬间杀 runner"的竞态复现；
  - `probe-deploy-mutation.json` 的变异发生在内存（替换 `Path.read_text` 返回值），
    **未修改任何部署脚本**；
  - `probe-rbac-matrix.json` / `probe-check-permission.json` 只读角色权限表与判定函数。
- 本目录不含任何口令、JWT 或完整认证日志；账号口令运行期生成于
  `data/reports/uat_d_1635_r3/admin.password`（`data/` 已被 `.gitignore` 排除）。

## 本机重复执行

仅允许本机 `13306` 与本脚本命名的 `uat_d_1635_r3_*` 专用库；不要指向内网或生产。

```powershell
$PY='C:\Users\linsa\AppData\Local\Python\pythoncore-3.14-64\python.exe'
$env:PYTHONIOENCODING='utf-8'
& $PY docs/evidence/v1.6.3.5-uat3-d/local_harness_d.py setup
& $PY docs/evidence/v1.6.3.5-uat3-d/local_harness_d.py web      # 127.0.0.1:8015
& $PY docs/evidence/v1.6.3.5-uat3-d/local_harness_d.py runner   # 独立终端
& $PY docs/evidence/v1.6.3.5-uat3-d/local_harness_d.py proxy    # 127.0.0.1:8016 -> 8015
```

浏览器场景（每个各起一个 headless Chrome 上下文）：

```powershell
& $PY docs/evidence/v1.6.3.5-uat3-d/browser_uat_d.py s1_baseline      # 基线/计数/耗时/分页
& $PY docs/evidence/v1.6.3.5-uat3-d/browser_uat_d.py s3_download      # 下载与 manifest 哈希
& $PY docs/evidence/v1.6.3.5-uat3-d/browser_uat_d.py s5_rescan        # 63->64 重扫新鲜度
& $PY docs/evidence/v1.6.3.5-uat3-d/browser_uat_d.py s6_refresh       # 刷新恢复（先 delay-child 25）
& $PY docs/evidence/v1.6.3.5-uat3-d/browser_uat_d.py s9_cancel        # 取消（先 delay-child 25）
& $PY docs/evidence/v1.6.3.5-uat3-d/browser_uat_d.py s15_double_click # 三连击单任务
```

故障注入（仅测试用途，代理侧丢弃受理响应 / runner 侧 child 启动延迟）：

```powershell
& $PY docs/evidence/v1.6.3.5-uat3-d/local_harness_d.py drop-post once
& $PY docs/evidence/v1.6.3.5-uat3-d/local_harness_d.py delay-child 25
```

探针：

```powershell
& $PY docs/evidence/v1.6.3.5-uat3-d/probe_contracts_d.py deploy_mutation
& $PY docs/evidence/v1.6.3.5-uat3-d/probe_contracts_d.py cancel_elapsed
& $PY docs/evidence/v1.6.3.5-uat3-d/probe_stale_waiting_d.py     # 需先停 runner
& $PY docs/evidence/v1.6.3.5-uat3-d/probe_ownership2_d.py
```

全量回归（本机既有解释器 + 用户站点包，与 O 同环境；必须跑在回归库上，G14 破坏性用例会校验库名）：

```powershell
$PY='C:\Python314\python.exe'
$env:PYTHONPATH='C:\Users\linsa\AppData\Roaming\Python\Python314\site-packages'
$env:SQLCHECK_DB_NAME='uat_d_1635_r3_regression'
$env:ADMIN_INITIAL_PASSWORD='Abcd1234'
$env:TDSQL_TEST_ADMIN_PASSWORD='Abcd1234'
$env:G14_ALLOW_DESTRUCTIVE_TESTS='1'
$env:G14_TEST_DB_NAME='uat_d_1635_r3_regression'
& $PY docs/evidence/v1.6.3.5-uat3-d/prepare_regression_d.py
& $PY -m pytest tests -q --tb=line --junitxml=data/reports/uat_d_1635_r3/full-regression.xml
```

> 不使用生产库、不重置任何既有账号；破坏性测试白名单只指向本人回归库。

## 第三轮整改复测专用（Q 提交 2ab7bf3 之后新增）

**D-02 生产接线验证**（Q 的用例只直接调方法，未锁调用点，故单独取证两条接线）：

```powershell
# W1 受理路径自愈：造 60 秒前 ACCEPTED 幽灵任务 → 新受理必须成功且旧任务判 START_TIMEOUT
& $PY docs/evidence/v1.6.3.5-uat3-d/probe_stale_reclaim_d.py w1
# W2 runner 回收：需先停 runner → prep → 启动 runner → 8 秒内 check 应显示已回收
& $PY docs/evidence/v1.6.3.5-uat3-d/probe_stale_reclaim_d.py prep
& $PY docs/evidence/v1.6.3.5-uat3-d/local_harness_d.py runner     # 另开终端
& $PY docs/evidence/v1.6.3.5-uat3-d/probe_stale_reclaim_d.py check
```

**回归锁变异有效性验证**（对源码施加回退式变异 → 跑 Q 的用例 → 看是否变红 → 自动恢复）：

```powershell
$env:SQLCHECK_DB_NAME='uat_d_1635_r3_meta'
& $PY docs/evidence/v1.6.3.5-uat3-d/probe_mutation_q_d.py      # 结果见 probe-mutation-q.json
```

> 该脚本以**字节级**读写源码并在 `finally` 中恢复原始字节；运行前工作区应干净，
> 运行后 `git status` 应无产品文件改动（首轮曾因文本模式写入把两个前端文件改成 CRLF，已修正并恢复）。

**D-01/D-03 浏览器复测**：

```powershell
& $PY docs/evidence/v1.6.3.5-uat3-d/local_harness_d.py delay-child 25
& $PY docs/evidence/v1.6.3.5-uat3-d/browser_uat_d.py s9_cancel           # 取消写 finished_at
& $PY docs/evidence/v1.6.3.5-uat3-d/browser_uat_d.py s16_recover_action  # D-03 恢复入口
& $PY docs/evidence/v1.6.3.5-uat3-d/browser_uat_d.py s17_phantom_accepted # D-04 幽灵任务观察
```

**探针残留清理**（构造 D-02/D-04 场景会在专用库留下非终态记录）：

```powershell
& $PY docs/evidence/v1.6.3.5-uat3-d/cleanup_probe_residue_d.py
```
