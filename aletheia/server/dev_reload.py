"""Dev-reload safety guard and shared state.

All auto-reload features are gated on ALETHEIA_DEV_RELOAD=1 AND live trading
being disabled. This module is the single source of truth for that decision.
"""

import os
import time

_boot_time = time.time()
_reload_counter = 0


def is_dev_reload_enabled() -> bool:
    if os.environ.get("ALETHEIA_DEV_RELOAD") != "1":
        return False
    try:
        from aletheia.config.loader import load_config
        cfg = load_config()
        if cfg.live.state != "live_disabled":
            return False
    except Exception:
        return False
    return True


def record_reload() -> str:
    global _reload_counter
    _reload_counter += 1
    return f"reload_{int(time.time())}_{_reload_counter}"


def get_status() -> dict:
    return {
        "enabled": is_dev_reload_enabled(),
        "env_set": os.environ.get("ALETHEIA_DEV_RELOAD") == "1",
        "reload_count": _reload_counter,
        "uptime_sec": round(time.time() - _boot_time, 1),
    }
