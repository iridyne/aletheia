"""Regression tests for economics fixes #1488 #1622 #1624 #1626."""
from __future__ import annotations
import inspect
import math
import pytest


# ─────────────────────────────────────────────────────────────────────────────
# #1488 — Non-finite floats sanitized in usage insights
# ─────────────────────────────────────────────────────────────────────────────

def test_sanitize_floats_replaces_inf_with_none():
    from aletheia.server.routes.economics import _sanitize_floats
    assert _sanitize_floats(float("inf")) is None
    assert _sanitize_floats(float("-inf")) is None
    assert _sanitize_floats(float("nan")) is None


def test_sanitize_floats_keeps_finite():
    from aletheia.server.routes.economics import _sanitize_floats
    assert _sanitize_floats(3.14) == pytest.approx(3.14)
    assert _sanitize_floats(0.0) == 0.0


def test_sanitize_floats_nested_dict():
    from aletheia.server.routes.economics import _sanitize_floats
    result = _sanitize_floats({"cost": float("inf"), "tokens": 100, "sub": {"rate": float("nan")}})
    assert result["cost"] is None
    assert result["tokens"] == 100
    assert result["sub"]["rate"] is None


def test_usage_insights_route_uses_sanitize():
    """usage_insights route must call _sanitize_floats on the result."""
    import aletheia.server.routes.economics as mod
    src = inspect.getsource(mod.usage_insights)
    assert "_sanitize_floats" in src, (
        "usage_insights route must sanitize non-finite floats before return (#1488)"
    )


# ─────────────────────────────────────────────────────────────────────────────
# #1622 — Concurrent budget admission serialized
# ─────────────────────────────────────────────────────────────────────────────

def test_budget_admission_lock_exists():
    """_BUDGET_ADMISSION_LOCK must exist in fallback module."""
    from aletheia.model_provider.fallback import _BUDGET_ADMISSION_LOCK
    import threading
    assert isinstance(_BUDGET_ADMISSION_LOCK, type(threading.Lock()))


def test_complete_with_fallback_uses_admission_lock():
    """complete_with_fallback must acquire _BUDGET_ADMISSION_LOCK around observe+enforce."""
    import aletheia.model_provider.fallback as mod
    src = inspect.getsource(mod.complete_with_fallback)
    assert "_BUDGET_ADMISSION_LOCK" in src, (
        "complete_with_fallback must use _BUDGET_ADMISSION_LOCK for atomic admission (#1622)"
    )


# ─────────────────────────────────────────────────────────────────────────────
# #1624 — Hard enforcement fires even without shadow observation
# ─────────────────────────────────────────────────────────────────────────────

def test_enforce_budget_computes_fresh_when_no_observations():
    """_enforce_budget_if_configured must compute observations when shadow is off."""
    import aletheia.model_provider.fallback as mod
    src = inspect.getsource(mod._enforce_budget_if_configured)
    assert "check_would_block" in src, (
        "_enforce_budget_if_configured must compute fresh observations when shadow is off (#1624)"
    )
    # Must not early-return if enforcement is active
    assert "enforcement_mode == \"warn_only\"" in src
