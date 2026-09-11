"""Bounded observation-only Deribit mark-IV projection; no market requests."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone
from statistics import NormalDist
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

VERSION = "deribit_mark_iv_forward_delta_v1"
DELTA_CONVENTION = "black_undiscounted_forward_non_premium_adjusted_act365.25"
TENOR_BOUNDARIES = {7: 2, 30: 7, 60: 14}


class OptionsVolatilitySnapshot(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    symbol: Literal["BTC", "ETH"]
    source: Literal["deribit_public"] = "deribit_public"
    authority: Literal["observation_only"] = "observation_only"
    derivation_version: str = VERSION
    delta_convention: str = DELTA_CONVENTION
    acquired_at: str
    source_time: str | None = None
    tenors: dict = Field(default_factory=dict)
    atm_iv_7d: float | None = None
    atm_iv_30d: float | None = None
    atm_iv_60d: float | None = None
    term_slope_7d_30d: float | None = None
    term_slope_30d_60d: float | None = None
    call_25d_iv_30d: float | None = None
    put_25d_iv_30d: float | None = None
    risk_reversal_25d_30d: float | None = None
    skew_25d_30d: float | None = None
    volatility_regime: str = "unavailable"
    coverage: dict = Field(default_factory=dict)
    inputs: list[dict] = Field(default_factory=list)


def _positive(value) -> float:
    if isinstance(value, bool):
        raise ValueError("invalid_numeric")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError("invalid_numeric")
    return result


def _point(value=None, *, state="unavailable", reason=None, inputs=()):
    return {"value": value, "state": state, "reason": reason,
            "input_ids": [row["id"] for row in inputs]}


def _delta_point(rows: list[dict], side: str) -> dict:
    points = sorted(((abs(row["delta"]), row) for row in rows
                     if row["side"] == side and 0.10 <= abs(row["delta"]) <= 0.40),
                    key=lambda point: (point[0], point[1]["instrument"]))
    exact = [row for delta, row in points if abs(delta - 0.25) <= 1e-12]
    if len(exact) == 1:
        return _point(exact[0]["iv"], state="exact", inputs=exact)
    lower = [(d, r) for d, r in points if d < 0.25]
    upper = [(d, r) for d, r in points if d > 0.25]
    if not lower or not upper:
        return _point(reason=f"insufficient_25delta_{side}_coverage")
    lo, a = lower[-1]
    hi, b = upper[0]
    if hi - lo > 0.15 + 1e-12:
        return _point(reason="delta_interpolation_boundary")
    value = a["iv"] + (b["iv"] - a["iv"]) * (0.25 - lo) / (hi - lo)
    return _point(value, state="interpolated", inputs=[a, b])


def derive_options_volatility(currency: str, items: list, acquired_at: str) -> OptionsVolatilitySnapshot:
    # Shared instrument parser retains the existing GEX expiry convention.
    from aletheia.data.collectors.options_structure import _parse_instrument_name

    acquired = datetime.fromisoformat(acquired_at)
    if acquired.tzinfo is None:
        raise ValueError("acquisition_timezone_required")
    snapshot = OptionsVolatilitySnapshot(symbol=currency, acquired_at=acquired_at)
    rejected: Counter = Counter()
    names = Counter(row.get("instrument_name") for row in items if isinstance(row, dict))
    expiries: dict[datetime, list[dict]] = {}
    for item in items:
        try:
            if not isinstance(item, dict):
                raise ValueError("invalid_row")
            name = item.get("instrument_name")
            if not isinstance(name, str) or not name.startswith(currency + "-"):
                raise ValueError("wrong_underlying")
            if names[name] != 1:
                raise ValueError("duplicate_instrument")
            parsed = _parse_instrument_name(name)
            if parsed is None:
                raise ValueError("invalid_instrument")
            strike, side, expiry = parsed
            strike = _positive(strike)
            days = (expiry - acquired).total_seconds() / 86400
            if days <= 0:
                raise ValueError("expired_instrument")
            # Retain expiry identity even when its quotes are invalid; do not
            # silently select a farther expiry to replace missing evidence.
            rows = expiries.setdefault(expiry, [])
            iv = _positive(item.get("mark_iv"))
            forward = _positive(item.get("underlying_price"))
            index = item.get("underlying_index")
            if index != "-".join(name.split("-")[:2]):
                raise ValueError("unproven_forward")
            source_ms = _positive(item.get("creation_timestamp"))
            source = datetime.fromtimestamp(source_ms / 1000, timezone.utc)
            if not 0 <= (acquired - source).total_seconds() <= 120:
                raise ValueError("stale_or_future_source")
            t = days / 365.25
            sigma = iv / 100
            d1 = (math.log(forward / strike) + sigma * sigma * t / 2) / (sigma * math.sqrt(t))
            delta = NormalDist().cdf(d1) - (1 if side == "P" else 0)
            row = {"instrument": name, "strike": strike, "side": side,
                   "expiry": expiry.isoformat(), "iv": iv, "forward": forward,
                   "underlying_index": index, "source_time": source.isoformat(),
                   "delta": delta}
            row["id"] = hashlib.sha256(json.dumps(row, sort_keys=True, allow_nan=False).encode()).hexdigest()
            rows.append(row)
        except (ValueError, TypeError, OverflowError, OSError) as exc:
            rejected[str(exc) if isinstance(exc, ValueError) else "invalid_numeric"] += 1

    selected: dict[str, dict] = {}
    for target, boundary in TENOR_BOUNDARIES.items():
        tenor = {"target_days": target, "actual_days": None, "expiry": None,
                 "state": "unavailable", "reason": "tenor_selection_boundary",
                 "atm": _point(reason="tenor_unavailable"),
                 "call_25d": _point(reason="tenor_unavailable"),
                 "put_25d": _point(reason="tenor_unavailable")}
        snapshot.tenors[str(target)] = tenor
        if not expiries:
            continue
        expiry = min(expiries, key=lambda e: (abs((e-acquired).total_seconds()/86400-target), e))
        days = (expiry-acquired).total_seconds()/86400
        if abs(days-target) > boundary:
            continue
        rows = expiries[expiry]
        tenor.update(actual_days=days, expiry=expiry.isoformat(),
                     state="exact" if abs(days-target) < 1e-12 else "nearest", reason=None,
                     valid_instruments=len(rows))
        if not rows:
            tenor.update(state="unavailable", reason="no_valid_quotes")
            continue
        times = [datetime.fromisoformat(r["source_time"]) for r in rows]
        forwards = [r["forward"] for r in rows]
        if (max(times)-min(times)).total_seconds() > 30 or max(forwards)/min(forwards) > 1.001:
            tenor.update(state="unavailable", reason="source_skew_or_forward_disagreement")
            continue
        pairs: dict[float, dict] = {}
        for row in rows:
            if abs(row["strike"]/row["forward"]-1) <= 0.05:
                pairs.setdefault(row["strike"], {})[row["side"]] = row
        complete = [p for p in pairs.values() if set(p) == {"C", "P"}]
        if complete:
            pair = min(complete, key=lambda p: (abs(p["C"]["strike"]/p["C"]["forward"]-1), p["C"]["strike"]))
            tenor["atm"] = _point(pair["C"]["iv"]/2+pair["P"]["iv"]/2,
                                  state="nearest", inputs=list(pair.values()))
        else:
            tenor["atm"] = _point(reason="missing_atm_call_put_pair")
        if target == 30:
            tenor["call_25d"] = _delta_point(rows, "C")
            tenor["put_25d"] = _delta_point(rows, "P")
        for key in ("atm", "call_25d", "put_25d"):
            ids = tenor[key]["input_ids"]
            selected.update({r["id"]: r for r in rows if r["id"] in ids})
        setattr(snapshot, f"atm_iv_{target}d", tenor["atm"]["value"])

    snapshot.inputs = sorted(selected.values(), key=lambda r: r["id"])
    if snapshot.inputs:
        snapshot.source_time = min(r["source_time"] for r in snapshot.inputs)
    selected_times = [datetime.fromisoformat(r["source_time"]) for r in snapshot.inputs]
    aligned = not selected_times or (max(selected_times)-min(selected_times)).total_seconds() <= 30
    for front, back in ((7, 30), (30, 60)):
        a, b = getattr(snapshot, f"atm_iv_{front}d"), getattr(snapshot, f"atm_iv_{back}d")
        if aligned and a is not None and b is not None:
            setattr(snapshot, f"term_slope_{front}d_{back}d", b-a)
    snapshot.call_25d_iv_30d = snapshot.tenors["30"]["call_25d"]["value"]
    snapshot.put_25d_iv_30d = snapshot.tenors["30"]["put_25d"]["value"]
    if snapshot.call_25d_iv_30d is not None and snapshot.put_25d_iv_30d is not None:
        snapshot.risk_reversal_25d_30d = snapshot.call_25d_iv_30d-snapshot.put_25d_iv_30d
        snapshot.skew_25d_30d = -snapshot.risk_reversal_25d_30d
    if aligned and snapshot.atm_iv_7d is not None and snapshot.atm_iv_60d is not None:
        slope = snapshot.atm_iv_60d-snapshot.atm_iv_7d
        snapshot.volatility_regime = "back_higher" if slope > 0 else "front_higher" if slope < 0 else "flat"
    snapshot.coverage = {"received": len(items), "valid": sum(map(len, expiries.values())),
                         "rejected": dict(rejected), "selected": len(snapshot.inputs),
                         "cross_tenor_reason": None if aligned else "source_skew"}
    return snapshot
