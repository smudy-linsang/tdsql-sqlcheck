# SIT-v1.6.3.0 深度诊断·表类型统计（G14）第一轮 SIT 测试报告

| 项 | 内容 |
|---|---|
| 被测对象 | v1.6.3.0 G14 表类型统计子模块（提交 `0c0b3b4`） |
| 设计基线 | `DESIGN-v1.6.3.0-…详细设计说明书.md` **Rev.M** |
| 测试类型 | 第一轮 SIT（系统集成测试） |
| 测试人 | 智能体 A |
| 测试日期 | 2026-09-01 |
| 测试环境 | 本地沙箱 MariaDB 10.11.14 @13306（元数据库 + 模拟业务实例） |
| **测试结论** | **不通过。发现 1 项 BLOCK（阻断）、2 项 MINOR。BLOCK 关闭后可再次提交 SIT。** |

---

## 1. 测试结论摘要

| 级别 | 编号 | 问题 | 影响 |
|---|---|---|---|
| **BLOCK** | **DEF-SIT-01** | `/api/v1/table-type-stats/` 未登记到 `_OPERATIONAL_WRITE_PREFIXES` | **`developer` 与全部自定义角色即使拥有 `deep-diag-tabletype` 菜单权限，点击"统计表类型"仍恒定 403**。4 个内置角色中 2 个不可用 |
| MINOR | DEF-SIT-02 | 空 `connection_id` 实际返回 **422**，设计 §5 契约表写的是 400 | 契约文档与实现不符；外部调用方按文档做错误处理会落空 |
| MINOR | DEF-SIT-03 | 全空白 `connection_id` 的报错文案是"未连接TDSQL实例或连接不存在"，而非设计 E-26 声明的"必须指定 connection_id" | 服务层的 P2-03 守卫经 HTTP 路径**不可达**，提示词误导；单测覆盖的是 API 走不到的路径 |

**零次生灾害：确认成立**（详见 §3.2）。**照图施工符合度：四个新增文件与设计附录逐字一致**（§2）。

---

## 2. 照图施工符合度核验

### 2.1 新增文件与设计附录逐字比对

把设计文档附录 A.1～A.4 的代码块原样抽出，与仓库落盘文件做逐行差分：

| 附录 | 落盘文件 | 设计行数 | 落盘行数 | 结果 |
|---|---|---:|---:|---|
| A.1 | `backend/services/table_type_stats_service.py` | 1178 | 1178 | **逐字一致** ✅ |
| A.2 | `backend/api/table_type_stats.py` | 71 | 71 | **逐字一致** ✅ |
| A.3 | `backend/schema/v13/130_table_type_stats.sql` | 46 | 46 | **逐字一致** ✅ |
| A.4 | `tests/test_table_type_stats.py` | 2429 | 2429 | **逐字一致** ✅ |

### 2.2 爆炸半径核验

设计 §4.4 声明"既有文件净改 10 行 + 2 个前端块"，实测：

| 文件 | 设计声明 | 实测 | 结果 |
|---|---|---|---|
| `backend/main.py` | +2 行 | +2 行（import + include_router） | ✅ |
| `backend/services/auth_service.py` | +3 行 | +3 行（`_PATH_TO_MENU` / `ALL_MENU_KEYS` / `MENU_LABELS`） | ✅ |
| `backend/services/database.py` | +1 行 | +1 行（`all_menus`） | ✅ |
| `frontend/index.html` | 1 个纯新增块 | +93 行，0 删除 | ✅ |
| `frontend/static/js/app.js` | 4 行 + 1 个新增块 | +76 / −3（3 处就地改 + 新增块） | ✅ |

**禁改清单核查**：`backend/engine/**`、119 条规则、`audit_service`、`scan_service`、
`tdsql_connector`、`instance_type_service`、`connection_registry`、`retention_service`、
`scheduler` **均未被触碰** ✅

---

## 3. 回归测试结果

### 3.1 门禁用例

| 门禁 | 设计要求 | 实测 | 结果 |
|---|---|---|---|
| 新模块 `tests/test_table_type_stats.py` | collect 110 | **110 passed** | ✅ |
| RBAC / 路由完整性 | 全通过 | **6 passed** | ✅ |
| 119 条核心规则 | 全通过 | **94 passed, 11 skipped** | ✅ |
| 迁移登记 | `v13_130_table_type_stats` 登记且幂等 | 登记 1 行；连续两次 `init_db()` 均成功、不重放 | ✅ |
| 留档表结构 | 18 列 + 12 列、索引齐全 | 完全符合；`_ensure_schema()` 通过 | ✅ |

### 3.2 全量回归与零次生灾害判定

沙箱 MariaDB 上全量回归有 402 项失败。**经对照实验证明与 G14 无关**：

| 提交 | 说明 | 全量结果 |
|---|---|---|
| `955e7fe` | G14 落盘**之前** | 406 failed / 1079 passed |
| `0c0b3b4` | G14 落盘**之后** | **402 failed / 1193 passed** |

**失败数不增反减 4 项，通过数净增 114 项**（110 为 G14 新增）。

**根因**：沙箱 MariaDB 上 `_create_all_tables` 建 `instance_gate_rules` 时报
`errno 150 (Foreign key constraint is incorrectly formed)` 而中断，导致
`_init_default_data` 未创建默认 admin，所有需登录的用例 401。
该现象在 G14 落盘前即存在，且 **MariaDB 从来不是本项目支持的元数据库目标**
（生产为 TDSQL/MySQL，`10.243.20.15:15197`）。

> 另有一处沙箱专属现象：`v4_040_instance_type_scope` 迁移在 MariaDB 上因
> `int(11)` vs `int` 显示宽度差异而失败关闭。同样在 G14 落盘前即存在。
> 本轮通过**仅在内存打补丁**的 pytest 插件抹平该差异后再测，未修改仓库任何文件。

**结论：G14 未产生任何次生灾害。**

---

## 4. 功能测试结果（真实 HTTP 栈 + 真实数据库）

单测大量使用 `FakePool`，故本轮另起真实链路验证：注册本地 MariaDB 为实例，
经 `TestClient` 打通"认证 → RBAC → 连接 → 采集 → 落库 → 回看"全链路。

### 4.1 端到端统计（集中式分支）

构造业务库：`sit_biz_a`（3 张表 + 1 个视图 + 1 张 `orders_tdsql_subp202601`）、`sit_biz_b`（2 张表）。

| 用例 | 期望 | 实测 | 结果 |
|---|---|---|---|
| 全业务库统计 | 采集成功并落库 | 11 库 / 139 表，`stat_id=1`，无告警 | ✅ |
| 视图不计入 | `sit_biz_a` 计 4 而非 5 | **4** | ✅ |
| **集中式不剔除 `_tdsql_subp` 表**（P1-03） | 计入总表，`subpartition_tables=0` | **总表 4、子分区 0** | ✅ **真库验证通过** |
| 指定单库 `sit_biz_a` | 只统计该库 | 库数 1 / 总表 4 | ✅ |
| 数字独立对账 | 与直查 `information_schema` 真值一致 | `sit_biz_a` 4=4、`sit_biz_b` 2=2 | ✅ |
| 历史回看 | 可回看且 `created_by` 为当前用户 | 2 条记录，`created_by='admin'`，`database_filter` 正确 | ✅ |
| 明细抽屉 | 可拉取逐库明细 | 明细行数与 items 一致 | ✅ |

### 4.2 错误分支与边界

| 用例 | 设计声明 | 实测 | 结果 |
|---|---|---|---|
| 指定不存在的库 | E-3：400 | 400 `数据库不存在或当前账号不可见: no_such_db_xyz（SHOW DATABASES 未返回该库）` | ✅ |
| **指定大小写不同的库** | E-33：400 且点名变体 | 400 `…SIT_BIZ_A…；实例上存在大小写不同的同名库: sit_biz_a` | ✅ **P1-01 真库验证通过** |
| 指定系统库 | E-2：400 | 400 `不允许统计系统库: mysql` | ✅ |
| 连接不存在 | E-1：400 | 400 `未连接TDSQL实例或连接不存在` | ✅ |
| 未认证 | 401 | 401 | ✅ |
| `detail` 取不存在的 id | E-16：200 空结构 | 200 `{"items":[],"warnings":[]}` | ✅ |
| 空 `connection_id` | §5：**400** | **422** | ❌ **DEF-SIT-02** |
| 全空白 `connection_id` | E-26：400 且提示"必须指定" | 400 但提示"未连接TDSQL实例或连接不存在" | ❌ **DEF-SIT-03** |

### 4.3 分布式分支失败路径

将同一连接标记为分布式（指向非 Proxy 端点），三条 `/*proxy*/` 命令必然 1064：

| 观察项 | 期望 | 实测 | 结果 |
|---|---|---|---|
| 告警 | `PROXY_CMD_FAILED` + `NOT_DISTRIBUTED_ENDPOINT` | 两条均出现，文案准确 | ✅ |
| 失败库计数 | `failed_databases=1` | 1 | ✅ |
| **失败库不进汇总**（P1-03） | `total=0` **且 `baseline=0`** | 总表 0、基线 0 | ✅ **真库验证通过** |
| 逐库状态 | FAILED 且 detail 含 errno | `FAILED` + `[errno 1064] 语法错误（该连接可能非 Proxy 端点）` | ✅ |

### 4.4 并发限流

| 用例 | 期望 | 实测 | 结果 |
|---|---|---|---|
| 占满每连接 2 个槽位后第 3 个请求 | 429 | **429** `目标库 … 扫描并发已达上限(2)，请稍后重试` | ✅ |
| 槽位释放后再请求 | 200 | 200 | ✅ |

### 4.5 前端

| 检查项 | 实测 | 结果 |
|---|---|---|
| `node --check frontend/static/js/app.js` | 通过 | ✅ |
| 页签块（93 行）引用的 19 个标识符是否都在 `setup()` 返回清单 | **全部在**，无悬挂引用 | ✅ |
| `subtabs` 回退清单登记（P1-06） | `perm:'deep-diag-tabletype',tab:'tabletype'` 已登记 | ✅ |
| P2-02 历史抽屉清理数据源 | `tabletypeDetailAll.value=[]` 出现在打开与切行两处 | ✅ |
| P2-03 / Rev.L P2-01 结果提示分流 | `n===0`→warning、`ok<=0`→error、其余→success 三分支齐备 | ✅ |

---

## 5. 缺陷详述与解决方案

### DEF-SIT-01（BLOCK）`/api/v1/table-type-stats/` 未登记到 `_OPERATIONAL_WRITE_PREFIXES`

#### 5.1.1 现象

自定义角色与 `developer` 角色**已经拥有** `deep-diag-tabletype` 菜单权限
（页面上"表类型统计"页签正常显示），但点击"统计表类型"按钮恒定返回 **HTTP 403**。

#### 5.1.2 定位与证据

`backend/services/auth_service.py::check_permission()` 是**两级**判定：

```
第一级：角色级放行
    role == "admin"                        → 直接 True
    role == "dba"                          → allowed = True
    role == "auditor"                      → allowed = (只读方法)
    role == "developer" / 自定义角色        → 写方法时：
        allowed = any(path.startswith(p) for p in _DEVELOPER_WRITE_PREFIXES)   ← 卡在这里
    if not allowed: return False           ← 直接 403，根本走不到第二级

第二级：role_permissions 菜单可见性校验（_PATH_TO_MENU → get_visible_menus）
```

`_DEVELOPER_WRITE_PREFIXES = _OPERATIONAL_WRITE_PREFIXES`（`auth_service.py:299`），
而该元组（`auth_service.py:278-298`）列出了既有**全部 9 个**深度诊断写端点：

```
"/api/v1/cluster-inspect/"   G3      "/api/v1/emergency/"    G7
"/api/v1/daily-inspect/"     G4      "/api/v1/sql-stats/"    G8
"/api/v1/index-audit/"       G5      "/api/v1/gateway-log/"  G11
"/api/v1/schema-diff/"       G6      "/api/v1/ppt-report/"   G12
"/api/v1/toolkit/"           G13
                                     ← /api/v1/table-type-stats/ （G14）缺失
```

**受控对照实验**（`developer` 角色同时拥有 `deep-diag-tabletype` 与 `deep-diag-index`
两个菜单权限，唯一差异就是本清单是否登记）：

| 角色 | `POST /api/v1/table-type-stats/run` | `POST /api/v1/index-audit/run` | `GET /…/history` |
|---|---|---|---|
| admin | 允许 | 允许 | 允许 |
| dba | 允许 | 允许 | 允许 |
| **developer** | **拒绝** ❌ | 允许 | 允许 |
| auditor | 拒绝（只读角色，与 G5 同） | 拒绝 | 允许 |

**在内存中补登记 `"/api/v1/table-type-stats/"` 后重测**：

| 角色 | `POST /…/run` | 结果 |
|---|---|---|
| **developer** | **允许** | ✅ 与 G5 恢复对等 |
| auditor | 拒绝 | ✅ 与 G5 对等（只读角色本就应拒绝，非缺陷） |

#### 5.1.3 影响面

* **`developer` 角色**：4 个内置角色之一，功能完全不可用；
* **全部自定义角色**：`check_permission` 的 `else` 分支用的是同一个
  `_DEVELOPER_WRITE_PREFIXES`，因此**任何自定义角色都不可用**——
  包括设计 §12.2 专门要求验收的"仅授予 `deep-diag` + `deep-diag-tabletype` 的自定义角色"；
* 只有 `admin`（短路返回 True）与 `dba`（短路 `allowed = True`）能用；
* **表现极具迷惑性**：页签正常显示、历史查询正常（GET 在第一级放行），
  唯独点击统计按钮 403，现场很容易误判为"权限矩阵没配对"而反复折腾配置。

#### 5.1.4 根因归属

**这不是 Q 的编码错误。** 逐字比对已证明 Q 的落盘代码与设计附录完全一致。
根因是**设计遗漏**：设计 §2.2 把"一个子模块需要登记的点"列为 6 处
（`_PATH_TO_MENU` / `ALL_MENU_KEYS` / `MENU_LABELS` / `database.py all_menus` /
`index.html` 页签 / `app.js subtabs`），**唯独没有列出 `_OPERATIONAL_WRITE_PREFIXES`**。
Q 按图施工，图上没有这一处。

这是"登记点枚举不全"的第三次复发（DEF-1 迁移槽位、P1-06 `subtabs`、本次），
与设计 KL-17 记录的教训同源。

#### 5.1.5 解决方案（照图施工）

**① 代码修改（1 行，`backend/services/auth_service.py`）**

在第 297 行 `"/api/v1/toolkit/",                     # 深度诊断-工具箱` 之后、
第 298 行 `)` 之前，插入一行：

```python
    "/api/v1/table-type-stats/",            # 深度诊断-表类型统计
```

修改后该元组末尾应为：

```python
    "/api/v1/sql-stats/",                   # 深度诊断-SQL统计
    "/api/v1/toolkit/",                     # 深度诊断-工具箱
    "/api/v1/table-type-stats/",            # 深度诊断-表类型统计
)
_DEVELOPER_WRITE_PREFIXES = _OPERATIONAL_WRITE_PREFIXES
```

> **注意末尾斜杠必须保留**。判定用的是 `path.startswith(prefix)`，
> 不带斜杠会让 `/api/v1/table-type-statsXXX` 之类的路径也被前缀命中。
> 全部既有条目均带尾斜杠，保持一致。

**② 测试加固（`tests/test_table_type_stats.py`）**

现有 `test_r08_permission_key_is_registered_at_every_point` 只检查 4 个文件中
**权限键字符串**是否出现，检查不到本项（本项登记的是**路径前缀**不是权限键）。
在该用例之后新增一条**行为级**用例（不依赖字符串扫描，直接断言判定结果）：

```python
def test_sit01_write_endpoint_is_reachable_by_non_admin_roles():
    """DEF-SIT-01：G14 写端点必须与既有深度诊断子模块处于同一放行清单。

    check_permission 是两级判定：第一级按角色 + _DEVELOPER_WRITE_PREFIXES 放行，
    第二级才查 role_permissions 菜单可见性。只登记 _PATH_TO_MENU 而不登记
    _OPERATIONAL_WRITE_PREFIXES 时，developer 与全部自定义角色会卡在第一级，
    拿到 403——而页签仍然显示，现场极易误判为权限矩阵没配对。

    本用例刻意用【与既有 G5 对照】的方式断言，而不是硬编码"必须为 True"：
    G14 与既有深度诊断子模块的可达性口径应当完全一致，将来平台整体调整
    角色策略时，这条断言会随之一起变，不会变成需要人工维护的死值。
    """
    from backend.services import auth_service as A

    G14 = "/api/v1/table-type-stats/run"
    G5 = "/api/v1/index-audit/run"          # 既有深度诊断写端点，作为基准
    for role in ("admin", "dba", "developer", "auditor"):
        assert A.check_permission(role, "POST", G14) == \
               A.check_permission(role, "POST", G5), \
            f"角色 {role} 对 G14 写端点的可达性与既有深度诊断子模块不一致"

    # 前缀本身必须登记，且带尾斜杠（判定用 startswith，不带会误命中兄弟路径）
    assert "/api/v1/table-type-stats/" in A._OPERATIONAL_WRITE_PREFIXES
    assert A._DEVELOPER_WRITE_PREFIXES is A._OPERATIONAL_WRITE_PREFIXES
```

**③ 设计文档同步（`DESIGN-v1.6.3.0-…Rev.M` → Rev.N）**

* **§2.2 由"6 个登记点"改为"7 个登记点"**，新增一行：

  | # | 文件:行 | 内容 | 缺失后果 |
  |---|---|---|---|
  | **P7** | `backend/services/auth_service.py:278-298` 的 `_OPERATIONAL_WRITE_PREFIXES` | 业务操作性**写端点**放行清单（`_DEVELOPER_WRITE_PREFIXES` 即其别名） | **致命**：`developer` 与全部自定义角色在 `check_permission` **第一级**即被拒，写端点恒 403；而菜单可见性正常、页签照常显示，现场极易误判为权限配置问题。admin/dba 因短路放行而测不出来 |

* **ADR-21** 的"6 处"改为"7 处"，并补一句：
  *"第一级放行清单（`_OPERATIONAL_WRITE_PREFIXES`）与第二级菜单可见性
  （`_PATH_TO_MENU` + `role_permissions`）是**两套独立机制**，缺任一处都不可用；
  只有 admin/dba 会短路跳过第一级，故必须用 developer 或自定义角色验收。"*

* **§12.2 权限验收**新增一条：
  `- [ ] developer 角色登录后，点击"统计表类型"按钮可正常执行（非 403）`

* **KL-17**（登记点枚举不全的教训）补记本次复发，并补充可执行的防复发做法：
  *"新增子模块时，用既有同类子模块（如 G5 `index-audit`）做**全仓库 grep 对照**，
  逐处确认登记面一致，而不是依赖设计文档里的清单。"*

#### 5.1.6 修复后验收步骤

```bash
# 1. 权限矩阵恢复对等
python -m pytest tests/test_table_type_stats.py -q          # 期望 111 passed（+1 新用例）
python -m pytest tests/test_rbac_path_coverage.py tests/test_app_routes_integrity.py -q

# 2. 人工：以 developer 账号登录 → 深度诊断 → 表类型统计 → 点击"统计表类型"
#    期望：正常出数，不再 403
# 3. 人工：仅授予 deep-diag + deep-diag-tabletype 的自定义角色，重复上述操作
```

---

### DEF-SIT-02（MINOR）空 `connection_id` 返回 422，设计契约表写的是 400

#### 现象与证据

| 输入 | 设计 §5 声明 | 实测 |
|---|---|---|
| `connection_id: ""` | 400 `必须指定 connection_id（…）` | **422** `string_too_short` |
| 缺 `connection_id` 字段 | （未声明） | 422 `Field required` |

原因：`StatsRequest.connection_id` 声明为 `Field(..., min_length=1)`，
FastAPI 的请求体校验在进入路由函数**之前**就返回 422，服务层的 400 分支走不到。

#### 影响

实现本身没有安全或正确性问题（都是拒绝），但**契约文档与实现不符**。
按文档做错误处理的外部调用方（或将来的前端改造）会漏掉 422 分支。
当前前端有 `if(!deepConnId.value)` 前置拦截，故用户侧无感知。

#### 解决方案（二选一，**推荐 ①**）

**① 修文档（推荐）**——实现是合理的，422 正是 FastAPI 对请求体校验失败的标准语义。
在设计 §5 的错误表中，把该行改为：

```
| 422 | `string_too_short` / `Field required`（FastAPI 请求体校验） | `connection_id` 缺失或为空串——由 `StatsRequest` 的 `min_length=1` 在进入路由前拦截 |
| 400 | `必须指定 connection_id（…）` | `connection_id` 为**全空白**字符串（通过了 min_length 但 `.strip()` 为空），由服务层拦截 |
```

同时修正 §8 的 **E-26** 行，把"HTTP 400"改为"HTTP 422（空串/缺字段）或 400（全空白）"。

**② 改实现**——若坚持对外只暴露 400，则把 `min_length=1` 从模型上去掉、
改由路由函数内显式校验后抛 `HTTPException(400)`。
**本轮不推荐**：会削弱 OpenAPI 契约的自描述能力，且与项目其他接口的 422 语义不一致。

---

### DEF-SIT-03（MINOR）全空白 `connection_id` 的报错文案误导，服务层守卫经 HTTP 不可达

#### 现象与证据

```
POST /run  {"connection_id": "   ", "database": ""}
  实测：400  {"detail": "未连接TDSQL实例或连接不存在"}
  设计 E-26 声明：400，提示"必须指定 connection_id"
```

原因：`backend/api/table_type_stats.py::run()` 的调用顺序是

```python
pool = _pool(body.connection_id)          # ← 先解析连接：registry.get("   ") 抛
                                          #   ConnectionNotFoundError → 400 未连接…
try:
    return svc.run_stats(pool, ...)       # ← 服务层的"必须指定 connection_id"守卫
                                          #   永远走不到
```

因此服务层 `run_stats()` 里那段 Rev.G 为关闭 O 首轮 P2-03 而加的守卫，
**经 HTTP 路径不可达**；单测 `test_r11_empty_connection_id_is_rejected` 直接调用
`svc.run_stats()`，测的是 API 走不到的路径，**给出的是虚假信心**。

#### 影响

功能与安全无影响（都是 400 拒绝）。但提示词误导：用户输入了空白，
被告知"未连接TDSQL实例"，排查方向会跑偏到连接管理页。

#### 解决方案（照图施工）

**修改 `backend/api/table_type_stats.py`**，把入参口径校验提到连接解析**之前**。
将 `run()` 函数体开头由：

```python
@router.post("/run", summary="发起表类型统计")
def run(body: StatsRequest, http_request: Request):
    pool = _pool(body.connection_id)
    try:
```

改为：

```python
@router.post("/run", summary="发起表类型统计")
def run(body: StatsRequest, http_request: Request):
    # DEF-SIT-03：入参口径校验必须先于连接解析。否则 registry.get("   ") 会先抛
    # ConnectionNotFoundError，用户输入空白却被告知"未连接TDSQL实例"，排查方向跑偏；
    # 服务层同名守卫也因此在 HTTP 路径上永远不可达（单测直调服务层，测不出来）。
    if not body.connection_id.strip():
        raise HTTPException(
            status_code=400,
            detail="必须指定 connection_id（本模块不接受默认连接："
                   "连接解析与实例类型解析在空 ID 下可能指向不同实例）")
    pool = _pool(body.connection_id)
    try:
```

> 服务层 `run_stats()` 里的同名守卫**保留不动**——它是服务被直接调用（如将来接入
> 定时任务）时的兜底，与 API 层的校验互不替代。

**测试加固**：在 `tests/test_table_type_stats.py` 中新增

```python
def test_sit03_blank_connection_id_reports_the_right_reason(monkeypatch):
    """DEF-SIT-03：全空白 connection_id 必须报"必须指定"，而不是"未连接实例"。

    Rev.M 的 API 先做连接解析、后做入参校验，于是服务层守卫在 HTTP 路径上不可达，
    用户输入空白却被指向连接管理页。这条用例直接打 API 层，覆盖真实调用顺序。
    """
    from fastapi import HTTPException
    from backend.api import table_type_stats as api

    called = {"pool": 0}
    monkeypatch.setattr(api, "_pool", lambda cid: called.__setitem__("pool", 1))
    for blank in ("   ", "\t", " \n "):
        with pytest.raises(HTTPException) as e:
            api.run(api.StatsRequest(connection_id=blank), _FakeRequest("u"))
        assert e.value.status_code == 400
        assert "必须指定 connection_id" in e.value.detail
    assert called["pool"] == 0, "入参不合格时不得先去解析连接"
```

---

## 6. 环境说明（不构成缺陷）

本轮沙箱为 MariaDB 10.11.14，**不是项目支持的元数据库目标**（生产为 TDSQL/MySQL）。
以下两项为环境专属现象，已用对照实验证明在 G14 落盘前即存在：

1. `_create_all_tables` 建 `instance_gate_rules` 报 `errno 150`，导致默认 admin 未创建；
2. `v4_040_instance_type_scope` 迁移因 `int(11)` vs `int` 显示宽度差异而失败关闭。

本轮以**仅内存打补丁**的 pytest 插件抹平第 2 项、手工补建 admin 绕过第 1 项，
**未修改仓库任何文件**。

**因此，落盘后的完整回归仍须在 TDSQL/MySQL 元数据库上重跑一次**——
本报告的门禁数据只能证明"G14 自身及其对既有功能的影响"，
不能替代目标环境的全量回归。

---

## 7. 遗留与后续

| 项 | 状态 |
|---|---|
| DEF-SIT-01（BLOCK） | **待 Q 修复**，修复后需重新提交 SIT |
| DEF-SIT-02 / DEF-SIT-03（MINOR） | 建议随 BLOCK 一并修复 |
| 内网 UAT `lzbj_ecif` 六个数字对账 | 未做（无 TDSQL 环境），落盘部署后执行 |
| T20 基线谓词性能证据 | 未做（需最大内网实例），发布前门禁 |
| T13 命令作用域 | 不阻断、不影响任何数字 |
| 目标环境（TDSQL/MySQL）全量回归 | 未做，部署后必须补 |

**建议**：三项缺陷合并为一次修订。DEF-SIT-01 是 1 行代码 + 1 条测试 + 设计文档
§2.2/ADR-21/§12.2/KL-17 的同步；DEF-SIT-02 是纯文档；DEF-SIT-03 是 API 层 4 行
+ 1 条测试。修复后重跑本报告 §3、§4 全部用例即可。
