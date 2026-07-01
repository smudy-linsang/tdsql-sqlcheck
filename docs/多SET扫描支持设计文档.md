# TDSQL 多 SET 扫描支持设计文档

**版本：** V1.0
**日期：** 2026-06-30
**作者：** TDSQL-SQLCheck 团队

---

## 一、背景与问题

### 1.1 问题描述

当前系统的慢 SQL 扫描功能通过 TDSQL Proxy 单点连接查询 `performance_schema.events_statements_summary_by_digest` 和 `information_schema.processlist`（多次轮询模式）。Proxy层的performance_schema自动聚合所有SET的SQL执行数据，因此通过Proxy查询即可获取全局慢SQL统计。

> **架构说明**: `mysql.slow_log` 在TDSQL分布式实例中各SET上不记录数据，慢日志由Proxy层统一管理（写入本地文件由赤兔平台收集），因此本系统不使用slow_log作为数据源。

### 1.2 TDSQL 多 SET 架构

TDSQL 分布式实例由 N 个 SET（分片）组成，每个 SET 是一个独立的 MySQL 主从复制组。Proxy（ODP）负责根据分片键值路由 SQL 到对应 SET。

**关键机制**：

| 机制 | 语法 | 说明 |
|------|------|------|
| SET 发现 | `/*proxy*/show status` | 通过 Proxy 命令查看所有 SET 名称和状态 |
| 指定 SET 路由 | `/*sets:set_1*/` | SQL 仅发送到 set_1 |
| 多 SET 路由 | `/*sets:set_1,set_2*/` | SQL 发送到多个指定 SET |
| 全 SET 路由 | `/*sets:allsets*/` | SQL 广播到所有 SET |
| Shardkey 路由 | `/*shardkey:value*/` | 根据 shardkey 值路由 |

**SET 内部系统表**：每个 SET 的 `performance_schema`、`information_schema.processlist` 都是独立的，但Proxy层的performance_schema会自动聚合所有SET的SQL执行统计数据。`mysql.slow_log` 在SET上不记录数据（已废弃）。

### 1.3 设计目标

1. 自动发现 TDSQL 分布式实例的所有 SET
2. 遍历每个 SET 执行三个数据源的扫描
3. 扫描结果标记来源 SET，支持 SET 维度筛选和对比
4. 提供跨 SET 顾问分析（分片不均、SET 热点等）

---

## 二、架构设计

### 2.1 整体方案

```
用户发起扫描
     │
     ▼
┌─────────────────────────────────────┐
│  1. SET 发现                        │
│  /*proxy*/show status → SET 列表    │
│  [set_1, set_2, ..., set_N]         │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│  2. 遍历 SET 扫描                    │
│  for each set in SETs:              │
│    /*sets:set_N*/ SELECT ...         │
│    → 每条结果标记 set_id             │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│  3. 结果聚合 + 跨 SET 分析           │
│  - 按 SQL 指纹 + SET 分组对比        │
│  - 识别 SET 热点（慢 SQL 集中分布）   │
│  - 识别分片不均（某 SET 负载远超其他）│
└─────────────────────────────────────┘
```

### 2.2 SET 发现机制

在 `TDSQLConnectionPool` 中新增方法：

```python
def discover_sets(self) -> list[dict]:
    """发现 TDSQL 分布式实例的所有 SET"""
    # 方式1: /*proxy*/show status（TDSQL Proxy 命令）
    rows = self._execute("/*proxy*/show status")
    # 解析返回的 SET 信息，提取 set_name
    # 方式2（回退）: information_schema.TDSQL_SHARDING_RULES 查看分片信息
    # 返回: [{"set_id": "set_1", "set_name": "set_1"}, ...]
```

### 2.3 多 SET 遍历扫描

每个数据源的查询增加 `/*sets:set_N*/` 前缀：

```python
def get_slow_queries_from_digest(self, set_id: str = None, ...):
    """性能摘要扫描（Proxy层自动聚合，无需指定SET）"""
    sql = "SELECT SCHEMA_NAME, DIGEST, DIGEST_TEXT, ... FROM performance_schema.events_statements_summary_by_digest ..."
    
# NOTE: get_slow_queries_from_slow_log() 已移除
# TDSQL分布式实例中SET实例的mysql.slow_log表不记录数据
    
def poll_processlist(self, duration_seconds=10.0, interval=1.0, min_time=0.1):
    """实时进程轮询扫描（多次采样合并去重）"""
    # 在duration_seconds内每隔interval秒查询一次processlist
    sql = "SELECT ... FROM information_schema.processlist ..."
```

### 2.4 数据结构变更

#### SlowQueryRecord（慢SQL记录）

```python
@dataclass
class SlowQueryRecord:
    ...
    set_id: str = ""           # 新增：来源 SET 标识（如 "set_1"）
```

#### scan_tasks 表（扫描任务）

```sql
ALTER TABLE scan_tasks ADD COLUMN sets_discovered TEXT DEFAULT '[]';  -- 发现的 SET 列表
ALTER TABLE scan_tasks ADD COLUMN set_count INTEGER DEFAULT 0;       -- SET 数量
```

#### slow_queries 表（慢SQL记录）

```sql
ALTER TABLE slow_queries ADD COLUMN set_id TEXT DEFAULT '';  -- 来源 SET
```

### 2.5 API 变更

#### 新增 API：获取 SET 列表

```
GET /api/v1/tdsql/sets
→ {"sets": [{"set_id": "set_1", "set_name": "set_1"}, ...], "total": 4}
```

#### 增强现有 API：慢SQL抓取

```
POST /api/v1/tdsql/slow-queries/fetch
Body 新增字段:
  scan_all_sets: bool = true   # 是否扫描所有 SET
  set_ids: list[str] = []       # 指定 SET 列表（为空则扫描全部）

响应新增字段:
  sets_scanned: ["set_1", "set_2", ...]
  set_count: 4
  results: [{..., "set_id": "set_1"}, ...]
```

#### 新增 API：跨 SET 对比分析

```
GET /api/v1/slow-queries/cross-set-analysis?scan_task_id=123
→ {
    "set_distribution": {
      "set_1": {"total": 15, "error": 3, "warning": 12},
      "set_2": {"total": 8, "error": 1, "warning": 7},
      ...
    },
    "hot_sets": ["set_1", "set_3"],   # 慢 SQL 最多的 SET
    "cross_set_sqls": [...],          # 在多个 SET 上都出现的慢 SQL
    "advice": "SET set_1 的慢 SQL 数量（15条）远超平均水平（8条），建议检查该 SET 的数据分布是否倾斜..."
  }
```

### 2.6 前端变更

1. **扫描表单**：新增"扫描所有 SET"选项（默认开启），显示发现的 SET 列表
2. **扫描结果列表**：新增 SET 列，支持按 SET 筛选
3. **扫描任务详情**：显示扫描了哪些 SET，各 SET 的慢 SQL 分布
4. **跨 SET 对比视图**：柱状图展示各 SET 的慢 SQL 分布，标记热点 SET

---

## 三、文件变更清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `backend/services/tdsql_connector.py` | 修改 | 新增 `discover_sets()`，三个扫描方法增加 `set_id` 参数 |
| `backend/engine/slow_analyzer.py` | 修改 | `SlowQueryRecord` 增加 `set_id` 字段 |
| `backend/services/slow_query_service.py` | 修改 | `init_db()` 增加 SET 列，新增跨 SET 分析方法 |
| `backend/api/tdsql_manage.py` | 修改 | 新增 `/sets` 端点，增强 `/slow-queries/fetch` |
| `backend/api/slow_query.py` | 修改 | 新增 `/cross-set-analysis` 端点 |
| `frontend/index.html` | 修改 | 扫描表单、结果列表、任务详情增强 |

---

## 四、测试计划

### 4.1 冒烟测试
- 连接公有云 TDSQL 实例（119.45.220.89:15005）
- 验证 `/*proxy*/show status` 能返回 SET 列表
- 逐 SET 执行三个数据源扫描，验证结果正确标记 set_id

### 4.2 SIT 测试
- 多 SET 扫描全链路：发现 SET → 遍历扫描 → 结果聚合 → 跨 SET 分析
- 单 SET 扫描：指定单个 SET 扫描
- 无 SET 场景：非分布式实例（集中式）的兼容性

### 4.3 UAT 测试
- 前端扫描表单交互
- 结果列表 SET 筛选
- 扫描任务详情 SET 分布展示
- 跨 SET 对比视图
