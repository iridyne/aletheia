"""Read-only economic meaning of derivatives observations; no trading authority."""

from __future__ import annotations

from datetime import datetime
import json
import math
from typing import Literal, NotRequired, TypedDict

from aletheia.data.freshness import assess_freshness


class DerivativesFact(TypedDict):
    fact_type: str
    provider: str | None
    source_scope: Literal["venue_native", "market_aggregate"]
    venue: str | None
    instrument: dict
    source_observed_at: str | None
    acquired_at: str | None
    availability: Literal["available", "stale", "unavailable", "invalid"]
    value_kind: Literal["observed", "forecast", "derived"]
    unit: str
    value: float | None
    source_ref: str | None
    unavailable_reason: str | None
    interval_seconds: NotRequired[float | None]
    event_price: NotRequired[float | None]
    side: NotRequired[str | None]
    coverage: NotRequired[str]
    population: NotRequired[str]


def finite(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _instrument_valid(instrument: dict) -> bool:
    return (
        isinstance(instrument, dict)
        and bool(instrument.get("canonical_symbol"))
        and bool(instrument.get("source_instrument_id"))
        and instrument.get("market_type") == "perp"
        and instrument.get("linear") is True
        and bool(instrument.get("base_asset"))
        and instrument.get("quote_asset") in {"USDT", "USDC", "USD"}
        and instrument.get("settle_asset") in {"USDT", "USDC", "USD"}
    )


def _fact(
    fact_type,
    value,
    *,
    instrument,
    provider,
    venue,
    acquired_at,
    source_observed_at=None,
    source_time_status=None,
    value_kind="observed",
    source_scope="venue_native",
    unit,
    reason=None,
    now=None,
    source_ref=None,
):
    acquired = acquired_at.isoformat() if isinstance(acquired_at, datetime) else acquired_at
    number = finite(value)
    if value is not None and number is None:
        reason = "nonfinite_value"
    if not _instrument_valid(instrument):
        reason = reason or "instrument_unproven"
    if source_scope == "venue_native" and not venue:
        reason = reason or "venue_unproven"
    if number is None:
        reason = reason or "value_unavailable"
    verdict = assess_freshness(
        {
            "observed_at": source_observed_at or acquired,
            "source_observed_at": source_observed_at,
            "acquired_at": acquired,
            "source_time_status": source_time_status or ("provided" if source_observed_at else "missing"),
        },
        "cross_venue_funding" if fact_type.startswith("funding") else "cross_venue_open_interest",
        now=now,
    )
    availability = "available"
    if reason:
        availability = "invalid" if reason == "nonfinite_value" else "unavailable"
    elif not verdict.usable:
        availability = "stale" if verdict.state == "stale" else "invalid"
        reason = f"source_time_{verdict.state}"
    return DerivativesFact(
        fact_type=fact_type,
        provider=provider,
        source_scope=source_scope,
        venue=venue if source_scope == "venue_native" else None,
        instrument=dict(instrument) if isinstance(instrument, dict) else {},
        source_observed_at=source_observed_at,
        acquired_at=acquired,
        availability=availability,
        value_kind=value_kind,
        unit=unit,
        value=number if availability == "available" else None,
        source_ref=source_ref,
        unavailable_reason=reason,
    )


def normalize_funding(raw, *, instrument, provider, venue, acquired_at, interval_seconds=None, value_kind="observed"):
    from aletheia.data.collectors.derivatives import provider_source_time

    source, status = provider_source_time(raw, acquired_at)
    result = _fact(
        "funding_rate",
        raw.get("fundingRate"),
        instrument=instrument,
        provider=provider,
        venue=venue,
        acquired_at=acquired_at,
        source_observed_at=source,
        source_time_status=status,
        value_kind=value_kind,
        unit="rate_per_settlement",
        now=acquired_at,
    )
    interval = finite(interval_seconds)
    result["interval_seconds"] = interval if interval is not None and interval > 0 else None
    return result


def normalize_open_interest(
    raw, *, instrument, provider, venue, acquired_at, amount_unit=None, mark_price=None, value_unit=None
):
    from aletheia.data.collectors.derivatives import provider_source_time

    source, status = provider_source_time(raw, acquired_at)
    amount, mark = finite(raw.get("openInterestAmount")), finite(mark_price)
    value, reason = None, "oi_unit_unproven"
    if value_unit == "USD":
        value, reason = raw.get("openInterestValue"), None
    elif amount_unit == "base" and amount is not None and mark is not None and mark > 0:
        value, reason = amount * mark, None
    elif amount_unit == "contracts" and amount is not None and mark is not None and mark > 0:
        size = finite(instrument.get("contract_size"))
        if size is not None and size > 0:
            value, reason = amount * size * mark, None
    if finite(value) is not None and finite(value) < 0:
        value, reason = None, "negative_open_interest"
    return _fact(
        "open_interest",
        value,
        instrument=instrument,
        provider=provider,
        venue=venue,
        acquired_at=acquired_at,
        source_observed_at=source,
        source_time_status=status,
        unit="USD",
        reason=reason,
        now=acquired_at,
    )


def project_liquidation(row, *, source_scope, instrument, now, reason=None):
    result = _fact(
        "liquidation",
        row.get("quantity_usd"),
        instrument=instrument,
        provider="coinalyze" if row.get("exchange") == "coinalyze" else row.get("provider"),
        venue=row.get("exchange"),
        source_scope=source_scope,
        unit="USD",
        source_observed_at=row.get("event_time"),
        acquired_at=row.get("observed_at"),
        now=now,
        source_ref=row.get("id"),
        reason=reason,
    )
    price = finite(row.get("price"))
    result.update(
        event_price=price if price is not None and price > 0 else None,
        side=row.get("side"),
        coverage="provider_reported",
    )
    return result


def instrument_from_market(symbol: str, market: dict) -> dict:
    return dict(
        canonical_symbol=symbol,
        source_instrument_id=market.get("id"),
        market_type="perp" if market.get("swap") else market.get("type"),
        base_asset=market.get("base"),
        quote_asset=market.get("quote"),
        settle_asset=market.get("settle"),
        linear=market.get("linear"),
        contract_size=market.get("contractSize"),
    )


def snapshot_metadata(
    *,
    provider,
    instrument,
    value_kind="observed",
    interval_seconds=None,
    oi_unit=None,
    source_scope="venue_native",
    venue=None,
    acquired_at=None,
    ratios=None,
) -> str:
    """Only source semantics missing from the existing physical scalar columns."""
    return json.dumps(
        dict(
            provider=provider,
            instrument=instrument,
            value_kind=value_kind,
            interval_seconds=interval_seconds,
            oi_unit=oi_unit,
            source_scope=source_scope,
            venue=venue,
            acquired_at=acquired_at,
            ratios=ratios,
        ),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def ccxt_metadata(symbol, venue, market, raw, *, family, ratios=None):
    """Persist only semantics proved by the current CCXT response, never its fallback arithmetic."""
    interval = raw.get("interval")
    hours = finite(interval[:-1]) if isinstance(interval, str) and interval.endswith("h") else None
    info = raw.get("info") or {}
    unit = None
    # CCXT 4.5.56 maps these explicitly USD-native fields to openInterestValue.
    # Amount-only responses stay unknown here until a compatible mark/unit is proved.
    if isinstance(info, dict):
        fields = {"okx": ("oiUsd",), "binance": ("sumOpenInterestValue", "sumOpenInterestUsd")}.get(venue, ())
        value = finite(raw.get("openInterestValue"))
        if value is not None and any(finite(info.get(key)) == value for key in fields):
            unit = "USD"
    return snapshot_metadata(
        provider="ccxt",
        venue=venue,
        instrument=instrument_from_market(symbol, market),
        interval_seconds=hours * 3600 if hours is not None and hours > 0 and family == "funding" else None,
        oi_unit=unit if family == "open_interest" else None,
        ratios=ratios,
    )


def hyperliquid_metadata(symbol, acquired_at, *, value_kind="observed", interval_seconds=3600, oi=False):
    # Native metaAndAssetCtxs / HlPerp are USDC-settled; the legacy USDT label is display-only.
    return snapshot_metadata(
        provider="hyperliquid_public",
        venue="hyperliquid",
        instrument=dict(
            canonical_symbol=symbol,
            source_instrument_id=symbol.split("/")[0],
            market_type="perp",
            base_asset=symbol.split("/")[0],
            quote_asset="USDC",
            settle_asset="USDC",
            linear=True,
        ),
        value_kind="derived" if oi else value_kind,
        interval_seconds=interval_seconds if not oi else None,
        oi_unit="USD" if oi else None,
        acquired_at=acquired_at,
    )


def project_snapshot(row: dict, family: Literal["funding", "open_interest"], *, now=None) -> list[dict]:
    """Project a stored observation without enriching or rewriting historical rows."""
    try:
        metadata = json.loads(row.get("derivatives_metadata_json") or "{}")
        if not isinstance(metadata, dict):
            metadata = {}
    except (TypeError, ValueError):
        metadata = {}
    instrument = metadata.get("instrument") or {"canonical_symbol": row.get("symbol")}
    aggregate = row.get("exchange") == "coinalyze"
    provider = metadata.get("provider") or ("coinalyze" if aggregate else None)
    common = dict(
        instrument=instrument,
        provider=provider,
        venue=metadata.get("venue"),
        acquired_at=row.get("acquired_at") or metadata.get("acquired_at"),
        source_observed_at=row.get("source_observed_at"),
        source_time_status=row.get("source_time_status"),
        source_ref=row.get("id"),
        now=now,
        source_scope=metadata.get("source_scope", "market_aggregate" if aggregate else "venue_native"),
        value_kind=metadata.get("value_kind", "observed"),
    )
    reason = None if metadata else "legacy_provenance_unknown"
    if family == "funding":
        fact = _fact("funding_rate", row.get("funding_rate"), unit="rate_per_settlement", reason=reason, **common)
        fact["interval_seconds"] = metadata.get("interval_seconds")
        return [fact]
    fact = _fact(
        "open_interest",
        row.get("open_interest_usd"),
        unit="USD",
        reason=reason or (None if metadata.get("oi_unit") == "USD" else "oi_unit_unproven"),
        **common,
    )
    result = [fact]
    for field, population in (
        ("long_short_ratio", "global_accounts"),
        ("top_trader_position_ratio", "top_trader_positions"),
    ):
        provenance = (metadata.get("ratios") or {}).get(field)
        if provenance:
            ratio = _fact(
                "long_short_ratio",
                row.get(field),
                provider="binance_public",
                venue="binance",
                instrument=provenance["instrument"],
                source_observed_at=provenance["source_observed_at"],
                acquired_at=provenance["acquired_at"],
                source_time_status=provenance["source_time_status"],
                unit="ratio",
                source_ref=row.get("id"),
                now=now,
            )
            ratio["population"] = population
            result.append(ratio)
    return result


def read_derivatives_facts(symbol: str, *, venue: str, now=None) -> list[dict]:
    """Exact-venue read-only entry point; never substitute another venue's rows."""
    from aletheia.storage import repositories as repo

    result = []
    for family, table in (("funding", "funding_rate_snapshots"), ("open_interest", "open_interest_snapshots")):
        rows = repo._select_all(
            table,
            where="symbol = ? AND exchange = ?",
            params=(symbol, venue),
            order="observed_at DESC, rowid DESC",
            limit=1,
        )
        if rows:
            result.extend(project_snapshot(rows[0], family, now=now))
    if venue == "hyperliquid":
        forecast = repo.latest_funding_rate(symbol, exchange="hyperliquid_predicted")
        if forecast:
            result.extend(project_snapshot(forecast, "funding", now=now))
    if venue == "coinalyze":
        # The old API shape/suffix does not prove aggregate units or population.
        for row in repo.recent_liquidations(symbol):
            if row.get("exchange") == "coinalyze":
                result.append(
                    project_liquidation(
                        row,
                        source_scope="market_aggregate",
                        instrument={"canonical_symbol": symbol},
                        now=now,
                        reason="legacy_provenance_unknown",
                    )
                )
    return result
