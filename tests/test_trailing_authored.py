"""Tests for ExitPlan-authored trailing (M4 / D2).

Only a frozen per-position trailing spec grants behavior authority. Global config
controls authoring for new positions; it never retrofits an unauthored Position.
"""

import json
from types import SimpleNamespace

import pytest

import aletheia.portfolio.position_manager as pm


def _cfg(trail_enabled=True, atr_mult=1.5, pct=1.0, breakeven=1.5):
    return SimpleNamespace(
        trading=SimpleNamespace(
            trail_enabled=trail_enabled,
            trail_atr_mult=atr_mult,
            trail_pct=pct,
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

@pytest.mark.parametrize("trail_enabled", [True, False])
def test_unauthored_position_never_resolves_trailing(trail_enabled):
    assert pm._resolve_trailing(_pos(), _cfg(trail_enabled=trail_enabled)) is None


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


def test_authored_spec_runs_even_when_global_authoring_is_disabled():
    pos = _pos(trailing_json=json.dumps({"mode": "pct", "distance": 0.5}))
    tc = pm._resolve_trailing(pos, _cfg(trail_enabled=False))
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


def test_unauthored_position_never_moves_stop(monkeypatch):
    calls = _capture_writes(monkeypatch)
    pos = _pos(stop_price=90.0)
    pm._maybe_update_trailing_stop(pos, 110.0, _cfg(pct=1.0))
    assert calls == []


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
