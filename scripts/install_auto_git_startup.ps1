param(
    [string]$RepoPath = (Resolve-Path ".").Path,
    [string]$Branch = "main",
    [int]$IntervalSeconds = 30
)

$scriptPath = Join-Path $RepoPath "scripts\auto_git_sync.ps1"
if (-not (Test-Path $scriptPath)) {
    throw "Cannot find $scriptPath"
}

$startupDir = [Environment]::GetFolderPath("Startup")
$launcherPath = Join-Path $startupDir "FridayAutoGitSync.cmd"

$cmdContent = "@echo off`r`n" +
    "powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `""$scriptPath`"" -RepoPath `""$RepoPath`"" -Branch `""$Branch`"" -IntervalSeconds $IntervalSeconds`r`n"

Set-Content -Path $launcherPath -Value $cmdContent -Encoding ASCII
Write-Host "Startup launcher created: $launcherPath"
