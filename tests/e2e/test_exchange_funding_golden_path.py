from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


_PROCESS_REPLAY = textwrap.dedent(
    r"""
    import asyncio
    import json
    import os
    from pathlib import Path
    import sys

    data_dir = Path(os.environ["ALETHEIA_DATA_DIR"])
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)

    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.sqlite import get_db

    run_migrations()

    from aletheia.storage import repositories as repo

    MODES = {
        "bitget_demo": {
            "environment": "demo",
            "direction": "long",
            "exit_price": 110.0,
            "funding_row": {
                "billId": "bg-funding-1",
                "symbol": "BTCUSDT",
                "amount": "-0.25",
                "businessType": "contract_settle_fee",
                "coin": "USDT",
                "cTime": "1784030400000",
            },
        },
        "okx_demo": {
            "environment": "demo",
            "direction": "short",
            "exit_price": 90.0,
            "funding_row": {
                "billId": "okx-funding-1",
                "instId": "BTC-USDT-SWAP",
                "pnl": "-0.25",
                "type": "8",
                "subType": "173",
                "ccy": "USDT",
                "ts": "1784030400000",
                "posSide": "short",
            },
        },
        "binance_testnet": {
            "environment": "testnet",
            "direction": "long",
            "exit_price": 110.0,
            "funding_row": {
                "tranId": "bn-funding-1",
                "symbol": "BTCUSDT",
                "income": "-0.25",
                "incomeType": "FUNDING_FEE",
                "asset": "USDT",
                "time": "1784030400000",
            },
        },
    }
    OPENED_AT = "2026-07-14T00:00:00+00:00"
    CLOSED_AT = "2026-07-14T12:00:00+00:00"
    CLOSED_AT_MS = 1784030400000


    def seed_exchange_chain(mode, spec):
        from aletheia.execution.exchange_materialization import materialize_bitget_close
        from aletheia.storage.models import (
            Fill,
            Opportunity,
            Order,
            Position,
            RiskDecision,
            TradeProposal,
        )

        suffix = mode.replace("_", "-")
        opportunity = Opportunity(
            id=f"opp-{suffix}",
            symbol="BTC/USDT",
            market="usdt_futures",
            direction_bias=spec["direction"],
            scanner_family="exchange-funding-golden-path",
            source_agent="exchange-funding-golden-path",
            created_at=OPENED_AT,
        )
        repo.insert_opportunity(opportunity)
        proposal = TradeProposal(
            id=f"proposal-{suffix}",
            opportunity_id=opportunity.id,
            strategy_id="exchange-funding-golden-path",
            symbol=opportunity.symbol,
            market_type="usdt_futures",
            direction=spec["direction"],
            order_type="market",
            quantity=1.0,
            price=100.0,
            execution_mode=mode,
            status="executed",
            created_at=OPENED_AT,
        )
        repo.insert_trade_proposal(proposal)
        decision = RiskDecision(
            id=f"risk-{suffix}",
            proposal_id=proposal.id,
            approved=True,
            allowed_execution_mode=mode,
            proposal_execution_mode=mode,
            decision_role="authoritative",
            evaluated_quantity=proposal.quantity,
            proposal_commit_hash=proposal.commit_hash,
            created_at=OPENED_AT,
        )
        repo.insert_risk_decision(decision)
        entry_order = Order(
            id=f"entry-order-{suffix}",
            proposal_id=proposal.id,
            risk_decision_id=decision.id,
            symbol=proposal.symbol,
            market_type=proposal.market_type,
            direction=proposal.direction,
            order_type=proposal.order_type,
            quantity=proposal.quantity,
            price=proposal.price,
            execution_mode=mode,
            exchange_order_id=f"remote-entry-{suffix}",
            client_order_id=proposal.id,
            status="filled",
            created_at=OPENED_AT,
            filled_at=OPENED_AT,
        )
        repo.insert_order(entry_order)
        repo.insert_fill(Fill(
            id=f"private-fill:{mode}:entry-{suffix}",
            order_id=entry_order.id,
            fill_price=100.0,
            fill_quantity=1.0,
            fee=0.1,
            fee_currency="USDT",
            fee_source="reconciled",
            filled_at=OPENED_AT,
        ))
        position = Position(
            id=f"position-{suffix}",
            proposal_id=proposal.id,
            order_id=entry_order.id,
            opportunity_id=opportunity.id,
            strategy_id=proposal.strategy_id,
            symbol=proposal.symbol,
            market_type=proposal.market_type,
            direction=proposal.direction,
            quantity=1.0,
            entry_price=100.0,
            current_price=100.0,
            entry_fee=0.1,
            execution_mode=mode,
            entry_price_source="broker_fill",
            entry_fill_verified=True,
            opened_at=OPENED_AT,
        )
        repo.insert_position(position)
        repo.update_order(entry_order.id, position_id=position.id)
        close_order = Order(
            id=f"close-order-{suffix}",
            proposal_id=proposal.id,
            symbol=proposal.symbol,
            market_type=proposal.market_type,
            direction=proposal.direction,
            order_type="market",
            quantity=1.0,
            price=spec["exit_price"],
            execution_mode=mode,
            exchange_order_id=f"remote-close-{suffix}",
            client_order_id=f"close-{position.id}",
            position_id=position.id,
            close_reason="take_profit",
            status="submitted",
            created_at=CLOSED_AT,
        )
        repo.insert_order(close_order)
        repo.update_position(
            position.id,
            pending_close_order_id=close_order.id,
            pending_close_status="submitted",
            pending_close_requested_at=CLOSED_AT,
        )
        fills = [{
            "tradeId": f"close-{suffix}",
            "orderId": close_order.exchange_order_id,
            "clientOid": close_order.client_order_id,
            "fillPrice": str(spec["exit_price"]),
            "fillQuantity": "1.0",
            "fee": "-0.2",
            "feeCoin": "USDT",
            "cTime": str(CLOSED_AT_MS),
        }]
        first = materialize_bitget_close(close_order.id, fills)
        replay = materialize_bitget_close(close_order.id, fills)
        return {
            "mode": mode,
            "first_state": first.state,
            "first_new_fill_count": first.new_fill_count,
            "replay_state": replay.state,
            "replay_new_fill_count": replay.new_fill_count,
        }


    def ledger_for(mode, start_time_ms, end_time_ms):
        from aletheia.execution.broker_funding import (
            make_complete_ledger,
            normalize_binance_funding_rows,
            normalize_bitget_funding_rows,
            normalize_okx_funding_rows,
        )

        normalizers = {
            "bitget_demo": normalize_bitget_funding_rows,
            "okx_demo": normalize_okx_funding_rows,
            "binance_testnet": normalize_binance_funding_rows,
        }
        endpoints = {
            "bitget_demo": "/api/v2/mix/account/bill",
            "okx_demo": "/api/v5/account/bills",
            "binance_testnet": "/fapi/v1/income",
        }
        spec = MODES[mode]
        return make_complete_ledger(
            broker_id=mode,
            account_environment=spec["environment"],
            source_endpoint=endpoints[mode],
            history_start_ms=start_time_ms,
            history_end_ms=end_time_ms,
            events=normalizers[mode]([spec["funding_row"]]),
        )


    class FixtureFundingClient:
        def __init__(self, mode):
            self.mode = mode

        async def get_funding_ledger(
            self, *, broker_id, symbol, start_time_ms, end_time_ms
        ):
            assert broker_id == self.mode
            assert symbol == "BTC/USDT"
            return ledger_for(self.mode, start_time_ms, end_time_ms)

        async def close(self):
            return None


    def database_snapshot(replay_result=None):
        db = get_db()
        positions = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM positions ORDER BY execution_mode"
            ).fetchall()
        ]
        hooks = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM outcome_hook_jobs ORDER BY position_id, hook_type"
            ).fetchall()
        ]
        return {
            "replay_result": replay_result,
            "positions": [{
                "id": row["id"],
                "execution_mode": row["execution_mode"],
                "status": row["status"],
                "realized_pnl": row["realized_pnl"],
                "funding_cost": row["funding_cost"],
                "funding_source": row["funding_source"],
                "outcome_eligible": row["outcome_eligible"],
                "outcome_exclusion_reason": row["outcome_exclusion_reason"],
                "reconciliation_evidence": row["reconciliation_evidence"],
            } for row in positions],
            "close_event_count": db.execute(
                "SELECT COUNT(*) FROM position_close_events"
            ).fetchone()[0],
            "close_fill_count": db.execute(
                "SELECT COUNT(*) FROM fills WHERE id LIKE 'private-fill:%:close-%'"
            ).fetchone()[0],
            "settlement_audit_count": db.execute(
                "SELECT COUNT(*) FROM audit_events "
                "WHERE event_type='exchange_position_funding_settled'"
            ).fetchone()[0],
            "paper_cash_event_count": db.execute(
                "SELECT COUNT(*) FROM paper_account_events"
            ).fetchone()[0],
            "hooks": [{
                "position_id": row["position_id"],
                "hook_type": row["hook_type"],
                "status": row["status"],
                "attempts": row["attempts"],
            } for row in hooks],
        }


    phase = sys.argv[1]
    if phase == "create":
        materializations = [
            seed_exchange_chain(mode, spec)
            for mode, spec in MODES.items()
        ]
        print(json.dumps({
            "materializations": materializations,
            **database_snapshot(),
        }, sort_keys=True))
    elif phase in {"settle", "verify"}:
        from aletheia.portfolio import broker_funding_settlement as settlement

        settlement._client_for_mode = lambda mode: FixtureFundingClient(mode)
        result = asyncio.run(settlement.settle_unsettled_exchange_closes())
        print(json.dumps(database_snapshot(result), sort_keys=True))
    else:
        raise SystemExit(f"unknown phase: {phase}")
    """
)


def _run_process_phase(data_dir: Path, phase: str) -> dict:
    project_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["ALETHEIA_DATA_DIR"] = str(data_dir)
    env["PYTHONPATH"] = str(project_root)
    completed = subprocess.run(
        [sys.executable, "-c", _PROCESS_REPLAY, phase],
        cwd=project_root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize(
    "mode",
    ["bitget_demo", "okx_demo", "binance_testnet"],
)
def test_exchange_funding_survives_private_fill_replay_and_process_restart(
    tmp_path: Path,
    mode: str,
):
    """A separate Golden Path for exchange net economics and restart convergence.

    The fixture replays adapter-normalized private fills plus venue-shaped
    funding bills without network access. Three real Python processes create,
    settle, and verify one durable SQLite state root, so the proof crosses both
    connection and process boundaries while retaining the normal production
    materializers and settler.
    """
    # Each parameter gets an isolated persistent state root. Keeping one venue
    # per run also prevents a passing venue from masking another venue's failure.
    data_dir = tmp_path / mode
    created = _run_process_phase(data_dir, "create")
    created_row = next(
        row for row in created["positions"] if row["execution_mode"] == mode
    )
    materialization = next(
        row
        for row in created["materializations"]
        if row["mode"] == mode
    )
    assert materialization == {
        "mode": mode,
        "first_state": "filled",
        "first_new_fill_count": 1,
        "replay_state": "filled",
        "replay_new_fill_count": 0,
    }
    assert created_row["status"] == "closed"
    assert created_row["realized_pnl"] is None
    assert created_row["outcome_eligible"] == 0
    assert (
        created_row["outcome_exclusion_reason"]
        == "incomplete_funding_economics"
    )

    settled = _run_process_phase(data_dir, "settle")
    settled_row = next(
        row for row in settled["positions"] if row["execution_mode"] == mode
    )
    assert settled["replay_result"]["checked"] == 3
    assert settled["replay_result"]["settled"] == 3
    assert settled["replay_result"]["still_pending"] == 0
    assert settled["replay_result"]["errors"] == []
    assert settled["replay_result"]["as_of"]
    assert settled_row["realized_pnl"] == pytest.approx(9.45)
    assert settled_row["funding_cost"] == pytest.approx(0.25)
    assert settled_row["funding_source"] == "broker_ledger"
    assert settled_row["outcome_eligible"] == 1
    assert settled_row["outcome_exclusion_reason"] == "eligible"
    evidence = json.loads(settled_row["reconciliation_evidence"])
    assert evidence["funding"]["source"] == "broker_ledger"
    assert evidence["funding"]["events"][0]["funding_cost"] == pytest.approx(0.25)
    assert evidence["funding_economics_fingerprint"].startswith("sha256:")

    # A third interpreter opens the same database and reruns the production
    # recovery job. Nothing is selected or duplicated after the durable result.
    verified = _run_process_phase(data_dir, "verify")
    assert verified["replay_result"]["checked"] == 0
    assert verified["replay_result"]["settled"] == 0
    assert verified["replay_result"]["still_pending"] == 0
    assert verified["replay_result"]["errors"] == []
    assert verified["close_event_count"] == 3
    assert verified["close_fill_count"] == 3
    assert verified["settlement_audit_count"] == 3
    assert verified["paper_cash_event_count"] == 0
    assert len(verified["hooks"]) == 3
    assert {
        (job["status"], job["attempts"], job["hook_type"])
        for job in verified["hooks"]
    } == {("completed", 1, "shadow_trades")}
