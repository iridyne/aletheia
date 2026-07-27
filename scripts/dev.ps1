# Worktree frontend HMR launcher. It never starts, stops, restarts, or updates the stable gateway.
#
# Default browser: http://127.0.0.1:5173
# Default API:     read-only stable gateway http://127.0.0.1:8787
#
# For an isolated backend Preview, start it separately:
#   uv run aletheia dev api --port 8791
# Then launch this Worktree UI with:
#   .\scripts\dev.ps1 -UiPort 5173 -ApiPort 8791

[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int]$UiPort = 5173,

    [int]$ApiPort = 0,

    [string]$ApiTarget = ''
)

$ErrorActionPreference = 'Stop'

if ($UiPort -in @(8787, 8788)) {
    throw "UiPort $UiPort is reserved for the stable Aletheia Runtime."
}

if ($ApiPort -ne 0) {
    if ($ApiPort -lt 1024 -or $ApiPort -gt 65535) {
        throw "ApiPort must be 0 (unset) or between 1024 and 65535."
    }
    if ($ApiTarget) {
        throw "Specify either -ApiPort or -ApiTarget, not both."
    }
    $ApiTarget = "http://127.0.0.1:$ApiPort"
}

if (-not $ApiTarget) {
    $ApiTarget = 'http://127.0.0.1:8787'
}

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

$env:VITE_API_TARGET = $ApiTarget
Write-Host "[ok] Worktree HMR: http://127.0.0.1:$UiPort" -ForegroundColor Green
Write-Host "[ok] API target: $ApiTarget" -ForegroundColor Green
if ($ApiTarget -eq 'http://127.0.0.1:8787') {
    Write-Host "[ok] Stable API proxy is read-only; non-GET writes are blocked." -ForegroundColor Green
} else {
    Write-Host "[ok] Verify the target is an isolated 'aletheia dev api' Preview." -ForegroundColor Yellow
}

Push-Location $Frontend
try {
    npm run dev -- --host 127.0.0.1 --port $UiPort
} finally {
    Pop-Location
}
