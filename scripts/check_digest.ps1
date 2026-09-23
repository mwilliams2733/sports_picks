<#
.SYNOPSIS
    Check whether this morning's digest went out, and record the verdict.

.DESCRIPTION
    Launched daily at 09:18 local by the "sports_picks digest check" task
    (see register_digest_check_task.ps1). Safe to run by hand at any time:
    it is read-only, touching neither the database nor scheduler.log.

    09:18 local is after the 11:00 ET digest in both DST regimes -- this
    machine is on Arizona time, which does not shift while Eastern does. The
    check reads the traces of both the 8am ET scout and the 11am ET send.

    Output is APPENDED to digest_health.log, one run per line group, so a
    week of mornings can be read at a glance. The file is never truncated --
    a health record that overwrites itself cannot show a pattern, and "it
    broke last Thursday too" is the useful observation.

    The exit code is the real signal: 0 healthy, 1 not. Task Scheduler stores
    it as LastTaskResult, so:

        (Get-ScheduledTaskInfo 'sports_picks digest check').LastTaskResult

    answers the question without opening anything. 0 = fine, 1 = look.

    Paths are absolute and point at the venv interpreter. The .bat files in
    the repo root reference a OneDrive path that has not existed since July
    2026; do not copy from them.
#>

$ErrorActionPreference = 'Stop'

$Repo = 'C:\Users\mwill\Documents\mwilliams2733\sports_picks'
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
$Log = Join-Path $Repo 'digest_health.log'

if (-not (Test-Path $Python)) { throw "interpreter missing: $Python" }

Set-Location $Repo

# 2>&1 so a traceback lands in the health log rather than vanishing: a check
# that fails silently is worse than no check, because its silence reads as
# health.
$output = & $Python -m backend.scripts.check_digest 2>&1
$code = $LASTEXITCODE

$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
Add-Content -Path $Log -Value "===== $stamp (exit $code) ====="
Add-Content -Path $Log -Value $output

Write-Output $output
exit $code
