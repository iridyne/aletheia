[CmdletBinding()]
param(
    [string]$BaseUrl = "http://127.0.0.1:8787",
    [int]$TimeoutSeconds = 30
)

$ErrorActionPreference = "Stop"
$deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
$lastError = $null

while ([DateTimeOffset]::UtcNow -lt $deadline) {
    try {
        $ready = Invoke-RestMethod -Uri "$($BaseUrl.TrimEnd('/'))/_infra/readyz" -TimeoutSec 3
        $health = Invoke-RestMethod -Uri "$($BaseUrl.TrimEnd('/'))/api/health" -TimeoutSec 5
        [pscustomobject]@{
            ok = $true
            base_url = $BaseUrl
            ready = $ready
            health = $health
        } | ConvertTo-Json -Depth 12
        exit 0
    }
    catch {
        $lastError = $_.Exception.Message
        Start-Sleep -Milliseconds 500
    }
}

[pscustomobject]@{
    ok = $false
    base_url = $BaseUrl
    timeout_seconds = $TimeoutSeconds
    error = $lastError
} | ConvertTo-Json -Depth 4
exit 1
