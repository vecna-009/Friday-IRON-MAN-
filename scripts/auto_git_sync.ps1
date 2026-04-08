param(
    [string]$RepoPath = ".",
    [string]$Branch = "main",
    [int]$IntervalSeconds = 30,
    [string]$CommitPrefix = "auto: sync"
)

$ErrorActionPreference = "Stop"

function Test-GitAvailable {
    $gitCmd = Get-Command git -ErrorAction SilentlyContinue
    if (-not $gitCmd) {
        $fallback = "C:\Program Files\Git\cmd"
        if (Test-Path (Join-Path $fallback "git.exe")) {
            $env:Path = "$fallback;$env:Path"
            $gitCmd = Get-Command git -ErrorAction SilentlyContinue
        }
    }

    if (-not $gitCmd) {
        throw "Git is not installed or not on PATH. Install Git for Windows first."
    }
}

function Ensure-Repo([string]$Path) {
    Set-Location $Path
    git rev-parse --is-inside-work-tree *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Path '$Path' is not a git repository. Run initial setup first."
    }
}

Test-GitAvailable
Ensure-Repo -Path $RepoPath

Write-Host "Auto Git sync started for repo: $(Get-Location)"
Write-Host "Branch: $Branch | Interval: $IntervalSeconds seconds"

while ($true) {
    git add -A

    $staged = git diff --cached --name-only
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Failed to inspect staged changes. Retrying in $IntervalSeconds seconds."
        Start-Sleep -Seconds $IntervalSeconds
        continue
    }

    if (-not [string]::IsNullOrWhiteSpace($staged)) {
        $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        $message = "$CommitPrefix $timestamp"

        git commit -m $message
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Commit failed. Check git config (user.name/user.email) or conflicts."
            Start-Sleep -Seconds $IntervalSeconds
            continue
        }

        git push origin $Branch
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Push failed. Check remote/auth/network and branch name."
        } else {
            Write-Host "Committed and pushed: $message"
        }
    }

    Start-Sleep -Seconds $IntervalSeconds
}
