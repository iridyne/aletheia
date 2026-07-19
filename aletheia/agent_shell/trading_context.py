"""Enriched trading context builder for System Operator decisions.

Assembles a three-layer context snapshot:
  Layer 1 — Environment Snapshot (deterministic, read-only, fresh each call)
  Layer 2 — Short-term Memory (7-day sliding window)
  Layer 3 — Long-term Wisdom (lessons + strategy performance)

Token budget target: ~2200 tokens total (vs ~800 in the old thin context).
"""

from __future__ import annotations

import hashlib
import json
import logging
from copy import deepcopy
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from aletheia.analytics.outcome_integrity import (
    OUTCOME_SEMANTICS_VERSION,
    eligible_strategy_outcomes,
)
from aletheia.data.position_x import build_position_x_agent_context

logger = logging.getLogger("aletheia.agent_shell.trading_context")

_PENDING_SIGNAL_VISIBLE_LIMIT = 5
_PENDING_SIGNAL_MAX_AGE_MINUTES = 30
_SERENITY_THESIS_DISPLAY_CHARS = 1200
_SERENITY_KEY_EVIDENCE_DISPLAY_CHARS = 2000


def _bounded_serenity_display(value: Any, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + " ... [display bounded; full fact persisted]"


def _serenity_evidence_display(
    evidence: list[dict[str, Any]],
) -> tuple[str, list[str]]:
    """Render complete evidence references and return only identities shown."""
    rendered: list[str] = []
    rendered_ids: list[str] = []
    for item in evidence:
        reference = str(
            item.get("source_ref") or item.get("url") or item.get("title") or ""
        ).strip()
        if not reference:
            continue
        candidate = "; ".join([*rendered, reference])
        if len(candidate) <= _SERENITY_KEY_EVIDENCE_DISPLAY_CHARS:
            rendered.append(reference)
            if item.get("id"):
                rendered_ids.append(str(item["id"]))
            continue
        if not rendered:
            rendered.append(
                _bounded_serenity_display(
                    reference, _SERENITY_KEY_EVIDENCE_DISPLAY_CHARS
                )
            )
            if item.get("id"):
                rendered_ids.append(str(item["id"]))
        break
    return "; ".join(rendered), rendered_ids


def _position_x_context_for_instrument(
    row: dict[str, Any] | None,
    market_type: Any,
) -> dict[str, Any] | None:
    """Position X is derivatives structure and must never decorate spot."""
    if _normalize_market_type(market_type) != "perp":
        return None
    return build_position_x_agent_context(row)


def operator_decision_context_fingerprint(ctx: dict[str, Any]) -> str:
    """Hash material decision inputs while ignoring carrier/timestamp churn.

    Opportunity ids and timestamps identify queue instances, not new market
    information. Excluding them lets the command suppress repeated model calls
    for materially identical symbol/direction contexts, while score, thesis,
    B2 status, regime, and instrument changes reopen evaluation.
    """

    def _score(value: Any) -> float | None:
        try:
            return round(float(value), 2) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _candidate(item: dict[str, Any]) -> dict[str, Any]:
        b2 = item.get("b2_evidence") or {}
        return {
            "symbol": item.get("symbol"),
            "direction": item.get("direction") or item.get("direction_bias"),
            "market_type": _normalize_market_type(
                item.get("market_type") or item.get("market")
            ),
            "scanner_family": item.get("scanner_family"),
            "asset_id": item.get("asset_id"),
            "rank_priority": _score(item.get("trade_score")),
            "reasoning": " ".join(str(item.get("reasoning") or "").split()).lower(),
            "b2": {
                "status": b2.get("status"),
                "n_total": b2.get("n_total"),
                "n_net": b2.get("n_net"),
                "outcome_coverage": _score(b2.get("outcome_coverage")),
                "avg_net_pct": _score(b2.get("avg_net_pct")),
                "shrunk_avg_net_pct": _score(b2.get("shrunk_avg_net_pct")),
                "directional_verdict": b2.get("directional_verdict"),
            },
        }

    layer1 = ctx.get("layer1") or {}
    layer2 = ctx.get("layer2") or {}
    pending = [_candidate(item) for item in (layer2.get("pending_signals") or [])]
    executable = [
        _candidate(item) for item in (layer2.get("executable_candidates") or [])
    ]
    candidate_instruments = {
        (item.get("symbol"), item.get("market_type"))
        for item in pending + executable
        if item.get("symbol")
    }
    material_feature_fields = (
        "rsi_15m", "return_15m", "last_candle_range_atr_15m",
        "return_1h", "return_4h", "return_24h", "funding_rate",
        "oi_delta_1h", "taker_buy_ratio", "vol_ratio",
        "relative_strength_24h", "oi_price_divergence", "rsi_4h",
        "bb_width_4h", "vol_ratio_4h", "bb_squeeze", "crowding_signal",
    )
    features = []
    for feature in ((layer1.get("feature_index") or {}).values()):
        identity = (
            feature.get("symbol"),
            _normalize_market_type(feature.get("market_type")),
        )
        if identity not in candidate_instruments:
            continue
        features.append({
            "symbol": identity[0],
            "market_type": identity[1],
            "asset_id": feature.get("asset_id"),
            "feature_snapshot_id": feature.get("feature_snapshot_id"),
            **{
                field: (
                    _score(feature.get(field))
                    if not isinstance(feature.get(field), bool)
                    else feature.get(field)
                )
                for field in material_feature_fields
            },
        })
    key = lambda item: (
        str(item.get("symbol") or ""),
        str(item.get("direction") or ""),
        str(item.get("market_type") or ""),
        str(item.get("scanner_family") or ""),
    )
    projection = {
        "version": "operator-decision-context-v3",
        "skip_policy_epoch_id": ctx.get("skip_policy_epoch_id"),
        "wake_context": ctx.get("wake_context"),
        "regime": layer1.get("regime") or layer1.get("market_regime"),
        "pending": sorted(pending, key=key),
        "executable": sorted(executable, key=key),
        "features": sorted(features, key=key),
        "serenity": sorted(
            [
                {
                    "symbol": item.get("symbol"),
                    "direction": item.get("direction"),
                    "market_type": _normalize_market_type(item.get("market_type")),
                    "thesis": " ".join(str(item.get("thesis") or "").split()),
                    "assessed_tier": item.get("assessed_tier"),
                    "researchability_grade": item.get("researchability_grade"),
                    "score": _score(item.get("score")),
                    "bucket": item.get("bucket"),
                    "evidence_ids": sorted(item.get("evidence_ids") or []),
                    "hydration_status": item.get("hydration_status"),
                    "unavailable_reason": item.get("unavailable_reason"),
                }
                for item in ((ctx.get("layer3") or {}).get("serenity_opportunities") or [])
            ],
            key=key,
        ),
    }
    encoded = json.dumps(
        projection, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def scope_operator_context_to_candidate(
    ctx: dict[str, Any], opportunity_id: str
) -> dict[str, Any]:
    """Return a one-carrier model context without cross-candidate evidence.

    Queue metadata remains global for scheduling/audit, but every candidate row,
    technical row, and open-opportunity thesis shown to the model belongs to the
    selected durable carrier.
    """
    scoped = deepcopy(ctx)
    layer1 = scoped.get("layer1") or {}
    layer2 = scoped.get("layer2") or {}
    selected = next(
        (
            row for row in (layer2.get("pending_signals") or [])
            if row.get("opportunity_id") == opportunity_id
        ),
        None,
    )
    if selected is None:
        raise ValueError(f"pending_candidate_not_found:{opportunity_id}")

    symbol = selected.get("symbol")
    market_type = _normalize_market_type(selected.get("market_type"))
    asset_id = selected.get("asset_id")

    def _same_instrument(row: dict[str, Any]) -> bool:
        if asset_id:
            return row.get("asset_id") == asset_id
        return (
            row.get("symbol") == symbol
            and _normalize_market_type(row.get("market_type") or row.get("market"))
            == market_type
        )

    layer2["pending_signals"] = [selected]
    layer2["executable_candidates"] = [
        row for row in (layer2.get("executable_candidates") or [])
        if row.get("opportunity_id") == opportunity_id
    ]
    layer2["analysis_only_candidates"] = []
    layer2["ranker_candidates"] = []
    layer2["excluded_candidates_summary"] = []
    layer2["selected_candidate"] = {
        "opportunity_id": opportunity_id,
        "signal_source": selected.get("signal_source") or "market_observer",
        "symbol": symbol,
        "direction": selected.get("direction"),
        "market_type": selected.get("market_type"),
        "asset_id": asset_id,
        "venue": selected.get("venue"),
    }

    layer1["features"] = [
        row for row in (layer1.get("features") or []) if _same_instrument(row)
    ]
    layer1["feature_index"] = {
        key: row
        for key, row in (layer1.get("feature_index") or {}).items()
        if _same_instrument(row)
    }
    layer1["opportunities"] = [
        row for row in (layer1.get("opportunities") or [])
        if row.get("id") == opportunity_id
    ]
    layer3 = scoped.get("layer3") or {}
    layer3["serenity_opportunities"] = [
        row for row in (layer3.get("serenity_opportunities") or [])
        if row.get("id") == opportunity_id
    ]
    scoped["layer1"] = layer1
    scoped["layer2"] = layer2
    scoped["layer3"] = layer3
    return scoped


def _dedupe_symbol_direction(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Keep the first item for each symbol/direction pair."""
    out: list[dict[str, Any]] = []
    seen: set[tuple[str | None, str | None]] = set()
    for item in items:
        key = (item.get("symbol"), item.get("direction"))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def _pending_identity_key(
    item: dict[str, Any],
    *,
    require_complete: bool = True,
) -> tuple[str, str, str, str, str] | None:
    """Return the complete execution-authority identity or None if it is partial."""
    key = (
        str(item.get("opportunity_id") or ""),
        str(item.get("signal_source") or "market_observer"),
        str(item.get("symbol") or ""),
        str(item.get("direction") or ""),
        str(item.get("market_type") or ""),
    )
    return key if (not require_complete or all(key)) else None


def _dedupe_pending_identity(
    items: list[dict[str, Any]], limit: int
) -> list[dict[str, Any]]:
    """Keep one carrier per exact source/instrument identity.

    Spot and futures carriers may legitimately share a canonical symbol and
    direction.  Collapsing those rows together makes the older market claim the
    newer one's execution authority, so pending de-duplication must retain the
    market and source dimensions.
    """
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for item in items:
        key = _pending_identity_key(item, require_complete=False)
        assert key is not None
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def _build_pending_signal_queue(
    *,
    limit: int = _PENDING_SIGNAL_VISIBLE_LIMIT,
    max_age_minutes: int = _PENDING_SIGNAL_MAX_AGE_MINUTES,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return the oldest unresolved queue slice plus honest backlog metadata."""
    from aletheia.storage.sqlite import get_db

    db = get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)).isoformat()
    # A submitting final is an execution claim, not a terminal decision. Keep
    # its exact carrier visible so a wake after process death can recover the
    # already-stored Proposal. Once any non-submitting final exists, the carrier
    # is terminally consumed and disappears from the queue.
    unique_entry_final = (
        "(SELECT COUNT(*) FROM pi_decisions all_f "
        " WHERE all_f.opportunity_id=p.opportunity_id "
        " AND COALESCE(all_f.signal_source, 'market_observer')="
        "     COALESCE(p.signal_source, 'market_observer') "
        " AND all_f.action NOT IN ('signal_pending', 'close', 'reduce'))=1"
    )
    exact_final_identity = (
        "f.symbol IS p.symbol AND f.direction IS p.direction "
        "AND f.market_type IS p.market_type"
    )
    submitting_final = (
        "SELECT 1 FROM pi_decisions f "
        "WHERE f.action NOT IN ('signal_pending', 'close', 'reduce') "
        "  AND f.opportunity_id = p.opportunity_id "
        "  AND COALESCE(f.signal_source, 'market_observer') = "
        "      COALESCE(p.signal_source, 'market_observer') "
        f"  AND {exact_final_identity} "
        "  AND f.proposal_status = 'submitting' "
        f"  AND {unique_entry_final}"
    )
    terminal_final = (
        "SELECT 1 FROM pi_decisions f "
        "WHERE f.action NOT IN ('signal_pending', 'close', 'reduce') "
        "  AND f.opportunity_id = p.opportunity_id "
        "  AND COALESCE(f.signal_source, 'market_observer') = "
        "      COALESCE(p.signal_source, 'market_observer') "
        f"  AND {exact_final_identity} "
        "  AND COALESCE(f.proposal_status, '') != 'submitting' "
        f"  AND {unique_entry_final}"
    )
    unresolved = (
        "p.action = 'signal_pending' "
        "AND (COALESCE(p.signal_source, 'market_observer') = 'serenity' "
        f"     OR EXISTS ({submitting_final}) "
        "     OR (COALESCE(p.signal_source, 'market_observer') = 'market_observer' "
        "         AND ((o.status='queued' "
        "               AND datetime(o.valid_until) IS NOT NULL "
        "               AND datetime(o.valid_until)>datetime('now')) "
        "              OR ((o.id IS NULL OR (o.status='queued' "
        "                                      AND datetime(o.valid_until) IS NULL)) "
        "                  AND datetime(p.created_at)>=datetime(?))))) "
        "AND (p.opportunity_id IS NULL OR NOT EXISTS ("
        f"  {terminal_final}"
        "))"
    )
    rows = db.execute(
        "SELECT p.symbol, p.direction, p.market_type, p.reasoning, p.created_at, "
        "p.opportunity_id, p.signal_source, o.universe_member_id, o.valid_until, "
        "(SELECT um.snapshot_id FROM universe_members um "
        " WHERE um.id=o.universe_member_id) AS universe_snapshot_id, "
        "COALESCE(o.scanner_family, (SELECT st.scanner_family FROM shadow_trades st "
        " WHERE st.opportunity_id=p.opportunity_id AND st.was_selected=1 "
        " ORDER BY datetime(st.created_at) DESC, st.rowid DESC LIMIT 1)) AS scanner_family, "
        "(SELECT st.trade_score FROM shadow_trades st "
        " WHERE st.opportunity_id=p.opportunity_id AND st.was_selected=1 "
        " ORDER BY datetime(st.created_at) DESC, st.rowid DESC LIMIT 1) AS trade_score "
        "FROM pi_decisions p LEFT JOIN opportunities o ON o.id=p.opportunity_id WHERE "
        + unresolved
        + " ORDER BY datetime(p.created_at) ASC, p.rowid ASC",
        (cutoff,),
    ).fetchall()
    pending = [
        {
            "symbol": row["symbol"],
            "direction": row["direction"],
            "market_type": row["market_type"],
            "reasoning": (row["reasoning"] or "")[:100],
            "at": row["created_at"],
            "opportunity_id": row["opportunity_id"],
            "signal_source": row["signal_source"] or "market_observer",
            "universe_member_id": row["universe_member_id"],
            "universe_snapshot_id": row["universe_snapshot_id"],
            "valid_until": row["valid_until"],
            "scanner_family": row["scanner_family"],
            "trade_score": row["trade_score"],
        }
        for row in rows
    ]
    from aletheia.storage.repositories import (
        classify_final_pi_decision_for_opportunity,
    )

    for item in pending:
        classification = classify_final_pi_decision_for_opportunity(
            item.get("opportunity_id"),
            item.get("signal_source") or "market_observer",
            symbol=item.get("symbol"),
            direction=item.get("direction"),
            market_type=item.get("market_type"),
        )
        if classification["status"] == "integrity_unavailable":
            item["final_integrity_status"] = "unavailable"
            item["final_integrity_reason"] = classification["reason"]
    unique = _dedupe_pending_identity(pending, limit=len(pending)) if pending else []
    # Any carrier with an active defer (defer_until > now) is demoted to the tail
    # so non-deferred candidates are always processed first. The previous "refreshed
    # defer keeps position" logic created a re-evaluation loop: feature snapshots
    # update every few minutes, the new snapshot_id changed the context fingerprint,
    # defeating the dedup check, and the carrier was re-evaluated on every feature
    # refresh cycle. Demoting all active defers — regardless of feature freshness —
    # ensures deferred carriers do not hold the queue head. The command-side
    # market-quiet fast-path (_existing_deferred_context_observation) is the
    # authoritative guard against re-invoking the LLM within the defer window.
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        deferred_rows = db.execute(
            "SELECT json_extract(queue_snapshot_json, '$.opportunity_id') "
            "         AS opportunity_id "
            "FROM operator_evaluation_events "
            "WHERE event_kind LIKE 'operator_decision_deferred_%' "
            "AND json_extract(queue_snapshot_json, '$.defer_until') > ? "
            "GROUP BY json_extract(queue_snapshot_json, '$.opportunity_id')",
            (now_iso,),
        ).fetchall()
        deferred_ids: set[str] = {
            str(row["opportunity_id"])
            for row in deferred_rows
            if row["opportunity_id"]
        }

        def _is_deferred(row: dict[str, Any]) -> bool:
            return str(row.get("opportunity_id") or "") in deferred_ids

        if deferred_ids:
            unique.sort(key=_is_deferred)
    except Exception:
        # Scheduling remains oldest-first if legacy schema/data cannot expose the
        # optional defer hint; command-side fact validation is still authoritative.
        pass
    visible_limit = max(int(limit), 0)
    visible = unique[:visible_limit]
    expired = db.execute(
        "SELECT COUNT(*) AS n, MIN(p.created_at) AS oldest_at "
        "FROM pi_decisions p LEFT JOIN opportunities o ON o.id=p.opportunity_id "
        "WHERE p.action = 'signal_pending' "
        "AND COALESCE(p.signal_source, 'market_observer') = 'market_observer' "
        "AND NOT EXISTS ("
        + submitting_final
        + ") "
        "AND (p.opportunity_id IS NULL OR NOT EXISTS ("
        + terminal_final
        + ")) AND ("
        " (o.id IS NULL AND datetime(p.created_at)<datetime(?)) "
        " OR (o.status='queued' AND ("
        "      (datetime(o.valid_until) IS NOT NULL "
        "       AND datetime(o.valid_until)<=datetime('now')) "
        "      OR (datetime(o.valid_until) IS NULL "
        "          AND datetime(p.created_at)<datetime(?))"
        " )) OR (o.id IS NOT NULL AND o.status!='queued')"
        ")",
        (cutoff, cutoff),
    ).fetchone()
    queue = {
        "read_status": "complete",
        "unresolved_total": len(unique),
        "visible_count": len(visible),
        "overflow_count": max(len(unique) - len(visible), 0),
        "oldest_at": unique[0]["at"] if unique else None,
        "max_age_minutes": max_age_minutes,
        "age_basis": "opportunity_valid_until_with_legacy_pending_fallback",
        "expired_unconsumed_rows": int(expired["n"] or 0),
        "oldest_expired_at": expired["oldest_at"],
    }
    return visible, queue


def _excluded_candidates_summary(items: list[dict[str, Any]], *, examples_per_reason: int = 3) -> list[dict[str, Any]]:
    """Summarize skipped ranker candidates without letting them occupy prompt slots."""
    counts: Counter[str] = Counter()
    examples: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        raw_reason = str(item.get("skip_reason") or "unknown")
        reason = _candidate_skip_bucket(raw_reason)
        counts[reason] += 1
        bucket_examples = examples.setdefault(reason, [])
        if len(bucket_examples) < examples_per_reason:
            bucket_examples.append({
                "symbol": item.get("symbol"),
                "direction": item.get("direction"),
                "trade_score": item.get("trade_score"),
                "scanner_family": item.get("scanner_family"),
                "skip_reason": raw_reason,
                # Carrier lifecycle — lets the funnel UI render
                # "expired X min ago · ttl was N min" for ttl_expired bucket.
                "opportunity_id": item.get("opportunity_id"),
                "valid_until": item.get("valid_until"),
            })
    return [
        {"reason": reason, "count": count, "examples": examples.get(reason, [])}
        for reason, count in counts.most_common()
    ]


def _candidate_skip_bucket(reason: str) -> str:
    if reason == "okx_demo_symbol_not_supported" or "demo_symbol_not_supported" in reason:
        return "unsupported_demo_symbol"
    if reason.startswith("guard_blocked:"):
        return "guard_blocked"
    if reason.startswith("confidence_below_"):
        return "confidence_below"
    return reason or "unknown"


def _record_candidate_supply_snapshot(layer: dict[str, Any]) -> None:
    """Record candidate supply telemetry for recommendations without changing decisions."""
    try:
        from aletheia.analytics.candidate_supply import build_candidate_supply_snapshot

        snapshot = build_candidate_supply_snapshot(
            executable_candidates=layer.get("executable_candidates") or [],
            excluded_candidates_summary=layer.get("excluded_candidates_summary") or [],
        )
    except Exception as exc:
        logger.debug("candidate supply snapshot build failed: %s", exc)
        layer["candidate_supply"] = {
            "executable_count": len(layer.get("executable_candidates") or []),
            "below_floor": None,
            "main_reason": "insufficient_data",
        }
        return

    layer["candidate_supply"] = snapshot
    try:
        from aletheia.agent_shell.operator_store import create_event

        event_id = create_event(
            event_type="candidate_supply_snapshot",
            severity="warning" if snapshot.get("below_floor") else "info",
            source="trading_context",
            related_entity_type="candidate_supply",
            related_entity_id="system_operator",
            payload=snapshot,
        )
        snapshot["event_id"] = event_id
    except Exception as exc:
        logger.debug("candidate supply snapshot skipped: %s", exc)
        snapshot["event_status"] = "not_persisted"


def build_enriched_trading_context(*, authorize_learning: bool = True) -> dict[str, Any]:
    """Build the three-layer context for System Operator trading decisions.

    Returns a dict with keys: layer1, layer2, layer3, each containing
    pre-formatted data ready for prompt injection.
    """
    layer1 = _build_layer1_snapshot()
    layer2 = _build_layer2_short_term()
    try:
        _hydrate_pending_candidate_context(layer1, layer2)
    except Exception as exc:
        # Command-side availability preserves the carrier and records a typed
        # defer instead of letting a candidate read failure crash the loop.
        logger.warning("pending candidate context hydration unavailable: %s", exc)
    layer3 = _build_layer3_wisdom()
    try:
        _hydrate_pending_serenity_context(layer2, layer3)
    except Exception as exc:
        logger.warning("pending Serenity context hydration unavailable: %s", exc)
    ctx = {
        "layer1": layer1,
        "layer2": layer2,
        "layer3": layer3,
    }
    if not authorize_learning:
        return ctx
    from aletheia.learning.prompt_bundles import authorize_operator_learning_context
    return authorize_operator_learning_context(ctx)


def build_decision_evidence_manifest(ctx: dict[str, Any], decision) -> dict[str, Any]:
    """Build a compact manifest of exact evidence available to one decision."""
    from aletheia.agent_shell.decision_facts import candidate_fact_bundle
    from aletheia.storage import repositories as repo
    from aletheia.learning.opportunity_information_policy import (
        POLICY_EPOCH_LABEL,
        ensure_opportunity_information_policy_epoch,
    )

    information_epoch_id = ctx.get("information_policy_epoch_id")
    if not information_epoch_id:
        try:
            information_epoch_id = ensure_opportunity_information_policy_epoch()
        except Exception:
            information_epoch_id = None

    symbol = getattr(decision, "symbol", None)
    market_type = _normalize_market_type(getattr(decision, "market_type", None))
    opportunity_id = getattr(decision, "opportunity_id", None)
    signal_source = getattr(decision, "signal_source", None)
    if not signal_source:
        selected_pending = next(
            (
                row for row in ((ctx.get("layer2") or {}).get("pending_signals") or [])
                if row.get("opportunity_id") == opportunity_id
            ),
            None,
        )
        signal_source = (
            selected_pending.get("signal_source")
            if isinstance(selected_pending, dict) else None
        ) or "market_observer"
    opportunity = (
        repo.get_opportunity(opportunity_id)
        if opportunity_id and signal_source == "market_observer" else None
    )
    serenity_opportunity = (
        repo.get_serenity_opportunity(opportunity_id)
        if opportunity_id and signal_source == "serenity" else None
    )
    member = (
        repo.get_universe_member(opportunity.get("universe_member_id"))
        if opportunity and opportunity.get("universe_member_id") else None
    )
    feature = _candidate_feature(
        (ctx.get("layer1") or {}).get("feature_index") or {},
        symbol=symbol,
        market_type=market_type,
        asset_id=member.get("asset_id") if member else None,
    )
    venue = feature.get("venue") if feature else None
    facts = candidate_fact_bundle(ctx, opportunity_id=opportunity_id)

    unavailable = {"status": "unavailable"}
    serenity_context = next(
        (
            row for row in ((ctx.get("layer3") or {}).get("serenity_opportunities") or [])
            if row.get("id") == opportunity_id
        ),
        None,
    )
    rendered_evidence_rows: list[dict[str, Any]] = []
    if signal_source == "serenity":
        opportunity_t0 = ({
            "status": "available",
            "trigger_type": "serenity_research",
            "opportunity_id": serenity_opportunity.get("id"),
            "theme_id": serenity_opportunity.get("theme_id"),
            "symbol": serenity_opportunity.get("symbol"),
            "direction": serenity_opportunity.get("direction"),
            "market_type": serenity_opportunity.get("market_type"),
            "assessed_tier": serenity_opportunity.get("assessed_tier"),
            "researchability_grade": serenity_opportunity.get("researchability_grade"),
        } if serenity_opportunity else unavailable)
        evidence_by_id = {
            row.get("id"): row
            for row in repo.list_serenity_evidence(
                theme_id=serenity_opportunity.get("theme_id"), limit=200,
            )
            if row.get("id")
        } if serenity_opportunity else {}
        if isinstance(serenity_context, dict):
            context_evidence = serenity_context.get("evidence") or []
            context_ids = [
                str(item)
                for item in (serenity_context.get("rendered_evidence_ids") or [])
                if item
            ]
            context_by_id = {
                str(item.get("id")): item
                for item in context_evidence
                if isinstance(item, dict) and item.get("id")
            }
            for evidence_id in context_ids:
                stored = evidence_by_id.get(evidence_id)
                if stored is None:
                    continue
                rendered = context_by_id.get(evidence_id) or stored
                rendered_evidence_rows.append({
                    key: rendered.get(key)
                    for key in ("id", "source", "source_ref", "title")
                    if rendered.get(key) is not None
                })
        opportunity_t1 = ({
            "status": "available",
            "sources": {"serenity_evidence": rendered_evidence_rows},
        } if serenity_context is not None else unavailable)
    else:
        opportunity_evidence = _opportunity_evidence_from_metadata(
            opportunity.get("metadata") if opportunity else None
        )
        opportunity_t0 = (
            opportunity_evidence.get("t0")
            if opportunity_evidence and isinstance(opportunity_evidence.get("t0"), dict)
            else unavailable
        )
        opportunity_t1 = (
            opportunity_evidence.get("t1")
            if opportunity_evidence and isinstance(opportunity_evidence.get("t1"), dict)
            else unavailable
        )

    rendered_opportunity = next(
        (
            row for row in ((ctx.get("layer1") or {}).get("opportunities") or [])
            if row.get("id") == opportunity_id
        ),
        None,
    )
    rendered_evidence = (
        rendered_opportunity.get("evidence_manifest")
        if isinstance(rendered_opportunity, dict) else None
    )
    rendered_t1 = (
        rendered_evidence.get("t1")
        if isinstance(rendered_evidence, dict)
        and isinstance(rendered_evidence.get("t1"), dict)
        else None
    )
    rendered_sources = (
        rendered_t1.get("sources")
        if isinstance(rendered_t1, dict) and isinstance(rendered_t1.get("sources"), dict)
        else {}
    )
    structure_context = (
        rendered_opportunity.get("structure_context")
        if isinstance(rendered_opportunity, dict) else None
    )
    operator_position_x = (
        structure_context.get("source_identity")
        if isinstance(structure_context, dict)
        and structure_context.get("status") == "ok"
        and isinstance(structure_context.get("source_identity"), dict)
        else None
    )
    rendered_position_x: list[dict[str, Any]] = []

    def add_position_x_identity(
        row: dict[str, Any], *, entity_type: str, market_key: str
    ) -> None:
        if _normalize_market_type(row.get(market_key)) != "perp":
            return
        context = row.get("structure_context")
        identity = (
            context.get("source_identity")
            if isinstance(context, dict) and context.get("status") == "ok"
            else None
        )
        if not isinstance(identity, dict):
            return
        rendered_position_x.append({
            **identity,
            "entity_type": entity_type,
            "entity_id": row.get("id"),
            "symbol": row.get("symbol"),
            "market_type": _normalize_market_type(row.get(market_key)),
        })

    for position in ((ctx.get("layer1") or {}).get("positions") or [])[:5]:
        add_position_x_identity(
            position, entity_type="position", market_key="market_type"
        )
    for rendered in ((ctx.get("layer1") or {}).get("opportunities") or [])[:8]:
        add_position_x_identity(
            rendered, entity_type="opportunity", market_key="market"
        )

    position_x_manifest = (
        {
            "status": "available_observation_only",
            "rendered_source_identities": rendered_position_x,
        }
        if rendered_position_x
        else {"status": "unavailable"}
    )

    def _json_list(value: Any) -> list:
        if isinstance(value, list):
            return value
        try:
            parsed = json.loads(value or "[]")
            return parsed if isinstance(parsed, list) else []
        except (TypeError, ValueError):
            return []

    return {
        "version": "vergex-utilization-v1",
        "decision_evidence_version": "operator-candidate-v3",
        "information_policy_epoch": {
            "label": POLICY_EPOCH_LABEL,
            "durable_id": information_epoch_id,
            "status": "available" if information_epoch_id else "not_persisted",
        },
        "policy_epoch_id": ctx.get("vergex_policy_epoch_id"),
        "skip_policy_epoch_id": ctx.get("skip_policy_epoch_id"),
        "source_chain": {
            "opportunity_id": opportunity_id,
            "signal_source": signal_source,
            "universe_snapshot_id": (
                opportunity.get("universe_snapshot_id") if opportunity else None
            ) or (member.get("snapshot_id") if member else None),
            "universe_member_id": opportunity.get("universe_member_id") if opportunity else None,
            "feature_snapshot_id": feature.get("feature_snapshot_id") if feature else None,
            "serenity_theme_id": (
                serenity_opportunity.get("theme_id") if serenity_opportunity else None
            ),
        },
        "instrument": {
            "venue": venue,
            "symbol": symbol,
            "market_type": market_type,
        },
        "technical_snapshot": ({
            "id": feature.get("feature_snapshot_id"),
            "observed_at": feature.get("feature_ts"),
            "data_freshness_sec": feature.get("data_freshness_sec"),
            "timeframes": ["15m", "1h", "4h"],
        } if feature else {"status": "unavailable"}),
        "opportunity_t0": opportunity_t0,
        "opportunity_t1": opportunity_t1,
        "system_operator_t1": ({
            "status": "available",
            "technical_snapshot_id": feature.get("feature_snapshot_id") if feature else None,
            "position_x": position_x_manifest,
            "opportunity_context": (
                {
                    "status": "via_observer_summary",
                    "evidence_version": rendered_evidence.get("version"),
                }
                if isinstance(rendered_evidence, dict)
                else {
                    "status": "direct_serenity_research",
                    "evidence_count": len(rendered_evidence_rows),
                }
                if signal_source == "serenity" and serenity_context is not None
                else {"status": "unavailable"}
            ),
        } if feature or operator_position_x or rendered_opportunity or serenity_context
        else {"status": "unavailable"}),
        "candidate_selection": {
            "rank_priority": facts["candidate"].get("rank_priority"),
            "rank_priority_semantics": "ordering_only",
            "scanner_family": facts["candidate"].get("scanner_family"),
            "execution_eligible": (
                member.get("execution_eligible") if member else facts["candidate"].get("execution_eligible")
            ),
            "universe_rank": member.get("rank") if member else None,
            "universe_total_score": member.get("total_score") if member else None,
            "risk_flags": _json_list(member.get("risk_flags")) if member else [],
        },
        "b2_candidate_evidence": facts["b2"],
        "market_regime": facts["regime"],
        "recent_performance": facts["recent_performance"],
        "account_context": facts["account"],
        "hyperliquid": ({
            "status": "not_directly_rendered",
            "via": "opportunity_t1",
        } if rendered_sources.get("hyperliquid") else {
            "status": "unavailable",
            "funding_id": None,
            "open_interest_id": None,
        }),
        "position_x": position_x_manifest,
        "operator_rationale": {
            "model_reasoning": getattr(decision, "model_reasoning", None),
            "skip_kind": getattr(decision, "skip_kind", None),
            "adverse_reason_codes": list(
                getattr(decision, "adverse_reason_codes", None) or []
            ),
            "adverse_evidence": list(
                getattr(decision, "adverse_evidence", None) or []
            ),
            "supporting_signals": list(
                getattr(decision, "supporting_signals", None) or []
            ),
            "opposing_signals": list(
                getattr(decision, "opposing_signals", None) or []
            ),
            "risk_factors": list(getattr(decision, "risk_factors", None) or []),
            "validated_adverse_claims": list(
                getattr(decision, "validated_adverse_claims", None) or []
            ),
        },
    }


# ── Layer 1: Environment Snapshot ────────────────────────────────────────────


def _top_observations(raw, n: int = 2) -> list[str]:
    """Parse the key_observations JSON blob → first n data-cited bullets.

    market_observer stores its most specific LLM output (numeric citations like
    "1h RSI 68, price > SMA20") in opportunities.key_observations, but the PI
    layer1 snapshot historically dropped it — the PI saw only a truncated thesis.
    Surfacing a couple of observations restores the citations for the decision.
    """
    if not raw:
        return []
    try:
        import json as _json
        obs = _json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(obs, list):
            return [str(x) for x in obs[:n] if x]
    except Exception:
        pass
    return []


def _normalize_market_type(value: object) -> str:
    raw = str(value or "spot").lower()
    return "perp" if raw in {"perp", "futures", "swap", "usdt_futures"} else "spot"


def _feature_identity_key(venue: object, market_type: object, symbol: object) -> str:
    return f"{venue or 'unknown'}|{_normalize_market_type(market_type)}|{symbol or ''}"


def _json_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value or "[]")
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []


def _opportunity_evidence_from_metadata(value: Any) -> dict | None:
    try:
        metadata = json.loads(value or "{}") if isinstance(value, str) else value
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(metadata, dict):
        return None
    manifest = metadata.get("evidence_manifest")
    if not isinstance(manifest, dict):
        return None
    if manifest.get("version") != "opportunity-information-integrity-v1":
        return None
    return manifest


def _feature_context_row(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Project one persisted feature row into the Operator context schema."""
    from aletheia.data.price_feed import get_cached_price_with_meta

    sym = snapshot.get("symbol", "")
    try:
        price_meta = get_cached_price_with_meta(sym)
    except Exception:
        price_meta = None
    fr = snapshot.get("funding_rate")
    oi_delta = snapshot.get("oi_delta_1h")
    crowding_signal = (
        fr is not None and oi_delta is not None
        and abs(fr) > 0.0005 and abs(oi_delta) > 5
    )
    return {
        "asset_id": snapshot.get("asset_id"),
        "feature_snapshot_id": snapshot.get("id"),
        "feature_ts": snapshot.get("ts"),
        "data_freshness_sec": snapshot.get("data_freshness_sec"),
        "venue": snapshot.get("venue"),
        "symbol": sym,
        "market_type": _normalize_market_type(snapshot.get("market_type")),
        "price": price_meta.get("price") if price_meta else None,
        "price_source": price_meta.get("provider") if price_meta else None,
        "rsi_14": snapshot.get("rsi_14"),
        "rsi_15m": snapshot.get("rsi_15m"),
        "atr_15m_pct": snapshot.get("atr_15m_pct"),
        "return_15m": snapshot.get("return_15m"),
        "last_candle_range_atr_15m": snapshot.get("last_candle_range_atr_15m"),
        "atr_pct": snapshot.get("atr_pct"),
        "return_1h": snapshot.get("return_1h"),
        "return_4h": snapshot.get("return_4h"),
        "return_24h": snapshot.get("return_24h"),
        "funding_rate": fr,
        "oi_delta_1h": oi_delta,
        "btc_beta": snapshot.get("btc_beta"),
        "volume_24h": snapshot.get("quote_volume_24h"),
        "taker_buy_ratio": snapshot.get("taker_buy_ratio"),
        "vol_ratio": snapshot.get("vol_ratio"),
        "relative_strength_24h": snapshot.get("relative_strength_24h"),
        "oi_price_divergence": snapshot.get("oi_price_divergence"),
        "rsi_4h": snapshot.get("rsi_4h"),
        "atr_4h_pct": snapshot.get("atr_4h_pct"),
        "bb_width_4h": snapshot.get("bb_width_4h"),
        "vol_ratio_4h": snapshot.get("vol_ratio_4h"),
        "bb_squeeze": (
            bool(snapshot.get("bb_squeeze"))
            if snapshot.get("bb_squeeze") is not None else None
        ),
        "crowding_signal": crowding_signal,
    }


def _candidate_feature(
    feature_index: dict[str, dict], *, symbol: object, market_type: object,
    asset_id: object = None,
) -> dict | None:
    if asset_id:
        exact_asset = [
            row for row in feature_index.values() if row.get("asset_id") == asset_id
        ]
        if len(exact_asset) == 1:
            return exact_asset[0]
    normalized = _normalize_market_type(market_type)
    matches = [
        row for row in feature_index.values()
        if row.get("symbol") == symbol and row.get("market_type") == normalized
    ]
    return matches[0] if len(matches) == 1 else None


def _hydrate_pending_candidate_context(
    layer1: dict[str, Any], layer2: dict[str, Any],
) -> None:
    """Attach exact persisted facts for every visible durable pending carrier.

    The general Layer 1 feature list is a bounded presentation slice. It must
    never be used as the authority lookup for the candidate being judged: one
    universe refresh commonly writes hundreds of same-timestamp feature rows,
    so a global LIMIT can omit every queued instrument.
    """
    from aletheia.storage import repositories as repo

    feature_index = layer1.setdefault("feature_index", {})
    display_features = layer1.setdefault("features", [])
    opportunities = layer1.setdefault("opportunities", [])
    opportunity_ids = {str(row.get("id")) for row in opportunities if row.get("id")}

    pending = layer2.get("pending_signals") or []
    for candidate in pending:
        if (candidate.get("signal_source") or "market_observer") != "market_observer":
            continue
        opportunity_id = candidate.get("opportunity_id")
        opportunity = repo.get_opportunity(opportunity_id) if opportunity_id else None
        member_id = (
            candidate.get("universe_member_id")
            or (opportunity or {}).get("universe_member_id")
        )
        member = repo.get_universe_member(member_id) if member_id else None
        asset_id = (member or {}).get("asset_id")
        asset = repo.get_universe_asset(asset_id) if asset_id else None

        # Fallback: opportunity created before universe was enabled carries no
        # universe_member_id, so the member→asset chain is broken. Resolve by
        # symbol + market_type from universe_registry so the feature snapshot
        # lookup can still proceed and candidate_technical_snapshot_unavailable
        # defers don't permanently hold queue slots.
        if asset is None and not asset_id:
            registry_row = repo.get_db().execute(
                "SELECT * FROM universe_registry WHERE symbol = ? AND market_type = ? "
                "AND status = 'active' ORDER BY last_seen_at DESC LIMIT 1",
                (candidate.get("symbol"), str(candidate.get("market_type") or "spot")),
            ).fetchone()
            if registry_row:
                asset_id = str(registry_row["id"])
                asset = dict(registry_row)

        candidate.update({
            "universe_member_id": member_id,
            "universe_snapshot_id": (
                candidate.get("universe_snapshot_id")
                or (opportunity or {}).get("universe_snapshot_id")
                or (member or {}).get("snapshot_id")
            ),
            "asset_id": asset_id,
            "venue": (asset or {}).get("venue"),
            "execution_eligible": (
                bool(member.get("execution_eligible")) if member is not None
                else (opportunity or {}).get("execution_eligible")
            ),
            "risk_flags": _json_list((member or {}).get("risk_flags")),
        })
        for row in layer2.get("executable_candidates") or []:
            if row.get("opportunity_id") == opportunity_id:
                for field in (
                    "universe_member_id", "universe_snapshot_id", "asset_id",
                    "venue", "execution_eligible", "risk_flags",
                ):
                    row[field] = candidate.get(field)

        feature = None
        if asset_id and asset:
            snapshot = repo.get_latest_feature_snapshot(asset_id)
            if snapshot:
                feature = _feature_context_row({**snapshot, **{
                    "symbol": asset.get("symbol"),
                    "market_type": asset.get("market_type"),
                    "venue": asset.get("venue"),
                }})
                feature_key = _feature_identity_key(
                    feature.get("venue"), feature.get("market_type"), feature.get("symbol")
                )
                feature_index[feature_key] = feature
                replaced = False
                for index, row in enumerate(display_features):
                    if row.get("asset_id") == asset_id:
                        display_features[index] = feature
                        replaced = True
                        break
                if not replaced:
                    display_features.append(feature)

        # Queued opportunities were historically absent because the general
        # presentation query read only status='open'. Preserve the exact source
        # thesis and observations for the selected carrier even after admission.
        if opportunity and str(opportunity_id) not in opportunity_ids:
            opportunities.append({
                "id": opportunity.get("id"),
                "linked_strategy_id": opportunity.get("linked_strategy_id"),
                "symbol": opportunity.get("symbol"),
                "market": opportunity.get("market"),
                "direction_bias": opportunity.get("direction_bias"),
                "confidence": opportunity.get("confidence"),
                "confidence_score": opportunity.get("confidence_score"),
                "scanner_family": opportunity.get("scanner_family"),
                "family_edge": None,
                "thesis": (opportunity.get("thesis") or "")[:220],
                "key_observations": _top_observations(
                    opportunity.get("key_observations")
                ),
                "evidence_manifest": (
                    _opportunity_evidence_from_metadata(opportunity.get("metadata"))
                    or {"status": "unavailable"}
                ),
                "technical_context": feature,
                "structure_context": _position_x_context_for_instrument(
                    repo.latest_perp_structure(opportunity.get("symbol")),
                    opportunity.get("market"),
                ),
            })
            opportunity_ids.add(str(opportunity_id))


def _serenity_context_row(opportunity: dict[str, Any]) -> dict[str, Any]:
    """Project one exact Serenity source with its own theme evidence."""
    from aletheia.storage import repositories as repo
    from aletheia.trading.serenity_scoring import score_serenity_opportunity

    evidence = repo.list_serenity_evidence(
        theme_id=opportunity.get("theme_id"), limit=200
    )
    gates = repo.list_serenity_risk_gates(
        opportunity_id=opportunity.get("id"), limit=200
    )
    score = score_serenity_opportunity(
        opportunity, evidence=evidence, risk_gates=gates
    )
    evidence_rows = [
        {
            "id": item.get("id"),
            "source": item.get("source"),
            "source_ref": item.get("source_ref"),
            "url": item.get("url"),
            "title": item.get("title"),
            "assessed_tier": item.get("assessed_tier"),
            "supports": bool(item.get("supports")),
            "agent_lens": item.get("agent_lens"),
        }
        for item in evidence
    ]
    references = [
        str(item.get("source_ref") or item.get("url") or item.get("title") or "").strip()
        for item in evidence
        if item.get("source_ref") or item.get("url") or item.get("title")
    ]
    try:
        verdict = json.loads(opportunity.get("verdict_json") or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        verdict = {}
    asset_class = opportunity.get("asset_class") or "crypto"
    thesis = opportunity.get("thesis") or ""
    key_evidence = "; ".join(references)
    key_evidence_display, rendered_evidence_ids = _serenity_evidence_display(evidence)
    return {
        "id": opportunity.get("id"),
        "symbol": opportunity.get("symbol"),
        "direction": opportunity.get("direction"),
        "thesis": thesis,
        "thesis_display": _bounded_serenity_display(
            thesis, _SERENITY_THESIS_DISPLAY_CHARS
        ),
        "confidence_score": opportunity.get("confidence_score"),
        "assessed_tier": opportunity.get("assessed_tier") or "unassessed",
        "evidence_tier": opportunity.get("evidence_tier"),
        "score": score.get("score"),
        "bucket": score.get("bucket"),
        "evidence_count": score.get("evidence_count", 0),
        "evidence_ids": [item["id"] for item in evidence_rows if item.get("id")],
        "rendered_evidence_ids": rendered_evidence_ids,
        "evidence": evidence_rows,
        "key_evidence": key_evidence,
        "key_evidence_display": key_evidence_display,
        "verdict": verdict,
        "researchability_grade": opportunity.get("researchability_grade") or "C",
        "asset_class": asset_class,
        "market_type": opportunity.get("market_type") or "spot",
        "execution_eligible": asset_class != "equity",
        "hydration_status": "available",
    }


def _hydrate_pending_serenity_context(
    layer2: dict[str, Any], layer3: dict[str, Any]
) -> None:
    """Ensure every visible Serenity carrier has its exact research projection."""
    from aletheia.storage import repositories as repo

    rows = layer3.setdefault("serenity_opportunities", [])
    by_id = {str(row.get("id")): index for index, row in enumerate(rows)}

    def mark_unavailable(pending: dict[str, Any], reason: str) -> None:
        opportunity_id = str(pending.get("opportunity_id") or "")
        pending["serenity_context_status"] = "unavailable"
        pending["serenity_context_unavailable_reason"] = reason
        projected = {
            "id": opportunity_id,
            "symbol": pending.get("symbol"),
            "direction": pending.get("direction"),
            "market_type": pending.get("market_type"),
            "thesis": "",
            "thesis_display": "",
            "key_evidence": "",
            "key_evidence_display": "",
            "evidence_ids": [],
            "rendered_evidence_ids": [],
            "evidence": [],
            "hydration_status": "unavailable",
            "unavailable_reason": reason,
        }
        if opportunity_id in by_id:
            rows[by_id[opportunity_id]] = projected
        else:
            by_id[opportunity_id] = len(rows)
            rows.append(projected)

    for pending in layer2.get("pending_signals") or []:
        if (pending.get("signal_source") or "market_observer") != "serenity":
            continue
        opportunity_id = str(pending.get("opportunity_id") or "")
        try:
            opportunity = repo.get_serenity_opportunity(opportunity_id)
        except Exception:
            mark_unavailable(pending, "serenity_context_hydration_failed")
            continue
        if opportunity is None:
            mark_unavailable(pending, "serenity_opportunity_missing")
            continue
        try:
            projected = _serenity_context_row(opportunity)
        except Exception:
            mark_unavailable(pending, "serenity_context_hydration_failed")
            continue
        pending["serenity_context_status"] = "available"
        pending.pop("serenity_context_unavailable_reason", None)
        if opportunity_id in by_id:
            rows[by_id[opportunity_id]] = projected
        else:
            by_id[opportunity_id] = len(rows)
            rows.append(projected)


def _build_layer1_snapshot() -> dict[str, Any]:
    """Current market + portfolio state. Fresh each call, never cached."""
    layer: dict[str, Any] = {}

    # Portfolio: open positions
    try:
        from aletheia.storage.repositories import (
            latest_perp_structure,
            list_all_open_positions,
        )

        all_open_pos = list_all_open_positions()
        display_open_pos = all_open_pos[:10]
        # Complete, compact identity/economics set for close/reduce matching and
        # portfolio calculations. The bounded ``positions`` list below remains
        # the presentation slice and alone receives expensive structure context.
        layer["position_authority"] = [
            {
                "id": p.get("id"),
                "symbol": p.get("symbol"),
                "market_type": p.get("market_type"),
                "direction": p.get("direction"),
                "quantity": p.get("quantity"),
                "entry_price": p.get("entry_price"),
                "current_price": p.get("current_price"),
                "unrealized_pnl": p.get("unrealized_pnl"),
                "hold_hours": _hours_since(p.get("opened_at")),
                "proposal_id": p.get("proposal_id"),
                "opportunity_id": p.get("opportunity_id"),
                "strategy_id": p.get("strategy_id"),
                "trade_school": p.get("trade_school"),
                "execution_mode": p.get("execution_mode"),
                "leverage": p.get("leverage"),
                "liquidation_price": p.get("liquidation_price"),
            }
            for p in all_open_pos
        ]
        layer["positions"] = [
            {
                "id": p.get("id"),
                "symbol": p.get("symbol"),
                "market_type": p.get("market_type"),
                "direction": p.get("direction"),
                "quantity": p.get("quantity"),
                "entry_price": p.get("entry_price"),
                "current_price": p.get("current_price"),
                "unrealized_pnl": p.get("unrealized_pnl"),
                "hold_hours": _hours_since(p.get("opened_at")),
                "proposal_id": p.get("proposal_id"),
                "opportunity_id": p.get("opportunity_id"),
                "strategy_id": p.get("strategy_id"),
                "trade_school": p.get("trade_school"),
                # Exit intent (exit-intent plan M2/M5 reback): surface the stop/target,
                # the Operator's holding horizon, and stop provenance so the Operator
                # can proactively re-author positions still on config-default risk
                # (stop_source='default') instead of letting defaults own them forever.
                "stop_price": p.get("stop_price"),
                "take_profit_price": p.get("take_profit_price"),
                "expected_horizon_hours": p.get("expected_horizon_hours"),
                "stop_source": p.get("stop_source") or "default",
                # Futures telemetry for the leverage/margin context + near-liquidation alerts.
                "leverage": p.get("leverage"),
                "liquidation_price": p.get("liquidation_price"),
                "structure_context": _position_x_context_for_instrument(
                    latest_perp_structure(p.get("symbol")), p.get("market_type")
                ),
            }
            for p in display_open_pos
        ]
        layer["positions_meta"] = {
            "total": len(all_open_pos),
            "display_count": len(display_open_pos),
            "prompt_visible_count": min(len(display_open_pos), 5),
            "prompt_overflow_count": max(len(all_open_pos) - 5, 0),
        }
    except Exception:
        layer["positions"] = []
        layer["position_authority"] = []
        layer["positions_meta"] = {
            "total": 0,
            "display_count": 0,
            "prompt_visible_count": 0,
            "prompt_overflow_count": 0,
        }

    # Account balance. Local paper is a separate cash ledger: showing an old
    # exchange cache here would make the agent misread a human balance adjustment.
    try:
        from aletheia.config.loader import load_config as _load_cfg
        _mode = _load_cfg().exchange.paper_execution_mode or "paper_internal"
        if _mode == "paper_internal":
            from aletheia.portfolio.paper_account import get_paper_account
            account = get_paper_account(event_limit=3)
            layer["balance"] = {
                "equity_usdt": account["equity_usdt"],
                "available_usdt": account["available_balance_usdt"],
                "reserved_margin_usdt": account["reserved_margin_usdt"],
                "source": "local_paper_ledger",
            }
            layer["recent_account_changes"] = [
                {"action": event["action"], "amount_usdt": event["amount_usdt"],
                 "note": event.get("note"), "at": event["created_at"]}
                for event in account["events"]
                if event["action"].startswith("manual_")
            ]
        else:
            from aletheia.portfolio.exchange_sync import get_cached_state
            state = get_cached_state()
            if state and state.get("age_seconds", 999) < 300:
                layer["balance"] = {
                    "equity_usdt": state.get("equity_usdt") or state.get("total_equity_usdt"),
                    "available_usdt": state.get("available_usdt") or state.get("total_available_usdt"),
                }
            else:
                layer["balance"] = None
            layer["recent_account_changes"] = []
    except Exception:
        layer["balance"] = None
        layer["recent_account_changes"] = []

    # Today's realized PnL (scoped to active execution mode so PI sees
    # the same figure as the paper dashboard, not a cross-mode total).
    try:
        from aletheia.config.loader import load_config as _load_cfg
        _mode = _load_cfg().exchange.paper_execution_mode or "paper_internal"
        today_start = f"{datetime.now(timezone.utc).date().isoformat()}T00:00:00+00:00"
        today_outcomes = eligible_strategy_outcomes(
            execution_mode=_mode, closed_after=today_start,
        )
        layer["today_pnl"] = (
            round(sum(float(row["realized_pnl"]) for row in today_outcomes), 2)
            if today_outcomes else None
        )
        layer["today_pnl_status"] = "available" if today_outcomes else "insufficient"
        layer["today_pnl_semantics"] = "eligible_strategy_outcomes"
        layer["outcome_semantics_version"] = OUTCOME_SEMANTICS_VERSION
    except Exception:
        layer["today_pnl"] = None

    # Market regime
    try:
        from aletheia.storage.repositories import get_latest_market_regime
        regime = get_latest_market_regime()
        if regime:
            layer["regime"] = {
                "state": regime.get("state"),
                "confidence": regime.get("confidence"),
                "long_bias": regime.get("long_bias"),
                "short_bias": regime.get("short_bias"),
            }
        else:
            layer["regime"] = None
    except Exception:
        layer["regime"] = None

    # Technical features (bounded market presentation slice). Exact pending
    # candidates are hydrated separately after Layer 2 is built.
    try:
        from aletheia.storage.repositories import list_universe_feature_snapshots
        snaps = list_universe_feature_snapshots(limit=50)
        seen: set[tuple[str, str, str]] = set()
        features: list[dict] = []
        feature_index: dict[str, dict] = {}
        for s in snaps:
            sym = s.get("symbol", "")
            identity = (
                str(s.get("venue") or ""),
                _normalize_market_type(s.get("market_type")),
                str(sym),
            )
            if identity in seen:
                continue
            seen.add(identity)
            feature = _feature_context_row(s)
            feature_index[_feature_identity_key(*identity)] = feature
            if len(features) < 10:
                features.append(feature)
        layer["features"] = features
        layer["feature_index"] = feature_index
    except Exception:
        layer["features"] = []
        layer["feature_index"] = {}

    # Open opportunities (what market_observer has flagged — System Operator was blind to these)
    try:
        from aletheia.storage.repositories import (
            _select_all, get_universe_member, latest_perp_structure,
        )
        opps = _select_all(
            "opportunities",
            where="status = 'open' AND asset_class = 'crypto'",
            order="created_at DESC",
            limit=10,
        )
        # Thesis evidence (P1a 2026-07-02): per-(scanner_family, direction) live edge
        # from settled forward outcomes, so the PI can weigh each candidate against
        # the actual track record of its scanner family + direction — the missing
        # decision-time signal the learning loop already computed but never surfaced.
        _edge_map: dict[tuple, dict] = {}
        try:
            from aletheia.storage.repositories import family_direction_edge
            for _e in family_direction_edge(horizon="24h", within_days=7):
                _edge_map[(
                    _e["scanner_family"],
                    _e["direction"],
                    _e.get("market_type") or "spot",
                )] = {
                    "n": _e["n"], "win_rate": _e["win_rate"], "avg_return": _e["avg_return"],
                }
        except Exception:
            _edge_map = {}

        def _family_edge_for(o: dict) -> dict | None:
            fam = o.get("scanner_family")
            d = (o.get("direction_bias") or "long")
            if d not in ("long", "short"):
                d = "long"
            raw_market = str(o.get("market") or "spot").lower()
            market_type = (
                "perp"
                if raw_market in {"perp", "futures", "swap", "usdt_futures"}
                else "spot"
            )
            return _edge_map.get((fam, d, market_type))

        def _opportunity_feature(o: dict) -> dict | None:
            member = (
                get_universe_member(o.get("universe_member_id"))
                if o.get("universe_member_id") else None
            )
            return _candidate_feature(
                layer.get("feature_index") or {},
                symbol=o.get("symbol"),
                market_type=o.get("market"),
                asset_id=member.get("asset_id") if member else None,
            )

        layer["opportunities"] = [
            {
                # R0 fix: carry the opportunity id so the System Operator's pick can be
                # matched back to its source opportunity and the proposal records
                # a real opportunity_id (not None). Without this the whole
                # attribution chain is severed (LEARNING_LOOP_AUDIT_2026-06-26 §R0).
                "id": o.get("id"),
                "linked_strategy_id": o.get("linked_strategy_id"),
                "symbol": o.get("symbol"),
                # Instrument type (spot | usdt_futures) so the PI sees what the
                # opportunity is for and can choose market_type informedly, and so
                # _attach_opportunity_context can inherit it (no hardcoded perp).
                "market": o.get("market"),
                "direction_bias": o.get("direction_bias"),
                "confidence": o.get("confidence"),
                "confidence_score": o.get("confidence_score"),
                "scanner_family": o.get("scanner_family"),
                # Per-(scanner_family, direction) live edge from settled forward
                # outcomes (thesis evidence). None when the family has no settled
                # outcomes in the 7d window yet.
                "family_edge": _family_edge_for(o),
                "thesis": (o.get("thesis") or "")[:220],
                # market_observer's LLM writes its most specific, data-cited output
                # here; the PI was previously blind to it (only the truncated thesis
                # surfaced). Expose the top observations so the decision can cite them.
                "key_observations": _top_observations(o.get("key_observations")),
                "evidence_manifest": (
                    _opportunity_evidence_from_metadata(o.get("metadata"))
                    or {"status": "unavailable"}
                ),
                "technical_context": _opportunity_feature(o),
                "structure_context": _position_x_context_for_instrument(
                    latest_perp_structure(o.get("symbol")), o.get("market")
                ),
            }
            for o in opps
        ]
    except Exception:
        layer["opportunities"] = []

    # Factor feedback status (which scanner+direction combos are blocked/preferred)
    try:
        from aletheia.analytics.factor_feedback import build_factor_feedback
        all_factors = build_factor_feedback(limit=200, min_trades=3)
        layer["factor_feedback"] = [
            {
                "factor_key": f.get("factor_key"),
                "group_type": f.get("group_type"),
                "status": f.get("status"),
                "win_rate": f.get("win_rate"),
                "expectancy": f.get("expectancy"),
                "trade_count": f.get("trade_count"),
            }
            for f in all_factors
            if f.get("status") in ("blocked_candidate", "preferred", "paper_failing")
        ][:10]
    except Exception:
        layer["factor_feedback"] = []

    # Risk state
    try:
        from aletheia.config.loader import load_config
        cfg = load_config()
        layer["risk"] = {
            # kill_switch_active lives on RuntimeConfig, not RiskConfig. The old
            # cfg.risk.kill_switch_active raised AttributeError, silently caught
            # below → the entire risk block was {} and the prompt showed
            # "max_daily_loss=?%, max_positions=?", blinding the System Operator to its
            # own risk limits (2026-06-23 profit-loop audit, freeze exception).
            "kill_switch": cfg.runtime.kill_switch_active,
            "max_daily_loss_pct": cfg.risk.max_daily_loss_pct,
            "max_open_positions": cfg.risk.max_open_positions_demo,
        }
    except Exception:
        layer["risk"] = {}

    # Data health — lets System Operator reason about its own environment quality
    layer["data_health"] = _get_data_health_summary()

    # Futures: leverage ceilings + account leverage ratio + margin headroom so the
    # System Operator chooses spot/perp and a leverage level against real limits.
    try:
        from aletheia.config.loader import load_config
        layer["futures"] = _build_futures_context(layer, load_config())
    except Exception:
        layer["futures"] = {}

    return layer


def _build_futures_context(layer: dict[str, Any], cfg) -> dict[str, Any]:
    """Leverage limits + account leverage ratio + margin headroom for the System Operator.

    Best-effort: any failure yields a partial/empty dict so the decision path never
    breaks on missing futures telemetry. Symbols come from the already-built feature +
    position lists in ``layer`` (no extra query). ``available_margin_pct`` is the share
    of equity NOT tied up as futures margin (used_margin = Σ notional/leverage).
    """
    fut: dict[str, Any] = {}
    authority_positions = (
        layer.get("position_authority") or layer.get("positions") or []
    )
    try:
        from aletheia.trading.instruments import get_asset_tier, get_max_leverage
        symbols: list[str] = []
        for f in (layer.get("features") or []):
            s = f.get("symbol")
            if s and s not in symbols:
                symbols.append(s)
        for p in authority_positions:
            s = p.get("symbol")
            if s and s not in symbols:
                symbols.append(s)
        fut["leverage_limits"] = {
            s: {
                "tier": get_asset_tier(s),
                "max_demo": get_max_leverage(s, False, cfg),
                "max_live": get_max_leverage(s, True, cfg),
            }
            for s in symbols[:12]
        }
    except Exception:
        fut["leverage_limits"] = {}

    try:
        from aletheia.risk.engine import check_account_leverage
        acct = check_account_leverage(cfg)
        ratio = acct.get("ratio")
        fut["account_leverage_ratio"] = round(ratio, 2) if ratio is not None else None
        fut["account_leverage_cap"] = acct.get("cap")
    except Exception:
        fut["account_leverage_ratio"] = None
        fut["account_leverage_cap"] = None

    # Available margin %: equity not consumed by futures margin (notional / leverage).
    try:
        balance = layer.get("balance") or {}
        equity = balance.get("equity_usdt")
        used_margin = 0.0
        for p in authority_positions:
            qty = float(p.get("quantity") or 0)
            price = float(p.get("current_price") or p.get("entry_price") or 0)
            lev = float(p.get("leverage") or 1) or 1.0
            used_margin += abs(qty * price) / lev
        if equity and equity > 0:
            fut["available_margin_pct"] = round(max(0.0, 1.0 - used_margin / equity) * 100, 1)
        else:
            fut["available_margin_pct"] = None
    except Exception:
        fut["available_margin_pct"] = None

    # Positions within 15% of their estimated liquidation price.
    near: list[dict] = []
    try:
        for p in authority_positions:
            liq = p.get("liquidation_price")
            cur = p.get("current_price") or p.get("entry_price")
            if not liq or not cur:
                continue
            gap_pct = abs(cur - liq) / cur * 100
            if gap_pct <= 15.0:
                near.append({
                    "symbol": p.get("symbol"),
                    "liquidation_price": liq,
                    "gap_pct": round(gap_pct, 1),
                })
    except Exception:
        pass
    fut["positions_near_liquidation"] = near

    return fut


# ── Layer 2: Short-term Memory (7-day window) ────────────────────────────────


def _build_layer2_short_term() -> dict[str, Any]:
    """Recent trade outcomes + pattern performance (7-day sliding window)."""
    layer: dict[str, Any] = {}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

    # Reflection: last decision outcome
    try:
        from aletheia.agent_shell.reflection import get_last_reflection
        layer["reflection"] = get_last_reflection()
    except Exception:
        layer["reflection"] = None

    # Recent closed positions — aggregate stats
    try:
        recent = eligible_strategy_outcomes(closed_after=cutoff)

        if recent:
            wins = [p for p in recent if (p.get("realized_pnl") or 0) > 0]
            losses = [p for p in recent if (p.get("realized_pnl") or 0) <= 0]
            total_pnl = sum(p.get("realized_pnl", 0) for p in recent)
            avg_return = total_pnl / len(recent) if recent else 0

            layer["outcome_stats"] = {
                "total_trades": len(recent),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": round(len(wins) / len(recent) * 100, 1) if recent else 0,
                "total_pnl": round(total_pnl, 2),
                "avg_pnl_per_trade": round(avg_return, 2),
                "worst_loss": round(min((p.get("realized_pnl", 0) for p in recent), default=0), 2),
                "best_win": round(max((p.get("realized_pnl", 0) for p in recent), default=0), 2),
                "outcome_semantics_version": OUTCOME_SEMANTICS_VERSION,
            }

            # Per-symbol breakdown (top 5 by trade count)
            from collections import Counter
            sym_counts = Counter(p.get("symbol") for p in recent)
            top_syms = [s for s, _ in sym_counts.most_common(5)]
            per_symbol: list[dict] = []
            for sym in top_syms:
                sym_trades = [p for p in recent if p.get("symbol") == sym]
                sym_wins = [p for p in sym_trades if (p.get("realized_pnl") or 0) > 0]
                sym_pnl = sum(p.get("realized_pnl", 0) for p in sym_trades)
                per_symbol.append({
                    "symbol": sym,
                    "trades": len(sym_trades),
                    "wins": len(sym_wins),
                    "pnl": round(sym_pnl, 2),
                })
            layer["per_symbol"] = per_symbol
        else:
            layer["outcome_stats"] = None
            layer["per_symbol"] = []
            layer["outcome_status"] = "insufficient"

    except Exception:
        layer["outcome_stats"] = None
        layer["per_symbol"] = []

    # Active strategies performance (7-day)
    try:
        from aletheia.storage.repositories import list_strategies
        strategies = list_strategies(limit=20)
        # strategy_records column is `state`, not `status` — the old s.get("status")
        # always returned None so the filter matched nothing and the System Operator saw
        # "(none active)" while 4 strategies were paper_running (2026-06-23 audit).
        active = [s for s in strategies if s.get("state") in ("paper_running", "active")]
        # S-Bug-C (LEARNING_LOOP_AUDIT): strategy_records has NO win_rate/total_pnl/
        # trade_count columns, so the System Operator saw None/'?' for every strategy. Real
        # KPIs come from build_strategy_kpis() (computed live from fills + positions).
        from aletheia.analytics.trade_stats import build_strategy_kpis
        kpis = build_strategy_kpis()  # {strategy_id: {trade_count, win_rate, total_pnl}}
        strat_perf: list[dict] = []
        for strat in active[:5]:
            sid = strat.get("id")
            k = kpis.get(sid, {}) if sid else {}
            strat_perf.append({
                "name": strat.get("name", "unnamed"),
                "win_rate": k.get("win_rate"),
                "total_pnl": k.get("total_pnl"),
                "trade_count": k.get("trade_count", 0),
            })
        layer["strategies"] = strat_perf
    except Exception:
        layer["strategies"] = []

    # Trades & Positions analytics — the same closed-position analytics surfaced
    # in the UI, compressed for System Operator consumption.
    try:
        from aletheia.analytics.trade_stats import build_trade_stats
        trade_stats = build_trade_stats()
        layer["trade_analytics"] = {
            "outcome_status": trade_stats.get("outcome_status", "insufficient"),
            "outcome_semantics_version": trade_stats.get("outcome_semantics_version"),
            "totals": trade_stats.get("totals", {}),
            "by_source": (trade_stats.get("by_source") or [])[:4],
            "by_family": sorted(
                trade_stats.get("by_family") or [],
                key=lambda x: x.get("trade_count", 0),
                reverse=True,
            )[:6],
            "hold_distribution": trade_stats.get("hold_distribution") or [],
        }
    except Exception:
        layer["trade_analytics"] = None

    # Trading Memo — structured insights from historical performance analysis
    try:
        from aletheia.analytics.insight_synthesizer import get_latest_memo, render_memo_for_observer
        memo = get_latest_memo()
        if memo:
            layer["trading_memo"] = {
                "generated_at": memo.get("generated_at"),
                "sample_size": memo.get("sample_size"),
                "direction_summary": memo.get("direction_summary"),
                "regime_playbook": memo.get("regime_playbook", [])[:3],
                "anti_patterns": memo.get("anti_patterns", [])[:3],
                "key_insight": memo.get("key_insight", ""),
                "rendered": render_memo_for_observer(memo),
            }
        else:
            layer["trading_memo"] = None
    except Exception:
        layer["trading_memo"] = None

    # Reasoning quality feedback — rolling pattern win rates from historical proposals
    try:
        from aletheia.analytics.reasoning_feedback import get_latest_reasoning_feedback, render_feedback_for_pi
        fb = get_latest_reasoning_feedback()
        layer["reasoning_feedback"] = render_feedback_for_pi(fb) if fb else None
    except Exception:
        layer["reasoning_feedback"] = None

    # Conviction calibration — actual vs expected win rate per conviction bucket
    try:
        from aletheia.analytics.confidence_calibration import get_latest_calibration_report, render_calibration_for_pi
        cal = get_latest_calibration_report()
        layer["calibration_feedback"] = render_calibration_for_pi(cal) if cal else None
    except Exception:
        layer["calibration_feedback"] = None

    # Ranker preview — useful analysis, but not execution authority. A candidate
    # becomes executable only after the durable pending queue below contains the
    # exact same opportunity/source/symbol/direction/market identity.
    eligible_ranker_candidates: list[dict[str, Any]] = []
    try:
        from aletheia.agents.paper_trader_ranker import build_paper_candidates
        candidates = build_paper_candidates(limit=30)
        ranker_candidates = [
            {
                "opportunity_id": c.get("opportunity_id"),
                "signal_source": "market_observer",
                "symbol": c.get("symbol"),
                "direction": c.get("direction"),
                "trade_score": c.get("trade_score"),
                "skip_reason": c.get("skip_reason"),
                "scanner_family": c.get("scanner_family"),
                "strategy_id": c.get("strategy_id") or c.get("linked_strategy_id"),
                "market_type": c.get("market_type") or c.get("market"),
                "valid_until": c.get("valid_until"),
            }
            for c in candidates
        ]
        eligible_ranker_candidates = [
            c for c in ranker_candidates if not c.get("skip_reason")
        ]
        excluded_candidates = [c for c in ranker_candidates if c.get("skip_reason")]
        layer["excluded_candidates_summary"] = _excluded_candidates_summary(excluded_candidates)
    except Exception:
        layer["excluded_candidates_summary"] = []

    # Phase 4b: Pending signals awaiting System Operator evaluation. This is an actual
    # unresolved queue view: final decisions are excluded, duplicates are removed
    # before the visible limit, and oldest work is served first.
    try:
        pending_signals, pending_queue = _build_pending_signal_queue()
        layer["pending_signals"] = pending_signals
        layer["pending_queue"] = pending_queue
    except Exception as exc:
        logger.warning("pending signal queue unavailable: %s", exc)
        layer["pending_signals"] = []
        layer["pending_queue"] = {
            "read_status": "error",
            "unresolved_total": None,
            "visible_count": 0,
            "overflow_count": None,
            "oldest_at": None,
            "max_age_minutes": _PENDING_SIGNAL_MAX_AGE_MINUTES,
            "expired_unconsumed_rows": None,
            "oldest_expired_at": None,
        }

    pending_identities = {
        identity
        for row in layer["pending_signals"]
        if (identity := _pending_identity_key(row)) is not None
    }
    executable_candidates = [
        row
        for row in eligible_ranker_candidates
        if _pending_identity_key(row) in pending_identities
    ]
    analysis_only_candidates = [
        row
        for row in eligible_ranker_candidates
        if _pending_identity_key(row) not in pending_identities
    ]
    layer["executable_candidates"] = _dedupe_pending_identity(
        executable_candidates, limit=8
    )
    layer["analysis_only_candidates"] = _dedupe_pending_identity(
        analysis_only_candidates, limit=8
    )
    # Compatibility alias: ranker candidates remain available for non-entry
    # attribution only, and are explicitly the non-authoritative preview.
    layer["ranker_candidates"] = layer["analysis_only_candidates"]
    _record_candidate_supply_snapshot(layer)

    return layer


# ── Layer 3: Long-term Wisdom ────────────────────────────────────────────────


def _build_layer3_wisdom() -> dict[str, Any]:
    """Lessons + guard candidates — natural language, not truncated."""
    layer: dict[str, Any] = {}

    # Active lessons (full text, not truncated)
    try:
        from aletheia.storage.repositories import list_knowledge_items
        # L-Bug-A (LEARNING_LOOP_AUDIT): the old filter ('lesson','accepted_lesson',
        # 'trading_guard') matched ZERO rows — none of those item_types have a
        # producer, so Layer3 lessons was permanently empty. Real attributed lessons
        # live in trade_case_study (closed-trade cases with full entry→exit chain,
        # which the R0 opportunity_id fix now lets complete). Query by item_type
        # directly so the limit window isn't swallowed by high-frequency rows.
        case_items = list_knowledge_items(item_type="trade_case_study", limit=7)
        lessons: list[dict] = []
        for item in case_items:
            meta = item.get("metadata") or {}
            raw_content = item.get("content") or item.get("summary") or ""
            if not raw_content:
                # L-Bug-D fix: DB content=NULL; actual content lives on disk at file_path.
                # Read frontmatter (outcome/pnl/factor_key) + body prefix to give PI real data.
                fpath = item.get("file_path")
                if fpath:
                    try:
                        from pathlib import Path as _Path
                        from aletheia.knowledge.frontmatter import parse_frontmatter as _pfm
                        _meta, _body = _pfm(_Path(fpath))
                        parts: list[str] = []
                        if _meta:
                            if _meta.get("outcome"):
                                parts.append(
                                    f"outcome={_meta['outcome']} pnl={_meta.get('realized_pnl_pct', '?')}%"
                                )
                            if _meta.get("factor_key"):
                                parts.append(
                                    f"factor={_meta['factor_key']} scanner={_meta.get('scanner_family', '?')}"
                                )
                            if _meta.get("close_reason"):
                                parts.append(f"close={_meta['close_reason']}")
                        if _body:
                            parts.append(_body[:150].strip())
                        raw_content = " | ".join(parts) if parts else (item.get("title") or "")
                    except Exception:
                        raw_content = item.get("title") or ""
            lessons.append({
                "type": item.get("item_type"),
                "symbol": meta.get("symbol"),
                "content": raw_content[:300],
                "source": meta.get("source_trade"),
            })
        layer["lessons"] = lessons
    except Exception:
        layer["lessons"] = []

    # Recent guard candidates (from self-evolution Tier 1)
    try:
        from aletheia.storage.repositories import list_knowledge_items
        # L-Bug-A' (LEARNING_LOOP_AUDIT): the old list_knowledge_items(limit=30)
        # scanned the whole table newest-first, and that window was permanently
        # occupied by high-frequency system_action rows, so guard_candidate never
        # appeared. Query by item_type directly.
        items = list_knowledge_items(item_type="guard_candidate", limit=3)
        guards: list[dict] = []
        for item in items:
            meta = item.get("metadata") or {}
            raw_content = item.get("content") or item.get("summary") or item.get("title") or ""
            if not raw_content:
                # Phase 3b: guard_candidate content lives on disk; DB content may be NULL.
                fpath = item.get("file_path")
                if fpath:
                    try:
                        from pathlib import Path as _Path
                        from aletheia.knowledge.frontmatter import parse_frontmatter as _pfm
                        _meta, _body = _pfm(_Path(fpath))
                        parts: list[str] = []
                        if _meta:
                            if _meta.get("outcome"):
                                parts.append(
                                    f"outcome={_meta['outcome']} "
                                    f"net={_meta.get('net_return_pct', '?')}%"
                                )
                            if _meta.get("scanner_family"):
                                parts.append(f"scanner={_meta['scanner_family']}")
                        if _body:
                            parts.append(_body[:150].strip())
                        raw_content = " | ".join(parts) if parts else (item.get("title") or "")
                    except Exception:
                        raw_content = item.get("title") or ""
            guards.append({
                "symbol": meta.get("symbol"),
                "content": raw_content[:200],
            })
        layer["guard_candidates"] = guards
    except Exception:
        layer["guard_candidates"] = []

    # Serenity research opportunities (most recent active, for System Operator consumption)
    try:
        from aletheia.storage.repositories import list_serenity_opportunities

        opps = list_serenity_opportunities(status="active", limit=10)
        serenity_opps: list[dict] = []
        for o in opps[:5]:
            try:
                serenity_opps.append(_serenity_context_row(o))
            except Exception:
                serenity_opps.append({
                    "id": o.get("id"),
                    "symbol": o.get("symbol"),
                    "direction": o.get("direction"),
                    "thesis": o.get("thesis") or "",
                    "assessed_tier": o.get("assessed_tier") or "unassessed",
                    "evidence_ids": [],
                    "evidence": [],
                })
        layer["serenity_opportunities"] = serenity_opps
    except Exception:
        layer["serenity_opportunities"] = []

    # L-Bug-B fix: inject APPLIED lessons (evidence-reviewed + policy-stamped),
    # not proposed (unreviewed raw output). Status flow: proposed → accepted → applied.
    # lesson_reviewer accepts then immediately marks_applied in the same run, so
    # 'accepted' is a transient state; 'applied' is the stable post-review population.
    # These lessons have passed: deterministic floor (≥20 settled outcomes) + LLM judge.
    try:
        from aletheia.learning.lessons import LessonStore
        from datetime import datetime, timezone
        _now_iso = datetime.now(timezone.utc).isoformat()
        # #18: exclude expired lessons — the risk engine skips them (engine.py:416),
        # so the prompt must not present a stale lesson as active guidance. Fetch a
        # wider set, drop expired, then cap at 5 (matches the previous surface size).
        _applied = LessonStore().list_all(status="applied", limit=20)
        ps = [l for l in _applied
              if not (getattr(l, "expires_at", None) and _now_iso > l.expires_at)
              and not (getattr(l, "dry_run_until", None) and _now_iso < l.dry_run_until)][:5]
        layer["ps_lessons"] = [
            {"summary": l.summary, "severity": l.severity, "category": l.category}
            for l in ps
        ]
    except Exception:
        layer["ps_lessons"] = []

    return layer


# ── data health ─────────────────────────────────────────────────────────────


def _get_data_health_summary() -> dict[str, Any]:
    """Build a concise data-health dict for System Operator context injection."""
    try:
        from aletheia.storage.repositories import get_active_universe_members, get_universe_asset
        from aletheia.storage.sqlite import get_db
        from aletheia.config.loader import load_config

        cfg = load_config()
        min_bars = cfg.historical_data.min_bars_for_backtest
        db = get_db()

        members = get_active_universe_members()
        total_members = len(members)
        ready_count = 0
        for m in members:
            sym = m.get("symbol", "")
            if not sym:
                continue
            asset = get_universe_asset(m.get("asset_id")) or {}
            venue = asset.get("venue", "")
            market_type = m.get("market_type") or asset.get("market_type", "")
            bars = db.execute(
                "SELECT COUNT(*) FROM market_ohlcv "
                "WHERE exchange=? AND symbol=? AND market_type=? AND timeframe='1h'",
                (venue, sym, market_type),
            ).fetchone()[0]
            if bars >= min_bars:
                ready_count += 1

        ohlcv_ready = ready_count == total_members and total_members > 0

        # Signal effectiveness (7d, 24h horizon)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        row = db.execute(
            """SELECT COUNT(*),
                      SUM(CASE WHEN net_return_pct > COALESCE(min_tradable_edge_pct, 0)
                               THEN 1 ELSE 0 END)
               FROM universe_forward_outcomes
               WHERE status = 'settled' AND horizon = '24h'
                 AND settled_at >= ? AND net_return_pct IS NOT NULL
                 AND ABS(net_return_pct) <= 100
                 AND direction IN ('long', 'short')""",
            (cutoff,),
        ).fetchone()
        settled, wins = row if row else (0, None)
        win_rate = round((wins or 0) / settled * 100, 1) if settled else None

        if win_rate is None:
            signal_status = "insufficient_data"
        elif win_rate < 40:
            signal_status = "degraded"
        elif win_rate >= 55:
            signal_status = "healthy"
        else:
            signal_status = "neutral"

        return {
            "ohlcv_ready": ohlcv_ready,
            "members_with_full_coverage": ready_count,
            "total_members": total_members,
            "signal_win_rate_24h": win_rate,
            "signal_status": signal_status,
        }
    except Exception as e:
        logger.debug("data_health_summary failed: %s", e)
        return {
            "ohlcv_ready": False,
            "members_with_full_coverage": 0,
            "total_members": 0,
            "signal_win_rate_24h": None,
            "signal_status": "unknown",
        }


# ── helpers ──────────────────────────────────────────────────────────────────


def _hours_since(iso_str: str | None) -> float | None:
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        return round(delta.total_seconds() / 3600, 1)
    except (ValueError, TypeError):
        return None
