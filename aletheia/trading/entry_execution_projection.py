"""Stage 1 read-only projection; not connected to submission or persistence.

The caller supplies validated snapshots from existing owners. A directive is a
projection of the next bounded action, never permission to bypass Gateway/Risk.
This will replace duplicated watcher/lifecycle branching at a later cutover;
Stage 1 deliberately preserves the original Plan's tactic and Maker lifetime.
"""
from dataclasses import dataclass
from typing import Literal

from aletheia.trading.entry_plan import EntryPlan


@dataclass(frozen=True)
class EntryExecutionProjection:
    activation: Literal["IMMEDIATE", "CONDITIONAL"]
    capture_priority: Literal["PRICE_FIRST", "FILL_FIRST"]
    # Keep original semantics visible: PRICE_FIRST does not erase a legacy timeout.
    max_wait_sec: int


def normalize_entry_execution(plan: EntryPlan) -> EntryExecutionProjection:
    combinations = {
        ("RESTING_MAKER", "POST_ONLY_LIMIT"): ("IMMEDIATE", "PRICE_FIRST"),
        ("TRIGGERED", "POST_ONLY_LIMIT"): ("CONDITIONAL", "PRICE_FIRST"),
        ("TRIGGERED", "OPPOSING_BBO_IOC"): ("CONDITIONAL", "FILL_FIRST"),
    }
    try:
        activation, capture = combinations[(plan.execution_mode, plan.order_tactic)]
    except KeyError:
        raise ValueError("entry_execution_combination_not_supported") from None
    return EntryExecutionProjection(activation, capture, plan.max_wait_sec)


@dataclass(frozen=True)
class EntryExecutionFacts:
    """Ephemeral owner facts, not a persisted lifecycle or a Risk disposition.

    authority_error includes current Plan expiry, hash/runtime authority failure
    and policy revocation. market_wait_reason comes from the current market gate.
    Neither validity nor freshness is inferred from absent evidence.
    """
    plan_status: str
    authority_error: str | None
    activation_current: bool
    market_wait_reason: str | None
    risk_approved: bool | None
    risk_reason: str | None
    preparation_claim_held: bool
    proposal_status: str | None
    order_status: str | None
    order_age_sec: float | None
    cancellation_unresolved: bool


@dataclass(frozen=True)
class EntryExecutionDirective:
    action: Literal["WAIT", "COMMAND", "STOP", "RECONCILE", "DONE"]
    reason: str
    command: Literal["SUBMIT_PASSIVE", "CANCEL_PASSIVE", "SUBMIT_IOC"] | None = None


def resolve_entry_execution(
    plan: EntryPlan, facts: EntryExecutionFacts,
) -> EntryExecutionDirective:
    """Pure existing-behavior projection. No clock, repository, network or writes."""
    projection = normalize_entry_execution(plan)
    # Durable external evidence wins over new activation, market or Risk evidence.
    if facts.cancellation_unresolved or facts.order_status in {
        "pending", "submission_unknown", "materialization_missing",
    } or (facts.order_status is None and facts.proposal_status in {
        "executing", "submitted", "submission_unknown",
    }):
        return EntryExecutionDirective("RECONCILE", "external_outcome_unresolved")
    if facts.order_status == "filled" or facts.plan_status == "filled":
        return EntryExecutionDirective("DONE", "entry_filled")
    terminal = facts.authority_error or (
        facts.plan_status if facts.plan_status in {"expired", "invalidated", "cancelled", "failed"} else None
    )
    if terminal:
        return EntryExecutionDirective("STOP", terminal)
    if facts.order_status in {"submitted", "partial", "partially_filled"}:
        if (plan.order_tactic == "POST_ONLY_LIMIT"
            and facts.order_age_sec is not None
            and projection.max_wait_sec > 0
            and facts.order_age_sec >= projection.max_wait_sec):
            return EntryExecutionDirective("COMMAND", "maker_order_unfilled_until_expiry", "CANCEL_PASSIVE")
        return EntryExecutionDirective("WAIT", "existing_order_owned_by_reconciliation")
    if facts.order_status in {"cancelled", "submission_rejected", "rejected", "error", "expired"}:
        # Known terminal Order never authorizes another leg under current contracts.
        return EntryExecutionDirective("DONE", "existing_order_terminal")
    if facts.order_status is not None:
        return EntryExecutionDirective("RECONCILE", "unclassified_order_evidence")
    if facts.proposal_status in {"rejected", "error", "cancelled"}:
        return EntryExecutionDirective("STOP", "proposal_terminal")
    if facts.preparation_claim_held:
        return EntryExecutionDirective("WAIT", "preparation_owned")
    if projection.activation == "CONDITIONAL" and not facts.activation_current:
        return EntryExecutionDirective("WAIT", "entry_trigger_activation_required")
    if facts.market_wait_reason:
        return EntryExecutionDirective("WAIT", facts.market_wait_reason)
    if facts.risk_approved is False:
        return EntryExecutionDirective("STOP", facts.risk_reason or "risk_rejected")
    if facts.risk_approved is None:
        return EntryExecutionDirective("WAIT", "risk_evaluation_required")
    command = "SUBMIT_PASSIVE" if plan.order_tactic == "POST_ONLY_LIMIT" else "SUBMIT_IOC"
    return EntryExecutionDirective("COMMAND", "current_entry_eligible", command)
