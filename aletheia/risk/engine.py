"""Risk Engine — enforces hard limits. Agent cannot modify these.

Change-controlled module (guide §31.3). Implements the §21.3 check matrix.
"""

import json
import logging
import math
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

from aletheia.config.loader import load_config
from aletheia.data.symbols import to_canonical
from aletheia.portfolio.account_loss import authoritative_account_realized_loss
from aletheia.storage.models import RiskDecision, TradeProposal
from aletheia.storage.repositories import (
    _compute_proposal_hash,
    _RISK_DECISION_PREFIX,
    _RISK_PROPOSAL_PREFIX,
    _validate_native_entry_order_snapshot,
    acquire_entry_risk_reservation,
    get_authoritative_risk_decision,
    get_entry_risk_reservation,
    get_opportunity,
    get_serenity_opportunity,
    get_trade_proposal,
    has_order_for_proposal,
    insert_authoritative_risk_decision,
    insert_risk_decision,
    list_active_entry_risk_reservations_for_risk,
    list_all_open_positions,
    list_unresolved_native_entry_orders_for_risk,
    transaction,
    update_trade_proposal,
)
from aletheia.trading.entry_plan import (
    RISK_REWARD_OUTSIDE_ENVELOPE,
    calculate_entry_plan_risk_reward,
    entry_plan_risk_reward_is_plausible,
)

logger = logging.getLogger("aletheia.risk.engine")


class ProposalIntegrityError(RuntimeError):
    """Stored immutable Proposal content no longer matches its commit hash."""

# Live market data older than this (seconds) is considered stale for live orders.
_MAX_DATA_AGE_SECONDS = 120
# Demo orders get a looser freshness bound: market data is collected every 5 min, so
# the tight 120s live bound would reject most healthy demo trades. This catches only a
# genuinely dead feed (several missed collection cycles) — a cheap anti-foot-gun so
# autonomous demo trades aren't placed against a frozen price.
_MAX_DEMO_DATA_AGE_SECONDS = 1800

# Serialize proposal evaluation. The daily-loss check can auto-trip the kill switch
# mid-evaluation (writes config); without serialization a concurrently-evaluated
# proposal could read the pre-trip config and slip through (TOCTOU). APScheduler
# runs jobs on a threadpool, so this lock closes that window.
_eval_lock = threading.Lock()

_DECISION_ROLES = frozenset({"preflight", "authoritative"})
_NATIVE_MARKET_TYPES = frozenset({"spot", "usdt_futures"})
_NATIVE_DIRECTIONS = frozenset({"long", "short"})
_NATIVE_ORDER_TYPES = frozenset({"market", "limit"})
_NATIVE_EXECUTION_MODES = frozenset({
    "paper_internal",
    "bitget_demo",
    "bitget_live_limited",
    "binance_testnet",
    "binance_live_limited",
    "okx_demo",
    "okx_live_limited",
})


def _physical_market_type(value: str | None) -> str | None:
    raw = str(value or "").strip().lower()
    if raw == "spot":
        return "spot"
    if raw in {"usdt_futures", "perp", "swap"}:
        return "perp"
    if raw == "futures":
        return "futures"
    return None


def _paper_opportunity_identity_violations(proposal: TradeProposal) -> list[str]:
    """Resolve one unambiguous durable source and compare its exact trade identity."""
    opportunity_id = str(proposal.opportunity_id or "").strip()
    if not opportunity_id:
        return ["proposal_opportunity_required"]
    try:
        normal = get_opportunity(opportunity_id)
        serenity = get_serenity_opportunity(opportunity_id)
    except Exception as exc:
        logger.warning(
            "proposal opportunity identity unavailable id=%s (%s)",
            opportunity_id,
            type(exc).__name__,
        )
        return ["proposal_opportunity_check_error"]
    sources = [
        ("market_observer", normal) if normal is not None else None,
        ("serenity", serenity) if serenity is not None else None,
    ]
    resolved = [item for item in sources if item is not None]
    if not resolved:
        return ["proposal_opportunity_not_found"]
    if len(resolved) != 1:
        return ["proposal_opportunity_ambiguous"]
    source_kind, source = resolved[0]
    violations: list[str] = []
    try:
        source_symbol = to_canonical(str(source.get("symbol") or ""))
    except Exception:
        source_symbol = ""
    if source_symbol != to_canonical(proposal.symbol):
        violations.append("proposal_opportunity_symbol_mismatch")
    source_market = (
        source.get("market")
        if source_kind == "market_observer"
        else source.get("market_type")
    )
    if _physical_market_type(source_market) != _physical_market_type(proposal.market_type):
        violations.append("proposal_opportunity_market_mismatch")
    source_direction = (
        source.get("direction_bias")
        if source_kind == "market_observer"
        else source.get("direction")
    )
    if str(source_direction or "").strip().lower() != str(proposal.direction).strip().lower():
        violations.append("proposal_opportunity_direction_mismatch")
    return violations


def _prefixed_snapshot(row: dict, prefix: str, model_type) -> dict:
    """Rebuild one joined authority row without additional SQLite reads."""
    return {
        field: row.get(f"{prefix}{field}")
        for field in model_type.model_fields
    }


def _pre_order_reservation_has_exact_authority(row: dict) -> bool:
    """Return whether one reservation matches its hashed Proposal and decision."""
    try:
        stored = TradeProposal.model_validate(
            _prefixed_snapshot(row, _RISK_PROPOSAL_PREFIX, TradeProposal)
        )
        if (
            stored.source != "native"
            or stored.id != row.get("proposal_id")
            or not stored.commit_hash
            or _compute_proposal_hash(stored) != stored.commit_hash
            or row.get("proposal_commit_hash") != stored.commit_hash
            or row.get("symbol") != stored.symbol
            or row.get("market_type") != stored.market_type
            or row.get("execution_mode") != stored.execution_mode
            or float(row.get("price")) != float(stored.price)
            or float(row.get("leverage")) != float(stored.leverage or 1.0)
            or int(row.get("exact_authoritative_decision_count") or 0) != 1
        ):
            return False
        quantity = float(row.get("quantity"))
        return math.isfinite(quantity) and quantity > 0
    except (TypeError, ValueError):
        return False


@dataclass(frozen=True)
class _UnresolvedEntryReservation:
    """Conservative allowance consumed by one unresolved native entry Order."""

    order_id: str
    symbol: str
    market_type: str
    execution_mode: str
    opportunity_id: str | None
    price: float | None
    leverage: float | None
    order_quantity: float | None
    persisted_fill_quantity: float | None
    remaining_quantity: float | None
    reserved_quantity: float | None
    has_open_position: bool
    invalid_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class _PreOrderEntryReservation:
    """Durable Risk allowance acquired before any Order can exist."""

    proposal_id: str
    symbol: str
    market_type: str
    execution_mode: str
    opportunity_id: str | None
    price: float | None
    leverage: float | None
    reserved_quantity: float | None
    invalid_reasons: tuple[str, ...] = ()


def _is_live(execution_mode: str) -> bool:
    return execution_mode in ("bitget_live_limited", "okx_live_limited", "binance_live_limited")


def _unresolved_entry_reservations() -> list[_UnresolvedEntryReservation]:
    """Normalize unresolved entry authority without treating bad data as zero.

    A linked open Position already represents the persisted Fill quantity, so
    only the unfilled remainder is reserved.  If no Position exists, even
    durable Fills are not represented in portfolio exposure; reserve the full
    authoritative Order quantity until materialization converges.
    """
    reservations: list[_UnresolvedEntryReservation] = []
    for row in list_unresolved_native_entry_orders_for_risk():
        invalid: list[str] = []
        authority_valid = False
        proposal_snapshot: dict = {}

        try:
            proposal_snapshot = _prefixed_snapshot(
                row,
                _RISK_PROPOSAL_PREFIX,
                TradeProposal,
            )
            decision_snapshot = _prefixed_snapshot(
                row,
                _RISK_DECISION_PREFIX,
                RiskDecision,
            )
            _validate_native_entry_order_snapshot(
                row,
                proposal_snapshot,
                decision_snapshot,
            )
            authority_valid = True
        except Exception:
            # Existing rows can be altered outside the model insert boundary.
            # Their numeric values remain useful only for conservative accounting;
            # they cannot authorize another Proposal while the chain is broken.
            invalid.append("unresolved_entry_authority_invalid")

        market_type = str(row.get("market_type") or "")
        proposal_market_type = str(row.get("proposal_market_type") or "")
        if (
            market_type not in _NATIVE_MARKET_TYPES
            or proposal_market_type != market_type
            or not str(row.get("proposal_commit_hash") or "").strip()
        ):
            invalid.append("unresolved_entry_invalid_proposal_identity")
        try:
            leverage = float(row.get("proposal_leverage") or 1.0)
        except (TypeError, ValueError):
            leverage = None
        if leverage is None or not math.isfinite(leverage) or leverage <= 0:
            leverage = None
            invalid.append("unresolved_entry_invalid_leverage")

        try:
            order_quantity = float(row.get("quantity"))
        except (TypeError, ValueError):
            order_quantity = None
        if (
            order_quantity is None
            or not math.isfinite(order_quantity)
            or order_quantity <= 0
        ):
            order_quantity = None
            invalid.append("unresolved_entry_invalid_quantity")

        try:
            price = float(row.get("price"))
        except (TypeError, ValueError):
            price = None
        if price is None or not math.isfinite(price) or price <= 0:
            price = None
            invalid.append("unresolved_entry_invalid_price")
        elif authority_valid and row.get("execution_mode") == "paper_internal":
            # Local paper stores the slipped fill price on Order while the hashed
            # Proposal retains the approved plan price. Either can be higher; use
            # the conservative floor so altering the mutable Order cannot lower
            # the Risk allowance.
            approved_price = float(proposal_snapshot["price"])
            price = max(price, approved_price)

        try:
            persisted_fill_quantity = float(
                row.get("persisted_fill_quantity") or 0.0
            )
        except (TypeError, ValueError):
            persisted_fill_quantity = None
        invalid_fill_count = int(row.get("invalid_fill_quantity_count") or 0)
        fill_quantity_valid = (
            persisted_fill_quantity is not None
            and math.isfinite(persisted_fill_quantity)
            and persisted_fill_quantity >= 0
            and invalid_fill_count == 0
        )
        if fill_quantity_valid and order_quantity is not None:
            tolerance = max(1e-12, order_quantity * 1e-9)
            if persisted_fill_quantity > order_quantity + tolerance:
                fill_quantity_valid = False
        if not fill_quantity_valid:
            invalid.append("unresolved_entry_invalid_fill_quantity")

        try:
            linked_position_count = int(
                row.get("linked_open_position_count") or 0
            )
            matching_position_count = int(
                row.get("matching_open_position_count") or 0
            )
            represented_position_quantity = float(
                row.get("represented_position_quantity")
            )
            represented_entry_fill_quantity = float(
                row.get("represented_entry_fill_quantity") or 0.0
            )
            invalid_represented_fill_count = int(
                row.get("invalid_represented_entry_fill_count") or 0
            )
        except (TypeError, ValueError):
            represented_position_quantity = None
            represented_entry_fill_quantity = None
            invalid_represented_fill_count = 1
            linked_position_count = int(
                row.get("linked_open_position_count") or 0
            )
            matching_position_count = int(
                row.get("matching_open_position_count") or 0
            )
        representation_valid = False
        if (
            fill_quantity_valid
            and represented_position_quantity is not None
            and math.isfinite(represented_position_quantity)
            and represented_position_quantity > 0
            and represented_entry_fill_quantity is not None
            and math.isfinite(represented_entry_fill_quantity)
            and represented_entry_fill_quantity >= 0
            and invalid_represented_fill_count == 0
            and linked_position_count == 1
            and matching_position_count == 1
        ):
            fill_tolerance = max(
                1e-12,
                abs(represented_entry_fill_quantity) * 1e-9,
            )
            representation_valid = (
                represented_position_quantity + fill_tolerance
                >= represented_entry_fill_quantity
            )
        has_open_position = representation_valid
        if linked_position_count > 0 and not representation_valid:
            invalid.append("unresolved_entry_position_authority_invalid")
        remaining_quantity: float | None = None
        reserved_quantity: float | None = None
        if order_quantity is not None:
            if fill_quantity_valid:
                remaining_quantity = max(
                    0.0, order_quantity - float(persisted_fill_quantity)
                )
            # Invalid Fill aggregation is already fail-closed.  Retain the full
            # Order as the conservative numeric reservation where possible.
            reserved_quantity = (
                remaining_quantity
                if has_open_position and remaining_quantity is not None
                else order_quantity
            )

        reservations.append(_UnresolvedEntryReservation(
            order_id=str(row.get("id") or ""),
            symbol=str(row.get("symbol") or ""),
            market_type=market_type,
            execution_mode=str(row.get("execution_mode") or ""),
            opportunity_id=(
                str(row.get(f"{_RISK_PROPOSAL_PREFIX}opportunity_id"))
                if row.get(f"{_RISK_PROPOSAL_PREFIX}opportunity_id")
                else None
            ),
            price=price,
            leverage=leverage,
            order_quantity=order_quantity,
            persisted_fill_quantity=(
                persisted_fill_quantity if fill_quantity_valid else None
            ),
            remaining_quantity=remaining_quantity,
            reserved_quantity=reserved_quantity,
            has_open_position=has_open_position,
            invalid_reasons=tuple(dict.fromkeys(invalid)),
        ))
    return reservations


def _pre_order_entry_reservations(
    *,
    exclude_proposal_id: str | None = None,
) -> list[_PreOrderEntryReservation]:
    """Normalize every durable pre-Order allowance without inventing values."""
    reservations: list[_PreOrderEntryReservation] = []
    for row in list_active_entry_risk_reservations_for_risk(
        exclude_proposal_id=exclude_proposal_id,
    ):
        invalid: list[str] = []
        if not _pre_order_reservation_has_exact_authority(row):
            invalid.append("entry_risk_reservation_authority_invalid")
        try:
            quantity = float(row.get("quantity"))
        except (TypeError, ValueError):
            quantity = None
        if quantity is None or not math.isfinite(quantity) or quantity <= 0:
            quantity = None
            invalid.append("entry_risk_reservation_invalid_quantity")
        try:
            price = float(row.get("price"))
        except (TypeError, ValueError):
            price = None
        if price is None or not math.isfinite(price) or price <= 0:
            price = None
            invalid.append("entry_risk_reservation_invalid_price")
        symbol = str(row.get("symbol") or "")
        market_type = str(row.get("market_type") or "")
        execution_mode = str(row.get("execution_mode") or "")
        try:
            leverage = float(row.get("leverage"))
        except (TypeError, ValueError):
            leverage = None
        if not symbol:
            invalid.append("entry_risk_reservation_invalid_symbol")
        if market_type not in _NATIVE_MARKET_TYPES:
            invalid.append("entry_risk_reservation_invalid_market_type")
        if execution_mode not in _NATIVE_EXECUTION_MODES:
            invalid.append("entry_risk_reservation_invalid_execution_mode")
        if leverage is None or not math.isfinite(leverage) or leverage <= 0:
            leverage = None
            invalid.append("entry_risk_reservation_invalid_leverage")
        reservations.append(_PreOrderEntryReservation(
            proposal_id=str(row.get("proposal_id") or ""),
            symbol=symbol,
            market_type=market_type,
            execution_mode=execution_mode,
            opportunity_id=(
                str(row.get(f"{_RISK_PROPOSAL_PREFIX}opportunity_id"))
                if row.get(f"{_RISK_PROPOSAL_PREFIX}opportunity_id")
                else None
            ),
            price=price,
            leverage=leverage,
            reserved_quantity=quantity,
            invalid_reasons=tuple(invalid),
        ))
    return reservations


def _cached_available_balance_usdt(execution_mode: str) -> float | None:
    """Return the cached available USDT for the account used by an order.

    Risk evaluation must stay local and deterministic: it never calls an exchange
    for a balance. Bitget has a per-mode cache; the other exchange-backed modes
    use their named account in the unified cache. ``paper_internal`` has no
    external account; it uses an isolated local ledger instead.
    """
    if execution_mode == "paper_internal":
        try:
            from aletheia.portfolio.paper_account import get_paper_account
            available = float(
                get_paper_account(event_limit=0)["available_balance_usdt"]
            )
            if not math.isfinite(available):
                return None
            return max(0.0, available)
        except Exception:
            # Unlike an exchange cache, the local paper ledger is the account
            # authority itself.  Its absence cannot be replaced by config capital.
            return None

    try:
        from aletheia.portfolio.exchange_sync import (
            get_cached_state,
            get_unified_cached_state,
        )

        if execution_mode.startswith("bitget_"):
            state = get_cached_state("live" if _is_live(execution_mode) else "demo")
            value = (state or {}).get("available_usdt")
        else:
            unified = get_unified_cached_state() or {}
            account = next(
                (
                    item for item in (unified.get("accounts") or [])
                    if item.get("broker_id") == execution_mode
                ),
                None,
            )
            value = (account or {}).get("available_usdt")
        available = float(value)
        return max(0.0, available)
    except (TypeError, ValueError, AttributeError):
        return None
    except Exception:
        # Balance synchronization is advisory to this calculation. Preserve the
        # configured hard cap if no local snapshot can be read.
        return None


def _effective_position_notional_cap(
    risk,
    execution_mode: str,
) -> float | None:
    """Return the hard position cap tightened by the matching account balance."""
    configured_cap = (
        risk.max_live_position_notional_usdt
        if _is_live(execution_mode)
        else risk.max_demo_position_notional_usdt
    )
    available = _cached_available_balance_usdt(execution_mode)
    if available is None:
        if execution_mode == "paper_internal":
            return None
        return configured_cap
    return min(configured_cap, available)


def _market_data_age_seconds(symbol: str, market_type: str | None = None) -> float | None:
    """Age in seconds of the latest market snapshot for symbol, or None if absent.

    When ``market_type`` (proposal vocabulary, e.g. "usdt_futures") is given, a
    typed snapshot is preferred so a futures freshness check does not pass on a
    spot snapshot for the same symbol (#13). ``latest_snapshot`` falls back to
    the symbol-only lookup when no typed snapshot exists, so this stays fail-closed
    on genuinely absent data without introducing new stale rejections.
    """
    from aletheia.trading.market_data import latest_snapshot
    row = latest_snapshot(symbol, market_type=market_type)
    if not row:
        return None
    ts = row.get("snapshot_at")
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except ValueError:
        return None


@dataclass
class RiskCheckResult:
    approved: bool = False
    risk_score: float = 0.0
    violations: list[str] = field(default_factory=list)
    allowed_execution_mode: str = "paper_internal"
    message: str = ""
    decision_id: str | None = None


def evaluate_proposal(
    proposal: TradeProposal,
    *,
    decision_role: str = "authoritative",
) -> RiskCheckResult:
    """Run all risk checks against a trade proposal. Returns a decision.

    Serialized via _eval_lock so a kill-switch trip during one evaluation is
    visible to the next proposal (closes the daily-loss TOCTOU window).

    ``preflight`` always performs a fresh evaluation. ``authoritative`` is
    idempotent for the proposal's original commit hash plus the quantity actually
    evaluated, so an execution retry cannot create a second authority row.
    """
    if decision_role not in _DECISION_ROLES:
        raise ValueError(f"unsupported risk decision role: {decision_role}")

    with _eval_lock:
        if decision_role == "authoritative":
            # The working-set read, authoritative decision and pre-Order
            # reservation are one writer transaction. SQLite therefore
            # serializes distinct Proposals across threads and processes before
            # either can observe the same last free slot.
            with transaction(immediate=True):
                from aletheia.risk.policy import (
                    UnknownRiskExecutionMode,
                    resolve_risk_policy,
                )
                proposal_commit_hash = _verified_original_proposal_commit_hash(
                    proposal,
                    require_evaluated_snapshot_match=True,
                )
                try:
                    current_policy_hash = resolve_risk_policy(
                        load_config(), proposal.execution_mode
                    ).policy_hash
                except UnknownRiskExecutionMode:
                    current_policy_hash = "__unknown_risk_mode__"
                existing = get_authoritative_risk_decision(
                    proposal.id,
                    proposal_commit_hash,
                    proposal.quantity,
                    current_policy_hash,
                )
                if existing is not None:
                    result = _risk_result_from_row(existing)
                    # A pre-reservation authority from an older schema cannot
                    # silently regain capacity after upgrade. If migration could
                    # not prove and carry its allowance forward, re-run the
                    # current dynamic gates before recreating the reservation.
                    if (
                        result.approved
                        and proposal.source == "native"
                        and not has_order_for_proposal(proposal.id)
                        and get_entry_risk_reservation(proposal.id) is None
                    ):
                        refreshed = _evaluate_proposal_locked(
                            proposal,
                            decision_role="preflight",
                        )
                        if not refreshed.approved:
                            return refreshed
                else:
                    # Preserve the historical one-argument private call for the
                    # default role; lock-focused tests instrument this boundary.
                    result = _evaluate_proposal_locked(proposal)
                if not result.approved:
                    stored = get_trade_proposal(proposal.id)
                    if (
                        stored is not None
                        and str(stored.get("status") or "")
                        in {"pending", "approved"}
                    ):
                        # The authoritative verdict and terminal Proposal state
                        # must commit together, but only from states whose
                        # transition matrix permits rejection. Gateway emits the
                        # post-commit audit; execution-progress states must never
                        # be rewritten by an idempotent Risk retry.
                        update_trade_proposal(proposal.id, status="rejected")
                if result.approved:
                    stored = get_trade_proposal(proposal.id)
                    if (
                        stored is not None
                        and proposal.source == "native"
                        and str(stored.get("status") or "")
                        not in {"rejected", "error", "executed"}
                        and not has_order_for_proposal(proposal.id)
                    ):
                        if (
                            proposal_commit_hash is None
                            or proposal.quantity is None
                            or proposal.price is None
                        ):
                            raise ProposalIntegrityError(
                                "approved Proposal lacks durable reservation identity"
                            )
                        acquire_entry_risk_reservation(
                            proposal_id=proposal.id,
                            proposal_commit_hash=proposal_commit_hash,
                            symbol=proposal.symbol,
                            market_type=proposal.market_type,
                            execution_mode=proposal.execution_mode,
                            quantity=proposal.quantity,
                            price=proposal.price,
                            leverage=float(proposal.leverage or 1.0),
                        )
                return result
        return _evaluate_proposal_locked(proposal, decision_role=decision_role)


def _evaluate_proposal_locked(
    proposal: TradeProposal,
    decision_role: str = "authoritative",
) -> RiskCheckResult:
    native_input_violations = _native_input_violations(proposal)
    if native_input_violations:
        # Malformed native intent is not eligible for the configurable risk
        # formulas. Persist the fail-closed verdict without letting invalid enum
        # or numeric values reach account, lesson, or portfolio side effects.
        return _persist_risk_result(
            proposal,
            native_input_violations,
            decision_role=decision_role,
        )

    cfg = load_config()
    from aletheia.risk.policy import (
        UnknownRiskExecutionMode,
        resolve_risk_policy,
    )
    try:
        resolved_policy = resolve_risk_policy(cfg, proposal.execution_mode)
    except UnknownRiskExecutionMode:
        return _persist_risk_result(
            proposal,
            [f"unregistered_risk_execution_mode:{proposal.execution_mode}"],
            decision_role=decision_role,
        )
    risk = resolved_policy.legacy_view(cfg.risk)
    # Downstream helpers receive the same mode-scoped view. This copy is local to
    # the evaluation and never mutates the cached or persisted configuration.
    cfg.risk = risk
    violations = []

    # 0. Kill switch — global brake, rejects everything when engaged.
    if cfg.runtime.kill_switch_active:
        violations.append("kill_switch_active")
    mode_brake_active = (
        cfg.runtime.live_brake_active
        if resolved_policy.mode == "live"
        else cfg.runtime.demo_brake_active
    )
    if mode_brake_active:
        violations.append(f"{resolved_policy.mode}_brake_active")

    # 1. Symbol filter (whitelist or blacklist mode, single list)
    sym_canonical = to_canonical(proposal.symbol)
    filter_set = {to_canonical(s) for s in risk.symbol_list}
    if risk.symbol_filter_mode == "blacklist":
        if sym_canonical in filter_set:
            violations.append(f"symbol_blocked:{proposal.symbol}")
    else:
        if filter_set and sym_canonical not in filter_set:
            violations.append(f"symbol_not_allowed:{proposal.symbol}")

    # 1b. Local paper has no exchange submission that can discover or reject an
    # unusable price later.  The one authoritative Risk pass therefore requires
    # the same exact-instrument, fail-closed mark used by protective closes:
    # memory <=90s, or typed SQLite fallback <=120s.  Rechecking here prevents a
    # Proposal prepared against a once-fresh mark from executing after it expires.
    if (
        decision_role == "authoritative"
        and proposal.source == "native"
        and proposal.execution_mode == "paper_internal"
    ):
        from aletheia.data.price_feed import (
            get_fresh_price_for_protective_close,
            protective_price_venue,
        )

        venue = protective_price_venue(
            proposal.execution_mode,
            configured_data_venue=cfg.market_data.ccxt_exchange,
        )
        physical_market = (
            "perp" if proposal.market_type == "usdt_futures" else proposal.market_type
        )
        fresh_mark = None
        if venue is not None:
            try:
                raw_fresh_mark = get_fresh_price_for_protective_close(
                    sym_canonical,
                    proposal.market_type,
                    venue=venue,
                )
                if raw_fresh_mark is not None:
                    candidate_mark = float(raw_fresh_mark)
                    if math.isfinite(candidate_mark) and candidate_mark > 0:
                        fresh_mark = candidate_mark
            except Exception as exc:
                logger.warning(
                    "paper market authority unavailable for %s/%s/%s (%s)",
                    venue,
                    physical_market,
                    sym_canonical,
                    type(exc).__name__,
                )
        if fresh_mark is None:
            violations.append(
                f"paper_market_data_unavailable:{venue or 'unknown'}:{physical_market}"
            )
        elif not math.isclose(
            float(proposal.price),
            fresh_mark,
            rel_tol=1e-9,
            abs_tol=1e-8,
        ):
            # The paper broker treats Proposal.price as its requested execution
            # mark and applies slippage afterwards. A merely fresh but different
            # market row must never authorize a stale/invented requested price.
            violations.append("proposal_price_not_current_mark")
        if proposal.order_type == "limit":
            violations.append("paper_internal_limit_not_supported")
        violations.extend(_paper_opportunity_identity_violations(proposal))

    # 2. Market allowed
    if proposal.market_type == "spot" and not cfg.exchange.enabled_markets.get("spot"):
        violations.append("spot_market_disabled")
    if proposal.market_type == "usdt_futures" and not cfg.exchange.enabled_markets.get("usdt_futures"):
        violations.append("futures_market_disabled")

    # 2a. A local futures Position is admissible only when the durable derivatives
    # collector has a fresh next-settlement anchor for this exact symbol.  The paper
    # close path then retains that venue's schedule and computes signed funding as
    # ``local_simulation``.  A missing/read-failed schedule remains fail-closed here;
    # existing Positions always retain their separate protective exit path.
    if (
        proposal.source == "native"
        and proposal.execution_mode == "paper_internal"
        and proposal.market_type == "usdt_futures"
    ):
        try:
            from aletheia.portfolio.local_funding import (
                has_fresh_local_funding_schedule,
            )

            funding_available = has_fresh_local_funding_schedule(proposal.symbol)
        except Exception as exc:
            logger.warning(
                "local funding schedule unavailable for %s (%s)",
                proposal.symbol,
                type(exc).__name__,
            )
            funding_available = False
        if not funding_available:
            violations.append("local_paper_futures_funding_unavailable")

    # 2b. Entry-plan completeness (owner discussion #6, 2026-07-03): every native
    #     local-paper entry, plus every native exchange limit entry, must commit all
    #     four numbers BEFORE the order — entry (price), stop (stop_price),
    #     take-profit, and reward:risk (risk_reward). Exchange market closes/reduces
    #     and reconciler-adopted chains legitimately carry no TP/RR and stay exempt.
    requires_complete_entry_plan = proposal.source == "native" and (
        proposal.order_type == "limit"
        or (
            proposal.execution_mode == "paper_internal"
            and proposal.order_type == "market"
        )
    )
    if requires_complete_entry_plan:
        _missing = []
        if (
            proposal.price is None
            or not math.isfinite(proposal.price)
            or proposal.price <= 0
        ):
            _missing.append("entry_price")
        if (
            proposal.stop_price is None
            or not math.isfinite(proposal.stop_price)
            or proposal.stop_price <= 0
        ):
            _missing.append("stop_loss")
        if (
            proposal.take_profit is None
            or not math.isfinite(proposal.take_profit)
            or proposal.take_profit <= 0
        ):
            _missing.append("take_profit")
        if (
            proposal.risk_reward is None
            or not math.isfinite(proposal.risk_reward)
            or proposal.risk_reward <= 0
        ):
            _missing.append("risk_reward")
        if _missing:
            violations.append(f"incomplete_entry_plan:{'+'.join(_missing)}")
        else:
            # 2c. Side-of-entry geometry (#98): all four numbers are present, but they
            #     must sit on the correct side of entry, else the "plan" is inverted
            #     (e.g. a long whose stop is ABOVE entry). Presence != a valid bet.
            entry = proposal.price
            if proposal.direction == "long" and not (proposal.stop_price < entry < proposal.take_profit):
                violations.append("entry_plan_geometry:expected_stop<entry<tp_for_long")
            elif proposal.direction == "short" and not (proposal.take_profit < entry < proposal.stop_price):
                violations.append("entry_plan_geometry:expected_tp<entry<stop_for_short")
            expected_risk_reward = calculate_entry_plan_risk_reward(
                entry,
                proposal.stop_price,
                proposal.take_profit,
            )
            if expected_risk_reward is not None:
                if not math.isclose(
                    proposal.risk_reward,
                    expected_risk_reward,
                    rel_tol=1e-9,
                    abs_tol=1e-9,
                ):
                    violations.append("entry_plan_risk_reward_mismatch")
                if not entry_plan_risk_reward_is_plausible(expected_risk_reward):
                    violations.append(RISK_REWARD_OUTSIDE_ENVELOPE)

    # 3. Live state — fail CLOSED. A *_live_limited order is permitted ONLY when the
    #    live state is the explicit unlocked value ("live_limited", set by human
    #    confirmation). Any other value (default "live_disabled", a typo, or an
    #    unknown/future state) must BLOCK — the previous `== "live_disabled"` check
    #    let every non-disabled value through, permitting live orders by accident (#7).
    live_state = cfg.live.state
    if proposal.execution_mode in ("bitget_live_limited", "okx_live_limited", "binance_live_limited") and live_state != "live_limited":
        violations.append("live_disabled")

    # 4. Permission mode
    perm = cfg.agent.permission_mode
    if proposal.execution_mode != "paper_internal" and perm == "Observe":
        violations.append(f"permission_mode_insufficient:{perm}")

    # 5. Max position size. Live and demo each have a configured hard cap, tightened
    #    to the cached available balance of the matching external account when one
    #    exists. This never raises a cap and never calls the exchange on an order path.
    #    Distinct tokens keep demo size rejections out of live-critical violations.
    #    Portfolio drawdown scaling: if current equity is significantly below peak,
    #    reduce the effective cap proportionally (industry standard: halve size at
    #    10% drawdown, reject at 20% drawdown).
    drawdown_scale = _portfolio_drawdown_scale(proposal.execution_mode)
    if proposal.quantity and proposal.price:
        notional = proposal.quantity * proposal.price
        position_cap = _effective_position_notional_cap(
            risk,
            proposal.execution_mode,
        )
        if position_cap is None:
            violations.append("paper_account_balance_unavailable")
        else:
            effective_cap = position_cap * drawdown_scale
            if _is_live(proposal.execution_mode):
                if notional > effective_cap:
                    violations.append(
                        f"exceeds_max_position_notional:{notional}>{effective_cap}"
                    )
            elif notional > effective_cap:
                violations.append(
                    f"exceeds_demo_position_notional:{notional}>{effective_cap}"
                )

    # 6. Max open positions
    # trade_school policy (D2-C02): shared pool — serenity and main-track positions
    # occupy the same max_open_positions quota. Isolated pools (serenity only counts
    # against serenity quota) are a Phase 2 decision deferred until serenity_executor
    # is implemented. Until then ALL open positions count regardless of trade_school.
    open_positions = list_all_open_positions()
    prop_is_live = _is_live(proposal.execution_mode)
    try:
        unresolved_entries = _unresolved_entry_reservations()
    except Exception as exc:
        # Accepted exchange Orders are part of Risk authority.  A failed local
        # read cannot be interpreted as zero pending exposure.
        logger.warning(
            "unresolved entry exposure unavailable (%s)", type(exc).__name__
        )
        unresolved_entries = []
        violations.append("unresolved_entry_exposure_unavailable")
    try:
        pre_order_entries = _pre_order_entry_reservations(
            exclude_proposal_id=proposal.id,
        )
    except Exception as exc:
        logger.warning(
            "pre-Order entry Risk reservations unavailable (%s)",
            type(exc).__name__,
        )
        pre_order_entries = []
        violations.append("entry_risk_reservation_unavailable")
    pool_unresolved_entries = [
        entry for entry in unresolved_entries
        if _is_live(entry.execution_mode) == prop_is_live
    ]
    pool_pre_order_entries = [
        entry for entry in pre_order_entries
        if _is_live(entry.execution_mode) == prop_is_live
    ]
    # A broken authority chain cannot use its own mutable mode/symbol to choose
    # which pool sees the failure. Reject every new Proposal until the durable
    # identity is repaired, while still using valid rows for normal pool counts.
    for reason in sorted({
        reason
        for entry in unresolved_entries
        for reason in entry.invalid_reasons
    }):
        violations.append(reason)
    for reason in sorted({
        reason
        for entry in pre_order_entries
        for reason in entry.invalid_reasons
    }):
        violations.append(reason)

    unresolved_entry_slots = sum(
        1 for entry in pool_unresolved_entries if not entry.has_open_position
    )
    pre_order_entry_slots = len(pool_pre_order_entries)
    if prop_is_live:
        live_positions = [p for p in open_positions if _is_live(p.get("execution_mode", ""))]
        live_slot_count = (
            len(live_positions) + unresolved_entry_slots + pre_order_entry_slots
        )
        if live_slot_count >= risk.max_open_positions_live:
            violations.append(f"max_live_positions_reached:{live_slot_count}")
    else:
        demo_positions = [p for p in open_positions if not _is_live(p.get("execution_mode", ""))]
        demo_slot_count = (
            len(demo_positions) + unresolved_entry_slots + pre_order_entry_slots
        )
        if demo_slot_count >= risk.max_open_positions_demo:
            violations.append(f"max_demo_positions_reached:{demo_slot_count}")

    # 6b. No duplicate open position for same symbol in the same pool (demo or live).
    #     Prevents the system from opening a second position when one already exists
    #     on a different broker in the same pool (e.g. paper_internal + okx_demo both
    #     holding ETHUSDT short at the same time).
    same_symbol_open = [
        p for p in open_positions
        if p.get("symbol") == proposal.symbol
        and _is_live(p.get("execution_mode", "")) == prop_is_live
    ]
    same_symbol_unresolved = any(
        entry.symbol == proposal.symbol for entry in pool_unresolved_entries
    )
    same_symbol_pre_order = any(
        entry.symbol == proposal.symbol for entry in pool_pre_order_entries
    )
    if same_symbol_open or same_symbol_unresolved or same_symbol_pre_order:
        violations.append(f"symbol_already_open:{proposal.symbol}")

    # 7. Short allowed
    if proposal.direction == "short":
        if _is_live(proposal.execution_mode) and not risk.allow_short_live:
            violations.append("short_not_allowed_live")
        elif not _is_live(proposal.execution_mode) and not risk.allow_short_demo:
            violations.append("short_not_allowed_demo")

    # 7b. Spot cannot be shorted (#24). A short is only expressible on a futures/perp
    #     instrument; a spot+short would reach the broker as a spot SELL of an asset
    #     the account may not hold. Reject at the risk boundary regardless of
    #     allow_short_* (which only gate direction, not instrument type).
    if proposal.direction == "short" and proposal.market_type == "spot":
        violations.append("spot_short_not_allowed")

    # 8. Max daily loss — global brake. If today's realized loss breaches the limit,
    #    reject and auto-trip the kill switch (guide §21.3, §2.4).
    #    Respects kill_switch_enabled_live / kill_switch_enabled_demo toggles:
    #    the daily-loss violation is always recorded, but the kill switch only
    #    auto-trips when the matching toggle is enabled.
    # Capital base is mode/account-specific (CR-RE-03). The default follows the
    # matching account equity; Custom uses only that mode's explicitly stored base.
    capital_base, capital_base_reason = _resolve_capital_base(
        proposal.execution_mode,
        resolved_policy.policy,
        cfg,
    )
    if capital_base is None:
        violations.append(f"capital_base_unavailable:{capital_base_reason}")
        capital_base = 0.0
    daily_loss_limit = capital_base * (risk.max_daily_loss_pct / 100.0)
    account_loss = authoritative_account_realized_loss(proposal.execution_mode)
    realized = account_loss.realized_pnl_usdt
    if not account_loss.available or realized is None:
        violations.append(f"account_loss_unavailable:{account_loss.reason}")
    elif daily_loss_limit > 0 and realized <= -daily_loss_limit:
        violations.append(f"max_daily_loss_exceeded:{realized}<=-{daily_loss_limit}")
        is_live = _is_live(proposal.execution_mode)
        ks_enabled = risk.kill_switch_enabled_live if is_live else risk.kill_switch_enabled_demo
        if ks_enabled and not mode_brake_active:
            from aletheia.risk.kill_switch import activate as _trip
            _trip(
                reason=f"daily loss {realized} breached limit -{daily_loss_limit}",
                actor="system",
                scope=resolved_policy.mode,
            )

    # 9a. Exchange allowlist — reject if target exchange != allowed_exchange.
    # binance_testnet/binance_live_limited and okx_demo/okx_live_limited are self-contained
    # (own credentials, independent of cfg.exchange) so exempt from allowlist checks.
    if proposal.execution_mode in ("binance_testnet", "binance_live_limited", "okx_demo", "okx_live_limited"):
        pass  # self-contained exchange modes use their own credentials
    else:
        if risk.allowed_exchange and cfg.exchange.name != risk.allowed_exchange:
            violations.append(f"exchange_not_allowed:{cfg.exchange.name}!={risk.allowed_exchange}")
        # 9. Account mode match — order mode and exchange account must agree.
        #    A live order requires exchange.mode == 'live'; a real demo order requires
        #    exchange.mode == 'demo'. Without the demo side of this check, a bitget_demo
        #    proposal evaluated while exchange.mode == 'live' would place a REAL live order
        #    through the demo path (trust-boundary hole). paper_internal is local sim and
        #    is exempt (it never touches the exchange).
        if _is_live(proposal.execution_mode) and cfg.exchange.mode != "live":
            violations.append(f"account_mode_mismatch:order=live,exchange={cfg.exchange.mode}")
        if proposal.execution_mode == "bitget_demo" and cfg.exchange.mode != "demo":
            violations.append(f"account_mode_mismatch:order=demo,exchange={cfg.exchange.mode}")

    # 10. Max leverage (futures). Per-asset-tier caps + account-level flat cap.
    if proposal.market_type == "usdt_futures" and proposal.leverage:
        from aletheia.trading.instruments import get_max_leverage, get_asset_tier
        tier_cap = get_max_leverage(proposal.symbol, _is_live(proposal.execution_mode), cfg)
        if proposal.leverage > tier_cap:
            tier = get_asset_tier(proposal.symbol)
            violations.append(f"exceeds_max_leverage:{proposal.leverage}>{tier_cap}(tier={tier})")

    # Live-only checks: data freshness, single-trade risk, strategy lifecycle.
    if _is_live(proposal.execution_mode):
        # 11. Data freshness
        age = _market_data_age_seconds(proposal.symbol, market_type=proposal.market_type)
        if age is None or age > _MAX_DATA_AGE_SECONDS:
            violations.append(f"stale_market_data:age={age}")

        # 12. Total live exposure limit (open positions + proposed notional).
        if proposal.price and proposal.quantity:
            current_exposure = _current_live_exposure(
                unresolved_entries,
                pre_order_entries,
            )
            projected = current_exposure + (proposal.price * proposal.quantity)
            live_exposure_cap = risk.max_total_live_exposure_pct / 100.0 * capital_base
            if live_exposure_cap > 0 and projected > live_exposure_cap:
                violations.append(f"max_total_live_exposure_exceeded:{projected}>{live_exposure_cap}")

        # 13. Max single trade risk must be calculable and within cap.
        if not proposal.stop_price:
            violations.append("max_single_trade_risk_missing_stop_price")
        elif proposal.price and proposal.quantity:
            risk_amount = abs(proposal.price - proposal.stop_price) * proposal.quantity
            max_risk = capital_base * (risk.max_single_trade_risk_pct / 100.0)
            if max_risk > 0 and risk_amount > max_risk:
                violations.append(f"exceeds_single_trade_risk:{risk_amount}>{max_risk}")

        # 14. Scanner-family evidence gate — live needs a family with a proven
        # clean-window edge. Replaces the deleted strategy-FSM eligibility gate
        # (Position C 2026-07-02): the rules engine no longer promotes strategies
        # through paper→live, so live-eligibility is now evidence-based on the
        # proposal's scanner_family (resolved via its opportunity) rather than a
        # per-strategy lifecycle state. Fail-closed: unknown family / no evidence
        # / insufficient samples / sub-threshold win-rate all reject the live trade.
        prop_family = _resolve_proposal_family(proposal)
        min_n = int(getattr(risk, "live_family_min_samples", 30) or 0)
        min_wr = float(getattr(risk, "live_family_min_win_rate_pct", 50.0) or 0.0)
        if prop_family is None:
            violations.append("family_not_live_eligible:unknown")
        else:
            edge = _family_live_edge(
                prop_family,
                proposal.direction,
                getattr(proposal, "market_type", "spot"),
            )
            if edge is None:
                violations.append(f"family_not_live_eligible:{prop_family}:no_evidence")
            elif (edge.get("n") or 0) < min_n:
                violations.append(
                    f"family_not_live_eligible:{prop_family}:samples={edge.get('n')}<{min_n}"
                )
            elif (edge.get("win_rate") or 0.0) < min_wr:
                violations.append(
                    f"family_not_live_eligible:{prop_family}:wr={edge.get('win_rate')}<{min_wr}"
                )
    else:
        # Demo single-trade risk: paper trades may omit a stop (we don't force one),
        # but when a stop is present the loss-at-stop must stay within the demo cap.
        # Distinct token keeps this out of the live-critical-violation count.
        if proposal.stop_price and proposal.price and proposal.quantity:
            demo_risk_amount = abs(proposal.price - proposal.stop_price) * proposal.quantity
            demo_max_risk = capital_base * (risk.max_demo_single_trade_risk_pct / 100.0)
            if demo_max_risk > 0 and demo_risk_amount > demo_max_risk:
                violations.append(f"exceeds_demo_single_trade_risk:{demo_risk_amount}>{demo_max_risk}")

        # Demo data freshness — bitget_demo places a REAL exchange order, so it must
        # not trade against a frozen feed. paper_internal (local sim) is exempt: it has
        # no exchange-side price and runs even when collection is paused. Looser bound
        # than live (see _MAX_DEMO_DATA_AGE_SECONDS).
        if proposal.execution_mode in (
            "bitget_demo",
            "okx_demo",
            "binance_testnet",
        ):
            demo_age = _market_data_age_seconds(proposal.symbol, market_type=proposal.market_type)
            if demo_age is None or demo_age > _MAX_DEMO_DATA_AGE_SECONDS:
                violations.append(f"stale_market_data_demo:age={demo_age}")

    # 15. Lesson-based constraints — accepted lessons with actionable recommendations
    #     can impose symbol/direction blocks learned from past failures.
    lesson_violations = _check_lesson_constraints(proposal)
    violations.extend(lesson_violations)

    # 16. Portfolio correlation guard — sector concentration + portfolio BTC beta.
    correlation_violations = _check_correlation_exposure(
        proposal,
        cfg,
        unresolved_entries,
        pre_order_entries,
    )
    violations.extend(correlation_violations)

    # 17. Account-level leverage ratio (futures only, additive).
    if proposal.market_type == "usdt_futures":
        acct_lev_violations = _check_account_leverage_ratio(
            proposal,
            cfg,
            unresolved_entries=unresolved_entries,
            pre_order_entries=pre_order_entries,
        )
        violations.extend(acct_lev_violations)

    # 18. Liquidation buffer (futures only, additive).
    if proposal.market_type == "usdt_futures" and proposal.leverage and proposal.price:
        liq_violations = _check_liquidation_buffer(proposal, cfg)
        violations.extend(liq_violations)

    # 19. Per-symbol cumulative notional cap (CUM-ADD). Same-symbol adds average-merge
    #     into one position (paper_broker / live brokers), so the single-position cap
    #     (#5) does not bound repeated accumulation. Sum existing open notional for this
    #     symbol in the same live/demo class + the proposed notional; reject if over cap.
    per_symbol_cap = getattr(risk, "max_per_symbol_notional_usdt", 0.0)
    if per_symbol_cap and per_symbol_cap > 0 and proposal.price and proposal.quantity:
        prop_is_live = _is_live(proposal.execution_mode)
        existing_sym_notional = 0.0
        for p in open_positions:
            if p.get("symbol") != proposal.symbol:
                continue
            if _is_live(p.get("execution_mode", "")) != prop_is_live:
                continue
            try:
                q = float(p.get("quantity") or 0)
                px = float(p.get("current_price") or p.get("entry_price") or 0)
                existing_sym_notional += abs(q * px)
            except (TypeError, ValueError):
                continue
        for entry in (*pool_unresolved_entries, *pool_pre_order_entries):
            if entry.symbol != proposal.symbol:
                continue
            if entry.price is None or entry.reserved_quantity is None:
                continue
            existing_sym_notional += abs(entry.reserved_quantity * entry.price)
        projected = existing_sym_notional + abs(proposal.price * proposal.quantity)
        if projected > per_symbol_cap:
            violations.append(
                f"exceeds_per_symbol_notional:{proposal.symbol}:"
                f"{projected:.0f}>{per_symbol_cap:.0f}"
            )

    return _persist_risk_result(
        proposal,
        violations,
        decision_role=decision_role,
    )


def _native_input_violations(proposal: TradeProposal) -> list[str]:
    """Validate native execution intent before configurable risk checks.

    Adopted chains describe exchange facts already observed by reconciliation;
    they are not new native order intent and retain their separate exception.
    """
    if proposal.source != "native":
        return []

    violations: list[str] = []
    symbol = proposal.symbol
    has_canonical_shape = (
        isinstance(symbol, str)
        and symbol == symbol.strip()
        and symbol.count("/") == 1
        and all(symbol.split("/", 1))
    )
    # r-series equity tokens deliberately preserve their lower-case leading
    # ``r``; every other native symbol must equal the canonical converter output.
    symbol_is_canonical = has_canonical_shape and symbol == to_canonical(symbol)
    if has_canonical_shape and not symbol_is_canonical:
        try:
            from aletheia.data.symbols import is_stock_token

            symbol_is_canonical = is_stock_token(symbol)
        except Exception:
            symbol_is_canonical = False
    if not symbol_is_canonical:
        violations.append("invalid_symbol_format")

    if proposal.market_type not in _NATIVE_MARKET_TYPES:
        violations.append("invalid_market_type")
    if proposal.direction not in _NATIVE_DIRECTIONS:
        violations.append("invalid_direction")
    if proposal.order_type not in _NATIVE_ORDER_TYPES:
        violations.append("invalid_order_type")

    quantity = proposal.quantity
    if quantity is None or not math.isfinite(quantity) or quantity <= 0:
        violations.append("invalid_quantity")
    price = proposal.price
    if price is None or not math.isfinite(price) or price <= 0:
        violations.append("invalid_price")
    if proposal.market_type == "usdt_futures":
        leverage = proposal.leverage
        if leverage is None or not math.isfinite(leverage) or leverage < 1:
            violations.append("invalid_leverage")
    if proposal.execution_mode not in _NATIVE_EXECUTION_MODES:
        violations.append("invalid_execution_mode")
    return violations


def _verified_original_proposal_commit_hash(
    proposal: TradeProposal,
    *,
    require_evaluated_snapshot_match: bool = False,
) -> str | None:
    """Verify and return stored immutable identity for an evaluated model copy."""
    row = get_trade_proposal(proposal.id)
    if row is not None:
        stored = TradeProposal.model_validate(row)
        if stored.commit_hash is None:
            raise ProposalIntegrityError("stored proposal has no commit hash")
        if _compute_proposal_hash(stored) != stored.commit_hash:
            raise ProposalIntegrityError(
                "stored proposal content does not match commit hash"
            )
        if require_evaluated_snapshot_match:
            # Venue preparation may normalize quantity, but every other field
            # covered by the original Proposal hash must remain identical. Put
            # the stored quantity back into the evaluated copy and verify it
            # against the stored commit: this uses the canonical hash field set
            # instead of maintaining a second, drift-prone comparison list.
            original_quantity_snapshot = proposal.model_copy(
                update={"quantity": stored.quantity}
            )
            if (
                _compute_proposal_hash(original_quantity_snapshot)
                != stored.commit_hash
            ):
                raise ProposalIntegrityError(
                    "evaluated proposal content differs from stored proposal"
                )
        return stored.commit_hash
    # Direct unit-level evaluations may use an unpersisted model. Gateway paths
    # always persist first; when a caller supplies a hash, still verify it.
    if (
        proposal.commit_hash is not None
        and _compute_proposal_hash(proposal) != proposal.commit_hash
    ):
        raise ProposalIntegrityError("proposal content does not match commit hash")
    return proposal.commit_hash


def _risk_result_from_row(row: dict) -> RiskCheckResult:
    try:
        parsed = json.loads(row.get("violations") or "[]")
        violations = parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        violations = []
    return RiskCheckResult(
        approved=bool(row.get("approved")),
        risk_score=float(row.get("risk_score") or 0.0),
        violations=violations,
        allowed_execution_mode=row.get("allowed_execution_mode") or "paper_internal",
        message=row.get("message") or "",
        decision_id=row.get("id"),
    )


def _persist_risk_result(
    proposal: TradeProposal,
    violations: list[str],
    *,
    decision_role: str,
) -> RiskCheckResult:
    """Persist a role-labelled decision over the immutable evaluated snapshot."""
    approved = len(violations) == 0
    allowed_mode = proposal.execution_mode if approved else "paper_internal"
    result = RiskCheckResult(
        approved=approved,
        risk_score=min(len(violations) * 0.2, 1.0),
        violations=violations,
        allowed_execution_mode=allowed_mode,
        message="Approved" if approved else f"Rejected: {', '.join(violations)}",
    )

    policy_identity = None
    try:
        from aletheia.risk.policy import resolve_risk_policy

        policy_identity = resolve_risk_policy(load_config(), proposal.execution_mode)
    except Exception:
        policy_identity = None

    decision = RiskDecision(
        proposal_id=proposal.id,
        approved=approved,
        risk_score=result.risk_score,
        violations=json.dumps(violations),
        allowed_execution_mode=allowed_mode,
        # Preserve the requested mode verbatim — allowed_mode is downgraded to
        # paper_internal on rejection, which erased the live/demo signal that
        # strategy_lifecycle needs to classify a live rejection as critical (CR-RE-01).
        proposal_execution_mode=proposal.execution_mode,
        decision_role=decision_role,
        evaluated_quantity=proposal.quantity,
        proposal_commit_hash=_verified_original_proposal_commit_hash(
            proposal,
            require_evaluated_snapshot_match=(decision_role == "authoritative"),
        ),
        risk_mode=policy_identity.mode if policy_identity else None,
        policy_id=policy_identity.policy.policy_id if policy_identity else None,
        policy_revision=policy_identity.policy.revision if policy_identity else None,
        policy_hash=policy_identity.policy_hash if policy_identity else None,
        message=result.message,
    )
    created = True
    if decision_role == "authoritative":
        stored, created = insert_authoritative_risk_decision(decision)
        if not created:
            return _risk_result_from_row(stored)
        result.decision_id = stored["id"]
    else:
        insert_risk_decision(decision)
        result.decision_id = decision.id

    # Filter evidence is consumed as the final Risk verdict. Preflight rows stay
    # visible in risk_decisions but do not double-count the same candidate.
    if decision_role == "authoritative":
        try:
            from aletheia.analytics.filter_evidence import record_risk_filter_decision

            record_risk_filter_decision(
                proposal,
                risk_decision_id=result.decision_id,
                approved=approved,
                violations=violations,
            )
        except Exception as evidence_error:
            logger.warning("risk filter evidence unavailable: %s", evidence_error)

    return result


_STATIC_RISK_LESSON_SOURCE_TYPES = frozenset({"manual", "owner", "human"})


def _learned_lesson_constraint_authorized(lesson, proposal, recommendation: dict) -> bool:
    """Return true only for a static owner rule or an authorized paper treatment."""
    source_type = str(getattr(lesson, "source_type", "") or "").strip().lower()
    if source_type in _STATIC_RISK_LESSON_SOURCE_TYPES:
        return True
    proposal_id = str(getattr(proposal, "id", "") or "").strip()
    if not proposal_id:
        raise ValueError("risk_lesson_constraint_missing_proposal_id")
    from aletheia.learning.experiments import apply_boolean_treatment
    decision = apply_boolean_treatment(
        path_id="risk_lesson_constraint",
        scope={
            "lesson_id": lesson.lesson_id,
            "source_type": source_type,
            "category": getattr(lesson, "category", None),
        },
        action={
            "type": "proposal_filter",
            "recommendation": recommendation,
        },
        source_cohort=f"ps_lessons:{source_type or 'unknown'}",
        target_horizon="4h",
        candidate_key=f"risk_lesson:{proposal_id}:{lesson.lesson_id}",
        consumer="risk_engine",
        consumer_stage="proposal_filter",
        # record_risk_filter_decision() creates this exact fixed-horizon candidate
        # for every authoritative Proposal.  The horizon suffix is required by
        # the Learning cohort join; bare ``proposal:<id>`` would never settle.
        outcome_candidate_key=f"proposal:{proposal_id}:4h",
        opportunity_id=getattr(proposal, "opportunity_id", None),
        manifest={
            "proposal_id": proposal_id,
            "lesson_id": lesson.lesson_id,
            "static_owner_rule": False,
        },
    )
    return bool(decision.applied and decision.value >= 1.0)


def _check_lesson_constraints(proposal) -> list[str]:
    """Check accepted lessons for actionable constraints.

    Lessons with category='risk' and recommendation_json containing:
      - {"action": "block_symbol", "symbol": "X"} → reject trades on symbol X
      - {"action": "block_direction", "direction": "short"} → reject shorts
      - {"action": "reduce_size", "factor": 0.5} → (advisory, logged but not enforced here)
      - {"action": "block_scanner", "scanner": "X"} → reject trades from scanner X
    """
    violations = []
    try:
        from aletheia.learning.lessons import LessonStore
        import json
        store = LessonStore()
        # L-Bug-E fix: 'accepted' is a transient state (lesson_reviewer calls
        # accept then mark_applied in the same run), so query 'applied' instead.
        # SQLite LIMIT -1 means no limit. Applied Risk lessons are active
        # authority, not a display feed; truncating them silently disables older
        # blocks once the table grows.
        active_lessons = store.list_all(status="applied", limit=-1)
        sym_canonical = to_canonical(proposal.symbol)

        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()

        for lesson in active_lessons:
            if not lesson.recommendation_json:
                logger.debug("lesson %s: recommendation_json is NULL, skipping", lesson.lesson_id)
                continue
            try:
                rec = json.loads(lesson.recommendation_json)
            except (ValueError, TypeError):
                continue

            # Guard: skip expired lessons (TTL exceeded).
            if lesson.expires_at and now_iso > lesson.expires_at:
                logger.debug("lesson %s expired at %s, skipping", lesson.lesson_id, lesson.expires_at)
                continue

            # Guard: dry-run window — log advisory only, do not block.
            in_dry_run = lesson.dry_run_until and now_iso < lesson.dry_run_until
            if in_dry_run:
                logger.info(
                    "lesson %s in dry-run window (until %s), advisory only",
                    lesson.lesson_id, lesson.dry_run_until,
                )

            action = rec.get("action")
            # Contract (#93): hard blocks (block_symbol/direction/scanner) are only
            # valid from category='risk' lessons — the docstring says so but the code
            # never checked. A non-risk lesson carrying a block_* action must NOT hard
            # reject (that gates trades outside the documented risk-lesson boundary).
            # Advisory actions (reduce_size) are unaffected.
            if action in ("block_symbol", "block_direction", "block_scanner") and getattr(lesson, "category", None) != "risk":
                logger.debug("lesson %s action=%s skipped: category=%s is not 'risk'",
                             lesson.lesson_id, action, getattr(lesson, "category", None))
                continue
            if action == "block_symbol":
                blocked_sym = to_canonical(rec.get("symbol", ""))
                if blocked_sym and blocked_sym == sym_canonical:
                    token = f"lesson_block_symbol:{lesson.lesson_id}:{blocked_sym}"
                    if in_dry_run:
                        logger.info("dry-run advisory (would block): %s", token)
                    elif _learned_lesson_constraint_authorized(lesson, proposal, rec):
                        violations.append(token)
                    else:
                        logger.info("learned lesson advisory (no treatment authority): %s", token)

            elif action == "block_direction":
                blocked_dir = rec.get("direction", "")
                if blocked_dir and proposal.direction == blocked_dir:
                    token = f"lesson_block_direction:{lesson.lesson_id}:{blocked_dir}"
                    if in_dry_run:
                        logger.info("dry-run advisory (would block): %s", token)
                    elif _learned_lesson_constraint_authorized(lesson, proposal, rec):
                        violations.append(token)
                    else:
                        logger.info("learned lesson advisory (no treatment authority): %s", token)

            elif action == "block_scanner":
                blocked_scanner = rec.get("scanner", "")
                if blocked_scanner and getattr(proposal, "scanner_family", None) == blocked_scanner:
                    token = f"lesson_block_scanner:{lesson.lesson_id}:{blocked_scanner}"
                    if in_dry_run:
                        logger.info("dry-run advisory (would block): %s", token)
                    elif _learned_lesson_constraint_authorized(lesson, proposal, rec):
                        violations.append(token)
                    else:
                        logger.info("learned lesson advisory (no treatment authority): %s", token)

            elif action == "reduce_size":
                # Advisory only — log but do not block (docstring intent preserved)
                factor = float(rec.get("factor", 1.0))
                logger.info(
                    "lesson reduce_size advisory: lesson=%s factor=%.2f (not enforced as hard block)",
                    lesson.lesson_id, factor,
                )

    except Exception as e:
        logger.warning("_check_lesson_constraints failed (safe-reject): %s", e)
        violations.append("lesson_check_error:internal_failure")
    return violations


def _portfolio_drawdown_scale(execution_mode: str) -> float:
    """Compute position size scaling factor based on portfolio drawdown.

    Uses recent portfolio_snapshots to detect peak-to-current drawdown.
    Returns 1.0 (no reduction) to 0.0 (reject entirely) as a multiplier
    on position size caps.

    Scale: 0-5% DD → 1.0, 5-10% DD → linear from 1.0 to 0.5, 10-20% → 0.5 to 0.0

    Data source by mode (this is the bug fix — see below):
      - Exchange-backed modes (okx_demo / *_demo / live): use the
        `exchange_account` snapshot rows, whose total_value_usdt is the REAL
        account equity. The per-mode rows (e.g. okx_demo) store position
        *notional* (Σ qty×price), NOT equity — so closing a winning position
        collapses notional (272→16) and the old code misread that as a 94%
        "drawdown", zeroing the size cap and rejecting every demo proposal
        while the real account sat untouched at ~$88k. Drawdown must track
        equity, not how much notional happens to be open right now.
      - paper_internal: its snapshots contain managed-position notional, not a
        simulated account-equity series. Skip this scaling until a dedicated
        paper-equity ledger exists; the configured cap and other risk checks
        continue to bound the order.
    """
    try:
        # Local paper has no trustworthy equity history. Its own snapshots are
        # position notional, while exchange_account snapshots belong to a separate
        # account and may be stale or unrelated. Either source can fabricate a
        # drawdown and turn every proposal into a $0-cap rejection.
        if execution_mode == "paper_internal":
            return 1.0

        # View-completeness gate (P0, 2026-07-02): for exchange-backed modes, if the
        # active broker is missing from the unified view (e.g. OKX API down), the
        # equity snapshots may reflect the WRONG broker's balance — computing a
        # drawdown from that fabricates a false ~99% DD that zeroes the size cap.
        # Skip drawdown scaling (return 1.0, the $-notional caps still bound size).
        from aletheia.portfolio.exchange_sync import active_broker_view_complete
        _complete, _ = active_broker_view_complete()
        if not _complete:
            return 1.0

        from aletheia.storage.repositories import list_portfolio_snapshots
        snapshots = list_portfolio_snapshots(limit=50)
        if len(snapshots) < 3:
            return 1.0

        # Exchange-backed modes use real account equity, never per-mode position
        # notional. paper_internal returned above and cannot reach this branch.
        mode_snapshots = [
            s for s in snapshots if s.get("execution_mode") == "exchange_account"
        ]
        if len(mode_snapshots) < 3:
            return 1.0

        values = [float(s.get("total_value_usdt") or 0) for s in mode_snapshots]
        peak = max(values)
        current = values[-1]

        if peak <= 0:
            return 1.0

        drawdown_pct = (peak - current) / peak * 100.0

        if drawdown_pct <= 5.0:
            return 1.0
        elif drawdown_pct <= 10.0:
            return 1.0 - (drawdown_pct - 5.0) / 5.0 * 0.5
        elif drawdown_pct <= 20.0:
            return 0.5 - (drawdown_pct - 10.0) / 10.0 * 0.5
        else:
            return 0.0
    except Exception:
        return 1.0


def _current_live_exposure(
    unresolved_entries: list[_UnresolvedEntryReservation] | None = None,
    pre_order_entries: list[_PreOrderEntryReservation] | None = None,
) -> float:
    """Sum live Positions plus every not-yet-represented entry allowance."""
    total = 0.0
    for row in list_all_open_positions():
        if not _is_live(row.get("execution_mode", "")):
            continue
        try:
            quantity = float(row.get("quantity") or 0.0)
            price = float(row.get("current_price") or row.get("entry_price") or 0.0)
            total += abs(quantity * price)
        except (TypeError, ValueError):
            continue
    if unresolved_entries is None:
        unresolved_entries = _unresolved_entry_reservations()
    for entry in unresolved_entries:
        if not _is_live(entry.execution_mode):
            continue
        if entry.price is None or entry.reserved_quantity is None:
            # Evaluation already emits the stable invalid-data violation.  Never
            # manufacture a numeric exposure from missing authority here.
            continue
        total += abs(entry.reserved_quantity * entry.price)
    if pre_order_entries is None:
        pre_order_entries = _pre_order_entry_reservations()
    for entry in pre_order_entries:
        if not _is_live(entry.execution_mode):
            continue
        if entry.price is None or entry.reserved_quantity is None:
            continue
        total += abs(entry.reserved_quantity * entry.price)
    return total


def _check_correlation_exposure(
    proposal: TradeProposal,
    cfg,
    unresolved_entries: list[_UnresolvedEntryReservation] | None = None,
    pre_order_entries: list[_PreOrderEntryReservation] | None = None,
) -> list[str]:
    """Check #16: sector concentration + portfolio BTC beta.

    Durable unresolved Orders and pre-Order Risk reservations are exposure even
    before a Position exists. Returns an empty list when the guard is disabled,
    there is no existing exposure, or market correlation data is insufficient.
    Never raises; an internal read failure produces a fail-closed violation.
    """
    risk = cfg.risk
    if not getattr(risk, "correlation_guard_enabled", True):
        return []
    violations: list[str] = []
    try:
        from aletheia.storage.repositories import (
            list_all_open_positions,
            list_opportunities,
        )
        from aletheia.storage.sqlite import get_db

        proposal_is_live = _is_live(proposal.execution_mode)
        open_positions = [
            position
            for position in list_all_open_positions()
            if _is_live(str(position.get("execution_mode") or ""))
            == proposal_is_live
        ]
        if unresolved_entries is None:
            unresolved_entries = _unresolved_entry_reservations()
        if pre_order_entries is None:
            pre_order_entries = _pre_order_entry_reservations(
                exclude_proposal_id=proposal.id,
            )
        unresolved_entries = [
            entry
            for entry in unresolved_entries
            if _is_live(entry.execution_mode) == proposal_is_live
        ]
        pre_order_entries = [
            entry
            for entry in pre_order_entries
            if _is_live(entry.execution_mode) == proposal_is_live
        ]

        # (symbol, notional, opportunity_id). Keep each position/reservation as
        # an independent row instead of collapsing repeated symbols.
        existing_exposures: list[tuple[str, float, str | None]] = []
        for pos in open_positions:
            try:
                quantity = float(pos.get("quantity") or 0.0)
                price = float(pos.get("current_price") or pos.get("entry_price") or 0.0)
                notional = abs(quantity * price)
            except (TypeError, ValueError):
                notional = 0.0
            if math.isfinite(notional) and notional > 0:
                existing_exposures.append((
                    str(pos.get("symbol") or ""),
                    notional,
                    str(pos.get("opportunity_id")) if pos.get("opportunity_id") else None,
                ))

        for entry in (*unresolved_entries, *pre_order_entries):
            if entry.price is None or entry.reserved_quantity is None:
                continue
            notional = abs(entry.price * entry.reserved_quantity)
            if math.isfinite(notional) and notional > 0:
                existing_exposures.append((
                    entry.symbol,
                    notional,
                    entry.opportunity_id,
                ))

        # Preserve the original behavior: the first exposure is not rejected
        # merely because it is, by definition, 100% of an empty portfolio.
        if not existing_exposures:
            return []

        prop_notional = 0.0
        if proposal.price and proposal.quantity:
            prop_notional = abs(proposal.price * proposal.quantity)

        all_exposures = [
            *existing_exposures,
            (proposal.symbol, prop_notional, proposal.opportunity_id),
        ]

        # ── Sector concentration ──────────────────────────────────────────────
        # Resolve all families in one read so a reservation cannot disappear
        # between per-row lookups and a database failure cannot look like "none".
        opportunity_ids = sorted({
            opportunity_id
            for _, _, opportunity_id in all_exposures
            if opportunity_id
        })
        families: dict[str, str] = {}
        if opportunity_ids:
            placeholders = ",".join("?" for _ in opportunity_ids)
            rows = get_db().execute(
                f"SELECT id, scanner_family FROM opportunities WHERE id IN ({placeholders})",
                opportunity_ids,
            ).fetchall()
            families = {
                str(row[0]): str(row[1])
                for row in rows
                if row[0] and row[1]
            }

        prop_family = families.get(str(proposal.opportunity_id))
        if prop_family:
            total_notional = sum(notional for _, notional, _ in all_exposures)
            if total_notional > 0:
                family_notional = sum(
                    notional
                    for _, notional, opportunity_id in all_exposures
                    if families.get(str(opportunity_id)) == prop_family
                )
                sector_pct = (family_notional / total_notional) * 100
                max_pct = getattr(risk, "max_sector_concentration_pct", 60.0)
                if sector_pct > max_pct:
                    violations.append(
                        f"sector_concentration:{prop_family}:{sector_pct:.0f}%>{max_pct:.0f}%"
                    )

        # ── Portfolio BTC beta ────────────────────────────────────────────────
        max_beta = getattr(risk, "max_portfolio_beta", 3.0)
        prop_beta = _lookup_beta(proposal.symbol)
        if prop_beta is not None and prop_notional > 0:
            total_notional = sum(notional for _, notional, _ in all_exposures)
            if total_notional > 0:
                weighted_beta = prop_beta * prop_notional
                for sym, notional, _ in existing_exposures:
                    b = _lookup_beta(sym)
                    if b is not None:
                        weighted_beta += abs(b) * notional
                portfolio_beta = weighted_beta / total_notional
                if portfolio_beta > max_beta:
                    violations.append(
                        f"portfolio_beta:{portfolio_beta:.1f}>{max_beta:.1f}"
                    )
    except Exception as exc:
        logger.warning("_check_correlation_exposure failed (safe-reject): %s", exc)
        violations.append("correlation_check_error:internal_failure")
    return violations


def _lookup_beta(symbol: str) -> float | None:
    """Fetch the most recent btc_beta for a symbol from universe_feature_snapshots.

    universe_feature_snapshots uses asset_id (FK → universe_registry) + ts,
    not symbol. We join through universe_registry to filter by symbol.
    """
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        row = db.execute(
            "SELECT ufs.btc_beta FROM universe_feature_snapshots ufs "
            "JOIN universe_registry ur ON ufs.asset_id = ur.id "
            "WHERE ur.symbol = ? "
            "ORDER BY ufs.ts DESC LIMIT 1",
            (symbol,)
        ).fetchone()
        if row and row[0] is not None:
            return float(row[0])
    except Exception:
        pass
    return None


def _resolve_proposal_family(proposal) -> str | None:
    """Resolve a proposal's scanner_family via its opportunity (mirrors check #16)."""
    if not getattr(proposal, "opportunity_id", None):
        return None
    try:
        from aletheia.storage.sqlite import get_db
        row = get_db().execute(
            "SELECT scanner_family FROM opportunities WHERE id = ?",
            (proposal.opportunity_id,),
        ).fetchone()
        return row[0] if row and row[0] else None
    except Exception:
        return None


def _family_live_edge(
    family: str,
    direction: str,
    market_type: str = "spot",
) -> dict | None:
    """Matching family/direction/market edge from settled net outcomes.

    Returns the matching family_direction_edge row ({n, win_rate (0-100), ...}) or
    None when the family has no settled evidence in the 7d clean window / on error.
    Check #14 (live-eligibility) gates on this — see engine §14.
    """
    normalized_market = (
        "perp"
        if str(market_type or "spot").lower()
        in {"perp", "futures", "swap", "usdt_futures"}
        else "spot"
    )
    try:
        from aletheia.storage.repositories import family_direction_edge
        for row in family_direction_edge(within_days=7):
            if (
                row.get("scanner_family") == family
                and row.get("direction") == direction
                and (row.get("market_type") or "spot") == normalized_market
            ):
                return row
    except Exception:
        return None
    return None


def _symbol_family(symbol: str, positions: list[dict]) -> str | None:
    """Look up scanner_family for a symbol via its open position's opportunity_id."""
    for pos in positions:
        if pos.get("symbol") != symbol:
            continue
        opp_id = pos.get("opportunity_id")
        if not opp_id:
            continue
        try:
            from aletheia.storage.sqlite import get_db
            db = get_db()
            row = db.execute(
                "SELECT scanner_family FROM opportunities WHERE id = ?", (opp_id,)
            ).fetchone()
            if row and row[0]:
                return row[0]
        except Exception:
            pass
    return None


# ---------------------------------------------------------------------------
# Check #17: Account-level leverage ratio
# ---------------------------------------------------------------------------


class _InvalidPositionExposure(ValueError):
    def __init__(self, field_category: str) -> None:
        self.field_category = field_category
        super().__init__(field_category)


def _validated_position_exposure(position: dict) -> tuple[float, float, float, float]:
    market_type = str(position.get("market_type") or "").strip().lower()
    if market_type not in {"spot", "usdt_futures"}:
        raise _InvalidPositionExposure("market_type")

    values = {}
    for field_name in ("quantity", "entry_price"):
        try:
            value = float(position.get(field_name))
        except (TypeError, ValueError):
            raise _InvalidPositionExposure(field_name) from None
        if not math.isfinite(value) or value <= 0:
            raise _InvalidPositionExposure(field_name)
        values[field_name] = value

    current_price = position.get("current_price")
    if current_price is None:
        price = values["entry_price"]
    else:
        try:
            price = float(current_price)
        except (TypeError, ValueError):
            raise _InvalidPositionExposure("current_price") from None
        if not math.isfinite(price) or price <= 0:
            raise _InvalidPositionExposure("current_price")

    raw_leverage = position.get("leverage")
    if market_type == "spot" and raw_leverage is None:
        leverage = 1.0
    else:
        try:
            leverage = float(raw_leverage)
        except (TypeError, ValueError):
            raise _InvalidPositionExposure("leverage") from None
        if (
            not math.isfinite(leverage)
            or leverage <= 0
            or not leverage.is_integer()
            or (market_type == "spot" and leverage != 1.0)
        ):
            raise _InvalidPositionExposure("leverage")

    position_exposure = abs(values["quantity"] * price) * leverage
    if not math.isfinite(position_exposure):
        raise _InvalidPositionExposure("position_exposure")
    return values["quantity"], values["entry_price"], price, leverage


def _safe_position_id(position: dict) -> str:
    raw = str(position.get("id") or "unknown")
    sanitized = "".join(
        character if character.isalnum() or character in {"-", "_"} else "_"
        for character in raw
    )
    return sanitized[:64] or "unknown"


def _invalid_position_exposure_violation(position: dict, field_category: str) -> str:
    return (
        "account_leverage_check_error:invalid_position_exposure:"
        f"position={_safe_position_id(position)}:field={field_category}"
    )


def _check_account_leverage_ratio(
    proposal: TradeProposal,
    cfg,
    *,
    unresolved_entries: list[_UnresolvedEntryReservation] | None = None,
    pre_order_entries: list[_PreOrderEntryReservation] | None = None,
) -> list[str]:
    """Reject if adding this trade would push account leverage ratio above cap.

    account_leverage = sum(|pos_notional| * pos_leverage) / equity
    Every exposure row and the equity denominator must belong to the same account
    pool.  Missing account equity is not zero exposure and cannot be guessed from a
    configured starting balance, so it rejects with a stable availability token.
    """
    risk = cfg.risk
    is_live = _is_live(proposal.execution_mode)
    max_ratio = (
        getattr(risk, "max_account_leverage_live", 1.5)
        if is_live else getattr(risk, "max_account_leverage_demo", 3.0)
    )
    try:
        open_positions = list_all_open_positions()
        relevant = _relevant_positions(open_positions, proposal.execution_mode)

        weighted_notional = 0.0
        for p in relevant:
            try:
                qty, _entry_price, price, lev = _validated_position_exposure(p)
            except _InvalidPositionExposure as exc:
                return [_invalid_position_exposure_violation(p, exc.field_category)]
            position_exposure = abs(qty * price) * lev
            weighted_notional += position_exposure
            if not math.isfinite(weighted_notional):
                return [_invalid_position_exposure_violation(p, "account_exposure")]

        if unresolved_entries is None:
            unresolved_entries = _unresolved_entry_reservations()
        for entry in unresolved_entries:
            if not _account_pool_matches(
                entry.execution_mode,
                proposal.execution_mode,
            ):
                continue
            try:
                weighted_notional += _entry_reservation_weighted_notional(entry)
            except (TypeError, ValueError):
                return ["account_leverage_check_error:invalid_entry_reservation"]
            if not math.isfinite(weighted_notional):
                return ["account_leverage_check_error:invalid_entry_reservation"]

        if pre_order_entries is None:
            pre_order_entries = _pre_order_entry_reservations(
                exclude_proposal_id=proposal.id,
            )
        for entry in pre_order_entries:
            if not _account_pool_matches(
                entry.execution_mode,
                proposal.execution_mode,
            ):
                continue
            try:
                weighted_notional += _entry_reservation_weighted_notional(entry)
            except (TypeError, ValueError):
                return ["account_leverage_check_error:invalid_entry_reservation"]
            if not math.isfinite(weighted_notional):
                return ["account_leverage_check_error:invalid_entry_reservation"]

        if proposal.price and proposal.quantity:
            prop_lev = float(proposal.leverage or 1)
            weighted_notional += abs(proposal.price * proposal.quantity) * prop_lev

        equity = _account_equity(proposal.execution_mode, cfg)
        if not equity or equity <= 0:
            return [f"account_equity_unavailable:{proposal.execution_mode}"]

        ratio = weighted_notional / equity
        if ratio > max_ratio:
            return [f"account_leverage_ratio:{ratio:.2f}x>{max_ratio:.1f}x"]
    except Exception as e:
        # Fail CLOSED (§17.2 / EH-1): a leverage check that errors must reject, not
        # silently allow leveraged exposure. Mirrors the lesson/correlation checks.
        logger.warning("_check_account_leverage_ratio failed (safe-reject): %s", e)
        return ["account_leverage_check_error:internal_failure"]
    return []


# ---------------------------------------------------------------------------
# Check #18: Liquidation buffer
# ---------------------------------------------------------------------------

def _check_liquidation_buffer(proposal: TradeProposal, cfg) -> list[str]:
    """Reject if estimated liquidation price is within buffer_pct of entry."""
    if not proposal.leverage or proposal.leverage <= 1:
        return []
    risk = cfg.risk
    buffer_pct = getattr(risk, "min_liquidation_buffer_pct", 5.0) / 100.0

    from aletheia.trading.instruments import calc_liquidation_price
    liq_price = calc_liquidation_price(
        proposal.price, proposal.direction, proposal.leverage, proposal.symbol
    )
    if liq_price is None:
        return []

    gap_pct = abs(proposal.price - liq_price) / proposal.price
    if gap_pct < buffer_pct:
        return [
            f"liquidation_too_close:{gap_pct*100:.1f}%<{buffer_pct*100:.1f}%"
            f"(liq={liq_price:.2f},lev={proposal.leverage}x)"
        ]
    return []


# ---------------------------------------------------------------------------
# Shared helpers for leverage checks
# ---------------------------------------------------------------------------

def _external_account_cache_is_healthy(state: dict | None) -> bool:
    """Whether an exchange equity snapshot is explicit, fresh, and usable.

    ``exchange_sync`` derives freshness from ``age_seconds``.  Some callers also
    attach a producer status; when present it must say healthy/ok.  Old cache
    formats have no explicit status, so the derived ``status_for`` result remains
    the authority instead of making every pre-status snapshot unusable.
    """
    if not isinstance(state, dict):
        return False
    try:
        from aletheia.portfolio.exchange_sync import status_for

        age = float(state.get("age_seconds"))
        if not math.isfinite(age) or age < 0:
            return False
        if status_for(state) != "ok":
            return False
    except Exception:
        return False
    explicit_status = state.get("status")
    if explicit_status is not None and str(explicit_status).strip().lower() not in {
        "healthy",
        "ok",
    }:
        return False
    return True


def _external_account_row_is_healthy(account: dict | None) -> bool:
    """Validate an exact named row, inheriting freshness from its parent view."""
    if not isinstance(account, dict):
        return False
    explicit_status = account.get("status")
    return explicit_status is None or str(explicit_status).strip().lower() in {
        "healthy",
        "ok",
    }


def _account_equity(execution_mode: str, cfg) -> float | None:
    """Return locally cached equity for the account pool used by ``execution_mode``.

    This helper never calls an exchange and never substitutes configured capital for
    an unavailable account snapshot:
    - ``paper_internal``: local paper ledger (cash + unrealized PnL). exchange_sync
      knows nothing about this account — its balance never appears there.
    - live modes: aggregate only cached live-account rows (with the explicit Bitget
      live cache as the legacy single-account fallback).
    - exchange demo modes: exact broker account only.  Bitget keeps its legacy
      per-mode cache; OKX/Binance are named rows in the unified cache.
    """
    if execution_mode == "paper_internal":
        try:
            from aletheia.portfolio.paper_account import get_paper_account
            equity = get_paper_account(event_limit=0).get("equity_usdt")
            if equity is not None and math.isfinite(float(equity)) and float(equity) > 0:
                return float(equity)
        except Exception:
            pass
        return None

    try:
        from aletheia.portfolio.exchange_sync import (
            get_cached_state,
            get_unified_cached_state,
        )

        if _is_live(execution_mode):
            unified = get_unified_cached_state() or {}
            live_equities = []
            if _external_account_cache_is_healthy(unified):
                for account in unified.get("accounts") or []:
                    if not _is_live(str(account.get("broker_id") or "")):
                        continue
                    if not _external_account_row_is_healthy(account):
                        return None
                    value = float(account.get("equity_usdt"))
                    if math.isfinite(value) and value > 0:
                        live_equities.append(value)
            if live_equities:
                return sum(live_equities)
            if execution_mode.startswith("bitget_"):
                cached = get_cached_state("live") or {}
                if not _external_account_cache_is_healthy(cached):
                    return None
                value = cached.get("equity_usdt") or cached.get("total_equity_usdt")
                equity = float(value)
                if math.isfinite(equity) and equity > 0:
                    return equity
            return None

        if execution_mode.startswith("bitget_"):
            cached = get_cached_state("demo") or {}
            if not _external_account_cache_is_healthy(cached):
                return None
            value = cached.get("equity_usdt") or cached.get("total_equity_usdt")
            equity = float(value)
            if math.isfinite(equity) and equity > 0:
                return equity
            return None

        unified = get_unified_cached_state() or {}
        if not _external_account_cache_is_healthy(unified):
            return None
        account = next(
            (
                item
                for item in unified.get("accounts") or []
                if item.get("broker_id") == execution_mode
            ),
            None,
        )
        if not _external_account_row_is_healthy(account):
            return None
        equity = float((account or {}).get("equity_usdt"))
        if math.isfinite(equity) and equity > 0:
            return equity
    except Exception:
        pass
    return None


def _resolve_capital_base(execution_mode: str, policy, cfg) -> tuple[float | None, str | None]:
    """Resolve the one authoritative denominator for percentage risk limits.

    Account equity is the shipped and migrated default.  It is deliberately
    fail-closed: stale or unavailable account state never falls back to the
    stored custom amount without an explicit operator policy change.
    """
    source = getattr(policy.capital, "base_source", "account_equity")
    if source == "custom":
        try:
            value = float(policy.capital.base_usdt)
        except (TypeError, ValueError):
            return None, "invalid_custom_value"
        if math.isfinite(value) and value > 0:
            return value, None
        return None, "invalid_custom_value"
    equity = _account_equity(execution_mode, cfg)
    if equity is None:
        return None, f"account_equity_unavailable:{execution_mode}"
    return equity, None


def _config_active_execution_mode(cfg) -> str:
    """Active execution mode string derived from the system configuration.

    Used by monitoring / dashboard paths that evaluate the full system rather
    than a specific proposal.  Maps ``exchange.mode == 'live'`` to the
    conventional ``{broker}_live_limited`` string so ``_is_live()`` / leverage
    fallbacks all work correctly.
    """
    if cfg.exchange.mode == "live":
        broker = (getattr(cfg.exchange, "name", None) or "bitget").lower()
        return f"{broker}_live_limited"
    return getattr(cfg.exchange, "paper_execution_mode", None) or "paper_internal"


def _account_pool_matches(existing_mode: str, target_mode: str) -> bool:
    """Whether two exposure rows draw on the same leverage-equity denominator."""
    if target_mode == "paper_internal":
        return existing_mode == "paper_internal"
    if _is_live(target_mode):
        return _is_live(existing_mode)
    return existing_mode == target_mode


def _relevant_positions(open_positions: list, execution_mode: str) -> list:
    """Return the subset of open positions that belong to the same account pool.

    - paper_internal: isolated local ledger — only paper_internal positions count.
    - live modes: all live positions aggregate (real-money account, any broker).
    - exchange demo modes: each broker is a separate account — exact mode match.
    """
    return [
        position
        for position in open_positions
        if _account_pool_matches(
            str(position.get("execution_mode") or ""),
            execution_mode,
        )
    ]


def _entry_reservation_weighted_notional(
    entry: _UnresolvedEntryReservation | _PreOrderEntryReservation,
) -> float:
    """Return one reservation's leverage-weighted exposure or reject bad authority.

    An unresolved Order can legitimately have zero remaining quantity only after an
    open Position represents its full persisted Fill.  A pre-Order reservation must
    always retain a strictly positive quantity until it is released.
    """
    if entry.invalid_reasons:
        raise ValueError("invalid entry reservation authority")
    quantity = float(entry.reserved_quantity)
    price = float(entry.price)
    leverage = float(entry.leverage)
    zero_is_represented = (
        isinstance(entry, _UnresolvedEntryReservation)
        and entry.has_open_position
        and quantity == 0.0
    )
    if (
        not math.isfinite(quantity)
        or (quantity <= 0 and not zero_is_represented)
        or not math.isfinite(price)
        or price <= 0
        or not math.isfinite(leverage)
        or leverage <= 0
    ):
        raise ValueError("invalid entry reservation exposure")
    weighted_notional = abs(quantity * price) * leverage
    if not math.isfinite(weighted_notional):
        raise ValueError("invalid entry reservation exposure")
    return weighted_notional


def _unavailable_account_leverage_result(
    *,
    cap: float,
    reason: str,
    positions: list[dict] | None = None,
    invalid_position: dict | None = None,
) -> dict:
    """Stable public shape for a leverage metric that cannot be computed honestly."""
    result = {
        "status": "unavailable",
        "reason": reason,
        "ratio": None,
        "cap": cap,
        "violation": None,
        "equity": None,
        "positions": list(positions or []),
    }
    if invalid_position is not None:
        result["invalid_position"] = invalid_position
    return result


def check_account_leverage(cfg=None, execution_mode: str | None = None) -> dict:
    """Public API: compute current account leverage ratio (for dashboard/monitoring).

    ``execution_mode`` selects which account pool to evaluate.  When omitted,
    the active mode is derived from ``cfg.exchange.paper_execution_mode`` (or the
    live mode string when ``cfg.exchange.mode == "live"``).  Pass it explicitly
    when the caller knows which pool it cares about (e.g. tests, per-account dashboards).

    Monitoring uses the same exposure-integrity rule as the admission check.  A
    corrupt relevant Position or unavailable equity makes the metric unavailable;
    it must never be rendered as a safe ``violation=False`` result.  The sole NULL
    leverage exception is a spot Position, whose structural leverage is exactly 1x.
    """
    if cfg is None:
        cfg = load_config()
    active_mode = execution_mode if execution_mode is not None else _config_active_execution_mode(cfg)
    is_live = _is_live(active_mode)
    max_ratio = (
        getattr(cfg.risk, "max_account_leverage_live", 1.5)
        if is_live else getattr(cfg.risk, "max_account_leverage_demo", 3.0)
    )
    try:
        open_positions = list_all_open_positions()
    except Exception:
        return _unavailable_account_leverage_result(
            cap=max_ratio,
            reason="position_data_unavailable",
        )
    relevant = _relevant_positions(open_positions, active_mode)
    weighted_notional = 0.0
    positions_detail = []
    for p in relevant:
        try:
            qty, _entry_price, price, lev = _validated_position_exposure(p)
        except _InvalidPositionExposure as exc:
            return _unavailable_account_leverage_result(
                cap=max_ratio,
                reason="invalid_position_exposure",
                invalid_position={
                    "field_category": exc.field_category,
                    "position_id": _safe_position_id(p),
                },
            )
        notional = abs(qty * price)
        position_exposure = notional * lev
        if not math.isfinite(notional) or not math.isfinite(position_exposure):
            return _unavailable_account_leverage_result(
                cap=max_ratio,
                reason="invalid_position_exposure",
                invalid_position={
                    "field_category": "position_exposure",
                    "position_id": _safe_position_id(p),
                },
            )
        weighted_notional += position_exposure
        if not math.isfinite(weighted_notional):
            return _unavailable_account_leverage_result(
                cap=max_ratio,
                reason="invalid_position_exposure",
                invalid_position={
                    "field_category": "account_exposure",
                    "position_id": _safe_position_id(p),
                },
            )
        positions_detail.append({
            "symbol": p.get("symbol"),
            "notional": notional,
            "leverage": lev,
        })

    try:
        unresolved_entries = _unresolved_entry_reservations()
        pre_order_entries = _pre_order_entry_reservations()
    except Exception:
        return _unavailable_account_leverage_result(
            cap=max_ratio,
            reason="entry_reservation_data_unavailable",
            positions=positions_detail,
        )
    for entry in (*unresolved_entries, *pre_order_entries):
        if not _account_pool_matches(entry.execution_mode, active_mode):
            continue
        try:
            weighted_notional += _entry_reservation_weighted_notional(entry)
        except (TypeError, ValueError):
            return _unavailable_account_leverage_result(
                cap=max_ratio,
                reason="invalid_entry_reservation",
                positions=positions_detail,
            )
        if not math.isfinite(weighted_notional):
            return _unavailable_account_leverage_result(
                cap=max_ratio,
                reason="invalid_entry_reservation",
                positions=positions_detail,
            )

    try:
        equity = _account_equity(active_mode, cfg)
        equity_value = float(equity)
    except Exception:
        equity_value = math.nan
    if not math.isfinite(equity_value) or equity_value <= 0:
        return _unavailable_account_leverage_result(
            cap=max_ratio,
            reason=f"account_equity_unavailable:{active_mode}",
            positions=positions_detail,
        )
    ratio = weighted_notional / equity_value
    return {
        "status": "available",
        "reason": None,
        "ratio": ratio,
        "cap": max_ratio,
        "violation": ratio > max_ratio,
        "equity": equity_value,
        "positions": positions_detail,
    }
