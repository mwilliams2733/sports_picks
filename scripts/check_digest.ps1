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

    The PYTHON side appends the verdict to digest_health.log and rotates it
    past 128 KB, keeping 3 older generations. Rotation lives with the write
    so there is no way to append to an unrotated log, and it keeps several
    generations rather than the single .prev start_scheduler.ps1 uses -- a
    health record exists to answer "has this failed before?", which one
    generation cannot do.

    This script therefore does NOT write the log itself; a second writer
    would double every entry. It only catches the case Python cannot report
    on: the interpreter failing to start at all.

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

# 2>&1 so a traceback is captured rather than vanishing: a check that fails
# silently is worse than no check, because its silence reads as health.
$output = & $Python -m backend.scripts.check_digest 2>&1
$code = $LASTEXITCODE

Write-Output $output

# Python writes its own verdict. It can only fail to do so if it never got
# far enough to try -- a broken venv, a missing module -- and that case has
# to leave a trace too, or a dead check looks exactly like a quiet one.
if ($code -ne 0 -and ($output -join "`n") -notmatch '\[\d{4}-\d{2}-\d{2}\]') {
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -Path $Log -Value "===== $stamp (exit $code) ====="
    Add-Content -Path $Log -Value "LAUNCH FAILED -- python produced no verdict:"
    Add-Content -Path $Log -Value $output
}

exit $code
