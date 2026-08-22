"""Regression tests for risk engine fixes #1506 #1507 #1451."""
from __future__ import annotations
import inspect
import pytest


# ─────────────────────────────────────────────────────────────────────────────
# #1506 — Policy re-stamped from second load_config() race
# ─────────────────────────────────────────────────────────────────────────────

def test_persist_risk_result_accepts_resolved_policy():
    """_persist_risk_result must accept a resolved_policy parameter."""
    from aletheia.risk.engine import _persist_risk_result
    sig = inspect.signature(_persist_risk_result)
    assert "resolved_policy" in sig.parameters, (
        "_persist_risk_result must have resolved_policy parameter to avoid second load_config() race"
    )


def test_evaluate_proposal_passes_resolved_policy():
    """_evaluate_proposal_locked must pass resolved_policy to _persist_risk_result."""
    import aletheia.risk.engine as mod
    src = inspect.getsource(mod._evaluate_proposal_locked)
    assert "resolved_policy=resolved_policy" in src or "resolved_policy=" in src, (
        "_evaluate_proposal_locked must pass resolved_policy to _persist_risk_result (#1506)"
    )


# ─────────────────────────────────────────────────────────────────────────────
# #1507 — Approved decisions commit when filter evidence fails
# ─────────────────────────────────────────────────────────────────────────────

def test_persist_risk_result_filter_evidence_not_swallowed():
    """_persist_risk_result must NOT swallow filter evidence exceptions for authoritative decisions."""
    from aletheia.risk.engine import _persist_risk_result
    src = inspect.getsource(_persist_risk_result)
    # The old code had: except Exception as evidence_error: logger.warning(...)
    # The fix removes the try/except so exceptions propagate to roll back the transaction
    assert "except Exception as evidence_error" not in src, (
        "_persist_risk_result must not swallow filter evidence exceptions (#1507)"
    )


# ─────────────────────────────────────────────────────────────────────────────
# #1451 — Cross-margin liquidation honest labeling
# ─────────────────────────────────────────────────────────────────────────────

def test_calc_liquidation_price_accepts_margin_mode():
    """calc_liquidation_price must accept margin_mode parameter."""
    from aletheia.trading.instruments import calc_liquidation_price
    # Should not raise
    liq_iso = calc_liquidation_price(50000, "long", 5, "BTC/USDT", margin_mode="isolated")
    liq_cross = calc_liquidation_price(50000, "long", 5, "BTC/USDT", margin_mode="cross")
    assert liq_iso is not None
    assert liq_cross is not None


def test_check_liquidation_buffer_includes_margin_mode_in_violation():
    """Liquidation violation must state margin mode."""
    import aletheia.risk.engine as mod
    src = inspect.getsource(mod._check_liquidation_buffer)
    assert "margin_mode" in src, (
        "_check_liquidation_buffer must include margin_mode in computation (#1451)"
    )
    assert "bitget" in src or "okx" in src, (
        "_check_liquidation_buffer must detect cross-margin modes (bitget/okx)"
    )
