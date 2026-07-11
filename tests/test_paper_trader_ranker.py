"""Unit tests for aletheia.agents.paper_trader_ranker."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest


def _make_opp(
    *,
    oid="opp-1",
    symbol="BTC/USDT",
    asset_class="crypto",
    status="open",
    direction_bias="long",
    confidence="high",
    confidence_score=80,
    execution_eligible=True,
    linked_strategy_id=None,
    universe_member_id=None,
    scanner_family="momentum",
    source_agent="market_observer",
):
    return {
        "id": oid,
        "symbol": symbol,
        "asset_class": asset_class,
        "status": status,
        "direction_bias": direction_bias,
        "confidence": confidence,
        "confidence_score": confidence_score,
        "execution_eligible": execution_eligible,
        "linked_strategy_id": linked_strategy_id,
        "universe_member_id": universe_member_id,
        "scanner_family": scanner_family,
        "source_agent": source_agent,
    }


def _mock_config():
    cfg = MagicMock()
    cfg.risk.symbol_filter_mode = "whitelist"
    cfg.trading.min_confidence_to_trade = "medium"
    return cfg


def _patch_deps(opps, strategies=None, universe_member=None, factor_status="watch"):
    """Return a context manager that patches all ranker dependencies."""
    patches = {}
    # Position C (2026-07-02): the ranker no longer resolves StrategyRecords, so
    # list_strategies / get_strategy are no longer module attributes to patch.
    patches["aletheia.agents.paper_trader_ranker.load_config"] = MagicMock(return_value=_mock_config())
    patches["aletheia.agents.paper_trader_ranker.list_opportunities"] = MagicMock(return_value=opps)
    patches["aletheia.agents.paper_trader_ranker.get_universe_member"] = MagicMock(return_value=universe_member)
    patches["aletheia.agents.paper_trader_ranker.tradable_symbols_from_config"] = MagicMock(return_value=["BTC/USDT", "ETH/USDT"])
    patches["aletheia.agents.paper_trader_ranker.lookup_factor_signal"] = MagicMock(return_value={
        "factor_key": "test", "status": factor_status,
        "expectancy": 0.5, "win_rate": 0.6, "trade_count": 5, "reason": "test",
    })

    class MultiPatch:
        def __enter__(self):
            self._patchers = [patch(k, v) for k, v in patches.items()]
            for p in self._patchers:
                p.start()
            return self

        def __exit__(self, *args):
            for p in self._patchers:
                p.stop()

    return MultiPatch()


# ---------------------------------------------------------------------------
# Filter tests
# ---------------------------------------------------------------------------

def test_equity_shadow_tracked():
    """Equity opportunities are scored but not executed (equity_shadow_track skip_reason)."""
    from aletheia.agents.paper_trader_ranker import build_paper_candidates, select_best_candidate

    opps = [_make_opp(asset_class="equity", direction_bias="long", confidence="high")]
    with _patch_deps(opps):
        candidates = build_paper_candidates(limit=50)
    assert len(candidates) == 1
    assert candidates[0]["skip_reason"] == "equity_shadow_track"
    assert select_best_candidate(candidates) is None


def test_neutral_bias_skipped():
    from aletheia.agents.paper_trader_ranker import build_paper_candidates

    opps = [_make_opp(direction_bias="neutral")]
    with _patch_deps(opps):
        candidates = build_paper_candidates(limit=50)
    assert len(candidates) == 1
    assert candidates[0]["skip_reason"] == "neutral_bias"


def test_low_confidence_skipped():
    from aletheia.agents.paper_trader_ranker import build_paper_candidates

    opps = [_make_opp(confidence="speculative", confidence_score=20)]
    with _patch_deps(opps):
        candidates = build_paper_candidates(limit=50)
    assert len(candidates) == 1
    assert "confidence_below" in candidates[0]["skip_reason"]


def test_expired_universe_member_skipped():
    from aletheia.agents.paper_trader_ranker import build_paper_candidates

    opps = [_make_opp(universe_member_id="um-1")]
    expired_member = {
        "id": "um-1", "execution_eligible": True,
        "expires_at": "2020-01-01T00:00:00+00:00",  # long expired
        "rank": 1, "total_score": 80.0, "tradability_score": 70.0,
        "late_entry_penalty": 0, "crowding_risk": 0, "risk_flags": "[]",
    }
    with _patch_deps(opps, universe_member=expired_member):
        candidates = build_paper_candidates(limit=50)
    assert len(candidates) == 1
    assert candidates[0]["skip_reason"] == "ttl_expired"


def test_blocked_factor_diagnostic_is_neutral_without_authority():
    """Retrospective blocked_candidate does not change score without prospective authority."""
    from aletheia.agents.paper_trader_ranker import build_paper_candidates

    opps = [_make_opp()]

    # A diagnostic status without authority must retain the static baseline.
    with _patch_deps(opps, factor_status="blocked_candidate"):
        blocked_candidates = build_paper_candidates(limit=50)
    assert len(blocked_candidates) == 1
    assert blocked_candidates[0]["skip_reason"] is None  # no hard-skip

    # Compare with "active" status: neither un-authorized label changes score.
    with _patch_deps(opps, factor_status="active"):
        active_candidates = build_paper_candidates(limit=50)
    assert len(active_candidates) == 1
    assert blocked_candidates[0]["trade_score"] == active_candidates[0]["trade_score"]


# ---------------------------------------------------------------------------
# Scoring tests
# ---------------------------------------------------------------------------

def test_highest_trade_score_selected():
    from aletheia.agents.paper_trader_ranker import build_paper_candidates, select_best_candidate

    opps = [
        _make_opp(oid="opp-1", confidence_score=60),
        _make_opp(oid="opp-2", confidence_score=90),
        _make_opp(oid="opp-3", confidence_score=40),
    ]
    with _patch_deps(opps, factor_status="active"):
        candidates = build_paper_candidates(limit=50)
        best = select_best_candidate(candidates)

    assert best is not None
    assert best["opportunity_id"] == "opp-2"
    assert best["trade_score"] > candidates[-1]["trade_score"]


def test_missing_factor_history_does_not_block():
    from aletheia.agents.paper_trader_ranker import build_paper_candidates, select_best_candidate

    opps = [_make_opp()]
    with _patch_deps(opps, factor_status="watch"):
        candidates = build_paper_candidates(limit=50)
        best = select_best_candidate(candidates)

    assert best is not None
    assert best["skip_reason"] is None


def test_select_best_candidate_none_when_all_skipped():
    from aletheia.agents.paper_trader_ranker import select_best_candidate

    candidates = [
        {"skip_reason": "neutral_bias", "trade_score": -999},
        {"skip_reason": "blocked_factor:test", "trade_score": -999},
    ]
    assert select_best_candidate(candidates) is None


def test_execution_not_eligible_skipped():
    from aletheia.agents.paper_trader_ranker import build_paper_candidates

    opps = [_make_opp(execution_eligible=False)]
    with _patch_deps(opps):
        candidates = build_paper_candidates(limit=50)
    assert len(candidates) == 1
    assert candidates[0]["skip_reason"] == "execution_eligible=False"


def test_candidate_builder_overfetches_past_skipped_front_page():
    from aletheia.agents.paper_trader_ranker import build_paper_candidates, select_best_candidate

    opps = [
        _make_opp(
            oid=f"skipped-{i}",
            symbol="ETH/USDT",
            confidence="speculative",
            confidence_score=20,
        )
        for i in range(10)
    ]
    opps.append(_make_opp(oid="executable-1", symbol="BTC/USDT", confidence="high", confidence_score=90))

    def list_page(*, limit):
        return opps[:limit]

    cfg = _mock_config()
    cfg.exchange.paper_execution_mode = "bitget_demo"

    with patch("aletheia.agents.paper_trader_ranker.load_config", return_value=cfg), \
         patch("aletheia.agents.paper_trader_ranker.list_opportunities", side_effect=list_page) as list_mock, \
         patch("aletheia.agents.paper_trader_ranker.get_universe_member", return_value=None), \
         patch("aletheia.agents.paper_trader_ranker.tradable_symbols_from_config", return_value=["BTC/USDT", "ETH/USDT"]), \
         patch("aletheia.agents.paper_trader_ranker.lookup_factor_signal", return_value={
             "factor_key": "test", "status": "active",
             "expectancy": 0.5, "win_rate": 0.6, "trade_count": 5, "reason": "test",
         }), \
         patch("aletheia.agents.paper_trader_ranker._evaluate_guards_safe", return_value=None):
        candidates = build_paper_candidates(limit=10)

    best = select_best_candidate(candidates)

    assert list_mock.call_args.kwargs["limit"] > 10
    assert len(candidates) <= 10
    assert best is not None
    assert best["opportunity_id"] == "executable-1"


# ---------------------------------------------------------------------------
# Regime scoring (Position C: strategy-evidence gate/scoring removed — the
# rules engine is deleted. Regime affinity survives as a scanner-family signal.)
# ---------------------------------------------------------------------------

def test_regime_scoring_is_direction_aware():
    """Bear regime should reward shorts and penalize longs; bull should do the opposite."""
    from unittest.mock import patch
    from aletheia.agents.paper_trader_ranker import _compute_trade_score
    from aletheia.regime.detector import MarketRegime

    bear = MarketRegime(state="bear", confidence=0.7, long_bias=0.25, short_bias=0.75)
    bull = MarketRegime(state="bull", confidence=0.7, long_bias=0.75, short_bias=0.25)
    factor_signal = {"status": "active"}
    opp = {"confidence_score": 80}

    with patch("aletheia.agents.paper_trader_ranker.get_current_regime", return_value=bear):
        score_bear_short = _compute_trade_score(opp, None, factor_signal, "high", None, direction="short")
        score_bear_long = _compute_trade_score(opp, None, factor_signal, "high", None, direction="long")

    with patch("aletheia.agents.paper_trader_ranker.get_current_regime", return_value=bull):
        score_bull_long = _compute_trade_score(opp, None, factor_signal, "high", None, direction="long")
        score_bull_short = _compute_trade_score(opp, None, factor_signal, "high", None, direction="short")

    # Bear: shorts score higher than longs (regime smooth: gap < old discrete 35pt but direction preserved)
    assert score_bear_short > score_bear_long
    assert score_bear_short - score_bear_long > 10  # meaningful gap, not just noise

    # Bull: longs score higher than shorts (gap < old discrete 30pt but direction preserved)
    assert score_bull_long > score_bull_short
    assert score_bull_long - score_bull_short > 10
