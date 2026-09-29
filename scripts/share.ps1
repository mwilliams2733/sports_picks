<#
.SYNOPSIS
    Share the app with friends: make sure the app server runs current code,
    open a Cloudflare quick tunnel to it, and print the public URL.

.DESCRIPTION
    The URL changes on every run (quick tunnels are anonymous). It works
    only while this laptop is awake. Owner routes need the owner key and bets
    need the player's PIN -- both enforced by the server, not by this script.

    -Stop stops the tunnel (the app server is left running).
#>
param([switch]$Stop)
$ErrorActionPreference = 'Stop'

$Repo = 'C:\Users\mwill\Documents\mwilliams2733\sports_picks'
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
$Cloudflared = 'C:\Program Files (x86)\cloudflared\cloudflared.exe'
$AppLog = Join-Path $Repo 'app.log'
$TunnelLog = Join-Path $Repo 'tunnel.log'

$tunnels = Get-CimInstance Win32_Process -Filter "Name = 'cloudflared.exe'" |
    Where-Object { $_.CommandLine -like '*127.0.0.1:8000*' }
if ($Stop) {
    $tunnels | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    Write-Output "Tunnel stopped ($($tunnels.Count) process(es))."
    exit 0
}

Set-Location $Repo

# 1. The built frontend must be at least as new as HEAD.
$headTime = [DateTimeOffset]::FromUnixTimeSeconds([int64](git log -1 --format=%ct)).LocalDateTime
$index = Join-Path $Repo 'frontend\dist\index.html'
if (-not (Test-Path $index) -or (Get-Item $index).LastWriteTime -lt $headTime) {
    Write-Output 'Building the frontend...'
    Push-Location (Join-Path $Repo 'frontend'); npm run build | Out-Null; Pop-Location
}

# 2. The app server must be running, and started after HEAD.
$app = Get-CimInstance Win32_Process -Filter "Name like '%python%'" |
    Where-Object { $_.CommandLine -like '*uvicorn*backend.api.main:app*' }
if ($app -and ($app | Where-Object { $_.CreationDate -lt $headTime })) {
    Write-Output 'App server predates HEAD; restarting it.'
    $app | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    Start-Sleep -Seconds 2
    $app = $null
}
if (-not $app) {
    if (Test-Path $AppLog) { Move-Item $AppLog "$AppLog.prev" -Force }
    Start-Process -FilePath $Python -ArgumentList '-m', 'uvicorn', 'backend.api.main:app',
        '--host', '127.0.0.1', '--port', '8000' -WorkingDirectory $Repo -WindowStyle Hidden `
        -RedirectStandardError $AppLog -RedirectStandardOutput "$AppLog.out" | Out-Null
    Start-Sleep -Seconds 5
}
$health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 10
if ($health.status -ne 'ok') { Write-Error 'App server is not healthy; see app.log'; exit 1 }

# 3. One tunnel at a time.
$tunnels | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
if (Test-Path $TunnelLog) { Remove-Item $TunnelLog -Force }
Start-Process -FilePath $Cloudflared -ArgumentList 'tunnel', '--url', 'http://127.0.0.1:8000', '--logfile', $TunnelLog `
    -WindowStyle Hidden | Out-Null

$url = $null
for ($i = 0; $i -lt 60 -and -not $url; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Path $TunnelLog) {
        $m = Select-String -Path $TunnelLog -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' | Select-Object -First 1
        if ($m) { $url = $m.Matches[0].Value }
    }
}
if (-not $url) { Write-Error "No tunnel URL after 60s; see $TunnelLog"; exit 1 }
Write-Output "Share this link: $url/paper-trading"
