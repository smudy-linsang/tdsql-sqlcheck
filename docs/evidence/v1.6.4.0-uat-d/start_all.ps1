# Agent D / v1.6.4.0 UAT: start all fixture processes detached (WMI Win32_Process).
#   powershell -ExecutionPolicy Bypass -File start_all.ps1
# ASCII-only on purpose: Windows PowerShell 5.1 reads -File as ANSI.
$ErrorActionPreference = 'Continue'
$Root = 'C:\TDSQL_SQLCHECK\TDSQL-SQLCheck'
$Ev   = Join-Path $Root 'docs\evidence\v1.6.4.0-uat-d'
$Rt   = Join-Path $Root 'data\reports\uat_d_1640'
$Py   = 'C:\Python314\python.exe'
$UserSite = 'C:\Users\linsa\AppData\Roaming\Python\Python314\site-packages'
New-Item -ItemType Directory -Force -Path $Rt | Out-Null

$envLines = @(
  'set SQLCHECK_DB_HOST=127.0.0.1',
  'set SQLCHECK_DB_PORT=13306',
  'set SQLCHECK_DB_USER=root',
  'set SQLCHECK_DB_PASSWORD=tdsql_test_2024',
  'set SQLCHECK_DB_NAME=uat_d_1640_meta',
  'set AUTH_ENABLED=true',
  'set SCHEDULER_ENABLED=false',
  'set DATA_MASKING_ENABLED=false',
  "set REPORT_OUTPUT_DIR=$Rt\reports",
  'set COPILOT_ENABLED=true',
  'set COPILOT_ALLOW_SCHEMA_IDENTIFIERS=true',
  "set COPILOT_KEYRING_FILE=$Rt\copilot-keyring.json",
  "set COPILOT_ENDPOINTS_FILE=$Rt\copilot-endpoints.json",
  "set UAT_D40_RUNTIME=$Rt",
  "set PYTHONPATH=$Root;$UserSite",
  'set PYTHONIOENCODING=utf-8',
  'set UAT_D40_GW_HOST=172.16.4.16'
)

function Start-Detached($name, $pyArgs) {
    $bat = Join-Path $Rt "$name.cmd"
    $body = @("@echo off", "cd /d $Root") + $envLines +
            @("`"$Py`" $pyArgs > `"$Rt\$name.log`" 2> `"$Rt\$name.err.log`"")
    Set-Content -Path $bat -Value $body -Encoding OEM
    $p = Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', "`"$bat`"" `
        -WorkingDirectory $Root -WindowStyle Hidden -PassThru
    Write-Host "[start] $name launcher-pid=$($p.Id)"
}

Start-Detached 'gw'       "$Ev\mock_llm_gateway.py 8443 8444 8445"
Start-Detached 'web'      "-m uvicorn backend.main:app --host 127.0.0.1 --port 8025 --log-level info"
Start-Detached 'cprunner' "-m backend.workers.copilot_runner"
Start-Detached 'mdrunner' "-m backend.workers.metadata_runner"
Write-Host "[done] launched; logs under $Rt"
