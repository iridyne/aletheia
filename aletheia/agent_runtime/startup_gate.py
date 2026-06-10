"""Fail-loud LLM startup gate.

At gateway startup we probe every enabled model provider with a REAL inference
call. If none can actually complete a request, the trading loop must NOT run —
a gateway with no working LLM produces only neutral/speculative opportunities and
never trades, while every dashboard looks "alive". That is the "False confidence"
failure mode: process up, system dead.

Why a real `complete()` call and not `router.healthcheck_all()`:
  - claude_cli's healthcheck returns available=True whenever the `claude` binary
    exists (it only runs `--version` + lists hardcoded models), even when real
    inference times out on every call — the exact zombie this gate exists to catch.
  - healthcheck_all() also silently skips keyless/default-URL HTTP providers.
Neither answers the only question that matters here: "can this provider actually
do inference right now?" A minimal real completion does.

This module only DIAGNOSES (returns an honest dict, never raises). The caller
(server lifespan) decides what to do with the verdict — see app.py.
"""

import asyncio
import logging

from aletheia.model_provider.base import ModelRequest

logger = logging.getLogger("aletheia.startup_gate")

# A real completion can be slow on a cold gateway endpoint; keep the probe short
# enough that startup isn't blocked for long, but long enough for a genuine call.
_PROBE_TIMEOUT_SEC = 20
_PROBE_MAX_TOKENS = 5


def _ordered_enabled_providers(cfg) -> list[str]:
    """Enabled provider names, default first — we report on what the system uses."""
    providers = cfg.models.providers
    names = [name for name, pcfg in providers.items() if getattr(pcfg, "enabled", False)]
    default = cfg.models.default_provider
    if default in names:
        names.remove(default)
        names.insert(0, default)
    return names


async def _probe_one(name: str) -> dict:
    """Run one real minimal completion. Returns {provider, usable, error}."""
    try:
        from aletheia.model_provider.router import get_provider
        provider = get_provider(name)
        resp = await asyncio.wait_for(
            provider.complete(ModelRequest(
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=_PROBE_MAX_TOKENS,
            )),
            timeout=_PROBE_TIMEOUT_SEC,
        )
        content = (resp.content or "").strip() if resp is not None else ""
        if content:
            return {"provider": name, "usable": True, "error": None}
        return {"provider": name, "usable": False, "error": "empty_response"}
    except asyncio.TimeoutError:
        return {"provider": name, "usable": False, "error": f"timeout>{_PROBE_TIMEOUT_SEC}s"}
    except Exception as e:  # unknown provider type, network, auth, etc.
        return {"provider": name, "usable": False, "error": str(e)[:200]}


async def probe_llm_providers_at_startup(cfg) -> dict:
    """Probe all enabled providers with a real inference call. Never raises.

    CONTRACT: `cfg` must be the live, on-disk config (i.e. `bootstrap()`/
    `load_config()` result). We enumerate provider NAMES from `cfg`, but
    `get_provider(name)` instantiates from the on-disk config it loads itself —
    so passing a cfg whose providers differ from disk would probe the wrong
    instances. The only production caller (server lifespan) passes the bootstrap
    cfg, so they always match.

    Returns:
        {
          "any_usable": bool,   # at least one provider completed a real call
          "results": [ {provider, usable, error}, ... ],
          "checked": int,       # number of enabled providers probed
        }
    """
    names = _ordered_enabled_providers(cfg)
    if not names:
        logger.warning("LLM startup gate: no enabled providers configured")
        return {"any_usable": False, "results": [], "checked": 0}

    results: list[dict] = []
    for name in names:
        r = await _probe_one(name)
        results.append(r)
        if r["usable"]:
            logger.info("LLM startup gate: provider '%s' usable", name)
        else:
            logger.warning("LLM startup gate: provider '%s' NOT usable (%s)", name, r["error"])

    any_usable = any(r["usable"] for r in results)
    return {"any_usable": any_usable, "results": results, "checked": len(names)}
