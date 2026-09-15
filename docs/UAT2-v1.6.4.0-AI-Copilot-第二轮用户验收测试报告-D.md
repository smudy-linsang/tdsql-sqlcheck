# v1.6.4.0 AI Copilot 专家助手 · 第二轮用户验收测试报告

| 项 | 内容 |
|---|---|
| 版本 | v1.6.4.0 / CP-1 |
| 被测提交 | `79885fc`（Q 的"UAT第一轮16项缺陷全修"） |
| 上一轮基线 | `52e270f`（第一轮 UAT 判定 NO-GO） |
| 测试性质 | 第二轮 UAT：**逐项复验第一轮 16 项修复** + 防回退 + 新缺陷发现 |
| 测试方 / 署名 | **智能体D**（独立于设计方 O、评审/SIT 方 A、开发方 Q） |
| 测试日期 | 2026-09-15 |
| 上游材料 | `UAT-v1.6.4.0-AI-Copilot-第一轮用户验收测试报告-D.md`、`DEV-…-开发记录-Q.md` §10 |
| 证据目录 | `docs/evidence/v1.6.4.0-uat2-d/`（脚本 14 份、JSON 证据 13 份、真实截图 5 张） |
| 提交对象 | Mr.Linsang |

---

## 0. 结论

**第二轮 UAT 判定：仍不通过（NO-GO）。**

一句话：**第一轮的"页面打不开"确实修好了——用户现在能看见 Copilot；但"问到答案"这条路
不但没通，反而比第一轮更差：所有业务轮次 100% 失败。**

| 维度 | 结果 |
|---|---|
| 第一轮 16 项修复 | **真修复并验证通过 7 项**；**未修复 1 项**；**修了但无效/半成品 8 项** |
| 浏览器可达性 | ✅ **已修复**（未登录无遮罩、抽屉/会话页/AI配置页全部可达） |
| 模型路径端到端 | ❌ **全灭**：所有业务轮次 `FAILED / EVIDENCE_UNAVAILABLE` |
| 管理员能否启用模型 | ❌ **仍不可能**（自检端点从 404 变成 **500 崩溃**） |
| 出站投影一致性（第一轮 M01） | ✅ **已修复且逐字节验证通过**（出站报文 == 冻结投影） |
| 业务解读入口（第一轮 M02） | ❌ **未修复**（`openCopilotWith` 零调用、浏览器 7 个业务页 0 个入口） |
| 回归 | ✅ `tests/copilot` **114 passed**、主产品审核/元数据审核/RBAC 全绿 |
| 回归锁 | ❌ **零新增**（修复提交未触碰任何测试文件） |

> 与第一轮相比，本轮的技术含量更高：第一轮是"功能没做通"，本轮是**"修补丁时打断了另一条链路"**。
> 我把两处新阻断的因果链完整取证到了字节级，并给出了施工方案。

---

## 1. 逐项复验总表（第一轮 16 项）

| 编号 | 第一轮问题 | Q 的修复 | 本轮实测判定 |
|---|---|---|---|
| **D40-B01** | `<el-dialog>` 未闭合吞掉 Copilot 前端 | 补 `</el-dialog>` + 删尾部多余闭合 | ✅ **已修复** |
| **D40-B02** | 普通对象包 ref 未解包 → 遮罩锁登录 | `reactive(createCopilotState(...))` | ✅ **已修复** |
| **D40-B03** | `ProviderRepo` 未导入 → 模型路径崩溃 | 补 import | ✅ **已修复** |
| **D40-B04** | 自检端点缺失 → 启用死锁 | 新增 `selftest.py` + 端点 | ❌ **未闭合：端点 500（见 R2-B03）** |
| **D40-B05** | 「AI配置」菜单无页面 | 补页面 + `copilot_admin.js` | ✅ **已修复** |
| **D40-M01** | 出站=原始 payload、投影三闸被旁路 | `_payload()` 改读投影 | ⚠️ **投影修好（逐字节验证通过）**，但**打断证据链**（见 R2-B01/B02） |
| **D40-M02** | ContextBridge 空壳、业务入口 0 个 | 实现 bridge + `openCopilotWith` 暴露 | ❌ **未修复：入口 0 个** |
| **D40-M03** | 会话页不加载能力/实例 | `initPage()` + `onMenuSelect` 调用 | ✅ **已修复** |
| **D40-M04** | `feedback-summary` 未实现 | 新增端点 | ⚠️ **可用但口径偏差**（见 R2-M06） |
| **D40-M05** | 应急脚本静默失败却报成功 | 探测→执行→失败即 exit 3 | ✅ **已修复** |
| **D40-N01** | 知识检索无相关性阈值 | `MIN_SCORE = 0.5` | ❌ **无效**（越界仍返 8 条） |
| **D40-N02** | `copilot-disabled.json` 只写不读 | bootstrap 读取该文件 | ❌ **无效**（`local_ready()` 零调用方） |
| **D40-N03** | editorBridge 直接覆盖草稿 | 加 `readRevision`/`applyDraftIfRevision` | ❌ **伪实现 + 死代码** |
| **D40-N04** | 抽屉内无会话入口 | 补会话下拉 + 新建按钮 | ⚠️ **控件有了但不可用**（字段名错 + 缺参 422） |
| **D40-N05** | 导出沿用全站 CSP | 导出端点单独设 CSP | ⏸ **本轮无法复验**（无可用终态轮次，见 §6） |
| **D40-N06** | 前端不发 `draft.revision` | `buildPreview` 补 revision | ✅ **已修复** |

**统计：真修复 7 / 未修复 2 / 修了但无效或半成品 6 / 无法复验 1。**

---

## 2. 阻断级缺陷（3 项）

### R2-B01 证据链被切断：runner 从"投影"里找 `source_refs`，而投影没有这个键

**现象（真实用户视角）**
所有业务场景（规则解释 / 审核解读 / 任务排障 / 慢SQL / 对比 / 表统计 / 网关 / SQL 建议）
提交后一律 `FAILED`。第一轮至少还能返回一个合成答案，**本轮连答案都没有了**。

**受控实验（本轮最有价值的一组证据）**

对 `RULE_EXPLAIN` 场景，正确带入 1 条规则来源：

| 检查点 | 实测 | 说明 |
|---|---|---|
| 预览 `evidence_cards` | **1** | 预览侧证据收集正常 |
| 冻结投影 `evidence` | **1** | 封存正常 |
| 出站报文 `evidence` | **1** | 发送正常 |
| 出站 == 冻结投影 | **true** | ✅ 第一轮 M01 的修复确实生效 |
| **轮次终态** | **FAILED / EVIDENCE_UNAVAILABLE** | ❌ runner 认为"没有证据" |

**根因**

Q 按我第一轮的处方把 `workflow.py::_payload()` 从"原始 payload"改成了"模型投影"。
但我第一轮的处方**不完整**：我漏掉了 `_collect()` 也在读 `_payload()`：

```python
# backend/services/copilot/workflow.py:129-132
def _collect(self) -> tuple[list[dict], list[dict], dict]:
    payload = self._payload()              # ← 现在是投影
    refs = payload.get("source_refs") or []   # ← 投影里没有 source_refs → 恒 []
    question = payload.get("question") or ""
```

投影的结构是 `{question, history, evidence, knowledge, allowed_rule_ids, output_schema}`，
**没有 `source_refs`**。于是 `refs` 恒为空 → 所有依赖来源的场景**一条证据都收集不到**。

后果链：模型收到证据并（正确地）引用它 → runner 侧 `evidence_ids` 为空 →
`validate_model_answer` 抛 `OUTPUT_INVALID` → 降级到本地模板 →
`has_evidence=False 且 has_knowledge=False` → **`EVIDENCE_UNAVAILABLE`**。

> **这是我在第一轮报告里的疏漏，本轮由 UAT 自己抓出来了。**正确的切分是：
> **出站用投影、取证用原始 payload** —— 两者用途不同，不能合并成一个方法。

**证据**：`r2_evidence_chain.json`（两个场景的完整对照）、`probe_turns.py` 输出
（全部轮次 `error_code=EVIDENCE_UNAVAILABLE`）、`r2_m01_projection.json`（出站==冻结=true）。

**解决方案（照图施工）**

把 `_payload()` 拆成两个职责明确的方法，**出站只走投影，取证只走原始 payload**：

```diff
--- a/backend/services/copilot/workflow.py
+++ b/backend/services/copilot/workflow.py
@@
     def _payload(self) -> dict:
-        """返回本轮封存的模型投影（§12.3：runner 不得再悄悄加入新来源）。
-
-        payload_envelope 仅用于会话内展示/审计追溯，不得作为出站体；
-        出站必须使用 model_projection_envelope —— 它是预览时按三闸脱敏、
-        冻结预算裁剪后封存的那一份，也是用户在预览里看到的那一份。
-        """
+        """出站体：本轮封存的模型投影（§12.3）。
+
+        这是预览时按三闸脱敏、预算裁剪后封存的那一份，也是用户在预览里看到的那一份；
+        runner 不得在此之上再添加来源。
+        """
         from backend.services.copilot import crypto as crypto_mod
         raw = crypto_mod.decrypt(
             self.preview["model_projection_envelope"], "copilot_previews",
             self.preview["id"], "model_projection_envelope",
             owner=self.preview["owner_subject_id"], keyring=self.keyring)
         return json.loads(raw)
+
+    def _request_payload(self) -> dict:
+        """取证体：用户本轮实际提交的原始请求（question / source_refs / draft / page_key）。
+
+        仅用于 runner 侧按 §5.2 工具合同重新取数，**绝不作为出站体**。
+        投影里没有 source_refs，因此取证必须读这一份，否则证据链恒空。
+        """
+        from backend.services.copilot import crypto as crypto_mod
+        raw = crypto_mod.decrypt(
+            self.preview["payload_envelope"], "copilot_previews",
+            self.preview["id"], "payload_envelope",
+            owner=self.preview["owner_subject_id"], keyring=self.keyring)
+        return json.loads(raw)
```

然后把 `_collect()` 的取数源改掉（`question` 仍可用于本地模板）：

```diff
     def _collect(self) -> tuple[list[dict], list[dict], dict]:
         """返回 (evidence, knowledge, projection_info)。"""
-        payload = self._payload()
+        payload = self._request_payload()      # 取证读原始请求
         refs = payload.get("source_refs") or []
```

`_run_inner` 里本地模板用的 `self._payload().get("question")` 改为
`self._request_payload().get("question")`（或用 `_collect` 已取到的值，避免重复解密）。

**防回退锁（本轮必须补，这正是缺锁的代价）**

```python
# tests/copilot/test_evidence_chain.py（示意）
def test_collect_reads_source_refs_from_request_payload(copilot_db, monkeypatch, ...):
    """铁律：取证必须从 payload_envelope 取 source_refs；出站必须用投影。二者不可互换。"""
    preview = make_preview(source_refs=[{"kind": "rule", "rule_ids": ["R001"]}])
    ex = TurnExecutor(turn, preview=preview, ...)
    evidence, knowledge, _ = ex._collect()
    assert evidence, "带 rule 来源时必须收集到证据（回归 D40/R2-B01）"
    sent = ex._payload()
    assert "source_refs" not in sent and "evidence" in sent, "出站必须是投影"
```

---

### R2-B02 runner 进程从不加载知识包 —— runner 侧知识能力恒为 0

**现象**
即使证据链修好，`knowledge` 在 runner 侧同样恒为空。第一轮能"看起来正常"，
只是因为当时的出站 payload 里根本没有知识，模型也就不会引用它——**缺陷被掩盖了**。

**决定性对照实验**（同一问题、同一检索函数，只差"是否调用过 load"）

| 阶段 | `store.bundle` | `bundle.search` | `execute_search_help` | 状态 |
|---|---|---|---|---|
| bootstrap 之前 | `None` | 0 | **0** | `MISSING` |
| bootstrap 之后 | 对象 | **8** | **8** | `READY` |
| 再显式 `store.load()` | 对象 | 8 | 8 | `READY` |

结论：**`execute_search_help` 返回 0 当且仅当该进程从未调用
`copilot_bootstrap_check()` / `store.load()`。**

而 `copilot_bootstrap_check()` 挂在 **Web 的 FastAPI lifespan** 上；
`backend/workers/copilot_runner.py` 全文对 `knowledge` / `store` / `bootstrap`
**零引用** → 跑模型、做取证的 runner 进程里 `store.bundle is None` →
`_t01()` 恒返回空 → `has_knowledge=False`。

**证据**
- `r2_knowledge_step.json`（上表原始数据）；
- 代码事实：`copilot_runner.py` 中 `knowledge|store|bootstrap` 命中数 = 0；
- 旁证：`r2_m01_projection.json` 中预览（Web 进程）有 8 条知识并成功封存/出站，
  而同一轮在 runner 侧却判定"无知识"。

**解决方案（照图施工）**

在 runner 启动流程里显式加载知识包（与 Web 同一份代码路径），并让知识降级
**不阻断**已能工作的其余能力：

```diff
--- a/backend/workers/copilot_runner.py
+++ b/backend/workers/copilot_runner.py
@@
     def _once(self):
         from backend.services.copilot import schema as schema_mod
         from backend.services.copilot.policy import copilot_enabled, policy_available, Limits
         from backend.services.copilot.crypto import crypto_available
         from backend.services.copilot.repository import RuntimeRepo
+        # §6/§5.2：runner 必须自行加载知识包——取证与本地模板都依赖它。
+        # 不加载时 execute_search_help 恒返回空，表现为"知识能力静默为 0"。
+        from backend.services.copilot.knowledge import store as knowledge_store
+        try:
+            _kst = knowledge_store.load()
+            if _kst != "READY":
+                logger.warning("runner 知识包状态 %s（%s）——知识能力降级，"
+                               "其余能力继续", _kst,
+                               knowledge_store.status_info().get("reason_code"))
+        except Exception as e:
+            logger.error("runner 知识包加载异常（仅知识能力降级）: %s", e)
```

**并加一条进程级锁**（否则下次重构又会漏）：

```python
def test_runner_process_loads_knowledge_bundle():
    """runner 启动路径必须加载知识包，否则 execute_search_help 恒空。"""
    src = Path("backend/workers/copilot_runner.py").read_text(encoding="utf-8")
    assert "knowledge_store.load()" in src or "store.load()" in src, \
        "runner 未加载知识包：知识检索会静默返回空（回归 R2-B02）"
```

---

### R2-B03 自检端点从 404 变成 500 —— 启用死锁依旧

**现象（管理员视角）**
按设计流程"建 provider → 连通性自检 → 启用"：第一步成功（201），
**第二步返回 500**，第三步照旧 422。**模型依然打不开。**

**实测**

```
POST /api/v1/copilot-admin/providers            → 201  (revision=1, enabled=false)
PUT  /api/v1/copilot-admin/providers/{id}/enabled → 422 PROVIDER_CONFIG_INVALID（未自检，符合设计）
POST /api/v1/copilot-admin/providers/{id}/self-tests → 500  ❌
PUT  /api/v1/copilot-admin/providers/{id}/enabled → 422（死锁未解）
```

**根因（服务端完整栈）**

```
File "backend/api/copilot_admin.py", line 259, in start_provider_self_test
File "backend/services/copilot/selftest.py", line 86, in admit_self_test
File "backend/services/copilot/repository.py", line 412, in insert
KeyError: 'scope_kind'
```

`selftest.admit_self_test()` 手写的会话/预览字典**缺少仓储层的必填字段**：

| 仓储 | 必填字段 | Q 未提供 | 缺几个 |
|---|---|---|---|
| `SessionRepo.insert` | id, owner_subject_id, owner, **scope_kind**, **instance_type**, **initial_page_key**, **name_source**, **title**, **expires_at** | 后 6 项 | **6** |
| `PreviewRepo.insert` | id, session_id, owner_subject_id, **owner**, scene, **input_hash**, payload_envelope, evidence_envelope, model_projection_envelope, snapshot_hash, permission_version, provider_revisions_json, data_class, **storage_reserved_bytes**, expires_at, projection_mode, **identifier_policy_revision**, module_schema_epoch | 4 项 | **4** |
| `TurnRepo.insert` | 15 项 | 无 | 0 ✅ |

**关键判断**：Q 报告"16 项全修、114/114"，但**这条端点的第一次真实调用就 500** ——
说明自检流程**从未被端到端执行过一次**。这类"写了没跑"的缺陷，只有真机 UAT 能拦下。

**证据**：`r2_b04_selftest.json`（404→500 与 422 的原始响应）、
`data/reports/uat_d_1640/web.err.log`（`KeyError: 'scope_kind'` 完整栈）、
浏览器实证 `r2-b05-ai-config.png`（AI配置页上"连通性自检"按钮可点）。

**解决方案（照图施工）**

**第 1 步：补齐必填字段，并且不要再手写一套 admit。**
§12.6 原文要求"调用同一 admit 函数"。自检应当复用受理事务的骨架
（runtime 锁 → 幂等 → 容量 → preview 消费 → turn 插入），而不是绕过它。

最小改动版（保留现有结构，只补字段；字段值按自检语义给固定常量）：

```diff
--- a/backend/services/copilot/selftest.py
+++ b/backend/services/copilot/selftest.py
@@
     SessionRepo.insert(conn, {
         "id": session_id,
         "owner": identity.username,
         "owner_subject_id": identity.subject_id,
+        "scope_kind": "GLOBAL_HELP",          # §12.6：自检固定走管理员的 GLOBAL_HELP 会话
+        "instance_type": "unknown",
+        "initial_page_key": "copilot-admin",
+        "name_source": "selftest",
+        "title": f"自检 {provider_id[:8]} r{revision}",
+        "expires_at": deadline,
-        "scene": _SELFTEST_SCENE,
         "connection_id": None,
-        "state": "OPEN",
-        "created_at": now,
-        "updated_at": now,
     })
@@
     PreviewRepo.insert(conn, {
         "id": preview_id,
         "session_id": session_id,
         "owner_subject_id": identity.subject_id,
+        "owner": identity.username,
         "scene": _SELFTEST_SCENE,
+        "input_hash": hashlib.sha256(
+            json.dumps(payload, sort_keys=True, ensure_ascii=False)
+            .encode("utf-8")).hexdigest(),
         "payload_envelope": payload_envelope,
         "model_projection_envelope": projection_envelope,
         "evidence_envelope": "[]",
         "snapshot_hash": hashlib.sha256(
             json.dumps(payload, ensure_ascii=False).encode("utf-8")).hexdigest(),
         "projection_mode": "PUBLIC_HELP",
         "data_class": "PUBLIC_HELP",
         "module_schema_epoch": 0,
         "permission_version": "selftest",
+        "storage_reserved_bytes": 0,
+        "identifier_policy_revision": "selftest",
         "provider_revisions_json": json.dumps({...}),
         "expires_at": deadline,
-        "created_at": now,
     })
```

> 注意：`deadline` 必须在 `SessionRepo.insert` 之前算好（会话的 `expires_at` 与预览共用）。
> `created_at/updated_at/state/revision` 由 SQL 里的 `UTC_TIMESTAMP(6)`、`'OPEN'`、`1` 提供，
> 不要重复传（`SessionRepo.insert` 不接受这些键，传了也不会报错但会造成误解）。

**第 2 步：把"自检能跑通"变成端到端锁**（这是本条真正的护栏）：

```python
def test_provider_enable_flow_is_reachable_end_to_end(copilot_db, client, monkeypatch):
    """建 provider → 自检 → 启用，全流程只走公开 API，一步都不许省。"""
    # 1) POST /providers          断言 201 且 enabled=false
    # 2) PUT  /enabled            断言 422（未自检时必须被拒）
    # 3) POST /self-tests         断言 202（回归 R2-B03：这里必须不是 500/404）
    # 4) 驱动 runner（或直接调 complete_self_test）直到终态
    # 5) GET  /providers          断言 tested_revision == revision
    # 6) PUT  /enabled            断言 200 且 enabled=true
```

**第 3 步（建议）自检不得绕过统一控制面。** 设计 §12.6 首句即
"自检不得绕过并发、额度、出站和审计"。当前 `admit_self_test` 跳过了
`COPILOT_MAX_ACTIVE_TURNS` 容量、每日额度预留、限流与 `session.active_turn_id` 维护。
除补字段外，建议由 §12.6 要求的"同一 admit 函数"承担其余部分，并加一条
"自检也受限流与容量约束"的用例。

---

## 3. 严重级缺陷（6 项）

### R2-M01 D40-M02 未修复：业务解读入口仍然是 0 个

Q 在 `app.js` 里实现了 `copilotContextBridge.getCurrentSelection()` 与
`openCopilotWith(selection)`，并"暴露到模板"。但**暴露 ≠ 接线**：

| 检查 | 结果 |
|---|---|
| `openCopilotWith` 在 `index.html` 中出现次数 | **0** |
| 「解读本次结果 / 解释这条规则 / 分析失败原因 / 生成修改建议 / 让 Copilot」出现次数 | **全部 0** |
| 浏览器逐页扫描（即时审核/文件审核/在线元数据审核/审核规则库/慢SQL记录/上线检查/大表治理） | **合计 0 个入口** |
| 后果 | `copilotSelection` 永远是 `{}` → `source_refs` 永远为空 → 业务场景全部 422 SOURCE_REQUIRED |

**证据**：`r2_browser.json`（`M02_scan` 七页全空、`M02_total_entries: 0`）、
代码扫描（`openCopilotWith` = 0）。

**解决方案（照图施工）**

桥已经好了，**只差把八个业务结果区挂上按钮**。以"在线元数据审核"为例（其余同构，见下表）：

```html
<!-- 在线元数据审核：任务结果卡按钮组内 -->
<el-button v-if="visibleMenus.has('copilot')" size="small" type="primary" plain
           :disabled="!selectedJobRow"
           @click="openCopilotWith({
             page_key:'schema-extractor-audit',
             source_refs:[{kind:'metadata_job', job_id:selectedJobRow.job_id,
                           offset:0, limit:10}],
             draft:null })">
  让 Copilot 解读
</el-button>
```

| 模块 | 入口文案 | `source_refs` 形状（严格按 §12.3 判别联合） |
|---|---|---|
| 即时审核 | 解读本次审核结果 | `[{kind:'audit_history', history_id, statement_indexes:[…]}]` |
| 文件审核 | 解读本次审核结果 | 同上 |
| 在线元数据审核 | 让 Copilot 解读 / 分析失败原因 | `[{kind:'metadata_job', job_id, offset:0, limit:10}]` |
| 审核规则库 | 解释这条规则 | `[{kind:'rule', rule_ids:[…≤10]}]` |
| 慢SQL记录 / EXPLAIN | 解读这条慢SQL | `[{kind:'slow_query', slow_id}]` |
| 四类扫描对比 | 解读本次对比 | `[{kind:'scan_snapshot', snapshot_ids:[a,b]}]` |
| 表类型统计 | 解读本次统计 | `[{kind:'table_type_stat', stat_id}]` |
| 网关日志分析 | 解读这份报告 | `[{kind:'gateway_report', report_id}]` |
| SQL 编辑器 | 生成修改建议 | `source_refs:[]` + `draft:{kind:'SQL', text, revision}` |

**加锁防漏**（§13.1 的 8 个模块是硬清单）：

```python
def test_required_modules_expose_copilot_entry():
    html = Path("frontend/index.html").read_text(encoding="utf-8")
    assert html.count("openCopilotWith(") >= 8, \
        "§13.1 首期需接线 8 个业务模块，结果区必须存在 Copilot 入口（回归 D40-M02）"
    for label in ("解读本次结果", "解释这条规则", "分析失败原因", "生成修改建议"):
        assert label in html
```

---

### R2-M02 D40-N01 无效：`MIN_SCORE = 0.5` 拦不住越界主题

**实测分数分布（同一检索函数）**

| 问题 | 命中条数 | 分数 |
|---|---|---|
| 越界·Oracle ROWNUM | **8** | 8.38, 7.686, 7.669, 6.492, 6.423, 3.11 … |
| 越界·红烧肉 | 0 | — |
| 越界·上证指数 | 0 | — |
| 在域·元数据审核 | 8 | 23.29, 16.31, 10.39, 6.75 … |
| 在域·分片键 | 8 | 28.67, 8.38, 7.686 … |

`MIN_SCORE=0.5` 只能滤掉"零词重叠"（本来就 0 分）的问题。
**Oracle 问题因共享 "TDSQL"/"怎么写" 等词，得分 3.1–8.4，全部越线。**
阈值比在域问题的分数低 1–2 个数量级，等于没设。

**证据**：`r2_n01_scores.json`、`r2_api_checks.json`（接口侧 `knowledge_count: 8`）。

**解决方案（照图施工）**

阈值必须**用黄金集标定**，不能拍一个 0.5。施工步骤：

1. 在 `r2_n01_scores.py` 的基础上扩成"在域 18 题 + 越界 10 题"的标定集，
   打印两组分数分布（本轮已给出脚本骨架与首批数据）；
2. 取"在域最低分"与"越界最高分"之间的分界（当前数据下大致在 **10 附近**），
   落到 `MIN_SCORE`，并把标定脚本纳入发布门禁（§16.3 M-03 要求知识包变更后重跑）；
3. **不能只靠阈值**：越界问题即使分数低，也应走"明确未知"话术。
   在 `render_usage_help()` 里把判据从"`knowledge == []`"改成
   "`knowledge == []` **或** 全部低于阈值"，统一输出
   "本地知识库未收录该主题，请查阅使用手册"；
4. 加用例：越界问题断言 `knowledge == []` 且答案含"未收录"；在域问题断言非空。

---

### R2-M03 D40-N02 无效：`local_ready()` 全仓零调用方

**实测（公平复测：给 Web 进程也设同一个 `TDSQL_SQLCHECK_DIR`）**

| 条件 | `capabilities.mode` |
|---|---|
| 标记存在 + DB `enabled=true` + 重启 Web | **READY** ❌（应为 DISABLED） |
| 删除标记 + 重启 | READY |

**根因（两层）**

1. **`capabilities.mode` 根本不看 bootstrap 结论。** `backend/api/copilot.py:109-125`
   只用 `evaluate_ready()`（B组结构）＋`enabled`（部署闸 ∧ DB 开关 ∧ crypto ∧ policy）
   ＋`_has_route()` 来算 mode，**从不调用 `local_ready()`**。
2. **`local_ready()` 全仓零调用方。** 它定义在
   `backend/services/copilot/__init__.py:132`，除定义处外**没有任何引用**。
   也就是说 `copilot_bootstrap_check()` 算出来的 `_LOCAL_READY` 只写进了日志——
   **包括原本就有的 B 组结构判定，整条链路都是"算了不用"的死代码。**

**证据**：`r2_n02.json`（两种条件均为 READY）、全仓 `local_ready` 引用检索（仅 1 处定义）。

**解决方案（照图施工）**

二选一，**推荐方案 A**：

**方案 A（推荐）——让标记成为真正的硬闸**，在受理入口与能力展示两处同时生效：

```diff
--- a/backend/api/copilot.py
+++ b/backend/api/copilot.py
@@
-        enabled = bool(copilot_enabled()) and bool(settings.get("enabled", False)) \
-            and crypto_available() and policy_available()
+        from backend.services.copilot import local_ready
+        _lr, _lr_reason = local_ready()
+        enabled = bool(copilot_enabled()) and bool(settings.get("enabled", False)) \
+            and crypto_available() and policy_available() \
+            and not (_lr_reason == "COPILOT_DISABLED")     # 持久停用标记：硬闸
         if not ready:
             mode = "UNAVAILABLE"
         elif not enabled:
             mode = "DISABLED"
```

并在 `POST /sessions/{id}/turns` 的受理事务（§11.2 伪代码第 4 步"新请求才检查开关"）
里同样加入该判据，返回 `COPILOT_DISABLED`——保证"标记存在时无法新受理"，
而不只是"页面显示为未启用"。

**方案 B——把标记降级为审计留痕**：删掉 bootstrap 里的读取代码，
改在 `deploy/README.md` 与 §16.6 表述为"DB 侧停用才是强制手段，本文件供人工恢复前确认"。
**但这样就没有"禁止自动重启后恢复"了**，不满足 §16.6 第 1 条，不建议。

**必须加的锁**：

```python
def test_persistent_disable_marker_blocks_capabilities(tmp_path, monkeypatch):
    """停用标记存在时，capabilities 必须为 DISABLED（回归 D40-N02）。"""
    monkeypatch.setenv("TDSQL_SQLCHECK_DIR", str(tmp_path))
    (tmp_path.parent / "conf").mkdir(exist_ok=True)
    (tmp_path.parent / "conf/copilot-disabled.json").write_text("{}")
    copilot_bootstrap_check()
    assert local_ready() == (False, "COPILOT_DISABLED")
    # 并断言 capabilities 的 mode 不再是 READY
```

---

### R2-M04 D40-N03 伪实现：读一次自增一次，且两个新方法无人调用

**实测（代码语义 + 调用点扫描）**

```js
let _editorRev = 0;
function _revCounter(){ return String(++_editorRev) }          // ← 调用即自增
readRevision: () => String(sqlInput.value ? _revCounter() : '0'),
applyDraftIfRevision: (expected, text) => {
  if (String(_revCounter()) !== String(expected)) { ...warn('草稿已变更'); return false }
  ...
}
```

| 检查 | 结果 |
|---|---|
| `readRevision` 调用点 | **0**（仅定义） |
| `applyDraftIfRevision` 调用点 | **0**（仅定义；`copilot.js` 仍走 `previewReplacement`） |
| `_editorRev` 是否随用户编辑草稿自增 | **从不**（只在被读/被比较时自增） |
| 假如被调用会怎样 | 读 → 计 1；比较 → 计 2 ≠ 1 → **永远拒绝应用** |

即：**N-03 要解决的"拒绝覆盖未保存草稿"既没接上，逻辑本身也是错的**。
第一轮 N-03 的实质问题（`previewReplacement` 直接覆盖 `sqlInput`）**依然存在**。

**证据**：`frontend/static/js/app.js:57,58,82,84`；调用点扫描（3 个文件计数）；
`copilot.js` 中 `previewReplacement` 2 次、两个新方法 0 次。

**解决方案（照图施工）**

**(a) 版本号必须由"编辑动作"驱动，读取不得有副作用：**

```js
// app.js
let _editorRev = 0;
const editorRevision = ref('0');
watch(sqlInput, () => { _editorRev += 1; editorRevision.value = String(_editorRev); });
// 只读，不改
readRevision: () => editorRevision.value,
```

**(b) 把 `previewReplacement` 换成"先差异、再确认、后写入"：**

```js
editorBridge: {
  readRevision: () => editorRevision.value,
  previewReplacement(candidate) {
    const expected = editorRevision.value;
    // §13.1：先显示差异，用户确认后才比较 revision 并写入
    showDiffDialog({ before: sqlInput.value, after: candidate.sql,
      onConfirm: () => editorBridge.applyDraftIfRevision(expected, candidate.sql) });
  },
  applyDraftIfRevision(expected, text) {
    if (editorRevision.value !== String(expected)) {   // 异步期间用户改过草稿
      ElementPlus.ElMessage.warning('草稿已变更，请重新比较'); return false;
    }
    sqlInput.value = text; currentPage.value = 'audit-sql'; return true;
  },
},
```

**(c) 用一条测试锁住语义**（Node 侧或 Playwright 均可）：连续两次 `readRevision()`
必须返回相同值；修改 `sqlInput` 后 `readRevision()` 必须变化；
revision 不匹配时 `applyDraftIfRevision` 必须返回 `false` 且**不修改**输入框。

---

### R2-M05 D40-N04 半成品：会话下拉字段名错、新建会话缺参 422

**实测（浏览器真实点击 + 接口对照）**

| 检查 | 结果 |
|---|---|
| 抽屉内有会话下拉 / 新建按钮 | ✅ 有（`N04_has_session_select: true`、`N04_has_new_session_btn: true`） |
| 点击「新建会话」 | ❌ 抽屉内显示 **"请求失败（422）"** |
| 页面模板绑定 | `s.session_id` ✅ |
| **抽屉模板绑定** | **`s.id`** ❌ —— 会话列表项里**没有 `id` 字段**（只有 `session_id`） |
| 接口事实 | `POST /sessions` 不带 `scope_kind` → 422 `Field required`；带则 201 |

**根因**：抽屉按钮 `@click="copilot.createSession()"` **无参调用** →
`scope_kind: undefined` → 422；且下拉 `:value="s.id"` 取不到值，
即使有会话也选不中（`N04_select_values: ["", ""]`）。

**证据**：`r2_browser.json`（抽屉文本含"请求失败（422）"）、
`r2_n04_binding.json`（`page_binding: ["s.session_id","s.session_id"]`
vs `drawer_binding: ["s.id","s.id"]`；接口 422/201 对照）。

**解决方案（照图施工）**

```diff
--- a/frontend/index.html
+++ b/frontend/index.html
@@
       <div class="copilot-toolbar" style="margin-top:4px">
         <el-select v-model="copilot.currentSessionId" placeholder="选择会话" size="small" clearable style="flex:1" @change="copilot.selectSession">
-          <el-option v-for="s in copilot.sessions" :key="s.id" :label="s.scene + ' ' + (s.created_at||'').slice(0,16)" :value="s.id"></el-option>
+          <el-option v-for="s in copilot.sessions" :key="s.session_id"
+                     :label="(s.connection_name || s.scope_kind) + ' ' + (s.updated_at||'').slice(0,16)"
+                     :value="s.session_id"></el-option>
         </el-select>
-        <el-button size="small" type="primary" plain @click="copilot.createSession()">新建会话</el-button>
+        <el-button size="small" type="primary" plain
+                   @click="copilot.createSession(copilot.selectedConnectionId ? 'INSTANCE' : 'GLOBAL_HELP')">
+          新建会话
+        </el-button>
       </div>
```

> 顺带修掉 `s.scene`（列表项里也没有这个字段，标签会显示 "undefined"），
> 用 `connection_name`/`scope_kind` 更贴近用户认知。

**加锁**：一条静态锁即可挡住字段名错配——断言抽屉模板里出现的会话字段名
必须与 `SessionRepo.list_for_owner` 返回的键集合一致。

---

### R2-M06 D40-M04 口径偏差：`feedback-summary` 未按 `rule_snapshot_hash` 分组

**实测**：端点已可用（200），隐私阈值生效（2 个 subject 时不展示，
`hidden_below_privacy_threshold: 2`），提示文案正确。

**但与 §12.5 / N-07 的口径有 3 处偏差**：

| 设计要求 | Q 的实现 | 影响 |
|---|---|---|
| 按 `scene + rule_id + rule_snapshot_hash` 分组 | 只按 `scene, feedback_rule_ids_json` | **合并了不同规则尺度**，违反 N-07"不能合并不同规则尺度" |
| "一轮多规则**分别**计入" | `rule_ids` 作为数组留在一行 | 多规则轮次只算 1 次，规则命中数被低估 |
| "先用 `feedback_at` 索引**窄查**至多 5001 行、单次 3 秒" | `GROUP BY … LIMIT 5000`（分组后截断） | 不是有界窄查；数据量大时既慢又可能截断失真 |

**证据**：`r2_api_checks.json`（端点在 2/3 subject 下的行为、返回结构）。

**解决方案（照图施工）**

```python
# 1) 先做有界窄查（用 feedback_at 索引，单次 3 秒预算）
rows = conn.execute(
    "SELECT feedback_rule_ids_json, COALESCE(rule_snapshot_hash,'') AS rsh, "
    "       scene, owner_subject_id, feedback_at "
    "FROM copilot_turns "
    "WHERE feedback_code = 'INCORRECT' AND feedback_at >= %s AND feedback_at < %s "
    "ORDER BY feedback_at DESC LIMIT 5001", (win_start, win_end)).fetchall()
if len(rows) > 5000:
    raise CopilotError("CONTEXT_TOO_LARGE", message="反馈数据过多，请缩短时间窗")

# 2) 一轮多规则分别计入：按 (scene, rule_id, rsh) 拆行聚合
buckets: dict[tuple, dict] = {}
for r in rows:
    rids = json.loads(r["feedback_rule_ids_json"] or "[]") or [None]   # N-07：无规则归 UNASSIGNED
    for rid in rids:
        k = (r["scene"], rid or "UNASSIGNED", r["rsh"])
        b = buckets.setdefault(k, {"scene": r["scene"],
                                   "rule_id": rid or "UNASSIGNED",
                                   "rule_snapshot_hash": r["rsh"],
                                   "count": 0, "subjects": set()})
        b["count"] += 1
        b["subjects"].add(r["owner_subject_id"])

# 3) 分组数上限与隐私阈值
if len(buckets) > 1000:
    raise CopilotError("CONTEXT_TOO_LARGE", message="分组过多，请缩短时间窗")
items = [{**{kk: vv for kk, vv in b.items() if kk != "subjects"},
          "subject_count": len(b["subjects"])}
         for b in buckets.values() if len(b["subjects"]) >= 3]
hidden = sum(1 for b in buckets.values() if len(b["subjects"]) < 3)
```

并加用例：造"一轮含 2 条规则"的反馈 → 断言产出 **2 行**（分别计数）；
造两个不同 `rule_snapshot_hash` 的同类反馈 → 断言**不被合并**。

---

## 4. 一般级缺陷（2 项）

### R2-N01 `TDSQL_SQLCHECK_DIR` 未在任何部署件中登记

**事实**：该变量全仓**只有 2 处产品引用**——`deploy/copilot_emergency_disable.sh:34`
与新增的 `backend/services/copilot/__init__.py:120`。
`deploy/tdsql-sqlcheck.service`、`deploy/tdsql-copilot-runner.service`、
`deploy/env.template` **都没有设置它**。

**影响**：默认安装到 `/opt/tdsql-sqlcheck` 时，脚本与读取方都回落到同一默认值，
行为一致（这也是我首测"失败"其实是不公平测试的原因，已在 `r2_n02.json` 中如实记录）。
但安装器支持自定义目录（unit 里是 `__INSTALL_DIR__` 模板），
**自定义安装时标记会写到 `/opt/conf/`——与真实安装目录脱钩**，可能不可写或随清理丢失。

**解决方案**：在 `deploy/env.template` 增加
`TDSQL_SQLCHECK_DIR=__INSTALL_DIR__`，并在两个 unit 的 `Environment=` 中带上该变量；
安装脚本落 `.env` 时替换为真实目录。同时在 `deploy/README.md` 的应急章节写明
"标记文件路径由 `TDSQL_SQLCHECK_DIR` 决定，脚本与应用必须一致"。

### R2-N02 D40-N05 导出 CSP 本轮**无法复验**（如实声明）

导出接口要求该轮为带结果的终态；而本轮**没有任何 `SUCCEEDED/LOCAL_ONLY/DEGRADED` 轮次**
（全部 `FAILED`），因此 `turn_id` 为空，无法取到真实的导出响应头。
代码层面 Q 的改动是正确方向（`default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox`），
**但按 UAT 纪律，未跑到的就是未验证**——待 R2-B01/B02 修复后与第三轮一并复验。

---

## 5. 流程级问题

### R2-P01 16 项修复，0 条回归锁

| 检查 | 结果 |
|---|---|
| `git show --name-only 79885fc` 中 `tests/` 路径 | **0 个** |
| `pytest tests/copilot --collect-only` | **114 tests**（与修复前完全一致） |
| 我第一轮报告要求的 4 条锁（DOM 结构 / 首屏无遮罩 / 真跑模型 / 端到端启用） | **一条都没加** |

**这不是形式问题。**本轮 3 个阻断缺陷里，**R2-B03（自检 500）与 R2-B01（证据链断裂）
都是"没有任何测试覆盖那条路径"的直接后果**；R2-M02/M03/M04 三个"修了等于没修"的项，
也全都能被一条最小用例挡住。第一轮我给出过用例骨架，本轮再补一遍（见各条目"加锁"小节），
并建议把它作为**UAT 复验的准入条件**：修复提交必须同时提交对应回归锁，
否则下一轮仍会出现"改了 A 坏了 B"。

---

## 6. 本轮无法复验 / 边界声明

1. **真实内网模型网关**：仍用同协议受控网关（TLS 校验真实开启、Bearer 真实发送、
   报文逐字节落盘），替换的只有"供应商那一段"。真实网关的认证/留存政策仍需 G 的 CP-GATE-DATA。
2. **systemd / Linux 应急路径**：本机无 systemd，`systemctl` 分支未实测；
   DB 侧分支与"无 pkill"分支均为真实执行（这正是 M05 要验的场景）。
3. **导出报告 CSP（D40-N05）**：因本轮无终态轮次而无法复验，见 R2-N02。
4. **N-09 双管理员、四道闸、幂等限流**等第一轮已通过项，本轮只做快检（未发现回退）。
5. **未做**：200 轮压力与长稳、真实模型黄金集答案质量、五条发布链路 —— 边界同第一轮。

---

## 7. 防回退结果（本轮全绿）

| 项 | 结果 |
|---|---|
| `tests/copilot/` 独立复跑（干净树） | **114 passed** ✅（与 Q 自述一致） |
| 主产品：即时审核 | `200` / `passed=true` / 0 违规 ✅ |
| 主产品：在线元数据审核任务 | `202` → `SUCCEEDED`，**4.2 s** ✅ |
| RBAC 四角色 | admin 200/200、第二 admin 200/200、developer 200/**403**、auditor **403/403** ✅ |
| 出站安全（本轮 22 次真实出站） | 含 `tools`/`functions` 的请求 **0** 次；全部 `Authorization: Bearer` ✅ |
| 越权访问他人会话 | `404`（不暴露存在性）✅ |
| 会话创建 | `201` ✅ |

---

## 8. 出口判定与下一步

### 判定

**第二轮 UAT 不通过（NO-GO）。**

理由：

1. **功能上比第一轮更差**：第一轮至少能"点开并拿到一个降级答案"，本轮**所有业务轮次 100% FAILED**；
2. **模型依然打不开**（自检 500，死锁未解）；
3. **第一轮唯一的高危安全项（M01 出站投影）虽然修好了**，但修复方式打断了证据链——
   这说明**修复过程本身缺少护栏**，是流程问题而非单点失误；
4. 16 项里有 **6 项属于"改了但无效/半成品"**，共同特征是**没有端到端执行过一次**。

### 建议的整改与复验顺序

| 批次 | 内容 | 依赖 |
|---|---|---|
| **第 1 批（阻断，必须同批）** | R2-B01 拆分取证/出站载荷 + R2-B02 runner 加载知识包 + R2-B03 自检补字段 | B03 依赖 B01 修好后才可能自检成功（自检本身也要跑模型） |
| **第 2 批（业务可达）** | R2-M01 八个业务入口接线 | 依赖第 1 批（否则接上了也是 FAILED） |
| **第 3 批（半成品收口）** | R2-M02 阈值标定、R2-M03 让 `local_ready()` 真正生效、R2-M04 草稿版本语义、R2-M05 抽屉字段与缺参 | 独立 |
| **第 4 批（口径与部署）** | R2-M06 feedback-summary 分组、R2-N01 `TDSQL_SQLCHECK_DIR` 登记、N05 CSP 复验 | 独立 |

**每一批必须同时提交回归锁**（本报告已逐条给出骨架），
并建议把"修复提交是否带锁"写进 UAT 准入清单。

**第三轮 UAT 我会重点复验**：
① 业务轮次能否真正走到 `SUCCEEDED`（用真实带证据的场景端到端跑通）；
② 自检 → 启用 → 出站 的完整管理员链路；
③ 八个业务入口在浏览器里是否真的可用；
④ 出站报文是否**仍然**逐字节等于冻结投影（防止为了修 B01 又把 M01 改回去）。
**这四条互为前提，任何一条回退都会立刻暴露。**

---

## 9. 证据索引

目录：`docs/evidence/v1.6.4.0-uat2-d/`

| 主题 | 文件 |
|---|---|
| **B04 自检端到端** | `run2.py`、`uat2_d40_core.py`、`r2_b04_selftest.json`、`web.err.log`（KeyError 栈） |
| **M01 出站==冻结投影** | `r2_m01_projection.json`（三配置逐字节比对） |
| **R2-B01 证据链断裂** | `r2_evidence_chain.py`、`r2_evidence_chain.json`、`probe_turns.py`、`probe_failed_result.py` |
| **R2-B02 runner 知识能力** | `r2_knowledge_step.py`、`r2_knowledge_step.json`、`r2_knowledge_probe.json` |
| **R2-M01 业务入口** | `r2_browser.json`（`M02_scan`）、`shots/r2-m03-copilot-page.png` |
| **R2-M02 知识阈值** | `r2_n01_scores.py`、`r2_n01_scores.json`、`r2_api_checks.json` |
| **R2-M03 持久标记** | `r2_n02.py`、`r2_n02.json`、`r2_emergency.json` |
| **R2-M04 草稿版本** | 代码扫描（`app.js:57,58,82,84`）；`r2_api_checks.json`（`draft.revision` 冻结验证） |
| **R2-M05 抽屉会话** | `r2_n04_binding.py`、`r2_n04_binding.json`、`r2_browser.json`（422 实证）、`shots/r2-n04-drawer-session.png` |
| **R2-M06 feedback-summary** | `r2_api_checks.json`（隐私阈值 2/3 subject 行为） |
| **M05 应急脚本** | `r2_emergency.py`、`r2_emergency.json`（rc=3、`runner_stopped=manual-required`、`result=partial`） |
| **回归与 RBAC** | `r2_regression.py`、`r2_regression.json` |
| **浏览器截图（5 张）** | `shots/r2-b01-anonymous.png`（未登录干净）、`r2-b02-drawer.png`（抽屉可用）、`r2-m03-copilot-page.png`（模式"已启用"）、`r2-b05-ai-config.png`（AI配置页）、`r2-n04-drawer-session.png`（新建会话 422） |

### 一键复现

```powershell
# 0) 起夹具（网关 + Web + Copilot runner + 元数据执行器）
powershell -ExecutionPolicy Bypass -File docs/evidence/v1.6.4.0-uat-d/start_all.ps1
# 1) 自检端到端（B04/B03）
python docs/evidence/v1.6.4.0-uat2-d/run2.py b04
# 2) 出站 == 冻结投影（M01）
python docs/evidence/v1.6.4.0-uat2-d/run2.py m01
# 3) 证据链受控实验（R2-B01）与 runner 知识能力（R2-B02）
python docs/evidence/v1.6.4.0-uat2-d/r2_evidence_chain.py
python docs/evidence/v1.6.4.0-uat2-d/r2_knowledge_step.py
# 4) API 侧复验（N01/N05/N06/M04）
python docs/evidence/v1.6.4.0-uat2-d/r2_api_checks.py
# 5) 浏览器复验（B01/B02/B05/M02/M03/N04）
python docs/evidence/v1.6.4.0-uat2-d/r2_browser.py
# 6) 应急脚本 + 持久标记（M05/N02）与回归
python docs/evidence/v1.6.4.0-uat2-d/r2_emergency.py
python docs/evidence/v1.6.4.0-uat2-d/r2_n02.py
python docs/evidence/v1.6.4.0-uat2-d/r2_regression.py
# 7) 收尾
powershell -ExecutionPolicy Bypass -File docs/evidence/v1.6.4.0-uat-d/stop_all.ps1
python docs/evidence/v1.6.4.0-uat-d/prepare_uat_d40.py untrust   # 还原 certifi
```

---

## 10. 给 Mr.Linsang 的一句话

第一轮的"看不见"确实修好了——**用户现在能打开 Copilot 了**；
但第二轮的"答不出"比第一轮更严重：**所有业务问答 100% 失败，模型仍然打不开**。
根子不在某一个补丁写错，而在**修复没有护栏**：16 项改动、0 条新用例，
于是"修好出站投影"顺手打断了证据链，"补上自检端点"从没被真实调用过一次。
建议把"**修复必须带回归锁**"作为第三轮 UAT 的准入条件——
否则每修一轮，都要靠下一轮 UAT 去发现上一轮修坏了什么。

---

**-智能体D**

*2026-09-15 · TDSQL-SQLCheck v1.6.4.0 AI Copilot 第二轮 UAT*
