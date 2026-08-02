"""Exchange unification map — single source of truth for CLI and Web routes."""

from __future__ import annotations

# Maps exchange_id → (ccxt_exchange_name, paper_execution_mode)
EXCHANGE_UNIFICATION_MAP: dict[str, tuple[str, str]] = {
    "okx": ("okx", "okx_demo"),
    "bitget": ("bitget", "bitget_demo"),
    "binance": ("binance", "binance_testnet"),
}

# Maps exchange_id → live-limited execution mode (issue #418).
LIVE_MODE_MAP: dict[str, str] = {
    "bitget": "bitget_live_limited",
    "okx": "okx_live_limited",
    "binance": "binance_live_limited",
}

VALID_EXCHANGES: list[str] = sorted(EXCHANGE_UNIFICATION_MAP.keys()) + ["paper_internal"]


def resolve_execution_mode(cfg=None) -> str:
    """Return the canonical execution mode for new proposals given current config.

    Issue #418: when live.state == 'live_limited' AND exchange.mode == 'live',
    route proposals to the selected broker's live-limited mode instead of always
    using exchange.paper_execution_mode (which is restricted to paper/demo values).

    In all other cases (paper, demo, or live state not yet unlocked) the configured
    paper_execution_mode is returned unchanged.
    """
    if cfg is None:
        from aletheia.config.loader import load_config
        cfg = load_config()
    if (
        getattr(cfg.exchange, "mode", "paper") == "live"
        and getattr(cfg.live, "state", "live_disabled") == "live_limited"
    ):
        live_mode = LIVE_MODE_MAP.get((cfg.exchange.name or "").lower())
        if live_mode:
            return live_mode
        # Unknown exchange or missing name — fail closed to paper to avoid silent
        # live routing with an unrecognised mode.
        import logging as _log
        _log.getLogger("aletheia.config.exchanges").warning(
            "resolve_execution_mode: live unlock active but exchange '%s' has no "
            "live-mode mapping — falling back to paper_execution_mode",
            cfg.exchange.name,
        )
    return cfg.exchange.paper_execution_mode or "paper_internal"
