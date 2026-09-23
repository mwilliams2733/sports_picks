<#
.SYNOPSIS
    Register the "sports_picks pitcher check" task.

.DESCRIPTION
    Runs scripts\check_pitcher_rows.ps1 daily at 09:18 local, as the
    interactive user, and records whether this morning's pitcher_skill_score
    rows landed cleanly.

    Why 09:18 local: the same trigger time as the "sports_picks digest
    check" task (see register_digest_check_task.ps1 for the full daylight
    -saving reasoning, not re-derived here) -- after the 8 ET morning scout
    and both its 9 ET and 10 ET retries, in both DST regimes. This machine
    is on Arizona time, which does not shift, while the scheduler runs on
    Eastern, which does.

    Why at a fixed time and not at logon, unlike the scheduler task: this one
    asks a question about a specific morning that has already happened.
    Running it at logon would ask about whatever day happened to be current
    then.

    The trade-off, stated plainly: if the machine is asleep or logged out at
    09:18, the check does not run that day. -StartWhenAvailable makes Windows
    run it late once the machine is back, which is the right behaviour here --
    a late answer about this morning is still the answer.

    It needs no credentials and no elevation: it reads a log file and a
    SQLite database, and writes only pitcher_health.log.

    Idempotent: re-registering replaces the existing task.

    Run:  powershell -ExecutionPolicy Bypass -File scripts\register_pitcher_check_task.ps1
#>

$ErrorActionPreference = 'Stop'

$TaskName = 'sports_picks pitcher check'
$Repo = 'C:\Users\mwill\Documents\mwilliams2733\sports_picks'
$Launcher = Join-Path $Repo 'scripts\check_pitcher_rows.ps1'

if (-not (Test-Path $Launcher)) { throw "launcher missing: $Launcher" }

$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Launcher`"" `
    -WorkingDirectory $Repo

$trigger = New-ScheduledTaskTrigger -Daily -At '09:18'

$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable
# The check finishes in seconds. A bound stops a wedged run holding the task
# in a running state until tomorrow, which would silence the next morning.
$settings.ExecutionTimeLimit = 'PT10M'

$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force `
    -Description 'Checks daily at 09:18 local (after the 8 ET morning scout and both retries, in both DST regimes) whether pitcher_skill_score rows landed for today, and whether any landed at exactly 0.5 (the plan-022 regression signal). Exit code 0 = healthy, 1 = look at pitcher_health.log.' | Out-Null

Write-Output "Registered '$TaskName'."
Get-ScheduledTask -TaskName $TaskName |
    Select-Object TaskName, State, @{n = 'At'; e = { $_.Triggers[0].StartBoundary } } |
    Format-List
