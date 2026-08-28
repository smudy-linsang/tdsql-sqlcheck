# v1.6.2.2 第二轮全项目用户验收测试报告

测试人：智能体O；日期：2026-08-28；被测提交：`d12fe8c16ab9861b85eea41e05e498a9ba305c7d`（main）。

**结论：不通过，仍不准出。Q 的核心修复有效但不完整；不能按“首轮问题均已修复”关闭验收。** 本轮只新增测试材料和报告，没有修改产品代码。

## 1. 给项目负责人的结论

1. **两个最初故障未退化。** gg77 假 UNIQUE 引起的目标 R054 仍已消除；gg78 唯一索引 COMMENT 仍可恢复解析，集中式结果保留 R036/R037 INFO，不再出现原有 E999、R003/R004/R005/R028 连带错误。HASH、广播表的仓库回归样例也未重新出现目标 R077/R054。
2. **首轮 BLOCK 只完成部分修复。** 原 75 个 KFN 鉴别样例中，首轮缺失 E999 的 72 个现在修好了 70 个；`kfn_literal:19/20` 仍缺 E999。新增的 252 个 KFN 路径组合中又有 60 个缺失，三个 sqlglot 版本均复现。它们属于同一根因，不登记成 60 个不同缺陷。
3. **默认页面红色不等于强制阻断修好了。** 剩余样例当前被 R003/R004/R005/R028/R042 等其他错误挡住，页面不是绿色；但这些主要是解析结构丢失和字面量误识别造成的错误。引擎显式关闭 119 条可配置业务规则后，这 60 个已知保真失败样例全部 `passed=true`。测试没有修改系统生效规则集；这是证明强制门禁不独立的隔离诊断。
4. **首轮其他六项仍未关闭。** 登录版本不一致、网关报告空正文、CSS 污染、演示数据混入正式报告、PDF 丢网关记录、慢 SQL 标记误开详情均有本轮证据。Q 的修复说明把其中几项顺延改号，且漏列原 O-03；本报告保持首轮 ID 不变。
5. **新登记一项历史串页缺陷 O-08。** 查看慢 SQL 详情后，从独立菜单打开 EXPLAIN，仍偷偷使用旧详情中的数据库名；同一 SQL/连接刷新前失败、刷新后成功。相关代码本轮未变，不归因为 Q 新引入的回归。

本轮状态为 **1 BLOCK、6 MAJOR、1 MINOR，共 8 项未关闭**：前七项延续首轮，O-08 为本轮新发现的历史问题。严重度沿用用户影响口径，不代表都由本次修复引入。是否分版本处理历史问题，需要项目负责人明确接受范围、风险及验收延期；Q 自行写“本轮不修”不能等同于已获验收豁免。即使六项历史问题获延期，O-01 仍单独阻止本次核心热修准出。

## 2. 基线、环境与证据边界

| 项目 | 本轮实际情况 |
|---|---|
| 同步及比较 | main 从 `6957499` 安全快进至 `d12fe8c`；用 `git archive 6957499` 做旧代码对照，没有创建分支 |
| Q 的产品改动 | 仅 `backend/engine/checker.py`，24 行新增、2 行删除；另有 [Q 修复说明](FIX-v1.6.2.2-UAT第一轮修复说明-Q.md)，没有新增产品回归测试 |
| 首轮依据 | [第一轮 UAT 报告](UAT-v1.6.2.2-第一轮全项目用户验收测试报告-智能体O.md)及其原始证据，未覆盖/重写首轮结果 |
| 设计合同 | [Rev.Q 详细设计](DESIGN-v1.6.2.2-索引类型误判与唯一索引注释解析崩溃修复详细设计说明书.md)：已证明的 KFN 必须在各解析路径最终产生 E999；不重开已经批准的 TDSQL 语义取舍 |
| 主运行时 | Windows、Python 3.14.6、sqlglot 30.14.0；另测 29.0.0/30.17.0 |
| 浏览器 | Codex 内置浏览器，实际导航、输入、选择文件、按钮点击、截图；不是用 HTTP 请求代替页面操作 |
| 本地服务 | `http://127.0.0.1:8002/`，`python -m uvicorn backend.main:app --host 127.0.0.1 --port 8002`；鉴权开启、调度关闭 |
| UAT 元数据库 | `tdsql_uat_o_r2_1622_20260828`；与首轮和原 8000 服务隔离 |
| 回归数据库 | `tdsql_uat_o_reg_r2_full_20260828`、`tdsql_uat_o_reg_r2_matrix_20260828`；服务差分另用 `tdsql_uat_o_reg_r2_pipeline_20260828` |
| 实际目标库 | `127.0.0.1:13306`，MySQL **8.0.45**，`tdsql_uat_o_target_1622` 两张小型合成表；不是 TDSQL 实例 |
| 角色 | admin、dba、developer、auditor；developer 关闭实例管理菜单；初始改口令标记在隔离 fixture 处理 |
| 浏览器交付证据 | **64 组 JPEG 截图 + DOM**，覆盖当前 19 个可见功能入口及深度诊断 9 个页签；入口覆盖不等于每个功能全部通过 |

所有被审计的 TDSQL DDL 只进入审核引擎，未拿去本地 MySQL 执行。在线提取、EXPLAIN、索引分析等操作的是本地合成数据。Digest、上线检查会读取同一本地 MySQL 的其他测试库元数据，不能将其计数理解成生产问题。

证据入口：[README](evidence/v1.6.2.2-uat-o-r2/README.md)、[浏览器操作记录](evidence/v1.6.2.2-uat-o-r2/browser_steps.json)、[离线证据核验](evidence/v1.6.2.2-uat-o-r2/validation.json)、[SHA256 清单](evidence/v1.6.2.2-uat-o-r2/evidence_manifest.json)。后两者证明材料一致性，不是新一轮产品测试。

## 3. 回归与 119 条核心规则结果

### 3.1 自动化与接口结果

| 执行 | 实际结果 | 含义/证据 |
|---|---|---|
| 独立全量 pytest | **1384 passed，10 warnings，234.09s** | [日志](evidence/v1.6.2.2-uat-o-r2/full_regression.txt)、[JUnit](evidence/v1.6.2.2-uat-o-r2/full_regression.xml) |
| 正式 implementation runner | 29.0.0 / 30.14.0 / 30.17.0 **各 680 passed** | [矩阵日志](evidence/v1.6.2.2-uat-o-r2/implementation_matrix.txt) |
| 冻结回归及正式全量 | **71 passed；1384 passed，294.93s** | 与上项同日志；两次全量不是 2768 个不同用例 |
| 文档/实现包校验 | manifest、codestat、bundle 全部一致 | bundle `6412e076871dcae15df8889c746819fc312729d7a69e9c4513334fdb274dfe89`；并不证明新 checker 边界完整 |
| 首轮同一份独立输入 | **1000 条，5 个独立判据失败** | 2 个 E999 遗漏 + 3 个既有精确期望差异；不是 1000 全通过 |
| 新增边界输入 | **324 条 × 三版本** | 每版 252 个 KFN 中 60 个无 E999；另外 72 个为反向/普通语法/标记字面量控制 |
| 第一批补充 HTTP | **614 条：611×200、3×403，无 5xx** | [HTTP 记录](evidence/v1.6.2.2-uat-o-r2/http_results.json)；200 不代表审核通过，403 为预期权限拒绝 |
| 引擎/服务差异复核 | 17 个差异，旧新服务输出完全相同 | [当前](evidence/v1.6.2.2-uat-o-r2/service_current.json)、[基线](evidence/v1.6.2.2-uat-o-r2/service_baseline.json)：旧存储程序分号切分问题，不是 Q 新增 |
| 后补 EXPLAIN 三组 HTTP | **200 / 500 / 200** | [结果](evidence/v1.6.2.2-uat-o-r2/explain_context_results.json)，只改变 db_name；不能把第一批“无 5xx”推广到整轮 |

614 条的构成为：273 条核心/生产 fixture/KFN 输入 + 324 条扩展输入，共 597 次即时审核；3 次信息读取；3 个样例 × 文件内容/实际上传两种接口共 6 次；8 次角色检查。在线/报告/PDF 等补充请求另存 [supplemental_results.json](evidence/v1.6.2.2-uat-o-r2/supplemental_results.json)，不与浏览器点击数混算。

### 3.2 同一 1000 输入的改前改后差分

[round2_diff.json](evidence/v1.6.2.2-uat-o-r2/round2_diff.json) 显示：**70 条命中集合变化，全部仅增加 E999；930 条命中集合不变。** 这是规则 ID 集合的差分，不代表所有内部 AST、消息顺序和性能逐字段相同。

| 项目 | 首轮当前代码 | Q 修复后 |
|---|---:|---:|
| KFN 鉴别输入 | 75 | 同一 75 |
| 应有 E999 却缺失 | 72 | **2** |
| 旧语料精确期望差异 | 3 | 同一 3 |
| 独立判据失败合计 | 75 | **5** |

三个旧语料是 `R023_01`、`R098_01`、`R116_01`，仍缺精确期望中的 R036/R037。没有改 expected 让它们“变绿”。新增 324 条中 160 条集合变化：156 条 KFN 补回 E999，4 条含特殊语句文字的截断 CREATE TABLE 补回 E999；其他 164 条不变。30 条真实对象写法、4 条特殊语句控制、3 条标记字面量控制的命中集合没有新漂移。它们只证明所列样本，不证明所有存储程序语法完整。

### 3.3 119 条规则逐项口径

119 条注册、ID、分类、级别、启停及定义与首轮相同；页面显示 119 条，默认规则集 119 条启用。**114 条在本轮实际命中，5 条仍不能宣称验收通过**。详见本轮重新生成的 [119 条覆盖账本](evidence/v1.6.2.2-uat-o-r2/rule_coverage_119.md)。

- 107 条有非注入元数据输入命中；R048/R055/R056/R057/R058/R060/R064 共 7 条依赖显式合成 `table_metadata`，只验证分支，不代表真实 TDSQL 在线供给已验收。
- R025（ALTER 动作供给）、R035（existing_columns 供给）、R038（自增约束消费错位）、R049（占位返回 None）、R059（跨语句事务上下文）仍是首轮列出的既有缺口；没有发现 Q 对它们的新改动，但不把“注册存在”写成“有效”。
- 核心引擎及已支持样例未发现额外无法解释的命中漂移；**强制失败关闭仍不合格，因此不能签署“119 条核心能力完全无损且全部通过”。**

### 3.4 原始问题及用户页面

| 场景 | 本轮页面结果 | 证据 |
|---|---|---|
| gg77 / `kcfb_list_info`，分布式 | 9 条原有其他命中保留，无目标 R054 | [05](evidence/v1.6.2.2-uat-o-r2/05-gg77-regression.jpg) |
| gg78 / `biz_tx_log`，集中式 | COMMENT 解析恢复，仅 R036/R037 INFO | [06](evidence/v1.6.2.2-uat-o-r2/06-gg78-regression.jpg) |
| 首轮绿色误放行样例 | 现在 E999，文件上传也判未通过 | [02](evidence/v1.6.2.2-uat-o-r2/02-original-guard-fixed.jpg)、[07](evidence/v1.6.2.2-uat-o-r2/07-file-original-fixed.jpg) |
| HASH / 广播回归 fixture | 无目标 R077/R054；其他规则未被屏蔽 | [62](evidence/v1.6.2.2-uat-o-r2/62-hash-regression.jpg)、[63](evidence/v1.6.2.2-uat-o-r2/63-broadcast-regression.jpg) |
| 100 条分布式违规语料文件 | 2 通过、98 未通过 | [09](evidence/v1.6.2.2-uat-o-r2/09-file-corpus-100.jpg)；不是 UAT 通过率 2% |
| MyBatis XML | 3 条语句，1 通过/2 未通过 | [60](evidence/v1.6.2.2-uat-o-r2/60-mybatis-upload.jpg)；没有把局部样例等同于全部动态 SQL 语义验收 |

gg77/gg78 使用首轮已与用户附件核对一致的 SQL 文本，没有重新改写 SQL。HASH 的 `report_03...` 是仓库精简 fixture；广播的 `report_6297...full` 是带普通索引的仓库 fixture，不能仅因文件名含 full 就称它等于用户最初附件中的全部列。此处仅签署对应语法的回归结果。

## 4. UAT-O-01（BLOCK）：强制错误仍被异常返回路径绕开

### 4.1 可直接复现的输入和结果

在分布式即时审核粘贴以下 SQL；也可直接上传 [uat_r2_remaining.sql](evidence/v1.6.2.2-uat-o-r2/uat_r2_remaining.sql)：

```sql
CREATE TABLE t_guard (
  id BIGINT NOT NULL COMMENT 'id',
  sk BIGINT NOT NULL COMMENT 'sk',
  u INT NOT NULL COMMENT 'u',
  PRIMARY KEY(id,sk),
  s INT SERIAL DEFAULT VALUE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='LOAD DATA' shardkey=sk;
```

1. 解析器已产出 `known_fidelity_failures=['KFN-5-SERIAL-DEFAULT-VALUE']`；`ast=None`，`sql_type='CREATE TABLE'`，`parse_error` 却只是普通 `Expecting ) ... SERIAL ...`。
2. 页面显示 R003、R004、R005、R028、R042，**没有 E999**：[03](evidence/v1.6.2.2-uat-o-r2/03-kfn-load-still-missing.jpg)。文件实际上传并展开也一样：[08](evidence/v1.6.2.2-uat-o-r2/08-file-remaining.jpg)。`audit/sql`、`audit/file`、`audit/upload` 三入口同现。
3. 仅把表注释改成 `COMMENT='plain'`，就出现 E999 + R003/R004/R005/R028：[04](evidence/v1.6.2.2-uat-o-r2/04-kfn-plain-control.jpg)。`LOAD XML` 也复现。

**判据不是“这条 SERIAL 写法必须被完全支持”，而是设计已明确该 KFN 暂不能保真，必须明确失败关闭。** 不能因为其他规则碰巧报红，就认为已经满足这个合同。SQL 已写主键、InnoDB、utf8mb4、COMMENT，却让用户补这些不存在的缺项；R042 又把注释当真实 LOAD，提示方向也错误。

### 4.2 发生原因：结构化失败信号存在，消息字符串却没有

| 链路 | 当前实现 | 后果 |
|---|---|---|
| `parser/parser_legacy.py:2133` | source preflight 先写 `known_fidelity_failures` | 已经可靠知道不能保真 |
| 同文件 `2207–2217` | ParseError 且重试未恢复时，写 `str(e)`，随后提前 return | 未到 2254 行统一的 `KNOWN_FIDELITY_GAP[...]` 消息赋值 |
| 同文件 `2273–2277` | 删注释后对剩余原始文本查 LOAD DATA/XML，没有排除字符串字面量 | `COMMENT='LOAD DATA'` 使 `has_load_data=True` |
| `checker.py:146–151` | 只在 `parse_error` 字符串内找两个 marker，不看 `known_fidelity_failures` | 已证明 KFN 被误当普通解析错误 |
| `checker.py:163` | 特殊 LOAD 豁免压掉 E999 | 强制失败关闭丢失，业务规则继续消费不完整结构 |

Q 写的“`parsed.sql_type` 是 AST 判定、不受字符串影响”也不成立：异常路径调用 `_detect_sql_type_regex()`（2388 行），Command/UNKNOWN 分支也会回退。扩展样例 `SELECT 'CREATE VIEW' FROM` 的 AST 为 None，却被标成 CREATE VIEW，仅报 R030、无 E999；旧版相同。这个反例不是本轮新增故障，但足以否定“相信这个字段就能证明语句类型可靠”的修复前提。

### 4.3 扩展到路径组合，而不只重测一个字符串

[edge_probe.py](evidence/v1.6.2.2-uat-o-r2/edge_probe.py) 覆盖：

- 3 类 KFN：完整 CONSTRAINT UNIQUE、SERIAL、SERIAL DEFAULT VALUE；
- 3 类邻接索引：无额外注释索引、UNIQUE COMMENT、PRIMARY KEY COMMENT；
- 4 类表尾：常规、错误 ENGINE 值、截断 DEFAULT、PARTITION；
- 7 类注释文本：plain、四种 CREATE 对象、LOAD DATA、LOAD XML。

合计 252 个 KFN；另加 30 个对象写法/空白控制、35 个普通解析错误、3 个 marker 字面量、4 个 LOAD/OR REPLACE/DEFINER 控制，共 324。**包含故意损坏的表尾，不宣称 252 条都是 TDSQL 合法 SQL。** 这些病例检验的是“已识别 KFN 无论走哪条异常路径都必须阻断”。其中正常表尾的剩余 SERIAL 样例已经足够独立证实漏洞。

| sqlglot | KFN 输入 | 有 E999 | 无 E999 | 缺失病例关闭所有业务规则后 |
|---|---:|---:|---:|---|
| 29.0.0 | 252 | 192 | **60** | 全部 passed=true |
| 30.14.0 | 252 | 192 | **60** | 全部 passed=true |
| 30.17.0 | 252 | 192 | **60** | 全部 passed=true |

证据：[29.0.0](evidence/v1.6.2.2-uat-o-r2/edge_29_0_0.json)、[30.14.0](evidence/v1.6.2.2-uat-o-r2/edge_current.json)、[30.17.0](evidence/v1.6.2.2-uat-o-r2/edge_30_17_0.json)。三版缺失 ID 相同。这里的 60 与首轮剩余 2 属于重叠机制及相近输入，不应相加为 62 个独立缺陷。

### 4.4 具体处理机制与防次生影响要求

1. **强制失败优先读结构化信号。** 对 `bool(parsed.known_fidelity_failures)` 为真的结果，无条件生成 E999，不依赖消息是否有 marker、是否非空，也不进入 VIEW/PROCEDURE/FUNCTION/TRIGGER/LOAD 豁免。为 UNIQUE 保真失败建立同样明确的 failure category / `must_fail_closed`；短期兼容现有 marker 可以保留，但不能作为唯一真值源。
2. **补齐所有 return 路径的归一化。** preflight、native Create、Command、ParseError、重试失败都保留失败类别及原始诊断。不要把原始异常简单覆盖掉导致可诊断性下降；结构化类别负责决策，消息负责展示。不要只改 2254 行正常出口。
3. **只对普通解析错误做可靠的特殊语句豁免。** 沿用项目已决定的 sqlglot 词法器/可靠 AST，识别真正顶层语句头；SQL 注释、COMMENT/DEFAULT 字符串、反引号标识符不能启动豁免。不再对整条字符串用正则 search；现有 `sql_type`、`has_load_data` 不能未经溯源直接视作可靠 AST 证据。
4. **不扩大拒绝域。** 不把 `unique_constraints_complete=False` 一律当错，不把 unknown source 等同于已证明 KFN；不删除所有真正存储程序/LOAD 历史豁免，不更改119规则数、业务启停与级别，不放宽扫描器白名单来绕开已证明的失败。TDSQL 语义注释按既定设计处理，不能一把剥掉。
5. **新增可执行的消费者级测试。** 原 75 个全部保留；新增 252 组合断言结构化失败、E999、passed=false；至少包含全业务规则关闭时仍强制失败、消息无 marker/普通异常、反向真对象及字符串诱饵。对三 API 入口、真实上传、页面详情/导出报告贯通断言，不能只断言 parser 或 HTTP200。

**关闭条件：原剩余 2 个归零、新扩展 60 个归零，真对象及已支持输入没有新增误报；三版本矩阵、全量和原始故障回归保持。** 若最终只保证某个依赖版本，必须同步声明支持范围，不能选择一个“恰好通过”的版本绕开本轮三版共同存在的根因。

## 5. 首轮其他问题及本轮新发现

### 5.1 保持首轮问题 ID，纠正修复台账

| 原 ID / 等级 | Q 修复说明写法 | 本轮复测结论 |
|---|---|---|
| O-01 / BLOCK | 已修，两段式豁免 | **部分修复，仍未关闭**，见 §4 |
| O-02 / MAJOR | 已在 a698cfc 修复 | **未修完整**：前端源码和新登录仍1.6.2.1 |
| O-03 / MAJOR | 漏列，改把 CSS 标成 O-03 | **网关报告空正文仍存在** |
| O-04 / MAJOR | 改号为 O-03，延期 | **CSS 泄漏仍存在** |
| O-05 / MAJOR | 改号为 O-04，延期 | **假数据仍存在** |
| O-06 / MAJOR | 改号为 O-05，延期 | **PDF 缺记录仍存在** |
| O-07 / MINOR | 改号为 O-06，延期 | **标记交互仍存在** |
| O-08 / MAJOR | 本轮新登记 | **EXPLAIN 旧数据库上下文串页**，历史代码未变 |

请 Q 以原 ID 更新“已修/部分修复/未修/批准延期”及对应提交、证据、验收负责人；遗漏条目不能消失，延期需要明确批准记录。这是追溯要求，不额外重复计一项产品缺陷。

### 5.2 O-02：前后端版本不一致

本轮全新登录 [01](evidence/v1.6.2.2-uat-o-r2/01-login-version.jpg)、退出后 [64](evidence/v1.6.2.2-uat-o-r2/64-final-logout.jpg) 都是 V1.6.2.1；[43 系统信息](evidence/v1.6.2.2-uat-o-r2/43-system-version.jpg) 与 health 为1.6.2.2。`frontend/index.html:8/16/18/30/2758` 仍硬编码旧 title、登录文案及 CSS/JS 版本参数，不是简单缓存推测。

**处理：** 以单一版本源生成页面、服务和资源版本；本次至少统一上述位置。验收新会话、旧缓存升级、强刷的三处版本一致；不能重复只改 VERSION 或 backend 配置便关闭。

### 5.3 O-03：网关报告只有页脚

实际上传两条本地日志，[13](evidence/v1.6.2.2-uat-o-r2/13-gateway-stats.jpg) 显示总2、慢1、均值756.35ms、最大1500.2ms；“查看报告”[14](evidence/v1.6.2.2-uat-o-r2/14-gateway-empty-report.jpg) 只有 Generated by 页脚。实际 [HTML](evidence/v1.6.2.2-uat-o-r2/gateway_report.html) 为8349字符/8463字节，没有分析正文。

**原因未变：** `gateway_log_service.py` 将上传文件改成 `uploaded_<pid>...`，分析器 `_organize_specific_files()` 要求受支持的 interf/sql/instance 命名，因而丢失有效分组；另一套 timecost 解析仍给页面统计，报告器却对空分组输出“成功 HTML”。

**处理：** 逐请求独立临时目录 + 受控文件名，或显式传入 log_type/instance 元数据；统计与正文共享解析结果。检查返回码、有效输入/记录数、章节，不以“生成了 HTML”代替有效报告。回归规范/非规范文件名、空/坏日志、多文件与并发隔离；不得直接拼接用户路径。

### 5.4 O-04：完整 HTML 注入污染应用 CSS

[计算样式证据](evidence/v1.6.2.2-uat-o-r2/gateway_css_leak.json)：打开前 body padding=0px、报告 style 节点0；关闭后20px、节点1。后续慢 SQL 抽屉 [22](evidence/v1.6.2.2-uat-o-r2/22-slow-mark-opens-detail.jpg) 可见白底表格和低对比文字；刷新后样式清除。

**原因未变：** `frontend/index.html:2703` 的 `v-html="gatewayHtml"` 把有 `body/table/td/th/*` 全局选择器的整篇报告放进应用 DOM；关闭抽屉不构成文档隔离。

**处理：** 清洗后的受限 iframe/srcdoc 或严格作用域化正文/样式，配置必要 CSP、最小权限，关闭时释放资源。不只改回 padding。验收打开前/中/后/切页/退出、长表、多个报告、深浅主题的全局样式不变；本轮证实 CSS 泄漏，不夸大为已复现脚本攻击。

### 5.5 O-05：未采集数据却给正式健康分和业务数据

采集索引前，数据库核对巡检、大表记录均为0；看板 [15](evidence/v1.6.2.2-uat-o-r2/15-dashboard-mock.jpg) 却显示 **健康89、巡检实例3、重复索引3组**。本轮不是首轮的100分，不能照抄首轮数字。PDF [第1页](evidence/v1.6.2.2-uat-o-r2/ops_report-1.png) 仍有 `biz.t_transaction` 8,920,194行/12.8GB、`biz.t_order_detail` 3,410,294行/4.5GB 等无来源数据。

**原因未变：** `ppt_report_service.py` 的巡检、索引、大表等空结果分支返回演示记录；评分还据此计算。之后做过真实索引采集不否定之前空态已经被伪造；证据按时间保存，没有把前后两种状态混用。

**处理：** 所有模块使用明确 `no_data/unavailable/stale`、来源、采集时间及覆盖度；Mock 仅限显式演示模式/fixture，不进入正式报告。无依据的请求量、95%/4%分布、把慢查询当错误数等同类逻辑也需一并检查。不能以“全填0”替换假数据；无数据不代表健康。空、部分、完整、过期、MonitorDB 断开都需页面和 PDF 合同断言。

### 5.6 O-06：PDF 漏掉真实存在的网关记录

网关记录 id1 在列表存在，总2/慢1。另行 HTTP 获取的 [PDF](evidence/v1.6.2.2-uat-o-r2/ops_report.pdf) 为200、5417字节，共两页；按 PDF 技能逐页渲染及视觉检查，[第2页](evidence/v1.6.2.2-uat-o-r2/ops_report-2.png) 仍称暂无网关日志分析存储记录。不是裁切造成。

**原因未变：** 模板消费 `modules.gateway_analysis.reports`，生产者提供 summary/daily_stats 等而无 reports。日报 history 的同类静态合同错配仍需真实阳性数据验证，不能写成已实库复现。

**处理：** 统一带类型的 DTO/适配器，网关内容带真实记录id、时间、总数、慢数、平均/最大时延；全模块消费字段一致。不造假 reports 来填空。补契约、PDF文本及逐页渲染测试。本轮浏览器下载事件等待5秒超时，**未证明客户端文件已落盘**；缺陷依据的是另行获取并检查的真实服务端 PDF。

### 5.7 O-07：点击“标记”打开详情抽屉

合成慢 SQL id1、库名 `uat_o_r2_workflow`。点击“标记”即打开详情，[22](evidence/v1.6.2.2-uat-o-r2/22-slow-mark-opens-detail.jpg)；刷新去掉网关样式污染后仍复现，[24](evidence/v1.6.2.2-uat-o-r2/24-slow-mark-clean.jpg)。排除了 O-04 是此交互故障原因的解释。本轮未通过菜单完成状态更新，不借用首轮曾成功更新的截图算第二轮通过。

**原因未变：** `index.html:919` 行点击打开详情，930 行 dropdown/标记没有阻止冒泡。

**处理：** 操作单元格/触发器阻止父行激活，或让行点击排除交互控件，同时保留数据行打开详情。验证三状态、关闭重开、Enter/Space、权限与持久化；不能只验证状态 API。

### 5.8 O-08：独立 EXPLAIN 使用已关闭慢 SQL 详情的库名（新登记，历史问题）

**复现链：**

1. admin 查看上述慢 SQL 详情，然后关闭。本轮是点击“标记”触发 O-07 后进入详情；源码确认正常“详情”按钮也调用同一 `openSlowDetail()`。核心原因是详情状态残留，不是 O-07 的事件冒泡本身。
2. 从侧边独立 EXPLAIN 菜单选择 `UAT-O 本地集中式样本`，输入 `SELECT id FROM tdsql_uat_o_target_1622.t_uat_order WHERE customer_id = 1`。
3. [44](evidence/v1.6.2.2-uat-o-r2/44-explain-stale-failure.jpg) 报“数据库不存在，请检查连接配置中的数据库名”。连接的默认库和 SQL 的全限定表名都是真实存在的。
4. 刷新页面，从独立菜单重填**同一连接、同一 SQL**，[45](evidence/v1.6.2.2-uat-o-r2/45-explain-clean-success.jpg) 成功显示 `ref / idx_customer / Using index`。

**原因机制：** `frontend/static/js/app.js:514` 缓存 `slowDetail`，关闭抽屉未清除；546 行 `analyzeExplainBySql()` 无条件从 `slowDetail.db_name` 构造独立页面请求。后端 `slow_query_service.py:287` 以请求 db_name 优先覆盖所选连接默认库，再注册连接。补充 HTTP 仅改变 db_name 得到200（不传）/500（旧库）/200（正确库），见 [三组对照](evidence/v1.6.2.2-uat-o-r2/explain_context_results.json)。本轮没有实际创建旧库，也没有证实错库中同名表时的错误执行计划；后者是机制风险，不能冒充已复现结果。

**归因：** 与 `6957499` 比较上述前后端文件无差异，属于本轮跨页面顺序覆盖新发现的历史问题，不是 checker 改动带来的次生回归。

**处理：** 把 EXPLAIN 的 connection_id/db_name/sql 作为独立、可见的表单上下文；仅显式“从该慢 SQL 去分析”时原子复制来源上下文。进入独立菜单、切换连接或退出角色时重置/重新选择库名，不直接依赖全局 slowDetail。后端核对有效目标库，业务输入错误返回可理解的4xx，且请求级库选择不应污染共享连接配置。

**关闭测试：** 未查看详情、查看库A后分析库B、来源不存在库、同名表不同库、切换实例、退出重登、详情直达与独立菜单两种入口；核对 UI 可见库、请求库和执行计划实际对象。用两库合成只读测试即可，不必触碰生产表。

## 6. 本轮真实浏览器覆盖矩阵

证据编号对应 [browser_steps.json](evidence/v1.6.2.2-uat-o-r2/browser_steps.json) 中同名前缀的 JPEG/TXT。每行只验收列出的动作；没有把“打开页面”扩写成完整业务成功。25/27 是 EXPLAIN 失败中间态，44/45 才是明确的失败/成功对照。

| 可见功能入口（19个） | 本轮实际动作和结果 | 证据编号 |
|---|---|---|
| 治理概览 | 登录/刷新，显示本轮审核及慢 SQL 汇总 | 23、46 |
| 即时审核 | 原始故障、KFN、普通注释单变量对照、HASH/广播，核对逐项违规 | 02–06、62–63；O-01未过 |
| 文件审核 | 选择器上传原KFN/剩余KFN/100条语料/XML，展开详情、看历史 | 07–10、60–61；O-01未过，下载保存未证实 |
| 在线元数据审核 | admin/developer 实际提取两表；查历史、勾两次快照、对比4→4、保存留档 | 11、47–51；真实集中式子集通过 |
| 扫描任务 | admin Digest实际抓取50条；developer新建面板可选实例 | 28、52；MonitorDB路径未验收 |
| 慢SQL记录 | 合成记录详情、1000:1提示、标记按钮及刷新复核 | 22、24；O-07未过 |
| EXPLAIN分析 | ALL/100000行JSON建议，直连失败/刷新恢复 | 25–27、44–45；O-08未过 |
| 上线检查 | 实际完成12项检查，11720条本地测试库问题 | 29；是流程验证，不是119条审核规则覆盖数字 |
| 大表治理 | 实际采集及空态，developer实例选项 | 30、53；真实大表/趋势未验收 |
| 深度诊断 | 9页签，见下表 | 12–21；混合结果 |
| 实例管理 | admin空表单必填/ZK入口；auditor只读及筛选；DBA新建编辑刷新、离线连接 | 31–33、54–59；删除/ZK成功路径未验收 |
| 审核规则库 | 119条分类；搜索R054仍展示该类14条，保留首轮搜索粒度观察 | 34–35 |
| 评估规则集 | 默认119条；内置开关只读；没有改全局策略 | 36–37 |
| 用户管理 | 搜索合成auditor，仅匹配一行 | 38；不含增删/重置口令 |
| 角色管理 | 查看四内置角色、删除禁用 | 39；不含自定义角色CRUD |
| 权限矩阵 | 查看四角色菜单权限，并以真实角色登录交叉验证 | 40、46–59；未在UI保存权限变更 |
| 数据保留 | 读取12项策略 | 41；不运行清理 |
| 操作审计 | 筛选admin，查看成功和失败操作记录 | 42 |
| 系统信息 | 后端1.6.2.2、鉴权/脱敏状态，与登录页不一致 | 01、43、64；O-02未过 |

| 深度诊断页签（9个） | 本轮结果 |
|---|---|
| 集群巡检 | 实际发起；无MonitorDB，补充HTTP400；成功路径受环境限制（12） |
| 日常巡检与对比报告 | 日期、阈值、空态及比较/导出禁用；未实际采集日报（16） |
| 索引体检 | 两表四索引，0ERROR/0WARNING，2条unused INFO（17） |
| 结构比对 | 同连接同库，0差异（18）；不代表跨实例差异全部正确 |
| 应急诊断 | 实际执行六个只读维度，展示连接/事务/锁/长SQL/引擎状态（19） |
| SQL分析 | 实际发起，MonitorDB不可用；接口400；20是发起时状态，后续提示及接口证据共同界定环境限制 |
| 网关日志分析 | 实际上传、指标、空正文及CSS泄漏（13–14） |
| PDF报告与大屏 | 假数据、PDF下载事件未捕获；另行获取PDF并逐页检查（15及PDF/PNG） |
| 运维工具箱 | 四个脚本下载入口；不下载执行运维脚本、不声称文件保存成功（21） |

权限方面：开发者无实例管理菜单仍能真提取和选择业务实例；审计员有实例菜单但无新建/编辑/删除/ZK管理控件；DBA新建连接 `618d9bed`、编辑名称后整页刷新仍保留。补充接口中 developer 管理列表GET及 developer/auditor 新建POST为预期403，其业务 options 均200。没有在浏览器或接口中执行删除连接、批量清理等破坏性操作。

## 7. 次生风险、工程限制与诚实披露

### 7.1 有界性能检查

复用首轮脚本，在同机同依赖、当前与6957499代码分别预热3次、审核15次取中位数：

| 列数 / SQL字符数 | 6957499 | d12fe8c |
|---|---:|---:|
| 21 / 723 | 3.844ms | 3.623ms |
| 101 / 3043 | 15.148ms | 15.111ms |
| 501 / 15043 | 74.007ms | 72.757ms |

[基线](evidence/v1.6.2.2-uat-o-r2/performance_baseline.json)、[当前](evidence/v1.6.2.2-uat-o-r2/performance_current.json)。未见该有界样本的性能退化；小幅下降可由测量波动产生，不宣称性能优化。没有生产并发、长时间资源泄漏或SLA认证。

### 7.2 未隐藏的测试工程失败

- 初次全量误继承本轮浏览器环境的 `AUTH_ENABLED=true`，而 `tests/conftest.py` 对旧测试默认设为false、鉴权测试自己打开。于是4个 `test_daily_inspect_compare.py` 无header请求返回401：**1380 passed/4 failed**。正式 runner 的首次运行也因此失败。保留 [独立失败日志](evidence/v1.6.2.2-uat-o-r2/full_regression_auth_override.txt)、[失败JUnit](evidence/v1.6.2.2-uat-o-r2/full_regression_auth_override.xml)、[正式失败日志](evidence/v1.6.2.2-uat-o-r2/implementation_matrix_auth_override.txt)。
- 只修正 O 自有 runner 的环境为测试框架预期值，未改产品/断言，然后独立及正式全量均1384通过。浏览器服务始终开启鉴权。这4个401不登记产品回归，也不删除失败证据。
- 初始化浏览器服务与角色fixture的顺序有竞争：先创建了非admin用户，启动逻辑因用户表非空不创建admin，第一次admin登录401。确认账户缺失后，仅在本轮精确库名下补建合成admin，没有重置既有用户口令。该失误属于测试准备，首次登录改口令不属于本轮通过项。
- 仍沿用显式admin/慢SQL回归fixture，不宣称全空库零准备自动化通过。部分浏览器选择器因自定义checkbox或表单“保存/更新”不同发生操作超时，重新观察页面后完成；没有把工具定位失败当产品故障，也没有绕过页面去改前端状态。

### 7.3 不能签署通过的范围

没有真实 TDSQL Proxy/SET、可用 MonitorDB/ZK、跨实例结构差异、大表分区/增长历史及故障现场；未运行生产压力测试、完整调度周期、破坏性删除清理、用户/角色全部CRUD、真实口令首改、所有浏览器/主题组合。HTML/PDF下载均实际点过，但下载事件5秒未捕获；接口可导出和PDF可读**不替代浏览器最终保存成功**。工具箱下载保存/运行也未验收。

17个服务层差异为旧routine分号切分问题；R104对自然语言注释全角括号、规则库按类别而非规则过滤等首轮观察仍应纳入后续台账。本轮不为了强制宣称“全项目覆盖”而把这些缺口写成通过。

## 8. 修复优先级、复测入口与交付

1. **立即修 O-01 的结构化强制门禁**，同时新增本轮失败样例的回归测试；不要继续只补示例字符串。完成后按 §4.4 三入口、三版本和真实页面核验。
2. **纠正 O-02 和 Q 处置台账**；其余历史 MAJOR 的延期由负责人明确批准，逐项保留，不能通过重编号或省略解除。
3. **报告真实性优先于外观修饰**：O-03/O-05/O-06 应使内容和来源可追溯；O-04 做文档隔离；O-07/O-08 分别修事件冒泡和独立表单上下文。
4. 再次送测前，Q 提供原ID→提交→新增测试→实际结果映射。至少运行原75KFN、新252KFN+72控制、1000差分、三个680矩阵、71冻结和全量，并附失败样例三入口及默认/非默认规则策略结果。不得改低断言或把KFN改成未知来“通过”。

脚本和命令见 [证据README](evidence/v1.6.2.2-uat-o-r2/README.md)。交付只包含本报告和 `docs/evidence/v1.6.2.2-uat-o-r2/`，不触碰产品源码及首轮材料。

**残留状态：** 浏览器已退出合成账号（64）；本轮8002服务保留供复现，原8000未改。隔离元数据库保留4个合成账号、3条连接（含 `618d9bed`）、1个扫描任务、51条慢SQL（1条显式fixture+50条Digest）、审核及快照对比留档，见 [final_state.json](evidence/v1.6.2.2-uat-o-r2/final_state.json)。未删除这些可复现数据，未改全局规则策略、正式权限或生产库。临时旧代码归档和矩阵虚拟环境也保留，不计为项目源码变更。

**最终判定：两个原始修复有界通过，现有自动化回归通过；系统强制失败关闭及全项目用户验收仍不通过。**
