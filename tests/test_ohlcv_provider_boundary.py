from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from aletheia.data.ccxt_provider import CCXTProvider
from aletheia.data.providers import OHLCVResult, parse_ohlcv_candle
from aletheia.data.sources.chain import FallbackChainProvider

_GET_OHLCV = CCXTProvider.get_ohlcv


def test_ohlcv_boundary_rejects_non_finite_and_impossible_rows() -> None:
    assert parse_ohlcv_candle(
        [1, 10, 11, 9, 10.5, 1], provider="fixture", exchange="fixture",
    ) is not None
    assert parse_ohlcv_candle(
        [1, 10, float("nan"), 9, 10.5, 1], provider="fixture", exchange="fixture",
    ) is None
    assert parse_ohlcv_candle(
        [1, 10, 9, 8, 10.5, 1], provider="fixture", exchange="fixture",
    ) is None
    assert parse_ohlcv_candle(
        [1, 10, 11, 9, 10.5, -1], provider="fixture", exchange="fixture",
    ) is None


@pytest.mark.anyio
async def test_empty_ohlcv_result_continues_fallback_with_attempt_facts() -> None:
    primary = AsyncMock()
    primary.get_ohlcv = AsyncMock(return_value=[])
    fallback = AsyncMock()
    fallback.get_ohlcv = AsyncMock(return_value=[
        parse_ohlcv_candle(
            [1, 10, 11, 9, 10.5, 1], provider="fallback", exchange="fallback",
        ),
    ])

    result = await FallbackChainProvider([primary, fallback]).get_ohlcv(
        "BTC/USDT", limit=1,
    )

    assert isinstance(result, OHLCVResult)
    assert result.outcome == "complete"
    assert result.accepted == 1
    assert [attempt.outcome for attempt in result.attempts] == ["empty", "complete"]


@pytest.mark.anyio
async def test_ccxt_boundary_reports_rejected_rows_as_partial(monkeypatch) -> None:
    exchange = AsyncMock()
    exchange.fetch_ohlcv = AsyncMock(return_value=[
        [1, 10, 11, 9, 10.5, 1],
        [2, 10, float("nan"), 9, 10.5, 1],
    ])
    provider = CCXTProvider(exchange="fixture", fallback_exchange="fallback")
    monkeypatch.setattr(provider, "_ensure_primary", lambda: exchange)
    monkeypatch.setattr(provider, "_persist", lambda *_args, **_kwargs: None)

    result = await _GET_OHLCV(provider, "BTC/USDT", limit=2, since=0)

    assert result.outcome == "partial"
    assert result.accepted == 1
    assert result.rejected == 1
    assert result.attempts[0].provider == "fixture"
    assert result.attempts[0].rejected == 1


@pytest.mark.anyio
async def test_failed_ohlcv_chain_preserves_last_error_and_attempt_facts() -> None:
    primary = AsyncMock()
    primary.get_ohlcv = AsyncMock(side_effect=RuntimeError("primary failed"))
    fallback = AsyncMock()
    fallback.get_ohlcv = AsyncMock(side_effect=ValueError("fallback failed"))

    with pytest.raises(ValueError, match="fallback failed") as exc_info:
        await FallbackChainProvider([primary, fallback]).get_ohlcv("BTC/USDT")

    attempts = exc_info.value.ohlcv_attempts
    assert [attempt.outcome for attempt in attempts] == ["failed", "failed"]
    assert [attempt.reason for attempt in attempts] == ["RuntimeError", "ValueError"]
