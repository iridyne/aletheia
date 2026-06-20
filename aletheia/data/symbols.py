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


# Bitget ON-series xStocks: base token (without /USDT) → canonical equity ticker.
# xStocks are tokenized US equities on Bitget — they track NYSE/NASDAQ prices
# but are a separate on-chain instrument (may carry small premium/discount).
# Extend this dict as new xStock pairs are listed; ticker_to_xstock() derives
# the reverse mapping automatically.
_XSTOCK_BASE_TO_TICKER: dict[str, str] = {
    "AAPLON": "AAPL",
    "TSLAON": "TSLA",
    "NVDAON": "NVDA",
    "SPYON": "SPY",
    "MSFTON": "MSFT",
    "AMZNON": "AMZN",
    "GOOGLON": "GOOGL",
    "METAON": "META",
    "NFLXON": "NFLX",
    "GOOGLBON": "GOOG",
}


def xstock_to_ticker(symbol: str) -> str | None:
    """Map a Bitget xStock symbol to its canonical equity ticker.

    'AAPLON/USDT' → 'AAPL'. Returns None if not in the mapping table.
    Input may be in any format (canonical 'AAPLON/USDT' or bare 'AAPLON').
    """
    base = symbol.split("/")[0].strip().upper()
    return _XSTOCK_BASE_TO_TICKER.get(base)


def ticker_to_xstock(ticker: str) -> str | None:
    """Map a canonical equity ticker to its Bitget xStock symbol.

    'AAPL' → 'AAPLON/USDT'. Returns None if no xStock exists for the ticker.
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
