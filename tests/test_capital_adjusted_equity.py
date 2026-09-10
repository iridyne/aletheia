from datetime import datetime, timedelta, timezone

from aletheia.server.routes import data
from aletheia.storage import repositories as repo
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import (
    CapitalEvent,
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
    start = datetime(2026, 7, 1, microsecond=100_000, tzinfo=timezone.utc)
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
            broker_id="bitget_demo",
            event_type="deposit",
            amount_usdt=500.0,
            detected_at=(start + timedelta(microseconds=100)).isoformat(),
        )
    )
    repo.insert_capital_event(
        CapitalEvent(
            broker_id="bitget_demo",
            event_type="withdraw",
            amount_usdt=200.0,
            detected_at=(start + timedelta(minutes=90)).isoformat(),
        )
    )
    repo.insert_capital_event(
        CapitalEvent(
            broker_id="bitget_demo",
            event_type="deposit",
            amount_usdt=50.0,
            detected_at=(start + timedelta(hours=2, microseconds=100)).isoformat(),
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


def test_account_equity_history_baseline_respects_day_cutoff(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    fixed_now = datetime(2026, 8, 22, 15, 0, 0, tzinfo=timezone.utc)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz is not None else fixed_now.replace(tzinfo=None)

    monkeypatch.setattr(repo, "datetime", FixedDatetime)
    before = fixed_now - timedelta(days=7, seconds=1)
    cutoff = fixed_now - timedelta(days=7)
    after = cutoff + timedelta(seconds=1)
    for snapshot_at, equity in (
        (before.isoformat(), 100.0),
        (cutoff.isoformat(), 200.0),
        (after.isoformat(), 300.0),
    ):
        repo.insert_portfolio_snapshot(PortfolioSnapshot(
            total_value_usdt=equity,
            account_equity_usdt=equity,
            execution_mode="exchange_account",
            snapshot_at=snapshot_at,
        ))

    result = data.get_account_equity_history(days=7)

    assert [point["equity_usdt"] for point in result["points"]] == [200.0, 300.0]
    assert result["adjustment"]["baseline_at"] == cutoff.isoformat()
