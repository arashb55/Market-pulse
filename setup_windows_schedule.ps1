# Creates a Windows scheduled task that runs Market Pulse every 15 minutes,
# Mon-Fri, 6:00 am - 2:45 pm local time. The script itself only builds a
# snapshot at the open, midday and close (New York time), once each per day,
# so this keeps working through every DST change.
# Run once from PowerShell in this folder:  powershell -ExecutionPolicy Bypass -File setup_windows_schedule.ps1

$dir    = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = (Get-Command pyw -ErrorAction SilentlyContinue).Source
if (-not $python) { $python = (Get-Command pythonw -ErrorAction SilentlyContinue).Source }
if (-not $python) { Write-Error "Python not found. Install it from python.org (tick 'Add to PATH')."; exit 1 }

$action  = New-ScheduledTaskAction -Execute $python -Argument "`"$dir\market_pulse.py`" --auto" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 6:00am
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At 6:00am -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Hours 8 -Minutes 45)).Repetition
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask -TaskName "Market Pulse" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "Scheduled task 'Market Pulse' created. Snapshots at the open, midday and close."
