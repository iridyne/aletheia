"""An unreadable external close ledger must not produce a false daily-loss total."""

import sqlite3

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations


def setup_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()


def test_partial_close_ledger_read_failure_marks_snapshot_unavailable(tmp_path, monkeypatch):
    """Monkeypatch list_position_close_events to raise; snapshot must be unavailable."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.risk.max_daily_loss_pct = 1.0  # off by default; this test covers the enabled check
    save_config(cfg)

    import sqlite3 as _sqlite3
    import aletheia.storage.repositories as _repo
    monkeypatch.setattr(_repo, "list_position_close_events",
                        lambda **_kw: (_ for _ in ()).throw(_sqlite3.OperationalError("no such table")))

    from aletheia.portfolio.account_loss import authoritative_account_realized_loss
    snapshot = authoritative_account_realized_loss("bitget_demo")

    assert snapshot.available is False
    assert snapshot.reason == "partial_close_ledger_read_failed"
    assert snapshot.realized_pnl_usdt is None
    assert snapshot.loss_usdt is None

    # Risk engine must emit account_loss_unavailable and reject any proposal.
    from aletheia.storage.models import TradeProposal
    from aletheia.storage import repositories as repo
    from aletheia.risk.engine import evaluate_proposal

    p = TradeProposal(symbol="ETH/USDT", market_type="spot", direction="long",
                      execution_mode="bitget_demo", quantity=0.001, price=3000)
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)
    assert result.approved is False
    assert any("account_loss_unavailable" in v for v in result.violations)
    assert not any("max_daily_loss" in v for v in result.violations)


# --- #3089-adjacent: Max Daily Loss is off by default in every mode ------------
# `risk.max_daily_loss_pct` defaults to 0, which disables check #8 entirely. The
# authoritative account-loss snapshot exists only to evaluate that limit, so it
# must not gate entries while the limit is off — that was the dominant
# rejection on the Fedora runtime (account_loss_unavailable:incomplete_funding_economics).


def _proposal_with_unavailable_account_loss(monkeypatch):
    """Insert a proposal and force the account-loss snapshot to be unavailable."""
    from aletheia.portfolio.account_loss import AccountLossSnapshot
    from aletheia.risk import engine as eng
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import TradeProposal

    monkeypatch.setattr(
        eng, "authoritative_account_realized_loss",
        lambda mode: AccountLossSnapshot(
            available=False, realized_pnl_usdt=None, loss_usdt=None,
            source="unavailable", reason="incomplete_funding_economics", event_count=0,
        ),
    )
    p = TradeProposal(symbol="ETH/USDT", market_type="spot", direction="long",
                      execution_mode="bitget_demo", quantity=0.001, price=3000)
    repo.insert_trade_proposal(p)
    return eng, p


def test_daily_loss_disabled_by_default_does_not_consult_account_loss(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config

    cfg = load_config()
    assert cfg.risk.max_daily_loss_pct == 0.0  # off by default

    eng, proposal = _proposal_with_unavailable_account_loss(monkeypatch)
    result = eng.evaluate_proposal(proposal)
    assert not any(v.startswith("account_loss_unavailable") for v in result.violations), (
        "a disabled limit must not reject entries for its own data dependency"
    )
    assert not any(v.startswith("max_daily_loss_exceeded") for v in result.violations)


def test_explicitly_disabled_daily_loss_is_inert(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.risk.max_daily_loss_pct = 0.0
    save_config(cfg)

    eng, proposal = _proposal_with_unavailable_account_loss(monkeypatch)
    result = eng.evaluate_proposal(proposal)
    assert not any(v.startswith("account_loss_unavailable") for v in result.violations)


def test_enabling_daily_loss_restores_the_unavailable_snapshot_rejection(tmp_path, monkeypatch):
    """Enabling the limit must restore the original fail-closed behaviour exactly."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.risk.max_daily_loss_pct = 1.0
    save_config(cfg)

    eng, proposal = _proposal_with_unavailable_account_loss(monkeypatch)
    result = eng.evaluate_proposal(proposal)
    assert "account_loss_unavailable:incomplete_funding_economics" in result.violations
    assert not any(v.startswith("max_daily_loss_exceeded") for v in result.violations), (
        "an unavailable snapshot must never be reported as a fabricated breach"
    )
