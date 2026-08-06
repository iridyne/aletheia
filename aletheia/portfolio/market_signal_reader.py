"""Read exact-instrument market signals for position-aware exit evaluation.

Pure DB read — no LLM, no network.  A signal is usable only when its
``universe_member_id`` resolves to the same venue, canonical symbol, and
physical market type as the position request.  Legacy symbol-only evidence is
intentionally ignored so spot/perpetual or cross-venue rows cannot shadow one
another.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from aletheia.data.symbols import (
    MARKET_FUTURES,
    MARKET_PERP,
    MARKET_SPOT,
    InstrumentId,
)
from aletheia.trading.instruments import instrument_market_to_db_market

logger = logging.getLogger("aletheia.portfolio.market_signal_reader")


def _physical_market_type(raw: str) -> str:
    """Normalize stored/configured market names to InstrumentId vocabulary."""
    normalized = (raw or "").strip().lower()
    if normalized in {"perp", "swap", "usdt_futures"}:
        return MARKET_PERP
    if normalized == MARKET_SPOT:
        return MARKET_SPOT
    if normalized == MARKET_FUTURES:
        return MARKET_FUTURES
    raise ValueError(f"unsupported market type for market signal identity: {raw!r}")


@dataclass(frozen=True)
class MarketSignalRequest:
    """Exact position identity whose advisory market evidence is requested."""

    instrument: InstrumentId
    position_opportunity_id: str | None = None

    @classmethod
    def from_parts(
        cls,
        venue: str,
        symbol: str,
        market_type: str,
        *,
        position_opportunity_id: str | None = None,
    ) -> "MarketSignalRequest":
        return cls(
            instrument=InstrumentId.from_parts(
                venue,
                symbol,
                _physical_market_type(market_type),
            ),
            position_opportunity_id=position_opportunity_id,
        )


@dataclass
class MarketSignalContext:
    symbol: str
    market_type: str | None = None
    venue: str | None = None
    direction_bias: str | None = None
    confidence_score: int | None = None
    signal_age_min: float | None = None
    source_opportunity_id: str | None = None
    source_agent: str | None = None
    source_created_at: str | None = None
    source_valid_until: str | None = None
    universe_member_id: str | None = None
    asset_id: str | None = None
    evidence_status: str | None = None
    feature_snapshot_id: str | None = None
    feature_asset_id: str | None = None
    feature_timestamp: str | None = None
    rsi_14: float | None = None
    return_1h: float | None = None
    return_4h: float | None = None
    atr_pct: float | None = None
    funding_rate: float | None = None

    def matches(self, request: MarketSignalRequest) -> bool:
        """Return true only for the request's exact venue/symbol/market tuple."""
        instrument = request.instrument
        return (
            self.venue == instrument.exchange
            and self.symbol == instrument.symbol
            and self.market_type == instrument.market_type
        )


def _normalize_direction(raw: str | None) -> str | None:
    """Normalize direction_bias to 'long'/'short'/'neutral' vocabulary."""
    if not raw:
        return None
    low = raw.lower().strip()
    if low in ("long", "bullish", "buy"):
        return "long"
    if low in ("short", "bearish", "sell"):
        return "short"
    if low in ("neutral", "flat", "none"):
        return "neutral"
    return raw


def _parse_utc(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _row_instrument(row) -> InstrumentId | None:
    """Resolve a joined opportunity row only when all stored identities agree."""
    try:
        instrument = InstrumentId.from_parts(
            row["venue"],
            row["registry_symbol"],
            _physical_market_type(row["registry_market_type"]),
        )
    except (TypeError, ValueError):
        return None
    if row["opportunity_symbol"] != instrument.symbol:
        return None
    if row["opportunity_market"] != instrument_market_to_db_market(
        instrument.market_type
    ):
        return None
    return instrument


def _validated_requests(db, requests: list[MarketSignalRequest]) -> list[MarketSignalRequest]:
    """Fail closed when a position's entry opportunity contradicts its identity."""
    ids = sorted(
        {
            request.position_opportunity_id
            for request in requests
            if request.position_opportunity_id
        }
    )
    if not ids:
        return requests

    placeholders = ", ".join("?" for _ in ids)
    rows = db.execute(
        f"""
        SELECT o.id AS opportunity_id,
               o.symbol AS opportunity_symbol,
               o.market AS opportunity_market,
               ur.venue,
               ur.symbol AS registry_symbol,
               ur.market_type AS registry_market_type
        FROM opportunities o
        JOIN universe_members um ON um.id = o.universe_member_id
        JOIN universe_registry ur ON ur.id = um.asset_id
        WHERE o.id IN ({placeholders})
        """,
        ids,
    ).fetchall()
    source_instruments = {
        row["opportunity_id"]: instrument
        for row in rows
        if (instrument := _row_instrument(row)) is not None
    }
    return [
        request
        for request in requests
        if not request.position_opportunity_id
        or source_instruments.get(request.position_opportunity_id) == request.instrument
    ]


def get_market_signals_for_positions(
    requests: list[MarketSignalRequest],
    max_signal_age_min: float = 35.0,
) -> dict[MarketSignalRequest, MarketSignalContext]:
    """Return latest fresh signal and features for each exact position identity.

    Rows without a resolvable universe identity, an explicit unexpired validity
    window, or an exact venue/symbol/market match are omitted.  The caller must
    treat omission as unavailable evidence, never as a neutral market signal.
    """
    unique_requests = list(dict.fromkeys(requests))
    if not unique_requests:
        return {}

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=max_signal_age_min)

    try:
        from aletheia.storage.sqlite import get_db

        db = get_db()
        valid_requests = _validated_requests(db, unique_requests)
        if not valid_requests:
            return {}

        symbols = sorted({request.instrument.symbol for request in valid_requests})
        placeholders = ", ".join("?" for _ in symbols)
        rows = db.execute(
            f"""
            SELECT o.id AS opportunity_id,
                   o.symbol AS opportunity_symbol,
                   o.market AS opportunity_market,
                   o.direction_bias,
                   o.confidence_score,
                   o.source_agent,
                   o.created_at,
                   o.valid_until,
                   o.universe_member_id,
                   um.asset_id,
                   ur.venue,
                   ur.symbol AS registry_symbol,
                   ur.market_type AS registry_market_type
            FROM opportunities o
            JOIN universe_members um ON um.id = o.universe_member_id
            JOIN universe_registry ur ON ur.id = um.asset_id
            WHERE o.symbol IN ({placeholders})
              AND o.status = 'open'
              AND o.asset_class = 'crypto'
              AND datetime(o.created_at) >= datetime(?)
              AND o.valid_until IS NOT NULL
              AND datetime(o.valid_until) >= datetime(?)
            ORDER BY datetime(o.created_at) DESC, o.id DESC
            """,
            (*symbols, cutoff.isoformat(), now.isoformat()),
        ).fetchall()
    except Exception as exc:
        logger.debug("exact opportunity query failed: %s", exc)
        return {}

    requests_by_instrument: dict[InstrumentId, list[MarketSignalRequest]] = {}
    for request in valid_requests:
        requests_by_instrument.setdefault(request.instrument, []).append(request)

    result: dict[MarketSignalRequest, MarketSignalContext] = {}
    chosen_assets: dict[MarketSignalRequest, str] = {}
    for row in rows:
        instrument = _row_instrument(row)
        if instrument is None:
            continue
        matching_requests = requests_by_instrument.get(instrument, ())
        if not matching_requests:
            continue

        created_at = _parse_utc(row["created_at"])
        valid_until = _parse_utc(row["valid_until"])
        if created_at is None or valid_until is None:
            continue
        if created_at < cutoff or valid_until < now:
            continue
        age = (now - created_at).total_seconds() / 60.0

        for request in matching_requests:
            if request in result:
                continue
            result[request] = MarketSignalContext(
                symbol=instrument.symbol,
                market_type=instrument.market_type,
                venue=instrument.exchange,
                direction_bias=_normalize_direction(row["direction_bias"]),
                confidence_score=row["confidence_score"],
                signal_age_min=round(max(0.0, age), 1),
                source_opportunity_id=row["opportunity_id"],
                source_agent=row["source_agent"],
                source_created_at=row["created_at"],
                source_valid_until=row["valid_until"],
                universe_member_id=row["universe_member_id"],
                asset_id=row["asset_id"],
                evidence_status="exact_signal_features_unavailable",
            )
            chosen_assets[request] = row["asset_id"]

    if not chosen_assets:
        return result

    asset_ids = sorted(set(chosen_assets.values()))
    placeholders = ", ".join("?" for _ in asset_ids)
    try:
        snapshots = db.execute(
            f"""
            SELECT id, asset_id, ts, rsi_14, return_1h, return_4h,
                   atr_pct, funding_rate
            FROM universe_feature_snapshots
            WHERE asset_id IN ({placeholders})
              AND datetime(ts) >= datetime(?)
            ORDER BY asset_id, datetime(ts) DESC, id DESC
            """,
            (*asset_ids, cutoff.isoformat()),
        ).fetchall()
    except Exception as exc:
        logger.debug("exact feature snapshot query failed: %s", exc)
        return result

    latest_by_asset = {}
    for snapshot in snapshots:
        latest_by_asset.setdefault(snapshot["asset_id"], snapshot)
    for request, asset_id in chosen_assets.items():
        snapshot = latest_by_asset.get(asset_id)
        if snapshot is None:
            continue
        context = result[request]
        context.feature_snapshot_id = snapshot["id"]
        context.feature_asset_id = snapshot["asset_id"]
        context.feature_timestamp = snapshot["ts"]
        context.rsi_14 = snapshot["rsi_14"]
        context.return_1h = snapshot["return_1h"]
        context.return_4h = snapshot["return_4h"]
        context.atr_pct = snapshot["atr_pct"]
        context.funding_rate = snapshot["funding_rate"]
        context.evidence_status = "exact_signal_and_features"

    return result
