"""Tests for horizon-driven time-stop + must-answer wake (M2, exit-intent plan).

When the Operator authors an expected_horizon:
  • auto-close moves out to the far backstop min(horizon * N, school_max),
  • hitting the horizon itself WAKES the Operator once (HORIZON_DUE, must-answer),
    it no longer auto-closes.
Positions without a horizon keep the legacy school_max auto-close.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import aletheia.portfolio.position_manager as pm
from aletheia.agent_runtime.wake_events import HORIZON_DUE


def _cfg(mult=3.0, max_hold_hours=72.0, max_hold_days=90, stop=2.0, tp=4.0):
    return SimpleNamespace(
        trading=SimpleNamespace(
            default_stop_loss_pct=stop,
            default_take_profit_pct=tp,
            max_position_hold_hours=max_hold_hours,
        ),
        serenity=SimpleNamespace(max_hold_days=max_hold_days),
        risk=SimpleNamespace(horizon_hard_backstop_mult=mult),
    )


def _pos(*, horizon=None, age_hours=0.0, school="short_term", entry=100.0, direction="long"):
    opened = (datetime.now(timezone.utc) - timedelta(hours=age_hours)).isoformat()
    p = {
        "id": "h1",
        "symbol": "BTC/USDT",
        "direction": direction,
        "entry_price": entry,
        "trade_school": school,
        "opened_at": opened,
    }
    if horizon is not None:
        p["expected_horizon_hours"] = horizon
    return p


# ── _decide_exit hard time-stop (price=entry so SL/TP never fire) ────────────

def test_backstop_closes_at_horizon_times_n():
    cfg = _cfg(mult=3.0)  # horizon 10 → hard = min(30, 72) = 30
    assert pm._decide_exit(_pos(horizon=10, age_hours=31), 100.0, cfg) == ("max_hold", 100.0)
    # before the backstop: NOT closed (horizon reached only wakes, handled elsewhere)
    assert pm._decide_exit(_pos(horizon=10, age_hours=20), 100.0, cfg) is None
    assert pm._decide_exit(_pos(horizon=10, age_hours=5), 100.0, cfg) is None


def test_backstop_capped_by_school_max():
    cfg = _cfg(mult=3.0, max_hold_hours=72.0)  # horizon 40 → 120 capped to 72
    assert pm._decide_exit(_pos(horizon=40, age_hours=73), 100.0, cfg) == ("max_hold", 100.0)
    assert pm._decide_exit(_pos(horizon=40, age_hours=71), 100.0, cfg) is None


def test_no_horizon_uses_legacy_school_max():
    cfg = _cfg(max_hold_hours=72.0)
    assert pm._decide_exit(_pos(horizon=None, age_hours=73), 100.0, cfg) == ("max_hold", 100.0)
    assert pm._decide_exit(_pos(horizon=None, age_hours=71), 100.0, cfg) is None


def test_serenity_backstop_uses_days_school_max():
    cfg = _cfg(mult=3.0, max_hold_days=90)  # school_max = 2160h; horizon 10 → 30
    assert pm._decide_exit(_pos(horizon=10, age_hours=31, school="serenity"), 100.0, cfg) == (
        "max_hold", 100.0,
    )
    # no horizon → legacy 90d cap, a 100h-old serenity hold stays open
    assert pm._decide_exit(_pos(horizon=None, age_hours=100, school="serenity"), 100.0, cfg) is None


# ── _maybe_wake_on_horizon (side-effecting, once per horizon) ────────────────

def test_completed_horizon_review_is_acknowledged_then_guarded(monkeypatch):
    from aletheia.storage.sqlite import get_db
    from aletheia.storage.repositories import _select_one
    import aletheia.agent_runtime.pi_daemon as pd

    opened = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
    get_db().execute(
        "INSERT INTO positions (id,symbol,market_type,direction,quantity,entry_price,"
        "execution_mode,status,expected_horizon_hours,opened_at) VALUES "
        "('h1','BTC/USDT','usdt_futures','long',1.0,100.0,'paper_internal','open',10.0,?)",
        (opened,),
    )

    captured = []

    def _accept_wake(source="unknown", event=None):
        captured.append(event)
        return True

    monkeypatch.setattr(pd, "notify_pi_daemon", _accept_wake)

    # Scheduling alone is not completion and therefore must not stamp the row.
    assert pm._maybe_wake_on_horizon(_select_one("positions", "h1")) is True
    assert len(captured) == 1
    assert captured[0].kind == HORIZON_DUE
    assert captured[0].position_id == "h1"
    assert _select_one("positions", "h1")["horizon_wake_sent_at"] is None

    # A completed review owns the durable acknowledgement.
    assert pm.acknowledge_horizon_review(["h1"]) == ["h1"]
    assert _select_one("positions", "h1")["horizon_wake_sent_at"] is not None

    # Acknowledged horizon → no duplicate review.
    assert pm._maybe_wake_on_horizon(_select_one("positions", "h1")) is False
    assert len(captured) == 1


def test_failed_wake_is_not_stamped_and_can_retry(monkeypatch):
    from aletheia.storage.sqlite import get_db
    from aletheia.storage.repositories import _select_one
    import aletheia.agent_runtime.pi_daemon as pd

    opened = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
    get_db().execute(
        "INSERT INTO positions (id,symbol,market_type,direction,quantity,entry_price,"
        "execution_mode,status,expected_horizon_hours,opened_at) VALUES "
        "('h2','ETH/USDT','usdt_futures','long',1.0,100.0,'paper_internal','open',10.0,?)",
        (opened,),
    )

    attempted = []

    def _reject_wake(source="unknown", event=None):
        attempted.append(event)
        return False

    monkeypatch.setattr(pd, "notify_pi_daemon", _reject_wake)

    assert pm._maybe_wake_on_horizon(_select_one("positions", "h2")) is False
    assert _select_one("positions", "h2")["horizon_wake_sent_at"] is None

    # A later position-manager tick must be allowed to try delivery again.
    assert pm._maybe_wake_on_horizon(_select_one("positions", "h2")) is False
    assert len(attempted) == 2


def test_scheduled_wake_is_not_acknowledged_before_review_and_survives_restart(
    monkeypatch,
):
    """Scheduling is not completion; a lost in-memory event must be recoverable."""
    from aletheia.storage.sqlite import get_db
    from aletheia.storage.repositories import _select_one
    import aletheia.agent_runtime.pi_daemon as pd

    opened = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
    get_db().execute(
        "INSERT INTO positions (id,symbol,market_type,direction,quantity,entry_price,"
        "execution_mode,status,expected_horizon_hours,opened_at) VALUES "
        "('h-restart','SOL/USDT','spot','long',1.0,100.0,'paper_internal','open',10.0,?)",
        (opened,),
    )
    delivered = []
    monkeypatch.setattr(
        pd,
        "notify_pi_daemon",
        lambda source="unknown", event=None: delivered.append(event) or True,
    )

    assert pm._maybe_wake_on_horizon(
        _select_one("positions", "h-restart")
    ) is True
    assert _select_one("positions", "h-restart")["horizon_wake_sent_at"] is None

    # A process restart loses the accepted in-memory event.  The next durable
    # position-manager scan must therefore be able to deliver it again.
    assert pm._maybe_wake_on_horizon(
        _select_one("positions", "h-restart")
    ) is True
    assert len(delivered) == 2


def test_wake_guards():
    # no horizon
    assert pm._maybe_wake_on_horizon(_pos(horizon=None, age_hours=100)) is False
    # horizon not yet reached
    assert pm._maybe_wake_on_horizon(_pos(horizon=10, age_hours=5)) is False
