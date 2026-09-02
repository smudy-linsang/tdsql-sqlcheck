# SIT2-v1.6.3.0 深度诊断·表类型统计（G14）第二轮 SIT 测试报告

| 项 | 内容 |
|---|---|
| 被测对象 | v1.6.3.0 G14 表类型统计子模块，**第一轮 SIT 整改版**（提交 `e94f3b6`） |
| 上一轮基线 | 落盘 `0c0b3b4` / 第一轮 SIT 报告 `35e05ad`（结论：不通过，1 BLOCK + 2 MINOR） |
| 设计基线 | `DESIGN-v1.6.3.0-…详细设计说明书.md` **Rev.N**（SIT 整改定版） |
| 整改方报告 | `docs/FIX-v1.6.3.0-…第一轮SIT整改完成情况报告-ClaudeQ.md` |
| 测试类型 | 第二轮 SIT（回归验证 + 整改爆炸半径核验 + 功能复测 + 真实浏览器验收） |
| 测试人 | 智能体 A |
| 测试日期 | 2026-09-02 |
| 测试环境 | 本地沙箱 MariaDB 10.11.14 @13306（元数据库 + 模拟业务实例）；后端 uvicorn @18800；Chromium 真实浏览器 |
| **测试结论** | **有条件通过。第一轮 3 项缺陷全部关闭，零次生灾害成立；本轮新发现 2 项 MINOR + 1 项 NIT，均为文档／测试覆盖类，不阻断功能，建议随下一次提交合并关闭。** |

---

## 1. 测试结论摘要

### 1.1 第一轮缺陷关闭情况

| 编号 | 级别 | 关闭状态 | 关键证据 |
|---|---|---|---|
| DEF-SIT-01 | BLOCK | ✅ **关闭** | `developer` 与最小权限自定义角色在**真实浏览器**里点击"统计表类型"均成功出数（HTTP 200，非 403）；四内置角色与 G5 可达性完全对齐 |
| DEF-SIT-02 | MINOR | ✅ **关闭** | 空串／缺字段／null 三种形态实测均 422，与 Rev.N §5 修订后的契约表逐条相符 |
| DEF-SIT-03 | MINOR | ✅ **关闭** | 四种全空白形态（空格／制表符／换行混合／全角空格 U+3000）实测均 400 且文案为"必须指定 connection_id"；连接解析不再先行 |

### 1.2 本轮新发现

| 级别 | 编号 | 问题 | 影响 |
|---|---|---|---|
| MINOR | DEF-SIT2-01 | **设计文档附录 A.4 未随 Rev.N 同步**：文档正文声称"Rev.N collect 112 项"，附录 A.4 的成品代码块仍是 Rev.M 的 2429 行 / 96 个 `def test_`，**缺失 `test_sit01_*` 与 `test_sit03_*` 两条用例** | 照 Rev.N 附录施工只能得到 110 项，且丢掉的恰是防 DEF-SIT-01 复发的那条守卫；"照图施工级"文档的自洽性被破坏 |
| MINOR | DEF-SIT2-03 | **防复发只做到模块级，未做到平台级**：`tests/test_rbac_path_coverage.py` 是全仓库唯一的写端点守卫，但它只校验第二级 `_PATH_TO_MENU`，**不校验第一级 `_OPERATIONAL_WRITE_PREFIXES`**；Q 新增的 `test_sit01_*` 硬编码 G14 路径，救不了下一个深度诊断子模块 | 同类缺陷（登记点枚举不全）已复发三次；下一个子模块（G15…）仍会在 admin 冒烟下静默漏网 |
| NIT | DEF-SIT2-02 | 附录 A.2 文件头 docstring 仍写 `Rev.M §5`，落盘文件是 `Rev.N §5` | 逐字比对唯一差异行；照图施工会产出与仓库不一致的文件头 |

**零次生灾害：确认成立**（§3）。**照图施工符合度：代码 100%，文档附录有 2 处未同步**（§4）。

---

## 2. 第一轮三项缺陷的关闭核验

### 2.1 DEF-SIT-01（BLOCK）—— 第一级写端点放行清单

**代码核验**：`backend/services/auth_service.py:298` 已插入

```python
    "/api/v1/table-type-stats/",            # 深度诊断-表类型统计
```

位置在 `"/api/v1/toolkit/",` 之后、元组闭合之前，尾斜杠保留，与既有条目风格一致；
`_DEVELOPER_WRITE_PREFIXES = _OPERATIONAL_WRITE_PREFIXES` 的别名关系未变。

**行为核验一：真实 HTTP 全角色矩阵**（携带真实 JWT 走完整中间件栈，非直调 `check_permission`）

| 角色 | G14 `POST /run` | G5 `POST /index-audit/run` | G14 `GET /history` | 判定 |
|---|---|---|---|---|
| `sit_admin` (admin) | **200** | 200 | 200 | 对齐 |
| `sit_dba` (dba) | **200** | 200 | 200 | 对齐 |
| **`sit_dev` (developer)** | **200**（修复前 403） | 200 | 200 | **对齐，BLOCK 关闭** |
| `sit_aud` (auditor) | 403 | 403 | 200 | 对齐（写端点对 auditor 一律拒绝，属平台既有策略） |

**行为核验二：最小权限自定义角色**。新建自定义角色 `sit2_tt`，`role_permissions` 只授
`deep-diag` / `deep-diag-tabletype` / `instances` 三个菜单键：

| 端点 | 结果 | 期望 |
|---|---|---|
| `POST /api/v1/table-type-stats/run` | **200** | 放行 ✅ |
| `GET /api/v1/table-type-stats/history` | 200 | 放行 ✅ |
| `POST /api/v1/index-audit/run` | 403 | 拒绝（未授该菜单）✅ |
| `POST /api/v1/emergency/run` | 403 | 拒绝 ✅ |
| `GET /api/v1/rules` | 403 | 拒绝 ✅ |

即：第一级放行恢复的同时，**第二级菜单可见性仍然生效，没有越权外溢**。

**行为核验三：因果性（摘除 P7 的对照实验）**。在同一进程内把新增前缀从元组里摘掉
（等价 Rev.M 修复前），其余一切不变：

| 角色/路径 | 含 P7 | 摘掉 P7 |
|---|---|---|
| `sit2_tt` → G14 `/run` | True | **False** |
| `sit2_tt` → G5 `/run` | True | True（不受影响） |

单一变量、单一结果，因果链闭合。

**行为核验四：真实浏览器点击**（关闭 Q 整改报告 §5 移交本轮的两条人工验收项）

用 Chromium 驱动真实前端，`sit_dev`（developer）与 `sit_tt`（最小权限自定义角色）分别：
登录 → 进入"深度诊断" → 选择目标实例 → 切到"表类型统计"页签 → 点击"统计表类型"。

| 观测项 | `sit_dev` | `sit_tt` |
|---|---|---|
| 侧边栏"深度诊断"可见 | ✅ | ✅（且只剩"深度诊断 + 实例管理"两项） |
| "表类型统计"页签可见 | ✅ | ✅（且是**唯一**可见子页签，`subtabs` 回退清单生效，页面有活动页签） |
| "统计表类型"按钮可点 | ✅ | ✅ |
| `POST /run` 网络响应 | **200** | **200** |
| 页面出现 403／"无权"／"权限不足" | 否 | 否 |
| 渲染出的汇总行 | `实例类型 集中式 · 库 15 · 总表 208 · 单表 208 · 广播表 0 · 分片表 0 · 逻辑基线 208` | 同左 |

**落库佐证**：`table_type_stat.created_by` 现存 `sit_dev`(2 次) / `sit_tt`(3 次) / `sit_dba`(1 次)
的真实记录——非管理员角色确实**跑完了**统计并落了盘，不是只拿到 HTTP 200。

### 2.2 DEF-SIT-02（MINOR）—— 空 `connection_id` 的契约

设计 §5 错误表已按推荐方案①拆成 422／400 两行，实现未动。实测（真实 HTTP）：

| 输入 | 实测 HTTP | 实测 detail | Rev.N §5 契约 | 判定 |
|---|---|---|---|---|
| `"connection_id": ""` | **422** | `string_too_short` / `String should have at least 1 character` | 422 | ✅ |
| 不传 `connection_id` | **422** | `missing` / `Field required` | 422 | ✅ |
| `"connection_id": null` | **422** | `string_type` / `Input should be a valid string` | 422（枚举未含此形态，见 §7） | ✅ 码正确 |

### 2.3 DEF-SIT-03（MINOR）—— 全空白 `connection_id` 的报错指向

`backend/api/table_type_stats.py::run()` 的空白校验已提前到 `_pool()` 之前。实测：

| 输入 | 实测 HTTP | 实测 detail |
|---|---|---|
| `"   "`（三个空格） | 400 | `必须指定 connection_id（本模块不接受默认连接：连接解析与实例类型解析在空 ID 下可能指向不同实例）` |
| `"\t"`（制表符） | 400 | 同上 |
| `" \n "`（空格+换行+空格） | 400 | 同上 |
| `"　"`（**全角空格 U+3000**） | 400 | 同上（Python `.strip()` 按 Unicode 空白处理，覆盖到位） |
| `"no_such_conn"`（对照组） | 400 | `未连接TDSQL实例或连接不存在` |

两类错误的**文案已经分开**：输入不合格指向"请填 connection_id"，连接找不到才指向连接管理。
排查方向不再跑偏。Q 新增的 `test_sit03_*` 还额外断言了 `_pool` 调用计数为 0，把"顺序"本身钉住了。

---

## 3. 整改的爆炸半径与零次生灾害

本轮整改**动了一份全局权限清单**（`_OPERATIONAL_WRITE_PREFIXES` 被平台所有角色的
第一级判定共用），因此爆炸半径必须逐条量化，不能只看"新增 1 行、看着没风险"。

### 3.1 全量权限判定快照比对（穷举，非抽样）

从运行中服务的 `openapi.json` 取全部 **159 条路径 / 188 个写读操作**，与 **4 个内置角色**
做笛卡尔积，逐条调用 `check_permission`，导出快照；再把 `auth_service.py` 换回修复前
版本（`git show 35e05ad:...`）重导一次，逐键比对：

```
对比条目总数: 752
判定发生变化的条目: 1
  developer|POST /api/v1/table-type-stats/run          False -> True
```

**752 条判定里只有 1 条改变，且正是目标那条。** 没有任何一个其他端点、任何一个其他角色的
权限被放宽或收紧。

### 3.2 全量回归对照实验

沙箱是 MariaDB、生产是 TDSQL/MySQL 8，二者本就存在若干环境性差异，所以**绝对通过数没有意义，
只有同一台机器上的前后差值有意义**。方法：同一 DB 状态谱系下，仅替换
`auth_service.py` / `table_type_stats.py` / `test_table_type_stats.py` 三个文件。

| 轮次 | 代码 | 结果 |
|---|---|---|
| ① 修复后（首跑） | `e94f3b6` | 12 failed / 1617 passed / 83 skipped / 22 errors |
| ② 修复前（控制组） | `35e05ad` | **4 failed / 1623 passed** / 83 skipped / 22 errors |
| ③ 修复后（复跑） | `e94f3b6` | **4 failed / 1625 passed** / 83 skipped / 22 errors |

> **关于①的 12 failed —— 是我自己的夹具污染，不是整改引入。**
> ① 跑之前我为做权限矩阵建了一条 `is_default=1` 的连接，导致
> `test_*_without_connection` 系列（先 `registry.disconnect()` 再断言 400）
> 因为服务自动回落到默认连接而拿到 200。①跑完后测试套件自身把 `is_default` 清零，
> 于是②③都不再复现。**②③才是同一状态谱系下的合法对照。**

**②与③的失败集合逐行 `diff` 完全一致（无任何增删）**，通过数 `1623 → 1625`，
净增 2 正是本次新增的两条用例。**零次生灾害成立。**

③ 中残留的 4 项失败在修复前后**完全相同**，均为沙箱环境差异，与 G14 无关：

| 失败用例 | 性质 |
|---|---|
| `test_o23_migration_fail_closed.py::test_boolean_default_normalized` | MariaDB 对字符串默认值回带引号，与 MySQL 8 不同 |
| `test_o23_migration_fail_closed.py::test_case_insensitive_keyword_default` | 同上 |
| `test_monitordb_slow.py::test_end_to_end_scan_persists` | monitordb 夹具依赖 |
| `test_file_report_delete.py::test_file_report_batch_delete_permissions` | 前置用户夹具依赖；**修复前后同样失败**，与权限清单改动无关 |

### 3.3 冻结面核验（`git diff 0c0b3b4..e94f3b6`）

| 路径 | 变更 |
|---|---|
| `backend/schema/**`（含 `v13/130_table_type_stats.sql`） | **零变更** ✅（M-3 迁移冻结约束成立） |
| `backend/services/table_type_stats_service.py` | **零变更** ✅（服务层含基线谓词一字未动） |
| `frontend/**` | **零变更** ✅ |
| `backend/engine/**`、119 条规则 | **零变更** ✅ |
| 其余 9 个深度诊断子模块 | **零变更** ✅ |

### 3.4 平台守卫与规则回归

| 门禁 | 结果 |
|---|---|
| `tests/test_table_type_stats.py` | **112 passed**（需 `SQLCHECK_DB_NAME=tdsql_sqlcheck_test`，见 §8.2） |
| `tests/test_rbac_path_coverage.py` + `tests/test_app_routes_integrity.py` | **6 passed** |
| `test_rules.py` + `test_sit_rules.py` + `test_sit_v1_rules.py` | **94 passed / 11 skipped**（collect 105） |

---

## 4. 照图施工符合度复核

把 Rev.N 附录 A.1～A.4 的代码块原样抽出，与仓库落盘文件逐行差分：

| 附录 | 落盘文件 | 设计行数 | 落盘行数 | 结果 |
|---|---|---:|---:|---|
| A.1 | `backend/services/table_type_stats_service.py` | 1178 | 1178 | **逐字一致** ✅ |
| A.2 | `backend/api/table_type_stats.py` | 81 | 81 | **1 行不一致** ⚠️（DEF-SIT2-02） |
| A.3 | `backend/schema/v13/130_table_type_stats.sql` | 46 | 46 | **逐字一致** ✅ |
| A.4 | `tests/test_table_type_stats.py` | 2429 | **2474** | **缺 45 行 / 2 条用例** ⚠️（DEF-SIT2-01） |

**代码本身 100% 照图施工**（A.2 的差异只在文件头 docstring 的版本号，逻辑逐字一致）；
问题出在**文档回填**这一步没做完。

七个登记点（ADR-21 / Rev.N）逐点复核，全部到位：

| 点 | 位置 | 状态 |
|---|---|---|
| P1 | `auth_service._PATH_TO_MENU:381` | ✅ `"/api/v1/table-type-stats": "deep-diag-tabletype"` |
| P2 | `auth_service.py:497` 菜单全集 | ✅ |
| P3 | `auth_service.py:513` 中文标签 | ✅ `'深度诊断-表类型统计'` |
| P4 | `database.py:1718` 默认角色清单 | ✅ |
| P5 | `frontend/index.html:1841` 页签 | ✅ `v-if="visibleMenus.has('deep-diag-tabletype')"` |
| P6 | `app.js:2033` `subtabs` 回退清单 | ✅ 末位 `{perm:'deep-diag-tabletype',tab:'tabletype'}`（浏览器实测生效，§2.1） |
| **P7** | `auth_service._OPERATIONAL_WRITE_PREFIXES:298` | ✅ **本次补齐** |

---

## 5. 功能复测

整改动了 API 层的执行顺序，因此功能面按第一轮同样的口径全量重跑，而非只测改动点。

### 5.1 端到端与数字对账（集中式实例）

沙箱业务夹具：`sit2_biz1`（3 张普通表 + `orders_tdsql_subp202601` + `orphan_tdsql_subp190001` + 1 个视图 `v_orders`）、
`sit2_biz2`（3 张表）、`sit2_empty`（空库），另有 11 个历史遗留库。

`POST /run`（不带 `database`，全实例）返回：

```
instance_type=centralized  type_source=declared  type_conflict=false
database_count=15  total_tables=208  shard=0  broadcast=0  single=208
baseline_tables=208  subpartition_tables=0  failed=0  skipped=0  overlap=0  warnings=[]
```

**独立数字对账**（绕开被测代码，直接查 `information_schema`）：

```sql
SELECT COUNT(*), COUNT(DISTINCT TABLE_SCHEMA) FROM information_schema.TABLES
WHERE TABLE_TYPE='BASE TABLE'
  AND TABLE_SCHEMA NOT IN ('information_schema','mysql','performance_schema','sys');
--  208 张 / 14 库
```

| 核对项 | 被测结果 | 独立事实 | 判定 |
|---|---|---|---|
| 总表数 | 208 | 208 | ✅ |
| 库数 | 15 | 14（+ 空库 `sit2_empty`，`GROUP BY` 天然不出行） | ✅ |
| 逐库数字（15 行） | 全部一致（`bank_enrich`1 / `idxaudit_test`5 / `sit2_biz1`5 / `sit2_biz2`3 / `sit2_empty`0 / `tdsql_sit_clean`61 …） | — | ✅ |
| 视图 `v_orders` | 未计入（`sit2_biz1`=5 而非 6） | `TABLE_TYPE='BASE TABLE'` 天然排除 | ✅ |
| `_tdsql_subp` 表 | 计入，`subpartition_tables=0` | 集中式一律不剔除（Rev.G / P1-03） | ✅ |
| 系统库 | 未出现在 15 库中 | `_SYS_DB` 过滤 | ✅ |
| 恒等式 | `total(208) == single(208) == baseline(208)` | 集中式分支 | ✅ |

### 5.2 入参边界

| 用例 | 结果 | 判定 |
|---|---|---|
| `database="sit2_biz1"` | 200，`db_cnt=1 total=5` | ✅ 定向统计 |
| `database="sit2_empty"` | 200，`db_cnt=1 total=0` | ✅ 空库不报错 |
| `database="  sit2_biz1  "` | 200，落库 `database_filter='sit2_biz1'` | ✅ 已 trim |
| `database="SIT2_BIZ1"`（大小写异形） | 400 `数据库不存在或当前账号不可见: SIT2_BIZ1（SHOW DATABASES 未返回该库）；实例上存在大小写不同的同名库: sit2_biz1` | ✅ 精确匹配 + 提示同名兄弟库，ADR-22 兄弟库误并封堵仍在 |
| `database="no_such_db"` | 400 `数据库不存在或当前账号不可见` | ✅ |
| `database="mysql"` / `"information_schema"` | 400 `不允许统计系统库: xxx` | ✅ |
| `database="sit2_biz1\`;DROP DATABASE sit2_biz2;--"` | 400（当作库名整体查不到）；事后 `SHOW DATABASES LIKE 'sit2_biz2'` **仍在** | ✅ **注入未生效** |
| `history?limit=0 / -5 / 999999 / abc` | 20条上限 / 1条 / 200条上限 / 422 | ✅ `max(1, min(limit or 20, 200))` 钳位正确 |
| `detail/999999`、`detail/0`、`detail/-1` | 200 + 空 `items` | ✅ 设计既定的 graceful 语义（`test_get_detail_missing_id_is_graceful`） |

### 5.3 分布式失败路径

建一条 `is_distributed=1` 的连接指向同一台 MariaDB（必然不认 `/*proxy*/` 语法）：

```
instance_type=distributed  database_count=1  total=0  baseline=0  failed_databases=1
warnings:
  - PROXY_CMD_FAILED (ERROR)        1 个库采集失败，未计入任何汇总数（sit2_biz1）；逐库失败原因见各行「说明」
  - NOT_DISTRIBUTED_ENDPOINT (ERROR) 全部已执行的业务库均因语法错误(1064)失败：该连接可能指向后端 TXSQL
                                     而非 Proxy 端口，或该实例实际并非分布式实例
item: sit2_biz1  status=FAILED  baseline_tables=5  total_tables=0
      detail: [errno 1064] 语法错误（该连接可能非 Proxy 端点）…
```

| 核对项 | 判定 |
|---|---|
| HTTP 码 | 200（部分失败不是接口错误）✅ |
| W1 `PROXY_CMD_FAILED` + W8 `NOT_DISTRIBUTED_ENDPOINT` 同时给出 | ✅ 与 §8 E-5 一致 |
| 失败库**不污染实例级汇总**（item 的 `baseline=5`，实例级 `baseline=0`） | ✅ 与 Rev.I "只有 eligible 库计入实例级汇总"一致 |
| 报错文案直接指向"可能连到了 TXSQL 而非 Proxy" | ✅ 可执行 |

### 5.4 并发与耗时

12 个并发 `POST /run` 打同一连接：**5×200 + 7×429**，无 500、无挂起、无连接池损坏；
429 的 `detail` 为 `目标库 xxx 扫描并发已达上限(N)，请稍后重试`。全实例（15 库 / 208 表）
单次耗时 0.024～0.085 s。

### 5.5 前端与可回看（REQ-6）

真实浏览器操作"历史"抽屉：`GET /history?connection_id=…&limit=20` → 200；抽屉列出
**统计时间 / 操作人 / 库名 / 实例类型 / 库数 / 总表 / 单表 / 广播表 / 分片表 / 失败** 各列，
**操作人列真实显示 `sit_tt` / `sit_dev` / `sit_admin`**——P2-02 的 `operator` 透传在
真实链路上闭合；点击任意行加载逐库明细，明细表正常渲染。全程无 JS 控制台报错
（唯一一条 401 是登录前的首次探测请求，登录后不再出现）。

---

## 6. 本轮新发现的问题与解决方案（照图施工级）

### 6.1 DEF-SIT2-01（MINOR）—— 设计文档附录 A.4 未随 Rev.N 同步

#### 6.1.1 现象

| 检查项 | 设计附录 A.4 | 仓库落盘 | 差 |
|---|---:|---:|---|
| 代码块行数 | 2429 | 2474 | **−45** |
| `def test_` 计数 | 96 | 98 | **−2** |
| 含 `test_sit01_*` / `test_sit03_*` | **0 处** | 2 处 | 缺失 |
| 代码块首行 docstring | `"""G14 · 表类型统计 回归测试（DESIGN-v1.6.3.0 **Rev.M** §11）` | 同左 | 版本号未更新 |

而文档正文有三处声称 A.4 已是 Rev.N 的 112 项：

* 第 8 行（文档等级栏）：`附录 A 给出全部新增/修改文件的逐行成品代码；Rev.N collect 112 项`
* 第 613 行（附录 A 变化表）：`附录 A.4 给出完整成品代码；Rev.N collect 112 项`
* 第 1595 行（§11 测试设计）：`tests/test_table_type_stats.py（附录 A.4），Rev.N collect 112 项`

#### 6.1.2 后果

本项目的文档等级是"**照图施工级**"——附录即唯一可施工源。按 Rev.N 附录 A.4 重建
测试文件只能得到 **110 项**，而丢掉的两条恰好是 `test_sit01_write_endpoint_is_reachable_by_non_admin_roles`
（DEF-SIT-01 的防复发守卫）与 `test_sit03_blank_connection_id_reports_the_right_reason`。
ADR-21 明确写着这类缺陷"**必须由单测钉住而不是靠人工回归**"，若守卫本身没进施工图，
这句承诺在文档层面就是空的。

#### 6.1.3 解决方案（照图施工）

**① 在附录 A.4 的代码块中插入两条用例。**
插入位置：文档第 **5068** 行之后（该行是代码块内 repo 第 1734 行，即
`test_r08_permission_key_is_registered_at_every_point` 结束后的第二个空行；
其下一行 5069 是 `# ══…` 的 Rev.J 定向回归分隔块）。插入内容为下列 **45 行**，一字不改：

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

> 该 45 行末尾自带一个空行，插入后 A.4 块内 `test_sit03_*` 与
> `# ══…Rev.J 定向回归` 分隔注释之间恰为两个空行，与仓库文件一致。

**② 同步 A.4 代码块首行 docstring 的版本号。**
把代码块第 1 行的 `Rev.M §11` 改为 `Rev.N §11`：

```diff
-"""G14 · 表类型统计 回归测试（DESIGN-v1.6.3.0 Rev.M §11）
+"""G14 · 表类型统计 回归测试（DESIGN-v1.6.3.0 Rev.N §11）
```

> ⚠️ 若仓库文件的该行也仍是 `Rev.M`，则**两边一起改**，改完必须再跑一次 ③ 的
> 逐字比对；否则只改文档会人为制造新的不一致。

**③ 落地即验（把"照图施工"变成可执行门禁，而不是靠人肉核对）。**
在仓库根执行：

```bash
python3 - <<'PY'
import re
D = "docs/DESIGN-v1.6.3.0-深度诊断表类型统计子模块详细设计说明书.md"
lines = open(D, encoding="utf-8").read().split("\n")
start = next(i for i, l in enumerate(lines) if l.startswith("## 14. 附录 A"))
end   = next(i for i, l in enumerate(lines) if l.startswith("## 15. 附录 B"))
seg   = lines[start:end]
secs  = [(i, re.match(r"^###\s+(A\.\d+)", l).group(1))
         for i, l in enumerate(seg) if re.match(r"^###\s+A\.\d+", l)]
MAP = {"A.1": "backend/services/table_type_stats_service.py",
       "A.2": "backend/api/table_type_stats.py",
       "A.3": "backend/schema/v13/130_table_type_stats.sql",
       "A.4": "tests/test_table_type_stats.py"}
bad = 0
for idx, (i, name) in enumerate(secs):
    if name not in MAP:
        continue
    j = secs[idx + 1][0] if idx + 1 < len(secs) else len(seg)
    body = seg[i:j]
    fences = [k for k, l in enumerate(body) if l.startswith("```")]
    pairs  = list(zip(fences[0::2], fences[1::2]))
    a, b   = max(pairs, key=lambda p: p[1] - p[0])
    design = "\n".join(body[a + 1:b]) + "\n"
    repo   = open(MAP[name], encoding="utf-8").read()
    ok = design == repo
    bad += 0 if ok else 1
    print(f"{name}  {MAP[name]:<50} {'逐字一致' if ok else '★ 不一致'}")
raise SystemExit(bad)
PY
```

期望输出四行全为 `逐字一致`，退出码 0。**建议把这段固化为
`tests/test_design_appendix_matches_repo.py`，让"附录与仓库不一致"直接红灯**——
它同时替代了 §12.2 里那条要靠人工做的"附录逐字核对"验收项。

**④ 文档三处 collect 声明与实际保持一致。** 完成 ① 后，第 8 / 613 / 1595 行的
"112 项"即为真；若将来再增减用例，三处必须同步（或改为不写死数字，只写
"以 `pytest --collect-only -q` 实际输出为准"）。

### 6.2 DEF-SIT2-03（MINOR）—— 防复发只做到模块级，未做到平台级

#### 6.2.1 现象

`tests/test_rbac_path_coverage.py` 是本仓库**唯一**的"新增写端点自检"守卫，它的
文件头 docstring 把设计意图写得很清楚：

> 因此把"闭"提前到开发期：本用例扫描所有写端点，发现未登记即失败。

但它扫描后只做了一件事（`test_all_write_endpoints_are_mapped`）：校验
**第二级** `_PATH_TO_MENU` 是否登记。**第一级** `_OPERATIONAL_WRITE_PREFIXES`
完全不在它的视野里——而 DEF-SIT-01 恰恰死在第一级。

Q 本轮新增的 `test_sit01_write_endpoint_is_reachable_by_non_admin_roles` 写在
`tests/test_table_type_stats.py` 里，且路径是**硬编码**的：

```python
    G14 = "/api/v1/table-type-stats/run"
    G5 = "/api/v1/index-audit/run"          # 既有深度诊断写端点，作为基准
```

它能防住 G14 自己被改回去，**但对 G15、G16 一无所知**。

#### 6.2.2 后果

"登记点枚举不全"这一缺陷类已经复发三次（DEF-1 → P1-06 → DEF-SIT-01）。
Rev.N 的 KL-17 给出的防复发做法是"新增子模块时用既有同类子模块（如 G5）做**全仓库 grep 对照**"
——这是**人工流程**，与 ADR-21 自己写的"必须由单测钉住而不是靠人工回归"相矛盾。
下一个深度诊断子模块只要作者忘了 grep，admin 冒烟照样全绿，缺陷照样漏到 SIT。

#### 6.2.3 事实核查：这条不变量今天成立吗？

在下结论前先穷举了全仓库 **98 个写端点**：

```
写端点总数: 98
其中 deep-diag* 归属: 12
deep-diag 写端点【未被 _OPERATIONAL_WRITE_PREFIXES 放行】: 0
```

12 个深度诊断写端点（覆盖全部 10 个子模块）**当前 100% 都在放行清单内**：

| 端点 | 菜单键 | 放行 |
|---|---|---|
| `POST /api/v1/cluster-inspect/run` | `deep-diag-cluster` | ✅ |
| `POST /api/v1/daily-inspect/run` | `deep-diag-daily` | ✅ |
| `POST /api/v1/emergency/run` | `deep-diag-emergency` | ✅ |
| `POST /api/v1/gateway-log/upload` | `deep-diag-gateway` | ✅ |
| `POST /api/v1/gateway-log/reports/{report_id}/ticket` | `deep-diag-gateway` | ✅ |
| `POST /api/v1/index-audit/run` | `deep-diag-index` | ✅ |
| `POST /api/v1/ppt-report/generate` | `deep-diag-ppt` | ✅ |
| `POST /api/v1/schema-diff/run` | `deep-diag-diff` | ✅ |
| `POST /api/v1/sql-stats/analyze` | `deep-diag-sqlstats` | ✅ |
| `POST /api/v1/sql-stats/bigtable/snapshot` | `deep-diag-sqlstats` | ✅ |
| `POST /api/v1/table-type-stats/run` | `deep-diag-tabletype` | ✅（本次补齐） |
| `POST /api/v1/toolkit/run` | `deep-diag-toolkit` | ✅ |

**零例外**，因此可以安全地把它提升为平台级断言——不会误伤任何既有端点。
（对非 deep-diag 的写端点**不做**这个断言：`/api/v1/rules`、`/api/v1/auth/roles` 等
本就该只对 admin/dba 开放，一刀切会立刻打断这些角色策略。）

#### 6.2.4 解决方案（照图施工）

**① 在 `tests/test_rbac_path_coverage.py` 末尾（第 86 行之后）追加下列内容，一字不改：**

```python


def _menu_keys(path: str) -> list:
    """返回该路径命中的菜单键列表（元组映射会被摊平），未命中返回空列表"""
    for p in sorted(_PATH_TO_MENU.keys(), key=len, reverse=True):
        if path == p or path.startswith(p + "/"):
            mk = _PATH_TO_MENU[p]
            return list(mk) if isinstance(mk, (tuple, list, set)) else [mk]
    return []


def test_deep_diag_write_endpoints_are_in_operational_allowlist():
    """深度诊断写端点必须同时登记第一级放行清单，不能只登记第二级菜单映射。

    check_permission 是两级判定：第一级按角色 + _DEVELOPER_WRITE_PREFIXES
    （即 _OPERATIONAL_WRITE_PREFIXES 的别名）放行，第二级才查 role_permissions
    菜单可见性。只登记 _PATH_TO_MENU 而漏登记放行清单时：

      · admin / dba 在第一级短路放行，冒烟全绿；
      · developer 与全部自定义角色卡在第一级，写端点恒 403；
      · 页签因菜单可见性正常而照常显示，现场极易误判为权限矩阵没配对。

    上一条用例 test_all_write_endpoints_are_mapped 只覆盖第二级，
    这一条补上第一级——两级都钉住，新子模块才不会重蹈 DEF-SIT-01。

    只对 deep-diag* 归属的写端点做此断言：这类"运维操作性工具"按平台既定策略
    对 dba/developer 开放；而 /api/v1/rules、/api/v1/auth/roles 等管理类写端点
    本就应仅限 admin，不适用本不变量。
    """
    missing = []
    for method, path, fname in _write_endpoints():
        if not any(str(k).startswith("deep-diag") for k in _menu_keys(path)):
            continue
        if not any(path.startswith(p) for p in _OPERATIONAL_WRITE_PREFIXES):
            missing.append(f"{method} {path}  ({fname})")
    assert not missing, (
        "以下深度诊断写端点已登记 _PATH_TO_MENU（第二级），却未登记 "
        "_OPERATIONAL_WRITE_PREFIXES（第一级），developer 与全部自定义角色将恒 403，"
        "而页签照常显示；admin/dba 因短路放行测不出来：\n  " + "\n  ".join(missing))
```

**② 同文件第 22～24 行的 import 增加一个名字**（其余不动）：

```diff
 from backend.services.auth_service import (PUBLIC_PATHS, WEBHOOK_PATHS,
                                            _PATH_TO_MENU,
-                                           _SELF_SERVICE_PREFIXES)
+                                           _SELF_SERVICE_PREFIXES,
+                                           _OPERATIONAL_WRITE_PREFIXES)
```

**③ 变异验证（本报告已实测，整改后请复现）：**

```bash
# M0 现版本：必须通过
python3 -m pytest tests/test_rbac_path_coverage.py -q
#   期望：4 passed（原 3 条 + 新增 1 条）

# M1 把 P7 摘掉，等价 Rev.M 修复前：必须失败，且报出确切端点
git stash && git show 35e05ad:backend/services/auth_service.py > backend/services/auth_service.py
python3 -m pytest tests/test_rbac_path_coverage.py::test_deep_diag_write_endpoints_are_in_operational_allowlist -q
#   期望：1 failed，断言消息里出现
#        POST /api/v1/table-type-stats/run  (table_type_stats.py)
git checkout backend/services/auth_service.py && git stash pop
```

> 本报告已完成该变异实验：**M0 passed / M1 failed 并精确点名
> `POST /api/v1/table-type-stats/run  (table_type_stats.py)`**。这条守卫有牙齿，
> 且它的失败消息直接告诉作者该改哪一行。

**④ 设计文档同步（Rev.N → Rev.O）：**

| 位置 | 修订内容 |
|---|---|
| ADR-21 | 在"故必须由单测钉住而不是靠人工回归"之后补一句：**钉住的位置是平台级守卫 `tests/test_rbac_path_coverage.py::test_deep_diag_write_endpoints_are_in_operational_allowlist`，而非各子模块自己的用例；子模块内的对照用例只作补充** |
| KL-17 | 把防复发做法从"新增子模块时用 G5 做全仓库 grep 对照"**改为**"由平台级守卫自动拦截；grep 对照降级为可选辅助"。人工流程不作为唯一防线 |
| §11 测试设计 | 在"Rev.N 新增的缺陷定向测试"表下增加一行：`tests/test_rbac_path_coverage.py::test_deep_diag_write_endpoints_are_in_operational_allowlist`（平台级，非本模块文件，故不计入 A.4 的 collect 数） |
| §9 爆炸半径 / 修改清单 | 在既有文件变更表中增加一行：`tests/test_rbac_path_coverage.py`，改动量约 +40 行（1 个 helper + 1 条用例 + 1 处 import 追加） |
| §12.2 验收清单 | 增加一项：`tests/test_rbac_path_coverage.py` 由 3 项增至 4 项且全绿 |

> **爆炸半径**：只动 `tests/` 下一个文件，不碰任何生产代码；新增断言经全量核查
> 对现存 12 个 deep-diag 写端点零误伤（§6.2.3）。

---

### 6.3 DEF-SIT2-02（NIT）—— 附录 A.2 文件头版本号未同步

#### 6.3.1 现象

A.1 / A.3 与仓库逐字一致，A.2 的唯一差异是代码块第 2 行：

```
< """G14 · 表类型统计 API（DESIGN-v1.6.3.0 Rev.M §5）      ← 设计附录 A.2
> """G14 · 表类型统计 API（DESIGN-v1.6.3.0 Rev.N §5）      ← 仓库落盘
```

A.2 的正文（`Rev.N / DEF-SIT-03` 那段 P2-03 说明、`run()` 里新增的空白校验）都已同步，
唯独文件头的版本号漏改。

#### 6.3.2 解决方案（照图施工）

把文档第 **3196** 行（A.2 代码块的第 2 行）改为：

```diff
-"""G14 · 表类型统计 API（DESIGN-v1.6.3.0 Rev.M §5）
+"""G14 · 表类型统计 API（DESIGN-v1.6.3.0 Rev.N §5）
```

改完由 §6.1.3 ③ 的比对脚本自动确认 A.2 变为"逐字一致"。**不需要动仓库文件。**

---

## 7. 观察项（本轮不计缺陷，仅记录）

| 编号 | 观察 | 为什么不算 G14 的缺陷 | 建议 |
|---|---|---|---|
| OBS-1 | `VERSION` 仍为 `1.6.2.2`，登录页页脚显示 `V1.6.2.2` | 设计 §9 的最小化修改清单**没有**把 `VERSION` / `backend/config.py:APP_VERSION` 列入 G14 的改动面（文档第 7 行只把它记为"当前基线"）；版本号提升属打包环节 | 移交 G 在 v1.6.3.0 打包时统一提升，并作为发布前门禁核对一次 |
| OBS-2 | `StatsRequest` 未设 `extra="forbid"`：客户端把 `database` 误写成 `databases`，多余字段被静默忽略，一次**定向统计悄悄变成全实例扫描** | FastAPI/Pydantic 默认口径，全平台所有请求模型同此，非 G14 引入；本轮实测触发过一次（`{"databases":["sit2_biz1"]}` → 扫了 15 库 208 表） | 若要收紧属平台级改造，需单独立项评估；G14 单独收紧反而与其余接口不一致 |
| OBS-3 | §5 错误表的 422 行只枚举了 `string_too_short` / `Field required`，实测 `connection_id: null` 是第三种 `string_type` | HTTP 码（422）与拦截机制（模型层，进路由前）都与文档一致，只是枚举不全 | 下次修订时把该行的枚举改为"`string_too_short` / `Field required` / `string_type` 等 FastAPI 请求体校验错误"，或直接不枚举具体 `type` |
| OBS-4 | `history?limit=0` 返回 20 条（走默认值）而非 0 条 | `max(1, min(int(limit or 20), 200))` 的 `or` 语义所致，是刻意的钳位；`limit=-5` 被钳到 1、`limit=999999` 被钳到 200，均无 SQL 注入或异常风险 | 不改 |
| OBS-5 | `detail/{stat_id}` 对不存在 / 0 / 负数一律 200 + 空 `items` | 设计既定的 graceful 语义，且有 `test_get_detail_missing_id_is_graceful` 钉住 | 不改 |

---

## 8. 测试环境与方法学说明

### 8.1 环境

| 项 | 值 |
|---|---|
| 元数据库 & 模拟业务实例 | MariaDB 10.11.14 @ `127.0.0.1:13306`（**沙箱**） |
| 后端 | `uvicorn backend.main:app` @ `127.0.0.1:18800`，V1.6.2.2 |
| 前端 | `frontend/index.html` + `frontend/static/js/app.js`（无 `dist`，走 v1 页面） |
| 浏览器 | Chromium（Playwright 驱动，1600×1000） |
| 迁移 | `v13_130_table_type_stats` 已应用，`table_type_stat` / `table_type_stat_item` 已建 |
| 账号 | `sit_admin`/`sit_dba`/`sit_dev`/`sit_aud` 四内置角色 + `sit_tt`（自定义角色 `sit2_tt`） |

### 8.2 两处必须说明的环境差异

**① 沙箱是 MariaDB，生产是 TDSQL/MySQL 8.0.33-v24-txsql。**
迁移器的结构验收在两处会误报（整型显示宽度 `int(11)` vs `int`；字符串默认值带引号），
本轮用一个**只存在于内存、从未进入仓库**的 pytest 插件 + 启动器抹平，
未修改仓库任何文件。**因此本报告的门禁数据只能证明"G14 自身及其对既有功能的影响"，
不能替代目标环境（TDSQL/MySQL）的全量回归。**

**② `tests/test_table_type_stats.py` 的破坏性用例带安全闸。**
22 项元数据库集成用例会 `DROP` 留档表，代码里有 `assert_destructive_target_is_safe()`
拒绝在非批准库上执行，必须 `SQLCHECK_DB_NAME=tdsql_sqlcheck_test` 才放行——
默认库名下这 22 项报 `DestructiveTargetError`。**这是设计正确的失败关闭保护，不是缺陷**；
执行时给对环境变量即得 112 passed。本报告全量回归采用默认库名（与其余用例一致），
故这 22 项计入 errors；G14 专项回归另行用批准库名执行。

### 8.3 方法学

* **穷举而非抽样**：权限影响用 752 条判定的全量快照比对，不靠挑几个端点试。
* **控制变量**：全量回归的前后对照严格同一 DB 状态谱系，并显式识别、剔除了一次
  由我自己夹具造成的 8 项伪回归（§3.2）。
* **变异测试**：新提出的守卫必须在"注入原缺陷"时红灯（§6.2.4 ③），
  否则只是一条永远为真的装饰性断言。
* **独立事实源**：所有数字都用绕开被测代码的 `information_schema` 查询二次对账。
* **真实链路**：权限结论用真实 JWT 走完整中间件栈 + 真实浏览器点击，
  不以 `check_permission` 的直调结果代替。

---

## 9. 遗留与结论

### 9.1 遗留清单

| 项 | 状态 |
|---|---|
| DEF-SIT2-01 / DEF-SIT2-03（MINOR） | **待整改**，均不阻断功能，建议合并为一次提交 |
| DEF-SIT2-02（NIT） | 同上，纯文档一行 |
| OBS-1 `VERSION` 提升 | 移交 G 打包环节 |
| 内网 UAT `lzbj_ecif` 六数字对账（215 / 0 / 117 / 98 / 215 / 78） | 未做（沙箱无 TDSQL），部署后执行 |
| T20 基线谓词性能证据（`EXPLAIN` + 耗时） | 未做，需最大内网实例，发布前门禁 |
| T13 命令作用域 | 不阻断、不影响任何数字 |
| 目标环境（TDSQL/MySQL）全量回归 | 未做，部署后必须补 |
| 真实浏览器验收（Q 移交本轮） | ✅ **本轮已完成并关闭**（§2.1 行为核验四） |

### 9.2 结论

**第一轮 SIT 的 1 项 BLOCK + 2 项 MINOR 全部关闭**，且关闭方式经得起追问：
BLOCK 用"真实浏览器点击 + 落库 `created_by` 记录 + 摘除前缀的对照实验"三重证据闭合，
不是只看代码有没有那一行。

**整改的爆炸半径已量化到条**：752 条权限判定只变了 1 条；全量回归在同一状态谱系下
失败集合逐行一致、通过数净增 2（正是新增的两条用例）；迁移文件、服务层、前端、
`engine` 与 119 条规则、其余 9 个深度诊断子模块**零变更**。**零次生灾害成立。**

本轮新发现的三项都不在运行代码上：两项是设计文档附录没回填完（`照图施工级` 文档的自洽性），
一项是防复发只做到了模块级、没做到平台级。后者值得单独强调——
"登记点枚举不全"已经是**第三次**复发，而现在的防线是一条硬编码 G14 路径的模块内用例
加一句"人工 grep 对照"的流程约定；§6.2 给出的平台级守卫已完成变异验证
（注入原缺陷即红灯并精确点名端点），且对现存 12 个深度诊断写端点零误伤，
是把这一类缺陷真正封死的最小改动。

**综合结论：有条件通过。** 功能与权限层面可以进入下一阶段；
三项新发现建议随下一次提交合并关闭后，本模块即可视为 SIT 完成。
