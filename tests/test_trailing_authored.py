"""Tests for AI-authored trailing (M4 / D2, exit-intent plan).

Trailing is authored on the ExitPlan: an operator spec overrides the global default,
'off' disables it, absence falls back to the global default (D2=A) unless
trailing_requires_authoring (D2=B). Behavior is unchanged for unauthored positions.
"""

import json
from types import SimpleNamespace

import pytest

import aletheia.portfolio.position_manager as pm


def _cfg(trail_enabled=True, atr_mult=1.5, pct=1.0, requires_authoring=False, breakeven=1.5):
    return SimpleNamespace(
        trading=SimpleNamespace(
            trail_enabled=trail_enabled,
            trail_atr_mult=atr_mult,
            trail_pct=pct,
            trailing_requires_authoring=requires_authoring,
            breakeven_trigger_pct=breakeven,
        )
    )


def _pos(**over):
    p = {"id": "t1", "symbol": "BTC/USDT", "direction": "long", "entry_price": 100.0}
    p.update(over)
    return p


@pytest.fixture(autouse=True)
def _no_atr(monkeypatch):
    # Deterministic: no ATR data → resolver picks pct mode.
    monkeypatch.setattr(pm, "_get_atr_pct", lambda s: None)


# ── _resolve_trailing ────────────────────────────────────────────────────────

def test_default_applies_when_unauthored():
    tc = pm._resolve_trailing(_pos(), _cfg(trail_enabled=True))
    assert tc is not None
    assert tc["mode"] == "pct" and tc["pct"] == 1.0
    assert tc["floor_at_breakeven"] is True
    assert tc["activation_profit_pct"] is None


def test_default_off_when_trail_disabled():
    assert pm._resolve_trailing(_pos(), _cfg(trail_enabled=False)) is None


def test_requires_authoring_disables_default():
    # D2=B: no spec + requires_authoring → no trailing
    assert pm._resolve_trailing(_pos(), _cfg(trail_enabled=True, requires_authoring=True)) is None


def test_operator_off_disables():
    pos = _pos(trailing_json=json.dumps({"mode": "off"}))
    assert pm._resolve_trailing(pos, _cfg()) is None


def test_operator_override():
    pos = _pos(trailing_json=json.dumps(
        {"mode": "atr", "distance": 2.0, "activation_profit_pct": 3.0, "floor_at_breakeven": False}
    ))
    tc = pm._resolve_trailing(pos, _cfg())
    assert tc["mode"] == "atr" and tc["atr_mult"] == 2.0
    assert tc["activation_profit_pct"] == 3.0
    assert tc["floor_at_breakeven"] is False


def test_authored_spec_runs_even_when_requires_authoring():
    pos = _pos(trailing_json=json.dumps({"mode": "pct", "distance": 0.5}))
    tc = pm._resolve_trailing(pos, _cfg(requires_authoring=True))
    assert tc is not None and tc["mode"] == "pct" and tc["pct"] == 0.5


# ── _maybe_update_trailing_stop behavior ─────────────────────────────────────

def _capture_writes(monkeypatch):
    import aletheia.portfolio.protective_barrier as barrier
    import aletheia.storage.repositories as repo

    calls = []
    monkeypatch.setattr(repo, "update_position", lambda pid, **kw: calls.append((pid, kw)))

    def _capture_plan(pid, plan, *, extra_position_updates=None, **_kwargs):
        updates = dict(extra_position_updates or {})
        updates["stop_price"] = plan.stop_price
        calls.append((pid, updates))
        return plan

    monkeypatch.setattr(barrier, "persist_position_exit_plan", _capture_plan)
    return calls


def test_operator_off_no_writes(monkeypatch):
    calls = _capture_writes(monkeypatch)
    pos = _pos(trailing_json=json.dumps({"mode": "off"}), stop_price=98.0)
    pm._maybe_update_trailing_stop(pos, 105.0, _cfg())
    assert calls == []


def test_activation_gate_defers(monkeypatch):
    calls = _capture_writes(monkeypatch)
    pos = _pos(
        trailing_json=json.dumps({"mode": "pct", "distance": 1.0, "activation_profit_pct": 10.0}),
        stop_price=98.0,
    )
    # profit at 105 = 5% < 10% activation → no trailing writes
    pm._maybe_update_trailing_stop(pos, 105.0, _cfg())
    assert calls == []


def test_default_trail_moves_stop(monkeypatch):
    calls = _capture_writes(monkeypatch)
    pos = _pos(stop_price=90.0)  # unauthored → global default pct=1.0
    pm._maybe_update_trailing_stop(pos, 110.0, _cfg(pct=1.0))
    # trail_distance = 110 * 1% = 1.1 → new_stop = 108.9 > 90 → update
    stops = [kw["stop_price"] for _, kw in calls if "stop_price" in kw]
    assert 108.9 in stops


def test_breakeven_opt_out(monkeypatch):
    calls = _capture_writes(monkeypatch)
    pos = _pos(
        trailing_json=json.dumps({"mode": "pct", "distance": 1.0, "floor_at_breakeven": False}),
        stop_price=95.0,
    )
    # profit at 110 = 10% clears breakeven, but opt-out → no floor-to-entry(100) move;
    # trailing still runs to 108.9
    pm._maybe_update_trailing_stop(pos, 110.0, _cfg())
    stops = [kw["stop_price"] for _, kw in calls if "stop_price" in kw]
    assert 100.0 not in stops       # breakeven did NOT fire
    assert 108.9 in stops           # trailing still applied
