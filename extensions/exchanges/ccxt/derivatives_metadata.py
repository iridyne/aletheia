"""CCXT response metadata; concrete field proof is supplied by composition."""

from datetime import datetime, timezone
import json
import math
from aletheia.data.freshness import MAX_FUTURE_SKEW

from aletheia.data.derivatives_facts import (
    finite, snapshot_metadata,
)


def provider_source_time(
    raw: dict,
    acquired_at: datetime,
) -> tuple[str | None, str]:
    """Normalize one CCXT source timestamp without inventing source-now."""
    raw_timestamp = raw.get("timestamp")
    raw_datetime = raw.get("datetime")
    try:
        if raw_timestamp is not None:
            if isinstance(raw_timestamp, bool):
                return None, "invalid"
            timestamp = float(raw_timestamp)
            if not math.isfinite(timestamp):
                return None, "invalid"
            parsed = datetime.fromtimestamp(timestamp / 1000, timezone.utc)
        elif raw_datetime:
            parsed = datetime.fromisoformat(str(raw_datetime).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            parsed = parsed.astimezone(timezone.utc)
        else:
            return None, "missing"
    except (OSError, OverflowError, TypeError, ValueError):
        return None, "invalid"

    acquired_at = acquired_at.astimezone(timezone.utc)
    if parsed > acquired_at + MAX_FUTURE_SKEW:
        return parsed.isoformat(), "future"
    return parsed.isoformat(), "provided"


def funding_interval_seconds(raw):
    interval = raw.get('interval')
    hours = finite(interval[:-1]) if isinstance(interval, str) and interval.endswith('h') else None
    return finite(hours * 3600) if hours is not None and hours > 0 else None


def instrument_from_market(symbol: str, market: dict) -> dict:
    return dict(
        canonical_symbol=symbol,
        source_instrument_id=market.get("id"),
        market_type="perp" if market.get("swap") else market.get("type"),
        base_asset=market.get("base"),
        quote_asset=market.get("quote"),
        settle_asset=market.get("settle"),
        linear=market.get("linear"),
        contract_size=finite(market.get("contractSize")),
    )


def ccxt_metadata(symbol, venue, market, raw, *, family, ratios=None, acquired_at=None,
                  oi_value_fields=(), funding_timestamp_field=None):
    """Persist only semantics proved by the current CCXT response, never its fallback arithmetic."""
    info = raw.get("info") or {}
    unit = None
    # CCXT 4.5.56 maps these explicitly USD-native fields to openInterestValue.
    # Amount-only responses stay unknown here until a compatible mark/unit is proved.
    if isinstance(info, dict):
        value = finite(raw.get("openInterestValue"))
        if value is not None and any(finite(info.get(key)) == value for key in oi_value_fields):
            unit = "USD"
    instrument = instrument_from_market(symbol, market)
    if raw.get('symbol') is not None and raw['symbol'] != market.get('symbol'):
        instrument['source_instrument_id'] = None
    metadata = snapshot_metadata(
        provider="ccxt",
        venue=venue,
        instrument=instrument,
        interval_seconds=funding_interval_seconds(raw) if family == "funding" else None,
        oi_unit=unit if family == "open_interest" else None,
        ratios=ratios,
    )
    if funding_timestamp_field and family == 'funding' and isinstance(info, dict) and info.get(funding_timestamp_field) is not None:
        acquired = datetime.fromisoformat(acquired_at) if acquired_at else datetime.now(timezone.utc)
        source, status = provider_source_time({'timestamp': info[funding_timestamp_field]}, acquired)
        data = json.loads(metadata)
        data.update(source_observed_at=source, source_time_status=status)
        metadata = json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return metadata
