# FIX-v1.6.3.0 深度诊断·表类型统计（G14）第一轮 SIT 整改完成情况报告

| 项 | 内容 |
|---|---|
| 整改对象 | `SIT-v1.6.3.0-G14表类型统计第一轮SIT测试报告-ClaudeA.md`（提交 `35e05ad`，结论：不通过，1 BLOCK + 2 MINOR） |
| 整改基线 | `main` / `0c0b3b4`（G14 落盘提交） |
| 整改人 | 智能体 Q |
| 整改日期 | 2026-09-01 |
| 设计定版 | `DESIGN-v1.6.3.0-…详细设计说明书.md` **Rev.M → Rev.N**（SIT 整改定版） |
| **整改结论** | **三项缺陷全部关闭，回归与冒烟全绿，可再次提交 SIT** |

---

## 1. 整改总览

| 级别 | 编号 | 问题 | 整改方式 | 状态 |
|---|---|---|---|---|
| **BLOCK** | DEF-SIT-01 | `/api/v1/table-type-stats/` 未登记到 `_OPERATIONAL_WRITE_PREFIXES`，developer 与全部自定义角色写端点恒 403 | 照报告 §5.1.5：补登记 1 行 + 行为级测试 1 条 + 设计文档同步 | ✅ 关闭 |
| MINOR | DEF-SIT-02 | 空 `connection_id` 实际 422，设计契约写 400 | 照报告推荐方案 ①：修文档（实现合理，422 为 FastAPI 标准语义） | ✅ 关闭 |
| MINOR | DEF-SIT-03 | 全空白 `connection_id` 报"未连接TDSQL实例"，服务层守卫经 HTTP 不可达 | 照报告 §5.3：API 层校验提前到连接解析之前 + 测试 1 条 | ✅ 关闭 |

三项均**严格按 SIT 报告给出的解决方案照图施工**，无自行变更方案之处。

---

## 2. 逐项整改明细

### 2.1 DEF-SIT-01（BLOCK）—— 第一级写端点放行清单补登记

**① 代码修改（1 行）**：`backend/services/auth_service.py::_OPERATIONAL_WRITE_PREFIXES`
在 `"/api/v1/toolkit/",` 之后插入：

```python
    "/api/v1/table-type-stats/",            # 深度诊断-表类型统计
```

尾斜杠保留（判定为 `startswith`，与既有条目一致，不误命中兄弟路径）。

**② 测试加固（1 条，`tests/test_table_type_stats.py`）**：
`test_sit01_write_endpoint_is_reachable_by_non_admin_roles`——以既有 G5 `index-audit/run`
为基准，断言四个内置角色对 G14 写端点的可达性与既有深度诊断子模块**完全一致**
（不硬编码死值，将来平台角色策略调整时断言随之联动）；并钉住前缀已登记且带尾斜杠。

**③ 设计文档同步（Rev.M → Rev.N）**：

| 位置 | 修订内容 |
|---|---|
| §2.2 | "6 个登记点"改为"**7 个登记点**"，新增 **P7** 行（`_OPERATIONAL_WRITE_PREFIXES`，写明缺失后果与"admin/dba 短路放行测不出来"） |
| ADR-21 | 6 处 → 7 处；补充"第一级放行清单与第二级菜单可见性是**两套独立机制**，缺任一处都不可用；只有 admin/dba 会短路跳过第一级，必须用 developer 或自定义角色验收" |
| §12.2 | 新增验收项："developer 角色登录后，点击『统计表类型』按钮可正常执行（非 403）" |
| KL-17 | 补记"登记点枚举不全"**第三次复发**（DEF-1 / P1-06 / DEF-SIT-01 同源），并补充防复发的可执行做法：新增子模块时用既有同类子模块（如 G5）做**全仓库 grep 对照** |
| §11 | 新增"Rev.N 新增的缺陷定向测试"表（`test_sit01_*` / `test_sit03_*`） |
| 附录 A 变化表 / 修订记录 | 新增 Rev.N 条目 |

### 2.2 DEF-SIT-02（MINOR）—— 纯文档修订（报告推荐方案 ①）

实现不动（`StatsRequest.min_length=1` 的 422 是 FastAPI 请求体校验的标准语义，且与
项目其他接口一致）。修订两处：

| 位置 | 修订内容 |
|---|---|
| §5.1 错误表 | 原"400 `必须指定 connection_id`（空或全空白）"拆分为两行：**422**（缺失/空串，模型层拦截）+ **400**（全空白，API 层拦截） |
| §8 E-26 | 改为"422（空串/缺字段）或 400（全空白）"，落点更新为 `StatsRequest` / API `run` / `run_stats` |

### 2.3 DEF-SIT-03（MINOR）—— API 层入参校验提前

**① 代码修改（`backend/api/table_type_stats.py::run()`）**：全空白校验插入到
`_pool(body.connection_id)` **之前**，逐字采用报告给出的代码块（含注释说明）：

```python
    if not body.connection_id.strip():
        raise HTTPException(
            status_code=400,
            detail="必须指定 connection_id（本模块不接受默认连接："
                   "连接解析与实例类型解析在空 ID 下可能指向不同实例）")
```

服务层 `run_stats()` 的同名守卫**保留不动**（服务被直接调用时的兜底，与 API 层互不替代）。
文件头注释同步更新（Rev.M → Rev.N，P2-03 说明改为 422/400 两级口径）。

**② 测试加固（1 条）**：`test_sit03_blank_connection_id_reports_the_right_reason`
——直接打 API 层，对三种空白形态（空格 / 制表符 / 混合）断言 400 且提示
"必须指定 connection_id"，并断言**入参不合格时不得先去解析连接**
（`_pool` 调用计数为 0），覆盖真实调用顺序，替代此前只测服务层不可达路径的虚假信心。

---

## 3. 变更清单

| 文件 | 变更 | 对应缺陷 |
|---|---|---|
| `backend/services/auth_service.py` | +1 行 | DEF-SIT-01 |
| `backend/api/table_type_stats.py` | +13 / −2 行（run 校验 + 头注释） | DEF-SIT-03 |
| `tests/test_table_type_stats.py` | +45 行（2 条新用例） | DEF-SIT-01 / 03 |
| `docs/DESIGN-v1.6.3.0-…说明书.md` | Rev.M → Rev.N（§2.2 / ADR-21 / §5 / §8 E-26 / §11 / §12.2 / KL-17 / 附录 A.2 与变化表 / 修订记录） | 三项全部 |

**禁改清单核查**：`backend/engine/**`、119 条规则、服务层逻辑（含基线谓词）、其余既有
深度诊断子模块**均未触碰**；迁移文件 `v13/130_table_type_stats.sql` **一字未改**
（M-3 冻结约束）。

---

## 4. 验证证据

### 4.1 回归测试（全部在 MySQL 8 元数据库上，非 MariaDB）

| 门禁 | 结果 |
|---|---|
| `tests/test_table_type_stats.py` | **112 passed**（110 → 112，+2 为本次新增；0 skipped，22 项元数据库集成真实执行） |
| `test_rbac_path_coverage.py` + `test_app_routes_integrity.py` | **6 passed** |
| `test_rules.py` + `test_sit_rules.py` + `test_sit_v1_rules.py` | **105 passed** |
| 全量 `tests/` | **1734 passed, 0 failed, 0 skipped**（整改前 1732，净增即本次 2 条新用例） |

### 4.2 整改专项冒烟（真实 HTTP 栈，本地 8877 服务 + 本地 13306 实例）

| 验证项 | 结果 |
|---|---|
| 空串 `connection_id` → **422** | ✅ |
| 缺字段 `connection_id` → **422** | ✅ |
| 全空白（空格 / 制表符）→ **400** 且提示"必须指定 connection_id" | ✅ ×2 |
| 四角色 `check_permission` 对 G14 与 G5 写端点可达性**完全一致**（admin/True、dba/True、developer/**True**、auditor/False） | ✅ |
| developer 对 `/run` 放行（BLOCK 关闭直接证据）；自定义角色第一级同样放行 | ✅ |
| `/api/v1/table-type-stats/` 已在 `_OPERATIONAL_WRITE_PREFIXES`（带尾斜杠） | ✅ |
| 端到端不回归：集中式实例 53 库 / 总表 **2143** = 基线 **2143**，恒等式成立 | ✅ |

冒烟合计 **8/8 通过**；另端到端复核一次统计落库（`stat_id=5`）。

> 冒烟中 adhoc 连接（默认声明分布式）对本地非 Proxy 端点报 53 库 1064 全失败
> + `PROXY_CMD_FAILED` + `NOT_DISTRIBUTED_ENDPOINT`——与 SIT 报告 §4.3 所述
> **失败路径预期行为一致**，非缺陷。

### 4.3 SIT 报告 §5.1.6 修复后验收步骤对照

| 报告要求 | 完成情况 |
|---|---|
| `pytest tests/test_table_type_stats.py`（报告期望 111，因报告仅计入 DEF-SIT-01 一条；DEF-SIT-03 亦按其方案新增一条，实际 112） | ✅ 112 passed |
| `pytest tests/test_rbac_path_coverage.py tests/test_app_routes_integrity.py` | ✅ 6 passed |
| 人工：developer 登录点击"统计表类型" | 以 `check_permission` 行为级断言替代（§4.2），真实浏览器操作留待下一轮 SIT |
| 人工：最小权限自定义角色 | 同上，`testor` 自定义角色第一级放行已验证 |

---

## 5. 遗留事项（移交下一轮 SIT / UAT）

| 项 | 说明 |
|---|---|
| developer / 自定义角色的真实浏览器验收 | 报告 §5.1.6 的人工步骤，建议下一轮 SIT 执行 |
| 内网 `lzbj_ecif` 六数字对账（215/0/117/98/215/78） | 需内网环境 |
| T20 基线谓词性能证据 | 发布前门禁，需最大内网实例 |
| T13 命令作用域 | 不阻断、不影响任何数字 |
| 目标环境（TDSQL/MySQL）全量回归 | 本地已为 MySQL 8 全量 1734 通过；内网部署后再跑一次 |

---

## 6. 结论

第一轮 SIT 的 1 项 BLOCK 与 2 项 MINOR 已全部按报告方案关闭，其中：

- **DEF-SIT-01** 属设计遗漏（登记点枚举不全的第三次复发），已补登记并以行为级
  测试钉住；设计文档 §2.2/ADR-21/KL-17 已同步，防止第四次复发；
- **DEF-SIT-02** 为实现合理、文档失真，按报告推荐只改文档；
- **DEF-SIT-03** 已让服务层守卫在 HTTP 路径上真实可达，且报错指向正确。

回归与冒烟证据齐全，**请求再次提交 SIT**。
