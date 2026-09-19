<#
.SYNOPSIS
    Start the sports_picks pipeline scheduler if it is not already running.

.DESCRIPTION
    Launched at logon by the "sports_picks scheduler" task (see
    register_scheduler_task.ps1). Safe to run by hand at any time.

    The guard matters: Task Scheduler's own -MultipleInstances IgnoreNew stops
    the TASK running twice, but not a task-started scheduler colliding with one
    started by hand. Two instances would share sports_picks.db and run every
    cron job twice.

    Paths are absolute and point at the venv interpreter. The .bat files in the
    repo root still reference C:\Users\mwill\OneDrive\... , which has not
    existed since July 2026; do not copy from them.
#>

$ErrorActionPreference = 'Stop'

$Repo = 'C:\Users\mwill\Documents\mwilliams2733\sports_picks'
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
# Python's logging.basicConfig writes to STDERR, so the scheduler's actual
# output -- job registration, scout results, tracebacks -- arrives on stderr.
# scheduler.log is therefore wired to stderr: it is the file to read.
# scheduler.out.log catches stray stdout and is normally empty. Naming these
# the other way round put every INFO line in a file called "err" and left
# scheduler.log at 0 bytes.
$Log = Join-Path $Repo 'scheduler.log'
$OutLog = Join-Path $Repo 'scheduler.out.log'

if (-not (Test-Path $Python)) {
    Write-Error "venv interpreter missing: $Python"
    exit 1
}

$running = Get-CimInstance Win32_Process -Filter "Name like '%python%'" |
    Where-Object { $_.CommandLine -like '*backend.pipeline.scheduler*' }
if ($running) {
    Write-Output "Scheduler already running (PID $($running.ProcessId -join ', ')); nothing to do."
    exit 0
}

# Keep one generation of history. Start-Process truncates its redirect target,
# so without this a restart destroys the log covering whatever went wrong.
foreach ($f in @($Log, $OutLog)) {
    if (Test-Path $f) { Move-Item $f "$f.prev" -Force }
}

Set-Location $Repo
$proc = Start-Process -FilePath $Python `
    -ArgumentList '-m', 'backend.pipeline.scheduler' `
    -WorkingDirectory $Repo `
    -WindowStyle Hidden `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $Log `
    -PassThru

Start-Sleep -Seconds 5
if ($proc.HasExited) {
    Write-Error "Scheduler exited immediately (code $($proc.ExitCode)). See $Log"
    exit 1
}
Write-Output "Scheduler started, PID $($proc.Id). Logging to $Log"
