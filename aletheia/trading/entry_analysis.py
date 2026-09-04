"""Bounded, canonical evidence contracts for AI-guided Entry planning."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia.learning.entry_context import EntryLearningContextV1
from aletheia.trading.entry_contract import entry_prompt_fingerprint


CONTEXT_VERSION = "entry-analysis-context/v1"
CONTEXT_V2_VERSION = "entry-analysis-context/v2"
ANCHOR_VERSION = "entry-anchor/v1"
HYPOTHESIS_VERSION = "entry-hypothesis/v1"
HYPOTHESIS_V2_VERSION = "entry-hypothesis/v2"
ENTRY_PROFILES = {
    ("anchor-resting-maker", "v1"),
    ("anchor-retrace-reclaim", "v1"),
}


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _candidate_opportunity_evidence_refs(opportunity: dict[str, Any]) -> tuple[str, ...]:
    refs = list(opportunity.get("primary_evidence_refs") or ())
    for mechanism in opportunity.get("secondary_mechanisms") or ():
        if isinstance(mechanism, dict):
            refs.extend(mechanism.get("evidence_refs") or ())
    return tuple(sorted({str(ref) for ref in refs if str(ref).strip()})[:64])


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
    entry_profiles: tuple[dict[str, Any], ...]
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
            bars = window.get("bars") or ()
            if window.get("availability") == "available" and len(bars) != limit:
                raise ValueError(f"candle_window_{timeframe}_incomplete")
            if window.get("availability") == "unavailable" and bars:
                raise ValueError(f"candle_window_{timeframe}_unavailable_with_bars")
            if len(bars) > limit:
                raise ValueError(f"candle_window_{timeframe}_too_large")
        if len(self.anchors) > 12:
            raise ValueError("anchor_registry_too_large")
        if len({anchor.anchor_id for anchor in self.anchors}) != len(self.anchors):
            raise ValueError("duplicate_anchor")
        profiles = {(item.get("id"), item.get("version")) for item in self.entry_profiles}
        if self.context_version == CONTEXT_VERSION:
            if not profiles.issubset(ENTRY_PROFILES):
                raise ValueError("entry_profile_not_registered")
        else:
            from aletheia.trading.entry_profiles import require_compatible_entry_profile

            mechanism = str(getattr(self, "candidate_opportunity_context", {}).get(
                "opportunity", {}
            ).get("primary_mechanism") or "")
            for profile_id, version in profiles:
                require_compatible_entry_profile(mechanism, str(profile_id), str(version))
        object.__setattr__(self, "analysis_cutoff_at", cutoff)
        expected = fingerprint(
            self.model_dump(mode="json", exclude={"context_fingerprint"})
        )
        if self.context_fingerprint and self.context_fingerprint != expected:
            raise ValueError("context_fingerprint_mismatch")
        object.__setattr__(self, "context_fingerprint", expected)
        return self


class EntryAnalysisContextV2(EntryAnalysisContextV1):
    context_version: Literal["entry-analysis-context/v2"] = CONTEXT_V2_VERSION
    candidate_opportunity_context: dict[str, Any]
    inference_identity: dict[str, str]
    learning_context: EntryLearningContextV1

    @model_validator(mode="after")
    def _inference_identity_is_exact(self) -> "EntryAnalysisContextV2":
        if (
            self.inference_identity.get("caller") != "agent_shell.entry_planner"
            or len(str(self.inference_identity.get("prompt_fingerprint") or "")) != 64
            or not str(self.inference_identity.get("model_provider") or "").strip()
        ):
            raise ValueError("entry_inference_identity_invalid")
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
        thesis = self.thesis.strip()
        scenario = self.scenario.strip()
        if not thesis or not scenario:
            raise ValueError("entry_thesis_scenario_required")
        object.__setattr__(self, "thesis", thesis)
        object.__setattr__(self, "scenario", scenario)
        if self.setup_archetype != "continuation":
            raise ValueError("setup_archetype_required")
        if not self.primary_anchor_ref or not self.invalidation_anchor_ref:
            raise ValueError("entry_anchor_required")
        if self.primary_anchor_ref == self.invalidation_anchor_ref:
            raise ValueError("entry_anchors_must_be_distinct")
        if (self.entry_profile_id, self.entry_profile_version) not in ENTRY_PROFILES:
            raise ValueError("entry_profile_required")
        if self.execution_urgency not in {"PASSIVE", "BOUNDED_URGENT"}:
            raise ValueError("entry_execution_urgency_required")
        if self.entry_profile_id == "anchor-resting-maker" and self.execution_urgency != "PASSIVE":
            raise ValueError("resting_maker_must_be_passive")
        if self.risk_tier == "NONE" or self.horizon == "NONE":
            raise ValueError("entry_risk_horizon_required")
        if not self.supporting_fact_refs:
            raise ValueError("supporting_fact_required")
        return self


class EntryHypothesisV2(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal["entry-hypothesis/v2"] = HYPOTHESIS_V2_VERSION
    decision: Literal["ENTRY_PLAN", "NO_PLAN"]
    reason_code: Literal[
        "OK", "INSUFFICIENT_EVIDENCE", "CONFLICTING_SIGNALS", "STALE_FACTS",
        "NO_VALID_ANCHOR", "NO_VALID_ENTRY_PROFILE",
    ]
    mechanism: Literal[
        "continuation", "reversal", "positioning_forced_flow", "information_catalyst"
    ]
    direction: Literal["long", "short"]
    primary_anchor_ref: str | None = None
    invalidation_anchor_ref: str | None = None
    entry_profile_id: str | None = None
    entry_profile_version: str | None = None
    execution_urgency: Literal["PASSIVE", "BOUNDED_URGENT"] | None = None
    risk_tier: Literal["LOW", "MEDIUM", "NONE"]
    horizon: Literal["INTRADAY", "NONE"]
    supporting_fact_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=8)
    opposing_fact_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=8)
    learning_evidence_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=8)

    @model_validator(mode="after")
    def _semantics(self) -> "EntryHypothesisV2":
        if self.decision == "NO_PLAN":
            if self.reason_code == "OK":
                raise ValueError("no_plan_reason_required")
            for field in (
                "primary_anchor_ref", "invalidation_anchor_ref", "entry_profile_id",
                "entry_profile_version", "execution_urgency",
            ):
                object.__setattr__(self, field, None)
            object.__setattr__(self, "risk_tier", "NONE")
            object.__setattr__(self, "horizon", "NONE")
            return self
        if self.reason_code != "OK":
            raise ValueError("entry_plan_reason_must_be_ok")
        if not self.primary_anchor_ref or not self.invalidation_anchor_ref:
            raise ValueError("entry_anchor_required")
        if self.primary_anchor_ref == self.invalidation_anchor_ref:
            raise ValueError("entry_anchors_must_be_distinct")
        if not self.entry_profile_id or not self.entry_profile_version:
            raise ValueError("entry_profile_required")
        if self.execution_urgency not in {"PASSIVE", "BOUNDED_URGENT"}:
            raise ValueError("entry_execution_urgency_required")
        if (
            self.entry_profile_id == "grounded-anchor-resting-maker"
            and self.execution_urgency != "PASSIVE"
        ):
            raise ValueError("resting_maker_must_be_passive")
        if self.risk_tier == "NONE" or self.horizon != "INTRADAY":
            raise ValueError("entry_risk_horizon_required")
        if not self.supporting_fact_refs:
            raise ValueError("supporting_fact_required")
        return self


# Unsuffixed names are the only current writer/runtime contract. Suffixed names
# remain importable solely to decode immutable historical payloads.
EntryAnchor = EntryAnchorV1
EntryAnalysisContext = EntryAnalysisContextV2
EntryHypothesis = EntryHypothesisV2


def validate_hypothesis_references(
    hypothesis: EntryHypothesisV1 | EntryHypothesisV2,
    context: EntryAnalysisContextV1 | EntryAnalysisContextV2,
) -> None:
    supplied_refs = set(hypothesis.supporting_fact_refs + hypothesis.opposing_fact_refs)
    anchors = {}
    if supplied_refs or hypothesis.decision != "NO_PLAN":
        anchors = {anchor.anchor_id: anchor for anchor in context.anchors}
        facts = set(anchors)
        for section in (
            context.market_structure,
            context.quant_context,
            context.portfolio_context,
            context.execution_context,
            context.candidate,
        ):
            refs = section.get("fact_refs", ()) if isinstance(section, dict) else ()
            facts.update(str(item) for item in refs)
        if not supplied_refs.issubset(facts):
            raise ValueError("hypothesis_fact_ref_not_supplied")
    if isinstance(hypothesis, EntryHypothesisV2):
        opportunity = context.candidate_opportunity_context["opportunity"]
        if (
            hypothesis.mechanism != opportunity["primary_mechanism"]
            or hypothesis.direction != opportunity["direction"]
        ):
            raise ValueError("hypothesis_opportunity_identity_mismatch")
        learning_refs = set(context.learning_context.evidence_refs)
        if hypothesis.learning_evidence_refs and context.learning_context.status == "unavailable":
            raise ValueError("hypothesis_learning_context_unavailable")
        if not set(hypothesis.learning_evidence_refs).issubset(learning_refs):
            raise ValueError("hypothesis_learning_ref_not_supplied")
    if hypothesis.decision == "NO_PLAN":
        return
    if hypothesis.primary_anchor_ref not in anchors or hypothesis.invalidation_anchor_ref not in anchors:
        raise ValueError("hypothesis_anchor_ref_not_supplied")
    if anchors[hypothesis.primary_anchor_ref].directional_role not in {"entry_candidate", "both"}:
        raise ValueError("hypothesis_primary_anchor_role_invalid")
    if anchors[hypothesis.invalidation_anchor_ref].directional_role not in {
        "invalidation_candidate", "both",
    }:
        raise ValueError("hypothesis_invalidation_anchor_role_invalid")
    if isinstance(hypothesis, EntryHypothesisV2):
        from aletheia.trading.entry_profiles import require_compatible_entry_profile

        profile = require_compatible_entry_profile(
            hypothesis.mechanism,
            str(hypothesis.entry_profile_id),
            str(hypothesis.entry_profile_version),
        )
        if (
            anchors[hypothesis.primary_anchor_ref].kind not in profile.primary_anchor_kinds
            or anchors[hypothesis.invalidation_anchor_ref].kind
            not in profile.invalidation_anchor_kinds
        ):
            raise ValueError("hypothesis_profile_anchor_kind_invalid")
        for section_name in profile.required_fact_sections:
            section = getattr(context, section_name)
            if section_name == "market_structure":
                if not any(
                    context.candle_windows.get(timeframe, {}).get("availability") == "available"
                    and section.get(timeframe, {}).get("availability") == "available"
                    for timeframe in profile.supported_timeframes
                ):
                    raise ValueError("hypothesis_profile_market_structure_unavailable")
            elif section.get("availability") != "available":
                raise ValueError(f"hypothesis_profile_{section_name}_unavailable")
        current_facts = set()
        for section in (
            context.market_structure, context.quant_context,
            context.portfolio_context, context.execution_context,
        ):
            current_facts.update(str(item) for item in section.get("fact_refs", ()))
        current_facts.update(
            anchor.anchor_id for anchor in context.anchors
            if anchor.kind != "candidate_reference"
        )
        if not set(hypothesis.supporting_fact_refs) & current_facts:
            raise ValueError("hypothesis_current_market_fact_required")


def _bind_experimental_entry_learning_context(
    learning_context: EntryLearningContextV1,
    treatment: object,
    learning_scope: dict[str, Any],
) -> EntryLearningContextV1:
    from aletheia.learning.entry_context import empty_entry_learning_context

    if learning_context.status != "observational":
        return empty_entry_learning_context(
            **learning_scope,
            reasons=learning_context.blocking_reasons,
        )
    return EntryLearningContextV1.model_validate({
        **learning_context.model_dump(mode="json"),
        "status": "experimental",
        "experiment_identity": {
            "observation_id": getattr(treatment, "observation_id", None),
            "generation": getattr(treatment, "generation", None),
            "manifest_hash": getattr(treatment, "manifest_hash", None),
            "receipt_id": getattr(treatment, "receipt_id", None),
            "assignment": getattr(treatment, "assignment", None),
        },
    })


def build_entry_analysis_context(
    generation: dict,
    candidate: dict,
    feature: dict,
    candle_windows: dict[str, list[dict]],
    *,
    cutoff_at: datetime,
    candidate_detected_at: str | None,
    positions: list[dict] | None = None,
    orderbook: dict | None = None,
    model_provider: str = "custom_planner",
) -> EntryAnalysisContext:
    """Freeze exact stored bars and existing causal structure into one model input."""
    from aletheia.data.ccxt_provider import timeframe_ms
    from aletheia.data.market_structure import (
        CONTRACT_VERSION,
        CORE_ALGORITHM_VERSION,
        PARAMS_HASH,
        canonical_market_structure_facts,
        derive_market_structure,
    )
    from aletheia.data.providers import Candle
    from aletheia.data.symbols import InstrumentId

    cutoff = cutoff_at.astimezone(timezone.utc)
    cutoff_ms = int(cutoff.timestamp() * 1000)
    serialized: dict[str, Any] = {}
    structures: dict[str, Any] = {}
    anchors: list[EntryAnchorV1] = []
    fact_refs: list[str] = []
    venue, symbol, market_type = (
        str(generation["venue"]), str(generation["symbol"]), str(generation["market_type"])
    )
    opportunity_context = None
    if generation.get("entry_behavior_fingerprint"):
        from aletheia.universe.candidate_opportunity import CandidateOpportunityContextV1

        opportunity_context = CandidateOpportunityContextV1.model_validate_json(
            candidate.get("candidate_opportunity_context_json") or ""
        )
        if (
            opportunity_context.candidate_id != candidate.get("id")
            or opportunity_context.context_hash != candidate.get("candidate_opportunity_context_hash")
            or opportunity_context.opportunity["primary_mechanism"]
            != generation.get("opportunity_mechanism")
            or opportunity_context.opportunity["direction"] != generation.get("direction")
            or opportunity_context.candidate_selection["policy"]
            != generation.get("candidate_selection_policy")
            or opportunity_context.candidate_selection.get("policy_epoch")
            != generation.get("candidate_selection_policy_epoch")
            or opportunity_context.opportunity["mechanism_version"]
            != generation.get("mechanism_version")
        ):
            raise ValueError("candidate_opportunity_generation_identity_mismatch")
    if (
        candidate.get("id") != generation.get("last_candidate_id")
        or candidate.get("venue") != venue
        or candidate.get("symbol") != symbol
        or candidate.get("market_type") != market_type
        or (
            opportunity_context is None
            and candidate.get("scanner_family") != generation.get("candidate_family")
        )
        or candidate.get("scanner_direction") != generation.get("direction")
    ):
        raise ValueError("candidate_generation_identity_mismatch")
    instrument_id = InstrumentId.from_parts(venue, symbol, market_type)
    for timeframe, limit in (("4h", 30), ("1h", 48), ("15m", 96)):
        interval = timeframe_ms(timeframe)
        rows = tuple(candle_windows.get(timeframe, ())[-limit:])
        valid = len(rows) == limit
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
        valid = valid and len({row.get("provider") for row in rows}) == 1
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
        swings, events = canonical_market_structure_facts(geometry, instrument_id, timeframe)
        swing_rows = [asdict(item) for item in swings]
        event_rows = [asdict(item) for item in events]
        section_refs = [item.id for item in (*swings, *events)]
        fact_refs.extend(section_refs)
        structures[timeframe] = {
            "availability": geometry.availability,
            "state": geometry.structure_state,
            "atr14": geometry.atr14,
            "swings": swing_rows,
            "events": event_rows,
            "geometry_fingerprint": geometry.geometry_fingerprint,
            "provenance": {
                "contract_version": CONTRACT_VERSION,
                "instrument": {
                    "exchange": instrument_id.exchange,
                    "symbol": instrument_id.symbol,
                    "market_type": instrument_id.market_type,
                },
                "instrument_id": str(instrument_id),
                "timeframe": timeframe,
                "requested_as_of_ms": cutoff_ms,
                "as_of_ms": bars[-1]["ts"] + interval,
                "first_bar_open_at_ms": bars[0]["ts"],
                "last_bar_open_at_ms": bars[-1]["ts"],
                "first_bar_close_at_ms": bars[0]["ts"] + interval,
                "last_bar_close_at_ms": bars[-1]["ts"] + interval,
                "admitted_bar_count": len(bars),
                "requested_limit": limit,
                "fetched_bar_count": len(rows),
                "provider_outcome": "stored_complete_window",
                "provider_failure_reason": None,
                "source_provider": next(iter({row["provider"] for row in bars})),
                "source_exchange": venue,
                "history_quality": "complete",
                "geometry_fingerprint": geometry.geometry_fingerprint,
                "source_candle_fingerprint": source_hash,
                "core_algorithm_version": CORE_ALGORITHM_VERSION,
                "params_hash": PARAMS_HASH,
                "fvg_algorithm_version": None,
                "computed_at_ms": cutoff_ms,
            },
            "fact_refs": section_refs,
        }
        if timeframe == "15m" and geometry.availability == "available":
            direction = str(generation["direction"])
            for item in swings:
                anchors.append(EntryAnchorV1(
                    venue=venue, symbol=symbol, market_type=market_type,
                    kind="confirmed_swing", price=item.price,
                    directional_role=(
                        "invalidation_candidate"
                        if (direction == "long" and item.kind == "low")
                        or (direction == "short" and item.kind == "high")
                        else "entry_candidate"
                    ),
                    known_at_ms=item.known_at_ms,
                    source_fact_refs=(item.id,),
                    source_fingerprint=fingerprint(asdict(item)),
                    geometry_fingerprint=geometry.geometry_fingerprint,
                    aliases=("price_action", "smc"),
                ))
            for item in events:
                anchors.append(EntryAnchorV1(
                    venue=venue, symbol=symbol, market_type=market_type,
                    kind="broken_structure_level", price=item.broken_level,
                    directional_role="both", known_at_ms=item.known_at_ms,
                    source_fact_refs=(item.id, item.broken_swing_id),
                    source_fingerprint=fingerprint(asdict(item)),
                    geometry_fingerprint=geometry.geometry_fingerprint,
                    aliases=("price_action", "smc"),
                ))
    if opportunity_context is not None:
        candidate_detected_at = str(opportunity_context.source["detected_at"])
    if candidate_detected_at is None:
        raise ValueError("candidate_detected_at_unavailable")
    detected = datetime.fromisoformat(str(candidate_detected_at).replace("Z", "+00:00"))
    if detected.tzinfo is None:
        raise ValueError("candidate_detected_at_timezone_required")
    detected = detected.astimezone(timezone.utc)
    detected_ms = int(detected.timestamp() * 1000)
    if detected_ms > cutoff_ms:
        raise ValueError("candidate_detected_at_after_cutoff")
    candidate_ref = f"candidate:{candidate['id']}"
    anchors.append(EntryAnchorV1(
        venue=venue, symbol=symbol, market_type=market_type,
        kind="candidate_reference", price=float(candidate["entry_price_usd"]),
        directional_role="entry_candidate", known_at_ms=detected_ms,
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
                directional_role="entry_candidate",
                known_at_ms=max(int(row["ts"]) + 15 * 60 * 1000 for row in serialized["15m"]["bars"][-21:]),
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
    quant_fields = (
        "atr_pct", "rsi_15m", "atr_15m_pct", "return_15m", "return_1h",
        "return_4h", "return_24h", "funding_rate", "open_interest", "oi_delta_1h",
        "long_short_ratio", "top_trader_position_ratio", "taker_buy_ratio", "vol_ratio",
        "relative_strength_24h", "oi_price_divergence", "rsi_4h", "atr_4h_pct",
        "bb_width_4h", "vol_ratio_4h", "bb_squeeze", "data_freshness_sec",
    )
    try:
        feature_at = datetime.fromisoformat(str(feature.get("ts") or "").replace("Z", "+00:00"))
    except ValueError:
        feature_at = None
    feature_valid = bool(
        feature.get("id")
        and feature.get("asset_id") == candidate.get("asset_id")
        and feature_at is not None
        and feature_at.tzinfo is not None
        and feature_at.astimezone(timezone.utc) <= cutoff
    )
    quant_values = (
        {key: feature.get(key) for key in quant_fields if feature.get(key) is not None}
        if feature_valid else {}
    )
    feature_ref = f"feature:{feature.get('id')}" if feature_valid else None
    admitted_positions = []
    for position in positions or ():
        try:
            opened = datetime.fromisoformat(str(position.get("opened_at") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if opened.tzinfo is None or opened.astimezone(timezone.utc) > cutoff:
            continue
        mark = position.get("current_price") or position.get("entry_price")
        try:
            notional = abs(float(position["quantity"]) * float(mark))
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(notional):
            admitted_positions.append((position, notional))
    portfolio_refs = [f"position:{item[0]['id']}" for item in admitted_positions]
    portfolio = {
        "availability": "available",
        "as_of": cutoff.isoformat(),
        "open_position_count": len(admitted_positions),
        "total_notional_usd": sum(item[1] for item in admitted_positions),
        "same_instrument_notional_usd": sum(
            item[1] for item in admitted_positions
            if item[0].get("symbol") == symbol and item[0].get("market_type") == market_type
        ),
        "long_count": sum(item[0].get("direction") == "long" for item in admitted_positions),
        "short_count": sum(item[0].get("direction") == "short" for item in admitted_positions),
        "fact_refs": portfolio_refs,
    }
    if opportunity_context is None:
        profiles = (
            {"id": "anchor-resting-maker", "version": "v1"},
            {"id": "anchor-retrace-reclaim", "version": "v1"},
        )
    else:
        from aletheia.trading.entry_profiles import compatible_entry_profiles

        profiles = tuple(
            profile.as_dict() for profile in compatible_entry_profiles(
                str(opportunity_context.opportunity["primary_mechanism"])
            )
        )
    if opportunity_context is None:
        learning_context: object = {
            "availability": "unavailable", "reason": "exact_ai_entry_evidence_unavailable"
        }
    else:
        from aletheia.learning.entry_context import (
            build_entry_learning_context,
            empty_entry_learning_context,
            resolve_entry_learning_treatment,
        )

        learning_scope = {
            "as_of_at": cutoff.isoformat(),
            "mechanism": str(opportunity_context.opportunity["primary_mechanism"]),
            "direction": str(opportunity_context.opportunity["direction"]),
            "market_type": market_type,
            "policy": str(opportunity_context.candidate_selection["policy"]),
            "policy_epoch": opportunity_context.candidate_selection.get("policy_epoch"),
        }
        treatment = resolve_entry_learning_treatment(
            planning_generation_id=str(generation["id"]), **learning_scope,
        )
        if treatment.payload.get("include_learning_context") is True:
            from aletheia.config.loader import load_config

            provider_config = load_config().models.providers.get(str(model_provider))
            current_behavior_identity = {
                "producer_commit": str(generation.get("producer_commit") or ""),
                "behavior_fingerprint": str(generation.get("behavior_fingerprint") or ""),
                "entry_behavior_fingerprint": str(generation.get("entry_behavior_fingerprint") or ""),
                "schema_fingerprint": str(generation.get("schema_fingerprint") or ""),
                "mechanism_version": str(generation.get("mechanism_version") or ""),
                "model_provider": str(model_provider),
                "model": str(getattr(provider_config, "model", "") or ""),
                "prompt_fingerprint": entry_prompt_fingerprint(),
                "entry_profile_registry_hash": fingerprint(profiles),
            }
            learning_context = build_entry_learning_context(
                **learning_scope,
                exclude_generation_id=str(generation["id"]),
                current_behavior_identity=current_behavior_identity,
            )
            learning_context = _bind_experimental_entry_learning_context(
                learning_context, treatment, learning_scope,
            )
        else:
            learning_context = empty_entry_learning_context(
                **learning_scope,
                reasons=(f"entry_learning_treatment:{treatment.reason}",),
            )
    candidate_fact_refs = [candidate_ref]
    if opportunity_context is not None:
        candidate_fact_refs.extend(
            _candidate_opportunity_evidence_refs(opportunity_context.opportunity)
        )
    context_fields = dict(
        analysis_cutoff_at=cutoff.isoformat(),
        instrument={"venue": venue, "symbol": symbol, "market_type": market_type, "direction": generation["direction"]},
        planning_generation={
            "id": generation["id"],
            "candidate_id": candidate["id"],
            "candidate_family": generation["candidate_family"],
            "cycle_number": generation.get("cycle_number", 1),
            "entry_behavior_fingerprint": generation.get("entry_behavior_fingerprint"),
            "schema_fingerprint": generation.get("schema_fingerprint"),
        },
        candidate={
            "candidate_price": candidate["entry_price_usd"],
            "detected_at": detected.isoformat(),
            "motive_evidence": (
                None if opportunity_context is not None else candidate.get("inclusion_reason")
            ),
            "risk_flags": candidate.get("risk_flags"),
            "fact_refs": candidate_fact_refs,
        },
        candle_windows=serialized, market_structure={**structures, "fact_refs": fact_refs},
        quant_context={
            "availability": "available" if feature_ref and quant_values else "unavailable",
            "feature_snapshot_id": feature.get("id") if feature_valid else None,
            "feature_snapshot_at": feature.get("ts") if feature_valid else None,
            "technical_algorithm_version": feature.get("technical_algorithm_version") if feature_valid else None,
            "technical_snapshot_manifest_hash": (
                fingerprint(feature.get("technical_snapshot_manifest") or "{}")
                if feature_valid else None
            ),
            "values": quant_values,
            "unavailable_fields": sorted(set(quant_fields) - set(quant_values)),
            "fact_refs": [feature_ref] if feature_ref else [],
        },
        portfolio_context=portfolio,
        execution_context={
            "availability": "available" if orderbook_available else "unavailable",
            "bid": orderbook.get("bid_price") if orderbook else None,
            "ask": orderbook.get("ask_price") if orderbook else None,
            "spread_bps": orderbook.get("spread_bps") if orderbook else None,
            "depth_10bps_usd": orderbook.get("total_depth_10bps_usd") if orderbook else None,
            "observed_at": orderbook.get("observed_at") if orderbook else None,
            "estimated_costs": estimated_cost,
            "fact_refs": [f"orderbook:{orderbook.get('id')}"] if orderbook and orderbook.get("id") else [],
        },
        anchors=selected,
        entry_profiles=profiles,
        learning_context=learning_context,
        unavailable_sections=tuple(key for key, value in serialized.items() if value["availability"] == "unavailable"),
    )
    if opportunity_context is None:
        raise ValueError("candidate_opportunity_context_missing")
    context_fields["candidate_opportunity_context"] = opportunity_context.model_dump(mode="json")
    context_fields["inference_identity"] = {
        "caller": "agent_shell.entry_planner",
        "prompt_fingerprint": entry_prompt_fingerprint(),
        "model_provider": str(model_provider),
    }
    return EntryAnalysisContext(**context_fields)
