# v1.6.2.2 第四轮 UAT 证据

- 被测提交：`820f0dd1ec0f16bc0b94c65443206b014994f74d`
- 执行人：智能体O
- 日期：2026-08-29
- 结论：不通过；2 BLOCK、4 MAJOR、2 MINOR。

核心结果：全量 `1450 passed`；三套 sqlglot 独立矩阵通过；119 条注册规则中主语料+补充上下文证明 116 条可命中；1000 条主语料无整体漂移。阻断证据为 `targeted_probe.json` 的 CR 绿色通过，以及 `01-gateway-iframe-refused.png`/`http_results.json` 的网关报告嵌入与脚本上下文问题。

完整结论和整改要求见 `../../UAT-v1.6.2.2-第四轮全项目用户验收测试报告-智能体O.md`。
