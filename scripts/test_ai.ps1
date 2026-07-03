param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs = @()
)

$ErrorActionPreference = "Stop"

function Invoke-Pytest {
    param([string[]]$ArgsForPytest)

    & uv run pytest @ArgsForPytest
    $code = $LASTEXITCODE

    # pytest returns 5 when --last-failed-no-failures=none selects no tests.
    if ($code -eq 5) {
        return 0
    }
    return $code
}

$cacheArgs = @("-o", "cache_dir=.pytest_cache_ai")

$lastFailedCode = Invoke-Pytest ($cacheArgs + @("--lf", "--last-failed-no-failures=none", "-q") + $PytestArgs)
if ($lastFailedCode -ne 0) {
    exit $lastFailedCode
}

$fastCode = Invoke-Pytest ($cacheArgs + @("-m", "fast", "-q") + $PytestArgs)
exit $fastCode
