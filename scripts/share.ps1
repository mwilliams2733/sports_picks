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
$BuildLog = Join-Path $Repo 'frontend-build.log'
$Db = Join-Path $Repo 'sports_picks.db'

# Match an existing quick tunnel for this app: a cloudflared "tunnel" process
# pointed at :8000 under any loopback spelling.
$tunnels = Get-CimInstance Win32_Process -Filter "Name = 'cloudflared.exe'" |
    Where-Object { $_.CommandLine -match 'tunnel' -and $_.CommandLine -match '(127\.0\.0\.1|localhost|\[::1\]):8000' }
if ($Stop) {
    $tunnels | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    Write-Output "Tunnel stopped ($($tunnels.Count) process(es))."
    exit 0
}

Set-Location $Repo

# 1. The built frontend must be at least as new as HEAD.
$headTime = [DateTimeOffset]::FromUnixTimeSeconds([int64](git log -1 --format=%ct)).LocalDateTime
$index = Join-Path $Repo 'frontend\dist\index.html'
$rebuilt = $false
if (-not (Test-Path $index) -or (Get-Item $index).LastWriteTime -lt $headTime) {
    Write-Output 'Building the frontend...'
    Push-Location (Join-Path $Repo 'frontend')
    # cmd /c, not a native PS pipeline: under $ErrorActionPreference = 'Stop',
    # PS 5.1 turns every stderr line from npm (including its routine "(!) Some
    # chunks are larger than 500 kB" warning) into a terminating
    # NativeCommandError. cmd's own redirection also writes the log as plain
    # bytes instead of PS 5.1's UTF-16.
    cmd /c "npm run build >> `"$BuildLog`" 2>&1"
    $buildExit = $LASTEXITCODE
    Pop-Location
    if ($buildExit -ne 0) {
        Write-Error 'Frontend build failed; see frontend-build.log'
        exit 1
    }
    $rebuilt = $true
}

# 2. The app server must be running, started after HEAD, and be this app on :8000.
$app = Get-CimInstance Win32_Process -Filter "Name like '%python%'" |
    Where-Object { $_.CommandLine -like '*uvicorn*backend.api.main:app*' -and $_.CommandLine -like '*--port 8000*' }
$restartNeeded = $false
if ($app -and ($app | Where-Object { $_.CreationDate -lt $headTime })) {
    $restartNeeded = $true
}
if ($rebuilt) {
    # A build just ran in this invocation: treat it like "predates HEAD" so
    # the server always serves what was just built.
    $restartNeeded = $true
}
if ($restartNeeded -and $app) {
    Write-Output 'App server predates HEAD or a rebuild just ran; restarting it.'
    $app | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
    $app = $null
}

if (-not $app) {
    if (Test-Path $AppLog) { Move-Item $AppLog "$AppLog.prev" -Force }

    # Pin the server's environment: the real scheduler already runs as its
    # own separate process, so a second server process with the scheduler
    # enabled would run every cron job twice. Set to '0' rather than
    # unsetting: backend/config.py calls load_dotenv(override=False), so an
    # unset variable could still be re-enabled by a .env file; '0' cannot be.
    # The child inherits these at launch; restore the caller's values right
    # after, so running this script does not change the caller's session.
    $prevScheduler = $env:ENABLE_SCHEDULER
    $prevDatabase = $env:DATABASE_PATH
    $env:ENABLE_SCHEDULER = '0'
    $env:DATABASE_PATH = $Db
    try {
        $proc = Start-Process -FilePath $Python -ArgumentList '-m', 'uvicorn', 'backend.api.main:app',
            '--host', '127.0.0.1', '--port', '8000' -WorkingDirectory $Repo -WindowStyle Hidden `
            -RedirectStandardError $AppLog -RedirectStandardOutput "$AppLog.out" -PassThru
    } finally {
        if ($null -eq $prevScheduler) { Remove-Item Env:ENABLE_SCHEDULER -ErrorAction SilentlyContinue }
        else { $env:ENABLE_SCHEDULER = $prevScheduler }
        if ($null -eq $prevDatabase) { Remove-Item Env:DATABASE_PATH -ErrorAction SilentlyContinue }
        else { $env:DATABASE_PATH = $prevDatabase }
    }

    $healthy = $false
    for ($i = 0; $i -lt 30 -and -not $healthy; $i++) {
        if ($proc.HasExited) {
            Write-Error 'App server is not healthy; see app.log'
            exit 1
        }
        try {
            $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 2
            if ($health.status -eq 'ok') {
                $healthy = $true
            } else {
                Start-Sleep -Seconds 1
            }
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    if (-not $healthy) {
        # Don't leave a half-started server running.
        Get-CimInstance Win32_Process -Filter "ParentProcessId = $($proc.Id)" -ErrorAction SilentlyContinue |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        Write-Error 'App server is not healthy; see app.log'
        exit 1
    }

    # Confirm the process actually listening on :8000 is the one we just
    # started (or a child of it), not something else that beat us to the
    # port -- and not an orphaned listener whose dead parent's PID happened
    # to get reused as $proc.Id, which the CreationDate check below rules out.
    $owners = (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue).OwningProcess
    $ownerOk = $false
    foreach ($ownerId in $owners) {
        $ownerProc = Get-CimInstance Win32_Process -Filter "ProcessId = $ownerId" -ErrorAction SilentlyContinue
        if (-not $ownerProc) { continue }
        $isSelfOrChild = ($ownerId -eq $proc.Id) -or ($ownerProc.ParentProcessId -eq $proc.Id)
        if ($isSelfOrChild -and $ownerProc.CreationDate -ge $proc.StartTime) {
            $ownerOk = $true
        }
    }
    if (-not $ownerOk) {
        $badPid = $owners | Select-Object -First 1
        Write-Error "Something else is serving :8000 (PID $badPid); stop it and re-run."
        exit 1
    }
} else {
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 10
        if ($health.status -ne 'ok') {
            Write-Error 'App server is not healthy; see app.log'
            exit 1
        }
    } catch {
        Write-Error 'App server is not healthy; see app.log'
        exit 1
    }
}

# 3. No link while any player can be claimed. A player with no PIN (the
#    legacy ones predate PINs) sets one on their next bet -- so whoever
#    bets first as them owns them. Read-only; no pin_hash column yet means
#    the migration has not run, so every player lacks a PIN.
$pinCheck = "import sqlite3,sys; c=sqlite3.connect('file:'+sys.argv[1].replace(chr(92),'/')+'?mode=ro',uri=True); " +
    "cols=[r[1] for r in c.execute('PRAGMA table_info(user_profiles)')]; " +
    "print(c.execute('SELECT COUNT(*) FROM user_profiles'+(' WHERE pin_hash IS NULL' if 'pin_hash' in cols else '')).fetchone()[0])"
$noPin = & $Python -c $pinCheck $Db
if ($LASTEXITCODE -ne 0 -or -not ("$noPin" -match '^\d+$')) {
    Write-Error "Could not check player PINs in $Db"
    exit 1
}
if ([int]$noPin -gt 0) {
    Write-Error "$noPin player(s) have no PIN. Set them first: PUT /users/<id>/pin with the owner key (see Admin), then re-run."
    exit 1
}

# 4. One tunnel at a time.
$tunnels | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
if (Test-Path $TunnelLog) { Remove-Item $TunnelLog -Force }
$tunnelProc = Start-Process -FilePath $Cloudflared -ArgumentList 'tunnel', '--url', 'http://127.0.0.1:8000', '--logfile', $TunnelLog `
    -WindowStyle Hidden -PassThru

$url = $null
for ($i = 0; $i -lt 60 -and -not $url; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Path $TunnelLog) {
        # Cloudflared's own failure/diagnostic messages reference
        # https://api.trycloudflare.com -- exclude it so a dead tunnel is
        # never reported as a working share link.
        $m = Select-String -Path $TunnelLog -Pattern 'https://(?!api\.)[a-z0-9-]+\.trycloudflare\.com' | Select-Object -First 1
        if ($m) { $url = $m.Matches[0].Value }
    }
}
if (-not $url) {
    Stop-Process -Id $tunnelProc.Id -Force -ErrorAction SilentlyContinue
    Write-Error "No tunnel URL after 60s; see $TunnelLog"
    exit 1
}
Write-Output "Share this link: $url/paper-trading"
