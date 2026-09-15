# Agent D / v1.6.4.0 UAT: stop all fixture processes (by port AND command line).
# ASCII-only on purpose: Windows PowerShell 5.1 reads -File as ANSI.
$Rt = 'C:\TDSQL_SQLCHECK\TDSQL-SQLCheck\data\reports\uat_d_1640'
foreach ($name in @('gw','web','cprunner','mdrunner')) {
    $f = Join-Path $Rt "$name.pid"
    if (Test-Path $f) { Remove-Item $f -Force -ErrorAction SilentlyContinue }
}
# 1) by listening port
foreach ($port in 8025, 8443, 8444, 8445) {
    Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object {
            try {
                Stop-Process -Id $_ -Force -ErrorAction Stop
                Write-Host "[stop] port $port pid=$_"
            } catch { }
        }
}
# 2) by command line (runners have no listening port)
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object {
        $_.CommandLine -match 'copilot_runner|metadata_runner|mock_llm_gateway|uvicorn'
    } | ForEach-Object {
        try {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop
            Write-Host "[stop] cmdline pid=$($_.ProcessId)"
        } catch { }
    }
Start-Sleep -Seconds 2
Write-Host "[done] stop_all finished"
