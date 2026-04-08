param(
    [string]$RepoPath = (Resolve-Path ".").Path,
    [string]$Branch = "main",
    [int]$IntervalSeconds = 30,
    [string]$TaskName = "FridayAutoGitSync"
)

$scriptPath = Join-Path $RepoPath "scripts\auto_git_sync.ps1"
if (-not (Test-Path $scriptPath)) {
    throw "Cannot find $scriptPath"
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" -RepoPath `"$RepoPath`" -Branch `"$Branch`" -IntervalSeconds $IntervalSeconds"
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force -ErrorAction Stop | Out-Null
Write-Host "Scheduled task '$TaskName' registered. It will auto-start at logon."
