# v1.6.3.0 G14 第一轮 UAT 证据说明

测试日期：2026-09-02

被测提交：`5f4e54b1a84db5fe4b8d99506fd818d0884083c3`

本地入口：`http://127.0.0.1:18800/`

隔离元数据库：`tdsql_uat_o_g14_r1_1630`

本目录只保存合成测试数据和页面截图，不保存测试口令、令牌或生产连接信息。

## 截图索引

| 文件 | 证明内容 |
|---|---|
| `01-login-version.png` | 登录页仍显示 `V1.6.2.2` |
| `02-central-all.png` | 集中式全部业务库统计成功，57 库/2207 BASE TABLE |
| `03-central-specific-view-excluded.png` | 指定库 2 张 BASE TABLE，1 个 VIEW 未计入 |
| `04-error-stale-result.png` | 输入不存在库收到错误，但上一轮空库结果仍留在页面 |
| `05-history-detail.png` | 历史列表及逐库明细可回放，操作人正确 |
| `06-distributed-nonproxy.png` | 非 Proxy 端点返回 `PROXY_CMD_FAILED` 与 `NOT_DISTRIBUTED_ENDPOINT`，失败库不进汇总 |
| `07-offline-stale-result.png` | 切换到离线实例后仍显示上一实例结果 |
| `08-cross-user-stale-result.png` | 前一用户退出、最小权限用户登录后仍显示前一用户结果 |
| `09-auditor-generic-error.png` | auditor 可见表类型统计页面；POST 被后端拒绝，但前端仅提示“执行失败” |
| `10-offline-500.png` | 离线实例请求结束后仍显示上一实例结果；服务端对应请求为 HTTP 500 |
| `10-offline-500.txt` | 离线实例 500 的服务日志关键摘录与异常边界定位 |

## 自动化与独立数据核对

```text
G14 专项、RBAC 路径、设计附录一致性、路由完整性：123 passed, 3 warnings, 27.18s
既有规则、前端、RBAC、实例权限抽样回归：80 passed, 5 warnings, 41.64s

information_schema 独立核对：
tdsql_uat_g14_r1_a / BASE TABLE = 2
tdsql_uat_g14_r1_a / VIEW       = 1
tdsql_uat_g14_r1_b / BASE TABLE = 1

浏览器指定库结果：
tdsql_uat_g14_r1_a / 总表=2 / 单表=2 / 广播表=0 / 分片表=0 / 逻辑基线=2
结论：BASE TABLE 数一致，VIEW 正确排除。
```

## 环境边界

本地 `127.0.0.1:13306` 不是 TDSQL Proxy。它可以证明集中式路径、Proxy 语法错误识别和错误展示，不能证明真实分布式单表/广播表/分片表分类成功。真实分布式全链路与最大实例 T20 性能证据必须在内网 UAT 环境补齐。
