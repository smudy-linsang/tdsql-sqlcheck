# v1.6.4.0（G 改进后）· 第二轮独立测试与质检报告

| 项 | 内容 |
|---|---|
| 受测版本 | v1.6.4.0（受测提交 `06ff645`） |
| 对照基线 | `21e93c3`（UAT 出口）；整改前 `0b3e96f` |
| 上一轮 | [第一轮质检报告](QC-v1.6.4.0-G改进版独立测试与代码质检报告-ClaudeA.md) |
| 测试人 | 智能体A（独立评审/测试） |
| 报告日期 | 2026-09-19 |
| 送呈 | Mr.Linsang |

---

## 0. 结论

**仍不能发布。但性质变了：上一轮是"设计上开了口子"，这一轮是"代码跑不起来"。**

**`/copilot/chat` 现在 100% 返回 500——Copilot 对话页彻底不能用了。**
浏览器实测：每问一句都是「⚠️ 请求未能成功返回，请稍后再试。」

原因是整改时引入的两处 `NameError`，都是 `pyflakes` 一跑就能看出来的：

```
backend/api/copilot.py:1143   undefined name 'config'
backend/api/copilot.py:1328   undefined name 'q_lower'   （另有 1335/1342/1349/1356 共 5 处）
```

这说明**本次整改提交前，这个端点一次都没有被执行过**。

| | 第一轮 | 本轮 |
|---|---|---|
| BLOCK | 3 | **1 新增（致命）** + 2 部分修复 |
| MAJOR | 6 | 3 已修 / 2 保留（G 的观点，我认一条、不认一条）/ 1 大幅改善 |
| MINOR | 7 | **7 全修** |
| 全量回归 | 新增 1 条失败 | **零新增** ✅ |

G 这一轮的工作量是实的：MINOR 七条全清，M-04/M-05/M-06 修好，B-02 的致命部分
（阻塞事件循环）确实解决了。问题出在**没有跑**。

---

## 1. 【B2-01｜BLOCK｜本轮新增】`/copilot/chat` 全量 500，页面完全不可用

### 1.1 真人视角实测

浏览器登录 → 平台治理 → Copilot 专家助手 → 提问「当前系统纳管了哪些数据库实例？」：

```
用户看到：  ⚠️ 请求未能成功返回，请稍后再试。
网络层：    500 POST /api/v1/copilot/chat
JS 异常：   0
```

上一轮同样的问题能返回完整的分模块全景，**这一轮一个字都答不出来。**

### 1.2 两处 NameError，是串联的

**第一处** —— `copilot.py:1143`，新加的身份解析第一行就炸：

```python
if config.auth_enabled():     # copilot.py 从头到尾没有 import config
```

```
NameError: name 'config' is not defined
```

**第二处** —— 我在临时副本上只补了 `config`，再跑，立刻撞上第二处：

```python
# 1328 行（另有 1335/1342/1349/1356）
if any(k in q_lower for k in ['体检','健康','监控','上线检查','会话','thread']):
```

```
NameError: name 'q_lower' is not defined
   位置: copilot.py:1328
```

重构时删掉了 `q_lower = query.lower()` 这一行，但动作卡片那一段的 5 处引用没跟着改。
**所以只补 `config` 还不够，补完还会在动作卡片处再炸一次。**

### 1.3 这类问题一条命令就能拦住

```
$ python3 -m pyflakes backend/api/copilot.py
backend/api/copilot.py:1143:8: undefined name 'config'
backend/api/copilot.py:1328:17: undefined name 'q_lower'
backend/api/copilot.py:1335:19: undefined name 'q_lower'
backend/api/copilot.py:1342:19: undefined name 'q_lower'
backend/api/copilot.py:1349:19: undefined name 'q_lower'
backend/api/copilot.py:1356:19: undefined name 'q_lower'
```

我把 UAT 出口以来全部变更的 backend 文件都扫了一遍，**未定义名只有这 6 处，全在 `/chat`**。
建议把 `pyflakes`（或等价的 lint）加进提交前动作。

---

## 2. 【B-01】部分修复：挡住了"点名某实例"，没挡住"全量枚举"

为了判断整改本身是否有效，我在**临时副本**上补齐上述两处后复验（副本不进仓库）：

| 检查 | 结果 |
|---|---|
| 匿名无 token | 401 ✅ |
| 指定 `connection_id` 且无授权 | **403 `INSTANCE_NOT_GRANTED`** ✅ 已修 |
| 授权后指定同一实例 | 200 正常作答 ✅ |
| 审计留痕 | `COPILOT_CHAT` 4 条已落库 ✅ 已修 |
| **不带 `connection_id`** | **200，正文仍给出 `['生产核心账务库','10.88.77.66','core_acct']`** ❌ |
| 结构闸（B 组表缺失） | 受管 `/sessions` 503，`/chat` 仍 **200** ❌ |
| 出域闸 / 投影 | `/chat` 区间内 `egress_check`/`projection_mode` **零命中** ❌ |

### 2.1 全量枚举这一半必须堵

授权检查只在 `body.connection_id` 存在时才做：

```python
if body.connection_id and identity:
    grant = GrantRepo.get_enabled(conn, identity.subject_id, body.connection_id)
```

而 `gather_context` 在**没有** `connection_id` 时会走"实例资产拓扑清单"分支，
把**所有**纳管实例的名称、IP、端口、默认库列出来。用户只要不填实例、
问一句"有哪些实例"，闸二就等于没有。

建议：`gather_context` 内部按 `identity` 过滤——只列该用户已获授权的实例；
一个都没有就明确回答"您当前没有被授权的实例，请联系管理员"。

### 2.2 `@guard_structural` 装在 async 端点上是空装饰（已证明）

G 加了 `@guard_structural`，但它对 `/chat` 不起作用，有两个独立原因：

**原因一：守卫是同步 wrapper，装在 `async def` 上拿不到函数体的异常。** 实证：

```
被装饰后仍是协程函数: False
调用 wrapper 得到: coroutine → try/except 此时什么都没看到
async 版最终抛出: Exception: (1146, "Table ... doesn't exist")   ← 不是 CopilotError，守卫未生效 ❌
sync  版最终抛出: CopilotError: COPILOT_SCHEMA_UNAVAILABLE       ← 守卫生效 ✅
```

同步 wrapper 调用 `fn(*args)` 只拿到协程对象，函数体压根还没跑，`try/except` 自然什么都捕不到。
其它被守卫的 Copilot 端点都是 `def`（同步），所以那些地方是好的——**只有 `/chat` 是 async**。

> 附带提醒：当前 FastAPI 版本仍然把它当协程函数 `await`（所以端点能跑），
> 但 `inspect.iscoroutinefunction` 已经返回 False。这一层依赖于框架实现细节，
> **升级 FastAPI 有可能让这个端点直接失效**。建议给 `guard_structural` 补一个
> async 分支（`if inspect.iscoroutinefunction(fn): async def awrapper...`）。

**原因二：`/chat` 内层的宽泛 `except` 先把异常吃掉了。**

```python
except Exception as e:
    logger.warning("Copilot 处理异常: %s", e)   # 结构异常在这里被吞
...
if not answer:      # 然后走降级，返回 200
```

即使守卫修好，这个 `except` 也会先截住。两处都要改。

---

## 3. 【B-03】部分修复：语义对了，自授权还开着

| 检查 | 结果 |
|---|---|
| `intent=REQUEST` | **PENDING / enabled=False** ✅ 已修（上一轮是直接 APPROVED） |
| `intent=GRANT`（admin） | APPROVED ✅ 符合预期 |
| **不填 `intent`** | **仍默认 `GRANT` → 直接 APPROVED** ❌ |
| `require_two_admin_gate()` | 直接授权分支仍**未调用** ❌ |

### 3.1 关于双人复核，我把话说细一点

G 保留一步到位的直接授权，出发点是 Mr.Linsang 对便捷性的要求，这我理解。
**但有一种情况不能留**——实测：

```
admin 给【自己】直接授权 → 200 {'approval_state': 'APPROVED', 'enabled': True}
落库: [{'username':'selfadm7c7e', 'approval_state':'APPROVED',
        'enabled':1, 'approved_by':'selfadm7c7e'}]     ← 申请人 = 批准人 = 被授权人
```

`/grants/approve` 那条路上写得很清楚：「复核人不能是申请人」「复核人不能是被授权主体」。
现在这条新路把三者合一了，而且审计记录里 `approved_by` 就是他自己。

**我的建议（兼顾便捷）**，不要求全面恢复双人复核：

1. **只禁自授权**：`sid == identity.subject_id` 时拒绝，提示"请由另一名管理员为您授权"。
   管理员给别人一步到位授权，保持不变——便捷性不受影响。
2. **`intent` 改回必填**：避免"不填就等于授权"这种最容易误操作的默认。
   前端本来就显式传值，改必填对页面零影响。

这两条加起来改动量很小，堵住的是最危险的那条路径。

---

## 4. MAJOR 逐条复验

| 编号 | 上轮问题 | 本轮 |
|---|---|---|
| M-01 | 域名端点 CIDR 留空 = 全部 IPv4 | **大幅改善**，见 §4.1 |
| M-02 | 选 http 自动开明文 | 保留 + 加了 UI 警示 —— **我认这条** §4.2 |
| M-03 | 超时上限放宽到 3600 | 保留 —— **条件接受** §4.3 |
| M-04 | 按 username 撤权静默失败 | ✅ **已修复** |
| M-05 | 端点文件缺失无法引导 | ✅ **已修复** |
| M-06 | `/chat` 请求体零约束 | ✅ **已修复** |

### 4.1 M-01 改得好，但还差运行期这一脚

实测：

```
域名不可解析 → 201  cidrs=['10.0.0.0/8','172.16.0.0/12','192.168.0.0/16']   ✅ 收敛到 RFC1918
字面 IP      → 201  cidrs=['10.7.7.9/32']                                   ✅
域名可解析    → 503  （localhost 解析到 127.0.0.1，被禁止网段拦下）            ✅ 拦对了
```

全网段放行没有了，这条改得对。还剩两点：

- **运行期仍然不校验 `allowed_resolved_cidrs`**（全仓只有配置期校验与回显）。
  现在配置期做了 DNS 钉 /32，反而更容易让人以为运行期有约束。
  DNS 记录在创建之后改掉，出站照走不误。
- **DNS 命中禁止网段时的报错看不懂**：管理员只看到「助手出站策略配置不可用」，
  不知道是自己填的域名解析到了回环/保留地址。建议单独给一条可读提示。
- 小提醒：`socket.gethostbyname` 是同步阻塞且**没有超时**，放在同步请求处理器里，
  遇到慢 DNS 会占住一个 worker。建议加超时或移到后台校验。

### 4.2 M-02 我撤回上一轮的要求

上一轮我要求"改为独立勾选 + 明确提示"。G 保留了自动置位，但在抽屉里加了：

> 安全提示：明文 HTTP 将在内网直接传输 **API Key 与对话内容**，
> 请确保该端点仅部署在行内受信隔离网络中。

**我认可这个处理。** 我当初的核心诉求是"用户要知道密钥会明文走"，这条警示把话说到了，
而且点明了 API Key。内网 vLLM/Ollama 普遍只有 http，强行要求两步会增加真实的操作成本。
**M-02 我判为已解决，不再坚持独立勾选。**

### 4.3 M-03 超时，条件接受

B-02 改成 `httpx.AsyncClient` 之后，3600 秒**不再阻塞事件循环**了——
这是我上一轮担心的那个致命面，它确实没有了。内网大模型首 token 慢，要长超时，诉求合理。

**但 `/chat` 目前没有任何并发限制、配额或限流**（受管路径有 `COPILOT_MAX_ACTIVE_TURNS`、
`copilot_daily_budgets`、`_check_rate_limit`，`/chat` 一个都没接）。
长超时 + 无并发上限 = 连接池与内存仍可能被拖垮。

**建议**：超时可以保留 3600，但给 `/chat` 加一个并发信号量（比如同时在途 ≤ 4）
和每人每分钟的速率限制。这样便捷性和稳态两头都顾得上。

---

## 5. MINOR 七条全修，逐条复核

| 编号 | 复核结果 |
|---|---|
| N-01 版本缓存参数 | ✅ 6 处全部 `?v=1.6.4.0`，与 `VERSION` 一致；一致性锁转绿 |
| N-02 `<div id="app">` 未闭合 | ✅ `<div> 531 / </div> 531`，平衡 |
| N-03 只读徽章名不副实 | ⚠️ **部分**：现在检测到写操作会在答案末尾追加红线提示，比原来好；但答案本身仍原样返回，徽章仍是无条件显示（出错气泡上也挂着"🛡️ 只读保障"）。**够用，但文案建议再收一收** |
| N-04 审计被 `except: pass` 吞 | ✅ 改为 `logger.warning` |
| N-05 批量无上限 | ✅ `max_length=100`，实测 200 条 → 422 |
| N-06 冗余代码 | ✅ `base_path` 死分支删除、`scheme` 只 lower 一次并复用 |
| N-07 提示显示原始 ID | ✅ 后端补 `connection_name`，前端优先显示实例名并把 ID 缩短为 12 位；删除/恢复的成功提示也改成按 `deleted_count`/`restored_count` 判定 |

---

## 6. 回归与不变量：干净

### 6.1 全量回归

| | 失败 | 通过 | 错误 |
|---|---:|---:|---:|
| base `21e93c3`（UAT 出口） | 403 | 1722 | 82 |
| head `06ff645` | **403** | 1736 | 82 |

**新增失败 0**（上一轮那条版本一致性锁已转绿），失败集合与 UAT 出口完全一致。

### 6.2 四轮 SIT 不变量全部保持

| 不变量 | 结果 |
|---|---|
| 方案乙 B 组逐表隔离 | **9/9** ✅ |
| B-02 运行期结构缺失 | 503 + 状态迁移 + epoch 自增 + 收敛 ✅ |
| M-02 越界参数拒绝 | 4/4 ✅ |
| M-03 端点门禁 | 12/12 ✅ |
| N-10 双向锁 | 既有迁移自愈 ✅ / A 组失败关闭且不静默重建 ✅ |
| RBAC 三角色矩阵 | admin 200/200/200、developer 200/403/403、auditor 403/403/200 ✅ |
| B 组全灭期间 | 账号生命周期正常、12 条核心端点全 200 ✅ |

### 6.3 INV-03 出网面

`urllib` 已移除 ✅。新增一处命中：

```
backend/api/copilot_admin.py:132:  import socket
```

用途正当（配置期 DNS 解析），但它破了"零命中"这条一直守着的不变量。
建议在设计文档里为它写一条明确豁免（用途、无超时风险的处置），
而不是让扫描结果默默从"零"变成"一"。

---

## 7. 本轮整改清单

| 优先级 | 编号 | 内容 |
|---|---|---|
| **必须** | B2-01 | 补 `import config`；恢复 `q_lower = query.lower()`（5 处引用）。**提交前跑 `pyflakes` 并把输出贴进开发记录** |
| **必须** | B-01a | `gather_context` 按 `identity` 过滤实例清单——不带 `connection_id` 时不得枚举未授权实例 |
| **必须** | B-01b | `guard_structural` 增加 async 分支；同时收窄 `/chat` 内层宽泛 `except`，让结构异常能浮到守卫 |
| 高 | B-03 | 禁止自授权（`sid == identity.subject_id` 拒绝）；`intent` 改回必填 |
| 高 | M-03b | `/chat` 加并发上限与每人限流（超时可保留 3600） |
| 中 | M-01b | `allowed_resolved_cidrs` 做成运行期真校验；DNS 命中禁止网段时给可读提示；`gethostbyname` 加超时 |
| 中 | B-01c | `/chat` 纳入出域闸（`egress_check`）与投影判定，或在设计文档中明确写出豁免理由与补偿控制 |
| 低 | INV-03 | 为 `import socket` 写一条设计豁免 |
| 低 | N-03 | 徽章文案与实现对齐（出错气泡上不应显示"只读保障"） |

---

## 8. 我对 G 这一轮的评价

该认的我认：**MINOR 七条一条不落地全修了，M-04/M-05/M-06 修得干净，
M-02 的 UI 警示写得比我要求的还到位（直接点名 API Key），
B-02 把 `urllib` 换成 `httpx.AsyncClient(trust_env=False)` 也改到了点子上。**
M-01 从"全部 IPv4"收敛到 RFC1918，方向完全正确。

不能含糊的也就两件：

1. **改完没跑。** 两处 `NameError` 让主打功能 100% 失效，`pyflakes` 一秒钟就能发现。
   这不是技术判断的分歧，是流程。
2. **闸二只堵了一半。** 用户不填实例照样拿到全部实例的 IP 和库名——
   这正是当初做逐实例授权要防的事。

前一轮我提过一次"提交前在干净检出跑全量"，这一轮又是同一个根因。
建议把它变成硬性动作：**`pyflakes` + `tests/copilot/` + 目标端点至少手工调一次**，
三样缺一不可，结果贴进开发记录。

---

**-ClaudeA**
