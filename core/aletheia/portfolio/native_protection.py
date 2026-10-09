"""Bookkeeping read-back of entry-attached exchange-native protection (#3848).

execution_contract.md §4 (owner D1, 2026-10-09): the entry order's own venue
acknowledgement confirms its attached SL/TP, because the pre-send guard refuses
to send a body that does not carry them. The ACK returns no id for the presets,
so this scan reads the venue's open strategy orders back only to persist the
ids (needed for amend and fill recognition). When it cannot find them it writes
an audit and reports an error; it never closes a Position.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger("aletheia.portfolio.native_protection")


def _stored_ids(position: dict) -> dict | None:
    try:
        ids = json.loads(position.get("exchange_tpsl_order_id") or "{}")
    except (TypeError, ValueError):
        return None
    return ids if isinstance(ids, dict) else None


def _error(position: dict, error_type: str) -> dict:
    return {
        "execution_mode": position.get("execution_mode"),
        "position_id": position.get("id"),
        "plan_order_id": None,
        "error_type": error_type,
    }


def verify_native_protection() -> dict:
    from aletheia.config.loader import load_config
    from aletheia.execution.close_convergence import has_unresolved_close_claim
    from aletheia.execution.coordinator import _position_account
    from aletheia.execution.stops import PRESET_ORIGIN, classify_native_protection, native_tpsl_enabled
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import AuditEvent
    from aletheia.uta.identity import IdentityRelation, identity_relation
    from aletheia.uta.manager import (
        configured_identity, create_native_protection_reader,
        native_protection_execution_modes,
    )
    from aletheia.uta.models import NativeProtectionOpenOrders

    summary: dict = {"verified": [], "errors": []}
    trading = load_config().trading
    modes = [mode for mode in native_protection_execution_modes() if native_tpsl_enabled(trading, mode)]
    candidates = []
    for position in repo.list_all_open_positions():
        ids = _stored_ids(position)
        if (
            position.get("execution_mode") in modes
            and position.get("market_type") == "usdt_futures"
            and position.get("stop_price") is not None
            and not has_unresolved_close_claim(position)
            and ids is not None
            and not ids.get("sl")
        ):
            candidates.append(position)
    if not candidates:
        return summary

    async def _read_mode(mode: str, symbols: set[str]) -> dict[str, object]:
        identity = configured_identity(mode)
        if identity is None:
            raise RuntimeError("uta_account_identity_unavailable")
        reader = create_native_protection_reader(mode, expected_account=identity)
        reads: dict[str, object] = {}
        try:
            for symbol in sorted(symbols):
                try:
                    snapshot = await reader.get_open_protection(symbol=symbol, market_type="usdt_futures")
                    if identity_relation(identity, snapshot.identity) is not IdentityRelation.SAME:
                        raise ValueError("uta_account_identity_mismatch")
                    reads[symbol] = snapshot
                except Exception as exc:
                    reads[symbol] = exc
        finally:
            await reader.close()
        return reads

    async def _read_all() -> dict[tuple[str, str], object]:
        out: dict[tuple[str, str], object] = {}
        for mode in sorted({p["execution_mode"] for p in candidates}):
            symbols = {p["symbol"] for p in candidates if p["execution_mode"] == mode}
            try:
                reads = await _read_mode(mode, symbols)
            except Exception as exc:
                reads = dict.fromkeys(symbols, exc)
            out.update({(mode, symbol): read for symbol, read in reads.items()})
        return out

    reads = asyncio.run(_read_all())
    now = datetime.now(timezone.utc)
    for position in candidates:
        snapshot = reads[(position["execution_mode"], position["symbol"])]
        if not isinstance(snapshot, NativeProtectionOpenOrders):
            summary["errors"].append(_error(position, type(snapshot).__name__))
            continue
        try:
            same_account = identity_relation(_position_account(position), snapshot.identity) is IdentityRelation.SAME
        except ValueError:
            same_account = False
        if not same_account:
            summary["errors"].append(_error(position, "uta_account_identity_mismatch"))
            continue
        try:
            state, detail = classify_native_protection(position, snapshot.legs, now=now)
        except Exception as exc:
            summary["errors"].append(_error(position, type(exc).__name__))
            continue
        if state == "absent":
            repo.insert_audit_event_once(AuditEvent(
                id=f"native-protection-absent-{position['id']}",
                actor="system",
                source="native_protection_check",
                event_type="native_protection_absent",
                entity_type="position",
                entity_id=position["id"],
                after_state=json.dumps({
                    "stop_price": position.get("stop_price"),
                    "opened_at": position.get("opened_at"),
                }, sort_keys=True, separators=(",", ":")),
                message="A complete venue read shows no stop-loss for this Position; ids not recorded. No close is requested.",
            ))
            summary["errors"].append(_error(position, "native_protection_absent"))
        elif isinstance(detail, str):
            summary["errors"].append(_error(position, detail))
        else:
            repo.update_open_position_exit_plan(
                position["id"],
                exchange_tpsl_order_id=json.dumps({**detail, "origin": PRESET_ORIGIN}, sort_keys=True),
            )
            triggers = {leg.kind: str(leg.trigger_price) for leg in snapshot.legs if leg.plan_order_id in detail.values()}
            repo.insert_audit_event_once(AuditEvent(
                id=f"native-protection-verified-{position['id']}-{detail['sl']}",
                actor="system",
                source="native_protection_check",
                event_type="native_protection_verified",
                entity_type="position",
                entity_id=position["id"],
                after_state=json.dumps({
                    "exchange_tpsl_order_id": detail,
                    "venue_trigger_prices": triggers,
                    "stop_price": position.get("stop_price"),
                    "take_profit_price": position.get("take_profit_price"),
                    "quantity": position.get("quantity"),
                }, sort_keys=True, separators=(",", ":")),
                message="Venue-side protection read back from the venue and persisted.",
            ))
            summary["verified"].append(position["id"])

    return summary
