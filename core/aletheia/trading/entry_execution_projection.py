"""Pure bounded Entry capture resolution over snapshots from existing owners."""
import math
from dataclasses import dataclass
from typing import Literal

from aletheia.trading.entry_plan import EntryPlan


@dataclass(frozen=True)
class EntryExecutionFacts:
    plan_status: str
    authority_error: str | None = None
    activation_current: bool = False
    market_wait_reason: str | None = None
    risk_disposition: str | None = None
    risk_reason: str | None = None
    preparation_claim_held: bool = False
    proposal_status: str | None = None
    proposal_leg_index: int | None = None
    proposal_tactic: str | None = None
    proposal_has_order: bool = False
    order_status: str | None = None
    order_leg_index: int = 0
    order_tactic: str | None = None
    order_age_sec: float | None = None
    cancellation_unresolved: bool = False
    # None means materialization is incomplete, never zero fills.
    filled_quantity: float | None = None
    passive_eligible: bool | None = None
    ioc_eligible: bool | None = None


@dataclass(frozen=True)
class EntryExecutionDirective:
    action: Literal["WAIT", "COMMAND", "STOP", "RECONCILE", "DONE"]
    reason: str
    command: Literal["SUBMIT_PASSIVE", "CANCEL_PASSIVE", "SUBMIT_IOC"] | None = None
    leg_index: int | None = None
    quantity: float | None = None


def resolve_entry_execution(plan: EntryPlan, facts: EntryExecutionFacts) -> EntryExecutionDirective:
    """No clock, I/O or writes. Submission commands still require fresh Gateway Risk."""
    if (facts.cancellation_unresolved or facts.proposal_status == "materialization_missing"
        or facts.order_status in {"pending", "submission_unknown", "materialization_missing"}
        or (not facts.proposal_has_order and facts.proposal_status in {
            "executing", "submitted", "submission_unknown", "partially_filled", "executed",
        })):
        return EntryExecutionDirective("RECONCILE", "external_outcome_unresolved")
    if facts.filled_quantity is None or not math.isfinite(facts.filled_quantity) or facts.filled_quantity < 0:
        return EntryExecutionDirective("RECONCILE", "entry_fill_evidence_incomplete")
    working = facts.order_status in {"submitted", "partial", "partially_filled"}
    maker = facts.order_tactic == "POST_ONLY_LIMIT"
    remaining = max(0.0, plan.target_quantity - facts.filled_quantity)
    # Gateway rounds each leg down to the venue step (entry-plan-contract), so a
    # fully filled latest leg has captured every executable unit of the target.
    if remaining <= 1e-12 or facts.order_status == "filled":
        if working and maker:
            return EntryExecutionDirective("COMMAND", "entry_target_filled", "CANCEL_PASSIVE", facts.order_leg_index)
        return EntryExecutionDirective("DONE", "entry_target_filled")
    terminal = facts.authority_error or (
        facts.plan_status if facts.plan_status in {"expired", "invalidated", "cancelled", "failed", "filled"} else None)
    if terminal or plan.capture_priority is None:
        reason = terminal or "entry_capture_policy_required"
        if working and maker:
            return EntryExecutionDirective("COMMAND", reason, "CANCEL_PASSIVE", facts.order_leg_index)
        return EntryExecutionDirective("RECONCILE" if working else "STOP", reason)
    if working:
        if not maker:
            return EntryExecutionDirective("RECONCILE", "ioc_outcome_unresolved")
        if (plan.capture_priority == "MAKER_PREFERRED" and facts.order_age_sec is not None
            and facts.order_age_sec >= plan.max_wait_sec):
            return EntryExecutionDirective("COMMAND", "passive_window_elapsed", "CANCEL_PASSIVE", facts.order_leg_index)
        return EntryExecutionDirective("WAIT", "passive_order_working")
    leg = 0
    if facts.order_status is not None:
        if (facts.order_status == "cancelled" and maker and facts.order_leg_index == 0
            and plan.capture_priority == "MAKER_PREFERRED"):
            leg = 1
        elif facts.order_status in {"filled", "cancelled", "submission_rejected", "rejected", "expired"}:
            return EntryExecutionDirective("DONE", "entry_capture_terminal")
        else:
            return EntryExecutionDirective("RECONCILE", "unclassified_order_evidence")
    if facts.proposal_status in {"rejected", "error", "cancelled"} and facts.proposal_leg_index == leg:
        return EntryExecutionDirective("STOP", "proposal_terminal")
    if facts.preparation_claim_held:
        return EntryExecutionDirective("WAIT", "preparation_owned")
    if leg == 0 and plan.activation_policy == "CONDITIONAL" and not facts.activation_current:
        return EntryExecutionDirective("WAIT", "entry_trigger_activation_required")
    if facts.market_wait_reason:
        return EntryExecutionDirective("WAIT", facts.market_wait_reason)
    if facts.risk_disposition == "DEFER":
        return EntryExecutionDirective("WAIT", facts.risk_reason or "canonical_risk_deferred")
    if facts.risk_disposition not in {None, "APPROVED"}:
        return EntryExecutionDirective("STOP", facts.risk_reason or "canonical_risk_rejected")
    # Once a Proposal exists its tactic is immutable, even before Broker handoff.
    tactic = facts.proposal_tactic if facts.proposal_leg_index == leg else None
    passive = tactic == "POST_ONLY_LIMIT" if tactic else (leg == 0 and (
        plan.capture_priority == "PRICE_FIRST"
        or (plan.capture_priority == "MAKER_PREFERRED" and facts.passive_eligible is True)))
    if passive and facts.passive_eligible is not True:
        return EntryExecutionDirective("WAIT", "post_only_would_cross_bbo")
    if not passive and facts.ioc_eligible is not True:
        return EntryExecutionDirective("WAIT", "entry_ioc_outside_frozen_bounds")
    return EntryExecutionDirective("COMMAND", "current_entry_eligible",
        "SUBMIT_PASSIVE" if passive else "SUBMIT_IOC", leg, remaining)
