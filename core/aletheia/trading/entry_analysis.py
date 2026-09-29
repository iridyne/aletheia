"""Bounded, canonical evidence contracts for AI-guided Entry planning."""

from __future__ import annotations

import logging

import hashlib
import json
import math
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia.trading.entry_learning import (
    EntryLearningContext, empty_entry_learning_context, resolve_entry_learning_context,
)
from aletheia.trading.entry_contract import entry_prompt_fingerprint
from aletheia.trading.risk_intent import RiskIntent


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


class EntryAnchor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    anchor_id: str = ""
    venue: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    market_type: Literal["spot", "perp"]
    kind: Literal["candidate_reference", "causal_breakout", "confirmed_swing", "broken_structure_level"]
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
    def _canonical_identity(self) -> "EntryAnchor":
        material = self.model_dump(mode="json", exclude={"anchor_id", "aliases"})
        expected = fingerprint(material)
        if self.anchor_id and self.anchor_id != expected:
            raise ValueError("anchor_id_mismatch")
        object.__setattr__(self, "anchor_id", expected)
        object.__setattr__(self, "aliases", tuple(sorted(set(self.aliases))))
        return self


class EntryAnalysisContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

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
    anchors: tuple[EntryAnchor, ...]
    entry_profiles: tuple[dict[str, Any], ...]
    learning_context: EntryLearningContext
    unavailable_sections: tuple[str, ...] = ()
    candidate_opportunity_context: dict[str, Any]
    inference_identity: dict[str, str]

    @model_validator(mode="after")
    def _bounded_and_canonical(self) -> "EntryAnalysisContext":
        cutoff = _aware_utc(self.analysis_cutoff_at)
        for timeframe, limit in {"4h": 30, "1h": 48, "15m": 96}.items():
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
        opportunity = self.candidate_opportunity_context
        mechanism = str((opportunity.get("opportunity") or {}).get("primary_mechanism") or "")
        from aletheia.trading.entry_profiles import require_current_entry_profile

        for profile in self.entry_profiles:
            require_current_entry_profile(mechanism, str(profile.get("id") or ""))
        if (
            self.inference_identity.get("caller") != "agent_shell.entry_planner"
            or len(str(self.inference_identity.get("prompt_fingerprint") or "")) != 64
            or not str(self.inference_identity.get("model_provider") or "").strip()
        ):
            raise ValueError("entry_inference_identity_invalid")
        object.__setattr__(self, "analysis_cutoff_at", cutoff)
        expected = fingerprint(self.model_dump(mode="json", exclude={"context_fingerprint"}))
        if self.context_fingerprint and self.context_fingerprint != expected:
            raise ValueError("context_fingerprint_mismatch")
        object.__setattr__(self, "context_fingerprint", expected)
        return self


logger = logging.getLogger("aletheia.trading.entry_analysis")


class EntryHypothesis(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: Literal["ENTRY_PLAN", "NO_PLAN"]
    reason_code: Literal[
        "OK",
        "INSUFFICIENT_EVIDENCE",
        "CONFLICTING_SIGNALS",
        "STALE_FACTS",
        "NO_VALID_ANCHOR",
        "NO_VALID_ENTRY_PROFILE",
    ]
    mechanism: Literal["continuation", "reversal", "positioning_forced_flow", "information_catalyst"]
    direction: Literal["long", "short"]
    primary_anchor_ref: str | None = None
    invalidation_anchor_ref: str | None = None
    entry_profile_id: str | None = None
    capture_priority: Literal["PRICE_FIRST", "MAKER_PREFERRED", "FILL_FIRST"] | None = None
    capture_reason: str | None = Field(default=None, min_length=1, max_length=512)
    capture_evidence_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=8)
    risk_tier: Literal["LOW", "MEDIUM", "NONE"]
    horizon: Literal["INTRADAY", "SWING", "NONE"]
    supporting_fact_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=8)
    opposing_fact_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=8)
    learning_evidence_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=8)
    risk_intent: RiskIntent | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_risk_intent(cls, value):
        """Accept the shapes a model actually emits for the risk intent (#3729).

        A bare posture string, or a mapping that leaves the posture out, still
        expresses exactly what the AI is allowed to express. Normalizing here keeps
        the *bound* intact — `posture` is still a closed Literal and no numeric
        field exists — while the missing reason/evidence is completed from the
        hypothesis itself in `_semantics`.
        """
        if not isinstance(value, dict) or "risk_intent" not in value:
            return value
        intent = value.get("risk_intent")
        if intent is None:
            return value  # `_semantics` derives the whole intent
        if isinstance(intent, str):
            intent = {"posture": intent}
        if not isinstance(intent, dict):
            return value
        completed = dict(intent)
        completed.setdefault("posture", "BASELINE")
        if not str(completed.get("reason") or "").strip():
            # The reason and evidence the AI already had to supply for this entry.
            completed["reason"] = value.get("capture_reason")
        if not completed.get("evidence_refs"):
            completed["evidence_refs"] = (
                value.get("capture_evidence_refs") or value.get("supporting_fact_refs")
            )
        return {**value, "risk_intent": completed}

    @model_validator(mode="after")
    def _semantics(self) -> "EntryHypothesis":
        if self.decision == "NO_PLAN":
            if self.reason_code == "OK":
                raise ValueError("no_plan_reason_required")
            for field in (
                "primary_anchor_ref",
                "invalidation_anchor_ref",
                "entry_profile_id",
                "capture_priority",
                "capture_reason",
                "risk_intent",
            ):
                object.__setattr__(self, field, None)
            object.__setattr__(self, "capture_evidence_refs", ())
            object.__setattr__(self, "risk_tier", "NONE")
            object.__setattr__(self, "horizon", "NONE")
            return self
        if self.reason_code != "OK":
            raise ValueError("entry_plan_reason_must_be_ok")
        if not self.primary_anchor_ref or not self.invalidation_anchor_ref:
            raise ValueError("entry_anchor_required")
        if self.primary_anchor_ref == self.invalidation_anchor_ref:
            raise ValueError("entry_anchors_must_be_distinct")
        if not self.entry_profile_id:
            raise ValueError("entry_profile_required")
        if self.capture_priority is None or not (self.capture_reason or "").strip() or not self.capture_evidence_refs:
            raise ValueError("entry_capture_evidence_required")
        if self.risk_tier == "NONE" or self.horizon == "NONE":
            raise ValueError("entry_risk_horizon_required")
        if not self.supporting_fact_refs:
            raise ValueError("supporting_fact_required")
        object.__setattr__(self, "risk_intent", self._resolved_risk_intent())
        return self

    def _resolved_risk_intent(self) -> "RiskIntent":
        """The risk intent, completed from evidence the AI already had to supply.

        A model that omits the intent, or supplies only a posture, has expressed no
        *reduction* — so the Owner baseline posture stands, and the reason/evidence
        come from `capture_reason`/`capture_evidence_refs`, which ENTRY_PLAN already
        requires and `validate_hypothesis_references` checks against the context.
        An explicit posture is never overridden, and nothing numeric can enter
        here (#3729).
        """
        supplied = self.risk_intent
        reason = (supplied.reason if supplied else "") or (self.capture_reason or "")
        refs = tuple(supplied.evidence_refs) if supplied else ()
        if not refs:
            refs = tuple(self.capture_evidence_refs) or tuple(self.supporting_fact_refs)
        if supplied is None or not supplied.reason or not supplied.evidence_refs:
            logger.warning(
                "entry hypothesis %s risk_intent (model omitted part of it); posture=%s "
                "derived from capture evidence",
                "omitted" if supplied is None else "incomplete",
                supplied.posture if supplied else "BASELINE",
            )
        return RiskIntent(
            posture=supplied.posture if supplied else "BASELINE",
            reason=str(reason).strip()[:512],
            evidence_refs=tuple(refs)[:8],
        )


def entry_reference_contract(context: EntryAnalysisContext) -> dict[str, list[str]]:
    """Expose exactly the facts accepted by hypothesis validation, not provenance."""
    current = {
        str(ref)
        for section in (context.market_structure, context.quant_context,
                        context.portfolio_context, context.execution_context)
        for ref in section.get("fact_refs", ())
    }
    citable = current | {str(ref) for ref in context.candidate.get("fact_refs", ())}
    citable.update(anchor.anchor_id for anchor in context.anchors)
    current.update(anchor.anchor_id for anchor in context.anchors if anchor.kind != "candidate_reference")
    return {"citable_fact_refs": sorted(citable), "current_market_fact_refs": sorted(current)}


def entry_model_view(context: EntryAnalysisContext) -> dict[str, object]:
    """Project frozen evidence into the bounded facts exposed to the model."""

    data = context.model_dump(mode="json")
    planning = data["planning_generation"]
    structures = data["market_structure"]
    return {
        "analysis_cutoff_at": data["analysis_cutoff_at"],
        "context_fingerprint": data["context_fingerprint"],
        "reference_contract": entry_reference_contract(context),
        "instrument": data["instrument"],
        "planning_generation": {key: planning.get(key) for key in ("id", "candidate_id", "candidate_family")},
        "candidate": {"fact_refs": data["candidate"].get("fact_refs", [])},
        "candidate_opportunity_context": data["candidate_opportunity_context"],
        "candle_windows": {
            timeframe: {
                "availability": window.get("availability"),
                "source_fingerprint": window.get("source_fingerprint"),
            }
            for timeframe, window in data["candle_windows"].items()
        },
        "market_structure": {
            **{
                timeframe: {
                    key: section.get(key)
                    for key in (
                        "availability",
                        "state",
                        "atr14",
                        "geometry_fingerprint",
                        "fact_refs",
                    )
                }
                for timeframe, section in structures.items()
                if timeframe != "fact_refs" and isinstance(section, dict)
            },
            "fact_refs": structures.get("fact_refs", []),
        },
        "quant_context": data["quant_context"],
        "portfolio_context": data["portfolio_context"],
        "execution_context": data["execution_context"],
        "anchors": [
            {
                key: anchor.get(key)
                for key in (
                    "anchor_id",
                    "kind",
                    "timeframe",
                    "price",
                    "directional_role",
                    "known_at_ms",
                    "source_fact_refs",
                )
            }
            for anchor in data["anchors"]
        ],
        "entry_profiles": data["entry_profiles"],
        "learning_context": data["learning_context"],
        "unavailable_sections": data["unavailable_sections"],
    }


def validate_hypothesis_references(
    hypothesis: EntryHypothesis,
    context: EntryAnalysisContext,
) -> None:
    supplied_refs = set(hypothesis.supporting_fact_refs + hypothesis.opposing_fact_refs + hypothesis.capture_evidence_refs)
    reference_contract = entry_reference_contract(context)
    anchors = {}
    if supplied_refs or hypothesis.decision != "NO_PLAN":
        anchors = {anchor.anchor_id: anchor for anchor in context.anchors}
        facts = set(reference_contract["citable_fact_refs"])
        if not supplied_refs.issubset(facts):
            raise ValueError("hypothesis_fact_ref_not_supplied")
    opportunity = context.candidate_opportunity_context["opportunity"]
    if hypothesis.mechanism != opportunity["primary_mechanism"] or hypothesis.direction != opportunity["direction"]:
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
        "invalidation_candidate",
        "both",
    }:
        raise ValueError("hypothesis_invalidation_anchor_role_invalid")
    from aletheia.trading.entry_profiles import require_current_entry_profile

    profile = require_current_entry_profile(hypothesis.mechanism, str(hypothesis.entry_profile_id))
    if (
        anchors[hypothesis.primary_anchor_ref].kind not in profile.primary_anchor_kinds
        or anchors[hypothesis.invalidation_anchor_ref].kind not in profile.invalidation_anchor_kinds
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
    current_facts = set(reference_contract["current_market_fact_refs"])
    if not set(hypothesis.capture_evidence_refs) & current_facts:
        raise ValueError("hypothesis_capture_current_fact_required")
    if not set(hypothesis.supporting_fact_refs) & current_facts:
        raise ValueError("hypothesis_current_market_fact_required")


def _bind_experimental_entry_learning_context(
    learning_context: EntryLearningContext,
    treatment: object,
    learning_scope: dict[str, Any],
) -> EntryLearningContext:
    if learning_context.status != "observational":
        return empty_entry_learning_context(
            **learning_scope,
            reasons=learning_context.blocking_reasons,
        )
    return EntryLearningContext.model_validate(
        {
            **learning_context.model_dump(mode="json"),
            "status": "experimental",
            "experiment_identity": {
                "observation_id": getattr(treatment, "observation_id", None),
                "generation": getattr(treatment, "generation", None),
                "manifest_hash": getattr(treatment, "manifest_hash", None),
                "receipt_id": getattr(treatment, "receipt_id", None),
                "assignment": getattr(treatment, "assignment", None),
            },
        }
    )


def entry_orderbook_available(orderbook: dict | None, cutoff: datetime) -> bool:
    if not orderbook or any(
        orderbook.get(key) is None
        for key in ("bid_price", "ask_price", "spread_bps", "total_depth_10bps_usd", "observed_at")
    ):
        return False
    try:
        observed = datetime.fromisoformat(str(orderbook["observed_at"]).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    return observed.tzinfo is not None and 0 <= (cutoff - observed).total_seconds() <= 2


def entry_causal_windows(
    venue: str, symbol: str, market_type: str, direction: str,
    candle_windows: dict, cutoff: datetime,
) -> tuple[dict, dict, list[EntryAnchor], list[str]]:
    """Share the Planner's exact causal admission and structure kernel."""
    from aletheia.data.market_structure import (
        CONTRACT_VERSION,
        CORE_ALGORITHM_VERSION,
        PARAMS_HASH,
        canonical_market_structure_facts,
        derive_market_structure,
    )
    from aletheia.data.providers import Candle, timeframe_ms
    from aletheia.data.symbols import InstrumentId

    cutoff_ms = int(cutoff.timestamp() * 1000)
    serialized: dict[str, Any] = {}
    structures: dict[str, Any] = {}
    anchors: list[EntryAnchor] = []
    fact_refs: list[str] = []
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
            bars.append(
                {
                    key: row.get(key)
                    for key in (
                        "ts",
                        "open",
                        "high",
                        "low",
                        "close",
                        "volume",
                        "provider",
                        "exchange",
                        "market_type",
                        "first_persisted_at",
                        "last_mutated_at",
                    )
                }
            )
        valid = valid and len({row.get("provider") for row in rows}) == 1
        if not valid:
            serialized[timeframe] = {"availability": "unavailable", "bars": [], "source_fingerprint": None}
            structures[timeframe] = {"availability": "unavailable", "fact_refs": []}
            continue
        source_hash = fingerprint(bars)
        serialized[timeframe] = {"availability": "available", "bars": bars, "source_fingerprint": source_hash}
        geometry = derive_market_structure(
            [
                Candle(
                    int(row["ts"]),
                    float(row["open"]),
                    float(row["high"]),
                    float(row["low"]),
                    float(row["close"]),
                    float(row["volume"]),
                    True,
                    str(row["provider"]),
                    venue,
                )
                for row in bars
            ],
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
            for item in swings:
                anchors.append(
                    EntryAnchor(
                        venue=venue,
                        symbol=symbol,
                        market_type=market_type,
                        kind="confirmed_swing",
                        price=item.price,
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
                    )
                )
            for item in events:
                anchors.append(
                    EntryAnchor(
                        venue=venue,
                        symbol=symbol,
                        market_type=market_type,
                        kind="broken_structure_level",
                        price=item.broken_level,
                        directional_role="both",
                        known_at_ms=item.known_at_ms,
                        source_fact_refs=(item.id, item.broken_swing_id),
                        source_fingerprint=fingerprint(asdict(item)),
                        geometry_fingerprint=geometry.geometry_fingerprint,
                        aliases=("price_action", "smc"),
                    )
                )
    return serialized, structures, anchors, fact_refs

def planning_structure_material(structures: dict) -> dict | None:
    """Only the latest confirmed 15m high, low and break can requalify a thesis.

    Rolling-window hashes, ATR, candidate anchors and disappearing old facts
    are not new material. The full immutable fact values remain replayable.
    """
    section = structures.get("15m", {})
    if section.get("availability") != "available":
        return None
    swings = section.get("swings", ())
    groups = ([s for s in swings if s["kind"] == "high"],
              [s for s in swings if s["kind"] == "low"], section.get("events", ()))
    facts = [max(group, key=lambda f: (f["known_at_ms"], f["id"])) for group in groups if group]
    if not facts:
        return None
    facts = sorted(facts, key=lambda f: f["id"])
    return {"facts": facts, "fingerprint": fingerprint(facts)}





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
    cutoff = cutoff_at.astimezone(timezone.utc)
    cutoff_ms = int(cutoff.timestamp() * 1000)
    venue, symbol, market_type = (str(generation["venue"]), str(generation["symbol"]), str(generation["market_type"]))
    opportunity_context = None
    if generation.get("entry_behavior_fingerprint"):
        from aletheia.universe.candidate_opportunity import CandidateOpportunityContext

        opportunity_context = CandidateOpportunityContext.model_validate(
            json.loads(generation["requalification_source_json"])["candidate_opportunity_context"]
        )
        if (
            opportunity_context.candidate_id != generation["candidate_id"]
            or opportunity_context.opportunity["primary_mechanism"] != generation.get("opportunity_mechanism")
            or opportunity_context.opportunity["direction"] != generation.get("direction")
            or opportunity_context.candidate_selection["policy"] != generation.get("candidate_selection_policy")
            or opportunity_context.candidate_selection.get("policy_epoch")
            != generation.get("candidate_selection_policy_epoch")
            or opportunity_context.opportunity["mechanism_version"] != generation.get("mechanism_version")
        ):
            raise ValueError("candidate_opportunity_generation_identity_mismatch")
    if (
        candidate.get("id") != generation.get("last_candidate_id")
        or candidate.get("venue") != venue
        or candidate.get("symbol") != symbol
        or candidate.get("market_type") != market_type
        or (opportunity_context is None and candidate.get("scanner_family") != generation.get("candidate_family"))
        or candidate.get("scanner_direction") != generation.get("direction")
    ):
        raise ValueError("candidate_generation_identity_mismatch")
    serialized, structures, anchors, fact_refs = entry_causal_windows(
        venue, symbol, market_type, str(generation["direction"]), candle_windows, cutoff,
    )
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
    anchors.append(
        EntryAnchor(
            venue=venue,
            symbol=symbol,
            market_type=market_type,
            kind="candidate_reference",
            price=float(candidate["entry_price_usd"]),
            directional_role="entry_candidate",
            known_at_ms=detected_ms,
            source_fact_refs=(candidate_ref,),
            source_fingerprint=fingerprint(candidate_ref),
        )
    )
    if serialized.get("15m", {}).get("availability") == "available":
        from datetime import timedelta
        from aletheia.trading.momentum_entry_geometry import (
            MomentumGeometryUnavailable,
            compile_momentum_entry_geometry,
        )

        try:
            geometry = compile_momentum_entry_geometry(
                candidate=candidate,
                feature=feature,
                candles=serialized["15m"]["bars"][-21:],
                direction=generation["direction"],
                minimum_risk_reward=1.5,
                source_cutoff_at=cutoff,
                valid_until=cutoff + timedelta(hours=4),
            )
        except MomentumGeometryUnavailable:
            geometry = None
        if geometry is not None:
            anchors.append(
                EntryAnchor(
                    venue=venue,
                    symbol=symbol,
                    market_type=market_type,
                    kind="causal_breakout",
                    price=geometry.breakout_level,
                    directional_role="entry_candidate",
                    known_at_ms=max(int(row["ts"]) + 15 * 60 * 1000 for row in serialized["15m"]["bars"][-21:]),
                    source_fact_refs=tuple(geometry.source_candle_ids),
                    source_fingerprint=geometry.source_hash,
                    geometry_fingerprint=geometry.geometry_fingerprint,
                    aliases=("price_action", "smc"),
                )
            )
    unique = {item.anchor_id: item for item in anchors if item.known_at_ms <= cutoff_ms}
    selected = tuple(sorted(unique.values(), key=lambda item: (item.known_at_ms, item.anchor_id), reverse=True)[:12])
    orderbook_available = entry_orderbook_available(orderbook, cutoff)
    estimated_cost = candidate.get("estimated_cost")
    quant_fields = (
        "atr_pct",
        "rsi_15m",
        "atr_15m_pct",
        "return_15m",
        "return_1h",
        "return_4h",
        "return_24h",
        "funding_rate",
        "open_interest",
        "oi_delta_1h",
        "long_short_ratio",
        "top_trader_position_ratio",
        "taker_buy_ratio",
        "vol_ratio",
        "relative_strength_24h",
        "oi_price_divergence",
        "rsi_4h",
        "atr_4h_pct",
        "bb_width_4h",
        "vol_ratio_4h",
        "bb_squeeze",
        "data_freshness_sec",
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
        {key: feature.get(key) for key in quant_fields if feature.get(key) is not None} if feature_valid else {}
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
            item[1]
            for item in admitted_positions
            if item[0].get("symbol") == symbol and item[0].get("market_type") == market_type
        ),
        "long_count": sum(item[0].get("direction") == "long" for item in admitted_positions),
        "short_count": sum(item[0].get("direction") == "short" for item in admitted_positions),
        "fact_refs": portfolio_refs,
    }
    if opportunity_context is None:
        profiles = ()
    else:
        from aletheia.trading.entry_profiles import compatible_entry_profiles

        profiles = tuple(
            profile.as_dict()
            for profile in compatible_entry_profiles(str(opportunity_context.opportunity["primary_mechanism"]))
        )
    if opportunity_context is None:
        learning_context: object = {"availability": "unavailable", "reason": "exact_ai_entry_evidence_unavailable"}
    else:
        learning_scope = {
            "as_of_at": cutoff.isoformat(),
            "mechanism": str(opportunity_context.opportunity["primary_mechanism"]),
            "direction": str(opportunity_context.opportunity["direction"]),
            "market_type": market_type,
            "policy": str(opportunity_context.candidate_selection["policy"]),
            "policy_epoch": opportunity_context.candidate_selection.get("policy_epoch"),
        }
        learning_context = resolve_entry_learning_context(
            generation=generation, model_provider=model_provider, profiles=profiles, **learning_scope,
        )
    candidate_fact_refs = [candidate_ref]
    if opportunity_context is not None:
        candidate_fact_refs.extend(_candidate_opportunity_evidence_refs(opportunity_context.opportunity))
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
            "motive_evidence": (None if opportunity_context is not None else candidate.get("inclusion_reason")),
            "risk_flags": candidate.get("risk_flags"),
            "fact_refs": candidate_fact_refs,
        },
        candle_windows=serialized,
        market_structure={**structures, "fact_refs": fact_refs},
        quant_context={
            "availability": "available" if feature_ref and quant_values else "unavailable",
            "feature_snapshot_id": feature.get("id") if feature_valid else None,
            "feature_snapshot_at": feature.get("ts") if feature_valid else None,
            "technical_algorithm_version": feature.get("technical_algorithm_version") if feature_valid else None,
            "technical_snapshot_manifest_hash": (
                fingerprint(feature.get("technical_snapshot_manifest") or "{}") if feature_valid else None
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
    return EntryAnalysisContext.model_validate(context_fields)
