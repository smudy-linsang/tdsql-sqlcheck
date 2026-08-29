# v1.6.2.2 UAT 第五轮整改证据（Q 侧）

| 文件 | 说明 |
|---|---|
| `o22_workers2_verification.txt` | O-22 生产形态实测：`AUTH_ENABLED=true` + `uvicorn --workers 2` + 每次全新 TCP 连接——100 次跨连接首次消费 100/100 返回 200、100 次重放 100/100 返回 401；伪造/无票据均 401；GET 签发被拒（405，签发仅 POST）。 |

- 自动化用例：`tests/test_o15_gateway_report_security.py`（票据共享存储/原子消费/哈希不落明文/并发唯一中标）、`tests/test_o23_migration_fail_closed.py`（迁移失败关闭 8 用例）、`tests/test_o19_offline_instance_inspect.py`（O-24 未知异常 500 + X-Request-ID）。
- 门禁执行记录：`docs/evidence/v1.6.2.2/o21_gate_run.txt`（`RESULT PASS`，退出码 0）。
- 说明：验收用测试账号口令经环境变量 `O22_VERIFY_PASSWORD` 注入，不落明文；本地验证无 Nginx 前置，生产如经 Nginx 入口，O 第六轮按同一入口复核即可（票据为共享存储，与入口形态无关）。
