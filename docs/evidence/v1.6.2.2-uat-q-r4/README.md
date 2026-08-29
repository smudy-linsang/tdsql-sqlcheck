# v1.6.2.2 UAT 第四轮整改证据（Q 侧）

| 文件 | 说明 |
|---|---|
| `o15_browser_report.png` | O-15 真实 Chromium 验证：含恶意标记日志的网关报告在抽屉 iframe 内正常渲染（标题/章节/统计卡片可见），交互脚本在 nonce 制 CSP 下运行，恶意 `</script>`/`<img onerror>` 标记未执行。 |

门禁执行记录：`docs/evidence/v1.6.2.2/o21_gate_run.txt`（`run_all.py --mode implementation --matrix`，`RESULT PASS`，退出码 0）。
