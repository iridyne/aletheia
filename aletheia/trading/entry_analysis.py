"""Bounded, canonical evidence contracts for AI-guided Entry planning."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


CONTEXT_VERSION = "entry-analysis-context/v1"
ANCHOR_VERSION = "entry-anchor/v1"
HYPOTHESIS_VERSION = "entry-hypothesis/v1"
ENTRY_PROFILES = {
    ("anchor-resting-maker", "v1"),
    ("anchor-retrace-reclaim", "v1"),
}


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _aware_utc(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone_required")
    return parsed.astimezone(timezone.utc).isoformat()


class EntryAnchorV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    anchor_version: Literal["entry-anchor/v1"] = ANCHOR_VERSION
    anchor_id: str = ""
    venue: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    market_type: Literal["spot", "perp"]
    kind: Literal[
        "candidate_reference", "causal_breakout", "confirmed_swing", "broken_structure_level"
    ]
    timeframe: Literal["15m"] = "15m"
    price: float
    directional_role: Literal["entry_candidate", "invalidation_candidate", "both"]
    known_at_ms: int = Field(ge=0)
    source_fact_refs: tuple[str, ...] = ()
    source_fingerprint: str = Field(min_length=1)
    geometry_fingerprint: str | None = None
    aliases: tuple[str, ...] = ()

    @field_validator("price")
    @classmethod
    def _positive_price(cls, value: float) -> float:
        if not math.isfinite(value) or value <= 0:
            raise ValueError("anchor_price_invalid")
        return value

    @model_validator(mode="after")
    def _canonical_identity(self) -> "EntryAnchorV1":
        material = self.model_dump(mode="json", exclude={"anchor_id", "aliases"})
        expected = fingerprint(material)
        if self.anchor_id and self.anchor_id != expected:
            raise ValueError("anchor_id_mismatch")
        object.__setattr__(self, "anchor_id", expected)
        object.__setattr__(self, "aliases", tuple(sorted(set(self.aliases))))
        return self


class EntryAnalysisContextV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    context_version: Literal["entry-analysis-context/v1"] = CONTEXT_VERSION
    context_fingerprint: str = ""
    analysis_cutoff_at: str
    instrument: dict[str, Any]
    planning_generation: dict[str, Any]
    candidate: dict[str, Any]
    candle_windows: dict[str, Any]
    market_structure: dict[str, Any]
    quant_context: dict[str, Any]
    portfolio_context: dict[str, Any]
    execution_context: dict[str, Any]
    anchors: tuple[EntryAnchorV1, ...]
    entry_profiles: tuple[dict[str, str], ...]
    learning_context: dict[str, Any]
    unavailable_sections: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _bounded_and_canonical(self) -> "EntryAnalysisContextV1":
        cutoff = _aware_utc(self.analysis_cutoff_at)
        limits = {"4h": 30, "1h": 48, "15m": 96}
        for timeframe, limit in limits.items():
            window = self.candle_windows.get(timeframe)
            if not isinstance(window, dict):
                raise ValueError(f"candle_window_{timeframe}_missing")
            if len(window.get("bars") or ()) > limit:
                raise ValueError(f"candle_window_{timeframe}_too_large")
        if len(self.anchors) > 12:
            raise ValueError("anchor_registry_too_large")
        if len({anchor.anchor_id for anchor in self.anchors}) != len(self.anchors):
            raise ValueError("duplicate_anchor")
        profiles = {(item.get("id"), item.get("version")) for item in self.entry_profiles}
        if not profiles.issubset(ENTRY_PROFILES):
            raise ValueError("entry_profile_not_registered")
        expected = fingerprint(
            self.model_dump(mode="json", exclude={"context_fingerprint"})
        )
        if self.context_fingerprint and self.context_fingerprint != expected:
            raise ValueError("context_fingerprint_mismatch")
        object.__setattr__(self, "analysis_cutoff_at", cutoff)
        object.__setattr__(self, "context_fingerprint", expected)
        return self


class EntryHypothesisV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal["entry-hypothesis/v1"] = HYPOTHESIS_VERSION
    decision: Literal["ENTRY_PLAN", "NO_PLAN"]
    reason_code: Literal[
        "OK",
        "INSUFFICIENT_EVIDENCE",
        "CONFLICTING_SIGNALS",
        "STALE_FACTS",
        "NO_VALID_ANCHOR",
        "NO_VALID_ENTRY_PROFILE",
    ]
    thesis: str = Field(max_length=400)
    scenario: str = Field(max_length=200)
    setup_archetype: Literal["continuation"] | None = None
    primary_anchor_ref: str | None = None
    invalidation_anchor_ref: str | None = None
    entry_profile_id: Literal["anchor-resting-maker", "anchor-retrace-reclaim"] | None = None
    entry_profile_version: Literal["v1"] | None = None
    execution_urgency: Literal["PASSIVE", "BOUNDED_URGENT"] | None = None
    risk_tier: Literal["LOW", "MEDIUM", "NONE"]
    horizon: Literal["INTRADAY", "SWING", "NONE"]
    supporting_fact_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=8)
    opposing_fact_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=8)
    key_assumptions: tuple[str, ...] = Field(default_factory=tuple, max_length=3)

    @model_validator(mode="after")
    def _semantics(self) -> "EntryHypothesisV1":
        execution = (
            "setup_archetype", "primary_anchor_ref", "invalidation_anchor_ref",
            "entry_profile_id", "entry_profile_version", "execution_urgency",
        )
        if self.decision == "NO_PLAN":
            if self.reason_code == "OK":
                raise ValueError("no_plan_reason_required")
            for field in execution:
                object.__setattr__(self, field, None)
            object.__setattr__(self, "risk_tier", "NONE")
            object.__setattr__(self, "horizon", "NONE")
            return self
        if self.reason_code != "OK":
            raise ValueError("entry_plan_reason_must_be_ok")
        if self.setup_archetype != "continuation":
            raise ValueError("setup_archetype_required")
        if not self.primary_anchor_ref or not self.invalidation_anchor_ref:
            raise ValueError("entry_anchor_required")
        if self.primary_anchor_ref == self.invalidation_anchor_ref:
            raise ValueError("entry_anchors_must_be_distinct")
        if (self.entry_profile_id, self.entry_profile_version) not in ENTRY_PROFILES:
            raise ValueError("entry_profile_required")
        if self.entry_profile_id == "anchor-resting-maker" and self.execution_urgency != "PASSIVE":
            raise ValueError("resting_maker_must_be_passive")
        if self.risk_tier == "NONE" or self.horizon == "NONE":
            raise ValueError("entry_risk_horizon_required")
        if not self.supporting_fact_refs:
            raise ValueError("supporting_fact_required")
        return self


def validate_hypothesis_references(
    hypothesis: EntryHypothesisV1, context: EntryAnalysisContextV1
) -> None:
    if hypothesis.decision == "NO_PLAN":
        return
    anchors = {anchor.anchor_id for anchor in context.anchors}
    if hypothesis.primary_anchor_ref not in anchors or hypothesis.invalidation_anchor_ref not in anchors:
        raise ValueError("hypothesis_anchor_ref_not_supplied")
    facts = set(anchors)
    for section in (context.market_structure, context.quant_context, context.candidate):
        refs = section.get("fact_refs", ()) if isinstance(section, dict) else ()
        facts.update(str(item) for item in refs)
    if not set(hypothesis.supporting_fact_refs + hypothesis.opposing_fact_refs).issubset(facts):
        raise ValueError("hypothesis_fact_ref_not_supplied")


def build_entry_analysis_context(
    generation: dict,
    candidate: dict,
    feature: dict,
    candle_windows: dict[str, list[dict]],
    *,
    cutoff_at: datetime,
    orderbook: dict | None = None,
) -> EntryAnalysisContextV1:
    """Freeze exact stored bars and existing causal structure into one model input."""
    from aletheia.data.ccxt_provider import timeframe_ms
    from aletheia.data.market_structure import derive_market_structure
    from aletheia.data.providers import Candle

    cutoff = cutoff_at.astimezone(timezone.utc)
    cutoff_ms = int(cutoff.timestamp() * 1000)
    serialized: dict[str, Any] = {}
    structures: dict[str, Any] = {}
    anchors: list[EntryAnchorV1] = []
    fact_refs: list[str] = []
    venue, symbol, market_type = (
        str(generation["venue"]), str(generation["symbol"]), str(generation["market_type"])
    )
    for timeframe, limit in (("4h", 30), ("1h", 48), ("15m", 96)):
        interval = timeframe_ms(timeframe)
        rows = tuple(candle_windows.get(timeframe, ())[-limit:])
        valid = bool(rows)
        previous = None
        bars: list[dict[str, Any]] = []
        for row in rows:
            ts = row.get("ts")
            valid = valid and isinstance(ts, int) and ts + interval <= cutoff_ms
            valid = valid and (previous is None or ts - previous == interval)
            valid = valid and row.get("exchange") == venue and row.get("market_type") == market_type
            valid = valid and bool(row.get("provider"))
            valid = valid and row.get("first_persisted_at") is not None and row.get("last_mutated_at") is not None
            if valid:
                for stamp in (row["first_persisted_at"], row["last_mutated_at"]):
                    known = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
                    valid = valid and known.tzinfo is not None and known.timestamp() * 1000 <= cutoff_ms
            previous = ts if isinstance(ts, int) else previous
            bars.append({key: row.get(key) for key in ("ts", "open", "high", "low", "close", "volume", "provider", "exchange", "market_type", "first_persisted_at", "last_mutated_at")})
        if not valid:
            serialized[timeframe] = {"availability": "unavailable", "bars": [], "source_fingerprint": None}
            structures[timeframe] = {"availability": "unavailable", "fact_refs": []}
            continue
        source_hash = fingerprint(bars)
        serialized[timeframe] = {"availability": "available", "bars": bars, "source_fingerprint": source_hash}
        geometry = derive_market_structure(
            [Candle(int(row["ts"]), float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]), float(row["volume"]), True, str(row["provider"]), venue) for row in bars],
            tf_ms=interval,
        )
        swing_rows = [asdict(item) for item in geometry.swings]
        event_rows = [
            asdict(item) for item in geometry.events
        ]
        section_refs = [f"structure:{timeframe}:{fingerprint(item)}" for item in swing_rows + event_rows]
        fact_refs.extend(section_refs)
        structures[timeframe] = {
            "availability": geometry.availability,
            "state": geometry.structure_state,
            "atr14": geometry.atr14,
            "swings": swing_rows,
            "events": event_rows,
            "geometry_fingerprint": geometry.geometry_fingerprint,
            "fact_refs": section_refs,
        }
        if timeframe == "15m" and geometry.availability == "available":
            for item in geometry.swings:
                anchors.append(EntryAnchorV1(
                    venue=venue, symbol=symbol, market_type=market_type,
                    kind="confirmed_swing", price=item.price,
                    directional_role="both", known_at_ms=item.known_at_ms,
                    source_fact_refs=(f"swing:{item.kind}:{item.origin_at_ms}",),
                    source_fingerprint=fingerprint(asdict(item)),
                    geometry_fingerprint=geometry.geometry_fingerprint,
                    aliases=("price_action", "smc"),
                ))
            for item in geometry.events:
                anchors.append(EntryAnchorV1(
                    venue=venue, symbol=symbol, market_type=market_type,
                    kind="broken_structure_level", price=item.broken_swing.price,
                    directional_role="both", known_at_ms=item.known_at_ms,
                    source_fact_refs=(f"{item.type}:{item.break_at_ms}",),
                    source_fingerprint=fingerprint(asdict(item)),
                    geometry_fingerprint=geometry.geometry_fingerprint,
                    aliases=("price_action", "smc"),
                ))
    candidate_ref = f"candidate:{candidate['id']}"
    anchors.append(EntryAnchorV1(
        venue=venue, symbol=symbol, market_type=market_type,
        kind="candidate_reference", price=float(candidate["entry_price_usd"]),
        directional_role="entry_candidate", known_at_ms=cutoff_ms,
        source_fact_refs=(candidate_ref,), source_fingerprint=fingerprint(candidate_ref),
    ))
    if serialized.get("15m", {}).get("availability") == "available":
        from datetime import timedelta
        from aletheia.trading.momentum_entry_geometry import (
            MomentumGeometryUnavailable,
            compile_momentum_entry_geometry_v2,
        )

        try:
            geometry = compile_momentum_entry_geometry_v2(
                candidate=candidate, feature=feature,
                candles=serialized["15m"]["bars"][-21:],
                direction=generation["direction"], minimum_risk_reward=1.5,
                source_cutoff_at=cutoff, valid_until=cutoff + timedelta(hours=4),
            )
        except MomentumGeometryUnavailable:
            geometry = None
        if geometry is not None:
            anchors.append(EntryAnchorV1(
                venue=venue, symbol=symbol, market_type=market_type,
                kind="causal_breakout", price=geometry.breakout_level,
                directional_role="entry_candidate", known_at_ms=cutoff_ms,
                source_fact_refs=tuple(geometry.source_candle_ids),
                source_fingerprint=geometry.source_hash,
                geometry_fingerprint=geometry.geometry_fingerprint,
                aliases=("price_action", "smc"),
            ))
    unique = {item.anchor_id: item for item in anchors if item.known_at_ms <= cutoff_ms}
    selected = tuple(sorted(unique.values(), key=lambda item: (item.known_at_ms, item.anchor_id), reverse=True)[:12])
    orderbook_available = False
    if orderbook and all(orderbook.get(key) is not None for key in ("bid_price", "ask_price", "spread_bps", "total_depth_10bps_usd", "observed_at")):
        observed = datetime.fromisoformat(str(orderbook["observed_at"]).replace("Z", "+00:00"))
        age_ms = (cutoff - observed.astimezone(timezone.utc)).total_seconds() * 1000 if observed.tzinfo else float("inf")
        orderbook_available = 0 <= age_ms <= 2_000
    estimated_cost = candidate.get("estimated_cost")
    return EntryAnalysisContextV1(
        analysis_cutoff_at=cutoff.isoformat(),
        instrument={"venue": venue, "symbol": symbol, "market_type": market_type, "direction": generation["direction"]},
        planning_generation={"id": generation["id"], "planner_contract_version": generation["planner_contract_version"], "candidate_id": candidate["id"], "candidate_family": generation["candidate_family"]},
        candidate={"candidate_price": candidate["entry_price_usd"], "detected_at": cutoff.isoformat(), "motive_evidence": candidate.get("inclusion_reason"), "risk_flags": candidate.get("risk_flags"), "fact_refs": [candidate_ref]},
        candle_windows=serialized, market_structure={**structures, "fact_refs": fact_refs},
        quant_context={"feature_snapshot": feature, "fact_refs": [f"feature:{feature.get('id')}"] if feature.get("id") else []},
        portfolio_context={"availability": "unavailable"},
        execution_context={
            "availability": "available" if orderbook_available else "unavailable",
            "bid": orderbook.get("bid_price") if orderbook else None,
            "ask": orderbook.get("ask_price") if orderbook else None,
            "spread_bps": orderbook.get("spread_bps") if orderbook else None,
            "depth_10bps_usd": orderbook.get("total_depth_10bps_usd") if orderbook else None,
            "observed_at": orderbook.get("observed_at") if orderbook else None,
            "estimated_costs": estimated_cost,
        },
        anchors=selected,
        entry_profiles=(
            {"id": "anchor-resting-maker", "version": "v1"},
            {"id": "anchor-retrace-reclaim", "version": "v1"},
        ),
        learning_context={"availability": "unavailable"},
        unavailable_sections=tuple(key for key, value in serialized.items() if value["availability"] == "unavailable"),
    )
