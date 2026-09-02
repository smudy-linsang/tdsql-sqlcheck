# FIX2-v1.6.3.0 深度诊断·表类型统计（G14）第二轮 SIT 整改完成情况报告

| 项 | 内容 |
|---|---|
| 整改对象 | `SIT2-v1.6.3.0-G14表类型统计第二轮SIT测试报告-ClaudeA.md`（提交 `0aec853`，结论：有条件通过，新发现 2 MINOR + 1 NIT） |
| 整改基线 | `main` / `e94f3b6`（第一轮 SIT 整改提交） |
| 整改人 | 智能体 Q |
| 整改日期 | 2026-09-02 |
| 设计定版 | `DESIGN-v1.6.3.0-…详细设计说明书.md` **Rev.N → Rev.O**（第二轮 SIT 整改定版） |
| **整改结论** | **三项缺陷全部关闭（含 NIT），变异验证全部复现报告结论，生产运行代码零改动，可进入 UAT** |

---

## 1. 整改总览

| 级别 | 编号 | 问题 | 整改方式 | 状态 |
|---|---|---|---|---|
| MINOR | DEF-SIT2-01 | 设计附录 A.4 未随 Rev.N 同步（缺 2 条用例 / 45 行 + docstring 版本号） | 照报告 §6.1.3：附录回填 + docstring 两边同步 + **固化为可执行门禁** | ✅ 关闭 |
| NIT | DEF-SIT2-02 | 附录 A.2 文件头 docstring 仍写 `Rev.M §5` | 照报告 §6.3.2：文档 1 行改 `Rev.N §5`，仓库文件不动 | ✅ 关闭 |
| MINOR | DEF-SIT2-03 | 防复发只做到模块级，未做到平台级 | 照报告 §6.2.4：rbac 守卫逐字追加 + import 扩名 + 变异验证 + 文档 Rev.O 同步 | ✅ 关闭 |

三项均**逐字采用报告给出的解决方案**；另按报告 OBS-3 建议顺带修订 §5 错误表
422 行枚举（观察项落实，非缺陷）。**用户要求全部缺陷修复后再进 UAT，NIT 亦一并关闭。**

---

## 2. 逐项整改明细

### 2.1 DEF-SIT2-01（MINOR）—— 附录 A.4 回填 + 一致性门禁固化

**① 附录 A.4 回填 45 行**：在设计文档附录 A.4 代码块内、
`test_r08_permission_key_is_registered_at_every_point` 结束之后、Rev.J 定向回归
分隔块之前，插入 `test_sit01_*` 与 `test_sit03_*` 两条用例（与仓库落盘逐字一致）。

**② docstring 版本号两边同步**：报告核查确认仓库文件该行也仍是 `Rev.M`，
按报告预案"两边一起改"——仓库 `tests/test_table_type_stats.py` 与设计附录 A.4
的首行 docstring 均由 `Rev.M §11` 改为 `Rev.N §11`（仓库侧 1 行 diff）。

**③ 固化为可执行门禁（报告建议，已采纳）**：新增
`tests/test_design_appendix_matches_repo.py`——把报告 §6.1.3③ 的比对脚本
落为参数化 pytest 用例（A.1～A.4 各一项），附录与仓库任何一侧单独改动即红灯，
并在失败消息里给出首个差异行号与两侧内容。替代 §12.2 中靠人工做的
"附录逐字核对"验收项。

**④ collect 三处声明核对**：完成 ① 后，文档第 8 / 613 / 1595 行的
"Rev.N collect 112 项"即为真（附录 A.4 现含 98 个 `def test_`）。

### 2.2 DEF-SIT2-02（NIT）—— 附录 A.2 文件头版本号

设计文档附录 A.2 代码块第 2 行 `Rev.M §5` → `Rev.N §5`（纯文档 1 行）。
仓库文件本就是 `Rev.N §5`，未动。由 ③ 的门禁确认 A.2 逐字一致。

### 2.3 DEF-SIT2-03（MINOR）—— 防复发提升为平台级守卫

**① `tests/test_rbac_path_coverage.py`（+42 / −1 行）**：
- import 追加 `_OPERATIONAL_WRITE_PREFIXES`（按报告 diff 逐字）；
- 文件末尾追加 `_menu_keys()` helper 与
  `test_deep_diag_write_endpoints_are_in_operational_allowlist()` 用例
  （按报告 §6.2.4① 逐字，一字不改）：扫描全部写端点，凡 deep-diag* 归属且
  已登记第二级 `_PATH_TO_MENU`、未登记第一级放行清单者，红灯并精确点名
  端点与文件。

**② 变异验证（复现报告 §6.2.4③ 结论）**：

| 变异 | 操作 | 期望 | 实测 |
|---|---|---|---|
| M0 | 现版本 | 4 passed | **4 passed** ✅ |
| M1 | `auth_service.py` 临时还原为 `35e05ad` 版（等价摘除 P7） | 1 failed 且点名端点 | **1 failed**，断言消息精确输出 `POST /api/v1/table-type-stats/run  (table_type_stats.py)` ✅ |
| 恢复 | `git checkout` 还原后复跑 | 4 passed | **4 passed** ✅ |

守卫有牙齿，且失败消息直接告诉下一个子模块的作者该改哪一行。

**③ 设计文档同步（Rev.N → Rev.O，按报告 §6.2.4④ 逐项）**：

| 位置 | 修订内容 |
|---|---|
| ADR-21 | 补充："钉住的位置是平台级守卫 `test_deep_diag_write_endpoints_are_in_operational_allowlist`，而非各子模块自己的用例；子模块内的对照用例只作补充" |
| KL-17 | 防复发做法由"人工全仓库 grep 对照"**改为**"平台级守卫自动拦截；grep 对照降级为可选辅助。人工流程不作为唯一防线" |
| §11 | 新增"Rev.O 新增的平台级守卫"表（rbac 守卫 + 附录一致性门禁，均注明不计入 A.4 collect 数） |
| §9.1 | 爆炸半径表新增 `tests/test_rbac_path_coverage.py`（+41 行，对现存 12 个 deep-diag 写端点零误伤）与 `tests/test_design_appendix_matches_repo.py`（全新）两行 |
| §12.2 | 新增验收项：rbac 由 3 项增至 **4 项且全绿**；附录一致性门禁 **4 项全绿** |
| 头部 / 修订记录 | Rev.O 定版说明与完整修订条目 |

### 2.4 观察项处理（OBS，非缺陷）

| 编号 | 处置 |
|---|---|
| OBS-1（`VERSION` 仍为 1.6.2.2） | 按报告移交 G 在 v1.6.3.0 打包时统一提升，本轮不动 |
| OBS-2（`extra="forbid"`） | 按报告不改（平台级改造需单独立项） |
| OBS-3（422 枚举缺 `string_type`） | **已顺带落实**：§5 错误表 422 行枚举放宽为"`string_too_short` / `Field required` / `string_type` 等 FastAPI 请求体校验错误" |
| OBS-4 / OBS-5 | 按报告不改 |

---

## 3. 变更清单

| 文件 | 变更 | 对应缺陷 |
|---|---|---|
| `tests/test_rbac_path_coverage.py` | +42 / −1 行（import 扩名 + helper + 平台级守卫用例） | DEF-SIT2-03 |
| `tests/test_table_type_stats.py` | 1 行（docstring `Rev.M §11` → `Rev.N §11`） | DEF-SIT2-01② |
| `tests/test_design_appendix_matches_repo.py` | 全新（72 行，附录↔仓库一致性门禁） | DEF-SIT2-01③ |
| `docs/DESIGN-v1.6.3.0-…说明书.md` | Rev.N → Rev.O（头部 / ADR-21 / KL-17 / §5 422 行 / §9.1 / §11 / §12.2 / 附录 A.2 与 A.4 / 修订记录） | 三项全部 + OBS-3 |

**冻结面核查**：`backend/**`（含服务层、API、迁移文件、`auth_service.py`）、
`frontend/**`、`backend/engine/**`、119 条规则**本轮零改动**——三项缺陷均为
文档/测试覆盖类，整改未触碰任何生产运行代码，与报告定性一致。

---

## 4. 验证证据

### 4.1 回归测试（MySQL 8 元数据库，本地全绿）

| 门禁 | 结果 | 与整改前对比 |
|---|---|---|
| `tests/test_table_type_stats.py` | **112 passed** | 不变（docstring 改动不影响 collect） |
| `test_rbac_path_coverage.py` + `test_app_routes_integrity.py` | **7 passed** | 6 → 7（+1 平台级守卫） |
| `test_design_appendix_matches_repo.py` | **4 passed** | 新增（A.1/A.2/A.3/A.4 逐字一致） |
| `test_rules.py` + `test_sit_rules.py` + `test_sit_v1_rules.py` | **105 passed** | 不变 |
| 全量 `tests/` | **1739 passed, 0 failed, 0 skipped** | 1734 → 1739（+1 守卫 +4 附录门禁），无既有失败变化 |

### 4.2 变异验证汇总（守卫"有牙齿"证明）

| 门禁 | 注入 | 结果 |
|---|---|---|
| rbac 平台级守卫 | `auth_service.py` 还原为修复前（摘除 P7） | 红灯并精确点名 `POST /api/v1/table-type-stats/run (table_type_stats.py)`；恢复后全绿 |
| 附录一致性门禁 | 向 `backend/api/table_type_stats.py` 追加 1 行 | A.2 项红灯；恢复后 4 项全绿 |

### 4.3 附录 ↔ 仓库逐字一致性终态

`tests/test_design_appendix_matches_repo.py` 4 项全绿即证明：
A.1（1178 行）/ A.2（81 行）/ A.3（46 行）/ A.4（2474 行、98 个 `def test_`）
与仓库落盘文件**逐字一致**，"照图施工级"文档的自洽性恢复，
且今后任何一侧单独漂移都会被该门禁自动拦截。

---

## 5. 遗留事项（移交 UAT / 打包 / 发布门禁）

| 项 | 说明 |
|---|---|
| OBS-1 `VERSION` / `APP_VERSION` 提升为 1.6.3.0 | 移交打包环节（G），发布前门禁核对 |
| 内网 UAT `lzbj_ecif` 六数字对账（215 / 0 / 117 / 98 / 215 / 78） | 需内网 TDSQL 环境 |
| T20 基线谓词性能证据（`EXPLAIN` + 耗时对比） | 发布前门禁，需最大内网实例 |
| T13 命令作用域 | 不阻断、不影响任何数字 |
| 目标环境（TDSQL/MySQL）全量回归 | 本地 MySQL 8 已 1739 全绿；内网部署后按部署手册再跑 |

---

## 6. 结论

第二轮 SIT 的 2 项 MINOR + 1 项 NIT 已全部关闭：

- **DEF-SIT2-01** 不只回填了附录，还按报告建议把"附录↔仓库逐字一致"固化为
  4 项参数化门禁——照图施工级文档的自洽性从"靠人记得回填"变成"漂移即红灯"；
- **DEF-SIT2-02** 一行文档修订，由上述门禁背书；
- **DEF-SIT2-03** 把"登记点枚举不全"这一已复发三次的缺陷类的防线，从
  硬编码 G14 路径的模块内用例升级为**平台级守卫**——变异验证证明它对
  下一个子模块（G15…）同样生效，且对现存 12 个 deep-diag 写端点零误伤。

全量回归 1739 通过、生产代码零改动、两道新门禁均通过变异检验。
**本模块 SIT 阶段可视为完成，请求进入 UAT。**
