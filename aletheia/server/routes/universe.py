"""Universe Generator and owner-facing Entry Workbench API routes."""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query

from aletheia.config.loader import load_config
from aletheia.storage import repositories as repo

router = APIRouter(prefix="/api/universe", tags=["universe"])


def _entry_plan_projection(row: dict) -> dict:
    import hashlib
    import json

    from aletheia.trading.entry_compat import decode_entry_plan

    item = dict(row)
    try:
        payload = str(item["plan_json"])
        plan = decode_entry_plan(payload)
        valid = hashlib.sha256(payload.encode()).hexdigest() == item["plan_hash"]
    except Exception:
        plan = None
        valid = False
    item["plan"] = json.loads(item["plan_json"]) if plan else None
    item["contract_valid"] = valid
    if (
        plan
        and plan.execution_account_mode == "paper_internal"
        and plan.market_type in {"perp", "swap", "usdt_futures"}
    ):
        from aletheia.portfolio.local_funding import local_funding_schedule_readiness

        item["funding_readiness"] = local_funding_schedule_readiness(
            plan.symbol,
            venue=plan.venue,
            market_type=plan.market_type,
        )
    return item


def _active_entry_plan_projections(limit: int) -> list[dict]:
    items = [_entry_plan_projection(row) for row in repo.list_active_entry_plans(limit)]
    if any(not item["contract_valid"] for item in items):
        raise ValueError("entry_plan_contract_invalid")
    return items


def _entry_plan_lineage(row: dict) -> dict:
    return repo.get_entry_plan_execution_lineage([row["id"]])[row["id"]]


@router.get("/workbench")
def get_entry_workbench(limit: int = Query(default=100, ge=1, le=200)):
    """Bounded direct-ledger projection; section failures remain explicit."""

    response = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "availability": {},
        "universe": None,
        "summary": None,
        "entry_plans": None,
        "current_candidates": None,
        "candidate_attention": None,
    }
    active_snapshot_id = None
    universe_read_available = False
    try:
        cfg = load_config()
        snap = repo.get_active_universe_snapshot()
        now = datetime.now(timezone.utc).isoformat()
        stale = bool(snap and snap.get("expires_at") and str(snap["expires_at"]) <= now)
        status = (
            "disabled"
            if not cfg.universe.enabled
            else "no_active_snapshot"
            if snap is None
            else "stale_snapshot"
            if stale
            else "active"
        )
        response["universe"] = {
            "enabled": cfg.universe.enabled,
            "status": status,
            "snapshot": snap,
            "fallback_instruments": (
                list(cfg.universe.fallback_symbols) if status != "active" else []
            ),
        }
        response["availability"]["universe"] = {"available": True, "stale": stale}
        universe_read_available = True
        if status == "active":
            active_snapshot_id = str(snap["id"])
    except Exception as exc:
        response["universe"] = {
            "enabled": None,
            "status": "read_unavailable",
            "snapshot": None,
            "fallback_instruments": [],
        }
        response["availability"]["universe"] = {
            "available": False,
            "stale": False,
            "reason": type(exc).__name__,
        }
    readers = {
        "summary": lambda: repo.get_entry_workbench_summary(),
        "entry_plans": lambda: _active_entry_plan_projections(limit),
        "current_candidates": lambda: (
            repo.list_current_candidate_workbench(active_snapshot_id)
            if active_snapshot_id
            else []
            if universe_read_available
            else (_ for _ in ()).throw(RuntimeError("universe_read_unavailable"))
        ),
        "candidate_attention": lambda: repo.list_entry_workbench_candidates(limit),
    }
    for section, read in readers.items():
        try:
            response[section] = read()
            if section in {"current_candidates", "candidate_attention"}:
                for candidate in response[section]:
                    if candidate.get("candidate_selection_policy") == "candidate_selection_utility_v1":
                        candidate["total_score"] = None
            response["availability"][section] = {"available": True, "stale": False}
        except Exception as exc:
            response["availability"][section] = {
                "available": False,
                "stale": False,
                "reason": type(exc).__name__,
            }
    from aletheia.agent_runtime.health import get_candidate_path_progress_snapshot

    progress = get_candidate_path_progress_snapshot()
    response["progress"] = progress
    response["availability"]["progress"] = {**progress["availability"], "stale": progress["availability"].get("reason") == "cache_expired"}
    return response


@router.get("/entry-plans/{plan_id}")
def get_entry_plan_detail(plan_id: str):
    row = repo.get_entry_plan(plan_id)
    if row is None:
        raise HTTPException(404, "Entry Plan not found")
    projection = _entry_plan_projection(row)
    if not projection["contract_valid"]:
        raise HTTPException(409, "Entry Plan contract validation failed")
    projection["lineage"] = _entry_plan_lineage(projection)
    return projection


@router.get("/planning-history")
def get_planning_history(
    limit: int = Query(default=50, ge=1, le=200),
    before: str | None = None,
):
    cursor = None
    if before:
        try:
            updated_at, generation_id = before.rsplit("|", 1)
            datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            if not generation_id:
                raise ValueError
            cursor = (updated_at, generation_id)
        except ValueError as exc:
            raise HTTPException(422, "Invalid planning history cursor") from exc
    rows = repo.list_planning_history(limit=limit + 1, before=cursor)
    page = rows[:limit]
    next_before = None
    if len(rows) > limit and page:
        next_before = f"{page[-1]['updated_at']}|{page[-1]['id']}"
    return {"items": page, "next_before": next_before}


def _attach_candidate_handoff_state(members: list[dict]) -> list[dict]:
    member_ids = [str(member.get("id") or "") for member in members if member.get("id")]
    if not member_ids:
        return members
    placeholders = ",".join("?" for _ in member_ids)
    from aletheia.storage.sqlite import get_db

    plan_rows = get_db().execute(
        "SELECT pg.last_candidate_id, ep.id, ep.status, ep.execution_mode, "
        "ep.created_at, ep.updated_at FROM candidate_planning_generations pg "
        "JOIN entry_plans ep ON ep.planning_generation_id=pg.id "
        f"WHERE pg.is_current=1 AND pg.last_candidate_id IN ({placeholders})",
        tuple(member_ids),
    ).fetchall()
    plans_by_candidate = {str(row["last_candidate_id"]): dict(row) for row in plan_rows}
    # Historical wire projection is not runtime authority.
    legacy_rows = get_db().execute(
        "SELECT us.member_id, us.state, us.opportunity_id "
        "FROM universe_setups us "
        "JOIN ("
        " SELECT member_id, MAX(datetime(created_at)) AS latest_created_at "
        " FROM universe_setups "
        f" WHERE member_id IN ({placeholders}) GROUP BY member_id"
        ") latest ON latest.member_id=us.member_id "
        "AND latest.latest_created_at=datetime(us.created_at)",
        tuple(member_ids),
    ).fetchall()
    legacy_by_member = {str(row["member_id"]): dict(row) for row in legacy_rows}
    enriched = []
    for member in members:
        item = dict(member)
        if item.get("candidate_selection_policy") == "candidate_selection_utility_v1":
            item["total_score"] = None
        candidate_id = str(item.get("id") or "")
        plan = plans_by_candidate.get(candidate_id)
        item["entry_plan_id"] = plan.get("id") if plan else None
        item["entry_plan_status"] = plan.get("status") if plan else None
        item["entry_plan_execution_mode"] = plan.get("execution_mode") if plan else None
        setup = legacy_by_member.get(candidate_id)
        item["setup_state"] = setup.get("state") if setup else None
        item["triggered_opportunity_id"] = setup.get("opportunity_id") if setup else None
        enriched.append(item)
    return enriched


@router.get("/current")
def get_current_universe():
    """Return the current active universe snapshot and its Candidates."""
    from aletheia.universe.service import UniverseService
    svc = UniverseService()
    cfg = load_config()

    # UniverseService owns the published snapshot namespace.  The configured
    # market_type controls which assets are scanned (crypto/stocks/mixed), while
    # snapshots are currently published under the service's canonical default.
    # Querying with cfg.universe.market_type here can otherwise return no
    # snapshot while the service returns members from the active one.
    snap = repo.get_active_universe_snapshot()
    # execution_eligible_only=False: UI sees stale members (tagged stale=True)
    # rather than falling back to the static 3-symbol pool.
    members = _attach_candidate_handoff_state(
        svc.get_current_universe(execution_eligible_only=False)
    )

    stale_count = sum(1 for m in members if m.get("stale"))
    return {
        "snapshot": snap,
        "members": members,  # compatibility alias: these rows are Candidates.
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
    """Get a specific snapshot with its Candidates."""
    from aletheia.storage.sqlite import get_db
    db = get_db()
    row = db.execute("SELECT * FROM universe_snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Snapshot not found")
    members = repo.list_universe_members(snapshot_id, limit=50)
    return {"snapshot": dict(row), "members": members}


@router.get("/members/{member_id}")
def get_member(member_id: str):
    """Get a Candidate with its current Entry Plan and compatibility history."""
    member = repo.get_universe_member(member_id)
    if not member:
        raise HTTPException(404, "Member not found")
    features = repo.get_latest_feature_snapshot(member["asset_id"])
    opportunities = repo.list_opportunities_by_member(member_id, limit=5)
    [member] = _attach_candidate_handoff_state([member])
    entry_plan = repo.get_entry_plan(member["entry_plan_id"]) if member.get("entry_plan_id") else None
    return {
        "member": member,
        "features": features,
        "entry_plans": [entry_plan] if entry_plan else [],
        "opportunities": opportunities,  # Backward-compatible historical read model.
    }


@router.get("/candidates/{candidate_id}")
def get_candidate(candidate_id: str):
    """Canonical Candidate detail alias; /members remains compatibility."""

    detail = get_member(candidate_id)
    attention = repo.get_candidate_planning_for_candidate(candidate_id)
    if attention is not None:
        attention.update({field: detail["member"].get(field) for field in (
            "admission_basis", "startup_reason", "pessimistic_strength",
        )})
    detail["attention"] = attention
    detail["lifecycle"] = repo.get_candidate_entry_lifecycle(candidate_id)
    return detail


@router.post("/refresh")
async def trigger_refresh():
    """Manually trigger a universe refresh. Returns immediately with the snapshot_id."""
    cfg = load_config()
    if not cfg.universe.enabled:
        raise HTTPException(400, detail="Universe generator is disabled in config")

    from aletheia.universe.service import UniverseService
    svc = UniverseService()
    snapshot_id = await svc.refresh()
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
    from aletheia.universe.outcome_economics import COST_MODEL_VERSION

    stats = repo.calibration_stats(cost_model_version=COST_MODEL_VERSION)

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
        "cost_model_version": COST_MODEL_VERSION,
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
