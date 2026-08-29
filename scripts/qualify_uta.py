#!/usr/bin/env python3
"""Secret-safe credential qualification for one UTA demo/testnet profile."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
from uuid import uuid4

from aletheia.config.loader import get_data_dir, load_config
from aletheia.execution.order_constraints import (
    get_order_constraints,
    normalize_quantity,
)
from aletheia.update.git_ops import current_commit, is_worktree_dirty
from aletheia.uta.compat import instrument_from_local
from aletheia.uta.contract import UTA_CONTRACT_VERSION
from aletheia.uta.manager import (
    _legacy_brokers,
    _query_legacy,
    _shadow_differences,
    create_uta_adapter,
)
from aletheia.uta.models import (
    CancelOrderCommand,
    CapabilitySupport,
    CommandEvidence,
    FillQuery,
    ModifyOrderCommand,
    OrderLifecycle,
    OrderQuery,
    QueryScope,
    ReadCompleteness,
    SubmitOrderCommand,
    VerifiedExitQuery,
    VerifiedExitState,
)


def _artifact_path(mode: str, target: Path | None) -> Path:
    return target or get_data_dir() / "qualifications" / f"uta-{mode}.json"


def _write_artifact(path: Path, artifact: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(artifact, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


async def _wait_fills(adapter, command, venue_order_id: str, start: datetime):
    query = FillQuery(
        scope=QueryScope(
            instrument=command.instrument,
            start_at=start,
            end_at=datetime.now(timezone.utc) + timedelta(minutes=2),
        ),
        client_order_id=command.client_order_id,
        venue_order_id=venue_order_id,
    )
    for _ in range(20):
        report = await adapter.get_fills(query)
        if report.completeness is ReadCompleteness.COMPLETE and report.fills:
            return report
        await asyncio.sleep(1)
    return report


async def _wait_order(adapter, instrument, venue_order_id: str, state):
    query = OrderQuery(scope=QueryScope(instrument=instrument), venue_order_id=venue_order_id)
    for _ in range(10):
        report = await adapter.get_order(query)
        if (
            report.completeness is ReadCompleteness.COMPLETE
            and report.found is not None
            and report.found.state is state
        ):
            return report
        await asyncio.sleep(1)
    return report


def _command(
    adapter,
    instrument,
    *,
    purpose,
    side,
    quantity,
    client_order_id,
    reduce_only=False,
    leverage=None,
):
    return SubmitOrderCommand(
        command_id=str(uuid4()),
        expected_account=adapter.identity,
        instrument=instrument,
        purpose=purpose,
        client_order_id=client_order_id,
        side=side,
        quantity=Decimal(str(quantity)),
        order_type="market",
        reduce_only=reduce_only,
        leverage=Decimal(leverage) if leverage is not None else None,
    )


async def _qualify(args) -> dict:
    if args.execution_mode not in {"binance_testnet", "okx_demo"}:
        raise ValueError("qualification supports binance_testnet and okx_demo only")
    confirmed = getattr(args, "confirm_testnet_order", False) or getattr(args, "confirm_demo_order", False)
    if args.execution_probe and not confirmed:
        raise ValueError("--execution-probe requires explicit order confirmation")
    if is_worktree_dirty():
        raise RuntimeError("qualification_worktree_dirty")
    cfg = load_config()
    adapter = create_uta_adapter(args.execution_mode, cfg)
    identity = adapter.identity
    artifact = {
        "qualified": False,
        "execution_mode": args.execution_mode,
        "environment": identity.environment,
        "code_sha": current_commit(),
        "uta_contract_version": UTA_CONTRACT_VERSION,
        "adapter": identity.adapter_implementation,
        "adapter_version": identity.adapter_version,
        "profile": identity.profile,
        "driver_version": identity.ccxt_version,
        "account_fingerprint": identity.account_fingerprint,
        "credential_generation": identity.credential_generation,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "unresolved_items": [],
    }
    try:
        await adapter.connect()
        authority = await adapter.probe_account_authority()
        account = await adapter.get_account()
        positions = await adapter.get_positions()
        capabilities = adapter.capabilities
        artifact["capabilities"] = capabilities.model_dump(mode="json")
        artifact["account_authority"] = {
            "completeness": authority.completeness.value,
            "private_readable": authority.authenticated_private_readable,
            "trading_permission": authority.trading_permission.value,
            "account_fingerprint": authority.account_fingerprint,
        }
        legacy = _legacy_brokers((args.execution_mode,)).get(args.execution_mode)
        if legacy is None:
            raise RuntimeError("legacy parity adapter unavailable")
        legacy_account, legacy_positions = await _query_legacy(legacy)
        differences = _shadow_differences(legacy_account, legacy_positions, account, positions)
        artifact["read_parity"] = {
            "account_completeness": account.completeness.value,
            "position_completeness": positions.completeness.value,
            "differences": differences,
        }
        read_ok = (
            account.completeness is ReadCompleteness.COMPLETE
            and positions.completeness is ReadCompleteness.COMPLETE
            and not differences
            and authority.completeness is ReadCompleteness.COMPLETE
            and authority.authenticated_private_readable
            and authority.trading_permission is CapabilitySupport.SUPPORTED
        )
        if not args.execution_probe:
            artifact["unresolved_items"].append("execution_probe_not_requested")
            artifact["read_parity_passed"] = read_ok
            return artifact

        instrument = instrument_from_local(args.symbol, "usdt_futures")
        constraints = get_order_constraints(args.execution_mode, args.symbol, "usdt_futures")
        quantity = normalize_quantity(float(constraints.amount_step), args.reference_price, constraints)
        started = datetime.now(timezone.utc) - timedelta(minutes=1)

        # Exercise modify/cancel on a far-away post-only order before exposure.
        limit = SubmitOrderCommand(
            command_id=str(uuid4()),
            expected_account=identity,
            instrument=instrument,
            purpose="entry",
            client_order_id=f"utaquallimit{uuid4().hex[:12]}",
            side="buy",
            quantity=Decimal(str(quantity)),
            order_type="limit",
            limit_price=Decimal(str(args.reference_price / 2)),
            post_only=True,
            leverage=Decimal(args.leverage),
        )
        limit_report = await adapter.submit_order(limit)
        if (
            limit_report.evidence is not CommandEvidence.ACKNOWLEDGED
            or not limit_report.venue_order_id
            or (limit_report.order and limit_report.order.state not in {OrderLifecycle.ACCEPTED, OrderLifecycle.OPEN})
        ):
            raise RuntimeError("resting limit qualification was not acknowledged open")
        modified = await adapter.modify_order(
            ModifyOrderCommand(
                command_id=str(uuid4()),
                expected_account=identity,
                instrument=instrument,
                purpose="entry",
                target_venue_order_id=limit_report.venue_order_id,
                limit_price=Decimal(str(args.reference_price * Decimal("0.49"))),
            )
        )
        if modified.evidence is not CommandEvidence.ACKNOWLEDGED:
            raise RuntimeError("modify qualification was not acknowledged")
        cancelled = await adapter.cancel_order(
            CancelOrderCommand(
                command_id=str(uuid4()),
                expected_account=identity,
                instrument=instrument,
                purpose="entry",
                target_venue_order_id=limit_report.venue_order_id,
                reason="qualification_cleanup",
            )
        )
        if cancelled.evidence is not CommandEvidence.ACKNOWLEDGED:
            raise RuntimeError("cancel qualification was not acknowledged")
        cancelled_exact = await _wait_order(adapter, instrument, limit_report.venue_order_id, OrderLifecycle.CANCELED)
        if (
            cancelled_exact.completeness is not ReadCompleteness.COMPLETE
            or cancelled_exact.found is None
            or cancelled_exact.found.state is not OrderLifecycle.CANCELED
        ):
            raise RuntimeError("cancel qualification exact state unavailable")

        entry = _command(
            adapter,
            instrument,
            purpose="entry",
            side="buy",
            quantity=quantity,
            client_order_id=f"utaqualentry{uuid4().hex[:12]}",
            leverage=args.leverage,
        )
        entry_report = await adapter.submit_order(entry)
        if entry_report.evidence is not CommandEvidence.ACKNOWLEDGED or not entry_report.venue_order_id:
            raise RuntimeError("entry qualification acknowledgement unavailable")
        entry_fills = await _wait_fills(adapter, entry, entry_report.venue_order_id, started)
        if entry_fills.completeness is not ReadCompleteness.COMPLETE or not entry_fills.fills:
            artifact["unresolved_items"].append("entry_private_fill_unavailable")
            raise RuntimeError("entry private fill qualification unavailable")
        filled_quantity = sum((fill.quantity for fill in entry_fills.fills), Decimal(0))

        protection_submit = protection_cancel = None
        if args.execution_mode == "okx_demo":
            protection = SubmitOrderCommand(
                command_id=str(uuid4()),
                expected_account=identity,
                instrument=instrument,
                purpose="protective_stop",
                client_order_id=f"utaqualstop{uuid4().hex[:12]}",
                side="sell",
                quantity=filled_quantity,
                order_type="market",
                trigger_price=Decimal(str(args.reference_price / 2)),
                reduce_only=True,
            )
            protection_report = await adapter.submit_order(protection)
            protection_submit = protection_report.evidence.value
            if not protection_report.venue_order_id:
                raise RuntimeError("protection qualification acknowledgement unavailable")
            protection_report_cancel = await adapter.cancel_order(
                CancelOrderCommand(
                    command_id=str(uuid4()),
                    expected_account=identity,
                    instrument=instrument,
                    purpose="protective_stop",
                    target_venue_order_id=protection_report.venue_order_id,
                    reason="qualification_cleanup",
                )
            )
            protection_cancel = protection_report_cancel.evidence.value
            if protection_report_cancel.evidence is not CommandEvidence.ACKNOWLEDGED:
                raise RuntimeError("protection cancel qualification was not acknowledged")

        close = _command(
            adapter,
            instrument,
            purpose="exit",
            side="sell",
            quantity=filled_quantity,
            client_order_id=f"utaqualclose{uuid4().hex[:12]}",
            reduce_only=True,
        )
        close_report = await adapter.submit_order(close)
        if close_report.evidence is not CommandEvidence.ACKNOWLEDGED or not close_report.venue_order_id:
            artifact["unresolved_items"].append("close_acknowledgement_unavailable")
            raise RuntimeError("close qualification acknowledgement unavailable")
        close_fills = await _wait_fills(adapter, close, close_report.venue_order_id, started)
        if close_fills.completeness is not ReadCompleteness.COMPLETE or not close_fills.fills:
            artifact["unresolved_items"].append("close_private_fill_unavailable")
            raise RuntimeError("close private fill qualification unavailable")
        verified = await adapter.verify_position_exit(
            VerifiedExitQuery(
                identity=identity,
                local_position_id="uta-qualification",
                instrument=instrument,
                direction="long",
                expected_quantity=filled_quantity,
                history_start_at=started,
                history_end_at=datetime.now(timezone.utc) + timedelta(minutes=2),
                local_order_ids=(entry.client_order_id, close.client_order_id),
            )
        )
        exact = await adapter.get_order(
            OrderQuery(
                scope=QueryScope(instrument=instrument),
                venue_order_id=close_report.venue_order_id,
            )
        )
        artifact["execution_probe"] = {
            "quantity": str(quantity),
            "entry": {
                "command_id": entry.command_id,
                "client_order_id": entry.client_order_id,
                "venue_order_id": entry_report.venue_order_id,
                "fill_ids": [fill.venue_fill_id for fill in entry_fills.fills],
                "fees": [str(fill.fee) if fill.fee is not None else None for fill in entry_fills.fills],
                "times": [fill.executed_at.isoformat() for fill in entry_fills.fills],
            },
            "modify": modified.evidence.value,
            "cancel": cancelled.evidence.value,
            "protection_submit": protection_submit,
            "protection_cancel": protection_cancel,
            "close": {
                "command_id": close.command_id,
                "client_order_id": close.client_order_id,
                "venue_order_id": close_report.venue_order_id,
                "fill_ids": [fill.venue_fill_id for fill in close_fills.fills],
            },
            "exact_close_order": exact.completeness.value,
            "verified_exit": verified.state.value,
            "reconciliation": "exact_order_and_private_fills_complete",
            "funding_capability": capabilities.funding_ledger.value,
        }
        execution_ok = (
            exact.completeness is ReadCompleteness.COMPLETE
            and exact.found is not None
            and verified.state is VerifiedExitState.VERIFIED_FILL_EXIT
        )
        if is_worktree_dirty():
            artifact["unresolved_items"].append("qualification_worktree_dirty")
        artifact["qualified"] = read_ok and execution_ok and not artifact["unresolved_items"]
        if not artifact["qualified"]:
            artifact["unresolved_items"].append("qualification_gate_failed")
        return artifact
    finally:
        artifact["finished_at"] = datetime.now(timezone.utc).isoformat()
        await adapter.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execution-mode", required=True)
    parser.add_argument("--read-parity", action="store_true")
    parser.add_argument("--execution-probe", action="store_true")
    parser.add_argument("--confirm-testnet-order", action="store_true")
    parser.add_argument("--confirm-demo-order", action="store_true")
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--reference-price", type=Decimal)
    parser.add_argument("--leverage", type=int, default=1)
    parser.add_argument("--artifact", type=Path)
    args = parser.parse_args()
    if not args.read_parity:
        parser.error("--read-parity is required")
    if args.execution_probe and (args.reference_price is None or args.reference_price <= 0):
        parser.error("--execution-probe requires positive --reference-price")
    path = _artifact_path(args.execution_mode, args.artifact)
    try:
        artifact = asyncio.run(_qualify(args))
    except Exception as exc:
        artifact = {
            "qualified": False,
            "execution_mode": args.execution_mode,
            "code_sha": current_commit(),
            "uta_contract_version": UTA_CONTRACT_VERSION,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "failure_type": type(exc).__name__,
            "unresolved_items": ["qualification_failed"],
        }
    _write_artifact(path, artifact)
    print(json.dumps({"qualified": artifact["qualified"], "artifact": str(path)}))
    return 0 if artifact["qualified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
