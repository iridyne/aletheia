"""Tests for market_observer parallel-prefetch helpers and signal-strength ordering.

Covers:
- _signal_score() normalisation
- Processing-order sort (signal-first)
- Round-robin advance uses original instrument order, not sorted order
- _prefetch_symbol() error isolation (one failing symbol doesn't kill others)
"""

import asyncio

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


def test_signal_score_does_not_accept_keyword_only():
    from aletheia.agents.market_observer import _signal_score

    pf = {"passed": True, "rsi": None, "atr_pct": None, "keyword_hits": ["breakout"], "matched_rules": ["keyword"]}
    assert _signal_score(pf) == 0.0


def test_signal_score_takes_max_of_dimensions():
    from aletheia.agents.market_observer import _signal_score, _PREFILTER_RSI_DEVIATION, _PREFILTER_ATR_PCT

    # RSI deviation = 14 → 14/12 ≈ 1.17; ATR% = 1.6 → 1.6/0.8 = 2.0 — ATR wins
    pf = {
        "passed": True,
        "rsi": 36.0,
        "atr_pct": 1.6,
        "keyword_hits": [],
        "matched_rules": ["rsi_deviation", "atr_pct"],
    }
    score = _signal_score(pf)
    expected = max(14.0 / _PREFILTER_RSI_DEVIATION, 1.6 / _PREFILTER_ATR_PCT)
    assert abs(score - expected) < 1e-9


def test_signal_score_below_threshold():
    from aletheia.agents.market_observer import _signal_score

    # RSI close to 50 → no deviation rule fires; keyword also absent → score < 1.0
    pf = {"passed": False, "rsi": 52.0, "atr_pct": 0.3, "keyword_hits": [], "matched_rules": []}
    assert _signal_score(pf) < 1.0


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_pre_filter_rejects_nonfinite_structured_values(value):
    from aletheia.agents.market_observer import _evaluate_pre_filter, _signal_score

    result = _evaluate_pre_filter(
        {
            "symbols": {
                "BTC/USDT": {"1h": {"rsi_14": value, "atr_pct": value}},
            },
        }
    )

    assert result == {
        "passed": False,
        "rsi": None,
        "atr_pct": None,
        "keyword_hits": [],
        "matched_rules": [],
    }
    assert _signal_score(result) == 0.0


# ---------------------------------------------------------------------------
# Signal-strength ordering
# ---------------------------------------------------------------------------


def test_signal_ordering_passed_before_failed():
    """Instruments with signal_score >= 1.0 sort before those with score < 1.0."""
    from aletheia.agents.market_observer import _signal_score

    scores = {
        "A": _signal_score({"passed": False, "rsi": 51.0, "atr_pct": 0.2, "keyword_hits": [], "matched_rules": []}),
        "B": _signal_score(
            {"passed": True, "rsi": 35.0, "atr_pct": None, "keyword_hits": [], "matched_rules": ["rsi_deviation"]}
        ),
        "C": _signal_score(
            {"passed": True, "rsi": None, "atr_pct": 1.6, "keyword_hits": [], "matched_rules": ["atr_pct"]}
        ),
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
        "A": _signal_score(
            {"passed": True, "rsi": 20.0, "atr_pct": None, "keyword_hits": [], "matched_rules": ["rsi_deviation"]}
        ),
        "B": _signal_score(
            {"passed": True, "rsi": 37.0, "atr_pct": None, "keyword_hits": [], "matched_rules": ["rsi_deviation"]}
        ),
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


def test_signal_priority_keeps_durable_round_robin_item_boundedly_reachable(tmp_path, monkeypatch):
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
            canonical,
            next_key=rr.next_key(),
            key=str,
        )
        ordered = _fair_signal_order(
            rotated,
            score=lambda item: scores[item],
            key=str,
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

    async def fake_wait(tasks, timeout):
        captured["timeout"] = timeout
        await asyncio.gather(*tasks)
        return set(tasks), set()

    monkeypatch.setattr(mo, "_monotonic", lambda: 140.0)
    monkeypatch.setattr(mo.asyncio, "wait", fake_wait)

    result = await mo._gather_prefetch_with_deadline([_value()], deadline=500.0)

    assert result == ["ready"]
    assert captured["timeout"] == pytest.approx(360.0)


@pytest.mark.asyncio
async def test_prefetch_deadline_does_not_drain_cancellation_resistant_io(monkeypatch):
    from aletheia.agents import market_observer as mo

    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    cleanup_finished = asyncio.Event()

    async def cancellation_resistant():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleanup_started.set()
            await release_cleanup.wait()
        finally:
            cleanup_finished.set()

    monkeypatch.setattr(mo, "_monotonic", lambda: 0.0)

    started = asyncio.get_running_loop().time()
    with pytest.raises(TimeoutError):
        await mo._gather_prefetch_with_deadline(
            [cancellation_resistant()],
            deadline=0.02,
        )
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < 0.1
    await asyncio.wait_for(cleanup_started.wait(), timeout=0.5)
    release_cleanup.set()
    await asyncio.wait_for(cleanup_finished.wait(), timeout=0.5)


@pytest.mark.asyncio
async def test_prefetch_batch_reuses_one_provider_and_bounds_concurrency(monkeypatch):
    from types import SimpleNamespace

    from aletheia.agents import market_observer as mo
    from aletheia.data import ccxt_provider

    class FakeProvider:
        def __init__(self):
            self.close_calls = 0

        async def close(self):
            self.close_calls += 1

    provider = FakeProvider()
    monkeypatch.setattr(
        ccxt_provider,
        "get_market_data_provider",
        lambda **_kwargs: provider,
    )

    active = 0
    max_active = 0
    seen_providers = []

    async def fake_prefetch(instrument, *, provider, **_kwargs):
        nonlocal active, max_active
        seen_providers.append(provider)
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return instrument.symbol

    monkeypatch.setattr(mo, "_prefetch_symbol", fake_prefetch)
    instruments = [SimpleNamespace(symbol=f"S{i}/USDT", market_type="spot") for i in range(10)]
    db_cache = mo._DbCache(
        perp_structures={},
        funding_hl={},
        funding_hl_pred={},
        news={},
        onchain_rows=[],
    )

    results = await mo._prefetch_instruments(
        instruments,
        venue="fixture",
        prediction_context_enabled=False,
        prediction_context_cutoff=None,
        db_cache=db_cache,
        concurrency=3,
    )

    assert results == [instrument.symbol for instrument in instruments]
    assert max_active == 3
    assert seen_providers == [provider] * len(instruments)
    assert provider.close_calls == 1


@pytest.mark.asyncio
async def test_prefetch_time_is_charged_to_same_serial_loop_deadline(tmp_path, monkeypatch):
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
        perp_structures={},
        funding_hl={},
        funding_hl_pred={},
        news={},
        onchain_rows=[],
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
                prediction_enabled=False,
                ccxt_exchange="fixture",
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
    assert result["errors"] == []
    assert result["warnings"] == [
        "loop_budget_exceeded: processed 1/3, skipped 2 (wall-clock cap 460s < 500s job timeout — avoids thread leak)"
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
        perp_structures={},
        funding_hl={},
        funding_hl_pred={},
        news={},
        onchain_rows=[],
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
async def test_spot_prefetch_omits_hl_and_position_x(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import patch

    _prepare_db(tmp_path, monkeypatch)
    from aletheia.agents.market_observer import _DbCache, _prefetch_symbol

    cache = _DbCache(
        perp_structures={"BTC/USDT": {"id": "px", "source": "position_x"}},
        funding_hl={"BTC/USDT": {"id": "hl-fr", "funding_rate_annualized": 0.5}},
        funding_hl_pred={},
        news={},
        onchain_rows=[],
        open_interest_hl={"BTC/USDT": {"id": "hl-oi", "open_interest_usd": 1e9}},
    )
    captured = {}

    async def fake_market_context(*args, **kwargs):
        return "spot technical context", {
            "fallback": False,
            "errors": [],
            "symbols": {"BTC/USDT": {"1h": {"rsi_14": 50.0, "atr_pct": 0.2}}},
        }

    def fake_render(*args, **kwargs):
        captured.update(kwargs)
        return ""

    instrument = SimpleNamespace(symbol="BTC/USDT", market_type="spot")
    snapshot = SimpleNamespace(tradability={}, leverage={}, macro={})
    with (
        patch(
            "aletheia.trading.market_context.build_market_context",
            side_effect=fake_market_context,
        ),
        patch(
            "aletheia.data.context_builder.build_market_context_from_db",
            return_value=snapshot,
        ),
        patch(
            "aletheia.data.context_builder.render_enriched_context_md",
            side_effect=fake_render,
        ),
    ):
        result = await _prefetch_symbol(
            instrument,
            venue="okx",
            provider=None,
            prediction_context_enabled=False,
            prediction_context_cutoff=None,
            db_cache=cache,
        )

    assert result.funding_rate_val is None
    assert result.open_interest_val is None
    assert captured["xvenue_funding"] == {}
    assert captured["perp_structure"] == {}


@pytest.mark.asyncio
async def test_failed_prefetch_does_not_reach_llm(tmp_path, monkeypatch):
    """When _prefetched.failed=True, _analyze_symbol() returns early without LLM call."""
    import asyncio
    from unittest.mock import AsyncMock, patch

    _prepare_db(tmp_path, monkeypatch)
    from aletheia.agents.market_observer import _analyze_symbol, _SymbolPrefetch

    failed_prefetch = _SymbolPrefetch(
        context_str="",
        debug={},
        pre_filter={"passed": False, "rsi": None, "atr_pct": None, "keyword_hits": [], "matched_rules": []},
        signal_score=0.0,
        funding_rate_val=None,
        open_interest_val=None,
        oi_delta_val=None,
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


# ---------------------------------------------------------------------------
# Budget truncation must NOT be classified as an error (fix: #612)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loop_budget_exceeded_is_warning_not_error(tmp_path, monkeypatch):
    """loop_budget_exceeded must land in result['warnings'], not result['errors'].

    When the LLM serial loop hits the wall-clock cap with instruments remaining,
    that is expected behaviour (safety budget < scheduler timeout).  Classifying
    it as an error causes the orchestrator to set status='completed_with_errors',
    which _async_run() in scheduler.py converts to a RuntimeError, incrementing
    the consecutive-failure counter and eventually firing a
    job_consecutive_failure:market_observer alert.
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

    instruments = [_Instrument(s) for s in ("X", "Y")]
    empty_cache = mo._DbCache(
        perp_structures={},
        funding_hl={},
        funding_hl_pred={},
        news={},
        onchain_rows=[],
    )
    empty_pf = {
        "passed": True,
        "rsi": 20.0,
        "atr_pct": None,
        "keyword_hits": [],
        "matched_rules": ["rsi_deviation"],
    }
    clock = {"now": 100.0}

    monkeypatch.setattr(mo, "_monotonic", lambda: clock["now"])
    monkeypatch.setattr(mo, "is_tool_permitted", lambda *_a, **_k: True)
    monkeypatch.setattr(
        mo,
        "load_config",
        lambda: SimpleNamespace(
            universe=SimpleNamespace(enabled=False, max_candidates=2),
            market_data=SimpleNamespace(
                prediction_enabled=False,
                ccxt_exchange="fixture",
            ),
            observer=SimpleNamespace(force_full_scan=False),
        ),
    )
    monkeypatch.setattr(mo, "observed_instruments_from_config", lambda _cfg: instruments)
    monkeypatch.setattr(mo, "list_strategies", lambda limit=50: [])
    monkeypatch.setattr(mo, "log_tool", lambda *_a, **_k: None)

    def fake_batch(_symbols):
        clock["now"] += 5.0
        return empty_cache

    async def fake_prefetch(instrument, **_kwargs):
        return mo._SymbolPrefetch(
            context_str=instrument.symbol,
            debug={},
            pre_filter=empty_pf,
            signal_score=1.0,
            funding_rate_val=None,
            open_interest_val=None,
            oi_delta_val=None,
        )

    async def fake_gather(coros, *, deadline):
        results = [await coro for coro in coros]
        # Consume nearly all remaining budget so loop starts at deadline
        clock["now"] = deadline  # exactly at deadline — first instrument is skipped
        return results

    monkeypatch.setattr(mo, "_batch_prefetch_db", fake_batch)
    monkeypatch.setattr(mo, "_prefetch_symbol", fake_prefetch)
    monkeypatch.setattr(mo, "_gather_prefetch_with_deadline", fake_gather)
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

    result = await mo.run_market_observer(permission_mode="Observe", run_id="budget-warn-test")

    # ── core contract: budget truncation is NOT a failure ──
    assert result["errors"] == [], (
        "loop_budget_exceeded must not appear in errors — it would trigger job_consecutive_failure:market_observer"
    )
    assert any("loop_budget_exceeded" in w for w in result.get("warnings", [])), (
        "loop_budget_exceeded must appear in warnings so operators can observe it"
    )


@pytest.mark.asyncio
async def test_prefetch_budget_exceeded_is_warning_not_error(tmp_path, monkeypatch):
    """prefetch_budget_exceeded must land in result['warnings'], not result['errors'].

    Same reasoning as test_loop_budget_exceeded_is_warning_not_error: a TimeoutError
    from the prefetch phase is a budget-safety truncation, not a real failure.
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

    instruments = [_Instrument("Z")]
    empty_cache = mo._DbCache(
        perp_structures={},
        funding_hl={},
        funding_hl_pred={},
        news={},
        onchain_rows=[],
    )

    monkeypatch.setattr(mo, "_monotonic", lambda: 100.0)
    monkeypatch.setattr(mo, "is_tool_permitted", lambda *_a, **_k: True)
    monkeypatch.setattr(
        mo,
        "load_config",
        lambda: SimpleNamespace(
            universe=SimpleNamespace(enabled=False, max_candidates=1),
            market_data=SimpleNamespace(
                prediction_enabled=False,
                ccxt_exchange="fixture",
            ),
            observer=SimpleNamespace(force_full_scan=False),
        ),
    )
    monkeypatch.setattr(mo, "observed_instruments_from_config", lambda _cfg: instruments)
    monkeypatch.setattr(mo, "list_strategies", lambda limit=50: [])
    monkeypatch.setattr(mo, "log_tool", lambda *_a, **_k: None)

    async def _noop_prefetch_instruments(*_a, **_k):
        return []

    def fake_batch(_symbols):
        return empty_cache

    async def fake_gather_timeout(coros, *, deadline):
        # Drain the coroutines so they don't linger as un-awaited tasks.
        for coro in coros:
            try:
                coro.close()
            except Exception:
                pass
        raise TimeoutError("budget test")

    monkeypatch.setattr(mo, "_batch_prefetch_db", fake_batch)
    monkeypatch.setattr(mo, "_prefetch_instruments", _noop_prefetch_instruments)
    monkeypatch.setattr(mo, "_gather_prefetch_with_deadline", fake_gather_timeout)
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

    result = await mo.run_market_observer(permission_mode="Observe", run_id="prefetch-warn-test")

    assert result["errors"] == [], "prefetch_budget_exceeded must not appear in errors"
    assert any("prefetch_budget_exceeded" in w for w in result.get("warnings", [])), (
        "prefetch_budget_exceeded must appear in warnings"
    )


# ---------------------------------------------------------------------------
# Phase 4 bounded-concurrent LLM loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_loop_runs_concurrently(tmp_path, monkeypatch):
    """_analyze_symbol calls for different instruments must overlap in time.

    With concurrency=2 and 4 instruments each taking 0.1s, the wall clock
    should be roughly 0.2s (2 batches × 0.1s), not 0.4s (4 × 0.1s serial).
    We use a generous 2× serial-time upper bound to keep the test robust.
    """
    import asyncio
    import time
    from types import SimpleNamespace

    _prepare_db(tmp_path, monkeypatch)
    from aletheia.agents import market_observer as mo

    class _Instrument:
        def __init__(self, symbol):
            self.symbol = symbol
            self.market_type = "spot"

        def __str__(self):
            return f"{self.symbol}:spot"

    n_symbols = 4
    instruments = [_Instrument(s) for s in ("A", "B", "C", "D")]
    empty_cache = mo._DbCache(
        perp_structures={},
        funding_hl={},
        funding_hl_pred={},
        news={},
        onchain_rows=[],
    )
    empty_pf = {
        "passed": True,
        "rsi": 20.0,
        "atr_pct": None,
        "keyword_hits": [],
        "matched_rules": ["rsi_deviation"],
    }

    monkeypatch.setattr(mo, "_monotonic", time.monotonic)
    monkeypatch.setattr(mo, "is_tool_permitted", lambda *_a, **_k: True)
    monkeypatch.setattr(
        mo,
        "load_config",
        lambda: SimpleNamespace(
            universe=SimpleNamespace(enabled=False, max_candidates=n_symbols),
            market_data=SimpleNamespace(prediction_enabled=False, ccxt_exchange="fixture"),
            observer=SimpleNamespace(force_full_scan=False),
        ),
    )
    monkeypatch.setattr(mo, "observed_instruments_from_config", lambda _cfg: instruments)
    monkeypatch.setattr(mo, "list_strategies", lambda limit=50: [])
    monkeypatch.setattr(mo, "log_tool", lambda *_a, **_k: None)

    def fake_batch(_symbols):
        return empty_cache

    async def fake_prefetch(instrument, **_kwargs):
        return mo._SymbolPrefetch(
            context_str=instrument.symbol,
            debug={},
            pre_filter=empty_pf,
            signal_score=1.0,
            funding_rate_val=None,
            open_interest_val=None,
            oi_delta_val=None,
        )

    async def fake_gather(coros, *, deadline):
        return [await c for c in coros]

    ANALYZE_DELAY_S = 0.05  # Each fake LLM call costs 50 ms

    async def fake_analyze(*, symbol, **_kwargs):
        await asyncio.sleep(ANALYZE_DELAY_S)
        return None, f"pre_filter_skip:{symbol}"

    monkeypatch.setattr(mo, "_batch_prefetch_db", fake_batch)
    monkeypatch.setattr(mo, "_prefetch_symbol", fake_prefetch)
    monkeypatch.setattr(mo, "_gather_prefetch_with_deadline", fake_gather)
    monkeypatch.setattr(mo, "_analyze_symbol", fake_analyze)
    monkeypatch.setattr("aletheia.knowledge.router.build_advisory_context", lambda **_: "")
    monkeypatch.setattr("aletheia.analytics.insight_synthesizer.get_latest_memo", lambda: None)
    monkeypatch.setattr("aletheia.learning.control.learning_influence_enabled", lambda: False)

    t0 = time.monotonic()
    result = await mo.run_market_observer(permission_mode="Observe", run_id="concurrency-test")
    elapsed = time.monotonic() - t0

    serial_time = n_symbols * ANALYZE_DELAY_S  # 0.20 s
    # With concurrency=2 the wall clock should be ~2 batches × ANALYZE_DELAY_S.
    # We allow up to serial_time * 0.8 as the upper bound to confirm overlap.
    assert elapsed < serial_time * 0.8, (
        f"LLM loop took {elapsed:.3f}s — expected < {serial_time * 0.8:.3f}s "
        f"(serial would be {serial_time:.3f}s); concurrency appears to be serial"
    )
    assert result["errors"] == []


@pytest.mark.asyncio
async def test_llm_loop_concurrent_results_collected(tmp_path, monkeypatch):
    """Concurrent _analyze_symbol results (outputs/errors) are all captured."""
    import asyncio
    from types import SimpleNamespace

    _prepare_db(tmp_path, monkeypatch)
    from aletheia.agents import market_observer as mo

    class _Instrument:
        def __init__(self, symbol):
            self.symbol = symbol
            self.market_type = "spot"

        def __str__(self):
            return f"{self.symbol}:spot"

    instruments = [_Instrument(s) for s in ("X1", "X2", "X3")]
    empty_cache = mo._DbCache(
        perp_structures={},
        funding_hl={},
        funding_hl_pred={},
        news={},
        onchain_rows=[],
    )
    empty_pf = {
        "passed": True,
        "rsi": 20.0,
        "atr_pct": None,
        "keyword_hits": [],
        "matched_rules": ["rsi_deviation"],
    }

    monkeypatch.setattr(mo, "_monotonic", lambda: 100.0)
    monkeypatch.setattr(mo, "is_tool_permitted", lambda *_a, **_k: True)
    monkeypatch.setattr(
        mo,
        "load_config",
        lambda: SimpleNamespace(
            universe=SimpleNamespace(enabled=False, max_candidates=3),
            market_data=SimpleNamespace(prediction_enabled=False, ccxt_exchange="fixture"),
            observer=SimpleNamespace(force_full_scan=False),
        ),
    )
    monkeypatch.setattr(mo, "observed_instruments_from_config", lambda _cfg: instruments)
    monkeypatch.setattr(mo, "list_strategies", lambda limit=50: [])
    monkeypatch.setattr(mo, "log_tool", lambda *_a, **_k: None)

    async def fake_gather(coros, *, deadline):
        return [await c for c in coros]

    async def fake_prefetch(instrument, **_kwargs):
        return mo._SymbolPrefetch(
            context_str=instrument.symbol,
            debug={},
            pre_filter=empty_pf,
            signal_score=1.0,
            funding_rate_val=None,
            open_interest_val=None,
            oi_delta_val=None,
        )

    # Two succeed (pre_filter_skip = counted in _skip_count, not errors/outputs),
    # one raises an unexpected exception.
    async def fake_analyze(*, symbol, **_kwargs):
        if symbol == "X2":
            raise RuntimeError("simulated_llm_crash")
        return None, f"pre_filter_skip:{symbol}"

    monkeypatch.setattr(mo, "_batch_prefetch_db", lambda _: empty_cache)
    monkeypatch.setattr(mo, "_prefetch_symbol", fake_prefetch)
    monkeypatch.setattr(mo, "_gather_prefetch_with_deadline", fake_gather)
    monkeypatch.setattr(mo, "_analyze_symbol", fake_analyze)
    monkeypatch.setattr("aletheia.knowledge.router.build_advisory_context", lambda **_: "")
    monkeypatch.setattr("aletheia.analytics.insight_synthesizer.get_latest_memo", lambda: None)
    monkeypatch.setattr("aletheia.learning.control.learning_influence_enabled", lambda: False)

    result = await mo.run_market_observer(permission_mode="Observe", run_id="concurrent-results-test")

    # X1 and X3 are pre-filter skips (not errors, not outputs).
    # X2 raises → must appear in errors.
    assert any("X2" in e and "simulated_llm_crash" in e for e in result["errors"]), (
        f"Expected X2 crash in errors, got: {result['errors']}"
    )
    # X1 and X3 skips should NOT appear in errors.
    assert not any("X1" in e or "X3" in e for e in result["errors"])


# ---------------------------------------------------------------------------
# T1: per-symbol LLM deadline propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_symbol_deadline_passed_to_llm_structured(tmp_path, monkeypatch):
    """_run_one() must compute _per_symbol_llm_deadline and pass it to _analyze_symbol.

    With a full run budget the per-symbol deadline must be
    _MARKET_OBSERVER_PER_SYMBOL_LLM_CAP_SEC (45s) away from now, not 180s.
    """
    import asyncio
    import time
    from types import SimpleNamespace

    _prepare_db(tmp_path, monkeypatch)
    from aletheia.agents import market_observer as mo

    class _Instrument:
        def __init__(self, symbol):
            self.symbol = symbol
            self.market_type = "spot"

        def __str__(self):
            return f"{self.symbol}:spot"

    instruments = [_Instrument("D1")]
    empty_cache = mo._DbCache(
        perp_structures={}, funding_hl={}, funding_hl_pred={}, news={}, onchain_rows=[],
    )
    empty_pf = {
        "passed": True, "rsi": 20.0, "atr_pct": None,
        "keyword_hits": [], "matched_rules": ["rsi_deviation"],
    }
    captured_deadline: list[float] = []

    monkeypatch.setattr(mo, "_monotonic", time.monotonic)
    monkeypatch.setattr(mo, "is_tool_permitted", lambda *_a, **_k: True)
    monkeypatch.setattr(
        mo, "load_config",
        lambda: SimpleNamespace(
            universe=SimpleNamespace(enabled=False, max_candidates=1),
            market_data=SimpleNamespace(prediction_enabled=False, ccxt_exchange="fixture"),
            observer=SimpleNamespace(force_full_scan=False),
        ),
    )
    monkeypatch.setattr(mo, "observed_instruments_from_config", lambda _cfg: instruments)
    monkeypatch.setattr(mo, "list_strategies", lambda limit=50: [])
    monkeypatch.setattr(mo, "log_tool", lambda *_a, **_k: None)

    async def fake_gather(coros, *, deadline):
        return [await c for c in coros]

    async def fake_prefetch(instrument, **_kwargs):
        return mo._SymbolPrefetch(
            context_str=instrument.symbol, debug={}, pre_filter=empty_pf,
            signal_score=1.0, funding_rate_val=None,
            open_interest_val=None, oi_delta_val=None,
        )

    async def fake_analyze(*, llm_deadline=None, **_kwargs):
        if llm_deadline is not None:
            captured_deadline.append(llm_deadline)
        return None, "pre_filter_skip:D1"

    monkeypatch.setattr(mo, "_batch_prefetch_db", lambda _: empty_cache)
    monkeypatch.setattr(mo, "_prefetch_symbol", fake_prefetch)
    monkeypatch.setattr(mo, "_gather_prefetch_with_deadline", fake_gather)
    monkeypatch.setattr(mo, "_analyze_symbol", fake_analyze)
    monkeypatch.setattr("aletheia.knowledge.router.build_advisory_context", lambda **_: "")
    monkeypatch.setattr("aletheia.analytics.insight_synthesizer.get_latest_memo", lambda: None)
    monkeypatch.setattr("aletheia.learning.control.learning_influence_enabled", lambda: False)

    await mo.run_market_observer(permission_mode="Observe", run_id="deadline-test")

    assert len(captured_deadline) == 1, "llm_deadline must be passed to _analyze_symbol"
    loop = asyncio.get_event_loop()
    cap = mo._MARKET_OBSERVER_PER_SYMBOL_LLM_CAP_SEC
    # The deadline must be no more than cap + 1s from now (generous margin for test latency).
    assert captured_deadline[0] <= loop.time() + cap + 1.0, (
        f"deadline {captured_deadline[0]:.1f} exceeds cap of {cap}s from now"
    )


@pytest.mark.asyncio
async def test_per_symbol_deadline_respects_remaining_budget(tmp_path, monkeypatch):
    """When the run budget is almost exhausted, per-symbol deadline is clamped to it."""
    import asyncio
    import time
    from types import SimpleNamespace

    _prepare_db(tmp_path, monkeypatch)
    from aletheia.agents import market_observer as mo

    class _Instrument:
        def __init__(self, symbol):
            self.symbol = symbol
            self.market_type = "spot"

        def __str__(self):
            return f"{self.symbol}:spot"

    instruments = [_Instrument("E1")]
    empty_cache = mo._DbCache(
        perp_structures={}, funding_hl={}, funding_hl_pred={}, news={}, onchain_rows=[],
    )
    empty_pf = {
        "passed": True, "rsi": 20.0, "atr_pct": None,
        "keyword_hits": [], "matched_rules": ["rsi_deviation"],
    }
    captured_deadline: list[float] = []

    # Fake clock: run started 455s ago → only 5s remaining before deadline (460s budget)
    real_start = time.monotonic()
    fake_start = real_start - 455.0

    def fake_monotonic():
        return time.monotonic() - real_start + fake_start

    monkeypatch.setattr(mo, "_monotonic", fake_monotonic)
    monkeypatch.setattr(mo, "is_tool_permitted", lambda *_a, **_k: True)
    monkeypatch.setattr(
        mo, "load_config",
        lambda: SimpleNamespace(
            universe=SimpleNamespace(enabled=False, max_candidates=1),
            market_data=SimpleNamespace(prediction_enabled=False, ccxt_exchange="fixture"),
            observer=SimpleNamespace(force_full_scan=False),
        ),
    )
    monkeypatch.setattr(mo, "observed_instruments_from_config", lambda _cfg: instruments)
    monkeypatch.setattr(mo, "list_strategies", lambda limit=50: [])
    monkeypatch.setattr(mo, "log_tool", lambda *_a, **_k: None)

    async def fake_gather(coros, *, deadline):
        return [await c for c in coros]

    async def fake_prefetch(instrument, **_kwargs):
        return mo._SymbolPrefetch(
            context_str=instrument.symbol, debug={}, pre_filter=empty_pf,
            signal_score=1.0, funding_rate_val=None,
            open_interest_val=None, oi_delta_val=None,
        )

    async def fake_analyze(*, llm_deadline=None, **_kwargs):
        if llm_deadline is not None:
            captured_deadline.append(llm_deadline)
        return None, "pre_filter_skip:E1"

    monkeypatch.setattr(mo, "_batch_prefetch_db", lambda _: empty_cache)
    monkeypatch.setattr(mo, "_prefetch_symbol", fake_prefetch)
    monkeypatch.setattr(mo, "_gather_prefetch_with_deadline", fake_gather)
    monkeypatch.setattr(mo, "_analyze_symbol", fake_analyze)
    monkeypatch.setattr("aletheia.knowledge.router.build_advisory_context", lambda **_: "")
    monkeypatch.setattr("aletheia.analytics.insight_synthesizer.get_latest_memo", lambda: None)
    monkeypatch.setattr("aletheia.learning.control.learning_influence_enabled", lambda: False)

    await mo.run_market_observer(permission_mode="Observe", run_id="tight-budget-test")

    if captured_deadline:
        loop = asyncio.get_event_loop()
        # With only ~5s remaining, deadline must be << 45s from now.
        remaining_at_capture = captured_deadline[0] - loop.time()
        assert remaining_at_capture <= mo._MARKET_OBSERVER_PER_SYMBOL_LLM_CAP_SEC, (
            f"Expected deadline clamped to remaining budget, got {remaining_at_capture:.1f}s"
        )


# ---------------------------------------------------------------------------
# T4: fair-budget per-symbol deadline distribution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fair_budget_distributes_remaining_time_evenly(tmp_path, monkeypatch):
    """Later symbols get shorter deadlines when an early one consumed extra time.

    With 4 symbols and 200s remaining, the first symbol gets min(45, 200/4=50) = 45s.
    After the first finishes (consuming 40s), 160s remain for 3 symbols:
    the second gets min(45, 160/3≈53) = 45s.  Fair-split always hits the 45s cap
    when budget is ample; it matters when budget is tight.
    """
    import asyncio
    import time
    from types import SimpleNamespace

    _prepare_db(tmp_path, monkeypatch)
    from aletheia.agents import market_observer as mo

    class _Instrument:
        def __init__(self, symbol):
            self.symbol = symbol
            self.market_type = "spot"

        def __str__(self):
            return f"{self.symbol}:spot"

    # 4 symbols; fake clock starts 260s into the run → 200s remaining
    real_start = time.monotonic()
    fake_elapsed = 260.0  # seconds already consumed

    def fake_monotonic():
        return real_start + fake_elapsed + (time.monotonic() - real_start)

    instruments = [_Instrument(s) for s in ("F1", "F2", "F3", "F4")]
    empty_cache = mo._DbCache(
        perp_structures={}, funding_hl={}, funding_hl_pred={}, news={}, onchain_rows=[],
    )
    empty_pf = {
        "passed": True, "rsi": 20.0, "atr_pct": None,
        "keyword_hits": [], "matched_rules": ["rsi_deviation"],
    }
    captured_deadlines: list[float] = []

    monkeypatch.setattr(mo, "_monotonic", fake_monotonic)
    monkeypatch.setattr(mo, "is_tool_permitted", lambda *_a, **_k: True)
    monkeypatch.setattr(
        mo, "load_config",
        lambda: SimpleNamespace(
            universe=SimpleNamespace(enabled=False, max_candidates=4),
            market_data=SimpleNamespace(prediction_enabled=False, ccxt_exchange="fixture"),
            observer=SimpleNamespace(force_full_scan=False),
        ),
    )
    monkeypatch.setattr(mo, "observed_instruments_from_config", lambda _cfg: instruments)
    monkeypatch.setattr(mo, "list_strategies", lambda limit=50: [])
    monkeypatch.setattr(mo, "log_tool", lambda *_a, **_k: None)

    async def fake_gather(coros, *, deadline):
        return [await c for c in coros]

    async def fake_prefetch(instrument, **_kwargs):
        return mo._SymbolPrefetch(
            context_str=instrument.symbol, debug={}, pre_filter=empty_pf,
            signal_score=1.0, funding_rate_val=None,
            open_interest_val=None, oi_delta_val=None,
        )

    async def fake_analyze(*, llm_deadline=None, **_kwargs):
        if llm_deadline is not None:
            captured_deadlines.append(llm_deadline)
        return None, "pre_filter_skip:Fn"

    monkeypatch.setattr(mo, "_batch_prefetch_db", lambda _: empty_cache)
    monkeypatch.setattr(mo, "_prefetch_symbol", fake_prefetch)
    monkeypatch.setattr(mo, "_gather_prefetch_with_deadline", fake_gather)
    monkeypatch.setattr(mo, "_analyze_symbol", fake_analyze)
    monkeypatch.setattr("aletheia.knowledge.router.build_advisory_context", lambda **_: "")
    monkeypatch.setattr("aletheia.analytics.insight_synthesizer.get_latest_memo", lambda: None)
    monkeypatch.setattr("aletheia.learning.control.learning_influence_enabled", lambda: False)

    await mo.run_market_observer(permission_mode="Observe", run_id="fair-budget-test")

    assert len(captured_deadlines) == 4
    loop = asyncio.get_event_loop()
    now = loop.time()
    cap = mo._MARKET_OBSERVER_PER_SYMBOL_LLM_CAP_SEC  # 45s
    for dl in captured_deadlines:
        budget_given = dl - now
        # Each deadline must not exceed cap + small test-latency margin
        assert budget_given <= cap + 2.0, (
            f"deadline budget {budget_given:.1f}s exceeds cap {cap}s"
        )
        # And must not be less than 10s (the floor)
        assert budget_given >= 10.0 - 2.0, (
            f"deadline budget {budget_given:.1f}s is below 10s floor"
        )


@pytest.mark.asyncio
async def test_fair_budget_clamps_to_minimum_floor(tmp_path, monkeypatch):
    """When budget is very tight, per-symbol deadline is clamped to the 10s floor."""
    import asyncio
    import time
    from types import SimpleNamespace

    _prepare_db(tmp_path, monkeypatch)
    from aletheia.agents import market_observer as mo

    class _Instrument:
        def __init__(self, symbol):
            self.symbol = symbol
            self.market_type = "spot"

        def __str__(self):
            return f"{self.symbol}:spot"

    # 3 symbols but only 15s remaining: fair share = 5s < 10s floor → clamped to 10s.
    real_start = time.monotonic()
    fake_elapsed = 445.0  # 460 - 15 = 445s elapsed

    def fake_monotonic():
        return real_start + fake_elapsed + (time.monotonic() - real_start)

    instruments = [_Instrument(s) for s in ("G1", "G2", "G3")]
    empty_cache = mo._DbCache(
        perp_structures={}, funding_hl={}, funding_hl_pred={}, news={}, onchain_rows=[],
    )
    empty_pf = {
        "passed": True, "rsi": 20.0, "atr_pct": None,
        "keyword_hits": [], "matched_rules": ["rsi_deviation"],
    }
    captured_deadlines: list[float] = []

    monkeypatch.setattr(mo, "_monotonic", fake_monotonic)
    monkeypatch.setattr(mo, "is_tool_permitted", lambda *_a, **_k: True)
    monkeypatch.setattr(
        mo, "load_config",
        lambda: SimpleNamespace(
            universe=SimpleNamespace(enabled=False, max_candidates=3),
            market_data=SimpleNamespace(prediction_enabled=False, ccxt_exchange="fixture"),
            observer=SimpleNamespace(force_full_scan=False),
        ),
    )
    monkeypatch.setattr(mo, "observed_instruments_from_config", lambda _cfg: instruments)
    monkeypatch.setattr(mo, "list_strategies", lambda limit=50: [])
    monkeypatch.setattr(mo, "log_tool", lambda *_a, **_k: None)

    async def fake_gather(coros, *, deadline):
        return [await c for c in coros]

    async def fake_prefetch(instrument, **_kwargs):
        return mo._SymbolPrefetch(
            context_str=instrument.symbol, debug={}, pre_filter=empty_pf,
            signal_score=1.0, funding_rate_val=None,
            open_interest_val=None, oi_delta_val=None,
        )

    async def fake_analyze(*, llm_deadline=None, **_kwargs):
        if llm_deadline is not None:
            captured_deadlines.append(llm_deadline)
        return None, "pre_filter_skip:Gn"

    monkeypatch.setattr(mo, "_batch_prefetch_db", lambda _: empty_cache)
    monkeypatch.setattr(mo, "_prefetch_symbol", fake_prefetch)
    monkeypatch.setattr(mo, "_gather_prefetch_with_deadline", fake_gather)
    monkeypatch.setattr(mo, "_analyze_symbol", fake_analyze)
    monkeypatch.setattr("aletheia.knowledge.router.build_advisory_context", lambda **_: "")
    monkeypatch.setattr("aletheia.analytics.insight_synthesizer.get_latest_memo", lambda: None)
    monkeypatch.setattr("aletheia.learning.control.learning_influence_enabled", lambda: False)

    await mo.run_market_observer(permission_mode="Observe", run_id="floor-budget-test")

    if captured_deadlines:
        loop = asyncio.get_event_loop()
        now = loop.time()
        for dl in captured_deadlines:
            budget_given = dl - now
            # With 15s total and 3 symbols, fair = 5s; floor = 10s → each gets 10s.
            # Allow generous +3s test margin.
            assert budget_given >= 10.0 - 3.0, (
                f"expected floor ~10s, got {budget_given:.1f}s"
            )
