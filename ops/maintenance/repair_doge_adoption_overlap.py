"""Owner-approved #3111 recovery for one proven DOGE adoption/native overlap.

Dry run: uv run python -m ops.maintenance.repair_doge_adoption_overlap --snapshot PATH
Apply:   uv run python -m ops.maintenance.repair_doge_adoption_overlap --apply PATH

No exchange mutations; no generic symbol/position recovery entry point.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from aletheia.storage import repositories as repo
from aletheia.storage.models import AuditEvent
from aletheia.uta.compat import scope_from_local
from aletheia.uta.manager import query_uta
from aletheia.uta.models import FillQuery, OrderQuery, PositionQuery, UTAAccountIdentity

POSITION = "56c173ee3ed0"
NATIVE_ORDER = "221047c90a12"
ADOPTED_ORDER = "a7f33d91338f"
NATIVE_PROPOSAL = "144507b184bf"
ADOPTED_PROPOSAL = "77e3b32b3608"
AUDIT = "3111-doge-adoption-overlap-v1"
START = "2026-09-11T00:00:00+00:00"


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def snapshot() -> dict:
    """Capture economic/authority state; live mark and uPnL are read projections."""
    db = repo.get_db()
    def rows(sql, args=()):
        return [dict(row) for row in db.execute(sql, args).fetchall()]
    position = rows("SELECT * FROM positions WHERE id=?", (POSITION,))[0]
    for field in ("current_price", "unrealized_pnl", "unrealized_pnl_source"):
        position.pop(field, None)
    return {
        "position": position,
        "orders": rows("SELECT * FROM orders WHERE id IN (?,?) OR position_id=? ORDER BY id",
                       (NATIVE_ORDER, ADOPTED_ORDER, POSITION)),
        "proposals": rows("SELECT * FROM trade_proposals WHERE id IN (?,?) ORDER BY id",
                          (NATIVE_PROPOSAL, ADOPTED_PROPOSAL)),
        "risk": rows("SELECT * FROM risk_decisions WHERE id IN "
                     "(SELECT risk_decision_id FROM orders WHERE id IN (?,?)) ORDER BY id", (NATIVE_ORDER, ADOPTED_ORDER)),
        "fills": rows("SELECT * FROM fills WHERE order_id IN (?,?) ORDER BY id", (NATIVE_ORDER, ADOPTED_ORDER)),
        "other_open_positions": rows("SELECT id FROM positions WHERE symbol='DOGE/USDT' "
            "AND market_type='usdt_futures' AND direction='long' AND execution_mode='bitget_demo' "
            "AND status='open' AND id<>? ORDER BY id", (POSITION,)),
        "other_entry_orders": rows("SELECT id,status FROM orders WHERE symbol='DOGE/USDT' "
            "AND market_type='usdt_futures' AND direction='long' AND execution_mode='bitget_demo' "
            "AND created_at >= (SELECT created_at FROM orders WHERE id=?) "
            "AND id NOT IN (?,?) ORDER BY id", (NATIVE_ORDER, NATIVE_ORDER, ADOPTED_ORDER)),
        "closes": rows("SELECT * FROM position_close_events WHERE position_id=? ORDER BY id", (POSITION,)),
        "exit_versions": rows("SELECT * FROM position_exit_plan_versions WHERE position_id=? ORDER BY version", (POSITION,)),
        "protection": rows("SELECT * FROM position_protection_state WHERE position_id=?", (POSITION,)),
    }


async def private_history(before: dict) -> dict:
    order = next(row for row in before["orders"] if row["id"] == NATIVE_ORDER)
    identity = UTAAccountIdentity.model_validate_json(order["uta_account_identity_json"])
    scope = scope_from_local("DOGE/USDT", "usdt_futures", start_at=START, end_at=datetime.now(timezone.utc))
    reports = {}
    for name, method, query in (
        ("fills", "get_fills", FillQuery(scope=scope)),
        ("order", "get_order", OrderQuery(scope=scope, venue_order_id=order["exchange_order_id"], client_order_id=order["client_order_id"])),
        ("positions", "get_positions", PositionQuery(scope=scope)),
    ):
        report = await query_uta("bitget_demo", method, query, expected_account=identity)
        reports[name] = report.model_dump(mode="json")
    return reports


def correction(before: dict, evidence: dict) -> dict:
    """Require the complete one-fill exposure proof, not just equal quantities."""
    def require(condition, reason):
        if not condition:
            raise ValueError(reason)
    require(not before["other_open_positions"] and not before["other_entry_orders"], "competing_local_exposure")
    position = before["position"]
    orders = {row["id"]: row for row in before["orders"]}
    proposals = {row["id"]: row for row in before["proposals"]}
    require(set(orders) == {NATIVE_ORDER, ADOPTED_ORDER}, "entry_or_close_order_changed")
    native, adopted = orders[NATIVE_ORDER], orders[ADOPTED_ORDER]
    for row in (position, native, adopted, *proposals.values()):
        require(tuple(row[key] for key in ("symbol", "market_type", "direction", "execution_mode"))
                == ("DOGE/USDT", "usdt_futures", "long", "bitget_demo"), "local_identity_mismatch")
    identity = json.loads(native["uta_account_identity_json"])
    require(identity["execution_mode"] == "bitget_demo" and identity["environment"] == "demo"
            and identity["profile"] == "classic_v2", "wrong_account")
    for report in evidence.values():
        require(report.get("completeness") == "complete" and report.get("identity") == identity, "incomplete_or_mismatched_private_history")
        scope = report["scope"]
        require(datetime.fromisoformat(scope["start_at"].replace("Z", "+00:00")) == datetime.fromisoformat(START), "history_interval_changed")
        require(scope["end_at"] is not None and scope.get("next_cursor") is None, "history_interval_incomplete")
    fills = evidence["fills"]["fills"]
    remote_positions = evidence["positions"]["positions"]
    require(len(fills) == 1 and len(remote_positions) == 1, "independent_or_ambiguous_exposure")
    fill, remote_position = fills[0], remote_positions[0]
    remote_order = evidence["order"]["found"]
    require(remote_order is not None, "private_order_missing")
    for row in (fill, remote_position, remote_order):
        require(row["identity"] == identity and row["instrument"]["canonical_symbol"] == "DOGE/USDT"
                and row["instrument"]["market_type"] == "usdt_futures", "private_identity_mismatch")
    require(fill["venue_order_id"] == native["exchange_order_id"] == remote_order["venue_order_id"]
            and remote_order["client_order_id"] == native["client_order_id"], "order_identity_mismatch")
    require(fill["side"] == "buy" and fill["trade_intent"] == "open" and remote_position["side"] == "long"
            and remote_order["state"] == "filled" and remote_order["reduce_only"] is False, "not_exact_entry")
    quantity, price, fee = (Decimal(str(fill[key])) for key in ("quantity", "price", "fee"))
    require(quantity > 0 and price > 0 and fee >= 0 and fill["fee_currency"] == "USDT", "invalid_private_economics")
    require(all(Decimal(str(value)) == quantity for value in
                (native["quantity"], adopted["quantity"], remote_position["quantity"], remote_order["filled_quantity"], remote_order["original_quantity"])), "quantity_mismatch")
    require(Decimal(str(position["quantity"])) == quantity * 2 and Decimal(str(position["entry_price"])) == price
            and Decimal(str(adopted["price"])) == price and Decimal(str(remote_position["entry_price"])) == price, "overlap_not_proven")
    end = datetime.fromisoformat(evidence["fills"]["scope"]["end_at"].replace("Z", "+00:00"))
    require(all(report["scope"]["end_at"] == evidence["fills"]["scope"]["end_at"]
                for report in evidence.values()), "incoherent_history_interval")
    for row in (remote_order, remote_position):
        require(row.get("source_at") is not None
                and datetime.fromisoformat(row["source_at"].replace("Z", "+00:00")) <= end,
                "private_state_changed_after_history")
    opened = datetime.fromisoformat(remote_position["opened_at"].replace("Z", "+00:00"))
    executed = datetime.fromisoformat(fill["executed_at"].replace("Z", "+00:00"))
    require(remote_position["opened_at_provenance"] == "exchange_reported"
            and opened == datetime.fromisoformat(position["opened_at"])
            and 0 <= (opened - executed).total_seconds() < 1, "opening_identity_mismatch")
    require(position["source"] == "adopted" and position["status"] == "open"
            and position["proposal_id"] == ADOPTED_PROPOSAL and position["order_id"] == ADOPTED_ORDER
            and not position["entry_fill_verified"] and not position["pending_close_order_id"]
            and not before["closes"], "position_state_changed")
    require(native["source"] == "native" and adopted["source"] == "adopted"
            and native["status"] == adopted["status"] == "filled"
            and native["position_id"] == adopted["position_id"] == POSITION
            and native["proposal_id"] == NATIVE_PROPOSAL and adopted["proposal_id"] == ADOPTED_PROPOSAL
            and not adopted["exchange_order_id"], "local_ownership_mismatch")
    local = before["fills"]
    require(len(local) == 1 and local[0]["id"] == "private-fill:bitget_demo:" + fill["venue_fill_id"]
            and local[0]["order_id"] == NATIVE_ORDER and local[0]["fee_source"] == "reconciled"
            and local[0]["fee_currency"] == "USDT"
            and Decimal(str(local[0]["fill_quantity"])) == quantity
            and Decimal(str(local[0]["fill_price"])) == price
            and Decimal(str(local[0]["fee"])) == fee, "durable_fill_mismatch")
    proposal = proposals[NATIVE_PROPOSAL]
    require(proposal["source"] == "native" and proposals[ADOPTED_PROPOSAL]["source"] == "adopted", "proposal_ownership_mismatch")
    require(proposal["status"] == "executed", "native_proposal_not_executed")
    decision = next((row for row in before["risk"] if row["id"] == native["risk_decision_id"]), None)
    repo._validate_native_entry_order_snapshot(native, proposal, decision)
    from aletheia.execution.exchange_materialization import _attribution_fields
    return {
        "quantity": float(quantity), "entry_price": float(price), "entry_fee": float(fee),
        "slippage_cost": abs(float(price) - float(native["price"])) * float(quantity),
        "source": "native", "entry_price_source": "broker_fill", "entry_fill_verified": 1,
        "proposal_id": NATIVE_PROPOSAL, "order_id": NATIVE_ORDER,
        "strategy_id": proposal.get("strategy_id"), "opportunity_id": proposal.get("opportunity_id"),
        **_attribution_fields(proposal),
    }


def apply(before: dict, evidence: dict) -> dict:
    changes = correction(before, evidence)
    with repo.transaction(immediate=True):
        db = repo.get_db()
        if db.execute("SELECT 1 FROM audit_events WHERE id=?", (AUDIT,)).fetchone():
            return {"status": "already_applied"}
        if snapshot() != before:
            raise ValueError("current_state_changed")
        assignments = ",".join(f"{key}=?" for key in changes)
        db.execute(f"UPDATE positions SET {assignments} WHERE id=?", (*changes.values(), POSITION))
        repo.insert_audit_event_outbox(AuditEvent(
            id=AUDIT, actor="owner_authorized_recovery", source="repair_doge_adoption_overlap",
            event_type="position_adoption_overlap_corrected", entity_type="position", entity_id=POSITION,
            before_state=_json(before), after_state=_json({"changes": changes, "private_evidence": evidence,
                "before_sha256": hashlib.sha256(_json(before).encode()).hexdigest()}),
            message="#3111 bounded recovery; immutable Proposal/Order/Fill history preserved; exit authority unchanged",
        ))
    return {"status": "applied", "changes": changes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--snapshot", type=Path)
    group.add_argument("--apply", type=Path)
    args = parser.parse_args()
    if args.apply and repo.get_db().execute("SELECT 1 FROM audit_events WHERE id=?", (AUDIT,)).fetchone():
        # Retry only the committed audit mirror, even if trading state has moved on.
        mirrored = repo.reconcile_audit_event_outbox(("position_adoption_overlap_corrected",))
        print(_json({"status": "already_applied", "audit_mirrored": mirrored}))
        return
    if args.apply:
        packet = json.loads(args.apply.read_text())
        before = packet["before"]
    else:
        with repo.transaction():
            before = snapshot()
    evidence = asyncio.run(private_history(before))
    changes = correction(before, evidence)
    if args.snapshot:
        with repo.transaction():
            if snapshot() != before:
                raise ValueError("current_state_changed")
        with args.snapshot.open("x") as target:
            target.write(_json({"before": before, "evidence": evidence, "changes": changes}))
        print(_json({"status": "dry_run", "changes": changes}))
    else:
        result = apply(before, evidence)
        result["audit_mirrored"] = repo.reconcile_audit_event_outbox(("position_adoption_overlap_corrected",))
        print(_json(result))


if __name__ == "__main__":
    main()
