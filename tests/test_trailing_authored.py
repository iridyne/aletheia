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
    monkeypatch.setattr(pm, "_get_atr_observation", lambda *args, **kwargs: None)


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
    import aletheia.portfolio.exit_plan as exit_plan

    calls = []

    def _capture_plan(pid, plan, *, exit_plan_updates=None, **_kwargs):
        updates = dict(exit_plan_updates or {})
        updates["stop_price"] = plan.stop_price
        calls.append((pid, updates))
        return plan

    monkeypatch.setattr(exit_plan, "save_exit_plan", _capture_plan)
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


def test_atr_revision_persists_exact_runtime_observation(monkeypatch):
    import aletheia.portfolio.exit_plan as exit_plan

    captured = []
    observation = {
        "source": "universe_feature_snapshots",
        "snapshot_id": "snapshot-1",
        "snapshot_at": "2026-08-11T03:25:00+00:00",
        "venue": "bitget",
        "symbol": "BTC/USDT",
        "market_type": "spot",
        "timeframe": "1h",
        "bar_close_ms": 1786417200000,
        "input_fingerprint": "bars-sha",
        "algorithm_version": "technical-context-v1",
        "atr_pct": 2.0,
        "observed_at": "2026-08-11T03:30:00+00:00",
    }
    monkeypatch.setattr(pm, "_get_atr_observation", lambda *args, **kwargs: observation)
    monkeypatch.setattr(
        exit_plan,
        "save_exit_plan",
        lambda _pid, plan, **_kwargs: captured.append(plan) or plan,
    )
    monkeypatch.setattr(
        "aletheia.execution.stops.replace_exchange_protective_stop",
        lambda *_args, **_kwargs: False,
    )
    pos = _pos(
        execution_mode="bitget_demo",
        market_type="spot",
        trailing_json=json.dumps({"mode": "atr", "distance": 1.5}),
        stop_price=95.0,
        exit_plan_version=1,
    )

    assert pm._maybe_update_trailing_stop(
        pos,
        110.0,
        _cfg(),
        effective_at="2026-08-11T03:30:00+00:00",
    ) is True
    assert captured[0].trailing["last_runtime_observation"] == {
        "distance_source": "atr",
        **observation,
        "exchange_synced": False,
    }


def _venue_down():
    raise RuntimeError("venue down")


@pytest.mark.parametrize(
    ("amend", "synced"),
    [(lambda: True, True), (lambda: False, False), (_venue_down, False)],
    ids=["amended", "amend_failed", "amend_raised"],
)
def test_trailing_reports_the_venue_amend_honestly_and_keeps_the_local_backstop(monkeypatch, amend, synced):
    """#3848 PR-B: the trailed stop is amended at the venue first; whatever the venue
    answers, the tighter local stop is kept as the backstop and `exchange_synced`
    records the venue's actual answer (it used to read True after a failed replace)."""
    import aletheia.portfolio.exit_plan as exit_plan

    order = []
    saved = []
    monkeypatch.setattr(
        "aletheia.execution.stops.replace_exchange_protective_stop",
        lambda position_id, stop: order.append(("amend", position_id, stop)) or amend(),
    )
    monkeypatch.setattr(
        exit_plan, "save_exit_plan",
        lambda _pid, plan, **_kwargs: order.append(("save",)) or saved.append(plan) or plan,
    )
    pos = _pos(
        execution_mode="bitget_demo", market_type="usdt_futures",
        trailing_json=json.dumps({"mode": "pct", "distance": 1.0, "floor_at_breakeven": False}),
        stop_price=95.0, exit_plan_version=1,
    )

    assert pm._maybe_update_trailing_stop(pos, 110.0, _cfg()) is True

    assert order == [("amend", "t1", 108.9), ("save",)]
    assert saved[0].stop_price == 108.9
    assert pos["stop_price"] == 108.9
    assert saved[0].trailing["last_runtime_observation"]["exchange_synced"] is synced


def test_trailing_never_loosens_the_stop(monkeypatch):
    calls = _capture_writes(monkeypatch)
    amends = []
    monkeypatch.setattr(
        "aletheia.execution.stops.replace_exchange_protective_stop",
        lambda *args: amends.append(args) or True,
    )
    pos = _pos(
        direction="short", entry_price=100.0, stop_price=100.5, trail_high_water=99.5,
        trailing_json=json.dumps({"mode": "pct", "distance": 1.0, "floor_at_breakeven": False}),
    )
    # A short's trail at 110 would sit at 99.5 + 1.1 = 100.6, looser than the 100.5 stop.
    pm._maybe_update_trailing_stop(pos, 110.0, _cfg())
    assert [kw for _, kw in calls if "stop_price" in kw] == []
    assert amends == []
