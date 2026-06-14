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
    patches["aletheia.agents.paper_trader_ranker.load_config"] = MagicMock(return_value=_mock_config())
    patches["aletheia.agents.paper_trader_ranker.list_opportunities"] = MagicMock(return_value=opps)
    patches["aletheia.agents.paper_trader_ranker.list_strategies"] = MagicMock(return_value=strategies or [])
    patches["aletheia.agents.paper_trader_ranker.get_strategy"] = MagicMock(return_value=None)
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

def test_non_crypto_excluded():
    from aletheia.agents.paper_trader_ranker import build_paper_candidates

    opps = [_make_opp(asset_class="equity")]
    with _patch_deps(opps):
        candidates = build_paper_candidates(limit=50)
    assert len(candidates) == 0


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


def test_blocked_factor_skipped():
    from aletheia.agents.paper_trader_ranker import build_paper_candidates

    opps = [_make_opp()]
    with _patch_deps(opps, factor_status="blocked_candidate"):
        candidates = build_paper_candidates(limit=50)
    assert len(candidates) == 1
    assert "blocked_factor" in candidates[0]["skip_reason"]


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
