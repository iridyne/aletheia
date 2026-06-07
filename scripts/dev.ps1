# Aletheia dev launcher — frontend hot reload + persistent gateway.
#
# Architecture:
#   browser  → http://localhost:5173        (Vite dev, HMR)
#                  └─ /api/*  proxied to →  http://127.0.0.1:8787  (gateway, persistent)
#
# Source edits in frontend/src/* hot-reload in <1s. Backend .py edits still require
# `aletheia gateway restart`.
#
# Usage:  pwsh -File scripts\dev.ps1   (or just  .\scripts\dev.ps1  in a PowerShell prompt)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

# --- 1. Ensure the gateway is running on 8787 -----------------------------
$Aletheia = Join-Path $ProjectRoot '.venv\Scripts\aletheia.exe'
if (-not (Test-Path $Aletheia)) {
    Write-Error "venv aletheia.exe not found at $Aletheia. Run 'uv sync' first."
}

$status = & $Aletheia gateway status 2>&1 | Out-String
if ($status -match 'Gateway:\s+running') {
    Write-Host "[ok] gateway already running on 127.0.0.1:8787" -ForegroundColor Green
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

# --- 2. Ensure node_modules exists ---------------------------------------
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

# --- 3. Launch Vite dev server (foreground) ------------------------------
Write-Host ""
Write-Host "Vite dev → http://127.0.0.1:5173/   (HMR active; Ctrl+C to stop Vite, gateway keeps running)" -ForegroundColor Cyan
Write-Host ""

Push-Location $Frontend
try {
    & node node_modules\vite\bin\vite.js --host 127.0.0.1 --port 5173
} finally {
    Pop-Location
}
