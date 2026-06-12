# TDSQL SQL审核工具

覆盖开发、测试、生产全生命周期的SQL质量管控与慢SQL分析工具。

## 功能特性

### 📝 SQL审核（22条规则）

基于《TDSQL数据库开发规范》构建的审核规则库：

| 类别 | 规则数 | 说明 |
|------|--------|------|
| 命名规范 | 2条 | 表名长度/格式、保留关键字 |
| DDL规范 | 9条 | 主键、引擎、字符集、字段类型等 |
| DML规范 | 8条 | SELECT*、WHERE条件、子查询、索引等 |
| 分布式规范 | 3条 | 分片键检查、禁止更新分片键 |

### 🐌 慢SQL分析

基于《TDSQL-MySQL慢查询发现与优化方案》：

- EXPLAIN执行计划自动分析
- 问题诊断：全表扫描、缺失索引、文件排序、深度分页等
- 优化建议自动生成
- SQL文本静态分析

### 🔗 GitLab集成

- Merge Request Webhook自动审核
- Git Diff中的SQL变更检测
- 仓库级别SQL文件批量审核
- 自动生成审核报告评论

### 🗄️ TDSQL管理

- 连接TDSQL实例获取元数据
- 分片键信息自动识别
- 从TDSQL抓取慢查询（digest/slow_log/processlist）
- 字符集一致性检查
- 大表检查（L1/L2/L3分级）

### 📊 Dashboard

- 审核统计概览
- 慢SQL TopN排行
- 规则分布图表

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python 3.11+ / FastAPI |
| SQL解析 | sqlglot |
| 数据库连接 | pymysql |
| 前端 | Vue 3 / Element Plus / ECharts |
| 存储 | SQLite（轻量级） |

## 快速开始

### 1. 安装依赖

```bash
cd TDSQL-SQLCheck
pip install -r requirements.txt
```

### 2. 启动服务

```bash
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

### 3. 访问

- **前端界面**: http://localhost:8000
- **API文档**: http://localhost:8000/docs
- **健康检查**: http://localhost:8000/health

## API接口

### SQL审核

```bash
# 单条SQL审核
curl -X POST http://localhost:8000/api/v1/audit/sql \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT * FROM t_user ORDER BY RAND()", "db_type": "tdsql"}'

# 文件审核
curl -X POST http://localhost:8000/api/v1/audit/file \
  -H "Content-Type: application/json" \
  -d '{"content": "SELECT * FROM t_user", "file_path": "test.sql"}'
```

### 慢SQL分析

```bash
# 添加慢SQL并分析
curl -X POST http://localhost:8000/api/v1/slow-queries \
  -H "Content-Type: application/json" \
  -d '{
    "fingerprint": "SELECT * FROM t_order WHERE user_id = ?",
    "sql_text": "SELECT * FROM t_order WHERE user_id = 123",
    "exec_count": 5000, "avg_time_ms": 200,
    "rows_examined": 850000, "rows_sent": 100
  }'

# EXPLAIN分析
curl -X POST http://localhost:8000/api/v1/slow-queries/analyze-explain \
  -H "Content-Type: application/json" \
  -d '{"explain_data": [{"type": "ALL", "rows": 850000, "extra": "Using where"}]}'
```

### GitLab集成

```bash
# 审核Git Diff
curl -X POST http://localhost:8000/api/v1/gitlab/audit/diff \
  -H "Content-Type: application/json" \
  -d '{"diff": "+SELECT * FROM t_user", "file_path": "UserMapper.xml"}'

# 审核仓库文件
curl -X POST http://localhost:8000/api/v1/gitlab/audit/repository \
  -H "Content-Type: application/json" \
  -d '{"files": [{"path": "mapper/UserMapper.xml", "content": "<mapper>...</mapper>"}]}'
```

### TDSQL管理

```bash
# 连接TDSQL
curl -X POST http://localhost:8000/api/v1/tdsql/connect \
  -H "Content-Type: application/json" \
  -d '{"host": "10.0.0.1", "port": 3306, "user": "root", "password": "xxx", "database": "mydb"}'

# 抓取慢SQL
curl -X POST http://localhost:8000/api/v1/tdsql/slow-queries/fetch \
  -H "Content-Type: application/json" \
  -d '{"source": "digest", "limit": 50}'

# 字符集检查
curl http://localhost:8000/api/v1/tdsql/check/charset?database=mydb

# 大表检查
curl http://localhost:8000/api/v1/tdsql/check/large-tables?database=mydb&threshold_gb=1.0

# 元数据增强审核
curl -X POST http://localhost:8000/api/v1/tdsql/audit/with-metadata \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT * FROM t_order WHERE user_id = 123"}'
```

## 项目结构

```
TDSQL-SQLCheck/
├── backend/
│   ├── main.py                  # FastAPI入口
│   ├── config.py                # 配置管理
│   ├── api/                     # API路由
│   │   ├── sql_audit.py         # SQL审核API
│   │   ├── slow_query.py        # 慢SQL API
│   │   ├── dashboard.py         # Dashboard API
│   │   ├── gitlab_hook.py       # GitLab Webhook API
│   │   └── tdsql_manage.py      # TDSQL管理API
│   ├── engine/                  # 核心引擎
│   │   ├── parser.py            # SQL解析器
│   │   ├── checker.py           # 规则检查器
│   │   ├── slow_analyzer.py     # 慢SQL分析器
│   │   └── rules/               # 规则库
│   │       ├── base.py          # 规则基类
│   │       ├── naming.py        # 命名规范 (R001-R002)
│   │       ├── ddl.py           # DDL规范 (R003-R011)
│   │       ├── dml.py           # DML规范 (R012-R019)
│   │       └── distributed.py   # 分布式规范 (R020-R022)
│   ├── models/                  # 数据模型
│   └── services/                # 业务服务
│       ├── audit_service.py     # 审核服务
│       ├── slow_query_service.py # 慢SQL服务
│       └── tdsql_connector.py   # TDSQL连接器
├── frontend/
│   └── index.html               # 前端单页应用
├── tests/                       # 测试（56个用例）
├── requirements.txt
└── README.md
```

## 审核规则清单

| 规则ID | 级别 | 描述 |
|--------|------|------|
| R001 | ERROR | 表名长度≤32，格式 `^[a-z][a-z0-9_]*$` |
| R002 | ERROR | 禁止使用TDSQL/MySQL保留关键字 |
| R003 | ERROR | 必须显式指定主键 |
| R004 | ERROR | 必须使用InnoDB引擎 |
| R005 | ERROR | 必须使用utf8mb4字符集 |
| R006 | ERROR | 禁止ENUM/SET类型 |
| R007 | ERROR | 禁止TIMESTAMP类型 |
| R008 | ERROR | 禁止外键约束 |
| R009 | ERROR | 财务字段禁止FLOAT/DOUBLE |
| R010 | WARNING | VARCHAR长度不超过2000 |
| R011 | WARNING | 禁止TEXT/BLOB类型 |
| R012 | ERROR | 禁止SELECT * |
| R013 | ERROR | DML必须带WHERE |
| R014 | ERROR | 禁止无WHERE的UPDATE/DELETE |
| R015 | ERROR | 子查询嵌套不超过3层 |
| R016 | WARNING | WHERE中禁止函数/计算 |
| R017 | ERROR | 禁止ORDER BY RAND() |
| R018 | WARNING | 单表索引不超过5个 |
| R019 | WARNING | 禁止冗余索引 |
| R020 | WARNING | 多表JOIN提醒分片键 |
| R021 | ERROR | 禁止更新分片键字段 |
| R022 | ERROR | 禁止不带分片键的全局DELETE/UPDATE |

## 运行测试

```bash
python -m pytest tests/ -v
```

## GitLab集成配置

1. 打开GitLab项目 → Settings → Webhooks
2. URL填写: `http://<your-host>:8000/api/v1/gitlab/webhook/merge-request`
3. Secret Token填写（可选）
4. Trigger勾选: Merge request events
5. SSL verification根据实际情况配置

## 参考资料

- 《TDSQL数据库开发规范》
- 《TDSQL-MySQL慢查询发现与优化方案》
- 《TDSQL大表定义与清理治理规范》
- 《北京农商银行SQL审核平台建设与运维实践》
