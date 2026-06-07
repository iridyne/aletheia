"""Settings API routes."""

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
