"""Shared protective-stop helpers for execution brokers.

The three real-exchange brokers (okx/binance/bitget) are structural clones of one
another. A protective stop must (a) be persisted on the Position row at open and
(b) tighten — never loosen — when averaging into an existing position. Keeping that
logic here gives all brokers ONE source of truth so the historical "stop dropped on
the floor at open" bug cannot re-appear via copy-paste.
"""

from __future__ import annotations

from aletheia.config.exchanges import ENTRY_ROUTE_POLICIES


def merge_protective_stop(
    direction: str,
    new_stop: float | None,
    old_stop: float | None,
) -> float | None:
    """Return the more protective of two stops when averaging into a position.

    For a long, the higher stop is tighter (closer to price → less downside);
    for a short, the lower stop is tighter. If either side is None, prefer the
    one that exists (a stop beats no stop). Returns None only when both are None.
    """
    if new_stop is None:
        return old_stop
    if old_stop is None:
        return new_stop
    if direction in ("long", "buy"):
        return max(new_stop, old_stop)
    return min(new_stop, old_stop)


# #3848 PR-A item 4: with the flag unset, native protection follows the route's
# `native_tpsl_default` in `config.exchanges.ENTRY_ROUTE_POLICIES`. An explicit
# true/false applies to every execution mode.
NATIVE_TPSL_DEFAULT_MODES = frozenset(
    mode for mode, policy in ENTRY_ROUTE_POLICIES.items() if policy.native_tpsl_default
)


def native_tpsl_enabled(trading, execution_mode: str | None) -> bool:
    explicit = trading.exchange_tpsl_enabled
    if explicit is not None:
        return bool(explicit)
    return execution_mode in NATIVE_TPSL_DEFAULT_MODES


def replace_exchange_protective_stop(position_id: str, new_stop_price: float) -> bool:
    """Broker-agnostic dispatch: amend the exchange-side protective SL in place for a
    position whose stop just tightened. Returns True only after the venue
    acknowledged the amend; otherwise returns False (#3848 PR-B).

    #139 fix: the trail loop previously only reached Bitget — okx_demo and
    binance_testnet positions could see their DB stop_price tighten while the
    exchange bracket stayed at the OLD level, so a gateway outage would let the
    original (looser) stop close them. The trail feature is gated behind
    `native_tpsl_enabled`; where it is on, all supported brokers must honour it
    consistently. Non-fatal.

    Gating:
      1. native_tpsl_enabled(cfg.trading, execution_mode) True
      2. execution_mode is a DEMO/TESTNET mode (never an autonomous live order).
    """
    from aletheia.config.exchanges import execution_mode_environment
    from aletheia.config.loader import load_config
    from aletheia.storage import repositories as _repo

    row = _repo._select_one("positions", position_id)
    if row is None:
        return False
    cfg = load_config()
    mode = row.get("execution_mode") or ""
    if not native_tpsl_enabled(cfg.trading, mode):
        return False

    environment = execution_mode_environment(mode)
    if environment == "demo":
        from aletheia.execution.gateway import replace_exchange_protective_stop
        return replace_exchange_protective_stop(position_id, new_stop_price)
    elif environment == "testnet":
        # The currently supported testnet client exposes only place_order (no
        # place_stop / cancel_algo yet). The DB stop_price is still tightened by
        # the caller — the exchange bracket just can't be replaced until the
        # client grows a stop-order surface. Log so this doesn't fail silently
        # if a user opts into exchange_tpsl_enabled on a testnet account.
        import logging as _log
        _log.getLogger("aletheia.execution.stops").warning(
            "Testnet trailed-stop exchange replacement not implemented "
            "(position %s); DB stop_price tightened but exchange bracket unchanged.",
            position_id,
        )
    # Live modes (okx_live_limited / binance_live_limited): deliberately no-op —
    # placing an exchange order from an autonomous trail update is forbidden.
    return False


# Two tpsl_fill_check cycles (90s): time for an accepted entry's attached preset
# to be readable before the bookkeeping read-back audits it as not found (#3848).
# The read-back never closes a Position (owner D1, 2026-10-09).
PROTECTION_GRACE_SECONDS = 180
# Marks ids read back from an entry preset; `replace_protective_stop` amends that
# strategy order in place (modify-strategy-order, #3848 PR-B).
PRESET_ORIGIN = "entry_preset"


def classify_native_protection(position: dict, legs, *, now) -> tuple[str, dict[str, str] | str]:
    """Judge one Position against a complete read of its symbol's venue SL/TP legs.

    Returns ``("verified", {"sl": id[, "tp": id]})``, ``("unverified", reason)`` when
    the venue state is real but not provably this Position's full stop, or
    ``("absent", reason)`` only when no venue stop exists past the grace window
    (audited by the bookkeeping read-back; never a close).
    A take-profit alone is not protection.
    """
    from datetime import datetime, timezone

    side = "long" if position["direction"] in {"long", "buy"} else "short"
    mine = [leg for leg in legs if leg.position_side == side]
    stops = [leg for leg in mine if leg.kind == "sl"]
    stop_ids = {leg.plan_order_id for leg in stops}
    take_ids = {leg.plan_order_id for leg in mine if leg.kind == "tp"}
    if len(stop_ids) > 1:
        return "unverified", "native_protection_ambiguous"
    if stop_ids:
        quantity = float(position["quantity"])
        if any(leg.quantity is not None and float(leg.quantity) < quantity * (1 - 1e-9) for leg in stops):
            return "unverified", "native_protection_partial"
        ids = {"sl": next(iter(stop_ids))}
        if len(take_ids) == 1:
            ids["tp"] = next(iter(take_ids))
        return "verified", ids
    opened = datetime.fromisoformat(str(position["opened_at"]).replace("Z", "+00:00"))
    if opened.tzinfo is None:
        opened = opened.replace(tzinfo=timezone.utc)
    if (now - opened).total_seconds() < PROTECTION_GRACE_SECONDS:
        return "unverified", "native_protection_pending"
    return "absent", "native_protection_absent"
