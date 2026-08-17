"""Bounded, local-only dashboard bootstrap view models."""

from __future__ import annotations

from datetime import datetime, timezone
import shutil
import time

from fastapi import APIRouter, Response

from aletheia import __version__
from aletheia.config.exchange_alignment import exchange_alignment
from aletheia.config.secrets import get_secret
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
        if not violations and repo.list_risk_decisions(limit=1):
            status = "healthy"
    return {"status": status, "violations": violations, "kill_switch_active": cfg.runtime.kill_switch_active}


def _model_provider_usable(cfg) -> bool:
    """Return whether at least one configured provider can actually serve work."""
    from aletheia.model_provider.registry import CLI_PROVIDER_TYPES

    default_commands = {"codex_cli": "codex", "claude_cli": "claude", "pi_cli": "pi"}
    for name, provider in cfg.models.providers.items():
        provider_type = getattr(provider, "type", "") or name
        for secret_ref in (
            getattr(provider, "api_key_secret_ref", None),
            getattr(provider, "oauth_token_ref", None),
        ):
            if secret_ref and get_secret(secret_ref):
                return True
        if provider_type in CLI_PROVIDER_TYPES:
            command = getattr(provider, "command", "") or default_commands.get(provider_type, provider_type)
            if shutil.which(command):
                return True
    return False


def _scheduler_snapshot() -> dict:
    try:
        from aletheia.agent_runtime.scheduler import status_snapshot

        return status_snapshot()
    except Exception as exc:
        return {"enabled": None, "running": False, "state": f"unavailable:{type(exc).__name__}"}


def _connectivity_snapshot() -> dict:
    """Read the cached connectivity verdict without starting a network probe."""
    from aletheia.agent_runtime.connectivity import get_status

    status = get_status().get("external_connectivity")
    if isinstance(status, dict):
        return status
    return {
        "state": "unknown",
        "severity": "info",
        "priority": 3,
        "message": "External connectivity status is unavailable.",
        "action": "Wait for the next connectivity probe.",
        "observed_at": None,
    }


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
        "SELECT id, agent_name, trigger_type, status, started_at, ended_at, model_provider, "
        "substr(outputs_created, 1, 400) AS outputs_created, substr(errors, 1, 400) AS errors "
        "FROM agent_runs ORDER BY started_at DESC LIMIT 8"
    ).fetchall()
    items = [
        {"id": row["id"], "kind": "run", "agent": row["agent_name"], "agent_name": row["agent_name"], "trigger_type": row["trigger_type"], "status": row["status"], "timestamp": row["started_at"], "started_at": row["started_at"], "ended_at": row["ended_at"], "model_provider": row["model_provider"], "outputs_created": row["outputs_created"], "errors": row["errors"], "headline": f"{row['agent_name']} {row['status']}"}
        for row in rows
    ]
    rows = db.execute(
        "SELECT id, symbol, market, direction_bias, thesis, confidence, confidence_score, source_agent, created_at, status, "
        "substr(thesis, 1, 180) AS excerpt FROM opportunities ORDER BY created_at DESC LIMIT 8"
    ).fetchall()
    items.extend(
        {"id": row["id"], "kind": "opportunity", "symbol": row["symbol"], "market": row["market"], "direction_bias": row["direction_bias"], "confidence": row["confidence"], "confidence_score": row["confidence_score"], "source_agent": row["source_agent"], "agent": row["source_agent"], "status": row["status"], "timestamp": row["created_at"], "created_at": row["created_at"], "thesis": row["thesis"], "headline": f"{row['direction_bias'] or 'unknown'} {row['symbol']}", "excerpt": row["excerpt"]}
        for row in rows
    )
    rows = db.execute(
        "SELECT id, symbol, market_type, direction, execution_mode, status, created_at, agent_rationale, "
        "substr(agent_rationale, 1, 180) AS excerpt FROM trade_proposals ORDER BY created_at DESC LIMIT 8"
    ).fetchall()
    items.extend(
        {"id": row["id"], "kind": "proposal", "symbol": row["symbol"], "market_type": row["market_type"], "direction": row["direction"], "execution_mode": row["execution_mode"], "status": row["status"], "timestamp": row["created_at"], "created_at": row["created_at"], "agent_rationale": row["agent_rationale"], "headline": f"{row['direction']} {row['symbol']}", "excerpt": row["excerpt"]}
        for row in rows
    )
    return sorted(items, key=lambda item: item.get("timestamp") or "", reverse=True)[:16]


def _count_summary() -> dict:
    db = get_db()
    return {
        "pending_proposals": int(db.execute("SELECT COUNT(*) FROM trade_proposals WHERE status = 'pending'").fetchone()[0]),
        "open_opportunities": int(db.execute("SELECT COUNT(*) FROM opportunities WHERE status = 'open'").fetchone()[0]),
        "runs_24h": int(db.execute("SELECT COUNT(*) FROM agent_runs WHERE replace(started_at, ' ', 'T') >= replace(datetime('now', '-1 day'), ' ', 'T')").fetchone()[0]),
    }


def _project_overview_account(portfolio: dict, cfg) -> dict:
    """Keep the compact account read model in parity with PnLZone's inputs."""
    fields = (
        "total_value_usdt", "account_equity_usdt", "position_notional_usdt",
        "today_pnl", "today_realized_pnl", "today_realized_pnl_complete",
        "today_realized_pnl_unavailable_count", "unrealized_pnl", "realized_pnl",
        "realized_pnl_complete", "realized_pnl_unavailable_count", "all_realized_pnl",
        "all_realized_pnl_complete", "all_realized_pnl_unavailable_count",
        "all_historical_reconciled_pnl", "all_historical_reconciled_count",
        "all_historical_reconciliation_unavailable_count", "positions_count",
        "exchange_equity_usdt", "exchange_state_age_sec", "exchange_sync_status",
    )
    account = {key: portfolio.get(key) for key in fields}
    account["exposure_by_symbol"] = dict(list((portfolio.get("exposure_by_symbol") or {}).items())[:8])
    account["today_pnl_by_symbol"] = (
        dict(list((portfolio.get("today_pnl_by_symbol") or {}).items())[:8])
        if portfolio.get("today_realized_pnl_complete") is not False
        else {}
    )
    account["unrealized_by_symbol"] = dict(list((portfolio.get("unrealized_by_symbol") or {}).items())[:8])
    account["exchange_asset_breakdown"] = list((portfolio.get("exchange_asset_breakdown") or [])[:8])

    uta = portfolio.get("uta_state")
    if isinstance(uta, dict):
        age = uta.get("age_seconds")
        raw_errors = uta.get("errors") or []
        if isinstance(raw_errors, dict):
            uta_errors = [f"{key}: {value}" for key, value in list(raw_errors.items())[:8]]
        elif isinstance(raw_errors, (list, tuple)):
            uta_errors = [str(error) for error in raw_errors[:8]]
        else:
            uta_errors = [str(raw_errors)]
        try:
            sync_status = uta.get("sync_status") or ("ok" if age is not None and float(age) <= 600 else "stale")
        except (TypeError, ValueError):
            sync_status = "stale"
        if uta_errors:
            try:
                sync_status = "partial" if age is not None and float(age) <= 600 else "stale"
            except (TypeError, ValueError):
                sync_status = "stale"
        account["uta_state"] = {
            "sync_status": sync_status,
            "total_equity_usdt": uta.get("total_equity_usdt"),
            "total_available_usdt": uta.get("total_available_usdt"),
            "total_unrealized_pnl": uta.get("total_unrealized_pnl"),
            "errors": uta_errors,
            "age_seconds": age,
            "fetched_at": uta.get("fetched_at"),
            "accounts": [
                {key: item.get(key) for key in ("broker_id", "display_name", "equity_usdt", "available_usdt", "unrealized_pnl")}
                for item in (uta.get("accounts") or [])[:8]
                if isinstance(item, dict)
            ],
        }
        if uta_errors:
            # A fresh UTA cache can still be incomplete. Do not let a partial
            # aggregate fall through to the account-value or unrealized tiles.
            for key in ("total_value_usdt", "account_equity_usdt", "exchange_equity_usdt", "unrealized_pnl"):
                account[key] = None
            account["exchange_asset_breakdown"] = []
            account["exchange_sync_status"] = sync_status
    else:
        account["uta_state"] = None

    if cfg is not None:
        account["exchange_mode"] = str(cfg.exchange.mode)
        account["execution_mode"] = str(cfg.exchange.paper_execution_mode)
    else:
        account["exchange_mode"] = portfolio.get("exchange_mode")
        account["execution_mode"] = portfolio.get("execution_mode")
    if account["execution_mode"] != "paper_internal":
        # The local position map cannot explain an external account headline.
        account["unrealized_by_symbol"] = {}
    return account


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
    connectivity = _safe("connectivity", _connectivity_snapshot, {
        "state": "unknown",
        "severity": "info",
        "priority": 3,
        "message": "External connectivity status is unavailable.",
        "action": "Wait for the next connectivity probe.",
        "observed_at": None,
    })
    risk = _safe("risk", lambda: _risk_snapshot(cfg), {"status": "not_evaluated", "violations": []})
    alignment = _safe("exchange_alignment", lambda: exchange_alignment(cfg).to_dict(), {
        "status": "not_available",
        "execution_exchange": None,
        "data_exchange": None,
        "diverged": False,
        "paper_execution_mode": None,
    })
    alignment_value = alignment["value"]
    connectivity_value = dict(connectivity["value"])
    if connectivity["reason"]:
        connectivity_value.update({"state": "unknown", "reason": connectivity["reason"]})
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
        "exchange": {
            "status": alignment_value.get("status"),
            "execution_exchange": alignment_value.get("execution_exchange"),
            "data_exchange": alignment_value.get("data_exchange"),
            "exchange_diverged": bool(alignment_value.get("diverged")),
            "paper_execution_mode": alignment_value.get("paper_execution_mode"),
        },
        "operator": operator["value"],
        "connectivity": connectivity_value,
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
    setup = _safe("setup", lambda: _model_provider_usable(cfg["value"]) if cfg["value"] is not None else False, False)
    setup_required = not bool(setup["value"])
    p = portfolio["value"] or {}
    account = _project_overview_account(p, cfg["value"])
    errors = {key: value["reason"] for key, value in {"portfolio": portfolio, "summary": summary, "activity": activity, "config": cfg, "setup": setup}.items() if value["reason"]}
    payload = {
        "schema_version": "ui-overview-v1",
        "generated_at": _now(),
        "freshness": {"state": "fresh", "max_age_sec": 15},
        "availability": "partial" if errors else "available",
        "account": account,
        "summary": summary["value"],
        "activity": activity["value"],
        "setup_required": setup_required,
        "coverage": {"availability": "deferred", "reason": "deferred_until_diagnostics"},
        "errors": errors,
    }
    response.headers["Cache-Control"] = "no-store"
    response.headers["Server-Timing"] = f"local;dur={(time.perf_counter() - started) * 1000:.1f}"
    return payload
