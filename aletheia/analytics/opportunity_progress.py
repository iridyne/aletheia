"""Authoritative read model for the current Opportunity-to-position progress.

This module only reads durable lifecycle/filter facts plus bounded in-process
Ranker wake telemetry.  It never re-runs the Ranker, changes Opportunity state,
or infers that an open Opportunity passed filters without evidence.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from typing import Any


_RANKER_DELIVERY_EPOCH = "opportunity_ranker_delivery/v2"
_CARRIER_CONTINUITY_EPOCH = "opportunity_carrier_continuity/v1"
_RANKER_CANDIDATE_LIMIT = 8
_SIGNAL_COOLDOWN_MINUTES = 30


def _parse_json_object(raw: object) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(str(raw or "{}"))
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _parse_json_list(raw: object) -> list[Any]:
    if isinstance(raw, list):
        return raw
    try:
        parsed = json.loads(str(raw or "[]"))
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []


def _parse_iso(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_seconds_until(value: object, now: datetime) -> int | None:
    parsed = _parse_iso(value)
    return int((parsed - now).total_seconds()) if parsed else None


def _earlier_deadline(first: object, second: object) -> object:
    """Return the original value for the earlier parseable deadline.

    Old defer observations predate the carrier-TTL cap and can therefore carry
    a window deadline beyond the Opportunity's own ``valid_until``.  Read models
    must not repeat that obsolete promise even though the write path is fixed.
    """
    first_dt = _parse_iso(first)
    second_dt = _parse_iso(second)
    if first_dt and second_dt:
        return first if first_dt <= second_dt else second
    return first if first_dt else second


def _entry_wait_reason(reason_code: str | None) -> tuple[str, str]:
    """Return owner-facing reason and next trigger for a durable Operator wait."""
    code = str(reason_code or "")
    if code == "candidate_technical_snapshot_unavailable":
        return (
            "Exact candidate technical context is unavailable; waiting for a feature refresh.",
            "feature_refresh",
        )
    if code == "candidate_technical_snapshot_incomplete":
        return (
            "Candidate technical context is incomplete; waiting for a complete feature refresh.",
            "feature_refresh",
        )
    if code == "candidate_technical_snapshot_stale":
        return (
            "Candidate technical context is stale; waiting for fresh market facts.",
            "feature_refresh",
        )
    if code.startswith("direct_skip_not_confirmed:unverified_adverse_reason"):
        return (
            "The Operator did not confirm a trade, and its adverse claim was not verified; "
            "waiting for material context to change.",
            "material_context_change",
        )
    if code.startswith("direct_skip_not_confirmed"):
        return (
            "The Operator did not confirm a trade, and the proposed skip lacked "
            "candidate-specific adverse evidence; waiting for new facts.",
            "material_context_change",
        )
    # Phase C (2026-07-18): non-terminal conviction/debate defers. Owner-facing
    # text tells the user this is a healthy pause — the candidate stays queued
    # and material fingerprint changes reopen evaluation.
    if code == "operator_low_conviction":
        return (
            "The Operator considered a trade but conviction was below the minimum; "
            "waiting for material context to change (new feature snapshot, "
            "regime tag, or catalyst).",
            "material_context_change",
        )
    if code == "debate_not_confirmed":
        return (
            "The Operator considered a trade but the bull/bear debate could not "
            "confirm the direction; waiting for material context to change.",
            "material_context_change",
        )
    return (
        "The candidate is waiting for the next Operator evaluation.",
        "operator_evaluation",
    )


def _latest_ranker_run(db) -> dict[str, Any] | None:
    row = db.execute(
        "SELECT id,trigger_type,started_at,ended_at,status,outputs_created,errors "
        "FROM agent_runs WHERE agent_name='paper_trader' "
        "ORDER BY julianday(started_at) DESC,rowid DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    return {
        "run_id": row["id"],
        "trigger_type": row["trigger_type"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "status": row["status"],
        "outputs": _parse_json_list(row["outputs_created"]),
        "errors": _parse_json_list(row["errors"]),
    }


def _ranker_wake_status() -> dict[str, Any]:
    try:
        from aletheia.agent_runtime.scheduler import paper_trader_wake_status

        return paper_trader_wake_status()
    except Exception:
        return {
            "scheduler_active": False,
            "pending": False,
            "pending_sources": [],
            "next_run_at": None,
        }


def _ranker_reason(filter_name: str | None) -> tuple[str, str, str, str]:
    """Map a durable Ranker fact to state, stage, explanation, and next trigger."""
    name = str(filter_name or "")
    if name == "signal_cooldown":
        return (
            "ranker_cooldown",
            "ranker_selection",
            "The same instrument and direction entered the Operator queue within the "
            "30 minute signal cooldown.",
            "cooldown_expiry",
        )
    if name == "active_operator_carrier":
        return (
            "ranker_carrier_active",
            "ranker_selection",
            "The same instrument, direction, and scanner setup already has an active "
            "Operator queue carrier; this snapshot generation was not enqueued again.",
            "operator_carrier_terminal_or_source_refresh",
        )
    if name == "signal_queue_capacity":
        return (
            "ranker_backpressure",
            "ranker_selection",
            "The Operator queue is at its five-carrier admission capacity.",
            "operator_queue_slot",
        )
    if name == "signal_batch_limit":
        return (
            "ranker_batch_limit",
            "ranker_selection",
            "Higher-ranked candidates filled this Ranker run's three-signal batch.",
            "next_ranker_run",
        )
    if name == "open_position_duplicate":
        return (
            "ranker_position_duplicate",
            "ranker_selection",
            "An open position already exists for this symbol.",
            "position_close",
        )
    if name == "duplicate_symbol":
        return (
            "ranker_duplicate",
            "ranker_selection",
            "A higher-ranked Opportunity for the same symbol owns this selection pass.",
            "next_ranker_run",
        )
    if name == "operator_already_evaluated":
        return (
            "ranker_already_evaluated",
            "ranker_selection",
            "The Operator already produced a terminal decision for this Opportunity.",
            "opportunity_terminalization",
        )
    if name:
        return (
            "ranker_filter_wait",
            "ranker_filter",
            f"Ranker stopped at the deterministic {name} filter.",
            "ranker_fact_change",
        )
    return (
        "ranker_pending",
        "ranker",
        "The Opportunity is open but has no durable Ranker evaluation yet.",
        "ranker_wake",
    )


def _cooldown_retry_at(db, *, symbol: str, direction: str | None) -> str | None:
    if not symbol or not direction:
        return None
    row = db.execute(
        "SELECT MAX(created_at) AS latest_at FROM pi_decisions "
        "WHERE action='signal_pending' AND symbol=? AND direction=?",
        (symbol, direction),
    ).fetchone()
    latest = _parse_iso(row["latest_at"] if row else None)
    if not latest:
        return None
    return (latest + timedelta(minutes=_SIGNAL_COOLDOWN_MINUTES)).isoformat()


def _latest_filter_run(
    db, opportunity_id: str, created_at: str
) -> tuple[str | None, list[dict]]:
    latest = db.execute(
        "SELECT run_id,evaluated_at FROM filter_decisions WHERE opportunity_id=? "
        "AND julianday(evaluated_at)>=julianday(?) "
        "ORDER BY julianday(evaluated_at) DESC,rowid DESC LIMIT 1",
        (opportunity_id, created_at),
    ).fetchone()
    if not latest:
        return None, []
    run_id = latest["run_id"]
    if run_id is None:
        rows = db.execute(
            "SELECT filter_name,stage,sequence_no,decision,reason_code,evaluated_at "
            "FROM filter_decisions WHERE opportunity_id=? AND run_id IS NULL "
            "AND evaluated_at=? ORDER BY stage,sequence_no,rowid",
            (opportunity_id, latest["evaluated_at"]),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT filter_name,stage,sequence_no,decision,reason_code,evaluated_at "
            "FROM filter_decisions WHERE opportunity_id=? AND run_id=? "
            "ORDER BY CASE stage WHEN 'paper_ranker' THEN 0 "
            "WHEN 'paper_trader_selection' THEN 1 ELSE 2 END,sequence_no,rowid",
            (opportunity_id, run_id),
        ).fetchall()
    return run_id, [dict(row) for row in rows]


def _latest_paper_ranker_passes(
    db, opportunity_id: str, created_at: str
) -> list[str]:
    """Return the latest durable gate passes even when selection ran later."""
    latest = db.execute(
        "SELECT run_id,evaluated_at FROM filter_decisions WHERE opportunity_id=? "
        "AND stage='paper_ranker' AND julianday(evaluated_at)>=julianday(?) "
        "ORDER BY julianday(evaluated_at) DESC,rowid DESC LIMIT 1",
        (opportunity_id, created_at),
    ).fetchone()
    if not latest:
        return []
    if latest["run_id"] is None:
        rows = db.execute(
            "SELECT filter_name,decision FROM filter_decisions "
            "WHERE opportunity_id=? AND stage='paper_ranker' AND run_id IS NULL "
            "AND evaluated_at=? ORDER BY sequence_no,rowid",
            (opportunity_id, latest["evaluated_at"]),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT filter_name,decision FROM filter_decisions "
            "WHERE opportunity_id=? AND stage='paper_ranker' AND run_id=? "
            "ORDER BY sequence_no,rowid",
            (opportunity_id, latest["run_id"]),
        ).fetchall()
    return [
        row["filter_name"]
        for row in rows
        if row["decision"] in {"passed", "passed_with_penalty"}
    ]


def _open_ranker_candidates(db, *, now: datetime, window_start: str) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT id,symbol,market,direction_bias,confidence_score,valid_until,"
        "scanner_family,created_at,last_ranked_at FROM opportunities "
        "WHERE status='open' AND julianday(created_at)>=julianday(?) "
        "AND julianday(created_at)<=julianday(?) "
        "ORDER BY julianday(created_at) ASC,rowid ASC",
        (window_start, now.isoformat()),
    ).fetchall()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        run_id, trace = _latest_filter_run(db, row["id"], row["created_at"])
        blockers = [
            fact for fact in trace
            if fact["decision"] in {"rejected", "error"}
        ]
        blocker = blockers[-1] if blockers else None
        blocking_filter = blocker["filter_name"] if blocker else None
        state, stage, reason, next_trigger = _ranker_reason(blocking_filter)
        if not trace and row["last_ranked_at"]:
            state = "ranker_evidence_unavailable"
            stage = "ranker"
            reason = "Ranker touched this Opportunity, but its latest filter facts are unavailable."
            next_trigger = "next_ranker_run"
        passed_filters = _latest_paper_ranker_passes(
            db, row["id"], row["created_at"]
        )
        retry_at = (
            _cooldown_retry_at(
                db,
                symbol=row["symbol"],
                direction=row["direction_bias"],
            )
            if blocking_filter == "signal_cooldown"
            else None
        )
        candidates.append({
            "opportunity_id": row["id"],
            "symbol": row["symbol"],
            "direction": row["direction_bias"],
            "market_type": row["market"],
            "scanner_family": row["scanner_family"],
            "trade_score": None,
            "confidence_score": row["confidence_score"],
            "status": state,
            "stage": stage,
            "reason_code": blocker["reason_code"] if blocker else (
                "ranker_evidence_unavailable" if row["last_ranked_at"] else "ranker_not_yet_evaluated"
            ),
            "reason": reason,
            "next_trigger": next_trigger,
            "queued_at": None,
            "valid_until": row["valid_until"],
            "expires_in_sec": _iso_seconds_until(row["valid_until"], now),
            "defer_until": None,
            "latest_event_at": trace[-1]["evaluated_at"] if trace else row["last_ranked_at"],
            "policy_epoch_id": _RANKER_DELIVERY_EPOCH,
            "blocking_filter": blocking_filter,
            "passed_filters": passed_filters,
            "passed_filter_count": len(passed_filters),
            "latest_ranker_run_id": run_id,
            "retry_at": retry_at,
            "retry_in_sec": _iso_seconds_until(retry_at, now),
        })
    return candidates


def _operator_candidates(db, *, now: datetime) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from aletheia.agent_shell.trading_context import _build_pending_signal_queue

    pending, queue = _build_pending_signal_queue(limit=5)
    candidates: list[dict[str, Any]] = []
    for item in pending:
        opportunity_id = item.get("opportunity_id")
        opportunity = db.execute(
            "SELECT confidence_score,valid_until,scanner_family,created_at "
            "FROM opportunities WHERE id=?",
            (opportunity_id,),
        ).fetchone() if opportunity_id else None
        ranker_run_id, _ranker_trace = (
            _latest_filter_run(db, opportunity_id, opportunity["created_at"])
            if opportunity_id and opportunity
            else (None, [])
        )
        ranker_passes = (
            _latest_paper_ranker_passes(db, opportunity_id, opportunity["created_at"])
            if opportunity_id and opportunity
            else []
        )
        event = db.execute(
            "SELECT event_kind,queue_snapshot_json,policy_epoch_id,created_at "
            "FROM operator_evaluation_events "
            "WHERE json_extract(queue_snapshot_json, '$.opportunity_id')=? "
            "AND julianday(created_at)>=julianday(?) "
            "ORDER BY julianday(created_at) DESC,rowid DESC LIMIT 1",
            (opportunity_id, item.get("at")),
        ).fetchone() if opportunity_id else None
        payload = _parse_json_object(event["queue_snapshot_json"]) if event else {}
        recorded_defer_until = payload.get("defer_until")
        # Any operator_decision_deferred_* kind counts as an active defer for the
        # owner-facing "waiting on X" tag. Phase C added
        # ``operator_decision_deferred_low_conviction`` — omitting it here made the
        # UI silently show "nothing happening" while the candidate was actually
        # deferred behind a conviction gate.
        event_kind = str(event["event_kind"]) if event else ""
        valid_until = opportunity["valid_until"] if opportunity else None
        defer_until = _earlier_deadline(recorded_defer_until, valid_until)
        active_defer = bool(
            event
            and event_kind.startswith("operator_decision_deferred_")
            and (_iso_seconds_until(defer_until, now) or 0) > 0
        )
        reason_code = str(payload.get("defer_reason") or "") if active_defer else None
        reason, next_trigger = _entry_wait_reason(reason_code)
        candidates.append({
            "opportunity_id": opportunity_id,
            "symbol": item.get("symbol"),
            "direction": item.get("direction"),
            "market_type": item.get("market_type"),
            "scanner_family": (
                opportunity["scanner_family"]
                if opportunity and opportunity["scanner_family"]
                else item.get("scanner_family")
            ),
            "trade_score": item.get("trade_score"),
            "confidence_score": opportunity["confidence_score"] if opportunity else None,
            # Preserve the existing candidate-level API values. The owning
            # stage carries the unambiguous Operator identity.
            "status": "deferred" if active_defer else "queued",
            "stage": "system_operator",
            "reason_code": reason_code,
            "reason": reason if active_defer else "Queued for Operator evaluation.",
            "next_trigger": next_trigger if active_defer else "operator_evaluation",
            "queued_at": item.get("at"),
            "valid_until": valid_until,
            "expires_in_sec": _iso_seconds_until(valid_until, now),
            "defer_until": defer_until if active_defer else None,
            "latest_event_at": event["created_at"] if event else None,
            "policy_epoch_id": event["policy_epoch_id"] if event else None,
            "blocking_filter": None,
            "passed_filters": ranker_passes,
            "passed_filter_count": len(ranker_passes),
            "latest_ranker_run_id": ranker_run_id,
            "retry_at": defer_until if active_defer else None,
            "retry_in_sec": _iso_seconds_until(defer_until, now) if active_defer else None,
        })
    return candidates, queue


def _ranker_counts(candidates: list[dict[str, Any]], total: int) -> dict[str, int]:
    states = [candidate["status"] for candidate in candidates]
    accounted = sum(
        states.count(state)
        for state in (
            "ranker_pending",
            "ranker_cooldown",
            "ranker_backpressure",
            "ranker_batch_limit",
            "ranker_carrier_active",
        )
    )
    return {
        "open": total,
        "not_evaluated": states.count("ranker_pending"),
        "cooldown": states.count("ranker_cooldown"),
        "backpressure": states.count("ranker_backpressure"),
        "batch_limit": states.count("ranker_batch_limit"),
        "carrier_active": states.count("ranker_carrier_active"),
        "other_wait": max(total - accounted, 0),
    }


def build_current_entry_status(
    db,
    *,
    now: datetime,
    window_hours: int,
    emitted_count: int,
    open_opportunities: int,
    proposed_count: int,
    setup_count: int,
    active_setup_count: int,
    terminal_breakdown: dict[str, int],
) -> dict[str, Any]:
    """Build one read-only answer to where entry work is and why it stopped."""
    window_start = (now - timedelta(hours=max(1, int(window_hours)))).isoformat()
    operator_candidates, queue = _operator_candidates(db, now=now)
    all_ranker_candidates = _open_ranker_candidates(
        db, now=now, window_start=window_start
    )
    ranker_candidates = all_ranker_candidates[:_RANKER_CANDIDATE_LIMIT]
    candidates = operator_candidates + ranker_candidates
    open_positions = int(db.execute(
        "SELECT COUNT(*) FROM positions WHERE status='open'"
    ).fetchone()[0])
    pending_proposals = int(db.execute(
        "SELECT COUNT(*) FROM trade_proposals WHERE status='pending'"
    ).fetchone()[0])
    deferred = [row for row in operator_candidates if row["status"] == "deferred"]
    latest_deferred = max(
        deferred,
        key=lambda row: str(row.get("latest_event_at") or ""),
        default=None,
    )
    queued_total = int(queue.get("unresolved_total") or 0)
    ranker_counts = _ranker_counts(all_ranker_candidates, open_opportunities)
    primary_ranker = all_ranker_candidates[0] if all_ranker_candidates else None

    # Layer-1 observability: pull active-defer reason-class breakdown, oldest
    # age, defer_rate, and the (currently static) queue capacity from the shared
    # pipeline_progress aggregator so the counts block, the Blotter tooltip,
    # and doctor attribution all agree on one taxonomy. Best-effort — the
    # counts block still renders if the aggregator raises.
    _defer_breakdown: dict[str, Any] = {
        "market_quiet_defers": 0,
        "candidate_specific_defers": 0,
        "defer_rate": 0.0,
        "oldest_defer_age_seconds": None,
        "queue_capacity": None,
    }
    try:
        from aletheia.diagnostics.pipeline_progress import summarize as _pp_summarize
        from aletheia.agents.paper_trader import (
            _PENDING_SIGNAL_QUEUE_CAPACITY as _QUEUE_CAP,
        )

        _snap = _pp_summarize(hours=12)
        _op = _snap.get("operator") or {}
        _defer_breakdown["market_quiet_defers"] = int(
            _op.get("operator_defer_market_quiet") or 0
        )
        _defer_breakdown["candidate_specific_defers"] = int(
            _op.get("operator_defer_candidate_specific") or 0
        )
        _defer_breakdown["defer_rate"] = float(_op.get("defer_rate") or 0.0)
        _defer_breakdown["oldest_defer_age_seconds"] = _op.get(
            "oldest_active_defer_age_seconds"
        )
        _defer_breakdown["queue_capacity"] = int(_QUEUE_CAP)
    except Exception:
        pass

    if open_positions > 0:
        state, primary_stage = "position_open", "position"
        headline = f"{open_positions} position{'s are' if open_positions != 1 else ' is'} open."
        reason_code = "position_open"
        reason = "The entry pipeline has produced an active position."
        next_trigger = "position_management"
    elif pending_proposals > 0:
        state, primary_stage = "human_approval_required", "human_approval"
        headline = (
            f"{pending_proposals} proposal{'s require' if pending_proposals != 1 else ' requires'} "
            "human approval."
        )
        reason_code = "proposal_pending"
        reason = "The automated decision path reached a Proposal and is waiting for you."
        next_trigger = "human_approval"
    elif queued_total > 0 and deferred:
        state, primary_stage = "operator_deferred", "system_operator"
        headline = (
            f"{queued_total} candidate{'s are' if queued_total != 1 else ' is'} waiting for new market facts."
            if len(deferred) == queued_total
            else f"{queued_total} candidates are in Operator review; {len(deferred)} waiting for new market facts."
        )
        reason_code = latest_deferred["reason_code"]
        reason = latest_deferred["reason"]
        next_trigger = latest_deferred["next_trigger"]
    elif queued_total > 0:
        state, primary_stage = "operator_queued", "system_operator"
        headline = f"{queued_total} candidate{'s are' if queued_total != 1 else ' is'} queued for Operator review."
        reason_code = "operator_evaluation_pending"
        reason = "The candidates passed Ranker admission and are waiting for Operator evaluation."
        next_trigger = "operator_evaluation"
    elif primary_ranker:
        state = primary_ranker["status"]
        primary_stage = primary_ranker["stage"]
        reason_code = primary_ranker["reason_code"]
        reason = primary_ranker["reason"]
        next_trigger = primary_ranker["next_trigger"]
        if ranker_counts["cooldown"] == open_opportunities:
            headline = f"{open_opportunities} Ranker candidate{'s are' if open_opportunities != 1 else ' is'} waiting for signal cooldown."
        elif ranker_counts["backpressure"] == open_opportunities:
            headline = f"{open_opportunities} Ranker candidate{'s are' if open_opportunities != 1 else ' is'} waiting for Operator queue capacity."
        elif ranker_counts["not_evaluated"] == open_opportunities:
            headline = f"{open_opportunities} opportunit{'ies are' if open_opportunities != 1 else 'y is'} waiting for the first Ranker evaluation."
        else:
            headline = f"{open_opportunities} open Opportunities are waiting at different Ranker gates."
    elif emitted_count > 0 and proposed_count == 0:
        state, primary_stage = "no_active_candidate", "opportunity_lifecycle"
        headline = "No active entry candidate remains from the selected window."
        reason_code = "window_candidates_terminal"
        superseded = int(terminal_breakdown.get("snapshot_superseded") or 0)
        ttl_expired = int(terminal_breakdown.get("ttl_expired") or 0)
        other_expired = int(terminal_breakdown.get("other_expired") or 0)
        operator_terminal = sum(
            int(terminal_breakdown.get(key) or 0)
            for key in ("operator_trade", "operator_skip", "operator_watch")
        )
        deterministic_review = int(
            terminal_breakdown.get("deterministic_review") or 0
        )
        other_reviewed = int(terminal_breakdown.get("other_reviewed") or 0)
        reason = (
            f"{emitted_count} generated rows represented {setup_count} unique setups: "
            f"{superseded} snapshot generations were superseded, {ttl_expired} reached "
            f"their market TTL, {operator_terminal} received a terminal Operator action, "
            f"{deterministic_review} ended at a deterministic filter, and "
            f"{other_expired + other_reviewed} have another or unavailable terminal "
            "reason; none produced a Proposal."
        )
        next_trigger = "new_opportunity"
    else:
        state, primary_stage = "no_candidates", "market_observer"
        headline = "No entry candidates were emitted in the selected window."
        reason_code = "no_candidates"
        reason = "The system is waiting for Market Observer to emit a candidate."
        next_trigger = "new_opportunity"

    operator_state = "waiting" if deferred else "working" if queued_total else "not_reached"
    proposal_state = "action_required" if pending_proposals else (
        "reached" if proposed_count or open_positions else "not_reached"
    )
    ranker_state = "passed" if queued_total else "waiting" if open_opportunities else "idle"
    pipeline = [
        {"stage": "observer", "label": "Observer", "state": "active" if emitted_count else "idle", "count": setup_count},
        {"stage": "ranker", "label": "Ranker", "state": ranker_state, "count": active_setup_count},
        {"stage": "operator", "label": "Operator", "state": operator_state, "count": queued_total},
        {"stage": "proposal", "label": "Proposal", "state": proposal_state, "count": pending_proposals},
        {"stage": "risk", "label": "Risk", "state": "reached" if open_positions else "not_reached" if not proposed_count else "not_available", "count": None},
        {"stage": "execution", "label": "Execution", "state": "position_open" if open_positions else "not_reached" if not proposed_count else "not_available", "count": open_positions},
    ]
    return {
        "state": state,
        "headline": headline,
        "primary_stage": primary_stage,
        "reason_code": reason_code,
        "reason": reason,
        "next_trigger": next_trigger,
        "as_of": now.isoformat(),
        "policy_epoch_id": (
            latest_deferred.get("policy_epoch_id")
            if latest_deferred
            else _CARRIER_CONTINUITY_EPOCH
        ),
        "counts": {
            "open_positions": open_positions,
            "open_opportunities": open_opportunities,
            "queued_candidates": queued_total,
            "deferred_candidates": len(deferred),
            "pending_proposals": pending_proposals,
            "discovered_generations": emitted_count,
            "unique_setups": setup_count,
            "active_setups": active_setup_count,
            "superseded_generations": int(
                terminal_breakdown.get("snapshot_superseded") or 0
            ),
            "ttl_expired": int(terminal_breakdown.get("ttl_expired") or 0),
            "market_quiet_defers": _defer_breakdown["market_quiet_defers"],
            "candidate_specific_defers": _defer_breakdown["candidate_specific_defers"],
            "defer_rate": _defer_breakdown["defer_rate"],
            "oldest_defer_age_seconds": _defer_breakdown["oldest_defer_age_seconds"],
            "queue_capacity": _defer_breakdown["queue_capacity"],
        },
        "ranker": {
            "delivery_epoch": _RANKER_DELIVERY_EPOCH,
            "carrier_epoch": _CARRIER_CONTINUITY_EPOCH,
            "counts": ranker_counts,
            "displayed_candidates": len(ranker_candidates),
            "undisplayed_candidates": max(
                len(all_ranker_candidates) - len(ranker_candidates), 0
            ),
            "last_run": _latest_ranker_run(db),
            "wake": _ranker_wake_status(),
        },
        "candidates": candidates,
        "pipeline": pipeline,
        "epoch_summary": _epoch_summary_snapshot(),
    }


def _epoch_summary_snapshot() -> dict[str, Any]:
    """Six-bucket operator attribution for the current epoch (Overview/CLI/doctor).

    Delegates to ``diagnostics.pipeline_progress.summarize`` so all three surfaces
    agree on how skips/defers are classified and never blend old-commit rows into
    the current-code stall picture. Best-effort — returns an empty payload on
    failure so the Overview response never breaks on diagnostic issues.
    """
    try:
        from aletheia.diagnostics.pipeline_progress import summarize

        return summarize(hours=12)
    except Exception:
        return {}
