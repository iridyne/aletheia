"""Settings API routes."""

import asyncio
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, field_validator

from aletheia.config.loader import load_config, save_config
from aletheia.config.schema import ModeRiskPolicy, default_mode_risk_policy
from aletheia.risk.policy import risk_policy_hash

router = APIRouter(prefix="/api/settings", tags=["settings"])

# Worker/agent permission surface (cfg.agent.permission_mode). Distinct from the
# Operator surface (cfg.operator.permission_mode: ReadOnly/Research/PaperAuto/
# AllowEditing/Auto) — this endpoint correctly governs only the worker side, so
# the 3-value allowlist is intentional, not a bug (CR-AR-02 was a false positive).
VALID_PERMISSION_MODES = {"Observe", "Paper", "Full"}


def _notify_ranker_config_change() -> None:
    """Wake Ranker after a committed config mutation that changes eligibility."""
    try:
        from aletheia.agent_runtime.scheduler import notify_paper_trader

        notify_paper_trader(source="ranker_config_changed")
    except Exception:
        # Config persistence is authoritative; wake telemetry is best-effort and
        # the three-minute Ranker heartbeat remains the fallback.
        pass


def _reject_live_identity_change(cfg, *, changed: bool) -> None:
    """Keep an unlocked account bound to the exact selected configuration."""
    if changed and getattr(cfg.live, "state", "") == "live_limited":
        raise HTTPException(
            409,
            detail={
                "error": "live_unlock_identity_conflict",
                "message": "Lock live trading and re-run the selected-account preflight before changing its identity.",
            },
        )


# --- Inline-secret redaction ---------------------------------------------------
# Secrets must never leave the backend via a GET response, an echoed POST body,
# or an audit record. `config.yaml` can hold credential-bearing fields (a proxy
# URL with user:pass, or the legacy market_data.coinalyze_api_key), so every
# surface that serializes those config sections runs them through these helpers.
def _mask_secret(value):
    """Mask a secret-bearing string for display, keeping only the last 4 chars."""
    if not value:
        return value
    s = str(value)
    return f"***{s[-4:]}" if len(s) > 4 else "***"


def _redact_proxy_url(url):
    """Strip embedded credentials from a proxy URL: scheme://user:pass@host -> scheme://***@host."""
    if not url:
        return url
    try:
        from urllib.parse import urlsplit, urlunsplit
        parts = urlsplit(str(url))
        if parts.username or parts.password:
            host = parts.hostname or ""
            if parts.port:
                host = f"{host}:{parts.port}"
            return urlunsplit((parts.scheme, f"***@{host}", parts.path, parts.query, parts.fragment))
    except Exception:  # noqa: BLE001 — never let redaction raise into a response
        return "***"
    return url


def _redacted_market_data(md: dict) -> dict:
    md = dict(md)
    if md.get("coinalyze_api_key"):
        md["coinalyze_api_key"] = _mask_secret(md["coinalyze_api_key"])
    return md


def _redacted_network(net: dict) -> dict:
    net = dict(net)
    if net.get("proxy_url"):
        net["proxy_url"] = _redact_proxy_url(net["proxy_url"])
    return net


def _redacted_webhook(wh: dict) -> dict:
    wh = dict(wh)
    if wh.get("secret"):
        wh["secret"] = _mask_secret(wh["secret"])
    return wh


class SettingsRequest(BaseModel):
    """Strict request body for Settings mutations.

    The dashboard should fail loudly when a control sends a stale or misspelled
    config key. Pydantic's default `extra=ignore` would otherwise make a UI/API
    mismatch look like a successful save while persisting nothing.
    """

    model_config = ConfigDict(extra="forbid")


class PermissionModeRequest(SettingsRequest):
    mode: str


class ExchangeModeRequest(SettingsRequest):
    mode: str


class TradingMandateRequest(SettingsRequest):
    mandate: dict[str, Any] | None


def _trading_mandate_view(cfg) -> dict[str, Any] | None:
    value = getattr(cfg, "trading_mandate", None)
    if value is None:
        return None
    from aletheia.trading.trading_mandate import resolve_trading_mandate

    try:
        resolution = resolve_trading_mandate(cfg)
    except Exception:
        resolution = None
    if resolution is not None and resolution.mandate is not None:
        return {
            "enabled": True,
            "resolution_status": "active",
            **resolution.mandate.model_dump(mode="json"),
        }

    raw = value.model_dump(mode="json") if isinstance(value, BaseModel) else {}
    view: dict[str, Any] = {
        "resolution_status": (
            resolution.status if resolution is not None else "mandate_unavailable"
        ),
    }
    for key in (
        "enabled",
        "contract_version",
        "mandate_id",
        "mandate_version",
        "status",
        "enforcement_mode",
    ):
        item = raw.get(key)
        if key in raw and (item is None or isinstance(item, (str, int, float, bool))):
            view[key] = item
    if resolution is not None and resolution.validation_errors:
        view["validation_errors"] = list(resolution.validation_errors)
    return view


@router.get("")
def get_settings():
    cfg = load_config()
    from aletheia.learning.registry import paper_influence_path_options
    providers_safe = {}
    for name, p in cfg.models.providers.items():
        providers_safe[name] = {
            "type": p.type,
            "base_url": p.base_url,
            "protocol": p.protocol,
            "model": p.model,
            "enabled": p.enabled,
            "command": p.command,
            "mode": p.mode,
            "codex_model": p.codex_model,
            "workspace_url": p.workspace_url,
            "auth_scheme": p.auth_scheme,
            "auth_type": p.auth_type,
            "oauth_expires_at": p.oauth_expires_at,
            "model_fallback_enabled": p.model_fallback_enabled,
            "fallback_models": list(p.fallback_models or []),
            "inference_max_concurrency": p.inference_max_concurrency,
            "inference_requests_per_minute": p.inference_requests_per_minute,
            "inference_tpm": p.inference_tokens_per_minute,
            "inference_queue_capacity": p.inference_queue_capacity,
            "inference_reserved_high_priority_slots": p.inference_reserved_high_priority_slots,
            "inference_timeout_sec": p.inference_timeout_sec,
        }
    return {
        "app": cfg.app.model_dump(mode="json"),
        "runtime": cfg.runtime.model_dump(mode="json"),
        "exchange": cfg.exchange.model_dump(mode="json"),
        "live": cfg.live.model_dump(mode="json"),
        "agent": cfg.agent.model_dump(mode="json"),
        "operator": cfg.operator.model_dump(mode="json"),
        "models": {
            "default_provider": cfg.models.default_provider,
            "deep_provider": cfg.models.deep_provider,
            "fast_provider": cfg.models.fast_provider,
            "providers": providers_safe,
        },
        "risk": cfg.risk.model_dump(mode="json"),
        "update": cfg.update.model_dump(mode="json"),
        "trading": cfg.trading.model_dump(mode="json"),
        "health": cfg.health.model_dump(mode="json"),
        "retention": cfg.retention.model_dump(mode="json"),
        "telegram": cfg.telegram.model_dump(mode="json"),
        "webhook": {k: v for k, v in cfg.webhook.model_dump(mode="json").items() if k != "secret"},
        "market_data": _redacted_market_data(cfg.market_data.model_dump(mode="json")),
        "universe": cfg.universe.model_dump(mode="json"),
        "guard": cfg.guard.model_dump(mode="json"),
        "tunnel": {k: v for k, v in cfg.tunnel.model_dump(mode="json").items()
                   if k != "credentials_path"},
        "remote_control": cfg.remote_control.model_dump(mode="json"),
        "regime": cfg.regime.model_dump(mode="json"),
        "network": _redacted_network(cfg.network.model_dump(mode="json")),
        "strategy_factory": {
            **cfg.strategy_factory.model_dump(mode="json"),
            "_deprecated": True,
            "_reason": (
                "Strategy Factory subsystem removed with Position C (2026-07-02). "
                "All fields are compatibility-only with no production consumer."
            ),
        },
        "historical_data": cfg.historical_data.model_dump(mode="json"),
        "event_triggers": cfg.event_triggers.model_dump(mode="json"),
        "pi_daemon": cfg.pi_daemon.model_dump(mode="json"),
        "news": cfg.news.model_dump(mode="json"),
        "serenity": cfg.serenity.model_dump(mode="json"),
        "controller": cfg.controller.model_dump(mode="json"),
        "learning": cfg.learning.model_dump(mode="json"),
        "economics": cfg.economics.model_dump(mode="json"),
        # Read-only visibility keeps the config schema honest; unknown mandate
        # fields are intentionally omitted so this GET cannot echo credentials.
        "trading_mandate": _trading_mandate_view(cfg),
        "learning_path_options": list(paper_influence_path_options()),
    }


@router.post("/trading-mandate")
async def set_trading_mandate(req: TradingMandateRequest):
    from aletheia.trading.trading_mandate import TradingMandateConfig, resolve_trading_mandate

    candidate = TradingMandateConfig.model_validate(
        {"enabled": False} if req.mandate is None else req.mandate
    )
    resolution = resolve_trading_mandate(candidate)
    if resolution.status == "mandate_unavailable":
        raise HTTPException(
            422,
            detail={
                "code": "trading_mandate_invalid",
                "validation_errors": list(resolution.validation_errors),
            },
        )
    if resolution.status == "not_configured":
        candidate = TradingMandateConfig(enabled=False)

    cfg = load_config()
    before = _trading_mandate_view(cfg)
    cfg.trading_mandate = candidate
    save_config(cfg)
    after = _trading_mandate_view(cfg)
    from aletheia.files.jsonl import write_audit

    write_audit({
        "actor": "human",
        "source": "dashboard",
        "event_type": "trading_mandate_changed",
        "entity_type": "trading_mandate",
        "before": before,
        "after": after,
        "message": "observe-only trading mandate updated",
    })
    return {"trading_mandate": after}


# ---------------------------------------------------------------------------
# Local paper account (No exchange only)
# ---------------------------------------------------------------------------

class PaperAccountAdjustmentRequest(SettingsRequest):
    amount_usdt: FiniteFloat
    note: str = ""


class PaperAccountSetBalanceRequest(SettingsRequest):
    cash_balance_usdt: FiniteFloat
    note: str = ""


def _require_local_paper_mode() -> None:
    if load_config().exchange.paper_execution_mode != "paper_internal":
        raise HTTPException(409, "Local paper account controls require Paper (internal) / No exchange mode")


def _audit_paper_account_change(action: str, before: dict, after: dict, note: str) -> None:
    from aletheia.files.jsonl import write_audit
    cfg = load_config()
    tunnel_active = getattr(getattr(cfg, "tunnel", None), "enabled", False)
    source = "tunnel_dashboard" if tunnel_active else "dashboard"
    write_audit({
        "actor": "human", "source": source, "event_type": f"paper_account_{action}",
        "entity_type": "paper_account", "before": before, "after": after,
        "message": f"Local paper account {action}: {note or 'manual operator action'}",
    })


@router.get("/paper-account")
async def get_paper_account():
    def _read():
        _require_local_paper_mode()
        from aletheia.portfolio.paper_account import get_paper_account as _get
        return _get()
    return await asyncio.to_thread(_read)


@router.post("/paper-account/credit")
async def credit_paper_account(req: PaperAccountAdjustmentRequest):
    if req.amount_usdt <= 0:
        raise HTTPException(400, "amount_usdt must be positive")

    def _credit():
        _require_local_paper_mode()
        from aletheia.portfolio.paper_account import adjust_cash, get_paper_account as _get
        before = _get(event_limit=0)
        try:
            after = adjust_cash(req.amount_usdt, note=req.note)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        _audit_paper_account_change("credit", before, after, req.note)
        return after
    return await asyncio.to_thread(_credit)


@router.post("/paper-account/debit")
async def debit_paper_account(req: PaperAccountAdjustmentRequest):
    if req.amount_usdt <= 0:
        raise HTTPException(400, "amount_usdt must be positive")

    def _debit():
        _require_local_paper_mode()
        from aletheia.portfolio.paper_account import adjust_cash, get_paper_account as _get
        before = _get(event_limit=0)
        try:
            after = adjust_cash(-req.amount_usdt, note=req.note)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        _audit_paper_account_change("debit", before, after, req.note)
        return after
    return await asyncio.to_thread(_debit)


@router.post("/paper-account/set-balance")
async def set_paper_account_balance(req: PaperAccountSetBalanceRequest):
    if req.cash_balance_usdt < 0:
        raise HTTPException(400, "cash_balance_usdt must be non-negative")

    def _set_balance():
        _require_local_paper_mode()
        from aletheia.portfolio.paper_account import set_cash_balance, get_paper_account as _get
        before = _get(event_limit=0)
        after = set_cash_balance(req.cash_balance_usdt, note=req.note)
        _audit_paper_account_change("set_balance", before, after, req.note)
        return after
    return await asyncio.to_thread(_set_balance)


def _close_local_positions(market_type: str | None) -> dict:
    _require_local_paper_mode()
    from aletheia.portfolio.position_manager import (
        LocalPaperClosePreflightError,
        close_local_positions_batch,
    )

    cfg = load_config()
    tunnel_active = getattr(getattr(cfg, "tunnel", None), "enabled", False)
    decision_source = "tunnel_dashboard" if tunnel_active else "dashboard"
    try:
        result = close_local_positions_batch(
            market_type,
            reason="manual_bulk_close",
            decision_actor="human",
            decision_source=decision_source,
        )
    except LocalPaperClosePreflightError as exc:
        raise HTTPException(
            409,
            detail={
                "code": "bulk_close_mark_preflight_failed",
                "closed_position_ids": [],
                "failed": exc.failures,
            },
        ) from exc

    from aletheia.portfolio.paper_account import get_paper_account as _get
    after = _get()
    action = "close_spot_positions" if market_type == "spot" else "close_all_positions"
    if result["failed"]:
        action += "_partial"
    _audit_paper_account_change(
        action,
        {"open_positions_count": result["requested_count"]},
        {
            "closed_position_ids": result["closed_position_ids"],
            "failed": result["failed"],
            **after,
        },
        "Manual local paper position close",
    )
    response = {**result, "account": after}
    if result["failed"]:
        raise HTTPException(
            500,
            detail={"code": "bulk_close_partial_failure", **response},
        )
    return response


@router.post("/paper-account/close-all")
async def close_all_local_paper_positions():
    return await asyncio.to_thread(_close_local_positions, None)


@router.post("/paper-account/close-spot")
async def close_local_paper_spot_positions():
    return await asyncio.to_thread(_close_local_positions, "spot")


# ---------------------------------------------------------------------------
# Agent config
# ---------------------------------------------------------------------------

class AutonomousLearningRequest(SettingsRequest):
    enabled: bool


@router.post("/autonomous-learning")
async def set_autonomous_learning(req: AutonomousLearningRequest):
    cfg = load_config()
    old = cfg.agent.autonomous_learning
    cfg.agent.autonomous_learning = req.enabled
    save_config(cfg)
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "autonomous_learning_changed",
                 "entity_type": "agent_config", "before": {"autonomous_learning": old},
                 "after": {"autonomous_learning": req.enabled},
                 "message": f"autonomous_learning {old} -> {req.enabled}"})
    return {"old": old, "new": req.enabled}


class BypassDistressGateRequest(SettingsRequest):
    enabled: bool


@router.post("/bypass-distress-gate")
async def set_bypass_distress_gate(req: BypassDistressGateRequest):
    cfg = load_config()
    old = cfg.agent.bypass_distress_gate
    cfg.agent.bypass_distress_gate = req.enabled
    save_config(cfg)
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "bypass_distress_gate_changed",
                 "entity_type": "agent_config", "before": {"bypass_distress_gate": old},
                 "after": {"bypass_distress_gate": req.enabled},
                 "message": f"bypass_distress_gate {old} -> {req.enabled}"})
    return {"old": old, "new": req.enabled}


# ---------------------------------------------------------------------------
# Exchange markets config
# ---------------------------------------------------------------------------

class ExchangeMarketsRequest(SettingsRequest):
    spot: bool
    usdt_futures: bool
    paper_execution_mode: Optional[str] = None
    confirm_exchange_divergence: bool = False


def _require_exchange_divergence_confirmation(alignment, confirmed: bool) -> None:
    if not alignment.diverged or confirmed:
        return
    raise HTTPException(
        409,
        detail={
            "code": "exchange_source_divergence_confirmation_required",
            "message": (
                "Market data and order execution would use different exchanges. "
                "Confirm this intentional divergence before saving."
            ),
            "execution_exchange": alignment.execution_exchange,
            "data_exchange": alignment.data_exchange,
        },
    )


@router.post("/exchange-markets")
async def set_exchange_markets(req: ExchangeMarketsRequest):
    if not req.spot and not req.usdt_futures:
        raise HTTPException(400, "At least one market must be enabled")
    if req.paper_execution_mode is not None and req.paper_execution_mode not in {"bitget_demo", "paper_internal", "binance_testnet", "okx_demo", "binance_live", "okx_live"}:
        raise HTTPException(400, "paper_execution_mode must be one of: bitget_demo, paper_internal, binance_testnet, okx_demo, binance_live, okx_live")
    cfg = load_config()
    old = cfg.exchange.model_dump(mode="json")
    next_paper_mode = req.paper_execution_mode or cfg.exchange.paper_execution_mode
    _reject_live_identity_change(
        cfg,
        changed=(req.paper_execution_mode is not None and next_paper_mode != cfg.exchange.paper_execution_mode),
    )
    from aletheia.config.exchange_alignment import exchange_alignment_for_values
    alignment = exchange_alignment_for_values(
        paper_execution_mode=next_paper_mode,
        data_exchange=cfg.market_data.ccxt_exchange,
        configured_exchange=cfg.exchange.name,
    )
    if req.paper_execution_mode is not None and req.paper_execution_mode != cfg.exchange.paper_execution_mode:
        _require_exchange_divergence_confirmation(
            alignment, req.confirm_exchange_divergence,
        )
    cfg.exchange.enabled_markets = {"spot": req.spot, "usdt_futures": req.usdt_futures}
    if req.paper_execution_mode is not None:
        cfg.exchange.paper_execution_mode = req.paper_execution_mode
    save_config(cfg)
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "exchange_markets_changed",
                 "entity_type": "exchange_config", "before": old,
                 "after": cfg.exchange.model_dump(mode="json"),
                 "message": "exchange config updated"})
    if old != cfg.exchange.model_dump(mode="json"):
        _notify_ranker_config_change()
    return {**cfg.exchange.model_dump(mode="json"), "exchange_alignment": alignment.to_dict()}


# ---------------------------------------------------------------------------
# Primary exchange unification
# ---------------------------------------------------------------------------

from aletheia.config.exchanges import EXCHANGE_UNIFICATION_MAP as _EXCHANGE_UNIFICATION_MAP


class PrimaryExchangeRequest(SettingsRequest):
    exchange_id: str


@router.post("/primary-exchange")
async def set_primary_exchange(req: PrimaryExchangeRequest):
    """Atomically set primary exchange across data, execution, and risk axes."""
    eid = req.exchange_id.strip().lower()
    valid = set(_EXCHANGE_UNIFICATION_MAP.keys()) | {"paper_internal"}
    if eid not in valid:
        raise HTTPException(400, f"exchange_id must be one of: {sorted(valid)}")

    cfg = load_config()
    desired_paper_mode = (
        "paper_internal"
        if eid == "paper_internal"
        else _EXCHANGE_UNIFICATION_MAP[eid][1]
    )
    _reject_live_identity_change(
        cfg,
        changed=(
            eid != str(cfg.exchange.name or "").lower()
            or desired_paper_mode != cfg.exchange.paper_execution_mode
        ),
    )
    old = {
        "exchange.name": cfg.exchange.name,
        "exchange.paper_execution_mode": cfg.exchange.paper_execution_mode,
        "market_data.ccxt_exchange": cfg.market_data.ccxt_exchange,
        "risk.allowed_exchange": cfg.risk.allowed_exchange,
    }

    if eid == "paper_internal":
        cfg.exchange.paper_execution_mode = "paper_internal"
    else:
        ccxt_ex, paper_mode = _EXCHANGE_UNIFICATION_MAP[eid]
        cfg.exchange.name = eid
        cfg.market_data.ccxt_exchange = ccxt_ex
        cfg.market_data.ccxt_fallback = ccxt_ex
        cfg.exchange.paper_execution_mode = paper_mode
        cfg.risk.allowed_exchange = eid

    save_config(cfg)
    after = {
        "exchange.name": cfg.exchange.name,
        "exchange.paper_execution_mode": cfg.exchange.paper_execution_mode,
        "market_data.ccxt_exchange": cfg.market_data.ccxt_exchange,
        "risk.allowed_exchange": cfg.risk.allowed_exchange,
    }
    from aletheia.files.jsonl import write_audit
    write_audit({
        "actor": "human", "source": "dashboard",
        "event_type": "primary_exchange_changed",
        "entity_type": "exchange_config",
        "before": old, "after": after,
        "message": f"Primary exchange set to {eid}",
    })
    if old != after:
        _notify_ranker_config_change()
    return {"exchange_id": eid, **after}


# ---------------------------------------------------------------------------
# Runtime config
# ---------------------------------------------------------------------------

class RuntimeConfigUpdate(SettingsRequest):
    scheduler_enabled: Optional[bool] = None
    git_enabled: Optional[bool] = None
    auto_reload: Optional[bool] = None
    runtime_continuity_enabled: Optional[bool] = None
    runtime_continuity_lock_ttl_sec: Optional[int] = None
    runtime_continuity_shadow_port_offset: Optional[int] = None


class EconomicsConfigUpdate(SettingsRequest):
    enabled: Optional[bool] = None
    budgets_shadow_enabled: Optional[bool] = None
    prompt_guardrail_detection_enabled: Optional[bool] = None


@router.post("/economics")
def update_economics_config(req: EconomicsConfigUpdate):
    """Persist the default-off Phase 2 shadow-observation controls."""
    cfg = load_config()
    old = cfg.economics.model_dump(mode="json")
    updates = req.model_dump(exclude_none=True)
    for field, value in updates.items():
        setattr(cfg.economics, field, value)
    if not cfg.economics.enabled:
        cfg.economics.budgets_shadow_enabled = False
    save_config(cfg)
    from aletheia.files.jsonl import write_audit
    write_audit({
        "actor": "human", "source": "dashboard",
        "event_type": "economics_config_changed", "entity_type": "economics_config",
        "before": old, "after": cfg.economics.model_dump(mode="json"),
        "message": f"Economics config updated: {list(updates.keys())}",
    })
    return {"updated": updates, "economics": cfg.economics.model_dump(mode="json")}


@router.post("/runtime")
async def update_runtime_config(req: RuntimeConfigUpdate):
    cfg = load_config()
    old = cfg.runtime.model_dump(mode="json")
    updates = req.model_dump(exclude_none=True)
    for field, value in updates.items():
        if field == "runtime_continuity_lock_ttl_sec" and not (30 <= int(value) <= 3600):
            raise HTTPException(400, "runtime_continuity_lock_ttl_sec must be between 30 and 3600")
        if field == "runtime_continuity_shadow_port_offset" and not (1 <= int(value) <= 10000):
            raise HTTPException(400, "runtime_continuity_shadow_port_offset must be between 1 and 10000")
        setattr(cfg.runtime, field, value)
    save_config(cfg)
    # Notify APScheduler if scheduler_enabled changed. The public resume entry
    # owns the durable pause-owner check, so Settings cannot bypass a gate lease.
    if "scheduler_enabled" in updates:
        try:
            from aletheia.agent_runtime.scheduler import pause_scheduler, resume_scheduler
            if cfg.runtime.scheduler_enabled:
                resume_scheduler()
            else:
                pause_scheduler()
        except Exception:
            pass
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "runtime_config_changed",
                 "entity_type": "runtime_config", "before": old,
                 "after": cfg.runtime.model_dump(mode="json"),
                 "message": f"Runtime config updated: {list(updates.keys())}"})
    return {"updated": updates}


# ---------------------------------------------------------------------------
# App config
# ---------------------------------------------------------------------------

_VALID_BIND_HOSTS = {"0.0.0.0", "::", "127.0.0.1", "localhost", "::1"}


class AppConfigUpdate(SettingsRequest):
    auto_open_browser: Optional[bool] = None
    equity_curve_mode: Optional[str] = None
    bind_host: Optional[str] = None

    @field_validator("equity_curve_mode")
    @classmethod
    def _validate_equity_curve_mode(cls, v):
        if v is not None and v not in ("full_cumulative", "window_relative"):
            raise ValueError("equity_curve_mode must be 'full_cumulative' or 'window_relative'")
        return v

    @field_validator("bind_host")
    @classmethod
    def _validate_bind_host(cls, v):
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("bind_host cannot be empty")
        if v not in _VALID_BIND_HOSTS:
            import re
            _ipv4 = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")
            if not _ipv4.match(v):
                raise ValueError(
                    f"bind_host must be one of {sorted(_VALID_BIND_HOSTS)} or a valid IPv4 address"
                )
        return v


@router.post("/app")
async def update_app_config(req: AppConfigUpdate):
    cfg = load_config()
    old = cfg.app.model_dump(mode="json")
    updates = req.model_dump(exclude_none=True)
    if "bind_host" in updates:
        raise HTTPException(
            400,
            "bind_host is managed by Remote Control network-adapter selection",
        )
    for field, value in updates.items():
        setattr(cfg.app, field, value)
    save_config(cfg)
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "app_config_changed",
                 "entity_type": "app_config", "before": old,
                 "after": cfg.app.model_dump(mode="json"),
                 "message": f"App config updated: {list(updates.keys())}"})
    return {"updated": updates}


# ---------------------------------------------------------------------------
# PI Daemon config
# ---------------------------------------------------------------------------

class PiDaemonConfigUpdate(SettingsRequest):
    enabled: Optional[bool] = None
    debounce_sec: Optional[float] = None
    cooldown_sec: Optional[float] = None
    heartbeat_sec: Optional[float] = None


_PI_DAEMON_BOUNDS: dict[str, tuple[float, float]] = {
    "debounce_sec": (0.0, 60.0),
    "cooldown_sec": (0.0, 600.0),
    "heartbeat_sec": (60.0, 3600.0),
}


@router.post("/pi_daemon")
async def update_pi_daemon_config(req: PiDaemonConfigUpdate):
    """Update System Operator daemon configuration.

    #97 note: the path `/pi_daemon`, the audit event `pi_daemon_config_changed`,
    and the entity type `pi_daemon_config` are LEGACY names preserved for
    backwards-compat with existing configs and audit trails. The subsystem is
    the System Operator daemon (see PiDaemonConfig docstring for the full
    naming registry). Any consumer parsing audit events must accept the legacy
    keys; new UI copy uses "System Operator daemon".
    """
    cfg = load_config()
    old = cfg.pi_daemon.model_dump(mode="json")
    updates = req.model_dump(exclude_none=True)
    for field, value in updates.items():
        if field in _PI_DAEMON_BOUNDS:
            lo, hi = _PI_DAEMON_BOUNDS[field]
            if not (lo <= value <= hi):
                raise HTTPException(400, f"'{field}' must be between {lo} and {hi}")
        setattr(cfg.pi_daemon, field, value)
    save_config(cfg)
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "pi_daemon_config_changed",
                 "entity_type": "pi_daemon_config", "before": old,
                 "after": cfg.pi_daemon.model_dump(mode="json"),
                 "message": f"System Operator daemon config updated: {list(updates.keys())}"})

    # Apply the runtime transition immediately so the running daemon reflects
    # the saved config without requiring a gateway restart.
    runtime_status: dict | None = None
    affects_runtime = bool(updates)  # any field change can affect daemon state
    if affects_runtime:
        try:
            from aletheia.agent_runtime.pi_daemon import reconfigure_pi_daemon
            loop = asyncio.get_event_loop()
            runtime_status = await reconfigure_pi_daemon(
                enabled=cfg.pi_daemon.enabled,
                debounce_sec=cfg.pi_daemon.debounce_sec,
                cooldown_sec=cfg.pi_daemon.cooldown_sec,
                heartbeat_sec=cfg.pi_daemon.heartbeat_sec,
                loop=loop,
            )
        except Exception as exc:
            runtime_status = {
                "configured": cfg.pi_daemon.enabled,
                "running": False,
                "blocker": f"reconfigure_error:{exc}",
            }

    response: dict = {"updated": updates}
    if runtime_status is not None:
        response["runtime_status"] = runtime_status
    return response


# ---------------------------------------------------------------------------
# Serenity Research config (Phase 3, Option A D5 switch)
# ---------------------------------------------------------------------------

class SerenityConfigUpdate(SettingsRequest):
    enabled: Optional[bool] = None
    auto_promote: Optional[bool] = None
    min_score_for_pi: Optional[int] = None
    stop_loss_pct: Optional[float] = None
    max_hold_days: Optional[int] = None


_SERENITY_BOUNDS: dict[str, tuple[float, float]] = {
    "min_score_for_pi": (0, 100),
    "stop_loss_pct": (0.1, 50.0),
    "max_hold_days": (1, 365),
}


@router.post("/serenity")
async def update_serenity_config(req: SerenityConfigUpdate):
    cfg = load_config()
    old = cfg.serenity.model_dump(mode="json")
    updates = req.model_dump(exclude_none=True)
    for field, value in updates.items():
        if field in _SERENITY_BOUNDS:
            lo, hi = _SERENITY_BOUNDS[field]
            if not (lo <= value <= hi):
                raise HTTPException(400, f"'{field}' must be between {lo} and {hi}")
        setattr(cfg.serenity, field, value)
    save_config(cfg)
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "serenity_config_changed",
                 "entity_type": "serenity_config", "before": old,
                 "after": cfg.serenity.model_dump(mode="json"),
                 "message": f"Serenity config updated: {list(updates.keys())}"})
    return {"updated": updates}


# ---------------------------------------------------------------------------
# Filter feedback controller config (Phase 1 shadow)
# ---------------------------------------------------------------------------

class ControllerConfigUpdate(SettingsRequest):
    shadow_enabled: Optional[bool] = None
    observe_interval_hours: Optional[int] = None
    min_samples_gate: Optional[int] = None
    max_step: Optional[float] = None


_CONTROLLER_BOUNDS: dict[str, tuple[float, float]] = {
    "observe_interval_hours": (1, 168),
    "min_samples_gate": (5, 500),
    "max_step": (0.01, 0.5),
}


@router.post("/controller")
async def update_controller_config(req: ControllerConfigUpdate):
    cfg = load_config()
    old = cfg.controller.model_dump(mode="json")
    updates = req.model_dump(exclude_none=True)
    for field, value in updates.items():
        if field in _CONTROLLER_BOUNDS:
            lo, hi = _CONTROLLER_BOUNDS[field]
            if not (lo <= value <= hi):
                raise HTTPException(400, f"'{field}' must be between {lo} and {hi}")
        setattr(cfg.controller, field, value)
    save_config(cfg)

    _CONTROLLER_SCHEDULER_FIELDS = {"shadow_enabled", "observe_interval_hours"}
    rescheduled = False
    if _CONTROLLER_SCHEDULER_FIELDS.intersection(updates):
        from aletheia.agent_runtime.scheduler import reconcile_filter_controller
        from starlette.concurrency import run_in_threadpool
        rescheduled = await run_in_threadpool(
            reconcile_filter_controller,
            cfg.controller.shadow_enabled,
            cfg.controller.observe_interval_hours,
        )

    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "controller_config_changed",
                 "entity_type": "controller_config", "before": old,
                 "after": cfg.controller.model_dump(mode="json"),
                 "message": f"Controller config updated: {list(updates.keys())}"})
    return {"updated": updates, "rescheduled": rescheduled}


# ---------------------------------------------------------------------------
# Paper Learning experiment controls (AT-02)
# ---------------------------------------------------------------------------

class LearningConfigUpdate(SettingsRequest):
    contribution_evaluation_enabled: Optional[bool] = None
    lifecycle_automation_enabled: Optional[bool] = None
    paper_influence_enabled: Optional[bool] = None
    paper_influence_paths: Optional[list[str]] = None
    auto_refreeze: Optional[bool] = None
    evaluation_interval_minutes: Optional[int] = None
    paper_active_allocation_pct: Optional[float] = None


def _validated_paper_influence_paths(values: list[str]) -> list[str]:
    requested_paths = sorted({
        str(path).strip() for path in values if str(path).strip()
    })
    from aletheia.learning.registry import (
        get_learning_path,
        supported_paper_influence_path_ids,
    )
    unknown_paths = []
    for path_id in requested_paths:
        try:
            get_learning_path(path_id)
        except KeyError:
            unknown_paths.append(path_id)
    if unknown_paths:
        raise HTTPException(
            400, f"unknown paper influence paths: {', '.join(unknown_paths)}",
        )
    unsupported_paths = sorted(
        set(requested_paths) - set(supported_paper_influence_path_ids())
    )
    if unsupported_paths:
        raise HTTPException(
            400,
            "paper influence path is not fully supported for: "
            + ", ".join(unsupported_paths),
        )
    return requested_paths


@router.post("/learning")
async def update_learning_config(req: LearningConfigUpdate):
    cfg = load_config()
    old = cfg.learning.model_dump(mode="json")
    updates = req.model_dump(exclude_none=True)
    if "evaluation_interval_minutes" in updates and not (15 <= int(updates["evaluation_interval_minutes"]) <= 1440):
        raise HTTPException(400, "evaluation_interval_minutes must be between 15 and 1440")
    if "paper_active_allocation_pct" in updates and not (0 < float(updates["paper_active_allocation_pct"]) <= 0.90):
        raise HTTPException(400, "paper_active_allocation_pct must be > 0 and <= 0.90")
    if "paper_influence_paths" in updates:
        updates["paper_influence_paths"] = _validated_paper_influence_paths(
            updates["paper_influence_paths"],
        )
    elif updates.get("paper_influence_enabled") is True:
        # Old versions accepted arbitrary free text.  Merely toggling the master
        # switch must not reactivate one of those unsupported saved values.
        _validated_paper_influence_paths(list(cfg.learning.paper_influence_paths or []))
    for field, value in updates.items():
        setattr(cfg.learning, field, value)
    save_config(cfg)
    rescheduled = False
    if (
        "evaluation_interval_minutes" in updates
        and updates["evaluation_interval_minutes"] != old.get("evaluation_interval_minutes")
    ):
        from aletheia.agent_runtime.scheduler import reschedule_learning_jobs
        from starlette.concurrency import run_in_threadpool
        rescheduled = await run_in_threadpool(
            reschedule_learning_jobs,
            cfg.learning.evaluation_interval_minutes,
        )
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "learning_config_changed",
                 "entity_type": "learning_config", "before": old,
                 "after": cfg.learning.model_dump(mode="json"),
                 "message": f"Learning experiment config updated: {list(updates.keys())}"})
    return {
        "updated": updates,
        "rescheduled": rescheduled,
        "learning": cfg.learning.model_dump(mode="json"),
    }


@router.get("/learning/experiments")
def list_learning_experiments(limit: int = 100):
    """List all learning experiments with lightweight summary (path, stage, status)."""
    from aletheia.storage.sqlite import get_db
    try:
        rows = get_db().execute(
            "SELECT e.experiment_id, e.hypothesis_id, e.stage, e.status, "
            "e.allocation_pct, e.min_samples_per_arm, e.activation_verdict_id, "
            "e.created_at, h.path_id, h.target_horizon "
            "FROM learning_experiments e "
            "LEFT JOIN learning_hypotheses h ON e.hypothesis_id = h.hypothesis_id "
            "ORDER BY datetime(e.created_at) DESC LIMIT ?",
            (min(max(limit, 1), 200),),
        ).fetchall()
    except Exception as exc:
        raise HTTPException(503, "learning ledger unavailable") from exc
    return [dict(row) for row in rows]


@router.get("/learning/experiments/{experiment_id}")
def get_learning_experiment(experiment_id: str):
    """Read-only lifecycle/operator view for one experiment."""
    from aletheia.learning.lifecycle import inspect_learning_experiment
    from aletheia.learning.experiments import (
        current_policy_epoch as resolve_current_policy_epoch,
    )
    from aletheia.learning.ledger import get_record
    try:
        row = get_record("learning_experiments", experiment_id)
    except Exception as exc:
        raise HTTPException(503, "learning ledger unavailable") from exc
    if row is None:
        raise HTTPException(404, f"experiment not found: {experiment_id}")
    hypothesis_row = get_record("learning_hypotheses", str(row.get("hypothesis_id") or ""))
    path_id = str((hypothesis_row or {}).get("path_id") or "")
    policy_epoch = resolve_current_policy_epoch(
        path_id,
        persist_config_migrations=False,
    ) if path_id else None
    if policy_epoch is None:
        raise HTTPException(409, "current policy epoch is not available")
    return inspect_learning_experiment(
        experiment_id,
        current_policy_epoch=policy_epoch,
    )


class AdmitTrialRequest(SettingsRequest):
    verdict_id: str
    expected_policy_epoch: str
    reason: str
    confirm_admission: Literal[True]

    @field_validator("verdict_id", "expected_policy_epoch")
    @classmethod
    def _non_empty_admission_evidence(cls, value: str) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("admission evidence must not be empty")
        return normalized

    @field_validator("reason")
    @classmethod
    def _bounded_admission_reason(cls, value: str) -> str:
        normalized = " ".join(str(value).split())
        if len(normalized) < 8:
            raise ValueError("admission reason must be at least 8 characters")
        return normalized[:200]


@router.post("/learning/experiments/{experiment_id}/admit-trial")
async def admit_learning_trial(experiment_id: str, req: AdmitTrialRequest):
    """Admit a shadow experiment to paper_10 trial. Change-management boundary."""
    from aletheia.learning.lifecycle import _execute_trial_admission
    result = _execute_trial_admission(
        experiment_id,
        verdict_id=req.verdict_id,
        expected_policy_epoch=req.expected_policy_epoch,
        reason=req.reason,
    )
    if result.action == "hold":
        raise HTTPException(409, {"action": result.action, "reason": result.reason})
    return {
        "action": result.action,
        "reason": result.reason,
        "experiment_id": result.experiment_id,
        "successor_experiment_id": result.successor_experiment_id,
        "verdict_id": result.verdict_id,
    }


# ---------------------------------------------------------------------------
# Operator config
# ---------------------------------------------------------------------------

class OperatorConfigUpdate(SettingsRequest):
    loop_enabled: Optional[bool] = None


@router.post("/operator")
async def update_operator_config(req: OperatorConfigUpdate):
    cfg = load_config()
    old = cfg.operator.model_dump(mode="json")
    updates = req.model_dump(exclude_none=True)
    for field, value in updates.items():
        setattr(cfg.operator, field, value)
    save_config(cfg)
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "operator_config_changed",
                 "entity_type": "operator_config", "before": old,
                 "after": cfg.operator.model_dump(mode="json"),
                 "message": f"Operator config updated: {list(updates.keys())}"})
    return {"updated": updates}


# ---------------------------------------------------------------------------
# Guard / lesson-promotion config
# ---------------------------------------------------------------------------

class RegimeConfigUpdate(SettingsRequest):
    enabled: Optional[bool] = None
    min_hold_periods: Optional[int] = None
    bias_threshold: Optional[float] = None
    short_enabled: Optional[bool] = None


@router.post("/regime")
async def update_regime_config(req: RegimeConfigUpdate):
    cfg = load_config()
    old = cfg.regime.model_dump(mode="json")
    updates = req.model_dump(exclude_none=True)
    for field, value in updates.items():
        setattr(cfg.regime, field, value)
    save_config(cfg)
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "regime_config_changed",
                 "entity_type": "regime_config", "before": old,
                 "after": cfg.regime.model_dump(mode="json"),
                 "message": f"Regime config updated: {list(updates.keys())}"})
    if updates:
        _notify_ranker_config_change()
    return {"updated": updates}


class GuardConfigUpdate(SettingsRequest):
    enabled: Optional[bool] = None
    enforce_in_paper_ranker: Optional[bool] = None
    auto_promote_enabled: Optional[bool] = None
    min_matching_trades: Optional[int] = None
    max_score_penalty: Optional[float] = None


@router.post("/guard")
async def update_guard_config(req: GuardConfigUpdate):
    cfg = load_config()
    old = cfg.guard.model_dump(mode="json")
    updates = req.model_dump(exclude_none=True)
    for field, value in updates.items():
        setattr(cfg.guard, field, value)
    save_config(cfg)
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "guard_config_changed",
                 "entity_type": "guard_config", "before": old,
                 "after": cfg.guard.model_dump(mode="json"),
                 "message": f"Guard config updated: {list(updates.keys())}"})
    if updates:
        _notify_ranker_config_change()
    return {"updated": updates}


# ---------------------------------------------------------------------------
# Health monitoring config
# ---------------------------------------------------------------------------

class HealthConfigUpdate(SettingsRequest):
    enabled: Optional[bool] = None
    alert_via_telegram: Optional[bool] = None
    alert_via_windows_toast: Optional[bool] = None
    max_agent_failure_ratio: Optional[float] = None
    llm_unavailable_alert_after: Optional[int] = None
    market_data_max_staleness_min: Optional[int] = None
    supervisor_max_restarts: Optional[int] = None
    supervisor_restart_window_sec: Optional[int] = None
    supervisor_backoff_cap_sec: Optional[int] = None
    self_heal_enabled: Optional[bool] = None
    self_heal_min_interval_sec: Optional[int] = None
    self_heal_max_per_hour: Optional[int] = None
    self_heal_code_drift: Optional[bool] = None
    self_heal_market_data: Optional[bool] = None
    self_heal_port_cleanup: Optional[bool] = None
    require_llm_at_startup: Optional[bool] = None
    startup_gate_max_retries: Optional[int] = None
    startup_gate_retry_delay_sec: Optional[int] = None
    # auto_resume_* were on the HealthConfig schema + returned by GET /health but
    # absent here, so the frontend HealthCard (which POSTs the whole health object
    # back) hit extra="forbid" → every save returned HTTP 422. Declared so health
    # config is saveable AND these gate-recovery params are controllable from UI.
    auto_resume_enabled: Optional[bool] = None
    auto_resume_interval_sec: Optional[int] = None
    exchange_probe_interval_sec: Optional[int] = None
    exchange_probe_failure_threshold: Optional[int] = None
    exchange_auto_resume_interval_sec: Optional[int] = None
    exchange_auto_resume_success_threshold: Optional[int] = None
    # Startup Behavior Gate (see agent_runtime/startup_behavior_gate.py). Declared
    # here so the HealthCard round-trips them; without this, extra="forbid" 422s.
    startup_behavior_gate_enabled: Optional[bool] = None
    startup_gate_recheck_interval_sec: Optional[int] = None


_HEALTH_BOUNDS: dict[str, tuple[float, float]] = {
    "max_agent_failure_ratio": (0.0, 1.0),
    "llm_unavailable_alert_after": (1, 100),
    "market_data_max_staleness_min": (5, 1440),
    "supervisor_max_restarts": (1, 50),
    "supervisor_restart_window_sec": (60, 86400),
    "supervisor_backoff_cap_sec": (5, 3600),
    "self_heal_min_interval_sec": (300, 86400),
    "self_heal_max_per_hour": (0, 12),
    "startup_gate_max_retries": (1, 10),
    "startup_gate_retry_delay_sec": (5, 120),
    "auto_resume_interval_sec": (30, 3600),
    "exchange_probe_interval_sec": (10, 300),
    "exchange_probe_failure_threshold": (1, 20),
    "exchange_auto_resume_interval_sec": (10, 600),
    "exchange_auto_resume_success_threshold": (1, 10),
    "startup_gate_recheck_interval_sec": (5, 3600),
}


@router.post("/health-config")
async def update_health_config(req: HealthConfigUpdate):
    cfg = load_config()
    old = cfg.health.model_dump(mode="json")
    updates = req.model_dump(exclude_none=True)
    for field, value in updates.items():
        if field in _HEALTH_BOUNDS:
            lo, hi = _HEALTH_BOUNDS[field]
            if not (lo <= value <= hi):
                raise HTTPException(400, f"'{field}' must be between {lo} and {hi}")
        setattr(cfg.health, field, value)
    save_config(cfg)
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "health_config_changed",
                 "entity_type": "health_config", "before": old,
                 "after": cfg.health.model_dump(mode="json"),
                 "message": f"Health config updated: {list(updates.keys())}"})
    return {"updated": updates}


# ---------------------------------------------------------------------------
# Retention config
# ---------------------------------------------------------------------------

class RetentionConfigUpdate(SettingsRequest):
    agent_runs_days: Optional[int] = None
    tool_calls_days: Optional[int] = None
    runtime_sessions_days: Optional[int] = None
    run_steps_days: Optional[int] = None
    market_snapshots_days: Optional[int] = None
    portfolio_snapshots_days: Optional[int] = None
    opportunity_ttl_hours: Optional[int] = None
    jsonl_max_mb: Optional[float] = None
    app_db_capacity_mb: Optional[int] = None


_RETENTION_BOUNDS: dict[str, tuple[float, float]] = {
    "agent_runs_days": (1, 90),
    "tool_calls_days": (1, 90),
    "runtime_sessions_days": (1, 90),
    "run_steps_days": (1, 365),
    "market_snapshots_days": (1, 365),
    "portfolio_snapshots_days": (1, 1825),
    "opportunity_ttl_hours": (1, 720),
    "jsonl_max_mb": (10.0, 10000.0),
    "app_db_capacity_mb": (512, 65536),
}


@router.post("/retention")
async def update_retention_config(req: RetentionConfigUpdate):
    cfg = load_config()
    old = cfg.retention.model_dump(mode="json")
    updates = req.model_dump(exclude_none=True)
    for field, value in updates.items():
        if field in _RETENTION_BOUNDS:
            lo, hi = _RETENTION_BOUNDS[field]
            if not (lo <= value <= hi):
                raise HTTPException(400, f"'{field}' must be between {lo} and {hi}")
        setattr(cfg.retention, field, value)
    save_config(cfg)
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "retention_config_changed",
                 "entity_type": "retention_config", "before": old,
                 "after": cfg.retention.model_dump(mode="json"),
                 "message": f"Retention config updated: {list(updates.keys())}"})
    return {"updated": updates}


# ---------------------------------------------------------------------------
# Telegram config
# ---------------------------------------------------------------------------

class TelegramConfigUpdate(SettingsRequest):
    enabled: Optional[bool] = None
    allowed_chat_ids: Optional[list[int]] = None
    notify_trade_fills: Optional[bool] = None
    notify_daily_summary: Optional[bool] = None
    daily_summary_hour_cst: Optional[int] = None
    dnd_enabled: Optional[bool] = None
    dnd_start_hour: Optional[int] = None
    dnd_end_hour: Optional[int] = None


@router.post("/telegram")
async def update_telegram_config(req: TelegramConfigUpdate):
    cfg = load_config()
    old = cfg.telegram.model_dump(mode="json")
    updates = req.model_dump(exclude_none=True)
    for field, value in updates.items():
        setattr(cfg.telegram, field, value)
    save_config(cfg)

    rescheduled = False
    if (
        "daily_summary_hour_cst" in updates
        and updates["daily_summary_hour_cst"] != old.get("daily_summary_hour_cst")
    ):
        from aletheia.agent_runtime.scheduler import reschedule_telegram_summary
        from starlette.concurrency import run_in_threadpool
        rescheduled = await run_in_threadpool(
            reschedule_telegram_summary, cfg.telegram.daily_summary_hour_cst,
        )

    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "telegram_config_changed",
                 "entity_type": "telegram_config", "before": old,
                 "after": cfg.telegram.model_dump(mode="json"),
                 "message": f"Telegram config updated: {list(updates.keys())}"})
    return {"updated": updates, "rescheduled": rescheduled}


# ---------------------------------------------------------------------------
# Webhook config
# ---------------------------------------------------------------------------

class WebhookConfigUpdate(SettingsRequest):
    enabled: Optional[bool] = None
    url: Optional[str] = None
    secret: Optional[str] = None
    notify_trade_fills: Optional[bool] = None
    notify_alerts: Optional[bool] = None
    notify_daily_summary: Optional[bool] = None
    timeout_sec: Optional[float] = None


@router.post("/webhook")
async def update_webhook_config(req: WebhookConfigUpdate):
    cfg = load_config()
    old = cfg.webhook.model_dump(mode="json")
    updates = req.model_dump(exclude_none=True)
    for field, value in updates.items():
        setattr(cfg.webhook, field, value)
    save_config(cfg)
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "webhook_config_changed",
                 "entity_type": "webhook_config", "before": _redacted_webhook(old),
                 "after": _redacted_webhook(cfg.webhook.model_dump(mode="json")),
                 "message": f"Webhook config updated: {list(updates.keys())}"})
    return {"updated": _redacted_webhook(updates)}


# ---------------------------------------------------------------------------
# Update config
# ---------------------------------------------------------------------------

class UpdateConfigUpdate(SettingsRequest):
    enabled: Optional[bool] = None
    branch: Optional[str] = None


@router.post("/update-config")
async def update_update_config(req: UpdateConfigUpdate):
    cfg = load_config()
    old = {"enabled": cfg.update.enabled, "branch": cfg.update.branch}
    if req.enabled is not None:
        cfg.update.enabled = req.enabled
    if req.branch is not None:
        branch = req.branch.strip()
        if not branch:
            raise HTTPException(400, "branch must not be empty")
        cfg.update.branch = branch
    save_config(cfg)
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "update_config_changed",
                 "entity_type": "update_config", "before": old,
                 "after": {"enabled": cfg.update.enabled, "branch": cfg.update.branch},
                 "message": "Update config changed"})
    return {"enabled": cfg.update.enabled, "branch": cfg.update.branch}


@router.post("/permission-mode")
async def set_permission_mode(req: PermissionModeRequest):
    if req.mode not in VALID_PERMISSION_MODES:
        raise HTTPException(400, f"Invalid mode. Choose from: {sorted(VALID_PERMISSION_MODES)}")
    cfg = load_config()
    old = cfg.agent.permission_mode
    cfg.agent.permission_mode = req.mode
    save_config(cfg)
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "permission_changed",
                 "entity_type": "permission_mode", "before": {"mode": old}, "after": {"mode": req.mode},
                 "message": f"Permission mode {old} -> {req.mode}"})
    if old != req.mode:
        _notify_ranker_config_change()
    return {"old": old, "new": req.mode}




@router.post("/exchange-mode")
async def set_exchange_mode(req: ExchangeModeRequest):
    if req.mode not in ("demo", "live"):
        raise HTTPException(400, "Mode must be 'demo' or 'live'")
    cfg = load_config()
    old = cfg.exchange.mode
    _reject_live_identity_change(cfg, changed=req.mode != old)
    cfg.exchange.mode = req.mode
    save_config(cfg)
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "exchange_mode_changed",
                 "entity_type": "exchange_mode", "before": {"mode": old}, "after": {"mode": req.mode},
                 "message": f"Exchange mode {old} -> {req.mode}"})
    return {"old": old, "new": req.mode}


# ---------------------------------------------------------------------------
# Risk config (editable limits)
# ---------------------------------------------------------------------------

class RiskConfigUpdate(SettingsRequest):
    max_daily_loss_pct: Optional[float] = None
    max_single_trade_risk_pct: Optional[float] = None
    max_total_live_exposure_pct: Optional[float] = None
    max_live_position_notional_usdt: Optional[float] = None
    max_live_limited_cap_usdt: Optional[float] = None
    starting_capital_usdt: Optional[float] = None
    max_demo_position_notional_usdt: Optional[float] = None
    max_per_symbol_notional_usdt: Optional[float] = None
    max_demo_single_trade_risk_pct: Optional[float] = None
    max_futures_leverage_live: Optional[int] = None
    max_futures_leverage_demo: Optional[int] = None
    max_leverage_major_demo: Optional[int] = None
    max_leverage_major_live: Optional[int] = None
    max_leverage_mid_demo: Optional[int] = None
    max_leverage_mid_live: Optional[int] = None
    max_leverage_alt_demo: Optional[int] = None
    max_leverage_alt_live: Optional[int] = None
    max_account_leverage_demo: Optional[float] = None
    max_account_leverage_live: Optional[float] = None
    futures_margin_warning_ratio: Optional[float] = None
    futures_margin_force_close_ratio: Optional[float] = None
    min_liquidation_buffer_pct: Optional[float] = None
    max_open_positions_live: Optional[int] = None
    max_open_positions_demo: Optional[int] = None
    allow_short_demo: Optional[bool] = None
    allow_short_live: Optional[bool] = None
    kill_switch_enabled_live: Optional[bool] = None
    kill_switch_enabled_demo: Optional[bool] = None
    correlation_guard_enabled: Optional[bool] = None
    max_sector_concentration_pct: Optional[float] = None
    max_portfolio_beta: Optional[float] = None
    # Scanner-family live-eligibility gate (#14, Position C 2026-07-02)
    live_family_min_samples: Optional[int] = None
    live_family_min_win_rate_pct: Optional[float] = None


# Fields that must never be changed via the dashboard (safety invariants).
_RISK_IMMUTABLE = {"allowed_exchange", "require_risk_check_for_all_orders"}

# Demo/paper capacity controls intentionally have no application-level maximum.
# They remain positive, finite typed values and continue to pass through every
# Risk Engine check; exchange-backed leverage retains venue-compatible bounds.
_RISK_MINIMUMS: dict[str, float] = {
    "max_demo_position_notional_usdt": 1.0,
    "max_demo_single_trade_risk_pct": 0.01,
    "max_account_leverage_demo": 1.0,
    "max_open_positions_demo": 1.0,
}

_RISK_BOUNDS: dict[str, tuple[float, float]] = {
    "max_daily_loss_pct": (0.1, 20.0),
    "max_single_trade_risk_pct": (0.01, 10.0),
    "max_total_live_exposure_pct": (1.0, 50.0),
    "max_live_position_notional_usdt": (1.0, 100_000.0),
    "max_live_limited_cap_usdt": (1.0, 100_000.0),
    "starting_capital_usdt": (1.0, 100_000.0),
    "max_per_symbol_notional_usdt": (0.0, 100_000.0),
    "max_futures_leverage_live": (1, 20),
    "max_futures_leverage_demo": (1, 125),
    "max_leverage_major_demo": (1, 20),
    "max_leverage_major_live": (1, 10),
    "max_leverage_mid_demo": (1, 10),
    "max_leverage_mid_live": (1, 5),
    "max_leverage_alt_demo": (1, 5),
    "max_leverage_alt_live": (1, 3),
    "max_account_leverage_live": (1.0, 5.0),
    "futures_margin_warning_ratio": (0.3, 0.95),
    "futures_margin_force_close_ratio": (0.5, 0.99),
    "min_liquidation_buffer_pct": (1.0, 20.0),
    "max_open_positions_live": (1, 50),
    "max_sector_concentration_pct": (10.0, 100.0),
    "max_portfolio_beta": (0.5, 10.0),
    "live_family_min_samples": (1, 500),
    "live_family_min_win_rate_pct": (0.0, 100.0),
}


@router.post("/risk")
async def update_risk_config(req: RiskConfigUpdate):
    """Update editable risk limits. Immutable fields are rejected."""
    cfg = load_config()
    old = cfg.risk.model_dump(mode="json")
    old_policy_hashes = {
        mode: risk_policy_hash(getattr(cfg.risk, mode))
        for mode in ("demo", "live")
    }
    updates = req.model_dump(exclude_none=True)

    for field, value in updates.items():
        if field in _RISK_IMMUTABLE:
            raise HTTPException(400, f"Field '{field}' cannot be modified via the dashboard")
        if field in _RISK_MINIMUMS and value < _RISK_MINIMUMS[field]:
            raise HTTPException(400, f"'{field}' must be at least {_RISK_MINIMUMS[field]}, got {value}")
        if field in _RISK_BOUNDS:
            lo, hi = _RISK_BOUNDS[field]
            if not (lo <= value <= hi):
                raise HTTPException(400, f"'{field}' must be between {lo} and {hi}, got {value}")
        setattr(cfg.risk, field, value)

    for mode in ("demo", "live"):
        policy = getattr(cfg.risk, mode)
        if risk_policy_hash(policy) != old_policy_hashes[mode]:
            policy.revision += 1
    save_config(cfg)
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "risk_config_changed",
                 "entity_type": "risk_config", "before": old,
                 "after": cfg.risk.model_dump(mode="json"),
                 "message": f"Risk config updated: {list(updates.keys())}"})
    return {"updated": updates, "risk": cfg.risk.model_dump(mode="json")}


RiskMode = Literal["demo", "live"]


def _policy_payload(cfg) -> dict:
    return {
        mode: {
            **getattr(cfg.risk, mode).model_dump(mode="json"),
            "policy_hash": risk_policy_hash(getattr(cfg.risk, mode)),
        }
        for mode in ("demo", "live")
    }


def _policy_diff(source: dict, target: dict, prefix: str = "") -> list[dict]:
    rows: list[dict] = []
    for key in sorted(set(source) | set(target)):
        path = f"{prefix}.{key}" if prefix else key
        left = source.get(key)
        right = target.get(key)
        if isinstance(left, dict) and isinstance(right, dict):
            rows.extend(_policy_diff(left, right, path))
        elif left != right:
            rows.append({"path": path, "source": left, "target": right})
    return rows


def _validate_mode_policy(policy: ModeRiskPolicy) -> None:
    import math

    numeric = {
        "capital.base_usdt": policy.capital.base_usdt,
        "capital.max_position_notional_usdt": policy.capital.max_position_notional_usdt,
        "capital.max_per_symbol_notional_usdt": policy.capital.max_per_symbol_notional_usdt,
        "capital.max_total_exposure_pct": policy.capital.max_total_exposure_pct,
        "loss.max_single_trade_risk_pct": policy.loss.max_single_trade_risk_pct,
        "loss.max_daily_loss_pct": policy.loss.max_daily_loss_pct,
        "positions.max_open_positions": policy.positions.max_open_positions,
        "leverage.futures_cap": policy.leverage.futures_cap,
        "leverage.major_cap": policy.leverage.major_cap,
        "leverage.mid_cap": policy.leverage.mid_cap,
        "leverage.alt_cap": policy.leverage.alt_cap,
        "leverage.account_cap": policy.leverage.account_cap,
        "margin.min_entry_liquidation_buffer_pct":
            policy.margin.min_entry_liquidation_buffer_pct,
        "margin.runtime_liquidation_buffer_pct":
            policy.margin.runtime_liquidation_buffer_pct,
        "correlation.max_sector_concentration_pct":
            policy.correlation.max_sector_concentration_pct,
        "correlation.max_portfolio_beta": policy.correlation.max_portfolio_beta,
    }
    invalid = [
        path for path, value in numeric.items()
        if not math.isfinite(float(value)) or float(value) < 0
    ]
    if policy.positions.max_open_positions < 1:
        invalid.append("positions.max_open_positions")
    if policy.capital.base_usdt <= 0:
        invalid.append("capital.base_usdt")
    if policy.capital.max_position_notional_usdt <= 0:
        invalid.append("capital.max_position_notional_usdt")
    if policy.capital.max_per_symbol_notional_usdt <= 0:
        invalid.append("capital.max_per_symbol_notional_usdt")
    if policy.loss.max_single_trade_risk_pct <= 0:
        invalid.append("loss.max_single_trade_risk_pct")
    if policy.loss.max_daily_loss_pct <= 0:
        invalid.append("loss.max_daily_loss_pct")
    for path, value in {
        "leverage.futures_cap": policy.leverage.futures_cap,
        "leverage.major_cap": policy.leverage.major_cap,
        "leverage.mid_cap": policy.leverage.mid_cap,
        "leverage.alt_cap": policy.leverage.alt_cap,
        "leverage.account_cap": policy.leverage.account_cap,
    }.items():
        if value < 1:
            invalid.append(path)
    if invalid:
        raise HTTPException(400, f"Invalid risk policy fields: {sorted(set(invalid))}")


class RiskPolicyCopyRequest(SettingsRequest):
    source: RiskMode
    target: RiskMode
    source_revision: int
    target_revision: int
    confirm_target_replacement: bool = False
    reason: str


class RiskPolicyReplaceRequest(SettingsRequest):
    policy: ModeRiskPolicy
    expected_revision: int
    confirm_target_replacement: bool = False
    reason: str


class RiskPolicyResetRequest(SettingsRequest):
    expected_revision: int
    confirm_target_replacement: bool = False
    reason: str


@router.get("/risk-policies")
async def get_risk_policies():
    return _policy_payload(load_config())


@router.post("/risk-policies/compare")
async def compare_risk_policies(req: RiskPolicyCopyRequest):
    cfg = load_config()
    source = getattr(cfg.risk, req.source).model_dump(mode="json")
    target = getattr(cfg.risk, req.target).model_dump(mode="json")
    return {"source": req.source, "target": req.target, "diff": _policy_diff(source, target)}


@router.post("/risk-policies/copy")
async def copy_risk_policy(req: RiskPolicyCopyRequest):
    if req.source == req.target:
        raise HTTPException(400, "source and target policies must differ")
    if not req.confirm_target_replacement:
        raise HTTPException(422, "Policy replacement requires explicit confirmation")
    cfg = load_config()
    source = getattr(cfg.risk, req.source)
    target = getattr(cfg.risk, req.target)
    if source.revision != req.source_revision or target.revision != req.target_revision:
        raise HTTPException(409, "policy_revision_conflict")

    before = target.model_dump(mode="json")
    common = source.model_dump(
        mode="json",
        exclude={"policy_id", "revision", "eligibility"},
    )
    replacement = ModeRiskPolicy.model_validate({
        **target.model_dump(mode="json"),
        **common,
        "revision": target.revision + 1,
    })
    _validate_mode_policy(replacement)
    setattr(cfg.risk, req.target, replacement)
    save_config(cfg)
    from aletheia.files.jsonl import write_audit
    write_audit({
        "actor": "human",
        "source": "dashboard",
        "event_type": "risk_policy_copied",
        "entity_type": "risk_policy",
        "entity_id": req.target,
        "before": before,
        "after": replacement.model_dump(mode="json"),
        "message": f"{req.source} policy copied to {req.target}: {req.reason}",
    })
    return _policy_payload(cfg)


@router.put("/risk-policies/{mode}")
async def replace_risk_policy(mode: RiskMode, req: RiskPolicyReplaceRequest):
    if not req.confirm_target_replacement:
        raise HTTPException(422, "Policy replacement requires explicit confirmation")
    cfg = load_config()
    current = getattr(cfg.risk, mode)
    if current.revision != req.expected_revision:
        raise HTTPException(409, "policy_revision_conflict")
    replacement = req.policy.model_copy(
        update={
            "policy_id": current.policy_id,
            "revision": current.revision + 1,
            "eligibility": req.policy.eligibility if mode == "live" else None,
        },
        deep=True,
    )
    _validate_mode_policy(replacement)
    setattr(cfg.risk, mode, replacement)
    save_config(cfg)
    from aletheia.files.jsonl import write_audit
    write_audit({
        "actor": "human",
        "source": "dashboard",
        "event_type": "risk_policy_replaced",
        "entity_type": "risk_policy",
        "entity_id": mode,
        "before": current.model_dump(mode="json"),
        "after": replacement.model_dump(mode="json"),
        "message": f"{mode} policy replaced: {req.reason}",
    })
    return _policy_payload(cfg)


@router.post("/risk-policies/{mode}/reset-defaults")
async def reset_risk_policy(mode: RiskMode, req: RiskPolicyResetRequest):
    if not req.confirm_target_replacement:
        raise HTTPException(422, "Policy replacement requires explicit confirmation")
    cfg = load_config()
    current = getattr(cfg.risk, mode)
    if current.revision != req.expected_revision:
        raise HTTPException(409, "policy_revision_conflict")
    defaults = default_mode_risk_policy(mode)
    replacement = defaults.model_copy(
        update={
            "policy_id": current.policy_id,
            "revision": current.revision + 1,
        },
        deep=True,
    )
    _validate_mode_policy(replacement)
    setattr(cfg.risk, mode, replacement)
    save_config(cfg)
    from aletheia.files.jsonl import write_audit
    write_audit({
        "actor": "human",
        "source": "dashboard",
        "event_type": "risk_policy_reset_to_defaults",
        "entity_type": "risk_policy",
        "entity_id": mode,
        "before": current.model_dump(mode="json"),
        "after": replacement.model_dump(mode="json"),
        "message": f"{mode} policy reset to defaults: {req.reason}",
    })
    return _policy_payload(cfg)


# ---------------------------------------------------------------------------
# Symbol filter (whitelist/blacklist mode + single symbol list)
# ---------------------------------------------------------------------------

VALID_SYMBOL_FILTER_MODES = {"whitelist", "blacklist"}


class SymbolsUpdate(SettingsRequest):
    mode: str = "blacklist"
    symbols: list[str] = []


@router.post("/symbols")
async def update_tradable_symbols(req: SymbolsUpdate):
    """Update symbol filter mode and list. Risk and exchange are updated atomically."""
    if req.mode not in VALID_SYMBOL_FILTER_MODES:
        raise HTTPException(400, f"mode must be one of {sorted(VALID_SYMBOL_FILTER_MODES)}")
    if req.mode == "whitelist" and not req.symbols:
        raise HTTPException(400, "whitelist mode requires at least one symbol")

    normalized = list(dict.fromkeys(s.strip().upper() for s in req.symbols if s.strip()))

    cfg = load_config()
    old = {"mode": cfg.risk.symbol_filter_mode, "symbols": list(cfg.risk.symbol_list)}

    cfg.risk.symbol_filter_mode = req.mode
    cfg.risk.symbol_list = normalized
    cfg.exchange.symbol_list = normalized

    # In whitelist mode, sync observed_symbols so the data layer fetches them.
    from aletheia.data.symbols import to_canonical, InstrumentId, MARKET_SPOT
    if req.mode == "whitelist" and normalized:
        canonical_symbols = [to_canonical(s) for s in normalized]
        cfg.market_data.observed_symbols = canonical_symbols
        exchange = cfg.market_data.ccxt_exchange or "binance"
        cfg.market_data.observed_instruments = [
            str(InstrumentId.from_parts(exchange, sym, MARKET_SPOT))
            for sym in canonical_symbols
        ]

    save_config(cfg)

    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "symbols_changed",
                 "entity_type": "symbols", "before": old,
                 "after": {"mode": req.mode, "symbols": normalized},
                 "message": f"Symbol filter updated: mode={req.mode}"})
    if old != {"mode": req.mode, "symbols": normalized}:
        _notify_ranker_config_change()
    return {"mode": req.mode, "symbols": normalized}


# ---------------------------------------------------------------------------
# Observed equities (US equity tickers for equity_observer)
# ---------------------------------------------------------------------------

class EquitiesConfigUpdate(SettingsRequest):
    tickers: list[str]


@router.post("/equities")
async def update_observed_equities(req: EquitiesConfigUpdate):
    """Update US equity tickers observed by equity_observer (observation-only, not traded)."""
    normalized = list(dict.fromkeys(t.strip().upper() for t in req.tickers if t.strip()))
    cfg = load_config()
    old = list(cfg.market_data.observed_equities or [])
    cfg.market_data.observed_equities = normalized
    save_config(cfg)
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "equities_config_changed",
                 "entity_type": "market_data_config", "before": {"observed_equities": old},
                 "after": {"observed_equities": normalized},
                 "message": f"Observed equities updated: {old} -> {normalized}"})
    return {"observed_equities": normalized}


# ---------------------------------------------------------------------------
# Trading config (paper position management)
# ---------------------------------------------------------------------------

class MarketAwareExitsUpdate(BaseModel):
    enabled: Optional[bool] = None
    min_hold_hours_before_market_exit: Optional[float] = None
    max_signal_age_min: Optional[float] = None
    urgent_exit_score: Optional[int] = None
    urgent_exit_max_pnl_pct: Optional[float] = None
    tighten_stop_score: Optional[int] = None
    confidence_collapse_score: Optional[int] = None
    tighten_atr_mult: Optional[float] = None
    tighten_pct: Optional[float] = None


class TradingConfigUpdate(SettingsRequest):
    default_stop_loss_pct: Optional[float] = None
    default_take_profit_pct: Optional[float] = None
    max_position_hold_hours: Optional[float] = None
    slippage_bps: Optional[float] = None
    execution_fee_pct: Optional[float] = None
    min_confidence_to_trade: Optional[str] = None
    auto_adopt_enabled: Optional[bool] = None
    trail_enabled: Optional[bool] = None
    trail_atr_mult: Optional[float] = None
    trail_pct: Optional[float] = None
    breakeven_trigger_pct: Optional[float] = None
    default_notional_usdt: Optional[float] = None
    vol_sizing_enabled: Optional[bool] = None
    vol_target_risk_usdt: Optional[float] = None
    vol_target_pct: Optional[float] = None
    vol_kelly_cap_fraction: Optional[float] = None
    exchange_tpsl_enabled: Optional[bool] = None
    persona_sizing_enabled: Optional[bool] = None
    active_persona: Optional[str] = None
    persona_learning_enabled: Optional[bool] = None
    persona_learning_min_samples: Optional[int] = None
    persona_progressive_unlock_enabled: Optional[bool] = None
    persona_unlock_step_samples: Optional[int] = None
    persona_pyramid_enabled: Optional[bool] = None
    persona_pyramid_add_trigger_pct: Optional[float] = None
    persona_auto_advance_enabled: Optional[bool] = None
    market_aware_exits: Optional[MarketAwareExitsUpdate] = None


_TRADING_BOUNDS: dict[str, tuple[float, float]] = {
    "default_stop_loss_pct": (0.1, 50.0),
    "default_take_profit_pct": (0.1, 100.0),
    "max_position_hold_hours": (1.0, 8760.0),
    "slippage_bps": (0.0, 200.0),
    "execution_fee_pct": (0.0, 5.0),
    "default_notional_usdt": (0.01, 100_000.0),
    "vol_target_risk_usdt": (0.01, 100_000.0),
    "vol_target_pct": (0.1, 100.0),
    "vol_kelly_cap_fraction": (0.01, 0.5),
    "persona_learning_min_samples": (3, 500),
    "persona_unlock_step_samples": (1, 1000),
    "persona_pyramid_add_trigger_pct": (0.1, 50.0),
}
_VALID_CONFIDENCE = {"high", "medium", "speculative"}
_EXPERIMENTAL_ENABLEMENT_HOLDS = {
    "persona_pyramid_enabled": (
        "Persona pyramid is a policy helper with no production trigger or "
        "risk-approved add-order materializer; new enablement is blocked"
    ),
    "persona_auto_advance_enabled": (
        "Persona auto-advance is safety-held until a prospective "
        "Persona/policy/step verdict exists; new enablement is blocked"
    ),
}


@router.post("/trading")
async def update_trading_config(req: TradingConfigUpdate):
    """Update paper position management parameters."""
    cfg = load_config()
    old = cfg.trading.model_dump(mode="json")
    updates = req.model_dump(exclude_none=True)

    for field, value in updates.items():
        if field == "market_aware_exits" and isinstance(value, dict):
            for k, v in value.items():
                if v is not None:
                    setattr(cfg.trading.market_aware_exits, k, v)
            continue
        if field == "min_confidence_to_trade":
            if value not in _VALID_CONFIDENCE:
                raise HTTPException(400, f"min_confidence_to_trade must be one of {sorted(_VALID_CONFIDENCE)}")
        elif field == "active_persona":
            if value not in (cfg.trading.personas or {}):
                raise HTTPException(400, f"active_persona must be one of {sorted(cfg.trading.personas or {})}")
        elif field in _EXPERIMENTAL_ENABLEMENT_HOLDS and value is True:
            raise HTTPException(409, _EXPERIMENTAL_ENABLEMENT_HOLDS[field])
        elif field in _TRADING_BOUNDS:
            lo, hi = _TRADING_BOUNDS[field]
            if not (lo <= value <= hi):
                raise HTTPException(400, f"'{field}' must be between {lo} and {hi}")
        setattr(cfg.trading, field, value)

    save_config(cfg)
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "trading_config_changed",
                 "entity_type": "trading_config", "before": old,
                 "after": cfg.trading.model_dump(mode="json"),
                 "message": f"Trading config updated: {list(updates.keys())}"})
    if "min_confidence_to_trade" in updates:
        _notify_ranker_config_change()
    return {"updated": updates, "trading": cfg.trading.model_dump(mode="json")}


# ---------------------------------------------------------------------------
# Market data config
# ---------------------------------------------------------------------------

_SUPPORTED_EXCHANGES = {"binance", "bybit", "okx", "bitget", "gate", "kucoin", "htx"}


class MarketDataConfigUpdate(SettingsRequest):
    ccxt_exchange: Optional[str] = None
    ccxt_fallback: Optional[str] = None
    confirm_exchange_divergence: bool = False
    ohlcv_cache_ttl_min: Optional[int] = None
    enable_yfinance_macro: Optional[bool] = None
    observed_stocks: Optional[list[str]] = None
    # Tokenized equity/ETF spot symbols on any supported exchange (Bitget r-series,
    # Bitget ON-series, future OKX SWAP).  Replaces the narrower observed_xstocks.
    observed_stock_tokens: Optional[list[str]] = None
    observed_xstocks: Optional[list[str]] = None  # deprecated alias, still accepted
    observed_prediction_markets: Optional[list[str]] = None
    # Round 2 macro signals (Step 1-4 zero-cost data integration).
    enable_long_short_ratio: Optional[bool] = None
    enable_fear_greed: Optional[bool] = None
    enable_coingecko_top: Optional[bool] = None
    coingecko_top_n: Optional[int] = None
    derivatives_history_hours: Optional[int] = None
    # Free zero-key data sources (datasrc overhaul).
    enable_hyperliquid: Optional[bool] = None
    enable_defillama: Optional[bool] = None
    # #35 fix: fields present in MarketDataConfig but previously missing from
    # the update surface — a settings-page save silently dropped edits to
    # these because the API model didn't accept them. Adding the safe
    # scalars here; `coinalyze_api_key` is a SECRET and stays with the
    # dedicated credential route, never in bulk-update payloads.
    provider: Optional[str] = None
    enable_crypto_fallback: Optional[bool] = None
    enable_coinalyze: Optional[bool] = None
    prediction_enabled: Optional[bool] = None
    price_divergence_threshold_pct: Optional[float] = None
    price_divergence_alert_enabled: Optional[bool] = None
    observed_equities: Optional[list[str]] = None


_MARKET_DATA_BOUNDS: dict[str, tuple[float, float]] = {
    "ohlcv_cache_ttl_min": (1, 60),
    "coingecko_top_n": (10, 250),  # CoinGecko per_page hard ceiling is 250
    "derivatives_history_hours": (0, 168),  # 0 = disabled, up to 7 days backfill
    # #35 fix: divergence-alert threshold percent — 0 disables alerts,
    # 25% is a generous ceiling (anything larger is noise, not a divergence).
    "price_divergence_threshold_pct": (0.0, 25.0),
}

# #35 fix: `provider` must resolve to a known data-source label. Kept in a
# separate set (not part of the numeric bounds) so validation stays explicit.
_MARKET_DATA_PROVIDERS = {"ccxt", "bitget"}


@router.post("/market-data")
async def update_market_data_config(req: MarketDataConfigUpdate):
    """Update market data source parameters."""
    cfg = load_config()
    old = cfg.market_data.model_dump(mode="json")
    updates = req.model_dump(
        exclude_none=True, exclude={"confirm_exchange_divergence"},
    )

    validated_updates: dict = {}
    for field, value in updates.items():
        if field in _MARKET_DATA_BOUNDS:
            lo, hi = _MARKET_DATA_BOUNDS[field]
            if not (lo <= value <= hi):
                raise HTTPException(400, f"'{field}' must be between {lo} and {hi}")
        if field in {"ccxt_exchange", "ccxt_fallback"}:
            value = str(value).strip().lower()
            if value not in _SUPPORTED_EXCHANGES:
                raise HTTPException(400, f"'{field}' must be one of: {sorted(_SUPPORTED_EXCHANGES)}")
        # #35 fix: validate `provider` against the known label set.
        if field == "provider":
            value = str(value).strip().lower()
            if value not in _MARKET_DATA_PROVIDERS:
                raise HTTPException(400, f"'provider' must be one of: {sorted(_MARKET_DATA_PROVIDERS)}")
        if field in {"observed_stocks", "observed_stock_tokens", "observed_xstocks", "observed_prediction_markets", "observed_equities"}:
            value = list(dict.fromkeys(str(s).strip() for s in value if str(s).strip()))
        validated_updates[field] = value

    from aletheia.config.exchange_alignment import exchange_alignment_for_values
    next_data_exchange = str(
        validated_updates.get("ccxt_exchange", cfg.market_data.ccxt_exchange),
    )
    alignment = exchange_alignment_for_values(
        paper_execution_mode=cfg.exchange.paper_execution_mode,
        data_exchange=next_data_exchange,
        configured_exchange=cfg.exchange.name,
    )
    if (
        "ccxt_exchange" in validated_updates
        and next_data_exchange != cfg.market_data.ccxt_exchange
    ):
        _require_exchange_divergence_confirmation(
            alignment, req.confirm_exchange_divergence,
        )

    for field, value in validated_updates.items():
        setattr(cfg.market_data, field, value)

    save_config(cfg)
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "market_data_config_changed",
                 "entity_type": "market_data_config", "before": old,
                 "after": cfg.market_data.model_dump(mode="json"),
                 "message": f"Market data config updated: {list(validated_updates.keys())}"})
    return {
        "updated": validated_updates,
        "market_data": cfg.market_data.model_dump(mode="json"),
        "exchange_alignment": alignment.to_dict(),
    }


# ---------------------------------------------------------------------------
# Universe config
# ---------------------------------------------------------------------------

class UniverseConfigUpdate(SettingsRequest):
    enabled: Optional[bool] = None
    refresh_interval_min: Optional[int] = None
    max_candidates: Optional[int] = None
    core_pool_size: Optional[int] = None
    min_volume_24h_usdt: Optional[float] = None
    max_spread_bps: Optional[float] = None
    min_depth_10bps_usd: Optional[float] = None
    explore_ratio: Optional[float] = None
    snapshot_ttl_enabled: Optional[bool] = None
    snapshot_ttl_hours: Optional[float] = None
    blacklist: Optional[list[str]] = None
    fallback_symbols: Optional[list[str]] = None
    market_type: Optional[str] = None
    family_quota: Optional[dict[str, float]] = None
    family_quota_by_regime: Optional[dict[str, dict[str, float]]] = None


_UNIVERSE_BOUNDS: dict[str, tuple[float, float]] = {
    "refresh_interval_min": (1, 60),
    "max_candidates": (5, 100),
    "core_pool_size": (1, 50),
    "min_volume_24h_usdt": (100_000, 500_000_000),
    "max_spread_bps": (1, 200),
    "min_depth_10bps_usd": (1_000, 10_000_000),
    "explore_ratio": (0.0, 0.5),
    "snapshot_ttl_hours": (1, 72),
}


@router.post("/universe")
async def update_universe_config(req: UniverseConfigUpdate):
    """Update universe generator parameters."""
    cfg = load_config()
    old = cfg.universe.model_dump(mode="json")
    updates = req.model_dump(exclude_none=True)

    _VALID_MARKET_TYPES = {"crypto", "stocks", "mixed"}
    for field, value in updates.items():
        if field in _UNIVERSE_BOUNDS:
            lo, hi = _UNIVERSE_BOUNDS[field]
            if not (lo <= value <= hi):
                raise HTTPException(400, f"'{field}' must be between {lo} and {hi}")
        if field in {"blacklist", "fallback_symbols"}:
            value = list(dict.fromkeys(str(s).strip().upper() for s in value if str(s).strip()))
            if field == "fallback_symbols" and not value:
                raise HTTPException(400, "fallback_symbols must not be empty")
        if field == "market_type" and value not in _VALID_MARKET_TYPES:
            raise HTTPException(400, f"market_type must be one of: {sorted(_VALID_MARKET_TYPES)}")
        if field == "family_quota":
            if not isinstance(value, dict) or not value:
                raise HTTPException(400, "family_quota must be a non-empty dict")
            total = sum(float(v) for v in value.values())
            if abs(total - 1.0) > 0.01:
                raise HTTPException(400, f"family_quota values must sum to 1.0 (got {total:.3f})")
            value = {k: round(float(v), 2) for k, v in value.items()}
        if field == "family_quota_by_regime":
            # Nested {regime: {family: ratio}}. An empty map disables per-regime
            # override (falls back to static family_quota). Each present regime's
            # quotas must sum to 1.0, same rule as the static quota.
            if not isinstance(value, dict):
                raise HTTPException(400, "family_quota_by_regime must be a dict")
            _VALID_REGIMES = {"bull", "bear", "sideways", "volatile"}
            cleaned: dict[str, dict[str, float]] = {}
            for regime_state, quota in value.items():
                if regime_state not in _VALID_REGIMES:
                    raise HTTPException(400, f"unknown regime '{regime_state}' (valid: {sorted(_VALID_REGIMES)})")
                if not isinstance(quota, dict) or not quota:
                    raise HTTPException(400, f"quota for regime '{regime_state}' must be a non-empty dict")
                total = sum(float(v) for v in quota.values())
                if abs(total - 1.0) > 0.01:
                    raise HTTPException(400, f"family_quota_by_regime['{regime_state}'] must sum to 1.0 (got {total:.3f})")
                cleaned[regime_state] = {k: round(float(v), 2) for k, v in quota.items()}
            value = cleaned
        setattr(cfg.universe, field, value)

    save_config(cfg)

    # IntervalTrigger is immutable once added — a saved refresh_interval_min only
    # takes effect at the next gateway restart unless the live job is rescheduled.
    rescheduled = False
    if "refresh_interval_min" in updates and updates["refresh_interval_min"] != old.get("refresh_interval_min"):
        from aletheia.agent_runtime.scheduler import reschedule_universe_refresh
        rescheduled = reschedule_universe_refresh(cfg.universe.refresh_interval_min)

    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "universe_config_changed",
                 "entity_type": "universe_config", "before": old,
                 "after": cfg.universe.model_dump(mode="json"),
                 "message": f"Universe config updated: {list(updates.keys())}"})
    if updates:
        _notify_ranker_config_change()
    return {"updated": updates, "rescheduled": rescheduled, "universe": cfg.universe.model_dump(mode="json")}


# ---------------------------------------------------------------------------
# Strategy Factory config  (DEPRECATED — subsystem removed 2026-07-02)
# ---------------------------------------------------------------------------

_STRATEGY_FACTORY_DEPRECATED_FIELDS = [
    "backtest_gate_enabled", "sweep_enabled", "sweep_max_variants",
    "sweep_n_trials", "sweep_top_k",
    "event_driven_enabled", "event_cooldown_minutes", "event_min_score",
    "walk_forward_enabled", "walk_forward_folds", "walk_forward_min_bars",
    "high_trade_count_threshold", "min_runtime_days_relaxed",
]

# Accepts (but ignores) the old field set so old API callers don't get a hard 422.
class StrategyFactoryConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    backtest_gate_enabled: bool | None = None
    sweep_enabled: bool | None = None
    sweep_max_variants: int | None = None
    event_driven_enabled: bool | None = None
    event_cooldown_minutes: int | None = None
    event_min_score: float | None = None
    walk_forward_enabled: bool | None = None
    walk_forward_folds: int | None = None
    walk_forward_min_bars: int | None = None
    high_trade_count_threshold: int | None = None
    min_runtime_days_relaxed: int | None = None
    sweep_n_trials: int | None = None
    sweep_top_k: int | None = None


@router.post("/strategy_factory")
async def update_strategy_factory_config(req: StrategyFactoryConfigUpdate):
    """Strategy Factory subsystem was removed with Position C (2026-07-02).

    This endpoint is kept for backward compatibility but performs no mutation and
    writes no audit event. The response explicitly signals that no runtime behavior
    changed. The current authority chain is scanner-family opportunities → ranker /
    System Operator → Proposal → Risk/Execution.
    """
    return {
        "deprecated": True,
        "supported": False,
        "reason": (
            "Strategy Factory subsystem removed with Position C (2026-07-02). "
            "These fields have no production consumer and saving them does not alter "
            "any runtime decision. Current path: scanner → ranker → System Operator "
            "→ Proposal → Risk/Execution."
        ),
        "fields": _STRATEGY_FACTORY_DEPRECATED_FIELDS,
        "updated": {},
    }


# ---------------------------------------------------------------------------
# Tunnel config (Cloudflare Tunnel)
# ---------------------------------------------------------------------------

class TunnelConfigUpdate(SettingsRequest):
    enabled: Optional[bool] = None
    tunnel_id: Optional[str] = None
    hostname: Optional[str] = None


class RemoteControlConfigUpdate(SettingsRequest):
    enabled: Optional[bool] = None
    bind_hosts: Optional[list[str]] = None
    require_token: Optional[bool] = None
    api_token: Optional[str] = Field(default=None, min_length=1)


@router.get("/remote-control/interfaces")
async def get_remote_control_interfaces():
    from aletheia.gateway.network import discover_ipv4_interfaces
    return {"interfaces": discover_ipv4_interfaces()}


@router.get("/tunnel/status")
async def get_tunnel_status():
    from aletheia.gateway.tunnel import tunnel_status
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(tunnel_status)


@router.post("/tunnel")
async def update_tunnel_config(req: TunnelConfigUpdate):
    cfg = load_config()
    old = cfg.tunnel.model_dump(mode="json")
    updates = req.model_dump(exclude_none=True)
    for field, value in updates.items():
        setattr(cfg.tunnel, field, value)
    save_config(cfg)
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "tunnel_config_changed",
                 "entity_type": "tunnel_config", "before": old,
                 "after": cfg.tunnel.model_dump(mode="json"),
                 "message": f"Tunnel config updated: {list(updates.keys())}"})
    tunnel_safe = {k: v for k, v in cfg.tunnel.model_dump(mode="json").items()
                   if k != "credentials_path"}
    return {"updated": updates, "tunnel": tunnel_safe}


@router.post("/remote-control")
async def update_remote_control_config(req: RemoteControlConfigUpdate):
    """Save the independent remote-control switch and optional write-only API token."""
    cfg = load_config()
    old_enabled = cfg.remote_control.enabled
    updates = req.model_dump(exclude_none=True)
    api_token = updates.pop("api_token", None)
    if "bind_hosts" in updates:
        from aletheia.gateway.network import normalize_remote_bind_hosts
        try:
            updates["bind_hosts"] = normalize_remote_bind_hosts(updates["bind_hosts"])
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    effective_enabled = updates.get("enabled", cfg.remote_control.enabled)
    effective_hosts = updates.get("bind_hosts", cfg.remote_control.bind_hosts)
    if effective_enabled and not effective_hosts:
        raise HTTPException(400, "Select at least one network adapter before enabling Remote Control")
    if updates.get("require_token") is True and api_token is None:
        from aletheia.config.secrets import get_secret
        if not (get_secret("remote_control_api_token") or get_secret("tunnel_api_token")):
            raise HTTPException(
                400,
                "Set an API token before enabling Remote Control Token protection",
            )
    if api_token is not None:
        from aletheia.config.secrets import set_secret
        set_secret("remote_control_api_token", api_token)
    if updates:
        for field, value in updates.items():
            setattr(cfg.remote_control, field, value)
        save_config(cfg)
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "remote_control_config_changed",
                 "entity_type": "remote_control_config", "before": {"enabled": old_enabled},
                 "after": cfg.remote_control.model_dump(mode="json"),
                 "message": "Remote Control config updated; gateway restart required for listener changes"})
    return {
        "updated": {**updates, "api_token_saved": api_token is not None},
        "restart_required": "enabled" in updates or "bind_hosts" in updates,
        "remote_control": cfg.remote_control.model_dump(mode="json"),
    }


@router.post("/tunnel/start")
async def start_tunnel_api():
    from aletheia.gateway.tunnel import start_tunnel
    from starlette.concurrency import run_in_threadpool
    result = await run_in_threadpool(start_tunnel)
    if not result.get("ok"):
        raise HTTPException(400, result.get("reason", "unknown error"))
    return result


@router.post("/tunnel/stop")
async def stop_tunnel_api():
    from aletheia.gateway.tunnel import stop_tunnel
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(stop_tunnel)


# ---------------------------------------------------------------------------
# Network / proxy config
# ---------------------------------------------------------------------------

class NetworkConfigUpdate(SettingsRequest):
    # Structured proxy definition — the panel drives these; proxy_url is derived.
    proxy_protocol: Optional[Literal["http", "https", "socks5"]] = None
    proxy_host: Optional[str] = None
    proxy_port: Optional[int] = None
    enforce: Optional[bool] = None
    no_proxy: Optional[str] = None
    # Legacy: accepted for backward compatibility (older clients sent only this).
    proxy_url: Optional[str] = None


@router.post("/network")
async def update_network_config(req: NetworkConfigUpdate):
    """Update global proxy configuration and apply it to the running process."""
    cfg = load_config()
    old = cfg.network.model_dump(mode="json")
    updates = req.model_dump(exclude_none=True)
    for field, value in updates.items():
        setattr(cfg.network, field, value)
    # setattr does not re-run the model validator, so keep proxy_url and the
    # structured parts consistent explicitly (structured inputs win; a legacy
    # proxy_url-only update is decomposed back into the parts).
    if {"proxy_protocol", "proxy_host", "proxy_port"} & set(updates):
        cfg.network.recompose_proxy_url()
    elif "proxy_url" in updates and cfg.network.proxy_url:
        cfg.network.parse_proxy_url()
        cfg.network.recompose_proxy_url()
    save_config(cfg)

    # Apply immediately: set env vars so trust_env=True clients pick them up.
    from aletheia.net import apply_proxy_env
    apply_proxy_env()

    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard",
                 "event_type": "network_config_changed",
                 "entity_type": "network_config", "before": _redacted_network(old),
                 "after": _redacted_network(cfg.network.model_dump(mode="json")),
                 "message": f"Network config updated: {list(updates.keys())}"})
    return {"updated": _redacted_network(updates), "network": _redacted_network(cfg.network.model_dump(mode="json"))}


@router.post("/network/test")
async def test_network_proxy():
    """Test proxy connectivity by pinging Binance API through the configured proxy."""
    import time
    from aletheia.net import httpx_client
    url = "https://api.binance.com/api/v3/ping"
    t0 = time.monotonic()
    try:
        async with httpx_client(timeout=10) as client:
            resp = await client.get(url)
        latency_ms = int((time.monotonic() - t0) * 1000)
        if resp.status_code == 200:
            return {"reachable": True, "latency_ms": latency_ms, "error": None}
        return {"reachable": False, "latency_ms": latency_ms, "error": f"HTTP {resp.status_code}"}
    except Exception as e:
        latency_ms = int((time.monotonic() - t0) * 1000)
        return {"reachable": False, "latency_ms": latency_ms, "error": str(e)[:200]}


# ---------------------------------------------------------------------------
# Historical Data config
# ---------------------------------------------------------------------------

class HistoricalDataConfigUpdate(SettingsRequest):
    enabled: bool | None = None
    max_bars_per_pair: int | None = None
    default_months: int | None = None
    nightly_backfill_enabled: bool | None = None
    nightly_backfill_hour_cst: int | None = None
    min_bars_for_backtest: int | None = None
    active_universe_backfill_enabled: bool | None = None
    active_universe_backfill_interval_minutes: int | None = None
    active_universe_backfill_max_pairs_per_run: int | None = None


_HIST_DATA_BOUNDS: dict[str, tuple[float, float]] = {
    "max_bars_per_pair": (1_000, 200_000),
    "default_months": (1, 24),
    "nightly_backfill_hour_cst": (0, 23),
    "min_bars_for_backtest": (30, 5_000),
    "active_universe_backfill_interval_minutes": (5, 60),
    "active_universe_backfill_max_pairs_per_run": (1, 10),
}


@router.post("/historical_data")
async def update_historical_data_config(req: HistoricalDataConfigUpdate):
    """Update historical OHLCV data management settings."""
    cfg = load_config()
    old = cfg.historical_data.model_dump(mode="json")
    updates = req.model_dump(exclude_none=True)
    for field, value in updates.items():
        if field in _HIST_DATA_BOUNDS:
            lo, hi = _HIST_DATA_BOUNDS[field]
            if not (lo <= value <= hi):
                raise HTTPException(400, f"'{field}' must be between {int(lo)} and {int(hi)}")
        setattr(cfg.historical_data, field, value)
    save_config(cfg)

    _SCHEDULER_FIELDS = {
        "nightly_backfill_enabled",
        "nightly_backfill_hour_cst",
        "active_universe_backfill_enabled",
        "active_universe_backfill_interval_minutes",
        "enabled",
    }
    rescheduled = False
    if _SCHEDULER_FIELDS.intersection(updates):
        from aletheia.agent_runtime.scheduler import reconcile_historical_data_jobs
        from starlette.concurrency import run_in_threadpool
        rescheduled = await run_in_threadpool(
            reconcile_historical_data_jobs, cfg.historical_data,
        )

    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard",
                 "event_type": "historical_data_config_changed",
                 "entity_type": "historical_data_config", "before": old,
                 "after": cfg.historical_data.model_dump(mode="json"),
                 "message": f"Historical data config updated: {list(updates.keys())}"})
    return {"updated": updates, "rescheduled": rescheduled, "historical_data": cfg.historical_data.model_dump(mode="json")}


# ---------------------------------------------------------------------------
# Event Trigger config
# ---------------------------------------------------------------------------

class EventTriggerConfigUpdate(SettingsRequest):
    enabled: bool | None = None
    price_move_pct_threshold: float | None = None
    volume_surge_multiplier: float | None = None
    watch_universe_members: bool | None = None
    # trigger_strategy_design and strategy_design_min_move_pct removed: DEAD CONFIG.
    # strategy_designer was removed with Position C (2026-07-02). Callers that send
    # these fields receive a 422 — the extra="forbid" SettingsRequest policy is
    # intentionally strict so UI/API mismatches fail loudly rather than silently.


_EVENT_TRIGGER_BOUNDS: dict[str, tuple[float, float]] = {
    "price_move_pct_threshold": (0.5, 20.0),
    "volume_surge_multiplier": (1.5, 20.0),
}


@router.post("/event_triggers")
async def update_event_trigger_config(req: EventTriggerConfigUpdate):
    """Update event-driven trigger thresholds."""
    cfg = load_config()
    old = cfg.event_triggers.model_dump(mode="json")
    updates = req.model_dump(exclude_none=True)
    for field, value in updates.items():
        if field in _EVENT_TRIGGER_BOUNDS:
            lo, hi = _EVENT_TRIGGER_BOUNDS[field]
            if not (lo <= value <= hi):
                raise HTTPException(400, f"'{field}' must be between {lo} and {hi}")
        setattr(cfg.event_triggers, field, value)
    save_config(cfg)
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard",
                 "event_type": "event_trigger_config_changed",
                 "entity_type": "event_trigger_config", "before": old,
                 "after": cfg.event_triggers.model_dump(mode="json"),
                 "message": f"Event trigger config updated: {list(updates.keys())}"})
    return {"updated": updates, "event_triggers": cfg.event_triggers.model_dump(mode="json")}


# ---------------------------------------------------------------------------
# News feeds config
# ---------------------------------------------------------------------------

class NewsFeedUpdate(SettingsRequest):
    name: str
    url: str
    source: str
    enabled: bool


class NewsConfigUpdate(SettingsRequest):
    enabled: Optional[bool] = None
    fetch_interval_minutes: Optional[int] = None
    retention_days: Optional[int] = None
    feeds: Optional[list[NewsFeedUpdate]] = None


@router.post("/news")
async def update_news_config(req: NewsConfigUpdate):
    cfg = load_config()
    old = cfg.news.model_dump(mode="json")
    if req.enabled is not None:
        cfg.news.enabled = req.enabled
    if req.fetch_interval_minutes is not None:
        if not (5 <= req.fetch_interval_minutes <= 120):
            raise HTTPException(400, "fetch_interval_minutes must be 5-120")
        cfg.news.fetch_interval_minutes = req.fetch_interval_minutes
    if req.retention_days is not None:
        if not (1 <= req.retention_days <= 90):
            raise HTTPException(400, "retention_days must be 1-90")
        cfg.news.retention_days = req.retention_days
    if req.feeds is not None:
        from aletheia.config.schema import NewsFeedEntry
        cfg.news.feeds = [NewsFeedEntry(**f.model_dump()) for f in req.feeds]
    save_config(cfg)

    rescheduled = False
    if (
        req.fetch_interval_minutes is not None
        and req.fetch_interval_minutes != old.get("fetch_interval_minutes")
    ):
        from aletheia.agent_runtime.scheduler import reschedule_news_fetch
        from starlette.concurrency import run_in_threadpool
        rescheduled = await run_in_threadpool(
            reschedule_news_fetch, cfg.news.fetch_interval_minutes,
        )

    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "news_config_changed",
                 "entity_type": "news_config", "before": old,
                 "after": cfg.news.model_dump(mode="json"),
                 "message": "News config updated"})
    return {"news": cfg.news.model_dump(mode="json"), "rescheduled": rescheduled}
