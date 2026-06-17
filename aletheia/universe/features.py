"""Feature Collector — batch tickers → volume pre-filter → concurrent OHLCV."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from aletheia.config.schema import AletheiaConfig
from aletheia.storage.models import UniverseFeatureSnapshot
from aletheia.storage import repositories as repo
from aletheia.trading.indicators import rsi, atr, sma

logger = logging.getLogger("aletheia.universe.features")

_OHLCV_CONCURRENCY = 10
_OHLCV_TIMEOUT = 8.0


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


async def _fetch_single_feature(
    exchange, asset: dict, ticker: dict, now: datetime, to_ccxt_perp,
) -> dict | None:
    """Fetch OHLCV + enrich for a single asset. Returns feature dict or None."""
    symbol = asset.get("symbol", "")
    asset_id = asset.get("id", "")
    is_perp = asset.get("market_type", "spot") == "perp"

    ccxt_sym = to_ccxt_perp(symbol) if is_perp else symbol

    quote_volume_24h = float(ticker.get("quoteVolume") or 0)
    bid = ticker.get("bid")
    ask = ticker.get("ask")
    spread = _spread_bps(
        float(bid) if bid else None,
        float(ask) if ask else None,
    )

    ohlcv_1h = []
    try:
        ohlcv_1h = await asyncio.wait_for(
            exchange.fetch_ohlcv(ccxt_sym, timeframe="1h", limit=30),
            timeout=_OHLCV_TIMEOUT,
        )
    except (asyncio.TimeoutError, Exception):
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

    data_freshness_sec = None
    if ohlcv_1h:
        last_ts = int(ohlcv_1h[-1][0]) / 1000
        data_freshness_sec = int(time.time() - last_ts)

    ob_row = repo.latest_orderbook(symbol)
    fr_row = repo.latest_funding_rate(symbol)
    oi_row = repo.latest_open_interest(symbol)

    depth_10bps = ob_row["total_depth_10bps_usd"] if ob_row else None
    funding_rate = fr_row["funding_rate"] if fr_row else None
    open_interest = oi_row["open_interest_usd"] if oi_row else None
    oi_delta_1h = oi_row["oi_change_1h_pct"] if oi_row else None

    if spread is None and ob_row:
        spread = ob_row.get("spread_bps")

    last_price = closes_1h[-1] if closes_1h else None

    feat = {
        "asset_id": asset_id,
        "symbol": symbol,
        "venue": asset.get("venue", ""),
        "market_type": asset.get("market_type", "spot"),
        "quote_volume_24h": quote_volume_24h,
        "volume_1h": volume_1h,
        "spread_bps": spread,
        "depth_10bps_usd": depth_10bps,
        "atr_pct": atr_val,
        "rsi_14": rsi_val,
        "return_1h": return_1h,
        "return_4h": return_4h,
        "return_24h": return_24h,
        "funding_rate": funding_rate,
        "open_interest": open_interest,
        "oi_delta_1h": oi_delta_1h,
        "data_freshness_sec": data_freshness_sec,
        "last_price": last_price,
    }

    snap = UniverseFeatureSnapshot(
        asset_id=asset_id,
        ts=now.isoformat(),
        quote_volume_24h=quote_volume_24h,
        volume_1h=volume_1h,
        spread_bps=spread,
        depth_10bps_usd=depth_10bps,
        atr_pct=atr_val,
        rsi_14=rsi_val,
        return_1h=return_1h,
        return_4h=return_4h,
        return_24h=return_24h,
        funding_rate=funding_rate,
        open_interest=open_interest,
        oi_delta_1h=oi_delta_1h,
        data_freshness_sec=data_freshness_sec,
    )
    repo.insert_universe_feature_snapshot(snap)

    return feat


async def collect_features(assets: list[dict], cfg: AletheiaConfig) -> list[dict]:
    """Two-phase feature collection: batch tickers → volume pre-filter → concurrent OHLCV.

    Phase A: fetch all tickers in batch (spot + perp separately) — ~2-4s total.
    Phase B: pre-filter by min_volume, then fetch OHLCV concurrently with semaphore.
    """
    from aletheia.data.ccxt_provider import CCXTProvider
    from aletheia.data.symbols import to_ccxt_perp

    venue = cfg.market_data.ccxt_exchange
    provider = CCXTProvider(exchange=venue, fallback_exchange=venue)
    results = []

    try:
        exchange = provider._ensure_primary()
        exchange.options["defaultType"] = "spot"
        exchange.options["fetchMarkets"] = ["spot", "linear"]
        await exchange.load_markets()

        # --- Phase A: batch ticker fetch (spot + perp) ---
        spot_assets = [a for a in assets if a.get("market_type", "spot") != "perp"]
        perp_assets = [a for a in assets if a.get("market_type", "spot") == "perp"]

        spot_symbols = list(dict.fromkeys(a["symbol"] for a in spot_assets if a.get("symbol")))
        perp_symbols = list(dict.fromkeys(to_ccxt_perp(a["symbol"]) for a in perp_assets if a.get("symbol")))

        tickers: dict = {}

        exchange.options["defaultType"] = "spot"
        try:
            tickers.update(await exchange.fetch_tickers(spot_symbols[:500]))
        except Exception as e:
            logger.warning("Batch spot ticker fetch failed: %s", e)

        if perp_symbols:
            exchange.options["defaultType"] = "swap"
            try:
                tickers.update(await exchange.fetch_tickers(perp_symbols[:300]))
            except Exception as e:
                logger.warning("Batch perp ticker fetch failed: %s", e)

        # --- Volume pre-filter ---
        min_vol = cfg.universe.min_volume_24h_usdt
        candidates = []
        for asset in assets:
            symbol = asset.get("symbol", "")
            asset_id = asset.get("id", "")
            if not symbol or not asset_id:
                continue

            is_perp = asset.get("market_type", "spot") == "perp"
            ccxt_sym = to_ccxt_perp(symbol) if is_perp else symbol
            ticker = tickers.get(ccxt_sym)

            if ticker is None:
                continue

            vol = float(ticker.get("quoteVolume") or 0)
            if vol < min_vol:
                continue

            candidates.append((asset, ticker))

        logger.info(
            "Volume pre-filter: %d/%d assets pass (min_vol=%.0f USDT)",
            len(candidates), len(assets), min_vol,
        )

        # --- Phase B: concurrent OHLCV fetch ---
        now = datetime.now(timezone.utc)
        sem = asyncio.Semaphore(_OHLCV_CONCURRENCY)

        async def _guarded_fetch(asset_ticker_pair):
            asset, ticker = asset_ticker_pair
            async with sem:
                try:
                    return await _fetch_single_feature(
                        exchange, asset, ticker, now, to_ccxt_perp,
                    )
                except Exception as e:
                    logger.debug("Feature collect failed for %s: %s", asset.get("symbol"), e)
                    return None

        gathered = await asyncio.gather(
            *[_guarded_fetch(pair) for pair in candidates],
            return_exceptions=True,
        )

        for result in gathered:
            if result is not None and not isinstance(result, BaseException):
                results.append(result)

    finally:
        await provider.close()

    logger.info("Features collected for %d / %d assets", len(results), len(assets))
    return results
