# v1.6.4.0 AI Copilot 专家助手 · 第五轮用户验收测试报告（复测）

| 项 | 内容 |
|---|---|
| 版本 | v1.6.4.0 / CP-1 |
| 被测提交 | `4b8eaf1`（Q 的"UAT第四轮自检链路闭环"） |
| 上轮基线 | `052cc39`（第四轮判定 NO-GO，剩 1 项阻断） |
| 测试性质 | 第五轮 UAT：复测第四轮 4 项整改 + 防回退 + 准入条件核验 |
| 测试方 / 署名 | **智能体D** |
| 测试日期 | 2026-09-16 |
| 证据目录 | `docs/evidence/v1.6.4.0-uat5-d/`（脚本 2 份、JSON 证据 2 份） |
| 提交对象 | Mr.Linsang |

---

## 0. 结论

**唯一阻断已解除。功能验收通过。**

| 维度 | 结果 |
|---|---|
| **F-1（阻断）自检链路** | ✅ **已修复，产品代码自身跑通**（未使用我的补丁） |
| F-2（建议）失败归因 | ⚠️ **半成品**：实测仍误报"所选资料不可用" |
| F-3（建议）召回并联 | ⚠️ **实现不完整**：新逻辑被上游过滤架空，召回仍 1.61（预期 2.39） |
| F-4（准入条件）回归锁 | ❌ **未满足**（连续第四轮零新增用例） |
| M01 防回退 | ✅ 出站仍逐字节等于冻结投影 |
| 全量回归 | ✅ `tests/copilot` **114 passed**、主产品/RBAC/出站安全全绿 |

**放行判定**：按我在第四轮明示的四条验收口径，**第 2 条（回归锁）未满足**，
因此我不能单方面宣布"可放行"。**功能侧已无阻断项**——
是否在缺少回归锁的情况下放行，是需要 Mr.Linsang 决策的事项（见 §4）。

---

## 1. 核心结论：自检链路终于闭环

四轮、四个根因，本轮**第一次由产品代码自己走通**（全程零补丁）：

| 步骤 | 结果 |
|---|---|
| `POST /providers` | 201 ✓ |
| `PUT /enabled`（未自检） | **422** `PROVIDER_CONFIG_INVALID` ✓（这道校验保留正确） |
| `POST /self-tests` | 202 ACCEPTED ✓ |
| 自检轮终态 | **`SUCCEEDED`** ✓ |
| 出站次数 | **1** ✓ |
| `GET /providers` | **`tested_revision = 1`（= revision）** ✓ |
| `PUT /enabled`（自检后） | **200 / enabled=true / revision=2** ✓ |

**"全新部署无法启用任何模型"这条阻断彻底解除**，CP-F11 的功能要求达成。

Q 的四项改动与工单逐条对应：
- F-1a 封套 AAD 改用真实 `preview_id`（`selftest.py`）✓
- F-1b 自检轮放行 `enabled=0`（`workflow.py`，且**采纳了更正后的 `TurnKind` 导入**）✓
- F-2 `_collect()` 补 `PROVIDER_SELFTEST` 分支 ✓（但只做了一半，见 §2.1）
- F-3 召回两道过滤改并联 ✓（但被上游架空，见 §2.2）

---

## 2. 两项遗留（均为**建议级**，不阻断）

### 2.1 F-2 只做了一半：`_collect` 修了，`_publish_local` 没修

**工单原文要求两件事**：① `_collect()` 给自检场景显式分支；② **让 `_publish_local` 对自检场景
报自检自身的原因码**，不要复用 `EVIDENCE_UNAVAILABLE`。Q 只做了 ①。

**实测（把两个网关端点都置 503，模拟模型不可达）**：

| 检查 | 实测 |
|---|---|
| 自检轮终态 | `FAILED` |
| `error_code` | **`EVIDENCE_UNAVAILABLE`** ❌（期望 `PROVIDER_UNAVAILABLE` 一类模型侧原因码） |
| 是否模型相关 | **false** ❌ |

用户/运维看到的会是「**所选资料不可用，请回到原页面确认后重试**」——
而真实原因是**模型网关不通**。这正是 F-2 要防的"错误归因"。

**证据**：`r5_core.json` → `f2`。

**解决方案**：`_publish_local` 里对自检场景单独归因（约 5 行）：

```python
def _publish_local(self, evidence, knowledge, has_evidence, has_knowledge) -> None:
    # §12.6：自检不带业务资料，因此"无证据"是设计使然，不能用 EVIDENCE_UNAVAILABLE 归因。
    if self.turn.get("scene") == "PROVIDER_SELFTEST":
        self._publish("FAILED", None,
                      self.last_model_error or "PROVIDER_UNAVAILABLE",
                      "自检未能完成：模型不可达或输出不合规")
        return
    ...（原有逻辑）
```

> 注意：此时已把"是否为模型侧原因"记录在 `self.last_model_error`，直接透出即可，
> 但**不要**把供应商原始错误体带进来（§12.7 禁止透传）。

### 2.2 F-3 并联逻辑被上游过滤架空，召回没有恢复

Q 在 `scored.sort()` 之后按处方加了"并联"块，**但采集循环里的旧过滤没有移除**：

```python
# knowledge.py:130  ← 仍在，先把 <9.0 的全部丢掉
if score >= MIN_SCORE:
    scored.append((score, idx))
...
# knowledge.py:135-141 ← 新增的并联块，作用在"已经被削到 ≥9.0"的列表上
if scored:
    if scored[0][0] < MIN_SCORE:
        scored = []
    else:
        _floor = max(MIN_SCORE_FLOOR, scored[0][0] * MIN_SCORE_RATIO)
        scored = [(s, i) for s, i in scored if s >= _floor]
```

**后果**：并联块里的 `_floor` 分支只能**进一步**削减，永远无法**恢复**任何被上游丢掉的条目，
新逻辑实际是死代码。实测召回与改前完全一致。

**实测对比**（18 题黄金集）：

| 版本 | 平均召回 | 零召回 | 越界拒绝 |
|---|---|---|---|
| 改前（串联） | 1.61 | 0 | 6/6 |
| **本轮（`4b8eaf1`）** | **1.61（无变化）** | 0 | 6/6 |
| 处方预期（真并联） | **2.39** | 0 | 6/6 |

**证据**：`r5_core.json` → `f3`（`recall_avg: 1.61`）。

**解决方案**：把采集循环改回"全收"，两道过滤只在排序后做一次（约 2 行）：

```diff
-            if score >= MIN_SCORE:
+            if score > 0:                      # 先全收，过滤统一放到排序后
                 scored.append((score, idx))
```

这样 `knowledge.py:135-141` 的并联块才真正生效：绝对下限决定"是否收录"，
相对比例决定"保留几条"。

> 安全指标不受影响：越界问题 top1 < 9.0 → 仍被判"未收录"，实测 6/6 拒绝。

---

## 3. 防回退结果（本轮全绿）

| 项 | 结果 |
|---|---|
| `tests/copilot/` 独立复跑 | **114 passed** ✅ |
| 主产品：即时审核 | `200` / `passed=true` / 0 违规 ✅ |
| 主产品：在线元数据审核任务 | `202` → `SUCCEEDED`，**4.1 s** ✅ |
| RBAC 四角色 | admin 200/200、第二 admin 200/200、developer 200/**403**、auditor **403/403** ✅ |
| 出站安全（累计 34 次真实出站） | 含 `tools`/`functions` **0** 次；全部 `Bearer` ✅ |
| 越权访问他人会话 | `404` ✅ |
| 重名 provider（第三轮修复） | `422 INVALID_REQUEST` ✅ 无回退 |
| **M01 出站投影** | `RULE_EXPLAIN`、`SQL_ADVISE` 出站**逐字节等于**冻结投影，两场景 `SUCCEEDED` + `answer_source=MODEL` ✅ |
| 管理端接口 | `health` / `feedback-summary` / `copilot-audit/events` 均 200 ✅ |

---

## 4. 准入条件 F-4：未满足（需 Mr.Linsang 决策）

| 检查 | 结果 |
|---|---|
| `4b8eaf1` 是否触碰 `tests/` | **否**（零文件） |
| `pytest tests/copilot --collect-only` | **114**（与第一轮修复前完全一致） |
| 是否提供"锁在未修复代码上失败"的红→绿证据 | **未提供** |

**连续第四轮未满足。**我在第四轮明示了四条验收口径，第 2 条即"逐条核验三条锁是否落地"。
按该口径，本轮**不能宣布"可放行"**。

但需要把话说清楚：**F-4 是流程/质量门，不是功能缺陷。**
- 从**功能**看：唯一阻断已解除，全部回归绿，核心链路稳定，出站安全无回退；
- 从**风险**看：自检链路四轮四个根因，全部是"跑一次就会暴露"的类型。
  缺锁意味着**下一次重构仍可能无声改坏它**，而不会再有人替它跑四轮。

**两个选项**（已提交 Mr.Linsang 裁定）：

| 选项 | 含义 | 影响 |
|---|---|---|
| A. 有条件放行 | 认可功能验收通过，三条回归锁移到发布门禁阶段补齐 | v1.6.4.0 可进发布门禁；风险是锁补齐前若再动 `selftest.py`/`workflow.py`，无护栏 |
| **B. 维持准入条件** | Q 补三条锁（含"红→绿"证据）后，本轮重新判定放行 | 多一轮往返，换来对同类问题的长期护栏 |

### 4.1 裁定结果：**B（维持准入条件）**

> **Mr.Linsang 于 2026-09-16 裁定：维持准入条件。**
> 即：**Q 补齐三条回归锁并提供"锁在未修复代码上失败"的红→绿证据后**，
> 本报告才转为"放行"结论。在此之前，v1.6.4.0 的 UAT 状态为
> **功能验收通过、准入条件未满足**。

### 4.2 为便于施工：参考锁已写好并**已亲自验证红→绿**

为让 Q 的工作变成机械动作，我按工单把三条锁写成**可直接落地的参考实现**并实测通过：

参考实现：`docs/evidence/v1.6.4.0-uat5-d/proposed_test_provider_selftest_e2e.py`
（建议原样收录为 `tests/copilot/test_provider_selftest_e2e.py`）

| 锁 | 覆盖的历史根因 | 断言要点 |
|---|---|---|
| `test_selftest_admits_and_writes_tested_revision` | 第二轮缺必填字段 → 500；第三轮 epoch 写死 → CONTEXT_CHANGED；第四轮 AAD/enabled | 受理自检 → 模拟 runner 领取（CAS ACCEPTED→RUNNING + fencing token）→ **离线打桩模型、真跑 `TurnExecutor.run()`** → 断言 `SUCCEEDED` **且 `tested_revision == revision`** → 断言能启用 |
| `test_selftest_envelopes_decrypt_with_real_preview_id` | 第四轮 R4-B01 AAD 字面量 | 用 preview **真实主键** 作 AAD 解密两个封套，必须成功 |
| `test_selftest_turn_bypasses_enabled_only_for_selftest` | 第四轮 R4-B02 enabled 死结 | 自检轮 `_load_provider_frozen` 必须返回 provider；`USER_QUESTION` 轮必须返回 `None` |

**红→绿实测（原始输出已归档）**：

| 步骤 | 命令 | 结果 |
|---|---|---|
| **红** | 临时把两个根因改回去（AAD 用字面量 + 去掉自检放行）→ 跑同一组锁 | **3 failed** — 全部三条锁报红，错误信息分别为<br>`state=FAILED error=INTERNAL_ERROR` + `InvalidTag` / `封套解密失败`、<br>解密断言失败、`自检轮必须能对未启用的 provider 发起验证调用` |
| **绿** | `git checkout` 还原产品代码 → 跑同一组锁 | **3 passed** |

- 复现脚本：`docs/evidence/v1.6.4.0-uat5-d/_reintroduce_bugs.py`（用完即还原）
- 原始输出：`lock-red.txt`（29.8 KB）、`lock-green.txt`（2.3 KB）

> **给 Q 的最小动作**：`cp` 参考实现到 `tests/copilot/` → 跑一次（绿）→
> 用 `_reintroduce_bugs.py` 制造红 → 再跑一次（红）→ `git checkout` 还原 →
> 把两次输出贴进开发记录。**不要重写这三条锁。**

**产品代码保持零改动**：验证用的临时测试文件已删除，`tests/copilot` 用例数仍为 **114**，
`git status` 对 `backend/`、`tests/` 无任何改动。

---

## 5. 出口判定

| 项 | 判定 |
|---|---|
| **功能验收** | ✅ **通过**——唯一阻断已解除，全部回归绿，出站投影四轮未失守 |
| **准入条件（F-4）** | ❌ **未满足**——Mr.Linsang 裁定维持该条件 |
| **v1.6.4.0 UAT 总判定** | **功能通过 / 放行待锁**：Q 收录三条回归锁并提交红→绿证据后，本报告即转"放行" |

遗留两项**建议级**（F-2 失败归因半完成、F-3 并联被上游架空）不阻断放行，
建议与三条锁一并处理；若时间紧，可并入发布门禁。

---

## 6. 本轮未做 / 边界声明

1. **真实内网模型网关**：仍用同协议受控网关（TLS 校验真实开启、Bearer 真实发送、
   报文逐字节落盘），替换的只有"供应商那一段"；真实网关认证/留存政策仍需 CP-GATE-DATA。
2. **systemd / Linux 应急路径**、**200 轮压力与长稳**、**真实模型黄金集答案质量**、
   **五条发布链路**：边界同前四轮，未执行。
3. **`feedback-summary` 的 ≥3 subject 展示分支**：本机只有 4 个账号，仍凑不齐 3 个
   有终态轮次的不同 subject，该分支未在运行期验证（实现侧已按处方落地）。

---

## 7. 证据索引

目录：`docs/evidence/v1.6.4.0-uat5-d/`

| 主题 | 文件 |
|---|---|
| **自检链路复测（主事件）** | `r4_selftest_verify.py`（复用第四轮脚本）、`r5-d/r4_selftest_after_fix.json` |
| **F-3 召回 + M01 防回退 + F-2 归因** | `r5_core.py`、`r5_core.json` |
| **防回退回归** | `r5_regression.py`、`r5_regression.json` |
| **参考锁（已验红→绿）** | `proposed_test_provider_selftest_e2e.py`、`lock-red.txt`、`lock-green.txt`、`_reintroduce_bugs.py` |

### 一键复现

```powershell
powershell -ExecutionPolicy Bypass -File docs/evidence/v1.6.4.0-uat-d/start_all.ps1
python docs/evidence/v1.6.4.0-uat4-d/r4_selftest_verify.py    # 期望 SUCCEEDED / tested_revision=1 / enable 200
python docs/evidence/v1.6.4.0-uat5-d/r5_core.py               # F-3 召回(1.61) / M01 防回退(绿) / F-2 归因(偏)
python docs/evidence/v1.6.4.0-uat5-d/r5_regression.py         # 主产品 + RBAC + 出站安全
powershell -ExecutionPolicy Bypass -File docs/evidence/v1.6.4.0-uat-d/stop_all.ps1
python docs/evidence/v1.6.4.0-uat-d/prepare_uat_d40.py untrust
```

---

## 8. 给 Mr.Linsang 的一句话

**四轮之后，那条自检链路终于通了**——而且是产品代码自己跑通的（我没打补丁）：
自检 `SUCCEEDED` → `tested_revision` 写入 → 启用 **200**；全部回归绿、出站投影四轮未失守。
按您的裁定（**维持准入条件**），本轮结论为「**功能验收通过 / 放行待锁**」：
三条回归锁我已写好、并**亲自跑出了红→绿**（缺陷回归 3 failed → 代码还原 3 passed），
Q 只需 `cp` 进 `tests/copilot/`、跑两次、把输出贴进开发记录即可。
另建议顺手清掉 F-2/F-3 两个建议级尾巴（各约 2–5 行）。

---

**-智能体D**

*2026-09-16 · TDSQL-SQLCheck v1.6.4.0 AI Copilot 第五轮 UAT（复测）*
*裁定记录：Mr.Linsang 2026-09-16 裁定维持 UAT 准入条件（选项 B）*
