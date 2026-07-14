"""Tests for market_observer parallel-prefetch helpers and signal-strength ordering.

Covers:
- _signal_score() normalisation
- Processing-order sort (signal-first)
- Round-robin advance uses original instrument order, not sorted order
- _prefetch_symbol() error isolation (one failing symbol doesn't kill others)
"""

import pytest


def _prepare_db(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sqlite
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    sqlite.reset_db()
    run_migrations()


# ---------------------------------------------------------------------------
# _signal_score()
# ---------------------------------------------------------------------------

def test_signal_score_no_signal():
    from aletheia.agents.market_observer import _signal_score

    pf = {"passed": False, "rsi": None, "atr_pct": None, "keyword_hits": [], "matched_rules": []}
    assert _signal_score(pf) == 0.0


def test_signal_score_rsi_deviation():
    from aletheia.agents.market_observer import _signal_score, _PREFILTER_RSI_DEVIATION

    rsi = 35.0  # deviation = |35 - 50| = 15
    pf = {"passed": True, "rsi": rsi, "atr_pct": None, "keyword_hits": [], "matched_rules": ["rsi_deviation"]}
    score = _signal_score(pf)
    assert abs(score - 15.0 / _PREFILTER_RSI_DEVIATION) < 1e-9
    assert score >= 1.0  # passed threshold


def test_signal_score_atr_pct():
    from aletheia.agents.market_observer import _signal_score, _PREFILTER_ATR_PCT

    pf = {"passed": True, "rsi": None, "atr_pct": 1.6, "keyword_hits": [], "matched_rules": ["atr_pct"]}
    score = _signal_score(pf)
    assert abs(score - 1.6 / _PREFILTER_ATR_PCT) < 1e-9


def test_signal_score_keyword_only():
    from aletheia.agents.market_observer import _signal_score

    pf = {"passed": True, "rsi": None, "atr_pct": None, "keyword_hits": ["breakout"], "matched_rules": ["keyword"]}
    assert _signal_score(pf) == 1.0


def test_signal_score_takes_max_of_dimensions():
    from aletheia.agents.market_observer import _signal_score, _PREFILTER_RSI_DEVIATION, _PREFILTER_ATR_PCT

    # RSI deviation = 14 → 14/12 ≈ 1.17; ATR% = 1.6 → 1.6/0.8 = 2.0 — ATR wins
    pf = {
        "passed": True, "rsi": 36.0, "atr_pct": 1.6,
        "keyword_hits": [], "matched_rules": ["rsi_deviation", "atr_pct"],
    }
    score = _signal_score(pf)
    expected = max(14.0 / _PREFILTER_RSI_DEVIATION, 1.6 / _PREFILTER_ATR_PCT)
    assert abs(score - expected) < 1e-9


def test_signal_score_below_threshold():
    from aletheia.agents.market_observer import _signal_score

    # RSI close to 50 → no deviation rule fires; keyword also absent → score < 1.0
    pf = {"passed": False, "rsi": 52.0, "atr_pct": 0.3, "keyword_hits": [], "matched_rules": []}
    assert _signal_score(pf) < 1.0


# ---------------------------------------------------------------------------
# Signal-strength ordering
# ---------------------------------------------------------------------------

def test_signal_ordering_passed_before_failed():
    """Instruments with signal_score >= 1.0 sort before those with score < 1.0."""
    from aletheia.agents.market_observer import _signal_score

    scores = {
        "A": _signal_score({"passed": False, "rsi": 51.0, "atr_pct": 0.2, "keyword_hits": [], "matched_rules": []}),
        "B": _signal_score({"passed": True, "rsi": 35.0, "atr_pct": None, "keyword_hits": [], "matched_rules": ["rsi_deviation"]}),
        "C": _signal_score({"passed": True, "rsi": None, "atr_pct": 1.6, "keyword_hits": [], "matched_rules": ["atr_pct"]}),
    }
    instruments = ["A", "B", "C"]
    sorted_order = sorted(instruments, key=lambda i: scores[i], reverse=True)
    # A (score=0) must be last
    assert sorted_order[-1] == "A"
    # B and C (both ≥ 1.0) must be first two (order between them depends on scores)
    assert set(sorted_order[:2]) == {"B", "C"}


def test_signal_ordering_higher_score_first():
    """Among passed instruments, higher score comes first."""
    from aletheia.agents.market_observer import _signal_score

    # A: RSI deviation 30 → score = 30/12 = 2.5
    # B: RSI deviation 13 → score = 13/12 ≈ 1.08
    scores = {
        "A": _signal_score({"passed": True, "rsi": 20.0, "atr_pct": None, "keyword_hits": [], "matched_rules": ["rsi_deviation"]}),
        "B": _signal_score({"passed": True, "rsi": 37.0, "atr_pct": None, "keyword_hits": [], "matched_rules": ["rsi_deviation"]}),
    }
    assert scores["A"] > scores["B"]
    instruments = ["B", "A"]
    sorted_order = sorted(instruments, key=lambda i: scores[i], reverse=True)
    assert sorted_order == ["A", "B"]


# ---------------------------------------------------------------------------
# Round-robin advance preserves original instrument order
# ---------------------------------------------------------------------------

def test_round_robin_advance_uses_original_order_not_sorted_order(tmp_path, monkeypatch):
    """When processing in signal-sorted order, advance_after must still receive the
    original RR-ordered list so canonical successor computation stays stable.

    Scenario:
      RR order:        [A, B, C]
      Signal scores:   C=2.5, A=1.2, B=0.0
      Processing:      [C, A, B]  (sorted by score desc)

    After processing C with advance_after([A,B,C], "C"):
      canonical=[A,B,C], C is index 2, successor = A → next_key = "A"
    After processing A with advance_after([A,B,C], "A"):
      canonical=[A,B,C], A is index 0, successor = B → next_key = "B"
    After processing B with advance_after([A,B,C], "B"):
      canonical=[A,B,C], B is index 1, successor = C → next_key = "C"
    Final next_key = "C"
    """
    _prepare_db(tmp_path, monkeypatch)
    from aletheia.agents.round_robin import DurableRoundRobin

    rr = DurableRoundRobin("test.signal-sort.rr")
    original_order = ["A", "B", "C"]
    processing_order = ["C", "A", "B"]  # signal-sorted

    for item in processing_order:
        rr.advance_after(original_order, completed_key=item, key=str)

    assert rr.next_key() == "C"


def test_round_robin_advance_partial_run(tmp_path, monkeypatch):
    """Budget-exceeded mid-run: only two of three processed.

    RR order [A, B, C], signal sort [C, A, B], budget hit after [C, A].
    advance_after calls: C → next=A, then A → next=B.
    Next run starts at B (picks up the skipped one).
    """
    _prepare_db(tmp_path, monkeypatch)
    from aletheia.agents.round_robin import DurableRoundRobin

    rr = DurableRoundRobin("test.partial-run.rr")
    original_order = ["A", "B", "C"]

    # Only C and A were processed (B was skipped by budget)
    rr.advance_after(original_order, completed_key="C", key=str)
    rr.advance_after(original_order, completed_key="A", key=str)

    assert rr.next_key() == "B"


# ---------------------------------------------------------------------------
# _prefetch_symbol() error isolation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_prefetch_failure_isolates_to_single_symbol(tmp_path, monkeypatch):
    """A build_market_context() exception for one symbol produces a failed _SymbolPrefetch
    without affecting coroutines for other symbols.

    Verified via asyncio.gather(return_exceptions=True) which never propagates
    individual exceptions to the caller.
    """
    import asyncio
    from unittest.mock import patch

    _prepare_db(tmp_path, monkeypatch)
    from aletheia.agents.market_observer import _SymbolPrefetch, _DbCache

    # Build a minimal _DbCache (empty — no DB data needed for this test)
    empty_cache = _DbCache(
        perp_structures={}, funding_hl={}, funding_hl_pred={}, news={}, onchain_rows=[],
    )

    # Fake InstrumentId-like objects
    class FakeInstrument:
        def __init__(self, sym):
            self.symbol = sym
            self.market_type = "spot"

    call_count = {"n": 0}

    async def mock_build_market_context(symbols, *, market_type, exchange, provider, include_glossary):
        sym = symbols[0]
        call_count["n"] += 1
        if sym == "FAIL":
            raise RuntimeError("exchange timeout")
        return (f"## ctx for {sym}", {"fallback": False, "errors": []})

    with patch("aletheia.trading.market_context.build_market_context", side_effect=mock_build_market_context):
        # Patch away the DB-enrichment imports so only the exchange call matters
        with patch("aletheia.data.context_builder.build_market_context_from_db", side_effect=Exception("skip")):
            from aletheia.agents.market_observer import _prefetch_symbol

            coros = [
                _prefetch_symbol(
                    FakeInstrument(sym),
                    venue="bybit",
                    provider=None,
                    prediction_context_enabled=False,
                    prediction_context_cutoff=None,
                    db_cache=empty_cache,
                )
                for sym in ["OK1", "FAIL", "OK2"]
            ]
            results = await asyncio.gather(*coros, return_exceptions=True)

    # No exception propagated — all three are _SymbolPrefetch instances
    assert all(isinstance(r, _SymbolPrefetch) for r in results)

    ok1, fail, ok2 = results
    assert not ok1.failed
    assert ok1.context_str.startswith("## ctx for OK1")

    assert fail.failed
    assert fail.signal_score == 0.0

    assert not ok2.failed
    assert ok2.context_str.startswith("## ctx for OK2")

    # All three symbols were attempted (no short-circuit)
    assert call_count["n"] == 3


@pytest.mark.asyncio
async def test_failed_prefetch_does_not_reach_llm(tmp_path, monkeypatch):
    """When _prefetched.failed=True, _analyze_symbol() returns early without LLM call."""
    import asyncio
    from unittest.mock import AsyncMock, patch

    _prepare_db(tmp_path, monkeypatch)
    from aletheia.agents.market_observer import _analyze_symbol, _SymbolPrefetch

    failed_prefetch = _SymbolPrefetch(
        context_str="", debug={},
        pre_filter={"passed": False, "rsi": None, "atr_pct": None, "keyword_hits": [], "matched_rules": []},
        signal_score=0.0,
        funding_rate_val=None, open_interest_val=None, oi_delta_val=None,
        failed=True,
    )

    llm_called = {"n": 0}

    async def mock_llm(*args, **kwargs):
        llm_called["n"] += 1
        return ({}, None)

    with patch("aletheia.agents.helpers.llm_structured", side_effect=mock_llm):
        opp_id, err = await _analyze_symbol(
            symbol="BTC",
            market_type="spot",
            permission_mode="Observe",
            run_id=None,
            linked_strategy_id=None,
            memory_block="",
            _prefetched=failed_prefetch,
        )

    assert opp_id is None
    assert err is not None and "prefetch_failed" in err
    assert llm_called["n"] == 0  # LLM was never called
