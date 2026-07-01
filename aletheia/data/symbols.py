"""Canonical symbol format + per-venue converters + InstrumentId identity.

Canonical form is CCXT spot: ``BASE/QUOTE`` upper-case (e.g. ``BTC/USDT``). This
is the ONLY format used internally (storage, risk, PnL, positions, opportunities).
Venue-specific forms appear ONLY at an adapter boundary:
  - Bitget execution REST wants the concatenated ``BTCUSDT`` (``to_bitget``).
  - CCXT perp/futures want a settle suffix ``BTC/USDT:USDT`` (``to_ccxt_perp``).
  - yFinance wants ``BTC-USD`` with USDT/USDC mapped to USD (``to_yfinance``).

``InstrumentId`` (exchange:symbol:market_type) is the unique instrument identity so
the same symbol on different exchanges / market types never collides in the DB.
"""

from __future__ import annotations

from dataclasses import dataclass

# Market type constants.
MARKET_SPOT = "spot"
MARKET_PERP = "perp"
MARKET_FUTURES = "futures"

_VALID_MARKET_TYPES = {MARKET_SPOT, MARKET_PERP, MARKET_FUTURES}

# Known multi-character quote currencies, longest-first so USDT matches before USD.
# Used to split a separator-less venue symbol (BTCUSDT) into base/quote.
_KNOWN_QUOTES = ("USDT", "USDC", "USD", "BTC", "ETH", "EUR", "DAI", "TUSD")


def _split_base_quote(token: str) -> tuple[str, str]:
    """Split an upper-cased separator-less symbol (e.g. 'BTCUSDT') into (base, quote).

    Falls back to a trailing-USDT assumption when no known quote matches.
    """
    for q in _KNOWN_QUOTES:
        if token.endswith(q) and len(token) > len(q):
            return token[: -len(q)], q
    # Unknown quote — best effort: assume USDT tail if present, else whole token.
    return token, ""


def to_canonical(symbol: str) -> str:
    """Normalise any symbol format to canonical ``BASE/QUOTE`` (upper-case, slash).

    Handles already-canonical (BTC/USDT), Bitget concatenated (BTCUSDT), dash
    (BTC-USDT), CCXT perp suffix (BTC/USDT:USDT → strip suffix) and lower-case.
    """
    if symbol is None:
        return ""
    s = symbol.strip().upper()
    if not s:
        return ""
    # Strip CCXT perp/futures settle suffix: "BTC/USDT:USDT" -> "BTC/USDT".
    if ":" in s:
        s = s.split(":", 1)[0]
    # Already has an explicit separator.
    if "/" in s:
        base, _, quote = s.partition("/")
        return f"{base}/{quote}"
    if "-" in s:
        base, _, quote = s.partition("-")
        return f"{base}/{quote}"
    # Separator-less venue form (BTCUSDT).
    base, quote = _split_base_quote(s)
    if quote:
        return f"{base}/{quote}"
    return s


def to_bitget(symbol: str) -> str:
    """Convert any format to Bitget execution form (concatenated, upper-case)."""
    canon = to_canonical(symbol)
    return canon.replace("/", "")


def to_binance(symbol: str) -> str:
    """Convert any format to Binance execution form (concatenated, upper-case).

    Binance Futures uses the same concatenated format as Bitget (BTCUSDT).
    Kept as a separate function for semantic clarity and future divergence.
    """
    canon = to_canonical(symbol)
    return canon.replace("/", "")


def to_ccxt(symbol: str) -> str:
    """Convert any format to canonical CCXT spot symbol (alias of to_canonical)."""
    return to_canonical(symbol)


def to_ccxt_perp(symbol: str) -> str:
    """Convert to CCXT perp/futures form with settle suffix: ``BTC/USDT:USDT``.

    Settle currency is the quote currency (USDT-margined / USDC-margined), so the
    suffix mirrors the quote directly — no USD mapping.
    """
    canon = to_canonical(symbol)
    _, _, quote = canon.partition("/")
    return f"{canon}:{quote}" if quote else canon


def to_yfinance(symbol: str) -> str:
    """Convert to Yahoo Finance form: ``BASE-QUOTE`` with USDT/USDC mapped to USD."""
    canon = to_canonical(symbol)
    base, _, quote = canon.partition("/")
    if quote in ("USDT", "USDC"):
        quote = "USD"
    return f"{base}-{quote}" if quote else base


# ---------------------------------------------------------------------------
# Generic stock token registry
# ---------------------------------------------------------------------------
# Explicit registry for tokenized equity tokens where the name does NOT follow
# a simple pattern (ON-series, ETFs with non-obvious tickers, future OKX SWAP,
# etc.).  Maps canonical_symbol → (equity_ticker, exchange).
#
# Bitget r-series tokens (rNVDA/USDT, rTSLA/USDT …) are handled automatically
# by the _is_r_series_base() rule — no need to enumerate all 113+ entries here.
# Add explicit entries only for tokens that break the rule or live on a
# different exchange.
_STOCK_TOKEN_REGISTRY: dict[str, tuple[str, str]] = {
    # Bitget ON-series
    "AAPLON/USDT": ("AAPL", "bitget"),
    "TSLAON/USDT": ("TSLA", "bitget"),
    "NVDAON/USDT": ("NVDA", "bitget"),
    "AMZNON/USDT": ("AMZN", "bitget"),
    "MSFTON/USDT": ("MSFT", "bitget"),
    "METAON/USDT": ("META", "bitget"),
    "GOOGLON/USDT": ("GOOGL", "bitget"),
    "GOOGLBON/USDT": ("GOOG", "bitget"),
    "AMDON/USDT": ("AMD", "bitget"),
    "NFLXON/USDT": ("NFLX", "bitget"),
    # Bitget ON-series ETFs / commodity ETFs
    "SPYON/USDT": ("SPY", "bitget"),
    "QQQON/USDT": ("QQQ", "bitget"),
    "IVVON/USDT": ("IVV", "bitget"),
    "ITOTON/USDT": ("ITOT", "bitget"),
    "IWMON/USDT": ("IWM", "bitget"),
    "IAON/USDT": ("IAU", "bitget"),
    "SLVON/USDT": ("SLV", "bitget"),
    # Future OKX SWAP (instCategory=3) — add entries here as they are integrated,
    # e.g. "AAPL/USDT": ("AAPL", "okx") once the OKX stock-SWAP adapter exists.
}

# Market-type overrides for stock tokens that are NOT plain spot.
# All entries in _STOCK_TOKEN_REGISTRY default to MARKET_SPOT ("spot").
# When OKX SWAP stock tokens are added to the registry, register their canonical
# symbols here too so stock_token_market_type() returns the correct value.
# Format: canonical_symbol → market_type string (matches InstrumentId._VALID_MARKET_TYPES).
#
# Example for future OKX SWAP:
#   "AAPL/USDT": "swap",    # perpetual SWAP on OKX
#   "AAPL-USD-XPERP": "perp",
_STOCK_TOKEN_NON_SPOT: dict[str, str] = {
    # (currently empty — all known Bitget r-series and ON-series are spot)
}

# Backward-compat view of the ON-series registry (base token → ticker).
# Used only by the deprecated xstock_to_ticker / ticker_to_xstock functions.
_XSTOCK_BASE_TO_TICKER: dict[str, str] = {
    sym.split("/")[0]: ticker
    for sym, (ticker, _) in _STOCK_TOKEN_REGISTRY.items()
    if sym.endswith("/USDT")
}


def _is_r_series_base(base: str) -> bool:
    """True for Bitget r-series token base coins: lowercase 'r' + 2-5 uppercase chars.

    Examples: 'rNVDA' (True), 'rTSLA' (True), 'rT' (False — too short),
    'rTMUS' (True), 'rCMCSA' (False — 5 uppercase chars after 'r' → would be 6 → actually
    CMCSA is 5 chars → True).  The rule mirrors Bitget's naming convention where the
    base coin is a literal 'r' prepended to the standard stock/ETF ticker.
    """
    if not base or base[0] != "r":
        return False
    stem = base[1:]
    return stem.isupper() and 2 <= len(stem) <= 6


def stock_token_to_ticker(symbol: str) -> str | None:
    """Generic: map any exchange stock token symbol → equity ticker.

    Resolution order:
    1. Explicit ``_STOCK_TOKEN_REGISTRY`` (ON-series, OKX SWAP, manual overrides).
    2. Bitget r-series rule: strip leading 'r' → ticker ('rNVDA/USDT' → 'NVDA').

    Returns None when the symbol is not a recognised stock token.

    Note: to_canonical() uppercases everything, destroying the lowercase 'r'
    prefix used by the r-series rule.  We therefore extract the original base
    from the raw symbol (before uppercasing) to preserve case for the rule check.
    """
    canon = to_canonical(symbol)
    entry = _STOCK_TOKEN_REGISTRY.get(canon)
    if entry:
        return entry[0]
    # Extract original base preserving case ('rNVDA/USDT' → 'rNVDA').
    raw_base = symbol.strip().split("/")[0].split(":")[0]
    if _is_r_series_base(raw_base):
        return raw_base[1:].upper()
    return None


def stock_token_exchange(symbol: str) -> str:
    """Get the exchange that lists this stock token.

    Currently all known tokens (ON-series + r-series) are on Bitget.
    Extend ``_STOCK_TOKEN_REGISTRY`` with ``(ticker, "okx")`` entries when OKX
    SWAP stock tokens are integrated; this function reads them transparently.
    """
    canon = to_canonical(symbol)
    entry = _STOCK_TOKEN_REGISTRY.get(canon)
    if entry:
        return entry[1]
    return "bitget"  # default: r-series tokens live on Bitget


def is_stock_token(symbol: str) -> bool:
    """Return True if symbol is a tokenized equity/ETF on any supported exchange."""
    return stock_token_to_ticker(symbol) is not None


def stock_token_market_type(symbol: str) -> str:
    """Return the market type for a stock token symbol.

    All current Bitget r-series and ON-series tokens are spot (cash-settled,
    no funding rate, no liquidation).  Future OKX SWAP tokens are perpetual
    swaps — register them in ``_STOCK_TOKEN_NON_SPOT`` to override.

    Returns MARKET_SPOT ("spot") for any unregistered / unknown symbol so callers
    always get a valid string (never None).

    Use this when building InstrumentId or deciding the display convention:
      - spot → keep slash  (BTC/USDT  →  BTC/USDT)
      - swap/perp → drop slash  (AAPL/USDT  →  AAPLUSDT)
    """
    canon = to_canonical(symbol)
    return _STOCK_TOKEN_NON_SPOT.get(canon, MARKET_SPOT)


def ticker_to_stock_tokens(ticker: str) -> list[str]:
    """All stock token canonical symbols available for an equity ticker.

    Returns tokens in priority order: highest-liquidity first.
    Currently: Bitget r-series (high liquidity, ~$10B/day) before ON-series.

    Example: 'NVDA' → ['rNVDA/USDT', 'NVDAON/USDT']
    """
    t = ticker.strip().upper()
    results: list[str] = []
    # r-series (primary, highest liquidity): rNVDA/USDT
    if _is_r_series_base(f"r{t}"):
        results.append(f"r{t}/USDT")
    # Registry entries (ON-series, OKX SWAP, etc.)
    for sym, (sym_ticker, _) in _STOCK_TOKEN_REGISTRY.items():
        if sym_ticker == t and sym not in results:
            results.append(sym)
    return results


# ---------------------------------------------------------------------------
# Backward-compatibility aliases (ON-series original API)
# ---------------------------------------------------------------------------

def xstock_to_ticker(symbol: str) -> str | None:
    """Deprecated: use stock_token_to_ticker().  Maps any stock token → equity ticker."""
    return stock_token_to_ticker(symbol)


def ticker_to_xstock(ticker: str) -> str | None:
    """Deprecated: use ticker_to_stock_tokens().

    Returns the Bitget ON-series symbol for a ticker ('AAPL' → 'AAPLON/USDT'), or
    None if no ON-series exists.  Preserves the old behaviour of preferring ON-series
    over r-series for callers that were written before r-series support was added.
    """
    inv = {v: k for k, v in _XSTOCK_BASE_TO_TICKER.items()}
    base = inv.get(ticker.strip().upper())
    return f"{base}/USDT" if base else None


@dataclass(frozen=True)
class InstrumentId:
    """Unique instrument identity: ``exchange:symbol:market_type``.

    ``symbol`` must be canonical (``BTC/USDT``); construct via ``from_parts`` to
    normalise arbitrary input. Frozen + hashable so it can key dicts/sets.
    """

    exchange: str
    symbol: str
    market_type: str = MARKET_SPOT

    def __post_init__(self) -> None:
        if not self.exchange:
            raise ValueError("InstrumentId.exchange must be non-empty")
        if "/" not in self.symbol:
            raise ValueError(
                f"InstrumentId.symbol must be canonical (BASE/QUOTE), got {self.symbol!r}"
            )
        if self.market_type not in _VALID_MARKET_TYPES:
            raise ValueError(
                f"InstrumentId.market_type must be one of {sorted(_VALID_MARKET_TYPES)}, "
                f"got {self.market_type!r}"
            )

    def __str__(self) -> str:
        return f"{self.exchange}:{self.symbol}:{self.market_type}"

    @classmethod
    def parse(cls, s: str) -> "InstrumentId":
        """Deserialise ``exchange:symbol:market_type``. Exchange is lower-cased.

        The symbol segment itself contains no ':' (perp settle suffix is not part
        of the stored canonical form), so a plain split on ':' is unambiguous.
        """
        if not s:
            raise ValueError("InstrumentId.parse: empty string")
        parts = s.split(":")
        if len(parts) != 3:
            raise ValueError(
                f"InstrumentId.parse: expected 'exchange:symbol:market_type', got {s!r}"
            )
        exchange, symbol, market_type = parts
        return cls(exchange=exchange.lower(), symbol=symbol, market_type=market_type)

    @classmethod
    def from_parts(
        cls, exchange: str, symbol: str, market_type: str = MARKET_SPOT
    ) -> "InstrumentId":
        """Normalising factory: lower-case exchange + canonicalise symbol."""
        return cls(
            exchange=(exchange or "").lower(),
            symbol=to_canonical(symbol),
            market_type=market_type,
        )

    def to_ccxt_symbol(self) -> str:
        """CCXT unified symbol: canonical for spot, settle-suffixed for perp/futures."""
        if self.market_type == MARKET_SPOT:
            return self.symbol
        return to_ccxt_perp(self.symbol)

    def to_bitget_symbol(self) -> str:
        """Bitget execution symbol (concatenated, upper-case)."""
        return to_bitget(self.symbol)


def canonical_of(symbol: object) -> str:
    """Canonical ``BASE/QUOTE`` for any symbol-like input — ``InstrumentId``-aware.

    ``InstrumentId.symbol`` is *already* canonical, so return it directly. Never
    feed ``str(instrument_id)`` to :func:`to_canonical`: the stringified form is
    ``exchange:symbol:market_type`` (e.g. ``okx:AAVE/USDT:spot``), and
    ``to_canonical`` reads a leading ``:`` segment as a CCXT perp *settle* suffix —
    it would split on the first ``:`` and collapse the whole thing to ``OKX``,
    which then 400s every downstream provider. Plain strings (already a venue or
    canonical form) go through :func:`to_canonical` unchanged.
    """
    if isinstance(symbol, InstrumentId):
        return symbol.symbol
    return to_canonical(str(symbol))
