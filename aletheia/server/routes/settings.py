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
    return cfg.model_dump(mode="json")


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
