# One-click "start work": pull latest from GitHub and sync uv deps.
# Run by double-clicking 开工.bat in the repo root.
$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent $PSScriptRoot

function Find-Git {
    $g = (Get-Command git -ErrorAction SilentlyContinue).Source
    if ($g) { return $g }
    foreach ($p in @("E:\Apps\Git\cmd\git.exe", "D:\Git\Git\cmd\git.exe",
                     "C:\Program Files\Git\cmd\git.exe",
                     "$env:LOCALAPPDATA\Programs\Git\cmd\git.exe")) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

function Find-Uv {
    $u = (Get-Command uv -ErrorAction SilentlyContinue).Source
    if ($u) { return $u }
    foreach ($p in @("$env:USERPROFILE\.local\bin\uv.exe",
                     "C:\Users\26943\.local\bin\uv.exe")) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

$git = Find-Git
if (-not $git) { Write-Output "[ERROR] git not found"; exit 1 }

Set-Location $repo
Write-Output "======== START WORK ========"
Write-Output "repo: $repo"

& $git fetch origin 2>&1 | Out-Null
$head = (& $git rev-parse --short HEAD)
$remote = (& $git rev-parse --short origin/main)
Write-Output "local  HEAD : $head"
Write-Output "remote main : $remote"

$dirty = @(& $git status --porcelain)
if ($dirty.Count -gt 0) {
    Write-Output "uncommitted: $($dirty.Count) file(s) (will be autostashed during pull)"
}

if ($head -ne $remote) {
    Write-Output "pulling from origin/main ..."
    & $git pull --rebase --autostash origin main 2>&1 | Select-Object -Last 8
} else {
    Write-Output "already up to date"
}

$uv = Find-Uv
if ($uv) {
    Set-Location (Join-Path $repo "backend")
    Write-Output "uv sync ..."
    & $uv sync 2>&1 | Select-Object -Last 4
} else {
    Write-Output "[WARN] uv not found, skip dependency sync"
}

Set-Location $repo
Write-Output ("HEAD now: " + (& $git rev-parse --short HEAD))
Write-Output "======== READY ========"
Write-Output "run: cd backend ; uv run uvicorn app.main:create_app --factory --reload"
