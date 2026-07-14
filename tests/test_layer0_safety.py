"""Tests for Layer-0 hard safety exits (M6, exit-intent plan).

catastrophic_stop and runtime liquidation-proximity are the deterministic backstops
that make handing stop discretion to the System Operator safe. They must fire BEFORE
any strategy exit and cannot be relaxed by a position's own stop.
"""

from types import SimpleNamespace

import aletheia.portfolio.position_manager as pm
from aletheia.trading.instruments import calc_liquidation_price


def _cfg(**risk_over):
    risk = SimpleNamespace(
        catastrophic_stop_enabled=True,
        catastrophic_stop_pct=10.0,
        catastrophic_stop_pct_serenity=15.0,
        runtime_liquidation_monitor_enabled=True,
        runtime_liquidation_buffer_pct=3.0,
    )
    for k, v in risk_over.items():
        setattr(risk, k, v)
    return SimpleNamespace(risk=risk)


def _pos(direction="long", entry=100.0, leverage=0, school="short_term", symbol="BTC/USDT"):
    return {
        "direction": direction,
        "entry_price": entry,
        "leverage": leverage,
        "trade_school": school,
        "symbol": symbol,
    }


# ── catastrophic drawdown ────────────────────────────────────────────────────

def test_catastrophic_long_fires_beyond_pct():
    # -11% ≥ 10% → force close; -9% → no
    assert pm._layer0_safety_exit(_pos(), 89.0, _cfg()) == ("catastrophic_stop", 89.0)
    assert pm._layer0_safety_exit(_pos(), 91.0, _cfg()) is None


def test_catastrophic_short_fires_beyond_pct():
    # short: price rising is the loss. +11% → close
    assert pm._layer0_safety_exit(_pos(direction="short"), 111.0, _cfg()) == (
        "catastrophic_stop", 111.0,
    )
    assert pm._layer0_safety_exit(_pos(direction="short"), 109.0, _cfg()) is None


def test_catastrophic_is_school_aware():
    # serenity tolerates a wider drawdown (15% vs short_term 10%)
    ser = _pos(school="serenity")
    assert pm._layer0_safety_exit(ser, 88.0, _cfg()) is None            # -12% < 15%
    assert pm._layer0_safety_exit(ser, 84.0, _cfg()) == ("catastrophic_stop", 84.0)  # -16%
    # same price DOES fire for short_term (10%)
    assert pm._layer0_safety_exit(_pos(), 88.0, _cfg()) == ("catastrophic_stop", 88.0)


def test_catastrophic_disabled():
    assert pm._layer0_safety_exit(
        _pos(), 50.0, _cfg(catastrophic_stop_enabled=False, runtime_liquidation_monitor_enabled=False)
    ) is None


def test_guards_zero_entry_or_price():
    assert pm._layer0_safety_exit(_pos(entry=0.0), 89.0, _cfg()) is None
    assert pm._layer0_safety_exit(_pos(), 0.0, _cfg()) is None


# ── runtime liquidation proximity (leveraged only) ───────────────────────────

def test_liquidation_proximity_fires_within_buffer():
    # isolate the liq branch: catastrophic off so it doesn't shadow the check
    cfg = _cfg(catastrophic_stop_enabled=False, runtime_liquidation_buffer_pct=3.0)
    pos = _pos(leverage=5)
    liq = calc_liquidation_price(100.0, "long", 5, "BTC/USDT")
    assert liq is not None
    near = liq * 1.01   # ~1% above liq → within 3% buffer
    far = liq * 1.05    # ~4.8% above liq → outside buffer
    assert pm._layer0_safety_exit(pos, near, cfg) == ("liquidation_proximity", near)
    assert pm._layer0_safety_exit(pos, far, cfg) is None


def test_liquidation_monitor_ignores_unleveraged():
    cfg = _cfg(catastrophic_stop_enabled=False)
    liq = calc_liquidation_price(100.0, "long", 5, "BTC/USDT")
    # leverage <=1 → no liq price → no proximity exit even right at the level
    assert pm._layer0_safety_exit(_pos(leverage=1), liq, cfg) is None


def test_liquidation_monitor_disabled():
    cfg = _cfg(catastrophic_stop_enabled=False, runtime_liquidation_monitor_enabled=False)
    liq = calc_liquidation_price(100.0, "long", 5, "BTC/USDT")
    assert pm._layer0_safety_exit(_pos(leverage=5), liq, cfg) is None


# ── integration: catastrophic pre-empts stop_loss and routes the close ───────

def test_catastrophic_preempts_and_routes_close(monkeypatch):
    """A -15% crash must close with reason 'catastrophic_stop', NOT 'stop_loss',
    even though the default 2% stop would also fire — Layer-0 runs first."""
    from aletheia.storage.sqlite import get_db

    db = get_db()
    db.execute(
        "INSERT INTO positions (id,symbol,market_type,direction,quantity,entry_price,"
        "execution_mode,status) VALUES "
        "('c1','BTC/USDT','usdt_futures','long',1.0,100.0,'paper_internal','open')"
    )

    monkeypatch.setattr(pm, "get_fresh_price_for_protective_close", lambda sym, market_type, *, venue="": 85.0)  # -15% crash

    calls = []

    def _fake_route(position_id, reason, fallback_exit_price=None, **kw):
        calls.append((position_id, reason, fallback_exit_price))
        return {"closed": True}

    monkeypatch.setattr(pm, "close_position_routed", _fake_route)

    result = pm.evaluate_open_positions()

    assert result["catastrophic_exits"] == 1
    assert calls == [("c1", "catastrophic_stop", 85.0)]
