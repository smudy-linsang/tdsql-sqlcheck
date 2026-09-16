# v1.6.4.0 AI Copilot 专家助手 · 第四轮用户验收测试报告（复测）

| 项 | 内容 |
|---|---|
| 版本 | v1.6.4.0 / CP-1 |
| 被测提交 | `052cc39`（Q 的"UAT第三轮4项修复"） |
| 上轮基线 | `8859f7b`（第三轮判定 NO-GO，剩 1 项阻断） |
| 测试性质 | 第四轮 UAT：复测第三轮 4 项修复 + 防回退 + 准入条件核验 |
| 测试方 / 署名 | **智能体D** |
| 测试日期 | 2026-09-16 |
| 证据目录 | `docs/evidence/v1.6.4.0-uat4-d/`（脚本 4 份、JSON 证据 5 份、已验证补丁 1 份） |
| 提交对象 | Mr.Linsang |

---

## 0. 结论

**第四轮 UAT 判定：仍不通过（NO-GO）。第三轮 4 项修复里 3 项已闭合，唯一未闭合的仍是那条自检链路。**

| 维度 | 结果 |
|---|---|
| R3-M01 知识阈值 | ✅ **已修复**：黄金集 18/18 命中、越界 **6/6 拒绝**、零召回 0 |
| R3-M02 标记受理拦截 | ✅ **已修复**：`POST /turns` → **503 COPILOT_DISABLED** |
| R3-N01 重名 provider | ✅ **已修复**：500 → **422 INVALID_REQUEST** |
| R2-B03 自检链路（第 4 轮） | ❌ **仍未闭合**：本轮暴露出**两个新的确定性失败**，我已用受控补丁验证修复可行 |
| M01 防回退 | ✅ 出站仍逐字节等于冻结投影 |
| 回归 | ✅ `tests/copilot` **114 passed**、主产品/RBAC/出站安全全绿 |
| 准入条件⑤（必须带回归锁） | ❌ **未满足**（第四轮仍零新增用例） |

> 自检链路已经是**连续第 3 轮、第 3 个不同根因**。每一轮都修掉了上一轮的根因，
> 又露出下一层——而这三层**全都只有"真跑一次"才能发现**。
> 本轮我把整条链路跑通并**验证了修复**，补丁随报告给出。

---

## 1. 逐项复测总表

| 编号 | 第三轮问题 | 本轮实测 | 判定 |
|---|---|---|---|
| **R3-M01** | `MIN_SCORE=10.0` 砍掉 81% 召回 | 改为 `9.0 + RATIO 0.30 + FLOOR 4.0`；**18/18 命中、越界 6/6 拒绝、零召回 0** | ✅ **已修复** |
| **R3-M02** | 标记只拦显示不拦受理 | `capabilities=DISABLED` 且 **`POST /turns` → 503 COPILOT_DISABLED**；删标记后回 READY | ✅ **已修复** |
| **R3-N01** | 重名 provider 返回 500 | **422 `INVALID_REQUEST`**「模型名称已存在，请换一个名称」 | ✅ **已修复** |
| **R2-B03** | 自检 `CONTEXT_CHANGED`（代次写死 0） | 代次已修好，但暴露出**两层新失败**（见 §2） | ❌ **仍未闭合** |
| **R3-P01** | 三轮零回归锁 | `git show --name-only 052cc39` 中 `tests/` 仍为 **0 个**；用例数仍 114 | ❌ **未整改** |
| **M01 防回退** | —— | `RULE_EXPLAIN` / `SQL_ADVISE` 两场景 `SUCCEEDED` + `answer_source=MODEL`，`出站 == 冻结投影` 全 true | ✅ **无回退** |

---

## 2. 唯一阻断：自检链路的第 4 轮（本轮定位到两层）

### 2.1 现象

| 步骤 | 本轮结果 |
|---|---|
| `POST /providers` | 201 ✓ |
| `PUT /enabled`（未自检） | 422 ✓ |
| `POST /self-tests` | 202 ACCEPTED ✓ |
| 轮次终态 | ❌ `FAILED` / **`error_code = INTERNAL_ERROR`**，出站 **0** |
| `tested_revision` | `null` → `PUT /enabled` 仍 422 → **死锁依旧** |

服务端栈（`cprunner.err.log`）：

```
File "backend/services/copilot/workflow.py", line 229, in _request_payload
    raw = crypto_mod.decrypt(self.preview["payload_envelope"], "copilot_previews",
                             self.preview["id"], "payload_envelope", ...)
CryptoUnavailableError: 封套解密失败（AAD/密文/kid 不匹配）
cryptography.exceptions.InvalidTag
```

### 2.2 根因一（R4-B01）：封套 AAD 的行 ID 用了字面量 `"selftest"`

`selftest.admit_self_test()` 加密封套时，把 **AAD 的行 ID 写成常量 `"selftest"`**：

```python
payload_envelope = crypto_mod.encrypt(
    json.dumps(payload, ensure_ascii=False), "copilot_previews",
    "selftest", "payload_envelope", owner=identity.subject_id, keyring=keyring)
#  ↑ 应为真实 preview_id
projection_envelope = crypto_mod.encrypt(
    ..., "copilot_previews", "selftest", "model_projection_envelope", ...)
```

而 runner 解密时用的是**真实主键**（`workflow.py:229`）：

```python
raw = crypto_mod.decrypt(self.preview["payload_envelope"], "copilot_previews",
                         self.preview["id"], "payload_envelope", ...)
#                        ↑ AAD 行 ID = 真实 preview_id
```

AES-GCM 的 AAD 不匹配 → `InvalidTag` → **每一次自检都必然解密失败**（确定性，非偶发）。
两个封套（`payload_envelope` 与 `model_projection_envelope`）都是同一个写法。

### 2.3 根因二（R4-B02）：自检轮被 `enabled=0` 挡住 —— 而自检的前提恰恰是"尚未启用"

修好 AAD 之后，错误推进为 `EVIDENCE_UNAVAILABLE`、出站仍为 0。继续定位到：

```python
# backend/services/copilot/workflow.py:273
def _load_provider_frozen(self, leg_info: dict) -> Optional[dict]:
    p = ProviderRepo.get(self.conn, leg_info["provider_id"])
    ...
    if not int(p.get("enabled") or 0):
        return None            # ← 未启用直接放弃这条腿
```

**逻辑死结**：自检的设计目的是「**启用之前**先验证连通性」（§12.6），
所以自检发起时 provider 必然是 `enabled=0`；而调用链又要求 `enabled=1` 才肯发请求。
两者不可能同时成立 → **自检永远发不出模型请求** → 只能落到本地降级 →
`has_evidence` 与 `has_knowledge` 均为假 → `EVIDENCE_UNAVAILABLE`。

### 2.4 附带发现（R4-N01，潜在）：`_collect()` 没有 `PROVIDER_SELFTEST` 分支

`_collect()` 的场景分支只覆盖 `USAGE_HELP|DIAGNOSTIC_HELP|RULE_EXPLAIN|AUDIT_EXPLAIN|
JOB_TROUBLESHOOT|SLOW_EXPLAIN|COMPARE_EXPLAIN|TABLETYPE_EXPLAIN|GATEWAY_EXPLAIN|SQL_ADVISE`，
**没有 `PROVIDER_SELFTEST`** → 该场景的证据与知识**必然为空**。
今天因为自检走的是模型成功路径，这个空集合没有暴露；一旦模型返回不合规答案而走本地降级，
用户看到的会是"所选资料不可用"，而不是"自检不通过"——**错误归因会跑偏**。

### 2.5 我已验证修复可行（受控补丁 + 实测）

我临时施加了上述两处修复（不改任何产品逻辑，仅补 AAD 与放开自检腿），重启后重跑同一条链路：

| 检查 | 修复前 | **修复后** |
|---|---|---|
| 自检轮终态 | `FAILED` / INTERNAL_ERROR | **`SUCCEEDED`** ✅ |
| 出站次数 | 0 | **1** ✅ |
| `tested_revision` | `null` | **`1`（= revision）** ✅ |
| `PUT /enabled` | 422 | **200 / enabled=true** ✅ |

补丁见 `docs/evidence/v1.6.4.0-uat4-d/verified-fix-selftest.patch`（`git apply` 可直接用），
验证后**已完整回滚**，干净树复跑 `tests/copilot` 得 **114 passed**。

> **⚠️ 更正（同上补记）**：本补丁文件初版是用 PowerShell 的 `>` 重定向生成的，
> 被写成了 **UTF-16LE**，`git apply` 报 `No valid patches in input` —— 这是我这边交付物的缺陷。
> 现已改用 `git diff --output=…`（由 git 自己写字节）并限定为仅两个产品文件重新生成：
> 编码正确（以 ASCII `diff` 开头）、排除自引用、**干净树上 `git apply --check` 通过**。

### 2.6 解决方案（照图施工）

**修复一：封套 AAD 必须用真实 `preview_id`（把 id 生成提到加密之前）**

```diff
--- a/backend/services/copilot/selftest.py
+++ b/backend/services/copilot/selftest.py
@@
     from backend.services.copilot import crypto as crypto_mod
     keyring = crypto_mod.load_keyring()
+    # AAD 行 ID 必须与 runner 解密时一致（它用 preview["id"]），
+    # 不能用字面量；否则 AES-GCM 必然 InvalidTag。
+    preview_id = new_id()
     payload_envelope = crypto_mod.encrypt(
         json.dumps(payload, ensure_ascii=False), "copilot_previews",
-        "selftest", "payload_envelope", owner=identity.subject_id,
+        preview_id, "payload_envelope", owner=identity.subject_id,
         keyring=keyring)
     projection_envelope = crypto_mod.encrypt(
         json.dumps(payload, ensure_ascii=False), "copilot_previews",
-        "selftest", "model_projection_envelope", owner=identity.subject_id,
+        preview_id, "model_projection_envelope", owner=identity.subject_id,
         keyring=keyring)
@@
     # 创建 preview（自检也需要 preview 因为 runner 会读）
     from backend.services.copilot.repository import PreviewRepo
-    preview_id = new_id()
+    # preview_id 已在加密封套前生成（见上）
     PreviewRepo.insert(conn, {
         "id": preview_id,
```

**修复二：自检轮必须允许对"未启用"的 provider 发起验证调用**

```diff
--- a/backend/services/copilot/workflow.py
+++ b/backend/services/copilot/workflow.py
@@
     def _load_provider_frozen(self, leg_info: dict) -> Optional[dict]:
         """运行轮次冻结旧版本：revision 不符即失败关闭，不在途中换 key。"""
         p = ProviderRepo.get(self.conn, leg_info["provider_id"])
         if p is None:
             return None
         if int(p.get("revision") or 0) != int(leg_info.get("provider_revision") or 0):
             self.last_model_error = "PROVIDER_CONFIG_INVALID"
             return None
         if not int(p.get("enabled") or 0):
-            return None
+            # §12.6：自检的目的就是"启用前先验证"，此时 provider 必然 enabled=0。
+            # 仅对 PROVIDER_SELFTEST 轮放行；普通业务轮仍要求 enabled=1。
+            if self.turn.get("turn_kind") != TurnKind.PROVIDER_SELFTEST.value:
+                return None
         return p
```

> 注意用 `TurnKind.PROVIDER_SELFTEST.value` 而不是字符串字面量，避免又一处"字面量对不上"的隐患。

> **⚠️ 更正（2026-09-16 补记）**：本节初版写的是"`workflow.py` 已在别处 import 该枚举"——
> **这句话是错的**。`workflow.py` 第 27 行原文只导入了 `ModelAnswer`：
> ```python
> from backend.models.copilot import ModelAnswer          # 原文
> ```
> 按初版说明直接施工会抛 `NameError: name 'TurnKind' is not defined`，
> 把"自检失败"换成"自检崩溃"。**必须同时补上导入**：
> ```diff
> -from backend.models.copilot import ModelAnswer
> +from backend.models.copilot import ModelAnswer, TurnKind
> ```
> 已修正的补丁见 `docs/evidence/v1.6.4.0-uat4-d/verified-fix-selftest.patch`
> （含该导入，已在干净树 `git apply --check` 通过，并重跑功能验证：
> `SUCCEEDED` + `tested_revision=1` + 启用 200）。

**修复三（建议，防错误归因）：`_collect()` 给自检场景一个显式分支**

```diff
     def _collect(self) -> tuple[list[dict], list[dict], dict]:
         payload = self._request_payload()
         refs = payload.get("source_refs") or []
         scene = self.turn["scene"]
         question = payload.get("question") or ""
         ...
-        if scene in ("USAGE_HELP", "DIAGNOSTIC_HELP"):
+        if scene == "PROVIDER_SELFTEST":
+            # §12.6：自检不带业务资料，也不做知识检索；空集合是设计使然，不是"资料不可用"。
+            return [], [], {"identifiers_allowed": False}
+        if scene in ("USAGE_HELP", "DIAGNOSTIC_HELP"):
```

并让 `_publish_local` 对自检场景**不要**用 `EVIDENCE_UNAVAILABLE` 报错，
而应报自检自身的原因码（模型不可达/输出不合规），否则运维会把"模型不通"读成"资料缺失"。

---

## 3. 一般发现（1 项）

### R4-N02 相对保留被串联在绝对阈值之后，召回只恢复了一半

随包实现是**两道过滤串联**（先 `score >= MIN_SCORE(9.0)`，再 `score >= max(FLOOR, top1×RATIO)`），
而处方设计是**并联**（绝对下限只判断"是否收录"，保留条数由相对比例决定）。
两种口径实测对比（18 题黄金集）：

| 口径 | 平均召回 | 零召回题 |
|---|---|---|
| **串联（随包）** | **1.61** 条 | 0 |
| **并联（处方）** | **2.39** 条 | 0 |

差异集中在几道题上：「Copilot 专家助手怎么打开？」1 → 4 条、
「TDSQL 二级分区有什么要求？」2 → 5 条、「慢SQL记录和EXPLAIN分析在哪里？」1 → 3 条。

**影响有限**（安全目标已达成：越界 6/6 拒绝、在域 18/18 命中），
但模型/本地模板得到的上下文比设计预期薄，属于"知道得对但知道得少"。
证据：`r4_recall_order.json`。

**解决方案**：把两道过滤改成并联即可（等价于第三轮变体 C）：

```python
# knowledge.py: KnowledgeBundle.search
scored.sort(key=lambda x: (-x[0], ...))
if scored:
    if scored[0][0] < MIN_SCORE:      # ① 绝对下限：判定"是否收录"
        scored = []
    else:                              # ② 相对比例：决定"保留几条"
        _floor = max(MIN_SCORE_FLOOR, scored[0][0] * MIN_SCORE_RATIO)
        scored = [(s, i) for s, i in scored if s >= _floor]
```

---

## 4. 准入条件核验（未满足）

| 条件 | 要求 | `052cc39` 实测 |
|---|---|---|
| ⑤ 修复必须带回归锁 | 修复提交同时提交对应回归锁，且锁能在未修复代码上失败 | ❌ **`tests/` 零改动**，用例数仍 **114** |

**这是连续第三轮未满足。** 代价可以量化：自检链路连续 3 轮、3 个不同根因
（缺必填字段 → 代次写死 → AAD 错 + enabled 死结），**每一个都是确定性失败**，
而我在第一轮给出的那条锁——只要断言 `tested_revision` 被写入——会在**第一次运行时就报红**。

**建议的落锁方式（可直接抄，覆盖本轮全部三层根因）**：

```python
# tests/copilot/test_provider_selftest_e2e.py
def test_provider_selftest_writes_tested_revision(copilot_db, client, monkeypatch):
    """自检必须真正跑完并写回 tested_revision —— 覆盖 R4-B01/B02 与历史 D40-B04。

    只断言"端点返回 202"没有任何意义：202 只代表受理。
    """
    pid = create_provider(client, name=unique_name(), endpoint_id="ep-a")

    # 未自检时必须被拒
    assert client.put(f"/providers/{pid}/enabled",
                      json={"expected_revision": 1, "enabled": True}).status_code == 422

    r = client.post(f"/providers/{pid}/self-tests",
                    json={"client_request_id": uuid4().hex,
                          "expected_provider_revision": 1})
    assert r.status_code == 202
    run_runner_until_terminal(r.json()["turn_id"])       # ← 真跑 runner，不是打桩

    prov = get_provider(client, pid)
    assert prov["tested_revision"] == prov["revision"], \
        "自检未写回 tested_revision：自检链路仍然是死的"
    assert client.put(f"/providers/{pid}/enabled",
                      json={"expected_revision": 1, "enabled": True}).status_code == 200

def test_selftest_envelope_aad_matches_preview_id(copilot_db):
    """回归 R4-B01：封套 AAD 行 ID 必须等于 preview 主键，否则必然 InvalidTag。"""
    pid = create_provider(...)
    admit_self_test(conn, identity, provider, uuid4().hex)
    row = fetch_last_preview(conn)
    # 用 row 的真实主键解密两个封套，必须成功
    assert decrypt(row["payload_envelope"], row_id=row["id"])
    assert decrypt(row["model_projection_envelope"], row_id=row["id"])

def test_selftest_turn_bypasses_enabled_check_only_for_selftest(copilot_db):
    """回归 R4-B02：自检轮可对 enabled=0 的 provider 发请求；业务轮不行。"""
```

---

## 5. 防回退结果（本轮全绿）

| 项 | 结果 |
|---|---|
| `tests/copilot/` 独立复跑（干净树） | **114 passed** ✅ |
| 主产品：即时审核 | `200` / `passed=true` ✅ |
| 主产品：在线元数据审核任务 | `202` → `SUCCEEDED`，**4.1 s** ✅ |
| RBAC | admin 200/200、developer 200/**403**、auditor **403/403** ✅ |
| 出站安全（累计 28 次真实出站） | 含 `tools`/`functions` **0** 次；全部 `Bearer` ✅ |
| 越权访问他人会话 | `404` ✅ |
| **M01 防回退** | `RULE_EXPLAIN`、`SQL_ADVISE` 出站**逐字节等于**冻结投影 ✅ |
| 标记停用 → 恢复 | `DISABLED` → 删标记 → `READY` ✅ |

---

## 6. 出口判定与下一步

### 判定

**第四轮 UAT 不通过（NO-GO）。**

理由只有一条，但很硬：**管理员仍然无法通过产品流程启用任何模型**
（自检链路第 4 轮未闭合，CP-F11 / P0 不满足）。

其余方面本轮表现是四轮里最好的：
- 第三轮 4 项修复 **3 项真闭合**（含两处安全/一致性项）；
- 核心业务链路稳定 `SUCCEEDED`，出站投影一致性**四轮保持不回退**；
- 主产品、RBAC、出站安全全绿。

### 建议的整改（工作量很小，且已验证）

| 批次 | 内容 | 规模 |
|---|---|---|
| **第 1 批（阻断）** | `R4-B01` 封套 AAD 用真实 `preview_id` + `R4-B02` 自检轮放开 `enabled` 检查 | 各 3–5 行，**已实测通过** |
| **第 2 批（防归因）** | `R4-N01` `_collect()` 补自检分支，降级时报自检原因码而非"资料不可用" | 数行 |
| **第 3 批（口径）** | `R4-N02` 两道召回过滤改并联（1.61 → 2.39） | 数行 |
| **第 4 批（准入）** | **补三条回归锁**（见 §4 骨架） | 一个测试文件 |

### 第五轮 UAT 我会怎么做

1. **只认 `tested_revision` 被写入**：自检 → 启用 → 真实出站 → 结果，一步不省；
2. 逐条核验 §4 的三条锁是否落地、且**能在未修复代码上失败**（要求 Q 贴出"红→绿"两次输出）；
3. 复核 M01 出站投影是否**仍然**逐字节等于冻结投影；
4. 若以上全绿，本轮即为**可放行**结论。

---

## 7. 证据索引

目录：`docs/evidence/v1.6.4.0-uat4-d/`

| 主题 | 文件 |
|---|---|
| **核心复测（自检 / 阈值 / 投影）** | `r4_core.py`、`r4_core.json` |
| **自检修复验证（受控补丁后）** | `r4_selftest_verify.py`、`r4_selftest_after_fix.json` |
| **已验证补丁** | `verified-fix-selftest.patch`（AAD + 自检腿放行，实测后已回滚） |
| **标记受理拦截 + 回归** | `r4_marker_regression.py`、`r4_marker_regression.json` |
| **召回口径对照** | `r4_recall_order.py`、`r4_recall_order.json` |

### 一键复现

```powershell
powershell -ExecutionPolicy Bypass -File docs/evidence/v1.6.4.0-uat-d/start_all.ps1
python docs/evidence/v1.6.4.0-uat4-d/r4_core.py                 # 自检(红) + 阈值(绿) + 投影(绿)
python docs/evidence/v1.6.4.0-uat4-d/r4_marker_regression.py    # 标记拦截(绿) + 回归(绿)
python docs/evidence/v1.6.4.0-uat4-d/r4_recall_order.py         # 召回口径 1.61 vs 2.39
# 验证修复可行（受控）：
git apply docs/evidence/v1.6.4.0-uat4-d/verified-fix-selftest.patch
powershell -ExecutionPolicy Bypass -File docs/evidence/v1.6.4.0-uat-d/stop_all.ps1
powershell -ExecutionPolicy Bypass -File docs/evidence/v1.6.4.0-uat-d/start_all.ps1
python docs/evidence/v1.6.4.0-uat4-d/r4_selftest_verify.py      # → SUCCEEDED / tested_revision=1 / enabled 200
git checkout -- backend/services/copilot/selftest.py backend/services/copilot/workflow.py
```

---

## 8. 给 Mr.Linsang 的一句话

本轮**三项修复全部真闭合**（阈值、标记拦截、重名映射），核心链路稳定、防回退四轮未失守——
离放行只差**最后一条自检链路**。它这轮又换了一层根因（AAD 行 ID 写字面量、
以及"要 `enabled=1` 才发请求、可自检恰恰发生在启用之前"的逻辑死结），
两层我都已定位、修复并**实测跑通**（`SUCCEEDED` + `tested_revision=1` + 启用 200），
补丁随报告附上，工程量各 3–5 行。

同时我必须第四次提出同一件事：**这条链路的三个根因，都属于"跑一次就会暴露"的类型**。
我在第一轮给出的那条锁（断言 `tested_revision` 被写入）如果当时落地，
它不会活到今天。**请把"修复必须带锁"作为第五轮的硬性准入条件**——
第五轮我会先验锁，再验功能。

---

**-智能体D**

*2026-09-16 · TDSQL-SQLCheck v1.6.4.0 AI Copilot 第四轮 UAT（复测）*
