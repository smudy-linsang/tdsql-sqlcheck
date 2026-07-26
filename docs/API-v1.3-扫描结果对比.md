# API-v1.3-扫描结果对比 (接口说明书)

> **版本**：v1.3.0  
> **基础路径**：`/api/v1/compare`  
> **认证方式**：Bearer Token (Header: `Authorization: Bearer <token>`)

---

## 1. 接口概览 (API Summary)

| 接口 Endpoint | 请求方式 | 功能说明 | 访问权限 |
| :--- | :--- | :--- | :--- |
| `/api/v1/compare/snapshots` | `GET` | 按实例与领域查询可对比的快照历史列表 | 全角色 |
| `/api/v1/compare/snapshots` | `POST` | 传入 2 个快照 ID，执行二元对比并生成对比分析报告 JSON | 全角色 |
| `/api/v1/compare/snapshots/export-html` | `POST` | 传入对比结果 JSON，导出离线 HTML 比对报告网页 | 全角色 |

---

## 2. 详细接口规范 (Detailed Specification)

### 2.1 查询实例历史快照列表
- **Endpoint**: `GET /api/v1/compare/snapshots`
- **Query Parameters**:
  - `domain` (string, required): 治理领域 (`schema_audit` | `slow_query` | `bigtable`)
  - `connection_id` (string, optional): 目标数据库连接 ID (为空则查全量)
  - `limit` (int, default=20): 分页条数

- **Response (200 OK)**:
```json
{
  "total": 5,
  "snapshots": [
    {
      "id": "snap_20260715_001",
      "domain": "schema_audit",
      "connection_id": "sit_db_01",
      "snapshot_name": "2026-07-15 14:00 元数据巡检",
      "total_issues": 28,
      "created_at": "2026-07-15T14:00:00Z",
      "created_by": "admin"
    },
    {
      "id": "snap_20260701_001",
      "domain": "schema_audit",
      "connection_id": "sit_db_01",
      "snapshot_name": "2026-07-01 10:00 元数据巡检",
      "total_issues": 35,
      "created_at": "2026-07-01T10:00:00Z",
      "created_by": "admin"
    }
  ]
}
```

---

### 2.2 执行双节点历史快照对比
- **Endpoint**: `POST /api/v1/compare/snapshots`
- **Request Body**:
```json
{
  "base_snapshot_id": "snap_20260701_001",
  "target_snapshot_id": "snap_20260715_001"
}
```

- **Response (200 OK)**:
```json
{
  "domain": "schema_audit",
  "connection_id": "sit_db_01",
  "base_snapshot": {
    "id": "snap_20260701_001",
    "name": "2026-07-01 10:00 元数据巡检",
    "total_issues": 35
  },
  "target_snapshot": {
    "id": "snap_20260715_001",
    "name": "2026-07-15 14:00 元数据巡检",
    "total_issues": 28
  },
  "kpi_summary": {
    "base_total": 35,
    "target_total": 28,
    "fixed_count": 12,
    "new_count": 5,
    "remaining_count": 23,
    "fix_rate_pct": 34.29,
    "net_change": -7
  },
  "diff_details": {
    "fixed": [
      {
        "key": "sit_db_01:t_order:R012",
        "node": "set_1782132369_1",
        "table_name": "t_order",
        "rule_id": "R012",
        "description": "表缺少主键定义"
      }
    ],
    "new": [
      {
        "key": "sit_db_01:t_pay:R005",
        "node": "set_1782132369_2",
        "table_name": "t_pay",
        "rule_id": "R005",
        "description": "禁止使用 FLOAT/DOUBLE 类型"
      }
    ],
    "remaining": [
      {
        "key": "sit_db_01:t_user:R020",
        "node": "set_1782132369_1",
        "table_name": "t_user",
        "rule_id": "R020",
        "description": "单表索引过多(>=5)",
        "trend_status": "UNCHANGED"
      },
      {
        "key": "sit_db_01:t_log:R035",
        "node": "set_1782132369_1",
        "table_name": "t_log",
        "rule_id": "R035",
        "description": "大表扫描",
        "trend_status": "DEGRADED",
        "trend_diff": "Avg time increased by 120ms"
      }
    ]
  }
}
```

- **Error Response (400 Bad Request)**:
```json
{
  "detail": "参数错误：必须提供且仅提供 2 个对比快照 ID (base_snapshot_id 与 target_snapshot_id)"
}
```
