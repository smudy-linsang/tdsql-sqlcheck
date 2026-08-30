# v1.6.2.2 UAT 第八轮整改证据（Q 侧，O-30 审计落库收口）

| 文件 | 说明 |
|---|---|
| `o30_e2e_verification_r2.txt` | O-30 端到端验收（R2）：历史 checksum 置回 → 无开关启动自动调和 + 审计新增 1 条 + ERROR 日志 → 二次启动幂等无新增 → `operation_logs` 查询结果展示 → 篡改失败关闭。三项证据（checksum、ERROR 日志、审计查询）同文呈现。 |

- 自动化用例：`tests/test_o23_migration_fail_closed.py`（32 用例，含单/双进程审计条数恰好 1 条、二次启动无新增、审计故障回滚且启动失败关闭）；`tests/test_o19_offline_instance_inspect.py`（34 用例，第八轮确认保持）。
- 门禁执行记录：`docs/evidence/v1.6.2.2/o21_gate_run.txt`（`RESULT PASS`，退出码 0，门禁内全量 1569 passed）。
- 修复说明：`docs/FIX-v1.6.2.2-UAT第八轮修复说明-Q.md`。
