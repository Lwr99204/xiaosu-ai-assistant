# One-click "end work": commit everything and push to GitHub (3 retries).
# Run by double-clicking 收工.bat in the repo root.
param([string]$Message = "")

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

$git = Find-Git
if (-not $git) { Write-Output "[ERROR] git not found"; exit 1 }

Set-Location $repo
Write-Output "======== END WORK ========"
Write-Output "repo: $repo"

$st = @(& $git status --porcelain)
if ($st.Count -gt 0) {
    & $git add -A
    if (-not $Message) {
        $Message = "wip: end-of-session snapshot " + (Get-Date -Format "yyyy-MM-dd HH:mm")
    }
    & $git commit -m $Message 2>&1 | Select-Object -Last 4
} else {
    Write-Output "no local changes to commit"
}

$ok = $false
for ($i = 1; $i -le 3; $i++) {
    & $git push origin main 2>&1 | Select-Object -Last 6
    if ($LASTEXITCODE -eq 0) { $ok = $true; Write-Output "push OK (attempt $i)"; break }
    Write-Output "push failed (attempt $i), retry in $($i * 5)s"
    Start-Sleep -Seconds ($i * 5)
}

if (-not $ok) {
    Write-Output "[WARN] push failed after 3 attempts. Commit is safe locally; try again later."
    Write-Output "       If network is blocked, enable the proxy (LeiSu / Clash) and rerun 收工.bat."
}

Write-Output ("local HEAD  = " + (& $git rev-parse --short HEAD))
& $git fetch origin 2>&1 | Out-Null
Write-Output ("remote main = " + (& $git rev-parse --short origin/main))
Write-Output "NOTE: .env / storage/ / .venv never sync (by design, see .gitignore)"
Write-Output "======== DONE ========"
