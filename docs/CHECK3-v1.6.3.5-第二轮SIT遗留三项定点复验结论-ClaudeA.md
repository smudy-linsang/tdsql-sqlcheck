# CHECK3-v1.6.3.5 第二轮 SIT 遗留三项定点复验结论

| 项 | 内容 |
|---|---|
| 被验版本 | v1.6.3.5，`main` / `9aed957` |
| 上轮结论 | 不通过：1 BLOCK（R2-01）＋ 1 MAJOR（R2-02）＋ 1 NIT（R2-03） |
| 复验范围 | 仅这三项 ＋ 变异有效性 ＋ 全量回归，不重开完整 SIT |
| 复验方 | 智能体 A |
| 复验日期 | 2026-09-09 |
| **复验结论** | **通过。三项全部关闭，回归相对施工前基线归零。新发现 1 项 MINOR（用例断言强度），不阻断，建议进入 UAT 前顺手补。** |

---

## 1. 三项复验结果

### R2-01 部署接线 —— ✅ 关闭

| 脚本 | 上轮 | 本轮 |
|---|---|---|
| `install.sh` | 8 处 | 7 处 |
| **`upgrade_incremental.sh`** | **0 处** | **9 处** |
| **`apply_patch.sh`** | **0 处** | **8 处** |
| **`rollback.sh`** | **0 处** | **5 处** |
| `verify_deploy.sh` | 3 处 | 4 处 |

**内网实际使用的增量升级路径已接通**——这是上轮定为 BLOCK 的核心（本版修复此前根本到不了生产）。

另外两处偏离也都改了：

* **顺序已改为先 runner 后 Web**：`install.sh` 第 146 行 restart runner、第 158 行 restart Web；`upgrade_incremental.sh` 第 103 行 runner、第 118—122 行 Web。
* **runner 起不来必定失败退出**：两脚本均为 `if ! systemctl is-active --quiet …; then fail "…" ; fi`，而 `fail()` 定义为 `echo …; exit 1`（`install.sh:31`、`upgrade_incremental.sh:24`），确认返回非零。

**一处超出我要求的处理**：`upgrade_incremental.sh` 为非 systemd 环境补了 `nohup` 拉起分支（第 111—115 行），并明确 `warn`。这比单纯"没有 systemd 就跳过"更实用，我认可。

### R2-02 verify_deploy 回归 —— ✅ 关闭

改法正确，且用的是**判定 systemd 是否为 init 的正确方法**（`/run/systemd/system` 目录是否存在），而不是只看 `systemctl` 二进制在不在：

```bash
if [[ -d /run/systemd/system ]]; then
  systemctl is-active --quiet tdsql-metadata-runner && ok "…" || bad "…"
else
  echo "  [SKIP] metadata-runner 服务检查（当前非 systemd 运行环境，跳过）"
fi
```

`[SKIP]` 是纯提示，**不计入 SKIP 计数、不影响退出码、不产生 PASS/FAIL**，注释里也写明了这是为兼容既有"服务不可达零 PASS"契约。

**上轮被打挂的 3 条用例已全部恢复**：`tests/test_verify_deploy_contract.py` → **11 passed**。

### R2-03 TMP_DIR —— ✅ 关闭

采纳"注明复用"方案：设计中已删除 `METADATA_TMP_DIR`（全文 0 命中），产物根目录固定复用 `REPORT_OUTPUT_DIR/metadata-audit`，开发记录中有对应说明。设计与实现现已一致。

---

## 2. 变异测试：新增部署契约用例基本是真锁

| 变异 | 结果 |
|---|---|
| M1 增量升级脚本抽掉 runner | ✅ 红：`test_script_references_runner[upgrade_incremental.sh]`、`test_runner_starts_before_web[upgrade_incremental.sh]` |
| M2 回滚脚本抽掉 runner | ✅ 红：`test_script_references_runner[rollback.sh]` |
| M3 补丁脚本抽掉 runner | ✅ 红：2 条 |
| M4 install.sh 去掉 runner 的 restart | ❌ **全绿** → 见 §3 |
| M5 verify_deploy 恢复无条件 FAIL | ✅ 红：正是上轮被打挂的那 3 条 |

M5 尤其有意义——它证明**上轮那个回归如果再次发生，会立刻被抓住**。

---

## 3. 新发现 R3-01（MINOR，不阻断）顺序断言比它声称保证的性质弱

**问题**

`tests/test_v1635_deploy_contract.py:36-43` 的顺序断言是：

```python
runner_pos = content.find("tdsql-metadata-runner")
web_restart_pos = content.find("systemctl restart tdsql-sqlcheck")
assert runner_pos < web_restart_pos
```

它比较的是 **"tdsql-metadata-runner" 这个字符串第一次出现的位置**——而该字符串最早出现在 unit 文件安装的 `sed` 那一段（`install.sh:141`），**不是 runner 真正被启动的位置**（`:146`）。

**实证**：我把 `systemctl restart tdsql-metadata-runner` 整行替换掉（即 **runner 根本不再被启动**），保留更早的 unit 安装引用——

```text
首次提及位置：runner=141   Web restart=158
结果：13 passed   ← 全绿
```

**也就是说，只要 unit 安装段还在前面，哪怕 runner 压根没启动、或启动被挪到 Web 之后，这条用例也发现不了。**

**影响**

有限但真实。当前脚本本身是**正确的**（我已逐行确认顺序无误），所以这不是功能缺陷；问题是这道锁挡不住将来把启动顺序改坏的改动——而"先 runner 后 Web"正是上轮 BLOCK 的组成部分之一。

**整改（改两行）**

把比较对象换成**真正的启动动作**：

```python
runner_start = content.find("systemctl restart tdsql-metadata-runner")
web_restart  = content.find("systemctl restart tdsql-sqlcheck")
assert runner_start != -1, f"{script} 未启动 metadata-runner"
assert runner_start < web_restart, f"{script} 中 runner 必须先于 Web 启动"
```

（`upgrade_incremental.sh` 的非 systemd 分支用 `nohup` 拉起，断言可用"`systemctl restart tdsql-metadata-runner` 或 `backend.workers.metadata_runner` 二者之一先于 Web restart"来兼容。）

补后请自行做一次变异复验：删掉 runner 的 restart 行，确认该用例会红。

---

## 4. 回归与整体状态

**全量套件相对施工前基线（`1f274cd`）：**

```text
本轮 = 544 行     施工前基线 = 544 行     diff 行数：0
```

上轮那 3 行新增 FAILED 已消失，**且没有引入任何新的失败**。

`tests/test_v1635_*.py` 共 **70 项全部通过**（第一轮 29 项 → 第二轮 57 项 → 本轮 70 项）。

**v1.6.3.5 SIT 阶段汇总**

| 轮次 | 发现 | 状态 |
|---|---|---|
| 第一轮 | 3 BLOCK ＋ 2 MAJOR ＋ 1 MINOR | 全部关闭 |
| 第二轮 | 1 BLOCK ＋ 1 MAJOR ＋ 1 NIT | 全部关闭 |
| 本轮 | 1 MINOR（断言强度） | 建议补，不阻断 |

**建议进入 UAT。**R3-01 与 UAT 并行修即可；补完我只做一次变异复验，不再开新一轮。

---

## 5. 仍未覆盖的部分（三轮一致，不因通过而改变）

1. **内网真实 6000+ 表容量验收（设计 §11.4 要求连续 3 次）仍未执行**——这是本版立项要解决的那个故障本身，**没有它就没有"修好了"的证据**。按设计，D03/容量门禁只能记待验证。
2. **CORE_SAFE 回退制品与 JOB-21 往返**未验证。
3. **RSS 超限的真实触发**未验证——我只验了参数校验与代码路径，没在沙箱制造真实内存越界。
4. 取消/恢复/断电重入、产物损坏分支、浏览器端真实点击（UI-10）、多 worker 并发受理竞争未覆盖。
5. 我在第二轮评审中提的 **GC 停顿观测（A-1）** 未见落地；它是我判断"5 秒强杀"机制是否成立的唯一低成本手段，**建议在内网容量验收时补上**——跑一次就能坐实或证伪。

以上均不因本轮通过而推定通过。

## 6. 复验边界声明

1. 本轮**未修改仓库任何文件**。变异与断言强度验证全部"改—跑—还原"，结束后 `git status` 干净，已核验。
2. 部署脚本的验证为**静态审读 ＋ 契约用例 ＋ 变异**，**未在真实 systemd 环境执行过 install/upgrade/rollback**——沙箱不是 systemd init。真实部署行为仍须由 G 在内网按 §12 验证。
3. §1 的顺序结论来自逐行读脚本与行号比对，不是运行验证。

---

复验人：智能体 A（ClaudeA）
被验版本：v1.6.3.5 `main@9aed957`
提交给：Mr.Linsang
