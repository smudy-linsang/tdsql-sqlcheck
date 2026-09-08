# O 本机量化证据与复现步骤：6000+ 表元数据审核失败诊断

关联报告：[O 独立排查与证据边界报告](C:/TDSQL_SQLCHECK/TDSQL-SQLCheck/docs/DIAG-6000表元数据审核失败-O独立排查与证据边界报告.md)。

## 1. 实验口径

| 项目 | 内容 |
|---|---|
| 执行日期 | 2026-09-08 |
| 源码基线 | `b52e324f37f38f498916775900e41f8fbd7263b0` |
| 工作目录 | `C:\TDSQL_SQLCHECK\TDSQL-SQLCheck` |
| 解释器 | `C:\Python314\python.exe`，CPython 3.14.6，64 位 |
| 依赖 | sqlglot 30.14.0、pydantic 2.13.4、uvicorn 0.49.0 |
| 持久化/网络 | 未调用服务层保存历史；未连接数据库；未发业务 HTTP 请求 |
| 测量范围 | 上下文构造最多 800 表；完整 checker 审核最多 400 表；健康检查测试全部使用 mock，无真实进程创建/终止 |
| 运行次数 | 各配置一次有界诊断，非多轮性能基准 |
| 原始结果解释 | 返回结果数不等于 SQL 通过数；合成 DDL 刻意简单，可能触发常规建表规范规则 |

加载模型时出现既有 Pydantic `schema` 字段遮蔽警告，不妨碍实验完成。未修改模型以消除警告。

## 2. 已执行脚本一：引用计数、内存与 R035 反例

在对应源码基线和已安装项目依赖的解释器中运行以下 Python，或在临时 UTF-8 诊断文件中运行。**不要在生产 Web worker 内执行，不需要安装额外依赖，不要把样本直接提高到 6000。** Windows PowerShell 的管道编码可能与 Python 默认 UTF-8 不同，带中文注释的块不要直接用默认编码管道传入；本文末提供编码明确的执行方式。

```python
import gc
import sys
import struct
import time
import tracemalloc
import json
from backend.engine.checker import RuleChecker
from backend.engine.parser import ParsedSQL

checker = RuleChecker()
print('runtime', sys.version.split()[0], 'pointer_bytes', struct.calcsize('P'))

for n in (100, 200, 400, 800):
    # 故意不创建完整 AST：将“上下文新增分配”与解析分配分开。
    items = [
        ('', i, ParsedSQL(
            raw_sql='', sql_type='CREATE TABLE', is_create_table=True,
            tables=[f't_{i}'],
            columns=[{'name': f'c_{j}', 'type': 'INT', 'raw_type': 'INT'}
                     for j in range(20)]))
        for i in range(n)
    ]
    gc.collect()
    tracemalloc.start()
    t = time.perf_counter()
    metas = checker._build_r035_cross_table_context(items, None, 'distributed')
    elapsed = time.perf_counter() - t
    cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    slots = sum(len(v) for m in metas
                for v in m.get(checker._R035_CROSS_KEY, {}).values())
    print(json.dumps({
        'experiment': 'context_only', 'n': n, 'cols': 20,
        'seconds': round(elapsed, 6), 'traced_peak_bytes': peak,
        'slots': slots, 'formula_slots': 20*n*(n-1)//2,
        'minimum_pointer_bytes': slots*struct.calcsize('P')}))
    assert slots == 20*n*(n-1)//2
    del metas, items
    gc.collect()

sql = 'CREATE TABLE a (id INT); CREATE TABLE b (id BIGINT); CREATE TABLE c (id INT);'
parsed = [checker.parser.parse(s) for s in sql.split(';') if s.strip()]
items = [(p.raw_sql, 1, p) for p in parsed]
metas = checker._build_r035_cross_table_context(items, None, 'distributed')
r035 = next(r for r in checker.rules if r.rule_id == 'R035')
full = [r035.check(p, m) for p, m in zip(parsed, metas)]
# 保持现有 list[dict] 契约，仅限制为第一条，单独验证语义是否等价。
first = [r035.check(p, {checker._R035_CROSS_KEY: {
    k: [v[0]] for k, v in m[checker._R035_CROSS_KEY].items()}})
    for p, m in zip(parsed, metas)]
print(json.dumps({
    'experiment': 'first_seen_counterexample',
    'full_history': [bool(v) for v in full],
    'first_only': [bool(v) for v in first],
    'full_third_message': full[2].message}, ensure_ascii=True))
print('AST_RETAINED', parsed[0].ast is not None)

for n, enabled in ((200, True), (400, True), (400, False)):
    content = '\n'.join(
        'CREATE TABLE t_%d (%s);' % (
            i, ', '.join(f'c_{j} INT' for j in range(20)))
        for i in range(n))
    gc.collect()
    tracemalloc.start()
    t = time.perf_counter()
    result = checker.audit_file(
        content, rule_overrides={'R035': {'enabled': enabled}},
        instance_type='distributed')
    elapsed = time.perf_counter() - t
    cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(json.dumps({
        'experiment': 'full_audit', 'n': n, 'cols': 20,
        'r035_enabled': enabled, 'seconds_with_tracing': round(elapsed, 6),
        'traced_peak_bytes': peak, 'results': len(result)}))
    del result, content
    gc.collect()
```

### 实际输出

以下为本轮脚本标准输出；省略标准错误流中的既有模型警告。不得把这些数字标为内网结果。

```text
runtime 3.14.6 pointer_bytes 8
{"experiment": "context_only", "n": 100, "cols": 20, "seconds": 0.00733, "traced_peak_bytes": 1364378, "slots": 99000, "formula_slots": 99000, "minimum_pointer_bytes": 792000}
{"experiment": "context_only", "n": 200, "cols": 20, "seconds": 0.015129, "traced_peak_bytes": 4324586, "slots": 398000, "formula_slots": 398000, "minimum_pointer_bytes": 3184000}
{"experiment": "context_only", "n": 400, "cols": 20, "seconds": 0.03813, "traced_peak_bytes": 15052362, "slots": 1596000, "formula_slots": 1596000, "minimum_pointer_bytes": 12768000}
{"experiment": "context_only", "n": 800, "cols": 20, "seconds": 0.129083, "traced_peak_bytes": 55721642, "slots": 6392000, "formula_slots": 6392000, "minimum_pointer_bytes": 51136000}
{"experiment": "first_seen_counterexample", "full_history": [false, true, true], "first_only": [false, true, false], "full_third_message": "\u5173\u8054\u5b57\u6bb5 id \u5728\u8868 c \u4e2d\u7684\u7c7b\u578b\u4e3a INT\uff0c\u4e0e\u8868 b \u4e2d\u7684\u7c7b\u578b BIGINT \u4e0d\u4e00\u81f4\u3002"}
AST_RETAINED True
{"experiment": "full_audit", "n": 200, "cols": 20, "r035_enabled": true, "seconds_with_tracing": 1.390009, "traced_peak_bytes": 14949800, "results": 200}
{"experiment": "full_audit", "n": 400, "cols": 20, "r035_enabled": true, "seconds_with_tracing": 3.847863, "traced_peak_bytes": 36173014, "results": 400}
{"experiment": "full_audit", "n": 400, "cols": 20, "r035_enabled": false, "seconds_with_tracing": 4.246933, "traced_peak_bytes": 21233942, "results": 400}
```

## 3. 已执行脚本二：Uvicorn 存活判据的 mock 验证

目的不是模拟 250 秒故障，而是验证：**底层进程尚存活但 ping 不响应，也会走 supervisor 的 kill/restart 分支**。

以下与实际执行逻辑一致，仅移除了另行只读打印 wheel 源码的部分。本 mock 针对本机 Uvicorn 0.49.0 的接口；其他版本须先核对方法签名，不能用于证明内网安装版本。

```python
import json
import threading
from types import SimpleNamespace
from unittest.mock import patch
import uvicorn.supervisors.multiprocess as mp

probe = mp.Process.__new__(mp.Process)  # 不执行构造器，不建进程/管道
probe.process = SimpleNamespace(is_alive=lambda: True, pid=123456789)
probe.ping = lambda timeout=5: False
print('OS_PROCESS_ALIVE', probe.process.is_alive(),
      'UVICORN_IS_ALIVE', probe.is_alive())

actions = []
probe.kill = lambda: actions.append('kill')
probe.join = lambda: actions.append('join')
manager = mp.Multiprocess.__new__(mp.Multiprocess)
manager.should_exit = threading.Event()
manager.processes = [probe]
manager.config = SimpleNamespace(timeout_worker_healthcheck=5)
manager.target = None
manager.sockets = []
replacement = SimpleNamespace(start=lambda: actions.append('start'))
with patch.object(mp, 'Process', return_value=replacement):
    manager.keep_subprocess_alive()
print('MOCK_SUPERVISOR_ACTIONS', json.dumps(actions))
print('REAL_PROCESS_CREATED_OR_KILLED', False)
```

实际输出：

```text
OS_PROCESS_ALIVE True UVICORN_IS_ALIVE False
MOCK_SUPERVISOR_ACTIONS ["kill", "join", "start"]
REAL_PROCESS_CREATED_OR_KILLED False
```

另只读查看了离线 `uvicorn-0.52.1-py3-none-any.whl` 中 `uvicorn/supervisors/multiprocess.py` 和 `uvicorn/config.py`：仍有 OS 存活判断加 ping、健康检查失败后 kill/join/重建的分支；默认 `timeout_worker_healthcheck` 为 5。该 wheel 的存在不证明内网当前安装的就是这个版本。

## 4. Windows PowerShell 复现方式

先核对工作目录、Python 及依赖版本符合 §1。本命令仅读取本文两个 Python 围栏块，通过 Base64 传输 UTF-8 源码后执行，避免 PowerShell 原生管道对中文注释的编码影响；不落盘 Python 文件，不修改源码。后续运行的耗时/分配数字不必与单次原始数字逐字一致，引用槽位和语义反例结果应一致。

```powershell
Set-Location -LiteralPath 'C:\TDSQL_SQLCHECK\TDSQL-SQLCheck'
$diagDoc = Get-Content -Raw -Encoding UTF8 -LiteralPath 'C:\TDSQL_SQLCHECK\TDSQL-SQLCheck\docs\DIAG-6000表元数据审核失败-O本机量化证据与复现步骤.md'
$diagBlocks = [regex]::Matches($diagDoc, '(?ms)^```python\r?\n(.*?)^```')
foreach ($diagBlock in $diagBlocks) {
    $diagB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($diagBlock.Groups[1].Value))
    $diagB64 | & 'C:\Python314\python.exe' -B -c "import base64,sys; exec(compile(base64.b64decode(sys.stdin.read()).decode('utf-8'), '<diagnostic>', 'exec'))"
    if ($LASTEXITCODE -ne 0) { throw '诊断块执行失败，停止后续实验' }
}
```

## 5. 没有完成、不能宣称完成的验证

- 没有从真实内网数据库抓取 DDL，也没有复现其浏览器 `Failed to fetch`。
- 没有测内网 worker 的死亡信号、发送者、OOM、RSS 峰值或 GC 停顿。
- 没有运行 6000 表 AST 或索引快照压测；2.682 GiB 是明确条件下的公式下界。
- 没有实施/验证正式增量索引修复，也没有运行全量产品回归或发布验证。
- first-seen 反例针对现有 R035 全历史语义，不是对未来重新定义业务规范的授权。

本附件可用于复现代码缺陷和审核拟议方案；不应作为真实大库修复完成的验收单。
