# v1.6.2.2 第六轮 UAT 证据索引

- `full_regression.txt/xml`：在隔离元数据库和本轮自启 HTTP fixture 下执行的全量 pytest，1521 passed、28 skipped。
- `full_regression_without_service.txt`：确认 8000 端口无服务后的干净环境复现，8 个固定端口 HTTP 用例失败、1513 passed、28 skipped。
- `implementation_gate.txt`：正式三版本实现门禁，最终 `RESULT PASS`。
- `manifest_*`、`edge_*`、`load_*`、`head_*`：sqlglot 29.0.0、30.14.0、30.17.0 三版本独立矩阵。
- `rule_probe_current.*`、`supplemental_rule_probe.*`：119 条注册规则、1000 条主语料及补充命中证据。
- `round6_diff.json`：与第五轮签字基线的规则、边界、LOAD、语句头逐项差分。
- `targeted_probe.*`：TDSQL 分片/广播、CR 失败关闭、未知异常伪装、已登记错误列结构和票据一次性语义的独立探针。
- `http_results.json`：认证开启、双 worker 的真实 HTTP 结果；含 100 次签发、首次消费和重放。
- `browser_steps.json`、`01` 至 `09` PNG：管理员、DBA、开发、审计员真实浏览器点击步骤与截图。
- `index_actual.pdf`、`index_actual_page1.png`：真实导出 PDF 与 160 DPI 视觉渲染。
- `gateway_partial.log`、`gateway_xss.log`：部分解析与脚本上下文安全的合成输入。
- `summary.json`：机器可读的结论、计数和缺陷清单。
- `run_round6.py`、`run_gate.py`、`run_without_http.py`、`http_round6.py` 及各 probe 脚本：本轮可复现测试驱动。

所有数据库、连接、账号和日志均为本地合成 UAT 数据，不包含生产数据或口令。
