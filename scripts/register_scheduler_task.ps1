<#
.SYNOPSIS
    Register the "sports_picks scheduler" task so the pipeline survives a reboot.

.DESCRIPTION
    Runs scripts\start_scheduler.ps1 at logon, as the interactive user.

    Why at logon and not at startup: the pipeline reads secrets from
    C:\Users\mwill\.secrets\shared.env and runs out of a per-user venv. An
    at-startup task runs as SYSTEM, which has no user profile and a different
    environment; running at startup AS the user instead would require storing
    the account password. Logon needs no credentials and the process gets
    exactly the environment it has when started by hand.

    The trade-off, stated plainly: if the machine reboots and nobody logs in,
    the scheduler does not start. A reboot at 03:00 with a 09:00 login misses
    that day's 08:00 morning_scout -- though scout_retry_9 and scout_retry_10
    exist for roughly this case.

    Idempotent: re-registering replaces the existing task.

    Run:  powershell -ExecutionPolicy Bypass -File scripts\register_scheduler_task.ps1
#>

$ErrorActionPreference = 'Stop'

$TaskName = 'sports_picks scheduler'
$Repo = 'C:\Users\mwill\Documents\mwilliams2733\sports_picks'
$Launcher = Join-Path $Repo 'scripts\start_scheduler.ps1'

if (-not (Test-Path $Launcher)) { throw "launcher missing: $Launcher" }

$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Launcher`"" `
    -WorkingDirectory $Repo

$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
# Let the network and any VPN settle before the first ESPN/Odds API call.
$trigger.Delay = 'PT1M'

$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5)
# 0 = run indefinitely. The default is 3 days, which would kill a long-lived
# scheduler mid-week for no reason.
$settings.ExecutionTimeLimit = 'PT0S'

$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force `
    -Description 'Starts the sports_picks pipeline scheduler at logon if it is not already running.' | Out-Null

Write-Output "Registered '$TaskName'."
Get-ScheduledTask -TaskName $TaskName |
    Select-Object TaskName, State, @{n = 'Trigger'; e = { $_.Triggers[0].CimClass.CimClassName } } |
    Format-List
