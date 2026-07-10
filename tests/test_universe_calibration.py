"""Direction-aware forward-outcome calibration.

A long member wins when the forward return is positive; a short member wins
when it is negative. Rows with NULL direction are excluded because their trade
side is unknown. These tests pin that contract on the real SQLite path.
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
    directional = None
    if direction == "long":
        directional = ret_pct
    elif direction == "short":
        directional = -ret_pct
    net = directional - 0.2 if directional is not None else None
    return UniverseForwardOutcome(
        member_id=f"m_{symbol}_{direction}_{ret_pct}",
        snapshot_id="snap_1",
        symbol=symbol,
        scanner_family=family,
        direction=direction,
        market_type="spot",
        horizon=horizon,
        entry_price_usd=100.0,
        due_at="2026-01-01T00:00:00+00:00",
        settled_at="2026-01-01T01:00:00+00:00",
        exit_price_usd=100.0 * (1 + ret_pct / 100.0),
        forward_return_pct=ret_pct,
        gross_return_pct=ret_pct,
        directional_return_pct=directional,
        net_return_pct=net,
        min_tradable_edge_pct=0.05,
        status="settled",
    )


def test_short_winner_counts_on_negative_return(tmp_path):
    setup_test_db(tmp_path)
    # A short that dropped 5% is a win; a short that rose 3% is a loss.
    repo.insert_forward_outcomes_batch([
        _settled("AAA/USDT", "short", -5.0, family="mean_reversion"),
        _settled("BBB/USDT", "short", 3.0, family="mean_reversion"),
    ])
    stats = {(r["scanner_family"], r["direction"], r["market_type"], r["horizon"]): r for r in repo.calibration_stats()}
    row = stats[("mean_reversion", "short", "spot", "1h")]
    assert row["n"] == 2
    assert row["win_count"] == 1  # only the -5% short wins
    reset_db()


def test_long_winner_counts_on_positive_return(tmp_path):
    setup_test_db(tmp_path)
    repo.insert_forward_outcomes_batch([
        _settled("AAA/USDT", "long", 5.0),
        _settled("BBB/USDT", "long", -2.0),
    ])
    stats = {(r["scanner_family"], r["direction"], r["market_type"], r["horizon"]): r for r in repo.calibration_stats()}
    row = stats[("momentum", "long", "spot", "1h")]
    assert row["n"] == 2
    assert row["win_count"] == 1  # only the +5% long wins
    reset_db()


def test_null_direction_is_excluded_from_calibration(tmp_path):
    setup_test_db(tmp_path)
    repo.insert_forward_outcomes_batch([
        _settled("AAA/USDT", None, 4.0, family="volatility"),
        _settled("BBB/USDT", None, -4.0, family="volatility"),
        _settled("CCC/USDT", "long", 3.0, family="volatility"),
    ])
    stats = {(r["scanner_family"], r["direction"], r["market_type"], r["horizon"]): r for r in repo.calibration_stats()}
    row = stats[("volatility", "long", "spot", "1h")]
    assert row["n"] == 1
    assert row["win_count"] == 1  # only the explicit long row counts
    reset_db()
