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
import socket as _socket

from aletheia.model_provider.base import ModelRequest

logger = logging.getLogger("aletheia.startup_gate")


def _is_dns_error(exc: BaseException) -> bool:
    """True when exc looks like a DNS resolution failure (getaddrinfo / WSAHOST_NOT_FOUND).

    DNS failures won't resolve within the inter-attempt retry window, so the
    caller short-circuits retries when EVERY provider fails this way — the
    auto-resume gate re-probes on its own schedule once the network returns.
    """
    msg = str(exc).lower()
    if "getaddrinfo" in msg or "name or service not known" in msg:
        return True
    # httpx wraps the underlying socket.gaierror in __cause__/__context__.
    cause = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
    if isinstance(cause, _socket.gaierror):
        return True
    if isinstance(exc, OSError) and getattr(exc, "errno", None) == 11001:  # WSAHOST_NOT_FOUND
        return True
    return False

# A real completion can be slow on a cold gateway endpoint; keep the probe short
# enough that startup isn't blocked for long, but long enough for a genuine call.
# Reasoning models (e.g. Qwen3.5) need more budget: 5 tokens are exhausted by the
# reasoning phase with nothing left for text output — bump to 32 so there's room
# for both phases. The completion_tokens fallback below covers edge cases.
_PROBE_TIMEOUT_SEC = 20
_PROBE_MAX_TOKENS = 32


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
        from aletheia.model_provider.fallback import complete_with_fallback
        resp = await asyncio.wait_for(
            complete_with_fallback(ModelRequest(
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=_PROBE_MAX_TOKENS,
            ), provider_name=name),
            timeout=_PROBE_TIMEOUT_SEC,
        )
        content = (resp.content or "").strip() if resp is not None else ""
        if content:
            return {"provider": name, "usable": True, "error": None}
        # Reasoning models may still exhaust the budget on the reasoning phase;
        # completion_tokens > 0 proves the model ran — treat as usable.
        usage = (resp.raw or {}).get("usage", {}) if resp is not None else {}
        if usage.get("completion_tokens", 0) > 0:
            return {"provider": name, "usable": True, "error": None}
        return {"provider": name, "usable": False, "error": "empty_response"}
    except asyncio.TimeoutError:
        return {"provider": name, "usable": False, "error": f"timeout>{_PROBE_TIMEOUT_SEC}s"}
    except Exception as e:  # unknown provider type, network, auth, etc.
        # 429 means the provider is alive but rate-limiting — not truly unavailable.
        if "429" in str(e):
            return {"provider": name, "usable": True, "error": None}
        return {
            "provider": name, "usable": False, "error": str(e)[:200],
            "dns_error": _is_dns_error(e),
        }


async def _probe_all_once(names: list[str]) -> dict:
    """Single round: probe all providers concurrently. Returns verdict dict."""
    results: list[dict] = list(await asyncio.gather(*(_probe_one(n) for n in names)))

    for r in results:
        if r["usable"]:
            logger.info("LLM startup gate: provider '%s' usable", r["provider"])
        else:
            logger.warning("LLM startup gate: provider '%s' NOT usable (%s)", r["provider"], r["error"])

    any_usable = any(r["usable"] for r in results)
    return {"any_usable": any_usable, "results": results, "checked": len(names)}


async def probe_llm_providers_at_startup(cfg, *, max_retries: int | None = None) -> dict:
    """Probe all enabled providers with a real inference call, retrying on failure.

    Retries up to `cfg.health.startup_gate_max_retries` times (default 3) with
    `cfg.health.startup_gate_retry_delay_sec` (default 15) between attempts.
    Returns as soon as any attempt finds a usable provider.

    `max_retries` overrides the configured retry count for callers that must not
    block for the full startup window — e.g. a MANUAL operator resume passes 1 so
    the HTTP handler returns within a single probe (≤ timeout) instead of ~90 s.

    Returns:
        {
          "any_usable": bool,
          "results": [ {provider, usable, error}, ... ],
          "checked": int,
          "attempts": int,   # how many rounds were actually run
        }
    """
    names = _ordered_enabled_providers(cfg)
    if not names:
        logger.warning("LLM startup gate: no enabled providers configured")
        return {"any_usable": False, "results": [], "checked": 0, "attempts": 0}

    if max_retries is None:
        max_retries = getattr(cfg.health, "startup_gate_max_retries", 3)
    retry_delay = getattr(cfg.health, "startup_gate_retry_delay_sec", 15)

    result: dict = {}
    attempt = 0
    for attempt in range(1, max_retries + 1):
        result = await _probe_all_once(names)
        if result["any_usable"]:
            result["attempts"] = attempt
            return result
        if attempt < max_retries:
            # If EVERY failed provider failed with a DNS-class error, retrying is
            # futile — DNS won't resolve in retry_delay seconds when it just failed
            # for all hosts. Bail early; auto_resume re-probes once the net returns.
            failed = [r for r in result["results"] if not r["usable"]]
            if failed and all(r.get("dns_error", False) for r in failed):
                logger.warning(
                    "LLM startup gate: all providers failed with DNS errors on "
                    "attempt %d/%d (getaddrinfo) — skipping remaining retries; "
                    "auto_resume will re-probe when the network recovers",
                    attempt, max_retries,
                )
                break
            logger.warning(
                "LLM startup gate: attempt %d/%d failed, retrying in %ds...",
                attempt, max_retries, retry_delay,
            )
            await asyncio.sleep(retry_delay)

    result["attempts"] = attempt
    return result
