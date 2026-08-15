"""Bounded, local-only dashboard bootstrap view models."""

from __future__ import annotations

from datetime import datetime, timezone
import time

from fastapi import APIRouter, Response

from aletheia import __version__
from aletheia.config.loader import load_config
from aletheia.server.routes.health import _cached_compute_health, _cached_head_commit
from aletheia.storage.sqlite import get_db
from aletheia.storage import repositories as repo

router = APIRouter(prefix="/api/ui", tags=["ui"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _available(value, *, reason: str | None = None) -> dict:
    return {"availability": "available" if reason is None else "unavailable", "value": value, "reason": reason}


def _safe(name: str, fn, default):
    try:
        return _available(fn())
    except Exception as exc:  # pragma: no cover - exercised through route contract tests
        return _available(default, reason=f"{name}:{type(exc).__name__}")


def _risk_snapshot(cfg) -> dict:
    violations: list[str] = []
    status = "not_evaluated"
    if cfg.runtime.kill_switch_active:
        status = "blocked"
        violations.append("kill_switch_active")
    elif cfg.runtime.live_brake_active and cfg.exchange.mode == "live":
        status = "blocked"
        violations.append("live_brake_active")
    elif cfg.runtime.demo_brake_active and cfg.exchange.mode != "live":
        status = "blocked"
        violations.append("demo_brake_active")
    else:
        if cfg.exchange.mode == "live" and cfg.live.state == "live_disabled":
            status = "warning"
            violations.append("live_disabled_while_exchange_live")
        if cfg.exchange.mode == "live" and cfg.agent.permission_mode == "Observe":
            status = "warning"
            violations.append(f"permission_mode_not_live_eligible:{cfg.agent.permission_mode}")
        if not violations:
            status = "healthy"
    return {"status": status, "violations": violations, "kill_switch_active": cfg.runtime.kill_switch_active}


def _scheduler_snapshot() -> dict:
    try:
        from aletheia.agent_runtime.scheduler import status_snapshot

        return status_snapshot()
    except Exception as exc:
        return {"enabled": None, "running": False, "state": f"unavailable:{type(exc).__name__}"}


def _operator_snapshot() -> dict:
    try:
        from aletheia.server.routes.operator import _operator_status_payload

        payload = _operator_status_payload()
        return {key: payload.get(key) for key in ("status", "mode", "permission_mode", "last_seen_at", "stale", "heartbeat_age_sec")}
    except Exception as exc:
        return {"status": "not_available", "reason": f"operator:{type(exc).__name__}"}


def _ticker_snapshot() -> dict[str, dict]:
    result: dict[str, dict] = {}
    for symbol in ("BTC/USDT", "ETH/USDT", "SOL/USDT"):
        try:
            rows = repo.list_market_snapshots(symbol=symbol, limit=1)
            if rows:
                row = rows[0]
                result[symbol] = {
                    "symbol": symbol,
                    "price": row.get("price"),
                    "change_pct_24h": row.get("change_pct_24h"),
                    "snapshot_at": row.get("snapshot_at"),
                }
            else:
                result[symbol] = {"availability": "unavailable", "reason": "no_snapshot"}
        except Exception as exc:
            result[symbol] = {"availability": "unavailable", "reason": f"snapshot:{type(exc).__name__}"}
    return result


def _compact_activity() -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT id, agent_name, status, started_at, ended_at FROM agent_runs ORDER BY started_at DESC LIMIT 8"
    ).fetchall()
    items = [
        {"id": row["id"], "kind": "run", "agent": row["agent_name"], "status": row["status"], "timestamp": row["started_at"], "headline": f"{row['agent_name']} {row['status']}"}
        for row in rows
    ]
    rows = db.execute(
        "SELECT id, symbol, market, direction_bias, confidence_score, source_agent, created_at, status, substr(thesis, 1, 180) AS excerpt FROM opportunities ORDER BY created_at DESC LIMIT 8"
    ).fetchall()
    items.extend(
        {"id": row["id"], "kind": "opportunity", "symbol": row["symbol"], "market": row["market"], "agent": row["source_agent"], "status": row["status"], "timestamp": row["created_at"], "headline": f"{row['direction_bias'] or 'unknown'} {row['symbol']}", "excerpt": row["excerpt"]}
        for row in rows
    )
    rows = db.execute(
        "SELECT id, symbol, market_type, direction, execution_mode, status, created_at, substr(agent_rationale, 1, 180) AS excerpt FROM trade_proposals ORDER BY created_at DESC LIMIT 8"
    ).fetchall()
    items.extend(
        {"id": row["id"], "kind": "proposal", "symbol": row["symbol"], "market_type": row["market_type"], "direction": row["direction"], "execution_mode": row["execution_mode"], "status": row["status"], "timestamp": row["created_at"], "headline": f"{row['direction']} {row['symbol']}", "excerpt": row["excerpt"]}
        for row in rows
    )
    return sorted(items, key=lambda item: item.get("timestamp") or "", reverse=True)[:16]


def _count_summary() -> dict:
    db = get_db()
    return {
        "pending_proposals": int(db.execute("SELECT COUNT(*) FROM trade_proposals WHERE status = 'pending'").fetchone()[0]),
        "open_opportunities": int(db.execute("SELECT COUNT(*) FROM opportunities WHERE status = 'open'").fetchone()[0]),
        "runs_24h": int(db.execute("SELECT COUNT(*) FROM agent_runs WHERE started_at >= datetime('now', '-1 day')").fetchone()[0]),
    }


@router.get("/shell-bootstrap")
def shell_bootstrap(response: Response):
    started = time.perf_counter()
    cfg_result = _safe("config", lambda: load_config(persist_migrations=False), None)
    cfg = cfg_result["value"]
    if cfg is None:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Server-Timing"] = f"config;dur={(time.perf_counter() - started) * 1000:.1f}"
        return {"schema_version": "ui-shell-v1", "generated_at": _now(), "availability": "unavailable", "reason": cfg_result["reason"]}

    health = _safe("health", _cached_compute_health, {"status": "not_available", "issues": []})
    scheduler = _safe("scheduler", _scheduler_snapshot, {})
    operator = _safe("operator", _operator_snapshot, {})
    try:
        from aletheia.risk import kill_switch

        killed = bool(kill_switch.is_active())
    except Exception:
        killed = bool(cfg.runtime.kill_switch_active)
    ticker = _safe("ticker", _ticker_snapshot, {})
    risk = _safe("risk", lambda: _risk_snapshot(cfg), {"status": "not_evaluated", "violations": []})
    current_commit = _safe("current_commit", _cached_head_commit, None)
    packaged = _safe("packaged_mode", lambda: __import__("aletheia.update.packaged_commands", fromlist=["is_packaged_mode"]).is_packaged_mode(), False)
    payload = {
        "schema_version": "ui-shell-v1",
        "generated_at": _now(),
        "freshness": {"state": "fresh", "max_age_sec": 15},
        "gateway": {"running": True, "paused": bool(cfg.runtime.paused)},
        "scheduler": scheduler["value"],
        "live": {"state": str(cfg.live.state), "enabled": str(cfg.live.state) != "live_disabled"},
        "permission": {"mode": str(cfg.agent.permission_mode)},
        "kill_switch": {"active": killed},
        "risk": risk["value"],
        "operator": operator["value"],
        "connectivity": {"state": "not_available", "reason": "deferred_until_diagnostics"},
        "health": {"status": health["value"].get("status", "not_available"), "issues": health["value"].get("issues", [])[:3], "availability": health["availability"], "reason": health["reason"]},
        "runtime": {"version": __version__, "boot_commit": _safe("boot_commit", lambda: __import__("aletheia.agent_runtime.build_info", fromlist=["get_boot_commit"]).get_boot_commit(), None)["value"], "current_commit": current_commit["value"], "mode": "packaged" if packaged["value"] else "development", "update": _safe("update", lambda: __import__("aletheia.update.applier", fromlist=["update_progress"]).update_progress(), {})["value"]},
        "ticker": ticker["value"],
    }
    response.headers["Cache-Control"] = "no-store"
    response.headers["Server-Timing"] = f"local;dur={(time.perf_counter() - started) * 1000:.1f}"
    return payload


@router.get("/overview-bootstrap")
def overview_bootstrap(response: Response):
    started = time.perf_counter()
    portfolio = _safe("portfolio", lambda: __import__("aletheia.portfolio.pnl", fromlist=["compute_portfolio"]).compute_portfolio(execution_mode=None), {})
    summary = _safe("summary", _count_summary, {"pending_proposals": 0, "open_opportunities": 0, "runs_24h": 0})
    activity = _safe("activity", _compact_activity, [])
    cfg = _safe("config", lambda: load_config(persist_migrations=False), None)
    setup_required = False
    if cfg["value"] is not None:
        setup_required = not bool(getattr(cfg["value"].models, "default_provider", ""))
    p = portfolio["value"] or {}
    account = {key: p.get(key) for key in (
        "total_value_usdt", "account_equity_usdt", "today_pnl", "today_realized_pnl",
        "today_realized_pnl_complete", "unrealized_pnl", "all_realized_pnl",
        "all_realized_pnl_complete", "all_realized_pnl_unavailable_count", "positions_count",
        "exchange_mode", "exchange_sync_status",
    )}
    account["exposure_by_symbol"] = dict(list((p.get("exposure_by_symbol") or {}).items())[:8])
    account["exchange_asset_breakdown"] = list((p.get("exchange_asset_breakdown") or [])[:8])
    if cfg["value"] is not None:
        account["exchange_mode"] = str(cfg["value"].exchange.mode)
        account["execution_mode"] = str(cfg["value"].exchange.paper_execution_mode)
    payload = {
        "schema_version": "ui-overview-v1",
        "generated_at": _now(),
        "freshness": {"state": "fresh", "max_age_sec": 15},
        "availability": "available",
        "account": account,
        "summary": summary["value"],
        "activity": activity["value"],
        "setup_required": setup_required,
        "coverage": {"availability": "unavailable", "reason": "deferred_until_diagnostics"},
        "errors": {key: value["reason"] for key, value in {"portfolio": portfolio, "summary": summary, "activity": activity, "config": cfg}.items() if value["reason"]},
    }
    response.headers["Cache-Control"] = "no-store"
    response.headers["Server-Timing"] = f"local;dur={(time.perf_counter() - started) * 1000:.1f}"
    return payload
