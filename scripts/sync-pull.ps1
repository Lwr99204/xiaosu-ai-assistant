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

$head = (& $git rev-parse --short HEAD)
Write-Output "local HEAD: $head"

$dirty = @(& $git status --porcelain)
if ($dirty.Count -gt 0) {
    Write-Output "uncommitted: $($dirty.Count) file(s) (autostashed during pull)"
}

$pulled = $false
for ($i = 1; $i -le 4; $i++) {
    & $git fetch origin 2>&1 | Out-Null
    $remote = (& $git rev-parse --short origin/main)
    if ($head -eq $remote) {
        Write-Output "already up to date (origin/main = $remote)"
        $pulled = $true
        break
    }
    Write-Output "pulling origin/main (attempt $i) ..."
    & $git pull --rebase --autostash origin main 2>&1 | Select-Object -Last 6
    if ($LASTEXITCODE -eq 0) { $pulled = $true; break }
    Write-Output "pull failed (attempt $i), retry in $($i * 8)s"
    Start-Sleep -Seconds ($i * 8)
}

if (-not $pulled) {
    Write-Output "[WARN] cannot reach GitHub from this network."
    Write-Output "  1) enable the proxy (LeiSu / Clash) and rerun 开工.bat, or"
    Write-Output "  2) on the other machine: git bundle create x.bundle main"
    Write-Output "     then here: git fetch x.bundle main ; git merge --ff-only FETCH_HEAD"
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
