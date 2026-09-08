# TDSQL SQL审核工具 - 在线元数据审核"Failed to fetch"问题诊断报告

**版本**: v1.6.3.4  
**日期**: 2025-09-07  
**环境**: 10.243.16.252 (测试环境)  
**数据库**: 总账系统-集中式-开发环境（种子环境）-15064-sungl_am (6000+ 表)  
**问题现象**: 点击"拉取元数据并执行文件审核"后，等待约 3 分 40 秒，页面弹出"提取失败: Failed to fetch"

---

## 一、问题摘要

| 项目 | 详情 |
|------|------|
| **影响功能** | SQL审核 > 在线元数据审核 > 拉取元数据并执行文件审核 |
| **触发条件** | 对包含 6000+ 张表的数据库执行元数据拉取 |
| **错误信息** | `提取失败: Failed to fetch` |
| **耗时约** | 220 秒（16:08:30 ~ 16:12:10） |
| **历史版本** | v1.6.3.0 版本正常，v1.6.3.4 版本失败 |

---

## 二、根因分析

### 2.1 架构调用链

```
前端 (Vue.js + fetch)
  |
  +--> apiFetch() [无超时设置]
        |
        +--> Nginx反向代理 [proxy_read_timeout: 120s]
              |
              +--> FastAPI (uvicorn) [无显式超时]
                    |
                    +--> extract_and_audit() API 端点
                          |
                          +--> 步骤1: 从 information_schema.TABLES 获取表清单
                          +--> 步骤2: 逐表调用 SHOW CREATE TABLE / SHOW CREATE VIEW
                          +--> 步骤3: 生成 .sql 文件
                          +--> 步骤4: 调用 audit_file_content() 审核
                          +--> 步骤5: 持久化至 audit_history 表
                          +--> 步骤6: 旁路生成对比快照 (schema_audit)
```

### 2.2 核心瓶颈

通过对 v1.6.3.0 和 v1.6.3.4 版本代码的逐行对比分析，发现 **v1.6.3.4 版本新增了以下处理逻辑**，成为问题的核心瓶颈：

#### 瓶颈 1: `capture_report_context()` 调用（v1.6.3.4 新增）

**位置**: `backend/api/sql_audit.py:278`

```python
# v1.6.3.4 新增（D02 需求）
from backend.services.report_context import capture_report_context, ORIGIN_BOUND
_report_ctx = capture_report_context(connection_id, database_name, ORIGIN_BOUND)
```

该函数在元数据提取前冻结实例连接名称，涉及 JSON 序列化、HTML 转义、6 KiB 大小校验等逻辑。

#### 瓶颈 2: 快照创建时新增 `report_context_json` 字段（v1.6.3.4 新增）

**位置**: `backend/api/sql_audit.py:385-388`

```python
# v1.6.3.4 新增（H05a）
"report_context_json": context_to_json_column(_report_ctx),
```

此字段将完整的上下文序列化后写入 `scan_snapshots.context_json` 列（VARCHAR(4096)）。

#### 瓶颈 3: 逐表 DDL 拉取（O(n) 复杂度）

**位置**: `backend/api/sql_audit.py:316-345`

```python
for obj in db_objects:  # 6000+ 次循环
    if "TABLE" in scopes and "VIEW" not in obj_type.upper():
        cursor.execute(f"SHOW CREATE TABLE `{target_db}`.`{obj_name}`")
        # ... 解析 DDL
```

**问题分析**:
- 对 6000+ 张表，需执行 **6000+ 次** `SHOW CREATE TABLE` 查询
- 每次查询涉及网络往返（RTT）、MySQL 解析 DDL、Python 字符串拼接
- 若单张表 DDL 平均 5KB，6000 张表 = **30MB** 数据量
- 总耗时预估: `6000 * (RTT + DDL解析时间 + 字符串处理)` ≈ **180~240秒**

### 2.3 超时触发链路

```
总耗时: ~220秒
  |
  +---> Nginx proxy_read_timeout: 120s  <-- 首次超时触发！
  |       返回 504 Gateway Timeout
  |
  +---> Fetch API 收到连接中断
            |
            +--> "Failed to fetch" 错误 ← 用户看到的提示
```

**关键发现**: `proxy_read_timeout` 设置为 **120s**，但实际请求在 **~220s** 才报错。这说明：

1. Nginx 的 120s 超时确实会触发 504 错误
2. 但 "Failed to fetch" 是浏览器 Fetch API 对跨域/连接中断的笼统错误
3. 实际超时可能由 **Nginx → Fetch → 前端 error catch** 多层传递造成

### 2.4 v1.6.3.0 vs v1.6.3.4 差异对比

| 差异点 | v1.6.3.0 | v1.6.3.4 | 影响 |
|--------|----------|----------|------|
| `capture_report_context()` | **无** | **有** (新增) | 增加 JSON 序列化开销 |
| 快照 `report_context_json` | **无** | **有** (新增) | 增加数据库写入量 |
| 表 DDL 拉取逻辑 | 相同 | 相同 | O(n) 循环，6000+ 次查询 |
| Nginx `proxy_read_timeout` | 120s | 120s | **瓶颈** |
| 前端 `apiFetch` 超时 | 无 | 无 | **隐患** |
| `_scope_fields()` 响应 | 直接展开 | 通过函数生成 | 无影响 |

---

## 三、代码级优化方案

### 优化 1: 为 Nginx `/extract-and-audit` 路由单独配置超时（立即可用）

**文件**: `/opt/tdsql-sqlcheck/releases/v1.6.3.4/deploy/nginx-sqlcheck.conf`

```nginx
# 新增 location 块（与 /api/v1/gateway-log/upload 同级）
location = /api/v1/audit/extract-and-audit {
    # 6000 张表 * 平均 50ms/表 = 300s，预留 5 倍余量
    client_max_body_size 20m;
    proxy_read_timeout 900s;    # 15 分钟
    proxy_send_timeout 900s;    # 15 分钟
    client_body_timeout 60s;

    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

**应用命令**:
```bash
nginx -t && systemctl reload nginx
```

**预期效果**: 临时解决 504 超时问题，但处理时间仍可能超过 3-4 分钟。

---

### 优化 2: 前端 `apiFetch` 添加超时控制（中优先级）

**文件**: `/opt/tdsql-sqlcheck/releases/v1.6.3.4/frontend/static/js/app.js`

**当前代码** (第 30-52 行):
```javascript
async function apiFetch(url, options={}) {
  const opts = Object.assign({}, options);
  // ... 认证逻辑
  const resp = await fetch(finalUrl, opts);
  // ... 错误处理
  return resp;
}
```

**修改后**:
```javascript
async function apiFetch(url, options={}) {
  const opts = Object.assign({}, options);
  // ... 认证逻辑

  // 新增: 超时控制（默认 5 分钟，extract-and-audit 可覆盖为 15 分钟）
  const timeoutMs = opts.timeout || 300000;
  delete opts.timeout;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  opts.signal = controller.signal;

  try {
    const resp = await fetch(finalUrl, opts);
    // ... 错误处理
    return resp;
  } finally {
    clearTimeout(timer);
  }
}
```

**调用方修改** (`runExtractAndAudit` 函数，第 1711 行):
```javascript
const resp = await apiFetch(`${API_BASE}/api/v1/audit/extract-and-audit`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  timeout: 900000,  // 15 分钟
  body: JSON.stringify({ ... })
});
```

---

### 优化 3: 后端元数据拉取增加进度反馈（中优先级）

**核心思路**: 将同步处理改为 **异步任务 + WebSocket/SSE 推送进度**

**当前代码** (同步阻塞):
```python
@router.post("/extract-and-audit")
async def extract_and_audit(payload: dict):
    # 1. 逐表拉取 DDL (6000+ 次查询)
    for obj in db_objects:
        cursor.execute(f"SHOW CREATE TABLE ...")
    # 2. 生成 .sql 文件
    # 3. 审核
    # 4. 返回结果（前端一直等待）
```

**修改建议** (异步任务):
```python
from fastapi.responses import StreamingResponse
import asyncio

@router.post("/extract-and-audit")
async def extract_and_audit(payload: dict):
    connection_id = payload.get("connection_id")
    database_name = payload.get("database")

    # 1. 创建异步任务
    task_id = str(uuid.uuid4())
    task_queue.put(task_id, {
        "status": "queued",
        "progress": 0,
        "total": 0,
        "current_table": "",
    })

    # 2. 后台异步执行
    asyncio.create_task(_background_extract(task_id, connection_id, database_name))

    # 3. 返回 task_id
    return {"task_id": task_id, "status": "queued"}


async def _background_extract(task_id, connection_id, database_name):
    """后台任务：逐表拉取并更新进度"""
    task_queue.update(task_id, {"status": "running", "progress": 0})

    with pool.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT TABLE_NAME, TABLE_TYPE FROM information_schema.TABLES ...")
        total = len(db_objects)
        task_queue.update(task_id, {"total": total})

        for idx, obj in enumerate(db_objects):
            task_queue.update(task_id, {
                "current_table": obj["TABLE_NAME"],
                "progress": idx + 1,
            })
            # 逐表拉取 DDL...

    task_queue.update(task_id, {"status": "completed"})
```

**前端配合** (轮询进度):
```javascript
// 提交任务
const { task_id } = await apiFetch('/api/v1/audit/extract-and-audit', {...});

// 轮询进度
const pollProgress = setInterval(async () => {
  const progress = await apiFetch(`/api/v1/audit/extract-progress/${task_id}`);
  if (progress.status === 'completed') {
    clearInterval(pollProgress);
    // 获取结果
  } else {
    // 更新 UI 进度条
    progressBar.value = progress.progress / progress.total * 100;
  }
}, 2000);
```

---

### 优化 4: 数据库查询优化 - 批量获取 DDL（高优先级）

**当前问题**: 6000+ 次 `SHOW CREATE TABLE` 串行查询

**优化方案**: 使用 `information_schema` 批量获取

```python
# 替换逐表 SHOW CREATE TABLE
cursor.execute("""
    SELECT TABLE_NAME, CREATE_TABLE
    FROM information_schema.TABLES
    LEFT JOIN (
        SELECT TABLE_NAME, GROUP_CONCAT(DISTINCT t.Sql_Command ORDER BY r.ROUTINE_NAME SEPARATOR ';') AS CREATE_TABLE
        FROM information_schema.ROUTINES r
        JOIN information_schema.ROUTINES t ON r.ROUTINE_SCHEMA = t.ROUTINE_SCHEMA
        WHERE r.ROUTINE_SCHEMA = %s AND r.ROUTINE_TYPE = 'PROCEDURE'
        GROUP BY r.ROUTINE_SCHEMA, r.ROUTINE_NAME
    ) proc ON TABLES.TABLE_NAME = proc.TABLE_NAME
    WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE'
""", (target_db, target_db))
```

**或使用 `mysqldump` 命令并行提取**:
```python
import subprocess

result = subprocess.run([
    'mysqldump',
    f'--host={host}',
    f'--port={port}',
    f'--user={user}',
    f'--password={passwd}',
    '--no-data',
    '--skip-comments',
    target_db
], capture_output=True, text=True, timeout=600)

full_ddl = result.stdout
# 解析 full_ddl 为多个 CREATE TABLE 语句
```

**预期效果**: 从 6000+ 次网络往返降低到 **1 次** 命令执行，耗时从 ~200s 降至 **~10s**。

---

### 优化 5: 数据库连接池与超时配置

**文件**: `/opt/tdsql-sqlcheck/releases/v1.6.3.4/backend/config.py`

```python
# 新增配置项
METADATA_EXTRACT_TIMEOUT = int(os.getenv("METADATA_EXTRACT_TIMEOUT", "600"))  # 默认 10 分钟
DATABASE_QUERY_TIMEOUT = int(os.getenv("DATABASE_QUERY_TIMEOUT", "30"))       # 单条查询超时
CONNECTION_POOL_SIZE = int(os.getenv("CONNECTION_POOL_SIZE", "20"))            # 连接池大小
CONNECTION_POOL_IDLE_TIMEOUT = int(os.getenv("CONNECTION_POOL_IDLE_TIMEOUT", "300"))  # 空闲回收
```

**文件**: `/opt/tdsql-sqlcheck/releases/v1.6.3.4/backend/services/connection_registry.py`

```python
class TDSQLConnectionPool:
    def __init__(self, ..., pool_size=20, idle_timeout=300):
        self._pool = asyncio.Semaphore(pool_size)
        self._idle_timeout = idle_timeout
```

---

### 优化 6: 前端 UI 增加进度提示与防重复提交

**文件**: `/opt/tdsql-sqlcheck/releases/v1.6.3.4/frontend/static/js/app.js`

**当前代码** (第 1708-1728 行):
```javascript
const runExtractAndAudit = async () => {
  if (!extractedAuditConnId.value) {
    ElementPlus.ElMessage.warning('请先选择目标实例'); return
  }
  extractAuditing.value = true;  // 仅一个布尔值
  try {
    const resp = await apiFetch(...);
    // ...
  } catch(e) {
    ElementPlus.ElMessage.error('提取失败: ' + e.message);  // 笼统错误
  } finally {
    extractAuditing.value = false;
  }
};
```

**修改建议**:
```javascript
const runExtractAndAudit = async () => {
  if (!extractedAuditConnId.value) {
    ElementPlus.ElMessage.warning('请先选择目标实例'); return
  }

  // 防重复提交
  if (extractAuditing.value) return;
  extractAuditing.value = true;

  // 新增: 进度对话框
  const progress = ElementPlus.ElMessageBox({
    title: '正在拉取元数据',
    message: h('div', [
      h('p', `已处理: ${extractedProgress.value.current} / ${extractedProgress.value.total}`),
      h('el-progress', {
        percentage: extractedProgress.value.percent,
        strokeWidth: 16,
      }),
      h('p', { class: 'text-sm text-gray-500' }, `当前表: ${extractedProgress.value.current_table || '无'}`),
    ]),
    showConfirmButton: false,
    customClass: 'progress-dialog',
  });

  try {
    const resp = await apiFetch(`${API_BASE}/api/v1/audit/extract-and-audit`, {
      method: 'POST',
      timeout: 900000,  // 15 分钟
      body: JSON.stringify({...})
    });

    if (resp.ok) {
      const d = await resp.json();
      extractedResult.value = d;
      ElementPlus.ElMessage.success('成功从 SIT/UAT 数据库提取在线元数据并完成文件规则审核');
      progress.close();
    } else {
      // ...
    }
  } catch(e) {
    // 区分超时错误和其他错误
    if (e.name === 'AbortError') {
      ElementPlus.ElMessage.error('提取超时：目标库表数量过多，请联系管理员调整 Nginx 超时配置');
    } else {
      ElementPlus.ElMessage.error('提取失败: ' + e.message);
    }
    progress.close();
  } finally {
    extractAuditing.value = false;
  }
};
```

---

## 四、优化方案优先级与预期效果

| 优先级 | 优化方案 | 实施难度 | 预期效果 | 建议实施周期 |
|--------|----------|----------|----------|--------------|
| **P0** | Nginx 为 `/extract-and-audit` 单独配置超时 | 低 | 消除 504 错误 | 1 小时 |
| **P1** | 前端 `apiFetch` 添加超时控制 | 低 | 精确控制超时，友好提示 | 2 小时 |
| **P1** | 数据库查询优化 - 批量获取 DDL | 中 | 耗时从 200s 降至 10s | 1 天 |
| **P2** | 后端异步任务 + 进度反馈 | 中 | 用户体验大幅提升 | 2-3 天 |
| **P2** | 前端 UI 增加进度条与防重复提交 | 低 | 用户体验提升 | 半天 |
| **P3** | 数据库连接池与超时配置 | 低 | 稳定性提升 | 半天 |

---

## 五、推荐实施路径

### 阶段一: 立即缓解（1-2 小时）

1. **修改 Nginx 配置**，为 `/api/v1/audit/extract-and-audit` 添加 900s 超时
2. **修改前端 `apiFetch`**，添加 AbortController 超时控制
3. **重新部署** Nginx + 前端

**预期效果**: 6000 张表场景下不再出现 "Failed to fetch"，但处理时间仍约 3-4 分钟。

### 阶段二: 中期优化（1 周）

1. **批量获取 DDL**，将 6000+ 次串行查询改为 1 次 `mysqldump` 或 `information_schema` 查询
2. **前端进度提示**，增加加载动画和预计剩余时间
3. **数据库连接池调优**

**预期效果**: 处理时间从 3-4 分钟降至 10-20 秒。

### 阶段三: 长期改进（2-3 周）

1. **异步任务架构**，支持后台处理 + WebSocket 推送
2. **分批次拉取**，支持用户选择只拉取特定类型的表
3. **缓存机制**，避免重复拉取未变化的元数据

**预期效果**: 用户体验接近即时响应，支持 10 万+ 张表的超大库场景。

---

## 六、配套修改文件清单

| 文件路径 | 修改内容 | 优先级 |
|----------|----------|--------|
| `/opt/tdsql-sqlcheck/releases/v1.6.3.4/deploy/nginx-sqlcheck.conf` | 新增 `/api/v1/audit/extract-and-audit` location 块 | P0 |
| `/opt/tdsql-sqlcheck/releases/v1.6.3.4/frontend/static/js/app.js` | `apiFetch` 添加超时控制、`runExtractAndAudit` 增加进度提示 | P0-P2 |
| `/opt/tdsql-sqlcheck/releases/v1.6.3.4/backend/api/sql_audit.py` | 批量 DDL 拉取逻辑 | P1 |
| `/opt/tdsql-sqlcheck/releases/v1.6.3.4/backend/config.py` | 新增超时与连接池配置项 | P3 |
| `/opt/tdsql-sqlcheck/releases/v1.6.3.4/backend/services/connection_registry.py` | 连接池参数可配置化 | P3 |

---

## 七、验证测试建议

### 测试场景

| 场景 | 表数量 | 预期耗时 | 预期行为 |
|------|--------|----------|----------|
| 小库测试 | < 100 | < 5s | 正常返回 |
| 中库测试 | 100 ~ 1000 | 5s ~ 30s | 正常返回 |
| 大库测试 | 1000 ~ 5000 | 30s ~ 120s | 正常返回，前端显示进度 |
| 超大库测试 | 5000 ~ 10000 | 120s ~ 300s | **需要 P0 优化**，否则仍超时 |
| 极限测试 | > 10000 | > 300s | **需要 P1+ 优化** |

### 测试步骤

1. **阶段一验证** (P0):
   ```bash
   # 修改 Nginx 配置后
   nginx -t && systemctl reload nginx

   # 模拟 6000 张表场景
   # 前端操作: SQL审核 > 在线元数据审核 > 选择目标库 > 点击"拉取元数据"
   # 预期: 不再出现 504 或 "Failed to fetch"，等待 3-4 分钟后返回成功
   ```

2. **阶段二验证** (P1):
   ```bash
   # 使用 mysqldump 批量拉取
   time mysqldump --host=xxx --port=xxx --user=xxx --password=xxx --no-data target_db > /dev/null
   # 预期: < 15s (vs. 原 6000 次 SHOW CREATE TABLE ~ 200s)
   ```

3. **阶段三验证** (P2):
   ```bash
   # 测试异步任务接口
   curl -X POST http://localhost:8000/api/v1/audit/extract-and-audit \
     -H "Content-Type: application/json" \
     -d '{"connection_id":"xxx","database":"target_db"}'
   # 预期: 立即返回 {"task_id":"xxx","status":"queued"}

   # 轮询进度
   curl http://localhost:8000/api/v1/audit/extract-progress/xxx
   # 预期: {"status":"running","progress":1234,"total":6000,"current_table":"t_1234"}
   ```

---

## 八、附录

### A. 关键代码位置

| 代码 | 文件 | 行号 |
|------|------|------|
| 元数据拉取 API 端点 | `backend/api/sql_audit.py` | 254-426 |
| 逐表 DDL 拉取循环 | `backend/api/sql_audit.py` | 316-345 |
| v1.6.3.4 新增: `capture_report_context` | `backend/api/sql_audit.py` | 278 |
| v1.6.3.4 新增: `report_context_json` | `backend/api/sql_audit.py` | 387 |
| 文件审核入口 | `backend/services/audit_service.py` | 287-337 |
| 前端 `apiFetch` 定义 | `frontend/static/js/app.js` | 30-52 |
| 前端 `runExtractAndAudit` 调用 | `frontend/static/js/app.js` | 1708-1728 |
| Nginx 配置 | `deploy/nginx-sqlcheck.conf` | 42-46 |

### B. Nginx 超时参数说明

| 参数 | 含义 | 当前值 | 建议值 |
|------|------|--------|--------|
| `proxy_read_timeout` | Nginx 等待上游（FastAPI）响应的时间 | 120s | 900s (仅针对 `/extract-and-audit`) |
| `proxy_send_timeout` | Nginx 向上传递请求体的超时 | 120s | 900s |
| `client_body_timeout` | Nginx 等待客户端发送请求体的时间 | 默认 60s | 60s（不变） |

**注意**: `proxy_read_timeout` **不是** 整个请求的硬超时，而是**相邻两次读操作**的空闲超时。如果 FastAPI 持续返回数据（如 `StreamingResponse`），Nginx 不会中断。但 `extract-and-audit` 使用同步返回，数据在结束时一次性返回，因此受此限制。

### C. 相关文件 diff 摘要

```diff
--- v1.6.3.0/backend/api/sql_audit.py
+++ v1.6.3.4/backend/api/sql_audit.py
@@ -275,6 +275,9 @@
     except Exception as e:
         raise HTTPException(status_code=400, detail=f"无法连接选定的数据库实例: {str(e)}")

+    # v1.6.3.4 / D02（H02，§3.1）新增
+    from backend.services.report_context import capture_report_context, ORIGIN_BOUND
+    _report_ctx = capture_report_context(connection_id, database_name, ORIGIN_BOUND)

     try:
         from backend.connectors.metadata_fetcher import MetadataFetcher
@@ -379,7 +382,9 @@
                 "scan_label": filename,
                 "scan_started_at": _started_at,
                 "scan_finished_at": datetime.now().isoformat(),
                 "created_by": _operator(http_request),
                 "rule_set_id": _rule_set_id,
                 "instance_type": ictx.instance_type.value,
+                # v1.6.3.4 / D02（H05a）新增
+                "report_context_json": context_to_json_column(_report_ctx),
             }, _items, _obj_total)
```

### D. 性能估算模型

```
总耗时 = 表查询耗时 + 逐表 DDL 拉取耗时 + 文件生成耗时 + 审核耗时 + 持久化耗时

对于 6000 张表的数据库:
  - 表查询耗时:    ~0.5s   (1 次查询)
  - 逐表 DDL:     ~180-220s (6000 次 * 30-35ms/次，含网络 RTT)
  - 文件生成:     ~2s     (Python 字符串拼接)
  - 审核耗时:     ~5s     (77 条规则 * 6000 SQL)
  - 持久化:       ~2s     (INSERT audit_history + INSERT scan_snapshots)

总计: ~190-230 秒

批量优化后:
  - mysqldump:    ~8-12s  (1 次命令)
  - DDL 解析:     ~2s     (Python 正则解析)
  - 文件生成:     ~2s
  - 审核耗时:     ~5s
  - 持久化:       ~2s

总计: ~19-23 秒
```

---

**报告生成时间**: 2025-09-07  
**适用版本**: v1.6.3.4  
**问题等级**: P1 (影响核心功能可用性)  
**建议解决期限**: 2 周内完成 P0+P1，1 个月内完成 P2
