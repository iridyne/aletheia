"""Venue margin envelope — canonical per-symbol margin-tier facts (Issue #3729 §13.3).

The Deterministic Leverage Resolver (§7) needs real venue tier max-leverage,
maintenance-margin-rate, and notional ranges per instrument — never a static
per-symbol default. This module is the one place that owns those facts:

- ``load_venue_margin_envelope()``    synchronous cache read, no network I/O.
  Safe on the hot Compiler/Risk sizing path.
- ``refresh_venue_margin_envelope()`` async CCXT fetch + persist for one key.
- ``refresh_venue_margin_demand()``   async batch over the Planner's current
  demand set (Issue #3741): fetches only what is missing or stale, on the venue
  that will hold the position, so an operator never has to seed facts by hand
  before a Candidate can be planned.

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
from typing import Sequence

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
    # CCXT advertises each venue's tier API in `exchange.has`: a native
    # per-symbol read is preferred; an emulated one wraps the batch read anyway.
    has = getattr(exchange, "has", None) or {}
    try:
        if has.get("fetchMarketLeverageTiers") is True:
            raw_tiers = await exchange.fetch_market_leverage_tiers(ccxt_symbol)
        elif has.get("fetchLeverageTiers") is True:
            tiers_by_symbol = await exchange.fetch_leverage_tiers([ccxt_symbol])
            raw_tiers = tiers_by_symbol.get(ccxt_symbol)
        else:
            raise VenueMarginUnavailable(
                REASON_TIERS_UNAVAILABLE,
                f"venue {venue!r} exposes no CCXT leverage-tier API",
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


# One tick refreshes at most this many missing instruments. The demand set is
# the Planner's current queue (tens of symbols at most), a fresh row is skipped,
# so this only bounds the first pass after a universe roll or a deploy.
VENUE_MARGIN_DEMAND_MAX_SYMBOLS = 8


async def refresh_venue_margin_demand(
    *,
    execution_mode: str,
    pairs: Sequence[tuple[str, str]],
    margin_mode: MarginMode | None = None,
    max_symbols: int = VENUE_MARGIN_DEMAND_MAX_SYMBOLS,
    exchange=None,
    now: int | None = None,
) -> dict:
    """Maintain margin facts for the instruments the Entry path is about to read.

    ``pairs`` is the Planner's live demand as ``(symbol, market_type)`` and the
    venue is the one that will hold the position (``execution_mode_venue``), not
    the venue that produced the market data (#3740). ``margin_mode`` defaults to
    the route's qualified *submission* mode (``submission_margin_mode``) — the
    same value the Compiler asks for. The mode is not part of the cache key, so
    writing any other mode would leave a row that still fails closed with
    ``REASON_MARGIN_MODE_UNAVAILABLE``.

    Existing non-stale rows in the requested mode are skipped, so a steady state
    costs no fetch at all and a rolling universe costs one fetch per newly
    demanded instrument; a single instrument's failure never blocks another, and
    nothing is ever defaulted or guessed.

    ``exchange`` is injectable for tests. When omitted, a CCXT Pro session is
    built here -- inside the running loop, because the session binds to it -- and
    closed in a ``finally``.
    """

    from aletheia.config.exchanges import execution_mode_venue
    from aletheia.execution.margin_mode_policy import submission_margin_mode

    venue = execution_mode_venue(execution_mode)
    mode: MarginMode | None = margin_mode or submission_margin_mode(execution_mode)
    summary: dict = {
        "status": "not_applicable",
        "venue": venue,
        "margin_mode": mode,
        "reason": None,
        "considered": 0,
        "refreshed": 0,
        "fresh": 0,
        "unavailable": [],
        "skipped_market_type": 0,
        "skipped_over_cap": 0,
    }
    if not venue or mode is None:
        # No position to hold (internal/paper route) or no qualified submission
        # mode for this route: the Compiler would refuse the instrument anyway.
        summary["reason"] = (
            "execution_venue_unavailable" if not venue else "margin_mode_unavailable"
        )
        logger.info(
            "venue_margin demand refresh skipped: execution_mode=%s reason=%s",
            execution_mode,
            summary["reason"],
        )
        return summary

    demand = list(dict.fromkeys(pairs))
    perps = [(symbol, market_type) for symbol, market_type in demand if market_type != MARKET_SPOT]
    summary["skipped_market_type"] = len(demand) - len(perps)
    considered = perps[: max(0, int(max_symbols))]
    summary["skipped_over_cap"] = len(perps) - len(considered)
    summary["considered"] = len(considered)
    summary["status"] = "ok"
    if not considered:
        logger.info(
            "venue_margin demand refresh: venue=%s mode=%s considered=0 (nothing demanded)",
            venue,
            mode,
        )
        return summary

    owns_exchange = exchange is None
    session = None
    close_ccxt = None
    if owns_exchange:
        from aletheia.net import close_ccxt as _close_ccxt
        from aletheia.net import make_ccxt_pro

        close_ccxt = _close_ccxt
        exchange, session = make_ccxt_pro(venue)
    try:
        for symbol, market_type in considered:
            try:
                load_venue_margin_envelope(
                    venue=venue,
                    symbol=symbol,
                    market_type=market_type,
                    margin_mode=mode,
                    now=now,
                )
            except VenueMarginUnavailable:
                # missing, stale, malformed, or mode-mismatched: refetch it below
                logger.debug("venue_margin demand: refetching %s:%s", venue, symbol)
            else:
                summary["fresh"] += 1
                continue
            try:
                await refresh_venue_margin_envelope(
                    exchange,
                    venue=venue,
                    symbol=symbol,
                    market_type=market_type,
                    margin_mode=mode,
                    now=now,
                )
                summary["refreshed"] += 1
            except VenueMarginUnavailable as exc:
                summary["unavailable"].append(
                    {"symbol": symbol, "market_type": market_type, "reason": exc.reason}
                )
            except Exception as exc:  # bound the reason; never fabricate facts
                summary["unavailable"].append(
                    {"symbol": symbol, "market_type": market_type, "reason": type(exc).__name__}
                )
    finally:
        if owns_exchange and session is not None and close_ccxt is not None:
            await close_ccxt(exchange, session)

    logger.info(
        "venue_margin demand refresh: venue=%s considered=%d refreshed=%d fresh=%d unavailable=%d",
        venue,
        summary["considered"],
        summary["refreshed"],
        summary["fresh"],
        len(summary["unavailable"]),
    )
    return summary
