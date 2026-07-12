"""Historical outcome repair stays additive, explainable, and idempotent."""

from __future__ import annotations

import json
import pytest


def _seed_polluted_history() -> None:
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import KnowledgeItem, PaperAccountEvent, Position

    common = {
        "symbol": "BTC/USDT", "market_type": "spot", "direction": "long",
        "quantity": 1.0, "entry_price": 100.0, "execution_mode": "paper_internal",
        "status": "closed", "closed_at": "2026-07-10T12:00:00+00:00",
    }
    repo.insert_position(Position(
        **common, id="admin-close", realized_pnl=12.0,
        close_reason="reconciled_gone",
    ))
    repo.insert_position(Position(
        **common, id="ambiguous-close", realized_pnl=-4.0,
        close_reason="manual",
    ))
    repo.insert_paper_account_event(PaperAccountEvent(
        id="cash-admin", action="position_closed", amount_usdt=12.0,
        cash_balance_before_usdt=1000.0, cash_balance_after_usdt=1012.0,
        position_id="admin-close",
    ))
    repo.insert_knowledge_item(KnowledgeItem(
        id="old-calibration", item_type="calibration_report",
        title="old calibration", status="active",
        content=json.dumps({"generated_at": "2026-07-10T12:00:00+00:00"}),
        created_at="2026-07-10T12:00:00+00:00",
    ))
    repo.insert_knowledge_item(KnowledgeItem(
        id="old-operator", item_type="operator_assessment",
        title="old operator assessment", status="active",
        content="historical assessment based on the polluted outcome window",
        created_at="2026-07-10T12:00:00+00:00",
    ))


def test_history_remediation_dry_run_apply_and_repeat_are_non_destructive():
    from aletheia.maintenance.outcome_integrity import remediate_outcome_history
    from aletheia.storage.sqlite import get_db

    _seed_polluted_history()
    db = get_db()
    before_positions = [dict(row) for row in db.execute(
        "SELECT id, realized_pnl, closed_at FROM positions ORDER BY id"
    )]
    before_cash = [dict(row) for row in db.execute(
        "SELECT * FROM paper_account_events ORDER BY id"
    )]

    dry = remediate_outcome_history(dry_run=True)
    assert dry["classification"]["planned"] == 1
    assert dry["archive"]["planned"] == 2
    assert dry["archive"]["by_type"] == {
        "calibration_report": 1, "operator_assessment": 1,
    }
    assert dry["owner_approval_required"] is True
    assert dry["compensation_candidates"] == [{
        "candidate_id": dry["compensation_candidates"][0]["candidate_id"],
        "position_id": "admin-close",
        "paper_account_event_id": "cash-admin",
        "amount_usdt": -12.0,
        "reason": "reverse_administrative_close_cash_effect",
        "owner_approval_required": True,
    }]

    applied = remediate_outcome_history(dry_run=False)
    assert applied["classification"]["planned"] == dry["classification"]["planned"]
    assert applied["classification"]["applied"] == 1
    assert applied["archive"]["planned"] == dry["archive"]["planned"]
    assert applied["archive"]["applied"] == 2
    assert applied["compensation_candidates"] == dry["compensation_candidates"]
    assert set(applied["rebuild"]) == {"calibration_report", "reasoning_feedback"}

    admin = dict(db.execute("SELECT * FROM positions WHERE id='admin-close'").fetchone())
    assert admin["close_category"] == "administrative"
    assert admin["outcome_eligible"] == 0
    assert admin["outcome_exclusion_reason"] == "administrative_close"
    assert admin["outcome_semantics_version"] == "oi-v1"
    ambiguous = dict(db.execute("SELECT * FROM positions WHERE id='ambiguous-close'").fetchone())
    assert ambiguous["close_category"] == "unknown"
    assert ambiguous["outcome_semantics_version"] is None

    assert [dict(row) for row in db.execute(
        "SELECT id, realized_pnl, closed_at FROM positions ORDER BY id"
    )] == before_positions
    assert [dict(row) for row in db.execute(
        "SELECT * FROM paper_account_events ORDER BY id"
    )] == before_cash
    assert db.execute(
        "SELECT COUNT(*) FROM knowledge_items WHERE status='archived'"
    ).fetchone()[0] == 2
    markers = db.execute(
        "SELECT content FROM knowledge_items "
        "WHERE item_type='outcome_remediation_supersession' ORDER BY id"
    ).fetchall()
    assert len(markers) == 2
    for marker in markers:
        content = json.loads(marker["content"])
        assert content["batch_id"] == applied["batch_id"]
        assert content["semantics_version"] == "oi-v1"
        assert content["superseded_by"].startswith("clean-window:")

    repeated = remediate_outcome_history(dry_run=False)
    assert repeated["classification"]["applied"] == 0
    assert repeated["archive"]["applied"] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM knowledge_items "
        "WHERE item_type='outcome_remediation_supersession'"
    ).fetchone()[0] == 2
    assert db.execute("SELECT COUNT(*) FROM capital_events").fetchone()[0] == 0


def test_versioned_clean_window_excludes_pre_anchor_incident_and_unknown_version():
    from aletheia.analytics.learning_window import (
        CLEAN_WINDOW_ANCHOR_ID,
        CLEAN_WINDOW_ANCHOR_ISO,
        assess_outcome_window,
    )

    base = {
        "closed_at": CLEAN_WINDOW_ANCHOR_ISO,
        "outcome_semantics_version": "oi-v1",
    }
    assert CLEAN_WINDOW_ANCHOR_ID.startswith("outcome-integrity-")
    assert assess_outcome_window({**base, "closed_at": "2026-07-10T00:00:00+00:00"}) == "pre_clean_window"
    assert assess_outcome_window({**base, "closed_at": "2026-07-12T06:00:00+00:00"}) == "incident_window"
    assert assess_outcome_window({**base, "outcome_semantics_version": "oi-v0"}) == "unknown_semantics_version"
    assert assess_outcome_window({**base, "closed_at": "2026-07-12T08:00:00+00:00"}) == "eligible"


def test_apply_rolls_back_classification_when_archive_step_fails():
    from aletheia.maintenance.outcome_integrity import remediate_outcome_history
    from aletheia.storage.sqlite import get_db

    _seed_polluted_history()
    db = get_db()
    db.execute(
        "CREATE TRIGGER fail_outcome_archive BEFORE UPDATE OF status ON knowledge_items "
        "BEGIN SELECT RAISE(ABORT, 'archive failed'); END"
    )
    with pytest.raises(Exception, match="archive failed"):
        remediate_outcome_history(dry_run=False)
    row = db.execute(
        "SELECT outcome_semantics_version FROM positions WHERE id='admin-close'"
    ).fetchone()
    assert row["outcome_semantics_version"] is None
    assert db.execute(
        "SELECT COUNT(*) FROM knowledge_items WHERE status='archived'"
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM knowledge_items "
        "WHERE item_type='outcome_remediation_supersession'"
    ).fetchone()[0] == 0


def test_maintenance_source_has_no_delete_or_compensation_apply_path():
    from pathlib import Path
    import aletheia.maintenance.outcome_integrity as module

    source = Path(module.__file__).read_text()
    assert "DELETE FROM" not in source.upper()
    assert "insert_capital_event" not in source
    assert "insert_paper_account_event" not in source
    assert "owner_approval_required" in source


def test_cli_defaults_to_dry_run_and_doctor_reports_pending_cleanup():
    from typer.testing import CliRunner
    from aletheia.diagnostics.doctor import Report, _probe_outcome_history_integrity
    from aletheia.main import app

    _seed_polluted_history()
    result = CliRunner().invoke(app, ["knowledge", "remediate-outcomes"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["dry_run"] is True
    assert payload["classification"]["applied"] == 0

    report = Report()
    _probe_outcome_history_integrity(report)
    check = next(item for item in report.checks if item.name == "loop.outcome_history")
    assert check.severity == "info"
    assert "classification_pending=1" in check.detail
    assert "owner approval required" in check.detail
