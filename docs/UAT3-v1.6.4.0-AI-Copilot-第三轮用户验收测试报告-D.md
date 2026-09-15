# v1.6.4.0 AI Copilot 专家助手 · 第三轮用户验收测试报告

| 项 | 内容 |
|---|---|
| 版本 | v1.6.4.0 / CP-1 |
| 被测提交 | `8859f7b`（Q 的"UAT第二轮3阻断+6严重+2一般全修"） |
| 上轮基线 | `79885fc`（第二轮判定 NO-GO） |
| 测试性质 | 第三轮 UAT：逐项复验第二轮 11 项修复 + 防回退 + 新缺陷 |
| 测试方 / 署名 | **智能体D**（独立于设计方 O、评审/SIT 方 A、开发方 Q） |
| 测试日期 | 2026-09-15 |
| 证据目录 | `docs/evidence/v1.6.4.0-uat3-d/`（脚本 6 份、JSON 证据 7 份、真实截图 5 张） |
| 提交对象 | Mr.Linsang |

---

## 0. 结论

**第三轮 UAT 判定：仍不通过（NO-GO），但只剩 1 项阻断。**

| 维度 | 结果 |
|---|---|
| 第二轮 11 项修复 | **真修复并验证 9 项**；**仍有 1 项阻断未闭合**；**1 项修过头**（见 R3-M01） |
| 业务问答端到端 | ✅ **首次全线打通**：`RULE_EXPLAIN`/`SQL_ADVISE`/`USAGE_HELP` 三场景全部 `SUCCEEDED` + `answer_source=MODEL` |
| 出站投影一致性 | ✅ 仍逐字节等于冻结投影（防回退成功） |
| 业务入口 | ✅ 真机可用（即时审核/在线元数据审核入口出现且点击能带入上下文） |
| 管理员启用模型 | ❌ **仍不可能**：自检从 500 变成"受理成功但轮次 `CONTEXT_CHANGED`" |
| 知识检索 | ⚠️ 越界误答已清零，但**召回被砍掉 81%**，1 道在域题变成零召回 |
| 回归 | ✅ `tests/copilot` **114 passed**、主产品/RBAC/出站安全全绿 |
| 回归锁 | ❌ **仍为零新增**（连续第三轮） |

> 本轮质量有明显跃升：前两轮"整条链路不通"，本轮**核心业务首次真正跑通**。
> 剩下的问题收敛为 1 个精确到行的死锁 + 2 个标定/一致性问题。

---

## 1. 逐项复验总表（第二轮 11 项）

| 编号 | 第二轮问题 | 本轮实测 | 判定 |
|---|---|---|---|
| **R2-B01** | 出站改投影后 `_collect()` 取不到 `source_refs`，证据链断裂 | 拆出 `_request_payload()`；三场景证据/知识正常收集，轮次 `SUCCEEDED` | ✅ **已修复** |
| **R2-B02** | runner 从不加载知识包 | `copilot_runner` 启动即 `store.load()`；`USAGE_HELP` 知识 3 条、轮次成功 | ✅ **已修复** |
| **R2-B03** | 自检端点 500，启用死锁 | 端点 500→**202 受理**，但轮次 `FAILED/CONTEXT_CHANGED` → `tested_revision` 未写 → 启用仍 422 | ❌ **未闭合（仅剩阻断）** |
| **R2-M01** | 业务入口 0 个 | 8 模块 9 处入口已接线；真机验证「生成修改建议」「让 Copilot 解读」出现且点击开抽屉 | ✅ **已修复** |
| **R2-M02** | `MIN_SCORE=0.5` 拦不住越界 | 越界误答 2/3→**0/6** ✅，但阈值 10.0 **砍掉 81% 召回**、1 题零召回 | ⚠️ **修过头（R3-M01）** |
| **R2-M03** | `local_ready()` 零调用方，标记无效 | 标记在→`DISABLED`，删标记→`READY` | ✅ **已修复** |
| **R2-M04** | 草稿版本读即自增、方法无人调用 | `editorRevision` ref + `watch(sqlInput)` 驱动；读取无副作用 | ✅ **已修复** |
| **R2-M05** | 抽屉绑定 `s.id`、新建按钮缺参 422 | 绑定改 `session_id`、按实例选择传 `scope_kind`；真机点「新建会话」无报错、下拉有值 | ✅ **已修复** |
| **R2-M06** | feedback-summary 分组口径偏差 | 已改为 `(scene, rule_id, rule_snapshot_hash)`、多规则分别计入、窄查 5001 行、分组上限 1000 | ✅ **已修复**（≥3 subject 分支未能在运行期验到，见 §6） |
| **R2-N01** | `TDSQL_SQLCHECK_DIR` 未登记 | `env.template` 已登记 `__INSTALL_DIR__`，经 systemd `EnvironmentFile` 生效 | ✅ **已修复** |
| **R2-N02** | 导出 CSP 无法复验 | 本轮有终态轮次：CSP = `default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox` | ✅ **已修复并验证** |

**统计：真修复 9 / 未闭合 1 / 修过头 1。**

---

## 2. 唯一的阻断项

### R2-B03（续）自检受理成功但轮次必失败 —— 死锁仍未解开

**现象（管理员视角）**
"建 provider → 连通性自检 → 启用"三步里，第二步现在**返回 202 并被受理**（不再 500），
但轮次随后落到 `FAILED`，`tested_revision` 始终为空，第三步照旧 422。**模型依然打不开。**

**实测链路**

| 步骤 | 结果 |
|---|---|
| `POST /providers`（唯一名） | **201** ✓ |
| `PUT /enabled`（未自检） | **422** `PROVIDER_CONFIG_INVALID` ✓（这道校验保留正确） |
| `POST /self-tests` | **202 ACCEPTED** ✓（上一轮的 500 已修好） |
| 同键重放 | **202 + `reused: true` + 同一 `turn_id`** ✓（幂等正确） |
| 轮次终态 | ❌ **`FAILED` / `error_code = CONTEXT_CHANGED`** |
| 出站次数 | **0**（根本没发模型请求） |
| `GET /providers` | `tested_revision: null` |
| `PUT /enabled`（自检后） | ❌ 仍 **422** → **死锁未解** |

**根因（精确到行）**

`copilot_runner.py:311-321` 在领取任务时做**代次冻结校验**：

```python
ready, _r, st = schema_mod.evaluate_ready(conn)
if not ready or int(st["module_schema_epoch"]) != \
        int(turn.get("module_schema_epoch") or 0):
    TurnRepo.publish_terminal(conn, turn_id, token, "FAILED",
                              error_code="CONTEXT_CHANGED",
                              error_message="模块结构代次已变化")
    return
```

而 `selftest.admit_self_test()` 把代次**硬编码成 0**：

```python
"module_schema_epoch": 0,        # ← preview 与 turn 都写 0
```

本机实测真实代次为 **1** → `1 != 0` → **每一次自检都必然被判"代次已变化"**。
这不是偶发，是**确定性失败**：自检功能自诞生起从未成功过一次。

**证据**：`r3_selftest.json`（完整链路与 `CONTEXT_CHANGED`）、
epoch 实测（`ready=True, module_schema_epoch=1`）。

**解决方案（照图施工）**

自检必须使用**当前 READY 代次**，不能写死：

```diff
--- a/backend/services/copilot/selftest.py
+++ b/backend/services/copilot/selftest.py
@@
 def admit_self_test(conn, identity, provider: dict,
                     client_request_id: str) -> dict:
     """受理 provider 自检：创建 PROVIDER_SELFTEST 内部 turn。"""
     provider_id = provider["id"]
     revision = int(provider["revision"])
+
+    # 代次必须取当前 READY 值：runner 领取时会用 module_schema_epoch 做冻结校验，
+    # 写死 0（或任何过期值）会让每一次自检都被判 CONTEXT_CHANGED 而必然 FAILED。
+    from backend.services.copilot import schema as schema_mod
+    _ready, _r, _st = schema_mod.evaluate_ready(conn)
+    if not _ready:
+        raise CopilotError("COPILOT_SCHEMA_UNAVAILABLE")
+    _epoch = int(_st["module_schema_epoch"])
@@
         "projection_mode": "PUBLIC_HELP",
         "identifier_policy_revision": "selftest",
-        "module_schema_epoch": 0,
+        "module_schema_epoch": _epoch,
     })
@@
         "rule_snapshot_hash": None,
-        "module_schema_epoch": 0,
+        "module_schema_epoch": _epoch,
         "client_request_id": idem_key,
```

**必须同时加的端到端锁**（这条锁能在 5 秒内抓住本缺陷，且能防住同类问题）：

```python
def test_provider_self_test_reaches_terminal_success(copilot_db, client, monkeypatch):
    """自检必须能真正跑完并写回 tested_revision。

    回归 R2-B03 / 第一轮 D40-B04：自检端点存在 ≠ 自检能用。
    只断言 202/404/500 都不够——必须断言 tested_revision 被写入。
    """
    pid = create_provider(client, name=unique_name(), endpoint_id="ep-a")
    assert client.put(f"/providers/{pid}/enabled",
                      json={"expected_revision": 1, "enabled": True}).status_code == 422

    r = client.post(f"/providers/{pid}/self-tests",
                    json={"client_request_id": uuid4().hex,
                          "expected_provider_revision": 1})
    assert r.status_code == 202
    run_runner_until_terminal(r.json()["turn_id"])          # 真实跑一遍 runner

    prov = get_provider(client, pid)
    assert prov["tested_revision"] == prov["revision"], \
        "自检未写回 tested_revision：自检链路仍然是死的（回归 R2-B03）"
    assert client.put(f"/providers/{pid}/enabled",
                      json={"expected_revision": 1,
                            "enabled": True}).status_code == 200
```

> 我在第一轮就把这条锁的骨架给了 Q，第二轮再次强调；**它会在第一次运行时立刻报红**。
> 这是三轮里唯一一项始终未闭合的缺陷，代价是"模型永远打不开"。

---

## 3. 修过头：知识检索阈值 10.0 砍掉 81% 召回

### R3-M01 `MIN_SCORE` 从"太低"矫枉过正为"太高"

**背景**：第二轮 `MIN_SCORE=0.5` 完全无效（越界问题照样返回 8 段）。
Q 按我"标定在 10 附近"的建议改为 **10.0**。方向正确，但**这个数值取高了**。

**18 题黄金集对照（同进程、同检索函数，只改阈值）**

| 变体 | 黄金集命中 | 平均召回 | 零召回题数 | 越界误答 |
|---|---|---|---|---|
| **A 阈值 10.0（当前随包）** | 17/18 (94%) | **1.33 条** | **1** | 0/6 |
| B 阈值 9.0 | **18/18 (100%)** | 1.61 条 | 0 | 0/6 |
| **C 9.0 + 相对保留 `max(4.0, top1×0.30)`** | **18/18 (100%)** | **2.39 条** | 0 | 0/6 |
| **D 9.0 + 领先幅度 `gap≥1.5`** | **18/18 (100%)** | **3.11 条** | 0 | 0/6 |

**为什么 10.0 恰好切错**：判别特征其实是干净可分的——

| 组 | top1 分数区间 | top1−top2 间距 |
|---|---|---|
| 在域 18 题 | **9.46** – 28.67 | 1.97 – 20.29 |
| 越界 5 题 | 0.00 – **8.38** | 0.00 – 0.69 |

分界点落在 **(8.38, 9.46)** 之间；而唯一被 10.0 误杀的在域题
「**Copilot 专家助手怎么打开？**」top1 = **9.46** —— 就差 0.54。
后果是用户问一个知识包里**明确收录**的功能问题，助手会回答"本地知识库未收录该主题"。

**证据**：`r3_n01_features.json`（逐题 top1/gap）、`r3_n01_variants.json`（四变体对照）、
`r3_n01_recall.json`（阈值扫描）、`r3_core.json`（`RULE_EXPLAIN`/`SQL_ADVISE` 知识条数实测为 0）。

**解决方案（照图施工）**

**最小改动（必做）**：`10.0` → `9.0`，一行恢复 100% 黄金集命中且越界仍为 0。

```diff
--- a/backend/services/copilot/knowledge.py
+++ b/backend/services/copilot/knowledge.py
@@
-MIN_SCORE = 10.0  # N-01（UAT D40）：阈值用黄金集标定——在域最低分与越界最高分之间的分界
+# N-01（UAT D40 / 第三轮 R3-M01）：标定自 18 题在域 + 6 题越界黄金集。
+# 在域 top1 最低 9.46、越界 top1 最高 8.38 → 分界取 9.0（10.0 会误杀 top1=9.46 的在域题）。
+MIN_SCORE = 9.0
+# 命中后的保留下限：绝对下限与相对比例取大者，避免只剩 1 条而丢失上下文
+MIN_SCORE_RATIO = 0.30
+MIN_SCORE_FLOOR = 4.0
```

**建议改动（恢复上下文完整度）**：命中后不要只留 top1，按相对比例保留：

```python
# knowledge.py: KnowledgeBundle.search 内，scored 排序之后
kept = [x for x in scored if x[0] >= max(MIN_SCORE_FLOOR, scored[0][0] * MIN_SCORE_RATIO)] \
       if scored else []
```

这样等价于上表变体 **C**：黄金集 18/18、越界 0/6、平均召回 2.39（对比当前 1.33）。

**必须加的标定锁**（否则下次改知识包又会漂）：

```python
def test_knowledge_threshold_separates_in_domain_from_out_of_scope():
    """阈值必须同时满足：在域全命中、越界全拒绝。改知识包/改阈值都会触发本锁。"""
    store.load()
    for q, must in IN_DOMAIN_GOLDEN:                 # 18 题，随知识包一起维护
        r = store.bundle.search(q, product_family="TDSQL-MySQL")
        assert r, f"在域问题零召回：{q}（阈值过高）"
        assert any(m in json.dumps(r, ensure_ascii=False) for m in must)
    for q in OUT_OF_SCOPE:                           # 越界题
        assert not store.bundle.search(q, product_family="TDSQL-MySQL"), \
            f"越界问题被误答：{q}"
```

---

## 4. 其他发现（2 项）

### R3-M02 持久停用标记只拦"显示"，不拦"受理"

**实测（标记存在 + DB `enabled=true`）**

| 检查 | 结果 |
|---|---|
| `GET /capabilities` | `mode = DISABLED` ✅（页面会告诉用户"未启用"） |
| `POST /sessions` | **201** ❌ |
| `POST /sessions/{id}/previews` | **201** ❌ |
| `POST /sessions/{id}/turns` | **202 已受理** ❌ |

即：**用户看到的是"未启用"，实际提问仍会被受理并可能调用模型**（花掉额度、产生出站）。
标记的立项目的是"立即停止新受理 + 禁止自动重启后恢复"（§16.6 第 1 条）；
现在只做到了"界面显示为停用"。

**注**：正常应急流程里脚本**同时**写了 `settings.enabled=false`，受理会被那道闸拦住。
但"标记在、开关被误开"恰恰是标记要防的场景（例如恢复旧备份、人工误操作），
此时它必须自己顶住。

**证据**：`r3_marker_admission.json`。

**解决方案（照图施工）**

把标记判据与 `settings.enabled` 一起加进**受理事务**（§11.2 伪代码第 4 步"新请求才检查开关"），
建议抽成一个复用的断言函数，避免"能力接口查了、受理路径忘了"：

```python
# backend/services/copilot/policy.py（或 authz.py）
def deployed_disabled_reason() -> Optional[str]:
    """返回部署侧强制停用原因；None 表示未强制停用。

    统一供 capabilities / admit / preview / runner 使用，避免各处口径不一致。
    """
    from backend.services.copilot import local_ready
    _lr, reason = local_ready()
    return reason if reason == "COPILOT_DISABLED" else None
```

```diff
--- a/backend/api/copilot.py
+++ b/backend/api/copilot.py
@@
     if not copilot_enabled() or not bool(settings.get("enabled", False)):
         raise CopilotError("COPILOT_DISABLED")
+    from backend.services.copilot.policy import deployed_disabled_reason
+    if deployed_disabled_reason():
+        raise CopilotError("COPILOT_DISABLED")
```

（`POST /sessions`、`POST /previews`、`POST /turns` 三处都应带该判据；
`GET` 状态类接口按 §12.7 保持可读。）

**加锁**：

```python
def test_persistent_marker_blocks_admission(tmp_path, monkeypatch, client):
    """标记存在时不得新受理（回归 R3-M02）。"""
    write_marker(tmp_path)
    copilot_bootstrap_check()
    assert client.get("/capabilities").json()["mode"] == "DISABLED"
    assert client.post("/sessions", json={...}).status_code == 503   # 或 403 COPILOT_DISABLED
```

### R3-N01 重复 provider 名称返回 500 而非可解释的 4xx

**实测**：用已存在的名称 `POST /copilot-admin/providers` → **HTTP 500**（响应体为空）。

**根因**：唯一键 `copilot_providers.uq_copilot_providers_name` 的
`pymysql.err.IntegrityError(1062)` 未在路由内映射，直接冒泡成 500。
按 §12.7 错误码闭集，这类输入冲突应归入 `INVALID_REQUEST`（422）。

**证据**：`r3_selftest.json`（`duplicate_name: {"status": 500}`）、
`web.err.log`（`Duplicate entry 'UAT2-自检主模型' for key ...`）。

**解决方案**：在 `create_provider` / `update_provider` 内先做一次名称存在性检查并抛
`CopilotError("INVALID_REQUEST", message="模型名称已存在，请换一个名称")`，
同时在外层兜底捕获 `IntegrityError` 映射为同一错误码（防止检查与写入之间的竞态）。

---

## 5. 流程问题（连续第三轮）

### R3-P01 仍然零回归锁

| 轮次 | 修复提交 | 是否触碰 `tests/` | 用例数 |
|---|---|---|---|
| 第一轮 | `79885fc` | **否** | 114 |
| 第二轮 | `8859f7b` | **否** | 114 |

三轮累计修复 27 项缺陷，**新增回归锁 0 条**。本轮的代价很直观：
`R2-B03` 是三轮里唯一始终未闭合的项，而它**恰好是我在第一轮就给出锁骨架的那一项**，
并且那条锁会在第一次运行时报红。

**建议（作为第四轮 UAT 的准入条件）**：
修复提交必须同时包含对应回归锁，且锁必须能在**未修复的代码上失败**
（Q 自证时可临时回退修复跑一次，把"红→绿"两个输出贴进开发记录）。
本报告已为每个未落实项给出可直接落地的锁代码。

---

## 6. 本轮未能完整验证的部分（如实声明）

1. **`feedback-summary` 的 ≥3 subject 展示分支**：本机只有 4 个账号，
   `uat_d_1640dev` 没有可反馈的终态轮次，凑不齐 3 个不同 subject，
   因此 `items` 始终为空（两侧 `hidden_below_privacy_threshold=2` 行为正确）。
   **实现侧已按处方落地**（分组含 `rule_snapshot_hash`、多规则分别计入、窄查与分组上限齐全），
   但"3 个主体后确实展示"这一条**未在运行期验证**。
2. **`审核规则库` 行内「解释」入口**：代码已接线（`<el-table-column>` 内 `openCopilotWith`），
   但本轮浏览器扫描未展开规则分类折叠面板，未在界面上触发到。
   **未验证 ≠ 未实现**，第四轮顺手核一次即可。
3. **真实内网模型网关**：仍用同协议受控网关（TLS 校验真实开启、Bearer 真实发送、
   报文逐字节落盘），替换的只有"供应商那一段"。
4. **systemd / Linux 应急路径**、**200 轮压力与长稳**、**真实模型黄金集答案质量**、
   **五条发布链路**：边界同前两轮，未执行。

---

## 7. 防回退结果（本轮全绿）

| 项 | 结果 |
|---|---|
| `tests/copilot/` 独立复跑 | **114 passed** ✅（与 Q 自述一致） |
| 主产品：即时审核 | `200` / `passed=true` ✅ |
| 主产品：在线元数据审核任务 | `202` → `SUCCEEDED`，**4.1 s** ✅ |
| RBAC 四角色 | admin 200/200、第二 admin 200/200、developer 200/**403**、auditor **403/403** ✅ |
| 出站安全（累计 26 次真实出站） | 含 `tools`/`functions` 的请求 **0**；全部 `Authorization: Bearer` ✅ |
| 越权访问他人会话 | `404` ✅ |
| **M01 防回退**：出站 == 冻结投影 | **逐字节相等（三场景全部 true）** ✅ |
| 浏览器：B01/B02/B05/M03/N04 | 未登录无遮罩、抽屉按需开、AI配置页三页签、模式"已启用"、抽屉会话可用 ✅ |

---

## 8. 出口判定与下一步

### 判定

**第三轮 UAT 不通过（NO-GO），但已收敛到 1 项阻断。**

- ✅ 核心业务链路**首次端到端打通**（业务轮次真实 `SUCCEEDED`、`answer_source=MODEL`）；
- ✅ 第二轮 11 项里 **9 项真修复并验证**，含最关键的两条（证据链、runner 知识包）；
- ❌ **`R2-B03` 自检死锁未闭合** → 全新部署**无法启用任何模型** → CP-F11（P0）不满足；
- ⚠️ `R3-M01` 阈值需回标 9.0（一行改动，已给出验证过的数值）；
- ⚠️ `R3-M02` 标记需补受理层判据。

**只要上面 1 个阻断 + 2 个标定项修完，v1.6.4.0 就具备进入发布门禁的条件。**
这是三轮以来第一次可以说这句话。

### 建议的整改顺序

| 批次 | 内容 | 工作量 |
|---|---|---|
| **第 1 批（阻断）** | `R2-B03`：自检代次改为当前 READY 值 + 端到端锁 | 一行 + 一条用例 |
| **第 2 批（标定）** | `R3-M01`：`MIN_SCORE` 10.0→9.0（+ 可选相对保留）+ 黄金集标定锁 | 一行/数行 + 一条用例 |
| **第 3 批（一致性）** | `R3-M02`：标记判据进受理路径（抽 `deployed_disabled_reason()` 复用）+ 锁 | 数行 + 一条用例 |
| **第 4 批（收尾）** | `R3-N01` 重名映射 4xx；补 §6 两项未验证点 | 小 |

**第四轮 UAT 我会重点复验**：
① 自检 → 启用 → 出站 全链路（**必须断言 `tested_revision` 被写入**，不接受"端点返回 202"）；
② 阈值调整后的黄金集命中率与越界拒绝率**同时**达标；
③ 标记存在时受理真的被拒；
④ 出站是否**仍然**逐字节等于冻结投影（防第三轮把 M01 改回去）；
⑤ 三项修复是否各自带锁（**准入条件**）。

---

## 9. 证据索引

目录：`docs/evidence/v1.6.4.0-uat3-d/`

| 主题 | 文件 |
|---|---|
| **核心链路（B01/B02 + M01 防回退）** | `r3_core.py`、`r3_core.json`（三场景 SUCCEEDED、出站==冻结、证据/知识条数） |
| **R2-B03 自检端到端** | `r3_selftest.py`、`r3_selftest.json`（202 受理 → CONTEXT_CHANGED → 启用仍 422） |
| **R3-M01 阈值影响** | `r3_n01_recall.py`、`r3_n01_recall.json`、`r3_n01_features.py`、`r3_n01_features.json`、`r3_n01_variants.py`、`r3_n01_variants.json` |
| **R3-M02 标记一致性** | `r3_marker_admission.py`、`r3_marker_admission.json` |
| **业务入口真机验证** | `r3_entries.py`、`r3_entries.json`、`shots/r3-entry-instant.png`、`r3-entry-metadata.png`、`r3-entry-drawer.png` |
| **M04 + 防回退回归** | `r3_m04_regression.py`、`r3_m04_regression.json` |
| **导出 CSP（R2-N02 补验）** | `r3_export_csp.py`、`r3_export_csp.json`、`export_sample_r3.html` |
| **浏览器截图** | `shots/r3-entry-rules.png`、`r3-entry-instant.png`、`r3-entry-drawer.png`、`r3-entry-metadata.png` |

### 一键复现

```powershell
# 0) 起夹具
powershell -ExecutionPolicy Bypass -File docs/evidence/v1.6.4.0-uat-d/start_all.ps1
# 1) 核心链路 + 出站投影（最重要）
python docs/evidence/v1.6.4.0-uat3-d/r3_core.py
# 2) 自检端到端（唯一阻断）
python docs/evidence/v1.6.4.0-uat3-d/r3_selftest.py
# 3) 阈值标定（三脚本依序：扫描→特征→变体）
python docs/evidence/v1.6.4.0-uat3-d/r3_n01_recall.py
python docs/evidence/v1.6.4.0-uat3-d/r3_n01_features.py
python docs/evidence/v1.6.4.0-uat3-d/r3_n01_variants.py
# 4) 标记一致性 / 业务入口 / M04+回归 / 导出CSP
python docs/evidence/v1.6.4.0-uat3-d/r3_marker_admission.py
python docs/evidence/v1.6.4.0-uat3-d/r3_entries.py
python docs/evidence/v1.6.4.0-uat3-d/r3_m04_regression.py
python docs/evidence/v1.6.4.0-uat3-d/r3_export_csp.py
# 5) 收尾
powershell -ExecutionPolicy Bypass -File docs/evidence/v1.6.4.0-uat-d/stop_all.ps1
python docs/evidence/v1.6.4.0-uat-d/prepare_uat_d40.py untrust
```

---

## 10. 给 Mr.Linsang 的一句话

三轮下来，**这一轮是第一次"功能真的跑起来了"**：业务问答端到端 `SUCCEEDED`、
出站报文逐字节等于用户在预览里看到的内容、业务入口在浏览器里点得动。
剩下的只有一个精确到行的死锁（自检代次写死为 0），加上两个标定/一致性问题——
**修完这三处，v1.6.4.0 就可以去走发布门禁了**。

同时我必须把一个流程问题再提一次：**三轮 27 项修复、零条回归锁**。
本轮唯一没修好的那项，恰好是我在第一轮就给了锁骨架的——**如果当初那条锁在，
它根本活不到第三轮**。恳请把"修复必须带锁"定为第四轮的准入条件。

---

**-智能体D**

*2026-09-15 · TDSQL-SQLCheck v1.6.4.0 AI Copilot 第三轮 UAT*
