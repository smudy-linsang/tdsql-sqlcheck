# 6000+ 表在线元数据审核失败：O 独立排查与证据边界报告

| 项目 | 内容 |
|---|---|
| 提交对象 | Mr.Linsang；供 G、Q、A 与内网责任方交叉复核 |
| 日期 | 2026-09-08 |
| 排查基线 | `main@b52e324f37f38f498916775900e41f8fbd7263b0`，产品代码 v1.6.3.4 |
| 历史对照 | v1.6.3.0：`887def684ba6f8e302b6abba8f38a8e351326aad`；引入变更：`c0e5e25d748c7443d87c95f4bfb897d593abd753` |
| 执行范围 | 阅读附件及四份报告、核查代码与版本差异、本机有界合成实验、官方资料核对；只新增/修订诊断文档 |
| 未执行 | 未连接内网目标库，未运行真实 6000+ 表全链路，未调整规则、进程、数据库、部署配置或业务代码 |
| 报告性质 | 故障诊断与整改建议；不是修复完成证明、UAT 通过结论或生产变更指令 |

**2026-09-08 补证更新：** Mr.Linsang 提供的 `wde.txt` 已核对。当前内网 checker 文件哈希与本报告基线一致，实际 Uvicorn 为 0.52.1，主机采用 cgroup v1。§13 给出新增事实、归因优先级调整及适配 v1 的只读检查；§1—12 保留首轮结论形成时的证据边界，涉及“内网版本/控制器待确认”的事项以 §13 为准。

**同日第二次补证更新（最新，以 §14 为准）：** `wd2e.txt` 已取得 v1 服务与祖先组数据。服务历史高水位约 **3.622 GiB**，三层 memory/memsw 限额失败及 OOM-kill 计数均为 **0**，未发现有限的小内存配额；标准启动路径下健康检查窗口可确定为 **5 秒**。OOM 已不应作为首要事故归因，Uvicorn 判失活后主动结束 worker 是当前首要待验证机制；基础静态检查到此收口，不再要求重复执行 §10.2/§13.3。

## 1. 先给结论

**已确认的产品缺陷是：v1.6.3.2 为 R035 新增的批内上下文构造，会为每条语句保留一份此前全部字段索引的快照，产生平方级引用复制；同时，全部语句的解析结果及 AST 被保留到审核结束。v1.6.3.4 仍保留这一实现。** 这足以解释版本升级后大批量审核资源需求显著升高，是本次应优先整改的代码问题。

**尚不能直接确认的是：内网这次失败一定由 Linux OOM-killer 杀进程造成，更不能认定代码设置了 250 秒请求超时。** 服务记录确认故障附近发生 worker 退出与替换，但没有请求级阶段日志、退出信号来源、RSS 峰值或浏览器网络记录，将该 worker 与截图请求完全关联也还差一环。

本报告相对既有报告的主要增量：

1. **补充 Uvicorn 健康检查杀进程分支。** `Child process [...] died` 不只出现在进程自行崩溃后；父进程也可能因子进程不响应健康探测，先强制结束它，再打印同样日志。该分支可以没有 OOM 内核日志，必须与 OOM 并列核查。
2. **给出内存下界，而不是把小样本拟合当成内网实测。** 若 6000 条均为成功解析的 CREATE TABLE、每表 20 个入索引字段，仅快照列表中的指针就至少占 **2,879,520,000 字节，即约 2.682 GiB**，还不含 AST、列表/字典头、字段记录及结果。不是断言真实库每表有 20 列。
3. **验证一项拟议修复会漏报。** G/Q 提出的“同名字段只保存第一条历史记录”不等价于当前 R035。三表类型依次 `INT → BIGINT → INT` 时，第三表应与第二表冲突，first-seen-only 会漏掉。
4. **指出材料关联不一致。** 截图是“总账系统-分布式-开发环境”；本次记录注册的是 `15063/sungl_busi`；早先报告写的是“集中式、15064/sungl_am”。不得未经核实合并为同一次请求。

建议顺序：**先保真修复平方级快照；同时补齐一次内网请求的进程退出证据；不要先把所有超时改成 900 秒，也不要以关闭 R035 或限制 2000 表代替恢复原有大库能力。**

## 2. 材料与证据分级

### 2.1 已检查材料

| 编号 | 材料 | 用途与限制 |
|---|---|---|
| E1 | `C:\Users\linsa\OneDrive\Desktop\拉取元数据失败截图.PNG` | 页面显示 `提取失败: Failed to fetch`，地址 `10.243.16.252:8000`；未含 Network 面板与精确开始时间 |
| E2 | `C:\Users\linsa\OneDrive\Desktop\内网测试环境排查输出.txt` | 本次失败后的 systemd、应用日志及内核检查记录；不是失败前后的连续内存监控 |
| R-IN | `C:\TDSQL_SQLCHECK\TDSQL-SQLCheck\docs\tdsql_metadata_extract_failure_analysis.md` | 内网早期分析，含 Nginx 与元数据提取建议；报告日期写 2025-09-07，不能直接替代本次 2026-09-08 事件时间 |
| R-G | `C:\TDSQL_SQLCHECK\TDSQL-SQLCheck\docs\REPORT-6000表元数据审核失败根因深度排查与解决方案报告.md` | G 的代码归因及整改建议 |
| R-Q | `C:\TDSQL_SQLCHECK\TDSQL-SQLCheck\docs\REPORT-6000表元数据审核失败-Q独立排查结论.md` | Q 的独立代码分析 |
| R-A | `C:\TDSQL_SQLCHECK\TDSQL-SQLCheck\docs\DIAG-6000表元数据审核失败-A独立排查结论与量化验证-ClaudeA.md` | A 的合成负载数据、外推模型及增量索引原型 |

附件 SHA-256，便于后续确认讨论的是同一份证据：

```text
E1 EE81E39A6FD1C11136DE7D6FD59EE6E4EC230D86C4CAA2007B9BF360D1A541A1
E2 E02D30462471B0840E8F9F9D1B4CCFC0A2C25C7960C2845B90E6D8EF73C7D23C
```

本报告将“代码直接确认”“本机合成验证”“内网日志事实”“待验证假设”分别标明。引用其他报告的测试数字不代表 O 重新执行或确认其可复现性。

### 2.2 E2 能确认的时间线

| 时间 | E2 行号 | 事实 | 不可据此推出的结论 |
|---|---|---|---|
| 15:49:42 | 7—15 | 主进程 846885 启动，命令含 `--workers 2 --no-access-log` | 不能用 service 一直 active 推断 worker 没有重启 |
| 18:06:00.717 | 83 | worker 850881 注册 `10.243.20.13:15063/sungl_busi` 连接 | 连接注册不是专门的 HTTP 请求开始日志 |
| 18:06—18:09 | 84—97 | worker 850881 的后台定时任务仍有成功日志 | 不能区分主请求在拉 DDL、解析还是构造索引；BackgroundScheduler 用后台线程 |
| 18:10:09 | 100—101 | 父进程等待 worker 850881，并输出 `Child process [850881] died` | 日志没有说明死亡信号，也没有说明是谁发出信号 |
| 18:10:10 | 108—122 | 新 worker 870996 启动，应用显示 V1.6.3.4 | 启动成功不能证明此前请求成功 |
| 失败后检查 | 10 | service 显示 `Memory: 150.0M` | 这是替换后的服务内存读数，不是已死亡 worker 的峰值，也不宜称为新 worker 单进程 RSS |

连接注册至退出日志约 248—249 秒，与 Mr.Linsang 观察的约 250 秒接近，但缺少同一 request_id 的起止时间，**只能作为关联线索，不能当作已精确测得的 HTTP 耗时**。

E2 的 OOM/segfault 检索无命中；记录中未看到相应内核死亡证据。第 542 行启动内存信息分母为 `67108304K`，约 64 GiB；它不是失败时的可用内存，也不是服务 cgroup 配额。不能继续把“这台机器只有 4/8 GB，所以必然 OOM”当作前提；也不能反过来断言“主机大，绝不可能 OOM”。需补实时可用量、服务及上级 cgroup 限额。

## 3. 实际调用链与报错含义

### 3.1 用户点击后，不只是拉取元数据

```text
浏览器 POST /api/v1/audit/extract-and-audit
  → 解析所选连接及默认库、冻结报告来源
  → information_schema.TABLES 枚举对象
  → 串行 SHOW CREATE TABLE / SHOW CREATE VIEW
  → 拼接完整 SQL 文本
  → audit_file_content → audit_file
      → 拆句 → 全量解析并保留 AST
      → 为每条语句复制历史字段索引
      → 执行审核规则并生成结果
  → 保存审核历史 → 生成对比快照
  → 序列化完整 SQL 和 results → 一次性响应
  → 浏览器读取 JSON
```

接口名虽然包含“拉取”，前端同一个 catch 覆盖了提取、审核、持久化后响应及 JSON 接收阶段。因此，**看到“提取失败”不能证明故障一定发生在 SHOW CREATE TABLE**。

关键位置，均以本报告基线为准：

| 文件 | 行号/函数 | 核查结果 |
|---|---|---|
| `C:\TDSQL_SQLCHECK\TDSQL-SQLCheck\frontend\static\js\app.js` | 30—48；1707—1726 | `apiFetch` 直接 fetch；本次调用未传超时或 AbortSignal；catch 拼接“提取失败” |
| `C:\TDSQL_SQLCHECK\TDSQL-SQLCheck\backend\api\sql_audit.py` | 255—425，`extract_and_audit` | async 路由中执行同步数据库/审核工作；无本接口 250 秒计时器；成功前不输出进度 |
| `C:\TDSQL_SQLCHECK\TDSQL-SQLCheck\backend\services\audit_service.py` | 287—329 | 解析实际生效规则集和实例类型，然后调用 checker |
| `C:\TDSQL_SQLCHECK\TDSQL-SQLCheck\backend\engine\checker.py` | 298—314；325—358 | 全量解析；逐语句复制索引；随后才开始规则循环 |
| `C:\TDSQL_SQLCHECK\TDSQL-SQLCheck\backend\engine\rules\ddl.py` | 547—589 | R035 默认启用，遍历同名字段全部历史引用，发现首个不同表类型冲突即报 |
| `C:\TDSQL_SQLCHECK\TDSQL-SQLCheck\backend\engine\parser\parser_legacy.py` | 4023；4127；4162 | ParsedSQL 确有 ast 字段且赋值，因此保留解析结果也会保留 AST |
| `C:\TDSQL_SQLCHECK\TDSQL-SQLCheck\backend\services\scheduler.py` | 19；306—322 | 后台线程调度，不是业务阶段探针 |

### 3.2 什么情况下会出现这个提示

| 情况 | 当前前端通常如何表现 | 与这次事件的关系 |
|---|---|---|
| 请求所在线程/worker 被结束，响应无法完成；连接被网络设备断开 | fetch 或读取响应体失败，进入外层 catch；可能出现该提示 | 与退出日志相符，是优先核查方向；未抓包，不能写已捕获 TCP RST |
| CORS、安全策略、连接失败或其他浏览器级网络错误 | fetch 拒绝，可能出现同类提示 | 提示本身无法排除；已等待很久且同时出现 worker 替换，让这些因素优先级相对靠后 |
| 后端正常返回 HTTP 400/500/504，响应可读 | 进入 `resp.ok` 的 else，显示 detail 或 HTTP 状态异常；5xx 还可能出现通用通知 | **正常收到一个 504，不会仅因状态码自动变成 Failed to fetch** |
| 单表 SHOW CREATE 失败 | 后端记录 warning 并继续；外层异常才包装 HTTP 400 | 不能把普通驱动异常直接等同于 worker 死亡；还需检查是否漏提对象 |
| 成功状态下响应体不完整、解码或 JSON 读取失败 | 也可进入外层 catch；具体文本取决于失败类型 | 必须记录是否已收到响应头、接收字节数及网络错误码 |

Fetch 的网络错误会导致 Promise 拒绝，普通 HTTP 响应仍会形成 Response；这是标准层面的区分，不是对截图底层错误码的认定。[WHATWG Fetch 标准](https://fetch.spec.whatwg.org/#fetch-method)

## 4. 版本退化点：确认存在，不依赖内网是否 OOM

### 4.1 变更由哪个版本引入

`c0e5e25` 提交将 VERSION 从 1.6.3.0 改为 1.6.3.2，并新增 REQ-05A 批内 R035 上下文。其父提交 `887def6` 的 `audit_file` 是逐条调用 `audit_sql`；变更后先生成全部 `parsed_items`，再生成全部 `metas`。

当前 v1.6.3.4 仍存在该结构。v1.6.3.4 新增的 `capture_report_context` 在此接口开始阶段调用一次，查询本地保存的连接信息；不是针对每张表调用。它可以有实际 I/O 延迟，但现有证据不支持把它列为本次主要容量退化来源，更解释不了 v1.6.3.2 已经失败。

**注意：旧版本整个文件审核也不是 O(1) 内存。** 输入、拆句列表、结果及输出仍随批量增长；差别在于旧版本不保留整批 ParsedSQL/AST，也没有这份平方级历史快照。

### 4.2 平方级开销是怎样产生的

现有关键语句：

```python
metas[idx][self._R035_CROSS_KEY] = {k: list(v) for k, v in index.items()}
```

它是浅拷贝：字段记录字典未被逐项深拷贝，但**新列表中每一个引用仍然要占用指针空间**；每条语句对应的外层字典、列表也都保留。说“只是浅拷贝，所以占用很小”不成立。

设第 i 条语句之前已有 P_i 个有效字段记录，则复制出的引用总数为 `Σ P_i`。当 N 条语句全部为有效 CREATE TABLE，每表 C 个字段进入索引时：

```text
引用槽位数 S = C × N × (N - 1) / 2
64 位指针下的空间下界 = 8 × S 字节

N=6000、C=20：
S = 359,940,000
仅指针空间 = 2,879,520,000 字节 = 2.682 GiB
```

这是给定条件下的数学下界，**不是创建了 6000 表后的实测 RSS**。真实值还取决于字段数、成功解析比例、字段名分布、DDL/索引/分区复杂度、结果量、运行时和并发。字段名越分散，快照外层字典及小列表的额外开销也可能更大。混入非 CREATE 语句时，当前实现仍先复制快照再判断语句类型，因此这些语句也可能增加不必要开销。

触发条件应准确表述为：**同一批文件审核中有足够多的有效字段历史，且本次实际生效的规则列表包含 R035。** 默认启用不等于每次请求一定启用，规则集覆盖仍可能关闭它；R035 在集中式、分布式均可参与。关闭后会跳过上下文构造，但全量 AST 保留仍存在。不能定义“只要超过 6000 必崩”的统一阈值。

## 5. O 本机验证结果

原始数字、完整复现脚本与实验边界见 [O 本机量化证据与复现步骤](C:/TDSQL_SQLCHECK/TDSQL-SQLCheck/docs/DIAG-6000表元数据审核失败-O本机量化证据与复现步骤.md)。

环境：Windows、CPython 3.14.6 64 位、sqlglot 30.14.0、pydantic 2.13.4；不访问数据库。最大仅 800 个简化 ParsedSQL 的上下文测试，完整解析审核最大 400 表；未冒险制造本机 OOM。

### 5.1 上下文构造：指针槽位与公式完全一致

每表 20 个同名字段 `c_0...c_19`；输入对象在 tracing 之前构造。这里测的是构造上下文新增的 Python 分配，不含输入对象及完整 AST。

| 表数 | 实际复制引用数 | 公式值 | tracemalloc 峰值/字节 |
|---:|---:|---:|---:|
| 100 | 99,000 | 99,000 | 1,364,378 |
| 200 | 398,000 | 398,000 | 4,324,586 |
| 400 | 1,596,000 | 1,596,000 | 15,052,362 |
| 800 | 6,392,000 | 6,392,000 | 55,721,642 |

### 5.2 当前完整审核路径的小规模对照

20 个 INT 列，无复杂索引/分区；本机默认规则基础上仅通过调用参数控制 R035，没有修改数据库规则配置。

| 表数 | R035 | 返回结果数 | tracemalloc 峰值/字节 | tracing 开启时耗时/秒 |
|---:|---|---:|---:|---:|
| 200 | 开 | 200 | 14,949,800 | 1.390009 |
| 400 | 开 | 400 | 36,173,014 | 3.847863 |
| 400 | 关 | 400 | 21,233,942 | 4.246933 |

400 表对照支持“R035 快照新增显著内存分配”；关闭仍保留全量解析。单次时长受 tracing、GC、缓存和调度影响，关闭组甚至略慢，**不能据此宣布关闭规则会固定提速，也不能外推内网 6000 表必为若干秒**。结果数正确不代表这些刻意简化的 DDL 通过业务审核。

`tracemalloc` 测 Python 跟踪到的分配，不能等同于 worker RSS；tracing 自身也有内存和 CPU 开销。不采用“RSS 固定为 traced peak 的 1.5—3 倍”的换算。[Python tracemalloc 官方文档](https://docs.python.org/3/library/tracemalloc.html)

### 5.3 对 A 外推数字的修正

认可 A 用对照实验验证内存增长以及“一份增量历史索引”的方向；但不采纳其 **6000 表快照 1.84 GB、占总内存 96%** 作为容量结论。

在其文字声明的“每表 20 列”全部有效入索引的条件下，6000 表快照的指针下界已达 2.880 GB（十进制），超过 1.84 GB。对 200—1600 的混合线性/平方项进行单一幂函数拟合，再向外推，不能突破实现必需的下界；如果其实际入索引字段不足 20，则应提供实际字段计数重新说明测试口径。96% 又来自两个独立拟合，不是同一次真实 6000 表采样的占比。

同理，A 的“80 秒 DDL + 167 秒审核”，与 G/Q 的“200—240 秒 DDL + 数秒审核”，目前都不能当作内网阶段时间。O 的简化负载测得不同时间，也不能反过来证明 A 实验有误；需要统一输入和运行环境。

## 6. 新的关键分支：Uvicorn 健康探测失败也会杀 worker

### 6.1 为什么 `died` 不能直接翻译成 OOM

本机安装 Uvicorn 0.49.0；同时只读检查了本地离线 wheel `C:\TDSQL_SQLCHECK\TDSQL-SQLCheck\dist\wheels_tmp\uvicorn-0.52.1-py3-none-any.whl`。两者均有如下语义：

1. `Process.is_alive()` 不只检查操作系统进程是否存在，还通过进程间管道 ping/pong 检查响应。
2. 子进程有独立的 `always_pong` 后台线程。
3. 健康检查不通过时，父进程执行 kill、join，再输出 `Child process [...] died` 并创建替代 worker。
4. 本机与该 wheel 的默认健康探测等待为 5 秒，不是请求总耗时 250 秒。

也核对了依赖最低允许版本 0.34.0 的官方源码，已存在该存活检查加 ping、失败后 kill 的机制；因此不能只凭新版本现象，宣称这是“升级 Uvicorn 新增的 bug”。[Uvicorn 0.34.0 官方源码](https://raw.githubusercontent.com/encode/uvicorn/0.34.0/uvicorn/supervisors/multiprocess.py)、[Uvicorn 0.49.0 官方源码](https://raw.githubusercontent.com/encode/uvicorn/0.49.0/uvicorn/supervisors/multiprocess.py)

项目 requirements 只是 `uvicorn>=0.34.0`，旧新产品版本该行未改变；**内网实际安装版本、发行包 wheel 和本机版本不能混为一谈**，必须用内网服务自己的 venv 读取。

O 还做了无进程创建/终止的 mock 验证：让底层 `process.is_alive()` 返回 true、ping 返回 false，调用本机真实的 supervisor 方法，观察到 `kill → join → start`。这证明日志判据不唯一，**没有复现内网健康探测超时**。

### 6.2 它与大批量审核可能怎样关联

同步业务代码占用事件循环，已经影响同 worker 的其他 HTTP 任务；但 **事件循环被占用不等于独立 pong 线程一定停顿**。普通 Python 运算会有线程调度，不能用“async 里跑同步代码”单独证明健康检查必失败。

合理但待证实的链条是：大量 AST、索引快照及其扫描/回收引发长时间 GC、长时间持有 GIL 的操作，或严重系统调度/换页压力，使 pong 线程未在探测窗口回应；父进程主动 kill；正在处理的 HTTP 响应中断。需要同次运行的健康探测、GC 停顿、CPU/RSS/交换和信号来源证据。**本报告没有测得一次持续超过 5 秒的真实 GC，也不把 GC 写成最终根因。**

即使最终确认 SIGKILL，也不自动等于 OOM：父进程主动 kill 在 Linux 同样可产生 SIGKILL。还需确认信号发送者或匹配的 OOM 记录。

## 7. “每次约 250 秒”的判定

已查当前调用路径，未发现 250 秒应用请求定时器；以下参数不能混用：

| 项目 | 实际含义 | 本案判断 |
|---|---|---|
| 前端 apiFetch | 本调用未配置超时或 AbortController | 不支持“前端写死 250 秒” |
| TDSQL 连接配置 | 默认连接 5 秒、读取 10 秒，实际应核对 pool 配置；属于连接/读取限制 | 不是整个 6000 表请求的 250 秒预算 |
| 表类型统计 180 秒 | 深度诊断表类型统计专属共享软预算 | 本次 extract-and-audit 没有调用该统计服务；不改 Mr.Linsang 已确定的 180 秒 |
| Nginx 配置样例 120 秒 | 经该代理时，上游连续两次读取间隔限制 | 样例存在不等于内网请求经过它；不能解释为“120 秒自然叠加到 250 秒” |
| Uvicorn 健康检查默认 5 秒 | 等待子进程响应的窗口 | 可能发生在请求的任意阶段；不是从点击起计时的总请求上限 |
| service RestartSec=5 | systemd 重启整个 service 的等待 | 本次是父进程还在、替换子进程，不能拿它解释 250 秒 |

相同数据量和机器条件下，累计处理到相似资源高压阶段，可能稳定在相近时间失败；网络设备的空闲超时也可能稳定。**稳定时间点是诊断线索，不是某一种故障的证明。** 要区分两者，应将同一 DDL 文件的离线引擎测试与原在线请求关联对照，再观察失败随对象数量、阶段或网络路径如何变化，而非继续凑数相加。

Nginx 对上游读取的限制确为相邻读操作间隔，而非完整响应传输总时长。[Nginx 官方说明](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_read_timeout)

## 8. 对既有报告的采纳与保留

| 意见 | O 判定及理由 |
|---|---|
| G/Q/A：v1.6.3.2 全量解析及 R035 快照导致明显容量退化 | **认可代码定位**；本机实验与引用数公式独立确认。不能因此省略内网死亡机制取证 |
| 内网报告：capture_report_context 是主要新增瓶颈 | **不认可现有归因**；按请求一次执行，且 v1.6.3.2 已失败；未给其阶段测时 |
| 内网报告：Nginx 120 秒 → 504 → 250 秒 Failed to fetch | **不认可该证明链**；入口/状态码/时间均无对应证据，HTTP 错误与网络失败混淆 |
| G/Q：审核只需数秒，失败在 250 秒具有必然性 | **不认可确定性和具体耗时**；没有内网阶段日志，资源阈值也未知 |
| A：用本机拟合替换成 80+167 秒、1.84 GB/96% | **认可测量方法的方向，不采纳外推为本案事实**；另存在数学下界冲突，详见 §5.3 |
| G/Q：只保留同名字段第一条历史，保证零漏报 | **不认可**；O 三表反例已实际验证。G 示例的值为 dict，与 R035 期望 list[dict] 还存在接口不兼容 |
| A：逐条审核后追加到同一份历史列表 | **认可作为最小保真整改方向**；仍需失败语句、同表排除、顺序、实例范围和全规则回归 |
| A：先设 2000 条上限 | **不作为本需求默认方案**；会直接拒绝此前能处理的大库，属于能力收缩，需要另行决策 |
| A：加 MemoryMax 可辅助防护 | 只能作为容量隔离的独立设计；MemoryMax 本身不能把 SIGKILL 转成友好 HTTP 错误，配置过低还可能加剧中断 |
| 内网/G：mysqldump 一次命令等于一次数据库往返，10—20 秒完成 | **不采纳**；一次进程调用不等于一次数据库查询，TDSQL 分片/分区 DDL 保真、权限及实际耗时均须验证 |
| 内网报告给出的 ROUTINES 拼 CREATE_TABLE 查询 | **不可照搬**；ROUTINES 是存储例程信息，不提供示例假设的表 DDL/Sql_Command 字段，也不能替代 SHOW CREATE 的 TDSQL 语义 |
| G/Q/A：后台任务化及进度反馈 | 方向合理，但应是持久、跨 worker 可查询的任务；仅在 Web worker 中开线程/create_task，worker 退出后仍会丢任务 |

ROUTINES 字段含义的核对依据：[MySQL 官方 INFORMATION_SCHEMA.ROUTINES 文档](https://dev.mysql.com/doc/refman/8.0/en/information-schema-routines-table.html)。本轮不改变 TDSQL DDL 获取语法；若后续优化提取方式，须另做 TDSQL 官方语法及真实 SHOW CREATE 等价验证。

## 9. 建议整改：保留审核语义，不先牺牲大库能力

### 9.1 第一优先级：去掉每条语句的历史快照

建议 Q 在另行授权的修复中按下列契约实现；本轮没有实施：

1. 保留既有换行规范化、DELIMITER-aware 拆句、MyBatis 提取及行号逻辑；不要为此次内存修复再次换拆句器。
2. 根据本次实际规则集和实例类型判断 R035 是否启用，再决定是否创建请求局部索引；不是按类默认值直接判断。
3. 每条语句只解析一次。拿当前历史索引调用既有 `_audit_parsed`，收集 AuditResult 后，再将本条有效 CREATE TABLE 的字段追加进索引。
4. 索引只保留一份，值仍为有序 `list[dict]`，每条含 `table_name/type/raw_type/statement_index`；当前语句审核前不得加入自身，不看未来语句。
5. 延续现有入索引条件：CREATE TABLE、没有 parse_error、有表名、有字段名；不要在这次性能修复中擅自重新定义语义。
6. 索引仅为一次调用所有，不能放到 checker 实例、全局变量或跨请求缓存；规则执行期间只读。未完成规则检查前不要并发追加。
7. 保留 `public_meta` 隔离：R035 内部保留键不得让其他规则误认为已有真实表元数据，尤其 R064；禁用 R035 时不创建该字段索引。
8. 不把 ParsedSQL、AST 或该元数据索引放进 AuditResult/后台闭包长期持有。每条逻辑处理完不再保留解析结果；循环引用的实际释放仍由 GC 决定，不承诺 del 就立即归还 RSS。

空间目标是将 **R035 上下文的保留量从平方级降至 O(总入索引字段数)**，不是将整个接口降至 O(1)。输入、拆句、结果、历史序列化与响应仍会占内存。

### 9.2 first-seen-only 的实测反例

```sql
CREATE TABLE a (id INT);
CREATE TABLE b (id BIGINT);
CREATE TABLE c (id INT);
```

当前 R035 的逐表命中是 `[不报, 报, 报]`，第三表报“与表 b 中的类型 BIGINT 不一致”。把每个字段历史压成一条、并保留 list[dict] 接口后，实际变为 `[不报, 报, 不报]`。

因此即使纠正 G 示例的 dict/list 接口，语义仍不正确。若后续希望用“按类型压缩证据”进一步优化 CPU，必须保留同表排除、最早冲突来源及可见顺序，单独证明等价；不能简单只留一条或一组类型名。

### 9.3 不能漏掉 CPU 和响应阶段

当前 R035 对同名且类型一致的历史引用会一路扫描。移除快照后，**该规则在这种输入下的比较次数仍可为平方级**；第一阶段解决的是快照的内存复杂度，不等于总审核耗时已线性化。应测“全同类型”“混合类型”“大量重复表名”等情况，再决定是否实施等价索引压缩。

接口还会同时返回完整 `extracted_sql` 和含 SQL 的 results，随后浏览器加载全部结果。若考虑按 report_id 下载或分页，需同步改前端下载契约（当前 `app.js:1728—1739` 依赖 extracted_sql）并核验原始 DDL 的完整保存；不能只删除响应字段。

单表 DDL 异常当前 warning 后继续，会有提取不完整风险。建议后续纳入 expected/fetched/failed 对象计数及失败名单，不能把缺对象的审核称为全量完成；这项是代码核查发现的关联风险，**不是已证实本次 worker 退出的原因**。

### 9.4 稳定性后续方案

持久化任务应记录任务 ID、实际生效规则与实例上下文、阶段、对象计数、最后错误和状态；跨 worker 查询、重启后失败恢复/重试策略都要设计。工作负载与 Web worker 隔离时，不把仍在运行的巨大输入和全部 AST 复制到多个进程。

不得先通过增加 workers、并发 8 路拉取、全局关闭 GC、关闭 R035、取消健康检查或盲目提高 MemoryMax 来声称根治。任何临时措施都需要容量评估和 Mr.Linsang 的变更授权。本次未执行这些操作。

## 10. 内网补证：一次受控复测要拿到什么

### 10.1 先统一请求身份，再判定机制

请责任方用同一失败事件回填：

| 必需证据 | 记录项 | 要区分的假设 |
|---|---|---|
| 请求身份 | request_id、起止时间及所在时区、connection_id、冻结的连接名称、实际 target_db、请求 scopes | 截图与日志是否同一请求；15063/sungl_busi 与 15064/sungl_am 是否两次测试 |
| 浏览器 Network | Preserve log；URL、请求开始/结束、状态码/网络错误码、是否收到响应头和完整 body | 504、连接被重置、其他浏览器错误；不要只提交 toast 截图 |
| 运行时指纹 | 服务 PID、实际 current 路径、VERSION、checker 文件哈希；服务 venv 的 Python/Uvicorn/sqlglot 版本 | 内网是否真的运行本报告所分析的实现及 supervisor |
| 阶段测量 | 枚举/逐表 DDL/拆句解析/索引构造/规则检查/历史/快照/响应序列化，各阶段起止与数量 | 故障发生在哪一段；不能用连接注册或定时任务当阶段标记 |
| 资源时间序列 | 每个服务进程 PID/PPID/RSS/CPU、主机 MemAvailable/Swap、服务 cgroup current/peak/limit、OOM 计数前后差值 | 内存高压还是相对低 RSS 的停顿；必须采到 worker，不只父进程 |
| 退出机制 | 父/子进程日志、退出码和信号发送者、OOM 记录、core；必要时经批准使用跟踪工具 | OOM、supervisor 主动 kill、原生崩溃、外部 kill |

新增业务阶段埋点/GC callback 属于后续诊断改动，需在批准的测试版本实施，不是本轮已经执行的事项。埋点只记计数、时长、PID、阶段和标识，不记录密码、Token 或整段业务 DDL；应覆盖失败路径，使用单调时钟算耗时。

### 10.2 可直接交内网执行的只读基础检查

以下是建议命令，**O 未在内网执行**。只读检查无需重启；输出可能包含内部路径/配置，分享前脱敏。不要输出 `.env` 或全量进程环境。

```bash
date --iso-8601=seconds
systemctl show tdsql-sqlcheck.service \
  -p MainPID -p ControlGroup -p MemoryCurrent -p MemoryMax -p MemoryHigh \
  -p EffectiveMemoryMax -p NRestarts -p ExecMainCode -p ExecMainStatus
systemctl cat tdsql-sqlcheck.service
readlink -f /opt/tdsql-sqlcheck/current
sha256sum /opt/tdsql-sqlcheck/current/backend/engine/checker.py
cat /opt/tdsql-sqlcheck/current/VERSION
/opt/tdsql-sqlcheck/current/venv/bin/python -B - <<'PY'
import sys, inspect, importlib.metadata as m
from uvicorn.supervisors.multiprocess import Process, Multiprocess
print(sys.version)
for name in ('uvicorn', 'sqlglot', 'pymysql', 'fastapi'):
    print(name, m.version(name))
print(inspect.getsource(Process.is_alive))
print(inspect.getsource(Process.ping))
print(inspect.getsource(Multiprocess.keep_subprocess_alive))
PY
free -m
cat /proc/meminfo
findmnt -t cgroup,cgroup2
ss -ltnp '( sport = :8000 )'
journalctl -u tdsql-sqlcheck.service \
  --since '2026-09-08 18:05:00' --until '2026-09-08 18:12:30' \
  --no-pager -o short-iso-precise
journalctl -k --since '2026-09-08 18:05:00' \
  --until '2026-09-08 18:12:30' --no-pager
coredumpctl list --since '2026-09-08 18:05:00' --until '2026-09-08 18:12:30'
```

复测时要替换时间窗。系统不支持的 systemd 属性/命令应记“不支持”，不能把空值解释为零。`ExecMainStatus/NRestarts` 针对主服务进程，**不一定反映被 Uvicorn 替换的 worker**；这些只是基线，不能作为 worker 无异常的证据。

### 10.3 内存采样不能只盯着 uvicorn 命令行

E2 已显示 worker 的命令行是 `multiprocessing.spawn ...`，不含 `uvicorn backend.main`。因此 A 示例用 `pgrep -f 'uvicorn backend.main'` 可能只采父进程，漏掉真正暴涨和死亡的 worker。

以下脚本仅适用于 **cgroup v2 挂载在 /sys/fs/cgroup**，先由 `findmnt` 确认；采样约五分钟并每轮重新枚举 PID，可捕捉 worker 替换。只读并输出到终端：

```bash
diag_cgroup=$(systemctl show tdsql-sqlcheck.service -p ControlGroup --value)
if [ -z "$diag_cgroup" ] || [ ! -r /sys/fs/cgroup/cgroup.controllers ]; then
  echo '停止：未确认 cgroup v2 挂载或服务 ControlGroup'
elif [ ! -r "/sys/fs/cgroup${diag_cgroup}/cgroup.procs" ]; then
  echo '停止：请按实际挂载点与 /proc/PID/cgroup 解析服务 cgroup 路径'
else
  diag_dir="/sys/fs/cgroup${diag_cgroup}"
  for diag_tick in $(seq 1 300); do
    date --iso-8601=seconds
    for diag_file in memory.current memory.peak memory.max memory.events; do
      if [ -r "$diag_dir/$diag_file" ]; then
        printf '%s\n' "$diag_file"
        cat "$diag_dir/$diag_file"
      fi
    done
    for diag_pid in $(cat "$diag_dir/cgroup.procs"); do
      ps -p "$diag_pid" -o pid,ppid,rss,pcpu,etime,comm
    done
    sleep 1
  done
fi
```

该脚本按现有部署“进程直接位于 service cgroup”编写；若存在子 cgroup/容器，还要对服务下各子组分别采样。若为 v1，应依据 memory 控制器挂载点和 `/proc/<MainPID>/cgroup` 定位，采集 `memory.usage_in_bytes`、`memory.max_usage_in_bytes`、`memory.limit_in_bytes`、`memory.failcnt`、`memory.oom_control` 及该组 PID；**failcnt 增长不等于已杀进程**。同时核对上级 slice/container 限额，不能读取根 cgroup 就代替服务组。

v2 `memory.events` 的 oom_kill 是计数，需比较同次复测前后变化并与时间/PID关联；历史非零不说明本次发生。[Linux cgroup v2 官方文档](https://docs.kernel.org/admin-guide/cgroup-v2.html)

若仍无 OOM 证据而怀疑父进程主动 kill，可在批准的测试窗口用系统可用的信号追踪工具，限定父/子 PID 捕捉 kill/wait 与退出状态。该步骤有性能与运维影响，应由内网责任方选择工具执行，不建议在生产直接挂全进程 strace。

## 11. 后续验证与缺陷关闭标准

1. **算法等价**：保持既有 R035 用例全过；额外覆盖 `INT/BIGINT/INT`、长度不同类型相同、别名规范化、同一表名重复、大小写、失败语句、非 CREATE 混排、首表/末表、跨请求隔离、规则关闭和两种实例架构。比较命中、消息来源、行号、顺序及全部非 R035 规则，不能只比较总条数。
2. **容量对照**：同一脱敏 DDL、同一依赖和规则集，对旧实现与新实现先在 100/500/1000 等安全规模测阶段时间及进程 RSS，再在评估后的测试资源上测真实 6000+；达到资源止损线就结束测试任务并保存证据，不故意把整机压到 OOM。
3. **时间真实性**：开启 tracing 的诊断运行和关闭 tracing 的性能运行分开记录；容量与耗时不能只由小样本幂函数拟合签署。
4. **真实浏览器闭环**：在原内网实例、原 scopes 下完成拉取、结果展示、SQL 下载、历史记录、HTML 报告（含实例名称）及对比快照；核对对象 expected/fetched/failed 数，不能漏表后“成功”。
5. **稳定性**：单大任务及批准的并发场景下，worker 不发生非预期替换，其他正常页面仍可响应；重试不会悄悄产生多个失控任务。执行失败必须能区分提取、审核和连接中断。
6. **全局回归与发布**：修复触及核心 checker，须跑既有全量规则/文件/在线审核回归及发布安装验证；不因为只修两行就跳过。若引入异步任务或修改响应契约，另加鉴权、跨 worker、重启恢复及旧前端兼容测试。
7. **关闭证据**：提交代码/依赖指纹、同源输入哈希、真实请求/阶段记录、资源曲线、PID 稳定性、对象完整性与浏览器结果；“接口返回成功一次”不足以宣告容量缺陷关闭。

## 12. 最终判定与交叉复核要点

本次可确认并应立项修复的是 **v1.6.3.2 引入、v1.6.3.4 延续的全量 AST 保留与 R035 历史快照容量退化**。现有日志同时表明故障附近有 worker 被替换，故“资源高压导致进程失去服务能力”是首要工作假设；**最终杀进程路径仍待 OOM/健康检查/其他信号的闭环证据，250 秒不是已查明的代码超时常量**。

请其他智能体优先独立复核四点，而非只重复结论：

- 在内网实际 Uvicorn 版本中，健康检查失败与自然死亡是否共用这条日志；能否找到此次信号来源。
- 以实际成功解析字段数计算 `Σ P_i`，验证快照内存下界及修复前后 RSS，而非采用固定 1.84 GB 外推。
- 对 first-seen-only 三表反例及“有序增量完整历史索引”的等价性进行独立验证。
- 把截图、连接 ID/库名、请求时间、worker PID 和业务阶段关联成同一次事件，消除 15063/15064 两种样本混用。

以上整改均为建议；**本轮仅产出本报告及实验复现附件，未修改业务代码、配置或数据库。**

## 13. 补证更新：wde.txt 内网检查结果

### 13.1 新证据已确认什么

附件：`C:\Users\linsa\OneDrive\Desktop\wde.txt`，检查时刻为 `2026-09-08T23:30:13+08:00`；这是故障约五小时后的基础检查及历史日志回查，不是一次新的失败复测。

```text
wde.txt SHA-256:
6B52E1A7BD46D45661777855FAA03848DA6F74DDD63144E9B00207FA97A4C31D

内网与本机当前 backend/engine/checker.py SHA-256 均为:
864FB18D0004703A3B729CA6837391348C2BD5AA08113651E8FB471966B729EE
```

| 新证据 | 可以确认 | 仍需保留的边界 |
|---|---|---|
| 第 44—49 行：current 指向 v1.6.3.4，VERSION 一致，checker 哈希一致 | 当前磁盘上的核心算法文件正是本报告分析的实现；不是仅凭页面版本号推断 | 单文件哈希不证明整包一致，也不是故障瞬间进程已加载代码的完整指纹 |
| 第 63—67 行：Python 3.11.11、Uvicorn 0.52.1、sqlglot 30.14.0、PyMySQL 1.2.0、FastAPI 0.141.1 | 内网实际运行时版本已由服务 venv 读取；不可继续套用本机 Python 3.14.6 的绝对耗时 | 本机小样本实验仍只作结构性验证，不是内网容量数据 |
| 第 68—103 行：实际安装包的方法源码 | 内网 supervisor 确有健康检查失败后 kill/join/restart 并打印 died 的路径 | 该代码分支存在，不证明本次已经进入；进程本来已死亡也会走存活检查失败分支 |
| 第 105—112 行：MemTotal 约 61.6 GiB，MemAvailable 约 57.2 GiB；Swap 使用为 0 | 推翻“该主机只有 4/8 GB”的假设；检查时并无明显主机内存紧张 | 不是 18:10 故障瞬间可用量或峰值，不能据此彻底排除 OOM |
| 第 6—13 行：MainPID 846885，NRestarts=0，MemoryCurrent=158613504，MemoryMax/High=infinity | 父进程持续存在；检查时服务内存约 151.3 MiB；没有显示有限的这两个 systemd 配置值 | cgroup v1 下还须读实际 memory 控制器及上级限制；未返回的 EffectiveMemoryMax 不作无限解释；主进程状态不代表旧 worker |
| 第 161—175 行：独立 memory 控制器挂在 /sys/fs/cgroup/memory | **确认 cgroup v1**，而非 v2 | service 在 memory 控制器中的准确相对路径还须从 /proc/MainPID/cgroup 读取 |
| 第 176—178 行：8000 监听归属 Python 父进程及两个 worker | 与截图直达应用 8000 端口的判断一致，未见该端口由 Nginx 监听 | 不由此断言客户端至服务器之间不存在任何网络设备/透明代理 |
| 第 203—204 行：18:10:09.552963 等待，18:10:09.625359 died | worker 850881 的替换事件与前份记录一致 | 两条日志的毫秒间隔不是信号发送者证据；不能用它认定 OOM 或 watchdog |
| 第 233—239 行：故障时间窗内 kernel 为 No entries，No coredumps found | 没有检索到该窗 OOM/崩溃内核日志或登记的 core | 缺少 core 可能与收集配置有关；“无记录”不是“所有崩溃机制已排除” |
| 第 245—270 行：v2 采样条件不满足后停止 | 原 §10.3 脚本**没有开始采样**，并未取得内存曲线 | 不是内存一直为零，也不是 Mr.Linsang 操作错误；应按下节使用 v1 文件 |

粘贴记录含少量命令回显交错，但版本行、后续独立执行的日志命令及结果可以辨认；未执行部分不作为成功采证。

### 13.2 更新后的归因与下一步优先级

**代码容量缺陷的结论不变，部署文件的一致性证据更强；对于本次 worker 为什么退出，优先核查 Uvicorn 健康检查未响应后的主动 kill。** 当前没有直接 OOM 证据，主机也不是此前假设的小内存机器。这个优先级调整不等于已经排除 OOM 或确认 GC 超过 5 秒。

新核对了 Uvicorn 0.52.1 的完整源码：pong 仍由独立后台线程执行；`ping()` 通过 `_healthcheck()` 判定，后者不只在等待超时后返回失败，也会处理管道的部分异常。单纯“async 路由被同步工作占用”不能直接证明 pong 线程失去响应。后续应区分长停顿、管道异常、原有进程死亡及外部信号。[Uvicorn 0.52.1 官方源码](https://raw.githubusercontent.com/encode/uvicorn/0.52.1/uvicorn/supervisors/multiprocess.py)

默认健康检查窗口为 5 秒；服务启动命令没有显式覆盖。但 0.52.1 的 CLI 支持 `UVICORN_` 环境变量前缀，service 又加载 `.env`，故**只凭方法签名默认值，尚不能把故障时生效值锁定为 5 秒**。下面仅白名单读取父进程对应的单个变量，避免泄漏整份环境或凭据。

§6.2 的“大批量对象导致 GC/GIL/调度压力，使 pong 未及时响应”仍是待验证机制。不要把本轮结论改写为“已确认 GC 导致 watchdog 杀进程”。约 250 秒也仍不是已发现的本接口超时常量。

### 13.3 先做这一组 v1 只读检查，不必立即重跑大库

由于主 service 未重启，v1 的 `memory.max_usage_in_bytes` 可能还保留故障期间的服务组高水位。它是**该 cgroup 自创建或上次计数重置以来**的高水位，可能包含别的任务，不能直接称为这次请求的 RSS 峰值；仍比故障后的单点内存有价值。不要为了读取它重启服务或清零计数。

以下命令根据本次已确认的 `/sys/fs/cgroup/memory` 挂载点编写，从 MainPID 实际 memory 控制器路径出发，读取自身和祖先组。只读、不改配置、不发 SQL 请求、不安装工具；O 尚未在内网执行。复制代码块内部即可，不要复制 Markdown 围栏。

```bash
bash <<'SH'
diag_main=$(systemctl show tdsql-sqlcheck.service -p MainPID --value)
case "$diag_main" in
  ''|0|*[!0-9]*) echo '停止：未找到运行中的 MainPID'; exit 1 ;;
esac
if [ ! -r "/proc/$diag_main/cgroup" ]; then
  echo '停止：MainPID 已退出或不可读取'; exit 1
fi
date --iso-8601=seconds
cat "/proc/$diag_main/cgroup"
diag_rel=$(awk -F: '$2 ~ /(^|,)memory(,|$)/ {print $3; exit}' "/proc/$diag_main/cgroup")
case "$diag_rel" in
  /*) ;;
  *) echo '停止：未找到 v1 memory 控制器路径'; exit 1 ;;
esac
diag_mount=/sys/fs/cgroup/memory
diag_service="${diag_mount}${diag_rel}"
diag_service=${diag_service%/}
if [ ! -r "$diag_service/memory.usage_in_bytes" ]; then
  echo '停止：memory 路径与已知挂载不匹配，请保留上面 cgroup 输出'; exit 1
fi
diag_dir="$diag_service"
while :; do
  printf '\nCGROUP %s\n' "$diag_dir"
  for diag_file in memory.usage_in_bytes memory.max_usage_in_bytes \
      memory.limit_in_bytes memory.failcnt memory.oom_control \
      memory.memsw.usage_in_bytes memory.memsw.max_usage_in_bytes \
      memory.memsw.limit_in_bytes memory.memsw.failcnt memory.use_hierarchy; do
    if [ -r "$diag_dir/$diag_file" ]; then
      printf '%s\n' "$diag_file"
      cat "$diag_dir/$diag_file"
    fi
  done
  if [ -r "$diag_dir/memory.stat" ]; then
    awk '/^hierarchical_memory_limit / || /^hierarchical_memsw_limit /' "$diag_dir/memory.stat"
  fi
  [ "$diag_dir" = "$diag_mount" ] && break
  diag_dir=${diag_dir%/*}
  case "$diag_dir" in
    "$diag_mount"|"$diag_mount"/*) ;;
    *) echo '停止：已到挂载边界'; break ;;
  esac
done
printf '\nCURRENT_SERVICE_PROCESSES\n'
if [ -r "$diag_service/cgroup.procs" ]; then
  for diag_pid in $(cat "$diag_service/cgroup.procs"); do
    ps -p "$diag_pid" -o pid,ppid,rss,pcpu,etime,comm
  done
fi
/opt/tdsql-sqlcheck/current/venv/bin/python -B - "$diag_main" <<'PY'
import pathlib, sys
pid = sys.argv[1]
key = b'UVICORN_TIMEOUT_WORKER_HEALTHCHECK'
found = []
try:
    for item in pathlib.Path('/proc', pid, 'environ').read_bytes().split(b'\0'):
        name, sep, value = item.partition(b'=')
        if sep and name == key:
            found.append(value.decode('utf-8', 'replace'))
    print(key.decode(), '=', repr(found) if found else '<not set in parent process>')
except OSError as error:
    print('HEALTHCHECK_ENV_READ_FAILED', type(error).__name__)
PY
SH
```

字段不存在或不可读应记“未取得”，不要补零。`memory.failcnt` 表示限制命中计数，不等价于杀进程次数；`memory.oom_control` 是否有 oom_kill 字段依内核实现而定；根组/祖先组也不能代替服务组。本机只核对了命令逻辑，未伪造其内网输出。上述文件的基本定义可参考 [Linux v1 memory 控制器文档](https://docs.kernel.org/admin-guide/cgroup-v1/memory.html)；该文档自述部分内容较旧，最终以本机内核实际暴露的文件和行为为准。

### 13.4 收到 v1 输出后如何判定

- **若高水位远低于所有有效限制、相关失败/杀进程记录也无支持**：进一步下调内存耗尽杀进程假设，重点取得父进程发信号证据及故障阶段栈/停顿；仍不把历史计数自动等同于单次请求测量。
- **若服务或祖先存在有限配额，且计数/日志与故障关联**：重新评估 memcg OOM；即使主机有几十 GiB 空闲，小 cgroup 也可能先耗尽。
- **若计数已重置、记录无法关联或高水位可能来自其他任务**：再安排一次批准的测试环境复测，按实际 v1 service cgroup 逐秒重新枚举 worker PID 采 RSS/组内存，并配合阶段日志及限定 PID 的信号跟踪。
- **若确认是父进程 kill**：还须区分健康探测超时、管道异常与其他管理动作；要确认 GC，则另需 GC 停顿/线程栈证据。不要仅靠增大健康检查窗口测试一次成功，就关闭算法容量缺陷。

本轮补证没有增加代码改动。G/Q/A 可以依据哈希一致性继续评审保真增量索引整改；故障死亡机制则按上述证据逐步收口，不应继续沿用“已确认 OOM”或“必然 250 秒”的表述。

## 14. 第二次补证：wd2e.txt 与阶段性诊断收口

### 14.1 材料身份与新增结果

附件：`C:\Users\linsa\OneDrive\Desktop\wd2e.txt`。前半部分重复了 `wde.txt` 的检查记录，真正新增的 v1 输出始于第 355 行，时间为 **2026-09-08 23:38:39 +08:00**。不能将重复的 18:10 worker 退出记录当作又一次失败。

```text
wd2e.txt SHA-256:
2D60CC81DABEA113E126341B4179189EC3D6C5CB9F8D6D7D0B0C49CE4731F4E6
```

MainPID 的 memory 控制器路径确认为 `/system.slice/tdsql-sqlcheck.service`，与本次读取的服务组一致。以下数值均来自附件，而不是本机模拟或拟合：

| v1 memory cgroup | 历史高水位/字节 | 换算/GiB | memory.failcnt / memsw.failcnt | oom_kill / oom_kill_local |
|---|---:|---:|---|---|
| 服务组（369—394 行） | 3,889,078,272 | 3.621986 | 0 / 0 | 0 / 0 |
| system.slice（396—421 行） | 6,052,622,336 | 5.636944 | 0 / 0 | 0 / 0 |
| memory 根组（423—448 行） | 11,465,011,200 | 10.677624 | 0 / 0 | 0 / 0 |

三层 `memory.limit_in_bytes`、`memory.memsw.limit_in_bytes` 及返回的层级有效限额，均为 `9223372036854771712`。这是 v1 接口接近无限的上限表示，**不是 4 GiB、8 GiB 等会被本次规模击穿的配额，也不表示主机真有这个物理容量**。三层 `memory.use_hierarchy=1`、`under_oom=0`、`oom_kill_disable=0`。

`oom_kill_disable=0` 表示没有通过该开关禁用 OOM killer，不应把它误读成“发生了零次 OOM”；历史次数看单独的 oom_kill 字段。`under_oom=0` 是当前状态，不是历史次数。上游 Linux 4.19 的 v1 `memory.oom_control` 实现也将这些字段分开输出；内网为带厂商补丁的麒麟 4.19.90，不能将上游源码当作该厂商内核的逐字等同实现。[Linux 4.19 memory 控制器源码](https://raw.githubusercontent.com/torvalds/linux/v4.19/mm/memcontrol.c)

服务当前用量为 `158613504` 字节（约 151.3 MiB），只是故障后的当前值。历史高水位 3.622 GiB 的口径是**整个服务 cgroup 的受计费内存**，不是旧 worker 850881 的单进程 RSS，也未绑定某一次请求。祖先与子组高水位不能相加，根组高水位也不等同于整机所有物理内存的历史峰值。memory 与 memsw 高水位相同，不足以证明历史上从未发生过交换。

第 450—458 行进程树仍是主进程 846885、resource_tracker、原 worker 846889、替换 worker 870996；主进程运行约 7 小时 49 分钟、替换 worker 约 5 小时 29 分钟，与此前时间线一致。这支持 service 持续运行，但不能独立证明所有计数从未被人为重置；现有材料没有显示重置、重建 cgroup 或迁移进程的操作。

### 14.2 健康检查窗口现在可以收口为 5 秒

第 459 行明确返回：

```text
UVICORN_TIMEOUT_WORKER_HEALTHCHECK = <not set in parent process>
```

结合已取得的证据：

1. 实际服务由 `python -m uvicorn ... --workers 2` 启动，没有 `--timeout-worker-healthcheck` 命令行覆盖；
2. 该父进程没有对应环境变量覆盖；
3. 已确认安装版本 0.52.1，其 CLI 默认值为 5。

因此，**按当前已核验的标准 CLI 启动路径，健康检查使用 5 秒窗口**。不再将“是否有常规参数覆盖”列为待补资料；若未来发现额外启动注入/运行时补丁，应另附证据，而不是先凭空假设。该 5 秒仍是父进程等待子进程回应的时间，不是用户点击后整个任务只允许运行 5 秒，更不是 250 秒业务请求计时器。[Uvicorn 0.52.1 CLI 官方源码](https://raw.githubusercontent.com/encode/uvicorn/0.52.1/uvicorn/main.py)

### 14.3 对本次故障的判断如何变化

**可以收口：** 在 cgroup 历史连续、没有事后重建/清零/变更限制等未披露操作的通常前提下，当前材料基本排除了“服务或其上级 memory/memsw 小配额被击穿而杀 worker”这一解释。结合三层零 OOM-kill 计数、故障窗口没有内核记录及主机容量信息，也明显不支持把本次事故归因为整机内核 OOM 杀进程。**后续报告不应继续把“OOM 已确认”作为结论。**

**还不能收口：** 尚未捕获故障时的退出信号发送者、健康探测失败原因或线程停顿。Uvicorn 健康检查失败后主动 kill 是当前首要工作假设，但也要保留管道异常、worker 先自行退出或其他外部信号的可能。零 OOM 计数不能反过来直接证明 GC/健康检查超时。

**算法缺陷并未因此消失：** 平方级快照的空间复杂度已经由代码和有界实验确认。服务出现数 GiB 历史高水位，与大批量处理显著增加资源占用的方向相容，但不能仅用这个总量分摊 AST/快照占比，或证明它就是某次任务的峰值。即使没有耗尽物理内存，长期保留大量对象也可能造成 GC/GIL/调度停顿；是否恰好使独立 pong 线程超过 5 秒未回应，仍需动态证据。

更新后的分层结论：

| 层次 | 结论状态 |
|---|---|
| v1.6.3.2 新增全量 AST 保留与 R035 平方级快照，v1.6.3.4 保留 | 已确认产品容量缺陷；整改方向不变 |
| 内网当前 checker 文件与排查基线一致、Uvicorn 0.52.1、标准路径健康检查 5 秒 | 已核验 |
| 故障附近 worker 850881 被替换，主进程未重启 | 已由日志确认；与具体 HTTP 请求的 request_id 仍未闭环 |
| Linux/cgroup OOM 是本次 worker 死亡原因 | 现有补证明显反对，不再列为首要归因 |
| 父进程因健康探测失败而主动结束 worker | 首要待验证机制，未取得信号来源及健康探测结果 |
| 精确原因是一次超过 5 秒的 GC 或 GIL 停顿 | 尚未实测，不能宣称已经确诊 |

### 14.4 不再重复静态排查；如需最终定案，只做一次定点动态取证

现有基础信息已足够更新事故判断，并支持对已确认的代码缺陷进行保真修复设计。**不再要求 Mr.Linsang 重复执行 free/systemctl/cgroup 基础检查，也不建议反复裸跑大库等待 toast。** 当前修复建议仍为 §9 的请求局部增量完整历史索引，不能改成 first-seen-only，不能只增加内存或超时就声称算法已修复。

若项目需要将事故机制而非仅代码缺陷最终定案，建议在批准的内网测试窗口完成一次以下关联取证；本轮不实施，不对生产挂跟踪器：

1. 将浏览器一次请求、实际 connection_id/库名、请求所在 worker PID 与开始时间对应；阶段信息至少区分 DDL 提取、解析、快照构造、规则审核及响应。
2. 在该次请求发生前，针对**实际父进程和请求所在 worker**进行限定范围的信号/退出状态观察；保留发送方 PID、目标 PID、信号、调用结果与时间。`SIGKILL` 或 `kill(...)=0` 单独仍不能说明业务停止前发生了什么。
3. 若记录父进程发出 kill，进一步关联其健康探测结果。当前 supervisor 在发现进程已死时也会走同一管理分支，故只看到 died 或父进程进入 kill 代码不够；应确认 kill 前 worker 尚存活及 ping 为何失败。
4. 同次采集 worker 的低频 RSS/CPU 和必要的线程栈；若要归因 GC，再捕捉 GC 起止及持续时间。诊断开销会改变时序，应记录工具和采样开销，不采用高频全量 tracemalloc 运行冒充无干扰性能结果。
5. 如果未取得动态证据即可按明确算法缺陷先修复，应将关闭项写成“平方级快照缺陷已修复、真实大库验收通过”，不要补写成“历史事故已证明由 GC 杀进程”。

本次只修订诊断报告；未改动业务代码、规则、数据库、健康检查窗口或部署配置。阶段性结论可直接供 G/Q/A 交叉评审，后续工作重点应从“猜测物理内存不足”转向“保真消除算法资源放大、验证 worker 存活及长任务完成能力”。
