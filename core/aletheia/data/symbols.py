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

import json
from dataclasses import dataclass

# Market type constants.
MARKET_SPOT = "spot"
MARKET_PERP = "perp"
MARKET_FUTURES = "futures"

_VALID_MARKET_TYPES = {MARKET_SPOT, MARKET_PERP, MARKET_FUTURES}

# Every stored/configured market_type alias that names a physical instrument.
# Execution rows use ``usdt_futures``; instrument-identity rows use ``perp``.
# ``futures`` has no producer (Fedora census on #3811) and is deliberately absent.
_PHYSICAL_MARKET_ALIASES = {
    "spot": MARKET_SPOT,
    "perp": MARKET_PERP,
    "swap": MARKET_PERP,
    "usdt_futures": MARKET_PERP,
}


def normalize_market_type(value: object) -> str | None:
    """Resolve a market_type alias to ``spot``/``perp``; anything else is None.

    The one canonical resolver (#3811). None is fail-closed: the caller decides
    at its own trust boundary what an unresolvable market means.
    """
    if value is None:
        return None
    return _PHYSICAL_MARKET_ALIASES.get(str(value).strip().lower())

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


# Durable product facts only. Exchange metadata remains the authority for asset
# classification; this catalog explains the relationship that metadata cannot.
_SEMICONDUCTOR_3X_ETFS = {"SOXL": 3.0, "SOXS": -3.0}
_SEMICONDUCTOR_3X_SOURCE = (
    "https://www.direxion.com/product/daily-semiconductor-bull-bear-3x-etfs"
)
INSTRUMENT_BACKGROUND_VERSION = "instrument-background/v1"
INSTRUMENT_ANALYSIS_CONTEXT_VERSION = "instrument-analysis-context/v1"
# These exchange/product pairs expose crypto instruments without a separate
# asset-class field. Every other missing classification stays unresolved.
# Bitget perpetuals are not here: they carry the first-party ``isRwa`` flag, and
# defaulting them to crypto classified stock/metal/oil/index perps as crypto (#3440).
_DEFAULT_CRYPTO_MARKETS = {
    ("binance", MARKET_SPOT),
    ("bitget", MARKET_SPOT),
}


def _metadata_dict(value: object) -> dict | None:
    try:
        metadata = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return metadata if isinstance(metadata, dict) else None


def instrument_background_from_metadata(value: object) -> dict | None:
    """Read the versioned background from a registry or Opportunity JSON value."""
    metadata = _metadata_dict(value)
    if metadata is None:
        return None
    background = metadata.get("instrument_background")
    return background if isinstance(background, dict) else None


def instrument_analysis_context_from_metadata(value: object) -> dict | None:
    """Read the frozen analysis applicability from an Opportunity JSON value."""
    metadata = _metadata_dict(value)
    if metadata is None:
        return None
    context = metadata.get("analysis_context")
    return context if isinstance(context, dict) else None


def instrument_analysis_context(background: object) -> dict | None:
    """Resolve the one supported decision context for a complete background."""
    if not isinstance(background, dict):
        return None
    asset_class = str(background.get("asset_class") or "").lower()
    product_kind = str(background.get("product_kind") or "").lower()
    if asset_class == "crypto" and product_kind in {
        "crypto_spot", "crypto_perpetual",
    }:
        return {
            "version": INSTRUMENT_ANALYSIS_CONTEXT_VERSION,
            "status": "supported",
            "kind": "crypto",
            "applicable_evidence": [
                "instrument_market_data", "btc_relative_strength",
                "global_crypto_regime", "crypto_family_prior",
            ],
            "inapplicable_evidence": [],
            "unavailable_evidence": [],
        }

    reference_asset = str(background.get("reference_asset") or "").upper()
    embedded = background.get("embedded_product")
    if (
        asset_class == "equity"
        and product_kind == "stock_linked_perp"
        and background.get("venue") == "binance"
        and background.get("market_type") == MARKET_PERP
        and reference_asset in _SEMICONDUCTOR_3X_ETFS
        and isinstance(embedded, dict)
        and embedded.get("kind") == "leveraged_etf"
        and embedded.get("benchmark_id") == "ICESEMIT"
        and embedded.get("embedded_daily_multiplier")
        == _SEMICONDUCTOR_3X_ETFS[reference_asset]
        and embedded.get("reset_horizon") == "1d"
        and isinstance(embedded.get("source_refs"), list)
        and bool(embedded["source_refs"])
    ):
        return {
            "version": INSTRUMENT_ANALYSIS_CONTEXT_VERSION,
            "status": "supported",
            "kind": "stock_linked_perp",
            "applicable_evidence": [
                "instrument_ohlcv", "instrument_price_path", "spread",
                "liquidity", "order_book", "funding", "open_interest",
                "volatility", "instrument_momentum", "cost_tradability",
                "product_background",
            ],
            "inapplicable_evidence": [
                "btc_relative_strength", "btc_beta", "global_crypto_regime",
                "crypto_family_prior", "crypto_sector_relationships",
            ],
            "unavailable_evidence": ["live_benchmark_market_data"],
        }
    return None


def instrument_background_rejection_reason(
    background: object,
    *,
    symbol: str,
    market_type: str,
    venue: str | None = None,
) -> str | None:
    """Return why identity/context cannot enter the unified mainline."""
    if not isinstance(background, dict):
        return "instrument_background_missing"
    if background.get("version") != INSTRUMENT_BACKGROUND_VERSION:
        return "instrument_background_version_mismatch"
    if background.get("status") != "complete":
        return "instrument_background_unresolved"
    required = (
        "venue", "symbol", "market_type", "physical_market_type",
        "asset_class", "product_kind", "reference_asset", "relationship",
    )
    if any(not background.get(field) for field in required) or not isinstance(
        background.get("classification"), dict
    ) or not background["classification"].get("source"):
        return "instrument_background_identity_incomplete"
    if (
        to_canonical(str(background.get("symbol") or "")) != to_canonical(symbol)
        or background.get("market_type") != market_type
        or background.get("physical_market_type") != market_type
        or (
            venue is not None
            and str(background.get("venue") or "").lower()
            != str(venue).lower()
        )
    ):
        return "instrument_background_identity_conflict"
    relationship = background.get("relationship")
    if relationship not in {
        "direct_asset", "tracks_reference_price",
        "perpetual_tracks_etf_reference",
    }:
        return "instrument_background_relationship_malformed"
    if background.get("product_kind") == "stock_linked_perp":
        embedded = background.get("embedded_product")
        if not isinstance(embedded, dict) or not instrument_analysis_context(background):
            return "instrument_background_relationship_malformed"
    if instrument_analysis_context(background) is None:
        product_kind = str(background.get("product_kind") or "unknown").lower()
        return f"unsupported_analysis_context:{product_kind}"
    return None


def build_instrument_background(
    *,
    symbol: str,
    venue: str,
    market_type: str,
    market_info: dict,
) -> dict:
    """Describe one exchange instrument without granting it trading authority."""
    canonical = to_canonical(symbol)
    base = canonical.partition("/")[0]
    raw = market_info.get("info") or {}
    venue = venue.strip().lower()
    underlying_type = str(raw.get("underlyingType") or "").upper()
    contract_type = str(
        raw.get("contractType") or raw.get("symbolType") or raw.get("instType") or ""
    ).upper()
    underlying_subtypes = raw.get("underlyingSubType") or []
    if not isinstance(underlying_subtypes, list):
        underlying_subtypes = [underlying_subtypes]
    underlying_subtypes = [str(value) for value in underlying_subtypes]

    okx_category = str(raw.get("instCategory") or "")
    # Bitget perps: isRwa=YES is any real-world-asset underlying (equity, metal,
    # oil, index) without a finer category; NO is crypto; absent stays unresolved.
    bitget_rwa = (
        str(raw.get("isRwa") or "").upper()
        if venue == "bitget" and market_type == MARKET_PERP else ""
    )
    is_exchange_equity = underlying_type.endswith("EQUITY") or okx_category == "3"
    if is_exchange_equity:
        asset_class = "equity"
    elif bitget_rwa in {"YES", "NO"}:
        asset_class = "real_world_asset" if bitget_rwa == "YES" else "crypto"
    elif underlying_type in {"COMMODITY", "INDEX"}:
        asset_class = underlying_type.lower()
    elif underlying_type:
        asset_class = "crypto" if underlying_type == "COIN" else "unknown"
    elif venue == "okx":
        asset_class = "crypto" if okx_category == "1" else "unknown"
    elif (venue, market_type) in _DEFAULT_CRYPTO_MARKETS:
        asset_class = "crypto"
    else:
        asset_class = "unknown"

    linked_multiple = (
        _SEMICONDUCTOR_3X_ETFS.get(base) if asset_class == "equity" else None
    )
    if linked_multiple is not None:
        product_kind = (
            "stock_linked_perp" if market_type == MARKET_PERP
            else "tokenized_equity"
        )
        reference_asset = base
        relationship = (
            "perpetual_tracks_etf_reference"
            if market_type == MARKET_PERP else "tracks_reference_price"
        )
        background_status = "complete"
    elif asset_class == "crypto":
        product_kind = "crypto_perpetual" if market_type == MARKET_PERP else "crypto_spot"
        reference_asset = base
        relationship = (
            "tracks_reference_price" if market_type == MARKET_PERP else "direct_asset"
        )
        background_status = "complete"
    elif asset_class in {"equity", "commodity", "index", "real_world_asset"}:
        product_kind = (
            "tradifi_perpetual" if market_type == MARKET_PERP
            else f"tokenized_{asset_class}"
        )
        reference_asset = base if underlying_type or bitget_rwa else None
        relationship = "tracks_reference_price" if reference_asset else "unknown"
        background_status = "complete" if reference_asset else "partial"
    else:
        product_kind = "unknown_exchange_product"
        reference_asset = None
        relationship = "unknown"
        background_status = "partial"

    background = {
        "version": INSTRUMENT_BACKGROUND_VERSION,
        "status": background_status,
        "venue": venue,
        "symbol": canonical,
        "market_type": market_type,
        "physical_market_type": market_type,
        "asset_class": asset_class,
        "product_kind": product_kind,
        "reference_asset": reference_asset,
        "relationship": relationship,
        "classification": {
            "source": (
                "exchange_metadata"
                if (
                    underlying_type
                    or bitget_rwa
                    or raw.get("instCategory") is not None
                )
                else (
                    "supported_market_default"
                    if (venue, market_type) in _DEFAULT_CRYPTO_MARKETS
                    else "unresolved"
                )
            ),
            "underlying_type": underlying_type or None,
            "contract_type": contract_type or None,
            "underlying_subtypes": underlying_subtypes,
        },
    }
    if linked_multiple is not None:
        background["embedded_product"] = {
            "kind": "leveraged_etf",
            "benchmark_name": "NYSE Semiconductor Index",
            "benchmark_id": "ICESEMIT",
            "embedded_daily_multiplier": linked_multiple,
            "reset_horizon": "1d",
            "source_refs": [_SEMICONDUCTOR_3X_SOURCE],
        }
    return background


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
