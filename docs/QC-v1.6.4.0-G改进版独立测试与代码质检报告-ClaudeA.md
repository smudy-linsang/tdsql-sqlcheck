# v1.6.4.0（G 改进后）· 独立测试与代码质检报告

| 项 | 内容 |
|---|---|
| 受测版本 | v1.6.4.0（受测提交 `55eab12`） |
| 对照基线 | `21e93c3`（UAT 出口通过点） |
| 受测范围 | UAT 出口之后的全部改动：QC1–QC4 四轮质检整改 + 批量授权 + 端点 WEB 管理 + 元数据修复 + 基础路径容错；代码面 21 个文件、4267 行新增 / 362 行删除 |
| 测试人 | 智能体A（独立评审/测试） |
| 报告日期 | 2026-09-19 |
| 送呈 | Mr.Linsang |

---

## 0. 结论

**不建议直接发布。发现 3 项 BLOCK、6 项 MAJOR。**

原有功能没有被改坏——四轮 SIT 锁住的不变量重跑全部仍然成立，全量回归只新增 1 条失败。
页面本身做得不错：21 个页面零 JS 异常、零 5xx，Copilot 对话可用且信息组织清晰，
Mr.Linsang 反馈的"不够人性化"确实被解决了。

问题出在**新增的 `/api/v1/copilot/chat` 这条路**：它没有走 v1.6.4.0 用四轮 SIT、六轮 UAT
建起来的那套闸门，而是另起了一条绕过全部管控的通道。再加上端点 WEB 管理带进来的
全网段默认放行、明文 HTTP 自助开通，以及新增的直接授权路径绕过双人复核——
这三件事叠在一起，等于把本版最核心的安全设计架空了。

| 级别 | 数量 | 编号 |
|---|---|---|
| BLOCK | 3 | B-01 / B-02 / B-03 |
| MAJOR | 6 | M-01 ~ M-06 |
| MINOR | 7 | N-01 ~ N-07 |

---

## 1. 【B-01｜BLOCK】`/copilot/chat` 绕过全部 Copilot 管控闸门

`backend/api/copilot.py:1115` 新增的 `POST /api/v1/copilot/chat`，**没有解析身份、没有任何闸门调用**：

```
在 1115–1330 行范围内检索 identity / check_instance_grant / check_source_menu /
_require_ready / guard_structural / egress_check / projection / effective_allow
→ 零命中
```

### 1.1 实测（`developer` 账号，对该实例无任何 Copilot 逐实例授权）

```
1. 匿名无 token                    → 401 ✅（RBAC 菜单闸仍在）
2. developer 无授权 + connection_id → 200，正文直接给出：
   ['生产核心账务库', '10.88.77.66', 'core_acct', '3306']
   → 闸二（逐实例授权）❌ 被绕过
3. 对照：受管端点 /copilot/connections 同条件 → 未给出 host/库名 ✅
4. B 组表缺失时：
   受管 /copilot/sessions → 503     新增 /copilot/chat → 200   ❌ 结构闸未纳入
5. auditor 角色                    → 403 ✅
6. COPILOT_ENABLED 未开启          → 200 ok=true  ❌ 总开关被绕过
```

### 1.2 一并绕过的还有

| 管控 | 受管路径 | `/chat` |
|---|---|---|
| 逐实例授权（闸二） | `check_instance_grant` | 无 |
| 投影模式 ALIASED / SCHEMA_IDENTIFIERS（M-01 三闸） | `preview_service` 三闸 | 无，真实实例名/IP/端口/库名直接入提示词 |
| 出域策略 `egress_check`（数据分级、端点能力位） | 有 | 无，只调 `build_url` |
| 结构闸 `_require_ready` / `guard_structural` | 有 | 无 |
| 总开关 `COPILOT_ENABLED` | 有 | 无 |
| 取证封存 / turn 记录 / `copilot_audit_events` | 有 | **无任何留痕** |
| 配额 `copilot_daily_budgets`、限流 | 有 | 无 |
| `Limits`（上下文字节、输出 token） | `workflow.py:309/311` 取 `Limits` | 硬编码 `max_tokens: 4096`，上下文不限 |

**M-01 是我在设计评审和三轮 SIT 里反复盯的那条三闸，本版实际对话走的是不经过它的另一条路。**

---

## 2. 【B-02｜BLOCK】INV-03 出网/执行面破防，且阻塞调用放进了 async 端点

### 2.1 INV-03 由"零命中"变为有命中

四轮 SIT 每轮都扫过、每轮都是零命中的那条不变量，现在破了：

```
backend/api/copilot.py:1090:  import urllib.request
backend/api/copilot.py:1223:  with urllib.request.urlopen(req, timeout=copilot_timeout) as resp:
```

受管出站通道一直是 `httpx.AsyncClient(trust_env=False, follow_redirects=False)`。
`urllib.request.urlopen` **默认读取 `http_proxy` / `https_proxy` 环境变量**——
`trust_env=False` 当初就是为了防这个。现在多了一条不受该约束的出站路径。

### 2.2 同步阻塞调用放在 `async def` 里

```python
async def copilot_chat(request: Request, ...):
    ...
    copilot_timeout = int(os.getenv("COPILOT_CHAT_TIMEOUT", "3600"))
    with urllib.request.urlopen(req, timeout=copilot_timeout) as resp:
```

`async def` 端点运行在事件循环上，同步 `urlopen` 会**阻塞整个事件循环**，不是只占一个线程。
默认超时 **3600 秒**。部署是 `--workers 2`——两个慢请求就能把整个 Web 服务按住一小时，
期间 SQL 审核、元数据审核、登录全部无响应。

受管路径是 `async with httpx.AsyncClient(...)`，不存在这个问题。

---

## 3. 【B-03｜BLOCK】新增直接授权路径绕过双人复核

`PUT /copilot-admin/grants` 新增了"管理员直接分配授权（即时生效，APPROVED + enabled=1）"
分支（`copilot_admin.py:712-728`，调 `GrantRepo.direct_grant`）。

**与 UAT 出口版本逐字对照：**

| | UAT 出口 `21e93c3` | 现在 `55eab12` |
|---|---|---|
| `intent` 取值 | `^(REQUEST\|REVOKE)$`，**必填** | `^(REQUEST\|REVOKE\|GRANT\|DELETE\|RESTORE)$`，**默认 `GRANT`** |
| `direct_grant` | **不存在** | 新增 |
| 双人复核 | `/grants/approve` 内 `require_two_admin_gate()` + 复核人≠申请人 + 复核人≠被授权主体 | 直接授权分支**一个都不调** |

实测：一次提交 3 用户 × 3 实例，返回 `approval_state: APPROVED, enabled: true, count: 9`，
`PENDING=0 / APPROVED=9`——**申请与批准一步完成，无第二名管理员参与**。

而且 `intent` 默认就是 `GRANT`：**不填 intent 就是直接授权**。更糟的是传 `intent="REQUEST"`
（语义是"申请"）也落到同一个分支，同样直接变成 APPROVED。

---

## 4. MAJOR

### 4.1 【M-01】域名端点 + CIDR 留空 = 全网段放行，且绕开了校验器自己的禁令

`copilot_admin.py:125-131`：CIDR 留空且主机不是字面 IP 时，默认写入
`["1.0.0.0/1", "128.0.0.0/1"]`。实测：

```
1.0.0.0/1   规范化 = 0.0.0.0/1      2147483648 个地址
128.0.0.0/1 规范化 = 128.0.0.0/1    2147483648 个地址
合计 4294967296 = 整个 IPv4 空间

10.1.2.3 / 127.0.0.1 / 8.8.8.8 / 169.254.169.254 / 223.5.5.5  全部落入

_validate_cidr('0.0.0.0/0')   → 拒绝（"CIDR 落入禁止范围"）
_validate_cidr('1.0.0.0/1')   → 放行
_validate_cidr('128.0.0.0/1') → 放行
```

校验器明令禁止 `0.0.0.0/0`，而产品自己的默认值用两个 `/1` 把同样的范围**绕了过去**。
实测创建：域名主机 + CIDR 留空 → `201`，存储 `allowed_resolved_cidrs=['1.0.0.0/1','128.0.0.0/1']`。

**附带说明（这条要讲公道）**：`allowed_resolved_cidrs` **在运行期从来没有被校验过**——
全仓只有配置期校验与管理视图回显，没有任何"解析 IP 后比对网段"的调用。我核过
UAT 出口版本，同样没有。所以**这是既存缺口，不是 G 引入的**。G 引入的是把它放大：
端点从部署文件受控，变成了管理员可在 WEB 上自助创建，且默认值是全网段。

字面 IP 的回环/元数据地址是被挡住的（`127.0.0.1`、`169.254.169.254` → 503），
但**域名主机完全不受此限**。

### 4.2 【M-02】明文 HTTP 端点可自助开通，密钥随之明文出网

`policy.py` 把 `if ep["scheme"] != "https": raise POLICY_UNAVAILABLE  # 不接受 HTTP 降级`
改成了允许 http（条件：`data_zone == INTERNAL` 且 `allow_http` 为真）。
而 `copilot_admin.py:149` 是：

```python
if body.scheme == "http":
    ep_data["allow_http"] = True
```

**在 WEB 上选了"HTTP (内网明文服务)"，就自动把 `allow_http` 给自己打开了**——
所谓的二次确认并不存在。实测 `scheme=http + INTERNAL` → `201, allow_http=True`。

后果：`/chat` 发出的请求里带 `Authorization: Bearer <解密后的模型密钥>`，
连同注入的实例名/IP/库名，**全程明文**。抽屉里只写"HTTP (内网明文服务)"，
**没有一个字提示密钥会明文传输**。

内网允许 http 本身可以是产品决策，但"选了就自动开、且不提示密钥明文"不行。

### 4.3 【M-03】M-02 的参数上限被放宽，方向与 SIT 结论相反

`policy.py` 的 `Limits.RANGES`：

| 参数 | UAT 出口 | 现在 |
|---|---|---|
| `COPILOT_TURN_DEADLINE_SECONDS` | (30, **120**, 90) | (30, **3600**, **3600**) |
| `COPILOT_PROVIDER_TOTAL_SECONDS` | (10, **60**, 60) | (10, **3600**, 3500) |
| `COPILOT_HTTP_READ_SECONDS` | (5, **30**, 20) | (5, **3600**, **3600**) |
| `COPILOT_HTTP_CONNECT_SECONDS` | (1, 5, 5) | (1, 30, 10) |

默认单轮超时从 90 秒变成 **1 小时**。`M-02` 当初的整改点是"越界参数必须拒绝而非夹值"，
拒绝逻辑还在（实测 4 项越界仍正确报错 ✅），但**闸门本身被抬高了 30 倍**。
配合 `COPILOT_MAX_ACTIVE_TURNS` 上限 8，最坏情况是 8 个 turn 各占 1 小时。

如果是为了适配内网慢模型而有意放宽，请在设计文档里写明依据与容量测算；
目前是直接改常量，没有任何配套说明。

### 4.4 【M-04】批量撤销/恢复对 `username` 形式静默不处理，前端还报"成功"

`GrantActionItem` 模型明确接受 `username` 作为定位方式，但两个处理器只认 `subject_id`：

```python
for item in body.grants:
    if item.subject_id and item.connection_id:   # username 形式直接被跳过
```

实测：用 `{"username": ..., "connection_id": ...}` 调 `batch-delete`
→ **200 `{"deleted_count": 0}`**，授权原封不动仍是 APPROVED。
`restore` 同样。**撤权这种安全操作静默失败并返回成功，是不能接受的。**

前端当前发的是 `subject_id`，所以页面上点按钮是好的。但前端的成功提示是：

```javascript
if (resp.ok) { ElementPlus.ElMessage.success('批量删除成功'); }
```

**只看 HTTP 200，不看 `deleted_count`**——只要后端返回 200 就报成功，
哪怕一条都没删。这两个问题是同一类：结果与提示脱钩。

### 4.5 【M-05】显式配置端点文件路径的部署，无法从零新增端点

`create_endpoint` 第一步就是 `load_policy()`，而 `_load()` 在文件不存在时
直接 `raise CopilotError("POLICY_UNAVAILABLE")`。实测：文件不存在 → 新增端点 **503
"助手出站策略配置不可用"**，提示里没有任何"请先创建配置文件"的线索。

分两种情况，要说清楚：

- `COPILOT_ENDPOINTS_FILE` **未设置**：`load_policy` 会自动创建 `data/copilot-endpoints.json`，**可以引导** ✅
- `COPILOT_ENDPOINTS_FILE` **已设置但文件不存在**：**引导失败** ❌

而 `deploy/tdsql-copilot-runner.service:26` 正好示范了设置这个变量
（`COPILOT_ENDPOINTS_FILE=/etc/tdsql-sqlcheck/copilot-endpoints.json`）。
照着部署文档走的现场，恰好会踩中——而这正是本功能要解决的场景。

### 4.6 【M-06】`/chat` 请求体零约束，`history` 可伪造助手轮次

```python
class CopilotChatRequest(BaseModel):
    query: str                      # 无 max_length
    connection_id: Optional[str] = None
    history: Optional[list] = None  # 无长度限制、无元素类型
```

全仓其它请求模型都是 `ConfigDict(extra="forbid")` + `Field(max_length=...)`，只有这个没有。

`history` 的元素被原样塞进模型消息：

```python
for h in body.history[-6:]:
    if isinstance(h, dict) and h.get('role') in ('user','assistant') and h.get('content'):
        messages.append({'role': h['role'], 'content': h['content']})
```

**调用方可以自己编造 `role: assistant` 的历史轮次**，伪造"助手先前已同意"的上下文来
诱导模型。配合 §5.3 只读护栏只标记不拦截，这条链是通的。

---

## 5. MINOR

### 5.1 【N-01】前端缓存参数版本号与 VERSION 不一致，项目自带的锁已报红

```
VERSION = 1.6.4.0
copilot.css?v=1.6.4.1      copilot.js?v=1.6.4.1      copilot_admin.js?v=1.6.4.3
```

`tests/test_version_consistency.py::test_frontend_version_marks_match` 报红：
`静态资源缓存参数残留旧版本号：['1.6.4.1','1.6.4.3']`。

这是本轮**唯一新增的回归失败**。它同时说明一件事：**本批改动提交前没有跑全量 `tests/`**
——提交信息写的是 `125/125`，那是 `tests/copilot/` 的数字，全量里这条一直是红的。
1.6.4.1 / 1.6.4.3 也不是任何已发布版本号。

### 5.2 【N-02】`<div id="app">` 从未闭合

```
非自闭合 <div> 529 个 / </div> 528 个 → 差 1
未闭合的是第 23 行 <div id="app">；文件末尾 </el-dialog> 之后直接是 <script> 和 </body>
```

浏览器会自动补全，页面能正常渲染（实测 21 个页面都正常）。但 QC4 这一轮的整改说明
就是"清理孤立标签"，还剩这一个；这类问题正是当初"坠底黑屏"的成因。

### 5.3 【N-03】"只读保障"徽章名不副实

页面显著位置有 `🛡 银行只读安全防线` / `🛡️ 只读保障` 徽章。实际实现是：

```python
safety_status = {"is_read_only": len(detected_violations)==0, "violations": [...]}
...
return {"ok": True, "answer": answer, ..., "safety_status": safety_status}
```

**检测到写操作 SQL 只是把标志位置 false，`answer` 仍然原样返回给用户。**
不脱敏、不拦截、不降级。另外动作卡片只取**第一个** ```sql 代码块判断，
命中后 `break`——后面代码块里的写操作语句不会被看到。

徽章给用户的心理暗示是"平台已经帮我把关了"，与实现不符。要么改成真拦截，
要么把文案降级成"仅作提示"。

### 5.4 【N-04】端点变更审计被 `except: pass` 吞掉，且与配置写入非原子

```python
pol.upsert(ep_data)        # 配置已落盘
conn = _get_connection()
try:
    ...
    AuditRepo.record(conn, "CONFIG_CHANGE", ...)
    conn.commit()
except Exception:
    pass                    # 审计失败静默
```

正常路径审计是落库的（实测 7 条 `CONFIG_CHANGE / copilot_endpoints` ✅）。
问题是失败路径：配置已经生效了，审计写失败却无声无息。
而页面上明写着"**所有端点变动均记入系统安全审计日志**"。

### 5.5 【N-05】`batch-approve` 无批量上限

`GrantBatchApproveRequest.grants` 只有 `min_length=1`，没有上限。
实测一次提交 **5000 条 → 200**，返回 5000 条 errors。响应体与 DB 往返都不设防。

### 5.6 【N-06】冗余代码

| 位置 | 问题 |
|---|---|
| `copilot_admin.py:134` | `("/" + raw_path) if not raw_path.startswith("/")` —— pydantic 的 `^(/[A-Za-z0-9/_-]*)?$` 已先行拒绝无前导斜杠的输入（实测 `base_path="v1"` → 422），**该分支不可达** |
| `copilot_admin.py:138,195` | `body.scheme.lower()` —— pydantic 已限定 `^(https\|http)$` 全小写，**恒等操作** |
| `copilot_admin.py:149` | `if body.scheme == "http"` 用的是**未小写**的原值，与上一行的 `.lower()` 写法不一致（现因模型限定而无害，改模型即成隐患） |
| `models/copilot.py:662` | `GrantActionItem.username` —— 两个处理器都不实现，见 M-04 |

### 5.7 【N-07】提示里对用户显示原始 32 位 ID

```javascript
ElMessage.success('已恢复用户 ' + row.username + ' 对实例 ' + row.connection_id + ' 的授权');
```

显示的是 `68ecf7d285714ee38d42901ccea474de`，不是实例名。同一行里 `username` 用的是名字，
`connection_id` 却用了 ID，前后不一致。

---

## 6. 通过项（逐条实测，不看自述）

### 6.1 原有功能零破坏

| 不变量 | 结果 |
|---|---|
| 方案乙 B 组逐表隔离 | **9/9** 结构验收全检出、6 张有读端点的全部 503 降级、核心功能零受损、修复后自动收敛 |
| B-02 运行期结构缺失 | 503 + `COPILOT_SCHEMA_UNAVAILABLE` + READY→UNAVAILABLE + epoch 自增 + 收敛 ✅ |
| M-01 八种投影组合 | 8 passed ✅ |
| M-02 越界参数拒绝 | 4/4 正确拒绝 ✅（上限值本身的问题见 M-03） |
| M-03 端点门禁 | 12/12 判定正确 ✅ |
| N-10 双向锁 | 既有迁移自愈 ✅；A 组 `copilot_subjects`/`copilot_runtime` 失败关闭且不静默重建 ✅ |
| RBAC 三角色矩阵 | admin 200/200/200、developer 200/403/403、auditor 403/403/200 ✅ |
| B 组全灭期间 | 建用户/登录/改密/删用户正常，12 条核心端点全 200 ✅ |

### 6.2 全量回归

| | 失败 | 通过 | 错误 |
|---|---:|---:|---:|
| base `21e93c3` | 403 | 1722 | 82 |
| head `55eab12` | 404 | 1735 | 82 |

**新增失败仅 1 条**，即 N-01 的版本一致性锁。其余失败集合完全一致。

### 6.3 元数据表名含 create 词根修复（4939a28）——做得好

根因抓得准：旧代码 `if "CREATE" in s.upper()` 会把表名列当成 DDL，
`seal_custcreateoperdetailx` 这类表名直接截胡。新实现按标准列名
`Create Table`/`Create View` 精确取值，再用 `^\s*CREATE\s+` 锚定兜底。

**变异自证**：把匹配退回旧的模糊 `in` 写法 → 5 条锁红 4 条。**锁是真的。**

### 6.4 基础路径容错（55eab12）——正确

```
入参 ''    → 存储 '/'    → https://10.9.8.7:8443/chat/completions
入参 '/'   → 存储 '/'    → https://10.9.8.7:8443/chat/completions
入参 '/v1/' → 存储 '/v1'  → https://10.9.8.7:8443/v1/chat/completions
入参 '/v1'  → 存储 '/v1'  → https://10.9.8.7:8443/v1/chat/completions
```

无双斜杠、无重复拼接。

### 6.5 感知引擎的 SQL 是安全的

`perception.py` 20 处 `execute`，两处用了 f-string：

```python
sql_where = "WHERE connection_id = %s" if target_id else ""
conn.execute(f"SELECT COUNT(*) as c FROM slow_queries {sql_where}", args)
```

插值的是**固定字面量**，值走参数绑定；用户 `query` 文本从不进入 SQL。**无注入。**

### 6.6 端点管理的输入校验大体到位

```
developer 创建端点      → 403 ✅
重复 endpoint_id        → 400 并给出可读提示 ✅
非法 endpoint_id        → 422 ✅
字面 IP 127.0.0.1       → 503 拦下 ✅
字面 IP 169.254.169.254 → 503 拦下 ✅
```

（拦是拦住了，但错误码是笼统的 `POLICY_UNAVAILABLE`，用户看不出"这个地址属禁止网段"。）

### 6.7 关于 `/copilot-admin/health` 从 200 变 503 —— 这不是缺陷

我的历史脚本按四轮 SIT 的口径判它为 ❌，**这个判定是我的期望过期了**。
QC1 有意改成结构不可用时返回 `503 + 完整诊断 JSON`，前端也同步做了适配：

```javascript
if (resp.ok) health.value = await resp.json();
else if (resp.status === 503) health.value = await resp.json();
```

运维在管理页上照样能看到完整诊断。行为是自洽的。
需要做的只是同步 O 那边设计 §10.4 的文字（原本挂的是"把设计改成 200"，现在方向相反）。

---

## 7. Web 页面真人视角实测

用 Chromium 真实驱动，账号 `uitest`（admin）。

### 7.1 走查结果

登录 → 首登改密 → 重新登录 → 逐菜单走查，**21 个页面**：

```
治理概览 / 即时审核 / 文件审核 / 在线元数据审核 / 深度诊断 / Copilot专家助手 /
扫描任务 / 慢SQL记录 / EXPLAIN分析 / 上线检查 / 大表治理 / 实例管理 /
审核规则库 / 评估规则集 / 用户管理 / 角色管理 / 权限矩阵 / 数据保留 /
操作审计 / AI配置 / 系统信息

JS 异常 0    5xx 0    console error 0    请求失败 0
```

（走查器一度把「即时审核」「EXPLAIN分析」标为"正文极少"，看截图后确认是编辑器型页面，
正文本来就少，**属我的误报**。）

### 7.2 体验上确实改好了

- 登录、改密、菜单展开、页面切换都顺；暗色主题一致，没有坠底黑屏或白屏
- Copilot 对话页信息组织清晰：气泡、时间戳、模型标识、动作卡片（"前往上线检查"等）
- 实测提问"当前系统纳管了哪些实例？总体健康状况怎么样？"，
  返回的是分模块的结构化全景（实例清单 / SQL 审核 / 元数据 / 慢SQL / 巡检 / 表类型 /
  大表 / 告警），可读性好，**这一块 G 做得确实到位**
- AI 配置页五个页签（模型 / 端点管理 / 场景路由 / 实例授权 / 运行设置）布局清楚，
  端点抽屉的字段说明写得挺细致

### 7.3 但有两处"文案比实现强"

1. **端点抽屉**：CIDR 栏写"可选，多个逗号分隔。IP主机默认自动匹配 /32。"
   ——只讲了 IP 主机的收紧默认，**没讲域名主机留空是全网段放行**（M-01）。
   用户看到这句话的合理理解恰好与实际相反。
2. **协议栏**："HTTP (内网明文服务)" 讲了明文，**但没讲 API 密钥也会明文出网**（M-02）。

以及 §5.3 的"只读保障"徽章。

---

## 8. 整改建议（按优先级）

| 优先级 | 编号 | 建议 |
|---|---|---|
| 必须 | B-01 | `/chat` 要么并入受管路径（复用 `preview_service` 三闸 + `check_instance_grant` + `guard_structural` + `egress_check` + 审计留痕），要么就此下线。当前形态不能进生产 |
| 必须 | B-02 | 出站改回 `httpx.AsyncClient(trust_env=False)`；`async def` 里不得有同步阻塞调用；`COPILOT_CHAT_TIMEOUT` 回到分钟级并纳入 `Limits` |
| 必须 | B-03 | `direct_grant` 补 `require_two_admin_gate()` 与复核人分离校验；`intent` 默认值改回必填；`REQUEST` 不得落到直接授权分支 |
| 高 | M-01 | 域名主机 + CIDR 留空应**拒绝创建并要求显式填写**，不能默认全网段；同时把 `allowed_resolved_cidrs` 做成运行期真校验（解析后比对），否则这个字段只是摆设 |
| 高 | M-02 | 选 http 不得自动置 `allow_http`；改为独立勾选 + 明确提示"API 密钥将以明文传输" |
| 高 | M-04 | `batch-delete`/`restore` 要么实现 `username` 解析，要么对未识别项返回错误；前端成功提示必须校验 `deleted_count`/`restored_count` |
| 中 | M-03 | 若确为适配内网慢模型，补容量测算与设计说明；否则回调至分钟级 |
| 中 | M-05 | `COPILOT_ENDPOINTS_FILE` 指向的文件缺失时自动创建空清单（与未设置时行为一致），或给出"请先创建配置文件"的明确提示 |
| 中 | M-06 | `CopilotChatRequest` 加 `extra="forbid"` + 长度上限；`history` 限条数与单条长度；`role` 只接受服务端可信来源 |
| 低 | N-01 | 三处缓存参数统一为 `VERSION`；**并把"提交前跑全量 `tests/`"这条规矩恢复**——本批就是只跑了 `tests/copilot/` |
| 低 | N-02~N-07 | 闭合 `#app`；只读徽章文案与实现对齐；审计失败不得静默；`batch-approve` 加批量上限；清掉四处冗余；提示改用实例名 |

---

## 9. 一句话总结

**页面体验确实上了一个台阶，元数据那个 create 词根的 bug 也修得干净漂亮；
但 `/chat` 这条新通道把四轮 SIT、六轮 UAT 建起来的闸门整体绕过去了，
再加上端点自助管理带进来的全网段默认与明文开关、以及直接授权绕过双人复核——
这三条必须先解决，才谈得上发布。**

---

**-ClaudeA**
