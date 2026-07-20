"""Execution Gateway — all orders flow through here. No direct exchange access."""

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from aletheia.config.loader import is_preview_mode, load_config
from aletheia.risk.engine import ProposalIntegrityError, evaluate_proposal
from aletheia.execution.errors import PostSubmitDBError, SubmissionUnknownError
from aletheia.execution.paper_broker import execute_paper_order
from aletheia.execution.post_commit_audit import (
    write_post_commit_audit as _write_best_effort_post_commit_audit,
)
from aletheia.trading.entry_snapshot import (
    capture_attribution_snapshot as capture_paper_attribution_snapshot,
)
from aletheia.storage.models import TradeProposal
from aletheia.storage.repositories import (
    get_authoritative_risk_decision,
    get_proposal_execution_claim,
    get_order,
    get_trade_proposal,
    has_order_for_proposal,
    insert_trade_proposal,
    insert_proposal_execution_claim,
    proposal_execution_claim_owned,
    release_entry_risk_reservation,
    release_proposal_execution_claim,
    replace_proposal_execution_claim,
    transaction,
    update_position,
    update_trade_proposal,
)
from aletheia.files.jsonl import write_audit

logger = logging.getLogger("aletheia.execution_gateway")

_EXECUTION_LEASE_SECONDS = 600


class PreviewExecutionBlockedError(RuntimeError):
    """Raised before an isolated preview can create or execute an order."""


def _reject_preview_execution() -> None:
    if is_preview_mode():
        raise PreviewExecutionBlockedError("execution is blocked in preview mode")


def _execution_claim_times() -> tuple[str, str, datetime]:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=_EXECUTION_LEASE_SECONDS)
    return now.isoformat(), expires.isoformat(), now


def _execution_claim_is_fresh(claim: dict, now: datetime) -> bool:
    try:
        expires = datetime.fromisoformat(
            str(claim.get("lease_expires_at")).replace("Z", "+00:00")
        )
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return expires > now
    except (TypeError, ValueError):
        return False


def _write_post_commit_audit(
    event: dict,
    *,
    proposal_id: str,
    persisted_status: str,
) -> None:
    """Best-effort audit after authoritative execution state has committed.

    The database remains the source of truth once an execution result or a
    retry-suppressing unresolved state is durable. A secondary JSONL sink
    failure must not escape into a caller that would rewrite that truth to
    ``error``. Exception text is intentionally excluded because file-system or
    library errors can contain sensitive paths or request material.
    """
    if not _write_best_effort_post_commit_audit(event, sink=write_audit):
        logger.warning(
            "Proposal %s kept persisted execution status %s after audit sink failure",
            proposal_id,
            persisted_status,
        )


def _reject_for_exchange_constraints(proposal: TradeProposal, reason: str) -> dict:
    """Persist a fail-closed executable-venue rule rejection without an order."""
    update_trade_proposal(proposal.id, status="rejected")
    write_audit({
        "actor": "system", "source": "execution_gateway",
        "event_type": "proposal_rejected", "entity_type": "trade_proposal",
        "entity_id": proposal.id, "before": {"status": proposal.status},
        "after": {"status": "rejected"}, "message": reason,
    })
    return {"status": "rejected", "proposal_id": proposal.id, "message": reason}


def _prepare_exchange_quantity(
    execution_mode: str, symbol: str, market_type: str, quantity: float, price: float,
) -> tuple[float, str | None]:
    """Apply executable-venue minima before Risk Engine sees an exchange order.

    Local paper fills intentionally have no exchange-side size constraints.
    """
    if execution_mode == "paper_internal":
        return quantity, None
    from aletheia.execution.order_constraints import (
        ConstraintUnavailable, prepare_exchange_quantity,
    )
    try:
        from aletheia.data.symbols import to_canonical
        return prepare_exchange_quantity(
            execution_mode, to_canonical(symbol), market_type, quantity, price,
        ), None
    except ConstraintUnavailable as exc:
        logger.warning("Exchange order constraints unavailable for %s/%s: %s", execution_mode, symbol, exc)
        return quantity, "Executable venue order rules are unavailable or do not support this order. No order was sent."


def submit_proposal(
    symbol: str,
    market_type: str,
    direction: str,
    order_type: str = "market",
    quantity: float = 0.0,
    price: float = 0.0,
    leverage: int | None = None,
    stop_price: float | None = None,
    take_profit: float | None = None,
    risk_reward: float | None = None,
    execution_mode: str = "paper_internal",
    strategy_id: str | None = None,
    opportunity_id: str | None = None,
    agent_rationale: str | None = None,
    trade_school: str = "short_term",
    expected_horizon_hours: float | None = None,
    stop_source: str | None = None,
) -> dict:
    """Submit a trade proposal through the full execution pipeline.

    Flow: Proposal -> Risk Engine -> Permission -> Live State -> Execution -> Fill -> Audit
    """
    _reject_preview_execution()
    # Private-fill materialization can happen after the broker call returns.
    # Capture the read-only decision-time attribution for every execution mode
    # so that delayed exchange reconciliation does not invent later context or
    # silently lose the same metadata already preserved for local paper.
    attribution_snapshot = _capture_attribution_snapshot_json(symbol)

    proposal = TradeProposal(
        strategy_id=strategy_id,
        opportunity_id=opportunity_id,
        symbol=symbol,
        market_type=market_type,
        direction=direction,
        order_type=order_type,
        quantity=quantity,
        price=price,
        leverage=leverage,
        stop_price=stop_price,
        take_profit=take_profit,
        risk_reward=risk_reward,
        execution_mode=execution_mode,
        agent_rationale=agent_rationale,
        attribution_snapshot=attribution_snapshot,
        trade_school=trade_school,
        expected_horizon_hours=expected_horizon_hours,
        stop_source=stop_source,
    )
    insert_trade_proposal(proposal)
    return execute_existing_proposal(proposal.id, execution_mode)


def execute_existing_proposal(proposal_id: str, execution_mode: str = "paper_internal") -> dict:
    """Atomically claim and execute one stored proposal without rewriting intent.

    The Proposal's execution mode is part of its hash-covered immutable intent.
    A caller may confirm that mode, but may not use this entry point to change it.
    The short ``BEGIN IMMEDIATE`` transaction ends before Risk or any broker I/O.
    """
    _reject_preview_execution()
    owner_token: str | None = None
    reconciliation_result: dict | None = None
    reconciliation_transition: tuple[str, str, str] | None = None

    with transaction(immediate=True):
        row = get_trade_proposal(proposal_id)
        if row is None:
            return {
                "status": "not_found",
                "proposal_id": proposal_id,
                "message": "Proposal not found",
            }

        proposal = TradeProposal.model_validate(dict(row))
        if execution_mode != proposal.execution_mode:
            return {
                "status": "rejected",
                "reason_code": "execution_mode_mismatch",
                "proposal_id": proposal.id,
                "message": (
                    "Requested execution mode does not match the stored Proposal; "
                    "no order was created."
                ),
            }
        if proposal.status == "executed":
            return {
                "status": "already_executed",
                "proposal_id": proposal.id,
                "message": "Proposal has already been executed; no new order was created.",
            }
        if proposal.status in {
            "error",
            "rejected",
            "submitted",
            "partially_filled",
            "submission_unknown",
            "materialization_missing",
        }:
            return {
                "status": proposal.status,
                "proposal_id": proposal.id,
                "message": (
                    f"Proposal is already marked as {proposal.status}; "
                    "no new order was created."
                ),
            }
        if proposal.status not in {"pending", "approved", "executing"}:
            return {
                "status": proposal.status,
                "proposal_id": proposal.id,
                "message": (
                    f"Proposal has unsupported execution state {proposal.status}; "
                    "no order was created."
                ),
            }

        claimed_at, lease_expires_at, now = _execution_claim_times()
        claim = get_proposal_execution_claim(proposal.id)
        if claim is not None and _execution_claim_is_fresh(claim, now):
            return {
                "status": "already_claimed",
                "proposal_id": proposal.id,
                "message": "Proposal execution is already in progress; no new order was created.",
            }

        if proposal.status == "executing" and (
            proposal.execution_mode != "paper_internal"
            or has_order_for_proposal(proposal.id)
        ):
            # An expired external claim may have crossed the exchange boundary;
            # never infer that no order was sent. A paper claim with an Order is
            # likewise not safe to replay because local materialization exists.
            stored_status = (
                "submission_unknown"
                if proposal.execution_mode != "paper_internal"
                else "materialization_missing"
            )
            reason_code = (
                "execution_lease_expired_exchange_reconciliation_required"
                if proposal.execution_mode != "paper_internal"
                else "paper_execution_order_exists_reconciliation_required"
            )
            update_trade_proposal(proposal.id, status=stored_status)
            if claim is not None:
                release_proposal_execution_claim(
                    proposal.id, str(claim.get("owner_token") or "")
                )
            reconciliation_result = {
                "status": "reconciliation_required",
                "proposal_status": stored_status,
                "reason_code": reason_code,
                "proposal_id": proposal.id,
                "message": (
                    "The previous execution owner expired after execution may have begun; "
                    "automatic resubmission is blocked."
                ),
            }
            reconciliation_transition = (
                proposal.status,
                stored_status,
                reason_code,
            )
        else:
            owner_token = uuid4().hex
            if claim is None:
                acquired = insert_proposal_execution_claim(
                    proposal.id,
                    owner_token,
                    claimed_at,
                    lease_expires_at,
                )
            else:
                acquired = replace_proposal_execution_claim(
                    proposal.id,
                    expected_owner_token=str(claim.get("owner_token") or ""),
                    expected_lease_expires_at=str(claim.get("lease_expires_at") or ""),
                    owner_token=owner_token,
                    claimed_at=claimed_at,
                    lease_expires_at=lease_expires_at,
                )
            if not acquired:
                return {
                    "status": "already_claimed",
                    "proposal_id": proposal.id,
                    "message": (
                        "Proposal execution was claimed concurrently; no new order was created."
                    ),
                }
            if proposal.status in {"pending", "approved"}:
                update_trade_proposal(proposal.id, status="executing")
            proposal.status = "executing"

    if reconciliation_result is not None:
        before_status, after_status, reason_code = reconciliation_transition
        _write_post_commit_audit(
            {
                "actor": "system",
                "source": "execution_gateway",
                "event_type": "proposal_execution_reconciliation_required",
                "entity_type": "trade_proposal",
                "entity_id": proposal_id,
                "reason": reason_code,
                "before": {"status": before_status},
                "after": {"status": after_status},
                "message": "Expired execution lease; automatic resubmission blocked.",
            },
            proposal_id=proposal_id,
            persisted_status=after_status,
        )
        return reconciliation_result

    if owner_token is None:
        raise RuntimeError(f"proposal execution claim missing after acquire: {proposal_id}")

    try:
        try:
            return _execute_proposal(
                proposal, execution_claim_token=owner_token
            )
        except ProposalIntegrityError as exc:
            return _terminal_execution_error(
                proposal,
                reason_code="proposal_integrity_failed",
                safe_message="Stored Proposal integrity verification failed; no order was created.",
                exception=exc,
            )
        except Exception as exc:
            return _terminal_execution_error(
                proposal,
                reason_code="execution_pipeline_failed",
                safe_message="Execution failed before a terminal result was recorded; no retry was sent.",
                exception=exc,
            )
    finally:
        try:
            with transaction(immediate=True):
                stored = get_trade_proposal(proposal.id)
                stored_status = stored.get("status") if stored else None
                # Once an Order exists, that durable row replaces the pre-Order
                # Risk allowance in every working set. Known no-Order terminal
                # states release it too. Ambiguous states deliberately retain the
                # reservation so a crash cannot reopen capacity.
                if has_order_for_proposal(proposal.id) or stored_status in {
                    "rejected",
                    "error",
                    "executed",
                }:
                    release_entry_risk_reservation(proposal.id)
                release_proposal_execution_claim(proposal.id, owner_token)
        except Exception as release_error:
            logger.warning(
                "Proposal %s execution claim release failed (%s)",
                proposal.id,
                type(release_error).__name__,
            )


# ---------------------------------------------------------------------------
# Symbol availability pre-check — covers all real exchange execution modes.
# paper_internal is excluded (virtual fills, exchange symbol list irrelevant).
#
# OKX demo:  uses OKX broker's sandbox-CCXT frozenset (curated limited set).
# All others: query universe_registry DB (loaded once per venue, TTL 4h).
#             Fail-open when the registry is empty/unsynced.
#
# To add a new exchange (e.g. Hyperliquid):
#   1. Add  "hyperliquid_demo": "hyperliquid"  to _EXECUTION_MODE_TO_VENUE.
#   2. Ensure sync_universe_registry() populates venue="hyperliquid".
# ---------------------------------------------------------------------------

_EXECUTION_MODE_TO_VENUE: dict[str, str] = {
    "okx_live_limited": "okx",
    "bitget_demo": "bitget",
    "bitget_live_limited": "bitget",
    "binance_testnet": "binance",
    "binance_live_limited": "binance",
}

_registry_symbol_cache: dict[str, tuple[dict, float]] = {}
_registry_cache_ttl = 14400  # 4h — same cadence as OKX broker cache
_registry_cache_lock = threading.Lock()


def _load_registry_symbols(venue: str) -> dict | None:
    """Return cached {market_type: frozenset} of canonical symbols for a venue.

    Returns None on cache miss + DB failure (fail-open).
    """
    with _registry_cache_lock:
        cached = _registry_symbol_cache.get(venue)
        if cached is not None and (time.time() - cached[1]) < _registry_cache_ttl:
            return cached[0]

    try:
        from aletheia.storage.repositories import list_universe_assets
        # This cache is execution authority, not a paginated UI feed. A bounded
        # page would turn every valid symbol after the cutoff into a false
        # "unavailable" rejection.
        assets = list_universe_assets(venue=venue, active_only=True, limit=None)
        if not assets:
            return None  # registry not yet synced — fail-open
        by_type: dict[str, set] = {}
        for row in assets:
            mtype = row.get("market_type") or "usdt_futures"
            by_type.setdefault(mtype, set()).add(row["symbol"])
        result = {k: frozenset(v) for k, v in by_type.items()}
        with _registry_cache_lock:
            _registry_symbol_cache[venue] = (result, time.time())
        total = sum(len(v) for v in result.values())
        logger.debug("Symbol cache loaded: %d symbols (%s) for venue=%s",
                     total, list(result.keys()), venue)
        return result
    except Exception as exc:
        logger.warning("Failed to load registry symbols for venue=%s: %s", venue, exc)
        return None


def _check_symbol_availability(symbol: str, execution_mode: str, market_type: str = "usdt_futures") -> str | None:
    """Return a rejection reason if the symbol is not available on the target exchange.

    Returns None when available, or when availability is unknown (fail-open).
    paper_internal is always None — it does not route to a real exchange.
    The check is market-type-aware for all exchange modes: a symbol available
    only as spot will not pass a futures/perp order, and vice versa.
    """
    if execution_mode == "paper_internal":
        return None

    from aletheia.data.symbols import to_canonical
    canonical = to_canonical(symbol)

    # OKX demo uses a dedicated CCXT sandbox market load (subset of live OKX).
    if execution_mode == "okx_demo":
        from aletheia.execution.okx_broker import _get_demo_symbols
        supported = _get_demo_symbols(market_type)
        if supported is not None and canonical not in supported:
            return f"{symbol} not available on OKX demo ({market_type})"
        return None

    # All other real-exchange modes: check universe_registry, market-type-aware.
    venue = _EXECUTION_MODE_TO_VENUE.get(execution_mode)
    if venue is None:
        return None  # unknown/future mode — fail-open

    registry = _load_registry_symbols(venue)
    if registry is None:
        return None  # registry not yet synced — fail-open
    supported = registry.get(market_type)
    if supported is not None and canonical not in supported:
        return f"{symbol} ({market_type}) not available on {execution_mode}"
    return None


def _reject_for_risk(proposal: TradeProposal, risk_result) -> dict:
    update_trade_proposal(proposal.id, status="rejected")
    write_audit({
        "actor": "system",
        "source": "execution_gateway",
        "event_type": "proposal_rejected",
        "entity_type": "trade_proposal",
        "entity_id": proposal.id,
        "before": {"status": proposal.status},
        "after": {"status": "rejected"},
        "message": risk_result.message,
    })
    return {
        "status": "rejected",
        "proposal_id": proposal.id,
        "risk_violations": getattr(risk_result, "violations", []),
        "message": risk_result.message,
    }

def _stamp_exit_intent(result: dict, proposal: TradeProposal) -> None:
    """Stamp the Operator's exit intent (expected_horizon_hours + stop provenance) from
    the proposal onto the just-opened position (exit-intent plan M2). No-op when the
    order rested unfilled (no position_id yet) or the proposal carries no intent —
    mirrors the existing take_profit post-open stamp pattern in this function.
    """
    pid = result.get("position_id") if isinstance(result, dict) else None
    if not pid:
        return
    updates: dict = {}
    if getattr(proposal, "expected_horizon_hours", None) is not None:
        updates["expected_horizon_hours"] = proposal.expected_horizon_hours
    if getattr(proposal, "stop_source", None):
        updates["stop_source"] = proposal.stop_source
    if updates:
        updates["exit_plan_version"] = 1
        update_position(pid, **updates)

def _exchange_order_matches_proposal(
    proposal: TradeProposal,
    order: dict,
    result: dict,
) -> bool:
    """Return whether one durable native Order is this accepted handoff."""
    if order.get("proposal_id") != proposal.id:
        return False
    if order.get("execution_mode") != proposal.execution_mode:
        return False
    if order.get("client_order_id") != proposal.id:
        return False
    for field in ("symbol", "market_type", "direction", "order_type"):
        if order.get(field) != getattr(proposal, field):
            return False
    result_exchange_id = str(result.get("exchange_order_id") or "").strip()
    stored_exchange_id = str(order.get("exchange_order_id") or "").strip()
    return not result_exchange_id or result_exchange_id == stored_exchange_id


def _exchange_fill_position_chain_is_durable(
    proposal: TradeProposal,
    order: dict,
    result: dict,
) -> bool:
    """Require one non-empty, identity-linked Fill and Position for executed."""
    fill_id = str(result.get("fill_id") or "").strip()
    position_id = str(result.get("position_id") or "").strip()
    order_id = str(order.get("id") or "").strip()
    if not fill_id or not position_id or not order_id:
        return False

    from aletheia.storage.sqlite import get_db

    db = get_db()
    fills = db.execute(
        "SELECT id, order_id, fill_quantity FROM fills WHERE order_id=?",
        (order_id,),
    ).fetchall()
    positions = db.execute(
        "SELECT id, proposal_id, order_id, quantity FROM positions "
        "WHERE order_id=? AND proposal_id=?",
        (order_id, proposal.id),
    ).fetchall()
    if len(fills) != 1 or len(positions) != 1:
        return False

    fill = dict(fills[0])
    position = dict(positions[0])
    try:
        quantities_are_non_empty = (
            float(fill.get("fill_quantity") or 0.0) > 0.0
            and float(position.get("quantity") or 0.0) > 0.0
        )
    except (TypeError, ValueError):
        return False
    return (
        quantities_are_non_empty
        and fill.get("id") == fill_id
        and fill.get("order_id") == order_id
        and position.get("id") == position_id
        and position.get("proposal_id") == proposal.id
        and position.get("order_id") == order_id
        and order.get("position_id") == position_id
    )


def _exchange_result_state(proposal: TradeProposal, result: dict) -> str:
    """Map durable broker facts to an honest, retry-suppressing Proposal state."""
    order_id = str(result.get("order_id") or "").strip()
    if not order_id:
        return "submission_unknown"
    order = get_order(order_id)
    if order is None or not _exchange_order_matches_proposal(proposal, order, result):
        return "submission_unknown"

    order_status = str(order.get("status") or "").lower()
    if order_status == "filled":
        if _exchange_fill_position_chain_is_durable(proposal, order, result):
            return "executed"
        return "materialization_missing"
    if order_status in {"partial", "partially_filled"}:
        return "partially_filled"
    if order_status in {"submission_unknown", "materialization_missing"}:
        return order_status
    if order_status == "submitted":
        return "submitted"
    # pending/error/unknown cannot disprove that a returned broker handoff
    # crossed the exchange boundary. Reconciliation must precede any retry.
    return "submission_unknown"


def _post_acceptance_unresolved_status(
    result: dict,
    *,
    known_status: str | None = None,
) -> str:
    """Choose the conservative retry-suppressing state when finalization fails."""
    result_status = str(result.get("order_status") or "").lower()
    if (
        known_status in {"executed", "partially_filled", "materialization_missing"}
        or result_status in {
            "filled",
            "partial",
            "partially_filled",
            "materialization_missing",
        }
        or result.get("fill_id")
        or result.get("position_id")
    ):
        return "materialization_missing"
    return "submission_unknown"


def _exchange_result_response(
    proposal: TradeProposal,
    result: dict,
    proposal_status: str,
    *,
    post_acceptance_failure: bool = False,
) -> dict:
    response = {
        **result,
        "status": proposal_status,
        "proposal_id": proposal.id,
    }
    if proposal_status in {"submission_unknown", "materialization_missing"}:
        response["reason_code"] = (
            "exchange_post_acceptance_local_finalization_failed"
            if post_acceptance_failure
            else (
                "exchange_fill_local_materialization_missing"
                if proposal_status == "materialization_missing"
                else "exchange_submission_acknowledgement_unknown"
            )
        )
        response["message"] = (
            "The exchange handoff may have been accepted, but local finalization "
            "is unresolved; automatic resubmission is blocked."
        )
    return response


def _recover_exchange_result_after_post_acceptance_failure(
    proposal: TradeProposal,
    execution_proposal: TradeProposal,
    result: dict,
    *,
    channel: str,
    failure: BaseException,
    retry_durable_read: bool,
    known_status: str | None = None,
) -> dict:
    """Contain every local failure after a broker returned an accepted result."""
    logger.warning(
        "Proposal %s exchange post-acceptance finalization failed; recovery "
        "required (%s)",
        proposal.id,
        type(failure).__name__,
    )
    proposal_status: str | None = None
    if retry_durable_read:
        try:
            proposal_status = _exchange_result_state(proposal, result)
        except Exception as evidence_error:
            logger.warning(
                "Proposal %s durable exchange evidence reread failed (%s)",
                proposal.id,
                type(evidence_error).__name__,
            )
    if proposal_status is None:
        proposal_status = _post_acceptance_unresolved_status(
            result,
            known_status=known_status,
        )

    status_persisted = False
    try:
        update_trade_proposal(proposal.id, status=proposal_status)
        status_persisted = True
    except Exception as persist_error:
        logger.warning(
            "Proposal %s post-acceptance recovery status write failed (%s)",
            proposal.id,
            type(persist_error).__name__,
        )
        unresolved_status = _post_acceptance_unresolved_status(
            result,
            known_status=proposal_status,
        )
        proposal_status = unresolved_status
        try:
            update_trade_proposal(proposal.id, status=unresolved_status)
            status_persisted = True
        except Exception as retry_error:
            logger.warning(
                "Proposal %s retry-suppressing recovery status remains pending (%s)",
                proposal.id,
                type(retry_error).__name__,
            )

    _write_post_commit_audit(
        {
            "actor": "system",
            "source": "execution_gateway",
            "event_type": "exchange_post_acceptance_recovered",
            "entity_type": "order",
            "entity_id": result.get("order_id"),
            "reason": "exchange_post_acceptance_local_finalization_failed",
            "before": {"status": proposal.status},
            "after": {
                "status": proposal_status,
                "proposal_status_persisted": status_persisted,
            },
            "message": (
                f"{channel.capitalize()} exchange handoff for "
                f"{execution_proposal.symbol} retained as "
                f"{proposal_status}; automatic resubmission blocked when unresolved"
            ),
        },
        proposal_id=proposal.id,
        persisted_status=proposal_status,
    )
    return _exchange_result_response(
        proposal,
        result,
        proposal_status,
        post_acceptance_failure=proposal_status
        in {"submission_unknown", "materialization_missing"},
    )


def _retry_suppressing_order_state(order_status: str) -> str | None:
    normalized = str(order_status or "").lower()
    if normalized in {"filled", "materialization_missing"}:
        # A broker exception before return carries no exact Fill/Position IDs
        # back to the Gateway. Even a filled Order therefore still requires
        # local materialization/reconciliation before it can be called executed.
        return "materialization_missing"
    if normalized in {"partial", "partially_filled"}:
        return "partially_filled"
    if normalized in {"pending", "submission_unknown"}:
        return "submission_unknown"
    if normalized == "submitted":
        return "submitted"
    return None


def _durable_retry_suppressing_exchange_order(
    proposal: TradeProposal,
) -> tuple[dict, str] | None:
    """Find accepted-or-possibly-accepted native evidence after broker failure."""
    from aletheia.storage.sqlite import get_db

    rows = get_db().execute(
        "SELECT * FROM orders WHERE proposal_id=? AND execution_mode=? "
        "AND source='native' AND status IN "
        "('pending','submitted','partial','partially_filled',"
        "'submission_unknown','materialization_missing','filled') "
        "ORDER BY rowid ASC",
        (proposal.id, proposal.execution_mode),
    ).fetchall()
    candidates: list[tuple[int, dict, str]] = []
    priority = {
        "submitted": 1,
        "submission_unknown": 2,
        "partially_filled": 3,
        "materialization_missing": 4,
    }
    for raw_row in rows:
        order = dict(raw_row)
        result_identity = {
            "order_id": order.get("id"),
            "exchange_order_id": order.get("exchange_order_id"),
        }
        if not _exchange_order_matches_proposal(
            proposal, order, result_identity
        ):
            continue
        recovered_status = _retry_suppressing_order_state(order.get("status"))
        if recovered_status is None:
            continue
        candidates.append(
            (priority[recovered_status], order, recovered_status)
        )
    if not candidates:
        return None
    _, order, recovered_status = max(candidates, key=lambda item: item[0])
    return order, recovered_status


def _recover_broker_exception_from_durable_order(
    proposal: TradeProposal,
    error: BaseException,
    *,
    channel: str,
) -> dict | None:
    """Recover when a broker crossed acceptance but failed before returning."""
    recovered: tuple[dict, str] | None = None
    evidence_read_succeeded = False
    for _attempt in range(2):
        try:
            recovered = _durable_retry_suppressing_exchange_order(proposal)
            evidence_read_succeeded = True
            break
        except Exception as evidence_error:
            logger.warning(
                "Proposal %s durable broker-failure evidence read failed (%s)",
                proposal.id,
                type(evidence_error).__name__,
            )
    if not evidence_read_succeeded:
        # Unavailable evidence is not evidence of absence. A broker exception
        # plus two failed local reads cannot prove that submission never began,
        # so fail closed and suppress retry even if this status write is also
        # temporarily unavailable. Production's durable execution lease keeps
        # an ``executing`` exchange Proposal from re-entering the broker.
        proposal_status = "submission_unknown"
        status_persisted = False
        for _attempt in range(2):
            try:
                update_trade_proposal(proposal.id, status=proposal_status)
                status_persisted = True
                break
            except Exception as persist_error:
                logger.warning(
                    "Proposal %s evidence-unavailable status write failed (%s)",
                    proposal.id,
                    type(persist_error).__name__,
                )
        logger.warning(
            "Proposal %s broker outcome evidence unavailable; automatic "
            "resubmission blocked (%s)",
            proposal.id,
            type(error).__name__,
        )
        _write_post_commit_audit(
            {
                "actor": "system",
                "source": "execution_gateway",
                "event_type": "broker_outcome_evidence_unavailable",
                "entity_type": "trade_proposal",
                "entity_id": proposal.id,
                "reason": "exchange_handoff_evidence_unavailable",
                "before": {"status": proposal.status},
                "after": {
                    "status": proposal_status,
                    "proposal_status_persisted": status_persisted,
                },
                "message": (
                    "Broker outcome evidence could not be read; automatic "
                    "resubmission blocked pending reconciliation"
                ),
            },
            proposal_id=proposal.id,
            persisted_status=proposal_status,
        )
        return {
            "status": proposal_status,
            "reason_code": "exchange_handoff_evidence_unavailable",
            "proposal_id": proposal.id,
            "proposal_status_persisted": status_persisted,
            "message": (
                "The broker outcome cannot be read safely; automatic "
                "resubmission is blocked pending reconciliation."
            ),
        }
    if recovered is None:
        return None

    order, proposal_status = recovered
    logger.warning(
        "Proposal %s broker failed after durable exchange handoff; retained as "
        "%s (%s)",
        proposal.id,
        proposal_status,
        type(error).__name__,
    )
    status_persisted = False
    try:
        update_trade_proposal(proposal.id, status=proposal_status)
        status_persisted = True
    except Exception as persist_error:
        logger.warning(
            "Proposal %s durable broker-failure status write failed (%s)",
            proposal.id,
            type(persist_error).__name__,
        )
        proposal_status = _post_acceptance_unresolved_status(
            {
                "order_status": order.get("status"),
                "position_id": order.get("position_id"),
            },
            known_status=proposal_status,
        )
        try:
            update_trade_proposal(proposal.id, status=proposal_status)
            status_persisted = True
        except Exception as retry_error:
            logger.warning(
                "Proposal %s broker-failure recovery status remains pending (%s)",
                proposal.id,
                type(retry_error).__name__,
            )

    result = {
        "order_id": order.get("id"),
        "exchange_order_id": order.get("exchange_order_id"),
        "order_status": order.get("status"),
        "fill_id": None,
        "position_id": order.get("position_id"),
    }
    _write_post_commit_audit(
        {
            "actor": "system",
            "source": "execution_gateway",
            "event_type": "broker_failure_after_durable_exchange_handoff",
            "entity_type": "order",
            "entity_id": order.get("id"),
            "reason": "exchange_handoff_recovered_from_durable_order",
            "before": {"status": proposal.status},
            "after": {
                "status": proposal_status,
                "proposal_status_persisted": status_persisted,
            },
            "message": (
                f"{channel.capitalize()} broker failure occurred after a durable "
                f"exchange handoff; retained as {proposal_status}"
            ),
        },
        proposal_id=proposal.id,
        persisted_status=proposal_status,
    )
    response = _exchange_result_response(
        proposal,
        result,
        proposal_status,
        post_acceptance_failure=True,
    )
    response["reason_code"] = "exchange_handoff_recovered_from_durable_order"
    response["message"] = (
        "A durable exchange handoff exists after broker finalization failed; "
        "automatic resubmission is blocked."
    )
    return response


def _record_exchange_result(
    proposal: TradeProposal,
    execution_proposal: TradeProposal,
    result: dict,
    *,
    channel: str,
) -> dict:
    try:
        proposal_status = _exchange_result_state(proposal, result)
    except Exception as exc:
        return _recover_exchange_result_after_post_acceptance_failure(
            proposal,
            execution_proposal,
            result,
            channel=channel,
            failure=exc,
            retry_durable_read=True,
        )
    try:
        update_trade_proposal(proposal.id, status=proposal_status)
    except Exception as exc:
        return _recover_exchange_result_after_post_acceptance_failure(
            proposal,
            execution_proposal,
            result,
            channel=channel,
            failure=exc,
            retry_durable_read=False,
            known_status=proposal_status,
        )
    exchange_order_id = result.get("exchange_order_id")
    _write_post_commit_audit(
        {
            "actor": "system",
            "source": "execution_gateway",
            "event_type": f"{channel}_order_{proposal_status}",
            "entity_type": "order",
            "entity_id": result.get("order_id"),
            "before": {"status": proposal.status},
            "after": {"status": proposal_status},
            "message": (
                f"{channel.capitalize()} order {proposal_status}: "
                f"{execution_proposal.symbol} {execution_proposal.direction} "
                f"{execution_proposal.quantity or 0.0} "
                f"(exchange_order_id={exchange_order_id or 'unavailable'})"
            ),
        },
        proposal_id=proposal.id,
        persisted_status=proposal_status,
    )
    return _exchange_result_response(proposal, result, proposal_status)


def _terminal_execution_error(
    proposal: TradeProposal,
    *,
    reason_code: str,
    safe_message: str,
    exception: BaseException | None = None,
) -> dict:
    """Persist a terminal, secret-free gateway failure before returning it."""
    if exception is not None:
        # Exception strings from config/exchange libraries can contain secrets.
        # Retain only the exception class in process logs; the durable audit is a
        # stable reason code and intentionally contains no raw exception text.
        logger.warning(
            "Proposal %s stopped with %s (%s)",
            proposal.id,
            reason_code,
            type(exception).__name__,
        )
    with transaction():
        update_trade_proposal(proposal.id, status="error")
    write_audit({
        "actor": "system",
        "source": "execution_gateway",
        "event_type": "execution_error",
        "entity_type": "trade_proposal",
        "entity_id": proposal.id,
        "before": {"status": proposal.status},
        "after": {"status": "error"},
        "message": f"{reason_code}: {safe_message}",
    })
    return {
        "status": "error",
        "reason_code": reason_code,
        "proposal_id": proposal.id,
        "message": safe_message,
    }


def _record_unresolved_execution(
    proposal: TradeProposal,
    error: PostSubmitDBError | SubmissionUnknownError,
) -> dict:
    """Persist an accepted-or-possibly-accepted exchange handoff without lying."""
    if isinstance(error, PostSubmitDBError):
        status = "materialization_missing"
        reason_code = "exchange_fill_local_materialization_missing"
        safe_message = (
            "The exchange reported an order result, but the local Fill/Position "
            "chain is incomplete; automatic resubmission is blocked."
        )
    else:
        status = "submission_unknown"
        reason_code = "exchange_submission_acknowledgement_unknown"
        safe_message = (
            "Exchange acceptance could not be confirmed after a transport failure; "
            "automatic resubmission is blocked."
        )
    exchange_order_id = getattr(error, "exchange_order_id", None)
    status_persisted = False
    try:
        update_trade_proposal(proposal.id, status=status)
        status_persisted = True
    except Exception as persist_error:
        logger.warning(
            "Proposal %s unresolved exchange status write failed (%s)",
            proposal.id,
            type(persist_error).__name__,
        )
        try:
            update_trade_proposal(proposal.id, status=status)
            status_persisted = True
        except Exception as retry_error:
            logger.warning(
                "Proposal %s unresolved exchange status remains pending (%s)",
                proposal.id,
                type(retry_error).__name__,
            )
    logger.warning(
        "Proposal %s remains %s (%s)",
        proposal.id,
        status,
        type(error).__name__,
    )
    _write_post_commit_audit(
        {
            "actor": "system",
            "source": "execution_gateway",
            "event_type": "execution_unresolved",
            "entity_type": "trade_proposal",
            "entity_id": proposal.id,
            "reason": reason_code,
            "before": {"status": proposal.status},
            "after": {
                "status": status,
                "exchange_order_id": exchange_order_id,
                "proposal_status_persisted": status_persisted,
            },
            "message": (
                f"{reason_code}; automatic resubmission blocked"
                + (
                    f" (exchange_order_id={exchange_order_id})"
                    if exchange_order_id
                    else ""
                )
            ),
        },
        proposal_id=proposal.id,
        persisted_status=status,
    )
    return {
        "status": status,
        "reason_code": reason_code,
        "proposal_id": proposal.id,
        "message": safe_message,
        "exchange_order_id": exchange_order_id,
    }


def _notify_committed_paper_fill(result: dict) -> None:
    """Publish a paper fill only after its complete Gateway transaction commits."""
    payload = result.pop("_notification_payload", None)
    if not isinstance(payload, dict):
        return
    try:
        from aletheia.notifications import notify_trade_fill

        notify_trade_fill(**payload)
    except Exception:
        # Notifications remain best-effort and cannot change committed trading
        # state or turn a successful execution into an API error.
        pass


def _execute_proposal(
    proposal: TradeProposal,
    *,
    execution_claim_token: str | None = None,
) -> dict:
    """Run risk and execution for an already-created proposal."""
    try:
        cfg = load_config()

        if proposal.execution_mode == "paper_internal":
            # A durable authoritative row proves the immutable Proposal snapshot,
            # but it cannot freeze dynamic gates such as kill switch, capacity,
            # account loss, or active lessons across a worker crash. Paper has no
            # venue normalization. The first authoritative evaluation is already
            # fresh; only a replay of an existing authority needs an additional
            # dispatch preflight. This preserves one RiskDecision on the normal
            # paper path while still preventing stale approval after a crash.
            existing_authority = get_authoritative_risk_decision(
                proposal.id,
                proposal.commit_hash,
                proposal.quantity,
            )
            if existing_authority is not None:
                preflight_result = evaluate_proposal(
                    proposal,
                    decision_role="preflight",
                )
                if not preflight_result.approved:
                    return _reject_for_risk(proposal, preflight_result)
            risk_result = evaluate_proposal(
                proposal, decision_role="authoritative"
            )
            execution_proposal = proposal
        else:
            # Exchange-bound orders retain a fresh preflight hard gate before
            # venue metadata or any other external preparation. That decision is
            # never passed to a broker.
            preflight_result = evaluate_proposal(
                proposal, decision_role="preflight"
            )
            if not preflight_result.approved:
                return _reject_for_risk(proposal, preflight_result)

            preflight_mode = preflight_result.allowed_execution_mode
            adjusted_quantity, constraint_error = _prepare_exchange_quantity(
                preflight_mode,
                proposal.symbol,
                proposal.market_type,
                proposal.quantity or 0.0,
                proposal.price or 0.0,
            )
            if constraint_error:
                return _reject_for_exchange_constraints(proposal, constraint_error)

            # quantity is hash-covered Proposal intent. Venue normalization
            # creates an immutable evaluated snapshot; it never rewrites storage
            # or mutates the caller's Proposal object. Every other causal field
            # is preserved, including opportunity_id=proposal.opportunity_id and
            # leverage=proposal.leverage.
            execution_proposal = proposal.model_copy(
                update={"quantity": adjusted_quantity}
            )
            risk_result = evaluate_proposal(
                execution_proposal, decision_role="authoritative"
            )

        if not risk_result.approved:
            return _reject_for_risk(proposal, risk_result)

        # The broker consumes only the authoritative decision and its exact
        # evaluated snapshot.
        allowed_mode = risk_result.allowed_execution_mode

        # Symbol availability is the last pre-order check. If it raises, the
        # Proposal must still reach a durable terminal state with no Order.
        _unavail = _check_symbol_availability(
            execution_proposal.symbol,
            allowed_mode,
            execution_proposal.market_type,
        )
        if _unavail:
            update_trade_proposal(proposal.id, status="rejected")
            logger.info(
                "Proposal %s skipped — symbol not on exchange: %s",
                proposal.id,
                _unavail,
            )
            write_audit({
                "actor": "system", "source": "execution_gateway",
                "event_type": "proposal_skipped", "entity_type": "trade_proposal",
                "entity_id": proposal.id,
                "before": {"status": proposal.status},
                "after": {"status": "rejected"},
                "message": _unavail,
            })
            return {
                "status": "rejected",
                "proposal_id": proposal.id,
                "message": _unavail,
            }
    except ProposalIntegrityError as exc:
        return _terminal_execution_error(
            proposal,
            reason_code="proposal_integrity_failed",
            safe_message="Stored Proposal integrity verification failed; no order was created.",
            exception=exc,
        )
    except Exception as exc:
        return _terminal_execution_error(
            proposal,
            reason_code="execution_precheck_failed",
            safe_message="Execution precheck failed; no order was created.",
            exception=exc,
        )

    accepted_exchange_result: dict | None = None
    accepted_exchange_channel: str | None = None
    try:
        if allowed_mode == "paper_internal":
            attribution_snapshot = _ensure_paper_attribution_snapshot(execution_proposal)
            with transaction(immediate=True):
                if execution_claim_token and not proposal_execution_claim_owned(
                    proposal.id, execution_claim_token
                ):
                    return {
                        "status": "already_claimed",
                        "proposal_id": proposal.id,
                        "message": (
                            "A newer execution owner reclaimed this Proposal; "
                            "no paper order was created."
                        ),
                    }
                result = execute_paper_order(
                    proposal_id=proposal.id,
                    risk_decision_id=risk_result.decision_id,
                    symbol=execution_proposal.symbol,
                    market_type=execution_proposal.market_type,
                    direction=execution_proposal.direction,
                    order_type=execution_proposal.order_type,
                    quantity=execution_proposal.quantity or 0.0,
                    price=execution_proposal.price or 0.0,
                    execution_mode=allowed_mode,
                    strategy_id=execution_proposal.strategy_id,
                    stop_price=execution_proposal.stop_price,
                    take_profit=execution_proposal.take_profit,
                    leverage=execution_proposal.leverage,
                    opportunity_id=execution_proposal.opportunity_id,
                    trade_school=execution_proposal.trade_school,
                    attribution_snapshot=attribution_snapshot,
                )
                _stamp_exit_intent(result, proposal)
                update_trade_proposal(proposal.id, status="executed")
            _notify_committed_paper_fill(result)
            _write_post_commit_audit(
                {
                    "actor": "system",
                    "source": "execution_gateway",
                    "event_type": "paper_order_executed",
                    "entity_type": "order",
                    "entity_id": result["order_id"],
                    "before": {"status": proposal.status},
                    "after": {"status": "executed"},
                    "message": (
                        f"Paper order filled: {execution_proposal.symbol} "
                        f"{execution_proposal.direction} "
                        f"{execution_proposal.quantity or 0.0}@"
                        f"{execution_proposal.price or 0.0}"
                    ),
                },
                proposal_id=proposal.id,
                persisted_status="executed",
            )
            return {"status": "executed", "proposal_id": proposal.id, **result}

        elif allowed_mode in ("bitget_demo", "binance_testnet", "okx_demo"):
            # Real exchange demo/testnet order — exchange-side record, no live guard
            # (live_disabled gates real funds only; demo/testnet is risk-free). On any
            # failure we mark the proposal error and audit; we NEVER fall back to a
            # fabricated local fill (honesty: a trade either hit the exchange or didn't).
            # execution_proposal is a quantity-only copy, so its broker argument
            # remains semantically trade_school=proposal.trade_school.
            try:
                if allowed_mode == "binance_testnet":
                    from aletheia.execution.binance_broker import execute_live_order
                elif allowed_mode == "okx_demo":
                    from aletheia.execution.okx_broker import execute_live_order
                else:
                    from aletheia.execution.bitget_broker import execute_live_order
                _entry = execution_proposal.price or 0.0
                result = execute_live_order(
                    proposal_id=proposal.id,
                    risk_decision_id=risk_result.decision_id,
                    symbol=execution_proposal.symbol,
                    market_type=execution_proposal.market_type,
                    direction=execution_proposal.direction,
                    order_type=execution_proposal.order_type,
                    quantity=execution_proposal.quantity or 0.0,
                    price=_entry,
                    strategy_id=execution_proposal.strategy_id,
                    execution_mode=allowed_mode,
                    stop_price=execution_proposal.stop_price,
                    take_profit_price=(
                        execution_proposal.take_profit
                        if allowed_mode == "bitget_demo"
                        else None                    ),
                    leverage=execution_proposal.leverage,
                    opportunity_id=execution_proposal.opportunity_id,
                    trade_school=execution_proposal.trade_school,
                )
            except (PostSubmitDBError, SubmissionUnknownError) as e:
                return _record_unresolved_execution(proposal, e)
            except Exception as e:
                recovered = _recover_broker_exception_from_durable_order(
                    proposal,
                    e,
                    channel="demo",
                )
                if recovered is not None:
                    return recovered
                exchange_oid = getattr(e, "exchange_order_id", None)
                with transaction():
                    update_trade_proposal(proposal.id, status="error")
                msg = f"demo_order_failed: broker execution failed ({type(e).__name__})"
                if exchange_oid:
                    msg += f" (exchange_order_id={exchange_oid})"
                write_audit({
                    "actor": "system", "source": "execution_gateway",
                    "event_type": "demo_order_error", "entity_type": "trade_proposal",
                    "entity_id": proposal.id, "before": {"status": proposal.status},
                    "after": {"status": "error"}, "message": msg,
                })
                # #8 fix: don't leak raw exception text to the API — some CCXT
                # errors carry request URLs / partial headers. Route through
                # clean_error_message (already used by broker error paths) and
                # cap length so the API response stays a summary.
                return {
                    "status": "error",
                    "proposal_id": proposal.id,
                    "message": "Broker rejected or failed the order before acceptance was confirmed.",
                    "exchange_order_id": exchange_oid,
                }
            accepted_exchange_result = result
            accepted_exchange_channel = "demo"
            return _record_exchange_result(
                proposal,
                execution_proposal,
                result,
                channel="demo",
            )

        elif allowed_mode in ("bitget_live_limited", "okx_live_limited", "binance_live_limited"):
            # Defense in depth: risk engine already checked live_disabled, but re-check here.
            # The quantity-only execution snapshot likewise preserves
            # trade_school=proposal.trade_school on this live dispatch path.
            if cfg.live.state == "live_disabled":
                update_trade_proposal(proposal.id, status="rejected")
                write_audit({
                    "actor": "system",
                    "source": "execution_gateway",
                    "event_type": "proposal_rejected",
                    "entity_type": "trade_proposal",
                    "entity_id": proposal.id,
                    "before": {"status": proposal.status},
                    "after": {"status": "rejected"},
                    "message": "Live trading is disabled",
                })
                return {"status": "rejected", "proposal_id": proposal.id, "message": "Live trading is disabled"}
            try:
                if allowed_mode == "okx_live_limited":
                    from aletheia.execution.okx_broker import execute_live_order
                elif allowed_mode == "binance_live_limited":
                    from aletheia.execution.binance_broker import execute_live_order
                else:
                    from aletheia.execution.bitget_broker import execute_live_order
                _entry2 = execution_proposal.price or 0.0
                result = execute_live_order(
                    proposal_id=proposal.id,
                    risk_decision_id=risk_result.decision_id,
                    symbol=execution_proposal.symbol,
                    market_type=execution_proposal.market_type,
                    direction=execution_proposal.direction,
                    order_type=execution_proposal.order_type,
                    quantity=execution_proposal.quantity or 0.0,
                    price=_entry2,
                    strategy_id=execution_proposal.strategy_id,
                    execution_mode=allowed_mode,
                    stop_price=execution_proposal.stop_price,
                    take_profit_price=(
                        execution_proposal.take_profit
                        if allowed_mode == "bitget_live_limited"
                        else None                    ),
                    leverage=execution_proposal.leverage,
                    opportunity_id=execution_proposal.opportunity_id,
                    trade_school=execution_proposal.trade_school,
                )
            except (PostSubmitDBError, SubmissionUnknownError) as e:
                return _record_unresolved_execution(proposal, e)
            except Exception as e:
                recovered = _recover_broker_exception_from_durable_order(
                    proposal,
                    e,
                    channel="live",
                )
                if recovered is not None:
                    return recovered
                exchange_oid = getattr(e, "exchange_order_id", None)
                with transaction():
                    update_trade_proposal(proposal.id, status="error")
                msg = f"live_order_failed: broker execution failed ({type(e).__name__})"
                if exchange_oid:
                    msg += f" (exchange_order_id={exchange_oid})"
                write_audit({
                    "actor": "system", "source": "execution_gateway",
                    "event_type": "live_order_error", "entity_type": "trade_proposal",
                    "entity_id": proposal.id, "before": {"status": proposal.status},
                    "after": {"status": "error"}, "message": msg,
                })
                # #8 fix: don't leak raw exception text to the API — some CCXT
                # errors carry request URLs / partial headers. Route through
                # clean_error_message (already used by broker error paths) and
                # cap length so the API response stays a summary.
                return {
                    "status": "error",
                    "proposal_id": proposal.id,
                    "message": "Broker rejected or failed the order before acceptance was confirmed.",
                    "exchange_order_id": exchange_oid,
                }
            accepted_exchange_result = result
            accepted_exchange_channel = "live"
            return _record_exchange_result(
                proposal,
                execution_proposal,
                result,
                channel="live",
            )

    except Exception as e:
        if accepted_exchange_result is not None:
            return _recover_exchange_result_after_post_acceptance_failure(
                proposal,
                execution_proposal,
                accepted_exchange_result,
                channel=accepted_exchange_channel or "exchange",
                failure=e,
                retry_durable_read=True,
            )
        recovered = _recover_broker_exception_from_durable_order(
            proposal,
            e,
            channel=accepted_exchange_channel or "exchange",
        )
        if recovered is not None:
            return recovered
        with transaction():
            update_trade_proposal(proposal.id, status="error")
        write_audit({
            "actor": "system", "source": "execution_gateway",
            "event_type": "execution_error", "entity_type": "trade_proposal",
            "entity_id": proposal.id, "before": {"status": proposal.status},
            "after": {"status": "error"},
            "message": (
                "execution_pipeline_failed: broker execution failed "
                f"({type(e).__name__})"
            ),
        })
        # #8 fix: same treatment as the inner exception branches — never dump
        # raw exception text to the API response.
        return {
            "status": "error",
            "proposal_id": proposal.id,
            "message": "Execution failed; no automatic resubmission was attempted.",
        }

    return {"status": "unknown_mode", "proposal_id": proposal.id}


def _ensure_paper_attribution_snapshot(proposal: TradeProposal) -> dict | None:
    # New paper Proposals capture this before insert. A legacy stored Proposal
    # with no snapshot must remain honestly missing: backfilling at execution
    # time would mutate hash-covered intent and pretend late context existed at
    # decision time.
    return _loads_attribution_snapshot(proposal.attribution_snapshot)


def _capture_attribution_snapshot_json(symbol: str) -> str | None:
    try:
        snapshot = capture_paper_attribution_snapshot(symbol)
        return json.dumps(snapshot, default=str)
    except Exception:
        return None


def _loads_attribution_snapshot(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None
