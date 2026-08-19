"""The available-balance seam that proposal sizing reads.

Equity answers "how big is this account"; available balance answers "how much is
not already pledged as margin".  Sizing needs the second one so each open
position shrinks the next entry.  An unreadable account must surface as None so
callers fail closed rather than substituting configured capital.
"""


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
