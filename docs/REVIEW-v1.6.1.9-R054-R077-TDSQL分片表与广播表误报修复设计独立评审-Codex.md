# REVIEW-v1.6.1.9 R054/R077 TDSQL 分片表与广播表误报修复设计独立评审

| 项 | 内容 |
|---|---|
| 评审对象 | `DESIGN-v1.6.1.9-TDSQL分片表与广播表建表语法识别缺陷修复详细设计说明书.md` Rev.C |
| 评审基线 | `origin/main @ 3c5167d` |
| 生产证据 | `Extracted_Schema_Report_6261.html`、用户提供的两张 TDSQL 管理平台截图 |
| 评审日期 | 2026-08-21 |
| 评审人 | 智能体 O（Codex） |
| 结论 | **有条件否决（BLOCK）——根因方向正确，但 Rev.C 不可按现稿直接开发** |

---

## 1. 执行结论

A 对两项生产误报的事实判断、当前代码根因和目标结果是正确的：

1. `cus_bas_corp_contact` 的 `TDSQL_DISTRIBUTED BY HASH(cust_no)` 未被 R077 识别；`cust_no` 已包含在联合主键 `(ID, CUST_NO)` 中，现有 R077 属误报。
2. `cus_name_list_type` 的 `shardkey=noshardkey_allset` 是广播表标记，不是列名；现有 R077/R054 把它拿去与主键比较，均属误报。
3. A 的原型确实能消除生产报告中的 5 处误报，同时保持 #4 的 R077 不变；B.4 所描述的 `_UNIQUE_RE` 与 R077 宽松 `OR` 判定之间的耦合现象也能复现。

但是，Rev.C 的拟议实现同时会把多类原本触发 R077 的非合规语句变成“零违规”。其中至少两类直接违反设计自己定义的 J-2/J-3：

- `TDSQL_DISTRIBUTED BY HASH(sk)`，`sk` 在主键中，但某个唯一索引不含 `sk`，改后 R077/R054 均不报；
- `TDSQL_DISTRIBUTED BY HASH(sk)`，`sk` 不在主键、只在裸名称唯一索引中，改后 R077/R054 均不报。

此外，原型还会被注释文本、单引号伪标识符和任意 `noshardkey_*` 列名绕过。故 A 所称“行为差集恰好等于误报集合”“不可能压制真实违规”不成立。

**最终裁定：**

- 可以保留“识别 HASH 尾子句”和“识别广播表哨兵”两个修复方向；
- 不批准 Rev.C 当前正则、哨兵前缀匹配、仅让 R077 识别 HASH、以及“永久保留坏正则”的设计；
- A 完成第 8 节列出的 7 项强制调整并补齐反例测试后，方可重新送审和进入开发。

---

## 2. 生产事实复核

### 2.1 #3 `cus_bas_corp_contact`

报告原始 DDL 的关键部分为：

```sql
PRIMARY KEY (`ID`,`CUST_NO`),
KEY `cus_bas_corp_contact_IDX1` (`CUST_NO`,`DATA_VALID_TM`),
KEY `cus_bas_corp_contact_IDX2` (`CONTACT_NO`,`DATA_VALID_TM`)
) ENGINE=InnoDB ...
TDSQL_DISTRIBUTED BY HASH(`cust_no`)
```

报告命中 R077，违规文案是“未声明 SHARDKEY 或 BROADCAST”。平台截图则显示该表为 `hash分片表`。因此问题是规则未识别内核实际 DDL 形态，不是用户未声明分片键。

`cust_no` 大小写归一后确实属于主键列集 `{id, cust_no}`；两个 `KEY` 是普通索引，不影响本次合规结论。A 在 Rev.B/Rev.C 中纠正了 Rev.A 把普通索引当依据的错误，这一纠正是正确的。

### 2.2 #5 `cus_name_list_type`

报告原始 DDL 尾部为：

```sql
) ENGINE=InnoDB ... shardkey=noshardkey_allset
```

报告命中：

- R054：把 `noshardkey_allset` 当作分片列，认为其不在主键中；
- R077：把同一哨兵当作分片列，认为其不在主键或唯一索引中。

平台截图明确显示该表类型为“全局表”。腾讯 TDSQL 文档也把 `shardkey=noshardkey_allset` 作为广播表建表语法。因此该值应被解析成“广播表类型”，而不是“名为 noshardkey_allset 的列”。

### 2.3 独立原型复现

评审使用 A 在 §5 中给出的拟议逻辑，以子类方式运行，不修改产品源码。14 张生产 DDL 中，R077/R054 判定差异恰为：

| 报告项 | 改前 `(R077,R054)` | A 原型 `(R077,R054)` | 目标是否达成 |
|---|---:|---:|---|
| #3 | `(True, False)` | `(False, False)` | 是 |
| #5 | `(True, True)` | `(False, False)` | 是 |
| #8 | `(True, True)` | `(False, False)` | 是 |
| #11 | `(True, True)` | `(False, False)` | 是 |
| #13 | `(True, True)` | `(False, False)` | 是 |
| #4 | `(True, False)` | `(True, False)` | 是，反向锚点保持 |

这证明 A 找对了生产问题，但只能证明“正例能过”，不能证明“反例不会被错误放行”。

---

## 3. §1 合规判据评审

### 3.1 J-2/J-3：准确

腾讯云当前《TDSQL MySQL 版—建表》明确要求分片键属于主键，并属于所有唯一索引；因此 A 的 J-2、J-3 和 NJ-1/NJ-2/NJ-3 语义是准确的：

- 普通 `KEY` 含分片键不能替代主键约束；
- 只在某一个唯一索引中出现不能替代主键约束；
- 每一个唯一索引都必须包含分片键。

参考：

- [腾讯云：TDSQL MySQL 版建表](https://cloud.tencent.com/document/product/557/8767)
- [腾讯金融云：TDSQL CREATE TABLE](https://doc.fincloud.tencent.cn/tcloud/Database/TDSQL/388315/97188/26779/98315/70661)

### 3.2 广播表判据：准确，但必须精确匹配

`shardkey=noshardkey_allset` 是有官方材料支持的广播表语法：

- [腾讯云 TDSQL 开发手册 PDF](https://mc.qcloudimg.com/static/qc_doc/80a1d876a78d7d419af05cc6804e2abd/doc-DCDB%2Bfor%2BTDSQL-Development%2BManual.pdf)
- [Tencent Cloud：Creating Table](https://intl.cloud.tencent.com/jp/document/product/1042/38506)

证据只支持**精确值** `noshardkey_allset`。Rev.C 把它扩大为 `^noshardkey(?:_[a-z0-9_]+)?$`，会额外放行 `noshardkey`、`noshardkey_shadow`、`noshardkey_anything`，没有规范依据。

### 3.3 `TDSQL_DISTRIBUTED BY HASH(col)`：生产事实成立，但版本口径需补证

本次生产 DDL、平台表类型以及用户确认足以作为本项目的需求判据，故必须支持该形态。

但公开文档的口径存在版本差异：当前基础建表文档仍主要把一级 HASH 写作 `shardkey=col`，把 `TDSQL_DISTRIBUTED BY RANGE/LIST` 用于其他一级分区；DTS 新二级分区材料才明确出现 `TDSQL_DISTRIBUTED BY HASH`。参考：[腾讯云 DTS 使用说明](https://cloud.tencent.com/document/product/571/105000)。

因此设计应把 J-1 的证据写成：

1. `shardkey=col`：公开官方规范；
2. `TDSQL_DISTRIBUTED BY HASH(col)`：本项目生产内核 `SHOW CREATE TABLE` 的已验证输出，并补录内核/Proxy 版本；
3. 后续若取得该内网版本的官方手册，再把链接或文档版本补入设计。

不能笼统声称所有 TDSQL 版本均采用相同 HASH 输出。

### 3.4 `BY KEY(col)`：无依据，应从 Phase 1 删除

用户需求、生产样本和 A 的 J-1 均只涉及 `HASH`。Rev.C 正则却额外接受 `KEY`，并设计 C5 为正例。独立检索未找到该形态在目标 TDSQL 产品中的权威依据。

**裁定：** Phase 1 只接受 `HASH`。若 A 要保留 `KEY`，必须提供目标内核版本的官方语法证据和真实实例执行/`SHOW CREATE TABLE` 证据，另加正反测试；否则属于无授权的行为扩张。

---

## 4. 阻断性发现

### P0-1：FIX-1 会把 J-2/J-3 违规 HASH 表变成零违规

Rev.C 只让 R077 识别 `TDSQL_DISTRIBUTED`，明确禁止 R054 识别该语法。问题在于：

- R077 只做“分片键在主键 **或任意唯一索引列并集**中”的宽松判断；
- R077 不检查“每一个唯一索引”；
- R054 才负责 J-2/J-3，但它仍取不到 HASH 分片键。

反例 1（违反 J-3）：

```sql
CREATE TABLE t_j3 (
  id BIGINT NOT NULL,
  sk BIGINT NOT NULL,
  PRIMARY KEY (id, sk),
  UNIQUE KEY `uk_id` (`id`)
) ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(sk);
```

`sk` 在主键中，但不在唯一索引 `uk_id` 中。按 J-3 必须违规。实测：

| | R077 | R054 |
|---|---:|---:|
| 改前 | 触发 | 不触发 |
| A 原型 | **不触发** | **不触发** |

反例 2（违反 J-2）：

```sql
CREATE TABLE t_j2 (
  id BIGINT NOT NULL,
  sk BIGINT NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_sk (sk)
) ENGINE=InnoDB TDSQL_DISTRIBUTED BY HASH(sk);
```

`sk` 不在主键中，仅在唯一索引中。实测改后 R077/R054 均不触发。

这不是“既有漏报原样保留”：在 FIX-1 之前这两条会触发 R077；FIX-1 之后被主动压成零违规，属于本次新引入的回归。

**必须修改：** HASH 分支必须由同一分布类型解析结果同时驱动 R077 和 R054；至少保证 J-2/J-3 仍有规则命中。禁止以 NG-3 为由让 R054 永久看不见 HASH 分片键。

### P0-2：原始 SQL 全文搜索可被注释/字符串伪造绕过

Rev.C 在原始 SQL 上执行 `_TDSQL_DISTRIBUTED_RE.search(raw_sql)`，没有移除注释和字符串字面量。

反例：

```sql
CREATE TABLE t_comment (
  id BIGINT NOT NULL,
  PRIMARY KEY (id)
) ENGINE=InnoDB COMMENT='TDSQL_DISTRIBUTED BY HASH(id)';
```

它没有任何分片声明，本应触发 R077。A 原型却从表注释中提取 `id`，再因 `id` 在主键中而返回零违规。

§10 把该风险称为“非本次引入”不成立：旧 `_BROADCAST_RE` 有类似缺陷，不代表新增 HASH 识别可以继续复制缺陷；而且本反例的绕过路径正是 FIX-1 新增的。

**必须修改：** 在只用于语法判定的扫描文本上移除 `--`、`#`、`/*...*/` 注释和字符串字面量，保留反引号标识符；HASH 尾子句正则应锚定清洗后的 DDL 尾部，而不是全文搜索。

### P0-3：`noshardkey*` 前缀放行会吞掉合法列名上的真实违规

反例：

```sql
CREATE TABLE t_noshard (
  id BIGINT NOT NULL,
  noshardkey_shadow BIGINT NOT NULL,
  PRIMARY KEY (id)
) ENGINE=InnoDB shardkey=noshardkey_shadow;
```

这里 `noshardkey_shadow` 是一个普通列名，且不在主键中，现有 R077/R054 都会触发。Rev.C 却把它当广播表，两个规则均直接返回。

设计中“这种真实列实际不可能出现”的说法没有证据。TDSQL 文档说明 `shardkey` 是关键字，但没有把所有 `noshardkey_*` 标识符宣布为保留值。

**必须修改：** 使用大小写不敏感的精确等值：

```python
shard_key.strip('`"\' ').casefold() == "noshardkey_allset"
```

未来若有新哨兵值，只能通过有出处的显式白名单扩展，不能以前缀猜测。

### P1-1：正则接受单引号参数和未证实的 KEY 方法

Rev.C 的 `[\`"']?` 会把下面的字符串字面量当列标识符：

```sql
TDSQL_DISTRIBUTED BY HASH('id')
```

若主键含 `id`，A 原型返回零违规。单引号在 MySQL/TDSQL 语义中是字符串，不应作为标识符引用。

同一正则还接受未证实的 `BY KEY(id)`。两者都扩大了放行域。

**必须修改：** Phase 1 只接受裸标识符和反引号标识符，只接受 `HASH`。双引号仅在已确认目标实例启用相应 SQL mode 后才能加入。

### P1-2：§7.1 的“最小可达域”证明只证明了代码位置，未证明语义安全

把 FIX-1 放在 `_extract_shard_key()` 最末尾，确实保证前 3 个来源非空时不进入新分支；A 对**控制流可达性**的描述成立。

但“前 3 源全空时改前必报”不能推出“改后只消除误报”。前 3 源全空集合还包含：

- 注释/字符串中出现伪 HASH 子句；
- HASH 子句真实存在但违反 J-2/J-3；
- 单引号参数等非合规形态；
- 未证实的 KEY 形态。

故“行为差集恰好等于当前误报集合”“任何其他语句逐字不变”是逻辑跳跃。201 条既有语料没有包含这些反例，只能说明测试集未覆盖，不能证明集合相等。

### P1-3：§8.1 的实验成立，但“永久保留坏正则”的结论不成立

独立执行 B.4 的完整版本得到：

| 场景 | R077 结果 |
|---|---|
| 保持 ADJ-5 不修 | 触发（符合 J-2） |
| 只修 `_UNIQUE_RE`、不改宽松 OR 判定 | 不触发（漏报） |

因此 A 证明了一个真实的**变更耦合**：不能单独修唯一索引提取而不同时修 R077 判定。

但这不等于应该永久依赖坏正则：

1. 当前正确性依赖两个缺陷相互抵消，是偶然行为，不是稳定设计；
2. `_collect_unique_index_cols()` 还有 `parsed.indexes` 来源，未来 sqlglot 或解析器升级可能在不改 `_UNIQUE_RE` 的情况下激活 OR 分支；
3. 裸索引名和反引号索引名产生不同审核结果，已经是确定的行为不一致；
4. “永久禁止修复”把正确性绑定在实现 bug 上，阻断未来正常重构。

**裁定：** 接受“不得单独修改”的原子变更约束，不接受“永久保留缺陷”。正确表述应是：

> 修改任一唯一索引提取来源时，必须同时把 R077 判定对齐 J-2/J-3，并通过裸名称、反引号名称两组同语义测试；不得拆分提交。

### P1-4：附录 B 不是完整的可执行验收物

- B.2 只定义原型类，没有提供 20 条 SQL、期望数据和断言循环，无法直接复现“20/20”。
- B.3 依赖 B.1/B.2 运行遗留的全局变量和 `report_items.json`，并用 `split(";")` 切 SQL；异常被无计数地 `continue`，可能把解析失败伪装成“无漂移”。
- B.4 只定义类和 `SQL` 字符串，没有解析、调用、打印或断言；按原文运行不会产生设计声称的结果。
- B.5 的全量基线已漂移：当前 `origin/main @ 3c5167d` 实测为 **1313 passed、0 failed、0 skipped**，不是 1312 passed / 1 skipped。

**必须修改：** 把用例落为 pytest，禁止以文档片段代替门禁；漂移脚本必须报告总输入数、成功数、失败数、跳过数，并在任一异常时失败，而不是静默跳过。

### P2-1：修复后规则说明和用户建议仍会误导

Rev.C 禁止修改 R077 的 `description` 和 `fix_suggestion`。这样即便代码接受 HASH/哨兵，规则目录仍只告诉用户可以使用 `SHARDKEY=列名` 或 `BROADCAST`，没有列出本次刚支持的真实语法。

**建议修改：** 保持 `rule_id`、severity、enabled、instance_scope 不变，但同步修订 description/fix suggestion，列出目标环境确认支持的两种分片语法和广播表精确语法。该文案变更应加入快照/接口回归。

### P2-2：§3.2 关于 `BROADCAST + shardkey=` 的“本次一并收口”与补丁不一致

拟议 R054 只识别 `noshardkey_allset`，并没有增加 BROADCAST 快速通道。因此“只要同时出现 shardkey 字样，本次一并收口”的表述不准确。

这类相互冲突的双重声明也不应不加区分地放行。设计应删除该承诺，并增加“冲突声明必须违规或至少不得被广播快速通道掩盖”的测试。

---

## 5. 对 A 特别请求的四项答复

| A 请求评审项 | 独立结论 | 裁定 |
|---|---|---|
| §1 判据 | J-2/J-3、精确广播哨兵正确；生产 HASH 形态应支持，但需补内核版本证据；`KEY` 无依据 | **部分通过，需修订** |
| §8.1 承重性 | 对照现象可复现；只能证明“必须原子修改”，不能证明“永久保留 bug” | **论据通过，结论否决** |
| §5 四处补丁最小可达域 | 插入位置的控制流确实小；语义放行域不小，并新增 J-2/J-3 漏报 | **否决** |
| 改动点 5 注释护栏 | 应采纳，但要改成“不得单独修改”的原子约束，使用准确文档路径并配套可执行测试 | **修改后强制采纳** |

---

## 6. 建议的可实施修订方案

### 6.1 用一个共享的“表分布声明解析结果”驱动两条规则

不要让 R077 和 R054 各自猜一次字符串。建议在 `distributed.py` 内新增私有、无副作用的共享助手，返回明确类型：

```text
DistributionSpec
├── kind = NONE | SHARDED | BROADCAST
├── shard_key = str | None
└── syntax = SHARDKEY | TDSQL_HASH | NOSHARDKEY_ALLSET | BROADCAST
```

优先级建议：

1. 结构化 `table_options`；
2. 清洗后的 `shardkey=...`；
3. 清洗且尾部锚定的 `TDSQL_DISTRIBUTED BY HASH(...)`；
4. 无声明。

若同一语句存在相互冲突的声明，不得按任一快速通道直接放行，应返回冲突状态或保守触发 R077。

### 6.2 使用精确语法，不做猜测式兼容

建议约束：

- 广播哨兵仅等于 `noshardkey_allset`；
- HASH 方法仅等于 `HASH`；
- 列名仅允许裸标识符或反引号标识符；
- HASH 子句必须位于去除注释/字符串后的 DDL 尾部；
- 不接受单引号列名，不接受未证实的 `KEY`；
- 大小写归一用 `casefold()` 或既有 lower 口径。

### 6.3 R077/R054 必须共同消费 HASH 结果

对 `kind=SHARDED`：

- R077 负责“存在合法分片声明”及项目已确认的 ERROR 门禁；
- R054 使用同一 `shard_key` 检查主键和**每一个**唯一索引；
- 对 HASH 新路径必须保证 J-2/J-3 违规至少仍被规则捕获，不能从改前 R077 变成零违规。

如果用户坚持暂不全局调整 R077 的既有 OR 口径，可以只对新 HASH 路径采用严格检查；但必须在设计中明确这是兼容性隔离，而不能继续声称 R077 已完整执行 J-2/J-3。

### 6.4 唯一索引不能展平成一个列并集来表达 J-3

J-3 是“每一个唯一索引都包含分片键”，数据结构应是 `list[set[str]]`，逐索引判断：

```python
for unique_columns in unique_indexes:
    if shard_key not in unique_columns:
        return violation
```

把全部唯一索引列合并成一个集合，只能回答“是否在任意唯一索引中”，无法表达 J-3。

### 6.5 注释护栏改为原子变更护栏

改动点 5 建议变为必选，并改写为：

```python
# 不得单独放宽唯一索引提取：R077 当前仍含 legacy OR 判定。
# 修改本正则或 parsed.indexes 产出时，必须原子对齐 J-2/J-3，
# 并通过 tests/test_r077_r054_tdsql_syntax.py 的 quoted/bare 用例。
```

不要在代码中写“永久保留坏正则”；不要使用 `docs/DESIGN-v1.6.1.9-...说明书.md` 这种不可解析的省略路径。

### 6.6 用户可见文案同步修订

至少修订 R077 的描述和修复建议，使其与实际接受语法一致。否则规则配置页、报告详情和后续排障仍会向用户提供不完整建议。

---

## 7. 强制补充测试矩阵

保留 A 的 P1-P5、#4 反向锚点和既有 20 条矩阵，并至少增加：

| 编号 | 场景 | 必须结果 |
|---|---|---|
| X1 | 表注释含 `TDSQL_DISTRIBUTED BY HASH(id)`，无真实声明 | R077 触发 |
| X2 | 行/块注释含 HASH 语法，真实 DDL 无声明 | R077 触发 |
| X3 | `HASH('id')` | 不得作为有效分片声明放行 |
| X4 | `BY KEY(id)`（无权威依据阶段） | 不得作为有效分片声明放行 |
| X5 | `shardkey=noshardkey_shadow` 且该列不在主键 | R077、R054 仍触发 |
| X6 | HASH 键在主键，但某个反引号 UNIQUE 不含键 | 至少 R054 触发，不得零违规 |
| X7 | HASH 键不在主键、只在裸名称 UNIQUE 中 | 不得零违规 |
| X8 | 两个 UNIQUE，仅一个包含 HASH 键 | R054 触发，验证“每一个” |
| X9 | metadata 通道返回精确 `noshardkey_allset` | R054/R077 均不误报 |
| X10 | `BROADCAST` 与真实 `shardkey=col` 冲突 | 不得被快速通道静默放行 |
| X11 | 裸索引名与反引号索引名的同语义 DDL | 结果必须一致 |
| X12 | #3 原始 DDL、大小写/空白/换行变体 | 无 R077，且结果一致 |

测试应落库为普通 pytest，并加入默认 `pytest tests/` 门禁。A 的 B.4 对照实验应改为断言测试，防止未来依赖升级悄悄激活宽松 OR 分支。

---

## 8. 必须完成的设计整改清单

A 重新提交前必须全部完成：

1. 将广播哨兵从前缀正则改为精确 `noshardkey_allset`。
2. FIX-1 只支持有证据的 `HASH`，删除 `KEY` 和单引号列名。
3. HASH 匹配改为注释/字符串清洗后的尾部语法匹配。
4. R054 与 R077 共享 HASH/广播解析结果，补齐 HASH 场景 J-2/J-3 反例。
5. 把 §8.1 从“永久保留坏正则”改为“唯一索引提取与判定必须原子修改”；改动点 5 改写后列为必选。
6. 把 B.2/B.3/B.4 落成可直接运行、遇异常失败的 pytest/QA 脚本，并更新当前全量基线为 1313 passed、0 skipped。
7. 同步修订 R077 用户可见描述/建议，并明确 Phase 2 邻接缺陷不会被本热修复解决。

以上任一项未完成，本评审结论保持 BLOCK。

---

## 9. 影响面与现有功能/体验判断

### 9.1 按 Rev.C 原稿开发

**不可接受。** 虽然能解决用户眼前两项误报，但会新增核心审核漏报，且存在可通过注释和列名构造的绕过。该影响比原误报更隐蔽，因为报告会显示“零违规”。

### 9.2 按本评审建议修订后

可以把主要改动控制在 `backend/engine/rules/distributed.py`、对应测试和规则文案，原则上不涉及数据库 schema、API 协议或前端交互结构。预期体验变化为：

- #3 不再误报 R077；
- #5/#8/#11/#13 不再误报 R054/R077；
- #4 继续触发 R077；
- 非合规 HASH 表、伪造声明和非精确哨兵仍能被拦截；
- 规则说明能准确告诉用户可接受语法。

### 9.3 明确保留的邻接问题

本次只修 R054/R077，并不能自动解决 A 已识别的 ADJ-1/2/3：

- `TDSQL_DISTRIBUTED` 使解析器降级，#3 仍可能漏掉 R036/R037/R061；
- `tdsql_connector.py` 仍可能把广播哨兵写入 `meta.shard_key`，影响其他元数据规则和“大表治理”展示；
- 重复 `_detect_shard_info()` 中的未定义变量仍可能导致广播识别静默失效。

这些不是驳回本热修复的理由，但必须在发布说明中明确，另开 Phase 2；尤其 ADJ-3 建议按高优先级处理。

---

## 10. 独立验证记录

| 验证项 | 结果 |
|---|---|
| 生产 HTML #3/#5 原始 DDL 与违规文本核验 | 通过 |
| 两张 TDSQL 平台截图表类型核验 | #3 为 Hash 分片表；#5 为全局表 |
| 14 表改前/原型 R077-R054 差异 | 仅 #3/#5/#8/#11/#13 变化，复现 A 结果 |
| B.4 完整对照实验 | 现象复现：只修 UNIQUE 提取会激活宽松 OR 漏报 |
| 补充反例 | 6/6 均证明 Rev.C 会扩大零违规集合 |
| 指定 4 个规则测试文件 | 168 passed，0 failed |
| 上述 4 文件 + `test_distributed.py` | 182 passed，0 failed |
| 全量 `pytest tests/ -q` | **1313 passed，0 failed，0 skipped**，耗时 255.66 秒 |

当前基线本身健康；本评审的阻断来自设计原型的语义反例，不是现有测试失败或环境问题。

---

## 11. 最终意见

这份 Rev.C 已经把生产问题的“为什么误报”说清楚了，且 #3/#5 的目标输出没有争议；但它把“控制流插入点很靠后”误当成“语义影响面天然最小”，又把两个缺陷互相抵消提升成了永久架构约束。

**结论：方向可用、实现不可直接采用。A 应按第 8 节整改后重新送审。** 在重审通过前，不建议把 Rev.C 交给开发施工，更不建议直接发布到 v1.6.1.9。
