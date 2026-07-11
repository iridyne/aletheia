"""Regression coverage for bounded observer-loop fairness."""

from aletheia.agents.round_robin import rotate_from_next


def _prepare_db(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sqlite
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    sqlite.reset_db()
    run_migrations()


def test_rotation_starts_at_persisted_identity_and_wraps():
    assert rotate_from_next(["a", "b", "c"], next_key="b", key=str) == ["b", "c", "a"]


def test_rotation_deduplicates_and_recovers_when_saved_item_disappears():
    assert rotate_from_next(["a", "a", "b"], next_key="gone", key=str) == ["a", "b"]


def test_durable_state_advances_to_the_first_unprocessed_successor(tmp_path, monkeypatch):
    from aletheia.agents.round_robin import DurableRoundRobin

    _prepare_db(tmp_path, monkeypatch)
    first = DurableRoundRobin("test.observer.round-robin")
    first.advance_after(["a", "b", "c"], completed_key="a", key=str)

    # A new instance models a gateway restart between observer cycles.
    restarted = DurableRoundRobin("test.observer.round-robin")
    assert restarted.next_key() == "b"
    assert rotate_from_next(["a", "b", "c"], next_key=restarted.next_key(), key=str) == ["b", "c", "a"]
