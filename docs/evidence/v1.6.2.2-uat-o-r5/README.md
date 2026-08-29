# v1.6.2.2 第五轮 UAT 证据索引

- `full_regression.txt/xml`：全量 pytest，1506 passed、28 skipped。
- `implementation_gate.txt`：正式三版本门禁，最终 `RESULT PASS`。
- `manifest_*`、`edge_*`、`load_*`、`head_*`：三套 sqlglot 版本独立矩阵。
- `rule_probe_current.*`、`supplemental_rule_probe.*`：119 条注册规则、1000 条主语料及补充命中证据。
- `round5_diff.json`：与第四轮签字基线的差分。
- `targeted_probe.*`：CR 失败关闭、TDSQL 分片/广播、临时池、票据语义与未知异常故障注入。
- `http_results.json`：认证开启、双 worker 的真实 HTTP 验证；含 30 次新连接票据消费结果。
- `migration_fail_closed_probe.*`：索引迁移首个 ALTER 失败后仍被记录为已应用的独立证明。
- `browser_steps.json`、`01` 至 `09` PNG：四角色真实浏览器点击步骤与截图。
- `index_actual.pdf`、`index_actual_page1.png`：真实导出 PDF 与视觉渲染。
- `gateway_partial.log`、`gateway_xss.log`：部分解析与脚本上下文安全的合成输入。

所有数据库、连接、账号和日志均为本地合成 UAT 数据，不包含生产数据或口令。
