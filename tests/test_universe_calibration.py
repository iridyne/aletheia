"""Direction-aware forward-outcome calibration.

A long member wins when the forward return is positive; a short member wins
when it is negative. Rows with NULL direction are excluded because their trade
side is unknown. These tests pin that contract on the real SQLite path.

After #3701 Batch B, calibration_stats reads filter_candidate_outcomes (FCO)
instead of universe_forward_outcomes. All fixtures seed FCO directly.
"""

from datetime import datetime, timedelta

from aletheia.analytics.learning_window import CLEAN_WINDOW_ANCHOR_ISO
from aletheia.storage import repositories as repo
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import FilterCandidateOutcome
from aletheia.storage.sqlite import get_db, reset_db


def _after_anchor(**delta) -> str:
    """An entry_ts inside the clean window.

    `calibration_stats` filters on `entry_ts >= clean_window_cutoff(...)`, and that
    floor is the clean-window anchor — a real boundary that moves whenever
    attribution integrity is re-anchored. Timestamps written as absolute literals
    silently fall out of the window at the next re-anchor and the cohort reads as
    empty, so every fixture timestamp is derived from the anchor.
    """
    return (datetime.fromisoformat(CLEAN_WINDOW_ANCHOR_ISO) + timedelta(**delta)).isoformat()


def setup_test_db(tmp_path):
    reset_db()
    import sqlite3

    import aletheia.storage.sqlite as sq
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    return conn


def _settled(
    symbol, direction, ret_pct, *, family="momentum", horizon="1h",
    entry_ts=None, member_id=None, cost_model_version="test-v1",
):
    """FCO fixture for calibration tests (member_id kept for compatibility; unused)."""
    import uuid
    directional = None
    if direction == "long":
        directional = ret_pct
    elif direction == "short":
        directional = -ret_pct
    net = directional - 0.2 if directional is not None else None
    kwargs: dict = {}
    if entry_ts is not None:
        kwargs["entry_ts"] = entry_ts
    return FilterCandidateOutcome(
        outcome_candidate_key=f"{member_id or str(uuid.uuid4())}:{direction or 'none'}",
        symbol=symbol,
        venue="bitget",
        scanner_family=family,
        direction=direction,
        market_type="spot",
        horizon=horizon,
        entry_price_usd=100.0,
        due_at="2026-01-01T00:00:00+00:00",
        settled_at="2026-01-01T01:00:00+00:00",
        **kwargs,
        exit_price_usd=100.0 * (1 + ret_pct / 100.0),
        gross_return_pct=ret_pct,
        directional_return_pct=directional,
        estimated_fee_pct=0.1,
        estimated_spread_slippage_pct=0.1,
        estimated_funding_pct=0.0,
        net_return_pct=net,
        min_tradable_edge_pct=0.05,
        min_cost_covering_edge_pct=0.25,
        settlement_price_source="horizon_ohlcv",
        settlement_lag_sec=0.0,
        cost_model_version=cost_model_version,
        status="settled",
    )


def test_short_winner_counts_on_negative_return(tmp_path):
    setup_test_db(tmp_path)
    # A short that dropped 5% is a win; a short that rose 3% is a loss.
    repo.insert_filter_candidate_outcomes_batch([
        _settled("AAA/USDT", "short", -5.0, family="mean_reversion"),
        _settled("BBB/USDT", "short", 3.0, family="mean_reversion"),
    ])
    stats = {
        (r["scanner_family"], r["direction"], r["market_type"], r["horizon"]): r
        for r in repo.calibration_stats(cost_model_version="test-v1")
    }
    row = stats[("mean_reversion", "short", "spot", "1h")]
    assert row["n"] == 2
    assert row["win_count"] == 1  # only the -5% short wins
    reset_db()


def test_long_winner_counts_on_positive_return(tmp_path):
    setup_test_db(tmp_path)
    repo.insert_filter_candidate_outcomes_batch([
        _settled("AAA/USDT", "long", 5.0),
        _settled("BBB/USDT", "long", -2.0),
    ])
    stats = {
        (r["scanner_family"], r["direction"], r["market_type"], r["horizon"]): r
        for r in repo.calibration_stats(cost_model_version="test-v1")
    }
    row = stats[("momentum", "long", "spot", "1h")]
    assert row["n"] == 2
    assert row["win_count"] == 1  # only the +5% long wins
    assert len(row["source_outcome_fingerprint"]) == 64
    first_fingerprint = row["source_outcome_fingerprint"]

    repo.insert_filter_candidate_outcomes_batch([
        _settled("CCC/USDT", "long", 0.0),
    ])
    updated = {
        (item["scanner_family"], item["direction"], item["market_type"], item["horizon"]): item
        for item in repo.calibration_stats(cost_model_version="test-v1")
    }[("momentum", "long", "spot", "1h")]
    assert updated["n"] == 3
    assert updated["source_outcome_fingerprint"] != first_fingerprint
    reset_db()


def test_calibration_stats_isolates_exact_cost_model_versions(tmp_path):
    setup_test_db(tmp_path)
    repo.insert_filter_candidate_outcomes_batch([
        _settled("AAA/USDT", "long", 5.0),
        _settled("BBB/USDT", "long", -2.0),
        _settled(
            "CCC/USDT", "long", 9.0,
            cost_model_version="legacy-v0",
        ),
    ])

    current = repo.calibration_stats(cost_model_version="test-v1")[0]
    legacy = repo.calibration_stats(cost_model_version="legacy-v0")[0]

    assert current["n"] == 2
    assert current["cost_model_version"] == "test-v1"
    assert legacy["n"] == 1
    assert legacy["cost_model_version"] == "legacy-v0"
    assert current["source_outcome_fingerprint"] != legacy["source_outcome_fingerprint"]
    reset_db()


def test_incomplete_tradable_edge_is_excluded_from_calibration(tmp_path):
    setup_test_db(tmp_path)
    complete = _settled("AAA/USDT", "long", 5.0)
    incomplete = _settled("BBB/USDT", "long", 5.0).model_copy(
        update={"min_tradable_edge_pct": None},
    )
    malformed = _settled("CCC/USDT", "long", 5.0)
    blob = _settled("DDD/USDT", "long", 5.0)
    max_finite = _settled("EEE/USDT", "long", 5.0).model_copy(
        update={
            "min_tradable_edge_pct": float.fromhex("0x1.fffffffffffffp+1023"),
            "min_cost_covering_edge_pct": float.fromhex("0x1.fffffffffffffp+1023"),
        },
    )
    malformed_net = _settled("FFF/USDT", "long", 5.0)
    blob_net = _settled("GGG/USDT", "long", 5.0)
    blank_cost = _settled("HHH/USDT", "long", 5.0)
    blob_cost = _settled("III/USDT", "long", 5.0)
    whitespace_cost = _settled("JJJ/USDT", "long", 5.0)
    unicode_space_cost = _settled("KKK/USDT", "long", 5.0)
    repo.insert_filter_candidate_outcomes_batch([
        complete, incomplete, malformed, blob, max_finite, malformed_net, blob_net,
        blank_cost, blob_cost, whitespace_cost, unicode_space_cost,
    ])
    get_db().execute(
        "UPDATE filter_candidate_outcomes SET min_tradable_edge_pct='malformed' WHERE id=?",
        (malformed.id,),
    )
    get_db().execute(
        "UPDATE filter_candidate_outcomes SET min_tradable_edge_pct=? WHERE id=?",
        (b"0.05", blob.id),
    )
    get_db().execute(
        "UPDATE filter_candidate_outcomes SET net_return_pct='malformed' WHERE id=?",
        (malformed_net.id,),
    )
    get_db().execute(
        "UPDATE filter_candidate_outcomes SET net_return_pct=? WHERE id=?",
        (b"5.0", blob_net.id),
    )
    get_db().execute(
        "UPDATE filter_candidate_outcomes SET cost_model_version=' ' WHERE id=?",
        (blank_cost.id,),
    )
    get_db().execute(
        "UPDATE filter_candidate_outcomes SET cost_model_version=? WHERE id=?",
        (b"v1", blob_cost.id),
    )
    get_db().execute(
        "UPDATE filter_candidate_outcomes SET cost_model_version=? WHERE id=?",
        ("\t", whitespace_cost.id),
    )
    get_db().execute(
        "UPDATE filter_candidate_outcomes SET cost_model_version=? WHERE id=?",
        ("\N{NO-BREAK SPACE}", unicode_space_cost.id),
    )
    get_db().commit()

    rows = repo.calibration_stats(cost_model_version="test-v1")
    assert len(rows) == 1
    assert rows[0]["n"] == 2
    assert rows[0]["win_count"] == 1
    reset_db()


def test_non_long_short_direction_is_excluded_from_calibration(tmp_path):
    # FCO direction is required; use "unknown" to represent a non-long/short value.
    setup_test_db(tmp_path)
    repo.insert_filter_candidate_outcomes_batch([
        _settled("AAA/USDT", "unknown", 4.0, family="volatility"),
        _settled("BBB/USDT", "unknown", -4.0, family="volatility"),
        _settled("CCC/USDT", "long", 3.0, family="volatility"),
    ])
    stats = {
        (r["scanner_family"], r["direction"], r["market_type"], r["horizon"]): r
        for r in repo.calibration_stats(cost_model_version="test-v1")
    }
    row = stats[("volatility", "long", "spot", "1h")]
    assert row["n"] == 1
    assert row["win_count"] == 1  # only the explicit long row counts
    reset_db()


def test_horizon_separated_counts(tmp_path):
    setup_test_db(tmp_path)
    # 3 rows for AAA/USDT, all within 30-minute spacing (< 1h horizon) → only 1 admitted
    # 1 row for AAA/USDT at T+90min (> 1h) → 2nd admission for that symbol
    # 1 row for BBB/USDT at T+0 → independent symbol, always admitted
    repo.insert_filter_candidate_outcomes_batch([
        _settled("AAA/USDT", "long", 1.0, entry_ts=_after_anchor(minutes=1), member_id="m_aaa_1"),
        _settled("AAA/USDT", "long", 2.0, entry_ts=_after_anchor(minutes=21), member_id="m_aaa_2"),
        _settled("AAA/USDT", "long", 3.0, entry_ts=_after_anchor(minutes=41), member_id="m_aaa_3"),
        _settled("AAA/USDT", "long", 4.0, entry_ts=_after_anchor(minutes=91), member_id="m_aaa_4"),  # +90min
        _settled("BBB/USDT", "long", 1.0, entry_ts=_after_anchor(minutes=1), member_id="m_bbb_1"),
    ])
    stats = {
        (r["scanner_family"], r["direction"], r["market_type"], r["horizon"]): r
        for r in repo.calibration_stats(cost_model_version="test-v1")
    }
    row = stats[("momentum", "long", "spot", "1h")]
    assert row["n"] == 5               # raw count unchanged
    assert row["independent_n"] == 3   # AAA@T0, AAA@T+90m, BBB@T0
    assert row["distinct_symbols"] == 2
    assert row["distinct_days"] == 1
    reset_db()
