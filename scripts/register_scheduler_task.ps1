<#
.SYNOPSIS
    Register the "sports_picks scheduler" task so the pipeline survives a reboot.

.DESCRIPTION
    Runs scripts\start_scheduler.ps1 at logon, and -- when this script itself
    is run elevated -- at startup as well.

    Why elevation changes the answer
    --------------------------------
    The pipeline reads secrets from the user's .secrets\shared.env and runs
    out of a per-user venv, so it wants the user's identity, not SYSTEM's.
    There are three ways to make a task run at boot and only one of them fits:

      SYSTEM      wrong profile and environment, and registering it needs
                  elevation anyway, so it buys nothing here.
      Password    runs as the user at boot, but requires storing the account
                  password. Not worth it for this.
      S4U         runs as the user, needs NO stored password, and runs whether
                  or not anyone is logged on. This is the one -- but
                  registering an S4U principal requires elevation.

    So: run this elevated and the task gets an at-startup trigger alongside the
    logon one. Run it unelevated and it gets logon only -- the previous
    behaviour -- and the script says so rather than pretending otherwise.

    A NON-elevated at-startup trigger is deliberately not offered. With an
    Interactive principal Windows accepts the trigger and then never fires it
    before logon, which is worse than not having it: the task list would claim
    boot coverage that does not exist.

    What S4U gives up: network CREDENTIALS. An S4U process cannot authenticate
    to file shares as the user. It can still open outbound connections, which
    is all this pipeline does (ESPN, the Odds API, Gmail SMTP), so the loss
    does not reach us.

    The gap this closes, stated plainly: this machine has AutoAdminLogon = 0,
    so with a logon trigger alone a reboot leaves the scheduler down until
    someone logs in. A reboot at 03:00 with a 09:00 login misses that day's
    08:00 ET morning_scout -- and therefore that day's digest, which is now
    downstream of it. scout_retry_9 and scout_retry_10 cover part of it.

    Both triggers firing is safe: start_scheduler.ps1 matches on the running
    command line and no-ops when a scheduler is already up.

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

$elevated = ([Security.Principal.WindowsPrincipal] `
        [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

$logon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
# Let the network and any VPN settle before the first ESPN/Odds API call.
$logon.Delay = 'PT1M'

if ($elevated) {
    $startup = New-ScheduledTaskTrigger -AtStartup
    # Longer than the logon delay: at boot the network stack, DNS and any VPN
    # are still coming up, and the first thing this process does is talk to
    # ESPN and the Odds API.
    $startup.Delay = 'PT3M'
    $triggers = @($startup, $logon)
    $principal = New-ScheduledTaskPrincipal `
        -UserId "$env:USERDOMAIN\$env:USERNAME" `
        -LogonType S4U `
        -RunLevel Limited
}
else {
    $triggers = @($logon)
    $principal = New-ScheduledTaskPrincipal `
        -UserId "$env:USERDOMAIN\$env:USERNAME" `
        -LogonType Interactive `
        -RunLevel Limited
}

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

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
    -Settings $settings -Principal $principal -Force `
    -Description 'Starts the sports_picks pipeline scheduler at logon (and at startup when registered elevated) if it is not already running.' | Out-Null

Write-Output "Registered '$TaskName'."

if (-not $elevated) {
    Write-Warning @"
Registered with a LOGON trigger only.

An at-startup trigger needs an S4U principal, and registering one requires
elevation. Without it the scheduler stays down after a reboot until someone
logs in -- and this machine does not auto-login (AutoAdminLogon = 0).

To add boot coverage, open PowerShell as Administrator and re-run:

    powershell -ExecutionPolicy Bypass -File "$Repo\scripts\register_scheduler_task.ps1"

Nothing else changes -- re-registering replaces the task in place, and the
logon trigger is kept alongside the new startup one.
"@
}

Get-ScheduledTask -TaskName $TaskName |
    Select-Object TaskName, State,
    @{n = 'LogonType'; e = { $_.Principal.LogonType } },
    @{n = 'Triggers'; e = { ($_.Triggers | ForEach-Object {
                $_.CimClass.CimClassName.Replace('MSFT_Task', '').Replace('Trigger', '') }) -join ', ' } } |
    Format-List
