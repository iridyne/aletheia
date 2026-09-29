"""Venue margin envelope — canonical per-symbol margin-tier facts (Issue #3729 §13.3).

The Deterministic Leverage Resolver (§7) needs real venue tier max-leverage,
maintenance-margin-rate, and notional ranges per instrument — never a static
per-symbol default. This module is the one place that owns those facts:

- ``load_venue_margin_envelope()``    synchronous cache read, no network I/O.
  Safe on the hot Compiler/Risk sizing path.
- ``refresh_venue_margin_envelope()`` async CCXT fetch + persist. Runs on its
  own schedule (or on demand); never called inline from sizing/leverage
  resolution.

Storage follows the ``exchange_sync.py`` cache pattern: one JSON blob per key
in the existing ``settings`` table, keyed by venue/symbol/market_type.

Forbidden: ``aletheia.trading.instruments.FUTURES_MMR`` (or any other static
per-symbol default) must never be read by this module. A tier fact that is
missing, stale, mode-mismatched, or malformed fails closed — it never falls
back to a guess.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from aletheia.data.symbols import MARKET_SPOT, to_ccxt_perp
from aletheia.execution.margin_mode_policy import MarginMode
from aletheia.storage.sqlite import execute_write, get_db

logger = logging.getLogger("aletheia.trading.venue_margin")

# Stable failure reasons (Issue #3729 §12). Never carry exchange exception text.
REASON_TIERS_UNAVAILABLE = "VENUE_MARGIN_TIERS_UNAVAILABLE"
REASON_TIERS_STALE = "VENUE_MARGIN_TIERS_STALE"
REASON_MARGIN_MODE_UNAVAILABLE = "MARGIN_MODE_UNAVAILABLE"

_KEY_PREFIX = "venue_margin_envelope"

# Tier schedules are exchange risk-engine facts that a venue changes on the
# order of months, via a deliberate and announced decision — not something
# that goes stale minute to minute. A short TTL would force a network round
# trip into the hot sizing/leverage path for data that is almost never
# actually wrong; this TTL instead accepts a bounded staleness window and
# leaves detecting a real tier change to the explicit
# ``refresh_venue_margin_envelope`` schedule, not to every cache read.
_TTL_MS = 24 * 60 * 60 * 1000  # 24h


class VenueMarginUnavailable(Exception):
    """A typed venue-margin failure. ``reason`` is one of the stable reason codes."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(reason)


class MarginTier(BaseModel):
    """One venue-reported leverage/MMR bracket for a range of position notional."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tier: int = Field(ge=1)
    min_notional: float | None = Field(default=None, ge=0)
    max_notional: float | None = Field(default=None, gt=0)
    max_leverage: float = Field(gt=0)
    maintenance_margin_rate: float = Field(ge=0)


class VenueMarginEnvelope(BaseModel):
    """The frozen, persisted margin-tier facts for one venue/symbol/market_type.

    ``age_ms`` reflects one load's view of freshness relative to
    ``fetched_at_ms``; it is not part of the persisted identity of the facts.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    venue: str
    symbol: str
    market_type: str
    margin_mode: MarginMode
    leverage_step: float | None = Field(default=None, gt=0)
    tiers: tuple[MarginTier, ...] = Field(min_length=1)
    source: str
    fetched_at_ms: int
    age_ms: int = 0
    facts_fingerprint: str

    @model_validator(mode="after")
    def _tiers_ascending_by_notional(self) -> "VenueMarginEnvelope":
        floor = float("-inf")
        for t in self.tiers:
            lower = t.min_notional if t.min_notional is not None else float("-inf")
            if lower < floor:
                raise ValueError("tiers must be ascending by min_notional")
            floor = lower
        return self

    def tier_for_notional(self, notional: float) -> MarginTier | None:
        """The tier whose ``[min_notional, max_notional)`` covers ``notional``.

        The last tier's upper bound is open: ``max_notional=None`` means no cap.
        """
        for t in self.tiers:
            lower = t.min_notional if t.min_notional is not None else float("-inf")
            if notional < lower:
                continue
            if t.max_notional is not None and notional >= t.max_notional:
                continue
            return t
        return None


def _settings_key(venue: str, symbol: str, market_type: str) -> str:
    return f"{_KEY_PREFIX}.{venue}.{symbol}.{market_type}"


def _read_raw(venue: str, symbol: str, market_type: str) -> dict | None:
    db = get_db()
    row = db.execute(
        "SELECT value FROM settings WHERE key = ?",
        (_settings_key(venue, symbol, market_type),),
    ).fetchone()
    if row is None:
        return None
    try:
        parsed = json.loads(row["value"])
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def store_venue_margin_envelope(envelope: VenueMarginEnvelope) -> None:
    """Persist ``envelope`` as the last-qualified fact row for its key.

    Used by ``refresh_venue_margin_envelope`` and directly by tests seeding a
    fact row without a network call.
    """
    key = _settings_key(envelope.venue, envelope.symbol, envelope.market_type)
    data = json.dumps(envelope.model_dump(mode="json"), allow_nan=False)
    execute_write(
        "INSERT INTO settings(key, value, updated_at) VALUES(?, ?, datetime('now')) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, data),
    )


def clear_venue_margin_envelope(*, venue: str, symbol: str, market_type: str) -> None:
    """Test/ops helper: delete the cached fact row for one key."""
    execute_write(
        "DELETE FROM settings WHERE key = ?",
        (_settings_key(venue, symbol, market_type),),
    )


def inspect_venue_margin_envelope(*, venue: str, symbol: str, market_type: str) -> dict | None:
    """Test/ops helper: the raw persisted JSON blob for one key, unvalidated."""
    return _read_raw(venue, symbol, market_type)


def load_venue_margin_envelope(
    *,
    venue: str,
    symbol: str,
    market_type: str,
    margin_mode: MarginMode,
    cfg=None,
    now: int | None = None,
) -> VenueMarginEnvelope:
    """Cache-read-only load of the last-qualified margin envelope.

    Synchronous and network-free by design: the hot Compiler/Risk path must
    never block on an exchange call to size or resolve leverage for a trade
    already in flight. Facts are refreshed out-of-band by
    ``refresh_venue_margin_envelope``.
    """
    del cfg  # reserved for the Owner-policy call signature; not consulted here.
    raw = _read_raw(venue, symbol, market_type)
    if raw is None:
        raise VenueMarginUnavailable(
            REASON_TIERS_UNAVAILABLE,
            f"no cached margin tiers for {venue}:{symbol}:{market_type}",
        )
    try:
        envelope = VenueMarginEnvelope.model_validate(raw)
    except ValidationError as exc:
        raise VenueMarginUnavailable(
            REASON_TIERS_UNAVAILABLE, f"malformed cached margin tiers: {exc}"
        ) from exc
    now_ms = now if now is not None else int(time.time() * 1000)
    age_ms = now_ms - envelope.fetched_at_ms
    if age_ms > _TTL_MS:
        raise VenueMarginUnavailable(
            REASON_TIERS_STALE,
            f"cached margin tiers are {age_ms}ms old (ttl={_TTL_MS}ms)",
        )
    if envelope.margin_mode != margin_mode:
        raise VenueMarginUnavailable(
            REASON_MARGIN_MODE_UNAVAILABLE,
            f"cached margin_mode={envelope.margin_mode!r} != requested {margin_mode!r}",
        )
    return envelope.model_copy(update={"age_ms": age_ms})


def _canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(
    venue: str, symbol: str, market_type: str, margin_mode: str, tiers: tuple[MarginTier, ...]
) -> str:
    payload = {
        "venue": venue,
        "symbol": symbol,
        "market_type": market_type,
        "margin_mode": margin_mode,
        "tiers": [t.model_dump(mode="json") for t in tiers],
    }
    return hashlib.sha256(_canonical(payload).encode()).hexdigest()


def build_venue_margin_envelope(
    *,
    venue: str,
    symbol: str,
    market_type: str,
    margin_mode: MarginMode,
    tiers: tuple[MarginTier, ...],
    source: str,
    fetched_at_ms: int,
    leverage_step: float | None = None,
) -> VenueMarginEnvelope:
    """Construct one envelope for a caller that already holds the tier facts.

    Computes ``facts_fingerprint`` with the same ``_fingerprint()`` used by
    ``refresh_venue_margin_envelope``, so a hand-built and a CCXT-fetched
    envelope over identical facts fingerprint identically. Pure construction
    only -- callers still persist through ``store_venue_margin_envelope``.
    """
    return VenueMarginEnvelope(
        venue=venue,
        symbol=symbol,
        market_type=market_type,
        margin_mode=margin_mode,
        leverage_step=leverage_step,
        tiers=tiers,
        source=source,
        fetched_at_ms=fetched_at_ms,
        age_ms=0,
        facts_fingerprint=_fingerprint(venue, symbol, market_type, margin_mode, tiers),
    )


def _tier_from_ccxt(raw: dict) -> MarginTier:
    """Map one CCXT leverage-tier dict into a ``MarginTier``.

    This is the one seam that replaces the forbidden
    ``FUTURES_MMR.get(symbol, 0.005)`` default: a field CCXT does not report
    must produce a typed failure, never a guess.
    """
    try:
        tier = int(raw["tier"])
        min_notional = raw.get("minNotional")
        max_notional = raw.get("maxNotional")
        max_leverage = float(raw["maxLeverage"])
        maintenance_margin_rate = float(raw["maintenanceMarginRate"])
    except (KeyError, TypeError, ValueError) as exc:
        raise VenueMarginUnavailable(
            REASON_TIERS_UNAVAILABLE, f"malformed CCXT leverage tier: {exc}"
        ) from exc
    return MarginTier(
        tier=tier,
        min_notional=float(min_notional) if min_notional is not None else None,
        max_notional=float(max_notional) if max_notional is not None else None,
        max_leverage=max_leverage,
        maintenance_margin_rate=maintenance_margin_rate,
    )


async def refresh_venue_margin_envelope(
    exchange,
    *,
    venue: str,
    symbol: str,
    market_type: str,
    margin_mode: MarginMode,
    now: int | None = None,
) -> VenueMarginEnvelope:
    """Fetch real tier facts from CCXT and persist them as the new fact row.

    ``exchange`` is a connected CCXT async exchange instance (e.g.
    ``ccxt.async_support.bitget()``); this function does not construct,
    authenticate, or close it. Explicit and out-of-band only — never called
    from the synchronous Compiler/Risk sizing path.
    """
    ccxt_symbol = symbol if market_type == MARKET_SPOT else to_ccxt_perp(symbol)
    venue_key = str(venue or "").strip().lower()
    try:
        if venue_key == "bitget":
            raw_tiers = await exchange.fetch_market_leverage_tiers(ccxt_symbol)
        elif venue_key == "binanceusdm":
            tiers_by_symbol = await exchange.fetch_leverage_tiers([ccxt_symbol])
            raw_tiers = tiers_by_symbol.get(ccxt_symbol)
        else:
            raise VenueMarginUnavailable(
                REASON_TIERS_UNAVAILABLE,
                f"no leverage-tier API mapping registered for venue {venue!r}",
            )
    except VenueMarginUnavailable:
        raise
    except Exception as exc:  # ccxt.BaseError and friends — never persist exception text
        raise VenueMarginUnavailable(
            REASON_TIERS_UNAVAILABLE,
            f"{venue} leverage-tier fetch failed: {type(exc).__name__}",
        ) from exc

    if not raw_tiers:
        raise VenueMarginUnavailable(
            REASON_TIERS_UNAVAILABLE, f"{venue} returned no leverage tiers for {symbol}"
        )

    tiers = tuple(
        sorted(
            (_tier_from_ccxt(t) for t in raw_tiers),
            key=lambda t: t.min_notional if t.min_notional is not None else float("-inf"),
        )
    )

    now_ms = now if now is not None else int(time.time() * 1000)
    envelope = build_venue_margin_envelope(
        venue=venue,
        symbol=symbol,
        market_type=market_type,
        margin_mode=margin_mode,
        tiers=tiers,
        source=f"ccxt:{venue}",
        fetched_at_ms=now_ms,
    )
    store_venue_margin_envelope(envelope)
    return envelope
