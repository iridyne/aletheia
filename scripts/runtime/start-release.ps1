[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$ReleaseRoot = ".",

    [Parameter(Mandatory = $true)]
    [string]$DataDir,

    [switch]$SkipSync
)

$ErrorActionPreference = "Stop"

$resolvedRelease = (Resolve-Path -LiteralPath $ReleaseRoot).Path
if (-not [System.IO.Path]::IsPathRooted($DataDir)) {
    throw "DataDir must be an absolute path."
}

New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
$resolvedData = (Resolve-Path -LiteralPath $DataDir).Path
$env:ALETHEIA_DATA_DIR = $resolvedData

Push-Location $resolvedRelease
try {
    if (-not $SkipSync) {
        uv sync --locked --no-dev
        if ($LASTEXITCODE -ne 0) {
            throw "uv sync failed with exit code $LASTEXITCODE"
        }
    }

    uv run --no-sync aletheia gateway start --no-browser
    if ($LASTEXITCODE -ne 0) {
        throw "Aletheia gateway start failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
