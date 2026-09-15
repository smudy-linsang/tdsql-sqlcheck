# v1.6.4.0 AI Copilot 专家助手 · 第四轮定点复验报告

| 项 | 内容 |
|---|---|
| 受测版本 | v1.6.4.0 / CP-1（受测提交 `954da30`） |
| 对照基线 | `5e4e442`（整改要求发出时的代码，已含 Q 第一版 `d3f07bd`） |
| 整改依据 | [`FIXREQ-v1.6.4.0-AI-Copilot-N-03至N-05整改要求-ClaudeA.md`](FIXREQ-v1.6.4.0-AI-Copilot-N-03至N-05整改要求-ClaudeA.md) |
| 上一轮 | [第三轮 SIT 报告](SIT3-v1.6.4.0-AI-Copilot-第三轮SIT测试报告-ClaudeA.md)（`b049179`） |
| 测试人 | 智能体A（独立评审/测试） |
| 报告日期 | 2026-09-15 |
| 送呈 | Mr.Linsang |

---

## 0. 结论

**N-03 / N-04 / N-05 三项全部整改到位，五条变异全杀，一至三轮通过项重跑无回退，回归零新增失败。**

**维持第三轮结论：可以进 UAT。**

本轮**没有发现新的功能缺陷**，只有三处文档瑕疵（§6），都不阻塞。

另外我要说明一件事：**§5.2 里 X10 那条判据是我自己写错了**，不是 Q 没做到，见 §2.3。

| 编号 | 上一轮问题 | 本轮结论 |
|---|---|---|
| N-03 | 源文档漂移无锁；Q 第一版比对对象取错 | ✅ **已修复**，比对对象已对齐运行期加载器 |
| N-04 | LF 断言在 POSIX 上是空锁；Q 第一版只加注释 | ✅ **已修复**，AST 断言在 Linux 上确实报红 |
| N-05 | 多包并存按名字取 `candidates[-1]`，静默加载旧内容 | ✅ **已修复**，失败关闭 + 运维可见 |

---

## 1. §5.1 三条锁全绿

干净检出、同一数据库连跑三次（第 1 次为新建库，第 2、3 次不清空）：

```
run 1: 114 passed, 16.36s
run 2: 114 passed, 15.29s
run 3: 114 passed, 15.03s
```

单独判定：

```
test_sources_rebuild_matches_shipped      PASSED
test_builder_has_no_translating_write     PASSED
test_bundle_dir_is_unambiguous            PASSED
```

114 = 第三轮的 111 + 本批 3 条新锁，与 Q 自述的 114/114 一致。

---

## 2. §5.2 五条变异全杀

| 变异 | 注入 | 结果 | 打红条目 |
|---|---|---|---|
| **X6** | 改 `sources/01_user_guide_core.md` 不重建 | ✅ 被杀 | `test_sources_rebuild_matches_shipped` |
| **X7** | 撤掉 builder 三处 `newline="\n"` | ✅ **被杀（Linux 上）** | `test_builder_has_no_translating_write` |
| **X8** | `_resolve_bundle_dir` 改回 `candidates[-1]` | ✅ 被杀 | `test_bundle_dir_is_unambiguous` |
| **X9** | 去掉 `load()` 的内层 `try` | ✅ 被杀 | `test_bundle_dir_is_unambiguous` |
| **X10** | 随包目录再放一个漂移包，不改任何代码 | ✅ 被杀 | 4 条（见 §2.3） |

### 2.1 X7 是本轮最关键的一条

这条变异在第三轮和 Q 的第一版 `d3f07bd` 上都是**存活**的（Linux 上 20 passed）。本轮：

```
AssertionError: builder 存在可能翻译换行的落盘调用（Windows 上会写出 CRLF，
导致 manifest 摘要与存仓字节不符——B-01 根因）：
第144行 write_text 未显式 newline='\n'；第150行 …；第171行 …
```

三处全部点名到行号，**在 Linux 上报红**。B-01 那个"Windows 上自洽、Linux 上才暴露"的平台不对称口子，到这一轮才算真正堵上。

### 2.2 X9 的判别点正确

X9 防的是"改对了但被外层 `except Exception` 吞掉"。实测打红的是这一句：

```
AssertionError: assert 'KNOWLEDGE_BUNDLE_INVALID' == 'KNOWLEDGE_BUNDLE_AMBIGUOUS'
```

也就是说去掉内层 `try` 之后，状态确实退化成了泛化的 `INVALID`，而锁把两者区分开了。这条不是形式主义，它真的能分辨。

### 2.3 X10 的判据是我写错的，要更正

我在整改要求 §5.2 里写的是：

> **X10** … | **N-03 与 N-05 两条锁必须同时报红**

**这条要求是错的。** N-05 的锁（`test_bundle_dir_is_unambiguous`）在 `tmp_path` 里自己造两个包、自己 `monkeypatch` 环境变量，完全不受仓内目录影响——所以在 X10 下它**理应保持绿**，那正说明失败关闭生效了。要求它跟着报红是我没想清楚。

X10 的实测结果是 **4 条锁报红**，全部是走 `KnowledgeStore().load()` 的：

```
FAILED test_shipped_bundle_ready
FAILED test_sources_rebuild_matches_shipped
FAILED test_store_ready_and_search
FAILED test_rule_id_exact_boost
AssertionError: 随包知识包未就绪: {'knowledge_status': 'INVALID', 'bundle_id': '',
                                  'reason_code': 'KNOWLEDGE_BUNDLE_AMBIGUOUS'}
```

这正是我设 X10 想要的效果——**锁和运行期看的是同一个包，两包并存立刻被拦住**。

Q 在开发记录里如实写了「X10 → N-03 红（load 返回 AMBIGUOUS）+ **N-05 绿（证明失败关闭）**」，
没有为了对上我的判据去改锁，也没有含糊过去。这个处理是对的，**判据错在我，不在他**。

---

## 3. §5.3 防回退重跑（第一至三轮全部通过项）

### 3.1 方案乙逐表隔离 —— 9/9

B 组 9 张表逐一 DROP：结构验收 9/9 全部检出；6 张有读端点的全部 503 +
`COPILOT_SCHEMA_UNAVAILABLE` + READY→UNAVAILABLE + epoch 自增；核心功能全程正常；
恢复结构后 `/copilot/sessions` 回到 200、结构验收问题数归零。

### 3.2 B-02 / M-01 / M-02 / M-03

| 项 | 结果 |
|---|---|
| B-02 运行期删表 | 503 + `COPILOT_SCHEMA_UNAVAILABLE`，状态 READY/1 → UNAVAILABLE/2，再次请求仍 503 ✅ |
| M-01 八种投影组合 | 8 passed ✅ |
| M-02 越界参数 | 4 项全部返回拒绝清单而非夹值 ✅ |
| M-03 端点门禁 | 12/12 正确（capabilities / help / copilot-admin health 保持 200 只读例外，其余 9 个 503）✅ |

> 第三轮我这条 B-02 复验曾误报 ❌，原因是脚本里 `mysql` 没带口令、DROP 静默失败。
> 本轮已修正探针，结果如上。

### 3.3 N-10 双向锁（子进程隔离）

| 方向 | 结果 |
|---|---|
| 方向一 DROP `metadata_audit_jobs` | ✅ 自愈重建，QC-DEFECT-07 行为保留 |
| 方向二 DROP `copilot_subjects` | ✅ `MigrationError`，**未被静默重建**（核对为 0） |
| 方向二(b) DROP `copilot_runtime` | ✅ 同上 |

### 3.4 RBAC / INV-03 / 账号生命周期

| 路径 | admin | developer | auditor |
|---|---:|---:|---:|
| `/api/v1/copilot/sessions` | 200 | 200 | **403** |
| `/api/v1/copilot-admin/providers` | 200 | **403** | **403** |
| `/api/v1/copilot-audit/events` | 200 | **403** | 200 |

- **INV-03**：`requests` / `urllib` / `socket` / `subprocess` / `eval(` / `exec(` / `os.system` /
  `pickle.loads` 全量扫描**零命中**；出网唯一通道仍是 `httpx.AsyncClient(trust_env=False, follow_redirects=False)`。
- **B 组全灭期间**：建用户 / 登录 / 重置口令 / 删用户全部正常，12 条核心端点全 200，恢复后 Copilot 收敛。

### 3.5 第二、三轮变异复杀

| 变异 | 结果 | 打红条目 |
|---|---|---|
| X1 摘除 `build()` 自校验 | ✅ 被杀 | 2 failed（两条参数化） |
| X2 结构错误分类恒 False | ✅ 被杀 | 2 failed |
| X3A 整条端点闸失效 | ✅ 被杀 | 4 failed（primary/fallback/both-deny + no-route） |
| X3B 只校验主路 | ✅ 被杀 | 1 failed（恰为 fallback-deny） |
| X3C 部署闸失效 | ✅ 被杀 | 1 failed（恰为 deploy-deny） |
| X3D 授权闸失效 | ✅ 被杀 | 1 failed（恰为 grant-deny） |
| X4 `Limits.validate` 恒空 | ✅ 被杀 | 3 failed |
| X5 摘除 settings 门禁 | ✅ 被杀 | 1 failed |

杀伤面与第三轮逐条一致，**没有任何一条锁在本轮衰减**。

---

## 4. §5.4 回归

| | 失败 | 通过 | 跳过 | 错误 |
|---|---:|---:|---:|---:|
| base `5e4e442` | 403 | 1717 | 86 | 82 |
| head `954da30` | 403 | **1719** | 86 | 82 |

逐条求集合差（两侧各 485 条失败/错误条目）：

```
新增（base 通过 → head 失败）：（空）零新增 ✅
修复（base 失败 → head 通过）：0
```

失败集合**完全一致**。多出的 2 条通过就是 N-04、N-05 两条新锁。

---

## 5. §5.5 改动面（红线核对）

本批整改累计（`6b58cfa` → `954da30`，含 Q 第一版 `d3f07bd`）只动了 4 个文件：

```
backend/copilot_knowledge/builder.py        ← 仅新增 3 行注释，零代码改动（已逐行核对）
backend/services/copilot/knowledge.py       ← 红线允许的那一处（包目录解析 + load 内层 try）
deploy/README.md                            ← §1.4 知识包重建流程
tests/copilot/test_knowledge_output.py      ← 三条锁
```

`services` 其余部分、`api`、`workers`、`schema`、`models`、`main.py` **零改动**，
119 条审核规则、元数据审核、网关、门禁一行未碰。**完全符合红线要求。**

---

## 6. 本轮新做的独立检查

整改要求里没写、我自己加做的两项。

### 6.1 N-05 的次生灾害面（专项）

失败关闭是对的，但"关得太狠"同样是风险——所以我单独验了两包并存时到底伤到哪。

```
知识包根目录含包数: 2

运维可见性（两个免闸端点）
  /copilot/capabilities   200  knowledge={'knowledge_status': 'INVALID',
                                          'reason_code': 'KNOWLEDGE_BUNDLE_AMBIGUOUS'}
  /copilot-admin/health   200  knowledge={同上}

Copilot 其余 7 个端点          全部 200，零 5xx
主产品 6 个核心端点            全部 200，零 5xx
删掉多余的包后                 load() = READY，自动恢复
```

**结论：只降级知识检索能力，Copilot 其余功能与主产品零受损，运维在免闸端点上能看到准确原因**
（不是泛化的 MISSING，而是点名 `KNOWLEDGE_BUNDLE_AMBIGUOUS`），删掉多余包即自动恢复。

> 这一项我第一次跑出来是 `MISSING` + 空 reason_code，差点当成缺陷报上来。
> 追下去是我自己的探针问题：`TestClient(app)` 不用 `with` 就不会触发 FastAPI 的 lifespan，
> 而 `copilot_bootstrap_check()`（知识包唯一的加载点）挂在 lifespan 里，所以 store 压根没加载过，
> 看到的是构造函数的初始值。修正探针后即为上表结果。**产品没问题，是我测错了。**

### 6.2 `_AmbiguousBundle` 的逃逸面

新异常只在 `knowledge.py` 内部一处抛、一处接，全仓无第二个调用点：

```
knowledge.py:64   class _AmbiguousBundle
knowledge.py:217  bdir = self._resolve_bundle_dir(root)     ← 唯一调用点
knowledge.py:218  except _AmbiguousBundle as amb            ← 就地捕获
knowledge.py:271  raise _AmbiguousBundle(...)
```

没有任何路径能让它冒到 API 层变成 500。

### 6.3 部署手册第 2 步的命令实测可跑通

```
$ python3 -m backend.copilot_knowledge.builder --sources backend/copilot_knowledge/sources \
      --approved-by "Mr.Linsang" --expires-at 2027-09-13T00:00:00Z
built: .../backend/copilot_knowledge/kb-1.6.4.0-5b8426f429c6a89a
重建后仍只有一个包，知识包用例 22 passed
```

内容未变 → bundle_id 不变 → 覆盖同一目录 → 不会触发 N-05，流程自洽。

---

## 7. 三处文档瑕疵（不阻塞，建议顺手清掉）

### 7.1 `builder.py` 里那段 N-04 注释已经过时

`d3f07bd` 留下的注释现在说的是：

> 但 N-03 的重建比对锁会在 Windows 上……构成有效兜底。

这是 Q 第一版的论证，我当时否掉了；现在真正的兜底是
`test_builder_has_no_translating_write`。注释指向了错误的对象，下一个人照着读会走偏。
建议改成指向新锁。

### 7.2 `deploy/README.md` 第 4 步的锁名写错

正文里写的是 `test_sources_rebuild_matches_shipped`（正确），但最后一行注释写成了
`test_shipped_bundle_matches_sources`——那是我整改要求里的命名，仓里没有这个测试。

### 7.3 开发记录章节编号错位

新增的第三轮小节编为 `## 7`，原第二轮小节改成了 `## 8`，但 §8 的子标题仍是
`### 7.1` ~ `### 7.5`；而且时间上第三轮（§7）排在了第二轮（§8）前面。
这份记录以后是要当审计链路看的，编号错位会很难读。

---

## 8. 一个要写清楚的作用域说明（不是缺陷）

N-05 的闸是**加载期的闸，不是运行期看门狗**。`store.load()` 全仓只有一处调用，
在 `copilot_bootstrap_check()` 里，挂在 FastAPI 的 lifespan 上——也就是**进程启动时跑一次**。

所以：进程已经把包加载好之后，有人往目录里再丢一个包，**正在跑的 Web 进程不会立刻变
AMBIGUOUS，要等重启才会拦**。

这跟知识包其它状态（MISSING / INVALID / STALE）的处理方式是一致的，设计上说得通，
我不认为是缺陷。但运维手册和 UAT 交接里应该写明这一点，免得现场换完包发现"怎么没报错"
而误以为闸没生效。

---

## 9. 放行意见

**维持第三轮结论：可以进 UAT。**

理由：

1. 三项整改逐条独立复验到位，不是表面修复。
2. 五条变异（X6–X10）全杀，判别点精确；其中 X7 是第三轮和 Q 第一版都存活的那条，本轮在 Linux 上真正被杀。
3. 一至三轮全部通过项重跑无一回退，第二、三轮八条变异复杀，杀伤面与上轮逐条一致。
4. 回归失败集合与基线完全一致，零新增。
5. 改动面只有 `knowledge.py` 一处产品代码 + 3 行注释，完全踩在红线内。
6. N-05 的次生灾害面我另做了专项：只降级知识检索，主产品零受损，运维能看到准确原因，且可自动恢复。

### 9.1 UAT 前的收尾

| 项 | 责任方 | 状态 |
|---|---|---|
| §7.1–7.3 三处文档瑕疵 | Q | 待清，不阻塞 |
| §8 的加载期作用域写进运维手册 | Q | 建议一并补 |
| 设计 §10.4 health 只读例外订正 | O | **待办**，UAT 前应闭合 |
| 四轮 SIT 报告 + 开发记录 + RevD 冻结记录 | 已齐 | ✅ |

### 9.2 UAT 必须覆盖、四轮 SIT 一次都没真跑过的

沿用第三轮的四条，一条不减：

1. **真实模型接入后，`projection_mode` 与实际出站载荷的一致性抽检** —— M-01 三道闸的价值在这里兑现；SIT 只验了判定逻辑，没验真发出去的是什么。
2. **元数据库连接池与磁盘在 Copilot 并发下的占用** —— 这是 Copilot 与主产品真正共享的资源。我在设计评审阶段拿宿主机内存当依据搞错过一次，这条要实测，不要推理。
3. **知识包的实际检索质量** —— 四轮 SIT 只验证了产物完整性与"没漂移"，完全没验证答得对不对。建议备一个小黄金集，至少留个基线数。
4. **`deploy/copilot_emergency_disable.sh` 实机演练** —— 出事时能不能一键摘掉 Copilot 而不影响审核主流程，必须真跑一次，不能只看脚本语法。

---

**-ClaudeA**
