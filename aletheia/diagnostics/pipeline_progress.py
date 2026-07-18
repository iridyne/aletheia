"""Unified pipeline-progress aggregator (loop.profit / Overview / CLI share this).

Named to distinguish it from ``aletheia.analytics.opportunity_progress``, which
answers "what is *this candidate* waiting on?" (per-carrier owner-facing text).
This module answers "where in the pipeline is the current *epoch*'s work?"
(aggregate six-bucket counts scoped to producer_commit + policy_epoch_id).

Prior to this module three surfaces answered the question "where in the pipeline is
the current epoch's work stuck?" three different ways:

* ``doctor._probe_profit_loop`` counted ``pi_decisions.action`` across the last 12h
  regardless of ``producer_commit`` — old-commit skips silently attributed to the
  running code.
* The Overview API and CLI each computed their own snapshots, so operator_deferred
  showed up in one place and system_operator_skip×N in another.
* The six real failure modes (direct model skip, conviction-gate skip,
  debate-not-confirmed, operator defer, context unavailable, ranker cooldown) were
  collapsed into one ``skip×N`` count.

This module returns the single truth: counts scoped to the current
``(producer_commit, policy_epoch_id)`` pair, split into the six buckets, plus
opportunity/queue/proposal ages. It is READ-ONLY and never raises into callers.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("aletheia.diagnostics.pipeline_progress")

# filter_decisions.reason_code values that classify Operator outputs.
# The name→bucket map is stable across the six buckets exposed to callers.
_OPERATOR_SKIP_REASON_TO_BUCKET: dict[str, str] = {
    # Direct model skip = model returned action='skip' end-to-end (may or may not
    # have carried adverse_candidate_evidence; the bucket does not distinguish).
    "operator_model_skip": "direct_model_skip",
    # Debate-triggered downgrade below the minimum conviction (Phase C: replaced
    # by DecisionUnavailable, but historical rows keep this code).
    "debate_below_min_conviction": "debate_not_confirmed",
    # Non-debate conviction gate: model returned action='trade' but conviction<55
    # (Phase C: replaced by DecisionUnavailable; the reason_code is kept for the
    # trace so historical cohorts remain queryable).
    "conviction_below_minimum": "conviction_gate_skip",
    "operator_low_conviction": "conviction_gate_skip",
    "debate_not_confirmed": "debate_not_confirmed",
    # Direction-brake rejections (deterministic). Two code sites emit different
    # names; both belong in the same bucket for diagnostic purposes.
    "proposal_direction_concentration": "direction_brake",  # bias_brake path
    "direction_overheated": "direction_brake",              # preflight overheat path
}


def _current_epoch(db) -> dict[str, str | None]:
    """Resolve the running ``(producer_commit, policy_epoch_id)`` pair.

    ``get_boot_commit()`` returns the commit that the running gateway process
    booted on. In CLI/diagnostic contexts (no lifespan) it is ``None``; fall
    back to disk HEAD so ``aletheia doctor`` still scopes results correctly.
    """
    from aletheia.agent_runtime.build_info import get_boot_commit

    commit = get_boot_commit()
    if not commit:
        try:
            from aletheia.update import git_ops

            commit = git_ops.current_commit() or None
        except Exception:
            commit = None
    epoch_row = db.execute(
        "SELECT epoch_id, epoch_kind, started_at "
        "FROM learning_policy_epochs "
        "WHERE epoch_kind = 'system_operator_skip_remediation' "
        "AND (ended_at IS NULL OR ended_at = '') "
        "ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    if epoch_row is None:
        epoch_row = db.execute(
            "SELECT epoch_id, epoch_kind, started_at "
            "FROM learning_policy_epochs "
            "WHERE epoch_kind = 'system_operator_skip_remediation' "
            "ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
    return {
        "producer_commit": commit,
        "policy_epoch_id": str(epoch_row["epoch_id"]) if epoch_row else None,
        "policy_epoch_kind": str(epoch_row["epoch_kind"]) if epoch_row else None,
        "policy_epoch_started_at": (
            str(epoch_row["started_at"]) if epoch_row else None
        ),
    }


def _count_opportunities(db, commit: str | None, since_iso: str) -> dict[str, int]:
    if commit:
        rows = db.execute(
            "SELECT status, COUNT(*) FROM opportunities "
            "WHERE producer_commit = ? AND created_at >= ? "
            "GROUP BY status",
            (commit, since_iso),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT status, COUNT(*) FROM opportunities "
            "WHERE created_at >= ? GROUP BY status",
            (since_iso,),
        ).fetchall()
    return {str(r[0] or "unknown"): int(r[1] or 0) for r in rows}


def _pending_stats(db, commit: str | None) -> dict[str, Any]:
    """Current signal_pending queue depth (unbounded by window — reflects live state)."""
    if commit:
        rows = db.execute(
            "SELECT COUNT(*), MIN(created_at) FROM pi_decisions "
            "WHERE action='signal_pending' AND producer_commit = ?",
            (commit,),
        ).fetchone()
    else:
        rows = db.execute(
            "SELECT COUNT(*), MIN(created_at) FROM pi_decisions "
            "WHERE action='signal_pending'"
        ).fetchone()
    return {
        "count": int(rows[0] or 0),
        "oldest_created_at": rows[1] if rows and rows[1] else None,
    }


def _operator_buckets(
    db,
    commit: str | None,
    policy_epoch_id: str | None,
    since_iso: str,
) -> dict[str, int]:
    """Six-bucket operator classification from filter_decisions + evaluation events."""
    buckets = {
        "direct_model_skip": 0,
        "conviction_gate_skip": 0,
        "debate_not_confirmed": 0,
        "operator_defer": 0,
        "context_unavailable": 0,
        "ranker_cooldown": 0,
        "direction_brake": 0,
        "operator_trade_intent": 0,
    }

    conditions = ["evaluated_at >= ?", "stage LIKE 'system_operator%'"]
    params: list[Any] = [since_iso]
    if commit:
        conditions.append("code_commit = ?")
        params.append(commit)
    if policy_epoch_id:
        conditions.append(
            "CASE WHEN json_valid(input_snapshot) "
            "THEN json_extract(input_snapshot, '$.policy_epoch_id') END = ?"
        )
        params.append(policy_epoch_id)
    rows = db.execute(
        "SELECT reason_code, decision, COUNT(*) FROM filter_decisions WHERE "
        + " AND ".join(conditions)
        + " GROUP BY reason_code, decision",
        tuple(params),
    ).fetchall()
    for reason_code, decision, count in rows:
        code = str(reason_code or "")
        bucket = _OPERATOR_SKIP_REASON_TO_BUCKET.get(code)
        if bucket:
            buckets[bucket] += int(count or 0)
        elif code == "operator_model_trade":
            buckets["operator_trade_intent"] += int(count or 0)

    if commit:
        cooldown_rows = db.execute(
            "SELECT COUNT(*) FROM filter_decisions "
            "WHERE code_commit = ? AND evaluated_at >= ? "
            "AND stage LIKE 'ranker%' AND decision='rejected' "
            "AND reason_code LIKE '%cooldown%'",
            (commit, since_iso),
        ).fetchone()
    else:
        cooldown_rows = db.execute(
            "SELECT COUNT(*) FROM filter_decisions "
            "WHERE evaluated_at >= ? AND stage LIKE 'ranker%' "
            "AND decision='rejected' AND reason_code LIKE '%cooldown%'",
            (since_iso,),
        ).fetchone()
    if cooldown_rows:
        buckets["ranker_cooldown"] = int(cooldown_rows[0] or 0)
    return buckets


def _defer_events(
    db, policy_epoch_id: str | None, since_iso: str
) -> dict[str, int]:
    """Per-kind counts of operator defer events under the current epoch."""
    if policy_epoch_id:
        rows = db.execute(
            "SELECT event_kind, COUNT(*) FROM operator_evaluation_events "
            "WHERE policy_epoch_id = ? AND created_at >= ? "
            "GROUP BY event_kind",
            (policy_epoch_id, since_iso),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT event_kind, COUNT(*) FROM operator_evaluation_events "
            "WHERE created_at >= ? GROUP BY event_kind",
            (since_iso,),
        ).fetchall()
    return {str(r[0] or "unknown"): int(r[1] or 0) for r in rows}


def _active_defer_stats(
    db, policy_epoch_id: str | None, now_iso: str
) -> dict[str, Any]:
    """Reason-class breakdown + oldest-age over LIVE defers (defer_until > now).

    Distinct from ``_defer_events`` which counts every defer event in the 12h
    window regardless of expiry. This queries the same ``operator_evaluation_events``
    table but restricts to events whose defer window has not elapsed — i.e., the
    carriers that are actually occupying queue slots RIGHT NOW.

    Uses ``defer_reason`` persisted in ``queue_snapshot_json`` (written by
    ``commands.py:1858``), routed through ``analytics.defer_classifier`` so the
    taxonomy is authoritative in one place. Distinct-by-opportunity_id so a
    carrier that has been re-deferred multiple times counts once.
    """
    try:
        from aletheia.analytics.defer_classifier import classify_defer_reason
    except Exception:
        return {
            "market_quiet": 0,
            "candidate_specific": 0,
            "unknown": 0,
            "oldest_active_defer_age_seconds": None,
        }

    conditions = [
        "event_kind LIKE 'operator_decision_deferred_%'",
        "json_valid(queue_snapshot_json)",
        "json_extract(queue_snapshot_json, '$.defer_until') > ?",
    ]
    params: list[Any] = [now_iso]
    if policy_epoch_id:
        conditions.append("policy_epoch_id = ?")
        params.append(policy_epoch_id)
    where = " AND ".join(conditions)

    # One row per opportunity_id — the newest defer event wins (created_at
    # descending). Prevents a carrier that has been re-deferred N times from
    # inflating either the ratio buckets or the oldest-age computation.
    query = (
        "SELECT opportunity_id, defer_reason, min_created_at FROM ("
        "  SELECT "
        "    COALESCE(json_extract(queue_snapshot_json, '$.opportunity_id'),"
        "             CAST(id AS TEXT)) AS opportunity_id, "
        "    json_extract(queue_snapshot_json, '$.defer_reason') AS defer_reason, "
        "    MIN(created_at) OVER (PARTITION BY "
        "      COALESCE(json_extract(queue_snapshot_json, '$.opportunity_id'),"
        "               CAST(id AS TEXT))"
        "    ) AS min_created_at, "
        "    ROW_NUMBER() OVER (PARTITION BY "
        "      COALESCE(json_extract(queue_snapshot_json, '$.opportunity_id'),"
        "               CAST(id AS TEXT)) "
        "      ORDER BY created_at DESC "
        "    ) AS rn "
        "  FROM operator_evaluation_events "
        f"  WHERE {where}"
        ") WHERE rn = 1"
    )
    rows = db.execute(query, tuple(params)).fetchall()

    buckets = {"market_quiet": 0, "candidate_specific": 0, "unknown": 0}
    oldest_iso: str | None = None
    for row in rows:
        cls = classify_defer_reason(row[1])
        buckets[cls] += 1
        created_at = row[2]
        if created_at and (oldest_iso is None or str(created_at) < oldest_iso):
            oldest_iso = str(created_at)

    oldest_age_seconds: int | None = None
    if oldest_iso:
        try:
            from datetime import datetime as _dt, timezone as _tz

            parsed = _dt.fromisoformat(oldest_iso.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=_tz.utc)
            now_parsed = _dt.fromisoformat(now_iso.replace("Z", "+00:00"))
            if now_parsed.tzinfo is None:
                now_parsed = now_parsed.replace(tzinfo=_tz.utc)
            oldest_age_seconds = max(int((now_parsed - parsed).total_seconds()), 0)
        except (TypeError, ValueError):
            oldest_age_seconds = None

    return {
        "market_quiet": buckets["market_quiet"],
        "candidate_specific": buckets["candidate_specific"],
        "unknown": buckets["unknown"],
        "oldest_active_defer_age_seconds": oldest_age_seconds,
    }


def _proposal_stats(db, commit: str | None, since_iso: str) -> dict[str, Any]:
    """Newest proposal and per-window trade count under the current commit.

    ``trade_proposals.producer_commit`` is authoritative. Older rows without it
    fall back to their linked Opportunity; ``commit_hash`` is a content hash.
    """
    if commit:
        row = db.execute(
            "SELECT MAX(tp.created_at), COUNT(*) FROM trade_proposals tp "
            "LEFT JOIN opportunities o ON o.id = tp.opportunity_id "
            "WHERE tp.created_at >= ? "
            "AND (tp.producer_commit = ? OR "
            "(tp.producer_commit IS NULL AND o.producer_commit = ?))",
            (since_iso, commit, commit),
        ).fetchone()
    else:
        row = db.execute(
            "SELECT MAX(created_at), COUNT(*) FROM trade_proposals "
            "WHERE created_at >= ?",
            (since_iso,),
        ).fetchone()
    return {
        "newest_created_at": row[0] if row else None,
        "count": int(row[1] or 0) if row else 0,
    }


def summarize(
    hours: int = 12,
    producer_commit: str | None = None,
    policy_epoch_id: str | None = None,
) -> dict[str, Any]:
    """Return a snapshot of pipeline progress scoped to the running code + policy epoch.

    All queries are bounded by ``since_iso = now - hours``. Callers that need
    live-queue depth (``signal_pending`` count / oldest age) read the unbounded
    fields nested under ``signal_pending``.
    """
    from datetime import datetime, timedelta, timezone
    from aletheia.storage.sqlite import get_db

    db = get_db()
    now = datetime.now(timezone.utc)
    since_iso = (now - timedelta(hours=hours)).isoformat()

    if producer_commit is None or policy_epoch_id is None:
        epoch = _current_epoch(db)
        if producer_commit is None:
            producer_commit = epoch["producer_commit"]
        if policy_epoch_id is None:
            policy_epoch_id = epoch["policy_epoch_id"]
    else:
        epoch = {
            "producer_commit": producer_commit,
            "policy_epoch_id": policy_epoch_id,
            "policy_epoch_kind": None,
            "policy_epoch_started_at": None,
        }

    try:
        opportunities = _count_opportunities(db, producer_commit, since_iso)
    except Exception as exc:
        logger.warning("opportunity_progress.opportunities failed: %s", exc)
        opportunities = {}
    try:
        signal_pending = _pending_stats(db, producer_commit)
    except Exception as exc:
        logger.warning("opportunity_progress.signal_pending failed: %s", exc)
        signal_pending = {"count": 0, "oldest_created_at": None}
    try:
        operator = _operator_buckets(
            db, producer_commit, policy_epoch_id, since_iso
        )
    except Exception as exc:
        logger.warning("opportunity_progress.operator failed: %s", exc)
        operator = {}
    try:
        defer_events = _defer_events(db, policy_epoch_id, since_iso)
    except Exception as exc:
        logger.warning("opportunity_progress.defer_events failed: %s", exc)
        defer_events = {}
    operator["operator_defer"] = sum(
        v for k, v in defer_events.items() if k.startswith("operator_decision_deferred_")
    )
    try:
        active_defer = _active_defer_stats(db, policy_epoch_id, now.isoformat())
    except Exception as exc:
        logger.warning("opportunity_progress.active_defer failed: %s", exc)
        active_defer = {
            "market_quiet": 0,
            "candidate_specific": 0,
            "unknown": 0,
            "oldest_active_defer_age_seconds": None,
        }
    operator["operator_defer_market_quiet"] = int(active_defer["market_quiet"])
    operator["operator_defer_candidate_specific"] = int(
        active_defer["candidate_specific"]
    )
    operator["operator_defer_unknown"] = int(active_defer["unknown"])
    # defer_rate = 12h defers / (12h defers + trade intents + all skip variants).
    # Denominator floors at 1 so an empty window renders 0.0 rather than NaN.
    total_skip_variants = int(operator.get("direct_model_skip") or 0) \
        + int(operator.get("conviction_gate_skip") or 0) \
        + int(operator.get("debate_not_confirmed") or 0) \
        + int(operator.get("direction_brake") or 0)
    defer_denom = max(
        int(operator["operator_defer"])
        + int(operator.get("operator_trade_intent") or 0)
        + total_skip_variants,
        1,
    )
    operator["defer_rate"] = round(
        float(operator["operator_defer"]) / float(defer_denom), 4
    )
    operator["oldest_active_defer_age_seconds"] = (
        active_defer["oldest_active_defer_age_seconds"]
    )
    try:
        proposals = _proposal_stats(db, producer_commit, since_iso)
    except Exception as exc:
        logger.warning("opportunity_progress.proposals failed: %s", exc)
        proposals = {"newest_created_at": None, "count": 0}

    return {
        "window_hours": hours,
        "epoch": epoch,
        "opportunities": opportunities,
        "signal_pending": signal_pending,
        "operator": operator,
        "defer_events": defer_events,
        "proposals": proposals,
    }
