<#
.SYNOPSIS
    Register the "sports_picks digest check" task.

.DESCRIPTION
    Runs scripts\check_digest.ps1 daily at 09:18 local, as the interactive
    user, and records whether that morning's digest went out.

    Why 09:18 LOCAL, and why that number is not arbitrary.

    This machine runs on Arizona time (UTC-07:00, no DST). The pipeline
    scheduler is explicitly ET -- BackgroundScheduler(timezone=ET) -- so the
    digest fires at 11:00 Eastern regardless of the machine clock. A Task
    Scheduler trigger, by contrast, is in LOCAL time, so the two have to be
    reconciled by hand:

        EDT (Mar-Nov): ET = local + 3  ->  digest at 08:00 local
        EST (Nov-Mar): ET = local + 2  ->  digest at 09:00 local

    Arizona does not shift but Eastern does, so the gap is three hours for
    part of the year and two for the rest. 09:18 local is after the digest in
    BOTH regimes -- 12:18 ET in summer, 11:18 ET in winter. An 08:18 trigger
    would look right today and start running BEFORE the digest in November,
    reporting "never ran" every morning all winter.

    Why at a fixed time and not at logon, unlike the scheduler task: this one
    asks a question about a specific moment that has already passed. Running
    it at logon would ask about whatever day happened to be current then.

    The trade-off, stated plainly: if the machine is asleep or logged out at
    09:18, the check does not run that day. -StartWhenAvailable makes Windows
    run it late once the machine is back, which is the right behaviour here --
    a late answer about this morning is still the answer.

    It needs no credentials and no elevation: it reads a log file and a
    SQLite database, and writes only digest_health.log.

    Idempotent: re-registering replaces the existing task.

    Run:  powershell -ExecutionPolicy Bypass -File scripts\register_digest_check_task.ps1
#>

$ErrorActionPreference = 'Stop'

$TaskName = 'sports_picks digest check'
$Repo = 'C:\Users\mwill\Documents\mwilliams2733\sports_picks'
$Launcher = Join-Path $Repo 'scripts\check_digest.ps1'

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
    -Description 'Checks daily at 09:18 local (after the 11:00 ET digest in both DST regimes) whether that morning digest was sent. Exit code 0 = healthy, 1 = look at digest_health.log.' | Out-Null

Write-Output "Registered '$TaskName'."
Get-ScheduledTask -TaskName $TaskName |
    Select-Object TaskName, State, @{n = 'At'; e = { $_.Triggers[0].StartBoundary } } |
    Format-List
