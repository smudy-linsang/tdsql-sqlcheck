@echo off
setlocal
set AUTH_ENABLED=false
set DATA_MASKING_ENABLED=false
set RAW_SLOWLOG_ENABLED=false
set SCHEDULER_ENABLED=false
set GITLAB_WEBHOOK_ALLOW_INSECURE=true
cd /d "C:\TDSQL_SQLCHECK\TDSQL-SQLCheck"
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8003 --log-level info > "C:\TDSQL_SQLCHECK\TDSQL-SQLCheck\backend_restart.log" 2>&1
