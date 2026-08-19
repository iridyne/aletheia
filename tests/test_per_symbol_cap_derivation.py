"""Check #19's ceiling is derived from sizing, so a first entry can never trip it.

The bug this guards against: `max_per_symbol_notional_usdt` was a fixed $400
while sizing produced `equity × per_trade_risk_pct × leverage`. Once equity
passed $400 / (20% × 3x) = $666.67, every first entry on every symbol was
rejected and the account deadlocked at zero positions — 68 of 75 rejections in
a 48h window on the live paper deployment.

A count-based cap cannot drift that way at any account size or leverage, which
is what these tests pin.
"""

import pytest

from aletheia.risk.engine import _derive_per_symbol_cap


def _first_entry_notional(equity: float, risk_pct: float, leverage: float) -> float:
    """What sizing produces — mirrored here so drift shows up as a test failure."""
    return equity * (risk_pct / 100.0) * leverage


@pytest.mark.parametrize("equity", [100.0, 668.52648937, 10_000.0, 250_000.0, 10_000_000.0])
@pytest.mark.parametrize("leverage", [1.0, 3.0, 5.0, 10.0, 20.0])
@pytest.mark.parametrize("risk_pct", [0.25, 2.0, 20.0])
@pytest.mark.parametrize("entries", [1, 3, 10])
def test_first_entry_never_exceeds_derived_cap(equity, leverage, risk_pct, entries):
    """The invariant. No combination of account size and leverage may break it."""
    cap = _derive_per_symbol_cap(equity, risk_pct, leverage, entries)
    first_entry = _first_entry_notional(equity, risk_pct, leverage)

    # Check #19 rejects on strict `>`, so equality at entries=1 must still pass.
    assert first_entry <= cap
    # And a first entry is exactly its 1/N share of the cap.
    assert first_entry == pytest.approx(cap / entries)


def test_the_exact_fedora_deadlock_no_longer_rejects():
    """The observed failure, reproduced against the derived cap.

    Fedora: equity $668.52648937, per-trade risk 20%, leverage 3x → $401.12
    notional against a fixed $400 cap → `exceeds_per_symbol_notional:LINK/USDT:401>400`.
    """
    equity, risk_pct, leverage = 668.52648937, 20.0, 3.0
    first_entry = _first_entry_notional(equity, risk_pct, leverage)
    assert first_entry == pytest.approx(401.11589362)

    # The retired absolute cap rejected this outright.
    assert first_entry > 400.0

    # The derived cap admits it, with room for the configured add-on entries.
    cap = _derive_per_symbol_cap(equity, risk_pct, leverage, entries=3)
    assert cap == pytest.approx(1203.34768086)
    assert first_entry <= cap


def test_cap_scales_with_equity_so_it_cannot_go_stale():
    """Growing the account must not turn the cap into a binding constraint."""
    risk_pct, leverage, entries = 20.0, 3.0, 3
    for equity in (668.52648937, 6_685.26, 668_526.48):
        cap = _derive_per_symbol_cap(equity, risk_pct, leverage, entries)
        assert _first_entry_notional(equity, risk_pct, leverage) <= cap


def test_zero_entries_disables_and_unavailable_base_yields_no_ceiling():
    # 0 entries is the documented "disabled" setting.
    assert _derive_per_symbol_cap(668.0, 20.0, 3.0, 0) == 0.0
    # An unresolvable capital base must not fabricate a ceiling out of nothing.
    assert _derive_per_symbol_cap(0.0, 20.0, 3.0, 3) == 0.0
    assert _derive_per_symbol_cap(-1.0, 20.0, 3.0, 3) == 0.0
    assert _derive_per_symbol_cap(668.0, 0.0, 3.0, 3) == 0.0


def test_spot_leverage_is_floored_at_one():
    """Spot passes leverage 0/None-ish; the cap must not collapse to zero."""
    assert _derive_per_symbol_cap(1000.0, 10.0, 0.0, 1) == 100.0
    assert _derive_per_symbol_cap(1000.0, 10.0, 0.5, 1) == 100.0
