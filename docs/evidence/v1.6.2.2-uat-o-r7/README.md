# v1.6.2.2 第七轮 UAT 证据说明

本目录是智能体O对提交 `e38c3d1f9d4e012a3a49cb15eb10d5862b90c630` 的独立第七轮 UAT 证据。所有数据库、用户、日志和浏览器数据均为 `uat_o_r7` / `tdsql_uat_o_r7` 前缀的隔离合成夹具；未连接生产数据。

## 关键证据

| 证据 | 用途 |
|---|---|
| `full_regression.txt` / `full_regression.xml` | 8000 无服务时全量回归：1538 passed / 28 skipped |
| `full_regression_occupied_port.txt` | 8000 被无关服务占用时全量回归仍为 1538 passed / 28 skipped |
| `implementation_gate.txt` | 正式实现门禁及三版本矩阵，最终 `RESULT PASS` |
| `independent_matrix.json` | sqlglot 29.0.0 / 30.14.0 / 30.17.0 独立矩阵退出码 |
| `rule_probe_current.*` | 119 条注册规则、1000 条主语料及精确期望差异 |
| `round7_diff.json` | 1000/324/27/77 与第六轮被测基线逐项差分 |
| `supplemental_rule_probe.*` | R025/R035/R038/R049/R059 补充阳性探针 |
| `targeted_probe.*` | O-25/O-26 独立故障注入及 O-28/O-29/O-30 复现 |
| `http_results.json` | 认证、双 worker、R054/R077、网关、票据、RBAC、PDF、EXPLAIN、离线错误合同 |
| `browser_steps.json` / `01` 至 `10` PNG | 管理员、开发、审计、DBA、未分配实例管理自定义角色的真实点击验收 |
| `index_actual.pdf` / `index_actual_page1.png` | 实际下载 PDF 及 150 DPI A4 视觉核验 |

## 可复现入口

- `python docs/evidence/v1.6.2.2-uat-o-r7/run_round7.py full`
- `python docs/evidence/v1.6.2.2-uat-o-r7/run_round7.py probes`
- `python docs/evidence/v1.6.2.2-uat-o-r7/run_round7.py matrix`
- `python docs/evidence/v1.6.2.2-uat-o-r7/run_gate.py`
- `python docs/evidence/v1.6.2.2-uat-o-r7/targeted_probe.py`
- `python docs/evidence/v1.6.2.2-uat-o-r7/run_occupied_port.py`

脚本均要求隔离测试数据库；`run_occupied_port.py` 会精确启动并清理本轮创建的无关 8000 服务。HTTP 与浏览器脚本还需要本地 MySQL/TDSQL 协议测试库和显式注入的测试口令，仓库不保存口令。
