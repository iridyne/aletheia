"""Universe Generator API routes."""

import asyncio
import json

from fastapi import APIRouter, HTTPException, BackgroundTasks

from aletheia.config.loader import load_config
from aletheia.storage import repositories as repo

router = APIRouter(prefix="/api/universe", tags=["universe"])


@router.get("/current")
def get_current_universe():
    """Return the current active universe snapshot and its members."""
    from aletheia.universe.service import UniverseService
    svc = UniverseService()
    cfg = load_config()

    snap = repo.get_active_universe_snapshot(cfg.universe.market_type)
    # execution_eligible_only=False: UI sees stale members (tagged stale=True)
    # rather than falling back to the static 3-symbol pool.
    members = svc.get_current_universe(execution_eligible_only=False)

    stale_count = sum(1 for m in members if m.get("stale"))
    return {
        "snapshot": snap,
        "members": members,
        "enabled": cfg.universe.enabled,
        "fallback": snap is None,
        "stale": stale_count > 0,
        "stale_count": stale_count,
    }


@router.get("/snapshots")
def list_snapshots(market_type: str = "crypto", limit: int = 20):
    """List recent universe snapshots."""
    return repo.list_universe_snapshots(market_type, limit=limit)


@router.get("/snapshots/{snapshot_id}")
def get_snapshot(snapshot_id: str):
    """Get a specific snapshot with its members."""
    from aletheia.storage.sqlite import get_db
    db = get_db()
    row = db.execute("SELECT * FROM universe_snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Snapshot not found")
    members = repo.list_universe_members(snapshot_id, limit=50)
    return {"snapshot": dict(row), "members": members}


@router.get("/members/{member_id}")
def get_member(member_id: str):
    """Get a single universe member with full explanation, features, and linked opportunities."""
    member = repo.get_universe_member(member_id)
    if not member:
        raise HTTPException(404, "Member not found")
    features = repo.get_latest_feature_snapshot(member["asset_id"])
    opportunities = repo.list_opportunities_by_member(member_id, limit=5)
    return {"member": member, "features": features, "opportunities": opportunities}


@router.post("/refresh")
async def trigger_refresh():
    """Manually trigger a universe refresh. Returns immediately with the snapshot_id."""
    cfg = load_config()
    if not cfg.universe.enabled:
        raise HTTPException(400, detail="Universe generator is disabled in config")

    from aletheia.universe.service import UniverseService
    svc = UniverseService()
    snapshot_id = await svc.refresh(cfg.universe.market_type)
    if snapshot_id is None:
        raise HTTPException(500, detail="Universe refresh failed — check logs")
    return {"snapshot_id": snapshot_id, "status": "completed"}


@router.get("/policy")
def get_current_policy():
    """Return the current active universe policy."""
    cfg = load_config()
    policy = repo.get_active_universe_policy(cfg.universe.market_type)
    if policy:
        return policy
    # Return the config-derived defaults as a "virtual" policy
    return {
        "id": None,
        "source": "config_defaults",
        "market_type": cfg.universe.market_type,
        "family_quota": cfg.universe.family_quota,
        "explore_ratio": cfg.universe.explore_ratio,
        "max_candidates": cfg.universe.max_candidates,
        "min_volume_24h_usdt": cfg.universe.min_volume_24h_usdt,
        "max_spread_bps": cfg.universe.max_spread_bps,
    }


@router.get("/calibration")
def get_calibration():
    """Net calibration: family/direction/market/horizon aggregate stats.

    Answers: "do the scanner families actually pick winners?"
    """
    stats = repo.calibration_stats()

    cohort_agg: dict[tuple[str, str, str], dict] = {}
    for row in stats:
        fam = row["scanner_family"] or "unknown"
        direction = row.get("direction") or "unknown"
        market_type = row.get("market_type") or "spot"
        key = (fam, direction, market_type)
        if key not in cohort_agg:
            cohort_agg[key] = {
                "family": fam,
                "direction": direction,
                "market_type": market_type,
                "total_n": 0,
                "total_wins": 0,
                "weighted_return": 0.0,
            }
        cohort_agg[key]["total_n"] += row["n"]
        cohort_agg[key]["total_wins"] += row["win_count"]
        cohort_agg[key]["weighted_return"] += (row["avg_return"] or 0) * row["n"]

    summaries = []
    for _, agg in sorted(cohort_agg.items()):
        n = agg["total_n"]
        summaries.append({
            "family": agg["family"],
            "direction": agg["direction"],
            "market_type": agg["market_type"],
            "total_outcomes": n,
            "hit_rate_pct": round(agg["total_wins"] / n * 100, 1) if n > 0 else 0,
            "avg_return_pct": round(agg["weighted_return"] / n, 3) if n > 0 else 0,
        })

    return {
        "by_cohort_horizon": stats,
        "by_cohort": summaries,
    }


@router.get("/members/{member_id}/outcomes")
def get_member_outcomes(member_id: str):
    """Forward outcomes for a specific universe member."""
    outcomes = repo.list_forward_outcomes_by_member(member_id)
    if not outcomes:
        raise HTTPException(404, "No outcomes found for this member")
    return outcomes


@router.get("/registry/stats")
def registry_stats():
    """Asset registry statistics."""
    from aletheia.storage.sqlite import get_db
    db = get_db()
    total = db.execute("SELECT COUNT(*) as c FROM universe_registry").fetchone()
    active = db.execute(
        "SELECT COUNT(*) as c FROM universe_registry WHERE status = 'active' AND is_blacklisted = 0"
    ).fetchone()
    blacklisted = db.execute(
        "SELECT COUNT(*) as c FROM universe_registry WHERE is_blacklisted = 1"
    ).fetchone()
    return {
        "total": total["c"] if total else 0,
        "active": active["c"] if active else 0,
        "blacklisted": blacklisted["c"] if blacklisted else 0,
    }
