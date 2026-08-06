# DEV-v1.6.1.1 实例管理分页筛选与显示优化、默认库屏蔽扩展 开发记录

> 依据设计：`docs/DESIGN-v1.6.1.1-实例管理分页筛选与显示优化及默认库屏蔽扩展详细设计说明书.md`
> 基线：v1.6.1.0（`main @ c28dc1a`）→ 交付版号 **v1.6.1.1**
> 性质：纯前端 + 常量变更，无 DDL、无接口变更

## 一、变更清单

| 文件 | 变更 |
|---|---|
| `frontend/static/js/app.js` | 新增 `connFilters`（name/address/type）、`connPage`、`connPageSize`、`filteredConnections`（连接名不区分大小写模糊 + 地址 contains + 类型精确，三条件 AND）、`pagedConnections` 切片、`onConnFilterChange` 回页 1；return 暴露 |
| `frontend/index.html` | ① 实例管理 card 内新增筛选工具栏（filter-bar 模式，含"共 N / M 条"计数）；② 表格 `:data` 改绑 `pagedConnections`；③ 表下 `el-pagination`（total/prev/pager/next/sizes，20/50/100）；④ 地址列 200→260 + `td-nowrap`；操作列 360→480 + `td-nowrap`；WARNING 列 110→120 + `th-nowrap`；⑤ 版本位 → V1.6.1.1；app.js 缓存参数 → `?v=20260806.3` |
| `frontend/static/css/app.css` | 追加 `.td-nowrap .cell{white-space:nowrap;}` 与 `.th-nowrap .cell{white-space:nowrap;}`（Element Plus class-name/label-class-name 精准挂载，不波及其他表） |
| `backend/services/zk_scan_enrich_service.py` | `SYSTEM_DATABASES` 增加 `query_rewrite`、`xa`（单源常量，预检侧自动同步） |
| `backend/config.py` / `VERSION` | 版号 → 1.6.1.1 |

## 二、关键实现决策

1. **客户端分页/筛选**：连接清单接口已全量返回，数百行规模与 ZK 发现列表同构，
   复用其 computed 模式，不引入新接口回归面；
2. **一行全显不用省略**：地址/操作按负责人硬性要求加宽 + nowrap 双保险，
   容器不足时 el-table 横向滚动、操作列右固定不受影响；
3. **默认库屏蔽边界不变**：手工业务库不过滤、历史连接不追溯。

## 三、自测摘要（详见 TEST 文档）

- 新增 `tests/test_zk_v1611.py` 3 用例（两路径排除 query_rewrite/xa + 前端结构守卫）；
- 适配缓存版本断言；全量回归 **1286 通过 / 0 失败 / 0 跳过**（R-18）；`node --check` 通过。
