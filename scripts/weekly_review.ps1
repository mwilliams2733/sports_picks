<#
.SYNOPSIS
    Write this week's review: the model, Claude and the closing line.

.DESCRIPTION
    Launched Tuesdays at 09:41 local by the "sports_picks weekly review" task
    (see register_weekly_review_task.ps1). Safe to run by hand.

    The week is Tuesday..Monday, ending yesterday, so Monday night's game is
    in it. The 8am ET morning scout grades it first: that is 05:00 local in
    EDT and 06:00 in EST (this machine is on Arizona time), both before 09:41.

    It reads a sqlite .backup snapshot, never the live file (a copy of the
    live file misses whatever is still in the WAL), writes
    logs-archive\weekly-review-<end date>.txt, and deletes the snapshot.

    Exit code: 0 = report written, 1 = look at the output.
#>

$ErrorActionPreference = 'Stop'

$Repo = 'C:\Users\mwill\Documents\mwilliams2733\sports_picks'
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
$Snap = Join-Path $Repo 'logs-archive\weekly-review-snap.db'

if (-not (Test-Path $Python)) { throw "interpreter missing: $Python" }
Set-Location $Repo

$end = (Get-Date).AddDays(-1).ToString('yyyy-MM-dd')
$Out = Join-Path $Repo "logs-archive\weekly-review-$end.txt"

try {
    & $Python -c "import sqlite3,sys; s=sqlite3.connect('sports_picks.db'); d=sqlite3.connect(sys.argv[1]); s.backup(d); d.close(); s.close()" $Snap
    if ($LASTEXITCODE -ne 0) { throw "snapshot failed (exit $LASTEXITCODE)" }
    $output = & $Python -m backend.scripts.weekly_review --db $Snap --end $end --out $Out 2>&1
    $code = $LASTEXITCODE
    Write-Output $output
    if ($code -ne 0) {
        # Leave a trace: a review that fails silently reads as a quiet week.
        Set-Content -Path $Out -Value (@("REVIEW FAILED (exit $code):") + $output)
    }
}
finally {
    if (Test-Path $Snap) { Remove-Item $Snap -Force -Confirm:$false }
}
exit $code
