"""Trading-persona resolver — the single seam sizing/risk read effective params through.

Design: docs/design/operator/dynamic_sizing_operator_persona_design.md
(three-layer decision box).

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

# Progressive unlock may only grant authority over parameters that have a
# production entry-sizing consumer. Other persona fields remain reference-only
# until their separate execution paths exist.
_PROGRESSIVE_UNLOCKABLE = frozenset({"notional_usdt", "kelly_fraction"})


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


def build_sizing_context(
    symbol: str | None = None,
    direction: str | None = None,
    cfg=None,
    *,
    apply_learning: bool = True,
) -> dict:
    """Gather the context the Operator uses to pick sizing WITHIN persona ranges (P2).

    Combines owner's three inputs — (b) 标的+市场情绪 (regime/F&G), (c) BTC 大盘 (proxied
    by the regime state, which is BTC-driven), and symbol volatility — plus recent
    account drawdown, into a single `risk_appetite` scalar in [0,1]:
      0.0 = defensive → pick the MIN of each range; 1.0 = aggressive → pick the MAX.

    Rule-based and fully defensive (never raises; missing data → neutral 0.5). The
    exact weights are a starting heuristic that Learning (P3) refines. `direction`
    lets regime alignment be directional (bull+long = aligned = more appetite).
    (a) 时段 is available via the returned dict for future rules; P2 keeps it neutral.
    """
    appetite = 0.5
    ctx: dict = {"risk_appetite": 0.5, "regime": None, "fng": None, "atr_pct": None}
    try:
        from aletheia.storage.repositories import get_latest_market_regime, latest_macro_snapshot
        reg = get_latest_market_regime()
        if reg:
            state = reg.get("state")
            conf = float(reg.get("confidence") or 0)
            ctx["regime"] = state
            if conf > 0.6:
                # (b)+(c): regime is BTC-macro-driven. Aligned direction → more appetite.
                if state == "bull":
                    appetite += 0.20 if direction != "short" else -0.20
                elif state == "bear":
                    appetite += 0.20 if direction == "short" else -0.20
                elif state == "volatile":
                    appetite -= 0.15
                # sideways → neutral

        macro = latest_macro_snapshot()
        if macro and macro.get("fng_value") is not None:
            fng = float(macro["fng_value"])
            ctx["fng"] = fng
            # Extreme sentiment (greed or fear) = higher reversal risk → defensive.
            if abs(fng - 50.0) > 30.0:
                appetite -= 0.15

        if symbol:
            from aletheia.trading.sizing import _lookup_atr_pct
            atr = _lookup_atr_pct(symbol)
            ctx["atr_pct"] = atr
            if atr is not None:
                if atr > 6.0:
                    appetite -= 0.15   # choppy/dangerous name → size down
                elif atr < 2.0:
                    appetite += 0.10

        # Account drawdown (defensive when bleeding). Reuse the risk engine's scale
        # (1.0=healthy, <1.0=in drawdown) as a direct appetite dampener.
        try:
            from aletheia.risk.engine import _portfolio_drawdown_scale
            from aletheia.config.loader import load_config as _lc
            _mode = (cfg or _lc()).exchange.paper_execution_mode or "paper_internal"
            dd = _portfolio_drawdown_scale(_mode)
            if dd < 1.0:
                appetite -= (1.0 - dd) * 0.3
        except Exception:
            pass
    except Exception:
        pass

    # P3: persona learning nudges appetite by the current regime bucket's clean-window
    # reward (dormant unless persona_learning_enabled + enough clean samples; bounded).
    if apply_learning:
        try:
            from aletheia.trading.sizing_policy import learned_appetite_adjustment
            adj = learned_appetite_adjustment(ctx.get("regime"), cfg)
            if adj:
                appetite += adj
                ctx["learned_adj"] = adj
        except Exception:
            pass

    ctx["risk_appetite"] = max(0.0, min(1.0, appetite))
    return ctx


def decide_param_value(prange, risk_appetite: float) -> float:
    """Pick a value within [min, max] by risk_appetite (linear). Higher appetite →
    larger value (bigger size/leverage/adds AND wider stop = more risk tolerance)."""
    a = max(0.0, min(1.0, float(risk_appetite)))
    return prange.min + a * (prange.max - prange.min)


def effective_operator_decidable(persona, cfg=None) -> dict:
    """Which params are CURRENTLY operator-decidable (P4 progressive unlock).

    - persona_progressive_unlock_enabled OFF → the static persona.operator_decidable
      (P1-P3 behavior, unchanged).
    - ON → read only the durable Persona rollout state. A parameter is decidable only
      after that exact rollout step has a prospective, scoped, stable verdict. Account
      DISTRESS (drawdown / kill switch) refreezes ALL (returns {}).
    """
    from aletheia.config.loader import load_config
    cfg = cfg or load_config()
    static = dict(persona.operator_decidable or {})
    if not getattr(cfg.trading, "persona_progressive_unlock_enabled", False):
        return static
    if not getattr(cfg.trading, "persona_sizing_enabled", False):
        return static
    ladder = [
        key for key in (persona.unlock_ladder or [])
        if key in _PROGRESSIVE_UNLOCKABLE
    ]
    if not ladder:
        return static
    # Once progressive mode is selected, ladder parameters are controlled only
    # by the durable rollout.  A stale static True must never bypass its gate.
    out = {key: value for key, value in static.items() if key not in ladder}
    try:
        from aletheia.trading.persona_rollout import rollout_runtime_safe

        if not rollout_runtime_safe(cfg):
            return {}
    except Exception:
        return {}
    try:
        from aletheia.trading.persona_rollout import current_persona_rollout

        rollout = current_persona_rollout(cfg, create=True)
        if not rollout:
            return {}
        status = str(rollout.get("status") or "")
        if status == "refrozen":
            return {}
        if status == "completed":
            unlocked_n = len(ladder)
        elif status == "unlocked":
            unlocked_n = min(len(ladder), int(rollout.get("current_step") or 0) + 1)
        elif status in {"frozen", "observing", "eligible"}:
            # A new parameter's control arm keeps earlier successful parameters
            # active while the current parameter remains frozen.
            unlocked_n = min(len(ladder), int(rollout.get("current_step") or 0))
        else:
            return {}
        for key in ladder[:unlocked_n]:
            out[key] = True
    except Exception:
        # A missing/invalid rollout state must never grant dynamic authority.
        return {}
    return out


def effective_sizing_params(
    cfg=None,
    symbol: str | None = None,
    direction: str | None = None,
    *,
    apply_learning: bool = True,
) -> dict:
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
    ctx = None  # built lazily, only if some param is operator_decidable
    # P4: progressive unlock may widen the decidable set beyond the static dict.
    decidable = effective_operator_decidable(persona, cfg) if persona is not None else {}
    for key, (attr, default) in _LEGACY_FALLBACK.items():
        legacy = getattr(cfg.trading, attr, default) if attr else default
        if persona is not None and key in (persona.params or {}):
            if decidable.get(key):
                # P2: the Operator decides WITHIN [min, max] by context. Only params
                # explicitly marked operator_decidable (progressive unlock, P4) take
                # this path; the rest stay pinned at init. Default personas mark none,
                # so this is dormant until the operator unlocks a param.
                if ctx is None:
                    ctx = build_sizing_context(
                        symbol, direction, cfg, apply_learning=apply_learning,
                    )
                out[key] = decide_param_value(persona.params[key], ctx["risk_appetite"])
            else:
                out[key] = float(persona.params[key].init)
        else:
            out[key] = legacy
    out["_persona"] = persona.name if persona is not None else "legacy"
    out["_risk_appetite"] = ctx["risk_appetite"] if ctx else None
    out["_regime"] = ctx.get("regime") if ctx else None
    out["_learned_adj"] = ctx.get("learned_adj", 0.0) if ctx else 0.0
    try:
        from aletheia.trading.persona_rollout import (
            build_sizing_manifest,
            current_persona_rollout,
        )

        rollout = (
            current_persona_rollout(
                cfg,
                create=bool(getattr(cfg.trading, "persona_progressive_unlock_enabled", False)),
            )
            if persona is not None
            else None
        )
        out["_persona_rollout"] = build_sizing_manifest(
            cfg,
            authorized_params=[key for key, value in decidable.items() if value],
            state=rollout,
        )
    except Exception:
        # Preserve the legacy resolver result, but do not fabricate rollout evidence.
        out["_persona_rollout"] = {
            "schema_version": "persona-progressive-unlock/v2",
            "persona_id": getattr(persona, "name", "unknown") if persona is not None else "legacy",
            "parameter_state": "unavailable",
            "assignment": "none",
            "active_params": [],
        }
    return out
