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

def test_completed_prefix_cursor_consumes_each_attempt_once(tmp_path, monkeypatch):
    """A full out-of-order run must not re-advance over completed identities.

    The mandatory prefix A runs first, then signal priority chooses C before B.
    Once B closes the canonical gap, the cursor may advance through B and the
    already-completed C, but it must stop at A rather than wrap over A again.
    """
    _prepare_db(tmp_path, monkeypatch)
    from aletheia.agents.market_observer import _advance_completed_round_robin_prefix
    from aletheia.agents.round_robin import DurableRoundRobin

    rr = DurableRoundRobin("test.signal-sort.rr")
    original_order = ["A", "B", "C"]
    processing_order = ["A", "C", "B"]
    completed: set[str] = set()
    next_index = 0

    for item in processing_order:
        completed.add(item)
        next_index = _advance_completed_round_robin_prefix(
            rr,
            original_order,
            completed_keys=completed,
            next_unprocessed_index=next_index,
            key=str,
        )

    assert rr.next_key() == "A"
    assert completed == set()


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


def test_signal_priority_keeps_durable_round_robin_item_boundedly_reachable(
    tmp_path, monkeypatch
):
    """A permanently low-score item must not starve behind the same high-score pair.

    The per-cycle capacity of two models the observer hitting its wall-clock budget
    after two LLM attempts.  C and A stay highest-scoring on every cycle; durable
    round-robin identity must still force B to the front on the next cycle.
    """
    _prepare_db(tmp_path, monkeypatch)
    from aletheia.agents.market_observer import (
        _advance_completed_round_robin_prefix,
        _fair_signal_order,
    )
    from aletheia.agents.round_robin import DurableRoundRobin, rotate_from_next

    canonical = ["A", "B", "C"]
    scores = {"A": 2.0, "B": 0.0, "C": 3.0}
    rr = DurableRoundRobin("test.signal-sort.bounded-fairness")
    processed_by_cycle: list[list[str]] = []

    for _cycle in range(len(canonical)):
        rotated = rotate_from_next(
            canonical, next_key=rr.next_key(), key=str,
        )
        ordered = _fair_signal_order(
            rotated, score=lambda item: scores[item], key=str,
        )
        processed = ordered[:2]
        processed_by_cycle.append(processed)

        # Use the same cancellation-safe cursor primitive as run_market_observer.
        _advance_completed_round_robin_prefix(
            rr,
            rotated,
            completed_keys=set(processed),
            next_unprocessed_index=0,
            key=str,
        )

    assert processed_by_cycle[0] == ["A", "C"]
    assert processed_by_cycle[1][0] == "B"
    assert set().union(*map(set, processed_by_cycle)) == set(canonical)


@pytest.mark.asyncio
async def test_prefetch_wait_uses_only_remaining_run_budget(monkeypatch):
    """Parallel prefetch cannot start a fresh full observer budget."""
    from aletheia.agents import market_observer as mo

    captured: dict[str, float] = {}

    async def _value():
        return "ready"

    async def fake_wait_for(awaitable, timeout):
        captured["timeout"] = timeout
        return await awaitable

    monkeypatch.setattr(mo, "_monotonic", lambda: 140.0)
    monkeypatch.setattr(mo.asyncio, "wait_for", fake_wait_for)

    result = await mo._gather_prefetch_with_deadline([_value()], deadline=500.0)

    assert result == ["ready"]
    assert captured["timeout"] == pytest.approx(360.0)


@pytest.mark.asyncio
async def test_prefetch_time_is_charged_to_same_serial_loop_deadline(
    tmp_path, monkeypatch
):
    """A slow prefetch leaves only its true remainder for serial LLM work.

    With 450 of 460 seconds consumed before the loop, only one 15-second attempt
    may start.  Resetting the deadline after prefetch would incorrectly process all
    three instruments and could overrun the scheduler's 500-second timeout.
    """
    from types import SimpleNamespace

    _prepare_db(tmp_path, monkeypatch)
    from aletheia.agents import market_observer as mo

    class _Instrument:
        def __init__(self, symbol):
            self.symbol = symbol
            self.market_type = "spot"

        def __str__(self):
            return f"{self.symbol}:spot"

    instruments = [_Instrument(symbol) for symbol in ("A", "B", "C")]
    empty_cache = mo._DbCache(
        perp_structures={}, funding_hl={}, funding_hl_pred={}, news={}, onchain_rows=[],
    )
    empty_pf = {
        "passed": True,
        "rsi": 20.0,
        "atr_pct": None,
        "keyword_hits": [],
        "matched_rules": ["rsi_deviation"],
    }
    clock = {"now": 100.0}
    captured: dict[str, float] = {}
    analyzed: list[str] = []

    monkeypatch.setattr(mo, "_monotonic", lambda: clock["now"])
    monkeypatch.setattr(mo, "is_tool_permitted", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        mo,
        "load_config",
        lambda: SimpleNamespace(
            universe=SimpleNamespace(enabled=False, max_candidates=3),
            market_data=SimpleNamespace(
                prediction_enabled=False, ccxt_exchange="fixture",
            ),
            observer=SimpleNamespace(force_full_scan=False),
        ),
    )
    monkeypatch.setattr(mo, "observed_instruments_from_config", lambda _cfg: instruments)
    monkeypatch.setattr(mo, "list_strategies", lambda limit=50: [])
    monkeypatch.setattr(mo, "log_tool", lambda *_args, **_kwargs: None)

    def fake_batch(_symbols):
        clock["now"] += 20.0
        return empty_cache

    async def fake_prefetch(instrument, **_kwargs):
        return mo._SymbolPrefetch(
            context_str=instrument.symbol,
            debug={},
            pre_filter=empty_pf,
            signal_score={"A": 2.0, "B": 0.0, "C": 3.0}[instrument.symbol],
            funding_rate_val=None,
            open_interest_val=None,
            oi_delta_val=None,
        )

    async def fake_gather(coros, *, deadline):
        captured["deadline"] = deadline
        captured["remaining_before_prefetch"] = deadline - clock["now"]
        results = [await coro for coro in coros]
        clock["now"] += 430.0
        return results

    async def fake_analyze(*, symbol, **_kwargs):
        analyzed.append(symbol)
        clock["now"] += 15.0
        return None, f"pre_filter_skip:{symbol}"

    monkeypatch.setattr(mo, "_batch_prefetch_db", fake_batch)
    monkeypatch.setattr(mo, "_prefetch_symbol", fake_prefetch)
    monkeypatch.setattr(mo, "_gather_prefetch_with_deadline", fake_gather)
    monkeypatch.setattr(mo, "_analyze_symbol", fake_analyze)
    monkeypatch.setattr(
        "aletheia.knowledge.router.build_advisory_context",
        lambda **_kwargs: "",
    )
    monkeypatch.setattr(
        "aletheia.analytics.insight_synthesizer.get_latest_memo",
        lambda: None,
    )
    monkeypatch.setattr(
        "aletheia.learning.control.learning_influence_enabled",
        lambda: False,
    )

    result = await mo.run_market_observer(permission_mode="Observe", run_id="budget-test")

    assert captured["deadline"] == pytest.approx(560.0)
    assert captured["remaining_before_prefetch"] == pytest.approx(440.0)
    assert analyzed == ["A"]
    assert result["errors"] == [
        "loop_budget_exceeded: processed 1/3, skipped 2 "
        "(wall-clock cap 460s < 500s job timeout — avoids thread leak)"
    ]


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
