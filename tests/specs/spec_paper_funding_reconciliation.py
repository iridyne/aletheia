"""Dry-run and apply contracts for paper funding reconciliation."""

from __future__ import annotations

import hashlib
import json
import re

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim
from tests.specs.spec_pipeline_local_funding import (
    _insert_entry_lineage,
    _iso,
    _iso_ms,
    _position,
    _setup_db,
    _snapshot_at,
)


def _seed_recoverable_position() -> str:
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    raw = _position()
    _insert_entry_lineage(raw)
    position = Position(
        id=raw["id"],
        proposal_id=raw["proposal_id"],
        order_id=raw["order_id"],
        symbol=raw["symbol"],
        market_type=raw["market_type"],
        direction=raw["direction"],
        quantity=raw["quantity"],
        entry_price=raw["entry_price"],
        current_price=101.0,
        unrealized_pnl=0.0,
        realized_pnl=None,
        execution_mode=raw["execution_mode"],
        status="closed",
        close_reason="max_hold",
        close_category="strategy",
        entry_price_source="local_fill",
        entry_fill_verified=True,
        exit_price_source="local_fill",
        exit_fill_verified=True,
        outcome_eligible=False,
        outcome_exclusion_reason="incomplete_funding_economics",
        entry_fee=0.1,
        exit_fee=0.1,
        funding_cost=None,
        funding_source="unavailable",
        reconciliation_evidence=json.dumps({
            "source": "local_paper_fill",
            "funding": {"status": "incomplete"},
        }),
        opened_at=raw["opened_at"],
        closed_at=_iso(9),
    )
    repo.insert_position(position)
    repo.insert_position_close_event({
        "id": f"close-event-{position.id}",
        "position_id": position.id,
        "symbol": position.symbol,
        "direction": position.direction,
        "execution_mode": position.execution_mode,
        "close_reason": position.close_reason,
        "exit_price": position.current_price,
        "closed_qty": position.quantity,
        "realized_pnl": None,
        "entry_fee": position.entry_fee,
        "exit_fee": position.exit_fee,
        "is_partial": 0,
        "closed_at": position.closed_at,
    })

    history = [
        (_iso_ms(0, millisecond=1), 0.0002),
        (_iso_ms(4, millisecond=4), 0.0009),
        (_iso_ms(8, millisecond=2), -0.0004),
        (_iso_ms(12, millisecond=1), 0.0003),
    ]
    for index, (settled_at, rate) in enumerate(history):
        _snapshot_at(rate=rate, target=settled_at, observed=settled_at)
        if index + 1 < len(history):
            _snapshot_at(
                rate=rate,
                target=history[index + 1][0],
                observed=settled_at,
            )
    return position.id


@spec_claim(
    SpecClaim(
        "PFA-REC-01",
        "paper_funding_reconciliation",
        "deterministic read-only dry run",
        "L2c",
        "Paper funding reconciliation is read-only by default and produces a "
        "deterministic fingerprint for unchanged durable facts.",
        status="implemented",
    )
)
def test_paper_funding_reconciliation_dry_run_is_deterministic_and_read_only(
    tmp_path, monkeypatch,
):
    conn = _setup_db(tmp_path, monkeypatch)
    position_id = _seed_recoverable_position()
    from aletheia.maintenance.paper_funding_reconciliation import (
        inspect_paper_funding_reconciliation,
    )

    first = inspect_paper_funding_reconciliation(position_ids=[position_id])
    second = inspect_paper_funding_reconciliation(position_ids=[position_id])

    assert first == second
    assert first["summary"]["funding_identity_mismatch"] == 1
    assert first["summary"]["recoverable_null_pnl"] == 1
    assert first["rows"][0]["repair_mode"] == "economics_materialization"
    assert first["rows"][0]["current"]["funding_accounting_version"] is None
    assert (
        first["rows"][0]["proposed"]["funding_accounting_version"]
        == "local-paper-funding-v2"
    )
    assert first["rows"][0]["proposed"]["realized_pnl"] == pytest.approx(1.7)
    assert first["report_fingerprint"].startswith("sha256:")
    assert conn.execute(
        "SELECT realized_pnl FROM positions WHERE id=?", (position_id,),
    ).fetchone()["realized_pnl"] is None
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM paper_account_events WHERE position_id=?",
        (position_id,),
    ).fetchone()["n"] == 0


def test_invalid_exit_price_is_out_of_scope_for_funding_reconciliation(
    tmp_path, monkeypatch,
):
    conn = _setup_db(tmp_path, monkeypatch)
    position_id = _seed_recoverable_position()
    conn.execute(
        "UPDATE positions SET outcome_exclusion_reason=? WHERE id=?",
        ("invalid_exit_price_evidence", position_id),
    )
    conn.commit()
    from aletheia.maintenance.paper_funding_reconciliation import (
        inspect_paper_funding_reconciliation,
    )

    report = inspect_paper_funding_reconciliation()

    assert report["rows"] == []
    assert all(value == 0 for value in report["summary"].values())


@spec_claim(
    SpecClaim(
        "PFA-REC-02",
        "paper_funding_reconciliation",
        "fingerprint-guarded apply",
        "L2c",
        "Apply requires explicit Position scope and an exact fresh report "
        "fingerprint, then delegates to atomic economics materialization.",
        status="implemented",
    )
)
def test_paper_funding_reconciliation_apply_is_guarded_and_idempotent(
    tmp_path, monkeypatch,
):
    conn = _setup_db(tmp_path, monkeypatch)
    position_id = _seed_recoverable_position()
    from aletheia.storage import repositories as repo
    from aletheia.maintenance.paper_funding_reconciliation import (
        apply_paper_funding_reconciliation,
        inspect_paper_funding_reconciliation,
    )

    report = inspect_paper_funding_reconciliation(position_ids=[position_id])
    with pytest.raises(ValueError, match="stale"):
        apply_paper_funding_reconciliation(
            position_ids=[position_id], expected_fingerprint="sha256:stale",
        )
    result = apply_paper_funding_reconciliation(
        position_ids=[position_id],
        expected_fingerprint=report["report_fingerprint"],
    )
    refreshed = inspect_paper_funding_reconciliation(position_ids=[position_id])
    repeated = apply_paper_funding_reconciliation(
        position_ids=[position_id],
        expected_fingerprint=refreshed["report_fingerprint"],
    )

    assert result["results"][0]["status"] == "settled"
    assert repeated["results"][0]["status"] == "already_current"
    assert (
        refreshed["rows"][0]["current"]["funding_accounting_version"]
        == "local-paper-funding-v2"
    )
    assert conn.execute(
        "SELECT realized_pnl FROM positions WHERE id=?", (position_id,),
    ).fetchone()["realized_pnl"] == pytest.approx(1.7)
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM paper_account_events "
        "WHERE position_id=? AND action='position_closed'",
        (position_id,),
    ).fetchone()["n"] == 1
    assert len(repo.list_outcome_hook_jobs(position_id=position_id)) <= 1


def test_reconciliation_preserves_validated_close_evidence_after_ledger_drift(
    tmp_path, monkeypatch,
):
    conn = _setup_db(tmp_path, monkeypatch)
    position_id = _seed_recoverable_position()
    from aletheia.maintenance.paper_funding_reconciliation import (
        apply_paper_funding_reconciliation,
        inspect_paper_funding_reconciliation,
    )
    from aletheia.storage import repositories as repo

    initial = inspect_paper_funding_reconciliation(position_ids=[position_id])
    apply_paper_funding_reconciliation(
        position_ids=[position_id],
        expected_fingerprint=initial["report_fingerprint"],
    )
    before = repo._select_one("positions", position_id)
    assert before is not None
    stored_funding = json.loads(before["reconciliation_evidence"])["funding"]

    # Later historical ingestion must not redefine the immutable evidence used
    # when this Position was closed and economically materialized.
    _snapshot_at(
        rate=0.1234,
        target=_iso_ms(4, millisecond=4),
        observed=_iso_ms(4, millisecond=4),
    )

    report = inspect_paper_funding_reconciliation(position_ids=[position_id])
    row = report["rows"][0]

    assert row["categories"] == []
    assert row["repair_mode"] == "already_current"
    assert row["proposed"]["funding_evidence"] == stored_funding
    assert row["proposed"]["funding_cost"] == pytest.approx(before["funding_cost"])
    assert row["proposed"]["realized_pnl"] == pytest.approx(before["realized_pnl"])
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM paper_account_events WHERE position_id=?",
        (position_id,),
    ).fetchone()["n"] == 1


@spec_claim(
    SpecClaim(
        "PFA-REC-04",
        "paper_funding_reconciliation",
        "identity-only historical convergence",
        "L2c",
        "A completed local-paper close with missing root funding identity is "
        "reported explicitly and repaired without rewriting its close-time funding "
        "evidence or any economic, ledger, eligibility, or hook projection.",
        status="implemented",
    )
)
def test_paper_funding_identity_repair_preserves_close_time_evidence_and_projections(
    tmp_path, monkeypatch,
):
    conn = _setup_db(tmp_path, monkeypatch)
    position_id = _seed_recoverable_position()
    from aletheia.diagnostics.doctor import Report, _probe_paper_funding_accounting
    from aletheia.maintenance.paper_funding_reconciliation import (
        apply_paper_funding_reconciliation,
        inspect_paper_funding_reconciliation,
    )
    from aletheia.storage import repositories as repo

    initial = inspect_paper_funding_reconciliation(position_ids=[position_id])
    apply_paper_funding_reconciliation(
        position_ids=[position_id],
        expected_fingerprint=initial["report_fingerprint"],
    )
    stored = repo._select_one("positions", position_id)
    assert stored is not None
    evidence_root = json.loads(stored["reconciliation_evidence"])
    evidence_root["funding"]["seed_close_anchor"] = {
        "attempts": [],
        "inserted": False,
        "skipped": "close_anchor_already_covered",
    }
    evidence_root.pop("funding_accounting_version")
    evidence_root.pop("funding_economics_fingerprint")
    repo.update_position(
        position_id,
        reconciliation_evidence=json.dumps(
            evidence_root, sort_keys=True, separators=(",", ":"),
        ),
    )

    before_position = repo._select_one("positions", position_id)
    assert before_position is not None
    before_root = json.loads(before_position["reconciliation_evidence"])
    before_funding_bytes = json.dumps(
        before_root["funding"], sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    expected_identity = (
        "sha256:" + hashlib.sha256(before_funding_bytes).hexdigest()
    )
    economic_fields = (
        "funding_cost",
        "funding_source",
        "realized_pnl",
        "outcome_eligible",
        "outcome_exclusion_reason",
    )
    before_economics = {
        field: before_position[field] for field in economic_fields
    }
    before_close_events = [
        dict(row) for row in conn.execute(
            "SELECT * FROM position_close_events WHERE position_id=? ORDER BY rowid",
            (position_id,),
        ).fetchall()
    ]
    before_cash_events = [
        dict(row) for row in conn.execute(
            "SELECT * FROM paper_account_events WHERE position_id=? ORDER BY rowid",
            (position_id,),
        ).fetchall()
    ]
    before_hooks = [
        dict(row) for row in conn.execute(
            "SELECT * FROM outcome_hook_jobs WHERE position_id=? ORDER BY rowid",
            (position_id,),
        ).fetchall()
    ]

    report = inspect_paper_funding_reconciliation(position_ids=[position_id])
    row = report["rows"][0]
    assert row["categories"] == ["funding_identity_mismatch"]
    assert row["repair_mode"] == "identity_only"
    assert row["current"]["funding_economics_fingerprint"] is None
    assert (
        row["current"]["nested_funding_economics_fingerprint"]
        == expected_identity
    )
    assert row["proposed"]["funding_economics_fingerprint"] == expected_identity
    assert row["proposed"]["funding_evidence"] == before_root["funding"]
    assert (
        row["proposed"]["recomputed_funding_economics_fingerprint"]
        != expected_identity
    )
    doctor = Report()
    _probe_paper_funding_accounting(doctor)
    check = next(
        item for item in doctor.checks if item.name == "accounting.paper_funding"
    )
    assert check.severity == "warn"
    assert "identity_mismatch=1" in check.detail

    result = apply_paper_funding_reconciliation(
        position_ids=[position_id],
        expected_fingerprint=report["report_fingerprint"],
    )
    after_position = repo._select_one("positions", position_id)
    assert after_position is not None
    after_root = json.loads(after_position["reconciliation_evidence"])
    after_funding_bytes = json.dumps(
        after_root["funding"], sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")

    assert result["results"][0]["status"] == "identity_repaired"
    assert after_funding_bytes == before_funding_bytes
    assert after_root["funding_accounting_version"] == "local-paper-funding-v2"
    assert after_root["funding_economics_fingerprint"] == expected_identity
    assert {
        field: after_position[field] for field in economic_fields
    } == before_economics
    assert [
        dict(item) for item in conn.execute(
            "SELECT * FROM position_close_events WHERE position_id=? ORDER BY rowid",
            (position_id,),
        ).fetchall()
    ] == before_close_events
    assert [
        dict(item) for item in conn.execute(
            "SELECT * FROM paper_account_events WHERE position_id=? ORDER BY rowid",
            (position_id,),
        ).fetchall()
    ] == before_cash_events
    assert [
        dict(item) for item in conn.execute(
            "SELECT * FROM outcome_hook_jobs WHERE position_id=? ORDER BY rowid",
            (position_id,),
        ).fetchall()
    ] == before_hooks

    refreshed = inspect_paper_funding_reconciliation(position_ids=[position_id])
    repeated = apply_paper_funding_reconciliation(
        position_ids=[position_id],
        expected_fingerprint=refreshed["report_fingerprint"],
    )
    assert refreshed["rows"][0]["categories"] == []
    assert refreshed["rows"][0]["repair_mode"] == "already_current"
    assert repeated["results"][0]["status"] == "already_current"
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM audit_events "
        "WHERE entity_id=? AND event_type='paper_position_funding_identity_repaired'",
        (position_id,),
    ).fetchone()["n"] == 1

    after_root.pop("funding_accounting_version")
    after_root.pop("funding_economics_fingerprint")
    repo.update_position(
        position_id,
        funding_cost=float(after_position["funding_cost"]) + 0.01,
        reconciliation_evidence=json.dumps(
            after_root, sort_keys=True, separators=(",", ":"),
        ),
    )
    divergent = inspect_paper_funding_reconciliation(position_ids=[position_id])
    divergent_row = divergent["rows"][0]
    assert "funding_identity_mismatch" in divergent_row["categories"]
    assert "duplicate_settlement_numeric_mismatch" in divergent_row["categories"]
    assert divergent_row["repair_mode"] == "economics_materialization"

    poisoned_root = json.loads(json.dumps(after_root))
    poisoned_root["funding"]["position_id"] = f"other-{position_id}"
    poisoned_funding_bytes = json.dumps(
        poisoned_root["funding"], sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    poisoned_root["funding_accounting_version"] = "local-paper-funding-v2"
    poisoned_root["funding_economics_fingerprint"] = (
        "sha256:" + hashlib.sha256(poisoned_funding_bytes).hexdigest()
    )
    repo.update_position(
        position_id,
        funding_cost=after_position["funding_cost"],
        reconciliation_evidence=json.dumps(
            poisoned_root, sort_keys=True, separators=(",", ":"),
        ),
    )
    invalid_nested = inspect_paper_funding_reconciliation(
        position_ids=[position_id],
    )["rows"][0]
    assert invalid_nested["categories"] == ["funding_identity_mismatch"]
    assert invalid_nested["repair_mode"] == "economics_materialization"


@spec_claim(
    SpecClaim(
        "PFA-REC-03",
        "paper_funding_reconciliation",
        "operator and Doctor visibility",
        "L2c",
        "The operator CLI defaults to dry-run, rejects unguarded apply, and Doctor "
        "reports recoverable paper funding divergence.",
        status="implemented",
    )
)
def test_paper_funding_cli_and_doctor_expose_guarded_reconciliation(
    tmp_path, monkeypatch,
):
    from typer.testing import CliRunner

    _setup_db(tmp_path, monkeypatch)
    position_id = _seed_recoverable_position()
    from aletheia.cli.data_cmd import data_app
    from aletheia.diagnostics.doctor import Report, _probe_paper_funding_accounting
    from aletheia.storage import migrations

    migration_calls: list[str] = []
    monkeypatch.setattr(
        migrations,
        "run_migrations",
        lambda: migration_calls.append("run_migrations"),
    )

    runner = CliRunner()
    dry_run = runner.invoke(
        data_app, ["reconcile-paper-funding", "--position-id", position_id],
    )
    unguarded = runner.invoke(
        data_app,
        ["reconcile-paper-funding", "--apply"],
    )
    report = Report()
    _probe_paper_funding_accounting(report)
    check = next(
        item for item in report.checks if item.name == "accounting.paper_funding"
    )

    assert dry_run.exit_code == 0
    assert migration_calls == []
    assert json.loads(dry_run.output)["summary"]["recoverable_null_pnl"] == 1
    assert unguarded.exit_code != 0
    # Strip ANSI color codes before checking: FORCE_COLOR (set by GitHub Actions)
    # causes Rich to split option names with escape sequences, breaking literal match.
    assert "--expected-fingerprint" in re.sub(r"\x1b\[[0-9;]*m", "", unguarded.output)
    assert check.severity == "blocker"
    assert "recoverable=1" in check.detail
    assert "ledger_mismatch=1" in check.detail


def test_paper_funding_doctor_warns_when_clean_sample_is_truncated(monkeypatch):
    from aletheia.diagnostics.doctor import Report, _probe_paper_funding_accounting
    from aletheia.maintenance import paper_funding_reconciliation

    summary = {
        name: 0
        for name in (
            "funding_identity_mismatch",
            "recoverable_null_pnl",
            "terminal_now_recoverable",
            "duplicate_settlement_numeric_mismatch",
            "close_event_projection_mismatch",
            "paper_cash_ledger_mismatch",
            "outcome_projection_mismatch",
            "missing_outcome_hooks",
            "still_incomplete",
        )
    }
    monkeypatch.setattr(
        paper_funding_reconciliation,
        "paper_funding_diagnostic_summary",
        lambda **_: {
            "accounting_version": "local-paper-funding-v2",
            "summary": summary,
            "scanned": 100,
            "truncated": True,
        },
    )

    report = Report()
    _probe_paper_funding_accounting(report)
    check = next(
        item for item in report.checks if item.name == "accounting.paper_funding"
    )

    assert check.severity == "warn"
    assert "truncated=true" in check.detail
    assert "complete dry run" in check.fix_hint
