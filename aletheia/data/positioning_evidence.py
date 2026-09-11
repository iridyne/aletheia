"""Point-in-time research projection over existing canonical derivatives facts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from statistics import median

from aletheia.data.derivatives_facts import (
    _instrument_valid, annualize_funding, finite, project_price_snapshot, project_snapshot,
)

VERSION = "positioning_evidence_v1"
FAMILIES = ("funding_rate", "open_interest", "mark_price", "perpetual_premium")
DEFAULT_VENUES = ("binance", "okx", "bitget", "hyperliquid")


def _clock(value):
    try:
        stamp = datetime.fromisoformat(value)
        return stamp.astimezone(timezone.utc) if stamp.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


def _id(fact):
    return f"{fact['fact_type']}:{fact.get('provider')}:{fact['source_ref']}"


def _metric(value=None, reason="insufficient_coverage", inputs=()):
    number = finite(value)
    if value is not None and number is None:
        reason = "nonfinite_value"
    return dict(value=number, availability="available" if number is not None else "unavailable",
                reason=None if number is not None else reason, input_ids=sorted({_id(f) for f in inputs}))


def _reason(fact, cutoff, *, current=True):
    if fact is None:
        return "not_observed"
    source, acquired = _clock(fact.get("source_observed_at")), _clock(fact.get("acquired_at"))
    if not source or not acquired or source > acquired or acquired > cutoff:
        return "source_time_unproven"
    if current and (cutoff-min(source, acquired)).total_seconds() > 1800:
        return "stale"
    if (acquired-source).total_seconds() > 1800:
        return "stale_at_acquisition"
    if fact.get("availability") != "available":
        return fact.get("unavailable_reason") or "canonical_unavailable"
    if fact.get("source_scope") != "venue_native" or fact.get("provider") == "coinalyze":
        return "not_exact_venue"
    if not _instrument_valid(fact.get("instrument", {})) or not fact.get("provider") or not fact.get("source_ref"):
        return "identity_unproven"
    if finite(fact.get("value")) is None:
        return "nonfinite_value"
    return None


def _aligned(facts):
    times = [_clock(f["source_observed_at"]) for f in facts]
    return bool(times) and all(times) and (max(times)-min(times)).total_seconds() <= 300


def _change(current, history, cutoff):
    if _reason(current, cutoff):
        return _metric(reason=_reason(current, cutoff)), None
    expected_unit = "USD" if current["fact_type"] == "open_interest" else current["instrument"]["quote_asset"]
    if current["unit"] != expected_unit or current["value"] < 0 or (current["fact_type"] == "mark_price" and current["value"] == 0):
        return _metric(reason="unit_or_value_unproven", inputs=[current]), None
    same = [f for f in history if not _reason(f, cutoff, current=False)
            and all(f.get(k) == current.get(k) for k in
                    ("fact_type", "provider", "source_scope", "venue", "instrument", "unit", "value_kind"))
            and f["value"] > 0
            and 2700 <= (_clock(current["source_observed_at"])-_clock(f["source_observed_at"])).total_seconds() <= 4500]
    if not same:
        return _metric(reason="compatible_1h_history_unavailable", inputs=[current]), None
    baseline = min(same, key=lambda f: (
        abs((_clock(current["source_observed_at"])-_clock(f["source_observed_at"])).total_seconds()-3600), _id(f)))
    return _metric(finite(current["value"]/baseline["value"]-1), reason="nonfinite_change",
                   inputs=[current, baseline]), baseline


def derive_positioning_evidence(symbol, facts, cutoff, *, venues=DEFAULT_VENUES):
    """Pure, reproducible kernel; no latest-time fallback or future observations."""
    end = _clock(cutoff)
    if end is None or not isinstance(symbol, str) or "/" not in symbol:
        raise ValueError("canonical symbol and timezone-aware cutoff required")
    venues = tuple(dict.fromkeys(venues))
    if not venues or len(venues) > 8 or any(v not in DEFAULT_VENUES for v in venues):
        raise ValueError("select 1-4 existing exact venues")
    known = [f for f in facts if f.get("source_ref") and _clock(f.get("acquired_at")) is not None
             and _clock(f["acquired_at"]) <= end
             and (_clock(f.get("source_observed_at")) is None or _clock(f["source_observed_at"]) <= end)
             and f.get("instrument", {}).get("canonical_symbol") == symbol
             and f.get("value_kind") != "forecast" and f.get("fact_type") in FAMILIES]
    latest = {}
    for fact in sorted(known, key=lambda f: (_clock(f.get("source_observed_at")) or _clock(f["acquired_at"]), _clock(f["acquired_at"]), _id(f))):
        if fact.get("venue") in venues and fact.get("source_scope") == "venue_native" and fact.get("provider") != "coinalyze":
            latest[fact["venue"], fact["fact_type"]] = fact
    inputs = {_id(f): f for f in latest.values()}
    per_venue, groups = {}, {}
    for venue in venues:
        selected = {family: latest.get((venue, family)) for family in FAMILIES}
        metrics = {}
        for family, name, unit in (("funding_rate", "funding_annualized", "rate_per_settlement"),
                                   ("open_interest", "oi_usd", "USD"),
                                   ("perpetual_premium", "basis_bps", "ratio")):
            fact = selected[family]
            reason = _reason(fact, end)
            if not reason and fact["unit"] != unit:
                reason = "unit_unproven"
            value = None
            if not reason:
                value = fact["value"]
                if family == "funding_rate":
                    value = annualize_funding(value, fact.get("interval_seconds"))
                    interval = finite(fact.get("interval_seconds"))
                    reason = "nonfinite_value" if interval and interval>0 else "funding_interval_unproven"
                elif family == "perpetual_premium":
                    value = finite(value*10000) if fact.get("reference_kind") == "index" else None
                    reason = "nonfinite_value" if fact.get("reference_kind") == "index" else "index_reference_unproven"
                elif value < 0:
                    value, reason = None, "negative_oi"
            metrics[name] = _metric(value, reason or "nonfinite_value", [fact] if fact else [])
            if metrics[name]["availability"] == "available":
                instrument = fact["instrument"]
                key = tuple(instrument[k] for k in ("base_asset", "market_type", "quote_asset", "settle_asset"))
                groups.setdefault(key, {}).setdefault(name, {})[venue] = (value, fact)
        oi, price = selected["open_interest"], selected["mark_price"]
        oi_change, oi_base = _change(oi, known, end)
        price_change, price_base = _change(price, known, end)
        state = "unavailable"
        reason = "compatible_price_oi_history_unavailable"
        if (metrics["oi_usd"]["availability"] == "available" and oi_base and price_base
            and oi_change["value"] is not None and price_change["value"] is not None
            and all(oi.get(k) == price.get(k) for k in ("provider", "venue", "instrument"))
            and _aligned([oi, price]) and _aligned([oi_base, price_base])):
            label = lambda n: "up" if n > 0 else "down" if n < 0 else "flat"
            state, reason = f"price_{label(price_change['value'])}_oi_{label(oi_change['value'])}", None
        for baseline in (oi_base, price_base):
            if baseline:
                inputs[_id(baseline)] = baseline
        metrics.update(oi_change_1h=oi_change, price_change_1h=price_change,
                       price_oi_state=dict(state=state, reason=reason,
                           input_ids=sorted(set(oi_change["input_ids"]+price_change["input_ids"]))))
        per_venue[venue] = metrics
    comparisons = []
    for key, families in sorted(groups.items()):
        group = dict(zip(("base_asset", "market_type", "quote_asset", "settle_asset"), key))
        for name in ("funding_annualized", "basis_bps", "oi_usd"):
            values = families.get(name, {})
            parents = [value[1] for value in values.values()]
            numbers = [value[0] for value in values.values()]
            aligned = _aligned(parents)
            reason = "source_skew" if numbers and not aligned else "insufficient_venues"
            item = dict(values_by_venue={v: x[0] for v, x in sorted(values.items())},
                        covered_venues=sorted(values), expected_venues=list(venues),
                        coverage=len(values)/len(venues),
                        median=_metric(median(numbers) if len(numbers)>=2 and aligned else None, reason, parents),
                        dispersion=_metric(max(numbers)-min(numbers) if len(numbers)>=2 and aligned else None, reason, parents))
            if name == "oi_usd":
                total = finite(sum(numbers)) if numbers and aligned else None
                item["subtotal"] = _metric(total, "nonfinite_subtotal" if numbers and aligned and total is None else reason, parents)
                item["shares"] = {v: x[0]/total for v,x in sorted(values.items())} if total and total>0 else None
                med = item["median"]["value"]
                item["disagreement_ratio"] = _metric((max(numbers)-min(numbers))/med if med and aligned else None,
                                                     reason if not med else "nonfinite_value", parents)
            group[name] = item
        comparisons.append(group)
    frozen = [inputs[k] for k in sorted(inputs)]
    payload = dict(symbol=symbol, cutoff=end.isoformat(), authority="observation_only", available=True,
                   value_kind="derived", input_sampling="public_snapshots",
                   derivation_version=VERSION, expected_venues=list(venues), exact_venues=per_venue,
                   comparisons=comparisons, inputs=frozen,
                   units=dict(funding_annualized="ratio/year", basis_bps="bps", oi_usd="USD", changes="ratio"),
                   outcome_join=dict(symbol=symbol, decision_cutoff=end.isoformat(), horizons=["15m","1h","4h"], population="all_observations_not_selected_trades"),
                   funding_extreme=_metric(reason="historical_distribution_unqualified"),
                   oi_acceleration=_metric(reason="equal_interval_history_unqualified"),
                   crowding_state=dict(state="unavailable", reason="history_and_rule_unqualified"),
                   liquidation_impulse=_metric(reason="aggregate_source_contract_unproven"),
                   aggregate_oi_comparison=_metric(reason="aggregate_population_units_unproven"))
    payload["fingerprint"] = hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return payload


def read_positioning_evidence(symbol, cutoff, *, venues=DEFAULT_VENUES):
    from aletheia.storage.sqlite import readonly_db

    end = _clock(cutoff)
    if end is None:
        raise ValueError("timezone-aware cutoff required")
    facts = []
    with readonly_db(allow_missing=True) as db:
        if db is None:
            return dict(available=False, reason="database_unavailable", authority="observation_only")
        for family, table in (("funding", "funding_rate_snapshots"),
                              ("open_interest", "open_interest_snapshots"), ("price", "perp_price_snapshots")):
            acquired = "acquired_at" if family == "price" else "COALESCE(acquired_at,json_extract(derivatives_metadata_json,'$.acquired_at'))"
            rows = db.execute(f"SELECT * FROM {table} WHERE symbol=? "
                              f"AND julianday({acquired}) BETWEEN julianday(?) AND julianday(?) "
                              f"ORDER BY julianday({acquired}) DESC,rowid DESC LIMIT 2049",
                              (symbol, (end-timedelta(minutes=105)).isoformat(), end.isoformat())).fetchall()
            if len(rows)>2048:
                return dict(available=False, reason="input_limit_exceeded", authority="observation_only")
            for row in map(dict, rows):
                acquired_at = row.get("acquired_at")
                if not acquired_at and family != "price":
                    acquired_at = json.loads(row.get("derivatives_metadata_json") or "{}").get("acquired_at")
                now = _clock(acquired_at)
                if now is None:
                    continue
                facts.extend(project_price_snapshot(row, now=now) if family == "price" else project_snapshot(row, family, now=now))
    return derive_positioning_evidence(symbol, facts, cutoff, venues=venues)
