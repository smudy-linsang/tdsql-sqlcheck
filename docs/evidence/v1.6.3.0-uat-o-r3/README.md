# v1.6.3.0 G14 第三轮 UAT 证据索引

被测提交：`37ea3eab1bb7f8e40ca28acac00d78ca8889768f`

报告：[第三轮用户验收测试报告](../../UAT3-v1.6.3.0-G14表类型统计第三轮用户验收测试报告-智能体O.md)

## 可复现材料

- `prepare_uat.py`：只允许使用隔离元数据库 `tdsql_uat_o_g14_r3_1630`，创建合成用户并登记靶场、集中式对照和离线连接；口令仅从环境变量读取。
- `verify_uat.py`：复核健康版本、靶场统计、空分类结果集、三条 Proxy 命令原始集合、独立 BASE TABLE 基线、响应/历史同源时间、400/422/403，以及模拟网关的会话数据库边界。
- `results-summary.json`：无凭据的结构化结果摘要。

## 浏览器截图

| 文件 | 证明内容 |
|---|---|
| `01-target-success.png` | 高仿分布式靶场显示 1 库、总表 8、单表 4、广播 2、分片 2、基线 8，并显示采集时间 |
| `02-history-detail.png` | 最新历史的时间、操作人、六个数字及逐库明细 |
| `03-stale-error-suppressed.png` | 离线请求发出后立即切回靶场，等待 2.6 秒无旧错误提示、无旧结果且按钮可用 |
| `04-current-error-visible.png` | 当前请求的不存在库错误仍可读，未被迟到请求抑制逻辑误吞 |
| `05-auditor-readonly.png` | 跨用户初始状态为空，auditor 的统计按钮禁用 |
| `06-auditor-history.png` | auditor 可读取 developer 产生的历史记录 |
| `07-unchanged-module-smoke.png` | 共享前端及未改模块冒烟页面正常渲染 |
| `08-empty-category-sets.png` | Proxy 返回空广播/分片集合时，页面正确显示 1/1/0/0 |

## 核心实测

```text
G14/权限/路由/版本/设计六件组：145 passed
全量：1761 passed, 0 failed, 11 warnings, 307.14s

/health = 200, version 1.6.3.0
靶场指定库 = 8 total / 4 single / 2 broadcast / 2 shard / 8 baseline
三类集合两两互斥；并集与独立 information_schema BASE TABLE 集合一致
空分类库 = 1 total / 1 single / 0 broadcast / 0 shard
响应 created_at 与 stat_id 对应历史行精确到秒一致
不存在库 = 400；离线实例 = 422；auditor 直接 POST = 403
浏览器 console error/warn = 0
```

## 证据边界

该靶场不是腾讯 TDSQL 产品：`tdsql_proxy.py` 是 Python 编写的 MySQL 协议模拟器，后端为标准 MySQL 8，三条专有命令按模拟器自建元数据生成。它可证明当前应用的 TCP 链路、`COM_INIT_DB`、结果形态、汇总、留档和页面展示，但不能替代真实 TDSQL Proxy 集合对账或 T20 优化器性能测试。
