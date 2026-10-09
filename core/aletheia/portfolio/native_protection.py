"""Verify-or-close for entry-attached exchange-native protection (#3848).

execution_contract.md §4: a Bitget futures entry carries its approved stop as a
venue preset, and the acknowledgement returns no id for it. This scan reads the
venue's open strategy orders back, persists only confirmed ids, and closes a
Position whose complete read still shows no venue stop past the grace window.
An incomplete, unavailable or ambiguous read writes nothing and closes nothing.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger("aletheia.portfolio.native_protection")

CLOSE_REASON = "protection_absent_closed"


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
    from aletheia.execution.stops import classify_native_protection
    from aletheia.portfolio.position_manager import close_position_routed
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import AuditEvent
    from aletheia.uta.identity import IdentityRelation, identity_relation
    from aletheia.uta.manager import (
        configured_identity, create_native_protection_reader,
        native_protection_execution_modes,
    )
    from aletheia.uta.models import NativeProtectionOpenOrders

    summary: dict = {"verified": [], "closed": [], "errors": []}
    if not load_config().trading.exchange_tpsl_enabled:
        return summary
    modes = native_protection_execution_modes()
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
    absent: list[dict] = []
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
            absent.append(position)
        elif isinstance(detail, str):
            summary["errors"].append(_error(position, detail))
        else:
            repo.update_open_position_exit_plan(
                position["id"], exchange_tpsl_order_id=json.dumps(detail, sort_keys=True),
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

    for position in absent:
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
                "close_reason": CLOSE_REASON,
            }, sort_keys=True, separators=(",", ":")),
            message="A complete venue read shows no stop-loss past the grace window; closing.",
        ))
        try:
            close_position_routed(position["id"], CLOSE_REASON, decision_source="native_protection_check")
            summary["closed"].append(position["id"])
        except Exception as exc:
            logger.warning("protection_absent close failed for %s (%s)", position["id"], type(exc).__name__)
            summary["errors"].append(_error(position, type(exc).__name__))
    return summary
