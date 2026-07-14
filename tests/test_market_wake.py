"""Tests for market-aware→wake and thesis review path (M5, exit-intent plan).

- A counter-signal WAKES the Operator (ADVERSE_SIGNAL) instead of auto-closing/tightening.
- Thesis invalidation auto-closes by default (D5); with thesis_auto_close=False it wakes.
"""

from types import SimpleNamespace

import aletheia.portfolio.position_manager as pm
from aletheia.agent_runtime.wake_events import ADVERSE_SIGNAL, THESIS_INVALIDATED


def _decide_cfg(thesis_auto_close=True):
    return SimpleNamespace(
        trading=SimpleNamespace(
            default_stop_loss_pct=2.0, default_take_profit_pct=4.0,
            max_position_hold_hours=72.0, thesis_auto_close=thesis_auto_close,
        ),
        serenity=SimpleNamespace(max_hold_days=90),
        risk=SimpleNamespace(horizon_hard_backstop_mult=3.0),
    )


def _serenity_pos():
    return {
        "id": "s1", "symbol": "BTC/USDT", "direction": "long", "entry_price": 100.0,
        "trade_school": "serenity", "opportunity_id": "o1",
    }


# ── _thesis_invalidated ──────────────────────────────────────────────────────

def test_thesis_invalidated(monkeypatch):
    import aletheia.storage.repositories as repo
    monkeypatch.setattr(repo, "get_serenity_opportunity", lambda oid: {"status": "dismissed"})
    assert pm._thesis_invalidated(_serenity_pos()) is True
    monkeypatch.setattr(repo, "get_serenity_opportunity", lambda oid: {"status": "active"})
    assert pm._thesis_invalidated(_serenity_pos()) is False
    # non-serenity never invalidates
    assert pm._thesis_invalidated({"trade_school": "short_term", "opportunity_id": "o1"}) is False


# ── thesis auto-close (default) vs review wake ───────────────────────────────

def test_thesis_auto_closes_by_default(monkeypatch):
    import aletheia.storage.repositories as repo
    monkeypatch.setattr(repo, "get_serenity_opportunity", lambda oid: {"status": "dismissed"})
    assert pm._decide_exit(_serenity_pos(), 100.0, _decide_cfg(thesis_auto_close=True)) == (
        "thesis_invalidated", 100.0,
    )


def test_thesis_no_autoclose_when_disabled(monkeypatch):
    import aletheia.storage.repositories as repo
    monkeypatch.setattr(repo, "get_serenity_opportunity", lambda oid: {"status": "dismissed"})
    # with auto-close off, _decide_exit does NOT close on thesis (returns None here)
    assert pm._decide_exit(_serenity_pos(), 100.0, _decide_cfg(thesis_auto_close=False)) is None


def test_thesis_review_wake(monkeypatch):
    import aletheia.storage.repositories as repo
    import aletheia.agent_runtime.pi_daemon as pd
    monkeypatch.setattr(repo, "get_serenity_opportunity", lambda oid: {"status": "dismissed"})
    captured = []
    monkeypatch.setattr(pd, "notify_pi_daemon", lambda source="unknown", event=None: captured.append(event))

    # auto_close ON → no review wake (default path auto-closes)
    assert pm._maybe_wake_on_thesis(_serenity_pos(), _decide_cfg(thesis_auto_close=True)) is False
    assert captured == []
    # auto_close OFF → review wake emitted
    assert pm._maybe_wake_on_thesis(_serenity_pos(), _decide_cfg(thesis_auto_close=False)) is True
    assert captured and captured[0].kind == THESIS_INVALIDATED and captured[0].position_id == "s1"


# ── market-aware → wake (not auto-close) ─────────────────────────────────────

def test_market_aware_wakes_not_closes(monkeypatch):
    from aletheia.storage.sqlite import get_db
    from aletheia.config.loader import load_config
    import aletheia.agent_runtime.pi_daemon as pd
    import aletheia.portfolio.exit_rules as exit_rules
    import aletheia.portfolio.market_signal_reader as msr

    get_db().execute(
        "INSERT INTO positions (id,symbol,market_type,direction,quantity,entry_price,"
        "execution_mode,status) VALUES "
        "('mw1','BTC/USDT','usdt_futures','long',1.0,100.0,'paper_internal','open')"
    )

    cfg = load_config()
    cfg.trading.market_aware_exits.enabled = True
    monkeypatch.setattr(pm, "load_config", lambda: cfg)
    monkeypatch.setattr(pm, "get_fresh_price_for_protective_close", lambda s, market_type, *, venue="": 100.0)

    signal = SimpleNamespace(confidence_score=85, direction_bias="short")
    monkeypatch.setattr(msr, "get_market_signals_for_positions", lambda syms, **kw: {"BTC/USDT": signal})
    monkeypatch.setattr(
        exit_rules, "evaluate_market_exit_rules",
        lambda pos, price, sig, c: exit_rules.MarketExitAction(
            action="urgent_exit", reason="counter_signal_urgent: short score=85", exit_price=price
        ),
    )

    wakes = []
    monkeypatch.setattr(pd, "notify_pi_daemon", lambda source="unknown", event=None: wakes.append(event))
    closes = []
    monkeypatch.setattr(pm, "close_position_routed", lambda pid, reason, **kw: closes.append((pid, reason)) or {"closed": True})

    result = pm.evaluate_open_positions()

    assert result["market_signal_wakes"] == 1
    assert closes == []                                   # NOT auto-closed
    adverse = [w for w in wakes if getattr(w, "kind", None) == ADVERSE_SIGNAL]
    assert adverse and adverse[0].position_id == "mw1"
    assert adverse[0].priority == 1                        # score 85 → P1 (urgent)
