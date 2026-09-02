# v1.6.3.0 G14 第四轮 UAT 证据

本目录对应被测提交 `02c64fff2d73d3b2b236cc89aa99c2023907c89e`。测试使用独立应用端口 `18803`、独立元数据库 `tdsql_uat_o_g14_r4_1630`，未操作既有应用服务和生产数据库。

## 可复现材料

- `prepare_uat.py`：创建隔离 developer/auditor 用户，并登记本地 TDSQL 协议模拟靶场与离线样本。口令只从环境变量读取，不写入仓库。
- `verify_uat.py`：验证 health、G14 成功路径、400/422/403、三类 Proxy 返回形态、集合互斥及与独立 BASE TABLE 集合对账。
- `results-summary.json`：第四轮机器可读结果、真实内网证据摘要和准出裁决。
- `release-dependency-check.txt`：生产依赖边界与离线 wheel 缺失的可复现 P1 证据。

## 浏览器截图

- `01-target-success.png`：developer 真实点击后的 8/4/2/2/8 成功结果及采集范围。
- `02-history-detail.png`：历史总表和逐库明细。
- `03-stale-error-suppressed.png`：离线请求发出后立即切回靶场，迟到错误未串入新上下文，旧结果已清空。
- `04-current-error-visible.png`：当前范围的不存在库 400 仍显示可读提示。
- `05-auditor-readonly.png`：auditor 选择实例后“统计表类型”按钮禁用。
- `06-auditor-history.png`：auditor 可查看 developer 产生的历史。

浏览器控制台 `error/warn` 共 0 条。

## 自动化结果

```text
tests/test_g14_request_ownership_browser.py 连续执行两次
=> 4 passed / 4 passed

G14、前端状态、行为浏览器、版本、RBAC、设计一致性、路由专项
=> 149 passed, 4 warnings

全量 tests/
=> 1765 passed, 11 warnings, 0 failed, 0 skipped
```

11 条全量告警均为既有 Pydantic/Starlette/pytest/httpx 弃用类告警，无本轮新增失败。

## 新发现发布阻断

Q 将只用于浏览器自动化测试的 `playwright==1.62.0` 加入了生产 `requirements.txt`。该文件会被 Dockerfile 和离线发布/安装脚本直接使用；当前 `dist/wheels_tmp` 又没有 Playwright wheel。等价的无写入 dry-run 返回退出码 1 和 `No matching distribution found`，因此当前版本按既有离线发布路径无法完成依赖校验。

本轮结论为功能 UAT 通过、整体有条件通过。关闭办法是将 Playwright 移入测试/开发依赖并让 CI 显式安装，不要让生产运行依赖承担浏览器测试框架。

## 用户提供的真实 TDSQL 证据

原始文件位于用户桌面，可能包含内网主机、库表名等信息，未复制进 Git；本目录只保存不可逆摘要与核对结果：

| 文件 | SHA-256 |
|---|---|
| `99001` | `84C3128172E89CDD1313E047443C40A1B0380ED0392B02999BC77CFD307FF4DB` |
| `1.jpg` | `6000C82C1CB4A83FF63577E2374D06702AACA9D920AE6F21D3C024A8090052C8` |
| `2.jpg` | `D77D314B108FFF1D67DB7CE4856CBFCFFB24AF7DFC66DEBF15F3D1F1A01B8CF8` |

`99001` 显示真实版本 `8.0.33-v24-txsql-22.6.9-20250509`，对 `lzbj_ecif` 的三类结果为单表 0、广播表 117、分片表 98；脚本式复核完整列表后两两交集均为 0，并集 215。`information_schema.TABLES` 的 BASE TABLE 为 293；配合已核实的 78 张物理二级分区子表，逻辑基线为 215，六数字为 `215/0/117/98/215/78`，与 Proxy 口径精确一致。

原始耗时为 0.001/0.002/0.001/0.004 秒。产品负责人另确认该语法由 TDSQL 原厂工程师提供，且在内网多次执行、包括数千表数据库均为毫秒级。本轮据此将性能项登记为产品负责人风险接受/免测，不伪装成外网 T20 对比压测通过。
