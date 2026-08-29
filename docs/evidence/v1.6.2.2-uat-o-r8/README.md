# v1.6.2.2 第八轮专项 UAT 证据

本轮严格按用户限定，只复测第七轮发现的 O-28、O-29、O-30 是否关闭，不开展全项目回归或新缺陷探索。

| 证据 | 内容 |
|---|---|
| `targeted_o28_o29_o30.txt/xml` | Q 新增及相关两组测试文件：64 passed |
| `targeted_closeout_probe.py/json/txt` | 三项原始反例的独立关闭探针；O-28/O-29 PASS，O-30 因审计未落库 FAIL |
| `summary.json` | 本轮范围、结论与关闭状态 |

被测提交：`d40cf739420be984a2805253ba671c890fe17c66`。

O-30 探针在隔离元数据库 `tdsql_uat_o_r8_1622_20260830` 中模拟历史 checksum，结束后恢复原 checksum 并清理临时表。仓库未保存口令或令牌。
