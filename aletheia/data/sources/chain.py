"""FallbackChainProvider — tries providers in order, returns first success.

NotImplementedError is treated as "provider doesn't support this" and skips
silently to the next. All other exceptions are logged at debug level and retried
on the next provider. Only after all providers fail is the last exception raised.

When the primary provider fails and a fallback succeeds, an operator notification
is fired (configurable via ``price_divergence_alert_enabled``). An optional
cross-source check fetches from a second provider to detect price divergence.
"""

from __future__ import annotations

import asyncio
import logging

from aletheia.data.providers import Candle, Ticker
from aletheia.data.symbols import InstrumentId, MARKET_SPOT

logger = logging.getLogger("aletheia.data.sources.chain")

_FALLBACK_DEDUP_SEC = 300.0
_last_fallback_alert: dict[str, float] = {}


def _should_alert(symbol: str) -> bool:
    """Rate-limit fallback alerts to one per symbol per 5 minutes."""
    import time

    now = time.monotonic()
    last = _last_fallback_alert.get(symbol)
    if last is not None and now - last < _FALLBACK_DEDUP_SEC:
        return False
    _last_fallback_alert[symbol] = now
    return True


def _load_divergence_config() -> tuple[bool, bool, float]:
    """Return (alert_enabled, cross_check_enabled, threshold_pct)."""
    try:
        from aletheia.config.loader import load_config

        md = load_config().market_data
        return (
            getattr(md, "price_divergence_alert_enabled", True),
            getattr(md, "cross_source_check_enabled", False),
            getattr(md, "price_divergence_threshold_pct", 2.0),
        )
    except Exception:
        return True, False, 2.0


class FallbackChainProvider:
    """Compose N MarketDataProviders; tries each in order, returns first success."""

    def __init__(self, providers: list) -> None:
        if not providers:
            raise ValueError("FallbackChainProvider requires at least one provider")
        self._providers = providers

    @property
    def _primary_name(self) -> str:
        return getattr(self._providers[0], "_primary_name", "unknown")

    def _ensure_primary(self):
        return self._providers[0]._ensure_primary()

    def _eligible_providers(self, symbol):
        """Never satisfy a derivative request with a spot-only fallback.

        The first provider is CCXT, whose own primary/fallback exchanges preserve
        InstrumentId.market_type. CoinGecko/BinancePublic are spot adapters; using
        them after a perpetual miss silently changes the instrument being priced.
        """
        providers = self._providers
        if isinstance(symbol, InstrumentId) and symbol.market_type != MARKET_SPOT:
            providers = providers[:1]

        from aletheia.data.venue_health import is_venue_available

        eligible = []
        for provider in providers:
            # Read only a provider class declaration. Mock instances fabricate
            # arbitrary attributes, which must not turn them into a venue adapter.
            venue = getattr(type(provider), "venue_name", None)
            if isinstance(venue, str) and venue and not is_venue_available(venue):
                logger.info("market-data fallback skipping cooling venue %s", venue)
                continue
            eligible.append(provider)
        return eligible

    async def get_ticker(self, symbol) -> Ticker:
        last_err: Exception | None = None
        result: Ticker | None = None
        fallback_used = False

        for idx, provider in enumerate(self._eligible_providers(symbol)):
            try:
                result = await provider.get_ticker(symbol)
                if idx > 0:
                    fallback_used = True
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                logger.warning(
                    "ticker fallback: %s failed for %s: %s",
                    type(provider).__name__,
                    symbol,
                    e,
                )

        if result is None:
            raise last_err if last_err else RuntimeError("all providers failed")

        alert_enabled, cross_check, threshold = _load_divergence_config()

        if fallback_used and alert_enabled and _should_alert(str(symbol)):
            try:
                from aletheia.notifications import notify_operator

                notify_operator(
                    "Data fallback active",
                    f"{symbol}: primary failed, using {result.provider or 'fallback'}",
                    level="warning",
                    source="price_fallback",
                    event_type="price_fallback",
                    entity_type="market_data",
                    entity_id=str(symbol),
                )
            except Exception:  # noqa: BLE001
                pass

        if cross_check and result and not fallback_used and len(self._providers) > 1:
            await self._cross_source_check(symbol, result, threshold)

        return result

    async def _cross_source_check(
        self, symbol, primary_result: Ticker, threshold_pct: float
    ) -> None:
        """Best-effort: fetch from second provider, compare, alert on divergence."""
        try:
            secondary = await asyncio.wait_for(
                self._providers[1].get_ticker(symbol), timeout=5.0
            )
            if not secondary or not secondary.last_price or not primary_result.last_price:
                return
            delta_pct = abs(
                (primary_result.last_price - secondary.last_price)
                / primary_result.last_price
                * 100
            )
            if delta_pct > threshold_pct and _should_alert(f"div:{symbol}"):
                from aletheia.notifications import notify_operator

                notify_operator(
                    "Price divergence detected",
                    f"{symbol}: {delta_pct:.1f}% gap — "
                    f"{primary_result.provider}={primary_result.last_price:.2f} vs "
                    f"{secondary.provider}={secondary.last_price:.2f}",
                    level="warning",
                    source="price_divergence",
                    event_type="price_divergence_alert",
                    entity_type="market_data",
                    entity_id=str(symbol),
                )
                logger.warning(
                    "price divergence %s: %.1f%% (%s=%.2f vs %s=%.2f)",
                    symbol,
                    delta_pct,
                    primary_result.provider,
                    primary_result.last_price,
                    secondary.provider,
                    secondary.last_price,
                )
        except Exception:  # noqa: BLE001
            pass

    async def get_ohlcv(
        self,
        symbol,
        timeframe: str = "1h",
        limit: int = 100,
        since: int | None = None,
    ) -> list[Candle]:
        last_err: Exception | None = None
        for provider in self._eligible_providers(symbol):
            try:
                return await provider.get_ohlcv(
                    symbol, timeframe=timeframe, limit=limit, since=since
                )
            except NotImplementedError:
                continue
            except Exception as e:  # noqa: BLE001
                last_err = e
                logger.debug(
                    "ohlcv fallback: %s failed for %s/%s: %s",
                    type(provider).__name__,
                    symbol,
                    timeframe,
                    e,
                )
        if last_err:
            raise last_err
        raise RuntimeError(f"no provider supports {timeframe} candles for {symbol}")

    async def close(self) -> None:
        for provider in self._providers:
            try:
                await provider.close()
            except Exception:  # noqa: BLE001
                pass
