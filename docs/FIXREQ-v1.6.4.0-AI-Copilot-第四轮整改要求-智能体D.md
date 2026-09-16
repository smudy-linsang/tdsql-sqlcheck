# v1.6.4.0 AI Copilot · 第四轮 UAT 整改要求

| 项 | 内容 |
|---|---|
| 提出方 | **智能体D**（UAT） |
| 施工方 | Q |
| 依据 | [第四轮 UAT 复测报告](UAT4-v1.6.4.0-AI-Copilot-第四轮复测报告-D.md) |
| 基线 | `052cc39`（已含第三轮 4 项修复，第四轮发现的问题均在本基线之后提出） |
| 目标 | 第五轮 UAT **放行** |

> 本工单只写"做什么、怎么验"，不重复报告里的论证过程。
> **F-1 已有可直接应用的补丁，请优先用它，不要重写。**

---

## 0. 一句话

只剩**一条链路**没通：**provider 自检**。它已经连续拦了 3 轮，本轮定位到两层新根因，
两层都已实测修复可行。另外两条是防归因与口径（低风险），三条回归锁是**准入条件**（硬性）。

| 编号 | 级别 | 内容 | 规模 |
|---|---|---|---|
| **F-1** | **阻断** | 自检链路两处修复（AAD 行 ID + 自检轮放行 enabled 检查） | 补丁已备好 |
| F-2 | 建议 | `_collect()` 补 `PROVIDER_SELFTEST` 分支，避免错误归因 | 约 5 行 |
| F-3 | 建议 | 知识召回两道过滤改并联（1.61 → 2.39 条） | 约 6 行 |
| **F-4** | **准入条件** | 补 3 条回归锁，**并证明它们能在未修复代码上失败** | 1 个测试文件 |

---

## F-1（阻断）自检链路两处修复 —— 补丁已备好，直接应用

```bash
git apply docs/evidence/v1.6.4.0-uat4-d/verified-fix-selftest.patch
```

补丁内容（两处）：

### F-1a 封套 AAD 的行 ID 必须用真实 `preview_id`

`backend/services/copilot/selftest.py` 第 81–88 行，加密时把 AAD 行 ID 写成了字面量
`"selftest"`，而 runner 解密时用的是**真实主键**（`workflow.py:229` 的 `self.preview["id"]`）
→ AES-GCM `InvalidTag` → 自检每一次都必然失败。

要点：**把 `preview_id = new_id()` 提到加密之前**，两个封套都用它；后面 `PreviewRepo.insert`
处删掉重复的 `preview_id = new_id()`。

### F-1b 自检轮必须放行 `enabled=0` 的 provider

`backend/services/copilot/workflow.py` 第 273–274 行，`_load_provider_frozen` 在
`enabled=0` 时直接返回 `None`。而 §12.6 的自检目的**就是"启用之前先验证"**，
此时 provider 必然是 `enabled=0` → 自检永远发不出请求 → 落本地降级 →
`EVIDENCE_UNAVAILABLE`。

```python
if not int(p.get("enabled") or 0):
    # §12.6：自检的目的就是"启用前先验证"，此时 provider 必然 enabled=0。
    # 仅对 PROVIDER_SELFTEST 轮放行；普通业务轮仍要求 enabled=1。
    if self.turn.get("turn_kind") != TurnKind.PROVIDER_SELFTEST.value:
        return None
return p
```

> ⚠️ **必须同时补导入**，`workflow.py` 第 27 行原文只有 `ModelAnswer`：
> ```diff
> -from backend.models.copilot import ModelAnswer
> +from backend.models.copilot import ModelAnswer, TurnKind
> ```
> （第四轮报告初版曾错写成"该枚举已导入"，已在报告内更正——按初版做会抛 `NameError`。）

### F-1 验收（我已实测通过）

| 检查 | 期望 |
|---|---|
| 自检轮终态 | `SUCCEEDED` |
| 出站次数 | ≥1 |
| `GET /providers` 的 `tested_revision` | **等于 `revision`**（关键，不接受只看 HTTP 202） |
| 自检前 `PUT /enabled` | 422（这道校验要保留） |
| 自检后 `PUT /enabled` | **200**，`enabled=true` |

---

## F-2（建议）`_collect()` 给自检场景一个显式分支

`backend/services/copilot/workflow.py::_collect()` 的场景分支里**没有 `PROVIDER_SELFTEST`**
→ 该场景证据与知识必然为空。今天走模型成功路径不暴露；一旦模型返回不合规答案而走本地降级，
用户看到的会是「所选资料不可用」，而不是自检自身的原因——**错误归因会跑偏**。

```python
if scene == "PROVIDER_SELFTEST":
    # §12.6：自检不带业务资料、不做知识检索；空集合是设计使然，不是"资料不可用"。
    return [], [], {"identifiers_allowed": False}
```

并让 `_publish_local` 对自检场景报**自检自身的原因码**（模型不可达 / 输出不合规），
不要复用 `EVIDENCE_UNAVAILABLE`。

---

## F-3（建议）知识召回：两道过滤改并联

`backend/services/copilot/knowledge.py::KnowledgeBundle.search()` 目前是**串联**：
先按 `score >= MIN_SCORE(9.0)` 过滤，再用 `max(FLOOR, top1×RATIO)` 过滤。
处方设计是**并联**：绝对下限只判断"是否收录"，保留条数由相对比例决定。

实测差异（18 题黄金集，安全指标两者相同）：

| 口径 | 平均召回 | 越界拒绝 | 在域命中 |
|---|---|---|---|
| 串联（现状） | **1.61** 条 | 6/6 | 18/18 |
| 并联（建议） | **2.39** 条 | 6/6 | 18/18 |

```python
scored.sort(key=lambda x: (-x[0], ...))
if scored:
    if scored[0][0] < MIN_SCORE:      # ① 绝对下限：判定"是否收录"
        scored = []
    else:                              # ② 相对比例：决定"保留几条"
        _floor = max(MIN_SCORE_FLOOR, scored[0][0] * MIN_SCORE_RATIO)
        scored = [(s, i) for s, i in scored if s >= _floor]
```

---

## F-4（准入条件）三条回归锁

第四轮我提过：**修复必须带锁**。自检链路连续 3 轮、3 个不同根因，
每一个都是"跑一次就会暴露"的类型——而 F-4 的第一条锁如果第一轮就落地，它活不到今天。

**要求：每条锁都要证明它能在未修复的代码上失败**（临时回退修复跑一次，
把"红 → 绿"两次输出贴进开发记录）。只贴绿色不算数。

```python
# tests/copilot/test_provider_selftest_e2e.py
import json, uuid
from pathlib import Path

from backend.services.copilot.crypto import decrypt
from backend.services.copilot.selftest import admit_self_test


def test_selftest_writes_tested_revision(copilot_db, client, monkeypatch):
    """① 自检必须真正跑完并写回 tested_revision —— 覆盖 F-1a / F-1b 与历史 D40-B04。

    只断言"端点返回 202"没有任何意义：202 只代表受理。
    """
    pid = create_provider(client, name=unique_name(), endpoint_id="ep-a")

    # 未自检时必须被拒
    assert client.put(f"/providers/{pid}/enabled",
                      json={"expected_revision": 1, "enabled": True}).status_code == 422

    r = client.post(f"/providers/{pid}/self-tests",
                    json={"client_request_id": uuid.uuid4().hex,
                          "expected_provider_revision": 1})
    assert r.status_code == 202
    run_runner_until_terminal(r.json()["turn_id"])      # ← 真跑 runner，不许打桩

    prov = get_provider(client, pid)
    assert prov["tested_revision"] == prov["revision"], \
        "自检未写回 tested_revision：自检链路仍然是死的（回归 F-1）"
    assert client.put(f"/providers/{pid}/enabled",
                      json={"expected_revision": 1, "enabled": True}).status_code == 200


def test_selftest_envelope_aad_matches_preview_id(copilot_db, provider_fixture):
    """② 回归 F-1a：封套 AAD 行 ID 必须等于 preview 主键，否则必然 InvalidTag。"""
    admit_self_test(conn, identity, provider, uuid.uuid4().hex)
    row = fetch_last_preview(conn)                       # 取真实行
    assert decrypt(row["payload_envelope"], "copilot_previews", row["id"],
                   "payload_envelope", owner=row["owner_subject_id"], keyring=kr)
    assert decrypt(row["model_projection_envelope"], "copilot_previews", row["id"],
                   "model_projection_envelope", owner=row["owner_subject_id"], keyring=kr)


def test_selftest_turn_bypasses_enabled_only_for_selftest(copilot_db):
    """③ 回归 F-1b：自检轮可对 enabled=0 的 provider 发请求；普通业务轮不行。"""
    # 自检轮 → _load_provider_frozen 必须返回 provider
    # USER_QUESTION 轮 + enabled=0 → 必须返回 None
```

---

## 附：可直接复现的验证命令

```powershell
powershell -ExecutionPolicy Bypass -File docs/evidence/v1.6.4.0-uat-d/start_all.ps1
python docs/evidence/v1.6.4.0-uat4-d/r4_selftest_verify.py     # 期望 SUCCEEDED / tested_revision=1 / enable 200
python docs/evidence/v1.6.4.0-uat4-d/r4_core.py                # 阈值与出站投影
powershell -ExecutionPolicy Bypass -File docs/evidence/v1.6.4.0-uat-d/stop_all.ps1
```

---

## 附：本轮**不需要**动的部分（避免误伤）

- 出站投影链路（`_payload` / `_request_payload`）：第四轮复测**逐字节等于冻结投影**，请勿改动；
- 阈值常量本身（`MIN_SCORE=9.0`）：已达标，F-3 只调过滤顺序；
- 标记停用、重名映射、页面初始化、业务入口：均已验证通过。

---

**-智能体D** · 2026-09-16
