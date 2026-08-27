from __future__ import annotations

import asyncio
import hashlib
import json

import pytest

from aletheia.data.providers import Candle, OHLCVResult
from aletheia.data.symbols import InstrumentId


TF_MS = 60_000


def _bar(
    index: int,
    *,
    tf_ms: int = TF_MS,
    open_: float = 100.0,
    high: float = 105.0,
    low: float = 95.0,
    close: float = 100.0,
    is_closed: bool = True,
    provider: str | None = "fake",
    exchange: str | None = "bitget",
) -> Candle:
    return Candle(
        timestamp=index * tf_ms,
        open=open_,
        high=max(high, close),
        low=min(low, close, open_),
        close=close,
        volume=10.0,
        is_closed=is_closed,
        exchange=exchange,
        provider=provider,
    )


def _replacement_fixture() -> list[Candle]:
    bars = [_bar(index) for index in range(30)]
    for index, high in ((3, 110.0), (8, 112.0), (13, 114.0), (18, 118.0)):
        bars[index] = _bar(index, high=high)
    for index, low in ((5, 90.0), (10, 92.0), (15, 94.0)):
        bars[index] = _bar(index, low=low)
    bars[21] = _bar(21, high=116.0, low=100.0, close=115.0)
    bars[22] = _bar(22, high=121.0, low=114.0, close=120.0)
    bars[23] = _bar(23, high=122.0, low=115.0, close=121.0)
    return bars


class _Provider:
    def __init__(self, rows=(), *, error: BaseException | None = None) -> None:
        self.rows = list(rows)
        self.error = error
        self.calls: list[dict] = []
        self.closed = False

    async def get_ohlcv(self, symbol, timeframe="1h", limit=100, since=None):
        self.calls.append({
            "symbol": symbol,
            "timeframe": timeframe,
            "limit": limit,
            "since": since,
        })
        if self.error is not None:
            raise self.error
        return OHLCVResult(self.rows, requested=limit)

    async def close(self) -> None:
        self.closed = True


def _snapshot(source: _Provider, *, timeframe="1m", as_of_ms=None,
              enrichments=frozenset()):
    from aletheia.data.market_structure import MarketStructureProvider

    instrument = InstrumentId.from_parts("bitget", "BTC/USDT", "spot")
    return asyncio.run(MarketStructureProvider(source).get_snapshot(
        instrument,
        timeframe,
        as_of_ms=as_of_ms,
        enrichments=enrichments,
    ))


def test_active_level_replacement_and_one_break_per_level():
    from aletheia.data.market_structure import derive_market_structure

    result = derive_market_structure(_replacement_fixture(), tf_ms=TF_MS)

    assert result.availability == "available"
    assert len(result.events) == 1
    event = result.events[0]
    assert event.type == "BOS"
    assert event.direction == "bullish"
    assert event.broken_swing.origin_at_ms == 18 * TF_MS
    assert event.break_at_ms == 23 * TF_MS  # bar 22 close
    assert event.break_tolerance == pytest.approx(0.05 * event.atr14)


def test_historical_cutoff_excludes_post_cutoff_close():
    from aletheia.data.market_structure import (
        MarketStructureProvider,
        derive_market_structure,
    )

    instrument = InstrumentId.from_parts("bitget", "BTC/USDT", "spot")
    cutoff = 30 * TF_MS

    class Provider:
        calls: list[dict]

        def __init__(self) -> None:
            self.calls = []

        async def get_ohlcv(self, symbol, timeframe="1h", limit=100, since=None):
            self.calls.append({
                "symbol": symbol,
                "timeframe": timeframe,
                "limit": limit,
                "since": since,
            })
            return OHLCVResult(
                [_bar(index) for index in range(31)],
                requested=limit,
            )

    source = Provider()
    snapshot = asyncio.run(
        MarketStructureProvider(source).get_snapshot(
            instrument, "1m", as_of_ms=cutoff
        )
    )

    assert source.calls == [{
        "symbol": instrument,
        "timeframe": "1m",
        "limit": 121,
        "since": 0,
    }]
    assert snapshot.availability == "available"
    assert snapshot.as_of_ms == cutoff
    assert snapshot.provenance.admitted_bar_count == 30
    assert snapshot.provenance.last_bar_open_at_ms == 29 * TF_MS
    assert all(fact.known_at_ms <= cutoff for fact in snapshot.swings)
    assert all(fact.known_at_ms <= cutoff for fact in snapshot.events)
    pure = derive_market_structure([_bar(index) for index in range(30)], tf_ms=TF_MS)
    assert snapshot.structure_state == pure.structure_state
    assert [
        (fact.kind, fact.price, fact.origin_at_ms, fact.known_at_ms)
        for fact in snapshot.swings
    ] == [
        (fact.kind, fact.price, fact.origin_at_ms, fact.known_at_ms)
        for fact in pure.swings
    ]


@pytest.mark.parametrize("tf_ms", [TF_MS, 5 * TF_MS])
def test_swing_is_prefix_causal_and_uses_confirming_bar_close(tf_ms):
    from aletheia.data.market_structure import derive_market_structure

    bars = [_bar(index, tf_ms=tf_ms) for index in range(34)]
    bars[31] = _bar(
        31, tf_ms=tf_ms, high=130.0, low=80.0
    )

    before = derive_market_structure(bars[:33], tf_ms=tf_ms)
    after = derive_market_structure(bars, tf_ms=tf_ms)
    before_origins = {(swing.kind, swing.origin_at_ms) for swing in before.swings}
    swings = [swing for swing in after.swings if swing.origin_at_ms == 31 * tf_ms]

    assert ("high", 31 * tf_ms) not in before_origins
    assert ("low", 31 * tf_ms) not in before_origins
    assert {swing.kind for swing in swings} == {"high", "low"}
    assert all(swing.confirmed_at_ms == 34 * tf_ms for swing in swings)
    assert all(swing.known_at_ms == 34 * tf_ms for swing in swings)


def test_equal_neighbor_rejects_only_the_equal_swing_kind():
    from aletheia.data.market_structure import derive_market_structure

    bars = [_bar(index) for index in range(34)]
    bars[30] = _bar(30, high=130.0)
    bars[31] = _bar(31, high=130.0, low=80.0)
    result = derive_market_structure(bars, tf_ms=TF_MS)
    at_pivot = [swing for swing in result.swings if swing.origin_at_ms == 31 * TF_MS]

    assert [swing.kind for swing in at_pivot] == ["low"]


@pytest.mark.parametrize(
    ("highs", "lows", "expected"),
    [
        ((110.0, 112.0), (90.0, 92.0), "bullish"),
        ((112.0, 110.0), (92.0, 90.0), "bearish"),
        ((110.0, 112.0), (92.0, 90.0), "range"),
    ],
)
def test_structure_state_uses_latest_confirmed_geometry(highs, lows, expected):
    from aletheia.data.market_structure import derive_market_structure

    bars = [_bar(index) for index in range(30)]
    for index, value in zip((5, 15), highs):
        bars[index] = _bar(index, high=value)
    for index, value in zip((10, 20), lows):
        bars[index] = _bar(index, low=value)

    assert derive_market_structure(bars, tf_ms=TF_MS).structure_state == expected


def test_structure_state_is_unconfirmed_without_two_swings_of_each_kind():
    from aletheia.data.market_structure import derive_market_structure

    result = derive_market_structure([_bar(index) for index in range(30)], tf_ms=TF_MS)
    assert result.structure_state == "unconfirmed"


def test_bullish_opposing_break_is_choch_and_does_not_repeat():
    from aletheia.data.market_structure import derive_market_structure

    bars = _replacement_fixture()
    bars[18] = _bar(18, high=118.0)
    bars[21] = _bar(21, high=101.0, low=89.0, close=90.0)
    bars[22] = _bar(22, high=100.0, low=88.0, close=89.0)
    result = derive_market_structure(bars, tf_ms=TF_MS)
    choch = [event for event in result.events if event.type == "CHOCH"]

    assert len(choch) == 1
    assert choch[0].direction == "bearish"
    assert choch[0].break_at_ms == 22 * TF_MS
    assert choch[0].known_at_ms == choch[0].break_at_ms


def test_bearish_bos_and_choch_are_exact_mirrors():
    from aletheia.data.market_structure import derive_market_structure

    def mirror(candle: Candle) -> Candle:
        return Candle(
            timestamp=candle.timestamp,
            open=200.0 - candle.open,
            high=200.0 - candle.low,
            low=200.0 - candle.high,
            close=200.0 - candle.close,
            volume=candle.volume,
            exchange=candle.exchange,
            provider=candle.provider,
        )

    bullish_bos = _replacement_fixture()
    bearish_bos = derive_market_structure(
        [mirror(candle) for candle in bullish_bos], tf_ms=TF_MS
    )
    bullish_choch = _replacement_fixture()
    bullish_choch[21] = _bar(21, high=101.0, low=89.0, close=90.0)
    bullish_choch[22] = _bar(22, high=100.0, low=88.0, close=89.0)
    bearish_choch = derive_market_structure(
        [mirror(candle) for candle in bullish_choch], tf_ms=TF_MS
    )

    assert [(event.type, event.direction) for event in bearish_bos.events] == [
        ("BOS", "bearish")
    ]
    assert [(event.type, event.direction) for event in bearish_choch.events
            if event.type == "CHOCH"] == [
        ("CHOCH", "bullish")
    ]


def test_simultaneous_cross_consumes_both_levels_without_an_event():
    from aletheia.data.market_structure import GeometrySwing, _apply_break

    active_high = GeometrySwing("high", 90.0, 1, TF_MS, 4 * TF_MS, 4 * TF_MS)
    active_low = GeometrySwing("low", 110.0, 2, 2 * TF_MS, 5 * TF_MS, 5 * TF_MS)
    high, low, event = _apply_break(
        active_high,
        active_low,
        _bar(10, high=105.0, low=95.0, close=100.0),
        tf_ms=TF_MS,
        atr14=10.0,
        state="bullish",
    )

    assert high is None and low is None
    assert event is None


def test_break_equality_at_tolerance_boundary_is_not_a_break():
    from aletheia.data.market_structure import derive_market_structure

    bars = [
        _bar(index, open_=100.5, high=105.5, low=95.5, close=100.5)
        for index in range(30)
    ]
    bars[3] = _bar(3, open_=100.5, high=108.0, low=95.5, close=100.5)
    bars[5] = _bar(5, open_=100.5, high=105.5, low=90.0, close=100.5)
    bars[13] = _bar(13, open_=100.5, high=110.0, low=95.5, close=100.5)
    bars[15] = _bar(15, open_=100.5, high=105.5, low=92.0, close=100.5)
    bars[29] = _bar(29, open_=100.5, high=110.5, low=100.5, close=110.5)
    result = derive_market_structure(bars, tf_ms=TF_MS)

    assert result.atr14 == pytest.approx(10.0)
    assert result.break_tolerance == pytest.approx(0.5)
    assert result.events == ()


def test_atr_is_latest_14_simple_true_range_without_shared_wilder(monkeypatch):
    from aletheia.data.market_structure import derive_market_structure

    monkeypatch.setattr(
        "aletheia.trading.indicators.atr",
        lambda *_args, **_kwargs: pytest.fail("Wilder ATR must not be called"),
    )
    bars = [_bar(index, high=101.0 + index, low=99.0) for index in range(30)]
    result = derive_market_structure(bars, tf_ms=TF_MS)
    expected = sum(
        max(
            bars[index].high - bars[index].low,
            abs(bars[index].high - bars[index - 1].close),
            abs(bars[index].low - bars[index - 1].close),
        )
        for index in range(16, 30)
    ) / 14

    assert result.atr14 == pytest.approx(expected)
    assert result.break_tolerance == pytest.approx(0.05 * expected)


def test_provider_latest_read_is_bounded_and_retains_120():
    rows = [_bar(index) for index in range(121)]
    source = _Provider(rows)
    snapshot = _snapshot(source)

    assert source.calls[0]["limit"] == 121
    assert source.calls[0]["since"] is None
    assert snapshot.availability == "available"
    assert snapshot.provenance.admitted_bar_count == 120
    assert snapshot.provenance.first_bar_open_at_ms == TF_MS
    assert snapshot.provenance.history_quality == "complete"
    assert source.closed is False


def test_latest_excludes_forming_bar_and_empty_source_is_explicit():
    rows = [_bar(index) for index in range(30)]
    rows.append(_bar(30, is_closed=False))
    snapshot = _snapshot(_Provider(rows))
    empty = _snapshot(_Provider())

    assert snapshot.availability == "available"
    assert snapshot.as_of_ms == 30 * TF_MS
    assert snapshot.provenance.admitted_bar_count == 30
    assert empty.availability == "unavailable"
    assert empty.unavailable_reason == "empty_ohlcv"


def test_zero_atr_is_a_real_available_value_not_missing_evidence():
    rows = [
        _bar(index, open_=100.0, high=100.0, low=100.0, close=100.0)
        for index in range(30)
    ]
    snapshot = _snapshot(_Provider(rows))
    assert snapshot.availability == "available"
    assert snapshot.atr14 == 0.0
    assert snapshot.break_tolerance == 0.0


@pytest.mark.parametrize(
    ("count", "availability", "quality", "reason"),
    [
        (29, "unavailable", "unavailable", "insufficient_closed_bars"),
        (30, "available", "partial", None),
        (119, "available", "partial", None),
        (120, "available", "complete", None),
    ],
)
def test_provider_history_quality_is_honest(count, availability, quality, reason):
    snapshot = _snapshot(_Provider([_bar(index) for index in range(count)]))
    assert snapshot.availability == availability
    assert snapshot.provenance.history_quality == quality
    assert snapshot.unavailable_reason == reason


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda rows: rows.__setitem__(10, _bar(10, exchange=None)),
         "source_venue_unproven"),
        (lambda rows: rows.__setitem__(10, _bar(10, exchange="okx")),
         "source_venue_mismatch"),
        (lambda rows: rows.__setitem__(10, _bar(9)),
         "duplicate_or_out_of_order"),
        (lambda rows: rows.__setitem__(10, _bar(10, tf_ms=TF_MS + 1)),
         "candle_gap"),
        (lambda rows: rows.__setitem__(10, _bar(10, close=float("nan"))),
         "invalid_candle"),
    ],
)
def test_provider_rejects_unproven_or_invalid_series_without_repair(mutate, reason):
    rows = [_bar(index) for index in range(30)]
    mutate(rows)
    snapshot = _snapshot(_Provider(rows))
    assert snapshot.availability == "unavailable"
    assert snapshot.unavailable_reason == reason


def test_historical_missing_tail_is_unavailable():
    snapshot = _snapshot(
        _Provider([_bar(index) for index in range(30)]),
        as_of_ms=32 * TF_MS,
    )
    assert snapshot.unavailable_reason == "historical_tail_gap"


def test_historical_read_uses_exact_since_and_is_replay_stable():
    from aletheia.data.market_structure import MarketStructureProvider

    instrument = InstrumentId.from_parts("bitget", "BTC/USDT", "spot")
    cutoff = 200 * TF_MS
    rows = [_bar(index) for index in range(80, 201)]
    source = _Provider(rows)
    provider = MarketStructureProvider(source)
    first = asyncio.run(provider.get_snapshot(instrument, "1m", as_of_ms=cutoff))
    source.rows.extend(_bar(index) for index in range(201, 211))
    second = asyncio.run(provider.get_snapshot(instrument, "1m", as_of_ms=cutoff))

    assert source.calls[0]["since"] == cutoff - 121 * TF_MS
    assert first.as_of_ms == second.as_of_ms == cutoff
    assert first.structure_state == second.structure_state
    assert first.swings == second.swings
    assert first.events == second.events
    assert (
        first.provenance.source_candle_fingerprint
        == second.provenance.source_candle_fingerprint
    )


def test_provider_failure_is_explicit_and_cancellation_propagates():
    failed = _snapshot(_Provider(error=RuntimeError("offline")))
    assert failed.availability == "unavailable"
    assert failed.unavailable_reason == "provider_unavailable"

    with pytest.raises(asyncio.CancelledError):
        _snapshot(_Provider(error=asyncio.CancelledError()))


def test_geometry_and_source_fingerprints_have_separate_identity():
    from aletheia.data.market_structure import MarketStructureProvider

    instrument = InstrumentId.from_parts("bitget", "BTC/USDT", "spot")
    first = _Provider([_bar(index, provider="one") for index in range(30)])
    second = _Provider([_bar(index, provider="two") for index in range(30)])
    first_snapshot = asyncio.run(
        MarketStructureProvider(first).get_snapshot(instrument, "1m")
    )
    second_snapshot = asyncio.run(
        MarketStructureProvider(second).get_snapshot(instrument, "1m")
    )

    assert (
        first_snapshot.provenance.geometry_fingerprint
        == second_snapshot.provenance.geometry_fingerprint
    )
    assert (
        first_snapshot.provenance.source_candle_fingerprint
        != second_snapshot.provenance.source_candle_fingerprint
    )
    assert first_snapshot.swings == second_snapshot.swings
    assert first_snapshot.events == second_snapshot.events

    revised_rows = [_bar(index, provider="one") for index in range(30)]
    revised_rows[-1] = _bar(29, close=101.0, provider="one")
    revised = _snapshot(_Provider(revised_rows))
    assert revised.provenance.geometry_fingerprint != (
        first_snapshot.provenance.geometry_fingerprint
    )
    assert revised.provenance.source_candle_fingerprint != (
        first_snapshot.provenance.source_candle_fingerprint
    )


def test_params_hash_and_fact_ids_are_full_stable_sha256(monkeypatch):
    from aletheia.data import market_structure as module

    payload = {
        "atr_method": "simple_latest_tr_mean",
        "atr_period": 14,
        "break_tolerance_atr": "0.05",
        "fractal_k": 2,
        "min_bars": 30,
        "window_bars": 120,
    }
    expected = hashlib.sha256(json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()).hexdigest()
    monkeypatch.setattr(module.time, "time_ns", lambda: 1_000_000_000)
    first = _snapshot(_Provider(_replacement_fixture()))
    monkeypatch.setattr(module.time, "time_ns", lambda: 2_000_000_000)
    second = _snapshot(_Provider(_replacement_fixture()))

    assert module.PARAMS_HASH == expected
    assert first.provenance.computed_at_ms != second.provenance.computed_at_ms
    assert [fact.id for fact in first.swings] == [fact.id for fact in second.swings]
    assert [fact.id for fact in first.events] == [fact.id for fact in second.events]
    assert all(len(fact.id) == 64 and fact.id == fact.id.lower() for fact in first.swings)


def test_fvg_formation_time_direction_mitigation_and_stable_id():
    from aletheia.data.smc_enrichment import derive_fvg_zones

    instrument = InstrumentId.from_parts("bitget", "BTC/USDT", "spot")
    bars = [
        _bar(0, open_=99.0, high=100.0, low=98.0, close=99.0),
        _bar(1, open_=100.0, high=105.0, low=99.0, close=104.0),
        _bar(2, open_=106.0, high=108.0, low=106.0, close=107.0),
    ]
    open_zone = derive_fvg_zones(
        bars, instrument=instrument, timeframe="1m", tf_ms=TF_MS
    )[0]
    mitigated = derive_fvg_zones(
        bars + [_bar(3, open_=107.0, high=108.0, low=105.0, close=106.0)],
        instrument=instrument,
        timeframe="1m",
        tf_ms=TF_MS,
    )[0]

    assert (open_zone.direction, open_zone.top, open_zone.bottom) == (
        "bullish", 106.0, 100.0
    )
    assert open_zone.origin_at_ms == TF_MS
    assert open_zone.known_at_ms == 3 * TF_MS
    assert open_zone.status == "open" and open_zone.mitigated_at_ms is None
    assert mitigated.status == "mitigated"
    assert mitigated.mitigated_at_ms == 4 * TF_MS
    assert mitigated.id == open_zone.id


def test_fvg_bearish_mirror_and_middle_direction_rules():
    from aletheia.data.smc_enrichment import derive_fvg_zones

    instrument = InstrumentId.from_parts("bitget", "BTC/USDT", "spot")
    bearish = [
        _bar(0, open_=111.0, high=112.0, low=110.0, close=111.0),
        _bar(1, open_=110.0, high=111.0, low=105.0, close=106.0),
        _bar(2, open_=103.0, high=104.0, low=102.0, close=103.0),
    ]
    zone = derive_fvg_zones(
        bearish, instrument=instrument, timeframe="1m", tf_ms=TF_MS
    )[0]
    doji = [bearish[0], _bar(1, open_=108.0, close=108.0), bearish[2]]
    wrong_direction = [
        _bar(0, open_=99.0, high=100.0, low=98.0, close=99.0),
        _bar(1, open_=104.0, high=105.0, low=99.0, close=100.0),
        _bar(2, open_=106.0, high=108.0, low=106.0, close=107.0),
    ]

    assert (zone.direction, zone.top, zone.bottom) == ("bearish", 110.0, 104.0)
    assert derive_fvg_zones(
        doji, instrument=instrument, timeframe="1m", tf_ms=TF_MS
    ) == ()
    assert derive_fvg_zones(
        wrong_direction, instrument=instrument, timeframe="1m", tf_ms=TF_MS
    ) == ()
    assert derive_fvg_zones(
        bearish[:2], instrument=instrument, timeframe="1m", tf_ms=TF_MS
    ) == ()


def test_requested_fvg_failure_does_not_invalidate_core(monkeypatch):
    monkeypatch.setattr(
        "aletheia.data.smc_enrichment.derive_fvg_zones",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    snapshot = _snapshot(
        _Provider(_replacement_fixture()), enrichments=frozenset({"fvg"})
    )

    assert snapshot.availability == "available"
    assert snapshot.fvg_availability == "unavailable"
    assert snapshot.fvg_unavailable_reason == "fvg_calculation_failed:RuntimeError"


def test_requested_fvg_on_rejected_core_reports_core_reason():
    snapshot = _snapshot(
        _Provider([_bar(index) for index in range(10)]),
        enrichments=frozenset({"fvg"}),
    )
    assert snapshot.availability == "unavailable"
    assert snapshot.fvg_availability == "unavailable"
    assert snapshot.fvg_unavailable_reason == (
        "core_unavailable:insufficient_closed_bars"
    )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"instrument": "BTC/USDT", "timeframe": "1m"},
         "market_structure_instrument_required"),
        ({"instrument": InstrumentId.from_parts("bitget", "BTC/USDT"),
          "timeframe": "2m"}, "market_structure_timeframe_unsupported:2m"),
        ({"instrument": InstrumentId.from_parts("bitget", "BTC/USDT"),
          "timeframe": "1m", "as_of_ms": -1}, "market_structure_as_of_invalid"),
        ({"instrument": InstrumentId.from_parts("bitget", "BTC/USDT"),
          "timeframe": "1m", "enrichments": frozenset({"order_block"})},
         "market_structure_enrichment_unsupported:order_block"),
    ],
)
def test_provider_rejects_contract_misuse(kwargs, message):
    from aletheia.data.market_structure import MarketStructureProvider

    provider = MarketStructureProvider(_Provider())
    with pytest.raises(ValueError, match=f"^{message}$"):
        asyncio.run(provider.get_snapshot(**kwargs))
