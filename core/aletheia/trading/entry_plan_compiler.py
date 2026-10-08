"""Compile bounded planner intent into an authoritative EntryPlan."""

from __future__ import annotations

import math
import hashlib
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Literal, cast

from aletheia.risk.policy import UnknownRiskExecutionMode, resolve_risk_policy
from aletheia.trading.entry_plan import EntryPlan
from aletheia.trading.instruments import get_max_leverage
from aletheia.trading.trigger_specs import ANCHOR_GEOMETRY_VERSION

if TYPE_CHECKING:
    from aletheia.trading.entry_analysis import EntryHypothesis


_RISK_REWARD_TARGET = {"LOW": 1.5, "MEDIUM": 2.5}
_HORIZON_HOURS = {"INTRADAY": 4.0, "SWING": 24.0}
_HORIZON_LIFETIME_MINUTES = {"INTRADAY": 60, "SWING": 240}

_GEOMETRY = {
    "band_vol_fraction": 0.5,
    "fallback_tolerance_atr": 0.05,
    "zone_atr": 0.25,
    # Structural stop and target geometry (#3777): the stop clears the
    # invalidation zone's outer edge by this multiple of the zone's OWN timeframe
    # ATR, and a target zone must clear the entry by this multiple of 15m ATR to
    # count as opposing structure rather than the entry's own band.
    "stop_buffer_atr": 0.5,
    "zone_min_distance_atr": 0.5,
}
_TACTIC = {"maker_wait_sec": 60, "max_requotes": 0, "urgent_slippage_bps": 20.0}


def compiler_contract_material() -> dict:
    return {
        "risk_reward_target": _RISK_REWARD_TARGET,
        "horizon_hours": _HORIZON_HOURS,
        "lifetime_minutes": _HORIZON_LIFETIME_MINUTES,
        "geometry": _GEOMETRY, "tactic": _TACTIC,
        "planning_market_quality": "exact-envelope-before-input-bind/v1",
        "static_risk_feasibility": "shared-instrument-and-stop-floor/v1",
        # Canonical sizing and financing identities (#3729). The compiler freezes
        # both resolutions; neither leverage nor a policy cap may enlarge the target.
        "sizing_policy": "adaptive-risk-intent-sizing/v2",
        "financing_policy": "lowest-sufficient-deterministic-leverage/v1",
        "submission_margin_mode": "declared-per-execution-route/v1",
    }


def _number(value: Any, code: str, *, positive: bool = False) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{code}_invalid") from exc
    if not math.isfinite(result) or (positive and result <= 0):
        raise ValueError(f"{code}_invalid")
    return result


def _intent_error(code: str, detail: str = ""):
    from aletheia.agent_shell.entry_planner import PlanIntentError

    return PlanIntentError(code, detail)


def _stop_reference(feature: dict, cfg) -> tuple[float, float]:
    try:
        atr_pct = float(feature.get("atr_pct") or 0.0)
    except (TypeError, ValueError):
        atr_pct = 0.0
    try:
        vol_target_pct = float(getattr(cfg.trading, "vol_target_pct", 0.0) or 0.0)
    except (TypeError, ValueError):
        vol_target_pct = 0.0
    if not math.isfinite(atr_pct):
        atr_pct = 0.0
    if not math.isfinite(vol_target_pct):
        vol_target_pct = 0.0
    if atr_pct <= 0 and vol_target_pct <= 0:
        raise _intent_error("NO_STOP_REFERENCE")
    return atr_pct, vol_target_pct


def _timeframe_atr(context, timeframe: str) -> float:
    """The frozen ATR14 of one structure timeframe, as a price distance."""
    section = context.market_structure.get(timeframe) or {}
    from aletheia.agent_shell.entry_planner import PlanIntentError

    try:
        atr = float(section.get("atr14"))
    except (TypeError, ValueError) as exc:
        raise PlanIntentError("ATR_UNAVAILABLE") from exc
    if not math.isfinite(atr) or atr <= 0:
        raise PlanIntentError("ATR_UNAVAILABLE")
    return atr


def _structural_stop(
    invalidation, *, direction: str, context, fallback_tolerance: float
) -> float:
    """The invalidation zone's outer edge, cleared by its own timeframe's noise.

    Noise is measured on the timeframe the level lives on: a 1h or 4h level is
    invalidated over 1h or 4h range, not over 15m range (#3777). An anchor with no
    band (the candidate reference) keeps the legacy narrow tolerance.
    """
    zone = getattr(invalidation, "zone", None)
    if zone is None:
        return (
            invalidation.price - fallback_tolerance
            if direction == "long" else invalidation.price + fallback_tolerance
        )
    buffer = _GEOMETRY["stop_buffer_atr"] * _timeframe_atr(context, invalidation.timeframe)
    return zone.low - buffer if direction == "long" else zone.high + buffer


def _widen_to_min_stop(closest_entry: float, stop: float, *, direction: str, cfg) -> float:
    """Push a stop out to the derived minimum distance. It never tightens one.

    The Compiler does not reject a structurally-correct but tight stop; the
    existing loss-budget sizing then derives a correspondingly smaller notional
    from the wider stop (#3777).
    """
    from aletheia.trading.entry_plan import min_stop_distance_pct, stop_distance_pct

    floor = min_stop_distance_pct(cfg=cfg)
    distance = stop_distance_pct(closest_entry, stop)
    if floor is None or distance is None or distance >= floor:
        return stop
    offset = closest_entry * floor / 100.0
    return closest_entry - offset if direction == "long" else closest_entry + offset


def _structural_target(
    *,
    direction: str,
    worst_entry: float,
    best_entry: float,
    stop: float,
    context,
    atr15: float,
) -> float:
    """The near edge of the nearest opposing zone inside the Plan's RR envelope.

    The envelope must hold at the *executable* entry, not only at the frozen
    zone's worst price: `EntryPlan`'s own coherence validator measures the ratio at
    the maker `limit_price` and Risk measures it at the submitted price, both of
    which sit between `best_entry` and `worst_entry`. Admitting a candidate on the
    worst-entry ratio alone would let a target near the cap compile into a Plan
    whose validator (or Risk) rejects the ratio it actually trades at. For either
    direction the worst-entry ratio is the smaller one, so requiring
    ``ratio(worst_entry) >= MIN`` and ``ratio(best_entry) <= MAX`` bounds both.

    No admissible target is a typed no-plan: an entry with no room to its opposing
    structure is not a bet worth making (#3777).
    """
    from aletheia.agent_shell.entry_planner import PlanIntentError
    from aletheia.trading.entry_plan import (
        ENTRY_PLAN_MAX_RISK_REWARD,
        ENTRY_PLAN_MIN_RISK_REWARD,
    )

    risk_worst = abs(worst_entry - stop)
    risk_best = abs(best_entry - stop)
    if not all(math.isfinite(value) and value > 0 for value in (risk_worst, risk_best)):
        raise PlanIntentError("COMPILER_GEOMETRY_UNSATISFIABLE")
    min_gap = _GEOMETRY["zone_min_distance_atr"] * atr15
    edges: list[float] = []
    for anchor in context.anchors:
        zone = getattr(anchor, "zone", None)
        if zone is None or anchor.directional_role not in {"entry_candidate", "both"}:
            continue
        # The *near* edge of the opposing band is where price first meets it.
        edge = zone.low if direction == "long" else zone.high
        if (edge - best_entry if direction == "long" else best_entry - edge) <= min_gap:
            continue
        edges.append(edge)
    for edge in sorted(edges, key=lambda value: abs(value - best_entry)):
        ratio_worst = abs(edge - worst_entry) / risk_worst
        ratio_best = abs(edge - best_entry) / risk_best
        if ratio_worst >= ENTRY_PLAN_MIN_RISK_REWARD and ratio_best <= ENTRY_PLAN_MAX_RISK_REWARD:
            return edge
    raise PlanIntentError("COMPILER_NO_EXECUTABLE_TARGET")


def _compile_bounded_risk_envelope(
    generation: dict,
    candidate: dict,
    feature: dict,
    hypothesis: "EntryHypothesis",
    cfg,
    *,
    sizing,
    financing,
    policy_leverage_cap: int,
    now: datetime | None = None,
) -> EntryPlan:
    """Compute the existing conservative risk envelope for canonical compilation."""

    from aletheia.agent_shell.entry_planner import PlanIntentError

    try:
        if hypothesis.decision != "ENTRY_PLAN":
            raise PlanIntentError("COMPILER_ENTRY_INTENT_REQUIRED")
        raw_market_type = generation.get("market_type")
        if raw_market_type not in {"spot", "perp"}:
            raise PlanIntentError("COMPILER_MARKET_TYPE_INVALID")
        market_type = cast(Literal["spot", "perp"], raw_market_type)
        raw_direction = generation.get("direction")
        if raw_direction not in {"long", "short"}:
            raise PlanIntentError("COMPILER_DIRECTION_INVALID")
        direction = cast(Literal["long", "short"], raw_direction)
        if hypothesis.risk_tier not in _RISK_REWARD_TARGET:
            raise PlanIntentError("COMPILER_RISK_TIER_INVALID")
        if hypothesis.horizon not in _HORIZON_HOURS:
            raise PlanIntentError("COMPILER_HORIZON_INVALID")

        try:
            candidate_price = _number(candidate.get("entry_price_usd"), "candidate_price", positive=True)
        except ValueError as exc:
            raise PlanIntentError("STALE_CANDIDATE_PRICE") from exc

        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        expires = current + timedelta(minutes=_HORIZON_LIFETIME_MINUTES[hypothesis.horizon])
        atr_pct, vol_target_pct = _stop_reference(feature or {}, cfg)
        # The bounded envelope's own geometry is a carrier: `compile_entry_plan`
        # always replaces entry/stop/target with the structural geometry (#3777).
        # It carries the same derived floor so the two can never disagree.
        from aletheia.trading.entry_plan import min_stop_distance_pct

        stop_distance_pct = min_stop_distance_pct(cfg=cfg) or max(vol_target_pct, atr_pct)
        if not math.isfinite(stop_distance_pct) or stop_distance_pct <= 0:
            raise PlanIntentError("NO_STOP_REFERENCE")
        if direction == "long":
            stop_price = candidate_price * (1.0 - stop_distance_pct / 100.0)
            target_price = candidate_price + _RISK_REWARD_TARGET[hypothesis.risk_tier] * (candidate_price - stop_price)
        else:
            stop_price = candidate_price * (1.0 + stop_distance_pct / 100.0)
            target_price = candidate_price - _RISK_REWARD_TARGET[hypothesis.risk_tier] * (stop_price - candidate_price)
        band_pct = _GEOMETRY["band_vol_fraction"] * vol_target_pct
        entry_zone = (
            candidate_price * (1.0 - band_pct / 100.0),
            candidate_price * (1.0 + band_pct / 100.0),
        )

        execution_account_mode = str(cfg.exchange.paper_execution_mode)
        try:
            resolved_policy = resolve_risk_policy(cfg, execution_account_mode)
        except UnknownRiskExecutionMode as exc:
            raise PlanIntentError("RISK_POLICY_UNKNOWN") from exc
        from aletheia.execution.venue_capabilities import entry_execution_minimum_permission
        if entry_execution_minimum_permission(execution_account_mode) is None:
            raise PlanIntentError("RISK_POLICY_UNKNOWN")
        policy = resolved_policy.policy
        max_risk_pct = float(policy.loss.max_single_trade_risk_pct)
        # Sizing and financing are resolved once, from the final stop geometry, by
        # the canonical pure functions (#3729). Leverage is a financing output,
        # never an input to the target exposure.
        if sizing.status != "resolved":
            raise PlanIntentError(sizing.reason or "COMPILER_RISK_BUDGET_UNAVAILABLE")
        if financing.execution_leverage is None or financing.execution_notional_usdt is None:
            raise PlanIntentError(financing.reason or "NO_FEASIBLE_FINANCING")
        max_leverage = int(financing.execution_leverage)
        max_notional = float(financing.execution_notional_usdt)
        if max_leverage < 1 or max_risk_pct <= 0 or max_notional <= 0:
            raise PlanIntentError("COMPILER_RISK_LIMIT_INVALID")

        try:
            return EntryPlan.model_construct(
                planning_generation_id=generation["id"],
                candidate_id=generation["last_candidate_id"],
                candidate_family=generation["candidate_family"],
                venue=generation["venue"],
                symbol=generation["symbol"],
                market_type=market_type,
                direction=direction,
                thesis=f"{hypothesis.mechanism} grounded entry",
                evidence_refs=tuple(hypothesis.supporting_fact_refs),
                scenario=str(hypothesis.entry_profile_id),
                execution_mode="RESTING_MAKER",
                trigger_spec_id=None,
                trigger_params={},
                trigger_data_requirements=(),
                trigger_freshness_ms=None,
                entry_zone=entry_zone,
                invalidation_conditions=(("close_below_stop",) if direction == "long" else ("close_above_stop",)),
                invalidation_price=stop_price,
                stop_price=stop_price,
                target_price=target_price,
                expected_horizon_hours=_HORIZON_HOURS[hypothesis.horizon],
                approved_at=current.isoformat(),
                expires_at=expires.isoformat(),
                order_tactic="POST_ONLY_LIMIT",
                maker_required=True,
                time_in_force="POST_ONLY",
                limit_price=candidate_price,
                price_rule="EXACT_LIMIT",
                bbo_reference=None,
                max_wait_sec=_TACTIC["maker_wait_sec"],
                cancel_replace_policy="CANCEL_ONLY",
                max_requotes=_TACTIC["max_requotes"],
                partial_fill_policy="KEEP_PARTIAL_CANCEL_REMAINDER",
                taker_fallback_allowed=False,
                max_slippage_bps=None,
                worst_price=None,
                max_notional=max_notional,
                max_risk_pct=max_risk_pct,
                max_leverage=max_leverage,
                risk_intent_posture=sizing.risk_intent_posture,
                risk_intent_multiplier=sizing.risk_intent_multiplier,
                sizing_resolution=sizing,
                leverage_resolution=financing,
                policy_leverage_cap=policy_leverage_cap,
                execution_leverage=max_leverage,
                target_notional_usdt=sizing.target_notional_usdt,
                execution_notional_usdt=max_notional,
                margin_mode=None if market_type == "spot" else financing.margin_mode,
                required_permission=str(cfg.agent.permission_mode),
                execution_account_mode=execution_account_mode,
                risk_policy_id=str(policy.policy_id),
                risk_policy_revision=int(policy.revision),
                risk_policy_hash=resolved_policy.policy_hash,
                treatment_exposure_id=None,
            )
        except ValueError as exc:
            raise PlanIntentError(f"COMPILER_GEOMETRY_UNSATISFIABLE:{str(exc)[:100]}") from exc
    except PlanIntentError:
        raise
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise PlanIntentError("COMPILER_INTERNAL", type(exc).__name__) from exc


def require_entry_market_quality(execution_context, cfg) -> None:
    """One exact spread/depth/cost envelope for preflight and Compiler."""
    from aletheia.agent_shell.entry_planner import PlanIntentError

    if execution_context.get("availability") != "available":
        raise PlanIntentError("EXECUTION_CONTEXT_UNAVAILABLE")
    try:
        spread = float(execution_context["spread_bps"])
        depth = float(execution_context["depth_10bps_usd"])
        costs = float(execution_context["estimated_costs"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PlanIntentError("EXECUTION_CONTEXT_UNAVAILABLE") from exc
    if (
        not all(math.isfinite(value) for value in (spread, depth, costs))
        or spread < 0 or costs < 0
        or spread > float(cfg.universe.max_spread_bps)
        or depth < float(cfg.universe.min_depth_10bps_usd)
    ):
        raise PlanIntentError("EXECUTION_CONTEXT_UNAVAILABLE")


def compile_entry_plan(generation, context, hypothesis, cfg) -> EntryPlan:
    """Compile a supplied-anchor hypothesis through the existing bounded Risk owner."""
    from aletheia.agent_shell.entry_planner import PlanIntentError
    from aletheia.trading.entry_analysis import (
        canonical_json,
        fingerprint,
        validate_hypothesis_references,
    )

    validate_hypothesis_references(hypothesis, context)

    from aletheia.trading.entry_authority import require_entry_runtime_authority
    from aletheia.trading.entry_profiles import require_current_entry_profile

    authority = require_entry_runtime_authority(cfg)

    from aletheia.trading.entry_plan import (
        entry_instrument_violation,
    )

    instrument_violation = entry_instrument_violation(generation["market_type"], generation["direction"])
    if instrument_violation:
        raise PlanIntentError(instrument_violation)
    require_entry_market_quality(context.execution_context, cfg)
    anchors = {item.anchor_id: item for item in context.anchors}
    primary = anchors[hypothesis.primary_anchor_ref]
    invalidation = anchors[hypothesis.invalidation_anchor_ref]
    if primary.directional_role not in {"entry_candidate", "both"}:
        raise PlanIntentError("PRIMARY_ANCHOR_ROLE_INVALID")
    if invalidation.directional_role not in {"invalidation_candidate", "both"}:
        raise PlanIntentError("INVALIDATION_ANCHOR_ROLE_INVALID")
    direction = generation["direction"]
    if (direction == "long" and invalidation.price >= primary.price) or (
        direction == "short" and invalidation.price <= primary.price
    ):
        raise PlanIntentError("INVALIDATION_ANCHOR_WRONG_SIDE")
    structure = context.market_structure.get("15m") or {}
    atr = float(structure.get("atr14") or 0.0)
    if not math.isfinite(atr) or atr <= 0:
        raise PlanIntentError("ATR_UNAVAILABLE")
    tolerance = float(structure.get("break_tolerance") or _GEOMETRY["fallback_tolerance_atr"] * atr)
    # Structural Support/Resistance geometry (#3777): the stop clears the
    # invalidation zone's outer edge, and the floor only ever widens it.
    stop = _structural_stop(
        invalidation, direction=direction, context=context, fallback_tolerance=tolerance
    )
    retrace = hypothesis.entry_profile_id == "grounded-anchor-retrace-reclaim"
    quarter = _GEOMETRY["zone_atr"] * atr
    if retrace:
        zone = (
            (primary.price, primary.price + quarter)
            if direction == "long" else (primary.price - quarter, primary.price)
        )
        worst_entry = zone[1] if direction == "long" else zone[0]
    else:
        zone = (primary.price, primary.price)
        worst_entry = primary.price
    # All prices in the frozen zone must sit beyond the floor, so the closest
    # permitted entry is the one that decides it.
    closest_entry = zone[0] if direction == "long" else zone[1]
    stop = _widen_to_min_stop(closest_entry, stop, direction=direction, cfg=cfg)
    quant_values = context.quant_context.get("values") or {}
    atr_pct = quant_values.get("atr_pct") or (atr / float(context.candidate["candidate_price"]) * 100.0)
    target = _structural_target(
        direction=direction, worst_entry=worst_entry, best_entry=closest_entry,
        stop=stop, context=context, atr15=atr,
    )
    if target <= 0:
        raise PlanIntentError("COMPILER_GEOMETRY_UNSATISFIABLE")
    context_generation = context.planning_generation
    instrument = context.instrument
    if (
        context_generation.get("id") != generation.get("id")
        or context_generation.get("entry_behavior_fingerprint")
        != generation.get("entry_behavior_fingerprint")
        or context_generation.get("schema_fingerprint")
        != generation.get("schema_fingerprint")
        or instrument.get("venue") != generation.get("venue")
        or instrument.get("symbol") != generation.get("symbol")
        or instrument.get("market_type") != generation.get("market_type")
        or instrument.get("direction") != generation.get("direction")
    ):
        raise PlanIntentError("FROZEN_CONTEXT_IDENTITY_MISMATCH")
    frozen_generation = {
        **generation,
        "last_candidate_id": context_generation["candidate_id"],
        "candidate_family": context_generation["candidate_family"],
    }
    frozen_candidate = {
        "id": context_generation["candidate_id"],
        "entry_price_usd": context.candidate["candidate_price"],
        "learning_candidate_key": None,
    }
    frozen_feature = {"atr_pct": atr_pct}
    geometry_material = {
        "entry_zone": zone,
        "invalidation_price": invalidation.price,
        "stop_price": stop,
        "target_price": target,
        "trigger_params": {},
    }
    trigger_params = {}
    if retrace:
        trigger_params = {
            "breakout_level": primary.price,
            "pullback_low": primary.price - quarter,
            "pullback_high": primary.price + quarter,
            "entry_low": zone[0], "entry_high": zone[1],
            "invalidation_price": invalidation.price,
            "sweep_low": primary.price - quarter if direction == "long" else primary.price,
            "sweep_high": primary.price if direction == "long" else primary.price + quarter,
            "atr_abs": atr,
            "geometry_version": ANCHOR_GEOMETRY_VERSION,
            "geometry_fingerprint": "0" * 64,
        }
        trigger_params["geometry_fingerprint"] = fingerprint(
            {key: value for key, value in trigger_params.items() if key != "geometry_fingerprint"}
        )
        geometry_material["trigger_params"] = trigger_params
    # ── Canonical adaptive sizing + deterministic financing (#3729) ──────────
    from aletheia.execution.margin_mode_policy import submission_margin_mode
    from aletheia.risk.engine import account_sizing_state, sizing_policy_facts
    from aletheia.trading.entry_sizing import SizingUnavailable, resolve_entry_sizing
    from aletheia.trading.leverage_resolution import resolve_execution_financing
    from aletheia.trading.risk_intent import RiskIntentInvalid, resolve_risk_intent_multiplier
    from aletheia.trading.venue_margin import VenueMarginUnavailable, load_venue_margin_envelope

    resolved_policy = resolve_risk_policy(cfg, authority.execution_route)
    market_type = generation["market_type"]
    try:
        multiplier, multiplier_source = resolve_risk_intent_multiplier(
            hypothesis.risk_intent, resolved_policy.policy
        )
    except RiskIntentInvalid as exc:
        raise PlanIntentError(exc.reason) from exc
    # The Owner policy tier leverage is a ceiling for the resolver, never the
    # execution leverage: the resolver finances the target with the lowest
    # leverage the venue and its own liquidation buffer allow.
    try:
        policy_leverage_cap = (
            1 if market_type == "spot"
            else int(get_max_leverage(generation["symbol"], False, cfg))
        )
    except Exception as exc:
        raise PlanIntentError("COMPILER_LEVERAGE_UNAVAILABLE") from exc
    margin_mode = submission_margin_mode(authority.execution_route)
    if market_type == "perp" and margin_mode is None:
        raise PlanIntentError("MARGIN_MODE_UNAVAILABLE")
    try:
        # Financing facts belong to the venue that will HOLD the position, which is
        # the execution route's venue — the candidate's venue only identifies the
        # market data (they differ whenever analysis and execution run on different
        # venues, e.g. a binance-sourced universe executed on bitget_demo).
        from aletheia.config.exchanges import execution_mode_venue

        position_venue = execution_mode_venue(authority.execution_route) or generation["venue"]
        envelope = None if market_type == "spot" else load_venue_margin_envelope(
            venue=position_venue,
            symbol=generation["symbol"],
            market_type=market_type,
            margin_mode=margin_mode or "isolated",
            cfg=cfg,
        )
        account = account_sizing_state(
            authority.execution_route,
            generation["symbol"],
            cfg=cfg,
            # The preflight depth envelope is the executable exposure the entry
            # market can absorb; venue order-rule minima stay enforced by the
            # existing submission boundary (`prepare_exchange_quantity`).
            executable_notional_cap_usdt=float(context.execution_context["depth_10bps_usd"]),
            instrument_min_notional_usdt=(
                envelope.tiers[0].min_notional if envelope is not None else None
            ),
        )
        sizing = resolve_entry_sizing(
            risk_intent_posture=hypothesis.risk_intent.posture,
            risk_intent_multiplier=multiplier,
            risk_intent_multiplier_source=multiplier_source,
            worst_entry_price=float(worst_entry),
            stop_price=float(stop),
            policy=sizing_policy_facts(authority.execution_route, cfg=cfg),
            account=account,
        )
        financing = resolve_execution_financing(
            sizing=sizing,
            envelope=envelope,
            policy_leverage_cap=policy_leverage_cap,
            liquidation_buffer_pct=float(
                resolved_policy.policy.margin.min_entry_liquidation_buffer_pct
            ),
            existing_position_notional_usdt=account.open_pool_notional_usdt,
            spot=market_type == "spot",
        )
    except (SizingUnavailable, VenueMarginUnavailable) as exc:
        raise PlanIntentError(exc.reason, getattr(exc, "detail", "") or "") from exc
    base = _compile_bounded_risk_envelope(
        frozen_generation, frozen_candidate, frozen_feature, hypothesis, cfg,
        sizing=sizing, financing=financing, policy_leverage_cap=policy_leverage_cap,
    )
    from aletheia.trading.entry_capture import CAPTURE_POLICIES
    capture_policy = CAPTURE_POLICIES[hypothesis.capture_priority]
    maker = hypothesis.capture_priority != "FILL_FIRST"
    bid, ask = float(context.execution_context["bid"]), float(context.execution_context["ask"])
    if not all(math.isfinite(value) and value > 0 for value in (bid, ask)) or ask < bid:
        raise PlanIntentError("EXECUTION_CONTEXT_UNAVAILABLE")
    limit_price = primary.price if maker else None
    # Frozen BBO is context, not future submission authority. The fixed passive
    # price waits for current-BBO eligibility in the existing execution boundary.
    hypothesis_json = canonical_json(hypothesis.model_dump(mode="json"))
    source = context.candidate_opportunity_context["source"]
    updates = {
        "source_kind": source["kind"],
        "source_id": source["id"],
        "trade_school": source["trade_school"],
        "holding_horizon_hours": (
            float(cfg.serenity.max_hold_days) * 24
            if source["kind"] == "serenity" else base.expected_horizon_hours
        ),
        "entry_zone": zone,
        "execution_mode": "TRIGGERED" if retrace else "RESTING_MAKER",
        "order_tactic": "POST_ONLY_LIMIT" if maker else "OPPOSING_BBO_IOC",
        "maker_required": maker, "time_in_force": "POST_ONLY" if maker else "IOC",
        "price_rule": "EXACT_LIMIT" if maker else "OPPOSING_BBO",
        "bbo_reference": None if maker else ("ask" if direction == "long" else "bid"),
        "max_wait_sec": capture_policy.passive_attempt_timeout_sec,
        "capture_policy_id": capture_policy.policy_id,
        "max_slippage_bps": None if hypothesis.capture_priority == "PRICE_FIRST" else _TACTIC["urgent_slippage_bps"],
        "trigger_spec_id": require_current_entry_profile(
            hypothesis.mechanism, hypothesis.entry_profile_id,
        ).trigger_spec_id,
        "invalidation_price": invalidation.price, "stop_price": stop, "target_price": target,
        "trigger_params": trigger_params,
        "trigger_data_requirements": (("quote", "trade", "books5", "candle1m") if retrace else ()),
        "trigger_freshness_ms": 2000 if retrace else None,
        "limit_price": limit_price,
        "worst_price": worst_entry,
        "required_permission": authority.minimum_permission,
        "execution_account_mode": authority.execution_route,
        "treatment_exposure_id": None,
        "producer_commit": generation.get("producer_commit"),
        "behavior_fingerprint": generation.get("entry_behavior_fingerprint"),
        "schema_fingerprint": generation.get("schema_fingerprint"),
        "planning_identity": generation.get("planning_identity"),
        "cycle_number": generation.get("cycle_number"),
        "analysis_context_fingerprint": context.context_fingerprint,
        "entry_hypothesis_json": hypothesis_json,
        "entry_hypothesis_hash": hashlib.sha256(hypothesis_json.encode()).hexdigest(),
        "primary_anchor_snapshot": primary.model_dump(mode="json"),
        "invalidation_anchor_snapshot": invalidation.model_dump(mode="json"),
        "entry_profile_id": hypothesis.entry_profile_id,
        "compiled_geometry_fingerprint": fingerprint(geometry_material),
    }
    return EntryPlan.model_validate({**base.model_dump(mode="json"), **updates})
