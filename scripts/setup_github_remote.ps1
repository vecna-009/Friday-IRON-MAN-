param(
    [Parameter(Mandatory = $true)]
    [string]$RemoteUrl,
    [string]$Branch = "main",
    [string]$InitialCommitMessage = "Initial commit"
)

$ErrorActionPreference = "Stop"

function Ensure-GitOnPath {
    $gitCmd = Get-Command git -ErrorAction SilentlyContinue
    if ($gitCmd) {
        return
    }

    $fallback = "C:\Program Files\Git\cmd"
    if (Test-Path (Join-Path $fallback "git.exe")) {
        $env:Path = "$fallback;$env:Path"
    }

    $gitCmd = Get-Command git -ErrorAction SilentlyContinue
    if (-not $gitCmd) {
        throw "Git is not installed or not on PATH. Install Git for Windows first."
    }
}

function Invoke-Git {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Command
    )

    Invoke-Expression $Command
    if ($LASTEXITCODE -ne 0) {
        throw "Git command failed: $Command"
    }
}

Ensure-GitOnPath

if (-not (Test-Path ".git")) {
    Invoke-Git "git init"
}

Invoke-Git "git branch -M $Branch"

$originExists = git remote | Select-String "^origin$"
if ($originExists) {
    Invoke-Git "git remote set-url origin $RemoteUrl"
} else {
    Invoke-Git "git remote add origin $RemoteUrl"
}

Invoke-Git "git add -A"

$hasStaged = git diff --cached --name-only
if (-not [string]::IsNullOrWhiteSpace($hasStaged)) {
    Invoke-Git "git commit -m '$InitialCommitMessage'"
}

Invoke-Git "git push -u origin $Branch"

Write-Host "Remote configured and pushed to $RemoteUrl on branch $Branch"
