"""Settings API routes."""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from aletheia.config.loader import load_config, save_config

router = APIRouter(prefix="/api/settings", tags=["settings"])

VALID_PERMISSION_MODES = {"Observe", "Paper", "Full"}


class PermissionModeRequest(BaseModel):
    mode: str


class ExchangeModeRequest(BaseModel):
    mode: str


@router.get("")
async def get_settings():
    cfg = load_config()
    providers_safe = {}
    for name, p in cfg.models.providers.items():
        providers_safe[name] = {
            "base_url": p.base_url,
            "protocol": p.protocol,
            "model": p.model,
            "enabled": p.enabled,
            "mode": p.mode,
        }
    return {
        "app": cfg.app.model_dump(mode="json"),
        "runtime": cfg.runtime.model_dump(mode="json"),
        "exchange": cfg.exchange.model_dump(mode="json"),
        "live": cfg.live.model_dump(mode="json"),
        "agent": cfg.agent.model_dump(mode="json"),
        "models": {"default_provider": cfg.models.default_provider, "providers": providers_safe},
        "risk": cfg.risk.model_dump(mode="json"),
        "telegram": {"enabled": cfg.telegram.enabled},
        "update": {"enabled": cfg.update.enabled, "branch": cfg.update.branch},
        "trading": cfg.trading.model_dump(mode="json"),
        "health": cfg.health.model_dump(mode="json"),
        "retention": cfg.retention.model_dump(mode="json"),
        "telegram": {
            "enabled": cfg.telegram.enabled,
            "allowed_chat_ids": cfg.telegram.allowed_chat_ids,
        },
        "market_data": {
            "observed_symbols": cfg.market_data.observed_symbols,
            "observed_instruments": cfg.market_data.observed_instruments,
            "ccxt_exchange": cfg.market_data.ccxt_exchange,
        },
        "universe": cfg.universe.model_dump(mode="json"),
    }


# ---------------------------------------------------------------------------
# Agent config
# ---------------------------------------------------------------------------

class AutonomousLearningRequest(BaseModel):
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


# ---------------------------------------------------------------------------
# Exchange markets config
# ---------------------------------------------------------------------------

class ExchangeMarketsRequest(BaseModel):
    spot: bool
    usdt_futures: bool


@router.post("/exchange-markets")
async def set_exchange_markets(req: ExchangeMarketsRequest):
    if not req.spot and not req.usdt_futures:
        raise HTTPException(400, "At least one market must be enabled")
    cfg = load_config()
    old = dict(cfg.exchange.enabled_markets)
    cfg.exchange.enabled_markets = {"spot": req.spot, "usdt_futures": req.usdt_futures}
    save_config(cfg)
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "exchange_markets_changed",
                 "entity_type": "exchange_config", "before": old,
                 "after": cfg.exchange.enabled_markets,
                 "message": f"enabled_markets updated"})
    return {"enabled_markets": cfg.exchange.enabled_markets}


# ---------------------------------------------------------------------------
# Runtime config
# ---------------------------------------------------------------------------

class RuntimeConfigUpdate(BaseModel):
    scheduler_enabled: Optional[bool] = None
    git_enabled: Optional[bool] = None
    runtime_continuity_enabled: Optional[bool] = None
    runtime_continuity_lock_ttl_sec: Optional[int] = None
    runtime_continuity_shadow_port_offset: Optional[int] = None


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
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "runtime_config_changed",
                 "entity_type": "runtime_config", "before": old,
                 "after": cfg.runtime.model_dump(mode="json"),
                 "message": f"Runtime config updated: {list(updates.keys())}"})
    return {"updated": updates}


# ---------------------------------------------------------------------------
# App config
# ---------------------------------------------------------------------------

class AppConfigUpdate(BaseModel):
    auto_open_browser: Optional[bool] = None


@router.post("/app")
async def update_app_config(req: AppConfigUpdate):
    cfg = load_config()
    old = cfg.app.model_dump(mode="json")
    updates = req.model_dump(exclude_none=True)
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
# Health monitoring config
# ---------------------------------------------------------------------------

class HealthConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    alert_via_telegram: Optional[bool] = None
    max_agent_failure_ratio: Optional[float] = None
    llm_unavailable_alert_after: Optional[int] = None
    market_data_max_staleness_min: Optional[int] = None
    supervisor_max_restarts: Optional[int] = None
    supervisor_restart_window_sec: Optional[int] = None
    supervisor_backoff_cap_sec: Optional[int] = None
    require_llm_at_startup: Optional[bool] = None
    startup_gate_max_retries: Optional[int] = None
    startup_gate_retry_delay_sec: Optional[int] = None


_HEALTH_BOUNDS: dict[str, tuple[float, float]] = {
    "max_agent_failure_ratio": (0.0, 1.0),
    "llm_unavailable_alert_after": (1, 100),
    "market_data_max_staleness_min": (5, 1440),
    "supervisor_max_restarts": (1, 50),
    "supervisor_restart_window_sec": (60, 86400),
    "supervisor_backoff_cap_sec": (5, 3600),
    "startup_gate_max_retries": (1, 10),
    "startup_gate_retry_delay_sec": (5, 120),
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

class RetentionConfigUpdate(BaseModel):
    agent_runs_days: Optional[int] = None
    tool_calls_days: Optional[int] = None
    run_steps_days: Optional[int] = None
    market_snapshots_days: Optional[int] = None
    portfolio_snapshots_days: Optional[int] = None
    jsonl_max_mb: Optional[float] = None


_RETENTION_BOUNDS: dict[str, tuple[float, float]] = {
    "agent_runs_days": (1, 365),
    "tool_calls_days": (1, 365),
    "run_steps_days": (1, 365),
    "market_snapshots_days": (1, 365),
    "portfolio_snapshots_days": (1, 1825),
    "jsonl_max_mb": (10.0, 10000.0),
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

class TelegramConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    allowed_chat_ids: Optional[list[int]] = None


@router.post("/telegram")
async def update_telegram_config(req: TelegramConfigUpdate):
    cfg = load_config()
    old = cfg.telegram.model_dump(mode="json")
    updates = req.model_dump(exclude_none=True)
    for field, value in updates.items():
        setattr(cfg.telegram, field, value)
    save_config(cfg)
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "telegram_config_changed",
                 "entity_type": "telegram_config", "before": old,
                 "after": cfg.telegram.model_dump(mode="json"),
                 "message": f"Telegram config updated: {list(updates.keys())}"})
    return {"updated": updates}


# ---------------------------------------------------------------------------
# Update config
# ---------------------------------------------------------------------------

class UpdateConfigUpdate(BaseModel):
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
    return {"old": old, "new": req.mode}




@router.post("/exchange-mode")
async def set_exchange_mode(req: ExchangeModeRequest):
    if req.mode not in ("demo", "live"):
        raise HTTPException(400, "Mode must be 'demo' or 'live'")
    cfg = load_config()
    old = cfg.exchange.mode
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

class RiskConfigUpdate(BaseModel):
    max_daily_loss_pct: Optional[float] = None
    max_single_trade_risk_pct: Optional[float] = None
    max_total_live_exposure_pct: Optional[float] = None
    max_live_position_notional_usdt: Optional[float] = None
    max_live_limited_cap_usdt: Optional[float] = None
    starting_capital_usdt: Optional[float] = None
    max_demo_position_notional_usdt: Optional[float] = None
    max_demo_single_trade_risk_pct: Optional[float] = None
    max_futures_leverage_live: Optional[int] = None
    max_futures_leverage_demo: Optional[int] = None
    max_open_positions_live: Optional[int] = None
    max_open_positions_demo: Optional[int] = None
    allow_short_demo: Optional[bool] = None
    allow_short_live: Optional[bool] = None


# Fields that must never be changed via the dashboard (safety invariants).
_RISK_IMMUTABLE = {"allowed_exchange", "require_risk_check_for_all_orders"}

_RISK_BOUNDS: dict[str, tuple[float, float]] = {
    "max_daily_loss_pct": (0.1, 20.0),
    "max_single_trade_risk_pct": (0.01, 10.0),
    "max_total_live_exposure_pct": (1.0, 50.0),
    "max_live_position_notional_usdt": (1.0, 100_000.0),
    "max_live_limited_cap_usdt": (1.0, 100_000.0),
    "starting_capital_usdt": (1.0, 100_000.0),
    "max_demo_position_notional_usdt": (1.0, 100_000.0),
    "max_demo_single_trade_risk_pct": (0.01, 20.0),
    "max_futures_leverage_live": (1, 20),
    "max_futures_leverage_demo": (1, 125),
    "max_open_positions_live": (1, 50),
    "max_open_positions_demo": (1, 200),
}


@router.post("/risk")
async def update_risk_config(req: RiskConfigUpdate):
    """Update editable risk limits. Immutable fields are rejected."""
    cfg = load_config()
    old = cfg.risk.model_dump(mode="json")
    updates = req.model_dump(exclude_none=True)

    for field, value in updates.items():
        if field in _RISK_IMMUTABLE:
            raise HTTPException(400, f"Field '{field}' cannot be modified via the dashboard")
        if field in _RISK_BOUNDS:
            lo, hi = _RISK_BOUNDS[field]
            if not (lo <= value <= hi):
                raise HTTPException(400, f"'{field}' must be between {lo} and {hi}, got {value}")
        setattr(cfg.risk, field, value)

    save_config(cfg)
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "risk_config_changed",
                 "entity_type": "risk_config", "before": old,
                 "after": cfg.risk.model_dump(mode="json"),
                 "message": f"Risk config updated: {list(updates.keys())}"})
    return {"updated": updates, "risk": cfg.risk.model_dump(mode="json")}


# ---------------------------------------------------------------------------
# Symbol filter (whitelist/blacklist mode + single symbol list)
# ---------------------------------------------------------------------------

VALID_SYMBOL_FILTER_MODES = {"whitelist", "blacklist"}


class SymbolsUpdate(BaseModel):
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
    return {"mode": req.mode, "symbols": normalized}


# ---------------------------------------------------------------------------
# Trading config (paper position management)
# ---------------------------------------------------------------------------

class TradingConfigUpdate(BaseModel):
    default_stop_loss_pct: Optional[float] = None
    default_take_profit_pct: Optional[float] = None
    max_position_hold_hours: Optional[float] = None
    min_confidence_to_trade: Optional[str] = None


_TRADING_BOUNDS: dict[str, tuple[float, float]] = {
    "default_stop_loss_pct": (0.1, 50.0),
    "default_take_profit_pct": (0.1, 100.0),
    "max_position_hold_hours": (1.0, 8760.0),
}
_VALID_CONFIDENCE = {"high", "medium", "speculative"}


@router.post("/trading")
async def update_trading_config(req: TradingConfigUpdate):
    """Update paper position management parameters."""
    cfg = load_config()
    old = cfg.trading.model_dump(mode="json")
    updates = req.model_dump(exclude_none=True)

    for field, value in updates.items():
        if field == "min_confidence_to_trade":
            if value not in _VALID_CONFIDENCE:
                raise HTTPException(400, f"min_confidence_to_trade must be one of {sorted(_VALID_CONFIDENCE)}")
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
    return {"updated": updates, "trading": cfg.trading.model_dump(mode="json")}


# ---------------------------------------------------------------------------
# Universe config
# ---------------------------------------------------------------------------

class UniverseConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    refresh_interval_min: Optional[int] = None
    max_candidates: Optional[int] = None
    core_pool_size: Optional[int] = None
    min_volume_24h_usdt: Optional[float] = None
    max_spread_bps: Optional[float] = None
    explore_ratio: Optional[float] = None
    blacklist: Optional[list[str]] = None


_UNIVERSE_BOUNDS: dict[str, tuple[float, float]] = {
    "refresh_interval_min": (1, 60),
    "max_candidates": (5, 100),
    "core_pool_size": (1, 50),
    "min_volume_24h_usdt": (100_000, 500_000_000),
    "max_spread_bps": (1, 200),
    "explore_ratio": (0.0, 0.5),
}


@router.post("/universe")
async def update_universe_config(req: UniverseConfigUpdate):
    """Update universe generator parameters."""
    cfg = load_config()
    old = cfg.universe.model_dump(mode="json")
    updates = req.model_dump(exclude_none=True)

    for field, value in updates.items():
        if field in _UNIVERSE_BOUNDS:
            lo, hi = _UNIVERSE_BOUNDS[field]
            if not (lo <= value <= hi):
                raise HTTPException(400, f"'{field}' must be between {lo} and {hi}")
        setattr(cfg.universe, field, value)

    save_config(cfg)
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "universe_config_changed",
                 "entity_type": "universe_config", "before": old,
                 "after": cfg.universe.model_dump(mode="json"),
                 "message": f"Universe config updated: {list(updates.keys())}"})
    return {"updated": updates, "universe": cfg.universe.model_dump(mode="json")}
