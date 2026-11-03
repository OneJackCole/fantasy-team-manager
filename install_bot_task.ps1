# Run the Discord bot in the background whenever you're logged in to Windows.
#
# Registers a Task Scheduler task ("Fantasy hockey Discord bot") that starts the bot at logon
# with no console window, restarts it if it crashes, and keeps it running on battery. Run it
# again after moving the repo. To remove it:
#   Unregister-ScheduledTask -TaskName "Fantasy hockey Discord bot" -Confirm:$false
#
# Usage (PowerShell, in this folder):
#   powershell -ExecutionPolicy Bypass -File install_bot_task.ps1

$name = 'Fantasy hockey Discord bot'
$folder = $PSScriptRoot
$pythonw = (Get-Command pythonw).Source

$action = New-ScheduledTaskAction -Execute $pythonw -Argument 'discord_bot.py' -WorkingDirectory $folder
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
    -Description 'Chat with the fantasy hockey agent from Discord' -Force | Out-Null
Start-ScheduledTask -TaskName $name
Write-Output "Registered and started '$name'. Its log is $folder\discord_bot.log"
