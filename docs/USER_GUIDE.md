# TDSQL SQL审核平台 - 系统功能使用手册 (v1.0.2)

> 文档版本：v1.0.2 ｜ 适用系统版本：v1.0.2 ｜ 更新日期：2026-07-07

> **V2.0 必读**：平台已启用登录认证与四角色权限（admin/dba/developer/auditor），
> 首次使用请先登录（见《[功能使用手册.md](功能使用手册.md)》第0章）；
> V2.0 新功能（用户管理/多实例连接/扫描计划/规则集/数据保留/操作审计）的使用方法
> 见《功能使用手册.md》第10章。本文其余章节的基础功能操作继续适用。

## 1. 功能概览

TDSQL SQL审核平台是一款覆盖开发、测试、生产全生命周期的SQL质量管控与慢SQL分析平台，
V2.0 支持纯内网部署与数百套数据库实例接入。

### 1.1 核心功能

| 功能 | 说明 |
|------|------|
| 认证与权限 (V2.0) | 登录认证、四角色RBAC、用户管理、操作审计 |
| SQL审核 | 基于119条规则审核SQL语句（支持项目级规则集差异化） |
| 文件审核 | 批量审核SQL文件和MyBatis XML |
| 慢SQL分析 | EXPLAIN计划自动分析（入库自动脱敏） |
| TDSQL管理 | 多数据库实例连接注册表（密码加密存储）、扫描计划 |
| GitLab集成 | MR Webhook自动审核 + MR评论 |
| 数据治理 (V2.0) | 数据保留策略与自动清理 |
| Dashboard | 审核统计可视化 |
| PDF报告导出 | 审核结果导出为PDF |

## 2. 界面使用

### 2.1 访问地址

- **前端界面**: http://localhost:8000
- **API文档**: http://localhost:8000/docs

### 2.2 页面导航

前端界面分为7个主要页面：

1. **📊 Dashboard** - 统计概览
2. **📝 SQL审核** - 单条SQL审核
3. **📄 文件审核** - 批量文件审核
4. **🐌 慢SQL分析** - 慢SQL管理
5. **📈 EXPLAIN分析** - 执行计划分析
6. **🗄️ TDSQL管理** - 数据库连接管理
7. **📋 审核规则** - SQL审核规则规范介绍

侧边栏底部显示当前TDSQL连接状态指示器。

## 3. TDSQL管理功能（核心功能）

### 3.1 功能说明

系统支持在前端配置多个TDSQL数据库实例，方便对不同环境/业务的数据库进行SQL审核。

### 3.2 界面操作步骤

#### 添加连接

1. 点击侧边栏 **「🗄️ TDSQL管理」** 菜单
2. 填写连接信息表单：
   - **连接名称**：标识此连接（如：生产环境-订单库）
   - **主机地址**：TDSQL实例 host
   - **端口**：默认 3306
   - **用户名**：数据库用户名
   - **密码**：数据库密码
   - **数据库名**：默认连接的数据库
   - **字符集**：默认 utf8mb4
3. 点击 **「测试连接」** 验证连接有效性
4. 连接成功后，点击 **「保存连接」**

#### 设置默认连接

1. 在已保存的连接列表中，点击连接右侧的 **「设为默认」** 按钮
2. 默认连接的标识会显示在连接名称旁边

#### 管理连接

- **测试连接**：验证连接是否正常
- **设为默认**：将此连接设为默认连接
- **删除**：移除已保存的连接配置

### 3.3 API调用示例

```bash
# 获取所有连接
curl http://localhost:8000/api/v1/tdsql/connections

# 添加新连接
curl -X POST http://localhost:8000/api/v1/tdsql/connections \
  -H "Content-Type: application/json" \
  -d '{
    "name": "测试环境",
    "host": "127.0.0.1",
    "port": 3306,
    "user": "test_user",
    "password": "test_password",
    "database": "test_db",
    "charset": "utf8mb4"
  }'

# 测试连接
curl -X POST http://localhost:8000/api/v1/tdsql/connections/test-id/connect

# 设置默认连接
curl -X POST http://localhost:8000/api/v1/tdsql/connections/test-id/set-default

# 删除连接
curl -X DELETE http://localhost:8000/api/v1/tdsql/connections/test-id

# 获取当前连接状态
curl http://localhost:8000/api/v1/tdsql/status

# 获取表结构（需要先连接）
curl http://localhost:8000/api/v1/tdsql/tables

# 断开连接
curl -X POST http://localhost:8000/api/v1/tdsql/disconnect
```

## 4. SQL审核功能

### 4.1 界面操作步骤

1. 点击「📝 SQL审核」菜单
2. 在SQL输入框中输入SQL语句
3. 点击「审核」按钮
4. 查看审核结果

### 4.2 审核规则（119条 / 9大分类）

| 规则ID | 类别 | 说明 | 严重级别 |
|--------|------|------|----------|
| R001 | 命名 | 表名长度不超过50字符 | ERROR |
| R002 | 命名 | 不能使用MySQL保留关键字 | ERROR |
| R003 | DDL | 必须有主键 | ERROR |
| R004 | DDL | 存储引擎必须为InnoDB | ERROR |
| R005 | DDL | 字符集必须为utf8mb4 | ERROR |
| R006 | DDL | 不允许使用ENUM/SET类型 | ERROR |
| R007 | DDL | 必须使用BIGINT而非INT | WARNING |
| R008 | DDL | 不允许使用外键 | WARNING |
| R009 | DDL | 金融字段不允许使用FLOAT/DOUBLE | ERROR |
| R010 | DDL | VARCHAR长度超过1000字符警告 | WARNING |
| R011 | DDL | 不允许使用TEXT/BLOB类型（建议拆分） | WARNING |
| R012 | DML | 不允许SELECT * | ERROR |
| R013 | DML | UPDATE/DELETE必须有WHERE条件 | ERROR |
| R014 | DML | 禁止无WHERE条件批量更新/删除 | ERROR |
| R015 | DML | 子查询深度不超过4层 | WARNING |
| R016 | DML | WHERE条件中不允许使用函数 | WARNING |
| R017 | DML | 不允许使用ORDER BY RAND() | ERROR |
| R018 | DML | 单表索引数量不超过5个 | WARNING |
| R019 | DML | 索引列不能重复 | WARNING |
| R020 | 分布式 | 分布式表查询必须包含分片键 | WARNING |
| R021 | 分布式 | 禁止更新分片键字段 | ERROR |
| R022 | 分布式 | 禁止不带分片键的全局DELETE/UPDATE | ERROR |

### 4.3 API调用示例

```bash
# 单条SQL审核
curl -X POST http://localhost:8000/api/v1/audit/sql \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT * FROM t_user", "db_type": "tdsql"}'
```

### 4.4 返回示例

```json
{
  "passed": false,
  "violations": [
    {
      "rule_id": "R012",
      "severity": "ERROR",
      "message": "不允许使用 SELECT *",
      "suggestion": "请指定需要查询的字段名"
    }
  ]
}
```

## 5. 文件审核功能

### 5.1 支持的文件类型

| 文件类型 | 说明 |
|----------|------|
| `.sql` | SQL文件 |
| `.xml` | MyBatis XML映射文件 |

### 5.2 界面操作步骤

1. 点击「📄 文件审核」菜单
2. 拖拽或点击上传文件
3. 等待审核完成
4. 查看文件审核结果

### 5.3 API调用示例

```bash
# 文件审核
curl -X POST http://localhost:8000/api/v1/audit/file \
  -H "Content-Type: application/json" \
  -d '{"content": "SELECT * FROM t_user", "file_path": "test.sql"}'
```

## 6. 慢SQL分析功能

### 6.1 添加慢SQL

#### 界面操作步骤

1. 点击「🐌 慢SQL分析」菜单
2. 填写慢SQL信息表单
3. 点击「分析」按钮
4. 查看分析结果

#### 表单字段说明

| 字段 | 必需 | 说明 |
|------|------|------|
| SQL指纹 | 是 | 脱敏后的SQL模板 |
| 原始SQL | 是 | 原始SQL语句 |
| 数据库名 | 否 | 数据库名称 |
| 执行次数 | 否 | 执行次数 |
| 平均耗时(ms) | 否 | 平均执行时间 |
| 扫描行数 | 否 | 扫描的行数 |
| 返回行数 | 否 | 返回的行数 |

### 6.2 慢SQL分析维度

| 分析维度 | 说明 |
|----------|------|
| 全表扫描 | 检测type=ALL的慢查询 |
| 缺失索引 | 检测可能导致全表扫描的查询 |
| 文件排序 | 检测使用filesort的查询 |
| 深度分页 | 检测深度分页查询 |
| 扫描比例 | 检测扫描行数与返回行数比例 |

### 6.3 API调用示例

```bash
# 添加慢SQL
curl -X POST http://localhost:8000/api/v1/slow-queries \
  -H "Content-Type: application/json" \
  -d '{
    "fingerprint": "SELECT * FROM t_order WHERE user_id = ?",
    "sql_text": "SELECT * FROM t_order WHERE user_id = 123",
    "db_name": "order_db",
    "exec_count": 5000,
    "avg_time_ms": 200,
    "rows_examined": 850000,
    "rows_sent": 100
  }'

# 获取慢SQL列表
curl http://localhost:8000/api/v1/slow-queries?limit=20

# 获取统计分析
curl http://localhost:8000/api/v1/slow-queries/statistics
```

## 7. EXPLAIN分析功能

### 7.1 界面操作步骤

1. 点击「📈 EXPLAIN分析」菜单
2. 粘贴EXPLAIN输出（JSON格式）
3. 点击「分析」按钮
4. 查看分析结果

### 7.2 支持的EXPLAIN类型

| type值 | 说明 | 风险级别 |
|--------|------|----------|
| system | 系统表 | 低 |
| const | 主键/唯一索引 | 低 |
| eq_ref | 唯一索引关联 | 低 |
| ref | 非唯一索引 | 中 |
| range | 索引范围 | 低 |
| index | 全索引扫描 | 中 |
| ALL | 全表扫描 | 高 |

### 7.3 API调用示例

```bash
# 分析EXPLAIN
curl -X POST http://localhost:8000/api/v1/slow-queries/analyze-explain \
  -H "Content-Type: application/json" \
  -d '{
    "explain_data": [{
      "id": 1,
      "select_type": "SIMPLE",
      "table": "t_order",
      "type": "ALL",
      "possible_keys": null,
      "key": null,
      "rows": 850000,
      "filtered": 10.0,
      "extra": "Using where"
    }]
  }'
```

## 8. GitLab集成功能

### 8.1 Webhook配置

在GitLab项目中配置Webhook：

1. 进入 GitLab 项目 → Settings → Webhooks
2. 添加Webhook URL: `http://your-domain/api/v1/gitlab/webhook`
3. 选择触发事件: **Merge request events**
4. 保存

### 8.2 环境变量配置

| 变量 | 说明 |
|------|------|
| `GITLAB_API_URL` | GitLab实例地址（默认：https://gitlab.com） |
| `GITLAB_API_TOKEN` | GitLab访问令牌（需要api权限） |

### 8.3 MR审核流程

1. 开发者提交MR
2. GitLab触发Webhook
3. 系统自动审核MR中的SQL变更
4. 系统自动在MR下发布审核评论（包含通过/失败结果和违规详情）

## 9. Dashboard功能

### 9.1 统计概览

Dashboard展示以下统计数据：

| 统计项 | 说明 |
|--------|------|
| 审核总数 | 累计审核次数 |
| 今日审核 | 今日审核次数 |
| 规则分布 | 各规则被触发次数 |
| 慢SQL TopN | 执行时间最长的SQL |

### 9.2 审核趋势

展示最近7天的审核趋势，包括：
- 每日审核数量
- 每日通过/不通过比例
- 规则触发趋势

## 10. PDF报告导出

### 10.1 生成审核报告

```bash
curl -X POST http://localhost:8000/api/v1/audit/report/{report_id}/export \
  -H "Content-Type: application/json" \
  -d '{"format": "pdf"}'
```

### 10.2 生成慢SQL报告

```bash
curl -X POST http://localhost:8000/api/v1/audit/slow-report/{slow_id}/export \
  -H "Content-Type: application/json" \
  -d '{"format": "pdf"}'
```

## 11. 审核规则功能

### 11.1 功能说明

系统提供专门的审核规则介绍页面，方便开发测试人员了解每条审核规则的用途和规范要求。规则信息从系统动态获取，新增规则后会自动同步更新。

### 11.2 界面操作

1. 点击侧边栏 **「📋 审核规则」** 菜单
2. 查看系统中所有审核规则
3. 规则按类别分组展示：
   - 🏷️ 命名规范
   - 🏗️ DDL规范
   - 📝 DML规范
   - 🌐 分布式规范

### 11.3 规则分类

| 类别 | 规则数 | 说明 |
|------|--------|------|
| 命名规范 | 2条 | 表名长度、保留关键字 |
| DDL规范 | 9条 | 主键、引擎、字符集、字段类型等 |
| DML规范 | 8条 | SELECT *、WHERE条件、索引等 |
| 分布式规范 | 3条 | 分片键相关规则 |

### 11.4 API调用

```bash
# 获取所有规则
curl http://localhost:8000/api/v1/rules

# 按类别统计
curl http://localhost:8000/api/v1/rules/categories
```

## 12. 常见问题

### 12.1 为什么SELECT *被拒绝？

SELECT * 可能导致以下问题：
- 增加网络传输开销
- 索引覆盖失效
- 语义不明确

建议：显式指定需要的字段名。

### 12.2 为什么慢SQL分析没有发现问题？

可能原因：
- EXPLAIN数据不是JSON格式
- 查询已经使用了索引
- 数据量较小未触发阈值

### 12.3 TDSQL连接失败怎么办？

1. 检查主机地址和端口是否正确
2. 确认用户名密码有效
3. 确认数据库存在且用户有访问权限
4. 使用前端「测试连接」功能验证

### 12.4 如何管理多个数据库实例？

通过前端「🗄️ TDSQL管理」菜单：
- 添加多个连接并命名区分
- 设置默认连接
- 随时切换当前使用的数据库实例

### 12.5 中文显示为方框怎么办？

这是系统缺少中文字体导致的，Linux系统请执行：

```bash
# Ubuntu/Debian
sudo apt-get install fonts-wqy-microhei fonts-wqy-zenhei

# 刷新字体缓存
fc-cache -f -v
```

系统会自动检测并使用可用的中文字体。