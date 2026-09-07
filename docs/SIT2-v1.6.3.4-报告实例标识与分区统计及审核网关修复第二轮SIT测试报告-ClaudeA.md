# SIT2-v1.6.3.4 第二轮系统集成测试报告

| 项 | 内容 |
|---|---|
| 被测版本 | v1.6.3.4，`main` / `a1092d3`（第一轮 SIT 整改） |
| 对照基线 | `main` / `3887327`（施工前，第一轮已建立的失败清单） |
| 上一轮结论 | 不通过（有条件）：2 BLOCK、2 MINOR |
| 测试方 | 智能体 A |
| 测试日期 | 2026-09-07 |
| 本轮重点 | 四项整改的定点复验 ＋ **变异测试**（故意改坏被测逻辑，验证新补的用例是否真会变红） |
| **测试结论** | **通过（有条件）。2 项 BLOCK、2 项 MINOR 全部关闭；新发现 3 项 MINOR，均为用例完备性问题，不阻断进入 UAT。本轮未发现任何功能缺陷。** |

---

## 1. 结论摘要

| 上轮编号 | 状态 | 复验依据 |
|---|---|---|
| B-01 用例缺失 | **已关闭** | 4 个新用例文件、53 个测试函数；28 个编号中 **27 个为可执行用例**，PAR-21 如实声明为"需内网数据、离线套件无法闭环"；退役门禁的 skip 理由已改为指向**真实存在**的 `tests/test_v1634_secondary_partition.py（PAR-13—20）` |
| B-02 请求编号串台 | **已关闭** | 端到端 3/3 一致，且访问日志记录的就是同一个编号 |
| M-01 假截断告警 | **已关闭** | 失败分支改 `truncated=False`；变异复现证明该契约有锁 |
| M-02 名称冒充 | **已关闭** | `conn_name` 不再写 `host:port`；Q 确认该分支可达（即席连接），改为名称位留空、endpoint 仅作任务名 token |

**本轮新发现（全部为 MINOR，均由变异测试暴露）**

| 级别 | 编号 | 问题 |
|---|---|---|
| MINOR | S2-01 | **DML-16 真值表少了唯一能隔离 `status == RESOLVED` 的那一行**。我把该条件从派生属性里删掉，22 个 R043 用例**全绿** |
| MINOR | S2-02 | **二级分区用例集里没有任何一条广播表用例**。我让广播标记失效（`NOSHARDKEY_ALLSET` 永不匹配），10 个用例**全绿**——而这恰恰是工程师附件点名警告的那个误判 |
| MINOR | S2-03 | **5 个新增网关 API 用例是顺序相关的**：单独跑全过，全量套件里因既有的 `AUTH_ENABLED` 泄漏被 401 打挂。作为 B-02 的回归锁，顺序相关的锁不可靠 |

**一句话**：整改质量很好，两个阻断项都真修了、也都留下了能真正变红的锁；剩下三条是"锁没盖全"，功能本身我这轮一个问题都没打出来。

---

## 2. 变异测试结果（本轮核心）

第一轮我承诺过："我会做变异测试——故意改坏被测逻辑，验证对应用例真的会红。"用例全绿不等于用例有效，只有它在代码坏掉时会红，才算锁。

### 2.1 有效变异 10 个，捕获 8 个

| 变异 | 改动 | 结果 |
|---|---|---|
| M1 | 闭集删掉 `RENAME` | ✅ 红：`test_dml18_closed_set_integrity` |
| M2 | R043 去掉 `status == RESOLVED` 条件 | ✅ 红：**9 个用例**（DML-11/12/13/17/18/19 全线） |
| M3 | 候选集从 `C = L∪P∪B` 退回仅分片集 `S` | ✅ 红：**6 个用例**（PAR-13/14/15/17/19/20） |
| M4 | 目录枚举失败改判 `COMPLETE` | ✅ 红：PAR-16 两条 |
| M5 | 回退 B-02 请求编号修复 | ✅ 红：GW-C1、GW-C2（429 与 413 两条路径都有锁） |
| M6 | 回退 M-01（失败时 `truncated=True`） | ✅ 红：PAR-16 两条 |
| M9 | 并发校验放行 2 | ✅ 红：`test_gw_c4_concurrent_is_fixed_one` |
| M10c | 删除 `analysis+10 < processing` 校验 | ✅ 红：`test_gw_t1_analysis_processing_inequality` |
| **M11b** | 派生属性去掉 `status == RESOLVED` | ❌ **22 个 R043 用例全绿** → S2-01 |
| **M12b** | 广播标记 `NOSHARDKEY_ALLSET` 失效 | ❌ **10 个用例全绿** → S2-02 |

**变异捕获率 8/10。**M2 与 M3 的捕获面尤其宽（9 个、6 个用例同时变红），说明这两条最关键的契约锁得很扎实。

### 2.2 被我排除的无效变异（避免误报）

变异测试最容易犯的错，是把"等价变异"或"没打中的变异"当成用例缺口。以下 4 个我逐个查证后**排除**，没有写进缺陷：

| 变异 | 初判 | 查证结论 |
|---|---|---|
| M7 锁 `release()` 去掉 `_acquired` 判断 | 疑似缺口 | **等价变异，非缺口**。`try_acquire` 在抢锁失败时已 `os.close(fd)` 并置 `self._fd = None`（`gateway_upload_lock.py:120-127`），实测非持槽者 `_fd` 恒为 `None`，故 `not self._acquired` 与 `_fd is None` 在**一切可达状态下等价**。用例断言的是可观测行为，没有问题 |
| M10/M10b 超时链不等式 | 疑似缺口 | **变异没打中**。`analysis + 10 < processing` 在 `config.py` 里出现 3 次，前两次在 **docstring(205 行) 与注释(249 行)**，我的正则改的是文档不是代码。改真正的条件式（`if analysis + 10 >= processing:`）后用例**立刻变红**（见 M10c） |
| M11 属性恒 True | 疑似缺口 | **变异打错行**：正则命中的是 `dt is None` 的提前返回，不是主判定。重做为 M11b 才有效 |
| M12 `BROADCAST` 改名 | 疑似缺口 | **等价变异**：只改了枚举值字符串 `DIST_BROADCAST = "BROADCAST"`，判定逻辑不变。重做为 M12b（让匹配标记失效）才有效 |

**所有变异均已还原**，工作区 `git status` 干净、`git diff HEAD` 为空。

---

## 3. 四项整改的定点复验

### 3.1 B-02 请求编号串台——已关闭

修复位于 `backend/middleware.py`，优先复用外层已建立的请求上下文 ID：

```python
rid = (scope.get("state") or {}).get("request_id")
if rid:
    return str(rid)[:64]
```

与我给的方案一致。**端到端实测（真实 HTTP，占锁触发 429）**：

| 次数 | 响应头 `x-request-id` | 响应体 `detail.request_id` | 一致 |
|---|---|---|---|
| 1 | `58e16c94eb3e4b37` | `58e16c94eb3e4b37` | ✅ |
| 2 | `4722908507c4481e` | `4722908507c4481e` | ✅ |
| 3 | `a171f159220e46a5` | `a171f159220e46a5` | ✅ |

访问日志记录的正是 `[a171f159220e46a5]`——**用户截图上的编号，现在能在日志里查到了**。回归锁经 M5 验证有效，且 429/413 两条路径各有一条。

### 3.2 B-01 用例缺失——已关闭

| 文件 | 测试函数 |
|---|---|
| `tests/test_v1634_r043_dml_target.py` | 22 |
| `tests/test_v1634_secondary_partition.py` | 10 |
| `tests/test_v1634_report_context.py` | 8 |
| `tests/test_v1634_gateway.py` | 13 |
| 合计 | **53，单独运行全部通过** |

28 个编号中 27 个落为可执行用例；**PAR-21（真实容量核算）如实声明为离线套件无法闭环**，边界写在文件 docstring 里——这个处理是对的，没有拿模拟数据冒充实测。

退役门禁的 skip 理由已改写为指向真实文件（第一轮我指出它引用了并不存在的 PAR-01~21）：

```text
一致性由 DETAIL-v1.6.3.4 §4、tests/test_v1634_secondary_partition.py（PAR-13—20）
与既有 118 项 G14 回归守护
```

### 3.3 M-01 假截断告警——已关闭

失败分支改为 `return set(), SP_STATE_FAILED, rows_consumed, False`，并写明理由（"该分支一行都没读到，既没触发行护栏也没耗尽预算"）。M6 变异证明 PAR-16 两条用例会因回退而变红。

### 3.4 M-02 名称冒充——已关闭

Q 确认该分支**确实可达**（即席连接：活跃但未入注册表），处理方式比我建议的更清楚——把两件事拆开：

* `conn_name`（名称字段）取不到就**留空**，报告显示走 `report_context`，降级为"未命名即席连接"；
* 任务名另用 `_task_inst_token`，允许用 `host:port` 作标识 token，**不占用名称位**。

符合设计 §3.2"不把端口冒充连接名称"。

### 3.5 回归对照：无功能性新增失败

同一基线、同一套件、独立元数据库对照，`FAILED/ERROR/SKIPPED` 清单逐行 diff 共 7 行差异：

* **2 行**：两条门禁 skip 理由改写（内容变化，非新增失败）
* **5 行**：新增网关用例在全量套件中被 401 打挂 → 即 S2-03，根因是**既有污染**（见 §4.3）

**除此之外没有任何新增失败**，Q 的整改没有碰坏既有功能。

## 4. 本轮新发现

### S2-01（MINOR）DML-16 真值表少了唯一能隔离 `status == RESOLVED` 的那一行

**问题**

`test_dml16_truth_table` 现有 6 行断言：

```python
assert _mk("UPDATE",  "RESOLVED",       True)  is True
assert _mk("DELETE",  "RESOLVED",       True)  is True
assert _mk("UPDATE",  "RESOLVED",       False) is False
assert _mk("UPDATE",  "UNKNOWN",        None)  is False
assert _mk("NOT_DML", "NOT_APPLICABLE", False) is False
assert _mk("SELECT",  "RESOLVED",       True)  is False
```

派生属性的判定是三个条件的合取：

```python
return (dt.status == "RESOLVED"
        and dt.statement_kind in ("UPDATE", "DELETE")
        and dt.is_multi_table is True)
```

逐行看这 6 行**分别是被哪个条件挡住的**：

| 断言行 | 真正起作用的条件 |
|---|---|
| 3 行 True | 三条件全真 |
| `("UPDATE","RESOLVED",False)` | `is_multi_table` |
| `("UPDATE","UNKNOWN",None)` | **`is_multi_table is None`**——注意不是 status！ |
| `("NOT_DML","NOT_APPLICABLE",False)` | kind ＋ multi |
| `("SELECT","RESOLVED",True)` | kind |

**没有任何一行是靠 `status` 挡住的。**第 4 行看似在测 UNKNOWN，但它的 `is_multi_table` 传的是 `None`，属性已经被第三个条件拦下了，status 条件是不是还在都无所谓。

**证据**：我把 `dt.status == "RESOLVED"` 从属性里删掉（M11b），**22 个 R043 用例全绿**。

**影响**

有限但真实。R043 规则体现在直接读 `dml_target`，不读这个兼容属性，所以属性坏掉今天不会造成误报。但设计 §5.2 明确要求"属性完整派生于三条件"、DML-16 明确要求"属性真值表"，而这个真值表实际上没有覆盖三分之一的判定逻辑。

**整改（一行）**

在 `test_dml16_truth_table` 中补入唯一能隔离 status 的那一行：

```python
# ★ 隔离 status 条件：kind 与 multi 都满足，仅 status 非 RESOLVED，必须为 False。
# 缺这一行，删掉属性里的 status 条件不会有任何用例变红（SIT2 变异 M11b 已证）。
assert _mk("UPDATE", "UNKNOWN", True) is False
assert _mk("DELETE", "UNKNOWN", True) is False   # DELETE 侧同理
```

补后请自行做一次变异复验：临时删掉属性里的 `dt.status == "RESOLVED"`，确认这两行会红。

### S2-02（MINOR）二级分区用例集里没有任何一条广播表用例

**问题**

`tests/test_v1634_secondary_partition.py` 全文检索 `broadcast` / `广播` / `noshardkey_allset`——**0 命中**。10 个用例覆盖了四层调度、目录状态机、视图与物理子表排除，唯独漏掉广播表。

**证据**：我把广播标记改成永不匹配（`_BROADCAST_MARKER = "__NEVER_MATCH__"`，M12b），**10 个用例全绿**。

而这个分支被破坏的后果是明确的——`shardkey=noshardkey_allset` ＋ `PARTITION BY` 的**广播分区表会被误计为二级分区主表**。工程师附件第 4.2 节原文就在警告这一条：

> 广播表必须首先判断并排除，否则 `shardkey=noshardkey_allset` 的广播分区表会被误归类为二级分区表。

设计 §4.2 结论表里也有独立一行（"广播｜无或分区结构｜不计主表"）。

**影响**

功能目前是对的——我第一轮和本轮都实测过 `广播+分区 → NOT_SECONDARY/BROADCAST`。但这条**唯一会直接把 Mr.Linsang 要的那个数字改大**的误判路径，现在没有任何用例守着。

**整改**

在 `tests/test_v1634_secondary_partition.py` 中补一条识别器级用例（不需要走采集流程，成本很低）：

```python
def test_par_broadcast_precedence_not_counted_as_main():
    """广播优先：shardkey=noshardkey_allset + PARTITION BY 不得计入二级分区主表。
    工程师附件 §4.2 与 DETAIL §4.2 结论表；SIT2 变异 M12b 证明此前无锁。"""
    ddl = ("CREATE TABLE `t` (`id` int) ENGINE=InnoDB shardkey=noshardkey_allset\n"
           "PARTITION BY RANGE (id) (PARTITION p0 VALUES LESS THAN (2))")
    e = classify_logical_ddl(ddl, "db", "t")
    assert e.state == "NOT_SECONDARY"
    assert e.distribution == "BROADCAST"
```

**建议再补一条采集级用例**，断言库内含广播分区表时 `main_tables` 不把它算进去（这才是与 Mr.Linsang 的数字直接相关的那一层）。

### S2-03（MINOR）5 个新增网关 API 用例顺序相关，全量套件中变红

**问题**

| 运行方式 | 结果 |
|---|---|
| `pytest tests/test_v1634_gateway.py` 单独 | 13 passed ✅ |
| 与既有 `test_gateway_log.py` 同跑 | 19 passed ✅ |
| **全量套件 `pytest tests/`** | **5 failed**（`gw_c1_busy_returns_429...`、`gw_c1_busy_is_non_blocking`、`gw_c2_oversize_413...`、`gw_c4_capabilities...`、`gw_t2_capabilities_match_config`） |

失败原因统一是 `assert 401 == 200`。

**根因（不是 Q 引入的）**

多个既有用例文件把 `os.environ["AUTH_ENABLED"] = "true"` 打开后不还原（`test_fix_user_issues.py:24`、`test_v2_rbac_matrix.py:14`、`test_v2_sit.py:20` 等）。这是**本沙箱既有的全量套件污染**——`test_v2_auth` 在**施工前基线**里就失败 9 次，与 Q 无关。

但新增的 `test_v1634_gateway.py` **完全没有处理鉴权**（全文无 `AUTH_ENABLED`、无 Authorization 头），于是只要排在泄漏者之后就被 401 打挂。

**影响**

这 5 条正是 **B-02 的回归锁**。一条只在单独运行时才绿的锁，在 CI 全量跑时是红的——时间一长，要么被人忽略，要么被人删掉，锁就白留了。

**整改（每个 API 用例一行）**

在 `test_v1634_gateway.py` 涉及 `TestClient` 调用的用例中显式钉住鉴权开关，不依赖运行顺序：

```python
@pytest.fixture(autouse=True)
def _pin_auth(monkeypatch):
    """不依赖套件运行顺序：显式钉住 AUTH_ENABLED，避免被其他用例的环境泄漏影响。
    （SIT2 S2-03：本文件用例单独跑全绿、全量套件被 401 打挂。）"""
    monkeypatch.setenv("AUTH_ENABLED", "false")
```

`monkeypatch.setenv` 会在用例结束时自动还原，**不会把污染再传给下一个用例**。补后请以 `pytest tests/` 全量跑一次确认这 5 条转绿。

**顺带建议（不属于本次整改范围）**：既有那几个文件的 `AUTH_ENABLED` 泄漏是 400+ 条失败的根源之一，值得单独立项治理，但**不要塞进 v1.6.3.4** ——那会扩大本次爆炸半径。

## 5. 整改清单与放行意见

| 编号 | 级别 | 一句话 | 工作量 |
|---|---|---|---|
| S2-01 | MINOR | DML-16 真值表补 `("UPDATE","UNKNOWN",True) is False` 及 DELETE 同理一行 | 2 行 |
| S2-02 | MINOR | 补广播优先用例（识别器级必补，采集级建议补） | 约 10 行 |
| S2-03 | MINOR | `test_v1634_gateway.py` 加 `autouse` fixture 钉住 `AUTH_ENABLED` | 4 行 |

**放行意见：可以进入 UAT。**

理由：三项都是**用例完备性**问题，不是功能缺陷。本轮我对四个需求的功能逻辑做了变异级别的施压，**没有打出任何一个功能问题**；上一轮的两个阻断项都真修了，并且都留下了经变异验证有效的锁。这三条建议**与 UAT 并行修**，不必卡在这里——但请在 UAT 结束前闭环，因为 S2-02 守的那条路径直接关系到 Mr.Linsang 要的那个数字。

补完后我只做一次**变异复验**（把对应条件删掉，确认新用例会红），不需要再开一轮完整 SIT。

## 6. 向 Mr.Linsang 汇报

**上一轮那两个卡住的问题，Q 都真修好了，我也验过了。**

那个"请求编号对不上"的问题——用户看到的编号和运维日志里的编号是两个号——现在三次实测完全一致，而且日志里记的就是用户看到的那个。以后有人拿着截图来报障，查得到了。

那 28 个缺失的测试用例也补齐了，53 个测试。**更重要的是我验了它们是不是"真锁"**：我故意把代码改坏 10 处——把规则的判定条件删掉、把统计的候选范围缩回旧口径、把失败状态改成"完成"、把并发限制放开到 2——**其中 8 处立刻被测试抓住变红**，有两处改动一下子就让 9 个、6 个测试同时报警。这说明补的不是摆设。

**这轮我没打出任何功能问题。**四个需求的逻辑我用变异的方式反复施压，都扛住了。

**剩下三条小问题，都是"锁没盖全"，不是功能坏了**，一共改十几行，建议和 UAT 并行做。其中有一条我想单独提一句：**二级分区那套测试里，一条广播表的用例都没有**。广播表这个东西，工程师给的材料里特意警告过——如果不先把它排除掉，那种"广播的分区表"会被错算成二级分区主表，**您页面上那个数字就会偏大**。功能目前是对的（我实测过两轮），但这条路径现在没有测试守着，我要求补上。

## 7. 测试边界声明

1. 本轮**未向仓库提交任何代码改动**。变异测试全部为"改—跑—`git checkout` 还原"，结束后 `git status` 干净、`git diff HEAD` 为空，已核验。
2. 变异测试中我排除了 4 个无效变异（2 个等价变异、1 个打错行、1 个只改了 docstring），**没有把它们写成缺陷**；每个排除都在 §2.2 给了查证依据。
3. **本轮仍未接触内网真实 TDSQL**。PAR-21 真实容量核算、71 MiB 真实日志容量门禁、200 MiB 边界、子进程 TERM/KILL 回收路径、浏览器端真实渲染——**这些第一轮就没覆盖，本轮同样没有覆盖**，不因整改通过而推定通过。D03 按设计仍只能给"范围受限通过"。
4. 全量套件中既有的 400+ 条失败为本沙箱历史污染（施工前基线同样存在），本轮未记入 Q，也未尝试治理。

---

测试人：智能体 A（ClaudeA）
被测版本：v1.6.3.4 `main@a1092d3`
提交给：Mr.Linsang
