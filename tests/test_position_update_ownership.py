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
        execution_mode="bitget_demo",
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
    # Owner-approved #3111 fixed-target recovery: exact before-state comparison,
    # private evidence validation and atomic audit idempotency; never a runtime caller.
    # Do not widen the ordinary Position field owners for this historical repair.
    "maintenance/repair_doge_adoption_overlap.py",
}


def test_raw_position_updates_are_confined_to_the_owning_modules():
    """PR #2955 removed the generic writer but not the raw-SQL bypasses.

    Historical raw-SQL bypasses skipped the field whitelist, expected-state
    CAS and PositionClaimConflict rowcount check. Current writes stay with
    the explicit owners below.
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


def test_fixed_target_historical_repair_is_not_imported_by_production():
    import ast

    root = Path(__file__).parents[1] / "aletheia"
    for source in root.rglob("*.py"):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [alias.name for alias in node.names]
                if isinstance(node, ast.ImportFrom) and node.module:
                    names.append(node.module)
                assert not any("repair_doge_adoption_overlap" in name for name in names), source


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
