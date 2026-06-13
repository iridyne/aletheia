"""Feature Collector — fetch tickers + OHLCV and compute cheap features per asset."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from aletheia.config.schema import AletheiaConfig
from aletheia.storage.models import UniverseFeatureSnapshot
from aletheia.storage import repositories as repo
from aletheia.trading.indicators import rsi, atr, sma

logger = logging.getLogger("aletheia.universe.features")


def _pct_return(closes: list[float], bars: int) -> float | None:
    """Percentage return over the last N bars."""
    if len(closes) < bars + 1:
        return None
    old = closes[-(bars + 1)]
    if old == 0:
        return None
    return ((closes[-1] - old) / old) * 100.0


def _atr_pct(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float | None:
    """ATR as a percentage of the last close."""
    atr_val = atr(highs, lows, closes, period)
    if atr_val is None or not closes or closes[-1] == 0:
        return None
    return (atr_val / closes[-1]) * 100.0


def _spread_bps(bid: float | None, ask: float | None) -> float | None:
    """Spread in basis points from ticker bid/ask."""
    if bid is None or ask is None or bid <= 0:
        return None
    return ((ask - bid) / bid) * 10000.0


async def collect_features(assets: list[dict], cfg: AletheiaConfig) -> list[dict]:
    """Collect features for all assets using batch ticker fetch + individual OHLCV.

    Returns list of dicts with feature fields + asset metadata.
    Skips assets that fail to fetch (never crashes).
    """
    from aletheia.data.ccxt_provider import CCXTProvider

    venue = cfg.market_data.ccxt_exchange
    provider = CCXTProvider(exchange=venue, fallback_exchange=venue)
    results = []

    try:
        exchange = provider._ensure_primary()
        await exchange.load_markets()

        # Batch fetch all tickers at once (much faster than per-symbol)
        symbols_to_fetch = [a["symbol"] for a in assets if a.get("symbol")]
        ccxt_symbols = []
        symbol_map: dict[str, str] = {}
        for sym in symbols_to_fetch:
            ccxt_sym = sym  # canonical BTC/USDT is CCXT spot format
            ccxt_symbols.append(ccxt_sym)
            symbol_map[ccxt_sym] = sym

        tickers: dict = {}
        try:
            tickers = await exchange.fetch_tickers(ccxt_symbols[:200])
        except Exception as e:
            logger.warning("Batch ticker fetch failed, will try individually: %s", e)

        now = datetime.now(timezone.utc)

        for asset in assets:
            symbol = asset.get("symbol", "")
            asset_id = asset.get("id", "")
            if not symbol or not asset_id:
                continue

            try:
                # Get ticker data
                ticker = tickers.get(symbol)
                if ticker is None:
                    try:
                        ticker = await exchange.fetch_ticker(symbol)
                    except Exception:
                        continue

                quote_volume_24h = float(ticker.get("quoteVolume") or 0)
                bid = ticker.get("bid")
                ask = ticker.get("ask")
                spread = _spread_bps(
                    float(bid) if bid else None,
                    float(ask) if ask else None,
                )

                # Fetch 1h OHLCV (24 bars for indicators)
                ohlcv_1h = []
                try:
                    ohlcv_1h = await exchange.fetch_ohlcv(symbol, timeframe="1h", limit=30)
                except Exception:
                    pass

                closes_1h = [float(c[4]) for c in ohlcv_1h] if ohlcv_1h else []
                highs_1h = [float(c[2]) for c in ohlcv_1h] if ohlcv_1h else []
                lows_1h = [float(c[3]) for c in ohlcv_1h] if ohlcv_1h else []
                volumes_1h = [float(c[5]) for c in ohlcv_1h] if ohlcv_1h else []

                rsi_val = rsi(closes_1h, 14) if len(closes_1h) >= 15 else None
                atr_val = _atr_pct(highs_1h, lows_1h, closes_1h) if len(closes_1h) >= 15 else None
                return_1h = _pct_return(closes_1h, 1)
                return_4h = _pct_return(closes_1h, 4)
                return_24h = _pct_return(closes_1h, 24)
                volume_1h = volumes_1h[-1] * closes_1h[-1] if volumes_1h and closes_1h else None

                # Data freshness: how old is the latest candle
                data_freshness_sec = None
                if ohlcv_1h:
                    last_ts = int(ohlcv_1h[-1][0]) / 1000
                    data_freshness_sec = int(time.time() - last_ts)

                feat = {
                    "asset_id": asset_id,
                    "symbol": symbol,
                    "venue": asset.get("venue", ""),
                    "market_type": asset.get("market_type", "spot"),
                    "quote_volume_24h": quote_volume_24h,
                    "volume_1h": volume_1h,
                    "spread_bps": spread,
                    "depth_10bps_usd": None,  # requires orderbook fetch, skip in MVP
                    "atr_pct": atr_val,
                    "rsi_14": rsi_val,
                    "return_1h": return_1h,
                    "return_4h": return_4h,
                    "return_24h": return_24h,
                    "funding_rate": None,  # perp-only, future enhancement
                    "open_interest": None,
                    "oi_delta_1h": None,
                    "data_freshness_sec": data_freshness_sec,
                }

                # Persist feature snapshot
                snap = UniverseFeatureSnapshot(
                    asset_id=asset_id,
                    ts=now.isoformat(),
                    quote_volume_24h=quote_volume_24h,
                    volume_1h=volume_1h,
                    spread_bps=spread,
                    atr_pct=atr_val,
                    rsi_14=rsi_val,
                    return_1h=return_1h,
                    return_4h=return_4h,
                    return_24h=return_24h,
                    data_freshness_sec=data_freshness_sec,
                )
                repo.insert_universe_feature_snapshot(snap)

                results.append(feat)
            except Exception as e:
                logger.debug("Feature collect failed for %s: %s", symbol, e)
                continue

    finally:
        await provider.close()

    logger.info("Features collected for %d / %d assets", len(results), len(assets))
    return results
