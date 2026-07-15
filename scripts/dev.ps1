# Worktree frontend HMR launcher. It never starts, stops, or restarts the main gateway.
# Browser: http://127.0.0.1:5173; API defaults to read-only master http://127.0.0.1:8787.
# To use an isolated API, first run `uv run aletheia dev api --port 8791`, then set
# `$env:VITE_API_TARGET = 'http://127.0.0.1:8791'` before invoking this script.

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

# --- Ensure node_modules exists ------------------------------------------------
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

if (-not $env:VITE_API_TARGET) { $env:VITE_API_TARGET = 'http://127.0.0.1:8787' }
Write-Host "[ok] Worktree HMR: http://127.0.0.1:5173" -ForegroundColor Green
Write-Host "[ok] API target: $env:VITE_API_TARGET (writes to :8787 are blocked)" -ForegroundColor Green
Push-Location $Frontend
try {
    npm run dev -- --host 127.0.0.1 --port 5173
} finally {
    Pop-Location
}
