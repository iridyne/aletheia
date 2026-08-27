"""Complete candidate-denominator facts, separate from Position economics."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from typing import Any, Iterable
from aletheia.analytics.filter_evidence import decode_filter_decision_batch
from aletheia.learning.outcomes import candidate_outcome_temporal_reason

from aletheia.storage import repositories as repo


FACT_CONTRACT_VERSION = "profit_loop_fact/v1"
_FIXED_HORIZONS = frozenset({"15m", "1h", "4h", "24h"})


def _utc(value: str | datetime | None) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _visible(row: dict[str, Any], cutoff: datetime) -> bool:
    for field in ("evaluated_at", "created_at", "entry_ts", "filled_at", "opened_at"):
        if row.get(field):
            value = _utc(row[field])
            return value is not None and value <= cutoff
    return True


def _rows(db: Any, sql: str, params: Iterable[object] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in db.execute(sql, tuple(params)).fetchall()]


def _one(db: Any, table: str, identity: object) -> dict[str, Any] | None:
    if identity in (None, ""):
        return None
    exists = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if exists is None:
        return None
    row = db.execute(f"SELECT * FROM {table} WHERE id=?", (identity,)).fetchone()
    return dict(row) if row is not None else None


def _parsed(value: object, expected: type) -> object:
    if isinstance(value, expected):
        return value
    if not isinstance(value, str):
        return expected()
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return expected()
    return parsed if isinstance(parsed, expected) else expected()


def _batch_facts(
    db: Any,
    cutoff: datetime,
    epoch_start: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    facts: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    if epoch_start is None:
        batch_sql = (
            "SELECT * FROM filter_decision_batches"
            " WHERE datetime(evaluated_at) <= datetime(?)"
            " ORDER BY datetime(evaluated_at), rowid"
        )
        batch_params = (cutoff.isoformat(),)
    else:
        batch_sql = (
            "SELECT * FROM filter_decision_batches"
            " WHERE datetime(evaluated_at) >= datetime(?)"
            " AND datetime(evaluated_at) <= datetime(?)"
            " ORDER BY datetime(evaluated_at), rowid"
        )
        batch_params = (epoch_start.isoformat(), cutoff.isoformat())
    for batch in _rows(
        db,
        batch_sql,
        batch_params,
    ):
        if not _visible(batch, cutoff):
            continue
        try:
            raw_facts = decode_filter_decision_batch(str(batch.get("candidate_facts") or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            exclusions.append(
                {
                    "source_table": "filter_decision_batches",
                    "source_batch_id": batch.get("id"),
                    "event_at": batch.get("evaluated_at"),
                    "availability": "unavailable",
                    "unavailable_reason": "malformed_candidate_facts_json",
                }
            )
            continue
        declared_count = batch.get("candidate_count")
        if declared_count is not None:
            try:
                declared_count = int(declared_count)
            except (TypeError, ValueError, OverflowError):
                declared_count = None
            if declared_count is None or declared_count < 0 or declared_count != len(raw_facts):
                exclusions.append(
                    {
                        "source_table": "filter_decision_batches",
                        "source_batch_id": batch.get("id"),
                        "event_at": batch.get("evaluated_at"),
                        "availability": "unavailable",
                        "unavailable_reason": "candidate_fact_count_mismatch",
                        "declared_candidate_count": declared_count,
                        "decoded_candidate_count": len(raw_facts),
                    }
                )
                continue
        batch_invalid_reason: str | None = None
        for raw in raw_facts:
            if not isinstance(raw, dict):
                batch_invalid_reason = "malformed_candidate_fact_row"
                break
            raw_evaluated_at = raw.get("evaluated_at")
            if raw_evaluated_at not in (None, ""):
                parsed_at = _utc(raw_evaluated_at)
                if parsed_at is None:
                    batch_invalid_reason = "malformed_candidate_fact_timestamp"
                    break
                if parsed_at > cutoff:
                    batch_invalid_reason = "candidate_fact_after_cutoff"
                    break
        if batch_invalid_reason is not None:
            exclusions.append(
                {
                    "source_table": "filter_decision_batches",
                    "source_batch_id": batch.get("id"),
                    "event_at": batch.get("evaluated_at"),
                    "availability": "unavailable",
                    "unavailable_reason": batch_invalid_reason,
                }
            )
            continue
        for sequence, raw in enumerate(raw_facts):
            raw_at = _utc(raw.get("evaluated_at")) or _utc(batch.get("evaluated_at"))
            if epoch_start is not None and raw_at is not None and raw_at < epoch_start:
                continue
            fact = {
                **raw,
                "id": raw.get("id") or f"{batch['id']}:{sequence}",
                "candidate_key": raw.get("candidate_key")
                or raw.get("outcome_candidate_key")
                or f"{batch['id']}:{sequence}",
                "candidate_kind": raw.get("candidate_kind") or batch.get("candidate_kind"),
                "run_id": raw.get("run_id") or batch.get("run_id"),
                "filter_name": raw.get("filter_name") or batch.get("filter_name"),
                "stage": raw.get("stage") or batch.get("stage"),
                "decision": raw.get("decision") or raw.get("status") or "not_applicable",
                "reason_code": raw.get("reason_code") or "batch_fact",
                "filter_version": raw.get("filter_version") or batch.get("filter_version"),
                "code_commit": raw.get("code_commit") or batch.get("code_commit"),
                "evaluated_at": raw.get("evaluated_at") or batch.get("evaluated_at"),
                "sequence_no": raw.get("sequence_no", sequence),
                "source_table": "filter_decision_batches",
                "source_batch_id": batch["id"],
            }
            facts.append(fact)
    return facts, exclusions


def _terminal(
    filters: list[dict[str, Any]],
    opportunity: dict[str, Any] | None,
    decision: dict[str, Any] | None,
    proposal: dict[str, Any] | None,
    risk: dict[str, Any] | None,
    order: dict[str, Any] | None,
    position: dict[str, Any] | None,
) -> str:
    rejected = next(
        (row for row in filters if str(row.get("decision") or "").lower() in {"rejected", "filtered", "error"}),
        None,
    )
    if rejected is not None and opportunity is None:
        return "filtered"
    if opportunity is None:
        return "opportunity_not_reached"
    action = str((decision or {}).get("action") or "").lower()
    if action in {"skip", "watch", "hold"}:
        return f"pi_{action}"
    if proposal is None:
        return "proposal_not_reached"
    if risk is not None and not bool(risk.get("approved")):
        return "risk_reject"
    if risk is None:
        return "risk_not_reached"
    if order is None:
        return "approved_unfilled"
    if position is None:
        return "filled_without_position" if str(order.get("status") or "").lower() == "filled" else "order_unfilled"
    return "traded"


def candidate_coverage_counts(
    db: Any,
    *,
    horizon: str = "24h",
    lower: datetime,
    upper: datetime,
) -> dict[str, Any]:
    """Window-bounded candidate/outcome counts for status probes.

    Unlike :func:`build_candidate_facts`, this never materializes the full
    ``filter_decisions`` / ``filter_decision_batches`` /
    ``filter_candidate_outcomes`` tables: it streams only window-scoped rows
    and retains candidate identities (not full fact dicts), so it stays safe
    on small-memory runtimes even when the evidence tables hold millions of
    candidates. Semantics mirror the full report restricted to ``[lower, upper]``:

    - ``candidate_units``: distinct candidate identities whose first exposure
      falls in the window. For decision rows the first exposure is the earliest
      ``evaluated_at`` across all rows of the identity (matching the grouped
      ``event_at`` of the full report); batch facts keep their
      ``batch_id:sequence`` fallback identity and are counted when their own
      timestamp lies in the window;
    - ``settled_outcome_units``: window candidates whose fixed-horizon outcome is
      settled and settled no later than ``upper``;
    - ``unavailable_units``: window batches with malformed payloads or a declared
      candidate count that does not match the decoded facts;
    - ``unknown_timestamp_units``: decisions whose timestamp is missing or
      unparseable (table-wide, since such rows cannot be attributed to any
      window), matching the full report's unknown-timestamp accounting.
    """
    if horizon not in _FIXED_HORIZONS:
        raise ValueError(f"unsupported fixed horizon: {horizon}")
    if lower.tzinfo is None or upper.tzinfo is None or lower > upper:
        raise ValueError("invalid_coverage_window")
    lower_iso = lower.astimezone(timezone.utc).isoformat()
    upper_iso = upper.astimezone(timezone.utc).isoformat()

    identities: set[str] = set()
    cursor = db.execute(
        "SELECT COALESCE(NULLIF(outcome_candidate_key, ''), "
        "NULLIF(candidate_key, ''), CAST(id AS TEXT)) AS identity "
        "FROM filter_decisions "
        "WHERE datetime(evaluated_at) IS NOT NULL "
        "GROUP BY identity "
        "HAVING MIN(datetime(evaluated_at)) >= datetime(?) "
        "AND MIN(datetime(evaluated_at)) <= datetime(?)",
        (lower_iso, upper_iso),
    )
    for row in cursor:
        identities.add(str(row[0]))

    unknown_row = db.execute(
        "SELECT COUNT(*) AS unknown FROM filter_decisions "
        "WHERE datetime(evaluated_at) IS NULL"
    ).fetchone()
    unknown_timestamp_units = int((unknown_row[0] if unknown_row is not None else 0) or 0)

    unavailable_units = 0
    batch_cursor = db.execute(
        "SELECT id, candidate_count, candidate_facts, evaluated_at "
        "FROM filter_decision_batches "
        "WHERE datetime(evaluated_at) >= datetime(?) "
        "AND datetime(evaluated_at) <= datetime(?) "
        "ORDER BY datetime(evaluated_at), rowid",
        (lower_iso, upper_iso),
    )
    for batch in batch_cursor:
        batch_id = str(batch["id"])
        try:
            raw_facts = decode_filter_decision_batch(str(batch["candidate_facts"] or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            unavailable_units += 1
            continue
        declared_count = batch["candidate_count"]
        if declared_count is not None:
            try:
                declared_count = int(declared_count)
            except (TypeError, ValueError, OverflowError):
                declared_count = None
            if declared_count is None or declared_count < 0 or declared_count != len(raw_facts):
                unavailable_units += 1
                continue
        for sequence, raw in enumerate(raw_facts):
            if not isinstance(raw, dict):
                continue
            fact_at = _utc(raw.get("evaluated_at")) or _utc(batch["evaluated_at"])
            if fact_at is None or not (lower <= fact_at <= upper):
                continue
            identities.add(
                str(
                    raw.get("outcome_candidate_key")
                    or raw.get("candidate_key")
                    or f"{batch_id}:{sequence}"
                )
            )

    settled_keys: set[str] = set()
    outcome_cursor = db.execute(
        "SELECT * FROM filter_candidate_outcomes "
        "WHERE horizon=? AND status='settled' "
        "AND datetime(entry_ts) <= datetime(?)",
        (horizon, upper_iso),
    )
    for row in outcome_cursor:
        record = dict(row)
        key = str(record.get("outcome_candidate_key") or "")
        if (
            key in identities
            and candidate_outcome_temporal_reason(record, as_of_at=upper) is None
        ):
            settled_keys.add(key)

    candidate_count = len(identities)
    settled_count = len(settled_keys)
    return {
        "fixed_horizon": horizon,
        "window": {"lower": lower_iso, "upper": upper_iso},
        "candidate_units": candidate_count,
        "settled_outcome_units": settled_count,
        "outcome_coverage": settled_count / candidate_count if candidate_count else None,
        "unavailable_units": unavailable_units,
        "unknown_timestamp_units": unknown_timestamp_units,
    }


def build_candidate_facts(
    *,
    horizon: str = "24h",
    as_of_at: str | datetime | None = None,
    epoch_start_at: str | datetime | None = None,
    db: Any = None,
) -> dict[str, Any]:
    """Build first-exposure facts for every durable candidate, including rejects."""
    if horizon not in _FIXED_HORIZONS:
        raise ValueError(f"unsupported fixed horizon: {horizon}")
    if isinstance(as_of_at, str) and as_of_at != "" and _utc(as_of_at) is None:
        raise ValueError("invalid as_of_at")
    if (
        isinstance(epoch_start_at, str)
        and epoch_start_at != ""
        and _utc(epoch_start_at) is None
    ):
        raise ValueError("invalid epoch_start_at")
    if db is None:
        from aletheia.storage.sqlite import readonly_db

        with readonly_db() as read_db:
            return build_candidate_facts(
                horizon=horizon,
                as_of_at=as_of_at,
                epoch_start_at=epoch_start_at,
                db=read_db,
            )
    source = db
    explicit_cutoff = _utc(as_of_at) is not None
    cutoff = _utc(as_of_at) or datetime.now(timezone.utc)
    epoch_start = _utc(epoch_start_at)
    started_txn = not getattr(source, "in_transaction", False)
    if started_txn:
        source.execute("BEGIN DEFERRED")
    try:
        return _build_candidate_facts_inner(
            horizon=horizon,
            cutoff=cutoff,
            explicit_cutoff=explicit_cutoff,
            epoch_start=epoch_start,
            source=source,
        )
    finally:
        if started_txn:
            source.execute("ROLLBACK")


def _build_candidate_facts_inner(
    *,
    horizon: str,
    cutoff: datetime,
    explicit_cutoff: bool,
    epoch_start: datetime | None,
    source: Any,
) -> dict[str, Any]:
    if epoch_start is None:
        explicit_sql = (
            "SELECT * FROM filter_decisions"
            " WHERE datetime(evaluated_at) <= datetime(?)"
            " ORDER BY datetime(evaluated_at), sequence_no, rowid"
        )
        explicit_params = (cutoff.isoformat(),)
    else:
        explicit_sql = (
            "SELECT * FROM filter_decisions"
            " WHERE datetime(evaluated_at) >= datetime(?)"
            " AND datetime(evaluated_at) <= datetime(?)"
            " ORDER BY datetime(evaluated_at), sequence_no, rowid"
        )
        explicit_params = (epoch_start.isoformat(), cutoff.isoformat())
    explicit = _rows(source, explicit_sql, explicit_params)
    filter_rows = [{**row, "source_table": "filter_decisions"} for row in explicit if _visible(row, cutoff)]
    batch_rows, batch_exclusions = _batch_facts(source, cutoff, epoch_start)
    filter_rows.extend(batch_rows)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in filter_rows:
        identity = str(row.get("outcome_candidate_key") or row.get("candidate_key") or row.get("id"))
        grouped[identity].append(row)

    outcomes = _rows(
        source,
        "SELECT * FROM filter_candidate_outcomes"
        " WHERE horizon=?"
        " ORDER BY datetime(entry_ts), rowid",
        (horizon,),
    )
    outcome_by_key: dict[str, dict[str, Any]] = {}
    outcome_unavailable_reason_by_key: dict[str, str] = {}
    outcome_exclusions: list[dict[str, Any]] = []
    for row in outcomes:
        outcome_key = str(row.get("outcome_candidate_key") or "")
        if outcome_key not in grouped:
            continue
        if str(row.get("status") or "").lower() == "settled":
            reason = candidate_outcome_temporal_reason(row, as_of_at=cutoff)
            if reason:
                outcome_unavailable_reason_by_key.setdefault(outcome_key, reason)
                outcome_exclusions.append(
                    {
                        "source_table": "filter_candidate_outcomes",
                        "source_outcome_id": row.get("id"),
                        "outcome_candidate_key": row.get("outcome_candidate_key"),
                        "horizon": row.get("horizon"),
                        "event_at": row.get("settled_at"),
                        "availability": "unavailable",
                        "unavailable_reason": reason,
                    }
                )
                continue
            outcome_by_key[outcome_key] = row
        elif _visible(row, cutoff):
            outcome_by_key[outcome_key] = row

    facts: list[dict[str, Any]] = []
    terminal_counts: defaultdict[str, int] = defaultdict(int)
    for fact_id, rows in sorted(
        grouped.items(),
        key=lambda item: (
            _utc(item[1][0].get("evaluated_at")) or datetime.max.replace(tzinfo=timezone.utc),
            item[1][0].get("sequence_no", 0),
            str(item[1][0].get("id", "")),
        ),
    ):
        ordered = sorted(
            rows,
            key=lambda row: (
                _utc(row.get("evaluated_at")) or datetime.max.replace(tzinfo=timezone.utc),
                int(row.get("sequence_no") or 0),
                str(row.get("id") or ""),
            ),
        )
        first = ordered[0]
        opportunity_id = next(
            (str(row["opportunity_id"]) for row in ordered if row.get("opportunity_id")),
            None,
        )
        candidate_key = str(first.get("candidate_key") or "")
        candidate_kind = str(first.get("candidate_kind") or "")
        decision_id = (
            candidate_key.removeprefix("pi_decision:")
            if candidate_kind == "pi_decision" and candidate_key.startswith("pi_decision:")
            else None
        )
        decision = _one(source, "pi_decisions", decision_id)
        if decision is not None and not _visible(decision, cutoff):
            decision = None
        if decision is None and opportunity_id and decision_id is None:
            decisions = _rows(
                source,
                "SELECT * FROM pi_decisions WHERE opportunity_id=? "
                "AND action!='signal_pending' ORDER BY datetime(created_at), rowid",
                (opportunity_id,),
            )
            decisions = [row for row in decisions if _visible(row, cutoff)]
            decision = decisions[0] if len(decisions) == 1 else None
        signal_source = str((decision or {}).get("signal_source") or "")
        opportunity = None
        if opportunity_id:
            if signal_source == "serenity":
                opportunity = _one(source, "serenity_opportunities", opportunity_id)
            elif signal_source == "market_observer":
                opportunity = _one(source, "opportunities", opportunity_id)
            else:
                market_opportunity = _one(source, "opportunities", opportunity_id)
                serenity_opportunity = _one(source, "serenity_opportunities", opportunity_id)
                opportunity = (
                    market_opportunity or serenity_opportunity
                    if (market_opportunity is None) != (serenity_opportunity is None)
                    else None
                )
        if opportunity is not None and not _visible(opportunity, cutoff):
            opportunity = None
        proposal_id = (
            candidate_key.removeprefix("proposal:")
            if candidate_kind == "proposal" and candidate_key.startswith("proposal:")
            else (decision or {}).get("proposal_id")
            or next((row.get("proposal_id") for row in ordered if row.get("proposal_id")), None)
        )
        proposal = _one(source, "trade_proposals", proposal_id)
        if proposal is not None and not _visible(proposal, cutoff):
            proposal = None
        orders = (
            _rows(
                source,
                "SELECT * FROM orders WHERE proposal_id=? "
                "AND COALESCE(trim(close_reason),'')='' "
                "ORDER BY datetime(created_at), rowid",
                (proposal_id,),
            )
            if proposal_id
            else []
        )
        orders = [row for row in orders if _visible(row, cutoff)]
        order = orders[0] if len(orders) == 1 else None
        # #1267: scrub post-cutoff fill state from Orders visible before cutoff
        if order is not None and explicit_cutoff:
            filled_at = _utc(order.get("filled_at"))
            if filled_at is not None and filled_at > cutoff:
                order = {
                    **order,
                    "filled_at": None,
                    "position_id": None,
                    "status": "open",
                }
        risk = _one(source, "risk_decisions", (order or {}).get("risk_decision_id"))
        if risk is not None and not _visible(risk, cutoff):
            risk = None
        if risk is None and proposal_id:
            risks = _rows(
                source,
                "SELECT * FROM risk_decisions WHERE proposal_id=? "
                "AND decision_role='authoritative' ORDER BY datetime(created_at), rowid",
                (proposal_id,),
            )
            risks = [row for row in risks if _visible(row, cutoff)]
            risk = risks[0] if len(risks) == 1 else None
        position = _one(source, "positions", (order or {}).get("position_id"))
        # A Position is a lifecycle transition, not an immutable row-existence
        # fact. Missing/invalid opened_at cannot prove historical visibility.
        if position is not None and (
            not position.get("opened_at") or not _visible(position, cutoff)
        ):
            position = None
        outcome = outcome_by_key.get(fact_id)
        terminal = _terminal(ordered, opportunity, decision, proposal, risk, order, position)
        terminal_counts[terminal] += 1
        facts.append(
            {
                "contract_version": FACT_CONTRACT_VERSION,
                "fact_unit": "candidate_fact",
                "fact_id": fact_id,
                "event_at": first.get("evaluated_at"),
                "evidence_available_at": (outcome or {}).get("settled_at"),
                "as_of_at": cutoff.isoformat(),
                "availability": "available",
                "unavailable_reason": None,
                "first_exposure": first,
                "filter_path": ordered,
                "terminal_state": terminal,
                "identity": {
                    "outcome_candidate_key": first.get("outcome_candidate_key"),
                    "candidate_key": first.get("candidate_key"),
                    "opportunity_id": opportunity_id,
                    "pi_decision_id": (decision or {}).get("id"),
                    "proposal_id": (proposal or {}).get("id"),
                    "risk_decision_id": (risk or {}).get("id"),
                    "order_id": (order or {}).get("id"),
                    "position_id": (position or {}).get("id"),
                },
                "funnel": {
                    "selection_reached": True,
                    "opportunity_reached": opportunity is not None,
                    "decision_reached": decision is not None,
                    "proposal_reached": proposal is not None,
                    "risk_reached": risk is not None,
                    "risk_approved": bool(risk.get("approved")) if risk else None,
                    "order_reached": order is not None,
                    "position_reached": position is not None,
                },
                "fixed_horizon_outcome": outcome,
                "fixed_horizon_outcome_unavailable_reason": (
                    outcome_unavailable_reason_by_key.get(fact_id)
                    if outcome is None
                    else None
                ),
                "fixed_horizon": horizon,
                "variable_horizon_position_outcome": None,
            }
        )

    outcome_count = sum(
        1
        for fact in facts
        if fact["fixed_horizon_outcome"] is not None
        and str(fact["fixed_horizon_outcome"].get("status") or "").lower()
        == "settled"
    )
    exclusions = [*batch_exclusions, *outcome_exclusions]
    unavailable_reasons = Counter(
        str(item.get("unavailable_reason") or "source_unavailable")
        for item in exclusions
    )
    return {
        "contract_version": FACT_CONTRACT_VERSION,
        "fact_unit": "candidate_fact",
        "epoch_start_at": epoch_start.isoformat() if epoch_start is not None else None,
        "as_of_at": cutoff.isoformat(),
        "fixed_horizon": horizon,
        "read_only": True,
        "candidate_count": len(facts),
        "settled_outcome_count": outcome_count,
        "outcome_coverage": outcome_count / len(facts) if facts else None,
        "unavailable_count": len(exclusions),
        "unavailable_reasons": dict(sorted(unavailable_reasons.items())),
        "exclusions": exclusions,
        "terminal_counts": dict(sorted(terminal_counts.items())),
        "facts": facts,
    }
