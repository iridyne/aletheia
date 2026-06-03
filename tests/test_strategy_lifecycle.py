"""Tests for strategy lifecycle: forward-only transitions, promotion gates."""

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.trading.strategy_lifecycle import (
    create_strategy, promote_strategy, FORWARD_TRANSITIONS, check_promotion_gates,
)
from aletheia.storage.repositories import get_strategy


def setup_test_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()


def test_create_strategy_in_draft(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    s = create_strategy("Trend Following V1", "Momentum-based")
    assert s.state == "draft"
    record = get_strategy(s.id)
    assert record["name"] == "Trend Following V1"
    reset_db()


def test_forward_transition_works(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    s = create_strategy("Test Strategy")
    result = promote_strategy(s.id, "research_note")
    assert result["success"] is True
    assert result["new_state"] == "research_note"
    record = get_strategy(s.id)
    assert record["state"] == "research_note"
    reset_db()


def test_backward_transition_blocked(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    s = create_strategy("Test")
    promote_strategy(s.id, "research_note")
    result = promote_strategy(s.id, "draft")
    assert result["success"] is False
    assert "Cannot transition" in result["error"]
    reset_db()


def test_skip_transition_blocked(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    s = create_strategy("Test")
    # Can't jump from draft to paper_running
    result = promote_strategy(s.id, "paper_running")
    assert result["success"] is False
    reset_db()


def test_full_lifecycle_forward(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    s = create_strategy("Full Lifecycle")
    transitions = ["research_note", "strategy_hypothesis", "paper_candidate", "paper_running", "paper_passed"]
    for target in transitions:
        result = promote_strategy(s.id, target)
        assert result["success"] is True, f"Failed at {target}: {result}"
    record = get_strategy(s.id)
    assert record["state"] == "paper_passed"
    reset_db()


def test_promotion_gate_check_not_in_paper_passed(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    s = create_strategy("Gate Test")
    result = check_promotion_gates(s.id)
    assert result["passed"] is False
    assert "must_be_in_paper_passed_state" in str(result["failures"])
    reset_db()


def test_live_candidate_requires_paper_passed(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    s = create_strategy("Live Gate Test")
    # Advance to paper_passed
    for target in ["research_note", "strategy_hypothesis", "paper_candidate", "paper_running", "paper_passed"]:
        promote_strategy(s.id, target)
    # Now promote to live_candidate should pass gates
    result = promote_strategy(s.id, "live_candidate")
    assert result["success"] is True
    reset_db()
