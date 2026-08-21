# v1.6.1.9 R054/R077 TDSQL 语法误报修复设计 Rev.E 独立复审报告

| 项 | 内容 |
|---|---|
| 复审对象 | `DESIGN-v1.6.1.9-TDSQL分片表与广播表建表语法识别缺陷修复详细设计说明书.md` Rev.E |
| 复审基线 | `main @ 49f3e2e` |
| 复审日期 | 2026-08-22 |
| 复审人 | Codex（独立复审） |
| 现场证据 | `Extracted_Schema_Report_6261.html` 的 14 张表，重点为 #3、#5 |
| 复审结论 | **有条件否决（BLOCK）——Rev.E 暂不可直接进入正式施工** |

---

## 1. 结论先行

Rev.E 相比 Rev.C 已有实质性进步。上轮关于 HASH 语法证据边界、`KEY`/单引号越权放宽、注释伪造 HASH、R054 未承接 J-2/J-3、唯一索引逐个判断、ADJ-5 原子变更约束、用户可见文案和测试基线等问题，绝大多数已经正确整改。

将 Rev.E §5 的代码片段逐字装入 `main @ 49f3e2e` 的临时 detached 工作副本后，现场 14 表回放结果符合主目标：

- #3 `cus_bas_corp_contact`：R077 消失；
- #5/#8/#11/#13 广播表：R077、R054 消失；
- #4 真正未声明分片键的单表：R077 保留；
- 其余 8 表规则集合不变；
- 指定 4 个规则测试文件为 **168 passed**，加 `test_distributed.py` 后为 **182 passed**；
- 排除独立复审临时反例文件后，全量为 **1313 passed、0 failed**。

但是，新增边界反例证明 Rev.E 仍存在两项会造成核心规则漏报的阻断问题，以及一项可直接造成合法 HASH 表误报的词法问题：

1. **广播哨兵仍从未经清洗的 raw SQL 提取。** 仅在字符串或注释中写入 `shardkey=noshardkey_allset`，R077 与 R054 就会同时提前放行；这是 Rev.E 新引入的静默绕过。
2. **R054 仍把“没有主键”当作无需检查。** HASH 键只在裸名 UNIQUE 中、表完全没有主键时，R077 的既有 `或` 分支和 R054 会同时放行，违反 J-2。
3. `_strip_sql_noise()` 把所有 `--` 都当注释，与 MySQL 5.7/8.0 的词法规则不一致；合法表达式中的 `a--b` 会截断后续真实 HASH 子句并误报 R077。

这三项均有小范围、可执行的修正方式，不需要重写解析器，不需要触碰 ADJ-6，也不需要扩大到 R077/R054 之外。复审建议 A 完成 §6 的三项强制整改后再提交 Rev.F 复审。

---

## 2. 复审范围与方法

本轮不是文档措辞检查，而是按“设计可施工性”做了四层验证：

1. 对比 Rev.C、Rev.D、Rev.E 的修订差异，逐项核销上轮阻断意见；
2. 对照当前 `distributed.py`，核验 §5 七处补丁的真实插入位置、控制流和影响域；
3. 在 detached 临时工作副本中逐字实现 §5 代码，执行现场 14 表、指定回归和全量回归；
4. 补充原矩阵未覆盖的广播哨兵伪造、无主键 J-2、MySQL `--` 词法反例，并做最小修补可行性验证。

临时验证代码没有写入产品分支，本报告只提交复审结论与整改要求。

---

## 3. 上轮意见销项结果

| 上轮关注点 | Rev.E 结果 | 复审判定 |
|---|---|---|
| §1 判据与语法证据分级 | 区分公开官方规范与目标环境实测；删除无据的 `BY KEY`；补录选中集群版本，并声明生产版本仍需另录 | **通过**。现场 DDL 本身足以支撑本次语法兼容；版本适用范围表述已足够克制 |
| HASH 参数类型 | 仅接受裸标识符和反引号标识符，不接受单/双引号字符串 | **通过** |
| HASH 注释/字符串伪造 | 新增 `_strip_sql_noise()` 与 `_ddl_options_tail()` | **HASH 路径通过**；广播哨兵路径仍未接入清洗，形成 BLOCK-1 |
| R077/R054 对 HASH 的共同消费 | 两条规则均调用 `_extract_tdsql_hash_key()` | **通过**，职责分配合理 |
| J-2/J-3 完整性 | R054 接管主键与逐个 UNIQUE 判断 | **部分通过**；有主键场景正确，无主键场景仍违反 J-2，形成 BLOCK-2 |
| 广播哨兵精确值 | 改为大小写不敏感的 `noshardkey_allset` 精确等值 | **值比较本身通过**；值的来源不可信，不能据此提前放行 |
| R054 UNIQUE 逐个判断及反引号索引名 | `_iter_unique_indexes()` 逐个返回索引 | **通过**；X6/X8 实测能由 R054 正确承接 |
| ADJ-5 §8.1 承重性论证 | 从“永久保留坏正则”改为“提取与判定原子修改”，并增加代码注释与 X13 | **通过**；这是正确的工程约束，不再依赖缺陷互相抵消作为长期方案 |
| R077 用户可见说明 | 补充 HASH 和 `noshardkey_allset`，并写明主键与每个 UNIQUE 的要求 | **通过** |
| 附录 B 与回归基线 | 要求测试真实落库；基线修订为同环境自比、总收集 1313 | **通过**，但必须再补本报告 §6 的用例 |
| ADJ-6 | 用户决策关闭，不进 Phase 2；仅保留两种现状的特征化测试 | **接受用户决策，不构成本轮阻断项** |

---

## 4. 阻断问题

### BLOCK-1（P0）：广播哨兵的 raw SQL 来源未清洗，可被字符串和注释伪造

#### 4.1 可复现证据

以下语句没有任何真实分片或广播声明：

```sql
CREATE TABLE fake_sentinel (
  id BIGINT PRIMARY KEY
) COMMENT='shardkey=noshardkey_allset';
```

Rev.E 候选补丁的实际结果：

| 规则 | 应有结果 | 实际结果 |
|---|---|---|
| R077 | 触发：没有真实分片/广播声明 | **不触发** |
| R054 | 不应把注释内容当作分片键 | 不触发 |

将伪语法分别放进 `COMMENT='...'`、`/* ... */`、`-- ...`、`# ...`，四种变体均可使 R077 静默放行。

#### 4.2 根因

Rev.E 只对新增 HASH 助手做了清洗：

```python
tail = _ddl_options_tail(_strip_sql_noise(raw_sql))
```

但广播哨兵仍由两个既有 raw SQL 正则从原文任意位置提取：

- R077 `_extract_shard_key()`：`_SHARDKEY_RE.search(raw_sql)`；
- R054 `check()`：`re.search(..., parsed.raw_sql, ...)`。

两条规则随后新增：

```python
if _is_broadcast_sentinel(shard_key):
    return None
```

因此“精确等值”只保证了**比较值**精确，没有保证这个值来自真实表选项。

这不是可以沿用的既有行为。改前即使 raw 正则从注释里提取到该串，也会因为它不在主键中而触发 R077/R054；Rev.E 新增提前返回后才变成零违规，所以它是本补丁新引入的漏报。

#### 4.3 强制整改

新增一个共享的 legacy `SHARDKEY` 提取助手，至少满足：

1. raw SQL 回退必须先经过 `_strip_sql_noise()`；
2. 最好再经过 `_ddl_options_tail()`，限定为列清单之后的表选项；
3. R077 与 R054 必须共同消费这个助手，不能各自保留一份 raw 正则；
4. 哨兵提前放行只能作用于这个可信来源或结构化 `table_options`/`table_metadata` 来源；
5. 捕获值后增加完整 token 边界，避免把非法后缀截断为合法哨兵。

建议结构：

```python
def _extract_legacy_shard_key(raw_sql: str) -> str:
    tail = _ddl_options_tail(_strip_sql_noise(raw_sql))
    # 在 tail 中匹配 SHARDKEY / SHARD_KEY，并要求值为完整 token
    ...
```

若为了控制改动量暂不抽新助手，最低限度也必须把 R077/R054 的 raw 正则输入改为 `_strip_sql_noise(raw_sql)`；但两份正则继续分叉并不是推荐的最终形态。

#### 4.4 必增用例

| 用例 | 场景 | 期望 |
|---|---|---|
| X14 | 表 `COMMENT` 字符串只含 `shardkey=noshardkey_allset` | R077 |
| X15 | `/* */` 只含该哨兵 | R077 |
| X16 | `-- ` 只含该哨兵 | R077 |
| X17 | `#` 只含该哨兵 | R077 |
| X18 | 真实 `shardkey=noshardkey_allset`，注释中另有干扰文本 | 零违规 |

---

### BLOCK-2（P1）：HASH 表没有主键、仅 UNIQUE 含分片键时，J-2 仍无人执行

#### 4.5 可复现证据

```sql
CREATE TABLE missing_pk (
  id BIGINT,
  sk BIGINT,
  UNIQUE KEY uk_sk (sk)
) TDSQL_DISTRIBUTED BY HASH(sk);
```

按 Rev.E 自己确立的 J-2，`sk` 必须是主键或主键的一部分；本表根本没有主键，显然不合规。

Rev.E 候选补丁实际结果却是：R077 不触发、R054 不触发。

#### 4.6 控制流原因

1. R077 保留用户已经决定不收紧的 legacy `主键 或 UNIQUE` 判定；裸名 `UNIQUE KEY uk_sk(sk)` 可被其旧正则识别，所以 R077 放行；
2. R054 的主键判断仍为：

```python
if pk_cols and shard_key.lower() not in pk_cols:
    return violation
```

`pk_cols` 为空时整个 J-2 检查被跳过；
3. 后续 J-3 看到唯一索引包含 `sk`，也会放行。

这不是 ADJ-4 的翻案。用户关闭的是 **R077 口径收紧**；Rev.E 明确把完整 J-2/J-3 交给 R054，本问题要求修正的是 R054 对“无主键”的漏判。

#### 4.7 强制整改

R054 的 J-2 判断应改为：

```python
if shard_key.lower() not in pk_cols:
    return self._make_violation(...)
```

消息可区分两种情况：

- `pk_cols` 为空：`表未声明主键，分片键 'sk' 必须是主键的一部分`；
- 有主键但不含 `sk`：保留现有消息。

同时更新 §7.2、§10 和施工检查单：修正后“R054 对无主键分片表补报”也是净收紧，Rev.E 当前“FIX-3b 是唯一净收紧”的表述不再成立。需要分别统计 HASH 与 legacy `SHARDKEY=` 语料漂移，不能只报总数。

#### 4.8 必增用例

| 用例 | 场景 | 期望 |
|---|---|---|
| X19 | HASH，无主键，裸名 UNIQUE 含分片键 | R054 |
| X20 | HASH，无主键，反引号 UNIQUE 含分片键 | 至少 R054；R077 现状可另行特征化 |
| X21 | `SHARDKEY=sk`，无主键，UNIQUE 含 `sk` | R054 |
| X22 | HASH，有主键且含键，无 UNIQUE | 零违规 |

---

### BLOCK-3（P2）：`--` 清洗规则与 MySQL 5.7/8.0 不一致，会重新制造合法 HASH 误报

MySQL 5.7 与 8.0 都规定：双短横线只有在第二个 `-` 后紧跟空白或控制字符时才开始注释。Rev.E 的实现却是：

```python
elif sql.startswith('--', i) or c == '#':
```

因此合法表达式中的 `a--b` 会被错误地当成行注释。例如：

```sql
CREATE TABLE mysql_double_minus (
  a BIGINT,
  b BIGINT,
  PRIMARY KEY(a),
  CHECK(a--b > 0)
) TDSQL_DISTRIBUTED BY HASH(a);
```

候选补丁把 `--b` 之后全部删除，真实 HASH 子句随之消失，R077 误报。

官方依据：

- [MySQL 5.7 Reference Manual — Comments](https://dev.mysql.com/doc/refman/5.7/en/comments.html)
- [MySQL 8.0 Reference Manual — Comments](https://dev.mysql.com/doc/refman/8.0/en/comments.html)

Rev.E 用“失败方向可见”接受该问题不充分。当前缺陷本身就是用户投诉的合法 DDL 误报，而该词法差异只需一个局部条件即可准确实现，没有必要有意保留。

#### 强制整改

```python
is_dash_comment = (
    sql.startswith('--', i)
    and (i + 2 >= n or sql[i + 2].isspace())
)
```

只有 `is_dash_comment` 为真时才跳到行末；`#` 的处理保持不变。新增 `a--b` 与真正 `-- comment` 的正反用例。

---

## 5. ADJ-6 复审结论

本轮明确服从用户决策：**ADJ-6 关闭，不进 Phase 2，不修改 `BROADCAST` 快速通道。**

Rev.E 对该决定的落法可以接受：

- 文档没有再声称冲突语法一定正确；
- 明确列出 `sk` 在/不在主键时的现状差异；
- 只用 X10 特征化测试锁定现状；
- 本报告要求的三项整改均不触碰 `BROADCAST` 快速通道，也不会变相重开 ADJ-6。

建议 X10 的测试名和注释使用 `characterization_current_contract` 一类措辞，并注明“源于用户决策”，避免未来维护者误解为 TDSQL 官方合规语法。

---

## 6. 可实施的最小整改包

建议 Rev.F 将以下内容作为一个原子设计修订：

1. 把 legacy `SHARDKEY/SHARD_KEY` raw 回退收敛为共享、清洗后、表选项尾部限定的助手；
2. 哨兵放行只接受结构化来源或上述可信 raw 来源；
3. R054 将空主键集合也判为 J-2 失败；
4. `_strip_sql_noise()` 按 MySQL 规则识别 `-- `；
5. 在现有 29 条 + X9/X10/X11/X13 基础上补 X14–X22；
6. 更新“唯一净收紧”影响说明，并重跑 201 条语料、生产 14 表、182 项指定回归和 1313 项全量回归。

独立复审已在临时副本验证前三项代码层修正的可行性：

| 验证集 | Rev.E 原方案 | 加入最小整改后 |
|---|---:|---:|
| 独立边界矩阵（含现场、J-2/J-3、注释伪造、`--`） | **20 passed / 6 failed** | **26 passed / 0 failed** |
| 指定 4 文件 + `test_distributed.py` | 182 passed | **182 passed** |
| 生产报告 14 表 | 目标 5 表变化正确 | **仍仅目标 5 表变化** |

这说明当前 BLOCK 不是要求全盘重写，而是三个局部条件尚未闭合。

---

## 7. 现场 14 表独立回放结果

| # | 表 | `main @ 49f3e2e` | Rev.E 候选补丁 | 判定 |
|---:|---|---|---|---|
| 1 | big_audit_trail | R029,R036,R037,R061 | 同左 | 不变 |
| 2 | big_order_log | R029,R061 | 同左 | 不变 |
| 3 | cus_bas_corp_contact | **R077** | **无违规** | 目标误报消除 |
| 4 | cus_bas_corp_contact_addr_20260511 | R001,R036,R037,R061,R077 | 同左 | 真 R077 保留 |
| 5 | cus_name_list_type | R036,R037,**R054**,R061,**R077** | R036,R037,R061 | 目标误报消除 |
| 6 | t_account | R036,R061,R063 | 同左 | 不变 |
| 7 | t_audit_log | R036,R037,R061,R062 | 同左 | 不变 |
| 8 | t_branch | R036,**R054**,R061,**R077** | R036,R061 | 同类误报消除 |
| 9 | t_customer | R061,R063 | 同左 | 不变 |
| 10 | t_deposit | R036,R061 | 同左 | 不变 |
| 11 | t_dict | R036,**R054**,R061,R063,**R077** | R036,R061,R063 | 同类误报消除 |
| 12 | t_loan | R036,R061,R063 | 同左 | 不变 |
| 13 | t_product | R036,**R054**,R061,R063,**R077** | R036,R061,R063 | 同类误报消除 |
| 14 | t_transaction | R036,R037,R061,R063 | 同左 | 不变 |

现场主目标已经被 Rev.E 正确覆盖，这是本轮认可方案主体方向的主要依据。

---

## 8. 测试记录

### 8.1 Rev.E 逐字候选补丁

| 命令/范围 | 结果 |
|---|---|
| `py_compile distributed.py` | 通过 |
| `test_rules.py + test_sit_rules.py + test_uat_rules.py + test_oracle_compat_rules.py` | **168 passed** |
| 上述 4 文件 + `test_distributed.py` | **182 passed** |
| `pytest tests/`（排除独立复审临时反例文件） | **1313 passed，0 failed** |
| 现场 HTML 14 DDL 基线/候选逐表对比 | 仅 #3/#5/#8/#11/#13 变化 |
| 独立 26 项边界矩阵 | **20 passed，6 failed** |

六个失败由三类根因组成：四种广播哨兵注释/字符串伪造、一个无主键 J-2 漏判、一个 `--` 词法误判。

### 8.2 最小整改可行性原型

| 范围 | 结果 |
|---|---|
| 独立 26 项边界矩阵 | **26 passed** |
| 指定 5 个规则文件 | **182 passed** |
| 现场 14 DDL | 与 Rev.E 目标结果一致 |

该原型只用于证明整改可达，没有作为产品实现提交。A 施工时仍须按 Rev.F 最终设计自行实现并执行全部门禁。

---

## 9. 版本基线与影响说明

Rev.E 已补录所选“开发测试环境（分离版 V22）”的 TDSQL/DB-TXSQL/Proxy 版本，并明确生产环境版本可能不同。该处理可接受，原因是：

1. 本次生产审核报告直接包含真实 `SHOW CREATE TABLE` 输出，是支持两种语法的首要事实证据；
2. 设计同时支持 `shardkey=...` 与 `TDSQL_DISTRIBUTED BY HASH(...)`，没有按所选开发集群版本做排他分支；
3. 文档没有再泛化成“所有 TDSQL 版本输出一致”。

非阻断建议：上线说明如能取得生产集群版本，应补录生产版本；若暂时不能取得，必须继续保留 Rev.E 的适用范围声明，不得把开发测试截图写成生产版本证据。

---

## 10. 最终准入条件

Rev.E 当前结论为 **BLOCK**。满足以下全部条件后，可转为“通过并允许施工”：

- [ ] BLOCK-1：哨兵来源经过清洗和表选项限定，X14–X18 全部落库通过；
- [ ] BLOCK-2：R054 对空主键集合正确触发，X19–X22 全部落库通过；
- [ ] BLOCK-3：`--` 按 MySQL 5.7/8.0 规则处理，正反例均通过；
- [ ] 更新影响分析，不再声称 FIX-3b 是唯一净收紧；
- [ ] 原 29 条及 X9/X10/X11/X13 全部保留；
- [ ] 现场 14 表仍只改变 #3/#5/#8/#11/#13；
- [ ] 指定 182 项、全量 1313 收集项在同环境下 0 failed；
- [ ] 201 条语料扫描异常为 0，新增变化逐条解释；
- [ ] ADJ-6 继续按用户决策关闭，补丁不得触碰 `BROADCAST` 快速通道。

完成上述整改后，方案主体不需要推倒重来；共享语法助手、R054/R077 职责拆分、ADJ-5 原子护栏及用户文案均可继续沿用。
