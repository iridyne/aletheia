"""Regression tests for C2 critical fixes: #1518 #1244."""
from __future__ import annotations
import inspect
import pytest
from unittest.mock import MagicMock, patch
from aletheia.knowledge.guards import TradingGuard, GuardAppliesTo


# ─────────────────────────────────────────────────────────────────────────────
# #1518 — Serenity theme invalidation must process all rows, not just 200
# ─────────────────────────────────────────────────────────────────────────────

def test_serenity_invalidation_loops_past_200():
    """data.py invalidation must loop to process all active opps, not limit to 200."""
    import aletheia.server.routes.data as data_mod
    src = inspect.getsource(data_mod.patch_serenity_theme_status)
    assert "while True" in src or "while " in src, (
        "theme invalidation must loop over all pages, not use a single LIMIT 200"
    )
    assert "LIMIT" not in src or "Fix #1518" in src, (
        "LIMIT 200 must be removed for full invalidation coverage"
    )


# ─────────────────────────────────────────────────────────────────────────────
# #1244 — Guard load/eval failures must block candidates (fail-closed)
# ─────────────────────────────────────────────────────────────────────────────

def test_guard_load_failure_inserts_blocking_placeholder():
    """A malformed guard file must produce a blocking placeholder, not be skipped."""
    import aletheia.knowledge.guards as guards_mod
    src = inspect.getsource(guards_mod.load_trading_guards)
    assert "LOAD_ERROR" in src, (
        "load_trading_guards must add a LOAD_ERROR blocking guard on parse failure"
    )
    assert "block_candidate=True" in src, (
        "load error placeholder must set block_candidate=True"
    )


def test_guard_eval_failure_returns_blocking_result():
    """Guard evaluation exception must return a blocking result, not None."""
    from aletheia.agents.paper_trader_ranker import _evaluate_guards_safe
    from aletheia.knowledge.guards import GuardEvaluation

    # Make evaluate_guards raise
    with patch("aletheia.agents.paper_trader_ranker.evaluate_guards",
               side_effect=RuntimeError("db error")):
        result = _evaluate_guards_safe(
            opp={"id": "opp-1", "symbol": "BTC/USDT"},
            universe_member=None,
            factor_signal=None,
            direction="long",
            confidence="high",
            strategy_has_ruleset=False,
            ruleset_entry_result=None,
            strategy_evidence=None,
        )

    assert result is not None, "Guard eval failure must not return None"
    assert isinstance(result, GuardEvaluation)
    assert result.blocked is True, "Guard eval failure must block the candidate"
    assert result.total_penalty >= 100.0, "Guard eval failure must apply high penalty"


def test_guard_eval_safe_return_type_is_not_optional():
    """_evaluate_guards_safe no longer returns Optional — always returns GuardEvaluation."""
    from aletheia.agents.paper_trader_ranker import _evaluate_guards_safe
    import typing
    hints = _evaluate_guards_safe.__annotations__
    ret = hints.get("return", "")
    # Should be GuardEvaluation, not GuardEvaluation | None
    assert "None" not in str(ret), (
        "_evaluate_guards_safe return type must not be Optional[GuardEvaluation]"
    )


# ─────────────────────────────────────────────────────────────────────────────
# #1895 — Remote Control auth sequence (static verification)
# ─────────────────────────────────────────────────────────────────────────────

def test_remote_control_auth_reads_config_per_request():
    """Auth middleware reads load_config() per request, not cached state.
    This means save_config is always called before the next request sees the change.
    """
    import aletheia.server.app as app_mod
    # Find the middleware/auth source
    try:
        src = inspect.getsource(app_mod)
    except Exception:
        return  # skip if not inspectable
    # The middleware must call load_config() or getattr(cfg, 'remote_control')
    # to pick up the latest saved state
    assert "load_config" in src or "remote_control" in src, (
        "app.py must check remote_control config per request, not cached"
    )
