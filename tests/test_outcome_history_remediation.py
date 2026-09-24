"""Historical outcome repair stays additive, explainable, and idempotent."""

from __future__ import annotations

import json
import pytest


def _seed_polluted_history() -> None:
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import KnowledgeItem, Position

    common = {
        "symbol": "BTC/USDT", "market_type": "spot", "direction": "long",
        "quantity": 1.0, "entry_price": 100.0, "execution_mode": "paper_internal",
        "status": "closed", "closed_at": "2026-07-10T12:00:00+00:00",
    }
    repo._insert("positions", Position(
        **common, id="admin-close", realized_pnl=12.0,
        close_reason="reconciled_gone",
    ).model_dump())
    repo._insert("positions", Position(
        **common, id="ambiguous-close", realized_pnl=-4.0,
        close_reason="manual",
    ).model_dump())
    repo._insert("positions", Position(**(common | {
        "id": "current-null-semantics",
        "closed_at": "2026-09-23T17:00:00+00:00",
        "realized_pnl": 3.0,
        "close_reason": "reconciled_gone",
    })).model_dump())
    repo._insert("paper_account_events", dict(
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
    repo.insert_knowledge_item(KnowledgeItem(
        id="current-calibration", item_type="calibration_report",
        title="current calibration", status="active",
        content=json.dumps({
            "outcome_semantics_version": "oi-v2",
            "clean_window_anchor_id": "outcome-integrity-v2-2026-09-23",
        }),
        created_at="2026-09-23T17:00:00+00:00",
    ))
    repo.insert_knowledge_item(KnowledgeItem(
        id="current-offset-calibration", item_type="calibration_report",
        title="current offset calibration", status="active",
        content=json.dumps({
            "outcome_semantics_version": "oi-v2",
            "clean_window_anchor_id": "outcome-integrity-v2-2026-09-23",
        }),
        created_at="2026-09-23T12:44:00-04:00",
    ))

def test_history_remediation_dry_run_apply_and_repeat_are_non_destructive():
    from ops.maintenance.outcome_integrity import remediate_outcome_history
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
    current = dict(db.execute(
        "SELECT * FROM positions WHERE id='current-null-semantics'"
    ).fetchone())
    assert current["close_category"] == "unknown"
    assert current["outcome_semantics_version"] is None

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
        "SELECT content, content_format, content_sha256, metadata_json "
        "FROM knowledge_items "
        "WHERE item_type='outcome_remediation_supersession' ORDER BY id"
    ).fetchall()
    assert len(markers) == 2
    for marker in markers:
        assert marker["content_format"] == "knowledge-markdown/v1"
        assert marker["content_sha256"]
        assert marker["metadata_json"] == "{}"
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
    assert db.execute(
        "SELECT status FROM knowledge_items WHERE id='current-calibration'"
    ).fetchone()[0] == "active"
    assert db.execute(
        "SELECT status FROM knowledge_items WHERE id='current-offset-calibration'"
    ).fetchone()[0] == "active"
    assert db.execute("SELECT COUNT(*) FROM capital_events").fetchone()[0] == 0

def test_versioned_clean_window_excludes_pre_anchor_incident_and_unknown_version():
    from aletheia.analytics.outcome_integrity import OUTCOME_SEMANTICS_VERSION
    from aletheia.analytics.learning_window import (
        CLEAN_WINDOW_ANCHOR_ID,
        CLEAN_WINDOW_ANCHOR_ISO,
        assess_outcome_window,
    )

    base = {
        "closed_at": CLEAN_WINDOW_ANCHOR_ISO,
        "outcome_semantics_version": OUTCOME_SEMANTICS_VERSION,
    }
    assert OUTCOME_SEMANTICS_VERSION == "oi-v2"
    assert CLEAN_WINDOW_ANCHOR_ID == "outcome-integrity-v2-2026-09-23"
    assert assess_outcome_window({**base, "outcome_semantics_version": "oi-v1"}) == "unknown_semantics_version"
    assert assess_outcome_window({**base, "closed_at": "2026-07-10T00:00:00+00:00"}) == "pre_clean_window"
    assert assess_outcome_window({**base, "closed_at": "2026-07-12T06:00:00+00:00"}) == "incident_window"
    assert assess_outcome_window({**base, "outcome_semantics_version": "oi-v0"}) == "unknown_semantics_version"
    assert assess_outcome_window(base) == "eligible"


def test_clean_window_dependent_fixtures_derive_timestamps_from_the_anchor():
    """Fixtures gated by the clean window must anchor, not hardcode a date.

    The anchor is a real moving boundary: it advances whenever attribution
    integrity is re-anchored. A fixture timestamp written as an absolute literal
    sits inside the window only until the next bump, then falls out — and the
    failure is silent and misleading, because the cohort simply reads as empty.
    That is exactly how test_universe_calibration, test_universe_twin_experiment
    and three test_filter_shadow_experiments cases went red for weeks
    (KeyError on a present cohort, `complete_cluster_n == 0`,
    `insufficient_data` instead of `positive`) and read as regressions.

    Any test module that imports the anchor is asserting clean-window behavior,
    so its own fixture timestamps must be derived from the anchor.
    """
    import ast
    from datetime import datetime
    from pathlib import Path

    from aletheia.analytics.learning_window import CLEAN_WINDOW_ANCHOR_ISO

    anchor = datetime.fromisoformat(CLEAN_WINDOW_ANCHOR_ISO)
    tests_root = Path(__file__).parent
    offenders = []
    for path in sorted(tests_root.glob("test_*.py")):
        source = path.read_text(encoding="utf-8")
        if "CLEAN_WINDOW_ANCHOR_ISO" not in source and "LEARNING_DATA_FLOOR_ISO" not in source:
            continue  # not a clean-window-dependent module
        for node in ast.walk(ast.parse(source)):
            # `datetime(2026, 7, 20, ...)` built from integer literals
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "datetime" and len(node.args) >= 3
                    and all(isinstance(a, ast.Constant) and isinstance(a.value, int) for a in node.args[:3])):
                year, month, day = (a.value for a in node.args[:3])
                try:
                    built = datetime(year, month, day, tzinfo=anchor.tzinfo)
                except ValueError:
                    continue
                if built < anchor.replace(hour=0, minute=0, second=0, microsecond=0):
                    offenders.append(f"{path.name}:{node.lineno} datetime({year}, {month}, {day}, ...)")
    assert not offenders, (
        "these clean-window-dependent fixtures hardcode a pre-anchor date and will "
        "silently fall out of the window at the next re-anchor; derive them from "
        f"CLEAN_WINDOW_ANCHOR_ISO instead: {offenders}"
    )


def test_apply_rolls_back_classification_when_archive_step_fails():
    from ops.maintenance.outcome_integrity import remediate_outcome_history
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
    import ops.maintenance.outcome_integrity as module

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
