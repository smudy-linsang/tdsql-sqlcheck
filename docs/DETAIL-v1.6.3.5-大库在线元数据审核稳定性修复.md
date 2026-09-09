# v1.6.3.5 大库在线元数据审核稳定性修复——详细设计说明书

| 项目 | 内容 |
|---|---|
| 文档版本 | Rev.A，2026-09-09 |
| 产品修复版本 | v1.6.3.5 |
| 提交对象 | Mr.Linsang；供 Q 实施、A 评审/SIT、O UAT、G 发布编排使用，不代表已向上述责任方派单 |
| 设计基线 | `main` / `ff4fd03`，业务代码版本 v1.6.3.4 |
| 当前交付性质 | **仅设计文档；未修改业务代码、测试代码、数据库、配置或部署环境** |
| 状态 | 待设计评审；实现、真实 TDSQL 容量验收及发布门禁均未完成 |

## 1. 结论与范围

本版采用“**逐句解析审核 + R035 精简等价索引 + 独立元数据任务执行器 + 短请求查询进度**”的修复方案。不是把浏览器、网关或 Uvicorn 的等待时间统一调大，也不是关闭 R035、删减审核表数或放松规则。

已经证实的代码缺陷是：当前 `audit_file()` 同时保留全批 AST，并为每条语句复制此前全部 R035 列引用，产生平方级引用增长。内网确切的进程终止机制尚未完全取证，不能宣称已证实 OOM 或健康检查强杀；修复应同时消除已证实缺陷与长请求/同进程重任务的脆弱性。

| 编号 | 必须交付 | 不扩大的边界 |
|---|---|---|
| FIX-01 | 批量审核取消全量 AST 保留和全历史逐句快照，R035 结果及首个冲突来源保真 | 不改变任何审核规则的业务尺度、解析器类型归一、SQL 拆句语法 |
| FIX-02 | 在线元数据提取与审核在独立执行进程运行；任务持久化、幂等、取消、恢复查询 | 不建设通用调度平台，不引入 Redis/Celery，不迁移慢 SQL/网关任务 |
| FIX-03 | 浏览器显示真实阶段、计数、失败原因；分页查看、下载完整 SQL 和报告 | 不扩展其他页面功能，不重做四模块跨页对比 |
| FIX-04 | 缺对象、落库失败、异常退出和回收失败不得伪装成功 | 不执行目标业务库 DDL/DML，不自动补建/修改业务对象 |
| FIX-05 | 两个 Web worker 下只有一个元数据重任务，部署与回滚覆盖新执行服务 | 不借此增加网关并发，不自动修改服务器内存/数据库包大小配置 |

以下既定行为保持：v1.6.3.4 报告实例连接名称冻结；R043 顶层语句判断；R011/R120、R030/R032/R035/R058、R121 的既有业务口径；表类型统计共享 **180 秒**预算和 PARTIAL/UNKNOWN 表达；网关分析并发 **1**。本文件新设的元数据任务预算不复用、更不覆盖这两个模块的参数。

## 2. 证据、判断与不能下的结论

### 2.1 本次新增材料

1. Mr.Linsang 手工计时：`00:01:00 → 00:05:39`，相隔 **279 秒**。仅有钟点，文档归档日期不充当采集日期证明。
2. `wd3e.txt` 和截图中，`/api/v1/audit/extract-and-audit` 明确出现 `net::ERR_EMPTY_RESPONSE`；页面展示 `提取失败: Failed to fetch`。
3. 其他接口出现 HTTP 401。它们的 `_t=1788854155813` 换算为东八区是 `2026-09-08 15:55:55.813`。这是客户端请求参数时间，不是服务端响应时间，也不是本轮审核接口的时间戳。
4. 控制台共显示四条审核接口空响应，不足以证明本轮同时发起四个请求，可能含保留的历史日志；不得按“四并发”做根因定论。

Chromium 将 `EMPTY_RESPONSE(-324)` 定义为对端关闭连接而没有返回数据；这支持“没有可用 HTTP 响应”的判断，**不定位**到底是哪一个进程或中间设备关闭连接，也不等价于已抓到 TCP RST。[Chromium 官方源码](https://chromium.googlesource.com/chromium/src/+/main/net/base/net_error_list.h)

| 材料 | SHA-256 |
|---|---|
| `wd3e.txt` | `830BCA2EBE828BAE646C09DC55B48DFA564B5288A294C1CD7C1A801F5A18ADD2` |
| 本轮 `codex-clipboard-cc64175b-f40e-4d4e-a340-531cb6452613.png` | `F1B5270DA362DC946B8272BF4B5AB4F897E41D91FC8BD01CA6C24815AE6BA755` |
| 前轮 `wde.txt` | `6B52E1A7BD46D45661777855FAA03848DA6F74DDD63144E9B00207FA97A4C31D` |
| 前轮 `wd2e.txt` | `2D60CC81DABEA113E126341B4179189EC3D6C5CB9F8D6D7D0B0C49CE4731F4E6` |

### 2.2 与前轮证据合并后的分层判断

| 判断 | 证据强度与限制 | 对方案的影响 |
|---|---|---|
| R035 全历史快照有平方级内存缺陷 | 源码、版本差异、本机分档测量相互印证，确定 | 必须修复，不能仅改超时 |
| 旧现场确有审核相关 Web worker 退出并补建 | 18:10:09 子进程退出、18:10:10 补建日志；不能直接套到本次 00 点复测 | 隔离重任务并记录子进程退出事实 |
| cgroup 内存限额导致 OOM | 前轮 v1 服务/父级/root 内存限制为无限制哨兵值，failcnt/oom_kill 均为 0；不支持此结论 | 不以扩容或 MemoryMax 调大作为根修复 |
| Uvicorn 健康检查触发终止 | 父进程环境未覆盖标准 5 秒窗口；机制成立，但该现场精确信号/阻塞原因未确证 | 保留为主要工作假设；不把 5 秒说成整个审核的时限 |
| 固定 250 秒应用超时 | 新计时为 279 秒，代码未找到对应固定时限 | 不编造一个 250 秒参数来修改 |
| JWT 过期直接导致本轮审核空响应 | 401 来自其他 URL；现有鉴权失败有明确 HTTP 401 JSON | 单独回归鉴权，不关闭认证或延长令牌有效期 |

前轮 cgroup 服务内存峰值为 `3,889,078,272` 字节，约 3.622 GiB；它是累计观测峰值，不是本轮故障瞬时 RSS，也不是某一个 Python 对象的大小。本轮没有新增服务进程日志，不能宣称已再次观测 worker 死亡。

详细原始分析沿用 [O 独立排查报告](DIAG-6000表元数据审核失败-O独立排查与证据边界报告.md) §13—14、[O 本机量化证据](DIAG-6000表元数据审核失败-O本机量化证据与复现步骤.md)。G、Q、A 和内网智能体报告是交叉核对材料，不以其中推测替代现场事实。

### 2.3 本版为何不能只改线程或延长等待

Uvicorn 的 `timeout-worker-healthcheck` 是等待 worker 健康响应的窗口，与 keep-alive 空闲等待及业务处理时长不同。[Uvicorn 官方设置](https://uvicorn.dev/settings/)

`async def` 内直接运行同步提取/解析不会自动隔离 CPU/内存；仅 `asyncio.to_thread()` 仍与 Web 共享进程内存和解释器资源。FastAPI 官方也区分进程内轻任务与适合其他进程执行的重计算。[FastAPI 官方说明](https://fastapi.tiangolo.com/tutorial/background-tasks/#caveat)

因此采用独立执行器是本次稳定性修复的实现步骤，并明确承担新增一个本机服务的部署成本；不以“后台线程已启动”冒充可恢复的持久任务。

## 3. 基线代码定位与回归契约

下列行号以 `ff4fd03` 为准；施工时以符号定位，行号随修改变化。

| 文件/符号 | 当前问题或必须继承的行为 |
|---|---|
| `backend/engine/checker.py:272 audit_file` | 第 300 行全量 `parsed_items`；随后全量 `metas`；全批 AST 和历史引用同时驻留 |
| 同文件 `:325 _build_r035_cross_table_context` | `{k: list(v) for k,v in index.items()}` 对每条语句复制旧列表，非 CREATE 也付出复制成本 |
| `backend/engine/rules/ddl.py:547 R035CrossTableFieldType` | 只比较此前、不同表、同名列的规范类型；保留第一条不一致来源；长度不比较 |
| `backend/engine/checker.py:_audit_parsed` | R035 私有上下文不能泄露给 R064 等规则；E999 与 v1.6.3.4 R043 UNKNOWN 失败关闭保持 |
| `backend/api/sql_audit.py:255 extract_and_audit` | 同一请求同步提取、审核、保存、快照、返回全量 SQL/结果；逐表异常被 warning 后跳过 |
| `backend/services/audit_service.py:_save_audit_history` | 保存异常只记 warning、返回 None，在线路径仍可返回 SUCCESS；新任务不能沿用这个吞错契约 |
| `backend/services/ruleset_service.py:get_active_overrides` | 30 秒进程缓存；规则集 ID 和内容存在分次读取路径；新任务需一次冻结执行尺度 |
| `backend/services/database.py` / `backend/schema/migrator.py` | 元数据库是 **MySQL**，`?` 是兼容游标占位符，不是 SQLite；迁移失败关闭 |
| `frontend/static/js/app.js:1707 runExtractAndAudit` | 一次 fetch 等待完整结果；没有持久 job_id；不能区分通信失败与审核失败 |
| `frontend/index.html:373` 起 | 全量 SQL 展示、全量结果折叠列表；大结果需按页加载 |
| `scan_snapshot_service.create_snapshot` | 对比快照是旁路；最多 20,000 条问题并保留截断信息；失败不使已完成审核变“通过”或抹掉报告 |

回归引入点为 `c0e5e25d748c7443d87c95f4bfb897d593abd753`（v1.6.3.2 REQ-05A），其父提交 `887def684ba6f8e302b6abba8f38a8e351326aad` 的文件审核逐句解析执行。不得通过整体回退到 v1.6.3.0 丢掉 v1.6.3.2/4 的业务修复。

## 4. FIX-01：保真消除 R035 平方级增长

### 4.1 复杂度与必须保留的语义

假设 N 张表各有 C 个有效列，旧快照累计保留引用槽位：

`S = C × N × (N - 1) / 2`

N=6000、C=20 时为 359,940,000 个槽位，仅按 64 位指针计算就需 2,879,520,000 字节（约 2.682 GiB），尚未包括字典、AST、SQL 和结果。此为条件计算，不是实际 6000 表的内网测量。

必须保持：

1. 每个逻辑语句解析恰好一次；继续使用 `normalize_newlines`、`split_audit_script`、既有 MyBatis 提取器，不使用 `split(';')` 或新正则拆句。
2. 同一文件内按原始顺序：先与此前历史比，再将本语句的成功 CREATE 列加入历史。没有当前语句自引用、未来引用或跨请求缓存。
3. 按当前列顺序找到第一个不一致列，再按此前语句/列顺序找到第一个冲突引用，错误消息、raw_type、来源表和行号不变。
4. 列索引键仍为 `name.lower()`；表名比较沿用现有精确字符串相等；类型比较沿用 `(type or '')`。不额外折叠表名大小写、移除 schema、解析长度或推导同义类型。
5. 解析失败、非 CREATE、无表名、无列名不追加历史；R035 实际禁用时不创建索引；规则集严重级别覆盖和架构过滤保持原有唯一入口。
6. R035 元数据仍是 `dict[str, list[dict]]`，保留键仍为 `__r035_cross_table_columns__`，只有 R035 消费；不把索引对象直接塞给现有 `isinstance(index, dict)` 的规则。

### 4.2 两种不采纳的简化

“同名列只保留第一张表”会漏报：`a.id INT → b.id BIGINT → c.id INT` 的 R035 命中应为 `[否, 是, 是]`，不能变成 `[否, 是, 否]`。

“一个增量完整历史列表 + 每次从头查”可消除快照内存，但所有同名同类型列仍可能扫描全部历史，CPU 最坏仍为平方级。本版不把它作为最终实现。

### 4.3 有界见证索引：每个列名最多 5 个引用槽位

新增 `backend/engine/r035_context.py`，实现请求局部 `R035PriorIndex`。每个列名的 summary 保存：

| 槽位 | 含义 |
|---|---|
| anchor | 该列名最早有效引用 a，固定不替换 |
| outside_anchor_table，最多 2 条 | 在 `table != a.table` 的历史中，最早出现的两个不同规范类型的首条引用 |
| outside_anchor_type，最多 2 条 | 在 `type != a.type` 的历史中，最早出现的两个不同表名的首条引用 |

引用仅含 `table_name,type,raw_type,statement_index,column_index` 标量。增加的 `column_index` 只用于稳定排序，不改变消息；不得持有 ParsedSQL、AST、完整 SQL 或父对象引用。两个列表满 2 后不替换；按既定属性去重，不是“最后两条”。对外投影合并三个槽位组，按 `(statement_index,column_index)` 去重并排序，最多 5 条。

等价性证明：设查询当前表 T、类型 Y，历史 anchor 为 A、X。

- T≠A 且 Y≠X：anchor 就是最早冲突。
- T=A：必须排除 A 表；outside_anchor_table 保存其余历史最早两个不同类型。一个查询类型最多排除其中一个，剩下最早者就是完整历史的首个冲突；只有一种且被排除则确实无冲突。
- T≠A 且 Y=X：必须排除 X 类型；outside_anchor_type 保存其余历史最早两个不同表。一个查询表最多排除其中一个，同理可取得完整历史首个冲突。

三种情况覆盖全部查询；把这些见证按历史顺序交给**现有 R035.check**，其第一条冲突与完整历史扫描相同。重复列名、同表多次 CREATE、规范类型为空也适用；保留 raw_type 原文用于原错误消息。

索引插入 O(1)/列、每次查询至多 5 引用；U 为不同列名数、M 为总列出现数，则索引空间 O(U)、累计索引工作 O(M)。这不是整个解析器或整个审核 O(M) 的承诺，其他规则/SQL 解析仍需独立测量。

### 4.4 checker 施工顺序

```text
规范化输入 → 既有拆句/提取器（保留原顺序和行号）
有效规则中有 R035 ? 创建请求局部 R035PriorIndex : None
for statement_index, (sql, line) in stmts:
    parsed = parser.parse(sql)                         # 只一次
    meta = 当前 CREATE 所涉及列名的见证投影，或 {}
    violations = _audit_parsed(parsed, sql, meta, ...)
    生成 AuditResult（保持现有字段和顺序）
    若有效成功 CREATE：追加本语句的列见证              # 必须在审核之后
    释放 parsed/meta 的本次引用                        # 不存入容器/闭包
return results
```

增加内部迭代入口 `iter_audit_file(...) -> Iterator[AuditResult]`；公共 `audit_file(...)` 保持返回列表，内部 `list(iter_audit_file(...))`，所有已有调用不破坏。在线子进程用迭代入口写结果文件，不为保存结果重解析 SQL。迭代器在 yield 前完成本语句历史更新并解除 AST 引用，取消/close 后上下文可释放。

旧 `_build_r035_cross_table_context` 从生产调用链移除，不留可被新入口误用的兼容分支；旧算法仅可作为小规模测试 oracle。R035 规则类本身原则上不改；测试中依赖旧私有方法的部分改为断言业务契约。

注意：现有拆句器可能仍产生完整语句列表，`audit_file` 仍返回完整结果列表，故整批内存不是常数。本版消除的是历史复制和全批 AST；在线路径通过文件化结果、资源限额控制剩余线性项，不谎称完全流式零内存。

### 4.5 已完成的设计级验证与待完成验证

2026-09-09 在本机运行独立 Python 模型：枚举 3 个表 × 3 个类型、长度 0—5 的全部历史，查询使用 4 个表 × 4 个类型（含从未出现值）。66,430 组历史、1,062,880 次首个冲突查询，与完整历史 oracle **0 差异**，观测最大去重见证数为 4；实现契约保守上界仍写 5。

该模型未导入或修改生产代码；不是 SIT、UAT、SQL 语法验证或内网 6000 表容量证明。Q 必须按 §11 将模型判定移入自动化测试，并使用真实 parser/全部规则验证完整 AuditResult 等价。

## 5. FIX-02：在线任务执行架构与参数

### 5.1 唯一执行路径

```text
浏览器 → Web（鉴权、受理/查询，两个 worker）→ 元数据库 MySQL 任务记录
                                             ↑ 轮询/状态
                    本机 metadata-runner 服务（单实例、轻量监督）
                                             ↓ 一个隔离子进程
               提取只读 DDL → 文件审核 → 结果/HTML 制备 → 原子发布报告
                                             ↓
                               持久产物目录 + audit_history
```

执行器为新增 `python -m backend.workers.metadata_runner`，不 import `backend.main`，不启动 BackgroundScheduler、不占用/复用 `scheduler_lease`，不复用网关锁。每个任务 `subprocess.Popen([sys.executable,'-m','backend.workers.metadata_audit_worker','--job-id',job_id,'--attempt-token',token], shell=False, ...)`；参数没有口令或 SQL。

生产支持边界：**单应用主机 + 多 Web worker + 一个 metadata-runner + 一个 MySQL 元数据库**。不声称跨主机共享文件锁、NFS 文件系统或多主机自动接管可用。发现第二 runner 或存储实例标识不一致时失败关闭；不要静默退化到 Web 内同步执行。

### 5.2 参数表（本版设计默认值，不是现场已有值）

| 参数 | 默认/约束 | 作用与超限行为 |
|---|---|---|
| `METADATA_MAX_CONCURRENT` | 固定 1，其他值启动拒绝 | 包含受理待启动、执行、发布、回收；不排长队 |
| `METADATA_JOB_TIMEOUT_SECONDS` | 1800，允许 300—7200 | 从 child 启动至发布的总墙钟预算；超时终止并明确 TIMEOUT，不保证任意 6000 表必在此内完成 |
| `METADATA_START_TIMEOUT_SECONDS` | 30，固定 | ACCEPTED 未被 runner 启动，记 START_TIMEOUT 后释放未启动槽位 |
| `METADATA_CHILD_RSS_LIMIT_MIB` | 8192，允许 512—32768 | runner 每秒读 child RSS，越界请求回收；采样保护非瞬时硬内存隔离承诺 |
| `METADATA_SQL_MAX_MIB` | 256，允许 16—1024 | 完整提取 SQL 的 UTF-8 字节上限；不是表数截断 |
| `METADATA_RESULTS_MAX_MIB` | 256，允许 16—1024 | 完整 results_json UTF-8 字节上限；超出 FAILED/RESULT_TOO_LARGE，不截断成成功 |
| `METADATA_ARTIFACT_MAX_MIB` | 1024，允许 64—4096 | 单任务所有产物实际总字节上限；同样明确失败 |
| 目标连接 connect/read/write timeout | 10/60/30 秒 | 仅元数据子进程的新连接；整体 deadline 优先，不改其他连接池全局值 |
| 监督周期 / runner 心跳 | 1 秒 / 2 秒 | 心跳含 PID、启动身份、版本；10 秒未更新判“执行器状态暂不可确认”，禁止新受理 |
| 进度持久化 | 每 1 秒或 50 个对象触发，最多每秒 1 次 | 收尾必须写最终计数；防每表写一次 MySQL |
| TERM 等待 / KILL 等待 | 5 秒 / 5 秒 | 超时、取消、服务停止均执行同一回收流程 |
| 浏览器受理/查询单请求 | 15 秒 / 10 秒 | 超时仅表示通信状态不确定，不能将后台任务改 FAILED |
| 浏览器轮询 | 2 秒；断网退避 2/4/8/10 秒 | 单飞轮询，隐藏页 10 秒；401/403 停止，重新登录后可恢复查询 |
| 结果分页 | 默认 50、最大 100 条 | 不向状态接口返回完整 SQL、结果或 HTML |

启动校验参数为严格正整数、上下界和路径可读写。`METADATA_ARTIFACT_MAX_MIB` 不足以容纳两个配置上限与 HTML 的极端组合时，仍按实际总量限额失败，不能承诺两个最大值同时可达。资源预算不得通过截取前 2000/5000 表绕过。

1800 秒和 8192 MiB 是有明确定义的初始保护预算，不是完成性能承诺；后续用真实容量验收决定是否需配置评审。Mr.Linsang 已定的**表类型统计 180 秒不变**，无需重新向其询问。

### 5.3 元数据提取行为

保留目前 `information_schema.TABLES WHERE TABLE_SCHEMA=%s` 枚举及逐对象 `SHOW CREATE TABLE/VIEW` 方式，不新增 TDSQL 私有查询语法，不根据物理子表名字私自减表，不把表类型统计的逻辑表排除算法套入本模块。SQL 只作文本审核，不执行附件建表语句。

枚举值用参数；库名/对象名作 SQL 标识符时统一函数如下，拒绝 NUL。不得把字符串转义或 `%s` 当标识符转义。输出 SQL 注释中的实例/对象名需去除 CR/LF 注入；HTML 必须转义。

```python
def quote_identifier(value: str) -> str:
    if not value or "\x00" in value:
        raise ValueError("invalid identifier")
    return "`" + value.replace("`", "``") + "`"
```

受理冻结 `database` 与 `database_name` 的现有优先级（优先 database）；留空使用连接保存的默认库，并把**最终实际库名**写入所有报告上下文。scope 集合仅允许 TABLE/INDEX/VIEW/SHARDKEY；全部未选返回 422，不误当默认全选；缺省才使用既有默认值。当前 INDEX/SHARDKEY 不独立移除 SHOW CREATE TABLE 中的条款，本版不暗改这种提取语义；页面说明其随完整表 DDL 一并提取，仅 INDEX/SHARDKEY 而无 TABLE/VIEW 的组合提示无法形成可审核对象。

一次保存枚举 manifest（名称、类型、原始顺序），统计 `enumerated_objects`、按 TABLE/VIEW 选择后的 `selected_objects`；**保持本次枚举原顺序**，不能为性能修复另行排序而改变 R035 首个来源。逐对象 DDL 追加临时 SQL 文件，成功后 `extracted_objects +1`；不存在名称/未知对象类型、权限失败、空 DDL、读取超时一律记录定位并终止，不再 warning 后继续生成“全库完成”。

`selected_objects=0` 时终态 FAILED/NO_AUDITABLE_OBJECTS；不创建空白成功报告。提取完成才进入审核阶段，成功条件 `extracted_objects == selected_objects`。保存 UTF-8 字节数、SHA-256、开始结束时间。计数中的“对象”和 `total_sql` 的“拆句数”不是同一单位，分别展示。

不宣称 SHOW CREATE 多次读取是数据库级一致性快照。报告标注“按扫描期间逐对象读取的结构”；遇到并发删表导致读取失败按失败处理，不重试后悄悄改换一套结构。目标事务不得长时间锁业务元数据以追求全库原子快照。

TDSQL 特有 shardkey/分区条款原样保留，仍由现有 parser 处理；本版没有新增 TDSQL 语法或正则。[TDSQL 官方建表说明](https://cloud.tencent.com/document/product/557/8767)、[二级分区说明](https://cloud.tencent.com/document/product/557/58907)、[MySQL 标识符转义](https://dev.mysql.com/doc/refman/8.0/en/identifiers.html)

## 6. 数据模型、幂等与状态机

### 6.1 迁移范围

新增 `backend/schema/v15/150_metadata_audit_jobs.sql`；现有最新为 v14/142，**不修改已发布迁移文件/checksum**。使用 InnoDB、utf8mb4；ID/hash/token 用 ascii_bin 比较。下表是必须实现的字段，不允许把任务状态只放在进程内字典。

| 表/字段 | 类型和约束 | 说明 |
|---|---|---|
| `metadata_audit_jobs.id` | CHAR(32) PK | 服务端 UUID hex；所有文件名由它派生 |
| `created_by` / `request_id` | VARCHAR(128) NOT NULL / VARCHAR(64) NOT NULL | 已认证服务端身份；前端不得覆盖 |
| `idempotency_key` / `request_hash` | CHAR(32) NOT NULL / CHAR(64) NOT NULL | UNIQUE(created_by,idempotency_key)；hash 仅客户端有效请求规范化内容 |
| `connection_id` / `db_name` | VARCHAR(128) / VARCHAR(128)，NOT NULL | 实际目标，不能由文件名反推 |
| `request_json` / `execution_context_json` | MEDIUMTEXT NOT NULL | 前者规范请求；后者冻结规则尺度、实例口径、报告来源及执行版本，合计上限 1 MiB |
| `connection_fingerprint` | CHAR(64) NOT NULL | 非口令连接配置及保存的密文凭据共同哈希，仅用来检测受理后变化；不保存明文/额外密文副本 |
| `state` / `phase` | VARCHAR(24) / VARCHAR(24)，NOT NULL | 下文封闭枚举，应用层校验，DB 不用 ENUM |
| `attempt_token` / `runner_id` | CHAR(32) NULL / VARCHAR(128) NULL | 一次执行的随机 fencing token、runner 启动身份 |
| `child_pid` / `child_start_identity` | BIGINT NULL / VARCHAR(128) NULL | PID+操作系统启动身份，避免 PID 复用误杀 |
| `created_at,updated_at` | DATETIME(6) NOT NULL | UTC；前端转换本地时区 |
| `started_at,finished_at,deadline_at,heartbeat_at,progress_at` | DATETIME(6) NULL | server/runner 写；heartbeat 是监督活性，progress 是实际阶段进展，两者不能混用 |
| `progress_json` / `result_meta_json` | MEDIUMTEXT NULL，各上限 128 KiB | 计数、字节、摘要、告警、artifact hash；不含全量结果 |
| `cancel_requested_at` | DATETIME(6) NULL | 用户取消意图；不能立即等同 CANCELLED |
| `error_code,error_message` | VARCHAR(64) NULL / VARCHAR(1024) NULL | 脱敏稳定错误码、用户提示 |
| `report_id,snapshot_id` | BIGINT NULL | report_id 建 UNIQUE，MySQL 允许多行 NULL；不建立会级联破坏历史的外键 |
| `exit_code,cleanup_ok` | INT NULL / TINYINT NULL | NULL=尚未确认，不得默认 true |
| `artifact_state` | VARCHAR(16) NOT NULL DEFAULT 'NONE' | NONE/READY/MISSING/DELETED；与任务结果分开 |
| `metadata_audit_slot.id` | TINYINT PK，固定 1 | 所有 Web worker 的原子受理锁，不是网关/调度器 lease |
| `active_job_id` / `runner_id` | CHAR(32) NULL / VARCHAR(128) NULL | 唯一占用、当前服务身份 |
| `runner_heartbeat_at` / `accepting` | DATETIME(6) NULL / TINYINT NOT NULL DEFAULT 0 | 初始不接受；服务完成恢复检查后开放 |
| `storage_instance_id` / `updated_at` | CHAR(32) NULL / DATETIME(6) NOT NULL | 持久目录安装标识、最近状态 |

jobs 加索引 `(created_by,created_at,id)`、`(state,updated_at)`。迁移幂等插入 slot=1（不得覆盖原 active_job_id）。既有 `audit_history` 结构和完整 `results_json` 格式不变，job 行承担唯一发布关系，避免更改历史消费者。

DDL 使用显式 `CREATE TABLE IF NOT EXISTS`；由于现有 migrator 的强校验主要针对 ADD COLUMN，新模块启动必须额外按 `information_schema.COLUMNS/STATISTICS` 校验上述两表的类型、空值、唯一索引与固定槽位。表“存在”不代表结构正确；缺列/错索引启动失败。DDL 权限缺失、半迁移后重复启动均有验收用例。禁止在元数据 MySQL 上执行 SQLite PRAGMA/锁方案。

### 6.2 受理事务与冻结上下文

1. 完成既有认证/RBAC，验证 JSON 长度/字段/标识符/项目参数。新路由在 `_PATH_TO_MENU` 明确映射 `schema-extractor-audit`，不走未登记写接口的 fail-open。
2. 开启短 MySQL 事务，先 `SELECT ... FROM metadata_audit_slot WHERE id=1 FOR UPDATE`。统一所有代码锁顺序为 **slot → job**，避免取消/提交交叉死锁。
3. 查 `(created_by,idempotency_key)`：相同 request_hash 返回已有 job（不受忙碌检查影响，不重新取新尺度）；不同 hash 返回 409/IDEMPOTENCY_CONFLICT。客户端改变条件必须使用新 key。
4. 无已有 job 时验证 accepting=1、runner 心跳新鲜、版本/存储标识匹配、active_job_id=NULL，否则 503/EXECUTOR_UNAVAILABLE 或 409/METADATA_BUSY。忙碌不泄露其他用户的连接名/任务详情，不产生候补任务。
5. 在一致性读取上下文内获取实际连接配置、默认库、实例类型及来源/冲突提示、全局有效规则集 ID/overrides，复用现有 report_context 数据结构冻结名称。新增专用 snapshot 方法接受同一数据库连接，report_context 构造函数接受本次已读连接行和最终库名，不再次反查连接、不使用 30 秒缓存后二次读取另一规则集。采用 InnoDB REPEATABLE READ 一致性快照，冻结查询是同连接非锁定一致性读取；slot/job 准入锁仍按第 2 步。获取失败返回明确失败，不无提示降回 default。
6. context 固定 `engine_version,engine_build,rule_set_id,overrides,effective_rule_ids,instance_context,report_context,scope_flags,context_hash`。有效规则仍通过 `get_enabled_rules` 唯一过滤入口计算；没有修改规则对象全局 enabled/severity 的操作。
7. 插入 ACCEPTED，更新 slot.active_job_id，commit 后返回 job_id。事务内不连接目标 TDSQL、不提取 DDL、不启动子进程。数据库提交结果不确定时返回 request_id 并允许同 key 重试，不宣称任务未创建。

只保存连接配置 fingerprint，不拷贝口令到任务参数、日志或文件。child 启动前从现有 `tdsql_connections` 读取并验证 fingerprint，一致才在 child 内用现有解密方法连接；删除/改地址/改凭据/改影响口径的配置返回 CONNECTION_CHANGED，要求用户重新发起。名称也纳入 fingerprint，受理后改名不会导致执行目标/报告冻结名悄然不同。运行已开始后的改名不修改冻结报告；已经建立的连接不热切换。

执行前再校验创建账户仍有效且有发起权限；禁用/撤权时尚未启动任务失败 AUTHORIZATION_REVOKED。已启动任务不因原 JWT 自然过期被终止；后续每次查询/下载/取消仍走当前鉴权与权限检查。代码版本在等待启动期间改变则 FAILED/EXECUTOR_VERSION_MISMATCH，不以新引擎处理旧冻结任务。request_hash 对原请求的规范 JSON（scopes 去重并固定顺序、保留默认库请求语义）计算，不含当时名称/规则集等可变服务端状态；这样连接改名后相同 key 重放仍返回原 job，而不是制造新任务。

### 6.3 状态与阶段

```text
ACCEPTED → RUNNING → PUBLISHING → PUBLISHED → SUCCEEDED
    │          │          │                     （仅发布且回收已确认）
    └──────────┴──────────┴→ STOPPING → FAILED / CANCELLED
                                      ↘ RECOVERY_REQUIRED（未证实回收）
```

phase 为 WAITING/ENUMERATING/EXTRACTING/AUDITING/SERIALIZING/SNAPSHOTTING/PERSISTING/CLEANUP/DONE。状态用于任务生命周期，phase 用于进度，不新增“99% 等于成功”捷径。

- ACCEPTED 未启动可直接 CANCELLED 或 START_TIMEOUT；slot 同事务释放。
- RUNNING/PUBLISHING 取消、deadline、资源超限进入 STOPPING，回收完成才终态并释放；无法确认进程退出则 RECOVERY_REQUIRED，**继续占槽、拒绝新任务**。
- PUBLISHED 表示结果与 audit_history 已提交、job.report_id 已原子关联，尚待 supervisor 确认进程退出。不可重跑审核，也不接受取消成“未完成”；返回 409/RESULT_ALREADY_COMMITTED。
- PUBLISHED 后 child 正常退出，runner 验证产物与落库关系，置 SUCCEEDED。若 child 在提交后异常退出但产物完整且进程树已回收，仍可 SUCCEEDED，并附 POSTCOMMIT_EXIT_WARNING，不能抹掉已提交结果。
- SUCCEEDED 只表示“审核执行及结果保存完整”，**不表示 SQL 没有违规**。结果 summary.failed>0 时页面显示“审核完成，存在违规”，不能绿标“审核通过”。
- audit_history 报告完整时，旁路快照失败允许 SUCCEEDED + SNAPSHOT_SAVE_FAILED；不得显示可对比按钮，不伪造 snapshot_id。
- 终态不可被迟到进度、旧 attempt_token、断网恢复请求覆盖。所有 child 更新 `WHERE id=? AND attempt_token=? AND state IN (...)`，检查影响行数；未命中立即停止发布。

## 7. 执行器、发布与故障回收

### 7.1 单实例与启动恢复

runner 在持久目录取得专用 OS 排他文件锁，整个服务生命周期持有，不删除锁文件 inode；锁 I/O 错误与“被占用”分别记录。数据库 slot 管准入，OS 锁管本机执行器唯一性，两者不可互相替代。

注册/心跳/释放操作都须核对 runner_id；旧 runner 的迟到更新不能覆盖新身份。storage_instance_id 在安装时写入持久根目录的小文件并与 DB 对照，另校验本机身份；不允许换一个锁目录绕过单实例。时间戳过期只能停止受理，**不能授权另一个主机/目录抢占**；无法证明原执行器死亡时保持 RECOVERY_REQUIRED。

runner_id 包含安装标识、boot/进程启动身份和随机 UUID；child 记录操作系统进程启动身份。Linux 使用 `/proc/<pid>/stat` starttime（按最后的 `)` 后解析，不能简单 split 整行）与 boot_id；Windows 使用创建时间及进程句柄。不得只 `kill(pid,0)` 后对同 PID 盲发信号。

重启时先设置 accepting=0，确认旧服务进程树已消失，再处理旧 active_job：

| 旧状态 | 恢复动作 |
|---|---|
| ACCEPTED，未启动且版本/指纹一致 | 30 秒启动期限内可首次启动；超过期限 START_TIMEOUT，非自动重试 |
| RUNNING/PUBLISHING/STOPPING | 已证实旧 child 不存活 → FAILED/EXECUTOR_INTERRUPTED；不接着半份 SQL 当完整审核，不自动重跑 |
| PUBLISHED | 验证报告、manifest、hash 均完整且旧进程树消失 → SUCCEEDED；缺文件则 RECOVERY_REQUIRED，不展示虚假成功 |
| RECOVERY_REQUIRED | 保持阻断；恢复程序只有取得进程树退出证据、数据库与文件核对完成才收口，不靠清空字段解锁 |
| 终态但 slot 残留 | 校验属于同一 job 且无残留进程，再条件清理 slot；记录审计 |

Web 重启不停止 runner，也不取消已受理任务。数据库不可用时 runner 不能盲目另起任务；正在执行 child 尽快停止，最多等待当前 DB 操作 timeout 后回收。恢复后有证据地终结原 job，不将状态缺失当成功。元数据库控制连接独立设置 connect/read/write=5/10/10 秒，短请求数据库工作放在线程池且不超配连接；不能让 slot 查询在事件循环无限等待。

child 实际阶段进度写数据库或有界 IPC，runner 独立每 2 秒写监督心跳；CPU 正忙但暂未完成下一语句不等于子进程死亡。RUNNING 的总预算使用进程内 `time.monotonic()` 截止时刻，持久 deadline_at 用 UTC 自证；系统时间跳变不能延长在跑任务。runner 重启不继续 RUNNING 计算，因此不需要用新 monotonic 值恢复旧进程预算。

### 7.2 子进程生命周期

Linux child 用独立 session/process group；禁止它再派生不受管控的后台子进程。TERM 发给核验过身份的进程组，等 5 秒；仍存活再 KILL，等 5 秒并 wait/reap。只有退出码/进程组成员检查均满足才 `cleanup_ok=true`。记录实际动作：TERM 是否发出、TERM 后是否退出、KILL 是否需要、最终 exit_code/signal，不伪造 `EXITED_AFTER_TERM=true`。

Windows 本机验证使用 kill-on-close Job Object，创建/绑定失败即任务启动失败，不照搬“Job Object 失败仍继续”的 best-effort 分支。没有 POSIX TERM 等价证据时 `exited_after_term=null`，记录平台专用退出结果。实现可参考 `gateway_process.py` 的 stdlib 管理模式，但元数据进程管理独立，不能未经回归改网关公共行为。

生产 systemd 新服务 `KillMode=control-group`、`KillSignal=SIGTERM`、`SendSIGKILL=yes`、`TimeoutStopSec=20`。父监督进程异常退出也必须由服务管理器清理剩余 child，然后再启动新 runner。超时流程按进程组终止且 wait 回收，不能只捕获 `TimeoutExpired` 就释放并发槽。[Python 3.11 subprocess 文档](https://docs.python.org/3.11/library/subprocess.html#subprocess.Popen.communicate)、[systemd 官方 kill 配置源码](https://github.com/systemd/systemd/blob/main/man/systemd.kill.xml)

stdout/stderr 仅允许结构化进度或有界脱敏日志：单行最多 8 KiB、保留尾部最多 64 KiB；并发持续排空，避免 PIPE 堵塞；DDL、密码、JWT 不写 stdout。父进程只读小体积心跳/状态，不反序列化全部审核结果。

### 7.3 产物与原子发布

持久目录为 `${REPORT_OUTPUT_DIR}/metadata-audit/<job_id>/`；REPORT_OUTPUT_DIR 必须在 release/current 外，Web 和 runner 同一用户可读，目录 0700、文件 0600；不用 `/tmp` 跨服务共享（两个 PrivateTmp 不保证可互见），不把目录挂静态站点。

产物清单：`schema.sql`（完整实际提取 SQL）、`results.json`（兼容 audit_history 的完整 JSON 数组）、`results.ndjson`（完整逐语句 JSON）、`results.offsets`（整数偏移/长度索引）、`previews.ndjson`/`previews.offsets`（已限长的页面条目及索引）、`statements.sql`/`statements.offsets`（逐条原文拼接及字节索引）、`report.html`、`manifest.json`。manifest 含版本、job/context hash、各文件字节/hash、对象/语句/违规计数，不含密码。所有文件先写 `.part`，校验、flush/fsync 后原子 rename；manifest 最后写入作为核心制备完成标志，Linux 对父目录也 fsync。索引偏移以 UTF-8 **字节**计算，不用字符下标。产物多副本均计入总量上限，不能只统计 schema.sql。

在线 child 用迭代审核写结果文件、累加 summary；原始 SQL 不塞进控制台或进度响应。`results.json` 存库前最多加载一次；不得在 API/runner 又转换同样的全量 AuditResult。v1.6.3.5 在线 HTML 在 child 制备，抽取现有导出函数的纯渲染部分复用，不能删掉旧报告字段/实例连接名称。HTML 显示 job_id 和扫描口径，不依赖提交后才分配的 report_id 才能制备。

保持 MySQL `audit_history.results_json` 的原格式，既有 v1.6.3.4 历史仍可读。**必须检测实际 INSERT 编码后的包大小**：专用严格 repository 用原生 PyMySQL 游标 `mogrify` 获取最终语句，按连接字符集算字节（包括转义膨胀），与同一连接 `@@max_allowed_packet` 比较并留 64 KiB 余量；超限 PERSIST_PAYLOAD_TOO_LARGE，提示需要评估元数据库容量，禁止自动 SET GLOBAL 或截断结果。实际执行仍捕获包错误和连接断开，不能认为预检消除了所有写入故障。该阶段的线性内存也纳入 RSS 保护与容量测试。

发布仅在 child 内执行一个短事务：

1. 再检查磁盘产物、累计计数、全部提取与审核完成；准备完整 history 参数。文件/计算均在事务外完成，不能持有 slot 行锁做解析/渲染。
2. `slot FOR UPDATE → job FOR UPDATE`，核对 attempt、无取消、期限未到、状态为 PUBLISHING；若已 PUBLISHED 返回已有 report_id，不重复 INSERT。
3. 使用新增 `metadata_audit_repository.publish()` 严格保存 audit_history；返回有效 report_id。**不调用吞错返回 None 的旧 `_save_audit_history`**，不改变其他调用者的默认容错契约。
4. 对比快照计算在事务外完成；现有快照服务增加“调用者提供 conn、由调用者 commit”的内部入口，默认入口行为不变。插入快照前建 savepoint；仅可恢复的快照语句错误回滚到 savepoint 并加 warning；连接丢失/事务失效必须整笔失败，不能假装旁路已隔离。
5. 更新 job 为 PUBLISHED，写 report_id/snapshot_id、摘要、artifact_state=READY，commit。commit 结果不确定时重新按 job_id 查，禁止无条件重做 INSERT。
6. 若为旧同步客户端，child 在同一进程内做 §8.2 的兼容包装收尾，然后退出；新 API 任务直接退出。runner 等进程树退出、验证 PUBLISHED 后，事务置 SUCCEEDED 并释放 slot。PUBLISHED 后总 deadline 只用于促使回收，不能撤销已完整提交报告。

发布事务最长数据库操作由连接 timeout 控制；超时不能抢先释放槽位。取消与提交竞争由上述相同行锁顺序串行：先落取消则无发布；先 PUBLISHED 则取消返回“结果已提交”。

快照沿用模块 `schema_audit`、`biz_ref_id=str(report_id)` 与原始报告同一个 report_context/rule_set/instance_context。提取器新增逐问题迭代入口，保持原 key/fingerprint/序号逻辑。先按 key 去重并计 unique_total；使用最多 20,000 完整问题的 top-K，排序键为 `(既有severity优先级,首次出现序号)`。unique_total≤20,000 时按原始出现顺序落库；超限时按该排序键落库，truncated_count=unique_total−20,000。seen 指纹集合仍为 O(问题数)，不是常数空间，纳入 RSS 测量；不再保留所有完整 Issue 对象。调用者事务入口接收已制备 payload，不再次把截断后集合误算成“未截断”。被归档对比引用的快照保留策略不变。

### 7.4 下载、缺文件与清理

新 job 的 SQL/HTML 下载通过鉴权后 FileResponse/流式文件响应，禁止把整份报告再次 JSON 编码。结果页只读取 previews 的所需 offset 和最多 100 条；条目由 child 预制 `sql_preview`（前 4096 字符、标记截断），保留完整违规数组。若本页编码后超过 1 MiB，缩小返回条数；单条违规信息仍超限，child 预制该条定位、各级违规数和详情下载链接，并标记 `detail_external=true`，不静默丢弃违规。Web 不先解析超大 results.ndjson 行再截预览。完整单条 SQL/JSON 通过 statements/results 的 offset 有界流式交付，检查 offset+length≤对应文件大小且 statement_index<total。

新增 `GET /metadata-jobs/{id}/sql-preview` 固定读取 schema.sql 前 64 KiB，按 UTF-8 完整字符边界解码并返回 truncated/total_bytes；不能由浏览器先下载整个文件才截取预览。索引每条用固定宽度无符号 64 位 offset/length（小端，头部有 magic/version/count），Web 可 seek 指定条目，不加载全部索引；拒绝损坏的大小/版本或越界偏移。

`/audit/report/{id}/html`、`/sql` 的**新 job 报告分支**先查 job 映射/文件，直接交付制备产物；旧报告无 job 映射时原读取兼容路径不变。新 SQL 文件下载返回完整提取文件，不再从结果片段重建；旧历史缺原始文件时保留原下载行为并不冒充原始 DDL 文件。`/export` PDF 仅增加新 job 所有权校验，其生成路径保持既有行为、另做兼容回归，不在状态轮询中调用；本版不把其宣称为已完成独立进程隔离的导出器。

文件损坏/被删：返回 410/ARTIFACT_MISSING 或 500/ARTIFACT_CORRUPT，UI 显示“审核已完成，产物当前不可用”；不能重跑目标库或生成空白通过报告。完整结果仍在 audit_history，允许运维离线恢复导出；本版不新增自动重建任务平台。历史记录被显式删除后 job 置 artifact_state=DELETED、report_id/snapshot_id 查询先校验存在性；不能把文件又作为被删历史提供给用户。

清理只针对已验证位于持久 metadata-audit 根下的 `<32位hex job_id>` 目录，拒绝 symlink/junction/越界；运行中、STOPPING、RECOVERY_REQUIRED 不清理。失败/取消任务产物保留 24 小时诊断后清理，任务脱敏行保留 30 天；成功产物**及其 job 映射行**跟随原审核历史保留/手工删除策略，不能提前删 job 使所有权检查退回旧报告分支，也不能固定 24 小时删除成功下载。历史删除后保留 job 墓碑至少 30 天，返回已删除而不重新创建；普通失败/取消 key 的幂等保证期为 30 天，过期删除后不承诺无限期防重。新增一条独立 metadata 维护程序由 runner 空闲时运行，不改现有 scheduler lease。

手工/保留策略删除 history 时先校验关联 job 非活动并在同一事务标记 DELETED，commit 后删除产物；文件删除失败记录待清理，API 已拒绝访问。继承原 `purge_snapshots` 选项与归档引用保护。备份与回滚必须同时保留 MySQL 和 REPORT_OUTPUT_DIR，不能只备份 current 目录。

## 8. API 契约与兼容

### 8.1 新接口

资源根路径为 `/api/v1/audit/metadata-jobs`，**下表路径相对于 `/api/v1/audit`**；返回 `Cache-Control: no-store`、request_id；所有错误有稳定 code 和中文 message，不返回 traceback/凭据。job_id 是定位符，不是权限凭证。

| 方法/路径 | 输入 | 响应/限制 |
|---|---|---|
| POST `/metadata-jobs` | Idempotency-Key: 32 位 hex；JSON connection_id、database/database_name、scopes | 新建/活动重放 202；终态重放 200；Location 指向 job；响应仅概要 |
| GET `/metadata-jobs/{id}` | 无 | 200 状态、阶段、计数、摘要、error/warnings、report/snapshot ID、可用下载链接；不带全部 SQL/结果 |
| GET `/metadata-jobs` | limit=20（≤100）、offset、state 可选 | 默认本人记录；admin 可显式查全部；按 created_at,id 降序 |
| GET `/metadata-jobs/{id}/results` | offset≥0，limit 1—100 | 仅 SUCCEEDED；条目保持原字段，增加 sql_preview/detail_external；total、next_offset |
| GET `/metadata-jobs/{id}/sql` | 无 | 仅 SUCCEEDED+READY；完整 UTF-8 提取 SQL 文件 |
| GET `/metadata-jobs/{id}/sql-preview` | 无 | 固定前 64 KiB；truncated、total_bytes、preview；不加载全文 |
| GET `/metadata-jobs/{id}/results/{statement_index}/sql` | 非负整数 | 单条完整 SQL；不能解释成文件路径 |
| GET `/metadata-jobs/{id}/results/{statement_index}/detail` | 非负整数 | 流式下载单条完整 JSON（含全文 SQL 和全部违规） |
| GET `/metadata-jobs/{id}/html` | 无 | 完整 HTML 文件，冻结实例连接名称 |
| POST `/metadata-jobs/{id}/cancel` | 空 JSON | 活动任务 202/重复取消同结果；已 CANCELLED 200；PUBLISHED/SUCCEEDED 409，不删除成果 |

本版不新建特殊下载 token，不扩展全局 query-token 白名单；前端采用 Authorization fetch → Blob 下载。浏览器 SQL/HTML Blob 生命周期结束及时 `URL.revokeObjectURL`；没有把凭据写进下载 URL。

新 job 详情/结果/下载仅创建者或 admin 且满足菜单权限可访问；其他用户返回 404（不泄漏存在性）。取消还须具备既有发起审核的写权限；auditor 即使有读取权限也不能取消。历史原共享读取策略不在本版全面收紧，但新 job 对应历史下载仍须应用相同所有权检查，避免绕到 `/report/{id}/html` 越权。

状态响应示例（展示字段而非认定已有任务）：

```json
{
  "job_id": "0123456789abcdef0123456789abcdef",
  "state": "RUNNING",
  "phase": "EXTRACTING",
  "connection_name": "已冻结的实例连接名称",
  "database": "业务库",
  "elapsed_seconds": 279,
  "progress": {
    "enumerated_objects": 6300,
    "selected_objects": 6280,
    "extracted_objects": 5100,
    "total_statements": null,
    "audited_statements": 0
  },
  "report_id": null,
  "snapshot_id": null,
  "cleanup_ok": null,
  "error": null,
  "warnings": [],
  "request_id": "服务端请求关联号"
}
```

total_statements 未拆句前为 null，不用 0 或对象数代替；百分比仅展示本阶段 `done/total`，总体只显示阶段和已用时间，不预估虚假的 ETA。

### 8.2 旧接口不做隐式破坏

`POST /api/v1/audit/extract-and-audit` 保留为兼容 façade：复用同一任务受理/执行器，**不保留旧同步重计算分支**。显式 `Prefer: respond-async` 返回新任务 202；未带 Prefer 的旧客户端仍等待旧 SUCCESS 响应（字段名称、results、summary、scope_fields 不变），等待代码必须异步查询小状态，不占 Web 的同步计算线程执行审核。

旧同步等待上限为 `METADATA_JOB_TIMEOUT_SECONDS + 60`；连接断开只停止等待，不取消 job。若等待上限到但后台状态尚未收口，返回 504/JOB_STATUS_UNCERTAIN、job_id 和查询链接，不能改后台终态。提供 Idempotency-Key 的旧客户端享受同样幂等；未提供则服务端生成，明确不保证断线后再次 POST 不会形成新的完成后重跑。

旧大结果组装用 child 制备 `legacy-response.json`，Web 流式发送。受理上下文另存 transport_mode=LEGACY_WAIT（不作为规则尺度），同一 child 在 PUBLISHED commit 后、退出前生成包装：使用已提交 report_id/snapshot_id、summary/scope_fields、schema.sql 与 results.json 流式组成原 SUCCESS JSON。SQL 字符串须用正确 JSON 编码器分块转义，不手拼未转义文本。该文件计入 artifact 上限，成功写独立 `legacy-manifest.json` 和兼容产物状态；**不改动已校验的核心 manifest**。

包装收尾失败或提交后进程已退出，核心结果仍按 PUBLISHED 恢复规则收口；旧 façade 返回 503/LEGACY_RESPONSE_UNAVAILABLE（含 job_id、report_id、查询链接），不宣称目标库提取失败、不再启动重任务。新 API 不依赖兼容包装，收到同 key 的旧同步重放但原任务没有包装时也按此明确响应。实现时不得为旧接口重新将全量结果加载进 Web 内存。

旧 façade 仍可能受外部长期 HTTP 连接设备限制，不能声称“旧页面长请求永不超时”；本版前端必须更新为新短请求流程。网络失败时可凭 key/job_id 查询成果，不重复盲点按钮。新 UI+新 API 是内网容量验收主路径，旧 façade 另列兼容测试。

### 8.3 错误分类

| HTTP/任务错误 | 用户文案与行为 |
|---|---|
| 401 | 登录已过期/会话已失效；停止轮询、保留 job_id，登录同账户后恢复；不自动重发创建 |
| 403/404 | 无权限/任务不存在或不可访问；停止查询，不修改其他用户任务 |
| 409 METADATA_BUSY | 当前已有元数据审核任务，请稍后重试；不自动排队 |
| 409 IDEMPOTENCY_CONFLICT | 本次提交标识与原参数不一致，请重新发起 |
| 422 INVALID_SCOPE/INVALID_ARGUMENT | 指出具体参数，不进入执行器 |
| 503 EXECUTOR_UNAVAILABLE | 元数据执行服务未就绪；保留输入，不退回 Web 执行 |
| CONNECTION_CHANGED / AUTHORIZATION_REVOKED | 受理后目标配置或发起权限已变化；本次未执行，重新确认后发起 |
| EXTRACT_OBJECT_FAILED / DB_READ_TIMEOUT / NO_AUDITABLE_OBJECTS | 展示库、对象、阶段和已完成数；本次审核不完整，没有全库成功报告 |
| JOB_TIMEOUT / RESOURCE_LIMIT / RESULT_TOO_LARGE | 已达到任务保护预算，明确完成范围与取消/回收结果，不能提示全部通过 |
| PERSIST_PAYLOAD_TOO_LARGE / REPORT_SAVE_FAILED | 结果未完整保存；不能返回 SUCCESS+report_id=null |
| CHILD_EXITED / EXECUTOR_INTERRUPTED | 后台进程退出/服务中断，显示 job_id；不把退出码猜成 OOM |
| RECOVERY_REQUIRED | 后台回收或成果核对未完成，已阻止新任务，需要运维处理；不能显示可再次启动 |
| SNAPSHOT_SAVE_FAILED | 审核已完成但对比快照保存失败，报告仍可下载，对比按钮不可用 |
| 网络错误/ERR_EMPTY_RESPONSE | 无法确认请求结果；保持原 job/key，自动重查状态，不展示“数据库提取失败”的归因 |

## 9. FIX-03：浏览器真实操作设计

页面仍在“SQL 审核 → 在线元数据审核 → 即时元数据提取与审核”，不增加无关导航。

1. 点击前按已登录用户建立 `client_submission_key`，连同当前输入摘要存储；先保存 key 再 POST。按钮本地 single-flight，loading/disabled 覆盖双击；两个标签/两个 worker 的并发由后端 slot 最终裁决。
2. 收到 job_id 后切换状态卡，显示冻结的实例连接名称/实际库/有效规则集/实例类型、阶段、对象数、语句数、用时和取消按钮。不得随全局下拉切换把旧 job 内容改名。
3. 单次状态请求完成后才安排下一次；使用 AbortController + view generation + job_id 共同校验响应归属。切换页面/实例、退出账户、组件卸载中止本页等待并清 timer，不取消后台 job；旧响应不得覆盖当前任务。
4. 受理响应丢失：提示“提交结果待确认”，使用**原 key、原 payload**重放；不生成新 key、不提示用户反复启动。已有 job 则直接查询。
5. 刷新/重新登录后通过用户作用域存储的 job_id 或本人任务列表恢复；客户端不得在 localStorage 保存原 SQL、口令、完整结果或下载凭据。登出清理账号运行视图，同用户下次登录以服务端本人任务列表恢复，避免账号 B 看到账户 A 的任务名。
6. 轮询断网显示“状态暂不可确认，后台可能仍在执行”；401 走现有统一登录流程，只弹一次；403/404 不无限重试。单请求 AbortError 不等于 job CANCELLED。
7. SUCCEEDED 后请求第一页结果，默认 50 条，按 statement_index 稳定 key；切页不加载全量 `results`。全文 SQL 默认只读预览前 64 KiB并标注“预览”，完整文件走下载；不能把 6000 条折叠组件一次性渲染。
8. 取消弹确认说明“会停止本次后台任务，尚未发布的结果不会作为完整报告”；提交后显示“取消及资源回收中”，后台确认才 CANCELLED。完成与取消竞争按服务端结果展示。
9. 新增本人最近任务小列表或状态卡“查看最近任务”入口，区分运行、失败和成功历史；失败任务不混入原“历史元数据审核记录”的完整结果列表。成功后刷新原历史，行数只增加一次，沿用既有跨页选中与对比实现。
10. 有违规仍显示完成摘要和完整问题，不把作业 SUCCEEDED 翻译为“SQL 审核通过”。快照失败、缺产物分别提示，不用一个成功绿色条遮住警告。

## 10. 施工文件与实施顺序

以下是 **Q 后续应修改的文件清单**，不是本次已修改清单。

| 顺序 | 文件/新增模块 | 具体责任 |
|---|---|---|
| D01 | 新 `backend/engine/r035_context.py`；改 `checker.py` | 见证索引、逐句迭代、移除平方级生产构造；R035 类保持契约 |
| D02 | 新 `backend/schema/v15/150_metadata_audit_jobs.sql`；新任务 schema 校验 | 两张表、索引、槽位；严格校验与重跑幂等；不改旧迁移 |
| D03 | 新 `backend/services/metadata_audit_repository.py` | slot→job 事务顺序、幂等、冻结尺度、CAS、严格原子发布 |
| D03a | `ruleset_service.py`、`report_context.py`、`instance_type_service.py` | 新增接受同一 conn/已读行的冻结构造入口；旧入口默认行为不变 |
| D04 | 新 `backend/services/metadata_audit_pipeline.py`；`audit_service.py` | 从旧路由抽取只读提取与执行服务；接收冻结上下文、禁止二次尺度解析；原公共调用保持兼容 |
| D05 | 新 `backend/workers/{metadata_runner,metadata_audit_worker}.py`、包初始化 | 独立服务、子进程入口、恢复、状态/进度/回收；不得 import Web app |
| D06 | 新 `backend/services/{metadata_job_process,metadata_artifacts}.py` | OS 身份/锁/进程树管理，预算、产物 hash、分页索引、限额和安全清理 |
| D07 | 新 `backend/api/metadata_audit.py`；`main.py`、`auth_service.py` | 新 API 注册、菜单映射、认证/所有权、只读 readiness；Web 不自动启动 runner |
| D08 | `backend/api/sql_audit.py` | 旧 façade、历史报告新产物分支、历史删除联动；重逻辑离开路由 |
| D09 | `scan_snapshot_service.py`、`snapshot_extractors/schema_audit.py` | 调用者事务入口、有界快照问题保留；旧调用/归档保护不变 |
| D10 | `frontend/static/js/app.js`、`frontend/index.html` | 新任务卡/短轮询/恢复/分页/错误归因/取消；保持已有菜单和对比选择 |
| D11 | `deploy/tdsql-metadata-runner.service`（新）、env.template、install/upgrade_incremental/apply_patch/rollback/verify_deploy | 双服务交付、持久目录、恢复/停止顺序、版本与运行检查 |
| D12 | make_release.sh/.ps1、make_patch.sh/.py、部署 README、VERSION 和版本展示来源 | 打包新包/迁移/unit，无漏文件；实现阶段统一产品版本 v1.6.3.5 |
| D13 | 新 `tests/test_v1635_*` + 浏览器 UAT 证据 | 见下节；测试依赖不进入生产 requirements/offline wheels |

先 D01+语义回归，再 D02—D06 进程/持久化故障测试，随后 D07—D10 浏览器联调，最后 D11—D13 全新安装/升级/回滚。不能只交 D01 并把异步/UI/部署标为“后续优化”，也不能先上后台队列而保留旧 R035 缺陷。

## 11. SIT、容量与 UAT 验收矩阵

### 11.1 规则语义与资源测试

| 用例 | 输入/操作 | 必须断言 |
|---|---|---|
| ALG-01 | a.id INT；b.id BIGINT；c.id INT | R035=[否,是,是]；第三张来源 b；完整 message、severity、line_number 与旧 oracle 一致 |
| ALG-02 | 同类型不同 VARCHAR 长度、DECIMAL 参数；大小写列名 | 不重新引入长度检查；按真实 parser 规范类型验证，不手造预期替代解析结果 |
| ALG-03 | 同表反复 CREATE、跨表混合类型、schema 限定名、空类型、重复列名 | 与完整历史 oracle 首个违规来源完全一致；不增加表名规范化 |
| ALG-04 | 多列各自冲突、同语句多个同名列 | 按当前列顺序报首个冲突；见证排序不改变 provenance |
| ALG-05 | CREATE 前后夹杂 VIEW/SELECT/ALTER、语法错误 | 只有成功 CREATE 入历史；非 CREATE 不生成全历史投影；E999 不丢失 |
| ALG-06 | MyBatis、CRLF/CR、DELIMITER、过程体、注释中的分号 | 拆句数/行号/解析调用次数与基线一致；每个逻辑语句恰好一次 parse |
| ALG-07 | R035 off、severity_override、集中/分布式两架构 | off 不创建索引；覆盖生效；架构不能被规则集越权打开；R064 不被私有 metadata 污染 |
| ALG-08 | 独立模型全部长度 0—5 历史、随机长历史，含从未见值 | ≥1,062,880 次查询 0 差异；每名≤5槽，且保存原 raw_type |
| ALG-09 | 相同 SQL 文件完整规则全开，旧/新小样本比较 | AuditResult 全字段逐项相同；只允许性能测量/内部诊断字段不同，不靠删规则使结果相同 |
| ALG-10 | 200/400/800 表，20 同名列和 20 全异名列两组 | 不保留 parsed_items/metas；AST 不随语句数累计；索引 O(U)，实测曲线无旧平方级项 |
| ALG-11 | 两个并发文件请求、请求完成后再次审核 | 各自索引隔离；无全局污染；parse 对象通过弱引用/受控 GC 验证不全批驻留 |

旧算法 oracle 仅在安全的小样本运行，不对 6000 表强行运行旧平方级实现。tracemalloc 只用于解释 Python 分配；RSS、cgroup 峰值另测，不混作同一指标。本版不要求“所有规则总 CPU 严格线性”，但必须证明 R035 不再有全历史逐次扫描。

### 11.2 任务、持久化与故障注入

| 用例 | 场景 | 判定 |
|---|---|---|
| JOB-01 | 两 Web worker、两个用户、同时 POST | 全主机最多 1 个活动任务；其余 409；不得各 worker 各跑一个 |
| JOB-02 | 同 key 同参数并发/丢失首次响应/事务提交回包丢失 | 恰好 1 个 job/1 个 history；返回同 job；同 key 改参数 409 |
| JOB-03 | runner 未启动、版本不同、心跳过期、锁权限错误 | 503/明确原因；无 Web 同步降级、无永久假 ACCEPTED |
| JOB-04 | Web worker/整个 Web 服务重启 | runner/child 继续；页面刷新可查同 job；不再发起目标提取 |
| JOB-05 | child 在提取/审核/序列化/发布前故障退出 | FAILED+阶段+exit_code；无成功历史；确认回收才释放 slot |
| JOB-06 | PUBLISHED 后 child 异常退出/runner 重启 | 成果只一份；按发布恢复规则核验后收口；不重复审核/INSERT |
| JOB-07 | 卡住 child、TERM 可退出/忽略 TERM 两分支 | 第一分支真实 EXITED_AFTER_TERM=true；第二分支真实 KILL/等待回收；回收失败继续占槽 |
| JOB-08 | PID 复用、进程组后代残留、两个 runner | 不误杀无关进程；不释放有残留的槽；第二 runner 拒绝启动 |
| JOB-09 | 内存/SQL/结果/磁盘总量/总时限各越界 | 稳定错误码、完整回收；不截表、不关规则、不写完整成功历史 |
| JOB-10 | 元数据库断开、包太小、commit 不确定、快照单独失败 | 严格区分未保存/已发布/旁路失败；无 report_id=null 的 SUCCESS；无重复结果 |
| JOB-11 | 受理后连接改名/换地址/删连接/改密码、规则集变化 | 启动前配置漂移失败；已冻结规则不漂移；已开始名称冻结；口令不进入日志 |
| JOB-12 | 取消发生在 ACCEPTED/RUNNING/commit 竞争/PUBLISHED | 状态符合 §6；“取消中”不当取消完成；已经提交不能撤销成果 |
| JOB-13 | 对象第 5999 次 SHOW CREATE 失败或返回空 DDL | 明确对象定位；不把前 5998 个伪装全库成功；未执行任何目标 DDL/DML |
| JOB-14 | .part 孤儿、manifest损坏、磁盘满、目录链接/路径穿越 | 不读任意文件、不产生空报告、不越界删文件；失败可追踪 |
| JOB-15 | 成功历史手工删除/保留清理，保留或清理快照 | 对应产物不可再绕路下载；运行中不可删；归档引用快照保护不变 |
| JOB-16 | 新建/已迁移/半迁移/错唯一索引/无 DDL 权限 | 幂等或明确失败关闭；不误判 schema ready |

### 11.3 浏览器真实点击验收

| 用例 | 人工动作 | 验收结果 |
|---|---|---|
| UI-01 | 登录→选真实实例→留空库/填写库→勾范围→点击 | 正确实际库/冻结名称，受理后显示阶段，不长期只转圈 |
| UI-02 | 双击、刷新、两个标签连续提交 | 无重复重任务；同 key 可恢复；busy 清晰且无他人敏感详情 |
| UI-03 | 在提取/审核过程中切其他模块、切实例、浏览器刷新 | 原任务不中断；旧响应不覆盖新选择；返回可恢复同 job |
| UI-04 | 制造 401、会话吊销、移除菜单/换账号、猜他人 job/report URL | 正确认证授权；不把 401 混成目标数据库错误；旧 report 路径不能绕新所有权 |
| UI-05 | 浏览器离线/恢复、受理回包丢失、单次轮询空响应 | 显示状态不确定；不重复创建；恢复后同 job 查询 |
| UI-06 | 6000+ 表结果页翻页、单条 SQL 下载、全文 SQL/HTML 下载 | 不全量渲染；完整内容、首末对象、实例名/库/计数和存库一致 |
| UI-07 | 审核完成但有 ERROR/INFO；快照失败 | 完成≠通过；规则完整；快照缺失不允许比对 |
| UI-08 | 点取消并确认、观察 TERM/KILL 后结果、再次启动 | 回收完成才可再次启动；需要 KILL 的分支不冒充 TERM 正常退出 |
| UI-09 | 两份成功结果跨页选择并对比、旧 v1.6.3.4 历史下载 | 原四模块跨页选择不回归；旧数据兼容；新结果只落一次 |

### 11.4 容量与发布硬门禁

测试分三层并分别出证据，不能用模拟 TDSQL 通过替代真实内网通过：

1. 本机模型/单元：算法等价、输入边界、状态机和故障注入。
2. 预生产应用栈：**Python 3.11.11、Uvicorn 0.52.1、sqlglot 30.14.0、PyMySQL 1.2.0、FastAPI 0.141.1** 对照前轮现场记录；实际依赖若变化另记 manifest，不假定本机 Python 3.14 与 Linux 等价。双 Web worker，Linux cgroup v1/systemd 进程回收实测。
3. Mr.Linsang 内网测试库：原失败的 **6000+ 表实际目标**，启用原规则尺度及相同范围。连续完整执行 3 次；记录每次对象清单/count/hash、DDL/结果字节数、分阶段时间、RSS 峰值、服务/worker PID、退出日志、report/snapshot ID、下载 hash。目标实例以当次保存配置为准，不混用旧材料的分布式 15063 与集中式 15064 样本。

通过门槛：

- 三次均得到完整 SUCCEEDED 作业且结果合法持久化；`selected=extracted`，所有拆句均审核，report_id 非空且恰好一条；没有无告警漏对象。业务规则命中不等于作业失败。
- 基准容量集以 6000 表×20 列，分别同名/异名列及混合类型构造；记录实际数据，不只有“6000”标签。真实库保留真实列宽/索引/视图/分区特征，不通过裁剪 DDL 过门禁。
- 新建受理请求在健康测试网络下 p95≤2 秒；运行期间状态/健康探针 p95≤2 秒、单次≤5秒（各至少 100 次）；无与该审核有关的 Web worker 重启或空响应。
- 子进程 RSS 峰值低于配置保护阈值且不触发资源错误；原 6000+ 表在默认 1800 秒内完整完成才算默认配置容量通过。预算耗尽是可解释失败，不是容量通过，也不自动要求把 180 秒改大。
- 通过延迟注入使一个合法任务持续超过 300 秒，验证跨越旧 250/本轮279秒观察点仍正常可查并完成；这是故障路径验证，不可写成真实 TDSQL 查询性能。
- 无 6000 份 ParsedSQL/AST 同时驻留，无全历史 metas，R035 见证计数≤5U；全规则语义差异为 0。
- 进程退出/取消/重启/断网/包大小/磁盘故障的定位可闭环；没有残留进程、重复报告或永久假成功。故障注入不在生产环境执行。
- 全量既有回归套件通过；上线检查、大表、慢 SQL、网关、深度诊断、鉴权、报告/快照至少完成冒烟。新/改代码按本矩阵严格测试，未改模块不扩展功能测试范围。

开发完成报告、A 的 SIT、O 的 UAT、G 的发布检查均必须列“实际执行/模拟执行/未执行”，不得在设计阶段预签通过。Mr.Linsang 无需再补本次诊断证据；上述真实容量运行是**开发完成后的验收活动**，不是继续写方案的前置条件。

## 12. 部署、回滚与运维施工

### 12.1 双服务安装

新增 unit 由 install 路径/用户模板渲染，核心字段如下（仅设计模板，不在本轮执行）：

```ini
[Unit]
Description=TDSQL SQLCheck Metadata Audit Runner
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=__USER__
Group=__USER__
WorkingDirectory=__INSTALL_DIR__/current
EnvironmentFile=__INSTALL_DIR__/.env
Environment=REPORT_OUTPUT_DIR=__INSTALL_DIR__/reports
ExecStart=__INSTALL_DIR__/current/venv/bin/python -m backend.workers.metadata_runner
Restart=on-failure
RestartSec=5
KillMode=control-group
KillSignal=SIGTERM
SendSIGKILL=yes
TimeoutStopSec=20
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=__INSTALL_DIR__
UMask=0077
StandardOutput=journal
StandardError=journal
SyslogIdentifier=tdsql-metadata-runner

[Install]
WantedBy=multi-user.target
```

unit 不设置仅新 systemd 才支持的硬资源选项来冒充麒麟 4.19 兼容性；RSS 保护由本版 runner 实现并在该环境验证。Web 保留 `--workers 2` 及既有启动参数，不调大健康检查来掩盖问题。生产只有服务管理器启动 runner，Web startup 不自建第二个。

### 12.2 全新安装/增量升级的顺序

1. 发布包包含 v15 迁移、workers Python 包、新 unit、新前端和 schema 检查；源码版本/包 VERSION/页面版本一致为 v1.6.3.5。原依赖锁定不因本设计自动升级；新实现优先标准库。
2. 升级前备份 MySQL 元数据库、旧 .env/unit、当前 release 指向与持久 reports；检查磁盘/目录权限/元数据库包大小，包过小给出预检提示，禁止自动变更服务器参数。
3. 将 metadata slot.accepting 关闭，由维护命令走 repository，而不是运维手改状态。等待活动任务完成；确需停止时走受控取消，**确认进程树回收**，无法确认则升级中止。首次从 .4 升级无 runner，但仍需等待原在线长请求结束后停 Web，不能把正在运行的旧进程带进新版本。
4. 停止旧 Web 和旧 runner（若存在），确认 runner cgroup 无子进程；在固定目标 release 安装代码/依赖并执行数据库迁移、结构校验，成功后切换 current。不要边跑旧 worker 边覆盖其代码。
5. 渲染/安装两个 unit，daemon-reload；启动 runner 并通过版本/存储/恢复校验后 accepting=1，再启动 Web。失败时整体安装/升级返回非零，不打印“升级圆满完成”。
6. `verify_deploy.sh` 增加两个服务状态、runner heartbeat/版本/单例、schema、持久目录检测；认证后提交一个小库任务，轮询到终态、下载 SQL/HTML 并核对实例名/hash；只检查 `/health` 不足以发布。
7. `apply_patch.sh` 与增量升级、make_patch 产物遵循相同双服务协议；不能只有全量安装脚本支持新 runner。
8. 执行离线 `dist/wheels_tmp` 干净安装门禁；测试/浏览器框架不进入根 `requirements.txt` 的生产依赖。没有新增第三方依赖不等于免掉离线发布验证。

### 12.3 回滚

回滚先关闭受理，受控结束活动任务并确认整个 runner 进程树退出，再 stop/disable 新 runner，切回旧 release 后启动旧 Web。v15 两张表作为加法迁移**保留不删**，audit_history 仍是旧 JSON 格式，旧版本可读已保存审核历史；新原始 SQL 产物保留供重新升级恢复。

回滚到 v1.6.3.4 会重新暴露大库长请求和 R035 平方级缺陷，必须明确暂停此类大库在线审核，不能把回滚当成已经修复。其他已验收模块可按原发布流程运行。禁止为回滚 DROP 新表、删除全部 reports、手工清空 active_job_id 或复活未回收 child。

### 12.4 最小日志字段与一键诊断

每次日志固定含 `request_id,job_id,attempt_token,runner_id,child_pid,engine_build,phase,state,elapsed_ms,selected/extracted/audited,sql_bytes,result_bytes,rss_peak_bytes,report_id,snapshot_id,error_code,exit_code,term_sent,exited_after_term,kill_sent,cleanup_ok`；口令/JWT/完整 SQL 不入日志。开始和阶段切换立即记录，不能只有 HTTP 完成后才有一条请求日志。

新增只读诊断命令汇集两个 service 的 PID/启动身份、job 状态、对应时间窗日志、产物 manifest/hash 与当前 cgroup 控制器类型。先判 cgroup v1/v2 再读对应计数，不以不存在的 memory.events 路径中止整个诊断。诊断不得发送信号/重启服务/清理文件。

## 13. 设计交付检查与责任门禁

本轮完成：读取新增 console、核对 279 秒计时、归并既有证据边界、复核基线源码和部署结构、查询相关官方文档、运行独立见证模型、形成本文。**本轮未实施修复，也未执行新版 SIT/UAT、内网容量测试、生产迁移或部署。**

| 门禁 | 后续责任输出 | 不能替代的事实 |
|---|---|---|
| 设计评审 | A 对算法等价、状态/事务/进程恢复、兼容/部署逐项审计；Q 确认可施工 | 本文有模型证明不等于已经实现正确 |
| 开发与 SIT | Q 提交实现/自测/包清单；A 按 ALG/JOB/UI 与全回归出 SIT 证据 | 不用关 R035 或减少目标表数使测试通过 |
| UAT | O 真实浏览器操作、故障恢复、历史/下载/对比核对；内网执行方记录真实容量 | 不能把模拟目标库或本机模型写成内网实测 |
| 发布 | G 核对双服务、离线安装、升级/回滚；Mr.Linsang 作最终发布决定 | 旧版本门禁签字不自动覆盖 v1.6.3.5 |

设计认可的修复方向：Q/A 提出的消除批内全历史复制、隔离长任务、增加阶段证据。未采纳或不直接照搬的内容：只存第一张表、未经测量宣称全流程线性、认定 250 秒固定超时、把其他接口 401 当审核接口空响应根因、无证据认定 OOM、单纯提高 watchdog/HTTP 等待。原因和替代做法已分别落实在 §2、§4、§5—8。

## 附录 A：官方资料核对清单

本版不新增 TDSQL 语法正则；官方材料用于连接/标识符、安全隔离和证据含义核对，不作为未取证现场的“根因证明”。查阅日期 2026-09-09。

| 资料 | 本文采用的有限结论 |
|---|---|
| [Chromium net_error_list.h](https://chromium.googlesource.com/chromium/src/+/main/net/base/net_error_list.h) | 空响应区别于 HTTP 401；不定位哪个设备/进程关闭连接 |
| [Uvicorn Settings](https://uvicorn.dev/settings/) | worker healthcheck 与 HTTP keep-alive 不同，不是本请求总耗时配置 |
| [FastAPI Background Tasks](https://fastapi.tiangolo.com/tutorial/background-tasks/#caveat) | 进程内后台任务不提供独立重计算进程的资源/生命周期隔离 |
| [Python 3.11 subprocess](https://docs.python.org/3.11/library/subprocess.html) | timeout 后需显式终止并回收、避免 PIPE 堵塞；POSIX process group 与 Windows 控制不同 |
| [systemd kill 官方源码](https://github.com/systemd/systemd/blob/main/man/systemd.kill.xml) | 受控停止进程树；具体麒麟安装版本支持情况必须部署验收 |
| [MySQL 标识符](https://dev.mysql.com/doc/refman/8.0/en/identifiers.html) | 反引号包围的标识符内反引号重复转义；值与标识符不同 |
| [MySQL Packet Too Large](https://dev.mysql.com/doc/refman/8.0/en/packet-too-large.html) | 大结果持久化仍受客户端/服务端数据包约束，不因字段为 LONGTEXT 即无限制 |
| [TDSQL 建表](https://cloud.tencent.com/document/product/557/8767)、[二级分区](https://cloud.tencent.com/document/product/557/58907) | 保留完整 shardkey/分区 DDL，沿用已验收语法识别，不裁剪此类条款减负 |

## 附录 B：见证索引可复现模型（只验证算法，不调用目标数据库）

以下为文档内的设计验证模型。`r=(table,type,历史序号)`，生产实现必须保留 §4 的原字段、语句/列序号和原始类型；不能用整数模型替代真实 parser 测试。该模型不创建文件、不修改代码、不启动数据库或子进程服务。

```python
from itertools import product


class Summary:
    def __init__(self):
        self.anchor = None
        self.not_table = []
        self.not_type = []

    def add(self, ref):
        if self.anchor is None:
            self.anchor = ref
            return
        anchor = self.anchor
        if (ref[0] != anchor[0] and len(self.not_table) < 2
                and all(item[1] != ref[1] for item in self.not_table)):
            self.not_table.append(ref)
        if (ref[1] != anchor[1] and len(self.not_type) < 2
                and all(item[0] != ref[0] for item in self.not_type)):
            self.not_type.append(ref)

    def refs(self):
        groups = ([self.anchor] if self.anchor else [])
        groups += self.not_table + self.not_type
        return sorted({r[2]: r for r in groups}.values(), key=lambda r: r[2])


queries = list(product(range(4), repeat=2))
domain = list(product(range(3), repeat=2))
histories = checks = peak = 0
for n in range(6):
    for sequence in product(domain, repeat=n):
        history = [(table, kind, i) for i, (table, kind) in enumerate(sequence)]
        summary = Summary()
        for ref in history:
            summary.add(ref)
        refs = summary.refs()
        peak = max(peak, len(refs))
        histories += 1
        for table, kind in queries:
            expected = next((r for r in history
                             if r[0] != table and r[1] != kind), None)
            actual = next((r for r in refs
                           if r[0] != table and r[1] != kind), None)
            assert actual == expected, (history, refs, table, kind)
            checks += 1
assert histories == 66430 and checks == 1062880
print({"histories": histories, "queries": checks,
       "max_unique_witnesses": peak, "mismatches": 0})
```

预期且本轮实际观测输出：

```text
{'histories': 66430, 'queries': 1062880, 'max_unique_witnesses': 4, 'mismatches': 0}
```
