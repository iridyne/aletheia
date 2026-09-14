from pathlib import Path
import re

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
    root = Path(__file__).parents[1]
    offenders = []
    for domain in (root / "core" / "aletheia", root / "extensions"):
        for source in domain.rglob("*.py"):
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
}


def test_raw_position_updates_are_confined_to_the_owning_modules():
    """PR #2955 removed the generic writer but not the raw-SQL bypasses.

    Historical raw-SQL bypasses skipped the field whitelist, expected-state
    CAS and PositionClaimConflict rowcount check. Current writes stay with
    the explicit owners below.
    """
    root = Path(__file__).parents[1]
    offenders = sorted(
        str(source.relative_to(root))
        for domain in (root / "core" / "aletheia", root / "extensions", root / "ops")
        for source in domain.rglob("*.py")
        if re.search(r"\bUPDATE\s+positions\b", source.read_text(encoding="utf-8"), re.IGNORECASE)
        and str(source.relative_to(root)) not in {
            f"core/aletheia/{owner}" for owner in _RAW_POSITION_UPDATE_ALLOWED
        # Fixed-target #3111 recovery retains its before-state, private-evidence
        # and atomic audit guards; relocation grants no generic Ops writer.
        } | {
            "ops/maintenance/repair_doge_adoption_overlap.py",
            # Version-null classification only; never final money settlement.
            "ops/maintenance/outcome_integrity.py",
        }
    )
    assert offenders == [], (
        f"raw `UPDATE positions` outside the owning modules: {offenders}. "
        "Route the write through one of the four public position owners in "
        "storage/repositories.py, or justify an addition to "
        "_RAW_POSITION_UPDATE_ALLOWED in this file."
    )


@pytest.mark.parametrize("domain", ("core/aletheia", "extensions", "ops"))
@pytest.mark.parametrize("sql", ("UPDATE positions SET", "update\npositions SET"))
def test_raw_position_guard_detects_all_domains(tmp_path, monkeypatch, domain, sql):
    source = tmp_path / domain / "foreign_writer.py"
    source.parent.mkdir(parents=True)
    source.write_text(f'SQL = """{sql}"""\n', encoding="utf-8")
    monkeypatch.setitem(globals(), "__file__", str(tmp_path / "tests" / "ownership.py"))
    with pytest.raises(AssertionError, match="foreign_writer.py"):
        test_raw_position_updates_are_confined_to_the_owning_modules()


@pytest.mark.parametrize("module", ("repair_doge_adoption_overlap", "repair_administrative_close", "data_retention_backfill"))
def test_historical_repairs_are_not_imported_by_production(module):
    import ast

    root = Path(__file__).parents[1] / "core" / "aletheia"
    assert not (root / "maintenance" / f"{module}.py").exists()
    for source in root.rglob("*.py"):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [alias.name for alias in node.names]
                if isinstance(node, ast.ImportFrom) and node.module:
                    names.append(node.module)
                assert not any(module in name for name in names), source


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
