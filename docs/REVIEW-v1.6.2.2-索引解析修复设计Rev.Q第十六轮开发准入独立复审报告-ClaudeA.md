# v1.6.2.2 索引解析修复设计 Rev.Q 第十六轮开发准入独立复审报告（智能体 A）

## 1. 评审结论

**结论：通过开发准入（Go）。**

第十五轮的 1 项 BLOCK、1 项 MINOR、3 项观察，Rev.Q **全部闭环**，且闭环质量高于我给出的两个原型：

- **BLOCK-15-01 完全关闭**：65 例伴随结构 × KFN 笛卡尔积 **未被阻断 0 例**，
  4 例净回归全部复原，**而且语料 + 生产 14 表漂移仍为 0 条**。
  我第十五轮的原型 A 虽然也能关闭缺口，但会误伤 5 条语料；Rev.Q 的**逐定义项扫描**
  把"保真阻断"与"通道可信度"彻底解耦，两个目标同时达成 —— 这比我提出的方向更好；
- 全量 **1355 passed / 0 failed**，四项冻结门槛 **45 / 14 / 12 / 14** 全部保持；
- `--mode design --matrix` 在 29.0.0 / 30.14.0 / 30.17.0 上各 **680 passed**，`RESULT PASS`，
  bundle 哈希 `6412e076…` 连跑两次一致；`--mode implementation` 施工前仍正确返回 `3`。

我按"尽量把问题一次暴露完"的要求，本轮在既有 manifest 之外**自建 6 组共 260 余条独立探针**
（假阳性对抗、顶层切分对抗、KFN 定位对抗、支持域 × 伴随结构双向矩阵、核心不变量穷举、
复杂度与模糊测试），并复跑了第 12~15 轮我方全部历史探针 —— **未发现新的阻断项**。

**建议进入开发。** §6 列出施工期需要盯住的 3 个点，均非方案缺陷。

## 2. 评审对象

| 项 | 内容 |
|---|---|
| Codex 的 Rev.Q 提交 | `8fa8ef7` |
| 我的第十五轮报告 | `b37a630` |
| 重建基线 commit | `03216b78` |
| design bundle 哈希 | `6412e076871dcae15df8889c746819fc312729d7a69e9c4513334fdb274dfe89`（两次重建一致） |
| 产品代码状态 | 未施工；本轮**不改产品代码、不改设计文档** |

## 3. 第十五轮问题的闭环复核

| 第十五轮项 | 复核结果 |
|---|---|
| **BLOCK-15-01** `unique_constraints_complete` fail-open | **完全关闭**，见 §4 |
| **MINOR-15-01** 文档头 1384 passed | **已改**：改为以 runner 实测为准，并明写"**禁止把 skip 计入 passed**" |
| **观察 1** preflight 重复词法化 | **已采纳**：合并为 `_preflight_create_definition_status()` 一次词法化同时产出 `(逐项 KFN, 完整性)`；并在测试里加了**源码级断言**（`parse()` 内只调用一次、且不得再调旧入口）——把"别退化"写成了可执行门禁，这一手很好 |
| **观察 2** KFN-4 仅被 ParseError 偶然保护 | **已采纳**：R15 矩阵直接断言 `_preflight_known_fidelity_failures()` 的精确 KFN 编号，再断最终 E999，不再拿 AST ParseError 当 preflight 有效的证据 |
| **观察 3** strict scanner 白名单缺口 | **已登记 ADJ-14**，口径正确：伴生结构只映射为 `complete=False`，**不自动判非法、不凭空制造 E999** |

## 4. BLOCK-15-01 的闭环机制与验证

### 4.1 机制

Rev.Q 把我指出的"三处叠加"逐一拆开：

```python
def _preflight_create_definition_status(sql, dialect="mysql"):
    ranges = _top_level_definition_ranges(toks, open_idx, close_idx)   # 只切分，不解释
    known = []
    for start, stop in ranges:
        known.extend(_definition_item_kfns(toks, start, stop))          # 逐项取证
    defs, _p, _a = _scan_definition_list(toks, open_idx, close_idx)     # 严格扫描另算
    return tuple(sorted(set(known))), defs is not None
```

关键是那句代码注释所写的：**"任何一个未知伴生项都不得清空其他项已经证明的 KFN"**。
"我看不懂第 j 项"不再抹掉"第 k 项确实是 KFN-6"这一事实，也不再被当成"整句没问题"。
同时 `complete` 独立由严格扫描给出，`exp.Constraint` 分支补齐，
三个默认值不再一起倒向宽松侧。

### 4.2 验证

| 检查 | 第十五轮 Rev.P | 本轮 Rev.Q |
|---|---|---|
| 13 伴随结构 × 5 KFN 形态 = 65 例 | **33 例漏（51%）** | **0 例漏** ✅ |
| 4 例净回归（基线命中、Rev.P 不命中且无 E999） | 3 例回归 | **4 例全部复原** ✅ |
| 语料 + 生产 14 表漂移 | 0 条 | **0 条** ✅ |
| 全量测试 | 1355 / 0 failed | **1355 / 0 failed** ✅ |

三版 sqlglot 结论完全一致。

### 4.3 我自建的对抗性探针（manifest 之外）

| 探针组 | 例数 | 结果 |
|---|---:|---|
| **假阳性对抗**：`CONSTRAINT … FOREIGN KEY` / `CONSTRAINT … CHECK` / 带 symbol 的 `CONSTRAINT … PRIMARY KEY`、列名为 `` `serial` `` / `` `constraint` `` / `` `unique` ``、列注释 / 表注释 / `DEFAULT` 值 / 行注释 / 块注释内含 `CONSTRAINT … UNIQUE` 与 `SERIAL DEFAULT VALUE` | 11 | **0 误判** ✅ |
| **顶层切分对抗**：`CHECK (a IN (1,2,3))` 嵌套逗号、`ENUM('a,b')` 串内逗号、`DEFAULT '('` 串内括号、`DEFAULT ','`、函数索引双层括号、注释内不配对括号、`CHECK ((a,b) IN ((1,2),(3,4)))` | 7 | **0 切错** ✅ |
| **KFN 定位对抗**：把真 KFN 放在嵌套逗号之后 / 串内逗号之后 / 函数索引之后 / 首项 / 末项、无 symbol 的 `CONSTRAINT UNIQUE`、`SERIAL DEFAULT VALUE` | 7 | **0 漏** ✅ |
| **支持域 × 伴随结构双向矩阵**：5 种支持域 UNIQUE（含"合规不该命中"的反向例）× 9 种伴随结构 | 45 | **0 例期望不符** ✅ |
| **核心不变量穷举**：7 种表级 UNIQUE 组合 × 3 种列级 × 7 种伴随结构，断言"`complete=True` ⇒ `len(unique_constraints)` 精确等于构造已知的真实唯一索引数" | 147 | **0 例假完整** ✅ |
| **复杂度**：50→800 列，preflight 耗时随定义项数**线性**（每翻倍 ×1.36 / ×2.16 / ×1.83 / ×1.99） | 5 | 无 O(n²) 退化 ✅ |
| **模糊测试**：4000 条随机 token 串同时喂 preflight 与 `parse()` | 4000 | **0 抛异常** ✅ |

### 4.4 历轮探针回归

第 12~15 轮我方全部历史探针在 Rev.Q 上复跑：

- 第十四轮三条路径（原生 `Create` / 方言 `Command` / 恢复）—— 均有 E999，不再有"无 E999 空结构" ✅
- 第十四轮 CreateShape 顶层/表尾 13 变异 —— **放行 0 例** ✅
- 第十三轮可执行注释 atom 内部 8 反例 + 2 正例 —— **失败 0** ✅
- 第十五轮官方 14 种 UNIQUE 写法 —— **静默漏审 0** ✅
- `unique_constraints` 内容（多列 / 前缀基列 / 反引号 / 裸名 / 无索引名 / 两个 UNIQUE）—— 全部正确 ✅

> 说明：探针里 `UNIQUE KEY uk (c DESC)` 一行显示 `complete=False`，
> **不是缺陷**。该语句 sqlglot 自身即 ParseError 且**无主目标**（无索引 COMMENT、无方言尾子句），
> 因此不进恢复链。与主干基线**逐条相同**（都是 E999 + 相同规则集合），已核对排除。

## 5. 证据面复核

| 项 | 结果 |
|---|---|
| `--mode design` | 30.14.0：**680 passed**，`RESULT PASS` |
| `--mode design --matrix` | 29.0.0 / 30.14.0 / 30.17.0 各 **680 passed**，`RESULT PASS` |
| `--mode implementation` | `STATUS NOT_IMPLEMENTED`，退出码 **3**（施工前正确拒绝） |
| 生成区段一致性 | `CHECK manifest-section=OK`、`CHECK codestat-section=OK` |
| bundle 哈希 | 连跑两次均 `6412e076…`，**重建确定** |
| manifest 规模 | 用例 **670**、变异套 9；`--collect-only` 与 runner 实跑一致 |
| 新增维度覆盖 | **R15-KFN 65 例**（与我的笛卡尔积同构）+ **R15-CH 91 例**，由维度**生成**而非手写 |
| KFN 断言方式 | 直接断言 `_preflight_known_fidelity_failures()` 输出，不依赖 ParseError ✅ |
| 冻结门槛 | frozen-71 **71 passed**；全量 **1355 passed / 29 skipped / 0 failed** |
| `verify_rules.py` | 119 / 107 / 未覆盖 0 / 断言失败 **3**（与基线同名同因） |
| 生产 fixture | 6309 / 6311 规则集合正确 |

## 6. 施工期需要盯住的三点（非方案缺陷，不阻断准入）

1. **`_scan_definition_list()` 的白名单缺口（ADJ-14）已登记但仍然存在。**
   `FOREIGN KEY` / `CHECK` / `GENERATED` / `VISIBLE` / `KEY_BLOCK_SIZE` / 函数索引 /
   列级 `REFERENCES` / `SRID` 出现时 `complete=False`，R054 走 raw 回退。
   当前实测**双向都正确**（§4.3 的 45 例矩阵），但这条回退路径的正确性依赖
   `_UNIQUE_IDX_RE` 只认 `UNIQUE (KEY|INDEX)` 这一事实。
   **后续任何人改动那条正则，必须同时复跑 §4.3 的 45 例矩阵。**
2. **preflight 现在是每条 `CREATE TABLE` 的固定成本。**
   实测 800 列大表约 44 ms、线性增长。当前审核规模无问题；
   若将来做全库批量扫描，建议先测一次真实库的 P99 建表语句长度。
3. **`implementation` 模式施工后才会变绿。** 施工提交必须同时满足：
   `--mode implementation --matrix` 全绿、bundle 哈希等于 `6412e076…`、
   四项冻结门槛不变、语料漂移 0 条。这四条建议直接进 CI，不要靠人工核对。

## 7. 本轮评审的边界（如实说明）

- 本轮验证的是**设计的可施工性与目标语义**，不是产品代码 —— 产品尚未施工，
  `--mode implementation` 按契约仍是 `NOT_IMPLEMENTED`；
- 我构建的目标树来自仓库提交的 `rebuild_from_design.py`，与 Codex 的 design runner 同源，
  因此"重建正确"这一环是**同源验证**，不是完全独立的第二实现；
  但重建产物的**行为**是我用自建探针独立测的；
- 未覆盖：真实内网实例上的端到端回放、并发/大批量场景、
  以及 ADJ-13（RANGE/LIST 不认作分片键声明）等既有登记项 —— 后者本轮实测与基线逐条相同。

## 8. 本轮测试记录

```text
# 仓库 runner
run_all.py --mode design                → RESULT PASS，680 passed
run_all.py --mode design --matrix       → 29.0.0/30.14.0/30.17.0 各 680 passed，RESULT PASS
run_all.py --mode implementation        → STATUS NOT_IMPLEMENTED，退出码 3
rebuild_from_design.py ×2               → bundle 6412e076… 两次一致

# Rev.Q 目标树
pytest tests/ -q                        → 1355 passed, 29 skipped, 0 failed
test_r077_r054_tdsql_syntax.py          → 45 passed
test_parser_tdsql_dialect_fallback.py   → 14 passed
test_r061_index_name_quoting.py         → 12 passed
test_parser.py                          → 14 passed
verify_rules.py                         → 119/107/0/断言失败 3（同名同因）
语料 201 条 + 生产 14 表                   → 漂移 0 条
生产 fixture 6309 / 6311                 → 规则集合正确

# 本轮自建探针（三版一致）
伴随结构 13 × KFN 形态 5 = 65 例           → 未被阻断 0 例
第十五轮 4 例净回归                         → 全部复原
假阳性对抗 11 + 切分对抗 7 + 定位对抗 7       → 失败 0
支持域 UNIQUE 5 × 伴随结构 9 = 45 例        → 期望不符 0
核心不变量穷举 147 例                       → 假完整 0
复杂度 50→800 列                          → 线性
模糊测试 4000 条                           → 抛异常 0

# 历轮探针回归（第 12~15 轮）
三路径 / CreateShape 13 变异 / 可执行注释 10 例 / 14 种 UNIQUE 写法 / 通道内容  → 全绿
```

## 9. 最终意见

从第十一轮到本轮，这个方案经历了 6 轮 No-Go。Rev.Q 是第一版我找不出阻断项的版本。

值得记一笔的是本轮的修法：我第十五轮给出的两个原型各有代价（一个留 33 例缺口、
一个误伤 5 条语料），Codex 没有二选一，而是找到了**逐定义项取证 + 完整性独立判定**
这条把两个目标解耦的路，两边同时拿满。这说明问题已经收敛到工程细节层面，
不再是方案层面的反复。

**同意进入开发阶段。** 施工时以 §6.3 的四条作为提交门槛，
其余以设计说明书 §7.3 的既有门槛表为准。
