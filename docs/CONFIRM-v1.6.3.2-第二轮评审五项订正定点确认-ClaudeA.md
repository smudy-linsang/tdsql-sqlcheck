# CONFIRM-v1.6.3.2 第二轮评审五项订正定点确认

| 项 | 内容 |
|---|---|
| 确认对象 | `docs/DESIGN-v1.6.3.2-…详细设计说明书.md` **Rev.C**（提交 `2f53de8`，1128 行；相对 Rev.B 净改 +68/−38） |
| 依据 | `REVIEW2-…第二轮评审报告-ClaudeA.md`（`5885c1f`，结论：通过（有条件），5 项定点订正） |
| 确认方 | 智能体 A |
| 确认范围 | **只看第二轮报告 §4.2 列的 5 项准入条件**，不重开完整评审（第二轮已承诺） |
| 确认日期 | 2026-09-03 |
| **确认结论** | **5 项全部关闭，无新增问题。Rev.C 可作为施工图纸交付实施。** |

---

## 1. 五项逐条确认

| # | 准入条件 | Rev.C 落点 | 结论 |
|---|---|---|---|
| 1 | N-01 `Limit.offset` → `lim.args.get("offset")`，补属性不存在说明与 SELECT/UPDATE 形态差异；§10.1 补"不含执行异常"断言 | §5.3 两处取值均改为 `lim.args.get("offset")`；新增整段说明写明 `hasattr(exp.Limit,"offset") is False`、`lim.offset` 抛 `AttributeError`、会被 `checker.py` 兜底转成"规则 R058 执行异常"WARNING 导致判定失效，并区分 `Limit.args["offset"]`(Literal) 与 `Select.args["offset"]`(Offset)；§10.1 R058 第 6 条补"结果中不得含'执行异常'或 `AttributeError` 字样"；§13 完成定义同步 | ✅ **关闭** |
| 2 | N-02 选定方案 i 或 ii 并写明；补"词法化次数不得超过既有基线"约束与回归 | 选**方案 i**。§4.7.3 改为：`_preflight_create_definition_status()` 仍只 `tokenize()` 一次、返回三元组，在 `open_idx < 0` 的 CREATE 专用提前返回**之前**按首个有效 token 分流 CREATE/ALTER，其余语句返回空策略事实；§5.4 新增 `_scan_secondary_partition_policy_tokens(toks)`（**接收既有 tokens，禁止再次 tokenize**），并新增"性能不变量"段落要求 tokenizer spy/monkeypatch 回归；§10.1 R121 第 12 条、§9.1、§13 同步 | ✅ **关闭** |
| 3 | N-03 §9.3 增 `tests_3p/test_1_smoke.py`；§9.4 清点范围显式含 `tests_3p/` | §9.3 新增该行并注明"不在默认 `testpaths` 内"、用例名/docstring/断言一并改 121；§9.4 补"清点范围必须显式包含 `tests_3p/`；不能只靠运行默认 pytest 等待失败来发现"；§13 完成定义同步 | ✅ **关闭** |
| 4 | N-04 §4.7.5 补"ALTER ADD 正常上界"行；§10.1 R121 负例口径同步 | §4.7.5 新增该行，分布式/集中式均为"含 E999、不含 R121"，并注明"sqlglot 对 `ADD PARTITION (PARTITION name …)` 整体不支持，与 MAXVALUE 无关"；§10.1 R121 新增第 7 条 | ✅ **关闭** |
| 5 | N-05 §9.4 补版本戳文档归类规则 | §9.4 新增独立段落：`DEPLOY-v1.6.3.0-…部署手册.md` 的 `[PASS] 规则总数 119` 属 v1.6.3.0 实测输出样例，按 OUT-08 保留；新建 v1.6.3.2 手册用 121；不得篡改旧手册版本证据，也不得复制旧样例到新手册 | ✅ **关闭** |

另：§0.2 新增"A 第二轮评审处置结论"表，5 项全部标注接受、无不接受项；§15 修订记录补 Rev.C 条目。

## 2. 订正内容的独立复核

不只看"改没改"，还核对了新写内容在代码上是否成立：

| 复核点 | 实测 | 结论 |
|---|---|---|
| §5.3 新增段落的三条事实 | `exp.Limit.arg_types` 含 `offset` 但 `hasattr(exp.Limit,"offset")=False`；`lim.offset` 抛 `AttributeError`；`checker.py:193-200` 确实把规则异常兜成 WARNING | ✅ 三条全对 |
| §5.4 方案 i 的可行性 | `_preflight_create_definition_status` 全仓库仅 3 个调用方：`_preflight_known_fidelity_failures`（取索引 0，O 已说明保持兼容）、`parse():2263`（O 已说明改收三元组）、`docs/evidence/…/test_parser_recovery_manifest.py:73`（断言 `parse()` 中该函数**只出现一次**——方案 i 恰好保持该断言成立） | ✅ 无遗漏调用方；方案 i 与既有 manifest 断言相容 |
| §4.7.5 新增行 | `ALTER TABLE t ADD PARTITION (PARTITION p1 VALUES LESS THAN (202702))` → `Expecting ). Line 1, Col: 41` | ✅ 与 MAXVALUE 无关，断言属实 |
| §9.3 新增行 | `tests_3p/test_1_smoke.py:80/81/85` 三处（用例名 `test_sm09_rule_library_119`、docstring、`assert body["total"] == 119`）均需改 | ✅ 清单精确 |
| 旧写法是否清干净 | 全文已无 `Limit.offset`／`lim.offset` 的非 `args.get` 写法；已无"无条件调用"与 `_scan_secondary_partition_policy(sql)` 的旧签名 | ✅ 无残留 |
| 文档结构 | 1128 行，代码围栏 20（偶数），表格列数无异常 | ✅ |
| 行号锚点仍有效 | `git diff 03ac422 HEAD -- backend/ frontend/ tests/` 为**空**，应用代码自设计基线起零变更 | ✅ §3 全部行号仍可直接使用 |

## 3. 结论

**5 项定点订正全部关闭，复核未发现新增问题。Rev.C 可作为施工图纸交付实施。**

移交实施阶段的提醒（非评审意见）：

1. §12 的三项**生产发布**书面门禁与开发无关但与发布强相关，建议开工即发起，不要留到 UAT：
   目标分布式实例满足 UPDATE/DELETE LIMIT 版本前提；DBA 接受 R030/R032 在集中式造成的零覆盖；
   活动规则集与流水线负责人接受 §10.2 的门禁双向变化。
2. REQ-01A（ALTER 列类型通道）与 REQ-05A（R035 批内跨表上下文）是本版**已承诺范围**。
   设计已写明"实现者不能自行降级"，若确需降级，是需求方书面批准 + 同步改验收口径。
3. 施工期 `main` 若前进，§5.3 的 sqlglot 字段形态需按当时锁定版本复测一次（当前 30.14.0）。
4. §9.4 的固定数字清点务必覆盖非默认目录 `tests_3p/`——它不随默认 pytest 运行，
   只靠"跑一遍看哪里红"发现不了。
