# ═══════════════════════════════════════════════════════════════════════
# start.ps1 — Windows 原生「一条命令启动」（对应 scripts/start.sh，FR-6-01）
#
# 用法（任选其一）：
#   .\scripts\start.ps1                # 启动 Embedding + 后端(含 IM) + 前端
#   .\scripts\start.ps1 -Stop          # 停止由本脚本拉起的全部进程
#   .\scripts\start.ps1 -NoPause       # 启动后不等待按键（给 CI / bat 用）
#   双击仓库根的「一键启动.bat」        # 等价于第一行
#
# 设计与 start.sh 保持一致：
#   · 全程出向连接，只监听 127.0.0.1（ADR-11 / TDD §11.2）
#   · IM 长连接跑在 FastAPI lifespan 内，随 backend 一起起停（TDD §4.6.3）
#   · 只写 storage/run/*.pid，-Stop 只杀这些 PID 的进程树，不误杀别的 Python/Node
#   · 从不打印 .env 里的变量值
# ═══════════════════════════════════════════════════════════════════════

[CmdletBinding()]
param(
    [switch]$Stop,
    [switch]$NoPause
)

$ErrorActionPreference = 'Stop'
$script:Root = Split-Path -Parent $PSScriptRoot
$script:RunDir = Join-Path $script:Root 'storage\run'
$script:LogDir = Join-Path $script:Root 'logs'

function Write-Step([string]$msg) { Write-Host "[start] $msg" -ForegroundColor Cyan }
function Fail([string]$msg) { Write-Host "[start] x $msg" -ForegroundColor Red; exit 1 }

# ── .env 加载（只在当前进程及子进程生效，不打印值）────────────────────
function Import-DotEnv([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) { Fail '缺少 .env —— 请先执行 bash scripts/setup.sh（会从 .env.example 复制）' }
    foreach ($line in Get-Content -LiteralPath $path -Encoding UTF8) {
        $t = $line.Trim()
        if ($t.Length -eq 0 -or $t.StartsWith('#')) { continue }
        if ($t -match '^export\s+') { $t = $t -replace '^export\s+', '' }
        $idx = $t.IndexOf('=')
        if ($idx -lt 1) { continue }
        $key = $t.Substring(0, $idx).Trim()
        $val = $t.Substring($idx + 1).Trim()
        if ($val.Length -ge 2) {
            if (($val.StartsWith('"') -and $val.EndsWith('"')) -or ($val.StartsWith("'") -and $val.EndsWith("'"))) {
                $val = $val.Substring(1, $val.Length - 2)
            }
        }
        Set-Item -Path "Env:$key" -Value $val
    }
}

function Get-EnvPort([string]$name, [int]$default) {
    $v = [Environment]::GetEnvironmentVariable($name, 'Process')
    if ([string]::IsNullOrWhiteSpace($v)) { return $default }
    $n = 0
    if ([int]::TryParse($v, [ref]$n)) { return $n }
    return $default
}

function Test-Http([string]$url) {
    try {
        $r = Invoke-WebRequest -Uri $url -TimeoutSec 3 -UseBasicParsing -ErrorAction Stop
        return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 400)
    } catch { return $false }
}

function Test-EmbeddingHealth([string]$url) {
    try {
        $r = Invoke-RestMethod -Uri $url -TimeoutSec 3 -ErrorAction Stop
        return ($r.status -eq 'ok' -and $r.model -eq 'bge-m3' -and $r.loaded -eq $true)
    } catch { return $false }
}

function Test-EmbeddingPython([string]$python, [string]$modelDir) {
    if ([string]::IsNullOrWhiteSpace($python) -or -not (Test-Path -LiteralPath $python -PathType Leaf)) { return $false }
    $onnxModel = Join-Path $modelDir 'onnx\model.onnx'
    if (Test-Path -LiteralPath $onnxModel -PathType Leaf) {
        & $python -c 'import fastapi, uvicorn, numpy, onnxruntime, tokenizers' 2>$null
    } else {
        & $python -c 'import fastapi, uvicorn, numpy, torch, sentence_transformers' 2>$null
    }
    return ($LASTEXITCODE -eq 0)
}

function Resolve-EmbeddingPython([string]$configured, [string]$modelDir) {
    if (-not [string]::IsNullOrWhiteSpace($configured)) {
        $candidate = $configured
        if (-not [IO.Path]::IsPathRooted($candidate)) { $candidate = Join-Path $script:Root $candidate }
        if (Test-EmbeddingPython $candidate $modelDir) { return (Resolve-Path -LiteralPath $candidate).Path }
        Fail "BGE_PYTHON 不可用或缺少模型运行依赖：$candidate"
    }
    foreach ($candidate in @(
            (Join-Path $script:Root 'backend\.venv-embedding\Scripts\python.exe'),
            (Join-Path $script:Root 'backend\.venv\Scripts\python.exe'),
            (Join-Path $script:Root 'backend\venv\Scripts\python.exe'))) {
        if (Test-EmbeddingPython $candidate $modelDir) { return (Resolve-Path -LiteralPath $candidate).Path }
    }
    return $null
}

function Test-Listening([int]$port) {
    $null -ne (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1)
}

function Save-Pid([string]$name, $process) {
    if ($null -eq $process) { return }
    Set-Content -LiteralPath (Join-Path $script:RunDir "$name.pid") -Value $process.Id -Encoding ASCII
}

# ── -Stop：只停本项目 PID 文件记录的进程树 ────────────────────────────
function Stop-All {
    Write-Step '读取本项目 PID 文件 …'
    if (Test-Path -LiteralPath $script:RunDir) {
        $files = Get-ChildItem -LiteralPath $script:RunDir -Filter '*.pid' -ErrorAction SilentlyContinue
        if (-not $files) { Write-Step '  无 PID 文件' }
        foreach ($f in $files) {
            $pidText = (Get-Content -LiteralPath $f.FullName -Encoding ASCII -ErrorAction SilentlyContinue) -join ''
            $procId = ($pidText -replace '[^0-9]', '')
            if ($procId) {
                # taskkill 不一定在 PATH 里（本机 PowerShell 7 实测会找不到），按绝对路径调用；
                # 找不到再退回 Stop-Process，宁可少杀子进程也不要整段失败。
                $taskkill = Join-Path $env:SystemRoot 'System32\taskkill.exe'
                if (Test-Path -LiteralPath $taskkill) {
                    & $taskkill /PID $procId /T /F > $null 2>&1
                } else {
                    Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
                }
                Write-Step ("  已停止 {0}（PID {1}）" -f $f.BaseName, $procId)
            }
            Remove-Item -LiteralPath $f.FullName -Force -ErrorAction SilentlyContinue
        }
        # 兜底：PID 文件丢了但端口还在监听，按端口找本项目日志里记录过的进程不现实，
        # 这里只提示，绝不擅自 kill 未知进程。
        foreach ($port in @($script:BackendPort, $script:FrontendPort)) {
            if ($port -and (Test-Listening $port)) {
                Write-Step "  提示：端口 $port 仍有进程在监听（可能不是本项目启动的，请自行确认）"
            }
        }
    }
    Write-Step '✅ stop 完成'
}

# ── 前置：目录、.env、端口 ────────────────────────────────────────────
if (-not (Test-Path -LiteralPath $script:RunDir)) { New-Item -ItemType Directory -Path $script:RunDir -Force > $null }
if (-not (Test-Path -LiteralPath $script:LogDir)) { New-Item -ItemType Directory -Path $script:LogDir -Force > $null }

Import-DotEnv (Join-Path $script:Root '.env')
$script:BackendPort  = Get-EnvPort 'BACKEND_PORT' 8000
$script:FrontendPort = Get-EnvPort 'FRONTEND_PORT' 3000
$script:EmbedPort    = Get-EnvPort 'BGE_PORT' 8001
$script:EmbedHealth  = "http://127.0.0.1:$($script:EmbedPort)/health"
$script:HealthUrl    = "http://127.0.0.1:$($script:BackendPort)/api/health"
$script:BgeModelDir  = [Environment]::GetEnvironmentVariable('BGE_MODEL_DIR', 'Process')
$script:BgePython    = [Environment]::GetEnvironmentVariable('BGE_PYTHON', 'Process')

if ($Stop) { Stop-All; exit 0 }

# 兼容旧 .env 没写 BACKEND_URL / BASE_URL 的情况，补一条给前端与验收脚本用
if ([string]::IsNullOrWhiteSpace($env:BACKEND_URL)) { Set-Item -Path 'Env:BACKEND_URL' -Value "http://127.0.0.1:$($script:BackendPort)" }
if ([string]::IsNullOrWhiteSpace($env:BASE_URL))    { Set-Item -Path 'Env:BASE_URL'    -Value "http://127.0.0.1:$($script:BackendPort)" }

# ── 步骤 1：前置检查 ──────────────────────────────────────────────────
Write-Step '1/5 前置检查 …'
$py = Join-Path $script:Root 'backend\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $py)) { Fail '未找到 backend\.venv —— 请先执行 bash scripts/setup.sh 或 uv sync' }

$pnpm = (Get-Command pnpm.cmd -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source)
if (-not $pnpm) { $pnpm = (Get-Command pnpm -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source) }
if (-not $pnpm) {
    foreach ($cand in @(
            (Join-Path $env:APPDATA 'npm\pnpm.cmd'),
            'E:\Apps\nodejs\npm-global\pnpm.cmd')) {
        if (Test-Path -LiteralPath $cand) { $pnpm = $cand; break }
    }
}
if (-not $pnpm) { Fail '未找到 pnpm —— 请先执行 bash scripts/setup.sh' }

$node = (Get-Command node.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source)
if (-not $node) { Fail '未找到 node —— 请先安装 Node 22 并确保在 PATH 中' }

# ── 步骤 2：embedding 服务自检（M3 起为硬依赖，R17）──────────────────
Write-Step '2/5 检查本地 embedding 服务 …'
if (Test-EmbeddingHealth $script:EmbedHealth) {
    Write-Step "  OK $($script:EmbedHealth) 可达"
} else {
    if (Test-Listening $script:EmbedPort) {
        Fail "端口 $($script:EmbedPort) 已被占用，但响应不是可用的 BGE-M3 服务"
    }
    if ([string]::IsNullOrWhiteSpace($script:BgeModelDir)) { Fail 'Embedding 未运行且未设置 BGE_MODEL_DIR（参见 .env.example）' }
    if (-not (Test-Path -LiteralPath $script:BgeModelDir)) { Fail "BGE_MODEL_DIR 不存在：$($script:BgeModelDir)" }
    $embedPython = Resolve-EmbeddingPython $script:BgePython $script:BgeModelDir
    if (-not $embedPython) {
        Fail '未找到可运行该模型的 Embedding Python。请按 README 创建 backend\.venv-embedding，或设置 BGE_PYTHON'
    }
    $entry = Join-Path $script:Root 'backend\dev_tools\embedding_service.py'
    if (-not (Test-Path -LiteralPath $entry)) { Fail '未找到 backend\dev_tools\embedding_service.py' }
    Write-Step '  启动本地 BGE-M3（首次加载可能需要约 1 分钟）…'
    $p = Start-Process -FilePath $embedPython -ArgumentList $entry -WorkingDirectory (Join-Path $script:Root 'backend') `
        -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $script:LogDir 'embedding.log') `
        -RedirectStandardError (Join-Path $script:LogDir 'embedding.err.log')
    Save-Pid 'embedding' $p
    $ready = $false
    for ($i = 1; $i -le 90; $i++) {
        if (Test-EmbeddingHealth $script:EmbedHealth) { $ready = $true; break }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) { Fail 'Embedding 启动超时，请查看 logs\embedding.log' }
    Write-Step '  OK Embedding ready'
}

# ── 步骤 3：后端（含 IM 长连接）───────────────────────────────────────
Write-Step "3/5 启动后端 :$($script:BackendPort) …"
if (Test-Listening $script:BackendPort) {
    Write-Step "  ⚠ 端口 $($script:BackendPort) 已在监听，跳过后端启动（如需重启请先 .\scripts\start.ps1 -Stop）"
} elseif (Test-Path -LiteralPath (Join-Path $script:Root 'backend\pyproject.toml')) {
    $p = Start-Process -FilePath $py `
        -ArgumentList '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$($script:BackendPort)" `
        -WorkingDirectory (Join-Path $script:Root 'backend') -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $script:LogDir 'backend.log') `
        -RedirectStandardError (Join-Path $script:LogDir 'backend.err.log')
    Save-Pid 'backend' $p
} else {
    Write-Step '  ⚠ backend 尚未初始化（M1），跳过'
}

# ── 步骤 4：前端 ──────────────────────────────────────────────────────
Write-Step "4/5 启动前端 :$($script:FrontendPort) …"
if (Test-Listening $script:FrontendPort) {
    Write-Step "  ⚠ 端口 $($script:FrontendPort) 已在监听，跳过前端启动（如需重启请先 .\scripts\start.ps1 -Stop）"
} elseif (Test-Path -LiteralPath (Join-Path $script:Root 'frontend\package.json')) {
    $env:BACKEND_URL = "http://127.0.0.1:$($script:BackendPort)"
    $p = Start-Process -FilePath 'cmd.exe' `
        -ArgumentList '/c', "$pnpm dev --hostname 127.0.0.1 --port $($script:FrontendPort)" `
        -WorkingDirectory (Join-Path $script:Root 'frontend') -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $script:LogDir 'frontend.log') `
        -RedirectStandardError (Join-Path $script:LogDir 'frontend.err.log')
    Save-Pid 'frontend' $p
} else {
    Write-Step '  ⚠ frontend 尚未初始化（M8），跳过'
}

# ── 步骤 5：轮询健康检查直到 ready ────────────────────────────────────
Write-Step '5/5 等待服务就绪 …'
$ready = $false
for ($i = 1; $i -le 60; $i++) {
    if (Test-Http $script:HealthUrl) { $ready = $true; break }
    Start-Sleep -Seconds 1
}
if (-not $ready) { Fail '后端启动超时，请查看 logs\backend.log' }
Write-Step '  OK 后端 ready'

$feReady = $false
for ($i = 1; $i -le 90; $i++) {
    if (Test-Listening $script:FrontendPort) { $feReady = $true; break }
    Start-Sleep -Seconds 1
}
if ($feReady) { Write-Step '  OK 前端 ready' } else { Write-Step '  ⚠ 前端 90s 内未就绪，查看 logs\frontend.log（不影响后端与 IM）' }

Write-Step ''
Write-Step "  后端 API  -> $($script:HealthUrl)"
Write-Step "  Web 后台  -> http://127.0.0.1:$($script:FrontendPort)"
Write-Step "  IM 长连接 -> 由 WECOM_ENABLED 控制，跑在 backend lifespan 内"
Write-Step "  日志目录  -> $($script:LogDir)"
Write-Step "  停止服务  -> .\scripts\start.ps1 -Stop"
Write-Step ''
Write-Step '可以关掉这个窗口；服务已在后台运行。'

if (-not $NoPause) {
    try { Read-Host '按回车关闭窗口' > $null } catch { }
}
