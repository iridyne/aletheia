"""Tests for proactive exit — market / limit + local OCO (M3.5, exit-intent plan).

The Operator can exit ahead of the passive stop/target: market now, or a resting
limit at a better price. The limit is watched locally by the executor and is OCO with
the stop (checked first, so a stop wins a simultaneous cross).
"""

from types import SimpleNamespace

import aletheia.portfolio.position_manager as pm


def _cfg():
    return SimpleNamespace(
        trading=SimpleNamespace(
            default_stop_loss_pct=2.0, default_take_profit_pct=4.0, max_position_hold_hours=72.0
        ),
        serenity=SimpleNamespace(max_hold_days=90),
        risk=SimpleNamespace(horizon_hard_backstop_mult=3.0),
    )


def _pos(**over):
    p = {"id": "x1", "symbol": "BTC/USDT", "direction": "long", "entry_price": 100.0}
    p.update(over)
    return p


def _insert_open(db, pid, **cols):
    base = {
        "id": pid, "symbol": "BTC/USDT", "market_type": "usdt_futures", "direction": "long",
        "quantity": 1.0, "entry_price": 100.0, "execution_mode": "paper_internal", "status": "open",
    }
    base.update(cols)
    keys = ",".join(base)
    ph = ",".join("?" for _ in base)
    db.execute(f"INSERT INTO positions ({keys}) VALUES ({ph})", tuple(base.values()))


# ── executor: operator_limit_exit ────────────────────────────────────────────

def test_decide_exit_limit_long():
    cfg = _cfg()
    # tp far (200) so it doesn't pre-empt; stop default 98
    assert pm._decide_exit(_pos(take_profit_price=200.0, exit_limit_price=110.0), 105.0, cfg) is None
    assert pm._decide_exit(_pos(take_profit_price=200.0, exit_limit_price=110.0), 111.0, cfg) == (
        "operator_limit_exit", 110.0,
    )


def test_decide_exit_limit_short():
    cfg = _cfg()
    pos = _pos(direction="short", take_profit_price=80.0, exit_limit_price=90.0)  # stop default 102
    assert pm._decide_exit(pos, 95.0, cfg) is None
    assert pm._decide_exit(pos, 89.0, cfg) == ("operator_limit_exit", 90.0)


def test_stop_wins_oco_over_limit():
    cfg = _cfg()
    # both a stop (99) and a far limit (110); a downside crash hits the stop first
    pos = _pos(stop_price=99.0, take_profit_price=200.0, exit_limit_price=110.0)
    assert pm._decide_exit(pos, 98.0, cfg) == ("stop_loss", 99.0)


# ── proactive_exit ───────────────────────────────────────────────────────────

def test_proactive_market_closes(monkeypatch):
    from aletheia.storage.sqlite import get_db

    _insert_open(get_db(), "m1")
    calls = []
    monkeypatch.setattr(pm, "_latest_price", lambda s: 100.0)
    monkeypatch.setattr(
        pm, "close_position_routed",
        lambda pid, **kw: calls.append((pid, kw)) or {"closed": True},
    )
    res = pm.proactive_exit("m1", order_type="market", reason="trend broke")
    assert res["closed"] is True
    assert calls[0][0] == "m1"
    assert calls[0][1]["reason"] == "operator_close"
    assert calls[0][1]["decision_actor"] == "system_operator"


def test_proactive_limit_sets_level():
    from aletheia.storage.sqlite import get_db
    from aletheia.storage.repositories import _select_one

    _insert_open(get_db(), "l1")
    res = pm.proactive_exit("l1", order_type="limit", limit_price=110.0, reason="scale out at resistance")
    assert res["pending_limit_exit"] is True
    assert _select_one("positions", "l1")["exit_limit_price"] == 110.0


def test_proactive_limit_requires_price():
    from aletheia.storage.sqlite import get_db

    _insert_open(get_db(), "l2")
    assert pm.proactive_exit("l2", order_type="limit", limit_price=None).get("error") == "limit_price_required"


def test_proactive_missing_position():
    assert pm.proactive_exit("nope").get("error") == "position_not_found_or_not_open"


def test_schema_has_order_type_and_limit():
    from aletheia.agent_shell.trading_decision import _DECISION_SCHEMA
    props = _DECISION_SCHEMA["properties"]
    assert "order_type" in props and "limit_price" in props
    assert props["order_type"]["enum"] == ["market", "limit"]


# ── end-to-end: a resting limit exit fires through the main loop ─────────────

def test_limit_exit_executes_via_loop(monkeypatch):
    from aletheia.storage.sqlite import get_db

    _insert_open(get_db(), "e1", exit_limit_price=110.0)
    monkeypatch.setattr(pm, "_latest_price", lambda s: 111.0)  # crossed the limit
    calls = []
    monkeypatch.setattr(
        pm, "close_position_routed",
        lambda pid, reason, **kw: calls.append((pid, reason)) or {"closed": True},
    )
    pm.evaluate_open_positions()
    assert ("e1", "operator_limit_exit") in calls
