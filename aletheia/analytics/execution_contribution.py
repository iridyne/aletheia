"""Read-only paired cost attribution for native local-Paper execution."""

from __future__ import annotations

from collections import Counter, defaultdict
import math
from statistics import fmean
from typing import Any

from aletheia.analytics.outcome_integrity import (
    eligible_strategy_outcomes,
    validated_funding_economics,
)


CONTRACT_VERSION = "execution-cost-pair/v1"
BASELINE_POLICY = "frictionless-requested-fill/v1"
COST_MODEL_VERSION = "paper-execution-reference/v1"
ALPHA_THRESHOLD_PCT = 0.10


def empty_execution_cost_contribution(reason: str) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "baseline_policy": BASELINE_POLICY,
        "cost_model_version": COST_MODEL_VERSION,
        "scope": "native_paper_internal",
        "statistical_unit": "position_id",
        "economic_unit": "net_return_pct",
        "behavior_authority": False,
        "promotion_eligible": False,
        "verdict": "insufficient_data",
        "contribution_verdict": "insufficient_data",
        "measurement_status": "not_measured",
        "causal_grade": "insufficient",
        "evidence_type": "prospective_paired_execution_accounting",
        "sample_size": 0,
        "raw_outcome_count": 0,
        "eligible_count": 0,
        "excluded_count": 0,
        "actual_intervention_count": 0,
        "baseline": None,
        "observed": None,
        "delta": None,
        "incremental_net_pnl_total": None,
        "incremental_net_pnl_mean": None,
        "fee_total": None,
        "price_impact_total": None,
        "confidence_label": "insufficient",
        "execution_identity": None,
        "exclusion_counts": {},
        "reason": reason,
        "details": {},
    }


def build_execution_cost_contribution(
    db,
    *,
    cutoff: str,
    min_sample_size: int = 5,
) -> dict[str, Any]:
    """Compare actual Paper fills with same-position frictionless references."""

    outcomes = eligible_strategy_outcomes(
        execution_mode="paper_internal",
        closed_after=cutoff,
    )
    scoped = [row for row in outcomes if _position_behavior_identity(row) is not None]
    if not scoped:
        return empty_execution_cost_contribution(
            "No current Paper execution behavior identity is available."
        )
    current_identity = _position_behavior_identity(max(
        scoped,
        key=lambda row: (str(row["closed_at"]), str(row["id"])),
    ))
    exclusions: Counter[str] = Counter()
    records: list[dict[str, Any]] = []
    for position in outcomes:
        if _position_behavior_identity(position) != current_identity:
            exclusions["non_current_execution_behavior"] += 1
            continue
        record, reason = _paired_position_cost(db, position)
        if reason is not None:
            exclusions[reason] += 1
        else:
            records.append(record)

    base = empty_execution_cost_contribution(
        "No identity-complete paired Paper execution costs are available."
    )
    base.update({
        "raw_outcome_count": len(outcomes),
        "eligible_count": len(records),
        "excluded_count": sum(exclusions.values()),
        "actual_intervention_count": len(records),
        "exclusion_counts": dict(sorted(exclusions.items())),
        "sample_size": len(records),
    })
    if not records:
        return base

    identities = {
        _execution_behavior_identity(row)
        for row in records
    }
    if len(identities) != 1:
        base["reason"] = "mixed_execution_identity"
        base["details"] = {"identity_count": len(identities)}
        return base
    entry_behavior, close_behavior = identities.pop()
    base["execution_identity"] = {
        "entry_behavior_fingerprint": entry_behavior,
        "close_behavior_fingerprint": close_behavior,
        "source_code_commits": sorted({
            row[field]
            for row in records
            for field in ("entry_producer_commit", "close_producer_commit")
        }),
    }
    if len(records) < max(1, int(min_sample_size)):
        base["reason"] = "insufficient_paired_execution_samples"
        return base

    baseline = fmean(row["reference_return_pct"] for row in records)
    observed = fmean(row["actual_return_pct"] for row in records)
    delta = observed - baseline
    verdict = (
        "positive"
        if delta > ALPHA_THRESHOLD_PCT
        else "negative"
        if delta < -ALPHA_THRESHOLD_PCT
        else "neutral"
    )
    quote_deltas = [row["incremental_net_pnl"] for row in records]
    base.update({
        "verdict": verdict,
        "contribution_verdict": verdict,
        "measurement_status": "measured_contribution",
        "causal_grade": "strong_prospective_paired_accounting",
        "baseline": round(baseline, 4),
        "observed": round(observed, 4),
        "delta": round(delta, 4),
        "incremental_net_pnl_total": round(sum(quote_deltas), 8),
        "incremental_net_pnl_mean": round(fmean(quote_deltas), 8),
        "fee_total": round(sum(row["fee_total"] for row in records), 8),
        "price_impact_total": round(
            sum(row["price_impact"] for row in records), 8
        ),
        "confidence_label": _confidence_label(len(records)),
        "reason": "measured_against_frictionless_requested_fill_control",
        "details": {
            "estimand": "same_position_execution_cost_delta",
            "control": "requested_entry_and_exit_prices_with_zero_fees",
            "treatment": "actual_local_fills_and_fees",
            "same_quantity_direction_timing_and_funding": True,
            "routing_value_measured": False,
            "portfolio_interference_modeled": False,
        },
    })
    return base


def _paired_position_cost(
    db,
    position: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    if position.get("source") != "native":
        return None, "non_native_position"
    if any(
        position.get(field) in {None, ""}
        for field in (
            "closed_runtime_session_id",
            "closed_producer_commit",
            "closed_behavior_fingerprint",
        )
    ):
        return None, "incomplete_close_provenance"

    rows = [
        dict(row)
        for row in db.execute(
            "SELECT o.id AS order_id, o.proposal_id AS order_proposal_id, "
            "o.risk_decision_id, o.symbol AS order_symbol, "
            "o.market_type AS order_market_type, o.direction AS order_direction, "
            "o.quantity AS order_quantity, o.price AS order_price, "
            "o.reference_price, o.reference_price_source, o.execution_mode, "
            "o.close_reason AS order_close_reason, o.status AS order_status, "
            "o.source AS order_source, o.filled_at AS order_filled_at, "
            "o.runtime_session_id AS order_runtime_session_id, "
            "o.producer_commit AS order_producer_commit, "
            "o.behavior_fingerprint AS order_behavior_fingerprint, "
            "f.id AS fill_id, f.fill_price, f.fill_quantity, f.fee, "
            "f.fee_currency, f.fee_source, f.filled_at AS fill_filled_at, "
            "f.runtime_session_id AS fill_runtime_session_id, "
            "f.producer_commit AS fill_producer_commit, "
            "f.behavior_fingerprint AS fill_behavior_fingerprint, "
            "rd.id AS joined_risk_decision_id, rd.proposal_id AS risk_proposal_id, "
            "rd.approved, rd.decision_role, rd.evaluated_quantity, "
            "rd.proposal_commit_hash, rd.proposal_execution_mode, "
            "rd.allowed_execution_mode, "
            "rd.runtime_session_id AS risk_runtime_session_id, "
            "rd.producer_commit AS risk_producer_commit, "
            "rd.behavior_fingerprint AS risk_behavior_fingerprint, "
            "tp.id AS joined_proposal_id, tp.symbol AS proposal_symbol, "
            "tp.market_type AS proposal_market_type, "
            "tp.direction AS proposal_direction, tp.quantity AS proposal_quantity, "
            "tp.price AS proposal_price, tp.execution_mode AS proposal_execution_mode, "
            "tp.source AS proposal_source, tp.commit_hash "
            "FROM orders o "
            "LEFT JOIN fills f ON f.order_id=o.id "
            "LEFT JOIN risk_decisions rd ON rd.id=o.risk_decision_id "
            "LEFT JOIN trade_proposals tp ON tp.id=o.proposal_id "
            "WHERE o.position_id=? "
            "ORDER BY datetime(o.created_at), o.id, f.id",
            (position["id"],),
        ).fetchall()
    ]
    by_order: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_order[str(row["order_id"])].append(row)
    if not by_order or any(len(group) != 1 for group in by_order.values()):
        return None, "ambiguous_execution_fill_chain"
    chain = [group[0] for group in by_order.values()]
    entries = [row for row in chain if not str(row.get("order_close_reason") or "").strip()]
    closes = [row for row in chain if str(row.get("order_close_reason") or "").strip()]
    if not entries or len(closes) != 1:
        return None, "incomplete_execution_order_chain"

    for row in chain:
        reason = _order_fill_exclusion(row, position)
        if reason is not None:
            return None, reason
    for row in entries:
        reason = _entry_authority_exclusion(row, position)
        if reason is not None:
            return None, reason

    close = closes[0]
    if close["order_close_reason"] != position.get("close_reason"):
        return None, "execution_close_reason_mismatch"
    if (
        close["order_runtime_session_id"] != position["closed_runtime_session_id"]
        or close["order_producer_commit"] != position["closed_producer_commit"]
        or close["order_behavior_fingerprint"]
        != position["closed_behavior_fingerprint"]
    ):
        return None, "execution_close_provenance_mismatch"

    quantity = _finite(position.get("quantity"), positive=True)
    entry_quantity = sum(float(row["fill_quantity"]) for row in entries)
    close_quantity = float(close["fill_quantity"])
    if (
        quantity is None
        or not _same(entry_quantity, quantity)
        or not _same(close_quantity, quantity)
    ):
        return None, "execution_position_quantity_mismatch"

    actual_entry = sum(
        float(row["fill_price"]) * float(row["fill_quantity"]) for row in entries
    ) / quantity
    reference_entry = sum(
        float(row["reference_price"]) * float(row["fill_quantity"])
        for row in entries
    ) / quantity
    actual_exit = float(close["fill_price"])
    reference_exit = float(close["reference_price"])
    if not _same(actual_entry, position.get("entry_price")) or not _same(
        actual_exit, position.get("current_price")
    ):
        return None, "execution_position_price_mismatch"

    entry_fee = sum(float(row["fee"]) for row in entries)
    exit_fee = float(close["fee"])
    if not _same(entry_fee, position.get("entry_fee")) or not _same(
        exit_fee, position.get("exit_fee")
    ):
        return None, "execution_position_fee_mismatch"
    funding, _funding_source = validated_funding_economics(
        market_type=position.get("market_type"),
        execution_mode=position.get("execution_mode"),
        funding_cost=position.get("funding_cost"),
        funding_source=position.get("funding_source"),
    )
    actual_net = _finite(position.get("realized_pnl"))
    if funding is None:
        return None, "incomplete_execution_funding_economics"
    if actual_net is None:
        return None, "incomplete_execution_economics"

    direction = str(position.get("direction") or "").lower()
    if direction in {"long", "buy"}:
        actual_gross = (actual_exit - actual_entry) * quantity
        reference_gross = (reference_exit - reference_entry) * quantity
    elif direction in {"short", "sell"}:
        actual_gross = (actual_entry - actual_exit) * quantity
        reference_gross = (reference_entry - reference_exit) * quantity
    else:
        return None, "invalid_execution_direction"
    expected_actual = actual_gross - entry_fee - exit_fee - funding
    if not _same(actual_net, expected_actual):
        return None, "execution_economics_mismatch"

    reference_net = reference_gross - funding
    notional = reference_entry * quantity
    incremental = actual_net - reference_net
    price_impact = actual_gross - reference_gross
    if notional <= 0 or not _same(incremental, price_impact - entry_fee - exit_fee):
        return None, "execution_economics_mismatch"
    first_entry = entries[0]
    return {
        "position_id": position["id"],
        "reference_return_pct": reference_net / notional * 100.0,
        "actual_return_pct": actual_net / notional * 100.0,
        "incremental_net_pnl": incremental,
        "fee_total": entry_fee + exit_fee,
        "price_impact": price_impact,
        "entry_producer_commit": first_entry["order_producer_commit"],
        "entry_behavior_fingerprint": first_entry["order_behavior_fingerprint"],
        "close_producer_commit": close["order_producer_commit"],
        "close_behavior_fingerprint": close["order_behavior_fingerprint"],
    }, None


def _execution_behavior_identity(row: dict[str, Any]) -> tuple[str, str]:
    return (
        str(row["entry_behavior_fingerprint"]),
        str(row["close_behavior_fingerprint"]),
    )


def _position_behavior_identity(row: dict[str, Any]) -> tuple[str, str] | None:
    values = (row.get("behavior_fingerprint"), row.get("closed_behavior_fingerprint"))
    if any(value in {None, ""} for value in values):
        return None
    return str(values[0]), str(values[1])


def _order_fill_exclusion(
    row: dict[str, Any],
    position: dict[str, Any],
) -> str | None:
    if (
        row.get("order_status") != "filled"
        or row.get("execution_mode") != "paper_internal"
        or row.get("order_source") != "native"
        or row.get("fill_id") is None
    ):
        return "invalid_execution_order_state"
    if any(
        str(row.get(order_field) or "").lower()
        != str(position.get(position_field) or "").lower()
        for order_field, position_field in (
            ("order_symbol", "symbol"),
            ("order_market_type", "market_type"),
            ("order_direction", "direction"),
        )
    ):
        return "execution_instrument_mismatch"
    values = {
        field: _finite(row.get(field), positive=field != "fee")
        for field in (
            "order_quantity", "order_price", "reference_price",
            "fill_price", "fill_quantity", "fee",
        )
    }
    if any(value is None for value in values.values()) or float(values["fee"]) < 0:
        return "incomplete_execution_fill_economics"
    if (
        not _same(values["order_quantity"], values["fill_quantity"])
        or not _same(values["order_price"], values["fill_price"])
        or row.get("fee_currency") != "USDT"
        or row.get("fee_source") != "estimated"
        or row.get("order_filled_at") != row.get("fill_filled_at")
    ):
        return "execution_fill_mismatch"
    order_provenance = tuple(
        row.get(field)
        for field in (
            "order_runtime_session_id",
            "order_producer_commit",
            "order_behavior_fingerprint",
        )
    )
    fill_provenance = tuple(
        row.get(field)
        for field in (
            "fill_runtime_session_id",
            "fill_producer_commit",
            "fill_behavior_fingerprint",
        )
    )
    if any(value in {None, ""} for value in order_provenance) or (
        order_provenance != fill_provenance
    ):
        return "execution_fill_provenance_mismatch"
    expected_source = (
        "paper_close_mark"
        if str(row.get("order_close_reason") or "").strip()
        else "risk_verified_proposal_price"
    )
    if row.get("reference_price_source") != expected_source:
        return "execution_reference_provenance_mismatch"
    return None


def _entry_authority_exclusion(
    row: dict[str, Any],
    position: dict[str, Any],
) -> str | None:
    if (
        row.get("joined_risk_decision_id") != row.get("risk_decision_id")
        or row.get("approved") != 1
        or row.get("decision_role") != "authoritative"
        or row.get("proposal_execution_mode") != "paper_internal"
        or row.get("allowed_execution_mode") != "paper_internal"
        or row.get("joined_proposal_id") != position.get("proposal_id")
        or row.get("order_proposal_id") != position.get("proposal_id")
        or row.get("risk_proposal_id") != position.get("proposal_id")
        or row.get("proposal_source") != "native"
    ):
        return "execution_authority_mismatch"
    if any(
        str(row.get(proposal_field) or "").lower()
        != str(position.get(position_field) or "").lower()
        for proposal_field, position_field in (
            ("proposal_symbol", "symbol"),
            ("proposal_market_type", "market_type"),
            ("proposal_direction", "direction"),
        )
    ):
        return "execution_authority_mismatch"
    if (
        not _same(row.get("proposal_quantity"), row.get("order_quantity"))
        or not _same(row.get("evaluated_quantity"), row.get("order_quantity"))
        or not _same(row.get("proposal_price"), row.get("reference_price"))
        or row.get("proposal_commit_hash") != row.get("commit_hash")
    ):
        return "execution_authority_mismatch"
    order_provenance = (
        row.get("order_runtime_session_id"),
        row.get("order_producer_commit"),
        row.get("order_behavior_fingerprint"),
    )
    risk_provenance = (
        row.get("risk_runtime_session_id"),
        row.get("risk_producer_commit"),
        row.get("risk_behavior_fingerprint"),
    )
    if order_provenance != risk_provenance:
        return "execution_authority_provenance_mismatch"
    return None


def _finite(value: Any, *, positive: bool = False) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or (positive and number <= 0):
        return None
    return number


def _same(left: Any, right: Any) -> bool:
    first = _finite(left)
    second = _finite(right)
    if first is None or second is None:
        return False
    return math.isclose(
        first,
        second,
        rel_tol=1e-9,
        abs_tol=max(1e-8, abs(first) * 1e-9, abs(second) * 1e-9),
    )


def _confidence_label(sample_size: int) -> str:
    if sample_size >= 30:
        return "high"
    if sample_size >= 10:
        return "medium"
    return "low"
