"""Read-only economic meaning of derivatives observations; no trading authority."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
import hashlib
from typing import Literal, NotRequired, TypedDict

from aletheia.data.freshness import assess_freshness
from aletheia.utils.dt import parse_utc


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
    reference_kind: NotRequired[str | None]


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
        and isinstance(instrument.get("canonical_symbol"), str) and bool(instrument['canonical_symbol'])
        and bool(instrument.get("source_instrument_id"))
        and instrument.get("market_type") == "perp"
        and instrument.get("linear") is True
        and bool(instrument.get("base_asset"))
        and instrument['canonical_symbol'].split('/')[0] == instrument['base_asset']
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




def normalize_funding(rate, *, instrument, provider, venue, acquired_at,
                      source_observed_at=None, source_time_status="missing",
                      interval_seconds=None, value_kind="observed"):
    result = _fact(
        "funding_rate",
        rate,
        instrument=instrument,
        provider=provider,
        venue=venue,
        acquired_at=acquired_at,
        source_observed_at=source_observed_at,
        source_time_status=source_time_status,
        value_kind=value_kind,
        unit="rate_per_settlement",
        now=acquired_at,
    )
    interval = finite(interval_seconds)
    result["interval_seconds"] = interval if interval is not None and interval > 0 else None
    return result


def annualize_funding(rate, interval_seconds):
    rate, interval = finite(rate), finite(interval_seconds)
    return finite(rate * (365 * 86400 / interval)) if rate is not None and interval is not None and interval > 0 else None


def open_interest_usd(*, instrument, amount=None, reported_value=None,
                      amount_unit=None, mark_price=None, value_unit=None):
    """The sole unit conversion primitive; callers supply source-proved semantics."""
    if not _instrument_valid(instrument):
        return None, 'instrument_unproven'
    amount, mark = finite(amount), finite(mark_price)
    value, reason = None, "oi_unit_unproven"
    if value_unit == "USD":
        value, reason = reported_value, None
    elif amount_unit == "base" and amount is not None and mark is not None and mark > 0:
        value, reason = amount * mark, None
    elif amount_unit == "contracts" and amount is not None and mark is not None and mark > 0:
        size = finite(instrument.get("contract_size"))
        if size is not None and size > 0:
            value, reason = amount * size * mark, None
    if finite(value) is not None and finite(value) < 0:
        value, reason = None, "negative_open_interest"
    number = finite(value)
    if value is not None and number is None:
        reason = 'nonfinite_value'
    return number, reason or ('value_unavailable' if number is None else None)


def normalize_open_interest(
    *, instrument, provider, venue, acquired_at, amount=None, reported_value=None,
    source_observed_at=None, source_time_status="missing",
    amount_unit=None, mark_price=None, value_unit=None
):

    value, reason = open_interest_usd(amount=amount, reported_value=reported_value,
        instrument=instrument, amount_unit=amount_unit,
        mark_price=mark_price, value_unit=value_unit)
    return _fact(
        "open_interest",
        value,
        instrument=instrument,
        provider=provider,
        venue=venue,
        acquired_at=acquired_at,
        source_observed_at=source_observed_at,
        source_time_status=source_time_status,
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




def comparison_row(row: dict) -> dict:
    """Mark the existing physical row as observation-only and make repeat persistence idempotent."""
    metadata = json.loads(row['derivatives_metadata_json'])
    metadata['observation_only'] = True
    if metadata.get('source_observed_at'):
        row.update(source_observed_at=metadata['source_observed_at'],
            observed_at=metadata['source_observed_at'], source_time_status=metadata['source_time_status'])
    row['derivatives_metadata_json'] = json.dumps(metadata, sort_keys=True, separators=(',', ':'), allow_nan=False)
    identity = dict(row)
    identity.pop('id', None)
    if row.get('source_observed_at'):
        identity.pop('acquired_at', None)
    row['id'] = hashlib.sha256(json.dumps(identity, sort_keys=True, allow_nan=False).encode()).hexdigest()[:24]
    return row


def price_snapshot(symbol, venue, *, instrument, acquired_at, provider,
                   mark_price, reference_price, source_observed_at=None,
                   source_time_status='missing', reference_kind='index'):
    """Mark and reference from one response share a clock; no cross-response premium guess."""
    if not _instrument_valid(instrument):
        return None
    source, status = source_observed_at, source_time_status
    mark, reference = finite(mark_price), finite(reference_price)
    mark = mark if mark is not None and mark > 0 else None
    reference = reference if reference is not None and reference > 0 else None
    if mark is None and reference is None:
        return None
    row = dict(symbol=symbol, exchange=venue, provider=provider,
        instrument_json=json.dumps(instrument, sort_keys=True, allow_nan=False),
        mark_price=mark, reference_price=reference, reference_kind=reference_kind if reference is not None else None,
        source_observed_at=source, acquired_at=acquired_at.isoformat(), source_time_status=status,
        observed_at=source or acquired_at.isoformat())
    identity = {k:v for k,v in row.items() if k != 'acquired_at' or source is None}
    row['id'] = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
    return row


def project_price_snapshot(row, *, now=None):
    instrument = json.loads(row['instrument_json'])
    common = dict(instrument=instrument, provider=row['provider'], venue=row['exchange'],
        source_observed_at=row['source_observed_at'], acquired_at=row['acquired_at'],
        source_time_status=row['source_time_status'], source_ref=row['id'],
        unit=instrument['quote_asset'], now=now)
    mark = _fact('mark_price', row['mark_price'], **common)
    reference = _fact('reference_price', row['reference_price'],
        reason='reference_kind_unproven' if row['reference_price'] is not None and not row['reference_kind'] else None,
        **common)
    reference['reference_kind'] = row['reference_kind']
    premium = dict(mark, fact_type='perpetual_premium', unit='ratio', value_kind='derived',
        reference_kind=row['reference_kind'], value=None)
    if mark['availability'] == reference['availability'] == 'available':
        premium['value'] = finite(mark['value'] / reference['value'] - 1)
        if premium['value'] is None:
            premium.update(availability='invalid', unavailable_reason='nonfinite_value')
    else:
        parent = next(f for f in (mark, reference) if f['availability'] != 'available')
        premium.update(availability=parent['availability'], unavailable_reason=parent['unavailable_reason'])
    return [mark, reference, premium]


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
        source_observed_at=metadata.get('source_observed_at') or row.get("source_observed_at"),
        source_time_status=metadata.get('source_time_status') or row.get("source_time_status"),
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
            # Historical ratio records without identity fields used Binance only.
            legacy_identity = "provider" not in provenance and "venue" not in provenance
            provider = "binance_public" if legacy_identity else provenance.get("provider")
            venue = "binance" if legacy_identity else provenance.get("venue")
            ratio = _fact(
                "long_short_ratio",
                row.get(field),
                provider=provider,
                venue=venue,
                reason=None if provider and venue else "ratio_source_identity_unproven",
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
            projected = project_snapshot(rows[0], family, now=now)
            result.extend(projected)
            if family == 'open_interest' and venue != 'coinalyze':
                result.append(_open_interest_change(rows[0], projected[0]))
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
    prices = repo._select_all('perp_price_snapshots', where='symbol = ? AND exchange = ?',
        params=(symbol, venue), order='observed_at DESC, rowid DESC', limit=1)
    if prices:
        result.extend(project_price_snapshot(prices[0], now=now))
    return result


def _open_interest_change(row, current, *, include_comparison=True):
    """Same physical series only, in the existing 45–75 minute baseline band."""
    from aletheia.storage import repositories as repo
    result = dict(current, fact_type='open_interest_change_1h', value_kind='derived', unit='ratio', value=None)
    if current['availability'] != 'available':
        return result
    result.update(availability='unavailable', unavailable_reason='compatible_baseline_missing')
    clock = parse_utc(current['source_observed_at'] or current['acquired_at'])
    acquired = parse_utc(current['acquired_at'])
    candidates = repo._select_all('open_interest_snapshots',
        where='symbol = ? AND exchange = ? AND observed_at BETWEEN ? AND ?' + ('' if include_comparison else ' AND '+repo.LEGACY_DERIVATIVES_WHERE),
        params=(row['symbol'], row['exchange'], (clock-timedelta(minutes=75)).isoformat(),
            (clock-timedelta(minutes=45)).isoformat()), order='observed_at DESC', limit=64)
    valid = []
    for baseline in candidates:
        # Judge the old observation at its acquisition, not against today's freshness.
        then = parse_utc(baseline.get('acquired_at') or json.loads(baseline.get('derivatives_metadata_json') or '{}').get('acquired_at'))
        if then is None or then > acquired:
            continue
        fact = project_snapshot(baseline, 'open_interest', now=then)[0]
        stamp = parse_utc(fact['source_observed_at'] or fact['acquired_at'])
        if (fact['availability'] != 'available' or fact['value'] <= 0 or stamp is None
            or not 45*60 <= (clock-stamp).total_seconds() <= 75*60
            or bool(fact['source_observed_at']) != bool(current['source_observed_at'])
            or any(fact[k] != current[k] for k in ('provider','source_scope','venue','instrument','unit','value_kind'))):
            continue
        valid.append((abs((clock-stamp).total_seconds()-3600), fact))
    if valid:
        baseline = min(valid, key=lambda item: item[0])[1]
        value = finite(current['value']/baseline['value']-1)
        result.update(value=value, availability='available' if value is not None else 'invalid',
            unavailable_reason=None if value is not None else 'nonfinite_value', baseline_source_ref=baseline['source_ref'])
    return result


def derivatives_readiness(symbol, cfg, *, now=None):
    """One diagnostic consumer. Reads durable facts; grants no Candidate or execution authority."""
    from aletheia.storage import repositories as repo
    now = now or datetime.now(timezone.utc)
    md = cfg.market_data
    venues = list(dict.fromkeys([md.ccxt_exchange, *md.derivatives_comparison_venues,
        *(['hyperliquid'] if md.enable_hyperliquid else [])]))
    health = repo.get_collector_health_state('derivatives_comparison') or {}
    coverage = json.loads(health.get('derivatives_coverage_json') or '{}')
    coverage_fresh = assess_freshness({'observed_at': coverage.get('acquired_at')}, 'cross_venue_funding', now=now).usable
    exact, comparable = {}, []
    for venue in venues:
        facts = read_derivatives_facts(symbol, venue=venue, now=now)
        attempt = (coverage.get('venues', {}).get(venue, {}).get(symbol) or {}) if coverage_fresh else {}
        listed = attempt.get('listed')
        # A current negative listing observation overrides older stored data without deleting it.
        if listed is False:
            facts = [dict(f, value=None, availability='unavailable', unavailable_reason='not_listed') for f in facts]
        families = {}
        for family in ('funding_rate','open_interest','open_interest_change_1h','mark_price','reference_price','perpetual_premium'):
            fact = next((f for f in facts if f['fact_type']==family and f['value_kind']!='forecast'), None)
            key = {'funding_rate':'funding','open_interest':'open_interest','mark_price':'price','reference_price':'price'}.get(family)
            reason = attempt.get(key, 'not_observed')
            if reason in {'available','owned_primary'}:
                reason = 'not_persisted'
            families[family] = dict(availability=fact['availability'] if fact else 'unavailable',
                reason=fact['unavailable_reason'] if fact else reason)
        exact[venue] = dict(listed=listed, latest_attempt=attempt, families=families, facts=facts)
        comparable.extend(f for f in facts if f['availability']=='available' and f['source_scope']=='venue_native')
    groups = {}
    for fact in comparable:
        family = fact['fact_type']
        if family not in {'funding_rate','open_interest','perpetual_premium'} or fact['value_kind']=='forecast':
            continue
        value = fact['value']
        if family == 'funding_rate':
            interval = finite(fact.get('interval_seconds'))
            if interval is None or interval <= 0:
                continue
            value = finite(value * 3600 / interval)
            if value is None:
                continue
        instrument = fact['instrument']
        # Do not silently assume parity between USDT- and USDC-settled series.
        key = (family, *(instrument[k] for k in ('base_asset','market_type','quote_asset','settle_asset')), fact.get('reference_kind'))
        groups.setdefault(key, {})[fact['venue']] = value
    comparisons = []
    for key, values in groups.items():
        if len(values) < 2:
            continue
        family, base, market, quote, settle, reference = key
        numbers = list(values.values())
        item = dict(fact_type=family, base_asset=base, market_type=market, quote_asset=quote,
            settle_asset=settle, reference_kind=reference, covered_venues=sorted(values), values_by_venue=values,
            unit='USD' if family=='open_interest' else 'rate_per_hour' if family=='funding_rate' else 'ratio',
            dispersion=finite(max(numbers)-min(numbers)))
        if family == 'open_interest':
            item['covered_subtotal'] = finite(sum(numbers))
        comparisons.append(item)
    return dict(symbol=symbol, mode='observe_only', observed_at=now.isoformat(), configured_venues=venues,
        coverage_acquired_at=coverage.get('acquired_at'), coverage_fresh=coverage_fresh,
        exact_venues=exact, aggregate_providers={'coinalyze':read_derivatives_facts(symbol, venue='coinalyze', now=now)} if md.enable_coinalyze else {},
        comparisons=comparisons)


def project_runtime_snapshot(row, family, *, now=None):
    """Current carrier view; raw history stays in storage, unproved economics do not leak out."""
    if row is None:
        return None
    fact = project_snapshot(row, family, now=now)[0]
    if family == 'funding':
        value = annualize_funding(fact['value'], fact.get('interval_seconds'))
        return dict(row, funding_rate_annualized=value)
    if fact['availability'] != 'available':
        return None
    delta = _open_interest_change(row, fact, include_comparison=False)['value']
    return dict(row, open_interest_usd=fact['value'], oi_change_1h_pct=delta*100 if delta is not None else None)
