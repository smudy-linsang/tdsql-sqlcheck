$ErrorActionPreference = "Stop"
$env:AUTH_ENABLED = "false"
$env:DATA_MASKING_ENABLED = "false"
$env:RAW_SLOWLOG_ENABLED = "false"
$env:SCHEDULER_ENABLED = "false"
$env:GITLAB_WEBHOOK_ALLOW_INSECURE = "true"
Set-Location "C:/TDSQL_SQLCHECK/TDSQL-SQLCheck"
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8003 --log-level info 2>&1 |
  Out-File -FilePath "C:/TDSQL_SQLCHECK/TDSQL-SQLCheck/backend_restart.log" -Encoding utf8
