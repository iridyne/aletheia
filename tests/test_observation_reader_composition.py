"""Optional Lab must never grant writes/deletion by disappearing."""

from tests.helpers.repo_test_helpers import insert_filter_shadow_evaluation, insert_filter_shadow_experiment, update_filter_shadow_experiment

from contextlib import contextmanager

import pytest

from aletheia.storage import repositories as repo


@pytest.fixture(autouse=True)
def absent_readers(monkeypatch):
    for name in (
        "_lab_authority", "_lab_retention_floor", "_lab_retention_refs",
        "_lab_priority_keys", "_lab_active_benchmark", "_lab_outcome_guard",
    ):
        monkeypatch.setattr(repo, name, None)


def test_absence_denies_authority_and_retention():
    for operation in (
        repo._canonical_lab_authority,
        lambda: repo._observation_retention_floor("outcomes"),
        lambda: repo._active_observation_claim_refs("outcomes"),
        lambda: insert_filter_shadow_experiment({}),
        lambda: update_filter_shadow_experiment("absent", status="active"),
        lambda: insert_filter_shadow_evaluation({}),
    ):
        with pytest.raises(repo.ExperimentStateReadError):
            operation()
    assert repo.prune_filter_candidate_outcomes() == 0
    assert repo.prune_filter_shadow_experiments() == 0
    with pytest.raises(OSError):
        repo.list_observation_filter_outcome_priority_keys(deadline=0)


def test_missing_window_guard_never_updates(monkeypatch):
    monkeypatch.setattr(repo, "_select_one", lambda *_: {"benchmark_window_id": "w"})
    with pytest.raises(repo.ExperimentStateReadError):
        repo._settle_outcome_with_exact_venue("filter_candidate_outcomes", "o", {}, kind="filter")


@pytest.mark.parametrize("window", [None, "w"])
def test_guard_spans_status_transaction_cas_and_linked_shadow(monkeypatch, window):
    events = []
    row = {"benchmark_window_id": window, "status": "pending", "venue": "bitget",
           "horizon": "24h", "outcome_candidate_key": "key"}
    monkeypatch.setattr(repo, "_select_one", lambda *_: row)

    @contextmanager
    def guard(window_id):
        assert window_id == "w"
        events.append("guard-enter")
        yield True
        events.append("guard-exit")

    @contextmanager
    def transaction(**kwargs):
        assert kwargs == {"immediate": True}
        events.append("tx-enter")
        yield
        events.append("tx-exit")

    monkeypatch.setattr(repo, "_lab_outcome_guard", guard)
    monkeypatch.setattr(repo, "transaction", transaction)
    monkeypatch.setattr(repo, "_update_pending", lambda *_: events.append("cas") or True)
    monkeypatch.setattr(repo, "settle_linked_shadow_outcomes", lambda *_: events.append("linked"))
    assert repo._settle_outcome_with_exact_venue("filter_candidate_outcomes", "o", {}, kind="filter")
    expected = ["tx-enter", "cas", "linked", "tx-exit"]
    assert events == (["guard-enter", *expected, "guard-exit"] if window else expected)


def test_window_guard_preserves_legacy_first_and_unknown_denial(monkeypatch):
    from extensions.observation_lab import main_readers as lab, kernel, worker_lock

    events = []
    @contextmanager
    def lock():
        events.append("enter")
        yield
        events.append("exit")

    monkeypatch.setattr(worker_lock, "worker_cycle_exclusion", lock)
    monkeypatch.setattr(lab, "get_universe_discovery_benchmark", lambda _: {"status": "finalized"})
    monkeypatch.setattr(kernel, "status_window", lambda _: pytest.fail("legacy must win"))
    with lab.outcome_write_guard("w") as allowed:
        assert not allowed
        assert events == ["enter"]
    assert events == ["enter", "exit"]
    monkeypatch.setattr(lab, "get_universe_discovery_benchmark", lambda _: None)
    def unknown(_):
        raise KeyError("unknown")
    monkeypatch.setattr(kernel, "status_window", unknown)
    with lab.outcome_write_guard("unknown") as allowed:
        assert not allowed


def test_pending_selection_keeps_active_lab_first_and_legacy_fallback(monkeypatch):
    monkeypatch.setattr(repo, "_lab_active_benchmark", lambda: {"status": "active", "observation_id": "lab"})
    monkeypatch.setattr(repo, "get_universe_discovery_benchmark", lambda: {"status": "active", "id": "legacy"})
    monkeypatch.setattr(repo, "_select_all", lambda *args, **kwargs: kwargs["params"])
    assert repo.list_pending_universe_discovery_outcomes("now")[0] == "lab"
    monkeypatch.setattr(repo, "_lab_active_benchmark", None)
    assert repo.list_pending_universe_discovery_outcomes("now")[0] == "legacy"


@pytest.mark.parametrize("entry", ["core/aletheia/main.py", "core/aletheia/server/app.py", "ops/maintenance/data_retention_backfill.py"])
@pytest.mark.parametrize("missing", [None, "extensions.observation_lab", "broken_lab_dependency"])
def test_exact_composition_blocks_are_cold_and_clear_stale_bindings(tmp_path, entry, missing):
    import os
    from pathlib import Path
    import subprocess
    import sys

    script = r'''
import ast
import importlib.abc
import pathlib
import socket
import sys
from aletheia.storage import repositories as repo
from aletheia.storage import sqlite
def forbidden(*args, **kwargs):
    raise AssertionError("composition must not perform I/O")
sqlite.get_db = forbidden
socket.socket.connect = forbidden
from aletheia.config import secrets
secrets.get_secret = forbidden
entry, missing = sys.argv[1:]
class Absent(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "extensions.observation_lab.main_readers" and missing:
            raise ModuleNotFoundError("bounded failure", name=missing)
sys.meta_path.insert(0, Absent())
repo.configure_observation_readers(authority=lambda: True)
tree = ast.parse(pathlib.Path(entry).read_text())
function = "_configure_runtime" if entry.endswith("main.py") else "run" if "data_retention_backfill" in entry else None
body = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function).body if function else tree.body
index = next(i for i, n in enumerate(body) if isinstance(n, ast.ImportFrom) and n.module == "aletheia.storage.repositories" and any(a.name == "configure_observation_readers" for a in n.names))
selected = ast.Module(body=body[index:index+2], type_ignores=[])
try:
    exec(compile(selected, entry, "exec"), {})
except ModuleNotFoundError as exc:
    assert missing == "broken_lab_dependency" and exc.name == missing
else:
    assert missing != "broken_lab_dependency"
    bindings = [repo._lab_authority, repo._lab_retention_floor, repo._lab_retention_refs, repo._lab_priority_keys, repo._lab_active_benchmark, repo._lab_outcome_guard, repo._lab_live_window_ids]
    assert all(callback is None for callback in bindings) if missing else all(callable(callback) for callback in bindings)
'''
    result = subprocess.run(
        [sys.executable, "-c", script, entry, missing or ""],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "ALETHEIA_DATA_DIR": str(tmp_path), "ALETHEIA_RUNTIME_MODE": "test"},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
