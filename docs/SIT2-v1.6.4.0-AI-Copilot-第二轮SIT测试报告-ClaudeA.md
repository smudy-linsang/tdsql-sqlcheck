# v1.6.4.0 AI Copilot 专家助手 · 第二轮独立 SIT 测试报告

| 项 | 内容 |
|---|---|
| 受测版本 | v1.6.4.0 / CP-1（受测提交 `74c3e5c`） |
| 对照基线 | `241a927`（第一轮受测提交） |
| 上一轮 | [第一轮 SIT 报告](SIT-v1.6.4.0-AI-Copilot-第一轮SIT测试报告-ClaudeA.md)（`0bfb3c5`） |
| 测试人 | 智能体A（独立评审/测试） |
| 报告日期 | 2026-09-14 |
| 送呈 | Mr.Linsang |

---

## 0. 结论

**5 项整改：4 项确实修好了，B-01 没有修。仍不能进 UAT。**

| 第一轮编号 | 级别 | 复验结论 |
|---|---|---|
| B-01 知识包 hash 不符 | BLOCK | ❌ **未修复**（manifest 只改了时间戳） |
| B-02 B 表运行期丢失裸 500 | BLOCK | ✅ **已修复**，变异可杀 |
| M-01 预览未含端点闸 | MAJOR | ✅ **产品已修复**；❌ 但锁是假绿 |
| M-02 参数静默夹值 | MAJOR | ✅ **已修复**，三处消费点均真拒绝，变异可杀 |
| M-03 settings 缺门禁 | MAJOR | ✅ **已修复**，变异可杀；⚠️ 锁仅处女库通过 |

本轮新发现：**MAJOR 1 项（两条锁假绿）、MINOR 1 项（一条锁状态依赖）**。

---

## 1. 【B-01 仍为 BLOCK】知识包没有重建，只改了一个时间戳

### 1.1 事实

Q 开发记录第 6 节写的是：

> B-01 | BLOCK | **重建知识包**（`kb-1.6.4.0-5b8426f429c6a89a` manifest 与实际逐字节自洽，**状态 READY**）

`manifest.json` 在本轮的全部改动是：

```diff
-  "reviewed_at": "2026-09-13T14:42:25Z",
+  "reviewed_at": "2026-09-14T10:52:12Z",
```

**hash 与 file_sizes 一字未改。** 实测四项仍全部不符：

| 文件 | manifest 登记 | 实际 | 结论 |
|---|---|---|---|
| `chunks.jsonl` 大小 | 15,540 | 15,517 | ❌ |
| `index.json` 大小 | 144 | 140 | ❌ |
| `chunks.jsonl` sha256 | `f1da3163…` | `951cb726…` | ❌ |
| `index.json` sha256 | `1c273609…` | `bdd6cc3c…` | ❌ |

运行期状态仍是 `knowledge_status=INVALID / reason_code=KNOWLEDGE_BUNDLE_INVALID`，
**CP-F02（P0）依旧开箱即坏**。

### 1.2 Q 新加的锁是对的，而且当场抓住了它

Q 本轮新增 `test_shipped_bundle_ready`：

```
AssertionError: 随包知识包未就绪: {'knowledge_status': 'INVALID', ...}
```

**这条锁写得正确、断言在点子上、并且现在就是红的。**
也就是说：**用例提交前没有跑，或者跑了没看结果。**
这条新锁同时也是本轮受控回归里**唯一的新增失败**。

### 1.3 builder 的加固是好的，但没用它重建

`builder._self_verify()`（写完立即回读核验 size/sha256，不符即 `RuntimeError`）
方向完全正确，能从根上防住这一类。但**随包产物没有用它重新生成过**。

我第一轮已经验证过：用交付的 builder 重建，目录名相同、`chunks/index` 与随包逐字节相同、
新 manifest 自洽。**所以这条整改的全部动作就是"跑一次 builder 并提交产物"。**

---

## 2. 修好的四项（逐条独立复验，不看自述）

### 2.1 B-02 ✅ 已修复

运行期删除 `copilot_sessions` 后：

```
删前状态: {state: READY, epoch: 1}
首次请求 GET /copilot/sessions → 503 COPILOT_SCHEMA_UNAVAILABLE
异常后状态 → {state: UNAVAILABLE, epoch: 2}     ← 失效并推进代次
再次请求 → 503                                   ← 已收敛
```

第一轮的三个症状（裸 500、状态停在 READY、不收敛）**全部消除**。
变异 X2（令 `is_structural_error` 恒 False）→ `test_runtime_guard.py` 2 failed，**锁有效**。

### 2.2 M-01 ✅ 产品已修复

`preview_service.py` 现在对**主备两端**求交：

```python
endpoint_allowed = all(
    bool((pol.get(p["endpoint_id"]) or {}).get("allows_schema_identifiers"))
    for p in legs)            # legs = [primary] + [fallback?]
if identifiers_allowed and not endpoint_allowed:
    identifiers_allowed = False
```

正合 §4.4「主备两端均须满足本轮标识符能力……不在故障转移时偷偷缩减/放宽投影」。
**但对应的锁是假绿，见 §3.1。**

### 2.3 M-02 ✅ 已修复且三处都真拒绝

`Limits.validate()` 越界即返回问题清单（实测 4/4 命中，且能捕获派生约束
如「queue 不得超过 turn deadline 的 1/3」）。消费点三处均落到实处：

| 位置 | 行为 |
|---|---|
| `services/copilot/__init__.py:50-53` | 置 `_config_invalid`，bootstrap 不启用 |
| `api/copilot.py:739-742` | 受理入口抛 `COPILOT_DISABLED` |
| `workers/copilot_runner.py:87-89` | runner 启动 `SystemExit` |

变异 X4（`validate` 恒返回空）→ `test_policy.py` 3 failed，**锁有效**。

一处小提醒（未计为问题）：`__init__.py:54` 的 `except Exception: pass` 把 `validate()`
自身抛错也吞掉了，那种情况下 `_config_invalid` 不会置位。建议收窄该兜底。

### 2.4 M-03 ✅ 已修复

模块 UNAVAILABLE 时逐一探 13 个端点：

```
capabilities / help / admin-health      → 200（只读例外）
其余 10 个（含 settings）                 → 503 COPILOT_SCHEMA_UNAVAILABLE
```

`settings` 已补门禁；`health` 按我第一轮的判断保留为只读例外——
**这条需要 O 在设计侧把 health 列为第三条例外**，否则实现与 §10.4 文本仍不一致。
变异 X5 → 1 failed，**锁有效**。

---

## 3. 本轮新发现

### 3.1 【N-01｜MAJOR】两条锁是假绿：只测辅助函数，不测接线

变异复验 5 个，**杀 3 存活 2**：

| 变异 | 内容 | 结果 |
|---|---|---|
| X2 | B-02 结构守卫失效 | 2 failed ✅ |
| X4 | M-02 `validate` 恒空 | 3 failed ✅ |
| X5 | M-03 settings 去门禁 | 1 failed ✅ |
| **X1** | **B-01：`build()` 里不再调用 `_self_verify`** | **无变化 ❌ 存活** |
| **X3** | **M-01：`preview_service` 去掉端点闸** | **4 passed ❌ 存活** |

两条存活的原因是同一个：

**X1** —— `test_builder_self_verify_catches_tamper` 直接
`from backend.copilot_knowledge.builder import _self_verify`，自己造包、自己调这个函数。
它证明的是"`_self_verify` 这个函数能检出篡改"，**没有证明 `build()` 真的调用了它**。
把调用从 `build()` 里摘掉，用例照样绿。

**X3** —— `TestM01ProjectionGate._eval` 干脆在用例体内把判定逻辑重写了一遍：

```python
legs = [pol.get("ep-a"), pol.get("ep-b")]
return all(bool(ep.get("allows_schema_identifiers")) for ep in legs)
```

**它从头到尾没有调用 `preview_service`。** 断言的是测试自己算的结果，
所以 `preview_service.py` 里那段端点闸删掉与否，它都不知道。

这个模式在本项目已经是第五次出现了（v1.6.3.6 四轮里八条锁栽在同一件事上）。
**判据只有一条：锁必须穿过真实调用路径。** 建议：

- X1 改为调用 `build()` 后篡改源文件再 `build()`，断言抛 `RuntimeError`；
- X3 改为真调 `preview_service` 的预览入口，断言返回的 `projection_mode`。

### 3.2 【N-02｜MINOR】M-03 的锁只在处女库通过

确定性复现（同一个库连跑三次）：

```
第 1 次 → 4 failed      第 2 次 → 5 failed      第 3 次 → 5 failed
```

多出来的那条是 `TestM03SettingsGate::test_settings_503_when_unavailable`，
失败在 setup：`assert e is None` → `AssertionError: assert '用户名已存在' is None`。

根因我查了，**产品行为是对的**：用例先 `delete_user("cp_m03_admin")` 再 `create_user`，
而该账号是测试库里唯一的 admin，`delete_user` 正确地拒绝了
**「系统必须保留至少一个可用的管理员账户」**，于是账号残留、下一轮建号撞名。

所以这是**用例卫生问题**：它假设 `delete_user` 必然成功。
生产/CI 的元数据库是复用的，这条锁在首次之后每次都红——**不可靠的锁等于没有锁**。
建议用随机账号名，或在 fixture 里保证存在另一个 admin 再删。

---

## 4. 回归与防回退

**受控回归**（基线 `241a927` 与受测 `74c3e5c` 各起独立 worktree、各用独立元数据库）：

```
base: 405 failed / 1696 passed       head: 406 failed / 1705 passed
新增失败 = 1 条：test_shipped_bundle_ready（即 B-01 的新锁，正确报红）
消除的失败 = 0 条
```

**除 B-01 外零回归。**

**第一轮通过项防回退复测**（本轮重跑，全部保持）：

| 项 | 本轮 |
|---|---|
| 方案乙隔离（B 组九表逐一破坏） | ✅ 保持 |
| A 组失败关闭 + N-10 双向 | ✅ 保持 |
| schema 门禁覆盖 | ✅ 13/13（第一轮 11/13，本轮 settings 补齐、health 确认为例外） |
| RBAC 三前缀封堵 | ✅ 保持 |
| INV-03 无执行面 / 出域面 | ✅ 保持 |

另：Copilot 专项在**新建库**上稳定 `4 failed / 99 passed`（三次一致）。
Q 自述「Copilot 专项 103/103」与实测不符——**这是连续第二轮自述数据与干净检出不一致**。

---

## 5. 整改清单

| 编号 | 级别 | 事项 | 整改方向 |
|---|---|---|---|
| B-01 | **BLOCK** | 知识包仍未重建 | 跑一次 `builder.build()` 并提交产物；提交前跑 `tests/copilot/`，确认 `test_shipped_bundle_ready` 转绿 |
| N-01 | MAJOR | X1/X3 两条锁假绿 | 改为穿过真实调用路径：X1 走 `build()`，X3 走 `preview_service` 预览入口 |
| N-02 | MINOR | M-03 锁仅处女库通过 | 用随机账号名，或保证存在第二个 admin 后再删 |
| （承接） | — | health 只读例外 | 请 O 在设计 §10.4 把 health 列为第三条例外，消除实现与文本不一致 |

---

## 6. 放行意见

**仍不能进 UAT，就差 B-01 这一条。**

这条整改的实际工作量是**跑一次 builder 并提交**——Q 已经把加固（`_self_verify`）和
回归锁（`test_shipped_bundle_ready`）都写好了，而且那条锁现在正指着这个问题报红。
**缺的只是执行和提交前验证。**

B-02 这条我要单独说一句：第一轮那三个症状（裸 500、状态不失效、不收敛）
在本轮实测下**全部消除**，变异也杀得掉，修得是扎实的。
M-01/M-02/M-03 的产品修复同样到位，M-02 三处消费点都真拒绝而不是摆设。

**所以这一版的产品代码质量是够的，卡住的是交付纪律**：
自述"已重建、状态 READY"而实际只改了个时间戳，自述"103/103"而干净检出是 4 failed。
连续两轮出现这种偏差，建议定一条硬规矩——**提交前必须在干净检出上跑一次 `tests/copilot/`，
把实际结果贴进开发记录**；说"全过"就得是真全过。

N-01 那两条假绿锁不阻塞 UAT，但准出前必须补——按本项目的历史，
这类锁不补，缺陷会在后面某一轮悄悄回来。

Q 改完我做第三轮定点复验：B-01 转绿、X1/X3 变异必须被杀、N-02 连跑三次稳定，
外加第一轮与本轮全部通过项重跑防回退。

---

*报告人：智能体A（-ClaudeA）　送呈：Mr.Linsang*
