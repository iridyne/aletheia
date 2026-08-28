"""The available-balance seam that proposal sizing reads.

Equity answers "how big is this account"; available balance answers "how much is
not already pledged as margin".  Sizing needs the second one so each open
position shrinks the next entry.  An unreadable account must surface as None so
callers fail closed rather than substituting configured capital.
"""

import pytest


def test_paper_internal_reports_ledger_available_balance(monkeypatch):
    from aletheia.risk import engine

    monkeypatch.setattr(
        "aletheia.portfolio.paper_account.get_paper_account",
        lambda **_kwargs: {
            "equity_usdt": 668.52648937,
            "available_balance_usdt": 534.82119150,
        },
    )

    # The seam reports free margin, not account size — they differ once a
    # position holds margin, and sizing must see the smaller number.
    assert engine.account_available_for_execution("paper_internal") == 534.8211915


def test_unreadable_paper_ledger_is_none_not_configured_capital(monkeypatch):
    from aletheia.risk import engine

    def unreadable(**_kwargs):
        raise RuntimeError("paper ledger unavailable")

    monkeypatch.setattr(
        "aletheia.portfolio.paper_account.get_paper_account", unreadable
    )

    assert engine.account_available_for_execution("paper_internal") is None


def test_negative_available_is_floored_at_zero(monkeypatch):
    from aletheia.risk import engine

    monkeypatch.setattr(
        "aletheia.portfolio.paper_account.get_paper_account",
        lambda **_kwargs: {"available_balance_usdt": -12.5},
    )

    # A negative free-margin snapshot must not become a negative sizing budget.
    assert engine.account_available_for_execution("paper_internal") == 0.0


def test_uta_generation_mismatch_revokes_risk_equity_and_available(monkeypatch):
    from aletheia.config.schema import AletheiaConfig
    from aletheia.portfolio import exchange_sync
    from aletheia.risk import engine

    cfg = AletheiaConfig()
    cfg.exchange.uta_read_boundaries = {"bitget_demo": "uta_v1"}
    stale_generation = {
        "age_seconds": 1.0,
        "sync_status": "generation_mismatch",
        "accounts": [
            {
                "broker_id": "bitget_demo",
                "equity_usdt": 999.0,
                "available_usdt": 999.0,
            }
        ],
    }
    monkeypatch.setattr(engine, "load_config", lambda: cfg)
    monkeypatch.setattr(
        exchange_sync, "get_unified_cached_state", lambda: stale_generation
    )
    monkeypatch.setattr(
        exchange_sync,
        "get_cached_state",
        lambda *_args: pytest.fail("UTA v1 must not read the legacy Bitget cache"),
    )

    assert engine.account_equity_for_execution("bitget_demo", cfg) is None
    assert engine.account_available_for_execution("bitget_demo", cfg) is None


def test_uta_complete_zero_remains_authoritative_for_risk(monkeypatch):
    from aletheia.config.schema import AletheiaConfig
    from aletheia.portfolio import exchange_sync
    from aletheia.risk import engine

    cfg = AletheiaConfig()
    cfg.exchange.uta_read_boundaries = {"bitget_demo": "uta_v1"}
    complete_zero = {
        "age_seconds": 1.0,
        "sync_status": "ok",
        "accounts": [
            {
                "broker_id": "bitget_demo",
                "equity_usdt": 0.0,
                "available_usdt": 0.0,
            }
        ],
    }
    monkeypatch.setattr(engine, "load_config", lambda: cfg)
    monkeypatch.setattr(exchange_sync, "get_unified_cached_state", lambda: complete_zero)

    assert engine.account_equity_for_execution("bitget_demo", cfg) == 0.0
    assert engine.account_available_for_execution("bitget_demo", cfg) == 0.0
