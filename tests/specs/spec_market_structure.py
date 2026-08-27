"""Executable claims for the current Market Structure Derived Data Contract."""

from __future__ import annotations

import ast
import asyncio
import inspect
from pathlib import Path

from aletheia.data.providers import Candle, OHLCVResult
from aletheia.data.symbols import InstrumentId
from tests.specs.spec_meta import SpecClaim, spec_claim


ROOT = Path(__file__).resolve().parents[2]
TF_MS = 60_000


@spec_claim(SpecClaim(
    "MS-S01",
    "market_structure_contract",
    "§ Invariants and trust boundaries",
    "L2a",
    "Universe contains only a compatibility projection; the canonical data "
    "module owns the sole Swing/BOS/CHOCH implementation and introduces no "
    "storage, cache, scheduler, incremental service, or heavy SMC dependency",
))
def test_market_structure_has_one_dependency_light_core():
    canonical = (ROOT / "aletheia/data/market_structure.py").read_text()
    compatibility = (ROOT / "aletheia/universe/market_structure.py").read_text()
    project = (ROOT / "pyproject.toml").read_text().lower()

    ast.parse(canonical)
    ast.parse(compatibility)
    assert compatibility.count("derive_market_structure(") == 1
    assert "MarketStructureProvider" not in compatibility
    assert "get_ohlcv" not in compatibility
    assert "smc_enrichment" not in compatibility
    assert "aletheia.storage" not in canonical
    enrichment = (ROOT / "aletheia/data/smc_enrichment.py").read_text()
    assert "join_consecutive" not in enrichment
    assert all(token not in project for token in (
        "smartmoneyconcepts", "pandas", "numba",
    ))
    for path in (ROOT / "aletheia/universe").rglob("*.py"):
        source = path.read_text()
        assert "FRACTAL_K" not in source
        assert "BREAK_TOLERANCE_ATR" not in source


@spec_claim(SpecClaim(
    "MS-C01",
    "market_structure_contract",
    "§ Observable result",
    "L2b",
    "A strict K=2 Swing is absent before its second right-side close and is "
    "known exactly at that close for canonical non-1m durations as well as 1m",
))
def test_market_structure_swing_known_time_is_bar_close():
    from aletheia.data.market_structure import derive_market_structure

    tf_ms = 300_000
    bars = [Candle(
        timestamp=index * tf_ms,
        open=100.0,
        high=130.0 if index == 31 else 105.0,
        low=80.0 if index == 31 else 95.0,
        close=100.0,
        volume=10.0,
    ) for index in range(34)]
    before = derive_market_structure(bars[:33], tf_ms=tf_ms)
    after = derive_market_structure(bars, tf_ms=tf_ms)

    assert not any(swing.origin_at_ms == 31 * tf_ms for swing in before.swings)
    facts = [swing for swing in after.swings if swing.origin_at_ms == 31 * tf_ms]
    assert {fact.kind for fact in facts} == {"high", "low"}
    assert all(fact.known_at_ms == 34 * tf_ms for fact in facts)


@spec_claim(SpecClaim(
    "MS-C02",
    "market_structure_contract",
    "§ Failure and recovery semantics",
    "L2b",
    "One exact historical provider read excludes post-cutoff closes and remains "
    "semantically stable after future source bars arrive",
))
def test_market_structure_provider_historical_cutoff_is_replay_stable():
    from aletheia.data.market_structure import MarketStructureProvider

    instrument = InstrumentId.from_parts("bitget", "BTC/USDT", "spot")
    rows = [Candle(
        timestamp=index * TF_MS,
        open=100.0,
        high=105.0,
        low=95.0,
        close=100.0,
        volume=10.0,
        provider="fake",
        exchange="bitget",
    ) for index in range(31)]

    class Provider:
        calls = []

        async def get_ohlcv(self, symbol, timeframe="1h", limit=100, since=None):
            self.calls.append((symbol, timeframe, limit, since))
            return OHLCVResult(rows, requested=limit)

    source = Provider()
    provider = MarketStructureProvider(source)
    cutoff = 30 * TF_MS
    first = asyncio.run(provider.get_snapshot(instrument, "1m", as_of_ms=cutoff))
    rows.append(Candle(
        timestamp=31 * TF_MS,
        open=100.0,
        high=105.0,
        low=95.0,
        close=100.0,
        volume=10.0,
        provider="fake",
        exchange="bitget",
    ))
    second = asyncio.run(provider.get_snapshot(instrument, "1m", as_of_ms=cutoff))

    assert source.calls[0] == (instrument, "1m", 121, 0)
    assert first.as_of_ms == second.as_of_ms == cutoff
    assert first.swings == second.swings
    assert first.events == second.events
    assert first.provenance.source_candle_fingerprint == (
        second.provenance.source_candle_fingerprint
    )


@spec_claim(SpecClaim(
    "MS-A01",
    "market_structure_contract",
    "§ Durable state and consumers",
    "L2c",
    "Fast Trigger delegates to the pure core without provider/FVG I/O and its "
    "persisted market-structure assessment remains Shadow-only",
))
def test_fast_trigger_market_structure_remains_shadow_only():
    from aletheia.universe import fast_trigger
    from aletheia.universe import market_structure

    evaluator = inspect.getsource(market_structure.evaluate_market_structure)
    materializer = inspect.getsource(fast_trigger.materialize_trigger)
    assert "derive_market_structure(" in evaluator
    assert "get_snapshot" not in evaluator
    assert "derive_fvg_zones" not in evaluator
    assert 'check_type="market_structure"' in materializer
    assert 'authority="shadow"' in materializer
    assert "hard_gate_decision" not in materializer
