# ==============================================================================
# TDSQL 轻量 Docker 靶场一键启动管理脚本 (Windows PowerShell)
# ==============================================================================

param (
    [Parameter(Position=0)]
    [ValidateSet("start", "stop", "restart", "init", "status", "test")]
    [string]$Action = "start"
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectDir = Split-Path -Parent (Split-Path -Parent $ScriptDir)

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "       TDSQL-SQLCheck 轻量 Docker 靶场集群管理工具        " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

switch ($Action) {
    "start" {
        Write-Host "[1/3] 启动 ZooKeeper 容器 (tdsql-zk)..." -ForegroundColor Yellow
        docker compose -f "$ScriptDir\docker-compose.yml" up -d
        
        Write-Host "[2/3] 检查 MySQL 容器 (tdsql-mysql-test)..." -ForegroundColor Yellow
        $mysqlStatus = docker ps --filter "name=tdsql-mysql-test" --format "{{.Status}}"
        if (-not $mysqlStatus) {
            Write-Host "  [+] 启动 MySQL 容器..." -ForegroundColor Green
            docker start tdsql-mysql-test
        } else {
            Write-Host "  [+] MySQL 容器已在运行中 ($mysqlStatus)" -ForegroundColor Green
        }

        Write-Host "[3/3] 初始化 ZK 节点树与靶场数据..." -ForegroundColor Yellow
        python -X utf8 "$ScriptDir\init_zk_nodes.py"
        python -c "
import pymysql
sql_file = r'$ScriptDir\init_cluster_data.sql'
with open(sql_file, 'r', encoding='utf-8') as f:
    sql_content = f.read()
conn = pymysql.connect(host='127.0.0.1', port=13306, user='root', password='tdsql_test_2024', client_flag=pymysql.constants.CLIENT.MULTI_STATEMENTS)
with conn.cursor() as cur:
    cur.execute(sql_content)
conn.commit()
conn.close()
print('[OK] 靶场元数据与监控库初始化就绪！')
"
        Write-Host "`n[SUCCESS] TDSQL 轻量靶场已全部就绪！" -ForegroundColor Green
        Write-Host "  - MySQL 地址: 127.0.0.1:13306 (user: root, db: tdsql_demo_distributed)" -ForegroundColor White
        Write-Host "  - ZooKeeper : 127.0.0.1:2181 (root_path: /tdsqlzk)" -ForegroundColor White
        Write-Host "  - 监控库:     tdsqlpcloud_monitor (proxy_classes_analysis 慢SQL表已就绪)" -ForegroundColor White
    }

    "stop" {
        Write-Host "正在停止 ZooKeeper 容器..." -ForegroundColor Yellow
        docker compose -f "$ScriptDir\docker-compose.yml" stop
        Write-Host "[OK] 靶场服务已停止。" -ForegroundColor Green
    }

    "restart" {
        & $MyInvocation.MyCommand.Path "stop"
        Start-Sleep -Seconds 1
        & $MyInvocation.MyCommand.Path "start"
    }

    "status" {
        docker ps --filter "name=tdsql"
    }

    "test" {
        Write-Host "执行全链路端到端集成测试..." -ForegroundColor Yellow
        python -X utf8 "$ScriptDir\test_full_platform_integration.py"
    }
}
