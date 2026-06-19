"""Tests for Self-Evolution Tier 1: auto guard_candidate generation.

Tests verify that settle_due() correctly generates guard_candidate knowledge items
for outcomes that breach the loss threshold, while respecting dedup and direction.
"""

from unittest.mock import patch, MagicMock, AsyncMock


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
):
    return {
        "id": outcome_id,
        "symbol": symbol,
        "scanner_family": family,
        "direction": direction,
        "forward_return_pct": forward_return,
        "horizon": horizon,
        "entry_price_usd": entry_price,
        "entry_ts": entry_ts,
        "snapshot_id": snapshot_id,
    }


def test_generates_for_long_loss():
    """Long outcome with return < -3% should generate a guard_candidate."""
    row = _make_row(forward_return=-5.0, direction="long")

    with patch("aletheia.universe.outcome_recorder.repo") as mock_repo, \
         patch("aletheia.knowledge.manager.create_guard_candidate") as mock_create:
        mock_repo.guard_candidate_exists_recent.return_value = False

        from aletheia.universe.outcome_recorder import _maybe_generate_guard_candidates
        _maybe_generate_guard_candidates([row])

        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args
        assert "BTC/USDT" in call_kwargs.kwargs["title"]
        assert "momentum" in call_kwargs.kwargs["title"]
        meta = call_kwargs.kwargs["metadata"]
        assert meta["forward_return_pct"] == -5.0
        assert meta["tier"] == 1
        assert meta["auto_generated"] is True


def test_generates_for_short_loss():
    """Short outcome with return > +3% should generate a guard_candidate."""
    row = _make_row(forward_return=+4.5, direction="short")

    with patch("aletheia.universe.outcome_recorder.repo") as mock_repo, \
         patch("aletheia.knowledge.manager.create_guard_candidate") as mock_create:
        mock_repo.guard_candidate_exists_recent.return_value = False

        from aletheia.universe.outcome_recorder import _maybe_generate_guard_candidates
        _maybe_generate_guard_candidates([row])

        mock_create.assert_called_once()


def test_no_candidate_for_long_profit():
    """Long outcome with positive return should NOT generate a candidate."""
    row = _make_row(forward_return=+2.0, direction="long")

    with patch("aletheia.universe.outcome_recorder.repo") as mock_repo, \
         patch("aletheia.knowledge.manager.create_guard_candidate") as mock_create:
        mock_repo.guard_candidate_exists_recent.return_value = False

        from aletheia.universe.outcome_recorder import _maybe_generate_guard_candidates
        _maybe_generate_guard_candidates([row])

        mock_create.assert_not_called()


def test_no_candidate_for_small_long_loss():
    """Long outcome with loss < threshold (-1.5%) should NOT generate a candidate."""
    row = _make_row(forward_return=-1.5, direction="long")

    with patch("aletheia.universe.outcome_recorder.repo") as mock_repo, \
         patch("aletheia.knowledge.manager.create_guard_candidate") as mock_create:
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
         patch("aletheia.knowledge.manager.create_guard_candidate") as mock_create:
        mock_repo.guard_candidate_exists_recent.return_value = False

        from aletheia.universe.outcome_recorder import _maybe_generate_guard_candidates
        _maybe_generate_guard_candidates(rows)

        mock_create.assert_not_called()


def test_dedup_blocks_generation():
    """When dedup check returns True, no candidate should be created."""
    row = _make_row(forward_return=-5.0)

    with patch("aletheia.universe.outcome_recorder.repo") as mock_repo, \
         patch("aletheia.knowledge.manager.create_guard_candidate") as mock_create:
        mock_repo.guard_candidate_exists_recent.return_value = True  # already exists

        from aletheia.universe.outcome_recorder import _maybe_generate_guard_candidates
        _maybe_generate_guard_candidates([row])

        mock_create.assert_not_called()


def test_skips_row_without_scanner_family():
    """Rows with no scanner_family should be silently skipped."""
    row = _make_row(forward_return=-5.0)
    row["scanner_family"] = None

    with patch("aletheia.universe.outcome_recorder.repo") as mock_repo, \
         patch("aletheia.knowledge.manager.create_guard_candidate") as mock_create:
        mock_repo.guard_candidate_exists_recent.return_value = False

        from aletheia.universe.outcome_recorder import _maybe_generate_guard_candidates
        _maybe_generate_guard_candidates([row])

        mock_create.assert_not_called()


def test_create_failure_does_not_raise():
    """A failure in create_guard_candidate must not propagate out."""
    row = _make_row(forward_return=-5.0)

    with patch("aletheia.universe.outcome_recorder.repo") as mock_repo, \
         patch("aletheia.knowledge.manager.create_guard_candidate", side_effect=RuntimeError("db error")):
        mock_repo.guard_candidate_exists_recent.return_value = False

        from aletheia.universe.outcome_recorder import _maybe_generate_guard_candidates
        # Should not raise
        _maybe_generate_guard_candidates([row])


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
         patch("aletheia.knowledge.manager.create_guard_candidate") as mock_create:
        mock_repo.guard_candidate_exists_recent.return_value = False

        from aletheia.universe.outcome_recorder import _maybe_generate_guard_candidates
        _maybe_generate_guard_candidates(rows)

        assert mock_create.call_count == 2
