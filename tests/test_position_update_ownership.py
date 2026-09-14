import ast
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


def _assert_raw_position_writes(root: Path):
    """Literal SQL/helper guard, not a claim to resolve arbitrary dynamic SQL."""
    allowed = {f"aletheia/{path}" for path in _RAW_POSITION_UPDATE_ALLOWED}
    writes = re.compile(
        r'''\b(?:UPDATE(?:\s+OR\s+\w+)?|INSERT\s+(?:OR\s+\w+\s+)?INTO|REPLACE\s+INTO|DELETE\s+FROM)\s+'''
        r'''(?:(?:"[^"]+"|`[^`]+`|\[[^\]]+\]|\w+)\s*\.\s*)?'''
        r'''(?:"positions"|`positions`|\[positions\]|positions\b)''', re.I,
    )
    for directory in ("aletheia", "core", "extensions", "legacy", "ops", "scripts"):
        for path in (root / directory).rglob("*.py"):
            if path.relative_to(root).as_posix() in allowed:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
            aliases = {
                alias.asname or alias.name: alias.name
                for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                for alias in node.names if alias.name in {"_insert", "_insert_many", "_update"}
            }
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    assert not writes.search(node.value), (path, node.lineno)
                if isinstance(node, (ast.Name, ast.Attribute)) and isinstance(node.ctx, ast.Load):
                    name = node.attr if isinstance(node, ast.Attribute) else aliases.get(node.id, node.id)
                    if name in {"_insert", "_insert_many", "_update"}:
                        call = parents.get(node)
                        assert isinstance(call, ast.Call) and call.func is node, (path, node.lineno)
                if isinstance(node, ast.Call):
                    name = node.func.attr if isinstance(node.func, ast.Attribute) else aliases.get(node.func.id, node.func.id) if isinstance(node.func, ast.Name) else None
                    if name in {"_insert", "_insert_many", "_update"}:
                        table = node.args[0] if node.args else next((kw.value for kw in node.keywords if kw.arg == "table"), None)
                        assert not (isinstance(table, ast.Constant) and table.value == "positions"), (path, node.lineno)


def test_raw_position_updates_are_confined_to_the_owning_modules():
    """PR #2955 removed the generic writer but not the raw-SQL bypasses.

    Historical raw-SQL bypasses skipped the field whitelist, expected-state
    CAS and PositionClaimConflict rowcount check. Current writes stay with
    the explicit owners below.
    """
    _assert_raw_position_writes(Path(__file__).parents[1])


@pytest.mark.parametrize("directory", ["aletheia", "core", "extensions", "legacy", "ops", "scripts"])
@pytest.mark.parametrize("source", [
    "db.execute('update [positions] SET realized_pnl=1')",
    "db.execute('UPDATE OR REPLACE positions SET realized_pnl=1')",
    "db.execute('UPDATE OR IGNORE main.[positions] SET realized_pnl=1')",
    "db.execute('DELETE FROM main.\"positions\"')",
    "db.execute('INSERT OR IGNORE INTO `positions` VALUES (?)')",
    "repo._update(table='positions', oid='id', data={'outcome_eligible': True})",
    "from aletheia.storage.repositories import _update as change\nchange('positions', 'id', fields)",
    "change = repo._update\nchange('positions', 'id', fields)",
    "from aletheia.storage.repositories import _insert as insert\nchange = insert\nchange('positions', fields)",
])
def test_position_writer_guard_rejects_bypasses(tmp_path, directory, source):
    path = tmp_path / directory / "other.py"
    path.parent.mkdir()
    path.write_text(source, encoding="utf-8")
    with pytest.raises(AssertionError):
        _assert_raw_position_writes(tmp_path)


# Distinct facts share storage, not permission to rewrite each other's fields.
_SETTLEMENT_CALLERS = {
    ("pnl.py", "close_position", "_commit_close_economics"): None,
    ("broker_funding_settlement.py", "materialize_exchange_close_costs", "_commit_close_economics"): None,
    ("pnl.py", "_commit_close_economics", "finalize_position_close"): None,
    ("pnl.py", "_commit_close_economics", "update_closed_position_settlement"): None,
    ("outcome_closure.py", "close_position_administrative", "finalize_position_close"): None,
    ("pnl.py", "persist_exit_reasoning", "update_closed_position_settlement"): {"exit_reasoning"},
    ("outcome_hooks.py", "_load_eligible_position", "update_closed_position_settlement"): {
        "outcome_eligible", "outcome_exclusion_reason",
    },
    ("broker_funding_settlement.py", "_terminalize_funding_history_expired", "update_closed_position_settlement"): {
        "funding_source", "outcome_eligible", "outcome_exclusion_reason", "reconciliation_evidence",
    },
}


def _assert_settlement_callers(root: Path):
    writers = {key[2] for key in _SETTLEMENT_CALLERS}
    seen = set()
    for directory in ("aletheia", "core", "extensions", "legacy", "ops", "scripts"):
        for path in (root / directory).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
            aliases = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        if alias.name in writers:
                            assert any(
                                path == root / "aletheia/portfolio" / key[0] and alias.name == key[2]
                                for key in _SETTLEMENT_CALLERS
                            ), (path, node.lineno)
                            aliases[alias.asname or alias.name] = alias.name
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Name, ast.Attribute)) or not isinstance(node.ctx, ast.Load):
                    continue
                name = node.attr if isinstance(node, ast.Attribute) else aliases.get(node.id, node.id)
                if name not in writers:
                    continue
                call = parents.get(node)
                # Do not permit hiding a settlement writer in a callback/alias.
                assert isinstance(call, ast.Call) and call.func is node, (path, node.lineno)
                parent = parents.get(call)
                while parent is not None and not isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    parent = parents.get(parent)
                key = (path.name, parent.name if parent else None, name)
                assert path.parent == root / "aletheia/portfolio" and key in _SETTLEMENT_CALLERS, (path, node.lineno)
                fields = _SETTLEMENT_CALLERS[key]
                if fields is not None:
                    assert len(call.args) <= 1 and not any(isinstance(arg, ast.Starred) for arg in call.args), (path, node.lineno)
                    assert {kw.arg for kw in call.keywords} <= fields | {"position_id"}, (path, node.lineno)
                seen.add(key)
    return seen


def test_settlement_callers_match_the_audited_facts_and_field_scopes():
    assert _assert_settlement_callers(Path(__file__).parents[1]) == set(_SETTLEMENT_CALLERS)


@pytest.mark.parametrize("path,source", [
    ("extensions/other.py", "from aletheia.storage.repositories import finalize_position_close as close\nclose('id', fields)"),
    ("aletheia/portfolio/other.py", "from ..storage.repositories import update_closed_position_settlement as update\nupdate('id', realized_pnl=1)"),
    ("aletheia/portfolio/pnl.py", "def other():\n    repo.update_closed_position_settlement('id', realized_pnl=1)"),
    ("aletheia/portfolio/outcome_hooks.py", "def _load_eligible_position():\n    repo.update_closed_position_settlement('id', realized_pnl=1)"),
    ("aletheia/portfolio/outcome_hooks.py", "def _load_eligible_position():\n    repo.update_closed_position_settlement('id', **fields)"),
    ("aletheia/portfolio/outcome_hooks.py", "def _load_eligible_position():\n    callback = repo.update_closed_position_settlement"),
])
def test_settlement_guard_rejects_new_owner_or_expanded_field_authority(tmp_path, path, source):
    target = tmp_path / path
    target.parent.mkdir(parents=True)
    target.write_text(source, encoding="utf-8")
    with pytest.raises(AssertionError):
        _assert_settlement_callers(tmp_path)


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
