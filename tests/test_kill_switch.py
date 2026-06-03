"""Tests for the global kill switch (system-wide trading brake)."""

import sqlite3

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations


def setup_test_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()


def test_kill_switch_inactive_by_default(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    from aletheia.risk import kill_switch
    assert kill_switch.is_active() is False


def test_kill_switch_activate_persists(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    from aletheia.risk import kill_switch
    kill_switch.activate(reason="manual test", actor="human")
    assert kill_switch.is_active() is True

    # Persisted to config — a fresh load still reports active
    from aletheia.config.loader import load_config
    assert load_config().runtime.kill_switch_active is True


def test_kill_switch_deactivate(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    from aletheia.risk import kill_switch
    kill_switch.activate(reason="x")
    kill_switch.deactivate(actor="human")
    assert kill_switch.is_active() is False


def test_kill_switch_writes_audit_event(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    from aletheia.risk import kill_switch
    from aletheia.files.jsonl import read_jsonl

    kill_switch.activate(reason="loss limit", actor="system")
    events = read_jsonl("audit.jsonl")
    types = [e.get("event_type") for e in events]
    assert "kill_switch_activated" in types


def test_kill_switch_blocks_all_proposals(tmp_path, monkeypatch):
    """When the kill switch is active, the risk engine rejects every proposal."""
    setup_test_env(tmp_path, monkeypatch)
    from aletheia.risk import kill_switch
    from aletheia.risk.engine import evaluate_proposal
    from aletheia.storage.models import TradeProposal
    from aletheia.storage import repositories as repo

    kill_switch.activate(reason="brake")
    proposal = TradeProposal(
        symbol="BTCUSDT", market_type="spot", direction="long",
        execution_mode="paper_internal", quantity=0.001, price=60000,
    )
    repo.insert_trade_proposal(proposal)
    result = evaluate_proposal(proposal)
    assert result.approved is False
    assert any("kill_switch_active" in v for v in result.violations)
