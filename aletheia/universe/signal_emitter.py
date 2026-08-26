"""Universe Candidate handoff and deterministic Trigger materialization.

Universe refresh selects Candidates. Each eligible family can own a durable
UniverseSetup row. Opportunity rows are created only when that setup's
EntryEligibility first becomes true for the exact current market facts.

Called at the end of every universe refresh cycle (source_agent="universe_scanner").
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging

from aletheia.storage import repositories as repo
from aletheia.storage.models import (
    Opportunity,
    OpportunityGenerationV2,
    OpportunityInstrumentIdentity,
    UniverseSetup,
)
from aletheia.analytics.factor_feedback import lookup_factor_signal
from aletheia.data.symbols import (
    MARKET_PERP,
    MARKET_SPOT,
    instrument_analysis_context,
    instrument_background_rejection_reason,
)
from aletheia.learning.opportunity_information_policy import POLICY_EPOCH_LABEL
from aletheia.trading.instruments import instrument_market_to_db_market
from aletheia.universe.funding_units import (
    CROWDED_FUNDING_RATE,
    ELEVATED_FUNDING_RATE,
)

logger = logging.getLogger("aletheia.universe.signal_emitter")


# Only arm setup handoff for members that scored high enough to be worth review.
_MIN_SCORE = 45.0
# Score threshold used by the existing regime-bias override.
_HIGH_CONF_THRESHOLD = 60.0

# Regime bias threshold: if the direction's bias < this value, skip signal
# unless score compensates (higher score can override weak regime alignment).
_REGIME_BIAS_THRESHOLD = 0.30
ENTRY_ELIGIBILITY_VERSION = "universe-entry-eligibility/v1"
ENTRY_TRIGGER_BEHAVIOR_VERSION = "universe-trigger-materializer/v1"
_DEFAULT_HORIZON_HOURS = 4.0
_OPPORTUNITY_EVIDENCE_VERSION = "opportunity-information-integrity-v1"
_FAMILY_ARCHETYPE = {
    "momentum": "continuation",
    "mean_reversion": "reversal",
    "volatility": "squeeze",
    "derivatives": "convergence",
    "event": "event",
    "short_bias": "continuation",
}


def _json(data: object) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha(data: object) -> str:
    return hashlib.sha256(_json(data).encode("utf-8")).hexdigest()


def _physical_market_type(market_type: str | None) -> str | None:
    normalized = str(market_type or "").strip().lower()
    if normalized in {"spot", MARKET_SPOT}:
        return MARKET_SPOT
    if normalized in {"perp", "swap", "futures", "usdt_futures", MARKET_PERP}:
        return MARKET_PERP
    return None


def _opportunity_market_for_context(
    market_type: str,
    analysis_context: dict | None,
) -> str:
    physical_market = _physical_market_type(market_type) or market_type
    if (analysis_context or {}).get("kind") == "stock_linked_perp":
        return physical_market
    return instrument_market_to_db_market(physical_market)


def _parse_time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _latest_exact_market_fact(
    *,
    venue: str,
    symbol: str,
    market_type: str,
    now: datetime,
) -> dict | None:
    physical_market = _physical_market_type(market_type)
    if not venue or not symbol or not physical_market:
        return None
    rows = repo.list_market_snapshots(
        symbol=symbol,
        exchange=venue,
        market_type=physical_market,
        limit=1,
    )
    if not rows:
        return None
    row = rows[0]
    observed_at = _parse_time(row.get("snapshot_at"))
    if observed_at is None or observed_at < now - timedelta(hours=2):
        return None
    try:
        price = float(row.get("price"))
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None
    return {**row, "price": price, "observed_at": observed_at.isoformat()}


def has_fresh_market_snapshot(*args, **kwargs) -> bool:
    from aletheia.universe.snapshot_freshness import has_fresh_market_snapshot as check

    return check(*args, **kwargs)


def _feature_market_fact(
    *,
    member: dict,
    feature: dict,
    setup: UniverseSetup,
    now: datetime,
) -> dict | None:
    try:
        price = float(feature.get("last_price") or member.get("entry_price_usd"))
    except (TypeError, ValueError):
        return None
    if price <= 0 or str(feature.get("venue") or member.get("venue") or "") != setup.venue:
        return None
    return {
        "id": feature.get("feature_snapshot_id") or f"universe_feature:{setup.member_id}",
        "price": price,
        "observed_at": now.isoformat(),
        "source": "universe_feature",
    }


def _trigger_evidence_manifest(
    *,
    setup: UniverseSetup,
    member: dict,
    feature: dict,
    market_fact: dict,
    total_score: float,
) -> dict:
    reasons = [str(reason)[:160] for reason in (feature.get("reasons") or [])[:5]]
    return {
        "version": _OPPORTUNITY_EVIDENCE_VERSION,
        "policy_epoch": POLICY_EPOCH_LABEL,
        "t0": {
            "status": "available",
            "trigger_type": "universe_scanner",
            "entry_trigger_type": "entry_eligibility_transition",
            "setup_id": setup.id,
            "universe_snapshot_id": setup.snapshot_id,
            "universe_member_id": setup.member_id,
            "feature_snapshot_id": feature.get("feature_snapshot_id"),
            "market_snapshot_id": market_fact.get("id"),
            "market_observed_at": market_fact.get("observed_at"),
            "venue": setup.venue,
            "symbol": setup.symbol,
            "market_type": setup.market_type,
            "scanner_family": setup.scanner_family,
            "direction": setup.direction,
            "material_fingerprint": setup.material_fingerprint,
            "total_score": total_score,
            "entry_price_usd": market_fact.get("price"),
            "valid_until": setup.valid_until,
            "reasons": reasons,
        },
        "t1": {"status": "unavailable"},
    }


def _build_setup(
    *,
    member: dict,
    feature: dict,
    snapshot_id: str,
    member_index: int,
    symbol: str,
    market_type: str,
    family: str,
    direction: str,
    material_fingerprint: str,
    regime_tag: str | None,
) -> UniverseSetup:
    venue = str(feature.get("venue") or member.get("venue") or "")
    valid_until = _parse_time(member.get("expires_at"))
    if valid_until is None:
        raise ValueError("candidate_valid_until_missing")
    motive = {
        "universe_snapshot_id": snapshot_id,
        "universe_member_id": member.get("id"),
        "feature_snapshot_id": feature.get("feature_snapshot_id"),
        "total_score": member.get("total_score"),
        "reasons": [str(reason)[:160] for reason in (feature.get("reasons") or [])[:5]],
        "regime": regime_tag,
    }
    try:
        compatibility_price = float(feature.get("last_price") or member.get("entry_price_usd") or 1.0)
    except (TypeError, ValueError):
        compatibility_price = 1.0
    compatibility_price = compatibility_price if compatibility_price > 0 else 1.0
    return UniverseSetup(
        snapshot_id=snapshot_id,
        member_id=str(member.get("id") or ""),
        asset_id=str(feature.get("asset_id") or member.get("asset_id") or ""),
        venue=venue,
        symbol=symbol,
        market_type=_physical_market_type(market_type) or market_type,
        direction=direction,
        rank=int(member.get("rank") or member_index + 1),
        material_fingerprint=material_fingerprint,
        trigger_spec_hash=_sha({
            "version": ENTRY_ELIGIBILITY_VERSION,
            "family": family,
            "direction": direction,
        }),
        trigger_spec_version=ENTRY_ELIGIBILITY_VERSION,
        scanner_family=family,
        motive_evidence=_json(motive),
        funding_rate=feature.get("funding_rate"),
        funding_observed_at=datetime.now(timezone.utc).isoformat()
        if feature.get("funding_rate") is not None else None,
        regime_tag=regime_tag,
        breakout_level=compatibility_price,
        atr=1.0,
        pullback_band="[]",
        entry_zone="[]",
        invalidation_price=compatibility_price,
        state="armed",
        valid_until=valid_until.isoformat(),
    )


def _materialize_trigger_opportunity(
    *,
    setup: UniverseSetup,
    member: dict,
    feature: dict,
    market_fact: dict,
    linked_strategy_id: str | None,
    total_score: float,
    instrument_background: dict,
    analysis_context: dict | None,
) -> Opportunity:
    from aletheia.universe.features import persist_feature_snapshot_for_emission

    persist_feature_snapshot_for_emission(feature)
    price = float(market_fact["price"])
    entry_zone = (price, price)
    detected_at = datetime.now(timezone.utc).isoformat()
    valid_until = setup.valid_until
    physical_market = _physical_market_type(setup.market_type) or setup.market_type
    db_market = _opportunity_market_for_context(physical_market, analysis_context)
    family = setup.scanner_family
    reasons: list[str] = feature.get("reasons") or []
    thesis = (
        f"[{family}] {'; '.join(reasons)}" if reasons
        else f"[{family}] score={total_score:.0f}"
    )
    confidence = "high" if total_score >= _HIGH_CONF_THRESHOLD else "medium"
    if family == "derivatives" and feature.get("is_proxy"):
        confidence = "medium"
    generation = OpportunityGenerationV2(
        instrument_identity=OpportunityInstrumentIdentity(
            venue=setup.venue,
            symbol=setup.symbol,
            market_type=physical_market,
        ),
        family=family,
        archetype=_FAMILY_ARCHETYPE.get(family, "continuation"),
        direction=setup.direction,
        detected_at=detected_at,
        signal_mark=price,
        motive_evidence=json.loads(setup.motive_evidence or "{}"),
        entry_trigger={
            "type": "entry_eligibility_transition",
            "from": "false",
            "to": "true",
            "behavior_version": ENTRY_TRIGGER_BEHAVIOR_VERSION,
            "market_snapshot_id": market_fact.get("id"),
            "market_observed_at": market_fact.get("observed_at"),
            "idempotency_key": _sha({
                "setup_id": setup.id,
                "behavior_version": ENTRY_TRIGGER_BEHAVIOR_VERSION,
                "market_snapshot_id": market_fact.get("id"),
            }),
        },
        entry_zone=entry_zone,
        invalidation={
            "type": "market_validity",
            "conditions": [
                "valid_until_elapsed",
                "material_fingerprint_superseded",
                "exact_market_fact_unavailable",
            ],
        },
        horizon_hours=_DEFAULT_HORIZON_HOURS,
        cost_snapshot={
            "cost_model_version": "universe-entry-eligibility/v1",
            "spread_bps": feature.get("spread_bps") or member.get("spread_bps"),
            "estimated_slippage_bps": feature.get("estimated_cost")
            or member.get("estimated_cost"),
            "funding": (
                {"status": "not_applicable"}
                if physical_market == MARKET_SPOT
                else {"status": "observed", "rate": feature.get("funding_rate")}
                if feature.get("funding_rate") is not None
                else {"status": "unavailable"}
            ),
            "depth_10bps_usd": feature.get("depth_10bps_usd"),
            "target_notional_usd": None,
            "capacity_ratio": None,
        },
        material_fingerprint=setup.material_fingerprint,
        valid_until=valid_until,
    )
    evidence_manifest = _trigger_evidence_manifest(
        setup=setup,
        member=member,
        feature=feature,
        market_fact=market_fact,
        total_score=total_score,
    )
    opportunity = Opportunity(
        symbol=setup.symbol,
        market=db_market,
        direction_bias=setup.direction,
        thesis=thesis,
        confidence=confidence,
        confidence_score=int(round(total_score)),
        source_agent="universe_scanner",
        linked_strategy_id=linked_strategy_id,
        asset_class=str(instrument_background.get("asset_class") or "crypto"),
        asset_subclass=(
            "stock_linked_perp"
            if (analysis_context or {}).get("kind") == "stock_linked_perp"
            else ""
        ),
        metadata=_json({
            "instrument_background": instrument_background,
            "analysis_context": analysis_context,
            "learning_candidate_key": member.get("learning_candidate_key"),
            "learning_exposure_id": member.get("learning_exposure_id"),
            "universe_snapshot_id": setup.snapshot_id,
            "universe_member_id": setup.member_id,
            "feature_snapshot_id": feature.get("feature_snapshot_id"),
            "material_fingerprint": setup.material_fingerprint,
            "trigger_behavior_version": ENTRY_TRIGGER_BEHAVIOR_VERSION,
            "market_snapshot_id": market_fact.get("id"),
            "evidence_manifest": evidence_manifest,
        }),
        execution_eligible=True,
        universe_snapshot_id=setup.snapshot_id,
        universe_member_id=setup.member_id,
        scanner_family=family,
        valid_until=valid_until,
        entry_zone=json.dumps(list(entry_zone), separators=(",", ":")),
        regime_tag=setup.regime_tag,
        funding_rate=feature.get("funding_rate"),
        open_interest=feature.get("open_interest"),
        oi_delta_1h=feature.get("oi_delta_1h"),
        setup_id=setup.id,
        signal_mark=str(price),
        trigger_behavior_version=ENTRY_TRIGGER_BEHAVIOR_VERSION,
    )
    return repo.insert_opportunity_generation(opportunity, generation)


def _active_defer_suppresses(
    *,
    symbol: str,
    direction: str,
    opportunity_market: str,
    material_fingerprint: str,
) -> dict | None:
    prior_skip = repo.latest_direct_skip_defer_for_setup(
        symbol,
        direction,
        opportunity_market,
    )
    if prior_skip and prior_skip.get("material_fingerprint") == material_fingerprint:
        return prior_skip
    return None


def _opportunity_material_fingerprint(opportunity: dict) -> str | None:
    try:
        metadata = json.loads(opportunity.get("metadata") or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        metadata = {}
    value = metadata.get("material_fingerprint") if isinstance(metadata, dict) else None
    if value:
        return str(value)
    try:
        generation = json.loads(opportunity.get("generation_json") or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    value = generation.get("material_fingerprint") if isinstance(generation, dict) else None
    return str(value) if value else None


class EmissionEvidenceError(RuntimeError):
    """A selected member could not be durably accounted for this cycle."""


def _record_emission_decision(
    *,
    member: dict,
    snapshot_id: str,
    member_index: int,
    symbol: str,
    market_type: str,
    total_score: float | None,
    short_enabled: bool,
    decision: str,
    reason_code: str,
    direction: str | None = None,
    opportunity_id: str | None = None,
    regime_tag: str | None = None,
    reason_detail: str | None = None,
    inputs: dict | None = None,
) -> None:
    """Persist one terminal fact for a selected universe member or fail retryably.

    Exact instrument and entry facts seed fixed-horizon evidence when available.
    This does not create an Opportunity or alter the emission decision.
    """
    try:
        from aletheia.analytics.filter_evidence import record_filter_decision

        member_id = str(member.get("id") or "")
        candidate_key = (
            f"universe_member:{member_id}"
            if member_id
            else f"universe_member_missing:{snapshot_id}:{member_index}"
        )
        input_snapshot = {
            "total_score": total_score,
            "market_type": market_type,
        }
        input_snapshot.update(inputs or {})
        evidence_id = record_filter_decision(
            candidate_key=candidate_key,
            candidate_kind="universe_member",
            parent_candidate_key=f"universe_snapshot:{snapshot_id}",
            filter_name="opportunity_emission",
            stage="signal_emitter",
            sequence_no=0,
            decision=decision,
            reason_code=reason_code,
            reason_detail=reason_detail,
            symbol=symbol or "[missing]",
            market_type=market_type,
            direction=direction,
            scanner_family=member.get("scanner_family"),
            regime_tag=regime_tag,
            opportunity_id=opportunity_id,
            universe_snapshot_id=snapshot_id,
            universe_member_id=member_id or None,
            venue=member.get("venue"),
            entry_price_usd=member.get("entry_price_usd"),
            entry_spread_bps=member.get("spread_bps"),
            funding_rate_at_entry=member.get("funding_rate"),
            outcome_candidate_key=candidate_key,
            inputs=input_snapshot,
            parameters={
                "min_score": _MIN_SCORE,
                "high_confidence_threshold": _HIGH_CONF_THRESHOLD,
                "regime_bias_threshold": _REGIME_BIAS_THRESHOLD,
                "short_enabled": short_enabled,
            },
            raise_on_error=True,
        )
        from aletheia.storage.sqlite import get_db

        if get_db().execute(
            "SELECT 1 FROM filter_decisions WHERE id = ?", (evidence_id,)
        ).fetchone() is None:
            raise RuntimeError("opportunity_emission_evidence_not_persisted")
    except Exception as exc:
        member_key = str(member.get("id") or f"missing-{member_index}")
        error_code = (
            f"opportunity_emission_evidence_failed:{snapshot_id}:{member_key}"
        )
        logger.error("%s (%s)", error_code, type(exc).__name__)
        # Universe members are already durable at this boundary. Propagating the
        # stable failure leaves that source input intact for retry instead of
        # returning a misleading zero-created success.
        raise EmissionEvidenceError(error_code) from exc


def emit_deterministic_opportunities(
    members: list[dict],
    snapshot_id: str,
    scored_features: list[dict],
    linked_strategy_id: str | None = None,
    regime=None,
    short_enabled: bool = True,
    allow_open_replacement: bool = False,
) -> int:
    """Publish derived handoff facts for selected Universe Candidates.

    Args:
        members: inserted_members list from service.refresh() — each has id, symbol,
                 snapshot_id, scanner_family, total_score, expires_at.
        snapshot_id: active universe snapshot id.
        scored_features: the `scored` list from refresh — carries scanner reasons +
                         raw feature values (return_1h, rsi_14, funding_rate, …).
        regime: MarketRegime instance (or None). When provided, signals whose
                direction conflicts with regime bias are filtered out.
        linked_strategy_id: compatibility argument; Universe refresh no longer
                attaches strategies directly to an Opportunity.
        allow_open_replacement: compatibility argument retained for old callers.
                Queued carriers remain protected; standalone callers retain the
                cross-snapshot anti-flood guard.

    Returns:
        Number of Opportunity rows created from canonical setup EntryEligibility
        transitions. Candidate rows can still publish only an armed Setup when
        exact current market facts are missing.
    """
    from aletheia.learning.opportunity_information_policy import (
        ensure_opportunity_information_policy_epoch,
    )

    ensure_opportunity_information_policy_epoch()
    # Key by (symbol, market_type): the same symbol can appear as both spot and
    # perp in one refresh. Keying by symbol alone let one overwrite the other, so
    # an emitted opportunity could inherit funding/direction from the wrong market.
    feat_by_key: dict[tuple, dict] = {
        (f["symbol"], f.get("market_type", "spot")): f for f in scored_features
    }
    created = 0

    from aletheia.agent_shell.trading_context import (
        material_fingerprint_from_features as _material_fingerprint,
    )

    for member_index, m in enumerate(members):
        symbol = m.get("symbol", "")
        member_id = m.get("id")
        family = m.get("scanner_family")
        market_type = m.get("market_type", "spot") or "spot"
        raw_total_score = m.get("total_score")
        try:
            total_score = float(raw_total_score or 0)
        except (TypeError, ValueError):
            _record_emission_decision(
                member=m,
                snapshot_id=snapshot_id,
                member_index=member_index,
                symbol=symbol,
                market_type=market_type,
                total_score=None,
                short_enabled=short_enabled,
                decision="error",
                reason_code="invalid_total_score",
                inputs={"raw_total_score_type": type(raw_total_score).__name__},
            )
            continue

        if not symbol or not member_id or not family:
            _record_emission_decision(
                member=m,
                snapshot_id=snapshot_id,
                member_index=member_index,
                symbol=symbol,
                market_type=market_type,
                total_score=total_score,
                short_enabled=short_enabled,
                decision="error",
                reason_code="member_identity_missing",
                inputs={
                    "has_symbol": bool(symbol),
                    "has_member_id": bool(member_id),
                    "has_scanner_family": bool(family),
                },
            )
            continue
        if total_score < _MIN_SCORE:
            _record_emission_decision(
                member=m,
                snapshot_id=snapshot_id,
                member_index=member_index,
                symbol=symbol,
                market_type=market_type,
                total_score=total_score,
                short_enabled=short_enabled,
                decision="rejected",
                reason_code="score_below_minimum",
            )
            continue

        feat = feat_by_key.get((symbol, market_type))
        if not feat:
            _record_emission_decision(
                member=m,
                snapshot_id=snapshot_id,
                member_index=member_index,
                symbol=symbol,
                market_type=market_type,
                total_score=total_score,
                short_enabled=short_enabled,
                decision="rejected",
                reason_code="feature_missing",
            )
            continue

        instrument_background = feat.get("instrument_background")
        background_reason = instrument_background_rejection_reason(
            instrument_background,
            symbol=symbol,
            market_type=market_type,
            venue=str(
                feat.get("venue")
                or m.get("venue")
                or (instrument_background or {}).get("venue")
                or ""
            ),
        )
        if (
            not background_reason
            and m.get("instrument_background") is not None
            and m.get("instrument_background") != instrument_background
        ):
            background_reason = "instrument_background_identity_conflict"
        if background_reason:
            _record_emission_decision(
                member=m,
                snapshot_id=snapshot_id,
                member_index=member_index,
                symbol=symbol,
                market_type=market_type,
                total_score=total_score,
                short_enabled=short_enabled,
                decision="rejected",
                reason_code=background_reason,
                inputs={
                    "background_status": (
                        instrument_background.get("status")
                        if isinstance(instrument_background, dict) else None
                    ),
                    "asset_class": (
                        instrument_background.get("asset_class")
                        if isinstance(instrument_background, dict) else None
                    ),
                },
            )
            continue
        analysis_context = instrument_analysis_context(instrument_background)
        applicable_regime = (
            regime if (analysis_context or {}).get("kind") == "crypto" else None
        )

        # Prefer scanner-intrinsic direction; fallback to _infer_direction
        direction = feat.get("scanner_direction") or _infer_direction(family, feat)
        if direction is None:
            counterfactual_direction = _funding_conflict_direction(family, feat)
            reason_code = (
                "funding_crowding_conflict"
                if counterfactual_direction
                else "direction_missing"
            )
            logger.warning(
                "no direction for %s family=%s score=%.0f — skipped",
                symbol, family, total_score,
            )
            _record_emission_decision(
                member=m,
                snapshot_id=snapshot_id,
                member_index=member_index,
                symbol=symbol,
                market_type=market_type,
                total_score=total_score,
                short_enabled=short_enabled,
                decision="rejected",
                reason_code=reason_code,
                direction=counterfactual_direction,
                inputs={"scanner_direction": feat.get("scanner_direction")},
            )
            continue

        if (analysis_context or {}).get("kind") == "crypto":
            # Factor feedback gate: block signals from proven-bad factor combinations
            try:
                factor_sig = lookup_factor_signal({
                    "scanner_family": family,
                    "direction": direction,
                    "symbol": symbol,
                })
                if factor_sig.get("status") == "blocked_candidate":
                    logger.debug(
                        "factor-blocked %s %s %s (wr=%.0f%% exp=%.4f)",
                        family, direction, symbol,
                        (factor_sig.get("win_rate") or 0) * 100,
                        factor_sig.get("expectancy") or 0,
                    )
                    _record_emission_decision(
                        member=m,
                        snapshot_id=snapshot_id,
                        member_index=member_index,
                        symbol=symbol,
                        market_type=market_type,
                        total_score=total_score,
                        short_enabled=short_enabled,
                        decision="rejected",
                        reason_code="factor_blocked",
                        direction=direction,
                        inputs={"factor_status": factor_sig.get("status")},
                    )
                    continue
            except Exception:
                pass

        # Regime bias gate: filter signals that conflict with current regime
        if not _passes_regime_gate(
            direction, total_score, applicable_regime, short_enabled
        ):
            reason_code = (
                "short_disabled"
                if direction == "short" and not short_enabled
                else "regime_filtered"
            )
            logger.debug(
                "regime-filtered %s %s %s (regime=%s)",
                family, direction, symbol,
                applicable_regime.state if applicable_regime else None,
            )
            _record_emission_decision(
                member=m,
                snapshot_id=snapshot_id,
                member_index=member_index,
                symbol=symbol,
                market_type=market_type,
                total_score=total_score,
                short_enabled=short_enabled,
                decision="rejected",
                reason_code=reason_code,
                direction=direction,
                regime_tag=applicable_regime.state if applicable_regime else None,
                inputs={
                    "direction_bias": (
                        applicable_regime.short_bias
                        if applicable_regime and direction == "short"
                        else applicable_regime.long_bias
                        if applicable_regime else None
                    ),
                },
            )
            continue

        current_material_fingerprint = _material_fingerprint(
            symbol=symbol,
            direction=direction,
            market_type=market_type,
            scanner_family=family,
            features=feat,
            regime=applicable_regime.state if applicable_regime else None,
        )
        opportunity_market = _opportunity_market_for_context(
            market_type,
            analysis_context,
        )

        try:
            setup = _build_setup(
                member=m,
                feature=feat,
                snapshot_id=snapshot_id,
                member_index=member_index,
                symbol=symbol,
                market_type=market_type,
                family=family,
                direction=direction,
                material_fingerprint=current_material_fingerprint,
                regime_tag=applicable_regime.state if applicable_regime else None,
            )
        except ValueError as exc:
            _record_emission_decision(
                member=m,
                snapshot_id=snapshot_id,
                member_index=member_index,
                symbol=symbol,
                market_type=market_type,
                total_score=total_score,
                short_enabled=short_enabled,
                decision="rejected",
                reason_code=str(exc)[:160] or "setup_unavailable",
                direction=direction,
                regime_tag=applicable_regime.state if applicable_regime else None,
            )
            continue
        current = datetime.now(timezone.utc)
        market_fact = _latest_exact_market_fact(
            venue=setup.venue,
            symbol=symbol,
            market_type=market_type,
            now=current,
        )
        if market_fact is None and has_fresh_market_snapshot(
            symbol, market_type, now=current
        ):
            market_fact = _feature_market_fact(
                member=m,
                feature=feat,
                setup=setup,
                now=current,
            )
        entry_eligible = bool(m.get("execution_eligible", True)) and market_fact is not None
        defer = _active_defer_suppresses(
            symbol=symbol,
            direction=direction,
            opportunity_market=opportunity_market,
            material_fingerprint=current_material_fingerprint,
        )
        existing_snapshot_owner = repo.find_opportunity_by_symbol_snapshot(
            symbol, snapshot_id, market=opportunity_market,
        )
        live_dup = repo.find_live_opportunity_by_symbol_direction(
            symbol, direction, market=opportunity_market,
        )
        live_material = (
            _opportunity_material_fingerprint(live_dup) if live_dup else None
        )
        live_duplicate_suppressed = bool(
            live_dup
            and (not live_material or live_material == current_material_fingerprint)
            and not (
                live_dup.get("status") == "open" and allow_open_replacement
            )
        )

        with repo.transaction(immediate=True):
            owner = repo.insert_universe_setup(setup)
            owner_state = str(owner.get("state") or "")
            setup_id = str(owner["id"])
            setup_model = UniverseSetup.model_validate(owner)
            if existing_snapshot_owner:
                _record_emission_decision(
                    member=m,
                    snapshot_id=snapshot_id,
                    member_index=member_index,
                    symbol=symbol,
                    market_type=market_type,
                    total_score=total_score,
                    short_enabled=short_enabled,
                    decision="passed",
                    reason_code="existing_opportunity_reused",
                    direction=direction,
                    opportunity_id=existing_snapshot_owner.get("id"),
                    regime_tag=applicable_regime.state if applicable_regime else None,
                    inputs={
                        "setup_id": setup_id,
                        "existing_status": existing_snapshot_owner.get("status"),
                    },
                )
                continue
            if defer:
                _record_emission_decision(
                    member=m,
                    snapshot_id=snapshot_id,
                    member_index=member_index,
                    symbol=symbol,
                    market_type=market_type,
                    total_score=total_score,
                    short_enabled=short_enabled,
                    decision="rejected",
                    reason_code="active_defer_suppressed",
                    direction=direction,
                    regime_tag=applicable_regime.state if applicable_regime else None,
                    inputs={
                        "setup_id": setup_id,
                        "material_fingerprint": current_material_fingerprint,
                        "prior_defer_opportunity_id": defer.get("opportunity_id"),
                    },
                )
                continue
            if live_duplicate_suppressed:
                _record_emission_decision(
                    member=m,
                    snapshot_id=snapshot_id,
                    member_index=member_index,
                    symbol=symbol,
                    market_type=market_type,
                    total_score=total_score,
                    short_enabled=short_enabled,
                    decision="rejected",
                    reason_code="live_duplicate_suppressed",
                    direction=direction,
                    regime_tag=applicable_regime.state if applicable_regime else None,
                    inputs={
                        "setup_id": setup_id,
                        "live_opportunity_id": live_dup.get("id") if live_dup else None,
                        "live_material_fingerprint": live_material,
                        "material_fingerprint": current_material_fingerprint,
                    },
                )
                continue
            if not entry_eligible:
                _record_emission_decision(
                    member=m,
                    snapshot_id=snapshot_id,
                    member_index=member_index,
                    symbol=symbol,
                    market_type=market_type,
                    total_score=total_score,
                    short_enabled=short_enabled,
                    decision="passed",
                    reason_code="setup_armed_entry_not_eligible",
                    direction=direction,
                    regime_tag=applicable_regime.state if applicable_regime else None,
                    inputs={
                        "setup_id": setup_id,
                        "setup_state": owner_state,
                        "has_exact_market_fact": market_fact is not None,
                        "execution_eligible": bool(m.get("execution_eligible", True)),
                    },
                )
                continue
            existing_for_setup = repo.get_opportunity_by_setup(setup_id)
            if existing_for_setup:
                _record_emission_decision(
                    member=m,
                    snapshot_id=snapshot_id,
                    member_index=member_index,
                    symbol=symbol,
                    market_type=market_type,
                    total_score=total_score,
                    short_enabled=short_enabled,
                    decision="passed",
                    reason_code="triggered_opportunity_reused",
                    direction=direction,
                    opportunity_id=existing_for_setup.get("id"),
                    regime_tag=applicable_regime.state if applicable_regime else None,
                    inputs={"setup_id": setup_id, "setup_state": owner_state},
                )
                continue
            if not repo.transition_universe_setup(
                setup_id,
                expected_states=("armed",),
                state="triggering",
            ):
                _record_emission_decision(
                    member=m,
                    snapshot_id=snapshot_id,
                    member_index=member_index,
                    symbol=symbol,
                    market_type=market_type,
                    total_score=total_score,
                    short_enabled=short_enabled,
                    decision="passed",
                    reason_code="trigger_transition_reused",
                    direction=direction,
                    regime_tag=applicable_regime.state if applicable_regime else None,
                    inputs={"setup_id": setup_id, "setup_state": owner_state},
                )
                continue
            opportunity = _materialize_trigger_opportunity(
                setup=setup_model.model_copy(update={"state": "triggering"}),
                member=m,
                feature=feat,
                market_fact=market_fact,
                linked_strategy_id=linked_strategy_id,
                total_score=total_score,
                instrument_background=instrument_background,
                analysis_context=analysis_context,
            )
            if not repo.transition_universe_setup(
                setup_id,
                expected_states=("triggering",),
                state="executed",
                opportunity_id=opportunity.id,
            ):
                raise RuntimeError("universe_trigger_setup_projection_conflict")
            created += 1
            _record_emission_decision(
                member=m,
                snapshot_id=snapshot_id,
                member_index=member_index,
                symbol=symbol,
                market_type=market_type,
                total_score=total_score,
                short_enabled=short_enabled,
                decision="passed",
                reason_code="triggered_opportunity_created",
                direction=direction,
                opportunity_id=opportunity.id,
                regime_tag=applicable_regime.state if applicable_regime else None,
                inputs={
                    "setup_id": setup_id,
                    "trigger_behavior_version": ENTRY_TRIGGER_BEHAVIOR_VERSION,
                    "market_snapshot_id": market_fact.get("id"),
                },
            )

    if created:
        logger.info(
            "Universe signal emitter: %d deterministic opportunities (snapshot=%s)",
            created, snapshot_id[:8],
        )

    return created


def _passes_regime_gate(direction: str, total_score: float, regime, short_enabled: bool = True) -> bool:
    """Check if a signal's direction passes the regime bias gate.

    Logic:
      - Get the bias for this direction (long_bias or short_bias)
      - If bias >= threshold, always pass
      - If bias < threshold, only pass if score is exceptionally high
        This allows strong contrarian signals to survive even in hostile regimes.

    US-9: short_enabled=False is a HARD operator switch — no score may override it.
    Previously a high-score short slipped through the score-override branch below,
    silently violating the operator's global short disable.
    """
    if direction == "short" and not short_enabled:
        return False
    if regime is None:
        return True

    bias = regime.long_bias if direction == "long" else regime.short_bias

    if bias >= _REGIME_BIAS_THRESHOLD:
        return True

    # Strong signals can override weak regime alignment
    if total_score >= _HIGH_CONF_THRESHOLD * 1.1:
        return True

    return False


def _funding_conflicts(direction: str, funding: float | None) -> bool:
    """Check if funding rate conflicts with proposed direction.

    Crowded longs (high positive funding) conflict with long direction;
    crowded shorts (extreme negative funding) conflict with short direction.
    """
    if funding is None:
        return False
    if direction == "long" and funding > CROWDED_FUNDING_RATE:
        return True
    if direction == "short" and funding < -CROWDED_FUNDING_RATE:
        return True
    return False


def _funding_conflict_direction(family: str | None, feat: dict) -> str | None:
    """Return an ex-ante side suppressed only by the funding crowding gate."""
    ret_1h = feat.get("return_1h")
    ret_4h = feat.get("return_4h")
    ret_24h = feat.get("return_24h")
    rsi = feat.get("rsi_14")
    funding = feat.get("funding_rate")

    direction = None
    if family == "momentum":
        if ret_24h is not None and abs(ret_24h) > 10.0:
            return None
        if ret_1h is not None and ret_4h is not None and ret_1h * ret_4h < 0:
            return None
        if ret_4h is not None and ret_4h > 0 and rsi is not None and rsi > 50:
            direction = "long"
        elif ret_4h is not None and ret_4h < 0 and rsi is not None and rsi < 50:
            direction = "short"
    elif family == "volatility" and ret_4h is not None and abs(ret_4h) > 1.0:
        direction = "long" if ret_4h > 0 else "short"

    return direction if direction and _funding_conflicts(direction, funding) else None


def _infer_direction(family: str | None, feat: dict) -> str | None:
    """Deterministically infer trade direction from scanner family + feature values.

    Returns "long", "short", or None (no clear signal — caller must skip).

    Phase 0 improvements:
      - 0.1: Momentum rejects when 24h already overextended (mean-reversion zone)
      - 0.2: Rejects when 1h and 4h directions conflict
      - 0.3: Funding rate confirmation across all families
      - 0.4: Volatility uses 4h direction instead of noisy 1h
    """
    ret_1h = feat.get("return_1h")
    ret_4h = feat.get("return_4h")
    ret_24h = feat.get("return_24h")
    rsi = feat.get("rsi_14")
    funding = feat.get("funding_rate")

    if family == "momentum":
        # 0.1: 24h already overextended → mean-reversion zone, don't chase
        if ret_24h is not None and abs(ret_24h) > 10.0:
            return None

        # 0.2: 1h vs 4h direction conflict → no clear trend
        if ret_1h is not None and ret_4h is not None and ret_1h * ret_4h < 0:
            return None

        # Direction from 4h (more stable than 1h) with RSI confirmation
        direction = None
        if ret_4h is not None and ret_4h > 0:
            if rsi is not None and rsi > 50:
                direction = "long"
        elif ret_4h is not None and ret_4h < 0:
            if rsi is not None and rsi < 50:
                direction = "short"

        # 0.3: Funding conflict → reject
        if direction and _funding_conflicts(direction, funding):
            return None
        return direction

    if family == "mean_reversion":
        # Mean reversion: fade extreme moves — oversold → long, overbought → short
        direction = None
        if rsi is not None:
            if rsi <= 30:
                direction = "long"
            elif rsi >= 70:
                direction = "short"
        if direction is None and ret_4h is not None:
            if ret_4h < -5.0:
                direction = "long"
            elif ret_4h > 5.0:
                direction = "short"

        # For MR, funding CONFIRMS (crowded side = reversion more likely)
        # so we don't filter on funding conflict here
        return direction

    if family == "volatility":
        # 0.4: Use 4h direction (more autocorrelation than 1h noise)
        if ret_4h is not None and abs(ret_4h) > 1.0:
            direction = "long" if ret_4h > 0 else "short"
            # 0.3: Funding conflict → reject
            if _funding_conflicts(direction, funding):
                return None
            return direction
        return None

    if family == "derivatives":
        # Derivatives: fade crowded positions via funding rate signal
        if funding is not None:
            if funding < -ELEVATED_FUNDING_RATE:
                return "long"
            if funding > ELEVATED_FUNDING_RATE:
                return "short"
        # Fall back to 4h momentum if no funding data
        if ret_4h is not None and ret_4h != 0:
            direction = "long" if ret_4h > 0 else "short"
            return direction
        return None

    # NOTE: family == "short_bias" is NOT handled here. It is a first-class
    # scanner family whose direction is emitted intrinsically by
    # scanners.score_short_bias() → run_all_scanners() sets scanner_direction,
    # so _infer_direction is never the source for short_bias members. Keeping a
    # duplicate threshold branch here would risk drift (2026-07-01: the previous
    # duplicate was dead code + a fake-GREEN spec; removed once the scorer landed).

    if family == "event":
        # Event catalyst: follow the sharp 1h displacement. Thresholds mirror
        # score_event (>4% long / <-4% short) so a fallback here agrees with the
        # scanner's intrinsic direction. Only used when scanner_direction is
        # absent — the primary path prefers the scanner's own direction.
        if ret_1h is not None:
            if ret_1h > 4.0:
                return "long"
            if ret_1h < -4.0:
                return "short"
        return None

    return None
