# v1.6.4.0 AI Copilot 专家助手 · 第六轮复测与 UAT 出口判定

| 项 | 内容 |
|---|---|
| 版本 | v1.6.4.0 / CP-1 |
| 被测提交 | `b0a2194`（Q 的"UAT第五轮整改——F-2 自检归因 + F-3 召回全收 + 三条回归锁"） |
| 上轮基线 | `ae63959`（第五轮：功能通过 / 准入条件未满足） |
| 测试性质 | 第六轮 UAT：复测第五轮整改 + **准入条件 F-4 独立核验** + UAT 出口判定 |
| 裁定依据 | Mr.Linsang 2026-09-16 裁定维持准入条件（选项 B） |
| 测试方 / 署名 | **智能体D** |
| 测试日期 | 2026-09-16 |
| 证据 | 复用 `docs/evidence/v1.6.4.0-uat5-d/`（本轮结论均有可复现命令） |
| 提交对象 | Mr.Linsang |

---

## 0. 判定

# ✅ UAT 通过 —— 准予放行

准入条件（选项 B）已满足：三条回归锁落地，且**经我独立复现"红"**（不是只采信自述）。
三项整改全部验证通过，全部回归绿，出站投影六轮未失守。

| 维度 | 结果 |
|---|---|
| **F-4 准入条件（回归锁）** | ✅ **满足**：3 条锁落地，`tests/copilot` **114 → 117**；我独立回退缺陷后 **3 failed**（红），还原后 **3 passed**（绿） |
| F-2 自检失败归因 | ✅ **已修复**：模型 503 时 `error_code` 由 `EVIDENCE_UNAVAILABLE` → **`PROVIDER_UNAVAILABLE`** |
| F-3 召回并联 | ✅ **已修复**：平均召回 **1.61 → 2.39**，越界拒绝仍 **6/6** |
| F-1 自检链路（历史阻断） | ✅ 保持修复：`SUCCEEDED` + `tested_revision=1` + 启用 **200** |
| M01 出站投影防回退 | ✅ 六轮未失守：出站**逐字节等于**冻结投影 |
| 全量回归 | ✅ `tests/copilot` **117 passed**；主产品/RBAC/出站安全全绿 |

> **放行的边界**：本判定是 **UAT 出口**。真实内网模型网关、systemd/Linux 应急路径、
> 200 轮压力与长稳、真实模型黄金集答案质量、五条发布链路 —— 这些属
> CP-GATE-DATA / CP-GATE-RELEASE / CP-GATE-TEST 的独立证据，**不在 UAT 结论的覆盖范围内**，
> 不能因本报告而视为已通过（详见 §5）。

---

## 1. F-4 准入条件：独立核验（本轮的核心理由）

Mr.Linsang 裁定"维持准入条件"，因此**放行的前提是锁真实有效**。我没有采信自述，
而是自己把缺陷改回去跑了一遍：

### 1.1 交付物核对

| 检查 | 结果 |
|---|---|
| `b0a2194` 是否新增测试 | ✅ `tests/copilot/test_provider_selftest_e2e.py`（225 行） |
| 是否为我给的参考实现 | ✅ 原样收录，**断言未被削弱**（逐行核对：受理 → 模拟 runner 领取 CAS+fencing token → 离线打桩模型 → 真跑 `TurnExecutor.run()` → 断言 `SUCCEEDED` 且 `tested_revision == revision` → 断言可启用） |
| 用例数 | ✅ **114 → 117** |
| 开发记录是否附红→绿 | ✅ 已附 3 failed / 117 passed 两段输出 |

### 1.2 我自己的独立红→绿（关键证据）

```text
① 全量（当前代码）        → 117 passed
② 我临时把两个根因改回去  → 3 failed
   · test_selftest_admits_and_writes_tested_revision
       E  AssertionError: 自检未成功：state=FAILED error=INTERNAL_ERROR
   · test_selftest_envelopes_decrypt_with_real_preview_id
   · test_selftest_turn_bypasses_enabled_only_for_selftest
③ git checkout 还原       → 117 passed（tests/copilot 无 diff）
```

**结论**：三条锁**真实有效、非同义反复**——把 R4-B01（AAD 字面量）与 R4-B02（enabled 死结）
任一改回去，锁立刻报红。这正是"准入条件"要买到的东西。

> 复现命令：`python docs/evidence/v1.6.4.0-uat5-d/_reintroduce_bugs.py`
> → `pytest tests/copilot/test_provider_selftest_e2e.py` → `git checkout -- backend/services/copilot/{selftest,workflow}.py`

---

## 2. F-2 / F-3 运行期验证

### 2.1 F-2 自检失败归因（此前会把"模型不通"报成"资料不可用"）

方法：把两个网关端点都置 503，触发自检，观察终态错误码。

| 检查 | 第五轮（修复前） | **本轮（修复后）** |
|---|---|---|
| 终态 | `FAILED` | `FAILED` |
| `error_code` | ~~`EVIDENCE_UNAVAILABLE`~~ | **`PROVIDER_UNAVAILABLE`** ✅ |
| 是否模型相关 | false ❌ | **true** ✅ |

用户/运维现在看到的是**真实原因（模型不可达）**，而不是被误导去查资料。

### 2.2 F-3 召回并联（此前并联逻辑被上游过滤架空）

| 检查 | 第五轮 | **本轮** | 处方目标 |
|---|---|---|---|
| 平均召回（18 题） | 1.61 | **2.39** ✅ | 2.39 |
| 零召回题数 | 0 | **0** ✅ | 0 |
| 越界误答 | 6/6 拒绝 | **6/6 拒绝** ✅ | 6/6 |

安全指标与召回同时达标。根因（`knowledge.py:130` 采集循环提前按 `MIN_SCORE` 过滤）
已按处方改为 `if score > 0`，过滤统一在排序后做一次。

---

## 3. 核心链路与防回退

### 3.1 F-1 自检链路（历史唯一阻断，本轮保持）

| 步骤 | 结果 |
|---|---|
| `POST /providers` → 201 | ✓ |
| `PUT /enabled`（未自检）→ **422** | ✓（校验保留） |
| `POST /self-tests` → 202 | ✓ |
| 自检轮终态 | **`SUCCEEDED`**，出站 1 次 |
| `tested_revision` | **= 1（= revision）** |
| `PUT /enabled`（自检后） | **200 / enabled=true** |

### 3.2 防回退（本轮全绿）

| 项 | 结果 |
|---|---|
| `tests/copilot/` 干净树全量 | **117 passed** ✅ |
| 主产品：即时审核 | `200` / `passed=true` / 0 违规 ✅ |
| 主产品：在线元数据审核任务 | `202` → `SUCCEEDED`，**4.1 s** ✅ |
| RBAC 四角色 | admin 200/200、第二 admin 200/200、developer 200/**403**、auditor **403/403** ✅ |
| 出站安全（累计 38 次真实出站） | 含 `tools`/`functions` **0** 次；全部 `Bearer` ✅ |
| 越权访问他人会话 | `404` ✅ |
| 重名 provider | `422 INVALID_REQUEST`（无回退）✅ |
| **M01 出站投影** | `RULE_EXPLAIN`、`SQL_ADVISE` 出站**逐字节等于**冻结投影，两场景 `SUCCEEDED` + `answer_source=MODEL` ✅ |
| 管理端接口 | `health` / `feedback-summary` / `copilot-audit/events` 均 200 ✅ |

---

## 4. 六轮 UAT 全景

| 轮次 | 被测提交 | 判定 | 阻断项 | 遗留 |
|---|---|---|---|---|
| 第一轮 | `52e270f` | ❌ NO-GO | 5 阻断（HTML 闭尾 / ref 未解包 / ProviderRepo / 自检缺失 / AI配置页缺失） | 4 严重 + 6 一般 |
| 第二轮 | `79885fc` | ❌ NO-GO | 3 阻断（自检 500 / 证据链断裂 / runner 无知识） | 6 严重 |
| 第三轮 | `052cc39` | ❌ NO-GO | 1 阻断（自检 CONTEXT_CHANGED） | 2 标定 |
| 第四轮 | `8859f7b`→`052cc39` | ❌ NO-GO | 1 阻断（自检 AAD + enabled 死结） | 2 建议 + 准入条件 |
| 第五轮 | `4b8eaf1` | ⚠️ 功能通过 / 准入未满足 | 无 | 2 建议 + 回归锁 |
| **第六轮** | **`b0a2194`** | ✅ **通过（放行）** | **无** | 见 §5 清理项 |

**累计**：6 轮、27 项缺陷、4 次修复提交。
**最有价值的一条经验**：自检链路连续 3 轮、3 个不同根因，**全部是"跑一次就会暴露"的类型**——
它们最终是被**一条回归锁**终结的，而不是被更多的代码审查。
这条锁在第一轮就给出过骨架；它在第五轮落地后，第六轮的放行才有依据。

---

## 5. 放行边界与遗留事项

### 5.1 本判定**不覆盖**的部分（属其它门禁，需独立证据）

| 门禁 | 内容 | 状态 |
|---|---|---|
| CP-GATE-DATA | 真实内网模型网关的授权/数据域/TLS/供应商留存政策；双管理员名单与职责分离证明 | **未取证**（UAT 全程使用同协议受控网关，替换的只有"供应商那一段"） |
| CP-GATE-RELEASE | 离线安装、同版本/隔离库预演、两组升级/补丁/台账回退/恢复对账、systemd 应急演练、五条发布链路 | **未执行** |
| CP-GATE-TEST 补充项 | 真实模型黄金集答案质量（≥100 题、必需事实正确率 ≥95% 等）；200 轮压力与长稳 | **未执行**（本轮的 18 题只测**检索命中**，不测答案事实正确率） |

> 明确写在这里，是为了避免"UAT 通过"被误读为"可以上生产"。
> 按 DETAIL §16.5，CP-GATE-DESIGN 已完成，**DATA / TEST / RELEASE 仍需各自责任方出具证据**。

### 5.2 遗留清理项（不阻断放行）

| 项 | 说明 | 建议 |
|---|---|---|
| `_verify_a_group.py` / `_verify_b_group.py` | Q 的临时验证脚本（脚本头自注"非交付物"），未跟踪，位于仓库根目录 | 删除，或补 `.gitignore` 规则（现有规则只忽略 `_debug_*.py`） |
| `tests/_tmp_test/` | pytest `--basetemp` 遗留目录（未跟踪、未被忽略），含大量临时密钥/知识包副本 | 删除；建议 `.gitignore` 增加 `tests/_tmp_test/` |
| `superpowers/`（若存在） | 同上，仓库根目录的未跟踪目录 | 一并清理 |

> 这三项会让工作区长期不干净，影响"提交前在干净检出跑回归"的习惯（A 在 GATE §6 强调过）。

### 5.3 低优先建议（可在发布门禁阶段处理）

| 编号 | 内容 | 规模 |
|---|---|---|
| F-5 | `feedback-summary` 的 ≥3 subject 展示分支至今未在运行期验证（本机只有 4 个账号，凑不齐 3 个有终态轮次的不同 subject）；实现侧已按 N-07 落地 | 需 ≥3 个账号构造用例 |
| F-6 | 导出报告已用报告专用 CSP，但 §13.3 要求的"超过 1 MiB 不给截断报告"边界未测 | 构造大结果用例 |

---

## 6. 复现命令

```powershell
# 起夹具
powershell -ExecutionPolicy Bypass -File docs/evidence/v1.6.4.0-uat-d/start_all.ps1
# 自检链路（F-1）
python docs/evidence/v1.6.4.0-uat4-d/r4_selftest_verify.py     # SUCCEEDED / tested_revision=1 / enable 200
# F-3 召回 + M01 防回退 + F-2 归因
python docs/evidence/v1.6.4.0-uat5-d/r5_core.py                # recall 2.39 / matches_frozen true / PROVIDER_UNAVAILABLE
# 防回退回归
python docs/evidence/v1.6.4.0-uat5-d/r5_regression.py
# 准入条件：三条锁的红→绿
python -m pytest tests/copilot -q                              # 117 passed
python docs/evidence/v1.6.4.0-uat5-d/_reintroduce_bugs.py
python -m pytest tests/copilot/test_provider_selftest_e2e.py -q # 3 failed（红）
git checkout -- backend/services/copilot/selftest.py backend/services/copilot/workflow.py
# 收尾
powershell -ExecutionPolicy Bypass -File docs/evidence/v1.6.4.0-uat-d/stop_all.ps1
python docs/evidence/v1.6.4.0-uat-d/prepare_uat_d40.py untrust
```

---

## 7. 给 Mr.Linsang 的一句话

**可以放行了。** 三条回归锁落地后我**自己把缺陷改回去验证了一遍——锁会红**，
这是这次放行唯一新增的依据；F-2、F-3 两个尾巴也确认修好（归因改为 `PROVIDER_UNAVAILABLE`、
召回 1.61 → 2.39 且越界拒绝未退让）。六轮下来，出站投影这条安全线一次都没失守过。

**请把"UAT 通过"与"可以上生产"分开看**：真实内网模型网关、systemd 应急演练、
真实模型黄金集答案质量、五条发布链路——这四类证据属 CP-GATE-DATA / RELEASE / TEST，
**UAT 没有覆盖，也不能代替**。建议下一步按 DETAIL §16.5 推进这三道门禁。

另建议顺手清掉仓库里三处未跟踪残留（Q 的两个 `_verify_*.py`、pytest 的 `tests/_tmp_test/`），
它们会让工作区长期不干净。

---

**-智能体D**

*2026-09-16 · TDSQL-SQLCheck v1.6.4.0 AI Copilot 第六轮 UAT（出口判定）*
*裁定链：Mr.Linsang 2026-09-16 维持准入条件（B）→ 本轮条件满足并判定放行*
