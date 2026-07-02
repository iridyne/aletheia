"""Tradable instrument definitions and timeframes (guide §3.2, §22.1)."""

from aletheia.data.symbols import (
    InstrumentId, to_canonical, MARKET_SPOT, MARKET_PERP,
    stock_token_exchange, is_stock_token,
)

# MVP tradable symbols — Bitget execution format (used by risk engine + order router)
TRADABLE_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

# Market types
MARKET_USDT_FUTURES = "usdt_futures"


def instrument_market_to_db_market(market_type: str) -> str:
    """InstrumentId.market_type ("perp") → DB/execution layer string ("usdt_futures")."""
    return MARKET_USDT_FUTURES if market_type == MARKET_PERP else market_type

# Canonical timeframes (used by data layer and agent reasoning)
TIMEFRAMES = ["5m", "15m", "1h", "4h", "1d"]

# Bitget-specific granularity strings — execution-context only.
# New code should use canonical timeframes ('1h', '4h', '1d') via data.symbols.
BITGET_GRANULARITY = {
    "5m": "5min",
    "15m": "15min",
    "1h": "1h",
    "4h": "4h",
    "1d": "1day",
}


def tradable_symbols_from_config(cfg) -> list[str]:
    """Return the symbol_list from exchange config in CANONICAL format (BTC/USDT).

    Previously returned the raw config list (which may hold Bitget-format
    'BTCUSDT'); consumers like paper_trader_ranker then had to to_canonical()
    each entry, and any that forgot (strategy_designer) silently mismatched the
    canonical opportunities/positions and dropped symbols at the filter. Now
    normalised at the source so every consumer gets canonical form. to_canonical
    is idempotent, so consumers that still call it are unaffected.
    (CR-DL-01 / data_layer Phase 3.6, 2026-06-23.)
    """
    return [to_canonical(s) for s in cfg.exchange.symbol_list]


def observed_symbols_from_config(cfg) -> list[str]:
    """Return the market data observation list (canonical BTC/USDT format).

    If observed_instruments is configured, derives symbol list from it.
    Otherwise returns observed_symbols directly (legacy path).
    """
    if cfg.market_data.observed_instruments:
        seen: dict[str, None] = {}
        for s in cfg.market_data.observed_instruments:
            try:
                iid = InstrumentId.parse(s)
                seen[iid.symbol] = None
            except (ValueError, Exception):
                pass
        return list(seen)
    return cfg.market_data.observed_symbols


def stock_tokens_from_config(cfg) -> list[InstrumentId]:
    """Return all configured tokenized equity/ETF instruments as InstrumentId list.

    Reads ``observed_stock_tokens`` (new field) and falls back to the deprecated
    ``observed_xstocks`` so existing configs continue to work.

    Exchange is inferred via ``stock_token_exchange()`` — Bitget r-series and
    ON-series resolve to "bitget"; future OKX SWAP entries resolve to "okx".
    This makes the function exchange-agnostic: new token series on any exchange
    only need a registry entry in ``symbols.py``.

    Invalid / non-token entries are silently skipped so a bad config line does
    not break the entire collection run.
    """
    tokens = list(getattr(cfg.market_data, "observed_stock_tokens", None) or [])
    # Backward-compat: merge observed_xstocks into the list (deduplicated).
    legacy = list(getattr(cfg.market_data, "observed_xstocks", None) or [])
    seen: set[str] = set()
    merged: list[str] = []
    for s in tokens + legacy:
        key = to_canonical(s)
        if key and key not in seen:
            seen.add(key)
            merged.append(s)

    result = []
    for s in merged:
        if not is_stock_token(s):
            continue
        exchange = stock_token_exchange(s)
        try:
            result.append(InstrumentId.from_parts(exchange, s, MARKET_SPOT))
        except (ValueError, Exception):
            pass
    return result


def xstocks_instruments_from_config(cfg) -> list[InstrumentId]:
    """Deprecated: use stock_tokens_from_config().

    Kept for call sites that were written before r-series support was added.
    Returns the same list as stock_tokens_from_config() — all stock tokens,
    not just ON-series — since callers use the result identically.
    """
    return stock_tokens_from_config(cfg)


def observed_instruments_from_config(cfg) -> list[InstrumentId]:
    """Return the full InstrumentId list for market data collection.

    If observed_instruments is configured (non-empty), parses and returns them.
    Otherwise derives from observed_symbols + ccxt_exchange (legacy path).
    When usdt_futures is enabled, the legacy path emits both spot AND perp
    instruments so the data pipeline sees futures symbols.

    Invalid entries are silently skipped — callers get a best-effort list.
    """
    if cfg.market_data.observed_instruments:
        result = []
        for s in cfg.market_data.observed_instruments:
            try:
                result.append(InstrumentId.parse(s))
            except (ValueError, Exception):
                pass
        return result
    # Legacy path: symbols + exchange from config.
    # Dedup: when both spot and usdt_futures are enabled, emit ONE instrument per
    # canonical symbol using cfg.exchange.preferred_market_type. Directional
    # analysis (market_observer LLM read) is instrument-agnostic — the same ETH
    # read 76% long whether the instrument is spot or perp (price ≈ identical).
    # Emitting two would call the LLM twice for the same signal and downstream
    # consumers (paper_trader, PI) could open duplicate positions. OD-S01 locks.
    exchange = cfg.market_data.ccxt_exchange or "binance"
    preferred = getattr(cfg.exchange, "preferred_market_type", "auto")
    result = []
    for sym in cfg.market_data.observed_symbols:
        spot_ok = cfg.exchange.enabled_markets.get("spot", True)
        perp_ok = cfg.exchange.enabled_markets.get("usdt_futures", False)
        if spot_ok and perp_ok:
            # Both enabled — pick one by configured preference ("auto" → perp).
            mt = MARKET_SPOT if preferred == "spot" else MARKET_PERP
            result.append(InstrumentId.from_parts(exchange, sym, mt))
        elif spot_ok:
            result.append(InstrumentId.from_parts(exchange, sym, MARKET_SPOT))
        elif perp_ok:
            result.append(InstrumentId.from_parts(exchange, sym, MARKET_PERP))
    return result


# ---------------------------------------------------------------------------
# Futures helpers: asset tiers, leverage limits, contract sizing
# ---------------------------------------------------------------------------

MAJOR_BASES = frozenset({"BTC", "ETH"})
MID_CAP_BASES = frozenset({
    "BNB", "SOL", "XRP", "ADA", "DOGE", "AVAX", "DOT", "MATIC", "LINK",
    "UNI", "ATOM", "LTC", "FIL", "APT", "SUI", "ARB", "OP", "INJ",
    "TIA", "JUP", "WIF", "NEAR", "SAND", "MANA", "RUNE",
})

FUTURES_CT_VAL: dict[str, float] = {
    "BTC/USDT": 0.001, "ETH/USDT": 0.01, "SOL/USDT": 1.0,
    "BNB/USDT": 0.01, "XRP/USDT": 10.0, "DOGE/USDT": 100.0,
}

FUTURES_MMR: dict[str, float] = {
    "BTC/USDT": 0.004, "ETH/USDT": 0.004, "SOL/USDT": 0.005,
    "BNB/USDT": 0.005, "XRP/USDT": 0.006, "DOGE/USDT": 0.008,
}


def get_asset_tier(symbol: str) -> str:
    """Return "major", "mid", or "alt" for a canonical symbol."""
    base = to_canonical(symbol).split("/")[0]
    if base in MAJOR_BASES:
        return "major"
    if base in MID_CAP_BASES:
        return "mid"
    return "alt"


def get_max_leverage(symbol: str, is_live: bool, cfg) -> int:
    """Return the per-asset-tier maximum leverage from config."""
    risk = cfg.risk
    tier = get_asset_tier(symbol)
    if is_live:
        account_cap = risk.max_futures_leverage_live
        if tier == "major":
            return min(risk.max_leverage_major_live, account_cap)
        if tier == "mid":
            return min(risk.max_leverage_mid_live, account_cap)
        return min(risk.max_leverage_alt_live, account_cap)
    else:
        account_cap = risk.max_futures_leverage_demo
        if tier == "major":
            return min(risk.max_leverage_major_demo, account_cap)
        if tier == "mid":
            return min(risk.max_leverage_mid_demo, account_cap)
        return min(risk.max_leverage_alt_demo, account_cap)


def get_ct_val(symbol: str) -> float:
    """Return contract value (coins per 1 contract) for a symbol. Defaults to 1.0."""
    return FUTURES_CT_VAL.get(to_canonical(symbol), 1.0)


def notional_to_contracts(notional_usdt: float, price: float, symbol: str) -> float:
    """Convert USDT notional to contract count for Bitget USDT-M futures."""
    ct_val = get_ct_val(symbol)
    if price <= 0:
        return 0.0
    coins = notional_usdt / price
    return coins / ct_val


def contracts_to_notional(contracts: float, price: float, symbol: str) -> float:
    """Convert contract count to USDT notional."""
    ct_val = get_ct_val(symbol)
    return abs(contracts) * ct_val * price


def calc_liquidation_price(
    entry_price: float, direction: str, leverage: int, symbol: str
) -> float | None:
    """Estimate liquidation price (isolated margin, USDT-M).

    Returns None if leverage <= 1 (spot-equivalent, no liquidation).
    """
    if leverage <= 1 or entry_price <= 0:
        return None
    mmr = FUTURES_MMR.get(to_canonical(symbol), 0.005)
    if direction in ("long", "buy"):
        return entry_price * (1.0 - 1.0 / leverage + mmr)
    else:
        return entry_price * (1.0 + 1.0 / leverage - mmr)


def futures_instruments_from_config(cfg) -> list[InstrumentId]:
    """Return InstrumentId list for configured futures symbols."""
    exchange = cfg.market_data.ccxt_exchange or "binance"
    symbols = getattr(cfg.exchange, "futures_symbols", []) or []
    return [InstrumentId.from_parts(exchange, sym, MARKET_PERP) for sym in symbols]
