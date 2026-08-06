"""Append-only evidence for Opportunity filter decisions and outcomes."""

from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from typing import Any
from uuid import uuid4
import zlib

from aletheia.storage import repositories as repo
from aletheia.storage.models import (
    FilterCandidateOutcome,
    FilterDecision,
    FilterDecisionBatch,
)


logger = logging.getLogger("aletheia.analytics.filter_evidence")

_SCHEMA_VERSION = "v1"
_HORIZONS = {
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "24h": timedelta(hours=24),
}
_VALID_DIRECTIONS = {"long", "short"}
_VALID_DECISIONS = {"passed", "passed_with_penalty", "rejected", "not_applicable", "error"}
_DROP_SNAPSHOT_VALUE = object()
HEAVY_BATCH_MIN_INTERVAL_SEC = 55 * 60
_SENSITIVE_KEY_PARTS = {
    "api_key", "authorization", "credential", "password", "prompt",
    "raw_context", "secret", "token",
}

# Shared causal assignment used by feedback statistics and bounded replays.
# Later scheduler retries remain stored but cannot place one candidate in both
# pass and reject cohorts.
FIRST_EXPOSURE_CTE_SQL = (
    "WITH ranked_filter_decisions AS ("
    " SELECT fd.*, "
    " ROW_NUMBER() OVER ("
    "   PARTITION BY fd.filter_name, fd.stage, fd.filter_version, fd.outcome_candidate_key "
    "   ORDER BY datetime(fd.evaluated_at), fd.rowid"
    " ) AS exposure_rank, "
    " MIN(fd.evaluated_at) OVER ("
    "   PARTITION BY fd.filter_name, fd.stage, fd.filter_version, fd.outcome_candidate_key"
    " ) AS first_evaluated_at "
    " FROM filter_decisions fd "
    " WHERE fd.decision IN ('passed','rejected') "
    "   AND fd.outcome_candidate_key IS NOT NULL"
    ") "
)


@contextmanager
def atomic_opportunity_evidence():
    """Own a rollback boundary for one Opportunity and its producer fact.

    Repository transactions intentionally join a caller-owned transaction.  A
    producer needs a stricter guarantee: its new lifecycle row must never survive
    without the exact evidence that says it was emitted.  Use an IMMEDIATE outer
    transaction when possible and a unique SAVEPOINT when already nested.
    """
    from aletheia.storage.sqlite import get_db

    db = get_db()
    if not db.in_transaction:
        with repo.transaction(immediate=True):
            yield
        return

    savepoint = f"opportunity_evidence_{uuid4().hex}"
    db.execute(f"SAVEPOINT {savepoint}")
    try:
        yield
        db.execute(f"RELEASE SAVEPOINT {savepoint}")
    except Exception:
        db.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        db.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise


def record_filter_decision(
    *,
    candidate_key: str,
    candidate_kind: str,
    filter_name: str,
    stage: str,
    decision: str,
    reason_code: str,
    symbol: str,
    market_type: str = "spot",
    parent_candidate_key: str | None = None,
    run_id: str | None = None,
    opportunity_id: str | None = None,
    universe_snapshot_id: str | None = None,
    universe_member_id: str | None = None,
    proposal_id: str | None = None,
    venue: str | None = None,
    direction: str | None = None,
    scanner_family: str | None = None,
    regime_tag: str | None = None,
    sequence_no: int = 0,
    reason_detail: str | None = None,
    inputs: dict[str, Any] | None = None,
    parameters: dict[str, Any] | None = None,
    evaluated_at: str | None = None,
    outcome_candidate_key: str | None = None,
    entry_price_usd: float | None = None,
    entry_spread_bps: float | None = None,
    funding_rate_at_entry: float | None = None,
    entry_ts: str | None = None,
    raise_on_error: bool = False,
) -> str:
    """Persist one gate fact and, when economically identifiable, its outcomes.

    Evidence failures are observational by default: callers receive a stable ID
    and the original decision semantics remain unchanged. Trading handoff callers
    can request propagation so their surrounding transaction fails closed.
    """
    if decision not in _VALID_DECISIONS:
        raise ValueError(f"unsupported filter decision: {decision}")
    if not candidate_key or not filter_name or not symbol:
        raise ValueError("candidate_key, filter_name and symbol are required")

    params = _sanitize_snapshot(parameters or {})
    input_values = _sanitize_snapshot(inputs or {})
    parameter_json = _canonical_json(params)
    input_json = _canonical_json(input_values)
    filter_version = _filter_version(filter_name, parameter_json)
    dedup_material = (
        f"{candidate_key}|{filter_name}|{stage}|{sequence_no}|{decision}|{filter_version}"
    )
    dedup_key = hashlib.sha256(dedup_material.encode("utf-8")).hexdigest()
    decision_id = dedup_key[:12]
    side = str(direction or "").lower() or None
    market = _normalize_market(market_type)
    timestamp = evaluated_at or _utcnow()
    outcome_entry_ts = entry_ts or timestamp

    outcome_ready = (
        bool(venue)
        and entry_price_usd is not None
        and float(entry_price_usd) > 0
    )
    result_key = outcome_candidate_key or candidate_key if outcome_ready else None
    fact = FilterDecision(
        id=decision_id,
        dedup_key=dedup_key,
        candidate_key=candidate_key,
        candidate_kind=candidate_kind,
        parent_candidate_key=parent_candidate_key,
        run_id=run_id,
        opportunity_id=opportunity_id,
        universe_snapshot_id=universe_snapshot_id,
        universe_member_id=universe_member_id,
        proposal_id=proposal_id,
        venue=venue,
        symbol=symbol,
        market_type=market,
        direction=side,
        scanner_family=scanner_family,
        regime_tag=regime_tag,
        filter_name=filter_name,
        stage=stage,
        sequence_no=sequence_no,
        decision=decision,
        reason_code=reason_code,
        reason_detail=_safe_text(reason_detail),
        input_snapshot=input_json,
        parameter_snapshot=parameter_json,
        filter_version=filter_version,
        code_commit=_boot_commit(),
        outcome_candidate_key=result_key,
        evaluated_at=timestamp,
    )
    try:
        repo.insert_filter_decision(fact)
        if result_key:
            _ensure_candidate_outcomes(
                outcome_candidate_key=result_key,
                venue=str(venue),
                symbol=symbol,
                market_type=market,
                direction=side if side in _VALID_DIRECTIONS else "unknown",
                scanner_family=scanner_family,
                regime_tag=regime_tag,
                entry_price_usd=float(entry_price_usd),
                entry_spread_bps=entry_spread_bps,
                funding_rate_at_entry=funding_rate_at_entry,
                entry_ts=outcome_entry_ts,
            )
    except Exception as exc:  # observation must not mutate the trading decision
        if raise_on_error:
            raise
        logger.warning(
            "filter evidence write failed candidate=%s filter=%s: %s",
            candidate_key,
            filter_name,
            exc,
        )
    return decision_id


def record_filter_decision_batch(
    *,
    run_id: str,
    candidate_kind: str,
    filter_name: str,
    stage: str,
    candidate_facts: list[dict[str, Any]],
    parameters: dict[str, Any] | None = None,
    evaluated_at: str | None = None,
    min_interval_sec: float | None = None,
    raise_on_error: bool = False,
) -> str:
    """Persist every high-volume directionless fact in one immutable run batch."""
    if not run_id or not filter_name:
        raise ValueError("run_id and filter_name are required")
    params = _sanitize_snapshot(parameters or {})
    parameter_json = _canonical_json(params)
    filter_version = _filter_version(filter_name, parameter_json)
    effective_evaluated_at = evaluated_at or _utcnow()
    if min_interval_sec is not None and min_interval_sec > 0:
        try:
            from aletheia.storage.sqlite import get_db

            previous = get_db().execute(
                "SELECT id, evaluated_at FROM filter_decision_batches "
                "WHERE candidate_kind=? AND filter_name=? AND stage=? AND filter_version=? "
                "ORDER BY evaluated_at DESC LIMIT 1",
                (candidate_kind, filter_name, stage, filter_version),
            ).fetchone()
            previous_at = (
                _parse_dt(previous["evaluated_at"] if hasattr(previous, "keys") else previous[1])
                if previous
                else None
            )
            current_at = _parse_dt(effective_evaluated_at)
            if (
                previous_at is not None
                and current_at is not None
                and 0 <= (current_at - previous_at).total_seconds() < min_interval_sec
            ):
                return str(previous["id"] if hasattr(previous, "keys") else previous[0])
        except Exception:
            # Evidence capture is fail-open: an unavailable cadence lookup must
            # not suppress the current immutable observation.
            pass
    facts = [_sanitize_snapshot(item) for item in candidate_facts]
    dedup_key = hashlib.sha256(
        f"{run_id}|{candidate_kind}|{filter_name}|{stage}|{filter_version}".encode("utf-8")
    ).hexdigest()
    counts = {name: 0 for name in _VALID_DECISIONS}
    for fact in facts:
        decision = str((fact or {}).get("decision") or "error")
        counts[decision if decision in counts else "error"] += 1
    batch = FilterDecisionBatch(
        id=dedup_key[:12],
        dedup_key=dedup_key,
        run_id=run_id,
        candidate_kind=candidate_kind,
        filter_name=filter_name,
        stage=stage,
        candidate_count=len(facts),
        passed_count=counts["passed"],
        rejected_count=counts["rejected"],
        not_applicable_count=counts["not_applicable"],
        error_count=counts["error"],
        candidate_facts=_encode_candidate_facts(facts),
        parameter_snapshot=parameter_json,
        filter_version=filter_version,
        code_commit=_boot_commit(),
        evaluated_at=effective_evaluated_at,
    )
    try:
        inserted = repo.insert_filter_decision_batch(batch)
        if raise_on_error and not inserted:
            raise RuntimeError("filter_evidence_batch_not_inserted")
    except Exception as exc:
        if raise_on_error:
            raise
        logger.warning(
            "filter evidence batch write failed run=%s filter=%s: %s",
            run_id,
            filter_name,
            exc,
        )
    return batch.id


def decode_filter_decision_batch(payload: str) -> list[dict[str, Any]]:
    """Decode current compact batches and legacy plain-list batch payloads."""
    encoded = payload or "[]"
    if encoded.startswith("zlib-base64:"):
        try:
            compressed = base64.b64decode(encoded.removeprefix("zlib-base64:"))
            encoded = zlib.decompress(compressed).decode("utf-8")
        except (ValueError, zlib.error, UnicodeDecodeError):
            return []
    raw = json.loads(encoded)
    if isinstance(raw, list):
        return [dict(item) for item in raw if isinstance(item, dict)]
    if not isinstance(raw, dict):
        return []
    fields = raw.get("fields") or []
    rows = raw.get("rows") or []
    if not isinstance(fields, list) or not isinstance(rows, list):
        return []

    decoded: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, list) or not row:
            continue
        try:
            presence_mask = int(row[0])
        except (TypeError, ValueError):
            continue
        values = iter(row[1:])
        fact: dict[str, Any] = {}
        try:
            for index, field in enumerate(fields):
                if presence_mask & (1 << index):
                    fact[str(field)] = next(values)
        except StopIteration:
            continue
        decoded.append(fact)
    return decoded


def filter_feedback_stats(
    shrinkage_k: float = 20.0,
    *,
    evaluated_since: str | None = None,
) -> list[dict[str, Any]]:
    """Return cost-aware pass/reject cells with a conservative shrunk mean."""
    from aletheia.storage.sqlite import get_db

    # A candidate can be evaluated on multiple scheduler ticks (fresh first,
    # expired later). Assign it once by its first persisted exposure so pass and
    # reject cohorts remain disjoint. Apply the report window only after ranking,
    # otherwise a pass just outside the window can be forgotten and its later
    # rejection incorrectly becomes the candidate's first exposure.
    time_clause = " AND datetime(fd.first_evaluated_at) >= datetime(?)" if evaluated_since else ""
    params = (evaluated_since,) if evaluated_since else ()
    rows = get_db().execute(
        FIRST_EXPOSURE_CTE_SQL +
        "SELECT fd.filter_name, fd.stage, fd.filter_version, "
        "fd.parameter_snapshot, fd.decision, "
        "COALESCE(fd.scanner_family, 'unknown') AS scanner_family, "
        "fd.direction, fd.market_type, fo.horizon, "
        "COUNT(DISTINCT fd.outcome_candidate_key) AS n_total, "
        "COUNT(DISTINCT CASE WHEN fo.status='settled' AND fo.net_return_pct IS NOT NULL "
        "  THEN fd.outcome_candidate_key END) AS n_net, "
        "AVG(CASE WHEN fo.status='settled' THEN fo.net_return_pct END) AS avg_net, "
        "SUM(CASE WHEN fo.status='settled' AND fo.net_return_pct > 0 "
        "  THEN fo.net_return_pct ELSE 0 END) AS gains, "
        "SUM(CASE WHEN fo.status='settled' AND fo.net_return_pct < 0 "
        "  THEN -fo.net_return_pct ELSE 0 END) AS losses, "
        "SUM(CASE WHEN fd.decision='rejected' AND fo.status='settled' "
        "  AND fo.net_return_pct < 0 THEN 1 ELSE 0 END) AS blocked_avoided_loss_count, "
        "SUM(CASE WHEN fd.decision='rejected' AND fo.status='settled' "
        "  AND fo.net_return_pct > COALESCE(fo.min_tradable_edge_pct, 0.05) "
        "  THEN 1 ELSE 0 END) AS blocked_missed_alpha_count, "
        "SUM(CASE WHEN fd.decision='rejected' AND fo.status='settled' "
        "  THEN fo.net_return_pct ELSE 0 END) AS blocked_net_sum "
        "FROM ranked_filter_decisions fd "
        "JOIN filter_candidate_outcomes fo "
        "  ON fo.outcome_candidate_key = fd.outcome_candidate_key "
        "WHERE fd.exposure_rank = 1 "
        f"{time_clause} "
        "GROUP BY fd.filter_name, fd.stage, fd.filter_version, "
        "fd.parameter_snapshot, fd.decision, "
        "COALESCE(fd.scanner_family, 'unknown'), fd.direction, fd.market_type, fo.horizon "
        "ORDER BY fd.filter_name, fd.stage, fd.filter_version, scanner_family, "
        "fd.direction, fd.market_type, fo.horizon, fd.decision",
        params,
    ).fetchall()
    raw = [dict(row) for row in rows]
    priors: dict[tuple, tuple[int, float]] = {}
    for row in raw:
        key = _cohort_key(row)
        n = int(row.get("n_net") or 0)
        total, weighted = priors.get(key, (0, 0.0))
        priors[key] = (total + n, weighted + n * float(row.get("avg_net") or 0.0))

    output: list[dict[str, Any]] = []
    for row in raw:
        n_total = int(row.get("n_total") or 0)
        n = int(row.get("n_net") or 0)
        prior_n, prior_sum = priors[_cohort_key(row)]
        prior = prior_sum / prior_n if prior_n else 0.0
        sample = float(row.get("avg_net") or 0.0)
        gains = float(row.pop("gains") or 0.0)
        losses = float(row.pop("losses") or 0.0)
        row["blocked_avoided_loss_count"] = int(
            row.get("blocked_avoided_loss_count") or 0
        )
        row["blocked_missed_alpha_count"] = int(
            row.get("blocked_missed_alpha_count") or 0
        )
        row["blocked_net_sum"] = round(float(row.get("blocked_net_sum") or 0.0), 6)
        row["n"] = n
        row["n_total"] = n_total
        row["n_net"] = n
        row["outcome_coverage"] = round(n / n_total, 4) if n_total else 0.0
        row["avg_net"] = round(sample, 6)
        row["shrunk_avg_net"] = round(
            (n * sample + float(shrinkage_k) * prior) / (n + float(shrinkage_k)),
            6,
        )
        row["profit_factor"] = round(gains / losses, 4) if losses > 0 else None
        row["assignment_policy"] = "first_exposure"
        output.append(row)
    return output


def filter_feedback_trajectory_summary(
    *, evaluated_since: str | None = None
) -> dict[str, Any]:
    """Explain how many retry rows were excluded by first-exposure assignment."""
    from aletheia.storage.sqlite import get_db

    time_clause = "WHERE datetime(first_evaluated_at) >= datetime(?)" if evaluated_since else ""
    params = (evaluated_since,) if evaluated_since else ()
    row = get_db().execute(
        "WITH trajectories AS ("
        " SELECT filter_name, stage, filter_version, outcome_candidate_key, "
        " COUNT(*) AS decision_rows, COUNT(DISTINCT decision) AS decision_kinds, "
        " MIN(evaluated_at) AS first_evaluated_at "
        " FROM filter_decisions "
        " WHERE decision IN ('passed','rejected') "
        "   AND outcome_candidate_key IS NOT NULL "
        " GROUP BY filter_name, stage, filter_version, outcome_candidate_key"
        ") "
        "SELECT COUNT(*) AS candidate_trajectories, "
        " COALESCE(SUM(decision_rows - 1), 0) AS excluded_later_rows, "
        " COALESCE(SUM(CASE WHEN decision_kinds > 1 THEN 1 ELSE 0 END), 0) "
        "   AS decision_transition_candidates "
        "FROM trajectories "
        f"{time_clause}",
        params,
    ).fetchone()
    return {
        "assignment_policy": "first_exposure",
        "candidate_trajectories": int(row["candidate_trajectories"] or 0),
        "excluded_later_rows": int(row["excluded_later_rows"] or 0),
        "decision_transition_candidates": int(row["decision_transition_candidates"] or 0),
    }


def _observer_range_bucket(value: float | None, boundaries: tuple[float, ...]) -> str:
    """Return a stable left-open/right-closed diagnostic bucket."""
    if value is None:
        return "unknown"
    lower = 0.0
    for upper in boundaries:
        if value <= upper:
            return f"{lower:g}_to_{upper:g}"
        lower = upper
    return f"gt_{boundaries[-1]:g}"


def _observer_rule_cohort(input_snapshot: dict[str, Any]) -> str:
    """Classify the persisted event-time rules without re-running current code."""
    matched = {
        str(item)
        for item in (input_snapshot.get("matched_rules") or [])
        if item is not None
    }
    has_rsi = "rsi_deviation" in matched
    has_atr = "atr_pct" in matched
    if has_rsi and has_atr:
        return "rsi_and_atr"
    if has_rsi:
        return "rsi_only"
    if has_atr:
        return "atr_only"
    return "neither"


def observer_pre_filter_diagnostics(
    *,
    evaluated_since: str | None = None,
    shrinkage_k: float = 20.0,
) -> dict[str, Any]:
    """Split Observer pass/reject economics by the exact persisted rule facts.

    This is deliberately diagnostic-only. It uses first exposure, event-time
    inputs and exact-version cost-aware outcomes; it never re-runs the current
    filter and never writes configuration or experiment state.
    """
    from aletheia.storage.sqlite import get_db

    time_clause = (
        " AND datetime(fd.first_evaluated_at) >= datetime(?)"
        if evaluated_since else ""
    )
    params = (evaluated_since,) if evaluated_since else ()
    rows = get_db().execute(
        FIRST_EXPOSURE_CTE_SQL
        + "SELECT fd.filter_version, fd.decision, fd.scanner_family, "
        "fd.direction, fd.market_type, fd.input_snapshot, "
        "fo.horizon, fo.status, fo.net_return_pct "
        "FROM ranked_filter_decisions fd "
        "JOIN filter_candidate_outcomes fo "
        "  ON fo.outcome_candidate_key = fd.outcome_candidate_key "
        "WHERE fd.exposure_rank = 1 "
        "  AND fd.filter_name = 'observer_pre_filter' "
        f"{time_clause}",
        params,
    ).fetchall()

    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    cell_priors: dict[tuple[Any, ...], list[float]] = {}
    for raw_row in rows:
        row = dict(raw_row)
        try:
            inputs = json.loads(row.get("input_snapshot") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            inputs = {}
        if not isinstance(inputs, dict):
            inputs = {}
        try:
            rsi = float(inputs["rsi"]) if inputs.get("rsi") is not None else None
        except (TypeError, ValueError):
            rsi = None
        try:
            atr_pct = (
                float(inputs["atr_pct"])
                if inputs.get("atr_pct") is not None else None
            )
        except (TypeError, ValueError):
            atr_pct = None
        rsi_deviation = abs(rsi - 50.0) if rsi is not None else None
        rule_cohort = _observer_rule_cohort(inputs)
        rsi_bucket = _observer_range_bucket(
            rsi_deviation, (6.0, 9.0, 12.0, 16.0)
        )
        atr_bucket = _observer_range_bucket(
            atr_pct, (0.4, 0.6, 0.8, 1.2)
        )
        cell_key = (
            row.get("filter_version"),
            row.get("scanner_family") or "unknown",
            row.get("direction"),
            row.get("market_type"),
            row.get("horizon"),
        )
        group_key = (
            *cell_key,
            row.get("decision"),
            rule_cohort,
            rsi_bucket,
            atr_bucket,
        )
        group = grouped.setdefault(
            group_key,
            {
                "filter_version": cell_key[0],
                "scanner_family": cell_key[1],
                "direction": cell_key[2],
                "market_type": cell_key[3],
                "horizon": cell_key[4],
                "decision": row.get("decision"),
                "rule_cohort": rule_cohort,
                "rsi_deviation_bucket": rsi_bucket,
                "atr_pct_bucket": atr_bucket,
                "n_total": 0,
                "n_net": 0,
                "_net_sum": 0.0,
            },
        )
        group["n_total"] += 1
        net = (
            float(row["net_return_pct"])
            if row.get("status") == "settled"
            and row.get("net_return_pct") is not None
            else None
        )
        if net is not None:
            group["n_net"] += 1
            group["_net_sum"] += net
            cell_priors.setdefault(cell_key, []).append(net)

    cohorts: list[dict[str, Any]] = []
    for group_key, group in grouped.items():
        cell_key = group_key[:5]
        prior_values = cell_priors.get(cell_key) or []
        prior = sum(prior_values) / len(prior_values) if prior_values else 0.0
        n_net = int(group["n_net"])
        raw_avg = group["_net_sum"] / n_net if n_net else None
        group.pop("_net_sum")
        group["outcome_coverage"] = round(
            n_net / int(group["n_total"]), 4
        ) if group["n_total"] else 0.0
        group["raw_avg_net_pct"] = (
            round(raw_avg, 6) if raw_avg is not None else None
        )
        group["shrunk_avg_net_pct"] = (
            round(
                (n_net * raw_avg + float(shrinkage_k) * prior)
                / (n_net + float(shrinkage_k)),
                6,
            )
            if raw_avg is not None else None
        )
        cohorts.append(group)

    cohorts.sort(
        key=lambda row: (
            str(row["scanner_family"]),
            str(row["direction"]),
            str(row["market_type"]),
            str(row["horizon"]),
            str(row["rule_cohort"]),
            str(row["rsi_deviation_bucket"]),
            str(row["atr_pct_bucket"]),
        )
    )
    return {
        "filter_name": "observer_pre_filter",
        "assignment_policy": "first_exposure",
        "read_only": True,
        "evaluated_since": evaluated_since,
        "rsi_deviation_boundaries": [6.0, 9.0, 12.0, 16.0],
        "atr_pct_boundaries": [0.4, 0.6, 0.8, 1.2],
        "row_count": len(rows),
        "cohort_count": len(cohorts),
        "cohorts": cohorts,
    }


def filter_version_status(filter_name: str) -> dict[str, Any]:
    """Return the latest persisted version and bounded version history."""
    from aletheia.storage.sqlite import get_db

    rows = get_db().execute(
        "SELECT filter_version, parameter_snapshot, MIN(evaluated_at) AS first_seen_at, "
        "MAX(evaluated_at) AS last_seen_at, COUNT(*) AS decision_count "
        "FROM filter_decisions WHERE filter_name=? "
        "GROUP BY filter_version, parameter_snapshot "
        "ORDER BY datetime(last_seen_at) DESC LIMIT 20",
        (filter_name,),
    ).fetchall()
    versions = [dict(row) for row in rows]
    return {
        "filter_name": filter_name,
        "current_filter_version": (
            versions[0]["filter_version"] if versions else None
        ),
        "versions": versions,
    }


def record_candidate_filter_trace(
    candidate: dict[str, Any],
    *,
    run_id: str | None = None,
    trace: list[dict[str, Any]] | None = None,
    raise_on_error: bool = False,
) -> int:
    """Persist a candidate's sequential gate trace using one shared outcome seed."""
    opportunity_id = candidate.get("opportunity_id")
    symbol = str(candidate.get("symbol") or "")
    if not opportunity_id or not symbol:
        return 0
    steps = trace if trace is not None else candidate.get("filter_trace") or []
    if not steps:
        return 0

    candidate_key = f"opportunity:{opportunity_id}"
    market = _normalize_market(candidate.get("market_type"))
    venue = candidate.get("venue") or _default_venue()
    entry_price = _exact_snapshot_price(symbol, market)
    if entry_price is None:
        entry_price = _positive_float(candidate.get("entry_price_usd"))
    evaluated_at = _utcnow()
    count = 0
    for sequence, step in enumerate(steps, start=1):
        try:
            record_filter_decision(
                candidate_key=candidate_key,
                candidate_kind="opportunity",
                parent_candidate_key=(
                    f"universe_member:{candidate['universe_member_id']}"
                    if candidate.get("universe_member_id")
                    else None
                ),
                run_id=run_id,
                opportunity_id=opportunity_id,
                universe_snapshot_id=candidate.get("universe_snapshot_id"),
                universe_member_id=candidate.get("universe_member_id"),
                venue=venue,
                symbol=symbol,
                market_type=market,
                direction=candidate.get("direction"),
                scanner_family=candidate.get("scanner_family"),
                regime_tag=candidate.get("regime_tag"),
                filter_name=str(step["filter_name"]),
                stage=str(step.get("stage") or "paper_ranker"),
                sequence_no=int(step.get("sequence_no") or sequence),
                decision=str(step["decision"]),
                reason_code=str(step.get("reason_code") or step["decision"]),
                reason_detail=step.get("reason_detail"),
                inputs=step.get("inputs") or {},
                parameters=step.get("parameters") or {},
                evaluated_at=evaluated_at,
                outcome_candidate_key=candidate_key,
                entry_price_usd=entry_price,
                entry_spread_bps=_optional_float(candidate.get("entry_spread_bps")),
                funding_rate_at_entry=_optional_float(candidate.get("funding_rate_at_entry")),
                raise_on_error=raise_on_error,
            )
            count += 1
        except Exception as exc:
            if raise_on_error:
                raise
            logger.warning(
                "candidate filter trace failed opportunity=%s filter=%s: %s",
                opportunity_id,
                step.get("filter_name"),
                exc,
            )
    return count


def record_observer_pre_filter_evidence(
    *,
    run_id: str | None,
    symbol: str,
    venue: str | None,
    market_type: str,
    scanner_direction: str | None,
    scanner_family: str | None,
    universe_snapshot_id: str | None,
    universe_member_id: str | None,
    decision: str,
    reason_code: str,
    inputs: dict[str, Any],
    parameters: dict[str, Any],
    entry_price_usd: float | None = None,
    entry_spread_bps: float | None = None,
    funding_rate_at_entry: float | None = None,
) -> str:
    """Record an Observer pre-filter without persisting raw market context."""
    market = _normalize_market(market_type)
    evaluation_id = run_id or f"direct-{uuid4().hex[:12]}"
    candidate_key = f"observer:{evaluation_id}:{venue or 'unknown'}:{symbol}:{market}"
    exact_price = _exact_snapshot_price(symbol, market)
    return record_filter_decision(
        candidate_key=candidate_key,
        candidate_kind="observer_evaluation",
        parent_candidate_key=(
            f"universe_member:{universe_member_id}" if universe_member_id else None
        ),
        run_id=run_id,
        universe_snapshot_id=universe_snapshot_id,
        universe_member_id=universe_member_id,
        venue=venue,
        symbol=symbol,
        market_type=market,
        direction=scanner_direction,
        scanner_family=scanner_family,
        filter_name="observer_pre_filter",
        stage="observer_pre_filter",
        decision=decision,
        reason_code=reason_code,
        inputs=inputs,
        parameters=parameters,
        outcome_candidate_key=candidate_key,
        entry_price_usd=exact_price or _positive_float(entry_price_usd),
        entry_spread_bps=entry_spread_bps,
        funding_rate_at_entry=funding_rate_at_entry,
    )


def record_observer_opportunity_emitted_evidence(
    *,
    run_id: str | None,
    opportunity_id: str,
    symbol: str,
    venue: str | None,
    market_type: str,
    direction: str,
    scanner_family: str | None,
    universe_snapshot_id: str | None,
    universe_member_id: str | None,
    confidence: str,
    confidence_score: int | None,
) -> str:
    """Persist the Observer's exact producer handoff for a new Opportunity."""
    market = _normalize_market(market_type)
    evidence_id = record_filter_decision(
        candidate_key=f"opportunity:{opportunity_id}",
        candidate_kind="opportunity",
        parent_candidate_key=(
            f"universe_member:{universe_member_id}" if universe_member_id else None
        ),
        run_id=run_id,
        opportunity_id=opportunity_id,
        universe_snapshot_id=universe_snapshot_id,
        universe_member_id=universe_member_id,
        venue=venue,
        symbol=symbol,
        market_type=market,
        direction=direction,
        scanner_family=scanner_family,
        filter_name="opportunity_emission",
        stage="market_observer",
        decision="passed",
        reason_code="opportunity_emitted",
        inputs={
            "confidence": confidence,
            "confidence_score": confidence_score,
        },
        parameters={"producer": "market_observer"},
        raise_on_error=True,
    )
    from aletheia.storage.sqlite import get_db

    if get_db().execute(
        "SELECT 1 FROM filter_decisions WHERE id = ?", (evidence_id,)
    ).fetchone() is None:
        raise RuntimeError("observer_opportunity_emission_evidence_not_persisted")
    return evidence_id


def ensure_candidate_outcomes(
    *,
    outcome_candidate_key: str,
    venue: str,
    symbol: str,
    market_type: str,
    direction: str,
    scanner_family: str | None,
    regime_tag: str | None,
    entry_price_usd: float,
    entry_spread_bps: float | None,
    funding_rate_at_entry: float | None,
    entry_ts: str,
) -> int:
    """Public registration seam for non-filter prospective control cohorts."""
    if str(direction or "").lower() not in _VALID_DIRECTIONS:
        return 0
    if not venue or not symbol or float(entry_price_usd or 0) <= 0:
        return 0
    return _ensure_candidate_outcomes(
        outcome_candidate_key=outcome_candidate_key,
        venue=venue,
        symbol=symbol,
        market_type=_normalize_market(market_type),
        direction=str(direction).lower(),
        scanner_family=scanner_family,
        regime_tag=regime_tag,
        entry_price_usd=float(entry_price_usd),
        entry_spread_bps=entry_spread_bps,
        funding_rate_at_entry=funding_rate_at_entry,
        entry_ts=entry_ts,
    )


def record_operator_filter_trace(
    decision_id: str,
    decision_result: dict[str, Any],
    *,
    raise_on_error: bool = False,
    require_trace: bool = False,
    require_outcome_seed: bool = False,
) -> int:
    """Persist System Operator gates for one authoritative PiDecision.

    General observations retain the historical best-effort default.  A terminal
    candidate skip calls this inside its claim transaction with ``require_trace``,
    ``require_outcome_seed`` and ``raise_on_error`` so no queue carrier can be
    consumed without its exact operator evidence and fixed-outcome cohort.
    """
    trace = decision_result.get("filter_trace") or []
    symbol = decision_result.get("symbol")
    direction = decision_result.get("direction")
    if not trace or not symbol:
        if require_trace:
            raise ValueError("terminal_operator_evidence_incomplete")
        return 0
    opportunity_id = decision_result.get("opportunity_id")
    opportunity = repo.get_opportunity(opportunity_id) if opportunity_id else None
    member = None
    if opportunity and opportunity.get("universe_member_id"):
        try:
            member = repo.get_universe_member(opportunity["universe_member_id"])
        except Exception:
            member = None
    market = _normalize_market(
        decision_result.get("market_type")
        or (opportunity or {}).get("market")
        or "spot"
    )
    venue = _default_venue()
    entry_snapshot = _exact_snapshot(str(symbol), market, max_age_seconds=120)
    entry_price = _positive_float((entry_snapshot or {}).get("price"))
    outcome_entry_ts = evaluated_at = decision_result.get("evaluated_at") or _utcnow()
    entry_spread_bps = _optional_float((member or {}).get("spread_bps"))
    funding_rate_at_entry = _optional_float((opportunity or {}).get("funding_rate"))
    outcome_seed_source = "live_snapshot"
    if entry_price is None and opportunity_id:
        queued_seed = _queued_candidate_outcome_seed(
            opportunity_id=str(opportunity_id),
            symbol=str(symbol),
            market_type=market,
            direction=str(direction or "").lower(),
        )
        if queued_seed is not None:
            entry_price = queued_seed["entry_price_usd"]
            outcome_entry_ts = queued_seed["entry_ts"]
            venue = queued_seed["venue"]
            entry_spread_bps = queued_seed["entry_spread_bps"]
            funding_rate_at_entry = queued_seed["funding_rate_at_entry"]
            outcome_seed_source = "queued_candidate_fixed"
    if require_outcome_seed and (
        direction not in _VALID_DIRECTIONS or not venue or entry_price is None
    ):
        raise ValueError("terminal_operator_outcome_unavailable")
    candidate_key = f"pi_decision:{decision_id}"
    count = 0
    for sequence, step in enumerate(trace, start=1):
        inputs = dict(step.get("inputs") or {})
        inputs["outcome_seed_source"] = outcome_seed_source
        if entry_snapshot is not None:
            inputs["entry_snapshot_id"] = entry_snapshot.get("id")
            inputs["entry_snapshot_at"] = entry_snapshot.get("snapshot_at")
            inputs["entry_price_usd"] = entry_price
        record_filter_decision(
            candidate_key=candidate_key,
            candidate_kind="pi_decision",
            parent_candidate_key=(
                f"opportunity:{opportunity_id}" if opportunity_id else None
            ),
            opportunity_id=opportunity_id,
            universe_snapshot_id=(opportunity or {}).get("universe_snapshot_id"),
            universe_member_id=(opportunity or {}).get("universe_member_id"),
            venue=venue,
            symbol=str(symbol),
            market_type=market,
            direction=direction,
            scanner_family=decision_result.get("scanner_family")
            or (opportunity or {}).get("scanner_family"),
            regime_tag=(opportunity or {}).get("regime_tag"),
            filter_name=str(step["filter_name"]),
            stage=str(step.get("stage") or "system_operator"),
            sequence_no=sequence,
            decision=str(step["decision"]),
            reason_code=str(step.get("reason_code") or step["decision"]),
            inputs=inputs,
            parameters=step.get("parameters") or {},
            evaluated_at=evaluated_at,
            outcome_candidate_key=candidate_key,
            entry_price_usd=entry_price,
            entry_spread_bps=entry_spread_bps,
            funding_rate_at_entry=funding_rate_at_entry,
            entry_ts=outcome_entry_ts,
            raise_on_error=raise_on_error,
        )
        count += 1
    return count


def _queued_candidate_outcome_seed(
    *,
    opportunity_id: str,
    symbol: str,
    market_type: str,
    direction: str,
) -> dict[str, Any] | None:
    """Return one immutable producer seed only when its whole cohort is exact."""
    try:
        from aletheia.storage.sqlite import get_db

        rows = get_db().execute(
            "SELECT venue, symbol, market_type, direction, entry_price_usd, "
            "entry_spread_bps, funding_rate_at_entry, entry_ts, horizon "
            "FROM filter_candidate_outcomes WHERE outcome_candidate_key=? "
            "ORDER BY horizon",
            (f"opportunity:{opportunity_id}",),
        ).fetchall()
    except Exception:
        return None
    if {row["horizon"] for row in rows} != set(_HORIZONS):
        return None
    normalized_rows = [dict(row) for row in rows]
    identities = {
        (row.get("venue"), row.get("symbol"), _normalize_market(row.get("market_type")),
         str(row.get("direction") or "").lower(), _positive_float(row.get("entry_price_usd")),
         row.get("entry_ts"))
        for row in normalized_rows
    }
    if len(identities) != 1:
        return None
    venue, seed_symbol, seed_market, seed_direction, entry_price, entry_ts = identities.pop()
    if (not venue or seed_symbol != symbol or seed_market != market_type
            or seed_direction != direction or entry_price is None
            or _parse_dt(str(entry_ts or "")) is None):
        return None
    first = normalized_rows[0]
    return {
        "venue": str(venue), "entry_price_usd": entry_price, "entry_ts": str(entry_ts),
        "entry_spread_bps": _optional_float(first.get("entry_spread_bps")),
        "funding_rate_at_entry": _optional_float(first.get("funding_rate_at_entry")),
    }


def record_risk_filter_decision(
    proposal: Any,
    *,
    risk_decision_id: str,
    approved: bool,
    violations: list[str],
) -> str:
    """Index the authoritative RiskDecision without weakening or duplicating it."""
    opportunity = repo.get_opportunity(proposal.opportunity_id) if proposal.opportunity_id else None
    member = None
    if opportunity and opportunity.get("universe_member_id"):
        try:
            member = repo.get_universe_member(opportunity["universe_member_id"])
        except Exception:
            member = None
    normalized_codes = sorted({str(item).split(":", 1)[0] for item in violations})
    return record_filter_decision(
        candidate_key=f"proposal:{proposal.id}",
        candidate_kind="proposal",
        parent_candidate_key=(
            f"opportunity:{proposal.opportunity_id}" if proposal.opportunity_id else None
        ),
        opportunity_id=proposal.opportunity_id,
        universe_snapshot_id=(opportunity or {}).get("universe_snapshot_id"),
        universe_member_id=(opportunity or {}).get("universe_member_id"),
        proposal_id=proposal.id,
        venue=_default_venue(),
        symbol=proposal.symbol,
        market_type=proposal.market_type,
        direction=proposal.direction,
        scanner_family=(opportunity or {}).get("scanner_family"),
        regime_tag=(opportunity or {}).get("regime_tag"),
        filter_name="risk_engine",
        stage="risk",
        decision="passed" if approved else "rejected",
        reason_code="risk_approved" if approved else "risk_rejected",
        inputs={
            "risk_decision_id": risk_decision_id,
            "violation_codes": normalized_codes,
            "violation_count": len(violations),
            "execution_mode": proposal.execution_mode,
        },
        parameters={"authority": "RiskDecision", "auto_tunable": False},
        outcome_candidate_key=f"proposal:{proposal.id}",
        entry_price_usd=_positive_float(proposal.price),
        entry_spread_bps=_optional_float((member or {}).get("spread_bps")),
        funding_rate_at_entry=_optional_float((opportunity or {}).get("funding_rate")),
    )


def _ensure_candidate_outcomes(
    *,
    outcome_candidate_key: str,
    venue: str,
    symbol: str,
    market_type: str,
    direction: str,
    scanner_family: str | None,
    regime_tag: str | None,
    entry_price_usd: float,
    entry_spread_bps: float | None,
    funding_rate_at_entry: float | None,
    entry_ts: str,
) -> int:
    outcomes = build_candidate_outcomes(
        outcome_candidate_key=outcome_candidate_key,
        venue=venue,
        symbol=symbol,
        market_type=market_type,
        direction=direction,
        scanner_family=scanner_family,
        regime_tag=regime_tag,
        entry_price_usd=entry_price_usd,
        entry_spread_bps=entry_spread_bps,
        funding_rate_at_entry=funding_rate_at_entry,
        entry_ts=entry_ts,
    )
    return repo.insert_filter_candidate_outcomes_batch(outcomes)


def build_candidate_outcomes(
    *,
    outcome_candidate_key: str,
    venue: str,
    symbol: str,
    market_type: str,
    direction: str,
    scanner_family: str | None,
    regime_tag: str | None,
    entry_price_usd: float,
    entry_spread_bps: float | None,
    funding_rate_at_entry: float | None,
    entry_ts: str,
    allocation_selected: bool | None = None,
    horizons: tuple[str, ...] | None = None,
) -> list[FilterCandidateOutcome]:
    """Build the requested fixed-horizon outcome rows without touching SQLite.

    General filter evidence keeps the canonical four-horizon matrix. A measured
    Learning exposure supplies its declared target horizon so unrelated horizons
    do not multiply the immutable experiment ledger.
    """

    entry_dt = _parse_dt(entry_ts)
    if entry_dt is None:
        return []
    outcomes: list[FilterCandidateOutcome] = []
    requested = tuple(_HORIZONS) if horizons is None else tuple(dict.fromkeys(horizons))
    for horizon in requested:
        delta = _HORIZONS.get(horizon)
        if delta is None:
            continue
        material = f"{outcome_candidate_key}|{horizon}"
        outcomes.append(FilterCandidateOutcome(
            id=hashlib.sha256(material.encode("utf-8")).hexdigest()[:12],
            outcome_candidate_key=outcome_candidate_key,
            venue=venue,
            symbol=symbol,
            market_type=market_type,
            direction=direction,
            scanner_family=scanner_family,
            regime_tag=regime_tag,
            horizon=horizon,
            allocation_selected=allocation_selected,
            entry_price_usd=entry_price_usd,
            entry_spread_bps=entry_spread_bps,
            funding_rate_at_entry=funding_rate_at_entry,
            entry_ts=entry_ts,
            due_at=(entry_dt + delta).isoformat(),
        ))
    return outcomes


def _filter_version(filter_name: str, parameter_json: str) -> str:
    material = f"{_SCHEMA_VERSION}|{filter_name}|{parameter_json}"
    return f"{_SCHEMA_VERSION}-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:12]}"


def _compact_candidate_facts(facts: list[dict[str, Any]]) -> dict[str, Any]:
    """Store repeated candidate dictionaries as a lossless field/row matrix."""
    fields = sorted({str(key) for fact in facts for key in fact})
    rows: list[list[Any]] = []
    for fact in facts:
        presence_mask = 0
        values: list[Any] = []
        for index, field in enumerate(fields):
            if field in fact:
                presence_mask |= 1 << index
                values.append(fact[field])
        rows.append([presence_mask, *values])
    return {"fields": fields, "rows": rows}


def _encode_candidate_facts(facts: list[dict[str, Any]]) -> str:
    """Compress the lossless row matrix while keeping a self-describing TEXT value."""
    raw = _canonical_json(_compact_candidate_facts(facts)).encode("utf-8")
    compressed = zlib.compress(raw, level=6)
    return "zlib-base64:" + base64.b64encode(compressed).decode("ascii")


def _normalize_market(market_type: str | None) -> str:
    return (
        "perp"
        if str(market_type or "spot").lower()
        in {"perp", "futures", "swap", "usdt_futures"}
        else "spot"
    )


def _sanitize_snapshot(value: Any, key: str = "") -> Any:
    key_lower = key.lower()
    if any(part in key_lower for part in _SENSITIVE_KEY_PARTS):
        return _DROP_SNAPSHOT_VALUE
    if isinstance(value, dict):
        sanitized_dict: dict[str, Any] = {}
        for child_key, child_value in value.items():
            sanitized = _sanitize_snapshot(child_value, str(child_key))
            if sanitized is not _DROP_SNAPSHOT_VALUE:
                sanitized_dict[str(child_key)] = sanitized
        return sanitized_dict
    if isinstance(value, (list, tuple)):
        return [
            sanitized
            for item in value[:50]
            if (sanitized := _sanitize_snapshot(item)) is not _DROP_SNAPSHOT_VALUE
        ]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value[:500] if isinstance(value, str) else value
    return str(value)[:500]


def _safe_text(value: str | None) -> str | None:
    if value is None:
        return None
    return str(value)[:500]


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _cohort_key(row: dict[str, Any]) -> tuple:
    return (
        row.get("filter_name"),
        row.get("stage"),
        row.get("filter_version"),
        row.get("scanner_family"),
        row.get("direction"),
        row.get("market_type"),
        row.get("horizon"),
    )


def _exact_snapshot(
    symbol: str,
    market_type: str,
    *,
    max_age_seconds: float | None = None,
) -> dict[str, Any] | None:
    """Read one exact typed snapshot, optionally requiring bounded freshness."""
    try:
        from aletheia.data.symbols import to_canonical

        rows = repo.list_market_snapshots(
            symbol=to_canonical(symbol),
            market_type=market_type,
            limit=1,
        )
        snapshot = rows[0] if rows else None
        if snapshot is None or _positive_float(snapshot.get("price")) is None:
            return None
        if max_age_seconds is not None:
            snapshot_at = _parse_dt(str(snapshot.get("snapshot_at") or ""))
            now = datetime.now(timezone.utc)
            if (
                snapshot_at is None
                or snapshot_at > now
                or (now - snapshot_at).total_seconds() > max_age_seconds
            ):
                return None
        return snapshot
    except Exception:
        return None


def _exact_snapshot_price(symbol: str, market_type: str) -> float | None:
    """Read only an exact typed snapshot; never fall back across spot/perp."""
    snapshot = _exact_snapshot(symbol, market_type)
    return _positive_float((snapshot or {}).get("price"))


def _default_venue() -> str | None:
    try:
        from aletheia.config.loader import load_config

        return load_config().market_data.ccxt_exchange
    except Exception:
        return None


def _positive_float(value: Any) -> float | None:
    parsed = _optional_float(value)
    return parsed if parsed is not None and parsed > 0 else None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _boot_commit() -> str | None:
    try:
        from aletheia.agent_runtime.build_info import get_boot_commit

        return get_boot_commit()
    except Exception:
        return None


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
