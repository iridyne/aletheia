"""Prediction Market Lens API — observation-only prediction market data.

Provides endpoints for the Prediction Market Lens frontend page.
All data is read-only except the manual refresh trigger.
"""

import json

from fastapi import APIRouter, HTTPException

from aletheia.storage import repositories as repo
from aletheia.storage.migrations import run_migrations

router = APIRouter(prefix="/api/prediction", tags=["prediction"])

_db_ready = False


def _ensure_db():
    global _db_ready
    if not _db_ready:
        run_migrations()
        _db_ready = True


def _parse_metadata(row: dict) -> dict:
    """Parse the JSON metadata field into the opportunity dict."""
    raw = row.get("metadata") or "{}"
    try:
        row["parsed_metadata"] = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        row["parsed_metadata"] = {}
    return row


@router.get("/opportunities")
async def get_prediction_opportunities(status: str | None = None, limit: int = 50):
    """List prediction-class opportunities with parsed metadata."""
    _ensure_db()
    opps = repo.list_opportunities_by_asset_class("prediction", limit=limit)
    if status:
        opps = [o for o in opps if o.get("status") == status]
    return [_parse_metadata(o) for o in opps]


@router.get("/opportunities/{opportunity_id}")
async def get_prediction_opportunity(opportunity_id: str):
    """Single prediction opportunity with parsed metadata."""
    _ensure_db()
    row = repo.get_opportunity(opportunity_id)
    if row is None:
        raise HTTPException(404, "Opportunity not found")
    if row.get("asset_class") != "prediction":
        raise HTTPException(404, "Not a prediction opportunity")
    return _parse_metadata(row)


@router.get("/status")
async def get_prediction_status():
    """Observer status: last run info, config, data freshness."""
    _ensure_db()
    last_run = repo.get_latest_prediction_run()
    opps = repo.list_opportunities_by_asset_class("prediction", limit=200)
    open_count = sum(1 for o in opps if o.get("status") == "open")

    from aletheia.config.loader import load_config
    cfg = load_config()
    categories = cfg.market_data.observed_prediction_markets

    return {
        "enabled": cfg.market_data.prediction_enabled,
        "last_run": last_run,
        "categories": categories,
        "total_opportunities": len(opps),
        "open_opportunities": open_count,
        "observer_interval_minutes": 30,
    }


@router.post("/toggle")
async def toggle_prediction(body: dict):
    """Enable or disable the prediction market module."""
    from aletheia.config.loader import load_config, save_config
    from aletheia.files.jsonl import write_audit
    cfg = load_config()
    old = cfg.market_data.prediction_enabled
    cfg.market_data.prediction_enabled = bool(body.get("enabled", False))
    save_config(cfg)
    write_audit({"actor": "human", "source": "dashboard", "event_type": "prediction_toggled",
                 "entity_type": "prediction_config", "before": {"enabled": old},
                 "after": {"enabled": cfg.market_data.prediction_enabled},
                 "message": f"Prediction market {'enabled' if cfg.market_data.prediction_enabled else 'disabled'}"})
    return {"enabled": cfg.market_data.prediction_enabled}


@router.get("/markets")
async def get_prediction_markets():
    """Return the most recent prediction opportunities as 'market cards' for the Lens.

    Each entry includes parsed metadata suitable for direct rendering.
    """
    _ensure_db()
    opps = repo.list_opportunities_by_asset_class("prediction", limit=100)
    results = []
    for o in opps:
        _parse_metadata(o)
        meta = o.get("parsed_metadata", {})
        results.append({
            "id": o["id"],
            "event_title": meta.get("event_title") or o.get("symbol", ""),
            "yes_probability": meta.get("yes_probability"),
            "probability_change_24h": meta.get("probability_change_24h"),
            "volume_24h": meta.get("volume_24h"),
            "liquidity": meta.get("liquidity"),
            "resolution_at": meta.get("resolution_at"),
            "category": meta.get("category", ""),
            "direction_bias": o.get("direction_bias"),
            "confidence": o.get("confidence"),
            "confidence_score": o.get("confidence_score"),
            "status": o.get("status"),
            "created_at": o.get("created_at"),
            "market_id": meta.get("market_id", ""),
            "enable_orderbook": meta.get("enable_orderbook", False),
            "source": meta.get("source", "polymarket"),
        })
    return results


@router.get("/runs")
async def get_prediction_runs(limit: int = 20):
    """List prediction observer run history."""
    _ensure_db()
    runs = repo.list_prediction_runs(limit=limit)
    for r in runs:
        for field in ("errors", "categories"):
            raw = r.get(field) or "[]"
            try:
                r[field] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                r[field] = []
    return runs


@router.get("/events")
async def get_prediction_events(category: str | None = None, status: str = "active",
                                search: str | None = None, limit: int = 50):
    """List prediction events with optional category/search filtering."""
    _ensure_db()
    events = repo.list_prediction_events(category=category, status=status, limit=limit)
    if search:
        q = search.lower()
        events = [e for e in events if q in (e.get("title") or "").lower()
                  or q in (e.get("category") or "").lower()]
    return events


@router.get("/events/{event_id}")
async def get_prediction_event_detail(event_id: str):
    """Event detail with linked markets."""
    _ensure_db()
    event = repo.get_prediction_event(event_id)
    if not event:
        raise HTTPException(404, "Event not found")
    markets = repo.list_prediction_markets_for_event(event_id)
    return {"event": event, "markets": markets}


@router.get("/markets/{market_id}/detail")
async def get_prediction_market_detail(market_id: str):
    """Single prediction market detail."""
    _ensure_db()
    market = repo.get_prediction_market_by_id(market_id)
    if not market:
        raise HTTPException(404, "Market not found")
    return market


@router.get("/markets/{market_id}/history")
async def get_prediction_market_history(market_id: str, interval: str = "1h"):
    """Probability history from local snapshots."""
    _ensure_db()
    snapshots = repo.list_probability_snapshots(market_id, limit=500)
    if not snapshots:
        market = repo.get_prediction_market_by_id(market_id)
        if not market:
            raise HTTPException(404, "Market not found")
        ext_id = market.get("external_market_id", "")
        if ext_id:
            from aletheia.data.polymarket_provider import fetch_market_history
            try:
                history = await fetch_market_history(ext_id, interval=interval)
                return {"source": "polymarket_api", "points": history}
            except Exception:
                pass
        return {"source": "local", "points": []}
    return {
        "source": "local",
        "points": [
            {"ts": s["ts"], "yes_probability": s["yes_probability"],
             "no_probability": s.get("no_probability"), "liquidity": s.get("liquidity")}
            for s in snapshots
        ],
    }


@router.get("/markets/{market_id}/claims")
async def get_prediction_market_claims(market_id: str, claim_type: str | None = None):
    """Claims / observations for a specific market."""
    _ensure_db()
    claims = repo.list_prediction_claims(market_id=market_id, claim_type=claim_type)
    return claims


@router.get("/claims")
async def get_all_prediction_claims(claim_type: str | None = None, limit: int = 100):
    """All prediction claims across markets."""
    _ensure_db()
    return repo.list_prediction_claims(claim_type=claim_type, limit=limit)


@router.get("/preferences")
async def get_prediction_preferences():
    """Current prediction observer preferences/config."""
    from aletheia.config.loader import load_config
    cfg = load_config()
    return {
        "categories": cfg.market_data.observed_prediction_markets,
        "observer_interval_minutes": 30,
    }


@router.patch("/preferences")
async def update_prediction_preferences(body: dict):
    """Update prediction observer preferences."""
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    if "categories" in body:
        cats = body["categories"]
        if isinstance(cats, list) and all(isinstance(c, str) for c in cats):
            cfg.market_data.observed_prediction_markets = cats
    save_config(cfg)
    return {"status": "ok", "categories": cfg.market_data.observed_prediction_markets}


@router.post("/refresh")
async def trigger_prediction_refresh():
    """Manually trigger a prediction observer run."""
    _ensure_db()
    from aletheia.config.loader import load_config
    cfg = load_config()
    if not cfg.market_data.observed_prediction_markets:
        raise HTTPException(400, "No prediction markets configured")

    from aletheia.agents import get_agent
    agent_fn = get_agent("prediction_observer")
    if not agent_fn:
        raise HTTPException(500, "prediction_observer agent not found")

    result = await agent_fn(permission_mode=cfg.agent.permission_mode)
    return {"status": "completed", "result": result}
