<#
.SYNOPSIS
    Register the "sports_picks weekly review" task.

.DESCRIPTION
    Runs scripts\weekly_review.ps1 every Tuesday at 09:41 local, as the
    interactive user. The week ends Monday, and Monday night's game is graded
    by the 8am ET morning scout -- 05:00 local in EDT, 06:00 in EST (Arizona
    does not shift; Eastern does) -- so 09:41 is after it in both regimes.

    -StartWhenAvailable: if the machine is asleep at 09:41, Windows runs it
    late once it wakes, which is right here -- a late review of last week is
    still the review of last week.

    No credentials, no elevation: it snapshots the database, reads the
    snapshot, and writes one file under logs-archive.

    Idempotent: re-registering replaces the existing task.

    Run:  powershell -ExecutionPolicy Bypass -File scripts\register_weekly_review_task.ps1
#>

$ErrorActionPreference = 'Stop'

$TaskName = 'sports_picks weekly review'
$Repo = 'C:\Users\mwill\Documents\mwilliams2733\sports_picks'
$Launcher = Join-Path $Repo 'scripts\weekly_review.ps1'

if (-not (Test-Path $Launcher)) { throw "launcher missing: $Launcher" }

$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Launcher`"" `
    -WorkingDirectory $Repo

$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Tuesday -At '09:41'

$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable
$settings.ExecutionTimeLimit = 'PT20M'

$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force `
    -Description 'Tuesdays 09:41 local: writes logs-archive\weekly-review-<date>.txt (model, Claude, closing line value, prop calibration). Exit 0 = written.' | Out-Null

Write-Output "Registered '$TaskName'."
Get-ScheduledTask -TaskName $TaskName |
    Select-Object TaskName, State, @{n = 'At'; e = { $_.Triggers[0].StartBoundary } } |
    Format-List
