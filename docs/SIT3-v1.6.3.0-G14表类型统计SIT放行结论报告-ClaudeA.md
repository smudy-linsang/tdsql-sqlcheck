# SIT3-v1.6.3.0 深度诊断·表类型统计（G14）SIT 放行结论报告

| 项 | 内容 |
|---|---|
| 被测对象 | v1.6.3.0 G14 表类型统计子模块，**第二轮 SIT 整改版**（提交 `0f01346`） |
| 整改方报告 | `docs/FIX2-v1.6.3.0-…第二轮SIT整改完成情况报告-ClaudeQ.md` |
| 设计基线 | `DESIGN-v1.6.3.0-…详细设计说明书.md` **Rev.O**（第二轮 SIT 整改定版） |
| 测试类型 | 第三轮 SIT（定点复核 + 门禁有效性变异验证 + 全量回归对照 + 放行判定） |
| 测试人 | 智能体 A |
| 测试日期 | 2026-09-02 |
| **结论** | **✅ 放行，可进入 UAT 阶段。** 三轮 SIT 累计 6 项缺陷全部关闭；生产运行代码零遗留问题。进入 UAT 前建议先由 G 提升 `VERSION`（见 §5.1）。 |

---

## 1. 三轮 SIT 缺陷全景

| 轮次 | 编号 | 级别 | 问题 | 关闭轮次 | 本轮复核 |
|---|---|---|---|---|---|
| 一 | DEF-SIT-01 | **BLOCK** | `/api/v1/table-type-stats/` 漏登记 `_OPERATIONAL_WRITE_PREFIXES`，developer 与全部自定义角色写端点恒 403 | Rev.N | ✅ 仍关闭 |
| 一 | DEF-SIT-02 | MINOR | 空 `connection_id` 实际 422，契约文档写 400 | Rev.N | ✅ 仍关闭 |
| 一 | DEF-SIT-03 | MINOR | 全空白 `connection_id` 报错指向"未连接实例"，服务层守卫 HTTP 不可达 | Rev.N | ✅ 仍关闭 |
| 二 | DEF-SIT2-01 | MINOR | 附录 A.4 未随 Rev.N 回填，正文称 112 项而附录只有 110 项 | Rev.O | ✅ **本轮关闭** |
| 二 | DEF-SIT2-03 | MINOR | 防复发只做到模块级，平台守卫不校验第一级放行清单 | Rev.O | ✅ **本轮关闭** |
| 二 | DEF-SIT2-02 | NIT | 附录 A.2 文件头 docstring 仍写 `Rev.M §5` | Rev.O | ✅ **本轮关闭** |

**零新增缺陷。**

---

## 2. 本轮三项的关闭核验

### 2.1 DEF-SIT2-01 / DEF-SIT2-02 —— 附录与仓库逐字一致

用**我自己独立写的抽取脚本**（不复用 Q 的门禁用例，避免"用被测物证明被测物"）重新
比对 Rev.O 附录 A.1～A.4 与仓库落盘：

| 附录 | 落盘文件 | 设计行数 | 仓库行数 | 结果 |
|---|---|---:|---:|---|
| A.1 | `backend/services/table_type_stats_service.py` | 1178 | 1178 | **逐字一致** ✅ |
| A.2 | `backend/api/table_type_stats.py` | 81 | 81 | **逐字一致** ✅（NIT 关闭） |
| A.3 | `backend/schema/v13/130_table_type_stats.sql` | 46 | 46 | **逐字一致** ✅ |
| A.4 | `tests/test_table_type_stats.py` | **2474** | **2474** | **逐字一致** ✅（+45 行 / +2 用例已回填） |

自洽性复核：仓库 `def test_` 计 98 个、`pytest --collect-only` 实际 **112 项**，
与文档三处"collect 112 项"的声明一致；`test_sit01_*` / `test_sit03_*` 均已进入附录。

### 2.2 新增门禁① `tests/test_design_appendix_matches_repo.py` 的有效性（变异验证）

一条不会红的断言等于没有断言，因此逐条注入缺陷验证：

| 变异 | 操作 | 结果 |
|---|---|---|
| M0 | 现版本 | **4 passed** ✅ |
| M1 | 在 `backend/api/table_type_stats.py` 里把一句注释改成别的字 | **1 failed** ✅，且报文精确到：`附录 A.2（backend/api/table_type_stats.py）与仓库落盘不一致：设计行数=81 仓库行数=81 首个差异在第 59 行`，并同时打印设计侧与仓库侧的原文 |
| M2 | 还原 | **4 passed** ✅ |

**结论：门禁有牙齿，且失败信息直接告诉作者改哪一行。**

### 2.3 新增门禁② 平台级 RBAC 两级登记守卫的有效性（变异验证）

| 变异 | 操作 | 新守卫 | 旧守卫 `test_all_write_endpoints_are_mapped` |
|---|---|---|---|
| M0 | 现版本 | **4 passed** ✅ | passed |
| M1 | 把 P7 前缀摘掉（等价 Rev.M 修复前） | **1 failed** ✅，精确点名 `POST /api/v1/table-type-stats/run  (table_type_stats.py)` | **仍 passed** |
| M2 | 还原 | **4 passed** ✅ | passed |

M1 的对照特别值得记录：**同一条件下旧守卫依旧全绿**——这就是第一轮 DEF-SIT-01
能漏到 SIT 的原因，也证明新守卫补上的正是那个真实存在的盲区，不是叠床架屋。

代码实现与我在第二轮报告 §6.2.4 给出的方案**逐字一致**，`_menu_keys` helper、
只对 `deep-diag*` 生效的收敛条件、import 追加三处都照办，未自行改动。

### 2.4 Rev.O 设计文档同步

| 位置 | 要求 | 落实 |
|---|---|---|
| ADR-21 | 钉住位置改为平台级守卫，子模块用例降为补充 | ✅ 原文已加入 |
| KL-17 | 人工 grep 对照降级为可选辅助，改为自动拦截 | ✅ 原文已改写 |
| §9 修改清单 | 两个测试文件入表，爆炸半径标注 | ✅ 两行均在，标注"零" |
| §11 测试设计 | 平台级守卫另立表，注明不计入 A.4 collect 数 | ✅ |
| §12.2 验收清单 | 两条新门禁入验收项 | ✅ |
| OBS-3 | §5 的 422 枚举放宽（`null` 是第三种 `string_type`） | ✅ 顺带落实 |

---

## 3. 爆炸半径与零次生灾害

### 3.1 本轮改动面

`git diff 0aec853..0f01346 --name-only` 全部 5 个文件：

```
docs/DESIGN-v1.6.3.0-…说明书.md          （Rev.N → Rev.O）
docs/FIX2-…整改完成情况报告-ClaudeQ.md    （新增）
tests/test_design_appendix_matches_repo.py（新增，72 行）
tests/test_rbac_path_coverage.py          （+41 行）
tests/test_table_type_stats.py            （±1 行：docstring 版本号）
```

**`backend/` / `frontend/` / `config/` 变更文件数均为 0——生产运行代码本轮零改动。**

### 3.2 权限判定矩阵

`backend/` 未动，故重新导出 159 路径 / 188 操作 × 4 角色 = **752 条判定**，
与第二轮快照逐键比对：**变化 0 条**。

### 3.3 全量回归对照

| 轮次 | 代码 | 结果 |
|---|---|---|
| 第二轮控制组（修复前） | `35e05ad` 之码 | 4 failed / **1623** passed / 83 skipped |
| 第二轮修复后 | `e94f3b6` | 4 failed / **1625** passed / 83 skipped |
| **本轮** | `0f01346` | 4 failed / **1630** passed / 83 skipped |

失败集合与第二轮 `diff` **完全一致**（仍是那 4 项沙箱环境差异：`o23` 默认值归一化 ×2、
`monitordb` 夹具、`file_report_delete` 夹具，与 G14 无关）。
通过数 `1625 → 1630`，净增 5 = 附录门禁 4 条参数化 + RBAC 守卫 1 条，**完全可解释**。

> 首跑再次出现 6 项 `*_without_connection` 伪回归，原因与第二轮同：我为浏览器验收
> 把 `sit2-central` 设回 `is_default=1`，跑批本身又会清零该标志。已复现并排除，
> 第二跑即为上表数据。这是**测试方夹具**问题，不是被测物问题。

### 3.4 冻结面（从落盘 `0c0b3b4` 到现在）

| 路径 | 变更 |
|---|---|
| `backend/schema/**`（含 `v13/130_table_type_stats.sql`） | **零** ✅ |
| `backend/services/table_type_stats_service.py` | **零** ✅ |
| `backend/engine/**` + 119 条规则 | **零** ✅ |
| `frontend/**` | **零** ✅ |
| 其余 9 个深度诊断子模块 | **零** ✅ |

**三轮 SIT 整改累计的生产代码改动只有 2 个文件、净 +11 行**
（`table_type_stats.py` +13/−2、`auth_service.py` +1）。

---

## 4. 放行门禁逐项复核

| 门禁 | 结果 |
|---|---|
| `tests/test_table_type_stats.py` | **112 passed**（需 `SQLCHECK_DB_NAME=tdsql_sqlcheck_test`，见第二轮报告 §8.2） |
| `tests/test_rbac_path_coverage.py` | **4 passed**（3 → 4，新增平台级第一级守卫） |
| `tests/test_design_appendix_matches_repo.py` | **4 passed**（全新） |
| `tests/test_app_routes_integrity.py` | **3 passed** |
| 119 条审核规则（`test_rules` + `test_sit_rules` + `test_sit_v1_rules`） | **94 passed / 11 skipped**（collect 105） |
| 既有 RBAC 矩阵（`test_v2_rbac_matrix` + `test_v3_rbac_instances`） | **19 passed** |

### 4.1 第一轮三项缺陷的行为回归（真实 HTTP，完整中间件栈）

| 角色 | G14 `POST /run` | G5 `POST /index-audit/run` | G14 `GET /history` |
|---|---|---|---|
| `sit_admin` (admin) | 200 | 200 | 200 |
| `sit_dba` (dba) | 200 | 200 | 200 |
| `sit_dev` (developer) | **200** | 200 | 200 |
| `sit_aud` (auditor) | 403 | 403 | 200 |
| `sit_tt` (最小权限自定义角色) | **200** | **403** | 200 |

最后一行是关键：自定义角色**放行了 G14、同时挡住了未授权的 G5**——第一级恢复的同时
第二级菜单可见性仍然生效，没有越权外溢。

| 入参 | 实测 | 契约 |
|---|---|---|
| 空串 / 缺字段 / `null` | 422 `string_too_short` / `missing` / `string_type` | ✅ Rev.O §5 |
| 三空格 / 全角空格 U+3000 | 400 `必须指定 connection_id（…）` | ✅ |
| 不存在的 ID | 400 `未连接TDSQL实例或连接不存在` | ✅ 两类错误文案仍然分开 |

### 4.2 端到端数字对账

| 项 | 被测结果 | 独立事实（直查 `information_schema`） |
|---|---|---|
| 总表 / 单表 / 逻辑基线 | 208 / 208 / 208 | 208 |
| 库数 | 15 | 14 有表 + 1 空库 |
| 分片 / 广播 / 二级分区子表 / 失败 / 重叠 | 0 / 0 / 0 / 0 / 0 | 集中式分支应当如此 |

### 4.3 真实浏览器验收（重跑）

`sit_dev`（developer）与 `sit_tt`（最小权限自定义角色）分别用 Chromium 走完整流程
（登录 → 深度诊断 → 选实例 → 表类型统计 → 点击"统计表类型"）：

| 观测 | `sit_dev` | `sit_tt` |
|---|---|---|
| 页签可见 / 按钮可点 | ✅ / ✅ | ✅ / ✅ |
| `POST /run` 网络响应 | **200** | **200** |
| 出现 403 / 无权 / 权限不足 | 否 | 否 |
| 渲染结果 | `实例类型 集中式 · 库 15 · 总表 208 · 单表 208 · 广播表 0 · 分片表 0 · 逻辑基线 208` | 同左 |

---

## 5. 移交 UAT 的清单

### 5.1 进入 UAT 前建议先办（1 项，非代码问题）

| 项 | 说明 |
|---|---|
| **提升 `VERSION` / `backend/config.py:APP_VERSION` 到 `1.6.3.0`** | 当前仍是 `1.6.2.2`，登录页页脚也显示 `V1.6.2.2`。设计 §9 未把它列入 G14 改动面（属打包环节），但**若不先提升，O 的 UAT 记录里版本号是错的，缺陷追溯会对不上**。建议由 G 打包时一并处理后再开工。 |

### 5.2 UAT 必须覆盖的头号未验证面

**分布式实例的成功路径，在本地沙箱上从未被真实验证过。**

沙箱是 MariaDB，`/*proxy*/show table with shardkey` 等三条命令必然 1064，
因此三轮 SIT 对分布式分支只验证了**失败路径**（W1 `PROXY_CMD_FAILED` +
W8 `NOT_DISTRIBUTED_ENDPOINT` + 逐库 FAILED + 不污染实例级汇总，均正确）。
下列逻辑只在单元测试的桩数据下跑过，**没有在真实 TDSQL Proxy 上跑过一次**：

* `_collect_distributed` 的三条命令解析与 `shape` 列名回传
* `_split_qualified`（`库名.表名` 拆分）与 `_NameSpace` 的 canonical 库名解析
* 三类结果集互斥性、`overlap_count` 计数
* `_classify_subpartitions` 对 `<父表>_tdsql_subp<数字>` 的三条件判定与剔除

**建议 O 把内网 `lzbj_ecif` 的六数字对账（215 / 0 / 117 / 98 / 215 / 78）作为
UAT 的第一优先级用例**——它一次性覆盖上述全部逻辑，也是本子模块唯一的真值锚点。

### 5.3 其余移交项

| 项 | 说明 |
|---|---|
| T20 基线谓词性能证据 | 在最大内网实例上留存 `EXPLAIN` + 耗时，确认普通 `IN` 仍可下推（ADR-27），发布前门禁 |
| 目标环境全量回归 | 沙箱 MariaDB 与生产 TDSQL/MySQL 8.0.33-v24-txsql 有已知差异（整型显示宽度、字符串默认值引号），本地门禁数据不能替代内网重跑一次 |
| T13 命令作用域 | 不阻断、不影响任何数字，UAT 顺带确认即可 |
| 集中式实例交叉验证 | 建议至少取一台内网集中式实例，与 `information_schema` 做同样的数字对账 |

### 5.4 观察项（不影响放行，登记备查）

| 编号 | 观察 | 建议 |
|---|---|---|
| OBS-6 | `test_design_appendix_matches_repo.py` 在设计文档不存在时走 `pytest.skip`，属 fail-open；本项目其余机制（迁移器、RBAC 守卫）都是 fail-closed | 后续把 `skip` 改为 `fail`，或在文件名/用例名里点明它只覆盖 G14 一份设计文档 |
| OBS-2 | `StatsRequest` 未设 `extra="forbid"`，客户端把 `database` 误写成 `databases` 会被静默忽略，定向统计悄悄变成全实例扫描 | 平台级议题（全平台请求模型同此口径），需单独立项，不宜 G14 单独收紧 |

---

## 6. 放行判定

**✅ 同意放行进入 UAT。**

判定依据：

1. **三轮 SIT 累计 6 项缺陷（1 BLOCK + 4 MINOR + 1 NIT）全部关闭**，且每一项都由
   独立于整改方的手段复核过——不是看提交信息，是重新抽取附录做逐字比对、
   重新导出权限矩阵做逐键比对、重新用真实浏览器点一遍按钮。
2. **两条新门禁经变异验证确实有牙齿**：注入原缺陷即红灯并精确点名；
   尤其 RBAC 守卫的 M1 对照证明旧守卫在同条件下依旧全绿——补的正是真实盲区。
3. **生产代码零遗留问题**：三轮整改累计只动 2 个文件、净 +11 行；迁移文件、服务层、
   前端、`engine`、119 条规则、其余 9 个深度诊断子模块全程零变更。
4. **回归可解释**：失败集合三轮完全一致且均为沙箱环境差异；通过数的每一次增加
   都能对应到具体新增用例。

需要 O 在 UAT 阶段特别留意的，是 §5.2 那一条：**分布式成功路径至今没有真实环境的
证据**。SIT 能做的到此为止，剩下的只有内网真机能回答。
