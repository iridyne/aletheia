"""Execution Gateway — all orders flow through here. No direct exchange access."""

import json
import logging
import threading
import time

from aletheia.config.loader import load_config
from aletheia.risk.engine import evaluate_proposal
from aletheia.execution.paper_broker import (
    capture_paper_attribution_snapshot,
    execute_paper_order,
)
from aletheia.storage.models import TradeProposal
from aletheia.storage.repositories import (
    get_trade_proposal,
    insert_trade_proposal,
    transaction,
    update_trade_proposal,
    update_position,
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
    attribution_snapshot = None
    if execution_mode == "paper_internal":
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
    """Execute a stored proposal without creating a duplicate proposal row."""
    row = get_trade_proposal(proposal_id)
    if row is None:
        return {"status": "not_found", "proposal_id": proposal_id, "message": "Proposal not found"}

    data = dict(row)
    data["execution_mode"] = execution_mode
    proposal = TradeProposal.model_validate(data)
    if proposal.status == "executed":
        return {
            "status": "already_executed",
            "proposal_id": proposal.id,
            "message": "Proposal has already been executed; no new order was created.",
        }
    if proposal.status in {"error", "rejected"}:
        return {
            "status": proposal.status,
            "proposal_id": proposal.id,
            "message": f"Proposal is already marked as {proposal.status}.",
        }
    update_trade_proposal(proposal.id, execution_mode=execution_mode)
    return _execute_proposal(proposal)


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


def _execute_proposal(proposal: TradeProposal) -> dict:
    """Run risk and execution for an already-created proposal."""
    cfg = load_config()

    # Risk check
    risk_result = evaluate_proposal(proposal)

    if not risk_result.approved:
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
            "risk_violations": risk_result.violations,
            "message": risk_result.message,
        }

    # Execute based on allowed mode
    allowed_mode = risk_result.allowed_execution_mode

    # The first Risk Engine pass preserves all existing hard gates (especially
    # live-disabled) before any external call.  Once it has admitted an
    # exchange-bound proposal, obtain authoritative venue rules, normalize the
    # size upward, and evaluate that *final* size again before it can be sent.
    adjusted_quantity, constraint_error = _prepare_exchange_quantity(
        allowed_mode, proposal.symbol, proposal.market_type,
        proposal.quantity or 0.0, proposal.price or 0.0,
    )
    if constraint_error:
        return _reject_for_exchange_constraints(proposal, constraint_error)
    if adjusted_quantity != proposal.quantity:
        proposal.quantity = adjusted_quantity
        update_trade_proposal(proposal.id, quantity=adjusted_quantity)
        risk_result = evaluate_proposal(proposal)
        if not risk_result.approved:
            update_trade_proposal(proposal.id, status="rejected")
            write_audit({
                "actor": "system", "source": "execution_gateway",
                "event_type": "proposal_rejected", "entity_type": "trade_proposal",
                "entity_id": proposal.id, "before": {"status": proposal.status},
                "after": {"status": "rejected"}, "message": risk_result.message,
            })
            return {
                "status": "rejected", "proposal_id": proposal.id,
                "risk_violations": risk_result.violations, "message": risk_result.message,
            }

    # Symbol availability pre-check: if the exchange doesn't list this symbol,
    # reject gracefully here instead of letting CCXT throw an opaque error.
    _unavail = _check_symbol_availability(proposal.symbol, allowed_mode, proposal.market_type)
    if _unavail:
        update_trade_proposal(proposal.id, status="rejected")
        logger.info("Proposal %s skipped — symbol not on exchange: %s", proposal.id, _unavail)
        write_audit({
            "actor": "system", "source": "execution_gateway",
            "event_type": "proposal_skipped", "entity_type": "trade_proposal",
            "entity_id": proposal.id,
            "before": {"status": proposal.status},
            "after": {"status": "rejected"},
            "message": _unavail,
        })
        return {"status": "rejected", "proposal_id": proposal.id, "message": _unavail}

    try:
        if allowed_mode == "paper_internal":
            attribution_snapshot = _ensure_paper_attribution_snapshot(proposal)
            with transaction():
                result = execute_paper_order(
                    proposal_id=proposal.id,
                    risk_decision_id=risk_result.decision_id,
                    symbol=proposal.symbol,
                    market_type=proposal.market_type,
                    direction=proposal.direction,
                    order_type=proposal.order_type,
                    quantity=proposal.quantity or 0.0,
                    price=proposal.price or 0.0,
                    execution_mode=allowed_mode,
                    strategy_id=proposal.strategy_id,
                    stop_price=proposal.stop_price,
                    take_profit=proposal.take_profit,
                    leverage=proposal.leverage,
                    opportunity_id=proposal.opportunity_id,
                    trade_school=proposal.trade_school,
                    attribution_snapshot=attribution_snapshot,
                )
                update_trade_proposal(proposal.id, status="executed")
            write_audit({
                "actor": "system",
                "source": "execution_gateway",
                "event_type": "paper_order_executed",
                "entity_type": "order",
                "entity_id": result["order_id"],
                "before": {"status": proposal.status},
                "after": {"status": "executed"},
                "message": (
                    f"Paper order filled: {proposal.symbol} {proposal.direction} "
                    f"{proposal.quantity or 0.0}@{proposal.price or 0.0}"
                ),
            })
            return {"status": "executed", "proposal_id": proposal.id, **result}

        elif allowed_mode in ("bitget_demo", "binance_testnet", "okx_demo"):
            # Real exchange demo/testnet order — exchange-side record, no live guard
            # (live_disabled gates real funds only; demo/testnet is risk-free). On any
            # failure we mark the proposal error and audit; we NEVER fall back to a
            # fabricated local fill (honesty: a trade either hit the exchange or didn't).
            try:
                if allowed_mode == "binance_testnet":
                    from aletheia.execution.binance_broker import execute_live_order
                elif allowed_mode == "okx_demo":
                    from aletheia.execution.okx_broker import execute_live_order
                else:
                    from aletheia.execution.bitget_broker import execute_live_order
                _cfg = load_config()
                _tp_pct = _cfg.trading.default_take_profit_pct
                _entry = proposal.price or 0.0
                # TP is intentionally NOT pre-computed here — we anchor it to the
                # actual exchange fill_price returned below, not the snapshot price
                # in the proposal (which may be stale by the time the order fills).
                result = execute_live_order(
                    proposal_id=proposal.id,
                    risk_decision_id=risk_result.decision_id,
                    symbol=proposal.symbol,
                    market_type=proposal.market_type,
                    direction=proposal.direction,
                    order_type=proposal.order_type,
                    quantity=proposal.quantity or 0.0,
                    price=_entry,
                    strategy_id=proposal.strategy_id,
                    execution_mode=allowed_mode,
                    stop_price=proposal.stop_price,
                    take_profit_price=None,
                    leverage=proposal.leverage,
                    opportunity_id=proposal.opportunity_id,
                    trade_school=proposal.trade_school,
                )
                # TP source (2026-07-03): prefer the System Operator's explicit
                # take_profit from the proposal (an entry now commits to it up
                # front); fall back to the config-percentage anchored to the
                # confirmed fill price for legacy proposals that carry no TP.
                if result.get("position_id"):
                    _actual_fill = result.get("fill_price") or _entry
                    if proposal.take_profit:
                        _tp = proposal.take_profit
                    elif _tp_pct and _actual_fill > 0:
                        _tp = round(_actual_fill * (1 + _tp_pct / 100.0), 8) if proposal.direction in ("long", "buy") else round(_actual_fill * (1 - _tp_pct / 100.0), 8)
                    else:
                        _tp = None
                    if _tp:
                        update_position(result["position_id"], take_profit_price=_tp)
                update_trade_proposal(proposal.id, status="executed")
                write_audit({
                    "actor": "system", "source": "execution_gateway",
                    "event_type": "demo_order_executed", "entity_type": "order",
                    "entity_id": result["order_id"],
                    "before": {"status": proposal.status},
                    "after": {"status": "executed"},
                    "message": (
                        f"Demo order placed: {proposal.symbol} {proposal.direction} "
                        f"{proposal.quantity or 0.0}@{result.get('fill_price') or proposal.price or 0.0} "
                        f"(ex:{result['exchange_order_id']})"
                    ),
                })
                return {"status": "executed", "proposal_id": proposal.id, **result}
            except Exception as e:
                exchange_oid = getattr(e, "exchange_order_id", None)
                with transaction():
                    update_trade_proposal(proposal.id, status="error")
                msg = f"Demo order failed: {e}"
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
                return {"status": "error", "proposal_id": proposal.id,
                        "message": _safe_error_message(str(e)),
                        "exchange_order_id": exchange_oid}

        elif allowed_mode in ("bitget_live_limited", "okx_live_limited", "binance_live_limited"):
            # Defense in depth: risk engine already checked live_disabled, but re-check here.
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
                _cfg2 = load_config()
                _tp_pct2 = _cfg2.trading.default_take_profit_pct
                _entry2 = proposal.price or 0.0
                result = execute_live_order(
                    proposal_id=proposal.id,
                    risk_decision_id=risk_result.decision_id,
                    symbol=proposal.symbol,
                    market_type=proposal.market_type,
                    direction=proposal.direction,
                    order_type=proposal.order_type,
                    quantity=proposal.quantity or 0.0,
                    price=_entry2,
                    strategy_id=proposal.strategy_id,
                    execution_mode=allowed_mode,
                    stop_price=proposal.stop_price,
                    take_profit_price=None,
                    leverage=proposal.leverage,
                    opportunity_id=proposal.opportunity_id,
                    trade_school=proposal.trade_school,
                )
                # TP source (2026-07-03): prefer the System Operator's explicit
                # take_profit from the proposal; fall back to config-percentage
                # anchored to the confirmed fill price for legacy proposals.
                if result.get("position_id"):
                    _actual_fill2 = result.get("fill_price") or _entry2
                    if proposal.take_profit:
                        _tp2 = proposal.take_profit
                    elif _tp_pct2 and _actual_fill2 > 0:
                        _tp2 = round(_actual_fill2 * (1 + _tp_pct2 / 100.0), 8) if proposal.direction in ("long", "buy") else round(_actual_fill2 * (1 - _tp_pct2 / 100.0), 8)
                    else:
                        _tp2 = None
                    if _tp2:
                        update_position(result["position_id"], take_profit_price=_tp2)
                update_trade_proposal(proposal.id, status="executed")
                write_audit({
                    "actor": "system", "source": "execution_gateway",
                    "event_type": "live_order_executed", "entity_type": "order",
                    "entity_id": result["order_id"],
                    "before": {"status": proposal.status},
                    "after": {"status": "executed"},
                    "message": (
                        f"Live order placed: {proposal.symbol} {proposal.direction} "
                        f"{proposal.quantity or 0.0}@{_actual_fill2} "
                        f"(ex:{result['exchange_order_id']})"
                    ),
                })
                return {"status": "executed", "proposal_id": proposal.id, **result}

            except Exception as e:
                exchange_oid = getattr(e, "exchange_order_id", None)
                with transaction():
                    update_trade_proposal(proposal.id, status="error")
                msg = f"Live order failed: {e}"
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
                return {"status": "error", "proposal_id": proposal.id,
                        "message": _safe_error_message(str(e)),
                        "exchange_order_id": exchange_oid}

    except Exception as e:
        with transaction():
            update_trade_proposal(proposal.id, status="error")
        write_audit({
            "actor": "system", "source": "execution_gateway",
            "event_type": "execution_error", "entity_type": "trade_proposal",
            "entity_id": proposal.id, "before": {"status": proposal.status},
            "after": {"status": "error"}, "message": f"Execution failed: {e}",
        })
        # #8 fix: same treatment as the inner exception branches — never dump
        # raw exception text to the API response.
        return {"status": "error", "proposal_id": proposal.id, "message": _safe_error_message(str(e))}

    return {"status": "unknown_mode", "proposal_id": proposal.id}


def _safe_error_message(raw: str, max_len: int = 200) -> str:
    """Clean an exception string for an API-facing error payload.

    #8 fix: prevents raw CCXT / requests exception text from leaking through
    the /gateway response body. Delegates to the shared clean_error_message
    which already knows the exchange-specific JSON shapes, then caps at
    max_len so a stack-trace-flavored message doesn't paint the UI.
    """
    from aletheia.execution.broker_utils import clean_error_message
    cleaned = clean_error_message(raw or "")
    return (cleaned or "execution error")[:max_len]


def _ensure_paper_attribution_snapshot(proposal: TradeProposal) -> dict | None:
    snapshot = _loads_attribution_snapshot(proposal.attribution_snapshot)
    if snapshot:
        return snapshot

    encoded = _capture_attribution_snapshot_json(proposal.symbol)
    if not encoded:
        return None
    update_trade_proposal(proposal.id, attribution_snapshot=encoded)
    return _loads_attribution_snapshot(encoded)


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
