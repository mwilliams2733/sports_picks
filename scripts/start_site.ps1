<#
.SYNOPSIS
    Bring the public site up: the app server on :8000 and the permanent
    Cloudflare tunnel (metricedgepicks.com). Launched at logon by the
    "sports_picks site" task; safe to run by hand at any time.

.DESCRIPTION
    Each part is started only if it is not already running, so this never
    restarts a healthy server or tunnel. It does not build the frontend or
    restart a stale server -- shipping a change is still build + restart.

    The tunnel reads C:\Users\mwill\.cloudflared\config.yml (tunnel
    metric-edge -> http://127.0.0.1:8000) and logs to tunnel-named.log.
    The old quick tunnel (scripts\share.ps1, a random trycloudflare.com
    address) is separate and not started here.
#>
$ErrorActionPreference = 'Stop'

$Repo = 'C:\Users\mwill\Documents\mwilliams2733\sports_picks'
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
$Cloudflared = 'C:\Program Files (x86)\cloudflared\cloudflared.exe'
$TunnelConfig = 'C:\Users\mwill\.cloudflared\config.yml'
$AppLog = Join-Path $Repo 'app.log'
$Db = Join-Path $Repo 'sports_picks.db'

# 1. App server.
$app = Get-CimInstance Win32_Process -Filter "Name like '%python%'" |
    Where-Object { $_.CommandLine -like '*uvicorn*backend.api.main:app*' -and $_.CommandLine -like '*--port 8000*' }
if ($app) {
    Write-Output "App server already running (PID $($app.ProcessId -join ', '))."
} else {
    if (Test-Path $AppLog) { Move-Item $AppLog "$AppLog.prev" -Force }
    # The scheduler runs as its own process ("sports_picks scheduler" task):
    # ENABLE_SCHEDULER=0 so the server never runs the cron jobs a second time.
    $env:ENABLE_SCHEDULER = '0'
    $env:DATABASE_PATH = $Db
    $proc = Start-Process -FilePath $Python -ArgumentList '-m', 'uvicorn', 'backend.api.main:app',
        '--host', '127.0.0.1', '--port', '8000' -WorkingDirectory $Repo -WindowStyle Hidden `
        -RedirectStandardError $AppLog -RedirectStandardOutput "$AppLog.out" -PassThru
    $healthy = $false
    for ($i = 0; $i -lt 60 -and -not $healthy; $i++) {
        if ($proc.HasExited) { break }
        try { $healthy = (Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 2).status -eq 'ok' }
        catch { Start-Sleep -Seconds 1 }
    }
    if (-not $healthy) { Write-Error "App server did not come up; see $AppLog"; exit 1 }
    Write-Output "App server started, PID $($proc.Id)."
}

# 2. Permanent tunnel.
$tunnel = Get-CimInstance Win32_Process -Filter "Name = 'cloudflared.exe'" |
    Where-Object { $_.CommandLine -like '*run metric-edge*' }
if ($tunnel) {
    Write-Output "Tunnel already running (PID $($tunnel.ProcessId -join ', '))."
} else {
    Start-Process -FilePath $Cloudflared -ArgumentList 'tunnel', '--config', $TunnelConfig, 'run', 'metric-edge' `
        -WindowStyle Hidden
    Write-Output 'Tunnel started: https://www.metricedgepicks.com'
}
