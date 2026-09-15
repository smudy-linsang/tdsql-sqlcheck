# Agent D / v1.6.4.0 UAT: restart ONLY the Web process (gw/runners stay).
# ASCII-only on purpose: Windows PowerShell 5.1 reads -File as ANSI.
$Root = 'C:\TDSQL_SQLCHECK\TDSQL-SQLCheck'
$Rt   = Join-Path $Root 'data\reports\uat_d_1640'
$Py   = 'C:\Python314\python.exe'
$UserSite = 'C:\Users\linsa\AppData\Roaming\Python\Python314\site-packages'

Get-NetTCPConnection -State Listen -LocalPort 8025 -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object {
        try { Stop-Process -Id $_ -Force } catch { }
    }
Start-Sleep -Seconds 2

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
  'set PYTHONIOENCODING=utf-8'
)
$bat = Join-Path $Rt 'web.cmd'
Set-Content -Path $bat -Value (@("@echo off", "cd /d $Root") + $envLines +
    @("`"$Py`" -m uvicorn backend.main:app --host 127.0.0.1 --port 8025 --log-level info > `"$Rt\web.log`" 2> `"$Rt\web.err.log`"")) -Encoding OEM
Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', "`"$bat`"" -WorkingDirectory $Root -WindowStyle Hidden | Out-Null

for ($i = 0; $i -lt 45; $i++) {
    Start-Sleep -Milliseconds 800
    try {
        $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8025/health' -UseBasicParsing -TimeoutSec 3
        if ($r.StatusCode -eq 200) { Write-Host "[restart] web ready"; exit 0 }
    } catch { }
}
Write-Host "[restart] web NOT ready"
