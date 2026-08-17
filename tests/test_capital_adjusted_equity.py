from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from aletheia.server.routes import data
from aletheia.storage import repositories as repo
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import (
    CapitalEvent,
    PaperAccountEvent,
    PortfolioSnapshot,
)
from aletheia.storage.sqlite import reset_db


def _setup_db(tmp_path, monkeypatch):
    import sqlite3

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sqlite

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    reset_db()
    connection = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    connection.row_factory = sqlite3.Row
    sqlite._db_conn = connection
    run_migrations()
    data.invalidate_account_equity_history_cache()


def test_capital_events_adjust_account_equity_without_changing_raw_series(
    tmp_path,
    monkeypatch,
):
    _setup_db(tmp_path, monkeypatch)
    monkeypatch.setattr(
        data,
        "load_config",
        lambda: SimpleNamespace(
            exchange=SimpleNamespace(paper_execution_mode="bitget_demo")
        ),
    )
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    snapshots = (
        (start, 1000.0),
        (start + timedelta(hours=1), 1600.0),
        (start + timedelta(hours=2), 1400.0),
    )
    for at, equity in snapshots:
        repo.insert_portfolio_snapshot(
            PortfolioSnapshot(
                total_value_usdt=equity,
                account_equity_usdt=equity,
                execution_mode="exchange_account",
                snapshot_at=at.isoformat(),
            )
        )
    repo.insert_capital_event(
        CapitalEvent(
            broker_id="paper_internal",
            event_type="deposit",
            amount_usdt=500.0,
            detected_at=(start + timedelta(minutes=30)).isoformat(),
        )
    )
    repo.insert_capital_event(
        CapitalEvent(
            broker_id="paper_internal",
            event_type="withdraw",
            amount_usdt=200.0,
            detected_at=(start + timedelta(minutes=90)).isoformat(),
        )
    )

    result = data.get_account_equity_history()

    assert [point["equity_usdt"] for point in result["points"]] == [
        1000.0,
        1600.0,
        1400.0,
    ]
    assert [point["adjusted_equity_usdt"] for point in result["adjusted_points"]] == [
        1000.0,
        1100.0,
        1100.0,
    ]
    assert [marker["signed_amount_usdt"] for marker in result["capital_flow_markers"]] == [
        500.0,
        -200.0,
    ]
    assert result["adjustment"]["method"] == "visible_window_net_flow_adjusted_usdt"


def test_local_paper_balance_change_is_exposed_as_adjustment_marker(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    for at, equity in ((start, 180.0), (start + timedelta(hours=1), 680.0)):
        repo.insert_portfolio_snapshot(PortfolioSnapshot(
            total_value_usdt=equity,
            account_equity_usdt=equity,
            execution_mode="paper_internal",
            snapshot_at=at.isoformat(),
        ))
    repo.insert_paper_account_event(PaperAccountEvent(
        action="manual_set_balance",
        amount_usdt=500.0,
        cash_balance_before_usdt=180.0,
        cash_balance_after_usdt=680.0,
        actor="human",
        source="dashboard",
        note="operator adjustment",
        created_at=(start + timedelta(minutes=30)).isoformat(),
    ))

    result = data.get_account_equity_history()

    assert [point["adjusted_equity_usdt"] for point in result["adjusted_points"]] == [180.0, 180.0]
    assert result["capital_flow_markers"] == [{
        "id": result["capital_flow_markers"][0]["id"],
        "time": (start + timedelta(minutes=30)).isoformat(),
        "broker_id": "paper_internal",
        "event_type": "deposit",
        "amount_usdt": 500.0,
        "signed_amount_usdt": 500.0,
        "change_type": "manual_set_balance",
        "balance_before_usdt": 180.0,
        "balance_after_usdt": 680.0,
        "note": "operator adjustment",
    }]
