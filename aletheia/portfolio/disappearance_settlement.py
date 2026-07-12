"""Route ready disappearance candidates through verified exit evidence."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from aletheia.execution.verified_exit import VerifiedExitRequest
from aletheia.portfolio.disappearance_confirmation import disappearance_ready
from aletheia.portfolio.outcome_closure import close_position_from_verified_exit
from aletheia.storage import repositories as repo


def _to_ms(value: str | None) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _client_for_mode(execution_mode: str):
    if execution_mode in {"bitget_demo", "bitget_live_limited"}:
        from aletheia.exchanges.bitget.client import BitgetClient
        return BitgetClient(
            mode="live" if execution_mode == "bitget_live_limited" else "demo"
        )
    if execution_mode in {"okx_demo", "okx_live_limited"}:
        from aletheia.exchanges.okx.client import OKXClient
        return OKXClient(mode="live" if execution_mode == "okx_live_limited" else "demo")
    if execution_mode in {"binance_testnet", "binance_live_limited"}:
        from aletheia.exchanges.binance.client import BinanceClient
        return BinanceClient(
            mode="live" if execution_mode == "binance_live_limited" else "testnet"
        )
    raise ValueError(f"unsupported verified-exit mode: {execution_mode}")


def _run_query(client, request):
    async def query():
        try:
            return await client.get_verified_position_exit(request)
        finally:
            close = getattr(client, "close", None)
            if close is not None:
                await close()

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, query()).result(timeout=60)
    return asyncio.run(query())


def settle_ready_disappearance_candidate(candidate_id: str) -> dict:
    candidate = repo._select_one("position_disappearance_candidates", candidate_id)
    if candidate is None:
        raise ValueError(f"disappearance candidate not found: {candidate_id}")
    if candidate.get("status") != "pending":
        return {"candidate_id": candidate_id, "closed": False, "reason": "already_resolved"}
    if not disappearance_ready(candidate):
        return {"candidate_id": candidate_id, "closed": False, "reason": "not_ready"}
    position = repo._select_one("positions", str(candidate["position_id"]))
    if position is None:
        raise ValueError(f"candidate position not found: {candidate['position_id']}")
    if position.get("status") != "open":
        return {"candidate_id": candidate_id, "closed": False, "reason": "position_not_open"}
    execution_mode = str(position["execution_mode"])
    if execution_mode.endswith("_live_limited"):
        return {"candidate_id": candidate_id, "closed": False, "reason": "live_alert_only"}

    history_start_ms = _to_ms(position.get("opened_at"))
    if history_start_ms is None:
        return {
            "candidate_id": candidate_id, "closed": False,
            "reason": "missing_complete_history_start",
        }
    history_end_ms = max(
        int(datetime.now(timezone.utc).timestamp() * 1000), history_start_ms + 1,
    )
    request = VerifiedExitRequest(
        broker_id=str(candidate["broker_id"]),
        account_environment=str(candidate["account_environment"]),
        account_scope=str(candidate["account_fingerprint"]),
        execution_mode=execution_mode,
        position_id=str(position["id"]),
        symbol=str(position["symbol"]),
        market_type=str(position["market_type"]),
        direction=str(position["direction"]).lower(),
        quantity=float(position["quantity"]),
        history_start_ms=history_start_ms,
        history_end_ms=history_end_ms,
    )
    try:
        evidence = _run_query(_client_for_mode(execution_mode), request)
    except Exception as exc:
        return {
            "candidate_id": candidate_id, "closed": False,
            "reason": "verified_exit_query_failed", "error_type": type(exc).__name__,
        }
    result = close_position_from_verified_exit(
        str(position["id"]), evidence,
        reason=("verified_exit" if evidence.state == "verified_fill" else "reconciled_gone"),
        candidate_id=candidate_id,
    )
    if result.get("closed") or result.get("already_closed"):
        stamp = datetime.now(timezone.utc).isoformat()
        status = (
            "settled_verified"
            if result.get("closure_type") in {"verified_strategy", "strategy"}
            else "settled_administrative"
        )
        repo.update_position_disappearance_candidate(
            candidate_id, status=status, resolved_at=stamp, updated_at=stamp,
        )
    return {"candidate_id": candidate_id, **result}


def settle_ready_disappearance_candidates(candidate_ids: list[str]) -> list[dict]:
    results = []
    for candidate_id in dict.fromkeys(candidate_ids):
        results.append(settle_ready_disappearance_candidate(candidate_id))
    return results
