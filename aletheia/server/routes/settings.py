"""Settings API routes."""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from aletheia.config.loader import load_config, save_config

router = APIRouter(prefix="/api/settings", tags=["settings"])

VALID_PERMISSION_MODES = {"ReadOnly", "Research", "PaperAuto", "AllowEditing", "Auto"}


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
    }


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


class AutonomousPaperTradingRequest(BaseModel):
    enabled: bool


@router.post("/autonomous-paper-trading")
async def set_autonomous_paper_trading(req: AutonomousPaperTradingRequest):
    cfg = load_config()
    old = cfg.agent.autonomous_paper_trading
    cfg.agent.autonomous_paper_trading = req.enabled
    save_config(cfg)
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "autonomous_paper_trading_changed",
                 "entity_type": "agent_config", "before": {"autonomous_paper_trading": old},
                 "after": {"autonomous_paper_trading": req.enabled},
                 "message": f"autonomous_paper_trading {old} -> {req.enabled}"})
    return {"old": old, "new": req.enabled}


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
# Tradable symbols (risk.allowed_symbols + exchange.tradable_symbols)
# ---------------------------------------------------------------------------

class SymbolsUpdate(BaseModel):
    symbols: list[str]


@router.post("/symbols")
async def update_tradable_symbols(req: SymbolsUpdate):
    """Update allowed trading symbols. Both risk and exchange lists are updated atomically."""
    if not req.symbols:
        raise HTTPException(400, "symbols list must not be empty")
    # Normalize: uppercase, strip whitespace, deduplicate, keep order
    normalized = list(dict.fromkeys(s.strip().upper() for s in req.symbols if s.strip()))
    if not normalized:
        raise HTTPException(400, "No valid symbols provided")

    cfg = load_config()
    old_risk = list(cfg.risk.allowed_symbols)
    old_exchange = list(cfg.exchange.tradable_symbols)

    cfg.risk.allowed_symbols = normalized
    cfg.exchange.tradable_symbols = normalized
    save_config(cfg)

    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "symbols_changed",
                 "entity_type": "symbols",
                 "before": {"risk": old_risk, "exchange": old_exchange},
                 "after": {"risk": normalized, "exchange": normalized},
                 "message": f"Tradable symbols updated: {normalized}"})
    return {"symbols": normalized}


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
