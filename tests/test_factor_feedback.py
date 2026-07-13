"""Unit tests for aletheia.analytics.factor_feedback."""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

from aletheia.analytics.factor_feedback import (
    build_factor_feedback,
    lookup_factor_signal,
    _classify_factor,
    _compute_group_stats,
    _max_consecutive_losses,
)


def _eligible_factor_row(
    *,
    position_id: str,
    symbol: str,
    direction: str,
    realized_pnl: float,
    holding_hours: float,
    close_reason: str,
    closed_at: str,
    confidence_score: int,
    confidence: str,
    late_entry_penalty: float | None,
) -> dict:
    """Build a genuine closed Position and return its enriched attribution row."""
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import (
        UniverseAsset,
        UniverseMember,
        UniverseSnapshot,
    )
    from tests.test_outcome_closure import _insert_closed_position_via_producer

    closed = datetime.fromisoformat(closed_at)
    opened = closed - timedelta(hours=holding_hours)
    asset_id = f"factor-asset-{symbol.lower().replace('/', '-')}"
    if repo.get_universe_asset(asset_id) is None:
        repo.insert_universe_asset(UniverseAsset(
            id=asset_id,
            venue="bitget",
            market_type="usdt_futures",
            symbol=symbol,
        ))
    snapshot_id = f"factor-snapshot-{position_id}"
    member_id = f"factor-member-{position_id}"
    repo.insert_universe_snapshot(UniverseSnapshot(
        id=snapshot_id,
        created_at=opened.isoformat(),
        market_type="crypto",
    ))
    repo.insert_universe_member(UniverseMember(
        id=member_id,
        snapshot_id=snapshot_id,
        asset_id=asset_id,
        symbol=symbol,
        market_type="usdt_futures",
        rank=1,
        total_score=80.0,
        late_entry_penalty=late_entry_penalty,
        scanner_family="momentum",
        scanner_direction=direction,
    ))
    _insert_closed_position_via_producer(
        position_id=position_id,
        symbol=symbol,
        market_type="usdt_futures",
        direction=direction,
        quantity=1.0,
        entry_price=100.0,
        realized_pnl=realized_pnl,
        execution_mode="paper_internal",
        opened_at=opened.isoformat(),
        closed_at=closed.isoformat(),
        close_reason=close_reason,
        universe_snapshot_id=snapshot_id,
        universe_member_id=member_id,
        scanner_family="momentum",
        confidence=confidence,
        confidence_score=confidence_score,
    )
    from aletheia.analytics.trade_attribution import build_trade_attribution_rows

    row = next(
        item
        for item in build_trade_attribution_rows(limit=500)
        if item["position_id"] == position_id
    )
    assert row["universe_member_id"] == member_id
    assert row["late_entry_penalty"] == late_entry_penalty
    assert row["holding_hours"] == holding_hours
    return row


# ---------------------------------------------------------------------------
# _classify_factor
# ---------------------------------------------------------------------------

def test_classify_watch_insufficient_data():
    status, reason = _classify_factor(
        trade_count=2, min_trades=3, expectancy=0.5, win_rate=0.6, profit_factor=2.0
    )
    assert status == "watch"
    assert "insufficient" in reason


def test_classify_blocked_candidate():
    status, reason = _classify_factor(
        trade_count=5, min_trades=3, expectancy=-0.5, win_rate=0.30, profit_factor=0.5
    )
    assert status == "blocked_candidate"
    assert "negative_expectancy" in reason


def test_classify_preferred():
    status, reason = _classify_factor(
        trade_count=10, min_trades=3, expectancy=2.0, win_rate=0.65, profit_factor=2.5
    )
    assert status == "preferred"
    assert "positive_expectancy" in reason


def test_classify_active_fallback():
    status, reason = _classify_factor(
        trade_count=5, min_trades=3, expectancy=0.1, win_rate=0.50, profit_factor=1.0
    )
    assert status == "active"


# ---------------------------------------------------------------------------
# _max_consecutive_losses
# ---------------------------------------------------------------------------

def test_max_consecutive_losses():
    rows = [
        {"realized_pnl": 1.0, "closed_at": "2026-06-14T01:00:00"},
        {"realized_pnl": -1.0, "closed_at": "2026-06-14T02:00:00"},
        {"realized_pnl": -2.0, "closed_at": "2026-06-14T03:00:00"},
        {"realized_pnl": -0.5, "closed_at": "2026-06-14T04:00:00"},
        {"realized_pnl": 3.0, "closed_at": "2026-06-14T05:00:00"},
    ]
    assert _max_consecutive_losses(rows) == 3


def test_max_consecutive_losses_none():
    rows = [
        {"realized_pnl": 1.0, "closed_at": "2026-06-14T01:00:00"},
        {"realized_pnl": 2.0, "closed_at": "2026-06-14T02:00:00"},
    ]
    assert _max_consecutive_losses(rows) == 0


# ---------------------------------------------------------------------------
# _compute_group_stats
# ---------------------------------------------------------------------------

def test_compute_group_stats_basic():
    rows = [
        {"realized_pnl": 10.0, "holding_hours": 4.0, "close_reason": "take_profit", "closed_at": "2026-06-14T01:00:00"},
        {"realized_pnl": -5.0, "holding_hours": 2.0, "close_reason": "stop_loss", "closed_at": "2026-06-14T02:00:00"},
        {"realized_pnl": 8.0, "holding_hours": 6.0, "close_reason": "take_profit", "closed_at": "2026-06-14T03:00:00"},
        {"realized_pnl": -3.0, "holding_hours": 1.0, "close_reason": "stop_loss", "closed_at": "2026-06-14T04:00:00"},
    ]
    stats = _compute_group_stats("test_key", "exact", rows, min_trades=3)
    assert stats["trade_count"] == 4
    assert stats["win_count"] == 2
    assert stats["loss_count"] == 2
    assert stats["win_rate"] == 0.5
    assert stats["total_pnl"] == 10.0
    assert stats["most_common_close_reason"] in ("take_profit", "stop_loss")


# ---------------------------------------------------------------------------
# lookup_factor_signal
# ---------------------------------------------------------------------------

def test_lookup_factor_signal_no_history():
    """Missing factor history returns watch status."""
    with patch("aletheia.analytics.factor_feedback.build_trade_attribution_rows", return_value=[]):
        signal = lookup_factor_signal({
            "scanner_family": "momentum",
            "symbol": "BTC/USDT",
            "direction": "long",
            "confidence_score": 75,
            "confidence": "high",
            "late_entry_penalty": None,
        })
    assert signal["status"] == "watch"
    assert signal["trade_count"] == 0
    assert signal["reason"] == "no_history"


def test_lookup_factor_signal_with_matching_history():
    """Matching history returns computed status."""
    fake_rows = [
        _eligible_factor_row(
            position_id="p1", symbol="BTC/USDT", direction="long",
            realized_pnl=5.0, holding_hours=4.0, close_reason="take_profit",
            closed_at="2026-07-12T08:01:00+00:00", confidence_score=75,
            confidence="high", late_entry_penalty=None,
        ),
        _eligible_factor_row(
            position_id="p2", symbol="BTC/USDT", direction="long",
            realized_pnl=3.0, holding_hours=2.0, close_reason="take_profit",
            closed_at="2026-07-12T08:02:00+00:00", confidence_score=75,
            confidence="high", late_entry_penalty=None,
        ),
        _eligible_factor_row(
            position_id="p3", symbol="BTC/USDT", direction="long",
            realized_pnl=-1.0, holding_hours=1.0, close_reason="stop_loss",
            closed_at="2026-07-12T08:03:00+00:00", confidence_score=75,
            confidence="high", late_entry_penalty=None,
        ),
    ]
    with patch("aletheia.analytics.factor_feedback.build_trade_attribution_rows", return_value=fake_rows):
        signal = lookup_factor_signal({
            "scanner_family": "momentum",
            "symbol": "BTC/USDT",
            "direction": "long",
            "confidence_score": 75,
            "confidence": "high",
            "late_entry_penalty": None,
        })
    assert signal["trade_count"] == 3
    assert signal["observed_status"] == "active"
    assert signal["status"] == "watch"  # retrospective history has no authority


# ---------------------------------------------------------------------------
# build_factor_feedback
# ---------------------------------------------------------------------------

def test_build_factor_feedback_empty():
    with patch("aletheia.analytics.factor_feedback.build_trade_attribution_rows", return_value=[]):
        result = build_factor_feedback(limit=10)
    assert result == []


def test_build_factor_feedback_groups():
    fake_rows = [
        _eligible_factor_row(
            position_id="g1", symbol="BTC/USDT", direction="long",
            realized_pnl=10.0, holding_hours=4.0, close_reason="take_profit",
            closed_at="2026-07-12T08:01:00+00:00", confidence_score=75,
            confidence="high", late_entry_penalty=5.0,
        ),
        _eligible_factor_row(
            position_id="g2", symbol="ETH/USDT", direction="short",
            realized_pnl=-2.0, holding_hours=1.0, close_reason="stop_loss",
            closed_at="2026-07-12T08:02:00+00:00", confidence_score=60,
            confidence="medium", late_entry_penalty=0.0,
        ),
    ]
    with patch("aletheia.analytics.factor_feedback.build_trade_attribution_rows", return_value=fake_rows):
        result = build_factor_feedback(limit=10, min_trades=1)

    group_types = {r["group_type"] for r in result}
    assert "exact" in group_types
    assert "scanner" in group_types
    assert "scanner_dir" in group_types
    assert "symbol" in group_types
    assert "late_entry" in group_types
