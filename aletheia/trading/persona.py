"""Trading-persona resolver — the single seam sizing/risk read effective params through.

Design: docs/design/dynamic_sizing_operator_persona_design.md (three-layer decision box).

- Persona provides bounds [min, init, max] per param.
- P1 (this module): the effective value is the active persona's `init`.
- P2 will let the System Operator pick within [min, max] by context.
- P3 will let Learning fit that choice.
- The risk-engine hard caps remain the OUTER envelope, applied downstream — nothing
  here can exceed them.

When `trading.persona_sizing_enabled` is False (DEFAULT), `effective_sizing_params`
returns the legacy flat-config values → ZERO behavior change. This is the flag that
keeps every persona feature dormant until the operator opts in.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("aletheia.trading.persona")

# Param keys a persona may define, mapped to the legacy flat-config attribute used
# when persona sizing is OFF (or a param is absent from the active persona).
_LEGACY_FALLBACK = {
    "notional_usdt": ("default_notional_usdt", 25.0),
    "kelly_fraction": ("vol_kelly_cap_fraction", 0.25),
    "per_trade_loss_pct": ("default_stop_loss_pct", 2.0),
    # No flat-config equivalent for these — legacy behavior is "feature off".
    "leverage_max": (None, None),   # legacy leverage lives in RiskConfig tiers
    "max_adds": (None, 0),          # legacy: no pyramiding
    "add_fraction": (None, 0.0),
}


def get_active_persona(cfg=None):
    """Return the active TradingPersona, or None when persona sizing is disabled /
    the active name is unknown (caller then uses legacy flat config)."""
    from aletheia.config.loader import load_config
    cfg = cfg or load_config()
    if not getattr(cfg.trading, "persona_sizing_enabled", False):
        return None
    name = getattr(cfg.trading, "active_persona", "conservative")
    persona = (cfg.trading.personas or {}).get(name)
    if persona is None:
        logger.warning("active_persona %r not found in personas — using legacy sizing", name)
    return persona


def effective_sizing_params(cfg=None) -> dict:
    """Resolve the effective sizing params for the current decision.

    Returns a dict of {param_key: value}. P1 semantics:
      - persona sizing OFF (or no active persona) → legacy flat-config values.
      - persona sizing ON → the active persona's `init` for each defined param,
        falling back to legacy for any param the persona omits.

    NOTE: this is the DECISION-INPUT layer only. Downstream risk-engine hard caps
    still bound the final size — a persona `init` above a cap is clamped by the
    engine, never here.
    """
    from aletheia.config.loader import load_config
    cfg = cfg or load_config()
    persona = get_active_persona(cfg)

    out: dict = {}
    for key, (attr, default) in _LEGACY_FALLBACK.items():
        legacy = getattr(cfg.trading, attr, default) if attr else default
        if persona is not None and key in (persona.params or {}):
            # P1: effective value = init. (P2 will decide within [min, max].)
            out[key] = float(persona.params[key].init)
        else:
            out[key] = legacy
    out["_persona"] = persona.name if persona is not None else "legacy"
    return out
