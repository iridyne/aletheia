# Aletheia dev launcher — full auto-reload stack.
#
# Architecture:
#   browser  → http://localhost:5173        (Vite dev, HMR — fastest feedback)
#   browser  → http://127.0.0.1:8787       (gateway, auto-refreshes on build)
#
# Processes managed:
#   1. Gateway (persistent, background — starts if not running)
#   2. vite build --watch → dashboard_static/ (auto-rebuilds for :8787)
#   3. watch_backend.py (auto-restarts gateway on .py changes)
#   4. vite dev :5173 (foreground, HMR)
#
# Usage:  pwsh -File scripts\dev.ps1   (or just  .\scripts\dev.ps1)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

# --- Safety check: refuse if live trading is enabled --------------------------
$ConfigPath = Join-Path $ProjectRoot 'data\config.yaml'
if (Test-Path $ConfigPath) {
    $configContent = Get-Content $ConfigPath -Raw
    if ($configContent -match 'state:\s*live_enabled') {
        Write-Error "REFUSED: live trading is enabled in config.yaml. Dev reload cannot run in live mode."
        exit 1
    }
}

# --- Set dev reload env var ---------------------------------------------------
$env:ALETHEIA_DEV_RELOAD = "1"

# --- 1. Ensure the gateway is running on 8787 --------------------------------
$Aletheia = Join-Path $ProjectRoot '.venv\Scripts\aletheia.exe'
if (-not (Test-Path $Aletheia)) {
    Write-Error "venv aletheia.exe not found at $Aletheia. Run 'uv sync' first."
}

$status = & $Aletheia gateway status 2>&1 | Out-String
if ($status -match 'Gateway:\s+running') {
    Write-Host "[ok] gateway already running on 127.0.0.1:8787" -ForegroundColor Green
    Write-Host "     NOTE: restart gateway to pick up ALETHEIA_DEV_RELOAD=1 for SSE endpoint" -ForegroundColor Yellow
} else {
    Write-Host "[..] starting gateway..." -ForegroundColor Yellow
    & $Aletheia gateway start | Out-Null
    Start-Sleep -Seconds 2
    $status = & $Aletheia gateway status 2>&1 | Out-String
    if ($status -match 'Gateway:\s+running') {
        Write-Host "[ok] gateway running on 127.0.0.1:8787" -ForegroundColor Green
    } else {
        Write-Error "gateway failed to start. Last status:`n$status"
    }
}

# --- 2. Ensure node_modules exists --------------------------------------------
$Frontend = Join-Path $ProjectRoot 'frontend'
$NodeModules = Join-Path $Frontend 'node_modules'
$Vite = Join-Path $NodeModules 'vite\bin\vite.js'

if (-not (Test-Path $Vite)) {
    Write-Host "[..] installing frontend deps (one-time)..." -ForegroundColor Yellow
    Push-Location $Frontend
    try {
        npm install --no-audit --no-fund --cache "$env:USERPROFILE\.npm-cache"
    } finally {
        Pop-Location
    }
}

# --- 3. Start vite build --watch (background) ---------------------------------
Write-Host "[..] starting vite build --watch (background)..." -ForegroundColor Yellow
$buildWatch = Start-Job -ScriptBlock {
    param($FrontendPath)
    Set-Location $FrontendPath
    & node node_modules\vite\bin\vite.js build --watch 2>&1
} -ArgumentList $Frontend
Write-Host "[ok] vite build --watch running (job $($buildWatch.Id))" -ForegroundColor Green

# --- 4. Start backend watcher (background) ------------------------------------
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$WatchScript = Join-Path $ProjectRoot 'scripts\watch_backend.py'

Write-Host "[..] starting backend watcher (background)..." -ForegroundColor Yellow
$backendWatch = Start-Job -ScriptBlock {
    param($PythonExe, $Script, $Root)
    $env:ALETHEIA_DEV_RELOAD = "1"
    Set-Location $Root
    & $PythonExe $Script 2>&1
} -ArgumentList $Python, $WatchScript, $ProjectRoot
Write-Host "[ok] backend watcher running (job $($backendWatch.Id))" -ForegroundColor Green

# --- 5. Launch Vite dev server (foreground) -----------------------------------
Write-Host ""
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "  Dev stack ready:" -ForegroundColor Cyan
Write-Host "    HMR dev    → http://127.0.0.1:5173/" -ForegroundColor Cyan
Write-Host "    Gateway    → http://127.0.0.1:8787/ (auto-refresh)" -ForegroundColor Cyan
Write-Host "    Ctrl+C     → stops Vite dev + watchers" -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host ""

Push-Location $Frontend
try {
    & node node_modules\vite\bin\vite.js --host 127.0.0.1 --port 5173
} finally {
    Pop-Location
    # Cleanup background jobs on exit
    Write-Host "`n[..] stopping background jobs..." -ForegroundColor Yellow
    Stop-Job $buildWatch -ErrorAction SilentlyContinue
    Remove-Job $buildWatch -Force -ErrorAction SilentlyContinue
    Stop-Job $backendWatch -ErrorAction SilentlyContinue
    Remove-Job $backendWatch -Force -ErrorAction SilentlyContinue
    Write-Host "[ok] dev stack stopped. Gateway continues running." -ForegroundColor Green
}
