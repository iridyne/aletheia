"""Focused regressions for risk-engine trust-boundary fixes (batch B2).

Covers findings #7 (live-gate fail-open), #24 (spot+short), #98 (entry-plan
geometry), #93 (lesson hard-block category boundary). Each drives a real
TradeProposal through evaluate_proposal and asserts on the resulting violations.
"""

import sqlite3

import pytest


def _prepare(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    sq._db_conn = conn
    run_migrations()


def _set_live_state(state):
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.live.state = state
    save_config(cfg)


def test_live_gate_fails_closed_on_non_unlocked_state(tmp_path, monkeypatch):
    """#7: a *_live_limited order is permitted ONLY when live.state == 'live_limited'."""
    _prepare(tmp_path, monkeypatch)
    from aletheia.storage.models import TradeProposal
    from aletheia.storage import repositories as repo
    from aletheia.risk.engine import evaluate_proposal

    def _mk():
        p = TradeProposal(symbol="BTC/USDT", market_type="usdt_futures", direction="long",
                          execution_mode="okx_live_limited", quantity=0.001, price=60000)
        repo.insert_trade_proposal(p)
        return p

    # Unknown/typo state → must BLOCK (fail closed) — the pre-fix bug permitted it.
    _set_live_state("weird_unexpected_state")
    assert "live_disabled" in evaluate_proposal(_mk()).violations

    # Default disabled → blocks.
    _set_live_state("live_disabled")
    assert "live_disabled" in evaluate_proposal(_mk()).violations

    # Explicit unlocked state → the live-state check does NOT fire.
    _set_live_state("live_limited")
    assert "live_disabled" not in evaluate_proposal(_mk()).violations


def test_spot_short_rejected(tmp_path, monkeypatch):
    """#24: a spot instrument cannot be shorted."""
    _prepare(tmp_path, monkeypatch)
    from aletheia.storage.models import TradeProposal
    from aletheia.storage import repositories as repo
    from aletheia.risk.engine import evaluate_proposal

    p = TradeProposal(symbol="BTC/USDT", market_type="spot", direction="short",
                      execution_mode="paper_internal", quantity=0.001, price=60000)
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)
    assert "spot_short_not_allowed" in result.violations
    assert not result.approved


def test_entry_plan_geometry_rejects_inverted_sides(tmp_path, monkeypatch):
    """#98: native LIMIT entry with stop/TP on the wrong side of entry is rejected."""
    _prepare(tmp_path, monkeypatch)
    from aletheia.storage.models import TradeProposal
    from aletheia.storage import repositories as repo
    from aletheia.risk.engine import evaluate_proposal

    # Inverted long: stop ABOVE entry, TP BELOW entry.
    bad = TradeProposal(symbol="BTC/USDT", market_type="usdt_futures", direction="long",
                        execution_mode="paper_internal", quantity=0.001, price=100.0,
                        order_type="limit", source="native",
                        stop_price=110.0, take_profit=90.0, risk_reward=2.0)
    repo.insert_trade_proposal(bad)
    assert any("entry_plan_geometry" in v for v in evaluate_proposal(bad).violations)

    # Correct long geometry: stop below, TP above → no geometry violation.
    good = TradeProposal(symbol="ETH/USDT", market_type="usdt_futures", direction="long",
                         execution_mode="paper_internal", quantity=0.01, price=100.0,
                         order_type="limit", source="native",
                         stop_price=90.0, take_profit=120.0, risk_reward=2.0)
    repo.insert_trade_proposal(good)
    assert not any("entry_plan_geometry" in v for v in evaluate_proposal(good).violations)

    # Correct short geometry: TP below, stop above → no geometry violation.
    short_ok = TradeProposal(symbol="SOL/USDT", market_type="usdt_futures", direction="short",
                             execution_mode="paper_internal", quantity=0.1, price=100.0,
                             order_type="limit", source="native",
                             stop_price=110.0, take_profit=80.0, risk_reward=2.0)
    repo.insert_trade_proposal(short_ok)
    assert not any("entry_plan_geometry" in v for v in evaluate_proposal(short_ok).violations)


def test_lesson_hard_block_requires_risk_category(tmp_path, monkeypatch):
    """#93: block_* hard rejects only apply to category='risk' lessons, not other categories."""
    _prepare(tmp_path, monkeypatch)
    import aletheia.storage.sqlite as sq
    from aletheia.learning.lessons import LessonStore
    from aletheia.storage.models import TradeProposal
    from aletheia.storage import repositories as repo
    from aletheia.risk.engine import evaluate_proposal

    store = LessonStore()

    def _applied_block_lesson(category):
        les = store.create("block BTC", source_type="test", source_id="t",
                           category=category, severity="high",
                           recommendation={"action": "block_symbol", "symbol": "BTC/USDT"})
        store.accept(les.lesson_id)
        store.mark_applied(les.lesson_id, "pv-test")
        # Push dry_run_until into the past so the guard is ACTIVE (not advisory).
        db = sq.get_db()
        db.execute(
            "UPDATE ps_lessons SET dry_run_until = '2000-01-01T00:00:00+00:00' WHERE lesson_id = ?",
            (les.lesson_id,),
        )
        db.commit()

    def _eval_btc():
        p = TradeProposal(symbol="BTC/USDT", market_type="usdt_futures", direction="long",
                          execution_mode="paper_internal", quantity=0.001, price=60000)
        repo.insert_trade_proposal(p)
        return evaluate_proposal(p).violations

    # A non-risk applied lesson carrying block_symbol must NOT hard-block.
    _applied_block_lesson("general")
    assert not any("lesson_block_symbol" in v for v in _eval_btc())

    # A risk-category applied lesson with the same action DOES hard-block.
    _applied_block_lesson("risk")
    assert any("lesson_block_symbol" in v for v in _eval_btc())


def test_paper_internal_drawdown_ignores_external_account_snapshots(monkeypatch):
    """Local paper must not borrow an unrelated exchange account's equity."""
    from aletheia.risk.engine import _portfolio_drawdown_scale

    # The local simulator's notional collapsed, while an unrelated cached
    # exchange account has its own equity history. Neither is paper equity.
    fake_snapshots = (
        # paper_internal rows (notional — misleading)
        [{"execution_mode": "paper_internal", "total_value_usdt": "272.0"}]
        + [{"execution_mode": "paper_internal", "total_value_usdt": "16.0"}] * 4
        # exchange_account rows (real equity — steady)
        + [{"execution_mode": "exchange_account", "total_value_usdt": "88000.0"}] * 5
    )
    monkeypatch.setattr(
        "aletheia.storage.repositories.list_portfolio_snapshots",
        lambda limit=50: fake_snapshots,
    )
    scale = _portfolio_drawdown_scale("paper_internal")
    assert scale == 1.0


def test_paper_internal_drawdown_ignores_local_notional_collapse(monkeypatch):
    """A closed local paper position is not an account-equity drawdown."""
    from aletheia.risk.engine import _portfolio_drawdown_scale

    fake_snapshots = (
        [{"execution_mode": "paper_internal", "total_value_usdt": "200.0"}] * 3
        + [{"execution_mode": "paper_internal", "total_value_usdt": "0.0"}] * 3
    )
    monkeypatch.setattr(
        "aletheia.storage.repositories.list_portfolio_snapshots",
        lambda limit=50: fake_snapshots,
    )

    scale = _portfolio_drawdown_scale("paper_internal")
    assert scale == 1.0


def test_paper_internal_drawdown_ignores_external_equity_drop(monkeypatch):
    """External-account loss must not freeze the independent local simulator."""
    from aletheia.risk.engine import _portfolio_drawdown_scale

    # Exchange equity peaked at 100k, now at 85k → 15% DD → scale ~0.25
    fake_snapshots = (
        [{"execution_mode": "paper_internal", "total_value_usdt": "50.0"}] * 3
        + [{"execution_mode": "exchange_account", "total_value_usdt": "100000.0"}] * 3
        + [{"execution_mode": "exchange_account", "total_value_usdt": "85000.0"}] * 2
    )
    monkeypatch.setattr(
        "aletheia.storage.repositories.list_portfolio_snapshots",
        lambda limit=50: fake_snapshots,
    )
    scale = _portfolio_drawdown_scale("paper_internal")
    assert scale == 1.0
