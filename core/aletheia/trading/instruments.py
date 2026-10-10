from aletheia.config.exchange_alignment import market_data_exchange
import logging
"""Tradable instrument definitions and timeframes (guide §3.2, §22.1)."""

from aletheia.data.symbols import InstrumentId, to_canonical, MARKET_PERP

logger = logging.getLogger("aletheia.trading.instruments")


# Market types
MARKET_USDT_FUTURES = "usdt_futures"


def instrument_market_to_db_market(market_type: str) -> str:
    """InstrumentId.market_type ("perp") → DB/execution layer string ("usdt_futures")."""
    return MARKET_USDT_FUTURES if market_type == MARKET_PERP else market_type

# Canonical timeframes (used by data layer and agent reasoning)
TIMEFRAMES = ["5m", "15m", "1h", "4h", "1d"]

def observed_instruments_from_config(cfg) -> list[InstrumentId]:
    """Return the full InstrumentId list for market data collection.

    If observed_instruments is configured (non-empty), parses and returns them.
    Otherwise derives one perp instrument per observed_symbols entry.

    Invalid entries are silently skipped — callers get a best-effort list.
    """
    if cfg.market_data.observed_instruments:
        result = []
        for s in cfg.market_data.observed_instruments:
            try:
                result.append(InstrumentId.parse(s))
            except ValueError:
                logger.warning("trading_instruments: observed-instrument spec failed to parse; instrument skipped from collection", exc_info=True)
        return result
    # Legacy path: observed_symbols on the market-data venue. USDT perpetuals are
    # the only product (CAPABILITY_GROUND_TRUTH), so each symbol is one perp.
    exchange = market_data_exchange(cfg)
    return [
        InstrumentId.from_parts(exchange, sym, MARKET_PERP)
        for sym in cfg.market_data.observed_symbols
    ]


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
    policy = cfg.risk.live if is_live else cfg.risk.demo
    tier = get_asset_tier(symbol)
    account_cap = policy.leverage.futures_cap
    if tier == "major":
        return min(policy.leverage.major_cap, account_cap)
    if tier == "mid":
        return min(policy.leverage.mid_cap, account_cap)
    return min(policy.leverage.alt_cap, account_cap)


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

