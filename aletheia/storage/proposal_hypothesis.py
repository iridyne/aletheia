"""Versioned, immutable opening hypothesis embedded in Proposal attribution."""

from __future__ import annotations

import json
import math
from typing import Any, Mapping

from aletheia.storage.models import TradeProposal


HYPOTHESIS_CONTRACT_VERSION = "position_hypothesis/v1"
_ALLOWED_INVALIDATIONS = frozenset(
    {
        "price_at_or_below",
        "price_at_or_above",
        "signal_at_or_below",
        "signal_at_or_above",
        "regime_not_in",
    }
)


def _mapping(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _finite(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _list(value: object) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _validated_conditions(value: object) -> list[dict[str, Any]]:
    conditions = _list(value)
    validated: list[dict[str, Any]] = []
    for condition in conditions:
        if not isinstance(condition, Mapping):
            raise ValueError("hypothesis invalidation condition must be an object")
        item = dict(condition)
        kind = str(item.get("kind") or "")
        if kind not in _ALLOWED_INVALIDATIONS:
            raise ValueError(f"unsupported hypothesis invalidation kind: {kind or 'missing'}")
        if kind == "regime_not_in":
            regimes = item.get("regimes")
            if (
                not isinstance(regimes, list)
                or not regimes
                or not all(isinstance(regime, str) and regime for regime in regimes)
            ):
                raise ValueError("regime_not_in requires non-empty regimes")
            validated.append({"kind": kind, "regimes": regimes})
            continue
        threshold = _finite(item.get("threshold"))
        if threshold is None:
            raise ValueError(f"{kind} requires finite threshold")
        if kind.startswith("signal_"):
            signal = str(item.get("signal") or "")
            if not signal:
                raise ValueError(f"{kind} requires signal")
            validated.append({"kind": kind, "signal": signal, "threshold": threshold})
        else:
            validated.append({"kind": kind, "threshold": threshold})
    return validated


def _opportunity(db: Any, proposal: TradeProposal) -> dict[str, Any] | None:
    if not proposal.opportunity_id:
        return None
    for table in ("opportunities", "serenity_opportunities"):
        row = db.execute(f"SELECT * FROM {table} WHERE id=?", (proposal.opportunity_id,)).fetchone()
        if row is not None:
            return dict(row)
    return None


def _market_identity(db: Any, opportunity: dict[str, Any] | None, proposal: TradeProposal) -> dict[str, Any]:
    venue = None
    member_id = (opportunity or {}).get("universe_member_id")
    if member_id:
        row = db.execute(
            "SELECT a.venue FROM universe_members m JOIN universe_registry a ON a.id=m.asset_id WHERE m.id=?",
            (member_id,),
        ).fetchone()
        venue = row["venue"] if row is not None else None
    return {
        "venue": venue,
        "symbol": proposal.symbol,
        "market_type": proposal.market_type,
    }


def freeze_position_hypothesis(proposal: TradeProposal, *, db: Any) -> None:
    """Freeze evidence before hashing/persistence; never rewrite an existing contract."""
    if (
        proposal.source != "native"
        or not proposal.opportunity_id
        or proposal.quantity is None
        or proposal.quantity <= 0
        or proposal.direction not in {"long", "short"}
    ):
        return
    snapshot = _mapping(proposal.attribution_snapshot)
    existing = snapshot.get("position_hypothesis")
    if existing is not None:
        if not isinstance(existing, Mapping) or existing.get("contract_version") != HYPOTHESIS_CONTRACT_VERSION:
            raise ValueError("position hypothesis contract is invalid or unsupported")
        _validated_conditions(existing.get("invalidation_conditions"))
        return

    opportunity = _opportunity(db, proposal)
    supporting_signals = _list(snapshot.get("supporting_signals"))
    invalidations = _validated_conditions(snapshot.get("invalidation_conditions"))
    entry = _finite(proposal.price)
    stop = _finite(proposal.stop_price)
    quantity = _finite(proposal.quantity)
    initial_risk = (
        abs(entry - stop) * quantity if entry is not None and stop is not None and quantity is not None else None
    )
    snapshot["position_hypothesis"] = {
        "contract_version": HYPOTHESIS_CONTRACT_VERSION,
        "frozen_at": proposal.created_at,
        "thesis": (opportunity or {}).get("thesis") or "not_recorded",
        "expected_direction": proposal.direction,
        "expected_horizon_hours": proposal.expected_horizon_hours,
        "supporting_signals": supporting_signals,
        "invalidation_conditions": invalidations,
        "invalidation_evaluable": bool(invalidations),
        "initial_risk": {
            "quote_amount": initial_risk,
            "quantity": quantity,
            "entry_price": entry,
            "stop_price": stop,
            "risk_reward": _finite(proposal.risk_reward),
        },
        "target_price": _finite(proposal.take_profit),
        "market_identity": _market_identity(db, opportunity, proposal),
        "decision_identity": {
            "proposal_id": proposal.id,
            "opportunity_id": proposal.opportunity_id,
            "runtime_session_id": proposal.runtime_session_id,
            "producer_commit": proposal.producer_commit,
            "behavior_fingerprint": proposal.behavior_fingerprint,
        },
    }
    proposal.attribution_snapshot = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def evaluate_hypothesis_prefix(
    hypothesis: Mapping[str, Any] | None,
    *,
    completed_bars: list[Mapping[str, Any]],
    signal_prefix: Mapping[str, float | int | None] | None = None,
    regime: str | None = None,
) -> str:
    """Evaluate only caller-supplied completed-prefix facts; no future access exists."""
    if not hypothesis or hypothesis.get("contract_version") != HYPOTHESIS_CONTRACT_VERSION:
        return "not_recorded"
    conditions = _validated_conditions(hypothesis.get("invalidation_conditions"))
    if not conditions:
        return "not_evaluable"
    latest = completed_bars[-1] if completed_bars else None
    signals = signal_prefix or {}
    weakening = False
    for condition in conditions:
        kind = condition["kind"]
        if kind == "regime_not_in":
            if regime is None:
                return "not_evaluable"
            if regime not in condition["regimes"]:
                return "invalidated"
            continue
        if kind.startswith("price_"):
            if latest is None:
                return "not_evaluable"
            price = _finite(latest.get("close"))
            if price is None:
                return "not_evaluable"
            threshold = float(condition["threshold"])
            if kind == "price_at_or_below" and price <= threshold:
                return "invalidated"
            if kind == "price_at_or_above" and price >= threshold:
                return "invalidated"
            distance = abs(price - threshold) / max(abs(threshold), 1e-12)
            weakening = weakening or distance <= 0.0025
            continue
        signal = condition["signal"]
        value = _finite(signals.get(signal))
        if value is None:
            return "not_evaluable"
        threshold = float(condition["threshold"])
        if kind == "signal_at_or_below" and value <= threshold:
            return "invalidated"
        if kind == "signal_at_or_above" and value >= threshold:
            return "invalidated"
        distance = abs(value - threshold) / max(abs(threshold), 1e-12)
        weakening = weakening or distance <= 0.05
    return "weakening" if weakening else "confirming"
