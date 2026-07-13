"""Execution Gateway — all orders flow through here. No direct exchange access."""

import json
import logging
import threading
import time

from aletheia.config.loader import load_config
from aletheia.risk.engine import ProposalIntegrityError, evaluate_proposal
from aletheia.execution.errors import PostSubmitDBError, SubmissionUnknownError
from aletheia.execution.paper_broker import (
    capture_paper_attribution_snapshot,
    execute_paper_order,
)
from aletheia.storage.models import TradeProposal
from aletheia.storage.repositories import (
    get_order,
    get_trade_proposal,
    insert_trade_proposal,
    transaction,
    update_trade_proposal,
)
from aletheia.files.jsonl import write_audit

logger = logging.getLogger("aletheia.execution_gateway")


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
) -> dict:
    """Submit a trade proposal through the full execution pipeline.

    Flow: Proposal -> Risk Engine -> Permission -> Live State -> Execution -> Fill -> Audit
    """
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
    )
    insert_trade_proposal(proposal)
    return _execute_proposal(proposal)


def execute_existing_proposal(proposal_id: str, execution_mode: str = "paper_internal") -> dict:
    """Atomically claim and execute one stored proposal without rewriting intent.

    The Proposal's execution mode is part of its hash-covered immutable intent.
    A caller may confirm that mode, but may not use this entry point to change it.
    The short ``BEGIN IMMEDIATE`` transaction ends before Risk or any broker I/O.
    """
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
        if proposal.status == "executing":
            return {
                "status": "already_claimed",
                "proposal_id": proposal.id,
                "message": "Proposal execution is already in progress; no new order was created.",
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
        update_trade_proposal(proposal.id, status="executing")

    try:
        return _execute_proposal(proposal)
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
        assets = list_universe_assets(venue=venue, active_only=True, limit=3000)
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


def _exchange_result_state(result: dict) -> str:
    """Map broker evidence to an honest, retry-suppressing Proposal state."""
    order = get_order(result.get("order_id")) if result.get("order_id") else None
    order_status = str(
        result.get("order_status") or (order or {}).get("status") or ""
    ).lower()
    if (
        order_status == "filled"
        and result.get("fill_id")
        and result.get("position_id")
    ):
        return "executed"
    if order_status in {"partial", "partially_filled"}:
        return "partially_filled"
    if order_status in {"submission_unknown", "materialization_missing"}:
        return order_status
    # An accepted exchange order without a durable Fill+Position remains
    # submitted. Order creation alone is not evidence that a trade occurred.
    return "submitted"


def _record_exchange_result(
    proposal: TradeProposal,
    execution_proposal: TradeProposal,
    result: dict,
    *,
    channel: str,
) -> dict:
    proposal_status = _exchange_result_state(result)
    update_trade_proposal(proposal.id, status=proposal_status)
    exchange_order_id = result.get("exchange_order_id")
    write_audit({
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
    })
    return {"status": proposal_status, "proposal_id": proposal.id, **result}


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
    update_trade_proposal(proposal.id, status=status)
    logger.warning(
        "Proposal %s remains %s (%s)",
        proposal.id,
        status,
        type(error).__name__,
    )
    write_audit({
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
        },
        "message": (
            f"{reason_code}; automatic resubmission blocked"
            + (
                f" (exchange_order_id={exchange_order_id})"
                if exchange_order_id
                else ""
            )
        ),
    })
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


def _execute_proposal(proposal: TradeProposal) -> dict:
    """Run risk and execution for an already-created proposal."""
    try:
        cfg = load_config()

        if proposal.execution_mode == "paper_internal":
            # Local paper has no executable-venue normalization. Its one Risk
            # result is therefore already the authoritative decision consumed
            # by the paper broker.
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

    try:
        if allowed_mode == "paper_internal":
            attribution_snapshot = _ensure_paper_attribution_snapshot(execution_proposal)
            with transaction():
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
                update_trade_proposal(proposal.id, status="executed")
            _notify_committed_paper_fill(result)
            write_audit({
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
            })
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
                        else None
                    ),
                    leverage=execution_proposal.leverage,
                    opportunity_id=execution_proposal.opportunity_id,
                    trade_school=execution_proposal.trade_school,
                )
                return _record_exchange_result(
                    proposal,
                    execution_proposal,
                    result,
                    channel="demo",
                )
            except (PostSubmitDBError, SubmissionUnknownError) as e:
                return _record_unresolved_execution(proposal, e)
            except Exception as e:
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
                        else None
                    ),
                    leverage=execution_proposal.leverage,
                    opportunity_id=execution_proposal.opportunity_id,
                    trade_school=execution_proposal.trade_school,
                )
                return _record_exchange_result(
                    proposal,
                    execution_proposal,
                    result,
                    channel="live",
                )

            except (PostSubmitDBError, SubmissionUnknownError) as e:
                return _record_unresolved_execution(proposal, e)
            except Exception as e:
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

    except Exception as e:
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
