"""Dry-run and apply contracts for paper funding reconciliation."""

from __future__ import annotations

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
    assert first["summary"]["recoverable_null_pnl"] == 1
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
    assert json.loads(dry_run.output)["summary"]["recoverable_null_pnl"] == 1
    assert unguarded.exit_code != 0
    # Strip ANSI color codes before checking: FORCE_COLOR (set by GitHub Actions)
    # causes Rich to split option names with escape sequences, breaking literal match.
    assert "--expected-fingerprint" in re.sub(r"\x1b\[[0-9;]*m", "", unguarded.output)
    assert check.severity == "blocker"
    assert "recoverable=1" in check.detail
    assert "ledger_mismatch=1" in check.detail
