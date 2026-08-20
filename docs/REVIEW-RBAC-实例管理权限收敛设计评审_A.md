# 《实例管理权限收敛与全模块实例读取解耦》设计评审意见（智能体 A）

| 项 | 内容 |
|---|---|
| 评审对象 | `docs/DESIGN-RBAC-实例管理权限收敛与全模块实例读取解耦详细设计说明书.md` V1.0（G 编写） |
| 基线代码 | `main` @ `993022d`（VERSION 1.6.1.7） |
| 评审人 | 智能体 A（结论均以实测为依据，未采信文档自述） |
| 日期 | 2026-08-20 |
| **结论** | 🟡 **方向认可，但不能照此实施**——存在 **1 项 P0**（照做会把 MonitorDB 口令密文广播给全部角色）、**3 项 P1**、**2 项 P2**。修订后可开工 |

---

## 1. 总体判断

**架构方向是对的**：读写分离、写操作收敛到 admin/dba、只读元数据与菜单解耦——这三条判断我认可，也确实能满足你提的两个要求。

**两处根因定位准确，我逐一核对过**：

| 文档结论 | 核对结果 |
|---|---|
| `frontend/index.html:1095-1136` 按钮未声明 `v-if="canManageInstances"` | ✅ **属实**。全页只有「探测类型」「探测诊断」挂了该门禁，「+ 新建连接」「编辑」「删除」「从 ZK 自动发现」「设为默认」「连接」全都没有 |
| `frontend/static/js/app.js:1436` 用 `if(visibleMenus.has('instances'))` 卡住了 `loadSavedConnections()` | ✅ **属实**，行号都对 |

**但实施清单不能照抄**——见 §3。最关键的是 §3.1 那条：设计把"放开只读"建立在"载荷已脱敏"这个前提上，而**这个前提经实测不成立**。

---

## 2. 需要更正的问题定性（§1.1）

文档把问题一定性为"**写权限越权（安全风险）**"，并称非管理员"**能够看到并执行**"高危操作。

**我在 v1.6.1.7 上把实例管理相关的全部写端点逐个打了一遍，非 admin/dba 角色一个也执行不了：**

```
角色 bizops（自定义角色，已分配「实例管理」菜单）
  新建连接 POST -> 403     更新连接 PUT -> 403      删除连接 DELETE -> 403
  设为默认 POST -> 403     设为默认(别名) POST -> 403  激活连接 POST -> 403
  探测实例类型 POST -> 403  锁定实例类型 PUT -> 403   探测诊断 POST -> 403
  monitordb连通性 POST -> 403  直接连接 POST -> 403   断开 POST -> 403
  测试连接 POST -> 403     ZK自动发现 POST -> 403    ZK保存配置 PUT -> 403
疑似放行的写端点：0 个
```

`developer`、`auditor` 同样全部 403。我特意挑了那些容易漏登记 `_PATH_TO_MENU`、会掉进 `check_permission` 末尾 fail-open 兜底的旁路端点，**一个都没漏网**。

**所以真实性质是「按钮暴露」而不是「越权」**：用户看得见、点得着，点下去弹 403 失败。文档 §2.2-2 自己也写了"虽然非管理员在某些默认配置下无法写"——这句含糊，应当改为明确结论。

**建议**：§1.1 标题与描述改为"**实例管理写操作入口未做角色收敛（体验缺陷 + 纵深防御缺口）**"，把"越权"的定性撤回。理由有二：

1. 银行内部对"发生过越权"和"存在越权入口"的定级与追责完全不同，不该背不实的锅；
2. 定性错了会带偏优先级——真正必须先修的是**前端按钮**与**§3.1 的口令泄露**，而不是"补一道本来就在的后端拦截"。

> ⚠️ **请内网确认一件事**：当时观察到的是"点了报错"，还是"连接真的从列表里消失了"？如果是后者，说明内网跑的不是这一版，或那个角色被建成了 `dba`（`dba` 在代码里对写操作是全放行的——这符合你"只能管理员和数据库管理员"的要求）。这个确认结果会影响是否需要追加应急排查。

---

## 3. 必须修订的问题

### 3.1 【P0】§3.1 / §4.2 的"载荷已脱敏"前提不成立——照做会把 MonitorDB 口令密文发给所有角色

文档在两处把"放开只读"建立在同一个前提上：

> §3.1：「载荷：脱敏实例清单（**密码字段已过滤**）」
> §4.2：「**密码已在 SQL 过滤层与 `registry.list_saved()` 中脱敏**」

**这个前提是错的。** `connection_registry.list_saved()`（第 371-390 行）执行 `SELECT c.*`，而后**只 pop 了 `password_encrypted` 一个字段**：

```python
d = dict(r)
d.pop("password_encrypted", None)
d["password"] = "***"
```

`tdsql_connections` 表里还有一个 **`monitor_password_encrypted`**，它没被 pop。实测 `GET /api/v1/tdsql/connections` 的真实响应：

```
monitor_password_encrypted = 'gAAAAABqhr71SPdoM-64X7gLXIMajnCttp9D_cizofiMQfAhy7owoxzMHxuXBgphfoWBjtAdhj6n4kfG'
monitor_user               = 'tdsqlpcloud'
password                   = '***'          ← 只有这一个被脱敏了
username / user            = 'checksql'
```

**按文档实施的后果**：把这个端点放开给"所有已登录用户"，等于把内网 209 个实例的 **MonitorDB 口令密文 + 监控账号名 + 监控主机端口**，一次性发给 developer、auditor 和任何自定义角色。Fernet 密钥就在同一台应用服务器上，这在银行环境属于**明确的数据分级违规**——比原本要修的问题严重得多。

**修订要求（二选一，推荐方案 B）**：

**方案 A（最小改动）**：先修 `list_saved()` 的字段投影，把 `monitor_password_encrypted` 一并去掉，**再**放开只读。
- 优点：改动小，前端 18 处下拉不用动。
- 仍有残留：`host / port / database / username / monitor_host / monitor_user` 等仍会暴露给全部角色，属信息披露面扩大，**需安全组书面确认可接受**。

**方案 B（推荐）**：**新增一个专用的精简只读端点**，例如 `GET /api/v1/tdsql/connections/options`，只投影下拉框真正需要的字段：

```python
# 只返回：id, name, host, port, database, effective_instance_type
# 不绑菜单；已登录即可读
```

- 全字段的 `GET /connections` **保持**绑定 `instances` 菜单不变；
- 前端 18 处下拉改用新端点，实例管理页仍用旧端点；
- 天然解决口令泄露、天然避免 §3.3 的前缀连带放开、信息披露面最小；
- 代价：前端 `loadSavedConnections()` 要拆成两个（`loadConnectionOptions()` / `loadSavedConnections()`），改动比方案 A 大，但一次到位。

> 无论选哪个方案，`monitor_password_encrypted` 出现在列表响应里**本身就是个应该修的缺陷**（与本设计无关，现有版本就有），建议单独记一条修掉。

### 3.2 【P1】§4.2 写端点清单漏 11 个、错 2 条

我从路由源码里把实例管理相关的写端点全量扫了一遍，**共 20 个**；文档只列了 9 条。

**漏登记（11 个）**：

```
POST   /api/v1/tdsql/connections/{id}/default              ← set-default 的别名路由，极易漏
PUT    /api/v1/tdsql/connections/{id}/instance-type-lock
POST   /api/v1/tdsql/connections/{id}/probe-instance-type
POST   /api/v1/tdsql/connections/{id}/probe-diagnostics
POST   /api/v1/tdsql/connections/{id}/monitor-probe
POST   /api/v1/tdsql/connect-from-config
POST   /api/v1/tdsql/disconnect
POST   /api/v1/tdsql/test-connection
PUT    /api/v1/tdsql/discover/config
POST   /api/v1/tdsql/discover/name-diagnose
POST   /api/v1/tdsql/discover/register
```

**路径写错（2 条）**：

| 文档写的 | 真实路径 |
|---|---|
| `POST /api/v1/tdsql/import-commit` | `POST /api/v1/tdsql/discover/import-commit` |
| `POST /api/v1/tdsql/import-preview` | `POST /api/v1/tdsql/discover/import-preview` |

（`zk_discovery.py` 的 router 前缀是 `/api/v1/tdsql/discover`。）按文档字面实现，这两条白名单**不会命中任何真实路由**。

**修订要求**：清单按上面补全；并且**不要靠人工维护清单**——建议把"实例管理写端点必须仅 admin/dba"做成**扫描式断言**（参照现成的 `tests/test_rbac_path_coverage.py::test_all_write_endpoints_are_mapped` 的做法），新增端点漏登记时在开发期即失败。

### 3.3 【P1】§4.2 的通配写法会破坏现有的前缀消歧机制

文档写的是 `POST /api/v1/tdsql/connect*` 与 `POST /api/v1/tdsql/discover*`（带星号）。

现有 `check_permission` 的匹配语义是**按前缀长度降序 + 边界匹配**：

```python
if path == prefix or path.startswith(prefix + "/"):
```

这个边界匹配是**刻意为之**的——因为 `/api/v1/tdsql/connect` 恰好是 `/api/v1/tdsql/connections` 的前缀：

```
'/api/v1/tdsql/connections'.startswith('/api/v1/tdsql/connect')        = True   ← 朴素前缀会误伤
边界匹配 (path==pre or startswith(pre+'/'))                             = False  ← 现有实现正确规避
```

如果新的写白名单实现成朴素 `startswith("/api/v1/tdsql/connect")`，**`/api/v1/tdsql/connections` 的读端点会被这条写白名单一并吃掉**，问题二当场复发。

**修订要求**：白名单必须沿用与 `_PATH_TO_MENU` **同一套**边界匹配语义（或改用带路径参数的精确路由模板匹配），并在文档里写死这一点，不要用含糊的 `*` 通配表述。

### 3.4 【P1】"把 `/api/v1/tdsql/connections` 整体调整为全局只读"会连带放开有副作用的 GET

`_PATH_TO_MENU` 是前缀匹配。按文档 §4.2 第一项"从字典中将 `/api/v1/tdsql/connections` 调整为全局基础只读服务"，同前缀下的这些 GET 会**一并**被放开：

```
GET /api/v1/tdsql/connections                       ← 本意要放开的
GET /api/v1/tdsql/connections/{id}/probe            ← ★会真的去连 MonitorDB，有副作用
```

`GET /connections/{id}/probe`（`tdsql_manage.py:920`）**没有任何端点级鉴权**，放开后任何登录用户都能驱动服务器去连内网 MonitorDB——可被用作内网探测。

**修订要求**：只放开**列表端点本身**（精确匹配 `path == "/api/v1/tdsql/connections"` 且 `method == "GET"`），不要放开整个前缀。方案 B（新增 `/options` 端点）天然没有这个问题。

> 顺带表扬一处现有的好做法：`GET /api/v1/tdsql/discover/config` 在端点内有 `_require_admin(request)` 双保险。**建议实例管理的写端点也照此加一层端点级 `_require_admin_or_dba`**——中间件 + 端点双保险，正是文档 §2.2-2 说的"防御性编程"，这条我赞成。

### 3.5 【P2】§5 "对现有业务完全无破坏性"的断言缺乏依据

放开只读后，`auditor`、`developer`、自定义角色将**首次**能看到全部实例的 IP、端口、库名、业务账号名。这**不是零影响**，而是信息披露面的实质扩大。

**修订要求**：§5 应如实写明"**哪些角色新增能看到哪些字段**"，并明确这是经安全组确认可接受的取舍，而不是笼统一句"无破坏性"。方案 B 可把这段收敛到 6 个字段，好说话得多。

### 3.6 【P2】§6 验收方案全是正向，缺反向鉴别

TC-01~TC-05 全部是"应该能"，没有一条是"**不应该能**"。这类权限改动最容易出的事故恰恰是"放开过了头"，正向用例一条都抓不到。

**必须补的反向鉴别用例**：

| 编号 | 场景 | 预期 |
|---|---|---|
| TC-06 | 放开只读后，检查 `GET /connections`（或 `/options`）**响应体字段清单** | **不含任何 `*password*` / `*secret*` 字段**（含 `monitor_password_encrypted`） |
| TC-07 | 普通角色调 `GET /connections/{id}/probe` | 仍 **403**（放开读不得带出有副作用的 GET） |
| TC-08 | 普通角色调 §3.2 补全后的**全部 20 个写端点** | **逐个 403**，一个不漏 |
| TC-09 | `dba` 角色调同样 20 个写端点 | **仍然全部放行**（防止白名单把 dba 一起挡了） |
| TC-10 | 不带 token / 过期 token 调 `GET /connections` | **401**（防止"所有登录用户可读"被实现成写进 `PUBLIC_PATHS` 的公开端点） |
| TC-11 | 已分配 `instances` 菜单的普通角色，页面上按钮数量 | 与 admin 对比，**写操作按钮数为 0**，且列表数据仍完整可见 |
| TC-12 | 改动后跑 `tests/test_rbac_path_coverage.py` 与 `test_v2_rbac_matrix.py` | **仍全绿**，且这两个套件要**跟着扩充**而不是被改松 |

TC-09 和 TC-10 尤其重要：前者防"矫枉过正"，后者防"解耦解过头"。

---

## 4. 另外两条实现提示

1. **`canManageInstances` 是前端硬编码角色名**（`app.js:385`：`['admin','dba'].includes(authState.role)`）。当前与后端一致，可以用。但将来若要支持"某个自定义角色也能管实例"，前后端两处都得改，且**前端判断永远只是体验层，后端必须独立成立**——文档 §4.1 最后一句"绝不展示变更入口"容易被读成"藏了就安全了"，建议补一句"前端隐藏仅为体验，鉴权以后端为准"。

2. **`loadAll()` 改为无条件 `loadSavedConnections()` 之后**，请确认 `watch(currentPage)`（`app.js:1612`）里 `if(v==='instances')loadSavedConnections()` 的重复拉取不会造成首屏两次请求；209 条数据量下不致命，但顺手合一下更干净。

---

## 5. 修订后可开工的判据

| # | 修订项 | 级别 |
|---|---|---|
| 1 | §3.1：先解决 `monitor_password_encrypted` 泄露（推荐方案 B 新增 `/options` 精简端点） | **P0，必改** |
| 2 | §3.2：写端点清单补全至 20 个、修正 2 条错误路径，并改为扫描式断言 | **P1，必改** |
| 3 | §3.3：白名单匹配沿用边界匹配语义，去掉 `*` 通配表述 | **P1，必改** |
| 4 | §3.4：只精确放开列表端点，不放开整个前缀 | **P1，必改** |
| 5 | §1.1：问题定性由"越权"更正为"写操作入口未收敛" | P2 |
| 6 | §5：如实写明信息披露面的扩大范围 | P2 |
| 7 | §6：补 TC-06~TC-12 七条反向鉴别用例 | P2 |

以上 7 条改完，我认为方案可以实施，能同时解决你提的两个问题，且不会伤到现有功能。

**实施完成后我会按 §3.6 的 TC-06~TC-12 逐条实测复验**，重点是响应体字段清单、20 个写端点的 403、dba 仍可写、以及未登录仍 401 这四项。

---

## 6. 评审依据（如实声明）

| 项 | 说明 |
|---|---|
| 代码基线 | `main` @ `993022d`（v1.6.1.7），文档标注适用版本 V1.6.1.7+，一致 |
| 权限现状 | 起真实服务（`uvicorn --workers 2`、AUTH_ENABLED=true），建 developer / auditor / 自定义角色 `bizops` 三类用户，逐端点打真实 HTTP，非推断 |
| 口令泄露 | 经真实接口建带 MonitorDB 口令的连接后，读 `GET /connections` 响应体核实，非读代码推断 |
| 端点清单 | 从 `backend/api/tdsql_manage.py` 与 `zk_discovery.py` 的路由装饰器全量扫描得出 |
| 未覆盖 | 内网实际观察到的现象（"点了报错"还是"真删掉"）我无法证实，需内网确认；本评审仅针对设计文档与 v1.6.1.7 代码 |
