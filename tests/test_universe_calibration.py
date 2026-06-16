"""Direction-aware forward-outcome calibration.

A long member wins when the forward return is positive; a short member wins
when it is negative. Legacy rows with NULL direction fall back to long
semantics. These tests pin that contract on the real SQLite path.
"""

from aletheia.storage.sqlite import get_db, reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import UniverseForwardOutcome
from aletheia.storage import repositories as repo


def setup_test_db(tmp_path):
    reset_db()
    import aletheia.storage.sqlite as sq
    import sqlite3
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    return conn


def _settled(symbol, direction, ret_pct, *, family="momentum", horizon="1h"):
    return UniverseForwardOutcome(
        member_id=f"m_{symbol}_{direction}_{ret_pct}",
        snapshot_id="snap_1",
        symbol=symbol,
        scanner_family=family,
        direction=direction,
        horizon=horizon,
        entry_price_usd=100.0,
        due_at="2026-01-01T00:00:00+00:00",
        settled_at="2026-01-01T01:00:00+00:00",
        exit_price_usd=100.0 * (1 + ret_pct / 100.0),
        forward_return_pct=ret_pct,
        status="settled",
    )


def test_short_winner_counts_on_negative_return(tmp_path):
    setup_test_db(tmp_path)
    # A short that dropped 5% is a win; a short that rose 3% is a loss.
    repo.insert_forward_outcomes_batch([
        _settled("AAA/USDT", "short", -5.0, family="mean_reversion"),
        _settled("BBB/USDT", "short", 3.0, family="mean_reversion"),
    ])
    stats = {(r["scanner_family"], r["horizon"]): r for r in repo.calibration_stats()}
    row = stats[("mean_reversion", "1h")]
    assert row["n"] == 2
    assert row["win_count"] == 1  # only the -5% short wins
    reset_db()


def test_long_winner_counts_on_positive_return(tmp_path):
    setup_test_db(tmp_path)
    repo.insert_forward_outcomes_batch([
        _settled("AAA/USDT", "long", 5.0),
        _settled("BBB/USDT", "long", -2.0),
    ])
    stats = {(r["scanner_family"], r["horizon"]): r for r in repo.calibration_stats()}
    row = stats[("momentum", "1h")]
    assert row["n"] == 2
    assert row["win_count"] == 1  # only the +5% long wins
    reset_db()


def test_null_direction_falls_back_to_long(tmp_path):
    setup_test_db(tmp_path)
    # Legacy row: direction is NULL → positive return treated as a win.
    repo.insert_forward_outcomes_batch([
        _settled("AAA/USDT", None, 4.0, family="volatility"),
        _settled("BBB/USDT", None, -4.0, family="volatility"),
    ])
    stats = {(r["scanner_family"], r["horizon"]): r for r in repo.calibration_stats()}
    row = stats[("volatility", "1h")]
    assert row["n"] == 2
    assert row["win_count"] == 1  # +4% wins, -4% loses (long fallback)
    reset_db()
