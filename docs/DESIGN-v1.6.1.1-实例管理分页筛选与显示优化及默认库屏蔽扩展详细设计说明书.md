# DESIGN-v1.6.1.1 实例管理分页筛选与显示优化、默认库屏蔽扩展 详细设计说明书

> 版本：v1.6.1.1（设计稿，待评审；评审通过后方可实施）
> 作者：智能体 Q
> 输入：项目负责人内网生产（v1.6.1.0）实测反馈 4 项 + 截图 4 张
> 基线：`main @ c28dc1a`（v1.6.1.0）

---

## 1. 背景与问题定义

| # | 反馈 | 现状核查（`frontend/index.html` L1088-1131） |
|---|---|---|
| Q1 | 实例管理无分页，实例多时体验差 | `el-table :data="savedConnections"` 全量渲染，无 `el-pagination` |
| Q2 | 无筛选；需按"连接名（模糊）/地址/类型"筛选 | 无任何 filter 控件 |
| Q3 | "WARNING上限"表头换行；"地址"内容换行；"操作"内容换行；**地址与操作必须一行全显** | 地址列 `width=200`、操作列 `width=360`、WARNING 列 `width=110`，均不足 |
| Q4 | 导入预览列出 `query_rewrite`、`xa` 两个实例默认库，应同 sysdb 一并屏蔽 | `SYSTEM_DATABASES={information_schema,mysql,performance_schema,sys,sysdb}`，未含二者 |

## 2. 变更一：实例管理分页 + 三维筛选（Q1/Q2，纯前端）

数据源 `savedConnections` 由 `loadSavedConnections()` 一次性全量加载（与 ZK 发现列表同构），
故采用**客户端**分页+筛选，复用 ZK 发现列表的既有模式（`zkFilteredDiscovered`/`zkPagedDiscovered`）：

- 新增状态：
  ```js
  const connFilters = reactive({name:'', address:'', type:''});   // type: ''|'distributed'|'centralized'
  const connPage = ref(1);
  const connPageSize = ref(20);
  ```
- 新增 computed：
  - `filteredConnections`：连接名**模糊**（不区分大小写 contains）、地址 contains（`host:port/database` 串）、
    类型按 `effective_instance_type`（缺省 distributed）精确匹配；三条件 AND；
  - `pagedConnections`：`(connPage-1)*pageSize` 切片；
- 表格 `:data` 改绑 `pagedConnections`；表格下方 `el-pagination`
  （`layout="total,prev,pager,next,sizes"`，`page-sizes [20,50,100]`，total 绑 `filteredConnections.length`）；
- 筛选工具栏置于 card-head 与表格之间，样式复用全站 `filter-bar` 模式
  （连接名 `el-input` 模糊、地址 `el-input`、类型 `el-select` 全部/分布式/集中式，均 clearable）；
- 筛选变更时 `connPage=1`（`@clear`/`@input`/`@change` 统一走一个 `onConnFilterChange`）；
- 跨页选择不涉及：本页无勾选列，无合并语义负担。

**不改后端**：连接清单接口已全量返回，数百行规模客户端分页足够，避免引入新接口的回归面。

## 3. 变更二：三处换行治理（Q3）

原则：**地址、操作内容必须一行完整显示**（负责人硬性要求），不用 tooltip 省略替代。

| 位置 | 现状 | 改法 |
|---|---|---|
| WARNING上限 表头 | `width=110`，表头两行 | `width=120` + `label-class-name="th-nowrap"` |
| 地址 内容 | `width=200` 换行 | `width=260` + `class-name="td-nowrap"`（实测最长 `10.243.20.15:15197/tdsql_sqlcheck` 33 字符≈230px，260 留余量） |
| 操作 内容 | `width=360`，"删除"掉行 | `width=480` + `class-name="td-nowrap"`（最大形态 7 个 link 按钮：连接/设为默认/编辑/探测类型/锁定类型/探测诊断/删除 ≈460px） |

- 新增全局 CSS（`<style>` 内）：
  ```css
  .td-nowrap .cell{white-space:nowrap;}
  .th-nowrap .cell{white-space:nowrap;}
  ```
  （Element Plus 的 `class-name` 作用于体单元格、`label-class-name` 作用于表头单元格，精准不波及他表）
- 列宽合计增大后，容器不足时 `el-table` 自带横向滚动，操作列已 `fixed="right"`，体验不受影响；
- 连接名列保留 `show-overflow-tooltip`（名称超长省略+提示，不在本次硬性要求内）。

## 4. 变更三：屏蔽 query_rewrite / xa（Q4）

- `zk_scan_enrich_service.SYSTEM_DATABASES` 增加 `"query_rewrite"`、`"xa"`
  （TDSQL 实例默认库：查询改写 / XA 事务管理库，非业务库，不纳入 SQL 审核）；
- 单源常量自动传导：扫描富集 `business_dbs` 与导入预检候选行同步屏蔽（v1.6.1.0 已统一常量源）；
- 边界保持不变：手工业务库（manual_databases）不过滤；历史已导入连接不追溯。

## 5. 测试计划

1. 新增 `tests/test_zk_v1611.py`：
   - 富集/预检两路径排除 `query_rewrite`、`xa`（假目录含二者 → 结果不含）；
   - 前端结构守卫：index.html 实例管理表绑 `pagedConnections`、存在 `el-pagination`、
     三个筛选控件（`connFilters.name/address/type`）、`td-nowrap`/`th-nowrap` 类与 CSS、
     地址列 `width="260"`、操作列 `width="480"`；
2. 既有用例无冲突（sysdb 用例继续有效）；全量回归 R-18 零跳过；`node --check app.js`。

## 6. 版本与交付

- 版号 **v1.6.1.1**；VERSION / config / 前端标题同步；app.js 缓存参数递增；
- 纯前端 + 常量变更，无 DDL、无接口变更，回滚直接退包。
