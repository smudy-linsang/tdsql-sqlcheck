# v1.6.4.0 QC1 证据索引

本目录仅保存隔离合成环境证据。测试账号、数据库、模型答案、SQL 和日志均为 QC1 合成数据；不包含生产密钥或真实业务行。

## 浏览器证据

以下截图由真实浏览器操作产生；同名 `.txt` 或 DOM snapshot 保存可检索的页面文字：

- `04-ai-config-empty`：空环境 AI 配置页没有创建入口。
- `05-ai-routes-empty`、`06-ai-grants-empty`：路由和授权只有只读空表。
- `08-followup-session-conflict`：首轮后的同会话预览冲突。
- `09-business-context-missing`、`10-sql-entry-empty-draft`、`11-rule-entry-missing-source`：业务按钮没有把 scene/source/draft 带入 Copilot。
- `12-new-session-idempotency-conflict`、`32-new-intent-conflict`：新意图复用旧提交键。
- `14-selftest-ui-stale`：服务端自检终态没有刷新到当前页面。
- `15-configured-routes-blank`：API 已存在十条路由，但页面字段为空。
- `20-schema-check-result`：原上线检查渲染结果；共享测试库范围，不作四表或 TDSQL 容量结论。
- `31-disabled-audit-verified`：Copilot DB 设置关闭时原即时审核仍正常。
- `33-cross-account-private-state-leak`：开发员登录后看到管理员未提交草稿和旧预览状态。
- `34-editor-before-candidate`、`35-candidate-without-validation`、`36-editor-after-candidate`：未保存草稿被未标验证状态的候选 SQL 一键覆盖。

编号 31—36 使用显式标签页句柄和 full-page screenshot/DOM snapshot 双份保存。早期混合标签页辅助捕获已从交付证据中剔除。

## API 与出站证据

- `api-provider-fixture.json`：provider 创建 API。
- `api-route-grant-fixture.json`：十场景路由和两管理员合成授权。
- `api-ui-selftest-result.json`：页面发起自检后的服务端终态。
- `api-conversation.json`：同 session 三轮后端调用及跨账号 404。
- `model-wire-summary.json`：三次真实 HTTPS 出站均成功，但每轮 `history` 长度为 0。
- `api-revocation.json`：grant 撤销后 result/action/export 仍为 200，随后恢复测试 grant。
- `api-disable.json`、`api-restore.json`：设置关闭/恢复以及 `/help` 仍为 200。
- `api-conversation-network-degraded.json`：笔记本地址变化导致受控网关不可达时的降级证据；不计作产品缺陷。

## 自动化与发布证据

- `copilot-focused-summary.json`：117 passed。
- `clean-head-original-regression-summary.json`：干净 HEAD 原功能套件结果。
- `base-original-regression-summary.json`：冻结设计基线原功能套件结果。
- `clean-regression-comparison.json`：干净导出树的失败集合对照；环境依赖失败不自动豁免，也不误算新增缺陷。
- `clean-head-secret-gate.txt`：干净 HEAD 密钥守卫 2 passed。
- `offline-clean-install.txt`：无网络 Linux Python 3.11 安装与 `pip check`。

`prepare.py`、`harness.py`、`refresh_fixture_host.py`、`api_checks.py` 和回归脚本用于复现隔离环境；运行时证书、测试口令、网关原始请求和数据库数据均留在 ignored `data/reports/qc_o_1640`，不进入 Git。
