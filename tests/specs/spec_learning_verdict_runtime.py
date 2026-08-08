"""Executable contract for the read-only universe contribution verdict runtime."""

from __future__ import annotations

from tests.specs.spec_meta import SpecClaim, spec_claim


@spec_claim(
    SpecClaim(
        "LE-V01", "learning_evolution", "§7 contribution decision", "L2b",
        "The only runtime verdict path evaluates full-arm filter candidate outcomes by exact key, "
        "uses selected-universe outcomes only as a non-duplicating mirror or fallback, and counts "
        "complete assignment clusters rather than survivor candidates.",
        status="implemented",
    ),
    SpecClaim(
        "LE-V02", "learning_evolution", "§7-8 measurement-only verdict", "L2c",
        "A deterministic, idempotent verdict records reproducibility and harm evidence without changing "
        "Learning authority, experiment lifecycle, or any trading behavior.",
        status="implemented",
    ),
    SpecClaim(
        "LE-V03", "learning_evolution", "§7 actual intervention", "L2c",
        "AT-01 carries exposure authority and its refusal reason into each cohort sample; only "
        "authority_applied=true units enter the causal treatment count and effect. Baseline "
        "treatment rows remain compliance evidence, and insufficient actual intervention cannot "
        "produce or promote a causal verdict.",
        status="implemented",
    ),
    SpecClaim(
        "LE-B30", "learning_evolution", "§7 market-day continuity", "L2b",
        "A measurement-supported cohort qualifies time only through consecutive UTC days that "
        "each contain a complete eligible exposure unit; a missing day fails "
        "closed with owner-visible coverage evidence and cannot be bridged by a later exposure.",
        status="implemented",
    ),
)
def test_read_only_verdict_runtime_surface_exists():
    from dataclasses import fields

    from aletheia.learning.cohorts import CohortSample
    from aletheia.learning.contribution_runner import evaluate_experiment, inspect_experiment
    from aletheia.learning.registry import get_learning_path
    from aletheia.learning.verdicts import METRIC_VERSION

    assert callable(evaluate_experiment)
    assert callable(inspect_experiment)
    assert "authority_applied" in {field.name for field in fields(CohortSample)}
    assert "authority_reason" in {field.name for field in fields(CohortSample)}
    assert "assignment_unit_key" in {field.name for field in fields(CohortSample)}
    assert METRIC_VERSION == "universe_family_calibration_cluster_allocation_utility_v4"
    assert (
        get_learning_path("universe_family_calibration").behavior_contract_version
        == "universe-family-calibration/v5"
    )


# ---------------------------------------------------------------------------
# LE-V04 — /analytics/learning-contribution returns persisted verdict counts
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "LE-V04", "learning_evolution", "§7 learning-contribution route verdict counts", "L2b",
        "GET /analytics/learning-contribution must return persisted verdict counts "
        "grouped by the `verdict` column (not `classification`). Fixes #322: a column "
        "name mismatch caused a silent SQLite error that converted all 5,580 verdict rows "
        "into a false total=0.",
        status="implemented",
    ),
)
def test_learning_contribution_route_returns_verdict_counts(monkeypatch):
    """LE-V04: persisted verdict rows are returned correctly by stored verdict value.

    Calls the route function directly (bypassing TestClient thread isolation) with
    a mocked DB that has pre-populated verdict rows.
    """
    import sqlite3
    from unittest.mock import patch
    import aletheia.storage.sqlite as sqlite_mod

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE learning_contribution_verdicts ("
        "  verdict_id TEXT PRIMARY KEY, verdict TEXT, created_at TEXT, "
        "  experiment_id TEXT, hypothesis_id TEXT)"
    )
    conn.execute(
        "INSERT INTO learning_contribution_verdicts VALUES "
        "('v1','insufficient_data','2026-01-01T01:00:00Z','exp1','hyp1'),"
        "('v2','invalid','2026-01-01T02:00:00Z','exp2','hyp2'),"
        "('v3','insufficient_data','2026-01-01T03:00:00Z','exp3','hyp3')"
    )
    conn.commit()
    # Create stub tables for the other queries in the route
    for tbl in ("learning_exposures", "learning_experiments", "learning_lifecycle_events"):
        cols = {
            "learning_exposures": "exposure_id TEXT, path_id TEXT, experiment_id TEXT, "
                                   "exposed_at TEXT, assignment TEXT, manifest_json TEXT",
            "learning_experiments": "experiment_id TEXT, stage TEXT, status TEXT",
            "learning_lifecycle_events": "event_id TEXT",
        }
        conn.execute(f"CREATE TABLE {tbl} ({cols[tbl]})")
    conn.commit()

    with patch.object(sqlite_mod, "get_db", return_value=conn):
        from aletheia.server.routes.analytics import get_learning_contribution
        result = get_learning_contribution()

    verdicts = result.get("verdicts", {})
    assert verdicts.get("total") == 3, (
        f"Expected total=3 verdicts, got {verdicts.get('total')!r} — "
        "the route may still be using the wrong column name"
    )
    by_verdict = {row["verdict"]: row["n"] for row in verdicts.get("by_verdict", [])}
    assert by_verdict.get("insufficient_data") == 2, (
        f"Expected insufficient_data:2, got {by_verdict}"
    )
    assert by_verdict.get("invalid") == 1, (
        f"Expected invalid:1, got {by_verdict}"
    )
    assert "query_error" not in verdicts, (
        f"Unexpected query_error in verdicts: {verdicts.get('query_error')}"
    )


# ---------------------------------------------------------------------------
# LE-V05 — DB read failure produces stable query_error, not silent total=0
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "LE-V05", "learning_evolution", "§7 learning-contribution route error honesty", "L2b",
        "A SQLite read failure on the verdicts query must surface as a stable query_error "
        "code, must NOT produce total=0 and must not return raw exception text.",
        status="implemented",
    ),
)
def test_learning_contribution_route_error_is_not_silent_zero():
    """LE-V05: a DB error produces query_error=read_failed and total=None.

    Calls the route function directly with a DB whose verdicts table is intentionally
    absent to simulate a schema/read failure.
    """
    import sqlite3
    from unittest.mock import patch
    import aletheia.storage.sqlite as sqlite_mod

    # A DB with no learning_contribution_verdicts table → OperationalError on query.
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    for tbl in ("learning_exposures", "learning_experiments", "learning_lifecycle_events"):
        cols = {
            "learning_exposures": "exposure_id TEXT, path_id TEXT, experiment_id TEXT, "
                                   "exposed_at TEXT, assignment TEXT, manifest_json TEXT",
            "learning_experiments": "experiment_id TEXT, stage TEXT, status TEXT",
            "learning_lifecycle_events": "event_id TEXT",
        }
        conn.execute(f"CREATE TABLE {tbl} ({cols[tbl]})")
    conn.commit()
    # NOTE: learning_contribution_verdicts is intentionally NOT created

    with patch.object(sqlite_mod, "get_db", return_value=conn):
        from aletheia.server.routes.analytics import get_learning_contribution
        result = get_learning_contribution()

    verdicts = result.get("verdicts", {})
    assert verdicts.get("total") is None, (
        f"Expected total=None when verdicts query fails, got total={verdicts.get('total')!r} — "
        "a DB error must not produce total=0 (indistinguishable from 'no verdicts exist')"
    )
    assert "query_error" in verdicts, (
        f"Expected 'query_error' key when DB read fails — silent false-zero must be gone. "
        f"Got verdicts={verdicts}"
    )
    assert verdicts["query_error"] == "read_failed"
    assert "no such table" not in str(result)
