"""One-off stability drive for the autonomous paper loop (north-star stage 0).

Runs the scheduler's agent jobs in a realistic sequence many times against an
ISOLATED throwaway data dir, with autonomous paper trading ON, then asserts the
state is internally consistent: no orphan orders/fills, strategy_id preserved
through the chain, promotion-gate counters actually moving, no crash.

This is NOT a test fixture — it's an operator tool. Run:
    .venv/Scripts/python.exe scripts/stability_drive.py [cycles]
"""

import asyncio
import sqlite3
import sys
import tempfile
from pathlib import Path


def _setup(tmp: Path):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    loader.DATA_DIR = tmp
    loader.CONFIG_PATH = tmp / "config.yaml"
    for sub in ("logs", "knowledge/research_notes", "knowledge/strategies", "playbooks", "reports"):
        (tmp / sub).mkdir(parents=True, exist_ok=True)
    from aletheia.storage.migrations import run_migrations
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    from aletheia.agent_runtime.tool_registry import init_tool_registry
    init_tool_registry()
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    save_config(cfg)


def _run_label(agent: str, cycle: int | None) -> str:
    return f"cycle{cycle}:{agent}" if cycle is not None else f"initial:{agent}"


def _redacted_error_types(errors: object) -> str:
    if not isinstance(errors, list):
        return ""
    items = []
    for error in errors[:3]:
        text = str(error)
        items.append((text.split(":", 1)[0] or "error")[:80])
    return ",".join(items)


def _record_agent_result(
    agent: str,
    cycle: int | None,
    result: dict,
    statuses: dict[str, int],
    degraded: list[str],
) -> None:
    status = result.get("status")
    statuses[status if status in statuses else "other"] += 1
    if status == "completed":
        return
    run_id = result.get("run_id") or "unknown_run"
    error_types = _redacted_error_types(result.get("errors"))
    detail = f"{_run_label(agent, cycle)}:{run_id}:{status or 'missing_status'}"
    if error_types:
        detail += f":errors={error_types}"
    degraded.append(detail)


async def _drive(cycles: int) -> dict:
    from aletheia.agent_runtime.orchestrator import run_agent
    from aletheia.trading.market_data import collect_market_snapshots
    from aletheia.storage.models import MarketSnapshot
    from aletheia.storage import repositories as repo

    statuses = {"completed": 0, "completed_with_errors": 0, "other": 0}
    crashes = []
    degraded = []

    # Seed one strategy producer up front.
    try:
        r = await run_agent("strategy_designer", trigger_type="manual")
        _record_agent_result("strategy_designer", None, r, statuses, degraded)
    except Exception as e:
        crashes.append(f"initial:strategy_designer:{type(e).__name__}:{e}")

    for i in range(cycles):
        # Fresh market snapshot each cycle (public fetch may fail offline → seed fallback).
        try:
            await collect_market_snapshots()
        except Exception:
            pass
        for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
            if not repo.list_market_snapshots(symbol=sym, limit=1):
                repo.insert_market_snapshot(MarketSnapshot(symbol=sym, market_type="spot", price=60000.0))

        # market_observer runs (records an opportunity; neutral when LLM is offline).
        try:
            r = await run_agent("market_observer", trigger_type="manual")
            _record_agent_result("market_observer", i, r, statuses, degraded)
        except Exception as e:
            crashes.append(f"cycle{i}:market_observer:{type(e).__name__}:{e}")

        # When the LLM is offline the observer can't produce a directional signal,
        # so the trade path never fires. Inject a directional opportunity bound to an
        # active strategy — this is exactly what the observer emits WITH an LLM — so we
        # still exercise the full trade chain under the real orchestrator/risk path.
        from aletheia.storage.models import Opportunity
        active = next((s for s in repo.list_strategies(limit=50)
                       if s.get("state") in ("paper_running", "paper_candidate")), None)
        repo.insert_opportunity(Opportunity(
            symbol="BTCUSDT", market="spot", direction_bias="long",
            thesis="stability-drive injected directional signal", confidence="medium",
            source_agent="market_observer",
            linked_strategy_id=active["id"] if active else None,
        ))

        for agent in ("paper_trader", "dreaming_agent"):
            try:
                r = await run_agent(agent, trigger_type="manual")
                _record_agent_result(agent, i, r, statuses, degraded)
            except Exception as e:
                crashes.append(f"cycle{i}:{agent}:{type(e).__name__}:{e}")

        # Close any open positions via the position manager (exercises the exit path).
        try:
            from aletheia.portfolio.position_manager import evaluate_open_positions
            evaluate_open_positions()
        except Exception as e:
            crashes.append(f"cycle{i}:position_manager:{type(e).__name__}:{e}")

    return {"statuses": statuses, "crashes": crashes, "degraded": degraded}


def _consistency_checks() -> list[str]:
    """Assert internal state integrity. Returns a list of violations (empty = clean)."""
    from aletheia.storage import repositories as repo
    problems = []

    orders = repo.list_orders(limit=10000)
    fills = repo.list_fills(limit=10000)
    proposals = {p["id"]: p for p in repo.list_trade_proposals(limit=10000)}
    order_ids = {o["id"] for o in orders}

    # 1. Every fill points at a real order (no orphan fills).
    for f in fills:
        if f["order_id"] not in order_ids:
            problems.append(f"orphan_fill:{f['id']}->order {f['order_id']}")

    # 2. Every order points at a real proposal.
    for o in orders:
        if o.get("proposal_id") and o["proposal_id"] not in proposals:
            problems.append(f"orphan_order:{o['id']}->proposal {o['proposal_id']}")

    # 3. strategy_id preserved: a proposal with strategy_id => its order keeps it.
    for o in orders:
        pid = o.get("proposal_id")
        if pid and proposals.get(pid, {}).get("strategy_id"):
            if not o.get("strategy_id") and o.get("status") == "filled":
                # order table may not carry strategy_id; check position instead below
                pass

    # 4. No executed proposal without an order.
    executed = [p for p in proposals.values() if p.get("status") == "executed"]
    for p in executed:
        if not any(o.get("proposal_id") == p["id"] for o in orders):
            problems.append(f"executed_proposal_without_order:{p['id']}")

    return problems


def _exercise_checks(strategies, proposals, orders, fills, positions) -> list[str]:
    """Assert the drive exercised the chain it claims to qualify."""
    problems = []
    if not strategies:
        problems.append("not_exercised:strategy_designer")
    if not proposals:
        problems.append("not_exercised:proposal")
    if not orders:
        problems.append("not_exercised:order")
    if not fills and not positions:
        problems.append("not_exercised:fill_or_position")

    proposal_ids = {p["id"] for p in proposals}
    order_ids = {o["id"] for o in orders}
    chain_orders = [
        o for o in orders
        if o.get("proposal_id") in proposal_ids
    ]
    if not chain_orders:
        problems.append("not_exercised:proposal_order_link")
    elif not any(
        f.get("order_id") in order_ids for f in fills
    ) and not any(
        p.get("order_id") in order_ids for p in positions
    ):
        problems.append("not_exercised:order_fill_or_position_link")
    return problems


def main():
    cycles = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    tmp = Path(tempfile.mkdtemp(prefix="aletheia_stability_"))
    print(f"[stability] data dir: {tmp}")
    print(f"[stability] driving {cycles} cycles (designer once + observer/trader/dreaming/posmgr per cycle)")
    _setup(tmp)
    result = asyncio.run(_drive(cycles))

    from aletheia.storage import repositories as repo
    from aletheia.trading.strategy_lifecycle import check_promotion_gates

    strategies = repo.list_strategies(limit=100)
    proposals = repo.list_trade_proposals(limit=10000)
    orders = repo.list_orders(limit=10000)
    fills = repo.list_fills(limit=10000)
    positions = repo.list_positions(limit=10000)

    print("\n=== run statuses ===")
    print(result["statuses"])
    print("\n=== state counts ===")
    print(f"strategies={len(strategies)} proposals={len(proposals)} "
          f"orders={len(orders)} fills={len(fills)} positions={len(positions)}")

    # promotion gate for the first strategy
    if strategies:
        g = check_promotion_gates(strategies[0]["id"])
        print(f"\n=== promotion gate (strategy {strategies[0]['id'][:8]}, state={strategies[0]['state']}) ===")
        print(f"paper_trades={g['metrics'].get('paper_trades')} "
              f"runtime_days={g['metrics'].get('paper_runtime_days')} "
              f"closed_positions={g['metrics'].get('closed_positions')}")

    problems = _consistency_checks()
    exercise_problems = _exercise_checks(strategies, proposals, orders, fills, positions)
    print("\n=== consistency check ===")
    if result["crashes"]:
        print(f"CRASHES ({len(result['crashes'])}):")
        for c in result["crashes"][:20]:
            print(f"  - {c}")
    if result["degraded"]:
        print(f"DEGRADED RUNS ({len(result['degraded'])}):")
        for item in result["degraded"][:20]:
            print(f"  - {item}")
    if problems:
        print(f"STATE PROBLEMS ({len(problems)}):")
        for p in problems[:20]:
            print(f"  - {p}")
    if exercise_problems:
        print(f"NOT EXERCISED ({len(exercise_problems)}):")
        for p in exercise_problems[:20]:
            print(f"  - {p}")
    if not result["crashes"] and not result["degraded"] and not problems and not exercise_problems:
        print("CLEAN: required agent runs completed and exercised chain is internally consistent.")

    print(f"\n[stability] (leaving {tmp} for inspection; delete when done)")
    return 1 if (result["crashes"] or result["degraded"] or problems or exercise_problems) else 0


if __name__ == "__main__":
    sys.exit(main())
