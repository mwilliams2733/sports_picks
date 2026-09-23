<#
.SYNOPSIS
    Check whether this morning's pitcher_skill_score rows landed, and record
    the verdict.

.DESCRIPTION
    Launched daily at 09:18 local by the "sports_picks pitcher check" task
    (see register_pitcher_check_task.ps1). Safe to run by hand at any time:
    it is read-only, touching neither the database nor scheduler.log.

    09:18 local is the same trigger time as check_digest.ps1, chosen for the
    same reason -- see register_pitcher_check_task.ps1's own docstring for
    the daylight-saving reasoning; it is not re-derived here.

    The PYTHON side appends the verdict to pitcher_health.log and rotates it
    past 128 KB, keeping 3 older generations, exactly like digest_health.log.
    This script does NOT write that log itself; a second writer would double
    every entry. It only catches the case Python cannot report on: the
    interpreter failing to start at all.

    The exit code is the real signal: 0 healthy, 1 not. Task Scheduler stores
    it as LastTaskResult, so:

        (Get-ScheduledTaskInfo 'sports_picks pitcher check').LastTaskResult

    answers the question without opening anything. 0 = fine, 1 = look --
    most often AMBIGUOUS_HALVES, meaning a row landed at exactly 0.5 and the
    plan-022 guard against a phantom neutral score has regressed.

    Paths are absolute and point at the venv interpreter. The .bat files in
    the repo root reference a OneDrive path that has not existed since July
    2026; do not copy from them.
#>

$ErrorActionPreference = 'Stop'

$Repo = 'C:\Users\mwill\Documents\mwilliams2733\sports_picks'
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
$Log = Join-Path $Repo 'pitcher_health.log'

if (-not (Test-Path $Python)) { throw "interpreter missing: $Python" }

Set-Location $Repo

# 2>&1 so a traceback is captured rather than vanishing: a check that fails
# silently is worse than no check, because its silence reads as health.
$output = & $Python -m backend.scripts.check_pitcher_rows 2>&1
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
