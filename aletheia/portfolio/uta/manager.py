"""UTAManager — parallel-query all configured brokers, aggregate into UnifiedState."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from aletheia.portfolio.uta.broker_protocol import IBroker

logger = logging.getLogger("aletheia.uta.manager")


@dataclass
class UnifiedState:
    total_equity_usdt: float
    total_available_usdt: float
    total_unrealized_pnl: float
    accounts: list[dict] = field(default_factory=list)
    positions: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # E2: broker_ids that actually returned an account this fetch — lets consumers/UI
    # distinguish "broker connected but 0 balance" from "broker not connected / missing
    # creds / query failed" (the raw accounts/errors lists already carry the detail).
    connected_brokers: list[str] = field(default_factory=list)
    fetched_at: str = ""


async def get_unified_state() -> UnifiedState:
    """Parallel-query all configured brokers and aggregate."""
    from aletheia.portfolio.uta.registry import create_all_configured
    brokers: list[IBroker] = create_all_configured()

    if not brokers:
        return UnifiedState(
            total_equity_usdt=0.0,
            total_available_usdt=0.0,
            total_unrealized_pnl=0.0,
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )

    results = await asyncio.gather(
        *[_query_broker(b) for b in brokers],
        return_exceptions=True,
    )

    total_equity = 0.0
    total_available = 0.0
    total_upnl = 0.0
    accounts = []
    all_positions = []
    errors = []

    for broker, result in zip(brokers, results):
        if isinstance(result, Exception):
            errors.append(f"{broker.broker_id}: {result}")
            logger.warning("UTA broker query failed (%s): %s", broker.broker_id, result)
            continue
        acct_info, positions = result
        total_equity += acct_info.equity_usdt
        total_available += acct_info.available_usdt
        total_upnl += acct_info.unrealized_pnl

        accounts.append({
            "broker_id": broker.broker_id,
            "display_name": broker.display_name,
            "equity_usdt": acct_info.equity_usdt,
            "available_usdt": acct_info.available_usdt,
            "frozen_usdt": acct_info.frozen_usdt,
            "unrealized_pnl": acct_info.unrealized_pnl,
            "asset_breakdown": acct_info.asset_breakdown,
        })
        from aletheia.portfolio.disappearance_confirmation import (
            logical_account_fingerprint,
        )

        account_environment = (
            "demo" if broker.broker_id.endswith("_demo")
            else "testnet" if broker.broker_id.endswith("_testnet")
            else "live" if broker.broker_id.endswith("_live_limited")
            else "unknown"
        )
        account_fingerprint = logical_account_fingerprint(
            broker_id=broker.broker_id,
            account_environment=account_environment,
            execution_mode=broker.broker_id,
        )
        for p in positions:
            all_positions.append({
                "broker_id": broker.broker_id,
                "account_environment": account_environment,
                "account_fingerprint": getattr(p, "account_fingerprint", None) or account_fingerprint,
                "position_id": getattr(p, "position_id", None),
                "symbol": p.symbol,
                "side": p.side,
                "quantity": p.quantity,
                "entry_price": p.entry_price,
                "unrealized_pnl": p.unrealized_pnl,
                "leverage": p.leverage,
                "leverage_source": p.leverage_source,
                "mark_price": p.mark_price,
                "cTime": p.opened_at_ms,
                "market_type": p.market_type,
            })

    return UnifiedState(
        total_equity_usdt=round(total_equity, 4),
        total_available_usdt=round(total_available, 4),
        total_unrealized_pnl=round(total_upnl, 4),
        accounts=accounts,
        positions=all_positions,
        errors=errors,
        connected_brokers=[a["broker_id"] for a in accounts],
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )


async def _query_broker(broker: IBroker):
    """Query a single broker's account + positions; closes session after."""
    try:
        acct, positions = await asyncio.gather(
            broker.get_account(),
            broker.get_positions(),
        )
        return acct, positions
    finally:
        try:
            await broker.close()
        except Exception:
            pass


def get_unified_state_sync() -> UnifiedState:
    """Sync wrapper — runs the async aggregation from any calling context."""
    from concurrent.futures import ThreadPoolExecutor

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(get_unified_state())

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, get_unified_state()).result(timeout=30)
