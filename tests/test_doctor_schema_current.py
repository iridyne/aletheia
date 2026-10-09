"""doctor must surface a database that is behind the code's migrations.

A stale schema makes ordinary read commands fail on missing columns (observed:
`inspect calibration` raising `no such column: min_cost_covering_edge_pct` on a
v73 database while the code expected v89), so the condition has to be visible
as a warning with the repair command rather than as a raw traceback later.
"""

from aletheia.diagnostics.doctor import Report, _probe_infra_schema_current


def _probe(monkeypatch, status: dict) -> Report:
    import aletheia.storage.migrations as migrations

    monkeypatch.setattr(migrations, "migration_status", lambda: status)
    report = Report()
    _probe_infra_schema_current(report)
    return report


def test_pending_migrations_warn_and_name_the_repair_command(monkeypatch):
    report = _probe(
        monkeypatch,
        {
            "schema_version": "aletheia-sqlite-v89",
            "applied": ["20260907_0072_uta_order_identity_v73"],
            "pending": ["20260907_0073_runtime_control_retirement_v74", "later_one_v89"],
            "current": False,
        },
    )
    (check,) = report.checks
    assert check.name == "infra.schema"
    assert check.severity == "warn"
    assert "2 pending migration(s)" in check.detail
    assert "aletheia-sqlite-v89" in check.detail
    assert "20260907_0073_runtime_control_retirement_v74" in check.detail
    assert "aletheia data migrate" in check.fix_hint


def test_current_schema_reports_ok(monkeypatch):
    report = _probe(
        monkeypatch,
        {
            "schema_version": "aletheia-sqlite-v89",
            "applied": ["everything"],
            "pending": [],
            "current": True,
        },
    )
    (check,) = report.checks
    assert check.severity == "ok"
    assert "aletheia-sqlite-v89" in check.detail


def test_unreadable_migration_state_is_a_warn(monkeypatch):
    import aletheia.storage.migrations as migrations

    def explode() -> dict:
        raise RuntimeError("ledger unreadable")

    monkeypatch.setattr(migrations, "migration_status", explode)
    report = Report()
    _probe_infra_schema_current(report)
    (check,) = report.checks
    assert check.severity == "warn"
    assert "ledger unreadable" in check.detail
