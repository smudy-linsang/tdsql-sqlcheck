# v1.6.4.0（G 改进后）· 第四轮复验与发布意见

| 项 | 内容 |
|---|---|
| 受测版本 | v1.6.4.0（受测提交 `f071701`） |
| 对照基线 | `21e93c3`（UAT 出口）；整改前 `b2a3923` |
| 上一轮 | [第三轮复测复检报告](QC3-v1.6.4.0-G改进版第三轮复测复检报告-ClaudeA.md) |
| 测试人 | 智能体A（独立评审/测试） |
| 报告日期 | 2026-09-20 |
| 送呈 | Mr.Linsang |

---

## 0. 结论

**上一轮的 Q3-M-01 与 Q3-N-01 都修对了，回归零新增失败，页面正常。**

**本轮发现 1 条 MAJOR：`/chat` 里 `ctx_parts` 在 try 之前没有预置，
感知阶段出任何普通异常都会二次崩成 500。这条是既存的，不是本次改出来的。**

改动很干净——三个文件共 8 行代码 + 4 行设计文档，没有夹带。

| 编号 | 上一轮问题 | 本轮 |
|---|---|---|
| Q3-M-01 | 数据分级依 `connection_id` 判定 | ✅ **已修复**，改为依 `ctx_parts` |
| Q3-N-01 | `Path` / `logger` 两处未定义名 | ✅ **已修复** |
| INV-03 | `import socket` 缺设计豁免 | ✅ 已写入设计说明书 |
| Q3-N-02 | 信号量与限流为进程级 | 未处理（我当初就是列作"建议"，不阻塞） |

---

## 1. Q3-M-01 修对了，而且没有误伤

改动就是我建议的那一行：

```diff
- data_class = "INTERNAL_REDACTED" if body.connection_id else "PUBLIC_HELP"
+ data_class = "INTERNAL_REDACTED" if (body.connection_id or ctx_parts) else "PUBLIC_HELP"
```

我上一轮担心过一件事：会不会把 `PUBLIC_HELP` 这条路彻底变成死代码
（因为"无授权"的提示本身也算一段 ctx）。**实测下来没有**：

| 场景 | ctx 段数 | 分级 |
|---|---:|---|
| 无授权 · 问"什么是分片表？和广播表有什么区别？" | 0 | **PUBLIC_HELP** ✅ 纯概念问题仍走公共帮助 |
| 无授权 · 问"系统纳管了哪些数据库实例？" | 1 | INTERNAL_REDACTED（内容是"访问受限"提示，偏保守，失败朝安全侧）✅ |
| 已授权 1 个实例 · 问概念题 | 1 | INTERNAL_REDACTED |
| 已授权 1 个实例 · 问"有哪些实例？" | 2 | INTERNAL_REDACTED ✅ |

结论：**只要正文里真的带了业务资料就按内网分级走，纯概念咨询仍可走 PUBLIC_HELP。**
便捷性没有被牺牲，闸门也补上了。

## 2. Q3-N-01 两处未定义名已清

```
backend/api/gitlab_hook.py     + import logging / logger = logging.getLogger("tdsql.gitlab")
backend/services/report_service.py  + from pathlib import Path
```

全 `backend/` 重扫，**只剩 `audit_service.py:191/294` 的 `InstanceContext`**——
上一轮我已核实那是字符串形式的类型注解、运行期不求值，**pyflakes 在这里是误报，不是缺陷**。

## 3. INV-03 豁免已入设计文档

`DETAIL-v1.6.4.0-...-O.md` 里加了明确条款：出网唯一通道为 `httpx`
（`trust_env=False, follow_redirects=False`）；`policy.py` 与 `copilot_admin.py` 的
`import socket` 经批准仅用于配置期/运行期本地 DNS 解析做 CIDR 白名单与回环/保留网段拦截，
必须显式设超时（3.0s / 2.0s）并在 `finally` 还原，严禁建立任何外部连接。

**扫描口径从此有据可依。** 这正是我要的效果。

---

## 4. 【Q4-M-01｜MAJOR｜既存】`ctx_parts` 未预置，感知阶段出错即二次崩溃

### 4.1 问题

`copilot_chat` 里 `ctx_parts` 只在 `try` 内部第 1211 行赋值：

```python
    conn = _get_connection()
    try:
        ensure_db()
        grant = None
        ...
        ctx_parts = CopilotPerceptionEngine.gather_context(...)   # 1211
        ...
    except Exception as e:
        ...
        logger.warning("Copilot 处理异常: %s", e)                  # 吞掉普通异常
    finally:
        conn.close()

    if not answer:
        if ctx_parts:                                             # 1324 ← try 之外
```

只要在 1211 之前或之中抛出**非结构类**异常（`ensure_db()`、`GrantRepo.get_enabled()`、
或 `gather_context` 自身），外层 `except` 把它记个日志就放过去了，
然后 1324 行访问从未被赋值的 `ctx_parts`，**直接崩**。

### 4.2 实测

让感知引擎抛一个普通的 `ValueError`（模拟 registry / DB 层的一般故障）：

```
基线（正常）              → 200
gather_context 抛普通异常 → 500  body='Internal Server Error'

真实异常: UnboundLocalError: cannot access local variable 'ctx_parts'
         where it is not associated with a value
位置: copilot.py:1324  if ctx_parts:
```

### 4.3 为什么值得单列

1. **它把"优雅降级"变成了"500"**。那段 `except` 的本意就是"模型或感知出问题也要能答"，
   结果恰恰在这条路上崩掉，降级逻辑等于白写。
2. **`pyflakes` 抓不到它**。这不是未定义名，是"条件未绑定的局部变量"，
   上一轮新立的那道门禁对这一类无效——所以要专门点出来。
3. **它是既存的**，从 `/chat` 建立时就在，不是本次改出来的。

### 4.4 改法（一行）

在 `conn = _get_connection()` 之前补一句：

```python
ctx_parts: list[str] = []
```

`answer`、`model_name` 这些同层变量本来就是这么预置的，补上它只是把写法拉齐。

---

## 5. 回归与不变量：干净

### 5.1 全量回归

| | 失败 | 通过 | 错误 |
|---|---:|---:|---:|
| base `21e93c3`（UAT 出口） | 403 | 1722 | 82 |
| head `f071701` | **403** | 1736 | 82 |

**新增失败 0**，失败集合与 UAT 出口逐条一致。

### 5.2 不变量与闸门复跑

| 项 | 结果 |
|---|---|
| 方案乙 B 组逐表隔离 | **9/9** ✅ |
| N-10 双向锁 | 既有迁移自愈 ✅ / A 组失败关闭不静默重建 ✅ |
| RBAC 三角色矩阵 | admin 200/200/200、developer 200/403/403、auditor 403/403/200 ✅ |
| `/chat` 闸二（不带实例） | 零泄漏 ✅ |
| `/chat` 闸二（指定未授权实例） | 403 `INSTANCE_NOT_GRANTED` ✅ |
| `/chat` 按授权过滤 | 授权 1 个只见 1 个 ✅ |
| `/chat` 结构闸 | 503 ✅ |
| `/chat` 限流 | 25 连击 → 200×15 / 429×10 ✅ |

### 5.3 浏览器真人视角

登录 → Copilot 专家助手 → 提问，正常返回实例全景，
**5xx 0、JS 异常 0**，徽章显示为「🛡️ 只读建议」。

---

## 6. 发布意见

**建议补上 Q4-M-01 那一行再发布。**

这条不是拦路虎——它要在感知阶段出普通异常才触发，正常路径不受影响；
但它的后果是"本该降级的场景反而 500"，而且修它只要一行。
既然已经走到这一步了，顺手清掉比留着好。

若 Mr.Linsang 认为可以带着这条先上，我的意见是**也可以接受**，
理由是：触发条件是异常路径、不涉及数据泄漏或越权、有明确的日志可追溯。
但请把它记进遗留清单，不要遗忘。

### 6.1 剩余遗留（均不阻塞）

| 编号 | 内容 |
|---|---|
| Q4-M-01 | `ctx_parts` 预置（一行） |
| Q3-N-02 | 信号量与限流表是进程级内存，`--workers 2` 时每人实际额度翻倍；要严格控量需改共享计数 |
| —— | `audit_service.py` 的 `InstanceContext` 是 pyflakes 误报，**无需处理**，此处备注以免下次重复排查 |

---

## 7. 四轮下来的总体看法

从第一轮到现在，走势是清楚的：

| 轮次 | BLOCK | MAJOR | 主要问题性质 |
|---|---:|---:|---|
| 第一轮 | 3 | 6 | 设计上开口子（`/chat` 绕过全部闸门） |
| 第二轮 | 1 新增 | 2 保留 | 改完没跑，主打功能 100% 500 |
| 第三轮 | 0 | 1 新增 | 闸门都真修好了，分级判定有瑕疵 |
| **第四轮** | **0** | **1（既存）** | 改动干净，只剩一处历史遗留 |

第三轮起 G 的完成质量就稳定下来了：我提的每条都按建议方向落地，
`pyflakes` 门禁还顺带挖出了藏很久的广播表判定失效。
本轮改动 8 行代码、零夹带、零回归——**这就是该有的样子。**

---

**-ClaudeA**
