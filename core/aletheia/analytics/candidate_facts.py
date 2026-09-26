"""Complete candidate-denominator facts, separate from Position economics."""

from __future__ import annotations

import logging

logger = logging.getLogger("aletheia.analytics.candidate_facts")

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from typing import Any, Iterable
from aletheia.analytics.filter_evidence import decode_filter_decision_batch
from aletheia.analytics.outcome_semantics import candidate_outcome_temporal_reason

from aletheia.storage import repositories as repo
from aletheia.storage.sqlite import iso_instant_us, register_instant_us


FACT_CONTRACT_VERSION = "profit_loop_fact/v1"
_FIXED_HORIZONS = frozenset({"15m", "1h", "4h", "24h"})
CANDIDATE_TERMINAL_STATES = frozenset({
    "filtered",
    "opportunity_not_reached",
    "pi_skip",
    "pi_watch",
    "pi_hold",
    "proposal_not_reached",
    "risk_reject",
    "risk_not_reached",
    "approved_unfilled",
    "order_unfilled",
    "filled_without_position",
    "traded",
})


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


def _instant_us(value: object) -> int | None:
    if isinstance(value, datetime):
        return iso_instant_us(value.astimezone(timezone.utc).isoformat())
    return iso_instant_us(value)


def _identity(row: dict[str, Any]) -> str:
    return str(row.get("outcome_candidate_key") or row.get("candidate_key") or row.get("id"))


def _in_window(value: object, *, lower_us: int | None, upper_us: int) -> bool:
    value_us = _instant_us(value)
    return value_us is not None and value_us <= upper_us and (
        lower_us is None or value_us >= lower_us
    )


def _register_instant(db: Any) -> None:
    try:
        register_instant_us(db)
    except Exception:
        logger.warning("candidate_facts: register_instant_us failed", exc_info=True)


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
    cutoff_us = _instant_us(cutoff)
    if cutoff_us is None:
        return facts, exclusions
    epoch_start_us = _instant_us(epoch_start) if epoch_start is not None else None
    batch_sql = (
        "SELECT * FROM filter_decision_batches"
        " WHERE aletheia_instant_us(evaluated_at) <= ?"
        " ORDER BY aletheia_instant_us(evaluated_at), rowid"
    )
    for batch in _rows(
        db,
        batch_sql,
        (cutoff_us,),
    ):
        if not _visible(batch, cutoff):
            continue
        batch_in_epoch = _in_window(
            batch.get("evaluated_at"), lower_us=epoch_start_us, upper_us=cutoff_us
        )
        try:
            raw_facts = decode_filter_decision_batch(str(batch.get("candidate_facts") or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            if batch_in_epoch:
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
                if batch_in_epoch:
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
            if batch_in_epoch:
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
            if raw_at is None:
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
    ``filter_candidate_outcomes`` tables: SQL selects only current-window
    identities, while historical batch payloads are streamed only to reject
    those identities when necessary. It retains candidate identities (not full
    fact dicts), so memory stays proportional to the requested window even
    when the evidence tables hold millions of candidates. Semantics mirror the
    full report restricted to ``[lower, upper]``:

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
    _register_instant(db)
    lower_iso = lower.astimezone(timezone.utc).isoformat()
    upper_iso = upper.astimezone(timezone.utc).isoformat()
    lower_us = _instant_us(lower)
    upper_us = _instant_us(upper)
    if lower_us is None or upper_us is None:
        raise ValueError("invalid_coverage_window")

    prior_identity_sql = (
        "COALESCE(NULLIF(prior.outcome_candidate_key, ''), "
        "NULLIF(prior.candidate_key, ''), CAST(prior.id AS TEXT))"
    )

    cursor = db.execute(
        "WITH current_outcome AS ("
        "  SELECT fd.outcome_candidate_key AS identity "
        "  FROM filter_decisions fd "
        "  WHERE fd.outcome_candidate_key IS NOT NULL "
        "    AND fd.outcome_candidate_key <> '' "
        "    AND aletheia_instant_us(fd.evaluated_at) BETWEEN ? AND ? "
        "  GROUP BY fd.outcome_candidate_key"
        "), current_candidate AS ("
        "  SELECT fd.candidate_key AS identity "
        "  FROM filter_decisions fd "
        "  WHERE (fd.outcome_candidate_key IS NULL OR fd.outcome_candidate_key = '') "
        "    AND fd.candidate_key IS NOT NULL "
        "    AND fd.candidate_key <> '' "
        "    AND aletheia_instant_us(fd.evaluated_at) BETWEEN ? AND ? "
        "  GROUP BY fd.candidate_key"
        "), eligible_outcome AS ("
        "  SELECT current_outcome.identity "
        "  FROM current_outcome "
        "  WHERE NOT EXISTS ("
        "    SELECT 1 FROM filter_decisions prior "
        "    WHERE aletheia_instant_us(prior.evaluated_at) < ? "
        f"      AND {prior_identity_sql} = current_outcome.identity "
        "  )"
        "), eligible_candidate AS ("
        "  SELECT current_candidate.identity "
        "  FROM current_candidate "
        "  WHERE NOT EXISTS ("
        "    SELECT 1 FROM filter_decisions prior "
        "    WHERE aletheia_instant_us(prior.evaluated_at) < ? "
        f"      AND {prior_identity_sql} = current_candidate.identity "
        "  )"
        ") SELECT identity FROM eligible_outcome "
        "UNION ALL SELECT identity FROM eligible_candidate "
        "UNION ALL SELECT CAST(fd.id AS TEXT) AS identity "
        "FROM filter_decisions fd "
        "WHERE (fd.outcome_candidate_key IS NULL OR fd.outcome_candidate_key = '') "
        "  AND (fd.candidate_key IS NULL OR fd.candidate_key = '') "
        "  AND aletheia_instant_us(fd.evaluated_at) BETWEEN ? AND ?",
        (lower_us, upper_us, lower_us, upper_us, lower_us, lower_us, lower_us, upper_us),
    )
    identities = {
        str(row["identity"] if hasattr(row, "keys") else row[0])
        for row in cursor
    }

    unknown_row = db.execute(
        "SELECT COUNT(*) AS unknown FROM filter_decisions "
        "WHERE aletheia_instant_us(evaluated_at) IS NULL"
    ).fetchone()
    unknown_timestamp_units = int((unknown_row[0] if unknown_row is not None else 0) or 0)

    unavailable_units = 0
    batch_first_seen: dict[str, int] = {}
    batch_cursor = db.execute(
        "SELECT id, candidate_count, candidate_facts, evaluated_at "
        "FROM filter_decision_batches "
        "WHERE aletheia_instant_us(evaluated_at) <= ? "
        "ORDER BY aletheia_instant_us(evaluated_at), rowid",
        (upper_us,),
    )
    for batch in batch_cursor:
        batch_id = str(batch["id"])
        batch_in_window = _in_window(
            batch["evaluated_at"], lower_us=lower_us, upper_us=upper_us
        )
        try:
            raw_facts = decode_filter_decision_batch(str(batch["candidate_facts"] or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            if batch_in_window:
                unavailable_units += 1
            continue
        declared_count = batch["candidate_count"]
        if declared_count is not None:
            try:
                declared_count = int(declared_count)
            except (TypeError, ValueError, OverflowError):
                declared_count = None
            if declared_count is None or declared_count < 0 or declared_count != len(raw_facts):
                if batch_in_window:
                    unavailable_units += 1
                continue
        for sequence, raw in enumerate(raw_facts):
            if not isinstance(raw, dict):
                continue
            fact_at = _utc(raw.get("evaluated_at")) or _utc(batch["evaluated_at"])
            fact_us = _instant_us(fact_at)
            if fact_us is None or fact_us > upper_us:
                continue
            # For a pre-window container, only retain facts whose own
            # timestamp is in the requested window. Older facts are streamed
            # again below solely to disprove a bounded set of identities.
            if fact_us < lower_us and not batch_in_window:
                continue
            identity = str(
                raw.get("outcome_candidate_key")
                or raw.get("candidate_key")
                or f"{batch_id}:{sequence}"
            )
            batch_first_seen[identity] = min(
                fact_us, batch_first_seen.get(identity, fact_us)
            )
    for identity, first_us in batch_first_seen.items():
        if first_us < lower_us:
            identities.discard(identity)
        elif first_us <= upper_us:
            identities.add(identity)

    # A batch candidate can have an older normalized decision-row exposure.
    # Resolve only the current batch candidate set; never rebuild all identity
    # minima in Python.
    batch_identities = {
        identity
        for identity, first_us in batch_first_seen.items()
        if lower_us <= first_us <= upper_us
    }
    ordered_batch_identities = tuple(sorted(batch_identities))
    for offset in range(0, len(ordered_batch_identities), 400):
        chunk = ordered_batch_identities[offset : offset + 400]
        if not chunk:
            continue
        placeholders = ",".join("?" for _ in chunk)
        older_explicit = db.execute(
            "SELECT DISTINCT COALESCE(NULLIF(outcome_candidate_key, ''), "
            "NULLIF(candidate_key, ''), CAST(id AS TEXT)) AS identity "
            "FROM filter_decisions "
            "WHERE aletheia_instant_us(evaluated_at) IS NOT NULL "
            "AND aletheia_instant_us(evaluated_at) < ? "
            f"AND COALESCE(NULLIF(outcome_candidate_key, ''), "
            f"NULLIF(candidate_key, ''), CAST(id AS TEXT)) IN ({placeholders})",
            (lower_us, *chunk),
        )
        for row in older_explicit:
            identities.discard(str(row["identity"] if hasattr(row, "keys") else row[0]))

    # Batch payloads are legacy JSON blobs, so their identities are not
    # SQL-queryable without a schema change. Validate every historical payload
    # before using it to disprove a current identity; an invalid declaration is
    # unavailable evidence, not proof of prior exposure.
    if identities:
        historical_cursor = db.execute(
            "SELECT id, candidate_count, candidate_facts, evaluated_at "
            "FROM filter_decision_batches "
            "WHERE aletheia_instant_us(evaluated_at) < ? "
            "ORDER BY aletheia_instant_us(evaluated_at) DESC, rowid DESC",
            (lower_us,),
        )
        for batch in historical_cursor:
            try:
                raw_facts = decode_filter_decision_batch(
                    str(batch["candidate_facts"] or "[]")
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            declared_count = batch["candidate_count"]
            if declared_count is not None:
                try:
                    declared_count = int(declared_count)
                except (TypeError, ValueError, OverflowError):
                    declared_count = None
                if declared_count is None or declared_count < 0 or declared_count != len(raw_facts):
                    continue
            batch_id = str(batch["id"])
            for sequence, raw in enumerate(raw_facts):
                if not isinstance(raw, dict):
                    continue
                fact_at = _utc(raw.get("evaluated_at")) or _utc(batch["evaluated_at"])
                fact_us = _instant_us(fact_at)
                if fact_us is None or fact_us >= lower_us:
                    continue
                identity = str(
                    raw.get("outcome_candidate_key")
                    or raw.get("candidate_key")
                    or f"{batch_id}:{sequence}"
                )
                identities.discard(identity)
            if not identities:
                break

    settled_keys: set[str] = set()
    outcome_cursor = db.execute(
        "SELECT * FROM filter_candidate_outcomes "
        "WHERE horizon=? AND status='settled' "
        "AND aletheia_instant_us(entry_ts) <= ?",
        (horizon, upper_us),
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
    _register_instant(source)
    cutoff_us = _instant_us(cutoff)
    epoch_start_us = _instant_us(epoch_start) if epoch_start is not None else None
    if cutoff_us is None:
        raise ValueError("invalid as_of_at")
    explicit_sql = (
        "SELECT * FROM filter_decisions"
        " WHERE aletheia_instant_us(evaluated_at) <= ?"
        " ORDER BY aletheia_instant_us(evaluated_at), sequence_no, rowid"
    )
    explicit_params = (cutoff_us,)
    explicit = _rows(source, explicit_sql, explicit_params)
    filter_rows = [{**row, "source_table": "filter_decisions"} for row in explicit if _visible(row, cutoff)]
    batch_rows, batch_exclusions = _batch_facts(source, cutoff, epoch_start)
    filter_rows.extend(batch_rows)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in filter_rows:
        grouped[_identity(row)].append(row)
    if epoch_start_us is not None:
        grouped = {
            identity: rows
            for identity, rows in grouped.items()
            if (
                first_us := min(
                    (
                        value_us
                        for row in rows
                        if (value_us := _instant_us(row.get("evaluated_at"))) is not None
                    ),
                    default=None,
                )
            )
            is not None
            and epoch_start_us <= first_us <= cutoff_us
        }

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
