# v1.6.2.2 解析恢复链 —— 证据面资产

本目录是设计说明书
`docs/DESIGN-v1.6.2.2-索引类型误判与唯一索引注释解析崩溃修复详细设计说明书.md`
的**可执行证据**。第十二轮复审 BLOCK-12-05 要求"唯一真源必须在仓库里、命令必须真实可执行"，
本目录就是对该要求的落地。

## ⚠️ 这些是准出用例，不是现网回归用例

`test_parser_recovery_manifest.py` 断言的是 v1.6.2.2 修复**之后**的行为。
主干 `backend/engine/parser/parser_legacy.py` 仍是 v1.6.2.1，直接对它跑必然大面积失败——
**这正是它作为开发准入门槛的意义**。因此它放在 `docs/evidence/` 而不是 `tests/`，
不会被 `pytest tests/` 收集、不影响现有 CI。
施工方把设计补丁落到产品代码后，可把本目录整体迁进 `tests/` 转为常驻回归。

## 一条命令跑通全部

```bash
python docs/evidence/v1.6.2.2/run_all.py
```

它在**临时目录**里完成四件事，全程不触碰工作区、不需要 `git stash`：

1. 拷贝仓库到临时目录，按设计说明书的前 12 个 ```python 代码块重建 `parser_legacy.py`；
2. 校验重建结果的 SHA256 与设计说明书登记的目标哈希一致；
3. 在重建树上跑 manifest 全量（用例 + 变异断言 + 模糊测试）；
4. 跑两个生成器，把输出与设计说明书正文**逐字比对**（§7.1 用例表、§3.4 规模表）。

任一步失败即以非零码退出。加 `--keep` 保留临时目录便于排查。

## 文件职责

| 文件 | 职责 |
|---|---|
| `parser_recovery_manifest.py` | **唯一 case manifest**：纯数据，无判定逻辑。每条含稳定 `cid`、SQL、`klass`（判据）、`prov`（证据来源）、`note`（理由）与判据参数 |
| `test_parser_recovery_manifest.py` | 参数化 pytest：判据全部来自 manifest，**不含任何用例数据** |
| `manifest_doc.py` | 从 manifest 生成设计说明书 §7.1 的全部表格与计数 |
| `codestat.py` | 从最终补丁生成 §3.4 的规模表、函数清单与唯一性检查 |
| `rebuild_from_design.py` | 「照图施工」：把设计说明书的代码块重建成 `parser_legacy.py`，**只写指定输出路径** |
| `run_all.py` | 上述四件事的一键编排 |

## 单独运行

```bash
# 只生成 §7.1 表格
python docs/evidence/v1.6.2.2/manifest_doc.py

# 只生成 §3.4 规模表（需要一个已打补丁的目标文件）
python docs/evidence/v1.6.2.2/codestat.py <基线 parser_legacy.py> <目标 parser_legacy.py>

# 只重建（写入你指定的副本，不改工作区）
cp backend/engine/parser/parser_legacy.py /tmp/pl.py
python docs/evidence/v1.6.2.2/rebuild_from_design.py /tmp/pl.py
```

## 计数口径（第十二轮 MINOR-12-01）

三个数字含义不同，不要混用：

| 口径 | 含义 |
|---|---|
| **用例数** | `len(CASES)`，manifest 里逐条 SQL 用例 |
| **变异断言数** | 每套变异 = 1 个正确候选 + N 个变异候选，逐条 `assert` 的总数 |
| **pytest collect 数** | 用例数 + 变异**套数** + 1（模糊测试整体是 1 个 item） |

准确数值以 `python docs/evidence/v1.6.2.2/manifest_doc.py`
与 `pytest --collect-only -q` 的实际输出为准，任何章节都不得人工维护。
