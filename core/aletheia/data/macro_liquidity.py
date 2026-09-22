"""Bounded FRED source facts and observation-only, current-known research.

No vintage backfill: every point is known no earlier than its batch acquisition.
Series contracts and join/cadence limits: docs/design/data/fred_liquidity.md.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math

VERSION = "fred_liquidity_v1"
# unit, frequency, maximum source age in calendar days
SERIES = {
    "SOFR": ("Percent", "Daily", 5),
    "DGS2": ("Percent", "Daily", 5),
    "DGS10": ("Percent", "Daily", 5),
    "WALCL": ("Millions of U.S. Dollars", "Weekly, As of Wednesday", 10),
    "RRPONTSYD": ("Billions of US Dollars", "Daily", 5),
    "WDTGAL": ("Millions of U.S. Dollars", "Weekly, As of Wednesday", 10),
}
REVISION = "current_known_revised_not_historical_vintage"


def clock(value):
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        raise ValueError("timezone-aware cutoff/acquisition required")
    return stamp.astimezone(timezone.utc)


def refresh_bucket(series_id, now):
    day = now.astimezone(timezone.utc).date()
    # Friday UTC follows normal Thursday H.4.1 publication. Late releases may
    # stay unavailable until the next weekly poll; never retry weekly intraday.
    if SERIES[series_id][1] != "Daily":
        day -= timedelta(days=(day.weekday()-4) % 7)
    return day.isoformat()


def source_batch(series_id, payload, acquired_at, *, reason=None):
    """Validate one bounded native-units response; retain holes as holes."""
    acquired = clock(acquired_at)
    unit, frequency, _ = SERIES[series_id]
    points = []
    raw = payload.get("observations") if isinstance(payload, dict) else None
    if reason is None and isinstance(payload, dict) and payload.get("units") != "lin":
        reason = "native_units_unproven"
    if reason is None and (not isinstance(raw, list) or len(raw) > 32):
        reason = "invalid_or_oversized_response"
    if reason is None:
        seen = set()
        for item in raw:
            try:
                day = date.fromisoformat(item["date"])
                if day.isoformat() != item["date"] or day > acquired.date() or day in seen:
                    raise ValueError("invalid source date")
                if frequency != "Daily" and day.weekday() != 2:
                    raise ValueError("not Wednesday")
                seen.add(day)
                value = None if item["value"] == "." else float(item["value"])
                if isinstance(item["value"], bool) or (value is not None and (
                    not math.isfinite(value) or abs(value) > 1e15 or (unit != "Percent" and value < 0)
                )):
                    raise ValueError("invalid value")
                points.append(dict(observation_date=day.isoformat(), value=value,
                    availability="available" if value is not None else "unavailable",
                    unavailable_reason=None if value is not None else "source_missing",
                    realtime_start=item.get("realtime_start"), realtime_end=item.get("realtime_end")))
            except (ValueError, TypeError, KeyError):
                reason = "invalid_observation"
                points = []
                break
    points.sort(key=lambda p: p["observation_date"])
    return dict(series_id=series_id, source="fred", unit=unit, frequency=frequency,
        seasonal_adjustment="Not Seasonally Adjusted", precision="date",
        acquired_at=acquired.isoformat(), refresh_bucket=refresh_bucket(series_id, acquired),
        revision_policy=REVISION, derivation_version=VERSION,
        availability="available" if any(p["value"] is not None for p in points) else "unavailable",
        unavailable_reason=reason or (None if any(p["value"] is not None for p in points) else "source_missing"),
        observations=points)


def read_source_batches(cutoff, *, require_schema=False):
    """At most one immutable acquisition per series, without creating a DB."""
    from aletheia.storage.sqlite import readonly_db

    end = clock(cutoff).isoformat()
    with readonly_db(allow_missing=True) as db:
        if db is None or not db.execute("SELECT 1 FROM sqlite_master WHERE name='fred_source_batches'").fetchone():
            if require_schema:
                raise ValueError("fred_storage_unavailable")
            return []
        rows = [db.execute(
            "SELECT id,payload_json FROM fred_source_batches WHERE series_id=? AND acquired_at<=? "
            "ORDER BY acquired_at DESC,id DESC LIMIT 1", (series_id, end)).fetchone()
            for series_id in SERIES]
    return [{**json.loads(row["payload_json"]), "row_id": row["id"]} for row in rows if row]


def derive_macro_liquidity(batches, cutoff):
    """Pure as-of projection; frozen batches reproduce all values and lineage."""
    end = clock(cutoff)
    latest = {}
    for batch in batches:
        sid = batch.get("series_id")
        try:
            acquired = clock(batch["acquired_at"])
        except (KeyError, ValueError, TypeError):
            continue
        if sid not in SERIES or acquired > end:
            continue
        if sid not in latest or (acquired, batch.get("row_id", 0)) > (
            clock(latest[sid]["acquired_at"]), latest[sid].get("row_id", 0)
        ):
            latest[sid] = batch
    selected = {}

    def point(sid, target, max_age):
        batch = latest.get(sid)
        if not batch:
            return None, "not_observed"
        if (batch.get("unit"), batch.get("frequency")) != SERIES[sid][:2] or (
            batch.get("seasonal_adjustment") != "Not Seasonally Adjusted"
            or batch.get("revision_policy") != REVISION or batch.get("source") != "fred"
        ):
            return None, "source_contract_unproven"
        if batch.get("availability") != "available":
            return None, batch.get("unavailable_reason") or "source_unavailable"
        valid = []
        for item in batch["observations"]:
            day = date.fromisoformat(item["observation_date"])
            value = item["value"]
            if day > min(target, clock(batch["acquired_at"]).date()) or value is None:
                continue
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                continue
            if value < 0 and SERIES[sid][0] != "Percent":
                continue
            valid.append(item)
        if not valid:
            return None, "source_missing"
        item = max(valid, key=lambda p: p["observation_date"])
        if (target-date.fromisoformat(item["observation_date"])).days > max_age:
            return None, "stale_source"
        identity = f"{sid}:{batch['acquired_at']}:{item['observation_date']}"
        selected[identity] = dict(input_id=identity, series_id=sid, unit=batch["unit"],
            acquired_at=batch["acquired_at"], row_id=batch.get("row_id"), **item)
        return selected[identity], None

    def metric(value=None, inputs=(), reason="insufficient_coverage", **extra):
        if value is not None and not math.isfinite(value):
            value, reason = None, "nonfinite_derived_value"
        return dict(value=value, availability="available" if value is not None else "unavailable",
            reason=None if value is not None else reason,
            input_ids=[p["input_id"] for p in inputs if p], **extra)

    facts = {}
    for sid, (unit, frequency, age) in SERIES.items():
        p, reason = point(sid, end.date(), age)
        facts[sid] = metric(p["value"] if p else None, [p], reason,
            unit=unit, frequency=frequency, seasonal_adjustment="Not Seasonally Adjusted",
            observation_date=p["observation_date"] if p else None,
            acquired_at=latest[sid]["acquired_at"] if sid in latest else None)

    sofr, _ = point("SOFR", end.date(), 5)
    base, _ = point("SOFR", date.fromisoformat(sofr["observation_date"])-timedelta(days=7), 3) if sofr else (None, None)
    change = metric((sofr["value"]-base["value"])*100 if sofr and base else None, [sofr, base], unit="basis_points")
    rates = dict(level=facts["SOFR"], change_7d=change,
        state="unavailable" if change["value"] is None else (
            "tightening" if change["value"] > 0 else "easing" if change["value"] < 0 else "neutral"),
        interpretation="SOFR_change_only_not_policy_stance")

    def curve_at(target, age):
        two, _ = point("DGS2", target, age)
        ten, _ = point("DGS10", target, age)
        aligned = two and ten and two["observation_date"] == ten["observation_date"]
        return metric((ten["value"]-two["value"])*100 if aligned else None, [two, ten],
                      "same_date_yields_unavailable", unit="basis_points")

    curve = curve_at(end.date(), 5)
    current_two, _ = point("DGS2", end.date(), 5)
    previous_curve = curve_at(date.fromisoformat(current_two["observation_date"])-timedelta(days=7), 3) if current_two else metric()
    curve["state"] = "unavailable" if curve["value"] is None else (
        "normal" if curve["value"] > 0 else "inverted" if curve["value"] < 0 else "flat")
    curve["change_7d"] = metric(curve["value"]-previous_curve["value"]
        if curve["value"] is not None and previous_curve["value"] is not None else None,
        [selected[i] for i in curve["input_ids"]+previous_curve["input_ids"]], unit="basis_points")

    fed, _ = point("WALCL", end.date(), 10)
    anchor = date.fromisoformat(fed["observation_date"]) if fed else None
    components = {}
    for sid, sign, divisor in (("WALCL", 1, 1000), ("RRPONTSYD", -1, 1), ("WDTGAL", -1, 1000)):
        # Weekly stocks must be on both exact Wednesdays. Daily RRP can use the
        # preceding business day, at most 3 calendar days back, separately shown.
        age = 3 if sid == "RRPONTSYD" else 0
        current, _ = point(sid, anchor, age) if anchor else (None, None)
        previous, _ = point(sid, anchor-timedelta(days=7), age) if anchor else (None, None)
        components[sid] = metric((current["value"]-previous["value"])/divisor
            if current and previous else None, [current, previous], unit="billion_USD", sign=sign)
    complete = all(c["value"] is not None for c in components.values())
    liquidity = metric(sum(c["sign"]*c["value"] for c in components.values()) if complete else None,
        [selected[i] for c in components.values() for i in c["input_ids"]],
        "incomplete_weekly_components", unit="billion_USD", components=components,
        as_of_date=anchor.isoformat() if anchor else None,
        formula="delta(WALCL)/1000 - delta(RRPONTSYD) - delta(WDTGAL)/1000",
        coverage=sum(c["value"] is not None for c in components.values())/3)
    frozen = [latest[sid] for sid in sorted(latest)]
    return dict(authority="observation_only", cutoff=end.isoformat(), derivation_version=VERSION,
        revision_policy=REVISION, source_facts=facts, rates_regime=rates,
        curve_regime=curve, dollar_liquidity_impulse=liquidity,
        coverage=sum(f["value"] is not None for f in facts.values())/len(SERIES),
        inputs=[selected[i] for i in sorted(selected)], frozen_batches=frozen,
        input_fingerprint=hashlib.sha256(json.dumps(frozen, sort_keys=True, allow_nan=False).encode()).hexdigest(),
        outcome_join=dict(decision_cutoff=end.isoformat(), scope="global_macro",
            horizons=["15m", "1h", "4h"], population="all_observations_not_selected_trades"))
