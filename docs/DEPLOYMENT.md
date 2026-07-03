# TDSQL SQL审核平台 - 系统部署手册 (V2.0)

> **V2.0 生产部署必读**：认证/密钥/脱敏等安全配置、V1.x升级步骤、纯内网部署说明、
> 目标库最小权限、Prometheus接入，见中文版《[部署手册.md](部署手册.md)》第0章
> "V2.0 生产部署必读"。本文其余章节的通用步骤（Docker/pip部署、健康检查）继续适用。
>
> V2.0 关键变化速览：
> - 认证默认开启（`AUTH_ENABLED=true`），首次启动创建 admin 账户
> - 前端资产本地化（`frontend/static/vendor/`），无外网CDN依赖
> - 连接密码 Fernet 加密存储，密钥经 `TDSQL_ENCRYPTION_KEY`/`AUTH_SECRET_KEY` 管理
> - 慢SQL入库脱敏默认开启（`DATA_MASKING_ENABLED=true`）
> - 新增 `/metrics` Prometheus 端点与操作审计日志

## 1. 环境要求

### 1.1 硬件要求

| 资源 | 最低配置 | 推荐配置 |
|------|----------|----------|
| CPU | 1核 | 2核+ |
| 内存 | 2GB | 4GB+ |
| 磁盘 | 10GB | 20GB+ |

### 1.2 软件要求

| 软件 | 版本要求 |
|------|----------|
| Python | 3.11+ |
| SQLite | 3.x (内置) |
| pip | 最新版 |
| fontconfig (Linux) | 用于中文字体检测 |

### 1.3 外部依赖

| 服务 | 必需 | 说明 |
|------|------|------|
| TDSQL | 否 | 用于元数据查询和慢日志拉取 |
| GitLab | 否 | 用于MR Webhook集成和评论 |

## 2. 快速部署

### 2.1 安装依赖

```bash
cd TDSQL-SQLCheck
pip install -r requirements.txt
```

### 2.2 启动服务

```bash
# 开发模式
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload

# 生产模式
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --workers 4
```

### 2.3 验证部署

```bash
# 访问前端
open http://localhost:8000

# 访问API文档
open http://localhost:8000/docs

# 健康检查
curl http://localhost:8000/api/v1/tdsql/status

# 审核规则API
curl http://localhost:8000/api/v1/rules
curl http://localhost:8000/api/v1/rules/categories
```

## 3. Docker部署

### 3.1 构建镜像

```bash
docker build -t tdsql-sqlcheck .
```

### 3.2 使用docker-compose启动

```bash
docker-compose up -d
```

### 3.3 验证容器状态

```bash
docker-compose ps
docker-compose logs -f
```

## 4. 配置说明

### 4.1 环境变量

| 变量名 | 必需 | 默认值 | 说明 |
|--------|------|--------|------|
| `DATABASE_URL` | 否 | `sqlite:///data/tdsql_check.db` | 数据库连接 |
| `SCHEDULER_ENABLED` | 否 | `false` | 是否启用定时任务 |
| `GITLAB_API_URL` | 否 | `https://gitlab.com` | GitLab API地址 |
| `GITLAB_API_TOKEN` | 否 | - | GitLab访问令牌 |
| `SCHEDULER_FETCH_INTERVAL` | 否 | `3600` | 慢SQL拉取间隔(秒) |

### 4.2 配置文件

配置文件位于 `config/` 目录：

```
config/
├── tdsql_connections.json  # TDSQL连接配置（多实例支持）
└── rules.json              # 规则配置（可选）
```

### 4.3 TDSQL连接配置

系统支持通过前端配置多个TDSQL数据库实例，配置存储在 `config/tdsql_connections.json`:

```json
{
  "connections": [
    {
      "id": "uuid-string",
      "name": "生产环境-订单库",
      "host": "your-tdsql-host",
      "port": 3306,
      "user": "your-username",
      "password": "your-password",
      "database": "your-database",
      "charset": "utf8mb4",
      "is_default": true
    }
  ],
  "default": "uuid-string"
}
```

**推荐通过前端界面管理连接**，路径：`🗄️ TDSQL管理` → 添加/编辑/删除连接

### 4.4 GitLab配置

如需启用MR自动评论功能，需要配置：

```bash
# 设置GitLab实例地址
export GITLAB_API_URL="https://your-gitlab.com"

# 设置访问令牌（需要api权限）
export GITLAB_API_TOKEN="your-private-token"
```

## 5. 目录结构

### 5.1 运行时目录

```
TDSQL-SQLCheck/
├── data/                   # SQLite数据库目录
│   └── tdsql_check.db     # 审核数据库
├── output/                 # 报告输出目录
│   └── reports/           # PDF报告
├── logs/                   # 日志目录
│   └── tdsql.log
└── config/                 # 配置文件目录
    ├── tdsql_connections.json  # TDSQL连接配置
    └── rules.json          # 规则配置
```

### 5.2 权限要求

| 目录 | 权限要求 |
|------|----------|
| `data/` | 读写权限 |
| `output/` | 读写权限 |
| `logs/` | 读写权限 |
| `config/` | 读写权限 |

## 6. Nginx反向代理配置

### 6.1 配置文件

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /docs {
        proxy_pass http://127.0.0.1:8000/docs;
    }
}
```

### 6.2 重载配置

```bash
nginx -t && nginx -s reload
```

## 7. 系统服务配置 (systemd)

### 7.1 创建服务文件

创建 `/etc/systemd/system/tdsql-sqlcheck.service`:

```ini
[Unit]
Description=TDSQL SQL Check Service
After=network.target

[Service]
Type=simple
User=your-user
WorkingDirectory=/path/to/TDSQL-SQLCheck
ExecStart=/path/to/venv/bin/python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5
Environment="PYTHONPATH=/path/to/TDSQL-SQLCheck"

[Install]
WantedBy=multi-user.target
```

### 7.2 启用服务

```bash
sudo systemctl daemon-reload
sudo systemctl enable tdsql-sqlcheck
sudo systemctl start tdsql-sqlcheck
sudo systemctl status tdsql-sqlcheck
```

## 8. 监控配置

### 8.1 健康检查端点

```bash
curl http://localhost:8000/api/v1/tdsql/status
```

### 8.2 日志配置

日志文件位于 `logs/tdsql.log`，使用 Python logging 模块配置。

### 8.3 进程监控

使用 `supervisor` 或 `pm2` 进行进程管理：

```ini
[program:tdsql-sqlcheck]
command=python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
directory=/path/to/TDSQL-SQLCheck
user=your-user
autostart=true
autorestart=true
```

## 9. 备份与恢复

### 9.1 备份数据库

```bash
# 备份SQLite数据库
cp data/tdsql_check.db data/tdsql_check.db.bak-$(date +%Y%m%d)

# 备份TDSQL连接配置
cp config/tdsql_connections.json config/tdsql_connections.json.bak-$(date +%Y%m%d)
```

### 9.2 恢复数据库

```bash
# 停止服务
sudo systemctl stop tdsql-sqlcheck

# 恢复数据库
cp data/tdsql_check.db.bak-YYYYMMDD data/tdsql_check.db

# 恢复连接配置
cp config/tdsql_connections.json.bak-YYYYMMDD config/tdsql_connections.json

# 启动服务
sudo systemctl start tdsql-sqlcheck
```

## 10. 故障排查

### 10.1 常见问题

| 问题 | 可能原因 | 解决方案 |
|------|----------|----------|
| 服务无法启动 | 端口被占用 | 检查8000端口或修改端口 |
| 数据库连接失败 | 文件权限问题 | 检查data目录权限 |
| 中文PDF显示方框 | 系统缺少中文字体 | 安装中文字体或配置fontconfig |
| GitLab Webhook失败 | Token配置错误 | 检查GITLAB_API_TOKEN |
| TDSQL连接失败 | 连接参数错误 | 通过前端测试连接验证 |
| 前端TDSQL管理无响应 | 后端API异常 | 检查日志 `logs/tdsql.log` |

### 10.2 日志查看

```bash
# 查看应用日志
tail -f logs/tdsql.log

# 查看systemd日志
journalctl -u tdsql-sqlcheck -f
```

### 10.3 端口检查

```bash
# 检查端口占用
netstat -tlnp | grep 8000

# 检查进程
ps aux | grep uvicorn
```

### 10.4 中文字体配置

系统支持自动检测中文字体，如遇PDF中文显示异常：

```bash
# Linux系统安装中文字体
sudo apt-get install fonts-wqy-microhei fonts-wqy-zenhei

# 刷新字体缓存
fc-cache -f -v
```

## 11. 卸载

```bash
# 停止服务
sudo systemctl stop tdsql-sqlcheck
sudo systemctl disable tdsql-sqlcheck

# 删除服务文件
sudo rm /etc/systemd/system/tdsql-sqlcheck.service
sudo systemctl daemon-reload

# 删除应用目录
rm -rf /path/to/TDSQL-SQLCheck
```

## 12. 版本升级

### 12.1 备份

```bash
# 备份数据库和配置
cp -r data data.bak
cp -r config config.bak
```

### 12.2 升级代码

```bash
git pull origin main
pip install -r requirements.txt
```

### 12.3 重启服务

```bash
sudo systemctl restart tdsql-sqlcheck
```

## 13. TDSQL连接池

系统使用线程本地存储实现连接池：

- 每个线程独立维护一个数据库连接
- 连接空闲时自动保活
- 线程结束时连接自动释放

配置文件 `config/tdsql_connections.json` 支持多实例配置，可通过前端界面动态管理。