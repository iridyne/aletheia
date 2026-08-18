"""Universe Signal Emitter — deterministic opportunity generation from universe members.

Converts high-scoring universe members into Opportunity rows without requiring LLM.
This decouples market sensing from LLM availability:
  - LLM up:   market_observer enriches the existing opportunity with language/context
  - LLM down: universe_scanner opportunities still flow to paper_trader ranker

Called at the end of every universe refresh cycle (source_agent="universe_scanner").
"""

from __future__ import annotations

from aletheia.universe.funding_units import (
    CROWDED_FUNDING_RATE,
    ELEVATED_FUNDING_RATE,
)

import json
import logging

from aletheia.storage.models import Opportunity
from aletheia.storage.repositories import (
    find_live_opportunity_by_symbol_direction,
    find_opportunity_by_symbol_snapshot,
    insert_opportunity_once_by_snapshot,
)
from aletheia.trading.instruments import instrument_market_to_db_market
from aletheia.analytics.factor_feedback import lookup_factor_signal
from aletheia.analytics.filter_evidence import atomic_opportunity_evidence
from aletheia.universe.snapshot_freshness import has_fresh_market_snapshot
from aletheia.learning.opportunity_information_policy import POLICY_EPOCH_LABEL

logger = logging.getLogger("aletheia.universe.signal_emitter")


def _opportunity_material_fingerprint(opportunity: dict) -> str | None:
    """Read the durable setup identity written by the emitter.

    Legacy rows have no fingerprint. Treating those as an unknown material setup
    keeps the duplicate guard fail-closed until a fresh generation carries the
    explicit identity.
    """
    raw_metadata = opportunity.get("metadata")
    if not raw_metadata:
        return None
    try:
        metadata = json.loads(raw_metadata) if isinstance(raw_metadata, str) else raw_metadata
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    value = metadata.get("material_fingerprint") if isinstance(metadata, dict) else None
    if value:
        return str(value)
    manifest = metadata.get("evidence_manifest") if isinstance(metadata, dict) else None
    t0 = manifest.get("t0") if isinstance(manifest, dict) else None
    return str(t0.get("material_fingerprint")) if isinstance(t0, dict) and t0.get("material_fingerprint") else None


# Only emit for members that scored high enough to be worth trading
_MIN_SCORE = 45.0
# total_score threshold for "high" vs "medium" confidence label.
# The allocator's score ceiling is ~68.5 (opportunity 0.35×100 + tradability
# 0.25×100 + liquidity 0.15×90 − min cost 5.0), so a 70 threshold made "high"
# unreachable — every emitted opportunity was forced to "medium". 60 keeps a
# meaningful split: medium = [45, 60), high = [60, ceiling].
_HIGH_CONF_THRESHOLD = 60.0

# Regime bias threshold: if the direction's bias < this value, skip signal
# unless score compensates (higher score can override weak regime alignment).
_REGIME_BIAS_THRESHOLD = 0.30
_OPPORTUNITY_EVIDENCE_VERSION = "opportunity-information-integrity-v1"


def _trigger_evidence_manifest(
    *,
    member: dict,
    feature: dict,
    snapshot_id: str,
    direction: str,
    material_fingerprint: str | None = None,
) -> dict:
    """Capture the bounded scanner trigger before any Observer enrichment."""
    reasons = [str(reason)[:160] for reason in (feature.get("reasons") or [])[:5]]
    return {
        "version": _OPPORTUNITY_EVIDENCE_VERSION,
        "policy_epoch": POLICY_EPOCH_LABEL,
        "t0": {
            "status": "available",
            "trigger_type": "universe_scanner",
            "universe_snapshot_id": snapshot_id,
            "universe_member_id": member.get("id"),
            "feature_snapshot_id": feature.get("feature_snapshot_id"),
            "symbol": member.get("symbol"),
            "market_type": member.get("market_type"),
            "scanner_family": member.get("scanner_family"),
            "direction": direction,
            "material_fingerprint": material_fingerprint,
            "total_score": member.get("total_score"),
            "entry_price_usd": member.get("entry_price_usd"),
            "valid_until": member.get("expires_at"),
            "reasons": reasons,
        },
        "t1": {"status": "unavailable"},
    }


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
    """Generate one deterministic Opportunity per eligible high-scoring universe member.

    Args:
        members: inserted_members list from service.refresh() — each has id, symbol,
                 snapshot_id, scanner_family, total_score, expires_at.
        snapshot_id: active universe snapshot id.
        scored_features: the `scored` list from refresh — carries scanner reasons +
                         raw feature values (return_1h, rsi_14, funding_rate, …).
        linked_strategy_id: optional strategy to attach.
        regime: MarketRegime instance (or None). When provided, signals whose
                direction conflicts with regime bias are filtered out.
        allow_open_replacement: publication callers may replace an open
                analytical generation while its parent snapshot is still active.
                Queued carriers remain protected; standalone callers retain the
                cross-snapshot anti-flood guard.

    Returns:
        Number of opportunities created.
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

    # #321 emit-source active-defer suppression (flag-gated; helpers hoisted once
    # so the per-member loop pays nothing when the flag is off). If the latest
    # Operator judgment for a setup was a direct_skip defer at the SAME material
    # fingerprint, universe refresh must not mint yet another fresh-id carrier
    # that re-burns an LLM re-eval. A real market move flips the fingerprint and
    # this gate falls through to normal emission (DL-INV-23).
    _emit_defer_suppression = False
    try:
        from aletheia.agent_shell.skip_policy import skip_policy_context

        _emit_defer_suppression = bool(
            skip_policy_context().get("emit_active_defer_suppression")
        )
    except Exception:
        _emit_defer_suppression = False
    if _emit_defer_suppression:
        from aletheia.agent_shell.trading_context import (
            material_fingerprint_from_features as _emit_material_fp,
            _normalize_direction as _emit_norm_dir,
            _normalize_market_type as _emit_norm_mkt,
        )
        from aletheia.storage.repositories import (
            latest_direct_skip_defer_for_setup as _emit_latest_skip,
        )

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
        if regime is not None and not _passes_regime_gate(direction, total_score, regime, short_enabled):
            logger.debug(
                "regime-filtered %s %s %s (regime=%s, bias=%.2f)",
                family, direction, symbol, regime.state,
                regime.short_bias if direction == "short" else regime.long_bias,
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
                reason_code="regime_filtered",
                direction=direction,
                regime_tag=regime.state,
                inputs={
                    "direction_bias": (
                        regime.short_bias if direction == "short" else regime.long_bias
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
            regime=regime.state if regime else None,
        )

        # Per-snapshot identity is authoritative before any cross-snapshot
        # suppression. A retry for the same selected member must reuse the
        # existing lifecycle row, regardless of another live carrier.
        db_market = instrument_market_to_db_market(market_type)
        existing = find_opportunity_by_symbol_snapshot(
            symbol, snapshot_id,
            market=db_market,
        )
        if existing:
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
                opportunity_id=existing.get("id"),
                regime_tag=regime.state if regime else None,
                inputs={"existing_status": existing.get("status")},
            )
            continue

        # Cross-snapshot live-duplicate guard (#326). An open row may be replaced
        # only inside the atomic publication that is about to supersede its parent
        # snapshot; otherwise it remains the anti-flood owner. Queued carriers are
        # always protected for an unchanged material setup, while a material change
        # gets a new generation without mutating the older queue carrier.
        live_dup = find_live_opportunity_by_symbol_direction(
            symbol, direction, market=db_market,
        )
        if live_dup:
            live_material_fingerprint = _opportunity_material_fingerprint(live_dup)
            same_material = (
                not live_material_fingerprint
                or live_material_fingerprint == current_material_fingerprint
            )
            live_status = str(live_dup.get("status") or "")
            suppress_live_duplicate = (
                same_material
                and not (live_status == "open" and allow_open_replacement)
            )
            if suppress_live_duplicate:
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
                    regime_tag=regime.state if regime else None,
                    inputs={
                        "live_opportunity_id": live_dup.get("id"),
                        "live_opportunity_status": live_status,
                        "live_valid_until": live_dup.get("valid_until"),
                        "material_fingerprint": current_material_fingerprint,
                        "live_material_fingerprint": live_material_fingerprint,
                        "material_match": same_material,
                    },
                )
                continue

        # Emit-source active-defer suppression (#321). See the hoisted setup above.
        if _emit_defer_suppression:
            _norm_dir = _emit_norm_dir(direction)
            _norm_mkt = _emit_norm_mkt(market_type)
            prior_skip = _emit_latest_skip(symbol, _norm_dir, _norm_mkt)
            stored_fp = (prior_skip or {}).get("material_fingerprint")
            if stored_fp:
                current_fp = _emit_material_fp(
                    symbol=symbol,
                    direction=direction,
                    market_type=market_type,
                    scanner_family=family,
                    features=feat,
                    regime=regime.state if regime else None,
                )
                if current_fp == stored_fp:
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
                        regime_tag=regime.state if regime else None,
                        inputs={
                            "material_fingerprint": current_fp,
                            "prior_defer_opportunity_id": (
                                (prior_skip or {}).get("opportunity_id")
                            ),
                        },
                    )
                    continue

        # Feature retention may reuse a <30-minute row during ordinary refreshes.
        # A newly emitted Opportunity is a new evidence boundary, so its T0 must
        # reference the exact calculation that selected this member.
        from aletheia.universe.features import persist_feature_snapshot_for_emission

        persist_feature_snapshot_for_emission(feat)

        confidence = "high" if total_score >= _HIGH_CONF_THRESHOLD else "medium"
        # Proxy derivatives signals (RSI fallback, no funding/OI data) are weaker —
        # cap at medium to avoid triggering strategy_designer high-conf events.
        if family == "derivatives" and feat.get("is_proxy"):
            confidence = "medium"
        reasons: list[str] = feat.get("reasons") or []
        thesis = (
            f"[{family}] {'; '.join(reasons)}" if reasons
            else f"[{family}] score={total_score:.0f}"
        )

        # Guard: only set execution_eligible if there is live price data within the
        # last 2 hours. Symbols with zero snapshots would otherwise reach PI Agent as
        # tradeable but fail silently at proposal submission (price=0 → proposal_id=None).
        _exec_eligible = has_fresh_market_snapshot(symbol, market_type)
        if not _exec_eligible:
            logger.warning(
                "no_live_price_for_symbol: %s — execution_eligible set to False", symbol
            )

        opp = Opportunity(
            symbol=symbol,
            market=db_market,
            direction_bias=direction,
            thesis=thesis,
            confidence=confidence,
            confidence_score=int(round(total_score)),
            source_agent="universe_scanner",
            linked_strategy_id=linked_strategy_id,
            asset_class="crypto",
            metadata=json.dumps({
                "learning_candidate_key": m.get("learning_candidate_key"),
                "learning_exposure_id": m.get("learning_exposure_id"),
                "evidence_manifest": _trigger_evidence_manifest(
                    member=m,
                    feature=feat,
                    snapshot_id=snapshot_id,
                    direction=direction,
                    material_fingerprint=current_material_fingerprint,
                ),
                "material_fingerprint": current_material_fingerprint,
            }, sort_keys=True),
            execution_eligible=_exec_eligible,
            universe_snapshot_id=snapshot_id,
            universe_member_id=member_id,
            scanner_family=family,
            valid_until=m.get("expires_at"),
            regime_tag=regime.state if regime else None,
            funding_rate=feat.get("funding_rate"),
            open_interest=feat.get("open_interest"),
            oi_delta_1h=feat.get("oi_delta_1h"),
        )
        try:
            # A new lifecycle row without its emitted fact is an unaccounted input,
            # not a partial success. Keep both writes inside one rollback boundary
            # and increment the caller-visible count only after commit succeeds.
            with atomic_opportunity_evidence():
                owner, owner_created = insert_opportunity_once_by_snapshot(opp)
                if not owner_created:
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
                        opportunity_id=owner.get("id"),
                        regime_tag=regime.state if regime else None,
                        inputs={"existing_status": owner.get("status")},
                    )
                else:
                    _record_emission_decision(
                        member=m,
                        snapshot_id=snapshot_id,
                        member_index=member_index,
                        symbol=symbol,
                        market_type=market_type,
                        total_score=total_score,
                        short_enabled=short_enabled,
                        decision="passed",
                        reason_code="opportunity_emitted",
                        direction=direction,
                        opportunity_id=opp.id,
                        regime_tag=regime.state if regime else None,
                        inputs={"execution_eligible": _exec_eligible},
                    )
            if not owner_created:
                continue
            created += 1
            logger.debug("emitted %s %s %s score=%.0f", family, direction, symbol, total_score)
        except EmissionEvidenceError:
            # The source Universe member remains durable and retryable. Do not
            # relabel an evidence outage as an Opportunity insert failure.
            raise
        except Exception as e:
            logger.warning("Failed to emit opportunity for %s: %s", symbol, e)
            _record_emission_decision(
                member=m,
                snapshot_id=snapshot_id,
                member_index=member_index,
                symbol=symbol,
                market_type=market_type,
                total_score=total_score,
                short_enabled=short_enabled,
                decision="error",
                reason_code="opportunity_insert_error",
                direction=direction,
                opportunity_id=opp.id,
                regime_tag=regime.state if regime else None,
                reason_detail=type(e).__name__,
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
      - If bias < threshold, only pass if score is exceptionally high (top 20% above _HIGH_CONF_THRESHOLD)
        This allows strong contrarian signals to survive even in hostile regimes.

    US-9: short_enabled=False is a HARD operator switch — no score may override it.
    Previously a high-score short slipped through the score-override branch below,
    silently violating the operator's global short disable.
    """
    if direction == "short" and not short_enabled:
        return False

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
