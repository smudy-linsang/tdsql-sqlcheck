# v1.6.3.0 G14 第二轮 UAT 证据索引

被测提交：`4c7a737c93e0a069c63ad929e6af2a4f854dafe8`

报告：[第二轮用户验收测试报告](../../UAT2-v1.6.3.0-G14表类型统计第二轮用户验收测试报告-智能体O.md)

## 可复现脚本

- `prepare_uat.py`：只允许使用隔离元数据库 `tdsql_uat_o_g14_r2_1630`，创建合成用户、角色、业务库和三类连接。
- `verify_uat.py`：复核版本、集中式统计、离线/不存在库/系统库状态码、auditor 403、独立表类型基线和历史落库情况。口令通过 `UAT_G14_PASSWORD` 注入，不写入仓库。

## 截图

| 文件 | 证明内容 |
|---|---|
| `01-login-version.png` | 登录页来自 1.6.3.0 被测服务 |
| `02-central-specific-scope.png` | 集中式 A 库返回 2/2/0/0，同时暴露范围时间为空 |
| `03-input-change-clears.png` | 只修改库名、未点击时旧结果立即消失 |
| `04-nonexistent-clean-error.png` | 不存在库提示可读，旧结果为空 |
| `05-instance-switch-clears.png` | 切换实例瞬间旧结果消失 |
| `06-distributed-nonproxy.png` | 普通 MariaDB 的 Proxy 语法失败被识别，失败库不进汇总 |
| `07-offline-readable-clean.png` | 离线实例可读错误，旧结果为空 |
| `08-cross-user-clean.png` | 最小权限用户未继承上一用户实例、输入和结果 |
| `09-auditor-disabled.png` | auditor 按钮禁用并显示只读提示 |
| `10-history-detail.png` | 历史含操作人，点击可回看逐库明细 |
| `11-stale-error-after-instance-switch.png` | 当前已切集中式，旧离线请求仍弹连接失败 |
| `12-empty-database.png` | 空业务库返回 1 库/0 表/OK |
| `13-system-database-rejected.png` | 系统库被明确拒绝且不残留旧结果 |

## 核心实测输出

```text
专项：139 passed, 0 failed
未改模块抽样：80 passed, 0 failed
硬编码敏感信息：2 passed, 0 failed
全量：1755 passed, 0 failed, 0 skipped

/health = 200, version 1.6.3.0
developer /run(A) = 200, total=2, single=2, broadcast=0, shard=0
developer /run(A) response created_at_present = false
offline /run = 422
missing database /run = 400
system database /run = 400
auditor /run = 403
independent information_schema: BASE TABLE=2, VIEW=1
table_type_stat: created_at IS NULL = 0
offline connection history rows = 0
browser console error/warn = 0
```

说明：真实 TDSQL Proxy 成功路径与 T20 未在本目录伪造；按报告第 7 节由内网智能体在受控生产上线后补齐。

收尾时工作区另有并发产生的 `deploy/tdsql-dev-cluster` 修改/未跟踪厂商文件，本目录没有改动或收录它们。上列敏感信息检查结果来自这些外部文件出现前的干净被测提交；外部文件出现后的命中不计作 G14 产品回归结果。
