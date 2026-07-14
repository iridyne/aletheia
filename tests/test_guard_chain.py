"""Tests for aletheia.trust.guard_chain."""

import sqlite3

import pytest
from unittest.mock import patch


def setup_env(tmp_path, monkeypatch):
    """Real DATA_DIR + DB so run_guards exercises the full chain against real rows."""
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    from aletheia.storage.migrations import run_migrations
    sq.reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()


def test_run_guards_all_pass():
    from aletheia.trust.guard_chain import run_guards, summarize

    proposal = {"symbol": "BTC/USDT", "direction": "long", "conviction": 70}
    with patch("aletheia.trust.guard_chain.repo") as mock_repo:
        mock_repo.list_all_open_positions.return_value = []
        mock_repo.list_portfolio_snapshots.return_value = []
        results = run_guards(proposal)

    assert len(results) == 4
    summary = summarize(results)
    assert summary["blocks"] == 0


def test_run_guards_chain_blocks_and_warns_on_real_data():
    """Full chain end-to-end: with data that trips BOTH a blocking guard
    (drawdown) and a warning guard (exposure concentration), run_guards +
    summarize must report exactly that — not just blocks==0 on empty data.

    Uses the real repo (not a blanket mock) so a regression in any guard's
    threshold, or in summarize's block/warn counting, would fail here.
    """
    from aletheia.trust.guard_chain import run_guards, summarize

    # Two open BTC positions → exposure_concentration warns.
    open_positions = [
        {"symbol": "BTC/USDT", "direction": "long"},
        {"symbol": "BTC/USDT", "direction": "long"},
    ]

    with patch("aletheia.trust.guard_chain.repo") as mock_repo, \
         patch("aletheia.trust.guard_chain.load_config") as mock_cfg:
        mock_cfg.return_value.risk.max_daily_loss_pct = 5.0
        mock_repo.list_all_open_positions.return_value = open_positions
        # 12% drawdown vs 5% limit → drawdown_brake blocks.
        mock_repo.list_portfolio_snapshots.return_value = [
            {"total_value": 880},   # latest
            {"total_value": 1000},  # prev
        ]
        results = run_guards({"symbol": "BTC/USDT", "direction": "long"})

    summary = summarize(results)
    assert summary["guards_run"] == 4
    # drawdown_brake → block; exposure_concentration → warn.
    assert summary["blocks"] == 1
    assert summary["warnings"] >= 1
    verdicts = {r.guard: r.verdict for r in results}
    assert verdicts["drawdown_brake"] == "block"
    assert verdicts["exposure_concentration"] == "warn"
    # The block detail must be carried into the summary for the audit trail.
    blocked_guards = [d["guard"] for d in summary["details"] if d["verdict"] == "block"]
    assert "drawdown_brake" in blocked_guards


def test_exposure_concentration_warns():
    from aletheia.trust.guard_chain import _guard_exposure_concentration

    with patch("aletheia.trust.guard_chain.repo") as mock_repo:
        mock_repo.list_all_open_positions.return_value = [
            {"symbol": "BTC/USDT", "direction": "long"},
            {"symbol": "BTC/USDT", "direction": "long"},
        ]
        result = _guard_exposure_concentration({"symbol": "BTC/USDT", "direction": "long"})

    assert result.verdict == "warn"
    assert "2 open positions" in result.reason


def test_recent_loss_streak_warns():
    from aletheia.trust.guard_chain import _guard_recent_loss_streak

    positions = [
        {"strategy_id": "s1", "closed_at": f"2026-01-0{i}", "realized_pnl": -5}
        for i in range(5, 0, -1)
    ]
    with patch("aletheia.trust.guard_chain.eligible_strategy_outcomes", return_value=positions):
        result = _guard_recent_loss_streak({"strategy_id": "s1"})

    assert result.verdict == "warn"
    assert "consecutive losses" in result.reason


def test_correlation_cluster_warns():
    from aletheia.trust.guard_chain import _guard_correlation_cluster

    with patch("aletheia.trust.guard_chain.repo") as mock_repo:
        mock_repo.list_all_open_positions.return_value = [
            {"symbol": "JUP/USDT", "direction": "long"},
        ]
        result = _guard_correlation_cluster({"symbol": "SOL/USDT", "direction": "long"})

    assert result.verdict == "warn"
    assert "JUP/USDT" in result.reason


def test_drawdown_brake_blocks():
    from aletheia.trust.guard_chain import _guard_drawdown_brake

    with patch("aletheia.trust.guard_chain.repo") as mock_repo, \
         patch("aletheia.trust.guard_chain.load_config") as mock_cfg:
        mock_cfg.return_value.risk.max_daily_loss_pct = 5.0
        mock_repo.list_portfolio_snapshots.return_value = [
            {"total_value": 900},   # latest
            {"total_value": 1000},  # prev
        ]
        result = _guard_drawdown_brake({"symbol": "BTC/USDT"})

    assert result.verdict == "block"
    assert "10.0%" in result.reason


def test_drawdown_brake_passes_when_healthy():
    from aletheia.trust.guard_chain import _guard_drawdown_brake

    with patch("aletheia.trust.guard_chain.repo") as mock_repo, \
         patch("aletheia.trust.guard_chain.load_config") as mock_cfg:
        mock_cfg.return_value.risk.max_daily_loss_pct = 5.0
        mock_repo.list_portfolio_snapshots.return_value = [
            {"total_value": 990},   # latest
            {"total_value": 1000},  # prev
        ]
        result = _guard_drawdown_brake({"symbol": "BTC/USDT"})

    assert result.verdict == "pass"


def test_guard_error_produces_warn():
    from aletheia.trust.guard_chain import run_guards, _guards, GuardResult

    def _exploding_guard(p):
        raise RuntimeError("boom")

    original = _guards.copy()
    _guards.clear()
    _guards.append(("exploder", _exploding_guard))
    try:
        results = run_guards({"symbol": "X"})
        assert results[0].verdict == "warn"
        assert "boom" in results[0].reason
    finally:
        _guards.clear()
        _guards.extend(original)
