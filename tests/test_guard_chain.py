"""Tests for aletheia.trust.guard_chain."""

import pytest
from unittest.mock import patch


def test_run_guards_all_pass():
    from aletheia.trust.guard_chain import run_guards, summarize

    proposal = {"symbol": "BTC/USDT", "direction": "long", "conviction": 70}
    with patch("aletheia.trust.guard_chain.repo") as mock_repo:
        mock_repo.list_positions.return_value = []
        mock_repo.list_portfolio_snapshots.return_value = []
        results = run_guards(proposal)

    assert len(results) == 4
    summary = summarize(results)
    assert summary["blocks"] == 0


def test_exposure_concentration_warns():
    from aletheia.trust.guard_chain import _guard_exposure_concentration

    with patch("aletheia.trust.guard_chain.repo") as mock_repo:
        mock_repo.list_positions.return_value = [
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
    with patch("aletheia.trust.guard_chain.repo") as mock_repo:
        mock_repo.list_positions.return_value = positions
        result = _guard_recent_loss_streak({"strategy_id": "s1"})

    assert result.verdict == "warn"
    assert "consecutive losses" in result.reason


def test_correlation_cluster_warns():
    from aletheia.trust.guard_chain import _guard_correlation_cluster

    with patch("aletheia.trust.guard_chain.repo") as mock_repo:
        mock_repo.list_positions.return_value = [
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
