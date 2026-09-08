from pathlib import Path

import pytest

from aletheia.storage import repositories as repo
from aletheia.storage.models import Position


def _position(position_id: str = "owned-position") -> Position:
    position = Position(
        id=position_id,
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        current_price=100.0,
        execution_mode="paper_internal",
    )
    repo.insert_position(position)
    return position


def test_position_field_owners_reject_foreign_fields_and_stale_state():
    position = _position()

    with pytest.raises(ValueError, match="invalid_observation_position_fields:quantity"):
        repo.update_open_position_observation(position.id, quantity=2.0)

    repo.update_open_position_materialization(position.id, quantity=2.0)
    repo.update_open_position_exit_plan(position.id, stop_price=90.0)
    repo.update_open_position_observation(position.id, current_price=101.0)
    repo.finalize_position_close(position.id, {"status": "closed"})

    with pytest.raises(repo.PositionClaimConflict, match="observation_position_conflict"):
        repo.update_open_position_observation(position.id, current_price=102.0)
    repo.update_closed_position_settlement(position.id, realized_pnl=2.0)

    stored = repo.get_position(position.id)
    assert stored is not None
    assert stored["quantity"] == 2.0
    assert stored["stop_price"] == 90.0
    assert stored["current_price"] == 101.0
    assert stored["realized_pnl"] == 2.0


def test_production_has_no_generic_position_update_entrypoint():
    root = Path(__file__).parents[1] / "aletheia"
    offenders = []
    for source in root.rglob("*.py"):
        if "update_position(" in source.read_text(encoding="utf-8"):
            offenders.append(str(source.relative_to(root)))
    assert offenders == []


# Raw `UPDATE positions` is allowed only where per-owner field ownership cannot
# apply. No trigger can express that ownership -- SQLite has no notion of which
# Python component issued a statement -- so this scan is the only enforcement
# there is, and banning the `update_position(` symbol alone left it open.
_RAW_POSITION_UPDATE_ALLOWED = {
    # Owns the table definition and every migration that rewrites it.
    "storage/migrations.py",
    # Hosts the owners themselves, including _update_owned_position_fields.
    "storage/repositories.py",
    # One-shot remediation batch (REMEDIATION_BATCH_ID). Its idempotence key is
    # `outcome_semantics_version IS NULL`, not a status CAS, and routing it
    # through the settlement owner would mean permanently widening that owner's
    # whitelist by close_category + outcome_semantics_version to accommodate a
    # migration-shaped job. Keep the raw statement and the narrower authority.
    "maintenance/outcome_integrity.py",
}


def test_raw_position_updates_are_confined_to_the_owning_modules():
    """PR #2955 removed the generic writer but not the raw-SQL bypasses.

    Three sites still wrote `positions` directly, skipping the field whitelist,
    the expected-state CAS and the rowcount check that raises
    PositionClaimConflict: two in paper_account.py (one live, one dead since the
    economics write moved to the settlement owner) and one remediation batch.
    """
    root = Path(__file__).parents[1] / "aletheia"
    offenders = sorted(
        str(source.relative_to(root))
        for source in root.rglob("*.py")
        if "UPDATE positions" in source.read_text(encoding="utf-8")
        and str(source.relative_to(root)) not in _RAW_POSITION_UPDATE_ALLOWED
    )
    assert offenders == [], (
        f"raw `UPDATE positions` outside the owning modules: {offenders}. "
        "Route the write through one of the four public position owners in "
        "storage/repositories.py, or justify an addition to "
        "_RAW_POSITION_UPDATE_ALLOWED in this file."
    )


def test_known_pre_submission_error_can_be_reclaimed_by_one_new_close_order():
    position = _position()
    repo.claim_position_close(
        position.id,
        order_id="failed-close",
        status="pending",
        requested_at="2026-09-06T00:00:00+00:00",
    )
    repo.update_position_close_claim(
        position.id,
        order_id="failed-close",
        status="error",
    )

    repo.claim_position_close(
        position.id,
        order_id="retry-close",
        status="pending",
        requested_at="2026-09-06T00:01:00+00:00",
    )

    stored = repo.get_position(position.id)
    assert stored["pending_close_order_id"] == "retry-close"
    assert stored["pending_close_status"] == "pending"
