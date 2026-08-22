"""Regression tests for C3/C4 critical fixes.

#1964: non-USDT quote currency rejected at Risk boundary
#1605: secrets CLI re-reads live state after prompt
#1998: drain verifies process actually stops
#1999: rollback verifies target process alive before route switch
"""
from __future__ import annotations
import inspect
import pytest
import os
from unittest.mock import patch, MagicMock


# ─────────────────────────────────────────────────────────────────────────────
# #1964 — Non-USDT pairs rejected by Risk engine
# ─────────────────────────────────────────────────────────────────────────────

def test_non_usdt_pair_rejected_by_risk():
    """ETH/BTC (non-USDT quote) must produce unsupported_quote_currency violation."""
    from aletheia.risk.engine import _native_input_violations
    from aletheia.storage.models import TradeProposal

    proposal = TradeProposal(
        symbol="ETH/BTC",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=0.1,
        price=0.05,
        execution_mode="paper_internal",
        source="native",
    )
    violations = _native_input_violations(proposal)
    assert any("unsupported_quote_currency" in v for v in violations), (
        f"ETH/BTC must be rejected with unsupported_quote_currency, got: {violations}"
    )


def test_usdt_pair_not_rejected():
    """ETH/USDT must not produce quote currency violation."""
    from aletheia.risk.engine import _native_input_violations
    from aletheia.storage.models import TradeProposal

    proposal = TradeProposal(
        symbol="ETH/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=0.1,
        price=2000.0,
        execution_mode="paper_internal",
        source="native",
    )
    violations = _native_input_violations(proposal)
    assert not any("unsupported_quote_currency" in v for v in violations), (
        f"ETH/USDT should not have quote violation, got: {violations}"
    )


def test_usdt_futures_perp_symbol_not_rejected():
    """BTC/USDT:USDT (perpetual) must not be rejected for currency."""
    from aletheia.risk.engine import _native_input_violations
    from aletheia.storage.models import TradeProposal

    proposal = TradeProposal(
        symbol="BTC/USDT:USDT",
        market_type="usdt_futures",
        direction="long",
        order_type="market",
        quantity=0.01,
        price=50000.0,
        leverage=5,
        execution_mode="paper_internal",
        source="native",
    )
    violations = _native_input_violations(proposal)
    assert not any("unsupported_quote_currency" in v for v in violations), (
        f"BTC/USDT:USDT should not have quote violation, got: {violations}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# #1605 — Secrets CLI re-reads live state after prompt
# ─────────────────────────────────────────────────────────────────────────────

def test_secrets_cli_rechecks_live_state_after_prompt():
    """CLI set_cmd source must check live state AFTER the prompt, not only before."""
    import aletheia.cli.secrets_cmd as mod
    src = inspect.getsource(mod.set_cmd)
    # The fix adds a second load_config() call after typer.prompt
    prompt_idx = src.index("typer.prompt")
    post_prompt = src[prompt_idx:]
    assert "load_config" in post_prompt, (
        "secrets set_cmd must call load_config() after the prompt to catch live-unlock race (#1605)"
    )
    assert "live_limited" in post_prompt, (
        "secrets set_cmd must re-check live.state after prompt"
    )


# ─────────────────────────────────────────────────────────────────────────────
# #1998 — Drain verifies process stops
# ─────────────────────────────────────────────────────────────────────────────

def test_is_process_alive_own_pid():
    """_is_process_alive returns True for own PID."""
    from aletheia.runtime_continuity.service import _is_process_alive
    assert _is_process_alive(os.getpid())


def test_is_process_alive_nonexistent():
    """_is_process_alive returns False for nonexistent PID."""
    from aletheia.runtime_continuity.service import _is_process_alive
    assert not _is_process_alive(99999999)


def test_kill_shadow_process_raises_on_timeout():
    """_kill_shadow_process raises RuntimeTransitionError when process survives SIGTERM."""
    from aletheia.runtime_continuity.service import _kill_shadow_process, RuntimeTransitionError

    runtime = {"runtime_id": "r1", "metadata": {"gateway_process": {"pid": 12345}}}
    with (
        patch("aletheia.runtime_continuity.service._is_process_alive", return_value=True),
        patch("aletheia.runtime_continuity.service.os.kill"),
        patch("aletheia.runtime_continuity.service.time.monotonic",
              side_effect=[0.0, 0.0, 10.1]),  # immediately timeout
        patch("aletheia.runtime_continuity.service.time.sleep"),
    ):
        with pytest.raises(RuntimeTransitionError, match="process_still_alive_after_drain_timeout"):
            _kill_shadow_process(runtime, force=False, timeout_seconds=10.0)


def test_kill_shadow_process_force_sends_sigkill():
    """With force=True, SIGKILL is sent if process survives SIGTERM."""
    from aletheia.runtime_continuity.service import _kill_shadow_process
    import signal

    runtime = {"runtime_id": "r1", "metadata": {"gateway_process": {"pid": 12345}}}
    signals_sent = []

    with (
        patch("aletheia.runtime_continuity.service._is_process_alive", return_value=True),
        patch("aletheia.runtime_continuity.service.os.kill",
              side_effect=lambda pid, sig: signals_sent.append(sig)),
        patch("aletheia.runtime_continuity.service.time.monotonic",
              side_effect=[0.0, 0.0, 10.1]),
        patch("aletheia.runtime_continuity.service.time.sleep"),
    ):
        _kill_shadow_process(runtime, force=True, timeout_seconds=10.0)

    assert signal.SIGKILL in signals_sent, "SIGKILL must be sent when force=True and process survives"


# ─────────────────────────────────────────────────────────────────────────────
# #1999 — Rollback verifies target process alive
# ─────────────────────────────────────────────────────────────────────────────

def test_apply_activation_rejects_dead_process():
    """_apply_activation must raise RuntimeTransitionError when backend_pid is dead."""
    from aletheia.runtime_continuity.service import RuntimeContinuityService, RuntimeTransitionError

    svc = RuntimeContinuityService.__new__(RuntimeContinuityService)
    activation = {
        "strategy": "frontdoor_route_switch",
        "backend_base_url": "http://127.0.0.1:9999",
        "runtime_id": "r-dead",
        "backend_pid": 99999999,  # nonexistent PID
    }
    with pytest.raises(RuntimeTransitionError, match="target_process_dead"):
        svc._apply_activation(activation, actor="test", cause="test")
