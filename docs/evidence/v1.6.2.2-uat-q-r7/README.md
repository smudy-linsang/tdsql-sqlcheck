# v1.6.2.2 UAT 第七轮整改证据（Q 侧）

| 文件 | 说明 |
|---|---|
| `o30_e2e_verification.txt` | O-30 端到端验收（模拟 v1.6.2.1 老库）：历史 checksum 置回 → 无人工开关启动自动一次性调和 → 二次启动幂等 → 调和后篡改必然失败关闭（MigrationError），随后恢复当前基线。 |

- 自动化用例：`tests/test_o19_offline_instance_inspect.py`（O-28 类型×短语×errno 矩阵，34 用例）、`tests/test_o23_migration_fail_closed.py`（O-26/O-29/O-30 迁移失败关闭、默认值规范化矩阵、精确三元组调和闭环，30 用例）。
- 升级手册：`docs/UPGRADE-v1.6.2.2-升级手册.md`（检测/备份/执行/验证/回滚全套命令）。
- 预检：`deploy/preflight_check.sh` 第 8 节（遗留开关 FAIL + v9_090 漂移四态提示）。
- 门禁执行记录：`docs/evidence/v1.6.2.2/o21_gate_run.txt`（`RESULT PASS`，退出码 0，门禁内全量 1567 passed）。
