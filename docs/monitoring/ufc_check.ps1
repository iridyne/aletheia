<#
.SYNOPSIS
    UFC CASI Monitor Check — runs all UFC observability checks and appends a summary line.
.DESCRIPTION
    Run by Orca automation "UFC Monitor (CASI)" id=84598be9-861f-44ac-b9b4-0190a659b705.
    Checks:
      1. Gateway health (/api/health)
      2. Epoch drift (newest open epoch_id vs prior run)
      3. code_commit consistency (learning_exposures.code_commit vs git HEAD)
      4. authority_applied=0 (UFC-scoped, plus all-paths context)
      5. UFC invalid/conflict/negative verdicts and harm breaches = 0
      6. learning_contribution_evaluator & learning_lifecycle_controller health
      7. Per-experiment dual-arm assignment units vs min_samples_per_arm
      8. UFC verdict distribution
      9. Universe scanner last run
     10. Admission time and sample thresholds
    Appends one-line summary to docs/monitoring/ufc_casi_monitor_state.md.
    ESCALATE triggers: negative|invalid|harm verdict, epoch drift on UFC path,
                       authority_applied>0, admission thresholds met.

    AU means one assignment unit (one assignment_unit_key = one cluster), never one
    exposure row: raw rows overstate units by roughly 4-20x. The admission gate is
    evaluated against the latest verdict's unique_treatment_n/unique_control_n, which
    is what authority.py actually gates on; an experiment with no verdict reports
    UNKNOWN rather than falling back to ledger counts. Verdict and authority figures
    are read path-scoped from the DB because the LCO route aggregates all paths.
.NOTES
    Constraints: live_disabled, paper_internal — never modify. Read-only analysis only.
    Requires: uv (in PATH), gateway running at 127.0.0.1:8787, app.db at $HOME\.aletheia\app.db
#>

param(
    [string]$DbPath      = "$HOME\.aletheia\app.db",
    [string]$GatewayUrl  = "http://127.0.0.1:8787",
    [string]$RepoRoot    = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)),
    [string]$StateFile   = "",   # resolved below if empty
    # Cold-start comparison basis only, and deliberately empty by default: a hardcoded
    # epoch id rots the moment the epoch rotates (it sat at 3fa19fd30d8f while the live
    # UFC epoch was 921c9f31a7d0), so every run reported drift against a stale constant.
    # Empty means the first run records the epoch and later runs compare against that.
    [string]$BaselineEpoch = "",
    [int]$MinArmAU       = 50,
    [int]$MinMarketDays  = 7
)

$ErrorActionPreference = "Continue"
$UtcNow = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mmZ")

if (-not $StateFile) {
    $StateFile = Join-Path $RepoRoot "docs\monitoring\ufc_casi_monitor_state.md"
}

# ── Helpers ──────────────────────────────────────────────────────────────────

$script:DbQueryErrors = @()

function Invoke-DbQuery([string]$Sql) {
    # SQL is passed base64-encoded: embedding it directly in a single-line Python
    # string literal breaks on any multi-line query (SyntaxError -> silent $null,
    # which previously made step 7 report "no experiments" while AU was accruing).
    $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Sql))
    $code = @"
import sqlite3, json, base64
sql = base64.b64decode('$b64').decode('utf-8')
db = sqlite3.connect(r'$DbPath')
db.row_factory = sqlite3.Row
rows = db.execute(sql).fetchall()
print(json.dumps([dict(r) for r in rows]))
db.close()
"@
    $result = uv run python -c $code 2>&1
    if ($LASTEXITCODE -ne 0) {
        $msg = ($result | Out-String).Trim()
        $script:DbQueryErrors += $msg
        Write-Warning "DB query FAILED (treat as unknown, not as zero): $msg"
        return $null
    }
    try { return ($result | ConvertFrom-Json) } catch {
        $script:DbQueryErrors += "JSON parse failed: $($result | Out-String)"
        Write-Warning "DB query returned unparseable output"
        return $null
    }
}

function Get-Api([string]$Path) {
    try {
        $r = Invoke-RestMethod -Uri "$GatewayUrl$Path" -TimeoutSec 10
        return $r
    } catch {
        return $null
    }
}

function Measure-Rows($Rows) {
    # @($null).Count is 1, so a bare @().Count silently turns a failed query into
    # "one row". Callers must be able to tell 0 from unknown, so null stays 0 here
    # and query failure is tracked separately in $script:DbQueryErrors.
    if ($null -eq $Rows) { return 0 }
    return @($Rows).Count
}

function Get-PreviousStateToken([string]$Path, [string]$Pattern) {
    # Run-over-run comparisons (AU delta, epoch drift) need the previous run's own
    # value, read back out of the state file's rows rather than a sidecar: the file is
    # append-only by design (see the note at the append below), and two CASI
    # instances share it, so anything with a read-modify-write would race.
    # Returns the LAST match in the file, i.e. the most recent run that recorded one.
    if (-not (Test-Path $Path)) { return $null }
    $previous = $null
    foreach ($line in (Get-Content -Path $Path -Encoding UTF8)) {
        $m = [regex]::Match($line, $Pattern)
        if ($m.Success) { $previous = $m.Groups[1].Value }
    }
    return $previous
}

function Get-PreviousUnitTotal([string]$Path) {
    $raw = Get-PreviousStateToken $Path 'total\s+(\d+)\s+units'
    if ($null -eq $raw) { return $null }
    return [int]$raw
}

function ConvertTo-UtcDateTime($Value) {
    # ConvertFrom-Json coerces ISO-8601 timestamps into [datetime] objects, so every
    # DB timestamp arrives here already parsed and in LOCAL time. Round-tripping such a
    # value back through [datetime]::Parse yielded an Unspecified-kind local wall time,
    # and subtracting that from [datetime]::UtcNow understated elapsed time by the UTC
    # offset (8h here) — enough to mis-measure the 7-day admission window, and to
    # escalate early rather than late on a negative-offset machine.
    if ($null -eq $Value -or "$Value" -eq "") { return $null }
    if ($Value -is [datetime]) {
        # The ledger stores UTC, so an offset-less value is UTC, not local.
        if ($Value.Kind -eq [System.DateTimeKind]::Unspecified) {
            return [datetime]::SpecifyKind($Value, [System.DateTimeKind]::Utc)
        }
        return $Value.ToUniversalTime()
    }
    try {
        $parsed = [datetime]::Parse("$Value", [System.Globalization.CultureInfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::RoundtripKind)
        if ($parsed.Kind -eq [System.DateTimeKind]::Unspecified) {
            return [datetime]::SpecifyKind($parsed, [System.DateTimeKind]::Utc)
        }
        return $parsed.ToUniversalTime()
    } catch { return $null }
}

function Format-Utc($Value) {
    $utc = ConvertTo-UtcDateTime $Value
    if ($null -eq $utc) { return if ($null -eq $Value -or "$Value" -eq "") { "unknown" } else { "$Value" } }
    return $utc.ToString("yyyy-MM-ddTHH:mm:ssZ")
}

# ── 1. Gateway health ─────────────────────────────────────────────────────────

Write-Host "`n=== UFC Check $UtcNow ===" -ForegroundColor Cyan
$health = Get-Api "/api/health"
$gatewayReachable = $null -ne $health
$gatewayStatus = if ($health) { $health.status } else { "unreachable" }
$gatewayIssues = if ($health -and $health.issues) { $health.issues -join "," } else { "" }
Write-Host "1. Gateway: $gatewayStatus$(if ($gatewayIssues) { " issues=[$gatewayIssues]" })"
# Note: gateway degraded due to llm_unavailable or trading_paused is informational, not a UFC ESCALATE.
# ESCALATE only when gateway is completely unreachable (null response).
if (-not $gatewayReachable) {
    Write-Warning "ESCALATE: Gateway unreachable"
} elseif ($gatewayStatus -eq "degraded") {
    Write-Warning "Gateway degraded (issues: $gatewayIssues) — note only, not UFC ESCALATE unless blocking AU flow"
}

# ── 2. Epoch drift ────────────────────────────────────────────────────────────

$epochRows = Invoke-DbQuery "SELECT epoch_id, epoch_kind, started_at FROM learning_policy_epochs WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
$currentEpoch = if ((Measure-Rows $epochRows) -gt 0) { [string]$epochRows[0].epoch_id } else { "unknown" }

# Drift is a CHANGE in the UFC epoch between runs, not the mere existence of an open
# one. The old test escalated whenever any open epoch affected UFC — but the live epoch
# is open and affects UFC by definition, so epoch_drift_UFC fired on every single run
# and told the owner nothing. The comparison basis is the epoch recorded by the previous
# run (falling back to -BaselineEpoch on a cold file), which is why the epoch token is
# emitted on every row below, ESCALATE rows included.
$ufcEpochRows = Invoke-DbQuery @'
SELECT epoch_id, epoch_kind, started_at
FROM learning_policy_epochs
WHERE ended_at IS NULL
  AND instr(affected_path_ids_json, 'universe_family_calibration') > 0
ORDER BY datetime(started_at) DESC
'@
$ufcEpochUnknown = $null -eq $ufcEpochRows
$openUfcEpochN = Measure-Rows $ufcEpochRows
$ufcEpoch = if ($openUfcEpochN -gt 0) { [string]$ufcEpochRows[0].epoch_id } else { "none" }

$previousUfcEpoch = Get-PreviousStateToken $StateFile 'ufc_epoch=([0-9a-f]{6,})'
if (-not $previousUfcEpoch) { $previousUfcEpoch = $BaselineEpoch }
if (-not $previousUfcEpoch) { $previousUfcEpoch = "unrecorded" }

# Only a real id -> different real id is drift. Everything else (no basis recorded yet,
# none->id, id->none) is reported but does not escalate: an epoch appearing, vanishing,
# or being seen for the first time is not the sample-pool invalidation the escalation
# exists for, and guessing otherwise would just restore the false alarm.
$ufcEpochDrift = ($ufcEpoch -ne "none") -and
                 ($previousUfcEpoch -notin @("none", "unrecorded")) -and
                 ($ufcEpoch -ne $previousUfcEpoch)
Write-Host "2. UFC open epoch: $ufcEpoch (previous run: $previousUfcEpoch, drift: $ufcEpochDrift; newest any-path: $currentEpoch)"
if ($ufcEpochUnknown) {
    Write-Warning "2. UFC epoch query FAILED — drift UNKNOWN this run (not 'no drift')"
} elseif ($ufcEpochDrift) {
    Write-Warning "ESCALATE: UFC policy epoch changed $previousUfcEpoch -> $ufcEpoch (prior verdicts no longer pool with current samples)"
} elseif ($openUfcEpochN -eq 0) {
    Write-Warning "2. No open epoch affects universe_family_calibration — exposures cannot attribute to a UFC epoch"
}
if ($openUfcEpochN -gt 1) {
    # Rotation is meant to close the prior epoch; several open at once means samples
    # are being split across cohorts that each look current.
    Write-Warning "2. $openUfcEpochN open UFC epochs at once (rotation did not close the prior one)"
    foreach ($ep in $ufcEpochRows) {
        Write-Host "   epoch=$($ep.epoch_id) kind=$($ep.epoch_kind) started=$(Format-Utc $ep.started_at)"
    }
}

# ── 3. code_commit consistency ────────────────────────────────────────────────

$gitHead = (git -C $RepoRoot rev-parse --short HEAD 2>&1)
$commitRows = Invoke-DbQuery "SELECT DISTINCT code_commit FROM learning_exposures WHERE path_id='universe_family_calibration' AND code_commit IS NOT NULL ORDER BY exposed_at DESC LIMIT 5"
$commitMismatch = $false
if ((Measure-Rows $commitRows) -gt 0) {
    $uniqueCommits = @($commitRows | Select-Object -ExpandProperty code_commit -Unique)
    # exposures store the full 40-char SHA while git HEAD here is abbreviated —
    # compare by prefix, otherwise every run reports a spurious mismatch.
    $commitMismatch = @($uniqueCommits | Where-Object {
        -not ($_.StartsWith($gitHead) -or $gitHead.StartsWith($_))
    }).Count -gt 0
    Write-Host "3. code_commit: git HEAD=$gitHead, exposure commits=$($uniqueCommits -join ','), mismatch=$commitMismatch"
} else {
    Write-Host "3. code_commit: no UFC exposures yet (git HEAD=$gitHead)"
}

# ── 4 & 5. authority_applied=0, invalid/conflict verdicts=0 ──────────────────

$lco = Get-Api "/api/analytics/learning-contribution"

# /api/analytics/learning-contribution aggregates `verdicts` and `authority_applied`
# across EVERY learning path, so a verdict or an applied treatment on some other path
# used to be reported here as a UFC finding (and a UFC-only change was invisible in a
# large global total). The escalation signals are therefore read path-scoped from the
# DB; the global numbers are kept as context only.
$verdictRows = Invoke-DbQuery @'
SELECT v.verdict AS verdict, COUNT(*) AS n
FROM learning_contribution_verdicts v
JOIN learning_hypotheses h ON h.hypothesis_id = v.hypothesis_id
WHERE h.path_id = 'universe_family_calibration'
GROUP BY v.verdict
ORDER BY n DESC
'@

$verdictsByVerdict = @{}
$verdictTotal = 0
$verdictsUnknown = $null -eq $verdictRows
if (-not $verdictsUnknown) {
    foreach ($v in $verdictRows) {
        $verdictsByVerdict[[string]$v.verdict] = [int]$v.n
        $verdictTotal += [int]$v.n
    }
}
$invalidCount  = if ($verdictsByVerdict.ContainsKey("invalid"))  { $verdictsByVerdict["invalid"] }  else { 0 }
$conflictCount = if ($verdictsByVerdict.ContainsKey("conflict")) { $verdictsByVerdict["conflict"] } else { 0 }
$negativeCount = if ($verdictsByVerdict.ContainsKey("negative")) { $verdictsByVerdict["negative"] } else { 0 }

# There is no "harm" value in the verdict column — a harm breach is recorded as
# harm_metrics_json.breached, so the old @("negative","invalid","harm") membership
# test could never fire on harm no matter how many breaches existed.
# Single-quoted here-string: the JSON path '$.breached' must reach SQLite literally,
# not go through PowerShell variable expansion.
$harmRows = Invoke-DbQuery @'
SELECT COUNT(*) AS n
FROM learning_contribution_verdicts v
JOIN learning_hypotheses h ON h.hypothesis_id = v.hypothesis_id
WHERE h.path_id = 'universe_family_calibration'
  AND json_extract(v.harm_metrics_json, '$.breached') = 1
'@
$harmUnknown = $null -eq $harmRows
$harmBreachCount = if ($harmUnknown) { 0 } else { [int]$harmRows[0].n }

$authorityRows = Invoke-DbQuery @'
SELECT SUM(CASE WHEN json_extract(x.manifest_json, '$.authority_applied') = 1 THEN 1 ELSE 0 END) AS applied
FROM learning_exposures x
WHERE x.path_id = 'universe_family_calibration'
'@
$authorityUnknown = $null -eq $authorityRows
$authorityApplied = if ($authorityUnknown) { 0 } else { [int]$authorityRows[0].applied }
$authorityAppliedGlobal = if ($lco) { [int]($lco.authority_applied.applied_n) } else { 0 }

Write-Host "4. authority_applied(UFC)=$(if ($authorityUnknown) { 'UNKNOWN' } else { $authorityApplied }) (all paths=$authorityAppliedGlobal)"
Write-Host "5. UFC verdicts: invalid=$invalidCount conflict=$conflictCount negative=$negativeCount harm_breached=$harmBreachCount (UFC total=$(if ($verdictsUnknown) { 'UNKNOWN' } else { $verdictTotal }))"

if ($authorityApplied -gt 0 -or $authorityAppliedGlobal -gt 0) {
    Write-Warning "ESCALATE: authority_applied UFC=$authorityApplied all_paths=$authorityAppliedGlobal > 0"
}
if ($invalidCount -gt 0 -or $conflictCount -gt 0) {
    Write-Warning "ESCALATE: invalid=$invalidCount conflict=$conflictCount"
}

# ── 6. LCE & LLC health ───────────────────────────────────────────────────────

$lceStatus = "unknown"
$llcStatus = "unknown"
if ($lco) {
    $lceStatus = $lco.job_health.learning_contribution_evaluator.status
    $llcStatus = $lco.job_health.learning_lifecycle_controller.status
}
Write-Host "6. LCE=$lceStatus LLC=$llcStatus"

$jobUnhealthy = ($lceStatus -notin @("healthy","unknown")) -or ($llcStatus -notin @("healthy","unknown"))
if ($jobUnhealthy) {
    Write-Warning "Job health degraded: LCE=$lceStatus LLC=$llcStatus"
}

# ── 7. Per-experiment dual-arm AU vs min_arm ─────────────────────────────────

# An assignment unit is one cluster (assignment_unit_key), NOT one exposure row: a
# single scanner run writes many rows per unit, so raw COUNT(*) overstated AU by
# roughly 4-20x and the admission gate looked far closer than it was. min_samples_per_arm
# is gated in authority.py against the verdict's unique_treatment_n/unique_control_n,
# which count settled, deduplicated units — so that verdict pair is the gate metric and
# is read here directly. The ledger's DISTINCT unit counts are reported alongside as an
# upper bound on what is accruing but must never drive the admission escalation.
$expRows = Invoke-DbQuery @"
SELECT e.experiment_id, e.stage, e.min_samples_per_arm, e.min_market_days,
       e.started_at,
       (SELECT COUNT(DISTINCT x.assignment_unit_key) FROM learning_exposures x
         WHERE x.experiment_id=e.experiment_id AND x.assignment='treatment') AS treatment_units,
       (SELECT COUNT(DISTINCT x.assignment_unit_key) FROM learning_exposures x
         WHERE x.experiment_id=e.experiment_id AND x.assignment='control') AS control_units,
       (SELECT COUNT(*) FROM learning_exposures x
         WHERE x.experiment_id=e.experiment_id) AS raw_rows,
       v.verdict AS latest_verdict, v.reason AS latest_reason,
       v.unique_treatment_n AS verdict_treatment_n,
       v.unique_control_n AS verdict_control_n
FROM learning_experiments e
JOIN learning_hypotheses h ON h.hypothesis_id=e.hypothesis_id
LEFT JOIN learning_contribution_verdicts v ON v.verdict_id=(
    SELECT v2.verdict_id FROM learning_contribution_verdicts v2
     WHERE v2.experiment_id=e.experiment_id
     ORDER BY datetime(v2.window_end) DESC, datetime(v2.created_at) DESC, v2.verdict_id DESC
     LIMIT 1)
WHERE h.path_id='universe_family_calibration' AND e.ended_at IS NULL
ORDER BY datetime(e.created_at) DESC
"@

$experimentsUnknown = $null -eq $expRows
$openExperimentN = Measure-Rows $expRows
$totalUnits = 0        # assignment units across both arms, all open UFC experiments
$totalRawRows = 0      # raw exposure rows — context only, not AU
$minArmActual = $null  # gate metric: min verdict-measured arm; null = not computed
$minUnitArm = $null    # ledger upper bound: min DISTINCT-unit arm
$armDetails = @()
if ($openExperimentN -gt 0) {
    foreach ($exp in $expRows) {
        $tU = [int]$exp.treatment_units
        $cU = [int]$exp.control_units
        $unitArm = [Math]::Min($tU, $cU)
        $minUnitArm = if ($null -eq $minUnitArm) { $unitArm } else { [Math]::Min($minUnitArm, $unitArm) }
        $totalUnits += $tU + $cU
        $totalRawRows += [int]$exp.raw_rows
        $minSPA = [int]$exp.min_samples_per_arm
        $minMD  = [int]$exp.min_market_days

        # No verdict yet means the gate metric does not exist for this experiment.
        # It stays UNKNOWN rather than falling back to the ledger counts, because
        # falling back is exactly what made the gate look satisfied when it was not.
        $hasVerdictArm = ($null -ne $exp.latest_verdict) -and ($null -ne $exp.verdict_treatment_n)
        $gateArm = $null
        if ($hasVerdictArm) {
            $gateArm = [Math]::Min([int]$exp.verdict_treatment_n, [int]$exp.verdict_control_n)
            $minArmActual = if ($null -eq $minArmActual) { $gateArm } else { [Math]::Min($minArmActual, $gateArm) }
        }

        # Elapsed observation days is the other half of the admission gate, so report it
        # next to the arm count instead of only the requirement it is measured against.
        $startUtc = ConvertTo-UtcDateTime $exp.started_at
        $daysElapsed = if ($null -eq $startUtc) { $null } else { ([datetime]::UtcNow - $startUtc).TotalDays }
        $daysStr = if ($null -eq $daysElapsed) { "unknown" } else { [math]::Round($daysElapsed, 1) }

        $gateArmStr = if ($hasVerdictArm) { "$gateArm" } else { "no-verdict" }
        $armDetails += ("exp=$($exp.experiment_id) stage=$($exp.stage) units T=$tU C=$cU " +
                        "gate_arm=$gateArmStr/$minSPA rows=$($exp.raw_rows) " +
                        "obs_days=$daysStr/$minMD started=$(Format-Utc $exp.started_at) verdict=$(if ($exp.latest_verdict) { $exp.latest_verdict } else { 'none' })")
        Write-Host "7. $($armDetails[-1])"

        if ($null -eq $daysElapsed) {
            # Never silently treat an unreadable start as day 0: that suppresses the
            # window half of the gate without saying so.
            Write-Warning "7. exp=$($exp.experiment_id) has no readable started_at — observation window UNKNOWN, admission check incomplete"
        } elseif ($hasVerdictArm -and $daysElapsed -ge $minMD -and $gateArm -ge $minSPA) {
            Write-Warning "ESCALATE: Admission thresholds met for exp=$($exp.experiment_id) — days=$daysStr gate_arm=$gateArm (verdict-measured) (owner must decide admission)"
        }
    }
} elseif ($experimentsUnknown) {
    # Query error — do NOT report this as "0 AU / no experiments".
    Write-Warning "7. UFC experiment query FAILED — AU/min_arm UNKNOWN this run (not zero)"
} else {
    Write-Host "7. No open UFC experiments (AU accumulation not started)"
}

# ── 8. LCO verdict distribution ───────────────────────────────────────────────

$verdictLine =
    if ($verdictsUnknown) { "UNKNOWN (query failed)" }
    elseif ($verdictTotal -eq 0) { "0 verdicts" }
    else {
        (@($verdictsByVerdict.GetEnumerator() | Sort-Object Value -Descending | ForEach-Object { "$($_.Key)=$($_.Value)" }) +
         @(if ($harmBreachCount -gt 0) { "harm_breached=$harmBreachCount" })) -join " "
    }
Write-Host "8. UFC verdicts: $verdictLine"

$negativeHarmVerdicts = @(
    @("negative","invalid") | Where-Object { $verdictsByVerdict.ContainsKey($_) -and $verdictsByVerdict[$_] -gt 0 }
) + @(if ($harmBreachCount -gt 0) { "harm_breached" })
if ((Measure-Rows $negativeHarmVerdicts) -gt 0) {
    Write-Warning "ESCALATE: Negative/invalid/harm verdicts detected: $($negativeHarmVerdicts -join ',')"
}

# ── 9. Scanner last run ────────────────────────────────────────────────────────

$scannerRows = Invoke-DbQuery "SELECT id, started_at, completed_at, status, assets_scanned, assets_passed FROM universe_scanner_runs ORDER BY started_at DESC LIMIT 1"
$scannerUnknown = $null -eq $scannerRows
if ((Measure-Rows $scannerRows) -gt 0) {
    $sr = $scannerRows[0]
    # Format-Utc, not the raw value: ConvertFrom-Json hands back a local-time [datetime]
    # whose rendering contains a space, which the notes column's Split(' ') then truncated.
    $scannerLast = Format-Utc $sr.started_at
    $scannerLine = "last=$scannerLast status=$($sr.status) scanned=$($sr.assets_scanned) passed=$($sr.assets_passed)"
    Write-Host "9. Scanner: $scannerLine"
} else {
    $scannerLast = if ($scannerUnknown) { "unknown" } else { "never" }
    $scannerLine = if ($scannerUnknown) { "UNKNOWN (query failed)" } else { "never run" }
    Write-Host "9. Scanner: $scannerLine"
}

# ── 10. Admission time and sample thresholds (summary) ───────────────────────

Write-Host "10. Admission thresholds: obs_window>=${MinMarketDays}d, min_arm>=${MinArmAU} AU per arm (escalate when both met, do not act autonomously)"

# ── Build summary line ────────────────────────────────────────────────────────

$escalateFlags = @()
if (-not $gatewayReachable)                        { $escalateFlags += "gateway_unreachable" }
if ($ufcEpochDrift)                                { $escalateFlags += "epoch_drift_UFC=$previousUfcEpoch->$ufcEpoch" }
if ($authorityApplied -gt 0)                       { $escalateFlags += "authority_applied=$authorityApplied" }
if ($authorityAppliedGlobal -gt 0)                 { $escalateFlags += "authority_applied_all_paths=$authorityAppliedGlobal" }
if ($invalidCount -gt 0)                           { $escalateFlags += "invalid=$invalidCount" }
if ($conflictCount -gt 0)                          { $escalateFlags += "conflict=$conflictCount" }
if ((Measure-Rows $negativeHarmVerdicts) -gt 0)    { $escalateFlags += "negative_verdict=$($negativeHarmVerdicts -join ',')" }
if ((Measure-Rows $script:DbQueryErrors) -gt 0)    { $escalateFlags += "db_query_failed=$(Measure-Rows $script:DbQueryErrors)" }

$status = if ((Measure-Rows $escalateFlags) -gt 0) { "ESCALATE" } else { "OK" }

# AU column: a real delta against the previous run's absolute total, which is read
# back out of this file (see Get-PreviousUnitTotal). "units" is stated explicitly
# because rows written before this change counted raw exposure rows instead, and the
# two series are not comparable — raw rows overstate units by roughly 4-20x.
$previousUnits = Get-PreviousUnitTotal $StateFile
$auDelta =
    if ($experimentsUnknown) { "UNKNOWN (query failed)" }
    elseif ($null -eq $previousUnits) { "total $totalUnits units (first run on unit basis; rows=$totalRawRows)" }
    else {
        $delta = $totalUnits - $previousUnits
        "$(if ($delta -ge 0) { '+' })$delta AU (total $totalUnits units; rows=$totalRawRows)"
    }

# Four distinct states, previously collapsed: a failed query, no open experiment, an
# open experiment with no verdict to measure the gate with, and a real measured value.
# Reporting "N/A (no exp)" for a genuine zero-AU arm hid the arm that had no samples.
$minArmStr =
    if ($experimentsUnknown)          { "UNKNOWN (query failed)" }
    elseif ($openExperimentN -eq 0)   { "N/A (no open exp)" }
    elseif ($null -eq $minArmActual)  { "UNKNOWN (no verdict yet; ledger units min=$minUnitArm)" }
    else                              { "$minArmActual AU (verdict-measured, need $MinArmAU; ledger units min=$minUnitArm)" }
$verdictDeltaStr = $verdictLine

# The context suffix is emitted on EVERY row, ESCALATE included. It used to be the
# else-branch of the escalate flags, which dropped the epoch token exactly on the runs
# that recorded a problem — and since drift is now measured against the previous row's
# ufc_epoch=, a missing token would have made the next run fall back to the baseline and
# report drift that had already been reported.
$context = "gw=$gatewayStatus$(if ($gatewayIssues) { "[$gatewayIssues]" }) LCE=$lceStatus LLC=$llcStatus " +
           "open_exp=$openExperimentN scanner=$scannerLast commit=$gitHead " +
           "ufc_epoch=$ufcEpoch"
$notes = if ((Measure-Rows $escalateFlags) -gt 0) {
    "ESCALATE: $($escalateFlags -join '; ') — $context"
} else {
    $context
}

$summaryLine = "| $UtcNow | $status | $auDelta | $verdictDeltaStr | $minArmStr | $notes |"

# ── Append to state file ──────────────────────────────────────────────────────

# Deliberately a plain EOF append, NOT a splice into the Monitor Log table.
# A mid-file insert requires reading the whole file and rewriting it, and at least
# two CASI instances run this same routine against this one untracked file (see the
# 14:25Z addendum). A read-modify-write would clobber a note written concurrently by
# the other instance; an append cannot. Rows therefore land below the prose notes and
# are moved into the table by hand each run. Restructuring the file so the table is
# last (making appends land correctly) is an owner decision — see the tooling items.
Add-Content -Path $StateFile -Value $summaryLine -Encoding UTF8
Write-Host "`nAppended to $StateFile"
Write-Host $summaryLine

if ((Measure-Rows $escalateFlags) -gt 0) {
    Write-Host "`n*** ESCALATE — stop and investigate before next action ***" -ForegroundColor Red
    Write-Host "Flags: $($escalateFlags -join ', ')" -ForegroundColor Red
} else {
    Write-Host "`n--- Status: OK ---" -ForegroundColor Green
}
