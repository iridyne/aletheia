"""Tests for Self-Evolution Tier 1: auto guard_candidate generation.

Tests verify that settle_due() correctly generates guard_candidate knowledge items
for outcomes that breach the loss threshold, while respecting dedup and direction.
"""

from unittest.mock import patch, MagicMock, AsyncMock

import pytest

pytestmark = pytest.mark.usefixtures("installed_learning_consumers")


# ---------------------------------------------------------------------------
# Unit tests for _maybe_generate_guard_candidates
# ---------------------------------------------------------------------------

def _make_row(
    symbol="BTC/USDT",
    family="momentum",
    direction="long",
    forward_return=-5.0,
    horizon="4h",
    outcome_id="abc123",
    snapshot_id="snap1",
    entry_price=67000.0,
    entry_ts="2026-06-17T10:00:00Z",
    market_type="spot",
):
    directional = -forward_return if direction == "short" else forward_return
    return {
        "id": outcome_id,
        "symbol": symbol,
        "scanner_family": family,
        "direction": direction,
        "forward_return_pct": forward_return,
        "directional_return_pct": directional,
        "net_return_pct": directional - 0.2,
        "horizon": horizon,
        "entry_price_usd": entry_price,
        "entry_ts": entry_ts,
        "snapshot_id": snapshot_id,
        "market_type": market_type,
    }


def test_generates_for_long_loss():
    """Long outcome with return < -3% should generate a guard_candidate."""
    row = _make_row(forward_return=-5.0, direction="long")

    with patch("aletheia.universe.outcome_recorder.repo") as mock_repo, \
         patch("extensions.knowledge.manager.create_guard_candidate") as mock_create:
        mock_repo.guard_candidate_exists_recent.return_value = False

        from aletheia.universe.outcome_recorder import _maybe_generate_guard_candidates
        _maybe_generate_guard_candidates([row])

        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args
        assert "BTC/USDT" in call_kwargs.kwargs["title"]
        assert "momentum" in call_kwargs.kwargs["title"]
        meta = call_kwargs.kwargs["metadata"]
        assert meta["forward_return_pct"] == -5.0
        assert meta["net_return_pct"] == -5.2
        assert meta["tier"] == 1
        assert meta["auto_generated"] is True


def test_generates_for_short_loss():
    """Short outcome with return > +3% should generate a guard_candidate."""
    row = _make_row(forward_return=+4.5, direction="short")

    with patch("aletheia.universe.outcome_recorder.repo") as mock_repo, \
         patch("extensions.knowledge.manager.create_guard_candidate") as mock_create:
        mock_repo.guard_candidate_exists_recent.return_value = False

        from aletheia.universe.outcome_recorder import _maybe_generate_guard_candidates
        _maybe_generate_guard_candidates([row])

        mock_create.assert_called_once()


def test_no_candidate_for_long_profit():
    """Long outcome with positive return should NOT generate a candidate."""
    row = _make_row(forward_return=+2.0, direction="long")

    with patch("aletheia.universe.outcome_recorder.repo") as mock_repo, \
         patch("extensions.knowledge.manager.create_guard_candidate") as mock_create:
        mock_repo.guard_candidate_exists_recent.return_value = False

        from aletheia.universe.outcome_recorder import _maybe_generate_guard_candidates
        _maybe_generate_guard_candidates([row])

        mock_create.assert_not_called()


def test_no_candidate_for_small_long_loss():
    """Long outcome with loss < threshold (-1.5%) should NOT generate a candidate."""
    row = _make_row(forward_return=-1.5, direction="long")

    with patch("aletheia.universe.outcome_recorder.repo") as mock_repo, \
         patch("extensions.knowledge.manager.create_guard_candidate") as mock_create:
        mock_repo.guard_candidate_exists_recent.return_value = False

        from aletheia.universe.outcome_recorder import _maybe_generate_guard_candidates
        _maybe_generate_guard_candidates([row])

        mock_create.assert_not_called()


def test_skips_non_4h_horizon():
    """Only 4h horizon rows should trigger candidate generation."""
    rows = [
        _make_row(forward_return=-10.0, horizon="15m"),
        _make_row(forward_return=-10.0, horizon="1h"),
        _make_row(forward_return=-10.0, horizon="24h"),
    ]

    with patch("aletheia.universe.outcome_recorder.repo") as mock_repo, \
         patch("extensions.knowledge.manager.create_guard_candidate") as mock_create:
        mock_repo.guard_candidate_exists_recent.return_value = False

        from aletheia.universe.outcome_recorder import _maybe_generate_guard_candidates
        _maybe_generate_guard_candidates(rows)

        mock_create.assert_not_called()


def test_dedup_blocks_generation():
    """When dedup check returns True, no candidate should be created."""
    row = _make_row(forward_return=-5.0)

    with patch("aletheia.universe.outcome_recorder.repo") as mock_repo, \
         patch("extensions.knowledge.manager.create_guard_candidate") as mock_create:
        mock_repo.guard_candidate_exists_recent.return_value = True  # already exists

        from aletheia.universe.outcome_recorder import _maybe_generate_guard_candidates
        _maybe_generate_guard_candidates([row])

        mock_create.assert_not_called()


def test_skips_row_without_scanner_family():
    """Rows with no scanner_family should be silently skipped."""
    row = _make_row(forward_return=-5.0)
    row["scanner_family"] = None

    with patch("aletheia.universe.outcome_recorder.repo") as mock_repo, \
         patch("extensions.knowledge.manager.create_guard_candidate") as mock_create:
        mock_repo.guard_candidate_exists_recent.return_value = False

        from aletheia.universe.outcome_recorder import _maybe_generate_guard_candidates
        _maybe_generate_guard_candidates([row])

        mock_create.assert_not_called()


def test_skips_row_without_known_market_identity():
    row = _make_row(forward_return=-5.0, market_type="crypto")

    with patch("aletheia.universe.outcome_recorder.repo") as mock_repo, \
         patch("extensions.knowledge.manager.create_guard_candidate") as mock_create:
        from aletheia.universe.outcome_recorder import _maybe_generate_guard_candidates
        _maybe_generate_guard_candidates([row])

        mock_repo.guard_candidate_exists_recent.assert_not_called()
        mock_create.assert_not_called()


def test_create_failure_does_not_raise():
    """A failure in create_guard_candidate must not propagate out."""
    row = _make_row(forward_return=-5.0)

    with patch("aletheia.universe.outcome_recorder.repo") as mock_repo, \
         patch("extensions.knowledge.manager.create_guard_candidate", side_effect=RuntimeError("db error")) as mock_create:
        mock_repo.guard_candidate_exists_recent.return_value = False

        from aletheia.universe.outcome_recorder import _maybe_generate_guard_candidates
        # Should not raise
        _maybe_generate_guard_candidates([row])

    assert mock_create.call_count == 1, (
        "the failing create_guard_candidate must actually be attempted (failure is swallowed, not skipped)"
    )


def test_content_includes_key_fields():
    """Generated content should mention symbol, family, return value, and direction."""
    from aletheia.universe.outcome_recorder import _build_guard_candidate_content
    row = _make_row(forward_return=-6.78, direction="long", symbol="ETH/USDT", family="mean_reversion")
    content = _build_guard_candidate_content(row, -6.78, "long")

    assert "ETH/USDT" in content
    assert "mean_reversion" in content
    assert "-6.78" in content
    assert "long" in content
    assert "medium_confidence" in content


def test_multiple_rows_separate_candidates():
    """Multiple bad outcomes for different symbol/family should each generate a candidate."""
    rows = [
        _make_row(symbol="BTC/USDT", family="momentum", forward_return=-5.0),
        _make_row(symbol="ETH/USDT", family="volatility", forward_return=-4.0),
    ]

    with patch("aletheia.universe.outcome_recorder.repo") as mock_repo, \
         patch("extensions.knowledge.manager.create_guard_candidate") as mock_create:
        mock_repo.guard_candidate_exists_recent.return_value = False

        from aletheia.universe.outcome_recorder import _maybe_generate_guard_candidates
        _maybe_generate_guard_candidates(rows)

        assert mock_create.call_count == 2


def test_settle_due_filter_outcomes_wires_settled_losses_into_guard_generation():
    """End-to-end: a real 4h FCO loss settled through the current production
    settlement path (settle_due_filter_outcomes) must still reach
    _maybe_generate_guard_candidates.

    #3701 Batch E removed the retired universe_forward_outcomes settlement loop,
    which was the only caller that collected settled rows and fed them into Tier
    1 guard generation; the replacement filter_candidate_outcomes-only path did
    not carry that wiring over, so a qualifying loss settled without ever
    generating a guard candidate.
    """
    import asyncio
    from datetime import datetime, timedelta, timezone

    from aletheia.storage import repositories as repo
    from aletheia.storage.models import FilterCandidateOutcome
    from aletheia.universe.filter_outcome_recorder import settle_due_filter_outcomes

    entry = datetime.now(timezone.utc) - timedelta(hours=5)
    outcome = FilterCandidateOutcome(
        outcome_candidate_key="guard-wiring-loss",
        venue="okx",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        scanner_family="momentum",
        horizon="4h",
        entry_price_usd=100.0,
        entry_spread_bps=10.0,
        entry_ts=entry.isoformat(),
        due_at=(entry + timedelta(hours=4)).isoformat(),
    )
    repo.insert_filter_candidate_outcomes_batch([outcome])

    async def losing_observation(rows, *, now):
        return {
            row["id"]: {
                "exit_price": 90.0,  # -10% gross, past the -3% net guard threshold
                "range_high": 100.0,
                "range_low": 89.0,
                "price_source": "horizon_ohlcv",
                "price_quality_ok": True,
                "settlement_lag_sec": 30.0,
            }
            for row in rows
        }

    with patch(
        "aletheia.universe.outcome_recorder._batch_fetch_market_observations",
        losing_observation,
    ), patch("extensions.knowledge.manager.create_guard_candidate") as mock_create:
        settled = asyncio.run(settle_due_filter_outcomes(max_rows=1))

    assert settled == 1
    mock_create.assert_called_once()
    assert "BTC/USDT" in mock_create.call_args.kwargs["title"]
